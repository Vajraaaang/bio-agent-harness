"""Strict, descriptive projections of saved bulk RNA plotting evidence.

This module never fits differential expression, shrinkage, filtering, LRTs or
influence models. Its input contracts deliberately distinguish saved estimates,
supplied biological identities and descriptive geometry. Artifact custody is
the caller's responsibility; arbitrary path strings are never read here.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
import hashlib
import math
import re

from .scientific_plot_contract import (
    distribution_summary, finite_number, line_mark, matrix_panel, panel,
    point_marks, require_fields, require_unique, row, validate_panel,
)


_SAMPLE_FIELDS = ["population_unit", "biological_unit", "samples"]
_EFFECT_FIELDS = ["population_unit", "binding", "genes", "estimators"]
_LIMITS = ["Saved plotting evidence does not establish biological or clinical validity.",
           "Recorded biological identities are supplied declarations; unique sample IDs do not establish independent biological replication."]


def _cap(required, optional, description):
    return {"required": list(required), "optional": list(optional), "description": description}


CAPABILITIES = {
    "bulk_ma_comparison": _cap(_EFFECT_FIELDS, ["calibration", "label_gene_ids"], "Separate saved raw and shrunken effects, their named uncertainties and declared boundary warnings against log10(baseMean+1)."),
    "bulk_volcano": _cap(_EFFECT_FIELDS + ["probability_mode"], ["calibration", "alpha", "effect_threshold", "label_gene_ids"], "Raw and shrunken volcano panels using explicitly chosen saved p or q; zero probabilities have no finite ordinate."),
    "bulk_dispersion": _cap(["population_unit", "genes", "trend", "trend_method", "dispersion_unit", "mean_unit", "model_id"], [], "Layered saved gene-wise, trend and final dispersions on log10 axes without fitting a trend."),
    "bulk_pca": _cap(_SAMPLE_FIELDS + ["scores", "explained_variance_ratio", "score_unit", "transformation"], ["pairing", "loadings", "loading_scale", "loading_unit", "loading_definition"], "Saved PC1/2 and variance scree, independently available PC3/4 and declared-scale loading biplot, with explicitly declared donor-directed vectors."),
    "bulk_mds": _cap(_SAMPLE_FIELDS + ["mode"], ["sample_ids", "distance_matrix", "distance_definition", "distance_unit", "scores", "score_unit", "eigenvalues", "eigenvalue_definition", "method", "pairing"], "Saved MDS scores or deterministic classical MDS of a declared distance matrix; fractions refer to positive eigenvalues, never PCA variance."),
    "bulk_sample_distance_heatmap": _cap(_SAMPLE_FIELDS + ["sample_ids", "matrix", "distance_definition", "unit", "covariate_tracks", "cluster"], [], "Symmetric saved sample distances with deterministic average-linkage dendrogram and aligned covariate tracks."),
    "bulk_sample_correlation_heatmap": _cap(_SAMPLE_FIELDS + ["sample_ids", "matrix", "correlation_definition", "unit", "covariate_tracks", "cluster"], [], "Symmetric saved correlations with fixed -1..1 scale, dendrogram using declared 1-r dissimilarity and aligned covariate tracks."),
    "bulk_pvalue_histogram": _cap(["population_unit", "genes", "bin_count", "test_id", "uniform_reference"], ["boundary_evidence", "boundary_endpoint_cutoff"], "Raw-p histograms for all genes and saved filtering statuses; declared uniform guide, endpoint counts and provenance-bound saved boundary-associated counts, not a calibration result or boundary diagnosis."),
    "bulk_independent_filtering": _cap(["population_unit", "curve", "alpha", "selection_id", "filter_statistic", "quantile_unit", "selected_quantile", "selection_rule", "tested_gene_count"], [], "Actual saved rejection counts versus filter quantile, retaining failed evaluations and saved alpha/selection metadata."),
    "bulk_paired_expression": _cap(_SAMPLE_FIELDS + ["pairing", "gene_ids", "normalized_counts", "normalization", "expression_unit", "contribution_definition", "pseudocount"], [], "Per-gene paired expression facets and each pair's absolute displayed log2 difference share; no model influence inference."),
    "bulk_library_qc": _cap(_SAMPLE_FIELDS + ["measurement", "gene_ids", "counts", "size_factors", "size_factor_method"], [], "Raw library depth, exact >0 and >=10 detected genes and saved size factor versus depth; no normalization refit."),
    "bulk_cooks_distance": _cap(_SAMPLE_FIELDS + ["gene_ids", "cooks_distances", "model_id", "distance_unit"], ["bandwidth"], "Saved gene/sample Cook's distances as log10 Tukey boxes and violin distributions; zero and failed values remain explicit."),
    "bulk_leave_one_pair_out": _cap(["population_unit", "biological_unit", "binding", "full_fit_id", "definition", "pairs", "genes"], [], "Saved leave-one-pair-out fitted-effect differences, retaining actual omitted-pair and refit identities and failures."),
    "bulk_concordance": _cap(["population_unit", "left", "right", "comparison_kind", "effect_comparability"], [], "Bound cohort/contrast/scale/gene-ID concordance and Bland-Altman geometry for effects and -log10(raw p), retaining test identities."),
    "bulk_upset_dispositions": _cap(["population_unit", "binding", "gene_ids", "methods"], [], "Exact saved selection intersections, UpSet membership matrix and saved disposition counts; calibrated labels require bound calibration evidence."),
    "bulk_residual_diagnostics": _cap(_SAMPLE_FIELDS + ["model_id", "residual_type", "residual_definition", "residual_unit", "fitted_unit", "group_by", "records"], ["bandwidth"], "Actual saved gene/sample fitted values and named residuals, with residual-versus-fitted and explicit-group Tukey/violin distributions; no residual derivation or model fitting."),
    "bulk_design_diagnostics": _cap(_SAMPLE_FIELDS + ["model_id", "sample_ids", "columns", "matrix", "diagnostics"], ["contrasts"], "Actual sample/column design matrix, saved rank/condition/singular values or explicitly requested descriptive SVD, and saved contrast estimability warnings; full rank does not establish causal identifiability."),
    "bulk_mean_variance": _cap(["population_unit", "biological_unit", "samples", "measurement_scale", "mean_unit", "variance_unit", "mean_definition", "variance_definition", "variance_ddof", "display_transform", "genes"], [], "Actual saved empirical gene means and variances over explicitly identified sample observations, with declared units/ddof/display transform; distinct from NB alpha dispersion."),
}


def _text(value, label="text"):
    if not isinstance(value, str) or not value.strip() or len(value) > 2000:
        raise ValueError(f"{label} must be a nonempty bounded string")
    return value


def _biological_identity(value):
    value = _text(value, "declared biological-unit identity")
    if value.strip().lower() in {"unknown", "unidentified", "unverified", "none", "null", "na", "n/a", "nan", "missing"}:
        raise ValueError("pairing cannot use an unknown biological identity encoded as text")
    return value


def _fields(value, required, optional=()):
    if type(value) is not dict:
        raise ValueError("scientific record must be an object")
    require_fields(value, required, optional)
    if set(value) - set(required) - set(optional):
        raise ValueError("unexpected scientific record fields")


def _number(value, *, nonnegative=False, positive=False, missing=False):
    if type(value) is bool:
        raise ValueError("boolean is not a scientific numeric quantity")
    result = finite_number(value, allow_none=missing)
    if result is None:
        return None
    result = float(result)
    if nonnegative and result < 0 or positive and result <= 0:
        raise ValueError("scientific quantity outside its declared nonnegative/positive domain")
    return result


def _probability(value, missing=True):
    value = _number(value, nonnegative=True, missing=missing)
    if value is not None and value > 1:
        raise ValueError("probability must lie in [0,1]")
    return value


def _records(value, label, maximum=100_000):
    if not isinstance(value, list) or not 0 < len(value) <= maximum:
        raise ValueError(f"{label} must be a nonempty bounded list")
    return value


def _ids(values, label, maximum=100_000):
    values = _records(values, label, maximum)
    for value in values:
        _text(value, label)
    require_unique(values, label)
    return list(values)


def _population(data, expected):
    if data["population_unit"] != expected:
        raise ValueError(f"population_unit must explicitly be {expected}")


def _binding(value):
    _fields(value, ["cohort_id", "contrast_id", "gene_id_namespace", "effect_unit"])
    for key in value:
        _text(value[key], key)
    if value["effect_unit"] != "log2_fold_change":
        raise ValueError("effect scale must explicitly be log2_fold_change")
    return deepcopy(value)


def _axis(label, unit, transform="identity"):
    return {"label": label, "unit": unit, "transform": transform}


def _axes(xlabel, xunit, ylabel, yunit, xt="identity", yt="identity"):
    return {"x": _axis(xlabel, xunit, xt), "y": _axis(ylabel, yunit, yt)}


def _row(identity, x=None, y=None, *, kind="gene", group="", details=None, reason=None, key=None, warnings=(), source_status=None):
    details, warnings = deepcopy(details or {}), list(warnings)
    if reason is not None:
        details["source_reason"] = _text(reason, "saved source reason")
    if source_status is not None:
        details["source_status"] = _text(source_status, "saved source status")
    if source_status in {"failed", "nonconverged", "not_assessed", "convergence_not_verified"}:
        reason = reason or f"saved_estimate_{source_status}"
        warnings.append(f"saved_estimate_{source_status}")
    elif x is None or y is None:
        reason = reason or "saved_quantity_missing_or_not_finitely_displayable"
    else:
        # A supplied informational note is retained verbatim in source_reason.
        # Only an explicit unsuccessful status makes finite geometry unqualified.
        reason = None
    return row(identity, x, y, entity_kind=kind, group=group, details=details,
               reason=reason, key=key, warnings=list(dict.fromkeys(warnings)), link={"kind": kind, "id": identity})


def _panel(cap, title, rows, *, axes, suffix, **kwargs):
    limitations = list(kwargs.pop("limitations", [])) + _LIMITS
    identity = f"{cap}:{suffix}"
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.:-]{0,159}", identity):
        identity = f"{cap}:{hashlib.sha256(suffix.encode()).hexdigest()[:24]}"
    return panel(cap, title, rows, axes=axes, panel_id=identity, limitations=limitations, **kwargs)


def _mark(kind, keys=(), group="", **geometry):
    return {"type": kind, "keys": list(keys), "group": group, **geometry}


def _finite_rows(rows):
    return [r for r in rows if r["x"] is not None and r["y"] is not None]


def _log10(value):
    return math.log10(value) if value is not None and value > 0 else None


def _neglog(value):
    return -math.log10(value) if value is not None and value > 0 else None


def _calibrated(disposition, evidence, binding, gene_id, method_id):
    if not disposition.lower().startswith("calibrated"):
        return
    if not isinstance(evidence, dict):
        raise ValueError("calibrated dispositions require saved calibration evidence")
    _fields(evidence, ["evidence_id", "method_id", "cohort_id", "contrast_id", "gene_ids", "criterion", "result"])
    _text(evidence["evidence_id"], "calibration evidence ID")
    _text(evidence["criterion"], "calibration criterion")
    if (evidence["result"] != "passed" or evidence["method_id"] != method_id or evidence["cohort_id"] != binding["cohort_id"]
            or evidence["contrast_id"] != binding["contrast_id"]
            or gene_id not in _ids(evidence["gene_ids"], "calibration gene IDs")):
        raise ValueError("calibration evidence is not bound to this method, cohort, contrast and gene")


def _calibration_detail(disposition, evidence, gene_id):
    if not disposition.lower().startswith("calibrated"):
        return None
    return {**{k: deepcopy(v) for k, v in evidence.items() if k != "gene_ids"}, "gene_id": gene_id}


def _label_marks(rows, labels):
    """Deterministic offset callouts; text is annotation geometry, source points remain exact."""
    selected = [r for r in _finite_rows(rows) if r["id"] in labels]
    if not selected:
        return []
    finite = _finite_rows(rows)
    xspan = max(r["x"] for r in finite)-min(r["x"] for r in finite) or 1.
    yspan = max(r["y"] for r in finite)-min(r["y"] for r in finite) or 1.
    gap = yspan * .07
    previous = -math.inf
    result = []
    for r in sorted(selected, key=lambda r: (r["y"], r["id"])):
        x, y = r["x"]+.025*xspan, max(r["y"]+.025*yspan, previous+gap)
        result.extend([_mark("line", [r["key"]], r["group"], x1=r["x"], y1=r["y"], x2=x, y2=y, style={"stroke": "#64748b", "opacity": .5}),
                       _mark("text", [r["key"]], r["group"], x=x, y=y, text=labels[r["id"]], anchor="start")])
        previous = y
    return result


def _effect_input(data):
    _population(data, "gene")
    binding = _binding(data["binding"])
    estimators = data["estimators"]
    _fields(estimators, ["raw", "shrunken"])
    for name, estimator in estimators.items():
        _fields(estimator, ["id", "uncertainty_type", "uncertainty_unit"])
        _text(estimator["id"], "estimator ID")
        _text(estimator["uncertainty_type"], "uncertainty type")
        if estimator["uncertainty_unit"] != binding["effect_unit"]:
            raise ValueError(f"{name} uncertainty unit does not match its effect")
    genes = deepcopy(_records(data["genes"], "genes"))
    for g in genes:
        _fields(g, ["gene_id", "base_mean", "raw_lfc", "shrunken_lfc", "raw_uncertainty", "shrunken_uncertainty",
                    "p_value", "q_value", "boundary_warning", "filter_status"], ["reason", "disposition"])
        _text(g["gene_id"], "gene ID")
        g["base_mean"] = _number(g["base_mean"], nonnegative=True, missing=True)
        for key in ["raw_lfc", "shrunken_lfc"]:
            g[key] = _number(g[key], missing=True)
        for key in ["raw_uncertainty", "shrunken_uncertainty"]:
            g[key] = _number(g[key], nonnegative=True, missing=True)
        for key in ["p_value", "q_value"]:
            g[key] = _probability(g[key])
        if g["p_value"] is not None and g["q_value"] is not None and g["q_value"] < g["p_value"]:
            raise ValueError("saved BH q must not be below raw p")
        if type(g["boundary_warning"]) is not bool:
            raise ValueError("boundary_warning must be explicitly declared boolean")
        _text(g["filter_status"], "filter status")
        if "disposition" in g:
            _text(g["disposition"], "disposition")
            _calibrated(g["disposition"], data.get("calibration"), binding, g["gene_id"], estimators["raw"]["id"])
    require_unique([g["gene_id"] for g in genes], "gene IDs")
    return genes, estimators, binding


def _effect_marks(rows, uncertainty=False):
    marks = []
    for r in _finite_rows(rows):
        boundary = r["details"]["boundary_warning"]
        marks.append(_mark("point", [r["key"]], r["group"], x=r["x"], y=r["y"], radius=3.5,
                           shape="triangle" if boundary else "circle",
                           style={"fill": "#b45309" if boundary else "#2563eb"}))
        u = r["details"].get("displayed_uncertainty")
        if uncertainty and u is not None:
            marks.append(_mark("line", [r["key"]], r["group"], x1=r["x"], x2=r["x"],
                               y1=r["y"] - u, y2=r["y"] + u, style={"stroke": "#64748b", "opacity": .6}))
    return marks


def _effects(cap, data):
    """Genes have saved raw/shrunken lfc and uncertainties; metadata names each estimator and uncertainty type."""
    genes, estimators, binding = _effect_input(data)
    volcano = cap == "bulk_volcano"
    mode = data.get("probability_mode")
    if volcano and mode not in {"p", "q"}:
        raise ValueError("probability_mode must be p or q")
    alpha = _probability(data["alpha"], missing=False) if "alpha" in data else None
    if alpha == 0:
        raise ValueError("alpha must be positive")
    threshold = _number(data["effect_threshold"], nonnegative=True) if "effect_threshold" in data else None
    labels = _ids(data["label_gene_ids"], "requested gene label IDs", 20) if "label_gene_ids" in data else []
    if set(labels) - {g["gene_id"] for g in genes}:
        raise ValueError("gene labels must be requested from exact source membership")
    panels = []
    for which in ["raw", "shrunken"]:
        rows = []
        for g in genes:
            value = g[f"{which}_lfc"]
            x = value if volcano else math.log10(g["base_mean"] + 1.) if g["base_mean"] is not None else None
            probability = g[f"{mode}_value"] if volcano else None
            y = _neglog(probability) if volcano else value
            reason = "zero_probability_has_no_finite_negative_log10_coordinate" if volcano and probability == 0 else g.get("reason")
            group = "boundary_warning" if g["boundary_warning"] else g.get("disposition", g["filter_status"])
            details = {**g, "binding": binding, "estimator": estimators[which],
                       "displayed_uncertainty": g[f"{which}_uncertainty"], "probability_mode": mode,
                       "calibration_evidence": _calibration_detail(g.get("disposition", ""), data.get("calibration"), g["gene_id"])}
            rows.append(_row(g["gene_id"], x, y, group=group, details=details, reason=reason))
        refs = []
        if volcano:
            axes = _axes(f"{which} log2 fold change ({estimators[which]['id']})", "log2_fold_change",
                         f"-log10(saved {mode})", "negative_log10_probability", yt="negative_log10")
            if alpha is not None:
                refs.append({"axis": "y", "value": -math.log10(alpha), "label": f"declared {mode} threshold {alpha:g}"})
            if threshold is not None:
                refs.extend({"axis": "x", "value": sign * threshold, "label": "declared effect threshold"} for sign in [-1, 1])
        else:
            axes = _axes("log10(baseMean + 1)", "log10_normalized_count_plus_one",
                         f"{which} log2 fold change ({estimators[which]['id']})", "log2_fold_change", xt="log10_plus_one")
            refs = [{"axis": "y", "value": x, "label": "effect reference"} for x in [-1., 0., 1.]]
        panels.append(_panel(cap, f"{which.title()} {'volcano' if volcano else 'MA'}", rows, axes=axes,
            suffix=which, marks=_effect_marks(rows, uncertainty=not volcano)+_label_marks(rows, {g: g for g in labels}), reference_lines=refs,
            annotations=[f"Estimator: {estimators[which]['id']}; uncertainty: {estimators[which]['uncertainty_type']} in {estimators[which]['uncertainty_unit']}.",
                         "MA whiskers show ± one saved named uncertainty, not an inferred confidence interval."] if not volcano else
                        [f"Estimator: {estimators[which]['id']}; vertical statistic: saved {mode}.", "Zero p/q is retained without a plotting floor."],
            limitations=["Boundary symbols use saved warnings only; effect magnitude does not establish a boundary.",
                         "Threshold crossings alone do not establish calibration or a calibrated discovery."]))
    return panels


def _dispersion(cap, data):
    """genes: gene_id, mean, gene_wise, final, boundary_warning; trend: saved mean/dispersion pairs."""
    _population(data, "gene")
    if data["dispersion_unit"] != "dimensionless" or data["mean_unit"] != "normalized_count":
        raise ValueError("dispersion requires dimensionless alpha and normalized-count mean")
    _text(data["model_id"], "model ID")
    _text(data["trend_method"], "saved trend method")
    genes = _records(data["genes"], "genes")
    rows = []
    for g in genes:
        _fields(g, ["gene_id", "mean", "gene_wise", "final", "boundary_warning"], ["reason", "convergence_status", "warnings"])
        _text(g["gene_id"], "gene ID")
        if type(g["boundary_warning"]) is not bool:
            raise ValueError("dispersion boundary warning must be a saved boolean")
        mean = _number(g["mean"], nonnegative=True, missing=True)
        convergence = g.get("convergence_status")
        if convergence is not None and convergence not in {"converged", "nonconverged", "failed", "not_assessed"}:
            raise ValueError("dispersion convergence_status must preserve an explicit saved assessment")
        if convergence is None and g.get("reason") == "Saved dispersion unavailable or nonconverged.":
            convergence = "convergence_not_verified"
        supplied_warnings = g.get("warnings", [])
        if not isinstance(supplied_warnings, list):
            raise ValueError("dispersion warnings must be a list of saved warning labels")
        for warning in supplied_warnings:
            _text(warning, "saved dispersion warning")
        for which in ["gene_wise", "final"]:
            value = _number(g[which], nonnegative=True, missing=True)
            rows.append(_row(g["gene_id"], _log10(mean), _log10(value), group=which, key=f"{g['gene_id']}:{which}",
                             details={**g, "estimate_layer": which, "dispersion": value}, reason=g.get("reason"),
                             warnings=supplied_warnings+(["saved_boundary_warning"] if g["boundary_warning"] else []), source_status=convergence))
    require_unique([g["gene_id"] for g in genes], "gene IDs")
    trend_rows = []
    for i, t in enumerate(_records(data["trend"], "saved trend")):
        _fields(t, ["mean", "dispersion"], ["reason"])
        mean = _number(t["mean"], nonnegative=True)
        value = _number(t["dispersion"], nonnegative=True, missing=True)
        trend_rows.append(_row(f"trend-{i}", _log10(mean), _log10(value), kind="trend", group="trend", details=t, reason=t.get("reason")))
    if len({t["mean"] for t in data["trend"]}) != len(trend_rows):
        raise ValueError("saved trend means must be unique")
    rows += trend_rows
    marks = []
    for r in _finite_rows(rows):
        color = {"gene_wise": "#64748b", "final": "#2563eb", "trend": "#dc2626"}[r["group"]]
        marks.append(_mark("point", [r["key"]], r["group"], x=r["x"], y=r["y"], radius=3,
                           shape="triangle" if r["warnings"] else "circle", style={"fill": color}))
    segment = []
    for r in sorted(trend_rows, key=lambda r: r["details"]["mean"]) + [None]:
        if r is not None and r["x"] is not None and r["y"] is not None:
            segment.append(r)
        else:
            if len(segment) > 1:
                marks.append(line_mark([[r["x"], r["y"]] for r in segment], keys=[r["key"] for r in segment], group="trend", style={"stroke": "#dc2626"}))
            segment = []
    return [_panel(cap, "Saved dispersion layers", rows, suffix="layers", marks=marks,
                   axes=_axes("log10(mean normalized count)", "log10_normalized_count", "log10(dispersion alpha)", "log10_dimensionless", "log10", "log10"),
                   annotations=[f"Saved trend: {data['trend_method']}; model: {data['model_id']}.", "Zero quantities have no finite log10 coordinate."],
                   limitations=["A displayed fitted trend is not independent evidence of model adequacy.",
                                "Finite values from failed, nonconverged or unassessed saved fits retain their original coordinates and explicit warning glyphs; they are not successful estimates."])]


def _empirical_mean_variance(cap, data):
    """Saved empirical_mean/empirical_variance per gene, with exact contributing sample_ids and declared variance ddof.

    No counts, NB alpha or fitted trend is substituted for the empirical
    quantities. The saved measurement scale and squared variance units are
    explicit, as is the optional identity-versus-log10 display transformation.
    """
    _population(data, "gene")
    samples = _samples({**data, "population_unit": "sample"})
    sample_map = {s["sample_id"]: s for s in samples}
    scale = data["measurement_scale"]
    expected_unit = {"raw_count": "raw_count", "normalized_count": "normalized_count", "log2_normalized_count": "log2_normalized_count",
                     "vst": "vst_expression", "rlog": "rlog_expression", "continuous": None}
    if scale not in expected_unit:
        raise ValueError("empirical mean/variance requires a declared supported measurement scale")
    mean_unit = _text(data["mean_unit"], "empirical mean unit")
    if expected_unit[scale] is not None and mean_unit != expected_unit[scale]:
        raise ValueError("empirical mean unit does not match the declared measurement scale")
    if data["variance_unit"] != f"{mean_unit}_squared":
        raise ValueError("empirical variance unit must be the squared declared measurement unit, not NB dispersion")
    for key in ["mean_definition", "variance_definition"]:
        _text(data[key], key)
    ddof = data["variance_ddof"]
    if type(ddof) is not int or not 0 <= ddof <= 1000:
        raise ValueError("empirical variance ddof must be an explicit nonnegative integer")
    transform = data["display_transform"]
    if transform not in {"identity", "log10"}:
        raise ValueError("empirical mean/variance display_transform must be identity or log10")
    genes = _records(data["genes"], "saved empirical gene summaries")
    rows = []
    for gene in genes:
        _fields(gene, ["gene_id", "empirical_mean", "empirical_variance", "sample_ids"], ["reason"])
        _text(gene["gene_id"], "empirical gene ID")
        ids = [] if gene["sample_ids"] == [] else _ids(gene["sample_ids"], "contributing empirical sample IDs", 1000)
        if set(ids) - set(sample_map):
            raise ValueError("empirical moments must bind their actual contributing samples to declared biological metadata")
        mean = _number(gene["empirical_mean"], nonnegative=scale in {"raw_count", "normalized_count"}, missing=True)
        variance = _number(gene["empirical_variance"], nonnegative=True, missing=True)
        if (not ids and mean is not None) or (len(ids) <= ddof and variance is not None):
            raise ValueError("saved empirical moments require enough actual sample observations for their declared ddof")
        x, y = (mean, variance) if transform == "identity" else (_log10(mean), _log10(variance))
        reason = gene.get("reason")
        if reason is None and transform == "log10" and ((mean is not None and mean <= 0) or variance == 0):
            reason = "nonpositive_empirical_quantity_has_no_finite_log10_coordinate"
        details = {**gene, "measurement_scale": scale, "mean_unit": mean_unit, "variance_unit": data["variance_unit"],
                   "variance_ddof": ddof, "mean_definition": data["mean_definition"], "variance_definition": data["variance_definition"],
                   "biological_unit": data["biological_unit"], "sample_biological_units": {sid: sample_map[sid]["biological_unit_id"] for sid in ids},
                   "n_observations": len(ids)}
        rows.append(_row(gene["gene_id"], x, y, group="saved_empirical", details=details, reason=reason))
    require_unique([r["id"] for r in rows], "empirical gene IDs")
    prefix = "log10(" if transform == "log10" else ""
    suffix = ")" if transform == "log10" else ""
    return [_panel(cap, "Saved empirical gene mean–variance", rows, suffix="empirical",
                   axes=_axes(f"{prefix}saved empirical mean{suffix}", f"log10_{mean_unit}" if transform == "log10" else mean_unit,
                              f"{prefix}saved empirical variance{suffix}", f"log10_{data['variance_unit']}" if transform == "log10" else data["variance_unit"], transform, transform),
                   annotations=[f"Measurement scale: {scale}; empirical variance ddof={ddof}.", f"Mean: {data['mean_definition']}.", f"Variance: {data['variance_definition']}.",
                                "Zeros are retained; a log10 display gives nonpositive quantities missing coordinates rather than a floor."],
                   limitations=["Empirical variance is distinct from negative-binomial dispersion alpha; no dispersion or fitted mean–variance trend is inferred.",
                                "These descriptive sample moments do not make repeated specimens independent biological replicates."])]


def _samples(data):
    _population(data, "sample")
    _text(data["biological_unit"], "biological unit")
    result = deepcopy(_records(data["samples"], "samples", 1000))
    for s in result:
        _fields(s, ["sample_id", "biological_unit_id", "condition"])
        for key in s:
            _text(s[key], key)
    require_unique([s["sample_id"] for s in result], "sample IDs")
    return result


def _pairs(pairing, samples, biological_unit):
    _fields(pairing, ["biological_unit", "from_condition", "to_condition", "pairs"])
    if pairing["biological_unit"] != biological_unit or biological_unit.lower() in {"sample", "library", "cell", "unknown"}:
        raise ValueError("pairing requires an explicit biological unit beyond sample/library identity")
    for key in ["from_condition", "to_condition"]:
        _text(pairing[key], key)
    if pairing["from_condition"] == pairing["to_condition"]:
        raise ValueError("paired direction requires distinct conditions")
    sample_map = {s["sample_id"]: s for s in samples}
    pairs = _records(pairing["pairs"], "pairs", 1000)
    used = []
    units = []
    for p in pairs:
        _fields(p, ["pair_id", "biological_unit_id", "from_sample", "to_sample"])
        for value in p.values():
            _text(value, "pair identity")
        _biological_identity(p["biological_unit_id"])
        if p["from_sample"] not in sample_map or p["to_sample"] not in sample_map:
            raise ValueError("pair references an unknown sample")
        a, b = sample_map[p["from_sample"]], sample_map[p["to_sample"]]
        if (a["biological_unit_id"] != p["biological_unit_id"] or b["biological_unit_id"] != p["biological_unit_id"]
                or a["condition"] != pairing["from_condition"] or b["condition"] != pairing["to_condition"]):
            raise ValueError("pair does not match declared biological identity and direction")
        used.extend([p["from_sample"], p["to_sample"]])
        units.append(p["biological_unit_id"])
    require_unique([p["pair_id"] for p in pairs], "pair IDs")
    require_unique(used, "paired sample membership")
    require_unique(units, "paired biological units")
    if set(used) != set(sample_map):
        raise ValueError("explicit pairing must account for all supplied samples")
    return pairs


def _score_rows(scores, samples, dimensions):
    score_map = {}
    for s in _records(scores, "saved scores", 1000):
        _fields(s, ["sample_id", "scores"], ["reason"])
        _text(s["sample_id"], "sample ID")
        if s["sample_id"] in score_map:
            raise ValueError("duplicate score sample ID")
        if not isinstance(s["scores"], list) or len(s["scores"]) != dimensions:
            raise ValueError("saved score dimensions do not match declared components")
        score_map[s["sample_id"]] = {**s, "scores": [_number(v, missing=True) for v in s["scores"]]}
    if set(score_map) != {s["sample_id"] for s in samples}:
        raise ValueError("score IDs must exactly match declared sample membership")
    return score_map


def _score_panel(cap, title, samples, scores, components, axes, suffix, pairs=()):
    rows = [_row(s["sample_id"], scores[s["sample_id"]]["scores"][components[0]], scores[s["sample_id"]]["scores"][components[1]],
                 kind="sample", group=s["condition"], details={**s, **scores[s["sample_id"]]}, reason=scores[s["sample_id"]].get("reason")) for s in samples]
    marks = point_marks(rows)
    by_id = {r["id"]: r for r in rows}
    shapes = ["circle", "square", "diamond", "triangle"]
    units = sorted({s["biological_unit_id"] for s in samples})
    shape_map = {unit: shapes[i % len(shapes)] for i, unit in enumerate(units)}
    for mark in marks:
        mark["shape"] = shape_map[by_id[mark["keys"][0]]["details"]["biological_unit_id"]]
    if len(samples) <= 50:
        marks.extend(_label_marks(rows, {s["sample_id"]: f"{s['biological_unit_id']} / {s['sample_id']}" for s in samples}))
    for pair in pairs:
        a, b = by_id[pair["from_sample"]], by_id[pair["to_sample"]]
        if a["x"] is not None and a["y"] is not None and b["x"] is not None and b["y"] is not None:
            marks.append(_mark("arrow", [a["key"], b["key"]], pair["pair_id"], x1=a["x"], y1=a["y"], x2=b["x"], y2=b["y"],
                               style={"stroke": "#64748b", "opacity": .7}))
    annotations = ["Arrows use the exact supplied biological-unit pairs and condition direction."] if pairs else ["Pairing was not supplied; no pairing is inferred."]
    annotations.append("Biological-unit shapes: " + "; ".join(f"{unit}={shape_map[unit]}" for unit in units) + ".")
    if len(units) > len(shapes):
        annotations.append("The four-shape cycle repeats; exact biological-unit identities are retained in rows and labels.")
    return _panel(cap, title, rows, axes=axes, suffix=suffix, marks=marks, annotations=annotations)


def _pca(cap, data):
    """Saved scores/loadings use identical component order; loading_scale explicitly defines the biplot projection."""
    samples = _samples(data)
    ratios = [_probability(x, missing=False) for x in _records(data["explained_variance_ratio"], "PCA variance ratios", 1000)]
    if len(ratios) < 2 or sum(ratios) > 1 + 1e-8:
        raise ValueError("PC1/2 requires at least two valid explained variance ratios with total <= 1")
    for field in ["score_unit", "transformation"]:
        _text(data[field], field)
    scores = _score_rows(data["scores"], samples, len(ratios))
    pairs = _pairs(data["pairing"], samples, data["biological_unit"]) if "pairing" in data else []
    panels = []
    for a, b in [(0, 1)] + ([(2, 3)] if len(ratios) >= 4 else []):
        axes = _axes(f"PC{a+1} ({100*ratios[a]:.1f}% variance)", data["score_unit"], f"PC{b+1} ({100*ratios[b]:.1f}% variance)", data["score_unit"])
        p = _score_panel(cap, f"Saved PCA PC{a+1}/PC{b+1}", samples, scores, (a, b), axes, f"pc{a+1}-{b+1}", pairs)
        p["annotations"].append(f"Saved transformation: {data['transformation']}.")
        panels.append(p)
    rows = [_row(f"PC{i+1}", float(i+1), 100*value, kind="component", details={"explained_variance_ratio": value}) for i, value in enumerate(ratios)]
    marks = [_mark("rect", [r["key"]], "variance", x=r["x"]-.35, y=0., width=.7, height=r["y"]) for r in rows]
    panels.append(_panel(cap, "Saved PCA explained variance", rows, suffix="scree", marks=marks,
                         axes=_axes("Principal component", "component_index", "Explained variance", "percent")))
    if len(ratios) < 4:
        for p in panels:
            p["annotations"].append("PC3/PC4 unavailable: fewer than four saved score components and explained variance ratios were supplied; PC1/2 and scree remain available.")
    loadings = data.get("loadings")
    if loadings is None or loadings == []:
        for p in panels:
            p["annotations"].append("Loading biplot unavailable: saved gene loadings were not supplied; sample PCA is retained independently.")
        return panels
    missing_loading_metadata = [field for field in ["loading_scale", "loading_unit", "loading_definition"] if field not in data]
    if missing_loading_metadata:
        for p in panels:
            p["annotations"].append("Loading biplot unavailable: supplied loadings lack " + ", ".join(missing_loading_metadata) + "; sample PCA is retained independently.")
        return panels
    for field in ["loading_unit", "loading_definition"]:
        _text(data[field], field)
    scale = _number(data["loading_scale"], positive=True)
    biplot_rows = deepcopy(panels[0]["rows"])
    biplot_marks = deepcopy(panels[0]["marks"])
    loadings = _records(loadings, "saved loadings")
    require_unique([l.get("gene_id") for l in loadings], "loading gene IDs")
    for l in loadings:
        _fields(l, ["gene_id", "loadings"], ["reason"])
        _text(l["gene_id"], "loading gene ID")
        if not isinstance(l["loadings"], list) or len(l["loadings"]) != len(ratios):
            raise ValueError("loading components must match the saved score dimensions")
        values = [_number(v, missing=True) for v in l["loadings"]]
        r = _row(l["gene_id"], values[0]*scale if values[0] is not None else None, values[1]*scale if values[1] is not None else None,
                 group="loading", details={**l, "loading_scale": scale, "loading_unit": data["loading_unit"], "loading_definition": data["loading_definition"]},
                 key=f"loading:{l['gene_id']}", reason=l.get("reason"))
        biplot_rows.append(r)
        if r["x"] is not None and r["y"] is not None:
            biplot_marks.extend([_mark("arrow", [r["key"]], "loading", x1=0., y1=0., x2=r["x"], y2=r["y"], style={"stroke": "#b45309"}),
                                 _mark("text", [r["key"]], "loading", x=r["x"], y=r["y"], text=r["id"], anchor="start")])
    panels.append(_panel(cap, "PCA scores and saved gene loadings", biplot_rows, axes=panels[0]["axes"], suffix="biplot", marks=biplot_marks,
                         annotations=[f"Loading arrows = saved {data['loading_definition']} × declared display scale {scale:g}; loading unit {data['loading_unit']}.",
                                      f"Saved sample transformation: {data['transformation']}."]))
    return panels


def _matrix(values, nrows, ncols, *, nonnegative=False, missing=False, integer=False):
    if nrows * ncols > 1_000_000 or not isinstance(values, list) or len(values) != nrows:
        raise ValueError("scientific matrix must match bounded declared row membership")
    result = []
    for values_row in values:
        if not isinstance(values_row, list) or len(values_row) != ncols:
            raise ValueError("scientific matrix columns do not match sample membership")
        result_row = []
        for value in values_row:
            value = _number(value, nonnegative=nonnegative, missing=missing)
            if integer and value is not None and (value != math.floor(value) or value > 2**53 - 1):
                raise ValueError("raw counts require exact nonnegative integers")
            result_row.append(value)
        result.append(result_row)
    return result


def _symmetric(values, ids, *, correlation=False, missing=False):
    values = _matrix(values, len(ids), len(ids), nonnegative=not correlation, missing=missing)
    for i, values_row in enumerate(values):
        expected = 1. if correlation else 0.
        if values_row[i] is not None and not math.isclose(values_row[i], expected, abs_tol=1e-10):
            raise ValueError("correlation diagonal must be one; distance diagonal must be zero")
        for j, value in enumerate(values_row):
            if value is None:
                continue
            if correlation and not -1 <= value <= 1:
                raise ValueError("correlations must be in [-1,1]")
            if values[j][i] is not None and not math.isclose(value, values[j][i], rel_tol=1e-10, abs_tol=1e-10):
                raise ValueError("sample distance/correlation matrix must be symmetric")
    return values


def _sample_order(ids, samples):
    ids = _ids(ids, "sample matrix IDs", 1000)
    if set(ids) != {s["sample_id"] for s in samples}:
        raise ValueError("sample matrix IDs must exactly match declared samples")
    return ids


def _mds(cap, data):
    """mode=distance_matrix declares metric/unit/order; saved_scores uses named saved scores and optional eigenvalues."""
    samples = _samples(data)
    pairs = _pairs(data["pairing"], samples, data["biological_unit"]) if "pairing" in data else []
    mode = data["mode"]
    eigenvalues = None
    if mode == "distance_matrix":
        for key in ["sample_ids", "distance_matrix", "distance_definition", "distance_unit"]:
            if key not in data:
                raise ValueError(f"distance-matrix MDS requires {key}")
        if any(k in data for k in ["scores", "score_unit", "eigenvalues", "eigenvalue_definition", "method"]):
            raise ValueError("MDS distance and saved-score sources must not be mixed")
        ids = _sample_order(data["sample_ids"], samples)
        distances = _symmetric(data["distance_matrix"], ids)
        _text(data["distance_definition"], "distance definition")
        unit = _text(data["distance_unit"], "distance unit")
        import numpy as np
        matrix = np.asarray(distances, dtype=float)
        squared = matrix * matrix
        if not np.isfinite(squared).all():
            raise ValueError("distance magnitudes overflow classical MDS squared distances")
        center = np.eye(len(ids)) - np.ones((len(ids), len(ids))) / len(ids)
        gram = -.5 * center @ squared @ center
        eigenvalues_array, eigenvectors = np.linalg.eigh(gram)
        order = np.argsort(-eigenvalues_array, kind="stable")
        eigenvalues_array, eigenvectors = eigenvalues_array[order], eigenvectors[:, order]
        tolerance = max(float(np.max(np.abs(eigenvalues_array))), 1.) * len(ids) * np.finfo(float).eps * 10
        eigenvalues_array[np.abs(eigenvalues_array) <= tolerance] = 0.
        eigenvalues = [float(v) for v in eigenvalues_array]
        coordinates = np.zeros((len(ids), 2))
        for axis in range(min(2, len(ids))):
            vec = eigenvectors[:, axis]
            if vec[int(np.argmax(np.abs(vec)))] < 0:
                vec = -vec
            if eigenvalues[axis] > 0:
                coordinates[:, axis] = vec * math.sqrt(eigenvalues[axis])
        scores = {identity: {"sample_id": identity, "scores": list(map(float, coordinates[i]))} for i, identity in enumerate(ids)}
        method = "Classical MDS: double-centered squared distances; symmetric eigendecomposition; eigenvalue descending order; largest-magnitude coordinate positive sign."
        description = data["distance_definition"]
    elif mode == "saved_scores":
        for key in ["scores", "score_unit", "method"]:
            if key not in data:
                raise ValueError(f"saved MDS requires {key}")
        if any(k in data for k in ["sample_ids", "distance_matrix", "distance_definition", "distance_unit"]):
            raise ValueError("saved MDS must not mix an alternative distance source")
        unit = _text(data["score_unit"], "MDS score unit")
        method = _text(data["method"], "MDS method")
        scores = _score_rows(data["scores"], samples, 2)
        description = "Saved scores; no MDS fitting was performed."
        if "eigenvalues" in data:
            if data.get("eigenvalue_definition") != "classical_mds_centered_squared_distance":
                raise ValueError("positive eigenvalue shares require the declared classical MDS spectrum")
            eigenvalues = [_number(v) for v in _records(data["eigenvalues"], "MDS eigenvalues", 1000)]
            if len(eigenvalues) < 2 or any(eigenvalues[i] < eigenvalues[i+1] for i in range(len(eigenvalues)-1)):
                raise ValueError("saved MDS eigenvalues must follow descending component order")
        elif "eigenvalue_definition" in data:
            raise ValueError("eigenvalue definition requires actual saved eigenvalues")
    else:
        raise ValueError("MDS mode must be distance_matrix or saved_scores")
    positive_total = sum(max(v, 0.) for v in eigenvalues or [])
    labels = []
    for i in range(2):
        fraction = max(eigenvalues[i], 0.) / positive_total if eigenvalues and len(eigenvalues) > i and positive_total else 0.
        labels.append(f"MDS{i+1} ({100*fraction:.1f}% positive eigenvalue mass)" if eigenvalues is not None else f"Saved MDS{i+1}")
    p = _score_panel(cap, "Sample MDS", samples, scores, (0, 1), _axes(labels[0], unit, labels[1], unit), "scores", pairs)
    p["annotations"].extend([method, description])
    p["limitations"].append("MDS positive-eigenvalue fractions are not PCA explained variance. Negative eigenvalues indicate non-Euclidean geometry lost by a positive-coordinate display.")
    panels = [p]
    if eigenvalues is not None:
        rows = [_row(f"MDS{i+1}", i+1., v, kind="component", group="positive" if v > 0 else "negative" if v < 0 else "zero",
                     details={"eigenvalue": v, "positive_eigenvalue_fraction": max(v, 0.)/positive_total if positive_total else 0.}) for i, v in enumerate(eigenvalues)]
        marks = [_mark("rect", [r["key"]], r["group"], x=r["x"]-.3, y=min(r["y"], 0.), width=.6, height=abs(r["y"])) for r in rows]
        panels.append(_panel(cap, "MDS eigenvalue spectrum", rows, suffix="spectrum", marks=marks,
                             axes=_axes("MDS component", "component_index", "Centered squared-distance eigenvalue", f"{unit}_squared"),
                             reference_lines=[{"axis": "y", "value": 0., "label": "zero eigenvalue"}],
                             annotations=[f"Sum of positive eigenvalues: {positive_total:g}; sum of negative eigenvalues: {sum(v for v in eigenvalues if v < 0):g}."]))
    return panels


def _cluster_distance(distances, ids):
    if len(ids) == 1:
        return [0], []
    from scipy.cluster.hierarchy import leaves_list, linkage
    from scipy.spatial.distance import squareform
    import numpy as np
    tree = linkage(squareform(np.asarray(distances), checks=False), method="average", optimal_ordering=False)
    return list(map(int, leaves_list(tree))), tree.tolist()


def _heatmap(cap, data):
    """A symmetric saved matrix and exact sample IDs; covariate maps are keyed by those IDs, never position inferred."""
    samples = _samples(data)
    ids = _sample_order(data["sample_ids"], samples)
    correlation = cap == "bulk_sample_correlation_heatmap"
    values = _symmetric(data["matrix"], ids, correlation=correlation, missing=True)
    definition = _text(data["correlation_definition" if correlation else "distance_definition"], "matrix definition")
    unit = _text(data["unit"], "matrix unit")
    if correlation and unit != "correlation":
        raise ValueError("sample correlation values require correlation units")
    expected_metric = "one_minus_correlation" if correlation else "precomputed"
    if data["cluster"] is not False and data["cluster"] != {"method": "average", "metric": expected_metric}:
        raise ValueError("clustering requires explicit average linkage and the actual precomputed or one-minus-correlation metric")
    distances = [[max(0., 1.-x) if x is not None else None for x in r] for r in values] if correlation else values
    incomplete = any(x is None for r in values for x in r)
    order, tree = _cluster_distance(distances, ids) if data["cluster"] and not incomplete else (list(range(len(ids))), [])
    ordered_ids = [ids[i] for i in order]
    ordered_values = [[values[i][j] for j in order] for i in order]
    label = "Sample correlation" if correlation else "Sample distance"
    p = matrix_panel(cap, label, ordered_values, ordered_ids, ordered_ids, unit=unit, cluster=False, diverging=correlation,
                     details={"definition": definition, "original_sample_order": ids, "display_sample_order": ordered_ids,
                              "row_entity_kind": "sample", "column_entity_kind": "sample"})
    p["id"] = f"{cap}:matrix"
    if correlation:
        p["color_scale"]["domain"] = [-1., 1.]
        p["color_scale"]["center"] = 0
    p["annotations"].append(f"Definition: {definition}. Clustering: {'average linkage using 1-r dissimilarity' if correlation else 'average linkage using supplied distances'}; stable input order resolves ties." if tree else f"Definition: {definition}; clustering not performed.")
    if incomplete and data["cluster"]:
        p["annotations"].append("Clustering unavailable: sample dissimilarities contain missing values; supplied sample order is preserved and no missing distances are imputed.")
    for r in p["rows"]:
        if r["x"] is not None and r["y"] is not None:
            a, b = ordered_ids[int(r["y"])], ordered_ids[int(r["x"])]
            r["details"].update(row_sample_id=a, column_sample_id=b)
            r["link"] = {"kind": "sample", "id": b}
    p["limitations"].extend(_LIMITS)
    panels = [p]
    if tree:
        position = {original: float(new) for new, original in enumerate(order)}
        heights = {i: 0. for i in range(len(ids))}
        members = {i: [ids[i]] for i in range(len(ids))}
        rows = [_row(s, float(i), 0., kind="sample", details={"display_order": i}) for i, s in enumerate(ordered_ids)]
        keys = {r["id"]: r["key"] for r in rows}
        marks = []
        for index, (a, b, height, _) in enumerate(tree):
            a, b = int(a), int(b)
            node = len(ids) + index
            marks.append(line_mark([[position[a], heights[a]], [position[a], height], [position[b], height], [position[b], heights[b]]],
                                   keys=[keys[s] for s in members[a] + members[b]], group="average_linkage"))
            position[node], heights[node], members[node] = (position[a]+position[b])/2., height, members[a]+members[b]
        panels.append(_panel(cap, "Sample dendrogram", rows, suffix="dendrogram", marks=marks,
                             axes=_axes("Sample", "sample", "Average-linkage dissimilarity", "1_minus_correlation" if correlation else unit),
                             x_categories=ordered_ids, annotations=["Average linkage on the supplied sample dissimilarities; no Euclidean row-profile substitution."]))
    tracks = data["covariate_tracks"]
    if not isinstance(tracks, dict) or not tracks or len(tracks) > 50:
        raise ValueError("covariate_tracks must be a nonempty bounded map")
    palette = ["#0072b2", "#e69f00", "#009e73", "#cc79a7", "#d55e00", "#56b4e9"]
    for track_name, mapping in tracks.items():
        _text(track_name, "covariate name")
        if not isinstance(mapping, dict) or set(mapping) != set(ids):
            raise ValueError("covariate tracks must exactly match sample identities")
        present = [mapping[s] for s in ordered_ids if mapping[s] is not None]
        numeric = all(type(v) in (int, float) for v in present)
        if numeric:
            for v in present:
                _number(v)
            categories = []
        else:
            categories = sorted({_text(v, "categorical covariate") for v in present})
        rows = [_row(s, float(i), 0. if mapping[s] is not None else None, kind="sample",
                     group=str(mapping[s]) if mapping[s] is not None else "missing", details={"covariate": track_name, "value": mapping[s]}) for i, s in enumerate(ordered_ids)]
        marks = []
        for r in _finite_rows(rows):
            value = r["details"]["value"]
            style = {} if numeric else {"fill": palette[categories.index(value) % len(palette)]}
            marks.append(_mark("rect", [r["key"]], r["group"], x=r["x"]-.45, y=-.4, width=.9, height=.8,
                               **({"value": float(value)} if numeric else {}), **({"style": style} if style else {})))
        scale = None
        if numeric and present:
            low, high = min(present), max(present)
            if low == high:
                low, high = low-.5, high+.5
            scale = {"label": track_name, "unit": "saved_covariate", "domain": [low, high], "palette": "viridis", "center": None}
        panels.append(_panel(cap, f"Covariate: {track_name}", rows, suffix=f"covariate:{track_name}", marks=marks,
                             axes=_axes("Sample", "sample", track_name, "covariate_track"), x_categories=ordered_ids,
                             y_categories=[track_name], color_scale=scale,
                             annotations=["Covariate values are supplied declarations aligned by exact sample identity."] + ([f"Categories: {', '.join(categories)}"] if categories else [])))
    return panels


def _histogram(cap, data):
    """Saved raw p/filter_status plus optional saved boundary_warning; any assessed flag requires boundary_evidence provenance."""
    _population(data, "gene")
    _text(data["test_id"], "test ID")
    bins = data["bin_count"]
    if type(bins) is not int or not 20 <= bins <= 50:
        raise ValueError("raw-p diagnostic histogram requires 20..50 declared bins")
    reference = data["uniform_reference"]
    _fields(reference, ["definition", "scope"])
    if reference != {"definition": "hypothetical_uniform_over_observed_finite_p", "scope": "all_finite"}:
        raise ValueError("uniform reference must declare hypothetical uniformity over finite observed p")
    evidence = data.get("boundary_evidence")
    if evidence is not None:
        _fields(evidence, ["evidence_id", "source_field", "definition"])
        for key, value in evidence.items():
            _text(value, f"boundary evidence {key}")
    near_one = _probability(data["boundary_endpoint_cutoff"], missing=False) if "boundary_endpoint_cutoff" in data else (bins-1)/bins
    if near_one <= 0:
        raise ValueError("near-one boundary endpoint display cutoff must be in (0,1]")
    genes = deepcopy(_records(data["genes"], "genes"))
    for g in genes:
        _fields(g, ["gene_id", "p_value", "filter_status"], ["reason", "boundary_warning", "boundary_reason"])
        _text(g["gene_id"], "gene ID")
        _text(g["filter_status"], "saved filter status")
        g["p_value"] = _probability(g["p_value"])
        boundary = g.get("boundary_warning")
        if boundary is not None and type(boundary) is not bool:
            raise ValueError("saved boundary status must be true, false, or explicitly missing")
        if boundary is not None and evidence is None:
            raise ValueError("assessed boundary flags require explicit saved boundary evidence provenance")
        if "boundary_reason" in g:
            _text(g["boundary_reason"], "saved boundary reason")
        g["boundary_warning"] = boundary
    require_unique([g["gene_id"] for g in genes], "gene IDs")
    groups = [None] + sorted({g["filter_status"] for g in genes})
    panels = []
    for index, group in enumerate(groups):
        selected = genes if group is None else [g for g in genes if g["filter_status"] == group]
        counts = Counter(min(int(g["p_value"]*bins), bins-1) for g in selected if g["p_value"] is not None)
        boundary_counts = Counter(min(int(g["p_value"]*bins), bins-1) for g in selected if g["p_value"] is not None and g["boundary_warning"] is True)
        rows = []
        for g in selected:
            value = g["p_value"]
            bin_index = min(int(value*bins), bins-1) if value is not None else None
            rows.append(_row(g["gene_id"], value, float(counts[bin_index]) if value is not None else None,
                             group=g["filter_status"], details={**g, "bin_index": bin_index, "boundary_evidence": evidence if g["boundary_warning"] is not None else None}, reason=g.get("reason"),
                             warnings=["saved_boundary_warning"] if g["boundary_warning"] is True else []))
        marks = []
        for i in range(bins):
            keys = [r["key"] for r in rows if r["details"]["bin_index"] == i and r["details"]["boundary_warning"] is not True]
            unflagged = counts[i]-boundary_counts[i]
            marks.append(_mark("rect", keys, "all" if group is None else group, x=i/bins, y=0., width=1/bins, height=float(unflagged)))
            if boundary_counts[i]:
                boundary_keys = [r["key"] for r in rows if r["details"]["bin_index"] == i and r["details"]["boundary_warning"] is True]
                marks.append(_mark("rect", boundary_keys, "saved_boundary_warning", x=i/bins, y=float(unflagged), width=1/bins,
                                   height=float(boundary_counts[i]), style={"fill": "#b45309"}))
        finite = sum(counts.values())
        p0 = sum(g["p_value"] == 0 for g in selected)
        p1 = sum(g["p_value"] == 1 for g in selected)
        flagged = [g for g in selected if g["boundary_warning"] is True]
        bp0, bp1 = sum(g["p_value"] == 0 for g in flagged), sum(g["p_value"] == 1 for g in flagged)
        near_count = sum(g["p_value"] is not None and g["p_value"] >= near_one for g in flagged)
        boundary_notes = ([f"Saved boundary evidence: {evidence['evidence_id']}; field: {evidence['source_field']}; {evidence['definition']}.",
                           f"Saved boundary-associated p=0: {bp0}; p=1: {bp1}; boundary-associated p>={near_one:g}: {near_count}; boundary status unknown: {sum(g['boundary_warning'] is None for g in selected)}."]
                          if evidence is not None else ["Saved boundary assessment unavailable; endpoint counts have no assigned boundary cause."])
        panels.append(_panel(cap, f"Raw p: {group or 'all saved genes'}", rows, suffix=f"histogram-{index}", marks=marks,
            axes=_axes("Saved raw p", "probability", "Genes per equal-width bin", "gene_count"),
            reference_lines=[{"axis": "y", "value": finite/bins, "label": "hypothetical uniform expected count"}],
            annotations=[f"Test: {data['test_id']}; p=0: {p0}; p=1: {p1}; missing p: {len(selected)-finite}.",
                         "Bins are left closed/right open except the final bin includes p=1."]+boundary_notes,
            limitations=["The hypothetical uniform reference and endpoint spikes do not establish null calibration or identify a boundary mechanism."]))
    return panels


def _filter_curve(cap, data):
    """curve contains actual saved quantile/rejections pairs, plus saved alpha and selected quantile identity."""
    _population(data, "gene")
    alpha = _probability(data["alpha"], missing=False)
    if not 0 < alpha < 1 or data["quantile_unit"] != "fraction":
        raise ValueError("filtering requires 0<alpha<1 and quantiles as fractions")
    for key in ["selection_id", "filter_statistic", "selection_rule"]:
        _text(data[key], key)
    n = data["tested_gene_count"]
    if type(n) is not int or n < 0:
        raise ValueError("tested gene count must be a nonnegative integer")
    selected = _probability(data["selected_quantile"], missing=False)
    rows = []
    for i, c in enumerate(_records(data["curve"], "saved rejection curve")):
        _fields(c, ["quantile", "rejections"], ["reason"])
        q = _probability(c["quantile"], missing=False)
        count = _number(c["rejections"], nonnegative=True, missing=True)
        if count is not None and (count != int(count) or count > n):
            raise ValueError("rejections must be integer counts within the declared tested universe")
        rows.append(_row(f"filter-{i}", q, count, kind="filter_threshold", details={**c, "selection_id": data["selection_id"]}, reason=c.get("reason")))
    if len({r["x"] for r in rows}) != len(rows):
        raise ValueError("saved filter quantiles must be unique")
    if not any(r["x"] == selected and r["y"] is not None for r in rows):
        raise ValueError("selected filter quantile must have an actual saved rejection count")
    rows.sort(key=lambda r: r["x"])
    marks = point_marks(rows)
    segment = []
    for r in rows + [None]:
        if r is not None and r["y"] is not None:
            segment.append(r)
        else:
            if len(segment) >= 2:
                marks.append(line_mark([[p["x"], p["y"]] for p in segment], keys=[p["key"] for p in segment], group="saved_rejections"))
            segment = []
    return [_panel(cap, "Saved independent-filter rejection curve", rows, suffix="curve", marks=marks,
        axes=_axes(f"{data['filter_statistic']} filter quantile", "fraction", "Saved rejected genes", "gene_count"),
        reference_lines=[{"axis": "x", "value": selected, "label": "saved selected quantile"}],
        annotations=[f"Saved alpha={alpha:g}; selection ID={data['selection_id']}; rule={data['selection_rule']}.", f"Tested gene count: {n}."],
        limitations=["No independent filtering, q values or selection optimization were recomputed; post-selection inference remains selection conditional."])]


def _paired(cap, data):
    """Saved normalized_counts[gene][sample], exact biological pairs and an explicit absolute-log2-difference-share definition."""
    samples = _samples(data)
    pairs = _pairs(data["pairing"], samples, data["biological_unit"])
    genes = _ids(data["gene_ids"], "gene IDs")
    if data["expression_unit"] != "normalized_count":
        raise ValueError("paired count facets require saved normalized counts")
    if data["contribution_definition"] != "absolute_log2_difference_share":
        raise ValueError("pair contribution is descriptive absolute log2 difference share, not model influence")
    _text(data["normalization"], "saved normalization")
    offset = _number(data["pseudocount"], positive=True)
    values = _matrix(data["normalized_counts"], len(genes), len(samples), nonnegative=True, missing=True)
    categories = [data["pairing"]["from_condition"], data["pairing"]["to_condition"]]
    panels = []
    for gi, gene_id in enumerate(genes):
        rows = []
        for si, sample in enumerate(samples):
            count = values[gi][si]
            y = math.log2(count+offset) if count is not None else None
            rows.append(_row(sample["sample_id"], float(categories.index(sample["condition"])), y, kind="sample",
                             group=sample["biological_unit_id"], details={**sample, "gene_id": gene_id, "normalized_count": count,
                                                                          "normalization": data["normalization"], "pseudocount": offset}))
        by_sample = {r["id"]: r for r in rows}
        marks = point_marks(rows)
        differences = []
        for pair in pairs:
            a, b = by_sample[pair["from_sample"]], by_sample[pair["to_sample"]]
            difference = b["y"]-a["y"] if a["y"] is not None and b["y"] is not None else None
            differences.append(difference)
            if difference is not None:
                marks.append(_mark("line", [a["key"], b["key"]], pair["pair_id"], x1=a["x"], y1=a["y"], x2=b["x"], y2=b["y"]))
        panels.append(_panel(cap, f"{gene_id}: paired expression", rows, suffix=f"{gene_id}:expression", marks=marks,
            axes=_axes("Declared condition direction", "condition", f"log2(normalized count + {offset:g})", "log2_normalized_count_plus_pseudocount", yt="log2_plus_declared_pseudocount"),
            x_categories=categories, annotations=[f"Saved normalization: {data['normalization']}; biological unit: {data['biological_unit']}."]))
        complete = all(v is not None for v in differences)
        denominator = sum(abs(v) for v in differences) if complete else None
        share_rows = []
        for pi, pair in enumerate(pairs):
            value = abs(differences[pi])/denominator if denominator else None
            reason = "incomplete_pair_differences_preclude_all_pair_share" if not complete else "zero_total_absolute_difference_share_undefined" if not denominator else None
            share_rows.append(_row(pair["pair_id"], float(pi), value, kind="pair", group=pair["biological_unit_id"], reason=reason,
                                   details={**pair, "gene_id": gene_id, "displayed_log2_difference": differences[pi],
                                            "total_absolute_difference": denominator, "share_definition": data["contribution_definition"]}))
        share_marks = [_mark("rect", [r["key"]], r["group"], x=r["x"]-.35, y=0., width=.7, height=r["y"]) for r in _finite_rows(share_rows)]
        annotation = f"Maximum pair share = {max(r['y'] for r in share_rows):.6g}." if denominator else "All-pair shares are undefined because at least one difference is missing or the total absolute difference is zero."
        panels.append(_panel(cap, f"{gene_id}: pair contribution shares", share_rows, suffix=f"{gene_id}:shares", marks=share_marks,
            axes=_axes("Declared pair", "pair", "|displayed pair difference| / sum across all pairs", "fraction"),
            x_categories=[p["pair_id"] for p in pairs], annotations=[annotation, "Differences use the displayed log2(normalized count + declared pseudocount) scale."],
            limitations=["These descriptive pair shares are not leave-one-pair-out influence, causal contribution or independent evidence of a fitted treatment effect."]))
    return panels


def _library(cap, data):
    """Counts are raw nonnegative integers with exact gene/sample order; factors are saved and keyed by sample_id."""
    samples = _samples(data)
    genes = _ids(data["gene_ids"], "gene IDs")
    if data["measurement"] != "raw_integer_count":
        raise ValueError("library depth and detected genes require raw integer count measurements")
    _text(data["size_factor_method"], "saved size-factor method")
    counts = _matrix(data["counts"], len(genes), len(samples), nonnegative=True, integer=True, missing=True)
    factors = {}
    for f in _records(data["size_factors"], "saved size factors", 1000):
        _fields(f, ["sample_id", "size_factor"], ["reason"])
        _text(f["sample_id"], "size-factor sample ID")
        if f["sample_id"] in factors:
            raise ValueError("duplicate saved size-factor sample")
        factors[f["sample_id"]] = _number(f["size_factor"], positive=True, missing=True)
    if set(factors) != {s["sample_id"] for s in samples}:
        raise ValueError("size factor IDs must exactly match sample membership")
    measurements = []
    for si, sample in enumerate(samples):
        column = [r[si] for r in counts]
        complete = all(v is not None for v in column)
        depth = sum(int(v) for v in column) if complete else None
        if depth is not None and depth > 2**53-1:
            raise ValueError("library depth exceeds exact JSON numeric integer range")
        measurements.append({**sample, "depth": depth, "detected_gt0": sum(v > 0 for v in column) if complete else None,
                             "detected_ge10": sum(v >= 10 for v in column) if complete else None,
                             "saved_size_factor": factors[sample["sample_id"]], "source_gene_count": len(genes),
                             "missing_count_values": sum(v is None for v in column)})
    panels = []
    labels = [("depth", "Raw library depth", "raw_count"), ("detected_gt0", "Detected genes (>0 raw count)", "gene_count"),
              ("detected_ge10", "Detected genes (>=10 raw counts)", "gene_count")]
    for name, label, unit in labels:
        rows = [_row(s["sample_id"], float(i), s[name], kind="sample", group=s["condition"], details=s) for i, s in enumerate(measurements)]
        marks = [_mark("rect", [r["key"]], r["group"], x=r["x"]-.35, y=0., width=.7, height=r["y"]) for r in _finite_rows(rows)]
        panels.append(_panel(cap, label, rows, suffix=name, marks=marks, x_categories=[s["sample_id"] for s in samples],
                             axes=_axes("Sample", "sample", label, unit),
                             annotations=[f"Declared raw-count gene universe: {len(genes)} genes; incomplete sample columns are not totaled."]))
    rows = [_row(s["sample_id"], s["depth"], s["saved_size_factor"], kind="sample", group=s["condition"], details=s) for s in measurements]
    panels.append(_panel(cap, "Saved size factor versus raw depth", rows, suffix="factor-depth", axes=_axes("Raw library depth", "raw_count", "Saved size factor", "dimensionless"),
                         annotations=[f"Saved factor method: {data['size_factor_method']}."], limitations=["No size factors were estimated and this display alone does not validate normalization."]))
    return panels


def _cooks(cap, data):
    """Saved Cook's distances[gene][sample]; nonpositive log geometry is absent, never floored."""
    samples = _samples(data)
    genes = _ids(data["gene_ids"], "gene IDs")
    _text(data["model_id"], "saved model ID")
    if data["distance_unit"] != "dimensionless":
        raise ValueError("Cook's distances must be dimensionless")
    values = _matrix(data["cooks_distances"], len(genes), len(samples), nonnegative=True, missing=True)
    bandwidth = _number(data["bandwidth"], positive=True) if "bandwidth" in data else None
    rows = []
    by_sample = {}
    for si, sample in enumerate(samples):
        sample_rows = []
        for gi, gene in enumerate(genes):
            value = values[gi][si]
            r = _row(gene, float(si), _log10(value), group=sample["sample_id"], key=f"{sample['sample_id']}:{gene}",
                     details={**sample, "gene_id": gene, "cooks_distance": value, "model_id": data["model_id"]},
                     reason="zero_cooks_distance_has_no_finite_log10_coordinate" if value == 0 else None)
            sample_rows.append(r)
            rows.append(r)
        by_sample[sample["sample_id"]] = sample_rows
    box_marks = point_marks(rows, radius=2)
    violin_marks = []
    annotations = [f"Saved model: {data['model_id']}; zeros and missing values are retained without log floors."]
    for si, sample in enumerate(samples):
        selected = by_sample[sample["sample_id"]]
        valid = _finite_rows(selected)
        if not valid:
            annotations.append(f"{sample['sample_id']}: no positive finite saved distances.")
            continue
        summary = distribution_summary([r["y"] for r in valid], bandwidth=bandwidth)
        keys = [r["key"] for r in valid]
        group = sample["sample_id"]
        box_marks.extend([
            _mark("line", keys, group, x1=si, x2=si, y1=summary["whisker_low"], y2=summary["whisker_high"]),
            _mark("rect", keys, group, x=si-.25, y=summary["q1"], width=.5, height=summary["q3"]-summary["q1"], style={"fill": "#56b4e9", "opacity": .4}),
            _mark("line", keys, group, x1=si-.25, x2=si+.25, y1=summary["median"], y2=summary["median"]),
        ])
        for whisker in ["whisker_low", "whisker_high"]:
            box_marks.append(_mark("line", keys, group, x1=si-.15, x2=si+.15, y1=summary[whisker], y2=summary[whisker]))
        density = summary["density"]
        if density:
            maximum = max(d for _, d in density)
            points = [[si-.4*d/maximum, value] for value, d in density] + [[si+.4*d/maximum, value] for value, d in reversed(density)]
            violin_marks.append(_mark("polygon", keys, group, points=points, style={"fill": "#56b4e9", "opacity": .5}))
        else:
            violin_marks.extend(point_marks(valid))
        annotations.append(f"{group}: positive n={len(valid)}, zero={sum(r['details']['cooks_distance'] == 0 for r in selected)}, missing={sum(r['details']['cooks_distance'] is None for r in selected)}; KDE bandwidth={summary['bandwidth']}; method={summary['bandwidth_method']}.")
        for r in selected:
            r["details"]["sample_distribution_summary"] = summary
    axes = _axes("Sample", "sample", "log10(saved Cook's distance)", "log10_dimensionless", yt="log10")
    return [_panel(cap, "Cook's-distance Tukey boxes", rows, suffix="boxes", marks=box_marks, axes=axes,
                   x_categories=[s["sample_id"] for s in samples], annotations=annotations,
                   limitations=["Whiskers use observed values inside 1.5 IQR fences; this is a descriptive distribution of saved distances, not a new influence fit."]),
            _panel(cap, "Cook's-distance distributions", deepcopy(rows), suffix="violins", marks=violin_marks, axes=axes,
                   x_categories=[s["sample_id"] for s in samples], annotations=annotations,
                   limitations=["Density smoothing is descriptive on log10 positive saved distances; zero/missing values do not acquire artificial density."])]


