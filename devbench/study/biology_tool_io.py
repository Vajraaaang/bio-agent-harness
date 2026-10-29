"""Observed biology inputs and answer fragments, with no import-time data access.

Only public task definitions, public annotations and observed expression enter
this module. It does not import generators, recipes, scoring or private truth.
The caller must explicitly authorize data execution before any path inspection.
"""
from __future__ import annotations

from array import array
import csv
from dataclasses import dataclass, field
import io
import json
import math
from pathlib import Path
import re
from typing import Any

from .biology_input_limits import (
    PUBLIC_DENIED_KEYS, PUBLIC_MAX_AGGREGATE_BYTES, PUBLIC_MAX_ANNOTATION_BYTES, PUBLIC_MAX_CHUNK_BYTES,
    PUBLIC_MAX_BULK_FEATURES, PUBLIC_MAX_FEATURES, PUBLIC_MAX_LABEL_CHARS, PUBLIC_MAX_MATRIX_BYTES,
    PUBLIC_MAX_METADATA_BYTES, PUBLIC_MAX_METADATA_COLUMNS, PUBLIC_MAX_METADATA_FIELD_CHARS,
    PUBLIC_MAX_METADATA_HEADER_CHARS, PUBLIC_MAX_METADATA_ROWS, PUBLIC_MAX_TASK_BYTES,
    PUBLIC_MAX_ZARR_FILES, PUBLIC_MAX_ZARR_HEADER_BYTES,
    PUBLIC_MAX_ZARR_METADATA_BYTES, PUBLIC_MEASUREMENTS, public_json_size, validate_expression_source_dtype,
    validate_public_structure,
)

MAX_MATRIX_BYTES = PUBLIC_MAX_MATRIX_BYTES
MAX_METADATA_BYTES = PUBLIC_MAX_METADATA_BYTES
MAX_ANNOTATION_BYTES = PUBLIC_MAX_ANNOTATION_BYTES
MAX_CHUNK_BYTES = PUBLIC_MAX_CHUNK_BYTES
MAX_FEATURES = PUBLIC_MAX_FEATURES
MAX_BULK_FEATURES = PUBLIC_MAX_BULK_FEATURES
DESIGN_CONDITION_NUMBER_LIMIT = 1000.0
MEASUREMENTS = PUBLIC_MEASUREMENTS
_PRIVATE_KEYS = PUBLIC_DENIED_KEYS
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:\-/]{0,199}$")


@dataclass(frozen=True)
class ObservedData:
    expression: Any  # ndarray, imported only after explicit execution permission
    gene_ids: tuple[str, ...]
    sample_ids: tuple[str, ...]
    metadata: Any  # DataFrame indexed by sample ID
    annotations: dict[str, Any]
    measurement: str
    unit_hierarchy: dict[str, Any]
    targets: tuple[dict[str, Any], ...]
    provenance: dict[str, Any]
    context: dict[str, Any] = field(default_factory=dict)


def require_data_execution(allow_data_execution: bool = False) -> None:
    if allow_data_execution is not True:
        raise PermissionError("Observed data execution requires allow_data_execution to be literal True")


def design_condition_policy(condition_number_limit: float = DESIGN_CONDITION_NUMBER_LIMIT) -> dict[str, Any]:
    """Pure public numerical-admission policy; callers may tighten but not relax it.

The actual fitting matrix is not rescaled to manufacture a passing diagnostic.
Its rank/conditioning and the centered, unit-RMS diagnostic matrix must both
pass. Public B-spline bases and binary coding are applied before this check;
reported biological contrasts retain their declared units and full covariance.
"""
    if (isinstance(condition_number_limit, bool) or not isinstance(condition_number_limit, (int, float))
            or not math.isfinite(condition_number_limit)
            or not 1 <= condition_number_limit <= DESIGN_CONDITION_NUMBER_LIMIT):
        raise ValueError("condition_number_limit must be finite and between 1 and the fixed ceiling 1000")
    return {"condition_number_limit": float(condition_number_limit),
            "maximum_permitted_limit": DESIGN_CONDITION_NUMBER_LIMIT,
            "gate": "both_original_and_standardized_full_rank_and_within_limit",
            "diagnostic_scaling": "preserve_unit_intercept_center_other_columns_then_divide_by_observed_population_RMS",
            "zero_variation_policy": "retain_and_reject_rank_deficiency_never_drop_columns",
            "fitting_parameterization": "unchanged_declared_design_with_its_full_covariance",
            "basis_policy": "public_bounded_spline_basis_and_explicit_binary_or_declared_covariate_columns"}


