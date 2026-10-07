"""Explicitly gated summaries of a complete, bounded observed gene family.

Import and request validation are configuration-only. Reading prepared Zarr v2
inputs and importing the numerical runtime require literal opt-in. The public
wrapper uses the existing supervised worker, including any coordinator
``worker_limits`` deadline, memory budget and scratch directory. Standalone
callers must invoke it under an ``if __name__ == "__main__":`` guard.

These are equally weighted library means at supplied numeric times, retaining
each time's observed batch mixture. They are neither fitted population curves
nor an assessment of independent biological replication.
"""

from __future__ import annotations

import csv
import io
import json
import math
from pathlib import Path
import re
from typing import Any


MAX_GENES = 5000
MAX_METADATA_BYTES = 5_000_000
MAX_HEADER_BYTES = 100_000
MAX_PANEL_BYTES = 16 * 1024 * 1024
MAX_CHUNK_BYTES = 4 * 1024 * 1024
MAX_MATRIX_BYTES = 100_000_000
MAX_RESULT_BYTES = 8_000_000
MIN_TIMEPOINTS = 3
MAX_TIMEPOINTS = 100


class DataExecutionDisabled(PermissionError):
    """A summary request alone does not authorize reading expression data."""


def _require_data_execution(allowed: bool) -> None:
    if allowed is not True:
        raise DataExecutionDisabled("Observed summaries require explicit allow_data_execution=True")


def _validate_request(gene_ids, measurement_type, time_col) -> tuple[str, ...]:
    """Validate declarations without importing a worker or touching any input."""
    if (not isinstance(gene_ids, (tuple, list)) or not 1 <= len(gene_ids) <= MAX_GENES
            or any(not isinstance(gene, str) or not gene.strip() or len(gene) > 200 for gene in gene_ids)
            or len(set(gene_ids)) != len(gene_ids)):
        raise ValueError("gene_ids must be the complete distinct declared family of 1..5000 labels")
    if not isinstance(measurement_type, str) or measurement_type not in {"raw_counts", "continuous"}:
        raise ValueError("measurement_type must be raw_counts or continuous")
    if not isinstance(time_col, str) or not time_col.strip() or len(time_col) > 200:
        raise ValueError("time_col must name the supplied numeric time column")
    return tuple(gene_ids)


def summarize_observed_expression(counts_path, metadata_path, *, gene_ids,
                                  measurement_type, time_col="numeric_time",
                                  allow_data_execution=False) -> dict[str, Any]:
    """Describe every declared gene; default denial precedes imports/path access.

Prepared Zarr v2 expression must have exactly the supplied family and a
bijective sample-ID match to metadata. Continuous values, including RPKM, stay
on their input scale. Counts require a whole-family float64 panel <=16 MiB and
the documented ``median_ratio_factors`` RLE estimator, including its disclosed
positive-count variant. No reference panel is silently sampled.

``genes[*].mean_curve`` contains arithmetic means on top-level ``time_grid``.
Continuous effects are last-minus-first means in input expression units;
count effects are log2 changes of positive normalized endpoint means. Missing
effects have an explicit reason. No tests or intervals are calculated.
"""
    _require_data_execution(allow_data_execution)
    family = _validate_request(gene_ids, measurement_type, time_col)
    paths = tuple(Path(path) for path in (counts_path, metadata_path))
    if any(not path.is_absolute() for path in paths):
        raise ValueError("Prepared expression and metadata require absolute paths")
    from devharness_core.watchdog import run_sandboxed

    return run_sandboxed(_summarize_worker, (
        *(str(path.resolve()) for path in paths), family, measurement_type, time_col, True,
    ))


def _bounded_bytes(path: Path, maximum: int) -> bytes:
    if path.stat().st_size > maximum:
        raise ValueError("Input exceeds its bounded read budget")
    with path.open("rb") as handle:
        content = handle.read(maximum + 1)
    if len(content) > maximum:
        raise ValueError("Input exceeds its bounded read budget")
    return content


