"""Actual saved tissue-image, spot, and cell-polygon plotting adapters.

Images are explicitly intrinsic raster context. Scientific overlays remain vector
geometry. No image registration, segmentation, expression normalization, tissue
identification, or inference is performed by these adapters.
"""

from __future__ import annotations

from copy import deepcopy
import math

from .scientific_plot_contract import finite_number, panel, require_fields, require_unique, row
from .scientific_plot_images import PHYSICAL_UNITS, validate_image_payload

MAX_SPATIAL_ROWS = 20_000
MAX_POLYGON_VERTICES = 512
MAX_TOTAL_VERTICES = 200_000
_MEMBERSHIP = ("sample_id", "section_id", "biological_unit_id", "coordinate_system_id", "registration_id")


def _spec(required, description, optional=""):
    return {"required": required.split(), "optional": [*optional.split(), "source_label"], "description": description}


CAPABILITIES = {
    "spatial_tissue_overlay": _spec(
        "image spots feature_id expression_unit coordinate_unit registration",
        "Original saved tissue PNG with genuine vector spots at registered physical coordinates; image context has no biological row count.",
        "spot_diameter"),
    "spatial_segmentation": _spec(
        "cells segmentation coordinate_unit registration",
        "Actual saved closed cell polygons in physical coordinates, with optional original tissue PNG and optional saved expression.",
        "image feature_id expression_unit"),
}


def _text(value, label):
    if not isinstance(value, str) or not value.strip() or len(value) > 500 or "\x00" in value:
        raise ValueError(f"{label} requires a bounded nonempty string")
    return value


def _scene(data):
    if not isinstance(data["coordinate_unit"], str) or data["coordinate_unit"] not in PHYSICAL_UNITS:
        raise ValueError("spatial physical coordinate units required")
    registration = data["registration"]
    require_fields(registration, ["id", "sample_id", "section_id", "biological_unit_id", "coordinate_system_id", "status", "method", "source_id"])
    for field, value in registration.items():
        _text(value, f"registration {field}")
    if registration["status"] != "registered":
        raise ValueError("saved registered physical coordinates required; registration is not inferred")
    membership = {field: registration["id" if field == "registration_id" else field] for field in _MEMBERSHIP}
    if "source_label" in data:
        _text(data["source_label"], "source label")
    return registration, membership


def _matching(record, membership, label):
    for field in _MEMBERSHIP:
        if record.get(field) != membership[field]:
            raise ValueError(f"{label} {field} must match the saved scene registration and biological membership")


def _image(data, membership):
    if "image" not in data:
        return []
    image = validate_image_payload(data["image"])
    identity = {**image["source"], "registration_id": image["registration_id"], "coordinate_system_id": image["coordinate_system_id"]}
    _matching(identity, membership, "image")
    if image["coordinate_unit"] != data["coordinate_unit"]:
        raise ValueError("image coordinate_unit must match the saved scene physical units")
    return [{"type": "image", "keys": [], "group": "tissue_reference", "role": "tissue_reference", "context": True, "image": deepcopy(image)}]


def _records(value, required, optional):
    if not isinstance(value, list) or not 1 <= len(value) <= MAX_SPATIAL_ROWS:
        raise ValueError("spatial source rows require a nonempty bounded list")
    for record in value:
        require_fields(record, ["id", *_MEMBERSHIP, *required], ["group", "status", "reason", "warnings", *optional])
        _text(record["id"], "spatial entity id")
        if "group" in record:
            _text(record["group"], "spatial group")
        warnings = record.get("warnings", [])
        if not isinstance(warnings, list) or len(warnings) > 100:
            raise ValueError("spatial warnings require a bounded list")
        for warning in warnings:
            _text(warning, "spatial warning")
        if not isinstance(record.get("status", "recorded"), str) or record.get("status", "recorded") not in {"recorded", "failed", "excluded", "unavailable"}:
            raise ValueError("unknown saved spatial status")
        if record.get("status", "recorded") != "recorded" and not record.get("reason"):
            raise ValueError("saved spatial failures require an explicit reason")
        if record.get("reason") is not None:
            _text(record["reason"], "saved spatial reason")
    require_unique([r["id"] for r in value], "spatial_entity_ids")
    return value


