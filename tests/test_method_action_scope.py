"""Offline action policies; no model or OCI calls."""
from copy import deepcopy
import pytest
from devbench.analysis.method_planner import _applicability
from devbench.analysis.method_planner_contract import validate_scientific_candidates
from tests.test_method_planner import candidate


def scope(intent="diagnostic_flagging", interpretation="numerical_atypicality"):
    return {"format": "devbench.method-action/1", "intent": intent,
            "interpretation": interpretation, "exclusions": intent in {"exclude", "infer"}}


def item(intent="diagnostic_flagging", interpretation="numerical_atypicality"):
    row = candidate()
    row["assumptions"] = []
    row["action_scope"] = scope(intent, interpretation)
    return row


def summary(**observed):
    return {"observed": {"input_integrity_valid": True, "observation_ids_unique": True,
                         "metrics_finite": True, **observed}, "inferred": {}, "unknowns": []}


def test_numeric_flags_do_not_require_assay_or_technical_causation():
    data = summary()
    data["unknowns"] = [{"field": "technical_damage_validated", "impact": "Cause unknown", "blocking": True}]
    assert _applicability(item(), data)["applicability"] == "eligible"
    assert _applicability(item("exclude", "qc_review"), data)["applicability"] == "blocked_unknown"
    assert _applicability(item(interpretation="qc_review"), data)["applicability"] == "blocked_unknown"


@pytest.mark.parametrize("field", ["input_integrity_valid", "observation_ids_unique", "metrics_finite"])
def test_scope_cannot_remove_mandatory_input_failures(field):
    row = item()
    row["assumptions"] = [{"statement": "Model says unnecessary", "status": "not_applicable", "evidence": None,
                           "hard_precondition": False, "field": field, "operator": "equals", "expected": False}]
    assert _applicability(row, summary(**{field: False}))["applicability"] == "inapplicable"


@pytest.mark.parametrize("change", [{"format": "devbench.method-action/0"}, {"intent": "remove"},
                                    {"exclusions": True}, {"interpretation": "proven_damage"}])
def test_malformed_diagnostic_scope_rejected(change):
    row = item()
    row["action_scope"].update(change)
    with pytest.raises(ValueError):
        validate_scientific_candidates({"candidates": [row]})


def test_legacy_behavior_and_identity_not_backfilled():
    row = candidate()
    data = summary(independent_samples=True)
    data["unknowns"] = [{"field": "technical_damage_validated", "impact": "Cause unknown"}]
    assert _applicability(row, data)["applicability"] == "blocked_unknown"
    assert "action_scope" not in validate_scientific_candidates({"candidates": [row]})[0]
    assert "action_scope" not in row


def test_inference_requires_independent_units_and_identifiable_design():
    row = item("infer", "none")
    assert _applicability(row, summary(assay_semantics_known=True, biological_units_independent=False,
                                      design_identifiable=False))["applicability"] == "inapplicable"


def test_hard_model_assumptions_cannot_be_downgraded_by_scope():
    row = item()
    row["assumptions"] = deepcopy(candidate()["assumptions"])
    assert _applicability(row, summary())["applicability"] == "blocked_unknown"


def test_scoped_candidate_cannot_bind_different_method_action(tmp_path):
    from tests.test_method_planner import setup, Selector
    planner, state, _, _, _, step = setup(tmp_path)
    selector = Selector({"candidates": [item()]}, {"bindings": [{"candidate": "Unknown QC",
        "method_version_id": step["method_version_id"], "discovery": None}]})
    planner.generate_candidates(state["session_id"], selector, actor="scientist")
    with pytest.raises(ValueError, match="scope|action"):
        planner.resolve_capabilities(state["session_id"], selector, [step["method_version_id"]], actor="scientist")


