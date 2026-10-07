"""Independent evidence assertions over tiny saved tables."""
import json

import pytest

from tests.test_figure_artifacts import figure_store, exported_figures
from devbench.analysis.checkpoints import atomic_json, sha256


def test_report_wrapper_emits_manifest(figure_store):
    from devbench.analysis.reporting import render_concordance_figures
    store, conditions, root = figure_store
    render_concordance_figures(store, conditions, root / "figures", batch_key="batch")
    assert (root / "figures/manifest.json").is_file()


def test_plotly_stable_ids_reference_reindex_and_shared_category_colors(figure_store):
    from devbench.analysis.html_report import write_interactive_figures
    store, conditions, root = figure_store
    (root / "figures").mkdir()
    write_interactive_figures(store, conditions, root / "figures", batch_key="batch")
    figures = json.loads((root / "figures/interactive-data.json").read_text())
    colors = {}
    for trace in figures["embedding"]["data"]:
        assert set(trace["customdata"]) <= {"001", "NA", "cell3"}
        key = (trace["meta"]["grouping"], trace["name"])
        if key in colors:
            assert colors[key] == trace["marker"]["color"]
        colors[key] = trace["marker"]["color"]
    points = [row for row in figures["QC"]["data"] if row["mode"] == "markers"][0]
    assert points["customdata"] == ["001", "NA", "cell3", "filtered"]
    assert points["x"] == [0., 4., 8., 12.] == points["y"]
    assert figures["confidence"]["data"][0]["customdata"] == points["customdata"]
    assert "calibration unknown" in figures["confidence"]["layout"]["title"]["text"]


def test_independent_evidence_review_catches_validly_rehashed_wrong_coordinates(exported_figures):
    from devbench.analysis.streaming_review import review_figure_evidence
    store, conditions, root = exported_figures
    parameters = json.loads((root / "parameters.json").read_text())
    before = review_figure_evidence(root, store, conditions, parameters)
    assert all(before.values()), before
    data_path = root / "figures/uncorrected-data.json"
    payload = json.loads(data_path.read_text())
    payload["panels"][0]["rows"][0]["x"] = 999.
    atomic_json(data_path, payload)
    manifest_path = root / "figures/manifest.json"
    manifest = json.loads(manifest_path.read_text())
    next(record for record in manifest["figures"] if record["figure_id"] == "uncorrected")["plot_data"]["sha256"] = sha256(data_path)
    atomic_json(manifest_path, manifest)
    after = review_figure_evidence(root, store, conditions, parameters)
    assert not after["figure_coordinates_and_categories_match_saved_tables"]


def test_plotly_missing_or_duplicate_reference_ids_fail(figure_store):
    from devbench.analysis.html_report import write_interactive_figures
    store, conditions, root = figure_store
    path = store.path("qc") / "external-reference.csv"
    path.write_text(path.read_text().replace("cell3", "unknown"))
    receipt = json.loads((path.parent / "receipt.json").read_text())
    receipt["artifacts"][path.name] = sha256(path)
    atomic_json(path.parent / "receipt.json", receipt)
    (root / "figures").mkdir()
    with pytest.raises(ValueError, match="reference.*identit"):
        write_interactive_figures(store, conditions, root / "figures", batch_key="batch")
