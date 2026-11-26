"""Bounded bulk gene-by-sample inputs, independent of observation QC contracts.

Declarations retain their caller attribution. Numeric integrity and exact label
alignment do not establish independent replication or scientific validity.
"""

from __future__ import annotations

import hashlib
import json
import math
import tempfile
from copy import deepcopy
from itertools import combinations
from pathlib import Path

from devbench.core._hashing import canonical_sha256 as _digest
from devbench.core.count_forensics import (
    CONFLICTING_PROVENANCE,
    SUSPECTED_TRANSFORM,
    CountDistribution,
    declares_count_transform,
)
from devbench.study.biology_input_limits import (
    EPISODE_EVALUATOR_FIELDS,
    PUBLIC_DENIED_KEYS,
)
from devharness_core.provenance import canonical_json

MAX_MATRIX_BYTES = 100_000_000
MAX_GENES = 100_000
MAX_SAMPLES = 128


def _labels(values, limit, label):
    if (
        not isinstance(values, (list, tuple))
        or not 1 <= len(values) <= limit
        or any(
            type(value) is not str or not value.strip() or len(value) > 200
            for value in values
        )
        or len(set(values)) != len(values)
    ):
        raise ValueError(f"Bulk RNA requires bounded unique {label} IDs")
    return list(values)


def _bulk_declaration_roles(declaration):
    keys = {"format", "representation", "axes", "provenance", "biological_independence"}
    if (
        type(declaration) is not dict
        or not keys <= set(declaration)
        or set(declaration) - keys - {"metadata_roles"}
        or declaration["format"] != "devbench.bulk-rna-input/1"
    ):
        raise ValueError("Use the versioned bulk RNA input declaration")
    if declaration["representation"] not in {
        None,
        "raw_counts",
        "normalized_expression",
    } or declaration["axes"] != ["gene", "sample"]:
        raise ValueError(
            "Declare raw counts, normalized expression or unknown, "
            "with gene/sample axes"
        )
    for field in ("provenance", "biological_independence"):
        if declaration[field] is not None and type(declaration[field]) is not dict:
            raise ValueError(
                "Bulk RNA provenance and independence require explicit objects or null"
            )
        if len(canonical_json(declaration[field])) > 100_000:
            raise ValueError("Bulk RNA declaration exceeds its bound")
    roles = declaration.get("metadata_roles", {})
    if (
        type(roles) is not dict
        or set(roles) - {"condition", "sex", "batch", "biological_unit", "time"}
        or any(
            type(column) is not str or not column or len(column) > 200
            for column in roles.values()
        )
    ):
        raise ValueError(
            "Bulk RNA metadata roles require bounded explicitly named columns"
        )
    return roles


def _align_metadata(metadata_rows, samples):
    if (
        type(metadata_rows) is not list
        or len(metadata_rows) != len(samples)
        or any(type(row) is not dict for row in metadata_rows)
    ):
        raise ValueError("Metadata requires one row per matrix sample ID")
    source_sample_ids = _labels(
        [row.get("sample_id") for row in metadata_rows], MAX_SAMPLES, "metadata sample"
    )
    if set(source_sample_ids) != set(samples):
        raise ValueError("Metadata sample IDs must match the matrix sample IDs exactly")
    source_metadata_sha256 = _digest(metadata_rows)
    positions = {identity: index for index, identity in enumerate(source_sample_ids)}
    source_indices = [positions[identity] for identity in samples]
    metadata_rows = [deepcopy(metadata_rows[index]) for index in source_indices]
    alignment = {
        "policy": "exact_unique_sample_id_join_no_positional_zip",
        "source_order_sample_ids": source_sample_ids,
        "matrix_order_sample_ids": samples,
        "source_indices_in_matrix_order": source_indices,
        "reordered": source_sample_ids != samples,
    }
    return metadata_rows, alignment, source_metadata_sha256