def _influence(cap, data):
    """For every gene and declared pair, supply the saved full effect and actual omitted-pair fit ID/effect (including failures)."""
    _population(data, "pair")
    _text(data["biological_unit"], "biological unit")
    if data["biological_unit"].lower() in {"sample", "library", "cell", "unknown"}:
        raise ValueError("leave-one-pair-out evidence requires explicit biological-unit identity")
    binding = _binding(data["binding"])
    _text(data["full_fit_id"], "full fit ID")
    if data["definition"] != "leave_one_pair_out_minus_full_log2_effect":
        raise ValueError("saved influence definition must specify omitted-pair minus full log2 effect")
    pairs = _records(data["pairs"], "omitted pairs", 1000)
    for pair in pairs:
        _fields(pair, ["pair_id", "biological_unit_id"])
        _text(pair["pair_id"], "pair ID")
        _biological_identity(pair["biological_unit_id"])
    require_unique([p["pair_id"] for p in pairs], "pair IDs")
    require_unique([p["biological_unit_id"] for p in pairs], "pair biological-unit IDs")
    pair_ids = [p["pair_id"] for p in pairs]
    genes = _records(data["genes"], "saved influence genes")
    require_unique([g.get("gene_id") for g in genes], "gene IDs")
    panels = []
    for gene in genes:
        _fields(gene, ["gene_id", "full_effect", "omissions"], ["reason"])
        _text(gene["gene_id"], "gene ID")
        full = _number(gene["full_effect"], missing=True)
        omissions = {}
        for omission in _records(gene["omissions"], "saved omission fits", 1000):
            _fields(omission, ["pair_id", "fit_id", "effect"], ["reason"])
            _text(omission["fit_id"], "omission fit ID")
            if omission["fit_id"] == data["full_fit_id"] or omission["pair_id"] in omissions:
                raise ValueError("omission fit must be distinct and each pair represented once")
            omissions[omission["pair_id"]] = {**omission, "effect": _number(omission["effect"], missing=True)}
        if set(omissions) != set(pair_ids):
            raise ValueError("omitted-pair fit IDs must exactly match declared biological pairs")
        require_unique([o["fit_id"] for o in omissions.values()], "omission fit IDs")
        rows = []
        for i, pair in enumerate(pairs):
            omission = omissions[pair["pair_id"]]
            difference = omission["effect"]-full if omission["effect"] is not None and full is not None else None
            rows.append(_row(pair["pair_id"], float(i), difference, kind="pair", group=pair["biological_unit_id"], reason=omission.get("reason", gene.get("reason")),
                             details={**pair, **omission, "gene_id": gene["gene_id"], "full_effect": full, "full_fit_id": data["full_fit_id"], "binding": binding}))
        marks = point_marks(rows)
        for r in _finite_rows(rows):
            marks.append(_mark("line", [r["key"]], r["group"], x1=r["x"], x2=r["x"], y1=0., y2=r["y"]))
        panels.append(_panel(cap, f"{gene['gene_id']}: saved leave-one-pair-out influence", rows, suffix=gene["gene_id"], marks=marks,
            axes=_axes("Omitted biological pair", "pair", "Omitted-pair effect minus full effect", "log2_fold_change"), x_categories=pair_ids,
            reference_lines=[{"axis": "y", "value": 0., "label": "full-fit effect reference"}],
            annotations=[f"Full fit: {data['full_fit_id']}; exact definition: {data['definition']}.", "Every omission refers to an actual saved fit; failed refits remain missing."] ))
    return panels