def _read_metadata(path: Path, time_col: str, measurement_type: str) -> list[dict[str, Any]]:
    reader = csv.DictReader(io.StringIO(_bounded_bytes(path, MAX_METADATA_BYTES).decode("utf-8")), strict=True)
    fields = reader.fieldnames or []
    if (not {"sample_id", time_col} <= set(fields) or len(set(fields)) != len(fields)
            or any(not field for field in fields)):
        raise ValueError("Metadata requires distinct sample_id and supplied numeric time columns")
    rows, seen, label_times = [], set(), {}
    for row in reader:
        sample = row.get("sample_id")
        if (None in row or any(value is None for value in row.values())
                or not sample or not sample.strip() or len(sample) > 200 or sample in seen):
            raise ValueError("Metadata rows require complete fields and distinct bounded sample IDs")
        try:
            coordinate = float(row[time_col])
        except (TypeError, ValueError, OverflowError):
            raise ValueError("Supplied time coordinates must be finite numeric values") from None
        if not math.isfinite(coordinate):
            raise ValueError("Supplied time coordinates must be finite numeric values")
        if "measurement_type" in row and row["measurement_type"] != measurement_type:
            raise ValueError("Metadata measurement declarations disagree with measurement_type")
        label = row.get("timepoint_label")
        if label:
            if label in label_times and label_times[label] != coordinate:
                raise ValueError("A chronological label cannot denote multiple numeric times")
            label_times[label] = coordinate
        for name in ("batch", "biological_unit_id", "specimen_id", "technical_replicate_id"):
            if len(row.get(name, "")) > 200:
                raise ValueError("Metadata composition labels must be bounded to 200 characters")
        seen.add(sample)
        rows.append(dict(row, **{time_col: coordinate}))
    coordinates = {row[time_col] for row in rows}
    if not MIN_TIMEPOINTS <= len(coordinates) <= MAX_TIMEPOINTS:
        raise ValueError("Observed summaries require 3..100 distinct supplied numeric times")
    return rows


def _validate_array_header(header, *, name: str, shape: tuple[int, ...],
                           measurement_type: str) -> int:
    """Pure bounded Zarr declaration check; returns declared decoded array bytes."""
    if (not isinstance(header, dict) or header.get("zarr_format") != 2
            or header.get("shape") != list(shape)
            or any(type(size) is not int or size < 1 for size in header.get("shape", []))):
        raise ValueError("Prepared Zarr dimensions must match the complete family and metadata")
    pattern = r"[<>=|]([iuf])([1248])" if name == "expression" else r"[<>=|](U)([0-9]+)"
    match = re.fullmatch(pattern, str(header.get("dtype", "")))
    if match is None:
        raise ValueError("Prepared Zarr requires fixed numeric expression and fixed Unicode labels")
    kind, width = match.group(1), int(match.group(2))
    if (kind == "f" and width not in {2, 4, 8}) or (kind == "U" and not 1 <= width <= 200):
        raise ValueError("Prepared Zarr dtype exceeds the supported numeric or label width")
    if name == "expression" and measurement_type == "raw_counts" and kind not in {"i", "u"}:
        raise ValueError("Prepared raw count storage must use an integer dtype")
    item_bytes = width * 4 if kind == "U" else width
    chunks = header.get("chunks")
    if (not isinstance(chunks, list) or len(chunks) != len(shape)
            or any(type(size) is not int or size < 1 for size in chunks)
            or math.prod(chunks) * item_bytes > MAX_CHUNK_BYTES):
        raise ValueError("Prepared Zarr decoded chunks exceed the 4 MiB budget")
    compressor = header.get("compressor")
    if (header.get("filters") not in (None, [])
            or (compressor is not None and (not isinstance(compressor, dict)
                                            or compressor.get("id") not in {"blosc", "zlib", "gzip"}))):
        raise ValueError("Prepared Zarr uses an unsupported codec or filter")
    return math.prod(shape) * item_bytes


