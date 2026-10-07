"""Observed single-cell methods, gated before data access or numerical imports.

Only the public measurement, question contracts, annotations and observations
enter these methods. They do not import biology generators or scoring code.
The numerical implementations remain unvalidated until separately authorized
execution. Importing this module, inspecting its registry and default denial
do not execute an analysis or start a worker.
"""
from __future__ import annotations

from dataclasses import replace
import hashlib
import json
import math
from pathlib import Path
import re
import time
from typing import Any

from .biology_tool_io import (DESIGN_CONDITION_NUMBER_LIMIT, ObservedData, design_condition_policy,
                              json_safe_result, load_observed_data, require_data_execution,
                              target_fragment, validate_public_task)


_TOOL_TARGETS = {
    "pseudobulk_membership_audit": ("cell_state_effect",),
    "cell_type_state_analysis": ("cell_state_effect",),
    "cell_composition_analysis": ("captured_abundance", "population_abundance"),
    "infer_lineage": ("state_topology", "sampled_state_geometry"),
    "lineage_history_analysis": ("ancestry", "transition_dynamics"),
    "competing_program_analysis": ("maturation_order", "cycle_state"),
    "single_cell_quality_audit": ("measurement_diagnostics",),
    "single_cell_contamination_analysis": ("clean_cell_state",),
    "infer_regulatory_network": ("association_activity",),
    "regulon_enrichment": ("regulon_membership",),
    "motif_enrichment": ("motif_occurrence",),
    "perturbation_design_audit": ("assignment_effect", "successful_intervention", "epistasis"),
    "perturbation_response_analysis": ("assignment_effect", "successful_intervention", "epistasis"),
    "regulatory_evidence_audit": ("binding", "direct_edge"),
}
SINGLECELL_TOOL_NAMES = tuple(_TOOL_TARGETS)
_AUDIT_TOOLS = frozenset({"pseudobulk_membership_audit", "perturbation_design_audit"})
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,199}\Z")
_TEXT_OPTIONS = {"condition_col", "reference_condition", "comparison_condition", "network_condition"}
from .biology_input_limits import MINIMUM_BIOLOGICAL_UNITS

_INT_OPTIONS = {"minimum_units": (MINIMUM_BIOLOGICAL_UNITS, 100), "max_cells": (4, 5000), "n_neighbors": (2, 100),
                "n_components": (2, 20), "n_clusters": (2, 24), "max_edges": (1, 10000)}
_REAL_OPTIONS = {"annotation_margin": (0.0, 3.0), "qc_mad_multiplier": (2.0, 10.0),
                 "gap_multiplier": (1.5, 10.0), "correlation_threshold": (0.0, 1.0),
                 "timeout_sec": (1.0, 300.0), "max_mem_mb": (128.0, 4096.0),
                 "condition_number_limit": (1.0, DESIGN_CONDITION_NUMBER_LIMIT)}


def singlecell_tool_capabilities() -> dict[str, Any]:
    """Pure dispatch metadata, without a claim of scientific qualification."""
    return {name: {"target_ids": list(targets), "observed_input_analysis": True,
                   "qualification": "unvalidated_numerical_implementation",
                   "result_role": "support_audit" if name in _AUDIT_TOOLS else "target_analysis",
                   "unsupported_estimation_targets": [],
                   "design_condition_policy": design_condition_policy()}
            for name, targets in _TOOL_TARGETS.items()}


def validate_singlecell_options(options: dict[str, Any] | None = None) -> dict[str, Any]:
    """Validate bounded method choices; identities, seeds and formulas are absent."""
    if options is None:
        return {}
    if not isinstance(options, dict):
        raise ValueError("options must be a JSON object")
    unknown = set(options) - (_TEXT_OPTIONS | _INT_OPTIONS.keys() | _REAL_OPTIONS.keys() | {"batch_cols"})
    if unknown:
        raise ValueError("Unknown single-cell analysis options: " + ", ".join(sorted(unknown)))
    result = dict(options)
    for key in _TEXT_OPTIONS & result.keys():
        if not isinstance(result[key], str) or not _IDENTIFIER.fullmatch(result[key]):
            raise ValueError(key + " must be a bounded identifier")
    for key, (lower, upper) in _INT_OPTIONS.items():
        if key in result and (type(result[key]) is not int or not lower <= result[key] <= upper):
            raise ValueError(key + " must be an integer within its bounded range")
    for key, (lower, upper) in _REAL_OPTIONS.items():
        if key in result and (type(result[key]) not in (float, int) or not math.isfinite(result[key])
                              or not lower <= result[key] <= upper):
            raise ValueError(key + " must be finite within its bounded range")
    if "batch_cols" in result:
        columns = result["batch_cols"]
        if not isinstance(columns, (list, tuple)) or len(columns) > 8:
            raise ValueError("batch_cols requires at most eight distinct identifiers")
        if any(not isinstance(value, str) or not _IDENTIFIER.fullmatch(value) for value in columns):
            raise ValueError("batch_cols requires identifiers, not formula grammar")
        if len(set(columns)) != len(columns):
            raise ValueError("batch_cols requires distinct identifiers")
    return result


def analyze_singlecell_tool(tool_name: str, public_task: dict[str, Any], *, counts_path: str,
                           metadata_path: str, annotations_path: str, options=None,
                           allow_data_execution: bool = False) -> dict[str, Any]:
    """Run one actual public-input method inside the existing supervised limits.

    Standalone callers must use a module-level ``if __name__ == '__main__'``
    guard. A broker supplies the receipt IDs after successful execution.
    """
    require_data_execution(allow_data_execution)
    if tool_name not in _TOOL_TARGETS:
        raise ValueError("Unregistered single-cell analysis tool")
    validate_public_task(public_task)
    settings = validate_singlecell_options(options)
    paths = tuple(Path(value) for value in (counts_path, metadata_path, annotations_path))
    if any(not path.is_absolute() for path in paths):
        raise ValueError("Observed inputs require absolute paths")
    from devharness_core.watchdog import run_sandboxed, worker_limits

    timeout = settings.get("timeout_sec", 180)
    memory = settings.get("max_mem_mb", 2048)
    with worker_limits(deadline=time.monotonic() + timeout, max_mem_mb=memory):
        return run_sandboxed(_analyze_singlecell_worker,
            (tool_name, public_task, *(str(path) for path in paths), settings, True),
            timeout_sec=timeout, max_mem_mb=memory)


class _Unavailable(ValueError):
    """Actual missing observed support or public evidence, not a successful fit."""


class _MethodUnavailable(RuntimeError):
    """An implementation/budget limitation must not masquerade as scientific refusal."""


def _identifier(*values):
    if len(values) == 1 and isinstance(values[0], str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,159}", values[0]):
        return values[0]
    encoded = json.dumps([str(value) for value in values], ensure_ascii=False, separators=(",", ":"))
    return "h-" + hashlib.sha256(encoded.encode()).hexdigest()


def _reason(error):
    value = str(error).split(":", 1)[0]
    return value if _IDENTIFIER.fullmatch(value) else "observed_analysis_failed"


def _check(code, status, explanation, evidence=("metadata",)):
    return {"code": code, "status": status, "evidence_ids": list(evidence), "explanation": explanation}


def _refusal_checks(reason, diagnostics=None):
    """Translate observed audit failures into the published criterion vocabulary.

    These are method error categories, not task/variant diagnoses. A criterion
    is emitted only after the corresponding observed-input check has failed.
    """
    reasons = [reason]
    for details in (diagnostics or {}).get("fits_by_type", {}).values():
        if details.get("unavailable"):
            reasons.append(details["unavailable"])
    findings = {}
    for failure in reasons:
        status, evidence = "unknown", ("metadata", "public_context")
        if failure in {"missing_biological_unit_identity", "biological_unit_identity_unverified",
                       "inconsistent_biological_unit_nesting", "cell_ids_must_match_transport_sample_ids"}:
            code = "biological_identity_recorded"
            if failure.startswith("inconsistent") or failure.startswith("cell_ids"):
                status = "violated"
        elif failure == "insufficient_independent_donors_per_group":
            code, status = "independent_biological_units", "violated"
        elif failure in {"rank_deficient_design", "design_condition_policy_rejected"}:
            code, status = "design_rank_identifiable", "violated"
        elif failure == "insufficient_independent_residual_degrees_of_freedom":
            code, status = "independent_biological_units", "violated"
        elif "batch" in failure:
            code = "condition_batch_overlap"
        elif failure.startswith("factorial_requires") or failure.startswith("multiple_single_guides"):
            code, status = "factorial_design_supported", "violated"
        elif failure in {"no_positive_assigned_intervention_support", "full_achieved_intervention_not_observed_for_complete_assigned_population"}:
            code, status, evidence = "achieved_intervention_positivity", "violated", ("guide_assignments", "efficacy_measurement", "metadata")
        elif "identification_assumptions" in failure:
            code, evidence = "exclusion_assumptions_supported", ("efficacy_measurement", "public_context")
        elif "efficacy" in failure or "achieved_intervention" in failure:
            code, evidence = "achieved_intervention_evidence", ("efficacy_measurement", "guide_assignments")
        elif "guide" in failure or "assignment" in failure or "nontargeting" in failure:
            code, evidence = "randomized_assignment_supported", ("guide_assignments", "metadata")
            if "violate" in failure or "inconsistent" in failure:
                status = "violated"
        elif "condition_contrast" in failure or "condition_levels" in failure:
            code, status = "contrast_levels_observed", "violated"
        elif "projection" in failure or "feature_universe_mismatch" in failure or "declared_feature_view" in failure:
            code, evidence = "shared_process_projection", ("public_context", "cell_annotation_reference", "counts")
            if any(word in failure for word in ("mismatch", "not_exact", "incompatible", "disagree", "outside")):
                status = "violated"
        elif "lineage_history" in failure:
            code, evidence = "independent_lineage_history", ("history_markers", "metadata")
        elif "state_root_anchor" in failure:
            code, evidence = "direction_anchor_observed", ("public_annotations", "public_context")
        elif "within_cell_state_transitions" in failure:
            code, evidence = "transition_dynamics_measured", ("history_markers", "public_context")
        elif "capture_probability" in failure or "capture_calibration" in failure:
            code, evidence = "capture_sampling_calibrated", ("capture_calibration", "public_context")
        elif "population_fractions" in failure:
            code, evidence = "fraction_denominator_defined", ("metadata", "cell_annotation_reference")
        elif "binding_measurements" in failure:
            code, evidence = "binding_assay_supplied", ("public_annotations",)
        elif "direct_edge" in failure:
            code, evidence = "direct_regulatory_edge_identified", ("public_annotations",)
        elif "motif" in failure:
            code, evidence = "motif_reference_supplied", ("motif_hit_annotation", "public_context")
        elif "regulon" in failure:
            code, evidence = "regulon_reference_supplied", ("regulon_annotation", "public_context")
        elif "enrichment" in failure or "annotation_membership" in failure:
            code, evidence = "hypothesis_observed_information", ("public_context", "public_annotations")
        elif "candidate_tf" in failure or "tf_candidate" in failure:
            code, evidence = "candidate_tf_universe_recorded", ("public_context", "counts")
        elif "marker" in failure or "clean_state" in failure or "cell_type_estimates" in failure:
            code, evidence = "cell_identity_reference_supported", ("cell_annotation_reference", "counts", "metadata")
        elif "cyclic_phase" in failure:
            code, evidence = "full_cycle_support", ("process_markers", "counts")
        elif "count" in failure or "continuous" in failure or "measurement" in failure:
            code, evidence = "protocol_and_analysis_layer_recorded", ("public_context", "counts")
        else:
            code, evidence = "hypothesis_observed_information", ("counts", "metadata", "public_context")
        findings.setdefault(code, _check(code, status,
            "The observed-input support check did not establish this criterion: " + failure.replace("_", " ") + ".", evidence))
    return list(findings.values())


