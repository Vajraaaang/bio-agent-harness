"""Assess ERQL calibration on a frozen public synthetic T09 roster."""

import argparse
import hashlib
import json
import os
import sys
import tempfile
from collections import Counter
from fractions import Fraction
from pathlib import Path
from statistics import mean

from devbench.analysis.erql_qualification import _write, score_family, summarize_family
from devbench.core.contrast_design import DesignSpec
from devbench.core.count_profiles import (
    CountBlock,
    CountComponent,
    CountObservation,
    ProfileKey,
    compile_count_profiles,
)
from devharness_controlled.erql_contracts import (
    AUGMENTED,
    CANONICAL,
    DESIGN_AWARE,
    DESIGN_AWARE_REGIMES,
    GROUP_FILTERED,
    IMAGE,
    LEGACY_REGIMES,
    LOCK,
    LOCK_SHA256,
    PAIRED_REGIMES,
    digest,
    driver_for_profile,
    prepare_erql_count_profiles,
)
from devharness_controlled.verification import _json, _read


PROFILES = {
    CANONICAL: LEGACY_REGIMES,
    AUGMENTED: LEGACY_REGIMES,
    DESIGN_AWARE: DESIGN_AWARE_REGIMES,
}
PROTOCOL_V1 = "devbench.erql-t09-calibration-diagnostic/1"
PROTOCOL_V2 = "devbench.erql-t09-calibration-diagnostic/2"
HISTORICAL_SEED = 20261115
DEFAULT_SEED = 20261116
THREE_PAIR_BATCH_ASSIGNMENTS = {
    "balanced": {"reference": ("batch-a", "batch-b", "batch-a"),
                 "target": ("batch-b", "batch-a", "batch-a")},
    "unbalanced": {"reference": ("batch-a", "batch-a", "batch-a"),
                   "target": ("batch-b", "batch-a", "batch-b")},
}
SCORE_STATUS = {"observed": "ready", "empty": "empty", "withheld": "withheld"}
SCENARIOS = ("global_null", "sparse_signal")
FEATURES = tuple(f"gene-{index:03d}" for index in range(128))
REPLICATES = 6
SIGNAL_FEATURES = 8


