"""Bounded saved-data adapters for expression and single-cell plot primitives.

These adapters do not fit biological models or choose scientific thresholds.
``CAPABILITIES`` describes the outer envelope; each builder also validates the
record fields, units and memberships it consumes. Null measurements remain rows
with an explicit reason. Cells are descriptive observations, never independent
biological replicates. All transformations here are declared display operations.
"""

from __future__ import annotations

from bisect import bisect_right
from collections import defaultdict
import hashlib
import json
import math
from statistics import median

from .scientific_plot_contract import (
    distribution_summary,
    line_mark,
    matrix_panel,
    panel,
    require_fields,
    require_unique,
    row,
)


MAX_ROWS = 100_000
MAX_MATRIX_CELLS = 100_000
CELL_LIMITATION = "Cells are descriptive observations; cell counts do not establish independent biological replication."
PLOTTING_LIMITATION = "Rendering fidelity and source membership do not establish biological validity."
_SCALES = {
    "counts": "count", "normalized_expression": "normalized_expression",
    "log1p_expression": "log1p_expression", "scaled_expression": "z_score",
    "module_score": "score",
}


def _spec(required, optional, description):
    return {"required": list(required), "optional": list(optional), "description": description}


_QC_FIELDS = ("cells", "group_by", "units", "thresholds", "threshold_source")
_OBS_FIELDS = ("observations", "unit", "group_by")
_EXPR_FIELDS = ("observations", "group_by", "source_scale", "unit", "layer")
_MATRIX_FIELDS = ("values", "row_ids", "column_ids", "unit", "source_scale", "layer")
_EMBEDDING_FIELDS = ("cells", "method", "representation", "configuration")
CAPABILITIES = {
    "qc_violins": _spec(_QC_FIELDS, ("bandwidth", "grid_size"), "Pre/post-filter Gaussian KDE violins of counts, detected genes and mitochondrial percent, including excluded source cells and saved cutoffs."),
    "qc_ridges": _spec(_QC_FIELDS, ("bandwidth", "grid_size"), "Pre/post-filter KDE ridges of all three QC measurements by sample/batch; excluded source membership remains explicit."),
    "qc_joint_scatter": _spec(_QC_FIELDS, (), "Three pairwise QC scatters, retained/excluded cell glyphs and saved filtering thresholds."),
    "depth_histogram": _spec((*_OBS_FIELDS, "bins", "cutoffs"), ("entity_kind", "transform"), "Explicit source-bin library-depth histograms, displayed medians and saved cutoffs; optional log display."),
    "depth_ecdf": _spec((*_OBS_FIELDS, "cutoffs"), ("entity_kind", "transform"), "Right-continuous empirical depth CDF with tied observations and complete membership."),
    "batch_boxplot": _spec(_OBS_FIELDS, ("entity_kind", "transform"), "Observed library-depth Tukey 1.5 IQR boxes, deterministic display jitter and n by saved group."),
    "expression_violins": _spec(_EXPR_FIELDS, ("display_transform", "bandwidth", "grid_size"), "Per-gene cell expression violins from a named saved layer with explicit KDE bandwidth."),
    "expression_boxplots": _spec(_EXPR_FIELDS, ("display_transform", "bandwidth", "grid_size"), "Per-gene Tukey boxes and every saved cell observation, with display-only jitter."),
    "expression_ridges": _spec(_EXPR_FIELDS, ("display_transform", "bandwidth", "grid_size"), "Per-gene Gaussian KDE ridges from saved cell values, preserving source scale and missing values."),
    "doublet_diagnostics": _spec(("cells", "bins", "threshold", "threshold_source", "method", "embedding_name", "score_unit"), ("bandwidth", "grid_size"), "Saved doublet score histogram/KDE with called threshold and exact-coordinate called-doublet embedding."),
    "hvg_scatter": _spec(("genes", "mean_unit", "statistic", "statistic_unit", "source_scale", "layer", "method"), ("x_transform", "y_transform"), "Saved mean-versus-variance/dispersion, highlighting the saved highly_variable boolean without recomputation."),
    "scrna_pca_scree": _spec(("components", "variance_unit", "layer", "source_scale"), (), "Scree plot of saved PCA explained variance; fraction-to-percent display conversion is explicit."),
    "integration_diagnostics": _spec(("cells", "embedding_name", "before_method", "after_method", "metrics", "metric_definitions"), (), "Matched before/after embeddings by batch and cell type plus actual saved raw ASW and LISI tradeoff scatters."),
    "feature_expression": _spec(("cells", "feature_id", "layer", "source_scale", "unit", "embedding_name"), ("display_transform",), "Exact embedding coordinates colored by a bounded, explicitly named expression layer."),
    "marker_dotplot": _spec(("group_gene_aggregates", "unit", "source_scale", "layer", "expression_threshold", "denominator_kind"), ("display_transform", "max_radius"), "Aggregate mean expression color and dot area equal to fraction strictly above the explicit source threshold; exact denominators and membership retained."),
    "expression_heatmap": _spec(_MATRIX_FIELDS, ("transform", "cluster", "row_metadata", "column_metadata"), "Saved expression matrix with explicit log1p/row-zscore displays and actual average-linkage Euclidean dendrograms."),
    "module_heatmap": _spec((*_MATRIX_FIELDS, "score_definition"), ("transform", "cluster", "row_metadata", "column_metadata"), "Saved module-score matrix, distinct from expression, with explicit scale and actual clustering dendrograms."),
    "donor_composition": _spec(("compositions", "denominator_kind", "biological_unit", "unit"), ("summaries", "pairs"), "Per-donor/sample composition points and saved supported summaries; interval type/level/scope/method/denominator are explicit, while legacy bare bounds remain unassessed."),
    "batch_composition": _spec(("compositions", "denominator_kind", "unit"), (), "Within-batch descriptive stacked cell-type composition with explicit fraction/percent/count units and exact source denominators."),
    "classifier_confidence_histogram": _spec(("observations", "bins", "classifier", "score_definition", "score_unit"), (), "Saved bounded classifier-confidence histogram; score distribution is not a calibration assessment."),
    "embedding_density_centroids": _spec(("cells", "embedding_name", "bins"), (), "All-point rectangular density bins and actual descriptive group centroids, retaining every exact source selection ID without sampling."),
    "saved_embedding": _spec(_EMBEDDING_FIELDS, (), "Exact saved UMAP or t-SNE coordinates with explicitly declared source representation and configuration; no embedding fit or alignment."),
    "embedding_hexbin": _spec((*_EMBEDDING_FIELDS, "hex_radius", "origin"), (), "Actual pointy-top axial hexagons, with explicit radius/origin, exact all-point bin membership and counts; no sampling."),
    "embedding_density_contours": _spec((*_EMBEDDING_FIELDS, "bandwidth", "grid_size", "levels"), ("bounds",), "Actual 2D product-Gaussian KDE contour paths at explicit density levels/grid/bandwidth, with complete source membership and explicit degenerate-input fallback."),
    "ambient_rna_diagnostics": _spec(("observations", "entity_kind", "group_by", "method", "measurement", "unit", "measurement_definition", "bins"),
                                    ("bandwidth", "grid_size", "cutoffs", "embedding", "ambient_gene_profile", "correction"),
                                    "Saved ambient-RNA estimator score/fraction distributions and sample/batch summaries; optional actual saved depth, embedding, ambient-gene profile and pre/post-correction values. No estimation or decontamination."),
}


def _text(value, name):
    if not isinstance(value, str) or not value.strip() or len(value) > 1000:
        raise ValueError(f"{name} must be a nonempty bounded string")
    return value


def _number(value, name, *, nullable=False, low=None, high=None):
    if value is None and nullable:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{name} must be finite numeric data" + (" or null" if nullable else ""))
    result = float(value)
    if (low is not None and result < low) or (high is not None and result > high):
        raise ValueError(f"{name} is outside its declared scientific domain")
    return result


def _integer(value, name, *, low=0):
    result = _number(value, name, low=low)
    if not result.is_integer():
        raise ValueError(f"{name} must be an integer count")
    return int(result)


def _boolean(value, name):
    if not isinstance(value, bool):
        raise ValueError(f"{name} must be a saved boolean")
    return value


def _records(data, name, required, optional=(), *, empty=False):
    records = data[name]
    if not isinstance(records, list) or len(records) > MAX_ROWS or (not records and not empty):
        raise ValueError(f"{name} must be a bounded nonempty array of saved records")
    for record in records:
        if not isinstance(record, dict):
            raise ValueError(f"{name} records must be objects")
        require_fields(record, required, optional)
        if "id" in required:
            _text(record["id"], f"{name}.id")
    if "id" in required:
        require_unique([r["id"] for r in records], f"{name} IDs")
    return records


def _axis(label, unit, transform="identity"):
    return {"label": label, "unit": unit, "transform": transform}


def _axes(xlabel, xunit, ylabel, yunit, xtransform="identity", ytransform="identity"):
    return {"x": _axis(xlabel, xunit, xtransform), "y": _axis(ylabel, yunit, ytransform)}


def _jitter(identity):
    return (int.from_bytes(hashlib.sha256(identity.encode()).digest()[:8], "big") / (2**64 - 1) - .5) * .26


def _panel_id(capability, identity):
    return f"{capability}-{hashlib.sha256(identity.encode()).hexdigest()[:20]}"


def _unique_values(values, name):
    if len(values) != len(set(values)):
        raise ValueError(f"Duplicate {name} are incompatible with source identity")


def _display(value, transform):
    if transform not in ("identity", "log1p", "log10"):
        raise ValueError("display transform must be identity, log1p or log10")
    if value is None:
        return None, "Saved numeric measurement is missing."
    if transform == "log10":
        return (math.log10(value), None) if value > 0 else (None, "Nonpositive saved value is unavailable under log10 display.")
    if transform == "log1p":
        return (math.log1p(value), None) if value > -1 else (None, "Saved value at or below -1 is unavailable under log1p display.")
    return value, None


def _scale(data, *, module=False, unit_field="unit", transform_field="display_transform"):
    source_scale = _text(data["source_scale"], "source_scale")
    if source_scale not in _SCALES or (module != (source_scale == "module_score")):
        raise ValueError("Module scores and expression have distinct source scales")
    if data[unit_field] != _SCALES[source_scale]:
        raise ValueError("Declared unit is incompatible with the saved source scale")
    _text(data["layer"], "layer")
    transform = data.get(transform_field, "identity")
    allowed = ("identity", "log1p", "row_zscore", "log1p_row_zscore", "natural_log1p", "zscore_row") if transform_field == "transform" else ("identity", "log1p")
    if transform not in allowed:
        raise ValueError("Unsupported expression display transform")
    if "log1p" in transform and source_scale not in ("counts", "normalized_expression"):
        raise ValueError("Cannot log-transform an already logged/scaled layer or a module score")
    return transform


def _expression_value(value, source_scale):
    return _number(value, "saved expression/score", nullable=True,
                   low=0 if source_scale in ("counts", "normalized_expression", "log1p_expression") else None)


def _raw_expression_value(value, source_scale):
    result = _expression_value(value, source_scale)
    if result is not None and source_scale == "counts" and not result.is_integer():
        raise ValueError("Raw count observations must be integer counts; no count conversion is performed")
    return result


def _color(values, label, unit, *, diverging=False):
    finite = [v for v in values if v is not None]
    low, high = (min(finite), max(finite)) if finite else (0.0, 1.0)
    if diverging:
        extent = max(abs(low), abs(high)) or 1.0
        low, high = -extent, extent
    elif low == high:
        high = low + 1.0
    return {"label": label, "unit": unit, "palette": "RdBu_r" if diverging else "viridis",
            "domain": [low, high], "center": 0 if diverging else None}


def _point(r, *, radius=2.7, shape="circle", value=None):
    result = {"type": "point", "x": r["x"], "y": r["y"], "radius": radius,
              "keys": [r["key"]], "group": r["group"], "shape": shape}
    if value is not None:
        result["value"] = value
    return result