def _refuse(target, reason, *, checks=(), restraint=False):
    payload = {"kind": "restraint", "status": "not_identified", "missing_evidence": [reason]} if restraint else None
    combined = [*_refusal_checks(reason), *checks]
    combined = list({check["code"]: check for check in combined}.values())
    return target_fragment(target, claim_type="refusal", evidence_scope="not_identified",
                           payload=payload, limitation_codes=[reason], support_checks=combined)


def _cell_ids(data):
    values = tuple(data.metadata["cell_id"].astype(str)) if "cell_id" in data.metadata else data.sample_ids
    if values != data.sample_ids:
        raise _Unavailable("cell_ids_must_match_transport_sample_ids")
    return values


def _column(data, settings, name="condition_col", default="condition"):
    label = settings.get(name, default)
    if label not in data.metadata or any(not str(value).strip() for value in data.metadata[label]):
        raise _Unavailable("missing_or_empty_" + name)
    return label


def _units(data):
    from .biology_tools_bulk import _unit_audit

    metadata, hierarchy = data.metadata, data.unit_hierarchy
    # The loader projects all supported hierarchy aliases into unit_hierarchy.
    # Retain the legacy negative declaration too; a second declaration cannot
    # silently promote explicitly unknown identities into independent donors.
    declarations = (hierarchy, data.context.get("biological_units", {}))
    if any(isinstance(item, dict) and item.get("identity_status") in {
            "unverified", "unknown", "intentionally_unknown"} for item in declarations):
        raise _Unavailable("biological_unit_identity_unverified")
    audit = _unit_audit(data)
    if not audit["identity_and_nesting_valid"]:
        if any(reason.startswith("missing_") for reason in audit["problems"]):
            raise _Unavailable("missing_biological_unit_identity")
        raise _Unavailable("inconsistent_biological_unit_nesting")
    _cell_ids(data)
    return {"population_unit_column": hierarchy["population_unit_label"],
            "biological_unit_count": audit["biological_unit_count"],
            "specimen_count": audit["specimen_count"], "library_count": audit["library_count"],
            "captured_cell_count": len(data.sample_ids),
            "capture_batch_available": "capture_batch" in metadata,
            "capture_batch_equals_batch": bool("capture_batch" in metadata and "batch" in metadata
                                                and (metadata["capture_batch"] == metadata["batch"]).all()),
            "cells_count_as_independent_donors": False}


def _marker_sets(data):
    reference = data.annotations.get("cell_annotation_reference")
    if not isinstance(reference, dict) or not isinstance(reference.get("markers"), list):
        raise _Unavailable("public_cell_annotation_reference_unavailable")
    if reference.get("query_cell_labels_supplied") is not False:
        raise _Unavailable("marker_reference_must_not_supply_query_cell_labels")
    lookup = set(data.gene_ids)
    sets, absent = {}, []
    for row in reference["markers"]:
        if not isinstance(row, dict) or set(row) != {"label", "gene_ids"}:
            raise _Unavailable("invalid_marker_reference_schema")
        label, genes = row["label"], row["gene_ids"]
        if not isinstance(label, str) or not _IDENTIFIER.fullmatch(label) or label in sets:
            raise _Unavailable("invalid_or_duplicate_marker_label")
        if not isinstance(genes, list) or not genes or len(set(genes)) != len(genes):
            raise _Unavailable("invalid_marker_feature_list")
        if any(not isinstance(gene, str) or not _IDENTIFIER.fullmatch(gene) for gene in genes):
            raise _Unavailable("invalid_marker_feature_id")
        sets[label] = tuple(genes)
        absent.extend(gene for gene in genes if gene not in lookup)
    if not sets:
        raise _Unavailable("empty_public_marker_dictionary")
    return sets, sorted(set(absent))


def _log_view(expression, measurement, *, projected=False):
    import numpy as np

    if measurement == "continuous":
        return expression.copy(), np.ones(expression.shape[1], dtype=bool)
    totals = expression.sum(axis=0)
    nonempty = totals > 0
    scaled = np.zeros_like(expression, dtype=float)
    scaled[:, nonempty] = expression[:, nonempty] / totals[nonempty] * 10000.0
    return np.log1p(scaled), nonempty


def _annotate(data, settings):
    """Public marker scoring; ambiguous and unsupported cells remain unlabeled."""
    import numpy as np

    sets, absent = _marker_sets(data)
    lookup = {gene: index for index, gene in enumerate(data.gene_ids)}
    log, nonempty = _log_view(data.expression, data.measurement)
    centered = log - log.mean(axis=1, keepdims=True)
    scale = log.std(axis=1, ddof=1, keepdims=True) if len(data.sample_ids) > 1 else np.ones((len(data.gene_ids), 1))
    standardized = np.divide(centered, scale, out=np.zeros_like(centered), where=scale > 1e-12)
    names, vectors, detected = [], [], []
    for label, markers in sets.items():
        rows = [lookup[gene] for gene in markers if gene in lookup]
        if not rows:
            continue
        names.append(label)
        vectors.append(standardized[rows].mean(axis=0))
        detected.append((data.expression[rows] > 0).sum(axis=0))
    if not names:
        raise _Unavailable("marker_reference_has_no_measured_features")
    scores, detections = np.asarray(vectors), np.asarray(detected)
    ranks = np.argsort(-scores, axis=0, kind="stable")
    top = ranks[0]
    best = scores[top, np.arange(scores.shape[1])]
    second = scores[ranks[1], np.arange(scores.shape[1])] if len(names) > 1 else np.zeros(scores.shape[1])
    margin = best - second
    labels = [names[int(top[index])] if nonempty[index] and best[index] > 0
              and margin[index] >= settings.get("annotation_margin", 0.25)
              and detections[top[index], index] > 0 else None for index in range(len(data.sample_ids))]
    return {"labels": labels, "marker_scores": scores, "marker_labels": names,
            "top_score": best, "runner_up_score": second, "margin": margin,
            "missing_marker_feature_ids": absent, "reference_coverage": "restricted",
            "method": "mean within-gene standardized log1p depth-normalized public marker expression",
            "uncertain_cell_count": sum(label is None for label in labels),
            "cell_type_labels_are_measured_truth": False}


def _batches(data, settings):
    specified = settings.get("batch_cols")
    columns = list(specified) if specified is not None else [name for name in ("capture_batch", "batch") if name in data.metadata]
    for column in columns:
        if column not in data.metadata or any(not str(value).strip() for value in data.metadata[column]):
            raise _Unavailable("missing_or_empty_declared_batch")
    # Exact observed aliases are one nuisance variable, never extra replication.
    unique = []
    for column in columns:
        if not any((data.metadata[column] == data.metadata[old]).all() for old in unique):
            unique.append(column)
    return unique


def _pseudobulk(data, labels, settings, *, grouping=None, population_ids=None):
    """Collapse observed cells through specimens to donor/group/type columns.

    There is no reindex-to-Cartesian-product operation. Missing donor/type
    combinations never create expression columns, zeros or nominal replicates.
    """
    import numpy as np
    import pandas as pd

    audit = _units(data)
    if not audit["capture_batch_available"]:
        raise _Unavailable("capture_batch_identity_unavailable")
    unit = data.unit_hierarchy["population_unit_label"]
    specimen = data.unit_hierarchy["specimen_label"]
    batches = _batches(data, settings)
    condition = grouping if grouping is not None else data.metadata[_column(data, settings)].astype(str).tolist()
    if len(condition) != len(data.sample_ids) or len(labels) != len(data.sample_ids):
        raise _Unavailable("pseudobulk_membership_alignment_error")
    groups = {}
    for index, (donor, assigned, label) in enumerate(zip(data.metadata[unit], condition, labels)):
        if label is not None:
            groups.setdefault((str(donor), str(assigned), str(label)), []).append(index)
    if not groups:
        raise _Unavailable("no_cells_have_supported_public_marker_labels")
    rows, values, membership = [], [], []
    for number, ((donor, assigned, label), indices) in enumerate(sorted(groups.items())):
        observed = data.metadata.iloc[indices]
        row = {"sample_id": f"pb{number}", unit: donor, "condition": assigned, "cell_type": label,
               specimen: f"pb_specimen{number}", data.unit_hierarchy["library_label"]: f"pb_library{number}"}
        for column in batches:
            observed_levels = observed[column].unique().tolist()
            if len(observed_levels) != 1:
                raise _MethodUnavailable("mixed_batch_pseudobulk_mixture_model_not_implemented")
            row[column] = str(observed_levels[0])
        if data.measurement == "continuous":
            specimen_means = [data.expression[:, [indices[int(i)] for i in positions]].mean(axis=1)
                              for positions in observed.reset_index(drop=True).groupby(specimen, sort=False).indices.values()]
            aggregated = np.stack(specimen_means).mean(axis=0)
        else:
            aggregated = data.expression[:, indices].sum(axis=1)
            if not np.isfinite(aggregated).all() or (aggregated > 2**53 - 1).any():
                raise _Unavailable("pseudobulk_count_sum_exceeds_exact_integer_range")
        rows.append(row)
        values.append(aggregated)
        membership.append({"pseudobulk_id": row["sample_id"], "donor_id": donor, "condition": assigned,
                           "cell_type": label, "cell_count": len(indices),
                           "specimen_ids": sorted(set(observed[specimen])),
                           "cell_ids": [data.sample_ids[index] for index in indices]})
    metadata = pd.DataFrame(rows).set_index("sample_id", drop=False)
    expression = np.asarray(values).T
    expression.flags.writeable = False
    observed_keys = set(groups)
    donors = sorted(set(data.metadata[unit].astype(str)))
    populations = sorted(set(population_ids)) if population_ids is not None else sorted({label for label in labels if label is not None})
    missing = [{"donor_id": donor, "condition": assigned, "cell_type": label}
               for donor in donors for assigned in sorted({str(condition[index]) for index, d in enumerate(data.metadata[unit]) if str(d) == donor})
               for label in populations if (donor, assigned, label) not in observed_keys]
    details = {"unit_audit": audit, "aggregation_definition": (
        "sum raw counts over cells and technical libraries within donor/specimen/condition/type, then sum donor specimens"
        if data.measurement != "continuous" else
        "mean continuous observations within donor/specimen/condition/type, then equal-weight observed donor specimens"),
        "membership": membership, "unavailable_donor_type_combinations": missing,
        "uncertain_cells_not_used_for_type_specific_model": sum(label is None for label in labels),
        "batch_columns": batches, "missing_groups_filled_with_zeros": False}
    return replace(data, expression=expression, sample_ids=tuple(metadata.index), metadata=metadata), details


