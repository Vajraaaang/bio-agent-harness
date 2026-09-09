"""Deterministic empirical summaries from observed outcomes and captured evidence.

Comparisons require matching task and data fingerprints. Missing biological
evidence is unknown, never an assumed zero failure rate. Context associations
are observational; no model judge or causal-effect estimator is used here.
"""
import math
from collections import Counter, defaultdict
from typing import Optional

import numpy as np
from scipy.stats import binomtest

_GROUP_FIELDS = ("model", "backend", "condition", "track", "source_kind")
_BOOTSTRAP_SEED = 1729
_BOOTSTRAP_REPLICATES = 4000
_COLLINEAR_CODES = {"confounded", "confounding", "rank_deficient", "singular_design", "collinear"}
_TRAP_CODES = {
    "oversmoothing_ignored": {"oversmoothing", "oversmoothed", "over_smoothed", "overcorrection"},
    "invalid_contrast_ignored": {"invalid_contrast", "invalid_design", "unidentifiable_contrast"},
}


def _number(value, minimum=0):
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value) and value >= minimum)


def _label(value):
    return value if isinstance(value, str) and value.strip() else "unknown"


def _model(row, metadata):
    ids = metadata.get("model_ids")
    if isinstance(ids, list):
        observed = sorted({item for item in ids if isinstance(item, str) and item.strip()})
        if observed:
            return "+".join(observed), "observed_model_ids"
    for field in ("actual_model", "model_id"):
        if _label(metadata.get(field)) != "unknown":
            return metadata[field], "observed_model"
    return _label(row.get("model")), "result_model_label"


def _resource(values):
    known = [float(value) for value in values if _number(value)]
    return {"total": sum(known) if known else None,
            "mean": sum(known) / len(known) if known else None,
            "maximum": max(known) if known else None,
            "reported": len(known), "unknown": len(values) - len(known),
            "coverage": len(known) / len(values) if values else 0.0}


def _normalize(results):
    valid, excluded = [], []
    for index, row in enumerate(results):
        reason = None
        if not isinstance(row, dict):
            reason = "record_is_not_an_object"
        elif not isinstance(row.get("passed"), bool):
            reason = "passed_is_not_a_boolean"
        elif _label(row.get("task_id")) == "unknown":
            reason = "missing_task_id"
        elif not isinstance(row.get("metadata", {}), dict):
            reason = "metadata_is_not_an_object"
        elif row.get("metadata", {}).get("comparison_eligible") is False:
            reason = "task_requires_condition_specific_tools"
        if reason:
            excluded.append({"index": index, "reason": reason})
            continue
        metadata = row.get("metadata", {})
        model, identity_source = _model(row, metadata)
        valid.append({**row, "metadata": metadata, "model": model, "index": index,
                      "model_identity_source": identity_source,
                      "backend": _label(row.get("backend", metadata.get("backend"))),
                      "condition": _label(row.get("condition", metadata.get("condition"))),
                      "track": _label(metadata.get("track")),
                      "source_kind": _label(metadata.get("source_kind"))})
    return valid, excluded


def _summarize_groups(rows):
    grouped = defaultdict(list)
    for row in rows:
        grouped[tuple(row[field] for field in _GROUP_FIELDS)].append(row)
    output = []
    for key, items in sorted(grouped.items()):
        scores = [row.get("score") for row in items if _number(row.get("score"))]
        output.append({**dict(zip(_GROUP_FIELDS, key)), "runs": len(items),
            "passed": sum(row["passed"] for row in items),
            "pass_rate": sum(row["passed"] for row in items) / len(items),
            "mean_score": sum(scores) / len(scores) if scores else None,
            "score_coverage": len(scores) / len(items),
            "model_identity_sources": sorted({row["model_identity_source"] for row in items}),
            **{metric: _resource([row["metadata"].get(metric) for row in items])
               for metric in ("tokens", "cost_usd", "duration_sec", "peak_memory_mb")}})
    return output


