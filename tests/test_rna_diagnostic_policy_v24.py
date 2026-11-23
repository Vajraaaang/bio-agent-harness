"""Synthetic T18 routing controls; these do not qualify scientific policies."""
import importlib
import importlib.util
import os
from copy import deepcopy
from dataclasses import replace

import pytest

from tests.test_review_hold_lifecycle import review as _review_fixture

review = _review_fixture

def api():
    assert importlib.util.find_spec("devbench.analysis.rna_diagnostic_policy") is not None, (
        "T18 needs immutable complete policies and finite scoped routing"
    )
    return importlib.import_module("devbench.analysis.rna_diagnostic_policy")


def context(**changes):
    return {"assay": "bulk_rna", "design": "B-P", "label_provenance": "declared",
            "coverage": "complete", "exposure": "diagnostic",
            "recipients": ["controller", "challenger"], **changes}


def graph():
    return {"graph": {"node_count": 3, "edges": [[0, 1], [1, 0]]}}


def state(**changes):
    return {"affected_use": "claim-a", "threat": "graph_isolated_nodes", "intent": "assess",
            "prerequisite": "verified", "prerequisite_reason": None,
            "current_design_review": False, "possible_threat": True,
            "resources_available": True, "check_status": "not_started", "checks_used": 0,
            "elapsed_seconds": 0, "post_result_changes": [], "obligations": [], **changes}


def test_complete_immutable_registered_families_are_not_scientific_qualification():
    m = api()
    policies = m.registered_rna_diagnostic_policies()
    assert set(policies) == {"Q-DESIGN", "Q-INFLUENCE", "Q-QL", "Q-REFERENCE", "Q-GRAPH"}
    for name, policy in policies.items():
        data = policy.to_dict()
        assert data["family"] == name
        assert data["qualification"]["status"] == "unqualified"
        assert data["qualification"]["required_evaluations"] == ["X3", "X4", "X6"]
        assert data["trigger"]["kind"] == "unresolved"
        assert data["calculation"]["formula"] and data["calculation"]["missingness"]
        assert data["required_inputs"] and data["access"]["recipients"]
        assert data["obligations"] and data["budget"]["max_checks"] > 0
        original = policy.sha256
        data["budget"]["max_checks"] = 7
        assert policy.sha256 == original and policy.to_dict()["budget"]["max_checks"] == 1
        with pytest.raises((AttributeError, TypeError)):
            policy.canonical = b"changed"


def test_v2_profiles_are_immutable_and_prefit_checks_need_a_supported_trigger():
    m = api()
    v1 = m.registered_rna_diagnostic_policies()
    v2 = m.registered_rna_diagnostic_policies(version="2")
    assert v1.keys() == v2.keys()
    assert all(row.to_dict()["trigger"]["kind"] == "unresolved" for row in v1.values())
    assert all(row.to_dict()["qualification"]["status"] == "unqualified" for row in v2.values())
    policy = v2["Q-GRAPH"]
    changed = policy.to_dict()
    changed["trigger"]["threshold"] = 2
    with pytest.raises(ValueError, match="immutable"):
        m.RnaDiagnosticPolicy.from_dict(changed)

    prefit = state(intent="prefit", affected_use="fit", possible_threat=False)
    unavailable = m.route_rna_diagnostic(policy, graph(), context(), prefit)
    assert unavailable["execution"]["decision"] is True
    assert unavailable["route"] == "unavailable"
    exploratory = m.route_rna_diagnostic(policy, graph(), context(), {**prefit, "possible_threat": True})
    assert exploratory["route"] == "registered_check" and exploratory["inferential_status"] == "exploratory"
    _, fixture_acceptance = qualified_policy_and_authority()
    admissible = m.route_rna_diagnostic(policy, graph(), context(), prefit, authority=fixture_acceptance)
    assert admissible["route"] == "registered_check" and admissible["inferential_status"] == "policy_conditional"


def test_core_validator_rejects_policy_shortcuts_and_unsafe_actions():
    from devharness_core.methods import validate_diagnostic_policy
    policy = api().registered_rna_diagnostic_policies()["Q-GRAPH"].to_dict()
    assert validate_diagnostic_policy(policy) == policy
    mutations = [lambda p: p.pop("access"), lambda p: p["qualification"].update(status="qualified"),
                 lambda p: p["actions"][0].update(kind="exclude_donor"),
                 lambda p: p["budget"].update(max_checks=True),
                 lambda p: p["trigger"].update(threshold=3),
                 lambda p: p["qualification"].update(required_evaluations=["X3"])]
    for change in mutations:
        bad = deepcopy(policy)
        change(bad)
        with pytest.raises(ValueError):
            validate_diagnostic_policy(bad)


def test_graph_arithmetic_available_does_not_resolve_biological_decision():
    m = api()
    policy = m.registered_rna_diagnostic_policies()["Q-GRAPH"]
    result = m.evaluate_rna_diagnostic(policy, graph(), context())
    assert result["execution_status"] == "completed"
    assert result["metrics"]["isolated_nodes"] == 1
    assert result["decision"] is None
    assert result["decision_reason"] == "trigger_unresolved"
    assert result["qualification_status"] == "unqualified"
    assert result["scientific_validity_established"] is False
    route = m.route_rna_diagnostic(policy, graph(), context(), state())
    assert route["route"] == "registered_check"
    assert route["inferential_status"] == "exploratory"
    assert route["satisfied_obligation_ids"] == []
    assert route == m.route_rna_diagnostic(policy, graph(), context(), state())


@pytest.mark.parametrize("changes,expected", [
    ({"prerequisite": "known_failure", "prerequisite_reason": "contrast_not_estimable"}, "dependent_fit_hold"),
    ({"prerequisite": "missing_fact", "prerequisite_reason": "biological_unit_identity"}, "necessary_fact"),
    ({"prerequisite": "unsupported_target", "prerequisite_reason": "target_outside_assay"}, "unsupported_target"),
    ({"resources_available": False}, "unavailable"),
    ({"check_status": "completed"}, "terminate_unresolved"),
    ({"check_status": "infeasible"}, "terminate_unresolved"),
    ({"check_status": "failed"}, "terminate_unresolved"),
    ({"checks_used": 1}, "terminate_unresolved"),
    ({"elapsed_seconds": 300}, "terminate_unresolved"),
    ({"post_result_changes": ["cohort"]}, "exploratory_branch"),
])
def test_finite_routes_name_affected_use_and_resumption(changes, expected):
    m = api()
    route = m.route_rna_diagnostic(m.registered_rna_diagnostic_policies()["Q-GRAPH"],
        graph(), context(), state(**changes))
    assert route["route"] == expected
    assert route["affected_use"] == "claim-a" and route["resumption_condition"]
    assert route["independent_work_continues"] is True
    assert route["satisfied_obligation_ids"] == []


def test_missing_inputs_and_unadmitted_recipients_fail_closed():
    m = api()
    policy = m.registered_rna_diagnostic_policies()["Q-GRAPH"]
    assert m.route_rna_diagnostic(policy, {}, context(), state())["route"] == "unavailable"
    assert m.route_rna_diagnostic(policy, graph(), context(coverage="unknown"), state())["route"] == "outside_qualified_scope"
    with pytest.raises(PermissionError, match="recipient"):
        m.evaluate_rna_diagnostic(policy, graph(), context(recipients=["external_model"]))


def qualified_policy_and_authority(*, missing=(), changes=None):
    m = api()
    value = m.registered_rna_diagnostic_policies()["Q-GRAPH"].to_dict()
    value["policy_id"], value["version"] = "synthetic-graph-control", "test-1"
    value["trigger"] = {"kind": "metric_comparison", "metric": "isolated_nodes", "operator": "gt",
        "threshold": 0, "action_id": value["actions"][0]["action_id"]}
    policy = m.RnaDiagnosticPolicy.from_dict(value)
    def verify(subject):
        accepted = {"format": "devbench.rna-diagnostic-acceptance/1", "subject": deepcopy(subject),
            "protocol_sha256": subject.get("protocol_sha256", "a" * 64), "acceptance": "independently_accepted",
            "evaluations": [{"evaluation_id": name, "evidence_sha256": letter * 64,
                "independent": True, "accepted": True} for name, letter in (("X3", "b"), ("X4", "c"), ("X6", "d"))
                if name not in missing],
            "matched_obligations": [{"obligation_id": "graph-review", "threat": "graph_isolated_nodes"}]}
        if changes:
            changes(accepted)
        return accepted
    return policy, m.DiagnosticQualificationAuthority(verify)


def test_qualified_trigger_and_matched_quantitative_alternative_are_narrow():
    m = api()
    policy, authority = qualified_policy_and_authority()
    obligations = [{"obligation_id": "graph-review", "threat": "graph_isolated_nodes"},
                   {"obligation_id": "visual-review", "threat": "visual_adequacy"}]
    result = m.route_rna_diagnostic(policy, graph(), context(), state(obligations=obligations), authority=authority)
    assert result["route"] == "registered_check" and result["qualification_status"] == "qualified"
    assert result["satisfied_obligation_ids"] == []  # A trigger schedules a check, not proof of adequacy.
    no_threat = {"graph": {"node_count": 2, "edges": [[0, 1], [1, 0]]}}
    result = m.route_rna_diagnostic(policy, no_threat, context(),
        state(obligations=obligations, possible_threat=False), authority=authority)
    assert result["route"] == "matched_quantitative_alternative"
    assert result["satisfied_obligation_ids"] == ["graph-review"]
    assert result["remaining_obligation_ids"] == ["visual-review"]


@pytest.mark.parametrize("missing", [("X3",), ("X4",), ("X6",)])
def test_each_independent_evaluation_is_required(missing):
    m = api()
    policy, authority = qualified_policy_and_authority(missing=missing)
    with pytest.raises(PermissionError, match="X3|X4|X6"):
        m.route_rna_diagnostic(policy, graph(), context(), state(), authority=authority)


@pytest.mark.parametrize("change", [
    lambda p: p["subject"].update(policy_sha256="f" * 64),
    lambda p: p["subject"].update(implementation_sha256="f" * 64),
    lambda p: p["evaluations"][0].update(independent=False),
    lambda p: p.update(acceptance="self_reported"),
    lambda p: p["matched_obligations"].append({"obligation_id": "visual-review", "threat": "visual_adequacy"}),
])
def test_forged_stale_or_overbroad_qualification_is_rejected(change):
    m = api()
    policy, authority = qualified_policy_and_authority(changes=change)
    with pytest.raises(PermissionError):
        m.route_rna_diagnostic(policy, graph(), context(), state(), authority=authority)
    with pytest.raises(TypeError):
        m.route_rna_diagnostic(policy, graph(), context(), state(), authority={"qualified": True})


def test_design_uses_actual_compiler_and_review_is_separate():
    from tests.test_erql_designs import profiles, spec
    m = api()
    policy = m.registered_rna_diagnostic_policies()["Q-DESIGN"]
    inputs = {"counts": profiles("B-P"), "specification": spec("B-P")}
    result = m.evaluate_rna_diagnostic(policy, inputs, context(exposure="structural"))
    assert result["metrics"]["rank"] == 4 and result["metrics"]["residual_df"] == 2
    assert result["metrics"]["estimable"] is True
    assert result["scientific_validity_established"] is False
    route = m.route_rna_diagnostic(policy, inputs, context(exposure="structural"),
        state(affected_use="fit", intent="prefit", possible_threat=False, current_design_review=True))
    assert route["route"] == "unavailable"
    assert route["reason"] == "qualified_procedure_profile_admission_required"
    broken = {"counts": profiles("B-I", technical=lambda key: {"batch": key.condition}),
        "specification": replace(spec("B-I"), technical_factors=("batch",))}
    route = m.route_rna_diagnostic(policy, broken, context(design="B-I", exposure="structural"),
        state(affected_use="fit", intent="prefit", possible_threat=False, current_design_review=True))
    assert route["route"] == "dependent_fit_hold"