def design_matrix_diagnostics(matrix, *, condition_number_limit: float = DESIGN_CONDITION_NUMBER_LIMIT) -> dict[str, Any]:
    """Numerical validation for already-authorized workers, without model fitting.

Original and standardized singular values test distinct failure modes. Retaining
both gates prevents a change of units from concealing an unstable fitted matrix.
Centering/scaling is diagnostic only, so coefficient/contrast covariance is never
silently transformed or discarded. No expression-dependent column is dropped.
"""
    policy = design_condition_policy(condition_number_limit)
    import numpy as np

    values = np.asarray(matrix, dtype=float)
    if values.ndim != 2 or not values.size or not np.isfinite(values).all():
        raise ValueError("design_matrix_must_be_nonempty_finite_and_two_dimensional")
    if not np.all(values[:, 0] == 1.0):
        raise ValueError("design_diagnostic_requires_explicit_unit_intercept_in_first_column")
    raw_rank = int(np.linalg.matrix_rank(values))
    raw_number = float(np.linalg.cond(values))
    centers = np.zeros(values.shape[1])
    scales = np.ones(values.shape[1])
    standardized = values.copy()
    if values.shape[1] > 1:
        centers[1:] = values[:, 1:].mean(axis=0)
        centered = values[:, 1:]-centers[None, 1:]
        rms = np.sqrt(np.mean(centered**2, axis=0))
        if not np.isfinite(centered).all() or not np.isfinite(rms).all():
            raise ValueError("design_diagnostic_scaling_nonfinite")
        scales[1:] = np.where(rms > 0, rms, 1.0)
        standardized[:, 1:] = centered/scales[None, 1:]
    standardized_rank = int(np.linalg.matrix_rank(standardized))
    standardized_number = float(np.linalg.cond(standardized))
    valid = (raw_rank == values.shape[1] and standardized_rank == values.shape[1]
             and math.isfinite(raw_number) and math.isfinite(standardized_number)
             and raw_number <= condition_number_limit and standardized_number <= condition_number_limit)
    return {"rank": raw_rank, "standardized_rank": standardized_rank,
            "column_count": values.shape[1], "residual_df": values.shape[0]-raw_rank,
            "condition_number": raw_number if math.isfinite(raw_number) else None,
            "raw_condition_number": raw_number if math.isfinite(raw_number) else None,
            "standardized_condition_number": standardized_number if math.isfinite(standardized_number) else None,
            "condition_number_validated": bool(valid), "condition_number_limit": float(condition_number_limit),
            "diagnostic_column_centers": centers.tolist(), "diagnostic_column_scales": scales.tolist(),
            "design_condition_policy": policy}


def require_design_condition(matrix, *, condition_number_limit: float = DESIGN_CONDITION_NUMBER_LIMIT) -> dict[str, Any]:
    """Enforce the same complete-design gate immediately before a numerical fit."""
    diagnostics = design_matrix_diagnostics(matrix, condition_number_limit=condition_number_limit)
    if diagnostics["rank"] != diagnostics["column_count"] or diagnostics["standardized_rank"] != diagnostics["column_count"]:
        raise ValueError("rank_deficient_design")
    if not diagnostics["condition_number_validated"]:
        raise ValueError("design_condition_policy_rejected: original=" + str(diagnostics["raw_condition_number"])
                         + ", standardized=" + str(diagnostics["standardized_condition_number"])
                         + ", limit=" + str(condition_number_limit))
    return diagnostics