def _segment(x1, y1, x2, y2, keys, group="", **style):
    result = {"type": "line", "x1": x1, "y1": y1, "x2": x2, "y2": y2,
              "keys": list(keys), "group": group}
    if style:
        result["style"] = style
    return result


def _density_options(data):
    bandwidth = data.get("bandwidth")
    if bandwidth is not None:
        bandwidth = _number(bandwidth, "bandwidth", low=0)
        if bandwidth == 0:
            raise ValueError("KDE bandwidth must be strictly positive")
    grid_size = _integer(data.get("grid_size", 64), "grid_size", low=16)
    if grid_size > 512:
        raise ValueError("KDE grid_size exceeds the bounded display limit of 512")
    return bandwidth, grid_size


def _distribution(capability, title, observations, *, kind, unit, transform="identity", entity_kind="cell",
                  bandwidth=None, grid_size=64, references=(), annotations=(), details=None, panel_id=None):
    """observations contain validated id, group, value and optional source/link/reason."""
    categories = list(dict.fromkeys(o["group"] for o in observations))
    groups = {g: i for i, g in enumerate(categories)}
    rows = []
    for observation in observations:
        value, reason = _display(observation["value"], transform)
        reason = observation.get("reason") or reason
        position = groups[observation["group"]]
        if reason:
            x = y = None
        elif kind == "ridge":
            x, y = value, position + _jitter(observation["id"]) * .15
        else:
            x, y = position + _jitter(observation["id"]), value
        source_details = {**(details or {}), **observation.get("source", {}),
                          "source_value": observation["value"], "display_value": value,
                          "display_transform": transform, "jitter": "deterministic SHA-256; display only"}
        rows.append(row(observation["id"], x, y, entity_kind=entity_kind, group=observation["group"],
                        details=source_details, reason=reason,
                        link=observation.get("link", {"kind": entity_kind, "id": observation["id"]})))
    marks, notes = [], list(annotations)
    for group, index in groups.items():
        members = [r for r in rows if r["group"] == group and r["x"] is not None and r["y"] is not None]
        values = [r["details"]["display_value"] for r in members]
        if not values:
            notes.append(f"{group}: n=0 finite displayed observations; source rows remain available.")
            continue
        summary = distribution_summary(values, bandwidth=bandwidth, grid_size=grid_size)
        keys = [r["key"] for r in members]
        notes.append(f"{group}: n={len(values)}; Tukey whiskers are observed extrema within 1.5 IQR fences.")
        if kind == "box":
            marks.extend([
                {"type": "rect", "x": index - .28, "y": summary["q1"], "width": .56,
                 "height": summary["q3"] - summary["q1"], "keys": keys, "group": group, "style": {"opacity": .3}},
                _segment(index - .28, summary["median"], index + .28, summary["median"], keys, group),
                _segment(index, summary["whisker_low"], index, summary["q1"], keys, group),
                _segment(index, summary["q3"], index, summary["whisker_high"], keys, group),
                _segment(index - .15, summary["whisker_low"], index + .15, summary["whisker_low"], keys, group),
                _segment(index - .15, summary["whisker_high"], index + .15, summary["whisker_high"], keys, group),
            ])
        else:
            density = summary["density"]
            if density:
                maximum = max(point[1] for point in density)
                if kind == "violin":
                    points = [[index - .38 * d / maximum, v] for v, d in density]
                    points += [[index + .38 * d / maximum, v] for v, d in reversed(density)]
                else:
                    points = [[density[0][0], index]] + [[v, index + .8 * d / maximum] for v, d in density] + [[density[-1][0], index]]
                marks.append({"type": "polygon", "points": points, "keys": keys, "group": group, "style": {"opacity": .28}})
                notes.append(f"{group}: Gaussian KDE bandwidth={summary['bandwidth']:.8g} in displayed units ({summary['bandwidth_method']}); each density normalized to equal maximum width/height, not cell abundance.")
            else:
                notes.append(f"{group}: density unavailable: {summary['density_unavailable_reason']}; no spread invented.")
        for r in members:
            shape = "diamond" if r["details"]["display_value"] < summary["whisker_low"] or r["details"]["display_value"] > summary["whisker_high"] else "circle"
            if r["details"].get("qc_pass") is False:
                shape = "triangle"
            marks.append(_point(r, shape=shape))
    notes.append("All observation marks are retained; jitter changes only the categorical display position.")
    if kind in ("violin", "ridge"):
        notes.append("Gaussian KDE is descriptive smoothing on the displayed scale; unbounded kernels can extend beyond nonnegative observations.")
    axes = _axes("Saved group", "category", title.split(" — ")[0], unit, "identity", transform)
    if kind == "ridge":
        axes = _axes(title.split(" — ")[0], unit, "Saved group; relative density height", "category", transform)
    return panel(capability, title, rows, axes=axes, marks=marks,
                 x_categories=categories if kind != "ridge" else None,
                 y_categories=categories if kind == "ridge" else None,
                 reference_lines=list(references), annotations=notes,
                 limitations=[CELL_LIMITATION if entity_kind == "cell" else "Displayed sample labels do not establish biological independence.", PLOTTING_LIMITATION],
                 panel_id=panel_id)


_QC_METRICS = {"total_counts": ("Total counts", "count"), "n_genes_by_counts": ("Detected genes", "gene"),
               "pct_counts_mt": ("Mitochondrial percentage", "percent")}
_QC_THRESHOLDS = {"min_counts": "total_counts", "max_counts": "total_counts", "min_genes": "n_genes_by_counts",
                  "max_genes": "n_genes_by_counts", "max_pct_mt": "pct_counts_mt"}


def _qc_data(data):
    if data["group_by"] not in ("sample", "batch"):
        raise ValueError("QC group_by must name saved sample or batch membership")
    if data["units"] != {metric: unit for metric, (_, unit) in _QC_METRICS.items()}:
        raise ValueError("QC units must distinguish counts, detected genes and mitochondrial percent")
    thresholds = data["thresholds"]
    if not isinstance(thresholds, dict):
        raise ValueError("thresholds must be saved numeric filtering cutoffs")
    require_fields(thresholds, (), tuple(_QC_THRESHOLDS))
    for name, value in thresholds.items():
        _number(value, name, low=0, high=100 if name == "max_pct_mt" else None)
    for lower, upper in (("min_counts", "max_counts"), ("min_genes", "max_genes")):
        if lower in thresholds and upper in thresholds and thresholds[lower] > thresholds[upper]:
            raise ValueError("Saved QC minimum exceeds the corresponding maximum")
    _text(data["threshold_source"], "threshold_source")
    cells = _records(data, "cells", ("id", "sample", "batch", "total_counts", "n_genes_by_counts", "pct_counts_mt", "qc_pass"))
    for cell in cells:
        _text(cell["sample"], "sample")
        _text(cell["batch"], "batch")
        _boolean(cell["qc_pass"], "qc_pass")
        for metric in _QC_METRICS:
            _number(cell[metric], metric, nullable=True, low=0, high=100 if metric == "pct_counts_mt" else None)
            if metric != "pct_counts_mt" and cell[metric] is not None:
                _integer(cell[metric], metric)
    return cells


def _qc(capability, data):
    cells = _qc_data(data)
    retained = sum(cell["qc_pass"] for cell in cells)
    counts = f"Saved qc_pass counts: retained={retained}; excluded={len(cells) - retained}; total={len(cells)}."
    source = f"Threshold source: {data['threshold_source']}. No cutoff is estimated by this plot."
    if not data["thresholds"]:
        source += " No numeric cutoffs were saved."
    if capability == "qc_joint_scatter":
        panels = []
        for xmetric, ymetric in (("total_counts", "n_genes_by_counts"), ("total_counts", "pct_counts_mt"), ("n_genes_by_counts", "pct_counts_mt")):
            rows, marks = [], []
            for cell in cells:
                x, y = cell[xmetric], cell[ymetric]
                reason = "Saved QC coordinate is missing." if x is None or y is None else None
                r = row(cell["id"], x if not reason else None, y if not reason else None, entity_kind="cell",
                        group=cell[data["group_by"]], details={**cell, "qc_status": "retained" if cell["qc_pass"] else "excluded"}, reason=reason)
                rows.append(r)
                if not reason:
                    marks.append(_point(r, shape="circle" if cell["qc_pass"] else "triangle", value=cell["pct_counts_mt"]))
            references = [{"axis": axis, "value": value, "label": name} for name, value in data["thresholds"].items()
                          for axis, metric in (("x", xmetric), ("y", ymetric)) if _QC_THRESHOLDS[name] == metric]
            panels.append(panel(capability, f"QC: {_QC_METRICS[xmetric][0]} vs {_QC_METRICS[ymetric][0]}", rows,
                                axes=_axes(*_QC_METRICS[xmetric], *_QC_METRICS[ymetric]), marks=marks,
                                color_scale=_color([c["pct_counts_mt"] for c in cells], "Saved mitochondrial percentage", "percent"),
                                reference_lines=references, annotations=[counts, source, "Triangles denote saved excluded cells; retained and excluded cells both remain visible."],
                                limitations=[CELL_LIMITATION, PLOTTING_LIMITATION], panel_id=f"{capability}-{xmetric}-{ymetric}"))
        return panels
    bandwidth, grid_size = _density_options(data)
    kind = "violin" if capability == "qc_violins" else "ridge"
    panels = []
    for metric, (label, unit) in _QC_METRICS.items():
        references = [{"axis": "y" if kind == "violin" else "x", "value": value, "label": name}
                      for name, value in data["thresholds"].items() if _QC_THRESHOLDS[name] == metric]
        for stage in ("pre-filter", "post-filter"):
            observations = [{"id": c["id"], "group": c[data["group_by"]], "value": c[metric], "source": {**c, "metric": metric, "stage": stage},
                             "reason": "Excluded by saved qc_pass; source row retained in post-filter table." if stage == "post-filter" and not c["qc_pass"] else None}
                            for c in cells]
            panels.append(_distribution(capability, f"{label} — {stage}", observations, kind=kind, unit=unit,
                                        bandwidth=bandwidth, grid_size=grid_size, references=references, annotations=[counts, source],
                                        panel_id=f"{capability}-{metric}-{stage}"))
    return panels


def _observation_data(data):
    if data["unit"] != "count":
        raise ValueError("Library-depth distributions require count units, not transformed expression")
    entity_kind = data.get("entity_kind", "cell")
    if entity_kind not in ("cell", "sample", "donor"):
        raise ValueError("Distribution entity_kind must be cell, sample or donor")
    group_by = _text(data["group_by"], "group_by")
    observations = _records(data, "observations", ("id", "value", group_by))
    result = []
    for observation in observations:
        result.append({"id": observation["id"], "group": _text(observation[group_by], "saved group"),
                       "value": _raw_expression_value(observation["value"], "counts"), "source": dict(observation)})
    return result, entity_kind


def _bins(values, edges, transform="identity"):
    if not isinstance(edges, list) or not 2 <= len(edges) <= 513:
        raise ValueError("bins must be 2–513 explicit, strictly increasing source-scale edges")
    edges = [_number(v, "bin edge") for v in edges]
    if any(a >= b for a, b in zip(edges, edges[1:])):
        raise ValueError("bin edges must be strictly increasing")
    transformed = [_display(v, transform)[0] for v in edges]
    if any(v is None for v in transformed):
        raise ValueError("bin edges are incompatible with the requested display transform")
    for value in values:
        if value is not None and _display(value, transform)[0] is not None and not edges[0] <= value <= edges[-1]:
            raise ValueError("Saved value lies outside explicit bin range; silent clipping is forbidden")
    return edges, transformed


def _cutoffs(data, transform):
    cutoffs = data["cutoffs"]
    if not isinstance(cutoffs, list) or len(cutoffs) > 100:
        raise ValueError("cutoffs must be a bounded list of saved values and labels")
    result = []
    for cutoff in cutoffs:
        require_fields(cutoff, ("value", "label"))
        value = _number(cutoff["value"], "cutoff", low=0)
        shown, reason = _display(value, transform)
        if reason:
            raise ValueError("Saved cutoff cannot be displayed under the requested transform")
        result.append({"axis": "x", "value": shown, "label": _text(cutoff["label"], "cutoff label")})
    return result


