"""Differential within-gene feature usage with a stageR confirmation procedure."""

import math
import warnings
from pathlib import Path
from typing import Any, Literal

import numpy as np
import pandas as pd
from numpy.typing import NDArray
from pydantic import Field
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


class _GroupedBinomialGLM(GLM):
    """Include trial sizes in the Pearson scale for two-column binomial endog."""

    def _estimate_x2_scale(self, mu: _Floats) -> float:
        # statsmodels 0.14 includes n_trials in pearson_chi2, but omits
        # it from its generic scale="X2" for the two-column response.
        squared_error = np.square(self.endog - mu)
        pearson = squared_error * self.iweights * self.n_trials
        return float(np.sum(pearson / self.family.variance(mu)) / self.df_resid)


def _usage_test(successes: _Floats, totals: _Floats, matrix: _Floats) -> dict[str, Any]:
    row: dict[str, Any] = {
        "tx_p": None,
        "tx_q": None,
        "p_within_shaffer": None,
        "log_odds_ratio": None,
        "pearson_dispersion": None,
        "status": "unestimable",
    }
    if not successes.any() or not (totals > successes).any():
        return {**row, "missing_reason": "feature_or_complement_absent"}
    try:
        with warnings.catch_warnings():
            warnings.filterwarnings("error", category=PerfectSeparationWarning)
            warnings.filterwarnings("error", category=RuntimeWarning)
            fit = _GroupedBinomialGLM(
                np.column_stack((successes, totals - successes)),
                matrix,
                family=Binomial(),
            ).fit(scale="X2", use_t=True)
        p_value, effect, scale = map(float, (fit.pvalues[1], fit.params[1], fit.scale))
    except (
        ValueError,
        ArithmeticError,
        np.linalg.LinAlgError,
        RuntimeWarning,
        PerfectSeparationError,
        PerfectSeparationWarning,
    ):
        return {**row, "missing_reason": "quasi_binomial_fit_failed"}
    if not (
        fit.converged
        and 0 <= p_value <= 1
        and math.isfinite(effect)
        and math.isfinite(scale)
        and scale > np.finfo(float).eps
    ):
        return {**row, "missing_reason": "quasi_binomial_inference_unavailable"}
    return {
        **row,
        "tx_p": max(math.ulp(0.0), p_value),
        "log_odds_ratio": effect,
        "pearson_dispersion": scale,
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
        "transcript_statistics": rows,
        "status": "not_screened" if gene_p is not None else "unestimable",
        "design_diagnostics": diagnostics,
    }


def _stagewise(genes: list[dict[str, Any]], alpha: float) -> tuple[int, int]:
    tested = [gene for gene in genes if gene["gene_p"] is not None]
    for gene, q_value in zip(
        tested, adjust_probabilities([gene["gene_p"] for gene in tested]), strict=True
    ):
        gene["gene_q"] = q_value
    screened = [gene for gene in tested if gene["gene_q"] <= alpha]
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
                1.0, max(gene["gene_q"], within * len(tested) / len(screened))
            )
            row.update(
                p_within_shaffer=within,
                tx_q=q_value,
                status="confirmed" if q_value <= alpha else "not_confirmed",
            )
    return len(tested), len(screened)


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
            "screened_genes": screened,
            "design_diagnostics": diagnostics,
            "gene_statistics": statistics,
            "warnings": observed_warnings,
            "method": (
                "Grouped quasi-binomial GLM with Pearson X2 scale and residual-df t "
                "inference. Gene screening uses Bonferroni min-p followed by BH. "
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
