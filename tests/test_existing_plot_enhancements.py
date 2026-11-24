"""Actual saved geometry, display scope and custody for existing plotting paths."""

import json
import subprocess
from copy import deepcopy
from pathlib import Path

import pytest

from tests import test_figure_artifacts
from tests.test_rna_publication_figures import binding, numeric_data, payload

exported_figures = test_figure_artifacts.exported_figures
figure_store = test_figure_artifacts.figure_store


def test_publication_vector_svg_300dpi_and_recorded_options(workspace_tmp_path):
    from PIL import Image

    from devbench.analysis.checkpoints import digest
    from devbench.analysis.figure_artifacts import (
        export_publication_figure,
        load_publication_figure,
    )

    data = payload(workspace_tmp_path)
    result = export_publication_figure(
        workspace_tmp_path / "publication",
        data,
        figure_id="saved_effects",
        analysis_binding=binding(workspace_tmp_path),
    )
    record, retained = load_publication_figure(
        result["manifest_path"], result["root_hash"]
    )
    svg = (workspace_tmp_path / "publication/figure.svg").read_text()
    assert "<path" in svg and "<image" not in svg
    with Image.open(workspace_tmp_path / "publication/figure.png") as image:
        assert image.size == (2100, 1500)
        assert image.info["dpi"] == pytest.approx((300, 300), abs=0.02)
    assert record["render_parameters"]["dpi"] == 300
    assert digest(record["render_parameters"]) == record["render_parameters_sha256"]
    assert record["render_parameters"]["scope"] == "complete"
    assert retained == data


def test_publication_current_scope_records_exact_omitted_ids_and_reference_geometry(
    workspace_tmp_path, monkeypatch
):
    from matplotlib.figure import Figure

    from devbench.analysis.figure_artifacts import (
        export_publication_figure,
        load_publication_figure,
    )

    seen = []
    original = Figure.savefig

    def capture(figure, *args, **kwargs):
        axis = figure.axes[0]
        seen.append(
            {
                "xlim": axis.get_xlim(),
                "ylim": axis.get_ylim(),
                "lines": [
                    (list(line.get_xdata()), list(line.get_ydata()))
                    for line in axis.lines
                ],
                "text": [text.get_text() for text in axis.texts],
            }
        )
        return original(figure, *args, **kwargs)

    monkeypatch.setattr(Figure, "savefig", capture)
    data = payload(workspace_tmp_path)
    receipt = export_publication_figure(
        workspace_tmp_path / "current",
        data,
        figure_id="current",
        analysis_binding=binding(workspace_tmp_path),
        display_options={
            "scope": "current",
            "bounds": [0, 3, 0, 3],
            "selected_ids": ["G2"],
            "reference_lines": [
                {"axis": "x", "value": 1.5, "label": "Supplied effect reference"}
            ],
        },
    )
    record, retained = load_publication_figure(
        receipt["manifest_path"], receipt["root_hash"]
    )
    assert all(item["xlim"] == (0, 3) and item["ylim"] == (0, 3) for item in seen)
    assert all(any(xs == [1.5, 1.5] for xs, ys in item["lines"]) for item in seen)
    assert record["render_coverage"]["viewport_excluded_ids"] == ["G1"]
    assert record["render_coverage"]["drawn_ids"] == ["G2"]
    assert retained["data"]["membership"] == ["G1", "G2"]


def test_cell_qc_prefilter_context_preserves_original_population(exported_figures):
    from devbench.analysis.checkpoints import sha256
    from devbench.analysis.figure_artifacts import load_figure

    store, _, root = exported_figures
    kept_record, kept = load_figure(root, "qc")
    record, context = load_figure(root, "qc_population_context")
    assert kept["population_ids"] == ["001", "NA", "cell3"]
    assert kept_record["analysis_n"] == 3
    assert context["population_ids"] == ["001", "NA", "cell3", "filtered"]
    before, after = context["panels"]
    assert (
        next(row for row in before["rows"] if row["observation_id"] == "filtered")[
            "status"
        ]
        == "rendered"
    )
    filtered = next(row for row in after["rows"] if row["observation_id"] == "filtered")
    assert (
        filtered["status"] == "excluded"
        and filtered["exclusion_reason"] == "not_retained_by_saved_qc_pass"
    )
    assert record["analysis_binding"]["generating_stage_receipts"]["qc"] == sha256(
        store.path("qc") / "receipt.json"
    )
    context_info = record["semantics"]["qc_population_context"]
    assert context_info["input_n"] == 4 and context_info["retained_n"] == 3
    assert context_info["parameters_ref"]["sha256"] == sha256(root / "parameters.json")
    assert before["reference_lines"][0]["value"] == pytest.approx(
        __import__("math").log1p(3)
    )
    assert before["reference_lines"][0]["source_parameter"] == "min_genes"


