"""Bounded, offline coordinator reports; the empty path is configuration only.

No datasets, actor adapters, generators or statistical procedures are imported.
    Nonempty summaries must have been produced separately by ``analyze_attempts``.
Matplotlib is imported only when supplied observations actually support a figure.
"""
from __future__ import annotations

from collections import Counter, defaultdict
import csv
import hashlib
import html
import io
from itertools import islice
import json
import os
from pathlib import Path
import re
from typing import Any

from .contracts import StudyPlan, canonical_json_bytes
from .records import GENE_EXPORT_FIELDS, validate_finite_json

MAX_ROWS = 10_000
MAX_TABLE_ROWS = 250
MAX_FIGURES = 40
MAX_FIGURE_BYTES = 1_500_000
MAX_OUTPUT_BYTES = 20_000_000
MAX_METADATA_NODES = 200_000
CONDITIONS = ("baseline", "optional", "enforced")
COLORS = {"baseline": "#68707a", "optional": "#697f9a", "enforced": "#2c6a61"}
CONTEXT = ("source_kind", "actual_model", "actor_type", "condition")
ARM_DESCRIPTIONS = {
    "baseline": "The same task and resource budget, without access to registered harness tools.",
    "optional": "Registered harness tools are available; the participant chooses whether to use them.",
    "enforced": "Registered harness tools are available; required checks govern report acceptance.",
}
PANEL_SPECS = (
    ("outcomes", "Scientific correctness & report outcomes", "Raw scientific correctness is the primary outcome. Accepted-correct reports require correctness AND report acceptance and are secondary. Workflow compliance is a separate observation.",
     ("raw_scientific_success_rate", "accepted_scientific_success_rate", "report_accepted", "workflow_passed", "correct_answer_rejection_rate", "incorrect_answer_acceptance_rate")),
    ("recovery", "Recovery, precision & false discoveries", "FDP describes an individual output; FDR is the mean dataset FDP among calculable outputs. Missing outputs are reported in the denominator.",
     ("signed_recall", "precision", "fdp", "fdr", "directional_error_rate")),
    ("accuracy", "Effects, trajectories & shape classes", "Effect and trajectory accuracy require a comparable reference and declared units. Public annotations do not provide complete causal truth.",
     ("effect_mae", "effect_rmse", "curve_rmse", "shape_macro_f1")),
    ("calibration", "Statistical calibration & interval quality", "Coverage is interpreted with interval width and interval scope. BH arithmetic checks alone do not establish statistical calibration.",
     ("interval_coverage", "interval_mean_width", "bh_arithmetic_valid")),
    ("diagnostic", "Diagnostics & refusals", "Required diagnostic recognition, correct refusals and false refusals remain separate. A diagnostic refusal is not automatically a failed scientific answer.",
     ("required_diagnostics_correct", "correct_structural_refusal", "incorrect_structural_refusal", "unsupported_population_claim")),
    ("resource", "Resources, failures & unknown usage", "Attempted failures remain in resource accounting. Elapsed time is seconds; RSS_MB retains runtime-defined source units. Cost is USD and unknown for subscription usage unless provided.",
     ("elapsed_sec", "RSS_MB", "tokens", "cost")),
    ("workflow", "Trusted tool adoption & compliance", "Only actual trusted capture establishes tool adoption. Availability, self-reported usage and workflow acceptance are different observations.",
     ("trusted_tool_capture_complete", "trusted_tool_calls", "workflow_passed", "report_accepted")),
)
UNITS = {"elapsed_sec": "seconds", "RSS_MB": "RSS_MB; runtime-defined source units",
         "tokens": "tokens", "cost": "USD", "trusted_tool_calls": "calls",
         "effect_mae": "source effect units; see gene rows", "effect_rmse": "source effect units; see gene rows",
         "curve_rmse": "source trajectory units; see gene rows",
         "interval_mean_width": "source interval units; see gene rows"}
BOUNDED_METRICS = frozenset(name for _, _, _, names in PANEL_SPECS for name in names) - frozenset(UNITS)
RUN_FIELDS = (*CONTEXT, "attempt_id", "study_id", "cohort_id", "scenario", "repetition",
              "attempted", "completion_status", "outcome", "accepted_final", "scientific_success",
              "accepted_scientific_success", "report_accepted", "workflow_passed", "integrity_status",
              "model_identity_verified", "binding_status", "included_in_study_summary", "elapsed_sec",
              "RSS_MB", "tokens", "cost", "accounting_completeness", "trusted_tool_capture_complete",
              "trusted_tool_calls", "diagnostic_outcomes", "refusal_outcomes")
METRIC_FIELDS = (*CONTEXT, "model_identity_verified", "scenario_scope", "metric", "value", "units", "available_count", "denominator",
                 "missing_count", "missing_reason", "known_subtotal", "complete_count", "per_success", "per_success_estimand",
                 "per_accepted_scientific_success", "status")
COMPARISON_FIELDS = ("source_kind", "actual_model", "actor_type", "model_identity_verified", "contrast", "primary", "estimand", "outcome_role", "contrast_role",
                     "mean_delta", "ci_low", "ci_high", "confidence_level", "units", "paired_repetition_count",
                     "paired_cohort_count", "independent_group_count", "pairing_coverage", "status", "interval_reason")
ERROR_FIELDS = ("code", "attempt_id", "disposition")
PRIVATE_FIELDS = frozenset({"source_kind", "condition", "actual_model", "gene_id", "true_effect",
                            "estimated_effect", "effect_units", "time_grid", "true_curve", "predicted_curve",
                            "trajectory_units", "true_shape", "predicted_shape"})
GENE_STORAGE_FIELDS = frozenset({"gene_detail_storage", "retained_gene_row_count", "loaded_gene_row_count",
                                 "gene_rows_omitted_from_aggregate"})
