"""Full-family negative-binomial contrasts and multiplicity adjustment."""

import math

from .family_fitting import (
    LOW_COUNT_FILTER_CODE,
    NB_RESIDUAL_DF_REFERENCE,
    _fit_family,
    conditional_nb_contrasts,
)
from .observed_data import ObservedData


def _contrast_estimates(fitted, vector):
    import numpy as np

    return (
        float(vector @ np.asarray(fitted.params) / math.log(2)),
        float(vector @ np.asarray(fitted.cov_params()) @ vector / math.log(2) ** 2),
        fitted.df_resid,
    )


def _fit_population(specification, design, matrix, counts, diagnostics, genes):
    import numpy as np
    import pandas as pd

    fits, failures = {}, {}
    global_failure = None
    vectors = [np.asarray(contrast["vector"]) for contrast in design["contrasts"]]
    if design["identifiable"]:
        if counts is None:
            raise ValueError(
                "An identifiable population contrast requires a pseudobulk count panel"
            )
        metadata = pd.DataFrame(design["profiles"]).set_index("profile_id")
        metadata[specification["biological_unit_column"]] = metadata["biological_unit"]
        matrix.attrs["bulk_plot_metadata"] = {
            "format": "devbench.bulk-plot-design/1",
            "condition_column": "population",
            "block_columns": [specification["biological_unit_column"]],
        }
        data = ObservedData(
            expression=counts,
            gene_ids=tuple(genes),
            sample_ids=tuple(metadata.index),
            metadata=metadata,
            annotations={},
            measurement="raw_counts",
            unit_hierarchy={
                "population_unit": "embryo",
                "population_unit_label": specification["biological_unit_column"],
                "specimen_label": "profile_id",
                "library_label": "profile_id",
                "identity_status": "declared",
            },
            targets=(),
            provenance={"source_sha256": specification["source_sha256"]},
        )
        try:

            def project(fitted):
                projected = conditional_nb_contrasts(fitted, vectors)
                return (
                    projected
                    if projected is not None
                    else tuple(
                        _contrast_estimates(fitted, vector) for vector in vectors
                    )
                )

            fits, failures = _fit_family(data, matrix, diagnostics, projection=project)
            diagnostics["count_test_reference"] = NB_RESIDUAL_DF_REFERENCE
            diagnostics["independent_count_filter"]["multiplicity_scope"] = (
                "complete_gene_stage_family_retained"
            )
        except (
            ValueError,
            ArithmeticError,
            RuntimeError,
            np.linalg.LinAlgError,
        ) as error:
            global_failure = str(error).split(":", 1)[0][:200]
            diagnostics["analysis_error"] = global_failure
    return fits, failures, global_failure


def _statistics(source, specification, design, matrix, counts, diagnostics):
    genes = list(source.var.index)
    fits, failures, global_failure = _fit_population(
        specification, design, matrix, counts, diagnostics, genes,
    )
    rows, estimated = [], []
    for gene_index, gene in enumerate(genes):
        fitted = fits.get(gene)
        all_zero = design["identifiable"] and not counts[gene_index].any()
        for index, contrast in enumerate(design["contrasts"]):
            row = {
                "gene_id": gene,
                "stage": contrast["stage"],
                "contrast_id": contrast["contrast_id"],
                "estimate_status": "failed",
                "log2_fold_change": None,
                "standard_error": None,
                "confidence_interval_lower": None,
                "confidence_interval_upper": None,
                "p_value": None,
                "q_value": None,
                "failure_code": None,
            }
            if not design["identifiable"] or not contrast["supported"]:
                row.update(
                    estimate_status="refused",
                    failure_code=(
                        contrast["refusal_code"] or ";".join(design["refusal_codes"])
                    ),
                )
            elif global_failure is None and all_zero:
                row.update(
                    estimate_status="not_estimable",
                    failure_code="all_zero_selected_profiles",
                )
            elif failures.get(gene) == LOW_COUNT_FILTER_CODE:
                row.update(
                    estimate_status="not_estimable", failure_code=LOW_COUNT_FILTER_CODE
                )
            elif fitted is None:
                row["failure_code"] = global_failure or failures.get(
                    gene, "gene_fit_unavailable"
                )
            else:
                effect, variance, residual_df = fitted[index]
                if (
                    not math.isfinite(effect)
                    or not math.isfinite(variance)
                    or variance <= 0
                ):
                    row["failure_code"] = "nonfinite_or_nonpositive_contrast_variance"
                elif not math.isfinite(residual_df) or residual_df <= 0:
                    row["failure_code"] = "insufficient_residual_degrees_of_freedom"
                else:
                    standard_error = math.sqrt(variance)
                    row.update(
                        estimate_status="estimated",
                        log2_fold_change=effect,
                        standard_error=standard_error,
                    )
                    estimated.append((len(rows), effect, standard_error, residual_df))
            rows.append(row)
    if estimated:
        _finish_inference(rows, estimated)
    diagnostics["gene_fit_failures"] = failures
    return rows


def _finish_inference(rows, estimated):
    import numpy as np
    from scipy.stats import t
    from statsmodels.stats.multitest import multipletests

    indices, effects, errors, degrees = zip(*estimated, strict=True)
    effects, errors = np.asarray(effects), np.asarray(errors)
    probabilities = 2 * t.sf(np.abs(effects / errors), degrees)
    widths = t.ppf(0.975, degrees) * errors
    family = np.ones(len(rows))
    index = np.asarray(indices)
    family[index] = probabilities
    adjusted = multipletests(family, method="fdr_bh")[1][index]
    for position, lower, upper, p_value, q_value in zip(
        indices,
        effects - widths,
        effects + widths,
        probabilities,
        adjusted,
        strict=True,
    ):
        rows[position].update(
            confidence_interval_lower=float(lower),
            confidence_interval_upper=float(upper),
            p_value=float(p_value),
            q_value=max(float(p_value), min(1.0, float(q_value))),
        )
