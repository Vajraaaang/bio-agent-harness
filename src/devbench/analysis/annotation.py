"""Bounded doublet and annotation stages; unavailable references stay explicit."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from devbench.core.expression_io import open_expression

from .checkpoints import CheckpointStore, atomic_json
from .scrna_qc import declared_groups, pin_configuration
from .singlecell import (
    annotate_celltypist,
    detect_doublets,
    gene_symbols,
    marker_annotation,
    validate_counts,
)
from .streaming import read_table


def _null_predictions(index):
    return pd.DataFrame(
        {"doublet_score": np.nan, "predicted_doublet": pd.NA},
        index=index,
    )


def _capture_groups(source, capture_key, batch_key, *, strict):
    message = "declared_capture_library_identities_unavailable"
    if strict:
        if getattr(source, "input_info", {}).get("batch_label_basis") is not None:
            raise ValueError(message)
        captures = declared_groups(source.obs, capture_key, message)
        batches = declared_groups(
            source.obs,
            batch_key,
            "declared_integration_batch_identities_unavailable",
        )
        batch_counts = batches.groupby(captures, sort=False).nunique()
        if (batch_counts > 1).any():
            raise ValueError("ambiguous_capture_library_identities_across_batches")
        return captures
    if (
        capture_key not in source.obs
        or source.obs[capture_key].isna().any()
        or any(not str(value).strip() for value in source.obs[capture_key])
    ):
        raise ValueError(message)
    return source.obs[capture_key].astype(str)


def _unavailable_lane(index, group, reason, **detail):
    return _null_predictions(index), {
        "status": "unavailable",
        "batches": [
            {
                "batch": group,
                "cells": len(index),
                "status": "unavailable",
                "reason": reason,
                **detail,
            }
        ],
    }


def _scrublet_lane(
    source,
    rows,
    group,
    capture_key,
    *,
    max_batch_bytes,
    expected_rate,
    seed,
    strict,
):
    index = source.obs.index[rows]
    if strict and len(rows) < 100:
        reason = (
            "fewer_than_100_cells_for_configured_doublet_method"
            if len(rows)
            else "no_qc_passing_cells_in_capture_lane"
        )
        return _unavailable_lane(index, group, reason)
    # Materialize only this capture in a detached, size-checked buffer.
    data = source.subset(rows, max_bytes=max_batch_bytes)
    data.obs = source.obs.iloc[rows][[capture_key]].copy()
    if strict:
        # Invalid counts are an input error rather than a failed Scrublet fit.
        validate_counts(data.X)
    try:
        result, report = detect_doublets(
            data,
            batch_key=capture_key,
            seed=seed,
            copy=False,
            expected_rate=expected_rate,
        )
    except (ValueError, np.linalg.LinAlgError) as error:
        if not strict:
            raise
        return _unavailable_lane(
            index,
            group,
            "scrublet_lane_fit_failed",
            error_type=type(error).__name__,
        )
    return result.obs[["doublet_score", "predicted_doublet"]], report


def _missing_capture_report(store, metrics, batch_key):
    keep = metrics["qc_pass"].to_numpy(dtype=bool)

    def unavailable(folder):
        _null_predictions(metrics.index[keep]).to_csv(folder / "predictions.csv")
        report = {
            "method": "scanpy_scrublet_per_declared_capture",
            "status": "unavailable",
            "reason": "capture_library_role_not_declared",
            "capture_key": None,
            "batches": [],
            "integration_batch_key": batch_key,
            "scope": "predictions_not_known_doublet_truth; capture_identity_required",
        }
        atomic_json(folder / "report.json", report)
        return report

    return store.run("doublets", unavailable, dependencies=("qc",))


def run_doublets(
    source_path: str | Path,
    store: CheckpointStore,
    *,
    layer: str | None,
    batch_key: str,
    seed: int,
    capture_key: str | None = None,
    max_batch_bytes: int = 1024**3,
    require_declared_capture: bool = False,
    expected_rate: float = 0.05,
) -> Path:
    """Predict doublets independently within each declared physical capture."""
    metrics = read_table(store.path("qc") / "metrics.csv")
    if capture_key is None:
        if require_declared_capture:
            raise ValueError("capture_library_role_not_declared")
        return _missing_capture_report(store, metrics, batch_key)
    if not 0 < expected_rate < 1:
        raise ValueError("expected_rate must lie strictly between zero and one")
    qc_pass = metrics["qc_pass"]
    if require_declared_capture and not pd.api.types.is_bool_dtype(qc_pass.dtype):
        raise ValueError("strict_doublet_detection_requires_boolean_qc_pass")
    qc_keep = qc_pass.to_numpy(dtype=bool)
    stages = []
    with open_expression(Path(source_path), layer=layer, batch_key=batch_key) as source:
        if not source.obs.index.equals(metrics.index):
            raise ValueError("doublet_capture_row_identity_mismatch")
        groups = _capture_groups(
            source,
            capture_key,
            batch_key,
            strict=require_declared_capture,
        )
        dependencies = _doublet_dependencies(
            store,
            require_declared_capture,
            expected_rate,
            {
                "counts_layer": layer,
                "batch_key": batch_key,
                "capture_key": capture_key,
                "seed": seed,
                "max_batch_bytes": max_batch_bytes,
                "expected_rate": expected_rate,
            },
        )
        positions = groups.groupby(groups, sort=True).indices
        for index, (group, capture_rows) in enumerate(positions.items()):
            rows = capture_rows[qc_keep[capture_rows]]
            if not len(rows) and not require_declared_capture:
                continue
            name = f"doublets_{index:04d}"

            def operation(folder, *, rows=rows, group=group):
                predictions, report = _scrublet_lane(
                    source,
                    rows,
                    group,
                    capture_key,
                    max_batch_bytes=max_batch_bytes,
                    expected_rate=expected_rate,
                    seed=seed,
                    strict=require_declared_capture,
                )
                predictions.to_csv(folder / "predictions.csv")
                atomic_json(folder / "report.json", report)
                return report

            store.run(name, operation, dependencies=dependencies)
            stages.append(name)

    def finish(folder):
        return _finish_doublets(
            folder,
            store,
            stages,
            metrics.index[qc_keep],
            capture_key,
            batch_key,
            expected_rate=expected_rate,
            strict=require_declared_capture,
        )

    return store.run("doublets", finish, dependencies=stages)


def _doublet_dependencies(store, strict, expected_rate, settings):
    if not strict and expected_rate == 0.05:
        return ("qc",)
    configuration = pin_configuration(
        store,
        "doublet_configuration",
        {**settings, "strict": strict},
        dependencies=("qc",),
    )
    return (configuration,)


def _finish_doublets(
    folder,
    store,
    stages,
    index,
    capture_key,
    batch_key,
    *,
    expected_rate,
    strict,
):
    frame = pd.concat(
        [read_table(store.path(name) / "predictions.csv") for name in stages]
    ).loc[index]
    frame.to_csv(folder / "predictions.csv")
    reports = [
        json.loads((store.path(name) / "report.json").read_text()) for name in stages
    ]
    available = int(frame["doublet_score"].notna().sum())
    status = "unavailable"
    if available:
        status = "completed" if available == len(frame) else "partial"
    report = {
        "method": "scanpy_scrublet_per_declared_capture",
        "filtering": "flag_only",
        "status": status,
        "batches": [batch for record in reports for batch in record["batches"]],
        "capture_key": capture_key,
        "integration_batch_key": batch_key,
        "scope": "predictions_not_known_doublet_truth; caller_declared_capture_library",
    }
    if strict or expected_rate != 0.05:
        report["expected_rate_assumption"] = expected_rate
    atomic_json(folder / "report.json", report)
    return report


def run_annotation(
    source_path,
    store,
    *,
    layer,
    panels=None,
    model_path=None,
    model_sha256=None,
    block_rows=128,
    max_block_bytes=64 * 1024**2,
):
    metrics = read_table(store.path("qc") / "metrics.csv")
    keep = metrics["qc_pass"].to_numpy(dtype=bool)
    stages = []
    panels = panels or {}
    if model_path is None and not panels:

        def unavailable(folder):
            pd.DataFrame({"cell_type": "uncertain"}, index=metrics.index[keep]).to_csv(
                folder / "predictions.csv"
            )
            report = {
                "status": "unavailable_no_model_or_marker_panel",
                "method": None,
                "original_annotation_reproduced": False,
                "scope": (
                    "no_inferred_cell_identity; external_labels_are_not_ground_truth"
                ),
            }
            atomic_json(folder / "report.json", report)
            return report

        return store.run("annotation", unavailable, dependencies=("qc",))
    with open_expression(Path(source_path), layer=layer) as source:
        symbols, _ = gene_symbols(source)
        requested = {
            str(marker).upper() for values in panels.values() for marker in values
        }
        features = (
            None
            if model_path
            else np.flatnonzero(np.isin(symbols.str.upper(), list(requested)))
        )
        if features is not None and not len(features):
            raise ValueError("annotation_marker_panel_has_no_observed_genes")
        for start in range(0, source.shape[0], block_rows):
            stop = min(start + block_rows, source.shape[0])
            rows = np.arange(start, stop)[keep[start:stop]]
            if not len(rows):
                continue
            name = f"annotation_{start:08d}"

            def operation(folder, rows=rows):
                data = source.subset(rows, features, max_bytes=max_block_bytes)
                data.obs = pd.DataFrame(index=data.obs_names)
                if model_path:
                    labels, confidence, report = annotate_celltypist(
                        data, model_path, expected_sha256=model_sha256
                    )
                    frame = pd.DataFrame({"cell_type": labels, "score": confidence})
                else:
                    labels, report = marker_annotation(
                        data,
                        panels,
                        library_totals=metrics.iloc[rows]["total_counts"].to_numpy(),
                    )
                    frame = pd.DataFrame({"cell_type": labels})
                frame.to_csv(folder / "predictions.csv")
                atomic_json(folder / "report.json", report)
                return {"cells": len(frame), "status": report["status"]}

            store.run(name, operation, dependencies=("qc",))
            stages.append(name)

        def finish(folder):
            frame = pd.concat(
                [read_table(store.path(name) / "predictions.csv") for name in stages]
            )
            if not frame.index.equals(metrics.index[keep]):
                raise ValueError("annotation_row_identity_mismatch")
            frame.to_csv(folder / "predictions.csv")
            report = {
                "status": "completed",
                "method": "celltypist" if model_path else "marker_panel",
                "model_sha256": model_sha256,
                "original_annotation_reproduced": False,
                "uncertain_cells": int((frame.cell_type == "uncertain").sum()),
                "scope": (
                    "independent_computational_predictions_not_experimental_accuracy"
                ),
                "chunk_reports": [
                    str(store.path(name).relative_to(store.root) / "report.json")
                    for name in stages
                ],
            }
            atomic_json(folder / "report.json", report)
            return report

        return store.run("annotation", finish, dependencies=stages)
