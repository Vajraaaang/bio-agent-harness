"""Bounded scientific comparisons of immutable single-file method outputs."""
from __future__ import annotations

from pathlib import Path
from bisect import bisect_left, bisect_right
from collections.abc import Mapping
import json
import math
import hashlib
from decimal import Decimal, localcontext

import numpy as np

from devharness_core.provenance import canonical_json, hash_artifact
from devharness_core.methods import (MethodContract, OBSERVATION_SELECTION_INVARIANTS,
                                    DIAGNOSTIC_INVARIANTS, DIAGNOSTIC_OUTPUT_FORMAT, diagnostic_reasons,
                                    DIAGNOSTIC_PROFILE_OUTPUT_FORMAT, validate_diagnostic_policy)


MAX_DATASET_BYTES = 100_000_000
MAX_ARRAY_VALUES = 5_000_000
_COMPARISON_ORIGIN = "devharness.method_validation/1"
_RETENTION_PREREQUISITES = ("observation_ids_unique", "retained_observation_ids_subset_of_input",
                          "variables_unchanged", "retained_count_matrix_unchanged", "sample_assignments_unchanged")


def coordinator_measurement_provenance(validation, field):
    """Admit known measurement bases from a verified coordinator run receipt.

    Candidate artifacts never supply this envelope. A recognized origin records
    independence from candidate self-reports, not scientific reference validity.
    Legacy diagnostics without this provenance remain observable but unqualified.
    """
    manifest = validation.get("comparison_measurement_provenance", {})
    record = manifest.get(field) if isinstance(manifest, dict) else None
    if (not isinstance(record, dict) or record.get("origin") != _COMPARISON_ORIGIN
            or record.get("raw_counts_recomputed") is not False or validation.get("passed") is not True):
        return None
    basis = record.get("basis")
    if basis == "immutable_input_annotations_by_retained_ids":
        reference = record.get("reference")
        if (validation.get("comparator") != "single_cell_qc" or not isinstance(reference, dict)
                or set(reference) != {"input", "annotation", "counts_layer"}
                or reference["input"] != "dataset" or not isinstance(reference["annotation"], str)
                or not isinstance(reference["counts_layer"], str)):
            return None
        escaped = reference["annotation"].replace("~", "~0").replace("/", "~1")
        if field not in {"/reference_qc_quantiles/" + escaped + "/after/" + str(index) for index in range(5)}:
            return None
        if any(validation.get("invariants", {}).get(name) is not True for name in _RETENTION_PREREQUISITES[:-1]):
            return None
        required = _RETENTION_PREREQUISITES
    elif basis == "immutable_observation_rows_by_retained_ids":
        reference = record.get("reference")
        invariants = validation.get("invariants")
        if (validation.get("comparator") != "observation_selection"
                or set(record) != {"origin", "basis", "reference", "raw_counts_recomputed", "prerequisites"}
                or not isinstance(reference, dict) or set(reference) != {"input", "measurement"}
                or reference["input"] != "dataset" or not _observation_text(reference["measurement"])
                or not isinstance(invariants, dict)
                or any(invariants.get(name) is not True for name in OBSERVATION_SELECTION_INVARIANTS)):
            return None
        metric = reference["measurement"]
        escaped = metric.replace("~", "~0").replace("/", "~1")
        quantile_fields = {"/reference_qc_quantiles/" + escaped + "/after/" + str(index): index for index in range(5)}
        mean_fields = {"/reference_qc_means/" + escaped + "/" + name: name
                       for name in ("after", "absolute_standardized_shift")}
        try:
            retained = validation["shape"]["after"][0]
            if type(retained) is not int or not 0 < retained <= 10_000:
                return None
            if field in quantile_fields:
                values = validation["reference_qc_quantiles"][metric]["after"]
                if not isinstance(values, list) or len(values) != 5 or not all(_finite_number(value) for value in values):
                    return None
            elif field in mean_fields:
                values = validation["reference_qc_means"][metric]
                if (not isinstance(values, dict) or set(values) != {"before", "after", "absolute_standardized_shift"}
                        or not all(_finite_number(value) for value in values.values())
                        or values["absolute_standardized_shift"] < 0):
                    return None
            else:
                return None
        except (KeyError, TypeError, IndexError):
            return None
        required = OBSERVATION_SELECTION_INVARIANTS
    elif basis == "immutable_input_reference_comparison":
        # Trusted numerical comparators may compare immutable reference values
        # against outputs; structural JSON validation alone never emits this.
        if validation.get("comparator") != "json" or not isinstance(record.get("reference"), str) or not record["reference"]:
            return None
        required = ("immutable_input_reference_loaded", "finite_comparison_values")
    else:
        return None
    prerequisites = record.get("prerequisites")
    if (not isinstance(prerequisites, dict) or set(prerequisites) != set(required)
            or any(prerequisites[name] is not True for name in required)):
        return None
    return record


def finite_json(path):
    path = Path(path)
    if not path.is_absolute():
        raise ValueError("An absolute artifact path is required")
    hash_artifact(path, max_bytes=16_000_000, max_entries=1)
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate JSON key")
            result[key] = value
        return result
    value = json.loads(path.read_bytes(), object_pairs_hook=unique)
    canonical_json(value)
    return value


def _observation_text(value):
    return isinstance(value, str) and 0 < len(value) <= 4096 and "\x00" not in value


def _finite_number(value):
    try:
        return isinstance(value, (int, float, np.integer, np.floating)) and not isinstance(value, bool) and math.isfinite(value)
    except OverflowError:
        return False