def _pair_runs(rows):
    candidates, pairs, excluded = defaultdict(list), [], []

    def exclude(items, reason):
        excluded.extend({"index": item["index"], "task_id": item["task_id"], "reason": reason}
                        for item in items)

    for row in rows:
        meta = row["metadata"]
        repetition = row.get("repetition")
        if row["condition"] not in {"baseline", "assisted"}:
            exclude([row], "unsupported_condition")
        elif row["model"] == "unknown" or row["backend"] == "unknown":
            exclude([row], "missing_model_or_backend")
        elif not isinstance(repetition, int) or isinstance(repetition, bool) or repetition < 0:
            exclude([row], "missing_or_invalid_repetition")
        elif any(_label(meta.get(field)) == "unknown" for field in ("dataset_hash", "task_spec_hash")):
            exclude([row], "missing_fingerprint")
        else:
            candidates[(row["task_id"], row["model"], row["backend"], repetition)].append(row)
    for _, items in sorted(candidates.items()):
        conditions = Counter(row["condition"] for row in items)
        if max(conditions.values()) > 1:
            exclude(items, "duplicate_condition")
        elif len(items) < 2:
            exclude(items, "missing_counterpart")
        elif any(len({row["metadata"][field] for row in items}) > 1
                 for field in ("dataset_hash", "task_spec_hash")):
            exclude(items, "fingerprint_mismatch")
        elif any(len({row[field] for row in items}) > 1 for field in ("track", "source_kind")):
            exclude(items, "stratum_mismatch")
        else:
            pair = {row["condition"]: row for row in items}
            pairs.append((pair["baseline"], pair["assisted"]))
    return pairs, {"paired_runs": 2 * len(pairs), "pairs": len(pairs),
                   "total_valid_runs": len(rows), "excluded_runs": len(excluded),
                   "coverage": 2 * len(pairs) / len(rows) if rows else 0.0,
                   "exclusion_counts": dict(sorted(Counter(item["reason"] for item in excluded).items())),
                   "exclusions": sorted(excluded, key=lambda item: item["index"])}


def _comparison(items, key):
    clusters = defaultdict(list)
    for baseline, assisted in items:
        meta = baseline["metadata"]
        cluster = (baseline["task_id"], meta["dataset_hash"], meta["task_spec_hash"])
        clusters[cluster].append(int(assisted["passed"]) - int(baseline["passed"]))
    values = [clusters[cluster] for cluster in sorted(clusters)]
    totals = np.asarray([sum(value) for value in values], dtype=float)
    sizes = np.asarray([len(value) for value in values], dtype=float)
    # Each sampled cluster retains every repeated pair. The estimator remains
    # the run-weighted paired difference, even with unequal repeat counts.
    rng = np.random.default_rng(_BOOTSTRAP_SEED)
    draws = []
    for _ in range(_BOOTSTRAP_REPLICATES):
        sampled = rng.integers(0, len(values), size=len(values))
        draws.append(100 * float(totals[sampled].sum() / sizes[sampled].sum()))
    interval = np.quantile(draws, [.025, .975]).tolist() if len(values) >= 2 else None
    gains = sum(not baseline["passed"] and assisted["passed"] for baseline, assisted in items)
    losses = sum(baseline["passed"] and not assisted["passed"] for baseline, assisted in items)
    discordant = gains + losses
    p_value = float(binomtest(gains, discordant, .5).pvalue) if discordant else 1.0
    return {**dict(zip(("model", "backend", "track", "source_kind"), key)),
        "pairs": len(items), "task_clusters": len(values),
        "baseline_pass_rate": sum(b["passed"] for b, _ in items) / len(items),
        "assisted_pass_rate": sum(a["passed"] for _, a in items) / len(items),
        "delta_percentage_points": 100 * sum(totals) / len(items),
        "bootstrap": {"unit": "task_cluster", "seed": _BOOTSTRAP_SEED,
            "replicates": _BOOTSTRAP_REPLICATES, "confidence_level": .95,
            "confidence_interval_percentage_points": interval,
            "estimator": "run_weighted_paired_difference",
            "status": "estimated" if interval is not None else "insufficient_task_clusters"},
        "mcnemar": {"gains": gains, "losses": losses, "discordant_pairs": discordant,
            "p_value": p_value, "method": "exact_two_sided_binomial",
            "independence_assumption_satisfied": len(items) == len(values),
            "interpretation": "unadjusted; descriptive only when tasks are repeated"},
        "exploratory_pilot": len(values) < 30}