def _histogram(capability, title, observations, *, edges, unit, transform="identity", entity_kind="cell", references=(), annotations=(), limitations=(), panel_id=None):
    source_edges, display_edges = _bins([o["value"] for o in observations], edges, transform)
    groups = list(dict.fromkeys(o["group"] for o in observations))
    grouped = {(g, i): [] for g in groups for i in range(len(edges) - 1)}
    rows = []
    for number, o in enumerate(observations):
        value, reason = _display(o["value"], transform)
        reason = o.get("reason") or reason
        if reason:
            value = None
        index = min(len(source_edges) - 2, bisect_right(source_edges, o["value"]) - 1) if value is not None else None
        source_details = {**o.get("source", {}), "source_value": o["value"], "display_value": value,
                          "bin_index": index, "source_bin_left": source_edges[index] if index is not None else None,
                          "source_bin_right": source_edges[index + 1] if index is not None else None, "display_transform": transform}
        if number == 0:
            source_details["source_bin_edges"] = source_edges
        r = row(o["id"], value, 0 if value is not None else None, entity_kind=entity_kind, group=o["group"], reason=reason,
                details=source_details, link=o.get("link"))
        rows.append(r)
        if index is not None:
            grouped[(o["group"], index)].append(r)
    marks = [{"type": "rect", "x": display_edges[i], "y": 0, "width": display_edges[i + 1] - display_edges[i],
              "height": len(members), "keys": [r["key"] for r in members], "group": group, "style": {"opacity": .34}}
             for (group, i), members in grouped.items()]
    notes = ["Bins are explicit source-scale intervals [left, right), with the final right edge included; no source value is clipped. Exact complete edges are retained in the first source row details.",
             "Groups use overlaid full-width transparent bars, not narrow side-by-side mini-bars.", *annotations]
    return panel(capability, title, rows, axes=_axes(title, unit, f"Number of {entity_kind} observations", "count", transform),
                 marks=marks, reference_lines=list(references), annotations=notes,
                 limitations=[CELL_LIMITATION if entity_kind == "cell" else "Sample/donor observation counts alone do not establish independent replication.", PLOTTING_LIMITATION, *limitations], panel_id=panel_id)


def _depth(capability, data):
    observations, entity_kind = _observation_data(data)
    transform = data.get("transform", "identity")
    _display(None, transform)
    if capability == "batch_boxplot":
        return [_distribution(capability, "Library depth by saved group", observations, kind="box", unit="count",
                              transform=transform, entity_kind=entity_kind)]
    references = _cutoffs(data, transform)
    for group in dict.fromkeys(o["group"] for o in observations):
        values = [o["value"] for o in observations if o["group"] == group and _display(o["value"], transform)[0] is not None]
        if values:
            source_median = median(values)
            references.append({"axis": "x", "value": _display(source_median, transform)[0], "label": f"Median ({group}), source={source_median:g} count"})
    if capability == "depth_histogram":
        return [_histogram(capability, "Library-depth histogram", observations, edges=data["bins"], unit="count",
                           transform=transform, entity_kind=entity_kind, references=references)]
    rows, marks = [], []
    for group in dict.fromkeys(o["group"] for o in observations):
        members = [o for o in observations if o["group"] == group]
        valid = sorted(_display(o["value"], transform)[0] for o in members if _display(o["value"], transform)[0] is not None)
        group_rows = []
        for o in members:
            x, reason = _display(o["value"], transform)
            y = bisect_right(valid, x) / len(valid) if x is not None else None
            r = row(o["id"], x, y, entity_kind=entity_kind, group=group, reason=reason,
                    details={**o["source"], "source_value": o["value"], "display_transform": transform, "finite_group_n": len(valid)})
            rows.append(r)
            if x is not None:
                group_rows.append(r)
        if valid:
            points, previous = [[valid[0], 0]], 0.0
            for value in dict.fromkeys(valid):
                fraction = bisect_right(valid, value) / len(valid)
                points.extend([[value, previous], [value, fraction]])
                previous = fraction
            marks.append(line_mark(points, keys=[r["key"] for r in group_rows], group=group))
    return [panel(capability, "Library-depth empirical CDF", rows,
                  axes=_axes("Library depth", "count", "Fraction at or below depth", "fraction", transform),
                  marks=marks, reference_lines=references,
                  annotations=["Right-continuous ECDF: tied values share count(value <= x) / finite group n; missing values remain rows outside that denominator."],
                  limitations=[CELL_LIMITATION if entity_kind == "cell" else "Independent biological replication is not inferred from sample IDs.", PLOTTING_LIMITATION])]


def _expression_distributions(capability, data):
    transform = _scale(data)
    bandwidth, grid_size = _density_options(data)
    group_by = _text(data["group_by"], "group_by")
    observations = _records(data, "observations", ("id", "cell_id", "gene_id", "value", group_by))
    groups = defaultdict(list)
    for source in observations:
        _text(source["cell_id"], "cell_id")
        _text(source["gene_id"], "gene_id")
        groups[source["gene_id"]].append({"id": source["id"], "group": _text(source[group_by], "saved group"),
                                          "value": _raw_expression_value(source["value"], data["source_scale"]),
                                          "source": dict(source), "link": {"kind": "cell", "id": source["cell_id"]}})
    kind = {"expression_violins": "violin", "expression_boxplots": "box", "expression_ridges": "ridge"}[capability]
    for gene, members in groups.items():
        require_unique([o["source"]["cell_id"] for o in members], f"cell IDs for {gene}")
    return [_distribution(capability, f"{gene} expression — {kind}", members, kind=kind, unit=data["unit"], transform=transform,
                          bandwidth=bandwidth, grid_size=grid_size,
                          details={"layer": data["layer"], "source_scale": data["source_scale"], "source_unit": data["unit"]},
                          annotations=[f"Saved layer: {data['layer']}; source scale: {data['source_scale']}; display transform: {transform}."],
                          panel_id=_panel_id(capability, gene)) for gene, members in groups.items()]


def _doublet(capability, data):
    if data["score_unit"] not in ("score", "probability"):
        raise ValueError("Doublet scores must be declared scores or probabilities, not expression/counts")
    for field in ("threshold_source", "method", "embedding_name"):
        _text(data[field], field)
    threshold = _number(data["threshold"], "called threshold", low=0 if data["score_unit"] == "probability" else None,
                        high=1 if data["score_unit"] == "probability" else None)
    bandwidth, grid_size = _density_options(data)
    cells = _records(data, "cells", ("id", "score", "predicted_doublet", "x", "y"))
    observations = []
    for c in cells:
        score = _number(c["score"], "doublet score", nullable=True, low=0 if data["score_unit"] == "probability" else None,
                        high=1 if data["score_unit"] == "probability" else None)
        _boolean(c["predicted_doublet"], "predicted_doublet")
        _number(c["x"], "embedding x", nullable=True)
        _number(c["y"], "embedding y", nullable=True)
        observations.append({"id": c["id"], "value": score, "group": "Called doublet" if c["predicted_doublet"] else "Other cell", "source": dict(c)})
    references = [{"axis": "x", "value": threshold, "label": f"Saved called threshold: {data['threshold_source']}"}]
    notes = [f"Score method: {data['method']}. Saved doublet calls are displayed without recomputation.", "Scores are not gene expression; a score alone does not establish a calibrated probability."]
    histogram = _histogram(capability, "Saved doublet-score histogram", observations, edges=data["bins"], unit=data["score_unit"], references=references, annotations=notes, panel_id=f"{capability}-histogram")
    density_rows, density_marks = [], []
    for o in observations:
        density_rows.append(row(o["id"], o["value"], 0 if o["value"] is not None else None, entity_kind="cell", group=o["group"],
                                details=o["source"], reason="Saved doublet score is missing." if o["value"] is None else None))
    values = [o["value"] for o in observations if o["value"] is not None]
    if values:
        summary = distribution_summary(values, bandwidth=bandwidth, grid_size=grid_size)
        if summary["density"]:
            density_marks.append(line_mark(summary["density"], keys=[r["key"] for r in density_rows if r["x"] is not None], group="All scored cells"))
            notes.append(f"Gaussian KDE bandwidth={summary['bandwidth']:.8g} ({summary['bandwidth_method']}); density integrates approximately to one on the displayed grid.")
        else:
            notes.append(f"Density unavailable: {summary['density_unavailable_reason']}; no spread invented.")
    density_marks.extend(_point(r, radius=2) for r in density_rows if r["x"] is not None)
    density = panel(capability, "Saved doublet-score KDE and observation rug", density_rows,
                    axes=_axes("Saved doublet score", data["score_unit"], "Probability density", f"1/{data['score_unit']}"),
                    marks=density_marks, reference_lines=references, annotations=notes, limitations=[CELL_LIMITATION, PLOTTING_LIMITATION], panel_id=f"{capability}-density")
    rows, marks = [], []
    for c, o in zip(cells, observations):
        reason = "Saved score or embedding coordinate is missing." if c["x"] is None or c["y"] is None or c["score"] is None else None
        r = row(c["id"], c["x"] if not reason else None, c["y"] if not reason else None, entity_kind="cell", group=o["group"], details=dict(c), reason=reason)
        rows.append(r)
        if not reason:
            marks.append(_point(r, shape="triangle" if c["predicted_doublet"] else "circle", value=c["score"]))
    embedding = panel(capability, f"{data['embedding_name']} — saved doublet calls", rows,
                      axes=_axes(f"{data['embedding_name']} 1", "embedding_coordinate", f"{data['embedding_name']} 2", "embedding_coordinate"),
                      marks=marks, color_scale=_color(values, "Saved doublet score", data["score_unit"]),
                      annotations=[*notes, "Triangle = saved predicted_doublet; circle = other cell."], limitations=[CELL_LIMITATION, PLOTTING_LIMITATION], panel_id=f"{capability}-embedding")
    return [histogram, density, embedding]


def _hvg(capability, data):
    _scale(data, unit_field="mean_unit")
    units = {"variance": f"{data['mean_unit']}_squared", "dispersion": "dimensionless",
             "standardized_variance": "dimensionless", "normalized_dispersion": "dimensionless"}
    if _text(data["statistic"], "saved HVG statistic") not in units or data["statistic_unit"] != units[data["statistic"]]:
        raise ValueError("HVG statistic and unit are incompatible")
    _text(data["method"], "HVG method")
    xtransform, ytransform = data.get("x_transform", "identity"), data.get("y_transform", "identity")
    genes = _records(data, "genes", ("id", "mean", "statistic", "highly_variable"))
    rows, marks = [], []
    for g in genes:
        mean = _expression_value(g["mean"], data["source_scale"])
        statistic = _number(g["statistic"], "saved HVG statistic", nullable=True,
                            low=0 if data["statistic"] in ("variance", "dispersion", "standardized_variance") else None)
        _boolean(g["highly_variable"], "highly_variable")
        x, xr = _display(mean, xtransform)
        y, yr = _display(statistic, ytransform)
        reason = xr or yr
        r = row(g["id"], x if not reason else None, y if not reason else None, entity_kind="gene",
                group="Selected HVG" if g["highly_variable"] else "Other gene",
                details={**g, "layer": data["layer"], "source_scale": data["source_scale"], "method": data["method"]}, reason=reason)
        rows.append(r)
        if not reason:
            marks.append(_point(r, shape="diamond" if g["highly_variable"] else "circle"))
    return [panel(capability, "Saved HVG mean–variance/dispersion", rows,
                  axes=_axes("Saved mean expression", data["mean_unit"], data["statistic"], data["statistic_unit"], xtransform, ytransform), marks=marks,
                  annotations=[f"Saved method: {data['method']}; layer: {data['layer']}.", "highly_variable flags and variance/dispersion values are saved inputs, not recomputed or invented."],
                  limitations=[PLOTTING_LIMITATION])]


