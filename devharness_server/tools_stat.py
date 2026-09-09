"""Bounded temporal GLMs and rank-aware diagnostics in supervised spawned workers.

Count inference uses PyDESeq2 dispersion calibration and RLE offsets. Continuous
data use Gaussian natural splines. AIC/BIC use maximized Gaussian likelihood
(including the variance parameter); GCV uses n*RSS/(n-rank(X))**2. Selected-model
Wald p-values are conditional, exploratory tests. The opt-in Gaussian Monte
Carlo mode repeats the fixed selection rule under independent homoskedastic
Gaussian errors; it does not establish causal or clinical effects. See the
returned provenance and inference_scope.
"""
import ast
import importlib.metadata
import re
import warnings
from numbers import Integral
from pathlib import Path
from typing import List, Optional

import numpy as np
import pandas as pd

from devharness_core.stages import parse_stages
from devharness_core.storage import ChunkedExpressionStore
from devharness_core.watchdog import run_sandboxed


def _metadata(path: str, time_col: str = "embryonic_age", categorical_columns=()) -> pd.DataFrame:
    metadata = pd.read_csv(Path(path).expanduser().resolve(),
                           dtype={name: str for name in ("sample_id", "specimen_id", "subject_id", "donor_id", *categorical_columns)})
    if time_col in metadata:
        metadata["numeric_time"] = parse_stages(metadata[time_col])
    elif "numeric_time" in metadata:
        metadata["numeric_time"] = parse_stages(metadata["numeric_time"])
    return metadata


def _diagnostics(design: np.ndarray, columns: List[str]) -> dict:
    from scipy.linalg import qr, svdvals
    if design.size == 0 or not np.isfinite(design).all():
        raise ValueError("Design must be nonempty and finite")
    _, r, pivots = qr(design, mode="economic", pivoting=True)
    singular = svdvals(design)
    tolerance = np.finfo(float).eps * max(design.shape) * singular[0]
    rank = int(np.sum(np.abs(np.diag(r)) > tolerance))
    full_rank = rank == design.shape[1]
    return {"is_full_rank": full_rank, "rank": rank, "num_parameters": design.shape[1],
            "condition_number": float(singular[0] / singular[-1]) if full_rank else None,
            "aliased_columns": [columns[int(i)] for i in pivots[rank:]]}


def _safe_formula(formula, metadata_columns):
    """Allow Patsy operators and a small expression grammar, never arbitrary Python.

Factors may contain metadata names, constants, simple arithmetic and calls to
C/cr/bs/I/Q. Attribute/subscript access, imports, lambdas, comprehensions and
other calls are rejected before Patsy evaluates anything. This is an input
allowlist; the spawned process itself is resource isolation, not a security VM.
    """
    from patsy import ModelDesc

    if not isinstance(formula, str) or not formula.strip() or len(formula) > 4096:
        raise ValueError("unsafe_formula: Formula must be a nonempty string of at most 4096 characters")
    functions = {"C", "cr", "bs", "I", "Q"}
    columns = set(metadata_columns)
    allowed = (ast.Expression, ast.BinOp, ast.UnaryOp, ast.Call, ast.Name, ast.Load,
               ast.Constant, ast.keyword, ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow,
               ast.Mod, ast.UAdd, ast.USub)
    try:
        description = ModelDesc.from_formula(formula)
        if description.lhs_termlist:
            raise ValueError("Response terms are not supported in a design formula")
        for term in description.rhs_termlist:
            for factor in term.factors:
                tree = ast.parse(factor.code, mode="eval")
                for node in ast.walk(tree):
                    if not isinstance(node, allowed):
                        raise ValueError("Unsupported formula syntax: " + type(node).__name__)
                    if isinstance(node, ast.Name) and ("__" in node.id or node.id not in columns | functions):
                        raise ValueError("Unknown or forbidden formula name: " + node.id)
                    if isinstance(node, ast.keyword) and (node.arg is None or "__" in node.arg):
                        raise ValueError("Expanded or private keyword arguments are forbidden")
                    if isinstance(node, ast.Call):
                        if not isinstance(node.func, ast.Name) or node.func.id not in functions:
                            raise ValueError("Only C/cr/bs/I/Q function calls are permitted")
                        if node.func.id == "Q" and (len(node.args) != 1 or node.keywords
                                or not isinstance(node.args[0], ast.Constant) or not isinstance(node.args[0].value, str)
                                or node.args[0].value not in columns or "__" in node.args[0].value):
                            raise ValueError("Q must name one existing metadata column")
    except (ValueError, SyntaxError) as error:
        raise ValueError("unsafe_formula: " + str(error)) from error


def _design_matrix(formula, metadata):
    from patsy import dmatrix

    _safe_formula(formula, metadata.columns)
    return dmatrix(formula, metadata, return_type="dataframe", NA_action="raise", eval_env=-1)


def _validate_worker(path: str, formula: str, time_col: str = "embryonic_age") -> dict:
    design = _design_matrix(formula, _metadata(path, time_col))
    return _diagnostics(np.asarray(design), list(design.columns))


def validate_design_matrix(metadata_csv_path: str, formula_str: str, time_col: str = "embryonic_age") -> dict:
    """Check a safe formula's rank and aliases, converting the selected time column."""
    return run_sandboxed(_validate_worker, (str(Path(metadata_csv_path).expanduser().resolve()), formula_str, time_col))


