"""Donor-level differential abundance of overlapping embedding neighborhoods."""

import hashlib
import math
import warnings
from pathlib import Path
from typing import Any, Literal, NamedTuple, Self

import numpy as np
import pandas as pd
from numpy.typing import NDArray
from pydantic import Field, model_validator
from scipy.spatial import cKDTree
from scipy.stats import chi2, rankdata

from rna_kernels.nb_irls import DenseDesign, NBIRLSGene, _deviance, fit_statsmodels_nb
from rna_kernels.normalization import (
    MAX_BLOCK_BYTES,
    calibrate_count_family,
    calibrate_counts,
    select_calibration_panel_indices,
)

from ._hashing import canonical_sha256
from ._rna_solver_inputs import (
    MAX_INPUT_BYTES,
    MAX_MATRIX_ENTRIES,
    ConditionContrast,
    adjust_probabilities,
    donor_design,
    indexed_rows,
    numeric_matrix,
    read_solver_csv,
)
from .family_fitting import NB_NORMAL_WALD_REFERENCE, conditional_nb_contrasts
from .scientific_types import FrozenContract, Probability, Sha256, content_sha256

_Floats = NDArray[np.float64]
_Counts = NDArray[np.int64]


class NeighborhoodDASpec(FrozenContract):
    """Freeze the two cell-keyed inputs, donor contrast, and graph sampling."""

    format: Literal["devbench.scrna-neighborhood-da/1"] = (
        "devbench.scrna-neighborhood-da/1"
    )
    embedding_sha256: Sha256
    metadata_sha256: Sha256
    contrast: ConditionContrast
    k: int = Field(ge=1)
    prop: float = Field(gt=0, le=1)
    seed: int = Field(ge=0, lt=2**32)
    refined: bool = True


class NeighborhoodDAStatistic(FrozenContract):
    center_cell_id: str
    center_coordinates: tuple[float, ...]
    membership_sha256: Sha256
    kth_neighbor_distance: float = Field(gt=0)
    dispersion_alpha: float | None = Field(default=None, gt=0)
    log2_fold_change: float | None = None
    log2_standard_error: float | None = Field(default=None, gt=0)
    p_value: Probability | None = None
    q_value: Probability | None = None
    spatial_fdr: Probability | None = None
    missing_reason: str | None = None

    @model_validator(mode="after")
    def validate_statistics(self) -> Self:
        statistics = (
            self.log2_fold_change,
            self.log2_standard_error,
            self.q_value,
            self.spatial_fdr,
        )
        if self.p_value is None:
            if self.missing_reason is None or any(
                value is not None for value in statistics
            ):
                raise ValueError("unestimable_neighborhood_statistics_must_be_null")
        elif (
            self.missing_reason is not None
            or any(value is None for value in statistics)
            or self.q_value is None
            or self.spatial_fdr is None
            or not self.p_value <= min(self.q_value, self.spatial_fdr)
        ):
            raise ValueError("invalid_neighborhood_test_statistics")
        return self


class NeighborhoodDAReceipt(FrozenContract):
    format: Literal["devbench.scrna-neighborhood-da-result/1"] = (
        "devbench.scrna-neighborhood-da-result/1"
    )
    receipt_sha256: Sha256
    specification_sha256: Sha256
    specification: NeighborhoodDASpec
    method: Literal["donor_nb_glm_milo_spatial_fdr"] = "donor_nb_glm_milo_spatial_fdr"
    claim_scope: Literal["relative_neighborhood_abundance"] = (
        "relative_neighborhood_abundance"
    )
    clinical_release_allowed: Literal[False] = False
    status: Literal["completed", "no_estimable_neighborhoods"]
    n_cells: int
    coordinate_columns: tuple[str, ...]
    donor_ids: tuple[str, ...]
    donors_per_condition: dict[str, int]
    design_diagnostics: dict[str, Any]
    count_matrix_sha256: Sha256
    normalization: dict[str, Any]
    dispersion_calibration: dict[str, Any]
    dispersion_diagnostics: tuple[dict[str, Any], ...]
    warnings: tuple[str, ...]
    neighborhoods: tuple[NeighborhoodDAStatistic, ...]
    limitations: tuple[str, ...]


