"""Contract-only checks may run before authorization; numerical cases may not."""
import copy
import importlib.util
import json

import pytest
from pydantic import ValidationError


def unattempted_record():
    """Metadata-only record. No input dataset or benchmark result is constructed."""
    return {
        "attempt_id": "planned-attempt", "study_id": "planned-study",
        "plan_identity": "a" * 64, "task_identity": "b" * 64,
        "cohort_id": "planned-cohort", "independence_id": "planned-cluster",
        "scenario": "balanced_temporal", "condition": "baseline",
        "actor_type": "model_participant", "repetition": 1,
        "source_kind": "synthetic", "input_sha256": None,
        "shared_environment_sha256": None, "harness_sha256": None,
        "actual_model": None, "requested_model": None, "model_identity_verified": False,
        "attempted": False, "completion_status": "not_started", "accepted_final": False,
        "scientific_success": None, "report_accepted": None,
        "workflow_passed": None, "integrity_status": "unavailable",
        "biological_metrics": {}, "metric_missingness": {}, "diagnostic_outcomes": {},
        "refusal_outcomes": {}, "elapsed_sec": None, "RSS_MB": None,
        "tokens": None, "cost": None, "accounting_completeness": {
            "elapsed_sec": "unavailable", "RSS_MB": "unavailable",
            "tokens": "unavailable", "cost": "unavailable"},
        "trusted_tool_capture_complete": False, "trusted_tool_calls": None,
    }


def test_schema_metrics_modules_exist():
    for module in ("records", "scoring", "metrics"):
        assert importlib.util.find_spec(f"devbench.study.{module}") is not None


def test_schema_scoring_api_import_is_passive():
    import inspect
    from devbench.study.scoring import score_answer
    assert list(inspect.signature(score_answer).parameters) == [
        "answer", "truth", "thresholds", "expected_task_id", "expected_input_sha256"]


def test_schema_unattempted_record_preserves_unknowns():
    from devbench.study.records import AttemptRecord
    record = AttemptRecord.model_validate(unattempted_record())
    exported = record.model_dump(mode="json")
    assert exported["record_format"] == "devbench.study.attempt/1"
    assert exported["scientific_success"] is None
    assert exported["report_accepted"] is None
    assert exported["cost"] is None
    assert exported["trusted_tool_calls"] is None
    assert AttemptRecord.model_validate_json(json.dumps(exported)) == record


@pytest.mark.parametrize("change", [
    {"scientific_success": True}, {"scientific_success": False},
    {"accepted_final": True}, {"workflow_passed": True},
    {"report_accepted": True}, {"report_accepted": False},
    {"actual_model": "observed", "model_identity_verified": True},
    {"cost": float("nan")}, {"repetition": True}, {"unexpected": "field"},
    {"biological_metrics": {"recall": None}},
    {"trusted_tool_capture_complete": True},
    {"integrity_status": "verified"},
])
def test_schema_rejects_incoherent_unattempted_metadata(change):
    from devbench.study.records import AttemptRecord
    payload = unattempted_record()
    payload.update(change)
    with pytest.raises(ValidationError):
        AttemptRecord.model_validate(payload)


def test_schema_unknown_required_accounting_cannot_be_defaulted():
    from devbench.study.records import AttemptRecord
    payload = unattempted_record()
    del payload["cost"]
    with pytest.raises(ValidationError):
        AttemptRecord.model_validate(payload)


def structurally_accepted_metadata():
    """Categorical schema fixture only; no genes, metrics, or run aggregation."""
    payload = unattempted_record()
    payload.update(attempted=True, completion_status="completed", accepted_final=True,
                   integrity_status="verified", input_sha256="c"*64,
                   shared_environment_sha256="d"*64, trusted_tool_capture_complete=True,
                   trusted_tool_calls=0)
    return payload


def test_schema_correctness_is_independent_of_report_rejection():
    from devbench.study.records import AttemptRecord
    payload = structurally_accepted_metadata()
    payload.update(condition="enforced", scientific_success=True,
                   workflow_passed=False, report_accepted=False)
    record = AttemptRecord.model_validate(payload)
    assert record.scientific_success is True
    assert record.report_accepted is False


