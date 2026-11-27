"""Workspace FIFO scheduling and identity-fenced recovery on the scientific WAL."""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import os
import re
import signal
import sqlite3
import subprocess
import threading
import time
from collections import Counter
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import psutil

from devbench.core._files import read_bounded_file
from devbench.daemon import auth
from devbench.engine.mounted_kernel_oci import (
    _daemon,
    _private_runtime,
    recover_mounted_container,
)
from devbench.engine.scientific_authority import verify_frozen_plan
from devbench.engine.workflow_operations import OperationBlocked
from devbench.platform.plugin_jobs import (
    JobStore,
    _spawn_worker,
    canonical,
    directory,
    fastq_cpu_slots,
    require_execution_policy,
    verify_native_resume_plan,
)

_DEADLINES = {"qc": 300, "standard": 14_400, "long": 259_200}
_MAX_CHECKPOINT = 4 * 1024**2
_RUN_ID = re.compile(r"[a-f0-9]{32}")
_RUNNING = ("running", "cancelling")
_POLICY_HOLDS = {
    "private_data_local_studio_only": "awaiting_offline",
    "explicit_plan_approval_required": "awaiting_approval",
    "outcome_blind_critic_required": "awaiting_approval",
}
_LOCAL: dict[Path, Supervisor] = {}
_LOCAL_LOCK = threading.Lock()


def local_supervisor(workspace):
    with _LOCAL_LOCK:
        return _LOCAL.get(workspace)


def _private_dir(path):
    if path.resolve() != path:
        raise PermissionError("Daemon run directory may not contain symlinks")
    with auth.owned_directory(path.parent) as parent:
        try:
            os.mkdir(path.name, 0o700, dir_fd=parent)
        except FileExistsError:
            pass
    with auth.owned_directory(path):
        return path


def _run_root(workspace, rid):
    if not isinstance(rid, str) or not _RUN_ID.fullmatch(rid):
        raise ValueError("Invalid run identity")
    with auth.state_directory(workspace, create=True):
        runs = _private_dir(workspace / auth.STATE_DIR / "runs")
        return _private_dir(runs / rid)


def _replace(path, raw):
    with auth.owned_directory(path.parent) as parent:
        auth.write_file(parent, path.name, raw)


def _control_bytes(path, limit):
    with auth.owned_directory(path.parent) as parent:
        return auth.read_file(parent, path.name, limit)


def _content_addressed(directory, raw):
    sha = hashlib.sha256(raw).hexdigest()
    path = directory / sha
    if path.exists():
        if _control_bytes(path, _MAX_CHECKPOINT) != raw:
            raise OperationBlocked("checkpoint_identity_mismatch")
    else:
        _replace(path, raw)
    return sha


def save_checkpoint(workspace, row, step, state):
    """Persist a completed worker cursor before publishing its digest in the WAL."""
    if type(step) is not str or not 0 < len(step) <= 256 or type(state) is not dict:
        raise ValueError("Checkpoint requires a step and a state object")
    payload = canonical(state)
    if len(payload) > _MAX_CHECKPOINT:
        raise ValueError("Checkpoint exceeds its size limit")
    root = _run_root(workspace, row["run_id"])
    objects = _private_dir(root / "checkpoints")
    manifest = {
        "format": "devbench.worker-checkpoint/1",
        "run_id": row["run_id"],
        "session_id": row["session_id"],
        "plan_id": row["plan_id"],
        "plan_sha256": row["plan_sha256"],
        "step": step,
        "state_sha256": _content_addressed(objects, payload),
        "state_bytes": len(payload),
        "previous_sha256": row["checkpoint_sha256"],
    }
    encoded = canonical(manifest)
    sha = _content_addressed(objects, encoded)
    _replace(root / "checkpoint_manifest.json", encoded)
    return {**manifest, "sha256": sha}


def _committed_manifest(root, expected):
    raw = _control_bytes(root / "checkpoint_manifest.json", 4096)
    objects = root / "checkpoints"
    actual = hashlib.sha256(raw).hexdigest()
    if _control_bytes(objects / actual, 4096) != raw:
        raise ValueError("Unregistered checkpoint manifest")
    if actual != expected:
        # The manifest file can be precisely one prepared generation ahead
        # of the WAL after SIGKILL. Only the committed generation is usable.
        if json.loads(raw).get("previous_sha256") != expected:
            raise ValueError("Manifest identity changed")
        raw = _control_bytes(objects / expected, 4096)
    sha = hashlib.sha256(raw).hexdigest()
    if not hmac.compare_digest(sha, expected):
        raise ValueError("Manifest identity changed")
    manifest = json.loads(raw)
    return manifest


def verified_checkpoint(workspace, previous, current, *, publication_retry=False):
    if previous is None or not previous["checkpoint_sha256"]:
        raise OperationBlocked("checkpoint_unavailable")
    if not publication_retry and (
        previous["status"] != "interrupted" or previous["cleanup_state"] != "confirmed"
    ):
        raise OperationBlocked("checkpoint_unavailable")
    keys = ("session_id", "plan_id", "plan_sha256")
    if any(previous[key] != current[key] for key in keys):
        raise OperationBlocked("checkpoint_identity_mismatch")
    root = _run_root(workspace, previous["run_id"])
    try:
        sha = previous["checkpoint_sha256"]
        manifest = _committed_manifest(root, sha)
        obj = manifest["state_sha256"]
        if (
            manifest["format"] != "devbench.worker-checkpoint/1"
            or manifest["run_id"] != previous["run_id"]
            or any(manifest[key] != current[key] for key in keys)
            or not re.fullmatch(r"[a-f0-9]{64}", obj)
        ):
            raise ValueError("Checkpoint scope changed")
        raw_state = _control_bytes(root / "checkpoints" / obj, _MAX_CHECKPOINT)
        if (
            len(raw_state) != manifest["state_bytes"]
            or hashlib.sha256(raw_state).hexdigest() != obj
        ):
            raise ValueError("Checkpoint state changed")
        return {**manifest, "sha256": sha, "state": json.loads(raw_state)}
    except (KeyError, TypeError, ValueError, OSError) as exc:
        raise OperationBlocked("checkpoint_identity_mismatch") from exc


def _process(row):
    if row["pid"] is None or row["create_time"] is None:
        return None
    try:
        process = psutil.Process(row["pid"])
        if (
            process.create_time() == row["create_time"]
            and process.status() != psutil.STATUS_ZOMBIE
        ):
            return process
    except psutil.NoSuchProcess:
        pass
    return None


def worker_heartbeat(workspace, row):
    root = _run_root(workspace, row["run_id"])
    pulse = {
        "generation": row["generation"],
        "updated_at": time.time(),
        "pid": row["pid"],
        "create_time": row["create_time"],
    }
    _replace(root / "worker-heartbeat.json", canonical(pulse))