def _diagnostic_input(path, policy):
    before = finite_json(path)
    if (not isinstance(before, dict) or set(before) != {"format", "observations"}
            or before["format"] != "devbench.qc-observations/1"
            or not isinstance(before["observations"], list) or not 0 < len(before["observations"]) <= 10_000):
        raise ValueError("Expected a bounded devbench.qc-observations/1 dataset")
    rows = before["observations"]
    if any(not isinstance(row, dict) for row in rows):
        raise ValueError("Observations must be JSON objects")
    columns = set(rows[0])
    metadata = {"cell_id", "sample", "group", policy.get("sample_key")}
    measurements = columns - metadata
    if ("cell_id" not in columns or not 0 < len(measurements) <= 128
            or any(not _observation_text(name) for name in columns)
            or any(set(row) != columns for row in rows)
            or not set(policy.get("qc_metrics", measurements)) <= measurements):
        raise ValueError("Observation columns and declared measurements must be consistent and bounded")
    if policy.get("sample_key") and policy["sample_key"] not in columns:
        raise ValueError("Declared sample column is missing")
    for row in rows:
        if (any(not _observation_text(row[name]) for name in columns & metadata)
                or any(not _finite_number(row[name]) for name in measurements)):
            raise ValueError("Observation identities must be strings and measurements finite numbers")
    ids = [row["cell_id"] for row in rows]
    if len(set(ids)) != len(ids):
        raise ValueError("Input observation IDs must be unique")
    return rows, sorted(measurements)


def observation_prerequisite_inspection(path, requested_fields, *, policy=None, artifact_type="json"):
    """Inspect bounded numeric facts, retaining errors rather than inventing evidence."""
    allowed = {"input_integrity_valid", "observation_ids_unique", "metrics_finite",
               "measurement_variance_positive", "measurement_rank"}
    if not isinstance(requested_fields, (list, tuple)) or not set(requested_fields) <= allowed:
        raise ValueError("Unsupported computable prerequisite inspection")
    policy = policy or {}
    record = {"format": "devbench.prerequisite-inspection/1", "observed": {}, "failures": [],
              "input_policy": policy, "input_artifact_type": artifact_type}
    try:
        if (not isinstance(policy, dict) or set(policy) - {"sample_key", "qc_metrics", "counts_layer"}
                or any(not _observation_text(policy[key]) for key in ("sample_key", "counts_layer") if key in policy)
                or ("qc_metrics" in policy and (not isinstance(policy["qc_metrics"], list)
                    or len(policy["qc_metrics"]) > 128 or any(not _observation_text(value) for value in policy["qc_metrics"])
                    or len(set(policy["qc_metrics"])) != len(policy["qc_metrics"])))):
            raise ValueError("Inspection needs bounded explicitly declared observation metadata roles")
        maximum = MAX_DATASET_BYTES if artifact_type == "anndata" else 16_000_000
        record["input_sha256"] = hash_artifact(path, max_bytes=maximum, max_entries=1)["sha256"]
        matrix = None
        if artifact_type == "json":
            rows, measurements = _diagnostic_input(path, policy)
            if "measurement_variance_positive" in requested_fields or "measurement_rank" in requested_fields:
                if len(rows) * len(measurements) > 100_000:
                    raise ValueError("Numerical prerequisite inspection exceeds the 100000-value computation bound")
                matrix = np.asarray([[row[name] for name in measurements] for row in rows], dtype=float)
        elif artifact_type == "anndata":
            data = _dataset(path)
            if not 0 < data.n_obs <= 10_000 or not data.n_vars or not data.var_names.is_unique or not data.obs_names.is_unique:
                raise ValueError("AnnData prerequisite identities/dimensions are invalid or exceed inspection bounds")
            if any(not _observation_text(value) for value in data.obs_names):
                raise ValueError("AnnData observation identities must be bounded strings")
            layer = policy.get("counts_layer")
            if not isinstance(layer, str):
                raise ValueError("AnnData prerequisite inspection requires a declared count representation")
            values = data.X if layer == "X" else data.layers.get(layer)
            if values is None or not _finite_matrix(values):
                raise ValueError("Declared AnnData count representation must be present, nonnegative and finite")
            sample_key = policy.get("sample_key")
            if sample_key and (sample_key not in data.obs or any(not _observation_text(value) for value in data.obs[sample_key])):
                raise ValueError("Declared AnnData sample identities are missing or invalid")
            metrics = policy.get("qc_metrics", [])
            if len(metrics) > 128 or any(name not in data.obs or any(not _finite_number(value) for value in data.obs[name]) for name in metrics):
                raise ValueError("Declared AnnData QC measurements must be present, bounded and finite")
            if "measurement_variance_positive" in requested_fields or "measurement_rank" in requested_fields:
                columns = len(metrics) if metrics else data.n_vars
                if data.n_obs * columns > 100_000:
                    raise ValueError("Numerical prerequisite inspection exceeds the 100000-value computation bound")
                matrix = data.obs[metrics].to_numpy(dtype=float) if metrics else _array(values)
        else:
            raise ValueError("Unsupported artifact type for bounded numerical prerequisite inspection")
        facts = {"input_integrity_valid": True, "observation_ids_unique": True, "metrics_finite": True}
        if matrix is not None:
            # Scale first to avoid overflow for finite but very large input values.
            scale = np.max(np.abs(matrix), axis=0)
            scaled = matrix / np.where(scale > 0, scale, 1)
            facts["measurement_variance_positive"] = bool(np.all(np.var(scaled, axis=0) > 0))
            facts["measurement_rank"] = int(np.linalg.matrix_rank(scaled))
        record["observed"] = {field: facts[field] for field in requested_fields}
    except (ValueError, OSError, TypeError, np.linalg.LinAlgError) as error:
        record["failures"].append(f"{type(error).__name__}: {error}"[:1000])
    return record