def _comparison_method(method):
    _fields(method, ["method_id", "test_id", "binding", "genes"])
    _text(method["method_id"], "method ID")
    _text(method["test_id"], "test ID")
    binding = _binding(method["binding"])
    genes = []
    for g in _records(method["genes"], "comparison genes"):
        _fields(g, ["gene_id", "effect", "p_value"], ["reason"])
        _text(g["gene_id"], "gene ID")
        genes.append({**g, "effect": _number(g["effect"], missing=True), "p_value": _probability(g["p_value"])})
    require_unique([g["gene_id"] for g in genes], "comparison gene IDs")
    return {**method, "binding": binding, "genes": genes}


def _concordance(cap, data):
    """left/right each name method_id, test_id, binding and gene-keyed saved effect/p; coefficient comparability is explicit."""
    _population(data, "gene")
    left, right = _comparison_method(data["left"]), _comparison_method(data["right"])
    if left["binding"] != right["binding"]:
        raise ValueError("effect comparisons require the same cohort, contrast, gene namespace and scale")
    if {g["gene_id"] for g in left["genes"]} != {g["gene_id"] for g in right["genes"]}:
        raise ValueError("comparison gene IDs must match; unavailable fits must be explicit rows")
    kind = data["comparison_kind"]
    expected = (left["method_id"] != right["method_id"], left["test_id"] != right["test_id"])
    if {"cross_method": (True, False), "cross_test": (False, True), "cross_method_and_test": (True, True)}.get(kind) != expected:
        raise ValueError("comparison kind must retain the actual method/test identity differences")
    comparability = _text(data["effect_comparability"], "effect comparability declaration")
    other = {g["gene_id"]: g for g in right["genes"]}
    labels = [f"{m['method_id']} / {m['test_id']}" for m in [left, right]]
    panels = []
    for quantity in ["effect", "p_value"]:
        rows, ba_rows = [], []
        for g in left["genes"]:
            h = other[g["gene_id"]]
            x = g[quantity] if quantity == "effect" else _neglog(g[quantity])
            y = h[quantity] if quantity == "effect" else _neglog(h[quantity])
            details = {"left": g, "right": h, "binding": left["binding"], "left_method": labels[0], "right_method": labels[1],
                       "quantity": quantity, "effect_comparability": comparability}
            reason = "zero_probability_has_no_finite_negative_log10_coordinate" if quantity == "p_value" and (g[quantity] == 0 or h[quantity] == 0) else g.get("reason", h.get("reason"))
            rows.append(_row(g["gene_id"], x, y, details=details, reason=reason))
            complete = x is not None and y is not None
            ba_rows.append(_row(g["gene_id"], (x+y)/2. if complete else None, y-x if complete else None, details=details, reason=reason))
        metric = "log2 fold change" if quantity == "effect" else "-log10(raw p)"
        unit = "log2_fold_change" if quantity == "effect" else "negative_log10_probability"
        marks = point_marks(rows)
        finite = _finite_rows(rows)
        if finite:
            extent = [v for r in finite for v in [r["x"], r["y"]]]
            marks.append(_mark("line", [], "identity_reference", x1=min(extent), y1=min(extent), x2=max(extent), y2=max(extent),
                               style={"stroke": "#64748b", "dash": [4, 3]}))
        panels.append(_panel(cap, f"{metric}: concordance", rows, suffix=f"{quantity}:scatter", marks=marks,
            axes=_axes(f"{labels[0]}: {metric}", unit, f"{labels[1]}: {metric}", unit),
            annotations=[f"Declared effect comparability: {comparability}.", "Diagonal is the equality reference."],
            limitations=["Agreement does not identify the best method or validate either test's calibration; Wald and LRT are retained as distinct tests."]))
        differences = [r["y"] for r in _finite_rows(ba_rows)]
        refs = [{"axis": "y", "value": 0., "label": "equal estimates"}]
        annotations = [f"Difference = {labels[1]} minus {labels[0]}; only complete finite pairs contribute to descriptive summaries."]
        if differences:
            mean = sum(differences)/len(differences)
            refs.append({"axis": "y", "value": mean, "label": "mean observed difference"})
            if len(differences) > 1:
                from statistics import stdev
                spread = stdev(differences)
                refs.extend({"axis": "y", "value": mean+sign*1.96*spread, "label": "descriptive mean ±1.96 sample SD"} for sign in [-1, 1])
            annotations.append(f"Complete finite comparison genes: {len(differences)}; mean difference: {mean:g}.")
        panels.append(_panel(cap, f"{metric}: Bland–Altman", ba_rows, suffix=f"{quantity}:bland-altman",
            axes=_axes(f"Mean {metric}: {labels[0]} and {labels[1]}", unit, f"{labels[1]} minus {labels[0]}", unit), reference_lines=refs,
            annotations=annotations, limitations=["Descriptive mean/SD reference lines are not inferential agreement limits and do not establish method interchangeability."]))
    return panels