@pytest.mark.parametrize("workflow", [False, None])
def test_schema_enforced_report_acceptance_requires_workflow_pass(workflow):
    from devbench.study.records import AttemptRecord
    payload = structurally_accepted_metadata()
    payload.update(condition="enforced", scientific_success=True,
                   workflow_passed=workflow, report_accepted=True)
    with pytest.raises(ValidationError):
        AttemptRecord.model_validate(payload)


def test_schema_baseline_report_acceptance_does_not_assert_scientific_correctness():
    from devbench.study.records import AttemptRecord
    payload = structurally_accepted_metadata()
    payload.update(scientific_success=False, report_accepted=True)
    record = AttemptRecord.model_validate(payload)
    assert record.scientific_success is False
    assert record.report_accepted is True


def test_default_empty_summary_does_not_import_numerical_libraries(monkeypatch):
    import builtins
    from devbench.study.metrics import analyze_attempts
    from devbench.study.planning import default_study_plan
    original = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        assert name.split(".")[0] not in {"numpy", "scipy", "sklearn", "pandas"}
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    plan = default_study_plan()
    report = analyze_attempts([], plan)
    assert report["status"] == "not_run"
    assert report["planned_attempt_count"] == len(plan.cohorts) * plan.repetitions * 3
    assert report["headline"]["task_success_rate"] is None
    assert report["headline"]["accepted_scientific_success_rate"] is None
    assert report["headline"]["raw_scientific_success_rate"] is None
    assert report["headline"]["correct_answer_rejection_rate"] is None
    assert report["headline"]["incorrect_answer_acceptance_rate"] is None
    assert report["headline"]["fdr"] is None
    assert report["headline"]["cost_per_success"] is None
    assert report["primary_outcome"] == "raw_scientific_success"
    assert report["headline"]["primary_outcome"] == "raw_scientific_success"
    assert report["headline"]["cost_per_accepted_scientific_success"] is None
    assert report["group_summaries"] == []
    assert report["quarantined_record_count"] == 0
    assert report["invalid_evidence_record_count"] == 0
    assert report["error_rows"] == []
    assert report["run_rows"] == report["gene_rows"] == []
    assert report["bootstrap"]["performed"] is False
    assert all(c["confidence_interval"] is None for c in report["paired_comparisons"])
    assert all(c["raw_scientific_mean_delta"] is None for c in report["paired_comparisons"])
    assert all(c["accepted_scientific_mean_delta"] is None for c in report["paired_comparisons"])
    assert all(c["accepted_scientific_confidence_interval"] is None for c in report["paired_comparisons"])
    assert all(c["estimand"] == "raw_scientific_success" for c in report["paired_comparisons"])
    json.dumps(report, allow_nan=False)


@pytest.mark.parametrize("kwargs", [
    {"bootstrap_replicates": True}, {"bootstrap_replicates": 0},
    {"bootstrap_seed": -1}, {"bootstrap_seed": "42"},
])
def test_schema_bootstrap_configuration_is_strict(kwargs):
    from devbench.study.metrics import analyze_attempts
    from devbench.study.planning import default_study_plan
    with pytest.raises(ValueError):
        analyze_attempts([], default_study_plan(), **kwargs)


# All following tests are intentionally unexecuted. Removing this explicit gate
# requires authorization to process even miniature nonempty benchmark results.
numerical = pytest.mark.skipif(__import__("os").environ.get("DEVBENCH_RUN_LOCAL_FIXTURES") != "1", reason="Small local numerical/report fixtures require opt-in")


