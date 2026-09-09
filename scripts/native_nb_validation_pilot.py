"""Execute the frozen synthetic native-NB pilot; never authorize clinical use.

The prepare step freezes all inputs before the run step starts native containers.
Every native attempt gets a fresh output directory and a durable start record.
No measured attempt is retried. External termination may leave an explicit start
record without an outcome; it cannot be silently interpreted as a successful run.
"""
from __future__ import annotations

import os

for _key in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "VECLIB_MAXIMUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ[_key] = "1"

import argparse
import csv
import hashlib
import json
import math
import platform
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import patsy
import scipy
from scipy.stats import beta, t

REPO = Path(__file__).resolve().parent.parent
PROTOCOL = REPO / "validation/native_nb_pilot_v1.json"
DRIVER = REPO / "devharness_server/r/native_nb.R"
LOCK = REPO / "results/native-nb-runtime-2026-09-07/package-lock/package-sources.lock.json"
DEFAULT_OUTPUT = REPO / "results/native-validation-pilot-2026-09-07"
DOCKER = "/usr/local/bin/docker"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def digest(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"Expected a regular non-symlink file: {path}")
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def save(path: Path, value: Any) -> None:
    """Create evidence once, flush it, and refuse overwriting another record."""
    raw = (json.dumps(value, indent=2, allow_nan=False) + "\n").encode()
    with path.open("xb") as handle:
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())


def load(path: Path) -> Any:
    def unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"Duplicate JSON key: {key}")
            result[key] = value
        return result

    def invalid_constant(value: str) -> Any:
        raise ValueError(f"Nonfinite JSON constant: {value}")

    if path.stat().st_size > 16 * 1024 * 1024:
        raise ValueError(f"Oversized JSON: {path}")
    return json.loads(path.read_text(), object_pairs_hook=unique_pairs, parse_constant=invalid_constant)