def _upset(cap, data):
    """Each method supplies exact gene membership, saved selected booleans, disposition, binding and selection definition."""
    _population(data, "gene")
    binding = _binding(data["binding"])
    genes = _ids(data["gene_ids"], "decision universe gene IDs")
    methods = _records(data["methods"], "saved decision methods", 50)
    method_maps = {}
    for method in methods:
        _fields(method, ["method_id", "binding", "selection_definition", "genes"], ["calibration", "test_id"])
        method_id = _text(method["method_id"], "method ID")
        if method_id in method_maps or _binding(method["binding"]) != binding:
            raise ValueError("decision methods must be unique and bind the same cohort, contrast, scale and namespace")
        _text(method["selection_definition"], "saved selection definition")
        decisions = {}
        for g in _records(method["genes"], "saved gene decisions"):
            _fields(g, ["gene_id", "selected", "disposition"], ["reason"])
            _text(g["gene_id"], "gene ID")
            _text(g["disposition"], "saved disposition")
            if type(g["selected"]) is not bool or g["gene_id"] in decisions:
                raise ValueError("saved selection must be boolean with unique gene IDs")
            _calibrated(g["disposition"], method.get("calibration"), binding, g["gene_id"], method_id)
            decisions[g["gene_id"]] = {**g, "calibration_evidence": _calibration_detail(g["disposition"], method.get("calibration"), g["gene_id"])}
        if set(decisions) != set(genes):
            raise ValueError("all methods must retain exact decision-universe membership")
        method_maps[method_id] = decisions
    method_ids = list(method_maps)
    memberships = {gene: tuple(m for m in method_ids if method_maps[m][gene]["selected"]) for gene in genes}
    counts = Counter(memberships.values())
    intersections = sorted(counts, key=lambda membership: (-counts[membership], membership))
    categories = [" & ".join(membership) if membership else "none selected" for membership in intersections]
    rows = [_row(g, float(intersections.index(memberships[g])), float(counts[memberships[g]]), group=" & ".join(memberships[g]) or "none selected",
                 details={"selected_methods": list(memberships[g]), "binding": binding,
                          "saved_decisions": {m: method_maps[m][g] for m in method_ids}}) for g in genes]
    marks = [_mark("rect", [r["key"] for r in rows if r["x"] == i], categories[i], x=i-.35, y=0., width=.7, height=float(counts[membership]))
             for i, membership in enumerate(intersections)]
    panels = [_panel(cap, "Exact saved selection intersections", rows, suffix="intersection-counts", marks=marks,
                     axes=_axes("Exact method intersection", "method_intersection", "Genes", "gene_count"), x_categories=categories,
                     annotations=[f"Declared decision universe: {len(genes)} genes; the none-selected intersection is retained."])]
    membership_rows = []
    membership_marks = []
    for gene in genes:
        for mi, method in enumerate(method_ids):
            decision = method_maps[method][gene]
            membership_rows.append(_row(gene, float(intersections.index(memberships[gene])), float(mi), group="selected" if decision["selected"] else "not_selected",
                                       key=f"membership:{gene}:{method}", details={"method_id": method, **decision, "exact_intersection": list(memberships[gene])}))
    for i, membership in enumerate(intersections):
        active = []
        all_active_keys = []
        for mi, method in enumerate(method_ids):
            keys = [r["key"] for r in membership_rows if r["x"] == i and r["y"] == mi]
            selected = method in membership
            membership_marks.append(_mark("point", keys, "selected" if selected else "not_selected", x=float(i), y=float(mi), radius=5. if selected else 2.5,
                                          style={"fill": "#0072b2" if selected else "#d1d5db"}))
            if selected:
                active.append(mi)
                all_active_keys.extend(keys)
        if len(active) > 1:
            membership_marks.append(_mark("line", all_active_keys, "selected", x1=float(i), x2=float(i), y1=float(min(active)), y2=float(max(active)),
                                          style={"stroke": "#0072b2"}))
    panels.append(_panel(cap, "UpSet exact membership", membership_rows, suffix="membership", marks=membership_marks,
                         axes=_axes("Exact method intersection", "method_intersection", "Saved decision method", "method"), x_categories=categories, y_categories=method_ids,
                         annotations=["Filled linked dots encode saved selection in each exact intersection; no pairwise-overlap approximation."]))
    disposition_rows, disposition_marks = [], []
    dispositions = sorted({d["disposition"] for decisions in method_maps.values() for d in decisions.values()})
    for mi, method in enumerate(method_ids):
        base = 0.
        for disposition in dispositions:
            selected = [g for g in genes if method_maps[method][g]["disposition"] == disposition]
            keys = []
            for gene in selected:
                r = _row(gene, float(mi), base+len(selected), group=disposition, key=f"disposition:{method}:{gene}",
                         details={"method_id": method, **method_maps[method][gene], "count_for_disposition": len(selected), "binding": binding})
                disposition_rows.append(r)
                keys.append(r["key"])
            if selected:
                disposition_marks.append(_mark("rect", keys, disposition, x=mi-.35, y=base, width=.7, height=float(len(selected))))
            base += len(selected)
    panels.append(_panel(cap, "Saved decision dispositions", disposition_rows, suffix="dispositions", marks=disposition_marks,
                         axes=_axes("Saved method", "method", "Genes in saved disposition", "gene_count"), x_categories=method_ids,
                         annotations=[f"{m['method_id']}: {m['selection_definition']}" for m in methods],
                         limitations=["No selection or calibration was inferred from q values. A calibrated label is accepted only with supplied saved evidence bound to its method, cohort, contrast and gene."]))
    return panels