SUMMARY_FIELDS = frozenset({
    "record_format", "study_id", "plan_identity", "status", "planned_attempt_count", "record_count", "primary_outcome",
    "planned_model_attempt_count", "planned_control_attempt_count",
    "received_record_count", "attempted_count", "quarantined_record_count", "invalid_evidence_record_count",
    "headline_inference_blocked", "headline", "group_summaries", "paired_comparisons", "outcome_taxonomy",
    "panels", "run_rows", "gene_rows", "error_rows", "problems", "bootstrap", "limitations",
}) | GENE_STORAGE_FIELDS


def _bounded_json(value: Any) -> None:
    """Bound traversal before the shared finite-JSON validator walks metadata."""
    pending = [(value, 0)]
    visited = 0
    while pending:
        item, depth = pending.pop()
        visited += 1
        if depth > 16 or visited > MAX_METADATA_NODES:
            raise ValueError("Report metadata exceeds the bounded size")
        if isinstance(item, dict):
            if len(item) > 1000 or any(not isinstance(key, str) or len(key) > 1000 for key in item):
                raise ValueError("Report mapping exceeds the bounded size")
            pending.extend((entry, depth + 1) for entry in item.values())
        elif isinstance(item, (list, tuple)):
            if len(item) > MAX_ROWS:
                raise ValueError("Report rows exceed the bounded size; partition the report")
            pending.extend((entry, depth + 1) for entry in item)
    validate_finite_json(value)


def _public(value: Any) -> Any:
    """Defensive removal supplements explicit plan/run/gene export projections."""
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            lower = key.lower()
            if (any(token in lower for token in ("nonce", "generation_seed", "recipe", "truth", "private", "reference", "golden"))
                    or lower in {"original_record", "expected_binding", "timepoint_labels", "batch_labels"}
                    or lower == "path" or lower.endswith(("_path", "_paths")) or lower.startswith("true_")):
                continue
            result[key] = _public(item)
        return result
    if isinstance(value, (list, tuple)):
        return [_public(item) for item in value]
    if isinstance(value, str):
        # No file system locator is needed in an offline scientific summary.
        if re.search(r"(?:^|[\s\"'=])(?:/[^/\s]|[A-Za-z]:[\\/]|file:/{2}|~/)", value):
            return "[source location omitted]"
    return value


def _project(row, names):
    return {name: row.get(name) for name in names}


def _validate_gene_storage(summary):
    """Validate only receipt metadata; never locate or load stored gene details."""
    if not any(name in summary for name in GENE_STORAGE_FIELDS):
        return
    if (not GENE_STORAGE_FIELDS <= summary.keys()
            or summary["gene_detail_storage"] != "per_attempt"):
        raise ValueError("Per-attempt gene storage metadata must be complete")
    retained, loaded = summary["retained_gene_row_count"], summary["loaded_gene_row_count"]
    if any(type(count) is not int or not 0 <= count < 2**63 for count in (retained, loaded)):
        raise ValueError("Retained and loaded gene counts must be nonnegative bounded integers")
    if loaded != len(summary.get("gene_rows", [])) or retained < loaded:
        raise ValueError("Loaded gene count must match report rows and cannot exceed retained rows")
    omitted = summary["gene_rows_omitted_from_aggregate"]
    if type(omitted) is not bool or (not omitted and retained != loaded):
        raise ValueError("Aggregate gene omission metadata must agree with retained and loaded counts")


def _gene_details(summary):
    rows = summary.get("gene_rows", [])
    title = '<section id="gene-details"><h3>Gene estimates · public projection</h3>'
    storage = summary.get("gene_detail_storage") == "per_attempt"
    notice = ""
    if storage:
        retained, loaded = summary["retained_gene_row_count"], summary["loaded_gene_row_count"]
        state = "Gene details stored separately / not loaded" if not loaded else "Gene details stored separately / selected rows loaded"
        notice = (f'<div class="notice"><b>{state}</b> Retained rows: {retained:,} · Loaded rows: {loaded:,}. '
                  'Gene details remain in per-attempt coordinator artifacts. The gene CSV includes only rows loaded into this report; scalar metrics retain their own evidence scope.</div>')
        if not rows:
            return title + notice + '<p class="note">No gene details loaded into this report. This describes report storage, not whether a gene analysis ran.</p></section>'
    return title + notice + _table(rows, ('attempt_id', 'gene_id', 'status', 'missing_reason', 'p_value', 'q_value',
                                         'effect', 'effect_units', 'shape'), label='Gene estimates') + '</section>'


def _format(value):
    if value is None:
        return "Unknown"
    if type(value) is bool:
        return "Yes" if value else "No"
    if type(value) is float:
        return f"{value:.4g}"
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    return str(value)


def _escape(value):
    return html.escape(_format(value), quote=True)


def _csv_bytes(rows, fields):
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    for row in rows:
        safe = {}
        for field in fields:
            value = row.get(field)
            text = "" if value is None else _format(value)
            # Prevent spreadsheet formula execution when source CSVs are opened.
            if isinstance(value, str) and text.lstrip().startswith(("=", "+", "-", "@")):
                text = "'" + text
            safe[field] = text
        writer.writerow(safe)
    return output.getvalue().encode("utf-8")