def _events(metadata):
    raw = metadata.get("tool_calls")
    events = [event for event in raw if isinstance(event, dict)
              and isinstance(event.get("tool_name"), str)
              and isinstance(event.get("succeeded"), bool)] if isinstance(raw, list) else []
    complete = (isinstance(raw, list) and len(events) == len(raw)
                and metadata.get("tool_capture_complete") is not False
                and metadata.get("trace_truncated") is not True)
    return events, complete


def _codes(value):
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return set()
    return {item.strip().lower().replace("-", "_").replace(" ", "_")
            for item in value if isinstance(item, str)}


def _truth_codes(metadata, events):
    truth = metadata.get("truth") if isinstance(metadata.get("truth"), dict) else {}
    recorded = any(isinstance(owner.get("confounded"), bool)
                   or any(isinstance(owner.get(field), (str, list))
                          and (isinstance(owner[field], str) or all(isinstance(code, str) for code in owner[field]))
                          for field in ("diagnostic_codes", "diagnostic_code"))
                   for owner in (metadata, truth))
    codes = set()
    for owner in (metadata, truth):
        codes |= _codes(owner.get("diagnostic_codes")) | _codes(owner.get("diagnostic_code"))
        if owner.get("confounded") is True:
            codes.add("confounded")
    for event in events:
        result = event.get("result")
        if event["succeeded"] and isinstance(result, dict):
            observed = _codes(result.get("diagnostic_codes")) | _codes(result.get("diagnostic_code"))
            codes |= observed
            recorded |= bool(observed)
            if event["tool_name"] == "validate_design_matrix" and result.get("is_full_rank") is False:
                codes.add("rank_deficient")
                recorded = True
    return codes, recorded


def _warns(answer, words):
    warnings = []
    for field in ("warnings", "diagnostic_warnings", "diagnostic_codes", "diagnostic_code"):
        value = answer.get(field)
        warnings.extend(value if isinstance(value, list) else [value])
    text = " ".join(item.lower().replace("_", " ").replace("-", " ")
                    for item in warnings if isinstance(item, str))
    return any(word.replace("_", " ") in text for word in words)


def _claim_status(answer, events):
    claimed = answer.get("claimed_significant_genes")
    if not isinstance(claimed, list) or any(not isinstance(gene, str) for gene in claimed):
        return None
    if not claimed:
        return "not_applicable"
    q_values = {}
    for event in events:
        result = event.get("result")
        if event["tool_name"] != "fit_temporal_trajectory" or not event["succeeded"] or not isinstance(result, dict):
            continue
        genes = result.get("genes")
        if not isinstance(genes, dict):
            continue
        for gene, stats in genes.items():
            if isinstance(stats, dict):
                q = stats.get("padj", stats.get("q_value", stats.get("qvalue")))
                if _number(q) and q <= 1:
                    q_values[gene] = q
    if any(q_values.get(gene, 0) > .05 for gene in claimed):
        return True
    return False if all(gene in q_values for gene in claimed) else None


def _biological_statuses(row):
    meta = row["metadata"]
    answer = meta.get("answer") if isinstance(meta.get("answer"), dict) else {}
    events, complete = _events(meta)
    codes, truth_recorded = _truth_codes(meta, events)
    checked = any(event["tool_name"] == "validate_design_matrix" and event["succeeded"]
                  and isinstance(event.get("result"), dict)
                  and isinstance(event["result"].get("is_full_rank"), bool) for event in events)
    statuses = {"nonsignificant_gene_claims": _claim_status(answer, events)}
    if codes & _COLLINEAR_CODES:
        warned = answer.get("rank_deficient") is True or _warns(answer, _COLLINEAR_CODES | {"rank deficient", "singular"})
        statuses["collinearity_blindness"] = False if checked or warned else True if complete else None
    else:
        statuses["collinearity_blindness"] = "not_applicable" if truth_recorded else None
    for metric, relevant in _TRAP_CODES.items():
        if codes & relevant:
            statuses[metric] = False if _warns(answer, relevant) else True if complete else None
        else:
            statuses[metric] = "not_applicable" if truth_recorded else None
    guard, fitted, missing = False, False, False
    for event in events:
        if (event["tool_name"] == "validate_design_matrix" and event["succeeded"]
                and isinstance(event.get("result"), dict)
                and isinstance(event["result"].get("is_full_rank"), bool)):
            guard = True
        elif event["tool_name"] == "fit_temporal_trajectory":
            fitted = True
            missing |= not guard
    statuses["missing_preflight_guard"] = (True if missing and complete else False if fitted and guard and complete
                                           else "not_applicable" if complete and not fitted else None)
    failure = meta.get("failure_type")
    runtime = {"resource_interrupt", "agent_error", "staging_error", "timeout", "runtime_error", "adapter_error"}
    formatting = {"answer_error", "answer_validation_error", "schema_error", "format_error"}
    for metric, relevant in (("runtime_errors", runtime), ("format_errors", formatting)):
        statuses[metric] = (failure in relevant if isinstance(failure, str)
                            else False if row["passed"] else None)
    return statuses