def _metadata_fields(metadata_rows, roles):
    fields = sorted({key for row in metadata_rows for key in row})
    if set(fields) & (
        (PUBLIC_DENIED_KEYS | EPISODE_EVALUATOR_FIELDS | {"expected_accuracy"})
        - {"role", "cohort_id"}
    ):
        raise ValueError(
            "Bulk RNA metadata cannot declare private or evaluator configuration fields"
        )
    if len(fields) > 128 or any(
        type(key) is not str or not key or len(key) > 200 for key in fields
    ):
        raise ValueError("Bulk RNA metadata columns exceed their bound")
    for row in metadata_rows:
        for value in row.values():
            if (
                value is not None
                and type(value) not in (str, bool, int, float)
                or type(value) is float
                and not math.isfinite(value)
                or type(value) is str
                and len(value) > 12_000
            ):
                raise ValueError(
                    "Bulk RNA sample metadata requires bounded finite scalar values"
                )
    if any(column not in fields for column in roles.values()):
        raise ValueError("Declared metadata role column is absent")
    if len(canonical_json(metadata_rows)) > 5_000_000:
        raise ValueError("Bulk RNA metadata exceeds its bound")
    return fields


def _numeric_scalar(value):
    if type(value).__module__.startswith("numpy"):
        value = value.item()
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError("Bulk RNA requires finite typed numeric measurements")
    return value


def _matrix_facts(matrix, gene_count, sample_count, representation):
    if hasattr(matrix, "nbytes") and matrix.nbytes > MAX_MATRIX_BYTES:
        raise ValueError("Bulk RNA matrix exceeds 100 MB")
    if len(matrix) != gene_count:
        raise ValueError("Bulk RNA matrix gene axis differs from its IDs")
    estimated_bytes = 0
    value_digest = hashlib.sha256()
    integer_values = True
    count_values_valid = True
    raw_counts = representation == "raw_counts"
    distribution = CountDistribution(major_width=sample_count, libraries_in_rows=False)
    for row in matrix:
        if len(row) != sample_count:
            raise ValueError("Bulk RNA matrix sample axis differs from its IDs")
        for value in row:
            value = _numeric_scalar(value)
            integer_values = integer_values and type(value) is int
            count_values_valid = (
                count_values_valid
                and value >= 0
                and (type(value) is int or value.is_integer())
            )
            if raw_counts and not count_values_valid:
                raise ValueError(
                    "Declared raw counts require "
                    "nonnegative integer-valued measurements"
                )
            encoded = canonical_json(value) + b"\n"
            estimated_bytes += len(encoded)
            if estimated_bytes > MAX_MATRIX_BYTES:
                raise ValueError("Bulk RNA matrix exceeds 100 MB")
            value_digest.update(encoded)
        if raw_counts:
            distribution.observe(row)
    return (
        value_digest.hexdigest(),
        "integer" if integer_values else "real",
        count_values_valid,
        raw_counts and distribution.suspected_transform,
    )


def _declared_transform(provenance, values_sha256):
    """Only consult structured processing history for this bulk matrix."""
    if (
        provenance.get("slot", "feature_rows") != "feature_rows"
        or provenance.get("measurement_values_sha256", values_sha256) != values_sha256
    ):
        return False
    return declares_count_transform(
        provenance.get("representation"), provenance.get("transformation")
    )


def _count1_disposition(declaration, values_sha256, suspect):
    """Preserve the plateau observation even after source-bound caller review."""
    if declaration["representation"] != "raw_counts":
        return {}
    provenance = declaration["provenance"] or {}
    conflict = _declared_transform(provenance, values_sha256)
    if not (suspect or conflict):
        return {}
    confirmation = provenance.get("count1_confirmation")
    details = {
        "count1_reason": CONFLICTING_PROVENANCE if conflict else SUSPECTED_TRANSFORM,
        "nonnegative_integer_values": True,
        "declared_representation": "raw_counts",
    }
    if (
        not conflict
        and isinstance(confirmation, dict)
        and set(confirmation) == {"slot", "measurement_values_sha256", "evidence"}
        and confirmation["slot"] == "feature_rows"
        and confirmation["measurement_values_sha256"] == values_sha256
        and isinstance(confirmation["evidence"], str)
        and confirmation["evidence"].strip()
        and len(confirmation["evidence"]) <= 4000
        and "\x00" not in confirmation["evidence"]
    ):
        return {**details, "count1_status": "caller_confirmed"}
    # Existing execution readers check the effective representation; some only
    # inspect numeric_storage for an explicitly selected count-based route.
    return {
        **details,
        "count1_status": "clarification_required",
        "count_values_valid": False,
        "representation": None,
    }