def _metric_rows(summary, plan):
    rows = []
    names = tuple(dict.fromkeys(name for _, _, _, fields in PANEL_SPECS for name in fields))
    for group in summary.get("group_summaries", []):
        context = _project(group, CONTEXT)
        context["model_identity_verified"] = group.get("model_identity_verified")
        scenario_scope = group.get("scenario", "Observed cohorts and scenarios within this source/model/actor scope")
        for name in names:
            measurement = group.get("resources", {}).get(name) if name in ("elapsed_sec", "RSS_MB", "tokens", "cost") else group.get("metrics", {}).get(name)
            measurement = measurement or {}
            value = measurement.get("total" if name in ("elapsed_sec", "RSS_MB", "tokens", "cost") else "mean")
            denominator = measurement.get("denominator", group.get("attempted_count"))
            available = measurement.get("available_count")
            if name in group:
                value = group[name]
                if name == "fdr":
                    available = group.get("fdr_available_outputs")
                elif name in ("accepted_scientific_success_rate", "raw_scientific_success_rate"):
                    available = denominator if value is not None else None
                elif name == "correct_answer_rejection_rate":
                    denominator = group.get("correct_answer_report_decision_count")
                    available = denominator if value is not None else None
                elif name == "incorrect_answer_acceptance_rate":
                    denominator = group.get("incorrect_answer_report_decision_count")
                    available = denominator if value is not None else None
            rows.append({**context, "scenario_scope": scenario_scope, "metric": name, "value": value,
                         "units": UNITS.get(name, "proportion"), "available_count": available,
                         "denominator": denominator,
                         "missing_count": denominator - available if type(denominator) is int and type(available) is int else None,
                         "missing_reason": measurement.get("missing_reason") or ("metric_not_available_or_not_applicable" if value is None else None),
                         "known_subtotal": measurement.get("known_subtotal"), "complete_count": measurement.get("complete_count"),
                         "per_success": measurement.get("per_success"),
                         "per_success_estimand": measurement.get("per_success_estimand"),
                         "per_accepted_scientific_success": measurement.get("per_accepted_scientific_success"),
                         "status": measurement.get("status", "available" if value is not None else "unavailable")})
    if len(rows) > MAX_ROWS:
        raise ValueError("Metric table exceeds the bounded size")
    return rows


def _comparison_rows(summary, plan):
    rows = []
    for comparison in summary.get("paired_comparisons", []):
        groups = comparison.get("groups", [])
        for group in groups:
            for estimand, prefix, role in (("raw_scientific_success", "raw_scientific", "primary"),
                                          ("accepted_scientific_success", "accepted_scientific", "secondary")):
                main = group.get("estimand") == estimand
                ci = group.get("confidence_interval" if main else f"{prefix}_confidence_interval")
                label = "raw scientific correctness" if role == "primary" else "accepted-correct reports"
                rows.append({**_project(group, COMPARISON_FIELDS), "contrast": comparison.get("contrast"),
                             "estimand": estimand, "outcome_role": role,
                             "contrast_role": "primary" if comparison.get("primary") else "exploratory",
                             "primary": bool(comparison.get("primary") and role == "primary"),
                             "mean_delta": group.get("mean_delta" if main else f"{prefix}_mean_delta"),
                             "interval_reason": group.get("interval_reason" if main else f"{prefix}_interval_reason"),
                             "ci_low": ci[0] if ci else None, "ci_high": ci[1] if ci else None,
                             "confidence_level": plan.thresholds.confidence_level,
                             "units": f"difference in {label} proportion"})
    return rows


def _table(rows, fields, *, label):
    if not rows:
        return '<div class="empty compact">Not run <span>No observed rows in this scope.</span></div>'
    header = "".join(f'<th scope="col">{_escape(field.replace("_", " "))}</th>' for field in fields)
    body = []
    for row in rows[:MAX_TABLE_ROWS]:
        attrs = " ".join(f'data-{name.replace("_", "-")}="{_escape(row.get(name, ""))}"'
                         for name in (*CONTEXT, "scenario"))
        cells = "".join(f"<td>{_escape(row.get(field))}</td>" for field in fields)
        body.append(f"<tr {attrs}>{cells}</tr>")
    bound = f"Showing at most {MAX_TABLE_ROWS} rows; CSV contains all {len(rows)} rows." if len(rows) > MAX_TABLE_ROWS else f"{len(rows)} source rows."
    return (f'<p class="table-count">{bound}</p><div class="table-wrap"><table aria-label="{_escape(label)}" data-filterable>'
            f"<thead><tr>{header}</tr></thead><tbody>{''.join(body)}</tbody></table></div>"
            '<p class="filter-empty" hidden>No visible table rows match these filters.</p>')


def _svg(figure):
    output = io.StringIO()
    figure.savefig(output, format="svg", metadata={"Date": None}, bbox_inches="tight")
    svg = output.getvalue()
    svg = svg[svg.index("<svg"):]
    if len(svg.encode("utf-8")) > MAX_FIGURE_BYTES:
        raise ValueError("Scientific figure exceeds the bounded size")
    return svg


def _figures(metric_rows, comparisons, private_rows):
    """Future-use plotting only; no figure or plotting import for an empty report."""
    numeric = [row for row in metric_rows if type(row.get("value")) in (float, int)]
    intervals = [row for row in comparisons if type(row.get("mean_delta")) in (float, int)]
    if not numeric and not intervals and not private_rows:
        return []
    from matplotlib import rc_context
    from matplotlib.figure import Figure

    figures = []

    def add(panel, caption, figure):
        if len(figures) >= MAX_FIGURES:
            raise ValueError("Report exceeds the bounded figure count; partition source/model scopes")
        figures.append({"panel": panel, "caption": caption, "svg": _svg(figure)})
        figure.clear()

    with rc_context({"svg.fonttype": "none", "text.usetex": False, "axes.spines.top": False,
                     "axes.spines.right": False, "font.size": 10, "axes.labelcolor": "#3e454e"}):
        scoped = defaultdict(list)
        for row in numeric:
            scoped[tuple(row.get(name) for name in (*CONTEXT[:3], "model_identity_verified"))].append(row)
        for scope, rows in scoped.items():
            for panel, title, _, names in PANEL_SPECS:
                present = [name for name in names if any(row["metric"] == name for row in rows)]
                if not present:
                    continue
                figure = Figure(figsize=(max(4.5, len(present) * 2.7), 3.4))
                for index, name in enumerate(present):
                    axis = figure.add_subplot(1, len(present), index + 1)
                    selected = [row for row in rows if row["metric"] == name]
                    values = [row["value"] for row in selected]
                    axis.scatter([CONDITIONS.index(row["condition"]) for row in selected], values,
                                 c=[COLORS[row["condition"]] for row in selected], s=46)
                    axis.set_xticks(range(3), CONDITIONS, rotation=25)
                    axis.set_xlim(-0.45, 2.45)
                    if name in BOUNDED_METRICS:
                        axis.set_ylim(0, 1)
                    else:
                        lower, upper = min(0, min(values)), max(values)
                        padding = max((upper - lower) * 0.12, abs(upper) * 0.1, 0.01)
                        axis.set_ylim(lower - padding if lower else 0, upper + padding)
                    axis.set_title(name.replace("_", " "), fontsize=9, wrap=True)
                    axis.set_ylabel(UNITS.get(name, "Proportion; fixed bounds: 0 to 1"), fontsize=8)
                    axis.grid(axis="y", alpha=0.18)
                figure.tight_layout()
                add(panel, f"{title} · {' / '.join(_format(item) for item in scope[:3])} · model identity verified: {_format(scope[3])}. Common axis bounds across arms for each metric. Probability metrics use fixed bounds: 0 to 1. Missing estimates are omitted, never imputed.", figure)
        for row in intervals:
            figure = Figure(figsize=(6.8, 2.4))
            axis = figure.add_subplot()
            estimate = row["mean_delta"]
            low, high = row.get("ci_low"), row.get("ci_high")
            axis.scatter([estimate], [0], color=COLORS["enforced"])
            if low is not None and high is not None:
                axis.hlines(0, low, high, color=COLORS["enforced"], linewidth=2)
            axis.axvline(0, color="#adb4bb", linestyle="--")
            axis.set_xlim(-1, 1)
            axis.set_yticks([0], [row["contrast"]])
            outcome = "raw scientific correctness" if row["estimand"] == "raw_scientific_success" else "accepted-correct reports"
            axis.set_xlabel(f"Paired {outcome} gain (proportion difference)")
            confidence = 100 * row["confidence_level"]
            add("outcomes", f"{row['outcome_role'].title()} outcome: {outcome} · {row['contrast']} ({row['contrast_role']} contrast) · {row.get('source_kind')} / {row.get('actual_model')} / {row.get('actor_type')}. Supplied {confidence:g}% CI; interval unavailable reason: {row.get('interval_reason') or 'none'}.", figure)
        _private_figures(private_rows, Figure, add)
    return figures