def bh_adjust(p_values: List[float]) -> List[float]:
    """Benjamini-Hochberg adjustment across exactly the submitted gene family."""
    p = np.asarray(p_values, dtype=float)
    if p.ndim != 1 or not np.isfinite(p).all() or np.any((p < 0) | (p > 1)):
        raise ValueError("p-values must be finite values in [0, 1]")
    if not len(p):
        return []
    order = np.argsort(p, kind="stable")
    adjusted = np.minimum.accumulate((p[order] * len(p) / np.arange(1, len(p) + 1))[::-1])[::-1]
    result = np.empty_like(p)
    result[order] = np.clip(adjusted, 0, 1)
    return np.maximum(result, p).tolist()


def _inference_configuration(mode, draws, seed):
    if not isinstance(mode, str) or mode not in {"conditional", "gaussian_selection_mc"}:
        raise ValueError("inference_mode must be conditional or gaussian_selection_mc")
    for value, name, low, high in ((draws, "monte_carlo_draws", 1, 1_000_000),
                                   (seed, "monte_carlo_seed", 0, 2**64-1)):
        if (isinstance(value, (bool, np.bool_)) or not isinstance(value, Integral)
                or not low <= value <= high):
            raise ValueError(f"{name} must be a nonboolean integer between {low} and {high}")
    if mode == "conditional" and (draws != 65535 or seed != 0):
        raise ValueError("Nondefault Monte Carlo controls require gaussian_selection_mc inference_mode")
    return int(draws), int(seed)


def _gaussian_residual_operators(designs):
    """Compile numerical-information checks without simulating a reference."""
    from devharness_server.selection_mc import _residual_operator

    operators = {}
    for df, (_, frame, _) in designs.items():
        matrix = np.asarray(frame, dtype=float)
        scale = float(np.max(np.abs(matrix)))
        if not np.isfinite(scale) or scale <= 0:
            raise ValueError("Gaussian design has no usable residual information")
        scaled = matrix / scale
        smallest = float(np.linalg.svd(scaled, compute_uv=False)[-1])
        if not np.isfinite(smallest) or smallest <= 0:
            raise ValueError("Gaussian design has no usable residual information")
        operators[df] = _residual_operator(scaled, smallest)
    return operators


def _spline_formula(formula: str, df: int = 3) -> str:
    """Replace only the temporal natural spline, preserving nuisance covariates."""
    if "numeric_time" not in formula:
        raise ValueError("formula_str must contain numeric_time for the temporal test")
    marker = "__DEVHARNESS_TIME_SPLINE__"
    formula = re.sub(r"cr\(\s*numeric_time\s*(?:,[^()]*)?\)", marker, formula)
    formula = re.sub(r"\bnumeric_time\b", marker, formula)
    return formula.replace(marker, f"cr(numeric_time, df={df}, constraints='center')")


def _aligned_metadata(store, metadata_path, time_col, categorical_columns=()):
    metadata = _metadata(metadata_path, time_col, categorical_columns)
    if "sample_id" not in metadata or metadata["sample_id"].isna().any() or metadata["sample_id"].duplicated().any():
        raise ValueError("Metadata requires unique nonmissing sample_id values")
    if "numeric_time" not in metadata or metadata["numeric_time"].nunique() < 3:
        raise ValueError("Natural splines require at least three distinct times")
    samples = list(store.sample_names)
    metadata["sample_id"] = metadata["sample_id"].astype(str)
    if set(samples) != set(metadata["sample_id"]):
        raise ValueError("Expression and metadata sample IDs must match exactly")
    return metadata.set_index("sample_id").loc[samples]


def _candidate_designs(metadata, formula, dfs):
    _safe_formula(formula, metadata.columns)
    feasible, rejected = {}, []
    for df in dfs:
        transformed = _spline_formula(formula, df)
        record = {"df": df, "formula": transformed, "status": "infeasible"}
        if df >= metadata["numeric_time"].nunique():
            rejected.append(dict(record, reason="insufficient_distinct_times"))
            continue
        design = _design_matrix(transformed, metadata)
        diagnostics = _diagnostics(np.asarray(design), list(design.columns))
        if not diagnostics["is_full_rank"]:
            rejected.append(dict(record, reason="rank_deficient", design=diagnostics))
        elif len(design) <= design.shape[1]:
            rejected.append(dict(record, reason="insufficient_residual_degrees_of_freedom", design=diagnostics))
        else:
            feasible[df] = (transformed, design, diagnostics)
    if not feasible:
        if any(record["reason"] == "rank_deficient" for record in rejected):
            raise ValueError("Rank-deficient design; validate_design_matrix and diagnose confounding before removing covariates")
        raise ValueError("Insufficient residual degrees of freedom or distinct times for any requested spline")
    return feasible, rejected


