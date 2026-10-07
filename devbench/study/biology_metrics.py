"""Guarded broad-biology comparisons, independent of the classic six scenarios.

Primary: raw full-contract correctness on the answer-independent fully scorable
reference subset. All attempted failures remain in that fixed denominator.
Unknown target truth is never a failure, and partial-contract correctness is an
explicit secondary result. No data, RNG, numerical libraries, or models run on
import or on the default empty path.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

from .biology_planning import BiologyStudyPlan
from .biology_records import parse_biological_attempt, reference_availability_identity
from .contracts import canonical_json_bytes, content_sha256
from .metrics import _independence_clusters, _mean, _measurement, _percentile
from .records import validate_finite_json

WEIGHTING = "mean_repetitions_within_cohort_equal_cohorts_within_case_equal_cases_within_template_equal_templates"
CONTRASTS = (("enforced", "baseline"), ("optional", "baseline"), ("enforced", "optional"))
RESOURCE_FIELDS = ("elapsed_sec", "RSS_MB", "tokens", "cost")


def validate_biology_export_budget(*, row_count: int, scalar_count: int) -> None:
    """Validate export-size metadata without receiving or inspecting arrays."""
    if any(type(value) is not int or value < 0 for value in (row_count, scalar_count)):
        raise ValueError("Export counts must be nonnegative integers")
    if row_count > 1_000_000 or scalar_count > 1_000_000:
        raise ValueError("Combined public prediction export exceeds the one-million row/scalar budget")


def _task_value(record, *, accepted=False):
    mask = record.target_reference_availability
    if not record.attempted or not mask or any(state != "scorable" for state in mask.values()):
        return None
    correct = record.accepted_final and record.integrity_status == "verified" and record.scientific_success is True
    return float(correct and (not accepted or record.report_accepted is True))


def _target_value(record, target_id, *, accepted=False):
    if not record.attempted or record.target_reference_availability.get(target_id) != "scorable":
        return None
    row = next((row for row in record.per_target_results if row.target_id == target_id), None)
    correct = bool(record.accepted_final and record.integrity_status == "verified" and row is not None and row.scientific_success is True)
    return float(correct and (not accepted or record.report_accepted is True))


def _available_value(record):
    if not record.attempted or not any(state == "scorable" for state in record.target_reference_availability.values()):
        return None
    return float(record.accepted_final and record.integrity_status == "verified"
                 and record.available_target_scientific_success is True)


def _ratio(left, right):
    return left/right if right else None


def _hierarchical(records, value):
    cohorts = defaultdict(list)
    for record in records:
        observed = value(record)
        if observed is not None:
            cohorts[(record.template_id, record.case_id, record.cohort_id)].append(observed)
    cases = defaultdict(list)
    for (template, case, _), values in cohorts.items():
        cases[(template, case)].append(_mean(values))
    templates = defaultdict(list)
    for (template, _), values in cases.items():
        templates[template].append(_mean(values))
    return _mean([_mean(values) for values in templates.values()])


def _comparison_empty(left, right):
    return {"contrast": f"{left}-{right}", "primary_contrast": (left, right) == CONTRASTS[0],
            "primary_outcome": "raw_scientific_success", "estimand": "full_contract_fixed_reference_subset",
            "mean_delta": None, "confidence_interval": None, "interval_reason": "no_attempts",
            "accepted_scientific_mean_delta": None, "accepted_scientific_confidence_interval": None,
            "accepted_scientific_interval_reason": "no_attempts", "available_target_mean_delta": None,
            "status": "not_run", "cohort_deltas": [], "groups": [], "problems": [],
            "paired_repetition_count": 0, "paired_cohort_count": 0, "independent_group_count": 0,
            "pairing_coverage": None, "eligible_pair_count": 0, "unknown_reference_pair_count": 0}


def _empty(plan, replicates, seed, minimum):
    from .study_precision import binary_design
    evaluation = [recipe for recipe in plan.recipes if recipe.role == "evaluation"]
    design_diagnostics=binary_design(len(evaluation))
    case_counts=Counter((recipe.template_id,recipe.case_id) for recipe in evaluation)
    template_cases=Counter(template for template,_ in case_counts)
    weights={1/len(template_cases)/template_cases[template]/count for (template,_),count in case_counts.items()}
    design_diagnostics['equal_planned_cohort_weights']=len(weights)==1
    if len(weights)!=1:design_diagnostics['difference_grid']=None
    return {
        "record_format": "devbench.biology.summary/1", "study_id": plan.study_id, "plan_identity": plan.sha256(),
        "thresholds_sha256": content_sha256(plan.thresholds.model_dump(mode="json")),
        "status": "not_run", "primary_outcome": "raw_scientific_success",
        "primary_eligibility": "all_required_target_references_scorable_determined_before_answer",
        "weighting": WEIGHTING, "planned_attempt_count": len(evaluation)*plan.repetitions*3,
        "design_diagnostics": design_diagnostics,
        "reasoning_evaluation": {"status": "human_review_pending", "reviewed_count": 0,
                                 "holistic_insight_success_rate": None},
        "detectability": {"status": "not_calibrated_for_current_case_and_method", "minimum_detectable_effect": None,
                          "failure_interpretation": "a missed signal does not distinguish limited detection power from analytical error"},
        "planned_cohort_count": len(evaluation), "planned_case_count": len({r.case_id for r in evaluation}),
        "planned_template_count": len({r.template_id for r in evaluation}),
        "planned_development_recipe_count": len(plan.recipes)-len(evaluation),
        "received_record_count": 0, "record_count": 0, "attempted_count": 0,
        "quarantined_record_count": 0, "invalid_evidence_record_count": 0,
        "headline": {"task_success_rate": None, "accepted_scientific_success_rate": None,
                     "available_target_success_rate": None, "primary_eligible_attempt_count": None,
                     "unknown_reference_attempt_count": None, "groups": [], "scope": "separate_source_model_and_condition"},
        "group_summaries": [], "target_summaries": [], "case_summaries": [],
        "paired_comparisons": [_comparison_empty(*contrast) for contrast in CONTRASTS],
        "outcome_taxonomy": {}, "run_rows": [], "safe_rows": [], "target_rows": [], "error_rows": [], "problems": [],
        "bootstrap": {"performed": False, "replicates": replicates, "seed": seed,
                      "minimum_independent_cohorts": minimum, "confidence_level": 0.95,
                      "unit": "independence_id_cluster_of_cohorts", "strata": "template_id_and_case_id",
                      "repetitions": "retained_inside_cohort_not_resampled_as_independent_units"},
        "limitations": ["Numerical evaluation has not been qualified; thresholds remain uncalibrated.",
                        "Malformed or duplicate records reject the entire aggregation; protected raw executor artifacts remain authoritative.",
                        "Counts of genes, cells, subjects within a cohort, and repeated attempts are not independent cohorts.",
                        "Partial-reference contracts are excluded from the full-contract primary denominator independently of outcomes."]}


def _key(record):
    return (record.source_kind, record.actual_model, record.model_identity_sha256,
            record.model_identity_verified, record.actor_type)


def _labels(key):
    return dict(zip(("source_kind", "actual_model", "model_identity_sha256", "model_identity_verified", "actor_type"), key))


def _outcome(record):
    if not record.attempted:
        return "not_attempted"
    if record.integrity_status == "failed":
        return "invalid_evidence_failure"
    if record.completion_status != "completed":
        return record.completion_status
    if not record.accepted_final:
        return "completed_without_structural_final"
    if _task_value(record) is None:
        return "full_contract_reference_unavailable"
    if record.scientific_success is True:
        return "scientifically_correct" if record.report_accepted is True else "scientifically_correct_report_not_accepted"
    return "scientific_failure"


def _mcse(records, value, clusters=None, blocked=False):
    from .study_precision import stratified_mcse
    if blocked:
        return {"monte_carlo_se": None, "reason": "reference_or_actor_binding_blocks_inference",
                "independent_cohort_count": None}
    return stratified_mcse([{'template_id':r.template_id,'case_id':r.case_id,'cohort_id':r.cohort_id,
        'independence_id':(clusters or {}).get(r.independence_id,r.independence_id),'value':value(r)} for r in records])


def _repeatability(records, blocked):
    from .study_precision import repeatability
    cells=defaultdict(list)
    for record in records:
        if record.attempted:cells[record.cohort_id].append(record)
    result=[]
    for cohort, rows in sorted(cells.items()):
        bindings={(r.input_sha256,r.source_sha256,r.shared_runtime_sha256,r.harness_sha256) for r in rows}
        valid=not blocked and len(bindings)==1 and all(r.integrity_status=='verified' for r in rows)
        if rows[0].actor_type=='model_participant':valid=valid and all(r.model_identity_verified for r in rows)
        values=[_task_value(r) for r in sorted(rows,key=lambda r:r.repetition)]
        summary=repeatability(values if valid else [])
        if not valid:summary['reason']='unverified_or_changed_repetition_configuration'
        result.append({'cohort_id':cohort,**summary,'scope':'conditional end-to-end success variation; may include execution failures'})
    return result


def _group(records, key, condition, *, blocked=False, clusters=None):
    from .execution_usage import tool_usage_metrics
    attempted = [record for record in records if record.attempted]
    eligible = [record for record in attempted if _task_value(record) is not None]
    available = [record for record in attempted if _available_value(record) is not None]
    raw_count = sum(_task_value(record) == 1 for record in eligible)
    accepted_count = sum(_task_value(record, accepted=True) == 1 for record in eligible)
    correct_decisions = [r for r in eligible if _task_value(r) == 1 and r.report_accepted is not None]
    incorrect_decisions = [r for r in eligible if r.accepted_final and r.integrity_status == "verified"
                           and r.scientific_success is False and r.report_accepted is not None]
    return {**_labels(key), **tool_usage_metrics(records), "condition": condition, "record_count": len(records), "attempted_count": len(attempted),
        "completion_count": sum(r.completion_status == "completed" for r in attempted),
        "structural_final_count": sum(r.accepted_final for r in attempted),
        "completion_rate": _ratio(sum(r.completion_status == "completed" for r in attempted), len(attempted)),
        "primary_eligible_attempt_count": len(eligible) if not blocked else None,
        "unknown_reference_attempt_count": len(attempted)-len(eligible) if not blocked else None,
        "primary_reference_coverage": _ratio(len(eligible), len(attempted)) if not blocked else None,
        "scientific_success_count": raw_count if eligible and not blocked else None,
        "task_success_rate": _hierarchical(eligible, _task_value) if not blocked else None,
        "task_success_precision": _mcse(eligible, _task_value, clusters, blocked),
        "within_cohort_repeatability": _repeatability(attempted, blocked),
        "full_denominator_task_success_rate": _ratio(raw_count, len(eligible)) if not blocked else None,
        "accepted_scientific_success_rate": _hierarchical(eligible, lambda r: _task_value(r, accepted=True)) if not blocked else None,
        "available_target_success_rate": _hierarchical(available, _available_value) if not blocked else None,
        "available_target_eligible_attempt_count": len(available) if not blocked else None,
        "correct_answer_rejection_rate": _ratio(sum(r.report_accepted is False for r in correct_decisions), len(correct_decisions)) if not blocked else None,
        "correct_answer_report_decision_count": len(correct_decisions) if not blocked else None,
        "incorrect_answer_acceptance_rate": _ratio(sum(r.report_accepted is True for r in incorrect_decisions), len(incorrect_decisions)) if not blocked else None,
        "incorrect_answer_report_decision_count": len(incorrect_decisions) if not blocked else None,
        "report_decision_coverage": _ratio(sum(r.report_accepted is not None for r in attempted), len(attempted)),
        "workflow_pass_rate": _ratio(sum(r.workflow_passed is True for r in attempted), sum(r.workflow_passed is not None for r in attempted)),
        "workflow_observed_count": sum(r.workflow_passed is not None for r in attempted),
        "trusted_capture_complete_count": sum(r.trusted_tool_capture_complete for r in attempted),
        "trusted_receipt_known_subtotal": sum(r.trusted_receipt_count for r in attempted if r.trusted_receipt_count is not None) if any(r.trusted_receipt_count is not None for r in attempted) else None,
        "resources": {name: _measurement(attempted, name, accepted_count if not blocked else 0, raw_count if not blocked else 0) for name in RESOURCE_FIELDS},
        "inference_status": "blocked_reference_or_actor_binding" if blocked else "descriptive_reference_conditional",
        "outcomes": dict(Counter(_outcome(r) for r in records)), "weighting": WEIGHTING}


def _target_summary(records, key, condition, template_id, target, blocked, clusters=None):
    attempted = [r for r in records if r.attempted and r.template_id == template_id and target in r.target_reference_availability]
    eligible = [r for r in attempted if _target_value(r, target) is not None]
    grades = {r.attempt_id: next((g for g in r.per_target_results if g.target_id == target), None) for r in attempted}
    metric_names = sorted({name for grade in grades.values() if grade is not None for name in grade.metrics})
    metrics = {}
    for name in metric_names:
        def metric(record):
            grade = grades[record.attempt_id]
            return grade.metrics.get(name) if grade is not None and record.accepted_final else None
        present = [r for r in eligible if metric(r) is not None]
        metrics[name] = {"mean": _hierarchical(present, metric) if not blocked else None,
                         **_mcse(present, metric, clusters, blocked),
                         "available_count": len(present), "eligible_denominator": len(eligible),
                         "availability": _ratio(len(present), len(eligible))}
    refusal = [r for r in eligible if "structural_refusal" in r.target_reference_components[target]]
    return {**_labels(key), "condition": condition, "template_id": template_id, "target_id": target,
            "attempted_count": len(attempted), "reference_eligible_attempt_count": len(eligible) if not blocked else None,
            "unknown_reference_attempt_count": len(attempted)-len(eligible) if not blocked else None,
            "target_success_rate": _hierarchical(eligible, lambda r: _target_value(r, target)) if not blocked else None,
            "accepted_target_success_rate": _hierarchical(eligible, lambda r: _target_value(r, target, accepted=True)) if not blocked else None,
            "refusal_reference_count": len(refusal),
            "correct_refusal_rate": _hierarchical(refusal, lambda r: _target_value(r, target)) if not blocked else None,
            "metrics": metrics, "metric_scope": "available_outputs_only_with_explicit_target_reference_and_output_coverage"}


def _error_projection(record):
    """Bounded metadata projection; prediction/target bodies stay in the raw artifact."""
    return {"original_record": record.model_dump(mode="json", exclude={"safe_rows", "per_target_results"}),
            "original_record_content_sha256": content_sha256(record.model_dump(mode="json")),
            "original_record_hash_scope": "canonical_record_json",
            "raw_artifact_attempt_id": record.attempt_id,
            "omitted_safe_row_count": len(record.safe_rows),
            "omitted_prediction_scalar_count": sum(len(row.values) for row in record.safe_rows),
            "omitted_target_result_count": len(record.per_target_results)}


def _bind(records, plan):
    from .biology import load_biology_catalog
    from .biology_planning import schedule_biology_attempts
    expected = {attempt.attempt_id: attempt for attempt in schedule_biology_attempts(plan)}
    expected_thresholds = content_sha256(plan.thresholds.model_dump(mode="json"))
    catalog = load_biology_catalog()
    target_ids = {template.template_id: {target.target_id for target in template.targets} for template in catalog.templates}
    retained, errors, invalid = [], [], set()
    blocked = False
    for record in records:
        scheduled = expected.get(record.attempt_id)
        reason = ("foreign_study_or_plan" if record.study_id != plan.study_id or record.plan_identity != plan.sha256() else
                  "unknown_attempt_id" if scheduled is None else
                  "actor_slot_collision" if record.actor_type != "model_participant" else None)
        if reason:
            errors.append({"attempt_id": record.attempt_id, "code": reason, "disposition": "quarantined",
                           **_error_projection(record)})
            blocked = blocked or reason == "actor_slot_collision"
            continue
        binding = {name: getattr(scheduled, name) for name in (
            "study_id", "plan_identity", "task_identity", "cohort_id", "recipe_id", "template_id", "case_id", "condition", "repetition")}
        binding.update(role="evaluation", source_kind="synthetic")
        if record.attempted or record.thresholds_sha256 is not None:
            binding["thresholds_sha256"] = expected_thresholds
        wrong = [name for name, value in binding.items() if getattr(record, name) != value]
        if record.target_reference_availability and set(record.target_reference_availability) != target_ids[scheduled.template_id]:
            wrong.append("target_reference_universe")
            blocked = True
        if wrong:
            invalid.add(record.attempt_id)
            errors.append({"attempt_id": record.attempt_id, "code": "known_schedule_binding_corrupted",
                           "fields": wrong, "disposition": "counted_invalid_evidence_failure", "expected_binding": binding,
                           **_error_projection(record)})
            payload = record.model_dump(mode="json")
            payload.update(binding)
            payload.update(accepted_final=False, scientific_success=None, available_target_scientific_success=None,
                           report_accepted=False if record.attempted else None, workflow_passed=None, integrity_status="failed",
                           per_target_results=[], safe_rows=[], trusted_tool_capture_complete=False,
                           trusted_tool_calls=None, trusted_receipt_count=None, final_execution_tool_calls=None)
            # Preserve the pre-answer eligibility mask. Only its envelope hash
            # changes when binding the normalized failure to the plan's rules.
            if payload["target_reference_availability"]:
                payload["reference_availability_sha256"] = reference_availability_identity(payload)
            record = parse_biological_attempt(payload)
        retained.append(record)
    masks = defaultdict(set)
    for record in retained:
        if record.attempted:
            masks[record.cohort_id].add(record.reference_availability_sha256)
    conflicts = [cohort for cohort, identities in masks.items() if len(identities) > 1]
    for cohort in conflicts:
        errors.append({"cohort_id": cohort, "code": "reference_eligibility_conflict", "disposition": "inference_blocked"})
    threshold_ids = {record.thresholds_sha256 for record in retained if record.attempted}
    if len(threshold_ids) > 1:
        errors.append({"code": "scientific_threshold_configuration_conflict", "disposition": "inference_blocked"})
    return retained, errors, invalid, blocked or bool(conflicts) or len(threshold_ids) > 1


def _cohort_mean(rows, field):
    cases = defaultdict(list)
    for row in rows:
        if row[field] is not None:
            cases[(row["template_id"], row["case_id"])].append(row[field])
    templates = defaultdict(list)
    for (template, _), values in cases.items():
        templates[template].append(_mean(values))
    return _mean([_mean(values) for values in templates.values()])


def _interval(rows, field, *, replicates, seed, minimum):
    strata = defaultdict(lambda: defaultdict(list))
    cluster_strata = defaultdict(set)
    for row in rows:
        if row[field] is not None:
            stratum = (row["template_id"], row["case_id"])
            strata[stratum][row["independence_id"]].append(row)
            cluster_strata[row["independence_id"]].add(stratum)
    if len(cluster_strata) < minimum:
        return None, "insufficient_independent_cohorts"
    if not strata or any(len(groups) < 2 for groups in strata.values()):
        return None, "fewer_than_two_independent_cohorts_in_an_eligible_case"
    if any(len(assignment) > 1 for assignment in cluster_strata.values()):
        return None, "shared_independence_cluster_crosses_case_strata"
    if len({row[field] for row in rows if row[field] is not None}) < 2:
        return None, "no_observed_cluster_variation"
    import random
    rng = random.Random(seed)
    samples = []
    for _ in range(replicates):
        sampled = []
        for stratum in sorted(strata):
            groups = strata[stratum]
            keys = sorted(groups)
            sampled.extend(row for _ in keys for row in groups[rng.choice(keys)])
        samples.append(_cohort_mean(sampled, field))
    return [_percentile(samples, 0.025), _percentile(samples, 0.975)], None


def _pairs(records, key, left, right, plan, clusters, invalid, blocked, replicates, seed, minimum):
    result = {**_comparison_empty(left, right), **_labels(key), "status": "incomplete_pairing"}
    slots = defaultdict(list)
    for record in records:
        slots[(record.cohort_id, record.repetition, record.condition)].append(record)
    by_cohort = defaultdict(list)
    expected_pairs = 0
    secondary_missing = False
    for recipe in plan.recipes:
        if recipe.role != "evaluation":
            continue
        for repetition in range(1, plan.repetitions+1):
            expected_pairs += 1
            locator = {"cohort_id": recipe.recipe_id, "repetition": repetition}
            arms = [slots.get((recipe.recipe_id, repetition, condition), []) for condition in (left, right)]
            if any(len(arm) != 1 for arm in arms):
                result["problems"].append({"code": "missing_or_duplicate_counterpart", **locator})
                continue
            a, b = arms[0][0], arms[1][0]
            if a.attempt_id in invalid or b.attempt_id in invalid or not a.attempted or not b.attempted:
                result["problems"].append({"code": "counterpart_not_attempted_or_invalid_evidence", **locator})
                continue
            fields = ("input_sha256", "source_sha256", "task_identity", "shared_runtime_sha256", "reference_availability_sha256", "independence_id")
            if any(getattr(a, name) is None or getattr(a, name) != getattr(b, name) for name in fields):
                result["problems"].append({"code": "shared_task_input_runtime_or_reference_mismatch", **locator})
                continue
            if (not a.model_identity_verified or not b.model_identity_verified or a.actual_model is None
                    or a.actual_model != b.actual_model or a.model_identity_sha256 != b.model_identity_sha256):
                result["problems"].append({"code": "observed_model_identity_not_verified", **locator})
                continue
            av, bv = _task_value(a), _task_value(b)
            if av is None or bv is None:
                result["unknown_reference_pair_count"] += 1
            else:
                result["eligible_pair_count"] += 1
            aa, ba = _available_value(a), _available_value(b)
            secondary_missing = secondary_missing or a.report_accepted is None or b.report_accepted is None
            by_cohort[recipe.recipe_id].append({"repetition": repetition,
                "left_attempt_id": a.attempt_id, "right_attempt_id": b.attempt_id,
                "raw_delta": av-bv if av is not None and bv is not None else None,
                "accepted_delta": _task_value(a, accepted=True)-_task_value(b, accepted=True) if av is not None and bv is not None else None,
                "available_delta": aa-ba if aa is not None and ba is not None else None,
                "independence_id": clusters[a.independence_id]})
    recipes = {recipe.recipe_id: recipe for recipe in plan.recipes}
    for cohort, repetitions in sorted(by_cohort.items()):
        identities = {row["independence_id"] for row in repetitions}
        if len(identities) != 1:
            result["problems"].append({"code": "cohort_independence_changed", "cohort_id": cohort})
            continue
        result["cohort_deltas"].append({"cohort_id": cohort, "case_id": recipes[cohort].case_id,
            "template_id": recipes[cohort].template_id, "independence_id": next(iter(identities)),
            "raw_delta": _mean([r["raw_delta"] for r in repetitions if r["raw_delta"] is not None]),
            "accepted_delta": _mean([r["accepted_delta"] for r in repetitions if r["accepted_delta"] is not None]),
            "available_delta": _mean([r["available_delta"] for r in repetitions if r["available_delta"] is not None]),
            "paired_repetition_count": len(repetitions), "repetitions": repetitions})
    rows = result["cohort_deltas"]
    result.update(paired_repetition_count=sum(r["paired_repetition_count"] for r in rows), paired_cohort_count=len(rows),
                  independent_group_count=len({r["independence_id"] for r in rows if r["raw_delta"] is not None}),
                  mean_delta=_cohort_mean(rows, "raw_delta") if not blocked else None,
                  accepted_scientific_mean_delta=_cohort_mean(rows, "accepted_delta") if not blocked else None,
                  available_target_mean_delta=_cohort_mean(rows, "available_delta") if not blocked else None)
    result["pairing_coverage"] = _ratio(result["paired_repetition_count"], expected_pairs)
    if blocked:
        result.update(eligible_pair_count=None, unknown_reference_pair_count=None, independent_group_count=None)
        for cohort in rows:
            for field in ("raw_delta", "accepted_delta", "available_delta"):
                cohort[field] = None
            for repetition in cohort["repetitions"]:
                for field in ("raw_delta", "accepted_delta", "available_delta"):
                    repetition[field] = None
    reason = ("reference_or_actor_binding_blocks_inference" if blocked else "incomplete_pairing" if result["problems"] or result["pairing_coverage"] != 1 else None)
    if reason:
        result.update(interval_reason=reason, accepted_scientific_interval_reason=reason, status="descriptive_only")
    else:
        interval, reason = _interval(rows, "raw_delta", replicates=replicates, seed=seed, minimum=minimum)
        accepted_interval, accepted_reason = (None, "report_decision_unavailable") if secondary_missing else _interval(
            rows, "accepted_delta", replicates=replicates, seed=seed, minimum=minimum)
        result.update(confidence_interval=interval, interval_reason=reason,
                      accepted_scientific_confidence_interval=accepted_interval,
                      accepted_scientific_interval_reason=accepted_reason,
                      status="paired_estimate" if interval is not None else "descriptive_only")
    from .study_precision import stratified_mcse
    result['precision'] = ({'monte_carlo_se':None,'reason':result['interval_reason']}
        if blocked or result['problems'] or result['pairing_coverage']!=1 else
        stratified_mcse([{**row,'value':row['raw_delta']} for row in rows]))
    return result


def analyze_biology_attempts(records, plan: BiologyStudyPlan, *, allow_result_analysis=False,
                             bootstrap_replicates=2000, bootstrap_seed=20260911,
                             minimum_independent_cohorts=10) -> dict[str, Any]:
    """Finite public coordinator report; nonempty analysis is denied by default.

    Malformed and duplicate typed records fail closed before aggregation. Their
    fixed reference eligibility cannot be inferred safely from untrusted input.
    """
    if not isinstance(records, (list, tuple)):
        raise TypeError("records must be a bounded list or tuple, not a lazy data reader")
    if records and allow_result_analysis is not True:
        raise PermissionError("Nonempty biology analysis requires allow_result_analysis=True")
    if type(allow_result_analysis) is not bool or not isinstance(plan, BiologyStudyPlan):
        raise ValueError("A validated BiologyStudyPlan and literal boolean permission are required")
    if type(bootstrap_replicates) is not int or not 1 <= bootstrap_replicates <= 100_000:
        raise ValueError("bootstrap_replicates must be an integer from 1 to 100000")
    if type(bootstrap_seed) is not int or not 0 <= bootstrap_seed < 2**63:
        raise ValueError("bootstrap_seed must be a bounded nonnegative integer")
    if type(minimum_independent_cohorts) is not int or not 10 <= minimum_independent_cohorts <= 100_000:
        raise ValueError("At least ten independent cohorts are required for empirical uncertainty")
    result = _empty(plan, bootstrap_replicates, bootstrap_seed, minimum_independent_cohorts)
    if not records:
        return result
    if len(records) > 100_000:
        raise ValueError("Biological attempt record count exceeds the bounded analysis limit")
    parsed = [parse_biological_attempt(record) for record in records]
    if len({record.attempt_id for record in parsed}) != len(parsed):
        raise ValueError("Duplicate biology attempt IDs are rejected before aggregation")
    current, errors, invalid, blocked = _bind(parsed, plan)
    result.update(status="observed_records" if current else "no_this_study_records", received_record_count=len(parsed),
                  record_count=len(current), attempted_count=sum(r.attempted for r in current), error_rows=errors,
                  quarantined_record_count=sum(e["disposition"] == "quarantined" for e in errors),
                  invalid_evidence_record_count=len(invalid), scientific_inference_blocked=blocked)
    if not current:
        validate_finite_json(result)
        return result
    clusters, cluster_problems = _independence_clusters(current)
    result["problems"].extend(cluster_problems)
    grouped = defaultdict(list)
    for record in current:
        grouped[_key(record)].append(record)
    for key, group in sorted(grouped.items(), key=lambda pair: canonical_json_bytes(pair[0])):
        for condition in plan.conditions:
            arm = [r for r in group if r.condition == condition]
            if not arm:
                continue
            result["group_summaries"].append(_group(arm, key, condition, blocked=blocked, clusters=clusters))
            target_keys = {(r.template_id, target) for r in arm for target in r.target_reference_availability}
            result["target_summaries"].extend(_target_summary(arm, key, condition, template, target, blocked, clusters)
                                               for template, target in sorted(target_keys))
            for case in sorted({r.case_id for r in arm}):
                subset = [r for r in arm if r.case_id == case]
                result["case_summaries"].append({**_group(subset, key, condition, blocked=blocked, clusters=clusters),
                                                  "case_id": case, "template_id": subset[0].template_id})
        for index, (left, right) in enumerate(CONTRASTS):
            comparison = _pairs(group, key, left, right, plan, clusters, invalid, blocked,
                                bootstrap_replicates, bootstrap_seed, minimum_independent_cohorts)
            result["paired_comparisons"][index]["groups"].append(comparison)
            result["bootstrap"]["performed"] |= comparison["confidence_interval"] is not None or comparison["accepted_scientific_confidence_interval"] is not None
    result["headline"]["groups"] = [{name: group[name] for name in (
        "source_kind", "actual_model", "condition", "task_success_rate", "accepted_scientific_success_rate",
        "available_target_success_rate", "primary_eligible_attempt_count", "unknown_reference_attempt_count")}
        for group in result["group_summaries"] if group["actor_type"] == "model_participant"]
    for comparison in result["paired_comparisons"]:
        if len(comparison["groups"]) == 1:
            comparison.update({name: value for name, value in comparison["groups"][0].items() if name != "groups"})
        else:
            comparison["status"] = "separate_source_and_model_groups"
    result["outcome_taxonomy"] = dict(Counter(_outcome(r) for r in current))
    exported_scalars = 0
    for record in current:
        row = record.model_dump(mode="json", exclude={"safe_rows", "per_target_results"})
        row.update(primary_task_success=None if blocked else _task_value(record),
                   accepted_scientific_success=None if blocked else _task_value(record, accepted=True),
                   outcome=_outcome(record), binding_status="invalid_evidence_expected_slot" if record.attempt_id in invalid else "verified_schedule_binding")
        result["run_rows"].append(row)
        exported_scalars += sum(len(row.values) for row in record.safe_rows)
        validate_biology_export_budget(row_count=len(result["safe_rows"])+len(record.safe_rows),
                                       scalar_count=exported_scalars)
        result["safe_rows"].extend({"attempt_id": record.attempt_id, **safe.model_dump(mode="json")} for safe in record.safe_rows)
        result["target_rows"].extend({"attempt_id": record.attempt_id, **grade.model_dump(mode="json")} for grade in record.per_target_results)
    validate_finite_json(result)
    return result
