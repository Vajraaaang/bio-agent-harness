"""Saved-table figure contracts; no numerical fitting or original matrix reads."""
from copy import deepcopy
import json
from pathlib import Path

import pandas as pd
import pytest

from devbench.analysis.checkpoints import atomic_json, digest, sha256


@pytest.fixture
def figure_store(tmp_path):
    from devbench.analysis.checkpoints import CheckpointStore

    root = tmp_path / "analysis"
    root.mkdir()
    parameters = {"data_sha256": "a" * 64, "source_sha256": "b" * 64,
                  "counts_layer": "counts", "batch_key": "batch", "min_genes": 3,
                  "mad_limit": None, "max_mito_percent": None, "seed": 128,
                  "n_neighbors": 3, "resolution": .5}
    identity = {"data_sha256": parameters["data_sha256"], "source_sha256": parameters["source_sha256"],
                "parameters": parameters, "packages": {}, "python_minor": [3, 11]}
    atomic_json(root / "parameters.json", parameters)
    atomic_json(root / "checkpoint-identity.json", identity)
    store = CheckpointStore(root / "checkpoints", identity)
    metrics = pd.DataFrame({"log1p_total_counts": [0., 1., 2., 3.],
        "log1p_n_genes_by_counts": [0., 2., 4., 6.], "pct_counts_mt": [0., 12., float("nan"), 9.],
        "qc_pass": [True, True, True, False], "batch": ["B", "A", "B", "C"],
        "total_counts": [0., 4., 8., 12.]}, index=["001", "NA", "cell3", "filtered"])
    reference = pd.DataFrame({"total_counts": [8., 0., 12., 4.],
        "celltypes": ["type-B", "type-A", "type-C", "type-B"],
        "conf_score": [.8, .1, .9, .4]}, index=["cell3", "001", "filtered", "NA"])
    table = pd.DataFrame({"UMAP_1": [10., 20., 30.], "UMAP_2": [1., 2., 3.],
                         "batch": ["B", "A", "B"], "leiden": ["1", "0", "1"]},
                        index=["001", "NA", "cell3"])
    for name, tables in {"qc": {"metrics.csv": metrics, "external-reference.csv": reference},
                         "uncorrected_graph": {"embedding.csv": table},
                         "harmony_graph": {"embedding.csv": table.iloc[::-1]}}.items():
        folder = store.path(name)
        folder.mkdir()
        for filename, frame in tables.items():
            frame.to_csv(folder / filename)
        atomic_json(folder / "receipt.json", {"format": "devbench.analysis.checkpoint/1", "stage": name,
                    "identity_sha256": store.identity, "dependencies": {}, "artifacts": {
                        filename: sha256(folder / filename) for filename in tables},
                    "measurement": {}, "metadata": {}})
    return store, {"uncorrected": "pca", "harmony": "harmony"}, root


@pytest.fixture
def exported_figures(figure_store):
    from devbench.analysis.figure_artifacts import export_figures
    store, conditions, root = figure_store
    export_figures(store, conditions, root / "figures", batch_key="batch")
    return figure_store


def test_saved_values_ids_missing_zero_and_bindings(exported_figures):
    from devbench.analysis.figure_artifacts import load_figure
    store, _, root = exported_figures
    record, payload = load_figure(root, "qc")
    rows = {row["observation_id"]: row for row in payload["panels"][0]["rows"]}
    assert rows["001"]["x"] == 0 and rows["001"]["color"] == 0
    assert rows["cell3"]["color"] is None and rows["cell3"]["status"] == "excluded"
    assert rows["cell3"]["exclusion_reason"]
    assert (record["analysis_n"], record["rendered_n"]) == (3, 2)
    assert record["analysis_binding"]["parameters_sha256"] == sha256(root / "parameters.json")
    assert record["analysis_binding"]["generating_stage_receipts"]["qc"] == sha256(store.path("qc") / "receipt.json")
    assert record["semantics"]["population_sha256"] == digest(sorted(rows))
    assert record["semantics"]["representation_or_layer"] == "count_derived_QC:counts"
    assert record["display_options"]["width"] == 770
    assert rows["001"]["metadata"]["batch"] == "B" and rows["001"]["metadata"]["total_counts"] == 0