def influence_inputs():
    from devbench.core.contrast_design import compile_contrast_design
    from devbench.core.count_profiles import ProfileKey
    from tests.test_erql_designs import profiles, spec
    counts, specification = profiles("B-P", extra=(ProfileKey("unit-3", "unit-3-base", "base", None),
        ProfileKey("unit-3", "unit-3-treated", "treated", None))), spec("B-P")
    full = compile_contrast_design(counts, specification)
    saved = {"full": {"fit_id": "full", "design_sha256": full.design_sha256,
        "effects": [{"gene_id": "g", "effect": 1.0, "q_value": 0.01}]}, "omissions": []}
    for index in range(4):
        unit = f"unit-{index}"
        reduced = replace(counts, profiles=tuple(row for row in counts.profiles if row.key.unit_id != unit))
        design = compile_contrast_design(reduced, specification)
        saved["omissions"].append({"unit_id": unit, "fit_id": f"omit-{index}",
            "design_sha256": design.design_sha256, "status": "completed", "reason": None,
            "effects": [{"gene_id": "g", "effect": 0.5 if index < 2 else -0.5, "q_value": 0.9}]})
    return {"counts": counts, "specification": specification, "saved_fits": saved}


def test_actual_pair_sensitivity_records_direction_without_significance_reversal():
    m = api()
    inputs = influence_inputs()
    result = m.evaluate_rna_diagnostic(m.registered_rna_diagnostic_policies()["Q-INFLUENCE"],
        inputs, context(exposure="target_outcome"))
    rows = result["views"]["sensitivity"]
    assert rows[0]["effect_delta"] == -0.5 and rows[0]["direction_reversal"] is False
    assert rows[0]["full_q_value"] == 0.01 and rows[0]["omission_q_value"] == 0.9
    assert rows[2]["direction_reversal"] is True
    assert result["exposure"] == "target_outcome"
    assert result["decision"] is None and result["automatic_actions"] == []
    inputs["saved_fits"]["omissions"][0]["design_sha256"] = "f" * 64
    with pytest.raises(ValueError, match="design"):
        m.evaluate_rna_diagnostic(m.registered_rna_diagnostic_policies()["Q-INFLUENCE"],
            inputs, context(exposure="target_outcome"))


def test_infeasible_omissions_are_retained_without_made_up_estimates():
    from devbench.core.contrast_design import compile_contrast_design
    m = api()
    inputs = influence_inputs()
    counts = inputs["counts"]
    inputs["counts"] = replace(counts, profiles=tuple(row for row in counts.profiles if row.key.unit_id != "unit-2"))
    inputs["saved_fits"]["full"]["design_sha256"] = compile_contrast_design(
        inputs["counts"], inputs["specification"]).design_sha256
    inputs["saved_fits"]["omissions"] = []
    result = m.evaluate_rna_diagnostic(m.registered_rna_diagnostic_policies()["Q-INFLUENCE"],
        inputs, context(exposure="target_outcome"))
    assert len(result["views"]["reduced_designs"]) == 3
    assert all(row["status"] == "infeasible" for row in result["views"]["reduced_designs"])
    assert all(row["effect_delta"] is None for row in result["views"]["sensitivity"])


def composition():
    return {"compositions": [{"id": "a", "cell_type": "A", "numerator": 1, "denominator": 2,
        "member_ids": ["c1", "c2"], "numerator_ids": ["c1"], "donor_id": "d1", "sample_id": "s1", "condition": "base"}],
        "denominator_kind": "all_saved_cells", "biological_unit": "donor", "unit": "fraction"}


def test_reference_composition_uses_membership_and_never_validates_reference():
    m = api()
    policy = m.registered_rna_diagnostic_policies()["Q-REFERENCE"]
    value = {"composition": composition()}
    result = m.evaluate_rna_diagnostic(policy, value, context(assay="single_cell_rna", design="S-P"))
    assert result["views"]["composition"][0]["fraction"] == 0.5
    assert result["reference_validity"] == "unassessed"
    value["composition"]["compositions"][0]["numerator_ids"] = ["foreign"]
    with pytest.raises(ValueError, match="membership"):
        m.evaluate_rna_diagnostic(policy, value, context(assay="single_cell_rna", design="S-P"))


def test_plot_states_keep_rendering_access_and_assessment_distinct():
    from devbench.analysis import scientific_plot_verification as m
    assert hasattr(m, "diagnostic_plot_state"), "T18 needs separate plot evidence states"
    value = m.diagnostic_plot_state(rendered=True, render_verified=False, image_accessible=True,
        scientific_assessment="unavailable", matched_threats=())
    assert value["rendered"] is True and value["render_verified"] is False
    assert value["scientific_assessment"] == "unavailable"
    with pytest.raises(ValueError):
        m.diagnostic_plot_state(rendered=False, render_verified=True, image_accessible=False,
            scientific_assessment="unavailable", matched_threats=())


def test_old_score_preview_reports_scientific_qualification_separately(tmp_path):
    from devbench.analysis.method_validation import extract_observation_diagnostics
    from tests.test_method_diagnostic_profiles import fixture
    before, after, _, value = fixture(tmp_path)
    result = extract_observation_diagnostics(before, after, value["validation"])
    assert result["diagnostic_status"]["execution"] == "completed"
    assert result["diagnostic_status"]["scientific_qualification"] == "unqualified"
    assert result["diagnostic_status"]["decision"] == "unresolved"


def test_existing_design_audit_exposes_registered_unqualified_policy(tmp_path):
    from devbench.analysis.rna_design_audit import audit_rna_design
    from tests.test_rna_design_audit import prepared_plan
    planner, plan, _ = prepared_plan(tmp_path)
    result = audit_rna_design(plan, planner.methods)
    assert result["diagnostic_policy"]["family"] == "Q-DESIGN"
    assert result["diagnostic_policy"]["scientific_qualification"] == "unqualified"
    assert result["diagnostic_policy"]["execution"] == "not_executed"
    assert result["diagnostic_policy"]["legacy_structural_audit"] == "completed"
    assert result["execution_authority_granted"] is False


def native_ql_inputs():
    from devharness_controlled.erql_contracts import VERSIONS
    from devharness_controlled.erql_results import STATISTICS
    request = {"profile": "ERQL-1.1", "feature_ids": ["g"], "profile_ids": ["a", "b", "c", "d"],
        "counts": [[20], [20], [40], [40]], "groups": ["base", "base", "target", "target"],
        "unit_ids": ["u1", "u2", "u3", "u4"], "numerator": "target", "denominator": "base"}
    statistics = {key: {"state": "finite", "value": 1.0} for key in STATISTICS}
    statistics.update(p={"state": "finite", "value": 0.2}, df_total={"state": "finite", "value": 3.0},
        df_prior={"state": "positive_infinity", "value": None},
        df_residual_adj={"state": "missing", "value": None})
    result = {"schema_version": "erql_result/1", **{key: deepcopy(request[key]) for key in
        ("profile", "feature_ids", "profile_ids", "groups")}, "package_versions": VERSIONS,
        "filter": {"keep": [True], "min_sample_size": 2, "cpm_cutoff": 10 / 30 * 1e6,
            "pre_filter_library_sizes": [20, 20, 40, 40]},
        "normalization": {"library_sizes": [20, 20, 40, 40], "factors": [1.0] * 4, "reference_profile_id": "a"},
        "fit": {"top_proportion": 0.1, "dispersion": {"state": ["finite"], "value": [0.1]}},
        "genes": [{"gene_id": "g", "status": "tested", "filter_reason": None,
            "support": {"numerator": 2, "denominator": 2}, "statistics": statistics,
            "interval": None, "interval_reason": "no_interval_in_profile"}]}
    return {"request": request, "result": result}


def test_ql_retains_actual_native_missingness_and_parent_profile_scope():
    m = api()
    policy = m.registered_rna_diagnostic_policies()["Q-QL"]
    inputs = native_ql_inputs()
    result = m.evaluate_rna_diagnostic(policy, inputs, context(exposure="target_outcome"))
    native = result["views"]["native_gene_statistics"][0]["statistics"]
    assert native["df_prior"] == {"state": "positive_infinity", "value": None}
    assert native["df_residual_adj"] == {"state": "missing", "value": None}
    assert result["decision"] is None
    inputs["request"]["profile"] = inputs["result"]["profile"] = "ERQL-1.1-estimateDisp-candidate/1"
    with pytest.raises(ValueError, match="estimateDisp"):
        m.evaluate_rna_diagnostic(policy, inputs, context(exposure="target_outcome"))


def test_relative_reference_has_exact_membership_no_pseudocount_and_no_reference_validation():
    m = api()
    inputs = {"relative_reference": {"reference_id": "declared-reference", "unit": "count", "profiles": [
        {"profile_id": "a", "counts": {"g1": 20, "g2": 10}, "numerator_ids": ["g1"], "denominator_ids": ["g2"]},
        {"profile_id": "b", "counts": {"g1": 0, "g2": 10}, "numerator_ids": ["g1"], "denominator_ids": ["g2"]}]}}
    result = m.evaluate_rna_diagnostic(m.registered_rna_diagnostic_policies()["Q-REFERENCE"], inputs, context())
    assert result["views"]["relative_reference"][0]["log2_ratio"] == pytest.approx(1)
    assert result["views"]["relative_reference"][1]["log2_ratio"] is None
    assert result["reference_validity"] == "unassessed"
    assert result["decision"] is None


def setup_policy_workflow(review, *, numerical=False, policy=None, inputs=None):
    from devbench.analysis.scientific_review_holds import (
        ResolutionPredicate,
        ReviewHoldLifecycle,
        ReviewObligation,
        obligations_digest,
    )
    from devbench.platform.adaptive_workflow import AdaptiveWorkflow
    from devbench.platform.workflow_actions import WorkflowActions
    from tests.test_investigation_invalidation import artifact, link, step
    from tests.test_review_hold_lifecycle import binding, complete, objection
    m = api()
    assert hasattr(WorkflowActions, "apply_rna_diagnostic_policy"), "T18 needs the owning WorkflowActions route"
    policy = policy or m.registered_rna_diagnostic_policies()["Q-GRAPH"]
    value = policy.to_dict()
    if value["policy_id"] == "Q-GRAPH":
        value["policy_id"], value["version"] = "synthetic-route-control", "test-1"
    value["actions"][0]["operation_id"] = "synthetic"
    policy = m.RnaDiagnosticPolicy.from_dict(value)
    flow = AdaptiveWorkflow(review.ledger, "study")
    source_sha = m._digest(inputs or graph())
    frozen = artifact("frozen-input").model_copy(update={"sha256": source_sha})
    flow.register_artifact(frozen)
    runner = ReviewHoldLifecycle(review.authority, "study", registered_checks=("synthetic",))
    runner.register_evidence("check-input", source_sha, relevant_entity_ids=("claim-a",))
    predicate = (api_review().DiagnosticResolutionPredicate(kind="DiagnosticThreatAdequate",
        owner_id=policy.to_dict()["policy_id"], policy_sha256=policy.sha256, threat="graph_isolated_nodes",
        evidence_required=("check-input",), resumption_condition="The exact graph-topology obligation is satisfied") if numerical else
        ResolutionPredicate(kind="CounterevidenceResolved", owner_id="reviewer", evidence_required=("check-input",),
            resumption_condition="The exact graph-topology obligation is satisfied"))
    obligation = ReviewObligation(obligation_id="graph-review", entity_id="claim-a", stage="claim",
        evidence_ids=("check-input",), predicate=predicate)
    current = binding(obligations_sha256=obligations_digest((obligation,)))
    runner.register_obligations((obligation,), binding=current)
    complete(runner, current=current, covered=("graph-review",))
    request = api_review().CheckRequest(threat="graph_isolated_nodes", affected_claim_ids=("claim-a",),
        required_evidence=("check-input",), registered_check_id="synthetic", predicate=objection().predicate,
        input_evidence_ids=("check-input",))
    hold_id = runner.admit_objection("design-1", objection(check_request=request))["hold_id"]
    actions = WorkflowActions(None, review.ledger, actor="tester", selector=None, review_lifecycle=runner,
        review_binding=lambda _: current)
    return actions, flow, policy, hold_id, step("policy-check", link("counts", "frozen-input", immutable=True))


def api_review():
    return importlib.import_module("devbench.analysis.scientific_review_holds")


@pytest.mark.wal
def test_workflow_routes_real_check_with_bound_obligation_without_resolving_hold(review):
    actions, flow, policy, hold_id, step = setup_policy_workflow(review)
    route = actions.apply_rna_diagnostic_policy(policy, graph(), context(),
        state(obligations=[{"obligation_id": "graph-review", "threat": "graph_isolated_nodes"}]),
        input_evidence_id="check-input", hold_id=hold_id, workflow=flow, step=step)
    assert route["route"] == "registered_check"
    assert route["scheduled_check"]["action_id"] == "policy-check"
    current = actions.review_lifecycle.state()
    assert current["holds"][hold_id]["status"] == "open"
    assert current["history"][-1]["action"] == "diagnostic_route"
    assert current["history"][-1]["detail"]["route"]["policy_sha256"] == policy.sha256
    assert any(not item["satisfied"] for rows in current["obligations"].values() for item in rows.values())
    with review.ledger._database() as db:
        assert "policy-check" in flow._state(db).nodes


