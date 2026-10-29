"""Transferable observed-data bulk analyses behind an explicit execution gate.

The methods use public target definitions and observed inputs only. No recipe,
case identifier, generator, private truth or scorer is imported. Numerical code
is unvalidated until separately authorized execution and calibration occur.
"""
from __future__ import annotations

from dataclasses import replace
import math
from pathlib import Path
import re
import time
from typing import Any

from .biology_tool_io import (DESIGN_CONDITION_NUMBER_LIMIT, ObservedData, design_condition_policy,
                              design_matrix_diagnostics, json_safe_result, load_observed_data,
                              require_data_execution, require_design_condition, target_fragment, validate_public_task)

_TOOL_TARGETS = {
    "audit_expression_quality": (),
    "validate_design_matrix": (),
    "fit_temporal_trajectory": ("sampled_mean",),
    "temporal_event_analysis": ("event_timing",),
    "periodic_trajectory_analysis": ("rhythm",),
    "condition_time_analysis": ("condition_main", "time_interaction"),
    "dose_response_analysis": ("dose_curve", "effective_concentration"),
    "mean_condition_analysis": ("mean_contrast", "adjusted_effect", "tissue_effect"),
    "differential_variability_analysis": ("variability",),
    "validate_biological_units": ("unit_accounting",),
    "covariate_response_analysis": ("covariate_effect", "covariate_interaction"),
    "normalization_scale_analysis": ("relative_expression", "absolute_rna"),
    "infer_regulatory_network": ("residual_modules",),
    "regulatory_evidence_audit": ("direct_regulation",),
    "bulk_mixture_deconvolution": ("cell_fractions",),
    "observation_robustness_analysis": ("sampled_mean", "clean_population_effect"),
    "descriptive_expression_summary": ("observed_difference",),
    "model_adequacy_analysis": ("measurement_route",),
}
BULK_TOOL_NAMES = tuple(_TOOL_TARGETS)
_ESTIMATION_LIMITS = {
    "differential_variability_analysis": {"variability": "Count layers currently provide EB dispersion diagnostics only; matched-mean count variance estimation is not implemented."},
    "covariate_response_analysis": {"covariate_effect": "Time-resolved covariate slopes are not implemented.",
                                     "covariate_interaction": "Time-resolved covariate slope interactions are not implemented."},
    "fit_temporal_trajectory": {"sampled_mean": "Repeated biological units across design coordinates require an unimplemented cluster model."},
    "observation_robustness_analysis": {
        "sampled_mean": "Only supported observed-unit response or observation-process diagnostics are available; quality aliasing or selective missingness does not establish clean-population inference.",
        "clean_population_effect": "Only quality-corrected observed-unit summaries or continuous conditional fits are available; clean unselected population recovery is not supplied."},
    "normalization_scale_analysis": {"relative_expression": "Observed library proportion summaries have no population hypothesis tests.",
                                      "absolute_rna": "Verified-control observed summaries omit calibration and population inference uncertainty."},
    "bulk_mixture_deconvolution": {"cell_fractions": "Estimates remain conditional on supplied reference populations and RNA-content calibration; exhaustive coverage and uncertainty are not established."},
}
_TEXT_OPTIONS = {"time_col", "condition_col", "dose_col", "covariate_col", "reference_condition",
                 "comparison_condition", "network_condition", "selection_criterion"}
_KNOWN_OPTIONS = _TEXT_OPTIONS | {"batch_cols", "block_cols", "spline_df", "spline_candidates", "reference_time",
    "frequency_domain", "frequency_count", "damping_grid", "correlation_threshold", "minimum_units",
    "timeout_sec", "max_mem_mb", "covariate_levels", "condition_number_limit"}


def bulk_tool_capabilities() -> dict[str, Any]:
    """Pure registry information. A handler is not a scientific qualification."""
    return {name: {"target_ids": list(targets), "observed_input_analysis": True,
                   "unsupported_estimation_targets": list(_ESTIMATION_LIMITS.get(name, {})),
                   "estimation_limits": dict(_ESTIMATION_LIMITS.get(name, {})),
                   "design_condition_policy": design_condition_policy(),
                   "qualification": "unvalidated_numerical_implementation"}
            for name, targets in _TOOL_TARGETS.items()}


def validate_bulk_options(options: dict[str, Any] | None = None) -> dict[str, Any]:
    from .biology_input_limits import MINIMUM_BIOLOGICAL_UNITS
    if options is None:
        return {}
    if not isinstance(options, dict):
        raise ValueError("options must be a JSON object")
    unknown = set(options) - _KNOWN_OPTIONS
    if unknown:
        raise ValueError("Unknown bulk analysis options: " + ", ".join(sorted(unknown)))
    result = dict(options)
    if "condition_number_limit" in result:
        design_condition_policy(result["condition_number_limit"])
    for name in _TEXT_OPTIONS & result.keys():
        if not isinstance(result[name], str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,160}", result[name]):
            raise ValueError(name + " must be a bounded column/level identifier")
    for name, lo, hi in (("spline_df", 3, 8), ("frequency_count", 3, 40), ("minimum_units", MINIMUM_BIOLOGICAL_UNITS, 100)):
        if name in result and (type(result[name]) is not int or not lo <= result[name] <= hi):
            raise ValueError(name + " is outside its bounded integer range")
    for name in ("spline_candidates", "damping_grid", "covariate_levels", "frequency_domain"):
        if name in result and (not isinstance(result[name], (list, tuple)) or not 1 <= len(result[name]) <= 20):
            raise ValueError(name + " must be a bounded array")
    for name, maximum in (("batch_cols", 20), ("block_cols", 1)):
        if name not in result:
            continue
        if (not isinstance(result[name], (list, tuple)) or len(result[name]) > maximum
                or any(not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,160}", value)
                       for value in result[name]) or len(set(result[name])) != len(result[name])):
            raise ValueError(name + " must contain bounded distinct column identifiers, not formula grammar")
        result[name] = list(result[name])
    if set(result.get("batch_cols", [])) & set(result.get("block_cols", [])):
        raise ValueError("fixed_block_cannot_also_be_declared_as_batch")
    if "spline_candidates" in result and any(type(value) is not int or not 3 <= value <= 8
                                              for value in result["spline_candidates"]):
        raise ValueError("spline_candidates must contain fixed integer df values from 3 to 8")
    if result.get("selection_criterion", "aic") not in {"aic", "bic", "gcv"}:
        raise ValueError("selection_criterion must be aic, bic or gcv")
    for name in ("reference_time", "timeout_sec", "max_mem_mb", "correlation_threshold"):
        if name in result and (isinstance(result[name], bool) or not isinstance(result[name], (float, int))
                               or not math.isfinite(result[name])):
            raise ValueError(name + " must be finite")
    if not 0 < result.get("timeout_sec", 180) <= 300 or not 128 <= result.get("max_mem_mb", 2048) <= 4096:
        raise ValueError("Worker resource options exceed the fixed safety ceilings")
    if not 0 < result.get("correlation_threshold", 0.5) < 1:
        raise ValueError("correlation_threshold must lie strictly between zero and one")
    if "frequency_domain" in result:
        domain = result["frequency_domain"]
        if len(domain) != 2 or any(isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x)
                                  for x in domain) or not 0 < domain[0] < domain[1]:
            raise ValueError("frequency_domain must be finite positive increasing bounds")
    if "damping_grid" in result and any(isinstance(x, bool) or not isinstance(x, (int, float))
            or not math.isfinite(x) or x < 0 for x in result["damping_grid"]):
        raise ValueError("damping_grid must contain finite nonnegative values")
    return result


def analyze_bulk_tool(tool_name: str, public_task: dict[str, Any], *, counts_path: str,
                      metadata_path: str, annotations_path: str, options=None,
                      allow_data_execution: bool = False) -> dict[str, Any]:
    """Start a bounded supervised worker; denial occurs before imports or paths."""
    require_data_execution(allow_data_execution)
    if tool_name not in _TOOL_TARGETS:
        raise ValueError("Unregistered bulk analysis tool")
    config = validate_public_task(public_task)
    settings = validate_bulk_options(options)
    paths = tuple(Path(path) for path in (counts_path, metadata_path, annotations_path))
    if any(not path.is_absolute() for path in paths):
        raise ValueError("Observed inputs require absolute paths")
    from devharness_core.watchdog import run_sandboxed, worker_limits

    timeout = settings.get("timeout_sec", 180)
    memory = settings.get("max_mem_mb", 2048)
    with worker_limits(deadline=time.monotonic() + timeout, max_mem_mb=memory):
        return run_sandboxed(_analyze_bulk_worker,
            (tool_name, config, *(str(path) for path in paths), settings, True),
            timeout_sec=timeout, max_mem_mb=memory)


def _numeric(metadata, column):
    import numpy as np

    if column not in metadata:
        raise ValueError("missing_numeric_column")
    try:
        values = metadata[column].to_numpy(dtype=float)
    except (ValueError, TypeError):
        raise ValueError("nonnumeric_design_coordinate") from None
    if not np.isfinite(values).all():
        raise ValueError("nonfinite_design_coordinate")
    return values


def _column(metadata, settings, option, candidates, required=True):
    specified = settings.get(option)
    if specified:
        if specified not in metadata:
            raise ValueError("missing_declared_" + option)
        return specified
    for name in candidates:
        if name in metadata:
            return name
    if required:
        raise ValueError("missing_" + option)
    return None


def _unit_audit(data: ObservedData, *, design_columns=()) -> dict[str, Any]:
    from devharness_core.units import biological_identity_audit

    metadata, hierarchy = data.metadata, data.unit_hierarchy
    unit, specimen, library = (hierarchy[key] for key in ("population_unit_label", "specimen_label", "library_label"))
    problems = []
    for name in (unit, specimen, library):
        if name not in metadata:
            problems.append("missing_" + name)
        elif any(not str(value).strip() or str(value).lower() in {"unknown", "na", "nan", "none", "unidentified"}
                 for value in metadata[name]):
            problems.append("missing_unit_identity")
    if hierarchy.get("identity_status") in {"intentionally_unknown", "unknown", "unverified"}:
        problems.append("missing_unit_identity")
    if all(name in metadata for name in (unit, specimen, library)):
        if (metadata.groupby(specimen)[unit].nunique() > 1).any():
            problems.append("specimen_crosses_biological_units")
        if (metadata.groupby(library)[specimen].nunique() > 1).any():
            problems.append("library_crosses_specimens")
    identities = biological_identity_audit(metadata.to_dict("records"), unit,
        population_unit=hierarchy["population_unit"], library_label=library, design_columns=design_columns)
    problems.extend(identities["problems"])
    return {"population_unit": hierarchy["population_unit"], "population_unit_label": unit,
            "biological_unit_count": int(metadata[unit].nunique()) if unit in metadata and not problems else None,
            "specimen_count": int(metadata[specimen].nunique()) if specimen in metadata else None,
            "library_count": int(metadata[library].nunique()) if library in metadata else None,
            "observation_count": len(metadata), "identity_and_nesting_valid": not problems,
            "problems": list(dict.fromkeys(problems)),
            "identity_findings": identities["identity_findings"],
            "repeated_identity_columns": identities["repeated_identity_columns"],
            "library_coordinate_findings": identities["library_coordinate_findings"],
            "independence_basis": "public_unit_declaration_and_observed_nesting_not_unique_sample_labels"}


def _independent_view(data: ObservedData, columns: list[str], *, descriptive=False, settings=None):
    """Aggregate memberships per unit, or per explicitly paired unit/condition."""
    import numpy as np
    import pandas as pd

    settings = _public_settings(data, settings or {})
    audit = _unit_audit(data, design_columns=columns)
    metadata = data.metadata
    unit = data.unit_hierarchy["population_unit_label"]
    if not audit["identity_and_nesting_valid"]:
        if descriptive:
            return data, dict(audit, inferential_support=False, aggregation="none")
        raise ValueError(audit["problems"][0])
    columns = list(dict.fromkeys(column for column in columns if column in metadata))
    block = settings.get("block_cols", [])
    if block:
        if data.measurement != "raw_counts":
            raise ValueError("paired_fixed_block_requires_raw_counts")
        if block != [unit]:
            raise ValueError("paired_fixed_block_must_be_declared_biological_unit")
        condition = _column(metadata, settings, "condition_col", ("condition",))
        if condition not in columns:
            raise ValueError("paired_fixed_block_requires_condition_analysis")
        levels = {settings.get("reference_condition"), settings.get("comparison_condition")}
        if None in levels or len(levels) != 2 or set(metadata[condition]) != levels:
            raise ValueError("paired_fixed_block_requires_explicit_two_condition_contrast")
        if any(set(group[condition]) != levels for _, group in metadata.groupby(unit, sort=False)):
            raise ValueError("paired_fixed_block_requires_complete_pairs")
        grouping = [unit, condition]
    else:
        grouping = [unit]
    varying = [column for column in columns if (metadata.groupby(grouping)[column].nunique(dropna=False) > 1).any()]
    if varying:
        if descriptive:
            return data, dict(audit, inferential_support=False, aggregation="none",
                              repeated_unit_covariates=varying)
        raise ValueError("repeated_units_across_design_coordinates_require_cluster_model")
    group_values = list(dict.fromkeys(tuple(row) for row in metadata[grouping].itertuples(index=False, name=None)))
    group_ids = []
    matrices, records = [], []
    specimen = data.unit_hierarchy["specimen_label"]
    for group in group_values:
        positions = np.flatnonzero(np.logical_and.reduce([
            (metadata[column] == value).to_numpy() for column, value in zip(grouping, group)]))
        frame = metadata.iloc[positions]
        group_ids.append(str(frame.index[0]) if block else group[0])
        if data.measurement == "continuous":
            # Equal specimen weights prevent an extra technical library weighting a donor twice.
            specimens = []
            for specimen_id in dict.fromkeys(frame[specimen]):
                local = positions[(frame[specimen] == specimen_id).to_numpy()]
                specimens.append(data.expression[:, local].mean(axis=1))
            matrices.append(np.mean(specimens, axis=0))
        else:
            matrices.append(data.expression[:, positions].sum(axis=1))
        records.append(frame.iloc[0].to_dict())
    values = np.column_stack(matrices)
    if not np.isfinite(values).all() or (data.measurement != "continuous" and (values > 2**53 - 1).any()):
        raise ValueError("aggregated_expression_exceeds_exact_finite_range")
    frame = pd.DataFrame(records, index=group_ids)
    view = replace(data, expression=values, metadata=frame, sample_ids=tuple(group_ids))
    return view, dict(audit, inferential_support=True,
                     analysis_unit_count=int(frame[unit].nunique()), analysis_observation_count=len(frame),
                     paired_block_column=unit if block else None,
                     aggregation=("sum_technical_counts_per_declared_biological_unit_condition" if block else
                         "sum_counts_or_equal_specimen_mean_per_declared_biological_unit"))


