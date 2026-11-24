"""Within-section spatial autocorrelation and cell-type neighborhood tests."""

import re
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Literal

import numpy as np
from numpy.typing import NDArray
from pydantic import Field, model_validator
from scipy.sparse import csr_matrix
from scipy.spatial import cKDTree
from scipy.special import ndtr

from ._rna_solver_inputs import (
    Label,
    adjust_probabilities,
    numeric_matrix,
    read_count_and_metadata,
    require_labels,
    solver_receipt,
)
from .scientific_types import PHYSICAL_UNITS, FrozenContract, Sha256

_Floats = NDArray[np.float64]
_Ints = NDArray[np.intp]
_Block = tuple[str, str]


class SpatialPatternSpec(FrozenContract):
    format: Literal["devbench.spatial-pattern/1"] = "devbench.spatial-pattern/1"
    counts_sha256: Sha256
    metadata_sha256: Sha256
    coordinate_frame: Label
    coordinate_units: Label
    k: int = Field(default=6, ge=1, le=128)
    max_radius: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    svg_test: Literal["analytical", "permutation"] = "analytical"
    n_perm: int = Field(default=999, ge=1, le=10_000)
    seed: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def physical_coordinates(self) -> "SpatialPatternSpec":
        if self.coordinate_units not in PHYSICAL_UNITS:
            raise ValueError("coordinate_units_must_be_physical")
        if self.coordinate_frame.strip() != self.coordinate_frame or re.search(
            r"umap|(?:^|[^a-z])pca|t[\W_]*sne|latent|embedding",
            self.coordinate_frame.lower(),
        ):
            raise ValueError("coordinate_frame_must_describe_physical_space")
        return self


def _block_indices(blocks: Sequence[_Block]) -> list[_Ints]:
    groups: dict[_Block, list[int]] = {}
    for index, block in enumerate(blocks):
        groups.setdefault(block, []).append(index)
    return [np.asarray(group, dtype=np.intp) for group in groups.values()]


def build_spatial_graph(
    coordinates: _Floats,
    blocks: Sequence[_Block],
    *,
    k: int,
    max_radius: float | None = None,
) -> csr_matrix:
    """Build a blockwise union-KNN graph with a physical distance cutoff."""
    if (
        coordinates.ndim != 2
        or coordinates.shape != (len(blocks), 2)
        or not np.isfinite(coordinates).all()
        or np.any(np.abs(coordinates) > 1e100)
        or not 1 <= k <= 128
    ):
        raise ValueError("invalid_spatial_graph_coordinates_or_k")
    if max_radius is not None and (not np.isfinite(max_radius) or max_radius <= 0):
        raise ValueError("spatial_graph_requires_positive_finite_max_radius")
    groups = _block_indices(blocks)
    if sum(len(rows) * min(k, len(rows) - 1) for rows in groups) > 2_000_000:
        raise ValueError("spatial_graph_edge_budget_exceeded")
    sources: list[_Ints] = []
    targets: list[_Ints] = []
    for rows in groups:
        neighbors = min(k, len(rows) - 1)
        if neighbors == 0:
            continue
        locations = coordinates[rows]
        if len(np.unique(locations, axis=0)) != len(rows):
            raise ValueError("duplicate_spatial_coordinates_within_donor_section")
        # Stable location order makes geometric distance ties independent of file order.
        order = np.lexsort(locations.T[::-1])
        rows, locations = rows[order], locations[order]
        distances, closest = cKDTree(locations).query(locations, k=neighbors + 1)
        radius = (
            max_radius
            if max_radius is not None
            else 2 * np.quantile(distances[:, 1], 0.90)
        )
        keep = distances[:, 1:] <= radius
        sources.append(np.broadcast_to(rows[:, None], keep.shape)[keep])
        targets.append(rows[closest[:, 1:][keep]])
    size = len(blocks)
    if not sources:
        return csr_matrix((size, size), dtype=float)
    left, right = np.concatenate(sources), np.concatenate(targets)
    directed = csr_matrix((np.ones(len(left)), (left, right)), shape=(size, size))
    return directed.maximum(directed.T)


