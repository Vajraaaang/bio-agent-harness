"""Offline biology reports with an explicit boundary around nonempty results.

Catalog/registration inspection is configuration work. This module never invokes
a handler, reads an expression matrix, builds a reference, fits or resamples.
"""
from __future__ import annotations

from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path

from .biology import BiologyCatalog, RuntimeSupportRegistry, load_biology_catalog
from .biology_planning import BiologyStudyPlan
from .biology_scoring import PUBLIC_METRIC_IDS, PUBLIC_SAFE_ROW_FIELDS
from .contracts import canonical_json_bytes, content_sha256
from .dashboard import (ARM_DESCRIPTIONS, COLORS, CONDITIONS, MAX_TABLE_ROWS, _CSS, _bounded_json,
                        _csv_bytes, _escape, _format, _public, _svg, _write_fresh)

MAX_OUTPUT_BYTES = 20_000_000
MAX_FIGURES = 36
CONTEXT = ("source_kind", "actual_model", "model_identity_sha256", "model_identity_verified", "actor_type", "condition")
GROUP_FIELDS = (*CONTEXT, "template_id", "case_id", "target_id", "record_count", "attempted_count",
    "completion_count", "structural_final_count", "completion_rate", "primary_eligible_attempt_count",
    "unknown_reference_attempt_count", "primary_reference_coverage", "scientific_success_count", "task_success_rate",
    "full_denominator_task_success_rate", "accepted_scientific_success_rate", "available_target_success_rate",
    "available_target_eligible_attempt_count", "correct_answer_rejection_rate", "incorrect_answer_acceptance_rate",
    "correct_answer_report_decision_count", "incorrect_answer_report_decision_count",
    "report_decision_coverage", "workflow_pass_rate", "workflow_observed_count", "trusted_capture_complete_count",
    "trusted_receipt_known_subtotal", "inference_status", "weighting", "reference_eligible_attempt_count",
    "target_success_rate", "accepted_target_success_rate", "refusal_reference_count", "correct_refusal_rate", "metric_scope")
GROUP_FIELDS += ("reasoning_reviewed_count", "reasoning_review_coverage", "reasoning_score_mean", "reviewed_insight_success_rate")
GROUP_FIELDS += ("tool_adoption_count", "tool_adoption_denominator", "tool_adoption_unknown_count", "tool_adoption_rate",
    "tool_calls_all_executions_known_subtotal", "final_execution_tool_calls_known_subtotal", "final_execution_usage_record_count")
GROUP_FIELDS += ("reasoning_score_lower_bound", "reasoning_score_upper_bound", "reviewed_insight_score_mean",
    "blinded_only_review_coverage", "blinded_only_reasoning_score_mean", "unblinded_rating_fraction", "condition_guess_count",
    "condition_guess_accuracy", "one_point_sensitive_fraction", "reasoning_pass_rate_at_7", "reasoning_pass_rate_at_8", "reasoning_pass_rate_at_9", "reasoning_comparison_status")
RESOURCE_FIELDS = ("total", "known_subtotal", "available_count", "complete_count", "denominator", "coverage",
                   "per_success", "per_success_estimand", "per_raw_scientific_success", "per_accepted_scientific_success", "status")
RUN_FIELDS = (*CONTEXT, "attempt_id", "template_id", "case_id", "repetition", "role", "attempted",
              "completion_status", "accepted_final", "scientific_success", "available_target_scientific_success",
              "primary_task_success", "accepted_scientific_success", "report_accepted", "workflow_passed",
              "integrity_status", "trusted_tool_capture_complete", "trusted_tool_calls", "trusted_receipt_count",
              "elapsed_sec", "RSS_MB", "tokens", "cost", "outcome", "binding_status")
RUN_FIELDS += ("reasoning_review_status", "reasoning_score", "reasoning_pass", "reviewed_insight_success", "reasoning_review_sha256")
RUN_FIELDS += ("observed_script_execution_count", "final_execution_id", "final_execution_tool_calls")
RUN_FIELDS += ("independent_rating_count", "reasoning_score_lower_bound", "reasoning_score_upper_bound", "reviewed_insight_score",
    "blinding_compromised", "blinded_only_reasoning_score", "reasoning_pass_at_7", "reasoning_pass_at_9", "one_point_sensitive", "critical_fabrication_reported")
COMPARISON_FIELDS = (*CONTEXT[:-1], "contrast", "primary_contrast", "primary_outcome", "estimand", "mean_delta",
    "confidence_interval", "interval_reason", "accepted_scientific_mean_delta", "accepted_scientific_confidence_interval",
    "accepted_scientific_interval_reason", "available_target_mean_delta", "status", "paired_repetition_count",
    "paired_cohort_count", "independent_group_count", "pairing_coverage", "eligible_pair_count", "unknown_reference_pair_count")
METRIC_FIELDS = (*CONTEXT, "scope", "template_id", "case_id", "target_id", "metric", "value", "units",
                 "available_count", "eligible_denominator", "attempted_denominator", "missing_reason", "known_subtotal")
PAIR_EXPORT_FIELDS = (*CONTEXT[:-1], "contrast", "contrast_role", "outcome", "outcome_role", "mean_delta", "ci_low",
    "ci_high", "confidence_level", "units", "eligible_pair_count", "unknown_reference_pair_count", "paired_repetition_count",
    "paired_cohort_count", "independent_group_count", "pairing_coverage", "interval_reason")
CAPABILITY_FIELDS = ("template_id", "template_name", "family", "case_id", "measurement", "effect_ids", "target_id",
                     "planned_cohorts", "planned_attempts", "registration_status", "missing_capabilities", "qualification_status")
CASE_FIELDS = ("template_id", "template_name", "family", "case_id", "measurement", "effect_ids", "target_count",
               "planned_cohorts", "planned_attempts", "status")
ERROR_FIELDS = ("attempt_id", "code", "disposition")
TARGET_RESULT_FIELDS = ("attempt_id", "target_id", "outcome", "scientific_success", "reference_availability")
RESULT_TABLES = ("group_summaries", "target_summaries", "case_summaries", "run_rows", "safe_rows", "target_rows")
SAFE_DETAIL_FIELDS = ("safe_detail_storage", "safe_rows_omitted_from_aggregate", "retained_safe_row_count",
    "retained_safe_scalar_count", "loaded_safe_row_count", "loaded_safe_scalar_count", "detail_task_id",
    "execution_manifest_sha256", "retained_root_verified", "receipt_scope", "clinical_qualification")
