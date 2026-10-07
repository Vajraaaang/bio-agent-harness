"""Neutral public biology transport contract; imports perform no I/O or numerics.

Producer and consumer share these conservative ceilings. Private truth has a
different custody/size contract and must not reuse or enlarge these public
limits. JSON counts one node per value/container, not per dictionary key; the
root has depth zero. Blank strings and nulls remain valid public values.
"""
from __future__ import annotations

import json
import math
import re
from typing import Any

PUBLIC_MAX_METADATA_BYTES = 5_000_000
PUBLIC_MAX_ANNOTATION_BYTES = 5_000_000
PUBLIC_MAX_TASK_BYTES = 5_000_000
PUBLIC_MAX_MATRIX_BYTES = 100_000_000
PUBLIC_MAX_DECODED_LABEL_BYTES = 5_000_000
PUBLIC_MAX_ZARR_METADATA_BYTES = 5_000_000
PUBLIC_MAX_ZARR_HEADER_BYTES = 100_000
PUBLIC_MAX_CHUNK_BYTES = 4 * 1024 * 1024
PUBLIC_MAX_AGGREGATE_BYTES = 125_000_000
PUBLIC_MAX_ZARR_FILES = 20_000
PUBLIC_MAX_FEATURES = 5000
MINIMUM_BIOLOGICAL_UNITS = 3  # Shared benchmark policy; not a power or identity guarantee.
PUBLIC_MAX_LABEL_CHARS = 200

PUBLIC_MAX_JSON_NODES = 200_000
PUBLIC_MAX_JSON_DEPTH = 24
PUBLIC_MAX_JSON_KEY_CHARS = 200
PUBLIC_MAX_JSON_STRING_CHARS = 12_000
PUBLIC_MAX_METADATA_COLUMNS = 256
PUBLIC_MAX_METADATA_ROWS = 100_000
PUBLIC_MAX_METADATA_HEADER_CHARS = 200
PUBLIC_MAX_METADATA_FIELD_CHARS = 12_000

PUBLIC_MEASUREMENTS = frozenset({"raw_counts", "umi_counts", "non_umi_counts", "continuous"})
PUBLIC_DENIED_KEYS = frozenset({
    "case_id", "paired_case_id", "template_id", "recipe_id", "cohort_id", "plan_identity", "role",
    "seed", "seeds", "generation_seed", "evaluator_seed", "evaluation_seed", "expected_scope", "expected_scopes", "expected_refusal",
    "expected_diagnostics", "target_scopes", "numerical_scoring", "private_truth", "private_targets",
    "private_goldens", "golden_answer", "generation_parameters", "latent_truth", "latent_simulation_truth",
    "rng_stream_roles", "structural_zero_gene_mask", "nonnull", "hypothesis_testable",
    "numerical_reference_available", "unidentifiable_simulation_effect",
    "simulation_population_fractions",
})


# Shared evaluator transport policy. Raw scientific metadata must enforce this
# before a typed projection can move column names from keys to ordinary values.
EPISODE_EVALUATOR_FIELDS = frozenset({
    "reward", "rewards", "reference_answer", "reference_answers", "evaluation_feedback",
    "heldout_feedback", "grader_feedback", "evaluation_scores",
})