def _subset(data, indices):
    return replace(data, expression=data.expression[:, indices],
                   sample_ids=tuple(data.sample_ids[index] for index in indices), metadata=data.metadata.iloc[indices].copy())


def _design(data, settings, levels, *, minimum=None):
    import numpy as np
    import pandas as pd
    from .biology_tool_io import require_design_condition

    metadata, unit = data.metadata, data.unit_hierarchy["population_unit_label"]
    observed = metadata["condition"].astype(str)
    if len(set(levels)) != len(levels) or set(observed) != set(levels):
        raise _Unavailable("requested_assignment_or_condition_levels_unavailable")
    minimum = max(settings.get("minimum_units", 3), minimum or 0)
    donor_groups = metadata.groupby(unit)["condition"].agg(lambda values: tuple(sorted(set(values))))
    per_condition = {level: int(metadata.loc[observed == level, unit].nunique()) for level in levels}
    if any(value < minimum for value in per_condition.values()):
        raise _Unavailable("insufficient_independent_donors_per_group")
    if metadata.duplicated([unit, "condition"]).any():
        raise _MethodUnavailable("multiple_rows_per_donor_group_covariance_model_not_implemented")
    paired = any(len(groups) > 1 for groups in donor_groups)
    if paired and any(set(groups) != set(levels) for groups in donor_groups):
        raise _MethodUnavailable("incomplete_repeated_donor_covariance_model_not_implemented")
    # An intercept plus k-1 condition indicators spans the same group-mean
    # model as k one-hot columns. Predictions and contrasts remain on the
    # unchanged biological scale, using the covariance of this fitted basis.
    columns = [np.ones(len(metadata))] + [np.asarray(observed == level, dtype=float) for level in levels[1:]]
    names = ["Intercept"] + [_identifier("condition", level) for level in levels[1:]]
    nuisance = []
    if paired:
        nuisance.append(unit)
    nuisance.extend(_batches(data, settings))
    aliased = []
    for name in nuisance:
        values = metadata[name].astype(str)
        for level in sorted(set(values))[1:]:
            column = np.asarray(values == level, dtype=float)
            base = np.column_stack(columns)
            rank = np.linalg.matrix_rank(base)
            proposed = np.column_stack([base, column])
            if paired and name != unit and np.linalg.matrix_rank(proposed) == rank:
                # Nuisances already represented by donor blocking may be omitted;
                # no group column or non-paired confounder is dropped.
                donor_only = np.column_stack([np.ones(len(metadata))] + [np.asarray(metadata[unit].astype(str) == d, float)
                                          for d in sorted(set(metadata[unit].astype(str)))[1:]])
                if np.linalg.matrix_rank(np.column_stack([donor_only, column])) == np.linalg.matrix_rank(donor_only):
                    aliased.append(_identifier(name, level))
                    continue
            columns.append(column)
            names.append(_identifier(name, level))
    matrix = np.column_stack(columns)
    limit = settings.get("condition_number_limit", DESIGN_CONDITION_NUMBER_LIMIT)
    try:
        details = require_design_condition(matrix, condition_number_limit=limit)
    except ValueError as error:
        raise _Unavailable(_reason(error)) from None
    if details["residual_df"] < 2:
        raise _Unavailable("insufficient_independent_residual_degrees_of_freedom")
    reference = np.tile(matrix.mean(axis=0), (len(levels), 1))
    reference[:, 0] = 1.0
    reference[:, 1:len(levels)] = np.eye(len(levels))[:, 1:]
    frame = pd.DataFrame(matrix, index=metadata.index, columns=names)
    frame.attrs["condition_number_limit"] = limit
    details.update(condition_levels=list(levels), independent_donors_per_condition=per_condition, paired_donor_blocking=paired,
                   nuisance_levels_represented_by_donor_block=aliased,
                   prediction_reference="common observed donor-population average of nuisance indicators")
    return frame, reference, details


def _levels(data, settings):
    observed = sorted(set(data.metadata["condition"].astype(str)))
    requested = data.context.get("requested_condition_contrast", {})
    reference = settings.get("reference_condition", requested.get("denominator"))
    comparison = settings.get("comparison_condition", requested.get("numerator"))
    declared = data.context.get("condition_labels", data.context.get("design", {}).get("condition_levels", []))
    if reference is None and comparison is None and len(observed) == 2:
        reference, comparison = declared if len(declared) == 2 and set(declared) == set(observed) else observed
    if not reference or not comparison or reference == comparison or not {reference, comparison} <= set(observed):
        raise _Unavailable("explicit_supported_condition_contrast_required")
    return [reference, comparison]


def _effect_payload(data, target, rows, details, reference, *, definition, computed_contrast=None):
    from .biology_tools_bulk import _bh

    payload = {"kind": "effects", "analysis_performed": True, "gene_statistics": rows,
            "curve_estimates": [], "multiplicity_scope": _bh(rows),
            "selection_conditional_inference": True,
            "biological_unit": data.unit_hierarchy["population_unit"], "analysis_layer": data.measurement,
            "effect_definition": definition, "reference_condition": reference,
            "condition_number_validated": all(item["condition_number_validated"] for item in details),
            "condition_number": max((item["condition_number"] for item in details), default=None),
            "design_rank": details[0]["rank"] if len(details) == 1 else None,
            "design_column_count": details[0]["column_count"] if len(details) == 1 else None}
    if computed_contrast is not None:
        payload["computed_contrast"] = computed_contrast.model_dump(mode="json")
    return payload


def _state_effect(data, target, settings, diagnostics):
    from .biology_tools_bulk import _fit_family, _linear_estimate
    from .biology_answers import ComputedConditionContrast, public_hypothesis_id

    annotation = _annotate(data, settings)
    population_ids = sorted(_marker_sets(data)[0])
    pseudobulk, membership = _pseudobulk(data, annotation["labels"], settings, population_ids=population_ids)
    levels = _levels(pseudobulk, settings)
    if len(population_ids) * len(data.gene_ids) > 100000:
        raise _MethodUnavailable("complete_gene_by_type_family_exceeds_answer_bound")
    diagnostics.update(annotation_method=annotation["method"], pseudobulk=membership, fits_by_type={})
    rows, designs = [], []
    for label in population_ids:
        indices = [index for index, row in enumerate(pseudobulk.metadata.itertuples())
                   if row.cell_type == label and row.condition in levels]
        subset = _subset(pseudobulk, indices)
        fit_diagnostics = diagnostics["fits_by_type"].setdefault(label, {})
        missing_status = "failed"
        try:
            design, reference, details = _design(subset, settings, levels)
            fit_diagnostics["design"] = details
            fits, failures = _fit_family(subset, design, fit_diagnostics)
            designs.append(details)
        except _Unavailable as error:
            fit_diagnostics["unavailable"] = str(error)
            fits, failures = {}, {gene: str(error) for gene in data.gene_ids}
            missing_status = "unavailable"
        for gene in data.gene_ids:
            row = {"hypothesis_id": public_hypothesis_id(gene, label), "gene_id": gene, "component_id": label}
            if gene not in fits:
                row.update(estimate_status=missing_status, missing_reason=failures.get(gene, "fit_unavailable"))
            else:
                try:
                    values, intervals, p_value = _linear_estimate(fits[gene], reference[1] - reference[0], data.measurement != "continuous")
                    row.update(estimate_status="estimated", coordinate_ids=["condition_contrast"], values=values,
                               intervals=intervals, p_value=p_value, q_value=p_value)
                except (ValueError, ArithmeticError, RuntimeError) as error:
                    row.update(estimate_status="failed", missing_reason=_reason(error))
            rows.append(row)
    if not any(row["estimate_status"] == "estimated" for row in rows):
        if any(row["estimate_status"] == "failed" for row in rows):
            raise _MethodUnavailable("all_attempted_donor_cell_type_fits_failed")
        raise _Unavailable("no_supported_donor_cell_type_estimates")
    limitations = ["marker_annotation_is_uncertain", "conditional_on_observed_type_membership",
                  "capture_is_not_population_sampling", "empirical_bayes_and_annotation_conditional_inference"]
    if any(row["estimate_status"] != "estimated" for row in rows):
        limitations.append("some_donor_type_fits_unavailable")
    computed_contrast = ComputedConditionContrast.from_measurement(data.measurement, levels[0], levels[1])
    payload = _effect_payload(data, target, rows, designs, levels[0],
        definition="Donor-level within-publicly-annotated-type contrast: " + computed_contrast.describe(),
        computed_contrast=computed_contrast)
    payload["aggregation_definition"] = membership["aggregation_definition"]
    return target_fragment(target, claim_type="restricted_estimate", evidence_scope="within_cell_type",
                           payload=payload, limitation_codes=limitations)


_PROJECTION_CONTRACT = {
    "scope": "shared_process_quotient",
    "rule": "exclude_union_of_declared_broad_type_identity_markers",
    "marker_reference_id": "cell_annotation_reference", "analysis_layer": "counts",
    "full_geometry_scope": "not_claimed", "all_observations_retained": True,
    "full_counts_preserved": True, "marker_genes_are_nuisance_for_other_questions": False,
}


def _projection(data, target):
    if data.measurement == "continuous":
        raise _Unavailable("shared_process_target_requires_unchanged_count_layer")
    declaration = data.context.get("shared_process_projection")
    question = target.get("projection_contract", target.get("shared_process_projection"))
    if not isinstance(declaration, dict):
        raise _Unavailable("public_shared_process_projection_unavailable")
    for key, value in _PROJECTION_CONTRACT.items():
        if declaration.get(key) != value or type(declaration.get(key)) is not type(value):
            raise _Unavailable("incompatible_public_shared_process_projection")
        if question is not None and (not isinstance(question, dict) or question.get(key) != value
                                     or type(question.get(key)) is not type(value)):
            raise _Unavailable("question_and_context_projection_disagree")
    sets, absent = _marker_sets(data)
    if absent:
        raise _Unavailable("projection_marker_union_contains_unmeasured_features")
    union = set().union(*(set(genes) for genes in sets.values()))
    excluded, retained = declaration.get("excluded_feature_ids"), declaration.get("retained_feature_universe")
    if not isinstance(excluded, list) or len(set(excluded)) != len(excluded) or set(excluded) != union:
        raise _Unavailable("projection_exclusion_is_not_exact_public_marker_union")
    expected = [gene for gene in data.gene_ids if gene not in union]
    if not excluded or not expected or retained != expected:
        raise _Unavailable("projection_retained_feature_universe_mismatch")
    if data.context.get("feature_universe", list(data.gene_ids)) != list(data.gene_ids):
        raise _Unavailable("public_feature_universe_mismatch")
    projection = dict(_PROJECTION_CONTRACT, excluded_feature_ids=excluded, retained_feature_universe=expected)
    rows = [index for index, gene in enumerate(data.gene_ids) if gene not in union]
    return {"shared_process_projection": projection, "analysis_layer": "counts",
            "retained_feature_universe": expected, "excluded_feature_ids": excluded}, rows


