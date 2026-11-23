"""Typed, source-linked scientific drawing records and descriptive plot calculations.

These helpers never fit an inferential model or create missing scientific results.
Distribution summaries, display transforms and declared clustering are recorded
descriptive calculations. Input custody remains with the artifact reader.
"""
from __future__ import annotations

from copy import deepcopy
import json
import math
import numbers
import re

MAX_ROWS = 200_000
MAX_MARKS = 1_000_000
MAX_MATRIX_VALUES = 1_000_000
MAX_BYTES = 64 * 1024**2
MAX_IMAGE_PIXELS = 16_000_000
_ID = re.compile(r"[A-Za-z][A-Za-z0-9_.:-]{0,159}")
_COLOR = re.compile(r"#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})")
_MARKS = {"point", "line", "arrow", "polyline", "polygon", "rect", "ellipse", "arc", "text", "image"}


def finite_number(value, *, allow_none=False):
    if value is None and allow_none:
        return None
    if isinstance(value, bool) or not isinstance(value, numbers.Real) or not math.isfinite(value):
        raise ValueError("plot_requires_finite_number")
    return float(value)


def _text(value):
    return isinstance(value, str) and 0 < len(value) <= 4000 and "\x00" not in value


def require_unique(values, label):
    if not isinstance(values, (list, tuple)) or not all(_text(v) for v in values) or len(set(values)) != len(values):
        raise ValueError("plot_requires_unique_" + label)
    return values


def require_fields(data, required, optional=()):
    if not isinstance(data, dict) or not set(required) <= set(data) or set(data) - set(required) - set(optional):
        raise ValueError("plot_input_fields_mismatch: required=" + ",".join(required))
    return data


def _json_check(value):
    try:
        body = json.dumps(value, allow_nan=False, ensure_ascii=False)
    except (TypeError, ValueError, OverflowError, RecursionError) as error:
        raise ValueError("plot_requires_finite_JSON") from error
    if len(body.encode()) > MAX_BYTES:
        raise ValueError("plot_exceeds_explicit_byte_budget")


def row(identity, x=None, y=None, *, entity_kind="gene", group="", details=None,
        warnings=(), reason=None, link=None, key=None):
    if not _text(identity) or not _text(entity_kind) or not isinstance(group, str):
        raise ValueError("plot_row_identity_and_unit_required")
    x, y = finite_number(x, allow_none=True), finite_number(y, allow_none=True)
    absent = x is None or y is None or reason is not None
    if absent and reason is None:
        reason = "coordinate_unavailable"
    if link is None and entity_kind not in {"bin", "group", "component", "annotation", "resource", "edge"}:
        link = {"kind": entity_kind, "id": identity}
    value = {"id": identity, "key": key or identity, "entity_kind": entity_kind,
        "x": x, "y": y, "group": group, "status": "excluded" if absent else "recorded",
        "reason": reason, "warnings": list(warnings), "details": deepcopy(details or {}), "link": deepcopy(link)}
    _json_check(value)
    return value


def point_marks(rows, *, radius=3, color_by="group"):
    marks = []
    for value in rows:
        if value["x"] is None or value["y"] is None:
            continue
        mark = {"type": "point", "keys": [value["key"]], "group": value.get(color_by, ""),
                "x": value["x"], "y": value["y"], "radius": radius}
        if value.get("warnings"):
            mark["shape"] = "triangle"
        marks.append(mark)
    return marks


def line_mark(points, *, keys=(), group="", style=None, kind="polyline"):
    return {"type": kind, "keys": list(keys), "group": group,
            "points": [[finite_number(x), finite_number(y)] for x, y in points], "style": dict(style or {})}