def _batch_columns(metadata, settings):
    columns = settings.get("batch_cols", ["batch"] if "batch" in metadata else [])
    if any(column not in metadata for column in columns):
        raise ValueError("missing_declared_batch_column")
    return list(columns)


def _basis(values, df, specification=None):
    import numpy as np
    from scipy.interpolate import BSpline

    values = np.asarray(values, dtype=float)
    if specification is None:
        unique = np.unique(values)
        if len(unique) < df + 1:
            raise ValueError("insufficient_distinct_coordinates_for_spline")
        lower, upper = float(unique[0]), float(unique[-1])
        interior = np.quantile(unique, np.linspace(0, 1, df - 1)[1:-1]) if df > 3 else []
        knots = np.r_[[lower] * 4, interior, [upper] * 4]
        specification = {"knots": knots.tolist(), "df": df}
    knots = np.asarray(specification["knots"], dtype=float)
    if np.any(values < knots[0]) or np.any(values > knots[-1]):
        raise ValueError("prediction_outside_observed_coordinate_support")
    basis = BSpline.design_matrix(values, knots, 3, extrapolate=False).toarray()[:, 1:]
    return basis, specification


def _design(data, mode, settings, df=None):
    """Build only structured numeric contrasts; no user-provided formula is evaluated."""
    import numpy as np
    import pandas as pd

    settings = _public_settings(data, settings)
    metadata = data.metadata
    n = len(metadata)
    columns, names = [np.ones(n)], ["intercept"]
    details = {"mode": mode, "n_observations": n}
    blocks = {}
    condition_col = _column(metadata, settings, "condition_col", ("condition",), required=mode in {"condition", "condition_time", "covariate"})
    condition = None
    if condition_col:
        levels = sorted(set(str(value) for value in metadata[condition_col]))
        reference = settings.get("reference_condition", levels[0])
        comparison = settings.get("comparison_condition", levels[-1])
        if reference not in levels or comparison not in levels:
            raise ValueError("condition_contrast_outside_observed_support")
        if mode in {"condition", "condition_time", "covariate"} and (len(levels) != 2 or reference == comparison):
            raise ValueError("condition_contrast_requires_two_distinct_supported_levels")
        details.update(condition_column=condition_col, condition_levels=levels,
                       reference_condition=reference, comparison_condition=comparison)
        if len(levels) > 1:
            for level in levels:
                if level == reference:
                    continue
                blocks.setdefault("condition", []).append(len(columns))
                columns.append((metadata[condition_col].astype(str) == level).to_numpy(dtype=float))
                names.append("condition_" + level)
            if len(levels) == 2:
                condition = (metadata[condition_col].astype(str) == comparison).to_numpy(dtype=float)
    if mode in {"temporal", "condition_time"}:
        coordinate = _column(metadata, settings, "time_col", ("time", "numeric_time", "embryonic_age"))
        values = _numeric(metadata, coordinate)
        grid = np.unique(values)
        if not 4 <= len(grid) <= 100:
            raise ValueError("temporal_grid_requires_four_to_one_hundred_observed_coordinates")
        reference_time = settings.get("reference_time", float(grid[0]))
        if reference_time < grid[0] or reference_time > grid[-1]:
            raise ValueError("reference_time_outside_observed_support")
        basis, specification = _basis(values, df or settings.get("spline_df", 3))
        at_reference, _ = _basis([reference_time], specification["df"], specification)
        basis = basis - at_reference[0]
        blocks["time"] = list(range(len(columns), len(columns) + basis.shape[1]))
        columns.extend(basis.T)
        names.extend("time_spline_" + str(index) for index in range(basis.shape[1]))
        if mode == "condition_time":
            blocks["time_interaction"] = list(range(len(columns), len(columns) + basis.shape[1]))
            columns.extend((basis * condition[:, None]).T)
            names.extend("condition_time_" + str(index) for index in range(basis.shape[1]))
        details.update(coordinate_column=coordinate, grid=grid.tolist(), reference_time=float(reference_time),
                       spline=specification)
    elif mode == "dose":
        coordinate = _column(metadata, settings, "dose_col", ("dose", "concentration"))
        values = _numeric(metadata, coordinate)
        grid = np.unique(values)
        if not 3 <= len(grid) <= 100 or grid[0] != 0 or np.any(grid < 0):
            raise ValueError("dose_design_requires_vehicle_and_two_positive_doses")
        blocks["dose"] = list(range(len(columns), len(columns) + len(grid) - 1))
        columns.extend((values == value).astype(float) for value in grid[1:])
        names.extend("dose_level_" + str(index) for index in range(1, len(grid)))
        details.update(coordinate_column=coordinate, grid=grid.tolist(), reference_time=0.0)
    elif mode == "covariate":
        observed_time_column = _column(metadata, settings, "time_col", ("time", "numeric_time", "embryonic_age"), False)
        if observed_time_column and len(set(_numeric(metadata, observed_time_column))) > 1:
            raise ValueError("time_resolved_covariate_method_not_implemented")
        coordinate = _column(metadata, settings, "covariate_col", ("genotype_dosage", "sex_covariate", "sex"))
        categorical = False
        try:
            values = _numeric(metadata, coordinate)
        except ValueError:
            categorical = True
            levels = settings.get("covariate_levels", sorted(set(str(x) for x in metadata[coordinate])))
            if len(levels) != 2 or set(levels) != set(metadata[coordinate].astype(str)):
                raise ValueError("covariate_contrast_requires_two_declared_levels")
            values = (metadata[coordinate].astype(str) == levels[1]).to_numpy(dtype=float)
            details["covariate_levels"] = list(levels)
        if len(np.unique(values)) < 2:
            raise ValueError("covariate_has_no_observed_variation")
        for group in np.unique(condition):
            if len(np.unique(values[condition == group])) < 2:
                raise ValueError("covariate_contrast_lacks_within_condition_overlap")
        supports = [set(values[condition == group]) for group in np.unique(condition)]
        if categorical and not all(support == supports[0] for support in supports):
            raise ValueError("covariate_condition_strata_lack_overlap")
        if not categorical and max(min(support) for support in supports) >= min(max(support) for support in supports):
            raise ValueError("covariate_condition_ranges_do_not_overlap")
        blocks["covariate"] = [len(columns)]
        columns.append(values)
        names.append("covariate_slope")
        blocks["covariate_interaction"] = [len(columns)]
        columns.append(condition * values)
        names.append("condition_covariate_slope")
        details.update(coordinate_column=coordinate, covariate_type="categorical" if categorical else "numeric")
    block_columns = settings.get("block_cols", [])
    if block_columns:
        if mode != "condition" or data.measurement != "raw_counts":
            raise ValueError("paired_fixed_block_requires_raw_counts_condition_analysis")
        if block_columns != [data.unit_hierarchy["population_unit_label"]]:
            raise ValueError("paired_fixed_block_must_be_declared_biological_unit")
        for column in block_columns:
            if column not in metadata:
                raise ValueError("missing_declared_block_column")
            levels = sorted(set(metadata[column].astype(str)))
            for level in levels[1:]:
                blocks.setdefault("fixed_block", []).append(len(columns))
                columns.append((metadata[column].astype(str) == level).to_numpy(dtype=float))
                names.append("fixed_block_" + column + "_" + level)
        details.update(block_columns=list(block_columns), block_coding="treatment_indicators_first_sorted_level_reference")
    for column in _batch_columns(metadata, settings):
        levels = sorted(set(metadata[column].astype(str)))
        for level in levels[1:]:
            columns.append((metadata[column].astype(str) == level).to_numpy(dtype=float))
            names.append("nuisance_" + column + "_" + level)
    matrix = np.column_stack(columns)
    details.update(design_matrix_diagnostics(matrix,
        condition_number_limit=settings.get("condition_number_limit", DESIGN_CONDITION_NUMBER_LIMIT)))
    details.update(columns=names, blocks=blocks)
    frame = pd.DataFrame(matrix, index=metadata.index, columns=names)
    frame.attrs["condition_number_limit"] = details["condition_number_limit"]
    return frame, details


def _require_design(design, details, minimum_units=3):
    if details["rank"] != details["column_count"] or details["standardized_rank"] != details["column_count"]:
        raise ValueError("rank_deficient_design")
    if not details["condition_number_validated"]:
        raise ValueError("design_condition_policy_rejected")
    if details["residual_df"] < 2 or len(design) < minimum_units:
        raise ValueError("insufficient_independent_residual_degrees_of_freedom")


def _bh(rows, *, hypothesis_ids=None):
    from devharness_server.tools_stat import bh_adjust

    lookup = {row["hypothesis_id"]: row for row in rows}
    family = list(hypothesis_ids) if hypothesis_ids is not None else list(lookup)
    if len(lookup) != len(rows) or len(family) != len(set(family)) or not set(lookup) <= set(family):
        raise ValueError("multiplicity_family_requires_distinct_all_attempted_hypotheses")
    # Numerical failure is not an independent filter. Unavailable family members
    # contribute conservative internal p=1 positions, while their public p/q stay
    # missing and their estimate status remains failed/unavailable.
    adjustment_values = [lookup[gene]["p_value"] if gene in lookup and lookup[gene].get("p_value") is not None else 1.0
                         for gene in family]
    q_values = bh_adjust(adjustment_values)
    for gene, q_value in zip(family, q_values):
        row = lookup.get(gene)
        if row is not None and row.get("p_value") is not None:
            row["q_value"] = max(row["p_value"], min(1.0, float(q_value)))
    return [row["hypothesis_id"] for row in rows if row.get("p_value") is not None]


def _fit_family(data, design, diagnostics):
    import numpy as np
    import statsmodels.api as sm
    import warnings

    counts = data.measurement != "continuous"
    diagnostics["design_condition"] = require_design_condition(design,
        condition_number_limit=design.attrs.get("condition_number_limit", DESIGN_CONDITION_NUMBER_LIMIT))
    diagnostics["model_family_attempted"] = "negative_binomial" if counts else "gaussian"
    from .biology_input_limits import PUBLIC_MAX_BULK_FEATURES
    if len(data.gene_ids) > PUBLIC_MAX_BULK_FEATURES:
        raise ValueError("complete_gene_family_exceeds_bounded_model_budget")
    factors, dispersions = np.ones(len(data.metadata)), {}
    if counts:
        from devharness_server.normalization import calibrate_counts

        if data.expression.nbytes > 16 * 1024 * 1024:
            raise ValueError("complete_gene_family_exceeds_empirical_bayes_panel_budget")
        panel = {"gene_ids": list(data.gene_ids), "panel_selection": "complete_declared_gene_family",
                 "panel_gene_count": len(data.gene_ids), "total_gene_count": len(data.gene_ids)}
        factors, normalization, calibration, calibration_diagnostics, warnings_list = calibrate_counts(
            data.expression, panel, data.metadata, design, strict=False)
        diagnostics.update(normalization=normalization, calibration=calibration,
                           calibration_diagnostics=calibration_diagnostics, calibration_warnings=warnings_list)
        dispersions = calibration["gene_estimates"]
    fits, failures = {}, {}
    for gene, values in zip(data.gene_ids, data.expression):
        try:
            with warnings.catch_warnings(record=True) as observed_warnings:
                warnings.simplefilter("always")
                if counts:
                    info = dispersions[gene]
                    alpha = info.get("dispersion_alpha")
                    if alpha is None or not info.get("dispersion_converged", False):
                        raise ValueError("dispersion_unavailable_or_nonconverged")
                    fitted = sm.GLM(values, design, family=sm.families.NegativeBinomial(alpha=alpha),
                                    offset=np.log(factors)).fit(maxiter=200, tol=1e-8)
                    if not fitted.converged:
                        raise ValueError("negative_binomial_nonconvergence")
                else:
                    fitted = sm.OLS(values, design).fit()
                    if fitted.scale <= np.finfo(float).tiny:
                        raise ValueError("zero_residual_variance")
            if (not np.isfinite(np.asarray(fitted.params)).all()
                    or not np.isfinite(np.asarray(fitted.cov_params())).all()):
                raise ValueError("nonfinite_model_estimate")
            fits[gene] = fitted
            if observed_warnings:
                diagnostics.setdefault("model_warnings", {})[gene] = list(dict.fromkeys(str(x.message)[:500] for x in observed_warnings))
        except (ValueError, ArithmeticError, RuntimeError, np.linalg.LinAlgError) as error:
            failures[gene] = _error_code(error)
    return fits, failures


def _error_code(error):
    text = str(error).split(":", 1)[0]
    return text if re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{0,159}", text) else "numerical_analysis_failed"


def _failure_kind(code):
    return "failed" if any(part in code for part in (
        "nonconver", "nonfinite", "failed", "dispersion_unavailable", "dependency", "not_implemented",
        "requires_cluster_model", "model_budget", "panel_budget", "periodic_method_",
        "design_condition_policy_rejected",
        "half_maximal_log2_response_requires_declared_log2_continuous_scale")) else "refusal"


def _failure_limitations(code):
    codes = [code]
    if "not_implemented" in code or "requires_cluster_model" in code:
        codes.append("method_not_implemented")
    elif ("periodic_method_" in code or "model_budget" in code or "panel_budget" in code
          or "design_condition_policy_rejected" in code):
        codes.append("method_support_limit")
    return list(dict.fromkeys(codes))


