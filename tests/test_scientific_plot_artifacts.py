"""Synthetic publication checks, not evidence of biological validity."""
from copy import deepcopy
import base64
import importlib
import importlib.util
import io
import json
from pathlib import Path
import re
import shutil
import subprocess
import xml.etree.ElementTree as ET
import zlib

from PIL import Image
import pytest

from devbench.analysis.scientific_plot_contract import panel, row
from devbench.analysis.scientific_plot_suite import capability_catalog, prepare_scientific_plot_workspace


ROOT = Path(__file__).resolve().parents[1]
SOURCE = {"sha256": "a" * 64, "run_id": "synthetic-publication", "kind": "synthetic"}
AXES = {"x": {"label": "Saved abundance", "unit": "log10 counts", "transform": "log10"},
        "y": {"label": "Saved response", "unit": "response units", "transform": "identity"}}


def api():
    name = "devbench.analysis.scientific_plot_artifacts"
    assert importlib.util.find_spec(name), "scientific publication exporter is not implemented"
    return importlib.import_module(name)


def workspace(marks=None, rows=None, **kwargs):
    rows = rows or [row("same", 1, 2, key="gene:1", group="A"),
                    row("same", 3, 4, key="sample:1", entity_kind="sample", group="B", warnings=["Saved warning"]),
                    row("missing", reason="Saved measurement absent", group="A")]
    p = panel("synthetic_marks", "Synthetic publication geometry", rows, axes=deepcopy(AXES),
              marks=marks, limitations=["Synthetic geometry only; no scientific qualification."], **kwargs)
    return {"format": "devbench.scientific-plot-workspace/1", "title": "Synthetic source workspace",
            "source": deepcopy(SOURCE), "panels": [p], "entities": [
                {"kind": "gene", "id": "same", "label": "Gene label", "details": {}, "warnings": []},
                {"kind": "sample", "id": "same", "label": "Sample label", "details": {}, "warnings": []}],
            "limitations": ["Synthetic inputs are not empirical observations."]}


def request(w, **kwargs):
    return {"panel_id": w["panels"][0]["id"], "scope": "complete", **kwargs}


def svg_metadata(raw, *, allow_images=False):
    root = ET.fromstring(raw)
    if not allow_images:
        assert not root.findall(".//{http://www.w3.org/2000/svg}image")
    description = root.find(".//{http://purl.org/dc/elements/1.1/}description")
    assert description is not None
    return root, json.loads(description.text)


def pdf_content(raw, *, allow_images=False):
    assert raw.startswith(b"%PDF-") and raw.rstrip().endswith(b"%%EOF")
    if not allow_images:
        assert not re.search(rb"/Subtype\s*/Image\b", raw), "a publication PDF must not rasterize the plot/colorbar"
    streams = []
    for match in re.finditer(rb"stream\r?\n(.*?)\r?\nendstream", raw, re.S):
        try:
            streams.append(zlib.decompress(match.group(1)))
        except zlib.error:
            streams.append(match.group(1))
    assert streams
    return b"\n".join(streams)


def test_export_api_is_implemented():
    assert callable(api().render_scientific_plot)
    assert callable(api().export_coverage)


def test_all_nine_primitives_export_as_vector_paths_and_preserve_geometry():
    marks = [
        {"type": "point", "keys": ["gene:1"], "group": "A", "x": 1, "y": 2, "radius": 4},
        {"type": "line", "keys": ["gene:1"], "group": "A", "x1": -3, "y1": 2, "x2": 7, "y2": 2},
        {"type": "arrow", "keys": ["sample:1"], "group": "B", "x1": 3, "y1": 4, "x2": 5, "y2": 5},
        {"type": "polyline", "keys": ["gene:1"], "group": "A", "points": [[0, 0], [1, 1], [2, 0]]},
        {"type": "polygon", "keys": ["gene:1", "sample:1"], "group": "A", "points": [[0, 1], [1, 4], [2, 1]]},
        {"type": "rect", "keys": ["gene:1"], "group": "A", "x": 1, "y": 0, "width": 1, "height": 2},
        {"type": "ellipse", "keys": ["sample:1"], "group": "B", "x": 3, "y": 4, "rx": 2, "ry": 1},
        {"type": "arc", "keys": ["gene:1"], "group": "A", "points": [[0, 0], [1, 5], [2, 0]]},
        {"type": "arc", "keys": ["gene:1"], "group": "A", "points": [[0, 0], [1, 5], [2, 3], [3, 0]]},
        {"type": "text", "keys": ["gene:1"], "group": "A", "x": 1, "y": 2, "text": "Saved <gene> & value", "anchor": "middle"},
    ]
    w = workspace(marks)
    original = deepcopy(w)
    root, info = svg_metadata(api().render_scientific_plot(w, request(w), format="svg"))
    ids = {element.get("id") for element in root.iter()}
    assert all(f"scientific-mark-{i}-{mark['type']}" in ids for i, mark in enumerate(marks))
    assert info["coverage"]["submitted_marks"] == 10
    assert info["source_rows"] == w["panels"][0]["rows"]
    assert info["axes"] == AXES
    assert info["render_parameters"]["coordinate_space"] == "saved_axis_coordinates"
    content = pdf_content(api().render_scientific_plot(w, request(w), format="pdf"))
    assert b" c\n" in content  # Actual Bezier geometry, not a straight splice connector.
    assert w == original