class _Estimate(NamedTuple):
    alpha: float | None = None
    effect: float | None = None
    stderr: float | None = None
    probability: float | None = None
    reason: str | None = None


class _Neighborhoods(NamedTuple):
    centers: NDArray[np.intp]
    members: NDArray[np.intp]
    radii: _Floats
    counts: _Counts


def _graph(
    coordinates: _Floats,
    donor_indexes: NDArray[np.intp],
    n_donors: int,
    spec: NeighborhoodDASpec,
) -> _Neighborhoods:
    n_cells = len(coordinates)
    if spec.k >= n_cells:
        raise ValueError("k_requires_at_least_k_plus_one_cells")
    n_centers = math.ceil(spec.prop * n_cells)
    # cKDTree returns both float distances and integer cell indexes.
    if n_centers * (spec.k + 1) * 16 > MAX_INPUT_BYTES:
        raise ValueError("neighborhood_graph_over_budget")
    if n_centers * n_donors > MAX_MATRIX_ENTRIES:
        raise ValueError("neighborhood_by_donor_counts_over_budget")

    rng = np.random.default_rng(spec.seed)
    centers = np.sort(rng.choice(n_cells, n_centers, replace=False))
    tree = cKDTree(coordinates)
    distances, members = tree.query(coordinates[centers], k=spec.k + 1)
    if spec.refined and np.all(np.isfinite(distances[:, -1]) & (distances[:, -1] > 0)):
        medians = np.stack(
            [
                np.median(coordinates[neighbors[neighbors != center]], axis=0)
                for center, neighbors in zip(centers, members, strict=True)
            ]
        )
        centers = np.unique(tree.query(medians, k=1)[1])
        distances, members = tree.query(coordinates[centers], k=spec.k + 1)
    radii = distances[:, -1].copy()
    if not np.isfinite(radii).all():
        raise ValueError("nonfinite_kth_neighbor_distance")
    if np.any(radii <= 0):
        raise ValueError("zero_kth_neighbor_distance")
    counts = np.empty((len(centers), n_donors), dtype=np.int64)
    for row, neighbors in enumerate(members):
        counts[row] = np.bincount(donor_indexes[neighbors], minlength=n_donors)
    return _Neighborhoods(centers, members, radii, counts)


def _spatial_fdr(
    probabilities: list[float | None], weights: _Floats
) -> list[float | None]:
    """Weighted step-up adjustment with failed hypotheses padded at p=1."""
    if (
        weights.shape != (len(probabilities),)
        or not np.isfinite(weights).all()
        or not (weights > 0).all()
    ):
        raise ValueError("spatial_fdr_requires_positive_finite_weights")
    if not probabilities:
        return []
    values = np.asarray([1.0 if value is None else value for value in probabilities])
    order = np.argsort(values, kind="stable")
    scaled_weights = weights / weights.max()
    if np.any(scaled_weights == 0):
        raise ValueError("spatial_fdr_weight_dynamic_range_over_budget")
    cumulative = np.cumsum(scaled_weights[order], dtype=float)
    weighted = np.minimum(values[order] * cumulative[-1], cumulative) / cumulative
    adjusted = np.minimum.accumulate(weighted[::-1])
    result: list[float | None] = [None] * len(order)
    for index, q_value in zip(order, adjusted[::-1], strict=True):
        p_value = probabilities[index]
        if p_value is not None:
            result[index] = max(p_value, min(1.0, float(q_value)))
    return result