def _geometry(data, target, settings):
    """PCA/kNN/cluster-MST prototype, retaining disconnected observed support.

    This follows the existing lineage tool's PCA, neighbour and centroid-tree
    method family. It uses the exact public feature view, no subsampling, no
    collection age and no forced root. Each maximal tree segment has its own
    unoriented coordinate; coordinates across different branches are not ranked.
    """
    import numpy as np
    from scipy.sparse import csr_matrix
    from scipy.sparse.csgraph import connected_components, minimum_spanning_tree
    from sklearn.cluster import KMeans
    from sklearn.decomposition import PCA
    from sklearn.neighbors import NearestNeighbors

    _units(data)
    declaration, rows = _projection(data, target)
    n = len(data.sample_ids)
    if n < 4:
        raise _Unavailable("insufficient_observed_cells_for_geometry")
    if n > settings.get("max_cells", 2000) or len(rows) > 5000:
        raise _MethodUnavailable("all_cell_geometry_exceeds_bounded_method_support")
    log, nonempty = _log_view(data.expression[rows], data.measurement, projected=True)
    if not nonempty.all():
        raise _Unavailable("some_cells_lack_expression_in_declared_process_view")
    centered = log - log.mean(axis=1, keepdims=True)
    scale = log.std(axis=1, ddof=1, keepdims=True)
    if int((scale > 1e-12).sum()) < 2:
        raise _Unavailable("declared_process_view_has_insufficient_variable_features")
    x = np.divide(centered, scale, out=np.zeros_like(centered), where=scale > 1e-12).T
    components = min(settings.get("n_components", 10), n - 1, x.shape[1])
    model = PCA(n_components=components, svd_solver="full")
    coordinates = model.fit_transform(x)
    neighbours = min(settings.get("n_neighbors", 10), n - 1)
    distances, indices = NearestNeighbors(n_neighbors=neighbours + 1).fit(coordinates).kneighbors(coordinates)
    edges = []
    for a, (distance_row, index_row) in enumerate(zip(distances, indices)):
        for distance, b in zip(distance_row, index_row):
            if int(b) != a:
                edges.append((a, int(b), float(distance)))
    positive = [distance for _, _, distance in edges if distance > 1e-12]
    if not positive:
        raise _Unavailable("observed_process_geometry_is_degenerate")
    edge_scale = float(np.median(positive))
    cutoff = settings.get("gap_multiplier", 3.0) * edge_scale
    eligible = [(a, b, max(distance, 1e-12)) for a, b, distance in edges if distance <= cutoff]
    adjacency = csr_matrix(([distance for _, _, distance in eligible],
                            ([a for a, _, _ in eligible], [b for _, b, _ in eligible])), shape=(n, n))
    adjacency = adjacency.maximum(adjacency.T)
    count, component = connected_components(adjacency, directed=False)
    allocation = np.ones(count, dtype=int)
    sizes = np.bincount(component)
    budget = max(count, min(settings.get("n_clusters", 8), n))
    while int(allocation.sum()) < budget:
        candidates = [index for index in range(count) if allocation[index] < sizes[index]]
        if not candidates:
            break
        chosen = max(candidates, key=lambda index: (sizes[index] / allocation[index], -index))
        allocation[chosen] += 1
    cluster = np.full(n, -1, dtype=int)
    centroids, cluster_components = [], []
    for group in range(count):
        members = np.flatnonzero(component == group)
        points = coordinates[members]
        clusters = min(int(allocation[group]), len(np.unique(points, axis=0)))
        if clusters == 1:
            local = np.zeros(len(members), dtype=int)
        else:
            first = int(np.argmax(np.sum((points - points.mean(axis=0)) ** 2, axis=1)))
            centers = [points[first]]
            while len(centers) < clusters:
                closest = np.min(np.sum((points[:, None, :] - np.asarray(centers)[None, :, :]) ** 2, axis=2), axis=1)
                centers.append(points[int(np.argmax(closest))])
            # The initializer is entirely determined by observed coordinates.
            local = KMeans(n_clusters=clusters, init=np.asarray(centers), n_init=1,
                           random_state=0, algorithm="lloyd").fit_predict(points)
        for local_id in sorted(set(local), key=lambda label: min(data.sample_ids[int(i)] for i in members[local == label])):
            selected = members[local == local_id]
            cluster[selected] = len(centroids)
            centroids.append(coordinates[selected].mean(axis=0))
            cluster_components.append(group)
    centers = np.asarray(centroids)
    coarse = np.zeros((len(centers), len(centers)))
    for a, b, _ in eligible:
        ca, cb = int(cluster[a]), int(cluster[b])
        if ca != cb:
            distance = max(float(np.linalg.norm(centers[ca] - centers[cb])), 1e-12)
            coarse[ca, cb] = coarse[cb, ca] = distance
    tree = minimum_spanning_tree(csr_matrix(coarse)).tocoo()
    links = {index: [] for index in range(len(centers))}
    for a, b, distance in zip(tree.row, tree.col, tree.data):
        links[int(a)].append((int(b), float(distance)))
        links[int(b)].append((int(a), float(distance)))
    paths, visited = [], set()
    for start in sorted(node for node, neighbors in links.items() if len(neighbors) != 2):
        for neighbor, _ in sorted(links[start]):
            key = tuple(sorted((start, neighbor)))
            if key in visited:
                continue
            path, previous, current = [start, neighbor], start, neighbor
            visited.add(key)
            while len(links[current]) == 2:
                following = next(node for node, _ in links[current] if node != previous)
                visited.add(tuple(sorted((current, following))))
                path.append(following)
                previous, current = current, following
            paths.append(path)
    if len(paths) > 28:
        raise _MethodUnavailable("observed_graph_exceeds_answer_edge_bound")
    state_graph = [{"source": f"node{path[0]}", "target": f"node{path[-1]}"} for path in paths]
    order, resolved = [], []
    for index, sample in enumerate(data.sample_ids):
        possibilities = []
        for path_id, path in enumerate(paths):
            if cluster_components[path[0]] != int(component[index]):
                continue
            cumulative = 0.0
            length = sum(float(np.linalg.norm(centers[b] - centers[a])) for a, b in zip(path[:-1], path[1:]))
            for a, b in zip(path[:-1], path[1:]):
                vector = centers[b] - centers[a]
                edge_length = float(np.linalg.norm(vector))
                fraction = float(np.clip(np.dot(coordinates[index] - centers[a], vector) / max(edge_length ** 2, 1e-24), 0, 1))
                distance = float(np.linalg.norm(coordinates[index] - centers[a] - fraction * vector))
                possibilities.append((distance, path_id, (cumulative + fraction * edge_length) / max(length, 1e-12)))
                cumulative += edge_length
        if not possibilities:
            order.append({"cell_id": sample, "group": f"isolated_component{int(component[index])}", "order": None})
            resolved.append(False)
            continue
        choices = sorted(possibilities)
        best = choices[0]
        other = next((choice for choice in choices if choice[1] != best[1]), None)
        branch_resolved = other is None or other[0] - best[0] > 0.1 * edge_scale
        order.append({"cell_id": sample, "group": f"branch{best[1]}", "order": best[2]})
        resolved.append(branch_resolved)
    gaps = ["disconnected_observed_support"] if count > 1 else []
    if any(not value for value in resolved):
        gaps.append("some_branch_memberships_are_ambiguous")
    if not paths:
        gaps.append("no_supported_between_cluster_edges")
    payload = dict(declaration, kind="graph", state_graph=state_graph, within_branch_order=order,
                   orientation_uncertainty="unanchored", support_gaps=gaps)
    details = {"algorithm": "all-cell public-projection PCA / pruned symmetric kNN / deterministic cluster MST forest / segment projection",
               "transform": "log1p per-cell depth normalization within the declared feature view, then gene standardization",
               "observation_count": n, "all_observations_retained": True,
               "input_counts_changed": False, "fitted_feature_count": len(rows),
               "connected_components": int(count), "n_neighbors": neighbours,
               "n_components": components, "n_clusters": len(centers), "edge_distance_cutoff": cutoff,
               "pca_variance_explained": float(model.explained_variance_ratio_.sum()),
               "sampling_time_used_for_fit_or_direction": False, "measured_root_cell_available": False,
               "root_evidence": "No cell-linked measured state/time root is supplied by the public history-record schema.",
               "limitations": ["Tree approximation does not establish biological topology, convergence, loops or ancestry.",
                               "Cluster resolution, capture, batch and gap threshold affect the exploratory graph.",
                               "Each segment coordinate is unanchored; no cross-branch total order is claimed."]}
    return payload, details, resolved


def _graph_target(data, target, settings, diagnostics):
    payload, details, _ = _geometry(data, target, settings)
    diagnostics.update(details)
    return target_fragment(target, claim_type="restricted_estimate", evidence_scope="state_geometry", payload=payload,
        limitation_codes=["exploratory_cluster_mst_geometry", "orientation_unanchored", "capture_and_batch_can_change_geometry",
                          "no_ancestry_or_exact_convergence_claim"])


def _programs(data, target, settings, diagnostics):
    import numpy as np
    from sklearn.decomposition import PCA

    _units(data)
    program = data.annotations.get("process_markers")
    if not isinstance(program, dict):
        raise _Unavailable("public_process_markers_unavailable")
    declaration, retained = _projection(data, target)
    name = "cycle" if target["target_id"] == "cycle_state" else "maturation"
    genes = program.get(name)
    if not isinstance(genes, list) or len(genes) < 2 or len(set(genes)) != len(genes):
        raise _Unavailable("insufficient_distinct_public_process_markers")
    if any(gene not in declaration["retained_feature_universe"] for gene in genes):
        raise _Unavailable("process_markers_outside_declared_feature_view")
    log, nonempty = _log_view(data.expression[retained], data.measurement, projected=True)
    if not nonempty.all():
        raise _Unavailable("some_cells_lack_expression_in_declared_process_view")
    retained_lookup = {data.gene_ids[index]: position for position, index in enumerate(retained)}
    x = log[[retained_lookup[gene] for gene in genes]].T
    scale = x.std(axis=0, ddof=1)
    x = np.divide(x - x.mean(axis=0), scale, out=np.zeros_like(x), where=scale > 1e-12)
    if np.linalg.matrix_rank(x) < (2 if name == "cycle" else 1):
        raise _Unavailable("public_process_marker_scores_have_insufficient_rank")
    pca = PCA(n_components=2, svd_solver="full")
    components = pca.fit_transform(x)
    diagnostics.update(process_marker_source=program.get("source"), score_gene_ids=genes,
                       program_variance_explained=pca.explained_variance_ratio_.tolist(),
                       cell_cycle_regressed_out=False, collection_age_used=False,
                       score_scope="empirical public-marker program coordinate; not latent biological ground truth")
    if name == "cycle":
        if pca.explained_variance_[1] <= 1e-8 * pca.explained_variance_[0]:
            raise _Unavailable("cyclic_phase_not_identified_by_single_observed_axis")
        scaled = components / np.sqrt(pca.explained_variance_)
        phase = np.mod(np.arctan2(scaled[:, 1], scaled[:, 0]), 2 * math.pi)
        angular = np.sort(phase)
        max_gap = float(np.max(np.diff(np.r_[angular, angular[0] + 2 * math.pi])))
        scope = "full_cycle" if len(phase) >= 20 and max_gap < math.pi / 2 else "observed_arc"
        values = [{"cell_id": cell, "group": "public_cycle_program", "phase_radians": float(value)}
                  for cell, value in zip(data.sample_ids, phase)]
        diagnostics.update(max_observed_angular_gap=max_gap, phase_coverage_definition="PCA-score angular occupancy, not proof of a complete biological cycle")
    else:
        graph, geometry, _ = _geometry(data, target, settings)
        diagnostics["branch_geometry"] = geometry
        values = []
        groups = [coordinate["group"] for coordinate in graph["within_branch_order"]]
        for index, cell in enumerate(data.sample_ids):
            members = np.asarray([position for position, group in enumerate(groups) if group == groups[index]])
            score = components[members, 0]
            width = float(score.max() - score.min())
            value = float((components[index, 0] - score.min()) / width) if width > 1e-12 else None
            values.append({"cell_id": cell, "group": groups[index], "order": value})
        scope = "not_applicable"
    payload = dict(declaration, kind="ordering", coordinates=values,
                   orientation_uncertainty="unanchored", cycle_target_scope=scope)
    return target_fragment(target, claim_type="restricted_estimate", evidence_scope="within_branch_ordering",
        payload=payload, limitation_codes=["public_marker_program_is_observed_association", "orientation_unanchored",
                                         "process_separation_depends_on_marker_specificity", "no_automatic_cycle_regression"])