def future_truth():
    return {
        "record_format": "devbench.study.truth/1", "target": "temporal_omnibus",
        "gene_ids": ["null", "signal", "omitted"], "time_grid": [0.0, 1.0, 2.0],
        "curve_reference": {"reference_batch": "batch1", "library_factor": 1.0,
                            "batch_reference_policy": "first_observation", "biological_unit_reference": "population_mean"},
        "population_identifiability": {"identifiable": True, "reasons": []},
        "expected_diagnostics": [],
        "genes": {
            "null": {"null": True, "shape": "flat", "amplitude_log2": 0.0,
                     "mean_reference": 100.0, "effect": 0.0,
                     "effect_units": "log2_normalized_mean_change", "predicted_curve": [100.0]*3},
            "signal": {"null": False, "eligible": False, "shape": "increasing", "amplitude_log2": 1.0,
                       "mean_reference": 150.0, "effect": 1.0,
                       "effect_units": "log2_normalized_mean_change", "predicted_curve": [100.0, 150.0, 200.0]},
            "omitted": {"null": False, "eligible": False, "shape": "increasing", "amplitude_log2": 1.0,
                        "mean_reference": 150.0, "effect": 1.0,
                        "effect_units": "log2_normalized_mean_change", "predicted_curve": [100.0, 150.0, 200.0]},
        },
    }


def future_answer():
    from devbench.study.contracts import content_sha256
    return {
        "task_id": "task", "input_sha256": "a"*64, "decision": "findings",
        "claim_scope": "association", "analysis": {
            "method": "prespecified", "formula": "time + batch", "measurement_type": "raw_counts",
            "normalization": "RLE", "selection_criterion": "fixed", "inference_scope": "fixed_design",
            "hypothesis_family_id": content_sha256({"gene_ids": future_truth()["gene_ids"], "target": "temporal_omnibus"}),
            "biological_unit_column": "biological_unit_id", "reference_description": "first batch; library factor one",
            "reference": future_truth()["curve_reference"]},
        "gene_statistics": [{"gene_id": "signal", "status": "tested", "p_value": 0.001,
            "q_value": 0.003, "claimed_discovery": True, "shape": "increasing", "effect": 1.0,
            "effect_units": "log2_normalized_mean_change"}],
    }


@numerical
def test_numerical_eligibility_recomputed_and_omitted_genes_reduce_recall():
    from devbench.study.scoring import score_answer
    from devbench.study.contracts import MetricThresholds
    result = score_answer(future_answer(), future_truth(), thresholds=MetricThresholds(),
                          expected_task_id="task", expected_input_sha256="a"*64)
    assert result["metrics"]["signed_recall"] == 0.5
    assert result["metrics"]["eligible_count"] == 2
    assert result["metrics"]["bh_arithmetic_valid"] is True
    assert result["gene_rows"][0]["p_value"] is None


@numerical
def test_numerical_q_values_cannot_shrink_missing_family():
    from devbench.study.scoring import score_answer
    from devbench.study.contracts import MetricThresholds
    answer = future_answer()
    answer["gene_statistics"][0]["q_value"] = 0.001
    result = score_answer(answer, future_truth(), thresholds=MetricThresholds(),
                          expected_task_id="task", expected_input_sha256="a"*64)
    assert result["scientific_success"] is False
    assert result["metrics"]["bh_arithmetic_valid"] is False


@numerical
def test_numerical_false_refusal_requires_structural_claim():
    from devbench.study.scoring import score_answer
    from devbench.study.contracts import MetricThresholds
    answer = future_answer()
    answer.update(decision="no_supported_discovery", gene_statistics=[])
    result = score_answer(answer, future_truth(), thresholds=MetricThresholds(),
                          expected_task_id="task", expected_input_sha256="a"*64)
    assert result["diagnostics"]["incorrect_structural_refusal"] is False


@numerical
def test_numerical_public_observations_do_not_become_negative_latent_truth():
    from devbench.study.scoring import score_answer
    from devbench.study.contracts import MetricThresholds
    truth = {"record_format": "devbench.study.truth/1", "source_kind": "public_processed",
             "reference_scope": "observational_expression", "gene_ids": ["signal"],
             "available_reference_metrics": [], "genes": {}}
    answer = {"task_id": "task", "input_sha256": "a"*64,
              "decision": "no_supported_discovery", "claim_scope": "association"}
    result = score_answer(answer, truth, thresholds=MetricThresholds(),
                          expected_task_id="task", expected_input_sha256="a"*64)
    assert result["metrics"]["fdp"] is None
    assert result["scientific_success"] is None


