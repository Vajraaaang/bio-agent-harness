"""Bounded expression-geometry lineage inference in supervised spawned workers.

This exploratory estimator is not lineage tracing, a clinical biomarker assay,
or an identifiable causal reconstruction. No sampling-time label enters its fit.
"""

import hashlib
import io
import json
from pathlib import Path
from typing import Literal

import numpy as np
import pandas as pd

from devharness_core.normalization import count_like, log1p_counts_per_10k
from devharness_core.storage import ChunkedExpressionStore
from devharness_core.units import BIOLOGICAL_UNIT_COLUMNS
from devharness_core.watchdog import run_sandboxed
from rna_kernels.lineage_branches import project_junction_branches


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


def _sample_variances(store, selected, data_type):
    raw_variance = []
    compatible_counts = data_type != "continuous"
    library_sizes = np.zeros(len(selected), dtype=float)
    for _, values in store.iter_gene_chunks(128, sample_indices=selected):
        block = np.asarray(values, dtype=float)
        if not np.isfinite(block).all():
            raise ValueError("Selected expression values must be finite")
        if compatible_counts:
            compatible_counts = count_like(block)
            if not compatible_counts and data_type == "counts":
                raise ValueError(
                    "declared counts require nonnegative integer-valued expression"
                )
            if compatible_counts:
                with np.errstate(over="ignore"):
                    library_sizes += block.sum(axis=0)
        if data_type != "counts":
            with np.errstate(over="ignore", invalid="ignore"):
                raw_variance.extend(np.var(block, axis=1).tolist())
    if compatible_counts:
        if np.any(library_sizes <= 0):
            raise ValueError(
                "Lineage inference requires positive library sizes for all "
                "selected samples"
            )
        log_variance = []
        for _, block in store.iter_gene_chunks(128, sample_indices=selected):
            transformed = log1p_counts_per_10k(block, library_sizes)
            log_variance.extend(np.var(transformed, axis=1).tolist())
        variance = np.asarray(log_variance)
    else:
        variance = np.asarray(raw_variance)
    return variance, library_sizes, compatible_counts


def _lineage_expression(
    counts_path, metadata, root_sample_id, max_cells, top_genes, seed, data_type
):
    with ChunkedExpressionStore(counts_path) as store:
        if set(metadata["sample_id"]) != set(store.sample_names):
            raise ValueError("Expression and metadata sample IDs must match exactly")
        if root_sample_id not in store.sample_names:
            raise ValueError("root_sample_id must identify a measured sample")
        if len(store.sample_names) < 4:
            raise ValueError("Lineage geometry requires at least four measured samples")
        root = store.sample_names.index(root_sample_id)
        selected = np.arange(len(store.sample_names))
        if len(selected) > max_cells:
            rest = np.delete(selected, root)
            selected = np.sort(
                np.r_[
                    root,
                    np.random.default_rng(seed).choice(
                        rest, max_cells - 1, replace=False
                    ),
                ]
            )
        samples = [store.sample_names[int(i)] for i in selected]
        variance, library_sizes, compatible_counts = _sample_variances(
            store,
            selected,
            data_type,
        )
        if not np.isfinite(variance).all():
            raise ValueError("Expression variance overflowed; rescale the input")
        ranked = sorted(
            np.flatnonzero(variance > 1e-12),
            key=lambda i: (-variance[i], store.gene_names[i]),
        )
        if len(ranked) < 2:
            raise ValueError("Lineage inference requires at least two variable genes")
        rows = ranked[:top_genes]
        genes = [store.gene_names[i] for i in rows]
        expression = np.asarray(store.get_chunk(rows, selected), dtype=float)
        if not np.isfinite(expression).all() or (
            compatible_counts and not count_like(expression)
        ):
            raise ValueError(
                "Selected expression no longer matches the validated measurement scale"
            )
        selected_hash = _matrix_sha256(expression, genes, samples)
        total_cells, total_genes = len(store.sample_names), len(store.gene_names)
    return (
        expression,
        genes,
        samples,
        library_sizes,
        compatible_counts,
        selected_hash,
        total_cells,
        total_genes,
    )


