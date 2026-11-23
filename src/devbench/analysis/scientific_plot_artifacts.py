"""Publication rendering of trusted, already prepared scientific plot geometry.

No paths, observations, fits, transforms, intervals or aggregates are accepted
from the export request.  Display filtering is the Python counterpart of
ScientificPlotPanels.filteredMarks; viewport cropping is an axes clip.
"""
from __future__ import annotations

from copy import copy, deepcopy
import io
import json
import math
import textwrap
import threading

from .scientific_plot_contract import MAX_MARKS, MAX_ROWS, finite_number, validate_workspace


_FIELDS = {"panel_id", "scope", "bounds", "filters", "selected_link", "selected_key", "reference_lines"}
_COLORS = ("#0072B2", "#E69F00", "#009E73", "#CC79A7", "#56B4E9", "#D55E00", "#F0E442", "#000000")
_SHAPES = ("circle", "square", "triangle", "diamond")
_PATTERNS = ("solid", "hollow", "diagonal", "cross", "dots", "horizontal", "vertical", "crosshatch")
_PX = 72 / 96  # Saved glyph radii/strokes are CSS pixels; publication geometry uses points.
_LOCK = threading.RLock()  # Matplotlib rc_context is process-global.


def _text(value, *, empty=False):
    return isinstance(value, str) and (empty or bool(value)) and len(value) <= 4000 and not any(
        ord(c) < 32 and c not in "\n\t" or 0xD800 <= ord(c) <= 0xDFFF for c in value)


def _bounds(values):
    if not isinstance(values, (list, tuple)) or len(values) != 4:
        raise ValueError("scientific_export_requires_four_bounds")
    result = [finite_number(v) for v in values]
    if not result[0] < result[1] or not result[2] < result[3] or not all(
            math.isfinite(b - a) for a, b in ((result[0], result[1]), (result[2], result[3]))):
        raise ValueError("scientific_export_bounds_must_increase")
    return result


def _extent(mark):
    kind = mark["type"]
    if kind == "image":
        from .scientific_plot_images import image_corners
        points = image_corners(mark["image"])
        return [min(p[0] for p in points), max(p[0] for p in points), min(p[1] for p in points), max(p[1] for p in points)]
    if kind in {"point", "text"}:
        return [mark["x"], mark["x"], mark["y"], mark["y"]]
    if kind in {"line", "arrow"}:
        points = ((mark["x1"], mark["y1"]), (mark["x2"], mark["y2"]))
    elif kind == "rect":
        points = ((mark["x"], mark["y"]), (mark["x"] + mark["width"], mark["y"] + mark["height"]))
    elif kind == "ellipse":
        points = ((mark["x"] - mark["rx"], mark["y"] - mark["ry"]),
                  (mark["x"] + mark["rx"], mark["y"] + mark["ry"]))
    else:
        points = mark["points"]
    return [min(p[0] for p in points), max(p[0] for p in points),
            min(p[1] for p in points), max(p[1] for p in points)]


def _full_bounds(panel):
    """Match ScientificPlotPanels.project().bounds, including source grids."""
    xs, ys = [], []
    for r in panel["rows"]:
        if r["x"] is not None and r["y"] is not None:
            xs.append(r["x"]); ys.append(r["y"])
    for mark in panel["marks"]:
        x0, x1, y0, y1 = _extent(mark)
        xs.extend((x0, x1)); ys.extend((y0, y1))
    for line in panel["reference_lines"]:
        (xs if line["axis"] == "x" else ys).append(line["value"])
    if panel["x_categories"]:
        xs.extend((0, len(panel["x_categories"]) - 1))
    if panel["y_categories"]:
        ys.extend((0, len(panel["y_categories"]) - 1))
    x0, x1 = (min(xs), max(xs)) if xs else (0, 1)
    y0, y1 = (min(ys), max(ys)) if ys else (0, 1)
    dx, dy = x1 - x0 or max(1, abs(x0) * .1), y1 - y0 or max(1, abs(y0) * .1)
    return _bounds([x0 - dx * .06, x1 + dx * .06, y0 - dy * .08, y1 + dy * .08])


