"""Differential within-gene feature usage with a stageR confirmation procedure."""

import math
import warnings
from pathlib import Path
from typing import Any, Literal

import numpy as np
import pandas as pd
from numpy.typing import NDArray
from pydantic import Field
from scipy.optimize import brentq
from scipy.stats import t
from statsmodels.genmod.families import Binomial
from statsmodels.genmod.generalized_linear_model import GLM
from statsmodels.tools.sm_exceptions import (
    PerfectSeparationError,
    PerfectSeparationWarning,
)

from ._rna_solver_inputs import (
    MAX_EXACT_COUNT,
    ConditionContrast,
    adjust_probabilities,
    donor_design,
    indexed_rows,
    numeric_matrix,
    raw_count_matrix,
    read_solver_csv,
    require_labels,
    solver_receipt,
)
from .design import require_design_condition
from .scientific_types import FrozenContract, Probability, Sha256

_Floats = NDArray[np.float64]


class BulkDTUSpec(FrozenContract):
    """Declare an exact two-group usage contrast and bind its input bytes."""

    format: Literal["devbench.bulk-dtu/1"] = "devbench.bulk-dtu/1"
    counts_sha256: Sha256
    metadata_sha256: Sha256
    mapping_sha256: Sha256
    effective_lengths_sha256: Sha256 | None = None
    measurement: Literal["transcript_counts", "exon_counts"] = "transcript_counts"
    count_scale: Literal["raw_counts", "scaledTPM"] = "raw_counts"
    contrast: ConditionContrast
    target_ofdr: Probability = Field(default=0.05, gt=0, lt=1)


def _usage_wald_df(
    matrix: _Floats,
    mu: _Floats,
    inverse_information: _Floats,
    variance_factor: _Floats,
    rho_derivative: _Floats,
    residual_df: int,
) -> float:
    """Moment-match contrast variance uncertainty to a scaled chi-squared."""
    weighted_design = matrix * np.sqrt(mu * (1 - mu) / variance_factor)[:, None]
    inverse_projection = weighted_design @ inverse_information
    leverage = np.sum(weighted_design * inverse_projection, axis=1)
    # The residual Pearson moment has variance 2 * residual_df in the
    # working GLM. Its slope in rho is the residual-leverage weighted
    # relative variance derivative. The contrast need not share that slope.
    relative_derivative = rho_derivative / variance_factor
    pearson_slope = (1 - leverage) @ relative_derivative
    contrast_weights = np.square(inverse_projection[:, 1]) / inverse_information[1, 1]
    contrast_slope = contrast_weights @ relative_derivative
    if contrast_slope <= 0:
        return float(residual_df)
    moment_df = (pearson_slope / contrast_slope) ** 2 / residual_df
    return float(min(residual_df, moment_df))