@pytest.mark.wal
def test_workflow_infeasible_check_withdraws_only_dependent_use_and_retains_predicate(review):
    actions, _, policy, hold_id, _ = setup_policy_workflow(review)
    route = actions.apply_rna_diagnostic_policy(policy, graph(), context(),
        state(check_status="infeasible", obligations=[{"obligation_id": "graph-review", "threat": "graph_isolated_nodes"}]),
        input_evidence_id="check-input", hold_id=hold_id)
    assert route["route"] == "terminate_unresolved"
    current = actions.review_lifecycle.state()
    assert set(current["withdrawn"]) == {"claim-a"}
    assert current["holds"][hold_id]["status"] == "open"
    assert "independent" not in current["withdrawn"] and "table-a" not in current["withdrawn"]


@pytest.mark.wal
def test_workflow_cannot_schedule_again_by_resetting_caller_check_state(review):
    actions, flow, policy, hold_id, step = setup_policy_workflow(review)
    s = state(obligations=[{"obligation_id": "graph-review", "threat": "graph_isolated_nodes"}])
    actions.apply_rna_diagnostic_policy(policy, graph(), context(), s,
        input_evidence_id="check-input", hold_id=hold_id, workflow=flow, step=step)
    again = actions.apply_rna_diagnostic_policy(policy, graph(), context(), s,
        input_evidence_id="check-input", hold_id=hold_id, workflow=flow,
        step=step.model_copy(update={"step_id": "duplicate"}))
    assert again["route"] in {"check_pending", "terminate_unresolved"}
    with review.ledger._database() as db:
        assert "duplicate" not in flow._state(db).nodes


@pytest.mark.wal
def test_workflow_check_budget_remains_bound_when_caller_omits_original_hold(review):
    actions, flow, policy, hold_id, step = setup_policy_workflow(review)
    s = state(obligations=[{"obligation_id": "graph-review", "threat": "graph_isolated_nodes"}])
    actions.apply_rna_diagnostic_policy(policy, graph(), context(), s,
        input_evidence_id="check-input", hold_id=hold_id, workflow=flow, step=step)
    again = actions.apply_rna_diagnostic_policy(policy, graph(), context(), s,
        input_evidence_id="check-input")
    assert again["route"] == "check_pending"


@pytest.mark.wal
def test_qualified_runtime_satisfies_only_typed_numerical_obligation(review):
    m = api()
    policy, authority = qualified_policy_and_authority()
    inputs = {"graph": {"node_count": 2, "edges": [[0, 1], [1, 0]]}}
    actions, _, policy, hold_id, _ = setup_policy_workflow(review, numerical=True, policy=policy, inputs=inputs)
    actions.diagnostic_qualification = authority
    route = actions.apply_rna_diagnostic_policy(policy, inputs, context(),
        state(possible_threat=False, obligations=[{"obligation_id": "graph-review", "threat": "graph_isolated_nodes"}]),
        input_evidence_id="check-input", hold_id=hold_id)
    assert route["satisfied_obligation_ids"] == ["graph-review"]
    current = actions.review_lifecycle.state()
    bound = actions.review_binding("claim-a").sha256()
    assert current["obligations"][bound]["graph-review"]["satisfied"] is True
    assert current["holds"][hold_id]["status"] == "open"
    assert current["obligations"][bound]["graph-review"]["proof"]["policy_sha256"] == policy.sha256


@pytest.mark.wal
def test_numerical_qualification_cannot_clear_generic_scientific_predicate(review):
    policy, authority = qualified_policy_and_authority()
    inputs = {"graph": {"node_count": 2, "edges": [[0, 1], [1, 0]]}}
    actions, _, policy, hold_id, _ = setup_policy_workflow(review, policy=policy, inputs=inputs)
    actions.diagnostic_qualification = authority
    with pytest.raises(PermissionError, match="numerical|diagnostic"):
        actions.apply_rna_diagnostic_policy(policy, inputs, context(),
            state(possible_threat=False, obligations=[{"obligation_id": "graph-review", "threat": "graph_isolated_nodes"}]),
            input_evidence_id="check-input", hold_id=hold_id)
    assert not next(iter(actions.review_lifecycle.state()["obligations"].values()))["graph-review"]["satisfied"]


@pytest.mark.wal
def test_generic_mechanical_or_challenger_paths_cannot_clear_numerical_predicate(review):
    from tests.test_review_hold_lifecycle import digest
    actions, _, _, _, _ = setup_policy_workflow(review, numerical=True)
    runner, current = actions.review_lifecycle, actions.review_binding("claim-a")
    with pytest.raises(PermissionError, match="diagnostic"):
        runner.satisfy_obligation("graph-review", current, verifier_id="alias-verifier",
            proof_sha256=digest("b"), evidence_ids=("check-input",))
    with pytest.raises(PermissionError, match="diagnostic"):
        runner.assess_obligation("graph-review", current, cycle_id="design-1",
            evidence_ids=("check-input",), assessment_sha256=digest("f"))


@pytest.mark.wal
@pytest.mark.parametrize("race", ["revision", "binding"])
def test_qualification_race_cannot_commit_stale_numerical_satisfaction(review, race):
    from devbench.engine.workflow_operations import OperationConflict
    m = api()
    policy, trusted = qualified_policy_and_authority()
    inputs = {"graph": {"node_count": 2, "edges": [[0, 1], [1, 0]]}}
    actions, _, policy, hold_id, _ = setup_policy_workflow(review, numerical=True, policy=policy, inputs=inputs)
    actual_binding = actions.review_binding("claim-a")
    current = [actual_binding]
    actions.review_binding = lambda _: current[0]
    calls = []
    def verify(subject):
        calls.append(subject)
        accepted = trusted.accepted_proof_verifier(subject)
        if len(calls) == 2:
            if race == "revision":
                actions.review_lifecycle.register_evidence("changed-during-verification", "e" * 64,
                    relevant_entity_ids=("claim-a",))
            else:
                current[0] = current[0].model_copy(update={"evidence_sha256": "e" * 64})
        return accepted
    actions.diagnostic_qualification = m.DiagnosticQualificationAuthority(verify)
    with pytest.raises(OperationConflict, match="revision|binding"):
        actions.apply_rna_diagnostic_policy(policy, inputs, context(),
            state(possible_threat=False, obligations=[{"obligation_id": "graph-review", "threat": "graph_isolated_nodes"}]),
            input_evidence_id="check-input", hold_id=hold_id)
    assert not actions.review_lifecycle.state()["obligations"][actual_binding.sha256()]["graph-review"]["satisfied"]


@pytest.mark.wal
@pytest.mark.parametrize("change", ["revoked", "replacement", "source"])
def test_qualification_custody_and_implementation_are_rechecked_at_atomic_satisfaction(review, monkeypatch, change):
    from devbench.engine.workflow_operations import OperationConflict
    m = api()
    policy, trusted = qualified_policy_and_authority()
    inputs = {"graph": {"node_count": 2, "edges": [[0, 1], [1, 0]]}}
    actions, _, policy, hold_id, _ = setup_policy_workflow(review, numerical=True, policy=policy, inputs=inputs)
    implementation = ["a" * 64]
    monkeypatch.setattr(m, "implementation_sha256", lambda: implementation[0])
    calls = []
    def verify(subject):
        calls.append(subject)
        accepted = trusted.accepted_proof_verifier(subject)
        if len(calls) == 3:
            if change == "revoked":
                return None
            if change == "replacement":
                accepted["protocol_sha256"] = "f" * 64
            if change == "source":
                implementation[0] = "f" * 64
        return accepted
    actions.diagnostic_qualification = m.DiagnosticQualificationAuthority(verify)
    with pytest.raises((PermissionError, OperationConflict), match="qualification|acceptance|source|implementation"):
        actions.apply_rna_diagnostic_policy(policy, inputs, context(),
            state(possible_threat=False, obligations=[{"obligation_id": "graph-review", "threat": "graph_isolated_nodes"}]),
            input_evidence_id="check-input", hold_id=hold_id)
    assert not next(iter(actions.review_lifecycle.state()["obligations"].values()))["graph-review"]["satisfied"]


def test_qualification_verifier_custody_cannot_be_replaced_in_place():
    m = api()
    authority = m.DiagnosticQualificationAuthority(lambda subject: None)
    with pytest.raises((AttributeError, TypeError)):
        authority.accepted_proof_verifier = lambda subject: {"qualified": True}


@pytest.mark.wal
@pytest.mark.parametrize("alias_literature", [False, True])
def test_quantitative_pass_preserves_literature_and_broader_scientific_obligations(
    workspace_tmp_path, monkeypatch, alias_literature,
):
    from devbench.analysis.literature_adapter import literature_use_current
    from devbench.analysis.scientific_review_holds import (
        DiagnosticResolutionPredicate,
        ResolutionPredicate,
        ReviewObligation,
        obligations_digest,
    )
    from devbench.platform.workflow_actions import WorkflowActions
    from tests.test_literature_claim_support import service
    from tests.test_review_hold_lifecycle import binding
    m = api()
    literature, external, calls, review = service(workspace_tmp_path, monkeypatch)
    policy, authority = qualified_policy_and_authority()
    inputs = {"graph": {"node_count": 2, "edges": [[0, 1], [1, 0]]}}
    review.register_evidence("diagnostic-input", m._digest(inputs), relevant_entity_ids=("claim",))
    obligations = (ReviewObligation(obligation_id="graph-review", entity_id="claim", stage="claim",
        evidence_ids=("diagnostic-input",), predicate=DiagnosticResolutionPredicate(
            owner_id=policy.to_dict()["policy_id"], policy_sha256=policy.sha256, threat="graph_isolated_nodes",
            evidence_required=("diagnostic-input",), resumption_condition="Assess mathematical topology")),
        ReviewObligation(obligation_id="literature", entity_id="claim", stage="claim",
            evidence_ids=("diagnostic-input",), predicate=ResolutionPredicate(kind="CounterevidenceResolved",
                owner_id="challenger", evidence_required=("diagnostic-input",), resumption_condition="Assess the external biological claim")))
    current = binding(obligations_sha256=obligations_digest(obligations))
    review.register_obligations(obligations, binding=current)
    literature.bind_claim(external.model_copy(update={"review_binding_sha256": current.sha256(),
        "obligation_id": "graph-review" if alias_literature else "literature"}))
    actions = WorkflowActions(None, review.ledger, actor="test", selector=None, review_lifecycle=review,
        review_binding=lambda _: current, diagnostic_qualification=authority)
    def apply():
        return actions.apply_rna_diagnostic_policy(policy, inputs, context(),
            state(affected_use="claim", possible_threat=False, obligations=[
                {"obligation_id": "graph-review", "threat": "graph_isolated_nodes"},
                {"obligation_id": "literature", "threat": "biological_claim"}]), input_evidence_id="diagnostic-input")
    if alias_literature:
        with pytest.raises(PermissionError, match="Literature"):
            apply()
    else:
        assert apply()["satisfied_obligation_ids"] == ["graph-review"]
    observed = review.state()["obligations"][current.sha256()]
    assert observed["graph-review"]["satisfied"] is (not alias_literature)
    assert observed["literature"]["satisfied"] is False
    assert literature_use_current(review, "claim", current.sha256()) is False
    assert calls == []


@pytest.mark.wal
def test_policy_identity_version_cannot_be_rebound_after_durable_registration(review):
    from devbench.engine.workflow_operations import OperationConflict
    m = api()
    actions, flow, policy, hold_id, step = setup_policy_workflow(review)
    s = state(obligations=[{"obligation_id": "graph-review", "threat": "graph_isolated_nodes"}])
    actions.apply_rna_diagnostic_policy(policy, graph(), context(), s,
        input_evidence_id="check-input", hold_id=hold_id, workflow=flow, step=step)
    changed = policy.to_dict()
    changed["budget"]["max_checks"] = 2
    with pytest.raises(OperationConflict, match="immutable|version"):
        actions.apply_rna_diagnostic_policy(m.RnaDiagnosticPolicy.from_dict(changed), graph(), context(), s,
            input_evidence_id="check-input", hold_id=hold_id)