def _taxonomy(rows):
    statuses = defaultdict(list)
    for row in rows:
        for metric, value in _biological_statuses(row).items():
            statuses[metric].append(value)
    result = {}
    for metric, values in sorted(statuses.items()):
        observed = sum(isinstance(value, bool) for value in values)
        failures = sum(value is True for value in values)
        result[metric] = {"failures": failures, "observed": observed,
            "unknown": sum(value is None for value in values),
            "not_applicable": sum(value == "not_applicable" for value in values),
            "rate": failures / observed if observed else None,
            "coverage": observed / len(rows) if rows else 0.0}
    return result


def _recovery(rows):
    errors = corrected = retries = observed = 0
    for row in rows:
        events, complete = _events(row["metadata"])
        observed += complete
        pending = {}
        for event in events:
            name = event["tool_name"]
            if not event["succeeded"]:
                errors += 1
                pending[name] = event.get("arguments")
            elif name in pending:
                previous = pending.pop(name)
                if isinstance(previous, dict) and isinstance(event.get("arguments"), dict):
                    if previous == event["arguments"]:
                        retries += 1
                    else:
                        corrected += 1
    return {"tool_errors": errors, "corrected_calls": corrected, "successful_retries": retries,
            "observed_runs": observed, "unknown_runs": len(rows) - observed,
            "coverage": observed / len(rows) if rows else 0.0,
            "interpretation": "Observed failed call followed by successful same-tool call; intent is not inferred."}


def _turn_measure(turn, metric) -> Optional[float]:
    if metric == "tokens":
        if _number(turn.get("tokens")):
            return float(turn["tokens"])
        usage = turn.get("usage")
        if isinstance(usage, dict) and all(_number(usage.get(key)) for key in ("input_tokens", "output_tokens")):
            return float(usage["input_tokens"] + usage["output_tokens"])
    elif metric == "output_bytes":
        for field in ("output_bytes", "stdout_bytes"):
            if _number(turn.get(field)):
                return float(turn[field])
    return None


def _context(rows):
    bounds = {"turn_count": [(1, "1"), (4, "2-4"), (8, "5-8"), (math.inf, "9+")],
              "tokens": [(1000, "0-1000"), (10000, "1001-10000"), (50000, "10001-50000"), (math.inf, "50001+")],
              "output_bytes": [(10000, "0-10000"), (100000, "10001-100000"),
                               (1000000, "100001-1000000"), (math.inf, "1000001+")]}
    bins, turns_by_number = defaultdict(list), defaultdict(list)
    seen = Counter()
    for row in rows:
        turns = row["metadata"].get("turns")
        if not isinstance(turns, list) or not turns or any(not isinstance(turn, dict) for turn in turns):
            continue
        measures = {"turn_count": len(turns)}
        for metric in ("tokens", "output_bytes"):
            values = [_turn_measure(turn, metric) for turn in turns]
            measures[metric] = sum(values) if all(value is not None for value in values) else None
        for number, turn in enumerate(turns, 1):
            turns_by_number[number].append((turn, row["passed"]))
        for metric, value in measures.items():
            if value is None:
                continue
            seen[metric] += 1
            bucket = next(label for limit, label in bounds[metric] if value <= limit)
            bins[(metric, bucket)].append(row)
    dimensions = {}
    for metric, ranges in bounds.items():
        summaries = []
        for _, label in ranges:
            items = bins[(metric, label)]
            if items:
                summaries.append({"bin": label, "runs": len(items),
                    "pass_rate": sum(row["passed"] for row in items) / len(items),
                    "coverage": len(items) / len(rows) if rows else 0.0})
        dimensions[metric] = {"observed": seen[metric], "unknown": len(rows) - seen[metric],
            "coverage": seen[metric] / len(rows) if rows else 0.0, "bins": summaries}
    per_turn = [{"turn": number, "runs": len(items),
        "final_pass_rate": sum(passed for _, passed in items) / len(items),
        "coverage": len(items) / len(rows) if rows else 0.0,
        **{metric: _resource([_turn_measure(turn, metric) for turn, _ in items])
           for metric in ("tokens", "output_bytes")}}
        for number, items in sorted(turns_by_number.items())]
    return {"observational_only": True, "dimensions": dimensions, "turns": per_turn,
        "interpretation": "Final accuracy by observed burden; task difficulty, model, and early stopping can confound associations. "
                          "Output bytes are a stream-volume proxy, not measured context-window occupancy."}


