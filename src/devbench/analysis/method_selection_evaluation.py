"""Frozen synthetic QC evaluation, outside every planner and candidate context.

The labels below describe a simulation, not biological truth discoverable from
QC extremes. This module never executes candidate code or calls a model. Native
planner decisions and OCI receipts must be sealed before reference scoring.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import random
import statistics

from devharness_core.provenance import canonical_json
from .method_executor import _path
from .method_validation import finite_json


FORMAT = "devbench.method-selection-pilot/1"
SIGNALS = ("signal_a", "signal_b", "signal_c")
CASE_TYPES = ("clean", "technical_damage", "valid_heterogeneity", "insufficient_information")
BOUNDS = {"cases": 4, "requested_model": "gpt-6.1-sol", "reasoning_effort": "low",
          "max_selections_per_case": 6, "max_selections_total": 24,
          "max_provider_sessions": 48, "selection_seconds": 120,
          "case_model_seconds": 720, "total_live_wall_seconds": 3600,
          "max_candidate_runs_per_case": 2, "max_candidate_runs": 8,
          "candidate_cpu": 1, "candidate_memory_mib": 512,
          "candidate_seconds": 20, "candidate_output_bytes": 1_000_000,
          "selector_memory_mib": 2048, "max_context_bytes": 128_000,
          "automatic_retries": 0,
          "cost_usd_limit": None,
          "cost_note": "Session, time and run caps are enforced. Subscription telemetry does not supply a dollar cost; no dollar cap is claimed."}
RUBRIC = {
    "format": "devbench.synthetic-qc-rubric/1",
    "authorship": "Evaluator design choices, not user-prespecified scientific method parameters.",
    "damage_sensitivity_min": .8, "valid_false_removal_max": .05,
    "worst_group_valid_preservation_min": .9, "rare_valid_preservation_min": .9,
    "global_signal_distortion_max": .2, "worst_group_signal_distortion_max": .2,
    "signal_fields": list(SIGNALS), "signal_scale": "population SD of all input valid observations, or 1 for a constant feature",
    "signal_distortion": "Mean over fixed signal fields of absolute retained-valid versus input-valid mean displacement divided by fixed input scale; also maximum of this mean over groups.",
    "criteria": "Conjunction of every defined metric bound for the case; absent damage, rare population or group denominators are N/A, not successes. Empty retained valid subsets fail and have unavailable signal distortion.",
    "pareto": "Compare two successful outputs on their common defined metrics only when defined metric sets match. Sensitivity and preservation increase; false removal and distortion decrease. A dominates B only if no metric is worse by more than 1e-12 and at least one is better by more than 1e-12. All differences within tolerance are a tie; no scalar winner is imposed.",
    "pareto_tolerance": 1e-12,
    "ambiguous_case": "No error-detection or signal-accuracy score: identical observations admit incompatible latent explanations. Independent review of a scientific abstention is required.",
    "null_dispositions": ["defensible_no_removal", "scientific_uncertainty_abstention", "capability_limited_nonselection", "invalid_or_execution_failure", "unresolved_review"],
    "separate_axes": "Review decision defensibility from visible evidence separately from numerical planted-damage recovery. A defensible retain/no-removal conclusion without a run does not invent a retained dataset. A defensible damage-case abstention need not recover planted damage.",
    "limits": "Four case-specific synthetic checks, not a reliability estimate, scientific optimality, real biological validation or clinical qualification.",
}

# Additive rubric: keep version 1 exactly replayable for sealed retention pilots.
DIAGNOSTIC_RUBRIC = {
    **RUBRIC, "format": "devbench.synthetic-qc-rubric/2",
    "flag_detection": {"damage_sensitivity_min": .8, "valid_false_flag_max": .05, "coverage_required": 1.,
                       "meaning": "Predicted atypicality flags versus planted simulation membership; not proven biological damage."},
    "action_policy": "Descriptive diagnostic flagging retains every immutable observation. Exclusion is a separate evidence-and-approval-dependent action.",
    "review_gate": "All decisions and independent blinded review are sealed before reference-label access.",
    "stability": "Unavailable unless independently evaluated matched perturbation outputs exist; retention and flag counts do not establish robustness.",
}
PROFILE_RUBRIC = {
    **DIAGNOSTIC_RUBRIC, "format": "devbench.synthetic-qc-rubric/3",
    "profile_evaluation": {
        "arithmetic_required": True, "computable_coverage_required": 1.,
        "meaning": "Independently checked numerical descriptions; no cutoff or technical-damage interpretation required.",
        "ranking": "Post-seal synthetic damage-versus-valid AUC on finite aggregate scores, higher means more atypical; ties receive half credit. Undefined with a missing class. No threshold optimization.",
        "ranking_directions": {"absolute_mad": "higher_score_more_unusual", "empirical_two_tail": "higher_score_more_unusual"},
        "decision_coverage": "Separate categorical decisions; no decided predictions means conditional classification accuracy is unavailable, not zero errors.",
    },
}


def digest(value):
    return hashlib.sha256(canonical_json(value)).hexdigest()


def file_digest(path):
    return hashlib.sha256(_path(path).read_bytes()).hexdigest()


def write_once(path, value):
    path = _path(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(canonical_json(value) + b"\n")
    return file_digest(path)


def _summary(values):
    ordered = sorted(values)
    def q(frac):
        position = (len(ordered) - 1) * frac
        lo, hi = math.floor(position), math.ceil(position)
        return ordered[lo] + (ordered[hi] - ordered[lo]) * (position - lo)
    return {"n": len(values), "min": ordered[0], "q25": q(.25), "median": q(.5),
            "q75": q(.75), "max": ordered[-1], "mean": statistics.mean(values)}


def _make_case(rng, kind):
    observations, valid, damaged, rare = [], [], [], []
    for sample in range(4):
        selected = set(rng.sample(range(18), 3 if kind == "technical_damage" else 2))
        for index in range(18):
            identity = "o_" + rng.randbytes(8).hex()
            library = rng.lognormvariate(6.9, .16)
            detected = max(1, round(240 + .08 * (library - 1000) + rng.gauss(0, 15)))
            mito = min(.95, max(.005, rng.gauss(.045, .009)))
            group = "A" if sample < 2 else "B"
            signals = [rng.gauss(2, .35), rng.gauss(2 + (group == "B"), .35), rng.gauss(2, .35)]
            is_damage = kind == "technical_damage" and index in selected
            is_rare = kind == "valid_heterogeneity" and index in selected
            if is_damage:
                library *= rng.uniform(.18, .32)
                detected = max(1, round(detected * rng.uniform(.15, .30)))
                mito = rng.uniform(.40, .60)
                signals = [value * rng.uniform(.25, .45) for value in signals]
            if is_rare:
                library *= 3.5
                detected = round(detected * 1.6)
                mito = rng.uniform(.14, .20)
                signals[0] += 4
                signals[2] += 4
            row = {"cell_id": identity, "sample": "S" + str(sample + 1), "group": group,
                   "total_counts": round(library), "detected_features": detected,
                   "mitochondrial_fraction": round(mito, 6),
                   **{key: round(value, 6) for key, value in zip(SIGNALS, signals)}}
            observations.append(row)
            (damaged if is_damage else valid).append(identity)
            if is_rare:
                rare.append(identity)
    rng.shuffle(observations)
    if kind == "insufficient_information":
        # Two latent worlds share the *same* visible observations. Nothing about
        # a large value alone identifies which world generated it.
        ambiguous_ids = {row["cell_id"] for row in rng.sample(observations, 12)}
        observations = [{"cell_id": row["cell_id"],
                         "measurement_a": row["total_counts"] * (3 if row["cell_id"] in ambiguous_ids else 1),
                         "measurement_b": round(row["mitochondrial_fraction"] * (4 if row["cell_id"] in ambiguous_ids else 1), 6)}
                        for row in observations]
        reference = {"case_type": kind, "identifiability": "ambiguous",
                     "compatible_worlds": ["The uncommon measurements represent valid state variation", "The same uncommon measurements represent technical damage"],
                     "alternative_world_damage_ids": sorted(ambiguous_ids)}
    else:
        order = [row["cell_id"] for row in observations]
        reference = {"case_type": kind, "identifiability": "known_synthetic",
                     "valid_ids": [identity for identity in order if identity in valid],
                     "damaged_ids": [identity for identity in order if identity in damaged],
                     "rare_valid_ids": [identity for identity in order if identity in rare],
                     "signal_fields": list(SIGNALS), "group_field": "group"}
    data = {"format": "devbench.qc-observations/1", "observations": observations}
    return data, reference


def public_context(data):
    rows = data["observations"]
    measurements = [key for key, value in rows[0].items() if type(value) in (int, float)]
    observed = {"synthetic_data": True, "row_count": len(rows), "observation_ids_unique": True,
                "metrics_finite": True, "data_format": data["format"], "measurement_names": measurements,
                "observations": rows, "measurement_summaries": {key: _summary([row[key] for row in rows]) for key in measurements},
                "expression_matrix_available": False, "independent_biological_replicates_verified": False,
                "sample_labels_present": "sample" in rows[0], "group_labels_present": "group" in rows[0]}
    if "sample" in rows[0]:
        observed.update(sample_labels=sorted({row["sample"] for row in rows}), group_labels=sorted({row["group"] for row in rows}),
                        sample_sizes={sample: sum(row["sample"] == sample for row in rows) for sample in sorted({row["sample"] for row in rows})},
                        declared_measurement_meanings={"total_counts": "Artificial RNA library size per observation", "detected_features": "Artificial detected-feature count",
                            "mitochondrial_fraction": "Artificial proportion assigned to mitochondrial features", "signal_a": "Artificial continuous signal measurement A",
                            "signal_b": "Artificial continuous signal measurement B", "signal_c": "Artificial continuous signal measurement C"})
        unknowns = [{"field": "biological_independence", "impact": "Sample labels do not establish independent donors; no biological hypothesis test is requested.", "blocking": False},
                    {"field": "quality_interpretation", "impact": "No per-observation quality labels or authoritative acceptable ranges are supplied; numerical extremeness alone is not technical damage.", "blocking": False}]
    else:
        observed["declared_measurement_meanings"] = {}
        unknowns = [{"field": "assay_semantics", "impact": "Assay type, measurement units and interpretation are not supplied.", "blocking": False},
                    {"field": "sample_provenance", "impact": "Samples, groups, expected valid states and technical provenance are unavailable.", "blocking": False}]
    return {"task": {"scientific_objective": "Determine whether removing any observations for technical quality is justified, while preserving valid variation, sample/group representation and the supplied signal measurements. Recommend a defensible approach or state which evidence is missing. A decision to retain the observations is allowed.",
                       "computational_task": "quality_control", "downstream_use": "Descriptive exploration of this artificial dataset; no biological causal or clinical claims.",
                       "constraints": ["No scientific method, filtering parameter or desired removal count is prescribed.", "Assess the supplied observations, objective, appropriate alternatives and assumptions before software availability.", "Use concise evidence statements; compare actual results only after execution, and state uncertainty or abstain when warranted."]},
            "dataset_summary": {"observed": observed, "inferred": {}, "unknowns": unknowns},
            "evaluation_purpose": "method_selection", "user_parameter_requirements": []}


def prepare_pilot(root, *, seed, design=None, runtime=None, diagnostic_outputs=False, diagnostic_profiles=False):
    root = _path(root)
    if (root / "pilot-manifest.json").exists() or (root / "private").exists():
        raise FileExistsError("Preserve the prior pilot preparation")
    if type(seed) is not int or not 0 <= seed < 2**128:
        raise ValueError("Use an explicit nonnegative bounded simulation seed")
    rng = random.Random(seed)
    cases, files, hidden = [], {}, {"format": FORMAT, "seed": seed, "cases": {}}
    ordered = list(CASE_TYPES)
    rng.shuffle(ordered)
    for kind in ordered:
        identity = "c_" + rng.randbytes(8).hex()
        cases.append(identity)
        data, reference = _make_case(rng, kind)
        hidden["cases"][identity] = reference
        context = public_context(data)
        if diagnostic_outputs or diagnostic_profiles:
            context["task"]["scientific_objective"] += (
                " Assess whether reversible descriptive diagnostic flags would help review while retaining all observations. "
                "Numerical atypicality does not establish technical causation; assay-supported QC interpretation requires measurement meaning. "
                "Declare the action intent and interpretation scope. No method or threshold is prescribed.")
        if diagnostic_profiles:
            context["task"]["scientific_objective"] += (
                " Retain computable continuous numerical profiles even when categorical flags are deferred. "
                "Declare a frozen diagnostic_profile describing the chosen statistic family, measurements, reference grouping, aggregation and nullable flag threshold. "
                "The family is a scientific definition; verified implementation availability is assessed afterward. "
                "Missing assay interpretation or a flag cutoff does not by itself make descriptive arithmetic unavailable.")
        for name, value in (("dataset.json", data), ("public-context.json", context)):
            path = root / "private" / "cases" / identity / name
            files[str(path.relative_to(root))] = write_once(path, value)
    files["private/evaluator/reference.json"] = write_once(root / "private/evaluator/reference.json", hidden)
    files["private/evaluator/rubric.json"] = write_once(root / "private/evaluator/rubric.json",
        PROFILE_RUBRIC if diagnostic_profiles else DIAGNOSTIC_RUBRIC if diagnostic_outputs else RUBRIC)
    files["private/evaluator/budget.json"] = write_once(root / "private/evaluator/budget.json", BOUNDS)
    if design is not None:
        files["private/evaluator/design.json"] = write_once(root / "private/evaluator/design.json", design)
    if runtime is not None:
        files["private/evaluator/runtime.json"] = write_once(root / "private/evaluator/runtime.json", runtime)
    payload = {"format": FORMAT, "case_ids": cases, "files": files}
    manifest = {**payload, "content_sha256": digest(payload)}
    write_once(root / "pilot-manifest.json", manifest)
    return manifest


def verify_preparation(root, expected_digest):
    root = _path(root)
    manifest = finite_json(root / "pilot-manifest.json")
    payload = {key: value for key, value in manifest.items() if key != "content_sha256"}
    if digest(payload) != expected_digest or manifest["content_sha256"] != expected_digest:
        raise ValueError("Preparation manifest hash differs from the retained freeze")
    for name, expected in manifest["files"].items():
        path = _path(root / name)
        if not path.is_relative_to(root) or not path.is_file() or file_digest(path) != expected:
            raise ValueError("Frozen preparation file changed: " + name)
    return manifest


def public_case(root, identity):
    root = _path(root)
    if identity not in finite_json(root / "pilot-manifest.json")["case_ids"]:
        raise ValueError("Unknown pilot case")
    return finite_json(root / "private/cases" / identity / "public-context.json")


def _selection(data, output):
    if not isinstance(output, dict) or set(output) != {"retained_ids"} or not isinstance(output["retained_ids"], list):
        raise ValueError("Selection must contain only retained_ids")
    rows = data["observations"]
    ids = [row["cell_id"] for row in rows]
    selected = output["retained_ids"]
    if (any(not isinstance(item, str) for item in ids + selected) or len(set(ids)) != len(ids)
            or len(set(selected)) != len(selected) or not set(selected) <= set(ids)
            or [item for item in ids if item in set(selected)] != selected):
        raise ValueError("Selection IDs must be a unique ordered input subset")
    return rows, set(selected)


def _ratio(numerator, denominator):
    return {"numerator": numerator, "denominator": denominator,
            "value": numerator / denominator if denominator else None}


def score_selection(data, reference, output):
    rows, selected = _selection(data, output)
    empty_ratio = _ratio(0, 0)
    if reference["identifiability"] == "ambiguous":
        return {"status": "scientific_selection_unidentified", "damage_sensitivity": empty_ratio,
                "valid_false_removal": empty_ratio, "rare_valid_preservation": empty_ratio,
                "worst_group_valid_preservation": None, "global_signal_distortion": None,
                "worst_group_signal_distortion": None, "criteria_passed": False,
                "retained_count": len(selected), "limitation": "Identical measurements support incompatible latent explanations; evaluate the scientific decision separately."}
    ids = {row["cell_id"] for row in rows}
    valid, damaged, rare = (set(reference[key]) for key in ("valid_ids", "damaged_ids", "rare_valid_ids"))
    if valid & damaged or valid | damaged != ids or not rare <= valid:
        raise ValueError("Synthetic reference membership is inconsistent with the input")
    signal_fields = reference["signal_fields"]
    valid_rows = [row for row in rows if row["cell_id"] in valid]
    kept = [row for row in valid_rows if row["cell_id"] in selected]
    scales = {key: statistics.pstdev(row[key] for row in valid_rows) or 1.0 for key in signal_fields}
    def shift(before, after):
        if not before or not after:
            return None
        return statistics.mean(abs(statistics.mean(row[key] for row in after) - statistics.mean(row[key] for row in before)) / scales[key] for key in signal_fields)
    group_field = reference.get("group_field")
    grouped = {}
    if group_field:
        for name in sorted({row[group_field] for row in valid_rows}):
            before = [row for row in valid_rows if row[group_field] == name]
            after = [row for row in kept if row[group_field] == name]
            grouped[str(name)] = {"valid_preservation": _ratio(len(after), len(before)), "signal_distortion": shift(before, after)}
    worst_preservation = min((entry["valid_preservation"]["value"] for entry in grouped.values()), default=None)
    group_shifts = [entry["signal_distortion"] for entry in grouped.values()]
    worst_shift = max(group_shifts) if group_shifts and all(value is not None for value in group_shifts) else None
    result = {"status": "scored", "retained_count": len(selected),
              "damage_sensitivity": _ratio(len(damaged - selected), len(damaged)),
              "valid_false_removal": _ratio(len(valid - selected), len(valid)),
              "rare_valid_preservation": _ratio(len(rare & selected), len(rare)),
              "worst_group_valid_preservation": worst_preservation,
              "global_signal_distortion": shift(valid_rows, kept), "worst_group_signal_distortion": worst_shift,
              "groups": grouped, "signal_scales": scales}
    comparisons = [(result["damage_sensitivity"]["value"], "min", RUBRIC["damage_sensitivity_min"]),
                   (result["valid_false_removal"]["value"], "max", RUBRIC["valid_false_removal_max"]),
                   (result["rare_valid_preservation"]["value"], "min", RUBRIC["rare_valid_preservation_min"]),
                   (worst_preservation, "min", RUBRIC["worst_group_valid_preservation_min"]),
                   (result["global_signal_distortion"], "max", RUBRIC["global_signal_distortion_max"]),
                   (worst_shift, "max", RUBRIC["worst_group_signal_distortion_max"])]
    result["criteria_passed"] = bool(kept and (not grouped or worst_shift is not None) and all(
        value is None or (value >= boundary if direction == "min" else value <= boundary)
        for value, direction, boundary in comparisons))
    canonical_json(result)
    return result


def score_diagnostics(before_path, reference, after_path, policy):
    """Score actual validated flags; hidden membership never guides their creation."""
    from .method_validation import extract_observation_diagnostics
    actual = extract_observation_diagnostics(before_path, after_path, policy)
    if policy["diagnostic_policy"]["format"] == "devbench.diagnostic-policy/2":
        return _score_profiles(before_path, reference, actual, policy)
    data = finite_json(before_path)
    ids, flags = set(actual["retained_ids"]), set(actual["flagged_ids"])
    unavailable = set(actual["unavailable_ids"])
    recovery = score_selection(data, reference, {"retained_ids": actual["retained_ids"]})
    coverage = _ratio(len(ids - unavailable), len(ids))
    detection = {"damage_sensitivity": _ratio(0, 0), "valid_false_flag": _ratio(0, 0),
                 "rare_valid_false_flag": _ratio(0, 0), "criteria_passed": False,
                 "status": "scientific_detection_unidentified",
                 "limitation": "Simulation labels assess predictions, not biological causation."}
    if reference["identifiability"] != "ambiguous":
        valid, damaged, rare = (set(reference[key]) for key in ("valid_ids", "damaged_ids", "rare_valid_ids"))
        detection.update(damage_sensitivity=_ratio(len(flags & damaged), len(damaged)),
                         valid_false_flag=_ratio(len(flags & valid), len(valid)),
                         rare_valid_false_flag=_ratio(len(flags & rare), len(rare)), status="scored")
        sensitivity = detection["damage_sensitivity"]["value"]
        false_flag = detection["valid_false_flag"]["value"]
        bounds = DIAGNOSTIC_RUBRIC["flag_detection"]
        detection["criteria_passed"] = bool(coverage["value"] == bounds["coverage_required"] and false_flag is not None
            and false_flag <= bounds["valid_false_flag_max"]
            and (sensitivity is None or sensitivity >= bounds["damage_sensitivity_min"]))
    result = {"status": "scored_diagnostic", "evidence_scope": actual["evidence_scope"],
              "retained_count": len(ids), "flagged_count": len(flags), "unavailable_count": len(unavailable),
              "coverage": coverage, "flagging_detection": detection, "exclusion_recovery": recovery,
              "stability": {"value": None, "reason": "No matched perturbation outputs were supplied"},
              "criteria_passed": False,
              "limitation": "Useful diagnostic evidence is separate from formal comparative selection and exclusion."}
    canonical_json(result)
    return result


def _score_profiles(before_path, reference, actual, policy):
    ids, flags = set(actual["retained_ids"]), set(actual["flagged_ids"])
    available, deferred = set(actual["score_available_ids"]), set(actual["deferred_flag_ids"])
    decided = ids - deferred
    score_coverage, decision_coverage = _ratio(len(available), len(ids)), _ratio(len(decided), len(ids))
    detection = {"damage_sensitivity": _ratio(0, 0), "valid_false_flag": _ratio(0, 0),
                 "rare_valid_false_flag": _ratio(0, 0), "criteria_passed": False,
                 "status": "scientific_detection_unidentified", "scope": "Conditional on categorical decisions only",
                 "limitation": "Deferred predictions receive no false-negative or false-positive credit; simulation membership is not biological causation."}
    ranking = {"value": None, "reason": "Simulation membership is scientifically unidentified",
               "direction": "higher_score_more_unusual", "ties": "half credit", "scope": "Post-seal synthetic selectivity only"}
    if reference["identifiability"] != "ambiguous":
        valid, damaged, rare = (set(reference[key]) for key in ("valid_ids", "damaged_ids", "rare_valid_ids"))
        detection.update(damage_sensitivity=_ratio(len(flags & damaged), len(damaged & decided)),
                         valid_false_flag=_ratio(len(flags & valid), len(valid & decided)),
                         rare_valid_false_flag=_ratio(len(flags & rare), len(rare & decided)),
                         status="scored_conditional" if decided else "categorical_decisions_deferred")
        sensitivity, false_flag = detection["damage_sensitivity"]["value"], detection["valid_false_flag"]["value"]
        bounds = PROFILE_RUBRIC["flag_detection"]
        detection["criteria_passed"] = bool(decision_coverage["value"] == bounds["coverage_required"] and false_flag is not None
            and false_flag <= bounds["valid_false_flag_max"]
            and (sensitivity is None or sensitivity >= bounds["damage_sensitivity_min"]))
        scores = {row["observation_id"]: row["score"] for row in actual["diagnostic_profiles"] if row["score"] is not None}
        positive, negative = [scores[key] for key in damaged & scores.keys()], [scores[key] for key in valid & scores.keys()]
        family = policy["diagnostic_policy"]["profile"]["family"]
        direction = PROFILE_RUBRIC["profile_evaluation"]["ranking_directions"].get(family)
        ranking.update(direction=direction, damage_score_count=len(positive), valid_score_count=len(negative))
        if direction == "higher_score_more_unusual" and positive and negative:
            ranking.update(value=sum((a > b) + .5 * (a == b) for a in positive for b in negative) / (len(positive) * len(negative)), reason=None)
        else:
            ranking["reason"] = "Missing available damage or valid score class" if direction else "No predeclared score direction"
    result = {"status": "scored_diagnostic_profile", "evidence_scope": actual["evidence_scope"],
              "retained_count": len(ids), "flagged_count": len(flags), "unavailable_count": len(ids - available),
              "deferred_flag_count": len(deferred), "coverage": score_coverage, "score_coverage": score_coverage,
              "decision_coverage": decision_coverage, "arithmetic_fidelity": actual["arithmetic_fidelity"],
              "profile_metrics": actual["profile_metrics"], "ranking_selectivity": ranking, "flagging_detection": detection,
              "exclusion_recovery": score_selection(finite_json(before_path), reference, {"retained_ids": actual["retained_ids"]}),
              "stability": {"value": None, "reason": "No matched perturbation outputs were supplied"},
              "criteria_passed": False,
              "limitation": "Arithmetic fidelity, descriptive coverage and synthetic selectivity do not establish a formal comparative winner or justify exclusion."}
    canonical_json(result)
    return result


def require_live_open(root):
    root = _path(root)
    if (root / "grading-started.json").exists():
        raise ValueError("Grading has begun; no further model dispatch is permitted")
    if (root / "blinded-review-seal.json").exists():
        raise ValueError("Blinded review is sealed; no further model dispatch is permitted")


def seal_blinded_review(root, decision_hashes, review_path):
    """Freeze an independent review against every decision, without opening truth."""
    root, review_path = _path(root), _path(review_path)
    require_live_open(root)
    manifest = finite_json(root / "pilot-manifest.json")
    if set(decision_hashes) != set(manifest["case_ids"]):
        raise ValueError("All decisions must be sealed before blinded review")
    for identity, expected in decision_hashes.items():
        if file_digest(root / "evidence/decisions" / (identity + ".json")) != expected:
            raise ValueError("Sealed decision hash changed before blinded review")
    if (not review_path.is_file() or review_path.stat().st_size > 1_000_000
            or not review_path.read_text().strip()):
        raise ValueError("A bounded retained blinded review is required")
    return write_once(root / "blinded-review-seal.json", {
        "format": "devbench.blinded-pilot-review/1", "decision_hashes": decision_hashes,
        "review_path": str(review_path), "review_sha256": file_digest(review_path),
        "expert_qualification": "unmet", "frozen_at": datetime.now(timezone.utc).isoformat()})


def verify_sealed_output(root, identity, item):
    """Recheck receipt bindings when scoring/plotting a previously sealed output."""
    from .methods import MethodService
    methods = MethodService(root / "private/cases" / identity / "service", readonly=True)
    run = methods.get("run", item["run_id"])
    path = _path(item["path"])
    policy = methods.method(run["method_version_id"])["contract"]["validation"]
    if (run["status"] != "succeeded" or run["validation"].get("passed") is not True
            or run["outputs"].get("dataset") != item["output_artifact_id"]
            or methods.artifact_path(item["output_artifact_id"]) != path
            or run["evidence"]["root_hash"] != item["receipt_root"]
            or not methods.provenance(run["run_id"])["valid"]
            or file_digest(path) != item["sha256"]
            or file_digest(methods.artifact_path(run["inputs"]["dataset"]))
               != file_digest(root / "private/cases" / identity / "dataset.json")
            or item.get("output_kind", "observation_selection") != policy["comparator"]
            or (policy["comparator"] == "observation_diagnostic" and item.get("diagnostic_policy") != policy)):
        raise ValueError("Sealed output receipt, immutable input or output binding changed")
    return policy


def seal_decision(root, identity, recommendation, *, accepted, terminal_status, outputs, state):
    root = _path(root)
    require_live_open(root)
    if identity not in finite_json(root / "pilot-manifest.json")["case_ids"]:
        raise ValueError("Unknown pilot case")
    records, names = [], set()
    for record in outputs:
        item = dict(record)
        if item["candidate"] in names:
            raise ValueError("Each candidate has at most one frozen output")
        names.add(item["candidate"])
        path = _path(item["path"])
        if not path.is_relative_to(root / "private/cases" / identity) or file_digest(path) != item["sha256"]:
            raise ValueError("Candidate output identity differs or escapes the private case")
        from .methods import MethodService
        methods = MethodService(root / "private/cases" / identity / "service", readonly=True)
        run = methods.get("run", item["run_id"])
        matches = [row for row in state.get("results", []) if row["candidate"] == item["candidate"]
                   and row["provenance"].get("run_id") == item["run_id"]]
        if (len(matches) != 1 or matches[0]["status"] not in {"success", "partial"} or item["status"] != "success"
                or run["status"] != "succeeded" or run["validation"]["passed"] is not True
                or run["outputs"].get("dataset") != item["output_artifact_id"]
                or methods.artifact_path(item["output_artifact_id"]) != path
                or run["evidence"]["root_hash"] != item["receipt_root"]
                or not methods.provenance(item["run_id"])["valid"]):
            raise ValueError("Candidate output is not supported by its actual successful method receipt")
        contract = methods.method(run["method_version_id"])["contract"]
        input_path = methods.artifact_path(run["inputs"]["dataset"])
        if file_digest(input_path) != file_digest(root / "private/cases" / identity / "dataset.json"):
            raise ValueError("Candidate output input is not the complete frozen case")
        comparator = contract["validation"]["comparator"]
        if comparator == "observation_diagnostic":
            rubric = finite_json(root / "private/evaluator/rubric.json")
            if rubric not in (DIAGNOSTIC_RUBRIC, PROFILE_RUBRIC):
                raise ValueError("Diagnostic outputs require a separately frozen v2 rubric")
            if contract["validation"]["diagnostic_policy"]["format"] == "devbench.diagnostic-policy/2" and rubric != PROFILE_RUBRIC:
                raise ValueError("Continuous profiles require a separately frozen v3 rubric")
            from .method_validation import extract_observation_diagnostics
            actual = extract_observation_diagnostics(input_path, path, contract["validation"])
            item.update(output_kind=comparator, diagnostic_policy=contract["validation"], diagnostics=actual)
        elif "output_kind" in item and item["output_kind"] != comparator:
            raise ValueError("Output kind must match its retained method contract")
        records.append(item)
    record = {"format": FORMAT, "case_id": identity, "recommendation": recommendation,
              "recommendation_accepted": accepted is True, "terminal_status": terminal_status,
              "outputs": records, "state": state, "state_sha256": digest(state),
              "sealed_at": datetime.now(timezone.utc).isoformat()}
    return write_once(root / "evidence/decisions" / (identity + ".json"), record)


def pareto_relations(scores):
    directions = {"damage_sensitivity": 1, "valid_false_removal": -1, "rare_valid_preservation": 1,
                  "worst_group_valid_preservation": 1, "global_signal_distortion": -1, "worst_group_signal_distortion": -1}
    vectors = {}
    for name, score in scores.items():
        vector = {}
        if score.get("status") != "scored":
            continue
        for key, direction in directions.items():
            value = score[key]
            value = value["value"] if isinstance(value, dict) else value
            if value is not None:
                vector[key] = value * direction
        vectors[name] = vector
    relations = []
    for index, left in enumerate(vectors):
        for right in list(vectors)[index + 1:]:
            a, b = vectors[left], vectors[right]
            if a.keys() != b.keys() or not a:
                relation = "not_comparable"
            else:
                differences = [a[key] - b[key] for key in a]
                tol = RUBRIC["pareto_tolerance"]
                relation = ("tie" if all(abs(value) <= tol for value in differences)
                            else "left_dominates" if min(differences) >= -tol and max(differences) > tol
                            else "right_dominates" if max(differences) <= tol and min(differences) < -tol
                            else "tradeoff")
            relations.append({"left": left, "right": right, "relation": relation})
    return relations


def evaluate_pilot(root, expected_digest, decision_hashes):
    root = _path(root)
    manifest = verify_preparation(root, expected_digest)
    if set(decision_hashes) != set(manifest["case_ids"]):
        raise ValueError("All case decisions must be sealed before evaluator reference access")
    decisions = {}
    for identity, expected in decision_hashes.items():
        path = root / "evidence/decisions" / (identity + ".json")
        if not path.is_file() or file_digest(path) != expected:
            raise ValueError("Sealed decision hash changed")
        record = finite_json(path)
        if digest(record["state"]) != record["state_sha256"]:
            raise ValueError("Sealed planner state hash changed")
        names = set()
        for item in record["outputs"]:
            if item["candidate"] in names:
                raise ValueError("Duplicate sealed candidate output binding")
            names.add(item["candidate"])
            output = _path(item["path"])
            if not output.is_relative_to(root / "private/cases" / identity) or file_digest(output) != item["sha256"]:
                raise ValueError("Sealed candidate output hash changed")
        decisions[identity] = record
    rubric = finite_json(root / "private/evaluator/rubric.json")
    if rubric not in (RUBRIC, DIAGNOSTIC_RUBRIC, PROFILE_RUBRIC):
        raise ValueError("Scoring implementation rubric differs from the frozen rubric")
    if rubric == RUBRIC and any(item.get("output_kind") == "observation_diagnostic"
            for record in decisions.values() for item in record["outputs"]):
        raise ValueError("Diagnostic outputs require a separately frozen v2 rubric")
    if rubric != PROFILE_RUBRIC and any(item.get("diagnostic_policy", {}).get("diagnostic_policy", {}).get("format") == "devbench.diagnostic-policy/2"
            for record in decisions.values() for item in record["outputs"]):
        raise ValueError("Continuous profiles require a separately frozen v3 rubric")
    if rubric in (DIAGNOSTIC_RUBRIC, PROFILE_RUBRIC):
        review_path = root / "blinded-review-seal.json"
        if not review_path.is_file():
            raise ValueError("A frozen blinded review is required before reference access")
        review = finite_json(review_path)
        if (review.get("decision_hashes") != decision_hashes
                or file_digest(_path(review["review_path"])) != review["review_sha256"]):
            raise ValueError("Frozen blinded review or its decision bindings changed")
        for identity, record in decisions.items():
            for item in record["outputs"]:
                verify_sealed_output(root, identity, item)
    write_once(root / "grading-started.json", {"freeze": expected_digest, "decision_hashes": decision_hashes,
               "started_at": datetime.now(timezone.utc).isoformat(), "future_model_dispatch_forbidden": True})
    # Integrity verification hashes reference bytes above without interpreting
    # labels. This is the first reference-label parsing in the live path.
    references = finite_json(root / "private/evaluator/reference.json")["cases"]
    cases = []
    for identity in manifest["case_ids"]:
        record, reference = decisions[identity], references[identity]
        data = finite_json(root / "private/cases" / identity / "dataset.json")
        candidate_scores = {}
        for item in record["outputs"]:
            if item["status"] == "success":
                if item.get("output_kind") == "observation_diagnostic":
                    candidate_scores[item["candidate"]] = score_diagnostics(
                        root / "private/cases" / identity / "dataset.json", reference,
                        _path(item["path"]), item["diagnostic_policy"])
                else:
                    candidate_scores[item["candidate"]] = score_selection(data, reference, finite_json(_path(item["path"])))
        choice = record["recommendation"].get("choice")
        accepted = record["recommendation_accepted"]
        decision_class = "planning_or_execution_failure" if not accepted else "abstention_pending_review" if choice is None else "conditional_selection"
        cases.append({"case_id": identity, "case_type": reference["case_type"],
                      "decision_class": decision_class, "scientific_abstention_verified": False,
                      "independent_decision_review": "required; classify no-removal, scientific uncertainty, capability limitation and failure separately",
                      "terminal_status": record["terminal_status"], "recommendation": record["recommendation"],
                      "candidate_scores": candidate_scores, "selected_score": candidate_scores.get(choice) if accepted else None,
                      "executed_output_metrics_available": bool(candidate_scores),
                      "numerical_recovery_demonstrated": bool(accepted and choice in candidate_scores and candidate_scores[choice]["criteria_passed"]),
                      "pareto_relations": pareto_relations(candidate_scores), "decision_sha256": decision_hashes[identity]})
    result = {"format": FORMAT, "scope": RUBRIC["limits"], "freeze": expected_digest,
              "rubric_sha256": file_digest(root / "private/evaluator/rubric.json"), "cases": cases}
    write_once(root / "independent-scores.json", result)
    return result


def plot_pilot(root, result):
    """Plot immutable synthetic observations and actual executed ID subsets."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    root = _path(root)
    if not (root / "grading-started.json").exists():
        raise ValueError("Reference plots are available only after decision sealing and grading")
    refs = finite_json(root / "private/evaluator/reference.json")["cases"]
    fig, axes = plt.subplots(2, 2, figsize=(13, 9), constrained_layout=True)
    plot_data = []
    for ax, case in zip(axes.flat, result["cases"]):
        identity = case["case_id"]
        data = finite_json(root / "private/cases" / identity / "dataset.json")
        rows, ref = data["observations"], refs[identity]
        unknown = ref["identifiability"] == "ambiguous"
        xkey, ykey = ("measurement_a", "measurement_b") if unknown else ("total_counts", "mitochondrial_fraction")
        seal = finite_json(root / "evidence/decisions" / (identity + ".json"))
        chosen = case["recommendation"].get("choice") if seal["recommendation_accepted"] else None
        output = next((row for row in seal["outputs"] if row["candidate"] == chosen and row["status"] == "success"), None)
        kept = set(finite_json(Path(output["path"]))["retained_ids"]) if output else None
        groups = [("Unknown validity", "#727A84", set(row["cell_id"] for row in rows))] if unknown else [
            ("Valid", "#277DA1", set(ref["valid_ids"]) - set(ref["rare_valid_ids"])),
            ("Rare valid", "#8A49A6", set(ref["rare_valid_ids"])),
            ("Planted damage", "#D9534F", set(ref["damaged_ids"]))]
        for label, color, ids in groups:
            points = [row for row in rows if row["cell_id"] in ids]
            if points:
                ax.scatter([row[xkey] for row in points], [row[ykey] for row in points], s=29, c=color, label=label, alpha=.8)
        if kept is not None:
            removed = [row for row in rows if row["cell_id"] not in kept]
            ax.scatter([row[xkey] for row in removed], [row[ykey] for row in removed], marker="x", color="black", s=80, label="Removed by selected method")
        title = case["case_type"].replace("_", " ").capitalize()
        ax.set_title(title + ("\nSelected output shown" if kept is not None else "\nNo selected executed output"), fontsize=12)
        ax.set_xlabel(xkey.replace("_", " "))
        ax.set_ylabel(ykey.replace("_", " "))
        ax.grid(alpha=.17)
        ax.legend(fontsize=8, loc="best")
        plot_data.append({"case_id": identity, "x": xkey, "y": ykey, "input_sha256": file_digest(root / "private/cases" / identity / "dataset.json"),
                          "selected_output_sha256": output["sha256"] if output else None,
                          "plotted_points": [{"cell_id": row["cell_id"], "x": row[xkey], "y": row[ykey], "selected_kept": row["cell_id"] in kept if kept is not None else None} for row in rows]})
    fig.suptitle("Synthetic QC pilot — evaluator labels revealed after decisions were sealed", fontsize=15)
    path = root / "pilot-observations.png"
    if path.exists():
        raise FileExistsError("Preserve earlier plots")
    fig.savefig(path, dpi=150)
    plt.close(fig)
    write_once(root / "plot-data.json", plot_data)
    return {"path": str(path), "sha256": file_digest(path), "plot_data_sha256": file_digest(root / "plot-data.json")}