def test_unsupported_scalar_trigger_does_not_fabricate_decision():
    m = api()
    value = m.registered_rna_diagnostic_policies()["Q-GRAPH"].to_dict()
    value["policy_id"], value["version"] = "synthetic-missing-metric", "test-1"
    value["trigger"] = {"kind": "metric_comparison", "metric": "biological_validity", "operator": "gt",
        "threshold": 0, "action_id": value["actions"][0]["action_id"]}
    result = m.evaluate_rna_diagnostic(m.RnaDiagnosticPolicy.from_dict(value), graph(), context())
    assert result["decision"] is None and result["metrics"]["isolated_nodes"] == 1
    assert result["decision_reason"] == "trigger_measurement_unavailable"


def test_unqualified_post_result_repair_does_not_withdraw_unaffected_original_use():
    m = api()
    result = m.route_rna_diagnostic(m.registered_rna_diagnostic_policies()["Q-GRAPH"],
        graph(), context(), state(post_result_changes=["normalization"]))
    assert result["route"] == "exploratory_branch"
    assert result["invalidate_dependent_use"] is False


def test_unmatched_threat_cannot_schedule_an_unrelated_diagnostic():
    m = api()
    result = m.route_rna_diagnostic(m.registered_rna_diagnostic_policies()["Q-GRAPH"], graph(), context(),
        state(threat="ambient_contamination"))
    assert result["route"] == "outside_qualified_scope"
    assert result["action"] is None


def test_canonical_family_identity_and_version_cannot_be_rewritten():
    m = api()
    changed = m.registered_rna_diagnostic_policies()["Q-GRAPH"].to_dict()
    changed["budget"]["max_checks"] = 2
    with pytest.raises(ValueError, match="immutable"):
        m.RnaDiagnosticPolicy.from_dict(changed)


def test_acceptance_must_bind_authoritative_evaluation_protocol():
    m = api()
    policy, authority = qualified_policy_and_authority(changes=lambda value: value.update(protocol_sha256="f" * 64))
    with pytest.raises(PermissionError, match="protocol|acceptance"):
        m.route_rna_diagnostic(policy, graph(), context(), state(), authority=authority)


def test_capability_catalog_does_not_claim_a_rendered_or_scientifically_assessed_image(tmp_path, monkeypatch):
    from devbench.analysis import scientific_plot_verification as m
    monkeypatch.setattr(m, "LEDGER", tmp_path / "missing-ledger.json")
    value = m.verified_capability_catalog()
    assert value["diagnostic_plot_state"] == m.diagnostic_plot_state(rendered=False, render_verified=False,
        image_accessible=False, scientific_assessment="unavailable")


def test_qualified_policy_does_not_promote_a_residual_unqualified_threat():
    m = api()
    policy, authority = qualified_policy_and_authority()
    inputs = {"graph": {"node_count": 2, "edges": [[0, 1], [1, 0]]}}
    result = m.route_rna_diagnostic(policy, inputs, context(), state(
        obligations=[{"obligation_id": "graph-review", "threat": "graph_isolated_nodes"}]), authority=authority)
    assert result["route"] == "registered_check"
    assert result["inferential_status"] == "exploratory"
    assert result["satisfied_obligation_ids"] == []


def test_qualified_trigger_retains_explicit_exploratory_action_scope():
    m = api()
    policy, authority = qualified_policy_and_authority()
    value = policy.to_dict()
    value["actions"][0]["inferential_status"] = "exploratory"
    result = m.route_rna_diagnostic(m.RnaDiagnosticPolicy.from_dict(value), graph(), context(), state(), authority=authority)
    assert result["route"] == "registered_check" and result["inferential_status"] == "exploratory"


def test_missing_saved_effect_has_explicit_absence_in_sensitivity_view():
    m = api()
    inputs = influence_inputs()
    inputs["saved_fits"]["omissions"][0]["effects"][0]["effect"] = None
    result = m.evaluate_rna_diagnostic(m.registered_rna_diagnostic_policies()["Q-INFLUENCE"],
        inputs, context(exposure="target_outcome"))
    row = result["views"]["sensitivity"][0]
    assert row["effect_delta"] is None and row["reason"] == "saved_effect_unavailable"


def activate_policy_execution(actions):
    from tests.test_investigation_publication_epochs import authorize
    authority = actions.review_lifecycle.authority
    with actions.ledger._database() as db:
        revision = authority._revision(db, "study")
    grant = authority.commit("study", expected_revision=revision, record={"kind": "grant",
        "record_id": "diagnostic-grant", "binding_id": "binding-1", "grant_sha256": "b" * 64})
    return authority.activate_execution("study", expected_revision=grant["revision"], binding_id="binding-1",
        grant_id="diagnostic-grant", max_jobs=8, authorize=authorize)


@pytest.mark.wal
def test_cancelled_execution_cannot_accept_numerical_obligation(review):
    from devbench.engine.workflow_operations import OperationBlocked
    policy, trusted = qualified_policy_and_authority()
    inputs = {"graph": {"node_count": 2, "edges": [[0, 1], [1, 0]]}}
    actions, _, policy, hold_id, _ = setup_policy_workflow(review, numerical=True, policy=policy, inputs=inputs)
    actions.diagnostic_qualification = trusted
    activate_policy_execution(actions)
    cancelled = actions.review_lifecycle.authority.cancel_execution("study", actor="tester")
    with pytest.raises(OperationBlocked, match="cancelled"):
        actions.apply_rna_diagnostic_policy(policy, inputs, context(),
            state(possible_threat=False, obligations=[{"obligation_id": "graph-review", "threat": "graph_isolated_nodes"}]),
            input_evidence_id="check-input", hold_id=hold_id)
    current = actions.review_lifecycle.state()
    assert not next(iter(current["obligations"].values()))["graph-review"]["satisfied"]
    assert actions.review_lifecycle.authority.execution("study")["epoch"] == cancelled["epoch"]
    assert current["holds"][hold_id]["status"] == "open"


@pytest.mark.wal
def test_active_execution_fence_is_retained_with_numerical_satisfaction(review):
    policy, trusted = qualified_policy_and_authority()
    inputs = {"graph": {"node_count": 2, "edges": [[0, 1], [1, 0]]}}
    actions, _, policy, hold_id, _ = setup_policy_workflow(review, numerical=True, policy=policy, inputs=inputs)
    actions.diagnostic_qualification = trusted
    execution = activate_policy_execution(actions)
    actions.apply_rna_diagnostic_policy(policy, inputs, context(),
        state(possible_threat=False, obligations=[{"obligation_id": "graph-review", "threat": "graph_isolated_nodes"}]),
        input_evidence_id="check-input", hold_id=hold_id)
    proof = next(iter(actions.review_lifecycle.state()["obligations"].values()))["graph-review"]["proof"]
    assert proof["execution_fence"]["epoch"] == execution["epoch"]
    assert proof["execution_fence"]["authorization_sha256"] == execution["authorization_sha256"]


@pytest.mark.wal
def test_last_verifier_binding_change_rolls_back_numerical_satisfaction(review):
    from devbench.engine.workflow_operations import OperationConflict
    m = api()
    policy, trusted = qualified_policy_and_authority()
    inputs = {"graph": {"node_count": 2, "edges": [[0, 1], [1, 0]]}}
    actions, _, policy, hold_id, _ = setup_policy_workflow(review, numerical=True, policy=policy, inputs=inputs)
    original = actions.review_binding("claim-a")
    current, calls = [original], []
    actions.review_binding = lambda _: current[0]
    def verify(subject):
        calls.append(subject)
        if len(calls) == 3:
            current[0] = original.model_copy(update={"evidence_sha256": "e" * 64})
        return trusted.accepted_proof_verifier(subject)
    actions.diagnostic_qualification = m.DiagnosticQualificationAuthority(verify)
    with pytest.raises(OperationConflict, match="binding"):
        actions.apply_rna_diagnostic_policy(policy, inputs, context(),
            state(possible_threat=False, obligations=[{"obligation_id": "graph-review", "threat": "graph_isolated_nodes"}]),
            input_evidence_id="check-input", hold_id=hold_id)
    assert len(calls) == 3
    assert not actions.review_lifecycle.state()["obligations"][original.sha256()]["graph-review"]["satisfied"]


@pytest.mark.wal
def test_last_verifier_revision_change_commits_before_satisfaction_write(review):
    from devbench.engine.workflow_operations import OperationConflict

    m = api()
    policy, trusted = qualified_policy_and_authority()
    inputs = {"graph": {"node_count": 2, "edges": [[0, 1], [1, 0]]}}
    actions, _, policy, hold_id, _ = setup_policy_workflow(review, numerical=True, policy=policy, inputs=inputs)
    calls = []

    def verify(subject):
        calls.append(subject)
        if len(calls) == 3:
            actions.review_lifecycle.register_evidence("new-during-final-verification", "e" * 64,
                relevant_entity_ids=("claim-a",))
        return trusted.accepted_proof_verifier(subject)

    actions.diagnostic_qualification = m.DiagnosticQualificationAuthority(verify)
    with pytest.raises(OperationConflict, match="revision"):
        actions.apply_rna_diagnostic_policy(policy, inputs, context(),
            state(possible_threat=False, obligations=[{"obligation_id": "graph-review", "threat": "graph_isolated_nodes"}]),
            input_evidence_id="check-input", hold_id=hold_id)
    current = actions.review_lifecycle.state()
    assert len(calls) == 3
    assert "new-during-final-verification" in current["evidence"]
    assert not next(iter(current["obligations"].values()))["graph-review"]["satisfied"]
    assert all(event["action"] != "diagnostic_route" for event in current["history"])


@pytest.mark.wal
def test_last_verifier_cannot_pass_after_grant_expiry(review, monkeypatch):
    from devbench.engine import scientific_authority
    from devbench.engine.workflow_operations import OperationBlocked
    m = api()
    policy, trusted = qualified_policy_and_authority()
    inputs = {"graph": {"node_count": 2, "edges": [[0, 1], [1, 0]]}}
    actions, _, policy, hold_id, _ = setup_policy_workflow(review, numerical=True, policy=policy, inputs=inputs)
    execution = activate_policy_execution(actions)
    calls = []
    def verify(subject):
        calls.append(subject)
        if len(calls) == 3:
            monkeypatch.setattr(scientific_authority.time, "time", lambda: execution["expires_at"] + 1)
        return trusted.accepted_proof_verifier(subject)
    actions.diagnostic_qualification = m.DiagnosticQualificationAuthority(verify)
    with pytest.raises(OperationBlocked, match="expired"):
        actions.apply_rna_diagnostic_policy(policy, inputs, context(),
            state(possible_threat=False, obligations=[{"obligation_id": "graph-review", "threat": "graph_isolated_nodes"}]),
            input_evidence_id="check-input", hold_id=hold_id)
    assert not next(iter(actions.review_lifecycle.state()["obligations"].values()))["graph-review"]["satisfied"]


@pytest.mark.wal
@pytest.mark.parametrize("owner_module", ["devbench.analysis.scientific_review_holds",
    "devbench.engine.scientific_authority", "devbench.platform.adaptive_workflow",
    "devbench.platform.workflow_adaptive_actions", "devbench.platform.workflow_diagnostic_actions",
    "devbench.platform.workflow_serial_evidence", "devbench.platform.workflow_state",
    "devbench.analysis._publication_policy"])
def test_satisfaction_owner_source_replacement_invalidates_actual_acceptance(review, monkeypatch, owner_module):
    from pathlib import Path
    m = api()
    policy, trusted = qualified_policy_and_authority()
    inputs = {"graph": {"node_count": 2, "edges": [[0, 1], [1, 0]]}}
    actions, _, policy, hold_id, _ = setup_policy_workflow(review, numerical=True, policy=policy, inputs=inputs)
    owner = Path(importlib.import_module(owner_module).__file__).resolve()
    read_bytes, changed, calls = Path.read_bytes, [False], []
    def read_source(path):
        data = read_bytes(path)
        return data + b"\n# synthetic replacement\n" if path == owner and changed[0] else data
    monkeypatch.setattr(Path, "read_bytes", read_source)
    def verify(subject):
        calls.append(subject)
        if len(calls) == 3:
            changed[0] = True
        return trusted.accepted_proof_verifier(subject)
    actions.diagnostic_qualification = m.DiagnosticQualificationAuthority(verify)
    with pytest.raises(PermissionError, match="source|implementation"):
        actions.apply_rna_diagnostic_policy(policy, inputs, context(),
            state(possible_threat=False, obligations=[{"obligation_id": "graph-review", "threat": "graph_isolated_nodes"}]),
            input_evidence_id="check-input", hold_id=hold_id)
    assert not next(iter(actions.review_lifecycle.state()["obligations"].values()))["graph-review"]["satisfied"]