_REFUSAL_CRITERIA = {
    "missing_unit_identity": ("biological_identity_recorded", "unknown", "metadata"),
    "specimen_crosses_biological_units": ("biological_identity_recorded", "violated", "metadata"),
    "library_crosses_specimens": ("biological_identity_recorded", "violated", "metadata"),
    "library_crosses_design_coordinates": ("biological_identity_recorded", "violated", "metadata"),
    "library_identity_unknown": ("biological_identity_recorded", "unknown", "metadata"),
    "library_identity_column_missing": ("biological_identity_recorded", "unknown", "metadata"),
    "insufficient_independent_units_in_observed_design_strata": ("independent_biological_units", "violated", "metadata"),
    "insufficient_independent_residual_degrees_of_freedom": ("independent_biological_units", "violated", "metadata"),
    "rank_deficient_design": ("design_rank_identifiable", "violated", "metadata"),
    "condition_contrast_outside_observed_support": ("contrast_levels_observed", "violated", "metadata"),
    "observed_contrast_requires_two_supported_conditions": ("contrast_levels_observed", "violated", "metadata"),
    "library_relative_contrast_requires_two_observed_conditions": ("contrast_levels_observed", "violated", "metadata"),
    "covariate_has_no_observed_variation": ("covariate_level_support", "violated", "metadata"),
    "covariate_contrast_lacks_within_condition_overlap": ("covariate_condition_overlap", "violated", "metadata"),
    "covariate_condition_strata_lack_overlap": ("covariate_condition_overlap", "violated", "metadata"),
    "covariate_condition_ranges_do_not_overlap": ("covariate_condition_overlap", "violated", "metadata"),
    "candidate_frequency_domain_crosses_sampling_nyquist_limit": ("rhythm_candidate_domain_identifiable", "violated", "metadata"),
    "prespecified_candidate_frequency_domain_required": ("rhythm_candidate_domain_identifiable", "unknown", "public_context"),
    "no_hypothesis_has_identified_half_response_concentration": ("dose_half_response_bracketed", "unknown", "expression"),
    "independent_external_control_amounts_missing": ("absolute_scale_anchor_verified", "unknown", "public_context"),
    "external_control_inputs_not_independently_verified": ("absolute_scale_anchor_verified", "unknown", "declared_external_controls"),
    "external_controls_missing_from_observed_feature_universe": ("absolute_scale_anchor_verified", "violated", "expression"),
    "control_recovery_cannot_be_verified_with_missing_or_single_control": ("absolute_scale_anchor_verified", "unknown", "expression"),
    "validated_mixture_reference_or_stable_markers_missing": ("stable_marker_reference", "unknown", "public_context"),
    "mixture_marker_features_do_not_align_with_observed_reference": ("stable_marker_reference", "violated", "expression"),
    "reference_gene_coverage_incomplete_for_rna_denominator": ("fraction_denominator_defined", "unknown", "cell_type_reference"),
    "mixture_reference_not_well_conditioned_or_distinguishable": ("complete_mixture_reference", "unknown", "cell_type_reference"),
    "clean_population_observation_process_not_identified": ("missingness_assumptions_supported", "unknown", "public_context"),
    "quality_response_separation_requires_independent_susceptibility_reference": ("observed_quality_overlap", "unknown", "metadata"),
    "direct_intervention_and_common_cause_control_missing": ("direct_regulatory_edge_identified", "unknown", "public_context"),
    "empty_observed_library_has_no_relative_expression_denominator": ("fraction_denominator_defined", "violated", "expression"),
}


def _attach_refusal_evidence(data, fragments, diagnostics):
    """Bind only observed validation failures to shared public identification criteria.

This table maps errors raised by input-dependent checks, never case labels or
expected outcomes. An ungrounded refusal becomes a method failure; no generic
reason string can masquerade as a successful scientific diagnosis.
"""
    from .biology_answers import IDENTIFICATION_CRITERIA

    hierarchy_labels = {data.unit_hierarchy[key] for key in ("population_unit_label", "specimen_label", "library_label")}
    for fragment in fragments:
        if fragment["claim_type"] != "refusal":
            continue
        checks = []
        for reason in fragment["limitation_codes"]:
            record = _REFUSAL_CRITERIA.get(reason)
            if reason.startswith("missing_") and reason.removeprefix("missing_") in hierarchy_labels:
                record = ("biological_identity_recorded", "unknown", "metadata")
            if reason.startswith("repeated_biological_unit_") or reason == "biological_unit_is_membership":
                record = ("independent_biological_units", "violated", "metadata")
            if reason.startswith("biological_unit_identity_unknown_"):
                record = ("biological_identity_recorded", "unknown", "metadata")
            if record is None:
                continue
            criterion, status, evidence = record
            if criterion not in IDENTIFICATION_CRITERIA:
                continue
            if evidence not in {"metadata", "expression", "public_context"} and evidence not in data.annotations:
                evidence = "public_context"
            check = {"code": criterion, "status": status, "evidence_ids": [evidence],
                     "explanation": "Observed input check reported " + reason + ". " + IDENTIFICATION_CRITERIA[criterion]}
            if not any(existing["code"] == criterion for existing in checks):
                checks.append(check)
        if checks:
            fragment["support_checks"] = checks
            diagnostics.setdefault("identification_findings", {})[fragment["target_id"]] = checks
        else:
            fragment["claim_type"] = "failed"
            fragment["estimate_status"] = "failed"
            fragment["limitation_codes"].append("method_could_not_ground_scientific_refusal")
            diagnostics.setdefault("ungrounded_refusal_converted_to_failure", []).append(fragment["target_id"])


def _linear_estimate(fitted, contrast, discrete):
    import numpy as np
    from scipy.stats import norm, t

    contrast = np.atleast_2d(contrast)
    scale = math.log(2) if discrete else 1.0
    values = contrast @ np.asarray(fitted.params) / scale
    covariance = contrast @ np.asarray(fitted.cov_params()) @ contrast.T / scale**2
    if not np.isfinite(values).all() or not np.isfinite(covariance).all() or (np.diag(covariance) < 0).any():
        raise ValueError("nonfinite_contrast_estimate")
    quantile = norm.ppf(0.975) if discrete else t.ppf(0.975, fitted.df_resid)
    errors = np.sqrt(np.diag(covariance)) * quantile
    intervals = [{"lower": float(value - error), "upper": float(value + error), "confidence_level": 0.95}
                 for value, error in zip(values, errors)]
    independent = []
    for row in contrast:
        if np.linalg.matrix_rank(np.asarray(independent + [row])) > len(independent):
            independent.append(row)
    if not independent:
        raise ValueError("requested_contrast_has_no_estimable_nonzero_direction")
    test_contrast = np.asarray(independent)
    p_value = float(fitted.wald_test(test_contrast, scalar=True).pvalue) if discrete else float(fitted.f_test(test_contrast).pvalue)
    if not math.isfinite(p_value):
        raise ValueError("nonfinite_contrast_test")
    return values.tolist(), intervals, min(1.0, max(0.0, p_value))


def _effects_payload(data, target, rows, curves, details, *, selection=False, observed=False, scale_anchor=None,
                     computed_contrast=None):
    # Serialize the public condition/grid target, including algebraic zero
    # contrasts at the reference point. A constant-effect model predicts the
    # same coefficient at every grid position; this is not a new fitted value.
    design = data.context.get("design", {})
    grid = design.get("dose_grid") or design.get("time_grid") or [0.0]
    conditions = design.get("condition_levels", data.context.get("condition_labels", ["observed"]))
    full_curves = target["target_id"] in {"sampled_mean", "clean_population_effect", "dose_curve"}
    prediction_structure = "coordinate_specific"
    for row in rows:
        if row["estimate_status"] != "estimated":
            continue
        values, intervals = list(row["values"]), list(row.get("intervals", []))
        if full_curves and len(values) == len(grid)-1 and row.get("coordinate_ids") == [f"grid_{i}" for i in range(1,len(grid))]:
            values.insert(0, 0.0)
            if intervals:
                intervals.insert(0, {"lower": 0.0, "upper": 0.0, "confidence_level": 0.95})
        if full_curves and len(values) == len(grid):
            if len(conditions)>1:
                prediction_structure = "shared_across_conditions"
            values *= len(conditions)
            intervals *= len(conditions)
        elif not full_curves and target["target_id"] not in {"condition_main", "observed_difference", "variability"} and len(values) == 1 and len(grid)>1:
            prediction_structure = "shared_across_grid"
            values *= len(grid)
            intervals *= len(grid)
        row.update(values=values, intervals=intervals)
        if target["target_id"] != "time_interaction":
            row["coordinate_ids"] = [f"grid_{i}" for i in range(len(values))] if len(values)>1 else ["contrast"]
    for curve in curves:
        if full_curves and curve["estimate_status"] == "estimated" and len(curve["values"]) == len(grid):
            curve["values"] *= len(conditions)
            curve["intervals"] *= len(conditions)
            curve["coordinate_ids"] = [f"grid_{i}" for i in range(len(curve["values"]))]
    family = _bh(rows)
    payload = {"kind": "effects", "analysis_performed": True, "gene_statistics": rows,
            "curve_estimates": curves, "multiplicity_scope": family, "selection_conditional_inference": selection,
            "biological_unit": "observed_library" if observed else data.unit_hierarchy["population_unit"],
            "analysis_layer": data.measurement, "effect_definition": target["contrast"],
            "prediction_structure": prediction_structure,
            "reference_condition": details.get("reference_condition"),
            "reference_time": details.get("reference_time"),
            "covariate_contrast": target["contrast"] if target["target_id"].startswith("covariate") else None,
            "scale_anchor": scale_anchor, "condition_number_validated": details.get("condition_number_validated"),
            "condition_number": details.get("condition_number"), "design_rank": details.get("rank"),
            "design_column_count": details.get("column_count")}
    if computed_contrast is not None:
        payload["computed_contrast"] = computed_contrast.model_dump(mode="json")
        payload["effect_definition"] = computed_contrast.describe()
    return payload


def _unavailable_row(gene, reason, *, failed=True):
    return {"hypothesis_id": gene, "gene_id": gene, "estimate_status": "failed" if failed else "unavailable",
            "coordinate_ids": [], "values": [], "intervals": [], "p_value": None, "q_value": None,
            "missing_reason": reason}


def _multiplicity_provenance(data, diagnostics):
    diagnostics["gene_test_adjustment_family"] = {
        "attempted_hypothesis_ids": list(data.gene_ids), "attempted_count": len(data.gene_ids),
        "adjustment": "Benjamini_Hochberg_over_all_attempted_hypotheses",
        "unavailable_hypothesis_policy": "internal_conservative_p_one_keeps_family_position_public_p_q_remain_missing",
        "reported_multiplicity_scope": "successfully_tested_hypotheses_only_as_required_by_answer_schema"}


def _mode_columns(data, mode, settings):
    columns = _batch_columns(data.metadata, settings)
    for option, candidates, required in (("condition_col", ("condition",), mode in {"condition", "condition_time", "covariate"}),
        ("time_col", ("time", "numeric_time", "embryonic_age"), mode in {"temporal", "condition_time"}),
        ("dose_col", ("dose", "concentration"), mode == "dose"),
        ("covariate_col", ("genotype_dosage", "sex_covariate", "sex"), mode == "covariate")):
        if required or option == "condition_col" or (mode == "covariate" and option == "time_col"):
            column = _column(data.metadata, settings, option, candidates, required=required)
            if column:
                columns.append(column)
    return columns


def _check_replication(data, details, settings):
    columns = [details[key] for key in ("condition_column", "coordinate_column") if key in details]
    if columns:
        unit = data.unit_hierarchy["population_unit_label"]
        sizes = data.metadata.groupby(columns, dropna=False)[unit].nunique()
        if int(sizes.min()) < settings.get("minimum_units", 3):
            raise ValueError("insufficient_independent_units_in_observed_design_strata")


def _prediction_rows(details, column_count):
    import numpy as np

    grid = details.get("grid", [0.0])
    rows = np.zeros((len(grid), column_count))
    rows[:, 0] = 1
    if "spline" in details:
        basis, _ = _basis(grid, details["spline"]["df"], details["spline"])
        reference, _ = _basis([details["reference_time"]], details["spline"]["df"], details["spline"])
        rows[:, details["blocks"]["time"]] = basis - reference[0]
    if "dose" in details["blocks"]:
        for index, column in enumerate(details["blocks"]["dose"], 1):
            rows[index, column] = 1
    return rows


def _contrasts(target_id, details, column_count):
    import numpy as np

    block = {"mean_contrast": "condition", "adjusted_effect": "condition", "tissue_effect": "condition",
             "condition_main": "condition", "covariate_effect": "covariate",
             "covariate_interaction": "covariate_interaction", "relative_expression": "condition",
             "absolute_rna": "condition"}.get(target_id)
    if block:
        indices = details["blocks"].get(block, [])
        if len(indices) != 1:
            raise ValueError("requested_contrast_is_not_one_supported_design_coefficient")
        return np.eye(column_count)[indices], ["contrast"]
    predicted = _prediction_rows(details, column_count)
    reference_prediction = predicted[0]
    if target_id != "dose_curve":
        reference_details = dict(details, grid=[details.get("reference_time", details.get("grid", [0.0])[0])])
        reference_prediction = _prediction_rows(reference_details, column_count)[0]
    if target_id in {"sampled_mean", "clean_population_effect"}:
        contrasts = predicted - reference_prediction
    elif target_id == "dose_curve":
        contrasts = predicted - predicted[0]
    elif target_id == "time_interaction":
        contrasts = np.zeros_like(predicted)
        contrasts[:, details["blocks"]["time_interaction"]] = predicted[:, details["blocks"]["time"]]
    else:
        raise ValueError("effect_contrast_not_implemented")
    if not contrasts.size or not np.any(contrasts):
        raise ValueError("empty_observed_grid_contrast")
    # Values retain every actual requested grid coordinate. _linear_estimate uses
    # an independent subset only for the omnibus test, never to relabel estimates.
    retained = ([index for index, row in enumerate(contrasts) if np.any(np.abs(row) > 1e-12)]
                if target_id == "time_interaction" else list(range(len(contrasts))))
    return contrasts[retained], ["grid_" + str(index) for index in retained]