def csv_file(path: Path, header: list[str], rows: Any) -> None:
    with path.open("x", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(header)
        writer.writerows(rows)
        handle.flush()
        os.fsync(handle.fileno())


def prepare(root: Path) -> None:
    protocol = load(PROTOCOL)
    if protocol["schema_version"] != "native_nb_validation_pilot/1":
        raise ValueError("Unsupported pilot protocol")
    for source, expected in ((DRIVER, protocol["driver_sha256"]), (LOCK, protocol["dependency_lock_sha256"])):
        if digest(source) != expected:
            raise ValueError(f"Protocol source digest mismatch: {source}")
    host_versions = {"numpy": np.__version__, "patsy": patsy.__version__, "scipy": scipy.__version__}
    if any(protocol["expected_versions"][key] != value for key, value in host_versions.items()):
        raise ValueError("Host generator version differs from frozen protocol")
    if os.getuid() == 0:
        raise ValueError("Pilot requires a mapped nonroot host UID")
    root.mkdir(mode=0o700, parents=True, exist_ok=False)
    frozen = root / "frozen"
    frozen.mkdir(mode=0o700)
    sources = {"protocol.json": PROTOCOL, "native_nb.R": DRIVER, "runner.py": Path(__file__).resolve(), "package-sources.lock.json": LOCK}
    for name, source in sources.items():
        shutil.copyfile(source, frozen / name)
    save(frozen / "host.json", {"recorded_at_utc": utc_now(), "python": sys.version,
        "platform": platform.platform(), "machine": platform.machine(), "versions": host_versions,
        "uid": os.getuid(), "gid": os.getgid(), "argv": sys.argv,
        "scope": "Host preparation identity; amd64 native-container timings may include emulation"})
    cohorts = np.random.SeedSequence(protocol["seed"]).spawn(protocol["cohorts"])
    planned = []
    for cohort_index, cohort_seed in enumerate(cohorts, start=1):
        case = frozen / f"cohort-{cohort_index:03d}"
        inputs = case / "input"
        inputs.mkdir(parents=True, mode=0o700)
        streams = cohort_seed.spawn(4)
        mean_rng, library_rng, batch_rng, count_rng = [np.random.Generator(np.random.PCG64(seed)) for seed in streams]
        n, m = protocol["samples"], protocol["panel_genes"]
        times = np.repeat(np.asarray(protocol["times"], dtype=float), protocol["replicates_per_time"])
        batch = np.tile(np.array([0.0, 1.0]), n // 2)
        samples = [f"S{i:03d}" for i in range(n)]
        genes = [f"g{i:03d}" for i in range(m)]
        base = np.exp(mean_rng.uniform(math.log(2.0), math.log(600.0), m))
        log_library = library_rng.normal(0.0, 0.7, n)
        log_library -= np.mean(log_library)
        batch_beta = batch_rng.normal(0.0, 0.35, m)
        log_unoffset_mean = np.log(base)[:, None] + batch_beta[:, None] * batch[None, :]
        mu = np.exp(log_unoffset_mean + log_library[None, :])
        counts = count_rng.negative_binomial(5.0, 5.0 / (5.0 + mu))
        design = patsy.dmatrix("1 + batch + cr(time, df=3, constraints='center')", {"time": times, "batch": batch})
        full = np.asarray(design)
        reduced = full[:, :2].copy()
        full_rank, reduced_rank = np.linalg.matrix_rank(full), np.linalg.matrix_rank(reduced)
        temporal_coef = np.linalg.lstsq(full, log_unoffset_mean.T, rcond=None)[0][2:]
        reduced_error = log_unoffset_mean.T - reduced @ np.linalg.lstsq(reduced, log_unoffset_mean.T, rcond=None)[0]
        residualized_time = full[:, 2:] - reduced @ np.linalg.lstsq(reduced, full[:, 2:], rcond=None)[0]
        tolerance = protocol["design"]["null_space_absolute_tolerance"]
        checks = {
            "full_rank": int(full_rank), "reduced_rank": int(reduced_rank),
            "residualized_temporal_rank": int(np.linalg.matrix_rank(residualized_time)),
            "full_residual_df": n - int(full_rank), "reduced_residual_df": n - int(reduced_rank),
            "full_condition_number": float(np.linalg.cond(full)), "reduced_condition_number": float(np.linalg.cond(reduced)),
            "max_absolute_true_temporal_coefficient": float(np.max(np.abs(temporal_coef))),
            "max_absolute_reduced_log_mean_residual": float(np.max(np.abs(reduced_error))),
            "batch_counts_per_time": [[int(np.sum((times == value) & (batch == group))) for group in (0, 1)] for value in protocol["times"]],
            "native_patsy_column_order": design.design_info.column_names,
            "full_columns": protocol["design"]["full_columns"], "reduced_exact_subset": bool(np.array_equal(reduced, full[:, :2])),
            "counts_shape": list(counts.shape), "maximum_count": int(counts.max()),
            "positive_in_every_sample_genes": int(np.sum(np.all(counts > 0, axis=1))),
            "raw_count_conversion_performed": False,
        }
        if (full_rank != 5 or reduced_rank != 2 or checks["residualized_temporal_rank"] != 3
                or max(checks["full_condition_number"], checks["reduced_condition_number"]) > 1000
                or checks["max_absolute_true_temporal_coefficient"] > tolerance
                or checks["max_absolute_reduced_log_mean_residual"] > tolerance
                or checks["batch_counts_per_time"] != [[2, 2]] * 6):
            raise ValueError(f"Independent generator coefficient-space checks failed: cohort {cohort_index}")
        csv_file(inputs / "counts.csv", ["gene_id", *samples], ([gene, *row.tolist()] for gene, row in zip(genes, counts, strict=True)))
        csv_file(inputs / "metadata.csv", ["sample_id", "biological_unit_id", "numeric_time"],
            ([sample, f"cohort{cohort_index:03d}_unit{i:03d}", float(times[i])] for i, sample in enumerate(samples)))
        for name, matrix, columns in (("full_design.csv", full, protocol["design"]["full_columns"]), ("reduced_design.csv", reduced, protocol["design"]["reduced_columns"])):
            csv_file(inputs / name, ["sample_id", *columns], ([sample, *row.tolist()] for sample, row in zip(samples, matrix, strict=True)))
        save(case / "generator.json", {"cohort": cohort_index, "cohort_seed": cohort_seed.state,
            "stream_seed_states": [seed.state for seed in streams], "base_means": base.tolist(),
            "log_library_factors": log_library.tolist(), "batch_log_coefficients": batch_beta.tolist(),
            "true_temporal_coefficients": "exactly zero", "scope": "Synthetic generator truth retained outside engine input mount"})
        save(case / "design-checks.json", checks)
        for engine in protocol["engines"]:
            request = {"schema_version": "native_nb_request/1", "engine": engine,
                "counts_csv": "/input/counts.csv", "metadata_csv": "/input/metadata.csv",
                "full_design_csv": "/input/full_design.csv", "reduced_design_csv": "/input/reduced_design.csv",
                "hypothesis_genes": genes[:protocol["hypothesis_genes"]], "temporal_columns": [3, 4, 5],
                "normalization": "rle", "data_classification": "synthetic"}
            if engine == "deseq2_lrt":
                request["deseq2_fit_type"] = protocol["deseq2_fit_type"]
            save(inputs / f"request-{engine}.json", request)
            planned.append({"cohort": cohort_index, "engine": engine, "input": str(inputs)})
    save(frozen / "schedule.json", planned)
    frozen_hashes = {str(path.relative_to(root)): digest(path) for path in sorted(frozen.rglob("*")) if path.is_file()}
    for path in frozen.rglob("*"):
        path.chmod(0o555 if path.is_dir() else 0o444)
    frozen.chmod(0o555)
    save(root / "FREEZE.json", {"schema_version": "native_pilot_freeze/1", "frozen_at_utc": utc_now(),
        "before_any_native_fitting": True, "sources": {str(path): digest(path) for path in sources.values()},
        "frozen_hashes": frozen_hashes, "planned_attempts": len(planned), "clinical_release_allowed": False})
    (root / "FREEZE.json").chmod(0o444)
    print(json.dumps({"prepared": str(root), "protocol_sha256": digest(PROTOCOL), "freeze_sha256": digest(root / "FREEZE.json"), "planned_attempts": len(planned)}), flush=True)


def verify_freeze(root: Path) -> list[str]:
    frozen = load(root / "FREEZE.json")
    mismatches = []
    for path, expected in {**frozen["sources"], **{str(root / key): value for key, value in frozen["frozen_hashes"].items()}}.items():
        try:
            if digest(Path(path)) != expected:
                mismatches.append(path)
        except (OSError, ValueError):
            mismatches.append(path)
    return mismatches


def captured_command(folder: Path, label: str, command: list[str], timeout: float = 30) -> dict[str, Any]:
    save(folder / f"{label}.command.json", command)
    start = time.monotonic()
    with (folder / f"{label}.stdout.log").open("xb") as stdout, (folder / f"{label}.stderr.log").open("xb") as stderr:
        try:
            done = subprocess.run(command, stdout=stdout, stderr=stderr, timeout=timeout, check=False)
            result = {"exit_code": done.returncode, "error": None}
        except (OSError, subprocess.TimeoutExpired) as error:
            result = {"exit_code": None, "error": f"{type(error).__name__}: {error}"}
    result["elapsed_seconds"] = time.monotonic() - start
    save(folder / f"{label}.outcome.json", result)
    return result


def bh_adjust(ps: list[float | None], family_size: int) -> list[float | None]:
    order = sorted((p, index) for index, p in enumerate(ps) if p is not None)
    result: list[float | None] = [None] * len(ps)
    running = 1.0
    for position in range(len(order) - 1, -1, -1):
        p, index = order[position]
        running = min(running, family_size * p / (position + 1))
        result[index] = running
    return result


def check_result(path: Path, engine: str, protocol: dict[str, Any], exit_code: int | None) -> dict[str, Any]:
    checked: dict[str, Any] = {"validation_errors": [], "native_status": None, "usable_hypotheses": 0,
        "missing_hypotheses": protocol["hypothesis_genes"], "nominal_rejections": 0, "bh_discoveries": 0,
        "all_hypotheses_usable": False, "observed_versions": None, "error": None}
    errors = checked["validation_errors"]
    if not path.is_file():
        errors.append("result_file_missing")
        return checked
    try:
        result = load(path)
        checked.update(native_status=result.get("status"), observed_versions=result.get("actual_versions"), error=result.get("error"))
        if result.get("schema_version") != "native_nb_result/1" or result.get("engine") != engine:
            errors.append("result_identity_mismatch")
        if result.get("clinical_release_allowed") is not False or result.get("qualification_status") != "candidate_unqualified":
            errors.append("incorrect_qualification_claim")
        keys = ("R", "limma", "BiocParallel", "jsonlite", "edgeR" if engine == "edger_qlf" else "DESeq2")
        if any(result.get("actual_versions", {}).get(key) != protocol["expected_versions"][key] for key in keys):
            errors.append("native_version_mismatch")
        hypotheses = [f"g{i:03d}" for i in range(protocol["hypothesis_genes"])]
        records = result.get("genes", {})
        if not records and result.get("status") == "failed":
            if exit_code == 0:
                errors.append("failed_result_with_zero_exit")
            return checked
        if list(records) != hypotheses or result.get("hypothesis_genes") != hypotheses:
            errors.append("ordered_hypothesis_family_mismatch")
            return checked
        ps, qs = [], []
        convergence = {}
        for gene in hypotheses:
            record = records[gene]
            p, q = record.get("p_value"), record.get("q_value")
            if record.get("status") == "ok":
                if not all(type(value) in (float, int) and math.isfinite(value) for value in (p, q)) or not 0 <= p <= q <= 1:
                    errors.append(f"invalid_usable_probability:{gene}")
                    p = q = None
                if record.get("test_df_num") != 3:
                    errors.append(f"wrong_temporal_test_df:{gene}")
                if record.get("test_distribution") != ("quasi_F" if engine == "edger_qlf" else "chi_square"):
                    errors.append(f"wrong_test_distribution:{gene}")
                native_p = record.get("native_p_value")
                if p is not None and (type(native_p) not in (float, int) or not math.isfinite(native_p) or abs(native_p - p) > 1e-12):
                    errors.append(f"native_usable_probability_mismatch:{gene}")
            else:
                if p is not None or q is not None:
                    errors.append(f"failed_hypothesis_has_usable_probability:{gene}")
                p = q = None
            ps.append(p)
            qs.append(q)
            for field in ("full_fit_convergence", "reduced_fit_convergence"):
                value = record.get(field, {})
                label = f"{field}:{value.get('scope')}:{value.get('converged')}"
                convergence[label] = convergence.get(label, 0) + 1
        expected_qs = bh_adjust(ps, protocol["hypothesis_genes"])
        tolerance = protocol["reporting"]["independent_bh_check_absolute_tolerance"]
        if any((q is None) != (expected is None) or (q is not None and abs(q - expected) > tolerance) for q, expected in zip(qs, expected_qs, strict=True)):
            errors.append("independent_bh_adjustment_mismatch")
        checked.update(usable_hypotheses=sum(p is not None for p in ps), missing_hypotheses=sum(p is None for p in ps),
            nominal_rejections=sum(p is not None and p <= 0.05 for p in ps), bh_discoveries=sum(q is not None and q <= 0.05 for q in qs),
            p_values=ps, q_values=qs, convergence=convergence, conditions=result.get("conditions", []),
            conditions_truncated=result.get("conditions_truncated"), native_elapsed_seconds=result.get("elapsed_seconds"))
        if result.get("conditions_truncated"):
            errors.append("native_conditions_truncated")
        checked["all_hypotheses_usable"] = not errors and all(p is not None for p in ps) and result.get("status") == "ok" and exit_code == 0
        if result.get("status") == "ok" and not checked["all_hypotheses_usable"]:
            errors.append("native_success_not_confirmed")
    except (ValueError, TypeError, KeyError, AttributeError) as error:
        errors.append(f"unreadable_result:{type(error).__name__}:{error}")
    return checked


def run_attempt(root: Path, item: dict[str, Any], protocol: dict[str, Any]) -> dict[str, Any]:
    cohort, engine = item["cohort"], item["engine"]
    attempt = root / "attempts" / f"cohort-{cohort:03d}" / engine
    attempt.mkdir(parents=True, mode=0o700, exist_ok=False)
    output = attempt / "output"
    output.mkdir(mode=0o700)
    name = f"devharness-pilot-{digest(root / 'FREEZE.json')[:12]}-{cohort:03d}-{engine.replace('_', '-')}"
    command = [DOCKER, "run", "--pull", "never", "--name", name, "--platform", "linux/amd64", "--network", "none", "--read-only",
        "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--pids-limit", "64", "--memory", "2g", "--memory-swap", "2g", "--cpus", "2",
        "--user", f"{os.getuid()}:{os.getgid()}", "--tmpfs", "/tmp:rw,nosuid,nodev,size=128m,mode=1777"]
    for key in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "VECLIB_MAXIMUM_THREADS", "NUMEXPR_NUM_THREADS"):
        command.extend(["--env", f"{key}=1"])
    command.extend(["--mount", f"type=bind,source={item['input']},target=/input,readonly",
        "--mount", f"type=bind,source={output},target=/output",
        "--mount", f"type=bind,source={root / 'frozen/native_nb.R'},target=/driver.R,readonly",
        protocol["image_id"], "--vanilla", "/driver.R", "--config", f"/input/request-{engine}.json", "--output", "/output/result.json"])
    save(attempt / "command.json", command)
    save(attempt / "STARTED.json", {"cohort": cohort, "engine": engine, "started_at_utc": utc_now(), "status": "started",
        "container_name": name, "coordinator_pid": os.getpid(), "freeze_sha256": digest(root / "FREEZE.json")})
    start, returncode, failure = time.monotonic(), None, None
    with (attempt / "stdout.log").open("xb") as stdout, (attempt / "stderr.log").open("xb") as stderr:
        try:
            process = subprocess.Popen(command, stdout=stdout, stderr=stderr, start_new_session=True)
            save(attempt / "PROCESS.json", {"pid": process.pid, "container_name": name, "launched_at_utc": utc_now()})
            while process.poll() is None:
                if time.monotonic() - start > protocol["runtime"]["timeout_seconds"]:
                    failure = "watchdog_timeout"
                if any((attempt / log).stat().st_size > protocol["runtime"]["logs_max_bytes_each"] for log in ("stdout.log", "stderr.log")):
                    failure = "watchdog_log_limit"
                if failure:
                    captured_command(attempt, "watchdog-kill", [DOCKER, "kill", "--signal", "KILL", name])
                    try:
                        process.wait(timeout=15)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=15)
                    break
                time.sleep(0.2)
            returncode = process.returncode
        except (OSError, subprocess.TimeoutExpired) as error:
            failure = f"{type(error).__name__}: {error}"
    elapsed = time.monotonic() - start
    inspect = captured_command(attempt, "container-state", [DOCKER, "inspect", "--format", "{{json .State}}", name])
    state = None
    if inspect["exit_code"] == 0:
        try:
            state = load(attempt / "container-state.stdout.log")
        except (ValueError, OSError):
            failure = failure or "container_state_unreadable"
    stopped = isinstance(state, dict) and state.get("Running") is False
    if stopped:
        captured_command(attempt, "container-remove", [DOCKER, "rm", name])
    else:
        failure = failure or "container_termination_unverified"
    checks = check_result(output / "result.json", engine, protocol, returncode)
    mismatches = verify_freeze(root)
    outcome = {"cohort": cohort, "engine": engine, "completed_at_utc": utc_now(), "exit_code": returncode,
        "wall_seconds_including_launch": elapsed, "supervisor_failure": failure, "container_state": state,
        "container_stopped_verified": stopped, "integrity_mismatches": mismatches, "result": checks,
        "memory_peak_mb": None, "memory_scope": protocol["reporting"]["memory_scope"],
        "command_sha256": digest(attempt / "command.json"), "result_sha256": digest(output / "result.json") if (output / "result.json").is_file() else None}
    save(attempt / "OUTCOME.json", outcome)
    print(json.dumps({"cohort": cohort, "engine": engine, "exit_code": returncode, "native_status": checks["native_status"],
        "seconds": round(elapsed, 3), "usable": checks["usable_hypotheses"], "nominal_rejections": checks["nominal_rejections"],
        "bh_discoveries": checks["bh_discoveries"], "supervisor_failure": failure, "validation_errors": checks["validation_errors"]}), flush=True)
    return outcome