def _scree(capability, data):
    if data["variance_unit"] not in ("fraction", "percent"):
        raise ValueError("PCA variance_unit must be fraction or percent")
    _text(data["layer"], "PCA layer")
    if _text(data["source_scale"], "source_scale") not in _SCALES or data["source_scale"] == "module_score":
        raise ValueError("PCA input must identify the saved expression source scale")
    components = _records(data, "components", ("pc", "variance_explained"))
    maximum = 1 if data["variance_unit"] == "fraction" else 100
    for c in components:
        _integer(c["pc"], "PC index", low=1)
        _number(c["variance_explained"], "explained variance", nullable=True, low=0, high=maximum)
    _unique_values([c["pc"] for c in components], "PC indices")
    if sum(c["variance_explained"] or 0 for c in components) > maximum + 1e-8:
        raise ValueError("Saved PCA variance contributions exceed the declared total")
    rows, marks, contiguous = [], [], []
    for c in sorted(components, key=lambda r: r["pc"]):
        value = c["variance_explained"]
        shown = None if value is None else value * (100 if maximum == 1 else 1)
        r = row(f"PC{c['pc']}", c["pc"] if shown is not None else None, shown, entity_kind="component", group="Saved PCs",
                details={**c, "source_unit": data["variance_unit"], "layer": data["layer"], "source_scale": data["source_scale"]},
                reason="Saved explained variance is missing." if value is None else None,
                link={"kind": "component", "id": f"PC{c['pc']}"})
        rows.append(r)
        if shown is not None:
            marks.append(_point(r))
        if shown is None or (contiguous and c["pc"] != contiguous[-1]["x"] + 1):
            if len(contiguous) > 1:
                marks.append(line_mark([[q["x"], q["y"]] for q in contiguous], keys=[q["key"] for q in contiguous], group="Saved PCs"))
            contiguous = []
        if shown is not None:
            contiguous.append(r)
    if len(contiguous) > 1:
        marks.append(line_mark([[q["x"], q["y"]] for q in contiguous], keys=[q["key"] for q in contiguous], group="Saved PCs"))
    return [panel(capability, "Saved single-cell PCA explained variance", rows,
                  axes=_axes("Principal component", "component_index", "Explained variance", "percent", "identity", "fraction_to_percent" if maximum == 1 else "identity"),
                  marks=marks, annotations=["Only saved explained-variance contributions are plotted; no elbow threshold or retained-PC choice is inferred."], limitations=[CELL_LIMITATION, PLOTTING_LIMITATION])]


def _integration(capability, data):
    for field in ("embedding_name", "before_method", "after_method"):
        _text(data[field], field)
    metric_names = ("batch_asw", "cell_type_asw", "batch_lisi", "cell_type_lisi")
    if not isinstance(data["metric_definitions"], dict):
        raise ValueError("metric_definitions must describe the saved quantities")
    require_fields(data["metric_definitions"], metric_names)
    for name in metric_names:
        _text(data["metric_definitions"][name], name)
    cells = _records(data, "cells", ("id", "batch", "cell_type", "before", "after"))
    for c in cells:
        _text(c["batch"], "batch")
        _text(c["cell_type"], "cell_type")
        for stage in ("before", "after"):
            if not isinstance(c[stage], list) or len(c[stage]) != 2:
                raise ValueError("Before/after pairing requires exactly two coordinates for each identical source cell")
            for value in c[stage]:
                _number(value, "embedding coordinate", nullable=True)
    metrics = _records(data, "metrics", ("id", "cell_type", *metric_names))
    for m in metrics:
        _text(m["cell_type"], "metric cell_type")
        if m["cell_type"] not in {c["cell_type"] for c in cells}:
            raise ValueError("Saved integration metric cell type is absent from source embeddings")
        for name in metric_names:
            _number(m[name], name, nullable=True, low=-1 if name.endswith("asw") else 1, high=1 if name.endswith("asw") else None)
    panels = []
    for stage in ("before", "after"):
        for grouping in ("batch", "cell_type"):
            rows, marks = [], []
            for c in cells:
                reason = "Saved matched embedding coordinate is missing." if None in c[stage] else None
                r = row(c["id"], c[stage][0] if not reason else None, c[stage][1] if not reason else None, entity_kind="cell", group=c[grouping],
                        details={**c, "stage": stage, "method": data[f"{stage}_method"]}, reason=reason)
                rows.append(r)
                if not reason:
                    marks.append(_point(r))
            panels.append(panel(capability, f"{stage.title()} integration — {grouping}", rows,
                                axes=_axes(f"{data['embedding_name']} 1", "embedding_coordinate", f"{data['embedding_name']} 2", "embedding_coordinate"), marks=marks,
                                annotations=[f"Saved method: {data[f'{stage}_method']}.", "Before/after source membership is keyed by the same explicit cell ID; embeddings are not aligned or recomputed."],
                                limitations=[CELL_LIMITATION, "Visual mixing alone does not establish successful integration or biological conservation.", PLOTTING_LIMITATION], panel_id=f"{capability}-{stage}-{grouping}"))
    for suffix in ("asw", "lisi"):
        xfield, yfield = f"batch_{suffix}", f"cell_type_{suffix}"
        rows = [row(m["id"], m[xfield] if m[yfield] is not None else None, m[yfield] if m[xfield] is not None else None,
                    entity_kind="cell_type", group=m["cell_type"], details={**m, "definitions": dict(data["metric_definitions"])},
                    reason="Saved integration metric is missing." if m[xfield] is None or m[yfield] is None else None,
                    link={"kind": "cell_type", "id": m["cell_type"]}) for m in metrics]
        panels.append(panel(capability, f"Saved batch / cell-type {suffix.upper()} tradeoff", rows,
                            axes=_axes(xfield, "silhouette" if suffix == "asw" else "inverse_simpson", yfield, "silhouette" if suffix == "asw" else "inverse_simpson"),
                            marks=[_point(r) for r in rows if r["x"] is not None and r["y"] is not None],
                            annotations=[f"{name}: {data['metric_definitions'][name]}" for name in (xfield, yfield)],
                            limitations=["Metrics are actual saved inputs. No quality direction, rescaling, optimality or comparative benefit is inferred.", PLOTTING_LIMITATION], panel_id=f"{capability}-{suffix}"))
    return panels


def _feature(capability, data):
    transform = _scale(data)
    _text(data["feature_id"], "feature_id")
    _text(data["embedding_name"], "embedding_name")
    cells = _records(data, "cells", ("id", "x", "y", "value"), ("group",))
    rows, marks, colors = [], [], []
    for c in cells:
        x, y = _number(c["x"], "embedding x", nullable=True), _number(c["y"], "embedding y", nullable=True)
        value = _raw_expression_value(c["value"], data["source_scale"])
        color, reason = _display(value, transform)
        reason = reason or ("Saved embedding coordinate is missing." if x is None or y is None else None)
        r = row(c["id"], x if not reason else None, y if not reason else None, entity_kind="cell", group=_text(c.get("group", "Expression"), "group"),
                details={**c, "source_value": value, "display_value": color, "layer": data["layer"], "source_scale": data["source_scale"],
                         "display_transform": transform, "feature_id": data["feature_id"]}, reason=reason)
        rows.append(r)
        if not reason:
            marks.append(_point(r, value=color))
            colors.append(color)
    color_unit = data["unit"] if transform == "identity" else f"{transform}({data['unit']})"
    return [panel(capability, f"{data['feature_id']} — {data['embedding_name']}", rows,
                  axes=_axes(f"{data['embedding_name']} 1", "embedding_coordinate", f"{data['embedding_name']} 2", "embedding_coordinate"), marks=marks,
                  color_scale=_color(colors, f"{data['feature_id']}; {transform} display", color_unit, diverging=data["source_scale"] == "scaled_expression"),
                  annotations=[f"Saved bounded layer: {data['layer']}; source scale/unit: {data['source_scale']} / {data['unit']}; display transform: {transform}.", "Every source cell is retained; neither expression nor embedding is recomputed."],
                  limitations=[CELL_LIMITATION, PLOTTING_LIMITATION])]


def _ids(values, name):
    if not isinstance(values, list) or len(values) > MAX_ROWS:
        raise ValueError(f"{name} must be a bounded list of exact source IDs")
    for value in values:
        _text(value, name)
    require_unique(values, name)
    return values


def _marker(capability, data):
    transform = _scale(data)
    if data["denominator_kind"] not in ("measured_cells", "measured_samples"):
        raise ValueError("Marker fraction denominator must identify measured cells or measured samples")
    threshold = _number(data["expression_threshold"], "expression threshold")
    max_radius = _number(data.get("max_radius", 11), "max_radius", low=1, high=30)
    aggregates = _records(data, "group_gene_aggregates", ("id", "group", "gene_id", "numerator", "denominator", "member_ids"), ("mean_expression", "values"))
    groups, genes = [], []
    for a in aggregates:
        for name, categories in (("group", groups), ("gene_id", genes)):
            value = _text(a[name], name)
            if value not in categories:
                categories.append(value)
    _unique_values([(a["group"], a["gene_id"]) for a in aggregates], "group/gene aggregate identities")
    rows, marks, means = [], [], []
    for a in aggregates:
        n = _integer(a["denominator"], "denominator")
        numerator = _integer(a["numerator"], "numerator")
        members = _ids(a["member_ids"], "marker denominator member_ids")
        if len(members) != n or numerator > n:
            raise ValueError("Marker numerator/denominator is incompatible with exact source membership")
        mean = _expression_value(a.get("mean_expression"), data["source_scale"])
        mean_origin = "saved aggregate mean"
        if "values" in a:
            if not isinstance(a["values"], list) or len(a["values"]) != n:
                raise ValueError("Raw marker values must match denominator member IDs")
            values = [_raw_expression_value(v, data["source_scale"]) for v in a["values"]]
            if any(v is None for v in values):
                raise ValueError("Measured-value denominators cannot silently include missing raw values")
            if sum(v > threshold for v in values) != numerator:
                raise ValueError("Saved numerator disagrees with raw values above the explicit expression threshold")
            raw_mean = sum(values) / n if n else None
            if mean is not None and (raw_mean is None or not math.isclose(mean, raw_mean, rel_tol=1e-8, abs_tol=1e-10)):
                raise ValueError("Saved mean expression disagrees with the exact raw values")
            if mean is None:
                mean = raw_mean
                mean_origin = "descriptive mean computed from exact saved raw values"
        if n == 0 and (mean is not None or numerator != 0):
            raise ValueError("Empty marker denominators cannot have a mean or positive numerator")
        color, reason = _display(mean, transform)
        if n == 0:
            reason = "No measured members in denominator; fraction and mean are unavailable."
        fraction = numerator / n if n else None
        r = row(a["id"], genes.index(a["gene_id"]) if not reason else None, groups.index(a["group"]) if not reason else None,
                entity_kind="gene", group=a["group"], reason=reason, link={"kind": "gene", "id": a["gene_id"]},
                details={**a, "source_mean_expression": mean, "mean_origin": mean_origin, "fraction_above_threshold": fraction,
                         "expression_threshold": threshold, "threshold_comparison": ">", "denominator_kind": data["denominator_kind"],
                         "layer": data["layer"], "source_scale": data["source_scale"], "display_transform": transform})
        rows.append(r)
        if not reason:
            mark = _point(r, radius=max_radius * math.sqrt(fraction), value=color)
            mark["size_value"] = fraction
            marks.append(mark)
            means.append(color)
    return [panel(capability, "Saved marker expression / fraction dotplot", rows,
                  axes=_axes("Gene", "gene", "Saved group", "category"), marks=marks, x_categories=genes, y_categories=groups,
                  color_scale=_color(means, f"Mean expression ({transform} display)", data["unit"] if transform == "identity" else f"{transform}({data['unit']})", diverging=data["source_scale"] == "scaled_expression"),
                  size_scale={"label": f"Fraction strictly above {threshold:g} ({data['unit']})", "unit": "fraction", "domain": [0, 1], "max_radius": max_radius},
                  annotations=["Dot area is numerator / measured denominator; it is not raw count or percent.", f"Threshold is applied to saved {data['source_scale']} values in layer {data['layer']} before the display transform."],
                  limitations=[CELL_LIMITATION, "Aggregate dotplots are descriptive; no expression test is performed.", PLOTTING_LIMITATION])]


