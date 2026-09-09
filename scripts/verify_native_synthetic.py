#!/usr/bin/env python3
"""Real synthetic CLI integration checks; ephemeral fixture keys are not human approvals.

Requires the approved native candidate image already installed in local Docker.
No downloads, model calls or clinical inputs occur. This checks software execution,
repeatability and refusal behavior; it is not a calibration/power benchmark.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
from pathlib import Path
import subprocess
import sys

for key in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "VECLIB_MAXIMUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ[key] = "1"


def main():
    import numpy as np
    from patsy import dmatrix
    from devharness_controlled.contracts import canonical_json_bytes
    from devharness_controlled.native_contracts import NativeAnalysisPlan
    from devharness_controlled.native_runtime import native_identity
    from devharness_controlled.qualification import synthetic_approvals
    from devharness_controlled.verification import verify_controlled_execution
    from devharness_core.provenance import hash_artifact

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = args.output.absolute()
    if root.resolve() != root:
        raise ValueError("Output must be canonical and not a symlink")
    root.mkdir(parents=True, exist_ok=False, mode=0o700)

    def save(path, value):
        with path.open("xb") as handle:
            path.chmod(0o600); handle.write(canonical_json_bytes(value))

    inputs = root / "fixture"
    inputs.mkdir(mode=0o700)
    rng = np.random.Generator(np.random.PCG64(2026090933))
    samples, genes = [f"S{i:03d}" for i in range(24)], [f"g{i:03d}" for i in range(80)]
    times = np.repeat(np.arange(6, dtype=float), 4)
    batch = np.tile([0, 1], 12)
    basis = np.asarray(dmatrix("cr(t, df=3, constraints='center') - 1", {"t": times}))
    full = np.column_stack((np.ones(24), basis, batch))
    reduced = full[:, [0, 4]]
    means = rng.uniform(80, 600, (80, 1)) * np.exp(rng.normal(0, .3, (1, 24)))
    means *= np.exp(.2*batch)
    means[:3] *= np.exp(.3*times)
    counts = rng.negative_binomial(5, 5/(5+means))
    data = {
        "counts.csv": [["gene_id", *samples], *[[g, *row.tolist()] for g,row in zip(genes, counts)]],
        "metadata.csv": [["sample_id", "biological_unit_id", "numeric_time"],
                         *[[s, "synthetic-unit-"+s, float(t)] for s,t in zip(samples, times)]],
        "full-design.csv": [["sample_id", "Intercept", "Time1", "Time2", "Time3", "Batch"],
                            *[[s, *row.tolist()] for s,row in zip(samples, full)]],
        "reduced-design.csv": [["sample_id", "Intercept", "Batch"], *[[s, *row.tolist()] for s,row in zip(samples, reduced)]],
    }
    for name, rows in data.items():
        with (inputs/name).open("x", newline="") as handle:
            csv.writer(handle).writerows(rows)
    identity = native_identity()
    save(root/"identity.json", identity)
    protocol = {"schema_version": "native_integration_checks/1", "seed": 2026090933, "generator": "NumPy PCG64",
                "scope": "synthetic software integration; not statistical or clinical qualification",
                "clinical_release_allowed": False, "model_service_calls": 0, "private_keys_saved": False,
                "identity": {key: identity[key] for key in ("implementation_sha256", "environment_sha256", "driver_sha256", "package_lock_sha256")},
                "cases": ["edger", "deseq", "edger-repeat", "deseq-repeat", "deadline", "invalid-counts", "missing-approval"]}
    save(root/"protocol.json", protocol)
    rows, numerical = [], {}
    for name in protocol["cases"]:
        case = root/name; case.mkdir(mode=0o700)
        engine = "deseq2_lrt" if name.startswith("deseq") or name == "deadline" else "edger_qlf"
        count_path = inputs/"counts.csv"
        if name == "invalid-counts":
            invalid = [row.copy() for row in data["counts.csv"]]; invalid[1][1] = "-1"
            count_path = case/"invalid-counts.csv"
            with count_path.open("x", newline="") as handle: csv.writer(handle).writerows(invalid)
        plan = NativeAnalysisPlan(plan_id="synthetic-native-"+name, revision=1, intended_use_ref="software-integration-only",
            study_id="synthetic-native-2026090933", data_classification="synthetic", engine=engine, normalization="rle",
            deseq2_fit_type="mean" if engine == "deseq2_lrt" else None, hypothesis_genes=tuple(genes[:32]), panel_genes=tuple(genes),
            sample_ids=tuple(samples), temporal_columns=(2, 3, 4), time_unit="simulated-days",
            basis_definition_ref="fixed Patsy centered natural cubic spline df=3 in saved protocol and matrices; balanced Batch nuisance",
            counts_sha256=hash_artifact(count_path)["sha256"], metadata_sha256=hash_artifact(inputs/"metadata.csv")["sha256"],
            full_design_sha256=hash_artifact(inputs/"full-design.csv")["sha256"],
            reduced_design_sha256=hash_artifact(inputs/"reduced-design.csv")["sha256"],
            max_memory_mb=1024, max_cpus=1, timeout_sec=1 if name == "deadline" else 60,
            **{key: identity[key] for key in ("implementation_sha256", "environment_sha256", "driver_sha256", "package_lock_sha256",
                                             "runtime_image_id", "platform")})
        approvals, signers = synthetic_approvals(plan)
        if name == "missing-approval": approvals = approvals[:1]
        save(case/"plan.json", plan.model_dump(mode="json"))
        save(case/"approvals.json", [a.model_dump(mode="json") for a in approvals])
        save(case/"trust-registry.json", [s.model_dump(mode="json") for s in signers])
        command = [sys.executable, "-m", "devharness_controlled.cli", "execute-native", "--plan", str(case/"plan.json"),
                   "--approvals", str(case/"approvals.json"), "--trust-registry", str(case/"trust-registry.json"),
                   "--counts", str(count_path), "--metadata", str(inputs/"metadata.csv"), "--full-design", str(inputs/"full-design.csv"),
                   "--reduced-design", str(inputs/"reduced-design.csv"), "--output", str(case/"execution")]
        save(case/"command.json", command)
        with (case/"coordinator.stdout").open("xb") as out, (case/"coordinator.stderr").open("xb") as err:
            completed = subprocess.run(command, stdin=subprocess.DEVNULL, stdout=out, stderr=err, check=False)
        saved_outcome = case/"execution/outcome.json"
        outcome = json.loads(saved_outcome.read_bytes()) if saved_outcome.is_file() else {"status": "infrastructure_incomplete"}
        receipt_path = case/"execution/receipt.json"
        verified = None
        if receipt_path.is_file():
            receipt = json.loads(receipt_path.read_bytes())
            verified = verify_controlled_execution(case/"execution/audit/manifest.json", receipt["root_hash"], signers)
            save(case/"independent-verification.json", verified)
        expected_complete = name in {"edger", "deseq", "edger-repeat", "deseq-repeat"}
        passed = outcome["status"] == ("analysis_complete_pending_review" if expected_complete else "failed")
        passed = passed and verified is not None and verified["valid"]
        transport_path = case/"execution/native-transport.json"
        transport = json.loads(transport_path.read_bytes()) if transport_path.is_file() else None
        if name == "deadline": passed = passed and transport is not None and transport["timed_out"] and transport["cleanup"]["removed"]
        if name == "invalid-counts": passed = passed and "raw_count" in outcome.get("error", "") and transport is None
        if name == "missing-approval": passed = passed and "approval" in outcome.get("error", "").lower() and transport is None
        if expected_complete and outcome["status"] == "analysis_complete_pending_review":
            native = json.loads((case/"execution/native-output/result.json").read_bytes())
            numerical[name] = {gene: {k: native["genes"][gene][k] for k in ("p_value", "q_value", "test_statistic")}
                               for gene in plan.hypothesis_genes}
            if name.endswith("-repeat"): passed = passed and numerical[name] == numerical.get(name[:-7])
        row = {"case": name, "engine": engine, "command_exit_code": completed.returncode,
               "expected_completed_analysis": expected_complete, "observed_status": outcome["status"],
               "check_passed": bool(passed), "error": outcome.get("error"), "verification": verified,
               "memory_peak_mb": None, "elapsed_seconds": outcome.get("elapsed_seconds"),
               "native_container_attempted": transport is not None,
               "container_removed": transport["cleanup"]["removed"] if transport else None}
        rows.append(row)
        print(json.dumps({k:v for k,v in row.items() if k != "verification"}), flush=True)
    after = native_identity()
    unchanged = all(after[key] == identity[key] for key in protocol["identity"])
    summary = {"schema_version": "native_integration_summary/1", "checks": rows, "protected_identity_unchanged": unchanged,
               "passed": unchanged and all(row["check_passed"] for row in rows), "clinical_release_allowed": False,
               "calibration_or_clinical_validation_established": False, "numerical_repeat_outputs": numerical,
               "scope": protocol["scope"]}
    save(root/"summary.json", summary)
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