def test_population_hash_order_independent_and_shared_palette(exported_figures):
    from devbench.analysis.figure_artifacts import load_figure
    _, _, root = exported_figures
    a, first = load_figure(root, "uncorrected")
    b, second = load_figure(root, "harmony")
    assert a["semantics"]["population_sha256"] == b["semantics"]["population_sha256"]
    assert a["plot_data"]["sha256"] != b["plot_data"]["sha256"]
    assert first["panels"][0]["color"]["colors"] == second["panels"][0]["color"]["colors"]
    assert [row["observation_id"] for row in second["panels"][0]["rows"]] == ["cell3", "NA", "001"]


def test_display_edit_is_pure_and_rerenders_saved_payload(exported_figures, tmp_path):
    from devbench.analysis.figure_artifacts import load_figure, render_figure_artifact, validate_display_options
    _, _, root = exported_figures
    record, payload = load_figure(root, "uncorrected")
    before = deepcopy((record, payload))
    label = payload["panels"][0]["labels"][0]["label_id"]
    options = validate_display_options({"point_size": 9, "legend_position": "lower left",
                                      "label_positions": {label: {"x": .45, "y": 1.04}}}, record, payload)
    output = tmp_path / "revision.png"
    render_figure_artifact(record, payload, output, options)
    assert output.is_file() and sha256(output) != record["image"]["sha256"]
    assert (record, payload) == before
    repeated = tmp_path / "repeated.png"
    render_figure_artifact(record, payload, repeated, options)
    assert sha256(output) == sha256(repeated)


def test_label_only_revision_keeps_immutable_text_and_changes_image(exported_figures, tmp_path):
    from devbench.analysis.figure_artifacts import load_figure, render_figure_artifact
    _, _, root = exported_figures
    record, payload = load_figure(root, "uncorrected")
    before = deepcopy((record, payload))
    label = payload["panels"][0]["labels"][2]["label_id"]
    output = tmp_path / "repositioned.png"
    render_figure_artifact(record, payload, output, {"label_positions": {label: {"x": -.15, "y": .6}}})
    assert sha256(output) != record["image"]["sha256"]
    assert (record, payload) == before


@pytest.mark.parametrize("options,reason", [
    ({"width": 319}, "display_option"), ({"height": 2401}, "display_option"),
    ({"point_size": 25}, "display_option"), ({"opacity": .09}, "display_option"),
    ({"font_size": 29}, "display_option"), ({"width": True}, "display_option"),
    ({"legend_position": "outside"}, "display_option"), ({"caption": "causal effect"}, "revision_required"),
    ({"layer": "X"}, "revision_required"), ({"filter": "batch == A"}, "revision_required"),
    ({"bandwidth": 1}, "revision_required"), ({"sampling": 2}, "not_implemented"),
    ({"clipping": [0, 2]}, "not_implemented"), ({"unknown": "x"}, "unknown_display_option"),
    ({"category_colors": {"observed_batch": {"missing": "#ffffff"}}}, "category"),
    ({"category_colors": {"observed_batch": {"A": "javascript:alert(1)"}}}, "color"),
    ({"label_positions": {"missing": {"x": 0, "y": 0}}}, "label"),
])
def test_strict_display_allowlist(exported_figures, options, reason):
    from devbench.analysis.figure_artifacts import load_figure, validate_display_options
    _, _, root = exported_figures
    record, payload = load_figure(root, "uncorrected")
    with pytest.raises(ValueError, match=reason):
        validate_display_options(options, record, payload)