def validate_public_structure(value: Any, *, depth: int = 0, budget: list[int] | None = None,
                              allow_algorithm_seed: bool = False, _path: tuple[str, ...] = ()) -> None:
    """Validate public JSON structure without reading files or importing models."""
    budget = [PUBLIC_MAX_JSON_NODES] if budget is None else budget
    if (type(depth) is not int or depth < 0 or not isinstance(budget, list) or len(budget) != 1
            or type(budget[0]) is not int or budget[0] > PUBLIC_MAX_JSON_NODES):
        raise ValueError("Public configuration structural bound cannot be enlarged")
    budget[0] -= 1
    if budget[0] < 0 or depth > PUBLIC_MAX_JSON_DEPTH:
        raise ValueError("Public configuration exceeds its structural bound")
    candidate_parameter_paths = {("parameter_candidates",), ("candidate", "parameter_candidates"),
        ("candidates", "parameter_candidates"), ("plan", "candidates", "parameter_candidates")}
    valid_parameter_path = (_path in {("parameters",), ("params",), ("scientific_parameters",),
        ("plan", "recommendation", "params"), ("plan", "results", "provenance", "parameters")}
        or len(_path) == 4 and _path[:3] == ("plan", "experiments", "parameters"))
    if isinstance(value, dict):
        if (allow_algorithm_seed and "parameter_candidates" in _path and value.get("name") == "seed"
                and _path not in candidate_parameter_paths):
            raise ValueError("Algorithm seed names are allowed only in scientific candidate parameters")
        if allow_algorithm_seed and _path in candidate_parameter_paths and value.get("name") == "seed":
            seed_value = value.get("value")
            if not (seed_value is None or type(seed_value) is int and 0 <= seed_value <= 2**32 - 1):
                raise ValueError("Public algorithm seed must be null or a bounded nonnegative integer")
        if allow_algorithm_seed and "parameter_candidates" in _path and value.get("name") in PUBLIC_DENIED_KEYS - {"seed"}:
            raise ValueError("Scientific parameters cannot name private or evaluator fields")
        for key, item in value.items():
            if not isinstance(key, str) or len(key) > PUBLIC_MAX_JSON_KEY_CHARS:
                raise ValueError("Public configuration requires bounded string keys")
            seed_schema = (allow_algorithm_seed and key == "seed" and (
                _path == ("interface", "parameters", "properties") or
                len(_path) == 4 and _path[0] == "registry" and _path[2:] == ("parameters", "properties")))
            algorithm_seed = allow_algorithm_seed and key == "seed" and valid_parameter_path
            if algorithm_seed and not (item is None or type(item) is int and 0 <= item <= 2**32 - 1):
                raise ValueError("Public algorithm seed must be null or a bounded nonnegative integer")
            if seed_schema:
                if type(item) is not dict or item.get("type") not in ("integer", "null", ["integer", "null"], ["null", "integer"]):
                    raise ValueError("Public algorithm seed schema must declare an integer or null")
                if "default" in item and not (item["default"] is None or type(item["default"]) is int and 0 <= item["default"] <= 2**32 - 1):
                    raise ValueError("Public algorithm seed schema default is invalid")
            if key in PUBLIC_DENIED_KEYS and not algorithm_seed and not seed_schema:
                raise ValueError("Public analysis cannot receive private or coordinator fields: " + key)
            validate_public_structure(item, depth=depth+1, budget=budget,
                                      allow_algorithm_seed=allow_algorithm_seed, _path=(*_path, key))
    elif isinstance(value, (list, tuple)):
        for item in value:
            validate_public_structure(item, depth=depth+1, budget=budget,
                                      allow_algorithm_seed=allow_algorithm_seed, _path=_path)
    elif isinstance(value, str):
        if len(value) > PUBLIC_MAX_JSON_STRING_CHARS:
            raise ValueError("Public configuration string exceeds its bound")
    elif value is not None and not isinstance(value, (bool, int, float)):
        raise ValueError("Public configuration must consist of JSON values")
    elif isinstance(value, float) and not math.isfinite(value):
        raise ValueError("Public configuration numbers must be finite")


def validate_expression_source_dtype(storage_dtype: str, measurement: str) -> str:
    """Check only the fixed Zarr dtype declaration, without creating an array."""
    if measurement not in PUBLIC_MEASUREMENTS:
        raise ValueError("Public expression measurement is unknown")
    match = re.fullmatch(r"[<>=|]([iuf])([1248])", storage_dtype) if isinstance(storage_dtype, str) else None
    if match is None or (match.group(1) == "f" and match.group(2) not in {"2", "4", "8"}):
        raise ValueError("Expression Zarr requires a fixed supported numeric dtype declaration")
    kind = match.group(1)
    if measurement == "continuous" and kind != "f":
        raise ValueError("Continuous expression requires floating-point Zarr source storage")
    if measurement != "continuous" and kind not in {"i", "u"}:
        raise ValueError("Count expression requires integer Zarr source storage")
    return kind


def public_json_size(value: Any, *, max_bytes: int = PUBLIC_MAX_TASK_BYTES) -> int:
    """Bound a public JSON value's compact UTF-8 representation incrementally.

File readers/writers must additionally enforce their actual encoded byte count;
whitespace in an on-disk document is not exempt from its public file ceiling.
"""
    if type(max_bytes) is not int or not 1 <= max_bytes <= PUBLIC_MAX_TASK_BYTES:
        raise ValueError("Public JSON byte ceiling cannot be enlarged")
    validate_public_structure(value)
    total = 0
    encoder = json.JSONEncoder(ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    for chunk in encoder.iterencode(value):
        total += len(chunk.encode("utf-8"))
        if total > max_bytes:
            raise ValueError("Public JSON exceeds its byte bound")
    return total