def _history(data, target, settings, diagnostics):
    audit = _units(data)
    history = data.annotations.get("history_markers")
    diagnostics.update(unit_audit=audit, history_annotation_present=isinstance(history, dict))
    if target["target_id"] == "transition_dynamics":
        return _refuse(target, "timed_within_cell_state_transitions_and_sampling_model_unavailable", restraint=True,
            checks=[_check("transition_dynamics_measured", "unknown",
                "Snapshot occupancy and founder equivalence do not identify timed transitions or fate rates.", ("public_context", "history_markers"))])
    if not isinstance(history, dict) or not isinstance(history.get("cell_records"), list):
        reason = "cell_linked_lineage_history_unavailable"
        return _refuse(target, reason, restraint=target["target_id"] == "transition_dynamics")
    records = {}
    for row in history["cell_records"]:
        if not isinstance(row, dict) or set(row) != {"cell_id", "founder_record"}:
            raise _Unavailable("invalid_lineage_history_record")
        cell, founder = row["cell_id"], row["founder_record"]
        if cell in records or cell not in data.sample_ids or not isinstance(founder, str) or not founder:
            raise _Unavailable("invalid_or_duplicate_lineage_history_join")
        records[cell] = founder
    diagnostics.update(record_count=len(records), unrecorded_cell_count=len(data.sample_ids) - len(records),
                       measurement=history.get("measurement"), recording_scheme=history.get("recording_scheme"),
                       founder_expression_state_measured=history.get("founder_expression_state_measured"),
                       declared_direction_anchor_available=history.get("direction_anchor_available"),
                       cell_linked_temporal_state_anchor_present=False)
    if not records:
        raise _Unavailable("no_observed_lineage_history_records")
    unit = data.unit_hierarchy["population_unit_label"]
    groups = [{"cell_id": cell, "group": _identifier(data.metadata.iloc[index][unit], records[cell])}
              for index, cell in enumerate(data.sample_ids) if cell in records]
    payload = {"kind": "ancestry", "ancestry_status": "equivalence_only", "clone_equivalence": groups,
               "history_evidence": ["history_markers"], "direction_identified": False}
    return target_fragment(target, claim_type="restricted_estimate", evidence_scope="clone_equivalence", payload=payload,
        limitation_codes=["founder_records_establish_equivalence_only", "within_path_direction_unavailable",
                          "barcode_collisions_or_recording_error_not_calibrated"])


def _quality(data, settings, diagnostics):
    import numpy as np

    audit = _units(data)
    if data.measurement == "continuous":
        raise _Unavailable("count_based_cell_detection_diagnostics_do_not_apply_to_continuous_layer")
    annotation = None
    try:
        annotation = _annotate(data, settings)
    except _Unavailable as error:
        diagnostics["cell_annotation_unavailable"] = str(error)
    labels = annotation["labels"] if annotation else [None] * len(data.sample_ids)
    totals = data.expression.sum(axis=0)
    detected = (data.expression > 0).sum(axis=0)
    batches = data.metadata["capture_batch"].astype(str).tolist() if "capture_batch" in data.metadata else ["unreported"] * len(labels)
    strata = {}
    for index, (batch, label) in enumerate(zip(batches, labels)):
        strata.setdefault((batch, label), []).append(index)
    bounds, outliers = [], {}
    for (batch, label), indices in strata.items():
        value = np.log1p(totals[indices])
        median = float(np.median(value))
        mad = float(np.median(np.abs(value - median)))
        width = settings.get("qc_mad_multiplier", 3.0) * 1.4826 * mad
        available = len(indices) >= 5 and mad > 1e-12
        bounds.append({"capture_batch": batch, "annotated_type": label, "cell_count": len(indices),
                       "log_library_median": median, "log_library_mad": mad,
                       "contextual_outlier_threshold_available": available})
        for index in indices:
            outliers[index] = (bool(available and math.log1p(totals[index]) < median - width),
                               bool(available and math.log1p(totals[index]) > median + width))
    decisions = []
    for index, cell in enumerate(data.sample_ids):
        low, high = outliers[index]
        if totals[index] <= 0:
            decision, reason = "exclude", "no_observed_rna_for_state_estimation"
        elif high:
            decision, reason = "uncertain", "contextual_high_rna_possible_multiplet"
        elif low and labels[index] is None:
            decision, reason = "uncertain", "low_rna_without_supported_marker_identity"
        elif low:
            decision, reason = "retain", "low_rna_with_compatible_public_markers"
        elif labels[index] is None:
            decision, reason = "uncertain", "public_marker_assignment_ambiguous"
        else:
            decision, reason = "retain", "no_contextual_artifact_flag"
        decisions.append({"cell_id": cell, "decision": decision, "reason": reason})
    diagnostics.update(unit_audit=audit, contextual_strata=bounds,
                       per_cell_detection=[{"cell_id": cell, "observed_total": float(total), "detected_gene_count": int(number)}
                                           for cell, total, number in zip(data.sample_ids, totals, detected)],
                       mitochondrial_fraction={"status": "unavailable", "reason": "gene_resolved_mitochondrial_annotation_not_supplied"},
                       library_size_is_absolute_rna_content=False,
                       thresholds_are_calibrated_artifact_probabilities=False)
    payload = {"kind": "diagnostics", "diagnostics": [
        {"code": "captured_cell_count", "value": len(data.sample_ids)},
        {"code": "empty_observed_libraries", "value": int((totals == 0).sum())},
        {"code": "mitochondrial_fraction", "value": None, "missing_reason": "mitochondrial_gene_annotation_unavailable"},
        {"code": "structural_zero_mechanism_identified", "value": False},
        {"code": "artifact_thresholds_calibrated", "value": False},
        {"code": "low_rna_implies_cell_failure", "value": False}],
        "qc_decisions": decisions, "biological_unit_count": audit["biological_unit_count"],
        "measurement_type": data.measurement, "analysis_route": "count_likelihood",
        "model_family": "contextual_observed_count_diagnostics", "protocol": data.context.get("protocol", data.context.get("assay", "protocol_unspecified")),
        "analysis_layer": "counts", "transformation_history": data.context.get("transformation_history", [])}
    return payload, annotation


def _quality_target(data, target, settings, diagnostics):
    payload, _ = _quality(data, settings, diagnostics)
    payload["individual_artifact_identification"] = "not_identified"
    return target_fragment(target, claim_type="restricted_estimate", evidence_scope="measurement_diagnostic", payload=payload,
        limitation_codes=["contextual_qc_thresholds_uncalibrated", "mitochondrial_fraction_unavailable",
                          "observed_zeros_do_not_identify_structural_dropout", "low_rna_cells_can_be_viable"])


def _contamination(data, target, settings, diagnostics):
    import numpy as np

    qc, annotation = _quality(data, settings, diagnostics)
    if annotation is None:
        raise _Unavailable("public_marker_labels_unavailable_for_clean_state_assignment")
    decisions = [dict(row) for row in qc["qc_decisions"]]
    ambiguous = set()
    for index, row in enumerate(decisions):
        if annotation["top_score"][index] > 0.8 and annotation["runner_up_score"][index] > 0.8:
            if row["decision"] != "exclude":
                row.update(decision="uncertain", reason="coexpressed_public_type_markers_possible_heterotypic_mixture")
                ambiguous.add(data.sample_ids[index])
    ambient = data.annotations.get("ambient_reference")
    if isinstance(ambient, dict):
        vector = np.asarray(ambient.get("counts"), dtype=float)
        if vector.shape != (len(data.gene_ids),) or not np.isfinite(vector).all() or (vector < 0).any() or (vector != np.floor(vector)).any():
            raise _Unavailable("ambient_reference_not_aligned_nonnegative_counts")
        if vector.sum() <= 0:
            raise _Unavailable("ambient_reference_has_no_observed_signal")
        total = data.expression.sum(axis=0)
        profile = np.divide(data.expression, total, out=np.zeros_like(data.expression), where=total > 0)
        reference = vector / vector.sum()
        numerator = reference @ profile
        denominator = np.linalg.norm(reference) * np.linalg.norm(profile, axis=0)
        similarity = np.divide(numerator, denominator, out=np.zeros_like(numerator), where=denominator > 0)
        diagnostics["ambient_reference_comparison"] = {
            "capture_batch_scope": ambient.get("capture_batch_scope"),
            "per_cell_profile_cosine": [{"cell_id": cell, "similarity": float(value) if total[index] > 0 else None}
                                         for index, (cell, value) in enumerate(zip(data.sample_ids, similarity))],
            "interpretation": "reference-profile similarity, not an identified ambient mixture fraction",
            "counts_subtracted": False}
    else:
        diagnostics["ambient_reference_comparison"] = {"status": "unavailable", "reason": "ambient_reference_absent"}
    uncertain = [cell for cell, row, label in zip(data.sample_ids, decisions, annotation["labels"])
                 if label is None or row["decision"] == "uncertain" or cell in ambiguous]
    payload = {"kind": "cell_state", "cell_type_labels": [
        {"cell_id": cell, "group": label if row["decision"] == "retain" else None}
        for cell, label, row in zip(data.sample_ids, annotation["labels"], decisions)],
        "qc_decisions": decisions, "reference_coverage": annotation["reference_coverage"], "uncertain_labels": uncertain}
    return target_fragment(target, claim_type="restricted_estimate", evidence_scope="measurement_diagnostic", payload=payload,
        limitation_codes=["ambient_fraction_not_identified", "homotypic_multiplets_not_resolved_by_expression",
                          "marker_mixture_is_not_new_cell_type", "uncalibrated_artifact_candidates_remain_uncertain"])