def _curve_row(gene, fitted, details, discrete, *, mean_scale=False):
    import numpy as np
    from scipy.stats import norm, t

    prediction = _prediction_rows(details, len(fitted.params))
    scale = math.log(2) if discrete else 1.0
    predicted = prediction @ np.asarray(fitted.params) / scale
    covariance = prediction @ np.asarray(fitted.cov_params()) @ prediction.T / scale**2
    quantile = norm.ppf(0.975) if discrete else t.ppf(0.975, fitted.df_resid)
    errors = quantile * np.sqrt(np.maximum(0, np.diag(covariance)))
    if not np.isfinite(predicted).all() or not np.isfinite(errors).all():
        raise ValueError("nonfinite_curve_estimate")
    lower, upper = predicted-errors, predicted+errors
    if discrete and mean_scale:
        predicted, lower, upper = np.exp2(predicted), np.exp2(lower), np.exp2(upper)
        if not all(np.isfinite(values).all() for values in (predicted, lower, upper)):
            raise ValueError("nonfinite_count_mean_prediction")
    return {"hypothesis_id": gene, "gene_id": gene, "estimate_status": "estimated",
            "coordinate_ids": ["grid_" + str(index) for index in range(len(predicted))],
            "values": predicted.tolist(), "intervals": [
                {"lower": float(lo), "upper": float(hi), "confidence_level": 0.95}
                for lo, hi in zip(lower, upper)], "p_value": None, "q_value": None}


def _fit_effect_targets(data, targets, mode, settings, diagnostics):
    settings = _public_settings(data, settings)
    view, units = _independent_view(data, _mode_columns(data, mode, settings), settings=settings)
    diagnostics["biological_units"] = units
    _multiplicity_provenance(view, diagnostics)
    candidates = settings.get("spline_candidates", [settings.get("spline_df", 3)]) if mode in {"temporal", "condition_time"} else [None]
    candidates = list(dict.fromkeys(candidates))
    criterion = settings.get("selection_criterion", "aic")
    if criterion == "gcv" and view.measurement != "continuous":
        raise ValueError("gcv_not_available_for_negative_binomial_likelihood")
    successful, candidate_errors = [], {}
    for degree in candidates:
        candidate_diagnostics = {}
        try:
            design, details = _design(view, mode, settings, degree)
            _require_design(design, details)
            _check_replication(view, details, settings)
            fits, failures = _fit_family(view, design, candidate_diagnostics)
            successful.append((fits, failures, design, details, candidate_diagnostics))
        except (ValueError, ArithmeticError, RuntimeError) as error:
            candidate_errors[str(degree)] = _error_code(error)
    diagnostics["candidate_errors"] = candidate_errors
    if not successful:
        raise ValueError(next(iter(candidate_errors.values()), "no_feasible_model"))
    selected, scores, candidate_scores = {}, {}, {}
    for gene in view.gene_ids:
        choices = []
        for candidate_index, (fits, _, design, _, _) in enumerate(successful):
            if gene not in fits:
                continue
            fit = fits[gene]
            # Count EB dispersion as one nuisance parameter for information criteria.
            parameter_count = len(fit.params) + 1
            aic = -2 * float(fit.llf) + 2 * parameter_count
            bic = -2 * float(fit.llf) + math.log(len(design)) * parameter_count
            gcv = (len(design) * float(sum(fit.resid**2)) / fit.df_resid**2
                   if view.measurement == "continuous" else None)
            value = {"aic": aic, "bic": bic, "gcv": gcv}[criterion]
            candidate_scores.setdefault(gene, []).append({
                "candidate_index": candidate_index,
                "spline_df": successful[candidate_index][3].get("spline", {}).get("df"),
                "aic": aic if math.isfinite(aic) else None,
                "bic": bic if math.isfinite(bic) else None,
                "gcv": gcv if gcv is not None and math.isfinite(gcv) else None})
            if value is not None and math.isfinite(value):
                choices.append((value, candidate_index))
        if choices:
            score, selected_index = min(choices)
            selected[gene] = selected_index
            scores[gene] = {"criterion": criterion, "score": score,
                            "spline_df": successful[selected_index][3].get("spline", {}).get("df")}
    diagnostics["selected_models"] = scores
    diagnostics["model_selection"] = {"criterion": criterion, "requested_spline_candidates": candidates,
        "candidate_scores": candidate_scores, "parameter_count_convention": "regression_coefficients_plus_one_scale_or_dispersion",
        "model_selection_performed": len(candidates) > 1,
        "normalization_and_dispersion_estimated": view.measurement != "continuous",
        "uncertainty_scope": ("selected_model" if len(candidates) > 1 else "prespecified_model")
            + ("_with_empirical_bayes_plugin_uncertainty" if view.measurement != "continuous" else "_under_gaussian_assumptions"),
        "empirical_calibration_established": False}
    diagnostics["designs"] = [entry[3] for entry in successful]
    if len(successful) == 1:
        diagnostics.update(successful[0][4])
    else:
        diagnostics["candidate_calibrations"] = [entry[4] for entry in successful]
    diagnostics["prediction_scale"] = "log2_relative_expression" if view.measurement != "continuous" else "unchanged_input_expression"
    selection = len(candidates) > 1 or view.measurement != "continuous"
    fragments = []
    for target in targets:
        rows, curves = [], []
        for gene in view.gene_ids:
            if gene not in selected:
                reason = next((entry[1][gene] for entry in successful if gene in entry[1]), "all_candidate_fits_failed")
                rows.append(_unavailable_row(gene, reason))
                continue
            candidate_index = selected[gene]
            fitted, _, design, details, _ = successful[candidate_index]
            try:
                contrast, coordinate_ids = _contrasts(target["target_id"], details, len(design.columns))
                values, intervals, p_value = _linear_estimate(fitted[gene], contrast, view.measurement != "continuous")
                curve = (_curve_row(gene, fitted[gene], details, view.measurement != "continuous", mean_scale=True)
                         if "grid" in details and target["target_id"] in {"sampled_mean", "clean_population_effect", "dose_curve"}
                         else None)
                row = {"hypothesis_id": gene, "gene_id": gene, "estimate_status": "estimated",
                       "coordinate_ids": coordinate_ids, "values": values, "intervals": intervals,
                       "p_value": p_value, "q_value": p_value}
                rows.append(row)
                if curve is not None:
                    curves.append(curve)
            except (ValueError, ArithmeticError, RuntimeError) as error:
                rows.append(_unavailable_row(gene, _error_code(error)))
        if not any(row["estimate_status"] == "estimated" for row in rows):
            fragments.append(target_fragment(target, claim_type="failed", evidence_scope="not_identified",
                                             limitation_codes=["all_gene_contrasts_failed"]))
            diagnostics.setdefault("failed_gene_statistics", {})[target["target_id"]] = rows
            continue
        limits = ["declared_unit_identity_assumed", "model_assumptions_not_independently_validated"]
        if selection:
            limits.append("selection_conditional_or_empirical_bayes_plugin_inference")
        computed_contrast = None
        if mode == "condition" and target["target_id"] in {
                "mean_contrast", "adjusted_effect", "relative_expression", "tissue_effect"}:
            from .biology_answers import ComputedConditionContrast
            fitted_details = successful[0][3]
            computed_contrast = ComputedConditionContrast.from_measurement(
                view.measurement, fitted_details["reference_condition"], fitted_details["comparison_condition"])
        payload = _effects_payload(view, target, rows, curves, successful[0][3], selection=selection,
                                   computed_contrast=computed_contrast)
        scope = "population_mean"
        if target["target_id"] == "relative_expression":
            scope = "observed_difference"
        fragments.append(target_fragment(target, claim_type="restricted_estimate", evidence_scope=scope,
                                         payload=payload, limitation_codes=limits))
    return fragments


def _normalized(data, diagnostics):
    if data.measurement == "continuous":
        diagnostics["normalization"] = {"method": "unchanged_continuous_input", "count_conversion": False}
        return data.expression
    from devharness_server.normalization import median_ratio_factors

    factors, normalization = median_ratio_factors(data.expression, data.gene_ids)
    diagnostics["normalization"] = normalization
    return data.expression / factors[None, :]


def _descriptive_targets(data, targets, settings, diagnostics):
    import numpy as np

    condition = _column(data.metadata, settings, "condition_col", ("condition",))
    view, units = _independent_view(data, [condition], descriptive=True)
    diagnostics["biological_units"] = units
    levels = sorted(set(view.metadata[condition].astype(str)))
    reference, comparison = settings.get("reference_condition", levels[0]), settings.get("comparison_condition", levels[-1])
    if reference == comparison or reference not in levels or comparison not in levels:
        raise ValueError("observed_contrast_requires_two_supported_conditions")
    normalized = _normalized(view, diagnostics)
    first = normalized[:, (view.metadata[condition] == reference).to_numpy()].mean(axis=1)
    second = normalized[:, (view.metadata[condition] == comparison).to_numpy()].mean(axis=1)
    rows = []
    for gene, left, right in zip(view.gene_ids, first, second):
        if view.measurement != "continuous" and (left <= 0 or right <= 0):
            rows.append(_unavailable_row(gene, "zero_group_mean_log_ratio_unavailable", failed=False))
            continue
        value = float(right-left) if view.measurement == "continuous" else float(np.log2(right/left))
        rows.append({"hypothesis_id": gene, "gene_id": gene, "estimate_status": "estimated",
                     "coordinate_ids": ["observed_condition_contrast"], "values": [value],
                     "intervals": [], "p_value": None, "q_value": None})
    details = {"reference_condition": reference}
    return [target_fragment(target, claim_type="restricted_estimate", evidence_scope="observed_difference",
        payload=_effects_payload(view, target, rows, [], details, observed=not units.get("inferential_support")),
        limitation_codes=["descriptive_observed_allocations_not_batch_adjusted", "no_population_hypothesis_test"])
        for target in targets]


def _measurement_audit(data):
    import numpy as np

    values = data.expression
    positive_totals = (values.sum(axis=0) > 0).tolist() if data.measurement != "continuous" else None
    return {"measurement": data.measurement, "analysis_route": data.provenance["analysis_route"],
            "gene_count": len(data.gene_ids), "observation_count": len(data.sample_ids),
            "finite": bool(np.isfinite(values).all()), "zero_fraction": float(np.mean(values == 0)),
            "constant_gene_count": int(np.count_nonzero(np.ptp(values, axis=1) == 0)),
            "negative_value_count": int(np.count_nonzero(values < 0)),
            "library_has_positive_total": positive_totals, "provenance": data.provenance,
            "count_conversion_performed": False}


def _diagnostic_fragment(target, data, values, units=None):
    scalar = [{"code": code, "value": value} for code, value in values.items()
              if value is None or isinstance(value, (str, int, float, bool))]
    return target_fragment(target, claim_type="supported_estimate", evidence_scope="measurement_diagnostic",
        payload={"kind": "diagnostics", "diagnostics": scalar[:100], "biological_unit_count": units,
                 "measurement_type": data.measurement, "analysis_route": data.provenance["analysis_route"],
                 "model_family": "gaussian" if data.measurement == "continuous" else "negative_binomial",
                 "residual_diagnostics": [{"code": "residual_check_status", "value": None,
                     "missing_reason": "no_model_fitted_in_measurement_route_audit"}],
                 "analysis_layer": "continuous" if data.measurement == "continuous" else "counts",
                 "transformation_history": data.provenance.get("transformation_history", [])},
        limitation_codes=["diagnostic_is_not_biological_or_clinical_validation", "model_family_is_a_candidate_not_a_validated_fit"])


def _variability(data, targets, settings, diagnostics):
    import numpy as np
    from scipy.stats import levene

    view, units = _independent_view(data, _mode_columns(data, "condition", settings))
    diagnostics["biological_units"] = units
    design, details = _design(view, "condition", settings)
    _require_design(design, details)
    _check_replication(view, details, settings)
    conditions = view.metadata[details["condition_column"]].astype(str)
    masks = [(conditions == details[key]).to_numpy() for key in ("reference_condition", "comparison_condition")]
    rows = []
    if view.measurement == "continuous":
        _multiplicity_provenance(view, diagnostics)
        fits, failures = _fit_family(view, design, diagnostics)
        for gene in view.gene_ids:
            if gene not in fits:
                rows.append(_unavailable_row(gene, failures[gene]))
                continue
            residual = np.asarray(fits[gene].resid)
            left, right = residual[masks[0]], residual[masks[1]]
            variances = [float(np.var(group, ddof=1)) for group in (left, right)]
            if min(variances) <= 0:
                rows.append(_unavailable_row(gene, "zero_residual_variance", failed=False))
                continue
            p_value = float(levene(left, right, center="median").pvalue)
            if not math.isfinite(p_value):
                rows.append(_unavailable_row(gene, "variability_test_nonfinite"))
                continue
            rows.append({"hypothesis_id": gene, "gene_id": gene, "estimate_status": "estimated",
                         "values": [math.log(variances[1] / variances[0])], "coordinate_ids": ["log_variance_ratio"],
                         "intervals": [], "p_value": p_value, "q_value": p_value})
        limits = ["brown_forsythe_asymptotic_reference_after_mean_adjustment", "no_variance_ratio_interval"]
    else:
        calibrations = []
        for index, mask in enumerate(masks):
            subgroup = replace(view, expression=view.expression[:, mask], metadata=view.metadata.loc[mask].copy(),
                               sample_ids=tuple(np.asarray(view.sample_ids)[mask]))
            subgroup_design, subgroup_details = _design(subgroup, "intercept", settings)
            _require_design(subgroup_design, subgroup_details)
            local = {}
            _fit_family(subgroup, subgroup_design, local)
            diagnostics["condition_calibration_" + str(index)] = local
            calibrations.append(local["calibration"]["gene_estimates"])
        for gene in view.gene_ids:
            records = [calibration[gene] for calibration in calibrations]
            alphas = [record.get("dispersion_alpha") for record in records]
            if any(alpha is None or alpha <= 0 or not record.get("dispersion_converged")
                   for alpha, record in zip(alphas, records)):
                rows.append(_unavailable_row(gene, "condition_dispersion_unavailable"))
                continue
            rows.append({"hypothesis_id": gene, "gene_id": gene, "estimate_status": "estimated",
                         "values": [math.log(alphas[1] / alphas[0])], "coordinate_ids": ["log_dispersion_ratio"],
                         "intervals": [], "p_value": None, "q_value": None})
        limits = ["empirical_bayes_dispersion_ratio_not_a_calibrated_variability_test",
                  "count_dispersion_is_distinct_from_raw_variance_at_different_means"]
        diagnostics["condition_dispersion_ratio_diagnostics"] = rows
        diagnostics["design"] = details
        return [target_fragment(target, claim_type="failed", evidence_scope="not_identified",
            limitation_codes=["method_not_implemented", "matched_mean_count_variance_estimator_not_implemented",
                              "dispersion_ratio_is_not_the_requested_variance_ratio"])
            for target in targets]
    diagnostics["design"] = details
    return [target_fragment(target, claim_type="restricted_estimate", evidence_scope="population_mean",
        payload=_effects_payload(view, target, rows, [], details, selection=view.measurement != "continuous"),
        limitation_codes=limits) for target in targets]


