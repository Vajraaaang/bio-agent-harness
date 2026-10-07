"""Synthetic attribution and comparative-evidence checks, with no model calls."""
from copy import deepcopy
import json

import pytest

from devbench.analysis.method_planner import ScientificMethodPlanner
from devbench.analysis.method_planner_contract import SCIENTIFIC_PLANNER_INSTRUCTIONS
from tests.test_extensible_methods import prepared, FixtureExecutor
from tests.test_method_planner import Selector, candidate, trial_spec, recommendation


def new_plan(tmp_path, *, task=None, **options):
    methods, analysis, branch, step, *_ = prepared(tmp_path)
    planner = ScientificMethodPlanner(methods)
    state = planner.create(task or {"scientific_objective": "Compare feasible methods for this supplied dataset",
                                   "computational_task": "quality_control"},
                           {"observed": {"independent_samples": True}, "inferred": {}, "unknowns": [],
                            "artifact_ids": list(step["inputs"].values())}, actor="researcher",
                           data_classification="synthetic", **options)
    return planner, state, analysis, branch, step


def declared_candidate(name="Unknown QC", origin="fixture"):
    item = candidate(name)
    item["parameter_candidates"][0].update(origin=origin, rationale="Fixture-authored synthetic test value")
    return item


def coordinator_comparison(monkeypatch):
    """A test-only numerical comparator; not biological performance evidence.

    The coordinator compares an output scalar against the synthetic input scalar.
    The method's output cannot declare the comparator's error or pass itself.
    """
    from devbench.analysis import methods
    original = methods.validate_outputs

    def validate(contract, inputs, outputs):
        result = original(contract, inputs, outputs)
        before = json.loads(inputs["dataset"].read_bytes())
        after = json.loads(outputs["dataset"].read_bytes())
        result["scientific_metrics"] = {"reference_error": abs(float(after["threshold"]) - float(before["cells"]))}
        result["comparison_measurement_provenance"] = {
            "/scientific_metrics/reference_error": {
                "origin": "devharness.method_validation/1",
                "basis": "immutable_input_reference_comparison",
                "prerequisites": {"immutable_input_reference_loaded": True, "finite_comparison_values": True},
                "reference": "Test-only immutable input scalar; not biological ground truth",
                "raw_counts_recomputed": False,
            },
        }
        result["samples"] = {"all": {"retained": before["cells"]}}
        result["limitations"] = ["Synthetic scalar comparator only; no biological method-selection claim"]
        return result

    monkeypatch.setattr(methods, "validate_outputs", validate)


def comparable_plan(tmp_path, monkeypatch, *, metric_source=None, metric_category="calibration", include_unavailable=False,
                    evaluation_purpose="method_selection", execute=True, declare=True):
    planner, state, analysis, branch, step = new_plan(tmp_path, evaluation_purpose=evaluation_purpose)
    contract = deepcopy(planner.methods.method(step["method_version_id"])["contract"])
    contract.update(method_id="fixture.alternative", name="Alternative QC")
    alternate = planner.methods.register_method(contract, source=tmp_path / "source.py",
                                                environment_lock=tmp_path / "environment.lock", actor="researcher")
    candidates = [declared_candidate(), declared_candidate("Alternative QC")]
    bindings = [{"candidate": "Unknown QC", "method_version_id": step["method_version_id"], "discovery": None},
                {"candidate": "Alternative QC", "method_version_id": alternate["version_id"], "discovery": None}]
    if include_unavailable:
        candidates.append(declared_candidate("Unavailable preferred method"))
        bindings.append({"candidate": "Unavailable preferred method", "method_version_id": None, "discovery": None})
    selector = Selector({"candidates": candidates}, {"bindings": bindings})
    planner.generate_candidates(state["session_id"], selector, actor="researcher")
    planner.resolve_capabilities(state["session_id"], selector, [step["method_version_id"], alternate["version_id"]], actor="researcher")
    coordinator_comparison(monkeypatch)
    spec = comparison_spec(step)
    spec["metrics"] = [{"name": "reference_error", "category": metric_category, "direction": "lower_better",
                        "interpretation": "Coordinator-computed synthetic numerical discrepancy; not biological validation"}]
    spec["metric_sources"] = {"reference_error": metric_source or {"kind": "validation", "field": "/scientific_metrics/reference_error"}}
    if declare:
        planner.declare_trial(state["session_id"], branch["branch_id"], spec, actor="researcher")
    if execute and declare:
        planner.run_trial(state["session_id"], spec["id"], actor="researcher", allow_data_execution=True, executor=FixtureExecutor())
    return planner, state, analysis, branch, step


