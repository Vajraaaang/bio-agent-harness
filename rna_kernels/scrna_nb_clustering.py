"""Compute de novo labels using only the clustering side of an NB split."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd
from scipy import sparse

from devbench.core._files import hash_bounded_file, read_bounded_file
from devbench.core._hashing import decode_receipt_json
from devbench.core.expression_io import BackedExpression, open_h5ad
from devbench.core.scientific_types import content_sha256

from .canonical import canonical_json
from .scrna_upstream_contract import (
    MAX_TABLE_BYTES,
    CountSplitClusteringParameters,
    MountedSingleCellRequest,
)

if TYPE_CHECKING:
    from devbench.analysis.checkpoints import CheckpointStore

_FOLD_GROUP = "clustering_fold"


def _split_report(path: Path, request: MountedSingleCellRequest) -> dict:
    raw = read_bounded_file(path, MAX_TABLE_BYTES)
    report = decode_receipt_json(raw)
    if (
        not isinstance(report, dict)
        or canonical_json(report) != raw
        or report.get("format") != "devbench.nb-countsplit/1"
        or report.get("split_distribution") != "dirichlet_multinomial"
        or report.get("marker_inference_performed") is not False
        or not isinstance(report.get("dispersion"), dict)
        or report.get("clustering_counts_sha256") != request.inputs["clustering_counts"]
    ):
        raise ValueError("NB split report does not bind the clustering fold")
    return report


def _fold_axis(source: BackedExpression, report: dict) -> tuple[str, ...]:
    cells = tuple(source.obs.index)
    genes = tuple(source.var.index)
    if (
        source.obs.columns.size
        or source.var.columns.size
        or source._handle.attrs.get("devbench_fold_role") != "clustering"
        or source.shape != (report.get("cells"), report.get("genes"))
        or content_sha256(cells) != report.get("cell_ids_sha256")
        or content_sha256(genes)
        != report.get("dispersion", {}).get("feature_ids_sha256")
    ):
        raise ValueError("NB split report does not bind the clustering fold axes")
    return cells


def _fold_metrics(folder: Path, source: BackedExpression) -> dict[str, int]:
    from devbench.analysis.singlecell import validate_counts

    totals = np.empty(source.shape[0], dtype=np.int64)
    position = 0
    for block in source.iter_rows(block_rows=128, max_bytes=16 * 1024**2):
        validate_counts(block.X)
        values = sparse.csr_matrix(block.X)
        end = position + values.shape[0]
        totals[position:end] = np.asarray(values.sum(axis=1, dtype=np.int64)).ravel()
        position = end
    kept = totals > 0
    if int(kept.sum()) < 3:
        raise ValueError("Clustering requires at least three positive-depth fold cells")
    metrics = pd.DataFrame(
        {_FOLD_GROUP: "fold", "total_counts": totals, "qc_pass": kept},
        index=source.obs.index,
    )
    metrics.to_csv(folder / "metrics.csv")
    source.var.to_csv(folder / "features.csv")
    return {"positive_depth": int(kept.sum()), "zero_depth": int((~kept).sum())}


def _labels_from_graph(
    folder: Path,
    store: CheckpointStore,
    cell_ids: tuple[str, ...],
) -> dict[str, str]:
    from devbench.analysis.streaming import read_table

    assignment = read_table(folder / "embedding.csv", label_columns=("leiden",))
    metrics = read_table(store.path("qc") / "metrics.csv")
    kept = metrics.index[metrics["qc_pass"].to_numpy(dtype=bool)]
    if not assignment.index.equals(kept) or tuple(metrics.index) != cell_ids:
        raise ValueError("Clustering labels do not match the positive-depth fold axis")
    labels = assignment["leiden"].tolist()
    if any(not isinstance(label, str) or not label for label in labels):
        raise ValueError("Clustering labels must be nonempty identifiers")
    if len(set(labels)) < 2:
        raise ValueError("Clustering fold produced fewer than two Leiden groups")
    return dict(zip(assignment.index, labels, strict=True))


def cluster_mounted_fold(
    request: MountedSingleCellRequest,
    paths: Mapping[str, Path],
    store: CheckpointStore,
    parameters: CountSplitClusteringParameters,
) -> dict[str, Path]:
    from devbench.analysis.embedding import run_embedding_stages
    from devbench.analysis.streaming import stream_hvg, stream_selected

    counts = paths["clustering_counts"]
    report = _split_report(paths["split_report"], request)
    with open_h5ad(counts) as source:
        cell_ids = _fold_axis(source, report)
        store.run("qc", lambda folder: _fold_metrics(folder, source))
    hvg = stream_hvg(
        counts,
        store,
        layer=None,
        batch_key=_FOLD_GROUP,
        n_top_genes=parameters.n_top_genes,
    )
    stream_selected(counts, store, layer=None)
    accepted = run_embedding_stages(
        store,
        batch_key=_FOLD_GROUP,
        correction="none",
        governed=True,
        seed=parameters.seed,
        n_pcs=parameters.n_pcs,
        resolution=parameters.resolution,
    )
    if "uncorrected" not in accepted:
        raise ValueError("Clustering fold requires a checked uncorrected graph")
    graph = store.path("uncorrected_graph")
    assessment = store.path("integration_assessment") / "assessment.json"
    labels = _labels_from_graph(graph, store, cell_ids)
    label_path = store.root / "cluster_labels.json"
    label_path.write_bytes(
        canonical_json(
            {
                "format": "devbench.nb-fold-cluster-labels/1",
                "split_report_sha256": request.inputs["split_report"],
                "clustering_counts_sha256": request.inputs["clustering_counts"],
                "cell_ids_sha256": report["cell_ids_sha256"],
                "labels": [
                    {"cell_id": cell, "cluster_id": labels.get(cell)}
                    for cell in cell_ids
                ],
                "inference_performed": False,
            }
        )
    )
    report_path = store.root / "clustering_report.json"
    report_path.write_bytes(
        canonical_json(
            {
                "format": "devbench.nb-fold-clustering/1",
                "clustering_method": "shifted_log_hvg_pca_leiden",
                "clustering_counts_sha256": request.inputs["clustering_counts"],
                "split_report_sha256": request.inputs["split_report"],
                "labels_sha256": hash_bounded_file(label_path, MAX_TABLE_BYTES),
                "hvg_sha256": hash_bounded_file(hvg / "features.csv", MAX_TABLE_BYTES),
                "graph_sha256": hash_bounded_file(
                    graph / "neighbors.npz", MAX_TABLE_BYTES
                ),
                "graph_assessment_sha256": hash_bounded_file(
                    assessment, MAX_TABLE_BYTES
                ),
                "parameters": parameters.model_dump(mode="json"),
                "clusters": len(set(labels.values())),
                "assigned_cells": len(labels),
                "unassigned_zero_depth_cells": len(cell_ids) - len(labels),
                "inference_performed": False,
            }
        )
    )
    return {
        "cluster_labels": label_path,
        "clustering_report": report_path,
        "graph_assessment": assessment,
    }