def _composition(data, target, settings, diagnostics):
    audit = _units(data)
    cell_ids = _cell_ids(data)
    limitations = ["relative_fraction_is_not_absolute_cell_abundance", "annotation_and_capture_conditional_inference"]
    if target["target_id"] == "captured_abundance":
        annotation = _annotate(data, settings)
        labels = annotation["labels"]
        eligible = [True] * len(cell_ids)
        weight = 1.0
        denominator = "All captured cells within each observed donor and condition, including cells with uncertain type labels."
        diagnostics["annotation_method"] = annotation["method"]
    else:
        calibration = data.annotations.get("capture_calibration")
        if not isinstance(calibration, dict):
            raise _Unavailable("independent_relative_capture_calibration_unavailable")
        probability = calibration.get("sampling_probability")
        if type(probability) not in (int, float) or not math.isfinite(probability) or not 0 < probability <= 1:
            raise _Unavailable("invalid_observed_capture_probability")
        if not data.annotations.get("state_root_anchor"):
            raise _Unavailable("independent_state_root_anchor_unavailable")
        # Presence of an arbitrary object cannot qualify a new anchor assay or
        # turn the old unrooted edge-count heuristic into a rooted estimator.
        raise _MethodUnavailable("rooted_branch_abundance_estimator_not_implemented")
    condition = data.metadata[_column(data, settings)].astype(str).tolist() if "condition" in data.metadata or "condition_col" in settings else ["observed"] * len(cell_ids)
    unit = data.unit_hierarchy["population_unit_label"]
    groups = {}
    for index, (donor, level) in enumerate(zip(data.metadata[unit], condition)):
        groups.setdefault((str(donor), level), []).append(index)
    fractions, denominators, unavailable = [], [], []
    for (donor, level), indices in sorted(groups.items()):
        members = [index for index in indices if eligible[index]]
        if not members:
            unavailable.append({"donor_id": donor, "condition": level, "reason": "no_eligible_branch_resolved_cells"})
            continue
        observed_labels = sorted({labels[index] for index in members if labels[index] is not None})
        denominator_weight = len(members) * weight
        for label in observed_labels:
            count = sum(labels[index] == label for index in members)
            fractions.append({"population_id": label, "coordinate_id": _identifier(level, donor),
                              "fraction": count * weight / denominator_weight})
        denominators.append({"donor_id": donor, "condition": level, "eligible_cell_count": len(members),
                             "unresolved_label_count": sum(labels[index] is None for index in members),
                             "excluded_from_denominator": len(indices) - len(members)})
    if not fractions:
        raise _Unavailable("no_observed_supported_population_fractions")
    # Average independent donors for the population question; retain donor
    # details, and use pooled cells only for an observed-subset question.
    donor_fractions = fractions
    aggregate = []
    type_ids = sorted(_marker_sets(data)[0])
    observed_subset = data.context.get("population_reference") == "observed captured subset"
    for level in sorted(set(condition)):
        group_keys = [key for key in groups if key[1] == level]
        for label in type_ids:
            local = [(sum(labels[index] == label for index in groups[key]), len(groups[key])) for key in group_keys]
            fraction = (sum(count for count, _ in local)/sum(size for _, size in local) if observed_subset else
                        sum(count/size for count, size in local)/len(local))
            aggregate.append({"population_id": label, "coordinate_id": level, "fraction": fraction})
    fractions = aggregate
    denominator = "observed_captured_subset_within_condition" if observed_subset else "all_captured_viable_cells"
    diagnostics.update(unit_audit=audit, denominators=denominators, unavailable_donor_denominators=unavailable,
                       donor_fraction_estimates=donor_fractions,
                       condition_aggregation="pooled_observed_cells" if observed_subset else "equal_weight_independent_donors",
                       unobserved_type_groups_imputed=False, binomial_cells_as_independent_donors=False,
                       fraction_intervals="not estimated; cell-binomial intervals do not quantify donor or annotation uncertainty")
    payload = {"kind": "fractions", "cell_fractions": fractions, "rna_fractions": [],
               "reference_denominator": denominator, "reference_coverage": "restricted", "absolute_population_claimed": False}
    return target_fragment(target, claim_type="restricted_estimate", evidence_scope="relative_composition",
                           payload=payload, limitation_codes=limitations)


def _network(data, target, settings, diagnostics):
    import numpy as np

    if data.measurement == "continuous":
        raise _Unavailable("network_log_signal_contract_requires_declared_count_measurement")
    candidates = data.context.get("transcription_factor_annotation")
    if not isinstance(candidates, list) or not candidates or len(set(candidates)) != len(candidates):
        raise _Unavailable("prespecified_public_tf_candidate_universe_unavailable")
    if any(gene not in data.gene_ids for gene in candidates):
        raise _Unavailable("candidate_tf_outside_measured_feature_universe")
    if len(candidates) * len(data.gene_ids) > 250000:
        raise _MethodUnavailable("prespecified_network_pair_family_exceeds_bounded_budget")
    condition_col = _column(data, settings)
    observed_conditions = sorted(set(data.metadata[condition_col].astype(str)))
    condition = settings.get("network_condition")
    if condition is None:
        if len(observed_conditions) != 1:
            raise _Unavailable("network_condition_must_be_prespecified_for_multicondition_input")
        condition = observed_conditions[0]
    indices = [index for index, value in enumerate(data.metadata[condition_col]) if str(value) == condition]
    if not indices:
        raise _Unavailable("requested_network_condition_unavailable")
    observed = _subset(data, indices)
    pseudobulk, membership = _pseudobulk(observed, ["captured_population"] * len(indices), settings)
    design, _, details = _design(pseudobulk, settings, [condition])
    log, nonempty = _log_view(pseudobulk.expression, data.measurement)
    if not nonempty.all():
        raise _Unavailable("network_donor_pseudobulk_has_empty_library")
    residual = log.T - design.to_numpy() @ np.linalg.lstsq(design.to_numpy(), log.T, rcond=None)[0]
    norm = np.linalg.norm(residual, axis=0)
    standardized = np.divide(residual, norm, out=np.zeros_like(residual), where=norm > 1e-12)
    lookup = {gene: index for index, gene in enumerate(data.gene_ids)}
    edges, rank_scores, constant, recorded_pairs = [], {}, [], set()
    for tf in candidates:
        index = lookup[tf]
        if norm[index] <= 1e-12:
            constant.append(tf)
            continue
        scores = standardized[:, index] @ standardized
        values = []
        for other, gene in enumerate(data.gene_ids):
            if gene == tf or norm[other] <= 1e-12:
                continue
            value = float(np.clip(scores[other], -1.0, 1.0))
            values.append(abs(value))
            if abs(value) >= settings.get("correlation_threshold", 0.5):
                pair = tuple(sorted((tf, gene)))
                if pair not in recorded_pairs:
                    recorded_pairs.add(pair)
                    edges.append({"source": pair[0], "target": pair[1], "covariance": value})
        if values:
            rank_scores[tf] = float(sum(values) / len(values))
    if not rank_scores:
        raise _Unavailable("no_variable_candidate_tf_after_donor_nuisance_adjustment")
    ordered = sorted(edges, key=lambda row: (-abs(row["covariance"]), row["source"], row["target"]))
    ranked = sorted(rank_scores, key=lambda tf: (-rank_scores[tf], tf))
    diagnostics.update(pseudobulk=membership, design=details, constant_candidate_tfs=constant,
                       ranking_definition="mean absolute residual donor-level log-expression correlation across variable measured genes",
                       candidate_scores=rank_scores, tested_association_p_values=False,
                       edge_count_before_output_bound=len(edges), output_truncated=len(edges) > settings.get("max_edges", 2000))
    payload = {"kind": "association", "association_edges": ordered[:settings.get("max_edges", 2000)],
               "module_membership": [], "candidate_regulators": ranked, "candidate_universe": candidates,
               "covariance_scale": "log_expression_signal_correlation", "condition": condition,
               "claims_direct_causality": False}
    return target_fragment(target, claim_type="restricted_estimate", evidence_scope="association", payload=payload,
        limitation_codes=["tf_expression_is_not_measured_activity", "correlation_is_not_regulatory_causality",
                          "cell_composition_can_generate_donor_associations", "association_ranking_has_no_calibrated_fdr"])


def _enrichment(data, target, settings, diagnostics):
    from devharness_server.tools_network import _enrichment_rows

    query = data.context.get("enrichment_query_gene_ids")
    background = data.context.get("enrichment_background_gene_ids")
    selection = data.context.get("enrichment_query_selection")
    if not isinstance(query, list) or not query or len(set(query)) != len(query):
        raise _Unavailable("fixed_public_enrichment_query_unavailable")
    if background != list(data.gene_ids) or not set(query) <= set(background):
        raise _Unavailable("enrichment_requires_complete_ordered_measured_background")
    if not isinstance(selection, dict) or selection.get("query_count") != len(query):
        raise _Unavailable("enrichment_query_selection_provenance_unavailable")
    motif = target["target_id"] == "motif_occurrence"
    key = "motif_hit_annotation" if motif else "regulon_annotation"
    reference = data.annotations.get(key)
    field = "hits" if motif else "memberships"
    if not isinstance(reference, dict) or not isinstance(reference.get(field), list) or not reference[field]:
        raise _Unavailable("public_" + key + "_unavailable")
    memberships, scanned, hit_values, outside = {}, {}, {}, set()
    for row in reference[field]:
        required = {"tf_id", "target_id", "hit_count"} if motif else {"tf_id", "target_id"}
        if not isinstance(row, dict) or set(row) != required:
            raise _Unavailable("invalid_public_annotation_membership_schema")
        tf, gene = row["tf_id"], row["target_id"]
        if any(not isinstance(value, str) or not _IDENTIFIER.fullmatch(value) for value in (tf, gene)):
            raise _Unavailable("invalid_public_annotation_feature_identifier")
        memberships.setdefault(tf, set())
        if gene not in background:
            outside.add(gene)
        if motif:
            count = row["hit_count"]
            if type(count) is not int or count < 0:
                raise _Unavailable("motif_hit_count_must_be_nonnegative_integer")
            pair = (tf, gene)
            if pair in hit_values and hit_values[pair] != (count > 0):
                raise _Unavailable("conflicting_public_motif_occurrence_calls")
            hit_values[pair] = count > 0
            scanned.setdefault(tf, set()).add(gene)
            if count > 0:
                memberships[tf].add(gene)
        else:
            memberships[tf].add(gene)
    if len(memberships) > 10000:
        raise _Unavailable("annotation_family_exceeds_enrichment_answer_bound")
    if len(memberships) * len(query) > 250000:
        raise _MethodUnavailable("complete_annotation_overlap_output_exceeds_bounded_budget")
    if motif and any(not set(background) <= seen for seen in scanned.values()):
        raise _Unavailable("motif_background_requires_explicit_scanned_hit_or_no_hit_calls")
    measured_background = set(background)
    rows = _enrichment_rows(set(query), measured_background, memberships, "regulator")
    estimates = [{"regulator_id": row["regulator"], "selected_gene_ids": query,
                  "overlap_gene_ids": [gene for gene in query if gene in memberships[row["regulator"]]],
                  "p_value": row["p_value"], "q_value": max(row["p_value"], row["q_value"])} for row in rows]
    for estimate in estimates:
        overlap = len(estimate["overlap_gene_ids"])
        # Match the hypergeometric test's measured universe. Annotation members
        # absent from the assay are neither positive nor negative observations.
        member_count = len(memberships[estimate["regulator_id"]] & measured_background)
        numerator = overlap * (len(background) - member_count - len(query) + overlap)
        denominator = (len(query) - overlap) * (member_count - overlap)
        estimate["odds_ratio_status"] = "finite" if denominator else "infinite" if numerator else "undefined"
        estimate["odds_ratio"] = numerator / denominator if denominator else None
    diagnostics.update(query_selection=selection, query_reselected_by_analysis=False,
                       background_includes_zero_expression_genes=True, reference_genes_outside_background=len(outside),
                       annotation_version=reference.get("version"), tested_annotation_sets=len(estimates),
                       probability_scope="conditional_combinatorial_annotation_overlap",
                       biological_discovery_fdr_calibrated=False, motif_occurrence_implies_binding=False)
    payload = {"kind": "enrichment", "enrichment_results": estimates, "feature_universe": background,
               "annotation_provenance": key, "test_definition": "one_sided_hypergeometric_membership",
               "unknown_edges_are_negatives": False, "binding_inferred": False,
               "selection_conditional_inference": True,
               "probability_scope": "conditional_combinatorial_annotation_overlap"}
    return target_fragment(target, claim_type="restricted_estimate",
        evidence_scope="motif_occurrence" if motif else "curated_membership", payload=payload,
        limitation_codes=["conditional_on_fixed_expression_selected_query", "annotation_overlap_is_not_biological_fdr_calibration",
                          "motif_or_curated_membership_is_not_binding"])