def _worker_pulse(root, row, stage):
    try:
        pulse = json.loads(_control_bytes(root / "worker-heartbeat.json", 2048))
        matching = all(
            pulse[key] == row[key] for key in ("generation", "pid", "create_time")
        )
        last = pulse["updated_at"] if matching else None
    except (FileNotFoundError, KeyError, TypeError, ValueError):
        last = None
    reference = last or row["dispatched_at"] or row["started_at"]
    stale = row["status"] == "running" and time.time() - reference > 3 * stage.get(
        "_heartbeat_sec", 10
    )
    return {
        "worker_heartbeat_utc": datetime.fromtimestamp(last, UTC).isoformat()
        if last
        else None,
        "worker_heartbeat_stale": stale,
    }


def write_progress(workspace, row):
    stage = json.loads(row["stage_json"])
    process = _process(row)
    rss = 0
    if process is not None:
        with suppress(psutil.NoSuchProcess):
            rss = process.memory_info().rss // 1024**2
    percent = stage.get("percent", 0)
    if row["status"] == "completed":
        percent = 100
    if (
        not isinstance(percent, (int, float))
        or not math.isfinite(percent)
        or not 0 <= percent <= 100
    ):
        raise ValueError("Progress percentage must be between zero and 100")
    root = _run_root(workspace, row["run_id"])
    progress = {
        **_worker_pulse(root, row, stage),
        "step": stage.get("step", row["status"]),
        "percent": percent,
        "rss_mb": rss,
        "updated_at_utc": datetime.now(UTC).isoformat(),
    }
    _replace(root / "progress.json", canonical(progress))


