"""Bulk plot interactions and exports preserve saved scientific identities."""
import json
from pathlib import Path
import shutil
import subprocess

import pytest


MODULE = Path(__file__).resolve().parents[1] / "devbench/analysis/assets/bulk-scientific-figure.js"


def run_js(code):
    assert MODULE.is_file(), "Bulk interactive renderer is not implemented"
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is required for the browser contract tests")
    output = subprocess.run([node, "-e", "const f=require(" + json.dumps(str(MODULE)) + ");\n" + code],
                            text=True, capture_output=True)
    assert output.returncode == 0, output.stderr
    return json.loads(output.stdout)


def workspace():
    samples = [{"sample_id": f"s{i}", "condition": "A" if i % 2 == 0 else "B",
                "unit_id": f"donor{i // 2}", "pair_id": f"donor{i // 2}", "size_factor": 1}
               for i in range(4)]
    genes = []
    for name, effect, p, q, counts in [("g<1", 2, .001, .003, [10, 40, 12, 48]),
                                       ("gene-two", -1, .2, .3, [30, 15, 40, 20]),
                                       ("unavailable", None, None, None, [0, 0, 0, 0])]:
        genes.append({"gene_id": name, "hypothesis_id": name, "effect": effect,
                      "interval": None, "p_value": p, "q_value": q,
                      "estimate_status": "estimated" if effect is not None else "failed",
                      "missing_reason": None if effect is not None else "all_zero",
                      "warnings": ["Recorded fit warning"] if name == "gene-two" else [],
                      "mean_normalized_count": sum(counts) / 4,
                      "dispersion": {"raw": .2, "trend": .1, "final": .12,
                                     "converged": True, "boundary": False}, "raw_counts": counts})
    return {"format": "devbench.bulk-plot-workspace/1",
            "source": {"sha256": "a" * 64, "run_id": "test-run", "kind": "synthetic"},
            "effect_definition": {"reference_condition": "A", "comparison_condition": "B",
                                  "effect_scale": "log2_relative_expression"},
            "effect_scale": "log2_relative_expression", "biological_unit": "donor", "genes": genes,
            "samples": samples, "pca": {"status": "available", "reason": None,
                "scores": [{"sample_id": s["sample_id"], "pc1": i, "pc2": i % 2} for i, s in enumerate(samples)],
                "explained_variance_ratio": [.7, .3], "explained_variance": [7, 3],
                "transform": "log2(normalized counts + 1)"},
            "availability": {key: {"status": "available", "reason": None}
                             for key in ["ma", "volcano", "dispersion", "pca", "paired_expression", "expression", "pvalue_histogram"]},
            "pairing": {"status": "paired", "reason": None, "unit_column": "donor", "condition_column": "condition"},
            "coverage": {"gene_count": 3, "sample_count": 4, "matrix_value_count": 12,
                         "retained_gene_count": 3, "truncated": False}, "limitations": ["Synthetic verification only"]}


def evaluate(code, data=None):
    return run_js("const w=" + json.dumps(data or workspace()) + ";\n" + code)


def test_workspace_projects_actual_values_and_retains_failed_genes():
    got = evaluate("const m=f.project(w,'volcano');console.log(JSON.stringify({ids:m.rows.map(r=>r.id),xy:m.rows[0],missing:m.rows[2]}));")
    assert got["ids"] == ["g<1", "gene-two", "unavailable"]
    assert got["xy"]["x"] == 2
    assert got["xy"]["y"] == pytest.approx(-__import__('math').log10(.003))
    assert got["missing"]["y"] is None
    assert got["missing"]["reason"]


def test_full_export_ignores_view_filters_and_discloses_undrawn():
    got = evaluate("const m=f.project(w,'volcano'),state={bounds:[-2,0,0,2],status:'warnings',selectedId:'gene-two'};console.log(JSON.stringify([f.exportSnapshot(m,state,'complete'),f.exportSnapshot(m,state,'current')]));")
    complete, current = got
    assert complete["displayed_ids"] == ["g<1", "gene-two"]
    assert complete["coverage"]["unplottable"] == 1
    assert complete["coverage"]["filtered"] == complete["coverage"]["cropped"] == 0
    assert current["displayed_ids"] == ["gene-two"]
    assert current["coverage"]["filtered"] == 1
    assert current["source"]["sha256"] == "a" * 64
    assert current["filters"]["status"] == "warnings"