PROPORTIONS = {"task_success_rate", "accepted_scientific_success_rate", "available_target_success_rate", "target_success_rate",
               "accepted_target_success_rate", "correct_refusal_rate", "primary_reference_coverage", "workflow_pass_rate",
               "completion_rate", "report_decision_coverage", "correct_answer_rejection_rate", "incorrect_answer_acceptance_rate"}
RESOURCE_UNITS = {"elapsed_sec": "seconds", "RSS_MB": "RSS_MB; runtime-defined source units", "tokens": "tokens", "cost": "USD"}


def _take(row, fields):
    """Take declared fields only; scalar/location sanitization is defense in depth."""
    result = {}
    for key in fields:
        if key not in row:
            continue
        value = row[key]
        if isinstance(value, (list, tuple)):
            if key not in {"values", "confidence_interval", "accepted_scientific_confidence_interval"}:
                continue
            if any(type(item) not in (int, float) for item in value):
                continue
            if key != "values" and len(value) != 2:
                continue
            result[key] = list(value)
        elif value is None or type(value) in (str, int, float, bool):
            result[key] = _public(value)
    return result


def _public_group(row):
    result = _take(row, GROUP_FIELDS)
    result["resources"] = {key: _take(value, RESOURCE_FIELDS) for key, value in row.get("resources", {}).items()
                           if key in RESOURCE_UNITS}
    precision_fields=("monte_carlo_se", "reason", "independent_cohort_count", "scope", "degenerate_observations")
    result["task_success_precision"] = _take(row.get("task_success_precision", {}), precision_fields)
    result["within_cohort_repeatability"] = [dict(cell_index=index,**_take(value, (
        "observed_repetitions", "variance_df", "mean", "sample_variance", "standard_error_of_conditional_mean",
        "population_repeatability_established", "reason", "scope")))
        for index,value in enumerate(row.get("within_cohort_repeatability", []),start=1)]
    result["metrics"] = {key: _take(value, ("mean", "available_count", "eligible_denominator", "availability", *precision_fields))
                         for key, value in row.get("metrics", {}).items() if key in PUBLIC_METRIC_IDS}
    result["outcomes"] = {key: value for key, value in row.get("outcomes", {}).items()
                          if key in _OUTCOMES and type(value) is int}
    return result


_OUTCOMES = frozenset({"not_attempted", "invalid_evidence_failure", "timeout", "provider_error", "tool_error",
    "invalid_output", "blocked", "completed_without_structural_final", "full_contract_reference_unavailable",
    "scientifically_correct", "scientifically_correct_report_not_accepted", "scientific_failure"})


def _public_analysis(analysis):
    fields = ("record_format", "study_id", "plan_identity", "status", "primary_outcome", "primary_eligibility", "weighting",
        "planned_attempt_count", "planned_cohort_count", "planned_case_count", "planned_template_count",
        "planned_development_recipe_count", "received_record_count", "record_count", "attempted_count",
        "quarantined_record_count", "invalid_evidence_record_count", "scientific_inference_blocked")
    result = _take(analysis, (*fields, *SAFE_DETAIL_FIELDS))
    result["design_diagnostics"] = _take(analysis.get("design_diagnostics", {}), ("planned_cohorts", "minimum_two_sided_exact_p", "test_scope",
        "iid_half_success_perfect_separation_either_direction", "iid_half_success_perfect_separation_prespecified_direction", "separation_assumption", "empirical_power", "case_minimum_detectable_effect", "equal_planned_cohort_weights"))
    result["reasoning_evaluation"] = _take(analysis.get("reasoning_evaluation", {}), ("status", "reviewed_count", "primary_blinded_review_count",
        "available_answer_count", "holistic_insight_success_rate", "scope", "rubric_calibrated", "human_authorship_authenticated",
        "protocol_version", "primary_human_endpoint", "binary_endpoint_role", "raw_rating_count", "paired_rating_answer_count",
        "declared_cohort_count", "agreement_uncertainty", "raw_ratings_sha256"))
    result["reasoning_evaluation"]["rating_agreement"] = [_take(row, ("criterion", "paired_answer_count", "exact_agreement",
        "kappa", "confidence_interval", "reason")) for row in analysis.get("reasoning_evaluation", {}).get("rating_agreement", [])]
    result["detectability"] = _take(analysis.get("detectability", {}), ("status", "minimum_detectable_effect", "failure_interpretation"))
    result["headline"] = _take(analysis.get("headline", {}), (*GROUP_FIELDS, "scope"))
    result["headline"]["groups"] = [_public_group(row) for row in analysis.get("headline", {}).get("groups", [])]
    for field in ("group_summaries", "case_summaries", "target_summaries"):
        result[field] = [_public_group(row) for row in analysis.get(field, [])]
    result["paired_comparisons"] = [{**_take(row, COMPARISON_FIELDS),
                                     "precision": _take(row.get("precision", {}), ("monte_carlo_se", "reason", "scope", "independent_cohort_count", "degenerate_observations")),
                                     "groups": [{**_take(group, COMPARISON_FIELDS), "precision": _take(group.get("precision", {}),
                                        ("monte_carlo_se", "reason", "scope", "independent_cohort_count", "degenerate_observations"))} for group in row.get("groups", [])]}
                                    for row in analysis.get("paired_comparisons", [])]
    result["run_rows"] = [_take(row, RUN_FIELDS) for row in analysis.get("run_rows", [])]
    result["safe_rows"] = [_take(row, ("attempt_id", *PUBLIC_SAFE_ROW_FIELDS)) for row in analysis.get("safe_rows", [])]
    result["target_rows"] = [{**_take(row, TARGET_RESULT_FIELDS),
                              "metrics": {key: value for key, value in row.get("metrics", {}).items() if key in PUBLIC_METRIC_IDS},
                              "metric_missingness": {key: _public(value) for key, value in row.get("metric_missingness", {}).items()
                                                     if key in PUBLIC_METRIC_IDS or key == "scientific_success"}}
                             for row in analysis.get("target_rows", [])]
    for field in ("error_rows", "problems"):
        result[field] = [_take(row, ERROR_FIELDS) for row in analysis.get(field, []) if isinstance(row, dict)]
    result["bootstrap"] = _take(analysis.get("bootstrap", {}), ("performed", "replicates", "minimum_independent_cohorts",
                    "confidence_level", "unit", "strata", "repetitions"))
    result["outcome_taxonomy"] = {key: value for key, value in analysis.get("outcome_taxonomy", {}).items()
                                  if key in _OUTCOMES and type(value) is int}
    return result