def _tmm_offsets(
    counts: _Counts, total_cells: _Counts, donor_ids: pd.Index
) -> tuple[_Floats, dict]:
    upper_quartiles = np.quantile(counts, 0.75, axis=0) / total_cells
    reference = int(
        np.argmax(np.sqrt(counts).sum(axis=0))
        if np.median(upper_quartiles) < 1e-20
        else np.argmin(np.abs(upper_quartiles - upper_quartiles.mean()))
    )
    log_factors = np.zeros(len(total_cells))
    fallback = []
    for i, library in enumerate(total_cells):
        if i == reference:
            continue
        shared = (counts[:, i] > 0) & (counts[:, reference] > 0)
        observed, baseline = counts[shared, i], counts[shared, reference]
        log_obs = np.log(observed / library)
        log_ref = np.log(baseline / total_cells[reference])
        m, a = log_obs - log_ref, (log_obs + log_ref) / 2
        count_variance = (1 - observed / library) / observed + (
            1 - baseline / total_cells[reference]
        ) / baseline
        trim_m, trim_a = int(0.30 * len(m)), int(0.05 * len(a))
        mr, ar = rankdata(m), rankdata(a)
        keep = (
            (mr >= trim_m + 1)
            & (mr <= len(m) - trim_m)
            & (ar >= trim_a + 1)
            & (ar <= len(a) - trim_a)
            & (count_variance > 0)
        )
        if keep.any():
            log_factors[i] = np.average(m[keep], weights=1 / count_variance[keep])
        else:
            fallback.append(donor_ids[i])
    log_factors -= log_factors.mean()
    effective = np.log(total_cells) + log_factors
    factors = np.exp(effective - effective.mean())
    return factors, {
        "method": "tmm",
        "scope": "all_sampled_neighborhoods_with_total_donor_cell_libraries",
        "reference_donor": donor_ids[reference],
        "per_donor_total_cells": dict(
            zip(donor_ids, total_cells.tolist(), strict=True)
        ),
        "per_donor_tmm_factors": dict(
            zip(donor_ids, np.exp(log_factors).tolist(), strict=True)
        ),
        "per_donor_size_factors": dict(zip(donor_ids, factors.tolist(), strict=True)),
        "donors_without_tmm_weight_support": fallback,
    }