def _failure(record):
    return record.get("reason") if record.get("status", "recorded") != "recorded" or record.get("reason") else None


def _expression_settings(data, records, required=False):
    has_feature, has_unit = "feature_id" in data, "expression_unit" in data
    if has_feature != has_unit or (not has_feature and (required or any("expression" in r or "expression_reason" in r for r in records))):
        raise ValueError("saved expression requires both feature_id and expression_unit")
    if has_feature:
        _text(data["feature_id"], "expression feature_id")
        _text(data["expression_unit"], "expression_unit")
    return has_feature


def _expression(record, enabled):
    if not enabled:
        return None, None
    value = record.get("expression")
    reason = record.get("expression_reason")
    if reason is not None:
        _text(reason, "saved expression reason")
    if value is not None:
        value = finite_number(value)
        if reason is not None:
            raise ValueError("available expression cannot also have an unavailability reason")
    return value, (reason or "saved expression unavailable") if value is None else None


def _data_row(record, x, y, kind, data, expression, expression_reason, reason=None):
    details = {**deepcopy(record), "coordinate_unit": data["coordinate_unit"], "registration": deepcopy(data["registration"]),
               "feature_id": data.get("feature_id"), "expression_unit": data.get("expression_unit"),
               "expression": expression, "expression_reason": expression_reason,
               "expression_status": "absent" if "feature_id" not in data else "unavailable" if expression is None else "recorded"}
    if "segmentation" in data:
        details["segmentation"] = deepcopy(data["segmentation"])
    warnings = [*record.get("warnings", [])]
    if expression_reason:
        warnings.append("Expression unavailable: " + expression_reason)
    return row(record["id"], x, y, entity_kind=kind, group=record.get("group", ""), key=kind + ":" + record["id"],
               details=details, warnings=warnings, reason=reason, link={"kind": kind, "id": record["id"]})


def _color(values, data):
    return None if not values else {"label": data["feature_id"], "unit": data["expression_unit"], "palette": "viridis",
                                   "domain": [min(values), max(values)], "center": None}


def _finish(capability, data, rows, marks, values, extra_annotations):
    registration = data["registration"]
    image_present = "image" in data
    annotations = [
        "Equal physical aspect: one unit on x equals one unit on y; no independent axis stretching.",
        f"Saved registration {registration['id']}; coordinate system {registration['coordinate_system_id']}; method {registration['method']}; source {registration['source_id']}.",
        f"Sample {registration['sample_id']}; section {registration['section_id']}; biological unit {registration['biological_unit_id']}. Spots/cells are not independent biological replicates.",
        ("Tissue reference is an original intrinsic raster PNG; spots and segmentation boundaries are genuine vector overlays. Image context is retained through biological data filters and is not a biological row."
         if image_present else "Tissue image absent: actual saved segmentation boundaries are shown without an inferred image."),
        *extra_annotations,
    ]
    if image_present:
        image = data["image"]
        annotations.append(f"Image source {image['source']['id']} ({image['source']['kind']}); SHA-256 {image['sha256']}; {image['width']}×{image['height']} intrinsic pixels. Pixel edges use saved affine {image['pixel_to_data']}.")
    limitations = [
        "Rendering verifies supplied geometry and source consistency, not tissue identity, registration accuracy, segmentation accuracy, biological validity, or clinical readiness.",
        "Only saved geometry and saved expression are displayed; no segmentation, registration, normalization, uncertainty, or inference was generated.",
    ]
    if data.get("source_label"):
        limitations.append(data["source_label"])
    if image_present and data["image"]["source"]["kind"] == "synthetic":
        limitations.append("Synthetic tissue-reference pixels are software verification fixtures, not tissue observations.")
    unit = data["coordinate_unit"]
    title = "Saved tissue image and spatial expression" if capability == "spatial_tissue_overlay" else "Saved cell segmentation"
    return [panel(capability, title, rows, axes={"x": {"label": "Physical x", "unit": unit, "transform": "identity"},
        "y": {"label": "Physical y", "unit": unit, "transform": "identity"}}, marks=marks, color_scale=_color(values, data),
        annotations=annotations, limitations=limitations)]