def run(root: Path) -> None:
    if (root / "SEAL.json").exists():
        raise ValueError("This measured pilot is sealed; no attempts may be rerun")
    mismatches = verify_freeze(root)
    if mismatches:
        raise ValueError(f"Frozen inputs or source changed before execution: {mismatches}")
    protocol = load(root / "frozen/protocol.json")
    preflights = root / "preflights"
    preflights.mkdir(exist_ok=True, mode=0o700)
    preflight = preflights / f"check-{len(list(preflights.iterdir())) + 1:03d}"
    preflight.mkdir(mode=0o700)
    check = captured_command(preflight, "image", [DOCKER, "image", "inspect", "--format", '{{json .Id}}', protocol["image_id"]])
    if check["exit_code"] != 0:
        print(json.dumps({"execution_blocked": str(preflight), "reason": "Docker image preflight failed; saved exact stdout/stderr; no numerical attempt substituted"}), flush=True)
        raise SystemExit(2)
    if load(preflight / "image.stdout.log") != protocol["image_id"]:
        raise ValueError("Actual Docker image identity differs from protocol")
    for item in load(root / "frozen/schedule.json"):
        attempt = root / "attempts" / f"cohort-{item['cohort']:03d}" / item["engine"]
        if attempt.exists():
            raise ValueError(f"An attempt already exists and cannot be repeated: {attempt}")
        mismatches = verify_freeze(root)
        if mismatches:
            save(root / "STOPPED.json", {"at_utc": utc_now(), "reason": "source_or_input_hash_changed", "mismatches": mismatches, "next_scheduled": item})
            break
        outcome = run_attempt(root, item, protocol)
        if outcome["integrity_mismatches"] or not outcome["container_stopped_verified"]:
            save(root / "STOPPED.json", {"at_utc": utc_now(), "reason": "integrity_or_container_termination_failure", "last_cohort": item})
            break
    summarize(root)


