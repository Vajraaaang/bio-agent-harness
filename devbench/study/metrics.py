"""Coordinator-only descriptive summaries and paired cluster comparisons.

The empty path is plan metadata only: no bootstrap, data reads, numeric-library
imports, or manufactured performance. Nonempty evaluation is future-use code and
must not run until data evaluation is authorized.

The primary estimand is raw scientific success against the hidden reference.
Accepted scientific success (correctness AND report acceptance) is a separate
secondary outcome; report rejection never changes raw scientific correctness.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from itertools import islice
import math
from typing import Any, Iterable

from .contracts import StudyPlan, task_identity
from .records import AttemptRecord, parse_attempt_record, validate_finite_json

RESOURCE_FIELDS = ("elapsed_sec", "RSS_MB", "tokens", "cost")
PANELS = {
    "recovery": ("signed_recall",),
    "accuracy": ("precision", "fdp", "directional_error_rate", "shape_macro_f1", "effect_mae", "effect_rmse", "curve_rmse"),
    "calibration": ("interval_coverage", "interval_mean_width", "bh_arithmetic_valid"),
    "diagnostic": ("required_diagnostics_correct", "incorrect_structural_refusal", "unsupported_population_claim"),
    "resource": RESOURCE_FIELDS,
    "workflow": ("workflow_passed", "report_accepted", "trusted_tool_capture_complete", "trusted_tool_calls"),
}
SUCCESS_FIELDS = (
    "task_success_rate", "raw_task_success_rate", "scientific_success_count",
    "accepted_scientific_success_rate", "accepted_scientific_success_count",
    "full_denominator_accepted_scientific_success_rate",
    "raw_scientific_success_rate", "raw_scientific_success_count",
    "full_denominator_raw_scientific_success_rate",
    "correct_answer_rejection_count", "correct_answer_rejection_rate",
    "incorrect_answer_acceptance_count", "incorrect_answer_acceptance_rate",
)


def _mean(values):
    if not values:
        return None
    scale = max(abs(value) for value in values)
    return scale * (math.fsum(value/scale for value in values)/len(values)) if scale else 0.0


def _ratio(numerator, denominator):
    return numerator/denominator if denominator else None


def _empty_comparison(left, right, primary):
    return {
        "contrast": f"{left}-{right}", "primary": primary,
        "status": "not_run", "mean_delta": None, "confidence_interval": None,
        "estimand": "raw_scientific_success", "primary_outcome": "raw_scientific_success",
        "raw_scientific_estimand_role": "primary_outcome",
        "raw_scientific_mean_delta": None, "raw_scientific_confidence_interval": None,
        "raw_scientific_interval_reason": "no_attempts",
        "accepted_scientific_estimand_role": "secondary_descriptive_with_unadjusted_interval",
        "accepted_scientific_mean_delta": None, "accepted_scientific_confidence_interval": None,
        "accepted_scientific_interval_reason": "no_attempts", "accepted_scientific_problems": [],
        "interval_reason": "no_attempts", "paired_repetition_count": 0,
        "paired_cohort_count": 0, "independent_group_count": 0,
        "pairing_coverage": None, "cohort_deltas": [], "problems": [], "groups": [],
        "superiority_inference": "unavailable",
    }


def _empty_summary(plan, replicates, seed):
    return {
        "record_format": "devbench.study.summary/1", "study_id": plan.study_id,
        "primary_outcome": "raw_scientific_success",
        "plan_identity": plan.sha256(), "status": "not_run",
        "planned_attempt_count": len(plan.cohorts)*plan.repetitions*len(plan.conditions),
        "planned_model_attempt_count": len(plan.cohorts)*plan.repetitions*len(plan.conditions),
        "planned_control_attempt_count": len(getattr(plan, "scripted_controls", ())),
        "record_count": 0, "attempted_count": 0,
        "received_record_count": 0, "quarantined_record_count": 0,
        "invalid_evidence_record_count": 0, "error_rows": [], "headline_inference_blocked": False,
        "headline": {"groups": [], **{name: None for name in SUCCESS_FIELDS},
                     "completion_rate": None, "primary_estimand": "raw_scientific_success",
                     "primary_outcome": "raw_scientific_success",
                     "fdr": None, "cost_total": None, "cost_per_success": None,
                     "cost_per_accepted_scientific_success": None,
                     "scope": "model_participants_separated_by_source_and_observed_model"},
        "group_summaries": [],
        "paired_comparisons": [_empty_comparison(*plan.primary_comparison, True)] + [
            _empty_comparison(*contrast, False) for contrast in plan.exploratory_comparisons],
        "outcome_taxonomy": {}, "panels": {name: {"status": "not_run", "groups": []} for name in PANELS},
        "run_rows": [], "gene_rows": [], "problems": [],
        "bootstrap": {"performed": False, "replicates": replicates, "seed": seed,
                      "unit": "independence_id_cluster_preserving_paired_repetitions",
                      "weighting": "mean_repetitions_then_equal_cohorts_within_scenario_then_equal_scenarios",
                      "interval_method": "stratified_cluster_percentile", "confidence_level": plan.thresholds.confidence_level},
        "limitations": [
            "Research thresholds are provisional and uncalibrated.",
            "Public observations and scripted controls do not establish model performance on latent synthetic truth.",
            "No clinical validity, authenticated approval, or causal biological validation is established.",
        ],
    }


def _model_label(record):
    if record.actor_type == "scripted_control":
        return "scripted_control"
    return record.actual_model or "unknown_model"


def _group_key(record):
    return record.actor_type, record.source_kind, _model_label(record), record.model_identity_verified


def _outcome(record):
    if not record.attempted:
        return "blocked_before_attempt" if record.completion_status == "blocked" else "not_started"
    if record.integrity_status == "failed":
        return "integrity_failure"
    if record.completion_status != "completed":
        return record.completion_status
    if not record.accepted_final:
        return "completed_without_accepted_final"
    if record.scientific_success is True:
        return ("accepted_scientific_success" if record.report_accepted is True else
                "correct_answer_rejected" if record.report_accepted is False else
                "correct_answer_report_decision_unavailable")
    if record.scientific_success is False:
        return ("incorrect_answer_accepted" if record.report_accepted is True else
                "incorrect_answer_rejected" if record.report_accepted is False else
                "incorrect_answer_report_decision_unavailable")
    return "scientific_outcome_unavailable"


def _success_value(record):
    # Every attempted run remains in the primary synthetic task-success
    # denominator, including provider failures, timeout, and invalid integrity.
    return float(record.scientific_success is True)


def _raw_scientific_value(record):
    return float(record.scientific_success is True)


def _accepted_scientific_value(record):
    return float(record.scientific_success is True and record.report_accepted is True)


def _standardized(records, value):
    """Mean observed repetitions, equal cohorts, then equal observed scenarios."""
    cells = defaultdict(list)
    for record in records:
        observed = value(record)
        if observed is not None:
            cells[(record.scenario, record.cohort_id)].append(observed)
    scenarios = defaultdict(list)
    for (scenario, _cohort), values in cells.items():
        scenarios[scenario].append(_mean(values))
    return _mean([_mean(values) for values in scenarios.values()])


def _measurement(records, name, accepted_successes, raw_successes):
    known = [getattr(record, name) for record in records if getattr(record, name) is not None]
    complete_count = sum(getattr(record.accounting_completeness, name) == "complete" for record in records)
    complete = bool(records) and complete_count == len(records)
    aggregation_error = None
    try:
        subtotal = math.fsum(known) if known else None
    except (OverflowError, ValueError):
        subtotal = None
        aggregation_error = "finite_measurement_sum_unrepresentable"
    return {"total": subtotal if complete else None, "known_subtotal": subtotal,
            "available_count": len(known), "complete_count": complete_count,
            "denominator": len(records), "coverage": _ratio(len(known), len(records)),
            "per_success": subtotal/raw_successes if complete and raw_successes and subtotal is not None else None,
            "per_success_estimand": "raw_scientific_success",
            "per_raw_scientific_success": subtotal/raw_successes if complete and raw_successes and subtotal is not None else None,
            "per_accepted_scientific_success": subtotal/accepted_successes if complete and accepted_successes and subtotal is not None else None,
            "aggregation_error": aggregation_error,
            "status": "numerically_unavailable" if aggregation_error else "complete" if complete else "partial" if known else "unavailable"}


def _metric_value(record, name):
    if name not in {"workflow_passed", "report_accepted", "trusted_tool_capture_complete", "trusted_tool_calls"} and (
            not record.accepted_final or record.integrity_status != "verified"):
        return None
    if name in record.biological_metrics:
        value = record.biological_metrics[name]
    elif name in record.diagnostic_outcomes:
        value = record.diagnostic_outcomes[name]
    elif name in record.refusal_outcomes:
        value = record.refusal_outcomes[name]
    elif name in {"workflow_passed", "report_accepted", "trusted_tool_capture_complete", "trusted_tool_calls"}:
        value = getattr(record, name)
    else:
        return None
    return float(value) if type(value) in (float, int, bool) else None


def _summarize_group(records, key, condition):
    from .execution_usage import tool_usage_metrics
    attempted = [record for record in records if record.attempted]
    raw_successes = sum(record.scientific_success is True for record in attempted)
    successes = sum(record.scientific_success is True and record.report_accepted is True for record in attempted)
    correct_decisions = [record for record in attempted if record.scientific_success is True and record.report_accepted is not None]
    incorrect_decisions = [record for record in attempted if record.scientific_success is False and record.report_accepted is not None]
    rejected_correct = sum(record.report_accepted is False for record in correct_decisions)
    accepted_incorrect = sum(record.report_accepted is True for record in incorrect_decisions)
    completed = sum(record.completion_status == "completed" for record in attempted)
    actor, source, model, verified = key
    performance_available = source == "synthetic" and bool(attempted)
    resources = {name: _measurement(attempted, name, successes, raw_successes) for name in RESOURCE_FIELDS}
    fdp_records = [r for r in attempted if r.accepted_final and _metric_value(r, "fdp") is not None]
    datasets = defaultdict(list)
    for record in fdp_records:
        datasets[(record.scenario, record.cohort_id)].append(_metric_value(record, "fdp"))
    metrics = {}
    for names in PANELS.values():
        for name in names:
            if name in RESOURCE_FIELDS:
                continue
            available = [r for r in attempted if _metric_value(r, name) is not None]
            metrics[name] = {"mean": _standardized(available, lambda r: _metric_value(r, name)),
                             "available_count": len(available), "denominator": len(attempted),
                             "coverage": _ratio(len(available), len(attempted))}
    return {
        **tool_usage_metrics(records),
        "actor_type": actor, "source_kind": source, "actual_model": model, "condition": condition,
        "model_identity_verified": verified, "record_count": len(records), "attempted_count": len(attempted),
        "completion_count": completed, "accepted_final_count": sum(r.accepted_final for r in attempted),
        "completion_rate": _ratio(completed, len(attempted)),
        "scientific_success_count": raw_successes if performance_available else None,
        "task_success_rate": _standardized(attempted, _success_value) if performance_available else None,
        "raw_task_success_rate": _ratio(raw_successes, len(attempted)) if performance_available else None,
        "accepted_scientific_success_rate": _standardized(attempted, _accepted_scientific_value) if performance_available else None,
        "accepted_scientific_success_count": successes if performance_available else None,
        "full_denominator_accepted_scientific_success_rate": _ratio(successes, len(attempted)) if performance_available else None,
        "raw_scientific_success_rate": _standardized(attempted, _raw_scientific_value) if performance_available else None,
        "raw_scientific_success_count": raw_successes if performance_available else None,
        "full_denominator_raw_scientific_success_rate": _ratio(raw_successes, len(attempted)) if performance_available else None,
        "primary_estimand": "raw_scientific_success", "primary_outcome": "raw_scientific_success",
        "legacy_field_scope": "task_success_rate_is_standardized_raw_correctness_raw_task_success_rate_is_unweighted_raw_correctness",
        "correct_answer_rejection_count": rejected_correct if performance_available else None,
        "correct_answer_rejection_rate": _ratio(rejected_correct, len(correct_decisions)) if performance_available else None,
        "correct_answer_report_decision_count": len(correct_decisions),
        "incorrect_answer_acceptance_count": accepted_incorrect if performance_available else None,
        "incorrect_answer_acceptance_rate": _ratio(accepted_incorrect, len(incorrect_decisions)) if performance_available else None,
        "incorrect_answer_report_decision_count": len(incorrect_decisions),
        "report_decision_available_count": sum(r.report_accepted is not None for r in attempted),
        "report_decision_coverage": _ratio(sum(r.report_accepted is not None for r in attempted), len(attempted)),
        "success_denominator": "all_attempted_runs_including_unsuccessful_and_unscorable_attempts",
        "standardization_scope": "observed_cohorts_and_scenarios_only_pairing_coverage_reported_separately",
        "fdr": _standardized(fdp_records, lambda r: _metric_value(r, "fdp")),
        "fdr_scope": "mean_dataset_FDP_among_calculable_outputs_repetitions_averaged_within_dataset",
        "fdr_available_outputs": len(fdp_records), "fdr_available_datasets": len(datasets),
        "fdr_availability": _ratio(len(fdp_records), len(attempted)),
        "independence_scope": "coordinator_cluster_ids_with_reused_input_hashes_coalesced_not_biological_validation",
        "outcomes": dict(sorted(Counter(_outcome(r) for r in records).items())),
        "resources": resources, "metrics": metrics,
    }


def _independence_clusters(records):
    """Merge claimed groups sharing input bytes; reused matrices cannot add n."""
    parent = {r.independence_id: r.independence_id for r in records}

    def root(key):
        while parent[key] != key:
            parent[key] = parent[parent[key]]
            key = parent[key]
        return key

    owners = {}
    problems = []
    for record in records:
        if record.input_sha256 is None:
            continue
        prior = owners.setdefault(record.input_sha256, record.independence_id)
        if root(prior) != root(record.independence_id):
            a, b = sorted((root(prior), root(record.independence_id)))
            parent[b] = a
            problems.append({"code": "reused_input_claimed_as_distinct_independence_groups",
                             "attempt_id": record.attempt_id})
    return {name: root(name) for name in parent}, problems


def _percentile(values, probability):
    ordered = sorted(values)
    position = (len(ordered)-1)*probability
    lower = int(position)
    upper = min(lower+1, len(ordered)-1)
    return ordered[lower]+(ordered[upper]-ordered[lower])*(position-lower)


def _cluster_interval(cohort_deltas, plan, replicates, seed, value_key="mean_delta"):
    """Resample independent groups within scenario; retain every paired repeat."""
    groups = defaultdict(lambda: defaultdict(list))
    cluster_scenarios = defaultdict(set)
    for row in cohort_deltas:
        groups[row["scenario"]][row["independence_id"]].append(row[value_key])
        cluster_scenarios[row["independence_id"]].add(row["scenario"])
    n = len(cluster_scenarios)
    if n < plan.thresholds.minimum_independent_cohorts:
        return None, "insufficient_independent_groups", n
    if set(groups) != set(plan.scenario_families) or any(len(g) < 2 for g in groups.values()):
        return None, "inadequate_scenario_strata", n
    if any(len(scenarios) != 1 for scenarios in cluster_scenarios.values()):
        return None, "independence_cluster_crosses_scenario_strata", n
    if len({row[value_key] for row in cohort_deltas}) < 2:
        return None, "no_observed_cluster_variation_for_empirical_interval", n
    import random
    rng = random.Random(seed)
    samples = []
    for _ in range(replicates):
        scenario_values = []
        for scenario in plan.scenario_families:
            clusters = groups[scenario]
            labels = sorted(clusters)
            sampled = [rng.choice(labels) for _ in labels]
            # Preserve cohort multiplicity when a reused-input cluster contains
            # multiple coordinator cohorts; all repetitions were averaged once.
            values = [value for cluster in sampled for value in clusters[cluster]]
            scenario_values.append(_mean(values))
        samples.append(_mean(scenario_values))
    tail = (1-plan.thresholds.confidence_level)/2
    return [_percentile(samples, tail), _percentile(samples, 1-tail)], None, n


def _comparison(records, key, left, right, plan, clusters, excluded, replicates, seed, block_inference=False):
    result = _empty_comparison(left, right, (left, right) == plan.primary_comparison)
    actor, source, model, verified = key
    result.update(actor_type=actor, source_kind=source, actual_model=model, model_identity_verified=verified,
                  status="incomplete_pairing")
    slots = defaultdict(list)
    for record in records:
        if record.condition in {left, right}:
            slots[(record.cohort_id, record.repetition, record.condition)].append(record)
    problems = result["problems"]
    pairs = defaultdict(list)
    cohort_map = {cohort.cohort_id: cohort for cohort in plan.cohorts}
    recipe_map = {recipe.recipe_id: recipe for recipe in plan.dataset_recipes}
    relevant_cohorts = [c for c in plan.cohorts if recipe_map[c.recipe_id].source_kind == source]
    expected = len(relevant_cohorts)*plan.repetitions
    for cohort in relevant_cohorts:
        for repetition in range(1, plan.repetitions+1):
            a_list = slots.get((cohort.cohort_id, repetition, left), [])
            b_list = slots.get((cohort.cohort_id, repetition, right), [])
            locator = {"cohort_id": cohort.cohort_id, "repetition": repetition}
            if len(a_list) > 1 or len(b_list) > 1:
                problems.append({"code": "duplicate_condition_slot", **locator})
                continue
            if not a_list or not b_list:
                problems.append({"code": "missing_counterpart", **locator})
                continue
            a, b = a_list[0], b_list[0]
            if a.attempt_id in excluded or b.attempt_id in excluded:
                problems.append({"code": "invalid_plan_binding", **locator})
                continue
            if not a.attempted or not b.attempted:
                problems.append({"code": "counterpart_not_attempted", **locator})
                continue
            fields = ("input_sha256", "task_identity", "shared_environment_sha256", "independence_id")
            mismatch = [name for name in fields if getattr(a, name) is None or getattr(a, name) != getattr(b, name)]
            if mismatch:
                problems.append({"code": "paired_identity_mismatch", "fields": mismatch, **locator})
                continue
            if actor == "model_participant" and (not verified or not a.model_identity_verified or not b.model_identity_verified
                                                  or a.actual_model is None or a.actual_model != b.actual_model):
                problems.append({"code": "observed_model_identity_not_verified", **locator})
                continue
            if source != "synthetic":
                problems.append({"code": "primary_scientific_truth_unavailable", **locator})
                continue
            if a.report_accepted is None or b.report_accepted is None:
                result["accepted_scientific_problems"].append({"code": "report_acceptance_decision_unavailable", **locator})
            pairs[cohort.cohort_id].append({"repetition": repetition,
                "left_attempt_id": a.attempt_id, "right_attempt_id": b.attempt_id,
                "delta": _success_value(a)-_success_value(b),
                "raw_scientific_delta": _raw_scientific_value(a)-_raw_scientific_value(b),
                "accepted_scientific_delta": _accepted_scientific_value(a)-_accepted_scientific_value(b),
                "independence_id": clusters[a.independence_id]})
    cohort_deltas = []
    for cohort_id, repetitions in sorted(pairs.items()):
        identities = {pair["independence_id"] for pair in repetitions}
        if len(identities) != 1:
            problems.append({"code": "cohort_independence_id_changes_between_repetitions", "cohort_id": cohort_id})
            continue
        cohort_deltas.append({"cohort_id": cohort_id, "scenario": cohort_map[cohort_id].scenario,
                              "independence_id": next(iter(identities)), "mean_delta": _mean([r["delta"] for r in repetitions]),
                              "raw_scientific_mean_delta": _mean([r["raw_scientific_delta"] for r in repetitions]),
                              "accepted_scientific_mean_delta": _mean([r["accepted_scientific_delta"] for r in repetitions]),
                              "paired_repetition_count": len(repetitions), "repetitions": repetitions})
    result["cohort_deltas"] = cohort_deltas
    result["paired_repetition_count"] = sum(r["paired_repetition_count"] for r in cohort_deltas)
    result["paired_cohort_count"] = len(cohort_deltas)
    result["pairing_coverage"] = _ratio(result["paired_repetition_count"], expected)
    result["independent_group_count"] = len({r["independence_id"] for r in cohort_deltas})
    if not cohort_deltas:
        result["interval_reason"] = "no_compatible_pairs"
        result["raw_scientific_interval_reason"] = "no_compatible_pairs"
        result["accepted_scientific_interval_reason"] = "no_compatible_pairs"
        return result
    by_scenario = defaultdict(list)
    raw_by_scenario = defaultdict(list)
    accepted_by_scenario = defaultdict(list)
    for row in cohort_deltas:
        by_scenario[row["scenario"]].append(row["mean_delta"])
        raw_by_scenario[row["scenario"]].append(row["raw_scientific_mean_delta"])
        accepted_by_scenario[row["scenario"]].append(row["accepted_scientific_mean_delta"])
    result["mean_delta"] = _mean([_mean(values) for values in by_scenario.values()])
    result["raw_scientific_mean_delta"] = _mean([_mean(values) for values in raw_by_scenario.values()])
    result["accepted_scientific_mean_delta"] = _mean([_mean(values) for values in accepted_by_scenario.values()])
    if result["pairing_coverage"] != 1 or problems:
        result["interval_reason"] = "incomplete_or_mismatched_pairing"
        result["raw_scientific_interval_reason"] = "incomplete_or_mismatched_pairing"
        result["accepted_scientific_interval_reason"] = "incomplete_or_mismatched_pairing"
        result["status"] = "descriptive_matched_subset_only"
        return result
    if actor != "model_participant":
        result["status"] = "scripted_control_descriptive_only"
        result["interval_reason"] = "scripted_controls_are_not_model_participants"
        result["raw_scientific_interval_reason"] = "scripted_controls_are_not_model_participants"
        result["accepted_scientific_interval_reason"] = "scripted_controls_are_not_model_participants"
        return result
    if block_inference:
        result.update(interval_reason="actor_slot_collision_blocks_inference",
                      raw_scientific_interval_reason="actor_slot_collision_blocks_inference",
                      accepted_scientific_interval_reason="actor_slot_collision_blocks_inference",
                      status="descriptive_only_invalid_actor_binding")
        return result
    interval, reason, n = _cluster_interval(cohort_deltas, plan, replicates, seed)
    accepted_interval, accepted_reason = None, "report_acceptance_decision_unavailable"
    if not result["accepted_scientific_problems"]:
        accepted_interval, accepted_reason, _ = _cluster_interval(cohort_deltas, plan, replicates, seed,
                                                                 value_key="accepted_scientific_mean_delta")
    result.update(confidence_interval=interval, interval_reason=reason, independent_group_count=n,
                  raw_scientific_confidence_interval=interval, raw_scientific_interval_reason=reason,
                  accepted_scientific_confidence_interval=accepted_interval, accepted_scientific_interval_reason=accepted_reason,
                  status="paired_estimate" if interval is not None else "descriptive_insufficient_independence")
    if interval is not None:
        result["superiority_inference"] = (
            "primary_interval_above_zero" if result["primary"] and interval[0] > 0 else
            "exploratory_unadjusted_interval_only" if not result["primary"] else "primary_interval_not_above_zero")
    return result


def _bind_to_schedule(records, plan):
    """Quarantine foreign/unknown IDs; retain known corrupt executions as failures.

    Original records remain intact in explicit error rows. Scripted controls have
    their own registered IDs and may never borrow a model participant's slot.
    """
    from .planning import schedule_attempts
    cohorts = {cohort.cohort_id: cohort for cohort in plan.cohorts}
    recipes = {recipe.recipe_id: recipe for recipe in plan.dataset_recipes}
    plan_hash = plan.sha256()
    expected = {}
    for attempt in schedule_attempts(plan):
        expected[attempt.attempt_id] = {
            "study_id": plan.study_id, "plan_identity": plan_hash, "task_identity": attempt.task_identity,
            "cohort_id": attempt.cohort_id, "scenario": attempt.scenario,
            "condition": attempt.condition, "repetition": attempt.repetition,
            "source_kind": recipes[attempt.recipe_id].source_kind, "actor_type": "model_participant",
        }
    if getattr(plan, "scripted_controls", ()):
        from .contracts import control_attempt_id
        for control in plan.scripted_controls:
            control_id = control_attempt_id(plan, control)
            if control_id in expected:
                raise ValueError("Registered control/model attempt ID collision")
            cohort = cohorts[control.cohort_id]
            expected[control_id] = {
                "study_id": plan.study_id, "plan_identity": plan_hash, "task_identity": task_identity(plan, cohort),
                "cohort_id": cohort.cohort_id, "scenario": cohort.scenario,
                "condition": control.condition, "repetition": control.repetition,
                "source_kind": recipes[cohort.recipe_id].source_kind, "actor_type": "scripted_control",
            }
    retained, errors, invalid_ids = [], [], set()
    inference_blocked = False
    for record in records:
        binding = expected.get(record.attempt_id)
        reason = None
        if record.study_id != plan.study_id or record.plan_identity != plan_hash:
            reason = "foreign_study_or_plan"
        elif binding is None:
            reason = "unknown_scheduled_attempt_id"
        elif record.actor_type != binding["actor_type"]:
            reason = "actor_type_collides_with_registered_slot"
            inference_blocked = True
        if reason:
            errors.append({"code": reason, "attempt_id": record.attempt_id, "disposition": "quarantined",
                           "original_record": record.model_dump(mode="json"), "expected_binding": binding})
            continue
        mismatched = [key for key, value in binding.items() if getattr(record, key) != value]
        if not mismatched:
            retained.append(record)
            continue
        invalid_ids.add(record.attempt_id)
        errors.append({"code": "known_attempt_binding_corrupted", "attempt_id": record.attempt_id,
                       "fields": mismatched, "disposition": "counted_invalid_evidence_failure",
                       "original_record": record.model_dump(mode="json"), "expected_binding": binding})
        corrected = record.model_dump(mode="json")
        corrected.update(binding)
        corrected.update(accepted_final=False, scientific_success=None,
                         report_accepted=False if record.attempted else None, workflow_passed=None,
                         integrity_status="failed", biological_metrics={}, metric_missingness={},
                         diagnostic_outcomes={}, refusal_outcomes={}, gene_rows=[],
                         trusted_tool_capture_complete=False, trusted_tool_calls=None, final_execution_tool_calls=None)
        retained.append(parse_attempt_record(corrected))
    return retained, errors, invalid_ids, inference_blocked


def analyze_attempts(records: Iterable[AttemptRecord | dict[str, Any]], plan: StudyPlan, *,
                     bootstrap_replicates: int = 2000, bootstrap_seed: int = 20260911) -> dict:
    """Return finite coordinator JSON; the default-empty path performs no analysis."""
    if not isinstance(plan, StudyPlan):
        raise ValueError("A validated StudyPlan is required")
    if type(bootstrap_replicates) is not int or not 1 <= bootstrap_replicates <= 100_000:
        raise ValueError("bootstrap_replicates must be an integer between 1 and 100000")
    if type(bootstrap_seed) is not int or not 0 <= bootstrap_seed < 2**63:
        raise ValueError("bootstrap_seed must be a nonnegative bounded integer")
    raw = list(islice(records, 100_001))
    summary = _empty_summary(plan, bootstrap_replicates, bootstrap_seed)
    if not raw:
        return summary
    if len(raw) > 100_000:
        raise ValueError("Attempt record count exceeds the bounded analysis limit")
    validated = [parse_attempt_record(record) for record in raw]
    ids = [record.attempt_id for record in validated]
    if len(set(ids)) != len(ids):
        raise ValueError("duplicate attempt_id is invalid; records cannot be silently dropped")
    summary["received_record_count"] = len(validated)
    validated, error_rows, excluded, inference_blocked = _bind_to_schedule(validated, plan)
    summary.update(status="observed_attempt_records" if validated else "no_this_study_records",
                   record_count=len(validated), attempted_count=sum(record.attempted for record in validated),
                   error_rows=error_rows, quarantined_record_count=sum(row["disposition"] == "quarantined" for row in error_rows),
                   invalid_evidence_record_count=len(excluded), headline_inference_blocked=inference_blocked)
    if inference_blocked:
        summary["headline"]["inference_status"] = "blocked_due_to_actor_slot_collision"
    summary["problems"].extend({key: value for key, value in row.items()
                                if key not in {"original_record", "expected_binding"}} for row in error_rows)
    if not validated:
        validate_finite_json(summary)
        return summary
    clusters, cluster_problems = _independence_clusters(validated)
    summary["problems"].extend(cluster_problems)
    grouped = defaultdict(list)
    for record in validated:
        grouped[_group_key(record)].append(record)
    group_summaries = []
    comparisons = summary["paired_comparisons"]
    for key, group in sorted(grouped.items()):
        for condition in plan.conditions:
            condition_records = [record for record in group if record.condition == condition]
            if condition_records:
                group_summaries.append(_summarize_group(condition_records, key, condition))
        for comparison in comparisons:
            left, right = comparison["contrast"].split("-")
            comparison["groups"].append(_comparison(group, key, left, right, plan, clusters, excluded,
                                                   bootstrap_replicates, bootstrap_seed, block_inference=inference_blocked))
    summary["group_summaries"] = group_summaries
    model_groups = [group for group in group_summaries if group["actor_type"] == "model_participant"]
    if len(model_groups) == 1:
        group = model_groups[0]
        summary["headline"].update({name: group[name] for name in (*SUCCESS_FIELDS, "completion_rate", "fdr")})
        summary["headline"].update(cost_total=group["resources"]["cost"]["total"],
                                   cost_per_success=group["resources"]["cost"]["per_success"],
                                   cost_per_accepted_scientific_success=group["resources"]["cost"]["per_accepted_scientific_success"])
    elif len(model_groups) > 1:
        summary["headline"]["scope"] = "conditions_sources_and_models_reported_separately"
    summary["headline"]["groups"] = [{name: group[name] for name in (
        "condition", "source_kind", "actual_model", "model_identity_verified", "attempted_count",
        *SUCCESS_FIELDS, "completion_rate", "fdr")}
        for group in model_groups]
    for comparison in comparisons:
        model_comparisons = [g for g in comparison["groups"] if g["actor_type"] == "model_participant"]
        if len(model_comparisons) == 1:
            comparison.update({key: value for key, value in model_comparisons[0].items() if key != "groups"})
        else:
            comparison["status"] = "separate_source_and_model_groups"
            comparison["interval_reason"] = "no_pooled_model_or_source_inference"
            comparison["raw_scientific_interval_reason"] = "no_pooled_model_or_source_inference"
            comparison["accepted_scientific_interval_reason"] = "no_pooled_model_or_source_inference"
        if any(group["confidence_interval"] is not None or group["accepted_scientific_confidence_interval"] is not None
               for group in comparison["groups"]):
            summary["bootstrap"]["performed"] = True
    if inference_blocked:
        for comparison in comparisons:
            for item in [comparison, *comparison["groups"]]:
                item.update(confidence_interval=None, raw_scientific_confidence_interval=None, accepted_scientific_confidence_interval=None,
                            interval_reason="actor_slot_collision_blocks_inference",
                            raw_scientific_interval_reason="actor_slot_collision_blocks_inference",
                            accepted_scientific_interval_reason="actor_slot_collision_blocks_inference",
                            superiority_inference="unavailable", status="descriptive_only_invalid_actor_binding")
        summary["headline"]["inference_status"] = "blocked_due_to_actor_slot_collision"
    summary["outcome_taxonomy"] = dict(sorted(Counter(_outcome(r) for r in validated).items()))
    for panel, names in PANELS.items():
        summary["panels"][panel] = {"status": "observed", "groups": [
            {"actor_type": group["actor_type"], "source_kind": group["source_kind"], "condition": group["condition"],
             "actual_model": group["actual_model"], "model_identity_verified": group["model_identity_verified"],
             "values": {name: group["resources"].get(name, group["metrics"].get(name)) for name in names}}
            for group in group_summaries]}
    for record in validated:
        row = record.model_dump(mode="json", exclude={"gene_rows", "biological_metrics", "metric_missingness"})
        row["outcome"] = _outcome(record)
        row["primary_outcome"] = "raw_scientific_success"
        row["primary_task_success"] = bool(record.scientific_success is True) if record.attempted and record.source_kind == "synthetic" else None
        row["included_in_study_summary"] = True
        row["binding_status"] = "invalid_evidence_bound_to_expected_schedule" if record.attempt_id in excluded else "verified_schedule_binding"
        row["accepted_scientific_success"] = (
            bool(record.scientific_success is True and record.report_accepted is True)
            if record.attempted and record.source_kind == "synthetic" else None)
        row["effective_independence_id"] = clusters[record.independence_id]
        summary["run_rows"].append(row)
        # Only participant estimates approved by AttemptRecord's explicit field
        # allowlist are exported; no simulator goldens/parameters/seeds.
        if len(summary["gene_rows"])+len(record.gene_rows) > 1_000_000:
            raise ValueError("Gene export exceeds one million rows; analyze bounded record partitions")
        summary["gene_rows"].extend({"attempt_id": record.attempt_id, **gene} for gene in record.gene_rows)
    validate_finite_json(summary)
    return summary