def analyze_runs(results: list) -> dict:
    """Return JSON-safe metrics; malformed records and incompatible pairs are explicit.

    Metadata tool_calls must be captured events, not answer-authored history.
    Set tool_capture_complete=False when capture is unavailable or incomplete.
    result.model is retained as a label if the CLI reports no actual model ID.
    """
    if not isinstance(results, list):
        raise ValueError("results must be a list of evaluation result objects")
    rows, invalid = _normalize(results)
    pairs, pairing = _pair_runs(rows)
    grouped_pairs = defaultdict(list)
    for pair in pairs:
        grouped_pairs[tuple(pair[0][field] for field in ("model", "backend", "track", "source_kind"))].append(pair)
    return {"schema_version": 1,
        "validation": {"input_records": len(results), "valid_records": len(rows),
                       "excluded_records": len(invalid), "exclusions": invalid},
        "groups": _summarize_groups(rows), "pairing": pairing,
        "comparisons": [_comparison(items, key) for key, items in sorted(grouped_pairs.items())],
        "failure_taxonomy": _taxonomy(rows), "tool_recovery": _recovery(rows),
        "context_degradation": _context(rows),
        "limitations": [
            "Synthetic results evaluate designed perturbations, not validated public biological discoveries.",
            "Exploratory pilot labels use fewer than 30 independent task clusters; this is not a power calculation.",
            "The percentile bootstrap resamples entire task clusters with all repetitions; a single task has no interval.",
            "Exact McNemar p-values assume independent discordant pairs; repeated-task values are descriptive only and unadjusted.",
            "Model labels are not verified actual model IDs unless identified as observed in model_identity_sources.",
            "Failure rates condition on observed evidence; absent q-values, diagnostic truth, or complete capture remain unknown.",
            "Context-length associations are observational and do not establish degradation caused by longer conversations.",
            "Missing costs and tokens are unknown; no API price or subscription cost is inferred."]}


def _text(value):
    return str(value).replace("|", "\\|").replace("\n", " ")


def _format(value, percent=False):
    if value is None:
        return "unknown"
    return f"{value:.1%}" if percent else f"{value:.4g}"