def _calibrate(
    counts: _Counts,
    centers: tuple[str, ...],
    design: pd.DataFrame,
    seed: int,
    total_cells: _Counts,
) -> tuple[_Floats, dict, dict, dict, list[dict], list[str]]:
    factors, normalization = _tmm_offsets(counts, total_cells, design.index)

    # Reuse the same empirical-Bayes dispersion reference and TMM exposure.
    indices, panel = select_calibration_panel_indices(
        centers, len(design), (), seed=seed
    )
    donor_metadata = pd.DataFrame(index=design.index)
    reference = calibrate_counts(
        counts[indices],
        panel,
        donor_metadata,
        design,
        strict=False,
        size_factors=factors,
    )
    rows_per_block = max(1, MAX_BLOCK_BYTES // (8 * len(design)))
    blocks = (
        (
            centers[start : start + rows_per_block],
            counts[start : start + rows_per_block],
        )
        for start in range(0, len(centers), rows_per_block)
    )
    dispersion, diagnostics, observed_warnings, filtered = calibrate_count_family(
        blocks, reference, donor_metadata, design, strict=False
    )
    return (
        np.log(factors),
        normalization,
        dispersion,
        filtered,
        diagnostics,
        observed_warnings,
    )


def _test_estimate(batch: Any, row: int, alpha: float, contrast: _Floats) -> _Estimate:
    if not batch.converged[row]:
        return _Estimate(alpha=alpha, reason="nb_irls_nonconvergence")
    fitted = NBIRLSGene(batch, row)
    try:
        projection = conditional_nb_contrasts(fitted, contrast)
    except (ValueError, ArithmeticError, np.linalg.LinAlgError):
        projection = None
    if projection is None:
        return _Estimate(alpha=alpha, reason="nb_contrast_covariance_unavailable")
    ((effect, variance, _),) = projection
    if not math.isfinite(effect) or not math.isfinite(variance) or variance <= 0:
        return _Estimate(alpha=alpha, reason="nb_contrast_covariance_unavailable")
    stderr = math.sqrt(variance)
    statistic = abs(effect) / stderr
    if not math.isfinite(statistic):
        return _Estimate(alpha=alpha, reason="nb_wald_statistic_nonfinite")
    return _Estimate(
        alpha, effect, stderr, max(math.ulp(0.0), math.erfc(statistic / math.sqrt(2)))
    )


def _nb_fitted_mean(
    counts: _Floats, design: DenseDesign, alpha: float, offset: _Floats
) -> _Floats | None:
    # The streaming IRLS kernel deliberately rejects exact fits. A saturated
    # model's likelihood supremum is the saturated likelihood, including zeros.
    if design.size == len(counts):
        return counts
    fitted = fit_statsmodels_nb(design, counts[None, :], [alpha], offset)
    if fitted.converged[0]:
        return np.exp(design.matrix @ fitted.params[0] + offset)
    if np.all(counts > 0):
        log_mean = np.log(counts) - offset
        coefficients = np.linalg.lstsq(design.matrix, log_mean, rcond=None)[0]
        if np.allclose(design.matrix @ coefficients, log_mean, rtol=0, atol=1e-10):
            return counts
    return None


def _nb_boundary_contrast(
    counts: _Floats, design: DenseDesign, offset: _Floats, alpha: float
) -> _Estimate:
    """NB LRT on observed counts when one arm's fitted means reach zero."""
    matrix = design.matrix
    treated = matrix[:, 1] == 1
    if not counts.any():
        return _Estimate(alpha=alpha, reason="nb_boundary_all_counts_zero")
    positive_arm = treated if counts[treated].any() else ~treated
    nuisance = np.delete(matrix, 1, axis=1)
    null = _nb_fitted_mean(counts, DenseDesign(nuisance), alpha, offset)
    # On the nonzero arm, nuisance levels can drop out. Use a full-rank
    # basis for the original fitted subspace.
    u, singular, _ = np.linalg.svd(nuisance[positive_arm], full_matrices=False)
    rank = np.count_nonzero(
        singular > singular[0] * max(nuisance.shape) * np.finfo(float).eps
    )
    active_counts = counts[positive_arm]
    alternative = _nb_fitted_mean(
        active_counts, DenseDesign(u[:, :rank]), alpha, offset[positive_arm]
    )
    if null is None or alternative is None:
        return _Estimate(alpha=alpha, reason="nb_boundary_likelihood_nonconvergence")
    null_dev = _deviance(counts[None, :], null[None, :], alpha)[0]
    active_dev = (
        0.0
        if alternative is active_counts
        else _deviance(active_counts[None, :], alternative[None, :], alpha)[0]
    )
    statistic = float(null_dev - active_dev)
    if not math.isfinite(statistic) or statistic < -1e-7:
        return _Estimate(alpha=alpha, reason="nb_boundary_likelihood_invalid")

    # Use a library-scaled prior count only for the finite descriptive effect
    # and its model-based SE. The LRT above uses the original counts.
    exposure = np.exp(offset)
    stabilized = counts + 0.5 * exposure / exposure.mean()
    mu = _nb_fitted_mean(stabilized, design, alpha, offset)
    if mu is None:
        return _Estimate(alpha=alpha, reason="nb_boundary_prior_count_nonconvergence")
    contrast = np.eye(design.size)[[1]]
    effect = np.linalg.lstsq(matrix, np.log(mu) - offset, rcond=None)[0][1]
    variance = design.contrast_covariance((mu / (1 + alpha * mu))[None, :], contrast)[
        0, 0, 0
    ]
    if not math.isfinite(variance) or variance <= 0:
        return _Estimate(
            alpha=alpha, reason="nb_boundary_prior_count_covariance_unavailable"
        )
    return _Estimate(
        alpha,
        float(effect / math.log(2)),
        math.sqrt(variance) / math.log(2),
        max(math.ulp(0.0), float(chi2.sf(statistic, 1))),
    )


def _fit_estimates(
    counts: _Counts,
    centers: tuple[str, ...],
    matrix: _Floats,
    offset: _Floats,
    dispersions: dict[str, dict],
    filtered: dict[str, str],
) -> tuple[list[_Estimate], list[str]]:
    design = DenseDesign(matrix)
    contrast = np.zeros((1, matrix.shape[1]))
    contrast[0, 1] = 1
    treated = matrix[:, 1] == 1
    estimates: list[_Estimate] = []
    for row, center in enumerate(centers):
        entry = dispersions.get(center, {})
        alpha = entry.get("dispersion_alpha")
        if center in filtered:
            estimates.append(_Estimate(reason=filtered[center]))
        elif not entry.get("dispersion_converged") or (
            alpha is None or not math.isfinite(alpha) or alpha < 1e-7
        ):
            estimates.append(_Estimate(reason="dispersion_unavailable_or_nonconverged"))
        elif counts[row, treated].any() and counts[row, ~treated].any():
            estimates.append(_Estimate(alpha))
        else:
            estimates.append(_nb_boundary_contrast(counts[row], design, offset, alpha))

    eligible = [
        row
        for row, estimate in enumerate(estimates)
        if estimate.reason is None and estimate.probability is None
    ]
    with warnings.catch_warnings(record=True) as observed:
        warnings.simplefilter("always")
        batch_size = min(512, max(1, 131_072 // len(matrix)))
        for start in range(0, len(eligible), batch_size):
            selected = eligible[start : start + batch_size]
            alphas = np.asarray([estimates[row].alpha for row in selected], dtype=float)
            batch = fit_statsmodels_nb(design, counts[selected], alphas, offset)
            for batch_row, source_row in enumerate(selected):
                estimates[source_row] = _test_estimate(
                    batch, batch_row, float(alphas[batch_row]), contrast
                )
    return estimates, list(dict.fromkeys(str(entry.message) for entry in observed))


def _statistics(
    coordinates: _Floats,
    ids: tuple[str, ...],
    neighborhoods: _Neighborhoods,
    estimates: list[_Estimate],
) -> tuple[NeighborhoodDAStatistic, ...]:
    probabilities = [estimate.probability for estimate in estimates]
    q_values = adjust_probabilities(probabilities)
    radii = neighborhoods.radii
    # A common rescaling of 1/d_k leaves the weighted BH formula unchanged.
    spatial = _spatial_fdr(probabilities, radii.min() / radii)
    return tuple(
        NeighborhoodDAStatistic(
            center_cell_id=ids[center],
            center_coordinates=tuple(float(value) for value in coordinates[center]),
            membership_sha256=canonical_sha256(sorted(ids[index] for index in members)),
            kth_neighbor_distance=float(distance),
            dispersion_alpha=estimate.alpha,
            log2_fold_change=estimate.effect,
            log2_standard_error=estimate.stderr,
            p_value=estimate.probability,
            q_value=q_value,
            spatial_fdr=spatial_fdr,
            missing_reason=estimate.reason,
        )
        for center, members, distance, estimate, q_value, spatial_fdr in zip(
            neighborhoods.centers,
            neighborhoods.members,
            radii,
            estimates,
            q_values,
            spatial,
            strict=True,
        )
    )


def fit_neighborhood_da(
    embedding_path: Path,
    metadata_path: Path,
    spec: NeighborhoodDASpec,
) -> NeighborhoodDAReceipt:
    """Fit on CSV inputs with exactly matched unique cell IDs.

    Embedding columns after removing `cell_id` are finite coordinates. Metadata
    contains `cell_id`, `donor_id`, `condition` and any declared technical
    factors. A neighborhood contains its center and its k nearest other cells.
    Only donor-level integer neighborhood memberships enter the NB count model.
    """
    spec = NeighborhoodDASpec.model_validate(spec.model_dump())
    embedding = indexed_rows(
        read_solver_csv(embedding_path, spec.embedding_sha256), "cell_id"
    ).sort_index()
    metadata = indexed_rows(
        read_solver_csv(metadata_path, spec.metadata_sha256), "cell_id"
    )
    if set(embedding.index) != set(metadata.index):
        raise ValueError("neighborhood_metadata_requires_exact_embedding_cell_ids")

    coordinates = numeric_matrix(embedding)
    ids = tuple(embedding.index)
    metadata = metadata.loc[list(ids)]
    donors, matrix, diagnostics = donor_design(metadata, spec.contrast)
    # DenseDesign caches every cross-product for each donor.
    if len(donors) * matrix.shape[1] ** 2 > MAX_MATRIX_ENTRIES:
        raise ValueError("neighborhood_donor_design_cross_products_over_budget")
    design = pd.DataFrame(
        matrix, index=pd.Index(donors), columns=diagnostics["model_terms"]
    )
    donor_lookup = {donor: index for index, donor in enumerate(donors)}
    donor_indexes = np.asarray(
        [donor_lookup[donor] for donor in metadata["donor_id"]], dtype=np.intp
    )
    neighborhoods = _graph(coordinates, donor_indexes, len(donors), spec)
    center_ids = tuple(ids[center] for center in neighborhoods.centers)

    (
        offset,
        normalization,
        dispersion,
        filtered,
        calibration_diagnostics,
        cal_warnings,
    ) = _calibrate(
        neighborhoods.counts,
        center_ids,
        design,
        spec.seed,
        np.bincount(donor_indexes, minlength=len(donors)),
    )
    estimates, fit_warnings = _fit_estimates(
        neighborhoods.counts,
        center_ids,
        matrix,
        offset,
        dispersion["gene_estimates"],
        filtered,
    )
    dispersion["gene_estimates_sha256"] = content_sha256(
        dispersion.pop("gene_estimates")
    )
    rows = _statistics(coordinates, ids, neighborhoods, estimates)
    receipt = NeighborhoodDAReceipt(
        receipt_sha256="0" * 64,
        specification_sha256=canonical_sha256(spec.model_dump(mode="json")),
        specification=spec,
        status="completed"
        if any(row.p_value is not None for row in rows)
        else "no_estimable_neighborhoods",
        n_cells=len(ids),
        coordinate_columns=tuple(embedding.columns),
        donor_ids=donors,
        donors_per_condition=diagnostics["donors_per_condition"],
        design_diagnostics=diagnostics,
        count_matrix_sha256=hashlib.sha256(
            neighborhoods.counts.astype("<i8", copy=False)
        ).hexdigest(),
        normalization=normalization,
        dispersion_calibration=dispersion,
        dispersion_diagnostics=tuple(calibration_diagnostics),
        warnings=tuple(dict.fromkeys([*cal_warnings, *fit_warnings])),
        neighborhoods=rows,
        limitations=(
            "Each neighborhood contains its center and k nearest other cells.",
            "Refinement snaps each sampled neighbor median to the nearest vertex "
            "and deduplicates the centers.",
            "The count hash uses row-major little-endian signed int64 counts.",
            "TMM uses total cells per donor as library sizes and assumes stable "
            "or balanced neighborhoods.",
            "Relative abundance estimates condition on the supplied embedding.",
            f"Two-sided {NB_NORMAL_WALD_REFERENCE} when both arms have counts. "
            "An all-zero arm uses a conditional NB likelihood-ratio test on observed "
            "counts and a library-scaled 0.5 prior count for the finite log2 effect "
            "and standard error.",
            "Probability underflow uses the smallest positive float.",
            "Failed tests contribute p=1 to correction; statistics remain null.",
        ),
    )
    return receipt.model_copy(
        update={
            "receipt_sha256": content_sha256(
                receipt.model_dump(mode="json", exclude={"receipt_sha256"})
            ),
        }
    )