def test_current_filters_keep_crossing_paths_but_omit_aggregate_with_excluded_member():
    marks = [{"type": "polyline", "keys": ["gene:1"], "group": "A", "points": [[-10, 2], [10, 2]]},
             {"type": "polygon", "keys": ["gene:1", "sample:1"], "group": "A", "points": [[0, 0], [2, 5], [4, 0]]},
             {"type": "line", "keys": [], "group": "B", "x1": 0, "y1": 0, "x2": 4, "y2": 4}]
    w = workspace(marks)
    req = request(w, scope="current", bounds=[.5, 1.5, 1, 3], filters={"group": "A"})
    info = api().export_coverage(w, req)
    assert info["submitted_mark_indices"] == [0]
    assert [x["reason"] for x in info["omitted_marks"]] == ["filtered_member", "filtered_group"]
    assert info["coverage"]["source_rows"] == 3
    assert info["coverage"]["filtered_rows"] == 1
    assert info["coverage"]["missing_rows"] == 1
    assert info["coverage"]["submitted_marks"] == 1
    assert info["coverage"]["omitted_marks"] == 2
    root, metadata = svg_metadata(api().render_scientific_plot(w, req, format="svg"))
    group = root.find(".//*[@id='scientific-mark-0-polyline']")
    assert group is not None and any(e.get("clip-path") for e in group.iter())
    assert metadata["render_parameters"]["bounds"] == req["bounds"]
    assert metadata["source_rows"] == w["panels"][0]["rows"]


def test_complete_scope_ignores_filters_crop_but_preserves_typed_selected_annotation_and_guides():
    w = workspace()
    req = request(w, bounds=[0, 1, 0, 1], filters={"group": "B", "status": "selected"},
                  selected_link={"kind": "gene", "id": "same"}, selected_key="sample:1",
                  reference_lines=[{"axis": "x", "value": 2, "label": "Manual guide"}])
    info = api().export_coverage(w, req)
    assert info["coverage"]["filtered_rows"] == 0
    assert info["coverage"]["submitted_marks"] == 2
    assert info["render_parameters"]["bounds"] != req["bounds"]
    assert info["selected_annotations"][0]["label"] == "gene: Gene label"
    assert [r["key"] for r in info["selected_annotations"]] == ["gene:1"]
    assert info["reference_guides"][-1]["origin"] == "manual_display_guide"
    svg, meta = svg_metadata(api().render_scientific_plot(w, req, format="svg"))
    text = " ".join(e.text or "" for e in svg.iter())
    assert "Gene label" in text and "Synthetic" in text
    assert meta["source"]["sha256"] == SOURCE["sha256"]