def compute_diagnostic_profiles(before_path, policy):
    """Recompute bounded, declared arithmetic without categorical or causal inference."""
    declared = validate_diagnostic_policy(policy.get("diagnostic_policy", {}))
    if declared["format"] != "devbench.diagnostic-policy/2":
        raise ValueError("Profile arithmetic requires an explicit version 2 diagnostic policy")
    profile = declared["profile"]
    rows, _ = _diagnostic_input(before_path, policy)
    names, groups = profile["measurements"], profile["group_by"]
    if (names != policy.get("qc_metrics") or len(rows) * len(names) > 100_000
            or not set(groups) <= {"sample", "group", policy.get("sample_key")}
            or not set(groups) <= rows[0].keys()):
        raise ValueError("Profile measurements/reference roles must be explicit and within the arithmetic bound")
    references = {}
    for row in rows:
        key = tuple(row[name] for name in groups)
        references.setdefault(key, []).append(row)
    def number(value):
        return Decimal(value) if isinstance(value, int) else Decimal.from_float(float(value))
    def median(values):
        ordered = sorted(values)
        middle = len(ordered) // 2
        return ordered[middle] if len(ordered) % 2 else (ordered[middle - 1] + ordered[middle]) / 2
    records = []
    # Decimal avoids converting tiny positive MADs to zero when the same
    # reference contains very large finite observations. Float serialization
    # remains bounded; genuinely overflowing ratios retain an absence reason.
    with localcontext() as context:
        context.prec = 1600
        calculations = {}
        for key, members in references.items():
            for name in names:
                values = [number(row[name]) for row in members]
                center = median(values)
                calculations[key, name] = (sorted(values), center, median([abs(value - center) for value in values]))
        for row in rows:
            key = tuple(row[name] for name in groups)
            profiles = {}
            for name in names:
                basis, center, mad = calculations[key, name]
                value, reason = number(row[name]), None
                if profile["family"] == "empirical_two_tail":
                    midrank = (bisect_left(basis, value) + bisect_right(basis, value)) / (2 * len(basis))
                    score = 1 - 2 * min(midrank, 1 - midrank)
                elif mad == 0:
                    score, reason = None, "Zero MAD in reference"
                else:
                    exact_score = abs(value - center) / mad
                    score = float(exact_score)
                    if not _finite_number(score):
                        score, reason = None, "Nonfinite score calculation"
                    elif exact_score > 0 and score == 0:
                        score, reason = None, "Score below floating representation"
                profiles[name] = {"score": score, "reason": reason, "reference_count": len(references[key]),
                                  "reference_group": dict(zip(groups, key))}
            aggregate = max(p["score"] for p in profiles.values()) if all(p["score"] is not None for p in profiles.values()) else None
            records.append({"observation_id": row["cell_id"], "score": aggregate,
                            "score_reason": None if aggregate is not None else "Profile score unavailable", "profiles": profiles})
    return records


def _matching_score(actual, expected):
    return actual is None if expected is None else _finite_number(actual) and math.isclose(actual, expected, rel_tol=1e-9, abs_tol=0.)


