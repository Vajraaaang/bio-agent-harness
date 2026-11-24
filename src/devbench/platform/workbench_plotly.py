"""Offline Plotly frames for the Studio's checked figure artifacts."""

from __future__ import annotations

import csv
import gzip
import hashlib
import io
import json
import math
import re
import secrets
from functools import lru_cache
from pathlib import Path
from typing import Any

_MAX_POINTS = 100_000
_MAX_CUSTOM_BYTES = 4_000_000
_STAGES = ("e09", "e10", "e11")
_COLORS = {"Neuroblast": "#147f8e", "Neuron": "#ac3f2d"}
_BUNDLE_BYTES = 5_185_335
_BUNDLE_SHA = "df71db19347acf7e283ced77d1c54d06f1f9583219952319d8a6fd749e3b3927"
_ASSETS = Path(__file__).resolve().parents[1] / "analysis" / "assets"
_Coordinates = dict[tuple[str, str], tuple[list[float], list[float]]]


def _motor_coordinates(content: bytes) -> _Coordinates:
    if not 0 < len(content) <= 32 * 1024**2:
        raise ValueError("Invalid interactive UMAP coordinate size")
    grouped: _Coordinates = {}
    try:
        source = csv.reader(
            io.StringIO(content.decode("utf-8-sig"), newline=""), strict=True
        )
        if next(source, None) != ["stage", "state", "umap_1", "umap_2"]:
            raise ValueError("Invalid interactive UMAP columns")
        for count, row in enumerate(source, 1):
            if count > _MAX_POINTS or len(row) != 4:
                raise ValueError("Invalid interactive UMAP coordinate row")
            stage, state, x_value, y_value = row
            if stage not in _STAGES or state not in _COLORS:
                raise ValueError("Invalid interactive UMAP cell label")
            x, y = float(x_value), float(y_value)
            if not (math.isfinite(x) and math.isfinite(y)):
                raise ValueError("Interactive UMAP coordinates must be finite")
            points = grouped.setdefault((stage, state), ([], []))
            points[0].append(x)
            points[1].append(y)
    except (UnicodeError, csv.Error) as error:
        raise ValueError("Invalid interactive UMAP coordinate CSV") from error
    if not grouped:
        raise ValueError("An interactive UMAP needs at least one point")
    return grouped


def motor_umap_figure(content: bytes) -> dict[str, Any]:
    grouped = _motor_coordinates(content)

    def extent(axis: int) -> list[float]:
        low = min(min(points[axis]) for points in grouped.values())
        high = max(max(points[axis]) for points in grouped.values())
        padding = max((high - low) * 0.035, 0.002)
        bounds = [low - padding, high + padding]
        if not all(map(math.isfinite, bounds)):
            raise ValueError("Invalid interactive UMAP extent")
        return bounds

    traces = []
    for (stage, state), (x, y) in sorted(grouped.items()):
        traces.append(
            {
                "type": "scattergl",
                "mode": "markers",
                "name": f"{stage} · {state}",
                "x": x,
                "y": y,
                "meta": stage,
                "marker": {"color": _COLORS[state], "size": 4, "opacity": 0.63},
                "hovertemplate": (
                    f"{stage} · {state}<br>UMAP 1 %{{x:.2f}}<br>"
                    "UMAP 2 %{y:.2f}<extra></extra>"
                ),
            }
        )
    return {
        "data": traces,
        "layout": {
            "xaxis": {
                "title": {"text": "UMAP 1"},
                "range": extent(0),
                "autorange": False,
                "constrain": "domain",
            },
            "yaxis": {
                "title": {"text": "UMAP 2"},
                "range": extent(1),
                "autorange": False,
                "constrain": "domain",
                "scaleanchor": "x",
                "scaleratio": 1,
            },
            "uirevision": "shared-umap",
        },
        "_stages": [
            stage for stage in _STAGES if any(key[0] == stage for key in grouped)
        ],
    }


def _keys(value: Any, allowed: set[str]) -> bool:
    return type(value) is dict and value.keys() <= allowed


def _label(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) <= 250
        and not re.search(
            r"[<>\x00-\x1f]|://|(?:javascript|data|blob):|^\s*//", value, re.I
        )
    )


def _number(value: Any) -> bool:
    return type(value) in (int, float) and -1e300 <= value <= 1e300


def _point(value: Any) -> bool:
    return value is None or _number(value) or _label(value)


def _color(value: Any) -> bool:
    return (
        isinstance(value, str)
        and re.fullmatch(r"#[0-9a-fA-F]{3,8}|[a-zA-Z]{1,24}", value) is not None
    )


def _valid_style(value: Any, *, line: bool) -> bool:
    allowed = {"color", "width"} if line else {"color", "size", "opacity"}
    return _keys(value, allowed) and all(
        _color(item)
        if key == "color"
        else _number(item) and 0 <= item <= (1 if key == "opacity" else 100)
        for key, item in value.items()
    )


