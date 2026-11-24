"""The shared mark renderer preserves saved geometry and typed source membership."""
import json
import importlib
import base64
from copy import deepcopy
from pathlib import Path
import shutil
import subprocess
import xml.etree.ElementTree as ET

import pytest


MODULE = Path(__file__).resolve().parents[1] / "src/devbench/analysis/assets/scientific-plot-panels.js"


def workspace():
    rows = [
        {"id": "shared<&\"'", "key": "gene:key<&\"'", "entity_kind": "gene", "x": 1, "y": 2,
         "group": "A", "status": "recorded", "reason": None, "warnings": [],
         "details": {"saved_value": 10}, "link": {"kind": "gene", "id": "shared<&\"'"}},
        {"id": "shared<&\"'", "key": "sample:key", "entity_kind": "sample", "x": 3, "y": 4,
         "group": "B", "status": "recorded", "reason": None, "warnings": ["Saved warning"],
         "details": {"saved_value": 100}, "link": {"kind": "sample", "id": "shared<&\"'"}},
        {"id": "absent", "key": "absent:key", "entity_kind": "gene", "x": None, "y": None,
         "group": "A", "status": "excluded", "reason": "Measurement not recorded", "warnings": [],
         "details": {}, "link": {"kind": "gene", "id": "absent"}},
    ]
    panel = {"id": "panel<&\"'", "capability": "synthetic_marks", "title": "Saved marks <&\"'",
             "entity_kind": "mixed", "axes": {
                 "x": {"label": "Saved log abundance", "unit": "log10 counts", "transform": "log10"},
                 "y": {"label": "Saved response", "unit": "response units", "transform": "identity"}},
             "rows": rows,
             "marks": [{"type": "point", "keys": [r["key"]], "group": r["group"],
                        "x": r["x"], "y": r["y"], "radius": 3} for r in rows[:2]],
             "x_categories": [], "y_categories": [], "color_scale": None, "size_scale": None,
             "reference_lines": [], "annotations": [], "limitations": ["Synthetic verification only"],
             "availability": {"status": "available", "reason": None},
             "coverage": {"source_rows": 3, "coordinate_rows": 2, "missing_rows": 1,
                          "mark_count": 2, "truncated": False}}
    return {"format": "devbench.scientific-plot-workspace/1", "title": "Synthetic saved workspace",
            "source": {"sha256": "a" * 64, "run_id": "synthetic-run", "kind": "synthetic"},
            "panels": [panel], "entities": [], "limitations": ["No scientific qualification"]}


def replace_marks(data, marks):
    data["panels"][0]["marks"] = marks
    data["panels"][0]["coverage"]["mark_count"] = len(marks)
    return data


JS_GEOMETRY = """
function geometry(m,b=m.bounds) {
  const width=500,height=400,left=40,right=20,top=30,bottom=40,pw=440,ph=330;
  return {width,height,m:{left,right,top,bottom},pw,ph,bounds:b,rows:[],
    sx:x=>left+(x-b[0])/(b[1]-b[0])*pw,sy:y=>top+(b[3]-y)/(b[3]-b[2])*ph};
}
function recordingContext() {
  const calls=[], ctx={calls};
  for(const k of ['save','restore','beginPath','closePath','moveTo','lineTo','arc','ellipse',
    'rect','fill','stroke','clip','quadraticCurveTo','bezierCurveTo','fillText','strokeText','setLineDash','transform','drawImage'])
    ctx[k]=(...args)=>calls.push([k,...args]);
  return ctx;
}
"""

JS_IMAGE_LOADER = """
let decodeCalls=0,closeCalls=0;
globalThis.createImageBitmap=async blob=>{
  decodeCalls++;const bytes=await blob.arrayBuffer(),view=new DataView(bytes);
  return {width:view.getUint32(16),height:view.getUint32(20),close(){closeCalls++;}};
};
"""


def image_workspace():
    from tests.test_plot_adapters_spatial import packet
    data = workspace()
    panel = data["panels"][0]
    panel["capability"] = "spatial_tissue_overlay"
    for axis in panel["axes"].values():
        axis.update(unit="um", transform="identity")
    for row, mark, position in zip(panel["rows"][:2], panel["marks"], [(11, 28), (13, 26)]):
        row.update(x=position[0], y=position[1])
        mark.update(x=position[0], y=position[1])
    image = {"type": "image", "keys": [], "group": "tissue_reference", "role": "tissue_reference",
             "context": True, "image": packet("spatial_tissue_overlay")["image"]}
    return replace_marks(data, [image, *panel["marks"]])