def _private_figures(rows, Figure, add):
    scoped = defaultdict(list)
    for row in rows:
        scoped[(row.get("actual_model"), row.get("effect_units"))].append(row)
    for (model, units), group in scoped.items():
        effects = [row for row in group if type(row.get("true_effect")) in (float, int)
                   and type(row.get("estimated_effect")) in (float, int)]
        if effects:
            figure = Figure(figsize=(5.5, 4.2))
            axis = figure.add_subplot()
            values = [row[name] for row in effects for name in ("true_effect", "estimated_effect")]
            pad = max((max(values) - min(values)) * 0.08, 0.01)
            bounds = (min(values) - pad, max(values) + pad)
            for condition in CONDITIONS:
                selected = [row for row in effects if row["condition"] == condition]
                if selected:
                    axis.scatter([row["true_effect"] for row in selected], [row["estimated_effect"] for row in selected],
                                 label=condition, color=COLORS[condition], s=16, alpha=0.65)
            axis.plot(bounds, bounds, color="#b3b8bd", linestyle="--")
            axis.set(xlim=bounds, ylim=bounds, xlabel=f"Synthetic true effect ({units or 'units unspecified'})",
                     ylabel=f"Estimated effect ({units or 'units unspecified'})")
            axis.legend()
            add("accuracy", f"Private synthetic reference · {model} · {len(effects)} supplied gene estimates; common x/y bounds.", figure)
        trajectories = [row for row in group if row.get("time_grid") and row.get("true_curve") and row.get("predicted_curve")]
        trajectory_bounds = defaultdict(list)
        for row in trajectories:
            times, true_curve, predicted = row["time_grid"], row["true_curve"], row["predicted_curve"]
            if not len(times) == len(true_curve) == len(predicted) or len(times) > 100:
                raise ValueError("Private trajectory arrays require equal bounded lengths")
            if any(type(value) not in (int, float) for values in (times, true_curve, predicted) for value in values):
                raise ValueError("Private trajectory values must be finite numbers")
            trajectory_bounds[(row.get("gene_id"), row.get("trajectory_units"))].append(
                (min(times), max(times), min((*true_curve, *predicted)), max((*true_curve, *predicted))))
        for row in trajectories[:6]:
            times, true_curve, predicted = row["time_grid"], row["true_curve"], row["predicted_curve"]
            figure = Figure(figsize=(6.0, 3.3))
            axis = figure.add_subplot()
            axis.plot(times, true_curve, color="#313940", label="Synthetic true trajectory")
            axis.plot(times, predicted, color=COLORS[row["condition"]], linestyle="--", label="Inferred trajectory")
            axis.set_xlabel("Supplied time grid (source units)")
            axis.set_ylabel(row.get("trajectory_units") or "Source trajectory units (unspecified)")
            bounds = trajectory_bounds[(row.get("gene_id"), row.get("trajectory_units"))]
            left, right = min(item[0] for item in bounds), max(item[1] for item in bounds)
            bottom, top = min(item[2] for item in bounds), max(item[3] for item in bounds)
            xpad, ypad = max((right-left)*0.05, 0.01), max((top-bottom)*0.08, 0.01)
            axis.set_xlim(left-xpad, right+xpad)
            axis.set_ylim(bottom-ypad, top+ypad)
            axis.legend()
            add("accuracy", f"Private synthetic reference · {model} / {row['condition']} / {row.get('gene_id')}. Common bounds across supplied arms for this gene and units. Display bounded to the first six supplied trajectories per model/effect-unit scope.", figure)