def _rooted_pseudotime(coordinates, samples, root_sample_id, n_neighbors, components):
    from scipy.linalg import eigh
    from scipy.sparse import csr_matrix
    from scipy.sparse.csgraph import connected_components, dijkstra
    from scipy.spatial.distance import cdist
    from sklearn.neighbors import NearestNeighbors

    neighbors = min(n_neighbors, len(samples) - 1)
    distances, indices = (
        NearestNeighbors(n_neighbors=neighbors + 1)
        .fit(coordinates)
        .kneighbors(coordinates)
    )
    distances, indices = distances[:, 1:], indices[:, 1:]
    positive = distances[distances > 0]
    if not positive.size:
        raise ValueError("Expression geometry is degenerate: all samples coincide")
    bandwidth = max(float(np.median(positive)), np.finfo(float).eps)
    affinities = np.zeros((len(samples), len(samples)))
    affinities[np.arange(len(samples))[:, None], indices] = np.exp(
        -np.minimum((distances / bandwidth) ** 2, 700)
    )
    affinities = np.maximum(affinities, affinities.T)
    np.fill_diagonal(affinities, 0)
    count, _ = connected_components(csr_matrix(affinities), directed=False)
    if count != 1:
        raise ValueError(
            f"Expression kNN graph has {count} disconnected components; "
            "rooted pseudotime is undefined across them"
        )
    degree = affinities.sum(axis=1)
    symmetric_transition = affinities / np.sqrt(degree[:, None] * degree[None, :])
    eigenvalues, eigenvectors = eigh(
        symmetric_transition,
        subset_by_index=(max(0, len(samples) - components - 1), len(samples) - 1),
    )
    # Remove the stationary eigenvector. Positive modes define a multiscale
    # diffusion embedding; shortest paths avoid distances cutting across arms.
    eigenvalues, eigenvectors = eigenvalues[:-1], eigenvectors[:, :-1]
    positive_modes = eigenvalues > 1e-10
    if not positive_modes.any():
        raise ValueError(
            "No nontrivial positive diffusion modes; geometry is not identifiable"
        )
    eigenvalues, eigenvectors = (
        eigenvalues[positive_modes],
        eigenvectors[:, positive_modes],
    )
    diffusion = (eigenvectors / np.sqrt(degree[:, None])) * (
        eigenvalues / np.maximum(1 - eigenvalues, 1e-10)
    )
    graph_distances = cdist(diffusion, diffusion)
    adjacency = np.where(affinities > 0, np.maximum(graph_distances, 1e-12), 0)
    np.fill_diagonal(adjacency, 0)
    root_index = samples.index(root_sample_id)
    times = np.asarray(
        dijkstra(csr_matrix(adjacency), directed=False, indices=root_index)
    )
    if not np.isfinite(times).all() or times.max() <= 0:
        raise ValueError("Rooted expression distances are undefined")
    times /= times.max()

    return times, root_index, neighbors


