"""Public executor configuration and protocol tests; no public data or models run."""
import os
from pathlib import Path
import tempfile

import pytest


def plan(*, repetitions=1, datasets=("developmental_general",), requested_model="pinned-model"):
    from devbench.study.public_study import PublicStudyPlan

    return PublicStudyPlan(
        study_id="public-test",
        blinding_nonce="1" * 64,
        datasets=datasets,
        repetitions=repetitions,
        requested_model=requested_model,
    )


def bundles_for(study):
    from devbench.study.public_study import DATASETS, PreparedPublicTask, public_identity

    return {
        dataset_id: PreparedPublicTask(
            root=f"/opt/public-bundles/{dataset_id}",
            dataset_id=dataset_id,
            source_id=DATASETS[dataset_id][0],
            task_id=public_identity(study, "task", dataset_id),
            plan_identity=study.sha256(),
            input_sha256=f"{index + 1:064x}",
            reference_sha256=f"{index + 11:064x}",
            preparation_sha256=f"{index + 21:064x}",
            independence_id=("public-shared-developmental" if dataset_id.startswith("developmental_")
                             else "public-shared-seqc"),
        )
        for index, dataset_id in enumerate(study.datasets)
    }


@pytest.mark.parametrize("authorization", [False, None, 0, 1, "true"])
def test_execution_guard_precedes_plan_paths_and_imports(authorization, monkeypatch):
    from devbench.study.public_execution import execute_public_study

    monkeypatch.setattr(Path, "resolve", lambda *a, **k: pytest.fail("path reached before guard"))
    with pytest.raises(PermissionError, match="disabled"):
        execute_public_study(object(), object(), object(), workspace_root=object(),
                             allow_data_execution=authorization)


def test_private_worker_and_staging_entry_points_are_guarded():
    from devbench.study.public_execution import _public_attempt_worker, _stage_public_inputs

    with pytest.raises(PermissionError, match="disabled"):
        _public_attempt_worker(None)
    with pytest.raises(PermissionError, match="disabled"):
        _stage_public_inputs(None, None, None, None)


def test_configuration_validation_is_contract_only(monkeypatch):
    from devbench.study.public_execution import validate_public_execution_configuration

    study = plan(datasets=("developmental_general", "developmental_markers"))
    prepared = bundles_for(study)
    monkeypatch.setattr(Path, "open", lambda *a, **k: pytest.fail("opened public data"))
    actual, copied = validate_public_execution_configuration(study, prepared)
    assert actual.sha256() == study.sha256()
    assert tuple(copied) == study.datasets
    assert copied["developmental_general"].independence_id == copied["developmental_markers"].independence_id


@pytest.mark.parametrize("case", ["missing", "extra", "path_only", "wrong_task", "wrong_source", "wrong_plan"])
def test_configuration_rejects_mismatched_prepared_tasks(case):
    from devbench.study.public_study import DATASETS
    study = plan()
    prepared = bundles_for(study)
    bundle = prepared[study.datasets[0]]
    if case == "missing":
        prepared = {}
    elif case == "extra":
        prepared["seqc_controls"] = bundle
    elif case == "path_only":
        prepared[study.datasets[0]] = Path(bundle.root)
    else:
        update = {
            "wrong_task": {"task_id": "f" * 64},
            "wrong_source": {"source_id": DATASETS[study.datasets[0]][0] + "-wrong"},
            "wrong_plan": {"plan_identity": "e" * 64},
        }[case]
        prepared[study.datasets[0]] = bundle.model_copy(update=update)
    from devbench.study.public_execution import validate_public_execution_configuration
    with pytest.raises((TypeError, ValueError)):
        validate_public_execution_configuration(study, prepared)


@pytest.mark.parametrize("requested_model", [None, "bad model"])
def test_configuration_requires_requested_model(requested_model):
    from devbench.study.public_execution import validate_public_execution_configuration
    study = plan(requested_model=requested_model)
    with pytest.raises(ValueError, match="requested_model"):
        validate_public_execution_configuration(study, bundles_for(study))