def test_cell_true_palette_samples_and_category_shape_fallback():
    from matplotlib import colormaps
    from matplotlib.colors import to_hex

    module = (
        Path(__file__).resolve().parents[1]
        / "src/devbench/analysis/assets/scientific-figure.js"
    )
    code = (
        "const f=require("
        + json.dumps(str(module))
        + "); console.log(JSON.stringify({colors:[0,.5,1].map("
        "t=>f.continuousColor('viridis',t,[0,1])), categories:Array.from("
        "{length:17},(_,i)=>f.categoryEncoding(i))}));"
    )
    result = subprocess.run(["node", "-e", code], text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    observed = json.loads(result.stdout)
    assert observed["colors"] == [
        to_hex(colormaps["viridis"](index / 255)) for index in (0, 128, 255)
    ]
    encodings = observed["categories"]
    assert len({(item["color"], item["shape"]) for item in encodings}) == 17


def test_diverging_palette_uses_symmetric_zero_center_and_refuses_clipping():
    from tests.test_scientific_figure_redesign import run_js

    result = run_js(
        "let rejected=false;try{f.continuousColor('viridis',11,[0,10]);}"
        "catch(error){rejected=true;}\n"
        "console.log(JSON.stringify({center:f.continuousColor('RdBu_r',0,[-2,5]),"
        "low:f.continuousColor('RdBu_r',-5,[-2,5]),"
        "high:f.continuousColor('RdBu_r',5,[-2,5]),rejected}));"
    )
    from matplotlib import colormaps
    from matplotlib.colors import to_hex

    assert result == {
        "center": to_hex(colormaps["RdBu_r"](128 / 255)),
        "low": to_hex(colormaps["RdBu_r"](0.0)),
        "high": to_hex(colormaps["RdBu_r"](1.0)),
        "rejected": True,
    }


def test_offline_qc_distributions_keep_exact_source_ids(figure_store):
    from devbench.analysis.html_report import write_interactive_figures

    store, conditions, root = figure_store
    (root / "figures").mkdir()
    write_interactive_figures(store, conditions, root / "figures", batch_key="batch")
    figures = json.loads((root / "figures/interactive-data.json").read_text())
    distributions = figures["qc_distributions"]
    all_cells = [
        trace
        for trace in distributions["data"]
        if trace["meta"]["population"] == "all_input"
    ]
    assert all_cells
    assert all(
        set(trace["customdata"]) <= {"001", "NA", "cell3", "filtered"}
        for trace in all_cells
    )
    counts = next(
        trace for trace in all_cells if trace["meta"]["field"] == "total_counts"
    )
    assert counts["x"] == [0.0, 4.0, 8.0, 12.0]
    assert counts["customdata"] == ["001", "NA", "cell3", "filtered"]
    assert all(
        trace["marker"]["color"]
        in {
            "#0072B2",
            "#E69F00",
            "#009E73",
            "#CC79A7",
            "#56B4E9",
            "#D55E00",
            "#F0E442",
            "#000000",
        }
        for trace in figures["embedding"]["data"]
    )


@pytest.mark.parametrize(
    "name,kind",
    [
        ("prepare_ma_plot_payload", "ma"),
        ("prepare_volcano_plot_payload", "volcano"),
        ("prepare_dispersion_plot_payload", "dispersion"),
        ("prepare_sample_pca_payload", "pca"),
        ("prepare_pvalue_hist_payload", "pvalue_histogram"),
        ("prepare_paired_counts_payload", "paired_expression"),
    ],
)
def test_named_producers_replay_saved_result_and_preserve_coverage(
    workspace_tmp_path, name, kind
):
    from devbench.analysis import figure_artifacts as figures
    from devbench.analysis.bulk_plot_data import (
        build_bulk_plot_workspace,
        numeric_plot_coverage,
        numeric_plot_data,
    )
    from devbench.analysis.checkpoints import atomic_json
    from devbench.analysis.episode_proposals import _load
    from devharness_core.provenance import ExecutionDAG
    from tests.test_bulk_plot_data import result
    from tests.test_rna_publication_figures import file_ref

    source = workspace_tmp_path / "input"
    source.mkdir()
    saved = result(paired=True)
    atomic_json(source / "result.json", saved)
    source_ref = file_ref(source / "result.json")
    workspace = build_bulk_plot_workspace(
        saved,
        source={
            "sha256": source_ref["sha256"],
            "run_id": "synthetic",
            "kind": "synthetic",
        },
    )
    atomic_json(source / "workspace.json", workspace)
    audit = ExecutionDAG(source / "audit", actor_id="synthetic-plot-source")
    audit.add_artifact(source / "result.json")
    audit.append("synthetic.saved_result", {"scope": "synthetic only"})
    receipt = audit.finalize()
    kwargs = {
        "workspace_ref": file_ref(source / "workspace.json"),
        "source_result_ref": source_ref,
        "source_receipt_ref": {
            "manifest_path": receipt["manifest_path"],
            "root_hash": receipt["root_hash"],
        },
    }
    if kind == "paired_expression":
        kwargs["gene_id"] = "g0"
    projected = getattr(figures, name)(workspace_tmp_path / "projection", **kwargs)
    assert projected["data"] == numeric_plot_data(
        workspace, kind, "g0" if kind == "paired_expression" else None
    )
    _, projection, _ = _load(
        projected["projection_ref"]["manifest_path"],
        projected["projection_ref"]["root_hash"],
        "figure-projection.json",
    )
    coverage = json.loads(Path(projection["coverage_ref"]["path"]).read_text())
    assert coverage == numeric_plot_coverage(
        workspace, kind, "g0" if kind == "paired_expression" else None
    )
    if kind == "volcano":
        assert any(
            item["entity_id"] == "g0"
            and "q_value_zero_has_no_finite_negative_log10" in item["reasons"]
            for item in coverage["undrawn"]
        )
    before = (source / "result.json").read_bytes()
    (source / "result.json").write_bytes(before + b" ")
    with pytest.raises(ValueError, match="source_hash_or_size"):
        figures.validate_publication_plot_payload(projected)


@pytest.mark.parametrize(
    "options",
    [
        {"counts": [1, 2]},
        {"q_threshold": 0.05},
        {"bins": 5},
        {"scope": "complete", "bounds": [0, 1, 0, 1]},
        {"scope": "current"},
        {"selected_ids": ["missing"]},
        {"dpi": 72},
    ],
)
def test_publication_rejects_scientific_or_ambiguous_display_changes(tmp_path, options):
    from devbench.analysis.figure_artifacts import export_publication_figure

    data = payload(tmp_path)
    with pytest.raises(ValueError):
        export_publication_figure(
            tmp_path / "invalid",
            data,
            figure_id="invalid",
            analysis_binding=binding(tmp_path),
            display_options=options,
        )
    assert not (tmp_path / "invalid").exists()


def test_publication_actual_category_shapes_and_nonoverlapping_labels(
    workspace_tmp_path, monkeypatch
):
    from matplotlib.figure import Figure

    from devbench.analysis.figure_artifacts import export_publication_figure

    data = numeric_data(uncertainty=False)
    prototype = deepcopy(data["rows"][0])
    data["rows"] = [
        {
            **deepcopy(prototype),
            "entity_id": f"G{i}",
            "group_id": f"group_{i:02d}",
            "x": {"status": "exact", "value": 1 + i * 0.001, "censoring": None},
            "y": {"status": "exact", "value": 1 + i * 0.001, "censoring": None},
        }
        for i in range(12)
    ]
    data["membership"] = [row["entity_id"] for row in data["rows"]]
    captured = []
    original = Figure.savefig

    def capture(figure, *args, **kwargs):
        figure.canvas.draw()
        axis = figure.axes[0]
        captured.append(
            {
                "paths": [
                    collection.get_paths()[0].vertices.tolist()
                    for collection in axis.collections
                ],
                "label_boxes": [
                    annotation.get_bbox_patch().get_window_extent().bounds
                    for annotation in axis.texts
                    if annotation.get_bbox_patch()
                ],
            }
        )
        return original(figure, *args, **kwargs)

    monkeypatch.setattr(Figure, "savefig", capture)
    value = payload(workspace_tmp_path, data=data)
    export_publication_figure(
        workspace_tmp_path / "categories",
        value,
        figure_id="categories",
        analysis_binding=binding(workspace_tmp_path),
        display_options={"selected_ids": data["membership"]},
    )
    for observed in captured:
        assert observed["paths"][0] != observed["paths"][8]
        boxes = observed["label_boxes"]
        assert len(boxes) == 12
        for index, (x, y, width, height) in enumerate(boxes):
            for xx, yy, ww, hh in boxes[index + 1 :]:
                assert (
                    x + width <= xx or xx + ww <= x or y + height <= yy or yy + hh <= y
                )


def test_cell_canvas_svg_use_same_declared_palette_shapes_and_thresholds():
    from tests.test_scientific_figure_redesign import BASE, DOM, run_js

    observed = run_js(
        BASE + DOM + "\n"
        "panel.color={kind:'continuous',field:'mito',label:'Mito (%)',"
        "palette:'magma',scale:{kind:'linear',min:0,max:10,clipped_n:0}};\n"
        "rows[0].color=0;rows[1].color=10;\n"
        "panel.reference_lines=[{axis:'x',value:5,label:'Saved x cutoff',"
        "source_parameter:'minimum',source_ref:{sha256:'a'.repeat(64)},batch:null}];\n"
        "const c=f.mount(host,{record,payload});flush(0);const svg=c.exportSVG();\n"
        "console.log(JSON.stringify({svg,configuration:c.getConfiguration(),"
        "bar:all().find(n=>n.className==='sf-colorbar').attributes.style,"
        "thresholdDrawn:calls.some(c=>c.method==='setLineDash'&&"
        "c.args[0].join(',')==='5,3')}));\n"
    )
    import xml.etree.ElementTree as ET

    from matplotlib import colormaps
    from matplotlib.colors import to_hex

    svg = ET.fromstring(observed["svg"])
    fills = [
        shape.attrib.get("fill")
        for shape in svg.findall(".//{http://www.w3.org/2000/svg}circle")
    ]
    assert to_hex(colormaps["magma"](0.0)) in fills
    assert to_hex(colormaps["magma"](1.0)) in fills
    assert observed["configuration"]["palette"] == "magma"
    assert observed["configuration"]["color_domain"] == [0, 10]
    assert to_hex(colormaps["magma"](1.0)) in observed["bar"]
    assert observed["thresholdDrawn"]
    assert svg.findall(".//{http://www.w3.org/2000/svg}path[@stroke-dasharray='5 3']")


def test_static_offline_qc_histogram_kde_and_identity_metadata(tmp_path):
    import pandas as pd
    from PIL import Image

    from devbench.analysis.plotting import render_figures

    embedding = pd.DataFrame(
        {
            "UMAP_1": range(12),
            "UMAP_2": range(12),
            "cell_type": [f"c{i}" for i in range(12)],
            "sample_id": [f"s{i}" for i in range(12)],
            "donor_id": [f"d{i // 2}" for i in range(12)],
        },
        index=[f"cell{i}" for i in range(12)],
    )
    qc = pd.DataFrame(
        {
            "total_counts": range(1, 13),
            "n_genes_by_counts": range(1, 13),
            "log1p_total_counts": [__import__("math").log1p(i) for i in range(1, 13)],
            "log1p_n_genes_by_counts": [
                __import__("math").log1p(i) for i in range(1, 13)
            ],
            "pct_counts_mt": range(12),
            "qc_pass": [True] * 9 + [False] * 3,
        },
        index=embedding.index,
    )
    context = render_figures(
        embedding, qc, tmp_path / "static", source_label="Saved synthetic table"
    )
    assert context["qc_ids"] == qc.index.tolist()
    assert context["source_labels"]["donor_id"]["cell0"] == "d0"
    assert context["populations"] == {"provided": 12, "saved_retained": 9}
    distribution = context["distribution_fields"]["total_counts"]
    assert distribution["finite_ids"] == qc.index.tolist()
    assert len(distribution["kde"]) == 2
    assert all(
        len(curve["x"]) == len(curve["density"]) == 128 for curve in distribution["kde"]
    )
    for name in ("embedding.png", "qc.png", "qc-distributions.png"):
        with Image.open(tmp_path / "static" / name) as image:
            assert image.info["dpi"] == pytest.approx((300, 300), abs=0.02)


def test_publication_curve_ribbons_use_saved_intervals_without_bridging_missing_points(
    workspace_tmp_path, monkeypatch
):
    from matplotlib.collections import PolyCollection
    from matplotlib.figure import Figure

    from devbench.analysis.figure_artifacts import export_publication_figure

    data = numeric_data(kind="measured_curve")
    data["rows"] = []
    for index in range(5):
        data["rows"].append(
            {
                "entity_id": "g",
                "coordinate_id": f"t{index}",
                "group_id": "group",
                "x": {"status": "exact", "value": index, "censoring": None},
                "y": {
                    "status": "missing" if index == 2 else "exact",
                    "value": None if index == 2 else 10 + index,
                    "censoring": None,
                },
                "interval": None
                if index == 2
                else {
                    "lower": 9 + index,
                    "upper": 11 + index,
                    "kind": "confidence",
                    "level": 0.95,
                },
            }
        )
    data["membership"] = [
        [row["entity_id"], row["coordinate_id"], row["group_id"]]
        for row in data["rows"]
    ]
    observed = []
    original = Figure.savefig

    def capture(figure, *args, **kwargs):
        observed.append(
            [
                collection.get_paths()[0].vertices.tolist()
                for collection in figure.axes[0].collections
                if isinstance(collection, PolyCollection)
            ]
        )
        return original(figure, *args, **kwargs)

    monkeypatch.setattr(Figure, "savefig", capture)
    value = payload(workspace_tmp_path, data=data)
    export_publication_figure(
        workspace_tmp_path / "ribbons",
        value,
        figure_id="ribbons",
        analysis_binding=binding(workspace_tmp_path),
    )
    for ribbons in observed:
        assert len(ribbons) == 2
        assert sorted({point[0] for point in ribbons[0]}) == [0, 1]
        assert sorted({point[0] for point in ribbons[1]}) == [3, 4]
        assert all(
            point[1] in {9 + point[0], 11 + point[0]}
            for ribbon in ribbons
            for point in ribbon
        )


def test_g08_centroids_use_complete_saved_groups_and_export_membership():
    from tests.test_scientific_figure_redesign import BASE, DOM, run_js

    result = run_js(
        BASE + DOM + "\n"
        "rows[2]={observation_id:'far-away',x:100,y:20,color:'A',metadata:{},"
        "status:'rendered',exclusion_reason:null};\n"
        "payload.population_ids=rows.map(row=>row.observation_id);record.rendered_n=3;\n"
        "const c=f.mount(host,{record,payload});flush(0);\n"
        "const toggle=all().find(node=>node.attributes['aria-label']==="
        "'Show saved group centroids');\n"
        "toggle.checked=true;toggle.emit('change');flush(1);\n"
        "const initial=c.getConfiguration();"
        "action('zoom-in').emit('click');flush(200);\n"
        "const after=c.getConfiguration(),svg=c.exportSVG();\n"
        "console.log(JSON.stringify({initial,after,svg,labels:calls.filter("
        "call=>call.method==='fillText').map(call=>call.args[0]),"
        "selection:c.getSelection()}));\n"
    )
    centroids = result["initial"]["group_centroids"]
    assert centroids[0] == {
        "group": "A",
        "x": 50,
        "y": 10,
        "n": 2,
        "observation_ids": ["cell,a", "far-away"],
    }
    assert centroids == result["after"]["group_centroids"]
    assert (
        result["initial"]["centroid_method"]
        == "arithmetic_mean_of_all_finite_saved_group_points_no_new_cluster_inference"
    )
    assert "A (n=2)" in result["labels"]
    assert "A (n=2)" in result["svg"]
    assert result["selection"] == []
