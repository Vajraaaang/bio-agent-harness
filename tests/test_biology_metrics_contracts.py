"""Only schema, empty/configuration, and default-denial checks may execute."""
import builtins
import importlib.util
import json

import pytest
from pydantic import ValidationError


def configuration_plan():
    """Explicit metadata only: no random nonce, assigned seed, or generator."""
    from devbench.study.biology import load_biology_catalog
    from devbench.study.biology_planning import BiologyStudyPlan, default_biology_recipe
    from devbench.study.contracts import content_sha256
    catalog = load_biology_catalog()
    recipes = tuple(default_biology_recipe(case.case_id)
                    for template in catalog.templates for case in template.cases)
    return BiologyStudyPlan(study_id="biology-metadata-only", blinding_nonce="a"*64,
                            catalog_sha256=content_sha256(catalog.model_dump(mode="json")), recipes=recipes)


def unattempted_metadata():
    return {
        "attempt_id": "bio-attempt-planned", "study_id": "biology-metadata-only", "plan_identity": "a"*64,
        "task_identity": "b"*64, "recipe_id": "cohort-one", "cohort_id": "cohort-one",
        "case_id": "T01-dense", "template_id": "T01", "role": "evaluation",
        "condition": "baseline", "repetition": 1, "actor_type": "model_participant",
        "independence_id": "cohort-one", "source_kind": "synthetic", "source_sha256": None,
        "input_sha256": None, "shared_runtime_sha256": None, "harness_sha256": None,
        "actual_model": None, "requested_model": None, "model_identity_sha256": None,
        "model_identity_verified": False, "attempted": False, "completion_status": "not_started",
        "accepted_final": False, "scientific_success": None, "available_target_scientific_success": None,
        "report_accepted": None, "workflow_passed": None, "integrity_status": "unavailable",
        "target_reference_availability": {}, "target_reference_scopes": {},
        "target_reference_components": {},
        "target_reference_missingness": {}, "reference_availability_sha256": None,
        "per_target_results": [], "safe_rows": [], "thresholds_sha256": None,
        "trusted_tool_capture_complete": False, "trusted_tool_calls": None, "trusted_receipt_count": None,
        "elapsed_sec": None, "RSS_MB": None, "tokens": None, "cost": None,
        "accounting_completeness": {name: "unavailable" for name in ("elapsed_sec", "RSS_MB", "tokens", "cost")},
    }


def test_schema_biology_metrics_modules_exist():
    for module in ("biology_records", "biology_metrics"):
        assert importlib.util.find_spec(f"devbench.study.{module}") is not None


def test_schema_unattempted_biology_record_has_no_fabricated_outcomes():
    from devbench.study.biology_records import BiologicalAttemptRecord
    record = BiologicalAttemptRecord.model_validate_json(json.dumps(unattempted_metadata()))
    assert record.record_format == "devbench.biology.attempt/1"
    assert record.scientific_success is record.available_target_scientific_success is None
    assert record.target_reference_availability == {}


@pytest.mark.parametrize("change", [
    {"scientific_success": True}, {"scientific_success": False}, {"report_accepted": True},
    {"available_target_scientific_success": False}, {"accepted_final": True},
    {"workflow_passed": True}, {"trusted_tool_capture_complete": True},
    {"model_identity_verified": True}, {"cost": float("nan")}, {"unexpected": "value"},
])
def test_schema_unattempted_record_rejects_execution_claims(change):
    from devbench.study.biology_records import BiologicalAttemptRecord
    payload = unattempted_metadata()
    payload.update(change)
    with pytest.raises((ValidationError, ValueError)):
        BiologicalAttemptRecord.model_validate_json(json.dumps(payload))