def _validate_mark(mark, keys):
    if not isinstance(mark, dict) or mark.get("type") not in _MARKS:
        raise ValueError("plot_mark_type_unknown")
    if mark["type"] == "image":
        from .scientific_plot_images import validate_image_mark
        validate_image_mark(mark)
        return
    allowed = {"type", "keys", "group", "style", "x", "y", "x1", "y1", "x2", "y2",
               "width", "height", "rx", "ry", "radius", "points", "text", "anchor", "shape", "value", "size_value"}
    if set(mark) - allowed or not isinstance(mark.get("keys"), list) or any(key not in keys for key in mark["keys"]):
        raise ValueError("plot_mark_source_key_invalid")
    if not isinstance(mark.get("group"), str):
        raise ValueError("plot_mark_group_required")
    kind = mark["type"]
    fields = {"point": ("x", "y", "radius"), "line": ("x1", "y1", "x2", "y2"),
              "arrow": ("x1", "y1", "x2", "y2"), "rect": ("x", "y", "width", "height"),
              "ellipse": ("x", "y", "rx", "ry"), "text": ("x", "y")}.get(kind, ())
    for name in fields:
        if name not in mark:
            raise ValueError("plot_mark_geometry_missing")
        finite_number(mark[name])
    if any(mark.get(key, 0) < 0 for key in ("width", "height", "rx", "ry", "radius", "size_value")):
        raise ValueError("plot_mark_size_negative")
    for key in ("value", "size_value"):
        if key in mark:
            finite_number(mark[key], allow_none=key == "value")
    if kind in {"polyline", "polygon", "arc"}:
        points = mark.get("points")
        minimum = 3 if kind in {"polygon", "arc"} else 2
        if not isinstance(points, list) or not minimum <= len(points) <= 200000 or (kind == "arc" and len(points) not in {3, 4}):
            raise ValueError("plot_mark_path_invalid")
        for point in points:
            if not isinstance(point, (list, tuple)) or len(point) != 2:
                raise ValueError("plot_mark_path_invalid")
            for number in point:
                finite_number(number)
    if kind == "text" and not _text(mark.get("text")):
        raise ValueError("plot_mark_label_required")
    if mark.get("anchor", "start") not in {"start", "middle", "end"} or mark.get("shape", "circle") not in {"circle", "triangle", "square", "diamond"}:
        raise ValueError("plot_mark_shape_or_anchor_invalid")
    style = mark.get("style", {})
    if not isinstance(style, dict) or set(style) - {"fill", "stroke", "stroke_width", "opacity", "dash"}:
        raise ValueError("plot_mark_style_invalid")
    for key in ("fill", "stroke"):
        if key in style and style[key] != "none" and (not isinstance(style[key], str) or not _COLOR.fullmatch(style[key])):
            raise ValueError("plot_mark_color_invalid")
    if "opacity" in style and not 0 <= finite_number(style["opacity"]) <= 1:
        raise ValueError("plot_mark_opacity_invalid")
    if "stroke_width" in style and not 0 <= finite_number(style["stroke_width"]) <= 20:
        raise ValueError("plot_mark_stroke_invalid")
    if "dash" in style and (not isinstance(style["dash"], list) or len(style["dash"]) > 8 or any(finite_number(v) < 0 for v in style["dash"])):
        raise ValueError("plot_mark_dash_invalid")


