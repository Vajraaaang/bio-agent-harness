"""Typed current-revision exports of sealed Workbench thesis packages."""

from __future__ import annotations

import hashlib
import io
import re
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass
from heapq import nsmallest
from operator import itemgetter
from pathlib import Path
from typing import Any

from devbench.analysis.checkpoints import atomic_json
from devbench.analysis.thesis_synthesis import THESIS_RECEIPT, verify_thesis_package
from devbench.engine.workflow_operations import OperationBlocked
from devbench.study.evidence import _read, decode_json

from .workbench_adaptive import _graph_revision
from .workbench_thesis import _checked_thesis_lineage, _workspace_path

_ID = re.compile(r"[a-f0-9]{32}")
_SHA = re.compile(r"[a-f0-9]{64}")
_RECEIPT_SIZE = 100_000
_MAX_EXPORT_BYTES = 16 * 1024**2
_MAX_LISTED_PACKAGES = 64


@dataclass(frozen=True)
class ThesisArchive:
    content: bytes


def _index(app, session_id: str) -> Path:
    if _ID.fullmatch(session_id) is None:
        raise ValueError("Select a valid thesis session")
    return _workspace_path(
        app.workspace / "thesis-package-index" / session_id, (app.workspace,)
    )


def record_thesis_export(
    app,
    session_id: str,
    package_id: str,
    package: dict[str, Any],
    revision: int,
) -> None:
    if _ID.fullmatch(package_id) is None or not _SHA.fullmatch(package["root_sha256"]):
        raise ValueError("A thesis export requires an exact sealed package")
    directory = _index(app, session_id)
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    target = _workspace_path(directory / (package_id + ".json"), (app.workspace,))
    if target.exists():
        raise FileExistsError(target)
    atomic_json(
        target,
        {
            "root_sha256": package["root_sha256"],
            "revision": revision,
            "outcome": package["outcome"],
        },
    )


def _record(app, path: Path) -> dict[str, Any]:
    return decode_json(_read(_workspace_path(path, (app.workspace,)), _RECEIPT_SIZE))


def _route(session_id: str, package_id: str) -> str:
    return f"/api/scientific/thesis/{session_id}/package/{package_id}"


def thesis_export_inventory(
    app,
    session_id: str,
    revision: int,
    *,
    allow_export: bool,
) -> list[dict[str, Any]]:
    directory = _index(app, session_id)
    if not directory.is_dir():
        return []

    def ranked_records():
        for path in directory.glob("*.json"):
            package_id = path.stem
            if not _ID.fullmatch(package_id):
                continue
            record = _record(app, path)
            package_revision = record["revision"]
            rank = (
                package_revision != revision,
                -package_revision,
                -path.stat().st_mtime_ns,
                package_id,
            )
            yield rank, record

    results = []
    for (stale, _, _, package_id), record in nsmallest(
        _MAX_LISTED_PACKAGES, ranked_records(), key=itemgetter(0)
    ):
        exportable = not stale and allow_export
        results.append(
            {
                "package_id": package_id,
                "root_sha256": record["root_sha256"],
                "outcome": record["outcome"],
                "status": "stale" if stale else "current",
                "download": _route(session_id, package_id) if exportable else None,
            }
        )
    return results


def _package_members(
    root: Path,
    expected_root: str,
) -> Iterator[tuple[str, Path, str]]:
    checked = verify_thesis_package(root, expected_root)
    if checked["valid"] is not True or checked["evidence_verified"] is not True:
        raise OperationBlocked("thesis_package_integrity_unverified")
    raw = _read(root / THESIS_RECEIPT, _RECEIPT_SIZE)
    if hashlib.sha256(raw).hexdigest() != expected_root:
        raise OperationBlocked("thesis_package_changed_during_export")
    receipt = decode_json(raw)
    entries = {**receipt["files"], THESIS_RECEIPT: expected_root}
    for name, expected in entries.items():
        path = _workspace_path(root / name, (root,))
        yield name, path, expected


def _archive(root: Path, expected: str) -> bytes:
    entries = tuple(_package_members(root, expected))
    if sum(path.stat().st_size for _, path, _ in entries) > _MAX_EXPORT_BYTES:
        raise OperationBlocked("thesis_package_exceeds_browser_export_budget")
    target = io.BytesIO()
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        total = 0
        for name, path, checksum in entries:
            raw = _read(path, _MAX_EXPORT_BYTES - total)
            total += len(raw)
            if hashlib.sha256(raw).hexdigest() != checksum:
                raise OperationBlocked("thesis_package_changed_during_export")
            archive.writestr(name, raw)
    value = target.getvalue()
    if len(value) > _MAX_EXPORT_BYTES:
        raise OperationBlocked("thesis_package_exceeds_browser_export_budget")
    return value


def export_thesis_package(app, session_id: str, package_id: str) -> ThesisArchive:
    """Recheck scoped permission, evidence revision and sealed package integrity."""
    from .workbench_thesis_actions import _capabilities

    if _ID.fullmatch(package_id) is None:
        raise ValueError("Select an exact thesis package")
    with app._lock:
        flow, lifecycle, binding = _checked_thesis_lineage(app, session_id)
        directory = _index(app, session_id)
        record = _record(app, directory / (package_id + ".json"))

        def current(db):
            if _graph_revision(flow, connection=db) != record["revision"]:
                raise OperationBlocked("thesis_package_stale")
            _, _, actions = _capabilities(binding, lifecycle, flow, db)
            if not actions["synthesize"]["enabled"]:
                raise PermissionError(actions["synthesize"]["reason"])

        flow._read_projection(current)
        path = _workspace_path(
            app.workspace / "thesis-packages" / session_id / package_id,
            (app.workspace,),
        )
        content = _archive(path, record["root_sha256"])
        delivery = binding.delivery
        broker = delivery.broker
        receipt = broker.reserve(
            delivery.context_id,
            delivery.source_id,
            {
                "format": "devbench.thesis-export-delivery/1",
                "root_sha256": record["root_sha256"],
                "content_sha256": hashlib.sha256(content).hexdigest(),
                "maximum_bytes": len(content),
            },
            _guard=current,
        )
        broker.mark_attempted(receipt["reservation_id"], _guard=current)
        return ThesisArchive(content)