def test_default_empty_biology_summary_is_metadata_without_rng_or_data_import(monkeypatch):
    from devbench.study.biology_metrics import analyze_biology_attempts
    plan = configuration_plan()
    original = builtins.__import__

    def guard(name, *args, **kwargs):
        assert name.split(".")[0] not in {"numpy", "pandas", "scipy", "sklearn", "random"}
        assert name not in {"devbench.study.biology_bulk", "devbench.study.biology_singlecell"}
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guard)
    summary = analyze_biology_attempts([], plan)
    assert summary["status"] == "not_run"
    assert summary["planned_attempt_count"] == 291
    assert summary["planned_case_count"] == 97
    assert summary["primary_outcome"] == "raw_scientific_success"
    from devbench.study.contracts import content_sha256
    assert summary["thresholds_sha256"] == content_sha256(plan.thresholds.model_dump(mode="json"))
    assert summary["headline"]["task_success_rate"] is None
    assert summary["group_summaries"] == summary["run_rows"] == summary["safe_rows"] == []
    assert summary["bootstrap"]["performed"] is False
    assert all(row["mean_delta"] is None for row in summary["paired_comparisons"])
    json.dumps(summary, allow_nan=False)


def test_default_denial_precedes_nonempty_record_validation():
    from devbench.study.biology_metrics import analyze_biology_attempts
    with pytest.raises(PermissionError, match="allow_result_analysis"):
        analyze_biology_attempts([object()], configuration_plan())


def test_default_grade_projection_denied_before_inspection():
    from devbench.study.biology_records import biology_grade_record_fields
    with pytest.raises(PermissionError, match="allow_result_analysis"):
        biology_grade_record_fields(object(), input_sha256="a"*64)


def test_schema_public_grade_fields_follow_producer_allowlists():
    from devbench.study.biology_scoring import PUBLIC_METRIC_IDS, PUBLIC_SAFE_ROW_FIELDS, SafeEstimateRow
    from devbench.study.biology_records import BiologicalAttemptRecord
    assert set(SafeEstimateRow.model_fields) <= PUBLIC_SAFE_ROW_FIELDS
    assert not {"true_effect", "generation_seed", "private_truth", "reference_values"} & PUBLIC_METRIC_IDS
    required = set(BiologicalAttemptRecord.model_json_schema()["required"])
    assert {"target_reference_availability", "target_reference_components", "reference_availability_sha256"} <= required


def test_schema_grade_identity_is_bound_before_attachment():
    from devbench.study.biology_scoring import BiologyGrade
    assert {"task_id", "input_sha256", "gene_universe_sha256"} <= set(BiologyGrade.model_json_schema()["required"])


@pytest.mark.parametrize("rows, scalars", [(1, 1_000_001), (1_000_001, 0), (-1, 0), (True, 0)])
def test_configuration_aggregate_export_budget(rows, scalars):
    from devbench.study.biology_metrics import validate_biology_export_budget
    with pytest.raises(ValueError):
        validate_biology_export_budget(row_count=rows, scalar_count=scalars)


def test_configuration_aggregate_export_boundary_is_allowed():
    from devbench.study.biology_metrics import validate_biology_export_budget
    assert validate_biology_export_budget(row_count=1_000_000, scalar_count=1_000_000) is None


@pytest.mark.parametrize("kwargs", [
    {"bootstrap_replicates": True}, {"bootstrap_replicates": 0}, {"bootstrap_seed": -1},
    {"minimum_independent_cohorts": 1}, {"allow_result_analysis": "true"},
])
def test_schema_biology_analysis_configuration(kwargs):
    from devbench.study.biology_metrics import analyze_biology_attempts
    with pytest.raises((TypeError, ValueError)):
        analyze_biology_attempts([], configuration_plan(), **kwargs)


numerical = pytest.mark.skipif(__import__("os").environ.get("DEVBENCH_RUN_LOCAL_FIXTURES") != "1", reason="NO DATA: nonempty biological-result evaluation is not authorized")


@numerical
def test_numerical_missing_answer_cannot_manufacture_unknown_reference():
    from devbench.study.biology_records import BiologicalAttemptRecord, reference_availability_identity
    payload = unattempted_metadata()
    payload.update(attempted=True, completion_status="timeout", integrity_status="verified",
                   input_sha256="c"*64, source_sha256="d"*64, shared_runtime_sha256="e"*64,
                   target_reference_availability={"mean_contrast": "scorable"},
                   target_reference_scopes={"mean_contrast": "supported"}, report_accepted=False,
                   target_reference_components={"mean_contrast": ["effect_estimation"]},
                   thresholds_sha256="f"*64)
    payload["reference_availability_sha256"] = reference_availability_identity(payload)
    record = BiologicalAttemptRecord.model_validate_json(json.dumps(payload))
    from devbench.study.biology_metrics import _task_value, _target_value
    assert _task_value(record) == 0.0
    assert _target_value(record, "mean_contrast") == 0.0