@numerical
def test_numerical_duplicate_attempt_ids_rejected():
    from devbench.study.metrics import analyze_attempts
    from devbench.study.planning import default_study_plan
    with pytest.raises(ValueError, match="duplicate attempt_id"):
        analyze_attempts([unattempted_record(), copy.deepcopy(unattempted_record())], default_study_plan())


def future_attempt(plan, cohort, condition, *, success=True, repetition=1):
    """Future numerical record helper, called only by explicitly skipped tests."""
    from devbench.study.contracts import content_sha256, task_identity
    from devbench.study.planning import schedule_attempts
    scheduled = next(item for item in schedule_attempts(plan)
                     if item.cohort_id == cohort.cohort_id and item.condition == condition and item.repetition == repetition)
    record = unattempted_record()
    record.update(
        attempt_id=scheduled.attempt_id, study_id=plan.study_id,
        plan_identity=plan.sha256(), task_identity=task_identity(plan, cohort),
        cohort_id=cohort.cohort_id, independence_id=cohort.cohort_id,
        scenario=cohort.scenario, condition=condition, repetition=repetition,
        attempted=True, completion_status="completed", accepted_final=True,
        integrity_status="verified", scientific_success=success, report_accepted=True,
        workflow_passed=True,
        actual_model="observed-model-version", requested_model="requested-alias", model_identity_verified=True,
        input_sha256=content_sha256(cohort.cohort_id), shared_environment_sha256="e"*64,
        biological_metrics={"fdp": 0.0}, trusted_tool_capture_complete=True, trusted_tool_calls=0,
        cost=1.0,
    )
    record["accounting_completeness"]["cost"] = "complete"
    return record


@numerical
def test_numerical_timeout_retained_in_task_success_denominator():
    from devbench.study.metrics import analyze_attempts
    from devbench.study.planning import default_study_plan
    plan = default_study_plan(stage="smoke")
    good = future_attempt(plan, plan.cohorts[0], "enforced")
    failed = future_attempt(plan, plan.cohorts[1], "enforced")
    failed.update(completion_status="timeout", accepted_final=False, scientific_success=None,
                  report_accepted=False, workflow_passed=None,
                  biological_metrics={}, trusted_tool_capture_complete=False, trusted_tool_calls=None)
    report = analyze_attempts([good, failed], plan)
    group = report["group_summaries"][0]
    assert group["raw_task_success_rate"] == 0.5
    assert group["completion_rate"] == 0.5
    assert group["resources"]["cost"]["total"] == 2.0
    assert group["resources"]["cost"]["per_success"] == 2.0
    assert group["fdr_availability"] == 0.5


@numerical
def test_numerical_no_completed_answers_cannot_report_safe_fdr():
    from devbench.study.metrics import analyze_attempts
    from devbench.study.planning import default_study_plan
    plan = default_study_plan(stage="smoke")
    failed = future_attempt(plan, plan.cohorts[0], "baseline")
    failed.update(completion_status="provider_error", accepted_final=False, scientific_success=None,
                  report_accepted=False, workflow_passed=None,
                  biological_metrics={}, trusted_tool_capture_complete=False, trusted_tool_calls=None)
    report = analyze_attempts([failed], plan)
    assert report["group_summaries"][0]["fdr"] is None
    assert report["group_summaries"][0]["raw_task_success_rate"] == 0.0