@pytest.mark.wal
@pytest.mark.parametrize("outcome", ["failed", "succeeded", "omitted"])
def test_actual_registered_dag_check_outcome_stops_unresolved_use(review, outcome):
    from tests.test_investigation_invalidation import Worker
    actions, flow, policy, hold_id, step = setup_policy_workflow(review)
    activate_policy_execution(actions)
    s = state(obligations=[{"obligation_id": "graph-review", "threat": "graph_isolated_nodes"}])
    actions.apply_rna_diagnostic_policy(policy, graph(), context(), s,
        input_evidence_id="check-input", hold_id=hold_id, workflow=flow, step=step)
    if outcome == "omitted":
        flow.declare_sweep("diagnostic-sweep", {"policy-check": "resource_unavailable"})
    else:
        actual = flow.dispatch("policy-check", Worker(fail=("policy-check",) if outcome == "failed" else ()), actor="tester")
        assert actual["status"] == outcome
    route = actions.apply_rna_diagnostic_policy(policy, graph(), context(), s,
        input_evidence_id="check-input", hold_id=hold_id, workflow=flow)
    assert route["route"] == "terminate_unresolved" and route["terminal"] is True
    assert route["registered_check_states"][0]["status"] == outcome
    current = actions.review_lifecycle.state()
    assert current["holds"][hold_id]["status"] == "open"
    assert set(current["withdrawn"]) == {"claim-a"}
    assert not next(iter(current["obligations"].values()))["graph-review"]["satisfied"]


@pytest.mark.wal
def test_pending_registered_check_expires_without_a_rhetorical_loop(review):
    actions, flow, policy, hold_id, step = setup_policy_workflow(review)
    s = state(obligations=[{"obligation_id": "graph-review", "threat": "graph_isolated_nodes"}])
    actions.apply_rna_diagnostic_policy(policy, graph(), context(), s,
        input_evidence_id="check-input", hold_id=hold_id, workflow=flow, step=step)
    route = actions.apply_rna_diagnostic_policy(policy, graph(), context(), {**s, "elapsed_seconds": 301},
        input_evidence_id="check-input", hold_id=hold_id, workflow=flow)
    assert route["route"] == "terminate_unresolved"
    assert route["reason"] == "diagnostic_budget_exhausted"
    assert route["independent_work_continues"] is True


@pytest.mark.wal
def test_pending_check_deadline_survives_new_controller_and_reset_elapsed_state(review, monkeypatch):
    import time

    from devbench.analysis.scientific_review_holds import ReviewHoldLifecycle
    from devbench.platform.workflow_actions import WorkflowActions
    clock = [1000.0]
    monkeypatch.setattr(time, "time", lambda: clock[0])
    actions, flow, policy, hold_id, step = setup_policy_workflow(review)
    s = state(obligations=[{"obligation_id": "graph-review", "threat": "graph_isolated_nodes"}])
    actions.apply_rna_diagnostic_policy(policy, graph(), context(), s,
        input_evidence_id="check-input", hold_id=hold_id, workflow=flow, step=step)
    clock[0] = 1301.0
    resumed = WorkflowActions(None, review.ledger, actor="tester", selector=None,
        review_lifecycle=ReviewHoldLifecycle(review.authority, "study"), review_binding=actions.review_binding)
    route = resumed.apply_rna_diagnostic_policy(policy, graph(), context(), s,
        input_evidence_id="check-input", hold_id=hold_id)
    assert route["route"] == "terminate_unresolved"
    assert route["reason"] == "diagnostic_budget_exhausted"
    assert route["budget_usage"]["elapsed_seconds"] == 301


def test_diagnostic_qualification_is_not_inferential_procedure_qualification():
    from tests.test_erql_designs import profiles, spec
    m = api()
    value = m.registered_rna_diagnostic_policies()["Q-DESIGN"].to_dict()
    value.update(policy_id="synthetic-qualified-design-diagnostic", version="test-1")
    value["trigger"] = {"kind": "metric_comparison", "metric": "rank", "operator": "gt",
        "threshold": 99, "action_id": value["actions"][0]["action_id"]}
    policy = m.RnaDiagnosticPolicy.from_dict(value)
    _, trusted = qualified_policy_and_authority()
    def verify(subject):
        accepted = trusted.accepted_proof_verifier(subject)
        accepted["matched_obligations"] = []
        return accepted
    route = m.route_rna_diagnostic(policy, {"counts": profiles("B-P"), "specification": spec("B-P")},
        context(exposure="structural"), state(affected_use="fit", intent="prefit", possible_threat=False,
            current_design_review=True), authority=m.DiagnosticQualificationAuthority(verify))
    assert route["qualification_status"] == "qualified"
    assert route["execution"]["metrics"]["estimable"] is True
    assert route["route"] == "unavailable" and route["reason"] == "qualified_procedure_profile_admission_required"
    with pytest.raises(ValueError):
        m.DiagnosticRouteState.model_validate(state(procedure_profile_qualified=True))


@pytest.mark.parametrize("changes", [{"elapsed_seconds": 301}, {"check_status": "failed"}, {"check_status": "infeasible"}])
def test_qualified_negative_diagnostic_cannot_override_expired_or_failed_check(changes):
    m = api()
    policy, trusted = qualified_policy_and_authority()
    inputs = {"graph": {"node_count": 2, "edges": [[0, 1], [1, 0]]}}
    route = m.route_rna_diagnostic(policy, inputs, context(), state(possible_threat=False,
        obligations=[{"obligation_id": "graph-review", "threat": "graph_isolated_nodes"}], **changes), authority=trusted)
    assert route["route"] == "terminate_unresolved"
    assert route["satisfied_obligation_ids"] == []


def another_policy_hold(actions, hold_id, *, action_kind="read_only"):
    original = deepcopy(actions.review_lifecycle.state()["holds"][hold_id]["objection"])
    original["evidence_ids"] = ["check-input"]
    original["predicate"].update(evidence_required=["check-input"],
        resumption_condition="Assess the second exact graph obligation")
    original["check_request"].update(action_kind=action_kind, predicate=deepcopy(original["predicate"]))
    result = actions.review_lifecycle.admit_objection("design-1", original)
    assert result["hold_id"] != hold_id
    return result["hold_id"]


@pytest.mark.wal
def test_completed_check_does_not_lend_its_expired_deadline_to_a_new_hold(review, monkeypatch):
    import time

    from tests.test_investigation_invalidation import Worker

    m = api()
    value = m.registered_rna_diagnostic_policies()["Q-GRAPH"].to_dict()
    value.update(policy_id="synthetic-two-check-budget", version="test-1")
    value["budget"].update(max_checks=2, max_branch_steps=2)
    policy = m.RnaDiagnosticPolicy.from_dict(value)
    clock = [time.time()]
    monkeypatch.setattr(time, "time", lambda: clock[0])
    actions, flow, policy, first_hold, step = setup_policy_workflow(review, policy=policy)
    activate_policy_execution(actions)
    assert schedule_concrete(actions, flow, policy, first_hold, step)["route"] == "registered_check"
    assert flow.dispatch(step.step_id, Worker(), actor="tester")["status"] == "succeeded"

    old_deadline = diagnostic_deadline(actions)
    clock[0] = old_deadline + 1
    second_hold = another_policy_hold(actions, first_hold)
    second = step.model_copy(update={"step_id": "second-check", "branch_id": "second-check"})
    route = schedule_concrete(actions, flow, policy, second_hold, second)
    assert route["route"] == "registered_check"
    starts = [event["detail"] for event in actions.review_lifecycle.state()["history"]
        if event["action"] == "diagnostic_check_start"]
    assert starts[0]["deadline_epoch"] == old_deadline
    assert starts[1]["deadline_epoch"] > clock[0]
    assert flow.dispatch(second.step_id, Worker(), actor="tester")["status"] == "succeeded"


@pytest.mark.wal
def test_invalidated_check_generation_has_its_own_pending_deadline(review, monkeypatch):
    import time

    from devbench.platform.adaptive_workflow import BasisUse
    from tests.test_investigation_invalidation import Worker

    m = api()
    value = m.registered_rna_diagnostic_policies()["Q-GRAPH"].to_dict()
    value.update(policy_id="synthetic-two-generation-budget", version="test-1")
    value["budget"].update(max_checks=2, max_branch_steps=2)
    clock = [time.time()]
    monkeypatch.setattr(time, "time", lambda: clock[0])
    actions, flow, policy, hold_id, step = setup_policy_workflow(review, policy=m.RnaDiagnosticPolicy.from_dict(value))
    activate_policy_execution(actions)
    flow.record_basis("check-code", "code", "a" * 64)
    step = step.model_copy(update={"basis": (BasisUse(basis_id="check-code"),)})
    assert schedule_concrete(actions, flow, policy, hold_id, step)["route"] == "registered_check"
    flow.record_basis("check-code", "code", "b" * 64)
    clock[0] = diagnostic_deadline(actions) + 1
    assert schedule_concrete(actions, flow, policy, hold_id, step)["route"] == "registered_check"
    starts = [event["detail"] for event in actions.review_lifecycle.state()["history"]
        if event["action"] == "diagnostic_check_start"]
    assert [item["generation"] for item in starts] == [1, 2]
    assert starts[1]["deadline_epoch"] > clock[0]
    assert flow.dispatch(step.step_id, Worker(), actor="tester")["status"] == "succeeded"


@pytest.mark.wal
@pytest.mark.parametrize("different_hold", [False, True])
def test_competing_controllers_reserve_one_shared_policy_check(review, monkeypatch, different_hold):
    import threading
    from concurrent.futures import ThreadPoolExecutor

    from devbench.engine.workflow_operations import OperationBlocked, OperationConflict
    actions, flow, policy, first_hold, step = setup_policy_workflow(review)
    holds = [first_hold, another_policy_hold(actions, first_hold) if different_hold else first_hold]
    original_state = actions.review_lifecycle.state
    barrier = threading.Barrier(2)
    def synchronized_state():
        value = original_state()
        barrier.wait(timeout=10)
        return value
    monkeypatch.setattr(actions.review_lifecycle, "state", synchronized_state)
    s = state(obligations=[{"obligation_id": "graph-review", "threat": "graph_isolated_nodes"}])
    def apply(index):
        name = "parallel-check-" + str(index)
        try:
            return actions.apply_rna_diagnostic_policy(policy, graph(), context(), s,
                input_evidence_id="check-input", hold_id=holds[index], workflow=flow,
                step=step.model_copy(update={"step_id": name, "branch_id": name}))
        except (OperationBlocked, OperationConflict) as error:
            return {"route": "blocked", "reason": str(error)}
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(apply, (0, 1)))
    monkeypatch.setattr(actions.review_lifecycle, "state", original_state)
    with review.ledger._database() as db:
        nodes = flow._state(db).nodes
    assert len(nodes) == 1, results
    assert sum(result["route"] == "registered_check" for result in results) == 1
    starts = [event["detail"] for event in original_state()["history"] if event["action"] == "diagnostic_check_start"]
    assert len({entry["action_id"] for entry in starts}) == 1


@pytest.mark.wal
@pytest.mark.parametrize("action_kind,effects,mutable", [
    ("new_fit", ["new_fit"], False), ("preprocessing", ["preprocessing"], False),
    ("read_only", ["new_fit"], False), ("read_only", ["read_only"], True),
])
def test_read_only_policy_cannot_borrow_broader_check_authority(review, action_kind, effects, mutable):
    from devbench.platform.adaptive_workflow import AdaptiveStep
    from tests.test_investigation_invalidation import link
    actions, flow, policy, hold_id, step = setup_policy_workflow(review)
    actions.review_lifecycle.registered_checks = {"synthetic": action_kind}
    hold_id = another_policy_hold(actions, hold_id, action_kind=action_kind)
    value = step.model_dump(mode="json")
    value["profile"]["operations"][0]["effects"] = effects
    if mutable:
        value["inputs"] = [link("counts", "upstream", immutable=False).model_dump(mode="json")]
    step = AdaptiveStep.model_validate(value)
    with pytest.raises(PermissionError, match="read.only|policy|effect|mutable"):
        actions.apply_rna_diagnostic_policy(policy, graph(), context(),
            state(obligations=[{"obligation_id": "graph-review", "threat": "graph_isolated_nodes"}]),
            input_evidence_id="check-input", hold_id=hold_id, workflow=flow, step=step)
    with review.ledger._database() as db:
        assert "policy-check" not in flow._state(db).nodes


