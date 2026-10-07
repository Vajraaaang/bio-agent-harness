"""Explicit multiplicity handling for a finite, prespecified model search."""
from __future__ import annotations

import copy
import math


def adjust_selected_model_tests(raw):
    """Apply K*p_selected then BH across the declared observed gene family.

    For K prespecified candidate tests of the same null, the union bound allows
    arbitrary data-dependent selection if each candidate p-value is valid. The
    fitted count tests are asymptotic plug-in tests: this safeguard does not
    establish their calibration, fix normalization assumptions, adjust selected
    confidence intervals, or establish BH's dependence conditions.
    """
    from statsmodels.stats.multitest import multipletests

    if "inference_review" in raw:
        raise ValueError("Model-search adjustment has already been applied")
    result = copy.deepcopy(raw)
    fragments = [x for x in result["target_results"] if (x.get("payload") or {}).get("kind") == "effects"]
    if not fragments:
        return result
    selection = result.get("diagnostics", {}).get("model_selection", {})
    candidates = selection.get("requested_spline_candidates")
    if (not isinstance(candidates, list) or not candidates or len(set(candidates)) != len(candidates)
            or any(x is not None and (type(x) is not int or x < 1) for x in candidates)):
        raise ValueError("A bounded, distinct prespecified candidate family is required")
    if len(candidates) > 1 and (None in candidates or selection.get("model_selection_performed") is not True):
        raise ValueError("Model-selection metadata contradicts the candidate family")
    review = {"method": "bonferroni_selected_candidate_then_BH/1", "candidate_count": len(candidates),
              "assumption": "Each candidate tests the same scientific null with a valid p-value",
              "count_dispersion_calibration_established": False,
              "BH_dependency_assumptions_verified": False, "selected_intervals_adjusted": False,
              "original_tests": {}}
    for fragment in fragments:
        rows = fragment["payload"]["gene_statistics"]
        if not rows or len({x["gene_id"] for x in rows}) != len(rows):
            raise ValueError("The declared gene family must be nonempty and distinct")
        adjusted = []
        for row in rows:
            if row["estimate_status"] != "estimated":
                # Worst-case padding retains the full multiplicity denominator;
                # no successful-looking values are written into failed rows.
                adjusted.append(1.0)
                continue
            p = row["p_value"]
            if type(p) not in (int, float) or not math.isfinite(p) or not 0 <= p <= 1:
                raise ValueError("Estimated p-values must be finite probabilities")
            review["original_tests"].setdefault(fragment["target_id"], {})[row["gene_id"]] = {
                "p_value": p, "q_value": row["q_value"]}
            adjusted.append(min(1.0, len(candidates) * p))
        q_values = multipletests(adjusted, method="fdr_bh")[1]
        for row, p, q in zip(rows, adjusted, q_values):
            if row["estimate_status"] == "estimated":
                row.update(p_value=p, q_value=float(q))
        fragment.setdefault("limitation_codes", []).append("finite_candidate_search_adjusted_plugin_calibration_unproven")
    result["inference_review"] = review
    return result
