"""Offline open-world planning and real coordinator-state tests, no model calls."""
from copy import deepcopy
import importlib

import pytest

from devbench.analysis.episode_agent import Selection
from tests.test_extensible_methods import prepared, FixtureExecutor


def candidate(name="Unknown QC"):
    return {"name": name, "method_family": "quality_control",
        "source": {"type": "official_package", "reference": "https://example.invalid/synthetic-method"},
        "assumptions": [{"statement": "Independent samples are available", "status": "satisfied",
                         "evidence": "Synthetic fixture", "hard_precondition": True,
                         "field": "independent_samples", "operator": "equals", "expected": True}],
        "expected_inputs": ["dataset"], "dataset_fit": "plausible", "objective_fit": "strong",
        "known_failure_modes": ["Selection bias"], "parameter_candidates": [
            {"name": "threshold", "value": 4, "origin": "fixture", "rationale": "Fixture-authored synthetic test value"}]}


class Selector:
    def __init__(self, *answers):
        self.answers = list(answers)
        self.contexts = []

    def configuration(self):
        return {"backend": "recorded_fixture"}

    def select(self, context, output, **kwargs):
        self.contexts.append(deepcopy(context))
        return Selection(self.answers.pop(0), {"backend": "recorded_fixture", "model_execution": False})


def setup(tmp_path, observed=None):
    service, analysis, branch, step, *_ = prepared(tmp_path)
    api = importlib.import_module("devbench.analysis.method_planner")
    planner = api.ScientificMethodPlanner(service)
    state = planner.create({"scientific_objective": "Compare sensible QC choices", "computational_task": "quality_control"},
        {"observed": {"independent_samples": True} if observed is None else observed,
         "inferred": {}, "unknowns": [], "artifact_ids": list(step["inputs"].values())},
        actor="scientist", data_classification="synthetic")
    selector = Selector({"candidates": [candidate("Unavailable preferred method"), candidate()]},
        {"bindings": [{"candidate": "Unknown QC", "method_version_id": step["method_version_id"], "discovery": None},
                      {"candidate": "Unavailable preferred method", "method_version_id": None, "discovery": None}]})
    return planner, state, selector, analysis, branch, step


def resolved(tmp_path, observed=None):
    planner, state, selector, analysis, branch, step = setup(tmp_path, observed)
    planner.generate_candidates(state["session_id"], selector, actor="scientist")
    planner.resolve_capabilities(state["session_id"], selector, [step["method_version_id"]], actor="scientist")
    return planner, state, selector, analysis, branch, step


def trial_spec(step):
    return {"id": "qc-pilot", "candidates": ["Unknown QC"], "purpose": "Synthetic software check",
        "subset_spec": {"kind": "full_dataset", "representation": "unchanged input snapshot"},
        "subset_validity": {"preserves_preconditions": True, "rationale": "All supplied synthetic samples retained"},
        "commands": [], "metrics": [{"name": "runtime_seconds", "category": "runtime", "direction": "lower_better",
                                        "interpretation": "Measured execution duration, not biological accuracy"}],
        "metric_sources": {"runtime_seconds": {"kind": "runtime", "field": "elapsed_seconds"}},
        "resource_limits": {"memory_mb": 256, "timeout_seconds": 30, "max_runs": 1},
        "inputs": {"Unknown QC": step["inputs"]}, "parameters": {"Unknown QC": {"threshold": 4}}}


def recommendation(choice="Unknown QC", implementation="low"):
    return {"choice": choice, "confidence": "low" if choice else "no_recommendation",
        "scientific_confidence": "low" if choice else "no_recommendation",
        "implementation_confidence": implementation if choice else "unknown",
        "confidence_basis": "One synthetic trial; unavailable alternatives remain untested",
        "rationale": "Review this provisional option", "tradeoffs": ["Other candidates remain unavailable"],
        "alternatives": ["Unavailable preferred method"], "params": {"threshold": 4} if choice else {},
        "tests_run": ["qc-pilot"] if choice else [], "approval_actions": [
            {"action": "approve_recommendation", "required": True, "status": "pending"},
            {"action": "approve_analysis_plan_change", "required": True, "status": "pending"}]}