def test_corrupt_oversized_or_escaping_payload_and_legacy_rejected(exported_figures, tmp_path, monkeypatch):
    import devbench.analysis.figure_artifacts as figures
    _, _, root = exported_figures
    path = root / "figures/qc-data.json"
    original = path.read_bytes()
    path.write_bytes(original + b" ")
    with pytest.raises(ValueError, match="plot_data_hash"):
        figures.load_figure(root, "qc")
    path.write_bytes(original)
    monkeypatch.setattr(figures, "MAX_PAYLOAD_BYTES", 2)
    with pytest.raises(ValueError, match="exceeds"):
        figures.load_figure(root, "qc")
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    with pytest.raises(ValueError, match="presentation_revision_unavailable"):
        figures.load_figure(legacy, "qc")
    with pytest.raises(ValueError, match="figure_id"):
        figures.load_figure(root, "../qc")


def test_contract_rejects_semantic_missing_ids_duplicate_nonfinite(exported_figures):
    from devbench.analysis.figure_artifacts import load_figure, validate_figure_artifact
    _, _, root = exported_figures
    record, payload = load_figure(root, "qc")
    for mutation in ("semantics", "duplicate", "nonfinite", "zero_missing", "bad_id"):
        a, b = deepcopy((record, payload))
        if mutation == "semantics":
            del a["semantics"]["representation_or_layer"]
        elif mutation == "duplicate":
            b["panels"][0]["rows"][1]["observation_id"] = "001"
        elif mutation == "nonfinite":
            b["panels"][0]["rows"][0]["x"] = float("nan")
        elif mutation == "zero_missing":
            b["panels"][0]["rows"][0]["x"] = None
        else:
            b["population_ids"][0] = "unknown"
        with pytest.raises(ValueError):
            validate_figure_artifact(a, b)


@pytest.mark.parametrize("kind,key,unit", [("sample_points", "sample_id", "biological_sample"),
    ("gene_results", "gene_id", "gene"), ("group_gene_aggregates", "group_id", "aggregate")])
def test_typed_future_payload_union_without_claiming_renderer(exported_figures, kind, key, unit, tmp_path):
    from devbench.analysis.figure_artifacts import load_figure, validate_figure_artifact, render_figure_artifact
    _, _, root = exported_figures
    record, payload = load_figure(root, "qc")
    record = deepcopy(record)
    payload = deepcopy(payload)
    payload["payload_kind"] = kind
    payload["key_columns"] = [key] if kind != "group_gene_aggregates" else ["group_id", "gene_id"]
    for panel in payload["panels"]:
        for row in panel["rows"]:
            row[key] = row.pop("observation_id")
            if kind == "group_gene_aggregates":
                row["gene_id"] = "gene-A"
                row["contributor_ids"] = [row[key]]
    if kind == "group_gene_aggregates":
        payload["population_ids"] = [[value, "gene-A"] for value in payload["population_ids"]]
    record["semantics"]["unit"] = unit
    record["semantics"]["population_sha256"] = digest(sorted(payload["population_ids"]))
    record["plot_data"]["payload_kind"] = kind
    record["plot_data"]["key_columns"] = payload["key_columns"]
    validate_figure_artifact(record, payload)
    with pytest.raises(ValueError, match="renderer_unavailable"):
        render_figure_artifact(record, payload, tmp_path / "not-produced.png")


def test_duplicate_or_mismatched_checkpoint_population_fails(figure_store):
    from devbench.analysis.figure_artifacts import export_figures
    store, conditions, root = figure_store
    path = store.path("harmony_graph") / "embedding.csv"
    frame = pd.read_csv(path, index_col=0)
    frame.index = ["different", "NA", "001"]
    frame.to_csv(path)
    receipt = json.loads((path.parent / "receipt.json").read_text())
    receipt["artifacts"][path.name] = sha256(path)
    atomic_json(path.parent / "receipt.json", receipt)
    with pytest.raises(ValueError, match="population"):
        export_figures(store, conditions, root / "figures", batch_key="batch")