def comparison_spec(step):
    spec = trial_spec(step)
    spec.update(candidates=["Unknown QC", "Alternative QC"], purpose="Test-only scalar comparison")
    spec["resource_limits"]["max_runs"] = 2
    spec["inputs"]["Alternative QC"] = step["inputs"]
    spec["parameters"]["Alternative QC"] = {"threshold": 3}
    spec["metrics"] = [{"name": "reference_error", "category": "calibration", "direction": "lower_better",
                        "interpretation": "Coordinator-computed synthetic numerical discrepancy; not biological validation"}]
    spec["metric_sources"] = {"reference_error": {"kind": "validation", "field": "/scientific_metrics/reference_error"}}
    return spec


def test_new_plan_metadata_is_explicit_and_outside_six_field_export(tmp_path):
    planner, state, *_ = new_plan(tmp_path)
    assert state["evaluation_purpose"] == "method_selection"
    assert state["user_parameter_requirements"] == []
    assert set(planner.export(state["session_id"])) == {"task", "dataset_summary", "candidates", "experiments", "results", "recommendation"}
    assert "recommendation_evidence_assessment" not in state


@pytest.mark.parametrize("options", [
    {"evaluation_purpose": "automatic_best_method"},
    {"user_parameter_requirements": [{"name": "threshold", "value": 4, "evidence": " "}]},
    {"user_parameter_requirements": [{"name": "threshold", "value": 4}]},
    {"user_parameter_requirements": [{"name": "threshold", "value": 4, "evidence": "recorded quote", "inferred": True}]},
    {"user_parameter_requirements": [{"name": "threshold", "value": 4, "evidence": "quote"}] * 2},
    {"user_parameter_requirements": [{"name": str(index), "value": index, "evidence": "quote"} for index in range(65)]},
])
def test_invalid_evaluation_metadata_is_rejected(tmp_path, options):
    with pytest.raises(ValueError):
        new_plan(tmp_path, **options)


def test_user_origin_requires_exact_retained_requirement_and_keeps_raw_bad_response(tmp_path):
    planner, state, *_ = new_plan(tmp_path)
    answer = {"candidates": [declared_candidate(origin="user")]}
    selector = Selector(answer)
    with pytest.raises(ValueError, match="user|requirement|origin"):
        planner.generate_candidates(state["session_id"], selector, actor="researcher")
    after = planner.get(state["session_id"])
    assert after["scientific_candidates"] is None
    assert after["selection_history"][-1]["response"] == answer
    assert after["selection_history"][-1]["status"] == "returned_unvalidated"


def test_explicit_requirement_allows_matching_user_origin_with_asserted_assurance(tmp_path):
    requirement = {"name": "threshold", "value": 4, "evidence": 'User message: "Use threshold 4 for this software check."'}
    planner, state, *_ = new_plan(tmp_path, user_parameter_requirements=[requirement])
    selector = Selector({"candidates": [declared_candidate(origin="user")]})
    planner.generate_candidates(state["session_id"], selector, actor="researcher")
    assert selector.contexts[0]["user_parameter_requirements"] == [requirement]
    assert selector.contexts[0]["parameter_requirement_assurance"] == "caller-declared; not authenticated human authorship"


@pytest.mark.parametrize("value", [3, 4.0, True, "4"])
def test_user_origin_value_matching_is_exact(tmp_path, value):
    planner, state, *_ = new_plan(tmp_path, user_parameter_requirements=[{"name": "threshold", "value": value, "evidence": "Explicit request"}])
    with pytest.raises(ValueError, match="requirement|origin"):
        planner.generate_candidates(state["session_id"], Selector({"candidates": [declared_candidate(origin="user")]}), actor="researcher")


def test_method_unspecified_context_does_not_invent_mad_or_four_as_requirements(tmp_path):
    planner, state, *_ = new_plan(tmp_path)
    selector = Selector({"candidates": []})
    planner.generate_candidates(state["session_id"], selector, actor="researcher")
    context = selector.contexts[0]
    assert context["user_parameter_requirements"] == []
    assert "MAD" not in SCIENTIFIC_PLANNER_INSTRUCTIONS
    assert "4-MAD" not in SCIENTIFIC_PLANNER_INSTRUCTIONS
    assert "threshold" not in context["task"]["scientific_objective"]
    assert planner.get(state["session_id"])["scientific_candidates"] == []