def _metadata_catalog(metadata_rows, fields):
    catalog = {}
    for column in fields:
        levels = {}
        for row in metadata_rows:
            value = row.get(column)
            token = canonical_json(value)
            levels.setdefault(token, {"value": value, "count": 0})["count"] += 1
        catalog[column] = {
            "types": sorted({type(row.get(column)).__name__ for row in metadata_rows}),
            "missing_count": sum(row.get(column) is None for row in metadata_rows),
            "distinct_count": len(levels),
            "levels": list(levels.values())[:32],
            "levels_complete": len(levels) <= 32,
        }
        numeric = [
            row.get(column)
            for row in metadata_rows
            if type(row.get(column)) in (int, float)
        ]
        if numeric:
            catalog[column]["numeric_summary"] = {
                "finite_count": len(numeric),
                "minimum": min(numeric),
                "maximum": max(numeric),
            }
    return catalog


def _metadata_cross_tabs(metadata_rows, roles):
    cross_tabs = {}
    for left, right in combinations(
        [
            role
            for role in ("condition", "sex", "batch", "biological_unit", "time")
            if role in roles
        ],
        2,
    ):
        groups = {}
        for row in metadata_rows:
            values = [row.get(roles[left]), row.get(roles[right])]
            groups.setdefault(canonical_json(values), {"values": values, "count": 0})[
                "count"
            ] += 1
        cross_tabs[left + ":" + right] = {
            "roles": [left, right],
            "groups": list(groups.values())[:32],
            "complete": len(groups) <= 32,
        }
    return cross_tabs


def validate_bulk_rna_input(
    declaration, *, gene_ids, sample_ids, metadata_rows, matrix
):
    """Inspect explicit genes x samples without conversion or method selection."""
    roles = _bulk_declaration_roles(declaration)
    genes = _labels(gene_ids, MAX_GENES, "gene")
    samples = _labels(sample_ids, MAX_SAMPLES, "sample")
    metadata_rows, alignment, source_metadata_sha256 = _align_metadata(
        metadata_rows, samples
    )
    fields = _metadata_fields(metadata_rows, roles)
    values_sha256, numeric_storage, count_values_valid, suspect = _matrix_facts(
        matrix, len(genes), len(samples), declaration["representation"]
    )
    disposition = _count1_disposition(declaration, values_sha256, suspect)
    catalog = _metadata_catalog(metadata_rows, fields)
    cross_tabs = _metadata_cross_tabs(metadata_rows, roles)
    return {
        **deepcopy(declaration),
        "inspection_format": "devbench.bulk-rna-inspection/1",
        "shape": [len(genes), len(samples)],
        "gene_ids_sha256": _digest(genes),
        "sample_ids_sha256": _digest(samples),
        "metadata_sha256": source_metadata_sha256,
        "aligned_metadata_sha256": _digest(metadata_rows),
        "metadata_alignment": alignment,
        "measurement_values_sha256": values_sha256,
        "numeric_storage": numeric_storage,
        "alignment_valid": True,
        "gene_ids_unique": True,
        "sample_ids_unique": True,
        "measurements_finite": True,
        "count_values_valid": count_values_valid,
        **disposition,
        "sample_metadata": deepcopy(metadata_rows) if len(samples) <= 128 else [],
        "sample_metadata_complete": len(samples) <= 128,
        "sample_metadata_fields": fields,
        "metadata_catalog": catalog,
        "metadata_role_cross_tabs": cross_tabs,
        "unknowns": [
            field
            for field in ("representation", "provenance", "biological_independence")
            if disposition.get(field, declaration[field]) is None
        ],
        "declaration_assurance": (
            "caller-declared; not independent scientific validation"
        ),
    }


def _read_json(path, limit):
    path = Path(path)
    if not path.is_absolute() or path.stat().st_size > limit:
        raise ValueError("Bulk RNA artifact requires an absolute bounded file")
    return json.loads(path.read_bytes())


