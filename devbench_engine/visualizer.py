"""Self-contained, read-only HTML reports for stored DevBench execution evidence.

No model calls, replay, network access, or content-addressed artifact reads occur.
Only manifest/node integrity is checked; scientific validity is a separate claim.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import math
import re
from collections import Counter
from pathlib import Path

from devharness_core.provenance import canonical_json

MAX_RESULTS_BYTES = 32_000_000
MAX_ARTIFACT_BYTES = 2_000_000
MAX_AUDIT_BYTES = 16_000_000
MAX_RUNS = 1000
MAX_NODES = 512
_HASH = re.compile(r"^[0-9a-f]{64}$")


def _map(value):
    return value if isinstance(value, dict) else {}


def _items(value):
    return value if isinstance(value, list) else []


def _text(value, limit=4000):
    if value is None:
        return "N/A"
    return str(value)[:limit]


def _h(value, limit=4000):
    return html.escape(_text(value, limit), quote=True)


def _number(value):
    return value if type(value) in (int, float) and math.isfinite(value) else None


def _fmt(value, decimals=1):
    number = _number(value)
    if number is None:
        return "N/A"
    return f"{number:,.{decimals}f}" if decimals else f"{number:,.0f}"


def _digest(value):
    return hashlib.sha256(canonical_json(value)).hexdigest()


def _read_json(path, limit):
    """Bound reads even when a file grows after stat; reject non-finite JSON."""
    with path.open("rb") as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError("JSON file exceeds the report size limit")
    def reject_constant(value):
        raise ValueError("Non-finite JSON value: " + value)
    return json.loads(data, parse_constant=reject_constant)


def _local_reference(reference, source_path):
    if not source_path or not isinstance(reference, str) or not reference:
        raise ValueError("A results source path is required for local artifact reads")
    base = Path(source_path).expanduser().resolve().parent
    candidate = Path(reference).expanduser()
    if not candidate.is_absolute():
        candidate = base / candidate
    # Both lexical and resolved containment matter: do not follow symlink aliases.
    try:
        parts = candidate.relative_to(base).parts
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(base)
    except (ValueError, OSError) as error:
        raise ValueError("Artifact reference is outside the results directory or unavailable") from error
    cursor = base
    for part in parts:
        if part == "..":
            raise ValueError("Parent traversal is not permitted in artifact references")
        cursor = cursor / part
        if cursor.is_symlink():
            raise ValueError("Symlink artifact references are not permitted")
    if not resolved.is_file():
        raise ValueError("Artifact reference is not a regular file")
    return resolved


def load_audit_nodes(receipt, source_path=None):
    """Read only the receipt's manifest and nodes, bounded to its results folder.

    ``checked`` means manifest/node hashes, receipt root, and DAG links agree. It
    does not authenticate the caller, validate artifact bytes, or perform replay.
    CAS objects (including task specifications and goldens) are never opened.
    """
    receipt = _map(receipt)
    info = {"status": "unavailable", "nodes": [], "root_hash": receipt.get("root_hash"),
            "message": "Audit unavailable: no readable bounded receipt manifest."}
    try:
        path = _local_reference(receipt.get("manifest_path"), source_path)
        if path.name != "manifest.json":
            raise ValueError("Only a named audit manifest may be read")
    except (ValueError, OSError, RuntimeError):
        return info
    try:
        manifest = _read_json(path, MAX_ARTIFACT_BYTES)
        if not isinstance(manifest, dict):
            raise ValueError("Manifest is not a JSON object")
        if (manifest.get("schema_version") != "devharness.execution-dag/1"
                or manifest.get("canonicalization") != "devharness.canonical-json/1"):
            raise ValueError("Unsupported audit schema or canonicalization")
        root = manifest.get("root_hash")
        if not isinstance(root, str) or not _HASH.fullmatch(root):
            raise ValueError("Manifest root is not a SHA-256 digest")
        if root != receipt.get("root_hash"):
            raise ValueError("Manifest root does not match the supplied receipt")
        if _digest({k: v for k, v in manifest.items() if k != "root_hash"}) != root:
            raise ValueError("Manifest hash mismatch")
        ids = manifest.get("node_ids")
        if (not isinstance(ids, list) or len(ids) > MAX_NODES or len(set(ids)) != len(ids)
                or manifest.get("node_count") != len(ids)):
            raise ValueError("Invalid or oversized manifest node list")
        if not all(isinstance(node_id, str) and _HASH.fullmatch(node_id) for node_id in ids):
            raise ValueError("Invalid node digest")
        nodes, seen, previous = [], set(), None
        total_bytes = path.stat().st_size
        for sequence, node_id in enumerate(ids, 1):
            node_path = _local_reference(str(path.parent / "nodes" / (node_id + ".json")), source_path)
            total_bytes += node_path.stat().st_size
            if total_bytes > MAX_AUDIT_BYTES:
                raise ValueError("Audit node files exceed the report size limit")
            node = _read_json(node_path, MAX_ARTIFACT_BYTES)
            if not isinstance(node, dict) or node.get("node_id") != node_id:
                raise ValueError("Node identity mismatch")
            if _digest({k: v for k, v in node.items() if k != "node_id"}) != node_id:
                raise ValueError("Node hash mismatch")
            parents = node.get("parents")
            if (not isinstance(parents, list) or any(not isinstance(p, str) or p not in seen for p in parents)
                    or len(set(parents)) != len(parents) or node.get("previous") != previous
                    or node.get("sequence") != sequence or node.get("run_id") != manifest.get("run_id")
                    or node.get("schema_version") != manifest.get("schema_version")):
                raise ValueError("Invalid node parent, identity, or sequence link")
            payload = _map(node.get("payload"))
            # Deliberately select operational labels; never include task spec or arbitrary payloads.
            nodes.append({"id": node_id, "parents": parents, "kind": node.get("kind"),
                          "status": payload.get("status"), "tool_name": payload.get("tool_name"),
                          "succeeded": payload.get("succeeded"), "sequence": sequence})
            previous = node_id
            seen.add(node_id)
        info.update(status="checked", nodes=nodes, root_hash=root,
                    message="Manifest + node hashes checked against the supplied receipt. Artifacts not verified.")
    except (ValueError, TypeError, OSError, RecursionError, RuntimeError) as error:
        info.update(status="invalid", nodes=[], message="Audit unavailable or invalid: " + str(error)[:240])
    return info


def _runs(results):
    if isinstance(results, dict):
        if "task_id" in results:
            values = [results]
        else:
            values = results.get("runs", results.get("results"))
    else:
        values = results
    if not isinstance(values, list) or not all(isinstance(run, dict) for run in values):
        raise ValueError("Results must be a run object, a list of run objects, or an object containing runs/results")
    if len(values) > MAX_RUNS:
        raise ValueError(f"Reports support at most {MAX_RUNS} runs")
    return values


def _observation_for(run, observations):
    metadata = _map(run.get("metadata"))
    matches = []
    for observation in observations:
        if not isinstance(observation, dict):
            continue
        if observation.get("task_id") != run.get("task_id"):
            continue
        if observation.get("condition") != metadata.get("condition"):
            continue
        session = metadata.get("session_id")
        # A session match avoids mixing retries or paired conditions with the same task.
        if not session or observation.get("session_id") != session:
            continue
        matches.append(observation)
    return matches[0] if len(matches) == 1 else {}


def _context_models(observation):
    return list(dict.fromkeys(context["model"] for context in _items(_map(observation).get("model_contexts"))
                              if isinstance(context, dict) and isinstance(context.get("model"), str) and context["model"]))


def _control_kind(metadata):
    """Identify privileged/mock controls from execution metadata, never dataset type."""
    adapter = _map(metadata.get("adapter_observations"))
    for data in (metadata, adapter):
        mode = data.get("oracle_mode")
        enabled_mode = mode is True or (isinstance(mode, str) and mode.strip().lower() not in ("", "false", "off", "none", "disabled"))
        labels = [str(data.get(key, "")).lower().replace("_", "-")
                  for key in ("backend", "model", "actual_model", "requested_model", "billing_mode")]
        if data.get("oracle") is True or enabled_mode or any(label in (
                "oracle", "synthetic-oracle", "matrix-oracle", "matrixoracle", "matrixoracleadapter") for label in labels):
            return "oracle-control"
    if str(metadata.get("backend", "")).lower() == "mock":
        return "mock-control"
    return "non-control"


def _model(metadata, observation=None):
    control = _control_kind(metadata)
    if control == "oracle-control":
        return "Privileged synthetic oracle", "Infrastructure control · not live model inference"
    if control == "mock-control":
        return "Mock infrastructure control", "Mock backend · not live model inference"
    adapter = _map(metadata.get("adapter_observations"))
    for data in (metadata, adapter):
        if data.get("actual_model"):
            return _text(data["actual_model"], 240), "Observed model ID"
        ids = [str(value) for value in _items(data.get("model_ids")) if isinstance(value, str) and value]
        if ids:
            return ", ".join(dict.fromkeys(ids)), "Observed model IDs"
    requested = metadata.get("requested_model") or adapter.get("requested_model") or metadata.get("model")
    if requested:
        return _text(requested, 240), "Requested label · actual model unobserved"
    if _context_models(observation):
        return "Adapter model unreported", "Model ID available in supplemental session context"
    return "Model unobserved", "Actual model ID unavailable"


def _tool_summary(metadata):
    calls = [event for event in _items(metadata.get("tool_calls")) if isinstance(event, dict)]
    succeeded = sum(event.get("succeeded") is True for event in calls)
    canceled = sum(event.get("succeeded") is not True and "cancel" in str(event.get("error", "")).lower()
                   for event in calls)
    return calls, succeeded, canceled


def _metric(label, value, note, accent=""):
    return f'<article class="metric {accent}"><p class="eyebrow">{_h(label)}</p><strong>{_h(value)}</strong><p>{_h(note)}</p></article>'


def _ledger(pairs):
    return '<dl class="ledger">' + "".join(f'<div><dt>{_h(key)}</dt><dd>{_h(value)}</dd></div>' for key, value in pairs) + '</dl>'


def _svg_start(label, width=720, height=220, css=""):
    return f'<svg class="{css}" viewBox="0 0 {width} {height}" role="img" aria-label="{_h(label)}" xmlns="http://www.w3.org/2000/svg"><title>{_h(label)}</title>'


def _memory_chart(metadata):
    samples = _items(metadata.get("memory_samples"))
    sampling = _map(metadata.get("memory_sampling"))
    points, unit = [], None
    for sample in samples[:4000]:
        sample = _map(sample)
        when = _number(sample.get("elapsed_sec"))
        if _number(sample.get("rss_bytes")) is not None:
            value, this_unit = sample["rss_bytes"] / 1048576, "MiB"
        else:
            value = _number(sample.get("rss_mb"))
            this_unit = _text(metadata.get("memory_unit") or "MB")
        if when is None or value is None or when < 0 or value < 0:
            continue
        if unit is not None and this_unit != unit:
            continue
        unit = this_unit
        points.append((when, value))
    if points:
        points.sort()
        max_x, max_y = max(p[0] for p in points) or 1, max(p[1] for p in points) or 1
        coordinates = " ".join(f"{50 + 620 * x / max_x:.2f},{165 - 120 * y / max_y:.2f}" for x, y in points)
        chart = _svg_start("Recorded memory samples; elapsed seconds and " + unit)
        chart += '<path class="axis" d="M50 30V165H680"/>'
        chart += f'<polyline class="memory-series" points="{coordinates}"/>'
        for x, y in points:
            chart += f'<circle class="sample-dot" cx="{50 + 620*x/max_x:.2f}" cy="{165 - 120*y/max_y:.2f}" r="2"><title>{_fmt(x)} s · {_fmt(y)} {_h(unit)}</title></circle>'
        chart += f'<text x="50" y="195">0 s</text><text x="615" y="195">{_fmt(max_x)} s</text><text x="50" y="22">{_fmt(max_y)} {_h(unit)}</text></svg>'
        scope = _h(sampling.get("scope") or "Scope unrecorded")
        interval = _fmt(sampling.get("interval_sec"))
        completeness = "complete" if sampling.get("complete") is True else "incomplete" if sampling.get("complete") is False else "completeness unreported"
        thinning = f' · retained stride {_h(sampling.get("retained_stride"))}' if sampling.get("retained_stride") is not None else ''
        total = f' · {_fmt(sampling.get("successful_sample_count"), 0)} successful polls' if sampling.get("successful_sample_count") is not None else ''
        return (chart + f'<p class="caption">{len(points)} recorded samples · {_h(unit)} · elapsed seconds. '
                f'Sampling: {scope} · {interval} s interval · {completeness}{thinning}{total}. '
                'Lines connect observed samples; gaps between samples are unmeasured. '
                'Summed process RSS can double-count shared pages and miss between-poll peaks.</p>')
    peaks = []
    for index, turn in enumerate(_items(metadata.get("turns"))):
        turn = _map(turn)
        value = _number(turn.get("peak_rss_mb"))
        if value is not None and value >= 0:
            peaks.append((turn.get("phase") or f"Phase {index + 1}", value,
                          turn.get("memory_measurement") or "Sampling method unrecorded"))
    if not peaks and _number(metadata.get("peak_rss_mb")) is not None:
        adapter = _map(metadata.get("adapter_observations"))
        peaks = [("Run", metadata["peak_rss_mb"], adapter.get("memory_measurement") or "Sampling method unrecorded")]
    if not peaks:
        return '<p class="empty">Memory series unavailable. No recorded phase peaks or samples.</p>'
    max_y = max(value for _, value, _ in peaks) or 1
    chart = _svg_start("Phase peaks only; disconnected RSS peak markers in MiB")
    chart += '<path class="axis" d="M50 30V165H680"/>'
    for index, (phase, value, method) in enumerate(peaks[:16]):
        x = 70 + 580 * (index + .5) / min(len(peaks), 16)
        y = 160 - 105 * value / max_y
        chart += f'<circle class="memory-peak" cx="{x:.2f}" cy="{y:.2f}" r="6"><title>{_h(phase)}: {_fmt(value)} MiB; {_h(method)}</title></circle>'
        chart += f'<text x="{x:.2f}" y="{y-15:.2f}" text-anchor="middle">{_fmt(value)}</text><text x="{x:.2f}" y="190" text-anchor="middle">{_h(phase, 18)}</text>'
    chart += '<text x="50" y="22">RSS · MiB</text></svg>'
    methods = ", ".join(dict.fromkeys(str(method) for _, _, method in peaks))
    return chart + f'<p class="caption"><strong>Phase peaks only.</strong> Disconnected markers are maxima within each phase, not measurements at a common instant. Sampling: {_h(methods)}. Historical peak_rss_mb stores MiB (2²⁰ bytes). Peak timing is unknown.</p>'


def _dag_chart(audit, index):
    nodes = audit["nodes"]
    if not nodes:
        return '<p class="empty">No checked execution nodes available to draw.</p>'
    visible = nodes[:48]
    columns, width = 3, 840
    height = math.ceil(len(visible) / columns) * 112 + 22
    locations = {node["id"]: (22 + (i % columns) * 276, 16 + (i // columns) * 112)
                 for i, node in enumerate(visible)}
    marker = f"dag-arrow-{index}"
    chart = _svg_start("Stored execution DAG; arrows follow recorded parent links", width, height, "execution-dag")
    chart += f'<defs><marker id="{marker}" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0 0L8 4L0 8" fill="currentColor"/></marker></defs>'
    for node in visible:
        x, y = locations[node["id"]]
        for parent in node["parents"]:
            if parent in locations:
                px, py = locations[parent]
                chart += f'<path class="dag-edge" d="M{px+244} {py+38} C{px+268} {py+38},{x-14} {y+38},{x} {y+38}" marker-end="url(#{marker})"/>'
    for node in visible:
        x, y = locations[node["id"]]
        failed = node.get("succeeded") is False or node.get("status") == "failed" or node.get("kind") == "tool_failed"
        chart += f'<g class="dag-node"><title>{_h(node["kind"])} · {_h(node["id"])} · {_h(node.get("tool_name"))}</title><rect x="{x}" y="{y}" width="244" height="78" rx="6" class="{"node-failed" if failed else "node-neutral"}"/><text x="{x+13}" y="{y+25}">{node["sequence"]:02d} · {_h(node["kind"], 25)}</text><text x="{x+13}" y="{y+48}" class="node-label">{_h(node.get("tool_name") or node.get("status") or "Recorded event", 29)}</text><text x="{x+13}" y="{y+66}" class="node-hash">{_h(node["id"][:18])}…</text></g>'
    chart += '</svg>'
    note = f"Showing {len(visible)} of {len(nodes)} checked nodes. " if len(nodes) > len(visible) else f"{len(nodes)} checked nodes. "
    return '<div class="graph-scroll" tabindex="0" aria-label="Scrollable execution graph">' + chart + '</div><p class="caption">' + note + 'Arrows show stored parents; layout position is not elapsed time. Node integrity does not imply tool success.</p>'


def _grade_panel(run, metadata):
    control = _control_kind(metadata) != "non-control"
    grade = run.get("grader_result")
    if not isinstance(grade, dict):
        if control:
            return '<p class="empty">Control not graded. No control grader result was recorded; control score and gate outcomes are N/A.</p>'
        return '<p class="empty">Not graded. No grader result was recorded; scientific score, passed gates, and veto outcomes are N/A.</p>'
    rows = []
    def collect(result, name, depth=0):
        if depth > 8 or len(rows) >= 100 or not isinstance(result, dict):
            return
        passed = result.get("passed")
        if result.get("role") == "hard_fail":
            state = "Triggered" if passed is True else "Not triggered" if passed is False else "Unreported"
        else:
            state = "Passed" if passed is True else "Failed" if passed is False else "Unreported"
        rows.append('<tr><th scope="row">' + _h(result.get("name") or result.get("role") or name)
                    + '</th><td>' + state + '</td><td>' + _h(result.get("score"))
                    + '</td><td>' + _h(result.get("reasoning") or "No rationale recorded") + '</td></tr>')
        children = result.get("children", result.get("results", []))
        for child_index, child in enumerate(_items(children), 1):
            collect(child, f"Gate {child_index}", depth + 1)
    collect(grade, "Overall result")
    veto = metadata.get("statistical_invariant_veto")
    if isinstance(veto, dict):
        collect(veto, "Statistical invariant veto")
    caption = "Stored control grader outcomes" if control else "Stored grader outcomes"
    return '<div class="table-scroll"><table class="mini-table"><caption>' + caption + '</caption><thead><tr><th>Gate / role</th><th>Outcome</th><th>Score</th><th>Recorded rationale</th></tr></thead><tbody>' + ''.join(rows) + '</tbody></table></div>'


def _science_panels(metadata, source_path):
    """Plot only explicit report artifacts, never infer curves from answer prose.

    report_artifacts may be an inline object or a bounded local JSON filename.
    Curves: expression_curves[{gene,x_label,y_label,points:[{x,prediction,
    ci_lower?,ci_upper?}]}]. Lineage: {nodes:[{id,label?}],edges:[{source,target}]}.
    These panels visualize supplied values; no fitting or CI calculation occurs.
    """
    artifacts = metadata.get("report_artifacts")
    if isinstance(artifacts, str):
        try:
            artifacts = _read_json(_local_reference(artifacts, source_path), MAX_ARTIFACT_BYTES)
        except (ValueError, OSError, RuntimeError):
            return '<p class="empty">Optional scientific artifact unavailable or outside the results directory.</p>'
    artifacts = _map(artifacts)
    result = []
    for curve in _items(artifacts.get("expression_curves"))[:12]:
        curve = _map(curve)
        raw = _items(curve.get("points"))[:2000]
        points = [p for p in raw if isinstance(p, dict) and _number(p.get("x")) is not None
                  and _number(p.get("prediction")) is not None]
        if len(points) < 2:
            continue
        points.sort(key=lambda p: p["x"])
        has_band = all(_number(p.get("ci_lower")) is not None and _number(p.get("ci_upper")) is not None
                       and p["ci_lower"] <= p["prediction"] <= p["ci_upper"] for p in points)
        xs = [p["x"] for p in points]
        ys = [p["prediction"] for p in points]
        if has_band:
            ys += [p["ci_lower"] for p in points] + [p["ci_upper"] for p in points]
        lo_x, hi_x, lo_y, hi_y = min(xs), max(xs), min(ys), max(ys)
        def xy(x, y):
            return f"{50+620*(x-lo_x)/(hi_x-lo_x or 1):.2f},{165-120*(y-lo_y)/(hi_y-lo_y or 1):.2f}"
        chart = _svg_start("Supplied predictions for " + _text(curve.get("gene")), css="expression-chart")
        chart += '<path class="axis" d="M50 30V165H680"/>'
        if has_band:
            polygon = [xy(p["x"], p["ci_lower"]) for p in points] + [xy(p["x"], p["ci_upper"]) for p in reversed(points)]
            chart += '<polygon class="confidence-band" points="' + ' '.join(polygon) + '"/>'
        chart += '<polyline class="prediction-line" points="' + ' '.join(xy(p["x"], p["prediction"]) for p in points) + '"/>'
        chart += f'<text x="50" y="22">{_h(curve.get("y_label") or "Expression units unspecified")}</text><text x="50" y="190">{_fmt(lo_x)}</text><text x="630" y="190">{_fmt(hi_x)}</text><text x="360" y="214" text-anchor="middle">{_h(curve.get("x_label") or "Recorded x coordinate")}</text></svg>'
        note = ' Supplied confidence limits shown; coverage level is ' + _text(curve.get("confidence_level") or "unspecified") + '.' if has_band else ' Confidence interval unavailable.'
        result.append('<section class="panel"><h3>' + _h(curve.get("gene") or "Recorded expression") + '</h3>' + chart + '<p class="caption">Predictions supplied in report_artifacts. No fit performed by this report.' + _h(note) + '</p></section>')
    curve_count = len(result)
    lineage_available = False
    lineage = _map(artifacts.get("lineage"))
    nodes = [n for n in _items(lineage.get("nodes")) if isinstance(n, dict) and isinstance(n.get("id"), str)]
    if nodes and len(nodes) <= 80 and len({n["id"] for n in nodes}) == len(nodes):
        edges = [edge for edge in _items(lineage.get("edges"))[:1000] if isinstance(edge, dict)]
        height = max(200, math.ceil(len(nodes) / 4) * 90)
        positions = {n["id"]: (90 + (i % 4) * 180, 50 + (i // 4) * 90) for i, n in enumerate(nodes)}
        chart = _svg_start("Supplied lineage graph; layout does not encode pseudotime", 720, height, "lineage-chart")
        for edge in edges:
            if edge.get("source") in positions and edge.get("target") in positions:
                x1, y1 = positions[edge["source"]]
                x2, y2 = positions[edge["target"]]
                chart += f'<path class="dag-edge" d="M{x1} {y1}L{x2} {y2}"><title>{_h(edge["source"])} → {_h(edge["target"])}</title></path>'
        for node in nodes:
            x, y = positions[node["id"]]
            chart += f'<circle class="sample-dot" cx="{x}" cy="{y}" r="8"/><text x="{x}" y="{y+26}" text-anchor="middle">{_h(node.get("label") or node["id"], 22)}</text>'
        result.append('<section class="panel"><h3>Recorded lineage graph</h3>' + chart + '</svg><p class="caption">Supplied nodes and edges only. Layout is schematic; pseudotime is an inferred ordering and does not establish causal lineage.</p></section>')
        lineage_available = True
    missing = []
    if not curve_count:
        missing.append("Expression curves and confidence intervals unavailable.")
    if not lineage_available:
        missing.append("Lineage graph unavailable.")
    if missing:
        result.append('<section class="panel"><h3>Scientific artifact availability</h3><p class="caption">'
                      + ' '.join(missing) + ' No usable artifacts were supplied for these panels.</p></section>')
    return ''.join(result)


def _observation_panel(observation, source):
    if not observation:
        return ''
    models = _context_models(observation)
    usage = _map(observation.get("last_token_usage"))
    errors = [error for error in _items(observation.get("observed_error_lines")) if isinstance(error, str)]
    body = _ledger([("Source", source or "Caller-supplied observation object"),
                    ("Session", observation.get("session_id")),
                    ("Model context", ", ".join(models) or "N/A"),
                    ("Last observed total tokens", _fmt(usage.get("total_tokens"), 0))])
    if errors:
        body += '<ul class="error-list">' + ''.join('<li>' + _h(error, 500) + '</li>' for error in errors[:12]) + '</ul>'
    return '<section class="panel supplemental"><h3>Supplemental session observations</h3><p class="caption">Separately supplied context, matched by task, condition, and session ID. It does not replace measured run telemetry or establish complete usage.</p>' + body + '</section>'


def _run_detail(run, index, audit, observation, source_path, observations_source):
    metadata = _map(run.get("metadata"))
    evaluation_label = "Control evaluation" if _control_kind(metadata) != "non-control" else "Scientific evaluation"
    model, model_source = _model(metadata, observation)
    calls, succeeded, canceled = _tool_summary(metadata)
    environment = _map(metadata.get("environment"))
    tokens = metadata.get("tokens")
    if isinstance(tokens, dict):
        tokens = tokens.get("total_tokens", tokens.get("total"))
    if tokens is None:
        tokens = _map(metadata.get("usage")).get("total_tokens")
    billing = "Unknown cost" if _number(metadata.get("cost_usd")) is None else "$" + _fmt(metadata["cost_usd"], 4)
    usage_label = "Complete usage" if metadata.get("usage_complete") is True else "Partial usage" if metadata.get("usage_complete") is False else "Usage completeness unreported"
    runtime = _ledger([("Model", model), ("Model source", model_source),
                       ("Elapsed time", _fmt(metadata.get("elapsed_sec")) + " s"),
                       ("Failure category", metadata.get("failure_type") or "None recorded"),
                       ("Process return code", metadata.get("returncode")),
                       ("Token count", _fmt(tokens, 0)), ("Usage scope", usage_label),
                       ("Cost", billing), ("Billing mode", metadata.get("billing_mode")),
                       ("Platform", " / ".join(str(environment[k]) for k in ("platform", "machine") if environment.get(k)) or "N/A"),
                       ("Python", environment.get("python")),
                       ("Harness peak memory (MB)", _fmt(metadata.get("peak_memory_mb"))),
                       ("Release status", metadata.get("release_status") or "Not recorded")])
    if metadata.get("error"):
        runtime += '<p class="error-message">' + _h(metadata["error"]) + '</p>'
    tool_rows = []
    for event in calls[:200]:
        state = "Succeeded" if event.get("succeeded") is True else "Canceled" if "cancel" in str(event.get("error", "")).lower() else "Failed" if event.get("succeeded") is False else "Unknown"
        tool_rows.append('<tr><th scope="row">' + _h(event.get("tool_name")) + '</th><td>' + state + '</td><td>' + _h(event.get("error") or "—", 700) + '</td></tr>')
    tool_note = f"{len(calls)} attempts · {succeeded} succeeded · {canceled} canceled"
    tools_html = '<p class="caption">' + tool_note + '. Capture scope: ' + ('registered domain tools' if metadata.get("tool_capture_complete") is True else 'incomplete or unreported; an empty list does not prove no tools ran') + '.</p>'
    if tool_rows:
        tools_html += '<div class="table-scroll"><table class="mini-table"><caption>Recorded domain tool calls</caption><thead><tr><th>Tool</th><th>Outcome</th><th>Recorded error</th></tr></thead><tbody>' + ''.join(tool_rows) + '</tbody></table></div>'
        if len(calls) > 200:
            tools_html += '<p class="caption">Showing the first 200 calls.</p>'
    fingerprints = _ledger([("Receipt root", audit.get("root_hash")),
                            ("Analysis fingerprint", metadata.get("analysis_fingerprint")),
                            ("Dataset hash", metadata.get("dataset_hash")),
                            ("Code hash", metadata.get("code_hash")),
                            ("Task specification hash", metadata.get("task_spec_hash"))])
    return (f'<div class="detail-grid"><section class="panel"><h3>Runtime &amp; telemetry</h3>{runtime}</section>'
            f'<section class="panel"><h3>Memory observations</h3>{_memory_chart(metadata)}</section></div>'
            f'<section class="panel"><h3>{evaluation_label}</h3>{_grade_panel(run, metadata)}</section>'
            f'<section class="panel"><h3>Tool execution</h3>{tools_html}</section>'
            f'<section class="panel audit-panel"><div class="panel-heading"><h3>Execution evidence</h3><span class="tag">{_h(audit["status"])}</span></div>'
            f'<p class="caption">{_h(audit["message"])}</p>{_dag_chart(audit, index)}{fingerprints}</section>'
            + _observation_panel(observation, observations_source) + _science_panels(metadata, source_path))


def render_report(results, source_path=None, *, session_observations=None,
                  observations_source=None, title="DevBench · execution report") -> str:
    """Render a standalone report without mutating results or accessing the network.

    Local manifests and explicit report_artifacts are read only under the source
    results file's directory. Omit source_path to disable all local artifact reads.
    Arbitrary answers, stdout/stderr, tool arguments, and unknown metadata are not
    copied into the report. Operational errors are included as escaped text.
    """
    runs = _runs(results)
    observations = _items(session_observations)
    if isinstance(session_observations, dict):
        observations = _items(session_observations.get("observations"))
    audits = [load_audit_nodes(_map(run.get("metadata")).get("audit"), source_path) for run in runs]
    controls = [run for run in runs if _control_kind(_map(run.get("metadata"))) != "non-control"]
    non_controls = [run for run in runs if _control_kind(_map(run.get("metadata"))) == "non-control"]
    oracle_count = sum(_control_kind(_map(run.get("metadata"))) == "oracle-control" for run in controls)
    control_graded = sum(isinstance(run.get("grader_result"), dict) for run in controls)
    control_passed = sum(_map(run.get("grader_result")).get("passed") is True for run in controls)
    graded = sum(isinstance(run.get("grader_result"), dict) for run in non_controls)
    passed = sum(_map(run.get("grader_result")).get("passed") is True for run in non_controls)
    interrupted = sum(_map(run.get("metadata")).get("failure_type") in ("resource_interrupt", "adapter_error", "runtime_error") for run in runs)
    tool_totals = [_tool_summary(_map(run.get("metadata"))) for run in runs]
    attempts = sum(len(calls) for calls, _, _ in tool_totals)
    successes = sum(success for _, success, _ in tool_totals)
    canceled = sum(cancel for _, _, cancel in tool_totals)
    tracks = sorted({_text(_map(run.get("metadata")).get("track") or "Unspecified") for run in runs})
    conditions = sorted({_text(_map(run.get("metadata")).get("condition") or "Unspecified") for run in runs})
    metrics = _metric("Recorded runs", str(len(runs)), f"{len(tracks)} tracks · {len(conditions)} conditions")
    if controls and not non_controls:
        metrics += _metric("Control evaluation", f"{control_graded} / {len(controls)}", f"Infrastructure control grades · {control_passed} passed", "metric-ivory")
    else:
        label = "Non-control evaluation" if controls else "Scientific evaluation"
        metrics += _metric(label, f"{graded} / {len(non_controls)}", f"Graded runs · {passed} passed" if graded else "No scientific outcomes available", "metric-ivory")
    metrics += _metric("Captured domain tools", str(successes), f"{attempts} attempts · {successes} succeeded · {canceled} canceled")
    metrics += _metric("Execution interruptions", str(interrupted), "Recorded runtime / adapter failures", "metric-alert" if interrupted else "")
    rows = []
    for index, run in enumerate(runs):
        metadata = _map(run.get("metadata"))
        control_kind = _control_kind(metadata)
        grade = run.get("grader_result")
        state = "not-graded" if not isinstance(grade, dict) else "passed" if grade.get("passed") is True else "failed" if grade.get("passed") is False else "unreported"
        state_label = {"not-graded": "Not graded", "passed": "Passed", "failed": "Failed", "unreported": "Unreported"}[state]
        if control_kind != "non-control":
            state_label = "Control " + state_label.lower()
        task = _text(run.get("task_id") or f"Run {index + 1}")
        track = _text(metadata.get("track") or "Unspecified")
        condition = _text(metadata.get("condition") or "Unspecified")
        obs = _observation_for(run, observations)
        model, model_source = _model(metadata, obs)
        elapsed = _number(metadata.get("elapsed_sec"))
        score = _number(grade.get("score")) if isinstance(grade, dict) else None
        subtitle = '<span class="model-label">' + _h(model) + '</span><span class="caption">' + _h(model_source) + '</span>'
        if obs and model == "Adapter model unreported":
            context_models = _context_models(obs)
            if context_models:
                subtitle += '<span class="supplement-label">Context: ' + _h(", ".join(context_models)) + ' · supplemental</span>'
        attributes = f'data-task="{_h(task)}" data-track="{_h(track)}" data-condition="{_h(condition)}" data-state="{state}" data-model="{_h(model)}" data-elapsed="{elapsed if elapsed is not None else ""}" data-evidence-kind="{control_kind}"'
        detail = _run_detail(run, index, audits[index], obs, source_path, observations_source)
        rows.append(f'<tbody class="run-group" {attributes}><tr class="run-row"><th scope="row"><span class="run-number">{index+1:02d}</span><a href="#detail-{index}" class="task-link" data-open="detail-{index}">{_h(task)}</a><span class="caption">{_h(track)}</span></th><td><span class="tag">{_h(condition)}</span></td><td>{subtitle}</td><td><span class="status {state}">{state_label}</span><span class="caption">{_h(metadata.get("failure_type") or "")}</span></td><td class="numeric">{_fmt(score, 3)}</td><td class="numeric">{_fmt(elapsed)}<span class="caption">seconds</span></td><td><button type="button" class="inspect-button" aria-label="Inspect {_h(task)} {_h(condition)}" aria-expanded="false" aria-controls="detail-{index}" data-open="detail-{index}">Inspect <span aria-hidden="true">↗</span></button></td></tr><tr class="detail-row" id="detail-{index}" hidden><td colspan="7">{detail}</td></tr></tbody>')
    options = lambda values: ''.join(f'<option value="{_h(value)}">{_h(value)}</option>' for value in values)
    checked = sum(audit["status"] == "checked" for audit in audits)
    source_name = str(Path(source_path).expanduser().resolve()) if source_path else "In-memory results supplied by caller"
    source_hash = "Unavailable · no readable source file"
    if source_path:
        try:
            source = Path(source_path).expanduser().resolve()
            with source.open("rb") as stream:
                raw = stream.read(MAX_RESULTS_BYTES + 1)
            if len(raw) <= MAX_RESULTS_BYTES:
                source_hash = hashlib.sha256(raw).hexdigest()
        except OSError:
            pass
    narrative = "No scientific results were graded. Runtime failures and captured tool attempts cannot establish biological performance." if not graded else "Scientific outcomes below come from recorded grader results. Missing grades remain N/A; execution evidence and biological validity are separate."
    control_summary = ""
    if controls:
        narrative = (f"{len(controls)} infrastructure controls, including {oracle_count} privileged synthetic oracle runs. "
                     "Control grades do not measure live-model performance. "
                     f"{len(non_controls)} non-control runs are reported separately; {graded} were graded.")
        control_summary = f'<p>{len(controls)} control runs · {control_graded} graded · {control_passed} passed</p>'
    cost_values = [_number(_map(run.get("metadata")).get("cost_usd")) for run in runs]
    known_costs = [cost for cost in cost_values if cost is not None]
    cost_label = ("$" + _fmt(sum(known_costs), 4) + f" reported subtotal · {len(known_costs)}/{len(runs)} runs") if known_costs else "Unknown cost · no measured USD values"
    partial = sum(_map(run.get("metadata")).get("usage_complete") is not True for run in runs)
    body = f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data:; font-src 'none'; connect-src 'none'; base-uri 'none'; form-action 'none'">
<title>{_h(title)}</title><style>{_CSS}</style></head><body>
<a class="skip-link" href="#run-ledger">Skip to run ledger</a>
<div class="masthead"><a href="#top" class="brand" aria-label="DevBench report top"><span class="brand-mark" aria-hidden="true">D/</span> DEV<span>BENCH</span></a><span class="edition">RESEARCH SYSTEMS / EXECUTION RECORD</span><span class="offline"><i aria-hidden="true"></i> OFFLINE REPORT</span></div>
<main id="top"><header class="hero"><div><p class="eyebrow">Evidence before interpretation</p><h1>Every run.<br><em>Every boundary.</em></h1><p class="lede">A measured view of agent execution, scientific evaluation, and the evidence that connects them.</p></div><aside class="hero-note"><span class="issue">01 / EXECUTION ATLAS</span><p>{_h(narrative)}</p><a href="#evidence-notes">Read the evidence scope <span aria-hidden="true">↘</span></a></aside></header>
<section class="metrics" aria-label="Overall recorded results">{metrics}</section>
<div class="summary-strip">{control_summary}<p><span class="dot" aria-hidden="true"></span> {checked}/{len(runs)} manifest + node chains checked</p><p>{_h(cost_label)}</p><p>{partial}/{len(runs)} runs with incomplete or unknown usage</p></div>
<section id="run-ledger" class="run-ledger" aria-labelledby="ledger-title"><div class="section-heading"><div><p class="eyebrow">01 / Run ledger</p><h2 id="ledger-title">Inspect the record.</h2></div><p>All values come from supplied results.<br>Expand a run to trace its evidence.</p></div>
<div class="filters"><label class="search-field">Search runs<input id="search" type="search" placeholder="Task, model, condition…" autocomplete="off"></label><label>Track<select id="track"><option value="">All tracks</option>{options(tracks)}</select></label><label>Condition<select id="condition"><option value="">All conditions</option>{options(conditions)}</select></label><label>Outcome<select id="outcome"><option value="">All outcomes</option><option value="not-graded">Not graded</option><option value="passed">Passed</option><option value="failed">Failed</option><option value="unreported">Unreported</option></select></label><label>Sort<select id="sort"><option value="recorded">Recorded order</option><option value="task">Task A–Z</option><option value="runtime-desc">Runtime: longest first</option><option value="runtime-asc">Runtime: shortest first</option><option value="model">Model A–Z</option></select></label><button type="button" id="reset">Reset</button></div>
<p id="result-count" class="filter-count" role="status" aria-live="polite">{len(runs)} of {len(runs)} runs shown</p>
<div class="table-scroll ledger-scroll" tabindex="0" aria-label="Scrollable run ledger"><table id="runs-table"><caption class="sr-only">Recorded runs, scientific scores, and runtime</caption><thead><tr><th scope="col">Task / track</th><th scope="col">Condition</th><th scope="col">Model identity</th><th scope="col">Outcome</th><th scope="col">Score</th><th scope="col">Runtime</th><th scope="col"><span class="sr-only">Details</span></th></tr></thead>{''.join(rows)}</table></div><p id="no-results" class="empty"{'' if not runs else ' hidden'}>{'No runs supplied. Add a results JSON file to inspect recorded evidence.' if not runs else 'No runs match the selected filters.'}</p></section>
<section id="evidence-notes" class="evidence-notes" aria-labelledby="scope-title"><div><p class="eyebrow">02 / Evidence scope</p><h2 id="scope-title">Traceable does not<br>mean validated.</h2></div><div class="scope-copy"><p>The execution DAG is tamper-evident against an externally retained root. This report compares stored manifest and node hashes with the supplied receipt; it does not authenticate that receipt or verify artifact bytes. It is not a compliance certificate or an authenticated electronic signature.</p><p>Tool failure, missing telemetry, and absent grader output remain visible. An ungraded run may carry a raw score of zero in its source; its scientific score here is N/A. Supplemental session context does not overwrite run measurements.</p><p>Clinical release remains held pending qualified independent validation, authenticated approval, signature, and operational controls. Network associations, inferred lineage, and supplied prediction bands have only their stated evidence scope.</p></div></section>
<footer><div><span class="brand-footer">DEV / BENCH</span><p>Self-contained HTML · system fonts · no network requests</p></div><dl><dt>Results source</dt><dd>{_h(source_name)}</dd><dt>Source file SHA-256</dt><dd>{_h(source_hash)}</dd></dl></footer></main><script>{_JS}</script></body></html>'''
    return body


def main(argv=None):
    parser = argparse.ArgumentParser(description="Render stored DevBench results as a self-contained offline HTML report.")
    parser.add_argument("results", type=Path, help="Results JSON file (read only)")
    parser.add_argument("--output", required=True, type=Path, help="Standalone HTML output")
    parser.add_argument("--session-observations", type=Path, help="Optional separately labeled session observations JSON")
    parser.add_argument("--title", default="DevBench · execution report")
    args = parser.parse_args(argv)
    source = args.results.expanduser().resolve()
    output = args.output.expanduser().resolve()
    observations_path = args.session_observations.expanduser().resolve() if args.session_observations else None
    if output in (source, observations_path):
        parser.error("Output must not overwrite a read-only input file")
    if output.suffix.lower() not in (".html", ".htm"):
        parser.error("Output must have an .html or .htm extension")
    try:
        results = _read_json(source, MAX_RESULTS_BYTES)
        observations = _read_json(observations_path, MAX_RESULTS_BYTES) if observations_path else None
        report = render_report(results, source, session_observations=observations,
                               observations_source=observations_path, title=args.title)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(report, encoding="utf-8")
    except (ValueError, TypeError, OSError, RecursionError) as error:
        parser.error(str(error))
    print(f"Offline report written: {output}")
    return 0


_CSS = r'''
:root{color-scheme:light;--ink:#17252d;--navy:#132530;--ivory:#f5f2e9;--paper:#fdfcf8;--line:#d7d8d1;--muted:#56646c;--teal:#237c72;--rust:#ae4b33;--mono:Menlo,Monaco,Consolas,monospace;--body:"Avenir Next",Avenir,"Segoe UI",sans-serif}*{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;background:var(--ivory);color:var(--ink);font-family:var(--body);font-size:15px;line-height:1.55}a{color:inherit}button,input,select{font:inherit}button,a,input,select,[tabindex]{outline-offset:5px}button:focus-visible,a:focus-visible,input:focus-visible,select:focus-visible,[tabindex]:focus-visible{outline:3px solid #168477}button{cursor:pointer}button:disabled{cursor:default}button:hover{background:#e4ece5}[hidden]{display:none!important}.skip-link{position:fixed;top:-60px;left:20px;background:var(--paper);padding:12px;z-index:9}.skip-link:focus{top:10px}.masthead{padding:23px 5vw;display:flex;align-items:center;justify-content:space-between;gap:20px;background:var(--navy);color:var(--ivory);border-bottom:1px solid #35505b}.brand{text-decoration:none;font-weight:700;letter-spacing:.16em;font-size:14px;white-space:nowrap}.brand>span:last-child{font-weight:400}.brand-mark{font-family:Georgia,serif;margin-right:14px;font-style:italic;font-size:27px;letter-spacing:-4px}.edition,.offline,.eyebrow,.issue{font-family:var(--mono);font-size:10px;letter-spacing:.12em;font-weight:400}.edition{color:#a8b7bb}.offline{white-space:nowrap}.offline i{display:inline-block;width:6px;height:6px;border-radius:50%;background:#a7c6a9;margin:0 8px 1px 0}main{max-width:1600px;margin:auto;padding:0 5vw}.hero{display:grid;grid-template-columns:1.45fr 1fr;gap:10%;padding:72px 0 54px;align-items:end}.eyebrow{color:var(--muted);text-transform:uppercase;margin:0 0 20px}h1,h2{font-family:Georgia,"Times New Roman",serif;letter-spacing:-.055em;font-weight:400;line-height:1.03;margin:0}h1{font-size:clamp(52px,6.5vw,94px)}h1 em{font-weight:400;color:#466657}.lede{max-width:440px;font-size:17px;color:var(--muted);margin-top:25px}.hero-note{border-top:1px solid var(--ink);padding-top:20px;margin-bottom:20px;max-width:385px}.hero-note p{font-family:Georgia,serif;font-size:21px;line-height:1.48;margin:22px 0}.hero-note a{font-size:12px;text-decoration:none;border-bottom:1px solid #91a39a;padding-bottom:4px}.issue{color:var(--muted)}.metrics{display:grid;grid-template-columns:repeat(4,1fr);background:var(--navy);gap:1px;border:1px solid var(--navy);border-radius:3px;overflow:hidden}.metric{background:var(--navy);color:var(--ivory);padding:26px 25px 23px;min-width:0;border-right:1px solid #405059}.metric:last-child{border-right:0}.metric .eyebrow{color:#b8c6c8;font-size:10px;min-height:32px;margin-bottom:12px}.metric strong{font-family:Georgia,serif;font-weight:400;font-size:52px;line-height:1.15;letter-spacing:-.055em}.metric p:last-child{font-size:11px;color:#bcc8cc;margin-bottom:0;line-height:1.6}.metric-ivory{background:#e6e8d7;color:var(--ink)}.metric-ivory .eyebrow,.metric-ivory p:last-child{color:#445651}.metric-alert strong{color:#edaf91}.summary-strip{display:flex;gap:25px;justify-content:space-between;flex-wrap:wrap;border-bottom:1px solid var(--line);padding:14px 0}.summary-strip p{font-size:11px;color:var(--muted);margin:0}.dot{width:5px;height:5px;display:inline-block;background:var(--teal);border-radius:50%;margin:0 5px 2px 0}.run-ledger{padding-top:64px}.section-heading{display:flex;justify-content:space-between;gap:30px;align-items:end;margin-bottom:30px}.section-heading .eyebrow{margin-bottom:12px}h2{font-size:43px}.section-heading>p{font-size:12px;color:var(--muted);margin:0}.filters{display:flex;align-items:end;gap:12px;flex-wrap:wrap;background:#eaece5;border:1px solid var(--line);padding:18px}.filters label{font-size:10px;letter-spacing:.04em;display:flex;flex-direction:column;gap:7px;color:#43534f}.filters select,.filters input{background:var(--paper);border:1px solid #bfc9c3;border-radius:3px;color:var(--ink);padding:9px 12px;min-height:40px;font-size:12px}.filters input{min-width:180px;width:100%}.filters .search-field{flex:1;min-width:180px}.filters button{border:1px solid #bfc9c3;border-radius:3px;min-height:40px;padding:7px 13px;background:transparent;font-size:12px}.filter-count{font-family:var(--mono);font-size:10px;color:var(--muted);margin:16px 0 12px}.table-scroll{overflow-x:auto;max-width:100%;overscroll-behavior-x:contain}.ledger-scroll{border-top:1px solid var(--ink);border-bottom:1px solid var(--line)}table{border-collapse:collapse;width:100%;text-align:left;font-size:12px}#runs-table{min-width:900px}th{font-weight:500}thead th{font-size:10px;color:var(--muted);letter-spacing:.025em;background:#eaece5;padding:15px 15px;border-bottom:1px solid var(--line);white-space:nowrap}.run-row th,.run-row td{vertical-align:top;padding:23px 15px;border-top:1px solid var(--line)}.run-row{background:var(--paper)}.run-row:hover{background:#f1f4ed}.run-row th{width:25%;min-width:225px}.run-number{font:10px var(--mono);color:#84918b;display:block;margin-bottom:7px}.task-link{font-weight:600;text-decoration:none;overflow-wrap:anywhere;display:block;font-size:13px}.task-link:hover{text-decoration:underline}.caption{font-size:11px;line-height:1.65;color:var(--muted);font-weight:400}.run-row .caption{display:block;font-size:10px;margin-top:6px;max-width:200px}.tag{font-family:var(--mono);font-size:9px;border:1px solid #bdc9c0;padding:4px 6px;display:inline-block;color:#40564d;white-space:nowrap;border-radius:3px}.model-label{display:block;overflow-wrap:anywhere;max-width:180px;font-size:12px;font-weight:500}.supplement-label{display:block;font-size:10px;color:#426453;max-width:200px;margin-top:5px}.status{font-size:10px;display:inline-block;white-space:nowrap;padding:4px 8px;border-radius:3px;background:#e9ebe5;color:#4d5f54}.status.not-graded,.status.failed{background:#f4e6dd;color:#863b2b}.status.passed{background:#dcebe2;color:#226645}.numeric{font-family:var(--mono);font-variant-numeric:tabular-nums}.inspect-button{font-size:11px;border:1px solid #b9c6bf;color:#385447;background:transparent;white-space:nowrap;padding:7px 10px;border-radius:3px}.inspect-button[aria-expanded=true]{background:#dce6dc}.inspect-button span{margin-left:5px}.detail-row>td{padding:24px;background:#e8ece6;border-top:1px solid var(--line);box-shadow:inset 0 1px 7px #1833260a}.detail-grid{display:grid;grid-template-columns:1fr 1fr;gap:20px}.panel{border:1px solid #cdd6cd;border-radius:4px;background:var(--paper);padding:23px;margin:0 0 18px;min-width:0}.panel h3{font-family:Georgia,serif;font-size:23px;font-weight:400;letter-spacing:-.025em;margin:0 0 18px}.panel-heading{display:flex;justify-content:space-between;align-items:start;gap:20px}.ledger{font-size:11px;margin:0}.ledger>div{display:grid;grid-template-columns:155px 1fr;gap:15px;border-bottom:1px solid #e4e8df;padding:8px 0}.ledger dt{color:var(--muted)}.ledger dd{margin:0;font-family:var(--mono);font-size:10px;overflow-wrap:anywhere}.error-message,.error-list{font-family:var(--mono);font-size:10px;color:#863b2b;overflow-wrap:anywhere}.error-message{border-left:2px solid #bd704c;background:#faf0e8;padding:12px;margin-bottom:0}.error-list{padding-left:20px}.error-list li{margin-bottom:8px}.mini-table{font-size:11px;min-width:520px}.mini-table caption{text-align:left;font-family:var(--mono);font-size:9px;color:var(--muted);padding-bottom:12px}.mini-table th,.mini-table td{padding:10px 12px;border-bottom:1px solid #e1e6dc;overflow-wrap:anywhere;vertical-align:top}.mini-table td:last-child{max-width:700px}.empty{padding:22px 0;color:var(--muted);font-size:13px}.panel .empty{padding:0;line-height:1.7}svg{width:100%;height:auto;display:block;overflow:visible}svg text{font:10px var(--mono);fill:#51665b}.axis{stroke:#c6d0c4;fill:none;stroke-width:1}.memory-peak{fill:#a25739;stroke:var(--paper);stroke-width:2}.memory-series,.prediction-line{stroke:#237c72;stroke-width:2;fill:none}.sample-dot{fill:#237c72}.confidence-band{fill:#237c7225;stroke:none}.graph-scroll{overflow-x:auto;max-width:100%;padding:0 2px}.execution-dag{min-width:660px;max-height:1900px}.dag-edge{fill:none;stroke:#82978c;stroke-width:1.3;color:#82978c}.node-failed{fill:#f5e7df;stroke:#d4a78e}.node-neutral{fill:#eaf0e8;stroke:#b8cbbb}.node-label{font-size:9px;fill:#355047}.node-hash{font-size:8px;fill:#697b70}.audit-panel .ledger{margin-top:22px}.supplemental{border-left:3px solid #719180}.evidence-notes{display:grid;grid-template-columns:1fr 1.2fr;gap:12%;padding:75px 0 65px;border-bottom:1px solid var(--ink);margin-top:35px}.evidence-notes h2{font-size:40px}.scope-copy p{font-size:12px;color:var(--muted);margin:0 0 18px}.scope-copy p:last-child{margin-bottom:0}footer{display:grid;grid-template-columns:1fr 1.3fr;gap:60px;padding:32px 0 44px;font-size:10px;color:var(--muted)}.brand-footer{font-weight:600;letter-spacing:.13em;font-size:12px;color:var(--ink)}footer dl{margin:0;min-width:0}footer dt{font-size:9px;text-transform:uppercase;letter-spacing:.08em}footer dd{font:9px/1.7 var(--mono);margin:5px 0 13px;overflow-wrap:anywhere}.sr-only{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap;border:0}@media(min-width:1500px){main{padding:0 70px}.masthead{padding-left:max(5vw,calc((100vw - 1460px)/2));padding-right:max(5vw,calc((100vw - 1460px)/2))}}@media(max-width:1000px){.edition{display:none}.hero{gap:7%;padding-top:50px}.hero-note p{font-size:18px}.metric{padding:20px 17px}.metric strong{font-size:44px}.metric .eyebrow{font-size:9px}.filters label{flex:1;min-width:125px}.filters .search-field{flex:2;min-width:220px}.detail-grid{grid-template-columns:1fr}.evidence-notes{gap:8%}}@media(max-width:600px){body{font-size:14px}.masthead{padding:15px 5vw}.brand{font-size:11px}.brand-mark{font-size:22px;margin-right:10px}.offline{font-size:8px}.hero{grid-template-columns:1fr;gap:20px;padding:37px 0 30px}h1{font-size:58px}.lede{font-size:15px;max-width:320px;margin-top:20px}.hero-note{margin:0;max-width:100%;padding-top:15px}.hero-note p{font-size:17px;margin:13px 0}.issue{font-size:9px}.metrics{grid-template-columns:1fr 1fr}.metric{border-bottom:1px solid #405059;padding:19px 15px}.metric strong{font-size:41px}.metric p:last-child{font-size:10px}.metric .eyebrow{font-size:9px;margin-bottom:7px;min-height:25px}.summary-strip{gap:8px;padding:15px 0}.summary-strip p{font-size:10px;width:100%}.run-ledger{padding-top:40px}.section-heading{display:block;margin-bottom:22px}h2{font-size:35px}.section-heading>p{margin-top:15px;font-size:11px}.filters{padding:14px;gap:11px}.filters label{min-width:calc(50% - 10px)}.filters .search-field{flex-basis:100%;min-width:100%}.filters input,.filters select{width:100%;min-width:0;padding:8px;font-size:11px}.filters button{width:100%}.detail-row>td{padding:14px}.panel{padding:18px}.ledger>div{grid-template-columns:130px 1fr;gap:8px}.evidence-notes{grid-template-columns:1fr;gap:25px;padding:44px 0 35px;margin-top:20px}.evidence-notes h2{font-size:34px}.scope-copy p{font-size:11px}footer{grid-template-columns:1fr;gap:20px;padding-bottom:30px}.eyebrow{font-size:9px}}@media(prefers-reduced-motion:reduce){html{scroll-behavior:auto}*,*::before,*::after{animation:none!important;transition:none!important}}@media print{body{background:white}.masthead,.filters,.inspect-button,.summary-strip,.skip-link{display:none}.hero{padding-top:20px}.metrics{break-inside:avoid}.table-scroll{overflow:visible}#runs-table{min-width:0;font-size:9px}.run-row th,.run-row td{padding:10px 5px}.run-row th{min-width:0}.evidence-notes{padding:30px 0}main{padding:0}.panel{break-inside:avoid}.detail-grid{grid-template-columns:1fr}a{text-decoration:none}}
'''

_JS = r'''
"use strict";
(() => {
  const table = document.getElementById("runs-table");
  const groups = Array.from(table.querySelectorAll(".run-group"));
  const search = document.getElementById("search");
  const track = document.getElementById("track");
  const condition = document.getElementById("condition");
  const outcome = document.getElementById("outcome");
  const sort = document.getElementById("sort");
  function refresh() {
    const query = search.value.trim().toLocaleLowerCase();
    let count = 0;
    const ordered = groups.slice();
    if (sort.value === "task" || sort.value === "model") {
      const key = sort.value;
      ordered.sort((a, b) => a.dataset[key].localeCompare(b.dataset[key]));
    } else if (sort.value.startsWith("runtime")) {
      const direction = sort.value === "runtime-asc" ? 1 : -1;
      ordered.sort((a, b) => {
        if (a.dataset.elapsed === "") return b.dataset.elapsed === "" ? 0 : 1;
        if (b.dataset.elapsed === "") return -1;
        return direction * (Number(a.dataset.elapsed) - Number(b.dataset.elapsed));
      });
    }
    for (const group of ordered) {
      const data = group.dataset;
      const haystack = [data.task, data.track, data.condition, data.model, group.querySelector(".supplement-label")?.textContent || ""].join(" ").toLocaleLowerCase();
      group.hidden = Boolean((query && !haystack.includes(query)) || (track.value && data.track !== track.value) || (condition.value && data.condition !== condition.value) || (outcome.value && data.state !== outcome.value));
      if (!group.hidden) count++;
      table.appendChild(group);
    }
    document.getElementById("result-count").textContent = `${count} of ${groups.length} runs shown`;
    document.getElementById("no-results").hidden = count !== 0;
  }
  [search, track, condition, outcome, sort].forEach(control => control.addEventListener(control === search ? "input" : "change", refresh));
  document.getElementById("reset").addEventListener("click", () => {
    search.value = track.value = condition.value = outcome.value = "";
    sort.value = "recorded";
    refresh();
    search.focus();
  });
  document.querySelectorAll("[data-open]").forEach(control => control.addEventListener("click", event => {
    event.preventDefault();
    const detail = document.getElementById(control.dataset.open);
    const button = detail.closest(".run-group").querySelector("button[data-open]");
    const open = control.tagName === "A" || detail.hidden;
    detail.hidden = !open;
    button.setAttribute("aria-expanded", String(open));
    if (control.tagName === "A") detail.scrollIntoView({block: "nearest"});
  }));
})();
'''


if __name__ == "__main__":
    raise SystemExit(main())
