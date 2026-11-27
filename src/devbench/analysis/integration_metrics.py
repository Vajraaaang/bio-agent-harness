"""Bounded comparisons of technical mixing, geometry, and label conservation."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from hashlib import sha256
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
_GEOMETRY_NEIGHBORS = 10
_MIN_GEOMETRY_BATCH_CELLS = 4
_MAX_ISOLATED_FRACTION = 0.1
_COMPARISON_BASIS = "within_batch_uncorrected_pca_on_same_selected_cells"
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
    reference: np.ndarray, candidate: np.ndarray, batch_codes: np.ndarray, seed: int
) -> dict:
    tolerance = EMBEDDING_THRESHOLDS["rank_relative_tolerance"]
    rank = embedding_rank(candidate, tolerance)
    if not rank:
        raise ValueError("collapsed_embedding")
    reference_rank = embedding_rank(reference, tolerance)
    if not reference_rank:
        raise ValueError("embedding_reference_unavailable")
    samples, scores, weights = [], [], []
    for batch in np.unique(batch_codes):
        rows = np.flatnonzero(batch_codes == batch)
        if len(rows) < _MIN_GEOMETRY_BATCH_CELLS:
            continue
        sample = rows
        samples.append(sample)
        weights.append(len(rows))
        scores.append(
            neighborhood_metrics(
                reference[sample], candidate[sample], _GEOMETRY_NEIGHBORS
            )
        )
    sampled_rows = sorted(np.concatenate(samples).tolist()) if samples else []
    neighbor_counts = {score["n_neighbors"] for score in scores}
    return {
        **{
            key: float(np.average([score[key] for score in scores], weights=weights))
            if scores
            else None
            for key in ("neighborhood_retention", "trustworthiness")
        },
        "n_neighbors": (
            next(iter(neighbor_counts)) if len(neighbor_counts) == 1 else None
        ),
        "comparison_available": bool(scores),
        "embedding_rank": rank,
        "reference_rank": reference_rank,
        "batches_compared": len(scores),
        "cells_in_compared_batches": sum(weights),
        "cells_compared": len(sampled_rows),
        "observations_subsampled": len(sampled_rows) < sum(weights),
        "comparison_rows_sha256": digest(sampled_rows),
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
    candidate: np.ndarray,
    batch_codes: np.ndarray,
    labels: np.ndarray,
    provenance: dict[str, str],
) -> tuple[dict, float | None]:
    levels = len(np.unique(labels))
    if levels < 2 or levels == len(labels):
        return dict(
            status="unavailable",
            reason="unestimable_label_structure",
            provenance=provenance,
        ), None
    if len(np.unique(batch_codes)) < 2:
        scores = full_silhouette_metrics(candidate, batch_codes, labels)
        return {
            "status": "measured",
            "cell_type_ASW": scores["cell_type_ASW"],
            "silhouette_label": (1 + scores["cell_type_ASW"]) / 2,
            "cells_evaluated": len(labels),
            "scope": "single_batch_label_preservation",
            "provenance": provenance,
        }, None
    benchmark = scib_benchmark(candidate, batch_codes, labels)
    batch_asw = benchmark["metrics"]["silhouette_batch"]
    silhouette = benchmark["metrics"]["silhouette_label"]
    score = None if silhouette is None else 2 * silhouette - 1
    if score is None:
        return dict(
            status="unavailable",
            reason="unestimable_label_structure",
            provenance=provenance,
        ), batch_asw
    return {
        "status": "measured",
        "cell_type_ASW": score,
        "silhouette_label": (1 + score) / 2,
        "cells_evaluated": len(labels),
        "benchmark": benchmark,
        "provenance": provenance,
        "scope": "descriptive_label_preservation_relative_to_qualified_reference",
    }, batch_asw


def _conservation(
    candidate: np.ndarray,
    batch_codes: np.ndarray,
    cell_ids: Sequence[str],
    selected_sha256: str | None,
    verified_labels: ExternalLabelEvidence | None,
) -> tuple[dict, float | None]:
    if verified_labels is None:
        return {
            "status": "unavailable",
            "reason": "verified_external_labels_required",
        }, None
    if not selected_sha256:
        raise ValueError("integration_label_cohort_mismatch")
    labels, provenance = verify_external_labels(
        verified_labels,
        cell_ids=cell_ids,
        selected_sha256=selected_sha256,
    )
    return _score_label_conservation(candidate, batch_codes, labels, provenance)


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
    """Check within-batch geometry and describe mixing on the complete saved graph."""
    report: dict[str, Any] = {
        "format": "devbench.scrna-embedding-assessment/1",
        "status": "fail",
        "embedding_valid": False,
        "diagnostics": [],
        "warnings": [],
        "policy": {
            **EMBEDDING_THRESHOLDS,
            "max_isolated_fraction": _MAX_ISOLATED_FRACTION,
            "geometry_population": "all_eligible_batches",
            "geometry_seed": seed,
            "geometry_min_cells_per_batch": _MIN_GEOMETRY_BATCH_CELLS,
            "comparison_basis": _COMPARISON_BASIS,
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
        reference = _validate_coordinates(reference, len(cell_ids))
        candidate = _validate_coordinates(candidate, len(cell_ids))
        report["geometry"] = geometry = _comparison_geometry(
            reference,
            candidate,
            batch_codes,
            seed,
        )
        report["graph"] = graph_report = neighbor_graph_metrics(graph, batch_codes)
    except (ValueError, np.linalg.LinAlgError) as error:
        report["diagnostics"].append(str(error))
        return report
    if graph_report["connected_components"] > 1:
        report["warnings"].append("graph_disconnected_components")
    isolates = graph_report["isolated_observations"]
    if isolates:
        report["warnings"].append("graph_has_isolated_observations")
    if not graph_report["undirected_edges"]:
        report["diagnostics"].append("graph_no_edges")
    elif isolates / len(cell_ids) > _MAX_ISOLATED_FRACTION:
        report["diagnostics"].append("graph_excessive_isolates")
    if geometry["batches_compared"] < n_batches:
        report["warnings"].append("within_batch_geometry_incomplete")
    if loses_neighbors(geometry, EMBEDDING_THRESHOLDS):
        report["diagnostics"].append("embedding_neighborhood_loss")
    if report["diagnostics"]:
        return report
    conservation, batch_asw = _conservation(
        candidate, batch_codes, cell_ids, selected_sha256, verified_labels
    )
    if 1 < n_batches < len(cell_ids):
        report["batch_mixing"] = {
            "status": "measured",
            "observed_batches": n_batches,
            "cross_batch_edge_fraction": graph_report["cross_batch_edge_fraction"],
            "batch_ASW": batch_asw,
            "scope": "unweighted_edges_across_declared_technical_batches",
        }
    else:
        report["batch_mixing"] = {
            "status": "unavailable",
            "reason": "unestimable_batch_structure",
        }
    report["biological_conservation"] = conservation
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


def _reference_scib(x, batch, labels, indices, distances, seed):
    """CPU reference metrics using full-data silhouettes and exact neighbor indices."""
    from scipy.sparse.csgraph import connected_components
    from scipy.stats import chi2
    from sklearn.cluster import KMeans
    from sklearn.metrics import (
        adjusted_rand_score,
        normalized_mutual_info_score,
        silhouette_samples,
    )

    classes = np.unique(labels)
    silhouettes = silhouette_samples(x, labels, working_memory=32)
    batch_counts = [len(np.unique(batch[labels == c])) for c in classes]
    isolated = [
        c for c, n in zip(classes, batch_counts, strict=True) if n == min(batch_counts)
    ]
    connectivity = []
    graph = sparse.csr_matrix(
        (
            np.ones(indices.size),
            indices.ravel(),
            np.arange(0, indices.size + 1, indices.shape[1]),
        ),
        shape=(len(x), len(x)),
    )
    for c in classes:
        group = labels == c
        _, membership = connected_components(
            graph[group][:, group], connection="strong"
        )
        connectivity.append(np.bincount(membership).max() / group.sum())
    partition = KMeans(
        n_clusters=len(classes), random_state=seed, n_init=10
    ).fit_predict(x)
    levels, codes = np.unique(batch, return_inverse=True)
    observed = np.stack(
        [(codes[indices] == i).sum(axis=1) for i in range(len(levels))], axis=1
    )
    expected = np.bincount(codes) / len(codes) * indices.shape[1]
    stat = np.sum((observed - expected) ** 2 / expected, axis=1)

    # Entropy-matched Gaussian kernel weights for inverse Simpson diversity.
    # Solve one bandwidth per observation, removing self exactly as in LISI.
    weights = distances.copy()
    for i, (row, idx) in enumerate(zip(distances, indices, strict=True)):
        valid = idx != i
        d = row[valid] - row[valid].min()
        target = np.log(max(1, indices.shape[1] // 3))
        low, high, beta = 0.0, np.inf, 1.0
        for _ in range(50):
            raw = np.exp(-d * beta)
            probabilities = raw / raw.sum()
            entropy = np.log(raw.sum()) + beta * np.dot(d, probabilities)
            if abs(entropy - target) < 1e-5:
                break
            if entropy > target:
                low = beta
                beta = beta * 2 if np.isinf(high) else (beta + high) / 2
            else:
                high = beta
                beta = (beta + low) / 2
        weights[i] = 0
        weights[i, valid] = probabilities

    def lisi(values):
        levels, codes = np.unique(values, return_inverse=True)
        local = np.asarray(
            [
                1 / np.sum(np.bincount(codes[idx], weights=w) ** 2)
                for idx, w in zip(indices, weights, strict=True)
            ]
        )
        return (np.median(local) - 1) / (len(levels) - 1)

    return {
        "silhouette_label": (1 + silhouettes.mean()) / 2,
        "silhouette_batch": full_silhouette_metrics(x, batch, labels)["batch_ASW"],
        "isolated_labels": np.mean(
            [((1 + silhouettes[labels == c]) / 2).mean() for c in isolated]
        ),
        "graph_connectivity": np.mean(connectivity),
        "ari": adjusted_rand_score(labels, partition),
        "nmi": normalized_mutual_info_score(labels, partition),
        "ilisi": lisi(batch),
        "clisi": 1 - lisi(labels),
        "kbet": np.mean(chi2.sf(stat, len(levels) - 1) >= 0.05),
    }


def scib_benchmark(coordinates, batches, reference_labels, seed=0):
    """Report separate scIB axes, metric values, backend and cohort size."""
    from sklearn.neighbors import NearestNeighbors

    x = _validate_coordinates(np.asarray(coordinates), len(batches))
    batch = np.asarray(batches)
    labels = np.asarray(reference_labels)
    if (
        labels.shape != batch.shape
        or batch.shape != (len(x),)
        or not all(1 < len(np.unique(v)) < len(x) for v in (batch, labels))
        or not embedding_rank(x, 1e-8)
    ):
        raise ValueError("unestimable_scib_design")
    # A population-wide neighborhood makes kBET and connectivity vacuous.
    k = min(90, max(3, int(np.ceil(np.sqrt(len(x))))), len(x) - 1)
    distances, indices = NearestNeighbors(n_neighbors=k, n_jobs=1).fit(x).kneighbors(x)
    try:
        import scib_metrics as scib
    except ModuleNotFoundError as exc:
        if exc.name != "scib_metrics":
            raise
        scores = _reference_scib(x, batch, labels, indices, distances, seed)
        backend = "numpy_scipy_sklearn_reference"
    else:
        from scib_metrics.nearest_neighbors import NeighborsResults

        nn = NeighborsResults(indices=indices, distances=distances)
        concordance = scib.nmi_ari_cluster_labels_kmeans(x, labels)
        estimable = any(
            1
            < len(np.unique(batch[labels == label]))
            < np.count_nonzero(labels == label)
            for label in np.unique(labels)
        )
        scores = {
            "silhouette_label": scib.silhouette_label(x, labels),
            "silhouette_batch": scib.silhouette_batch(x, labels, batch)
            if estimable
            else None,
            "graph_connectivity": scib.graph_connectivity(nn, labels),
            "ilisi": scib.ilisi_knn(nn, batch),
            "clisi": scib.clisi_knn(nn, labels),
            "kbet": scib.kbet(nn, batch)[0],
            "isolated_labels": scib.isolated_labels(x, labels, batch),
            "ari": concordance["ari"],
            "nmi": concordance["nmi"],
        }
        backend = "scib-metrics/" + scib.__version__
    scores = {
        k: float(v) if v is not None and np.isfinite(v) else None
        for k, v in scores.items()
    }
    axes = {
        "batch_correction": ["silhouette_batch", "ilisi", "kbet", "graph_connectivity"],
        "bio_conservation": [
            "silhouette_label",
            "clisi",
            "ari",
            "nmi",
            "isolated_labels",
        ],
    }
    return {
        "backend": backend,
        "cells_evaluated": len(x),
        "metrics": scores,
        "axis_metrics": axes,
        "neighbors": k,
        "kbet_definition": "exact_knn_global_batch_acceptance",
        **{
            axis: float(np.mean([scores[key] for key in keys]))
            if all(scores[key] is not None for key in keys)
            else None
            for axis, keys in axes.items()
        },
    }
