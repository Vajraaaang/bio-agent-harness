"""Bounded, descriptive views of retained episode evidence.

No helper fits, normalizes, adjusts p-values, alters the hypothesis family, or
reads a participant-selected path. The coordinator must authenticate ``bindings``
and the source result. Delivery through the authority reserves the complete
page and its continuation receipt.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import re
from collections import Counter
from contextlib import contextmanager
from pathlib import Path

MAX_QUERY_BYTES = 12 * 1024
MAX_VIEW_BYTES = 10 * 1024  # Reserve coordinator receipt/envelope headroom.
MAX_ROWS = 20
MAX_INPUT_BYTES = 100_000_000
_SORTS = (
    "gene_id",
    "hypothesis_id",
    "p_value",
    "q_value",
    "q_local",
    "q_budget",
    "effect",
    "base_mean",
)
QUERY_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "mode": {
            "enum": [
                "gene_statistics",
                "counts",
                "paired",
                "annotations",
                "aggregate",
                "family_summary",
                "gene_set_summary",
            ]
        },
        "cursor": {"type": "string", "pattern": "^[a-f0-9]{32}$"},
        "gene_set_id": {"type": "string", "minLength": 1, "maxLength": 160},
        "target_id": {"type": "string", "minLength": 1, "maxLength": 300},
        "gene_ids": {
            "type": "array",
            "maxItems": 100,
            "uniqueItems": True,
            "items": {"type": "string", "minLength": 1, "maxLength": 300},
        },
        "filters": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "estimate_status": {"type": "string", "maxLength": 100},
                "gene_id": {"type": "string", "maxLength": 300},
                **{
                    field: {"type": "number", "minimum": 0}
                    for field in (
                        "p_value_lte",
                        "q_value_lte",
                        "q_local_lte",
                        "q_budget_lte",
                        "effect_abs_gte",
                        "base_mean_gte",
                    )
                },
            },
        },
        "sort_by": {"enum": list(_SORTS)},
        "descending": {"type": "boolean"},
        "offset": {"type": "integer", "minimum": 0, "maximum": 1000000},
        "limit": {"type": "integer", "minimum": 1, "maximum": MAX_ROWS},
        "layer": {"enum": ["raw", "normalized"]},
        "units": {
            "type": "array",
            "minItems": 1,
            "maxItems": MAX_ROWS,
            "uniqueItems": True,
            "items": {"type": "string", "minLength": 1, "maxLength": 200},
        },
        "samples": {
            "type": "array",
            "maxItems": MAX_ROWS,
            "uniqueItems": True,
            "items": {"type": "string", "minLength": 1, "maxLength": 300},
        },
        "pairs": {
            "type": "array",
            "maxItems": MAX_ROWS,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["reference", "comparison"],
                "properties": {
                    name: {"type": "string", "minLength": 1, "maxLength": 300}
                    for name in ("reference", "comparison")
                },
            },
        },
    },
}


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def _quantiles(values):
    values = sorted(value for value in values if _finite(value))
    if not values:
        return {
            "count": 0,
            "minimum": None,
            "q25": None,
            "median": None,
            "q75": None,
            "maximum": None,
        }

    def at(fraction):
        position = (len(values) - 1) * fraction
        low = int(position)
        high = min(low + 1, len(values) - 1)
        return values[low] * (1 - position + low) + values[high] * (position - low)

    return dict(
        count=len(values),
        minimum=values[0],
        q25=at(0.25),
        median=at(0.5),
        q75=at(0.75),
        maximum=values[-1],
    )


def _targets(result):
    if result.get("format") == "devbench.erql-family-synthesis/1":
        targets = []
        for family in result["families"]:
            rows = []
            for gene in family["genes"]:
                rows.append(
                    {
                        **gene,
                        "hypothesis_id": gene["gene_id"],
                        "estimate_status": gene["status"],
                        "values": [] if gene["effect"] is None else [gene["effect"]],
                        "p_value": gene["p"],
                        "missing_reason": gene.get("reason"),
                    }
                )
            observed = {row["gene_id"] for row in rows}
            rows.extend(
                {
                    "gene_id": gene,
                    "hypothesis_id": gene,
                    "estimate_status": "unavailable",
                    "status": "unavailable",
                    "values": [],
                    "missing_reason": family.get("reason")
                    or "family_feature_result_unavailable",
                }
                for gene in family["feature_ids"]
                if gene not in observed
            )
            targets.append(
                {
                    "target_id": family["family_id"],
                    "payload": {"gene_statistics": rows},
                    "feature_ids": family["feature_ids"],
                    "eligible_features": family["eligible_features"],
                    "family_status": family["status"],
                }
            )
        return targets
    return result.get("targets", result.get("target_results", [])) or []


def _effect(row):
    values = row.get("values", [])
    return (
        values[0]
        if isinstance(values, list) and len(values) == 1 and _finite(values[0])
        else None
    )


def _value(row, key):
    return _effect(row) if key == "effect" else row.get(key)


def _aggregate(rows):
    return {
        "attempted_gene_rows": len(rows),
        "tested_gene_rows": sum(_finite(row.get("p_value")) for row in rows),
        "status_counts": dict(
            sorted(
                Counter(
                    str(row.get("estimate_status", "unknown")) for row in rows
                ).items()
            )
        ),
        "quantiles": {
            key: _quantiles(_value(row, key) for row in rows)
            for key in ("p_value", "q_value", "effect", "base_mean")
        },
    }


def _compact(value, omitted, path="", depth=0):
    if isinstance(value, str):
        if len(value) > 400:
            omitted.append(path)
            return value[:400]
        return value
    if value is None or type(value) is bool or _finite(value):
        return value
    if isinstance(value, (list, tuple)):
        if value and all(_finite(item) for item in value):
            return {"numeric_summary": _quantiles(value)}
        if depth >= 3:
            omitted.append(path)
            return {"omitted_due_to_budget": True, "item_count": len(value)}
        if len(value) > 10:
            omitted.append(path)
        return [
            _compact(item, omitted, f"{path}[{i}]", depth + 1)
            for i, item in enumerate(value[:10])
        ]
    if isinstance(value, dict):
        return _compact_mapping(value, omitted, path, depth)
    return None


def _compact_mapping(value, omitted, path, depth):
    if depth >= 3:
        omitted.append(path)
        return {"omitted_due_to_budget": True, "field_count": len(value)}
    if len(value) > 20:
        omitted.append(path)
    fields = ((str(key)[:100], item) for key, item in list(value.items())[:20])
    return {
        key: _compact(item, omitted, f"{path}.{key}", depth + 1) for key, item in fields
    }


def _bound(view):
    # Default json.dumps spacing is deliberately used for the public byte cap.
    while len(json.dumps(view, allow_nan=False).encode()) > MAX_VIEW_BYTES:
        if view.get("rows"):
            view["rows"].pop()
            view["omitted_due_to_budget"]["rows"] += 1
        elif view.get("targets"):
            view["targets"].pop()
            view["omitted_due_to_budget"]["fields"].append("target")
        else:
            # Diagnostics can be arbitrarily large even after shape compaction.
            for key in ("diagnostics", "warnings", "normalization", "aggregate"):
                if key in view:
                    view.pop(key)
                    view["omitted_due_to_budget"]["fields"].append(key)
                    break
            else:
                raise ValueError("evidence_query_envelope_exceeds_budget")
    return view


def summarize_result(result):
    """Generic whole-family summaries, warnings and normalization availability."""
    omitted = []
    diagnostics = result.get("diagnostics") or {}
    normalization = diagnostics.get("normalization", result.get("normalization"))
    warnings = []
    for location in (result, diagnostics):
        for key in ("warnings", "calibration_warnings"):
            value = location.get(key, [])
            warnings.extend(value if isinstance(value, list) else [value])
    targets = []
    for target in _targets(result)[:MAX_ROWS]:
        payload = target.get("payload") or {}
        rows = payload.get("gene_statistics") or []
        targets.append(
            {
                "target_id": target.get("target_id"),
                **_aggregate(rows),
                "estimate_status": target.get("estimate_status"),
                "analysis_layer": payload.get("analysis_layer"),
                "selection_conditional_inference": payload.get(
                    "selection_conditional_inference"
                ),
                "normalization_available": normalization is not None,
            }
        )
    return _bound(
        {
            "format": "devbench.episode-evidence-summary/1",
            "scope": "retained_descriptive_evidence_not_independent_validation",
            "individual_gene_rows_in_prompt": 0,
            "full_result_retained": True,
            "targets": targets,
            "diagnostics": _compact(diagnostics, omitted, "diagnostics"),
            "warnings": _compact(warnings, omitted, "warnings"),
            "normalization": {
                "status": "available" if normalization is not None else "unavailable",
                "details": _compact(normalization, omitted, "normalization"),
            },
            "omitted_due_to_budget": {
                "rows": sum(
                    len((t.get("payload") or {}).get("gene_statistics", []))
                    for t in _targets(result)
                ),
                "fields": omitted,
            },
            "unavailable_fields": []
            if normalization is not None
            else ["normalization"],
        }
    )


def _validate(query):
    if not isinstance(query, dict) or set(query) - QUERY_SCHEMA["properties"].keys():
        raise ValueError("unregistered_evidence_query_field")
    try:
        size = len(json.dumps(query, allow_nan=False).encode())
    except (ValueError, TypeError):
        raise ValueError("nonfinite_or_invalid_evidence_query") from None
    if size > MAX_QUERY_BYTES:
        raise ValueError("evidence_query_exceeds_budget")
    # Use the same schema presented to the action selector, with exact bool/int
    # semantics (jsonschema also rejects bool as an integer).
    from jsonschema import Draft202012Validator

    errors = list(Draft202012Validator(QUERY_SCHEMA).iter_errors(query))
    if errors:
        raise ValueError("invalid_evidence_query: " + errors[0].message[:300])
    if ("gene_set_id" in query) != (query.get("mode") == "gene_set_summary"):
        raise ValueError("Gene-set summaries require an explicit gene_set_id")
    if "units" in query and (query.get("mode") != "counts" or "samples" in query):
        raise ValueError(
            "Select biological units only for counts, without a sample selector"
        )
    return dict(query)


def _family(result, query):
    targets = _targets(result)
    selected = (
        [target for target in targets if target.get("target_id") == query["target_id"]]
        if "target_id" in query
        else targets
    )
    if len(selected) > 1:
        raise ValueError("evidence_query_requires_unambiguous_target_id")
    return (
        (selected[0].get("payload") or {}).get("gene_statistics", [])
        if selected
        else []
    )


def _filtered(rows, query):
    genes = set(query.get("gene_ids", []))
    filters = query.get("filters", {})

    def keep(row):
        if "gene_ids" in query and row.get("gene_id") not in genes:
            return False
        for key, expected in filters.items():
            if key in {"gene_id", "estimate_status"}:
                if row.get(key) != expected:
                    return False
            else:
                field = "effect" if key == "effect_abs_gte" else key.rsplit("_", 1)[0]
                value = _value(row, field)
                if not _finite(value):
                    return False
                if key.endswith("lte") and value > expected:
                    return False
                if (
                    key.endswith("gte")
                    and (abs(value) if key == "effect_abs_gte" else value) < expected
                ):
                    return False
        return True

    return [row for row in rows if keep(row)]


def _sorted(rows, query):
    key = query.get("sort_by", "gene_id")
    # Deterministic identity ties; missing sort values always come last.
    rows = sorted(
        rows,
        key=lambda row: (
            str(row.get("gene_id", "")),
            str(row.get("hypothesis_id", "")),
        ),
    )
    present = [row for row in rows if _value(row, key) is not None]
    absent = [row for row in rows if _value(row, key) is None]
    return (
        sorted(
            present,
            key=lambda row: _value(row, key),
            reverse=query.get("descending", False),
        )
        + absent
    )


@contextmanager
def _bound_text(bindings, role, maximum=None):
    from devbench.core.population_data import bound_query_file

    name = bindings.get(role + "_path")
    if name is None:
        yield None
        return
    path = Path(name)
    if not path.is_absolute() or path.is_symlink():
        raise ValueError("evidence_binding_requires_absolute_regular_path")
    if not path.is_file():
        yield None
        return
    expected = bindings.get(role + "_sha256")
    if not isinstance(expected, str) or len(expected) != 64:
        raise ValueError("evidence_input_hash_binding_required")
    with bound_query_file(
        path, expected, MAX_INPUT_BYTES if maximum is None else maximum
    ) as raw:
        with io.TextIOWrapper(raw, encoding="utf-8", newline="") as stream:
            yield stream


def _read_counts(handle, bindings, wanted):
    reader = csv.reader(handle, strict=True)
    header = next(reader, [])
    if len(header) < 2 or header[0] not in {"gene_id", "gene", "gene_name", ""}:
        raise ValueError("evidence_counts_axis_invalid")
    samples = header[1:]
    if len(set(samples)) != len(samples) or any(not sample for sample in samples):
        raise ValueError("evidence_sample_axis_invalid")
    if "sample_ids" in bindings and samples != list(bindings["sample_ids"]):
        raise ValueError("evidence_sample_axis_mismatch")
    selected, genes = {}, []
    totals = [0.0] * len(samples)
    for row in reader:
        if len(row) != len(header) or not row[0]:
            raise ValueError("evidence_gene_axis_invalid")
        genes.append(row[0])
        try:
            values = [int(value) for value in row[1:]]
        except (ValueError, OverflowError):
            raise ValueError("evidence_raw_counts_invalid") from None
        if any(value < 0 or value > 2**53 - 1 for value in values):
            raise ValueError("evidence_raw_counts_invalid")
        totals = [a + b for a, b in zip(totals, values, strict=False)]
        if row[0] in wanted:
            selected[row[0]] = values
    return samples, genes, selected, totals


def _counts(bindings, wanted):
    if (
        bindings.get("counts_path")
        and Path(bindings["counts_path"]).suffix.lower() != ".csv"
    ):
        return None, "unsupported_counts_transport"
    with _bound_text(bindings, "counts") as handle:
        if handle is None:
            return None, "bound_counts_unavailable"
        samples, genes, selected, totals = _read_counts(handle, bindings, wanted)
    if len(set(genes)) != len(genes):
        raise ValueError("evidence_gene_axis_duplicate")
    if "gene_ids" in bindings and genes != list(bindings["gene_ids"]):
        raise ValueError("evidence_gene_axis_mismatch")
    with _bound_text(bindings, "metadata", 8_000_000) as handle:
        if handle is None:
            return None, "bound_metadata_unavailable"
        reader = csv.DictReader(handle, strict=True)
        identity = bindings.get("sample_id_column", "sample_id")
        if identity not in (reader.fieldnames or []):
            raise ValueError("evidence_metadata_sample_axis_unavailable")
        ids = [row[identity] for row in reader]
    if (
        len(ids) != len(samples)
        or len(set(ids)) != len(ids)
        or set(ids) != set(samples)
    ):
        raise ValueError("evidence_metadata_sample_axis_mismatch")
    return (samples, selected, totals, set(genes)), None


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_annotation_mapping_key")
        result[key] = value
    return result


def _declared_pairs(bindings, query):
    """Derive sample pairs from bound declarations and exact metadata membership."""
    from .checkpoints import digest

    task = bindings.get("public_task")
    if not isinstance(task, dict) or bindings.get("public_task_sha256") != digest(task):
        raise ValueError("paired_evidence_public_task_hash_binding_required")
    context = task.get("context", {})
    if context.get("dependence") != "paired":
        raise ValueError("paired_evidence_requires_declared_paired_dependence")
    contrast = context.get("requested_condition_contrast", {})
    reference, comparison = contrast.get("denominator"), contrast.get("numerator")
    if (
        not isinstance(reference, str)
        or not reference.strip()
        or not isinstance(comparison, str)
        or not comparison.strip()
        or reference == comparison
    ):
        raise ValueError("paired_evidence_requires_declared_contrast")
    questions = task.get("questions", [])
    selected = (
        [q for q in questions if q.get("target_id") == query["target_id"]]
        if "target_id" in query
        else questions
    )
    if len(selected) != 1:
        raise ValueError("paired_evidence_requires_unambiguous_declared_target")
    if selected[0].get("contrast") != comparison + " minus " + reference:
        raise ValueError("paired_evidence_declared_target_contrast_mismatch")
    identity = task.get("observation_id_label")
    unit = task.get("population_unit_label") or task.get("unit_hierarchy", {}).get(
        "population_unit_label"
    )
    condition = task.get("analysis_columns", {}).get("condition")
    groups = _metadata_pair_groups(
        bindings,
        identity,
        unit,
        condition,
        (reference, comparison),
    )
    if not groups or any(
        set(group) != {reference, comparison} for group in groups.values()
    ):
        raise ValueError("paired_evidence_declared_unit_incomplete")
    if len(groups) > MAX_ROWS:
        raise ValueError(
            "paired_evidence_declared_pairs_exceed_budget_use_explicit_pairs"
        )
    pairs = [
        {
            "reference": groups[unit_id][reference],
            "comparison": groups[unit_id][comparison],
        }
        for unit_id in sorted(groups)
    ]
    return pairs, {
        "public_task_sha256": bindings["public_task_sha256"],
        "metadata_sha256": bindings["metadata_sha256"],
        "population_unit_label": unit,
        "condition_column": condition,
        "reference_condition": reference,
        "comparison_condition": comparison,
        "target_id": selected[0]["target_id"],
        "distinct_declared_units": len(groups),
    }


def _metadata_pair_groups(bindings, identity, unit, condition, levels):
    groups = {}
    with _bound_text(bindings, "metadata", 8_000_000) as handle:
        if handle is None:
            raise ValueError("paired_evidence_bound_metadata_unavailable")
        reader = csv.DictReader(handle, strict=True)
        if any(
            not key or key not in (reader.fieldnames or [])
            for key in (identity, unit, condition)
        ):
            raise ValueError("paired_evidence_declared_columns_unavailable")
        for row in reader:
            if row[condition] not in levels:
                continue
            if not row[unit] or not row[identity]:
                raise ValueError("paired_evidence_declared_unit_incomplete")
            group = groups.setdefault(row[unit], {})
            if row[condition] in group:
                raise ValueError("paired_evidence_declared_unit_ambiguous")
            group[row[condition]] = row[identity]
    return groups


def _row_value(value, omitted, unavailable, path):
    # Evidence rows preserve vectors and pair records verbatim. Compaction of
    # diagnostics is a different operation from returning actual observations.
    if isinstance(value, str) and len(value) > 400:
        omitted.append(path)
        return value[:400]
    if isinstance(value, dict):
        return {
            key: _row_value(child, omitted, unavailable, path + "." + str(key))
            for key, child in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_row_value(child, omitted, unavailable, path + "[]") for child in value]
    if type(value) in (int, float) and not _finite(value):
        unavailable.append(path)
        return None
    return value


def _annotate(rows, bindings, *, matched=None, facts=None):
    with _bound_text(bindings, "annotation", 8_000_000) as handle:
        annotations = (
            json.load(handle, object_pairs_hook=_unique_object) if handle else {}
        )
    # Only this explicit gene-keyed namespace is supported. Other public
    # annotation content is not evidence for an inferred symbol mapping.
    mapping = (
        annotations.get("gene_annotations", {}) if isinstance(annotations, dict) else {}
    )
    if not isinstance(mapping, dict):
        mapping = {}
    if facts is not None:

        def unavailable(gene):
            item = mapping.get(gene)
            return item is None or isinstance(item, list) and len(item) != 1

        facts["missing_count"] = sum(unavailable(row["gene_id"]) for row in matched)
    output = []
    for row in rows:
        gene = row["gene_id"]
        annotation = mapping.get(gene)
        if isinstance(annotation, list):
            status = (
                "ambiguous"
                if len(annotation) > 1
                else "available"
                if annotation
                else "unavailable"
            )
            value = annotation[0] if len(annotation) == 1 else None
        else:
            status = "available" if annotation is not None else "unavailable"
            value = annotation
        output.append(
            {
                "gene_id": gene,
                "annotation_status": status,
                "annotation": value,
                "missing_reason": "multiple_bound_annotations"
                if status == "ambiguous"
                else "bound_annotation_missing"
                if status == "unavailable"
                else None,
            }
        )
    return output


def _row_status(row):
    status = row.get("estimate_status")
    if status == "failed":
        return "failed"
    if status in {"filtered", "filtered_all_zero", "excluded"}:
        return "filtered"
    if status in {"tested", "estimated", "ok"} and any(
        _finite(value) for value in row.get("values", [])
    ):
        return "tested"
    return "missing"


def _bounded_digest(value):
    hasher = hashlib.sha256()
    size = 0
    encoder = json.JSONEncoder(
        sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    )
    for piece in encoder.iterencode(value):
        encoded = piece.encode()
        size += len(encoded)
        if size > MAX_INPUT_BYTES:
            raise ValueError("Evidence source exceeds the input byte bound")
        hasher.update(encoded)
    return hasher.hexdigest()


def _gene_set_membership(bindings, set_id, measured, version):
    from .scientific_result_contract import GeneMapping, GeneSetLibrary

    library = bindings.get("gene_set_library")
    if library is None or _bounded_digest(library) != bindings.get(
        "gene_set_library_sha256"
    ):
        raise ValueError("A versioned, hash-bound gene-set library is required")
    library = GeneSetLibrary.model_validate_json(json.dumps(library, allow_nan=False))
    members = next(
        (item.feature_ids for item in library.sets if item.set_id == set_id),
        None,
    )
    if members is None:
        return None, 0
    mapping = bindings.get("gene_mapping")
    if mapping is not None:
        if _bounded_digest(mapping) != bindings.get("gene_mapping_sha256"):
            raise ValueError("Gene-set feature mapping hash mismatch")
        mapping = GeneMapping.model_validate_json(json.dumps(mapping, allow_nan=False))
        lookup = {record.source_id: record.target_id for record in mapping.records}
        mapped = [
            lookup[member] for member in members if lookup.get(member) is not None
        ]
        version.update(
            mapping_version=mapping.version,
            mapping_sha256=bindings["gene_mapping_sha256"],
        )
    else:
        feature_ids = set(measured)
        mapped = [member for member in members if member in feature_ids]
        version["mapping_rule"] = "exact_feature_identity"
    version.update(
        database_id=library.database_id,
        version=library.version,
        library_sha256=bindings["gene_set_library_sha256"],
        set_id=set_id,
    )
    return mapped, len(members)


def _directional_effects(mapped, by_gene):
    statuses, effects = Counter(), []
    for gene in mapped:
        row = by_gene.get(gene, {})
        status = _row_status(row)
        effect = _effect(row)
        if status == "tested":
            if effect is None:
                status = "missing"
            else:
                effects.append(effect)
        statuses[status] += 1
    return statuses, effects


def _directional_summary(view, targets, family, query, bindings, facts):
    if (
        "gene_ids" in query
        or "filters" in query
        or "sort_by" in query
        or query.get("offset")
    ):
        raise ValueError(
            "Complete directional summaries require the full declared membership"
        )
    if (
        not targets
        or "target_id" in query
        and not any(t["target_id"] == query["target_id"] for t in targets)
    ):
        view.update(
            status="unavailable",
            missing_reason="source_family_unavailable",
            matched_rows=0,
        )
        return
    target = next(
        t
        for t in targets
        if t["target_id"] == query.get("target_id", targets[0]["target_id"])
    )
    by_gene = {row["gene_id"]: row for row in family}
    if len(by_gene) != len(family):
        raise ValueError(
            "Directional summary requires one row per feature in the selected family"
        )
    measured = target.get("feature_ids", list(by_gene))
    if len(set(measured)) != len(measured) or set(by_gene) - set(measured):
        raise ValueError("Invalid complete feature membership in result family")
    version = {
        "family_id": target["target_id"],
        "family_sha256": _bounded_digest(target),
        "family_status": target.get("family_status", "retained"),
    }
    if query["mode"] == "gene_set_summary":
        mapped, total = _gene_set_membership(
            bindings,
            query["gene_set_id"],
            measured,
            version,
        )
        if mapped is None:
            view.update(
                status="unavailable",
                missing_reason="gene_set_unavailable",
                matched_rows=0,
            )
            return
    else:
        mapped = measured
        total = len(mapped)
    unmapped = total - len(mapped)
    statuses, effects = _directional_effects(mapped, by_gene)
    view.update(
        matched_rows=total,
        directional_summary={
            "scope": "descriptive_membership_and_observed_effects",
            "membership": version,
            "counts": {
                "mapped": len(mapped),
                "unmapped": unmapped,
                **{
                    key: statuses[key]
                    for key in ("filtered", "tested", "missing", "failed")
                },
            },
            "effects": {
                "up": _quantiles(value for value in effects if value > 0),
                "down": _quantiles(value for value in effects if value < 0),
                "zero": _quantiles(value for value in effects if value == 0),
            },
        },
    )
    if facts is not None:
        facts.update(
            missing_count=statuses["missing"],
            failed_count=statuses["failed"],
            excluded_count=unmapped + statuses["filtered"],
        )


def _query_identity(result, query, bindings):
    source_sha256 = _bounded_digest(result)
    parameters = {
        key: value for key, value in query.items() if key not in {"cursor", "offset"}
    }
    binding = {
        key: value for key, value in bindings.items() if not key.endswith("_path")
    }
    query_sha256 = _bounded_digest(
        {"source": source_sha256, "query": parameters, "bindings": binding}
    )
    return source_sha256, query_sha256


def _prepare_page(
    result, query, bindings, *, source_sha256, query_sha256, offset, include_statistics
):
    facts = {"missing_count": 0, "failed_count": 0, "excluded_count": 0}
    request = {key: value for key, value in query.items() if key != "cursor"}
    view = _query_result(
        result,
        {**request, "offset": offset},
        bindings=bindings,
        facts=facts,
        include_statistics=include_statistics,
    )
    if view["status"] == "unavailable":
        if offset:
            raise ValueError("Bound evidence became unavailable during pagination")
        view["next_offset"] = None
        if not view.get("rows"):
            facts["missing_count"] = view["matched_rows"]
    target = next(
        (
            target
            for target in _targets(result)
            if target["target_id"] == request.get("target_id")
        ),
        None,
    )
    if target is None:
        targets = _targets(result)
        target = targets[0] if len(targets) == 1 else {}
    eligibility = {
        "family_id": target.get("target_id"),
        "status": target.get("family_status"),
        "allocation_sha256": result.get("allocation_sha256"),
        "family_rows": view["family_rows"],
    }
    eligible = target.get(
        "eligible_features", (target.get("payload") or {}).get("multiplicity_scope")
    )
    if eligible is not None:
        eligibility.update(
            eligible_count=len(eligible), eligible_sha256=_bounded_digest(eligible)
        )
    return _seal_page(
        view,
        request,
        facts,
        _page_joins(bindings, request, view),
        eligibility,
        source_sha256=source_sha256,
        source_schema=result.get("format", "devbench.legacy-episode-result/1"),
        query_sha256=query_sha256,
        include_statistics=include_statistics,
    )


def _page_joins(bindings, request, view):
    mode = view["mode"]
    joins = {key: value for key, value in bindings.items() if key.endswith("_sha256")}
    joins["mode"] = mode
    for axis in ("gene_ids", "sample_ids", "unit_ids"):
        if axis in bindings:
            joins[axis + "_sha256"] = _bounded_digest(bindings[axis])
    if "biological_units" in view:
        joins["biological_unit_rule"] = "exact_bound_sample_to_unit"
    if mode in {"counts", "paired"}:
        joins.update(
            transport=bindings.get("counts_transport", "csv"),
            layer=request.get("layer", "raw"),
        )
        joins.update(
            {
                key: request[key]
                for key in ("samples", "pairs", "units")
                if key in request
            }
        )
    if "pairing_scope" in view:
        joins["pairing_rule"] = view["pairing_scope"]
    if "directional_summary" in view:
        joins["membership"] = view["directional_summary"]["membership"]

    return joins


def _seal_page(
    view,
    request,
    facts,
    joins,
    eligibility,
    *,
    source_sha256,
    source_schema,
    query_sha256,
    include_statistics,
):
    from .scientific_result_contract import QueryReceipt

    offset, mode = view["offset"], view["mode"]

    def seal(identity, policy_sha256, policy_version, registered_source_sha256):
        while True:
            next_offset = view.get("next_offset")
            if next_offset is not None and not view["rows"]:
                raise ValueError(
                    "Evidence query byte budget cannot fit a single row and receipt"
                )
            receipt = QueryReceipt(
                source_sha256=source_sha256,
                source_schema=source_schema,
                registered_source_sha256=registered_source_sha256,
                query_sha256=query_sha256,
                predicate={
                    key: request[key]
                    for key in ("target_id", "gene_ids", "filters", "gene_set_id")
                    if key in request
                },
                aggregation=view.get(
                    "aggregate_scope", mode if "directional_summary" in view else "none"
                ),
                sort={
                    "key": request.get("sort_by", "gene_id"),
                    "descending": request.get("descending", False),
                    "ties": "gene_id_then_hypothesis_id_ascending_missing_last",
                },
                joins=joins,
                eligibility=eligibility,
                **facts,
                total_matched=view["matched_rows"],
                total_returned=len(view["rows"]),
                offset=offset,
                status=view["status"],
                next_cursor=identity if next_offset is not None else None,
                complete=view["status"] == "available" and next_offset is None,
                disclosure_reservation_id=identity,
                policy_sha256=policy_sha256,
                policy_version=policy_version,
                statistics_included=include_statistics,
            )
            view["query_receipt"] = receipt.model_dump(mode="json")
            if len(json.dumps(view, allow_nan=False).encode()) <= MAX_VIEW_BYTES:
                return view
            if not view["rows"]:
                raise ValueError("Evidence query receipt exceeds the byte budget")
            view["rows"].pop()
            view["omitted_due_to_budget"]["rows"] += 1
            view["next_offset"] = offset + len(view["rows"])

    return seal


def query_result(
    result,
    query,
    *,
    bindings=None,
    evidence_delivery=None,
    _use_guard=None,
    _reservation_id=None,
):
    """Build a retained view or deliver a page with an authority-issued receipt."""
    query = _validate(query)
    bindings = bindings or {}
    if evidence_delivery is None:
        if "cursor" in query or _use_guard is not None or _reservation_id is not None:
            raise PermissionError("Evidence query requires its issuing authority")
        return _query_result(result, query, bindings=bindings)

    from .evidence_access import AccessDenied, EvidenceDelivery

    if type(evidence_delivery) is not EvidenceDelivery:
        raise ValueError("Evidence delivery must be coordinator-owned")
    evidence_delivery.check_query(query)
    if "offset" in query:
        raise AccessDenied("An authenticated query advances through its issued cursor")
    if query.get("sort_by") == "q_value" or "q_value_lte" in query.get("filters", {}):
        raise AccessDenied("Adjusted significance requires q_local or q_budget")
    broker, context_id, source_id = (
        evidence_delivery.broker,
        evidence_delivery.context_id,
        evidence_delivery.source_id,
    )
    source_sha256, query_sha256 = _query_identity(result, query, bindings)
    dependencies = tuple(
        sorted(
            {
                source_sha256,
                *(
                    value
                    for name, value in bindings.items()
                    if name.endswith("_sha256")
                    and isinstance(value, str)
                    and re.fullmatch(r"[a-f0-9]{64}", value)
                ),
            }
        )
    )
    policy, offset = broker.query_admission(
        context_id,
        source_id,
        query_sha256,
        query.get("cursor"),
        dependencies=dependencies,
    )
    if not policy.allow_outcomes and (
        query.get("mode", "gene_statistics")
        in {"gene_statistics", "aggregate", "family_summary", "gene_set_summary"}
        or query.get("sort_by") == "effect"
        or "effect_abs_gte" in query.get("filters", {})
    ):
        raise AccessDenied("Retained outcome evidence is outside this AccessPolicy")
    _check_requested_statistics(query, policy, result)
    # Validate and read the source before acquiring the authority's writer lock.
    seal = _prepare_page(
        result,
        query,
        bindings,
        source_sha256=source_sha256,
        query_sha256=query_sha256,
        offset=offset,
        include_statistics=policy.allow_statistics,
    )

    def build(identity, context, current, source):
        return seal(
            identity,
            context["policy_sha256"],
            current.version,
            source["content_sha256"],
        )

    return broker.release_query_page(
        context_id,
        source_id,
        query_sha256,
        policy.sha256(),
        build,
        _guard=_use_guard,
        dependencies=dependencies,
        _reservation_id=_reservation_id,
    )


def _check_requested_statistics(query, policy, result):
    from .evidence_access import AccessDenied

    metrics = {"p_value", "q_local", "q_budget"}
    filters = query.get("filters", {})
    requested = {field for field in metrics if field + "_lte" in filters}
    requested.update({query.get("sort_by")} & metrics)
    if not requested:
        return
    if policy.allowed_fields and requested - set(policy.allowed_fields):
        raise AccessDenied(
            "Query references a significance field outside the AccessPolicy"
        )
    family = _family(result, query)
    if any(not any(field in row for row in family) for field in requested):
        raise AccessDenied(
            "Requested significance field is unavailable in the bound family"
        )


_PARQUET_SCHEMA = "devbench.tier-a-result/1"
_PARQUET_METADATA = b"devbench.result"
TIER_A_PARQUET_MAX_BYTES = 256 * 1024**2
TIER_A_PARQUET_MAX_ROWS = 100_000
_PARQUET_BATCH_ROWS = 8192
_PARQUET_SCALARS = (
    "gene_id",
    "hypothesis_id",
    "estimate_status",
    "effect",
    "missing_reason",
)
_PARQUET_ADJUSTED = ("q_local", "q_budget")
_PARQUET_STATISTICS = ("p_value", "q_value", *_PARQUET_ADJUSTED, "intervals")
_PARQUET_INTERVAL_FIELDS = ("lower", "upper", "confidence_level")


@contextmanager
def _open_evidence_parquet(artifact, open_artifact):
    if artifact.kind != "table" or artifact.representation != "parquet":
        raise ValueError("Parquet evidence requires an accepted columnar table")
    if "admitted_research_inference" not in artifact.permitted_uses:
        raise PermissionError("Parquet evidence requires an admitted Tier A artifact")
    with _open_tier_a_parquet(artifact, open_artifact) as checked:
        yield checked


@contextmanager
def _open_tier_a_parquet(artifact, open_artifact):
    import pyarrow as pa
    import pyarrow.parquet as pq

    from rna_kernels.mounted_model import MODEL_QUANTITY_COLUMNS

    with open_artifact(artifact) as stream:
        if not 0 < stream.seek(0, io.SEEK_END) <= TIER_A_PARQUET_MAX_BYTES:
            raise ValueError("Parquet evidence exceeds its file byte bound")
        stream.seek(0)
        if hashlib.file_digest(stream, "sha256").hexdigest() != artifact.sha256:
            raise ValueError("Parquet artifact identity mismatch")
        stream.seek(0)
        reader = pq.ParquetFile(
            stream,
            thrift_string_size_limit=65_536,
            thrift_container_size_limit=16_384,
            arrow_extensions_enabled=False,
        )
        try:
            schema = reader.schema_arrow
            intervals = pa.struct(
                [(name, pa.float64()) for name in _PARQUET_INTERVAL_FIELDS]
            )
            fields = {
                **dict.fromkeys(
                    ("gene_id", "hypothesis_id", "estimate_status", "missing_reason"),
                    pa.string(),
                ),
                **dict.fromkeys(("effect", "p_value", "q_value"), pa.float64()),
                "coordinate_ids": pa.list_(pa.string()),
                "values": pa.list_(pa.float64()),
                "intervals": pa.list_(intervals),
                **{
                    name: pa.float64()
                    for name in (*_PARQUET_ADJUSTED, *MODEL_QUANTITY_COLUMNS)
                    if name in schema.names
                },
            }
            if (
                len(schema.names) != len(fields)
                or set(schema.names) != fields.keys()
                or any(schema.field(name).type != kind for name, kind in fields.items())
            ):
                raise ValueError("Unsupported Tier A Parquet column contract")
            size = sum(
                reader.metadata.row_group(index).total_byte_size
                for index in range(reader.num_row_groups)
            )
            if (
                not 0 < reader.metadata.num_rows <= TIER_A_PARQUET_MAX_ROWS
                or not 0 < size <= TIER_A_PARQUET_MAX_BYTES
            ):
                raise ValueError(
                    "Parquet evidence exceeds its row or decoded byte bound",
                )
            header = (schema.metadata or {}).get(_PARQUET_METADATA, b"")
            yield reader, _parquet_metadata(header, reader.metadata.num_rows)
        finally:
            reader.close()


def _parquet_metadata(raw, rows):
    if not 0 < len(raw) <= 4096:
        raise ValueError("Tier A Parquet summary exceeds its metadata budget")
    summary = json.loads(raw, object_pairs_hook=_unique_object)
    labels = ("target_id", "family_id", "measurement", "biological_unit")
    digests = ("request_sha256", "family_sha256")
    counts = summary.get("status_counts") if isinstance(summary, dict) else None
    if (
        not isinstance(summary, dict)
        or summary.get("format") != _PARQUET_SCHEMA
        or summary.get("claim_scope") != "admitted_research_inference"
        or any(
            type(summary.get(name)) is not str or not 0 < len(summary[name]) <= 300
            for name in labels
        )
        or not all(
            re.fullmatch("[a-f0-9]{64}", str(summary.get(name))) for name in digests
        )
        or type(summary.get("row_count")) is not int
        or summary["row_count"] != rows
        or type(summary.get("tested_count")) is not int
        or not 0 <= summary["tested_count"] <= rows
        or not isinstance(counts, dict)
        or not 0 < len(counts) <= 16
        or any(
            not 0 < len(name) <= 100 or type(count) is not int or count < 1
            for name, count in counts.items()
        )
        or sum(counts.values()) != rows
    ):
        raise ValueError("Invalid retained Tier A Parquet summary")
    retained = {
        name: summary[name]
        for name in (
            "format",
            *labels,
            *digests,
            "claim_scope",
            "row_count",
            "tested_count",
            "status_counts",
        )
    }
    if "comparison" in summary:
        if summary["comparison"] not in ("coefficient", "contrast", "nested", "lrt"):
            raise ValueError("Invalid retained Tier A comparison")
        retained["comparison"] = summary["comparison"]
    return retained


def parquet_evidence_summary(artifact, *, open_artifact):
    """Read the verified whole-family footer without decoding gene rows."""
    with _open_evidence_parquet(artifact, open_artifact) as (_, summary):
        return summary


def _parquet_status_counts(column):
    import pyarrow.compute as pc

    counts = pc.value_counts(column).to_pylist()
    return {item["values"]: item["counts"] for item in counts}


def _validate_parquet_rows(data, summary, numeric):
    import pyarrow.compute as pc

    for name in ("gene_id", "hypothesis_id"):
        axis = data[name]
        if (
            axis.null_count
            or pc.count_distinct(axis).as_py() != data.num_rows
            or pc.any(pc.equal(pc.utf8_length(axis), 0)).as_py()
            or pc.any(pc.greater(pc.utf8_length(axis), 300)).as_py()
        ):
            raise ValueError("Tier A Parquet requires unique bounded row identities")
    if _parquet_status_counts(data["estimate_status"]) != summary["status_counts"]:
        raise ValueError("Parquet family status counts disagree with the summary")
    columns = [data[name] for name in numeric]
    if "intervals" in data.column_names:
        intervals = pc.list_flatten(data["intervals"])
        columns.extend(
            pc.struct_field(intervals, name) for name in _PARQUET_INTERVAL_FIELDS
        )
    for values in columns:
        if pc.any(pc.invert(pc.is_finite(values))).as_py():
            raise ValueError("Tier A Parquet contains nonfinite evidence")
    if "p_value" in numeric:
        _validate_parquet_probabilities(data, numeric)


def _validate_parquet_probabilities(data, numeric):
    import pyarrow.compute as pc

    p_values = pc.fill_null(data["p_value"], 0.0)
    for name in ("p_value", "q_value", *_PARQUET_ADJUSTED):
        if name not in numeric:
            continue
        values = data[name]
        minimum = 0.0 if name == "p_value" else p_values
        invalid = pc.or_(pc.less(values, minimum), pc.greater(values, 1.0))
        if pc.any(invalid).as_py():
            raise ValueError("Tier A Parquet contains invalid retained probabilities")


def _parquet_statistic_columns(reader, allowed_fields):
    available = set(reader.schema_arrow.names)
    if allowed_fields:
        permitted = set(allowed_fields)
        if not permitted.issuperset(_PARQUET_INTERVAL_FIELDS):
            permitted.discard("intervals")
        # Apply the field restriction to newly disclosed statistics. The broker
        # continues to enforce its allowlist on the whole original envelope.
        available.intersection_update(permitted | {"p_value"})
    return tuple(name for name in _PARQUET_STATISTICS if name in available)


def _read_parquet_projection(reader, columns):
    import pyarrow as pa

    batches, decoded_bytes, rows = [], 0, 0
    for batch in reader.iter_batches(
        batch_size=_PARQUET_BATCH_ROWS,
        columns=columns,
        use_threads=False,
    ):
        rows += batch.num_rows
        decoded_bytes += batch.nbytes
        if rows > TIER_A_PARQUET_MAX_ROWS or decoded_bytes > TIER_A_PARQUET_MAX_BYTES:
            raise ValueError("Parquet evidence exceeds its row or decoded byte bound")
        batches.append(batch)
    if rows != reader.metadata.num_rows or not batches:
        raise ValueError("Parquet evidence row count disagrees with the summary")
    return pa.Table.from_batches(batches)


def _parquet_row_page(
    reader,
    summary,
    query,
    offset,
    include_statistics,
    allowed_fields=(),
):
    import pyarrow as pa
    import pyarrow.compute as pc

    statistics = (
        _parquet_statistic_columns(reader, allowed_fields) if include_statistics else ()
    )
    columns = [*_PARQUET_SCALARS, *statistics]
    data = _read_parquet_projection(reader, columns)
    numeric = ("effect", *(name for name in statistics if name != "intervals"))
    _validate_parquet_rows(data, summary, numeric)
    if "gene_ids" in query:
        genes = pa.array(query["gene_ids"], type=pa.string())
        data = data.filter(pc.is_in(data["gene_id"], value_set=genes))
    for field, value in query.get("filters", {}).items():
        if field == "effect_abs_gte":
            matched = pc.greater_equal(pc.abs(data["effect"]), value)
        elif field == "p_value_lte":
            matched = pc.less_equal(data["p_value"], value)
        else:
            matched = pc.equal(data[field], value)
        data = data.filter(matched)

    sort = query.get("sort_by", "gene_id")
    keys = [(sort, "descending" if query.get("descending") else "ascending")]
    keys.extend(
        (name, "ascending") for name in ("gene_id", "hypothesis_id") if name != sort
    )
    ordered = pc.sort_indices(data, sort_keys=keys, null_placement="at_end")
    selected = data.take(ordered.slice(offset, query.get("limit", MAX_ROWS)))
    counts = _parquet_status_counts(data["estimate_status"])
    failed = counts.get("failed", 0)
    filtered = sum(
        counts.get(key, 0)
        for key in (
            "filtered",
            "filtered_all_zero",
            "excluded",
        )
    )
    tested = pc.is_in(
        data["estimate_status"],
        value_set=pa.array(["estimated", "tested", "ok"]),
    )
    available = (
        pc.sum(tested).as_py()
        if summary.get("comparison") in {"nested", "lrt"}
        else pc.sum(pc.and_(tested, pc.is_valid(data["effect"]))).as_py()
    ) or 0
    rows = selected.to_pylist()
    for row in rows:
        row["values"] = [] if row["effect"] is None else [row["effect"]]
    return (
        rows,
        len(data),
        dict(
            missing_count=len(data) - failed - filtered - available,
            failed_count=failed,
            excluded_count=summary["row_count"] - len(data) + filtered,
        ),
    )


def _prepare_parquet_view(
    artifact,
    open_artifact,
    query,
    offset,
    include_statistics,
    allowed_fields=(),
):
    mode = query.get("mode", "gene_statistics")
    with _open_evidence_parquet(artifact, open_artifact) as (reader, summary):
        total = summary["row_count"]
        facts = {"missing_count": 0, "failed_count": 0, "excluded_count": 0}
        if query.get("target_id", summary["target_id"]) != summary["target_id"]:
            rows, matched = [], 0
            status = "unavailable"
        elif mode == "family_summary":
            rows, matched = [], total
            status = "available"
            counts = summary["status_counts"]
            failed = counts.get("failed", 0)
            excluded = sum(
                counts.get(key, 0)
                for key in (
                    "filtered",
                    "filtered_all_zero",
                    "excluded",
                )
            )
            estimated = sum(counts.get(key, 0) for key in ("estimated", "tested", "ok"))
            facts.update(
                failed_count=failed,
                excluded_count=excluded,
                missing_count=total - failed - excluded - estimated,
            )
        else:
            rows, matched, facts = _parquet_row_page(
                reader,
                summary,
                query,
                offset,
                include_statistics,
                allowed_fields,
            )
            status = "available"
    if offset > matched:
        raise ValueError("Bound Parquet evidence disagrees with the issued offset")
    next_offset = offset + len(rows)
    view = {
        "format": "devbench.episode-evidence-query/1",
        "status": status,
        "mode": mode,
        "scope": "retained_descriptive_evidence_not_refit_or_independent_validation",
        "family_rows": total,
        "matched_rows": matched,
        "offset": offset,
        "rows": rows,
        "omitted_due_to_budget": {"rows": matched - len(rows), "fields": []},
        "unavailable_fields": ["target"] if status == "unavailable" else [],
        "next_offset": (
            next_offset if mode == "gene_statistics" and next_offset < matched else None
        ),
    }
    if status == "unavailable":
        view["missing_reason"] = "source_target_unavailable"
    elif mode == "family_summary":
        view.update(family_summary=summary, aggregate_scope="complete_retained_family")
    return view, summary, facts


def _validate_parquet_query(query, evidence_delivery):
    from .evidence_access import AccessDenied, EvidenceDelivery

    query = _validate(query)
    if type(evidence_delivery) is not EvidenceDelivery:
        raise PermissionError("Parquet evidence requires its issuing authority")
    if "offset" in query:
        raise AccessDenied("An authenticated query advances through its issued cursor")
    evidence_delivery.check_query(query)
    filters = set(query.get("filters", {}))
    sort = query.get("sort_by", "gene_id")
    mode = query.get("mode", "gene_statistics")
    if (
        mode not in {"family_summary", "gene_statistics"}
        or sort not in {"gene_id", "hypothesis_id", "effect", "p_value"}
        or filters - {"gene_id", "estimate_status", "effect_abs_gte", "p_value_lte"}
        or set(query) & {"layer", "units", "samples", "pairs"}
    ):
        raise AccessDenied("Requested predicate, sort or query mode is unavailable")
    if mode == "family_summary" and set(query) - {"target_id", "mode"}:
        raise AccessDenied("Whole-family Parquet summaries accept an exact target")
    return query


def query_parquet_result(
    artifact,
    query,
    *,
    open_artifact,
    evidence_delivery,
    bindings=None,
    _use_guard=None,
    _reservation_id=None,
):
    """Issue a broker-bound Tier A page from selected Arrow rows."""
    from .evidence_access import AccessDenied

    query = _validate_parquet_query(query, evidence_delivery)
    bindings = bindings or {}
    broker = evidence_delivery.broker
    context_id, source_id = evidence_delivery.context_id, evidence_delivery.source_id
    source_sha = artifact.sha256
    joins = {key: value for key, value in bindings.items() if key.endswith("_sha256")}
    identity = {
        "schema": _PARQUET_SCHEMA,
        "source": source_sha,
        "version_id": artifact.version_id,
        "query": {key: value for key, value in query.items() if key != "cursor"},
        "bindings": {
            key: value for key, value in bindings.items() if not key.endswith("_path")
        },
    }
    query_sha = _bounded_digest(identity)
    dependencies = tuple(
        sorted(
            {
                source_sha,
                *(
                    value
                    for value in joins.values()
                    if isinstance(value, str) and re.fullmatch("[a-f0-9]{64}", value)
                ),
            }
        )
    )

    def guard(db):
        if _use_guard is not None:
            _use_guard(db)
        context, policy = broker._context_policy(db, context_id)
        source = broker._source(db, source_id)
        if source["content_sha256"] != source_sha:
            raise AccessDenied(
                "Parquet bytes differ from their registered evidence source",
            )
        broker._check(context, policy, source, set())
        if not policy.allow_outcomes:
            raise AccessDenied("Retained outcome evidence is outside this AccessPolicy")

    policy, offset = broker.query_admission(
        context_id,
        source_id,
        query_sha,
        query.get("cursor"),
        dependencies=dependencies,
    )
    broker.authority.ledger._read(guard)
    if (
        (query.get("sort_by") == "p_value" or "p_value_lte" in query.get("filters", {}))
        and policy.allowed_fields
        and "p_value" not in policy.allowed_fields
    ):
        raise AccessDenied("Significance field is outside the AccessPolicy")
    include_statistics = (
        policy.allow_statistics and query.get("mode") != "family_summary"
    )
    view, summary, facts = _prepare_parquet_view(
        artifact,
        open_artifact,
        query,
        offset,
        include_statistics,
        policy.allowed_fields,
    )
    seal = _seal_page(
        view,
        query,
        facts,
        {**joins, "transport": "parquet", "mode": view["mode"]},
        {
            "family_id": summary["family_id"],
            "family_sha256": summary["family_sha256"],
            "family_rows": view["family_rows"],
        },
        source_sha256=source_sha,
        source_schema=_PARQUET_SCHEMA,
        query_sha256=query_sha,
        include_statistics=include_statistics,
    )

    def build(identity, context, current, source):
        return seal(
            identity,
            context["policy_sha256"],
            current.version,
            source["content_sha256"],
        )

    return broker.release_query_page(
        context_id,
        source_id,
        query_sha,
        policy.sha256(),
        build,
        _guard=guard,
        dependencies=dependencies,
        _reservation_id=_reservation_id,
    )


def rebuild_query_page(result, query, *, bindings, receipt):
    """Reconstruct a sealed historical page for local episode verification."""
    from .scientific_result_contract import QueryReceipt

    receipt = QueryReceipt.model_validate(receipt)
    source_sha256, query_sha256 = _query_identity(result, query, bindings)
    seal = _prepare_page(
        result,
        query,
        bindings,
        source_sha256=source_sha256,
        query_sha256=query_sha256,
        offset=receipt.offset,
        include_statistics=receipt.statistics_included,
    )
    page = seal(
        receipt.disclosure_reservation_id,
        receipt.policy_sha256,
        receipt.policy_version,
        receipt.registered_source_sha256,
    )
    if page["query_receipt"] != receipt.model_dump(mode="json"):
        raise ValueError("Historical query receipt disagrees with its retained source")
    return page


def _page_row_facts(family, rows, facts):
    identities = [(row.get("gene_id"), row.get("hypothesis_id", "")) for row in family]
    if any(
        not isinstance(gene, str) or not gene or not isinstance(hypothesis, str)
        for gene, hypothesis in identities
    ) or len(set(identities)) != len(identities):
        raise ValueError(
            "Evidence family requires unique feature/hypothesis identities"
        )
    statuses = Counter(_row_status(row) for row in rows)
    facts.update(
        missing_count=statuses["missing"],
        failed_count=statuses["failed"],
        excluded_count=len(family) - len(rows) + statuses["filtered"],
    )


def _aggregate_query(view, rows, facts):
    view["aggregate"] = _aggregate(rows)
    if facts is not None:
        quantiles = view["aggregate"]["quantiles"]
        quantiles.pop("q_value")
        for key in ("q_local", "q_budget"):
            if any(key in row for row in rows):
                quantiles[key] = _quantiles(row.get(key) for row in rows)
    view["aggregate_scope"] = "entire_matching_family_before_pagination"
    return _bound(view)


def _statistic_rows(result, selected, facts, include_statistics):
    fields = (
        "gene_id",
        "hypothesis_id",
        "estimate_status",
        "values",
        "p_value",
        "q_value",
        "q_local",
        "q_budget",
        "missing_reason",
        "base_mean",
    )
    if result.get("format") == "devbench.erql-family-synthesis/1":
        fields += ("effect", "status")
    if facts is not None:
        fields = tuple(key for key in fields if key != "q_value")
    if not include_statistics:
        fields = tuple(
            key
            for key in fields
            if key not in {"p_value", "q_value", "q_local", "q_budget"}
        )
    return [{key: row[key] for key in fields if key in row} for row in selected]


def _count_query_input(view, query, bindings, wanted, paired_samples):
    mode_is_counts = view["mode"] == "counts"
    transport = bindings.get("counts_transport")
    if transport in {"h5ad_csr", "count_profiles"}:
        from devbench.core.population_data import query_population_counts

        samples = list(bindings.get("sample_ids", []))
        chosen = query.get("samples", samples[:MAX_ROWS])
        if "units" in query:
            unit_ids = bindings.get("unit_ids", [])
            if set(query["units"]) - set(unit_ids):
                raise ValueError("Requested biological unit is outside the bound axis")
            chosen = [
                sample
                for sample, unit in zip(samples, unit_ids, strict=True)
                if unit in query["units"]
            ]
            if len(chosen) > MAX_ROWS:
                raise ValueError(
                    "Biological unit drill-down exceeds the observation budget"
                )
        required = set(chosen if mode_is_counts else paired_samples)
        counts, totals, features, units = query_population_counts(
            bindings,
            wanted,
            required,
            MAX_INPUT_BYTES,
        )
        view["biological_units"] = {
            sample: units[sample] for sample in sorted(required)
        }
    else:
        if "units" in query:
            raise ValueError(
                "Biological unit drill-down requires a bound biological unit axis"
            )
        data, reason = _counts(bindings, wanted)
        if data is None:
            return None, reason
        samples, counts, totals, features = data
        chosen = query.get("samples", samples[:MAX_ROWS])
        required = set(chosen if mode_is_counts else paired_samples)
        counts = {
            gene: dict(zip(samples, values, strict=True))
            for gene, values in counts.items()
        }
        totals = dict(zip(samples, totals, strict=True))
    if required - set(samples):
        raise ValueError("evidence_query_sample_axis_mismatch")
    return (samples, counts, totals, features, chosen, required), None


def _count_query_factors(result, bindings, samples, layer):
    if layer != "normalized":
        return dict.fromkeys(samples, 1.0)
    normalization = (result.get("diagnostics") or {}).get("normalization") or {}
    retained = normalization.get(
        "per_sample_size_factors", bindings.get("size_factors")
    )
    if (
        not isinstance(retained, dict)
        or set(retained) != set(samples)
        or (
            "observation_ids" in normalization
            and normalization["observation_ids"] != samples
        )
        or any(
            not _finite(retained[sample]) or retained[sample] <= 0 for sample in samples
        )
    ):
        return None
    return retained


def _paired_count_row(record, pairs, scaled, values, totals):
    projected = []
    for pair in pairs:
        reference, comparison = pair["reference"], pair["comparison"]
        before, after = scaled[reference], scaled[comparison]
        if before is None or after is None:
            projected.append(
                {
                    **pair,
                    "reference_value": before,
                    "comparison_value": after,
                    "difference": None,
                    "missing_reason": "profile_unavailable",
                }
            )
            continue
        difference = after - before
        reference_total, comparison_total = totals[reference], totals[comparison]
        projected.append(
            {
                **pair,
                "reference_value": before,
                "comparison_value": after,
                "difference": difference,
                "sign": 1 if difference > 0 else -1 if difference < 0 else 0,
                "reference_share": values[reference] / reference_total
                if reference_total
                else None,
                "comparison_share": values[comparison] / comparison_total
                if comparison_total
                else None,
                "share_missing_reason": "zero_library_total"
                if not reference_total or not comparison_total
                else None,
                "log2_ratio": math.log2(after) - math.log2(before)
                if before > 0 and after > 0
                else None,
                "log2_ratio_missing_reason": (
                    "zero_in_pair" if not before or not after else None
                ),
            }
        )
    differences = [
        pair["difference"] for pair in projected if pair["difference"] is not None
    ]
    absolute_total = sum(abs(value) for value in differences)
    definition = (
        "maximum_absolute_pair_difference_divided_by_sum_absolute_pair_differences"
    )
    record.update(
        pairs=projected,
        pair_sign_counts={
            "positive": sum(value > 0 for value in differences),
            "negative": sum(value < 0 for value in differences),
            "zero": sum(value == 0 for value in differences),
        },
        absolute_difference_shares=[
            abs(value) / absolute_total for value in differences
        ]
        if absolute_total
        else None,
        descriptive_dominance=max(abs(value) for value in differences) / absolute_total
        if absolute_total
        else None,
        descriptive_dominance_definition=definition,
        descriptive_dominance_missing_reason=None
        if absolute_total
        else "all_pair_differences_zero",
    )


def _count_query_rows(selected, counts, totals, chosen, required, factors, pairs, mode):
    rows = []
    for row in selected:
        gene = row.get("gene_id")
        values = counts.get(gene)
        if values is None:
            rows.append(
                {
                    "gene_id": gene,
                    "status": "unavailable",
                    "missing_reason": "gene_absent_from_bound_counts",
                }
            )
            continue
        scaled = {
            sample: (
                None if values[sample] is None else values[sample] / factors[sample]
            )
            for sample in required
        }
        if not all(value is None or _finite(value) for value in scaled.values()):
            return None
        record = {"gene_id": gene}
        if mode == "counts":
            record["counts"] = {sample: scaled[sample] for sample in chosen}
        else:
            _paired_count_row(record, pairs, scaled, values, totals)
        rows.append(record)
    return rows


def _count_query_unavailable(view, reason, field, facts=None, matched=0):
    view.update(status="unavailable", missing_reason=reason, unavailable_fields=[field])
    if facts is not None:
        facts["missing_count"] = matched


def _project_count_query(result, view, selected, rows, query, bindings, facts):
    if bindings.get("measurement", "raw_counts") not in {
        "raw_counts",
        "umi_counts",
        "non_umi_counts",
    }:
        _count_query_unavailable(view, "input_is_not_raw_counts", "counts")
        return
    pairs = query.get("pairs", [])
    if view["mode"] != "counts":
        view["pairing_scope"] = (
            "explicit_sample_pairs_not_biological_identity_or_independence_validation"
        )
        if not pairs:
            pairs, view["pairing_declarations"] = _declared_pairs(bindings, query)
            view["pairing_scope"] = (
                "hash_bound_metadata_declarations_not_biological_identity_or_independence_validation"
            )
    paired_samples = [
        pair[key] for pair in pairs for key in ("reference", "comparison")
    ]
    wanted = {row["gene_id"] for row in selected}
    data, reason = _count_query_input(view, query, bindings, wanted, paired_samples)
    if data is None:
        _count_query_unavailable(view, reason, "counts", facts, len(rows))
        return
    samples, counts, totals, features, chosen, required = data
    if facts is not None:
        facts["missing_count"] = sum(
            row["gene_id"] not in features
            or any(totals[sample] is None for sample in required)
            for row in rows
        )
    layer = query.get("layer", "raw")
    factors = _count_query_factors(result, bindings, samples, layer)
    if factors is None:
        _count_query_unavailable(
            view,
            "retained_normalization_factors_unavailable",
            "normalized_counts",
            facts,
            len(rows),
        )
        return
    view["layer"] = layer
    observations = _count_query_rows(
        selected,
        counts,
        totals,
        chosen,
        required,
        factors,
        pairs,
        view["mode"],
    )
    if observations is None:
        _count_query_unavailable(
            view,
            "nonfinite_normalized_counts",
            "normalized_counts",
        )
        return
    view["rows"] = observations


def _bound_row_page(view, offset, total):
    omitted = view["omitted_due_to_budget"]["fields"]
    view["rows"] = [
        {
            key: _row_value(
                value, omitted, view["unavailable_fields"], f"rows[{i}].{key}"
            )
            for key, value in row.items()
        }
        for i, row in enumerate(view["rows"])
    ]
    # Retain each identity even when one bulky evidence field cannot fit, so
    # pagination still advances and budget omission cannot masquerade as absence.
    for i, row in enumerate(view["rows"]):
        for key in list(row):
            if (
                key not in {"gene_id", "hypothesis_id"}
                and len(json.dumps(row[key], allow_nan=False).encode()) > 6000
            ):
                row.pop(key)
                omitted.append(f"rows[{i}].{key}")
    bounded = _bound(view)
    if bounded.get("next_offset") is not None or bounded["rows"]:
        next_offset = offset + len(bounded["rows"])
        bounded["next_offset"] = next_offset if next_offset < total else None
    return bounded


def _query_result(result, query, *, bindings=None, facts=None, include_statistics=True):
    """Project retained data with coordinator-owned bindings.

    Pairs use comparison minus reference, sample shares of the entire count
    matrix, and log2(comparison/reference) when both values are positive.
    Normalized values require retained positive sample-keyed size_factors.
    """
    query = _validate(query)
    bindings = bindings or {}
    mode = query.get("mode", "gene_statistics")
    targets = _targets(result)
    family = _family({"targets": targets}, query)
    if (
        not family
        and mode in {"counts", "paired", "annotations"}
        and "target_id" not in query
    ):
        family = [{"gene_id": gene} for gene in bindings.get("gene_ids", [])]
    rows = _filtered(family, query)
    if facts is not None:
        _page_row_facts(family, rows, facts)
    offset = query.get("offset", 0)
    view = {
        "format": "devbench.episode-evidence-query/1",
        "status": "available",
        "mode": mode,
        "scope": "retained_descriptive_evidence_not_refit_or_independent_validation",
        "family_rows": len(family),
        "matched_rows": len(rows),
        "offset": offset,
        "rows": [],
        "omitted_due_to_budget": {"rows": 0, "fields": []},
        "unavailable_fields": [],
    }
    if "target_id" in query and not any(
        t.get("target_id") == query["target_id"] for t in targets
    ):
        view.update(
            status="unavailable",
            missing_reason="source_target_unavailable",
            unavailable_fields=["target"],
        )
        return _bound(view)
    if mode in {"family_summary", "gene_set_summary"}:
        _directional_summary(view, targets, family, query, bindings, facts)
        return _bound(view)
    if mode == "aggregate":
        return _aggregate_query(view, rows, facts)
    ordered = _sorted(rows, query)
    selected = ordered[offset : offset + query.get("limit", MAX_ROWS)]
    view["omitted_due_to_budget"]["rows"] = len(rows) - len(selected)
    view["next_offset"] = (
        offset + len(selected) if offset + len(selected) < len(rows) else None
    )
    if mode == "gene_statistics":
        view["rows"] = _statistic_rows(result, selected, facts, include_statistics)
    elif mode == "annotations":
        view["rows"] = _annotate(selected, bindings, matched=rows, facts=facts)
    else:
        _project_count_query(result, view, selected, rows, query, bindings, facts)
        if view["status"] == "unavailable":
            return _bound(view)
    return _bound_row_page(view, offset, len(rows))