@numerical
def test_numerical_smoke_pairs_do_not_gain_spurious_confidence_interval():
    from devbench.study.metrics import analyze_attempts
    from devbench.study.planning import default_study_plan
    plan = default_study_plan(stage="smoke")
    records = [future_attempt(plan, cohort, condition, success=condition == "enforced")
               for cohort in plan.cohorts for condition in plan.conditions]
    # Harness hashes intentionally differ; the common environment is identical.
    for record in records:
        record["harness_sha256"] = "a"*64 if record["condition"] == "enforced" else "b"*64
    result = analyze_attempts(records, plan)
    primary = result["paired_comparisons"][0]
    assert primary["mean_delta"] == 1.0
    assert primary["pairing_coverage"] == 1.0
    assert primary["confidence_interval"] is None
    assert primary["interval_reason"] == "insufficient_independent_groups"
    assert result["bootstrap"]["performed"] is False


@numerical
def test_numerical_unknown_model_identity_is_not_a_verified_pair():
    from devbench.study.metrics import analyze_attempts
    from devbench.study.planning import default_study_plan
    plan = default_study_plan(stage="smoke")
    records = [future_attempt(plan, cohort, condition) for cohort in plan.cohorts for condition in plan.conditions]
    for record in records:
        record.update(actual_model=None, model_identity_verified=False)
    result = analyze_attempts(records, plan)
    primary = result["paired_comparisons"][0]
    assert primary["mean_delta"] is None
    assert primary["paired_repetition_count"] == 0
    assert any(p["code"] == "observed_model_identity_not_verified" for p in primary["problems"])


@numerical
def test_numerical_missing_cost_prevents_complete_totals():
    from devbench.study.metrics import analyze_attempts
    from devbench.study.planning import default_study_plan
    plan = default_study_plan(stage="smoke")
    records = [future_attempt(plan, cohort, "baseline") for cohort in plan.cohorts]
    records[1]["cost"] = None
    records[1]["accounting_completeness"]["cost"] = "unavailable"
    cost = analyze_attempts(records, plan)["group_summaries"][0]["resources"]["cost"]
    assert cost["known_subtotal"] == 1.0
    assert cost["total"] is cost["per_success"] is None
    assert cost["coverage"] == 0.5


@numerical
def test_numerical_reused_input_bytes_merge_independence_groups():
    from devbench.study.metrics import analyze_attempts
    from devbench.study.planning import default_study_plan
    plan = default_study_plan(stage="smoke")
    records = [future_attempt(plan, cohort, condition) for cohort in plan.cohorts for condition in plan.conditions]
    for record in records:
        record["input_sha256"] = "f"*64
    report = analyze_attempts(records, plan)
    assert report["paired_comparisons"][0]["independent_group_count"] == 1
    assert any(p["code"] == "reused_input_claimed_as_distinct_independence_groups" for p in report["problems"])


@numerical
def test_numerical_wrong_estimand_cannot_supply_effect_or_curve_error():
    from devbench.study.scoring import score_answer
    from devbench.study.contracts import MetricThresholds
    answer = future_answer()
    answer["analysis"]["reference"]["reference_batch"] = "wrong-batch"
    score = score_answer(answer, future_truth(), thresholds=MetricThresholds(),
                         expected_task_id="task", expected_input_sha256="a"*64)
    assert score["scientific_success"] is False
    assert score["metrics"]["effect_mae"] is None
    assert "estimand_reference_missing_or_mismatched" in score["errors"]


@numerical
def test_numerical_wrong_sign_is_direction_error_not_global_null_false_discovery():
    from devbench.study.scoring import score_answer
    from devbench.study.contracts import MetricThresholds
    answer = future_answer()
    answer["gene_statistics"][0].update(effect=-1.0, shape="decreasing")
    score = score_answer(answer, future_truth(), thresholds=MetricThresholds(),
                         expected_task_id="task", expected_input_sha256="a"*64)
    assert score["metrics"]["fdp"] == 0.0
    assert score["metrics"]["directional_error_rate"] == 1.0
    assert score["metrics"]["signed_recall"] == 0.0


