"""Signed-in route engineering checks; no native model or numerical dispatch."""
from tests.support.workflow_science_fixtures import RecordingScientificRuntime
from copy import deepcopy

import pytest

from devbench.analysis.rna_scientific_trial_contract import (
    trial_binding, validate_approval, validate_trial_request,
)
from tests.test_rna_scientific_trial import NOW, approve
from tests.test_rna_scientific_trial_v2 import request_v2


def request_v3(tmp_path):
    request = request_v2(tmp_path)
    return as_signedin(request)


def as_signedin(request):
    request["format"] = "devbench.rna-scientific-trial/3"
    request["engineering"].update(route="codex_chatgpt", model="configured-codex-model",
        client={"kind": "official_codex_cli", "identity_sha256": "1" * 64},
        authentication={"method": "chatgpt_signin", "observation_sha256": "2" * 64},
        budget_semantics="generation_jobs", qualified_boundary=None)
    request["allocation"] = {"kind": "generation_jobs",
        "generation": {"jobs": 3, "seconds": 120, "context_bytes": 128_000, "output_bytes": 64_000},
        "numeric": None}
    return request


def test_signedin_contract_has_real_job_units_and_no_invented_api_allocation(tmp_path):
    request = request_v3(tmp_path)
    assert validate_trial_request(request) == request
    assert set(request["allocation"]["generation"]) == {"jobs", "seconds", "context_bytes", "output_bytes"}
    for field, value in (("total_tokens", 828_288), ("requests", 3), ("cost_ceiling", 12)):
        changed = deepcopy(request)
        changed["allocation"][field] = value
        with pytest.raises(ValueError, match="closed"):
            validate_trial_request(changed)


@pytest.mark.parametrize("field", ["client", "authentication", "generation_budget", "qualified_boundary"])
def test_signedin_identity_is_part_of_exact_generation_approval(tmp_path, field):
    request = request_v3(tmp_path)
    approve(request, "workflow_generation")
    validate_approval(request, "workflow_generation", now=NOW)
    if field == "client":
        request["engineering"][field]["identity_sha256"] = "3" * 64
    elif field == "authentication":
        request["engineering"][field]["observation_sha256"] = "3" * 64
    elif field == "qualified_boundary":
        request["engineering"][field] = request["dataset"]["access_record"]
    else:
        request["allocation"]["generation"]["jobs"] = 4
    with pytest.raises(PermissionError, match="binding"):
        validate_approval(request, "workflow_generation", now=NOW)


def test_signedin_generation_grant_does_not_activate_future_numeric_allocation(tmp_path):
    request = request_v3(tmp_path)
    approve(request, "workflow_generation")
    binding = trial_binding(request, "workflow_generation")
    request["allocation"]["numeric"] = {"memory_mb": 512, "seconds": 90, "storage_bytes": 1_000_000, "max_runs": 2}
    assert trial_binding(request, "workflow_generation") == binding
    validate_approval(request, "workflow_generation", now=NOW)
    with pytest.raises(PermissionError, match="Scientific context|execution approval"):
        validate_approval(request, "trial_execution", now=NOW)


def test_signedin_route_cannot_be_smuggled_into_historical_schema(tmp_path):
    request = request_v3(tmp_path)
    request["format"] = "devbench.rna-scientific-trial/2"
    with pytest.raises(ValueError, match="closed"):
        validate_trial_request(request)


def test_signedin_generation_requires_all_three_existing_stages(tmp_path):
    request = request_v3(tmp_path)
    request["allocation"]["generation"]["jobs"] = 2
    with pytest.raises(ValueError, match="three|3|stage"):
        validate_trial_request(request)


def test_signedin_numeric_execution_needs_explicit_unactivated_numeric_limits(tmp_path):
    request = request_v3(tmp_path)
    request["scientific_context"] = {"project_id": "a" * 32, "scientific_run_id": "b" * 32,
        "attempt_id": "c" * 32, "dataset_version_ids": ["d" * 64]}
    approve(request, "trial_execution")
    with pytest.raises(PermissionError, match="numeric allocation"):
        validate_approval(request, "trial_execution", now=NOW)


def test_signedin_helpers_are_in_installed_enforcement_custody():
    from devbench.analysis.rna_trial_authority import _CUSTODY_PATHS
    assert {"devbench/analysis/codex_signedin.py", "devbench/analysis/codex_job_policy.py",
        "devbench/analysis/codex_job_trace.py"} <= set(_CUSTODY_PATHS)


