"""Bounded temporal GLMs and rank-aware diagnostics in supervised spawned workers.

Count inference uses PyDESeq2 dispersion calibration and RLE offsets. Continuous
data use Gaussian natural splines. AIC/BIC use maximized Gaussian likelihood
(including the variance parameter); GCV uses n*RSS/(n-rank(X))**2. Selected-model
Wald p-values are conditional, exploratory tests. Open count fits use an
approximate residual-df F reference with plug-in dispersions. The opt-in Gaussian Monte
Carlo mode repeats the fixed selection rule under independent homoskedastic
Gaussian errors; it does not establish causal or clinical effects. See the
returned provenance and inference_scope.
"""
import ast
import importlib.metadata
import warnings
from collections.abc import Iterable
from numbers import Integral
from pathlib import Path
from typing import Any, List, Literal, NotRequired, Optional, TypedDict

import numpy as np
import pandas as pd

from devharness_core.design_terms import (
    specified_temporal_df,
    temporal_spline_formula,
)
from devharness_core.design_terms import (
    uses_temporal_variable as _uses_temporal_variable,
)
from devharness_core.stages import parse_stages
from devharness_core.storage import ChunkedExpressionStore
from devharness_core.units import (
    BIOLOGICAL_UNIT_COLUMNS,
    UNKNOWN_UNIT_MARKERS,
    validate_expression_selection,
)
from devharness_core.watchdog import run_sandboxed
from devharness_server.tools_lineage import _bounded_metadata


class _CalibrationDiagnosticSummary(TypedDict):
    code: str
    severity: str
    diagnostic_record_count: int
    example_gene_ids: list[str]
    message: NotRequired[str]


def _metadata(path: str, time_col: str = "embryonic_age", categorical_columns=()) -> pd.DataFrame:
    metadata = pd.read_csv(Path(path).expanduser().resolve(),
                           dtype={name: str for name in ("sample_id", *BIOLOGICAL_UNIT_COLUMNS, *categorical_columns)})
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


def _check_formula_node(node, allowed, columns, functions):
    if not isinstance(node, allowed):
        raise ValueError("Unsupported formula syntax: " + type(node).__name__)
    if isinstance(node, ast.Name) and (
        "__" in node.id or node.id not in columns | functions
    ):
        raise ValueError("Unknown or forbidden formula name: " + node.id)
    if isinstance(node, ast.keyword) and (node.arg is None or "__" in node.arg):
        raise ValueError("Expanded or private keyword arguments are forbidden")
    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name) or node.func.id not in functions:
            raise ValueError("Only C/cr/bs/I/Q function calls are permitted")
        if node.func.id == "Q" and (len(node.args) != 1 or node.keywords
                or not isinstance(node.args[0], ast.Constant)
                or not isinstance(node.args[0].value, str)
                or node.args[0].value not in columns or "__" in node.args[0].value):
            raise ValueError("Q must name one existing metadata column")


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
                    _check_formula_node(node, allowed, columns, functions)
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
    return temporal_spline_formula(formula, df)


def _align_samples(store, metadata):
    if (
        "sample_id" not in metadata
        or metadata["sample_id"].isna().any()
        or metadata["sample_id"].duplicated().any()
    ):
        raise ValueError("Metadata requires unique nonmissing sample_id values")
    samples = list(store.sample_names)
    metadata["sample_id"] = metadata["sample_id"].astype(str)
    if set(samples) != set(metadata["sample_id"]):
        raise ValueError("Expression and metadata sample IDs must match exactly")
    return metadata.set_index("sample_id").loc[samples]


def _aligned_metadata(store, metadata_path, time_col, categorical_columns=()):
    metadata = _align_samples(
        store, _metadata(metadata_path, time_col, categorical_columns)
    )
    if "numeric_time" not in metadata or metadata["numeric_time"].nunique() < 3:
        raise ValueError("Natural splines require at least three distinct times")
    return metadata


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

    time_grid = sorted(float(value) for value in metadata["numeric_time"].unique())
    comparison = metadata.iloc[[0] * len(time_grid)].copy()
    comparison["numeric_time"] = time_grid
    prediction_design = np.asarray(build_design_matrices([design.design_info], comparison)[0])
    linear = prediction_design @ np.asarray(fitted.params)
    predicted = np.exp(linear) if discrete else linear
    contrast = prediction_design[-1] - prediction_design[0]
    variance = float(contrast @ np.asarray(fitted.cov_params()) @ contrast)
    if not np.isfinite(predicted).all() or not np.isfinite(variance) or variance < -1e-10:
        raise ValueError("contrast_nonfinite: Endpoint estimates or covariance are invalid")
    # Generic continuous inputs may already be logged or standardized. Positive
    # predictions alone do not establish an abundance-ratio measurement scale.
    log2fc = float((linear[-1] - linear[0]) / np.log(2)) if discrete else None
    difference = float(predicted[-1] - predicted[0])
    if not np.isfinite(difference) or (log2fc is not None and not np.isfinite(log2fc)):
        raise ValueError("nonfinite_statistical_output: Endpoint effect estimate overflowed")
    return {"predicted_start": float(predicted[0]), "predicted_end": float(predicted[-1]),
            "time_grid": time_grid, "predicted_curve": predicted.tolist(),
            "effect_difference": difference, "log2_fold_change": log2fc,
            "endpoint_contrast_se": float(np.sqrt(max(variance, 0))),
            "effect_scale": "log_mean" if discrete else "expression_units",
            "reference_batch": str(metadata.iloc[0]["batch"]) if "batch" in metadata else None,
            "contrast_note": "First sample nuisance covariates held fixed at both endpoints; count library factor=1",
            "time_start": time_grid[0], "time_end": time_grid[-1]}


def _replication_diagnostics(metadata, batch_col="batch"):
    stage_replicates = metadata.groupby("numeric_time").size()
    diagnostics = []
    if int(stage_replicates.min()) < 2:
        diagnostics.append(
            {
                "code": "insufficient_stage_replicates",
                "severity": "warning",
                "message": (
                    "Some time points have fewer than two independent observations"
                ),
            }
        )
    if (
        batch_col in metadata
        and int(metadata.groupby(["numeric_time", batch_col]).size().min()) < 2
    ):
        diagnostics.append(
            {
                "code": "insufficient_batch_time_replicates",
                "severity": "warning",
                "message": (
                    "Some observed batch-time cells have fewer than two observations"
                ),
            }
        )
    return diagnostics, {
        str(float(time)): int(n) for time, n in stage_replicates.items()
    }


def _biological_unit_diagnostics(metadata):
    """Check every declared unit level; unique specimens cannot erase repeated subjects."""
    columns = [name for name in BIOLOGICAL_UNIT_COLUMNS if name in metadata]
    repeated, incomplete, unique_counts = [], [], {}
    for name in columns:
        values = metadata[name].astype("string").str.strip()
        observed = values[values.notna() & ~values.str.lower().isin(UNKNOWN_UNIT_MARKERS)]
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


