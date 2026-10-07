"""Execution-verifier scope checks without OCI, model calls or data execution."""
import importlib

from devbench.analysis.method_planner_contract import validate_recommendation


def verifier():
    return importlib.import_module("scripts.verify_method_planner")


def test_fixed_procedure_parameters_are_fixture_choices_not_user_requirements():
    parameters = {"metrics": ["total_counts"], "threshold_mad": 4}
    candidate = verifier()._candidate("Fixed synthetic MAD procedure", parameters)
    assert {row["name"]: row["value"] for row in candidate["parameter_candidates"]} == parameters
    assert {row["origin"] for row in candidate["parameter_candidates"]} == {"fixture"}
    assert all("not user" in row["rationale"] for row in candidate["parameter_candidates"])


def test_fixed_procedure_evidence_records_abstention_without_scientific_approval():
    script = verifier()
    assert callable(getattr(script, "_execution_abstention", None)), "Verifier needs a recorded execution-only abstention"
    answer = validate_recommendation(script._execution_abstention("Unavailable alternative", "fixture-trial"))
    assert answer["choice"] is None
    assert answer["confidence"] == answer["scientific_confidence"] == "no_recommendation"
    assert answer["params"] == {} and answer["required_environment"] == {}
    assert answer["tests_run"] == ["fixture-trial"]
    assert answer["alternatives"] == ["Unavailable alternative"]
    assert "method selection" in answer["rationale"]
    assert all(action["status"] == "not_requested" and action["required"] is False
               for action in answer["approval_actions"])


def test_abstaining_fixture_cannot_approve_save_recipe_or_propose_selected_method(tmp_path):
    script = verifier()
    assert callable(getattr(script, "_blocked_selection_actions", None)), "Verifier must exercise abstention guards"
    planner = script.ScientificMethodPlanner(script.MethodService(tmp_path / "workspace"))
    state = planner.create(
        {"scientific_objective": "Verify fixed procedure execution only", "computational_task": "quality_control"},
        {"observed": {}, "inferred": {}, "unknowns": [], "artifact_ids": []},
        actor="fixture-author", data_classification="synthetic", evaluation_purpose="execution_verification",
    )
    before = planner.get(state["session_id"])
    blocked = script._blocked_selection_actions(planner, state["session_id"], actor="fixture-reviewer")
    assert set(blocked) == {"approve_recommendation", "save_experimental_recipe", "propose_selected"}
    assert all(result["status"] == "blocked" and result["reason"] for result in blocked.values())
    assert planner.get(state["session_id"]) == before
    assert before["approvals"] == {} and before["experiments"] == [] and before["results"] == []