def test_synthetic_signedin_intent_can_be_prepared_while_boundary_remains_blocked(tmp_path):
    from tests.test_rna_scientific_trial_v2 import configured_v2
    from devbench.analysis.rna_scientific_trial import prepare_trial
    request, planner, *_ = configured_v2(tmp_path)
    request = as_signedin(request)
    request["approvals"]["workflow_generation"] = None
    result = prepare_trial(tmp_path / "signedin-preparation", request, methods=planner.methods,
        workspace=tmp_path, purpose="workflow_generation", now=NOW)
    readiness = result["readiness"]
    assert readiness["format"] == "devbench.rna-trial-readiness/3"
    assert readiness["local_status"] == "blocked"
    assert "signedin_native_boundary_unqualified" in readiness["blockers"]
    assert readiness["generation_controls"]["hard_total_tokens"] == "UNSUPPORTED"
    assert readiness["generation_controls"]["internal_api_requests"] == "UNSUPPORTED"
    assert readiness["generation_controls"]["credits"] == "UNSUPPORTED"
    assert readiness["generation_controls"]["spend"] == "UNSUPPORTED"
    assert planner.methods.list_records("run") == []


def test_three_stage_planner_reuses_job_ledger_and_grounded_final_bytes(tmp_path, monkeypatch):
    """Offline process transcripts test plumbing, not native admission or science."""
    import hashlib
    import json
    from devbench.analysis import codex_signedin as signedin
    from devbench.analysis import rna_trial_authority
    from devbench.analysis.checkpoints import digest
    from devbench.analysis.episode_agent import AgentLimits
    from devbench.analysis.rna_publication_evidence import participant_publication_context
    from devbench.analysis.rna_scientific_trial import GroundedSelector
    from devbench.analysis.rna_trace_receipts import LocalTraceObserver
    from devbench.analysis.workflow_operations import WorkflowOperationLedger
    from tests.test_rna_scientific_trial import fixture

    request, planner, recorded, _, method, _ = fixture(tmp_path)
    monkeypatch.setattr(rna_trial_authority, "trial_enforcement_source_digest", lambda: "a" * 64)
    monkeypatch.setattr(rna_trial_authority, "trial_enforcement_runtime_digest", lambda: "b" * 64)
    policy = {"format": "devbench.model-job-budget-policy/1", "kind": "generation_jobs", "max_jobs": 3,
        "max_concurrent_jobs": 1, "max_total_seconds": 30, "max_context_bytes": 128_000,
        "max_output_bytes": 100_000, "max_children": 0,
        "reconciliation": {"jobs": 3, "seconds": 24, "context_bytes": 128_000}}
    selector = signedin.SignedInCodexActionSelector(requested_model="fixture-model", private_root=planner.methods.root,
        max_context_bytes=128_000, max_output_bytes=100_000,
        _test_process=signedin._OfflineProcessBinding(b"fixed synthetic process identity"))
    ledger = WorkflowOperationLedger(planner.methods.root / "jobs.sqlite")
    backend = signedin.SignedInCodexInvestigationBackend(planner.methods, selector, policy=policy, ledger=ledger)
    # Only fixed local Python children replay these invented transcripts. The
    # real authenticated authority is not patched or used to admit this fixture.
    transcripts = []
    for index, answer in enumerate(recorded.answers):
        rows = [{"type": "thread.started", "thread_id": "synthetic-stage-" + str(index)}, {"type": "turn.started"},
            {"type": "item.completed", "item": {"type": "agent_message", "text": json.dumps(answer)}},
            {"type": "turn.completed", "model": "fixture-model"}]
        transcripts.append(b"".join(json.dumps(row).encode() + b"\n" for row in rows))
    original_command = signedin._OfflineProcessBinding.command
    pending = iter(transcripts)
    monkeypatch.setattr(signedin._OfflineProcessBinding, "command",
        lambda self: original_command(signedin._OfflineProcessBinding(next(pending))))
    authorized = []
    def synthetic_dispatch(reservation):
        assert ledger.get_model_reservation(reservation)["status"] == "active"
        authorized.append(reservation.ref)
    monkeypatch.setattr(backend, "_authorize_dispatch", synthetic_dispatch)
    observer = LocalTraceObserver(planner.methods.root / "observer.sqlite", source_digest="a" * 64,
        runtime_digest="b" * 64, policy_digest=digest(policy))
    backend.trace_observer = observer
    ids = [request["dataset"]["binding"][key] for key in ("matrix_artifact_id", "metadata_artifact_id")]
    state = planner.create(request["task"], {"observed": {}, "inferred": {}, "unknowns": [], "artifact_ids": ids},
        actor="participant", data_classification="synthetic", limits=AgentLimits(max_selections=3, timeout_seconds=30,
            session_timeout_seconds=10), rna_input_contract=request["dataset"]["binding"])
    allocations = backend.generation_allocations(state["limits"])
    trial_id = "synthetic-three-stage-control-flow"
    campaign = ledger.open_campaign("offline-three-stage", session_id=state["session_id"], policy=policy,
        capability=backend.capabilities(), identity={"source_digest": "a" * 64, "runtime_digest": "b" * 64,
            "expected_plan_hash": digest(state), "parent_call_limits": allocations,
            "protected_parent_allocations": allocations, "trial_id": trial_id}, backend=backend)
    planner.attach_model_campaign(state["session_id"], ledger, campaign["campaign_id"], backend,
        expected_plan_hash=digest(state), actor="participant")
    publication = participant_publication_context(manifest=request["publication"]["manifest_path"],
        expected_root=request["publication"]["root_hash"], allow_evidence_read=True)
    grounded = GroundedSelector(selector, publication, methods=planner.methods)
    planner.generate_candidates(state["session_id"], grounded, actor="participant", allow_model_execution=True)
    planner.resolve_capabilities(state["session_id"], grounded, [method["version_id"]], actor="participant", allow_model_execution=True)
    drafts = planner.design_trials(state["session_id"], grounded, actor="participant", allow_model_execution=True)
    history = planner.get(state["session_id"])["selection_history"]
    assert drafts and len(history) == len(authorized) == 3
    assert all(row["model_call_allocation"]["jobs"] == 1 for row in history)
    assert all("model_usage" not in row and "model_job_receipt_ref" in row for row in history)
    assert ledger.get_campaign(campaign["campaign_id"])["verified_jobs"] == 3
    phases = {row["phase"] for row in observer.read_events(trial_id)}
    assert {"method_scientific_planning", "method_capability_resolution", "method_trial_design"} <= phases
    assert observer.read_events(state["session_id"]) == []
    assert observer.failure_count == 0
    for row in history:
        retained = ledger.get_model_reservation(row["model_reservation_ref"])
        capture = json.loads(backend._artifact(row["model_capture_ref"], max_bytes=3_000_000))
        material = signedin.request_material(capture["context"])
        assert capture["context"]["publication_evidence"] == publication
        assert retained["request"]["prompt_digest"] == hashlib.sha256(material["prompt"].encode()).hexdigest()
    assert planner.methods.list_records("run") == []