@pytest.mark.parametrize("saved_status", ["failed", "infeasible"])
def test_saved_refit_failure_reason_survives_ready_reduced_design(saved_status):
    m = api()
    inputs = influence_inputs()
    inputs["saved_fits"]["omissions"][0].update(status=saved_status,
        reason="native_fit_memory_exhausted", effects=[])
    result = m.evaluate_rna_diagnostic(m.registered_rna_diagnostic_policies()["Q-INFLUENCE"],
        inputs, context(exposure="target_outcome"))
    reduced, sensitivity = result["views"]["reduced_designs"][0], result["views"]["sensitivity"][0]
    assert reduced["refusal_codes"] == [] and reduced["status"] == saved_status
    assert reduced["reason"] == sensitivity["reason"] == "native_fit_memory_exhausted"
    assert sensitivity["effect_delta"] is None


@pytest.mark.wal
def test_stale_terminal_route_cannot_commit_withdrawal(review, monkeypatch):
    from devbench.engine.workflow_operations import OperationConflict
    actions, _, policy, hold_id, _ = setup_policy_workflow(review)
    original_binding, write = actions.review_binding("claim-a"), actions.review_lifecycle._write
    current = [original_binding]
    actions.review_binding = lambda _: current[0]
    def change_after_registration(action, *args, **kwargs):
        result = write(action, *args, **kwargs)
        if action == "register_diagnostic_policy":
            current[0] = original_binding.model_copy(update={"inputs_sha256": "e" * 64})
        return result
    monkeypatch.setattr(actions.review_lifecycle, "_write", change_after_registration)
    with pytest.raises(OperationConflict, match="binding"):
        actions.apply_rna_diagnostic_policy(policy, graph(), context(),
            state(check_status="infeasible", obligations=[{"obligation_id": "graph-review", "threat": "graph_isolated_nodes"}]),
            input_evidence_id="check-input", hold_id=hold_id)
    assert "claim-a" not in actions.review_lifecycle.state()["withdrawn"]


@pytest.mark.wal
def test_stale_binding_after_actual_insert_rolls_back_dag_and_check(review, monkeypatch):
    from devbench.engine.workflow_operations import OperationConflict
    actions, flow, policy, hold_id, step = setup_policy_workflow(review)
    original_binding, insert = actions.review_binding("claim-a"), flow.insert
    current = [original_binding]
    actions.review_binding = lambda _: current[0]
    def change_after_insert(*args, **kwargs):
        result = insert(*args, **kwargs)
        current[0] = original_binding.model_copy(update={"inputs_sha256": "e" * 64})
        return result
    monkeypatch.setattr(flow, "insert", change_after_insert)
    with pytest.raises(OperationConflict, match="binding"):
        actions.apply_rna_diagnostic_policy(policy, graph(), context(),
            state(obligations=[{"obligation_id": "graph-review", "threat": "graph_isolated_nodes"}]),
            input_evidence_id="check-input", hold_id=hold_id, workflow=flow, step=step)
    with review.ledger._database() as db:
        assert "policy-check" not in flow._state(db).nodes
    current = actions.review_lifecycle.state()
    assert current["holds"][hold_id]["checks"] == []
    assert any(event["action"] == "diagnostic_check_start" for event in current["history"])


@pytest.mark.wal
def test_adaptive_insert_respects_caller_transaction_and_rejects_foreign_connection(tmp_path):
    import inspect

    from tests.test_investigation_invalidation import make, step
    flow, foreign = make(tmp_path), make(tmp_path, name="foreign.sqlite")
    assert "connection" in inspect.signature(flow.insert).parameters
    with foreign.ledger._database() as db:
        db.execute("BEGIN IMMEDIATE")
        with pytest.raises(PermissionError, match="ledger|transaction"):
            flow.insert(step("foreign"), connection=db)
    with flow.ledger._database() as db:
        db.execute("BEGIN IMMEDIATE")
        flow.insert(step("atomic"), connection=db)
        assert db.in_transaction and "atomic" in flow._state(db).nodes
        db.rollback()
    with flow.ledger._database() as db:
        assert not flow._state(db).nodes


@pytest.mark.wal
def test_interrupted_reservation_counts_capacity_and_exact_action_can_resume(review, monkeypatch):
    actions, flow, policy, hold_id, step = setup_policy_workflow(review)
    schedule = actions.schedule_review_check
    def interrupt(*args, **kwargs):
        raise RuntimeError("synthetic interruption after durable reservation")
    monkeypatch.setattr(actions, "schedule_review_check", interrupt)
    s = state(obligations=[{"obligation_id": "graph-review", "threat": "graph_isolated_nodes"}])
    with pytest.raises(RuntimeError, match="interruption"):
        actions.apply_rna_diagnostic_policy(policy, graph(), context(), s,
            input_evidence_id="check-input", hold_id=hold_id, workflow=flow, step=step)
    retained = actions.review_lifecycle.state()
    starts = [event["detail"] for event in retained["history"] if event["action"] == "diagnostic_check_start"]
    assert len(starts) == 1 and not retained["withdrawn"]
    monkeypatch.setattr(actions, "schedule_review_check", schedule)
    route = actions.apply_rna_diagnostic_policy(policy, graph(), context(), s,
        input_evidence_id="check-input", hold_id=hold_id, workflow=flow, step=step)
    assert route["route"] == "registered_check"
    with review.ledger._database() as db:
        assert list(flow._state(db).nodes) == ["policy-check"]
    after = [event["detail"] for event in actions.review_lifecycle.state()["history"] if event["action"] == "diagnostic_check_start"]
    assert after == starts


def test_influence_reports_depth_leverage_and_missing_precision_separately():
    m = api()
    result = m.evaluate_rna_diagnostic(m.registered_rna_diagnostic_policies()["Q-INFLUENCE"],
        influence_inputs(), context(exposure="target_outcome"))
    available = result["view_availability"]
    assert available["count_depth"]["status"] == available["design_leverage"]["status"] == "available"
    assert available["distance"]["reason"] == "distance_representation_not_supplied"
    assert available["effect_precision"]["reason"] == "saved_effect_precision_not_supplied"
    assert result["views"]["count_depth"]["units"][0]["library_size"] == 3
    assert [row["leverage"] for row in result["views"]["design_leverage"]["profiles"]] == pytest.approx([5 / 8] * 8)
    assert result["views"]["sensitivity"][0]["effect_delta"] == -0.5
    assert result["views"]["effect_precision"][0]["standard_error"] is None


def test_influence_uses_supplied_distance_representation_and_saved_precision():
    from devbench.core.contrast_design import compile_contrast_design
    m = api()
    inputs = influence_inputs()
    full = compile_contrast_design(inputs["counts"], inputs["specification"])
    inputs["distance_representation"] = {"profile_ids": list(full.profile_ids), "coordinate_ids": ["axis"],
        "coordinates": [[float(i)] for i in range(8)], "representation": "declared_saved_log_expression_axis", "unit": "log_expression"}
    for fit in [inputs["saved_fits"]["full"], *inputs["saved_fits"]["omissions"]]:
        fit["effects"][0].update(standard_error=0.2, precision_source="saved_synthetic_fit")
    result = m.evaluate_rna_diagnostic(m.registered_rna_diagnostic_policies()["Q-INFLUENCE"],
        inputs, context(exposure="target_outcome"))
    assert result["view_availability"]["distance"]["status"] == "available"
    assert result["views"]["distance"]["pairs"][0]["distance"] == 1.0
    assert result["view_availability"]["effect_precision"]["status"] == "available"
    assert result["views"]["effect_precision"][0]["standard_error"] == 0.2


def normalization_input():
    return {"method": "saved_TMM", "profile_ids": ["p1", "p2"], "factors": [0.5, 2.0],
        "library_sizes": [100, 200], "reference_profile_id": "p1"}


def reference_sensitivity_input():
    binding = {"cohort_ids": ["d1", "d2"], "contrast_sha256": "a" * 64,
        "gene_id_namespace": "synthetic", "effect_unit": "log2_fold_change"}
    base = {"reference_id": "original", "fit_id": "full", "method_id": "fixed-estimator",
        "binding": binding, "status": "completed", "reason": None,
        "genes": [{"gene_id": "g", "effect": 1.0}]}
    alt = {**deepcopy(base), "reference_id": "alternative", "fit_id": "alternative-fit",
        "genes": [{"gene_id": "g", "effect": 0.75}]}
    return {"baseline": base, "alternatives": [alt]}


def test_reference_projects_saved_factors_and_marks_absent_alternative_view():
    m = api()
    result = m.evaluate_rna_diagnostic(m.registered_rna_diagnostic_policies()["Q-REFERENCE"],
        {"composition": composition(), "normalization": normalization_input()}, context())
    assert result["views"]["normalization_factors"][0]["effective_library_size"] == 50
    assert result["views"]["normalization_factors"][1]["effective_library_size"] == 400
    assert result["view_availability"]["normalization_factors"]["status"] == "available"
    assert result["view_availability"]["reference_sensitivity"]["reason"] == "reference_sensitivity_not_supplied"
    assert result["reference_validity"] == "unassessed"


def test_reference_sensitivity_compares_only_same_cohort_target_method_and_scale():
    m = api()
    inputs = {"relative_reference": {"reference_id": "original", "unit": "count", "profiles": [
        {"profile_id": "p", "counts": {"g": 20, "ref": 10}, "numerator_ids": ["g"], "denominator_ids": ["ref"]}]},
        "normalization": normalization_input(), "reference_sensitivity": reference_sensitivity_input()}
    policy = m.registered_rna_diagnostic_policies()["Q-REFERENCE"]
    result = m.evaluate_rna_diagnostic(policy, inputs, context(exposure="target_outcome"))
    assert result["views"]["reference_sensitivity"][0]["genes"][0]["effect_delta"] == -0.25
    assert result["view_availability"]["reference_sensitivity"]["status"] == "available"
    inputs["reference_sensitivity"]["alternatives"][0]["binding"]["cohort_ids"] = ["d1"]
    result = m.evaluate_rna_diagnostic(policy, inputs, context(exposure="target_outcome"))
    assert result["view_availability"]["reference_sensitivity"]["status"] == "unavailable"
    assert result["views"]["reference_sensitivity"][0]["reason"] == "cohort_target_or_scale_mismatch"
    assert result["views"]["normalization_factors"][0]["factor"] == 0.5


def full_graph_views():
    graph_context = {"node_ids": ["n0", "n1", "n2"], "method_id": "saved_knn",
        "parameters": {"k": 1}, "representation_sha256": "a" * 64}
    return {**graph(), "graph_context": graph_context,
        "source_graph": {"graph": {"node_count": 3, "edges": [[0, 2], [2, 0]]},
            "context": {**deepcopy(graph_context), "representation_sha256": "b" * 64}},
        "graph_variants": [{"variant_id": "k2", "graph": {"node_count": 3,
            "edges": [[0, 1], [1, 0], [1, 2], [2, 1]]}, "context": {**deepcopy(graph_context), "parameters": {"k": 2}}}],
        "biological_controls": {"exposure": "diagnostic", "controls": [{"control_id": "fixture-positive", "kind": "positive",
            "node_ids": ["n0", "n1", "n2"], "labels": ["A", "B", "A"], "expected_pattern": "same_label_neighbors",
            "label_origin": "independent", "source_reference": "declared_synthetic_control"}]}}


def test_graph_partial_scores_survive_missing_neighborhood_stability_and_controls():
    m = api()
    result = m.evaluate_rna_diagnostic(m.registered_rna_diagnostic_policies()["Q-GRAPH"], graph(), context())
    assert result["metrics"]["isolated_nodes"] == 1
    assert result["views"]["degree_connectivity"]["weak_components"] == 2
    assert result["view_availability"]["source_neighborhoods"]["reason"] == "source_graph_and_node_binding_not_supplied"
    assert result["view_availability"]["parameter_stability"]["reason"] == "parameter_variants_not_supplied"
    assert result["view_availability"]["biological_controls"]["reason"] == "biological_controls_not_supplied"


