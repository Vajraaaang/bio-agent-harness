"""Historical DevBench signatures for the shared temporal network kernels."""

from pathlib import Path
from typing import Literal

from devharness_core.watchdog import run_sandboxed
from rna_kernels import network
from rna_kernels.network import (
    _enrichment_rows as _enrichment_rows,
)
from rna_kernels.network import (
    _enrichment_worker as _enrichment_worker,
)
from rna_kernels.network import (
    _table_rows,
    _validate_enrichment,
)


def infer_regulatory_network(
    counts_path: str,
    metadata_path: str,
    candidate_tfs: list[str],
    target_genes: list[str],
    time_col: str = "embryonic_age",
    lag: int = 1,
    alpha: float = 1.0,
    max_cells: int = 2000,
    max_edges: int = 1000,
    data_type: Literal["counts", "continuous", "auto"] = "auto",
    aggregation: Literal["time_mean", "sample", "replicate"] = "time_mean",
    replicate_col: str | None = None,
    control_target_history: bool = False,
) -> dict:
    """Rank measured TF-target pairs by regularized lagged temporal association.

    TFs and targets must be explicit. Fits run in a supervised spawned worker;
    the finite scores are exploratory. The default uses observed stage means.
    Set control_target_history to true for a Granger-style comparison against
    an unpenalized autoregressive baseline using target expression at the start
    of each lag pair. This estimates incremental temporal association. The
    default retains the TF-only lag model.
    Sample aggregation requires unique time or pseudotime coordinates.
    Replicate aggregation requires a declared biological identity column and
    fits within-identity lagged associations on aligned observed coordinates.
    Supported columns are biological_unit_id, donor_id, subject_id, specimen_id,
    donor and subject. Metadata declarations leave independence unverified.
    Use continuous for reviewed normalized expression, including integral values.
    """
    return network.infer_regulatory_network(
        counts_path,
        metadata_path,
        candidate_tfs,
        target_genes,
        time_col,
        lag,
        alpha,
        max_cells,
        max_edges,
        data_type,
        aggregation,
        replicate_col,
        control_target_history,
    )


def regulon_enrichment(
    selected_genes: list[str],
    background_genes: list[str],
    reference_path: str,
    max_results: int = 100,
) -> dict:
    """BH-corrected overlap with an explicit TF-target TSV (tf,target columns)."""
    _validate_enrichment(selected_genes, background_genes, max_results)
    path = str(Path(reference_path).expanduser().resolve())
    # Header validation is cheap and gives callers a direct schema error.
    next(_table_rows(path, {"tf", "target"}), None)
    return run_sandboxed(
        _enrichment_worker,
        (selected_genes, background_genes, path, "regulon", max_results),
        max_mem_mb=2000,
        timeout_sec=60,
    )


def motif_enrichment(
    foreground_genes: list[str],
    background_genes: list[str],
    motif_hits_path: str,
    max_results: int = 100,
) -> dict:
    """Test genuine supplied sequence-hit calls, separately from regulon overlap.

    The TSV requires gene,motif,hit and explicit hit/no-hit status for every
    background gene and tested motif. Unmeasured genes cannot become negatives.
    """
    _validate_enrichment(foreground_genes, background_genes, max_results)
    path = str(Path(motif_hits_path).expanduser().resolve())
    next(_table_rows(path, {"gene", "motif", "hit"}), None)
    return run_sandboxed(
        _enrichment_worker,
        (foreground_genes, background_genes, path, "motif", max_results),
        max_mem_mb=2000,
        timeout_sec=60,
    )