def test_vector_svg_contains_no_raster_wrapper_and_escapes_labels():
    got = evaluate("const m=f.project(w,'ma');console.log(JSON.stringify(f.toSVG(m,{selectedId:'g<1'},'complete')));")
    assert "<image" not in got and "data:image" not in got
    assert "<circle" in got and "<metadata" in got
    assert "g&lt;1" in got and "Complete saved population" in got
    assert "Synthetic" in got


def test_zero_q_is_undrawn_without_invented_floor():
    data = workspace()
    data["genes"][0]["q_value"] = data["genes"][0]["p_value"] = 0
    got = evaluate("const m=f.project(w,'volcano');console.log(JSON.stringify({row:m.rows[0],scope:f.exportSnapshot(m,{},'complete')}));", data)
    assert got["row"]["y"] is None
    assert "zero" in got["row"]["reason"]
    assert got["scope"]["coverage"]["unplottable"] == 2


def test_pca_selection_uses_samples_and_variance_labels():
    got = evaluate("const m=f.project(w,'pca');console.log(JSON.stringify({unit:m.entityKind,ids:m.rows.map(r=>r.id),axes:m.axes}));")
    assert got["unit"] == "sample" and got["ids"] == ["s0", "s1", "s2", "s3"]
    assert "70" in got["axes"]["x"] and "30" in got["axes"]["y"]


def test_pair_connections_are_not_created_without_pairing_evidence():
    data = workspace()
    data["pairing"] = {"status": "unpaired", "reason": "Unit identity not supplied"}
    for sample in data["samples"]:
        sample["pair_id"] = None
    got = evaluate("const m=f.project(w,'paired_expression','g<1');console.log(JSON.stringify({connections:m.connections,rows:m.rows}));", data)
    assert got["connections"] == []
    assert len(got["rows"]) == 4


@pytest.mark.parametrize("mutation", [
    "w.genes[1].gene_id=w.genes[0].gene_id",
    "w.genes[0].q_value=.0001",
    "w.genes[0].effect=Infinity",
    "w.genes[0].raw_counts=[1]",
    "w.samples[0].size_factor=0",
    "w.pca.scores[1].sample_id=w.pca.scores[0].sample_id",
])
def test_workspace_rejects_ambiguous_or_invalid_data(mutation):
    got = evaluate(mutation + ";let refused=false;try{f.prepareWorkspace(w);}catch(e){refused=true;}console.log(JSON.stringify(refused));")
    assert got is True


def test_one_hundred_thousand_genes_are_not_sampled_for_export():
    got = evaluate("w.genes=Array.from({length:100000},(_,i)=>({...w.genes[0],gene_id:'g'+i,hypothesis_id:'g'+i,raw_counts:null}));w.coverage.gene_count=w.coverage.retained_gene_count=100000;const m=f.project(w,'volcano');console.log(JSON.stringify(f.exportSnapshot(m,{},'complete').coverage));")
    assert got["total"] == got["displayed"] == 100000


def test_generic_figure_two_preserves_missing_values_and_curve_identity():
    code = """
const data={format:'devbench.numeric-plot-source/2',plot_kind:'effect_scatter',entity_kind:'gene',axes:{x:{label:'Reference',unit:'log2',transform:'identity'},y:{label:'Actual',unit:'log2',transform:'identity'}},membership:['g1','g2'],rows:[{entity_id:'g1',coordinate_id:null,group_id:'A',x:{status:'exact',value:1,censoring:null},y:{status:'exact',value:2,censoring:null},interval:null},{entity_id:'g2',coordinate_id:null,group_id:'A',x:{status:'missing',value:null,censoring:null},y:{status:'missing',value:null,censoring:null},interval:null}],limitations:[]};
const m=f.prepareNumeric({format:'devbench.figure/2',figure_id:'effects',plot_kind:'effect_scatter'},{format:'devbench.figure-data/2',source_ref:{sha256:'a'.repeat(64)},data});console.log(JSON.stringify({kind:m.kind,count:m.rows.length,unplottable:f.exportSnapshot(m,{},'complete').coverage.unplottable}));
"""
    got = run_js(code)
    assert got == {"kind": "effect_scatter", "count": 2, "unplottable": 1}


