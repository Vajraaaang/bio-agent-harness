"""Capture-scoped scDblFinder and explicitly identified Scrublet predictions."""

from __future__ import annotations

import tempfile
from pathlib import Path

import h5py
import numpy as np
import pandas as pd
import pyarrow.parquet as pq
from scipy import sparse

from devbench.analysis.annotation import _capture_groups, _scrublet_lane
from devbench.analysis.singlecell import validate_counts
from devbench.core._files import hash_bounded_file
from devbench.core.expression_io import open_h5ad

from .scrna_qc_audit import SolverUnavailable, basic_metrics, publish_qc, run_r
from .scrna_upstream_contract import MAX_MATRIX_BYTES, MAX_TABLE_BYTES


def write_csr(f, group, matrix):
    """One cell per CSR row; R sees the same arrays as a column-compressed matrix."""
    x = sparse.csr_matrix(matrix, dtype=np.float64)
    x.sum_duplicates()
    x.sort_indices()
    validate_counts(x)
    if max(*x.shape, x.nnz) > 2**31 - 1:
        raise ValueError("matrix_exceeds_R_sparse_index_limit")
    g = f.create_group(group)
    for key, value in (
        ("p", x.indptr.astype("int32")),
        ("i", x.indices.astype("int32")),
        ("x", x.data),
        ("dim", np.array(x.shape[::-1], dtype="int32")),
    ):
        g.create_dataset(key, data=value)


def _scdblfinder(source, rows, root, expected_rate, seed):
    data = source.subset(rows, max_bytes=MAX_MATRIX_BYTES)
    with h5py.File(root / "counts.h5", "w") as f:
        write_csr(f, "counts", data.X)
    run_r(
        "scrna_doublets.R",
        root,
        seed,
        "auto" if expected_rate is None else expected_rate,
    )
    result = pd.read_csv(root / "predictions.csv")
    if (
        list(result.columns) != ["position", "score", "class"]
        or result.position.tolist() != list(range(1, len(rows) + 1))
        or not result["class"].isin(["singlet", "doublet"]).all()
        or not result.score.between(0, 1).all()
    ):
        raise ValueError("invalid_scdblfinder_output")
    return pd.DataFrame(
        dict(
            doublet_score=result.score.to_numpy(),
            predicted_doublet=result["class"].eq("doublet").to_numpy(),
        ),
        index=source.obs.index[rows],
    )


def predict_captures(
    source,
    qc_keep,
    capture_key,
    batch_key,
    *,
    method,
    expected_rates,
    default_rate,
    allow_fallback,
    seed,
    scratch,
):
    groups = _capture_groups(source, capture_key, batch_key, strict=True)
    positions = groups.groupby(groups, sort=True).indices
    if expected_rates is not None and set(expected_rates) != set(positions):
        raise ValueError("expected_rates_must_match_all_declared_captures")
    scores = pd.DataFrame(index=source.obs.index)
    scores["doublet_score"] = np.nan
    scores["predicted_doublet"] = pd.Series(pd.NA, index=scores.index, dtype="boolean")
    reports = []
    for group, members in positions.items():
        rows = members[np.asarray(qc_keep)[members]]
        rate = expected_rates[group] if expected_rates is not None else default_rate
        actual = method
        reason = None
        predictions = None
        if method == "scdblfinder" and len(rows):
            with tempfile.TemporaryDirectory(dir=scratch) as name:
                try:
                    predictions = _scdblfinder(source, rows, Path(name), rate, seed)
                except SolverUnavailable as error:
                    reason = "scdblfinder_" + str(error)
                    if allow_fallback:
                        actual = "scrublet"
        rate_basis = (
            "declared" if rate is not None else "scdblfinder_capture_size_default"
        )
        if actual == "scrublet" and len(rows):
            if rate is None:
                # Report the loading assumption separately from a supplied rate.
                rate = min(0.5, 0.008 * len(members) / 1000)
                rate_basis = "assumed_0.8_percent_per_1000_loaded_barcodes"
            try:
                predictions, report = _scrublet_lane(
                    source,
                    rows,
                    group,
                    capture_key,
                    max_batch_bytes=MAX_MATRIX_BYTES,
                    expected_rate=rate,
                    seed=seed,
                    strict=True,
                )
                detail = report.get("batches") or [{}]
                scrublet_reason = detail[0].get("reason")
            except ImportError:
                scrublet_reason = "scrublet_runtime_unavailable"
        else:
            scrublet_reason = None
        if predictions is not None:
            scores.loc[predictions.index, "doublet_score"] = predictions.doublet_score
            scores.loc[predictions.index, "predicted_doublet"] = pd.array(
                predictions.predicted_doublet, dtype="boolean"
            )
        available = int(scores.iloc[rows].doublet_score.count())
        reports.append(
            dict(
                batch=str(group),
                cells=len(rows),
                method=actual,
                assessed=available,
                expected_rate=rate,
                rate_basis=rate_basis,
                fallback_reason=reason if actual != method else None,
                reason=None
                if available
                else scrublet_reason or reason or "no_qc_passing_cells",
            )
        )
    return scores, reports


def capture_doublets(paths, p, outputs):
    with open_h5ad(paths["expression"]) as source:
        obs = source.obs.copy()
        metrics, warnings = basic_metrics(source, p)
        if "qc_metrics" in paths:
            path = paths["qc_metrics"]
            f = pq.ParquetFile(path, arrow_extensions_enabled=False)
            if (
                path.stat().st_size > MAX_TABLE_BYTES
                or f.metadata.num_rows != source.shape[0]
                or sum(
                    f.metadata.row_group(i).total_byte_size
                    for i in range(f.num_row_groups)
                )
                > MAX_TABLE_BYTES
            ):
                raise ValueError("invalid_qc_metrics_size")
            qc = pd.read_parquet(path)
            digest = hash_bounded_file(
                paths["expression"], paths["expression"].stat().st_size
            )
            if (
                not qc.index.equals(obs.index)
                or qc.attrs.get("source_sha256") != digest
                or qc.qc_pass.dtype != bool
            ):
                raise ValueError("qc_metrics_source_mismatch")
            metrics["qc_pass"] = qc.qc_pass
        predictions, reports = predict_captures(
            source,
            metrics.qc_pass,
            p.capture_key,
            p.capture_key,
            method=p.method,
            expected_rates=p.expected_rates,
            default_rate=None,
            allow_fallback=p.allow_scrublet_fallback,
            seed=p.seed,
            scratch=outputs,
        )
    metrics = metrics.join(predictions)
    n = int(metrics.doublet_score.count())
    status = (
        "completed"
        if n and n == int(metrics.qc_pass.sum())
        else ("partial" if n else "runtime_unavailable")
    )
    result = dict(
        status=status,
        reason=None if n else "no_admissible_capture_predictions",
        requested_method=p.method,
        batches=reports,
        warnings=warnings,
        filtering="flag_only",
    )
    return publish_qc(outputs, obs, p, result, metrics)
