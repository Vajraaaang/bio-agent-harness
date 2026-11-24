"""Custody and offline projection of a retained local Studio comparison."""

from __future__ import annotations

import hashlib
import math
import os
import re
from pathlib import Path
from tempfile import TemporaryDirectory

from devbench.analysis.checkpoints import atomic_json
from devbench.core._files import read_bounded_json
from devbench.core._hashing import decode_receipt_json
from devbench.engine.workspace import read_bounded_file

from .plugin_jobs import JobStore
from .plugin_mcp import studio_job_readback
from .workbench_interactive import ensure_adaptive_binding
from .workbench_ui_projection import _public_investigation, _publication_hashes

_FORMAT = "devbench.retained-comparison/1"
_MANIFEST_BYTES = 64_000
_FIGURE_BYTES = 4_000_000
_ID = re.compile(r"[a-f0-9]{32}")
_SHA = re.compile(r"[a-f0-9]{64}")
_PNG = b"\x89PNG\r\n\x1a\n"
_PNG_FILENAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9 ._-]{0,119}\.png")
_PROGRAMS = ("Branchio-visceromotor-like", "Somatic-motor-like")
_FIELDS = {
    "format",
    "session_id",
    "plan_id",
    "plan_sha256",
    "investigation_id",
    "result_ref",
    "title",
    "states",
    "figures",
    "limitations",
}


def _matching(value, pattern):
    return isinstance(value, str) and pattern.fullmatch(value) is not None


def _text(value, limit=240):
    return isinstance(value, str) and bool(value.strip()) and len(value) <= limit


def _items(value, minimum, maximum):
    return isinstance(value, list) and minimum <= len(value) <= maximum


def _count(value):
    return type(value) is int and 0 <= value <= 2**53 - 1


def _manifest(path, *, expected_sha256=None):
    raw = read_bounded_file(path, _MANIFEST_BYTES)
    if (
        expected_sha256 is not None
        and hashlib.sha256(raw).hexdigest() != expected_sha256
    ):
        raise ValueError("Retained comparison manifest digest differs")
    value = decode_receipt_json(raw)
    if (
        not isinstance(value, dict)
        or value.keys() != _FIELDS
        or value["format"] != _FORMAT
        or not all(
            _matching(value[key], _ID)
            for key in ("session_id", "plan_id", "investigation_id")
        )
        or not all(_matching(value[key], _SHA) for key in ("plan_sha256", "result_ref"))
        or not _text(value["title"])
        or not _items(value["states"], 1, 24)
        or not _items(value["figures"], 1, 4)
        or not _items(value["limitations"], 0, 12)
        or not all(_text(note, 800) for note in value["limitations"])
    ):
        raise ValueError("Invalid retained comparison manifest")
    for state in value["states"]:
        if (
            not isinstance(state, dict)
            or state.keys() != {"label", "counts"}
            or not _text(state["label"])
            or not _items(state["counts"], 2, 8)
            or not all(_count(count) for count in state["counts"])
        ):
            raise ValueError("Invalid retained comparison state counts")
    for figure in value["figures"]:
        if (
            not isinstance(figure, dict)
            or figure.keys() != {"title", "filename", "sha256"}
            or not _text(figure["title"])
            or not _matching(figure["filename"], _PNG_FILENAME)
            or not _matching(figure["sha256"], _SHA)
        ):
            raise ValueError("Invalid retained comparison figure")
    if len({state["label"] for state in value["states"]}) != len(value["states"]):
        raise ValueError("Retained comparison states require distinct labels")
    if len({figure["sha256"] for figure in value["figures"]}) != len(value["figures"]):
        raise ValueError("Retained comparison figures require distinct content")
    return value


def _session_path(app, session_id):
    if not _matching(session_id, _ID):
        raise ValueError("Invalid retained comparison session identity")
    return app._path("retained-comparisons", session_id)


def _fingerprints(profiles):
    result = [(row["sha256"], row["size_bytes"]) for row in profiles]
    if not 2 <= len(result) <= 8 or any(
        not _matching(sha, _SHA) or not _count(size) or not size for sha, size in result
    ):
        raise PermissionError("Retained comparison requires exact source fingerprints")
    return result