def _heatmap(capability, data):
    module = capability == "module_heatmap"
    transform = _scale(data, module=module, transform_field="transform")
    if module:
        _text(data["score_definition"], "score_definition")
    rows, columns = _ids(data["row_ids"], "matrix row IDs"), _ids(data["column_ids"], "matrix column IDs")
    values = data["values"]
    if not rows or not columns or len(rows) * len(columns) > MAX_MATRIX_CELLS:
        raise ValueError("Expression matrix must be nonempty and bounded to 100000 saved cells")
    if not isinstance(values, list) or len(values) != len(rows) or any(not isinstance(r, list) or len(r) != len(columns) for r in values):
        raise ValueError("Saved matrix dimensions must match explicit row and column identities")
    for source_row in values:
        for value in source_row:
            _raw_expression_value(value, data["source_scale"])
    cluster = _boolean(data.get("cluster", False), "cluster")
    result = matrix_panel(capability, "Saved module-score heatmap" if module else "Saved expression heatmap", values, rows, columns,
                          unit=data["unit"], transform=transform, row_metadata=data.get("row_metadata"), column_metadata=data.get("column_metadata"),
                          cluster=cluster, diverging=data["source_scale"] in ("scaled_expression", "module_score") or "zscore" in transform,
                          details={"source_scale": data["source_scale"], "layer": data["layer"], "row_entity_kind": "module" if module else "gene",
                                   "score_definition": data.get("score_definition"), "measurement_kind": "module_score" if module else "expression"})
    result["annotations"].append(f"Source layer: {data['layer']}; source scale: {data['source_scale']}; display transform: {transform}.")
    if module:
        result["annotations"].append(f"Module scores are not expression: {data['score_definition']}")
    result["limitations"].extend([CELL_LIMITATION, PLOTTING_LIMITATION])
    return [result]


def _composition_records(data, *, donor=False):
    if data["denominator_kind"] not in ("all_saved_cells", "qc_pass_cells", "classified_cells"):
        raise ValueError("Composition denominator_kind must identify the saved cell population")
    required = ("id", "cell_type", "numerator", "denominator", "member_ids", "numerator_ids")
    required += ("donor_id", "sample_id", "condition") if donor else ("batch",)
    records = _records(data, "compositions", required)
    denominators, numerator_sets, cell_units, identities = {}, defaultdict(set), {}, set()
    for c in records:
        _text(c["cell_type"], "cell_type")
        if donor:
            for name in ("donor_id", "sample_id", "condition"):
                _text(c[name], name)
            grouping = (c[f"{data['biological_unit']}_id"], c["condition"])
        else:
            grouping = _text(c["batch"], "batch")
        identity = (grouping, c["cell_type"])
        if identity in identities:
            raise ValueError("Duplicate biological unit/batch and cell-type records cannot be extra donor replicates")
        identities.add(identity)
        n = _integer(c["denominator"], "composition denominator", low=1)
        members = set(_ids(c["member_ids"], "composition denominator member_ids"))
        if n != len(members):
            raise ValueError("Composition denominator must equal exact member count")
        if grouping in denominators and denominators[grouping] != members:
            raise ValueError("Cell types in a single composition must share identical denominator membership")
        denominators[grouping] = members
        for member in members:
            if member in cell_units and cell_units[member] != grouping:
                raise ValueError("One source cell cannot be assigned to multiple biological-unit/batch denominator memberships")
            cell_units[member] = grouping
        if c["numerator"] is None:
            if c["numerator_ids"] is not None:
                raise ValueError("Missing composition numerator must explicitly mark numerator membership unavailable")
            continue
        numerator = _integer(c["numerator"], "composition numerator")
        positives = set(_ids(c["numerator_ids"], "composition numerator_ids"))
        if numerator != len(positives) or numerator > n or not positives <= members:
            raise ValueError("Composition numerator is incompatible with denominator membership")
        if positives & numerator_sets[grouping]:
            raise ValueError("Exclusive cell-type composition cannot reuse a cell in multiple categories")
        numerator_sets[grouping].update(positives)
    return records


def _composition_interval_definition(summary, data):
    """Describe saved bounds without inventing coverage or an inferential method."""
    kind = summary.get("interval_kind", "unassessed")
    if kind not in ("confidence", "credible", "range", "unassessed"):
        raise ValueError("Saved interval_kind must be confidence, credible, range or unassessed")
    level = summary.get("level")
    scope = summary.get("scope")
    denominator_kind = summary.get("denominator_kind")
    if kind in ("confidence", "credible"):
        level = _number(level, "saved interval level", low=0, high=1)
        if not 0 < level < 1:
            raise ValueError("Saved confidence/credible interval level must be strictly between zero and one")
    elif level is not None:
        raise ValueError("A saved range or unassessed interval kind cannot claim a confidence/credible level")
    if kind != "unassessed":
        scope = _text(scope, "saved interval scope")
        denominator_kind = _text(denominator_kind, "saved interval denominator_kind")
    else:
        if scope is not None:
            scope = _text(scope, "saved unassessed interval scope")
        if denominator_kind is not None:
            denominator_kind = _text(denominator_kind, "saved interval denominator_kind")
    if denominator_kind is not None and denominator_kind != data["denominator_kind"]:
        raise ValueError("Saved interval denominator population is inconsistent with the displayed donor/sample fractions")
    label = f"{100 * level:g}% {kind}" if kind in ("confidence", "credible") else "Saved range" if kind == "range" else "Unassessed bounds"
    return {"interval_kind": kind, "level": level, "scope": scope, "denominator_kind": denominator_kind,
            "display_denominator_kind": data["denominator_kind"], "interval_label": label,
            "uncertainty_definition_status": "unassessed" if kind == "unassessed" else "explicit_saved_definition",
            "saved_interval_definition_fields": {key: summary[key] for key in ("interval_kind", "level", "scope", "denominator_kind") if key in summary}}


def _donor_composition(capability, data):
    if data["unit"] != "fraction" or data["biological_unit"] not in ("donor", "sample"):
        raise ValueError("Donor composition requires explicit donor/sample biological unit and fraction units")
    records = _composition_records(data, donor=True)
    unit_field = f"{data['biological_unit']}_id"
    identities = [(c[unit_field], c["condition"], c["cell_type"]) for c in records]
    if len(set(identities)) != len(identities):
        raise ValueError("Multiple records per biological unit/condition/cell type cannot be treated as extra donor replicates")
    if data["biological_unit"] == "sample":
        by_donor = defaultdict(set)
        for c in records:
            by_donor[c["donor_id"]].add(c["sample_id"])
        if any(len(samples) > 1 for samples in by_donor.values()):
            raise ValueError("Repeated samples from one donor cannot be independent biological units; supply donor-level composition")
    summaries = data.get("summaries", [])
    pairs = data.get("pairs", [])
    if not isinstance(summaries, list) or len(summaries) > MAX_ROWS or not isinstance(pairs, list) or len(pairs) > MAX_ROWS:
        raise ValueError("Saved composition summaries and pairing must be bounded arrays")
    lookup = {c["id"]: c for c in records}
    for pair in pairs:
        require_fields(pair, ("from_id", "to_id"))
        if pair["from_id"] not in lookup or pair["to_id"] not in lookup:
            raise ValueError("Saved pair endpoints must both refer to displayed composition records")
        first, second = lookup[pair["from_id"]], lookup[pair["to_id"]]
        if first[unit_field] != second[unit_field] or first["cell_type"] != second["cell_type"] or first["condition"] == second["condition"]:
            raise ValueError("Pairing must connect the same biological unit and cell type across different conditions")
    panels = []
    for celltype in dict.fromkeys(c["cell_type"] for c in records):
        subset = [c for c in records if c["cell_type"] == celltype]
        conditions = list(dict.fromkeys(c["condition"] for c in subset))
        rows, marks, summary_notes = [], [], []
        for c in subset:
            fraction = c["numerator"] / c["denominator"] if c["numerator"] is not None else None
            r = row(c["id"], conditions.index(c["condition"]) + _jitter(c[unit_field]) if fraction is not None else None, fraction,
                    entity_kind=data["biological_unit"], group=c["condition"], details={**c, "denominator_kind": data["denominator_kind"], "fraction": fraction},
                    reason="Saved cell-type numerator is unavailable." if fraction is None else None,
                    link={"kind": data["biological_unit"], "id": c[unit_field]})
            rows.append(r)
            if fraction is not None:
                marks.append(_point(r, radius=4))
        row_lookup = {r["id"]: r for r in rows}
        for pair in pairs:
            if pair["from_id"] in row_lookup:
                start, end = row_lookup[pair["from_id"]], row_lookup[pair["to_id"]]
                if start["x"] is not None and end["x"] is not None:
                    marks.append(_segment(start["x"], start["y"], end["x"], end["y"], [start["key"], end["key"]], "Saved pairing"))
        for summary in summaries:
            if not isinstance(summary, dict):
                raise ValueError("Composition summary must be a saved object")
            common = ("id", "type", "condition", "cell_type", "unit_ids", "method", "unit")
            kind = summary.get("type")
            if kind == "interval":
                require_fields(summary, (*common, "estimate", "low", "high"), ("interval_kind", "level", "scope", "denominator_kind"))
            elif kind == "box":
                require_fields(summary, (*common, "q1", "median", "q3", "whisker_low", "whisker_high"))
            else:
                raise ValueError("Saved summary type must be box or interval")
            _text(summary["id"], "summary id")
            _text(summary["method"], "saved summary method")
            if summary["unit"] != "fraction":
                raise ValueError("Saved composition summaries require the same fraction units")
            if summary["cell_type"] not in {c["cell_type"] for c in records}:
                raise ValueError("Saved summary references a cell type absent from source composition")
            if summary["cell_type"] != celltype:
                continue
            members = [r for r in rows if r["entity_kind"] == data["biological_unit"] and r["group"] == summary["condition"] and r["y"] is not None]
            unit_ids = _ids(summary["unit_ids"], "summary unit_ids")
            if not members or set(unit_ids) != {r["details"][unit_field] for r in members}:
                raise ValueError("Saved summary support must exactly match displayed finite biological units")
            x = conditions.index(summary["condition"])
            estimate = summary["estimate"] if kind == "interval" else summary["median"]
            value_fields = ("low", "estimate", "high") if kind == "interval" else ("whisker_low", "q1", "median", "q3", "whisker_high")
            values = [_number(summary[name], f"saved {name}", low=0, high=1) for name in value_fields]
            if values != sorted(values):
                raise ValueError("Saved composition summary bounds must be ordered")
            if kind == "box":
                actual = distribution_summary([r["y"] for r in members])
                if any(not math.isclose(summary[name], actual[name], abs_tol=1e-9) for name in value_fields):
                    raise ValueError("Saved Tukey box summary disagrees with displayed biological-unit values")
            definition = _composition_interval_definition(summary, data) if kind == "interval" else {}
            r = row(summary["id"], x, estimate, entity_kind="group", group=summary["condition"], details={**summary, **definition},
                    link={"kind": "group", "id": f"{celltype}/{summary['condition']}"})
            rows.append(r)
            keys = [m["key"] for m in members] + [r["key"]]
            if kind == "interval":
                marks.extend([_segment(x, summary["low"], x, summary["high"], keys, summary["condition"]),
                              _segment(x - .12, summary["low"], x + .12, summary["low"], keys, summary["condition"]),
                              _segment(x - .12, summary["high"], x + .12, summary["high"], keys, summary["condition"]), _point(r, shape="diamond", radius=5),
                              {"type": "text", "x": x + .18, "y": summary["high"], "text": definition["interval_label"],
                               "anchor": "start", "keys": keys, "group": summary["condition"]}])
                if definition["interval_kind"] == "unassessed":
                    summary_notes.append(f"{summary['condition']} / {celltype}: Unassessed saved bounds; no confidence or credible coverage is assigned. Saved method: {summary['method']}; saved scope: {definition['scope'] or 'not supplied'}. Support is the exact {len(unit_ids)} displayed {data['biological_unit']} IDs.")
                else:
                    summary_notes.append(f"{summary['condition']} / {celltype}: {definition['interval_label']}; scope: {definition['scope']}; saved method: {summary['method']}; support: n={len(unit_ids)} {data['biological_unit']} units; denominator population: {definition['denominator_kind']}. This reports a saved interval definition; no interval is computed by the plot.")
            else:
                marks.extend([{"type": "rect", "x": x - .25, "y": summary["q1"], "width": .5, "height": summary["q3"] - summary["q1"],
                               "keys": keys, "group": summary["condition"], "style": {"opacity": .2}},
                              _segment(x - .25, estimate, x + .25, estimate, keys, summary["condition"]),
                              _segment(x, summary["whisker_low"], x, summary["whisker_high"], keys, summary["condition"])])
        panels.append(panel(capability, f"{celltype} composition per {data['biological_unit']}", rows,
                            axes=_axes("Saved condition", "category", "Cell-type fraction of saved denominator", "fraction"), marks=marks,
                            x_categories=conditions,
                            annotations=[f"Biological unit: {data['biological_unit']}; denominator population: {data['denominator_kind']}.", "Each point retains numerator/denominator cell IDs. Summary boxes/intervals and pairing are shown only if explicitly saved.",
                                         *[f"{condition}: n={sum(c['condition'] == condition and c['numerator'] is not None for c in subset)} finite {data['biological_unit']} compositions." for condition in conditions], *summary_notes],
                            limitations=["No composition significance test or uncertainty estimate is computed; donor count, not cell count, determines biological replication.", PLOTTING_LIMITATION],
                            panel_id=_panel_id(capability, celltype)))
    return panels