def _preflight_store(path: Path, family: tuple[str, ...], sample_count: int,
                     measurement_type: str) -> None:
    if path.suffix != ".zarr":
        raise ValueError("Observed summaries require a prepared Zarr v2 expression store")
    if sample_count * 8 > MAX_CHUNK_BYTES:
        raise ValueError("A single expression row exceeds the 4 MiB read budget")
    if measurement_type == "raw_counts" and len(family) * sample_count * 8 > MAX_PANEL_BYTES:
        raise ValueError("The complete raw-count family exceeds the 16 MiB normalization panel; no sampling is allowed")
    from .zarr_bounds import validate_prepared_zarr

    validate_prepared_zarr(path, expected_shape=(len(family), sample_count),
                          measurement_type=measurement_type, max_chunk_bytes=MAX_CHUNK_BYTES,
                          max_metadata_bytes=MAX_METADATA_BYTES, max_matrix_bytes=MAX_MATRIX_BYTES)
    group = json.loads(_bounded_bytes(path / ".zgroup", MAX_HEADER_BYTES))
    if not isinstance(group, dict) or group.get("zarr_format") != 2:
        raise ValueError("Observed summaries require a prepared Zarr v2 group")
    label_bytes = 0
    for name, shape in (("expression", (len(family), sample_count)),
                        ("gene_names", (len(family),)), ("sample_names", (sample_count,))):
        header = json.loads(_bounded_bytes(path / name / ".zarray", MAX_HEADER_BYTES))
        decoded_bytes = _validate_array_header(header, name=name, shape=shape,
                                               measurement_type=measurement_type)
        if name != "expression":
            label_bytes += decoded_bytes
    if label_bytes > MAX_METADATA_BYTES:
        raise ValueError("Prepared gene and sample labels exceed the 5 MB metadata budget")


