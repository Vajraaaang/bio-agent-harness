"""Default Plotly selection for bounded, receipt-backed Studio charts."""

from types import SimpleNamespace

import pytest

from devbench.platform import workbench_adaptive, workbench_interactive
from devbench.platform.workbench_receipt_plots import (
    receipt_figure,
    receipt_plot_paths,
    receipt_plotly,
)


def test_plot_recipes_preserve_units_and_do_not_drop_intervals():
    qc = {
        "caption": "QC retention by group",
        "columns": ["Group", "Input", "Retained"],
        "rows": [["A", 100, 64], ["B", 0, 0], ["C", 20, 21]],
    }
    figure = receipt_figure(qc)
    assert figure["data"][0] == {"type": "bar", "x": ["A"], "y": [64.0]}
    assert figure["layout"]["yaxis"]["range"] == [0, 100]
    effects = {
        "caption": "Composition effects",
        "columns": ["Cell type", "Covariate", "Coefficient"],
        "rows": [["Neuron", "e11", -0.2]],
    }
    assert receipt_figure(effects)["data"][0]["x"] == [-0.2]
    effects["columns"].append("Credible interval lower")
    assert receipt_figure(effects) is None
    assert receipt_figure({"caption": "Expression audit", "rows": None}) is None


def test_receipt_frame_is_bound_to_current_run_and_chart_digest(monkeypatch):
    sid, rid = "a" * 32, "b" * 32
    run = {
        "status": "completed",
        "run_id": rid,
        "replay_bundle_sha256": "c" * 64,
        "summary_tables": [{
            "caption": "Expression audit",
            "columns": ["Metric", "Value"],
            "rows": [["nonzero_features_median", 12]],
        }],
    }
    assert receipt_plot_paths(sid, {**run, "status": "running"}) == {}
    assert receipt_plot_paths(sid, {**run, "replay_bundle_sha256": None}) == {}
    run["plotly_paths"] = receipt_plot_paths(sid, run)
    identity = run["plotly_paths"]["0"].split("/")[-2]
    binding = SimpleNamespace(
        delivery=SimpleNamespace(context_id=f"plugin-scientist-{sid}"),
    )
    monkeypatch.setattr(
        workbench_interactive, "ensure_adaptive_binding", lambda *_: binding,
    )
    monkeypatch.setattr(
        workbench_adaptive, "_plugin_projection", lambda *_: {"runs": [run]},
    )
    chart, source = receipt_plotly(object(), sid, rid, 0, identity)
    assert source == run["replay_bundle_sha256"]
    assert chart["data"][0]["y"] == [12]
    with pytest.raises(KeyError):
        receipt_plotly(object(), sid, rid, 0, "d" * 64)
    with pytest.raises(PermissionError):
        receipt_plotly(object(), "e" * 32, rid, 0, identity)
