"""Closed declarations for an experimental supplied-population contrast.

This contract identifies data and proposed statistical choices. It grants no
execution permission and does not authenticate declared biological identities.
"""
from __future__ import annotations

import hashlib
import json
import re


FORMAT = "devbench.scrna-population-spec/1"
PROCEDURE = "embryo_fixed_effect_stage_population_nb_v1"
TOOL_NAMES = tuple("scrna_population_" + stage for stage in ("audit", "design", "pseudobulk", "contrast"))
LIMITS = {"max_file_bytes": 2 * 1024**3, "max_cells": 100_000, "max_features": 100_000,
          "max_profiles": 256, "max_panel_bytes": 16 * 1024**2,
          "max_metadata_bytes": 32 * 1024**2, "block_rows": 128,
          "block_bytes": 16 * 1024**2, "timeout_seconds": 300, "memory_mb": 4096}
_FIELDS = {"format", "source_sha256", "measurement", "counts_layer", "populations",
    "biological_unit_column", "biological_unit_identity", "stage_column", "stage_levels",
    "technical_alias_columns", "question", "requirement_source", "procedure", "choice_origin"}


def canonical_bytes(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False).encode("utf-8")


def content_sha256(value):
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def _text(value, name, maximum=200):
    if type(value) is not str or not value.strip() or len(value) > maximum or "\x00" in value:
        raise ValueError(name + " must be bounded nonempty text")
    return value


def _texts(value, name, maximum=20, empty=False):
    if (type(value) is not list or not (0 if empty else 1) <= len(value) <= maximum
            or any(type(item) is not str for item in value) or len(set(value)) != len(value)):
        raise ValueError(name + " must be a bounded distinct array")
    return [_text(item, name) for item in value]


def validate_population_spec(specification):
    """Validate the exact declaration without reading input paths or data."""
    if type(specification) is not dict or not _FIELDS <= specification.keys() or set(specification) - _FIELDS - {"limits"}:
        raise ValueError("Population specification fields are incomplete or unknown")
    try:
        body = canonical_bytes(specification)
    except (ValueError, TypeError, RecursionError) as error:
        raise ValueError("Population specification requires finite JSON") from error
    if len(body) > 64 * 1024:
        raise ValueError("Population specification exceeds its byte bound")
    result = json.loads(body)
    if (result["format"] != FORMAT or result["procedure"] != PROCEDURE
            or result["measurement"] != "umi_counts" or result["biological_unit_identity"] != "declared"
            or result["choice_origin"] != "assistant_proposed"):
        raise ValueError("Unsupported population specification procedure or declarations")
    if type(result["source_sha256"]) is not str or not re.fullmatch("[a-f0-9]{64}", result["source_sha256"]):
        raise ValueError("source_sha256 must be the exact lowercase source digest")
    for key in ("counts_layer", "biological_unit_column", "stage_column"):
        _text(result[key], key)
        if "/" in result[key] or result[key] in {".", ".."}:
            raise ValueError(key + " must be a single metadata/layer name")
    _text(result["question"], "question", 8000)
    if type(result["requirement_source"]) is not dict or not result["requirement_source"]:
        raise ValueError("Retain a separate requirement_source provenance record")
    _texts(result["stage_levels"], "stage_levels")
    aliases = _texts(result["technical_alias_columns"], "technical_alias_columns", empty=True)
    if any("/" in name or name in {".", ".."} for name in aliases):
        raise ValueError("Technical alias columns must identify direct child metadata names")
    if result["stage_column"] == result["biological_unit_column"] or set(aliases) & {result["stage_column"], result["biological_unit_column"]}:
        raise ValueError("Biological unit, stage and technical alias roles must be distinct")
    populations = result["populations"]
    if type(populations) is not list or len(populations) != 2:
        raise ValueError("Declare exactly denominator then numerator populations")
    for population in populations:
        if type(population) is not dict or set(population) != {"label", "column", "values"}:
            raise ValueError("Population predicate requires exact label, column and values fields")
        _text(population["label"], "population label")
        _text(population["column"], "population column")
        if "/" in population["column"]:
            raise ValueError("Population column must be one metadata name")
        _texts(population["values"], "population values", maximum=100)
    if populations[0]["label"] == populations[1]["label"]:
        raise ValueError("Population labels must differ")
    supplied_limits = result.get("limits", {})
    if type(supplied_limits) is not dict or set(supplied_limits) - set(LIMITS):
        raise ValueError("Unknown population execution limits")
    for name, value in supplied_limits.items():
        minimum = 128 if name == "memory_mb" else 1
        if type(value) is not int or not minimum <= value <= LIMITS[name]:
            raise ValueError(name + " exceeds its fixed bounded range")
    result["limits"] = {**LIMITS, **supplied_limits}
    return result


def specification_sha256(specification):
    return content_sha256(validate_population_spec(specification))