def evaluate(code, data=None):
    assert MODULE.is_file(), "Scientific primitive renderer is not implemented"
    node = shutil.which("node")
    assert node, "Node is required for shared renderer verification"
    script = ("const f=require(" + json.dumps(str(MODULE)) + ");"
              "const w=JSON.parse(require('fs').readFileSync(0,'utf8'));\n" + JS_GEOMETRY + code)
    result = subprocess.run([node, "-e", script], input=json.dumps(workspace() if data is None else data),
                            text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def svg_elements(value):
    return list(ET.fromstring("<svg>" + value + "</svg>").iter())


def test_projection_preserves_all_rows_typed_identity_and_already_transformed_values():
    result = evaluate("const m=f.project(w,w.panels[0].id);console.log(JSON.stringify(m));")
    assert len(result["rows"]) == 3
    assert result["rows"][0]["x"] == 1  # Saved coordinates must not be logged a second time.
    assert result["rows"][0]["entityKind"] == "gene"
    assert result["rows"][1]["entityKind"] == "sample"
    assert result["rows"][0]["id"] == result["rows"][1]["id"]
    assert result["rows"][0]["key"] != result["rows"][1]["key"]
    assert result["rows"][2]["status"] == "excluded"
    assert result["rows"][2]["x"] is None and result["rows"][2]["reason"]
    assert result["rows"][0]["status"] == "recorded"
    assert result["source"]["sha256"] == "a" * 64
    assert result["connections"] == [] and result["scientific"] is True
    assert result["scientificPanel"]["axes"]["x"]["transform"] == "log10"
    assert "log10 counts" in result["axes"]["x"]


@pytest.mark.parametrize("mutation", [
    "w.format='untrusted'", "w.source.sha256='f'", "w.source.kind='clinical'", "w.source.run_id=''",
    "w.panels[0].rows[1].key=w.panels[0].rows[0].key",
    "w.panels[0].rows[0].x=Infinity", "w.panels[0].rows[0].details.value=NaN",
    "w.panels[0].rows[2].reason=null", "w.panels[0].rows[0].status='estimated'",
    "w.panels[0].rows[0].link={id:'same'}", "w.panels[0].marks[0].keys=['unknown']",
    "w.panels[0].marks[0].keys=[w.panels[0].rows[0].key,w.panels[0].rows[0].key]",
    "w.panels[0].marks[0].radius=-1", "w.panels[0].marks[0].x=null",
    "w.panels[0].marks[0].shape='executable'",
    "w.panels[0].marks[0].style={fill:'url(https://example.test)'}",
    "w.panels[0].marks[0].style={onclick:'bad()'}",
    "w.panels[0].marks[0].style={opacity:2}",
    "w.panels[0].marks[0].type='image'", "w.panels[0].coverage.source_rows=99",
    "w.panels[0].x_categories=['duplicate','duplicate']",
    "w.panels[0].color_scale={label:'Effect',unit:'log2',palette:'diverging',domain:[-1,2],center:0}",
    "w.panels[0].size_scale={label:'Fraction',unit:'%',domain:[-1,100],max_radius:12}",
    "w.panels[0].axes.x.transform=''", "w.panels[0].marks[0].style={stroke_width:Infinity}",
    "w.panels[0].rows[0].reason='excluded reason but status recorded'",
    "w.panels[0].rows[0].status='excluded'",
    "w.panels[0].rows[0].id='bad\\ud800'",
    "w.panels[0].marks[0].expression='x => x + 1'",
])
def test_invalid_or_unsafe_saved_workspace_is_refused(mutation):
    result = evaluate(mutation + ";let refused=false;try{f.prepareWorkspace(w);}catch(e){refused=true;}console.log(JSON.stringify(refused));")
    assert result is True


def primitive_marks():
    common = {"keys": [workspace()["panels"][0]["rows"][0]["key"]], "group": "A"}
    return [{**common, **mark} for mark in [
        {"type": "point", "x": 1, "y": 2, "radius": 3, "shape": "diamond"},
        {"type": "line", "x1": -3, "y1": -4, "x2": 4, "y2": 5},
        {"type": "arrow", "x1": 0, "y1": 0, "x2": 2, "y2": 2},
        {"type": "polyline", "points": [[-2, 0], [0, 2], [2, 0]]},
        {"type": "polygon", "points": [[-1, 1], [0, 0], [1, 1], [0, 4]]},
        {"type": "rect", "x": 1, "y": 0, "width": 2, "height": 6},
        {"type": "ellipse", "x": -3, "y": 4, "rx": 2, "ry": 3},
        {"type": "arc", "points": [[0, 0], [1, 10], [2, 0]]},
        {"type": "arc", "points": [[0, 0], [1, 8], [2, 6], [3, 0]]},
        {"type": "text", "x": 1, "y": 2, "text": "glyph <&\"'", "anchor": "middle"},
    ]]


def test_every_primitive_has_real_canvas_paths_and_genuine_vector_svg():
    data = replace_marks(workspace(), primitive_marks())
    result = evaluate("const m=f.project(w,w.panels[0].id),g=geometry(m),ctx=recordingContext();const d=f.drawMarks(ctx,m,g,{});console.log(JSON.stringify({drawing:d,calls:ctx.calls,svg:f.svgMarks(m,g,{},'complete')}));", data)
    assert result["drawing"]["drawnMarks"] == 10
    assert result["drawing"]["omittedMarks"] == []
    calls = [call[0] for call in result["calls"]]
    assert {"lineTo", "rect", "ellipse", "quadraticCurveTo", "bezierCurveTo", "fillText"} <= set(calls)
    elems = svg_elements(result["svg"])
    assert {"polygon", "polyline", "rect", "ellipse", "path", "text"} <= {e.tag for e in elems}
    assert any("Q" in e.get("d", "") for e in elems)
    assert any("C" in e.get("d", "") for e in elems)
    assert any(e.get("data-arrowhead") == "true" for e in elems)
    assert all("image" != e.tag for e in elems)
    assert sum(e.text == "glyph <&\"'" for e in elems) == 1
    keyed = [e for e in elems if e.get("data-source-keys")]
    assert len(keyed) == 10
    assert all(json.loads(e.get("data-source-keys")) == [data["panels"][0]["rows"][0]["key"]] for e in keyed)


@pytest.mark.parametrize("shape,tag", [("circle", "circle"), ("square", "rect"), ("triangle", "polygon"), ("diamond", "polygon")])
def test_point_shapes_are_drawn_in_canvas_and_svg(shape, tag):
    data = workspace()
    data["panels"][0]["marks"][0]["shape"] = shape
    result = evaluate("const m=f.project(w,w.panels[0].id),g=geometry(m),ctx=recordingContext();f.drawMarks(ctx,m,g,{});console.log(JSON.stringify({svg:f.svgMarks(m,g,{},'complete'),calls:ctx.calls}));", data)
    assert tag in [e.tag for e in svg_elements(result["svg"])]
    assert any(call[0] == ("arc" if shape == "circle" else "rect" if shape == "square" else "lineTo") for call in result["calls"])


def test_bounds_include_baselines_ribbons_boxes_ellipses_and_dendrogram_geometry():
    data = replace_marks(workspace(), primitive_marks())
    result = evaluate("console.log(JSON.stringify(f.project(w,w.panels[0].id).bounds));", data)
    assert result[0] <= -5 and result[1] >= 4
    assert result[2] <= -4 and result[3] >= 7


def test_bezier_bounds_include_the_saved_control_point_envelope():
    data = replace_marks(workspace(), [primitive_marks()[7]])
    result = evaluate("console.log(JSON.stringify(f.project(w,w.panels[0].id).bounds));", data)
    assert result[2] <= 0 and result[3] >= 10


@pytest.mark.parametrize("capability", ["spatial_expression", "rna_velocity", "multimodal_features",
                                        "embedding_hexbin", "saved_embedding", "embedding_density_contours",
                                        "embedding_density_centroids"])
def test_comparable_embedding_coordinates_preserve_equal_aspect(capability):
    data = workspace()
    data["panels"][0]["capability"] = capability
    for axis in data["panels"][0]["axes"].values():
        axis.update(unit="coordinate units", transform="identity")
    result = evaluate("console.log(JSON.stringify(f.project(w,w.panels[0].id).equalAspect));", data)
    assert result is True


def test_multimodal_features_do_not_force_equal_aspect_for_different_units():
    data = workspace()
    data["panels"][0]["capability"] = "multimodal_features"
    assert evaluate("console.log(JSON.stringify(f.project(w,w.panels[0].id).equalAspect));", data) is False


def test_explicit_categories_are_preserved_without_coercing_numeric_labels():
    data = workspace()
    data["panels"][0]["x_categories"] = ["10 days", "2 days", "01", "missing group"]
    data["panels"][0]["y_categories"] = ["feature-2", "feature-1"]
    result = evaluate("const m=f.project(w,w.panels[0].id);console.log(JSON.stringify({x:m.xLabels,y:m.yLabels}));", data)
    assert result == {"x": data["panels"][0]["x_categories"], "y": data["panels"][0]["y_categories"]}


def test_cropping_never_drops_crossing_mark_from_uncropped_membership():
    data = replace_marks(workspace(), [{"type": "line", "x1": 1, "y1": 2, "x2": 3, "y2": 4,
                                      "keys": [r["key"] for r in workspace()["panels"][0]["rows"][:2]], "group": ""}])
    result = evaluate("const m=f.project(w,w.panels[0].id),g=geometry(m,[1.8,2.2,2.8,3.2]),ctx=recordingContext();const d=f.drawMarks(ctx,m,g,{});console.log(JSON.stringify({drawn:d.drawnMarks,omitted:d.omittedMarks,svg:f.svgMarks(m,g,{},'current')}));", data)
    assert result["drawn"] == 1 and result["omitted"] == []
    assert "<line" in result["svg"] or "<path" in result["svg"]


def test_filtering_any_member_omits_saved_aggregate_without_synthetic_recomputation():
    data = workspace()
    marks = data["panels"][0]["marks"] + [{"type": "polygon", "points": [[1, 0], [2, 4], [3, 0]],
                                          "keys": [r["key"] for r in data["panels"][0]["rows"][:2]], "group": ""}]
    replace_marks(data, marks)
    result = evaluate("const m=f.project(w,w.panels[0].id),g=geometry(m),ctx=recordingContext();const d=f.drawMarks(ctx,m,g,{group:'A'});console.log(JSON.stringify({drawn:d.drawnMarks,omitted:d.omittedMarks,current:f.svgMarks(m,g,{group:'A'},'current'),complete:f.svgMarks(m,g,{group:'A'},'complete')}));", data)
    assert result["drawn"] == 1 and len(result["omitted"]) == 2
    assert all(m["reason"] == "filtered_member" for m in result["omitted"])
    assert "<polygon" not in result["current"] and "<polygon" in result["complete"]


def test_selected_typed_link_and_key_do_not_merge_duplicate_biological_ids():
    result = evaluate("const m=f.project(w,w.panels[0].id),g=geometry(m);console.log(JSON.stringify([f.svgMarks(m,g,{status:'selected',selectedLink:{kind:'sample',id:m.rows[1].id},selectedKey:m.rows[0].key},'current'),f.svgMarks(m,g,{status:'selected',selectedKey:m.rows[0].key},'current'),f.svgMarks(m,g,{status:'selected',selectedId:m.rows[0].id},'current')]));")
    keys = [[json.loads(e.get("data-source-keys")) for e in svg_elements(s) if e.get("data-source-keys")] for s in result]
    assert keys[0] == [["sample:key"]]
    assert keys[1] == [[workspace()["panels"][0]["rows"][0]["key"]]]
    assert keys[2] == []


def test_filters_keep_excluded_rows_in_model_without_calling_them_successful():
    result = evaluate("const m=f.project(w,w.panels[0].id),g=geometry(m),ctx=recordingContext();const d=f.drawMarks(ctx,m,g,{status:'excluded'});console.log(JSON.stringify({count:m.rows.length,drawn:d.drawnMarks,keys:d.screenPoints.map(p=>p.row.key),warnings:f.svgMarks(m,g,{status:'warnings'},'current')}));")
    assert result["count"] == 3 and result["drawn"] == 0 and result["keys"] == []
    assert "sample:key" in result["warnings"]


@pytest.mark.parametrize("palette,expected", [
    ("viridis", ["#440154", "#21918c", "#fde725"]),
    ("magma", ["#000004", "#b73779", "#fcfdbf"]),
])
def test_true_palette_samples_and_colorbar_units(palette, expected):
    data = workspace()
    data["panels"][0]["color_scale"] = {"label": "Saved abundance", "unit": "log10 counts", "palette": palette, "domain": [0, 10], "center": None}
    result = evaluate("const m=f.project(w,w.panels[0].id);console.log(JSON.stringify({colors:[0,5,10].map(value=>f.color(m,{group:'A',value})),legend:f.legend(m),svg:f.svgLegend(m,{x:10,y:20,width:250})}));", data)
    assert result["colors"] == expected
    assert result["legend"]["color"]["domain"] == [0, 10]
    assert result["legend"]["color"]["unit"] == "log10 counts"
    assert "log10 counts" in result["svg"] and "<image" not in result["svg"]


def test_symmetric_diverging_scale_and_area_size_mapping():
    data = workspace()
    panel = data["panels"][0]
    panel["color_scale"] = {"label": "Saved effect", "unit": "log2 ratio", "palette": "diverging", "domain": [-4, 4], "center": 0}
    panel["size_scale"] = {"label": "Observed fraction", "unit": "%", "domain": [0, 100], "max_radius": 10}
    panel["marks"][0]["size_value"] = 25
    panel["marks"][1]["size_value"] = 100
    result = evaluate("const m=f.project(w,w.panels[0].id);console.log(JSON.stringify({colors:[-4,0,4].map(value=>f.color(m,{group:'A',value})),svg:f.svgMarks(m,geometry(m),{},'complete'),legend:f.legend(m)}));", data)
    assert result["colors"] == ["#053061", "#f7f6f6", "#67001f"]
    radii = [float(e.get("r")) for e in svg_elements(result["svg"]) if e.tag == "circle"]
    assert radii == [5, 10]
    sizes = result["legend"]["size"]["values"]
    assert all(s["radius"] ** 2 / 100 == pytest.approx(s["value"] / 100) for s in sizes)


def test_more_than_eight_groups_get_distinct_shape_pattern_mappings():
    result = evaluate("const p=w.panels[0];p.rows=Array.from({length:40},(_,i)=>({...p.rows[0],id:'g'+i,key:'key'+i,group:'group'+String(i).padStart(2,'0'),link:{kind:'gene',id:'g'+i}}));p.marks=p.rows.map(r=>({type:'point',keys:[r.key],group:r.group,x:r.x,y:r.y,radius:4}));p.coverage={source_rows:40,coordinate_rows:40,missing_rows:0,mark_count:40,truncated:false};const m=f.project(w,p.id);console.log(JSON.stringify({legend:f.legend(m),svg:f.svgMarks(m,geometry(m),{},'complete')}));")
    mappings = result["legend"]["categories"]
    assert len(mappings) == 40
    assert len({(d["color"], d["shape"], d["pattern"]) for d in mappings}) == 40
    assert {d["shape"] for d in mappings} == {"circle", "square", "diamond", "triangle"}
    assert len({d["pattern"] for d in mappings}) > 1
    assert "<pattern" in result["svg"]


def test_declared_styles_and_literal_text_are_safe_and_not_reinterpreted():
    data = replace_marks(workspace(), [primitive_marks()[-1], primitive_marks()[1]])
    data["panels"][0]["marks"][0]["style"] = {"fill": "#123456", "opacity": .6}
    data["panels"][0]["marks"][1]["style"] = {"stroke": "#abcdef", "stroke_width": 2.5, "dash": [3, 2]}
    result = evaluate("const m=f.project(w,w.panels[0].id);console.log(JSON.stringify(f.svgMarks(m,geometry(m),{},'complete')));", data)
    elems = svg_elements(result)
    assert sum(e.text == "glyph <&\"'" for e in elems) == 1
    assert any(e.get("text-anchor") == "middle" for e in elems)
    assert any(e.get("stroke-width") == "2.5" and e.get("stroke-dasharray") == "3 2" for e in elems)
    assert "<script" not in result and "<image" not in result


def test_thousands_of_points_have_no_implicit_sampling():
    result = evaluate("const p=w.panels[0];p.rows=Array.from({length:12000},(_,i)=>({...p.rows[0],id:'g'+i,key:'key'+i,x:i,y:i%10,link:{kind:'gene',id:'g'+i}}));p.marks=p.rows.map(r=>({type:'point',keys:[r.key],group:r.group,x:r.x,y:r.y,radius:2}));p.coverage={source_rows:p.rows.length,coordinate_rows:p.rows.length,missing_rows:0,mark_count:p.rows.length,truncated:false};const m=f.project(w,p.id),ctx=recordingContext(),draw=f.drawMarks(ctx,m,geometry(m),{}),svg=f.svgMarks(m,geometry(m),{},'complete');console.log(JSON.stringify({rows:m.rows.length,drawn:draw.drawnMarks,points:draw.screenPoints.length,svgMarks:(svg.match(/data-source-keys=/g)||[]).length,omitted:draw.omittedMarks.length}));")
    assert result == {"rows": 12000, "drawn": 12000, "points": 12000, "svgMarks": 12000, "omitted": 0}


@pytest.mark.parametrize("palette", ["viridis", "magma", "RdBu_r", "coolwarm"])
def test_every_palette_entry_matches_installed_matplotlib(palette):
    from matplotlib import colormaps, colors
    data = workspace()
    diverging = palette in {"RdBu_r", "coolwarm"}
    data["panels"][0]["color_scale"] = {"label": "Saved value", "unit": "units", "palette": palette,
                                         "domain": [-1, 1] if diverging else [0, 1], "center": 0 if diverging else None}
    result = evaluate("const m=f.project(w,w.panels[0].id);console.log(JSON.stringify(f.legend(m).color.stops.map(s=>s.color)));", data)
    assert result == [colors.to_hex(colormaps[palette](i)) for i in range(256)]


def test_finite_excluded_row_keeps_its_status_reason_and_membership():
    data = workspace()
    data["panels"][0]["rows"][0].update(status="excluded", reason="Failed independent saved assessment")
    data["panels"][0]["coverage"]["missing_rows"] = 2
    result = evaluate("const m=f.project(w,w.panels[0].id);console.log(JSON.stringify({r:m.rows[0],all:f.svgMarks(m,geometry(m),{},'complete'),recorded:f.svgMarks(m,geometry(m),{status:'recorded'},'current')}));", data)
    assert result["r"]["x"] == 1 and result["r"]["status"] == "excluded"
    assert len([e for e in svg_elements(result["all"]) if e.get("data-source-keys")]) == 2
    assert len([e for e in svg_elements(result["recorded"]) if e.get("data-source-keys")]) == 1


def test_explicit_no_fill_is_honored_without_changing_an_independent_stroke():
    data = replace_marks(workspace(), [primitive_marks()[-1], primitive_marks()[4]])
    for mark in data["panels"][0]["marks"]:
        mark["style"] = {"fill": "none", "stroke": "#123456", "stroke_width": 2}
    result = evaluate("const m=f.project(w,w.panels[0].id),ctx=recordingContext();f.drawMarks(ctx,m,geometry(m),{});console.log(JSON.stringify({svg:f.svgMarks(m,geometry(m),{},'complete'),calls:ctx.calls}));", data)
    assert not any(call[0] in {"fill", "fillText"} for call in result["calls"])
    assert any(call[0] == "strokeText" for call in result["calls"])
    assert all(e.get("fill") == "none" and e.get("stroke") == "#123456" for e in svg_elements(result["svg"]) if e.tag in {"text", "polygon"})


def test_current_key_filter_is_exact_while_complete_retains_saved_membership():
    result = evaluate("const m=f.project(w,w.panels[0].id),g=geometry(m);console.log(JSON.stringify([f.svgMarks(m,g,{keys:[m.rows[1].key]},'current'),f.svgMarks(m,g,{keys:[m.rows[1].key]},'complete')]));")
    assert [len([e for e in svg_elements(s) if e.get("data-source-keys")]) for s in result] == [1, 2]


@pytest.mark.parametrize("family", ["bulk", "expression", "advanced", "spatial"])
def test_real_adapter_fixtures_validate_and_draw_every_saved_mark(family):
    fixtures = importlib.import_module("tests.test_plot_adapters_" + family).fixture_packets()
    adapter = importlib.import_module("devbench.analysis.plot_adapters_" + family)
    workspaces = []
    for packet in fixtures:
        data = workspace()
        data["title"] = "Synthetic renderer integration: " + packet["capability"]
        data["panels"] = adapter.build_panels(packet["capability"], packet["data"])
        workspaces.append(data)
    result = evaluate(JS_IMAGE_LOADER + """
(async()=>{
const results=[];
for(const workspace of w) {await f.loadAssets(workspace);for(const panel of workspace.panels) {
  try {
    const m=f.project(workspace,panel.id),g=geometry(m),ctx=recordingContext();
    const d=f.drawMarks(ctx,m,g,{}),svg=f.svgMarks(m,g,{},'complete');
    results.push({id:panel.id,rows:m.rows.length,sourceRows:panel.rows.length,
      marks:panel.marks.length,drawn:d.drawnMarks,omitted:d.omittedMarks.length,
      svgCount:(svg.match(/data-source-keys=/g)||[]).length});
  } catch(error) {throw new Error(workspace.title+' / '+panel.id+': '+error.message);}
}}
console.log(JSON.stringify(results));
})();
""", workspaces)
    assert result
    for panel in result:
        assert panel["rows"] == panel["sourceRows"], panel
        assert panel["drawn"] == panel["svgCount"] == panel["marks"], panel
        assert panel["omitted"] == 0, panel


def test_separate_panels_use_distinct_svg_pattern_identifiers():
    result = evaluate("""
const p=w.panels[0];p.rows=Array.from({length:24},(_,i)=>({...p.rows[0],id:'g'+i,key:'k'+i,group:'group'+String(i).padStart(2,'0')}));
p.marks=p.rows.map(r=>({type:'point',x:r.x,y:r.y,radius:4,keys:[r.key],group:r.group}));
p.coverage={source_rows:24,coordinate_rows:24,missing_rows:0,mark_count:24,truncated:false};
const a=f.project(w,p.id),sa=f.svgMarks(a,geometry(a),{},'complete');
p.id='other-panel';const b=f.project(w,p.id),sb=f.svgMarks(b,geometry(b),{},'complete');
console.log(JSON.stringify([sa,sb]));
""")
    identifiers = [{e.get("id") for e in svg_elements(s) if e.get("id")} for s in result]
    assert identifiers[0] and identifiers[1]
    assert identifiers[0].isdisjoint(identifiers[1])


@pytest.mark.parametrize("family", ["bulk", "expression", "advanced", "spatial"])
def test_all_fixture_aspect_flags_match_vector_export(family):
    from devbench.analysis.scientific_plot_artifacts import _equal_aspect
    fixtures = importlib.import_module("tests.test_plot_adapters_" + family).fixture_packets()
    adapter = importlib.import_module("devbench.analysis.plot_adapters_" + family)
    packets = []
    expected = []
    for packet in fixtures:
        data = workspace()
        data["panels"] = adapter.build_panels(packet["capability"], packet["data"])
        packets.append(data)
        expected.extend({"id": p["id"], "equalAspect": _equal_aspect(p)} for p in data["panels"])
    result = evaluate("console.log(JSON.stringify(w.flatMap(s=>s.panels.map(p=>({id:p.id,equalAspect:f.project(s,p.id).equalAspect})))));", packets)
    assert result == expected


def test_actual_legend_preserves_explicit_colors_point_shapes_and_line_variants():
    data = workspace()
    first = data["panels"][0]["marks"][0]
    first.update(style={"fill": "#2563eb"})
    second = {**first, "shape": "triangle", "style": {"fill": "#b45309"}}
    line = {"type": "line", "x1": 0, "y1": 0, "x2": 1, "y2": 1,
            "keys": first["keys"], "group": "A", "style": {"stroke": "#123456", "dash": [3, 2]}}
    replace_marks(data, [first, second, line])
    result = evaluate("const m=f.project(w,w.panels[0].id);console.log(JSON.stringify({legend:f.legend(m),svg:f.svgLegend(m,{x:0,y:0,width:260})}));", data)
    entries = result["legend"]["categories"]
    assert len(entries) == 3
    assert {(e["color"], e["shape"], e["symbol"]) for e in entries} == {
        ("#2563eb", "circle", "point"), ("#b45309", "triangle", "point"), ("#123456", "circle", "line")}
    assert {tuple(e["markIndices"]) for e in entries} == {(0,), (1,), (2,)}
    assert len({e["label"] for e in entries}) == 3
    assert all(e["group"] == "A" for e in entries)
    assert any(e.tag == "line" and e.get("stroke") == "#123456" and e.get("stroke-dasharray") == "3 2" for e in svg_elements(result["svg"]))


def test_continuous_color_marks_use_colorbar_without_invented_category_colors():
    data = workspace()
    panel = data["panels"][0]
    panel["color_scale"] = {"label": "Saved value", "unit": "units", "palette": "viridis", "domain": [0, 10], "center": None}
    panel["marks"][0]["value"] = 2
    panel["marks"][1]["value"] = 8
    result = evaluate("const m=f.project(w,w.panels[0].id);console.log(JSON.stringify(f.legend(m)));", data)
    assert result["color"]["domain"] == [0, 10]
    assert result["categories"] == []


def test_continuous_color_keeps_distinct_category_shapes_with_an_explicit_colorbar_label():
    result = evaluate("""
const p=w.panels[0];p.rows=Array.from({length:9},(_,i)=>({...p.rows[0],id:'g'+i,key:'k'+i,group:'group'+i}));
p.marks=p.rows.map((r,i)=>({type:'point',x:r.x,y:r.y,radius:4,keys:[r.key],group:r.group,value:i}));
p.coverage={source_rows:9,coordinate_rows:9,missing_rows:0,mark_count:9,truncated:false};
p.color_scale={label:'Saved expression',unit:'counts',palette:'viridis',domain:[0,8],center:null};
const m=f.project(w,p.id);console.log(JSON.stringify(f.legend(m)));
""")
    assert len(result["categories"]) == 9
    assert [entry["shape"] for entry in result["categories"]] == ["circle"] * 8 + ["square"]
    assert all(entry["continuous"] and "color: continuous scale" in entry["label"] for entry in result["categories"])
    assert result["color"]["domain"] == [0, 8]


def test_continuous_fill_warning_triangle_keeps_shape_and_explicit_outline():
    data = workspace()
    panel = data["panels"][0]
    panel["color_scale"] = {"label": "Saved value", "unit": "units", "palette": "viridis", "domain": [0, 10], "center": None}
    panel["marks"][0].update(value=2, shape="triangle", style={"stroke": "#ff0000"})
    panel["marks"][1]["value"] = 8
    result = evaluate("const m=f.project(w,w.panels[0].id);console.log(JSON.stringify(f.legend(m)));", data)
    triangle = next(entry for entry in result["categories"] if entry["shape"] == "triangle")
    assert triangle["stroke"] == "#ff0000"
    assert triangle["continuous"] and "color: continuous scale" in triangle["label"]


@pytest.mark.parametrize("family", ["bulk", "expression", "advanced", "spatial"])
def test_actual_legend_glyphs_match_all_fixture_saved_mark_styles(family):
    fixtures = importlib.import_module("tests.test_plot_adapters_" + family).fixture_packets()
    adapter = importlib.import_module("devbench.analysis.plot_adapters_" + family)
    packets = []
    for packet in fixtures:
        data = workspace()
        data["panels"] = adapter.build_panels(packet["capability"], packet["data"])
        packets.append(data)
    result = evaluate(JS_IMAGE_LOADER + """
(async()=>{for(const workspace of w) await f.loadAssets(workspace);
console.log(JSON.stringify(w.flatMap(s=>s.panels.map(p=>{
  const m=f.project(s,p.id);
  return {id:p.id,marks:p.marks,colorScale:p.color_scale,categories:f.legend(m).categories,
    svg:f.svgMarks(m,geometry(m),{},'complete')};
}))));
})();
""", packets)
    closed_types = {"point", "rect", "ellipse", "polygon", "text"}
    for panel in result:
        drawn = {int(e.get("data-mark-index")): e[0] for e in svg_elements(panel["svg"]) if e.get("data-mark-index") is not None}
        indexed = {i: entry for entry in panel["categories"] for i in entry["markIndices"]}
        for index, mark in enumerate(panel["marks"]):
            ink = drawn[index]
            filled = mark["type"] in closed_types and ink.get("fill") != "none"
            ink_field = "fill" if filled else "stroke"
            visible = ink.get(ink_field, "none") != "none" and float(ink.get("opacity", 1)) > 0
            continuous = panel["colorScale"] is not None and panel["colorScale"]["palette"] not in {"OkabeIto", "okabe_ito"} and mark.get("value") is not None and ink_field not in mark.get("style", {})
            if not mark["group"] or not visible:
                assert index not in indexed, (panel["id"], index, mark)
                continue
            if continuous and index not in indexed:
                continue
            assert index in indexed, (panel["id"], index, mark)
            entry = indexed[index]
            if continuous:
                assert entry["continuous"] and "color: continuous scale" in entry["label"]
                if "stroke" in mark.get("style", {}):
                    assert entry["stroke"] == mark["style"]["stroke"]
            else:
                assert entry["fill"] == ink.get("fill") and entry["stroke"] == ink.get("stroke"), (panel["id"], index, entry, ink.attrib)
            if mark["type"] == "point":
                shape = "circle" if ink.tag == "circle" else "square" if ink.tag == "rect" else "triangle" if len(ink.get("points").split()) == 3 else "diamond"
                assert entry["shape"] == shape, (panel["id"], index, entry)
            assert entry["symbol"] == ("line" if mark["type"] in {"line", "arrow", "polyline", "arc"} else "point")


def test_legend_layout_wraps_long_labels_and_fits_large_size_examples():
    data = workspace()
    panel = data["panels"][0]
    panel["rows"][0]["group"] = panel["marks"][0]["group"] = "very_long_saved_condition_identifier_" * 5
    panel["size_scale"] = {"label": "Saved size", "unit": "observed fraction", "domain": [0, 100], "max_radius": 100}
    result = evaluate("const m=f.project(w,w.panels[0].id);console.log(JSON.stringify({height:f.legendHeight(m,{width:850}),svg:f.svgLegend(m,{x:26,y:10,width:850})}));", data)
    elements = svg_elements(result["svg"])
    for element in elements:
        if element.tag == "circle":
            assert 26 <= float(element.get("cx")) - float(element.get("r"))
            assert float(element.get("cx")) + float(element.get("r")) <= 876
        if element.tag == "text":
            assert float(element.get("y")) <= result["height"] + 10
    long_fragments = [e.text for e in elements if e.tag == "text" and "very_" in (e.text or "")]
    assert len(long_fragments) >= 2
    assert result["height"] > 260


def test_current_legend_uses_submitted_marks_and_complete_palette_mapping():
    result = evaluate("""
const p=w.panels[0];p.rows=Array.from({length:9},(_,i)=>({...p.rows[0],id:'g'+i,key:'k'+i,group:'group'+i}));
p.marks=p.rows.map(r=>({type:'point',x:r.x,y:r.y,radius:4,keys:[r.key],group:r.group}));
p.coverage={source_rows:9,coordinate_rows:9,missing_rows:0,mark_count:9,truncated:false};
const m=f.project(w,p.id),state={group:'group8'};
console.log(JSON.stringify({all:f.legend(m).categories,current:f.legend(m,state,'current').categories,
  currentHeight:f.legendHeight(m,{width:260,state,scope:'current'}),allHeight:f.legendHeight(m,{width:260}),
  svg:f.svgLegend(m,{width:260,state,scope:'current'})}));
""")
    assert len(result["current"]) == 1
    for key in ("color", "shape", "pattern", "fill", "stroke", "markIndices"):
        assert result["current"][0][key] == result["all"][8][key]
    assert result["currentHeight"] < result["allHeight"]
    labels = [e.get("data-legend-group") for e in svg_elements(result["svg"]) if e.get("data-legend-group")]
    assert labels == ["group8"]


def test_registered_image_preserves_rows_affine_extent_and_physical_aspect():
    result = evaluate("const m=f.project(w,w.panels[0].id);console.log(JSON.stringify({rows:m.rows.length,bounds:m.bounds,equalAspect:m.equalAspect,legend:f.legend(m)}));", image_workspace())
    assert result["rows"] == 3
    assert result["bounds"][0] <= 10 and result["bounds"][1] >= 14
    assert result["bounds"][2] <= 24 and result["bounds"][3] >= 30
    assert result["equalAspect"] is True
    assert {entry["group"] for entry in result["legend"]["categories"]} == {"A", "B"}


@pytest.mark.parametrize("mutation", [
    "m.context=false", "m.keys=[w.panels[0].rows[0].key]", "m.role='scientific_heatmap'",
    "m.group='other'", "m.style={opacity:.5}", "m.image.url='https://example.test/a.png'",
    "m.image.sha256='BAD'", "m.image.width=3", "m.image.height=true", "m.image.width=20000",
    "m.image.pixel_to_data=[1,2,2,4,0,0]", "m.image.pixel_to_data=[1,0,0,1,Infinity,0]",
    "m.image.coordinate_unit='pixels'", "m.image.coordinate_system_id=''",
    "m.image.registration_id=''", "m.image.source.kind='clinical'", "delete m.image.source.section_id",
    "m.image.png_base64='https://example.test/a.png'", "m.image.png_base64+='\\n'",
    "w.panels[0].axes.x.unit='mm'",
])
def test_image_primitive_rejects_invalid_context_registration_and_png_contract(mutation):
    result = evaluate("const m=w.panels[0].marks[0];" + mutation + ";let refused=false;try{f.prepareWorkspace(w);}catch(e){refused=true;}console.log(JSON.stringify(refused));", image_workspace())
    assert result is True


def test_image_content_sha_is_verified_before_decode_or_vector_embedding():
    data = image_workspace()
    data["panels"][0]["marks"][0]["image"]["sha256"] = "0" * 64
    result = evaluate(JS_IMAGE_LOADER + "(async()=>{let error=null;try{await f.loadAssets(w);}catch(e){error=e.message;}console.log(JSON.stringify({error,decodeCalls}));})();", data)
    assert "SHA" in result["error"] and result["decodeCalls"] == 0


def test_verified_intrinsic_image_stays_raster_while_scientific_marks_stay_vector():
    data = image_workspace()
    result = evaluate(JS_IMAGE_LOADER + """
(async()=>{await f.loadAssets(w);const m=f.project(w,w.panels[0].id),g=geometry(m,[10,14,24,30]),ctx=recordingContext();
const d=f.drawMarks(ctx,m,g,{});console.log(JSON.stringify({svg:f.svgMarks(m,g,{},'complete'),calls:ctx.calls,drawn:d.drawnMarks,rows:m.rows.length,decodeCalls}));})();
""", data)
    elements = svg_elements(result["svg"])
    images = [e for e in elements if e.tag == "image"]
    assert len(images) == 1 and len([e for e in elements if e.tag == "circle"]) == 2
    image = images[0]
    payload = data["panels"][0]["marks"][0]["image"]
    assert image.get("href") == "data:image/png;base64," + payload["png_base64"]
    assert image.get("data-image-sha256") == payload["sha256"]
    assert image.get("data-role") == "tissue_reference"
    assert image.get("width") == "2" and image.get("height") == "2"
    assert image.get("transform") == "matrix(220 0 0 165 40 30)"
    assert [call[1:] for call in result["calls"] if call[0] == "transform"] == [[220, 0, 0, 165, 40, 30]]
    assert sum(call[0] == "drawImage" for call in result["calls"]) == 1
    assert result["drawn"] == 3 and result["rows"] == 3 and result["decodeCalls"] == 1


def test_context_image_bypasses_filters_without_adding_biological_selection_points():
    result = evaluate(JS_IMAGE_LOADER + """
(async()=>{await f.loadAssets(w);const m=f.project(w,w.panels[0].id),g=geometry(m),ctx=recordingContext();
const state={status:'excluded',group:'absent',keys:[]},d=f.drawMarks(ctx,m,g,state);
console.log(JSON.stringify({drawn:d.drawnMarks,points:d.screenPoints.length,omitted:d.omittedMarks.length,svg:f.svgMarks(m,g,state,'current')}));})();
""", image_workspace())
    assert result["drawn"] == 1 and result["points"] == 0 and result["omitted"] == 2
    assert sum(e.tag == "image" for e in svg_elements(result["svg"])) == 1
    assert not any(e.tag == "circle" for e in svg_elements(result["svg"]))


def test_image_assets_are_decoded_once_per_workspace_and_released():
    result = evaluate(JS_IMAGE_LOADER + """
(async()=>{await Promise.all([f.loadAssets(w),f.loadAssets(w)]);await f.loadAssets(w);
const before={decodeCalls,closeCalls};f.disposeAssets(w);const after={decodeCalls,closeCalls};
await f.loadAssets(w);f.disposeAssets(w);console.log(JSON.stringify({before,after,final:{decodeCalls,closeCalls}}));})();
""", image_workspace())
    assert result == {"before": {"decodeCalls": 1, "closeCalls": 0}, "after": {"decodeCalls": 1, "closeCalls": 1},
                      "final": {"decodeCalls": 2, "closeCalls": 2}}


def test_unloaded_or_mutated_images_cannot_be_silently_drawn_or_embedded():
    result = evaluate(JS_IMAGE_LOADER + """
(async()=>{const m=f.project(w,w.panels[0].id);let unloaded=false,mutated=false;
try{f.svgMarks(m,geometry(m),{},'complete');}catch(e){unloaded=true;}
await f.loadAssets(w);m.scientificPanel.marks[0].image.png_base64+='AAAA';
try{f.svgMarks(m,geometry(m),{},'complete');}catch(e){mutated=true;}
console.log(JSON.stringify({unloaded,mutated}));})();
""", image_workspace())
    assert result == {"unloaded": True, "mutated": True}


@pytest.mark.parametrize("shape", ["circle", "square", "triangle", "diamond"])
def test_full_view_pixel_padding_keeps_large_glyphs_within_plot(shape):
    data = workspace()
    for mark in data["panels"][0]["marks"]:
        mark.update(radius=100, shape=shape, style={"stroke_width": 4})
    result = evaluate("const m=f.project(w,w.panels[0].id),b=f.paddedBounds(m,440,330);console.log(JSON.stringify({bounds:b,svg:f.svgMarks(m,geometry(m,b),{},'complete')}));", data)
    elements = svg_elements(result["svg"])
    for e in elements:
        if e.tag == "circle":
            x, y, r = float(e.get("cx")), float(e.get("cy")), float(e.get("r")) + 2
            assert 40 <= x - r <= x + r <= 480
            assert 30 <= y - r <= y + r <= 360
        elif e.tag == "rect":
            assert 40 <= float(e.get("x")) - 2 and float(e.get("x")) + float(e.get("width")) + 2 <= 480
            assert 30 <= float(e.get("y")) - 2 and float(e.get("y")) + float(e.get("height")) + 2 <= 360
        elif e.tag == "polygon":
            for coordinate in e.get("points").split():
                x, y = map(float, coordinate.split(","))
                assert 42 <= x <= 478 and 32 <= y <= 358


def test_pixel_padding_refuses_a_box_physically_smaller_than_the_saved_glyph():
    result = evaluate("w.panels[0].marks[0].radius=100;const m=f.project(w,w.panels[0].id);let refused=false;try{f.paddedBounds(m,150,150);}catch(e){refused=true;}console.log(JSON.stringify(refused));")
    assert result is True


@pytest.mark.parametrize("corruption", ["magic", "truncated", "trailing", "animated", "pixel_budget"])
def test_png_headers_chunks_and_dimension_budget_are_checked_synchronously(corruption):
    from tests.test_plot_adapters_spatial import _chunk
    data = image_workspace()
    payload = data["panels"][0]["marks"][0]["image"]
    raw = base64.b64decode(payload["png_base64"])
    if corruption == "magic":
        raw = b"not PNG!" + raw[8:]
    elif corruption == "truncated":
        raw = raw[:-7]
    elif corruption == "trailing":
        raw += b"trailing"
    elif corruption == "animated":
        raw = raw[:33] + _chunk(b"acTL", b"\0\0\0\1\0\0\0\0") + raw[33:]
    else:
        payload["width"] = payload["height"] = 3000
    payload["png_base64"] = base64.b64encode(raw).decode()
    result = evaluate("let refused=false;try{f.prepareWorkspace(w);}catch(e){refused=true;}console.log(JSON.stringify(refused));", data)
    assert result is True


def test_rotated_tissue_registration_keeps_pixel_edge_order():
    data = image_workspace()
    data["panels"][0]["marks"][0]["image"]["pixel_to_data"] = [0, 2, 3, 0, 10, 20]
    result = evaluate(JS_IMAGE_LOADER + """
(async()=>{await f.loadAssets(w);const m=f.project(w,w.panels[0].id),g=geometry(m,[10,16,20,24]);
console.log(JSON.stringify({bounds:m.bounds,svg:f.svgMarks(m,g,{},'complete')}));})();
""", data)
    image = next(e for e in svg_elements(result["svg"]) if e.tag == "image")
    matrix = [float(v) for v in image.get("transform")[7:-1].split()]
    assert matrix == pytest.approx([0, -165, 220, 0, 40, 360])
    assert result["bounds"][0] <= 10 and result["bounds"][1] >= 16
    assert result["bounds"][2] <= 20 and result["bounds"][3] >= 24


def test_disposing_pending_image_load_closes_its_decoded_resource():
    result = evaluate("""
let decodeStarted,finishDecode,closed=0;const started=new Promise(resolve=>{decodeStarted=resolve;});
globalThis.createImageBitmap=()=>{decodeStarted();return new Promise(resolve=>{finishDecode=resolve;});};
(async()=>{const loading=f.loadAssets(w);await started;f.disposeAssets(w);
finishDecode({width:2,height:2,close(){closed++;}});let error=null;
try{await loading;}catch(e){error=e.message;}
console.log(JSON.stringify({closed,error}));})();
""", image_workspace())
    assert result["closed"] == 1 and "disposed" in result["error"]


def test_native_decode_dimension_mismatch_is_refused_and_resource_closed():
    result = evaluate("""
let closed=0;globalThis.createImageBitmap=async()=>({width:3,height:2,close(){closed++;}});
(async()=>{let error=null;try{await f.loadAssets(w);}catch(e){error=e.message;}console.log(JSON.stringify({closed,error}));})();
""", image_workspace())
    assert result["closed"] == 1 and "dimensions" in result["error"]


@pytest.mark.parametrize("family", ["bulk", "expression", "advanced", "spatial"])
def test_full_glyph_padding_matches_python_exporter_for_all_fixture_panels(family):
    from devbench.analysis.scientific_plot_artifacts import _categories, _full_bounds, _padded_full_bounds, _pixel_padding
    fixtures = importlib.import_module("tests.test_plot_adapters_" + family).fixture_packets()
    adapter = importlib.import_module("devbench.analysis.plot_adapters_" + family)
    packets, expected = [], []
    for packet in fixtures:
        data = workspace()
        data["panels"] = adapter.build_panels(packet["capability"], packet["data"])
        packets.append(data)
        for panel in data["panels"]:
            pads = _pixel_padding(panel, _categories(panel))
            width, height = max(440, pads[0] + pads[1] + 50), max(330, pads[2] + pads[3] + 50)
            expected.append({"id": panel["id"], "pads": pads, "width": width, "height": height,
                             "bounds": _padded_full_bounds(panel, _full_bounds(panel), width, height)})
    result = evaluate("""
console.log(JSON.stringify(w.flatMap(s=>s.panels.map(p=>{
  const m=f.project(s,p.id),pad=f.pixelPadding(m),width=Math.max(440,pad.left+pad.right+50),height=Math.max(330,pad.bottom+pad.top+50);
  return {id:p.id,pads:[pad.left,pad.right,pad.bottom,pad.top],width,height,bounds:f.paddedBounds(m,width,height)};
}))));
""", packets)
    assert len(result) == len(expected)
    for actual, reference in zip(result, expected):
        assert actual["id"] == reference["id"]
        assert actual["pads"] == pytest.approx(reference["pads"]), actual["id"]
        assert actual["bounds"] == pytest.approx(reference["bounds"]), actual["id"]


def test_arrowhead_padding_remains_when_saved_shaft_width_is_zero():
    data = replace_marks(workspace(), [{**primitive_marks()[2], "style": {"stroke_width": 0}}])
    result = evaluate("console.log(JSON.stringify(f.pixelPadding(f.project(w,w.panels[0].id))));", data)
    assert result == {"left": 8, "right": 8, "bottom": 8, "top": 8}


def test_an_image_alone_has_no_scientific_pixel_glyph_padding():
    data = image_workspace()
    replace_marks(data, [data["panels"][0]["marks"][0]])
    result = evaluate("console.log(JSON.stringify(f.pixelPadding(f.project(w,w.panels[0].id))));", data)
    assert result == {"left": 0, "right": 0, "bottom": 0, "top": 0}


def png_payload(width, height, color, *, compress_level=6):
    from hashlib import sha256
    from io import BytesIO
    from PIL import Image
    payload = deepcopy(image_workspace()["panels"][0]["marks"][0]["image"])
    output = BytesIO()
    Image.new("RGB", (width, height), color).save(output, format="PNG", compress_level=compress_level)
    raw = output.getvalue()
    payload.update(width=width, height=height, png_base64=base64.b64encode(raw).decode(), sha256=sha256(raw).hexdigest())
    return payload


def test_valid_png_payload_may_exceed_the_general_text_field_limit():
    data = image_workspace()
    payload = png_payload(100, 100, (20, 80, 120), compress_level=0)
    assert len(payload["png_base64"]) > 10000
    data["panels"][0]["marks"][0]["image"] = payload
    result = evaluate(JS_IMAGE_LOADER + "(async()=>{await f.loadAssets(w);console.log(JSON.stringify({decodeCalls,rows:f.project(w,w.panels[0].id).rows.length}));})();", data)
    assert result == {"decodeCalls": 1, "rows": 3}


def test_unique_decoded_pixel_budget_is_checked_before_any_native_decode():
    data = image_workspace()
    template = data["panels"][0]["marks"][0]
    replace_marks(data, [{**template, "image": png_payload(2000, 2000, (i, 10, 20))} for i in range(5)])
    result = evaluate(JS_IMAGE_LOADER + "(async()=>{let error=null;try{await f.loadAssets(w);}catch(e){error=e.message;}console.log(JSON.stringify({error,decodeCalls}));})();", data)
    assert result["error"] and "pixel" in result["error"].lower()
    assert result["decodeCalls"] == 0


def test_repeated_background_hash_counts_decoded_pixels_only_once():
    data = image_workspace()
    template = data["panels"][0]["marks"][0]
    template["image"] = png_payload(2000, 2000, (1, 10, 20))
    replace_marks(data, [deepcopy(template) for _ in range(5)])
    result = evaluate(JS_IMAGE_LOADER + "(async()=>{await f.loadAssets(w);console.log(JSON.stringify({decodeCalls}));})();", data)
    assert result["decodeCalls"] == 1


def test_context_mark_budget_is_checked_before_native_decode():
    data = image_workspace()
    template = data["panels"][0]["marks"][0]
    replace_marks(data, [deepcopy(template) for _ in range(257)])
    result = evaluate(JS_IMAGE_LOADER + "(async()=>{let error=null;try{await f.loadAssets(w);}catch(e){error=e.message;}console.log(JSON.stringify({error,decodeCalls}));})();", data)
    assert result["error"] and "context" in result["error"].lower()
    assert result["decodeCalls"] == 0


def test_stale_disposed_image_load_cannot_destroy_a_new_successful_cache():
    result = evaluate("""
let decodeCalls=0,startedOld,finishOld,closed=[];
const started=new Promise(resolve=>{startedOld=resolve;});
const bitmap=id=>({width:2,height:2,id,close(){closed.push(id);}});
globalThis.createImageBitmap=async()=>{
  const id=++decodeCalls;
  if(id===1){startedOld();return new Promise(resolve=>{finishOld=()=>resolve(bitmap(id));});}
  return bitmap(id);
};
(async()=>{const old=f.loadAssets(w).catch(error=>error.message);await started;f.disposeAssets(w);
await f.loadAssets(w);const model=f.project(w,w.panels[0].id);
const before=f.svgMarks(model,geometry(model),{},'complete').includes('<image');
finishOld();const oldError=await old;let after=false;
try{after=f.svgMarks(model,geometry(model),{},'complete').includes('<image');}catch(error){}
const closedBeforeFinalDispose=closed.slice();f.disposeAssets(w);
console.log(JSON.stringify({before,after,oldError,closedBeforeFinalDispose,closed}));})();
""", image_workspace())
    assert result["before"] is True and result["after"] is True
    assert "disposed" in result["oldError"]
    assert result["closedBeforeFinalDispose"] == [1]
    assert result["closed"] == [1, 2]


def test_unavailable_measurements_keep_the_registered_context_without_biological_marks():
    data = image_workspace()
    panel = data["panels"][0]
    replace_marks(data, [panel["marks"][0]])
    for row in panel["rows"]:
        row.update(x=None, y=None, status="excluded", reason="Expression measurements were not retained")
    panel["coverage"].update(coordinate_rows=0, missing_rows=3)
    panel["availability"] = {"status": "unavailable", "reason": "Expression measurements were not retained"}
    result = evaluate(JS_IMAGE_LOADER + """
(async()=>{await f.loadAssets(w);const m=f.project(w,w.panels[0].id),g=geometry(m),d=f.drawMarks(recordingContext(),m,g,{});
console.log(JSON.stringify({unavailable:m.unavailable,drawn:d.drawnMarks,points:d.screenPoints.length,rows:m.rows.length,svg:f.svgMarks(m,g,{},'complete')}));})();
""", data)
    assert result["unavailable"] == "Expression measurements were not retained"
    assert result["drawn"] == 1 and result["points"] == 0 and result["rows"] == 3
    assert sum(e.tag == "image" for e in svg_elements(result["svg"])) == 1
    assert not any(e.tag == "circle" for e in svg_elements(result["svg"]))