def _verified_investigation(app, manifest):
    sid = manifest["session_id"]
    binding = ensure_adaptive_binding(app, sid)
    if binding is None or binding.delivery.context_id != "plugin-scientist-" + sid:
        raise PermissionError(
            "Retained comparison requires the current Plugin session owner"
        )
    planner, _ = binding.require_owner(app.scientific)
    with studio_job_readback(app.workspace) as db:
        if db is None:
            raise PermissionError("Retained comparison requires a private Plugin plan")
        row = db.execute(
            "SELECT * FROM plugin_plans WHERE session_id=? AND plan_id=?",
            (sid, manifest["plan_id"]),
        ).fetchone()
        plan = JobStore._plan(row, manifest["plan_id"], manifest["plan_sha256"])
    if (
        plan.get("data_classification") != "private"
        or planner.get("data_classification") != "private"
    ):
        raise PermissionError("Retained comparison requires a private Plugin plan")
    investigation = _public_investigation(
        app, app.investigations.get(manifest["investigation_id"])
    )
    if (
        investigation.get("publication_status") == "withheld"
        or investigation.get("id") != manifest["investigation_id"]
        or investigation.get("status") != "completed"
        or not investigation.get("request", {}).get("motor_program_comparison")
        or (investigation.get("description_operation") or {}).get("result_ref")
        != manifest["result_ref"]
        or _fingerprints(investigation.get("source_profiles", []))
        != _fingerprints(plan.get("profiles", []))
    ):
        raise PermissionError(
            "Retained comparison does not match the completed investigation"
        )
    identities = {
        manifest["result_ref"],
        *(item["sha256"] for item in manifest["figures"]),
    }
    if identities & _publication_hashes(app):
        raise PermissionError("Retained comparison publication is withheld")
    answer = next(
        (
            turn["content"]
            for turn in reversed(investigation.get("turns", []))
            if turn.get("kind") == "dataset_answer"
            and turn.get("content", {}).get("binding", {}).get("result_ref")
            == manifest["result_ref"]
        ),
        None,
    )
    if answer is None:
        raise PermissionError(
            "Retained comparison lacks the exact investigation result"
        )
    return answer


def _stage_rows(tables):
    denominators = tables.get("motor_program_group_denominators", [])
    stages, days = [], []
    for day, dataset, cells, stage in denominators:
        if (
            not _text(day)
            or not _matching(dataset, re.compile(r"[^/\\]+\.h5ad", re.IGNORECASE))
            or not _count(cells)
            or not cells
            or not _matching(stage, re.compile(r"e[0-9]{1,2}(?:\.[0-9]+)?"))
        ):
            raise ValueError("Invalid verified comparison denominators")
        whole, dot, partial = stage[1:].partition(".")
        label = f"E{int(whole):02d}{dot}{partial}"
        days.append(day)
        stages.append({"label": label, "dataset": dataset, "cells": cells})
    if not 2 <= len(stages) <= 8 or len(set(days)) != len(days):
        raise ValueError("Verified comparison requires distinct stages")
    return stages, days


def _counts_table(caption, stages, field, groups, denominators):
    rows = []
    for label, values in groups:
        cells = [
            f"{value:,} ({100 * value / total:.1f}%)" if total else "0 (—)"
            for value, total in zip(values, denominators, strict=True)
        ]
        rows.append([label, *cells])
    return {
        "caption": caption,
        "columns": [field, *(stage["label"] for stage in stages)],
        "rows": rows,
    }


def _project(app, manifest):
    answer = _verified_investigation(app, manifest)
    tables = {table["source_ref"]: table["rows"] for table in answer["tables"]}
    stages, days = _stage_rows(tables)
    totals = [stage["cells"] for stage in stages]
    markers = {
        (day, program): count
        for day, program, count, *_ in tables.get(
            "motor_program_marker_positive_observations", []
        )
    }
    percentages = {
        row[0]: row[1:-1] for row in tables.get("motor_program_group_proportions", [])
    }
    programs = []
    for program in _PROGRAMS:
        counts = [markers.get((day, program), 0) for day in days]
        rates = percentages.get(program, [])
        if len(rates) != len(stages) or any(
            not _count(count)
            or count > total
            or not math.isclose(100 * count / total, rate, abs_tol=0.000051, rel_tol=0)
            for count, total, rate in zip(counts, totals, rates, strict=True)
        ):
            raise ValueError("Inconsistent verified motor-program counts")
        programs.append((program, counts))
    states = [(item["label"], item["counts"]) for item in manifest["states"]]
    if any(len(counts) != len(stages) for _, counts in states):
        raise ValueError("Retained state counts must follow the verified stage order")
    screened = [
        sum(counts) for counts in zip(*(counts for _, counts in states), strict=True)
    ]
    if any(count > total for count, total in zip(screened, totals, strict=True)):
        raise ValueError("Screened state counts exceed the original population")
    denominators = ", ".join(
        f"{stage['label']}: {count:,}"
        for stage, count in zip(stages, screened, strict=True)
    )
    figure_route = f"/api/scientific/adaptive/{manifest['session_id']}/comparison"
    return {
        "title": manifest["title"],
        "stages": stages,
        "tables": [
            _counts_table(
                "Motor-neuron candidate populations (% of all cells)",
                stages,
                "Population",
                programs,
                totals,
            ),
            _counts_table(
                "Candidate cell states by developmental stage (% of screened cells)",
                stages,
                "Cell state",
                states,
                screened,
            ),
        ],
        "figures": [
            {
                "title": item["title"],
                "download_path": f"{figure_route}/{item['sha256']}.png",
            }
            for item in manifest["figures"]
        ],
        "limitations": [
            f"Screened cell-state denominators: {denominators}.",
            *manifest["limitations"],
        ],
    }


