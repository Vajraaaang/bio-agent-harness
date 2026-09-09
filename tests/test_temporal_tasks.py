"""Shipped temporal tasks must bind classification to observed data values."""
import copy
import json

import pandas as pd
import pytest
from jsonschema import validate, ValidationError

from devbench_engine.runner import EvalRunner, evaluate_grader, _lookup
from devharness_core.storage import ChunkedExpressionStore


def test_explicit_field_paths_preserve_decimal_stage_keys_and_legacy_paths():
    answer = {"stages": {"E8.5": 12, "E8": {"5": 99}}}
    assert _lookup(answer, ["stages", "E8.5"]) == 12
    assert _lookup(answer, "stages.E8.5") == 99
    result = evaluate_grader(answer, {"type":"numeric_tolerance", "config":{
        "field":["stages","E8.5"], "expected_from_truth":["values","E8.5"],
        "tolerances":{"type":"absolute","value":0}}}, {"ground_truth":{"values":{"E8.5":12}}})
    assert result["passed"]


@pytest.mark.parametrize("field", [[], ["stages", 8.5], [""], None, 4])
def test_invalid_explicit_paths_are_configuration_errors(field):
    with pytest.raises(ValueError, match="field path"):
        _lookup({}, field)


@pytest.fixture
def tasks(tmp_path):
    from benchmarks.temporal import build_temporal_tasks
    return [json.loads(path.read_text()) for path in build_temporal_tasks(tmp_path / "definitions")]


def _stage(tmp_path, task, suffix=""):
    from benchmarks.temporal import stage_temporal_task
    work = tmp_path / (task["id"] + suffix)
    return work, stage_temporal_task(work, task["dataset"])


def _kinds(spec):
    yield spec["type"]
    for child in spec.get("config", {}).get("children", []):
        yield from _kinds(child)


def test_five_generated_tasks_have_numeric_and_temporal_gates(tasks, tmp_path):
    assert len(tasks) == len({task["id"] for task in tasks}) == 5
    assert {task["dataset"]["profile"] for task in tasks} == {
        "increasing", "decreasing", "transient", "plateau", "label_reversal"}
    for task in tasks:
        assert "temporal_monotonicity" in set(_kinds(task["grader"]))
        assert task["grader"]["type"] == "all_of"
        work, staged = _stage(tmp_path, task)
        truth = staged["ground_truth"]
        validate(truth["reference_answer"], task["answer_schema"])
        grade = evaluate_grader(truth["reference_answer"], task["grader"], {"ground_truth":truth})
        assert grade["passed"], (task["id"], grade)
        assert len(staged["truth_digest"]) == 64
        inputs = json.loads((work / "task_inputs.json").read_text())
        assert set(inputs) == {"target_gene", "time_column", "initial_formula", "summary_operation", "evidence_scope"}
        assert not any("truth" in p.name or "gold" in p.name or "grader" in p.name for p in work.iterdir())
        assert not {"reference_answer", "expected", "stage_means", "temporal_score"} & inputs.keys()


def test_private_reference_matches_independent_sample_aligned_means(tasks, tmp_path):
    for task in tasks:
        work, staged = _stage(tmp_path, task)
        inputs = json.loads((work / "task_inputs.json").read_text())
        metadata = pd.read_csv(work / "metadata.csv", dtype={"sample_id":str})
        with ChunkedExpressionStore(work / "counts.zarr") as store:
            series = pd.Series(store.get_gene_vector(inputs["target_gene"]), index=store.sample_names)
            joined = metadata.assign(expression=metadata.sample_id.map(series))
            observed = joined.groupby(inputs["time_column"])["expression"].mean().to_dict()
            if task["dataset"]["profile"] == "label_reversal":
                assert metadata.sample_id.tolist() != store.sample_names
        assert observed == pytest.approx(staged["ground_truth"]["stage_means"], abs=1e-12)


def test_profile_behaviors_include_two_negative_controls_and_reversed_labels(tasks, tmp_path):
    expected = {"increasing":1., "decreasing":-1., "plateau":0., "label_reversal":-1.}
    for task in tasks:
        _, staged = _stage(tmp_path, task)
        truth = staged["ground_truth"]
        profile = task["dataset"]["profile"]
        if profile == "transient":
            assert abs(truth["temporal_score"]) < 1 and not truth["is_monotonic"]
        else:
            assert truth["temporal_score"] == expected[profile]
        assert truth["is_monotonic"] == (abs(truth["temporal_score"]) == 1)
        assert 1 <= truth["condition_number"] <= 1000


@pytest.mark.parametrize("corruption", ["shift_values", "wrong_score", "wrong_direction", "wrong_flag", "wrong_condition"])
def test_correct_looking_or_contradictory_answers_fail_binary_composite(tasks, tmp_path, corruption):
    task = next(t for t in tasks if t["dataset"]["profile"] == "increasing")
    _, staged = _stage(tmp_path, task)
    truth = staged["ground_truth"]
    answer = copy.deepcopy(truth["reference_answer"])
    if corruption == "shift_values":
        answer["stage_kinetics"] = {k:v+17 for k,v in answer["stage_kinetics"].items()}
    elif corruption == "wrong_score":
        answer["temporal_score"] = -1
    elif corruption == "wrong_direction":
        answer["temporal_direction"] = "decreasing"
    elif corruption == "wrong_flag":
        answer["is_monotonic"] = False
    else:
        answer["condition_number"] += 10
    validate(answer, task["answer_schema"])
    result = evaluate_grader(answer, task["grader"], {"ground_truth":truth})
    assert not result["passed"] and result["score"] == 0
    if corruption == "shift_values":
        assert result["children"][-1]["passed"]  # Direction alone would pass.


@pytest.mark.parametrize("values", [{}, {"E8.5":1}, {"E8.5":"1","E10.5":2,"E12.5":3,"E14.5":4}])
def test_missing_or_nonnumeric_stage_evidence_cannot_pass(tasks, tmp_path, values):
    task = tasks[0]
    _, staged = _stage(tmp_path, task)
    answer = copy.deepcopy(staged["ground_truth"]["reference_answer"])
    answer["stage_kinetics"] = values
    with pytest.raises(ValidationError):
        validate(answer, task["answer_schema"])
    assert not evaluate_grader(answer, task["grader"], {"ground_truth":staged["ground_truth"]})["passed"]


def test_staging_route_and_repeatability_keep_reference_out_of_workspace(tasks, tmp_path):
    from benchmarks.temporal import build_temporal_tasks
    task_path = build_temporal_tasks(tmp_path / "routed-definitions")[0]
    runner = EvalRunner(task_path, audit_dir=tmp_path / "audit")
    first_work, second_work = tmp_path / "first", tmp_path / "second"
    first_work.mkdir()
    second_work.mkdir()
    first, second = runner._stage(first_work), runner._stage(second_work)
    assert first["truth_digest"] == second["truth_digest"]
    assert first["ground_truth"] == second["ground_truth"]
    assert json.loads((first_work / "answer_schema.json").read_text()) == runner.spec["answer_schema"]
    assert not (first_work / "task_spec.json").exists()


@pytest.mark.parametrize("dataset", [
    {"builder":"observed_temporal","profile":"unknown","seed":42},
    {"builder":"observed_temporal","profile":"increasing","seed":True},
    {"builder":"observed_temporal","profile":"increasing","seed":-1},
])
def test_invalid_dataset_configuration_is_refused_before_staging(tmp_path, dataset):
    from benchmarks.temporal import stage_temporal_task
    work = tmp_path / "work"
    with pytest.raises(ValueError):
        stage_temporal_task(work, dataset)
    assert not work.exists()