def _tissue(data):
    _, membership = _scene(data)
    marks = _image(data, membership)
    records = _records(data["spots"], ["x", "y", "expression"], ["expression_reason"])
    _expression_settings(data, records, required=True)
    diameter = finite_number(data["spot_diameter"]) if "spot_diameter" in data else None
    if diameter is not None and diameter <= 0:
        raise ValueError("physical spot_diameter must be positive")
    rows, values = [], []
    for record in records:
        _matching(record, membership, "spot")
        reason = _failure(record)
        x, y = finite_number(record["x"], allow_none=True), finite_number(record["y"], allow_none=True)
        if x is None or y is None:
            reason = reason or "saved spatial coordinates unavailable"
        expression, expression_reason = _expression(record, True)
        r = _data_row(record, None if reason else x, None if reason else y, "spot", data, expression, expression_reason, reason)
        if diameter is not None:
            r["details"]["spot_diameter"] = diameter
        rows.append(r)
        if reason:
            continue
        mark = {"type": "point" if diameter is None else "ellipse", "keys": [r["key"]], "group": r["group"],
                "x": x, "y": y, "value": expression, "style": {"stroke": "#111111", "stroke_width": 0.8}}
        if diameter is None:
            mark["radius"] = 5
        else:
            mark.update(rx=diameter / 2, ry=diameter / 2)
        if expression is None:
            mark["style"]["fill"] = "#B3B3B3"
        else:
            values.append(expression)
        marks.append(mark)
    return _finish("spatial_tissue_overlay", data, rows, marks, values, [
        "Missing expression retains saved spot geometry as neutral gray; failed/missing coordinates retain source rows and reasons without glyphs.",
        f"Saved physical spot diameter: {diameter:g} {data['coordinate_unit']}." if diameter is not None else "Spot radius is a display glyph size (5 pixels), not a measured physical capture diameter.",
    ])


def _cross(a, b, c):
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def _segments_intersect(a, b, c, d):
    if max(a[0], b[0]) < min(c[0], d[0]) or max(c[0], d[0]) < min(a[0], b[0]) or max(a[1], b[1]) < min(c[1], d[1]) or max(c[1], d[1]) < min(a[1], b[1]):
        return False
    p, q, r, s = _cross(a, b, c), _cross(a, b, d), _cross(c, d, a), _cross(c, d, b)
    return ((p <= 0 <= q) or (q <= 0 <= p)) and ((r <= 0 <= s) or (s <= 0 <= r))