def _diagnostic_profile_preview(before_path, after_path, policy, rows, measurements, output):
    declared = validate_diagnostic_policy(policy["diagnostic_policy"])
    scope, profile = declared["interpretation"], declared["profile"]
    expected = compute_diagnostic_profiles(before_path, policy)
    ids = [row["cell_id"] for row in rows]
    digest = hash_artifact(before_path, max_bytes=16_000_000, max_entries=1)["sha256"]
    if (not isinstance(output, dict) or set(output) != {"format", "input_sha256", "evidence_scope", "retained_ids", "records"}
            or output["format"] != DIAGNOSTIC_PROFILE_OUTPUT_FORMAT or output["input_sha256"] != digest
            or output["evidence_scope"] != scope or output["retained_ids"] != ids
            or not isinstance(output["records"], list) or len(output["records"]) != len(ids)):
        raise ValueError("Version 2 diagnostic output must bind and retain every immutable input in original order")
    if scope == "qc_review" and not set(policy["qc_metrics"]) <= declared["measurement_semantics"].keys():
        raise ValueError("QC interpretation requires coordinator measurement semantics")
    flagged, unavailable, deferred, available = [], [], [], []
    decided = set()
    for index, (actual, computed) in enumerate(zip(output["records"], expected)):
        if (not isinstance(actual, dict) or set(actual) != {"observation_id", "score", "score_reason", "flag", "flag_reason", "profiles", "evidence_refs"}
                or actual["observation_id"] != ids[index] or actual["score_reason"] != computed["score_reason"]
                or not _matching_score(actual["score"], computed["score"])
                or not isinstance(actual["profiles"], dict) or set(actual["profiles"]) != set(profile["measurements"])):
            raise ValueError("Diagnostic profile aggregate/coverage must match independent arithmetic")
        for name, reference in computed["profiles"].items():
            supplied = actual["profiles"][name]
            if (not isinstance(supplied, dict) or set(supplied) != set(reference)
                    or not _matching_score(supplied["score"], reference["score"])
                    or supplied["reason"] != reference["reason"]
                    or type(supplied["reference_count"]) is not int or supplied["reference_count"] != reference["reference_count"]
                    or supplied["reference_group"] != reference["reference_group"]):
                raise ValueError("Per-measurement profile must match immutable-input reference arithmetic")
        threshold, score = profile["flag_threshold"], computed["score"]
        flag = None if threshold is None or score is None else score >= threshold
        reported_flag = None if threshold is None or actual["score"] is None else actual["score"] >= threshold
        reason = ("Flag threshold unresolved" if threshold is None else "Profile score unavailable" if score is None
                  else diagnostic_reasons(scope, "flagged" if flag else "not_flagged")[0])
        if actual["flag"] is not flag or actual["flag"] is not reported_flag or actual["flag_reason"] != reason:
            raise ValueError("Diagnostic flag decision must match its frozen rule or explicit deferral")
        valid_refs = [f"/observations/{index}/" + name.replace("~", "~0").replace("/", "~1") for name in profile["measurements"]]
        refs = actual["evidence_refs"]
        if not isinstance(refs, list) or any(not isinstance(ref, str) for ref in refs) or len(refs) != len(valid_refs) or set(refs) != set(valid_refs):
            raise ValueError("Every profile must cite its declared immutable observation measurement")
        (available if score is not None else unavailable).append(ids[index])
        if flag is None:
            deferred.append(ids[index])
        else:
            decided.add(ids[index])
            if flag:
                flagged.append(ids[index])
    def summary(values):
        finite = [value for value in values if value is not None]
        scale = max(max(map(abs, finite)), 1.) if finite else 1.
        return {"available_fraction": len(finite) / len(values),
                "mean_score": float(np.mean(np.asarray(finite) / scale)) * scale if finite else None,
                "max_score": max(finite) if finite else None,
                "unavailable_reason": None if finite else "No computable profile scores"}
    aggregate = summary([record["score"] for record in expected])
    profile_metrics = {"aggregate_mean": aggregate["mean_score"], "aggregate_max": aggregate["max_score"],
                       "aggregate_unavailable_reason": aggregate["unavailable_reason"],
                       "by_measurement": {name: summary([record["profiles"][name]["score"] for record in expected])
                                          for name in profile["measurements"]}}
    flagged_set = set(flagged)
    groups = {}
    for key in dict.fromkeys((policy.get("sample_key", "sample"), "group")):
        if key in rows[0]:
            groups[key] = {}
            for label in sorted({row[key] for row in rows}):
                group_decided = [row["cell_id"] for row in rows if row[key] == label and row["cell_id"] in decided]
                groups[key][label] = sum(identity in flagged_set for identity in group_decided) / len(group_decided) if group_decided else None
    return {"format": "devharness.scientific-preview/1", "comparator": "observation_diagnostic", "passed": True,
            "diagnostic_output_format": DIAGNOSTIC_PROFILE_OUTPUT_FORMAT,
            "input_sha256": digest, "output_sha256": hash_artifact(after_path, max_bytes=16_000_000, max_entries=1)["sha256"],
            "evidence_scope": scope, "retained_ids": ids, "flagged_ids": flagged, "unavailable_ids": unavailable,
            "score_available_ids": available, "deferred_flag_ids": deferred, "diagnostic_profiles": expected,
            "profile_metrics": profile_metrics,
            "arithmetic_fidelity": {"verified": True, "family": profile["family"], "profile_sha256": hashlib.sha256(canonical_json(profile)).hexdigest(),
                                    "input_sha256": digest, "scope": "Declared numerical calculation only"},
            "shape": {"before": [len(rows), len(measurements)], "after": [len(rows), len(measurements)]},
            "removed_ids": [], "added_ids": [], "invariants": dict.fromkeys(DIAGNOSTIC_INVARIANTS, True), "warnings": [],
            "diagnostic_measurements": {"available_fraction": len(available) / len(rows), "decision_fraction": len(decided) / len(rows),
                                        "flag_rate": len(flagged) / len(decided) if decided else None, "by_measurement": {}},
            "group_flag_rates": groups, "diagnostic_stability": {"value": None, "reason": "No matched perturbation outputs were supplied"},
            "comparison_measurement_provenance": {},
            "limitations": ["Arithmetic fidelity verifies the declared formula, not scientific validity or biological truth",
                            "Deferred flags are unresolved classifications, not normality or unavailable numerical data",
                            "Full immutable input retention; diagnostic validation does not authorize exclusion or establish a comparative winner"]}