def _residuals(cap, data):
    """Saved records have gene_id/sample_id/fitted/residual; group_by names an explicit sample metadata field.

    The row population is gene_sample, distinct from the declared biological
    unit. Pearson/deviance/studentized/quantile residuals require dimensionless
    units. Response residuals use the fitted-response unit. All residual values
    are supplied saved values, including failures; they are never reconstructed.
    """
    _population(data, "gene_sample")
    samples = _samples({**data, "population_unit": "sample"})
    sample_map = {s["sample_id"]: s for s in samples}
    for key in ["model_id", "residual_definition", "residual_unit", "fitted_unit"]:
        _text(data[key], key)
    residual_type = data["residual_type"]
    if residual_type not in {"pearson", "deviance", "studentized", "randomized_quantile", "response", "working"}:
        raise ValueError("residual_type must explicitly name the saved model residual definition")
    if residual_type in {"pearson", "deviance", "studentized", "randomized_quantile"} and data["residual_unit"] != "dimensionless":
        raise ValueError("standardized/quantile residuals require dimensionless units")
    if residual_type == "response" and data["residual_unit"] != data["fitted_unit"]:
        raise ValueError("response residual and fitted-response units must agree")
    group_by = data["group_by"]
    if group_by not in {"condition", "biological_unit_id", "sample_id"}:
        raise ValueError("residual grouping must name an explicit sample metadata field")
    bandwidth = _number(data["bandwidth"], positive=True) if "bandwidth" in data else None
    records = _records(data["records"], "saved gene/sample residual records", 200_000)
    rows, seen = [], set()
    for record in records:
        _fields(record, ["gene_id", "sample_id", "fitted", "residual"], ["reason"])
        gene = _text(record["gene_id"], "residual gene ID")
        sample_id = _text(record["sample_id"], "residual sample ID")
        if sample_id not in sample_map or (gene, sample_id) in seen:
            raise ValueError("residual records require unique gene/sample identities from the declared samples")
        seen.add((gene, sample_id))
        fitted = _number(record["fitted"], missing=True)
        residual = _number(record["residual"], missing=True)
        identity = f"{gene} / {sample_id}"
        key = "residual:" + hashlib.sha256(repr((gene, sample_id)).encode()).hexdigest()[:24]
        details = {**record, **sample_map[sample_id], "model_id": data["model_id"],
                   "residual_type": residual_type, "residual_definition": data["residual_definition"],
                   "residual_unit": data["residual_unit"], "fitted_unit": data["fitted_unit"], "group_by": group_by}
        r = _row(identity, fitted, residual, kind="gene_sample", group=sample_map[sample_id][group_by], key=key,
                 details=details, reason=record.get("reason"), warnings=["saved_residual_unavailable"] if residual is None else [])
        r["link"] = {"kind": "sample", "id": sample_id}
        rows.append(r)
    if {record["sample_id"] for record in records} != set(sample_map):
        raise ValueError("saved residual records must retain every declared sample, including failed observations")
    refs = [{"axis": "y", "value": 0., "label": "zero residual"}]
    annotations = [f"Saved model: {data['model_id']}; residual type: {residual_type}.", f"Residual definition: {data['residual_definition']}.",
                   f"Grouping field: {group_by}; biological unit: {data['biological_unit']}."]
    limitations = ["Gene/sample residual records within a sample or biological unit are not independent biological replicates.",
                   "Residual geometry is descriptive; it neither establishes model adequacy nor replaces independent scientific review."]
    panels = [_panel(cap, "Saved residual versus fitted response", rows, suffix="residual-fitted",
                     axes=_axes("Saved fitted response", data["fitted_unit"], f"Saved {residual_type} residual", data["residual_unit"]),
                     reference_lines=refs, annotations=annotations, limitations=limitations)]
    categories = list(dict.fromkeys(sample[group_by] for sample in samples))
    grouped = []
    for r in rows:
        item = _row(r["id"], float(categories.index(r["group"])), r["y"], kind="gene_sample", group=r["group"], key=r["key"],
                    details=r["details"], reason=r["reason"], warnings=r["warnings"])
        item["link"] = deepcopy(r["link"])
        grouped.append(item)
    boxes = point_marks(grouped, radius=2)
    violins = []
    group_notes = []
    for index, category in enumerate(categories):
        group_rows = [r for r in grouped if r["group"] == category]
        valid = _finite_rows(group_rows)
        group_notes.append(f"{category}: saved residual n={len(valid)}; missing residuals={len(group_rows)-len(valid)}.")
        if not valid:
            continue
        summary = distribution_summary([r["y"] for r in valid], bandwidth=bandwidth)
        keys = [r["key"] for r in valid]
        boxes.extend([
            _mark("line", keys, category, x1=index, x2=index, y1=summary["whisker_low"], y2=summary["whisker_high"]),
            _mark("rect", keys, category, x=index-.3, y=summary["q1"], width=.6, height=summary["q3"]-summary["q1"], style={"fill": "#56b4e9", "opacity": .4}),
            _mark("line", keys, category, x1=index-.3, x2=index+.3, y1=summary["median"], y2=summary["median"]),
        ])
        for whisker in ["whisker_low", "whisker_high"]:
            boxes.append(_mark("line", keys, category, x1=index-.15, x2=index+.15, y1=summary[whisker], y2=summary[whisker]))
        density = summary["density"]
        if density:
            height = max(d for _, d in density)
            points = [[index-.4*d/height, value] for value, d in density] + [[index+.4*d/height, value] for value, d in reversed(density)]
            violins.append(_mark("polygon", keys, category, points=points, style={"fill": "#56b4e9", "opacity": .5}))
        else:
            violins.extend(point_marks(valid))
        group_notes.append(f"{category}: Tukey 1.5 IQR observed whiskers; descriptive KDE bandwidth={summary['bandwidth']}; {summary['bandwidth_method']}.")
    for name, marks in [("boxes", boxes), ("violins", violins)]:
        panels.append(_panel(cap, f"Saved residuals by {group_by}: {name}", deepcopy(grouped), suffix=f"group-{name}", marks=marks,
                             axes=_axes(f"Declared {group_by}", group_by, f"Saved {residual_type} residual", data["residual_unit"]),
                             x_categories=categories, reference_lines=refs, annotations=annotations+group_notes, limitations=limitations))
    return panels


