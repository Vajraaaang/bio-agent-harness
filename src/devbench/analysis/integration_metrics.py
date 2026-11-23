"""Bounded comparisons of technical mixing, geometry, and label conservation."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from hashlib import sha256
from importlib.metadata import version
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from scipy import sparse
from sklearn.metrics import pairwise_distances_chunked

from devbench.core._files import read_bounded_file
from devbench.core._hashing import decode_receipt_json
from rna_kernels.embedding_qc import (
    EMBEDDING_THRESHOLDS,
    embedding_rank,
    loses_neighbors,
    neighbor_graph_metrics,
    neighborhood_metrics,
)

from .checkpoints import digest

_MAX_CELLS = 100_000
_GEOMETRY_CELLS = 500
_GEOMETRY_NEIGHBORS = 10
_LABEL_QUALIFICATION = "devbench.integration-label-verification/1"
_INDEPENDENT_LABEL_ORIGINS = {"orthogonal_measurement", "independent_assignment"}
MAX_LABEL_MAP_BYTES = 16_000_000
MAX_LABEL_QUALIFICATION_BYTES = 16_384


@dataclass(frozen=True)
class ExternalLabelEvidence:
    """Files and a receipt identity already admitted by the trusted coordinator."""

    labels_path: Path
    qualification_path: Path
    qualification_sha256: str


def _bounded_text(value: object, maximum: int = 1024) -> bool:
    return (
        isinstance(value, str)
        and 0 < len(value) <= maximum
        and value == value.strip()
        and "\x00" not in value
    )


def verify_external_labels(
    evidence: ExternalLabelEvidence,
    *,
    cell_ids: Sequence[str],
    selected_sha256: str,
) -> tuple[np.ndarray, dict[str, str]]:
    """Require a separately pinned, actor-attributed review of exact label bytes."""
    if type(evidence) is not ExternalLabelEvidence:
        raise ValueError("integration_label_qualification_required")
    raw = read_bounded_file(evidence.qualification_path, MAX_LABEL_QUALIFICATION_BYTES)
    if sha256(raw).hexdigest() != evidence.qualification_sha256:
        raise ValueError("integration_label_qualification_hash_mismatch")
    proof = decode_receipt_json(raw)
    fields = {
        "format",
        "actor",
        "rationale",
        "label_origin",
        "source_sha256",
        "labels_sha256",
        "selected_sha256",
        "cell_axis_sha256",
    }
    if (
        type(proof) is not dict
        or set(proof) != fields
        or proof["format"] != _LABEL_QUALIFICATION
        or any(not _bounded_text(proof[key]) for key in ("actor", "rationale"))
        or any(
            not isinstance(proof[key], str)
            or re.fullmatch(r"[0-9a-f]{64}", proof[key]) is None
            for key in ("source_sha256", "labels_sha256", "selected_sha256")
        )
    ):
        raise ValueError("invalid_integration_label_qualification")
    if proof["selected_sha256"] != selected_sha256 or proof[
        "cell_axis_sha256"
    ] != digest(list(cell_ids)):
        raise ValueError("integration_label_cohort_mismatch")
    if (
        not isinstance(proof["label_origin"], str)
        or proof["label_origin"] not in _INDEPENDENT_LABEL_ORIGINS
        or proof["source_sha256"] == selected_sha256
    ):
        raise ValueError("integration_label_origin_unqualified")
    label_bytes = read_bounded_file(evidence.labels_path, MAX_LABEL_MAP_BYTES)
    if sha256(label_bytes).hexdigest() != proof["labels_sha256"]:
        raise ValueError("integration_labels_hash_mismatch")
    labels = decode_receipt_json(label_bytes)
    if (
        type(labels) is not dict
        or set(labels) != set(cell_ids)
        or any(not _bounded_text(label) for label in labels.values())
    ):
        raise ValueError("integration_label_identity_mismatch")
    return np.asarray([labels[cell_id] for cell_id in cell_ids], dtype=object), {
        "qualification_sha256": evidence.qualification_sha256,
        "labels_sha256": proof["labels_sha256"],
        "source_sha256": proof["source_sha256"],
        "label_origin": proof["label_origin"],
    }


def _validate_coordinates(coordinates: np.ndarray, n_cells: int) -> np.ndarray:
    if (
        coordinates.ndim != 2
        or coordinates.shape[0] != n_cells
        or not 1 <= coordinates.shape[1] <= 128
        or coordinates.dtype.kind not in "fiu"
        or coordinates.dtype.itemsize > 8
    ):
        raise ValueError("embedding_dimensions_or_dtype")
    if not np.isfinite(coordinates).all():
        raise ValueError("nonfinite_embedding")
    values = np.asarray(coordinates, dtype=np.float64)
    # Uniform positive scaling preserves the ranks of Euclidean distances.
    # Extreme magnitudes would overflow squared distances or the centering step.
    scale = max(float(values.max()), -float(values.min()))
    if scale > 1e140:
        return values / scale
    return values


def _comparison_geometry(
    reference: np.ndarray, candidate: np.ndarray, n_cells: int, seed: int
) -> dict:
    reference = _validate_coordinates(reference, n_cells)
    candidate = _validate_coordinates(candidate, n_cells)
    tolerance = EMBEDDING_THRESHOLDS["rank_relative_tolerance"]
    rank = embedding_rank(candidate, tolerance)
    if not rank:
        raise ValueError("collapsed_embedding")
    reference_rank = embedding_rank(reference, tolerance)
    if not reference_rank:
        raise ValueError("embedding_reference_unavailable")
    sample = np.arange(n_cells)
    if n_cells > _GEOMETRY_CELLS:
        sample = np.sort(
            np.random.default_rng(seed).choice(
                n_cells,
                _GEOMETRY_CELLS,
                replace=False,
            )
        )
    return {
        **neighborhood_metrics(
            reference[sample],
            candidate[sample],
            _GEOMETRY_NEIGHBORS,
        ),
        "embedding_rank": rank,
        "reference_rank": reference_rank,
        "cells_compared": len(sample),
        "observations_subsampled": len(sample) < n_cells,
        "comparison_rows_sha256": digest(sample.tolist()),
    }


def _batch_structure(
    cell_ids: Sequence[str], graph_cell_ids: Sequence[str], batches: Sequence[str]
) -> tuple[int, np.ndarray]:
    n_cells = len(cell_ids)
    if not 4 <= n_cells <= _MAX_CELLS:
        raise ValueError("insufficient_or_oversize_embedding_cohort")
    if not all(_bounded_text(item, 256) for item in cell_ids):
        raise ValueError("invalid_embedding_observation_id")
    if tuple(cell_ids) != tuple(graph_cell_ids) or len(set(cell_ids)) != n_cells:
        raise ValueError("graph_cell_alignment")
    if len(batches) != n_cells or not all(_bounded_text(item, 256) for item in batches):
        raise ValueError("observed_batch_identities_required")
    observed, codes = np.unique(batches, return_inverse=True)
    return len(observed), codes


def _score_label_conservation(
    candidate: np.ndarray, labels: np.ndarray, provenance: dict[str, str]
) -> dict:
    levels = len(np.unique(labels))
    if not 1 < levels < len(labels):
        return dict(
            status="unavailable",
            reason="unestimable_label_structure",
            provenance=provenance,
        )
    # Exact ASW computes quadratic distances. The fixed limit bounds CPU work
    # while the graph and finite-coordinate checks still cover all observations.
    if len(labels) > 10_000:
        return dict(
            status="unavailable",
            reason="scib_label_metrics_size_bound",
            provenance=provenance,
        )
    try:
        from scib_metrics import silhouette_label
    except ImportError:
        return dict(
            status="unavailable",
            reason="scib_metrics_not_in_runtime",
            provenance=provenance,
        )
    score = float(silhouette_label(candidate, labels, chunk_size=256))
    if not np.isfinite(score) or not 0 <= score <= 1:
        raise ValueError("invalid_scib_label_score")
    return {
        "status": "measured",
        "scib_metrics_version": version("scib-metrics"),
        "silhouette_label": score,
        "cells_evaluated": len(labels),
        "provenance": provenance,
        "scope": "descriptive_label_preservation_relative_to_qualified_reference",
    }


def _conservation(
    candidate: np.ndarray,
    cell_ids: Sequence[str],
    selected_sha256: str | None,
    verified_labels: ExternalLabelEvidence | None,
) -> dict:
    if verified_labels is None:
        return {"status": "unavailable", "reason": "verified_external_labels_required"}
    if not selected_sha256:
        raise ValueError("integration_label_cohort_mismatch")
    labels, provenance = verify_external_labels(
        verified_labels,
        cell_ids=cell_ids,
        selected_sha256=selected_sha256,
    )
    return _score_label_conservation(candidate, labels, provenance)


def assess_integration(
    reference: np.ndarray,
    candidate: np.ndarray,
    graph: Any,
    *,
    cell_ids: Sequence[str],
    graph_cell_ids: Sequence[str],
    batches: Sequence[str],
    seed: int = 0,
    selected_sha256: str | None = None,
    verified_labels: ExternalLabelEvidence | None = None,
) -> dict:
    """Fail closed on invalid geometry or a fragmented saved neighbor graph."""
    report: dict[str, Any] = {
        "format": "devbench.scrna-embedding-assessment/1",
        "status": "fail",
        "embedding_valid": False,
        "diagnostics": [],
        "policy": {
            **EMBEDDING_THRESHOLDS,
            "max_connected_components": 1,
            "geometry_max_cells": _GEOMETRY_CELLS,
            "geometry_seed": seed,
            "comparison_basis": "uncorrected_pca_on_same_selected_cells",
        },
        "geometry": None,
        "graph": None,
        "batch_mixing": {"status": "unavailable", "reason": "embedding_not_valid"},
        "biological_conservation": {
            "status": "unavailable",
            "reason": "embedding_not_valid",
        },
    }
    try:
        n_batches, batch_codes = _batch_structure(cell_ids, graph_cell_ids, batches)
        report["cell_axis_sha256"] = digest(list(cell_ids))
        report["geometry"] = geometry = _comparison_geometry(
            reference,
            candidate,
            len(cell_ids),
            seed,
        )
        report["graph"] = graph_report = neighbor_graph_metrics(graph, batch_codes)
    except (ValueError, np.linalg.LinAlgError) as error:
        report["diagnostics"].append(str(error))
        return report
    if graph_report["connected_components"] != 1:
        report["diagnostics"].append("fragmented_graph")
    if loses_neighbors(geometry, EMBEDDING_THRESHOLDS):
        report["diagnostics"].append("embedding_neighborhood_loss")
    if report["diagnostics"]:
        return report
    if 1 < n_batches < len(cell_ids):
        report["batch_mixing"] = {
            "status": "measured",
            "observed_batches": n_batches,
            "cross_batch_edge_fraction": graph_report["cross_batch_edge_fraction"],
            "scope": "unweighted_edges_across_declared_technical_batches",
        }
    else:
        report["batch_mixing"] = {
            "status": "unavailable",
            "reason": "unestimable_batch_structure",
        }
    report["biological_conservation"] = _conservation(
        candidate,
        cell_ids,
        selected_sha256,
        verified_labels,
    )
    report.update(status="pass", embedding_valid=True)
    return report


def _silhouette(sums, counts, codes):
    rows = np.arange(len(codes))
    own_counts = counts[codes]
    a = np.divide(
        sums[rows, codes],
        own_counts - 1,
        out=np.zeros(len(codes)),
        where=own_counts > 1,
    )
    means = np.divide(
        sums, counts[None, :], out=np.full_like(sums, np.inf), where=counts[None, :] > 0
    )
    means[rows, codes] = np.inf
    b = means.min(axis=1)
    denominator = np.maximum(a, b)
    result = np.divide(
        b - a, denominator, out=np.zeros(len(codes)), where=denominator > 0
    )
    result[own_counts == 1] = (
        0.0  # The standard silhouette definition for singleton groups.
    )
    if not np.isfinite(result).all():
        raise ValueError("nonfinite_silhouette_result")
    return result


def _reference_batch_groups(type_names, type_codes, joint_pairs):
    eligible = []
    reports = []
    for code, name in enumerate(type_names):
        joints = np.flatnonzero(joint_pairs[:, 0] == code)
        count = int((type_codes == code).sum())
        admitted = 1 < len(joints) < count
        reports.append(
            {
                "reference_celltype": str(name),
                "cells": count,
                "batches": len(joints),
                "status": "eligible" if admitted else "ineligible_batch_structure",
            }
        )
        if admitted:
            eligible.append((code, joints))
    return eligible, reports


def _within_type_batch_scores(conditional, type_codes, type_reports):
    batch_scores = []
    for code, record in enumerate(type_reports):
        if record["status"] == "eligible":
            scores = conditional[type_codes == code]
            value = float(np.mean(1 - np.abs(scores)))
            record["batch_ASW"] = value
            batch_scores.append(value)
        else:
            record["batch_ASW"] = None
    return batch_scores


def full_silhouette_metrics(
    representation, batches, reference_types, *, working_memory_mb=64
):
    """Euclidean ASW on all admitted cells, never on a UMAP or a subsample.

    Batch ASW is the scIB definition: average 1-|s| within each eligible stored
    cell-type group, then weight those groups equally. Cell-type ASW is the
    ordinary signed silhouette against the stored predictions. These are
    reference-dependent proxies, not evidence of experimental cell identity.
    """
    x = np.asarray(representation, dtype=np.float64)
    batches = pd.Series(np.asarray(batches), dtype="object")
    if (
        x.ndim != 2
        or len(x) < 2
        or len(batches) != len(x)
        or batches.isna().any()
        or not np.isfinite(x).all()
        or not np.isfinite(working_memory_mb)
        or working_memory_mb <= 0
    ):
        raise ValueError(
            "ASW requires finite embeddings, nonmissing batches and a positive "
            "memory budget"
        )
    batch_codes, batch_names = pd.factorize(batches.astype(str), sort=True)
    labels = (
        pd.Series([None] * len(x), dtype="object")
        if reference_types is None
        else pd.Series(np.asarray(reference_types), dtype="object")
    )
    if len(labels) != len(x):
        raise ValueError("reference_celltype_identity_length_mismatch")
    type_codes, type_names = pd.factorize(labels, sort=True)
    valid = type_codes >= 0
    nb, nt = len(batch_names), len(type_names)
    joint_pairs, joint_valid = np.unique(
        np.column_stack([type_codes[valid], batch_codes[valid]]),
        axis=0,
        return_inverse=True,
    )
    joint_codes = np.full(len(x), -1, dtype=int)
    joint_codes[valid] = joint_valid
    nj = len(joint_pairs)
    row = np.r_[np.arange(len(x)), np.flatnonzero(valid), np.flatnonzero(valid)]
    col = np.r_[batch_codes, nb + type_codes[valid], nb + nt + joint_valid]
    membership = sparse.csr_matrix(
        (np.ones(len(row)), (row, col)), shape=(len(x), nb + nt + nj)
    )
    group_counts = np.asarray(membership.sum(axis=0)).ravel()
    global_batch = np.full(len(x), np.nan)
    celltype = np.full(len(x), np.nan)
    conditional = np.full(len(x), np.nan)
    batch_available = 1 < nb < len(x)
    type_available = 1 < nt < int(valid.sum())
    eligible, type_reports = _reference_batch_groups(
        type_names, type_codes, joint_pairs
    )
    if batch_available or type_available or eligible:
        offset = 0
        for distances in pairwise_distances_chunked(
            x, metric="euclidean", n_jobs=1, working_memory=working_memory_mb
        ):
            stop = offset + len(distances)
            distances[np.arange(len(distances)), np.arange(offset, stop)] = 0.0
            sums = np.asarray(distances @ membership)
            if batch_available:
                global_batch[offset:stop] = _silhouette(
                    sums[:, :nb], group_counts[:nb], batch_codes[offset:stop]
                )
            in_chunk = np.flatnonzero(valid[offset:stop])
            if type_available and len(in_chunk):
                celltype[offset + in_chunk] = _silhouette(
                    sums[in_chunk, nb : nb + nt],
                    group_counts[nb : nb + nt],
                    type_codes[offset + in_chunk],
                )
            for code, joints in eligible:
                selected = np.flatnonzero(type_codes[offset:stop] == code)
                if not len(selected):
                    continue
                lookup = np.full(nj, -1, dtype=int)
                lookup[joints] = np.arange(len(joints))
                columns = nb + nt + joints
                conditional[offset + selected] = _silhouette(
                    sums[np.ix_(selected, columns)],
                    group_counts[columns],
                    lookup[joint_codes[offset + selected]],
                )
            offset = stop
    batch_scores = _within_type_batch_scores(conditional, type_codes, type_reports)
    return {
        "batch_ASW": float(np.mean(batch_scores)) if batch_scores else None,
        "cell_type_ASW": float(celltype[valid].mean()) if type_available else None,
        "batch_silhouette": float(global_batch.mean()) if batch_available else None,
        "cells_evaluated": len(x),
        "reference_label_cells": int(valid.sum()),
        "constant_embedding": bool(np.all(np.var(x, axis=0) == 0)),
        "eligible_reference_types": len(batch_scores),
        "per_reference_type": type_reports,
        "batch_ASW_eligible_cells": sum(
            row["cells"] for row in type_reports if row["status"] == "eligible"
        ),
        "subsampling": False,
        "distance": "euclidean",
        "working_memory_MB": working_memory_mb,
        "batch_ASW_definition": (
            "equal_type_mean_of_within_type_mean(1-abs(batch_silhouette))"
        ),
        "cell_type_ASW_definition": (
            "mean_signed_silhouette_against_stored_celltype_predictions"
        ),
        "batch_ASW_range": [0, 1],
        "cell_type_ASW_range": [-1, 1],
        "higher_is_better_for_both": True,
        "scope": (
            "batch_mixing_and_preservation_of_external_prediction_structure"
            "_not_annotation_validation"
        ),
    }