def _fit_options(
    fdr_scope,
    genes,
    gene_offset,
    gene_limit,
    selection_criterion,
    formula,
    spline_dfs,
    inference_mode,
    monte_carlo_draws,
    monte_carlo_seed,
):
    if fdr_scope not in ("requested_genes", "genome_wide"):
        raise ValueError("fdr_scope must be requested_genes or genome_wide")
    genome_wide = fdr_scope == "genome_wide"
    if (
        len(set(genes)) != len(genes)
        or len(genes) > 100
        or (not genes and not genome_wide)
    ):
        raise ValueError(
            "Request between 1 and 100 distinct genes to bound tool output"
        )
    if (
        type(gene_offset) is not int
        or gene_offset < 0
        or type(gene_limit) is not int
        or not 1 <= gene_limit <= 100
    ):
        raise ValueError(
            "gene_offset must be nonnegative and gene_limit must be between 1 and 100"
        )
    if (genes or not genome_wide) and (gene_offset != 0 or gene_limit != 100):
        raise ValueError(
            "Source-order pagination requires genome_wide and an empty gene_list"
        )
    if selection_criterion not in {"aic", "bic", "gcv", "fixed"}:
        raise ValueError("selection_criterion must be aic, bic, gcv, or fixed")
    monte_carlo_draws, monte_carlo_seed = _inference_configuration(
        inference_mode, monte_carlo_draws, monte_carlo_seed
    )
    if inference_mode == "gaussian_selection_mc" and selection_criterion == "fixed":
        raise ValueError(
            "fixed criterion uses conditional analytic inference; choose aic, bic, or "
            "gcv for selection MC"
        )
    if selection_criterion == "fixed" and spline_dfs is None:
        spline_dfs = [specified_temporal_df(formula)]
    dfs = [2, 3, 4, 5] if spline_dfs is None else spline_dfs
    if (
        not dfs
        or any(
            isinstance(df, bool) or not isinstance(df, int) or df not in {2, 3, 4, 5}
            for df in dfs
        )
        or len(set(dfs)) != len(dfs)
    ):
        raise ValueError("spline_dfs must contain distinct integers from 2, 3, 4, 5")
    if selection_criterion == "fixed" and len(dfs) != 1:
        raise ValueError("fixed selection requires exactly one spline df")
    return genome_wide, dfs, monte_carlo_draws, monte_carlo_seed


def _fit_gene_page(store, genes, genome_wide, gene_offset, gene_limit):
    from devharness_server.normalization import MAX_BLOCK_BYTES

    if not genome_wide:
        return genes, None
    explicit_gene_page = bool(genes)
    if store.shape[0] > 100_000 or store.shape[1] * 8 > MAX_BLOCK_BYTES:
        raise ValueError("genome_wide_family_exceeds_bounded_model_budget")
    if explicit_gene_page:
        unknown = set(genes) - set(store.gene_names)
        if unknown:
            raise ValueError("Unknown genes: " + ", ".join(sorted(unknown)))
    else:
        if gene_offset >= store.shape[0]:
            raise ValueError("gene_offset starts outside the expression feature axis")
        genes = store.gene_names[gene_offset : gene_offset + gene_limit]
    next_offset = gene_offset + len(genes)
    gene_page = {
        "selection": "explicit_gene_list" if explicit_gene_page else "source_order",
        "offset": None if explicit_gene_page else gene_offset,
        "count": len(genes),
        "next_offset": None
        if explicit_gene_page or next_offset >= store.shape[0]
        else next_offset,
        "input_gene_count": store.shape[0],
    }
    return genes, gene_page


def _repeated_unit_report(
    independence,
    unit_diagnostics,
    strict,
    genes,
    formula,
    fdr_scope,
    genome_wide,
    gene_page,
    selection_criterion,
    dfs,
    parameters,
):
    failure = unit_diagnostics[0]
    if strict:
        raise ValueError(failure["code"] + ": " + failure["message"])
    return {
        "status": "failed",
        "failure_code": failure["code"],
        "model_fitting_performed": False,
        "family": None,
        "formula": formula,
        "formula_scope": "not_fitted",
        "design": None,
        "genes": {
            gene: {
                "status": "failed",
                "failure_code": failure["code"],
                "p_value": None,
                "padj": None,
                "q_value": None,
                "selected_df": None,
                "candidate_scores": [],
                "coefficients": {},
                "dispersion_alpha": None,
                "diagnostics": unit_diagnostics,
            }
            for gene in genes
        },
        "fdr_scope": fdr_scope,
        "fdr_adjustment_performed": False,
        **({"gene_page": gene_page} if genome_wide else {}),
        "selected_df": {gene: None for gene in genes},
        "selection_criterion": selection_criterion,
        "spline_dfs": sorted(dfs),
        "normalization_method": "not_performed",
        "normalization": {"method": "not_performed"},
        "size_factors": None,
        "dispersion_calibration": None,
        "expression_diagnostics": None,
        "independence_assumption": independence,
        "diagnostics": unit_diagnostics,
        "warnings": [failure["message"]],
        "inference_scope": "No inference performed. " + failure["message"],
        "provenance": {
            "parameters": parameters,
            "library_versions": {
                name: importlib.metadata.version(name) for name in ("numpy", "pandas")
            },
        },
    }


def _selection_fit_setup(
    discrete,
    inference_mode,
    selection_criterion,
    designs,
    monte_carlo_draws,
    monte_carlo_seed,
):
    if discrete and inference_mode == "gaussian_selection_mc":
        raise ValueError(
            "gaussian_selection_mc requires continuous Gaussian inputs; counts are "
            "unsupported"
        )
    if discrete and selection_criterion == "gcv":
        raise ValueError(
            "GCV is only implemented for continuous Gaussian expression; use aic or "
            "bic for counts"
        )
    selection_engine, gaussian_operators = None, {}
    if not discrete:
        if inference_mode == "gaussian_selection_mc":
            from devharness_server.selection_mc import GaussianSelectionMC

            selection_engine = GaussianSelectionMC(
                designs, selection_criterion, monte_carlo_draws, monte_carlo_seed
            )
        else:
            gaussian_operators = _gaussian_residual_operators(designs)
    return selection_engine, gaussian_operators


def _family_calibration_diagnostics(
    records: list[dict],
) -> Iterable[_CalibrationDiagnosticSummary]:
    by_code: dict[str, _CalibrationDiagnosticSummary] = {}
    for record in records:
        summary_record = by_code.setdefault(
            record["code"],
            {
                "code": record["code"],
                "severity": record["severity"],
                "diagnostic_record_count": 0,
                "example_gene_ids": [],
            },
        )
        summary_record["diagnostic_record_count"] += 1
        examples = summary_record["example_gene_ids"]
        examples.extend(record.get("gene_ids", [])[: max(0, 20 - len(examples))])
        if "message" in record:
            summary_record["message"] = record["message"]
    return by_code.values()