def _design_diagnostic_values(source, values, n_columns):
    """Saved diagnostics are copied and internally checked, or an explicitly requested descriptive SVD is evaluated."""
    if not isinstance(source, dict):
        raise ValueError("design diagnostics require an explicit saved or descriptive_svd mode")
    n_components = min(len(values), n_columns)
    mode = source.get("mode")
    if mode == "saved":
        _fields(source, ["mode", "evidence_id", "method", "rank", "condition_number", "condition_number_definition",
                         "singular_values", "rank_tolerance", "status"], ["reason"])
        _text(source["evidence_id"], "saved design diagnostic evidence ID")
        _text(source["method"], "saved design diagnostic method")
        if source["condition_number_definition"] != "full_column_rank_2norm":
            raise ValueError("design condition number must declare the full-column-rank 2-norm definition")
        tolerance = _number(source["rank_tolerance"], positive=True)
        if not isinstance(source["singular_values"], list) or len(source["singular_values"]) != n_components:
            raise ValueError("saved singular-value membership must match min(sample rows, design columns)")
        singular = [_number(v, nonnegative=True, missing=True) for v in source["singular_values"]]
        rank = source["rank"]
        condition = _number(source["condition_number"], positive=True, missing=True)
        if source["status"] == "failed":
            _text(source.get("reason"), "saved diagnostic failure reason")
            if rank is not None or condition is not None:
                raise ValueError("failed design diagnostics cannot supply successful rank/condition estimates")
        else:
            if type(rank) is not int or not 0 <= rank <= n_components or any(v is None for v in singular):
                raise ValueError("successful saved rank requires finite singular values and valid dimensions")
            if any(a < b for a, b in zip(singular, singular[1:])) or sum(v > tolerance for v in singular) != rank:
                raise ValueError("saved rank/singular values must agree with the saved absolute rank tolerance")
            expected_status = "full_rank" if rank == n_columns else "rank_deficient"
            if source["status"] != expected_status:
                raise ValueError("saved diagnostic status must retain rank deficiency")
            if rank < n_columns and condition is not None:
                raise ValueError("a rank-deficient design has no finite full-column-rank condition number")
            if rank == n_columns:
                expected = singular[0] / singular[-1]
                if condition is None or condition < 1 or not math.isclose(condition, expected, rel_tol=1e-6, abs_tol=1e-10):
                    raise ValueError("saved full-rank condition number must agree with the saved singular spectrum")
        return {**deepcopy(source), "rank": rank, "condition_number": condition, "singular_values": singular, "rank_tolerance": tolerance}
    if mode != "descriptive_svd":
        raise ValueError("design diagnostic mode must be saved or explicitly requested descriptive_svd")
    _fields(source, ["mode", "rank_tolerance", "scaling"])
    tolerance = _number(source["rank_tolerance"], positive=True)
    if source["scaling"] != "as_supplied_no_rescaling":
        raise ValueError("descriptive SVD must explicitly retain the supplied design scaling")
    result = {**source, "rank_tolerance": tolerance, "rank": None, "condition_number": None,
              "condition_number_definition": "full_column_rank_2norm", "singular_values": [None]*n_components,
              "status": "failed", "method": "explicit descriptive NumPy SVD on the supplied matrix; no column rescaling and no statistical model fit"}
    if any(v is None for values_row in values for v in values_row):
        return {**result, "reason": "missing_design_values_preclude_descriptive_svd"}
    import numpy as np
    try:
        singular = np.linalg.svd(np.asarray(values, dtype=float), compute_uv=False).tolist()
    except np.linalg.LinAlgError:
        return {**result, "reason": "descriptive_design_svd_failed"}
    if not all(math.isfinite(v) for v in singular):
        return {**result, "reason": "nonfinite_descriptive_design_singular_values"}
    rank = sum(v > tolerance for v in singular)
    condition = singular[0]/singular[-1] if rank == n_columns else None
    if condition is not None and not math.isfinite(condition):
        condition = None
    return {**result, "rank": rank, "condition_number": condition, "singular_values": singular,
            "status": "full_rank" if rank == n_columns else "rank_deficient",
            "reason": "rank_deficient_design" if rank < n_columns else "nonfinite_design_condition_number" if condition is None else None}