def _document(plan, summary, planned_rows, metrics, comparisons, figures, privacy):
    planned = len(plan.cohorts) * plan.repetitions * len(CONDITIONS)
    executed = summary.get("attempted_count", 0)
    confidence_label = f"{100 * plan.thresholds.confidence_level:g}%"
    rows = summary.get("run_rows", [])
    requested = plan.requested_model or "Not selected"
    cards = "".join(f'<article class="arm"><div class="arm-top"><span class="dot {arm}"></span><h3>{arm.title()}</h3>'
                    f'<span>{planned // 3:,} planned</span></div><p>{description}</p></article>'
                    for arm, description in ARM_DESCRIPTIONS.items())
    panels = []
    for index, (key, title, description, names) in enumerate(PANEL_SPECS, 1):
        matching = [row for row in metrics if row["metric"] in names]
        plots = "".join(f'<figure>{figure["svg"]}<figcaption>{_escape(figure["caption"])}</figcaption></figure>'
                        for figure in figures if figure["panel"] == key)
        if not any(row.get("value") is not None for row in matching) and not plots:
            plots = '<div class="empty"><span class="empty-mark">—</span><strong>Not run</strong><p>Scientific performance is unknown. No observations support a curve, score or interval.</p></div>'
        details = ""
        if key == "outcomes":
            for role, outcome, prefix in (("Primary", "raw scientific correctness", "raw_scientific"),
                                          ("Secondary", "accepted-correct report", "accepted_scientific")):
                reasons = "".join(f'<li><b>{_escape(item.get("contrast"))}</b><span>{_escape(item.get("status"))} · '
                                  f'{_escape(item.get("interval_reason") if item.get("estimand") == f"{prefix}_success" else item.get(f"{prefix}_interval_reason"))}</span></li>'
                                  for item in summary.get("paired_comparisons", []))
                details += f'<h3>{role}: paired {outcome} gain · {confidence_label} CI</h3><ul class="comparison-status">{reasons}</ul>'
            details += (
                       '<p class="note">Primary: enforced − baseline. Exploratory: optional − baseline and enforced − optional. '
                       'Cluster intervals require sufficient independent cohorts and valid matching; repetitions do not add biological independence.</p>'
                       + _table(comparisons, COMPARISON_FIELDS, label="Paired comparisons")
                       + '<p class="note">Raw scientific correctness is reported separately from report acceptance and workflow compliance. A change in report acceptance alone does not establish improved scientific correctness. No combined clinical-readiness score is computed.</p>')
            details += '<p class="note">Group success rates average repetitions within cohorts, then weight observed cohorts and scenarios equally. Full attempted-run counts and unweighted rates remain in the summary export; they answer a different descriptive question.</p>'
        if key == "accuracy":
            details += '<p class="note">Shape classes are available in the per-gene source table when observed. True-versus-estimated effects and trajectories require an explicitly enabled private coordinator reference report.</p>'
        panels.append(f'<section class="panel" id="{key}"><div class="section-title"><span>{index:02}</span><h2>{title}</h2></div>'
                      f'<p class="description">{description}</p>{plots}{details}'
                      + (f'<details><summary>Metric source rows & missingness</summary>{_table(matching, METRIC_FIELDS, label=title)}</details>' if matching else "") + '</section>')
    options = {}
    for field in (*CONTEXT, "scenario"):
        labels = sorted({_format(row[field]) for row in [*rows, *metrics, *planned_rows] if row.get(field) is not None})
        options[field] = ''.join(f'<option value="{_escape(label)}">{_escape(label)}</option>' for label in labels)
    filters = ''.join(
        f'<div class="filter-field"><label for="filter-{field.replace("_", "-")}">{_escape(field.replace("_", " ").title())}</label>'
        f'<select id="filter-{field.replace("_", "-")}" aria-label="{_escape(field.replace("_", " ").title())}" '
        f'data-filter="{field.replace("_", "-")}"><option value="">All</option>{options[field]}</select></div>'
        for field in (*CONTEXT, "scenario"))
    errors = [{**_project(row, ERROR_FIELDS)} for row in summary.get("error_rows", [])]
    problems = ''.join(f'<li>{_escape(item)}</li>' for item in summary.get("problems", []))
    quarantine_count = summary.get("quarantined_record_count", 0)
    inference_notice = ('<div class="notice"><b>Comparative inference blocked.</b> Schedule or actor identity conflicts prevent a headline or superiority claim. Review record dispositions and pairing reasons.</div>'
                        if summary.get("headline_inference_blocked") else "")
    notes = '<li>Research thresholds are provisional and uncalibrated.</li><li>Synthetic reference performance and public processed observations are separate evidence scopes.</li><li>Scripted controls are never pooled with model participants.</li><li>No biological or clinical validation is established.</li>'
    return f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; img-src data:; font-src 'none'; connect-src 'none'; base-uri 'none'; form-action 'none'">