def _batch_composition(capability, data):
    if data["unit"] not in ("fraction", "percent", "count"):
        raise ValueError("Composition unit must be explicit fraction, percent or count")
    records = _composition_records(data)
    _unique_values([(c["batch"], c["cell_type"]) for c in records], "batch/cell-type identities")
    batches = list(dict.fromkeys(c["batch"] for c in records))
    rows, marks, totals = [], [], defaultdict(float)
    for c in records:
        value = None if c["numerator"] is None else (c["numerator"] if data["unit"] == "count" else c["numerator"] / c["denominator"] * (100 if data["unit"] == "percent" else 1))
        x, baseline = batches.index(c["batch"]), totals[c["batch"]]
        r = row(c["id"], x if value is not None else None, baseline + value / 2 if value is not None else None,
                entity_kind="cell_type", group=c["cell_type"], details={**c, "display_value": value, "denominator_kind": data["denominator_kind"]},
                reason="Saved composition numerator is unavailable; no count/fraction is invented." if value is None else None,
                link={"kind": "cell_type", "id": c["cell_type"]})
        rows.append(r)
        if value is not None:
            marks.append({"type": "rect", "x": x - .35, "y": baseline, "width": .7, "height": value, "keys": [r["key"]], "group": c["cell_type"]})
            totals[c["batch"]] += value
    return [panel(capability, "Within-batch descriptive cell-type composition", rows,
                  axes=_axes("Saved batch", "category", f"Cell-type composition ({data['unit']})", data["unit"]), marks=marks, x_categories=batches,
                  annotations=[f"Denominator population: {data['denominator_kind']}; exact membership retained per segment.", "Stacked bars show only saved categories; unassigned/missing categories are not fabricated or renormalized."],
                  limitations=[CELL_LIMITATION, "Pooled batch composition is descriptive and cannot establish a donor-level condition effect.", PLOTTING_LIMITATION])]


def _classifier(capability, data):
    if data["score_unit"] not in ("confidence_score", "probability"):
        raise ValueError("Classifier confidence unit must be explicit confidence_score or probability")
    _text(data["classifier"], "classifier")
    _text(data["score_definition"], "score_definition")
    records = _records(data, "observations", ("id", "value", "group"))
    observations = [{"id": r["id"], "value": _number(r["value"], "classifier confidence", nullable=True, low=0, high=1),
                     "group": _text(r["group"], "saved classifier group"), "source": dict(r)} for r in records]
    edges, _ = _bins([o["value"] for o in observations], data["bins"])
    if any(edge < 0 or edge > 1 for edge in edges):
        raise ValueError("Classifier-confidence bins must respect the saved [0,1] score domain")
    return [_histogram(capability, "Saved classifier-confidence distribution", observations, edges=data["bins"], unit=data["score_unit"],
                       annotations=[f"Classifier: {data['classifier']}; score definition: {data['score_definition']}"],
                       limitations=["A confidence-score histogram does not establish calibration, classification accuracy or biological identity."])]


def _embedding_density(capability, data):
    _text(data["embedding_name"], "embedding_name")
    bins = data["bins"]
    if not isinstance(bins, list) or len(bins) != 2:
        raise ValueError("Density bins must be an explicit [x_count,y_count] grid")
    nx, ny = [_integer(v, "density bin count", low=1) for v in bins]
    if max(nx, ny) > 128:
        raise ValueError("Density grid is bounded to at most 128 bins per dimension")
    cells = _records(data, "cells", ("id", "x", "y", "group"))
    rows = []
    for c in cells:
        x, y = _number(c["x"], "embedding x", nullable=True), _number(c["y"], "embedding y", nullable=True)
        reason = "Saved embedding coordinate is missing." if x is None or y is None else None
        rows.append(row(c["id"], x if not reason else None, y if not reason else None, entity_kind="cell", group=_text(c["group"], "saved group"), details=dict(c), reason=reason))
    finite = [r for r in rows if r["x"] is not None and r["y"] is not None]
    if not finite:
        raise ValueError("No finite source embedding coordinates are available for density geometry")
    xmin, xmax = min(r["x"] for r in finite), max(r["x"] for r in finite)
    ymin, ymax = min(r["y"] for r in finite), max(r["y"] for r in finite)
    if xmin == xmax:
        xmin, xmax = xmin - .5, xmax + .5
    if ymin == ymax:
        ymin, ymax = ymin - .5, ymax + .5
    width, height = (xmax - xmin) / nx, (ymax - ymin) / ny
    grouped = defaultdict(list)
    for r in finite:
        i, j = min(nx - 1, int((r["x"] - xmin) / width)), min(ny - 1, int((r["y"] - ymin) / height))
        grouped[(i, j)].append(r)
    marks = [{"type": "rect", "x": xmin + i * width, "y": ymin + j * height, "width": width, "height": height,
              "value": len(members), "keys": [r["key"] for r in members], "group": "All-cell density"} for (i, j), members in sorted(grouped.items())]
    for group in dict.fromkeys(r["group"] for r in finite):
        members = [r for r in finite if r["group"] == group]
        marks.append({"type": "text", "x": sum(r["x"] for r in members) / len(members), "y": sum(r["y"] for r in members) / len(members),
                      "text": f"{group} (n={len(members)})", "anchor": "middle", "keys": [r["key"] for r in members], "group": group})
    return [panel(capability, f"{data['embedding_name']} — all-cell density and centroids", rows,
                  axes=_axes(f"{data['embedding_name']} 1", "embedding_coordinate", f"{data['embedding_name']} 2", "embedding_coordinate"), marks=marks,
                  color_scale=_color([len(v) for v in grouped.values()], "Cells per exact rectangular bin", "count"),
                  annotations=[f"Grid: {nx} × {ny}; x range [{xmin:g}, {xmax:g}]; y range [{ymin:g}, {ymax:g}]. All finite source points are binned exactly once; no sampling.",
                               "Labels are arithmetic coordinate centroids of the displayed saved groups (descriptive only). Every bin and centroid retains exact source selection keys."],
                  limitations=[CELL_LIMITATION, "Embedding-coordinate density and centroids depend on the saved embedding and are not biological distances or inferred cell types.", PLOTTING_LIMITATION])]


def _embedding_context(data):
    """Saved coordinates and method metadata, shared by actual embedding views."""
    method = data["method"]
    if method not in ("UMAP", "t-SNE"):
        raise ValueError("Saved embedding method must be exactly declared UMAP or t-SNE")
    representation = _text(data["representation"], "saved representation")
    configuration = data["configuration"]
    if not isinstance(configuration, dict) or not configuration or len(configuration) > 64:
        raise ValueError("Saved embedding configuration must be a nonempty bounded object")
    for key in configuration:
        _text(key, "configuration key")
    try:
        encoded = json.dumps(configuration, sort_keys=True, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError, OverflowError, RecursionError) as error:
        raise ValueError("Saved embedding configuration must contain finite JSON metadata") from error
    if len(encoded.encode()) > 3000:
        raise ValueError("Saved embedding configuration exceeds the bounded metadata budget")
    cells = _records(data, "cells", ("id", "x", "y", "group"), ("sample", "batch", "cell_type"))
    rows = []
    for index, cell in enumerate(cells):
        x = _number(cell["x"], "saved embedding x", nullable=True)
        y = _number(cell["y"], "saved embedding y", nullable=True)
        reason = "Saved embedding coordinate is missing; no coordinate is inferred." if x is None or y is None else None
        details = {**cell, "embedding_method": method, "representation": representation}
        if index == 0:
            details["source_configuration"] = configuration
        rows.append(row(cell["id"], x if not reason else None, y if not reason else None,
                        entity_kind="cell", group=_text(cell["group"], "saved embedding group"), details=details, reason=reason))
    axes = _axes(f"{method} 1", "embedding_coordinate", f"{method} 2", "embedding_coordinate")
    annotations = [f"Saved embedding method: {method}; source representation: {representation}.",
                   f"Saved embedding configuration: {encoded}",
                   "All coordinates and typed cell IDs are retained exactly. No embedding fit, alignment or coordinate rescaling is performed."]
    limitations = [CELL_LIMITATION, "Embedding distances, densities and separated groups do not by themselves establish biological relationships or cluster validity.", PLOTTING_LIMITATION]
    return rows, axes, annotations, limitations


def _saved_embedding(capability, data):
    rows, axes, annotations, limitations = _embedding_context(data)
    return [panel(capability, f"Saved {data['method']} embedding", rows, axes=axes,
                  marks=[_point(r) for r in rows if r["x"] is not None and r["y"] is not None],
                  annotations=annotations, limitations=limitations)]


def _axial_bin(x, y, radius, origin):
    """Nearest pointy-top axial hex center via deterministic cube rounding."""
    q = (math.sqrt(3) / 3 * (x - origin[0]) - (y - origin[1]) / 3) / radius
    r = (2 / 3 * (y - origin[1])) / radius
    if not math.isfinite(q) or not math.isfinite(r):
        raise ValueError("Hexbin radius/origin is numerically incompatible with saved coordinates")
    cube_y = -q - r
    rounded_q, rounded_y, rounded_r = round(q), round(cube_y), round(r)
    errors = (abs(rounded_q - q), abs(rounded_y - cube_y), abs(rounded_r - r))
    if errors[0] > errors[1] and errors[0] > errors[2]:
        rounded_q = -rounded_y - rounded_r
    elif errors[1] > errors[2]:
        rounded_y = -rounded_q - rounded_r
    else:
        rounded_r = -rounded_q - rounded_y
    return rounded_q, rounded_r


