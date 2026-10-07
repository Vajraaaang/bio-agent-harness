"""Versioned accepted-result contracts for fixed temporal GLM evidence.

This checks the complete exposed gene-fit structure and internal invariants.
It does not establish calibration, clinical validity or genuine host execution.
New numerical engines require their own versioned result contract.
"""
import math


GENE_FIELDS = set("""candidate_scores coefficients contrast_note degrees_of_freedom design diagnostics
dispersion_alpha dispersion_converged dispersion_outlier effect_difference effect_scale endpoint_contrast_se
formula genewise_dispersion log2_fold_change nonzero_observations p_value padj predicted_end predicted_start
q_value reference_batch residual_df selected_df status test_df_denom test_df_num test_distribution test_statistic
time_end time_start trend_dispersion wald_statistic warnings zero_fraction""".split())
ANALYSIS_FIELDS = set("""design diagnostics dispersion_calibration expression_diagnostics family fdr_scope formula
formula_scope genes independence_assumption inference_scope normalization normalization_method provenance
selected_df selection_criterion size_factors spline_dfs stage_replicates status warnings""".split())


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _number(value):
    return type(value) in (float, int) and math.isfinite(value)


def _close(left, right):
    return _number(left) and _number(right) and math.isclose(left, right, rel_tol=1e-9, abs_tol=1e-10)


def _notices(record):
    _require(type(record["warnings"]) is list and all(type(x) is str for x in record["warnings"]), "Invalid warning records")
    _require(type(record["diagnostics"]) is list, "Invalid diagnostic records")
    for diagnostic in record["diagnostics"]:
        _require(type(diagnostic) is dict and isinstance(diagnostic.get("code"), str)
                 and diagnostic.get("severity") in {"info", "warning"}, "Accepted analysis contains an error or malformed diagnostic")


