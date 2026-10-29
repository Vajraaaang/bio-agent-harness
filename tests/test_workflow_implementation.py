"""Controlled implementation branches never turn draft source into execution authority."""
from copy import deepcopy

import pytest

from tests.test_method_planner import Selector, resolved, trial_spec
from tests.test_method_authoring import implementation
from tests.test_extensible_methods import FixtureExecutor


def request(planner, step, tmp_path, **changes):
    interface = deepcopy(planner.methods.method(step["method_version_id"])["contract"])
    interface["name"] = "Unavailable preferred method"
    value = {"candidate": interface["name"], "interface": interface,
        "environment_lock": str(tmp_path / "environment.lock"),
        "justification": "Explicitly implement this unavailable synthetic alternative",
        "authorized": True}
    value.update(changes)
    return value


def test_missing_implementation_noop_is_visible_and_has_no_execution(tmp_path):
    from devbench.analysis.workflow_implementation import perform_implementation
    planner, state, _, _, _, _ = resolved(tmp_path)
    selector = Selector()
    before = planner.get(state["session_id"])
    result = perform_implementation(planner, state["session_id"], selector, actor="scientist")
    assert result["status"] == "unavailable"
    assert result["unavailable"][0]["candidate"] == "Unavailable preferred method"
    assert result["drafts"] == [] and not result["execution_granted"]
    assert planner.get(state["session_id"]) == before
    assert selector.contexts == []


@pytest.mark.parametrize("changes", [{"authorized": False}, {"justification": ""},
    {"interface": {"implementation": {"kind": "dispatcher"}}}])
def test_unauthorized_unjustified_or_unsupported_gap_never_dispatches(tmp_path, changes):
    from devbench.analysis.workflow_implementation import perform_implementation
    planner, state, _, _, _, step = resolved(tmp_path)
    selector = Selector()
    result = perform_implementation(planner, state["session_id"], selector, actor="scientist",
        requests=[request(planner, step, tmp_path, **changes)])
    assert result["status"] == "unavailable"
    assert selector.contexts == []
    assert result["drafts"] == []


def test_justified_draft_remains_unreviewed_and_blocks_reliance_without_numeric_evidence(tmp_path):
    from devbench.analysis.workflow_implementation import perform_implementation
    planner, state, _, analysis, _, step = resolved(tmp_path)
    marker = tmp_path / "must-not-exist"
    response = implementation()
    response["source_code"] = "from pathlib import Path\nPath(" + repr(str(marker)) + ").write_text('executed')\n"
    selector = Selector(response)
    values = [request(planner, step, tmp_path)]
    result = perform_implementation(planner, state["session_id"], selector, actor="scientist", requests=values)
    assert result["status"] == "needs_validation"
    method = planner.methods.method(result["drafts"][0]["method_version_id"])
    assert method["contract"]["implementation_trust"] == "unreviewed"
    assert method["contract"]["lifecycle"] == "custom"
    assert not marker.exists()
    assert planner.get(state["session_id"])["results"] == []
    assert planner.methods.analysis(analysis["analysis_id"])["revision_id"] == analysis["revision_id"]
    assert not result["execution_granted"] and not result["promotion_granted"]
    # A replay reuses exact retained draft identity without a second selector call.
    replay = perform_implementation(planner, state["session_id"], Selector(), actor="scientist", requests=values)
    assert replay["drafts"] == result["drafts"]
    assert replay["decision_sha256"] == result["decision_sha256"]


def test_changed_request_cannot_rewrite_an_existing_branch_draft(tmp_path):
    from devbench.analysis.workflow_implementation import perform_implementation
    planner, state, _, _, _, step = resolved(tmp_path)
    value = request(planner, step, tmp_path)
    perform_implementation(planner, state["session_id"], Selector(implementation()), actor="scientist", requests=[value])
    value["justification"] = "Different intended implementation"
    with pytest.raises(ValueError, match="immutable|changed|new planning cycle"):
        perform_implementation(planner, state["session_id"], Selector(), actor="scientist", requests=[value])


@pytest.mark.parametrize("mutation", ["source", "lock"])
def test_replay_rechecks_retained_source_and_environment_bytes(tmp_path, mutation):
    from devbench.analysis.workflow_implementation import perform_implementation
    planner, state, _, _, _, step = resolved(tmp_path)
    value = request(planner, step, tmp_path)
    result = perform_implementation(planner, state["session_id"], Selector(implementation()), actor="scientist", requests=[value])
    method = planner.methods.method(result["drafts"][0]["method_version_id"])
    if mutation == "source":
        target = planner.methods.artifact_path(method["source_artifact"])
        target.chmod(0o600)
    else:
        target = tmp_path / "environment.lock"
    target.write_text("Changed after implementation branching")
    selector = Selector()
    with pytest.raises(ValueError, match="changed|hash mismatch"):
        perform_implementation(planner, state["session_id"], selector, actor="scientist", requests=[value])
    assert selector.contexts == []