def _embedding_hexbin(capability, data):
    rows, axes, annotations, limitations = _embedding_context(data)
    radius = _number(data["hex_radius"], "hex_radius", low=0)
    if radius == 0:
        raise ValueError("Hexbin circumradius must be strictly positive")
    if not isinstance(data["origin"], list) or len(data["origin"]) != 2:
        raise ValueError("Hexbin origin must explicitly contain [x,y]")
    origin = [_number(v, "hexbin origin") for v in data["origin"]]
    grouped = defaultdict(list)
    for source_row in rows:
        source_row["details"].update({"hex_radius": radius, "hex_origin": origin, "hex_orientation": "pointy_top"})
        if source_row["x"] is None or source_row["y"] is None:
            source_row["details"]["hex_axial_bin"] = None
            continue
        q, r = _axial_bin(source_row["x"], source_row["y"], radius, origin)
        source_row["details"]["hex_axial_bin"] = [q, r]
        grouped[(q, r)].append(source_row)
    if not grouped:
        raise ValueError("No finite source coordinates are available for hexbin geometry")
    marks = []
    for (q, r), members in sorted(grouped.items()):
        center_x = origin[0] + radius * math.sqrt(3) * (q + r / 2)
        center_y = origin[1] + radius * 1.5 * r
        vertices = [[center_x + radius * math.cos(math.pi / 3 * k - math.pi / 6),
                     center_y + radius * math.sin(math.pi / 3 * k - math.pi / 6)] for k in range(6)]
        if any(not math.isfinite(v) for point in vertices for v in point):
            raise ValueError("Hexbin display geometry exceeds finite coordinate range")
        marks.append({"type": "polygon", "points": vertices, "keys": [r["key"] for r in members],
                      "group": "All-cell hexbin density", "value": len(members)})
        for source_row in members:
            source_row["details"]["hex_center"] = [center_x, center_y]
            source_row["details"]["hex_count"] = len(members)
    annotations.extend([f"Pointy-top axial hexbin; circumradius={radius:g} embedding-coordinate units; origin=({origin[0]:g}, {origin[1]:g}). Equal x/y display scale is required for regular hexagons.",
                        "Deterministic nearest-center cube rounding assigns every finite source cell exactly once. Polygon vertices are bin boundaries; source row coordinates remain the saved embedding.",
                        "Color is the exact cell count in each equal-area hexagon; empty hexagons are omitted, and no source sampling is performed."])
    return [panel(capability, f"Saved {data['method']} — true hexbin density", rows, axes=axes, marks=marks,
                  color_scale=_color([len(members) for members in grouped.values()], "Cells per equal-area hexagon", "count"),
                  annotations=annotations, limitations=limitations)]


def _contour_parameters(data):
    bandwidth, grid_size, levels = data["bandwidth"], data["grid_size"], data["levels"]
    if not isinstance(bandwidth, list) or len(bandwidth) != 2:
        raise ValueError("2D KDE bandwidth must explicitly contain [x_bandwidth,y_bandwidth]")
    bandwidth = [_number(v, "2D KDE bandwidth", low=0) for v in bandwidth]
    if min(bandwidth) == 0:
        raise ValueError("2D KDE bandwidths must be strictly positive")
    if not isinstance(grid_size, list) or len(grid_size) != 2:
        raise ValueError("2D KDE grid_size must explicitly contain [x_nodes,y_nodes]")
    grid_size = [_integer(v, "2D KDE grid nodes", low=16) for v in grid_size]
    if max(grid_size) > 128:
        raise ValueError("2D KDE grid is bounded to 16–128 nodes per dimension")
    if not isinstance(levels, list) or not 1 <= len(levels) <= 16:
        raise ValueError("Density levels must be an explicit bounded nonempty list")
    levels = [_number(v, "density contour level", low=0) for v in levels]
    if min(levels) == 0 or any(a >= b for a, b in zip(levels, levels[1:])):
        raise ValueError("Density contour levels must be positive and strictly increasing")
    bounds = data.get("bounds")
    if bounds is not None:
        if not isinstance(bounds, list) or len(bounds) != 4:
            raise ValueError("Density grid bounds must contain [xmin,xmax,ymin,ymax]")
        bounds = [_number(v, "density grid bounds") for v in bounds]
        if bounds[0] >= bounds[1] or bounds[2] >= bounds[3]:
            raise ValueError("Density grid bounds must be strictly ordered")
    return bandwidth, grid_size, levels, bounds


def _embedding_contours(capability, data):
    import numpy as np
    import contourpy

    rows, axes, annotations, limitations = _embedding_context(data)
    bandwidth, grid_size, levels, supplied_bounds = _contour_parameters(data)
    finite = [r for r in rows if r["x"] is not None and r["y"] is not None]
    if not finite:
        raise ValueError("No finite source embedding coordinates are available for density contours")
    points = np.asarray([[r["x"], r["y"]] for r in finite], dtype=float)
    minima, maxima = points.min(axis=0), points.max(axis=0)
    spans = maxima - minima
    if not np.isfinite(spans).all():
        raise ValueError("Density coordinate range exceeds finite display arithmetic")
    if supplied_bounds is None:
        bounds = [float(minima[0] - 3 * bandwidth[0]), float(maxima[0] + 3 * bandwidth[0]),
                  float(minima[1] - 3 * bandwidth[1]), float(maxima[1] + 3 * bandwidth[1])]
    else:
        bounds = supplied_bounds
    if not all(math.isfinite(v) for v in bounds):
        raise ValueError("Density grid extent exceeds finite coordinate range")
    if minima[0] < bounds[0] or maxima[0] > bounds[1] or minima[1] < bounds[2] or maxima[1] > bounds[3]:
        raise ValueError("Density grid bounds would clip saved source points")
    unavailable = None
    if len(finite) < 3 or np.any(spans == 0):
        unavailable = "Saved coordinate rank is below two; at least three non-collinear cells are needed for a two-dimensional density display."
    else:
        scaled = (points - minima) / spans
        if np.linalg.matrix_rank(scaled - scaled.mean(axis=0)) < 2:
            unavailable = "Saved coordinate rank is below two; collinear source coordinates cannot support a two-dimensional density display."
    for source_row in rows:
        source_row["details"].update({"density_bandwidth": bandwidth, "density_grid_size": grid_size,
                                      "density_grid_bounds": bounds, "density_levels": levels,
                                      "density_kernel": "product_gaussian_absolute_bandwidth",
                                      "density_unavailable_reason": unavailable})
    marks = [_point(r, radius=2) for r in finite]
    annotations.extend([f"Descriptive product-Gaussian KDE: absolute bandwidths ({bandwidth[0]:g}, {bandwidth[1]:g}) in embedding-coordinate units; grid={grid_size[0]} × {grid_size[1]}.",
                        f"Exact display grid bounds: {bounds}; density levels: {levels} inverse embedding-coordinate area.",
                        "Density uses every finite saved cell with weight 1/n. Contour paths are linear interpolation of the actual 2D KDE grid; no embedding coordinates are fitted or replaced.",
                        "The Gaussian density has unbounded tails outside the finite display grid; all source observations remain inside the grid and in the exact source table."])
    if unavailable:
        annotations.append(f"Density contours unavailable: {unavailable} Exact saved observation points are retained; no two-dimensional spread is invented.")
        return [panel(capability, f"Saved {data['method']} — density contours unavailable", rows, axes=axes, marks=marks,
                      annotations=annotations, limitations=limitations)]
    evaluations = len(finite) * grid_size[0] * grid_size[1]
    if evaluations > 20_000_000:
        raise ValueError("Exact KDE exceeds the explicit 20000000 evaluation display budget; use all-point hexbin/rectangular bins or a smaller grid, not sampling")
    xs = np.linspace(bounds[0], bounds[1], grid_size[0])
    ys = np.linspace(bounds[2], bounds[3], grid_size[1])
    density = np.zeros((grid_size[1], grid_size[0]), dtype=float)
    for start in range(0, len(finite), 256):
        chunk = points[start:start + 256]
        kx = np.exp(-.5 * ((xs[:, None] - chunk[None, :, 0]) / bandwidth[0]) ** 2)
        ky = np.exp(-.5 * ((ys[:, None] - chunk[None, :, 1]) / bandwidth[1]) ** 2)
        density += ky @ kx.T
    density /= len(finite) * 2 * math.pi * bandwidth[0] * bandwidth[1]
    if not np.isfinite(density).all():
        raise ValueError("Supplied bandwidth produces nonfinite 2D density; no contour can be substituted")
    generator = contourpy.contour_generator(x=xs, y=ys, z=density, line_type="Separate", name="serial")
    keys = [r["key"] for r in finite]
    line_count = 0
    for level in levels:
        paths = generator.lines(level)
        emitted = 0
        for path in paths:
            if len(path) < 2:
                continue
            mark = line_mark(path.tolist(), keys=keys, group=f"KDE density {level:g}", style={"stroke_width": 1.5, "opacity": .9})
            mark["value"] = level
            marks.append(mark)
            line_count += 1
            emitted += 1
        if not emitted:
            annotations.append(f"Requested density level {level:g} does not intersect the saved-coordinate KDE grid; no path is invented at that level.")
    if not line_count:
        raise ValueError("None of the requested density contour levels intersects the actual KDE grid")
    rows[0]["details"]["density_grid_value_range"] = [float(density.min()), float(density.max())]
    rows[0]["details"]["density_grid_evaluations"] = evaluations
    return [panel(capability, f"Saved {data['method']} — actual 2D density contours", rows, axes=axes, marks=marks,
                  color_scale=_color(levels, "KDE contour probability density", "1/embedding_coordinate_squared"),
                  annotations=annotations, limitations=[*limitations, "These are descriptive density contours, not confidence regions or hypothesis-test boundaries."])]


_AMBIENT_LIMITATION = "Saved contamination estimates are estimator outputs, not independently measured contamination truth. These plots do not establish estimator calibration, correction benefit or downstream biological validity."


def _saved_estimate_reason(record):
    if record["status"] not in ("recorded", "failed", "missing"):
        raise ValueError("Saved estimator status must be recorded, failed or missing")
    reason = record.get("reason")
    if record["status"] in ("failed", "missing"):
        return _text(reason, "reason for saved failed/missing estimate")
    if reason is not None:
        _text(reason, "saved estimator reason")
    return None


def _ambient_value(value, unit, *, failed=False):
    if failed:
        return _number(value, "failed saved estimator value", nullable=True)
    if unit == "fraction":
        return _number(value, "saved contamination fraction", nullable=True, low=0, high=1)
    if unit == "percent":
        return _number(value, "saved contamination percent", nullable=True, low=0, high=100)
    return _number(value, "saved ambient-RNA score", nullable=True)


def _ambient_profile(capability, profile):
    require_fields(profile, ("genes", "unit", "quantity_definition", "source_population"))
    if profile["unit"] not in ("fraction", "percent", "count", "normalized_expression", "score"):
        raise ValueError("Saved ambient-gene profile requires explicit fraction/percent/count/expression/score units")
    for field in ("quantity_definition", "source_population"):
        _text(profile[field], field)
    genes = _records(profile, "genes", ("id", "value", "status"), ("reason",))
    rows, marks = [], []
    for index, gene in enumerate(genes):
        reason = _saved_estimate_reason(gene)
        value = gene["value"]
        if reason:
            _number(value, "saved failed ambient-profile value", nullable=True)
            shown = None
        elif profile["unit"] == "count":
            shown = _raw_expression_value(value, "counts")
        elif profile["unit"] == "normalized_expression":
            shown = _expression_value(value, "normalized_expression")
        else:
            shown = _ambient_value(value, profile["unit"])
        reason = reason or ("Saved ambient-gene profile value is missing." if shown is None else None)
        r = row(gene["id"], index if not reason else None, shown if not reason else None, entity_kind="gene", group="Saved ambient profile",
                details={**gene, "saved_profile_value": value, "unit": profile["unit"], "quantity_definition": profile["quantity_definition"],
                         "source_population": profile["source_population"]}, reason=reason)
        rows.append(r)
        if not reason:
            marks.extend([_segment(index, 0, index, shown, [r["key"]], "Saved ambient profile"), _point(r, radius=4)])
    return panel(capability, "Actual saved ambient-gene profile", rows,
                 axes=_axes("Saved profile gene", "gene", profile["quantity_definition"], profile["unit"]), marks=marks,
                 x_categories=[g["id"] for g in genes],
                 annotations=[f"Saved source population: {profile['source_population']}.", "Only actual saved profile quantities are shown. No profile, marker list, background or contamination estimate is inferred."],
                 limitations=[_AMBIENT_LIMITATION, PLOTTING_LIMITATION], panel_id=f"{capability}-gene-profile")