def _public_tree(value: Any, *, depth: int = 0, budget: list[int] | None = None) -> None:
    """Bound JSON configuration and reject coordinator-only fields recursively."""
    validate_public_structure(value, depth=depth, budget=budget)


def validate_public_task(public_task: dict[str, Any]) -> dict[str, Any]:
    """Pure public-config projection; opaque task IDs never enter method selection."""
    if not isinstance(public_task, dict):
        raise ValueError("public_task must be a public JSON object")
    public_json_size(public_task, max_bytes=PUBLIC_MAX_TASK_BYTES)
    context = public_task.get("context", public_task.get("analysis_context", {}))
    if not isinstance(context, dict):
        raise ValueError("Public context must be an object")
    context = dict(context)
    role_names = {"condition", "time", "dose", "covariate", "population_unit", "batches", "blocking"}
    role_declarations = [context.get("analysis_columns"), public_task.get("analysis_columns")]
    roles = None
    for declaration_roles in role_declarations:
        if declaration_roles is None:
            continue
        if not isinstance(declaration_roles, dict):
            raise ValueError("Invalid declared analysis_columns")
        declared = {key: value for key, value in declaration_roles.items() if key in role_names}
        if roles is not None and any(roles[key] != declared[key] for key in roles.keys() & declared.keys()):
            raise ValueError("Contradictory declared analysis_columns roles")
        roles = {**(roles or {}), **declared}
    if roles is not None:
        if "condition" in roles and roles["condition"] is None:
            raise ValueError("A declared condition role requires an explicit column identifier")
        for role, column in roles.items():
            columns = column if role in {"batches", "blocking"} else ([] if column is None else [column])
            if role == "blocking" and (not isinstance(columns, list) or len(columns) > 1):
                raise ValueError("A fixed block requires zero or one declared biological-unit column")
            if (not isinstance(columns, list) or len(columns) > 8
                    or any(not isinstance(name, str) or not _IDENTIFIER.fullmatch(name) for name in columns)
                    or len(set(columns)) != len(columns)):
                raise ValueError("Analysis column roles require bounded identifiers")
    layer = public_task.get("measurement_layer", public_task.get("measurement", public_task.get("measurement_type")))
    declaration = dict(layer) if isinstance(layer, dict) else (
        dict(context["measurement"]) if isinstance(context.get("measurement"), dict) else {"measurement": layer})
    measurement = declaration.get("measurement", declaration.get("measurement_type"))
    if isinstance(layer, str) and measurement != layer:
        raise ValueError("Public context measurement disagrees with the selected layer")
    if measurement not in MEASUREMENTS:
        raise ValueError("Public task must declare the actual observed measurement layer")
    route = "continuous_likelihood" if measurement == "continuous" else "count_likelihood"
    if declaration.get("analysis_route", route) != route:
        raise ValueError("Measurement provenance and analysis route disagree")
    history = public_task.get("transformation_history", context.get("transformation_history", []))
    if not isinstance(history, list) or any(not isinstance(item, (dict, str)) for item in history):
        raise ValueError("Transformation history must be a list of declarations")
    if measurement != "continuous":
        count_semantics = {"raw_counts", "direct_counts", "umi_counts", "non_umi_counts"}
        if declaration.get("assay_semantics", "raw_counts") not in count_semantics:
            raise ValueError("Count assay provenance requires an original supported count assay")
        selected = declaration.get("layer", declaration.get("matrix_source", declaration.get("assay")))
        def layer_name(value):
            return value.removeprefix("layers/") if isinstance(value, str) else value
        for entry in history:
            item = entry if isinstance(entry, dict) else {"operation": entry}
            target = item.get("output_layer", item.get("target_layer", item.get("layer")))
            # Only an explicitly distinct output can exempt a transformation.
            # An unspecified target cannot establish that selected counts survived.
            if target is not None and selected is not None and layer_name(target) != layer_name(selected):
                continue
            operation = str(item.get("operation", "")).lower()
            semantics = item.get("output_semantics")
            if (semantics is not None and semantics not in count_semantics) or any(
                    token in operation for token in ("normaliz", "correct", "scal", "imput")) or operation.startswith("log"):
                raise ValueError("Transformation history contradicts selected count assay provenance")
    hierarchy = public_task.get("unit_hierarchy", public_task.get("biological_unit_hierarchy",
                               context.get("biological_units")))
    if not isinstance(hierarchy, dict) and public_task.get("population_unit_label"):
        hierarchy = {"population_unit": context.get("biological_unit", "donor"),
                     "population_unit_label": public_task["population_unit_label"]}
    elif isinstance(hierarchy, dict):
        hierarchy = dict(hierarchy)
        hierarchy.setdefault("population_unit_label", public_task.get("population_unit_label"))
        hierarchy.setdefault("population_unit", context.get("biological_unit", "donor"))
    if not isinstance(hierarchy, dict) or hierarchy.get("population_unit") not in {
            "donor", "organism", "experimental_culture", "donor_derived_cell_line"}:
        raise ValueError("Public task requires its biological unit hierarchy")
    hierarchy = dict(hierarchy)
    hierarchy.setdefault("specimen_label", "specimen_id")
    hierarchy.setdefault("library_label", "library_id")
    labels = [hierarchy.get(key) for key in ("population_unit_label", "specimen_label", "library_label")]
    if any(not isinstance(label, str) or not _IDENTIFIER.fullmatch(label) for label in labels):
        raise ValueError("Biological unit labels must be explicit bounded identifiers")
    if len(set(labels)) != len(labels):
        raise ValueError("Biological unit hierarchy labels must be distinct")
    if roles is not None and roles.get("population_unit", labels[0]) != labels[0]:
        raise ValueError("Analysis population-unit role contradicts the declared hierarchy")
    if roles is not None and roles.get("blocking", []) not in ([], [labels[0]]):
        raise ValueError("The fixed block must be the declared biological-unit column")
    targets = public_task.get("questions", public_task.get("targets"))
    if not isinstance(targets, (list, tuple)) or not 1 <= len(targets) <= 50:
        raise ValueError("Public task requires 1..50 target definitions")
    projected = []
    for target in targets:
        if not isinstance(target, dict):
            raise ValueError("Public targets must be objects")
        record = {}
        for name in ("target_id", "estimand", "contrast", "reference_population", "prediction_reference"):
            value = target.get(name)
            if not isinstance(value, str) or not value.strip() or len(value) > 3000:
                raise ValueError("Public target requires canonical " + name)
            record[name] = value
        units = target.get("measurement_units", target.get("units"))
        if not isinstance(units, str) or not units.strip() or len(units) > 3000:
            raise ValueError("Public target requires canonical measurement_units")
        record["measurement_units"] = units
        for name in ("minimum_design_support", "required_observation_layers", "tool_requirements",
                     "shared_process_projection", "projection_contract", "identification_assumptions"):
            if name in target:
                record[name] = target[name]
        if "projection_contract" in record and "shared_process_projection" not in record:
            record["shared_process_projection"] = record["projection_contract"]
        projected.append(record)
    if len({target["target_id"] for target in projected}) != len(projected):
        raise ValueError("Public target IDs must be distinct")
    result = {"measurement": measurement, "measurement_layer": declaration,
              "unit_hierarchy": hierarchy, "targets": projected, "analysis_context": context,
              "observation_id_label": public_task.get("observation_id_label", "sample_id")}
    if roles is not None:
        result["analysis_columns"] = roles
    for name in ("gene_ids", "gene_universe", "transformation_history"):
        if name in public_task:
            result[name] = public_task[name]
    result.setdefault("transformation_history", context.get("transformation_history", []))
    if "gene_ids" not in result and "feature_universe" in context:
        result["gene_ids"] = context["feature_universe"]
    return result