def mean_interval(values: list[float]) -> dict[str, Any]:
    if len(values) < 2:
        return {"mean": float(np.mean(values)) if values else None, "ci95": None, "cohorts": len(values)}
    mean = float(np.mean(values))
    se = float(np.std(values, ddof=1) / math.sqrt(len(values)))
    half_width = float(t.ppf(0.975, len(values) - 1) * se)
    return {"mean": mean, "ci95": [mean - half_width, mean + half_width], "monte_carlo_standard_error": se, "cohorts": len(values)}


def binomial_interval(events: int, total: int) -> dict[str, Any]:
    if not total:
        return {"events": events, "cohorts": 0, "rate": None, "ci95": None}
    lower = 0.0 if events == 0 else float(beta.ppf(0.025, events, total - events + 1))
    upper = 1.0 if events == total else float(beta.ppf(0.975, events + 1, total - events))
    return {"events": events, "cohorts": total, "rate": events / total, "ci95": [lower, upper]}


def summarize(root: Path) -> None:
    protocol = load(root / "frozen/protocol.json")
    summary: dict[str, Any] = {"scope": protocol["scope"], "clinical_release_allowed": False,
        "generated_at_utc": utc_now(), "planned_cohorts_per_engine": protocol["cohorts"],
        "planned_native_attempts": protocol["cohorts"] * len(protocol["engines"]),
        "protocol_sha256": digest(root / "frozen/protocol.json"), "driver_sha256": digest(root / "frozen/native_nb.R"),
        "freeze_sha256": digest(root / "FREEZE.json"), "image_id": protocol["image_id"], "reporting": protocol["reporting"],
        "source_integrity_mismatches": verify_freeze(root), "engines": {}}
    all_complete: dict[str, dict[int, dict[str, Any]]] = {}
    for engine in protocol["engines"]:
        attempts = sorted((root / "attempts").glob(f"*/{engine}/STARTED.json"))
        outcomes = [load(path.parent / "OUTCOME.json") for path in attempts if (path.parent / "OUTCOME.json").is_file()]
        complete = [x for x in outcomes if x["result"]["all_hypotheses_usable"] and not x["supervisor_failure"] and not x["integrity_mismatches"]]
        all_complete[engine] = {x["cohort"]: x for x in complete}
        observed = [x for x in outcomes if not x["result"]["validation_errors"] and not x["integrity_mismatches"]]
        valid_ps = sum(x["result"]["usable_hypotheses"] for x in observed)
        rejection_count = sum(x["result"]["nominal_rejections"] for x in observed)
        total_ps = len(attempts) * protocol["hypothesis_genes"]
        missing_ps = total_ps - valid_ps
        family_events = sum(x["result"]["bh_discoveries"] > 0 for x in complete)
        incomplete = len(attempts) - len(complete)
        nominal = mean_interval([x["result"]["nominal_rejections"] / protocol["hypothesis_genes"] for x in complete]) if not incomplete else {"mean": None, "ci95": None, "reason": "Incomplete cohorts; no selected-success calibration interval"}
        if nominal.get("ci95") is not None:
            nominal["ci95"] = [max(0.0, nominal["ci95"][0]), min(1.0, nominal["ci95"][1])]
        family = binomial_interval(family_events, len(complete))
        failures: dict[str, int] = {}
        for x in outcomes:
            reason = x["supervisor_failure"] or x["result"]["error"] or x["result"]["validation_errors"] or (None if x in complete else "partial_hypothesis_failure")
            if reason:
                key = json.dumps(reason, sort_keys=True)
                failures[key] = failures.get(key, 0) + 1
        warnings = []
        if nominal.get("ci95") is not None and nominal["ci95"][0] > 0.05:
            warnings.append("Unadjusted cohort-based nominal-rejection interval lies above 0.05")
        if family["ci95"] is not None and family["ci95"][0] > 0.05:
            warnings.append("Unadjusted complete-cohort all-null BH occurrence interval lies above 0.05")
        summary["engines"][engine] = {"attempted_cohorts": len(attempts), "terminal_outcomes": len(outcomes), "completed_usable_cohorts": len(complete),
            "failed_or_incomplete_cohorts": incomplete, "unattempted_cohorts": protocol["cohorts"] - len(attempts),
            "usable_p_values": valid_ps, "missing_p_values": missing_ps, "nominal_rejections": rejection_count,
            "nominal_observed_rate_over_attempted_hypotheses": rejection_count / total_ps if total_ps else None,
            "nominal_missingness_bounds_over_attempted_hypotheses": [rejection_count / total_ps, (rejection_count + missing_ps) / total_ps] if total_ps else None,
            "nominal_cohort_interval": nominal, "all_null_bh_occurrence_complete_cohorts": family,
            "bh_occurrence_missingness_bounds_all_attempts": [family_events / len(attempts), (family_events + incomplete) / len(attempts)] if attempts else None,
            "wall_seconds": {"total": sum(x["wall_seconds_including_launch"] for x in outcomes),
                "median": float(np.median([x["wall_seconds_including_launch"] for x in outcomes])) if outcomes else None,
                "max": max((x["wall_seconds_including_launch"] for x in outcomes), default=None)},
            "observed_versions": [json.loads(s) for s in sorted({json.dumps(x["result"]["observed_versions"], sort_keys=True) for x in outcomes})],
            "failure_taxonomy": failures, "calibration_warnings": warnings, "memory_peak_mb": None}
    paired = sorted(set(all_complete["edger_qlf"]) & set(all_complete["deseq2_lrt"]))
    summary["paired_complete_cohort_comparison"] = {"direction": "deseq2_lrt minus edger_qlf", "cohorts": len(paired),
        "nominal_rejection_fraction_difference": mean_interval([(all_complete["deseq2_lrt"][i]["result"]["nominal_rejections"] - all_complete["edger_qlf"][i]["result"]["nominal_rejections"]) / protocol["hypothesis_genes"] for i in paired]),
        "bh_any_discovery_difference": mean_interval([float(all_complete["deseq2_lrt"][i]["result"]["bh_discoveries"] > 0) - float(all_complete["edger_qlf"][i]["result"]["bh_discoveries"] > 0) for i in paired])}
    save(root / "summary.json", summary)
    lines = ["# Native NB pilot: timing, transport and calibration warnings", "", protocol["scope"], "", "Clinical release remains blocked. Both engines used the same frozen synthetic raw counts and fixed designs.", "",
        "| Engine | Usable/attempted cohorts | Nominal rejections / attempted hypotheses | All-null BH-positive / complete cohorts | Median wall seconds |", "|---|---:|---:|---:|---:|"]
    for engine, values in summary["engines"].items():
        family = values["all_null_bh_occurrence_complete_cohorts"]
        lines.append(f"| {engine} | {values['completed_usable_cohorts']}/{values['attempted_cohorts']} | {values['nominal_rejections']}/{values['attempted_cohorts'] * 32} | {family['events']}/{family['cohorts']} | {values['wall_seconds']['median']} |")
    lines.extend(["", "See summary.json for cohort-level intervals, paired differences, missingness bounds, versions, failures and warnings. Intervals are descriptive and unadjusted; no clinical acceptance margin was invented.", "", "The 2 GiB setting is a container memory limit. Peak memory is unknown. Timings include Docker startup and amd64 emulation on the arm64 host.", "", "The main 2,000-cohort-per-cell matrix, stress scenarios, assay context, clinical evidence and independent qualification remain outstanding."])
    with (root / "report.md").open("x") as handle:
        handle.write("\n".join(lines) + "\n")
    save(root / "SEAL.json", {"sealed_at_utc": utc_now(), "scope": "Local hashes of retained pilot evidence, not authenticated protected retention",
        "files": {str(path.relative_to(root)): digest(path) for path in sorted(root.rglob("*")) if path.is_file()}, "clinical_release_allowed": False})
    print(json.dumps({"summary": str(root / "summary.json"), "seal": str(root / "SEAL.json"), "engines": summary["engines"]}), flush=True)