def validate_accepted_result(plan, result, metadata_rows, formula):
    _require(type(result) is dict and set(result) == {"analysis", "input_validation"}, "Unexpected accepted-result structure")
    analysis = result["analysis"]
    method = analysis["provenance"]["parameters"].get("statistical_method_version")
    current = method == "temporal_glm/4_exact_terms_and_effect_scale"
    _require(current or method == "temporal_glm/2_gaussian_F", "Unsupported fixed temporal result version")
    _require(type(analysis) is dict and set(analysis) == ANALYSIS_FIELDS, "Unknown or missing analysis fields")
    _notices(analysis)
    samples = result["input_validation"]["sample_ids"]
    count = len(samples)
    parameter_count = analysis["design"]["num_parameters"]
    _require(type(parameter_count) is int and 0 < parameter_count < count, "Invalid design parameter count")
    _require(analysis["design"]["aliased_columns"] == [], "Accepted model has aliased columns")
    temporal = f"cr(numeric_time, df={plan.spline_df}, constraints='center')"
    columns = {"Intercept", *plan.continuous_covariates, *[f"{temporal}[{i}]" for i in range(plan.spline_df)]}
    for covariate in plan.categorical_covariates:
        levels = sorted({row[covariate] for row in metadata_rows})
        columns.update(f"C({covariate})[T.{level}]" for level in levels[1:])
    _require(len(columns) == parameter_count, "Coefficient dimension does not match approved covariates")
    times = [float(row[plan.time_column]) for row in metadata_rows]
    _require(all(_number(t) for t in times) and min(times) < max(times), "Invalid preserved numeric times")
    if current:
        import numpy as np
        import pandas as pd
        from patsy import dmatrix, build_design_matrices
        frame = pd.DataFrame(metadata_rows).set_index("sample_id").loc[samples].copy()
        frame["numeric_time"] = frame[plan.time_column].astype(float)
        for covariate in plan.continuous_covariates:
            frame[covariate] = frame[covariate].astype(float)
        design = dmatrix(formula, frame, return_type="dataframe")
        time_grid = sorted(set(times))
        prediction = frame.iloc[[0] * len(time_grid)].copy()
        prediction["numeric_time"] = time_grid
        prediction_design = np.asarray(build_design_matrices([design.design_info], prediction)[0])
    for gene in analysis["genes"].values():
        expected_fields = GENE_FIELDS | ({"time_grid", "predicted_curve"} if current else set())
        _require(type(gene) is dict and set(gene) == expected_fields, "Unknown or missing accepted gene fields")
        _notices(gene)
        coefficients = gene["coefficients"]
        _require(type(coefficients) is dict and set(coefficients) == columns
                 and all(_number(value) for value in coefficients.values()), "Invalid accepted coefficients")
        if current:
            curve = gene["predicted_curve"]
            _require(type(gene["time_grid"]) is list and gene["time_grid"] == time_grid
                     and type(curve) is list and len(curve) == len(time_grid) and all(_number(value) for value in curve),
                     "Invalid recorded prediction grid")
            predicted = prediction_design @ np.asarray([coefficients[name] for name in design.columns])
            if plan.data_type == "counts":
                predicted = np.exp(predicted)
            _require(all(_close(actual, float(expected)) for actual, expected in zip(curve, predicted))
                     and _close(curve[0], gene["predicted_start"]) and _close(curve[-1], gene["predicted_end"]),
                     "Recorded curve differs from accepted coefficients")
        for name in ("predicted_start", "predicted_end", "effect_difference", "endpoint_contrast_se", "time_start", "time_end", "wald_statistic"):
            _require(_number(gene[name]), "Invalid numeric gene field: " + name)
        _require(gene["endpoint_contrast_se"] >= 0 and gene["wald_statistic"] >= 0, "Negative uncertainty or Wald statistic")
        _require(_close(gene["effect_difference"], gene["predicted_end"]-gene["predicted_start"]), "Inconsistent endpoint difference")
        _require(gene["time_start"] == min(times) and gene["time_end"] == max(times), "Endpoint times differ from approved data")
        _require(gene["effect_scale"] == ("log_mean" if plan.data_type == "counts" else "expression_units"), "Wrong endpoint effect scale")
        _require(gene["reference_batch"] is None or type(gene["reference_batch"]) is str, "Invalid reference batch")
        _require(gene["contrast_note"] == "First sample nuisance covariates held fixed at both endpoints; count library factor=1", "Unsupported endpoint contrast")
        expected_wald = gene["test_statistic"]*(plan.spline_df if plan.data_type == "continuous" else 1)
        _require(_close(gene["wald_statistic"], expected_wald), "Inconsistent Wald statistic")
        start, end = gene["predicted_start"], gene["predicted_end"]
        if plan.data_type == "counts":
            _require(start > 0 and end > 0, "Nonpositive NB endpoint mean")
        if current and plan.data_type == "continuous":
            _require(gene["log2_fold_change"] is None, "Generic continuous expression has no abundance fold-change scale")
        elif start > 0 and end > 0:
            _require(_close(gene["log2_fold_change"], (math.log(end)-math.log(start))/math.log(2)), "Inconsistent endpoint fold change")
        else:
            _require(gene["log2_fold_change"] is None, "Undefined Gaussian log-fold-change")
        _require(type(gene["dispersion_outlier"]) is bool, "Invalid dispersion outlier flag")
        if plan.data_type == "counts":
            _require(gene["dispersion_converged"] is True, "Accepted dispersion did not converge")
            _require(all(_number(gene[k]) and gene[k] > 0 for k in ("dispersion_alpha", "genewise_dispersion", "trend_dispersion")),
                     "Invalid count dispersion estimates")
        else:
            _require(all(gene[k] is None for k in ("dispersion_converged", "dispersion_alpha", "genewise_dispersion", "trend_dispersion")),
                     "Unexpected Gaussian count-dispersion fields")
        for key in ("degrees_of_freedom", "residual_df", "selected_df", "test_df_num", "nonzero_observations"):
            _require(type(gene[key]) is int, "Invalid integer gene field: " + key)
        _require(0 <= gene["nonzero_observations"] <= count and _number(gene["zero_fraction"])
                 and _close(gene["zero_fraction"], 1-gene["nonzero_observations"]/count), "Inconsistent observation counts")
        _require(gene["degrees_of_freedom"] == plan.spline_df, "Changed temporal degrees of freedom")
        scores = gene["candidate_scores"]
        _require(type(scores) is list and len(scores) == 1, "Unapproved candidate model set")
        score = scores[0]
        _require(set(score) == {"aic", "bic", "df", "formula", "gcv", "log_likelihood", "num_parameters", "status", "warnings"}
                 and score["status"] == "ok" and score["df"] == plan.spline_df and score["formula"] == formula,
                 "Malformed or failed candidate model")
        _require(type(score["warnings"]) is list and all(type(w) is str for w in score["warnings"]), "Invalid candidate warnings")
        _require(type(score["num_parameters"]) is int and score["num_parameters"] == parameter_count+1, "Invalid candidate parameter count")
        _require(all(_number(score[k]) for k in ("aic", "bic", "log_likelihood")), "Invalid candidate scores")
        _require(_close(score["aic"], -2*score["log_likelihood"]+2*score["num_parameters"])
                 and _close(score["bic"], -2*score["log_likelihood"]+math.log(count)*score["num_parameters"]), "Inconsistent candidate information criteria")
        _require(score["gcv"] is None if plan.data_type == "counts" else _number(score["gcv"]) and score["gcv"] >= 0,
                 "Invalid candidate GCV")
    if plan.data_type == "continuous":
        _require(analysis["size_factors"] is None and analysis["dispersion_calibration"] is None, "Unapproved continuous normalization/calibration")
        return
    factors = analysis["size_factors"]
    _require(type(factors) is dict and set(factors) == set(samples)
             and all(_number(x) and x > 0 for x in factors.values()), "Invalid size factor values or sample coverage")
    _require(abs(sum(math.log(x) for x in factors.values())/count) < 1e-9, "Size factors are not centered as declared")
    normalization = analysis["normalization"]
    references = normalization["reference_gene_ids"]
    _require(type(references) is list and references and len(set(references)) == len(references)
             == normalization["reference_gene_count"] and set(references) <= set(plan.calibration_genes)
             and normalization["factor_centering"] == "geometric_mean_one" and normalization["scope"] == "calibration_panel",
             "Inconsistent normalization reference panel")
    calibration = analysis["dispersion_calibration"]
    _require(calibration["package"] == "pydeseq2" and calibration["version"] == analysis["provenance"]["library_versions"]["pydeseq2"]
             and calibration["method"] in {"pydeseq2_empirical_bayes", "pydeseq2_genewise_poisson_boundary"}
             and calibration["spline_df"] == plan.spline_df and calibration["formula"] == formula,
             "Unapproved calibration implementation/design")
    _require(set(calibration["design_columns"]) == columns and calibration["n_cpus"] == 1
             and calibration["low_memory"] is True and calibration["refit_cooks"] is False,
             "Unapproved calibration configuration")
    _require(set(calibration["gene_estimates"]) == set(plan.hypothesis_genes), "Incomplete reported dispersion family")
    for name, estimates in calibration["gene_estimates"].items():
        _require(set(estimates) == {"genewise_dispersion", "dispersion_alpha", "trend_dispersion", "dispersion_outlier", "dispersion_converged"}
                 and all(value == analysis["genes"][name][key] for key, value in estimates.items()), "Inconsistent per-gene dispersion evidence")