def validate_panel(value):
    fields = {"id", "capability", "title", "entity_kind", "axes", "rows", "marks", "x_categories", "y_categories",
              "color_scale", "size_scale", "reference_lines", "annotations", "availability", "coverage", "limitations"}
    if not isinstance(value, dict) or set(value) != fields or not _ID.fullmatch(value.get("id", "")) or not _ID.fullmatch(value.get("capability", "")):
        raise ValueError("plot_panel_fields_or_identity_invalid")
    if not _text(value["title"]) or not _text(value["entity_kind"]):
        raise ValueError("plot_panel_meaning_required")
    if set(value["axes"]) != {"x", "y"}:
        raise ValueError("plot_axes_required")
    for axis in value["axes"].values():
        if not isinstance(axis, dict) or set(axis) != {"label", "unit", "transform"} or not all(_text(v) for v in axis.values()):
            raise ValueError("plot_axis_label_unit_transform_required")
    rows, marks = value["rows"], value["marks"]
    if not isinstance(rows, list) or len(rows) > MAX_ROWS or not isinstance(marks, list) or len(marks) > MAX_MARKS:
        raise ValueError("plot_explicit_row_or_mark_budget_exceeded")
    keys = set()
    row_fields = {"id", "key", "entity_kind", "x", "y", "group", "status", "reason", "warnings", "details", "link"}
    for item in rows:
        if not isinstance(item, dict) or set(item) != row_fields or not all(_text(item[k]) for k in ("id", "key", "entity_kind")) or item["key"] in keys:
            raise ValueError("plot_row_key_or_identity_invalid")
        keys.add(item["key"])
        for key in ("x", "y"):
            finite_number(item[key], allow_none=True)
        if not isinstance(item["group"], str) or not isinstance(item["details"], dict) or not isinstance(item["warnings"], list) or not all(_text(x) for x in item["warnings"]):
            raise ValueError("plot_row_details_invalid")
        if item["status"] == "recorded":
            if item["x"] is None or item["y"] is None or item["reason"] is not None:
                raise ValueError("plot_recorded_row_has_missing_geometry")
        elif item["status"] != "excluded" or not _text(item["reason"]):
            raise ValueError("plot_excluded_row_requires_reason")
        link = item["link"]
        if link is not None and (not isinstance(link, dict) or set(link) != {"kind", "id"} or not all(_text(x) for x in link.values())):
            raise ValueError("plot_typed_link_invalid")
    for mark in marks:
        _validate_mark(mark, keys)
        if mark["type"] == "image" and any(mark["image"]["coordinate_unit"] != value["axes"][axis]["unit"] for axis in ("x", "y")):
            raise ValueError("plot_image_axis_coordinate_unit_mismatch")
    for name in ("x_categories", "y_categories", "annotations", "limitations"):
        if not isinstance(value[name], list) or not all(_text(v) for v in value[name]):
            raise ValueError("plot_labels_or_limitations_invalid")
    scale = value["color_scale"]
    if scale is not None:
        if not isinstance(scale, dict) or set(scale) != {"label", "unit", "palette", "domain", "center"} or not all(_text(scale[k]) for k in ("label", "unit", "palette")):
            raise ValueError("plot_color_scale_invalid")
        if scale["palette"] not in {"viridis", "magma", "RdBu_r", "coolwarm", "diverging", "OkabeIto", "okabe_ito"}:
            raise ValueError("plot_palette_unsupported")
        domain = scale["domain"]
        if not isinstance(domain, list) or len(domain) != 2 or finite_number(domain[0]) > finite_number(domain[1]):
            raise ValueError("plot_color_domain_invalid")
        if scale["center"] is not None and (scale["center"] != 0 or not math.isclose(-domain[0], domain[1], rel_tol=1e-10, abs_tol=1e-12)):
            raise ValueError("plot_diverging_scale_must_be_symmetric_at_zero")
        for mark in marks:
            if mark.get("value") is not None and not domain[0] <= mark["value"] <= domain[1]:
                raise ValueError("plot_color_value_outside_declared_domain")
    scale = value["size_scale"]
    if scale is not None:
        if not isinstance(scale, dict) or set(scale) != {"label", "unit", "domain", "max_radius"} or not _text(scale["label"]) or not _text(scale["unit"]):
            raise ValueError("plot_size_scale_invalid")
        if not isinstance(scale["domain"], list) or len(scale["domain"]) != 2 or scale["domain"][0] != 0 or finite_number(scale["domain"][1]) <= 0 or not 0 < finite_number(scale["max_radius"]) <= 100:
            raise ValueError("plot_size_area_scale_invalid")
        if any(mark.get("size_value", 0) > scale["domain"][1] for mark in marks):
            raise ValueError("plot_size_value_outside_declared_domain")
    for line in value["reference_lines"]:
        if not isinstance(line, dict) or set(line) != {"axis", "value", "label"} or line["axis"] not in {"x", "y"} or not _text(line["label"]):
            raise ValueError("plot_reference_line_invalid")
        finite_number(line["value"])
    availability = value["availability"]
    if availability != {"status": "available", "reason": None}:
        if (not rows or any(not mark.get("context", False) for mark in marks) or any(r["x"] is not None and r["y"] is not None for r in rows)
                or not isinstance(availability, dict) or set(availability) != {"status", "reason"}
                or availability["status"] != "unavailable" or not _text(availability["reason"])):
            raise ValueError("plot_unavailable_state_requires_retained_unplottable_rows")
    expected = {"source_rows": len(rows), "coordinate_rows": sum(r["x"] is not None and r["y"] is not None for r in rows),
                "missing_rows": sum(r["status"] == "excluded" for r in rows), "mark_count": len(marks), "truncated": False}
    if value["coverage"] != expected:
        raise ValueError("plot_source_coverage_mismatch")
    _json_check(value)
    return value