<title>Three-arm study · {_escape(plan.study_id)}</title><style>{_CSS}</style></head>
<body><header class="masthead"><a href="#overview" class="brand">DEVBENCH <span>RESEARCH / STUDY REPORT</span></a><span class="status">{_escape(summary.get('status'))}</span></header>
<main><section id="overview" class="overview"><p class="eyebrow">PROPOSED THREE-ARM STUDY · {_escape(plan.stage.upper())}</p>
<h1>Does DevHarness<br>improve scientific outcomes?</h1>
<p class="lede">Compare the same task under baseline, optional and enforced harness access. Track scientific correctness, report acceptance and the cost of getting there.</p>
<div class="meta"><span>Study <b>{_escape(plan.study_id)}</b></span><span>Requested model <b>{_escape(requested)}</b></span><span>Observed model <b>{'Unknown — not run' if not rows else 'See separate source/model scopes'}</b></span><span>Privacy <b>{privacy}</b></span></div>
<div class="stat-strip"><div><span>Planned model attempts</span><strong data-planned-attempts="{planned}">{planned:,}</strong><small>{len(plan.cohorts)} proposed cohorts × {plan.repetitions} repeats × 3 arms</small></div>
<div><span>Observed attempts in study</span><strong data-executed-attempts="{executed}">{_escape(executed)}</strong><small>Attempted failures stay in the denominator</small></div>
<div><span>Scientific performance</span><strong class="word">{'Unknown' if not executed else 'By scope'}</strong><small>{'Not run; no results inferred' if not executed else 'Separate sources, actors and observed models'}</small></div>
<div><span>Quarantined records</span><strong>{_escape(quarantine_count)}</strong><small>Excluded from current-study results</small></div></div></section>
<div class="notice"><b>Research plan, not a validated result.</b> Planned counts describe scheduling only. Cohort independence and scientific thresholds are unverified.</div>
{inference_notice}
<section class="arm-grid" aria-label="Study conditions">{cards}</section>
<section class="scope"><h2>Scope & source context</h2><p>Requested model settings are configuration. Actual model identity must come from trusted execution evidence. All figures use the full labeled source/model/actor scope; no source or actor pooling is implied.</p>
{_table(planned_rows, ('source_kind', 'scenario', 'planned_cohorts', 'planned_model_attempts', 'independence_status'), label='Planned scope')}</section>
<nav class="panel-nav" aria-label="Evidence panels">{''.join(f'<a href="#{key}">{i:02} {title.split(" & ")[0]}</a>' for i, (key, title, _, _) in enumerate(PANEL_SPECS, 1))}</nav>
<section class="filters"><div><h2>Table-only filters</h2><p>Filters affect displayed table rows only. Figures and headline counts remain unchanged. Rows without a selected context are hidden.</p></div><div class="filter-grid">{filters}<button type="button" id="reset-filters">Reset</button></div></section>
<div class="panels">{''.join(panels)}</div>
<section class="panel" id="source-tables"><div class="section-title"><span>08</span><h2>Source tables & record disposition</h2></div>
<p>CSV exports preserve units, denominators and missingness; blank cells mean unavailable. Visible tables are bounded, while exports include all bounded source rows.</p>
<div class="downloads"><a href="summary.json" download>Summary JSON</a><a href="metrics.csv" download>Metric rows</a><a href="comparisons.csv" download>Paired comparisons</a><a href="runs.csv" download>Attempt ledger</a><a href="genes.csv" download>Gene estimates</a><a href="errors.csv" download>Record disposition</a><a href="planned_scope.csv" download>Planned scope</a></div>
<h3>Attempt ledger</h3>{_table(rows, (*CONTEXT, 'scenario', 'attempt_id', 'binding_status', 'completion_status', 'outcome', 'scientific_success', 'report_accepted', 'workflow_passed', 'elapsed_sec', 'RSS_MB', 'tokens', 'cost'), label='Attempt ledger')}
<h3>Quarantined & invalid evidence records</h3>{_table(errors, ERROR_FIELDS, label='Record disposition')}
{_gene_details(summary)}
<h3>Limitations & diagnostic messages</h3><ul class="limitations">{notes}{problems}</ul></section>
<footer>Offline report · {_escape(plan.study_id)} · {privacy}<span>No external assets. No data acquisition or execution is performed by this report.</span></footer></main>
<script>{_JS}</script></body></html>'''


def _write_fresh(output_dir, files):
    """Walk directory descriptors without symlinks, then exclusively create files."""
    path = Path(output_dir)
    if not path.is_absolute():
        raise ValueError("Output directory must be absolute")
    if '..' in path.parts or path == Path('/'):
        raise ValueError("Output directory must be a fresh absolute child directory")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    parent = os.open('/', flags)
    report_fd = None
    try:
        for component in path.parts[1:-1]:
            next_fd = os.open(component, flags, dir_fd=parent)
            os.close(parent)
            parent = next_fd
        os.mkdir(path.name, mode=0o700, dir_fd=parent)
        report_fd = os.open(path.name, flags, dir_fd=parent)
        for filename, payload in files.items():
            descriptor = os.open(filename, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                 mode=0o600, dir_fd=report_fd)
            with os.fdopen(descriptor, 'wb') as handle:
                handle.write(payload)
    finally:
        if report_fd is not None:
            os.close(report_fd)
        os.close(parent)
    return path


def render_dashboard(plan: StudyPlan, summary: dict | None, output_dir: Path, *,
                     private_reference_rows=None, include_private_reference: bool = False) -> dict:
    """Write a self-contained report and bounded CSV sources into a fresh directory.

    ``None`` calls ``analyze_attempts([], plan)`` only. The caller owns any later
    nonempty evaluation. Private synthetic references require explicit opt-in;
    they are neither discovered on disk nor accepted as public causal truth.
    A verified execution receipt may supply ``gene_detail_storage='per_attempt'``,
    ``retained_gene_row_count``, ``loaded_gene_row_count`` and the boolean
    ``gene_rows_omitted_from_aggregate`` as summary metadata. No artifact paths
    are followed; the gene CSV contains only the supplied ``gene_rows``.
    Return absolute paths, SHA-256 hashes, status and report privacy classification.
    """
    if not isinstance(plan, StudyPlan):
        raise ValueError("A validated StudyPlan is required")
    # Revalidate unchecked model_copy changes before using the plan.
    plan = StudyPlan.model_validate_json(canonical_json_bytes(plan.model_dump(mode="json")))
    if not Path(output_dir).is_absolute():
        raise ValueError("Output directory must be absolute")
    if type(include_private_reference) is not bool:
        raise ValueError("include_private_reference must be a boolean")
    if private_reference_rows is not None and not include_private_reference:
        raise ValueError("Private reference rows require include_private_reference=True")
    if summary is None:
        from .metrics import analyze_attempts
        summary = analyze_attempts([], plan)
    if not isinstance(summary, dict):
        raise ValueError("A study summary dictionary is required")
    _bounded_json(summary)
    if summary.get("study_id") != plan.study_id or summary.get("plan_identity") != plan.sha256():
        raise ValueError("Summary belongs to a different study plan")
    _validate_gene_storage(summary)
    public = _public({key: value for key, value in summary.items() if key in SUMMARY_FIELDS})
    public["run_rows"] = [_project(row, RUN_FIELDS) for row in public.get("run_rows", [])]
    public["gene_rows"] = [{key: value for key, value in row.items() if key in GENE_EXPORT_FIELDS or key == "attempt_id"}
                           for row in public.get("gene_rows", [])]
    private = []
    if include_private_reference and private_reference_rows is not None:
        private = list(islice(private_reference_rows, MAX_ROWS + 1))
        _bounded_json(private)
        for row in private:
            if not isinstance(row, dict) or set(row) - PRIVATE_FIELDS or row.get("source_kind") != "synthetic":
                raise ValueError("Private reference rows require whitelisted synthetic reference fields")
            if row.get("condition") not in CONDITIONS:
                raise ValueError("Private reference rows require an explicit condition")
        private = [{key: _public(value) for key, value in row.items()} for row in private]
    privacy = "coordinator_reference_report" if include_private_reference else "public_summary"
    public["privacy_status"] = privacy
    public["units"] = UNITS
    public["display_contract"] = {"table_filters_only": True, "max_visible_table_rows": MAX_TABLE_ROWS,
                                  "missing_numeric_values": "unknown_not_zero",
                                  "gene_csv_scope": "loaded_gene_rows_only",
                                  "public_annotations": "incomplete_observations_not_complete_causal_truth"}
    recipe_source = {recipe.recipe_id: recipe.source_kind for recipe in plan.dataset_recipes}
    planned_counts = Counter((recipe_source[cohort.recipe_id], cohort.scenario) for cohort in plan.cohorts)
    planned_rows = [{"source_kind": source, "scenario": scenario, "planned_cohorts": count,
                     "planned_model_attempts": count * plan.repetitions * 3,
                     "independence_status": "planned_not_verified"}
                    for (source, scenario), count in sorted(planned_counts.items())]
    metrics = _metric_rows(public, plan)
    comparisons = _comparison_rows(public, plan)
    figures = _figures(metrics, comparisons, private)
    document = _document(plan, public, planned_rows, metrics, comparisons, figures, privacy)
    gene_fields = ("attempt_id", *sorted(GENE_EXPORT_FIELDS))
    files = {
        "index.html": document.encode("utf-8"),
        "summary.json": json.dumps(public, indent=2, ensure_ascii=False, allow_nan=False).encode("utf-8"),
        "runs.csv": _csv_bytes(public.get("run_rows", []), RUN_FIELDS),
        "genes.csv": _csv_bytes(public.get("gene_rows", []), gene_fields),
        "metrics.csv": _csv_bytes(metrics, METRIC_FIELDS),
        "comparisons.csv": _csv_bytes(comparisons, COMPARISON_FIELDS),
        "errors.csv": _csv_bytes(public.get("error_rows", []), ERROR_FIELDS),
        "planned_scope.csv": _csv_bytes(planned_rows, ("source_kind", "scenario", "planned_cohorts", "planned_model_attempts", "independence_status")),
    }
    if include_private_reference:
        files["private_reference.csv"] = _csv_bytes(private, sorted(PRIVATE_FIELDS))
    for index, figure in enumerate(figures, 1):
        files[f"figure-{index:02}.svg"] = figure["svg"].encode("utf-8")
    if sum(len(payload) for payload in files.values()) > MAX_OUTPUT_BYTES:
        raise ValueError("Report output exceeds the bounded size; partition the report")
    destination = _write_fresh(output_dir, files)
    keys = {filename: "html" if filename == "index.html" else filename.rsplit('.', 1)[0] for filename in files}
    return {"paths": {keys[name]: str(destination / name) for name in files},
            "hashes": {keys[name]: hashlib.sha256(payload).hexdigest() for name, payload in files.items()},
            "privacy_status": privacy, "status": public.get("status"), "figure_count": len(figures),
            "output_bytes": sum(map(len, files.values()))}


_CSS = """
:root{color-scheme:light;--ink:#20262c;--muted:#636e77;--line:#dce1e4;--paper:#fff;--wash:#f5f6f7;--accent:#2c6a61}
*{box-sizing:border-box}html{scroll-behavior:smooth;scroll-padding-top:24px}body{margin:0;background:#f9fafb;color:var(--ink);font-family:'Helvetica Neue',Helvetica,sans-serif;font-size:14px;line-height:1.55}a{color:inherit;text-underline-offset:3px}a:focus-visible,button:focus-visible,select:focus-visible,summary:focus-visible{outline:3px solid #377eaa;outline-offset:4px}.masthead{display:flex;align-items:center;justify-content:space-between;border-bottom:1px solid var(--line);padding:20px 5vw;background:#fff}.brand{font-size:15px;font-weight:750;letter-spacing:.1em;text-decoration:none}.brand span{font-size:10px;font-weight:500;color:var(--muted);margin-left:22px;letter-spacing:.13em}.status{background:#f0f2f3;border:1px solid var(--line);border-radius:4px;font-size:11px;padding:4px 10px}main{max-width:1440px;padding:50px 5vw 28px;margin:auto}.eyebrow{font-size:10px;letter-spacing:.14em;color:var(--accent);font-weight:700;margin:0 0 17px}h1{font-family:Georgia,'Times New Roman',serif;font-size:clamp(34px,4.1vw,58px);font-weight:400;line-height:1.1;letter-spacing:-.028em;margin:0 0 22px}.lede{color:var(--muted);font-size:16px;max-width:770px;margin-bottom:25px}.meta{display:flex;flex-wrap:wrap;gap:10px 26px;color:var(--muted);font-size:11px}.meta b{font-weight:500;color:var(--ink);margin-left:7px}.stat-strip{display:grid;grid-template-columns:repeat(4,1fr);margin:32px 0 26px;border:1px solid var(--line);border-radius:6px;background:#fff}.stat-strip>div{padding:20px 22px;border-right:1px solid var(--line)}.stat-strip>div:last-child{border:0}.stat-strip span,.stat-strip small{display:block;font-size:11px;color:var(--muted)}.stat-strip strong{display:block;font-size:38px;line-height:1.5;font-weight:500;font-variant-numeric:tabular-nums}.stat-strip .word{font-family:Georgia,serif;font-size:29px;line-height:1.96}.notice{border-left:3px solid #9c854e;padding:12px 17px;background:#f6f3ec;font-size:12px;color:#62573f}.notice b{font-weight:600;margin-right:5px}.arm-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:14px;margin:25px 0 30px}.arm{background:#fff;border:1px solid var(--line);border-radius:6px;padding:18px}.arm-top{display:flex;align-items:center;gap:8px}.arm-top span:last-child{margin-left:auto;color:var(--muted);font-size:10px}.arm h3{font-size:14px;margin:0;font-weight:600}.arm p{font-size:12px;margin:12px 0 0;color:var(--muted)}.dot{width:7px;height:7px;border-radius:50%}.baseline{background:#68707a}.optional{background:#697f9a}.enforced{background:#2c6a61}h2{font-weight:600;font-size:17px;margin:0}h3{font-weight:600;font-size:13px;margin:22px 0 10px}.scope{margin-bottom:26px}.scope>p,.filters p{font-size:12px;color:var(--muted);max-width:950px;margin:8px 0 14px}.table-wrap{overflow:auto;border:1px solid var(--line);border-radius:4px;background:#fff}table{border-collapse:collapse;min-width:100%;text-align:left;font-size:11px;font-variant-numeric:tabular-nums}th{background:#f3f5f6;font-weight:600;white-space:nowrap;color:#53606b;padding:9px 12px;border-bottom:1px solid var(--line)}td{padding:9px 12px;border-bottom:1px solid #e9edef;max-width:330px;overflow-wrap:anywhere;min-width:90px;vertical-align:top}tbody tr:last-child td{border-bottom:0}tbody tr:hover{background:#f6f9f8}.table-count{font-size:10px;color:var(--muted);margin:7px 0}.panel-nav{display:flex;flex-wrap:wrap;gap:10px 20px;padding:16px 0;border-block:1px solid var(--line);margin:18px 0 25px}.panel-nav a{font-size:11px;text-decoration:none;color:#586871}.panel-nav a:hover{color:var(--accent)}.filters{margin-bottom:22px}.filter-grid{display:grid;grid-template-columns:repeat(5,minmax(0,1fr)) auto;gap:10px;align-items:end}.filter-grid label{font-size:10px;color:var(--muted);text-transform:capitalize}.filter-grid select{display:block;width:100%;margin-top:4px;border:1px solid #c7ced3;border-radius:4px;background:white;font:inherit;font-size:12px;padding:8px;color:var(--ink)}button{padding:8px 13px;border:1px solid #c7ced3;border-radius:4px;background:white;font-size:12px;cursor:pointer;min-height:36px}.panel{border:1px solid var(--line);border-radius:6px;padding:25px;background:#fff;margin-bottom:18px;min-width:0}.section-title{display:flex;align-items:center;gap:12px}.section-title>span{font-size:10px;color:var(--muted);font-variant-numeric:tabular-nums}.description{font-size:12px;color:var(--muted);max-width:1020px;margin:10px 0 18px}.empty{min-height:128px;border:1px dashed #d9dfe3;background:#fafbfc;display:flex;flex-direction:column;align-items:center;justify-content:center;color:var(--muted);padding:17px;text-align:center;border-radius:4px}.empty-mark{font-family:Georgia,serif;color:#9ca7b0;font-size:25px;line-height:1}.empty strong{font-size:13px;font-weight:500;margin-top:8px}.empty p{font-size:11px;margin:6px 0 0}.empty.compact{min-height:62px;font-size:12px}.empty.compact span{font-size:10px;display:block;margin-top:3px}.note{font-size:11px;color:var(--muted);margin-top:14px}.comparison-status{list-style:none;padding:0;border-top:1px solid var(--line)}.comparison-status li{display:flex;justify-content:space-between;gap:15px;padding:9px 0;border-bottom:1px solid #edf0f2;font-size:11px}.comparison-status b{font-weight:500}.comparison-status span{color:var(--muted)}figure{margin:20px 0}figure svg{max-width:100%;height:auto;display:block}figcaption{font-size:10px;color:var(--muted);margin:9px 0 0}details{margin-top:18px}summary{cursor:pointer;font-size:11px;color:var(--accent);padding:8px 0}.downloads{display:flex;flex-wrap:wrap;gap:8px;margin:18px 0}.downloads a{font-size:11px;padding:6px 10px;border:1px solid var(--line);border-radius:4px;text-decoration:none}.limitations{font-size:11px;color:var(--muted);padding-left:17px}.limitations li{margin:7px 0}.filter-empty{font-size:12px;color:var(--muted)}[hidden]{display:none!important}footer{font-size:10px;color:var(--muted);display:flex;justify-content:space-between;gap:20px;padding-top:12px}footer span{text-align:right}@media(max-width:850px){main{padding:30px 20px}.masthead{padding:16px 20px}.brand span{display:none}.stat-strip{grid-template-columns:repeat(2,1fr)}.stat-strip>div{border-bottom:1px solid var(--line)}.stat-strip>div:nth-child(2){border-right:0}.arm-grid{grid-template-columns:1fr}.filter-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.panel{padding:18px}.comparison-status li{flex-direction:column;gap:3px}footer{display:block}footer span{display:block;text-align:left;margin-top:8px}}@media(prefers-reduced-motion:reduce){html{scroll-behavior:auto}}@media print{body{background:white}.filters,.panel-nav,.downloads{display:none}.panel{break-inside:avoid}.masthead{padding:12px 0}main{padding:20px 0}details{display:block}details>summary{display:none}}
"""

_JS = """
(() => {
  const filters = [...document.querySelectorAll('[data-filter]')];
  function apply() {
    const active = filters.filter(control => control.value);
    document.querySelectorAll('table[data-filterable]').forEach(table => {
      let visible = 0;
      table.querySelectorAll('tbody tr').forEach(row => {
        const matches = active.every(control => row.getAttribute('data-' + control.dataset.filter) === control.value);
        row.hidden = !matches;
        if (matches) visible += 1;
      });
      const notice = table.closest('.table-wrap').nextElementSibling;
      if (notice && notice.classList.contains('filter-empty')) notice.hidden = visible !== 0;
    });
  }
  filters.forEach(control => control.addEventListener('change', apply));
  document.getElementById('reset-filters').addEventListener('click', () => {
    filters.forEach(control => { control.value = ''; }); apply();
  });
})();
"""