def test_legacy_plan_export_does_not_backfill_attribution_or_mutate_history(tmp_path):
    planner, state, *_ = new_plan(tmp_path)
    legacy = deepcopy(state)
    legacy.pop("evaluation_purpose", None)
    legacy.pop("user_parameter_requirements", None)
    with planner.methods._db() as db:
        planner.methods._put(db, "method_plan", state["session_id"], legacy, update=True)
    before = planner.methods.database.read_bytes()
    exported = planner.export(state["session_id"])
    assert planner.get(state["session_id"]) == legacy
    assert "evaluation_purpose" not in exported
    assert planner.methods.database.read_bytes() == before


@pytest.mark.parametrize("source,category", [
    ({"kind": "runtime", "field": "elapsed_seconds"}, "runtime"),
    ({"kind": "runtime", "field": "output_bytes"}, "memory"),
    ({"kind": "output_json", "output": "dataset", "field": "/threshold"}, "calibration"),
    ({"kind": "validation", "field": "/passed"}, "accuracy_proxy"),
    ({"kind": "validation", "field": "/samples/all/retained"}, "signal_preservation"),
    ({"kind": "validation", "field": "/parameters/threshold"}, "calibration"),
    ({"kind": "validation", "field": "/outputs/dataset/size_bytes"}, "signal_preservation"),
])
def test_non_scientific_metrics_cannot_support_selected_winner(tmp_path, monkeypatch, source, category):
    planner, state, *_ = comparable_plan(tmp_path, monkeypatch, metric_source=source, metric_category=category)
    answer = recommendation()
    with pytest.raises(ValueError, match="scientific|comparative|evidence"):
        planner.recommend(state["session_id"], Selector(answer), actor="researcher")
    current = planner.get(state["session_id"])
    assert current["recommendation"]["choice"] is None
    assert current["selection_history"][-1]["response"] == answer
    assert current["selection_history"][-1]["status"] == "returned_unvalidated"


def test_execution_verification_rejects_scientific_choice_but_accepts_abstention(tmp_path, monkeypatch):
    planner, state, *_ = comparable_plan(tmp_path, monkeypatch, evaluation_purpose="execution_verification")
    with pytest.raises(ValueError, match="[Ee]xecution.verification"):
        planner.recommend(state["session_id"], Selector(recommendation()), actor="researcher")
    selector = Selector(recommendation(None))
    assert planner.recommend(state["session_id"], selector, actor="researcher")["choice"] is None
    assessment = planner.get(state["session_id"])["recommendation_evidence_assessment"]
    assert assessment["evaluation_purpose"] == "execution_verification"
    assert not any(item["meets_evidence_floor"] for item in assessment["candidates"])


def test_comparable_coordinator_metrics_meet_only_a_limited_evidence_floor(tmp_path, monkeypatch):
    planner, state, *_ = comparable_plan(tmp_path, monkeypatch, include_unavailable=True)
    before = planner.methods.database.read_bytes()
    assessment = planner.evidence_assessment(state["session_id"])
    assert planner.methods.database.read_bytes() == before
    row = next(item for item in assessment["candidates"] if item["candidate"] == "Unknown QC")
    assert row["meets_evidence_floor"] is True
    assert row["comparable_experiments"][0]["metrics"][0]["source"]["kind"] == "validation"
    assert assessment["unavailable_candidates"] == ["Unavailable preferred method"]
    assert assessment["scope"] == "comparative_pilot_only"
    assert assessment["independent_scientific_validation"] is False
    selector = Selector(recommendation())
    assert planner.recommend(state["session_id"], selector, actor="researcher")["choice"] == "Unknown QC"
    assert selector.contexts[0]["recommendation_evidence_assessment"] == assessment
    assert planner.get(state["session_id"])["recommendation_evidence_assessment"] == assessment


def test_missing_feasible_alternative_blocks_winner_and_abstention_needs_no_execution(tmp_path, monkeypatch):
    planner, state, *_ = comparable_plan(tmp_path, monkeypatch, execute=False)
    selector = Selector(recommendation(None))
    assert planner.recommend(state["session_id"], selector, actor="researcher")["choice"] is None
    assert planner.methods.list_records("run") == []
    assert not any(item["meets_evidence_floor"] for item in planner.evidence_assessment(state["session_id"])["candidates"])