def _record_calibration_notices(panel, normalization, diagnostics, notices):
    if panel["panel_gene_count"] < panel["total_gene_count"]:
        notices.append(
            "Normalization and the empirical dispersion reference use a bounded gene "
            "panel; its representativeness requires assessment"
        )
    if normalization["reference_gene_count"] < 20:
        diagnostics.append(
            {
                "code": "small_normalization_reference",
                "severity": "warning",
                "message": (
                    "Fewer than 20 reference genes; RLE factors may absorb real "
                    "coordinated changes"
                ),
            }
        )
    if panel["panel_gene_count"] < 20:
        diagnostics.append(
            {
                "code": "small_dispersion_panel",
                "severity": "warning",
                "message": (
                    "Fewer than 20 calibration genes; the empirical dispersion prior "
                    "may be unstable"
                ),
            }
        )
    if normalization["method"] == "median_of_ratios_poscounts":
        notices.append(
            "No calibration gene is positive in every sample; the disclosed DESeq2 "
            "positive-count RLE variant was used"
        )


def _temporal_expression(
    store: ChunkedExpressionStore,
    metadata,
    designs,
    genes: list[str],
    diagnostics,
    notices,
    *,
    discrete: bool,
    genome_wide: bool,
    finite_count_reference: bool,
    strict: bool,
    calibration_max_genes: int,
) -> tuple[
    Iterable[tuple[str, np.ndarray]],
    np.ndarray | None,
    dict[str, Any],
    dict | None,
    dict[str, str],
    np.ndarray | None,
]:
    from devharness_server.normalization import (
        MAX_BLOCK_BYTES,
        calibrate_count_family,
        calibrate_counts,
        calibration_panel,
        low_count_mask,
    )

    factors, calibration = None, None
    normalization: dict[str, Any] = {
        "method": "none",
        "scope": "continuous_input_unchanged",
    }
    filtered, log_offset = {}, None
    rows_per_block = max(1, min(500, MAX_BLOCK_BYTES // (8 * store.shape[1])))
    if discrete:
        # A report page cannot influence normalization, the prior or BH.
        values, panel = calibration_panel(
            store, [] if genome_wide else genes, calibration_max_genes
        )
        # A common richest feasible design limits residual biological signal
        # entering dispersion estimation; fixed alphas then make candidate
        # likelihoods comparable. Its identity is returned explicitly.
        calibration_df = max(designs)
        calibration_formula, calibration_design, _ = designs[calibration_df]
        reference = calibrate_counts(
            values, panel, metadata, calibration_design, strict
        )
        (
            factors,
            normalization,
            calibration,
            calibration_diagnostics,
            calibration_warnings,
        ) = reference
        log_offset = np.log(factors)
        if genome_wide:
            calibration, calibration_diagnostics, calibration_warnings, filtered = (
                calibrate_count_family(
                    store.iter_gene_chunks(rows_per_block),
                    reference,
                    metadata,
                    calibration_design,
                    strict=strict,
                )
            )
        elif finite_count_reference:
            filtered = {
                gene: "independent_low_count_filter"
                for gene, low in zip(
                    panel["gene_ids"], low_count_mask(values, factors), strict=True
                )
                if low
            }
        calibration.update(formula=calibration_formula, spline_df=calibration_df)
        diagnostics.extend(
            _family_calibration_diagnostics(calibration_diagnostics)
            if genome_wide
            else calibration_diagnostics
        )
        notices.extend(calibration_warnings)
        _record_calibration_notices(panel, normalization, diagnostics, notices)
        if not genome_wide:
            gene_values = {
                gene: values[i]
                for i, gene in enumerate(panel["gene_ids"])
                if gene in genes
            }
    elif not genome_wide:
        values, panel = calibration_panel(store, genes, len(genes))
        gene_values = dict(zip(panel["gene_ids"], values, strict=False))
    if genome_wide:
        to_fit = (
            (gene, row)
            for names, block in store.iter_gene_chunks(rows_per_block)
            for gene, row in zip(names, block, strict=True)
        )
    else:
        to_fit = ((gene, gene_values[gene]) for gene in genes)
    return to_fit, factors, normalization, calibration, filtered, log_offset


def _evaluate_temporal_selection(selection_engine, y, gene):
    if selection_engine is None:
        return None
    try:
        return selection_engine.evaluate(y)
    except (ValueError, np.linalg.LinAlgError, FloatingPointError) as error:
        raise ValueError(f"selection_calibration_failed: {gene}: {error}") from error


def _fit_temporal_candidates(
    gene,
    y,
    designs,
    candidates,
    gaussian_operators,
    mc_result,
    alpha,
    log_offset,
    diagnostics,
    *,
    report_gene,
    strict,
):
    fitted_candidates = {}
    non_page_candidate_errors = 0
    for candidate_df, (transformed, design, _) in designs.items():
        try:
            if candidate_df in gaussian_operators:
                from devharness_server.selection_mc import _resolved_rss

                with np.errstate(
                    over="raise", invalid="raise", divide="raise", under="ignore"
                ):
                    _resolved_rss(
                        y[None, :],
                        gaussian_operators[candidate_df],
                        f"Gaussian candidate df={candidate_df}",
                    )
            fitted, scores, fit_warnings = _fit_candidate(y, design, alpha, log_offset)
            if mc_result is not None:
                scores = mc_result["candidate_scores"][candidate_df]
            fitted_candidates[candidate_df] = fitted
            candidates.append(
                dict(
                    scores,
                    df=candidate_df,
                    formula=transformed,
                    status="ok",
                    warnings=fit_warnings,
                )
            )
        except (ValueError, np.linalg.LinAlgError, FloatingPointError) as error:
            candidates.append(
                {
                    "df": candidate_df,
                    "formula": transformed,
                    "status": "failed",
                    "error_type": type(error).__name__,
                    "reason": str(error),
                }
            )
            if report_gene:
                diagnostics.append(
                    {
                        "code": "candidate_fit_failed",
                        "severity": "error",
                        "gene_id": gene,
                        "df": candidate_df,
                        "message": str(error),
                    }
                )
            else:
                non_page_candidate_errors += 1
            if strict:
                raise ValueError(
                    f"candidate_fit_failed: {gene}, df={candidate_df}: {error}"
                ) from error
    return fitted_candidates, non_page_candidate_errors


def _constant_temporal_fit(y, design, metadata, discrete):
    coefficients = np.zeros(design.shape[1])
    if "Intercept" in design:
        coefficients[list(design.columns).index("Intercept")] = (
            np.log(1e-12) if discrete else float(y[0])
        )
    return {
        "coefficients": dict(zip(design.columns, coefficients.tolist(), strict=False)),
        "wald_statistic": 0.0,
        "p_value": 1.0,
        "status": "no_information",
        "predicted_start": float(y[0]),
        "predicted_end": float(y[0]),
        "effect_difference": 0.0,
        "time_grid": sorted(
            float(value) for value in metadata["numeric_time"].unique()
        ),
        "predicted_curve": [float(y[0])] * metadata["numeric_time"].nunique(),
        "reference_batch": str(metadata.iloc[0]["batch"])
        if "batch" in metadata
        else None,
        "log2_fold_change": None,
        "test_statistic": None,
        "test_distribution": "not_tested",
        "test_df_num": None,
        "test_df_denom": None,
    }


def _estimate_temporal_fit(
    gene,
    fitted,
    design,
    metadata,
    selected_record,
    mc_result,
    discrete,
    finite_count_reference,
):
    temporal = [
        i
        for term, sl in design.design_info.term_name_slices.items()
        if _uses_temporal_variable(term)
        for i in range(sl.start, sl.stop)
    ]
    with warnings.catch_warnings(record=True) as wald_warnings:
        warnings.simplefilter("always")
        # Gaussian F inference is conditional on the design. The
        # open NB reference is approximate with plug-in dispersion.
        use_f = not discrete or finite_count_reference
        wald = fitted.wald_test(
            np.eye(design.shape[1])[temporal], use_f=use_f, scalar=True
        )
    test_statistic, p_value = float(wald.statistic), float(wald.pvalue)
    statistic = test_statistic * len(temporal) if use_f else test_statistic
    if not np.isfinite([statistic, test_statistic, p_value]).all():
        raise ValueError(f"wald_nonfinite: Nonfinite Wald test for gene {gene}")
    if mc_result is not None and (
        not np.isclose(
            test_statistic, mc_result["conditional_f_statistic"], rtol=1e-8, atol=1e-10
        )
        or not np.isclose(
            p_value, mc_result["conditional_p_value"], rtol=1e-8, atol=1e-10
        )
    ):
        raise ValueError(
            f"selection_calibration_failed: fitted and projection F calculations "
            f"differ for {gene}"
        )
    selected = {
        "coefficients": dict(zip(design.columns, np.asarray(fitted.params).tolist(), strict=False)),
        "wald_statistic": statistic,
        "p_value": p_value,
        "status": "ok",
        "test_statistic": test_statistic,
        "test_distribution": "F" if use_f else "chi_square",
        "test_df_num": len(temporal),
        "test_df_denom": float(fitted.df_resid) if use_f else None,
        "warnings": list(
            dict.fromkeys(
                selected_record["warnings"] + [str(w.message) for w in wald_warnings]
            )
        ),
    }
    selected.update(_effect_estimate(fitted, design, metadata, discrete))
    if mc_result is not None:
        selected.update(
            p_value=mc_result["p_value"],
            test_distribution="selection_monte_carlo",
            test_statistic=mc_result["conditional_p_value"],
            test_tail="lower",
            test_df_num=None,
            test_df_denom=None,
            conditional_p_value=mc_result["conditional_p_value"],
            conditional_f_statistic=mc_result["conditional_f_statistic"],
            conditional_df_num=mc_result["conditional_df_num"],
            conditional_df_denom=mc_result["conditional_df_denom"],
            conditional_test_distribution="F",
            wald_statistic=mc_result["conditional_f_statistic"]
            * mc_result["conditional_df_num"],
            wald_statistic_scope=(
                "Conditional selected-model F quadratic; not the Monte Carlo rank "
                "statistic"
            ),
            endpoint_inference_scope=(
                "Selected-model estimates and conditional SEs; no selection-adjusted "
                "interval"
            ),
        )
    return selected


def _fit_temporal_gene(
    gene,
    y,
    metadata,
    designs,
    rejected,
    dispersion,
    log_offset,
    selection_criterion,
    selection_engine,
    gaussian_operators,
    diagnostics,
    *,
    discrete,
    finite_count_reference,
    report_gene,
    strict,
):
    per_gene_diagnostics = []
    if np.count_nonzero(y) < max(3, len(y) // 5):
        per_gene_diagnostics.append(
            {
                "code": "high_dropout",
                "severity": "warning",
                "message": (
                    "Fewer than 20% (or fewer than three) observations are nonzero"
                ),
            }
        )
    candidates = [dict(record) for record in rejected]
    non_page_candidate_errors = 0
    no_information = np.all(y == 0) or (not discrete and np.ptp(y) == 0)
    if no_information:
        df = min(designs)
        transformed, design, design_diagnostics = designs[df]
        candidates.extend(
            {
                "df": candidate_df,
                "status": "no_information",
                "reason": "constant_expression",
            }
            for candidate_df in designs
        )
        selected = _constant_temporal_fit(y, design, metadata, discrete)
        per_gene_diagnostics.append(
            {
                "code": "no_expression_information",
                "severity": "warning",
                "message": (
                    "Constant continuous or all-zero count expression has no temporal "
                    "test information"
                ),
            }
        )
    else:
        mc_result = _evaluate_temporal_selection(selection_engine, y, gene)
        fitted_candidates, non_page_candidate_errors = _fit_temporal_candidates(
            gene,
            y,
            designs,
            candidates,
            gaussian_operators,
            mc_result,
            dispersion["dispersion_alpha"],
            log_offset,
            diagnostics,
            report_gene=report_gene,
            strict=strict or selection_engine is not None,
        )
        eligible = [record for record in candidates if record["status"] == "ok"]
        if not eligible:
            failed = dict(
                dispersion,
                status="failed",
                failure_code="all_candidate_fits_failed",
                candidate_scores=sorted(candidates, key=lambda record: record["df"]),
                p_value=None,
                padj=None,
                selected_df=None,
                diagnostics=per_gene_diagnostics,
            )
            if report_gene:
                diagnostics.append(
                    {
                        "code": "all_candidate_fits_failed",
                        "severity": "error",
                        "gene_id": gene,
                    }
                )
            return failed, non_page_candidate_errors
        selected_record = min(
            eligible,
            key=lambda record: (
                record[selection_criterion] if selection_criterion != "fixed" else 0,
                record["df"],
            ),
        )
        df = selected_record["df"]
        if mc_result is not None and df != mc_result["selected_df"]:
            raise ValueError(
                "selection_calibration_failed: candidate selection disagrees with "
                "reference kernel"
            )
        transformed, design, design_diagnostics = designs[df]
        selected = _estimate_temporal_fit(
            gene,
            fitted_candidates[df],
            design,
            metadata,
            selected_record,
            mc_result,
            discrete,
            finite_count_reference,
        )
    temporal_count = sum(
        sl.stop - sl.start
        for term, sl in design.design_info.term_name_slices.items()
        if _uses_temporal_variable(term)
    )
    if len(design) - design.shape[1] <= 3:
        per_gene_diagnostics.append(
            {
                "code": "low_residual_df",
                "severity": "warning",
                "message": (
                    "At most three residual degrees of freedom; asymptotic inference "
                    "is unstable"
                ),
            }
        )
    result = dict(
        selected,
        **dispersion,
        selected_df=df,
        formula=transformed,
        design=design_diagnostics,
        degrees_of_freedom=temporal_count,
        residual_df=len(design) - design.shape[1],
        candidate_scores=sorted(candidates, key=lambda record: record["df"]),
        zero_fraction=float(np.mean(y == 0)),
        nonzero_observations=int(np.count_nonzero(y)),
        diagnostics=per_gene_diagnostics,
    )
    return result, non_page_candidate_errors


def _fit_temporal_family(
    to_fit,
    wanted,
    metadata,
    designs,
    rejected,
    calibration,
    filtered,
    log_offset,
    selection_criterion,
    selection_engine,
    gaussian_operators,
    diagnostics,
    *,
    discrete,
    genome_wide,
    finite_count_reference,
    strict,
):
    results = {}
    probabilities, selected_model_dfs = {}, set()
    failed_hypotheses, non_page_candidate_errors = 0, 0
    no_dispersion = {
        "genewise_dispersion": None,
        "dispersion_alpha": None,
        "trend_dispersion": None,
        "dispersion_outlier": False,
        "dispersion_converged": None,
    }
    for gene, values in to_fit:
        y = np.asarray(values, dtype=float)
        report_gene = gene in wanted
        if gene in filtered:
            result = dict(
                no_dispersion,
                status="filtered",
                failure_code=filtered[gene],
                candidate_scores=[],
                p_value=None,
                padj=None,
                q_value=None,
                selected_df=None,
                test_distribution="not_tested",
                diagnostics=[{"code": filtered[gene], "severity": "info"}],
            )
        else:
            dispersion = (
                calibration["gene_estimates"][gene]
                if calibration is not None
                else no_dispersion
            )
            if discrete and not dispersion["dispersion_converged"]:
                result = dict(
                    dispersion,
                    status="failed",
                    failure_code="dispersion_fit_failed",
                    candidate_scores=[],
                    p_value=None,
                    padj=None,
                    selected_df=None,
                    diagnostics=[
                        {"code": "dispersion_fit_failed", "severity": "error"}
                    ],
                )
            else:
                result, unreported_errors = _fit_temporal_gene(
                    gene,
                    y,
                    metadata,
                    designs,
                    rejected,
                    dispersion,
                    log_offset,
                    selection_criterion,
                    selection_engine,
                    gaussian_operators,
                    diagnostics,
                    discrete=discrete,
                    finite_count_reference=finite_count_reference,
                    report_gene=report_gene,
                    strict=strict,
                )
                non_page_candidate_errors += unreported_errors
        if not genome_wide or result["status"] != "filtered":
            p_value = result["p_value"]
            probabilities[gene] = p_value if p_value is not None else 1.0
        if result["selected_df"] is not None:
            selected_model_dfs.add(result["selected_df"])
        if result["status"] == "failed":
            failed_hypotheses += 1
        if report_gene:
            results[gene] = result
    if non_page_candidate_errors:
        diagnostics.append(
            {
                "code": "nonpage_candidate_fit_failures",
                "severity": "error",
                "count": non_page_candidate_errors,
            }
        )
    return results, probabilities, selected_model_dfs, failed_hypotheses


def _adjust_temporal_family(genes, results, probabilities, selection_engine):
    # Failed genes remain explicitly failed. A unit p-value occupies their
    # denominator position. The report page does not enter this calculation.
    adjusted = dict(
        zip(probabilities, bh_adjust(list(probabilities.values())), strict=True)
    )
    results = {gene: results[gene] for gene in genes}
    for gene, result in results.items():
        result["padj"] = adjusted[gene] if result["p_value"] is not None else None
        result["q_value"] = result["padj"]
    if selection_engine is not None:
        from scipy.stats import false_discovery_control

        by_values = false_discovery_control(list(probabilities.values()), method="by")
        by_adjusted = dict(zip(probabilities, by_values, strict=True))
        for gene, result in results.items():
            result["by_q_value"] = (
                float(by_adjusted[gene]) if result["p_value"] is not None else None
            )
    return results


def _temporal_inference_scope(
    selection_engine,
    selection_criterion,
    dfs,
    discrete,
    genome_wide,
    finite_count_reference,
    notices,
    diagnostics,
    monte_carlo_draws,
):
    if selection_engine is None and selection_criterion != "fixed" and len(dfs) > 1:
        notices.append(
            "Temporal p-values condition on data-selected spline complexity; they are "
            "not adjusted for model selection"
        )
    if not discrete and selection_engine is None:
        notices.append(
            "Gaussian F tests assume independent homoskedastic normal errors; "
            "finite-sample calibration requires a prespecified design"
        )
    notices.extend(record["message"] for record in diagnostics if "message" in record)
    adjustment_scope = (
        "the eligible complete submitted feature axis before any output selection"
        if genome_wide
        else "exactly the requested gene family"
    )
    count_reference = (
        "an approximate residual-df F reference"
        if finite_count_reference
        else "an asymptotic chi-square reference"
    )
    inference_scope = (
        "Observational associations in the submitted cohort; no causal batch-adjusted "
        "effect is identified by removing a confounded covariate. "
        f"Gaussian inference uses an F reference conditional on the design; NB Wald "
        f"inference uses {count_reference}. "
        "Inference conditions on spline selection and fitted dispersion/size factors; "
        "selection uncertainty is not included. "
        f"BH adjustment covers {adjustment_scope}. Independent observations and "
        f"correctly specified covariates are assumed."
    )
    if selection_engine is not None:
        notices.extend(
            [
                "Experimental selection calibration assumes independent homoskedastic "
                "Gaussian errors and fixed design/candidates/criterion/seed; unequal "
                "variance and correlated errors remain unqualified.",
                "Endpoint estimates and SEs remain conditional on the selected model; "
                "no selection-adjusted confidence interval is provided.",
                "BH values require their usual dependence conditions; separately "
                "named BY values are more conservative. Neither is a clinical FDR "
                "qualification.",
                f"Monte Carlo p-value resolution is 1/{monte_carlo_draws + 1}; "
                f"probabilities below this floor are not estimated.",
            ]
        )
        inference_scope = (
            "Experimental Gaussian Monte Carlo rank test, marginal over the complete "
            "fixed model-selection rule. "
            "Observed and reference statistics use the same Householder numerical "
            "kernel. "
            "Independent homoskedastic Gaussian errors, a fixed design/candidate "
            "set/criterion/seed and a prespecified gene family are assumed. "
            "The reference is shared within the testing family; BH dependence "
            "conditions are not automatically established. "
            f"BH padj/q_value and separate Benjamini-Yekutieli by_q_value cover "
            f"{adjustment_scope}. "
            "Conditional F diagnostics and endpoint estimates retain their conditional "
            "meaning. "
            "No heteroskedastic, correlated-error, negative-binomial, interval or "
            "clinical qualification is established."
        )
    return inference_scope


def _temporal_library_versions(calibration, genes, filtered, genome_wide, wanted):
    versions = {
        name: importlib.metadata.version(name)
        for name in ("numpy", "pandas", "scipy", "statsmodels", "patsy")
    }
    if calibration is not None:
        versions["pydeseq2"] = calibration["version"]
        calibration["gene_estimates"] = {
            gene: calibration["gene_estimates"][gene]
            for gene in genes
            if gene not in filtered
        }
        calibration["gene_estimates_scope"] = (
            "reported_genes" if genome_wide else "requested_genes"
        )
        if genome_wide:
            calibration["optimizer_retry_count"] = len(calibration["optimizer_retries"])
            calibration["optimizer_retries"] = [
                record
                for record in calibration["optimizer_retries"]
                if record["gene_id"] in wanted
            ]
    return versions


def _fit_worker(
    counts_path: str,
    metadata_path: str,
    formula: str,
    time_col: str,
    genes: List[str],
    selection_criterion="aic",
    spline_dfs=None,
    data_type="auto",
    calibration_max_genes=1000,
    strict=True,
    categorical_columns=(),
    inference_mode="conditional",
    monte_carlo_draws=65535,
    monte_carlo_seed=0,
    *,
    exploratory_counts=False,
    fdr_scope="requested_genes",
    gene_offset=0,
    gene_limit=100,
    selection_provenance=None,
) -> dict:
    from devharness_server.normalization import (
        MIN_NORMALIZED_MEAN_COUNT,
        expression_summary,
    )

    genome_wide, dfs, monte_carlo_draws, monte_carlo_seed = _fit_options(
        fdr_scope,
        genes,
        gene_offset,
        gene_limit,
        selection_criterion,
        formula,
        spline_dfs,
        inference_mode,
        monte_carlo_draws,
        monte_carlo_seed,
    )
    parameters = {
        "formula_str": formula,
        "time_col": time_col,
        "gene_list": genes,
        "statistical_method_version": "temporal_glm/4_exact_terms_and_effect_scale",
        "temporal_term_interpretation": "parsed_variable_identity/1",
        "selection_criterion": selection_criterion,
        "spline_dfs": sorted(dfs),
        "data_type": data_type,
        "calibration_max_genes": calibration_max_genes,
        "strict": strict,
        "glm_maxiter": 200,
        "glm_tol": 1e-8,
    }
    if categorical_columns:
        parameters["categorical_columns"] = list(categorical_columns)
    if inference_mode == "gaussian_selection_mc":
        parameters.update(
            statistical_method_version="temporal_glm/4_gaussian_selection_mc_exact_terms",
            inference_mode=inference_mode,
            monte_carlo_draws=monte_carlo_draws,
            monte_carlo_seed=monte_carlo_seed,
        )
    with ChunkedExpressionStore(counts_path) as store:
        genes, gene_page = _fit_gene_page(
            store, genes, genome_wide, gene_offset, gene_limit
        )
        if gene_page is not None:
            parameters.update(
                fdr_scope=fdr_scope,
                gene_offset=gene_page["offset"],
                gene_limit=gene_limit,
            )
        metadata = _aligned_metadata(
            store, metadata_path, time_col, categorical_columns
        )
        independence, unit_diagnostics = _biological_unit_diagnostics(metadata)
        if independence["repeated_unit_columns"]:
            return _repeated_unit_report(
                independence,
                unit_diagnostics,
                strict,
                genes,
                formula,
                fdr_scope,
                genome_wide,
                gene_page,
                selection_criterion,
                dfs,
                parameters,
            )
        unit_column = (
            independence["declared_unit_columns"][0]
            if independence["verification_status"] == "declared_unique_units"
            else None
        )
        selection = validate_expression_selection(
            selection_provenance, metadata, biological_unit_column=unit_column
        )
        designs, rejected = _candidate_designs(metadata, formula, sorted(dfs))
        summary = expression_summary(store, data_type)
        discrete = summary["data_type"] == "counts"
        selection_engine, gaussian_operators = _selection_fit_setup(
            discrete,
            inference_mode,
            selection_criterion,
            designs,
            monte_carlo_draws,
            monte_carlo_seed,
        )
        diagnostics, replicates = _replication_diagnostics(metadata)
        diagnostics.extend(unit_diagnostics)
        notices: list[str] = []
        finite_count_reference = discrete and exploratory_counts
        if finite_count_reference:
            parameters["statistical_method_version"] = (
                "temporal_glm/5_exploratory_count_residual_df"
            )
        to_fit, factors, normalization, calibration, filtered, log_offset = (
            _temporal_expression(
                store,
                metadata,
                designs,
                genes,
                diagnostics,
                notices,
                discrete=discrete,
                genome_wide=genome_wide,
                finite_count_reference=finite_count_reference,
                strict=strict,
                calibration_max_genes=calibration_max_genes,
            )
        )
        wanted = set(genes)
        results, probabilities, selected_model_dfs, failed_hypotheses = (
            _fit_temporal_family(
                to_fit,
                wanted,
                metadata,
                designs,
                rejected,
                calibration,
                filtered,
                log_offset,
                selection_criterion,
                selection_engine,
                gaussian_operators,
                diagnostics,
                discrete=discrete,
                genome_wide=genome_wide,
                finite_count_reference=finite_count_reference,
                strict=strict,
            )
        )
        results = _adjust_temporal_family(
            genes, results, probabilities, selection_engine
        )
        selected_dfs = {gene: result["selected_df"] for gene, result in results.items()}
        top_df = (
            next(iter(selected_model_dfs))
            if len(selected_model_dfs) == 1
            else max(designs)
        )
        top_formula, _, top_design = designs[top_df]
        inference_scope = _temporal_inference_scope(
            selection_engine,
            selection_criterion,
            dfs,
            discrete,
            genome_wide,
            finite_count_reference,
            notices,
            diagnostics,
            monte_carlo_draws,
        )
        versions = _temporal_library_versions(
            calibration, genes, filtered, genome_wide, wanted
        )
        output = {
            "family": "negative_binomial" if discrete else "gaussian",
            "formula": top_formula,
            "formula_scope": "common_selected_model"
            if len(selected_model_dfs) == 1
            else "richest_feasible_design; see per-gene selected formulas",
            "design": top_design,
            "genes": results,
            "fdr_scope": fdr_scope,
            "selected_df": selected_dfs,
            "selection_criterion": selection_criterion,
            "spline_dfs": sorted(dfs),
            "normalization_method": normalization["method"],
            "normalization": normalization,
            "size_factors": dict(zip(store.sample_names, factors.tolist(), strict=False))
            if factors is not None
            else None,
            "dispersion_calibration": calibration,
            "expression_diagnostics": summary,
            "stage_replicates": replicates,
            "independence_assumption": independence,
            "warnings": list(dict.fromkeys(notices)),
            "diagnostics": diagnostics,
            "inference_scope": inference_scope,
            "status": "partial_failure"
            if failed_hypotheses
            or any(record["severity"] == "error" for record in diagnostics)
            else "ok",
            "provenance": {"library_versions": versions, "parameters": parameters},
        }
        if genome_wide:
            output["gene_page"] = gene_page
            output["fdr_family"] = {
                "input_gene_count": store.shape[0],
                "test_family_count": len(probabilities),
                "filtered_gene_count": len(filtered),
                "failed_hypothesis_count": failed_hypotheses,
                "scope": "complete_submitted_feature_axis",
                "failed_hypotheses_contribute_internal_p_one": True,
            }
        if finite_count_reference:
            output["independent_count_filter"] = {
                "statistic": "across_observations_mean_of_RLE_normalized_counts",
                "minimum_normalized_mean_count": MIN_NORMALIZED_MEAN_COUNT,
                "threshold_origin": "fixed_method_default_before_contrast_testing",
                "uses_group_labels_or_test_results": False,
                "filtered_gene_count": len(filtered)
                if genome_wide
                else len(wanted & filtered.keys()),
                "filtered_gene_bh_policy": "excluded"
                if genome_wide
                else "internal_p_one_in_requested_family",
            }
        if selection is not None:
            output["selection_provenance"] = selection
        if selection_engine is not None:
            output["selection_adjustment"] = selection_engine.metadata()
            output["fdr_methods"] = {
                "padj": "benjamini_hochberg",
                "q_value": "benjamini_hochberg",
                "by_q_value": "benjamini_yekutieli",
            }
        return output


def fit_temporal_trajectory(counts_path: str, metadata_path: str, formula_str: str,
                            time_col: str, gene_list: List[str], selection_criterion: str = "aic",
                            spline_dfs: Optional[List[int]] = None, data_type: str = "auto",
                            calibration_max_genes: int = 1000, strict: bool = True,
                            inference_mode: str = "conditional", monte_carlo_draws: int = 65535,
                            monte_carlo_seed: int = 0, *, fdr_scope: str = "requested_genes",
                            gene_offset: int = 0, gene_limit: int = 100,
                            selection_provenance: Optional[dict] = None) -> dict:
    """Fit temporal GLMs with bounded count calibration and per-gene df selection.

``data_type='auto'`` treats floating source data as continuous; ``counts`` is an
explicit assertion of raw count provenance, validated for integer values.
Use ``fixed`` with one spline df for prespecified inference. Default candidates
are 2,3,4,5; AIC/BIC apply to either family, GCV only to Gaussian data. Existing
five positional arguments and per-gene test/coefficient keys are preserved.
The returned warnings and inference_scope are part of the statistical result.
Open count fits use an approximate residual-df F reference with fitted RLE
factors and empirical-Bayes dispersions. Low normalized-mean genes stay
untested. The default requested-gene family retains their conservative BH
positions. Use ``fdr_scope='genome_wide'`` to test and adjust the complete
eligible submitted feature axis. The ``gene_list`` then selects output only,
or supply an empty list and ``gene_offset``/``gene_limit`` for source-order
pagination. Each call recomputes the same complete family for unchanged inputs.
Declared HVG or lineage-derived selection remains exploratory unless a sample
split can rule out overlapping declared units. A split does not authenticate
the input declarations. Caller-supplied thinning claims cannot establish an
independent count partition.
Repeated biological_unit_id/specimen_id/subject_id/donor_id values block inference: strict mode
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
        inference_mode, monte_carlo_draws, monte_carlo_seed),
        kwargs={"exploratory_counts": True, "fdr_scope": fdr_scope, "gene_offset": gene_offset,
                "gene_limit": gene_limit, "selection_provenance": selection_provenance})


def _batch_factor(metadata, column, role):
    if (
        column not in metadata
        or metadata[column].isna().any()
        or metadata[column].str.strip().eq("").any()
    ):
        raise ValueError(
            f"Batch diagnostics require nonmissing {role} metadata in {column!r}"
        )
    # Keep the historical default formula and quote explicit column names.
    return "C(batch)" if column == "batch" else f"C(Q({column!r}))"


def _batch_metadata(path, time_col, batch_col, condition_col):
    categories = (batch_col,) if condition_col is None else (batch_col, condition_col)
    metadata = _bounded_metadata(path, categories)
    observed_time = None
    if time_col is not None:
        if time_col in metadata:
            observed_time = time_col
        elif "numeric_time" in metadata:
            observed_time = "numeric_time"
        elif time_col != "embryonic_age":
            raise ValueError(
                f"Requested sampling-time column is unavailable: {time_col!r}"
            )
    if observed_time is not None:
        if observed_time in categories or "numeric_time" in categories:
            raise ValueError(
                "Sampling-time and categorical roles must use distinct columns"
            )
        metadata["numeric_time"] = parse_stages(metadata[observed_time])
    return metadata, observed_time


def _batch_design(metadata, temporal, batch_col, condition_col):
    from devharness_server.normalization import MAX_PANEL_BYTES

    batch_term = _batch_factor(metadata, batch_col, "batch")
    base_formula = "1"
    parameters = metadata[batch_col].nunique()
    if condition_col is not None:
        base_formula += " + " + _batch_factor(metadata, condition_col, "condition")
        parameters += metadata[condition_col].nunique() - 1
    adjusted_formula = base_formula
    if temporal:
        distinct_times = metadata["numeric_time"].nunique()
        if distinct_times < 3:
            raise ValueError("Natural splines require at least three distinct times")
        df = min(3, distinct_times - 1)
        parameters += df
        adjusted_formula = _spline_formula(base_formula + " + numeric_time", df)
    if len(metadata) * parameters * np.dtype(float).itemsize > MAX_PANEL_BYTES:
        raise ValueError(
            "Batch diagnostic design exceeds the bounded dense design budget"
        )

    formula = adjusted_formula + " + " + batch_term
    second_reduced_formula = (base_formula if temporal else "1") + " + " + batch_term
    return (
        formula,
        _design_matrix(formula, metadata),
        _design_matrix(adjusted_formula, metadata),
        _design_matrix(second_reduced_formula, metadata),
    )


def _unidentifiable_batch_diagnostic(
    batch_levels: int, adjustment: str,
) -> dict[str, str]:
    if batch_levels < 2:
        return {
            "code": "insufficient_batch_levels",
            "severity": "warning",
            "message": "Batch partial R2 requires at least two observed batch levels",
        }
    return {
        "code": f"confounded_or_saturated_batch_{adjustment}_design",
        "severity": "error",
        "message": (
            "Batch partial R2 is unavailable for a confounded or saturated design"
        ),
    }


def _batch_pca(values, metadata, genes, batch_col):
    from sklearn.decomposition import PCA

    n = len(metadata)
    indices = np.linspace(0, n - 1, min(500, n), dtype=int)
    expression = values[:, indices].T
    if len(indices) < 2 or not np.any(expression != expression[0]):
        return None
    pca = PCA(n_components=min(2, len(indices) - 1, len(genes)), svd_solver="full")
    coordinates = pca.fit_transform(expression)
    if (
        not np.isfinite(coordinates).all()
        or not np.isfinite(pca.explained_variance_ratio_).all()
    ):
        raise ValueError("nonfinite_statistical_output: Batch PCA overflowed")
    return {
        "sample_ids": metadata.index[indices].tolist(),
        "batch_labels": metadata[batch_col].iloc[indices].tolist(),
        "coordinates": coordinates.tolist(),
        "variance_ratio": pca.explained_variance_ratio_.tolist(),
        "gene_ids": genes,
        "sample_count": len(indices),
        "source_sample_count": n,
        "sampling": "all" if n == len(indices) else "evenly_spaced_in_expression_order",
    }


def _batch_effects_worker(
    counts_path,
    metadata_path,
    time_col,
    gene_list,
    *,
    batch_col="batch",
    condition_col=None,
    data_type="auto",
    layer=None,
):
    from devharness_server.normalization import (
        calibration_panel,
        expression_summary,
        median_ratio_factors,
    )

    with ChunkedExpressionStore(counts_path, layer=layer) as store:
        metadata, observed_time = _batch_metadata(
            metadata_path, time_col, batch_col, condition_col
        )
        metadata = _align_samples(store, metadata)
        temporal = observed_time is not None
        n = len(metadata)
        formula, full, without_batch, without_adjustment = _batch_design(
            metadata,
            temporal,
            batch_col,
            condition_col,
        )

        genes = (
            list(gene_list)
            if gene_list is not None
            else [
                store.gene_names[i]
                for i in np.linspace(
                    0, store.shape[0] - 1, min(100, store.shape[0]), dtype=int
                )
            ]
        )
        if not genes or len(set(genes)) != len(genes) or len(genes) > 100:
            raise ValueError(
                "Request between 1 and 100 distinct genes for batch diagnostics"
            )
        summary = expression_summary(store, data_type)
        values, panel = calibration_panel(store, genes)
        factors, normalization = None, {"method": "none"}
        if summary["data_type"] == "counts":
            factors, normalization = median_ratio_factors(values, panel["gene_ids"])
            values = np.log1p(values / factors[None, :])
        design_diagnostics = _diagnostics(np.asarray(full), list(full.columns))
        adjusted_rank = _diagnostics(
            np.asarray(without_batch), list(without_batch.columns)
        )["rank"]
        batch_rank = _diagnostics(
            np.asarray(without_adjustment), list(without_adjustment.columns)
        )["rank"]
        full_rank = design_diagnostics["rank"]
        design_identifiable = design_diagnostics["is_full_rank"] and n > full_rank
        batch_increment = full_rank - adjusted_rank
        identifiable = design_identifiable and batch_increment > 0
        diagnostics, replicates = (
            _replication_diagnostics(metadata, batch_col) if temporal else ([], {})
        )
        units, unit_diagnostics = _biological_unit_diagnostics(metadata)
        diagnostics.extend(unit_diagnostics)
        adjustment = (
            "time"
            if temporal
            else "condition"
            if condition_col is not None
            else "intercept"
        )
        if not identifiable:
            diagnostics.append(
                _unidentifiable_batch_diagnostic(
                    metadata[batch_col].nunique(), adjustment,
                )
            )
        result = {}
        positions = {gene: i for i, gene in enumerate(panel["gene_ids"])}
        for gene in genes:
            y = np.asarray(values[positions[gene]], dtype=float)
            total = float(np.sum((y - y.mean()) ** 2))
            rss = {}
            for name, design in (
                ("full", full),
                ("adjusted", without_batch),
                ("batch", without_adjustment),
            ):
                coefficients = np.linalg.lstsq(np.asarray(design), y, rcond=None)[0]
                rss[name] = float(np.sum((y - np.asarray(design) @ coefficients) ** 2))
            if not np.isfinite(total) or not np.isfinite(list(rss.values())).all():
                raise ValueError(
                    f"nonfinite_statistical_output: Batch variance calculation "
                    f"overflowed for gene {gene}"
                )
            r_squared = (
                float(np.clip(1 - rss["full"] / total, 0, 1)) if total > 0 else None
            )

            def partial(reduced, increment, *, rss=rss, total=total):
                return (
                    float(np.clip((rss[reduced] - rss["full"]) / rss[reduced], 0, 1))
                    if design_identifiable
                    and increment > 0
                    and rss[reduced] > np.finfo(float).eps
                    and total > 0
                    else None
                )

            adjustment_r2 = partial("batch", full_rank - batch_rank)
            result[gene] = {
                "full_r2": r_squared,
                "batch_partial_r2": partial("adjusted", batch_increment),
                "time_partial_r2": adjustment_r2 if temporal else None,
                "residual_df": n - full_rank,
                "status": "ok"
                if identifiable and total > 0
                else "not_identifiable"
                if not identifiable
                else "no_information",
            }
            if condition_col is not None and not temporal:
                result[gene]["condition_partial_r2"] = adjustment_r2
        adjusted_name = f"{adjustment}_only"
        batch_name = "batch_only"
        if condition_col is not None and temporal:
            adjusted_name, batch_name = "time_and_condition", "batch_and_condition"
        pca = _batch_pca(
            values[[positions[gene] for gene in genes]], metadata, genes, batch_col
        )
        return {
            "identifiable": bool(identifiable),
            "design": design_diagnostics,
            "formula": formula,
            "reduced_model_ranks": {
                adjusted_name: adjusted_rank,
                batch_name: batch_rank,
            },
            "genes": result,
            "transformation": "log1p_RLE_normalized_counts"
            if factors is not None
            else "none_continuous_input",
            "normalization": normalization,
            "size_factors": dict(zip(store.sample_names, factors.tolist(), strict=False))
            if factors is not None
            else None,
            "gene_scope": "requested_genes"
            if gene_list is not None
            else "at_most_100_evenly_spaced_genes",
            "calibration_panel": panel,
            "diagnostics": diagnostics,
            "stage_replicates": replicates,
            "biological_units": units,
            "pca": pca,
            "batch_col": batch_col,
            "condition_col": condition_col,
            "time_col": observed_time,
            "layer": layer,
            "warnings": [
                record["message"] for record in diagnostics if "message" in record
            ],
            "inference_scope": (
                "Descriptive column-level OLS partial "
                "R2=(RSS_reduced-RSS_full)/RSS_reduced "
                "on the stated expression scale; not a causal batch effect. "
                "Observation counts do not establish biological replication."
            ),
        }


def analyze_batch_effects(
    counts_path: str,
    metadata_path: str,
    time_col: str | None = "embryonic_age",
    gene_list: Optional[List[str]] = None,
    *,
    batch_col: str = "batch",
    condition_col: str | None = None,
    data_type: Literal["counts", "continuous", "auto"] = "auto",
    layer: str | None = None,
) -> dict:
    """Describe batch partial R2 and PCA with rank control in a supervised child.

    Counts use log1p RLE-normalized expression; continuous values are unchanged.
    Pass time_col=None for a non-temporal design, with an optional categorical
    condition_col. The default time role uses the observed legacy time axis when
    available. A named AnnData layer selects layers/<name>; the default selects X.
    Without gene_list, at most 100 evenly spaced genes are reported. Confounded or
    saturated full designs return explicit non-identifiability and null partial R2.
    A single observed batch level also leaves batch partial R2 undefined.
    PCA centers those genes on the stated expression scale and includes up to
    500 observations in expression order. Constant input has no PCA.
    """
    for name, column in (
        ("batch_col", batch_col),
        ("condition_col", condition_col),
        ("time_col", time_col),
    ):
        if column is None and name != "batch_col":
            continue
        if (
            not isinstance(column, str)
            or not column.strip()
            or len(column) > 200
            or "\x00" in column
        ):
            raise ValueError(f"{name} must name a bounded metadata column")
    if batch_col == condition_col:
        raise ValueError(
            "batch_col and condition_col must identify distinct metadata columns"
        )
    return run_sandboxed(
        _batch_effects_worker,
        (
            str(Path(counts_path).expanduser().resolve()),
            str(Path(metadata_path).expanduser().resolve()),
            time_col,
            gene_list,
        ),
        kwargs={
            "batch_col": batch_col,
            "condition_col": condition_col,
            "data_type": data_type,
            "layer": layer,
        },
    )