def panel(capability, title, rows, *, axes, marks=None, x_categories=None, y_categories=None,
          color_scale=None, size_scale=None, reference_lines=(), annotations=(), limitations=(), panel_id=None):
    rows = list(rows)
    kinds = {r["entity_kind"] for r in rows}
    marks = point_marks(rows) if marks is None else list(marks)
    value = {"id": panel_id or capability, "capability": capability, "title": title,
        "entity_kind": next(iter(kinds)) if len(kinds) == 1 else "mixed", "axes": deepcopy(axes),
        "rows": rows, "marks": marks, "x_categories": list(x_categories or []), "y_categories": list(y_categories or []),
        "color_scale": deepcopy(color_scale), "size_scale": deepcopy(size_scale), "reference_lines": list(reference_lines),
        "annotations": list(annotations), "availability": {"status": "available", "reason": None} if any(not mark.get("context", False) for mark in marks) else
            {"status": "unavailable", "reason": "No plottable saved coordinates; retained row reasons explain each missing value."},
        "coverage": {"source_rows": len(rows), "coordinate_rows": sum(r["x"] is not None and r["y"] is not None for r in rows),
                     "missing_rows": sum(r["status"] == "excluded" for r in rows), "mark_count": len(marks), "truncated": False},
        "limitations": list(limitations)}
    return validate_panel(value)


def validate_workspace(value):
    if not isinstance(value, dict) or set(value) != {"format", "source", "title", "panels", "entities", "limitations"} or value["format"] != "devbench.scientific-plot-workspace/1":
        raise ValueError("scientific_plot_workspace_fields_invalid")
    source = value["source"]
    if not isinstance(source, dict) or not {"sha256", "run_id", "kind"} <= set(source) or not re.fullmatch(r"[0-9a-f]{64}", source["sha256"]) or not _text(source["run_id"]) or source["kind"] not in {"synthetic", "retained"}:
        raise ValueError("scientific_plot_source_binding_required")
    if not _text(value["title"]) or not isinstance(value["panels"], list) or not 1 <= len(value["panels"]) <= 128:
        raise ValueError("scientific_plot_panel_budget_invalid")
    image_sizes, image_count = {}, 0
    for prepared in value["panels"]:
        for mark in prepared.get("marks", []) if isinstance(prepared, dict) else []:
            if isinstance(mark, dict) and mark.get("type") == "image":
                image_count += 1
                image = mark.get("image", {})
                if not isinstance(image, dict) or any(type(image.get(key)) is not int or image[key] <= 0 for key in ("width", "height")):
                    raise ValueError("scientific_plot_image_dimensions_invalid")
                image_sizes[image.get("sha256")] = image["width"] * image["height"]
    if image_count > 256 or sum(image_sizes.values()) > MAX_IMAGE_PIXELS:
        raise ValueError("scientific_plot_aggregate_image_budget_exceeded")
    require_unique([p["id"] for p in value["panels"]], "panel_ids")
    for item in value["panels"]:
        validate_panel(item)
    seen = set()
    for entity in value["entities"]:
        if not isinstance(entity, dict) or set(entity) != {"kind", "id", "label", "details", "warnings"} or not all(_text(entity[k]) for k in ("kind", "id", "label")):
            raise ValueError("scientific_plot_entity_invalid")
        key = (entity["kind"], entity["id"])
        if key in seen:
            raise ValueError("scientific_plot_duplicate_entity")
        seen.add(key)
    if not isinstance(value["limitations"], list) or not all(_text(x) for x in value["limitations"]):
        raise ValueError("scientific_plot_limitations_invalid")
    _json_check(value)
    return value


def distribution_summary(values, *, bandwidth=None, grid_size=64):
    import numpy as np
    numbers_ = np.asarray([finite_number(v) for v in values], dtype=float)
    if numbers_.size == 0 or numbers_.size > MAX_ROWS or type(grid_size) is not int or not 16 <= grid_size <= 512:
        raise ValueError("distribution_requires_bounded_observations_and_grid")
    q1, median, q3 = (float(x) for x in np.quantile(numbers_, [.25, .5, .75], method="linear"))
    iqr = q3 - q1
    inliers = numbers_[(numbers_ >= q1 - 1.5 * iqr) & (numbers_ <= q3 + 1.5 * iqr)]
    result = {"n": int(numbers_.size), "q1": q1, "median": median, "q3": q3,
        "whisker_low": float(inliers.min()), "whisker_high": float(inliers.max()),
        "outliers": [float(x) for x in numbers_ if x < q1 - 1.5 * iqr or x > q3 + 1.5 * iqr],
        "density": [], "bandwidth": None, "bandwidth_method": "supplied" if bandwidth is not None else "silverman_descriptive_rule",
        "density_unavailable_reason": None}
    if bandwidth is not None and finite_number(bandwidth) <= 0:
        raise ValueError("density_bandwidth_must_be_positive")
    if numbers_.size < 2 or float(numbers_.max()) == float(numbers_.min()):
        result["density_unavailable_reason"] = "insufficient_variation_for_density"
        return result
    deviation = float(np.std(numbers_, ddof=1))
    bandwidth = float(bandwidth) if bandwidth is not None else 1.06 * deviation * numbers_.size ** (-.2)
    if not math.isfinite(bandwidth) or bandwidth <= 0:
        result["density_unavailable_reason"] = "density_bandwidth_unavailable"
        return result
    grid = np.linspace(numbers_.min() - 3 * bandwidth, numbers_.max() + 3 * bandwidth, grid_size)
    density = np.zeros(grid_size)
    for start in range(0, numbers_.size, 4096):
        difference = (grid[:, None] - numbers_[None, start:start + 4096]) / bandwidth
        density += np.exp(-.5 * difference**2).sum(axis=1)
    density /= numbers_.size * bandwidth * math.sqrt(2 * math.pi)
    result.update(density=[[float(x), float(y)] for x, y in zip(grid, density)], bandwidth=bandwidth)
    return result