def self_check() -> None:
    assert bh_adjust([0.01, 0.04, 0.03, None], 4) == [0.04, 0.05333333333333334, 0.05333333333333334, None]
    assert bh_adjust([None] * 32, 32) == [None] * 32
    assert binomial_interval(0, 20)["ci95"][0] == 0.0
    assert binomial_interval(20, 20)["ci95"][1] == 1.0
    assert mean_interval([0.0, 0.0])["ci95"] == [0.0, 0.0]
    print("Pilot analysis helper checks passed; no native or synthetic cohort measurements executed.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "run", "summarize", "verify", "self-check"))
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    if not args.output.is_absolute():
        parser.error("--output must be absolute")
    root = args.output.resolve()
    os.umask(0o077)
    if args.action == "self-check":
        self_check()
    elif args.action == "verify":
        mismatches = verify_freeze(root)
        if (root / "SEAL.json").exists():
            mismatches.extend(str(root / path) for path, expected in load(root / "SEAL.json")["files"].items() if digest(root / path) != expected)
        print(json.dumps({"integrity_mismatches": mismatches, "clinical_release_allowed": False}))
        if mismatches:
            raise SystemExit(1)
    else:
        {"prepare": prepare, "run": run, "summarize": summarize}[args.action](root)


if __name__ == "__main__":
    main()