def test_coordinator_inspection_resolves_computable_unknowns_without_backfill(tmp_path):
    from tests.test_method_diagnostic_outputs import artifacts
    from tests.test_method_planner import setup
    planner, state, *_ = setup(tmp_path)
    before, *_ = artifacts(tmp_path)
    artifact = planner.methods.import_artifact(before, artifact_type="json", actor="scientist")
    data = summary()
    data["observed"] = {}
    data["unknowns"] = [{"field": "measurement_rank", "impact": "Not yet inspected"}]
    row = item()
    row["assumptions"] = [{"statement": "Nonzero rank", "status": "unknown", "evidence": None,
        "hard_precondition": True, "field": "measurement_rank", "operator": "at_least", "expected": 1}]
    result = planner._inspect_applicability(row, data, [artifact["artifact_id"]], actor="scientist")
    assert result["applicability"] == "eligible"
    assert data["observed"] == {} and data["unknowns"]
    record = planner.methods.get("method_prerequisite_inspection", result["prerequisite_inspections"][0])
    assert record["observed"]["measurement_rank"] == 1
    assert record["origin"] == "devharness.coordinator-prerequisites/1"


def test_trial_execution_recomputes_observed_prerequisites(tmp_path):
    from tests.test_method_planner import resolved, trial_spec
    planner, state, _, _, branch, step = resolved(tmp_path)
    spec = planner.declare_trial(state["session_id"], branch["branch_id"], trial_spec(step), actor="scientist")
    old = planner.get(state["session_id"])
    updated = deepcopy(old)
    updated["context"]["dataset_summary"]["observed"]["independent_samples"] = False
    planner._save(old, updated, actor="scientist", action="test_metadata_correction")
    def forbidden(*args, **kwargs):
        pytest.fail("Execution started despite a failed hard prerequisite")
    planner.methods.run_step = forbidden
    rows = planner.run_trial(state["session_id"], spec["id"], actor="scientist", allow_data_execution=True)
    assert rows[0]["status"] == "failed"
    assert "precondition" in str(rows[0]["failures"])


@pytest.mark.parametrize("action", ["save_recipe", "prepare_full_execution", "propose_selected", "approve_full_execution", "run_full"])
def test_downstream_approval_actions_recompute_prerequisites(tmp_path, monkeypatch, action):
    from tests.test_method_planner_guards import _reviewed_trial
    planner, state, _, branch, step, _ = _reviewed_trial(tmp_path, monkeypatch)
    old = planner.get(state["session_id"])
    updated = deepcopy(old)
    updated["context"]["dataset_summary"]["observed"]["independent_samples"] = False
    planner._save(old, updated, actor="scientist", action="test_metadata_correction")
    kwargs = {"actor": "scientist"}
    args = [state["session_id"]]
    if action == "save_recipe":
        args.append("Cannot inherit old approval")
    if action == "prepare_full_execution":
        args.extend([branch["branch_id"], step["inputs"]])
    if action in {"save_recipe", "propose_selected", "approve_full_execution"}:
        kwargs["rationale"] = "Metadata changed after review"
    if action == "run_full":
        kwargs["allow_data_execution"] = True
    with pytest.raises(ValueError, match="evidence|eligible|precondition"):
        getattr(planner, action)(*args, **kwargs)


def test_inference_with_exclusion_inherits_exclusion_prerequisites():
    data = summary(assay_semantics_known=True, biological_units_independent=True, design_identifiable=True)
    assert _applicability(item("infer", "none"), data)["applicability"] == "blocked_unknown"


def test_failed_numeric_inspection_retains_failure_and_cannot_use_declared_success(tmp_path):
    from tests.test_method_planner import setup
    planner, state, _, _, _, step = setup(tmp_path)
    result = planner._inspect_applicability(item(), summary(), step["inputs"].values(), actor="scientist")
    assert result["applicability"] == "blocked_unknown"
    record = planner.methods.get("method_prerequisite_inspection", result["prerequisite_inspections"][0])
    assert record["failures"] and not record["observed"]