def matrix_panel(capability, title, values, row_ids, column_ids, *, unit, transform="identity",
                 row_metadata=None, column_metadata=None, cluster=False, diverging=False, details=None):
    import numpy as np
    require_unique(row_ids, "matrix_rows"); require_unique(column_ids, "matrix_columns")
    if not row_ids or not column_ids or len(row_ids) * len(column_ids) > MAX_MATRIX_VALUES:
        raise ValueError("matrix_exceeds_declared_plot_budget")
    if not isinstance(values, (list, tuple)) or len(values) != len(row_ids) or any(len(r) != len(column_ids) for r in values):
        raise ValueError("matrix_identity_shape_mismatch")
    if not _text(unit):
        raise ValueError("matrix_measurement_unit_required")
    aliases = {"natural_log1p": "log1p", "zscore_row": "row_zscore"}
    transform = aliases.get(transform, transform)
    if transform not in {"identity", "log1p", "row_zscore", "log1p_row_zscore"}:
        raise ValueError("matrix_transform_not_implemented")
    original = [[finite_number(v, allow_none=True) for v in r] for r in values]
    array = np.asarray([[np.nan if v is None else v for v in r] for r in original], dtype=float)
    reasons = [["value_missing" if v is None else None for v in r] for r in original]
    if transform in {"log1p", "log1p_row_zscore"}:
        if (array[np.isfinite(array)] < 0).any():
            raise ValueError("matrix_log1p_requires_nonnegative_expression")
        array = np.log1p(array)
    if transform in {"row_zscore", "log1p_row_zscore"}:
        for i in range(len(row_ids)):
            if not np.isfinite(array[i]).all():
                array[i] = np.nan
                reasons[i] = ["row_zscore_missing_measurement"] * len(column_ids)
            else:
                deviation = float(array[i].std(ddof=0))
                if deviation == 0:
                    array[i] = np.nan
                    reasons[i] = ["row_zscore_zero_variance"] * len(column_ids)
                else:
                    array[i] = (array[i] - array[i].mean()) / deviation
        diverging = True
    rorder, corder = list(range(len(row_ids))), list(range(len(column_ids)))
    trees, annotations = {}, ["Display transform: " + transform]
    options = {"metric": "euclidean", "method": "average"} if cluster is True else cluster
    if cluster:
        if not isinstance(options, dict) or set(options) - {"metric", "method", "rows", "columns"}:
            raise ValueError("matrix_clustering_contract_invalid")
        metric, method = options.get("metric", "euclidean"), options.get("method", "average")
        if metric not in {"euclidean", "precomputed"} or method not in {"average", "complete", "single"}:
            raise ValueError("matrix_clustering_method_not_supported")
        if not np.isfinite(array).all():
            annotations.append("Clustering unavailable: missing display values; no imputation performed.")
        else:
            from scipy.cluster.hierarchy import linkage, leaves_list, dendrogram
            from scipy.spatial.distance import pdist, squareform
            if metric == "precomputed" and (array.shape[0] != array.shape[1] or row_ids != column_ids
                    or not np.allclose(array, array.T) or not np.allclose(np.diag(array), 0) or (array < 0).any()):
                raise ValueError("precomputed_distance_matrix_invalid")
            for dimension, enabled in (("rows", options.get("rows", True)), ("columns", options.get("columns", True))):
                vectors = array if dimension == "rows" else array.T
                if not enabled or vectors.shape[0] < 2:
                    continue
                if vectors.shape[0] > 2000:
                    raise ValueError("matrix_clustering_exceeds_explicit_2000_entity_budget")
                distance = squareform(vectors, checks=True) if metric == "precomputed" else pdist(vectors, metric="euclidean")
                linkage_ = linkage(distance, method=method, optimal_ordering=True)
                order = [int(x) for x in leaves_list(linkage_)]
                if dimension == "rows": rorder = order
                else: corder = order
                trees[dimension] = dendrogram(linkage_, no_plot=True)
            annotations.append("Clustering: " + method + " linkage; " + metric + "; order is a descriptive display calculation.")
    row_lookup = {old: new for new, old in enumerate(rorder)}
    col_lookup = {old: new for new, old in enumerate(corder)}
    rows, marks = [], []
    row_metadata, column_metadata = row_metadata or {}, column_metadata or {}
    common = dict(details or {})
    row_kind = common.pop("row_entity_kind", "gene")
    column_kind = common.pop("column_entity_kind", "group")
    if not _text(row_kind) or not _text(column_kind):
        raise ValueError("matrix_entity_kinds_required")
    for i, row_id in enumerate(row_ids):
        for j, column_id in enumerate(column_ids):
            value = float(array[i, j]) if np.isfinite(array[i, j]) else None
            key = json.dumps([row_id, column_id], separators=(",", ":"))
            item = row(row_id, col_lookup[j], row_lookup[i], entity_kind=row_kind, group="matrix",
                key=key, reason=reasons[i][j], link={"kind": row_kind, "id": row_id},
                details={**common, "row_id": row_id, "column_id": column_id, "value": original[i][j],
                         "display_value": value, "unit": unit, "display_transform": transform,
                         "column_link": {"kind": column_kind, "id": column_id},
                         "row_metadata": row_metadata.get(row_id), "column_metadata": column_metadata.get(column_id)})
            rows.append(item)
            mark = {"type": "rect", "keys": [key], "group": "matrix", "x": col_lookup[j] - .48,
                    "y": row_lookup[i] - .48, "width": .96, "height": .96, "value": value}
            if value is None: mark["style"] = {"fill": "#edf1f4"}
            marks.append(mark)
    for dimension, tree in trees.items():
        maximum = max(max(path) for path in tree["dcoord"]) or 1
        for positions, distances in zip(tree["icoord"], tree["dcoord"]):
            points = [((position - 5) / 10, -1 - distance / maximum * 1.3) if dimension == "columns"
                      else (-1 - distance / maximum * 1.3, (position - 5) / 10)
                      for position, distance in zip(positions, distances)]
            marks.append(line_mark(points, group="clustering", style={"stroke": "#5d7281", "stroke_width": 1}))
    # Actual covariate bands have their own categorical colors, never the expression scale.
    fields = sorted({key for metadata in column_metadata.values() if isinstance(metadata, dict) for key in metadata})
    for track, field in enumerate(fields):
        category_for = lambda name: "Not recorded" if column_metadata.get(name, {}).get(field) is None else str(column_metadata[name][field])
        categories = sorted({category_for(name) for name in column_ids})
        for j, name in enumerate(column_ids):
            category = category_for(name)
            marks.append({"type": "rect", "keys": [r["key"] for r in rows if r["details"]["column_id"] == name],
                          "group": field + ": " + category, "x": col_lookup[j] - .48,
                          "y": len(row_ids) + .3 + track * .65, "width": .96, "height": .5})
        annotations.append("Covariate " + field + ": " + "; ".join(categories) + "; exact colors and additional patterns follow the category legend.")
    finite = array[np.isfinite(array)]
    lo, hi = (float(finite.min()), float(finite.max())) if finite.size else (0., 1.)
    if diverging:
        maximum = max(abs(lo), abs(hi)) or 1.
        lo, hi = -maximum, maximum
    return panel(capability, title, rows,
        axes={"x": {"label": "Samples / groups", "unit": "column", "transform": "categorical"},
              "y": {"label": "Features", "unit": "row", "transform": "categorical"}}, marks=marks,
        x_categories=[column_ids[i] for i in corder], y_categories=[row_ids[i] for i in rorder],
        color_scale={"label": "Displayed " + unit, "unit": "row z-score" if "zscore" in transform else unit,
                     "palette": "RdBu_r" if diverging else "viridis", "domain": [lo, hi], "center": 0 if diverging else None},
        annotations=annotations, limitations=["Missing matrix values remain missing; display transforms are not new inference."])