def _polygon(value):
    if not isinstance(value, list) or not 4 <= len(value) <= MAX_POLYGON_VERTICES:
        raise ValueError("segmentation requires a bounded saved closed polygon")
    points = []
    for point in value:
        if not isinstance(point, (list, tuple)) or len(point) != 2:
            raise ValueError("segmentation polygon coordinate pair required")
        points.append([finite_number(point[0]), finite_number(point[1])])
    if points[0] != points[-1]:
        raise ValueError("saved segmentation polygon must be explicitly closed")
    if len({tuple(point) for point in points[:-1]}) != len(points) - 1:
        raise ValueError("segmentation polygon has repeated vertices")
    # Translate before area/centroid calculations to avoid cancellation at large
    # physical offsets. The saved vertices, not a fitted boundary, remain marks.
    ox, oy = points[0]
    local = [[x - ox, y - oy] for x, y in points]
    crosses = [a[0] * b[1] - b[0] * a[1] for a, b in zip(local, local[1:])]
    area2 = math.fsum(crosses)
    if not math.isfinite(area2) or area2 == 0:
        raise ValueError("segmentation polygon must have finite nonzero area")
    n = len(points) - 1
    for i in range(n):
        for j in range(i + 2, n):
            if i == 0 and j == n - 1:
                continue
            if _segments_intersect(local[i], local[i + 1], local[j], local[j + 1]):
                raise ValueError("segmentation polygon self-intersects")
    x = ox + math.fsum((a[0] + b[0]) * cross for a, b, cross in zip(local, local[1:], crosses)) / (3 * area2)
    y = oy + math.fsum((a[1] + b[1]) * cross for a, b, cross in zip(local, local[1:], crosses)) / (3 * area2)
    return points, finite_number(x), finite_number(y), abs(area2) / 2


def _segmentation(data):
    _, membership = _scene(data)
    saved = data["segmentation"]
    require_fields(saved, ["id", *_MEMBERSHIP, "method", "source_id", "status"])
    for field, value in saved.items():
        _text(value, "segmentation " + field)
    _matching(saved, membership, "segmentation")
    if saved["status"] != "saved":
        raise ValueError("segmentation must be a saved output; no new segmentation is inferred")
    records = _records(data["cells"], ["polygon", "segmentation_id"], ["expression", "expression_reason"])
    enabled = _expression_settings(data, records)
    total_vertices = sum(len(r["polygon"]) for r in records if isinstance(r["polygon"], list))
    if total_vertices > MAX_TOTAL_VERTICES:
        raise ValueError("segmentation total vertex budget exceeded")
    marks, rows, values = _image(data, membership), [], []
    for record in records:
        _matching(record, membership, "cell")
        if record["segmentation_id"] != saved["id"]:
            raise ValueError("cell segmentation_id must match the saved segmentation source")
        reason = _failure(record)
        points, x, y, area = None, None, None, None
        if record["polygon"] is None:
            reason = reason or "saved segmentation polygon unavailable"
        elif not reason:
            points, x, y, area = _polygon(record["polygon"])
        expression, expression_reason = _expression(record, enabled)
        r = _data_row(record, x, y, "cell", data, expression, expression_reason, reason)
        r["details"].update(polygon_area=area, polygon_area_unit=data["coordinate_unit"] + "²", row_coordinate="descriptive polygon area centroid")
        rows.append(r)
        if reason:
            continue
        mark = {"type": "polygon", "keys": [r["key"]], "group": r["group"], "points": points,
                "style": {"stroke": "#111111", "stroke_width": 1, "opacity": 0.65}}
        if enabled:
            mark["value"] = expression
        if expression is None:
            mark["style"]["fill"] = "#B3B3B3" if enabled else "none"
        else:
            values.append(expression)
        marks.append(mark)
    return _finish("spatial_segmentation", data, rows, marks, values, [
        f"Saved segmentation {saved['id']}; method {saved['method']}; source {saved['source_id']}. Each mark preserves its complete saved exterior polygon; holes are not inferred.",
        "Row coordinates are descriptive polygon area centroids for selection; displayed boundaries are the exact saved vertices.",
        "Missing expression retains saved polygons as neutral gray; failed segmentations retain rows and reasons without fabricated boundaries." if enabled else "Expression absent: only saved segmentation boundaries are shown; no expression color scale is implied.",
    ])


def build_panels(capability, data):
    """Prepare saved spatial geometry with strict image/source/registration binding."""
    if not isinstance(capability, str) or capability not in CAPABILITIES:
        raise ValueError("unknown spatial plot capability: " + str(capability))
    spec = CAPABILITIES[capability]
    require_fields(data, spec["required"], spec["optional"])
    return _tissue(data) if capability == "spatial_tissue_overlay" else _segmentation(data)
