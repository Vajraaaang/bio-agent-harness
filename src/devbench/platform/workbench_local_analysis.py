"""Retain local analysis results alongside a completed private motor UMAP."""

from __future__ import annotations

import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from devbench.analysis.checkpoints import atomic_json
from devbench.core._files import read_bounded_file
from devbench.core._hashing import decode_receipt_json

from .plugin_jobs import digest
from .plugin_motor_umap import _studio_receipt, _studio_record
from .workbench_ui_projection import _publication_hashes

_ID = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{32}$")]
_SHA = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
_Label = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=160)]
_Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1500)]
_Value = Annotated[str, StringConstraints(max_length=160)]
_Name = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,100}\.png$")]
_DOCUMENT_LIMIT = 96_000
_IMAGE_LIMIT = 4_000_000


class _Record(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class _Finding(_Record):
    title: _Label
    text: _Text


class _Table(_Record):
    caption: _Label
    columns: list[_Label] = Field(min_length=1, max_length=8)
    rows: list[Annotated[list[_Value], Field(min_length=1, max_length=8)]] = Field(
        min_length=1, max_length=20
    )


class _Figure(_Record):
    title: _Label
    filename: _Name
    sha256: _SHA


class _Analysis(_Record):
    format: Literal["devbench.local-analysis/1"]
    session_id: _ID
    source_receipt_sha256: _SHA
    input_sha256: list[_SHA] = Field(min_length=3, max_length=3)
    title: _Label
    summary: _Text
    findings: list[_Finding] = Field(min_length=1, max_length=10)
    tables: list[_Table] = Field(max_length=6)
    figures: list[_Figure] = Field(max_length=6)


class _Stored(_Record):
    manifest: _Analysis
    sha256: _SHA


def _check_source(app, report: _Analysis):
    record = _studio_record(app, report.session_id)
    if record is None or record[2] is None or record[2]["status"] != "completed":
        raise KeyError("Local analysis requires a completed shared motor UMAP")
    plan = record[0]
    receipt = _studio_receipt(app.workspace, *record)
    if (
        receipt["session_id"] != report.session_id
        or digest(receipt) != report.source_receipt_sha256
        or [item["sha256"] for item in plan["profiles"]] != report.input_sha256
    ):
        raise KeyError("Local analysis does not match the current Studio run")
    identities = {
        report.source_receipt_sha256,
        *report.input_sha256,
        *(figure.sha256 for figure in report.figures),
    }
    if identities & _publication_hashes(app):
        raise PermissionError("Local analysis is withheld")


def _image(path, checksum):
    data = read_bounded_file(path, _IMAGE_LIMIT)
    if not data.startswith(b"\x89PNG\r\n\x1a\n") or hashlib.sha256(data).hexdigest() != checksum:
        raise ValueError("Local analysis figure differs from the manifest")
    return data


def _stored(app, session_id):
    target = app._path("local-analyses", session_id)
    content = read_bounded_file(target / "analysis.json", _DOCUMENT_LIMIT)
    record = _Stored.model_validate(decode_receipt_json(content))
    report = record.manifest
    if report.session_id != session_id or digest(report.model_dump()) != record.sha256:
        raise ValueError("Local analysis manifest differs from its stored identity")
    _check_source(app, report)
    return target, report


def retain_local_analysis(app, path):
    """Admit an explicitly selected local package into the private session."""
    if app.offline_only is not True:
        raise PermissionError("Local analysis requires offline Studio")
    path = Path(path)
    if not path.is_absolute() or path.resolve(strict=True) != path:
        raise ValueError("Use a canonical absolute local analysis manifest path")
    report = _Analysis.model_validate(decode_receipt_json(read_bounded_file(path, _DOCUMENT_LIMIT)))
    if any(len(row) != len(table.columns) for table in report.tables for row in table.rows):
        raise ValueError("Local analysis table column counts differ")
    if len({item.sha256 for item in report.figures}) != len(report.figures):
        raise ValueError("Local analysis figures must have distinct hashes")
    _check_source(app, report)

    target = app._path("local-analyses", report.session_id)
    if target.exists():
        directory, existing = _stored(app, report.session_id)
        if existing != report:
            raise ValueError("This session already has a different local analysis")
        for figure in report.figures:
            _image(directory / (figure.sha256 + ".png"), figure.sha256)
        return
    target.parent.mkdir(mode=0o700, exist_ok=True)
    with TemporaryDirectory(dir=target.parent, prefix=".pending-") as temporary:
        staging = Path(temporary)
        for figure in report.figures:
            data = _image(path.parent / figure.filename, figure.sha256)
            destination = staging / (figure.sha256 + ".png")
            destination.write_bytes(data)
            destination.chmod(0o600)
        record = {"manifest": report.model_dump(), "sha256": digest(report.model_dump())}
        stored_manifest = staging / "analysis.json"
        atomic_json(stored_manifest, record)
        read_bounded_file(stored_manifest, _DOCUMENT_LIMIT)
        staging.rename(target)


def local_analysis_view(app, session_id):
    if app.offline_only is not True:
        return None
    try:
        _, report = _stored(app, session_id)
    except (FileNotFoundError, KeyError):
        return None
    return {
        **report.model_dump(include={"title", "summary", "findings", "tables"}),
        "figures": [figure.model_dump(include={"title", "sha256"}) for figure in report.figures],
    }


def local_analysis_figure(app, session_id, checksum):
    if app.offline_only is not True:
        raise PermissionError("Local analysis requires offline Studio")
    try:
        directory, report = _stored(app, session_id)
    except FileNotFoundError:
        raise KeyError("Unknown local analysis figure") from None
    if not any(item.sha256 == checksum for item in report.figures):
        raise KeyError("Unknown local analysis figure")
    return _image(directory / (checksum + ".png"), checksum)