def _prepare(workspace, request):
    validate_workspace(workspace)
    if sum(len(p["rows"]) for p in workspace["panels"]) > MAX_ROWS or sum(
            len(p["marks"]) for p in workspace["panels"]) > MAX_MARKS:
        raise ValueError("scientific_export_workspace_row_or_mark_budget_exceeded")
    if not isinstance(request, dict) or set(request) - _FIELDS or not _text(request.get("panel_id")):
        raise ValueError("scientific_export_request_fields_invalid")
    matches = [p for p in workspace["panels"] if p["id"] == request["panel_id"]]
    if len(matches) != 1:
        raise ValueError("scientific_export_panel_not_found")
    p = matches[0]
    scale = p["color_scale"]
    if scale is not None:
        diverging = _palette_name(scale["palette"]) in {"RdBu_r", "coolwarm"}
        if diverging != (scale["center"] == 0):
            raise ValueError("scientific_export_diverging_palette_requires_symmetric_zero_center")
    for mark in p["marks"]:
        if "size_value" in mark and (p["size_scale"] is None or mark["type"] != "point"):
            raise ValueError("scientific_export_size_scale_required_for_point_area")
        if mark["type"] == "point" and mark["radius"] > 1000:
            raise ValueError("scientific_export_point_radius_budget_exceeded")
    scope = request.get("scope", "complete")
    if scope not in ("complete", "current"):
        raise ValueError("scientific_export_scope_invalid")
    if scope == "current" and "bounds" not in request:
        raise ValueError("scientific_export_current_bounds_required")
    supplied_bounds = _bounds(request["bounds"]) if "bounds" in request else None
    filters = request.get("filters", {})
    if (not isinstance(filters, dict) or set(filters) - {"group", "status"}
            or any(not _text(v, empty=True) for v in filters.values())
            or filters.get("status", "all") not in {"", "all", "recorded", "excluded", "warnings", "selected"}):
        raise ValueError("scientific_export_filters_invalid")
    link, key = request.get("selected_link"), request.get("selected_key")
    if link is not None and (not isinstance(link, dict) or set(link) != {"kind", "id"}
                             or not all(_text(v) for v in link.values())):
        raise ValueError("scientific_export_typed_selection_invalid")
    if key is not None and not _text(key):
        raise ValueError("scientific_export_selected_key_invalid")
    guides = request.get("reference_lines", [])
    if not isinstance(guides, list) or len(guides) > 128:
        raise ValueError("scientific_export_reference_guides_invalid")
    for guide in guides:
        if (not isinstance(guide, dict) or set(guide) != {"axis", "value", "label"}
                or guide["axis"] not in ("x", "y") or not _text(guide["label"])):
            raise ValueError("scientific_export_reference_guide_invalid")
        finite_number(guide["value"])
    selected = {r["key"] for r in p["rows"] if r["link"] == link} if link is not None else ({key} if key is not None else set())
    allowed = set()
    for r in p["rows"]:
        keep = True
        if scope == "current":
            group, status = filters.get("group"), filters.get("status")
            if group and group != "all" and group != r["group"]:
                keep = False
            if status == "warnings" and not r["warnings"]:
                keep = False
            elif status == "selected" and r["key"] not in selected:
                keep = False
            elif status and status not in {"all", "warnings", "selected"} and status != r["status"]:
                keep = False
        if keep:
            allowed.add(r["key"])
    submitted, omitted = [], []
    for index, mark in enumerate(p["marks"]):
        reason = "filtered_member" if any(k not in allowed for k in mark["keys"]) else None
        if (reason is None and scope == "current" and not mark.get("context", False) and not mark["keys"] and filters.get("group")
                and filters["group"] != "all" and mark["group"] and mark["group"] != filters["group"]):
            reason = "filtered_group"
        if reason:
            omitted.append({"index": index, "type": mark["type"], "keys": list(mark["keys"]), "reason": reason})
        else:
            submitted.append(index)
    full_bounds = _full_bounds(p)  # Browser projection validates this range even for current views.
    parameters = {"scope": scope, "bounds": supplied_bounds if scope == "current" else full_bounds,
                  "source_geometry_bounds": full_bounds,
                  "filters": deepcopy(filters), "selected_link": deepcopy(link), "selected_key": key,
                  "reference_lines": deepcopy(guides), "coordinate_space": "saved_axis_coordinates", "dpi": 300,
                  "display_filters_applied": scope == "current", "viewport_clipping": "axes_clip_no_geometry_recomputation"}
    return p, parameters, allowed, selected, submitted, omitted


def _inside(row, bounds):
    return row["x"] is not None and row["y"] is not None and bounds[0] <= row["x"] <= bounds[1] and bounds[2] <= row["y"] <= bounds[3]


def _equal_aspect(panel):
    return panel["capability"] in {"spatial_expression", "spatial_tissue_overlay", "spatial_segmentation", "rna_velocity", "saved_embedding", "embedding_hexbin",
                                   "embedding_density_contours", "embedding_density_centroids"} or (
        panel["axes"]["x"]["unit"] == panel["axes"]["y"]["unit"] == "embedding_coordinate") or (
        panel["capability"] == "multimodal_features" and panel["axes"]["x"]["unit"] == panel["axes"]["y"]["unit"]
        and panel["axes"]["x"]["transform"] == panel["axes"]["y"]["transform"])


def export_coverage(workspace, request):
    """Return exact source custody and display accounting embedded in exports.

    Missing is a source status, so it may overlap display/filter counts (for
    example a retained missing heatmap cell). Cropping counts row coordinates,
    not aggregate membership. Every submitted primitive is clipped by the axes.
    """
    p, parameters, allowed, selected, submitted, omitted = _prepare(workspace, request)
    bounds, rows = parameters["bounds"], p["rows"]
    contributing = {k for index in submitted for k in p["marks"][index]["keys"]}
    keys = {"allowed": [], "display": [], "filtered": [], "cropped": [], "missing": [], "contributing": [], "without_mark": []}
    annotations = []
    labels = {(e["kind"], e["id"]): e["label"] for e in workspace["entities"]}
    for r in rows:
        key = r["key"]
        has_coordinates = r["x"] is not None and r["y"] is not None
        if r["status"] == "excluded":
            keys["missing"].append(key)
        if key in contributing:
            keys["contributing"].append(key)
        if key not in allowed:
            keys["filtered"].append(key)
        else:
            keys["allowed"].append(key)
            if has_coordinates and not _inside(r, bounds):
                keys["cropped"].append(key)
            elif _inside(r, bounds) and key in contributing:
                keys["display"].append(key)
            if key not in contributing:
                keys["without_mark"].append(key)
        if key in selected:
            link = r["link"]
            label = (link["kind"] + ": " + labels.get((link["kind"], link["id"]), link["id"])) if link else r["entity_kind"] + ": " + r["id"]
            state = "displayed" if key in allowed and key in contributing and _inside(r, bounds) else (
                "filtered" if key not in allowed else "missing" if not has_coordinates else "cropped" if not _inside(r, bounds) else "without_mark")
            annotations.append({"key": key, "id": r["id"], "link": deepcopy(link), "label": label,
                                "x": r["x"], "y": r["y"], "state": state})
    outside, clipped = [], []
    for index in submitted:
        x0, x1, y0, y1 = _extent(p["marks"][index])
        if x1 < bounds[0] or x0 > bounds[1] or y1 < bounds[2] or y0 > bounds[3]:
            outside.append(index)
        elif x0 < bounds[0] or x1 > bounds[1] or y0 < bounds[2] or y1 > bounds[3]:
            clipped.append(index)
    coverage = {"source_rows": len(rows), "coordinate_rows": p["coverage"]["coordinate_rows"],
                "missing_rows": len(keys["missing"]), "allowed_rows": len(keys["allowed"]),
                "display_rows": len(keys["display"]), "filtered_rows": len(keys["filtered"]),
                "cropped_rows": len(keys["cropped"]), "contributing_rows": len(keys["contributing"]),
                "rows_without_submitted_mark": len(keys["without_mark"]), "source_marks": len(p["marks"]),
                "submitted_marks": len(submitted), "omitted_marks": len(omitted),
                "partly_outside_mark_envelopes": len(clipped), "fully_outside_mark_envelopes": len(outside), "truncated": False}
    context_images = []
    for index, mark in enumerate(p["marks"]):
        if mark["type"] == "image":
            from .scientific_plot_images import image_corners
            context_images.append({"mark_index": index, "role": mark["role"],
                **{key: deepcopy(value) for key, value in mark["image"].items() if key != "png_base64"},
                "pixel_edge_corners": image_corners(mark["image"])})
    coverage["context_image_marks"] = len(context_images)
    coverage["submitted_context_image_marks"] = sum(p["marks"][i]["type"] == "image" for i in submitted)
    coverage["submitted_vector_marks"] = sum(p["marks"][i]["type"] != "image" for i in submitted)
    info = {"format": "devbench.scientific-plot-export/1", "source": deepcopy(workspace["source"]),
            "workspace_title": workspace["title"], "panel_id": p["id"], "capability": p["capability"], "title": p["title"],
            "axes": deepcopy(p["axes"]), "color_scale": deepcopy(p["color_scale"]), "size_scale": deepcopy(p["size_scale"]),
            "availability": deepcopy(p["availability"]), "x_categories": list(p["x_categories"]), "y_categories": list(p["y_categories"]),
            "source_rows": deepcopy(rows), "source_marks": deepcopy(p["marks"]), "render_parameters": parameters,
            "context_images": context_images,
            "coverage": coverage, "row_keys": keys, "submitted_mark_indices": submitted, "omitted_marks": omitted,
            "partly_outside_mark_envelope_indices": clipped, "fully_outside_mark_envelope_indices": outside,
            "selected_annotations": annotations,
            "reference_guides": [{**line, "origin": "saved_source_reference"} for line in p["reference_lines"]]
                + [{**line, "origin": "manual_display_guide"} for line in parameters["reference_lines"]],
            "annotations": list(p["annotations"]), "limitations": [*workspace["limitations"], *p["limitations"]],
            "equal_aspect": _equal_aspect(p),
            "coverage_meaning": "Missing is a source status and may overlap other counts. Display rows have a submitted mark and a coordinate in bounds; contributing rows include all submitted aggregate/path members. Cropped rows are allowed finite coordinates outside bounds. Mark envelopes use saved vertices (Bezier control hulls), not sampled curves. Submitted geometry is axes-clipped, never dropped or recomputed by viewport."}
    if parameters["scope"] == "complete":
        import matplotlib
        matplotlib.use("Agg")
        categories = _categories(p)
        entries = _legend_entries(p, categories, submitted)
        sidebar_width = max(216, 2 * p["size_scale"]["max_radius"] * _PX + 90 if p["size_scale"] else 216)
        _, side_height = _side_layout(p, info, entries, sidebar_width)
        pads = _pixel_padding(p, categories)
        plot_width = max(510, (pads[0] + pads[1] + 120) * _PX)
        plot_height = max(360, side_height + 12, 15 * min(25, len(p["y_categories"])), (pads[2] + pads[3] + 120) * _PX)
        parameters["bounds"] = _padded_full_bounds(p, bounds, plot_width / _PX, plot_height / _PX, categories)
        parameters["pixel_padding"] = {"algorithm": "directional_css_pixel_insets_v1", "pads_css_pixels": pads,
            "plot_width_css_pixels": plot_width / _PX, "plot_height_css_pixels": plot_height / _PX,
            "meaning": "Conservative full-scope glyph/stroke insets; saved sizes and source coordinates are unchanged."}
        info["publication_plot_box"] = {"width_points": plot_width, "height_points": plot_height}
    return info


