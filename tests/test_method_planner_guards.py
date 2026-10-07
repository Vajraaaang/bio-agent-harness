"""Independent planner guard regressions; synthetic data and offline selectors."""

import json

import pytest

from tests.test_method_planner import resolved, trial_spec, recommendation, Selector
from tests.test_method_planner_scientific_safeguards import comparable_plan, comparison_spec


class PopulationExecutor:
    """Expose which fixture population actually reached a run's output."""
    def execute(self, contract, inputs, parameters, source, lock, output_dir):
        data = json.loads(inputs["dataset"].read_bytes())
        (output_dir / "result.json").write_text(json.dumps({"cells": data["cells"], **parameters}))
        return {"exit_code": 0, "runtime": "synthetic_fixture"}


def _completed_trial(tmp_path, monkeypatch, *, subset=False):
    planner, state, analysis, branch, step = comparable_plan(tmp_path, monkeypatch, declare=False)
    spec = comparison_spec(step)
    if subset:
        subset_path = tmp_path / "pilot-subset.json"
        subset_path.write_text('{"cells":3}')
        artifact = planner.methods.import_artifact(subset_path, artifact_type="json", actor="scientist")
        inspection_path = tmp_path / "subset-inspection.json"
        inspection_path.write_text(json.dumps({
            "input_artifact_ids": [artifact["artifact_id"]], "parent_artifact_ids": list(step["inputs"].values()),
            "dataset_summary": {"observed": {"independent_samples": True}, "inferred": {}, "unknowns": []},
        }))
        inspection = planner.methods.import_artifact(inspection_path, artifact_type="json", actor="scientist")
        spec["subset_spec"] = {"kind": "representative_pilot", "inspection_artifact_id": inspection["artifact_id"]}
        spec["inputs"] = {name: {"dataset": artifact["artifact_id"]} for name in spec["candidates"]}
    planner.declare_trial(state["session_id"], branch["branch_id"], spec, actor="scientist")
    rows = planner.run_trial(state["session_id"], spec["id"], actor="scientist", executor=PopulationExecutor(),
                             allow_data_execution=True)
    assert rows[0]["status"] == "success"
    return planner, state, analysis, branch, step, rows[0]


def _reviewed_trial(tmp_path, monkeypatch, *, subset=False):
    prepared = _completed_trial(tmp_path, monkeypatch, subset=subset)
    planner, state, *_ = prepared
    planner.recommend(state["session_id"], Selector(recommendation()), actor="scientist")
    planner.approve_recommendation(state["session_id"], actor="reviewer", rationale="Reviewed this exact recommendation")
    return prepared


def test_canonical_proposal_uses_approved_full_run_instead_of_pilot(tmp_path, monkeypatch):
    planner, state, analysis, branch, step, pilot = _reviewed_trial(tmp_path, monkeypatch, subset=True)
    planner.prepare_full_execution(state["session_id"], branch["branch_id"], step["inputs"], actor="scientist")
    planner.approve_full_execution(state["session_id"], actor="reviewer", rationale="Run against the original full population")
    full = planner.run_full(state["session_id"], actor="scientist", executor=PopulationExecutor(), allow_data_execution=True)
    assert full["status"] == "succeeded"
    proposal = planner.propose_selected(state["session_id"], actor="scientist", rationale="Review the full result")
    assert proposal["run_id"] == full["run_id"]
    assert proposal["run_id"] != pilot["provenance"]["run_id"]
    output = planner.methods.artifact_path(proposal["outputs"]["dataset"])
    assert json.loads(output.read_bytes())["cells"] == 10
    assert planner.methods.analysis(analysis["analysis_id"])["revision_id"] == analysis["revision_id"]


def test_pilot_cannot_be_proposed_as_full_analysis_without_a_full_run(tmp_path, monkeypatch):
    planner, state, *_ = _reviewed_trial(tmp_path, monkeypatch, subset=True)
    with pytest.raises(ValueError, match="full|subset|pilot"):
        planner.propose_selected(state["session_id"], actor="scientist", rationale="Must not promote the subset")


@pytest.mark.parametrize("field", ["/p_values/0", "/q_values/0", "/p~1value", "/statistics/significant_genes/0"])
def test_metric_pointer_aliases_cannot_select_final_hypothesis_outcomes(tmp_path, field):
    planner, state, _, _, branch, step = resolved(tmp_path)
    spec = trial_spec(step)
    spec["metrics"] = [{"name": "fit_quality", "category": "accuracy_proxy", "direction": "lower_better",
                        "interpretation": "An alias must not bypass the scientific outcome exclusion"}]
    spec["metric_sources"] = {"fit_quality": {"kind": "output_json", "output": "dataset", "field": field}}
    with pytest.raises(ValueError, match="hypothesis|metric|outcome"):
        planner.declare_trial(state["session_id"], branch["branch_id"], spec, actor="scientist")
    assert planner.get(state["session_id"])["experiments"] == []