def freeze_calibration(*, studies_per_stratum, seed, protocol_version=2):
    """Freeze the current roster or reconstruct a historical version 1 protocol."""
    from scipy.stats import binom

    if type(studies_per_stratum) is not int or studies_per_stratum < 20:
        raise ValueError("T09 diagnostic requires at least 20 studies per stratum")
    if type(seed) is not int or not 0 <= seed < 2**32:
        raise ValueError("T09 diagnostic seed must be a nonnegative 32-bit integer")
    if protocol_version not in (1, 2):
        raise ValueError("Unknown T09 diagnostic protocol version")
    historical = protocol_version == 1
    if not historical and seed == HISTORICAL_SEED:
        raise ValueError("Fresh T09 diagnostic must not reuse the historical seed")
    studies = [
        {"study_id": f"{regime}-{scenario}-{draw + 1:03d}",
         "regime": regime, "scenario": scenario}
        for regime in DESIGN_AWARE_REGIMES
        for scenario in SCENARIOS
        for draw in range(studies_per_stratum)
    ]
    profiles = PROFILES if historical else {GROUP_FILTERED: DESIGN_AWARE_REGIMES}
    bound_count = len(SCENARIOS) * sum(map(len, profiles.values()))
    if not historical:
        studies = [
            dict(case, study_id=f"{case['study_id']}-nominal", batch_balance="balanced",
                 n_pairs=REPLICATES if case["regime"] in PAIRED_REGIMES else None)
            for case in studies
        ]
        studies.extend(
            {"study_id": f"{regime}-{scenario}-three-pairs-{balance}-{draw + 1:03d}",
             "regime": regime, "scenario": scenario, "n_pairs": 3, "batch_balance": balance}
            for regime in DESIGN_AWARE_REGIMES if regime in PAIRED_REGIMES
            for balance in THREE_PAIR_BATCH_ASSIGNMENTS
            for scenario in SCENARIOS
            for draw in range(studies_per_stratum)
        )
        bound_count += len(PAIRED_REGIMES) * len(THREE_PAIR_BATCH_ASSIGNMENTS) * len(SCENARIOS)
    delta = Fraction(1, 20 * bound_count)
    nominal, inflated = 0.05, 0.40
    critical = int(binom.isf(float(delta), studies_per_stratum, nominal)) + 1
    protocol = {
        "format": PROTOCOL_V1 if historical else PROTOCOL_V2,
        "scope": "public_synthetic_fixed_procedures",
        "qualification_admitted": False,
        "publication_eligible": False,
        "generator": "shared-unit-depth-batch-nb-sparse/1" if historical else "shared-unit-depth-batch-nb-sparse/2",
        "profiles": {profile: list(regimes) for profile, regimes in profiles.items()},
        "seed": seed,
        "studies_per_stratum": studies_per_stratum,
        "studies": [dict(case, rng_index=index) for index, case in enumerate(studies)],
        "bh_alpha": "1/20",
        "family_weight": "1",
        "bound_count": bound_count,
        "delta_per_bound": str(delta),
        "endpoints": [
            "study_fdp", "global_null_any_discovery", "signal_recovered_fraction", "ready_studies",
        ],
        "null_inflation_power": {
            "nominal_probability": nominal,
            "alternative_probability": inflated,
            "critical_any_discovery_count": critical,
            "size_bound": float(delta),
            "exact_size": float(binom.sf(critical - 1, studies_per_stratum, nominal)),
            "planned_power": float(binom.sf(critical - 1, studies_per_stratum, inflated)),
        },
    }
    if not historical:
        protocol.update(
            calibration_profile=GROUP_FILTERED,
            calibration_pass_rule={
                "global_null_any_discovery": "below_critical_count_in_every_global_null_stratum",
                "allowed_withheld_attempts": 0,
            },
            three_pair_batch_assignments={
                balance: {group: list(batches) for group, batches in assignments.items()}
                for balance, assignments in THREE_PAIR_BATCH_ASSIGNMENTS.items()
            },
            historical_non_admission={
                "profile": DESIGN_AWARE,
                "protocol_sha256": "57d082ac27d079f6d10b445c5db4cd77cbca36e3249384ffa316f32234129e36",
                "result_sha256": "3f9fdb25069ff08b743d14faa2c3178097a10e8498f1547262c83fbb2e6e72b5",
                "reason": "global_null_inflation_detected",
            },
        )
    return protocol