def _usage_test(successes: _Floats, totals: _Floats, matrix: _Floats) -> dict[str, Any]:
    row: dict[str, Any] = {
        "tx_p": None,
        "tx_q": None,
        "p_within_shaffer": None,
        "log_odds_ratio": None,
        "pearson_dispersion": None,
        "donor_intraclass_correlation": None,
        "wald_df": None,
        "status": "unestimable",
    }
    if not successes.any() or not (totals > successes).any():
        return {**row, "missing_reason": "feature_or_complement_absent"}
    usage = successes / totals
    # A positive fractional scaledTPM total below one is one effective trial.
    inverse_depth = 1 / np.maximum(totals, 1.0)
    rho_derivative = 1 - inverse_depth
    has_donor_variance_information = bool(rho_derivative.any())
    df = matrix.shape[0] - matrix.shape[1]
    if df <= 0:
        return {**row, "missing_reason": "insufficient_gene_residual_df"}

    def fit_at(rho: float):
        # Beta-binomial variance for a donor proportion:
        # mu * (1 - mu) * (rho + (1 - rho) / n).
        # Quasi-scores also admit the fractional counts from length adjustment.
        weights = 1 / (inverse_depth + rho * rho_derivative)
        fit = GLM(usage, matrix, family=Binomial(), var_weights=weights).fit(scale=1.0)
        if (
            not fit.converged
            or not np.isfinite(fit.mu).all()
            or np.any((fit.mu <= 0) | (fit.mu >= 1))
        ):
            raise ValueError("invalid_beta_binomial_quasi_likelihood_fit")
        return fit

    try:
        with warnings.catch_warnings():
            # IRLS also emits this warning for exactly fitted interior fractions.
            boundary = np.any((usage == 0) | (usage == 1))
            warnings.filterwarnings(
                "error" if boundary else "ignore", category=PerfectSeparationWarning
            )
            warnings.filterwarnings("error", category=RuntimeWarning)
            rho = 0.0
            fit = fit_at(rho)
            if has_donor_variance_information and fit.pearson_chi2 > df:
                upper_fit = fit_at(1.0)
                if upper_fit.pearson_chi2 >= df:
                    rho, fit = 1.0, upper_fit
                else:
                    rho = brentq(
                        lambda value: fit_at(value).pearson_chi2 - df,
                        0.0, 1.0, xtol=1e-12,
                    )
                    fit = fit_at(rho)
            # At the binomial boundary, underdispersion must not sharpen the test.
            scale = max(1.0, float(fit.pearson_chi2 / df))
            effect = float(fit.params[1])
            standard_error = float(fit.bse[1]) * math.sqrt(scale)
            if not math.isfinite(standard_error) or standard_error <= 0:
                raise ValueError("invalid_beta_binomial_standard_error")
            wald_df = _usage_wald_df(
                matrix, fit.mu, fit.normalized_cov_params,
                inverse_depth + rho * rho_derivative, rho_derivative, df,
            )
            p_value = float(2 * t.sf(abs(effect) / standard_error, wald_df))
    except (
        ValueError,
        ArithmeticError,
        np.linalg.LinAlgError,
        RuntimeWarning,
        PerfectSeparationError,
        PerfectSeparationWarning,
    ):
        return {**row, "missing_reason": "beta_binomial_quasi_likelihood_fit_failed"}
    if not (
        0 <= p_value <= 1
        and math.isfinite(effect)
        and math.isfinite(scale)
        and 0 < wald_df <= df
    ):
        return {**row, "missing_reason": "beta_binomial_inference_unavailable"}
    return {
        **row,
        "tx_p": max(math.ulp(0.0), p_value),
        "log_odds_ratio": effect,
        "pearson_dispersion": scale,
        "donor_intraclass_correlation": rho if has_donor_variance_information else None,
        "wald_df": wald_df,
        "status": "not_screened",
    }


def _gene_usage(
    gene: str,
    features: list[str],
    counts: _Floats,
    matrix: _Floats,
    samples: pd.DataFrame,
    sample_counts: _Floats,
    lengths: _Floats | None,
    arms: tuple[str, str],
) -> dict[str, Any]:
    totals = counts.sum(axis=1)
    keep = totals > 0
    support = {
        arm: int(np.count_nonzero(keep & (matrix[:, 1] == group)))
        for group, arm in enumerate(arms)
    }
    result: dict[str, Any] = {
        "gene_id": gene,
        "gene_p": None,
        "gene_q": None,
        "status": "unestimable",
        "screening_eligible": False,
        "biological_units_per_group": support,
        "zero_total_samples_dropped": samples.index[
            sample_counts.sum(axis=1) == 0
        ].tolist(),
        "transcript_statistics": [],
    }
    if len(features) == 1:
        return {**result, "status": "single_isoform_gene"}
    if min(support.values()) < 2:
        return {**result, "missing_reason": "insufficient_units_after_zero_total_drop"}
    model = matrix[keep]
    try:
        diagnostics = require_design_condition(model)
    except ValueError as error:
        return {**result, "missing_reason": str(error)}
    if diagnostics["residual_df"] <= 0:
        return {**result, "missing_reason": "insufficient_gene_residual_df"}
    counts, totals = counts[keep], totals[keep]
    if lengths is not None:
        abundance = counts * (lengths.min() / lengths)
        counts = (abundance / abundance.sum(axis=1, keepdims=True)) * totals[:, None]
        counts = np.minimum(counts, totals[:, None])
    rows = [
        {"feature_id": feature, **_usage_test(counts[:, i], totals, model)}
        for i, feature in enumerate(features)
    ]
    probabilities = [row["tx_p"] for row in rows if row["tx_p"] is not None]
    # Bonferroni min-p tests the global null without assuming independent
    # transcript tests. A two-feature gene has one unique (complementary) test.
    multiplier = 1 if len(features) == 2 else len(features)
    gene_p = min(1.0, multiplier * min(probabilities)) if probabilities else None
    return {
        **result,
        "gene_p": gene_p,
        "screening_eligible": True,
        "transcript_statistics": rows,
        "status": "not_screened" if gene_p is not None else "unestimable",
        "design_diagnostics": diagnostics,
    }