def inspect_registered_bulk_rna_input(methods, binding, *, actor=None):
    """Recompute an inspection from registered immutable matrix and metadata."""
    if (
        type(binding) is not dict
        or set(binding)
        != {"format", "matrix_artifact_id", "metadata_artifact_id", "declarations"}
        or binding["format"] != "devbench.bulk-rna-binding/1"
    ):
        raise ValueError("Use an exact versioned bulk RNA artifact binding")
    references = {}
    for port in ("matrix", "metadata"):
        identity = binding[port + "_artifact_id"]
        path = methods.artifact_path(
            identity
        )  # Verify manifest and content before reading.
        references[port] = methods.get("artifact", identity)
        if references[port]["artifact_type"] != "json":
            raise ValueError("Bulk RNA /1 requires registered JSON artifacts")
        references[port] = {
            key: references[port][key]
            for key in ("artifact_id", "sha256", "size_bytes", "artifact_type")
        }
        if port == "matrix":
            matrix = _read_json(path, MAX_MATRIX_BYTES)
        else:
            metadata = _read_json(path, 5_000_000)
    if (
        type(matrix) is not dict
        or set(matrix) != {"gene_ids", "sample_ids", "matrix"}
        or type(metadata) is not dict
        or set(metadata) != {"samples"}
    ):
        raise ValueError("Bulk RNA /1 requires exact matrix and sample-metadata fields")
    result = validate_bulk_rna_input(
        binding["declarations"],
        gene_ids=matrix["gene_ids"],
        sample_ids=matrix["sample_ids"],
        metadata_rows=metadata["samples"],
        matrix=matrix["matrix"],
    )
    result["input_artifact_refs"] = references
    result["input_integrity_valid"] = True
    if actor is not None:
        # Use the existing immutable artifact service for the complete inspection.
        # The model receives a bounded view, not duplicated source metadata.
        with tempfile.NamedTemporaryFile(
            dir=methods.root, suffix=".json", delete=False
        ) as handle:
            retained_path = Path(handle.name)
            handle.write(canonical_json(result))
        try:
            retained = methods.import_artifact(
                retained_path, artifact_type="json", actor=actor
            )
        finally:
            retained_path.unlink(missing_ok=True)
        reference = {
            key: retained[key]
            for key in ("artifact_id", "sha256", "size_bytes", "artifact_type")
        }
        result = compact_bulk_rna_inspection(result)
        result["inspection_artifact_ref"] = reference
    return result


def _preview_scalar(value, max_chars=80):
    if type(value) is str and len(value) > max_chars:
        return {
            "value_preview": value[:max_chars],
            "value_sha256": _digest(value),
            "value_complete": False,
        }
    return {"value": value, "value_complete": True}


def compact_bulk_rna_inspection(full):
    """Retain exact typed facts and roles while bounding long metadata previews.

    Full values remain in the registered inspection; previews are explicitly
    incomplete and never substituted for the original data or scientific facts.
    """
    result = deepcopy(full)
    typed_column_names = bool(
        set(full["sample_metadata_fields"]) & {"role", "cohort_id"}
    )
    if len(canonical_json(result)) <= 32_000 and not typed_column_names:
        return result
    result["selector_projection_format"] = "devbench.bulk-rna-selector-view/1"
    result["full_inspection_sha256"] = _digest(full)
    result["sample_metadata"] = []
    result["sample_metadata_complete"] = False
    result["metadata_catalog_sha256"] = _digest(full["metadata_catalog"])
    role_columns = set(full.get("metadata_roles", {}).values())
    for column, catalog in result["metadata_catalog"].items():
        maximum = 16 if column in role_columns else 2
        original = catalog["levels"]
        catalog["levels"] = [
            {**_preview_scalar(level["value"]), "count": level["count"]}
            for level in original[:maximum]
        ]
        catalog["levels_complete"] = (
            catalog["levels_complete"]
            and len(original) <= maximum
            and all(level["value_complete"] for level in catalog["levels"])
        )
    for name, tab in result["metadata_role_cross_tabs"].items():
        original = tab["groups"]
        if len(canonical_json(original)) > 4000:
            tab["groups"] = [
                {
                    "values": [_preview_scalar(value) for value in group["values"]],
                    "count": group["count"],
                }
                for group in original[:4]
            ]
            tab["complete"] = False
        tab["groups_sha256"] = _digest(full["metadata_role_cross_tabs"][name]["groups"])
    # Rare, long field names can consume most of a context themselves. Keep
    # every catalog column's typed counts, plus explicit preview completeness.
    if len(canonical_json(result)) > 85_000:
        result["sample_metadata_fields_sha256"] = _digest(
            result["sample_metadata_fields"]
        )
        result["sample_metadata_fields"] = []
        result["sample_metadata_fields_complete"] = False
        for column, catalog in result["metadata_catalog"].items():
            if column not in role_columns:
                catalog["levels"] = []
                catalog["levels_complete"] = False
    if typed_column_names:
        result["metadata_columns"] = [
            {"name": column, **catalog}
            for column, catalog in result["metadata_catalog"].items()
        ]
        result["metadata_catalog"] = {}
        result["metadata_catalog_representation"] = (
            "typed_column_records_names_are_values"
        )
    return result