def test_graph_views_bind_nodes_compare_parameters_and_do_not_invent_control_validity():
    m = api()
    inputs = full_graph_views()
    result = m.evaluate_rna_diagnostic(m.registered_rna_diagnostic_policies()["Q-GRAPH"], inputs, context())
    assert result["views"]["source_neighborhoods"]["mean_jaccard"] == 0
    assert result["views"]["parameter_stability"][0]["mean_jaccard"] == pytest.approx(0.5)
    assert result["views"]["biological_controls"][0]["same_label_edge_fraction"] == 0
    assert result["views"]["biological_controls"][0]["biological_validity"] == "unassessed"
    assert result["decision"] is None
    inputs["source_graph"]["context"]["node_ids"][2] = "foreign"
    result = m.evaluate_rna_diagnostic(m.registered_rna_diagnostic_policies()["Q-GRAPH"], inputs, context())
    assert result["view_availability"]["source_neighborhoods"]["status"] == "unavailable"
    assert result["view_availability"]["source_neighborhoods"]["reason"] == "graph_node_inventory_mismatch"
    assert result["metrics"]["isolated_nodes"] == 1


def test_qualified_broad_graph_obligation_stays_unresolved_with_missing_views():
    m = api()
    policy, authority = qualified_policy_and_authority(changes=lambda proof: proof.update(matched_obligations=[
        {"obligation_id": "broad", "threat": "graph_topology"}]))
    inputs = {"graph": {"node_count": 2, "edges": [[0, 1], [1, 0]]}}
    route = m.route_rna_diagnostic(policy, inputs, context(), state(threat="graph_topology", possible_threat=False,
        obligations=[{"obligation_id": "broad", "threat": "graph_topology"}]), authority=authority)
    assert route["route"] == "unavailable" and route["reason"] == "required_diagnostic_views_unavailable"
    assert route["execution"]["metrics"]["isolated_nodes"] == 0
    assert route["satisfied_obligation_ids"] == []
    assert set(route["missing_required_views"]["broad"]) == {"source_neighborhoods", "parameter_stability", "biological_controls"}


def test_exact_numerical_subobligation_can_use_partial_family_without_clearing_broad_one():
    m = api()
    policy, authority = qualified_policy_and_authority(changes=lambda proof: proof.update(matched_obligations=[
        {"obligation_id": "isolation", "threat": "graph_isolated_nodes"},
        {"obligation_id": "broad", "threat": "graph_topology"}]))
    value = policy.to_dict()
    value["obligations"] = [
        {"obligation_id": "isolation", "threat": "graph_isolated_nodes", "required_views": ["mathematical_topology"]},
        {"obligation_id": "broad", "threat": "graph_topology", "required_views": ["mathematical_topology", "degree_connectivity",
            "source_neighborhoods", "parameter_stability", "biological_controls"]}]
    policy = m.RnaDiagnosticPolicy.from_dict(value)
    inputs = {"graph": {"node_count": 2, "edges": [[0, 1], [1, 0]]}}
    route = m.route_rna_diagnostic(policy, inputs, context(), state(threat="graph_isolated_nodes", possible_threat=False,
        obligations=[{"obligation_id": "isolation", "threat": "graph_isolated_nodes"},
                     {"obligation_id": "broad", "threat": "graph_topology"}]), authority=authority)
    assert route["satisfied_obligation_ids"] == ["isolation"]
    assert route["remaining_obligation_ids"] == ["broad"]
    assert route["missing_required_views"]["broad"]


def test_broad_required_views_cannot_be_weakened_by_an_unqualified_declaration():
    m = api()
    value = m.registered_rna_diagnostic_policies()["Q-GRAPH"].to_dict()
    value.update(policy_id="synthetic-weakened-graph", version="test-1")
    value["obligations"] = [{"obligation_id": "broad", "threat": "graph_topology", "required_views": ["mathematical_topology"]}]
    with pytest.raises(ValueError, match="required.views"):
        m.evaluate_rna_diagnostic(m.RnaDiagnosticPolicy.from_dict(value), graph(), context())


@pytest.mark.wal
def test_candidate_envelope_reuses_validation_and_does_not_mutate_authority(tmp_path):
    from devbench.engine.workflow_operations import OperationBlocked
    from tests.test_investigation_invalidation import artifact, digest, link, make, step
    flow, foreign = make(tmp_path), make(tmp_path, name="foreign.sqlite")
    assert hasattr(flow, "candidate_envelope")
    source = artifact("frozen")
    flow.register_artifact(source)
    flow.record_basis("environment", "environment", digest("effective-environment"))
    node = step("proposed", link("counts", "frozen", immutable=True), basis=({"basis_id": "environment"},))
    before = flow.authority.execution("study")
    envelope = flow.candidate_envelope(node, actor="tester")
    assert envelope["environment_sha256"] == digest("effective-environment")
    assert envelope["pins"][0]["sha256"] == source.sha256
    assert flow.authority.execution("study") == before
    assert not flow.authority.attempts("study")
    with flow.ledger._database() as db:
        assert not flow._state(db).nodes
    with foreign.ledger._database() as db:
        db.execute("BEGIN")
        with pytest.raises(PermissionError, match="ledger|transaction"):
            flow.candidate_envelope(node, actor="tester", connection=db)
    bad = node.model_copy(update={"profile": node.profile.model_copy(update={"input_representations": ("foreign",)})})
    with pytest.raises(OperationBlocked, match="representation"):
        flow.candidate_envelope(bad, actor="tester")


@pytest.mark.wal
def test_same_ledger_connection_does_not_bypass_readonly_facade(tmp_path):
    from devbench.engine.workflow_operations import (
        OperationBlocked,
        WorkflowOperationLedger,
    )
    from devbench.platform.adaptive_workflow import AdaptiveWorkflow
    from tests.test_investigation_invalidation import make, step
    flow = make(tmp_path)
    readonly = AdaptiveWorkflow(WorkflowOperationLedger(flow.ledger.path, readonly=True), "study")
    with flow.ledger._database() as db:
        db.execute("BEGIN IMMEDIATE")
        with pytest.raises(OperationBlocked, match="readonly"):
            readonly.insert(step("forbidden"), connection=db)


@pytest.mark.wal
def test_generic_executor_cannot_reuse_diagnostic_acceptance_as_action_qualification(review):
    from tests.test_investigation_invalidation import Worker
    policy, authority = qualified_policy_and_authority()
    actions, flow, policy, hold_id, step = setup_policy_workflow(review, policy=policy)
    actions.diagnostic_qualification, actions.executor = authority, Worker()
    route = actions.apply_rna_diagnostic_policy(policy, graph(), context(),
        state(obligations=[{"obligation_id": "graph-review", "threat": "graph_isolated_nodes"}]),
        input_evidence_id="check-input", hold_id=hold_id, workflow=flow, step=step)
    assert route["qualification_status"] == "qualified"
    assert route["inferential_status"] == "exploratory"
    assert route["action_qualification"]["status"] == "unavailable"


def concrete_action_workflow(tmp_path, *, accept_action=True, action_proof=None):
    from devbench.analysis.scientific_review_holds import (
        ReviewEntity,
        ReviewHoldLifecycle,
    )
    from devbench.engine.exploratory_oci import ExploratoryOCI
    from devbench.engine.methods import MethodService
    from devbench.engine.workflow_operations import LEDGER_NAME, WorkflowOperationLedger
    from devharness_core.provenance import canonical_json
    from tests.test_exploratory_execution_profile import bound_step, register
    m = api()
    root = tmp_path.resolve()
    methods = MethodService(root)
    ledger = WorkflowOperationLedger(root / LEDGER_NAME, scientific=True)
    ledger.scientific.commit("study", expected_revision=0, record={"kind": "binding", "record_id": "binding-1",
        **dict.fromkeys(("cohort_sha256", "design_sha256", "procedure_sha256", "family_allocation_sha256",
            "exposure_basis_sha256", "policy_sha256"), "a" * 64)})
    review = ReviewHoldLifecycle(ledger.scientific, "study")
    review.register_entities((ReviewEntity(entity_id="fit", kind="fit"),
        ReviewEntity(entity_id="claim-a", kind="claim", dependencies=("fit",)),
        ReviewEntity(entity_id="independent", kind="fit")))
    review.register_evidence("source", "a" * 64, relevant_entity_ids=("fit",))
    policy, diagnostic_authority = qualified_policy_and_authority()
    actions, flow, policy, hold_id, step = setup_policy_workflow(review, policy=policy)
    code = "import json\nfrom pathlib import Path\ndata=json.loads(Path('/inputs/counts').read_bytes())\nparams=json.loads(Path('/method/parameters.json').read_bytes())\nPath('/outputs/out.json').write_text(json.dumps({'nodes':data['graph']['node_count'],'threshold':params['threshold']}))\n"
    method = register(methods, root / "method-source", "diagnostic-control", ("counts",), ("out",), code,
        memory_mb=128, timeout_seconds=10, max_output_bytes=20000)
    worker = ExploratoryOCI(methods, ledger, [{"threshold": 1}, {"threshold": 2}])
    assert worker.training.put(canonical_json(graph())) == m._digest(graph())
    step = bound_step(method, step)
    subjects = []
    def verifier(subject):
        if subject.get("purpose") != "registered_diagnostic_action":
            return diagnostic_authority.accepted_proof_verifier(subject)
        subjects.append(deepcopy(subject))
        if action_proof is not None:
            return deepcopy(action_proof)
        if not accept_action:
            return diagnostic_authority.accepted_proof_verifier(subject)
        return {"format": "devbench.rna-diagnostic-action-acceptance/1", "subject": deepcopy(subject),
            "protocol_sha256": subject["protocol_sha256"], "acceptance": "independently_accepted",
            "evaluations": [{"evaluation_id": name, "evidence_sha256": letter * 64,
                "independent": True, "accepted": True} for name, letter in (("X3", "b"), ("X4", "c"), ("X6", "d"))]}
    actions.executor, actions.allow_data_execution = worker, True
    actions.diagnostic_qualification = m.DiagnosticQualificationAuthority(verifier)
    return actions, flow, policy, hold_id, step, worker, method, subjects


def schedule_concrete(actions, flow, policy, hold_id, step):
    return actions.apply_rna_diagnostic_policy(policy, graph(), context(),
        state(obligations=[{"obligation_id": "graph-review", "threat": "graph_isolated_nodes"}]),
        input_evidence_id="check-input", hold_id=hold_id, workflow=flow, step=step)


@pytest.mark.wal
def test_concrete_registered_source_still_needs_separate_action_acceptance(tmp_path):
    actions, flow, policy, hold_id, step, _, _, subjects = concrete_action_workflow(tmp_path, accept_action=False)
    route = schedule_concrete(actions, flow, policy, hold_id, step)
    assert subjects
    assert route["inferential_status"] == "exploratory"
    assert route["action_qualification"]["reason"] == "independent_action_acceptance_unavailable"


@pytest.mark.wal
def test_action_acceptance_binds_real_contract_parameters_environment_and_pins(tmp_path):
    actions, flow, policy, hold_id, step, _, method, subjects = concrete_action_workflow(tmp_path)
    route = schedule_concrete(actions, flow, policy, hold_id, step)
    assert route["inferential_status"] == "policy_conditional"
    actual = subjects[0]["action_binding"]
    assert actual["contract"] == method["contract"]
    assert actual["parameters"] == {"threshold": 1}
    assert actual["source_artifact"]["sha256"] == method["contract"]["implementation"]["source_sha256"]
    assert actual["envelope"]["environment_sha256"] == step.environment_sha256
    assert actual["envelope"]["pins"][0]["sha256"] == api()._digest(graph())
    assert actual["envelope"]["step"]["profile"] == step.profile.model_dump(mode="json")


@pytest.mark.wal
def test_action_qualification_verifier_runs_outside_write_transactions(tmp_path):
    actions, flow, policy, hold_id, step, _, _, _ = concrete_action_workflow(tmp_path)
    trusted = actions.diagnostic_qualification.accepted_proof_verifier
    observations = []

    def verify(subject):
        if subject.get("purpose") == "registered_diagnostic_action":
            identity = f"verification-{len(observations)}"
            actions.review_lifecycle.register_evidence(identity, "e" * 64, relevant_entity_ids=("claim-a",))
            observations.append(identity)
        return trusted(subject)

    actions.diagnostic_qualification = api().DiagnosticQualificationAuthority(verify)
    route = schedule_concrete(actions, flow, policy, hold_id, step)
    assert route["action_qualification"]["status"] == "qualified"
    flow._prepare_diagnostic_admission(route["action_qualification"]["binding"]["envelope"])
    assert len(observations) >= 2
    assert set(observations) <= actions.review_lifecycle.state()["evidence"].keys()