def _safe_detail_metadata(summary):
    """Check loaded-versus-retained counts only; never resolve a source artifact."""
    if "safe_detail_storage" not in summary:
        return
    if summary["safe_detail_storage"] != "per_attempt":
        raise ValueError("Safe detail storage must use per_attempt metadata")
    names = ("retained_safe_row_count", "retained_safe_scalar_count", "loaded_safe_row_count", "loaded_safe_scalar_count")
    if any(type(summary.get(name)) is not int or not 0 <= summary[name] < 2**63 for name in names):
        raise ValueError("Safe detail counts must be bounded nonnegative integers")
    loaded = summary["safe_rows"]
    if (summary["loaded_safe_row_count"] != len(loaded)
            or summary["loaded_safe_scalar_count"] != sum(len(row.get("values", [])) for row in loaded)
            or summary["loaded_safe_row_count"] > summary["retained_safe_row_count"]
            or summary["loaded_safe_scalar_count"] > summary["retained_safe_scalar_count"]):
        raise ValueError("Safe detail loaded counts must match the supplied rows and retained totals")
    if type(summary.get("safe_rows_omitted_from_aggregate")) is not bool:
        raise ValueError("Safe detail omission metadata must be boolean")
    if "retained_root_verified" in summary and type(summary["retained_root_verified"]) is not bool:
        raise ValueError("Receipt integrity metadata must be boolean")
    if summary.get("clinical_qualification") not in (None, False):
        raise ValueError("This report cannot establish clinical qualification")


def _prediction_details(summary):
    fields = ('attempt_id', 'target_id', 'hypothesis_id', 'gene_id', 'estimate_status', 'p_value', 'q_value', 'missing_reason')
    start = '<section id="prediction-details"><h3>Participant estimates & stored details</h3>'
    if summary.get("safe_detail_storage") != "per_attempt":
        return start + _table(summary["safe_rows"], fields, 'Participant estimates') + '</section>'
    loaded = summary["loaded_safe_row_count"]
    state = "Prediction details stored separately / selected rows loaded" if loaded else "Prediction details stored separately / not loaded"
    notice = (f'<div class="notice"><b>{state}</b> Loaded rows: {loaded:,} · '
              f'Retained rows: {summary["retained_safe_row_count"]:,} · '
              f'Loaded scalar values: {summary["loaded_safe_scalar_count"]:,} · '
              f'Retained scalar values: {summary["retained_safe_scalar_count"]:,}.</div>'
              '<p class="note">The participant-estimate CSV contains only loaded rows. Unloaded details are not missing predictions; scalar summaries retain their own evidence scope.</p>'
              f'<p class="note">Selected task: {_escape(summary.get("detail_task_id"))} · '
              f'Caller-reported retained-root verification: {_escape(summary.get("retained_root_verified"))} · '
              f'Receipt scope: {_escape(summary.get("receipt_scope"))}. '
              'Receipt integrity is separate from scientific or clinical qualification.</p>')
    return start + notice + (_table(summary["safe_rows"], fields, 'Loaded participant estimates') if loaded else '') + '</section>'


def _coverage(plan, catalog, registry):
    counts = Counter(recipe.case_id for recipe in plan.recipes if recipe.role == "evaluation")
    cases, capabilities = [], []
    for template in catalog.templates:
        for case in template.cases:
            base = {"template_id": template.template_id, "template_name": template.name, "family": template.family,
                    "case_id": case.case_id, "measurement": case.measurement, "effect_ids": list(case.effect_ids),
                    "planned_cohorts": counts[case.case_id], "planned_attempts": counts[case.case_id] * plan.repetitions * 3}
            cases.append({**base, "target_count": len(template.targets), "status": "catalog_configuration_only"})
            for target in template.targets:
                case_scope = next(scope for scope in case.target_scopes if scope.target_id == target.target_id)
                required = (("generator", template.generator_capability_id), ("scorer", target.scorer_capability_id),
                            *(("tool", name) for name in (case_scope.tool_requirements or target.tool_requirements)))
                missing = None if registry is None else [f"{kind}:{name}" for kind, name in required if not registry.contains(
                    kind, name, template.template_id, case.case_id, target.target_id, case.measurement)]
                capabilities.append({**base, "target_id": target.target_id,
                    "registration_status": "not_inspected" if missing is None else "callable_registered" if not missing else "missing_registration",
                    "missing_capabilities": missing, "qualification_status": "not_established"})
    return cases, capabilities


def _methods(rows, catalog):
    targets = {target.target_id for template in catalog.templates for target in template.targets}
    projected = []
    for row in rows or []:
        item = _take(row, ("tool_name", "driver", "result_role", "qualification"))
        item["target_ids"] = [target for target in row.get("target_ids", []) if target in targets]
        item["unsupported_estimation_targets"] = [target for target in row.get("unsupported_estimation_targets", []) if target in targets]
        item["estimation_limits"] = {target: _public(note) for target, note in row.get("estimation_limits", {}).items() if target in targets}
        projected.append(item)
    return projected


