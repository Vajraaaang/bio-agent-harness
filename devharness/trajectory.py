"""Bounded temporal summaries and rooted expression pseudotime."""
from ._runtime import execute, sdk_call
from .data import PathLike
from typing import Literal


@sdk_call
def compact_manifold(counts_zarr: PathLike, metadata_csv: PathLike,
                     time_col: str = "embryonic_age") -> dict:
    """Return the existing compact temporal manifold, without raw matrices."""
    return execute("compact_temporal_state", counts_path=counts_zarr,
                   metadata_path=metadata_csv, time_col=time_col)


@sdk_call
def infer_lineage(counts_zarr: PathLike, metadata_csv: PathLike, root_sample_id: str,
                  max_cells: int = 500, top_genes: int = 200, n_components: int = 10,
                  n_neighbors: int = 10, n_clusters: int = 6, seed: int = 0,
                  data_type: Literal["counts", "continuous", "auto"] = "auto") -> dict:
    """Infer rooted expression pseudotime and a cluster tree in a spawned worker.

    The root is explicit. Pseudotime is an inferred ordering, not collection age
    or lineage tracing; original provenance and limitations remain in the result.
    Use an explicit reviewed counts/continuous declaration. Auto is a warned
    legacy heuristic and does not establish the biological measurement scale.
    """
    return execute("infer_lineage", counts_path=counts_zarr, metadata_path=metadata_csv,
                   root_sample_id=root_sample_id, max_cells=max_cells, top_genes=top_genes,
                   n_components=n_components, n_neighbors=n_neighbors, n_clusters=n_clusters, seed=seed,
                   data_type=data_type)