def _library_relative_targets(data, targets, settings, diagnostics):
    import numpy as np

    if data.measurement == "continuous":
        raise ValueError("observed_library_count_denominator_unavailable_for_continuous_layer")
    condition = _column(data.metadata, settings, "condition_col", ("condition",))
    view, units = _independent_view(data, [condition], descriptive=True)
    total = view.expression.sum(axis=0)
    if (total <= 0).any():
        raise ValueError("empty_observed_library_has_no_relative_expression_denominator")
    proportions = view.expression/total[None, :]
    levels = sorted(set(view.metadata[condition].astype(str)))
    reference, comparison = settings.get("reference_condition", levels[0]), settings.get("comparison_condition", levels[-1])
    if reference not in levels or comparison not in levels or reference == comparison:
        raise ValueError("library_relative_contrast_requires_two_observed_conditions")
    first = proportions[:, (view.metadata[condition].astype(str) == reference).to_numpy()].mean(axis=1)
    second = proportions[:, (view.metadata[condition].astype(str) == comparison).to_numpy()].mean(axis=1)
    rows = []
    for gene, left, right in zip(view.gene_ids, first, second):
        if left <= 0 or right <= 0:
            rows.append(_unavailable_row(gene, "zero_mean_relative_abundance", failed=False))
            continue
        rows.append({"hypothesis_id": gene, "gene_id": gene, "estimate_status": "estimated",
                     "coordinate_ids": ["log2_ratio_of_mean_library_proportions"],
                     "values": [float(np.log2(right/left))], "intervals": [], "p_value": None, "q_value": None})
    diagnostics["biological_units"] = units
    diagnostics["relative_expression_denominator"] = {
        "definition": "sum_of_counts_over_complete_observed_feature_universe_in_each_analysis_library",
        "feature_count": len(view.gene_ids), "declared_controls_in_denominator": len(
            data.annotations.get("declared_external_controls", {}).get("gene_ids", [])),
        "condition_summary": "arithmetic_mean_of_unit_or_observed_library_proportions",
        "reference_centering": "none", "hypothesis_tests_performed": False}
    return [target_fragment(target, claim_type="restricted_estimate", evidence_scope="observed_difference",
        payload=_effects_payload(view, target, rows, [], {"reference_condition": reference},
                                 observed=not units.get("inferential_support")),
        limitation_codes=["observed_library_proportions_not_absolute_abundance", "no_population_hypothesis_test",
                          "descriptive_observed_allocations_not_batch_adjusted"])
        for target in targets]


def _network(data, targets, settings, diagnostics):
    import numpy as np

    temporal = _column(data.metadata, settings, "time_col", ("time", "numeric_time", "embryonic_age"), False)
    mode = "temporal" if temporal and len(set(data.metadata[temporal])) >= 4 else "intercept"
    view, units = _independent_view(data, _mode_columns(data, mode, settings))
    diagnostics["biological_units"] = units
    design, details = _design(view, mode, settings)
    _require_design(design, details)
    if len(view.gene_ids) > 2500:
        raise ValueError("complete_network_feature_family_exceeds_covariance_budget")
    normalized = _normalized(view, diagnostics)
    signal = np.log2(normalized + 1.0) if view.measurement != "continuous" else normalized.copy()
    if view.measurement == "continuous" and not str(view.provenance.get("measurement_declaration", {}).get("units", "")).lower().startswith("log"):
        raise ValueError("network_log_expression_scale_not_declared_for_continuous_layer")
    residual = signal - (np.asarray(design) @ np.linalg.lstsq(np.asarray(design), signal.T, rcond=None)[0]).T
    condition_col = details.get("condition_column")
    condition = settings.get("network_condition", details.get("reference_condition"))
    mask = np.ones(len(view.metadata), dtype=bool) if condition_col is None else (view.metadata[condition_col].astype(str) == condition).to_numpy()
    if int(mask.sum()) < 4:
        raise ValueError("insufficient_independent_condition_observations_for_residual_network")
    local = residual[:, mask]
    standard = np.std(local, axis=1, ddof=1)
    eligible = np.flatnonzero(standard > np.finfo(float).eps)
    standardized = (local[eligible] - local[eligible].mean(axis=1, keepdims=True)) / standard[eligible, None]
    correlations = standardized @ standardized.T / (local.shape[1]-1)
    threshold = settings.get("correlation_threshold", 0.5)
    ordered_genes = sorted(view.gene_ids)
    declared_pairs = {tuple(sorted((gene, ordered_genes[(index+offset) % len(ordered_genes)])))
                      for index, gene in enumerate(ordered_genes) for offset in (1, 2, 4, 8)
                      if len(ordered_genes)>offset}
    edges, adjacency = [], {int(index): set() for index in eligible}
    for a in range(len(eligible)):
        for b in range(a + 1, len(eligible)):
            correlation = float(max(-1, min(1, correlations[a, b])))
            left, right = int(eligible[a]), int(eligible[b])
            pair = tuple(sorted((view.gene_ids[left], view.gene_ids[right])))
            if pair in declared_pairs:
                edges.append({"source": pair[0], "target": pair[1], "covariance": correlation})
            if abs(correlation) >= threshold:
                adjacency[left].add(right)
                adjacency[right].add(left)
    if len(edges) > 100_000:
        raise ValueError("residual_network_edges_exceed_explicit_answer_bound")
    memberships, visited, module_index = {}, set(), 0
    for root in eligible:
        root = int(root)
        if root in visited or not adjacency[root]:
            continue
        pending = [root]
        visited.add(root)
        while pending:
            gene_index = pending.pop()
            # Membership sign is an observed correlation to an explicitly chosen module root.
            root_position = int(np.flatnonzero(eligible == root)[0])
            gene_position = int(np.flatnonzero(eligible == gene_index)[0])
            sign = "positive" if correlations[root_position, gene_position] >= 0 else "negative"
            memberships[gene_index] = "module_" + str(module_index) + "_" + sign
            for neighbor in adjacency[gene_index] - visited:
                visited.add(neighbor)
                pending.append(neighbor)
        module_index += 1
    tf_ids = data.context.get("transcription_factor_annotation", [])
    candidates = [gene for gene in tf_ids if gene in view.gene_ids and any(
        gene in (edge["source"], edge["target"]) for edge in edges)]
    diagnostics.update(design=details, network_condition=condition, correlation_threshold=threshold,
                       retained_variable_gene_count=len(eligible), residual_observations=int(mask.sum()))
    payload = {"kind": "association", "association_edges": edges,
               "module_membership": [{"gene_id": gene, "module_id": memberships.get(index)}
                                     for index, gene in enumerate(view.gene_ids)],
               "candidate_regulators": candidates, "candidate_universe": list(tf_ids),
               "covariance_scale": "log_expression_signal_correlation", "condition": condition,
               "claims_direct_causality": False}
    return [target_fragment(target, claim_type="restricted_estimate", evidence_scope="association", payload=payload,
        limitation_codes=["observed_residual_correlation_includes_measurement_noise", "association_not_direct_regulation",
                          "threshold_selected_modules_have_no_calibrated_membership_probability"])
        for target in targets]


def _absolute_scale(data, targets, settings, diagnostics):
    import numpy as np

    controls = data.annotations.get("declared_external_controls")
    if not isinstance(controls, dict):
        raise ValueError("independent_external_control_amounts_missing")
    ids = controls.get("gene_ids", [])
    abundance = np.asarray(controls.get("reference_abundance", []), dtype=float)
    verification = controls.get("input_verification", {})
    diagnostics["scale_verification"] = {
        "declared_control_count": len(ids), "input_status": verification.get("status"),
        "input_source": verification.get("source"), "quantity_scope": verification.get("quantity_scope"),
        "absolute_scale_is_per_cell": False}
    if (verification.get("status") != "verified_reference_input" or not verification.get("source")
            or not verification.get("quantity_scope") or not controls.get("units") or not controls.get("protocol")):
        raise ValueError("external_control_inputs_not_independently_verified")
    if data.measurement == "continuous":
        raise ValueError("absolute_count_to_molecule_calibration_requires_untransformed_counts")
    if not ids or len(ids) != len(set(ids)) or abundance.shape != (len(ids),) or not np.isfinite(abundance).all() or (abundance <= 0).any():
        raise ValueError("invalid_external_control_identity_or_amount_vector")
    if not set(ids) <= set(data.gene_ids):
        raise ValueError("external_controls_missing_from_observed_feature_universe")
    condition = _column(data.metadata, settings, "condition_col", ("condition",))
    view, units = _independent_view(data, [condition] + _batch_columns(data.metadata, settings))
    diagnostics["biological_units"] = units
    control_positions = [view.gene_ids.index(gene) for gene in ids]
    # Preaggregation sums technical libraries. The corresponding control counts supply
    # aggregate capture exposure; no extra biological input is inferred from that sum.
    control_counts = view.expression[control_positions]
    if len(ids) < 2 or (control_counts <= 0).any():
        raise ValueError("control_recovery_cannot_be_verified_with_missing_or_single_control")
    ratios = control_counts / abundance[:, None]
    exposure = np.median(ratios, axis=0)
    discrepancy = np.abs(np.log2(ratios / exposure[None, :]))
    diagnostics["scale_verification"]["maximum_control_log2_disagreement"] = float(discrepancy.max())
    diagnostics["scale_verification"]["method_admission_threshold_log2"] = 0.5
    diagnostics["scale_verification"]["threshold_status"] = "uncalibrated_method_policy"
    if float(np.median(discrepancy.max(axis=0))) > 0.5:
        raise ValueError("control_recovery_exceeds_uncalibrated_method_admission_threshold")
    calibrated = view.expression / exposure[None, :]
    reference = settings.get("reference_condition", sorted(set(view.metadata[condition]))[0])
    comparison = settings.get("comparison_condition", sorted(set(view.metadata[condition]))[-1])
    masks = [(view.metadata[condition] == level).to_numpy() for level in (reference, comparison)]
    if reference == comparison or any(int(mask.sum()) < settings.get("minimum_units", 3) for mask in masks):
        raise ValueError("insufficient_independent_units_for_absolute_contrast")
    endogenous = [index for index, gene in enumerate(view.gene_ids) if gene not in set(ids)]
    if not endogenous:
        raise ValueError("no_observed_endogenous_features")
    rows = []
    for index in endogenous:
        left, right = (float(calibrated[index, mask].mean()) for mask in masks)
        gene = view.gene_ids[index]
        if left <= 0 or right <= 0:
            rows.append(_unavailable_row(gene, "zero_absolute_group_mean", failed=False))
            continue
        rows.append({"hypothesis_id": gene, "gene_id": gene, "estimate_status": "estimated",
                     "coordinate_ids": ["log2_absolute_ratio"], "values": [math.log2(right/left)],
                     "intervals": [], "p_value": None, "q_value": None})
    totals = calibrated[endogenous].sum(axis=0)
    left, right = (float(totals[mask].mean()) for mask in masks)
    rows.append({"hypothesis_id": "total_endogenous_rna", "component_id": "endogenous_total",
                 "estimate_status": "estimated", "coordinate_ids": ["log2_absolute_ratio"],
                 "values": [math.log2(right/left)], "intervals": [], "p_value": None, "q_value": None})
    diagnostics["endogenous_total_by_condition"] = {reference: left, comparison: right}
    return [target_fragment(target, claim_type="restricted_estimate", evidence_scope="anchored_absolute",
        payload=_effects_payload(view, target, rows, [], {"reference_condition": reference},
                                 scale_anchor="declared_external_controls"),
        limitation_codes=["standardized_biological_input_not_per_cell", "shared_control_and_endogenous_recovery_assumption",
                          "calibration_uncertainty_and_population_tests_not_estimated", "observed_condition_allocation"])
        for target in targets]


