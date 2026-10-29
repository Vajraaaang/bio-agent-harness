"""Additive three-arm RNA workflow comparison for trusted local fixtures.

This module does not admit providers, native jobs, or paid execution. Injected
Python functions are trusted coordinator code, not an OS sandbox. Source hashes
identify their files and code, not all mutable globals or transitive dependencies.
Usage is fixture-supplied telemetry; actual callback wall time is separate.
Custody replay verifies sealed records without dispatching callbacks or rescoring.
Neither a coordinator check nor a hash authenticates independent expert review.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import inspect
import json
import marshal
import math
import os
from pathlib import Path
import random
import re
import stat
import statistics
import time


ARMS = ("checked_workflow", "analyst_same_tools_checks", "analyst_plus_reviewer")
ENDPOINTS = ("numeric_correctness", "design_validity", "appropriate_refusal", "claim_support", "replay_validity")
SPLITS = ("development", "calibration", "locked_test")
STATUSES = ("completed", "abstained", "failed", "blocked", "unknown_usage", "budget_exhausted", "timeout")
FORMAT = "devbench.rna-workflow-comparison/1"
MAX_RECORD = 4_000_000
MAX_CAMPAIGN = 64_000_000
SCOPE = "coordinator_checked_software_fixture_only"


def _encoded(value):
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"),
            ensure_ascii=False, allow_nan=False).encode()
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError("Use finite JSON comparison records") from error


def _copy(value):
    return json.loads(_encoded(value))


def _digest(value):
    return hashlib.sha256(_encoded(value)).hexdigest()


def _hash_bytes(value):
    return hashlib.sha256(value).hexdigest()


def _path(value):
    path = Path(value)
    if not path.is_absolute() or any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError("Use absolute paths without symlinks")
    return path


def _bytes(path, maximum=MAX_RECORD):
    path = _path(path)
    descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(descriptor, "rb") as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > maximum:
            raise ValueError("Use bounded regular comparison artifacts")
        value = handle.read(maximum + 1)
        if len(value) > maximum:
            raise ValueError("Comparison artifact exceeds its bound")
        return value


def _parse(data):
    try:
        return _copy(json.loads(data))
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise ValueError("Invalid retained comparison JSON") from error


def _read(path, maximum=MAX_RECORD):
    return _parse(_bytes(path, maximum))


class _Captures:
    """One bounded byte snapshot per artifact in a verification operation."""

    def __init__(self):
        self.values = {}

    def read(self, path, expected=None):
        path = _path(path)
        if path not in self.values:
            self.values[path] = _bytes(path)
        data = self.values[path]
        if expected is not None and (not _sha(expected) or _hash_bytes(data) != expected):
            raise ValueError("Retained comparison artifact hash changed: " + path.name)
        return _parse(data)


def _write(path, value, maximum=MAX_RECORD):
    path = _path(path)
    data = _encoded(value) + b"\n"
    if len(data) > maximum:
        raise ValueError("Comparison record exceeds its bound")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    return _hash_bytes(data)


def _positive(value, name, maximum):
    if type(value) is not int or not 0 < value <= maximum:
        raise ValueError("Invalid comparison bound: " + name)


def _scalar(value):
    return type(value) in (int, float) and 0 <= value <= float.fromhex("0x1.fffffffffffffp+1023")


def _text(value):
    return isinstance(value, str) and 0 < len(value.encode()) <= 512


def _sha(value):
    return isinstance(value, str) and re.fullmatch("[0-9a-f]{64}", value) is not None


def driver_identity(driver):
    """Identify a module-level trusted function; this grants no execution power."""
    if (not inspect.isfunction(driver) or driver.__closure__ or "<" in driver.__qualname__
            or "." in driver.__qualname__ or inspect.iscoroutinefunction(driver)
            or inspect.isgeneratorfunction(driver)):
        raise ValueError("Use a source-backed module-level local driver without closures")
    source = inspect.getsourcefile(driver)
    if not source:
        raise ValueError("Local driver source identity is unavailable")
    source = _path(Path(source).absolute())
    return {"callable": driver.__module__ + "." + driver.__qualname__, "source_path": str(source),
        "source_sha256": _hash_bytes(_bytes(source)),
        "code_sha256": _hash_bytes(marshal.dumps(driver.__code__)),
        "scope": "trusted_local_function_not_os_sandbox_or_transitive_dependency_freeze"}


def _storage_forecast(protocol, case_count):
    resources = protocol["identities"]["resources"]
    # A call retains the request and both raw/normalized response fields.
    # Attempt JSON inlines every call as well as initial/feedback/final outputs;
    # the campaign also retains standalone call captures. Do not admit maxima
    # which can execute successfully but cannot seal their complete history.
    call = resources["max_context_bytes"] + 2 * resources["max_output_bytes"] + 16000
    attempt = resources["max_calls_per_attempt"] * call + 3 * resources["max_output_bytes"] + 32000
    campaign = case_count * protocol["replicates"] * len(ARMS) * (
        attempt + resources["max_calls_per_attempt"] * call) + 128000
    return {"maximum_call_bytes": call, "maximum_attempt_bytes": attempt, "maximum_campaign_bytes": campaign}


def _validate(protocol, cases, drivers, *, legacy_replay=False):
    protocol, cases, drivers = _copy(protocol), _copy(cases), _copy(drivers)
    fields = {"format", "scope", "arms", "splits", "replicates", "ordering_seed", "seed_supported",
        "identities", "stop_on_unknown_usage", "endpoints", "primary_endpoint", "endpoint_scope"}
    legacy = isinstance(protocol, dict) and protocol.get("format") == "devbench.rna-workflow-comparison-protocol/1"
    if legacy and not legacy_replay:
        raise ValueError("Legacy v1 campaigns are custody replay only; freeze v2 coordinator checkers for new work")
    if not legacy:
        fields.add("coordinator_checks")
    if not isinstance(protocol, dict) or set(protocol) != fields:
        raise ValueError("Freeze the complete three-arm comparison protocol")
    if protocol["scope"] != "software_fixture":
        raise PermissionError("Only local software fixture scope is admitted; native execution is unavailable")
    if (protocol["format"] not in {"devbench.rna-workflow-comparison-protocol/1", "devbench.rna-workflow-comparison-protocol/2"}
            or protocol["arms"] != list(ARMS) or set(protocol["splits"]) != set(SPLITS)
            or len(protocol["splits"]) != len(SPLITS)):
        raise ValueError("Freeze the additive three arms and dataset splits")
    _positive(protocol["replicates"], "replicates", 20)
    if type(protocol["ordering_seed"]) is not int or type(protocol["seed_supported"]) is not bool:
        raise ValueError("Declare ordering seed and actual driver seed support")
    if (protocol["stop_on_unknown_usage"] is not True or protocol["endpoints"] != list(ENDPOINTS)
            or protocol["primary_endpoint"] not in ENDPOINTS or protocol["endpoint_scope"] != SCOPE):
        raise ValueError("Freeze separate endpoint checks and unknown-usage stop policy")
    ids = protocol["identities"]
    if not isinstance(ids, dict) or set(ids) != {"tools", "checks", "evidence", "model", "memory", "resources"}:
        raise ValueError("Freeze tool/check/evidence/model/memory/resource identities")
    for name in ("tools", "checks", "evidence"):
        if (not isinstance(ids[name], dict) or not ids[name] or len(ids[name]) > 100
                or any(not _text(k) or not _sha(v) for k, v in ids[name].items())):
            raise ValueError("Freeze named version hashes for " + name)
    if (not isinstance(ids["model"], dict) or set(ids["model"]) != {"name", "version"}
            or not all(_text(v) for v in ids["model"].values())
            or not isinstance(ids["memory"], dict) or set(ids["memory"]) != {"version", "sha256"}
            or not _text(ids["memory"]["version"]) or not _sha(ids["memory"]["sha256"])):
        raise ValueError("Freeze explicit local model and memory versions")
    resources = ids["resources"]
    limits = {"max_calls_per_attempt": 32, "max_driver_attempts": 8,
        "max_context_bytes": 128000, "max_output_bytes": 128000}
    if not isinstance(resources, dict) or set(resources) != set(limits) | {"max_elapsed_seconds", "max_cost_usd"}:
        raise ValueError("Freeze complete local resource bounds")
    for name, maximum in limits.items():
        _positive(resources[name], name, maximum)
    if resources["max_calls_per_attempt"] < 3:
        raise ValueError("The reviewed arm needs initial, review and revision opportunities")
    if (not _scalar(resources["max_elapsed_seconds"]) or not 0 < resources["max_elapsed_seconds"] <= 3600
            or not _scalar(resources["max_cost_usd"])):
        raise ValueError("Freeze finite time and fixture cost bounds")
    if not isinstance(drivers, dict) or set(drivers) != {"deterministic", "analyst", "reviewer"}:
        raise ValueError("Freeze all three driver identities")
    for identity in drivers.values():
        if (not isinstance(identity, dict) or set(identity) != {"callable", "source_path", "source_sha256", "code_sha256", "scope"}
                or not _text(identity["callable"]) or not _sha(identity["source_sha256"])
                or not _sha(identity["code_sha256"])):
            raise ValueError("Invalid source-backed driver identity")
        _path(identity["source_path"])
    if not legacy:
        checks = protocol["coordinator_checks"]
        if not isinstance(checks, dict) or set(checks) != set(ENDPOINTS):
            raise ValueError("Freeze an appropriate source-backed coordinator checker per endpoint")
        for identity in checks.values():
            if (not isinstance(identity, dict) or set(identity) != {"callable", "source_path", "source_sha256", "code_sha256", "scope"}
                    or not _text(identity["callable"]) or not _sha(identity["source_sha256"])
                    or not _sha(identity["code_sha256"])):
                raise ValueError("Invalid frozen coordinator checker source identity")
            _path(identity["source_path"])
    if not isinstance(cases, list) or not 0 < len(cases) <= 1000:
        raise ValueError("Use a bounded public case manifest")
    required = {"case_id", "dataset_id", "family_id", "cohort_ids", "donor_ids", "provenance_ids", "split", "public_data"}
    seen, relations = set(), {}
    for case in cases:
        if not isinstance(case, dict) or set(case) != required:
            raise ValueError("Freeze complete public dataset and related-unit manifests")
        if (any(not _text(case[k]) for k in ("case_id", "dataset_id", "family_id"))
                or case["case_id"] in seen or case["split"] not in SPLITS
                or not isinstance(case["public_data"], dict) or len(_encoded(case)) > 32000):
            raise ValueError("Invalid bounded public case or split")
        seen.add(case["case_id"])
        for kind in ("dataset_id", "family_id", "cohort_ids", "donor_ids", "provenance_ids"):
            values = [case[kind]] if kind in {"dataset_id", "family_id"} else case[kind]
            if (not isinstance(values, list) or not values or len(values) > 1000
                    or any(not _text(v) for v in values) or len(set(values)) != len(values)):
                raise ValueError("Declare unique related units: " + kind)
            for value in values:
                key = (kind, value)
                if key in relations and relations[key] != case["split"]:
                    raise ValueError("Related dataset/unit leaks across splits: " + kind)
                relations[key] = case["split"]
    if {case["split"] for case in cases} != set(SPLITS):
        raise ValueError("Declare development, calibration and locked-test splits")
    trials = len(cases) * protocol["replicates"] * len(ARMS)
    forecast = _storage_forecast(protocol, len(cases))
    if (not legacy and (forecast["maximum_call_bytes"] > MAX_RECORD
            or forecast["maximum_attempt_bytes"] > MAX_RECORD
            or forecast["maximum_campaign_bytes"] > MAX_CAMPAIGN)):
        raise ValueError("Declared storage maxima cannot fit bounded sealable call/attempt/campaign records")
    if trials > 2000:
        raise ValueError("Prospective comparison storage exceeds the bounded campaign envelope")
    return protocol, cases, drivers


def _clusters(cases):
    parents = {case["case_id"]: case["case_id"] for case in cases}
    def find(key):
        while parents[key] != key:
            parents[key] = parents[parents[key]]
            key = parents[key]
        return key
    seen = {}
    for case in sorted(cases, key=lambda c: c["case_id"]):
        for kind in ("dataset_id", "family_id", "cohort_ids", "donor_ids", "provenance_ids"):
            for value in ([case[kind]] if kind in {"dataset_id", "family_id"} else case[kind]):
                key = (kind, value)
                if key in seen:
                    left, right = find(case["case_id"]), find(seen[key])
                    parents[max(left, right)] = min(left, right)
                seen[key] = case["case_id"]
    members = {}
    for case in cases:
        members.setdefault(find(case["case_id"]), []).append(case["case_id"])
    return {case["case_id"]: _digest(sorted(members[find(case["case_id"])])) for case in cases}


def _schedule(protocol, cases):
    rng = random.Random(protocol["ordering_seed"])
    units = [(case["case_id"], rep) for case in sorted(cases, key=lambda c: c["case_id"])
        for rep in range(protocol["replicates"])]
    rng.shuffle(units)
    base = list(ARMS)
    rng.shuffle(base)
    schedule = []
    for index, (case_id, replicate) in enumerate(units):
        offset = index % len(ARMS)
        arms = base[offset:] + base[:offset]
        seed = rng.randrange(2**31) if protocol["seed_supported"] else None
        for arm in arms:
            trial = {"case_id": case_id, "replicate": replicate, "arm": arm, "seed": seed,
                "order": len(schedule)}
            schedule.append({**trial, "attempt_id": _digest(trial)[:24]})
    return schedule


@dataclass(frozen=True)
class PreparedComparison:
    root: Path
    manifest_sha256: str


def prepare_comparison(output, *, protocol, cases, driver_identities):
    """Freeze public fixture inputs before any trusted local driver executes."""
    root = _path(output)
    protocol, cases, drivers = _validate(protocol, cases, driver_identities)
    if root.exists():
        raise FileExistsError("Preserve the existing comparison workspace")
    for identity in protocol["coordinator_checks"].values():
        if _hash_bytes(_bytes(identity["source_path"])) != identity["source_sha256"]:
            raise ValueError("Frozen coordinator checker source hash differs from its actual source")
    manifest = {"format": FORMAT, "protocol": protocol, "cases": sorted(cases, key=lambda c: c["case_id"]),
        "drivers": drivers, "schedule": _schedule(protocol, cases), "clusters": _clusters(cases),
        "storage_forecast": _storage_forecast(protocol, len(cases)),
        "implementation_sha256": _hash_bytes(_bytes(Path(__file__).absolute())),
        "analysis_policy": {"source": "platform_default_not_user_prespecified", "split": "locked_test",
            "unit": "connected_dataset_family_cohort_donor_provenance", "bootstrap_resamples": 1000,
            "confidence": 0.95, "minimum_clusters_for_interval": 2,
            "scope": "descriptive_cluster_bootstrap_not_population_or_scientific_qualification"}}
    root.mkdir(mode=0o700, parents=True, exist_ok=False)
    identity = _write(root / "manifest.json", manifest)
    _write(root / "preparation-seal.json", {"format": FORMAT, "manifest_sha256": identity})
    return PreparedComparison(root, identity)


def _manifest(prepared, *, captures=None, legacy_replay=False):
    if type(prepared) is not PreparedComparison or not _sha(prepared.manifest_sha256):
        raise TypeError("Use a prepared local comparison")
    root = _path(prepared.root)
    captures = captures or _Captures()
    manifest = captures.read(root / "manifest.json", prepared.manifest_sha256)
    seal = captures.read(root / "preparation-seal.json")
    if seal != {"format": FORMAT, "manifest_sha256": prepared.manifest_sha256}:
        raise ValueError("Comparison preparation seal changed")
    _validate(manifest["protocol"], manifest["cases"], manifest["drivers"], legacy_replay=legacy_replay)
    if (manifest["schedule"] != _schedule(manifest["protocol"], manifest["cases"])
            or manifest["clusters"] != _clusters(manifest["cases"])):
        raise ValueError("Frozen comparison schedule or grouping changed")
    return manifest


def _sum(values):
    if any(v is None for v in values):
        return None
    try:
        value = sum(values) if all(type(v) is int for v in values) else math.fsum(values)
    except (OverflowError, ValueError):
        return None
    return value if _scalar(value) else None


def _usage(calls):
    totals = {key: _sum([call["usage"][key] for call in calls])
        for key in ("tokens", "cost_usd", "elapsed_seconds")}
    unknown = [key for key, value in totals.items() if value is None]
    return {**totals, "complete": not unknown, "unknown_dimensions": unknown,
        "request_attempts": len(calls), "scope": "driver_supplied_fixture_telemetry_not_provider_billing",
        "wall_elapsed_seconds": _sum([call["wall_elapsed_seconds"] for call in calls])}


def _invoke(driver, request, resources, directory, number):
    call = {"phase": request["phase"], "request": _copy(request), "status": "failed", "output": None,
        "usage": {"tokens": None, "cost_usd": None, "elapsed_seconds": None}, "interventions": [],
        "error_type": None, "retryable": False, "invalid_usage": False, "raw_response": None}
    started = time.monotonic()
    try:
        response = driver(_copy(request))
        data = _encoded(response)
        if len(data) > resources["max_output_bytes"]:
            call["error_type"] = "OutputTooLarge"
            call["rejected_response_sha256"] = _hash_bytes(data)
            call["rejected_response_bytes"] = len(data)
        else:
            response = _copy(response)
            call["raw_response"] = response
            allowed = {"status", "output", "usage", "interventions", "error_type", "retryable"}
            if (not isinstance(response, dict) or set(response) - allowed
                    or response.get("status") not in {"completed", "abstained", "failed", "timeout"}
                    or not isinstance(response.get("interventions", []), list)
                    or len(response.get("interventions", [])) > 100
                    or type(response.get("retryable", False)) is not bool):
                call["error_type"] = "InvalidDriverResponse"
            else:
                usage = response.get("usage")
                supplied = usage if isinstance(usage, dict) else {}
                for key in ("tokens", "cost_usd", "elapsed_seconds"):
                    value = supplied.get(key)
                    valid = type(value) is int and value >= 0 if key == "tokens" else _scalar(value)
                    call["usage"][key] = value if valid else None
                    if value is not None and not valid:
                        call["invalid_usage"] = True
                call.update(status=response["status"], output=response.get("output"),
                    interventions=response.get("interventions", []), error_type=response.get("error_type"),
                    retryable=response.get("retryable", False))
                if call["invalid_usage"]:
                    call.update(status="failed", error_type="InvalidFixtureUsage", retryable=False)
                elif call["status"] in {"completed", "abstained"} and not isinstance(call["output"], dict):
                    call.update(status="failed", error_type="InvalidFixtureOutput", retryable=False)
    except Exception as error:
        call["error_type"] = type(error).__name__
        call["error"] = str(error)[:1000]
    call["wall_elapsed_seconds"] = time.monotonic() - started
    path = directory / ("call-%04d.json" % number)
    identity = _write(path, call)
    return {**call, "capture_path": str(path), "capture_sha256": identity}


def _authorization(value):
    expected = {"scope": "local_fixture", "allow_model_execution": False,
        "allow_native_execution": False, "allow_paid_execution": False}
    if type(value) is not dict or value != expected or any(value.get(k) is not False for k in expected if k != "scope"):
        raise PermissionError("Local fixture declarations cannot grant model, native or paid authority")


def run_comparison(prepared, *, deterministic_driver, analyst_driver, reviewer_driver, authorization):
    """Execute bounded trusted callbacks. No serialized driver imports exist."""
    _authorization(authorization)
    manifest = _manifest(prepared)
    root, protocol = prepared.root, manifest["protocol"]
    drivers = {"deterministic": deterministic_driver, "analyst": analyst_driver, "reviewer": reviewer_driver}
    if {name: driver_identity(fn) for name, fn in drivers.items()} != manifest["drivers"]:
        raise PermissionError("Frozen local driver source identity changed")
    if _hash_bytes(_bytes(Path(__file__).absolute())) != manifest["implementation_sha256"]:
        raise ValueError("Comparison implementation source changed before run")
    _write(root / "run-reservation.json", {"manifest_sha256": prepared.manifest_sha256,
        "scope": "trusted_local_fixture_callbacks_only"})
    (root / "attempts").mkdir(mode=0o700)
    resources = protocol["identities"]["resources"]
    attempts, bindings, stop = [], [], None
    for trial in manifest["schedule"]:
        case = next(c for c in manifest["cases"] if c["case_id"] == trial["case_id"])
        directory = root / "attempts" / trial["attempt_id"]
        directory.mkdir(mode=0o700)
        attempt = {**trial, "split": case["split"], "dataset_id": case["dataset_id"],
            "cluster_id": manifest["clusters"][case["case_id"]], "status": "blocked" if stop else "completed",
            "stop_reason": stop, "initial_output": None, "feedback": None, "final_output": None,
            "initial_status": None, "calls": [], "manifest_sha256": prepared.manifest_sha256}
        phases = [("workflow", "deterministic")] if trial["arm"] == ARMS[0] else [("initial", "analyst")]
        if trial["arm"] == ARMS[2]:
            phases += [("review", "reviewer"), ("revision", "analyst")]
        for phase, role in ([] if stop else phases):
            phase_completed = False
            for driver_attempt in range(1, resources["max_driver_attempts"] + 1):
                request = {"case": case, "arm": trial["arm"], "replicate": trial["replicate"],
                    "seed": trial["seed"], "phase": phase, "driver_attempt": driver_attempt,
                    "identities": protocol["identities"]}
                if phase in {"review", "revision"}:
                    request["initial_output"] = attempt["initial_output"]
                    request["initial_status"] = attempt["initial_status"]
                if phase == "revision":
                    request["feedback"] = attempt["feedback"]
                if len(_encoded(request)) > resources["max_context_bytes"] or len(attempt["calls"]) >= resources["max_calls_per_attempt"]:
                    attempt.update(status="budget_exhausted", stop_reason="pre_dispatch_context_or_call_limit")
                    break
                # Recheck bound source files at each dispatch, not just startup.
                if driver_identity(drivers[role]) != manifest["drivers"][role]:
                    attempt.update(status="blocked", stop_reason="driver_source_changed")
                    stop = "campaign_stopped_after_driver_source_change"
                    break
                call = _invoke(drivers[role], request, resources, directory, len(attempt["calls"]) + 1)
                attempt["calls"].append(call)
                usage = _usage(attempt["calls"])
                if not usage["complete"]:
                    attempt.update(status=call["status"] if call["status"] == "failed" else "unknown_usage",
                        stop_reason="unknown_or_invalid_fixture_usage")
                    stop = "campaign_stopped_after_unknown_usage"
                    break
                if (usage["cost_usd"] > resources["max_cost_usd"] or usage["elapsed_seconds"] > resources["max_elapsed_seconds"]
                        or usage["wall_elapsed_seconds"] > resources["max_elapsed_seconds"]):
                    attempt.update(status="budget_exhausted", stop_reason="observed_fixture_resource_overrun")
                    stop = "campaign_stopped_after_resource_overrun"
                    break
                if call["status"] in {"completed", "abstained"}:
                    phase_completed = True
                    output = _copy(call["output"])
                    if phase in {"workflow", "initial"}:
                        attempt["initial_output"] = output
                        attempt["initial_status"] = call["status"]
                    if phase == "review":
                        attempt["feedback"] = output
                    elif phase in {"workflow", "revision"} or trial["arm"] == ARMS[1]:
                        attempt["final_output"] = output
                    # An initial analyst refusal is reviewable evidence. Only
                    # the terminal analyst response (or reviewer refusal to
                    # continue) ends the reviewed workflow as abstained.
                    if call["status"] == "abstained" and not (phase == "initial" and trial["arm"] == ARMS[2]):
                        attempt.update(status="abstained", stop_reason="driver_scoped_abstention")
                    break
                if call["status"] != "failed" or not call["retryable"] or driver_attempt == resources["max_driver_attempts"]:
                    attempt.update(status=call["status"], stop_reason="local_driver_did_not_complete")
                    break
            if not phase_completed or attempt["status"] != "completed":
                break
        attempt["usage"] = _usage(attempt["calls"])
        for field, name in (("initial_output", "initial_sha256"), ("feedback", "feedback_sha256"), ("final_output", "final_sha256")):
            attempt[name] = _digest(attempt[field]) if attempt[field] is not None else None
        attempt["attempt_sha256"] = _digest(attempt)
        identity = _write(directory / "attempt.json", attempt)
        bindings.append({"attempt_id": trial["attempt_id"], "path": str(directory / "attempt.json"), "sha256": identity})
        attempts.append(attempt)
    seal = {"format": FORMAT, "manifest_sha256": prepared.manifest_sha256, "attempts": bindings,
        "full_denominator": len(manifest["schedule"]), "all_attempts_sealed_before_checks": True}
    seal_path = root / "campaign-seal.json"
    identity = _write(seal_path, seal)
    return {"seal_path": str(seal_path), "seal_sha256": identity, "attempts": _copy(attempts)}


def _verified_campaign(prepared, receipt, *, captures=None, legacy_replay=False):
    captures = captures or _Captures()
    manifest = _manifest(prepared, captures=captures, legacy_replay=legacy_replay)
    root = prepared.root
    if (not isinstance(receipt, dict) or set(receipt) != {"seal_path", "seal_sha256", "attempts"}
            or receipt["seal_path"] != str(root / "campaign-seal.json")
            or not _sha(receipt["seal_sha256"])):
        raise ValueError("Comparison campaign receipt seal changed")
    seal = captures.read(root / "campaign-seal.json", receipt["seal_sha256"])
    if (seal["manifest_sha256"] != prepared.manifest_sha256 or seal["full_denominator"] != len(manifest["schedule"])
            or len(seal["attempts"]) != len(manifest["schedule"])):
        raise ValueError("Comparison campaign denominator or manifest binding changed")
    attempts = []
    for trial, binding in zip(manifest["schedule"], seal["attempts"]):
        path = root / "attempts" / trial["attempt_id"] / "attempt.json"
        if (set(binding) != {"attempt_id", "path", "sha256"} or binding["attempt_id"] != trial["attempt_id"]
                or binding["path"] != str(path)):
            raise ValueError("Retained attempt artifact hash changed")
        attempt = captures.read(path, binding["sha256"])
        identity = attempt.pop("attempt_sha256", None)
        if identity != _digest(attempt) or any(attempt.get(k) != v for k, v in trial.items()):
            raise ValueError("Retained attempt binding changed")
        attempt["attempt_sha256"] = identity
        for number, call in enumerate(attempt["calls"], 1):
            call_path = path.parent / ("call-%04d.json" % number)
            record = {k: v for k, v in call.items() if k not in {"capture_path", "capture_sha256"}}
            if (call["capture_path"] != str(call_path)
                    or captures.read(call_path, call["capture_sha256"]) != record):
                raise ValueError("Retained call capture hash changed")
        if attempt["usage"] != _usage(attempt["calls"]):
            raise ValueError("Retained attempt usage changed")
        for field, name in (("initial_output", "initial_sha256"), ("feedback", "feedback_sha256"), ("final_output", "final_sha256")):
            if attempt[name] != (_digest(attempt[field]) if attempt[field] is not None else None):
                raise ValueError("Initial/review/final output hash changed")
        attempts.append(attempt)
    if receipt["attempts"] != attempts:
        raise ValueError("Returned receipt attempts differ from retained attempts")
    return manifest, attempts


def _outcomes(records, attempts, protocol):
    records = _copy(records)
    if not isinstance(records, list) or len(records) > len(attempts):
        raise ValueError("Use unique bounded coordinator outcomes")
    known = {attempt["attempt_id"]: attempt for attempt in attempts}
    checked = {}
    fields = {"attempt_id", "attempt_sha256", "scope", "endpoints", "initial_errors", "final_errors"}
    optional = {"check_source_sha256", "error_check_source_sha256"}
    for record in records:
        if (not isinstance(record, dict) or fields - set(record) or set(record) - fields - optional
                or record["attempt_id"] not in known):
            raise ValueError("Invalid coordinator outcome binding")
        identity = record["attempt_id"]
        if identity in checked:
            raise ValueError("Duplicate checked outcome")
        attempt = known[identity]
        if record["scope"] != SCOPE:
            raise PermissionError("Only coordinator checked fixture scope is supported")
        if record["attempt_sha256"] != attempt["attempt_sha256"]:
            raise ValueError("Coordinator outcome attempt hash binding changed")
        checkers = protocol["coordinator_checks"]
        sources = {identity["source_sha256"] for identity in checkers.values()}
        # The global field remains a fallback for older same-source records.
        # Distinct scientific checkers bind each assessed endpoint separately;
        # error taxonomy belongs to the independently declared primary checker.
        fallback = record.get("check_source_sha256")
        if "check_source_sha256" in record and (not _sha(fallback) or fallback not in sources):
            raise ValueError("Coordinator checker source is not bound to frozen checks")
        error_source = record.get("error_check_source_sha256", fallback)
        if "error_check_source_sha256" in record and (not _sha(error_source) or error_source not in sources):
            raise ValueError("Error labels require a frozen primary checker source")
        if not isinstance(record["endpoints"], dict) or set(record["endpoints"]) != set(ENDPOINTS):
            raise ValueError("Keep all endpoint assessments separate")
        for name, endpoint in record["endpoints"].items():
            if not isinstance(endpoint, dict):
                raise ValueError("Invalid checked endpoint")
            if endpoint.get("status") == "assessed":
                required = {"status", "value", "evidence_sha256"}
                if (required - set(endpoint) or set(endpoint) - required - {"check_source_sha256"}
                        or type(endpoint["value"]) is not bool or not _sha(endpoint["evidence_sha256"])):
                    raise ValueError("Assessed outcomes need a boolean and bound evidence")
                endpoint_source = endpoint.get("check_source_sha256", fallback)
                if not _sha(endpoint_source) or endpoint_source != checkers[name]["source_sha256"]:
                    raise ValueError("Assessed endpoint checker source differs from the frozen appropriate checker")
                if name in {"numeric_correctness", "claim_support", "replay_validity"} and (
                        attempt["status"] != "completed" or attempt["final_output"] is None):
                    raise ValueError("Output-dependent endpoints require an eligible completed final output")
                if name in {"design_validity", "appropriate_refusal"} and (
                        attempt["status"] not in {"completed", "abstained"} or attempt["final_output"] is None):
                    raise ValueError("Design/refusal endpoints require an eligible completed or abstained final output")
            elif (set(endpoint) != {"status", "value", "reason"} or endpoint["status"] != "not_assessed"
                    or endpoint["value"] is not None or not _text(endpoint["reason"])):
                raise ValueError("Unassessed endpoints require a reason and null value")
        for field, output in (("initial_errors", "initial_output"), ("final_errors", "final_output")):
            errors = record[field]
            if errors is not None and (not isinstance(errors, list) or len(errors) > 100
                    or any(not _text(error) for error in errors) or len(set(errors)) != len(errors)):
                raise ValueError("Errors require unique coordinator-defined identifiers")
            if attempt[output] is None and errors is not None:
                raise ValueError("Absent outputs cannot have checked error labels")
            if errors is not None and (not _sha(error_source)
                    or error_source != checkers[protocol["primary_endpoint"]]["source_sha256"]):
                raise ValueError("Error labels must bind the frozen primary-endpoint checker source")
        checked[identity] = record
    return checked


def _paired_effects(manifest, attempts, checked, *, execution_completion=False):
    """Exploratory paired locked-test effects; repetitions never increase units."""
    result = {}
    for name in (("execution_completion",) if execution_completion else ENDPOINTS):
        pairs = {}
        for attempt in attempts:
            if attempt["split"] != "locked_test" or attempt["arm"] not in ARMS[1:]:
                continue
            key = (attempt["case_id"], attempt["replicate"])
            assessment = ({"status": "assessed", "value": attempt["status"] in {"completed", "abstained"}
                and attempt["final_output"] is not None} if execution_completion else
                checked.get(attempt["attempt_id"], {}).get("endpoints", {}).get(name, {}))
            pairs.setdefault(key, {"cluster": attempt["cluster_id"], "arms": {}})["arms"][attempt["arm"]] = (
                int(assessment["value"]) if assessment.get("status") == "assessed" else None)
        grouped, omitted = {}, 0
        for pair in pairs.values():
            values = pair["arms"]
            if any(values.get(arm) is None for arm in ARMS[1:]):
                omitted += 1
                continue
            grouped.setdefault(pair["cluster"], []).append(values[ARMS[2]] - values[ARMS[1]])
        means = [statistics.mean(values) for _, values in sorted(grouped.items())]
        policy = manifest["analysis_policy"]
        record = {"split": "locked_test", "contrast": "analyst_plus_reviewer-minus-analyst_same_tools_checks",
            "independent_clusters": len(means), "paired_units": sum(len(values) for values in grouped.values()),
            "unassessed_pairs": omitted, "estimate": statistics.mean(means) if means else None,
            "interval": None, "status": "insufficient_independent_clusters", "policy": policy,
            "limitations": ["Dataset relationships and checks are coordinator declarations.",
                "Repeated attempts and related views are not independent datasets.",
                "Small cluster counts and selected public fixtures cannot establish scientific improvement.",
                "Intervals are exploratory and are not adjusted for multiple endpoints."]}
        if len(means) >= policy["minimum_clusters_for_interval"]:
            rng = random.Random(manifest["protocol"]["ordering_seed"])
            samples = sorted(statistics.mean(rng.choices(means, k=len(means))) for _ in range(policy["bootstrap_resamples"]))
            record.update(status="descriptive_cluster_bootstrap", interval=[samples[24], samples[974]])
        result[name] = record
    return result


def _report(manifest, attempts, outcomes):
    checked = _outcomes(outcomes, attempts, manifest["protocol"])
    arms = {}
    for arm in ARMS:
        group = [attempt for attempt in attempts if attempt["arm"] == arm]
        endpoint_counts = {name: {"assessed": 0, "not_assessed": 0, "passed": 0, "failed": 0} for name in ENDPOINTS}
        errors = {"corrected": 0, "introduced": 0, "unresolved": 0, "not_assessed": 0}
        execution = {"denominator": len(group), "completed_with_final_output": 0,
            "abstained_with_final_output": 0, "abstained_without_final_output": 0,
            "failure_before_initial": 0, "failure_after_initial": 0,
            "introduced_after_clean_initial": 0, "failure_with_unassessed_initial": 0,
            "scope": "execution_outcome_not_numeric_or_scientific_correctness"}
        for attempt in group:
            outcome = checked.get(attempt["attempt_id"])
            if attempt["status"] in {"completed", "abstained"}:
                key = attempt["status"] + ("_with_final_output" if attempt["final_output"] is not None else "_without_final_output")
                execution[key] = execution.get(key, 0) + 1
            else:
                execution["failure_before_initial" if attempt["initial_output"] is None else "failure_after_initial"] += 1
                if attempt["initial_output"] is not None:
                    if outcome and outcome["initial_errors"] == []:
                        execution["introduced_after_clean_initial"] += 1
                    elif not outcome or outcome["initial_errors"] is None:
                        execution["failure_with_unassessed_initial"] += 1
            for name in ENDPOINTS:
                assessment = outcome["endpoints"][name] if outcome else {"status": "not_assessed"}
                counts = endpoint_counts[name]
                if assessment["status"] == "assessed":
                    counts["assessed"] += 1
                    counts["passed" if assessment["value"] else "failed"] += 1
                else:
                    counts["not_assessed"] += 1
            if not outcome or outcome["initial_errors"] is None or outcome["final_errors"] is None:
                errors["not_assessed"] += 1
            else:
                initial, final = set(outcome["initial_errors"]), set(outcome["final_errors"])
                errors["corrected"] += len(initial - final)
                errors["introduced"] += len(final - initial)
                errors["unresolved"] += len(initial & final)
        arms[arm] = {"denominator": len(group), "completion": {status: sum(a["status"] == status for a in group) for status in STATUSES},
            "endpoints": endpoint_counts, "errors": errors, "execution_outcomes": execution,
            "usage": _usage([call for a in group for call in a["calls"]])}
    return {"format": FORMAT, "scope": "software_fixture", "denominator": len(attempts), "arms": arms,
        "statuses": {status: sum(a["status"] == status for a in attempts) for status in STATUSES},
        "unknown_usage_attempts": sum(not a["usage"]["complete"] for a in attempts),
        "usage": _usage([call for a in attempts for call in a["calls"]]),
        "intervention_count": sum(len(call["interventions"]) for a in attempts for call in a["calls"]),
        "paired_effects": _paired_effects(manifest, attempts, checked),
        "paired_execution_completion": _paired_effects(manifest, attempts, checked,
            execution_completion=True)["execution_completion"],
        "scientific_improvement_claim": False, "qualified_independent_review": False,
        "limitations": ["Trusted local callbacks are not an OS sandbox and their complete dependency state is not frozen.",
            "Capabilities and usage are fixture declarations, not proof of provider isolation or actual billing.",
            "Callback wall deadlines are checked after return, not externally enforced.",
            "Coordinator checker source bindings do not prove that a checker ran or authenticate independent scientific adjudication."]}


def summarize_comparison(prepared, receipt, *, checked_outcomes):
    """Freeze separate coordinator checks after every attempt is retained."""
    manifest, attempts = _verified_campaign(prepared, receipt)
    report = _report(manifest, attempts, checked_outcomes)
    root = prepared.root
    _write(root / "summary-reservation.json", {"campaign_seal_sha256": receipt["seal_sha256"]})
    outcomes_hash = _write(root / "checked-outcomes.json", checked_outcomes)
    report_hash = _write(root / "report.json", report)
    seal = {"format": FORMAT, "manifest_sha256": prepared.manifest_sha256,
        "campaign_seal_sha256": receipt["seal_sha256"], "checked_outcomes_sha256": outcomes_hash,
        "report_sha256": report_hash, "scope": "custody_not_authenticated_scientific_approval"}
    identity = _write(root / "report-seal.json", seal)
    return {**report, "report_seal_sha256": identity}


def replay_comparison(root_absolute, expected_seal):
    """Read-only seal verification. Never imports drivers, executes or rescores."""
    root = _path(root_absolute)
    if not _sha(expected_seal):
        raise ValueError("External comparison report seal hash differs")
    captures = _Captures()
    seal = captures.read(root / "report-seal.json", expected_seal)
    prepared = PreparedComparison(root, seal["manifest_sha256"])
    manifest = _manifest(prepared, captures=captures, legacy_replay=True)
    campaign = captures.read(root / "campaign-seal.json", seal["campaign_seal_sha256"])
    if len(campaign["attempts"]) != len(manifest["schedule"]):
        raise ValueError("Comparison campaign denominator changed")
    attempts = []
    for trial, binding in zip(manifest["schedule"], campaign["attempts"]):
        path = root / "attempts" / trial["attempt_id"] / "attempt.json"
        if binding.get("attempt_id") != trial["attempt_id"] or binding.get("path") != str(path):
            raise ValueError("Retained attempt path binding changed")
        attempts.append(captures.read(path, binding["sha256"]))
    receipt = {"seal_path": str(root / "campaign-seal.json"), "seal_sha256": seal["campaign_seal_sha256"], "attempts": attempts}
    _verified_campaign(prepared, receipt, captures=captures, legacy_replay=True)
    if campaign["manifest_sha256"] != seal["manifest_sha256"]:
        raise ValueError("Campaign and report seal identities differ")
    captures.read(root / "checked-outcomes.json", seal["checked_outcomes_sha256"])
    report = captures.read(root / "report.json", seal["report_sha256"])
    return {"valid": True, "report_seal_sha256": expected_seal, "scope": "read_only_custody_verification_no_dispatch_no_rescoring",
        "legacy_unbound_checks": manifest["protocol"]["format"] == "devbench.rna-workflow-comparison-protocol/1",
        "report": report}


def _example_checked(request):
    data = request["case"]["public_data"]
    value = statistics.mean(data["right"]) - statistics.mean(data["left"])
    return {"status": "completed", "output": {"estimate": value}, "interventions": [],
        "usage": {"tokens": 0, "cost_usd": 0, "elapsed_seconds": 0}}


def _example_analyst(request):
    result = _example_checked(request)
    if request["phase"] == "initial" and request["case"]["public_data"]["mode"] in {"correctable", "unresolved"}:
        result["output"]["estimate"] += 10
    elif request["phase"] == "revision":
        result["output"]["estimate"] = request["feedback"]["suggested_estimate"]
    return result


def _example_reviewer(request):
    value = _example_checked(request)["output"]["estimate"]
    if request["case"]["public_data"]["mode"] in {"introduced", "unresolved"}:
        value += 10
    return {"status": "completed", "output": {"suggested_estimate": value}, "interventions": [],
        "usage": {"tokens": 0, "cost_usd": 0, "elapsed_seconds": 0}}


def _example(root):
    protocol = {"format": "devbench.rna-workflow-comparison-protocol/2", "scope": "software_fixture",
        "arms": list(ARMS), "splits": list(SPLITS), "replicates": 2, "ordering_seed": 417, "seed_supported": True,
        "identities": {"tools": {"difference": _digest("fixture-arithmetic/1")},
            "checks": {"independent_difference": _digest("fixture-difference-check/1")},
            "evidence": {"source": _digest("new-public-arithmetic-fixtures/1")},
            "model": {"name": "no-provider-fixture", "version": "1"},
            "memory": {"version": "empty/1", "sha256": _digest([])},
            "resources": {"max_calls_per_attempt": 4, "max_driver_attempts": 2, "max_context_bytes": 64000,
                "max_output_bytes": 64000, "max_elapsed_seconds": 30, "max_cost_usd": 0}},
        "stop_on_unknown_usage": True, "endpoints": list(ENDPOINTS), "primary_endpoint": "numeric_correctness", "endpoint_scope": SCOPE,
        "coordinator_checks": {name: driver_identity(_example_outcomes) for name in ENDPOINTS}}
    cases = [{"case_id": "fixture-" + str(i), "dataset_id": "dataset-" + str(i), "family_id": "family-" + str(i),
        "cohort_ids": ["cohort-" + str(i)], "donor_ids": ["donor-" + str(i)], "provenance_ids": ["source-" + str(i)],
        "split": split, "public_data": {"left": [1, 2, 3], "right": [4, 5, 6], "mode": mode}}
        for i, (split, mode) in enumerate(zip(SPLITS, ("correctable", "introduced", "unresolved")))]
    prepared = prepare_comparison(root, protocol=protocol, cases=cases, driver_identities={
        "deterministic": driver_identity(_example_checked), "analyst": driver_identity(_example_analyst),
        "reviewer": driver_identity(_example_reviewer)})
    receipt = run_comparison(prepared, deterministic_driver=_example_checked, analyst_driver=_example_analyst,
        reviewer_driver=_example_reviewer, authorization={"scope": "local_fixture", "allow_model_execution": False,
            "allow_native_execution": False, "allow_paid_execution": False})
    return summarize_comparison(prepared, receipt, checked_outcomes=_example_outcomes(receipt, cases))


def _example_outcomes(receipt, cases):
    """Fixed local coordinator checker, prospectively source-bound per endpoint."""
    outcomes = []
    for attempt in receipt["attempts"]:
        endpoints = {name: {"status": "not_assessed", "value": None, "reason": "Arithmetic fixture does not assess this endpoint."} for name in ENDPOINTS}
        # Independent fixture arithmetic: pairwise differences instead of the
        # participant's difference-of-means implementation.
        case = next(case for case in cases if case["case_id"] == attempt["case_id"])
        data = case["public_data"]
        expected = sum(right - left for left, right in zip(data["left"], data["right"])) / len(data["left"])
        initial, final = attempt["initial_output"], attempt["final_output"]
        if final is not None and attempt["status"] == "completed":
            endpoints["numeric_correctness"] = {"status": "assessed", "value": final["estimate"] == expected,
                "evidence_sha256": _digest({"expected": expected, "observed": final["estimate"]})}
        outcomes.append({"attempt_id": attempt["attempt_id"], "attempt_sha256": attempt["attempt_sha256"], "scope": SCOPE,
            "endpoints": endpoints, "initial_errors": (["wrong_estimate"] if initial["estimate"] != expected else []) if initial else None,
            "final_errors": (["wrong_estimate"] if final["estimate"] != expected else []) if final else None,
            "check_source_sha256": _hash_bytes(_bytes(Path(__file__).absolute()))})
    return outcomes


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--offline-example", action="store_true")
    modes.add_argument("--replay", action="store_true")
    modes.add_argument("--native", action="store_true")
    parser.add_argument("--output", required=True)
    parser.add_argument("--expected-seal")
    args = parser.parse_args(argv)
    if args.native:
        print(json.dumps({"status": "blocked", "provider_calls": 0,
            "reason": "This additive fixture module cannot grant native, paid or provider authority."}))
        return 2
    if args.replay:
        if not args.expected_seal:
            parser.error("--replay requires the externally retained --expected-seal")
        result = replay_comparison(args.output, args.expected_seal)
        output = {key: result[key] for key in ("valid", "report_seal_sha256", "scope")}
    else:
        result = _example(_path(args.output))
        output = {key: result[key] for key in ("scope", "denominator", "scientific_improvement_claim", "report_seal_sha256")}
        output["provider_calls"] = 0  # Only the fixed arithmetic functions above run.
    print(json.dumps(output, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