def test_default_live_cap_fails_before_path_or_launch(monkeypatch):
    from devbench.study.public_execution import execute_public_study

    study = plan(repetitions=3)
    prepared = bundles_for(study)
    monkeypatch.setattr(Path, "resolve", lambda *a, **k: pytest.fail("path reached before live cap"))
    with pytest.raises(ValueError, match="max_live_attempts"):
        execute_public_study(study, prepared, object(), workspace_root=object(),
                             allow_data_execution=True)


def test_initial_records_preserve_all_slots_and_unknown_scientific_scope():
    from devbench.study.public_execution import initial_public_attempt_records
    from devbench.study.public_study import schedule_public_attempts

    study = plan(datasets=("developmental_general", "seqc_controls"))
    records = initial_public_attempt_records(study, bundles_for(study))
    schedule = schedule_public_attempts(study)
    assert [row.attempt_id for row in records] == [row["attempt_id"] for row in schedule]
    assert {row.condition for row in records} == {"baseline", "optional", "enforced"}
    assert records[0].independence_id == records[1].independence_id == records[2].independence_id
    for row in records:
        assert row.attempted is False and row.completion_status == "not_started"
        assert row.reference_task_eligible is None
        assert row.reference_task_success is None and row.complete_biological_correctness is None
        assert row.report_accepted is None and row.workflow_passed is None
        assert row.actual_model is None and row.tokens is None and row.cost is None
        assert row.metrics == {} and row.metric_missingness == {}


def test_public_record_never_accepts_complete_biological_correctness():
    from devbench.study.public_execution import initial_public_attempt_records
    from pydantic import ValidationError

    study = plan()
    record = initial_public_attempt_records(study, bundles_for(study))[0]
    with pytest.raises(ValidationError):
        record.model_copy(update={"complete_biological_correctness": True}).__class__.model_validate(
            {**record.model_dump(mode="json"), "complete_biological_correctness": True})


@pytest.mark.parametrize("condition", ["baseline", "optional", "enforced"])
def test_public_guidance_has_fixed_protocol_without_private_reference(condition):
    from devbench.study.public_execution import public_participant_guidance

    text = public_participant_guidance(condition)
    assert "solve.py" in text and "answer_schema.json" in text
    assert "private/reference.json" not in text and "blinding_nonce" not in text
    assert "technical libraries are not patients" in text
    if condition == "optional":
        assert "no tool use" in text
    if condition == "enforced":
        assert "receipt" in text


def test_grade_projection_keeps_acceptance_and_limited_correctness_separate():
    from devbench.study.public_execution import apply_public_grade

    values = {"task_id": "a" * 64, "input_sha256": "b" * 64}
    apply_public_grade(values, {
        "task_id": "a" * 64,
        "input_sha256": "b" * 64,
        "reference_task_success": True,
        "complete_biological_correctness": None,
        "metrics": {"max_error": 0.1},
        "errors": [],
        "metric_missingness": {},
    })
    assert values["reference_task_success"] is True
    assert values["complete_biological_correctness"] is None
    assert "report_accepted" not in values


@pytest.mark.parametrize("condition,evidence,workflow,accepted", [
    ("baseline", (), None, True),
    ("baseline", ("a" * 64,), None, False),
    ("optional", (), False, True),
    ("optional", ("a" * 64,), False, False),
    ("optional", ("a" * 64,), True, True),
    ("enforced", (), False, False),
    ("enforced", ("a" * 64,), True, True),
])
def test_report_acceptance_requires_receipt_binding_only_when_claimed_or_enforced(
        condition, evidence, workflow, accepted):
    from devbench.study.public_answers import DevelopmentalObservation, ObservedGeneCurve, PublicAnswer
    from devbench.study.public_execution import _report_acceptance

    answer = PublicAnswer(task_id="b" * 64, input_sha256="c" * 64,
        payload=DevelopmentalObservation(stage_labels=("E1", "E2", "E3"),
            curves=(ObservedGeneCurve(gene_id="g", means=(1.0, 2.0, 3.0)),)),
        tool_evidence_ids=evidence, limitations=("fixture",))
    assert _report_acceptance(condition, answer, workflow) is accepted