def _group_exists(pid):
    try:
        os.killpg(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _group_bound(row):
    process = _process(row)
    try:
        return (
            process is not None
            and os.getpgid(process.pid) == os.getsid(process.pid) == process.pid
        )
    except ProcessLookupError:
        return False


def observe_children(row, stage):
    """Persist birth identities while the leader is still verifiably ours."""
    process = _process(row)
    if process is None:
        return
    identities = {}
    with suppress(psutil.NoSuchProcess):
        for child in process.children(recursive=True):
            with suppress(psutil.NoSuchProcess, ProcessLookupError):
                identities[child.pid] = child.create_time()
    if len(identities) > 4096:
        raise OperationBlocked("worker_process_limit_exceeded")
    if identities:
        retained = dict(stage.get("_children", []))
        retained.update(identities)
        stage["_children"] = sorted(retained.items())[-4096:]


def _known_children(row):
    for pid, created in json.loads(row["stage_json"]).get("_children", ()):
        try:
            child = psutil.Process(pid)
            if (
                child.create_time() == created
                and child.status() != psutil.STATUS_ZOMBIE
            ):
                yield child
        except psutil.NoSuchProcess:
            continue


def _owned_group_member(row):
    for child in _known_children(row):
        with suppress(ProcessLookupError):
            if os.getpgid(child.pid) == os.getsid(child.pid) == row["pid"]:
                return True
    return False


def _stop_detached_children(row, grace):
    children = []
    for child in _known_children(row):
        with suppress(ProcessLookupError):
            if os.getpgid(child.pid) != row["pid"]:
                children.append(child)
    for child in children:
        with suppress(psutil.NoSuchProcess):
            child.terminate()
    _, remaining = psutil.wait_procs(children, timeout=grace)
    for child in remaining:
        with suppress(psutil.NoSuchProcess):
            child.kill()
    _, remaining = psutil.wait_procs(remaining, timeout=2)
    return not remaining


def _leader_reused(pid, created):
    try:
        return psutil.Process(pid).create_time() != created
    except psutil.NoSuchProcess:
        return False


def _stop_group(row, grace):
    """Signal a new-session worker only after checking its birth time and PGID."""
    pid = row["pid"]
    if pid is None:
        return True
    if type(pid) is not int or pid <= 1:
        return False
    if not _group_exists(pid):
        return True
    if not (_group_bound(row) or _owned_group_member(row)):
        return False
    with suppress(ProcessLookupError):
        os.killpg(pid, signal.SIGTERM)
    for sig, seconds in ((None, grace), (signal.SIGKILL, 2)):
        if sig:
            # The original group can outlive its leader. Never hit a reused PID.
            if _leader_reused(pid, row["create_time"]):
                return False
            with suppress(ProcessLookupError):
                os.killpg(pid, sig)
        until = time.monotonic() + seconds
        while _group_exists(pid) and time.monotonic() < until:
            time.sleep(min(0.025, max(0, until - time.monotonic())))
        if not _group_exists(pid):
            return True
    return False


_OCI_RECORDS = ("oci-create-intent.json", "oci-created.json", "oci-cleanup.json")


def _container_directories(root):
    # Only inspect this interrupted run. A truncated inventory is never evidence
    # of zero containers. Normal completion avoids walking large result trees.
    if not root.exists():
        return set()
    if root.resolve() != root:
        raise ValueError("Run container inventory contains a symlink")
    pending, found, remaining = [root], set(), 65_536
    while pending:
        parent = pending.pop()
        with os.scandir(parent) as entries:
            for entry in entries:
                remaining -= 1
                if remaining < 0:
                    raise OperationBlocked("container_inventory_unverified")
                if entry.name in _OCI_RECORDS:
                    found.add(parent)
                elif entry.is_dir(follow_symlinks=False):
                    pending.append(Path(entry.path))
    return found


def _mounted_bindings(root, stage, *, interrupted):
    explicit = stage.get("mounted_container")
    bindings = {}
    if explicit is not None:
        path = Path(explicit["stage"])
        if (
            not path.is_absolute()
            or path.resolve() != path
            or not path.is_relative_to(root)
        ):
            raise ValueError("Mounted stage is outside this run")
        bindings[path.parent] = (
            path,
            explicit["request_sha"],
            explicit["image_id"],
            explicit["attempt_id"],
        )
    # The existing Salmon worker records its mounted model under the owned attempt.
    candidates = _container_directories(root) if interrupted else {root}
    if attempt := stage.get("nextflow_attempt"):
        attempt = Path(attempt)
        if attempt.parent != root / "nextflow" or attempt.resolve() != attempt:
            raise ValueError("Nextflow attempt is outside this run")
        candidates.add(attempt / "count-model")
    for parent in candidates:
        if parent in bindings:
            continue
        records = [parent / name for name in _OCI_RECORDS]
        record = next((p for p in records if p.exists() or p.is_symlink()), None)
        if record is None:
            continue
        saved = json.loads(read_bounded_file(record, 16_384))
        paths = [parent / name for name in ("mounted", ".work", "stage")]
        stage_path = next((p for p in paths if p.is_dir()), paths[0])
        if stage_path.resolve() != stage_path:
            raise ValueError("Mounted stage contains a symlink")
        bindings[parent] = (
            stage_path,
            saved["request_sha256"],
            saved["image_id"],
            saved["name"].removeprefix("devbench-rna-tier-a-"),
        )
    return bindings.values()


def nextflow_docker_binding(attempt):
    with _private_runtime(attempt) as runtime:
        return _daemon(runtime)


def _nextflow_containers(attempt, binding):
    """Remove exactly this attempt's labeled containers on its launch daemon."""
    if not isinstance(binding, dict) or set(binding) != {"endpoint", "daemon_id"}:
        raise OperationBlocked("nextflow_docker_custody_unverified")
    label = "devbench.fastq.attempt=" + attempt.name
    query = ["container", "ls", "-aq", "--no-trunc", "--filter", "label=" + label]
    with _private_runtime(attempt, endpoint=binding["endpoint"]) as runtime:
        if _daemon(runtime) != binding:
            raise OperationBlocked("nextflow_docker_custody_unverified")
        ids = runtime._call(query).splitlines()
        if len(ids) > 256 or any(
            not re.fullmatch(r"[0-9a-f]{64}", item) for item in ids
        ):
            raise OperationBlocked("nextflow_container_inventory_unverified")
        for cid in ids:
            observed = runtime._call(
                [
                    "container",
                    "inspect",
                    "--format",
                    '{{index .Config.Labels "devbench.fastq.attempt"}}',
                    cid,
                ]
            )
            if observed != attempt.name or _daemon(runtime) != binding:
                raise OperationBlocked("nextflow_docker_custody_unverified")
            if runtime._call(["rm", "--force", cid]) != cid:
                raise OperationBlocked("nextflow_container_cleanup_unverified")
        if runtime._call(query) or _daemon(runtime) != binding:
            raise OperationBlocked("nextflow_container_cleanup_unverified")
    return {"status": "complete", "matched_container_count": 0, "label": label}


def _nextflow_process(result):
    pid, birth = (
        result.get("engine_process_id"),
        result.get("engine_process_create_time"),
    )
    if (
        type(pid) is not int
        or pid <= 1
        or type(birth) not in {int, float}
        or not math.isfinite(birth)
        or birth <= 0
    ):
        raise OperationBlocked("nextflow_engine_identity_unverified")
    if psutil.pid_exists(pid):
        with suppress(psutil.NoSuchProcess, ProcessLookupError):
            process = psutil.Process(pid)
            if process.create_time() != birth or os.getsid(pid) != pid:
                raise OperationBlocked("nextflow_engine_identity_unverified")
    return pid, birth


def _version_probe_identity(proof):
    if not isinstance(proof, dict) or set(proof) != {"pid", "create_time"}:
        raise OperationBlocked("nextflow_version_probe_unverified")
    pid, birth = proof["pid"], proof["create_time"]
    if (
        type(pid) is not int
        or pid <= 1
        or type(birth) not in {float, int}
        or (not math.isfinite(birth) or birth <= 0)
    ):
        raise OperationBlocked("nextflow_version_probe_unverified")
    return pid, birth


def _recover_version_probe(attempt, stage, result, grace):
    committed = stage.get("nextflow_version_probe")
    retained = result.get("version_probe")
    finished = stage.get("nextflow_version_probe_finished") is True
    if committed is None:
        if finished:
            raise OperationBlocked("nextflow_version_probe_unverified")
        if retained is not None:
            # A receipt can precede its WAL acknowledgement. The gated child
            # cannot exec in this window. Only accept independently observed exit.
            pid, _ = _version_probe_identity(retained)
            if _group_exists(pid):
                raise OperationBlocked("nextflow_version_probe_unverified")
        return
    if committed != retained:
        raise OperationBlocked("nextflow_version_probe_unverified")
    pid, birth = _version_probe_identity(committed)
    if not _group_exists(pid):
        return
    if finished:
        raise OperationBlocked("nextflow_version_probe_unverified")
    with suppress(psutil.NoSuchProcess, ProcessLookupError):
        owner = psutil.Process(pid)
        if owner.create_time() != birth or Path(owner.cwd()).resolve() != attempt:
            raise OperationBlocked("nextflow_version_probe_unverified")
    probe = {"pid": pid, "create_time": birth, "stage_json": canonical(stage).decode()}
    if not _stop_group(probe, grace):
        raise OperationBlocked("nextflow_version_probe_unverified")


def _no_nextflow_launch(attempt, stage, result, grace):
    not_run = (
        result.get("engine_execution") == "not_run"
        and type(result.get("engine_launch_attempt_count")) is int
        and type(result.get("engine_launch_count")) is int
        and result["engine_launch_attempt_count"] == result["engine_launch_count"] == 0
    )
    marker = result.get("daemon_prelaunch")
    expected = stage.get("nextflow_prelaunch_sha256")
    if marker is None and expected is None:
        # Legacy terminal records predate the gated version probe. A missing
        # result or a preparing/intent record cannot prove an interrupted exit.
        return not_run and result.get("status") not in {None, "preparing", "running"}
    if not (
        isinstance(marker, dict)
        and set(marker) == {"format", "attempt_dir", "nonce"}
        and marker["format"] == "devbench.fastq-prelaunch/1"
        and marker["attempt_dir"] == str(attempt)
        and isinstance(marker["nonce"], str)
        and _RUN_ID.fullmatch(marker["nonce"])
        and isinstance(expected, str)
        and hmac.compare_digest(hashlib.sha256(canonical(marker)).hexdigest(), expected)
    ):
        raise OperationBlocked("nextflow_prelaunch_identity_unverified")
    _recover_version_probe(attempt, stage, result, grace)
    if not not_run:
        return False
    # A Docker endpoint pin alone has no compute effect. The native launcher
    # must fsync main intent and commit its WAL fence before releasing its child.
    if (
        stage.get("nextflow_engine_launch_intent")
        or stage.get("nextflow_engine_process")
        or result.get("engine_process_id") is not None
        or result.get("engine_process_create_time") is not None
    ):
        raise OperationBlocked("nextflow_engine_identity_unverified")
    return True


def _recover_nextflow(root, stage, grace):
    from devbench.analysis.fastq_execution import reconcile_fastq_runtime

    attempt = Path(stage["nextflow_attempt"])
    directory = root / "nextflow"
    if (
        attempt.parent != directory
        or attempt.resolve() != attempt
        or not re.fullmatch(r"fastq-[a-f0-9]{32}", attempt.name)
    ):
        raise OperationBlocked("nextflow_attempt_identity_unverified")
    result = json.loads(read_bounded_file(attempt / "result.json", 8 * 1024**2))
    if result.get("format") != "devbench.fastq-attempt/1" or result.get(
        "attempt_dir"
    ) != str(attempt):
        raise OperationBlocked("nextflow_attempt_identity_unverified")
    if _no_nextflow_launch(attempt, stage, result, grace):
        return
    pid, birth = _nextflow_process(result)
    if "nextflow_prelaunch_sha256" in stage and (
        stage.get("nextflow_engine_launch_intent") is not True
        or stage.get("nextflow_engine_process") != {"pid": pid, "create_time": birth}
    ):
        raise OperationBlocked("nextflow_engine_identity_unverified")
    event = {
        "event": "engine_started",
        "attempt_dir": str(attempt),
        "pid": pid,
        "process_create_time": birth,
        "container_label": "devbench.fastq.attempt=" + attempt.name,
    }

    def cleanup(path):
        # An ambiguous retained engine must never authorize Docker mutations.
        if path != attempt or _group_exists(pid):
            raise OperationBlocked("nextflow_engine_cleanup_unverified")
        binding = stage.get("nextflow_docker")
        if not binding:
            raise OperationBlocked("nextflow_docker_custody_unverified")
        return _nextflow_containers(attempt, binding)

    proof = reconcile_fastq_runtime([event], directory, container_cleanup=cleanup)
    verified = proof.get("container_cleanup", {})
    if not (
        proof.get("status") == "reconciled"
        and proof.get("process_stopped") is True
        and verified.get("status") == "complete"
        and verified.get("matched_container_count") == 0
        and verified.get("label") == event["container_label"]
    ):
        raise OperationBlocked("nextflow_cleanup_unverified")


def _needs_recovery(row):
    stage = json.loads(row["stage_json"])
    return (
        row["status"] in _RUNNING
        or row["generation"] == "legacy"
        or stage.get("_worker_terminal_status") == "failed"
        or row["error_text"] is not None
    )


def _recover_containers(workspace, row):
    root = workspace / "runs" / row["run_id"]
    stage = json.loads(row["stage_json"])
    for path, sha, image, attempt in _mounted_bindings(
        root, stage, interrupted=_needs_recovery(row)
    ):
        proof = recover_mounted_container(
            path, request_sha=sha, image_id=image, attempt_id=attempt
        )
        if proof.get("cleanup_confirmed") is not True:
            raise OperationBlocked("mounted_container_cleanup_unverified")


def _recover_tier_a(jobs, row):
    """Settle only retained native attempts whose steps belong to this frozen plan."""
    if not (jobs.workspace / "methods" / "adaptive-tier-a" / "runs").exists():
        return
    from devbench.engine.tier_a_executor import TierAExecutor
    from devbench.platform.adaptive_workflow import AdaptiveWorkflow

    plan = jobs.plan(row["session_id"], row["plan_id"])
    verify_frozen_plan(plan, row["session_id"], row["plan_id"], row["plan_sha256"])
    params = plan.get("design", {}).get("parameters", {})
    lineage = (
        params.get("source_session_id") if plan.get("operation") == "pathways" else None
    )
    ledger = jobs.authority.ledger
    flow = AdaptiveWorkflow(ledger, lineage or row["session_id"])
    executor = TierAExecutor(ledger, mounted_image_id=plan.get("mounted_image_id"))
    prefix = "plugin-step-" + row["plan_id"] + "-"
    for token, envelope, _ in flow._unresolved_dispatches(executor):
        step = envelope["step"]["step_id"]
        if not step.startswith(prefix):
            continue
        with ledger._operation_lock((flow.lineage_id, "adaptive-step", step)):
            # The existing custody verifier checks original daemon/attempt/stage
            # identities and calls ScientificAuthority.reconcile_attempt.
            flow._reconcile_step(step, executor, "tier_a")
        if flow.authority.attempt(token)["cleanup_state"] != "confirmed":
            raise OperationBlocked("mounted_container_cleanup_unverified")


def _positive(value, field, *, zero=False):
    if type(value) is not int or value < (0 if zero else 1):
        raise ValueError(f"Invalid {field}")
    return value


def _seconds(value, field, *, zero=False, maximum=None):
    if (
        type(value) not in (int, float)
        or not math.isfinite(value)
        or value < 0
        or (value == 0 and not zero or maximum is not None and value > maximum)
    ):
        raise ValueError(f"Invalid {field}")
    return value


class Supervisor:
    """Dispatch committed FIFO reservations to isolated, new-session workers."""

    def __init__(
        self,
        workspace,
        *,
        cpu_slots=None,
        gpu_slots=1,
        memory_budget_mb=None,
        max_concurrent_runs=None,
        grace_sec=15,
        deadlines=None,
        heartbeat_sec=10,
        poll_sec=0.15,
        worker_factory=None,
        offline_only=True,
        allow_external_input_files=False,
        policy_sha256=None,
    ):
        self.policy_sha256 = policy_sha256
        self.workspace = directory(Path(workspace), exists=False)
        self.offline_only = offline_only is True
        self.allow_external_input_files = allow_external_input_files is True
        cpu = (
            max(2, min(4, (os.cpu_count() or 1) // 2))
            if cpu_slots is None
            else cpu_slots
        )
        self.slots = {
            "cpu": _positive(cpu, "cpu_slots"),
            "gpu": _positive(gpu_slots, "gpu_slots", zero=True),
        }
        memory = (
            psutil.virtual_memory().total // 1024**2
            if memory_budget_mb is None
            else memory_budget_mb
        )
        self.memory_budget_mb = _positive(memory, "memory_budget_mb")
        total = cpu + gpu_slots if max_concurrent_runs is None else max_concurrent_runs
        self.max_concurrent_runs = _positive(total, "max_concurrent_runs")
        self.deadlines = {**_DEADLINES, **(deadlines or {})}
        if self.deadlines.keys() != _DEADLINES.keys():
            raise ValueError("Unknown deadline profile")
        for seconds in self.deadlines.values():
            _seconds(seconds, "deadline", maximum=_DEADLINES["long"])
        self.grace_sec = _seconds(grace_sec, "grace_sec", zero=True)
        self.heartbeat_sec = _seconds(heartbeat_sec, "heartbeat_sec")
        self.poll_sec = _seconds(poll_sec, "poll_sec")
        self.jobs = JobStore(self.workspace)
        self.authority = self.jobs.authority
        self._spawn = worker_factory or _spawn_worker
        self._wake = threading.Event()
        self._quit = threading.Event()
        self._thread = None
        self._last_pulse = {}
        self._cleanup_lock = threading.Lock()
        self._cleanup_threads = {}
        self._accepting = True
        self._retry_after = {}

    def start(self):
        with _LOCAL_LOCK:
            if self._thread is not None:
                return
            if self.workspace in _LOCAL:
                raise RuntimeError("A supervisor already owns this workspace")
            _LOCAL[self.workspace] = self
        try:
            self._restore_projection()
            self._thread = threading.Thread(
                target=self._serve, name="devbench-scheduler", daemon=True
            )
            self._thread.start()
        except BaseException:
            with _LOCAL_LOCK:
                _LOCAL.pop(self.workspace, None)
            raise

    def _adopt_legacy_runs(self, db):
        # The pre-daemon mutex did not record a memory/pool reservation. Drain
        # these original process generations before granting any new slots.
        for old in self.jobs.unsettled_legacy_runs():
            stage = json.loads(old["stage_json"])
            values = {**old, "created": stage.get("process_created_at")}
            db.execute(
                "INSERT INTO scientific_daemon_runs "
                "(run_id,session_id,plan_id,plan_sha256,profile,pool,memory_mb,"
                "deadline_sec,status,started_at,updated_at,dispatched_at,pid,"
                "create_time,stage_json,generation) VALUES "
                "(:run_id,:session_id,:plan_id,:plan_sha256,'standard','cpu',0,"
                "3600,'running',:started_at,:updated_at,:started_at,:pid,:created,"
                ":stage_json,'legacy') ON CONFLICT(run_id) DO NOTHING",
                values,
            )

    def _restore_projection(self):
        # Requests retain their frozen resource needs across a daemon restart.
        # An operator may have reduced the capacity of a formerly available pool.
        with self.authority.daemon_transaction() as db:
            self._adopt_legacy_runs(db)
            self._restore_policy(db)
            now = time.time()
            failed = db.execute(
                "UPDATE scientific_daemon_runs SET status='failed',finished_at=?,"
                "updated_at=?,cleanup_state='confirmed',error_text='pool_unavailable' "
                "WHERE status='queued' AND (memory_mb>? OR (pool='gpu' AND ?=0)) "
                "RETURNING run_id",
                (now, now, self.memory_budget_mb, self.slots["gpu"]),
            ).fetchall()
            for (rid,) in failed:
                self._seal_terminal(db, rid)
        sequence = 0
        while True:
            rows = self.authority.daemon_read(
                lambda db, after=sequence: db.execute(
                    "SELECT rowid AS sequence,* FROM scientific_daemon_runs "
                    "WHERE rowid>? ORDER BY rowid LIMIT 16",
                    (after,),
                ).fetchall()
            )
            if not rows:
                return
            for row in rows:
                self.jobs.project_run(row)
            sequence = rows[-1]["sequence"]

    def _check_policy(self, row):
        return self.jobs.frozen_policy(
            row["session_id"], row["plan_id"], row["plan_sha256"]
        )

    def _enforce(self, plan, sha, protected, *, db=None):
        from devbench.mcp.approval import active_policy_digest

        if (
            plan.get("allow_external_input_files") is True
            and not self.allow_external_input_files
        ):
            raise OperationBlocked("input_scope_violation")
        policy = active_policy_digest(self.workspace, self.policy_sha256)
        require_execution_policy(
            self.authority,
            plan,
            sha,
            protected,
            offline_only=self.offline_only,
            policy_sha=policy,
            db=db,
        )

    def _seal_terminal(self, db, rid):
        if not db.execute(
            "SELECT 1 FROM sqlite_master WHERE name='mcp_frozen_plans'"
        ).fetchone():
            return
        row = db.execute(
            "SELECT r.* FROM scientific_daemon_runs r JOIN mcp_frozen_plans p "
            "ON p.session_id=r.session_id AND p.plan_id=r.plan_id "
            "AND p.plan_sha256=r.plan_sha256 WHERE r.run_id=? "
            "AND r.status IN ('failed','cancelled') AND r.cleanup_state='confirmed'",
            (rid,),
        ).fetchone()
        if row is not None:
            from devbench.mcp.receipts import seal_terminal

            seal_terminal(self.authority, db, dict(row))

    def _hold(self, db, rid, reason):
        status = _POLICY_HOLDS.get(reason, "failed")
        now = time.time()
        db.execute(
            "UPDATE scientific_daemon_runs SET status=?,error_text=?,updated_at=?,"
            "finished_at=?,cleanup_state='confirmed' WHERE run_id=?",
            (status, reason, now, now if status == "failed" else None, rid),
        )
        self._seal_terminal(db, rid)

    def _resources(self, plan, pool, memory_mb):
        native = plan.get("operation") == "qc-salmon"
        resources = plan.get("resource_override") or {}
        slots = fastq_cpu_slots(plan) if native else resources.get("cpus", 1)
        if pool == "gpu":
            slots = 1
        preferred = 6 * 1024 if native else 1024
        memory = (
            min(preferred, self.memory_budget_mb) if memory_mb is None else memory_mb
        )
        if (
            pool not in self.slots
            or slots > self.slots[pool]
            or (_positive(memory, "memory_mb") > self.memory_budget_mb)
        ):
            raise ValueError("Job resource request exceeds configured pool capacity")
        if native:
            if pool != "cpu" or not 1024 <= memory <= 1024**2:
                raise ValueError(
                    "qc-salmon requires CPU slots and 1–1024 GiB of memory"
                )
            # Nextflow's native contract accepts an integer number of GiB.
            memory = memory // 1024 * 1024
        return slots, memory

    def _restore_fastq_reservation(self, db, row, plan, *, waiting):
        if plan.get("operation") != "qc-salmon" or row["generation"] == "legacy":
            return False
        stage = json.loads(row["stage_json"])
        if waiting:
            try:
                slots, memory = self._resources(plan, row["pool"], row["memory_mb"])
            except ValueError as error:
                raise OperationBlocked("pool_unavailable") from error
        elif "_cpu_slots" in stage and row["pool"] == "cpu":
            return False
        else:
            # An already-running older worker used the standalone 2 CPU/6 GiB profile.
            slots, memory = 2, max(6 * 1024, row["memory_mb"])
        if (
            row["pool"] == "cpu"
            and stage.get("_cpu_slots") == slots
            and memory == row["memory_mb"]
        ):
            return False
        if row["pool"] != "cpu":
            # A running older row may have been charged to a GPU slot while
            # Nextflow actually occupied CPU cores. Preserve its original label.
            stage["_prior_pool"] = row["pool"]
        stage["_cpu_slots"] = slots
        db.execute(
            "UPDATE scientific_daemon_runs SET stage_json=?,memory_mb=?,pool='cpu' "
            "WHERE run_id=?",
            (canonical(stage).decode(), memory, row["run_id"]),
        )
        return True

    def _restore_policy(self, db):
        rows = db.execute(
            "SELECT * FROM scientific_daemon_runs WHERE cleanup_state!='confirmed' "
            "OR status IN ('queued','awaiting_approval','awaiting_offline')"
        )
        for row in rows:
            waiting = row["status"] in {"queued", *_POLICY_HOLDS.values()}
            try:
                with self._check_policy(row) as (plan, protected):
                    self._restore_fastq_reservation(db, row, plan, waiting=waiting)
                    self._enforce(plan, row["plan_sha256"], protected, db=db)
            except OperationBlocked as error:
                if waiting:
                    self._hold(db, row["run_id"], error.state)
                elif not self.offline_only:
                    # No online Workbench/MCP surface may adopt this generation.
                    raise
                elif row["status"] == "running" and _process(row) is not None:
                    db.execute(
                        "UPDATE scientific_daemon_runs SET status='cancelling',"
                        "error_text=?,cancel_requested_at=?,updated_at=? "
                        "WHERE run_id=?",
                        (error.state, time.time(), time.time(), row["run_id"]),
                    )
            else:
                if row["status"] in _POLICY_HOLDS.values():
                    db.execute(
                        "UPDATE scientific_daemon_runs SET status='queued',"
                        "cleanup_state='pending',error_text=NULL,updated_at=? "
                        "WHERE run_id=?",
                        (time.time(), row["run_id"]),
                    )

    def close(self):
        self._quit.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join()
        with self._cleanup_lock:
            pending = list(self._cleanup_threads.values())
        for thread in pending:
            thread.join()
        with _LOCAL_LOCK:
            if _LOCAL.get(self.workspace) is self:
                _LOCAL.pop(self.workspace, None)

    def active_count(self):
        return self.authority.daemon_read(
            lambda db: db.execute(
                "SELECT count(*) FROM scientific_daemon_runs WHERE status='queued' "
                "OR cleanup_state!='confirmed'"
            ).fetchone()[0]
        )

    def stop_if_idle(self):
        """Fence new queue admissions while proving this daemon can stop."""
        with self.authority.daemon_transaction() as db:
            active = db.execute(
                "SELECT 1 FROM scientific_daemon_runs WHERE status='queued' "
                "OR cleanup_state!='confirmed' LIMIT 1"
            ).fetchone()
            if active:
                return False
            self._accepting = False
            return True

    def status(self, run_id):
        row = self.jobs.managed_run(run_id)
        if row is None:
            raise OperationBlocked("unknown_run")
        return self.jobs.visible_run(row)

    def _check_control_runtime(self, plan, sha, resume):
        from devbench.mcp.planning import require_runtime_pins

        if resume is None:
            prior = self.authority.daemon_read(
                lambda db: db.execute(
                    "SELECT * FROM scientific_daemon_runs WHERE session_id=? "
                    "AND plan_id=? AND plan_sha256=? AND (resume_from_run_id IS NULL "
                    "OR run_id=resume_from_run_id) ORDER BY rowid DESC LIMIT 1",
                    (plan["session_id"], plan["plan_id"], sha),
                ).fetchone()
            )
            if prior:
                return self.jobs.visible_run(prior)
        require_runtime_pins(self, plan)
        return None

    def _requested_resources(self, plan, sha, protected, pool, memory_mb):
        if plan.get("format") != "devbench.control-plan/1":
            return pool, memory_mb
        if not protected:
            from devbench.mcp.approval import prepare_auto_grant

            prepare_auto_grant(self.jobs, plan, sha, self.policy_sha256)
        resources = plan.get("resource_override") or {}
        return ("gpu" if resources.get("gpu") else "cpu"), resources.get(
            "memory_mb", memory_mb
        )

    def submit(
        self,
        session_id,
        plan_id,
        plan_sha256,
        *,
        profile=None,
        pool="cpu",
        memory_mb=None,
        resume_from_run_id=None,
    ):
        with self.jobs.frozen_policy(session_id, plan_id, plan_sha256) as policy:
            plan, protected = policy
        pool, memory_mb = self._requested_resources(
            plan, plan_sha256, protected, pool, memory_mb
        )
        self._enforce(plan, plan_sha256, protected)
        if not resume_from_run_id and (
            existing := self.jobs.legacy_run(session_id, plan_id)
        ):
            return existing
        if plan.get("format") == "devbench.control-plan/1":
            if existing := self._check_control_runtime(
                plan, plan_sha256, resume_from_run_id
            ):
                return existing
        if profile is None:
            profile = "qc" if plan.get("operation") == "matrix_audit" else "standard"
        if profile not in self.deadlines:
            raise ValueError("Unknown job deadline profile")
        slots, memory = self._resources(plan, pool, memory_mb)
        fields = {
            "session_id": session_id,
            "plan_id": plan_id,
            "plan_sha256": plan_sha256,
            "profile": profile,
            "pool": pool,
            "memory_mb": memory,
            "deadline_sec": (plan.get("resource_override") or {}).get(
                "timeout_sec", self.deadlines[profile]
            ),
            "resume_from_run_id": resume_from_run_id,
            "stage_json": canonical(
                {"_heartbeat_sec": self.heartbeat_sec, "_cpu_slots": slots}
            ).decode(),
        }
        with self.authority.daemon_transaction() as db:
            if not self._accepting or self._quit.is_set():
                raise OperationBlocked("daemon_stopping")
            prior = db.execute(
                "SELECT * FROM scientific_daemon_runs WHERE plan_id=? "
                "AND (resume_from_run_id IS ? OR "
                "(run_id=resume_from_run_id AND ? IS NULL))",
                (plan_id, resume_from_run_id, resume_from_run_id),
            ).fetchone()
            if prior is not None and not (
                prior["status"] == "interrupted"
                and prior["run_id"] == prior["resume_from_run_id"] == resume_from_run_id
            ):
                return self.jobs.visible_run(prior)
            parent = None
            if resume_from_run_id is not None:
                parent = self._resume_parent(db, resume_from_run_id, fields, plan)
            if plan.get("format") == "devbench.control-plan/1":
                from devbench.engine.dispatch_deadline import check_deadline

                check_deadline()
                self._enforce(plan, plan_sha256, protected, db=db)
            row = self._enqueue_run(db, fields, parent)
        self.jobs.project_run(row)
        self._wake.set()
        return row

    def _resume_parent(self, db, rid, fields, plan):
        parent = db.execute(
            "SELECT * FROM scientific_daemon_runs WHERE run_id=?", (rid,)
        ).fetchone()
        state = verified_checkpoint(self.workspace, parent, fields)["state"]
        if plan.get("format") == "devbench.control-plan/1":
            units = state.get("completed_units")
            if (
                state.get("worker_protocol") != "devbench.control-worker/1"
                or state.get("phase") != "completed_units"
                or state.get("plan_sha256") != fields["plan_sha256"]
                or not isinstance(units, list)
                or not 1 <= len(units) <= 17 * 64
            ):
                raise OperationBlocked("checkpoint_identity_mismatch")
            return None
        if state.get("worker_protocol") == "devbench.plugin-worker/1":
            if state.get("phase") == "native_steps":
                verify_native_resume_plan(plan, state)
                return parent
            if state.get("phase") == "publish_receipt":
                return parent
            if state.get("phase") != "verified_inputs":
                raise OperationBlocked("scientific_step_replay_unverified")
        return None

    @staticmethod
    def _enqueue_run(db, fields, bound_parent):
        # Accepted scientific steps and receipts bind the logical run ID. Each
        # process generation still receives its own newly admitted reservation.
        rid = bound_parent["run_id"] if bound_parent is not None else uuid4().hex
        values = {**fields, "run_id": rid, "now": time.time()}
        if bound_parent is not None:
            db.execute(
                "UPDATE scientific_daemon_runs SET status='queued',"
                "cleanup_state='pending',"
                "resume_from_run_id=run_id,pid=NULL,create_time=NULL,"
                "dispatched_at=NULL,"
                "finished_at=NULL,receipt_json=NULL,error_text=NULL,"
                "cancel_requested_at=NULL,"
                "profile=:profile,pool=:pool,memory_mb=:memory_mb,"
                "deadline_sec=:deadline_sec,"
                "stage_json=:stage_json,updated_at=:now WHERE run_id=:run_id",
                values,
            )
        else:
            db.execute(
                "INSERT INTO scientific_daemon_runs(run_id,session_id,plan_id,"
                "plan_sha256,"
                "profile,pool,memory_mb,deadline_sec,status,started_at,updated_at,"
                "resume_from_run_id,stage_json) VALUES(:run_id,:session_id,:plan_id,"
                ":plan_sha256,:profile,:pool,:memory_mb,:deadline_sec,'queued',:now,:now,"
                ":resume_from_run_id,:stage_json)",
                values,
            )
        return dict(
            db.execute(
                "SELECT * FROM scientific_daemon_runs WHERE run_id=?", (rid,)
            ).fetchone()
        )

    def _serve(self):
        while not self._quit.is_set():
            self._wake.clear()
            try:
                self._tick()
            except (
                OSError,
                ValueError,
                RuntimeError,
                sqlite3.Error,
                psutil.Error,
            ):
                # A transient database/OS failure leaves the durable queue intact.
                self._wake.wait(max(0.5, self.poll_sec))
            self._wake.wait(self.poll_sec)

    def _tick(self):
        rows = self.authority.daemon_read(
            lambda db: db.execute(
                "SELECT * FROM scientific_daemon_runs WHERE status!='queued' "
                "AND cleanup_state!='confirmed'"
            ).fetchall()
        )
        for row in rows:
            rid = row["run_id"]
            if time.monotonic() < self._retry_after.get(rid, 0):
                continue
            try:
                self._monitor(dict(row))
            except (
                KeyError,
                TypeError,
                ValueError,
                OSError,
                RuntimeError,
                psutil.Error,
            ):
                self._retry_after[rid] = time.monotonic() + 5
        while not self._quit.is_set() and self._dispatch():
            pass

    def _candidate(self, db):
        active = db.execute(
            "SELECT pool,memory_mb,generation,stage_json FROM scientific_daemon_runs "
            "WHERE status!='queued' AND cleanup_state!='confirmed'"
        ).fetchall()
        if len(active) >= self.max_concurrent_runs or any(
            row["generation"] == "legacy" for row in active
        ):
            return None
        usage = Counter()
        for row in active:
            usage[row["pool"]] += json.loads(row["stage_json"]).get("_cpu_slots", 1)
        remaining = self.memory_budget_mb - sum(row["memory_mb"] for row in active)
        # The oldest pending run in each pool reserves its place while waiting for
        # memory. An independent pool may run, but cannot indefinitely starve it.
        rows = db.execute(
            "SELECT * FROM scientific_daemon_runs WHERE status='queued' ORDER BY rowid"
        )
        blocked = set()
        for row in rows:
            pool = row["pool"]
            slots = json.loads(row["stage_json"]).get("_cpu_slots", 1)
            if slots > self.slots[pool] - usage[pool]:
                blocked.add(pool)
            if pool in blocked:
                continue
            if row["memory_mb"] > remaining:
                return None
            return dict(row)
        return None

    def _released_approvals(self, db):
        rows = db.execute(
            "SELECT * FROM scientific_daemon_runs AS run "
            "WHERE status='awaiting_approval' "
            "AND EXISTS (SELECT 1 FROM scientific_plan_approvals AS grant_record "
            "WHERE grant_record.session_id=run.session_id "
            "AND grant_record.plan_id=run.plan_id "
            "AND grant_record.plan_sha256=run.plan_sha256)"
        ).fetchall()
        for row in rows:
            try:
                with self._check_policy(row) as (plan, protected):
                    self._enforce(plan, row["plan_sha256"], protected, db=db)
            except OperationBlocked as error:
                if error.state != row["error_text"]:
                    self._hold(db, row["run_id"], error.state)
            else:
                db.execute(
                    "UPDATE scientific_daemon_runs SET status='queued',"
                    "cleanup_state='pending',error_text=NULL,updated_at=? "
                    "WHERE run_id=?",
                    (time.time(), row["run_id"]),
                )

    def _dispatch(self):
        with self.authority.daemon_transaction() as db:
            if not self._accepting:
                return False
            self._released_approvals(db)
            row = self._candidate(db)
            if row is None:
                return False
            try:
                # Keep the frozen plan and the sticky input classifications fenced
                # against concurrent custody writes through the actual process fork.
                with self._check_policy(row) as (plan, protected):
                    self._enforce(plan, row["plan_sha256"], protected, db=db)
                    if not self._restore_fastq_reservation(db, row, plan, waiting=True):
                        self._spawn_reserved(db, row)
            except OperationBlocked as error:
                self._hold(db, row["run_id"], error.state)
        self.jobs.project_run(self.status(row["run_id"]))
        return True

    def _spawn_reserved(self, db, row):
        rid, now = row["run_id"], time.time()
        try:
            process = self._spawn(self.workspace, rid)
        except (OSError, ValueError, RuntimeError):
            db.execute(
                "UPDATE scientific_daemon_runs SET status='failed',"
                "error_text='worker_launch_failed',"
                "finished_at=?,updated_at=?,cleanup_state='confirmed' WHERE run_id=?",
                (now, now, rid),
            )
            self._seal_terminal(db, rid)
            return
        try:
            identity = psutil.Process(process.pid)
            created = identity.create_time()
            if process.pid != os.getpgid(process.pid) or process.pid != os.getsid(
                process.pid
            ):
                raise ValueError("Worker must own its process group and session")
        except (psutil.Error, ProcessLookupError, ValueError):
            cleanup = self._reject_unbound_child(process)
            db.execute(
                "UPDATE scientific_daemon_runs SET status='failed',"
                "error_text='unbound_worker_launch',finished_at=?,updated_at=?,"
                "pid=?,cleanup_state=? WHERE run_id=?",
                (now, now, process.pid, cleanup, rid),
            )
            self._seal_terminal(db, rid)
            return
        finally:
            # Capture the child's birth before a reaper can release its PID.
            threading.Thread(
                target=process.wait, name="devbench-reap", daemon=True
            ).start()
        stage = json.loads(row["stage_json"])
        stage["_offline_only"] = self.offline_only
        stage["_allow_external_input_files"] = self.allow_external_input_files
        stage["_approval_policy_sha256"] = self.policy_sha256
        db.execute(
            "UPDATE scientific_daemon_runs SET status='running',pid=?,create_time=?,"
            "dispatched_at=?,updated_at=?,generation=?,stage_json=? WHERE run_id=?",
            (
                process.pid,
                created,
                now,
                now,
                uuid4().hex,
                canonical(stage).decode(),
                rid,
            ),
        )

    @staticmethod
    def _reject_unbound_child(process):
        # Popen owns this child, even when the adapter did not establish the
        # requested PGID. Never signal an unverified process group.
        with suppress(ProcessLookupError):
            process.terminate()
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            with suppress(ProcessLookupError):
                process.kill()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                return "unconfirmed"
        return "unconfirmed" if _group_exists(process.pid) else "confirmed"

    def _sync_legacy(self, row):
        """Read the last stage/receipt from the original writer before cleanup."""
        old = self.jobs.legacy_run(row["session_id"], row["plan_id"])
        if old is None or any(
            old[key] != row[key] for key in ("run_id", "plan_sha256", "pid")
        ):
            raise OperationBlocked("legacy_job_identity_unverified")
        stage = json.loads(old["stage_json"])
        if stage.get("process_created_at") != row["create_time"]:
            raise OperationBlocked("legacy_job_identity_unverified")
        # Earlier job versions never authenticated these control-plane fields.
        stage = {key: value for key, value in stage.items() if not key.startswith("_")}
        with self.authority.daemon_transaction() as db:
            current = dict(
                db.execute(
                    "SELECT * FROM scientific_daemon_runs WHERE run_id=?",
                    (row["run_id"],),
                ).fetchone()
            )
            if current["cleanup_state"] == "confirmed" or (
                current["status"] == "finishing"
                and "_worker_terminal_status" in json.loads(current["stage_json"])
            ):
                return current
            if current["status"] != "cancelling" and old["status"] in {
                "completed",
                "failed",
                "awaiting_review",
            }:
                stage["_worker_terminal_status"] = old["status"]
                current.update(
                    status="finishing",
                    receipt_json=old["receipt_json"],
                    error_text=old["error_text"],
                    finished_at=old["finished_at"],
                )
            current["stage_json"] = canonical(stage).decode()
            db.execute(
                "UPDATE scientific_daemon_runs SET stage_json=:stage_json,"
                "status=:status,error_text=:error_text,receipt_json=:receipt_json,"
                "finished_at=:finished_at WHERE run_id=:run_id",
                current,
            )
        return current

    def _monitor(self, row):
        rid = row["run_id"]
        if row["status"] == "cancelling" or _process(row) is None:
            self._schedule_cleanup(rid)
            return
        if row["generation"] == "legacy" or row["status"] != "running":
            return
        if time.time() - row["dispatched_at"] > row["deadline_sec"]:
            self._request_cancel(rid, "plugin_worker_deadline_exceeded")
            self._schedule_cleanup(rid)
        elif (
            time.monotonic() - self._last_pulse.get(rid, -math.inf)
            >= self.heartbeat_sec
        ):
            self._sample_heartbeat(rid)

    def _sample_heartbeat(self, rid):
        with self.authority.daemon_transaction() as db:
            fresh = db.execute(
                "SELECT * FROM scientific_daemon_runs WHERE run_id=?", (rid,)
            ).fetchone()
            if fresh["status"] != "running" or not self._still_admitted(db, fresh):
                return
            stage = json.loads(fresh["stage_json"])
            observe_children(fresh, stage)
            updated = canonical(stage).decode()
            if updated != fresh["stage_json"]:
                db.execute(
                    "UPDATE scientific_daemon_runs SET stage_json=? WHERE run_id=?",
                    (updated, rid),
                )
            write_progress(self.workspace, fresh)
        self._last_pulse[rid] = time.monotonic()

    def _still_admitted(self, db, row):
        try:
            with self._check_policy(row) as (plan, protected):
                self._enforce(plan, row["plan_sha256"], protected, db=db)
        except OperationBlocked as error:
            now = time.time()
            db.execute(
                "UPDATE scientific_daemon_runs SET status='cancelling',"
                "cancel_requested_at=?,error_text=?,updated_at=? WHERE run_id=?",
                (now, error.state, now, row["run_id"]),
            )
            return False
        return True

    def _request_cancel(self, rid, reason):
        with self.authority.daemon_transaction() as db:
            row = db.execute(
                "SELECT * FROM scientific_daemon_runs WHERE run_id=?", (rid,)
            ).fetchone()
            if row is None:
                raise OperationBlocked("unknown_run")
            if row["status"] in _POLICY_HOLDS.values():
                now = time.time()
                db.execute(
                    "UPDATE scientific_daemon_runs SET status='cancelled',"
                    "cancel_requested_at=?,finished_at=?,updated_at=?,error_text=? "
                    "WHERE run_id=?",
                    (now, now, now, reason, rid),
                )
                self._seal_terminal(db, rid)
                return
            if row["status"] not in {"queued", "running"}:
                return
            now = time.time()
            db.execute(
                "UPDATE scientific_daemon_runs SET status='cancelling',"
                "cancel_requested_at=?,"
                "error_text=?, updated_at=? WHERE run_id=?",
                (now, reason, now, rid),
            )

    def cancel_run(self, run_id, *, wait=True):
        self._request_cancel(run_id, "cancel_requested")
        cleanup = self._schedule_cleanup(run_id)
        if wait:
            cleanup.join()
        row = self.status(run_id)
        self.jobs.project_run(row)
        self._wake.set()
        return row

    def _schedule_cleanup(self, rid):
        def run():
            try:
                self._settle(rid)
            finally:
                with self._cleanup_lock:
                    self._cleanup_threads.pop(rid, None)
                self._wake.set()

        with self._cleanup_lock:
            if rid not in self._cleanup_threads:
                thread = threading.Thread(
                    target=run, name="devbench-cleanup", daemon=True
                )
                self._cleanup_threads[rid] = thread
                thread.start()
            return self._cleanup_threads[rid]

    def _stop_workers(self, row):
        if not _stop_group(row, self.grace_sec):
            raise OperationBlocked("worker_group_cleanup_unverified")
        stage = json.loads(row["stage_json"])
        if stage.get("nextflow_attempt"):
            _recover_nextflow(
                self.workspace / "runs" / row["run_id"], stage, self.grace_sec
            )
        if not _stop_detached_children(row, self.grace_sec):
            raise OperationBlocked("worker_group_cleanup_unverified")

    def _settle(self, rid):
        row = self.jobs.managed_run(rid)
        if row["cleanup_state"] == "confirmed":
            return
        if row["status"] not in _RUNNING and _process(row) is not None:
            return
        if row["status"] == "running" and _process(row) is not None:
            return
        try:
            if row["generation"] == "legacy":
                row = self._sync_legacy(row)
                pid = row["pid"]
                if pid is not None and (
                    pid <= 1
                    or (
                        _process(row) is None
                        and psutil.pid_exists(pid)
                        and psutil.Process(pid).status() != psutil.STATUS_ZOMBIE
                    )
                ):
                    raise OperationBlocked("legacy_worker_identity_unverified")
            self._stop_workers(row)
            _recover_containers(self.workspace, row)
            if row["dispatched_at"] is not None:
                # A worker's terminal report cannot settle an independently
                # pending Tier A attempt or mounted cleanup record.
                _recover_tier_a(self.jobs, row)
            self._finish_cleanup(row)
            self._retry_after.pop(rid, None)
        except (
            OSError,
            ValueError,
            KeyError,
            TypeError,
            RuntimeError,
            psutil.Error,
        ):
            self._retry_after[rid] = time.monotonic() + 5
            with self.authority.daemon_transaction() as db:
                db.execute(
                    "UPDATE scientific_daemon_runs SET cleanup_state='unconfirmed' "
                    "WHERE run_id=? AND cleanup_state!='confirmed'",
                    (rid,),
                )
        finally:
            self._last_pulse.pop(rid, None)

    def _finish_cleanup(self, row):
        with self.authority.daemon_transaction() as db:
            current = dict(
                db.execute(
                    "SELECT * FROM scientific_daemon_runs WHERE run_id=?",
                    (row["run_id"],),
                ).fetchone()
            )
            status, error = current["status"], current["error_text"]
            if status == "finishing":
                status = json.loads(current["stage_json"])["_worker_terminal_status"]
            elif status == "running":
                status, error = "interrupted", "worker_not_running"
            elif status == "cancelling":
                status = (
                    "failed"
                    if error == "plugin_worker_deadline_exceeded"
                    else "cancelled"
                )
            now = time.time()
            finished = current["finished_at"] or now
            db.execute(
                "UPDATE scientific_daemon_runs SET status=?,error_text=?,"
                "cleanup_state='confirmed',"
                "finished_at=?,updated_at=? WHERE run_id=?",
                (status, error, finished, now, row["run_id"]),
            )
            current.update(
                status=status,
                error_text=error,
                cleanup_state="confirmed",
                finished_at=finished,
                updated_at=now,
            )
            self._seal_terminal(db, row["run_id"])
            write_progress(self.workspace, current)
        self.jobs.project_run(current)