def _categories(panel):
    names = sorted({r["group"] for r in panel["rows"] if r["group"]} | {m["group"] for m in panel["marks"] if m["group"]})
    result = {}
    for i, name in enumerate(names):
        block = i // 8
        result[name] = {"color": _COLORS[i % 8], "shape": _SHAPES[(block + block // 8) % 4],
                        "pattern": _PATTERNS[block % 8], "dash": [3 + block % 4 * 2, 2 + block // 4 % 4] if block else []}
    return result


def _palette_name(name):
    return {"diverging": "RdBu_r", "okabe_ito": "OkabeIto"}.get(name, name)


def _color(scale, value):
    from matplotlib import colormaps
    from matplotlib.colors import to_hex
    lo, hi = scale["domain"]
    fraction = .5 if hi == lo else (value - lo) / (hi - lo)
    index = int(math.floor(min(1., max(0., fraction)) * 255 + .5))
    return to_hex(colormaps[_palette_name(scale["palette"])](index / 255))


def _style(panel, mark, categories):
    category = categories.get(mark["group"], {"color": "#596b76", "shape": "circle", "pattern": "solid", "dash": []})
    saved = mark.get("style", {})
    scale = panel["color_scale"]
    continuous = bool(scale and _palette_name(scale["palette"]) != "OkabeIto" and mark.get("value") is not None)
    base = saved.get("fill") if saved.get("fill") not in {None, "none"} else _color(scale, mark["value"]) if continuous else category["color"]
    closed = mark["type"] in {"point", "polygon", "rect", "ellipse", "text"}
    return {"fill": saved.get("fill", base if closed else "none"), "stroke": saved.get("stroke", "none" if mark["type"] == "text" else base),
            "width": saved.get("stroke_width", 1) * _PX, "opacity": saved.get("opacity", 1),
            "dash": [x * _PX for x in saved.get("dash", [] if closed else category["dash"])],
            "shape": mark.get("shape", category["shape"]),
            "pattern": category["pattern"] if closed and mark["type"] != "text" and "fill" not in saved and not continuous else "solid",
            "continuous": continuous}


def _pixel_padding(panel, categories):
    """Directional left/right/bottom/top insets in saved CSS-pixel units."""
    pads = [0., 0., 0., 0.]
    for mark in panel["marks"]:
        kind = mark["type"]
        if kind == "image":
            continue
        style = _style(panel, mark, categories)
        closed = kind in {"point", "polygon", "rect", "ellipse", "text"}
        filled = closed and style["fill"] != "none" and style["pattern"] != "hollow"
        stroked = style["stroke"] != "none" and (style["width"] > 0 or kind == "arrow")
        if style["opacity"] == 0 or not (filled or stroked):
            continue
        half_stroke = style["width"] / _PX / 2 if stroked else 0
        extent = [0., 0., 0., 0.]
        if kind == "point":
            scale = panel["size_scale"]
            radius = scale["max_radius"] * math.sqrt(mark["size_value"] / scale["domain"][1]) if "size_value" in mark else mark["radius"]
            shape = style["shape"]
            if shape == "triangle":
                horizontal = radius * math.sqrt(math.pi / math.sqrt(3))
                vertical = radius * math.sqrt(4 * math.pi / (3 * math.sqrt(3)))
                extent = [horizontal, horizontal, vertical / 2, vertical]
            else:
                factor = {"circle": 1., "square": math.sqrt(math.pi) / 2, "diamond": math.sqrt(math.pi / 2)}[shape]
                extent = [radius * factor] * 4
        elif kind == "arrow" and stroked:
            extent = [7 + style["width"] / _PX] * 4
            half_stroke = 0.  # Arrow envelope already exceeds its shaft stroke.
        elif kind == "text":
            width = 11 * len(mark["text"])
            anchor = mark.get("anchor", "start")
            extent = [width if anchor == "end" else width / 2 if anchor == "middle" else 0,
                      width if anchor == "start" else width / 2 if anchor == "middle" else 0, 3, 11]
        pads = [max(prior, value + half_stroke + 1) for prior, value in zip(pads, extent)]
    return pads


def _padded_full_bounds(panel, base, width_css, height_css, categories=None):
    """Share with ScientificPlotPanels.paddedBounds: no iterative fitting.

    Source span occupies the viewport minus CSS-pixel insets. With equal aspect,
    use the smaller available pixels-per-data-unit scale for both axes.
    """
    left, right, bottom, top = _pixel_padding(panel, _categories(panel) if categories is None else categories)
    if width_css <= left + right or height_css <= bottom + top:
        raise ValueError("scientific_export_viewport_too_small_for_saved_pixel_glyphs")
    sx, sy = (width_css - left - right) / (base[1] - base[0]), (height_css - bottom - top) / (base[3] - base[2])
    if _equal_aspect(panel):
        sx = sy = min(sx, sy)
    return _bounds([base[0] - left / sx, base[1] + right / sx, base[2] - bottom / sy, base[3] + top / sy])


def _glyph(shape):
    from matplotlib.path import Path
    if shape == "circle":
        return Path.unit_circle()
    if shape == "square":
        a = math.sqrt(math.pi) / 2
        vertices = [(-a, -a), (a, -a), (a, a), (-a, a)]
    elif shape == "diamond":
        a = math.sqrt(math.pi / 2)
        vertices = [(0, a), (a, 0), (0, -a), (-a, 0)]
    else:
        a = math.sqrt(4 * math.pi / (3 * math.sqrt(3)))
        vertices = [(0, a), (math.sqrt(3) * a / 2, -a / 2), (-math.sqrt(3) * a / 2, -a / 2)]
    return Path(vertices + [vertices[0]], [Path.MOVETO, Path.LINETO, *([Path.LINETO] * (len(vertices) - 2)), Path.CLOSEPOLY])


def _contrasting_hatch(ax, artist, hatch):
    if not hatch:
        return
    if hasattr(artist, "set_hatchcolor"):
        artist.set_hatchcolor("#ffffff")
        return
    # Matplotlib 3.8 has no independent hatch-color setter. A transparent
    # vector overlay keeps its white hatch separate from the saved outline.
    from matplotlib.collections import Collection
    overlay = copy(artist)
    overlay.set_facecolor("none"); overlay.set_edgecolor("#ffffff"); overlay.set_linewidth(0)
    overlay.set_gid((artist.get_gid() or "scientific-legend") + "-pattern")
    artist.set_hatch(None)
    if isinstance(overlay, Collection): ax.add_collection(overlay, autolim=False)
    else: ax.add_patch(overlay)


def _image_placement(ax, payload, cache):
    """Share one normalized native array per SHA without changing registrations."""
    from matplotlib.artist import Artist
    from matplotlib.image import AxesImage
    from matplotlib.transforms import Affine2D
    from .scientific_plot_images import image_rgba
    key = payload["sha256"]
    if key not in cache:
        template = AxesImage(ax, origin="upper", interpolation="none", extent=(0, payload["width"], payload["height"], 0))
        template.set_data(image_rgba(payload))
        template.set_figure(ax.figure)
        cache[key] = template
    template = cache[key]

    class ImagePlacement(Artist):
        def draw(self, renderer):
            if not self.get_visible():
                return
            template.set_transform(self.get_transform())
            template.set_clip_path(self.get_clip_path())
            template.set_clip_box(self.get_clip_box())
            template.set_clip_on(self.get_clip_on())
            template.set_gid(self.get_gid())
            template.draw(renderer)
            self.stale = False

    artist = ImagePlacement()
    artist.set_transform(Affine2D.from_values(*payload["pixel_to_data"]) + ax.transData)
    artist.set_zorder(0)
    ax.add_artist(artist)
    return artist


def _draw_mark(ax, panel, mark, style, index, image_cache=None):
    from matplotlib.collections import PathCollection
    from matplotlib.path import Path
    from matplotlib.patches import Ellipse, PathPatch, Polygon, Rectangle
    from matplotlib.transforms import IdentityTransform
    from matplotlib import patheffects
    fill = "none" if style["pattern"] == "hollow" else style["fill"]
    hatch = {"diagonal": "/", "cross": "+", "dots": ".", "horizontal": "-", "vertical": "|", "crosshatch": "x"}.get(style["pattern"])
    props = {"facecolor": fill, "edgecolor": style["stroke"], "linewidth": style["width"], "alpha": style["opacity"], "hatch": hatch, "clip_on": True}
    if style["dash"] and any(style["dash"]):
        props["linestyle"] = (0, style["dash"])
    kind = mark["type"]
    if kind == "image":
        artist = _image_placement(ax, mark["image"], {} if image_cache is None else image_cache)
    elif kind == "point":
        scale = panel["size_scale"]
        radius = scale["max_radius"] * math.sqrt(mark["size_value"] / scale["domain"][1]) if "size_value" in mark else mark["radius"]
        artist = PathCollection([_glyph(style["shape"])], sizes=[(radius * _PX) ** 2], offsets=[(mark["x"], mark["y"])],
                                offset_transform=ax.transData, **props)
        artist.set_transform(IdentityTransform())
        ax.add_collection(artist, autolim=False)
    elif kind == "text":
        artist = ax.text(mark["x"], mark["y"], mark["text"], fontsize=8.25, color=style["fill"], alpha=style["opacity"],
                         ha={"start": "left", "middle": "center", "end": "right"}[mark.get("anchor", "start")], va="baseline", clip_on=True)
        if style["stroke"] != "none" and style["width"]:
            artist.set_path_effects([patheffects.withStroke(linewidth=style["width"], foreground=style["stroke"])])
    else:
        if kind == "rect":
            artist = Rectangle((mark["x"], mark["y"]), mark["width"], mark["height"], **props)
        elif kind == "ellipse":
            artist = Ellipse((mark["x"], mark["y"]), 2 * mark["rx"], 2 * mark["ry"], **props)
        else:
            points = [(mark["x1"], mark["y1"]), (mark["x2"], mark["y2"])] if kind in {"line", "arrow"} else mark["points"]
            if kind == "arc":
                codes = [Path.MOVETO] + [Path.CURVE3 if len(points) == 3 else Path.CURVE4] * (len(points) - 1)
            else:
                codes = [Path.MOVETO] + [Path.LINETO] * (len(points) - 1)
                if kind == "polygon":
                    points = [*points, points[0]]; codes.append(Path.CLOSEPOLY)
            artist = PathPatch(Path(points, codes), capstyle="round", joinstyle="round", **props)
        ax.add_patch(artist)
        if kind == "arrow" and style["stroke"] != "none":
            start, end = ax.transData.transform([(mark["x1"], mark["y1"]), (mark["x2"], mark["y2"])])
            dx, dy = end - start
            length = math.hypot(dx, dy)
            if length:
                ux, uy = dx / length, dy / length
                size = min(length, (7 * _PX + style["width"]) * ax.figure.dpi / 72)
                half = size * .45
                vertices = [end, (end[0] - ux * size - uy * half, end[1] - uy * size + ux * half),
                            (end[0] - ux * size + uy * half, end[1] - uy * size - ux * half)]
                head = Polygon(ax.transData.inverted().transform(vertices), color=style["stroke"], linewidth=0, alpha=style["opacity"], clip_on=True)
                head.set_gid(f"scientific-arrowhead-{index}"); ax.add_patch(head)
    artist.set_gid(f"scientific-mark-{index}-{kind}")
    artist.set_clip_path(ax.patch)
    artist.set_rasterized(False)
    _contrasting_hatch(ax, artist, hatch)
    return artist


def _wrap(value, width):
    lines = []
    for paragraph in str(value).splitlines() or [""]:
        lines.extend(textwrap.wrap(paragraph, max(10, width), replace_whitespace=False, break_long_words=True, break_on_hyphens=False) or [""])
    return "\n".join(lines)


def _wrap_points(value, width, size, *, bold=False):
    """Wrap publication prose using font widths, including long unbroken IDs."""
    from matplotlib.font_manager import FontProperties
    from matplotlib.textpath import TextToPath
    font = FontProperties(family="DejaVu Sans", size=size, weight="bold" if bold else "normal")
    converter = TextToPath()
    def fits(text):
        return converter.get_text_width_height_descent(text, font, ismath=False)[0] <= width
    lines = []
    for paragraph in str(value).splitlines() or [""]:
        current = ""
        for word in paragraph.split():
            candidate = current + (" " if current else "") + word
            if fits(candidate):
                current = candidate
                continue
            if current:
                lines.append(current); current = ""
            while word and not fits(word):
                low, high = 1, len(word)
                while low < high:
                    middle = (low + high + 1) // 2
                    if fits(word[:middle]): low = middle
                    else: high = middle - 1
                lines.append(word[:low]); word = word[low:]
            current = word
        lines.append(current)
    return "\n".join(lines)


def _number(value):
    return f"{value:.3g}"


def _axis_label(axis):
    return axis["label"] + " · " + axis["unit"] + (" · saved " + axis["transform"] if axis["transform"] != "identity" else "")


def _legend_entries(panel, categories, mark_indices=None):
    """Describe encodings actually used, including explicit covariate colors."""
    indices = range(len(panel["marks"])) if mark_indices is None else mark_indices
    records = [(index, panel["marks"][index], _style(panel, panel["marks"][index], categories))
               for index in indices if panel["marks"][index]["type"] != "image"]
    point_shapes = {style["shape"] for _, mark, style in records if mark["type"] == "point" and mark["group"]}
    entries, grouped = [], {}
    for index, mark, original_style in records:
        if not mark["group"]:
            continue
        style = dict(original_style)
        saved = mark.get("style", {})
        closed = mark["type"] in {"point", "rect", "ellipse", "polygon", "text"}
        fill = "none" if style["pattern"] == "hollow" else style["fill"]
        ink = "fill" if closed and fill != "none" else "stroke"
        if style["opacity"] == 0 or ink == "stroke" and (style["stroke"] == "none" or style["width"] == 0):
            continue
        continuous = style["continuous"] and ink not in saved
        if continuous:
            shape_channel = mark["type"] == "point" and ("shape" in mark or len(point_shapes) > 1)
            outline_channel = closed and saved.get("stroke") not in {None, "none"} and style["width"] > 0
            other_channel = (style["pattern"] != "solid" or bool(style["dash"])
                or "opacity" in saved and saved["opacity"] != 1
                or "stroke_width" in saved and saved["stroke_width"] != 1 and style["width"] > 0 and style["stroke"] != "none")
            if not (shape_channel or outline_channel or other_channel):
                continue
            if ink == "fill":
                style["fill"] = "#d5dce0"
                style["stroke"] = saved.get("stroke", "none" if style["stroke"] == "none" else "#d5dce0")
            else:
                style["stroke"] = "#596b76"
        style["fill"] = "none" if style["pattern"] == "hollow" else style["fill"]
        style["continuous"] = continuous
        style["symbol"] = "line" if mark["type"] in {"line", "arrow", "polyline", "arc"} else "point"
        signature = (style["fill"], style["stroke"], style["shape"], style["pattern"], style["symbol"],
                     style["width"], style["opacity"], tuple(style["dash"]), continuous)
        variants = grouped.setdefault(mark["group"], {})
        variant = variants.setdefault(signature, {"style": style, "types": [], "mark_indices": []})
        variant["mark_indices"].append(index)
        if mark["type"] not in variant["types"]:
            variant["types"].append(mark["type"])
    for name in categories:
        variants = list(grouped.get(name, {}).values())
        for index, variant in enumerate(variants):
            style = variant["style"]
            detail = "/".join(variant["types"]) + (" · " + style["shape"] if style["symbol"] == "point" else "")
            label = name + (f" · {detail} · {index + 1}/{len(variants)}" if len(variants) > 1 else "")
            if style["continuous"]:
                label += " (color: continuous scale)"
            entries.append({"group": name, "label": label, "styles": [style], "mark_indices": variant["mark_indices"]})
    return entries


def _side_layout(panel, info, entries, width):
    items, y = [], 0.
    def text(value, *, bold=False):
        nonlocal y
        wrapped = _wrap_points(value, width, 9, bold=bold)
        items.append(("text", y, wrapped, bold)); y += (wrapped.count("\n") + 1) * 12 + 6
    scale = panel["color_scale"]
    if scale and _palette_name(scale["palette"]) != "OkabeIto":
        text(scale["label"] + " · " + scale["unit"], bold=True)
        items.append(("color", y, scale, None)); y += 40
    scale = panel["size_scale"]
    if scale:
        text(scale["label"] + " · " + scale["unit"] + " · proportional area", bold=True)
        for value in (0, scale["domain"][1] / 4, scale["domain"][1] / 2, scale["domain"][1]):
            radius = scale["max_radius"] * _PX * math.sqrt(value / scale["domain"][1])
            height = max(14, 2 * radius)
            items.append(("size", y + height / 2, (value, radius, scale["max_radius"] * _PX), None)); y += height + 10
    for entry in entries:
        text(entry["label"])
        for start in range(0, len(entry["styles"]), max(1, int(width // 22))):
            items.append(("swatches", y, entry["styles"][start:start + max(1, int(width // 22))], None)); y += 18
    if info["selected_annotations"]:
        text("Manual selection annotation", bold=True)
        grouped = {}
        for item in info["selected_annotations"]:
            group = grouped.setdefault(item["label"], {"count": 0, "displayed": 0})
            group["count"] += 1; group["displayed"] += item["state"] == "displayed"
        for label, counts in grouped.items():
            text(label + f"; {counts['displayed']}/{counts['count']} saved row positions displayed")
    return items, y


def _draw_sidebar(ax, panel, items, width):
    from matplotlib.patches import PathPatch, Rectangle, Circle
    from matplotlib.transforms import Affine2D
    for kind, y, value, bold in items:
        if kind == "text":
            ax.text(0, y, value, va="top", fontsize=9, linespacing=1.2, weight="bold" if bold else "normal")
        elif kind == "color":
            lo, hi = value["domain"]
            for i in range(256):
                color = _color(value, lo + (hi - lo) * i / 255)
                ax.add_patch(Rectangle((i * width / 256, y), width / 256 + .01, 10, facecolor=color, linewidth=0, rasterized=False))
            ax.text(0, y + 15, _number(lo), va="top", fontsize=8)
            ax.text(width, y + 15, _number(hi), va="top", ha="right", fontsize=8)
            if value["center"] == 0:
                ax.text(width / 2, y + 15, "0", va="top", ha="center", fontsize=8)
        elif kind == "size":
            number, radius, maximum = value
            ax.add_patch(Circle((maximum + 2, y), radius, fill=False, edgecolor="#405766", linewidth=.7))
            ax.text(2 * maximum + 16, y, _number(number), va="center", fontsize=8)
        else:
            for i, style in enumerate(value):
                if style["symbol"] == "line":
                    ax.plot([i * 22, i * 22 + 14], [y + 5, y + 5], color=style["stroke"],
                            linewidth=style["width"], alpha=style["opacity"],
                            linestyle=(0, style["dash"]) if any(style["dash"]) else "-")
                    continue
                transform = Affine2D().scale(4 * _PX, -4 * _PX).translate(i * 22 + 5, y + 5) + ax.transData
                hatch = {"diagonal": "/", "cross": "+", "dots": ".", "horizontal": "-", "vertical": "|", "crosshatch": "x"}.get(style["pattern"])
                symbol = PathPatch(_glyph(style["shape"]), transform=transform,
                    facecolor="none" if style["pattern"] == "hollow" else style["fill"], edgecolor=style["stroke"],
                    linewidth=style["width"], alpha=style["opacity"], hatch=hatch, rasterized=False)
                ax.add_patch(symbol)
                _contrasting_hatch(ax, symbol, hatch)


def _tick_labels(ax, panel, bounds, info):
    from matplotlib.ticker import MaxNLocator
    info["categorical_tick_coverage"] = {}
    for name, position in (("x", (bounds[0], bounds[1])), ("y", (bounds[2], bounds[3]))):
        labels = panel[name + "_categories"]
        if not labels:
            getattr(ax, name + "axis").set_major_locator(MaxNLocator(nbins=7, min_n_ticks=3))
            continue
        visible = [i for i in range(len(labels)) if position[0] <= i <= position[1]]
        maximum = 18 if name == "x" else 25
        stride = max(1, math.ceil(len(visible) / maximum))
        indices = visible[::stride]
        if visible and visible[-1] not in indices:
            indices.append(visible[-1])
        getattr(ax, "set_" + name + "ticks")(indices)
        wrapped = [_wrap_points(labels[i], 100 if name == "x" else 132, 8) for i in indices]
        getattr(ax, "set_" + name + "ticklabels")(wrapped, fontsize=8, **({"rotation": 55, "ha": "right", "rotation_mode": "anchor"} if name == "x" else {}))
        info["categorical_tick_coverage"][name] = {"source_categories": len(labels), "categories_in_bounds": len(visible),
                                                  "labeled_indices": indices, "tick_thinning_only": stride > 1}


def _fit_decoration(fig, canvas, ax, sidebar, info):
    """Keep publication labels readable without altering the saved plot limits."""
    canvas.draw()
    renderer = canvas.get_renderer()
    for name, tick_info in info["categorical_tick_coverage"].items():
        labels = getattr(ax, "get_" + name + "ticklabels")()
        retained, boxes, values = [], [], []
        for label, index in zip(labels, tick_info["labeled_indices"]):
            box = label.get_window_extent(renderer).padded(2)
            if any(box.overlaps(prior) for prior in boxes):
                continue
            retained.append(index); values.append(label.get_text()); boxes.append(box)
        if len(retained) < len(labels):
            getattr(ax, "set_" + name + "ticks")(retained)
            getattr(ax, "set_" + name + "ticklabels")(values, fontsize=8,
                **({"rotation": 55, "ha": "right", "rotation_mode": "anchor"} if name == "x" else {}))
            tick_info.update(labeled_indices=retained, tick_thinning_only=True)
    canvas.draw()
    decorations = [*fig.texts, ax.xaxis.label, ax.yaxis.label, *ax.get_xticklabels(), *ax.get_yticklabels()]
    if sidebar is not None:
        decorations.extend(sidebar.texts)
    renderer = canvas.get_renderer()
    boxes = [t.get_window_extent(renderer) for t in decorations if t.get_text()]
    width, height = fig.get_size_inches() * 72
    factor = 72 / fig.dpi
    left = max(0., -min(b.x0 for b in boxes) * factor + 5)
    bottom = max(0., -min(b.y0 for b in boxes) * factor + 5)
    right = max(0., max(b.x1 for b in boxes) * factor - width + 5)
    top = max(0., max(b.y1 for b in boxes) * factor - height + 5)
    if any((left, right, bottom, top)):
        positions = [a.get_position(original=True).bounds for a in fig.axes]
        text_positions = [t.get_position() for t in fig.texts]
        new_width, new_height = width + left + right, height + bottom + top
        if new_width > 1728 or new_height > 3456:
            raise ValueError("scientific_export_readable_layout_exceeds_24_by_48_inch_budget")
        fig.set_size_inches(new_width / 72, new_height / 72)
        for target, (x, y, w, h) in zip(fig.axes, positions):
            target.set_position([(x * width + left) / new_width, (y * height + bottom) / new_height,
                                 w * width / new_width, h * height / new_height])
        for target, (x, y) in zip(fig.texts, text_positions):
            target.set_position(((x * width + left) / new_width, (y * height + bottom) / new_height))
        canvas.draw()
    renderer = canvas.get_renderer()
    overflow = []
    for index, item in enumerate(decorations):
        if not item.get_text(): continue
        box = item.get_window_extent(renderer)
        if box.x0 < -1 or box.y0 < -1 or box.x1 > fig.bbox.width + 1 or box.y1 > fig.bbox.height + 1:
            overflow.append(index)
    overlaps = []
    for name in info["categorical_tick_coverage"]:
        boxes = [t.get_window_extent(renderer) for t in getattr(ax, "get_" + name + "ticklabels")()]
        if any(a.overlaps(b) for i, a in enumerate(boxes) for b in boxes[i + 1:]):
            overlaps.append(name)
    info["publication_layout"].update(width_inches=float(fig.get_size_inches()[0]), height_inches=float(fig.get_size_inches()[1]),
        decoration_overflow=overflow, categorical_tick_overlaps=overlaps)
    if overflow or overlaps:
        raise ValueError("scientific_export_cannot_fit_readable_publication_labels")


def render_scientific_plot(workspace, request, *, format="pdf"):
    """Return a vector PDF/SVG or 300-dpi PNG without changing the source."""
    return _render_scientific_plots(workspace, request, formats=(format,))[format]


def _render_scientific_plots(workspace, request, *, formats):
    """Reuse validated coverage and figure layout across encodings of one plot."""
    if any(format not in ("pdf", "svg", "png") for format in formats):
        raise ValueError("scientific_export_format_must_be_pdf_svg_or_png")
    info = export_coverage(workspace, request)
    panel = next(p for p in workspace["panels"] if p["id"] == info["panel_id"])
    import matplotlib
    matplotlib.use("Agg")  # Select the noninteractive backend before any pyplot import.
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure
    bounds, coverage = info["render_parameters"]["bounds"], info["coverage"]
    categories = _categories(panel)
    entries = _legend_entries(panel, categories, info["submitted_mark_indices"])
    info["legend_entries"] = deepcopy(entries)
    sidebar_width = max(216, 2 * panel["size_scale"]["max_radius"] * _PX + 90 if panel["size_scale"] else 216)
    side_items, side_height = _side_layout(panel, info, entries, sidebar_width)
    title = _wrap(panel["title"], 90)
    captions = [f"{info['source']['kind'].upper()} source · {info['source']['run_id']} · {info['render_parameters']['scope'].capitalize()} export.",
                "Source SHA-256: " + info["source"]["sha256"],
                f"Rows: source {coverage['source_rows']}; displayed {coverage['display_rows']}; filtered {coverage['filtered_rows']}; cropped {coverage['cropped_rows']}; missing/excluded {coverage['missing_rows']}; aggregate/path contributors {coverage['contributing_rows']}.",
                f"Marks: {coverage['submitted_marks']}/{coverage['source_marks']} submitted to axes clipping; {coverage['omitted_marks']} omitted by display filters. Missing status may overlap the other row counts.",
                "Axes use saved coordinates without another transform: x = " + _axis_label(panel["axes"]["x"]) + "; y = " + _axis_label(panel["axes"]["y"]) + ".",
                "Availability: " + panel["availability"]["status"] + (" · " + panel["availability"]["reason"] if panel["availability"]["reason"] else ".")]
    if info["render_parameters"]["scope"] == "complete":
        captions.append("Complete scope ignores display filters and viewport; any manual selection or guides remain display annotations.")
    else:
        captions.append("Current scope: exact requested bounds " + json.dumps(bounds) + "; filters " + json.dumps(info["render_parameters"]["filters"]) + ". Crossing geometry is axes-clipped; aggregate membership is never recomputed.")
    if info["source"]["kind"] == "synthetic":
        captions.append("Synthetic source: these graphics verify rendering and do not establish empirical scientific findings.")
    if info["equal_aspect"]:
        captions.append("Equal axis aspect: one saved x unit has the same length as one saved y unit.")
    selection = info["render_parameters"]["selected_link"]
    selected_key = info["render_parameters"]["selected_key"]
    if (selection is not None or selected_key is not None) and not info["selected_annotations"]:
        identity = selection["kind"] + ": " + selection["id"] if selection is not None else "row key: " + selected_key
        captions.append("Manual selection " + identity + ": no matching saved row in this panel; no annotation position was invented.")
    captions.extend("Saved annotation: " + s for s in info["annotations"])
    captions.extend("Limitation: " + s for s in info["limitations"])
    for context in info["context_images"]:
        captions.append(f"Registered image context: {context['source']['id']} · intrinsic {context['width']}×{context['height']} PNG · SHA-256 {context['sha256']} · "
            f"registration {context['registration_id']} · coordinates {context['coordinate_system_id']} ({context['coordinate_unit']}). Image pixels are not biological rows; scientific overlays remain vector marks.")
    captions.extend(("Saved source reference" if g["origin"] == "saved_source_reference" else "Manual display guide; not an inferential threshold")
                    + f": {g['label']} · {g['axis']}={_number(g['value'])} in saved axis coordinates." for g in info["reference_guides"])
    if panel["x_categories"] or panel["y_categories"]:
        captions.append("Dense category ticks may be thinned for legibility; every saved category and source row remains in the export metadata.")
    x_length = max((len(_wrap_points(x, 100, 8).splitlines()) for x in panel["x_categories"]), default=1)
    y_length = max((min(len(x), 30) for x in panel["y_categories"]), default=8)
    left = max(76, y_length * 4.5 + 25)
    plot_width = info.get("publication_plot_box", {}).get("width_points", 510)
    width = left + plot_width + 38 + sidebar_width + 26
    caption_text = "\n".join(_wrap_points(value, width - 52, 8) for value in captions)
    caption_height = (caption_text.count("\n") + 1) * 11.2
    top = 32 + (title.count("\n") + 1) * 16
    bottom = 110 + 10 * x_length if panel["x_categories"] else 62
    main_height = info.get("publication_plot_box", {}).get("height_points", max(360, side_height + 12, 15 * min(25, len(panel["y_categories"]))))
    height = top + main_height + bottom + caption_height + 32
    if width > 1728 or height > 3456:
        raise ValueError("scientific_export_readable_layout_exceeds_24_by_48_inch_budget; choose_a_smaller_current_view")
    info["publication_layout"] = {"width_inches": width / 72, "height_inches": height / 72, "glyph_css_pixel_to_point": _PX}
    with _LOCK, matplotlib.rc_context({"font.family": "DejaVu Sans", "font.size": 9, "text.usetex": False,
            "text.parse_math": False, "svg.fonttype": "none", "svg.hashsalt": "devbench-scientific-publication-v1",
            "pdf.fonttype": 42, "axes.unicode_minus": True, "path.simplify": False, "agg.path.chunksize": 0, "image.composite_image": False}):
        fig = Figure(figsize=(width / 72, height / 72), dpi=96, facecolor="white")
        canvas = FigureCanvasAgg(fig)
        try:
            ybottom = caption_height + bottom + 26
            ax = fig.add_axes([left / width, ybottom / height, plot_width / width, main_height / height])
            ax.set(xlim=bounds[:2], ylim=bounds[2:], xlabel=_wrap_points(_axis_label(panel["axes"]["x"]), plot_width, 9),
                   ylabel=_wrap_points(_axis_label(panel["axes"]["y"]), main_height - 35, 9))
            ax.spines[["top", "right"]].set_visible(False)
            ax.tick_params(labelsize=8)
            _tick_labels(ax, panel, bounds, info)
            if info["equal_aspect"]:
                ax.set_aspect("equal", adjustable="box")
            ax.apply_aspect()
            image_cache = {}
            for index in info["submitted_mark_indices"]:
                mark = panel["marks"][index]
                _draw_mark(ax, panel, mark, _style(panel, mark, categories), index, image_cache)
            for index, guide in enumerate(info["reference_guides"]):
                line = (ax.axvline if guide["axis"] == "x" else ax.axhline)(guide["value"], color="#6b7280", linestyle="--", linewidth=.7, clip_on=True)
                line.set_gid("scientific-reference-guide-" + str(index))
            if panel["availability"]["status"] != "available":
                ax.text(.5, .5, _wrap(panel["availability"]["reason"], 60), transform=ax.transAxes,
                        ha="center", va="center", fontsize=10)
            visible_selected = [r for r in info["selected_annotations"] if r["state"] == "displayed"]
            if visible_selected:
                ax.plot([item["x"] for item in visible_selected], [item["y"] for item in visible_selected], marker="o", markersize=10,
                        fillstyle="none", color="#222222", linestyle="none", markeredgewidth=.8, clip_on=True)
            # Labels live in the separate sidebar; rings identify their exact saved positions.
            side = None
            if side_items:
                side = fig.add_axes([(left + plot_width + 38) / width, ybottom / height, sidebar_width / width, main_height / height])
                side.set(xlim=(0, sidebar_width), ylim=(main_height, 0)); side.set_axis_off()
                _draw_sidebar(side, panel, side_items, sidebar_width)
            fig.text(26 / width, 1 - 20 / height, title, ha="left", va="top", fontsize=12, weight="bold")
            fig.text(26 / width, (caption_height + 18) / height, caption_text, ha="left", va="top", fontsize=8, linespacing=1.4)
            # Keep limits exact even if future Matplotlib tick/reference APIs autoscale.
            ax.set(xlim=bounds[:2], ylim=bounds[2:])
            _fit_decoration(fig, canvas, ax, side, info)
            info["publication_layout"]["axes_bounds"] = [*ax.get_xlim(), *ax.get_ylim()]
            info["publication_layout"]["axes_inches"] = [ax.get_position().width * fig.get_size_inches()[0], ax.get_position().height * fig.get_size_inches()[1]]
            position = ax.get_position()
            info["publication_layout"]["axes_rectangle_inches"] = [position.x0 * fig.get_size_inches()[0], position.y0 * fig.get_size_inches()[1],
                position.width * fig.get_size_inches()[0], position.height * fig.get_size_inches()[1]]
            description = json.dumps(info, ensure_ascii=True, allow_nan=False, sort_keys=True, separators=(",", ":"))
            exports = {}
            for format in formats:
                if format == "pdf":
                    metadata = {"Creator": "DevBench trusted scientific plot exporter", "Title": panel["title"],
                                "Subject": description, "CreationDate": None, "ModDate": None}
                elif format == "svg":
                    metadata = {"Creator": "DevBench trusted scientific plot exporter", "Title": panel["title"],
                                "Description": description, "Date": None}
                else:
                    metadata = {"Software": "DevBench trusted scientific plot exporter", "Title": panel["title"],
                                "Description": description}
                output = io.BytesIO()
                fig.savefig(output, format=format, dpi=300, metadata=metadata, facecolor="white")
                exports[format] = output.getvalue()
            return exports
        finally:
            fig.clear()