def _expression_blocks(store, measurement_type, np):
    block_size = max(1, min(500, MAX_CHUNK_BYTES // (8 * store.shape[1])))
    for names, source in store.iter_gene_chunks(block_size):
        values = np.asarray(source, dtype=float)
        if not np.isfinite(values).all():
            raise ValueError("nonfinite_expression: Expression observations must be finite")
        if measurement_type == "raw_counts" and (np.any(values < 0)
                or np.any(values != np.floor(values)) or np.any(values > 2**53 - 1)):
            raise ValueError("invalid_counts: Raw counts must be nonnegative integers <=2**53-1")
        yield names, values


def _summarize_worker(counts_path, metadata_path, gene_ids, measurement_type,
                      time_col="numeric_time", allow_data_execution=False):
    """Importable worker; direct accidental invocation also defaults to denial."""
    _require_data_execution(allow_data_execution)
    family = _validate_request(gene_ids, measurement_type, time_col)
    rows = _read_metadata(Path(metadata_path), time_col, measurement_type)
    _preflight_store(Path(counts_path), family, len(rows), measurement_type)
    import numpy as np
    from devharness_core.storage import ChunkedExpressionStore

    with ChunkedExpressionStore(counts_path) as store:
        if set(store.gene_names) != set(family) or len(store.gene_names) != len(family):
            raise ValueError("Expression genes must exactly match the complete declared family")
        by_sample = {row["sample_id"]: row for row in rows}
        if set(store.sample_names) != set(by_sample) or len(store.sample_names) != len(rows):
            raise ValueError("Expression columns and metadata must have exactly matching sample IDs")
        rows = [by_sample[sample] for sample in store.sample_names]
        coordinates = np.asarray([row[time_col] for row in rows], dtype=float)
        time_grid = sorted(set(coordinates.tolist()))
        positions = [np.flatnonzero(coordinates == coordinate) for coordinate in time_grid]
        composition = []
        for coordinate, selected in zip(time_grid, positions):
            batches, unknown = {}, 0
            for index in selected:
                batch = rows[int(index)].get("batch", "")
                if batch and batch.strip():
                    batches[batch] = batches.get(batch, 0) + 1
                else:
                    unknown += 1
            composition.append({"time": coordinate, "sample_count": len(selected),
                                "batch_counts": batches, "unknown_batch_count": unknown})

        factors = None
        normalization = {"method": "none_continuous_input", "scope": "unchanged_input_expression",
                         "transformation": "none"}
        if measurement_type == "raw_counts":
            from devharness_server.normalization import median_ratio_factors

            panel = np.empty(store.shape, dtype=float)
            offset = 0
            for names, values in _expression_blocks(store, measurement_type, np):
                panel[offset:offset + len(names)] = values
                offset += len(names)
            if offset != len(family):
                raise ValueError("The complete normalization family was not read")
            factors, normalization = median_ratio_factors(panel, store.gene_names)
            del panel
            if normalization.get("method") not in {"median_of_ratios", "median_of_ratios_poscounts"}:
                raise ValueError("Raw summaries require RLE; alternative normalization fallback is refused")
            if (factors.shape != (len(rows),) or not np.isfinite(factors).all()
                    or np.any(factors <= 0)):
                raise ValueError("RLE normalization requires finite positive factors for every observed library")
            normalization = dict(normalization, scope="entire_declared_gene_family",
                                 panel_selection="all_declared_genes", panel_gene_count=len(family),
                                 gene_ids=list(store.gene_names), panel_bytes=len(family) * len(rows) * 8,
                                 max_panel_bytes=MAX_PANEL_BYTES)

        genes = {}
        for names, values in _expression_blocks(store, measurement_type, np):
            with np.errstate(over="ignore", invalid="ignore", divide="ignore"):
                if factors is not None:
                    values = values / factors
                means = np.column_stack([values[:, selected].mean(axis=1) for selected in positions])
            for gene, curve in zip(names, means):
                row = {"status": "described", "missing_reason": "descriptive_summary_no_hypothesis_test",
                       "mean_curve": [], "effect": None, "effect_units": None}
                if not np.isfinite(curve).all():
                    row.update(status="no_information", missing_reason="nonfinite_observed_mean",
                               effect_missing_reason="nonfinite_observed_mean")
                else:
                    row["mean_curve"] = curve.tolist()
                    if measurement_type == "raw_counts":
                        if curve[0] <= 0 or curve[-1] <= 0:
                            row["effect_missing_reason"] = "zero_endpoint_mean"
                        else:
                            effect = math.log2(float(curve[-1])) - math.log2(float(curve[0]))
                            row.update(effect=effect, effect_units="log2_normalized_mean_change")
                    else:
                        effect = float(curve[-1]) - float(curve[0])
                        if math.isfinite(effect):
                            row.update(effect=effect, effect_units="expression_difference")
                        else:
                            row["effect_missing_reason"] = "nonfinite_endpoint_difference"
                genes[gene] = row
        if set(genes) != set(family):
            raise ValueError("Summary did not account for the complete declared family")

        limitations = [
            "Describes only the supplied observed subset; biological-unit provenance is unverified and population replication is not established.",
            "Libraries receive equal weight within each time; technical replicates are not independent biological replicates.",
            "Each time retains its observed batch mixture; there is no batch adjustment or causal contrast.",
            "No model fitting, hypothesis tests, significance claims or confidence intervals are produced.",
        ]
        if any(row["unknown_batch_count"] for row in composition):
            limitations.append("Batch labels are missing for some observed libraries; their batch composition is unknown.")
        result = {
            "record_format": "devbench.study.descriptive-summary/1", "method": "observed_expression_summary",
            "measurement_type": measurement_type, "inference_scope": "descriptive",
            "biological_unit_provenance": "unverified", "curve_kind": "arithmetic_mean_of_observed_libraries",
            "reference": {"reference_batch": None, "library_factor": 1.0 if factors is not None else None,
                          "batch_reference_policy": "observed_batch_mix", "biological_unit_reference": "observed_subset"},
            "reference_description": "Arithmetic means over all supplied libraries at each numeric time, including that time's observed batch mixture; no batch adjustment or population extrapolation.",
            "time_col": time_col, "time_grid": time_grid, "sample_count": len(rows),
            "sample_counts": [row["sample_count"] for row in composition], "time_composition": composition,
            "family_size": len(family), "hypothesis_family": list(family),
            "normalization": normalization,
            "size_factors": dict(zip(store.sample_names, factors.tolist())) if factors is not None else None,
            "genes": {gene: genes[gene] for gene in family}, "limitations": limitations,
        }
        if len(json.dumps(result, allow_nan=False, separators=(",", ":")).encode("utf-8")) > MAX_RESULT_BYTES:
            raise ValueError("Observed summary exceeds the 8 MB evidence budget; no truncation is allowed")
        return result