def _design(cap, data):
    """Actual matrix[sample][column] with column_id/unit/role and saved or explicit descriptive-SVD diagnostics.

    Optional contrasts supply full column-aligned weights and saved estimable,
    unestimable or not_assessed status/evidence; no contrast status is fabricated.
    """
    samples = _samples(data)
    sample_ids = _sample_order(data["sample_ids"], samples)
    sample_map = {s["sample_id"]: s for s in samples}
    _text(data["model_id"], "design model ID")
    columns = _records(data["columns"], "design columns", 1000)
    for column in columns:
        _fields(column, ["column_id", "unit", "role"])
        for value in column.values():
            _text(value, "design column metadata")
    column_ids = _ids([c["column_id"] for c in columns], "design column IDs", 1000)
    values = _matrix(data["matrix"], len(sample_ids), len(columns), missing=True)
    diagnostics = _design_diagnostic_values(data["diagnostics"], values, len(columns))
    rank = diagnostics["rank"]
    warnings = ["rank_deficient_design"] if rank is not None and rank < len(columns) else ["design_diagnostics_unavailable"] if rank is None else []
    limitations = ["Full design rank does not establish causal identifiability, unconfounded treatment effects, correct model specification or independent biological replication.",
                   "Condition numbers and singular values depend on the supplied column units and scaling; no hidden standardization is performed."]
    note = [f"Model: {data['model_id']}; diagnostic mode: {diagnostics['mode']}; {diagnostics['method']}.",
            f"Absolute singular-value rank tolerance: {diagnostics['rank_tolerance']:g}; status: {diagnostics['status']}.",
            "Column units: " + "; ".join(f"{c['column_id']}={c['unit']} ({c['role']})" for c in columns) + "."]
    if diagnostics.get("reason"):
        note.append(f"Diagnostic reason: {diagnostics['reason']}.")
    heat = matrix_panel(cap, "Actual design matrix", values, sample_ids, column_ids, unit="declared_column_units", cluster=False, diverging=any(v is not None and v < 0 for r in values for v in r),
                        details={"row_entity_kind": "sample", "column_entity_kind": "design_column", "model_id": data["model_id"], "diagnostics": diagnostics})
    heat["id"] = f"{cap}:matrix"
    for i, r in enumerate(heat["rows"]):
        si, ci = divmod(i, len(columns))
        r["details"].update(sample=sample_map[sample_ids[si]], column=columns[ci])
        r["warnings"].extend(warnings)
        r["link"] = {"kind": "sample", "id": sample_ids[si]}
    heat["annotations"].extend(note)
    heat["limitations"].extend(limitations+_LIMITS)
    panels = [heat]
    rank_rows = [_row("rank", 0., float(rank) if rank is not None else None, kind="diagnostic", details=diagnostics,
                      reason=diagnostics.get("reason"), warnings=warnings),
                 _row("design_columns", 1., float(len(columns)), kind="diagnostic", details={"column_ids": column_ids}),
                 _row("sample_rows", 2., float(len(samples)), kind="diagnostic", details={"sample_ids": sample_ids})]
    rank_marks = [_mark("rect", [r["key"]], r["id"], x=r["x"]-.3, y=0., width=.6, height=r["y"]) for r in _finite_rows(rank_rows)]
    panels.append(_panel(cap, "Design rank and dimensions", rank_rows, suffix="rank", marks=rank_marks,
                         axes=_axes("Diagnostic quantity", "diagnostic", "Matrix dimension / rank", "dimension"),
                         x_categories=["rank", "design columns", "sample rows"], annotations=note, limitations=limitations,
                         reference_lines=[{"axis": "y", "value": float(len(columns)), "label": "full column rank"}]))
    singular_rows = [_row(f"singular-{i+1}", float(i+1), v, kind="component", details={"component": i+1, "diagnostics": diagnostics},
                          reason=diagnostics.get("reason"), warnings=warnings) for i, v in enumerate(diagnostics["singular_values"])]
    singular_marks = [_mark("rect", [r["key"]], "singular_value", x=r["x"]-.3, y=0., width=.6, height=r["y"]) for r in _finite_rows(singular_rows)]
    panels.append(_panel(cap, "Design singular values", singular_rows, suffix="singular-values", marks=singular_marks,
                         axes=_axes("Singular-value index", "component_index", "Singular value (supplied design scaling)", "design_coordinate"),
                         annotations=note, limitations=limitations,
                         reference_lines=[{"axis": "y", "value": diagnostics["rank_tolerance"], "label": "declared absolute rank tolerance"}]))
    condition = diagnostics["condition_number"]
    reason = diagnostics.get("reason") or "full_column_rank_condition_number_not_finite" if condition is None else None
    condition_rows = [_row("condition_number", 0., condition, kind="diagnostic", details=diagnostics, reason=reason, warnings=warnings)]
    panels.append(_panel(cap, "Design condition number", condition_rows, suffix="condition", axes=_axes("Declared diagnostic", "diagnostic", "Full-column-rank 2-norm condition number", "dimensionless"),
                         x_categories=["condition number"], annotations=note, limitations=limitations))
    if "contrasts" in data:
        contrasts = _records(data["contrasts"], "saved contrast diagnostics", 1000)
        require_unique([c.get("contrast_id") for c in contrasts], "contrast IDs")
        contrast_values = []
        for contrast in contrasts:
            _fields(contrast, ["contrast_id", "weights", "status", "evidence_id"], ["reason"])
            _text(contrast["contrast_id"], "contrast ID")
            _text(contrast["evidence_id"], "saved contrast evidence ID")
            if contrast["status"] not in {"estimable", "unestimable", "not_assessed"}:
                raise ValueError("contrast estimability must be an explicit saved status")
            if contrast["status"] != "estimable":
                _text(contrast.get("reason"), "unestimable/unassessed contrast reason")
            if not isinstance(contrast["weights"], list) or len(contrast["weights"]) != len(columns):
                raise ValueError("contrast weights must exactly match actual design column order")
            contrast_values.append([_number(v) for v in contrast["weights"]])
        contrast_heat = matrix_panel(cap, "Saved design contrasts and estimability", contrast_values, [c["contrast_id"] for c in contrasts], column_ids,
                                     unit="contrast_weight_in_declared_column_basis", cluster=False, diverging=True,
                                     details={"row_entity_kind": "contrast", "column_entity_kind": "design_column"})
        contrast_heat["id"] = f"{cap}:contrasts"
        for i, r in enumerate(contrast_heat["rows"]):
            ci, column = divmod(i, len(columns))
            contrast = contrasts[ci]
            r["details"].update(contrast=contrast, column=columns[column], model_id=data["model_id"])
            r["group"] = contrast["status"]
            r["warnings"].extend(warnings + ([f"contrast_{contrast['status']}"] if contrast["status"] != "estimable" else []))
            r["link"] = {"kind": "contrast", "id": contrast["contrast_id"]}
        contrast_heat["annotations"].extend(note+["Contrast statuses are copied from the supplied saved evidence, not inferred from full design rank."])
        contrast_heat["limitations"].extend(limitations+_LIMITS)
        panels.append(contrast_heat)
    return panels