def _deconvolve(data, targets, settings, diagnostics):
    import numpy as np
    from scipy.optimize import nnls

    reference = data.annotations.get("cell_type_reference")
    markers = data.annotations.get("stable_reference_markers")
    if not isinstance(reference, dict) or not isinstance(markers, list) or not markers:
        raise ValueError("validated_mixture_reference_or_stable_markers_missing")
    genes, populations = reference.get("gene_ids", []), reference.get("cell_type_ids", [])
    if not genes or len(genes) != len(set(genes)) or len(populations) < 2 or len(populations) != len(set(populations)):
        raise ValueError("invalid_mixture_reference_axes")
    matrix = np.asarray(reference.get("relative_expression"), dtype=float)
    rna = np.asarray(reference.get("rna_content"), dtype=float)
    if (matrix.shape != (len(genes), len(populations)) or matrix.nbytes > 16*1024*1024
            or not np.isfinite(matrix).all() or (matrix < 0).any()):
        raise ValueError("invalid_or_unbounded_mixture_expression_reference")
    if rna.ndim != 2 or rna.shape[0] != len(populations) or not np.isfinite(rna).all() or (rna <= 0).any():
        raise ValueError("positive_aligned_per_cell_rna_content_reference_missing")
    if len(markers) != len(set(markers)) or not set(markers) <= set(genes) & set(data.gene_ids):
        raise ValueError("mixture_marker_features_do_not_align_with_observed_reference")
    if data.measurement == "continuous":
        raise ValueError("mixture_linear_count_scale_not_identified_for_continuous_layer")
    design_context = data.context.get("design", {})
    conditions = design_context.get("condition_levels", [])
    declared_times = design_context.get("time_grid") or [None]
    if not conditions or rna.shape[1] != len(conditions)*len(declared_times):
        raise ValueError("rna_content_reference_positions_do_not_match_public_design")
    condition_col = _column(data.metadata, settings, "condition_col", ("condition",))
    time_col = _column(data.metadata, settings, "time_col", ("time", "numeric_time", "embryonic_age"), False)
    columns = [condition_col] + ([time_col] if len(declared_times) > 1 and time_col else [])
    view, units = _independent_view(data, columns)
    diagnostics["biological_units"] = units
    reference_positions = [genes.index(gene) for gene in markers]
    observed_positions = [view.gene_ids.index(gene) for gene in markers]
    # The full supplied gene universe supplies each population's RNA denominator.
    # A subset reference cannot silently be interpreted as exhaustive transcript mass.
    if set(genes) != set(view.gene_ids):
        raise ValueError("reference_gene_coverage_incomplete_for_rna_denominator")
    totals = matrix.sum(axis=0)
    if (totals <= 0).any():
        raise ValueError("reference_population_has_no_positive_expression")
    signature = matrix[reference_positions] / totals[None, :]
    rank, number = int(np.linalg.matrix_rank(signature)), float(np.linalg.cond(signature))
    diagnostics["mixture_reference"] = {"marker_count": len(markers), "population_count": len(populations),
        "rank": rank, "condition_number": number if math.isfinite(number) else None,
        "expression_units": reference.get("expression_units"), "rna_content_units": reference.get("rna_content_units"),
        "condition_number_admission_limit": 1000, "relative_residual_admission_limit": 0.2,
        "admission_threshold_status": "uncalibrated_method_policy"}
    if rank != len(populations) or not math.isfinite(number) or number > 1000:
        raise ValueError("mixture_reference_not_well_conditioned_or_distinguishable")
    normalized = _normalized(view, diagnostics)
    fractions, residuals = {}, []
    for observation in range(len(view.metadata)):
        row = view.metadata.iloc[observation]
        condition = str(row[condition_col])
        if condition not in conditions:
            raise ValueError("observed_condition_missing_from_reference_positions")
        time_index = 0
        if len(declared_times) > 1:
            if not time_col:
                raise ValueError("time_resolved_rna_reference_requires_observed_time")
            observed_time = float(row[time_col])
            if observed_time not in declared_times:
                raise ValueError("observed_time_missing_from_reference_positions")
            time_index = declared_times.index(observed_time)
        reference_index = conditions.index(condition)*len(declared_times) + time_index
        observation_vector = normalized[observed_positions, observation]
        weights, residual = nnls(signature, observation_vector)
        denominator = float(np.linalg.norm(observation_vector))
        relative_residual = float(residual / denominator) if denominator else 1.0
        residuals.append(relative_residual)
        if weights.sum() <= 0 or relative_residual > 0.2:
            raise ValueError("observed_mixture_exceeds_method_reference_fit_threshold")
        rna_weights = weights/weights.sum()
        cell_weights = rna_weights/rna[:, reference_index]
        cell_weights /= cell_weights.sum()
        coordinate = condition + "_grid_" + str(time_index)
        fractions.setdefault(coordinate, []).append((cell_weights, rna_weights))
    diagnostics["mixture_reference"]["maximum_relative_reconstruction_residual"] = max(residuals)
    cell_rows, rna_rows = [], []
    for coordinate, values in fractions.items():
        if len(values) < settings.get("minimum_units", 3):
            raise ValueError("insufficient_independent_units_per_mixture_reference_position")
        cell_mean = np.mean([item[0] for item in values], axis=0)
        rna_mean = np.mean([item[1] for item in values], axis=0)
        for population, cell_fraction, rna_fraction in zip(populations, cell_mean, rna_mean):
            cell_rows.append({"population_id": population, "coordinate_id": coordinate, "fraction": float(cell_fraction)})
            rna_rows.append({"population_id": population, "coordinate_id": coordinate, "fraction": float(rna_fraction)})
    payload = {"kind": "fractions", "cell_fractions": cell_rows, "rna_fractions": rna_rows,
               "reference_denominator": "supplied reference populations conditional on stable markers and per-cell RNA content",
               "reference_coverage": "restricted", "absolute_population_claimed": False}
    return [target_fragment(target, claim_type="restricted_estimate", evidence_scope="relative_composition",
        payload=payload, limitation_codes=["reference_conditioned_population_coverage_not_proven_exhaustive",
        "rna_content_calibration_assumed", "deconvolution_uncertainty_not_estimated", "within_cell_type_response_not_identified"])
        for target in targets]


def _public_settings(data, settings):
    settings = dict(settings)
    roles = data.context.get("analysis_columns")
    if roles is not None:
        for option, role in (("condition_col", "condition"), ("batch_cols", "batches"), ("block_cols", "blocking")):
            if role == "condition" and roles.get(role) is None:
                continue
            value = roles.get(role, [])
            if option in settings and settings[option] != value:
                raise ValueError("tool_" + option + "_conflicts_with_declared_analysis_columns")
            settings[option] = value
        if hasattr(data, "metadata") and not roles.get("batches", []) and "batch" in data.metadata:
            raise ValueError("metadata_batch_column_must_be_explicitly_declared")
        if settings.get("block_cols") and settings["block_cols"] != [data.unit_hierarchy["population_unit_label"]]:
            raise ValueError("paired_fixed_block_must_be_declared_biological_unit")
    requested = dict(data.context.get("requested_condition_contrast", {}))
    declared_levels = data.context.get("design", {}).get("condition_levels", data.context.get("condition_labels", []))
    if declared_levels:
        requested.setdefault("denominator", declared_levels[0])
        requested.setdefault("numerator", declared_levels[1] if len(declared_levels) > 1 else declared_levels[0])
    for option, field in (("reference_condition", "denominator"), ("comparison_condition", "numerator")):
        if field in requested:
            if option in settings and settings[option] != requested[field]:
                raise ValueError("tool_condition_contrast_conflicts_with_canonical_public_question")
            settings[option] = requested[field]
    rhythm = data.context.get("rhythm_analysis_domain", {}).get("frequency_cycles_per_time_unit")
    if rhythm is not None:
        if "frequency_domain" in settings and list(settings["frequency_domain"]) != list(rhythm):
            raise ValueError("tool_frequency_domain_conflicts_with_public_target_domain")
        settings["frequency_domain"] = rhythm
    return validate_bulk_options(settings)


def _restraint(data, targets, diagnostics):
    annotation_keys = sorted(data.annotations)
    intervention_columns = [name for name in data.metadata.columns if name in {
        "assignment", "intervention", "randomized_assignment", "knockout", "guide_target", "efficacy"}]
    binding_reference = any(key in data.annotations for key in ("binding_assay", "chromatin_binding", "chip_seq"))
    diagnostics["regulatory_evidence_audit"] = {"observed_features": len(data.gene_ids),
        "observed_libraries": len(data.sample_ids), "public_annotation_types": annotation_keys,
        "intervention_columns": intervention_columns, "binding_measurement_supplied": binding_reference,
        "observed_regulatory_association_is_interventional_evidence": False}
    code = "causal_identification_method_not_implemented_for_supplied_intervention" if intervention_columns else "direct_intervention_and_common_cause_control_missing"
    return [target_fragment(target, claim_type=_failure_kind(code), evidence_scope="not_identified",
        payload={"kind": "restraint", "status": "not_identified", "missing_evidence": [code]},
        limitation_codes=_failure_limitations(code) + ["correlation_does_not_identify_direct_regulation"])
        for target in targets]


def _observation_robustness(data, targets, settings, diagnostics):
    import numpy as np

    quality_col = "quality_score" if "quality_score" in data.metadata else None
    coefficients = data.annotations.get("gene_susceptibility")
    selection_columns = [name for name in data.metadata if name in {
        "observation_probability", "selection_probability", "missingness_probability", "included", "observed"}]
    diagnostics["observation_support"] = {"quality_covariate_observed": bool(quality_col),
        "public_quality_susceptibility_supplied": isinstance(coefficients, dict),
        "selection_or_inclusion_columns": selection_columns,
        "missing_unobserved_expression_reconstructed": False}
    if not quality_col:
        raise ValueError("clean_population_observation_process_not_identified")
    quality = _numeric(data.metadata, quality_col)
    if (quality < 0).any() or (quality > 1).any():
        raise ValueError("quality_covariate_outside_declared_unit_interval")
    time_col = _column(data.metadata, settings, "time_col", ("time", "numeric_time", "embryonic_age"))
    times = _numeric(data.metadata, time_col)
    diagnostics["observation_support"]["quality_minimum"] = float(quality.min())
    diagnostics["observation_support"]["quality_maximum"] = float(quality.max())
    diagnostics["observation_support"]["quality_time_correlation"] = (
        float(np.corrcoef(quality, times)[0, 1]) if np.ptp(quality) > 0 and np.ptp(times) > 0 else None)
    if not isinstance(coefficients, dict):
        raise ValueError("quality_response_separation_requires_independent_susceptibility_reference")
    genes = coefficients.get("gene_ids", [])
    slopes = np.asarray(coefficients.get("log2_attenuation_per_quality_deficit", []), dtype=float)
    if (len(genes) != len(set(genes)) or set(genes) != set(data.gene_ids) or slopes.shape != (len(genes),)
            or not np.isfinite(slopes).all() or not coefficients.get("reference")):
        raise ValueError("quality_susceptibility_reference_must_exactly_cover_observed_features")
    # An observed quality correction does not establish that missing specimens are
    # ignorable, or remove contamination without an independently measured process.
    diagnostics["observation_support"]["quality_reference_aligned"] = True
    view, units = _independent_view(data, _mode_columns(data, "temporal", settings) + [quality_col])
    diagnostics["biological_units"] = units
    quality = _numeric(view.metadata, quality_col)
    corrections = slopes[[genes.index(gene) for gene in view.gene_ids], None] * (1-quality[None, :])
    diagnostics["observation_support"]["maximum_reference_log2_quality_correction"] = float(np.max(np.abs(corrections)))
    if float(np.max(np.abs(corrections))) > 20:
        raise ValueError("quality_reference_correction_exceeds_stable_observed_support")
    if view.measurement == "continuous":
        declared_units = str(view.provenance.get("measurement_declaration", {}).get("units", "")).lower()
        if "log2" not in declared_units:
            raise ValueError("quality_log2_coefficient_cannot_be_applied_to_undeclared_continuous_scale")
        view = replace(view, expression=view.expression + corrections)
        fragments = _fit_effect_targets(view, targets, "temporal", settings, diagnostics)
    else:
        from devharness_server.normalization import median_ratio_factors

        corrected_factors, factor_details = median_ratio_factors(view.expression * np.exp2(corrections), view.gene_ids)
        corrected_summary = view.expression*np.exp2(corrections)/corrected_factors[None, :]
        diagnostics["quality_corrected_size_factor_reference"] = factor_details
        diagnostics["raw_count_values_unchanged"] = True
        diagnostics["quality_corrected_count_inference"] = "not_performed_dispersion_model_with_matching_gene_offsets_unavailable"
        fragments = _quality_count_summaries(view, targets, corrected_summary, settings, diagnostics)
    for fragment in fragments:
        if fragment["estimate_status"] == "estimated":
            fragment["evidence_scope"] = "observed_difference"
            fragment["limitation_codes"].extend(["quality_corrected_observed_units_only",
                "unobserved_selection_and_contamination_not_identified", "quality_reference_coefficients_treated_as_fixed"])
    diagnostics["quality_correction_method_description"] = "Known-quality-corrected observed-unit response; an unselected clean population is not identified."
    return fragments


def _quality_count_summaries(data, targets, corrected, settings, diagnostics):
    import numpy as np

    time_column = _column(data.metadata, settings, "time_col", ("time", "numeric_time", "embryonic_age"))
    times = _numeric(data.metadata, time_column)
    grid = np.unique(times)
    if not 2 <= len(grid) <= 100:
        raise ValueError("quality_summary_requires_two_to_one_hundred_observed_times")
    reference = settings.get("reference_time", float(grid[0]))
    if reference not in grid:
        raise ValueError("quality_summary_reference_requires_observed_time")
    reference_index = int(np.flatnonzero(grid == reference)[0])
    means = np.column_stack([corrected[:, times == value].mean(axis=1) for value in grid])
    rows, curves = [], []
    selected_indices = [index for index in range(len(grid)) if index != reference_index]
    for index, gene in enumerate(data.gene_ids):
        if (means[index] <= 0).any():
            rows.append(_unavailable_row(gene, "zero_quality_corrected_mean_log_contrast_unavailable", failed=False))
            continue
        log_means = np.log2(means[index])
        rows.append({"hypothesis_id": gene, "gene_id": gene, "estimate_status": "estimated",
                     "coordinate_ids": ["grid_" + str(position) for position in selected_indices],
                     "values": (log_means[selected_indices]-log_means[reference_index]).tolist(),
                     "intervals": [], "p_value": None, "q_value": None})
        curves.append({"hypothesis_id": gene, "gene_id": gene, "estimate_status": "estimated",
                       "coordinate_ids": ["grid_" + str(position) for position in range(len(grid))],
                       "values": log_means.tolist(), "intervals": [], "p_value": None, "q_value": None})
    diagnostics["quality_summary_grid"] = grid.tolist()
    diagnostics["quality_summary_reference_time"] = float(reference)
    return [target_fragment(target, claim_type="restricted_estimate", evidence_scope="observed_difference",
        payload=_effects_payload(data, target, rows, curves, {}),
        limitation_codes=["corrected_count_summary_has_no_calibrated_test_or_interval",
                          "raw_counts_retained_as_observed_input"])
        for target in targets]


