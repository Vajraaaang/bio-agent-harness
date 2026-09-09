"""Immutable, portable analysis specifications; no clinical defaults or qualification claims.

Canonical JSON is UTF-8, sorted string keys, compact separators, no ASCII escaping
and no nonfinite numbers. Unicode is preserved exactly, without normalization.
JSON array inputs become immutable tuples through Pydantic's strict JSON parser;
Python callers must supply tuples. Hashes identify this specified content only.
"""
import hashlib
import json
import keyword
import math
import os
from pathlib import Path
import re
import stat
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StringConstraints, field_validator, model_validator

JSON_LIMIT_BYTES = 1024 * 1024
NonemptyText = Annotated[str, StringConstraints(strict=True, min_length=1, max_length=2048)]
Digest = Annotated[str, StringConstraints(strict=True, pattern=r"^[0-9a-f]{64}$")]
Revision = Annotated[StrictInt, Field(ge=1)]
Role = Literal["author", "reviewer"]


def canonical_json_bytes(value) -> bytes:
    """Serialize finite JSON values exactly; reject key coercion and foreign types."""
    def check(item):
        if item is None or type(item) in (bool, int, str):
            return
        if type(item) is float:
            if not math.isfinite(item):
                raise ValueError("Nonfinite JSON number")
            return
        if type(item) in (list, tuple):
            for child in item:
                check(child)
            return
        if type(item) is dict:
            if any(type(key) is not str for key in item):
                raise TypeError("JSON object keys must be strings")
            for child in item.values():
                check(child)
            return
        raise TypeError(f"Unsupported JSON value: {type(item).__name__}")

    check(value)
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def parse_json_bytes(raw: bytes):
    """Parse at most 1 MB of strict UTF-8 JSON, rejecting duplicates and nonfinite values."""
    if type(raw) is not bytes:
        raise TypeError("JSON input must be bytes")
    if len(raw) > JSON_LIMIT_BYTES:
        raise ValueError("JSON input exceeds 1 MB size limit")

    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError(f"Duplicate JSON key: {key}")
            result[key] = value
        return result

    def invalid_constant(value):
        raise ValueError(f"Nonfinite JSON number: {value}")

    try:
        parsed = json.loads(raw.decode("utf-8", errors="strict"), object_pairs_hook=pairs,
                            parse_constant=invalid_constant)
        canonical_json_bytes(parsed)  # Also detects overflowing finite-looking literals and surrogates.
    except RecursionError as error:
        raise ValueError("JSON nesting exceeds parser limit") from error
    return parsed


class FrozenContract(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True,
                              validate_default=True, revalidate_instances="always")

    @field_validator("*", mode="after")
    @classmethod
    def nonblank_strings(cls, value):
        if isinstance(value, str) and not value.strip():
            raise ValueError("Text must not be blank")
        return value

    def canonical_bytes(self) -> bytes:
        """Canonical bytes of validated fields, including all explicit defaults."""
        validated = type(self).model_validate(self)
        return canonical_json_bytes(validated.model_dump(mode="json"))

    def sha256(self) -> str:
        return hashlib.sha256(self.canonical_bytes()).hexdigest()


class AnalysisPlan(FrozenContract):
    schema_version: Literal["controlled_analysis/1"] = "controlled_analysis/1"
    plan_id: NonemptyText
    revision: Revision
    intended_use_ref: NonemptyText
    study_id: NonemptyText
    data_classification: Literal["synthetic", "public_processed", "restricted_clinical"]
    data_type: Literal["counts", "continuous"]
    selection_criterion: Literal["fixed"]
    spline_df: Annotated[StrictInt, Field(ge=2, le=5)]
    time_column: NonemptyText
    time_unit: NonemptyText
    categorical_covariates: tuple[NonemptyText, ...] = ()
    continuous_covariates: tuple[NonemptyText, ...] = ()
    hypothesis_genes: Annotated[tuple[NonemptyText, ...], Field(min_length=1, max_length=100)]
    calibration_genes: Annotated[tuple[NonemptyText, ...], Field(max_length=5000)] = ()
    normalization: Literal["rle", "none"]
    biological_unit_column: NonemptyText
    reference_sample_id: NonemptyText
    counts_sha256: Digest
    metadata_sha256: Digest
    implementation_sha256: Digest
    environment_sha256: Digest
    max_memory_mb: Annotated[StrictInt, Field(ge=1, le=65536)]
    timeout_sec: Annotated[StrictInt, Field(ge=1, le=86400)]

    @field_validator("time_column", "biological_unit_column", "categorical_covariates", "continuous_covariates")
    @classmethod
    def safe_identifiers(cls, value):
        for identifier in value if isinstance(value, tuple) else (value,):
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", identifier) or keyword.iskeyword(identifier):
                raise ValueError("Column names must be safe ASCII identifiers")
            if identifier in {"numeric_time", "C", "cr", "bs", "I", "Q"} or "__" in identifier:
                raise ValueError("Column name is reserved by the fixed-design formula implementation")
        return value

    @field_validator("categorical_covariates", "continuous_covariates", "hypothesis_genes", "calibration_genes")
    @classmethod
    def unique_items(cls, value):
        if len(value) != len(set(value)):
            raise ValueError("Collection entries must be unique")
        if any(not item.strip() for item in value):
            raise ValueError("Collection entries must not be blank")
        return value

    @field_validator("biological_unit_column")
    @classmethod
    def declared_biological_unit(cls, value):
        if value.lower() in {"unknown", "unverified", "not_available", "none", "null", "sample_id"}:
            raise ValueError("An explicit biological-unit column is required")
        return value

    @model_validator(mode="after")
    def coherent_plan(self):
        covariates = self.categorical_covariates + self.continuous_covariates
        if len(covariates) != len(set(covariates)) or self.time_column in covariates:
            raise ValueError("Covariates must be disjoint and must not repeat the time column")
        if self.biological_unit_column in (self.time_column, *covariates):
            raise ValueError("Biological-unit identity must be separate from time and modeled covariates")
        if self.data_type == "counts":
            if self.normalization != "rle":
                raise ValueError("Count plans require RLE normalization")
            if not self.calibration_genes or not set(self.hypothesis_genes).issubset(self.calibration_genes):
                raise ValueError("Count calibration genes must include every hypothesis gene")
        elif self.normalization != "none":
            raise ValueError("Continuous plans require no normalization")
        return self


def parse_plan_bytes(raw: bytes) -> AnalysisPlan:
    """Validate the JSON boundary before accepting strict JSON arrays as tuples."""
    parse_json_bytes(raw)
    return AnalysisPlan.model_validate_json(raw)


def load_plan(path) -> AnalysisPlan:
    """Read one absolute regular file under the 1 MB bound; do not follow its symlink."""
    source = Path(path)
    if not source.is_absolute():
        raise ValueError("Plan path must be absolute")
    descriptor = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode):
            raise ValueError("Plan must be a regular file")
        if info.st_size > JSON_LIMIT_BYTES:
            raise ValueError("Plan exceeds 1 MB size limit")
        return parse_plan_bytes(handle.read(JSON_LIMIT_BYTES + 1))