@pytest.mark.parametrize("dataset_id,reference,expected", [
    ("developmental_general", {
        "kind": "developmental_observation", "gene_ids": ["g1"],
    }, True),
    ("seqc_controls", {
        "kind": "seqc_normalization", "eligible_evaluation_controls": [],
        "evaluation_ratios_A_over_B": {},
    }, False),
])
def test_reference_eligibility_is_answer_independent(dataset_id, reference, expected):
    from devbench.study.public_execution import _reference_task_eligibility

    study = plan(datasets=(dataset_id,))
    bundle = bundles_for(study)[dataset_id]
    reference.update(record_format="devbench.public-reference/1", task_id=bundle.task_id,
                     input_sha256=bundle.input_sha256, source_id=bundle.source_id,
                     complete_biological_truth=False)
    result = _reference_task_eligibility(bundle, reference)
    assert result["reference_task_eligible"] is expected
    assert "means" not in result and "evaluation_ratios_A_over_B" not in result


def test_eligible_attempt_failure_counts_without_claiming_complete_correctness():
    from devbench.study.public_execution import initial_public_attempt_records
    from devbench.study.public_records import parse_public_attempt

    study = plan()
    initial = initial_public_attempt_records(study, bundles_for(study))[0]
    failed = parse_public_attempt({**initial.model_dump(mode="json"),
        "attempted": True, "completion_status": "invalid_output", "integrity_status": "verified",
        "shared_runtime_sha256": "c" * 64, "harness_sha256": "d" * 64,
        "reference_task_eligible": True, "reference_task_success": False,
        "errors": ["invalid_or_missing_answer"], "report_accepted": False,
        "trusted_tool_capture_complete": True, "trusted_tool_calls": 0, "trusted_receipt_count": 0,
        "elapsed_sec": 1.0,
        "accounting_completeness": {"elapsed_sec": "complete", "RSS_MB": "unavailable",
                                    "tokens": "unavailable", "cost": "unavailable"}})
    assert failed.reference_task_success is False
    assert failed.accepted_final is False and failed.complete_biological_correctness is None


@pytest.mark.skipif(os.environ.get("DEVBENCH_RUN_LOCAL_FIXTURES") != "1",
                    reason="Small numerical fixture opt-in is required")
def test_prelaunch_block_retains_reportable_records_for_every_slot():
    """A missing executable proves durable failure records without launching a model."""
    import numpy as np
    from devbench.study.public_execution import execute_public_study
    from devbench.study.public_reporting import load_public_records
    from devbench.study.public_study import _materialize, default_public_plan

    repository = Path(__file__).resolve().parents[1]
    scratch = repository / "tmp"
    scratch.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=scratch, prefix="public-executor-") as temporary:
        root = Path(temporary)
        study = default_public_plan(datasets=("developmental_general",), requested_model="test-only")
        genes = ["g1", "g2", "g3", "g4"]
        values = np.arange(36, dtype=float).reshape(4, 9)
        metadata = [{"sample_id": f"s{i}", "stage": f"E{10 + i // 3}", "organ": "Brain",
                     "batch": "fixture", "specimen_label": f"s{i}", "independent_animal_id": ""}
                    for i in range(9)]
        reference = {"stage_labels": ["E10", "E11", "E12"], "gene_ids": genes,
                     "means": values.reshape(4, 3, 3).mean(axis=2).tolist()}
        bundle = _materialize(study, "developmental_general", root / "bundle", values, genes,
            metadata, {}, reference, {"fixture": True}, allow_data_execution=True)
        result = execute_public_study(study, {bundle.dataset_id: bundle}, root / "output",
            workspace_root=root / "workspaces", codex_executable="definitely-missing-codex",
            allow_data_execution=True, allow_exec_preflight=True)
        assert result["status"] == "blocked" and result["attempted_count"] == 0
        assert len(result["attempt_artifacts"]) == 3
        records, _ = load_public_records(root / "output/records.json", study,
            execution_receipt_sha256=result["execution_manifest_sha256"], allow_result_analysis=True)
        assert all(row.completion_status == "blocked" and row.reference_task_eligible is True
                   for row in records)
