"""Verify the bounded aggregate data and labels accompanying a produced figure."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Never

from devbench.core._files import read_bounded_file
from devbench.core._hashing import decode_receipt_json
from devbench.engine.workflow_operations import OperationBlocked

from ._sources import clinical

_LIMIT = 4 * 1024**2


def _reject() -> Never:
    raise OperationBlocked("scientific_refusal")


def _metadata(meta):
    axes = meta.get("axis_labels")
    if (
        not isinstance(axes, dict)
        or not 1 <= len(axes) <= 4
        or not all(
            isinstance(label, str) and 0 < len(label) <= 200 and not clinical(label)
            for label in axes.values()
        )
    ):
        _reject()
    transforms = meta.get("transformations")
    if (
        not isinstance(transforms, list)
        or len(transforms) > 16
        or not all(isinstance(t, str) and 0 < len(t) <= 200 for t in transforms)
    ):
        _reject()
    for key in ("sample_count", "donor_count"):
        count = meta.get(key)
        if type(count) is not int or count < 0:
            _reject()
    if meta["donor_count"] > meta["sample_count"]:
        _reject()


def _rows(columns, rows):
    if (
        not isinstance(columns, list)
        or not 1 <= len(columns) <= 32
        or not all(
            isinstance(c, str) and 0 < len(c) <= 128 and not clinical(c)
            for c in columns
        )
    ):
        _reject()
    if len(set(columns)) != len(columns):
        _reject()
    # Long-form summaries have named quantities. A sample-by-feature array
    # does not become a plot summary by changing its declared artifact kind.
    if any(
        c.casefold() in {"cell_id", "barcode", "raw_counts", "feature_id", "ensembl_id"}
        for c in columns
    ):
        _reject()
    quantities = {
        "value",
        "mean",
        "median",
        "count",
        "fraction",
        "percent",
        "percentage",
        "effect",
        "estimate",
        "score",
        "frequency",
        "x",
        "y",
        "log2fc",
        "p_value",
        "q_value",
        "lower",
        "upper",
        "n",
        "total",
    }
    measures = [i for i, c in enumerate(columns) if c.casefold() in quantities]
    if not measures:
        _reject()
    count = 0
    for row in rows:
        count += 1
        if (
            count > 4096
            or not isinstance(row, (tuple, list))
            or len(row) != len(columns)
        ):
            _reject()
        if not all(
            type(v) in (int, float) and math.isfinite(v)
            for i in measures
            if (v := row[i]) is not None
        ):
            _reject()
        if not all(v is None or isinstance(v, (str, int, float, bool)) for v in row):
            _reject()
    if not count:
        _reject()


def verify_plot_sidecar(path: Path, fmt: str):
    if fmt == "json":
        data = decode_receipt_json(read_bounded_file(path, _LIMIT))
        if type(data) is not dict:
            _reject()
        _metadata(data)
        _rows(data.get("columns"), data.get("rows") or [])
        return
    if fmt != "parquet" or path.stat().st_size > _LIMIT:
        _reject()
    import pyarrow.parquet as pq

    file = pq.ParquetFile(path)
    meta = (file.schema_arrow.metadata or {}).get(b"devbench.plot_sidecar")
    if not meta:
        _reject()
    _metadata(json.loads(meta))
    _rows(
        file.schema_arrow.names,
        (
            row
            for batch in file.iter_batches(batch_size=256)
            for row in zip(*(col.to_pylist() for col in batch.columns), strict=True)
        ),
    )