def _lineage_graph(coordinates, samples, times, root_index, n_clusters, seed):
    import networkx as nx
    from scipy.sparse.csgraph import minimum_spanning_tree
    from scipy.spatial.distance import cdist
    from sklearn.cluster import KMeans

    cluster_count = min(n_clusters, len(samples), len(np.unique(coordinates, axis=0)))
    clusters = KMeans(
        n_clusters=cluster_count, random_state=seed, n_init=10
    ).fit_predict(coordinates)
    old_ids = sorted(
        set(clusters),
        key=lambda c: min(samples[i] for i in np.flatnonzero(clusters == c)),
    )
    names = {cluster: f"cluster_{i}" for i, cluster in enumerate(old_ids)}
    centroids = np.asarray([coordinates[clusters == c].mean(axis=0) for c in old_ids])
    centroid_distances = cdist(centroids, centroids)
    centroid_distances[centroid_distances == 0] = 1e-12
    np.fill_diagonal(centroid_distances, 0)
    tree = nx.from_scipy_sparse_array(
        minimum_spanning_tree(centroid_distances), create_using=nx.Graph
    )
    root_cluster = old_ids.index(clusters[root_index])
    oriented = nx.bfs_tree(tree, root_cluster)
    branches = {root_cluster: "root"}
    for parent in nx.topological_sort(oriented):
        children = list(oriented.successors(parent))
        for child in children:
            branches[child] = (
                f"branch_{child}" if len(children) > 1 else branches[parent]
            )
    cluster_index = {cluster: index for index, cluster in enumerate(old_ids)}
    branch_labels = project_junction_branches(
        coordinates,
        np.array([cluster_index[cluster] for cluster in clusters]),
        centroids,
        list(oriented.edges),
        branches,
    )
    sample_branches = dict(zip(samples, branch_labels, strict=True))
    nodes = [
        {
            "id": names[c],
            "label": names[c],
            "sample_count": int(np.sum(clusters == c)),
            "mean_pseudotime": float(np.mean(times[clusters == c])),
        }
        for c in old_ids
    ]
    edges = [
        {"source": names[old_ids[a]], "target": names[old_ids[b]]}
        for a, b in oriented.edges
    ]
    return sample_branches, {
        "nodes": nodes,
        "edges": edges,
        "root": names[clusters[root_index]],
    }


def _lineage_worker(
    counts_path: str,
    metadata_path: str,
    root_sample_id: str,
    max_cells: int,
    top_genes: int,
    n_components: int,
    n_neighbors: int,
    n_clusters: int,
    seed: int,
    data_type: str = "auto",
) -> dict:
    from sklearn.decomposition import PCA
    from sklearn.preprocessing import StandardScaler

    if not isinstance(data_type, str) or data_type not in {
        "counts",
        "continuous",
        "auto",
    }:
        raise ValueError("data_type must be counts, continuous or auto")
    metadata = _bounded_metadata(metadata_path)
    (
        expression,
        genes,
        samples,
        library_sizes,
        compatible_counts,
        selected_hash,
        total_cells,
        total_genes,
    ) = _lineage_expression(
        counts_path,
        metadata,
        root_sample_id,
        max_cells,
        top_genes,
        seed,
        data_type,
    )
    if compatible_counts:
        transform = "counts per 10,000, log1p and gene-wise z-score"
        expression = log1p_counts_per_10k(expression, library_sizes)
    else:
        transform = "continuous expression; gene-wise z-score"
    x = StandardScaler().fit_transform(expression.T)
    components = min(n_components, x.shape[1], x.shape[0] - 1)
    pca = PCA(n_components=components, svd_solver="full")
    coordinates = pca.fit_transform(x)
    times, root_index, neighbors = _rooted_pseudotime(
        coordinates,
        samples,
        root_sample_id,
        n_neighbors,
        components,
    )
    sample_branches, lineage_graph = _lineage_graph(
        coordinates,
        samples,
        times,
        root_index,
        n_clusters,
        seed,
    )
    return {
        "pseudotime": dict(zip(samples, times.tolist(), strict=False)),
        "branch_assignments": sample_branches,
        "lineage_graph": lineage_graph,
        "provenance": {
            "algorithm": (
                "variable genes / standardization / PCA / kNN diffusion / rooted "
                "geodesic / cluster MST"
            ),
            "branch_assignment": "nearest incident tree segment within split clusters",
            "root_sample_id": root_sample_id,
            "selected_gene_count": len(genes),
            "selected_genes": genes,
            "input_gene_count": total_genes,
            "input_sample_count": total_cells,
            "selected_sample_count": len(samples),
            "subsampled": total_cells > len(samples),
            "seed": seed,
            "transform": transform,
            "requested_data_type": data_type,
            "resolved_data_type": "counts" if compatible_counts else "continuous",
            "measurement_declaration": "legacy_auto_unverified"
            if data_type == "auto"
            else "explicit_caller_declaration_not_experimental_provenance",
            "measurement_validation_scope": (
                "selected samples across input genes; excluded samples are not "
                "validated"
            ),
            "n_neighbors": neighbors,
            "n_components": components,
            "n_clusters": len(lineage_graph["nodes"]),
            "pca_variance_explained": float(pca.explained_variance_ratio_.sum()),
            "counts_path": counts_path,
            "metadata_path": metadata_path,
            "metadata_sha256": metadata.attrs["source_sha256"],
            "selected_expression_sha256": selected_hash,
            "hash_scope": (
                "selected numeric expression, ordered gene/sample IDs; complete "
                "metadata file"
            ),
            "library_sizes_sha256": _matrix_sha256(
                library_sizes[None, :], ["library_size"], samples
            )
            if compatible_counts
            else None,
            "library_size_scope": "all input genes in selected samples"
            if compatible_counts
            else None,
            "sampling_time_used": False,
        },
        "warnings": (
            [
                {
                    "code": "measurement_type_inferred_from_numeric_values",
                    "severity": "warning",
                    "message": "Legacy auto mode cannot distinguish counts from "
                    "integer-valued normalized expression. "
                    "Use a reviewed explicit data_type declaration; numeric "
                    "appearance is not measurement provenance.",
                }
            ]
            if data_type == "auto"
            else []
        ),
        "limitations": [
            (
                "Pseudotime is rooted expression geometry, not sampling time or "
                "an observed developmental clock."
            ),
            (
                "Cluster MST and branch partitions are exploratory and depend on "
                "root, preprocessing and resolution; they are not lineage-tracing "
                "evidence."
            ),
            (
                "Batch effects and cell composition can change the inferred "
                "geometry; no causal ancestry or clinical biomarker validation is "
                "established."
            ),
            (
                "Only selected samples are assigned; excluded cells are not "
                "silently projected into the fitted trajectory."
            ),
        ],
    }