def generate_study(protocol, case):
    """Generate count and provenance records once for all supported arms."""
    import numpy as np

    regime = case["regime"]
    paired = regime in PAIRED_REGIMES
    population_pair = regime == "P-P"
    bulk = regime.startswith("B-")
    study_seed = np.random.SeedSequence([protocol["seed"], case["rng_index"]])
    rng = np.random.default_rng(study_seed)
    measurement = "non_umi_counts" if bulk else "umi_counts"
    signal = case["scenario"] == "sparse_signal"
    replicates = case.get("n_pairs") or REPLICATES
    batches = protocol["three_pair_batch_assignments"][case["batch_balance"]] if replicates == 3 else None
    depth = rng.lognormal(0, 0.35, size=2 * replicates)
    unit_effect = rng.lognormal(0, 0.22, size=replicates if paired else 2 * replicates)
    baseline = np.geomspace(6, 160, len(FEATURES))
    low_support = set(rng.choice(2 * replicates, size=2, replace=False).tolist())
    keys, components, observations, matrix, metadata = [], [], [], [], []
    for arm, group in enumerate(("reference", "target")):
        for replicate in range(replicates):
            index = arm * replicates + replicate
            unit_index = replicate if paired else index
            unit = f"unit-{unit_index:02d}"
            condition = "fixed-condition" if population_pair else group
            population = None if bulk else group if population_pair else "fixture-population"
            specimen = f"{unit}-{condition}"
            obs = f"profile-{index:02d}"
            if batches is None:
                batch = "batch-a" if (replicate + arm) % 2 == 0 else "batch-b"
            else:
                batch = batches[group][replicate]
            key = ProfileKey(unit, specimen, condition, population)
            mu = baseline * depth[index] * unit_effect[unit_index]
            if batch == "batch-b":
                mu[32:48] *= 1.5
            if signal and group == "target":
                mu[:SIGNAL_FEATURES] *= 3.0
            counts = rng.negative_binomial(10, 10 / (10 + mu)).astype(np.int64)
            counts[-2] = 200 if index in low_support else 0
            counts[-1] = 0
            component_sha256 = digest([case, obs, counts.tolist()])
            components.append(CountComponent(
                obs, specimen, obs, "synthetic-panel", obs, 0, 1,
                component_sha256, measurement, (("batch", batch),),
            ))
            observations.append(CountObservation(obs, key, obs, cell_id=None if bulk else obs))
            keys.append(key)
            matrix.append(counts)
            metadata.append({
                "profile_id": obs, "unit": unit, "specimen": specimen, "condition": condition,
                "population": population, "batch": batch,
            })
    panel = np.vstack(matrix)
    source = {"case": case, "seed": protocol["seed"], "measurement": measurement,
              "feature_ids": list(FEATURES), "profiles": metadata, "counts": panel.tolist()}
    source_sha = digest(source)
    origin = "independent_assignment" if population_pair else None
    inspection = {
        "generator": protocol["generator"],
        "source_sha256": source_sha,
        "biological_unit_basis": (
            "independent simulated units with two paired profiles" if paired
            else "independent simulated units with one profile each"
        ),
        "population_label_origin": origin,
    }
    counts = compile_count_profiles(
        FEATURES, tuple(components), (CountBlock(tuple(observations), panel),),
        expected_profiles=tuple(keys),
    )
    design = DesignSpec(
        regime, "target", "reference", technical_factors=("batch",),
        condition="fixed-condition" if population_pair else None,
        population="fixture-population" if regime.startswith("S-") else None,
    )
    bindings = {
        "study_id": case["study_id"], "data_classification": "synthetic",
        "source_sha256": source_sha, "inspection_sha256": digest(inspection),
        "population_label_origin": origin,
    }
    truth = {gene: signal and index < SIGNAL_FEATURES for index, gene in enumerate(FEATURES)}
    return counts, design, bindings, {"source": source, "inspection": inspection}, truth


def _withheld(truth):
    return score_family(truth, {"status": "withheld", "genes": []})


def _attempt(root, profile, counts, design, bindings, truth):
    from devharness_controlled.native_executor import execute_erql_plan
    from devharness_controlled.qualification import synthetic_approvals

    root.mkdir(mode=0o700)
    receipt = None
    try:
        plan, request = prepare_erql_count_profiles(
            counts, design_spec=design, profile=profile, **bindings,
        )
        source = root / "request.json"
        _write(source, request)
        approvals, signers = synthetic_approvals(plan)
        output = root / "execution"
        result = execute_erql_plan(
            plan, approvals, signers, request_path=source, output_dir=output,
        )
        receipt = result["receipt"]["root_hash"]
        if (result["status"] != "analysis_complete_pending_review"
                or result["audit_verification"]["valid"] is not True):
            raise ValueError(result.get("error", "ERQL result or audit withheld"))
        reference = result["family_synthesis"]
        synthesis = _json(_read(
            output / "family-synthesis.json", reference["sha256"], 16 * 1024**2,
        ))
        if synthesis["publication_eligible"] is not False or len(synthesis["families"]) != 1:
            raise ValueError("Expected one synthetic exploratory result family")
        family = synthesis["families"][0]
        score = score_family(truth, family)
        if score["status"] not in {"ready", "empty"}:
            raise ValueError("ERQL family unavailable for calibration scoring")
    except (OSError, RuntimeError, ValueError) as error:
        return {"disposition": "withheld", "error": str(error)[:1000], "score": _withheld(truth),
                "execution_root_sha256": receipt}
    return {"disposition": "observed" if score["ready"] else "empty", "score": score,
            "family_counts": family["counts"], "execution_root_sha256": receipt}