def _fit_candidate(values, design, alpha, offset):
    import statsmodels.api as sm

    family = sm.families.NegativeBinomial(alpha=alpha) if alpha is not None else sm.families.Gaussian()
    with warnings.catch_warnings(record=True) as recorded:
        warnings.simplefilter("always")
        fitted = sm.GLM(values, design, family=family, offset=offset).fit(maxiter=200, tol=1e-8)
    if not fitted.converged:
        raise ValueError("glm_nonconvergence: GLM did not converge")
    if not np.isfinite(np.asarray(fitted.params)).all() or not np.isfinite(np.asarray(fitted.cov_params())).all():
        raise ValueError("glm_nonfinite: Nonfinite GLM coefficients or covariance")
    n, p = design.shape
    rss = float(np.sum((values - fitted.fittedvalues) ** 2))
    if alpha is None:
        if rss <= np.finfo(float).tiny:
            raise ValueError("zero_residual_variance: Gaussian inference requires nonzero residual variance")
        ll = -n / 2 * (np.log(2 * np.pi) + 1 + np.log(rss / n))
        parameters = p + 1  # Include the estimated Gaussian variance parameter.
        gcv = n * rss / (n - p) ** 2
    else:
        ll, parameters, gcv = float(fitted.llf), p + 1, None
    scores = {"aic": float(-2 * ll + 2 * parameters), "bic": float(-2 * ll + np.log(n) * parameters),
              "gcv": float(gcv) if gcv is not None else None, "log_likelihood": float(ll),
              "num_parameters": parameters}
    if not np.isfinite([scores["aic"], scores["bic"], ll]).all():
        raise ValueError("model_score_nonfinite: Nonfinite model selection score")
    return fitted, scores, list(dict.fromkeys(str(w.message) for w in recorded))


def _effect_estimate(fitted, design, metadata, discrete):
    from patsy import build_design_matrices

    comparison = metadata.iloc[[0, 0]].copy()
    comparison["numeric_time"] = [metadata["numeric_time"].min(), metadata["numeric_time"].max()]
    prediction_design = np.asarray(build_design_matrices([design.design_info], comparison)[0])
    linear = prediction_design @ np.asarray(fitted.params)
    predicted = np.exp(linear) if discrete else linear
    contrast = prediction_design[1] - prediction_design[0]
    variance = float(contrast @ np.asarray(fitted.cov_params()) @ contrast)
    if not np.isfinite(predicted).all() or not np.isfinite(variance) or variance < -1e-10:
        raise ValueError("contrast_nonfinite: Endpoint estimates or covariance are invalid")
    log2fc = float((linear[1] - linear[0]) / np.log(2)) if discrete else (
        float((np.log(predicted[1]) - np.log(predicted[0])) / np.log(2)) if np.all(predicted > 0) else None)
    difference = float(predicted[1] - predicted[0])
    if not np.isfinite(difference) or (log2fc is not None and not np.isfinite(log2fc)):
        raise ValueError("nonfinite_statistical_output: Endpoint effect estimate overflowed")
    return {"predicted_start": float(predicted[0]), "predicted_end": float(predicted[1]),
            "effect_difference": difference, "log2_fold_change": log2fc,
            "endpoint_contrast_se": float(np.sqrt(max(variance, 0))),
            "effect_scale": "log_mean" if discrete else "expression_units",
            "reference_batch": str(metadata.iloc[0]["batch"]) if "batch" in metadata else None,
            "contrast_note": "First sample nuisance covariates held fixed at both endpoints; count library factor=1",
            "time_start": float(comparison["numeric_time"].iloc[0]), "time_end": float(comparison["numeric_time"].iloc[1])}


def _replication_diagnostics(metadata):
    stage_replicates = metadata.groupby("numeric_time").size()
    diagnostics = []
    if int(stage_replicates.min()) < 2:
        diagnostics.append({"code": "insufficient_stage_replicates", "severity": "warning",
                            "message": "Some time points have fewer than two independent observations"})
    if "batch" in metadata and int(metadata.groupby(["numeric_time", "batch"]).size().min()) < 2:
        diagnostics.append({"code": "insufficient_batch_time_replicates", "severity": "warning",
                            "message": "Some observed batch-time cells have fewer than two observations"})
    return diagnostics, {str(float(time)): int(n) for time, n in stage_replicates.items()}


def _biological_unit_diagnostics(metadata):
    """Check every declared unit level; unique specimens cannot erase repeated subjects."""
    columns = [name for name in ("specimen_id", "subject_id", "donor_id") if name in metadata]
    repeated, incomplete, unique_counts = [], [], {}
    for name in columns:
        values = metadata[name].astype("string").str.strip()
        observed = values[values.notna() & ~values.str.lower().isin(["", "unknown", "unverified", "not_available", "n/a"])]
        unique_counts[name] = int(observed.nunique())
        if observed.duplicated().any():
            repeated.append(name)
        if len(observed) < len(metadata):
            incomplete.append(name)
    if "specimen_provenance" in metadata and metadata["specimen_provenance"].astype(str).str.contains("unknown|unverified", case=False).any():
        incomplete.append("specimen_provenance")
    assumption = {"assumed_unit": "one independent biological unit per expression column",
                  "declared_unit_columns": columns, "declared_unique_unit_counts": unique_counts,
                  "repeated_unit_columns": repeated, "incomplete_unit_columns": incomplete,
                  "verification_status": "repeated_units_detected" if repeated else
                  "unknown_unit_provenance" if not columns or incomplete else "declared_unique_units",
                  "limitation": "Unique identifiers alone do not establish statistical independence; no clustered or longitudinal covariance model is fitted"}
    diagnostics = []
    if repeated:
        diagnostics.append({"code": "pseudoreplication_requires_aggregation_or_repeated_measures_model",
                            "severity": "error", "unit_columns": repeated,
                            "message": "Repeated biological units declared in " + ", ".join(repeated) +
                            "; preaggregate according to the biological sampling design or use an appropriate externally validated repeated-measures model. This tool does not provide clustered longitudinal inference"})
    elif not columns or incomplete:
        diagnostics.append({"code": "unknown_biological_unit_provenance", "severity": "warning",
                            "message": "Biological-unit provenance is absent or incomplete; expression columns are assumed independent, and that assumption has not been verified"})
    return assumption, diagnostics