def _title(value: Any) -> bool:
    return _label(value) or (_keys(value, {"text"}) and _label(value.get("text")))


def _axis(value: Any) -> bool:
    if not _keys(
        value, {"title", "range", "type", "showgrid", "zeroline", "autorange"}
    ):
        return False
    for key, item in value.items():
        if key == "title" and not _title(item):
            return False
        if key == "range" and not (
            type(item) is list and len(item) == 2 and all(map(_number, item))
        ):
            return False
        if key == "type" and item not in ("linear", "log", "date", "category"):
            return False
        if key in {"showgrid", "zeroline", "autorange"} and type(item) is not bool:
            return False
    return True


def _heatmap_points(x: Any, y: Any, values: Any) -> int:
    if (
        not isinstance(values, list)
        or not values
        or not isinstance(values[0], list)
        or not values[0]
    ):
        raise ValueError("Plotly heatmap requires a rectangular matrix")
    width, height = len(values[0]), len(values)
    if width * height > _MAX_POINTS or any(
        not isinstance(row, list)
        or len(row) != width
        or any(value is not None and not _number(value) for value in row)
        for row in values
    ):
        raise ValueError("Invalid Plotly heatmap values")
    for axis, length in ((x, width), (y, height)):
        if axis is not None and (
            not isinstance(axis, list)
            or len(axis) != length
            or not all(map(_point, axis))
        ):
            raise ValueError("Invalid Plotly heatmap axes")
    return width * height


def _trace_points(trace: Any) -> int:
    if not _keys(
        trace, {"type", "x", "y", "z", "name", "mode", "opacity", "marker", "line"}
    ):
        raise ValueError("Unsupported Plotly trace field")
    kind = trace.get("type", "scatter")
    if kind not in ("scatter", "scattergl", "bar", "heatmap"):
        raise ValueError("Unsupported Plotly trace type")
    if "name" in trace and not _label(trace["name"]):
        raise ValueError("Invalid Plotly trace label")
    if "mode" in trace and not (
        kind in ("scatter", "scattergl")
        and trace["mode"] in ("markers", "lines", "lines+markers", "markers+lines")
    ):
        raise ValueError("Unsupported Plotly trace mode")
    opacity = trace.get("opacity", 1)
    if not _number(opacity) or not 0 <= opacity <= 1:
        raise ValueError("Invalid Plotly trace opacity")
    for key in ("marker", "line"):
        if key in trace and not _valid_style(trace[key], line=key == "line"):
            raise ValueError("Unsupported Plotly trace style")
    x, y = trace.get("x"), trace.get("y")
    if kind == "heatmap":
        return _heatmap_points(x, y, trace.get("z"))
    if (
        "z" in trace
        or not isinstance(x, list)
        or not isinstance(y, list)
        or not 0 < len(x) == len(y) <= _MAX_POINTS
        or not all(map(_point, x))
        or not all(map(_point, y))
    ):
        raise ValueError("Plotly traces require equally sized x and y data")
    return len(x)


def validated_plotly_figure(content: bytes) -> dict[str, Any]:
    """Allow bounded numeric and categorical data with inert appearance fields."""
    if not 0 < len(content) <= _MAX_CUSTOM_BYTES:
        raise ValueError("Invalid Plotly JSON size")
    try:
        value = json.loads(content)
    except (UnicodeError, RecursionError) as error:
        raise ValueError("Invalid Plotly JSON") from error
    if not _keys(value, {"data", "layout"}):
        raise ValueError("Unsupported Plotly figure fields")
    data = value.get("data")
    if not isinstance(data, list) or not 0 < len(data) <= 24:
        raise ValueError("Invalid number of Plotly traces")
    if sum(_trace_points(trace) for trace in data) > _MAX_POINTS:
        raise ValueError("Plotly figure exceeds the point budget")
    layout = value.get("layout", {})
    if not _keys(layout, {"title", "xaxis", "yaxis", "barmode", "showlegend"}):
        raise ValueError("Unsupported Plotly layout fields")
    if (
        ("title" in layout and not _title(layout["title"]))
        or any(
            axis in layout and not _axis(layout[axis]) for axis in ("xaxis", "yaxis")
        )
        or ("showlegend" in layout and type(layout["showlegend"]) is not bool)
        or (
            "barmode" in layout
            and layout["barmode"] not in ("group", "stack", "relative", "overlay")
        )
    ):
        raise ValueError("Invalid Plotly layout")
    return value


@lru_cache(maxsize=1)
def _plotly_bundle() -> bytes:
    try:
        with gzip.open(_ASSETS / "plotly-strict.min.js.gz", "rb") as stream:
            source = stream.read(_BUNDLE_BYTES + 1)
    except (OSError, EOFError) as error:
        raise ValueError("Local Plotly bundle is unavailable") from error
    if (
        len(source) != _BUNDLE_BYTES
        or hashlib.sha256(source).hexdigest() != _BUNDLE_SHA
    ):
        raise ValueError("Local Plotly bundle failed its integrity check")
    return source