def infer_lineage(
    counts_path: str,
    metadata_path: str,
    root_sample_id: str,
    max_cells: int = 500,
    top_genes: int = 200,
    n_components: int = 10,
    n_neighbors: int = 10,
    n_clusters: int = 6,
    seed: int = 0,
    data_type: Literal["counts", "continuous", "auto"] = "auto",
) -> dict:
    """Infer bounded, rooted expression pseudotime and a cluster MST in a worker.

    Return per-sample pseudotime, branch assignments, a directed cluster tree,
    preprocessing/input provenance and explicit exploratory limitations.
    Declare counts or continuous from the reviewed input provenance. Continuous
    input stays continuous even when integer-valued. Auto preserves legacy
    behavior with an explicit warning; it is not verified measurement identity.
    """
    if not isinstance(data_type, str) or data_type not in {
        "counts",
        "continuous",
        "auto",
    }:
        raise ValueError("data_type must be counts, continuous or auto")
    for value, name, lower, upper in (
        (max_cells, "max_cells", 4, 1000),
        (top_genes, "top_genes", 2, 2000),
        (n_components, "n_components", 1, 50),
        (n_neighbors, "n_neighbors", 2, 100),
        (n_clusters, "n_clusters", 2, 32),
        (seed, "seed", 0, 2**32 - 1),
    ):
        _bounded_int(value, name, lower, upper)
    if not isinstance(root_sample_id, str) or not root_sample_id:
        raise ValueError("root_sample_id must be a nonempty sample ID")
    return run_sandboxed(
        _lineage_worker,
        (
            str(Path(counts_path).expanduser().resolve()),
            str(Path(metadata_path).expanduser().resolve()),
            root_sample_id,
            max_cells,
            top_genes,
            n_components,
            n_neighbors,
            n_clusters,
            seed,
            data_type,
        ),
        max_mem_mb=2000,
        timeout_sec=120,
    )