def test_predeclared_trial_cannot_execute_after_a_recommendation(tmp_path, monkeypatch):
    planner, state, _, branch, step = comparable_plan(tmp_path, monkeypatch, declare=False)
    first = comparison_spec(step)
    planner.declare_trial(state["session_id"], branch["branch_id"], first, actor="scientist")
    later = comparison_spec(step)
    later["id"] = "confirmation-after-selection"
    later["parameters"]["Unknown QC"] = {"threshold": 5}
    planner.declare_trial(state["session_id"], branch["branch_id"], later, actor="scientist")
    planner.run_trial(state["session_id"], first["id"], actor="scientist", executor=PopulationExecutor(),
                      allow_data_execution=True)
    planner.recommend(state["session_id"], Selector(recommendation()), actor="scientist")
    with pytest.raises(ValueError, match="recommend|frozen"):
        planner.run_trial(state["session_id"], later["id"], actor="scientist", executor=PopulationExecutor(),
                          allow_data_execution=True)
    assert planner.get(state["session_id"])["experiments"][-1]["status"] == "declared"


def test_new_trial_declaration_is_frozen_after_a_recommendation(tmp_path, monkeypatch):
    planner, state, _, branch, step, _ = _reviewed_trial(tmp_path, monkeypatch)
    later = trial_spec(step)
    later["id"] = "new-after-selection"
    with pytest.raises(ValueError, match="recommend|frozen"):
        planner.declare_trial(state["session_id"], branch["branch_id"], later, actor="scientist")


def test_recommendation_approval_does_not_authorize_full_execution(tmp_path, monkeypatch):
    planner, state, analysis, branch, step, _ = _reviewed_trial(tmp_path, monkeypatch)
    planner.prepare_full_execution(state["session_id"], branch["branch_id"], step["inputs"], actor="scientist")
    with pytest.raises(ValueError, match="approved"):
        planner.run_full(state["session_id"], actor="scientist", executor=PopulationExecutor(), allow_data_execution=True)
    assert planner.get(state["session_id"])["full_execution"]["status"] == "pending"
    planner.approve_full_execution(state["session_id"], actor="reviewer", rationale="Separate full-execution approval")
    with pytest.raises(PermissionError):
        planner.run_full(state["session_id"], actor="scientist", executor=PopulationExecutor())
    assert planner.methods.analysis(analysis["analysis_id"])["revision_id"] == analysis["revision_id"]


def test_replacing_the_pending_full_step_does_not_inherit_earlier_approval(tmp_path, monkeypatch):
    planner, state, _, branch, step, _ = _reviewed_trial(tmp_path, monkeypatch)
    first = planner.prepare_full_execution(state["session_id"], branch["branch_id"], step["inputs"], actor="scientist")
    planner.approve_full_execution(state["session_id"], actor="reviewer", rationale="Approve only the first concrete step")
    second = planner.prepare_full_execution(state["session_id"], branch["branch_id"], step["inputs"], actor="scientist")
    assert first["step_id"] != second["step_id"] and second["status"] == "pending"
    scope = next(item for item in planner.export(state["session_id"])["recommendation"]["approval_actions"]
                 if item["action"] == "approve_full_execution")
    assert scope["status"] in {"pending", "not_requested"}


@pytest.mark.parametrize("status", ["approved", "rejected"])
def test_model_cannot_supply_human_approval_decisions(tmp_path, monkeypatch, status):
    planner, state, *_ = _completed_trial(tmp_path, monkeypatch)
    answer = recommendation()
    answer["approval_actions"][0]["status"] = status
    with pytest.raises(ValueError, match="approval|rejection"):
        planner.recommend(state["session_id"], Selector(answer), actor="scientist")
    assert planner.get(state["session_id"])["approvals"] == {}
    assert planner.get(state["session_id"])["recommendation"]["choice"] is None


def test_full_run_exception_leaves_a_terminal_recoverable_state(tmp_path, monkeypatch):
    planner, state, _, branch, step, _ = _reviewed_trial(tmp_path, monkeypatch)
    planner.prepare_full_execution(state["session_id"], branch["branch_id"], step["inputs"], actor="scientist")
    planner.approve_full_execution(state["session_id"], actor="reviewer", rationale="Explicit full-execution approval")

    def fail_before_run_record(*args, **kwargs):
        raise ValueError("Retained input failed its execution-boundary digest check")

    monkeypatch.setattr(planner.methods, "run_step", fail_before_run_record)
    with pytest.raises(ValueError, match="digest"):
        planner.run_full(state["session_id"], actor="scientist", executor=PopulationExecutor(), allow_data_execution=True)
    full = planner.get(state["session_id"])["full_execution"]
    assert full["status"] in {"failed", "interrupted"}
    assert "digest" in full.get("error", "")