@pytest.mark.parametrize("case", ["different_name", "fabricated_reference", "hostile_path"])
def test_invalid_source_binding_fails_before_any_host_execution(tmp_path, case):
    from devbench.analysis.workflow_implementation import perform_implementation
    planner, state, _, _, _, step = resolved(tmp_path)
    value = request(planner, step, tmp_path)
    response = implementation()
    if case == "different_name":
        value["interface"]["name"] = "Different scientific method"
    elif case == "fabricated_reference":
        response["source_references"] = ["https://example.invalid/never-retrieved"]
    else:
        value["environment_lock"] = "relative.lock"
    with pytest.raises(ValueError):
        perform_implementation(planner, state["session_id"], Selector(response), actor="scientist", requests=[value])
    assert planner.get(state["session_id"]).get("implementation_drafts", []) == []
    assert planner.get(state["session_id"])["results"] == []


def test_inapplicable_scientific_candidate_does_not_become_eligible_by_drafting(tmp_path):
    from devbench.analysis.workflow_implementation import perform_implementation
    planner, state, _, _, _, step = resolved(tmp_path, observed={"independent_samples": False})
    selector = Selector()
    result = perform_implementation(planner, state["session_id"], selector, actor="scientist",
        requests=[request(planner, step, tmp_path)])
    assert result["status"] == "unavailable" and result["drafts"] == []
    assert result["unavailable"][0]["reason"] == "scientific_preconditions_unresolved_or_inapplicable"
    assert selector.contexts == []


def test_synthetic_structural_success_does_not_validate_numeric_behavior(tmp_path):
    from devbench.analysis.workflow_implementation import verify_implementation_validation
    planner, _, _, _, _, step = resolved(tmp_path)
    run = planner.methods.run_step(step["step_id"], actor="scientist", executor=FixtureExecutor())
    assert run["status"] == "succeeded" and run["validation"]["passed"]
    with pytest.raises(ValueError, match="OCI|numerical|local_docker"):
        verify_implementation_validation(planner.methods, step["method_version_id"], {
            "run_id": run["run_id"], "packet_manifest": str(tmp_path / "not-a-packet"),
            "packet_root": "0" * 64, "judgment_manifest": str(tmp_path / "not-a-review"),
            "judgment_root": "1" * 64})


def test_review_binding_includes_actual_source_contract_and_retained_run(tmp_path):
    from devbench.analysis.workflow_implementation import implementation_review_binding
    planner, _, _, _, _, step = resolved(tmp_path)
    run = planner.methods.run_step(step["step_id"], actor="scientist", executor=FixtureExecutor())
    binding = implementation_review_binding(planner.methods, step["method_version_id"], run["run_id"])
    method = planner.methods.method(step["method_version_id"])
    assert binding["source_code"] == planner.methods.artifact_path(method["source_artifact"]).read_text()
    assert binding["interface"] == method["contract"]
    assert binding["validation"] == run["validation"]
    assert binding["run_root"] == run["evidence"]["root_hash"]
    # Preparing exact review material does not qualify fixture execution as OCI.
    assert binding.get("validated") is None


def test_reviewer_acceptance_boolean_cannot_grant_numeric_execution_or_trust(tmp_path):
    from devbench.analysis.workflow_implementation import verify_implementation_validation
    planner, _, _, _, _, step = resolved(tmp_path)
    with pytest.raises(ValueError, match="exact retained"):
        verify_implementation_validation(planner.methods, step["method_version_id"], {
            "reviewer_accepted": True, "passed": True})
    assert planner.methods.method(step["method_version_id"])["contract"]["implementation_trust"] == "unreviewed"


def test_late_evidence_requires_explicit_exact_reassessment(tmp_path):
    from devbench.analysis.workflow_implementation import require_late_evidence_policy, reassessment_declaration
    planner, state, _, _, _, _ = resolved(tmp_path)
    plan = planner.get(state["session_id"])
    with pytest.raises(ValueError, match="reassessment"):
        require_late_evidence_policy(plan, operation="retrieve_evidence")
    declaration = reassessment_declaration(plan, reason="Assess newly supplied source")
    assert require_late_evidence_policy(plan, operation="retrieve_evidence", reassessment=declaration)["status"] == "reassessment_required"
    changed = deepcopy(plan)
    changed["scientific_candidates"][0]["known_failure_modes"].append("New limitation")
    with pytest.raises(ValueError, match="stale|binding"):
        require_late_evidence_policy(changed, operation="retrieve_evidence", reassessment=declaration)


@pytest.mark.parametrize("operation", ["retrieve_evidence", "resolve_capabilities", "design_trials", "declare_trial", "draft_implementation", "reassess_candidates"])
def test_results_freeze_comparisons_and_evidence_cycle(tmp_path, operation):
    from devbench.analysis.workflow_implementation import require_late_evidence_policy
    planner, state, _, _, branch, step = resolved(tmp_path)
    spec = trial_spec(step)
    planner.declare_trial(state["session_id"], branch["branch_id"], spec, actor="scientist")
    planner.run_trial(state["session_id"], spec["id"], actor="scientist", executor=FixtureExecutor(), allow_data_execution=True)
    plan = planner.get(state["session_id"])
    with pytest.raises(ValueError, match="results|new planning cycle|frozen"):
        require_late_evidence_policy(plan, operation=operation)