def _stagewise(genes: list[dict[str, Any]], alpha: float) -> tuple[int, int]:
    family = [
        gene for gene in genes
        if gene["gene_p"] is not None or gene.get("screening_eligible", False)
    ]
    for gene, q_value in zip(
        family, adjust_probabilities([gene["gene_p"] for gene in family]), strict=True
    ):
        gene["gene_q"] = q_value
    screened = [
        gene for gene in family
        if gene["gene_q"] is not None and gene["gene_q"] <= alpha
    ]
    for gene in screened:
        gene["status"] = "screened"
        rows = sorted(
            gene["transcript_statistics"],
            key=lambda row: row["tx_p"] if row["tx_p"] is not None else 1.0,
        )
        within = 0.0
        for rank, row in enumerate(rows):
            p_value = row["tx_p"]
            if p_value is None:
                continue
            multiplier = min(len(rows) - 1, len(rows) - rank)
            within = min(1.0, max(within, multiplier * p_value))
            q_value = min(
                1.0, max(gene["gene_q"], within * len(family) / len(screened))
            )
            row.update(
                p_within_shaffer=within,
                tx_q=q_value,
                status="confirmed" if q_value <= alpha else "not_confirmed",
            )
    return sum(gene["gene_p"] is not None for gene in family), len(screened)


def _lengths(
    path: Path | None, spec: BulkDTUSpec, role: str, features: pd.Index
) -> _Floats | None:
    if path is None and spec.effective_lengths_sha256 is None:
        return None
    if path is None or spec.effective_lengths_sha256 is None:
        raise ValueError("effective_lengths_path_and_digest_required_together")
    if spec.count_scale != "raw_counts":
        raise ValueError("scaledTPM_cannot_be_length_adjusted_twice")
    frame = indexed_rows(read_solver_csv(path, spec.effective_lengths_sha256), role)
    if set(frame.columns) != {"effective_length"} or set(frame.index) != set(features):
        raise ValueError("effective_lengths_must_cover_the_exact_feature_axis")
    lengths = numeric_matrix(frame.loc[features]).ravel()
    if np.any(lengths < 1e-6) or np.any(lengths > 1e12):
        raise ValueError("effective_lengths_outside_positive_numerical_range")
    return lengths


def _read_mapping(
    path: Path | None, spec: BulkDTUSpec, columns: pd.Index
) -> tuple[dict, str]:
    if path is None:
        raise ValueError("transcript_or_exon_mapping_required")
    role = "transcript_id" if spec.measurement == "transcript_counts" else "exon_id"
    try:
        mapping = indexed_rows(read_solver_csv(path, spec.mapping_sha256), role)
    except FileNotFoundError as error:
        raise ValueError("transcript_or_exon_mapping_required") from error
    require_labels(mapping, ("gene_id",))
    if set(mapping.index) != set(columns):
        raise ValueError("mapping_must_cover_the_exact_feature_axis")
    groups = mapping.groupby("gene_id", sort=True).groups
    if all(len(features) == 1 for features in groups.values()):
        raise ValueError("multi_feature_gene_required_for_usage")
    return groups, role