def _assignment(data, settings):
    audit = _units(data)
    annotation = data.annotations.get("guide_assignments")
    if not isinstance(annotation, dict) or not isinstance(annotation.get("records"), list):
        raise _Unavailable("cell_linked_guide_assignment_evidence_unavailable")
    unit = data.unit_hierarchy["population_unit_label"]
    if annotation.get("randomization_unit") not in {unit, data.unit_hierarchy["population_unit"]}:
        raise _Unavailable("guide_assignment_unit_not_supported_independent_biological_unit")
    control = annotation.get("negative_control")
    if not isinstance(control, str) or not control:
        raise _Unavailable("declared_nontargeting_control_unavailable")
    records, targets = {}, {}
    for row in annotation["records"]:
        if not isinstance(row, dict) or set(row) != {"cell_id", "assigned_targets", "assignment"}:
            raise _Unavailable("invalid_guide_assignment_record")
        cell, label, assigned = row["cell_id"], row["assignment"], row["assigned_targets"]
        if cell not in data.sample_ids or cell in records or not isinstance(label, str) or not _IDENTIFIER.fullmatch(label):
            raise _Unavailable("invalid_or_duplicate_guide_assignment_join")
        if not isinstance(assigned, list) or len(set(assigned)) != len(assigned) or any(not isinstance(gene, str) for gene in assigned):
            raise _Unavailable("invalid_assigned_target_set")
        membership = tuple(sorted(assigned))
        if label in targets and targets[label] != membership:
            raise _Unavailable("assignment_label_has_inconsistent_target_membership")
        targets[label] = membership
        records[cell] = label
    if set(records) != set(data.sample_ids):
        raise _Unavailable("guide_assignments_do_not_cover_all_observed_cells")
    if control not in targets or targets[control]:
        raise _Unavailable("nontargeting_control_has_missing_or_nonempty_target_set")
    assignment = [records[cell] for cell in data.sample_ids]
    donor_assignments = {}
    for donor, assigned in zip(data.metadata[unit].astype(str), assignment):
        donor_assignments.setdefault(donor, set()).add(assigned)
    if any(len(groups) != 1 for groups in donor_assignments.values()):
        raise _Unavailable("observed_assignments_violate_declared_donor_randomization_unit")
    allocation = str(annotation.get("allocation", "")).lower()
    randomized = bool(re.search(r"\brandomi[sz]ed\b", allocation)) and not bool(re.search(r"\bnot\b|non.?random|unrandom", allocation))
    details = {"unit_audit": audit, "randomization_unit": annotation["randomization_unit"],
               "allocation_declaration": annotation.get("allocation"), "declared_randomized": randomized,
               "observed_assignment_unit_consistent": True,
               "negative_control": control, "assignment_targets": targets,
               "donors_per_assignment": {label: sum(label in values for values in donor_assignments.values()) for label in sorted(targets)},
               "assignment_provenance_scope": "supplied allocation declaration plus observed donor-to-guide consistency"}
    return assignment, targets, control, details


def _factorial_levels(targets, control):
    singles = {genes[0]: label for label, genes in targets.items() if len(genes) == 1}
    if len(singles) != sum(len(genes) == 1 for genes in targets.values()):
        raise _Unavailable("multiple_single_guides_require_prespecified_factorial_comparison")
    if len(singles) != 2:
        raise _Unavailable("factorial_requires_two_distinct_observed_single_assignments")
    a, b = sorted(singles)
    doubles = [label for label, genes in targets.items() if set(genes) == {a, b} and len(genes) == 2]
    if len(doubles) != 1:
        raise _Unavailable("factorial_requires_one_observed_double_assignment")
    return [control, singles[a], singles[b], doubles[0]]


def _epistasis_estimate(fitted, prediction):
    import numpy as np
    from scipy.stats import norm

    # All means are evaluated at the same RLE-reference exposure and nuisance
    # population. Ratios avoid mixing latent production-rate and observed units.
    beta, covariance = np.asarray(fitted.params), np.asarray(fitted.cov_params())
    c, a, b, ab = prediction
    control_mean = float(np.exp(c @ beta))
    if not math.isfinite(control_mean) or control_mean <= 0:
        raise ValueError("finite_positive_reference_control_mean_unavailable")
    ratios = np.exp(np.asarray([ab - c, a - c, b - c]) @ beta)
    if not np.isfinite(ratios).all():
        raise ValueError("nonfinite_control_normalized_factorial_response")
    estimate = float(ratios[0] - ratios[1] - ratios[2] + 1)
    gradient = ratios[0] * (ab - c) - ratios[1] * (a - c) - ratios[2] * (b - c)
    variance = float(gradient @ covariance @ gradient)
    if not math.isfinite(variance) or variance <= 0:
        raise ValueError("factorial_delta_variance_unavailable")
    standard_error = math.sqrt(variance)
    p_value = float(2 * norm.sf(abs(estimate) / standard_error))
    width = float(norm.ppf(0.975)) * standard_error
    return [estimate], [{"lower": estimate - width, "upper": estimate + width, "confidence_level": 0.95}], p_value


def validate_achieved_intervention_contract(efficacy, assigned_target_ids, *, randomization_unit="donor"):
    """Pure mapping/assumption validation; never inspects observation records.

    Full prescribed dose is an assay coordinate, not a statement that every
    target transcript was removed. This function validates declared scientific
    assumptions, without treating them as independently authenticated evidence.
    """
    if not isinstance(efficacy, dict):
        raise ValueError("observed_target_efficacy_unavailable")
    mapping = efficacy.get("target_to_column")
    if not isinstance(mapping, dict):
        raise ValueError("public_target_to_efficacy_mapping_unavailable")
    if not isinstance(assigned_target_ids, (list, tuple)) or any(not isinstance(gene, str) for gene in assigned_target_ids):
        raise ValueError("assigned_target_identifiers_must_be_explicit")
    if len(set(assigned_target_ids)) != len(assigned_target_ids) or set(mapping) != set(assigned_target_ids):
        raise ValueError("efficacy_mapping_must_match_public_assigned_target_universe")
    if any(not isinstance(gene, str) or not _IDENTIFIER.fullmatch(gene) for gene in mapping):
        raise ValueError("invalid_public_efficacy_target_identifier")
    if any(not isinstance(column, str) or column not in {"target_a_reduction", "target_b_reduction"} for column in mapping.values()) or len(set(mapping.values())) != len(mapping):
        raise ValueError("efficacy_mapping_requires_distinct_declared_assay_coordinates")
    if efficacy.get("units") != "achieved_intervention_dose_reduction":
        raise ValueError("efficacy_readout_is_not_declared_prescribed_dose")
    for key, expected in (("reference_value", 0.0), ("full_intervention_value", 1.0)):
        value = efficacy.get(key)
        if type(value) not in (int, float) or not math.isfinite(value) or value != expected:
            raise ValueError("efficacy_requires_declared_zero_reference_and_unit_full_dose")
    for key in ("assay", "scale_definition", "assay_scope", "evidence_scope", "assay_qualification"):
        if not isinstance(efficacy.get(key), str) or not efficacy[key].strip():
            raise ValueError("efficacy_assay_interpretation_or_qualification_unavailable")
    assumptions = efficacy.get("identification_assumptions")
    if not isinstance(assumptions, dict):
        raise ValueError("achieved_intervention_identification_assumptions_unavailable")
    required_true = ("randomized_assignment", "consistency_assumed", "target_specificity_assumed",
                     "exclusion_restriction_assumed", "no_between_donor_interference_assumed",
                     "achieved_dose_observed_for_mapped_targets")
    if any(assumptions.get(key) is not True for key in required_true):
        raise ValueError("achieved_intervention_identification_assumptions_not_satisfied")
    if assumptions.get("measurement_is_expression_estimated") is not False:
        raise ValueError("efficacy_must_be_observed_independently_of_fitted_expression")
    if assumptions.get("randomization_unit") != randomization_unit:
        raise ValueError("efficacy_and_observed_assignment_units_disagree")
    return {"target_to_column": dict(mapping), "reference_value": 0.0, "full_intervention_value": 1.0,
            "units": efficacy["units"], "assay": efficacy["assay"], "assay_scope": efficacy["assay_scope"],
            "evidence_scope": efficacy["evidence_scope"], "assay_qualification": efficacy["assay_qualification"],
            "scale_definition": efficacy["scale_definition"], "identification_assumptions": dict(assumptions)}