def observation_diagnostic_preview(before_path, after_path, policy):
    """Validate full-coverage flags; measurements come only from immutable inputs."""
    rows, measurements = _diagnostic_input(before_path, policy)
    output = finite_json(after_path)
    declared = policy.get("diagnostic_policy", {})
    if declared.get("format") == "devbench.diagnostic-policy/2":
        return _diagnostic_profile_preview(before_path, after_path, policy, rows, measurements, output)
    scope = declared.get("interpretation")
    if (scope not in {"numerical_atypicality", "qc_review"}
            or declared.get("format") != "devbench.diagnostic-policy/1"):
        raise ValueError("An explicit diagnostic policy is required")
    if scope == "qc_review" and not set(policy["qc_metrics"]) <= declared.get("measurement_semantics", {}).keys():
        raise ValueError("QC interpretation requires coordinator measurement semantics")
    digest = hash_artifact(before_path, max_bytes=16_000_000, max_entries=1)["sha256"]
    ids = [row["cell_id"] for row in rows]
    if (not isinstance(output, dict)
            or set(output) != {"format", "input_sha256", "evidence_scope", "retained_ids", "records"}
            or output["format"] != DIAGNOSTIC_OUTPUT_FORMAT or output["input_sha256"] != digest
            or output["evidence_scope"] != scope or output["retained_ids"] != ids
            or not isinstance(output["records"], list) or len(output["records"]) != len(rows)):
        raise ValueError("Diagnostic output must bind and retain every immutable input in original order")
    flagged, unavailable = [], []
    for index, record in enumerate(output["records"]):
        if (not isinstance(record, dict)
                or set(record) != {"observation_id", "status", "score", "reason", "evidence_refs"}
                or record["observation_id"] != ids[index]
                or record["status"] not in {"flagged", "not_flagged", "unavailable"}
                or not _observation_text(record["reason"]) or not record["reason"].strip()):
            raise ValueError("Diagnostic records require exact ordered IDs, bounded reasons and valid statuses")
        if record["reason"] not in diagnostic_reasons(scope, record["status"]):
            raise ValueError("Diagnostic reason must use canonical language for its status and evidence scope; damage/causation assertions are inadmissible")
        if record["status"] == "unavailable":
            if record["score"] is not None:
                raise ValueError("Unavailable diagnostics require null scores and a reason")
            unavailable.append(ids[index])
        elif not _finite_number(record["score"]):
            raise ValueError("Available diagnostic scores must be finite numbers")
        refs = record["evidence_refs"]
        if (not isinstance(refs, list) or len(refs) > 16 or any(not isinstance(ref, str) for ref in refs)
                or len(set(refs)) != len(refs)
                or (record["status"] != "unavailable" and not refs)):
            raise ValueError("Available diagnostics require bounded unique evidence references")
        for reference in refs:
            valid_refs = {f"/observations/{index}/" + name.replace("~", "~0").replace("/", "~1")
                          for name in policy["qc_metrics"]}
            if not isinstance(reference, str) or reference not in valid_refs:
                raise ValueError("Diagnostic evidence must resolve a declared measurement in the immutable observation")
        if record["status"] == "flagged":
            flagged.append(ids[index])
    flagged_set = set(flagged)
    metrics = {"flag_rate": len(flagged) / len(rows), "available_fraction": 1 - len(unavailable) / len(rows),
               "by_measurement": {}}
    for name in policy["qc_metrics"]:
        values = np.asarray([row[name] for row in rows], dtype=float)
        scale = max(float(np.max(np.abs(values))), 1.)
        scaled = values / scale
        selected = np.asarray([row[name] / scale for row in rows if row["cell_id"] in flagged_set])
        before_mean = float(np.mean(scaled)) * scale
        flagged_mean = float(np.mean(selected)) * scale if len(selected) else None
        spread = float(np.std(scaled))
        shift = abs(float(np.mean(selected)) - float(np.mean(scaled))) / spread if len(selected) and spread > 0 else None
        metrics["by_measurement"][name] = {"input_mean": before_mean, "flagged_mean": flagged_mean,
                         "absolute_standardized_flagged_shift": shift,
                         "unavailable_reason": None if shift is not None else "No flagged observations or no input variance"}
    groups = {}
    for key in dict.fromkeys((policy.get("sample_key", "sample"), "group")):
        if key in rows[0]:
            groups[key] = {label: sum(row["cell_id"] in flagged_set for row in rows if row[key] == label)
                          / sum(row[key] == label for row in rows) for label in sorted({row[key] for row in rows})}
    invariants = dict.fromkeys(DIAGNOSTIC_INVARIANTS, True)
    return {"format": "devharness.scientific-preview/1", "comparator": "observation_diagnostic", "passed": True,
            "input_sha256": digest, "output_sha256": hash_artifact(after_path, max_bytes=16_000_000, max_entries=1)["sha256"],
            "evidence_scope": scope, "retained_ids": ids, "flagged_ids": flagged, "unavailable_ids": unavailable,
            "shape": {"before": [len(rows), len(measurements)], "after": [len(rows), len(measurements)]},
            "removed_ids": [], "added_ids": [], "invariants": invariants, "warnings": [],
            "diagnostic_measurements": metrics, "group_flag_rates": groups,
            "diagnostic_stability": {"value": None, "reason": "No matched perturbation outputs were supplied"},
            "comparison_measurement_provenance": {},
            "limitations": ["Flags are candidate predictions, not biological truth", "Full immutable input retention",
                            "Descriptive flag rates and shifts do not establish detection accuracy or method robustness",
                            "Diagnostic validation does not authorize exclusion or establish a comparative winner"]}


def extract_observation_diagnostics(before_path, after_path, policy):
    """Return actual flags only after exact-input-bound output validation."""
    preview = observation_diagnostic_preview(before_path, after_path, policy)
    extra = {key: preview[key] for key in ("diagnostic_output_format", "score_available_ids", "deferred_flag_ids",
                                         "diagnostic_profiles", "profile_metrics", "arithmetic_fidelity") if key in preview}
    return {**extra, **{key: preview[key] for key in ("input_sha256", "output_sha256", "evidence_scope", "retained_ids",
                                           "flagged_ids", "unavailable_ids")},
            "independent_diagnostics": {"measurements": preview["diagnostic_measurements"],
                                        "group_flag_rates": preview["group_flag_rates"],
                                        "stability": preview["diagnostic_stability"]}}