def target_fragment(target: dict[str, Any], *, claim_type: str, evidence_scope: str,
                    payload: dict[str, Any] | None = None, limitation_codes=(), support_checks=()) -> dict[str, Any]:
    """Copy canonical estimand fields without inventing a host receipt ID."""
    return {"target_id": target["target_id"], "estimand": target["estimand"],
            "units": target.get("measurement_units", target.get("units")), "contrast": target["contrast"],
            "reference_population": target["reference_population"], "prediction_reference": target["prediction_reference"],
            "claim_type": claim_type, "evidence_scope": evidence_scope,
            "estimate_status": "failed" if claim_type == "failed" else (
                "unavailable" if claim_type == "refusal" else "estimated"),
            "limitation_codes": list(dict.fromkeys(limitation_codes)), "asserted_claims": [],
            "support_checks": list(support_checks), "tool_evidence_ids": [], "payload": payload}


def json_safe_result(result: dict[str, Any]) -> dict[str, Any]:
    """Validate finite JSON and each fragment; never rewrite failed numbers as zero."""
    from .biology_answers import TargetAnswer

    for fragment in result.get("target_results", []):
        TargetAnswer.model_validate_json(json.dumps(fragment, allow_nan=False))
    return json.loads(json.dumps(result, allow_nan=False))