def _analyze_bulk_worker(tool_name, public_task, counts_path, metadata_path, annotations_path,
                         options=None, allow_data_execution=False):
    require_data_execution(allow_data_execution)
    if tool_name not in _TOOL_TARGETS:
        raise ValueError("Unregistered bulk analysis tool")
    data = load_observed_data(public_task, counts_path=counts_path, metadata_path=metadata_path,
                             annotations_path=annotations_path, allow_data_execution=True)
    settings = _public_settings(data, validate_bulk_options(options))
    targets = [target for target in data.targets if target["target_id"] in _TOOL_TARGETS[tool_name]]
    diagnostics = {"input_measurement": data.measurement, "gene_count": len(data.gene_ids),
                   "observation_count": len(data.sample_ids), "biological_units": _unit_audit(data),
                   "design_condition_policy": design_condition_policy(settings.get("condition_number_limit", DESIGN_CONDITION_NUMBER_LIMIT))}
    method = {"tool": tool_name, "version": "observed-bulk-1", "uses_observed_inputs_only": True,
              "count_model": None, "continuous_model": None,
              "design_condition_policy": design_condition_policy(settings.get("condition_number_limit", DESIGN_CONDITION_NUMBER_LIMIT)),
              "qualification": "unvalidated_numerical_implementation"}
    try:
        if tool_name == "audit_expression_quality":
            diagnostics["expression_quality"] = _measurement_audit(data)
            fragments = []
        elif tool_name == "validate_biological_units":
            audit = diagnostics["biological_units"]
            fragments = [_diagnostic_fragment(target, data, audit, audit["biological_unit_count"]) for target in targets]
        elif tool_name == "validate_design_matrix":
            question_ids = {target["target_id"] for target in data.targets}
            mode = ("covariate" if question_ids & {"covariate_effect", "covariate_interaction"}
                    else "condition_time" if question_ids & {"condition_main", "time_interaction"}
                    else "temporal" if "sampled_mean" in question_ids else "condition")
            view, units = _independent_view(data, _mode_columns(data, mode, settings), settings=settings)
            _, details = _design(view, mode, settings)
            diagnostics.update(design=details, biological_units=units)
            fragments = []
        elif tool_name == "model_adequacy_analysis":
            audit = _measurement_audit(data)
            diagnostics["expression_quality"] = audit
            diagnostics["mean_variance_summary"] = _mean_variance_summary(data)
            fragments = [_diagnostic_fragment(target, data, audit, diagnostics["biological_units"]["biological_unit_count"])
                         for target in targets]
        elif tool_name in {"fit_temporal_trajectory", "condition_time_analysis", "mean_condition_analysis", "covariate_response_analysis"}:
            mode = {"fit_temporal_trajectory": "temporal", "condition_time_analysis": "condition_time",
                    "mean_condition_analysis": "condition", "covariate_response_analysis": "covariate"}[tool_name]
            relative_targets = [target for target in targets if target["target_id"] == "tissue_effect"]
            model_targets = [target for target in targets if target["target_id"] != "tissue_effect"]
            fragments = _fit_effect_targets(data, model_targets, mode, settings, diagnostics) if model_targets else []
            if relative_targets:
                fragments.extend(_library_relative_targets(data, relative_targets, settings, diagnostics))
        elif tool_name == "descriptive_expression_summary":
            fragments = _descriptive_targets(data, targets, settings, diagnostics)
        elif tool_name == "differential_variability_analysis":
            fragments = _variability(data, targets, settings, diagnostics)
        elif tool_name == "infer_regulatory_network":
            fragments = _network(data, targets, settings, diagnostics)
        elif tool_name == "regulatory_evidence_audit":
            fragments = _restraint(data, targets, diagnostics)
        elif tool_name == "bulk_mixture_deconvolution":
            fragments = _deconvolve(data, targets, settings, diagnostics)
        elif tool_name == "normalization_scale_analysis":
            fragments = []
            for target in targets:
                try:
                    fragments.extend(_absolute_scale(data, [target], settings, diagnostics) if target["target_id"] == "absolute_rna"
                                     else _library_relative_targets(data, [target], settings, diagnostics))
                except (ValueError, ArithmeticError, RuntimeError) as error:
                    code = _error_code(error)
                    diagnostics.setdefault("target_errors", {})[target["target_id"]] = code
                    fragments.append(target_fragment(target, claim_type=_failure_kind(code), evidence_scope="not_identified", limitation_codes=_failure_limitations(code)))
        elif tool_name == "observation_robustness_analysis":
            fragments = _observation_robustness(data, targets, settings, diagnostics)
        elif tool_name == "temporal_event_analysis":
            fragments = _temporal_events(data, targets, settings, diagnostics)
        elif tool_name == "periodic_trajectory_analysis":
            fragments = _periodic(data, targets, settings, diagnostics)
        elif tool_name == "dose_response_analysis":
            fragments = _dose_response(data, targets, settings, diagnostics)
        else:
            raise ValueError("registered_handler_missing")
    except (ValueError, ArithmeticError, RuntimeError, ImportError) as error:
        code = "missing_numerical_dependency" if isinstance(error, ImportError) else _error_code(error)
        diagnostics["analysis_error"] = {"code": code, "type": type(error).__name__, "message": str(error)[:1000]}
        fragments = [target_fragment(target, claim_type=_failure_kind(code),
                                     evidence_scope="not_identified", limitation_codes=_failure_limitations(code)) for target in targets]
    for index, fragment in enumerate(fragments):
        payload = fragment.get("payload") or {}
        records = payload.get("gene_statistics") if payload.get("kind") == "effects" else (
            payload.get("rhythms") if payload.get("kind") == "rhythm" else None)
        if records is not None and not any(record.get("estimate_status") == "estimated" for record in records):
            diagnostics.setdefault("unavailable_target_records", {})[fragment["target_id"]] = records
            failed = any(record.get("estimate_status") == "failed" for record in records)
            target = next(target for target in targets if target["target_id"] == fragment["target_id"])
            fragments[index] = target_fragment(target, claim_type="failed" if failed else "refusal",
                evidence_scope="not_identified", limitation_codes=["no_successfully_estimated_hypotheses"])
    fitted_route = diagnostics.get("model_family_attempted") or (
        "negative_binomial" if any(key.startswith("condition_calibration_") for key in diagnostics) else None)
    if data.measurement != "continuous" and (fitted_route == "negative_binomial" or "calibration" in diagnostics):
        method["count_model"] = "negative_binomial_empirical_bayes_dispersion"
    elif data.measurement == "continuous" and (fitted_route == "gaussian" or any(
            key in diagnostics for key in ("selected_models", "concentration_models", "candidate_calibrations"))):
        method["continuous_model"] = "gaussian_on_declared_continuous_or_explicitly_quality_corrected_scale"
    _attach_refusal_evidence(data, fragments, diagnostics)
    return json_safe_result({"target_results": fragments, "diagnostics": diagnostics, "method": method})


def _mean_variance_summary(data):
    import numpy as np

    mean = data.expression.mean(axis=1)
    variance = data.expression.var(axis=1, ddof=1) if data.expression.shape[1] > 1 else None
    return {"median_gene_mean": float(np.median(mean)),
            "median_gene_variance": float(np.median(variance)) if variance is not None else None,
            "genes_with_variance_exceeding_mean": int(np.count_nonzero(variance > mean)) if variance is not None else None,
            "interpretation": "marginal moments include design effects and do not prove likelihood adequacy"}


def _temporal_events(data, targets, settings, diagnostics):
    import numpy as np

    view, units = _independent_view(data, _mode_columns(data, "temporal", settings))
    _multiplicity_provenance(view, diagnostics)
    design, details = _design(view, "temporal", settings)
    _require_design(design, details)
    _check_replication(view, details, settings)
    fits, failures = _fit_family(view, design, diagnostics)
    test_contrast, _ = _contrasts("sampled_mean", details, len(design.columns))
    tests, curve_values = [], {}
    for gene, fitted in fits.items():
        try:
            _, _, p_value = _linear_estimate(fitted, test_contrast, view.measurement != "continuous")
            curve = _curve_row(gene, fitted, details, view.measurement != "continuous")
            curve_values[gene] = curve["values"]
            tests.append({"hypothesis_id": gene, "p_value": p_value, "q_value": p_value})
        except (ValueError, ArithmeticError, RuntimeError) as error:
            failures[gene] = _error_code(error)
    _bh(tests, hypothesis_ids=view.gene_ids)
    if not tests:
        diagnostics["failed_event_genes"] = failures
        raise ValueError("all_temporal_event_gene_tests_failed")
    grid, events, eligible = details["grid"], [], []
    for test in tests:
        gene = test["hypothesis_id"]
        if test["q_value"] > 0.05:
            continue
        values = np.asarray(curve_values[gene])
        differences = np.diff(values)
        tolerance = np.finfo(float).eps*max(1, float(np.max(np.abs(values))))*100
        gene_events = []
        for index in range(1, len(grid)-1):
            left, right = differences[index-1:index+1]
            kind = "peak" if left > tolerance and right < -tolerance else (
                "trough" if left < -tolerance and right > tolerance else None)
            if kind:
                gene_events.append({"hypothesis_id": gene, "kind": kind,
                                    "interval": {"lower": grid[index-1], "upper": grid[index+1]}})
        # Half-response is relative to observed endpoints and requires an observed
        # monotone path. It is not latent onset, an EC50 or an unsampled change point.
        if np.all(differences >= -tolerance) or np.all(differences <= tolerance):
            halfway = (values[0]+values[-1])/2
            if abs(values[-1]-values[0]) > tolerance:
                for index in range(len(grid)-1):
                    if min(values[index:index+2]) <= halfway <= max(values[index:index+2]):
                        gene_events.append({"hypothesis_id": gene, "kind": "half_observed_response",
                                            "interval": {"lower": grid[index], "upper": grid[index+1]}})
                        break
        relative = values-values[0]
        for index in range(1, len(grid)-1):
            if relative[index]*relative[index+1] < -tolerance**2:
                gene_events.append({"hypothesis_id": gene, "kind": "baseline_crossing_reversal",
                                    "interval": {"lower": grid[index], "upper": grid[index+1]}})
        if gene_events:
            eligible.append(gene)
            events.extend(gene_events)
    diagnostics.update(design=details, biological_units=units, temporal_gene_tests=tests,
                       failed_event_genes=failures, event_selection="temporal_BH_q_below_0.05_then_observed_grid_shape")
    payload = {"kind": "events", "event_intervals": events, "eligible_hypotheses": list(view.gene_ids),
               "unavailable_hypotheses": failures,
               "timing_definition": "observed_grid_brackets"}
    return [target_fragment(target, claim_type="restricted_estimate", evidence_scope="population_mean", payload=payload,
        limitation_codes=["events_conditional_on_fixed_spline_and_temporal_selection",
                          "grid_brackets_are_sampling_resolution_not_confidence_intervals",
                          "no_continuous_onset_or_unsampled_event_claim"])
        for target in targets]


def _fit_given_calibration(values, design, *, alpha=None, offset=None,
                           condition_number_limit=DESIGN_CONDITION_NUMBER_LIMIT):
    import numpy as np
    import statsmodels.api as sm
    import warnings

    condition_diagnostics = require_design_condition(design, condition_number_limit=condition_number_limit)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        if alpha is None:
            fitted = sm.OLS(values, design).fit()
            if fitted.df_resid < 2 or fitted.scale <= np.finfo(float).tiny:
                raise ValueError("insufficient_or_zero_gaussian_residual_variation")
        else:
            fitted = sm.GLM(values, design, family=sm.families.NegativeBinomial(alpha=alpha),
                            offset=offset).fit(maxiter=200, tol=1e-8)
            if not fitted.converged:
                raise ValueError("candidate_negative_binomial_nonconvergence")
    if not np.isfinite(fitted.params).all() or not np.isfinite(fitted.cov_params()).all() or not math.isfinite(float(fitted.llf)):
        raise ValueError("candidate_fit_nonfinite")
    fitted._devbench_design_condition = condition_diagnostics
    return fitted


def _calibrated_parameters(data, diagnostics):
    import numpy as np

    if data.measurement == "continuous":
        return {}, None
    from devharness_server.normalization import median_ratio_factors

    factors, _ = median_ratio_factors(data.expression, data.gene_ids)
    return diagnostics["calibration"]["gene_estimates"], np.log(factors)