def _permutation(blocks: list[_Ints], rng: np.random.Generator, size: int) -> _Ints:
    indices = np.arange(size)
    for block in blocks:
        indices[block] = rng.permutation(block)
    return indices


def _randomization_moments(
    centered: _Floats, blocks: list[_Ints], degree: _Floats
) -> tuple[_Floats, _Floats]:
    """Exact first two moments of undirected edge sums under block permutations."""
    mean = np.zeros((2, centered.shape[1]))
    variance = np.zeros_like(mean)
    for block in blocks:
        n = len(block)
        if n < 2:
            continue
        edges = degree[block].sum() / 2
        adjacent = np.sum(degree[block] * (degree[block] - 1))
        disjoint = edges * (edges - 1) - adjacent
        squared = np.square(centered[block])
        s2, s4 = squared.sum(axis=0), np.square(squared).sum(axis=0)
        # Mixed moments of distinct draws from the centered block.
        pairs = n * (n - 1)
        m22 = (s2**2 - s4) / pairs
        m31 = -s4 / pairs
        m211 = (2 * s4 - s2**2) / (pairs * (n - 2)) if n > 2 else 0
        m1111 = -3 * m211 / (n - 3) if n > 3 else 0
        per_edge = np.array([-s2 / pairs, 2 * s2 / (n - 1)])
        mean += edges * per_edge
        second = (
            edges * np.array([m22, 2 * s4 / n - 8 * m31 + 6 * m22])
            + adjacent
            * np.array([np.broadcast_to(m211, s2.shape), s4 / n - 4 * m31 + 3 * m22])
            + disjoint
            * np.array(
                [np.broadcast_to(m1111, s2.shape), 4 * m22 - 8 * m211 + 4 * m1111]
            )
        )
        delta = second - (edges * per_edge) ** 2
        variance += np.where(
            delta > 32 * np.finfo(float).eps * np.abs(second), delta, 0
        )
    return mean, variance


def _autocorrelation(
    expression: _Floats,
    graph: csr_matrix,
    blocks: list[_Ints],
    spec: SpatialPatternSpec,
) -> tuple[_Floats, _Floats, _Floats, _Floats]:
    size, features = expression.shape
    results = tuple(np.full(features, np.nan) for _ in range(4))
    degree = np.asarray(graph.sum(axis=1)).ravel()
    weight = degree.sum()
    if not weight:
        return results[0], results[1], results[2], results[3]
    moran, geary, moran_p, geary_p = results
    for start in range(0, features, 64):
        end = min(start + 64, features)
        z = expression[:, start:end].copy()
        for block in blocks:
            values = z[block]
            centered = values - values.mean(axis=0)
            centered[:, np.ptp(values, axis=0) == 0] = 0
            z[block] = centered
        variance = np.einsum("ij,ij->j", z, z)
        valid = variance > 0
        z, variance = z[:, valid], variance[valid]
        if not len(variance):
            continue
        observed_i, observed_c = _spatial_indices(z, graph, degree, variance)
        indices = start + np.flatnonzero(valid)
        moran[indices], geary[indices] = observed_i, observed_c
        if spec.svg_test == "analytical":
            # Normalize before calculating fourth moments and undo the index scales
            # so that the observed statistics and null moments use the same units.
            mean, null_var = _randomization_moments(
                z / np.sqrt(variance), blocks, degree
            )
            observed = np.array(
                [observed_i * weight / (2 * size), observed_c * weight / (size - 1)]
            )
            tail = (mean - observed) * np.array([[1], [-1]])
            keep = null_var > 0
            scores = np.divide(
                tail, np.sqrt(null_var), out=np.zeros_like(tail), where=keep
            )
            p = np.where(keep, np.maximum(ndtr(scores), np.nextafter(0.0, 1.0)), 1.0)
            moran_p[indices], geary_p[indices] = p
            continue
        i_exceed = np.zeros(len(variance), dtype=int)
        c_exceed = i_exceed.copy()
        rng = np.random.default_rng(spec.seed)
        for _ in range(spec.n_perm):
            shuffled = z[_permutation(blocks, rng, size)]
            perm_i, perm_c = _spatial_indices(shuffled, graph, degree, variance)
            i_exceed += perm_i >= observed_i - 1e-12
            c_exceed += perm_c <= observed_c + 1e-12
        moran_p[indices] = (1 + i_exceed) / (1 + spec.n_perm)
        geary_p[indices] = (1 + c_exceed) / (1 + spec.n_perm)
    return moran, geary, moran_p, geary_p