def _achieved_support(data, assignments, targets, control, comparisons, diagnostics):
    efficacy = data.annotations.get("efficacy_measurement")
    if not isinstance(efficacy, dict) or not isinstance(efficacy.get("records"), list):
        raise _Unavailable("observed_target_efficacy_unavailable")
    assigned_target_ids = sorted(set().union(*(set(genes) for genes in targets.values())))
    try:
        contract = validate_achieved_intervention_contract(efficacy, assigned_target_ids,
            randomization_unit=data.unit_hierarchy["population_unit"])
    except ValueError as error:
        raise _Unavailable(_reason(error)) from None
    records = {}
    for row in efficacy["records"]:
        if not isinstance(row, dict) or set(row) != {"cell_id", "target_a_reduction", "target_b_reduction"}:
            raise _Unavailable("invalid_efficacy_measurement_record")
        cell = row["cell_id"]
        if cell in records or cell not in data.sample_ids:
            raise _Unavailable("invalid_or_duplicate_efficacy_cell_join")
        values = {key: row[key] for key in ("target_a_reduction", "target_b_reduction")}
        if any(type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1 for value in values.values()):
            raise _Unavailable("efficacy_must_be_finite_prescribed_dose_in_declared_domain")
        records[cell] = values
    if set(records) != set(data.sample_ids):
        raise _Unavailable("efficacy_measurements_do_not_cover_assigned_population")
    mapping, unit = contract["target_to_column"], data.unit_hierarchy["population_unit_label"]
    donor_records = {}
    for index, (cell, assigned) in enumerate(zip(data.sample_ids, assignments)):
        donor = str(data.metadata.iloc[index][unit])
        donor_records.setdefault((donor, assigned), []).append(records[cell])
    summaries, violations, positive_donors = [], [], set()
    for (donor, assigned), observations in sorted(donor_records.items()):
        ranges = {gene: {"minimum": min(row[column] for row in observations),
                         "maximum": max(row[column] for row in observations)} for gene, column in mapping.items()}
        summaries.append({"donor_id": donor, "assignment": assigned, "mapped_dose_ranges": ranges})
        if assigned not in [control, *comparisons]:
            continue
        for gene, column in mapping.items():
            expected = contract["full_intervention_value"] if gene in targets[assigned] else contract["reference_value"]
            # This is floating-point equality tolerance on a prescribed assay
            # coordinate, never a fitted success threshold or a cell filter.
            if any(not math.isclose(row[column], expected, rel_tol=0.0, abs_tol=1e-9) for row in observations):
                violations.append({"donor_id": donor, "assignment": assigned, "target_id": gene,
                                   "reason": "observed_dose_does_not_equal_declared_assignment_dose"})
            elif expected == contract["full_intervention_value"]:
                positive_donors.add(donor)
    evidence = dict(contract, record_count=len(records), donor_dose_summaries=summaries,
                    compared_assignments=[control, *comparisons], full_achievement_violations=violations,
                    positive_intervention_donor_count=len(positive_donors), success_conditioning_performed=False,
                    efficacy_selected_cell_ids=[], efficacy_threshold_fitted=False,
                    postassignment_selection_is_randomized=False,
                    assignment_and_achieved_estimands_coincide=bool(positive_donors and not violations))
    diagnostics["efficacy_evidence"] = evidence
    if not positive_donors or not mapping or not comparisons:
        raise _Unavailable("no_positive_assigned_intervention_support")
    if violations:
        raise _Unavailable("full_achieved_intervention_not_observed_for_complete_assigned_population")
    return evidence


def _perturbation(data, target, settings, diagnostics):
    from .biology_tools_bulk import _fit_family, _linear_estimate
    from .biology_answers import public_hypothesis_id

    if data.measurement == "continuous":
        raise _Unavailable("declared_perturbation_count_estimand_requires_observed_counts")
    assignments, targets, control, details = _assignment(data, settings)
    diagnostics.update(assignment_audit=details)
    if not details["declared_randomized"]:
        raise _Unavailable("randomized_assignment_declaration_unavailable")
    if target["target_id"] == "epistasis":
        levels = _factorial_levels(targets, control)
    else:
        comparisons = [settings["comparison_condition"]] if "comparison_condition" in settings else sorted(set(assignments) - {control})
        if target["target_id"] == "successful_intervention" and not comparisons:
            _achieved_support(data, assignments, targets, control, comparisons, diagnostics)
        if not comparisons or not set(comparisons) <= set(assignments) or control in comparisons:
            raise _Unavailable("supported_guide_vs_control_contrast_unavailable")
        levels = [control, *comparisons]
        if target["target_id"] == "successful_intervention":
            _achieved_support(data, assignments, targets, control, comparisons, diagnostics)
    selected = [index for index, assigned in enumerate(assignments) if assigned in levels]
    observed = _subset(data, selected)
    selected_assignments = [assignments[index] for index in selected]
    pseudobulk, membership = _pseudobulk(observed, ["captured_population"] * len(selected), settings, grouping=selected_assignments)
    design, reference, design_details = _design(pseudobulk, settings, levels)
    diagnostics.update(pseudobulk=membership, design=design_details)
    fits, failures = _fit_family(pseudobulk, design, diagnostics)
    rows = []
    for gene in data.gene_ids:
        comparisons = [("factorial_nonadditivity", None)] if target["target_id"] == "epistasis" else [(level, index) for index, level in enumerate(levels[1:], 1)]
        for label, index in comparisons:
            component = None if target["target_id"] == "epistasis" else label
            row = {"hypothesis_id": public_hypothesis_id(gene, component), "gene_id": gene, "component_id": component}
            if gene not in fits:
                row.update(estimate_status="failed", missing_reason=failures.get(gene, "fit_unavailable"))
            else:
                try:
                    if index is None:
                        values, intervals, p_value = _epistasis_estimate(fits[gene], reference)
                    else:
                        values, intervals, p_value = _linear_estimate(fits[gene], reference[index] - reference[0], True)
                    row.update(estimate_status="estimated", coordinate_ids=[label], values=values,
                               intervals=intervals, p_value=p_value, q_value=p_value)
                except (ValueError, ArithmeticError, RuntimeError) as error:
                    row.update(estimate_status="failed", missing_reason=_reason(error))
            rows.append(row)
    if not any(row["estimate_status"] == "estimated" for row in rows):
        raise _MethodUnavailable("all_attempted_donor_perturbation_fits_failed")
    factorial = target["target_id"] == "epistasis"
    achieved = target["target_id"] == "successful_intervention"
    definition = ("(mean_AB - mean_A - mean_B + mean_control) / mean_control on common RLE-reference expected count scale; NB delta-method inference."
                  if factorial else (
                  "Donor-level log2 expected count-rate response to full prescribed intervention versus reference dose. Complete observed achievement makes this the ITT contrast under the supplied consistency, specificity, exclusion and no-interference assumptions. RLE offsets and empirical-Bayes NB dispersion."
                  if achieved else "Intention-to-treat donor-level log2 expected count-rate response to assigned guide versus declared non-targeting control; RLE offsets and empirical-Bayes NB dispersion."))
    payload = _effect_payload(data, target, rows, [design_details], control, definition=definition)
    payload["aggregation_definition"] = membership["aggregation_definition"]
    if achieved:
        from .contracts import content_sha256
        payload["efficacy_evidence_sha256"] = content_sha256(data.annotations["efficacy_measurement"])
    limitations = ["conditional_on_supplied_randomization_record", "empirical_bayes_conditional_inference",
                   "captured_population_response_includes_composition", "total_response_is_not_direct_regulatory_binding"]
    if achieved:
        limitations += ["conditional_on_declared_achieved_dose_assumptions", "prescribed_dose_is_not_transcript_knockdown_fraction",
                        "assay_qualification_is_declared_not_independently_verified"]
    return target_fragment(target, claim_type="restricted_estimate",
        evidence_scope="factorial_nonadditivity" if factorial else ("achieved_intervention" if achieved else "randomized_assignment"),
        payload=payload, limitation_codes=limitations)


def _regulatory_audit(data, target, settings, diagnostics):
    _units(data)
    motif = data.annotations.get("motif_hit_annotation")
    regulon = data.annotations.get("regulon_annotation")
    guides = data.annotations.get("guide_assignments")
    diagnostics.update(available_expression_observations=len(data.sample_ids),
                       curated_membership_record_count=len(regulon.get("memberships", [])) if isinstance(regulon, dict) else None,
                       motif_occurrence_record_count=len(motif.get("hits", [])) if isinstance(motif, dict) else None,
                       motif_annotation_declares_binding_measured=motif.get("binding_measured") if isinstance(motif, dict) else None,
                       guide_assignment_record_count=len(guides.get("records", [])) if isinstance(guides, dict) else None,
                       locus_resolved_binding_assay_records_supplied=False,
                       intervention_mediation_or_target_specific_binding_records_supplied=False)
    reason = "locus_resolved_independent_binding_measurements_unavailable" if target["target_id"] == "binding" else "direct_edge_specific_binding_and_mediation_evidence_unavailable"
    return _refuse(target, reason, restraint=True, checks=[
        _check("evidence_scope_audited", "satisfied", "Expression, curated memberships, supplied motif calls and guide assignments are counted separately.", ("public_annotations", "counts")),
        _check("direct_evidence", "unknown", "The supplied annotation schemas do not contain locus-resolved binding or directed mediation measurements.", ("public_annotations",))])


def _analyze_singlecell_worker(tool_name, public_task, counts_path, metadata_path, annotations_path,
                              options=None, allow_data_execution=False):
    """Worker entry also denies execution before any read or numerical import."""
    require_data_execution(allow_data_execution)
    if tool_name not in _TOOL_TARGETS:
        raise ValueError("Unregistered single-cell analysis tool")
    settings = validate_singlecell_options(options)
    data = load_observed_data(public_task, counts_path=counts_path, metadata_path=metadata_path,
                              annotations_path=annotations_path, allow_data_execution=True)
    targets = [target for target in data.targets if target["target_id"] in _TOOL_TARGETS[tool_name]]
    if not targets:
        raise ValueError("Tool has no matching public question")
    result = {"tool_name": tool_name, "target_results": [], "diagnostics": {},
              "provenance": dict(data.provenance, implementation="observed_public_input_singlecell_methods",
                                 qualification="unvalidated_numerical_implementation",
                                 counts_path=str(counts_path), metadata_path=str(metadata_path),
                                 annotations_path=str(annotations_path), tool_evidence_ids=[])}
    if tool_name in _AUDIT_TOOLS:
        try:
            if tool_name == "pseudobulk_membership_audit":
                annotation = _annotate(data, settings)
                pseudobulk, details = _pseudobulk(data, annotation["labels"], settings,
                                                population_ids=sorted(_marker_sets(data)[0]))
                details["observed_pseudobulk_columns"] = len(pseudobulk.sample_ids)
                details["label_uncertainty_count"] = annotation["uncertain_cell_count"]
            else:
                _, _, _, details = _assignment(data, settings)
            result["diagnostics"].update(status="audited", audited_target_ids=[target["target_id"] for target in targets], **details)
        except _Unavailable as error:
            result["diagnostics"].update(status="support_unavailable", reason=str(error))
        except (ValueError, TypeError, ArithmeticError, RuntimeError) as error:
            result["diagnostics"].update(status="analysis_failed", reason=_reason(error))
        return json_safe_result(result)
    handlers = {"cell_type_state_analysis": _state_effect, "cell_composition_analysis": _composition,
                "infer_lineage": _graph_target, "lineage_history_analysis": _history,
                "competing_program_analysis": _programs, "single_cell_quality_audit": _quality_target,
                "single_cell_contamination_analysis": _contamination, "infer_regulatory_network": _network,
                "regulon_enrichment": _enrichment, "motif_enrichment": _enrichment,
                "perturbation_response_analysis": _perturbation, "regulatory_evidence_audit": _regulatory_audit}
    for target in targets:
        diagnostics = result["diagnostics"].setdefault(target["target_id"], {})
        try:
            answer = handlers[tool_name](data, target, settings, diagnostics)
        except _Unavailable as error:
            diagnostics.update(status="support_unavailable", reason=str(error))
            answer = _refuse(target, str(error), checks=_refusal_checks(str(error), diagnostics),
                             restraint=target["target_id"] in {"transition_dynamics", "binding", "direct_edge"})
        except (ValueError, TypeError, ArithmeticError, RuntimeError) as error:
            diagnostics.update(status="analysis_failed", reason=_reason(error))
            answer = target_fragment(target, claim_type="failed", evidence_scope="not_identified", limitation_codes=[_reason(error)])
        result["target_results"].append(answer)
    from .biology_answers import TargetAnswer

    for answer in result["target_results"]:
        TargetAnswer.model_validate_json(json.dumps(answer, allow_nan=False))
    return json_safe_result(result)