@pytest.mark.wal
@pytest.mark.parametrize("change", ["profile", "parameters", "pin", "environment", "source"])
def test_action_proof_cannot_be_reused_for_changed_computation(tmp_path, change):
    from devbench.platform.adaptive_workflow import AdaptiveStep, request_digest
    first = concrete_action_workflow(tmp_path / "first")
    route = schedule_concrete(*first[:5])
    proof = route["action_qualification"]["acceptance"]["proof"]
    second = concrete_action_workflow(tmp_path / "second", action_proof=proof)
    actions, flow, policy, hold_id, step, worker, method, _ = second
    if change == "profile":
        value = step.model_dump(mode="json")
        value["profile"].update(profile_id="different-check-profile", version=7)
        step = AdaptiveStep.model_validate(value)
    elif change == "parameters":
        step = step.model_copy(update={"parameters_sha256": request_digest({"threshold": 2})})
    elif change == "pin":
        from tests.test_investigation_invalidation import artifact, link
        flow.register_artifact(artifact("different-input").model_copy(update={"sha256": api()._digest(graph())}))
        step = step.model_copy(update={"inputs": (link("counts", "different-input", immutable=True),)})
    elif change == "environment":
        step = step.model_copy(update={"environment_sha256": "e" * 64})
    else:
        source = worker.methods.artifact_path(method["source_artifact"])
        source.chmod(0o600)
        source.write_bytes(source.read_bytes() + b"\n# changed actual retained code\n")
    with pytest.raises((PermissionError, ValueError), match="action|binding|artifact|hash|environment"):
        schedule_concrete(actions, flow, policy, hold_id, step)
    with flow.ledger._database() as db:
        assert "policy-check" not in flow._state(db).nodes


@pytest.mark.wal
def test_explicit_exploratory_branch_uses_only_matching_broader_check_authority(review):
    from devbench.platform.adaptive_workflow import AdaptiveStep
    actions, flow, policy, hold_id, step = setup_policy_workflow(review)
    value = policy.to_dict()
    value["actions"][0].update(kind="bounded_branch", inferential_status="exploratory")
    policy = api().RnaDiagnosticPolicy.from_dict(value)
    actions.review_lifecycle.registered_checks = {"synthetic": "new_fit"}
    hold_id = another_policy_hold(actions, hold_id, action_kind="new_fit")
    value = step.model_dump(mode="json")
    value["profile"]["operations"][0]["effects"] = ["new_fit"]
    route = schedule_concrete(actions, flow, policy, hold_id, AdaptiveStep.model_validate(value))
    assert route["route"] == "registered_check" and route["inferential_status"] == "exploratory"


@pytest.mark.wal
def test_deadline_expiring_after_insert_rolls_back_scheduling_but_retains_capacity(review, monkeypatch):
    import time

    from devbench.engine.workflow_operations import OperationBlocked
    clock = [1000.0]
    monkeypatch.setattr(time, "time", lambda: clock[0])
    actions, flow, policy, hold_id, step = setup_policy_workflow(review)
    insert = flow.insert
    def expire_after_insert(*args, **kwargs):
        result = insert(*args, **kwargs)
        clock[0] = 1301.0
        return result
    monkeypatch.setattr(flow, "insert", expire_after_insert)
    with pytest.raises(OperationBlocked, match="budget"):
        schedule_concrete(actions, flow, policy, hold_id, step)
    with flow.ledger._database() as db:
        assert "policy-check" not in flow._state(db).nodes
    current = actions.review_lifecycle.state()
    assert current["holds"][hold_id]["checks"] == []
    assert any(event["action"] == "diagnostic_check_start" for event in current["history"])


@pytest.mark.parametrize("family,threat,metric", [
    ("Q-INFLUENCE", "donor_influence", "infeasible_designs"),
    ("Q-REFERENCE", "relative_reference", "view_count"),
])
def test_broad_influence_and_reference_obligations_require_all_named_views(family, threat, metric):
    m = api()
    _, trusted = qualified_policy_and_authority(changes=lambda proof: proof.update(matched_obligations=[
        {"obligation_id": "broad", "threat": threat}]))
    value = m.registered_rna_diagnostic_policies()[family].to_dict()
    value.update(policy_id="synthetic-broad-control", version="test-1")
    value["trigger"] = {"kind": "metric_comparison", "metric": metric, "operator": "gt", "threshold": 99,
        "action_id": value["actions"][0]["action_id"]}
    inputs = influence_inputs() if family == "Q-INFLUENCE" else {"composition": composition()}
    route = m.route_rna_diagnostic(m.RnaDiagnosticPolicy.from_dict(value), inputs,
        context(exposure="target_outcome" if family == "Q-INFLUENCE" else "diagnostic"),
        state(threat=threat, possible_threat=False, obligations=[{"obligation_id": "broad", "threat": threat}]), authority=trusted)
    assert route["execution"]["execution_status"] == "completed" and route["execution"]["metrics"]
    assert route["satisfied_obligation_ids"] == [] and route["missing_required_views"]["broad"]
    assert route["reason"] == "required_diagnostic_views_unavailable"


@pytest.mark.wal
@pytest.mark.skipif(os.environ.get("DEVBENCH_T15_DOCKER") != "1", reason="explicit local Docker smoke opt-in")
@pytest.mark.parametrize("change_before_acceptance", [False, True])
def test_real_oci_action_binding_survives_reconstructed_staging_and_checks_acceptance(tmp_path, monkeypatch, change_before_acceptance):
    from devbench.engine.exploratory_oci import ExploratoryOCI
    from devharness_core.provenance import canonical_json
    actions, flow, policy, hold_id, step, worker, _, _ = concrete_action_workflow(tmp_path)
    route = schedule_concrete(actions, flow, policy, hold_id, step)
    assert route["inferential_status"] == "policy_conditional"
    activate_policy_execution(actions)
    if change_before_acceptance:
        verify = ExploratoryOCI.verify
        def change_after_verified(self, envelope, receipt):
            result = verify(self, envelope, receipt)
            self.parameters[step.parameters_sha256] = canonical_json({"threshold": 2})
            return result
        monkeypatch.setattr(ExploratoryOCI, "verify", change_after_verified)
    outcome = actions.perform_adaptive(flow, "policy-check")
    assert outcome["status"] == ("audit_only" if change_before_acceptance else "succeeded")
    assert bool(outcome["outputs"]) is (not change_before_acceptance)
    if change_before_acceptance:
        assert outcome["staged_outputs"]
    else:
        reopened = type(flow)(flow.ledger, "study")
        assert reopened.current_use("policy-check", "out", purpose="query").sha256


def diagnostic_deadline(actions):
    starts = [event["detail"] for event in actions.review_lifecycle.state()["history"]
        if event["action"] == "diagnostic_check_start"]
    assert len(starts) == 1
    return starts[0]["deadline_epoch"]


@pytest.mark.wal
@pytest.mark.parametrize("boundary", ["before_deadline", "dispatch_expired", "acceptance_expired", "acceptance_expired_empty"])
def test_exploratory_diagnostic_deadline_at_actual_dispatch_and_acceptance(review, monkeypatch, boundary):
    import time

    from devbench.engine.workflow_operations import OperationBlocked
    from tests.test_investigation_invalidation import Worker
    clock = [time.time()]
    monkeypatch.setattr(time, "time", lambda: clock[0])
    actions, flow, policy, hold_id, step = setup_policy_workflow(review)
    if boundary == "acceptance_expired_empty":
        step = step.model_copy(update={"outputs": tuple(port.model_copy(update={"required": False}) for port in step.outputs)})
    schedule_concrete(actions, flow, policy, hold_id, step)
    grant = activate_policy_execution(actions)
    deadline = diagnostic_deadline(actions)
    class ClockedWorker(Worker):
        def run(self, envelope):
            self.receipt = super().run(envelope)
            if boundary.startswith("acceptance_expired"):
                clock[0] = deadline + 1
            if boundary == "acceptance_expired_empty":
                self.receipt = self.receipt.model_copy(update={"outputs": {}})
            return self.receipt
    worker = ClockedWorker()
    actions.executor, actions.allow_data_execution = worker, True
    if boundary == "dispatch_expired":
        clock[0] = deadline + 1
        with pytest.raises(OperationBlocked, match="diagnostic.*budget|diagnostic.*deadline"):
            actions.perform_adaptive(flow, "policy-check")
        assert worker.calls == [] and not flow.history("policy-check")
    else:
        outcome = actions.perform_adaptive(flow, "policy-check")
        assert worker.calls == ["policy-check"]
        assert outcome["status"] == ("audit_only" if boundary.startswith("acceptance_expired") else "succeeded")
        if boundary.startswith("acceptance_expired"):
            assert not outcome["outputs"] and "staged_outputs" in outcome
            assert bool(outcome["staged_outputs"]) is (boundary != "acceptance_expired_empty")
            with pytest.raises(OperationBlocked):
                flow.current_use("policy-check", "out", purpose="query")
        else:
            accepted = flow.current_use("policy-check", "out", purpose="query")
            clock[0] = deadline + 1
            assert flow.current_use("policy-check", "out", purpose="query") == accepted
            token = next(attempt["token"] for attempt in flow.authority.attempts("study")
                if attempt["token"]["attempt_id"] == outcome["attempt_id"])
            assert flow.complete(token, worker.receipt, verify=worker.verify) == outcome
    assert clock[0] < grant["expires_at"]


@pytest.mark.wal
@pytest.mark.skipif(os.environ.get("DEVBENCH_T15_DOCKER") != "1", reason="explicit local Docker smoke opt-in")
@pytest.mark.parametrize("boundary", ["before_deadline", "dispatch_expired", "after_verifier", "acceptance_expired"])
def test_conditional_oci_diagnostic_deadline_at_real_execution_boundaries(tmp_path, monkeypatch, boundary):
    import time

    from devbench.engine.exploratory_oci import ExploratoryOCI
    from devbench.engine.workflow_operations import OperationBlocked
    clock = [time.time()]
    monkeypatch.setattr(time, "time", lambda: clock[0])
    actions, flow, policy, hold_id, step, worker, _, _ = concrete_action_workflow(tmp_path)
    route = schedule_concrete(actions, flow, policy, hold_id, step)
    assert route["inferential_status"] == "policy_conditional"
    grant = activate_policy_execution(actions)
    deadline = diagnostic_deadline(actions)
    if boundary == "dispatch_expired":
        clock[0] = deadline + 1
    elif boundary == "after_verifier":
        admit = actions.adaptive_diagnostic_admission
        def expire_after_admission(connection, envelope, record):
            result = admit(connection, envelope, record)
            clock[0] = deadline + 1
            return result
        monkeypatch.setattr(actions, "adaptive_diagnostic_admission", expire_after_admission)
    elif boundary == "acceptance_expired":
        verify = ExploratoryOCI.verify
        def expire_after_verified(self, envelope, receipt):
            result = verify(self, envelope, receipt)
            clock[0] = deadline + 1
            return result
        monkeypatch.setattr(ExploratoryOCI, "verify", expire_after_verified)
    if boundary in {"dispatch_expired", "after_verifier"}:
        with pytest.raises(OperationBlocked, match="diagnostic.*budget|diagnostic.*deadline"):
            actions.perform_adaptive(flow, "policy-check")
        assert not list(worker.runs.iterdir()) and not flow.history("policy-check")
    else:
        outcome = actions.perform_adaptive(flow, "policy-check")
        assert outcome["status"] == ("audit_only" if boundary == "acceptance_expired" else "succeeded")
        if boundary == "acceptance_expired":
            assert outcome["staged_outputs"] and not outcome["outputs"]
            with pytest.raises(OperationBlocked):
                flow.current_use("policy-check", "out", purpose="query")
        else:
            accepted = flow.current_use("policy-check", "out", purpose="query")
            clock[0] = deadline + 1
            reopened = type(flow)(flow.ledger, "study")
            assert reopened.current_use("policy-check", "out", purpose="query") == accepted
    assert clock[0] < grant["expires_at"]
