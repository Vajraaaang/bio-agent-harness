"""Plotly defaults for the existing Studio receipt chart recipes."""

from __future__ import annotations

import math
import re
from typing import Any

from .plugin_jobs import canonical, digest
from .workbench_plotly import validated_plotly_figure

_SHA = re.compile(r"[a-f0-9]{64}")
_ID = re.compile(r"[a-f0-9]{32}")
_QUANTILES = {
    "nonzero_features_p25": "p25",
    "nonzero_features_median": "median",
    "nonzero_features_p75": "p75",
}


def _number(value: Any) -> bool:
    return type(value) in (int, float) and abs(value) <= 1e300 and math.isfinite(value)


def _percentages(rows, *, doublets: bool):
    names, values = [], []
    for row in rows:
        if len(row) < (4 if doublets else 3):
            continue
        label, total, subset = row[:3]
        if not (
            isinstance(label, str) and type(total) is int and type(subset) is int
            and 0 <= subset <= total and total > 0
        ):
            continue
        rate = row[3] if doublets else subset / total
        if _number(rate) and 0 <= rate <= 1:
            names.append(label)
            values.append(100 * rate)
    return names, values


def _composition(columns, rows):
    # Interval-bearing results keep the existing interactive effect-scale view.
    if set(columns) & {"Credible interval lower", "Credible interval upper"}:
        return [], []
    required = ["Cell type", "Covariate", "Coefficient"]
    if not set(required) <= set(columns):
        return [], []
    position = [columns.index(name) for name in required]
    names, values = [], []
    for row in rows:
        if len(row) <= max(position):
            continue
        kind, covariate, estimate = (row[i] for i in position)
        if isinstance(kind, str) and isinstance(covariate, str) and _number(estimate):
            names.append(f"{kind} · {covariate}")
            values.append(estimate)
    return names, values


def _expression(rows):
    available = {
        row[0]: row[1] for row in rows
        if len(row) == 2 and isinstance(row[0], str)
        and row[0] in _QUANTILES and _number(row[1]) and row[1] >= 0
    }
    names, values = [], []
    for key, label in _QUANTILES.items():
        if key in available:
            names.append(label)
            values.append(available[key])
    return names, values


def _recipe(caption, columns, rows):
    axis = None
    if caption == "QC retention by group" and columns[:3] == [
        "Group", "Input", "Retained",
    ]:
        names, values = _percentages(rows, doublets=False)
        title, axis = "Cell retention (%)", [0, 100]
    elif caption == "Doublet predictions" and columns[:4] == [
        "Capture", "Cells", "Predictions", "Rate",
    ]:
        names, values = _percentages(rows, doublets=True)
        title, axis = "Predicted doublets (%)", [0, 100]
    elif caption == "Expression audit" and columns == ["Metric", "Value"]:
        names, values = _expression(rows)
        title = "Nonzero features per profile"
    elif caption == "Composition effects":
        names, values = _composition(columns, rows)
        title = "scCODA relative log coefficients"
    else:
        return None
    return title, names, values, axis


def receipt_figure(table):
    if not isinstance(table, dict):
        return None
    caption, columns = table.get("caption"), table.get("columns")
    incoming = table.get("rows")
    if not isinstance(columns, list) or not isinstance(incoming, list):
        return None
    rows = [row for row in incoming if isinstance(row, list)][:16]
    recipe = _recipe(caption, columns, rows)
    if recipe is None:
        return None
    title, names, values, axis = recipe
    if not names:
        return None
    trace = {"type": "bar", "x": names, "y": values}
    if caption == "Composition effects":
        trace = {"type": "scatter", "mode": "markers", "x": values, "y": names}
    layout = {"title": {"text": title}}
    if axis:
        layout["yaxis"] = {"title": {"text": title}, "range": axis}
    if caption == "Composition effects":
        layout["xaxis"] = {"title": {"text": "Relative log coefficient"}}
    try:
        return validated_plotly_figure(canonical({"data": [trace], "layout": layout}))
    except (TypeError, ValueError):
        return None


def receipt_plot_paths(session_id, run):
    if (
        run["status"] != "completed"
        or not _ID.fullmatch(run.get("run_id") or "")
        or not _SHA.fullmatch(run.get("replay_bundle_sha256") or "")
    ):
        return {}
    paths = {}
    for index, table in enumerate(run.get("summary_tables", ())):
        figure = receipt_figure(table)
        if figure:
            path = f"/api/scientific/adaptive/{session_id}/receipt-plot/{run['run_id']}"
            paths[str(index)] = f"{path}/{index}/{digest(figure)}/plotly"
    return paths


def receipt_plotly(app, session_id, run_id, index, identity):
    from .workbench_adaptive import _plugin_projection
    from .workbench_interactive import ensure_adaptive_binding

    binding = ensure_adaptive_binding(app, session_id)
    if (
        binding is None
        or binding.delivery.context_id != f"plugin-scientist-{session_id}"
    ):
        raise PermissionError("A current Plugin readback is required for receipt plots")
    projection = _plugin_projection(app, binding)
    if projection is None:
        raise PermissionError("The Plugin receipt is not available")
    run = next((item for item in projection["runs"] if item["run_id"] == run_id), None)
    expected = (
        f"/api/scientific/adaptive/{session_id}/receipt-plot/{run_id}"
        f"/{index}/{identity}/plotly"
    )
    if run is None or run.get("plotly_paths", {}).get(str(index)) != expected:
        raise KeyError("Unknown verified receipt plot")
    figure = receipt_figure(run["summary_tables"][index])
    return figure, run["replay_bundle_sha256"]