def test_scientific_pass_never_receives_registry_and_unavailable_choice_survives(tmp_path):
    planner, state, selector, _, _, step = resolved(tmp_path)
    first, second = selector.contexts
    assert first["decision_kind"] == "method_scientific_planning"
    assert "registry" not in first and "availability" not in str(first)
    assert step["method_version_id"] not in str(first)
    assert second["decision_kind"] == "method_capability_resolution" and second["registry"]
    plan = planner.export(state["session_id"])
    assert [c["name"] for c in plan["candidates"]] == ["Unavailable preferred method", "Unknown QC"]
    assert plan["candidates"][0]["method_version_id"] is None
    assert plan["candidates"][1]["availability"] == "workspace_custom"


@pytest.mark.parametrize("observed, expected", [({"independent_samples": False}, "inapplicable"), ({}, "blocked_unknown")])
def test_harness_recomputes_hard_preconditions_instead_of_trusting_model(tmp_path, observed, expected):
    planner, state, _, _, branch, step = resolved(tmp_path, observed)
    assert planner.export(state["session_id"])["candidates"][1]["applicability"] == expected
    with pytest.raises(ValueError, match="precondition|eligible|unknown"):
        planner.declare_trial(state["session_id"], branch["branch_id"], trial_spec(step), actor="scientist")


@pytest.mark.parametrize("mutation", ["invalid_subset", "undeclared_subset", "p_value", "budget"])
def test_invalid_trials_are_blocked_before_execution(tmp_path, mutation):
    planner, state, _, _, branch, step = resolved(tmp_path)
    spec = trial_spec(step)
    if mutation == "invalid_subset":
        spec["subset_validity"]["preserves_preconditions"] = False
    elif mutation == "undeclared_subset":
        spec.pop("subset_validity")
    elif mutation == "p_value":
        spec["metrics"][0]["name"] = "p_value"
    else:
        spec["resource_limits"]["memory_mb"] = 1
    with pytest.raises(ValueError):
        planner.declare_trial(state["session_id"], branch["branch_id"], spec, actor="scientist")


def test_trial_is_frozen_and_cannot_approve_itself_or_canonical_state(tmp_path):
    planner, state, _, analysis, branch, step = resolved(tmp_path)
    spec = trial_spec(step)
    planner.declare_trial(state["session_id"], branch["branch_id"], spec, actor="scientist")
    with pytest.raises(PermissionError):
        planner.run_trial(state["session_id"], "qc-pilot", actor="scientist", executor=FixtureExecutor())
    results = planner.run_trial(state["session_id"], "qc-pilot", actor="scientist", executor=FixtureExecutor(), allow_data_execution=True)
    assert results[0]["status"] == "success"
    assert results[0]["metric_values"]["runtime_seconds"] >= 0
    assert results[0]["provenance"]["run_id"]
    assert planner.methods.analysis(analysis["analysis_id"])["revision_id"] == analysis["revision_id"]
    spec["metrics"][0]["direction"] = "higher_better"
    with pytest.raises(ValueError, match="immutable|already|frozen"):
        planner.declare_trial(state["session_id"], branch["branch_id"], spec, actor="scientist")
    with pytest.raises(ValueError, match="already|once|completed"):
        planner.run_trial(state["session_id"], "qc-pilot", actor="scientist", executor=FixtureExecutor(), allow_data_execution=True)


def test_failed_trials_keep_errors_and_cannot_support_a_selected_winner(tmp_path):
    planner, state, _, _, branch, step = resolved(tmp_path)
    planner.declare_trial(state["session_id"], branch["branch_id"], trial_spec(step), actor="scientist")
    class Broken(FixtureExecutor):
        def execute(self, *args):
            raise RuntimeError("observable synthetic failure")
    rows = planner.run_trial(state["session_id"], "qc-pilot", actor="scientist", executor=Broken(), allow_data_execution=True)
    assert rows[0]["status"] == "failed" and rows[0]["metric_values"] == {}
    assert "observable synthetic failure" in str(rows[0]["failures"])
    with pytest.raises(ValueError, match="successful|success"):
        planner.recommend(state["session_id"], Selector(recommendation()), actor="scientist")
    result = planner.recommend(state["session_id"], Selector(recommendation(None)), actor="scientist")
    assert result["choice"] is None