def _absolute_read_path(value: str | Path) -> Path:
    path = Path(value)
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("Observed inputs require absolute canonical paths")
    if any(part.is_symlink() for part in (path, *path.parents)):
        raise ValueError("Observed inputs cannot contain symlink paths")
    return path


def bounded_bytes(path: Path, maximum: int) -> bytes:
    if not path.is_file() or path.stat().st_size > maximum:
        raise ValueError("Observed file is missing or exceeds its read bound")
    with path.open("rb") as handle:
        content = handle.read(maximum + 1)
    if len(content) > maximum:
        raise ValueError("Observed file exceeds its read bound")
    return content


def _unique_json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Public annotation JSON keys must be unique")
        result[key] = value
    return result


def _reject_nonfinite_json(value):
    raise ValueError("Public annotation JSON cannot contain nonfinite numbers")


def _unique_labels(values, description: str) -> tuple[str, ...]:
    labels = tuple(str(item) for item in values)
    if not labels or len(set(labels)) != len(labels) or any(
            not item.strip() or len(item) > PUBLIC_MAX_LABEL_CHARS for item in labels):
        raise ValueError(description + " must be distinct nonempty bounded labels")
    return labels


def _read_csv_expression(path: Path, measurement: str):
    import numpy as np

    if not path.is_file() or path.stat().st_size > MAX_MATRIX_BYTES:
        raise ValueError("Expression CSV exceeds its 100 MB read bound")
    values, genes = array("d"), []
    maximum_features = MAX_BULK_FEATURES if measurement in {"raw_counts", "continuous"} else MAX_FEATURES
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle, strict=True)
        header = next(reader, None)
        if not header or len(header) < 2 or header[0] not in {"gene_id", "gene", "gene_name", ""}:
            raise ValueError("Expression CSV requires a gene identifier column followed by sample columns")
        samples = _unique_labels(header[1:], "Expression sample IDs")
        if len(samples) * 8 > MAX_CHUNK_BYTES:
            raise ValueError("Expression row exceeds the bounded decoded chunk size")
        for row in reader:
            if len(row) != len(header) or len(genes) >= maximum_features:
                raise ValueError("Expression CSV has inconsistent dimensions or too many genes")
            if (len(genes) + 1) * len(samples) * 8 > MAX_MATRIX_BYTES:
                raise ValueError("Decoded expression exceeds the 100 MB matrix bound")
            genes.append(row[0])
            try:
                parsed = [float(item) if measurement == "continuous" else int(item) for item in row[1:]]
            except (ValueError, OverflowError):
                raise ValueError("Expression values do not match declared measurement provenance") from None
            if any(not math.isfinite(value) or (measurement != "continuous" and not 0 <= value <= 2**53 - 1)
                   for value in parsed):
                raise ValueError("Expression must be finite; counts must be nonnegative exact integers")
            values.extend(parsed)
    genes = _unique_labels(genes, "Expression gene IDs")
    expression = np.frombuffer(values, dtype=np.float64).reshape((len(genes), len(samples)))
    return expression, genes, samples, {"format": "csv", "source_integer_dtype": measurement != "continuous"}