def _figure(path, sha256):
    raw = read_bounded_file(path, _FIGURE_BYTES)
    if hashlib.sha256(raw).hexdigest() != sha256:
        raise ValueError("Retained comparison figure digest differs")
    if (
        len(raw) < 33
        or raw[:16] != _PNG + b"\0\0\0\x0dIHDR"
        or not 0 < int.from_bytes(raw[16:20], "big") <= 8192
        or not 0 < int.from_bytes(raw[20:24], "big") <= 8192
    ):
        raise ValueError("Retained comparison figure must be a bounded PNG")
    return raw


def _sync_directory(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def retain_local_comparison(app, manifest_path):
    """Retain a local bundle under the custody of its private session."""
    if app.offline_only is not True:
        raise PermissionError("Retained comparison requires offline Studio")
    manifest_path = Path(manifest_path)
    if not manifest_path.is_absolute() or manifest_path.resolve() != manifest_path:
        raise ValueError(
            "Comparison import requires an absolute canonical manifest path"
        )
    manifest = _manifest(manifest_path)
    view = _project(app, manifest)
    target = _session_path(app, manifest["session_id"])
    if target.exists():
        _, existing = _retained_manifest(app, manifest["session_id"])
        if existing != manifest:
            raise ValueError(
                "A different comparison is already retained for this session"
            )
        for figure in manifest["figures"]:
            sha = figure["sha256"]
            _figure(target / (sha + ".png"), sha)
        return view
    target.parent.mkdir(mode=0o700, exist_ok=True)
    with TemporaryDirectory(prefix=".pending-", dir=target.parent) as pending:
        staging = Path(pending)
        for figure in manifest["figures"]:
            sha = figure["sha256"]
            raw = _figure(manifest_path.parent / figure["filename"], sha)
            destination = staging / (sha + ".png")
            with os.fdopen(
                os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb"
            ) as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
        stored = staging / "manifest.json"
        atomic_json(stored, manifest)
        sha256 = hashlib.sha256(read_bounded_file(stored, _MANIFEST_BYTES)).hexdigest()
        atomic_json(staging / "manifest-sha256.json", sha256)
        _sync_directory(staging)
        staging.rename(target)
        _sync_directory(target.parent)
    return view


def _retained_manifest(app, session_id):
    directory = _session_path(app, session_id)
    sha256 = read_bounded_json(directory / "manifest-sha256.json", 128)
    if not _matching(sha256, _SHA):
        raise ValueError("Invalid retained comparison manifest digest")
    manifest = _manifest(directory / "manifest.json", expected_sha256=sha256)
    if manifest["session_id"] != session_id:
        raise PermissionError("Retained comparison belongs to a different session")
    return directory, manifest


def local_comparison_view(app, session_id):
    """Read verified metadata without releasing figure bytes through JSON."""
    if app.offline_only is not True:
        return None
    try:
        _, manifest = _retained_manifest(app, session_id)
    except FileNotFoundError:
        return None
    return _project(app, manifest)


def local_comparison_figure(app, session_id, sha256):
    """Revalidate the bound result before reading a figure's verified bytes."""
    if app.offline_only is not True:
        raise PermissionError("Retained comparison requires offline Studio")
    if not _matching(sha256, _SHA):
        raise KeyError("Unknown retained comparison figure")
    try:
        directory, manifest = _retained_manifest(app, session_id)
    except FileNotFoundError:
        raise KeyError("Unknown retained comparison") from None
    _verified_investigation(app, manifest)
    if not any(figure["sha256"] == sha256 for figure in manifest["figures"]):
        raise KeyError("Unknown retained comparison figure")
    return _figure(directory / (sha256 + ".png"), sha256)
