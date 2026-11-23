"""Observable metadata admission for the current independent-observation model.

This module has no access to generator truth or scenario names. Source-backed
unit provenance is supplied by the trusted coordinator; unique strings alone
never establish biological independence.
"""
from collections import Counter, defaultdict
import csv
import hashlib
import io
import math
from pathlib import Path

from devharness_core.units import BIOLOGICAL_UNIT_COLUMNS, UNKNOWN_UNIT_MARKERS
from .evidence import _read


def inspect_admission(metadata_path, *, unit_provenance_verified, time_col="numeric_time",
                      biological_unit_column="biological_unit_id", allow_data_execution=False):
    if allow_data_execution is not True:
        raise PermissionError("Metadata analysis is disabled during plan preparation")
    if type(unit_provenance_verified) is not bool:
        raise TypeError("Unit provenance must be explicitly verified by the coordinator")
    payload = _read(Path(metadata_path), 5_000_000)
    reader = csv.DictReader(io.StringIO(payload.decode("utf-8-sig")), strict=True)
    names = reader.fieldnames or []
    if len(names) != len(set(names)) or len(names) > 100:
        raise ValueError("Metadata columns must be distinct and bounded")
    rows = []
    for row in reader:
        if len(rows) >= 100_000 or None in row or any(value is None for value in row.values()):
            raise ValueError("Metadata rows are malformed or exceed their bound")
        rows.append(row)
    blocked, warnings, counts = [], [], {}
    if not rows or any(key not in names for key in ("sample_id", time_col, "batch", biological_unit_column)):
        blocked.append("missing_required_sampling_metadata")
    else:
        sample_ids = [row["sample_id"].strip() for row in rows]
        if not all(sample_ids) or len(set(sample_ids)) != len(rows):
            blocked.append("invalid_or_duplicate_sample_ids")
        repeated = False
        for key in dict.fromkeys((biological_unit_column, *BIOLOGICAL_UNIT_COLUMNS)):
            if key not in names:
                continue
            values = [row[key].strip() for row in rows]
            if any(value.lower() in UNKNOWN_UNIT_MARKERS for value in values):
                blocked.append("incomplete_biological_unit_identity")
                continue
            counts[key] = len(set(values))
            repeated |= len(set(values)) < len(values)
        if counts and min(counts.values()) < 2:
            blocked.append("insufficient_independent_biological_units")
        if repeated:
            warnings.append("technical_replicates_not_independent")
            blocked.append("repeated_unit_model_required")
        batch_times, allocation = defaultdict(set), Counter()
        times = []
        for row in rows:
            try:
                value = float(row[time_col])
                if not math.isfinite(value):
                    raise ValueError("nonfinite")
            except (TypeError, ValueError, OverflowError):
                blocked.append("invalid_numeric_time")
                break
            batch = row["batch"].strip()
            if not batch or batch.lower() in UNKNOWN_UNIT_MARKERS:
                blocked.append("missing_batch_identity")
            times.append(value)
            batch_times[batch].add(value)
            allocation[(value, batch)] += 1
        if times and len(times) == len(rows):
            if len(set(times)) < 3:
                blocked.append("insufficient_distinct_times")
            elif len(batch_times) > 1 and all(len(values) == 1 for values in batch_times.values()):
                blocked.append("time_batch_complete_aliasing")
            elif len(set(allocation.values())) > 1 or len(allocation) < len(set(times)) * len(batch_times):
                warnings.append("partial_time_batch_confounding")
    if not unit_provenance_verified:
        blocked.append("biological_unit_provenance_unverified")
    return {"record_format": "devbench.study.observable-admission/1",
            "metadata_sha256": hashlib.sha256(payload).hexdigest(),
            "population_inference_allowed": not blocked,
            "blocking_diagnostics": sorted(set(blocked)), "diagnostics": sorted(set(blocked + warnings)),
            "sample_count": len(rows), "declared_unit_counts": counts,
            "unit_provenance_verified": unit_provenance_verified,
            "time_col": time_col, "biological_unit_column": biological_unit_column,
            "scope": "current_independent_observation_temporal_model",
            "limitations": ["No private truth was used", "A permitted design still requires a numerical rank check",
                "Repeated units may support other models; this toolkit does not supply a validated clustered model",
                "Metadata declarations do not establish clinical or causal validity"]}