def _zarr_encoded_size(path: Path, maximum: int) -> int:
    """Bound encoded prepared-store bytes before any frame or array is read."""
    if type(maximum) is not int or not 1 <= maximum <= PUBLIC_MAX_AGGREGATE_BYTES:
        raise ValueError("Public aggregate byte ceiling cannot be enlarged")
    total = 0
    for count, member in enumerate(path.rglob("*"), 1):
        if count > PUBLIC_MAX_ZARR_FILES+3 or member.is_symlink():
            raise ValueError("Prepared Zarr exceeds member bounds or contains symlinks")
        if member.is_file():
            total += member.stat().st_size
            if total > maximum:
                raise ValueError("Public inputs exceed their aggregate byte bound")
    return total


def _read_zarr_expression(path: Path, measurement: str, *, max_storage_bytes=PUBLIC_MAX_AGGREGATE_BYTES):
    from .zarr_bounds import validate_prepared_zarr

    encoded_bytes = _zarr_encoded_size(path, max_storage_bytes)
    maximum_features = MAX_BULK_FEATURES if measurement in {"raw_counts", "continuous"} else MAX_FEATURES
    provenance = validate_prepared_zarr(path,
        measurement_type="continuous" if measurement == "continuous" else "raw_counts",
        max_chunk_bytes=MAX_CHUNK_BYTES, max_metadata_bytes=PUBLIC_MAX_ZARR_METADATA_BYTES,
        max_matrix_bytes=MAX_MATRIX_BYTES, max_genes=maximum_features)
    expression_header = json.loads(bounded_bytes(path / "expression" / ".zarray", PUBLIC_MAX_ZARR_HEADER_BYTES),
                                   parse_constant=_reject_nonfinite_json, object_pairs_hook=_unique_json_object)
    source_kind = validate_expression_source_dtype(expression_header.get("dtype"), measurement)
    shape = provenance["shape"]
    if shape[0] > maximum_features:
        raise ValueError("Expression Zarr exceeds the complete feature-family transport bound")
    if math.prod(shape) * 8 > MAX_MATRIX_BYTES:
        raise ValueError("Float64 expression view exceeds the 100 MB matrix bound")
    import numpy as np
    import zarr

    group = zarr.open_group(str(path), mode="r")
    genes = _unique_labels(group["gene_names"][:].tolist(), "Expression gene IDs")
    samples = _unique_labels(group["sample_names"][:].tolist(), "Expression sample IDs")
    expression = np.empty(shape, dtype=np.float64)
    block_size = max(1, MAX_CHUNK_BYTES // (shape[1] * 8))
    for start in range(0, shape[0], block_size):
        block = group["expression"][start:start + block_size, :]
        if not np.isfinite(block).all() or (measurement != "continuous" and (
                (block < 0).any() or (block > 2**53 - 1).any() or (block != np.floor(block)).any())):
            raise ValueError("Expression is nonfinite or incompatible with raw count provenance")
        expression[start:start + block_size] = block
    provenance["source_integer_dtype"] = source_kind in {"i", "u"}
    provenance["encoded_store_bytes"] = encoded_bytes
    return expression, genes, samples, provenance


def load_observed_data(public_task: dict[str, Any], *, counts_path: str | Path,
                       metadata_path: str | Path, annotations_path: str | Path,
                       allow_data_execution: bool = False) -> ObservedData:
    """Read exact aligned observed inputs only inside an explicitly opted-in worker.

Matrices have genes on rows. No sorting, gene filtering, normalization or layer
conversion occurs here. CSV and complete fixed-array Zarr v2 are supported.
"""
    require_data_execution(allow_data_execution)
    config = validate_public_task(public_task)
    paths = tuple(_absolute_read_path(path) for path in (counts_path, metadata_path, annotations_path))
    expression_path, metadata_file, annotation_file = paths
    annotation_content = bounded_bytes(annotation_file, MAX_ANNOTATION_BYTES)
    raw_annotations = json.loads(annotation_content,
                                 parse_constant=_reject_nonfinite_json, object_pairs_hook=_unique_json_object)
    if not isinstance(raw_annotations, dict):
        raise ValueError("Public annotations must be one bounded JSON object")
    _public_tree(raw_annotations)
    metadata_content = bounded_bytes(metadata_file, MAX_METADATA_BYTES)
    reader = csv.DictReader(io.StringIO(metadata_content.decode("utf-8")), strict=True)
    fields = reader.fieldnames or []
    if (not fields or len(fields) > PUBLIC_MAX_METADATA_COLUMNS or len(fields) != len(set(fields))
            or any(not field or len(field) > PUBLIC_MAX_METADATA_HEADER_CHARS for field in fields)):
        raise ValueError("Metadata requires distinct bounded column names")
    identity = config["observation_id_label"]
    if identity not in fields:
        raise ValueError("Metadata requires its declared observation ID column")
    rows = []
    for row in reader:
        if (len(rows) >= PUBLIC_MAX_METADATA_ROWS or None in row
                or any(value is None or len(value) > PUBLIC_MAX_METADATA_FIELD_CHARS for value in row.values())):
            raise ValueError("Metadata rows must exactly match the declared columns")
        rows.append(row)
    metadata_ids = _unique_labels([row[identity] for row in rows], "Metadata observation IDs")
    measurement = config["measurement"]
    if expression_path.suffix.lower() == ".csv":
        expression, genes, samples, provenance = _read_csv_expression(expression_path, measurement)
    else:
        remaining_bytes = (PUBLIC_MAX_AGGREGATE_BYTES - len(annotation_content) - len(metadata_content)
                           - public_json_size(public_task, max_bytes=PUBLIC_MAX_TASK_BYTES))
        expression, genes, samples, provenance = _read_zarr_expression(expression_path, measurement,
                                                                      max_storage_bytes=remaining_bytes)
    if set(samples) != set(metadata_ids) or len(samples) != len(metadata_ids):
        raise ValueError("Expression and metadata require an exact bijective observation-ID match")
    declared_genes = config.get("gene_ids", config.get("gene_universe"))
    if declared_genes is not None and (not isinstance(declared_genes, (list, tuple)) or tuple(declared_genes) != genes):
        raise ValueError("Expression genes must exactly match the ordered declared feature universe")
    import pandas as pd

    metadata = pd.DataFrame(rows).set_index(identity, drop=False).loc[list(samples)].copy()
    for field in ("measurement", "measurement_type"):
        if field in metadata and set(metadata[field]) != {measurement}:
            raise ValueError("Observed metadata measurement disagrees with the public layer declaration")
    chronology = {}
    for label_column, numeric_column in (("timepoint_label", "numeric_time"), ("time_label", "time")):
        if label_column in metadata and numeric_column in metadata:
            for label, coordinate in zip(metadata[label_column], metadata[numeric_column]):
                try:
                    number = float(coordinate)
                except (ValueError, TypeError):
                    raise ValueError("Chronological coordinates must be numeric") from None
                if not math.isfinite(number) or (label in chronology and chronology[label] != number):
                    raise ValueError("Chronological labels must have one finite numerical meaning")
                chronology[label] = number
    expression.flags.writeable = False
    provenance = dict(provenance, measurement=measurement, analysis_route=(
        "continuous_likelihood" if measurement == "continuous" else "count_likelihood"),
        matrix_bytes=int(expression.nbytes), exact_gene_alignment=True, exact_sample_alignment=True,
        transformation_history=config.get("transformation_history", []),
        measurement_declaration=config["measurement_layer"])
    context = dict(config["analysis_context"])
    if "analysis_columns" in config:
        context["analysis_columns"] = config["analysis_columns"]
    return ObservedData(expression, genes, samples, metadata, raw_annotations, measurement,
                        config["unit_hierarchy"], tuple(config["targets"]), provenance, context)