@pytest.mark.parametrize("filters,selection,expected", [
    ({"status": "all"}, {}, ["gene:1", "sample:1", "missing"]),
    ({"status": "warnings"}, {}, ["sample:1"]),
    ({"status": "recorded"}, {}, ["gene:1", "sample:1"]),
    ({"status": "excluded"}, {}, ["missing"]),
    ({"status": "selected"}, {"selected_link": {"kind": "sample", "id": "same"}}, ["sample:1"]),
    ({"status": "selected"}, {"selected_key": "gene:1"}, ["gene:1"]),
    ({"group": "A"}, {}, ["gene:1", "missing"]),
])
def test_filter_membership_and_bounds_match_shared_browser(filters, selection, expected):
    w = workspace()
    info = api().export_coverage(w, request(w, scope="current", bounds=[0, 5, 0, 5], filters=filters, **selection))
    assert info["row_keys"]["allowed"] == expected
    state = {**filters, **{"selectedLink" if k == "selected_link" else "selectedKey": v for k, v in selection.items()}}
    script = ("const f=require(process.argv[1]);const w=JSON.parse(require('fs').readFileSync(0,'utf8'));"
              "const m=f.project(w,w.panels[0].id);console.log(JSON.stringify({bounds:m.bounds,view:f.filteredMarks(m,"
              + json.dumps(state) + ")}));")
    result = subprocess.run([shutil.which("node"), "-e", script, str(ROOT / "src/devbench/analysis/assets/scientific-plot-panels.js")],
                            input=json.dumps(w), text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    browser = json.loads(result.stdout)
    assert info["row_keys"]["allowed"] == browser["view"]["allowedKeys"]
    assert info["submitted_mark_indices"] == [m["index"] for m in browser["view"]["marks"]]
    assert api().export_coverage(w, request(w))["render_parameters"]["source_geometry_bounds"] == browser["bounds"]


@pytest.mark.parametrize("extra", [
    {"path": "/private/untrusted.json"}, {"rows": []}, {"marks": []},
    {"scope": "other"}, {"scope": []}, {"scope": "current"}, {"bounds": [0, float("inf"), 0, 1]},
    {"bounds": [0, 0, 0, 1]}, {"bounds": [True, 1, 0, 1]},
    {"filters": {"query": "replacement"}}, {"selected_link": {"id": "same"}},
    {"reference_lines": [{"axis": "x", "value": float("nan"), "label": "bad"}]},
    {"reference_lines": [{"axis": [], "value": 0, "label": "bad"}]},
])
def test_untrusted_or_nonfinite_export_requests_are_rejected(extra):
    w = workspace()
    with pytest.raises(ValueError):
        api().render_scientific_plot(w, request(w, **extra), format="svg")


def _packets():
    packets = []
    for name in ("bulk", "expression", "advanced", "spatial", "diagnostics", "scrna_population"):
        spec = importlib.util.spec_from_file_location("publication_fixture_" + name, ROOT / f"tests/test_plot_adapters_{name}.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        packets.extend(module.fixture_packets())
    return packets


PACKETS = _packets()


def test_fixture_inventory_matches_entire_current_catalog():
    assert len(PACKETS) >= 58
    assert {p["capability"] for p in PACKETS} == {p["id"] for p in capability_catalog()}


@pytest.mark.parametrize("packet", PACKETS, ids=lambda p: p["capability"])
def test_all_catalog_capabilities_export_every_saved_panel_as_real_vectors(packet):
    w = prepare_scientific_plot_workspace({"format": "devbench.scientific-plot-input/1", "plots": [packet]}, source=SOURCE)
    for p in w["panels"]:
        req = {"panel_id": p["id"], "scope": "complete"}
        has_image = any(m["type"] == "image" for m in p["marks"])
        root, info = svg_metadata(api().render_scientific_plot(w, req, format="svg"), allow_images=has_image)
        assert info["coverage"]["submitted_marks"] == len(p["marks"])
        assert info["source_rows"] == p["rows"]
        assert info["availability"] == p["availability"]
        assert info["axes"] == p["axes"]
        assert root.findall(".//{http://www.w3.org/2000/svg}path")
        pdf_content(api().render_scientific_plot(w, req, format="pdf"), allow_images=has_image)
    raw = api().render_scientific_plot(w, {"panel_id": w["panels"][0]["id"], "scope": "complete"}, format="png")
    with Image.open(io.BytesIO(raw)) as image:
        info = json.loads(image.info["Description"])
        assert image.format == "PNG" and image.width >= 2400 and image.height >= 1500
        assert image.info["dpi"] == pytest.approx((300, 300), abs=.01)
        assert info["source"]["kind"] == "synthetic"
        assert info["render_parameters"]["dpi"] == 300


@pytest.mark.parametrize("capability,unit_x,unit_y,expected", [
    ("spatial_expression", "um", "um", True), ("rna_velocity", "um", "um", True),
    ("embedding_hexbin", "embedding coordinate", "embedding coordinate", True),
    ("saved_embedding", "embedding coordinate", "embedding coordinate", True),
    ("embedding_density_contours", "embedding coordinate", "embedding coordinate", True),
    ("embedding_density_centroids", "embedding_coordinate", "embedding_coordinate", True),
    ("feature_expression", "embedding_coordinate", "embedding_coordinate", True),
    ("integration_diagnostics", "embedding_coordinate", "embedding_coordinate", True),
    ("multimodal_features", "count", "count", True),
    ("multimodal_features", "count", "score", False),
])
def test_required_equal_aspect_preserves_exact_current_bounds(capability, unit_x, unit_y, expected):
    w = workspace()
    p = w["panels"][0]
    p["capability"] = capability
    p["axes"]["x"].update(unit=unit_x, transform="identity")
    p["axes"]["y"].update(unit=unit_y, transform="identity")
    req = request(w, scope="current", bounds=[-1, 9, -1, 4])
    assert api().export_coverage(w, req)["equal_aspect"] is expected
    _, info = svg_metadata(api().render_scientific_plot(w, req, format="svg"))
    assert info["publication_layout"]["axes_bounds"] == req["bounds"]
    if expected:
        width, height = info["publication_layout"]["axes_inches"]
        assert width / height == pytest.approx(2)


@pytest.mark.parametrize("palette", ["viridis", "magma", "RdBu_r", "coolwarm"])
def test_exact_256_color_scale_matches_browser_and_has_no_pdf_image(palette):
    w = workspace()
    p = w["panels"][0]
    p["color_scale"] = {"label": "Saved value", "unit": "score", "palette": palette,
                         "domain": [-2, 2] if palette in {"RdBu_r", "coolwarm"} else [0, 4],
                         "center": 0 if palette in {"RdBu_r", "coolwarm"} else None}
    for i, mark in enumerate(p["marks"]):
        mark["value"] = p["color_scale"]["domain"][i]
    script = "const p=require(process.argv[1]);console.log(JSON.stringify(p.lut(process.argv[2])));"
    result = subprocess.run([shutil.which("node"), "-e", script, str(ROOT / "src/devbench/analysis/assets/scientific-palettes.js"), palette],
                            text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    expected = json.loads(result.stdout)
    root, info = svg_metadata(api().render_scientific_plot(w, request(w), format="svg"))
    styles = " ".join(e.get("style", "") for e in root.iter()).lower()
    assert all(color.lower() in styles for color in set(expected))
    assert info["color_scale"] == p["color_scale"]
    pdf_content(api().render_scientific_plot(w, request(w), format="pdf"))


def test_dot_area_and_more_than_eight_categories_use_real_shape_fallback():
    rows = [row(str(i), i, 1, group=f"category-{i:02}") for i in range(10)]
    w = workspace(rows=rows, size_scale={"label": "Observed fraction", "unit": "fraction", "domain": [0, 1], "max_radius": 8})
    for i, mark in enumerate(w["panels"][0]["marks"]):
        mark["size_value"] = (i + 1) / 10
    root, info = svg_metadata(api().render_scientific_plot(w, request(w), format="svg"))
    circle = root.find(".//*[@id='scientific-mark-0-point']")
    square = root.find(".//*[@id='scientific-mark-8-point']")
    assert circle is not None and square is not None
    circle_paths = [e.get("d", "") for e in circle.iter()]
    square_paths = [e.get("d", "") for e in square.iter()]
    assert any("C" in d for d in circle_paths) and any("L" in d and "C" not in d for d in square_paths)
    assert info["size_scale"]["domain"] == [0, 1]
    assert "proportional area" in " ".join(e.text or "" for e in root.iter())


def test_full_geometry_extent_includes_source_grid_baseline_and_missing_rows():
    rows = [row("saved", 2, 10), row("missing", reason="Not measured")]
    w = workspace(rows=rows, marks=[{"type": "rect", "keys": ["saved"], "group": "counts", "x": 1, "y": 0, "width": 2, "height": 10}],
                  x_categories=["c" + str(i) for i in range(12)])
    info = api().export_coverage(w, request(w))
    assert info["render_parameters"]["bounds"][0] < 0
    assert info["render_parameters"]["bounds"][1] > 11
    assert info["render_parameters"]["bounds"][2] < 0
    assert info["coverage"]["missing_rows"] == 1


def test_budget_invalid_source_and_unknown_panel_or_format_are_refusals(monkeypatch):
    w = workspace()
    with pytest.raises(ValueError, match="panel_not_found"):
        api().render_scientific_plot(w, {"panel_id": "absent"})
    with pytest.raises(ValueError, match="format"):
        api().render_scientific_plot(w, request(w), format="jpeg")
    bad = deepcopy(w); bad["panels"][0]["rows"][0]["x"] = float("nan")
    with pytest.raises(ValueError):
        api().render_scientific_plot(bad, request(bad))
    monkeypatch.setattr(api(), "MAX_ROWS", 2)
    with pytest.raises(ValueError, match="budget"):
        api().render_scientific_plot(w, request(w))


def test_long_publication_labels_are_wrapped_or_padded_and_ticks_do_not_collide():
    rows = [row(f"entity-{i}", i, i % 3, group="Wide " + "W" * 70) for i in range(20)]
    w = workspace(rows=rows, x_categories=[f"Long-synthetic-label-{i}-" + "W" * 55 for i in range(20)],
                  annotations=["Saved annotation " + "W" * 150])
    w["panels"][0]["axes"]["y"]["label"] = "Saved response " + "W" * 90
    root, info = svg_metadata(api().render_scientific_plot(w, request(w), format="svg"))
    assert info["publication_layout"]["decoration_overflow"] == []
    assert info["publication_layout"]["categorical_tick_overlaps"] == []
    assert info["categorical_tick_coverage"]["x"]["tick_thinning_only"] is True
    assert info["x_categories"] == w["panels"][0]["x_categories"]
    assert "Saved annotation" in " ".join(e.text or "" for e in root.iter())


def test_large_categorical_palette_keeps_visible_white_pattern_fallback():
    rows = [row(str(i), i, 1, group=f"category-{i:02}") for i in range(33)]
    w = workspace(rows=rows)
    root, _ = svg_metadata(api().render_scientific_plot(w, request(w), format="svg"))
    patterns = root.findall(".//{http://www.w3.org/2000/svg}pattern")
    assert patterns
    assert any("#ffffff" in element.get("style", "") for p in patterns for element in p.iter()), "hatching must contrast with the saved category fill"


def test_selection_missing_from_panel_remains_an_explicit_manual_annotation():
    w = workspace()
    selected = {"kind": "gene", "id": "only-in-another-panel"}
    root, info = svg_metadata(api().render_scientific_plot(w, request(w, selected_link=selected), format="svg"))
    text = " ".join(e.text or "" for e in root.iter())
    assert "only-in-another-panel" in text and "no matching saved row" in text
    assert info["selected_annotations"] == []
    assert info["render_parameters"]["selected_link"] == selected


@pytest.mark.parametrize("palette,domain,center", [
    ("RdBu_r", [-1, 1], None), ("coolwarm", [0, 1], None), ("viridis", [-1, 1], 0),
])
def test_palette_center_contract_matches_the_browser_refusals(palette, domain, center):
    w = workspace()
    w["panels"][0]["color_scale"] = {"label": "Saved effect", "unit": "effect", "palette": palette, "domain": domain, "center": center}
    with pytest.raises(ValueError, match="diverging"):
        api().render_scientific_plot(w, request(w), format="svg")


def test_area_value_without_declared_size_scale_is_an_explicit_refusal():
    w = workspace()
    w["panels"][0]["marks"][0]["size_value"] = .5
    with pytest.raises(ValueError, match="size_scale"):
        api().render_scientific_plot(w, request(w), format="svg")


@pytest.mark.parametrize("group,bounds,color", [("Group0", [-1, 2, -1, 3], "#0072B2"), ("Group42", [40, 44, -1, 3], "#D55E00")])
def test_current_legend_uses_submitted_marks_with_complete_source_category_mapping(group, bounds, color):
    rows = [row(str(i), i, i % 3, group=f"Group{i}") for i in range(100)]
    w = workspace(rows=rows)
    req = request(w, scope="current", bounds=bounds, filters={"group": group})
    _, info = svg_metadata(api().render_scientific_plot(w, req, format="svg"))
    assert {entry["group"] for entry in info["legend_entries"]} == {group}
    assert info["coverage"]["source_rows"] == 100 and len(info["source_rows"]) == 100
    assert info["coverage"]["submitted_marks"] == 1
    assert info["legend_entries"][0]["styles"][0]["fill"] == color


def test_continuous_legend_preserves_explicit_outline_and_distinct_shapes():
    rows = [row(str(i), i, 1, group=f"group-{i}") for i in range(9)]
    w = workspace(rows=rows, color_scale={"label": "Saved value", "unit": "score", "domain": [0, 8], "palette": "viridis", "center": None})
    p = w["panels"][0]
    for i, mark in enumerate(p["marks"]):
        mark["value"] = i
    p["marks"][0]["style"] = {"stroke": "#ff0000", "stroke_width": 2}
    _, info = svg_metadata(api().render_scientific_plot(w, request(w), format="svg"))
    entries = info["legend_entries"]
    red = next(e for e in entries if e["group"] == "group-0")["styles"][0]
    assert red["fill"] == "#d5dce0" and red["stroke"] == "#ff0000"
    assert {s["shape"] for e in entries for s in e["styles"]} == {"circle", "square"}
    assert all("color: continuous scale" in e["label"] for e in entries)


def test_pure_continuous_marks_need_only_colorbar_but_explicit_hollow_outline_is_retained():
    w = workspace(rows=[row("a", 0, 0, group="pure"), row("b", 1, 1, group="outline")],
                  color_scale={"label": "Saved value", "unit": "score", "domain": [0, 1], "palette": "viridis", "center": None})
    for i, mark in enumerate(w["panels"][0]["marks"]):
        mark["value"] = i
    w["panels"][0]["marks"][1]["style"] = {"fill": "none", "stroke": "#ff0000"}
    _, info = svg_metadata(api().render_scientific_plot(w, request(w), format="svg"))
    assert [e["group"] for e in info["legend_entries"]] == ["outline"]
    style = info["legend_entries"][0]["styles"][0]
    assert style["fill"] == "none" and style["stroke"] == "#ff0000"


@pytest.mark.parametrize("shape", ["circle", "square", "diamond", "triangle"])
@pytest.mark.parametrize("equal_aspect", [False, True])
def test_complete_bounds_keep_full_radius_100_glyphs_and_strokes_visible(shape, equal_aspect):
    rows = [row("left", 0, 0, group="A"), row("right", 1, 1, group="A")]
    w = workspace(rows=rows)
    p = w["panels"][0]
    if equal_aspect:
        p["capability"] = "spatial_expression"
    for mark in p["marks"]:
        mark.update(radius=100, shape=shape, style={"stroke_width": 20})
    _, info = svg_metadata(api().render_scientific_plot(w, request(w), format="svg"))
    bounds = info["render_parameters"]["bounds"]
    width, height = [v * 96 for v in info["publication_layout"]["axes_inches"]]
    a = (4 * 3.141592653589793 / (3 * 3 ** .5)) ** .5
    ex = {"circle": 100, "square": 100 * 3.141592653589793 ** .5 / 2,
          "diamond": 100 * (3.141592653589793 / 2) ** .5, "triangle": 100 * (3.141592653589793 / 3 ** .5) ** .5}[shape]
    bottom = 50 * a if shape == "triangle" else ex
    top = 100 * a if shape == "triangle" else ex
    assert (0 - bounds[0]) / (bounds[1] - bounds[0]) * width >= ex + 10
    assert (bounds[1] - 1) / (bounds[1] - bounds[0]) * width >= ex + 10
    assert (0 - bounds[2]) / (bounds[3] - bounds[2]) * height >= bottom + 10
    assert (bounds[3] - 1) / (bounds[3] - bounds[2]) * height >= top + 10
    current = request(w, scope="current", bounds=[0, 1, 0, 1])
    _, cropped = svg_metadata(api().render_scientific_plot(w, current, format="svg"))
    assert cropped["render_parameters"]["bounds"] == [0, 1, 0, 1]


@pytest.mark.parametrize("capability", ["spatial_tissue_overlay", "spatial_segmentation"])
def test_registered_image_is_intrinsic_raster_with_vector_scientific_overlays(capability):
    packet = next(p for p in PACKETS if p["capability"] == capability)
    w = prepare_scientific_plot_workspace({"format": "devbench.scientific-plot-input/1", "plots": [packet]}, source=SOURCE)
    p = w["panels"][0]
    image_mark = next(m for m in p["marks"] if m["type"] == "image")
    root, info = svg_metadata(api().render_scientific_plot(w, request(w), format="svg"), allow_images=True)
    images = root.findall(".//{http://www.w3.org/2000/svg}image")
    assert len(images) == 1
    encoded = images[0].get("{http://www.w3.org/1999/xlink}href")
    with Image.open(io.BytesIO(base64.b64decode(encoded.split(",", 1)[1]))) as native:
        assert native.size == (2, 2), "only intrinsic tissue pixels may be rasterized"
    assert all(root.find(f".//*[@id='scientific-mark-{i}-{m['type']}']") is not None for i, m in enumerate(p["marks"]))
    assert info["context_images"][0]["sha256"] == image_mark["image"]["sha256"]
    assert info["context_images"][0]["pixel_edge_corners"] == [[10, 30], [14, 30], [14, 24], [10, 24]]
    assert info["source_rows"] == p["rows"]
    assert info["equal_aspect"] is True
    raw = api().render_scientific_plot(w, request(w), format="pdf")
    pdf_content(raw, allow_images=True)
    assert re.search(rb"/Subtype\s*/Image\b", raw)
    assert re.search(rb"/Width\s+2\b", raw) and re.search(rb"/Height\s+2\b", raw)


@pytest.mark.parametrize("affine,bounds,centers", [
    ([2, 0, 0, -3, 10, 30], [10, 14, 24, 30], [[11, 28.5], [13, 28.5], [11, 25.5], [13, 25.5]]),
    ([0, 2, 3, 0, 10, 20], [10, 16, 20, 24], [[11.5, 21], [11.5, 23], [14.5, 21], [14.5, 23]]),
])
def test_image_affine_pixel_centers_and_filter_independent_context(affine, bounds, centers):
    packet = deepcopy(next(p for p in PACKETS if p["capability"] == "spatial_tissue_overlay"))
    packet["data"]["image"]["pixel_to_data"] = affine
    w = prepare_scientific_plot_workspace({"format": "devbench.scientific-plot-input/1", "plots": [packet]}, source=SOURCE)
    req = request(w, scope="current", bounds=bounds, filters={"group": "exclude-all-biological-marks"})
    raw = api().render_scientific_plot(w, req, format="png")
    with Image.open(io.BytesIO(raw)) as output:
        info = json.loads(output.info["Description"])
        assert info["coverage"]["submitted_marks"] == 1 and info["coverage"]["display_rows"] == 0
        assert info["coverage"]["source_rows"] == 4
        x0, y0, width, height = info["publication_layout"]["axes_rectangle_inches"]
        for (x, y), color in zip(centers, [(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0)]):
            px = round((x0 + (x - bounds[0]) / (bounds[1] - bounds[0]) * width) * 300)
            py = output.height - round((y0 + (y - bounds[2]) / (bounds[3] - bounds[2]) * height) * 300)
            assert output.getpixel((px, py))[:3] == color


def test_repeated_image_placements_share_decoding_and_keep_individual_registration(monkeypatch):
    from devbench.analysis import scientific_plot_images
    packet = deepcopy(next(p for p in PACKETS if p["capability"] == "spatial_tissue_overlay"))
    w = prepare_scientific_plot_workspace({"format": "devbench.scientific-plot-input/1", "plots": [packet]}, source=SOURCE)
    p = w["panels"][0]
    repeated = deepcopy(next(m for m in p["marks"] if m["type"] == "image"))
    repeated["image"]["pixel_to_data"][4] += 6
    repeated["image"]["registration_id"] = "synthetic-second-registration"
    p["marks"].append(repeated)
    p["coverage"]["mark_count"] += 1
    decode = scientific_plot_images.image_rgba
    calls = []
    def track(payload):
        calls.append(payload["sha256"])
        return decode(payload)
    monkeypatch.setattr(scientific_plot_images, "image_rgba", track)
    root, info = svg_metadata(api().render_scientific_plot(w, request(w), format="svg"), allow_images=True)
    images = root.findall(".//{http://www.w3.org/2000/svg}image")
    assert len(calls) == 1, "one decoded array/template per unique image prevents repeated-placement memory multiplication"
    assert len(images) == 2 and images[0].get("transform") != images[1].get("transform")
    assert info["context_images"][0]["pixel_edge_corners"] != info["context_images"][1]["pixel_edge_corners"]