def observation_selection_preview(before_path, after_path, policy):
    """Select immutable measurement rows; never accept candidate measurements."""
    before, after = finite_json(before_path), finite_json(after_path)
    if (not isinstance(before, dict) or set(before) != {"format", "observations"}
            or before["format"] != "devbench.qc-observations/1"
            or not isinstance(before["observations"], list) or not 0 < len(before["observations"]) <= 10_000):
        raise ValueError("Expected a bounded devbench.qc-observations/1 dataset")
    rows = before["observations"]
    if not all(isinstance(row, dict) for row in rows):
        raise ValueError("Observations must be JSON objects")
    columns = set(rows[0])
    sample_key = policy.get("sample_key")
    metadata = {"cell_id", "sample", "group", sample_key}
    measurements = columns - metadata
    if ("cell_id" not in columns or not 0 < len(measurements) <= 128
            or not all(_observation_text(name) for name in columns)
            or any(set(row) != columns for row in rows)):
        raise ValueError("Observation columns must be consistent, named and bounded")
    if sample_key and sample_key not in columns:
        raise ValueError("Declared sample column is missing")
    metrics = policy["qc_metrics"]
    if not metrics or not set(metrics) <= measurements:
        raise ValueError("Declared QC measurement column is missing")
    for row in rows:
        if (any(not _observation_text(row[name]) for name in columns & metadata)
                or any(not _finite_number(row[name]) for name in measurements)):
            raise ValueError("Observation identities must be strings and measurements finite numbers")
    old_ids = [row["cell_id"] for row in rows]
    if len(set(old_ids)) != len(old_ids):
        raise ValueError("Input observation IDs must be unique")
    if (not isinstance(after, dict) or set(after) != {"retained_ids"}
            or not isinstance(after["retained_ids"], list)
            or not all(_observation_text(value) for value in after["retained_ids"])):
        raise ValueError("Candidate output must contain only retained_ids")
    retained_ids = after["retained_ids"]
    positions = {value: index for index, value in enumerate(old_ids)}
    retained_set = set(retained_ids)
    if len(retained_set) != len(retained_ids) or not retained_set <= positions.keys():
        raise ValueError("Retained IDs must be a unique subset of the input IDs")
    indices = [positions[value] for value in retained_ids]
    if indices != sorted(indices):
        raise ValueError("Retained IDs must preserve input order")
    # Candidates provide no rows or values: reconstruction makes preservation
    # unconditional once unique, ordered input IDs have been established.
    retained = [rows[index] for index in indices]
    invariants = dict.fromkeys(OBSERVATION_SELECTION_INVARIANTS, True)
    warnings, samples, quantiles, means, provenance = [], {}, {}, {}, {}
    if sample_key:
        for label in sorted({row[sample_key] for row in rows}):
            count = sum(row[sample_key] == label for row in rows)
            kept = sum(row[sample_key] == label for row in retained)
            samples[label] = {"before": count, "retained": kept, "removed": count - kept,
                              "removal_rate": (count - kept) / count}
        rates = [row["removal_rate"] for row in samples.values()]
        if max(rates) - min(rates) > .2 or any(row["retained"] == 0 for row in samples.values()):
            warnings.append("Sample retention differs substantially; review possible selection bias")
    if not retained:
        warnings.append("The candidate contains no observations; comparison measurements are unavailable")
    else:
        for metric in metrics:
            values = np.asarray([row[metric] for row in rows], dtype=float)
            # Scaling avoids intermediate overflow for otherwise finite inputs.
            scale = float(np.max(np.abs(values))) or 1.0
            scaled = values / scale
            selected = scaled[indices]
            before_mean, after_mean = float(np.mean(scaled)), float(np.mean(selected))
            sd = float(np.std(scaled, ddof=0))
            shift = abs(after_mean - before_mean) / sd if sd else 0.0
            if not sd and before_mean != after_mean:
                raise ValueError("Input population SD is zero but retained means differ")
            quantiles[metric] = {name: (np.quantile(array, [0, .25, .5, .75, 1]) * scale).tolist()
                                 for name, array in (("before", scaled), ("after", selected))}
            means[metric] = {"before": before_mean * scale, "after": after_mean * scale,
                             "absolute_standardized_shift": shift}
            if not all(_finite_number(value) for value in [*means[metric].values(),
                        *quantiles[metric]["before"], *quantiles[metric]["after"]]):
                raise ValueError("Comparison measurements exceed finite numeric precision")
            escaped = metric.replace("~", "~0").replace("/", "~1")
            fields = ["/reference_qc_quantiles/" + escaped + "/after/" + str(index) for index in range(5)]
            fields += ["/reference_qc_means/" + escaped + "/" + name for name in ("after", "absolute_standardized_shift")]
            for field in fields:
                provenance[field] = {"origin": _COMPARISON_ORIGIN,
                    "basis": "immutable_observation_rows_by_retained_ids",
                    "reference": {"input": "dataset", "measurement": metric},
                    "raw_counts_recomputed": False, "prerequisites": dict(invariants)}
    return {"format": "devharness.scientific-preview/1", "comparator": "observation_selection", "passed": True,
            "shape": {"before": [len(rows), len(measurements)], "after": [len(retained), len(measurements)]},
            "removed_ids": [value for value in old_ids if value not in retained_set], "added_ids": [],
            "invariants": invariants, "samples": samples, "warnings": warnings,
            "reference_qc_quantiles": quantiles, "reference_qc_means": means,
            "comparison_measurement_provenance": provenance,
            "limitations": ["A validation pass is not scientific approval",
                            "Bounded JSON observation selection uses supplied input measurements, not raw counts",
                            "counts_layer is a compatibility field and makes no count-matrix assertion",
                            "QC shifts and sample retention are diagnostics, not independently validated quality or biological truth"]}


def _dataset(path):
    """Preflight HDF5 dimensions before AnnData materializes arrays or layers."""
    import anndata
    import h5py
    path = Path(path)
    hash_artifact(path, max_bytes=MAX_DATASET_BYTES, max_entries=1)
    count, values, decoded_bytes, seen = 0, 0, 0, set()
    with h5py.File(path, "r") as handle:
        def visit(group):
            nonlocal count, values, decoded_bytes
            for name in group:
                count += 1
                if count > 512 or not isinstance(group.get(name, getlink=True), h5py.HardLink):
                    raise ValueError("Dataset contains links or exceeds the comparison budget")
                item = group[name]
                address = h5py.h5o.get_info(item.id).addr
                if address in seen:
                    raise ValueError("Repeated HDF5 object links are not supported")
                seen.add(address)
                if isinstance(item, h5py.Dataset):
                    if item.is_virtual or item.external:
                        raise ValueError("External dataset storage is not supported")
                    values += item.size
                    decoded_bytes += item.size * max(item.dtype.itemsize, 64 if item.dtype.hasobject else 1)
                    if values > MAX_ARRAY_VALUES or decoded_bytes > 64 * 1024**2 or item.dtype.itemsize > 1024:
                        raise ValueError("Dataset exceeds the comparison budget")
                else:
                    visit(item)
        visit(handle)
    data = anndata.read_h5ad(path)
    if data.n_obs * data.n_vars > MAX_ARRAY_VALUES:
        raise ValueError("Dataset exceeds the comparison budget")
    return data