@numerical
def test_numerical_unidentifiable_design_requires_specific_diagnostic():
    from devbench.study.scoring import score_answer
    from devbench.study.contracts import MetricThresholds
    truth = future_truth()
    truth["population_identifiability"] = {"identifiable": False, "reasons": ["complete aliasing"]}
    truth["expected_diagnostics"] = ["time_batch_complete_aliasing"]
    answer = {"task_id": "task", "input_sha256": "a"*64, "decision": "insufficient_evidence",
              "claim_scope": "descriptive", "diagnostics": [{"code": "generic_failure",
                  "message": "Unable to fit", "scope": "model"}]}
    score = score_answer(answer, truth, thresholds=MetricThresholds(),
                         expected_task_id="task", expected_input_sha256="a"*64)
    assert score["scientific_success"] is False
    answer["diagnostics"][0].update(code="time_batch_complete_aliasing", scope="design")
    score = score_answer(answer, truth, thresholds=MetricThresholds(),
                         expected_task_id="task", expected_input_sha256="a"*64)
    assert score["scientific_success"] is True


@numerical
def test_numerical_nonfinite_attempt_measurements_are_rejected():
    from devbench.study.metrics import analyze_attempts
    from devbench.study.planning import default_study_plan
    plan = default_study_plan(stage="smoke")
    record = future_attempt(plan, plan.cohorts[0], "baseline")
    record["biological_metrics"]["fdp"] = float("nan")
    with pytest.raises((ValidationError, ValueError)):
        analyze_attempts([record], plan)


@numerical
def test_numerical_foreign_and_unknown_attempts_are_quarantined_from_headlines():
    from devbench.study.metrics import analyze_attempts
    from devbench.study.planning import default_study_plan
    plan = default_study_plan(stage="smoke")
    foreign = future_attempt(plan, plan.cohorts[0], "baseline")
    foreign["study_id"] = "another-study"
    unknown = future_attempt(plan, plan.cohorts[1], "baseline")
    unknown["attempt_id"] = "unknown-attempt"
    result = analyze_attempts([foreign, unknown], plan)
    assert result["quarantined_record_count"] == 2
    assert result["attempted_count"] == 0
    assert result["headline"]["task_success_rate"] is None
    assert result["group_summaries"] == result["run_rows"] == []
    assert len(result["error_rows"]) == 2


@numerical
def test_numerical_known_corrupted_binding_remains_failed_in_expected_arm():
    from devbench.study.metrics import analyze_attempts
    from devbench.study.planning import default_study_plan
    plan = default_study_plan(stage="smoke")
    record = future_attempt(plan, plan.cohorts[0], "baseline")
    record.update(condition="optional", task_identity="f"*64)
    result = analyze_attempts([record], plan)
    group = result["group_summaries"][0]
    assert group["condition"] == "baseline"
    assert group["attempted_count"] == 1
    assert group["raw_scientific_success_count"] == 0
    assert group["accepted_scientific_success_count"] == 0
    assert result["invalid_evidence_record_count"] == 1
    assert result["error_rows"][0]["original_record"]["scientific_success"] is True


@numerical
def test_numerical_public_diagnostics_and_missingness_do_not_invent_truth():
    from devbench.study.scoring import score_answer
    from devbench.study.contracts import MetricThresholds
    truth = {"record_format": "devbench.study.truth/1", "source_kind": "public_processed",
             "reference_scope": "observational_expression", "gene_ids": ["gene"],
             "available_reference_metrics": [], "genes": {}}
    answer = {"task_id": "task", "input_sha256": "a"*64,
              "decision": "no_supported_discovery", "claim_scope": "association"}
    score = score_answer(answer, truth, thresholds=MetricThresholds(),
                         expected_task_id="task", expected_input_sha256="a"*64)
    assert score["diagnostics"]["required_diagnostics_correct"] is None
    assert score["diagnostics"]["unsupported_population_claim"] is None
    assert set(score["missingness"]) == {key for key, value in score["metrics"].items() if value is None}


