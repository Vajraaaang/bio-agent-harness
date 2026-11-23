"""Pure coordinator scoring of explicit claims against compatible references.

No I/O, fitting, LLM judging, imports of numerical libraries, or simulator calls.
Scientific correctness is independent of product report acceptance and workflow
enforcement; those decisions belong to the coordinator attempt record.
This module must never be placed in a participant's input bundle. Numerical
evaluation paths remain unvalidated until data execution is authorized.
"""
from __future__ import annotations

import math
from typing import Any

from pydantic import ValidationError

from .answers import StudyAnswer
from .contracts import MetricThresholds, canonical_json_bytes, content_sha256
from .records import validate_finite_json

SHAPES = ("flat", "increasing", "decreasing", "transient", "delayed")
STRUCTURAL_CODES = frozenset({"time_batch_complete_aliasing", "insufficient_independent_biological_units"})
METRIC_NAMES = (
    "signed_recall", "precision", "fdp", "directional_error_rate", "shape_macro_f1",
    "effect_mae", "effect_rmse", "curve_rmse", "interval_coverage", "interval_mean_width",
    "bh_arithmetic_valid", "eligible_count", "discovery_count", "false_discovery_count",
    "unsupported_probability_count", "lineage_labeled_graph_distance", "lineage_rank_accuracy",
    "lineage_branch_accuracy", "network_auprc", "activation_lcs_length", "activation_lcs_precision",
)


def _mean(values):
    """Scale finite terms before summing; finite means need not overflow."""
    if not values:
        return None
    scale = max(abs(value) for value in values)
    return scale * (math.fsum(value/scale for value in values)/len(values)) if scale else 0.0


def _difference(left, right):
    """Return an unavailable estimate when a finite-input subtraction overflows."""
    try:
        value = left-right
        return value if math.isfinite(value) else None
    except (OverflowError, ValueError):
        return None


def _root_mean_square(values):
    if not values:
        return None
    scale = max(abs(value) for value in values)
    return scale * math.sqrt(_mean([(value/scale)**2 for value in values])) if scale else 0.0


