"""Shared bounded geometry checks for diagnostic and integration workers."""

import numpy as np
from scipy import sparse
from scipy.sparse.csgraph import connected_components

EMBEDDING_THRESHOLDS = {
    "neighborhood_retention_warning": 0.6,
    "trustworthiness_warning": 0.8,
    "rank_relative_tolerance": 1e-8,
}


def embedding_rank(coordinates: np.ndarray, tolerance: float) -> int:
    """Estimate numerical rank after centering the finite saved coordinates."""
    centered = coordinates - coordinates.mean(axis=0)
    singular = np.linalg.svd(centered, compute_uv=False)
    if singular.size == 0 or singular[0] == 0:
        return 0
    return int(np.sum(singular > tolerance * singular[0]))


def neighborhood_metrics(
    reference: np.ndarray, candidate: np.ndarray, n_neighbors: int
) -> dict:
    """Compare Euclidean neighborhoods on caller-bounded aligned coordinates."""
    from scipy.spatial.distance import cdist

    n = len(reference)
    if n < 4:
        return {
            "trustworthiness": None,
            "neighborhood_retention": None,
            "n_neighbors": None,
            "comparison_available": False,
        }
    k = min(n_neighbors, (n - 1) // 2)
    # Rank every cell against the full cohort in memory-bounded blocks.
    block = max(1, min(n, (16 * 1024**2) // (n * 32)))
    intrusion_penalty = retained = 0
    for start in range(0, n, block):
        stop = min(n, start + block)
        rows = np.arange(stop - start)
        cols = np.arange(start, stop)
        reference_distances = cdist(reference[start:stop], reference)
        candidate_distances = cdist(candidate[start:stop], candidate)
        reference_distances[rows, cols] = candidate_distances[rows, cols] = np.inf
        order = np.argsort(reference_distances, axis=1, kind="stable")
        neighbors = np.argsort(candidate_distances, axis=1, kind="stable")[:, :k]
        ranks = np.empty_like(order)
        np.put_along_axis(ranks, order, np.arange(1, n + 1)[None, :], axis=1)
        candidate_ranks = np.take_along_axis(ranks, neighbors, axis=1)
        intrusion_penalty += np.maximum(candidate_ranks - k, 0).sum()
        retained += int(np.count_nonzero(candidate_ranks <= k))

    normalization = n * k * (2 * n - 3 * k - 1)
    return {
        "trustworthiness": float(1 - 2 * intrusion_penalty / normalization),
        "neighborhood_retention": float(retained / (n * k)),
        "n_neighbors": k,
        "comparison_available": True,
    }


def loses_neighbors(metrics: dict, thresholds: dict) -> bool:
    return bool(
        metrics["comparison_available"]
        and (
            metrics["trustworthiness"] < thresholds["trustworthiness_warning"]
            or metrics["neighborhood_retention"]
            < thresholds["neighborhood_retention_warning"]
        )
    )


def neighbor_graph_metrics(graph, batch_codes: np.ndarray) -> dict:
    """Check the complete saved undirected graph and describe technical mixing."""
    count = len(batch_codes)
    if not sparse.issparse(graph) or graph.format != "csr":
        raise ValueError("graph_csr_required")
    if graph.shape != (count, count) or graph.nnz > 64 * count:
        raise ValueError("graph_dimensions_or_edge_budget")
    graph.check_format(full_check=True)
    if graph.dtype.kind not in "biuf" or not np.isfinite(graph.data).all():
        raise ValueError("graph_nonfinite_or_non_numeric_weights")
    if np.any(graph.data < 0):
        raise ValueError("graph_negative_weights")
    # Explicit zeros and diagonal entries do not connect two observations.
    edges = graph.astype(bool)
    edges.sum_duplicates()
    edges.setdiag(0)
    edges.eliminate_zeros()
    if (edges != edges.T).nnz:
        raise ValueError("graph_asymmetric_connectivities")
    components, membership = connected_components(edges, directed=False)
    degrees = np.diff(edges.indptr)
    cross_batch = (np.repeat(batch_codes, degrees) != batch_codes[edges.indices]).sum()
    return {
        "connected_components": int(components),
        "largest_component_fraction": float(np.bincount(membership).max() / count),
        "isolated_observations": int(np.count_nonzero(degrees == 0)),
        "undirected_edges": int(edges.nnz // 2),
        "cross_batch_edge_fraction": float(cross_batch / edges.nnz)
        if edges.nnz
        else None,
    }