@numerical
def test_numerical_huge_finite_estimates_are_rejected_without_overflow():
    from devbench.study.scoring import score_answer
    from devbench.study.contracts import MetricThresholds
    answer = future_answer()
    row = answer["gene_statistics"][0]
    row.update(effect=1e308, confidence_interval=[-1e308, 1e308], confidence_level=0.95,
               interval_scope="fixed_design", time_grid=[0.0, 1.0, 2.0], predicted_curve=[1e-308, 1.0, 1e308])
    score = score_answer(answer, future_truth(), thresholds=MetricThresholds(),
                         expected_task_id="task", expected_input_sha256="a"*64)
    assert score["scientific_success"] is False
    assert score["metrics"]["interval_mean_width"] is None
    assert any("numerical" in error for error in score["errors"])
    json.dumps(score, allow_nan=False)


@numerical
def test_numerical_null_guess_without_analysis_cannot_pass_or_claim_safe_fdr():
    from devbench.study.scoring import score_answer
    from devbench.study.contracts import MetricThresholds
    truth = future_truth()
    truth["gene_ids"] = ["null"]
    truth["genes"] = {"null": truth["genes"]["null"]}
    answer = {"task_id": "task", "input_sha256": "a"*64,
              "decision": "no_supported_discovery", "claim_scope": "association"}
    score = score_answer(answer, truth, thresholds=MetricThresholds(),
                         expected_task_id="task", expected_input_sha256="a"*64)
    assert score["scientific_success"] is False
    assert score["metrics"]["fdp"] is None


@numerical
def test_numerical_tested_genes_require_q_values_even_without_discovery():
    from devbench.study.scoring import score_answer
    from devbench.study.contracts import MetricThresholds
    answer = future_answer()
    answer["decision"] = "no_supported_discovery"
    answer["gene_statistics"][0].update(claimed_discovery=False, q_value=None)
    score = score_answer(answer, future_truth(), thresholds=MetricThresholds(),
                         expected_task_id="task", expected_input_sha256="a"*64)
    assert score["scientific_success"] is False
    assert score["metrics"]["bh_arithmetic_valid"] is False
    assert score["metrics"]["fdp"] is None


@numerical
def test_numerical_supported_null_analysis_can_pass_with_complete_bh_accounting():
    from devbench.study.scoring import score_answer
    from devbench.study.contracts import MetricThresholds, content_sha256
    truth = future_truth()
    truth.update(gene_ids=["null"], genes={"null": truth["genes"]["null"]})
    answer = future_answer()
    answer["decision"] = "no_supported_discovery"
    answer["analysis"]["hypothesis_family_id"] = content_sha256({"gene_ids": ["null"], "target": "temporal_omnibus"})
    answer["gene_statistics"] = [{"gene_id": "null", "status": "tested", "p_value": 0.8,
                                  "q_value": 0.8, "shape": "flat", "claimed_discovery": False}]
    score = score_answer(answer, truth, thresholds=MetricThresholds(),
                         expected_task_id="task", expected_input_sha256="a"*64)
    assert score["scientific_success"] is True
    assert score["metrics"]["fdp"] == 0.0


@numerical
def test_numerical_unavailable_hypotheses_keep_missing_probabilities():
    from devbench.study.scoring import score_answer
    from devbench.study.contracts import MetricThresholds
    truth = future_truth()
    answer = future_answer()
    answer.update(decision="insufficient_evidence", diagnostics=[{
        "code": "no_applicable_tests", "message": "No test could be fit", "scope": "model"}])
    answer["gene_statistics"] = [{"gene_id": gene, "status": "no_information",
                                  "missing_reason": "No usable observations"} for gene in truth["gene_ids"]]
    score = score_answer(answer, truth, thresholds=MetricThresholds(),
                         expected_task_id="task", expected_input_sha256="a"*64)
    assert score["scientific_success"] is False
    assert score["metrics"]["fdp"] is None
    assert all(row["p_value"] is None and row["q_value"] is None for row in score["gene_rows"])