def _usage_counts(
    frame: pd.DataFrame,
    metadata: pd.DataFrame,
    scale: Literal["raw_counts", "scaledTPM"],
) -> _Floats:
    if scale == "scaledTPM":
        values = numeric_matrix(frame)
        if np.any((values < 0) | (values > MAX_EXACT_COUNT)):
            raise ValueError("usage_requires_bounded_nonnegative_counts")
        return values
    counts = raw_count_matrix(frame)
    totals = counts.sum(axis=1)
    for indices in metadata.groupby("donor_id").indices.values():
        if sum(int(value) for value in totals[indices]) > MAX_EXACT_COUNT:
            raise ValueError("pooled_donor_counts_exceed_exact_integer_range")
    return counts


def fit_bulk_dtu(
    counts_path: Path,
    metadata_path: Path,
    mapping_path: Path | None,
    spec: BulkDTUSpec,
    *,
    effective_lengths_path: Path | None = None,
) -> dict[str, Any]:
    """Fit usage on digest-pinned sample-by-feature counts and a complete map.

    Metadata uses sample_id, donor_id, condition, and optional batch and other
    declared categorical covariates. Same-profile donor libraries are summed.
    Exon input requires disjoint bins and yields exon-usage evidence.
    """
    spec = BulkDTUSpec.model_validate(spec.model_dump())
    frame = indexed_rows(read_solver_csv(counts_path, spec.counts_sha256), "sample_id")
    groups, role = _read_mapping(mapping_path, spec, frame.columns)
    samples = indexed_rows(
        read_solver_csv(metadata_path, spec.metadata_sha256), "sample_id"
    )
    if set(samples.index) != set(frame.index):
        raise ValueError("counts_and_metadata_sample_ids_must_match")
    samples = samples.loc[frame.index]
    contrast = spec.contrast
    units, design, diagnostics = donor_design(samples, contrast, min_units_per_group=2)
    counts = _usage_counts(frame, samples, spec.count_scale)
    lengths = _lengths(effective_lengths_path, spec, role, frame.columns)
    pooled = np.zeros((len(units), counts.shape[1]))
    np.add.at(pooled, pd.Index(units).get_indexer(samples["donor_id"]), counts)
    statistics = []
    for gene, features in groups.items():
        columns = frame.columns.get_indexer(features)
        statistics.append(
            _gene_usage(
                str(gene),
                list(features),
                pooled[:, columns],
                design,
                samples,
                counts[:, columns],
                lengths[columns] if lengths is not None else None,
                (contrast.denominator, contrast.numerator),
            )
        )
    tested, screened = _stagewise(statistics, spec.target_ofdr)
    observed_warnings = []
    if spec.count_scale == "raw_counts" and lengths is None:
        observed_warnings.append(
            "Raw unscaled counts may reflect transcript length bias."
        )
    if spec.measurement == "exon_counts":
        observed_warnings.append(
            "Disjoint exon bins test exon usage. "
            "Isoform identities require transcript data."
        )
    return solver_receipt(
        "devbench.bulk-dtu-result/1",
        spec,
        {
            "counts": spec.counts_sha256,
            "metadata": spec.metadata_sha256,
            "mapping": spec.mapping_sha256,
            "effective_lengths": spec.effective_lengths_sha256,
        },
        {
            "status": "completed" if tested else "no_estimable_genes",
            "claim_scope": "differential_within_gene_feature_usage",
            "target_ofdr": spec.target_ofdr,
            "tested_genes": tested,
            "bh_family_size": sum(gene["screening_eligible"] for gene in statistics),
            "screened_genes": screened,
            "design_diagnostics": diagnostics,
            "gene_statistics": statistics,
            "warnings": observed_warnings,
            "method": (
                "Donor beta-binomial quasi-likelihood logistic regression with "
                "Pearson moment intraclass correlation and a Wald t test with "
                "moment-matched contrast degrees of freedom capped by donor residual "
                "degrees of freedom. Gene screening uses Bonferroni min-p followed "
                "by BH with eligible failed genes padded at 1. "
                "Shaffer confirmation produces alpha-specific tx_q values for "
                "isoform usage changes distinct from total gene differential "
                "expression."
            ),
            "length_handling": (
                "inverse_effective_length_within_gene_rescaled_to_original_gene_total"
                if lengths is not None
                else spec.count_scale
            ),
        },
    )
