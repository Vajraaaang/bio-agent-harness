"""Frozen synthetic matrix through the real signed 0.4.4 native CLI.

Preparation creates all cohorts, public fixture trust, plans and signatures before
any fitting. No private keys are written. Current controlled source is not used
by participant CLI processes. Results are diagnostic, never clinical approvals.
"""
from __future__ import annotations

import os
for _key in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "VECLIB_MAXIMUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ[_key] = "1"

import argparse
import base64
from concurrent.futures import ThreadPoolExecutor
import csv
from datetime import datetime, timedelta, timezone
import hashlib
import importlib
import io
import json
import math
from pathlib import Path, PurePosixPath
import platform
import re
import shutil
import stat
import subprocess
import sys
import time
import zipfile

import numpy as np
import patsy
from scipy.stats import beta, chi2, f
from statsmodels.stats.multitest import multipletests

REPO = Path(__file__).resolve().parent.parent
PROTOCOL = REPO / "validation/native_nb_matrix_v1.json"
WHEEL = REPO / "results/native-controlled-2026-09-07/dist/devharness-0.4.4-py3-none-any.whl"
DEFAULT_ROOT = REPO / "results/native-matrix-2026-09-07"
THREADS = ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "VECLIB_MAXIMUM_THREADS", "NUMEXPR_NUM_THREADS")


def now():
    return datetime.now(timezone.utc).isoformat()


def sha(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"Expected regular file: {path}")
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for part in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(part)
    return h.hexdigest()


def put(path, value):
    raw = value if isinstance(value, bytes) else (json.dumps(value, indent=2, allow_nan=False) + "\n").encode()
    with Path(path).open("xb") as handle:
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())


