"""Quality diagnostics with the existing numerical resource-error types."""
from typing import Dict, Optional

from devharness_core.watchdog import HarnessResourceInterrupt, RemoteWorkerError
from ._runtime import execute, sdk_call
from .data import PathLike


@sdk_call
def audit_quality(counts_zarr: PathLike, metadata_csv: PathLike,
                  time_col: str = "embryonic_age", reference_counts_path: Optional[PathLike] = None,
                  candidate_embedding_path: Optional[PathLike] = None,
                  contrast: Optional[dict] = None, thresholds: Optional[Dict[str, float]] = None,
                  max_cells: int = 500, top_genes: int = 200,
                  n_neighbors: int = 10, seed: int = 0) -> dict:
    """Audit expression, replication, contrast validity and representation loss.

    Matrix checks stream bounded chunks; geometry uses capped cells and genes.
    Invalid data remains an explicit failure diagnosis. No contrast is fitted.
    Reference expression and embedding paths, if supplied, stay in the workspace.
    """
    return execute("audit_expression_quality", counts_path=counts_zarr, metadata_path=metadata_csv,
                   time_col=time_col, reference_counts_path=reference_counts_path,
                   candidate_embedding_path=candidate_embedding_path, contrast=contrast,
                   thresholds=thresholds, max_cells=max_cells, top_genes=top_genes,
                   n_neighbors=n_neighbors, seed=seed)


__all__ = ["audit_quality", "HarnessResourceInterrupt", "RemoteWorkerError"]