def test_csv_preserves_all_source_rows_and_escapes_formula_ids():
    data = workspace()
    data["genes"][0]["gene_id"] = data["genes"][0]["hypothesis_id"] = "=bad()"
    got = evaluate("console.log(JSON.stringify(f.toCSV(f.project(w,'volcano'))));", data)
    assert "'=bad()" in got and "unavailable" in got and "all_zero" in got


@pytest.mark.parametrize("kind", ["ma", "volcano", "dispersion", "pca", "paired_expression", "pvalue_histogram"])
def test_browser_coordinates_match_actual_python_projections(kind):
    from tests.test_bulk_plot_data import result, SOURCE
    from devbench.analysis.bulk_plot_data import build_bulk_plot_workspace, numeric_plot_data
    from devbench.analysis.figure_artifacts import _transformed
    saved = build_bulk_plot_workspace(result(paired=True), source=SOURCE)
    numeric = numeric_plot_data(saved, kind, "g0")
    expected = [[_transformed(row[axis]["value"], numeric["axes"][axis]["transform"])
                 if row[axis]["status"] == "exact" else None for axis in ("x", "y")]
                for row in numeric["rows"]]
    actual = evaluate("console.log(JSON.stringify(f.project(w," + json.dumps(kind) + ",'g0').rows.map(r=>[r.x,r.y])));", saved)
    assert len(actual) == len(expected)
    for actual_row, expected_row in zip(actual, expected):
        for actual_value, expected_value in zip(actual_row, expected_row):
            if expected_value is None:
                assert actual_value is None
            else:
                assert actual_value == pytest.approx(expected_value)


def test_histogram_keeps_zero_baseline_and_svg_clips_marks_to_plot():
    got = evaluate("w.genes=Array.from({length:20},(_,i)=>({...w.genes[0],gene_id:'g'+i,hypothesis_id:'g'+i,p_value:(i+.5)/20,q_value:(i+.5)/20,raw_counts:null}));const m=f.project(w,'pvalue_histogram');console.log(JSON.stringify({bounds:m.bounds,svg:f.toSVG(m,{bounds:[0,1,.5,1.2]},'current')}));")
    assert got["bounds"][2] == 0
    assert got["bounds"][0] == 0 and got["bounds"][1] == 1
    assert '<clipPath' in got["svg"] and 'clip-path="url(#plot-area)"' in got["svg"]


def test_unavailable_pca_keeps_every_sample_as_explicit_missing_geometry():
    data = workspace()
    data["pca"].update(status="unavailable", reason="no_variation", scores=[], explained_variance_ratio=[])
    data["availability"]["pca"] = {"status": "unavailable", "reason": "no_variation"}
    got = evaluate("const m=f.project(w,'pca');console.log(JSON.stringify({ids:m.rows.map(r=>r.id),view:f.exportSnapshot(m,{},'complete')}));", data)
    assert got["ids"] == ["s0", "s1", "s2", "s3"]
    assert got["view"]["coverage"]["total"] == got["view"]["coverage"]["unplottable"] == 4


def test_recorded_intervals_are_visible_in_svg_and_retained_in_csv():
    data = workspace()
    data["genes"][0]["interval"] = {"lower": 1, "upper": 3, "level": .95, "kind": "confidence"}
    got = evaluate("const m=f.project(w,'ma');console.log(JSON.stringify({svg:f.toSVG(m,{},'complete'),csv:f.toCSV(m)}));", data)
    assert 'data-uncertainty="confidence"' in got["svg"]
    assert 'interval' in got["csv"] and 'lower' in got["csv"]


def test_exports_keep_unavailable_reason_and_condition_labels():
    data = workspace()
    data["pairing"] = {"status": "unpaired", "reason": "pairing_not_explicitly_declared"}
    data["availability"]["paired_expression"] = {"status": "unavailable", "reason": "pairing_not_explicitly_declared"}
    for sample in data["samples"]:
        sample["pair_id"] = None
    got = evaluate("const m=f.project(w,'paired_expression','g<1');console.log(JSON.stringify({svg:f.toSVG(m,{},'complete'),view:f.exportSnapshot(m,{},'complete')}));", data)
    assert got["view"]["availability"]["reason"] == "pairing_not_explicitly_declared"
    assert got["view"]["pairing"]["status"] == "unpaired"
    assert 'Unavailable: pairing_not_explicitly_declared' in got["svg"]
    assert '>A</text>' in got["svg"] and '>B</text>' in got["svg"]