def read(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 16 * 1024 * 1024:
        raise ValueError(f"Missing or oversized regular JSON: {path}")
    def unique(pairs):
        output = {}
        for key, value in pairs:
            if key in output:
                raise ValueError("Duplicate JSON key")
            output[key] = value
        return output
    def nonfinite(value):
        raise ValueError("Nonfinite JSON: " + value)
    return json.loads(path.read_bytes(), object_pairs_hook=unique, parse_constant=nonfinite)


def cohort_arrays(protocol, cell, sequence):
    streams = sequence.spawn(4)
    mean_rng, library_rng, batch_rng, count_rng = [np.random.Generator(np.random.PCG64(seed)) for seed in streams]
    n, m = cell["samples"], protocol["panel_genes"]
    times = np.repeat(np.asarray(protocol["times"], dtype=float), n // 6)
    batch = np.tile([0.0, 1.0], n // 2)
    mean_settings = protocol["gene_mean_distribution"]
    base = np.exp(mean_rng.uniform(math.log(mean_settings["lower"]), math.log(mean_settings["upper"]), m))
    log_library = library_rng.normal(0.0, protocol["log_library_distribution"]["sd"], n)
    log_library -= log_library.mean()
    batch_beta = batch_rng.normal(0.0, protocol["batch_coefficient_distribution"]["sd"], m)
    effects = np.zeros((m, n))
    shapes = ["null"] * protocol["hypothesis_genes"]
    if cell["regime"] == "mixed":
        for group in protocol["mixed_effects"]["alternatives"]:
            curve = np.asarray(group["curve"])[times.astype(int)] * protocol["mixed_effects"]["amplitude"]
            effects[group["indices"], :] = curve
            for index in group["indices"]:
                shapes[index] = group["shape"]
    log_mean = np.log(base)[:, None] + batch_beta[:, None] * batch[None, :] + effects
    mu = np.exp(log_mean + log_library[None, :])
    size = 1.0 / cell["alpha"]
    counts = count_rng.negative_binomial(size, size / (size + mu))
    full = np.asarray(patsy.dmatrix(protocol["design"]["formula"], {"batch": batch, "time": times}))
    reduced = full[:, :2].copy()
    null_panel = np.all(effects == 0, axis=1)
    null_coef = np.linalg.lstsq(full, log_mean[null_panel].T, rcond=None)[0][2:]
    error = float(np.max(np.abs(null_coef)))
    if (np.linalg.matrix_rank(full) != 5 or np.linalg.matrix_rank(reduced) != 2
            or np.linalg.cond(full) > protocol["design"]["condition_number_max"]
            or error > protocol["design"]["null_coefficient_absolute_tolerance"]):
        raise ValueError("Generator design/null coefficient-space check failed")
    truth = {"cohort_seed_state": sequence.state, "stream_seed_states": [seed.state for seed in streams],
        "base_means": base.tolist(), "log_library_factors": log_library.tolist(), "batch_log_coefficients": batch_beta.tolist(),
        "null_hypotheses": [shape == "null" for shape in shapes], "hypothesis_shapes": shapes,
        "max_absolute_null_temporal_coefficient": error, "full_condition_number": float(np.linalg.cond(full)),
        "full_rank": 5, "reduced_rank": 2, "full_residual_df": n - 5,
        "scope": "Synthetic generating truth; never mounted in native container"}
    return {"counts": counts, "times": times, "batch": batch, "effects": effects,
            "full": full, "reduced": reduced, "truth": truth}


def cohort_scores(p_values, q_values, null, shapes):
    if not (len(p_values) == len(q_values) == len(null) == len(shapes) == 32):
        raise ValueError("Complete fixed family required")
    if any(type(value) not in (float, int) or not math.isfinite(value) or not 0 <= value <= 1 for value in [*p_values, *q_values]):
        raise ValueError("Missing/invalid probability cannot be scored as complete")
    if any(q < p for p, q in zip(p_values, q_values, strict=True)):
        raise ValueError("q must be at least p")
    null = np.asarray(null, dtype=bool)
    rejected = np.asarray(q_values) <= 0.05
    false = int(np.sum(rejected & null))
    true = int(np.sum(rejected & ~null))
    total = false + true
    return {"null_rejection_fraction": float(np.mean(np.asarray(p_values)[null] <= 0.05)),
        "null_nominal_rejections": int(np.sum(np.asarray(p_values)[null] <= 0.05)),
        "null_hypotheses": int(np.sum(null)), "discoveries": total, "false_discoveries": false,
        "true_discoveries": true, "fdp": false / max(total, 1),
        "power": true / int(np.sum(~null)) if np.any(~null) else None,
        "shape_power": {shape: float(np.mean(rejected[np.asarray(shapes) == shape])) for shape in sorted(set(shapes) - {"null"})}}


def bounded_summary(values, *, low=0.0, high=1.0, binary=False):
    values = list(values)
    if any(not math.isfinite(value) or not low <= value <= high for value in values):
        raise ValueError("Invalid bounded cohort metric")
    if not values:
        return {"cohorts": 0, "mean": None, "ci95": None, "method": "unavailable"}
    n, mean = len(values), float(np.mean(values))
    if binary:
        if any(value not in (0, 1) for value in values):
            raise ValueError("Exact binomial interval requires Bernoulli cohort values")
        events = int(sum(values))
        ci = [0.0 if events == 0 else float(beta.ppf(0.025, events, n - events + 1)),
              1.0 if events == n else float(beta.ppf(0.975, events + 1, n - events))]
        method = "exact_Clopper_Pearson_95_percent"
    else:
        width = (high - low) * math.sqrt(math.log(2 / 0.05) / (2 * n))
        ci, method = [max(low, mean - width), min(high, mean + width)], "distribution_free_Hoeffding_95_percent"
    return {"cohorts": n, "mean": mean, "ci95": ci, "method": method,
            "scope": "Independent cohorts; descriptive bound, no acceptance margin"}


def primary_warning(values, *, binary=False):
    values = list(values)
    alpha = 0.05 / 24
    output = {"family_slots": 24, "one_sided_alpha": alpha, "nominal_reference": 0.05,
              "assessable": len(values) == 20, "warning": None, "lower_bound": None}
    if len(values) != 20:
        output["reason"] = "All 20 prespecified cohorts must be usable; missingness is not a pass"
        return output
    if binary:
        events = int(sum(values))
        lower = 0.0 if events == 0 else float(beta.ppf(alpha, events, len(values) - events + 1))
        method = "exact_binomial_Bonferroni_lower_bound"
    else:
        lower = max(0.0, float(np.mean(values)) - math.sqrt(math.log(1 / alpha) / (2 * len(values))))
        method = "Hoeffding_Bonferroni_lower_bound"
    output.update(warning=lower > 0.05, lower_bound=lower, method=method,
                  interpretation="A warning is evidence against nominal calibration; no warning does not establish qualification")
    return output


def child_environment(root):
    for name in ("DOCKER_HOST", "DOCKER_TLS_VERIFY", "DOCKER_CERT_PATH", "DOCKER_CONTEXT"):
        if os.environ.get(name):
            raise ValueError("Unapproved Docker environment override: " + name)
    env = {key: os.environ[key] for key in ("PATH", "HOME", "USER", "LOGNAME", "TMPDIR", "LANG", "LC_ALL", "TZ") if key in os.environ}
    env.update(PYTHONPATH=str(root / "frozen/runtime"), PYTHONNOUSERSITE="1", PYTHONDONTWRITEBYTECODE="1", MPLBACKEND="Agg",
               MPLCONFIGDIR=str(root / "plot-cache"))
    env.update({name: "1" for name in THREADS})
    return env


def call(root, directory, arguments, *, code=False, timeout=150):
    directory.mkdir(parents=True, mode=0o700, exist_ok=False)
    command = [sys.executable, "-B", "-s", *( ["-c", arguments] if code else ["-m", "devharness_controlled.cli", *arguments])]
    cwd = root / "frozen/runtime"
    put(directory / "command.json", {"argv": command, "cwd": str(cwd), "PYTHONPATH": str(cwd),
        "expected_cli_origin": str(cwd / "devharness_controlled/cli.py")})
    put(directory / "STARTED.json", {"at_utc": now(), "coordinator_pid": os.getpid(), "scope": "Actual frozen CLI process"})
    start, error, exit_code = time.monotonic(), None, None
    with (directory / "stdout.log").open("xb") as stdout, (directory / "stderr.log").open("xb") as stderr:
        try:
            process = subprocess.Popen(command, cwd=cwd, env=child_environment(root), stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr)
            put(directory / "PROCESS.json", {"pid": process.pid, "at_utc": now()})
            try:
                exit_code = process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=15)
                error = "outer_cli_watchdog_timeout"
        except (OSError, subprocess.TimeoutExpired) as failure:
            error = f"{type(failure).__name__}: {failure}"
    result = {"exit_code": exit_code, "error": error, "wall_seconds": time.monotonic() - start, "finished_at_utc": now()}
    put(directory / "OUTCOME.json", result)
    return result


def extract_runtime(root, protocol):
    if sha(WHEEL) != protocol["wheel"]["sha256"]:
        raise ValueError("Sealed wheel digest mismatch")
    target = root / "frozen/runtime"
    if target.exists():
        expected = read(root / "extraction.json")["files"]
        actual = {str(p.relative_to(target)): sha(p) for p in target.rglob("*") if p.is_file()}
        if expected != actual:
            raise ValueError("Prepared frozen runtime changed")
        return
    target.mkdir(parents=True, mode=0o700)
    with zipfile.ZipFile(WHEEL) as archive:
        if len(archive.infolist()) > 5000 or sum(info.file_size for info in archive.infolist()) > 100 * 1024 * 1024:
            raise ValueError("Unexpected wheel bounds")
        record_name = "devharness-0.4.4.dist-info/RECORD"
        rows = list(csv.reader(io.StringIO(archive.read(record_name).decode())))
        hashes = {}
        for info in archive.infolist():
            path = PurePosixPath(info.filename)
            if path.is_absolute() or ".." in path.parts or stat.S_ISLNK(info.external_attr >> 16):
                raise ValueError("Unsafe wheel member")
            if info.is_dir():
                continue
            raw = archive.read(info)
            hashes[info.filename] = hashlib.sha256(raw).hexdigest()
            destination = target.joinpath(*path.parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            put(destination, raw)
        for name, encoded, size in rows:
            if name == record_name:
                continue
            algorithm, expected = encoded.split("=", 1)
            actual = base64.urlsafe_b64encode(bytes.fromhex(hashes[name])).decode().rstrip("=")
            if algorithm != "sha256" or expected != actual or (target / name).stat().st_size != int(size):
                raise ValueError("Wheel RECORD verification failed: " + name)
        if set(hashes) != {row[0] for row in rows}:
            raise ValueError("Wheel contains unrecorded files")
    if sha(target / "devharness_server/r/native_nb.R") != protocol["wheel"]["driver_sha256"]:
        raise ValueError("Wheel has wrong native driver")
    put(root / "extraction.json", {"wheel_sha256": sha(WHEEL), "files": hashes, "RECORD_verified": True})


def write_csv(path, header, rows):
    with path.open("x", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(header)
        writer.writerows(rows)
        handle.flush()
        os.fsync(handle.fileno())


def prepare(root):
    protocol = read(PROTOCOL)
    if (root / "FREEZE.json").exists():
        raise ValueError("Matrix already frozen; do not prepare or sign it again")
    root.mkdir(parents=True, mode=0o700, exist_ok=True)
    extract_runtime(root, protocol)
    preflight = root / "preflight" / f"attempt-{len(list((root / 'preflight').glob('*'))) + 1:03d}"
    origin = call(root, preflight / "origin", "import json,devharness_controlled.cli as m;print(json.dumps({'origin':m.__file__}))", code=True, timeout=30)
    if origin["exit_code"] != 0 or Path(read(preflight / "origin/stdout.log")["origin"]) != root / "frozen/runtime/devharness_controlled/cli.py":
        raise ValueError("Frozen CLI origin verification failed")
    identity_call = call(root, preflight / "identity", ["native-identity"], timeout=120)
    if identity_call["exit_code"] != 0:
        raise ValueError("Native identity preflight blocked; exact stdout/stderr retained at " + str(preflight))
    identity = read(preflight / "identity/stdout.log")
    if identity["runtime_image_id"] != protocol["wheel"]["runtime_image_id"] or identity["driver_sha256"] != protocol["wheel"]["driver_sha256"]:
        raise ValueError("Native runtime identity mismatch")
    runtime = root / "frozen/runtime"
    for name, module in sys.modules.items():
        if name.startswith("devharness_controlled") and getattr(module, "__file__", None) and not Path(module.__file__).is_relative_to(runtime):
            raise ValueError("Preparation must run in a fresh process without current controlled imports")
    sys.path.insert(0, str(runtime))
    contracts = importlib.import_module("devharness_controlled.native_contracts")
    trust = importlib.import_module("devharness_controlled.trust")
    if not Path(contracts.__file__).is_relative_to(runtime) or not Path(trust.__file__).is_relative_to(runtime):
        raise ValueError("Fixture signing did not resolve frozen contract modules")
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    pairs, registry = [], []
    for role in ("author", "reviewer"):
        private = Ed25519PrivateKey.generate()
        signer = trust.TrustedSigner(key_id="matrix-fixture-key-" + role, subject_id="synthetic-matrix-fixture-" + role,
            role=role, public_key_pem=private.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode())
        pairs.append((private, signer))
        registry.append(signer.model_dump(mode="json"))
    frozen = root / "frozen"
    put(frozen / "protocol.json", PROTOCOL.read_bytes())
    put(frozen / "runner.py", Path(__file__).read_bytes())
    put(frozen / "identity.json", identity)
    put(frozen / "trust-registry.json", registry)
    put(frozen / "generator-environment.json", {"python": sys.version, "platform": platform.platform(), "numpy": np.__version__,
        "patsy": patsy.__version__, "prepared_at_utc": now(), "fixture_keys": "Two ephemeral private keys held only in preparation memory; no human identity or independent approval claim"})
    schedule = []
    issued = datetime.now(timezone.utc)
    seeds = np.random.SeedSequence(protocol["seed"]).spawn(len(protocol["cells"]))
    for cell, cell_seed in zip(protocol["cells"], seeds, strict=True):
        for number, seed in enumerate(cell_seed.spawn(protocol["cohorts_per_cell"]), start=1):
            data = cohort_arrays(protocol, cell, seed)
            case = frozen / "cohorts" / cell["id"] / f"cohort-{number:03d}"
            case.mkdir(parents=True, mode=0o700)
            samples = [f"S{i:03d}" for i in range(cell["samples"])]
            genes = [f"g{i:03d}" for i in range(protocol["panel_genes"])]
            write_csv(case / "counts.csv", ["gene_id", *samples], ([gene, *row.tolist()] for gene, row in zip(genes, data["counts"], strict=True)))
            write_csv(case / "metadata.csv", ["sample_id", "biological_unit_id", "numeric_time"],
                ([sample, f"{cell['id']}_c{number:03d}_unit{i:03d}", float(data["times"][i])] for i, sample in enumerate(samples)))
            for name in ("full", "reduced"):
                write_csv(case / f"{name}-design.csv", ["sample_id", *protocol["design"][name + "_columns"]],
                    ([sample, *row.tolist()] for sample, row in zip(samples, data[name], strict=True)))
            put(case / "truth.json", data["truth"])
            for engine in protocol["engines"]:
                plans = case / engine
                plans.mkdir(mode=0o700)
                values = {key: identity[key] for key in ("implementation_sha256", "environment_sha256", "driver_sha256", "package_lock_sha256", "runtime_image_id", "platform")}
                values.update(plan_id=f"matrix-{cell['id']}-{number:03d}-{engine}", revision=1,
                    intended_use_ref="synthetic-diagnostic-protocol-sha256:" + sha(PROTOCOL), study_id="NATIVE_MATRIX_V1_SYNTHETIC",
                    data_classification="synthetic", engine=engine, normalization="rle",
                    deseq2_fit_type="mean" if engine == "deseq2_lrt" else None, hypothesis_genes=tuple(genes[:32]),
                    panel_genes=tuple(genes), sample_ids=tuple(samples), temporal_columns=(3, 4, 5),
                    time_unit=protocol["time_unit"], basis_definition_ref="fixed-centered-natural-spline-3df;protocol-sha256:" + sha(PROTOCOL),
                    counts_sha256=sha(case / "counts.csv"), metadata_sha256=sha(case / "metadata.csv"),
                    full_design_sha256=sha(case / "full-design.csv"), reduced_design_sha256=sha(case / "reduced-design.csv"),
                    max_memory_mb=1024, max_cpus=1, timeout_sec=60)
                plan = contracts.NativeAnalysisPlan(**values)
                approvals = []
                for private, signer in pairs:
                    payload = trust.ApprovalPayload(plan_sha256=plan.sha256(), plan_id=plan.plan_id, revision=plan.revision,
                        study_id=plan.study_id, key_id=signer.key_id, signer_id=signer.subject_id, meaning=signer.role,
                        issued_at=issued, expires_at=issued + timedelta(hours=24))
                    approval = trust.Approval(payload=payload, signature=base64.b64encode(private.sign(payload.canonical_bytes())).decode())
                    approvals.append(approval.model_dump(mode="json"))
                trust.verify_plan_approvals(plan, approvals, registry)
                put(plans / "plan.json", plan.canonical_bytes())
                put(plans / "approvals.json", approvals)
                schedule.append({"cell": cell["id"], "cohort": number, "engine": engine,
                    "input": str(case), "plan": str(plans / "plan.json"), "approvals": str(plans / "approvals.json")})
    put(frozen / "schedule.json", schedule)
    hashes = {str(path.relative_to(root)): sha(path) for path in sorted(frozen.rglob("*")) if path.is_file()}
    for path in frozen.rglob("*"):
        path.chmod(0o555 if path.is_dir() else 0o444)
    frozen.chmod(0o555)
    put(root / "FREEZE.json", {"schema_version": "native_matrix_freeze/1", "before_native_fitting": True,
        "frozen_at_utc": now(), "protocol_sha256": sha(PROTOCOL), "wheel_sha256": sha(WHEEL),
        "runner_sha256": sha(Path(__file__)), "files": hashes, "planned_native_attempts": len(schedule), "clinical_release_allowed": False})
    (root / "FREEZE.json").chmod(0o444)
    print(json.dumps({"frozen": str(root), "cohorts": 120, "planned_native_attempts": len(schedule),
        "protocol_sha256": sha(PROTOCOL), "freeze_sha256": sha(root / "FREEZE.json"), "clinical_release_allowed": False}), flush=True)


def verify_freeze(root):
    freeze = read(root / "FREEZE.json")
    expected = freeze["files"]
    actual = {str(path.relative_to(root)): sha(path) for path in sorted((root / "frozen").rglob("*")) if path.is_file()}
    errors = [name for name in set(expected) | set(actual) if expected.get(name) != actual.get(name)]
    for path, key in ((PROTOCOL, "protocol_sha256"), (WHEEL, "wheel_sha256"), (Path(__file__), "runner_sha256")):
        if sha(path) != freeze[key]:
            errors.append(str(path))
    return errors


def numerical_check(result, engine):
    genes = result.get("genes", {})
    output = {"usable": False, "errors": [], "available_gene_records": len(genes), "p_values": None, "q_values": None}
    if result.get("status") != "ok":
        output["reason"] = "Native result failed or was partial; no completed-cohort score"
        return output
    if result.get("engine") != engine or list(genes) != [f"g{i:03d}" for i in range(32)]:
        output["errors"].append("engine_or_ordered_family_mismatch")
        return output
    ps, qs = [], []
    for name, row in genes.items():
        p, q = row.get("p_value"), row.get("q_value")
        if row.get("status") != "ok" or any(type(x) not in (float, int) or not math.isfinite(x) or not 0 <= x <= 1 for x in (p, q)) or q < p:
            output["errors"].append("invalid_probability:" + name)
            continue
        statistic, df = row.get("test_statistic"), row.get("test_df_num")
        if type(statistic) not in (float, int) or not math.isfinite(statistic) or statistic < 0 or df != 3:
            output["errors"].append("invalid_statistic:" + name)
            continue
        if engine == "edger_qlf":
            denominator = row.get("test_df_denom")
            if type(denominator) not in (float, int) or not math.isfinite(denominator) or denominator <= 0:
                output["errors"].append("invalid_denominator_df:" + name)
                continue
            tail = float(f.sf(statistic, df, denominator))
        else:
            tail = float(chi2.sf(statistic, df))
        if abs(p - tail) > 1e-10:
            output["errors"].append("independent_reference_tail_mismatch:" + name)
        ps.append(p)
        qs.append(q)
    if not output["errors"]:
        maximum = float(np.max(np.abs(np.asarray(qs) - multipletests(ps, method="fdr_bh")[1])))
        output["independent_bh_max_absolute_error"] = maximum
        if maximum > 1e-10:
            output["errors"].append("independent_bh_mismatch")
    output.update(usable=not output["errors"], p_values=ps if not output["errors"] else None, q_values=qs if not output["errors"] else None)
    return output


def outer_timeout_cleanup(root, attempt, item):
    """Bound cleanup to the persisted UUID, fixed image and frozen plan label."""
    journal = attempt / "execution/native-logs/lifecycle.jsonl"
    if not journal.is_file() or journal.is_symlink():
        return {"removed": False, "reason": "No retained lifecycle identity; operator recovery required"}
    first = json.loads(journal.read_bytes().splitlines()[0])
    name = first.get("container_name", "")
    if not re.fullmatch(r"devharness-native-[0-9a-f]{32}", name) or first.get("plan_sha256") != sha(Path(item["plan"])):
        return {"removed": False, "reason": "Cleanup ownership not established"}
    identity = read(root / "frozen/identity.json")["environment"]["docker"]
    executable = Path(shutil.which("docker") or "missing").resolve()
    if not executable.is_file() or sha(executable) != identity["executable_sha256"]:
        return {"removed": False, "reason": "Docker binary identity changed"}
    prefix = [str(executable), "--config", str(attempt / "execution/native-client"), "--host", identity["endpoint"]]
    folder = attempt / "outer-cleanup"
    folder.mkdir(mode=0o700)
    def command(label, arguments):
        put(folder / (label + ".command.json"), [*prefix, *arguments])
        result = subprocess.run([*prefix, *arguments], env=child_environment(root), stdin=subprocess.DEVNULL, capture_output=True, timeout=30)
        put(folder / (label + ".stdout.log"), result.stdout)
        put(folder / (label + ".stderr.log"), result.stderr)
        return result
    try:
        fmt = '{"Id":{{json .Id}},"Name":{{json .Name}},"Image":{{json .Image}},"State":{{json .State}},"Plan":{{json (index .Config.Labels "io.devharness.plan-sha256")}}}'
        inspected = command("inspect", ["inspect", "--format", fmt, name])
        if inspected.returncode != 0:
            return {"removed": False, "reason": "Owned container inspection unavailable"}
        record = json.loads(inspected.stdout)
        if record["Name"] != "/" + name or record["Image"] != read(root / "frozen/protocol.json")["wheel"]["runtime_image_id"] or record["Plan"] != sha(Path(item["plan"])):
            return {"removed": False, "reason": "Owned container label/image mismatch"}
        if record["State"].get("Running") is True:
            killed = command("kill", ["kill", record["Id"]])
            if killed.returncode != 0:
                return {"removed": False, "reason": "Owned container kill failed"}
        removed = command("remove", ["rm", record["Id"]])
        return {"removed": removed.returncode == 0, "container_id": record["Id"]}
    except (OSError, ValueError, KeyError, subprocess.TimeoutExpired) as error:
        return {"removed": False, "reason": f"{type(error).__name__}: {error}"}


def run_attempt(root, item):
    attempt = root / "attempts" / item["cell"] / f"cohort-{item['cohort']:03d}" / item["engine"]
    attempt.mkdir(parents=True, mode=0o700, exist_ok=False)
    put(attempt / "SCHEDULED.json", {**item, "at_utc": now(), "freeze_sha256": sha(root / "FREEZE.json")})
    base = Path(item["input"])
    arguments = ["execute-native", "--plan", item["plan"], "--approvals", item["approvals"], "--trust-registry", str(root / "frozen/trust-registry.json"),
        "--counts", str(base / "counts.csv"), "--metadata", str(base / "metadata.csv"), "--full-design", str(base / "full-design.csv"),
        "--reduced-design", str(base / "reduced-design.csv"), "--output", str(attempt / "execution")]
    process = call(root, attempt / "cli", arguments)
    row = {"cell": item["cell"], "cohort": item["cohort"], "engine": item["engine"], "process": process,
        "usable": False, "infrastructure_blocker": None, "metrics": None, "memory_peak_mb": None}
    execution = attempt / "execution"
    if process["error"]:
        row["infrastructure_blocker"] = process["error"]
        if process["error"] == "outer_cli_watchdog_timeout":
            row["outer_cleanup"] = outer_timeout_cleanup(root, attempt, item)
    try:
        row["outcome"] = read(execution / "outcome.json") if (execution / "outcome.json").is_file() else read(attempt / "cli/stdout.log")
        transport_path = execution / "native-transport.json"
        transport = read(transport_path) if transport_path.is_file() else None
        if transport is None or transport.get("created") is None or not transport.get("cleanup", {}).get("removed"):
            row["infrastructure_blocker"] = row["infrastructure_blocker"] or "Native container creation/cleanup not established"
        row["transport"] = {key: transport.get(key) for key in ("status", "timed_out", "memory_limit_verified", "elapsed_seconds", "error", "cleanup")} if transport else None
        receipt = execution / "receipt.json"
        manifest = execution / "audit/manifest.json"
        if receipt.is_file() and manifest.is_file():
            put(attempt / "saved-root-receipt.json", receipt.read_bytes())
            verified = call(root, attempt / "verify", ["verify", "--manifest", str(manifest), "--receipt", str(attempt / "saved-root-receipt.json"),
                "--trust-registry", str(root / "frozen/trust-registry.json")], timeout=120)
            row["audit"] = {"process": verified, "result": read(attempt / "verify/stdout.log"), "saved_receipt_sha256": sha(attempt / "saved-root-receipt.json")}
            if verified["exit_code"] != 0 or row["audit"]["result"].get("valid") is not True:
                row["infrastructure_blocker"] = row["infrastructure_blocker"] or "Controlled audit verification failed"
        else:
            row["infrastructure_blocker"] = row["infrastructure_blocker"] or "Available controlled audit/receipt incomplete"
        result_path = execution / "native-output/result.json"
        if result_path.is_file():
            result = read(result_path)
            row["native_status"], row["native_error"] = result.get("status"), result.get("error")
            references = [entry for entry in read(manifest)["artifacts"] if entry.get("name") == "result.json"]
            if len(references) != 1 or references[0]["sha256"] != sha(result_path):
                raise ValueError("Loose native output differs from verified audit artifact")
            row["numerical"] = numerical_check(result, item["engine"])
            if row["numerical"]["errors"]:
                row["infrastructure_blocker"] = row["infrastructure_blocker"] or "Independent statistical arithmetic verification failed"
            row["versions"] = result.get("actual_versions")
            row["native_elapsed_seconds"] = result.get("elapsed_seconds")
            row["usable"] = (process["exit_code"] == 0 and row["outcome"].get("status") == "analysis_complete_pending_review"
                              and row["numerical"]["usable"] and row["infrastructure_blocker"] is None)
            if row["usable"]:
                truth = read(base / "truth.json")
                row["metrics"] = cohort_scores(row["numerical"]["p_values"], row["numerical"]["q_values"], truth["null_hypotheses"], truth["hypothesis_shapes"])
        row["integrity_mismatches"] = verify_freeze(root)
        if row["integrity_mismatches"]:
            row["infrastructure_blocker"] = "Frozen code/data drift"
            row["usable"] = False
    except (OSError, ValueError, KeyError, TypeError) as error:
        row["infrastructure_blocker"] = row["infrastructure_blocker"] or f"{type(error).__name__}: {error}"
        row["usable"] = False
    put(attempt / "RESULT.json", row)
    print(json.dumps({"cell": item["cell"], "cohort": item["cohort"], "engine": item["engine"], "usable": row["usable"],
        "seconds": round(process["wall_seconds"], 3), "native_status": row.get("native_status"), "blocker": row["infrastructure_blocker"],
        "metrics": row["metrics"]}), flush=True)
    return row


def run(root):
    if (root / "SEAL.json").exists() or (root / "attempts").exists():
        raise ValueError("Measured matrix attempts cannot be rerun")
    errors = verify_freeze(root)
    if errors:
        raise ValueError("Frozen inputs changed: " + str(errors))
    schedule = read(root / "frozen/schedule.json")
    with ThreadPoolExecutor(max_workers=2) as pool:
        for position in range(0, len(schedule), 2):
            errors = verify_freeze(root)
            if errors:
                put(root / "STOPPED.json", {"at_utc": now(), "reason": "Frozen code/data drift", "mismatches": errors})
                break
            futures = [pool.submit(run_attempt, root, item) for item in schedule[position:position+2]]
            rows = []
            for item, future in zip(schedule[position:position+2], futures, strict=True):
                try:
                    rows.append(future.result())
                except Exception as error:
                    failure = {"cell": item["cell"], "cohort": item["cohort"], "engine": item["engine"],
                        "infrastructure_blocker": f"Uncaught {type(error).__name__}: {error}", "at_utc": now()}
                    attempt = root / "attempts" / item["cell"] / f"cohort-{item['cohort']:03d}" / item["engine"]
                    if attempt.is_dir():
                        put(attempt / "INCOMPLETE.json", failure)
                    rows.append(failure)
            blockers = [row for row in rows if row["infrastructure_blocker"]]
            if blockers:
                put(root / "STOPPED.json", {"at_utc": now(), "reason": "Infrastructure or verification blocker", "attempts": blockers})
                break
    summarize(root)


def summarize(root):
    protocol = read(root / "frozen/protocol.json")
    rows = []
    starts = sorted((root / "attempts").glob("*/*/*/SCHEDULED.json"))
    for scheduled in starts:
        terminal = scheduled.parent / "RESULT.json"
        if terminal.is_file():
            rows.append(read(terminal))
        else:
            item = read(scheduled)
            process = scheduled.parent / "cli/OUTCOME.json"
            rows.append({"cell": item["cell"], "cohort": item["cohort"], "engine": item["engine"],
                "usable": False, "metrics": None, "process": read(process) if process.is_file() else {"wall_seconds": None},
                "infrastructure_blocker": "Started invocation lacks a complete terminal matrix record",
                "outcome": {"status": "incomplete", "error": "No probabilities or completion inferred"}})
    summary = {"scope": protocol["scope"], "clinical_release_allowed": False, "planned_native_attempts": 240,
        "attempted_native_cli_runs": len(starts),
        "cli_processes_launched": len(list((root / "attempts").glob("*/*/*/cli/PROCESS.json"))),
        "terminal_results": len(list((root / "attempts").glob("*/*/*/RESULT.json"))),
        "usable_results": sum(row["usable"] for row in rows), "protocol_sha256": sha(root / "frozen/protocol.json"),
        "freeze_sha256": sha(root / "FREEZE.json"), "wheel_sha256": protocol["wheel"]["sha256"], "memory_peak_mb": None,
        "cells": {}, "primary_warning_family": protocol["reporting"]["simultaneous_warning"]}
    for cell in protocol["cells"]:
        output = {"definition": cell, "engines": {}}
        paired = {}
        for engine in protocol["engines"]:
            selected = [row for row in rows if row["cell"] == cell["id"] and row["engine"] == engine]
            complete = [row for row in selected if row["usable"]]
            paired[engine] = {row["cohort"]: row for row in complete}
            null_values = [row["metrics"]["null_rejection_fraction"] for row in complete]
            fdp_values = [row["metrics"]["fdp"] for row in complete]
            powers = [row["metrics"]["power"] for row in complete if row["metrics"]["power"] is not None]
            missing = len(selected) - len(complete)
            def missing_bounds(values):
                return [sum(values) / len(selected), (sum(values) + missing) / len(selected)] if selected else None
            output["engines"][engine] = {"planned": 20, "attempted": len(selected), "usable": len(complete),
                "failed_or_incomplete": missing, "unattempted": 20 - len(selected), "unavailable_hypotheses": missing * 32,
                "null_rejection": bounded_summary(null_values), "fdr": bounded_summary(fdp_values, binary=cell["regime"] == "complete_null"),
                "power": bounded_summary(powers), "null_rejection_missingness_bounds": missing_bounds(null_values),
                "fdr_missingness_bounds": missing_bounds(fdp_values), "power_missingness_bounds": missing_bounds(powers) if cell["regime"] == "mixed" else None,
                "null_rejection_simultaneous_warning": primary_warning(null_values),
                "fdr_simultaneous_warning": primary_warning(fdp_values, binary=cell["regime"] == "complete_null"),
                "shape_power": {shape: bounded_summary([row["metrics"]["shape_power"][shape] for row in complete]) for shape in sorted({shape for row in complete for shape in row["metrics"]["shape_power"]})},
                "wall_seconds": {"scope": "Signed CLI wall time, including its internal verification",
                    "total_observed": sum(row["process"]["wall_seconds"] for row in selected if row["process"]["wall_seconds"] is not None),
                    "median": float(np.median([row["process"]["wall_seconds"] for row in selected if row["process"]["wall_seconds"] is not None])) if any(row["process"]["wall_seconds"] is not None for row in selected) else None},
                "nominal_null_rejections_observed": sum(row["metrics"]["null_nominal_rejections"] for row in complete),
                "null_hypotheses_observed": sum(row["metrics"]["null_hypotheses"] for row in complete),
                "false_discoveries_observed": sum(row["metrics"]["false_discoveries"] for row in complete),
                "true_discoveries_observed": sum(row["metrics"]["true_discoveries"] for row in complete),
                "observed_versions": [json.loads(text) for text in sorted({json.dumps(row["versions"], sort_keys=True) for row in selected if row.get("versions")})],
                "failures": [{"cohort": row["cohort"], "blocker": row["infrastructure_blocker"], "native_error": row.get("native_error"), "outcome": row.get("outcome")} for row in selected if not row["usable"]],
                "summary_scope": "Complete-case statistics are conditional on usable runs; missingness bounds retain failed attempts"}
        matches = sorted(set(paired["edger_qlf"]) & set(paired["deseq2_lrt"]))
        output["paired_engine_differences"] = {"direction": "deseq2_lrt minus edger_qlf", "matched_usable_cohorts": len(matches),
            "conditional_on_usable_runs": len(matches) < 20,
            **{metric: bounded_summary([paired["deseq2_lrt"][i]["metrics"][metric] - paired["edger_qlf"][i]["metrics"][metric] for i in matches], low=-1, high=1)
               for metric in (["null_rejection_fraction", "fdp", "power"] if cell["regime"] == "mixed" else ["null_rejection_fraction", "fdp"])}}
        summary["cells"][cell["id"]] = output
    put(root / "summary.json", summary)
    lines = ["# Frozen post-repair native matrix", "", protocol["scope"], "", "No clinical release or absence-of-warning acceptance is established.", "",
        "| Cell | Engine | Usable/attempted | Null p rejection | Mean cohort FDP | Mixed power |", "|---|---|---:|---:|---:|---:|"]
    def show(value):
        return "unknown" if value is None else f"{value:.4f}"
    for name, cell in summary["cells"].items():
        for engine, result in cell["engines"].items():
            lines.append(f"| {name} | {engine} | {result['usable']}/{result['attempted']} | {show(result['null_rejection']['mean'])} | {show(result['fdr']['mean'])} | {show(result['power']['mean'])} |")
    lines.extend(["", "summary.json retains independent-cohort uncertainty, all-attempt missingness bounds, prespecified simultaneous warnings and matched engine differences. Failed runs are not successful negatives.",
        "", "Memory peak is unknown. Each native request is capped at one CPU, 1 GiB and 60 seconds; at most two native CLI runs are scheduled concurrently. Actual transport records identify verified limits.",
        "", "The 36-cell/2,000-cohort matrix, stress scenarios, independent qualification and assay/clinical validation remain outstanding."])
    put(root / "report.md", ("\n".join(lines) + "\n").encode())
    put(root / "SEAL.json", {"schema_version": "native_matrix_seal/1", "at_utc": now(), "clinical_release_allowed": False,
        "scope": "Local SHA-256 evidence, not protected retention or human approval",
        "files": {str(path.relative_to(root)): sha(path) for path in sorted(root.rglob("*")) if path.is_file()}})
    print(json.dumps({"summary": str(root / "summary.json"), "attempted": summary["attempted_native_cli_runs"],
        "terminal": summary["terminal_results"], "usable": summary["usable_results"], "seal_sha256": sha(root / "SEAL.json")}), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "run", "summarize", "verify"))
    parser.add_argument("--output", type=Path, default=DEFAULT_ROOT)
    args = parser.parse_args()
    if not args.output.is_absolute() or args.output.resolve() != args.output:
        parser.error("Output must be an absolute canonical path")
    os.umask(0o077)
    if args.action == "verify":
        mismatches = verify_freeze(args.output)
        seal = read(args.output / "SEAL.json")
        mismatches.extend(name for name, expected in seal["files"].items() if sha(args.output / name) != expected)
        print(json.dumps({"files_verified": len(seal["files"]), "mismatches": mismatches, "clinical_release_allowed": False}))
        return int(bool(mismatches))
    {"prepare": prepare, "run": run, "summarize": summarize}[args.action](args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