def test_task_prose_and_prior_mentions_do_not_establish_user_requirements(tmp_path):
    planner, state, *_ = new_plan(tmp_path,
        task={"scientific_objective": "An earlier assistant mentioned MAD and threshold 4; compare methods", "computational_task": "quality_control"},
        prior_state={"earlier_fixture_parameter": {"name": "threshold", "value": 4}})
    with pytest.raises(ValueError, match="requirement|origin"):
        planner.generate_candidates(state["session_id"], Selector({"candidates": [declared_candidate(origin="user")]}), actor="researcher")


def test_failed_feasible_alternative_is_a_comparison_gap(tmp_path, monkeypatch):
    planner, state, *_ = comparable_plan(tmp_path, monkeypatch, execute=False)
    class FailsAlternative(FixtureExecutor):
        def execute(self, contract, inputs, parameters, source, lock, output_dir):
            if parameters["threshold"] == 3:
                return {"exit_code": 1, "error": "bounded synthetic failure"}
            return super().execute(contract, inputs, parameters, source, lock, output_dir)
    planner.run_trial(state["session_id"], "qc-pilot", actor="researcher", allow_data_execution=True, executor=FailsAlternative())
    with pytest.raises(ValueError, match="comparative|comparable|scientific"):
        planner.recommend(state["session_id"], Selector(recommendation()), actor="researcher")
    assessment = planner.evidence_assessment(state["session_id"])
    assert not any(item["meets_evidence_floor"] for item in assessment["candidates"])
    assert any("Alternative QC" in item for item in assessment["limitations"])


def test_separate_candidate_runs_do_not_replace_a_predeclared_joint_comparison(tmp_path, monkeypatch):
    planner, state, _, branch, step = comparable_plan(tmp_path, monkeypatch, declare=False)
    for index, name in enumerate(("Unknown QC", "Alternative QC")):
        spec = comparison_spec(step)
        spec.update(id="separate-" + str(index), candidates=[name], inputs={name: spec["inputs"][name]}, parameters={name: spec["parameters"][name]})
        planner.declare_trial(state["session_id"], branch["branch_id"], spec, actor="researcher")
    for identity in ("separate-0", "separate-1"):
        planner.run_trial(state["session_id"], identity, actor="researcher", allow_data_execution=True, executor=FixtureExecutor())
    answer = recommendation()
    answer["tests_run"] = ["separate-0", "separate-1"]
    with pytest.raises(ValueError, match="comparative|comparable|scientific"):
        planner.recommend(state["session_id"], Selector(answer), actor="researcher")


def test_legacy_unspecified_purpose_cannot_gain_new_scientific_qualification(tmp_path, monkeypatch):
    planner, state, *_ = comparable_plan(tmp_path, monkeypatch)
    legacy = planner.get(state["session_id"])
    legacy.pop("evaluation_purpose")
    legacy.pop("user_parameter_requirements")
    with planner.methods._db() as db:
        planner.methods._put(db, "method_plan", state["session_id"], legacy, update=True)
    with pytest.raises(ValueError, match="evidence|legacy|purpose"):
        planner.recommend(state["session_id"], Selector(recommendation()), actor="researcher")
    after = planner.get(state["session_id"])
    assert "evaluation_purpose" not in after and "user_parameter_requirements" not in after
    assert after["recommendation"]["choice"] is None


@pytest.mark.parametrize("origin", ["assistant", "fixture"])
def test_non_user_choices_keep_their_actual_origin(tmp_path, origin):
    planner, state, *_ = new_plan(tmp_path)
    candidate = declared_candidate(origin=origin)
    result = planner.generate_candidates(state["session_id"], Selector({"candidates": [candidate]}), actor="researcher")
    assert result[0]["parameter_candidates"][0]["origin"] == origin


def test_historical_candidate_user_origin_is_not_rewritten_on_export(tmp_path):
    planner, state, *_ = new_plan(tmp_path)
    legacy = planner.get(state["session_id"])
    legacy.pop("evaluation_purpose")
    legacy.pop("user_parameter_requirements")
    old = declared_candidate(origin="user")
    legacy["scientific_candidates"] = [old]
    legacy["candidates"] = [{**old, "availability": "unknown", "method_version_id": None,
                             "implementation_confidence": "unknown", "applicability": "eligible", "blockers": [], "discovery": None}]
    with planner.methods._db() as db:
        planner.methods._put(db, "method_plan", state["session_id"], legacy, update=True)
    before = planner.methods.database.read_bytes()
    exported = planner.export(state["session_id"])
    assert exported["candidates"][0]["parameter_candidates"][0]["origin"] == "user"
    assert planner.methods.database.read_bytes() == before