def test_recommendation_projects_artifact_labels_without_mutating_retained_evidence(tmp_path):
    from devbench.analysis.episodes import _public_only

    planner, state, _, _, branch, step = resolved(tmp_path)
    planner.declare_trial(state["session_id"], branch["branch_id"], trial_spec(step), actor="scientist")
    planner.run_trial(state["session_id"], "qc-pilot", actor="scientist", executor=FixtureExecutor(), allow_data_execution=True)
    original = planner.export(state["session_id"])["results"]

    class PublicSelector(Selector):
        def select(self, context, output, **kwargs):
            _public_only(context)
            artifacts = context["plan"]["results"][0]["artifacts"]
            assert artifacts == [{**{key: value for key, value in item.items() if key != "role"},
                                  "output_name": item["role"]} for item in original[0]["artifacts"]]
            return super().select(context, output, **kwargs)

    planner.recommend(state["session_id"], PublicSelector(recommendation(None)), actor="scientist")
    assert planner.export(state["session_id"])["results"] == original
    assert len(planner.methods.list_records("run")) == 1
    with pytest.raises(ValueError, match="role"):
        _public_only({"unexpected": {"role": "private-coordinator-label"}})


def test_custom_confidence_and_experimental_recipe_do_not_upgrade_trust(tmp_path, monkeypatch):
    from tests.test_method_planner_scientific_safeguards import comparable_plan
    planner, state, analysis, branch, step = comparable_plan(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="confidence"):
        planner.recommend(state["session_id"], Selector(recommendation(implementation="high")), actor="scientist")
    planner.recommend(state["session_id"], Selector(recommendation()), actor="scientist")
    planner.approve_recommendation(state["session_id"], actor="researcher", rationale="Reviewed the limited evidence")
    recipe = planner.save_recipe(state["session_id"], "Experimental QC", actor="researcher", rationale="Reuse experimentally")
    assert recipe["status"] == "experimental" and recipe["method_version_id"] == step["method_version_id"]
    assert planner.methods.method(step["method_version_id"])["contract"]["implementation_trust"] == "unreviewed"
    assert planner.methods.analysis(analysis["analysis_id"])["revision_id"] == analysis["revision_id"]
    proposal = planner.propose_selected(state["session_id"], actor="researcher", rationale="Review candidate for this analysis")
    assert proposal["status"] == "pending"


def test_private_clinical_summary_cannot_reach_a_live_selector(tmp_path):
    planner, state, _, _, _, _ = setup(tmp_path)
    private = planner.create({"scientific_objective": "Private case", "computational_task": "quality_control"},
                            {"observed": {}, "inferred": {}, "unknowns": []}, actor="researcher", data_classification="private_clinical")
    class Live(Selector):
        def configuration(self):
            return {"backend": "codex-cli"}
    live = Live({"candidates": []})
    with pytest.raises(PermissionError):
        planner.generate_candidates(private["session_id"], live, actor="researcher", allow_model_execution=True)
    assert live.contexts == []


def test_model_cannot_remove_required_approval_scopes(tmp_path, monkeypatch):
    from tests.test_method_planner_scientific_safeguards import comparable_plan
    planner, state, *_ = comparable_plan(tmp_path, monkeypatch)
    decision = recommendation()
    for action in decision["approval_actions"]:
        action["required"] = False
    planner.recommend(state["session_id"], Selector(decision), actor="scientist")
    assert all(action["required"] for action in planner.export(state["session_id"])["recommendation"]["approval_actions"])


def test_metric_values_are_recomputed_from_execution_evidence_before_recommendation(tmp_path):
    planner, state, _, _, branch, step = resolved(tmp_path)
    planner.declare_trial(state["session_id"], branch["branch_id"], trial_spec(step), actor="scientist")
    planner.run_trial(state["session_id"], "qc-pilot", actor="scientist", executor=FixtureExecutor(), allow_data_execution=True)
    forged = planner.get(state["session_id"])
    forged["results"][0]["metric_values"]["runtime_seconds"] = 999999
    with planner.methods._db() as db:
        planner.methods._put(db, "method_plan", state["session_id"], forged, update=True)
    selector = Selector(recommendation())
    with pytest.raises(ValueError, match="metrics"):
        planner.recommend(state["session_id"], selector, actor="scientist")
    assert selector.contexts == []


def test_interrupted_selection_cannot_reset_its_time_budget(tmp_path):
    planner, state, _, _, _, _ = setup(tmp_path)
    interrupted = planner.get(state["session_id"])
    interrupted["limits"].update(timeout_seconds=1, session_timeout_seconds=1)
    interrupted["selection_in_progress"] = "lost-attempt"
    interrupted["selection_history"] = [{"attempt": "lost-attempt", "phase": "method_scientific_planning", "status": "running"}]
    with planner.methods._db() as db:
        planner.methods._put(db, "method_plan", state["session_id"], interrupted, update=True)
    selector = Selector({"candidates": []})
    with pytest.raises(ValueError, match="budget"):
        planner.generate_candidates(state["session_id"], selector, actor="scientist")
    assert selector.contexts == []
