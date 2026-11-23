"""Numerical admission diagnostics for a declared fitting design."""

import math
from typing import Any


DESIGN_CONDITION_NUMBER_LIMIT = 1000.0


def design_condition_policy(condition_number_limit: float = DESIGN_CONDITION_NUMBER_LIMIT) -> dict[str, Any]:
    """Pure public numerical-admission policy; callers may tighten but not relax it.

The actual fitting matrix is not rescaled to manufacture a passing diagnostic.
Its rank/conditioning and the centered, unit-RMS diagnostic matrix must both
pass. Public B-spline bases and binary coding are applied before this check;
reported biological contrasts retain their declared units and full covariance.
"""
    if (isinstance(condition_number_limit, bool) or not isinstance(condition_number_limit, (int, float))
            or not math.isfinite(condition_number_limit)
            or not 1 <= condition_number_limit <= DESIGN_CONDITION_NUMBER_LIMIT):
        raise ValueError("condition_number_limit must be finite and between 1 and the fixed ceiling 1000")
    return {"condition_number_limit": float(condition_number_limit),
            "maximum_permitted_limit": DESIGN_CONDITION_NUMBER_LIMIT,
            "gate": "both_original_and_standardized_full_rank_and_within_limit",
            "diagnostic_scaling": "preserve_unit_intercept_center_other_columns_then_divide_by_observed_population_RMS",
            "zero_variation_policy": "retain_and_reject_rank_deficiency_never_drop_columns",
            "fitting_parameterization": "unchanged_declared_design_with_its_full_covariance",
            "basis_policy": "public_bounded_spline_basis_and_explicit_binary_or_declared_covariate_columns"}


def design_matrix_diagnostics(matrix, *, condition_number_limit: float = DESIGN_CONDITION_NUMBER_LIMIT) -> dict[str, Any]:
    """Numerical validation for already-authorized workers, without model fitting.

Original and standardized singular values test distinct failure modes. Retaining
both gates prevents a change of units from concealing an unstable fitted matrix.
Centering/scaling is diagnostic only, so coefficient/contrast covariance is never
silently transformed or discarded. No expression-dependent column is dropped.
"""
    policy = design_condition_policy(condition_number_limit)
    import numpy as np

    values = np.asarray(matrix, dtype=float)
    if values.ndim != 2 or not values.size or not np.isfinite(values).all():
        raise ValueError("design_matrix_must_be_nonempty_finite_and_two_dimensional")
    if not np.all(values[:, 0] == 1.0):
        raise ValueError("design_diagnostic_requires_explicit_unit_intercept_in_first_column")
    raw_rank = int(np.linalg.matrix_rank(values))
    raw_number = float(np.linalg.cond(values))
    centers = np.zeros(values.shape[1])
    scales = np.ones(values.shape[1])
    standardized = values.copy()
    if values.shape[1] > 1:
        centers[1:] = values[:, 1:].mean(axis=0)
        centered = values[:, 1:]-centers[None, 1:]
        rms = np.sqrt(np.mean(centered**2, axis=0))
        if not np.isfinite(centered).all() or not np.isfinite(rms).all():
            raise ValueError("design_diagnostic_scaling_nonfinite")
        scales[1:] = np.where(rms > 0, rms, 1.0)
        standardized[:, 1:] = centered/scales[None, 1:]
    standardized_rank = int(np.linalg.matrix_rank(standardized))
    standardized_number = float(np.linalg.cond(standardized))
    valid = (raw_rank == values.shape[1] and standardized_rank == values.shape[1]
             and math.isfinite(raw_number) and math.isfinite(standardized_number)
             and raw_number <= condition_number_limit and standardized_number <= condition_number_limit)
    return {"rank": raw_rank, "standardized_rank": standardized_rank,
            "column_count": values.shape[1], "residual_df": values.shape[0]-raw_rank,
            "condition_number": raw_number if math.isfinite(raw_number) else None,
            "raw_condition_number": raw_number if math.isfinite(raw_number) else None,
            "standardized_condition_number": standardized_number if math.isfinite(standardized_number) else None,
            "condition_number_validated": bool(valid), "condition_number_limit": float(condition_number_limit),
            "diagnostic_column_centers": centers.tolist(), "diagnostic_column_scales": scales.tolist(),
            "design_condition_policy": policy}


def require_design_condition(matrix, *, condition_number_limit: float = DESIGN_CONDITION_NUMBER_LIMIT) -> dict[str, Any]:
    """Enforce the same complete-design gate immediately before a numerical fit."""
    diagnostics = design_matrix_diagnostics(matrix, condition_number_limit=condition_number_limit)
    if diagnostics["rank"] != diagnostics["column_count"] or diagnostics["standardized_rank"] != diagnostics["column_count"]:
        raise ValueError("rank_deficient_design")
    if not diagnostics["condition_number_validated"]:
        raise ValueError("design_condition_policy_rejected: original=" + str(diagnostics["raw_condition_number"])
                         + ", standardized=" + str(diagnostics["standardized_condition_number"])
                         + ", limit=" + str(condition_number_limit))
    return diagnostics