def _study(root, protocol, case):
    root.mkdir(mode=0o700)
    counts, design, bindings, generated, truth = generate_study(protocol, case)
    _write(root / "synthetic-count-source.json", generated["source"])
    _write(root / "synthetic-inspection.json", generated["inspection"])
    attempts = {}
    for index, (profile, regimes) in enumerate(protocol["profiles"].items()):
        if design.regime in regimes:
            attempts[profile] = _attempt(root / f"arm-{index}", profile, counts, design, bindings, truth)
        else:
            attempts[profile] = {"disposition": "unsupported_regime", "score": None,
                                 "execution_root_sha256": None}
    _write(root / "generator-truth.json", truth)
    record = {**case, "source_sha256": bindings["source_sha256"], "attempts": attempts}
    _write(root / "score.json", record)
    return record


def summarize_calibration(protocol, records):
    if protocol.get("format") not in (PROTOCOL_V1, PROTOCOL_V2):
        raise ValueError("Unknown T09 diagnostic protocol version")
    historical = protocol["format"] == PROTOCOL_V1
    expected = freeze_calibration(
        studies_per_stratum=protocol["studies_per_stratum"], seed=protocol["seed"],
        protocol_version=1 if historical else 2,
    )
    if protocol != expected:
        raise ValueError("Modified T09 diagnostic protocol")
    if len(records) != len(expected["studies"]) or any(
            any(record.get(key) != value for key, value in case.items())
            or set(record["attempts"]) != set(protocol["profiles"])
            for record, case in zip(records, expected["studies"])):
        raise ValueError("T09 diagnostic roster is incomplete or reordered")
    delta = float(Fraction(protocol["delta_per_bound"]))
    cutoff = protocol["null_inflation_power"]["critical_any_discovery_count"]
    stratum_keys = ("regime", "scenario") if historical else ("regime", "scenario", "n_pairs", "batch_balance")
    assigned_by_stratum = {}
    for record in records:
        key = tuple(record[field] for field in stratum_keys)
        assigned_by_stratum.setdefault(key, []).append(record["attempts"])
    strata = []
    for key, assigned in assigned_by_stratum.items():
        stratum = dict(zip(stratum_keys, key, strict=True))
        regime = stratum["regime"]
        global_null = stratum["scenario"] == "global_null"
        profiles = {}
        for profile, regimes in protocol["profiles"].items():
            attempts = [row[profile] for row in assigned]
            if regime not in regimes:
                if any(arm["disposition"] != "unsupported_regime" or arm["score"] is not None
                       for arm in attempts):
                    raise ValueError("Unsupported arm supplied numerical evidence")
                profiles[profile] = {"status": "unsupported_regime",
                                     "assigned_studies": len(attempts)}
                continue
            scores = [arm["score"] for arm in attempts]
            if any(arm["disposition"] not in SCORE_STATUS
                   or type(arm["score"]) is not dict
                   or arm["score"]["status"] != SCORE_STATUS[arm["disposition"]]
                   or arm["score"]["ready"] != (arm["disposition"] == "observed")
                   for arm in attempts):
                raise ValueError("Supported arm is missing its execution disposition or score")
            summary = summarize_family(scores, global_null=global_null, delta=delta)
            dispositions = Counter(arm["disposition"] for arm in attempts)
            summary.update(
                observed_studies=dispositions["observed"] + dispositions["empty"],
                empty_studies=dispositions["empty"],
                withheld_studies=dispositions["withheld"],
            )
            if historical and profile != CANONICAL and regime in protocol["profiles"][CANONICAL]:
                summary["mean_paired_fdp_delta_vs_erql_1_1"] = mean(
                    score["fdp"] - row[CANONICAL]["score"]["fdp"]
                    for score, row in zip(scores, assigned, strict=True)
                )
            if not historical and global_null:
                summary.update(critical_any_discovery_count=cutoff,
                               null_inflation_detected=summary["any_discovery_studies"] >= cutoff)
            profiles[profile] = summary
        strata.append({**stratum, "profiles": profiles})
    result = {"format": f"devbench.erql-t09-calibration-result/{1 if historical else 2}",
              "scope": protocol["scope"], "qualification_admitted": False, "publication_eligible": False,
              "protocol_sha256": digest(protocol), "strata": strata, "studies": records}
    if not historical:
        profile = protocol["calibration_profile"]
        summaries = [stratum["profiles"][profile] for stratum in strata]
        result.update(
            calibration_profile=profile,
            calibration_passed=all(not summary["withheld_studies"] and not summary.get("null_inflation_detected", False)
                                   for summary in summaries),
        )
    return result