@numerical
def test_numerical_unavailable_target_remains_unknown_even_on_timeout():
    from devbench.study.biology_records import BiologicalAttemptRecord, reference_availability_identity
    payload = unattempted_metadata()
    payload.update(attempted=True, completion_status="timeout", integrity_status="verified",
                   input_sha256="c"*64, source_sha256="d"*64, shared_runtime_sha256="e"*64,
                   target_reference_availability={"mean_contrast": "unavailable"},
                   target_reference_scopes={"mean_contrast": "limited_reference"},
                   target_reference_components={"mean_contrast": []},
                   target_reference_missingness={"mean_contrast": "unadjudicated_reference"},
                   report_accepted=False, thresholds_sha256="f"*64)
    payload["reference_availability_sha256"] = reference_availability_identity(payload)
    record = BiologicalAttemptRecord.model_validate_json(json.dumps(payload))
    from devbench.study.biology_metrics import _task_value, _target_value
    assert _task_value(record) is None
    assert _target_value(record, "mean_contrast") is None


@numerical
def test_numerical_partial_contract_known_failure_never_enters_primary_denominator():
    from devbench.study.biology_records import BiologicalAttemptRecord, reference_availability_identity
    from devbench.study.biology_metrics import _task_value, _target_value
    payload = unattempted_metadata()
    payload.update(attempted=True, completion_status="timeout", integrity_status="verified",
                   input_sha256="c"*64, source_sha256="d"*64, shared_runtime_sha256="e"*64,
                   target_reference_availability={"mean_contrast": "scorable", "variability": "unavailable"},
                   target_reference_scopes={"mean_contrast": "supported", "variability": "limited_reference"},
                   target_reference_components={"mean_contrast": ["effect_estimation"], "variability": []},
                   target_reference_missingness={"variability": "unadjudicated_reference"},
                   report_accepted=False, thresholds_sha256="f"*64)
    payload["reference_availability_sha256"] = reference_availability_identity(payload)
    record = BiologicalAttemptRecord.model_validate_json(json.dumps(payload))
    assert _task_value(record) is None
    assert _target_value(record, "mean_contrast") == 0.0
    assert _target_value(record, "variability") is None


@numerical
def test_numerical_refusal_is_scorable_without_numeric_reference():
    from devbench.study.biology_records import BiologicalAttemptRecord, reference_availability_identity
    from devbench.study.biology_metrics import _task_value
    payload = unattempted_metadata()
    payload.update(attempted=True, completion_status="completed", accepted_final=True, scientific_success=True,
                   available_target_scientific_success=True, integrity_status="verified", input_sha256="c"*64,
                   source_sha256="d"*64, shared_runtime_sha256="e"*64, report_accepted=True, thresholds_sha256="f"*64,
                   target_reference_availability={"mean_contrast": "scorable"},
                   target_reference_scopes={"mean_contrast": "limited_reference"},
                   target_reference_components={"mean_contrast": ["structural_refusal"]},
                   per_target_results=[{"target_id": "mean_contrast", "outcome": "correct_refusal",
                       "scientific_success": True, "reference_availability": "scorable",
                       "reference_scope": "limited_reference", "scorable_components": ["structural_refusal"]}])
    payload["reference_availability_sha256"] = reference_availability_identity(payload)
    record = BiologicalAttemptRecord.model_validate_json(json.dumps(payload))
    assert _task_value(record) == 1.0


@numerical
def test_numerical_hierarchy_gives_equal_case_and_template_weights():
    from types import SimpleNamespace
    from devbench.study.biology_metrics import _hierarchical
    # Repeated low-performing runs in one cohort cannot outweigh other cases.
    records = [SimpleNamespace(template_id="T01", case_id="T01-a", cohort_id="cohort-a", value=0.0) for _ in range(10)]
    records += [SimpleNamespace(template_id="T01", case_id="T01-b", cohort_id="cohort-b", value=1.0),
                SimpleNamespace(template_id="T02", case_id="T02-a", cohort_id="cohort-c", value=1.0)]
    assert _hierarchical(records, lambda record: record.value) == 0.75