def _finite_number(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def _reference(truth):
    validate_finite_json(truth)
    if not isinstance(truth, dict) or truth.get("record_format") != "devbench.study.truth/1":
        raise ValueError("Expected devbench.study.truth/1")
    universe = truth.get("gene_ids")
    if (not isinstance(universe, list) or not universe or len(universe) > 100_000
            or any(not isinstance(g, str) or not g for g in universe) or len(set(universe)) != len(universe)):
        raise ValueError("Reference requires a bounded, distinct fixed gene universe")
    recipe = truth.get("recipe", {})
    if not isinstance(recipe, dict):
        raise ValueError("Reference recipe metadata must be an object")
    source = truth.get("source_kind", recipe.get("source_kind", "synthetic"))
    if source not in {"synthetic", "public_processed"}:
        raise ValueError("Unknown reference source_kind")
    genes = truth.get("genes", {})
    if not isinstance(genes, dict) or set(genes) - set(universe):
        raise ValueError("Reference genes must belong to the fixed universe")
    if source == "synthetic":
        if set(genes) != set(universe):
            raise ValueError("Synthetic truth must cover the entire fixed gene universe")
        identifiability = truth.get("population_identifiability", {})
        if not isinstance(identifiability, dict) or type(identifiability.get("identifiable")) is not bool:
            raise ValueError("Synthetic truth requires explicit population identifiability")
        reasons = identifiability.get("reasons")
        if not isinstance(reasons, list) or any(not isinstance(reason, str) or not reason for reason in reasons):
            raise ValueError("Population identifiability requires explicit reason metadata")
        if not identifiability["identifiable"] and not reasons:
            raise ValueError("Unidentifiable references must declare their structural reason")
        reference = truth.get("curve_reference", {})
        if (not isinstance(reference, dict)
                or set(reference) != {"reference_batch", "library_factor", "batch_reference_policy", "biological_unit_reference"}
                or not isinstance(reference.get("reference_batch"), str) or not reference["reference_batch"]
                or type(reference.get("library_factor")) not in (int, float) or reference["library_factor"] != 1.0
                or reference.get("batch_reference_policy") != "first_observation"
                or reference.get("biological_unit_reference") != "population_mean"):
            raise ValueError("Synthetic truth requires the shared first-observation population-mean reference")
        grid = truth.get("time_grid")
        if (not isinstance(grid, list) or not 3 <= len(grid) <= 100
                or any(not _finite_number(t) for t in grid)
                or any(b <= a for a, b in zip(grid, grid[1:]))):
            raise ValueError("Synthetic reference requires a strictly increasing bounded time grid")
        for gene in genes.values():
            if type(gene.get("null")) is not bool or gene.get("shape") not in SHAPES:
                raise ValueError("Synthetic gene truth requires null and shape labels")
            if gene["null"] != (gene["shape"] == "flat"):
                raise ValueError("Global temporal null and flat shape labels must agree")
            if any(not _finite_number(gene.get(key)) for key in ("amplitude_log2", "mean_reference", "effect")):
                raise ValueError("Synthetic reference estimates must be finite")
            if gene["amplitude_log2"] < 0 or gene["mean_reference"] <= 0:
                raise ValueError("Synthetic abundance/amplitude reference is invalid")
            curve = gene.get("predicted_curve")
            if (not isinstance(curve, list) or len(curve) != len(grid)
                    or any(not _finite_number(value) or value <= 0 for value in curve)
                    or gene.get("effect_units") != "log2_normalized_mean_change"):
                raise ValueError("Synthetic effect/curve reference units are invalid")
    diagnostics = truth.get("expected_diagnostics", [])
    if (not isinstance(diagnostics, list) or len(diagnostics) > 100
            or any(not isinstance(code, str) or not code for code in diagnostics)
            or len(set(diagnostics)) != len(diagnostics)):
        raise ValueError("Expected diagnostics must be bounded distinct explicit codes")
    available = truth.get("available_reference_metrics", []) if source == "public_processed" else list(METRIC_NAMES)
    if not isinstance(available, list) or any(name not in METRIC_NAMES for name in available):
        raise ValueError("Reference metric availability must be explicitly declared with supported names")
    return source, universe, genes, set(available)


def _bh_over_family(rows, universe):
    """Missing hypotheses occupy conservative p=1 positions internally only.

    The returned mapping contains observed p-values only. Failed/omitted tests
    never receive a fabricated p-value or q-value in any exported row.
    """
    positions = sorted((rows[g].p_value if g in rows and rows[g].p_value is not None else 1.0, g)
                       for g in universe)
    adjusted = {}
    running = 1.0
    total = len(universe)
    for rank in range(total, 0, -1):
        value, gene = positions[rank-1]
        running = min(running, value * total / rank)
        if gene in rows and rows[gene].p_value is not None:
            adjusted[gene] = running
    return adjusted


def _signed_correct(row, reference):
    if reference["shape"] in {"transient", "delayed"}:
        return row.shape == reference["shape"]
    if row.effect is not None and row.effect_units == reference.get("effect_units"):
        return (row.effect > 0 and reference["effect"] > 0) or (row.effect < 0 and reference["effect"] < 0)
    return row.shape == reference["shape"] and reference["shape"] in {"increasing", "decreasing"}


def _shape_scores(rows, genes, universe):
    per_class = {}
    for shape in SHAPES:
        actual = {g for g in universe if genes.get(g, {}).get("shape") == shape}
        predicted = {g for g, row in rows.items() if row.shape == shape}
        tp, fp, fn = len(actual & predicted), len(predicted-actual), len(actual-predicted)
        per_class[shape] = {
            "f1": 2*tp/(2*tp+fp+fn) if 2*tp+fp+fn else None,
            "support": len(actual), "predicted_count": len(predicted),
            "true_positive": tp, "false_positive": fp, "false_negative": fn,
        }
    # Include a class with false predictions even if it has no true support.
    return _mean([v["f1"] for v in per_class.values() if v["f1"] is not None]), per_class


def _lcs(left, right):
    previous = [0] * (len(right)+1)
    for a in left:
        current = [0]
        for j, b in enumerate(right, 1):
            current.append(previous[j-1]+1 if a == b else max(previous[j], current[-1]))
        previous = current
    return previous[-1]


def _optional_metrics(answer, truth, metrics, missingness, errors, coverage):
    """Only explicit bounded labeled references enable these ancillary panels."""
    lineage = truth.get("lineage_reference")
    if lineage is not None:
        nodes, edges = lineage.get("nodes", []), lineage.get("edges", [])
        if (lineage.get("scope") not in {"synthetic_labeled_graph", "validated_labeled_graph"}
                or not 1 <= len(nodes) <= 100 or len(set(nodes)) != len(nodes)
                or any(len(edge) != 2 or edge[0] not in nodes or edge[1] not in nodes for edge in edges)):
            raise ValueError("Lineage truth requires a compatible small labeled graph")
        if answer.lineage is not None:
            observed = answer.lineage
            # Exact labeled node/edge symmetric difference; no NP-hard unlabeled matching.
            metrics["lineage_labeled_graph_distance"] = (
                len(set(nodes) ^ set(observed.nodes)) + len({tuple(e) for e in edges} ^ set(observed.edges)))
            ordered = lineage.get("pseudotime", {})
            shared = sorted(set(ordered) & set(observed.pseudotime))
            if len(ordered) > 2000:
                raise ValueError("Rank comparison is bounded to 2000 explicitly referenced items")
            ordered_items = sorted(ordered)
            comparisons = [float(a in observed.pseudotime and b in observed.pseudotime and
                                 (ordered[a]-ordered[b]) * (observed.pseudotime[a]-observed.pseudotime[b]) > 0)
                           for i, a in enumerate(ordered_items) for b in ordered_items[i+1:]
                           if ordered[a] != ordered[b]]
            metrics["lineage_rank_accuracy"] = _mean(comparisons) if shared else None
            coverage["lineage_rank_reported_items"] = len(shared)
            coverage["lineage_rank_reference_items"] = len(ordered)
            coverage["lineage_rank_pair_denominator"] = len(comparisons)
            branches = lineage.get("branch_assignments", {})
            metrics["lineage_branch_accuracy"] = (
                sum(observed.branch_assignments.get(item) == branch for item, branch in branches.items()) / len(branches)
                if branches and observed.branch_assignments else None)
            coverage["lineage_branch_reported_items"] = len(set(branches) & set(observed.branch_assignments))
            coverage["lineage_branch_reference_items"] = len(branches)
    network = truth.get("network_reference")
    if network is not None:
        pairs = [tuple(pair) for pair in network.get("edge_universe", [])]
        positives = {tuple(pair) for pair in network.get("positive_edges", [])}
        if (network.get("scope") not in {"synthetic_association", "curated_membership", "perturbation_response"}
                or not 1 <= len(pairs) <= 100_000 or len(set(pairs)) != len(pairs)
                or any(len(pair) != 2 for pair in pairs) or not positives <= set(pairs)
                or network.get("complete_labels") is not True):
            raise ValueError("Network AUPRC requires explicit complete labels over a bounded edge universe")
        predicted = {(edge.source, edge.target): edge.score for edge in answer.regulatory_edges}
        coverage["network_reported_edges"] = len(set(predicted) & set(pairs))
        coverage["network_full_universe_edges"] = len(pairs)
        if len(predicted) != len(answer.regulatory_edges) or set(predicted)-set(pairs):
            errors.append("network_edges_duplicate_or_outside_declared_universe")
        elif positives and predicted:
            # Missing edges occupy a tied score strictly below all reported edges.
            groups = {}
            for pair in pairs:
                score = predicted.get(pair)
                groups.setdefault(score, []).append(pair)
            ordered_scores = sorted((score for score in groups if score is not None), reverse=True)
            if None in groups:
                ordered_scores.append(None)
            tp = fp = 0
            ap = 0.0
            for score in ordered_scores:
                new_tp = sum(pair in positives for pair in groups[score])
                tp += new_tp
                fp += len(groups[score])-new_tp
                ap += new_tp/len(positives) * tp/(tp+fp)
            metrics["network_auprc"] = ap
    activation = truth.get("activation_order_reference")
    if activation is not None:
        order = activation.get("order", [])
        if (activation.get("scope") not in {"synthetic_ordering", "observed_reference_ordering"}
                or len(order) > 1000 or len(set(order)) != len(order)):
            raise ValueError("Activation reference requires an explicit bounded unique ordering")
        predicted = answer.tf_activation_order
        coverage["activation_predicted_length"] = len(predicted)
        coverage["activation_reference_length"] = len(order)
        if len(set(predicted)) != len(predicted):
            errors.append("duplicate_activation_order_labels")
        elif order:
            length = _lcs(predicted, order)
            metrics["activation_lcs_length"] = length
            metrics["activation_lcs_precision"] = length/len(predicted) if predicted else None
    for name, value in metrics.items():
        if value is not None:
            missingness.pop(name, None)


def score_answer(answer: StudyAnswer | dict | None, truth: dict, *, thresholds: MetricThresholds,
                 expected_task_id: str, expected_input_sha256: str) -> dict:
    """Score participant claims; never confuse absent reference truth with negatives.

    Malformed answers return primary failure. Malformed coordinator references
    raise ValueError. Metrics are partial and have explicit coverage; unsupported
    probability fields do not silently become actual false discoveries.

    accepted_answer certifies only answer-schema and task/input identity checks.
    It is not report acceptance, scientific correctness, executed provenance, or
    workflow compliance; the runner independently verifies execution/integrity.
    """
    if not isinstance(thresholds, MetricThresholds):
        raise ValueError("Locked MetricThresholds are required")
    source, universe, genes, available = _reference(truth)
    metrics = {name: None for name in METRIC_NAMES}
    missingness = {name: "no_compatible_reference_or_observed_estimate" for name in METRIC_NAMES}
    result = {
        "record_format": "devbench.study.score/1", "source_kind": source,
        "accepted_answer": False, "schema_valid_answer": False, "answer_identity_valid": None,
        "scientific_claims_supported": False, "scientific_success": False, "primary_status": "invalid_answer",
        "metrics": metrics, "missingness": missingness, "diagnostics": {},
        "coverage": {}, "shape_per_class": {}, "interval_scopes": [], "gene_rows": [], "errors": [],
        "scope": {"narrative": "unverified_not_scored", "causal_truth": "not_established",
                  "thresholds": thresholds.status, "curve_reference": "shared_task_estimand_required"},
    }
    try:
        if answer is None:
            raise ValueError("missing_answer")
        payload = answer.model_dump(mode="json") if isinstance(answer, StudyAnswer) else answer
        parsed = StudyAnswer.model_validate_json(canonical_json_bytes(payload))
    except (ValueError, TypeError, ValidationError) as exc:
        # Avoid echoing arbitrary participant text or private reference values.
        result["errors"] = ["missing_answer" if answer is None else f"malformed_answer:{type(exc).__name__}"]
        return result
    errors = result["errors"]
    result["schema_valid_answer"] = True
    result["answer_identity_valid"] = parsed.task_id == expected_task_id and parsed.input_sha256 == expected_input_sha256
    result["accepted_answer"] = result["answer_identity_valid"]
    if not result["answer_identity_valid"]:
        errors.append("task_or_input_identity_mismatch")
    rows = {row.gene_id: row for row in parsed.gene_statistics}
    if set(rows)-set(universe):
        errors.append("genes_outside_fixed_universe")
        return result
    expected_family = truth.get("hypothesis_family_id", content_sha256({"gene_ids": universe, "target": truth.get("target", "temporal_omnibus")}))
    if parsed.analysis is not None and parsed.analysis.hypothesis_family_id != expected_family:
        errors.append("hypothesis_family_identity_mismatch")
    adjusted = _bh_over_family(rows, universe)
    invalid_probability = set()
    q_count = 0
    for gene, row in rows.items():
        if row.status != "tested" and row.p_value is not None:
            invalid_probability.add(gene)
        if row.status == "tested" and row.q_value is None:
            invalid_probability.add(gene)
        if row.q_value is not None:
            q_count += 1
            if not math.isclose(row.q_value, adjusted[gene], rel_tol=1e-7, abs_tol=1e-10):
                invalid_probability.add(gene)
        if row.claimed_discovery and (row.q_value is None or row.q_value > thresholds.alpha):
            invalid_probability.add(gene)
    metrics["bh_arithmetic_valid"] = not invalid_probability if q_count or invalid_probability else None
    metrics["unsupported_probability_count"] = len(invalid_probability)
    if invalid_probability:
        errors.append("unsupported_probability_fields")
    analysis = parsed.analysis
    reference_matches = False
    if analysis is not None:
        declared_reference = getattr(analysis, "reference", None)
        if declared_reference is not None:
            declared_reference = declared_reference.model_dump(mode="json")
        reference_matches = bool(truth.get("curve_reference") is not None
                                 and declared_reference == truth["curve_reference"])
        if any(row.effect is not None or row.predicted_curve for row in rows.values()) and not reference_matches:
            errors.append("estimand_reference_missing_or_mismatched")
    if analysis is not None and analysis.selection_criterion != "fixed" and analysis.inference_scope == "fixed_design":
        errors.append("model_selection_inference_scope_not_disclosed")
    numerical_errors = []
    for row in rows.values():
        if analysis is not None and analysis.measurement_type == "raw_counts" and row.predicted_curve:
            if any(value <= 0 for value in row.predicted_curve):
                errors.append("nonpositive_count_mean_prediction")
        if row.effect is not None and row.predicted_curve:
            if row.effect_units == "log2_normalized_mean_change" and all(value > 0 for value in row.predicted_curve):
                # Subtract logs rather than taking a ratio that can overflow or underflow.
                implied = _difference(math.log2(row.predicted_curve[-1]), math.log2(row.predicted_curve[0]))
            elif row.effect_units == "expression_difference":
                implied = _difference(row.predicted_curve[-1], row.predicted_curve[0])
            else:
                implied = None
            if implied is None:
                errors.append("numerical_effect_curve_comparison_unavailable")
                numerical_errors.append({"gene_id": row.gene_id, "field": "effect_curve_consistency"})
            elif not math.isclose(row.effect, implied, rel_tol=1e-5, abs_tol=1e-6):
                errors.append("effect_inconsistent_with_declared_curve_endpoints")
        if row.confidence_interval is not None and analysis is not None:
            if analysis.selection_criterion != "fixed" and row.interval_scope == "fixed_design":
                errors.append("interval_selection_scope_not_disclosed")
    expected_codes = set(truth.get("expected_diagnostics", []))
    observed_codes = {diagnostic.code for diagnostic in parsed.diagnostics}
    population_reference = truth.get("population_identifiability", {})
    diagnostic_truth_available = source == "synthetic" or truth.get("diagnostic_truth_adjudicated") is True
    identifiable = population_reference.get("identifiable") if isinstance(population_reference, dict) else None
    if not diagnostic_truth_available:
        identifiable = None
    structural_claim = bool(observed_codes & STRUCTURAL_CODES)
    incorrect_refusal = bool((observed_codes & STRUCTURAL_CODES)-expected_codes) if identifiable is not None else None
    unsupported_population = bool(identifiable is False and any(
        row.p_value is not None or row.q_value is not None or row.claimed_discovery
        or ((row.effect is not None or row.predicted_curve) and parsed.claim_scope != "descriptive") for row in rows.values())) if diagnostic_truth_available and identifiable is not None else None
    result["diagnostics"] = {
        "expected_code_count": len(expected_codes) if diagnostic_truth_available else None,
        "observed_code_count": len(observed_codes),
        "required_diagnostics_correct": expected_codes <= observed_codes if diagnostic_truth_available else None,
        "missing_required_codes": sorted(expected_codes-observed_codes) if diagnostic_truth_available else None,
        "diagnostic_truth_available": diagnostic_truth_available,
        "incorrect_structural_refusal": incorrect_refusal,
        "structural_refusal_claimed": structural_claim,
        "unsupported_population_claim": unsupported_population,
        "nondiscovery_is_not_automatically_refusal": True,
    }
    if unsupported_population:
        errors.append("unsupported_fitted_population_claim")
    family_accounted = set(rows) == set(universe)
    applicable_tests = [gene for gene, row in rows.items()
                        if row.status == "tested" and row.q_value is not None and gene not in invalid_probability]
    analysis_evidence_available = analysis is not None and family_accounted and bool(applicable_tests) and parsed.decision != "execution_failure"
    if source == "synthetic" and identifiable is True:
        if analysis is None:
            errors.append("declared_analysis_required")
        if not family_accounted:
            errors.append("incomplete_hypothesis_family_accounting")
        if not applicable_tests:
            errors.append("no_applicable_tested_evidence")
    calls = {gene for gene, row in rows.items() if row.claimed_discovery}
    metrics["discovery_count"] = len(calls)
    null_available = (source == "synthetic" or truth.get("null_reference_scope") in {
        "complete_temporal_null_labels", "validated_control_labels"}) and all(type(genes.get(g, {}).get("null")) is bool for g in universe)
    if null_available and "fdp" in available:
        false_calls = {g for g in calls if genes[g]["null"]}
        metrics["false_discovery_count"] = len(false_calls)
        metrics["fdp"] = len(false_calls)/len(calls) if calls else 0.0 if analysis_evidence_available else None
        metrics["precision"] = (len(calls)-len(false_calls))/len(calls) if calls else None
    eligible = set()
    if identifiable is True and "signed_recall" in available and all(
            all(_finite_number(genes.get(g, {}).get(k)) for k in ("amplitude_log2", "mean_reference")) for g in universe) and null_available:
        eligible = {g for g in universe if not genes[g]["null"]
                    and genes[g]["amplitude_log2"] >= thresholds.eligible_amplitude_log2
                    and genes[g]["mean_reference"] >= thresholds.eligible_mean}
        metrics["eligible_count"] = len(eligible)
        correct = {g for g in calls & eligible if g not in invalid_probability and _signed_correct(rows[g], genes[g])}
        metrics["signed_recall"] = len(correct)/len(eligible) if eligible else None
        directional = {g for g in calls if not genes[g]["null"] and genes[g]["shape"] in {"increasing", "decreasing"}}
        metrics["directional_error_rate"] = (
            sum(not _signed_correct(rows[g], genes[g]) for g in directional)/len(directional) if directional else None)
    if "shape_macro_f1" in available and all(genes.get(g, {}).get("shape") in SHAPES for g in universe):
        metrics["shape_macro_f1"], result["shape_per_class"] = _shape_scores(rows, genes, universe)
    effect_errors, curve_errors, intervals = [], [], []
    grid = truth.get("time_grid")
    interval_groups = {}
    for gene in universe:
        row = rows.get(gene)
        if row is None:
            result["gene_rows"].append({"gene_id": gene, "status": "not_reported", "p_value": None, "q_value": None,
                                       "missing_reason": "omitted_from_answer"})
            continue
        reference = genes.get(gene, {})
        effect_ok = reference_matches and row.effect is not None and _finite_number(reference.get("effect")) and row.effect_units == reference.get("effect_units")
        curve_ok = bool(reference_matches and row.predicted_curve and grid is not None and list(row.time_grid) == grid
                        and len(reference.get("predicted_curve", [])) == len(grid))
        if effect_ok:
            difference = _difference(row.effect, reference["effect"])
            if difference is None:
                effect_ok = False
                errors.append("numerical_effect_difference_unavailable")
                numerical_errors.append({"gene_id": gene, "field": "effect_error"})
            else:
                effect_errors.append(difference)
        if curve_ok:
            differences = [_difference(a, b) for a, b in zip(row.predicted_curve, reference["predicted_curve"])]
            if any(difference is None for difference in differences):
                curve_ok = False
                errors.append("numerical_curve_difference_unavailable")
                numerical_errors.append({"gene_id": gene, "field": "curve_error"})
            else:
                curve_errors.append(_root_mean_square(differences))
        if effect_ok and row.confidence_interval is not None:
            low, high = row.confidence_interval
            width = _difference(high, low)
            if width is None:
                errors.append("numerical_interval_width_unavailable")
                numerical_errors.append({"gene_id": gene, "field": "interval_width"})
            else:
                event = (float(low <= reference["effect"] <= high), width)
                interval_groups.setdefault((row.interval_scope, row.confidence_level), []).append(event)
                if math.isclose(row.confidence_level, thresholds.confidence_level):
                    intervals.append(event)
        exported = row.model_dump(mode="json")
        exported.update(probability_support="unsupported" if gene in invalid_probability else "reported_not_independently_reproduced",
                        effect_comparable=effect_ok, curve_comparable=curve_ok)
        result["gene_rows"].append(exported)
    for name, value in {
        "effect_mae": _mean([abs(error) for error in effect_errors]),
        "effect_rmse": _root_mean_square(effect_errors),
        "curve_rmse": _root_mean_square(curve_errors),
        "interval_coverage": _mean([event[0] for event in intervals]),
        "interval_mean_width": _mean([event[1] for event in intervals]),
    }.items():
        if name in available:
            metrics[name] = value
    result["interval_scopes"] = [{"scope": scope, "confidence_level": level, "available_count": len(events),
                                  "coverage": _mean([e[0] for e in events]), "mean_width": _mean([e[1] for e in events])}
                                 for (scope, level), events in sorted(interval_groups.items())]
    result["coverage"] = {
        "fixed_gene_universe": len(universe), "reported_genes": len(rows), "omitted_genes": len(universe)-len(rows),
        "observed_p_values": len(adjusted), "reported_q_values": q_count,
        "hypothesis_family_accounted": family_accounted,
        "applicable_tested_count": len(applicable_tests),
        "analysis_evidence_available": analysis_evidence_available,
        "effect_comparable_count": len(effect_errors), "effect_denominator": len(universe),
        "curve_comparable_count": len(curve_errors), "curve_denominator": len(universe),
        "interval_available_count": len(intervals), "interval_denominator": len(universe),
        "interval_nominal_level": thresholds.confidence_level,
        "interval_aggregate_scope": "descriptive_available_estimates_see_scope_strata_not_calibration_proof",
        "effect_scope": "reported_estimates_with_compatible_units_only",
        "curve_scope": "exact_structured_shared_reference_and_time_grid_required",
        "estimand_reference_matches": reference_matches,
    }
    result["numerical_evidence"] = {"valid": not numerical_errors, "unavailable_fields": numerical_errors}
    if source == "synthetic" or available & {
        "lineage_labeled_graph_distance", "lineage_rank_accuracy", "lineage_branch_accuracy", "network_auprc",
        "activation_lcs_length", "activation_lcs_precision",
    }:
        _optional_metrics(parsed, truth, metrics, missingness, errors, result["coverage"])
    # Suppress undeclared public-reference metrics, including derived diagnostics
    # when the reference has no adjudicated design/replication truth.
    if source == "public_processed":
        for name in metrics:
            if name not in available and name not in {"discovery_count", "unsupported_probability_count", "bh_arithmetic_valid"}:
                metrics[name] = None
        if not {"interval_coverage", "interval_mean_width"} & available:
            result["interval_scopes"] = []
    if not calls and (errors or not analysis_evidence_available):
        metrics["fdp"] = None
        missingness["fdp"] = "no_completed_supported_tested_analysis_for_zero_discovery_FDP"
    result["scientific_claims_supported"] = not errors and parsed.decision != "execution_failure"
    if errors:
        result["primary_status"] = "unsupported_or_incoherent_answer"
    elif parsed.decision == "execution_failure":
        result["primary_status"] = "execution_failure"
    elif source == "public_processed":
        result["scientific_success"] = None
        result["primary_status"] = "reference_metrics_only_no_latent_success_adjudication"
    elif identifiable is False:
        correct = bool(expected_codes & STRUCTURAL_CODES) and expected_codes <= observed_codes and not unsupported_population
        result["scientific_success"] = correct
        result["primary_status"] = "correct_diagnostic" if correct else "diagnostic_failure"
    else:
        checks = [not incorrect_refusal, expected_codes <= observed_codes,
                  metrics["fdp"] is not None and metrics["fdp"] <= thresholds.maximum_fdp]
        if eligible:
            checks.extend([metrics["signed_recall"] is not None and metrics["signed_recall"] >= thresholds.minimum_signed_recall,
                           metrics["shape_macro_f1"] is not None and metrics["shape_macro_f1"] >= thresholds.minimum_shape_macro_f1])
        result["scientific_success"] = all(checks)
        result["primary_status"] = "scientific_pass" if all(checks) else "scientific_fail"
    if numerical_errors:
        for name in ("effect_mae", "effect_rmse", "curve_rmse", "interval_coverage", "interval_mean_width"):
            if metrics[name] is None:
                missingness[name] = "numerical_evidence_unavailable_or_no_compatible_estimate"
    result["missingness"] = {name: missingness.get(name, (
        "not_declared_in_public_reference_scope" if source == "public_processed" and name not in available
        else "no_compatible_reference_or_observed_estimate"))
        for name, value in metrics.items() if value is None}
    result["errors"] = sorted(set(errors))
    validate_finite_json(result)
    return result