def _check_image(root):
    from devharness_controlled.native_runtime import LocalDocker

    docker = LocalDocker(erql=True)
    docker.isolate_client(root / "preflight-client")
    image = docker._call([
        "image", "inspect", IMAGE, "--format",
        '{"id":{{json .Id}},"os":{{json .Os}},"architecture":{{json .Architecture}}}',
    ], json_result=True)
    if image != {"id": IMAGE, "os": "linux", "architecture": "amd64"}:
        raise ValueError("Pinned ERQL image unavailable or mismatched")


def main(argv=None):
    from devbench.engine.execution_snapshot import current_binding, launch
    from devbench.engine.workspace import protected_path

    argv = sys.argv[1:] if argv is None else argv
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--studies-per-stratum", type=int, default=20)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--plan-only", action="store_true")
    mode.add_argument("--allow-data-execution", action="store_true")
    args = parser.parse_args(argv)
    try:
        parent = protected_path(args.output_root)
        if not parent.is_dir() or not parent.is_relative_to(Path.cwd().resolve()):
            raise ValueError("Use an existing T09 diagnostic output root within the checkout")
        protocol = freeze_calibration(
            studies_per_stratum=args.studies_per_stratum, seed=args.seed,
        )
        binding = current_binding()
        if args.allow_data_execution and binding is None:
            os.environ["DEVBENCH_RUNS_ROOT"] = str(parent / ".runs")
            return launch("scripts.run_erql_calibration_diagnostic", argv, replace_process=True)
        output = Path(tempfile.mkdtemp(prefix="erql-t09-diagnostic-", dir=parent))
    except (OSError, ValueError) as error:
        parser.error(str(error))
    _write(output / "protocol.json", protocol)
    if args.plan_only:
        print(json.dumps({
            "status": "synthetic_diagnostic_planned", "qualification_admitted": False,
            "protocol": str(output / "protocol.json"), "studies": len(protocol["studies"]),
        }))
        return 0
    try:
        _read(LOCK, LOCK_SHA256, 1024**2)
        _check_image(output)
        drivers = {profile: hashlib.sha256(
            _read(driver_for_profile(profile), limit=1024**2)
        ).hexdigest() for profile in protocol["profiles"]}
        _write(output / "execution-identity.json", {
            "execution_code": binding, "image": IMAGE, "platform": "linux/amd64",
            "package_lock_sha256": LOCK_SHA256, "driver_sha256": drivers,
        })
    except (OSError, RuntimeError, ValueError) as error:
        _write(output / "preflight-failure.json", {
            "error": str(error)[:1000], "calibration_passed": False, "qualification_admitted": False,
            "publication_eligible": False,
        })
        print(json.dumps({
            "status": "synthetic_diagnostic_preflight_failed", "qualification_admitted": False,
            "calibration_passed": False, "publication_eligible": False,
            "receipt": str(output / "preflight-failure.json"),
        }))
        return 1
    records = [_study(output / case["study_id"], protocol, case) for case in protocol["studies"]]
    result = summarize_calibration(protocol, records)
    _write(output / "calibration-result.json", result)
    dispositions = Counter(arm["disposition"] for row in records for arm in row["attempts"].values())
    print(json.dumps({"status": "synthetic_diagnostic_completed", "qualification_admitted": False,
                      "publication_eligible": False, "calibration_passed": result["calibration_passed"],
                      "studies": len(records), "withheld_attempts": dispositions["withheld"],
                      "empty_attempts": dispositions["empty"],
                      "receipt": str(output / "calibration-result.json")}))
    return int(not result["calibration_passed"])


if __name__ == "__main__":
    raise SystemExit(main())
