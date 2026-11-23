"""Inspect receipt-bound Tier B figures without releasing source values."""

from __future__ import annotations

import csv
import math
import struct
from io import BytesIO, StringIO
from pathlib import Path
from typing import TYPE_CHECKING, Any

from devbench.analysis.scientific_plot_verification import diagnostic_plot_state
from devbench.core._hashing import decode_receipt_json
from devbench.engine.workflow_operations import OperationConflict

if TYPE_CHECKING:
    from devbench.engine.exploratory_oci import ExploratoryOCI
    from devbench.platform.adaptive_workflow import AdaptiveWorkflow

_PNG = b"\x89PNG\r\n\x1a\n"
_MAX_IMAGE_BYTES = 8 * 1024**2
_MAX_SIDECAR_BYTES = 1_000_000
_MAX_ROWS = 4096
_MAX_COLUMNS = 32


def _read(vault, sha256: str, limit: int) -> bytes:
    with vault.open(sha256) as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise ValueError("Plot evidence exceeds its bounded byte budget")
    return raw


def _png_shape(raw: bytes) -> dict[str, int]:
    from PIL import Image

    if len(raw) < 33 or raw[:8] != _PNG or raw[12:16] != b"IHDR":
        raise ValueError("Invalid plot PNG header")
    width, height = struct.unpack_from(">II", raw, 16)
    if not width or not height or width * height > 4_000_000:
        raise ValueError("Plot PNG exceeds the pixel budget")
    with Image.open(BytesIO(raw), formats=("PNG",)) as image:
        if image.size != (width, height) or getattr(image, "n_frames", 1) != 1:
            raise ValueError("Unexpected plot PNG dimensions or animation")
        image.verify()
    with Image.open(BytesIO(raw), formats=("PNG",)) as image:
        image.load()
    return {"width": width, "height": height}


def _table_shape(columns, rows, *, csv_values: bool) -> dict[str, int]:
    if (
        type(columns) is not list
        or not 1 <= len(columns) <= _MAX_COLUMNS
        or not all(
            type(name) is str and 0 < len(name.strip()) <= 128 for name in columns
        )
        or len(columns) != len(set(columns))
    ):
        raise ValueError("Invalid numeric sidecar column declarations")
    count = 0
    for count, row in enumerate(rows, 1):
        if count > _MAX_ROWS or type(row) is not list or len(row) != len(columns):
            raise ValueError("Numeric sidecar exceeds its shape bound")
        if not csv_values and any(type(value) not in {int, float} for value in row):
            raise ValueError("Numeric sidecar requires numbers")
        if any(not math.isfinite(float(value)) for value in row):
            raise ValueError("Numeric sidecar requires finite values")
    if not count:
        raise ValueError("Numeric sidecar has no observations")
    return {"row_count": count, "column_count": len(columns)}


def _numeric_shape(raw: bytes, suffix: str, figure_sha256: str) -> dict[str, int]:
    if suffix == ".csv":
        reader = csv.reader(StringIO(raw.decode("utf-8")), strict=True)
        return _table_shape(next(reader, []), reader, csv_values=True)
    if suffix == ".json":
        value = decode_receipt_json(raw)
        if (
            type(value) is not dict
            or set(value) != {"format", "figure_sha256", "columns", "rows"}
            or value["format"] != "devbench.exploratory-plot-data/1"
            or value["figure_sha256"] != figure_sha256
            or type(value["rows"]) is not list
        ):
            raise ValueError("Numeric JSON does not bind the exact figure")
        return _table_shape(value["columns"], value["rows"], csv_values=False)
    raise ValueError("Use CSV or receipt-bound numeric JSON sidecars")


def _figure_check(executor, artifact, spec):
    reference = {"sha256": artifact.sha256, "version_id": artifact.version_id}
    if Path(spec["path"]).suffix != ".png" or spec["artifact_type"] != "file":
        return {**reference, "status": "unsupported_plot_verification"}
    try:
        dimensions = _png_shape(
            _read(executor.training, artifact.sha256, _MAX_IMAGE_BYTES)
        )
    except (ImportError, OSError, SyntaxError, ValueError):
        return {**reference, "status": "invalid_or_unavailable"}
    return {**reference, "status": "verified_png_decode", **dimensions}


def _numeric_check(executor, artifact, spec, figure_sha256):
    reference = {"sha256": artifact.sha256, "version_id": artifact.version_id}
    if artifact.kind != "table" or artifact.representation != "table":
        return {**reference, "status": "invalid"}
    try:
        raw = _read(executor.training, artifact.sha256, _MAX_SIDECAR_BYTES)
        bounds = _numeric_shape(raw, Path(spec["path"]).suffix, figure_sha256)
    except (OSError, ValueError, OverflowError, csv.Error):
        return {**reference, "status": "invalid"}
    return {**reference, "status": "verified", **bounds}


def _projection(figure, numeric, *, receipt_sha256=None):
    render_verified = figure["status"] == "verified_png_decode"
    return {
        "format": "devbench.exploratory-plot-inspection/1",
        "claimed_scope": "exploratory_only",
        "receipt_sha256": receipt_sha256,
        "figure": figure,
        "numeric_sidecar": numeric,
        "diagnostic_plot_state": diagnostic_plot_state(
            rendered=render_verified,
            render_verified=render_verified,
            image_accessible=False,
            scientific_assessment=(
                "pending"
                if render_verified and numeric["status"] == "verified"
                else "unavailable"
            ),
        ),
    }


def inspect_figure(
    executor: ExploratoryOCI,
    workflow: AdaptiveWorkflow,
    *,
    step_id: str,
    figure_port: str,
    numeric_port: str | None,
) -> dict[str, Any]:
    """Check PNG decoding and sidecar shape for one current, unprotected receipt.

    Both declared ports must originate from one adaptive receipt. Numeric
    values, pixels, paths and a recipient's evidence rights are never returned.
    """
    if executor.ledger.path != workflow.ledger.path:
        raise PermissionError("Plot inspection requires the bound workflow")
    with workflow._read_connection() as db:
        state = workflow._state(db)
        outcome = state.outcome(step_id)
        outputs = (
            outcome["outputs"] if outcome and outcome["status"] == "succeeded" else {}
        )
        if figure_port not in outputs:
            return _projection({"status": "absent"}, {"status": "absent"})
        figure = workflow._current_use(db, step_id, figure_port, "compute", None)
        if figure.kind != "figure":
            raise ValueError("Plot evidence requires a figure port")
        retained = executor._retained_receipt(state, step_id)
        if retained is None:
            raise OperationConflict("Figure receipt is unavailable")
        envelope, _ = retained
        method = executor.methods.method(envelope["step"]["oci_method_version_id"])
        specs = method["contract"]["outputs"]
        figure_proof = _figure_check(executor, figure, specs[figure_port])
        numeric = {"status": "absent"}
        if numeric_port is not None and numeric_port in outputs:
            artifact = workflow._current_use(db, step_id, numeric_port, "compute", None)
            numeric = _numeric_check(
                executor, artifact, specs[numeric_port], figure.sha256
            )
        return _projection(
            figure_proof, numeric, receipt_sha256=outcome["receipt_sha256"]
        )
