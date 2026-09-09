"""Reconcile an operated native attempt without retrying its numerical work.

The local registry, daemon and coordinator remain trusted infrastructure. A
recovery records a new observation and its own receipt; it never repairs or
finishes the original execution DAG. A local lease is coordination, not process
authentication, and is never authority to signal a host PID.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import stat

from devharness_core.provenance import ExecutionDAG, verify_execution
from .contracts import canonical_json_bytes, parse_json_bytes
from .executor import _save
from .native_contracts import parse_native_plan_bytes
from .native_runtime import (
    LocalDocker, NATIVE_IMAGE_ID, SUPPORTED_PROFILES, check_container_record,
    container_profile, create_arguments, local_endpoint,
)
from .operated_native import operation_binding, verify_saved_native_attempt
from .run_registry import RunRegistry, _path
from .trust import TrustedSigner
from .verification import _time

MAX_SNAPSHOT_BYTES = 256 * 1024 * 1024
MAX_SNAPSHOT_FILES = 4096
MAX_SNAPSHOT_ENTRIES = 8192
MAX_FILE_BYTES = 64 * 1024 * 1024
_TERMINAL = {"completed", "failed", "interrupted"}
_ROOT_FILES = {"operation.json", "plan.json", "approvals.json", "trust-registry.json",
               "environment.json", "native-transport.json", "outcome.json", "receipt.json",
               "controlled-verification.json", "authority-snapshot.json",
               "authority-administrators.json", "authority-checkpoint.json"}
_ROOT_DIRS = {"audit", "inputs", "native-output", "native-logs", "native-client"}
_LIFECYCLE = {"runtime_intent": "intent", "container_observed": "created",
              "launch_authorized": "start_authorized", "start_requested": "start_requested"}
_LIMITATIONS = [
    "Local filesystem leases coordinate cooperating processes; no PID identity or host attestation is established.",
    "No numerical retry or new analysis authorization occurs during recovery.",
    "Original incomplete evidence remains incomplete; a recovery receipt does not replace an execution receipt.",
    "Registry checkpoints and recovery roots require independent retention; local storage is not WORM.",
    "Actor labels are caller assertions, not authenticated human approvals or electronic signatures.",
]


def _require(value, reason):
    if not value:
        raise ValueError(reason)


def _sha(value):
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _file(path, limit=1024 * 1024):
    """Bounded regular-file read, including a nonblocking open against FIFO races."""
    path = _path(path)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        before = os.fstat(descriptor)
        _require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and before.st_size <= limit,
                 "Recovery requires a bounded regular file without hard links")
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            raw = handle.read(limit + 1)
        after = os.fstat(descriptor)
        _require(_signature(before) == _signature(after) and len(raw) <= limit,
                 "Recovery source changed during bounded read")
        _require(_signature(path.lstat()) == _signature(before), "Recovery source path was replaced")
        return raw
    finally:
        os.close(descriptor)


def _signature(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_nlink, info.st_size,
            info.st_mtime_ns, info.st_ctime_ns)


def _binding(attempt, registry_id):
    return operation_binding(attempt, registry_id)


def _owned_workspace(workspace, binding):
    marker = parse_json_bytes(_file(workspace / "operation.json", 65536))
    _require(marker == binding, "Original operation binding does not match the registered attempt")
    plan = parse_native_plan_bytes(_file(workspace / "plan.json"))
    _require(plan.sha256() == binding["plan_sha256"], "Original plan does not match the registered attempt")
    return plan


def _runtime_contract(attempt, workspace, plan):
    identity = parse_json_bytes(_file(workspace / "environment.json"))
    environment = identity["environment"]
    _require(identity["environment_sha256"] == plan.environment_sha256 == _sha(environment),
             "Recorded native environment is not bound to the signed plan")
    _require(identity["implementation_sha256"] == plan.implementation_sha256
             and _sha(identity["source"]) == plan.implementation_sha256,
             "Recorded coordinator source identity does not match the signed plan")
    _require(identity["runtime_image_id"] == plan.runtime_image_id == NATIVE_IMAGE_ID
             and identity["platform"] == plan.platform == "linux/amd64",
             "Recorded native runtime is not the approved immutable image")
    for field in ("driver_sha256", "package_lock_sha256"):
        _require(identity[field] == getattr(plan, field), "Recorded native identity mismatch: " + field)
    profile_version = environment["profile_version"]
    _require(profile_version in SUPPORTED_PROFILES, "Unsupported recorded native profile")
    profile = container_profile(plan, workspace / "inputs", workspace / "native-output",
                                uid=environment["uid"], gid=environment["gid"])
    profile["schema_version"] = profile_version
    docker = environment["docker"]
    endpoint = local_endpoint(docker["endpoint"])
    if profile_version != "native_container_profile/1":
        _require(type(docker.get("daemon_id")) is str and 0 < len(docker["daemon_id"]) <= 256,
                 "Recorded daemon identity is missing")
    name, entries, cid = "devharness-native-" + attempt["attempt_id"], {}, None
    lifecycle = dict(_LIFECYCLE)
    if profile_version == "native_container_profile/1":
        # Historical profile1 recorded this registry stage after Popen. New
        # profile2 persists start intent before Popen; attach is diagnostic only.
        lifecycle["start_requested"] = "attach_started"
    prior_time = _time(attempt["created_at"])
    for event in attempt["events"]:
        if event["kind"] not in _LIFECYCLE:
            if event["kind"] not in _TERMINAL:
                prior_time = _time(event["recorded_at"])
            continue
        entry = event["payload"]
        _require(type(entry) is dict and set(entry) == {"schema_version", "event", "recorded_at",
                 "plan_sha256", "container_name", "payload"}, "Malformed recorded runtime checkpoint")
        _require(entry["schema_version"] == "native_lifecycle/1"
                 and entry["event"] == lifecycle[event["kind"]]
                 and entry["plan_sha256"] == plan.sha256() and entry["container_name"] == name,
                 "Recorded runtime checkpoint does not bind the registered attempt")
        when = _time(entry["recorded_at"])
        _require(prior_time <= when <= _time(event["recorded_at"]),
                 "Recorded runtime checkpoint chronology is invalid")
        prior_time = _time(event["recorded_at"])
        entries[event["kind"]] = entry["payload"]
        if event["kind"] != "runtime_intent":
            candidate = entry["payload"].get("container_id")
            _require(type(candidate) is str and re.fullmatch(r"[0-9a-f]{64}", candidate),
                     "Recorded immutable container ID is malformed")
            _require(cid is None or candidate == cid, "Recorded container identity changes across checkpoints")
            cid = candidate
    intent = entries["runtime_intent"]
    _require(set(intent) == {"image_id", "profile", "command"}
             and intent["image_id"] == NATIVE_IMAGE_ID and intent["profile"] == profile,
             "Runtime intent profile differs from signed limits or registered workspace")
    command = intent["command"]
    _require(type(command) is list and all(type(x) is str for x in command) and len(command) > 5,
             "Malformed recorded runtime command")
    _require(Path(command[0]).is_absolute() and command[1:5] ==
             ["--config", str(workspace / "native-client"), "--host", endpoint]
             and command[5:] == create_arguments(profile, plan.sha256(), name),
             "Recorded runtime command differs from the fixed local invocation")
    return {"profile": profile, "docker": docker, "endpoint": endpoint, "name": name,
            "container_id": cid, "recorded_checkpoints": entries}


def _ids(runtime, filter_text):
    text = runtime._call(["container", "ls", "--all", "--no-trunc", "--filter", filter_text,
                          "--format", "{{.ID}}"])
    _require(type(text) is str and len(text) <= 1024, "Unbounded container discovery response")
    values = text.splitlines() if text else []
    _require(len(values) <= 1 and all(re.fullmatch(r"[0-9a-f]{64}", v) for v in values),
             "Ambiguous or malformed immutable container discovery")
    return values


def _record_summary(record):
    # A mismatched container can carry unrelated configuration. Keep a hash of
    # that observation, not arbitrary mount paths or environment values.
    return {"record_sha256": _sha(record), "container_id": record.get("Id"),
            "name": record.get("Name"), "image_id": record.get("Image")}


def _cleanup(contract, plan, root, report, checkpoint):
    runtime = LocalDocker(endpoint=contract["endpoint"])
    runtime.isolate_client(root / "recovery-client")
    identity = runtime.identity()
    report["daemon"] = identity
    _require(identity["endpoint"] == contract["endpoint"], "Observed Docker endpoint differs from recorded endpoint")
    expected_daemon = contract["docker"].get("daemon_id")
    if expected_daemon is not None:
        _require(identity.get("daemon_id") == expected_daemon, "Recorded local Docker daemon was replaced")
    else:
        report["limitations"].append("Historical profile1 omitted daemon identity; ownership uses the full inspected container contract.")
    checkpoint("recovery_daemon_observed", {"identity": identity, "recorded_endpoint": contract["endpoint"]})
    name, cid = contract["name"], contract["container_id"]
    named = _ids(runtime, "name=^/" + name + "$")
    report["discovered_container_ids"] = named
    checkpoint("recovery_container_discovered", {"container_name": name, "container_ids": named,
                                               "recorded_container_id": cid})
    if cid is not None:
        observed = _ids(runtime, "id=" + cid)
        _require(not observed or observed == [cid], "Daemon returned a different immutable container ID")
        _require(not named or named == [cid], "Recorded container name now belongs to a different immutable ID")
        if not observed:
            _require(not named, "Container discovery is inconsistent")
            report["cleanup"] = {"status": "recorded_container_absent", "container_id": cid,
                                 "mutation_performed": False}
            checkpoint("recovery_container_absent", report["cleanup"])
            return True
    elif not named:
        report["cleanup"] = {"status": "unknown_create_may_be_in_flight", "mutation_performed": False}
        return False
    else:
        cid = named[0]
    record = runtime.inspect(cid)
    report["container_observation"] = _record_summary(record)
    checkpoint("recovery_container_observed", report["container_observation"])
    _require(record.get("Id") == cid, "Inspected immutable container ID differs from discovery")
    check_container_record(record, contract["profile"], plan.sha256(), name)
    report["ownership_verified"] = True
    report["container_observation"] = record
    checkpoint("recovery_container_ownership_verified", {"container_id": cid, "container_name": name,
        "plan_sha256": plan.sha256(), "profile_sha256": _sha(contract["profile"]), "record_sha256": _sha(record)})
    state = record["State"]
    _require(type(state.get("Running")) is bool, "Container running state is unavailable")
    actions = report["cleanup"] = {"status": "owned_container_observed", "container_id": cid,
                                  "mutation_performed": False, "actions": []}
    if state["Running"]:
        actions["mutation_performed"] = True
        actions["actions"].append({"action": "kill", "container_id": cid, "status": "requested"})
        checkpoint("recovery_cleanup_requested", actions["actions"][-1])
        try:
            runtime._call(["container", "kill", "--signal", "KILL", cid])
            actions["actions"][-1]["status"] = "command_returned"
        except (OSError, ValueError, RuntimeError) as error:
            # It can exit between inspect and kill. Reinspect its exact ID and
            # entire contract before deciding whether removal is still safe.
            actions["actions"][-1].update(status="command_failed", error_type=type(error).__name__)
        checkpoint("recovery_cleanup_returned", actions["actions"][-1])
    record = runtime.inspect(cid)
    _require(record.get("Id") == cid, "Container identity changed before removal")
    check_container_record(record, contract["profile"], plan.sha256(), name)
    _require(record["State"].get("Running") is False, "Owned container is still running; removal refused")
    report["container_after_stop"] = record
    checkpoint("recovery_container_stopped", {"container_id": cid, "record_sha256": _sha(record)})
    actions["mutation_performed"] = True
    actions["actions"].append({"action": "remove", "container_id": cid, "status": "requested"})
    checkpoint("recovery_cleanup_requested", actions["actions"][-1])
    response = runtime._call(["container", "rm", cid])
    _require(response == cid, "Container removal was not confirmed by immutable ID")
    actions["actions"][-1]["status"] = "command_returned"
    checkpoint("recovery_cleanup_returned", actions["actions"][-1])
    _require(not _ids(runtime, "id=" + cid), "Removed container remains present")
    actions["status"] = "owned_container_removed"
    return True


def _snapshot_original(workspace, destination):
    """Stream a bounded snapshot using directory-relative, no-follow descriptors.

    Unknown root entries are refused before traversal. Special files, hard links,
    symlinks and changing directories fail closed. This is only called after the
    operation marker is bound and the recorded container is stopped/absent, or
    before the runtime intent existed.
    """
    destination.mkdir(mode=0o700)
    entries, files, total = [], 0, 0
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
    root_fd = os.open(workspace, flags | os.O_DIRECTORY)

    def visit(fd, relative, target):
        nonlocal files, total
        before = _signature(os.fstat(fd))
        children = []
        with os.scandir(fd) as iterator:
            for child in iterator:
                _require(len(entries) + len(children) < MAX_SNAPSHOT_ENTRIES, "Original inventory exceeds entry bound")
                info = child.stat(follow_symlinks=False)
                _require(stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode),
                         "Original inventory contains a symlink or special file")
                _require(not any(ord(c) < 32 or ord(c) == 127 for c in child.name), "Unsafe original artifact name")
                if not relative:
                    admitted = child.name in (_ROOT_DIRS if stat.S_ISDIR(info.st_mode) else _ROOT_FILES)
                    _require(admitted, "Unrecognized original workspace entry; inventory refused")
                children.append((child.name, info))
        for name, info in sorted(children):
            _require(len(entries) < MAX_SNAPSHOT_ENTRIES, "Original inventory exceeds entry bound")
            member = (relative / name).as_posix() if relative else name
            if stat.S_ISDIR(info.st_mode):
                child_fd = os.open(name, flags | os.O_DIRECTORY, dir_fd=fd)
                try:
                    _require(_signature(os.fstat(child_fd)) == _signature(info), "Original directory changed before opening")
                    (target / name).mkdir(mode=0o700)
                    entries.append({"path": member, "kind": "directory"})
                    visit(child_fd, Path(member), target / name)
                finally:
                    os.close(child_fd)
            else:
                files += 1
                _require(files <= MAX_SNAPSHOT_FILES and info.st_size <= MAX_FILE_BYTES
                         and total + info.st_size <= MAX_SNAPSHOT_BYTES and info.st_nlink == 1,
                         "Original inventory exceeds file/byte bounds or contains a hard link")
                child_fd = os.open(name, flags, dir_fd=fd)
                try:
                    _require(_signature(os.fstat(child_fd)) == _signature(info)
                             and stat.S_ISREG(os.fstat(child_fd).st_mode), "Original file changed before opening")
                    digest, count = hashlib.sha256(), 0
                    with os.fdopen(child_fd, "rb", closefd=False) as source, (target / name).open("xb") as sink:
                        (target / name).chmod(0o600)
                        while chunk := source.read(1024 * 1024):
                            count += len(chunk)
                            _require(count <= info.st_size and total + count <= MAX_SNAPSHOT_BYTES,
                                     "Original file grew beyond snapshot bounds")
                            sink.write(chunk); digest.update(chunk)
                        sink.flush(); os.fsync(sink.fileno())
                    _require(count == info.st_size and _signature(os.fstat(child_fd)) == _signature(info)
                             and _signature(os.stat(name, dir_fd=fd, follow_symlinks=False)) == _signature(info),
                             "Original file changed during snapshot")
                    total += count
                    entries.append({"path": member, "kind": "file", "sha256": digest.hexdigest(), "size_bytes": count})
                finally:
                    os.close(child_fd)
        _require(_signature(os.fstat(fd)) == before, "Original directory changed during snapshot")

    try:
        root_identity = _signature(os.fstat(root_fd))
        visit(root_fd, None, destination)
        _require(_signature(workspace.lstat()) == root_identity, "Original workspace was replaced during snapshot")
    finally:
        os.close(root_fd)
    return {"schema_version": "native_recovery_inventory/1", "file_count": files,
            "total_bytes": total, "entries": entries, "inventory_sha256": _sha(entries)}


def _sealed_original(workspace, plan, binding, trusted_signers, *, authority_administrators=None):
    # Preflight the fixed paths with nonblocking regular-file reads before the
    # shared read-only verifier checks original CAS and registration bindings.
    receipt = parse_json_bytes(_file(workspace / "receipt.json"))
    manifest_path = workspace / "audit/manifest.json"
    manifest_raw = _file(manifest_path, 16 * 1024 * 1024)
    saved = verify_saved_native_attempt({**binding, "workspace": str(workspace)}, binding["registry_id"], trusted_signers,
        **({"authority_administrators":authority_administrators} if authority_administrators is not None else {}))
    _require(saved["root_hash"] == receipt["root_hash"] and plan.sha256() == binding["plan_sha256"],
             "Original execution binding changed during verification")
    return {"receipt": receipt, "verification": saved["verification"],
            "manifest_sha256": hashlib.sha256(manifest_raw).hexdigest()}


def recover_native_attempt(registry_dir, attempt_id, trusted_signers, *, actor_id,
                           output_dir, expected_checkpoint=None, authority_administrators=None):
    """Observe and safely reconcile one operated attempt under its local lease.

    Unresolved ownership, an unavailable daemon, in-flight creation ambiguity or
    incomplete preservation leaves a nonterminal registry record unchanged. Only
    an already sealed, independently verified execution can restore a lost
    ``completed`` commit. Every successful call creates a separate recovery DAG.
    """
    registry_path, root = _path(registry_dir), _path(output_dir)
    _require(registry_path.is_dir() and (registry_path / "runs.sqlite3").is_file(), "An existing registry is required")
    _require(type(actor_id) is str and 0 < len(actor_id) <= 200 and actor_id.strip()
             and not any(ord(c) < 32 or ord(c) == 127 for c in actor_id), "Invalid recovery actor label")
    _require(isinstance(trusted_signers, (list, tuple, dict)) and trusted_signers, "Explicit public signer registry required")
    for signer in trusted_signers.values() if isinstance(trusted_signers, dict) else trusted_signers:
        TrustedSigner.model_validate(signer)
    registry = RunRegistry(registry_path)
    registry.verify_integrity(expected_checkpoint)
    with registry.lease(attempt_id):
        registry_check = registry.verify_integrity(expected_checkpoint)
        attempt = registry.get(attempt_id)
        workspace = _path(attempt["workspace"])
        for protected in (workspace, registry_path):
            _require(not root.is_relative_to(protected) and not protected.is_relative_to(root),
                     "Recovery output must not overlap the original workspace or registry")
        root.mkdir(mode=0o700, parents=True, exist_ok=False)
        root.chmod(0o700)
        dag = ExecutionDAG(root / "audit", actor_id=actor_id)
        checkpoint = registry.export_checkpoint()
        for name, value in (("registry-before.json", checkpoint), ("attempt-before.json", attempt)):
            _save(root / name, value); dag.add_artifact(root / name)
        if expected_checkpoint is not None:
            _save(root / "external-checkpoint.json", expected_checkpoint); dag.add_artifact(root / "external-checkpoint.json")
        previous = dag.append("recovery_started", {"registry_id": registry.registry_id,
            "attempt_id": attempt_id, "plan_sha256": attempt["plan_sha256"], "original_workspace": str(workspace),
            "original_event_root_hash": attempt["event_root_hash"], "registry_check": registry_check})
        report = {"schema_version": "operated_native_recovery/1", "registry_id": registry.registry_id,
                  "attempt_id": attempt_id, "plan_sha256": attempt["plan_sha256"], "status": "unresolved",
                  "registry_status_before": attempt["status"], "proposed_terminal_status": None,
                  "original_execution_root_hash": None, "analysis_completed": False,
                  "clinical_release_allowed": False, "compliance_certified": False,
                  "ownership_verified": False, "cleanup": {"status": "not_observed", "mutation_performed": False},
                  "limitations": list(_LIMITATIONS), "errors": []}
        terminal = None

        def recovery_checkpoint(kind, payload):
            nonlocal previous
            previous = dag.append(kind, payload, parents=[previous])

        try:
            binding = _binding(attempt, registry.registry_id)
            runtime_intent = any(e["kind"] == "runtime_intent" for e in attempt["events"])
            marker_exists = (workspace / "operation.json").exists() or (workspace / "operation.json").is_symlink()
            plan_exists = (workspace / "plan.json").exists() or (workspace / "plan.json").is_symlink()
            receipt_exists = (workspace / "receipt.json").exists() or (workspace / "receipt.json").is_symlink()
            _require(attempt["status"] != "completed" or (marker_exists and plan_exists and receipt_exists),
                     "Completed registry requires the original bound plan, operation marker and saved receipt")
            if not marker_exists or (not runtime_intent and not plan_exists):
                _require(not runtime_intent, "Runtime intent exists without a matching original operation marker")
                _require(not receipt_exists, "Saved receipt exists without a verifiable original plan/operation pair")
                if marker_exists:
                    marker_raw = _file(workspace / "operation.json", 65536)
                    _require(parse_json_bytes(marker_raw) == binding,
                             "Original operation binding does not match the registered attempt")
                    marker_copy = root / "original-operation.json"
                    with marker_copy.open("xb") as handle:
                        marker_copy.chmod(0o600)
                        handle.write(marker_raw); handle.flush(); os.fsync(handle.fileno())
                    report["original_operation_marker"] = dag.add_artifact(marker_copy)
                report["cleanup"]["status"] = "no_registered_runtime_intent"
                report["pre_runtime_observation"] = {"operation_binding_verified": marker_exists,
                    "plan_verified": False, "inputs_verified": False, "inventory_performed": False}
                report["limitations"].append("A bound original plan/operation pair was unavailable before runtime intent; no plan/input verification or workspace inventory was performed.")
                terminal = "interrupted" if attempt["status"] not in _TERMINAL else None
            else:
                plan = _owned_workspace(workspace, binding)
                sealed = None
                if receipt_exists:
                    sealed = _sealed_original(workspace, plan, binding, trusted_signers,
                        **({"authority_administrators":authority_administrators} if authority_administrators is not None else {}))
                    report["original_execution"] = sealed
                    report["original_execution_root_hash"] = sealed["receipt"]["root_hash"]
                    completed = sealed["verification"]["analysis_completed"]
                    if completed:
                        _require(attempt["status"] in {"start_requested", "completed"},
                                 "Sealed analysis conflicts with the registered execution lifecycle")
                        if attempt["status"] == "completed":
                            _require(attempt["events"][-1]["payload"]["execution_root_hash"] == sealed["receipt"]["root_hash"],
                                     "Terminal registry root differs from the verified original execution")
                        report["analysis_completed"] = True
                        report["cleanup"]["status"] = "verified_original_completed_transport"
                        terminal = "completed" if attempt["status"] not in _TERMINAL else None
                    else:
                        _require(attempt["status"] != "completed", "Completed registry conflicts with a failed original execution")
                if not sealed or not sealed["verification"]["analysis_completed"]:
                    if runtime_intent:
                        contract = _runtime_contract(attempt, workspace, plan)
                        report["recorded_runtime"] = contract
                        resolved = _cleanup(contract, plan, root, report, recovery_checkpoint)
                        _require(resolved, "Creation without a recorded container ID remains unresolved")
                    else:
                        report["cleanup"]["status"] = "no_registered_runtime_intent"
                    terminal = ("failed" if sealed else "interrupted") if attempt["status"] not in _TERMINAL else None
                _require(plan.data_classification != "restricted_clinical" or not (workspace / "inputs").exists(),
                         "Clinical input inventory is not admitted by this recovery workflow")
                inventory = _snapshot_original(workspace, root / "original-snapshot")
                _require(_owned_workspace(workspace, binding).sha256() == plan.sha256(), "Original operation changed after snapshot")
                report["original_inventory"] = inventory
                report["snapshot"] = dag.add_artifact(root / "original-snapshot")
            report["status"] = "reconciled" if terminal is not None else "terminal_observation"
            report["proposed_terminal_status"] = terminal
        except (OSError, ValueError, RuntimeError, KeyError, TypeError, IndexError, RecursionError) as error:
            terminal = None
            report["status"] = "unresolved"
            report["proposed_terminal_status"] = None
            report["errors"].append({"type": type(error).__name__, "message": str(error)[:2000]})
        _save(root / "recovery.json", report)
        report_ref = dag.add_artifact(root / "recovery.json")
        dag.append("recovery_observation_finished", {"report": report_ref, "status": report["status"],
                   "proposed_terminal_status": terminal, "original_event_root_hash": attempt["event_root_hash"]}, parents=[previous])
        receipt = dag.finalize(analysis_fingerprint=attempt["plan_sha256"])
        _save(root / "receipt.json", receipt)
        _require(verify_execution(root / "audit/manifest.json", receipt["root_hash"])["valid"], "Recovery evidence failed integrity verification")
        committed = None
        if terminal is not None:
            payload = {"recovery_root_hash": receipt["root_hash"], "recovery_workspace": str(root),
                       "execution_root_hash": report["original_execution_root_hash"],
                       "analysis_completed": report["analysis_completed"], "clinical_release_allowed": False}
            committed = registry.append_event(attempt_id, terminal, payload, actor_id)
        after = registry.export_checkpoint()
        _save(root / "registry-after.json", after)
        return {**report, "receipt": receipt, "registry_terminal_event": committed,
                "registry_status_after": registry.get(attempt_id)["status"], "registry_checkpoint_after": after}