def _spatial_indices(
    centered: _Floats, graph: csr_matrix, degree: _Floats, variance: _Floats
) -> tuple[_Floats, _Floats]:
    numerator = np.einsum("ij,ij->j", centered, graph @ centered)
    weighted_sq = degree @ np.square(centered)
    size = len(centered)
    return (
        (size / degree.sum()) * (numerator / variance),
        ((size - 1) / degree.sum())
        * (np.maximum(0, weighted_sq - numerator) / variance),
    )


def _enrichment(
    categories: list[str],
    graph: csr_matrix,
    blocks: list[_Ints],
    spec: SpatialPatternSpec,
) -> list[dict[str, Any]]:
    types, labels = np.unique(categories, return_inverse=True)
    n_types = len(types)
    if n_types > 128:
        raise ValueError("spatial_cell_type_family_over_budget")
    left, right = graph.nonzero()
    unique_edges = left < right
    left, right = left[unique_edges], right[unique_edges]
    first, second = np.triu_indices(n_types)
    codes = n_types * first + second

    def pair_counts(assignment: _Ints) -> _Ints:
        a, b = assignment[left], assignment[right]
        pairs = n_types * np.minimum(a, b) + np.maximum(a, b)
        return np.bincount(pairs, minlength=n_types**2)[codes]

    observed = pair_counts(labels)
    mean = np.zeros(len(codes))
    squared_delta = mean.copy()
    exceed = np.zeros(len(codes), dtype=int)
    rng = np.random.default_rng(spec.seed)
    for iteration in range(1, spec.n_perm + 1):
        counts = pair_counts(labels[_permutation(blocks, rng, len(labels))])
        delta = counts - mean
        mean += delta / iteration
        squared_delta += delta * (counts - mean)
        exceed += counts >= observed
    std = np.sqrt(np.maximum(0, squared_delta) / spec.n_perm)
    p_values = [
        float((1 + count) / (1 + spec.n_perm)) if len(left) else None
        for count in exceed
    ]
    q_values = adjust_probabilities(p_values)
    return [
        {
            "cell_types": [str(types[a]), str(types[b])],
            "observed_edges": int(observed[i]),
            "permutation_mean_edges": float(mean[i]) if len(left) else None,
            "z_score": float((observed[i] - mean[i]) / std[i]) if std[i] else None,
            "p_value": p_values[i],
            "q_value": q_values[i],
        }
        for i, (a, b) in enumerate(zip(first, second, strict=True))
    ]


def _gene_results(
    genes: list[str], stats: tuple[_Floats, _Floats, _Floats, _Floats]
) -> tuple[list[dict[str, Any]], dict[str, float | None]]:
    moran, geary, moran_p, geary_p = stats
    mi = [float(x) if np.isfinite(x) else None for x in moran_p[:-1]]
    gc = [float(x) if np.isfinite(x) else None for x in geary_p[:-1]]
    mq, gq = adjust_probabilities(mi), adjust_probabilities(gc)
    rows = []
    for i, gene in enumerate(genes):
        estimable = mi[i] is not None
        rows.append(
            {
                "gene_id": gene,
                "status": "tested" if estimable else "unestimable",
                "moran_i": float(moran[i]) if estimable else None,
                "geary_c": float(geary[i]) if estimable else None,
                "moran_p": mi[i],
                "moran_q": mq[i],
                "geary_p": gc[i],
                "geary_q": gq[i],
            }
        )
    return rows, {
        "moran_i": float(moran[-1]) if np.isfinite(moran[-1]) else None,
        "p_value": float(moran_p[-1]) if np.isfinite(moran_p[-1]) else None,
        "q_value": float(moran_p[-1]) if np.isfinite(moran_p[-1]) else None,
    }