def _metric_rows(summary, catalog):
    rows = []
    units = {(template.template_id, target.target_id): target.measurement_units
             for template in catalog.templates for target in template.targets}
    for field, scope in (("group_summaries", "condition"), ("case_summaries", "case"), ("target_summaries", "target")):
        for group in summary[field]:
            context = _take(group, (*CONTEXT, "case_id", "template_id", "target_id"))
            for name in sorted(PROPORTIONS):
                if name not in group:
                    continue
                value = group[name]
                denominator = group.get("reference_eligible_attempt_count", group.get("primary_eligible_attempt_count"))
                if name in {"completion_rate", "report_decision_coverage", "primary_reference_coverage"}:
                    denominator = group.get("attempted_count")
                if name == "available_target_success_rate":
                    denominator = group.get("available_target_eligible_attempt_count")
                if name == "workflow_pass_rate":
                    denominator = group.get("workflow_observed_count")
                if name == "correct_refusal_rate":
                    denominator = group.get("refusal_reference_count")
                if name == "correct_answer_rejection_rate":
                    denominator = group.get("correct_answer_report_decision_count")
                if name == "incorrect_answer_acceptance_rate":
                    denominator = group.get("incorrect_answer_report_decision_count")
                rows.append({**context, "scope": scope, "metric": name, "value": value, "units": "proportion",
                             "eligible_denominator": denominator, "attempted_denominator": group.get("attempted_count"),
                             "available_count": None, "missing_reason": "unavailable_or_inference_blocked" if value is None else None})
            for name, measurement in group.get("metrics", {}).items():
                rows.append({**context, "scope": scope, "metric": name, "value": measurement.get("mean"),
                    "units": "metric-specific; target units: " + units.get((group.get("template_id"), group.get("target_id")), "not declared"),
                    "eligible_denominator": measurement.get("eligible_denominator"), "available_count": measurement.get("available_count"),
                    "attempted_denominator": group.get("attempted_count"),
                    "missing_reason": "unavailable_or_inference_blocked" if measurement.get("mean") is None else None})
            for name, measurement in group.get("resources", {}).items():
                rows.append({**context, "scope": scope, "metric": name, "value": measurement.get("total"),
                    "units": RESOURCE_UNITS[name], "available_count": measurement.get("available_count"),
                    "eligible_denominator": measurement.get("denominator"), "attempted_denominator": group.get("attempted_count"),
                    "known_subtotal": measurement.get("known_subtotal"),
                    "missing_reason": measurement.get("status") if measurement.get("total") is None else None})
    return rows


def _pair_rows(summary):
    rows = []
    minimum = summary["bootstrap"].get("minimum_independent_cohorts", 10)
    for comparison in summary["paired_comparisons"]:
        for group in comparison["groups"]:
            for name, role, mean_field, ci_field, reason_field in (
                ("raw_full_contract_correctness", "primary", "mean_delta", "confidence_interval", "interval_reason"),
                ("accepted_correct_reports", "secondary", "accepted_scientific_mean_delta", "accepted_scientific_confidence_interval", "accepted_scientific_interval_reason"),
                ("available_target_correctness", "secondary", "available_target_mean_delta", "not_reported", "not_reported")):
                ci, reason = group.get(ci_field), group.get(reason_field)
                if name == "available_target_correctness":
                    reason = "secondary_interval_not_reported"
                if type(group.get("independent_group_count")) is not int or group["independent_group_count"] < minimum:
                    ci, reason = None, "insufficient_independent_cohorts"
                if summary.get("scientific_inference_blocked"):
                    ci, reason = None, "scientific_inference_blocked"
                rows.append({**_take(group, PAIR_EXPORT_FIELDS), "contrast": comparison.get("contrast"),
                    "contrast_role": "primary" if comparison.get("primary_contrast") else "exploratory", "outcome": name,
                    "outcome_role": role, "mean_delta": group.get(mean_field), "ci_low": ci[0] if ci else None,
                    "ci_high": ci[1] if ci else None, "confidence_level": summary["bootstrap"].get("confidence_level", 0.95),
                    "units": "proportion difference", "interval_reason": reason})
    return rows


def _table(rows, fields, title, *, empty="Not run — no observed rows."):
    if not rows:
        return f'<div class="empty compact">{_escape(empty)}</div>'
    headers = ''.join(f'<th scope="col">{_escape(name.replace("_", " "))}</th>' for name in fields)
    body = []
    for row in rows[:MAX_TABLE_ROWS]:
        attributes = ' '.join(f'data-{name.replace("_", "-")}="{_escape(row.get(name, ""))}"' for name in _FILTERS)
        body.append(f'<tr {attributes}>' + ''.join(f'<td>{_escape(row.get(name))}</td>' for name in fields) + '</tr>')
    return (f'<p class="table-count">{len(rows)} source rows; at most {MAX_TABLE_ROWS} displayed. Full bounded rows are exported.</p>'
            f'<div class="table-wrap"><table data-filterable aria-label="{_escape(title)}"><thead><tr>{headers}</tr></thead>'
            f'<tbody>{"".join(body)}</tbody></table></div><p class="filter-empty" hidden>No table rows match these filters.</p>')


def _figures(metrics, pairs):
    numeric = [row for row in metrics if type(row.get("value")) in (int, float)]
    paired = [row for row in pairs if type(row.get("mean_delta")) in (int, float)]
    if not numeric and not paired:
        return [], 0
    from matplotlib import rc_context
    from matplotlib.figure import Figure
    figures, omitted = [], 0
    grouped = defaultdict(list)
    for row in numeric:
        grouped[tuple(row.get(key) for key in (*CONTEXT[:-1], "scope", "metric", "units"))].append(row)
    with rc_context({"svg.fonttype": "none", "text.usetex": False, "font.size": 9,
                     "axes.spines.top": False, "axes.spines.right": False}):
        for identity, rows in grouped.items():
            if len(figures) >= MAX_FIGURES:
                omitted += 1
                continue
            categories = sorted({row.get("case_id") or row.get("target_id") or "all configured evaluation cases" for row in rows})
            figure = Figure(figsize=(max(6, min(14, len(categories) * 0.4)), 4.2))
            axis = figure.add_subplot()
            for arm in CONDITIONS:
                selected = [row for row in rows if row.get("condition") == arm]
                axis.scatter([categories.index(row.get("case_id") or row.get("target_id") or "all configured evaluation cases")
                              + (CONDITIONS.index(arm)-1)*0.15 for row in selected], [row["value"] for row in selected],
                             label=arm, color=COLORS[arm], s=22)
            axis.set_xticks(range(len(categories)), categories, rotation=75 if len(categories) > 4 else 0, fontsize=7)
            metric, unit = identity[-2:]
            values = [row["value"] for row in rows]
            if unit == "proportion":
                axis.set_ylim(0, 1)
            else:
                low, high = min(0, min(values)), max(values)
                pad = max((high-low)*0.1, 0.01)
                axis.set_ylim(low-pad if low else 0, high+pad)
            axis.set_ylabel(f'{metric.replace("_", " ")} ({unit})', fontsize=8)
            axis.legend(fontsize=8)
            figure.tight_layout()
            figures.append({"kind": "resources" if metric in RESOURCE_UNITS else "metrics", "svg": _svg(figure),
                            "caption": f"Descriptive {identity[-3]} values · {' / '.join(_format(item) for item in identity[:5])}. Common bounds across arms; interval unavailable for these descriptive values."})
            figure.clear()
        for row in paired:
            if len(figures) >= MAX_FIGURES:
                omitted += 1
                continue
            figure = Figure(figsize=(7, 2.4))
            axis = figure.add_subplot()
            axis.scatter(row["mean_delta"], 0, color=COLORS["enforced"])
            if row["ci_low"] is not None and row["ci_high"] is not None:
                axis.hlines(0, row["ci_low"], row["ci_high"], color=COLORS["enforced"])
            axis.axvline(0, color="#a6adb4", linestyle="--")
            axis.set(xlim=(-1, 1), yticks=[0], yticklabels=[row["contrast"]], xlabel="Paired proportion difference")
            figures.append({"kind": "paired", "svg": _svg(figure), "caption": f"{row['outcome_role']} / {row['outcome']} / {row.get('source_kind')} / {row.get('actual_model')}. CI reason: {row.get('interval_reason') or 'available'}; independent cohorts: {row.get('independent_group_count')}."})
            figure.clear()
    return figures, omitted