def _ambient_correction(capability, correction, observations, entity_kind):
    require_fields(correction, ("values", "before_layer", "after_layer", "before_scale", "after_scale", "before_unit", "after_unit", "method"))
    scales = {k: v for k, v in _SCALES.items() if k != "module_score"}
    scales["estimated_counts"] = "estimated_count"
    for stage in ("before", "after"):
        _text(correction[f"{stage}_layer"], f"{stage}_layer")
        scale = _text(correction[f"{stage}_scale"], f"{stage}_scale")
        if scale not in scales or correction[f"{stage}_unit"] != scales[scale]:
            raise ValueError("Saved pre/post-correction source units are incompatible with their declared scales")
    if correction["before_scale"] != correction["after_scale"] and (correction["before_scale"], correction["after_scale"]) != ("counts", "estimated_counts"):
        raise ValueError("Saved pre/post-correction values use incomparable scales; the adapter does not normalize or transform them")
    _text(correction["method"], "saved correction method")
    values = _records(correction, "values", ("id", "observation_id", "gene_id", "before", "after", "status"), ("reason",))
    _unique_values([(v["observation_id"], v["gene_id"]) for v in values], "saved observation/gene correction pairs")
    lookup = {o["id"]: o for o in observations}
    rows, marks = [], []
    for value in values:
        if value["observation_id"] not in lookup:
            raise ValueError("Saved correction observation identity is not bound to the source diagnostic observations")
        _text(value["gene_id"], "saved correction gene_id")
        reason = _saved_estimate_reason(value)
        coordinates = []
        for stage in ("before", "after"):
            raw = value[stage]
            if reason:
                coordinates.append(_number(raw, "failed saved correction value", nullable=True))
            elif correction[f"{stage}_scale"] == "estimated_counts":
                coordinates.append(_number(raw, "saved corrected count estimate", nullable=True, low=0))
            else:
                coordinates.append(_raw_expression_value(raw, correction[f"{stage}_scale"]))
        reason = reason or ("Saved pre/post-correction value is missing; no value is substituted." if None in coordinates else None)
        source_observation = lookup[value["observation_id"]]
        r = row(value["id"], coordinates[0] if not reason else None, coordinates[1] if not reason else None,
                entity_kind=entity_kind, group=value["gene_id"], reason=reason, link=source_observation["link"],
                details={**value, "before_layer": correction["before_layer"], "after_layer": correction["after_layer"],
                         "before_scale": correction["before_scale"], "after_scale": correction["after_scale"],
                         "before_unit": correction["before_unit"], "after_unit": correction["after_unit"],
                         "correction_method": correction["method"], "gene_link": {"kind": "gene", "id": value["gene_id"]}})
        rows.append(r)
        if not reason:
            marks.append(_point(r, radius=4))
    available = [r for r in rows if r["x"] is not None and r["y"] is not None]
    if available:
        minimum = min(min(r["x"], r["y"]) for r in available)
        maximum = max(max(r["x"], r["y"]) for r in available)
        marks.append(_segment(minimum, minimum, maximum, maximum, [r["key"] for r in available], "Numeric equality", dash=[4, 3]))
    return panel(capability, "Actual saved pre/post ambient-correction values", rows,
                 axes=_axes(f"Before: {correction['before_layer']}", correction["before_unit"], f"After: {correction['after_layer']}", correction["after_unit"]),
                 marks=marks, annotations=[f"Saved correction method: {correction['method']}.",
                                          "Each point joins the same explicit observation and gene. The diagonal denotes numeric equality, not correction accuracy.",
                                          "Observed integer counts and estimated corrected counts retain distinct declared source scales; no decontamination or normalization is run."],
                 limitations=[_AMBIENT_LIMITATION, PLOTTING_LIMITATION], panel_id=f"{capability}-correction")


def _ambient_diagnostics(capability, data):
    if data["entity_kind"] not in ("cell", "sample"):
        raise ValueError("Ambient-RNA diagnostic entity_kind must explicitly be cell or sample")
    if data["group_by"] not in ("sample_id", "batch"):
        raise ValueError("Ambient-RNA summaries must use saved sample_id or batch membership")
    if data["measurement"] == "estimated_contamination_fraction":
        if data["unit"] not in ("fraction", "percent"):
            raise ValueError("Estimated contamination fractions require fraction or percent units")
        label = "Saved estimated ambient-RNA contamination"
    elif data["measurement"] == "ambient_rna_score":
        if data["unit"] != "score":
            raise ValueError("Ambient-RNA scores cannot be labeled as contamination fractions or expression")
        label = "Saved ambient-RNA score"
    else:
        raise ValueError("Unknown ambient-RNA measurement definition")
    _text(data["method"], "saved ambient-RNA estimator method")
    _text(data["measurement_definition"], "saved ambient-RNA measurement definition")
    source = _records(data, "observations", ("id", "sample_id", "batch", "value", "status"), ("reason", "total_counts"))
    if data["entity_kind"] == "sample":
        require_unique([s["sample_id"] for s in source], "sample-level ambient-RNA identities")
    observations = []
    for s in source:
        _text(s["sample_id"], "ambient-RNA sample_id")
        _text(s["batch"], "ambient-RNA batch")
        reason = _saved_estimate_reason(s)
        saved_value = _ambient_value(s["value"], data["unit"], failed=reason is not None)
        value = None if reason else saved_value
        reason = reason or ("Saved ambient-RNA estimator value is missing." if value is None else None)
        if "total_counts" in s:
            _raw_expression_value(s["total_counts"], "counts")
        observations.append({"id": s["id"], "value": value, "group": s[data["group_by"]], "reason": reason,
                             "link": {"kind": data["entity_kind"], "id": s["id"] if data["entity_kind"] == "cell" else s["sample_id"]},
                             "source": {**s, "saved_estimator_value": saved_value, "source_status": s["status"],
                                        "estimator_method": data["method"], "measurement": data["measurement"],
                                        "source_unit": data["unit"], "measurement_definition": data["measurement_definition"]}})
    references = []
    cutoffs = data.get("cutoffs", [])
    if not isinstance(cutoffs, list) or len(cutoffs) > 100:
        raise ValueError("Ambient-RNA cutoffs must be a bounded list of actually saved threshold records")
    for cutoff in cutoffs:
        require_fields(cutoff, ("value", "label", "source"))
        _text(cutoff["label"], "cutoff label")
        _text(cutoff["source"], "cutoff source")
        value = _ambient_value(cutoff["value"], data["unit"])
        if value is None:
            raise ValueError("Saved ambient-RNA cutoff must be finite")
        references.append({"axis": "x", "value": value, "label": f"{cutoff['label']} ({cutoff['source']})"})
    bandwidth, grid_size = _density_options(data)
    n_valid = sum(o["reason"] is None for o in observations)
    annotations = [f"Saved estimator method: {data['method']}; quantity definition: {data['measurement_definition']}.",
                   f"Source observations={len(observations)}; available estimates={n_valid}; failed/missing={len(observations) - n_valid}.",
                   "Thresholds are shown only when explicitly saved with their source. No estimator, decontamination, normalization or threshold selection is run."]
    histogram = _histogram(capability, f"{label} histogram", observations, edges=data["bins"], unit=data["unit"],
                           entity_kind=data["entity_kind"], references=references, annotations=annotations,
                           limitations=[_AMBIENT_LIMITATION], panel_id=f"{capability}-histogram")
    violin = _distribution(capability, f"{label} — by saved {data['group_by']}", observations, kind="violin", unit=data["unit"],
                           entity_kind=data["entity_kind"], bandwidth=bandwidth, grid_size=grid_size,
                           references=[{**r, "axis": "y"} for r in references], annotations=annotations, panel_id=f"{capability}-group-distribution")
    violin["limitations"].append(_AMBIENT_LIMITATION)
    panels = [histogram, violin]
    if any("total_counts" in s for s in source):
        rows, marks = [], []
        for o in observations:
            depth = o["source"].get("total_counts")
            reason = o["reason"] or ("Saved library depth is missing for this observation." if depth is None else None)
            r = row(o["id"], depth if not reason else None, o["value"] if not reason else None,
                    entity_kind=data["entity_kind"], group=o["group"], details=o["source"], reason=reason, link=o["link"])
            rows.append(r)
            if not reason:
                marks.append(_point(r))
        panels.append(panel(capability, f"{label} versus actual saved library depth", rows,
                            axes=_axes("Saved library depth", "count", label, data["unit"]), marks=marks,
                            reference_lines=[{**r, "axis": "y"} for r in references], annotations=annotations,
                            limitations=[CELL_LIMITATION, _AMBIENT_LIMITATION], panel_id=f"{capability}-depth"))
    if "embedding" in data:
        embedding = data["embedding"]
        require_fields(embedding, ("method", "representation", "configuration", "coordinates"))
        coordinates = _records(embedding, "coordinates", ("id", "x", "y"))
        source_ids = {o["id"] for o in observations}
        if any(c["id"] not in source_ids for c in coordinates):
            raise ValueError("Embedding coordinate identity is not in the source ambient-RNA observations")
        lookup = {c["id"]: c for c in coordinates}
        cells = [{"id": o["id"], "x": lookup.get(o["id"], {}).get("x"), "y": lookup.get(o["id"], {}).get("y"), "group": o["group"]} for o in observations]
        template_rows, axes, embedding_notes, limitations = _embedding_context({"cells": cells, **{k: embedding[k] for k in ("method", "representation", "configuration")}})
        rows, marks = [], []
        for template, o in zip(template_rows, observations):
            reason = o["reason"] or template["reason"]
            r = row(o["id"], template["x"] if not reason else None, template["y"] if not reason else None,
                    entity_kind=data["entity_kind"], group=o["group"], reason=reason, link=o["link"],
                    details={**template["details"], **o["source"]})
            rows.append(r)
            if not reason:
                marks.append(_point(r, value=o["value"]))
        panels.append(panel(capability, f"Saved {embedding['method']} — ambient-RNA estimator overlay", rows, axes=axes, marks=marks,
                            color_scale=_color([o["value"] for o in observations], label, data["unit"]),
                            annotations=[*annotations, *embedding_notes], limitations=[*limitations, _AMBIENT_LIMITATION], panel_id=f"{capability}-embedding"))
    if "ambient_gene_profile" in data:
        panels.append(_ambient_profile(capability, data["ambient_gene_profile"]))
    if "correction" in data:
        panels.append(_ambient_correction(capability, data["correction"], observations, data["entity_kind"]))
    return panels


def build_panels(capability, data):
    """Validate one saved-data packet and return its actual linked geometry."""
    if _text(capability, "capability") not in CAPABILITIES:
        raise ValueError(f"Unknown expression plotting capability: {capability}")
    if not isinstance(data, dict):
        raise ValueError("Scientific plot data must be an object")
    spec = CAPABILITIES[capability]
    require_fields(data, spec["required"], spec["optional"])
    if capability.startswith("qc_"):
        return _qc(capability, data)
    if capability in ("depth_histogram", "depth_ecdf", "batch_boxplot"):
        return _depth(capability, data)
    if capability in ("expression_violins", "expression_boxplots", "expression_ridges"):
        return _expression_distributions(capability, data)
    builders = {"doublet_diagnostics": _doublet, "hvg_scatter": _hvg, "scrna_pca_scree": _scree,
                "integration_diagnostics": _integration, "feature_expression": _feature, "marker_dotplot": _marker,
                "expression_heatmap": _heatmap, "module_heatmap": _heatmap, "donor_composition": _donor_composition,
                "batch_composition": _batch_composition, "classifier_confidence_histogram": _classifier,
                "embedding_density_centroids": _embedding_density, "saved_embedding": _saved_embedding,
                "embedding_hexbin": _embedding_hexbin, "embedding_density_contours": _embedding_contours,
                "ambient_rna_diagnostics": _ambient_diagnostics}
    return builders[capability](capability, data)