def _array(value):
    from scipy import sparse
    return value.toarray() if sparse.issparse(value) else np.asarray(value)


def _equal(left, right):
    if left is None or right is None:
        return left is right
    left, right = _array(left), _array(right)
    numeric = np.issubdtype(left.dtype, np.number) and np.issubdtype(right.dtype, np.number)
    return left.shape == right.shape and np.array_equal(left, right, equal_nan=numeric)


def _finite_matrix(value):
    from scipy import sparse
    values = value.data if sparse.issparse(value) else np.asarray(value)
    return bool(np.isfinite(values).all() and (values >= 0).all())


def _columns(before, after):
    common = set(before.columns) & set(after.columns)
    return {"added": sorted(set(after.columns) - set(before.columns)),
            "removed": sorted(set(before.columns) - set(after.columns)),
            "changed": sorted(name for name in common if not before[name].equals(after[name]))}


def _slots(before, after, indices):
    common = set(before) & set(after)
    return {"added": sorted(set(after) - set(before)), "removed": sorted(set(before) - set(after)),
            "changed": sorted(name for name in common if indices is None or not _equal(before[name][indices], after[name]))}


def _retained_value_equal(before, after, *, depth=0, budget=None):
    """Compare complete retained slots/metadata within the existing reader bounds."""
    import pandas as pd
    budget = [10_000] if budget is None else budget
    budget[0] -= 1
    if depth > 32 or budget[0] < 0:
        raise ValueError("Scoped retention metadata exceeds the bounded semantic comparison")
    if isinstance(before, (pd.DataFrame, pd.Series)) or isinstance(after, (pd.DataFrame, pd.Series)):
        return type(before) is type(after) and before.equals(after)
    if isinstance(before, Mapping) or isinstance(after, Mapping):
        return (isinstance(before, Mapping) and isinstance(after, Mapping) and set(before) == set(after)
                and all(_retained_value_equal(before[key], after[key], depth=depth + 1, budget=budget) for key in before))
    if isinstance(before, (list, tuple)) or isinstance(after, (list, tuple)):
        return (type(before) is type(after) and len(before) == len(after)
                and all(_retained_value_equal(left, right, depth=depth + 1, budget=budget)
                        for left, right in zip(before, after)))
    return _equal(before, after)


def single_cell_preview(before_path, after_path, policy):
    """Compare observable changes; no scientific endorsement follows from a pass."""
    before, after = _dataset(before_path), _dataset(after_path)
    old_ids, new_ids = list(before.obs_names), list(after.obs_names)
    old_set, new_set = set(old_ids), set(new_ids)
    unique = after.obs_names.is_unique and before.obs_names.is_unique
    subset = new_set <= old_set and unique
    same_vars = before.var_names.equals(after.var_names) and before.var_names.is_unique
    indices = before.obs_names.get_indexer(after.obs_names) if subset else None
    layer = policy["counts_layer"]
    left = before.X if layer == "X" else before.layers.get(layer)
    right = after.X if layer == "X" else after.layers.get(layer)
    valid_counts = left is not None and right is not None and _finite_matrix(left) and _finite_matrix(right)
    same_counts = bool(valid_counts and subset and same_vars and _equal(left[indices], right))
    invariants = {"observation_ids_unique": bool(unique),
                  "retained_observation_ids_subset_of_input": bool(subset),
                  "variables_unchanged": bool(same_vars),
                  "retained_count_matrix_unchanged": same_counts}
    warnings, samples, qc = [], {}, {}
    sample_assignments_unchanged = True
    if not valid_counts:
        warnings.append("Required count representation is missing, negative or nonfinite")
    sample_key = policy.get("sample_key")
    if sample_key:
        if sample_key not in before.obs or sample_key not in after.obs:
            raise ValueError("Declared sample column is missing")
        if before.obs[sample_key].isna().any() or after.obs[sample_key].isna().any():
            raise ValueError("Declared sample identities contain missing values")
        for label in sorted(set(before.obs[sample_key].astype(str)) | set(after.obs[sample_key].astype(str))):
            n = int((before.obs[sample_key].astype(str) == label).sum())
            retained = int((after.obs[sample_key].astype(str) == label).sum())
            samples[label] = {"before": n, "retained": retained, "removed": n - retained,
                              "removal_rate": (n - retained) / n if n else None}
        rates = [row["removal_rate"] for row in samples.values() if row["removal_rate"] is not None]
        if rates and (max(rates) - min(rates) > .2 or any(row["retained"] == 0 for row in samples.values())):
            warnings.append("Sample retention differs substantially; review possible selection bias")
        sample_assignments_unchanged = bool(subset and before.obs.iloc[indices][sample_key].equals(after.obs[sample_key]))
        if subset and not sample_assignments_unchanged:
            warnings.append("Sample assignments changed for retained cells")
    reference_qc, measurement_provenance = {}, {}
    prerequisites = {**invariants, "sample_assignments_unchanged": sample_assignments_unchanged}
    for metric in policy.get("qc_metrics", []):
        if metric not in before.obs or metric not in after.obs:
            raise ValueError("Declared QC metric is missing: " + metric)
        quantiles = {}
        for name, data in (("before", before), ("after", after)):
            values = np.asarray(data.obs[metric], dtype=float)
            if not np.isfinite(values).all():
                raise ValueError("QC metric contains nonfinite values: " + metric)
            quantiles[name] = np.quantile(values, [0, .25, .5, .75, 1]).tolist() if len(values) else []
        qc[metric] = quantiles
        # Output obs columns are candidate-writable diagnostics. Reference
        # measurements use only immutable input annotations and verified IDs;
        # they do not recompute those annotations from raw counts.
        if all(prerequisites.values()):
            values = np.asarray(before.obs[metric], dtype=float)[indices]
            reference_qc[metric] = {"before": quantiles["before"],
                                    "after": np.quantile(values, [0, .25, .5, .75, 1]).tolist() if len(values) else []}
            escaped = metric.replace("~", "~0").replace("/", "~1")
            for index in range(len(reference_qc[metric]["after"])):
                measurement_provenance["/reference_qc_quantiles/" + escaped + "/after/" + str(index)] = {
                    "origin": _COMPARISON_ORIGIN, "basis": "immutable_input_annotations_by_retained_ids",
                    "reference": {"input": "dataset", "annotation": metric, "counts_layer": layer},
                    "raw_counts_recomputed": False, "prerequisites": dict(prerequisites)}
    if after.n_obs == 0:
        warnings.append("The candidate contains no cells")
    metadata = _columns(before.obs.iloc[indices], after.obs) if subset else {"unavailable": "Observation identities changed"}
    return {"format": "devharness.scientific-preview/1", "comparator": "single_cell_qc",
            "passed": bool(valid_counts and unique and all(invariants[name] for name in policy["invariants"])),
            "shape": {"before": [before.n_obs, before.n_vars], "after": [after.n_obs, after.n_vars]},
            "removed_ids": sorted(old_set - new_set), "added_ids": sorted(new_set - old_set),
            "samples": samples, "qc_quantiles": qc, "invariants": invariants, "warnings": warnings,
            "reference_qc_quantiles": reference_qc, "comparison_measurement_provenance": measurement_provenance,
            "metadata": metadata, "variables": _columns(before.var, after.var) if same_vars else {"changed": True},
            "layers": _slots(before.layers, after.layers, indices),
            "embeddings": _slots(before.obsm, after.obsm, indices),
            "limitations": ["A validation pass is not scientific approval", "Bounded single-file H5AD comparison",
                            "QC output annotations are candidate-supplied diagnostics, not independent comparison measurements",
                            "Reference QC measurements use immutable input annotations selected by retained IDs; annotations are not recomputed from raw counts or independently scientifically validated"]}


