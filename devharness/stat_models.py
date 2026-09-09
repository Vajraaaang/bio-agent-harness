"""Thin public wrappers around the original supervised statistical tools."""
from typing import List, Optional

from ._runtime import execute, sdk_call
from .data import PathLike


@sdk_call
def validate_design(metadata_csv: PathLike, formula: str, time_col: str = "embryonic_age") -> dict:
    """Return rank, condition number and aliases without modifying covariates.

    Time labels are parsed into ``numeric_time`` by the original tool. A false
    ``is_full_rank`` is a diagnosis to resolve, not permission to drop batch.
    """
    return execute("validate_design_matrix", metadata_csv_path=metadata_csv,
                   formula_str=formula, time_col=time_col)


@sdk_call
def fit_trajectory(counts_zarr: PathLike, metadata_csv: PathLike, formula: str,
                   genes: List[str], time_col: str = "embryonic_age",
                   selection_criterion: str = "bic", spline_dfs: Optional[List[int]] = None,
                   data_type: str = "auto", strict: bool = True,
                   inference_mode: str = "conditional", monte_carlo_draws: int = 65535,
                   monte_carlo_seed: int = 0) -> dict:
    """Fit 1–100 genes using the existing count/Gaussian spline implementation.

    ``counts_zarr`` also accepts supported HDF5 and gene-by-sample CSV stores.
    ``spline_dfs=None`` selects [2, 3, 4, 5]; for ``fixed``, pass exactly one df.
    Float inputs are continuous in ``auto`` mode. Asserting ``counts`` requires
    valid raw integer counts and retains RLE offsets and EB dispersions.

    All original coefficients, p/q values, candidate scores, warnings, failure
    statuses and inference limitations are returned unchanged. Rank-deficient
    covariates are never removed. ``strict=False`` preserves explicit failure
    records where the original tool supports them. Endpoint predictions hold
    nuisance covariates fixed; they are not means at every collection stage.

    Gaussian temporal tests use an F reference with residual degrees of freedom;
    NB tests retain an asymptotic chi-square reference. Returned test_distribution,
    test_statistic and test_df_num/test_df_denom identify the calculation.
    Default adaptive spline selection is exploratory: its conditional p-values
    do not include selection uncertainty or establish confirmatory inference.

    Opt in to inference_mode='gaussian_selection_mc' for experimental continuous
    Gaussian selection calibration. Prespecify the criterion, candidates, draw
    count and seed. Counts, fixed criterion, unequal-variance/correlated-error
    qualification and selection-adjusted endpoint intervals are unsupported.
    Conditional F diagnostics and a separate BY adjusted value remain explicit.

    Standalone scripts must call this inside ``if __name__ == '__main__':``
    because the existing resource supervisor uses multiprocessing spawn.
    """
    options = {}
    if (inference_mode != "conditional" or type(monte_carlo_draws) is not int
            or monte_carlo_draws != 65535 or type(monte_carlo_seed) is not int or monte_carlo_seed != 0):
        options = {"inference_mode": inference_mode, "monte_carlo_draws": monte_carlo_draws,
                   "monte_carlo_seed": monte_carlo_seed}
    return execute("fit_temporal_trajectory", counts_path=counts_zarr, metadata_path=metadata_csv,
                   formula_str=formula, time_col=time_col, gene_list=genes,
                   selection_criterion=selection_criterion,
                   spline_dfs=[2, 3, 4, 5] if spline_dfs is None else spline_dfs,
                   data_type=data_type, strict=strict, **options)
