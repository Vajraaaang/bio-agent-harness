"""Shared count and continuous gene-family fitting."""

import re

from .bulk_fit_evidence import capture_bulk_plot_evidence
from .design import DESIGN_CONDITION_NUMBER_LIMIT, require_design_condition
from .estimators import FittedGene, StatisticalEstimator, StatsmodelsEstimator
from .observed_data import PUBLIC_MAX_BULK_FEATURES

_DEFAULT_ESTIMATOR = StatsmodelsEstimator()
LOW_COUNT_FILTER_CODE = "independent_low_count_filter"
NB_RESIDUAL_DF_REFERENCE = (
    "approximate_t_or_F_conditional_on_fitted_dispersion_and_offsets"
)
NB_NORMAL_WALD_REFERENCE = "normal_Wald_conditional_on_fitted_dispersion_and_offsets"


def _fit_family(
    data,
    design,
    diagnostics,
    *,
    estimator: StatisticalEstimator | None = None,
    _fastq_paired_wald: bool = False,
    projection=None,
):
    import numpy as np

    from rna_kernels.units import validate_expression_selection

    default_estimator = estimator is None
    if default_estimator:
        estimator = _DEFAULT_ESTIMATOR
    assert estimator is not None
    counts = data.measurement != "continuous"
    diagnostics["design_condition"] = require_design_condition(
        design,
        condition_number_limit=design.attrs.get(
            "condition_number_limit", DESIGN_CONDITION_NUMBER_LIMIT
        ),
    )
    if diagnostics["design_condition"]["residual_df"] <= 0:
        raise ValueError("insufficient_residual_degrees_of_freedom")
    diagnostics["model_family_attempted"] = (
        "negative_binomial" if counts else "gaussian"
    )
    if len(data.gene_ids) > PUBLIC_MAX_BULK_FEATURES:
        raise ValueError("complete_gene_family_exceeds_bounded_model_budget")
    selection = validate_expression_selection(
        data.context.get("selection_provenance"),
        data.metadata,
        biological_unit_column=data.unit_hierarchy.get("population_unit_label"),
    )
    if selection is not None:
        diagnostics["selection_provenance"] = selection
    if counts:
        factors, dispersions, failures, offset = _calibrate_family_counts(
            data, design, diagnostics, _fastq_paired_wald
        )
    else:
        factors = np.ones(len(data.metadata))
        dispersions, failures, offset = {}, {}, None
    batched_path = (
        counts
        and default_estimator
        and projection is not None
        and not _fastq_paired_wald
    )
    if batched_path:
        from .batched_nb_fit import iter_batched_nb_genes

        candidates = iter_batched_nb_genes(data, design, offset, dispersions, failures)
    else:
        candidates = (
            (gene, values, None)
            for gene, values in zip(data.gene_ids, data.expression, strict=True)
        )
    fits, batched_count = _fit_models(
        candidates,
        estimator,
        design,
        offset,
        diagnostics,
        dispersions,
        failures,
        projection,
    )
    if batched_path:
        diagnostics["conditional_nb_execution"] = {
            "batched_fits": batched_count,
            "scalar_fits": len(fits) - batched_count,
        }
    diagnostics["plot_evidence"] = capture_bulk_plot_evidence(
        data, factors, diagnostics.get("calibration", {}), design=design
    )
    return fits, failures