@numerical
def test_numerical_one_cohort_per_case_cannot_generate_confidence_intervals():
    from devbench.study.biology_metrics import _interval
    rows = [{"template_id": "T01", "case_id": f"T01-case-{i}", "cohort_id": f"cohort-{i}",
             "independence_id": f"cohort-{i}", "raw_delta": float(i % 2)} for i in range(12)]
    interval, reason = _interval(rows, "raw_delta", replicates=2000, seed=20260911, minimum=10)
    assert interval is None
    assert reason == "fewer_than_two_independent_cohorts_in_an_eligible_case"


@numerical
def test_numerical_timeout_stays_primary_failure_without_becoming_incorrect_answer():
    from devbench.study.biology_records import BiologicalAttemptRecord, reference_availability_identity
    from devbench.study.biology_metrics import _group, _key
    payload = unattempted_metadata()
    payload.update(attempted=True, completion_status="timeout", integrity_status="verified",
                   input_sha256="c"*64, source_sha256="d"*64, shared_runtime_sha256="e"*64,
                   target_reference_availability={"mean_contrast": "scorable"},
                   target_reference_scopes={"mean_contrast": "supported"}, report_accepted=False,
                   target_reference_components={"mean_contrast": ["effect_estimation"]}, thresholds_sha256="f"*64)
    payload["reference_availability_sha256"] = reference_availability_identity(payload)
    record = BiologicalAttemptRecord.model_validate_json(json.dumps(payload))
    group = _group([record], _key(record), "baseline")
    assert group["primary_eligible_attempt_count"] == 1
    assert group["task_success_rate"] == 0.0
    assert group["incorrect_answer_report_decision_count"] == 0
    assert group["incorrect_answer_acceptance_rate"] is None


@numerical
def test_numerical_out_of_plan_thresholds_remain_failure_in_fixed_denominator():
    from devbench.study.biology import load_biology_catalog
    from devbench.study.biology_planning import schedule_biology_attempts
    from devbench.study.biology_records import BiologicalAttemptRecord, reference_availability_identity
    from devbench.study.biology_metrics import _bind, _task_value
    from devbench.study.contracts import content_sha256
    plan = configuration_plan()
    scheduled = schedule_biology_attempts(plan)[0]
    targets = load_biology_catalog().template(scheduled.template_id).targets
    payload = unattempted_metadata()
    payload.update({name: getattr(scheduled, name) for name in (
        "attempt_id", "study_id", "plan_identity", "task_identity", "cohort_id", "recipe_id",
        "template_id", "case_id", "condition", "repetition")})
    payload.update(attempted=True, completion_status="completed", accepted_final=True, scientific_success=True,
                   available_target_scientific_success=True, integrity_status="verified", input_sha256="c"*64,
                   source_sha256="d"*64, shared_runtime_sha256="e"*64, thresholds_sha256="f"*64,
                   target_reference_availability={target.target_id: "scorable" for target in targets},
                   target_reference_scopes={target.target_id: "refusal" for target in targets},
                   target_reference_components={target.target_id: ["structural_refusal"] for target in targets},
                   per_target_results=[{"target_id": target.target_id, "outcome": "correct_refusal",
                       "scientific_success": True, "reference_availability": "scorable", "reference_scope": "refusal",
                       "scorable_components": ["structural_refusal"]} for target in targets])
    payload["reference_availability_sha256"] = reference_availability_identity(payload)
    record = BiologicalAttemptRecord.model_validate_json(json.dumps(payload))
    retained, errors, invalid, blocked = _bind([record], plan)
    assert len(retained) == 1 and retained[0].attempted
    assert retained[0].target_reference_availability == record.target_reference_availability
    assert retained[0].thresholds_sha256 == content_sha256(plan.thresholds.model_dump(mode="json"))
    assert _task_value(retained[0]) == 0.0
    assert scheduled.attempt_id in invalid and blocked is False
    assert errors[0]["disposition"] == "counted_invalid_evidence_failure"
    assert errors[0]["original_record"]["scientific_success"] is True
    assert "safe_rows" not in errors[0]["original_record"]
    assert "per_target_results" not in errors[0]["original_record"]