def _periodic(data, targets, settings, diagnostics):
    import numpy as np

    view, units = _independent_view(data, _mode_columns(data, "temporal", settings))
    _multiplicity_provenance(view, diagnostics)
    full_design, design_details = _design(view, "temporal", settings)
    _require_design(full_design, design_details)
    _check_replication(view, design_details, settings)
    full_fits, failures = _fit_family(view, full_design, diagnostics)
    calibrations, offset = _calibrated_parameters(view, diagnostics)
    time_values = _numeric(view.metadata, design_details["coordinate_column"])
    grid = np.unique(time_values)
    span = float(grid[-1]-grid[0])
    if len(grid) < 8 or span <= 0:
        raise ValueError("periodic_method_requires_at_least_eight_observed_timepoints")
    domain = settings.get("frequency_domain")
    if domain is None:
        raise ValueError("prespecified_candidate_frequency_domain_required")
    diagnostics["periodic_cycle_coverage"] = {"minimum_cycles_in_declared_domain": domain[0]*span,
        "method_minimum_cycles": 1.5, "threshold_status": "uncalibrated_method_policy"}
    if domain[0]*span < 1.5:
        raise ValueError("periodic_method_minimum_cycle_coverage_not_met_for_full_declared_domain")
    # Evenly spaced measurements cannot identify frequencies above Nyquist.
    steps = np.diff(grid)
    regular = bool(np.allclose(steps, steps[0], rtol=1e-6, atol=1e-12))
    if regular and domain[1] >= 0.5/float(steps[0]):
        raise ValueError("candidate_frequency_domain_crosses_sampling_nyquist_limit")
    frequencies = np.linspace(domain[0], domain[1], settings.get("frequency_count", 16))
    dampings = settings.get("damping_grid", [0.0, 0.5/span, 1.0/span])
    candidate_count = len(frequencies)*len(dampings)
    if candidate_count*len(view.gene_ids) > 40_000:
        raise ValueError("complete_periodic_gene_search_exceeds_model_budget")
    # Smooth drift is retained in every harmonic null, so a marginal trend is
    # never the harmonic alternative's unadjusted baseline.
    keep = [index for index in range(full_design.shape[1]) if index not in design_details["blocks"].get("time", [])]
    centered = (time_values-grid[0])/span
    null_design = np.column_stack([np.asarray(full_design)[:, keep], centered, centered**2])
    if np.linalg.matrix_rank(null_design) != null_design.shape[1] or len(null_design)-null_design.shape[1] < 6:
        raise ValueError("periodic_nuisance_design_not_identifiable")
    condition_column = design_details.get("condition_column")
    condition_levels = design_details.get("condition_levels", ["observed"])
    if len(condition_levels) > 2:
        raise ValueError("periodic_phase_model_supports_at_most_two_declared_conditions")
    indicators = [np.ones(len(view.metadata))] if condition_column is None else [
        (view.metadata[condition_column].astype(str) == level).to_numpy(dtype=float) for level in condition_levels]
    records, fitted_search = [], {}
    for index, gene in enumerate(view.gene_ids):
        if gene not in full_fits:
            records.append({"hypothesis_id": gene, "estimate_status": "failed", "missing_reason": failures[gene]})
            continue
        alpha = calibrations[gene]["dispersion_alpha"] if calibrations else None
        choices = []
        for frequency in frequencies:
            for damping in dampings:
                elapsed = time_values-grid[0]
                envelope = np.exp(-damping*elapsed)
                harmonic = []
                for indicator in indicators:
                    harmonic.extend([indicator*envelope*np.cos(2*np.pi*frequency*elapsed),
                                     indicator*envelope*np.sin(2*np.pi*frequency*elapsed)])
                candidate_design = np.column_stack([null_design, *harmonic])
                if (np.linalg.matrix_rank(candidate_design) != candidate_design.shape[1]
                        or len(candidate_design)-candidate_design.shape[1] < 2):
                    continue
                try:
                    fitted = _fit_given_calibration(view.expression[index], candidate_design, alpha=alpha, offset=offset,
                        condition_number_limit=settings.get("condition_number_limit", DESIGN_CONDITION_NUMBER_LIMIT))
                    contrast = np.eye(candidate_design.shape[1])[-len(harmonic):]
                    _, _, p_value = _linear_estimate(fitted, contrast, view.measurement != "continuous")
                    bic = -2*float(fitted.llf) + math.log(len(candidate_design))*(len(fitted.params)+3)
                    choices.append((bic, float(frequency), float(damping), p_value, fitted))
                except (ValueError, ArithmeticError, RuntimeError) as error:
                    counts = diagnostics.setdefault("periodic_candidate_failures", {}).setdefault(gene, {})
                    code = _error_code(error)
                    counts[code] = counts.get(code, 0)+1
                    continue
        if not choices:
            records.append({"hypothesis_id": gene, "estimate_status": "failed", "missing_reason": "all_periodic_candidates_failed"})
            continue
        best = min(choices, key=lambda value: value[0])
        p_value = min(1.0, best[3]*candidate_count)
        transient = full_fits[gene]
        transient_bic = -2*float(transient.llf)+math.log(len(view.metadata))*(len(transient.params)+1)
        fitted_search[gene] = {"best": best, "transient_bic": transient_bic,
                              "near_best_count": sum(choice[0] <= best[0]+2 for choice in choices)}
        records.append({"hypothesis_id": gene, "estimate_status": "estimated", "rhythmic": False,
                        "p_value": p_value, "q_value": p_value})
    _bh(records)
    any_ambiguity = False
    for record in records:
        if record["estimate_status"] != "estimated":
            continue
        selected = fitted_search[record["hypothesis_id"]]
        bic, frequency, damping, _, fitted = selected["best"]
        diagnostics.setdefault("selected_periodic_design_conditions", {})[record["hypothesis_id"]] = fitted._devbench_design_condition
        ambiguous = selected["near_best_count"] > 1
        any_ambiguity = any_ambiguity or ambiguous
        if record["q_value"] <= 0.05 and bic < selected["transient_bic"]:
            coefficients = np.asarray(fitted.params)[-2*len(indicators):]
            phases = []
            for index, condition in enumerate(condition_levels):
                cosine, sine = coefficients[2*index:2*index+2]
                phases.append({"condition": condition, "phase_radians": float(math.atan2(-sine, cosine) % (2*math.pi))})
            record.update(rhythmic=True, period=1/frequency, phase_by_condition=phases, damping=damping)
    diagnostics.update(biological_units=units, design=design_details,
        frequency_grid=frequencies.tolist(), damping_grid=list(dampings), candidate_count=candidate_count,
        frequency_search_p_adjustment="Bonferroni_over_prespecified_frequency_damping_candidates_then_BH_across_genes",
        phase_definition="nonnegative_amplitude_cosine_at_first_observed_time",
        transient_comparator="fixed_df_gaussian_or_NB_spline_BIC",
        rhythmic_false_meaning="not_selected_by_this_model_rule_not_proven_absence")
    payload = {"kind": "rhythm", "rhythms": records, "phase_reference_time": float(grid[0]),
               "amplitude_convention": "nonnegative", "phase_convention": "cosine_plus_phase",
               "candidate_frequency_domain": {"lower": domain[0], "upper": domain[1]},
               "sampling_ambiguity": any_ambiguity, "selection_conditional_inference": True}
    return [target_fragment(target, claim_type="restricted_estimate", evidence_scope="population_mean", payload=payload,
        limitation_codes=["selected_grid_frequency_phase_and_damping_uncertainty_not_estimated",
            "conditional_harmonic_model_and_empirical_bayes_reference", "finite_sampling_cannot_exclude_other_aliases",
            "nonsignificance_does_not_establish_rhythm_absence"])
        for target in targets]


def _effective_concentrations(data, targets, settings, diagnostics):
    import numpy as np
    from scipy.stats import chi2

    view, units = _independent_view(data, _mode_columns(data, "dose", settings))
    _multiplicity_provenance(view, diagnostics)
    full_design, details = _design(view, "dose", settings)
    _require_design(full_design, details)
    _check_replication(view, details, settings)
    if view.measurement == "continuous":
        units_description = str(view.provenance.get("measurement_declaration", {}).get("units", "")).lower()
        if "log2" not in units_description:
            raise ValueError("half_maximal_log2_response_requires_declared_log2_continuous_scale")
    elif "calibration" not in diagnostics:
        _fit_family(view, full_design, diagnostics)
    calibrations, offset = _calibrated_parameters(view, diagnostics)
    dose = _numeric(view.metadata, details["coordinate_column"])
    grid = np.unique(dose)
    if len(grid) < 5:
        raise ValueError("asymptotic_dose_model_requires_vehicle_and_four_positive_doses")
    positive = grid[grid > 0]
    ec_grid = np.geomspace(float(positive[0])/4, float(positive[-1])*4, 17)
    hill_slopes = [0.5, 1.0, 2.0, 4.0]
    candidate_count = len(ec_grid)*len(hill_slopes)+1
    if candidate_count*len(view.gene_ids) > 40_000:
        raise ValueError("complete_dose_model_search_exceeds_model_budget")
    keep = [index for index in range(full_design.shape[1]) if index not in details["blocks"].get("dose", [])]
    nuisance = np.asarray(full_design)[:, keep]
    transformed = np.log1p(dose/positive[0])
    endpoint = float(np.log1p(positive[-1]/positive[0]))
    records, tests, models = [], [], {}
    for index, gene in enumerate(view.gene_ids):
        if calibrations and (not calibrations[gene].get("dispersion_converged") or calibrations[gene].get("dispersion_alpha") is None):
            records.append({"hypothesis_id": gene, "solution_kind": "unavailable", "missing_reason": "dispersion_unavailable"})
            continue
        alpha = calibrations[gene]["dispersion_alpha"] if calibrations else None
        candidates, hill_profiles = [], []
        for slope in hill_slopes:
            for ec in ec_grid:
                response = np.power(dose, slope)/(ec**slope + np.power(dose, slope))
                candidate_design = np.column_stack([nuisance, response])
                try:
                    fit = _fit_given_calibration(view.expression[index], candidate_design, alpha=alpha, offset=offset,
                        condition_number_limit=settings.get("condition_number_limit", DESIGN_CONDITION_NUMBER_LIMIT))
                    _, _, p_value = _linear_estimate(fit, np.eye(candidate_design.shape[1])[-1:], view.measurement != "continuous")
                    bic = -2*float(fit.llf)+math.log(len(dose))*(len(fit.params)+3)
                    candidate = {"kind": "hill", "bic": bic, "ec": float(ec), "slope": slope,
                                 "log_likelihood": float(fit.llf), "p_value": p_value, "fit": fit}
                    candidates.append(candidate)
                    hill_profiles.append(candidate)
                except (ValueError, ArithmeticError, RuntimeError) as error:
                    counts = diagnostics.setdefault("concentration_candidate_failures", {}).setdefault(gene, {})
                    code = _error_code(error)
                    counts[code] = counts.get(code, 0)+1
                    continue
        quadratic_design = np.column_stack([nuisance, transformed, transformed**2])
        if np.linalg.matrix_rank(quadratic_design) == quadratic_design.shape[1]:
            try:
                fit = _fit_given_calibration(view.expression[index], quadratic_design, alpha=alpha, offset=offset,
                    condition_number_limit=settings.get("condition_number_limit", DESIGN_CONDITION_NUMBER_LIMIT))
                _, _, p_value = _linear_estimate(fit, np.eye(quadratic_design.shape[1])[-2:], view.measurement != "continuous")
                bic = -2*float(fit.llf)+math.log(len(dose))*(len(fit.params)+1)
                candidates.append({"kind": "log_dose_quadratic", "bic": bic, "p_value": p_value, "fit": fit})
            except (ValueError, ArithmeticError, RuntimeError) as error:
                counts = diagnostics.setdefault("concentration_candidate_failures", {}).setdefault(gene, {})
                code = _error_code(error)
                counts[code] = counts.get(code, 0)+1
        if not candidates:
            records.append({"hypothesis_id": gene, "solution_kind": "unavailable", "missing_reason": "all_dose_models_failed"})
            continue
        best = min(candidates, key=lambda candidate: candidate["bic"])
        tests.append({"hypothesis_id": gene, "p_value": min(1.0, best["p_value"]*candidate_count),
                      "q_value": min(1.0, best["p_value"]*candidate_count)})
        record = {"hypothesis_id": gene, "solution_kind": "unavailable", "missing_reason": "half_response_not_identified_in_observed_dose_support"}
        if best["kind"] == "hill":
            ec, slope = best["ec"], best["slope"]
            upper_fraction = positive[-1]**slope/(ec**slope+positive[-1]**slope)
            conditional_profile = [candidate["ec"] for candidate in hill_profiles if candidate["slope"] == slope
                and 2*(best["log_likelihood"]-candidate["log_likelihood"]) <= float(chi2.ppf(0.95, 1))]
            # Do not infer an asymptote from an unsaturated tail or report a grid
            # boundary as an identified effective concentration.
            if (positive[0] <= ec <= positive[-1] and upper_fraction >= 0.9 and conditional_profile
                    and min(conditional_profile) > ec_grid[0] and max(conditional_profile) < ec_grid[-1]):
                record = {"hypothesis_id": gene, "solution_kind": "unique", "roots": [ec],
                          "intervals": [{"lower": min(conditional_profile), "upper": max(conditional_profile)}]}
        else:
            linear, quadratic = np.asarray(best["fit"].params)[-2:]
            if abs(quadratic) > np.finfo(float).eps:
                vertex = float(-linear/(2*quadratic))
                excursion = float(linear*vertex+quadratic*vertex**2)
                if 0 < vertex < endpoint and abs(excursion) > np.finfo(float).eps:
                    roots = np.roots([quadratic, linear, -excursion/2])
                    if len(roots) == 2 and np.isreal(roots).all():
                        transformed_roots = sorted(float(np.real(value)) for value in roots)
                        if 0 <= transformed_roots[0] < transformed_roots[1] <= endpoint:
                            roots = [float(positive[0]*np.expm1(value)) for value in transformed_roots]
                            record = {"hypothesis_id": gene, "solution_kind": "root_set", "roots": roots, "intervals": []}
        records.append(record)
        models[gene] = {"model": best["kind"], "bic": best["bic"], "candidate_count": len(candidates),
                        "design_condition": best["fit"]._devbench_design_condition}
    _bh(tests, hypothesis_ids=view.gene_ids)
    by_gene = {record["hypothesis_id"]: record for record in tests}
    for index, record in enumerate(records):
        test = by_gene.get(record["hypothesis_id"])
        if record["solution_kind"] != "unavailable" and (test is None or test["q_value"] > 0.05):
            records[index] = {"hypothesis_id": record["hypothesis_id"], "solution_kind": "unavailable",
                              "missing_reason": "dose_response_not_detected_under_candidate_search_correction"}
    diagnostics.update(biological_units=units, dose_design=details, concentration_models=models,
        concentration_tests=tests, concentration_candidate_search_correction="Bonferroni_then_BH",
        hill_ec_grid=ec_grid.tolist(), hill_slope_grid=hill_slopes,
        concentration_uncertainty="hill_conditional_profile_grid_support_without_nominal_coverage_claim; quadratic_root_intervals_unavailable")
    if not any(record["solution_kind"] != "unavailable" for record in records):
        diagnostics["concentration_results"] = records
        failed = not tests and any(_failure_kind(record.get("missing_reason", "")) == "failed" for record in records)
        code = "all_concentration_estimates_failed" if failed else "no_hypothesis_has_identified_half_response_concentration"
        return [target_fragment(target, claim_type="failed" if failed else "refusal", evidence_scope="not_identified",
                                limitation_codes=[code])
                for target in targets]
    payload = {"kind": "concentration", "concentration_definition": "half_maximal_log2_response",
               "concentration_result": records}
    return [target_fragment(target, claim_type="restricted_estimate", evidence_scope="population_mean", payload=payload,
        limitation_codes=["model_selected_from_hill_and_log_dose_quadratic_candidates",
            "grid_resolution_and_selected_shape_condition_inference", "quadratic_root_uncertainty_unavailable",
            "no_extrapolated_or_clinical_effective_dose_claim"])
        for target in targets]


def _dose_response(data, targets, settings, diagnostics):
    fragments = []
    curves = [target for target in targets if target["target_id"] == "dose_curve"]
    concentrations = [target for target in targets if target["target_id"] == "effective_concentration"]
    if curves:
        fragments.extend(_fit_effect_targets(data, curves, "dose", settings, diagnostics))
    if concentrations:
        try:
            fragments.extend(_effective_concentrations(data, concentrations, settings, diagnostics))
        except (ValueError, ArithmeticError, RuntimeError) as error:
            code = _error_code(error)
            diagnostics["concentration_error"] = code
            fragments.extend(target_fragment(target, claim_type=_failure_kind(code), evidence_scope="not_identified",
                                              limitation_codes=_failure_limitations(code)) for target in concentrations)
    return fragments