_FRAME_JS = b"""
const payload = JSON.parse(document.getElementById('figure-data').textContent);
const chart = document.getElementById('plot');
const status = document.getElementById('plot-status');
const stageFilter = document.getElementById('stage-filter');
const stages = payload.figure._stages || [];
const {data} = payload.figure;
const css = getComputedStyle(document.documentElement);
const ink = css.getPropertyValue('--ink').trim();
const paper = css.getPropertyValue('--paper').trim();
const grid = css.getPropertyValue('--grid').trim();
const layout = Object.assign({
  autosize: true, paper_bgcolor: paper, plot_bgcolor: paper,
  font: {family: 'system-ui, sans-serif', color: ink},
  margin: {t: payload.figure.layout?.title ? 52 : 16, b: 54, l: 55, r: 30},
  legend: {orientation: 'h', y: -0.24},
  hoverlabel: {bgcolor: paper, font: {color: ink}},
}, payload.figure.layout);
for (const axis of ['xaxis','yaxis']) layout[axis] = Object.assign({
  gridcolor: grid, zerolinecolor: grid, automargin: true,
}, layout[axis]);
function webglAvailable() {
  try {
    const canvas = document.createElement('canvas');
    const context = canvas.getContext('webgl2') || canvas.getContext('webgl');
    if (!context) return false;
    context.getExtension('WEBGL_lose_context')?.loseContext();
    return true;
  } catch (_) { return false; }
}
if (data.some(trace => trace.type === 'scattergl') && !webglAvailable()) {
  for (const trace of data) if (trace.type === 'scattergl') trace.type = 'scatter';
}
for (const stage of stages) {
  const option = document.createElement('option');
  option.value = stage;
  option.textContent = stage;
  stageFilter.append(option);
}
const config = {
  responsive: true, displaylogo: false, scrollZoom: false,
  modeBarButtonsToRemove: ['toImage','lasso2d','select2d'],
};
function report(type) { window.parent.postMessage({type}, '*'); }
function failure() {
  status.hidden = false;
  status.textContent = 'The interactive plot could not be rendered.';
  report('studio-plotly-error');
}
(async () => {
  try {
    if (!window.Plotly || typeof Plotly.newPlot !== 'function') { failure(); return; }
    await Plotly.newPlot(chart, data, layout, config);
    status.hidden = true;
    document.getElementById('stage-control').hidden = stages.length === 0;
    stageFilter.addEventListener('change', () => {
      const selected = stageFilter.value;
      Plotly.restyle(chart, {visible: data.map(trace =>
        selected === 'all' || trace.meta === selected
      )});
    });
    report('studio-plotly-ready');
  } catch (_) { failure(); }
})();
"""


def render_plotly_frame(figure: dict[str, Any], theme: str) -> tuple[bytes, str]:
    """Make the complete frame from checked data, with no remote dependencies."""
    mode = theme if theme in {"light", "dark"} else "light"
    nonce = secrets.token_urlsafe(24)
    payload = json.dumps({"figure": figure}, allow_nan=False, ensure_ascii=True)
    payload = (
        payload.replace("<", r"\u003c").replace(">", r"\u003e").replace("&", r"\u0026")
    )
    start = f"""<!doctype html>
<html lang="en" data-theme="{mode}"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Interactive research figure</title>
<style>
:root{{--ink:#1c1c1e;--paper:#fff;--grid:#ddd;color-scheme:light}}
:root[data-theme=dark]{{--ink:#ececf0;--paper:#1e1e1e;--grid:#444;color-scheme:dark}}
body{{margin:0;background:var(--paper);color:var(--ink);font:14px system-ui,sans-serif}}
#stage-control{{padding:6px 12px;position:absolute;inset-inline-start:0;
  top:0;z-index:10;background:var(--paper)}}
select{{font:inherit;margin-inline-start:6px}}
#plot{{width:100%;height:calc(100vh - 42px);margin-top:42px;min-height:260px}}
#plot-status{{padding:0 12px}}
</style></head>
<body><label id="stage-control" hidden>Stage
<select id="stage-filter" aria-label="Stage">
<option value="all">All stages</option></select>
</label><div id="plot" aria-label="Interactive research figure"></div>
<p id="plot-status" role="status">Loading interactive figure…</p>
<script id="figure-data" type="application/json" nonce="{nonce}">{payload}</script>
<script nonce="{nonce}">""".encode()
    body = b"".join(
        (
            start,
            _plotly_bundle(),
            f'\n</script><script nonce="{nonce}">'.encode(),
            _FRAME_JS,
            b"\n</script></body></html>",
        )
    )
    return body, nonce