def _depth_residuals(counts: _Floats, totals: _Floats, blocks: list[_Ints]) -> _Floats:
    residuals = np.zeros_like(counts, dtype=float)
    for block in blocks:
        values = counts[block]
        depth = totals[block, None]
        rate = values.sum(axis=0) / depth.sum()
        expected = depth * rate
        sd = np.sqrt(expected * (1 - rate))
        residuals[block] = np.divide(
            values - expected, sd, out=np.zeros_like(expected), where=sd > 0
        )
    return residuals


def fit_spatial_pattern(
    counts_path: Path, metadata_path: Path, spec: SpatialPatternSpec
) -> dict[str, Any]:
    """Analyze spot counts in a declared physical frame with optional cell types."""
    spec = SpatialPatternSpec.model_validate(spec.model_dump())
    counts, metadata, genes = read_count_and_metadata(
        counts_path, metadata_path, spec.counts_sha256, spec.metadata_sha256, "spot_id"
    )
    require_labels(metadata, ("donor_id", "section_id"))
    has_types = "cell_type" in metadata
    if has_types:
        require_labels(metadata, ("cell_type",))
    if not {"x", "y"}.issubset(metadata.columns):
        raise ValueError("spatial_coordinates_require_physical_x_and_y_columns")
    order = np.argsort(metadata.index.to_numpy())
    counts, metadata = counts[order], metadata.iloc[order]
    n_spots = len(counts)
    zero_library = counts.sum(axis=1) == 0
    zero_library_ids = metadata.index[zero_library].tolist()
    n_donor_sections = len(metadata[["donor_id", "section_id"]].drop_duplicates())
    counts, metadata = counts[~zero_library], metadata.loc[~zero_library]
    coordinates = (
        numeric_matrix(metadata[["x", "y"]]) if len(metadata) else np.empty((0, 2))
    )
    block_labels = list(zip(metadata["donor_id"], metadata["section_id"], strict=True))
    graph = build_spatial_graph(
        coordinates, block_labels, k=spec.k, max_radius=spec.max_radius
    )
    active = np.asarray(graph.getnnz(axis=1)) > 0
    blocks = _block_indices([block_labels[i] for i in np.flatnonzero(active)])
    eligible_graph = graph[active][:, active]
    eligible_counts = counts[active]
    totals = eligible_counts.sum(axis=1)
    expression = _depth_residuals(eligible_counts, totals, blocks)
    values = np.column_stack((expression, totals))
    stats = _autocorrelation(values, eligible_graph, blocks, spec)
    gene_results, library_size = _gene_results(genes, stats)
    return solver_receipt(
        "devbench.spatial-pattern-result/1",
        spec,
        {"counts": spec.counts_sha256, "metadata": spec.metadata_sha256},
        {
            "status": "completed" if eligible_graph.nnz else "no_nonisolated_spots",
            "claim_scope": "within_section_spatial_association",
            "count1_unchanged": True,
            "n_spots": n_spots,
            "n_donor_sections": n_donor_sections,
            "zero_library_spot_ids": zero_library_ids,
            "n_undirected_edges": eligible_graph.nnz // 2,
            "isolated_spot_ids": metadata.index[~active].tolist(),
            "gene_statistics": gene_results,
            "library_size_diagnostic": {"measurement": "total_counts", **library_size},
            "neighborhood_enrichment": _enrichment(
                metadata.loc[active, "cell_type"].tolist(),
                eligible_graph,
                blocks,
                spec,
            )
            if has_types
            else [],
            "warnings": [
                "Spatial tests assume exchangeable residuals within donor-section. "
                "The binomial null conditions on library size and targets relative "
                "expression. The analytical reference uses a normal approximation "
                "to the randomization tails.",
                "Moran's I of total_counts measures spatial library-size structure "
                "that can confound expression patterns.",
            ],
            "method": (
                "Symmetric binary KNN with a physical radius within donor-section. "
                "The adaptive radius is twice the 90th percentile nearest-neighbor "
                "distance. Gene tests use centered binomial Pearson residuals "
                "conditional on spot total counts and donor-section gene proportions. "
                f"High Moran I and low Geary C use the {spec.svg_test} reference. "
                "Cell-type edge counts use permutations when annotated. There are "
                "separate BH families for each gene statistic and type-pair "
                "enrichment, plus a separate library-size diagnostic."
            ),
        },
    )
