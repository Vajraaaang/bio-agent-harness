"""Bounded input identities and model diagnostics used by temporal networks."""

import hashlib
import io
import json
from pathlib import Path

import numpy as np
import pandas as pd

from devharness_core.units import BIOLOGICAL_UNIT_COLUMNS


def _bounded_int(value: int, name: str, lower: int, upper: int) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not lower <= value <= upper
    ):
        raise ValueError(f"{name} must be an integer in [{lower}, {upper}]")


def _file_sha256(path: str) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _bounded_metadata(path: str, text_columns: tuple[str, ...] = ()) -> pd.DataFrame:
    source = Path(path)
    limit = 20 * 1024 * 1024
    if source.stat().st_size > limit:
        raise ValueError("Metadata exceeds the 20 MB bounded inference limit")
    with source.open("rb") as handle:
        content = handle.read(limit + 1)
    if len(content) > limit:
        raise ValueError("Metadata exceeds the 20 MB bounded inference limit")
    metadata = pd.read_csv(
        io.BytesIO(content),
        dtype={
            name: str for name in ("sample_id", *BIOLOGICAL_UNIT_COLUMNS, *text_columns)
        },
        keep_default_na=False,
    )
    if (
        "sample_id" not in metadata
        or metadata["sample_id"].duplicated().any()
        or (metadata["sample_id"] == "").any()
    ):
        raise ValueError("Metadata requires distinct nonempty sample_id values")
    metadata.attrs["source_sha256"] = hashlib.sha256(content).hexdigest()
    return metadata


def _matrix_sha256(values: np.ndarray, genes: list[str], samples: list[str]) -> str:
    digest = hashlib.sha256(
        json.dumps(
            {"genes": genes, "samples": samples},
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode()
    )
    digest.update(np.asarray(values, dtype="<f8", order="C").tobytes())
    return digest.hexdigest()


def _diagnostics(design: np.ndarray, columns: list[str]) -> dict:
    from scipy.linalg import qr, svdvals

    if design.size == 0 or not np.isfinite(design).all():
        raise ValueError("Design must be nonempty and finite")
    _, r, pivots = qr(design, mode="economic", pivoting=True)
    singular = svdvals(design)
    tolerance = np.finfo(float).eps * max(design.shape) * singular[0]
    rank = int(np.sum(np.abs(np.diag(r)) > tolerance))
    full_rank = rank == design.shape[1]
    return {
        "is_full_rank": full_rank,
        "rank": rank,
        "num_parameters": design.shape[1],
        "condition_number": float(singular[0] / singular[-1]) if full_rank else None,
        "aliased_columns": [columns[int(i)] for i in pivots[rank:]],
    }


def bh_adjust(p_values: list[float]) -> list[float]:
    """Benjamini-Hochberg adjustment across exactly the submitted gene family."""
    p = np.asarray(p_values, dtype=float)
    if p.ndim != 1 or not np.isfinite(p).all() or np.any((p < 0) | (p > 1)):
        raise ValueError("p-values must be finite values in [0, 1]")
    if not len(p):
        return []
    order = np.argsort(p, kind="stable")
    adjusted = np.minimum.accumulate(
        (p[order] * len(p) / np.arange(1, len(p) + 1))[::-1]
    )[::-1]
    result = np.empty_like(p)
    result[order] = np.clip(adjusted, 0, 1)
    return np.maximum(result, p).tolist()