def plot_executed_outputs(root, result):
    """Display every actual output, including diagnostics accompanying null choice."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from .method_validation import extract_observation_diagnostics
    root = _path(root)
    if not (root / "grading-started.json").is_file():
        raise ValueError("Actual output plots require sealed decisions and grading")
    for case in result["cases"]:
        identity = case["case_id"]
        decision_path = root / "evidence/decisions" / (identity + ".json")
        if file_digest(decision_path) != case.get("decision_sha256"):
            raise ValueError("Sealed plotted decision hash changed")
        for item in finite_json(decision_path)["outputs"]:
            verify_sealed_output(root, identity, item)
    fig, axes = plt.subplots(2, 2, figsize=(13, 8), constrained_layout=True)
    plotted = []
    for ax, case in zip(axes.flat, result["cases"]):
        identity = case["case_id"]
        data_path = root / "private/cases" / identity / "dataset.json"
        data = finite_json(data_path)
        decision = finite_json(root / "evidence/decisions" / (identity + ".json"))
        records = []
        for item in decision["outputs"]:
            output_path = _path(item["path"])
            if file_digest(output_path) != item["sha256"]:
                raise ValueError("Sealed plotted output changed")
            row = {"candidate": item["candidate"], "output_sha256": item["sha256"],
                   "input_sha256": file_digest(data_path), "receipt_root": item["receipt_root"],
                   "flagged": None, "unavailable": None, "score_available": None, "flags_deferred": None}
            if item.get("output_kind") == "observation_diagnostic":
                actual = extract_observation_diagnostics(data_path, output_path, item["diagnostic_policy"])
                row.update(retained=len(actual["retained_ids"]), flagged=len(actual["flagged_ids"]),
                           unavailable=len(actual["unavailable_ids"]))
                if "diagnostic_profiles" in actual:
                    row.update(score_available=len(actual["score_available_ids"]), flags_deferred=len(actual["deferred_flag_ids"]),
                               profile_definition=item["diagnostic_policy"]["diagnostic_policy"]["profile"],
                               numeric_profiles=actual["diagnostic_profiles"], profile_metrics=actual["profile_metrics"],
                               arithmetic_fidelity=actual["arithmetic_fidelity"])
            else:
                _, selected = _selection(data, finite_json(output_path))
                row["retained"] = len(selected)
            records.append(row)
        if records:
            categories = [("retained", "#277DA1"), ("flagged", "#D99537"), ("unavailable", "#727A84")]
            if any(row["score_available"] is not None for row in records):
                categories.extend([("score_available", "#55995F"), ("flags_deferred", "#9B75B3")])
            width = .8 / len(categories)
            for offset, (key, color) in enumerate(categories):
                values = [(index, row[key]) for index, row in enumerate(records) if row[key] is not None]
                ax.bar([index + (offset - (len(categories) - 1) / 2) * width for index, _ in values], [value for _, value in values],
                       width=width, color=color, label=key.replace("_", " ").capitalize())
            ax.set_xticks(range(len(records)), [row["candidate"][:36] for row in records], fontsize=8)
            ax.legend(fontsize=8)
        else:
            ax.text(.5, .5, "No executed output\n" + case["terminal_status"], ha="center", va="center", transform=ax.transAxes)
            ax.set_xticks([])
        ax.set_ylim(0, len(data["observations"]) * 1.1)
        ax.set_ylabel("Observation count")
        ax.set_title(case["case_type"].replace("_", " ").capitalize())
        plotted.append({"case_id": identity, "output_present": bool(records), "outputs": records})
    for ax in list(axes.flat)[len(result["cases"]):]:
        ax.set_visible(False)
    fig.suptitle("Actual executed outputs — flags retain observations; missing outputs remain absent")
    path = root / "executed-outputs.png"
    if path.exists():
        raise FileExistsError("Preserve earlier output plots")
    fig.savefig(path, dpi=150)
    plt.close(fig)
    write_once(root / "executed-output-plot-data.json", plotted)
    result = {"path": str(path), "sha256": file_digest(path),
              "plot_data_sha256": file_digest(root / "executed-output-plot-data.json")}
    profiles = [(case["case_id"], row) for case in plotted for row in case["outputs"] if "numeric_profiles" in row]
    if profiles:
        # Separate axes prevent interpreting family-specific score scales as a winner.
        fig, axes = plt.subplots(len(profiles), 1, figsize=(12, max(3, 2.6 * len(profiles))), squeeze=False, constrained_layout=True)
        for ax, (identity, row) in zip(axes.flat, profiles):
            for measurement in row["profile_definition"]["measurements"]:
                values = [(index, record["profiles"][measurement]["score"]) for index, record in enumerate(row["numeric_profiles"])
                          if record["profiles"][measurement]["score"] is not None]
                if values:
                    ax.scatter([index for index, _ in values], [value for _, value in values], s=12, label=measurement)
            if ax.collections:
                ax.legend(fontsize=8)
            else:
                ax.text(.5, .5, "No computable measurement profiles", ha="center", transform=ax.transAxes)
            ax.set_xlabel("Immutable input observation index")
            ax.set_ylabel(row["profile_definition"]["family"] + " score")
            ax.set_title(identity + " / " + row["candidate"] + f"; categorical flags deferred {row['flags_deferred']}/{row['retained']}", fontsize=9)
        fig.suptitle("Actual independently verified measurement profiles — family-specific scales; no causal interpretation")
        profile_path = root / "executed-profiles.png"
        if profile_path.exists():
            raise FileExistsError("Preserve earlier profile plots")
        fig.savefig(profile_path, dpi=150)
        plt.close(fig)
        result["profile_plot"] = {"path": str(profile_path), "sha256": file_digest(profile_path)}
    return result