def validate_outputs(contract, inputs, outputs):
    policy = contract["validation"]
    if policy["comparator"] in {"observation_selection", "observation_diagnostic"} or "action_scope" in contract:
        # Also protect direct callers that did not enter through MethodService.
        MethodContract.from_dict(contract)
        for section, artifacts in (("inputs", inputs), ("outputs", outputs)):
            required = {name for name, spec in contract[section].items() if spec["required"]}
            if not required <= artifacts.keys() or not artifacts.keys() <= contract[section].keys():
                raise ValueError("Observation selection artifact bindings do not match the contract")
    for name, path in outputs.items():
        kind = contract["outputs"][name]["artifact_type"]
        if kind == "json":
            finite_json(path)
        elif kind == "anndata" and policy["comparator"] != "single_cell_qc":
            _dataset(path)
    if policy["comparator"] == "single_cell_qc":
        if "dataset" not in inputs or "dataset" not in outputs:
            raise ValueError("Single-cell comparison requires named dataset input and output")
        preview = single_cell_preview(inputs["dataset"], outputs["dataset"], policy)
        if contract.get("action_scope", {}).get("exclusions") is False:
            before, after = _dataset(inputs["dataset"]), _dataset(outputs["dataset"])
            sample_key = policy.get("sample_key")
            if (list(before.obs_names) != list(after.obs_names) or preview["added_ids"] or preview["removed_ids"]
                    or any(value is not True for value in preview["invariants"].values())
                    or (sample_key and not before.obs[sample_key].equals(after.obs[sample_key]))):
                raise ValueError("Non-exclusion action must retain every immutable observation in order")
            if contract["action_scope"]["intent"] == "retain":
                matrix_slots = ((before.layers, after.layers), (before.obsm, after.obsm),
                                (before.obsp, after.obsp), (before.varm, after.varm), (before.varp, after.varp))
                changed = (not _equal(before.X, after.X)
                           or any(_columns(before.obs, after.obs).values())
                           or any(_columns(before.var, after.var).values())
                           or any(not _retained_value_equal(left, right) for left, right in matrix_slots)
                           or not _retained_value_equal(before.uns, after.uns)
                           or (before.raw is None) != (after.raw is None))
                if before.raw is not None and after.raw is not None:
                    changed = changed or (not before.raw.var_names.equals(after.raw.var_names)
                        or not _equal(before.raw.X, after.raw.X)
                        or any(_columns(before.raw.var, after.raw.var).values())
                        or not _retained_value_equal(before.raw.varm, after.raw.varm))
                if changed:
                    raise ValueError("Scoped retention must preserve immutable measurements and existing representations")
        return preview
    if policy["comparator"] == "observation_selection":
        preview = observation_selection_preview(inputs["dataset"], outputs["dataset"], policy)
        if contract.get("action_scope", {}).get("exclusions") is False and preview["removed_ids"]:
            raise ValueError("Non-exclusion action must retain every immutable observation")
        return preview
    if policy["comparator"] == "observation_diagnostic":
        return observation_diagnostic_preview(inputs["dataset"], outputs["dataset"], policy)
    if policy["invariants"]:
        raise ValueError("Scientific invariants require the single-cell comparator")
    return {"format": "devharness.scientific-preview/1", "comparator": policy["comparator"], "passed": True,
            "invariants": {}, "warnings": ["Structural validation only; scientific review is required"],
            "inputs": {name: hash_artifact(path, max_bytes=MAX_DATASET_BYTES) for name, path in inputs.items()},
            "outputs": {name: hash_artifact(path, max_bytes=MAX_DATASET_BYTES) for name, path in outputs.items()}}