_BUILDERS = {
    "bulk_ma_comparison": _effects,
    "bulk_volcano": _effects,
    "bulk_dispersion": _dispersion,
    "bulk_pca": _pca,
    "bulk_mds": _mds,
    "bulk_sample_distance_heatmap": _heatmap,
    "bulk_sample_correlation_heatmap": _heatmap,
    "bulk_pvalue_histogram": _histogram,
    "bulk_independent_filtering": _filter_curve,
    "bulk_paired_expression": _paired,
    "bulk_library_qc": _library,
    "bulk_cooks_distance": _cooks,
    "bulk_leave_one_pair_out": _influence,
    "bulk_concordance": _concordance,
    "bulk_upset_dispositions": _upset,
    "bulk_residual_diagnostics": _residuals,
    "bulk_design_diagnostics": _design,
    "bulk_mean_variance": _empirical_mean_variance,
}


def build_panels(capability, data):
    """Validate a capability-specific saved evidence object and return real linked geometry.

    ``CAPABILITIES`` lists the outer contract. Builder docstrings identify the
    saved row quantities; ``tests.test_plot_adapters_bulk.fixture_packets`` is a
    public, labeled synthetic example of every complete contract.
    """
    if capability not in CAPABILITIES:
        raise ValueError("unknown bulk plotting capability")
    specification = CAPABILITIES[capability]
    _fields(data, specification["required"], specification["optional"])
    result = _BUILDERS[capability](capability, deepcopy(data))
    require_unique([p["id"] for p in result], "panel IDs")
    for p in result:
        validate_panel(p)
    return result