_FILTERS = {"template_id": "Template", "case_id": "Case", "target_id": "Target", "condition": "Condition",
            "source_kind": "Source", "actual_model": "Actual Model"}


def _html(plan, summary, cases, capabilities, methods, metrics, pairs, figures):
    attempts = summary["planned_attempt_count"]
    precision_rows=[{**{key:group.get(key) for key in CONTEXT},**group.get("task_success_precision", {})}
                    for group in summary.get("group_summaries", [])]
    repeat_rows=[{**{key:group.get(key) for key in CONTEXT},**cell}
                 for group in summary.get("group_summaries", []) for cell in group.get("within_cohort_repeatability", [])]
    executed = summary.get("attempted_count", 0)
    callable_count = summary["callable_case_target_count"]
    coverage = summary["catalog_coverage"]
    controls = []
    context_rows = [*cases, *capabilities, *summary["run_rows"], *metrics]
    for field, label in _FILTERS.items():
        options = ''.join(f'<option value="{_escape(value)}">{_escape(value)}</option>'
                          for value in sorted({_format(row[field]) for row in context_rows if row.get(field) is not None}))
        controls.append(f'<div><label for="filter-{field}">{label}</label><select id="filter-{field}" aria-label="{label}" data-filter="{field.replace("_", "-")}"><option value="">All</option>{options}</select></div>')
    def plots(kind):
        selected = [figure for figure in figures if figure["kind"] == kind]
        return ''.join(f'<figure>{figure["svg"]}<figcaption>{_escape(figure["caption"])}</figcaption></figure>' for figure in selected)
    empty = '<div class="empty"><span class="empty-mark">—</span><strong>Not run</strong><p>No measured observations support a scientific score, fitted curve or interval.</p></div>'
    status_rows = [{"contrast": row.get("contrast"), "outcome": "raw full-contract correctness", "status": row.get("status"),
                    "interval_reason": row.get("interval_reason")} for row in summary["paired_comparisons"]]
    registry_note = ("Registry not supplied — callable support has not been inspected." if summary["registration_status"] == "not_inspected"
                     else "Exact case / target / measurement registrations inspected; no handler was invoked.")
    arms = ''.join(f'<article class="arm"><div class="arm-top"><span class="dot {arm}"></span><h3>{arm.title()}</h3><span>{attempts // 3} planned</span></div><p>{description}</p></article>' for arm, description in ARM_DESCRIPTIONS.items())
    errors = [*summary["error_rows"], *summary["problems"]]
    blocked = '<div class="notice"><b>Scientific inference blocked.</b> Inspect record disposition and reference eligibility conflicts.</div>' if summary.get("scientific_inference_blocked") else ''
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; img-src data:; connect-src 'none'; base-uri 'none'; form-action 'none'">
<title>Biology study · {_escape(plan.study_id)}</title><style>{_CSS}
.coverage-grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin:24px 0}}.coverage-card{{border:1px solid var(--line);background:white;border-radius:6px;padding:18px}}.coverage-card h2{{font-size:13px}}.coverage-card strong{{font-family:Georgia,serif;display:block;font-size:28px;font-weight:400;margin:10px 0}}.coverage-card p{{font-size:11px;color:var(--muted);margin:0}}.biology-filters{{grid-template-columns:repeat(3,minmax(0,1fr)) auto}}@media(max-width:850px){{.coverage-grid{{grid-template-columns:repeat(2,1fr)}}.biology-filters{{grid-template-columns:repeat(2,minmax(0,1fr))}}}}
</style></head><body><header class="masthead"><a class="brand" href="#overview">DEVBENCH <span>BIOLOGY / STUDY REPORT</span></a><span class="status">{_escape(summary['status'])}</span></header><main>
<section id="overview"><p class="eyebrow">BIOLOGICAL COVERAGE · THREE-ARM COMPARISON</p><h1>Does DevHarness improve<br>biological inference?</h1><p class="lede">{coverage['case_count']} cases · {coverage['template_count']} templates · {coverage['effect_family_count']} effect families. Compare scientific correctness under baseline, optional and enforced tool access, with explicit evidence limits for every target.</p>
<div class="meta"><span>Study <b>{_escape(plan.study_id)}</b></span><span>Requested model <b>{_escape(plan.requested_model or 'Not selected')}</b></span><span>Primary <b>Raw full-contract correctness</b></span><span>Privacy <b>public_summary</b></span></div>
<div class="stat-strip"><div><span>Planned model attempts</span><strong data-planned-attempts="{attempts}">{attempts}</strong><small>{summary['planned_cohort_count']} proposed cohorts × {plan.repetitions} repeats × 3 arms</small></div><div><span>Observed attempts</span><strong data-executed-attempts="{executed}">{executed}</strong><small>Attempted failures retained within eligibility</small></div><div><span>Planned case / template denominator</span><strong>{summary['planned_case_count']} / {summary['planned_template_count']}</strong><small>Development recipes excluded from evaluation</small></div><div><span>Scientific performance</span><strong class="word">{'Unknown' if not executed else 'By scope'}</strong><small>Unknown reference coverage remains explicit</small></div></div></section>
<div class="notice"><b>Research configuration, not qualification.</b> No scientific validation, causal completeness or clinical readiness follows from this catalog or a callable method.</div>{blocked}
<section class="coverage-grid" aria-label="Evidence levels"><article class="coverage-card"><h2>Configured</h2><strong>{coverage['case_count']} / {coverage['template_count']} / {coverage['effect_family_count']}</strong><p>Catalog cases / templates / effect families. {summary['planned_case_count']} cases selected for evaluation.</p></article><article class="coverage-card"><h2>Callable registered</h2><strong>{_escape(callable_count)}</strong><p>Complete case-target bundles. {registry_note}</p></article><article class="coverage-card"><h2>Measured</h2><strong>{executed} attempts</strong><p>Performance and target-reference eligibility remain unknown until observed.</p></article><article class="coverage-card"><h2>Qualified</h2><strong>Not established</strong><p>Numerical implementations and scientific thresholds remain uncalibrated and unqualified.</p></article></section>
<section class="arm-grid" aria-label="Study conditions">{arms}</section>
<nav class="panel-nav" aria-label="Biology report sections"><a href="#outcomes">01 Scientific outcomes</a><a href="#eligibility">02 Reference coverage</a><a href="#catalog">03 Catalog & registration</a><a href="#method-gaps">04 Method gaps</a><a href="#target-metrics">05 Case & target metrics</a><a href="#resources">06 Resources & adoption</a><a href="#exports">07 Source tables</a></nav>
<section class="filters"><h2>Table-only filters</h2><p>Filters change displayed table rows only. Figures and headline counts keep their stated scope; rows lacking a selected context are hidden.</p><div class="filter-grid biology-filters">{''.join(controls)}<button id="reset-filters" type="button">Reset</button></div></section>
<section id="outcomes" class="panel"><div class="section-title"><span>01</span><h2>Raw full-contract correctness</h2></div><p class="description">Primary: enforced − baseline on the fixed subset whose required target references are all scorable before the answer. A workflow gate changing acceptance alone does not establish improved inference.</p>{plots('paired') or empty}
<p class="note">This primary outcome scores numbers and structured claims. Free-text biological reasoning requires separate human adjudication; an unreviewed narrative is not a verified biological insight.</p>
<h3>Biological reasoning review</h3>{_table([summary.get('reasoning_evaluation', {})], ('status','raw_rating_count','paired_rating_answer_count','declared_cohort_count','rubric_calibrated'), 'Independent rating coverage')}
<p class="note">Continuous panel-mean reasoning scores are primary for human review. All independent ratings remain included regardless of reported blinding; unblinding may bias scores and is reported separately. Raw ratings are retained in a private audit sibling directory. Reliability is unqualified; repeated answers within a cohort are dependent.</p>
{_table(summary.get('group_summaries', []), (*CONTEXT,'reasoning_review_coverage','reasoning_score_mean','reasoning_score_lower_bound','reasoning_score_upper_bound','reviewed_insight_score_mean'), 'Continuous reasoning scores and missing-rating bounds')}
{_table(summary.get('reasoning_evaluation', {}).get('rating_agreement', []), ('criterion','paired_answer_count','exact_agreement','kappa','reason'), 'Inter-rater agreement — descriptive only')}
{_table(summary.get('group_summaries', []), (*CONTEXT,'unblinded_rating_fraction','condition_guess_accuracy','blinded_only_review_coverage','blinded_only_reasoning_score_mean','reasoning_comparison_status'), 'Blinding sensitivity and availability by arm')}
{_table(summary.get('group_summaries', []), (*CONTEXT,'reasoning_pass_rate_at_7','reasoning_pass_rate_at_8','reasoning_pass_rate_at_9','one_point_sensitive_fraction'), 'Secondary cutoff and one-point sensitivity')}
<h3>Precision and repeatability</h3>{_table(precision_rows, (*CONTEXT,'monte_carlo_se','independent_cohort_count','reason'), 'Cohort-level Monte Carlo SE')}
{_table(repeat_rows, (*CONTEXT,'cell_index','observed_repetitions','variance_df','sample_variance','standard_error_of_conditional_mean','reason'), 'Within-cohort repeated-run variation')}
<p class="note">One run cannot estimate repeated-run variance. Zero observed variance in a few runs does not establish zero stochasticity. Minimum detectable effects remain uncalibrated for the current case and analytical method.</p>
{_table([summary.get('design_diagnostics', {})], ('planned_cohorts','minimum_two_sided_exact_p','empirical_power'), 'Idealized equal-weight paired-binary design limits')}
<h3>Accepted-correct reports · secondary</h3><p class="note">Correctness AND report acceptance; reported separately from raw scientific correctness.</p><h3>Available-target correctness · secondary</h3><p class="note">Partial-contract evidence is useful but does not replace full-contract correctness or silently change its denominator.</p>
{_table(status_rows, ('contrast','outcome','status','interval_reason'), 'Pairing availability')}
<p class="note">95% uncertainty is unavailable without sufficient independent cohorts (minimum {summary['bootstrap'].get('minimum_independent_cohorts', 10)}), valid pairing and reference masks. Genes, cells, specimens and repeated attempts do not add independent cohorts.</p>{_table(pairs, PAIR_EXPORT_FIELDS, 'Paired source rows')}</section>
<section id="eligibility" class="panel"><div class="section-title"><span>02</span><h2>Fixed reference coverage & unknowns</h2></div><p class="description">Unknown target truth is not scored as failure. All attempted failures stay in the fixed eligible denominator; partial-reference contracts remain outside the primary denominator independently of the answer.</p>
{_table(summary['group_summaries'], (*CONTEXT, 'attempted_count','primary_eligible_attempt_count','unknown_reference_attempt_count','primary_reference_coverage'), 'Reference coverage', empty='Unknown reference coverage — no attempts or reference masks observed.')}
<p class="note">Weighting: average repeats within each cohort, then give equal weight to cohorts within a case, cases within a template and templates. Public annotations are incomplete observations; they are not complete causal truth.</p></section>
<section id="catalog" class="panel"><div class="section-title"><span>03</span><h2>Configured catalog & callable registration</h2></div><p class="description">{registry_note} Registration is checked for the exact case, target and measurement. Planned cohort counts describe scheduling, not observed independence.</p>{_table(cases, CASE_FIELDS, 'Configured cases')}
<details><summary>Exact case-target registration and missing requirements</summary>{_table(capabilities, CAPABILITY_FIELDS, 'Case-target capabilities')}</details></section>
<section id="method-gaps" class="panel"><div class="section-title"><span>04</span><h2>Implementation & method gaps</h2></div><p class="description">Callable registration does not establish case-level method adequacy. A handler can return a diagnostic or an explicit limitation without estimating a target. No method is labeled qualified by registration alone.</p>
{_table(methods, ('tool_name','driver','target_ids','unsupported_estimation_targets','estimation_limits','qualification'), 'Method limitations', empty='Method metadata not supplied — estimation coverage and implementation gaps have not been inspected.')}</section>
<section id="target-metrics" class="panel"><div class="section-title"><span>05</span><h2>Case, target & statistical performance</h2></div><p class="description">Effects, temporal events, periodicity, variability, cellular state, topology, regulation and perturbations retain their target-specific definitions, reference eligibility and units. Coverage and interval width remain distinct from BH arithmetic.</p>{plots('metrics') or empty}
{_table(metrics, METRIC_FIELDS, 'Scientific and resource source metrics')}<p class="note">Case/target figures are descriptive. Target-specific or case-specific paired intervals are not fabricated when the analyzer supplies only an overall paired comparison. Missing estimates remain unavailable.</p></section>
<section id="resources" class="panel"><div class="section-title"><span>06</span><h2>Resources, failures & trusted adoption</h2></div><p class="description">Elapsed time is seconds. RSS_MB retains runtime-defined source units. Tokens and USD cost stay unknown when capture is absent; subscription cost is unknown unless supplied. Failed attempts remain in accounting.</p>{plots('resources') or empty}
{_table(summary['group_summaries'], (*CONTEXT, 'completion_count','attempted_count','workflow_observed_count','workflow_pass_rate','tool_adoption_count','tool_adoption_denominator','tool_adoption_unknown_count','tool_adoption_rate','trusted_receipt_known_subtotal','final_execution_tool_calls_known_subtotal'), 'Participant adoption and calls across all executions')}
{_table(summary['run_rows'], (*CONTEXT, 'attempt_id','case_id','completion_status','outcome','elapsed_sec','RSS_MB','tokens','cost','trusted_tool_calls','observed_script_execution_count','final_execution_id','final_execution_tool_calls'), 'Attempt accounting')}</section>
<section id="exports" class="panel"><div class="section-title"><span>07</span><h2>Source tables & record disposition</h2></div><p class="description">Blank CSV values mean unavailable, never zero. Public exports contain allowlisted summaries and participant estimates only. Private plans, references and raw answers are omitted.</p><div class="downloads">{''.join(f'<a href="{name}" download>{label}</a>' for name,label in [('summary.json','Summary JSON'),('cases.csv','Configured cases'),('capabilities.csv','Callable coverage'),('methods.csv','Method gaps'),('runs.csv','Attempts'),('metrics.csv','Metrics'),('comparisons.csv','Paired comparisons'),('target_results.csv','Target outcomes'),('safe_estimates.csv','Participant estimates'),('errors.csv','Record disposition')])}</div>
{_table(errors, ERROR_FIELDS, 'Invalid and quarantined records', empty='No record disposition events reported.')}
{_table(summary['target_rows'], TARGET_RESULT_FIELDS, 'Target outcomes')}
{_prediction_details(summary)}
<p class="note">No clinical-readiness score is computed. Figure views are bounded to {MAX_FIGURES}; {summary['omitted_figure_scope_count']} additional scopes are available in source tables without a rendered figure.</p></section>
<footer>Offline biology report · {_escape(plan.study_id)}<span>No network assets, data acquisition, reference construction, fitting or model execution.</span></footer></main><script>{_JS}</script></body></html>'''


def build_biology_dashboard(plan: BiologyStudyPlan, analysis: dict | None, output_dir: Path, *,
                            catalog: BiologyCatalog | None = None, registry: RuntimeSupportRegistry | None = None,
                            method_metadata: list[dict] | None = None, allow_result_reporting: bool = False) -> dict:
    """Write a fresh offline report; empty analysis is the default, data-free path.

    Registry handlers are inspected, never called. Optional method_metadata is a
    list of ``tool_name``/``driver`` plus pure capability metadata dictionaries.
    A nonempty, previously computed analysis requires allow_result_reporting=True;
    the renderer never calls the analyzer on nonempty records or computes CIs.
    """
    if type(allow_result_reporting) is not bool:
        raise ValueError("allow_result_reporting must be a boolean")
    if analysis is not None and isinstance(analysis, dict) and not allow_result_reporting:
        headline = analysis.get("headline", {})
        headline_results = isinstance(headline, dict) and any(headline.get(field) is not None for field in
            ("task_success_rate", "accepted_scientific_success_rate", "available_target_success_rate",
             "primary_eligible_attempt_count", "unknown_reference_attempt_count"))
        paired_results = any(row.get("groups") or any(row.get(field) is not None for field in
            ("mean_delta", "confidence_interval", "accepted_scientific_mean_delta", "available_target_mean_delta"))
            for row in analysis.get("paired_comparisons", []) if isinstance(row, dict))
        if (any(analysis.get(field) for field in RESULT_TABLES)
                or analysis.get("attempted_count", 0) != 0 or analysis.get("record_count", 0) != 0
                or analysis.get("received_record_count", 0) != 0 or analysis.get("error_rows")
                or headline_results or paired_results or analysis.get("bootstrap", {}).get("performed", False)):
            raise PermissionError("Nonempty biology reporting requires allow_result_reporting=True")
    if not isinstance(plan, BiologyStudyPlan):
        raise ValueError("A validated BiologyStudyPlan is required")
    plan = BiologyStudyPlan.model_validate_json(canonical_json_bytes(plan.model_dump(mode="json")))
    if not Path(output_dir).is_absolute():
        raise ValueError("Output directory must be absolute")
    catalog = catalog if catalog is not None else load_biology_catalog()
    if not isinstance(catalog, BiologyCatalog) or content_sha256(catalog.model_dump(mode="json")) != plan.catalog_sha256:
        raise ValueError("Biology plan must match the supplied catalog")
    if registry is not None and not isinstance(registry, RuntimeSupportRegistry):
        raise ValueError("A RuntimeSupportRegistry is required for callable inspection")
    if registry is not None and len(registry.registrations) > 10_000:
        raise ValueError("Registration metadata exceeds the bounded size")
    for recipe in plan.recipes:
        recipe.validate_against(catalog)
    if analysis is None:
        from .biology_metrics import analyze_biology_attempts
        analysis = analyze_biology_attempts([], plan)
    if not isinstance(analysis, dict):
        raise ValueError("A biology analysis dictionary is required")
    _bounded_json(analysis)
    _bounded_json(method_metadata or [])
    minimum = analysis.get("bootstrap", {}).get("minimum_independent_cohorts")
    if type(minimum) is not int or not 10 <= minimum <= 100_000:
        raise ValueError("Biology uncertainty requires at least ten independent cohorts")
    if analysis.get("study_id") != plan.study_id or analysis.get("plan_identity") != plan.sha256():
        raise ValueError("Biology analysis belongs to a different study plan")
    evaluation = [recipe for recipe in plan.recipes if recipe.role == "evaluation"]
    planned = {"planned_attempt_count": len(evaluation) * plan.repetitions * 3, "planned_cohort_count": len(evaluation),
               "planned_case_count": len({recipe.case_id for recipe in evaluation}),
               "planned_template_count": len({recipe.template_id for recipe in evaluation}),
               "planned_development_recipe_count": len(plan.recipes) - len(evaluation)}
    if any(type(analysis.get(key)) is not int or analysis[key] != count for key, count in planned.items()):
        raise ValueError("Analysis planned counts must match the biology study plan")
    public = _public_analysis(analysis)
    _safe_detail_metadata(public)
    cases, capabilities = _coverage(plan, catalog, registry)
    methods = _methods(method_metadata, catalog)
    public.update(privacy_status="public_summary", qualification_status="not_established",
                  registration_status="not_inspected" if registry is None else "inspected_callable_metadata_only",
                  callable_case_target_count=None if registry is None else sum(row["registration_status"] == "callable_registered" for row in capabilities),
                  catalog_coverage={"case_count": len(cases), "template_count": len(catalog.templates), "effect_family_count": len(catalog.effects)})
    metrics, pairs = _metric_rows(public, catalog), _pair_rows(public)
    _bounded_json(metrics)
    figures, omitted = _figures(metrics, pairs)
    public["omitted_figure_scope_count"] = omitted
    public["display_contract"] = {"table_filters_only": True, "unknown_is_not_zero": True,
                                  "safe_estimate_csv_scope": "loaded_rows_only",
                                  "qualification_not_established_by_registration": True}
    document = _html(plan, public, cases, capabilities, methods, metrics, pairs, figures)
    files = {"index.html": document.encode(), "summary.json": json.dumps(public, indent=2, allow_nan=False, ensure_ascii=False).encode(),
             "cases.csv": _csv_bytes(cases, CASE_FIELDS), "capabilities.csv": _csv_bytes(capabilities, CAPABILITY_FIELDS),
             "methods.csv": _csv_bytes(methods, ("tool_name","driver","target_ids","unsupported_estimation_targets","estimation_limits","qualification")),
             "runs.csv": _csv_bytes(public["run_rows"], RUN_FIELDS), "metrics.csv": _csv_bytes(metrics, METRIC_FIELDS),
             "comparisons.csv": _csv_bytes(pairs, PAIR_EXPORT_FIELDS), "target_results.csv": _csv_bytes(public["target_rows"], TARGET_RESULT_FIELDS),
             "safe_estimates.csv": _csv_bytes(public["safe_rows"], ("attempt_id", *sorted(PUBLIC_SAFE_ROW_FIELDS))),
             "errors.csv": _csv_bytes([*public["error_rows"], *public["problems"]], ERROR_FIELDS)}
    for index, figure in enumerate(figures, 1):
        files[f"figure-{index:02}.svg"] = figure["svg"].encode()
    if sum(map(len, files.values())) > MAX_OUTPUT_BYTES:
        raise ValueError("Biology report exceeds the bounded output size; partition the report")
    if any(plan.blinding_nonce.encode() in payload for payload in files.values()):
        raise ValueError("Public report must not contain private plan security metadata")
    destination = _write_fresh(output_dir, files)
    keys = {name: "html" if name == "index.html" else name.rsplit('.', 1)[0] for name in files}
    return {"paths": {keys[name]: str(destination / name) for name in files},
            "hashes": {keys[name]: hashlib.sha256(payload).hexdigest() for name, payload in files.items()},
            "status": public["status"], "privacy_status": "public_summary", "figure_count": len(figures),
            "output_bytes": sum(map(len, files.values()))}


_JS = """
(() => {
 const controls = [...document.querySelectorAll('[data-filter]')];
 function filterTables() {
  const selected = controls.filter(control => control.value);
  document.querySelectorAll('table[data-filterable]').forEach(table => {
   let visible = 0;
   table.querySelectorAll('tbody tr').forEach(row => {
    row.hidden = !selected.every(control => row.getAttribute('data-' + control.dataset.filter) === control.value);
    if (!row.hidden) visible += 1;
   });
   const notice = table.closest('.table-wrap').nextElementSibling;
   if (notice && notice.classList.contains('filter-empty')) notice.hidden = visible > 0;
  });
 }
 controls.forEach(control => control.addEventListener('change', filterTables));
 document.getElementById('reset-filters').addEventListener('click', () => {
  controls.forEach(control => { control.value = ''; }); filterTables();
 });
})();
"""