def _calibrate_family_counts(data, design, diagnostics, _fastq_paired_wald):
    import numpy as np

    from rna_kernels.normalization import (
        MAX_BLOCK_BYTES,
        MAX_PANEL_BYTES,
        MIN_NORMALIZED_MEAN_COUNT,
        calibrate_count_family,
        calibrate_counts,
        low_count_mask,
    )

    filtered = {}
    if data.expression.size * 8 <= MAX_PANEL_BYTES:
        panel = {
            "gene_ids": list(data.gene_ids),
            "panel_selection": "complete_declared_gene_family",
            "panel_gene_count": len(data.gene_ids),
            "total_gene_count": len(data.gene_ids),
        }
        (
            factors,
            normalization,
            calibration,
            calibration_diagnostics,
            warnings_list,
        ) = calibrate_counts(
            data.expression, panel, data.metadata, design, strict=False
        )
        if not _fastq_paired_wald:
            filtered = {
                gene: LOW_COUNT_FILTER_CODE
                for gene, low in zip(
                    data.gene_ids,
                    low_count_mask(data.expression, factors),
                    strict=True,
                )
                if low
            }
    elif _fastq_paired_wald:
        raise ValueError(
            "complete_gene_family_exceeds_empirical_bayes_panel_budget"
        )
    else:
        from rna_kernels.normalization import select_calibration_panel_indices
        from rna_kernels.streaming_rle import streaming_median_ratio_factors

        sample_count = len(data.sample_ids)
        rows_per_block = max(1, MAX_BLOCK_BYTES // (8 * sample_count))

        def count_blocks():
            for start in range(0, len(data.gene_ids), rows_per_block):
                yield data.expression[start : start + rows_per_block]

        factors, normalization = streaming_median_ratio_factors(
            count_blocks, sample_count
        )
        indices, panel = select_calibration_panel_indices(
            data.gene_ids, sample_count, (), 1000
        )
        panel_values = data.expression[indices]
        panel["panel_bytes"] = int(panel_values.nbytes)
        panel["panel_role"] = "diagnostic_dispersion_calibration"
        reference = calibrate_counts(
            panel_values,
            panel,
            data.metadata,
            design,
            strict=False,
            size_factors=factors,
        )
        del panel_values
        blocks = (
            (
                data.gene_ids[start : start + rows_per_block],
                data.expression[start : start + rows_per_block],
            )
            for start in range(0, len(data.gene_ids), rows_per_block)
        )
        calibration, calibration_diagnostics, warnings_list, filtered = (
            calibrate_count_family(
                blocks, reference, data.metadata, design, strict=False
            )
        )
    # A structural zero has no dispersion estimate. Retain its failed
    # hypothesis and BH position separately from the positive-count filter.
    zero_signal = {
        gene: "dispersion_unavailable_or_nonconverged"
        for gene, row in zip(data.gene_ids, data.expression, strict=True)
        if gene in filtered and not row.any()
    }
    for gene in zero_signal:
        del filtered[gene]
    if not _fastq_paired_wald:
        diagnostics["independent_count_filter"] = {
            "statistic": "across_observations_mean_of_RLE_normalized_counts",
            "minimum_normalized_mean_count": MIN_NORMALIZED_MEAN_COUNT,
            "threshold_origin": "fixed_method_default_before_contrast_testing",
            "uses_group_labels_or_test_results": False,
            "filtered_gene_ids": list(filtered),
            "structural_zero_count": len(zero_signal),
            "eligible_count": len(data.gene_ids) - len(filtered) - len(zero_signal),
        }
    # Retain the actual calibration vector supplied to this fit, on its
    # observation axis (which may already aggregate technical libraries).
    # Historical results without these fields must not be backfilled.
    normalization.update(
        per_sample_size_factors={
            sample: float(factor)
            for sample, factor in zip(data.sample_ids, factors, strict=True)
        },
        per_sample_library_sizes={
            sample: float(total)
            for sample, total in zip(
                data.sample_ids,
                data.expression.sum(axis=0, dtype=float),
                strict=True,
            )
        },
        observation_ids=list(data.sample_ids),
        observation_scope="fitted_observations",
        evidence_source="fresh_fit",
        gene_universe="complete_declared_gene_family",
        library_size_gene_count=len(data.gene_ids),
    )
    diagnostics.update(
        normalization=normalization,
        calibration=calibration,
        calibration_diagnostics=calibration_diagnostics,
        calibration_warnings=warnings_list,
    )
    dispersions = calibration["gene_estimates"]
    offset = np.log(factors)
    return factors, dispersions, {**zero_signal, **filtered}, offset


def _fit_models(
    candidates,
    estimator,
    design,
    offset,
    diagnostics,
    dispersions,
    failures,
    projection,
):
    import warnings

    import numpy as np

    fits, batched_count = {}, 0
    fitted: FittedGene
    for gene, values, batched in candidates:
        if gene in failures:
            continue
        if batched is not None and projection is not None:
            try:
                fits[gene] = projection(batched)
            except (ValueError, ArithmeticError, RuntimeError, np.linalg.LinAlgError):
                # Keep the scalar fit and its diagnostics at numerical boundaries.
                pass
            else:
                batched_count += 1
                continue
        try:
            with warnings.catch_warnings(record=True) as observed_warnings:
                warnings.simplefilter("always")
                fitted = _scalar_fit(
                    estimator, gene, values, design, offset, dispersions
                )
            if (
                not np.isfinite(np.asarray(fitted.params)).all()
                or not np.isfinite(np.asarray(fitted.cov_params())).all()
            ):
                raise ValueError("nonfinite_model_estimate")
            fits[gene] = fitted if projection is None else projection(fitted)
            if observed_warnings:
                diagnostics.setdefault("model_warnings", {})[gene] = list(
                    dict.fromkeys(str(x.message)[:500] for x in observed_warnings)
                )
        except (
            ValueError,
            ArithmeticError,
            RuntimeError,
            np.linalg.LinAlgError,
        ) as error:
            failures[gene] = _error_code(error)
    return fits, batched_count


def _scalar_fit(estimator, gene, values, design, offset, dispersions):
    if offset is None:
        return estimator.fit_gaussian(values, design)
    info = dispersions[gene]
    alpha = info.get("dispersion_alpha")
    if alpha is None or not info.get("dispersion_converged", False):
        raise ValueError("dispersion_unavailable_or_nonconverged")
    return estimator.fit_negative_binomial(values, design, alpha=alpha, offset=offset)


def _error_code(error):
    text = str(error).split(":", 1)[0]
    return (
        text
        if re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{0,159}", text)
        else "numerical_analysis_failed"
    )


def conditional_nb_contrasts(fitted, vectors):
    from .batched_nb_fit import conditional_nb_contrasts as project

    return project(fitted, vectors)