@numerical
def test_numerical_continuous_effect_must_match_curve_endpoint_difference():
    from devbench.study.scoring import score_answer
    from devbench.study.contracts import MetricThresholds
    answer = future_answer()
    answer["analysis"]["measurement_type"] = "continuous"
    answer["gene_statistics"][0].update(effect=1.0, effect_units="expression_difference",
                                       time_grid=[0.0, 1.0, 2.0], predicted_curve=[0.0, 2.0, 4.0])
    score = score_answer(answer, future_truth(), thresholds=MetricThresholds(),
                         expected_task_id="task", expected_input_sha256="a"*64)
    assert score["scientific_success"] is False
    assert "effect_inconsistent_with_declared_curve_endpoints" in score["errors"]


@numerical
def test_numerical_control_cannot_borrow_model_attempt_slot():
    from devbench.study.metrics import analyze_attempts
    from devbench.study.planning import default_study_plan
    plan = default_study_plan(stage="smoke")
    record = future_attempt(plan, plan.cohorts[0], "baseline")
    record.update(actor_type="scripted_control", model_identity_verified=False, actual_model=None)
    report = analyze_attempts([record], plan)
    assert report["quarantined_record_count"] == 1
    assert report["headline_inference_blocked"] is True
    assert report["group_summaries"] == []


@numerical
def test_numerical_primary_pair_is_raw_correctness_and_acceptance_is_secondary():
    from devbench.study.metrics import analyze_attempts
    from devbench.study.planning import default_study_plan
    plan = default_study_plan(stage="smoke")
    records = [future_attempt(plan, cohort, condition, success=condition == "enforced")
               for cohort in plan.cohorts for condition in plan.conditions]
    for record in records:
        if record["condition"] == "enforced":
            record.update(report_accepted=False, workflow_passed=False)
    report = analyze_attempts(records, plan)
    primary = report["paired_comparisons"][0]
    enforced = next(group for group in report["group_summaries"] if group["condition"] == "enforced")
    assert report["primary_outcome"] == primary["primary_outcome"] == "raw_scientific_success"
    assert primary["mean_delta"] == primary["raw_scientific_mean_delta"] == 1.0
    assert primary["accepted_scientific_mean_delta"] == 0.0
    assert enforced["task_success_rate"] == enforced["raw_scientific_success_rate"] == 1.0
    assert enforced["accepted_scientific_success_rate"] == 0.0
    assert enforced["resources"]["cost"]["per_success"] == 1.0
    assert enforced["resources"]["cost"]["per_accepted_scientific_success"] is None


@numerical
def test_numerical_public_descriptive_completion_keeps_scientific_success_unknown():
    from devbench.study.contracts import StudyPlan
    from devbench.study.metrics import analyze_attempts
    from devbench.study.planning import default_study_plan
    payload = default_study_plan(stage="smoke").model_dump(mode="json")
    for recipe in payload["dataset_recipes"]:
        recipe.update(source_kind="public_processed", generator_family=None,
                      public_accession="GSE47774", latent_truth_scope="none", status="not_acquired",
                      biological_unit_policy="require_verified_biological_unit_identifiers",
                      independent_unit_count=None)
    plan = StudyPlan.model_validate_json(json.dumps(payload))
    records = [future_attempt(plan, cohort, condition) for cohort in plan.cohorts for condition in plan.conditions]
    for record in records:
        record.update(source_kind="public_processed", scientific_success=None,
                      biological_metrics={"effect_mae": 0.25})
    report = analyze_attempts(records, plan)
    for group in report["group_summaries"]:
        for key in ("task_success_rate", "raw_scientific_success_rate", "accepted_scientific_success_rate",
                    "raw_scientific_success_count", "accepted_scientific_success_count"):
            assert group[key] is None
        assert group["completion_rate"] == 1.0
        assert group["metrics"]["effect_mae"]["mean"] == 0.25
        assert set(group["outcomes"]) == {"scientific_outcome_unavailable"}
    for comparison in report["paired_comparisons"]:
        assert comparison["mean_delta"] is None
        assert comparison["raw_scientific_mean_delta"] is None
        assert comparison["accepted_scientific_mean_delta"] is None
        assert comparison["confidence_interval"] is None
    assert all(row["primary_task_success"] is None and row["accepted_scientific_success"] is None
               for row in report["run_rows"])