def render_empirical_report(analysis: dict) -> str:
    """Render the analyzed evidence as a self-contained technical Markdown report."""
    validation = analysis["validation"]
    lines = ["# DevBench empirical analysis", "", "Exploratory comparisons; outcomes, provenance and evidence coverage are reported separately.", "",
             f"Valid runs: {validation['valid_records']}; malformed records excluded: {validation['excluded_records']}.", "",
             "## Observed outcomes", "",
             "| Model | Backend | Condition | Track | Source | Runs | Pass rate | Tokens (reported runs) | Cost USD (reported runs) | Peak RSS MB |",
             "|---|---|---|---|---|---:|---:|---:|---:|---:|"]
    for group in analysis["groups"]:
        tokens, cost = group["tokens"], group["cost_usd"]
        fields = [_text(group[field]) for field in _GROUP_FIELDS]
        fields += [str(group["runs"]), _format(group["pass_rate"], True),
                   f"{_format(tokens['total'])} ({tokens['reported']}/{group['runs']})",
                   f"{_format(cost['total'])} ({cost['reported']}/{group['runs']})",
                   _format(group["peak_memory_mb"]["maximum"])]
        lines.append("| " + " | ".join(fields) + " |")
    pairing = analysis["pairing"]
    lines += ["", "## Matched baseline versus assisted", "",
        f"Paired runs: {pairing['paired_runs']}/{pairing['total_valid_runs']} ({_format(pairing['coverage'], True)} coverage). "
        "Pairs require identical task IDs, model/backend, repetition, data and task fingerprints, track and source.", "",
        "| Model / backend / track / source | Pairs | Task clusters | Baseline | Assisted | Gain (percentage points) | 95% task-cluster interval | Exact McNemar p | Scope |",
        "|---|---:|---:|---:|---:|---:|---|---:|---|"]
    for comparison in analysis["comparisons"]:
        interval = comparison["bootstrap"]["confidence_interval_percentage_points"]
        ci = "unknown (fewer than 2 task clusters)" if interval is None else f"[{interval[0]:.2f}, {interval[1]:.2f}]"
        p = _format(comparison["mcnemar"]["p_value"])
        if not comparison["mcnemar"]["independence_assumption_satisfied"]:
            p += " (descriptive; repeated tasks)"
        label = " / ".join(_text(comparison[field]) for field in ("model", "backend", "track", "source_kind"))
        lines.append(f"| {label} | {comparison['pairs']} | {comparison['task_clusters']} | "
                     f"{_format(comparison['baseline_pass_rate'], True)} | {_format(comparison['assisted_pass_rate'], True)} | "
                     f"{comparison['delta_percentage_points']:.2f} | {ci} | {p} | "
                     f"{'Exploratory pilot' if comparison['exploratory_pilot'] else 'Exploratory benchmark'} |")
    if not analysis["comparisons"]:
        lines.append("No matched baseline/assisted pairs; no gain is estimated.")
    lines += ["", "### Pairing exclusions", "", "| Reason | Runs |", "|---|---:|"]
    lines += [f"| {_text(reason)} | {count} |" for reason, count in pairing["exclusion_counts"].items()]
    if not pairing["exclusion_counts"]:
        lines.append("| none | 0 |")
    lines += ["", "## Evidence-based failure taxonomy", "",
              "| Failure | Observed violations | Evaluable runs | Unknown | Not applicable | Rate | Coverage |",
              "|---|---:|---:|---:|---:|---:|---:|"]
    for name, metric in analysis["failure_taxonomy"].items():
        lines.append(f"| {_text(name)} | {metric['failures']} | {metric['observed']} | {metric['unknown']} | "
                     f"{metric['not_applicable']} | {_format(metric['rate'], True)} | {_format(metric['coverage'], True)} |")
    recovery = analysis["tool_recovery"]
    lines += ["", f"Captured tool errors: {recovery['tool_errors']}; successful changed calls: {recovery['corrected_calls']}; "
              f"successful unchanged retries: {recovery['successful_retries']}. Complete trace coverage: "
              f"{recovery['observed_runs']}/{validation['valid_records']}.", "",
              "## Context burden (observational)", "", analysis["context_degradation"]["interpretation"], "",
              "| Burden | Bin | Runs | Final pass rate | All-run coverage |", "|---|---|---:|---:|---:|"]
    for name, metric in analysis["context_degradation"]["dimensions"].items():
        for bucket in metric["bins"]:
            lines.append(f"| {name} | {bucket['bin']} | {bucket['runs']} | {_format(bucket['pass_rate'], True)} | "
                         f"{_format(bucket['coverage'], True)} |")
        if metric["unknown"]:
            lines.append(f"| {name} | unknown | {metric['unknown']} | unknown | unknown |")
    lines += ["", "| Turn reached | Runs | Final pass rate | Mean tokens (reported runs) | Mean output bytes (reported runs) |",
              "|---|---:|---:|---:|---:|"]
    for turn in analysis["context_degradation"]["turns"]:
        tokens, output = turn["tokens"], turn["output_bytes"]
        lines.append(f"| {turn['turn']} | {turn['runs']} | {_format(turn['final_pass_rate'], True)} | "
                     f"{_format(tokens['mean'])} ({tokens['reported']}/{turn['runs']}) | "
                     f"{_format(output['mean'])} ({output['reported']}/{turn['runs']}) |")
    lines += ["", "## Interpretation limits", ""]
    lines.extend("- " + limitation for limitation in analysis["limitations"])
    return "\n".join(lines) + "\n"