def _fit_worker(counts_path: str, metadata_path: str, formula: str, time_col: str, genes: List[str],
                selection_criterion="aic", spline_dfs=None, data_type="auto", calibration_max_genes=1000,
                strict=True, categorical_columns=(), inference_mode="conditional",
                monte_carlo_draws=65535, monte_carlo_seed=0) -> dict:
    from devharness_server.normalization import calibration_panel, calibrate_counts, expression_summary

    if not genes or len(set(genes)) != len(genes) or len(genes) > 100:
        raise ValueError("Request between 1 and 100 distinct genes to bound tool output")
    if selection_criterion not in {"aic", "bic", "gcv", "fixed"}:
        raise ValueError("selection_criterion must be aic, bic, gcv, or fixed")
    monte_carlo_draws, monte_carlo_seed = _inference_configuration(
        inference_mode, monte_carlo_draws, monte_carlo_seed)
    if inference_mode == "gaussian_selection_mc" and selection_criterion == "fixed":
        raise ValueError("fixed criterion uses conditional analytic inference; choose aic, bic, or gcv for selection MC")
    if selection_criterion == "fixed" and spline_dfs is None:
        match = re.search(r"cr\(\s*numeric_time\s*,\s*df\s*=\s*(\d+)", formula)
        spline_dfs = [int(match.group(1)) if match else 3]
    dfs = [2, 3, 4, 5] if spline_dfs is None else spline_dfs
    if (not dfs or any(isinstance(df, bool) or not isinstance(df, int) or df not in {2, 3, 4, 5} for df in dfs)
            or len(set(dfs)) != len(dfs)):
        raise ValueError("spline_dfs must contain distinct integers from 2, 3, 4, 5")
    if selection_criterion == "fixed" and len(dfs) != 1:
        raise ValueError("fixed selection requires exactly one spline df")
    parameters = {"formula_str": formula, "time_col": time_col, "gene_list": genes,
                  "statistical_method_version": "temporal_glm/2_gaussian_F",
                  "selection_criterion": selection_criterion, "spline_dfs": sorted(dfs),
                  "data_type": data_type, "calibration_max_genes": calibration_max_genes, "strict": strict,
                  "glm_maxiter": 200, "glm_tol": 1e-8}
    if categorical_columns:
        parameters["categorical_columns"] = list(categorical_columns)
    if inference_mode == "gaussian_selection_mc":
        parameters.update(statistical_method_version="temporal_glm/3_gaussian_selection_mc",
                          inference_mode=inference_mode, monte_carlo_draws=monte_carlo_draws,
                          monte_carlo_seed=monte_carlo_seed)
    with ChunkedExpressionStore(counts_path) as store:
        metadata = _aligned_metadata(store, metadata_path, time_col, categorical_columns)
        independence, unit_diagnostics = _biological_unit_diagnostics(metadata)
        if independence["repeated_unit_columns"]:
            failure = unit_diagnostics[0]
            if strict:
                raise ValueError(failure["code"] + ": " + failure["message"])
            return {"status": "failed", "failure_code": failure["code"], "model_fitting_performed": False,
                    "family": None, "formula": formula, "formula_scope": "not_fitted", "design": None,
                    "genes": {gene: {"status": "failed", "failure_code": failure["code"], "p_value": None,
                                      "padj": None, "q_value": None, "selected_df": None, "candidate_scores": [],
                                      "coefficients": {}, "dispersion_alpha": None, "diagnostics": unit_diagnostics}
                              for gene in genes},
                    "fdr_scope": "requested_genes", "fdr_adjustment_performed": False,
                    "selected_df": {gene: None for gene in genes}, "selection_criterion": selection_criterion,
                    "spline_dfs": sorted(dfs), "normalization_method": "not_performed",
                    "normalization": {"method": "not_performed"}, "size_factors": None,
                    "dispersion_calibration": None, "expression_diagnostics": None,
                    "independence_assumption": independence, "diagnostics": unit_diagnostics,
                    "warnings": [failure["message"]],
                    "inference_scope": "No inference performed. " + failure["message"],
                    "provenance": {"parameters": parameters,
                                   "library_versions": {name: importlib.metadata.version(name) for name in ("numpy", "pandas")}}}
        designs, rejected = _candidate_designs(metadata, formula, sorted(dfs))
        summary = expression_summary(store, data_type)
        discrete = summary["data_type"] == "counts"
        if discrete and inference_mode == "gaussian_selection_mc":
            raise ValueError("gaussian_selection_mc requires continuous Gaussian inputs; counts are unsupported")
        if discrete and selection_criterion == "gcv":
            raise ValueError("GCV is only implemented for continuous Gaussian expression; use aic or bic for counts")
        selection_engine, gaussian_operators = None, {}
        if not discrete:
            if inference_mode == "gaussian_selection_mc":
                from devharness_server.selection_mc import GaussianSelectionMC
                selection_engine = GaussianSelectionMC(designs, selection_criterion,
                                                       monte_carlo_draws, monte_carlo_seed)
            else:
                gaussian_operators = _gaussian_residual_operators(designs)
        diagnostics, replicates = _replication_diagnostics(metadata)
        diagnostics.extend(unit_diagnostics)
        notices = []
        factors, normalization, calibration = None, {"method": "none", "scope": "continuous_input_unchanged"}, None
        if discrete:
            values, panel = calibration_panel(store, genes, calibration_max_genes)
            # A common richest feasible design limits residual biological signal
            # entering dispersion estimation; fixed alphas then make candidate
            # likelihoods comparable. Its identity is returned explicitly.
            calibration_df = max(designs)
            calibration_formula, calibration_design, _ = designs[calibration_df]
            factors, normalization, calibration, calibration_diagnostics, calibration_warnings = calibrate_counts(
                values, panel, metadata, calibration_design, strict)
            calibration.update(formula=calibration_formula, spline_df=calibration_df)
            diagnostics.extend(calibration_diagnostics)
            notices.extend(calibration_warnings)
            if panel["panel_gene_count"] < panel["total_gene_count"]:
                notices.append("Normalization and dispersion calibration use a bounded gene panel; file ordering can bias panel representativeness")
            if normalization["reference_gene_count"] < 20:
                diagnostics.append({"code": "small_normalization_reference", "severity": "warning",
                                    "message": "Fewer than 20 reference genes; RLE factors may absorb real coordinated changes"})
            if panel["panel_gene_count"] < 20:
                diagnostics.append({"code": "small_dispersion_panel", "severity": "warning",
                                    "message": "Fewer than 20 calibration genes; the empirical dispersion prior may be unstable"})
            if normalization["method"] == "median_of_ratios_poscounts":
                notices.append("No calibration gene is positive in every sample; the disclosed DESeq2 positive-count RLE variant was used")
            gene_values = {gene: values[i] for i, gene in enumerate(panel["gene_ids"]) if gene in genes}
        else:
            values, panel = calibration_panel(store, genes, len(genes))
            gene_values = dict(zip(panel["gene_ids"], values))
        results = {}
        for gene in genes:
            y = np.asarray(gene_values[gene], dtype=float)
            mc_result = None
            dispersion = (calibration["gene_estimates"][gene] if discrete else
                          {"genewise_dispersion": None, "dispersion_alpha": None, "trend_dispersion": None,
                           "dispersion_outlier": False, "dispersion_converged": None})
            if discrete and not dispersion["dispersion_converged"]:
                results[gene] = dict(dispersion, status="failed", failure_code="dispersion_fit_failed",
                                     candidate_scores=[], p_value=None, padj=None, selected_df=None,
                                     diagnostics=[{"code": "dispersion_fit_failed", "severity": "error"}])
                continue
            per_gene_diagnostics = []
            if np.count_nonzero(y) < max(3, len(y) // 5):
                per_gene_diagnostics.append({"code": "high_dropout", "severity": "warning",
                                             "message": "Fewer than 20% (or fewer than three) observations are nonzero"})
            candidates = [dict(record) for record in rejected]
            fitted_candidates = {}
            no_information = np.all(y == 0) or (not discrete and np.ptp(y) == 0)
            if no_information:
                df = min(designs)
                transformed, design, design_diagnostics = designs[df]
                coefficients = np.zeros(design.shape[1])
                if "Intercept" in design:
                    coefficients[list(design.columns).index("Intercept")] = np.log(1e-12) if discrete else float(y[0])
                for candidate_df in designs:
                    candidates.append({"df": candidate_df, "status": "no_information", "reason": "constant_expression"})
                selected = {"coefficients": dict(zip(design.columns, coefficients.tolist())), "wald_statistic": 0.0,
                            "p_value": 1.0, "status": "no_information", "predicted_start": float(y[0]),
                            "predicted_end": float(y[0]), "effect_difference": 0.0,
                            "log2_fold_change": None if y[0] == 0 else 0.0,
                            "test_statistic": None, "test_distribution": "not_tested",
                            "test_df_num": None, "test_df_denom": None}
                per_gene_diagnostics.append({"code": "no_expression_information", "severity": "warning",
                                             "message": "Constant continuous or all-zero count expression has no temporal test information"})
            else:
                if selection_engine is not None:
                    try:
                        mc_result = selection_engine.evaluate(y)
                    except (ValueError, np.linalg.LinAlgError, FloatingPointError) as error:
                        raise ValueError(f"selection_calibration_failed: {gene}: {error}") from error
                for candidate_df, (transformed, design, design_diagnostics) in designs.items():
                    try:
                        if candidate_df in gaussian_operators:
                            from devharness_server.selection_mc import _resolved_rss
                            with np.errstate(over="raise", invalid="raise", divide="raise", under="ignore"):
                                _resolved_rss(y[None, :], gaussian_operators[candidate_df],
                                              f"Gaussian candidate df={candidate_df}")
                        fitted, scores, fit_warnings = _fit_candidate(y, design, dispersion["dispersion_alpha"],
                                                                    np.log(factors) if discrete else None)
                        if mc_result is not None:
                            scores = mc_result["candidate_scores"][candidate_df]
                        fitted_candidates[candidate_df] = fitted
                        candidates.append(dict(scores, df=candidate_df, formula=transformed, status="ok", warnings=fit_warnings))
                    except (ValueError, np.linalg.LinAlgError, FloatingPointError) as error:
                        candidates.append({"df": candidate_df, "formula": transformed, "status": "failed",
                                           "error_type": type(error).__name__, "reason": str(error)})
                        diagnostics.append({"code": "candidate_fit_failed", "severity": "error", "gene_id": gene,
                                            "df": candidate_df, "message": str(error)})
                        if strict or selection_engine is not None:
                            raise ValueError(f"candidate_fit_failed: {gene}, df={candidate_df}: {error}") from error
                eligible = [record for record in candidates if record["status"] == "ok"]
                if not eligible:
                    results[gene] = dict(dispersion, status="failed", failure_code="all_candidate_fits_failed",
                                         candidate_scores=sorted(candidates, key=lambda record: record["df"]),
                                         p_value=None, padj=None, selected_df=None, diagnostics=per_gene_diagnostics)
                    diagnostics.append({"code": "all_candidate_fits_failed", "severity": "error", "gene_id": gene})
                    continue
                selected_record = min(eligible, key=lambda record: (record[selection_criterion] if selection_criterion != "fixed" else 0, record["df"]))
                df = selected_record["df"]
                if mc_result is not None and df != mc_result["selected_df"]:
                    raise ValueError("selection_calibration_failed: candidate selection disagrees with reference kernel")
                fitted = fitted_candidates[df]
                transformed, design, design_diagnostics = designs[df]
                temporal = [i for term, sl in design.design_info.term_name_slices.items()
                            if "numeric_time" in term for i in range(sl.start, sl.stop)]
                with warnings.catch_warnings(record=True) as wald_warnings:
                    warnings.simplefilter("always")
                    # Gaussian variance is estimated from the same samples.
                    # Conditional on a prespecified design and independent,
                    # homoskedastic normal errors, use the finite-sample F
                    # reference, not the anti-conservative chi-square limit.
                    # NB inference remains asymptotic with fitted dispersions.
                    wald = fitted.wald_test(np.eye(design.shape[1])[temporal], use_f=not discrete, scalar=True)
                test_statistic, p_value = float(wald.statistic), float(wald.pvalue)
                statistic = test_statistic if discrete else test_statistic * len(temporal)
                if not np.isfinite([statistic, test_statistic, p_value]).all():
                    raise ValueError(f"wald_nonfinite: Nonfinite Wald test for gene {gene}")
                if mc_result is not None and (
                    not np.isclose(test_statistic, mc_result["conditional_f_statistic"], rtol=1e-8, atol=1e-10)
                    or not np.isclose(p_value, mc_result["conditional_p_value"], rtol=1e-8, atol=1e-10)
                ):
                    raise ValueError(f"selection_calibration_failed: fitted and projection F calculations differ for {gene}")
                selected = {"coefficients": dict(zip(design.columns, np.asarray(fitted.params).tolist())),
                            "wald_statistic": statistic, "p_value": p_value, "status": "ok",
                            "test_statistic": test_statistic,
                            "test_distribution": "chi_square" if discrete else "F",
                            "test_df_num": len(temporal),
                            "test_df_denom": None if discrete else float(fitted.df_resid),
                            "warnings": list(dict.fromkeys(selected_record["warnings"] + [str(w.message) for w in wald_warnings]))}
                selected.update(_effect_estimate(fitted, design, metadata, discrete))
                if mc_result is not None:
                    selected.update(
                        p_value=mc_result["p_value"], test_distribution="selection_monte_carlo",
                        test_statistic=mc_result["conditional_p_value"], test_tail="lower",
                        test_df_num=None, test_df_denom=None,
                        conditional_p_value=mc_result["conditional_p_value"],
                        conditional_f_statistic=mc_result["conditional_f_statistic"],
                        conditional_df_num=mc_result["conditional_df_num"],
                        conditional_df_denom=mc_result["conditional_df_denom"],
                        conditional_test_distribution="F",
                        wald_statistic=mc_result["conditional_f_statistic"] * mc_result["conditional_df_num"],
                        wald_statistic_scope="Conditional selected-model F quadratic; not the Monte Carlo rank statistic",
                        endpoint_inference_scope="Selected-model estimates and conditional SEs; no selection-adjusted interval",
                    )
            temporal_count = sum(sl.stop - sl.start for term, sl in design.design_info.term_name_slices.items() if "numeric_time" in term)
            if len(design) - design.shape[1] <= 3:
                per_gene_diagnostics.append({"code": "low_residual_df", "severity": "warning",
                                             "message": "At most three residual degrees of freedom; asymptotic inference is unstable"})
            results[gene] = dict(selected, **dispersion, selected_df=df, formula=transformed, design=design_diagnostics,
                                 degrees_of_freedom=temporal_count, residual_df=len(design) - design.shape[1],
                                 candidate_scores=sorted(candidates, key=lambda record: record["df"]),
                                 zero_fraction=float(np.mean(y == 0)), nonzero_observations=int(np.count_nonzero(y)),
                                 diagnostics=per_gene_diagnostics)
        # Failed genes remain explicitly failed. A unit p-value occupies their
        # denominator position only, retaining the original requested family.
        adjusted = bh_adjust([result["p_value"] if result["p_value"] is not None else 1.0 for result in results.values()])
        for result, q in zip(results.values(), adjusted):
            result["padj"] = q if result["p_value"] is not None else None
            result["q_value"] = result["padj"]
        if selection_engine is not None:
            from scipy.stats import false_discovery_control
            by_values = false_discovery_control(
                [result["p_value"] if result["p_value"] is not None else 1.0 for result in results.values()],
                method="by",
            )
            for result, q in zip(results.values(), by_values):
                result["by_q_value"] = float(q) if result["p_value"] is not None else None
        selected_dfs = {gene: result["selected_df"] for gene, result in results.items()}
        distinct_dfs = {df for df in selected_dfs.values() if df is not None}
        top_df = next(iter(distinct_dfs)) if len(distinct_dfs) == 1 else max(designs)
        top_formula, _, top_design = designs[top_df]
        if selection_engine is None and selection_criterion != "fixed" and len(dfs) > 1:
            notices.append("Temporal p-values condition on data-selected spline complexity; they are not adjusted for model selection")
        if not discrete and selection_engine is None:
            notices.append("Gaussian F tests assume independent homoskedastic normal errors; finite-sample calibration requires a prespecified design")
        notices.extend(record["message"] for record in diagnostics if "message" in record)
        inference_scope = ("Observational associations in the submitted cohort; no causal batch-adjusted effect is identified by removing a confounded covariate. "
                           "Gaussian inference uses an F reference conditional on the design; NB Wald inference uses an asymptotic chi-square reference. "
                           "Inference conditions on spline selection and fitted dispersion/size factors; selection uncertainty is not included. "
                           "BH adjustment covers exactly the requested gene family, not the transcriptome. Independent observations and correctly specified covariates are assumed.")
        if selection_engine is not None:
            notices.extend([
                "Experimental selection calibration assumes independent homoskedastic Gaussian errors and fixed design/candidates/criterion/seed; unequal variance and correlated errors remain unqualified.",
                "Endpoint estimates and SEs remain conditional on the selected model; no selection-adjusted confidence interval is provided.",
                "BH values require their usual dependence conditions; separately named BY values are more conservative. Neither is a clinical FDR qualification.",
                f"Monte Carlo p-value resolution is 1/{monte_carlo_draws + 1}; probabilities below this floor are not estimated.",
            ])
            inference_scope = (
                "Experimental Gaussian Monte Carlo rank test, marginal over the complete fixed model-selection rule. "
                "Observed and reference statistics use the same Householder numerical kernel. "
                "Independent homoskedastic Gaussian errors, a fixed design/candidate set/criterion/seed and a prespecified gene family are assumed. "
                "The reference is shared within the requested family; BH dependence conditions are not automatically established. "
                "BH padj/q_value and separate Benjamini-Yekutieli by_q_value cover exactly the requested family. "
                "Conditional F diagnostics and endpoint estimates retain their conditional meaning. "
                "No heteroskedastic, correlated-error, negative-binomial, interval or clinical qualification is established."
            )
        versions = {name: importlib.metadata.version(name) for name in ("numpy", "pandas", "scipy", "statsmodels", "patsy")}
        if discrete:
            versions["pydeseq2"] = calibration["version"]
            calibration["gene_estimates"] = {gene: calibration["gene_estimates"][gene] for gene in genes}
            calibration["gene_estimates_scope"] = "requested_genes"
        output = {"family": "negative_binomial" if discrete else "gaussian", "formula": top_formula,
                "formula_scope": "common_selected_model" if len(distinct_dfs) == 1 else "richest_feasible_design; see per-gene selected formulas",
                "design": top_design, "genes": results, "fdr_scope": "requested_genes", "selected_df": selected_dfs,
                "selection_criterion": selection_criterion, "spline_dfs": sorted(dfs),
                "normalization_method": normalization["method"], "normalization": normalization,
                "size_factors": dict(zip(store.sample_names, factors.tolist())) if factors is not None else None,
                "dispersion_calibration": calibration, "expression_diagnostics": summary, "stage_replicates": replicates,
                "independence_assumption": independence,
                "warnings": list(dict.fromkeys(notices)), "diagnostics": diagnostics, "inference_scope": inference_scope,
                "status": "partial_failure" if any(record["severity"] == "error" for record in diagnostics) else "ok",
                "provenance": {"library_versions": versions, "parameters": parameters}}
        if selection_engine is not None:
            output["selection_adjustment"] = selection_engine.metadata()
            output["fdr_methods"] = {"padj": "benjamini_hochberg", "q_value": "benjamini_hochberg",
                                     "by_q_value": "benjamini_yekutieli"}
        return output


def fit_temporal_trajectory(counts_path: str, metadata_path: str, formula_str: str,
                            time_col: str, gene_list: List[str], selection_criterion: str = "aic",
                            spline_dfs: Optional[List[int]] = None, data_type: str = "auto",
                            calibration_max_genes: int = 1000, strict: bool = True,
                            inference_mode: str = "conditional", monte_carlo_draws: int = 65535,
                            monte_carlo_seed: int = 0) -> dict:
    """Fit temporal GLMs with bounded count calibration and per-gene df selection.

``data_type='auto'`` treats floating source data as continuous; ``counts`` is an
explicit assertion of raw count provenance, validated for integer values.
Use ``fixed`` with one spline df for prespecified inference. Default candidates
are 2,3,4,5; AIC/BIC apply to either family, GCV only to Gaussian data. Existing
five positional arguments and per-gene test/coefficient keys are preserved.
The returned warnings and inference_scope are part of the statistical result.
Repeated specimen_id/subject_id/donor_id values block inference: strict mode
raises, while non-strict mode returns failed genes with null p/q. Preaggregate
according to the experimental design or use an externally validated model for
repeated measures. No clustered or longitudinal covariance adjustment is made;
missing unit identifiers produce an explicit independence-assumption warning.

Experimental inference_mode='gaussian_selection_mc' calibrates the complete
AIC/BIC/GCV selection rule for continuous Gaussian data. Draws and seed must be
prespecified. Counts and fixed criterion are refused in this mode. It assumes
independent homoskedastic errors; unequal variance/correlation and clinical use
remain unqualified. Conditional F diagnostics are retained separately. BH q-values
remain named as before; additional by_q_value uses Benjamini-Yekutieli adjustment.
Selected-model endpoint SEs are not selection-adjusted intervals.
    """
    return run_sandboxed(_fit_worker, (str(Path(counts_path).expanduser().resolve()),
        str(Path(metadata_path).expanduser().resolve()), formula_str, time_col, gene_list,
        selection_criterion, spline_dfs, data_type, calibration_max_genes, strict, (),
        inference_mode, monte_carlo_draws, monte_carlo_seed))


def _batch_effects_worker(counts_path, metadata_path, time_col, gene_list):
    from devharness_server.normalization import calibration_panel, expression_summary, median_ratio_factors

    with ChunkedExpressionStore(counts_path) as store:
        metadata = _aligned_metadata(store, metadata_path, time_col)
        if "batch" not in metadata or metadata["batch"].isna().any():
            raise ValueError("Batch diagnostics require nonmissing batch metadata")
        genes = list(gene_list) if gene_list is not None else [store.gene_names[i] for i in np.linspace(0, store.shape[0] - 1, min(100, store.shape[0]), dtype=int)]
        if not genes or len(set(genes)) != len(genes) or len(genes) > 100:
            raise ValueError("Request between 1 and 100 distinct genes for batch diagnostics")
        summary = expression_summary(store)
        values, panel = calibration_panel(store, genes)
        factors, normalization = None, {"method": "none"}
        if summary["data_type"] == "counts":
            factors, normalization = median_ratio_factors(values, panel["gene_ids"])
            values = np.log1p(values / factors[None, :])
        n = len(metadata)
        df = min(3, metadata["numeric_time"].nunique() - 1)
        time_formula = _spline_formula("1 + numeric_time", df)
        full = _design_matrix(time_formula + " + C(batch)", metadata)
        time_only = _design_matrix(time_formula, metadata)
        batch_only = _design_matrix("1 + C(batch)", metadata)
        design_diagnostics = _diagnostics(np.asarray(full), list(full.columns))
        time_rank = _diagnostics(np.asarray(time_only), list(time_only.columns))["rank"]
        batch_rank = _diagnostics(np.asarray(batch_only), list(batch_only.columns))["rank"]
        full_rank = design_diagnostics["rank"]
        identifiable = design_diagnostics["is_full_rank"] and n > full_rank
        diagnostics, replicates = _replication_diagnostics(metadata)
        if not identifiable:
            diagnostics.append({"code": "confounded_or_saturated_batch_time_design", "severity": "error",
                                "message": "Batch and time partial effects are not jointly identifiable; partial R2 is not reported"})
        result = {}
        positions = {gene: i for i, gene in enumerate(panel["gene_ids"])}
        for gene in genes:
            y = np.asarray(values[positions[gene]], dtype=float)
            total = float(np.sum((y - y.mean()) ** 2))
            rss = {}
            for name, design in (("full", full), ("time", time_only), ("batch", batch_only)):
                coefficients = np.linalg.lstsq(np.asarray(design), y, rcond=None)[0]
                rss[name] = float(np.sum((y - np.asarray(design) @ coefficients) ** 2))
            if not np.isfinite(total) or not np.isfinite(list(rss.values())).all():
                raise ValueError(f"nonfinite_statistical_output: Batch variance calculation overflowed for gene {gene}")
            r_squared = float(np.clip(1 - rss["full"] / total, 0, 1)) if total > 0 else None
            def partial(reduced, increment):
                return (float(np.clip((rss[reduced] - rss["full"]) / rss[reduced], 0, 1))
                        if identifiable and increment > 0 and rss[reduced] > np.finfo(float).eps and total > 0 else None)
            result[gene] = {"full_r2": r_squared, "batch_partial_r2": partial("time", full_rank - time_rank),
                            "time_partial_r2": partial("batch", full_rank - batch_rank), "residual_df": n - full_rank,
                            "status": "ok" if identifiable and total > 0 else "not_identifiable" if not identifiable else "no_information"}
        return {"identifiable": bool(identifiable), "design": design_diagnostics, "formula": time_formula + " + C(batch)",
                "reduced_model_ranks": {"time_only": time_rank, "batch_only": batch_rank}, "genes": result,
                "transformation": "log1p_RLE_normalized_counts" if factors is not None else "none_continuous_input",
                "normalization": normalization, "size_factors": dict(zip(store.sample_names, factors.tolist())) if factors is not None else None,
                "gene_scope": "requested_genes" if gene_list is not None else "at_most_100_evenly_spaced_genes",
                "calibration_panel": panel, "diagnostics": diagnostics, "stage_replicates": replicates,
                "warnings": [record["message"] for record in diagnostics if "message" in record],
                "inference_scope": "Descriptive OLS partial R2=(RSS_reduced-RSS_full)/RSS_reduced on the stated expression scale; not a causal batch effect. Full rank and independent observations are required."}


def analyze_batch_effects(counts_path: str, metadata_path: str, time_col: str = "embryonic_age",
                          gene_list: Optional[List[str]] = None) -> dict:
    """Describe batch/time partial R2 with rank control in a supervised child.

Counts use log1p RLE-normalized expression; continuous values are unchanged.
Without gene_list, at most 100 evenly spaced genes are reported. Confounded or
saturated full designs return explicit non-identifiability and null partial R2.
    """
    return run_sandboxed(_batch_effects_worker, (str(Path(counts_path).expanduser().resolve()),
                         str(Path(metadata_path).expanduser().resolve()), time_col, gene_list))