def test_matching_authenticated_generation_grant_cannot_launch_unqualified_native_route(tmp_path, monkeypatch):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from devbench.analysis import codex_signedin as signedin
    from devbench.analysis.rna_scientific_trial import AuthenticatedTrialAuthority, generate_question_workflow
    from devbench.analysis.workflow_operations import WorkflowOperationLedger
    from tests.test_rna_scientific_trial_v2 import configured_v2
    from tests.test_rna_trial_authority import signed_approval

    request, planner, _, branch, method, _, _ = configured_v2(tmp_path)
    request = as_signedin(request)
    monkeypatch.setattr(signedin, "inspect_official_client", lambda *args, **kwargs: {
        "client": {"synthetic_official_client_observation": True},
        "authentication": {"method": "chatgpt_signin", "source": "synthetic_status_fixture"}})
    launches = []
    def forbidden_launch(*args, **kwargs):
        launches.append(True)
        raise AssertionError("An unqualified native process must never launch")
    monkeypatch.setattr(signedin, "_run_process", forbidden_launch)
    selector = signedin.SignedInCodexActionSelector(requested_model="fixture-model", private_root=planner.methods.root)
    ledger = WorkflowOperationLedger(planner.methods.root / "native-job-ledger.sqlite")
    policy = {"format": "devbench.model-job-budget-policy/1", "kind": "generation_jobs", "max_jobs": 3,
        "max_concurrent_jobs": 1, "max_total_seconds": 30, "max_context_bytes": 48_000,
        "max_output_bytes": 100_000, "max_children": 0,
        "reconciliation": {"jobs": 3, "seconds": 24, "context_bytes": 48_000}}
    backend = signedin.SignedInCodexInvestigationBackend(planner.methods, selector, policy=policy, ledger=ledger)
    request["engineering"].update(backend.runtime_identity())
    key = Ed25519PrivateKey.generate()
    signed_approval(request, key, tmp_path, grant_id="signedin-native-denied")
    authority = AuthenticatedTrialAuthority(tmp_path / "native-authority.sqlite", workspace=tmp_path,
        issuer_keys={"fixture-issuer": key.public_key()}, runtime_verifier=lambda context: backend.runtime_identity(),
        clock=lambda: NOW, signedin_backend=backend)
    with pytest.raises(PermissionError, match="blocked_backend_capability|unqualified"):
        generate_question_workflow(request, planner=planner, workspace=tmp_path, selector=selector,
            branch_id=branch["branch_id"], version_ids=[method["version_id"]], actor="participant",
            authority=authority, backend=backend, allow_model_execution=True, now=NOW,
            output=tmp_path / "native-generation-output", scientific_workflow=RecordingScientificRuntime())
    assert launches == []
    assert planner.methods.list_records("run") == []
    assert planner.methods.list_records("method_plan") == []
