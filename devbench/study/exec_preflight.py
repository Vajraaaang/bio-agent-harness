"""Explicitly authorized, unscored negative control of the scored exec surface.

An offline fixture is protocol coverage, never live permission evidence. A live
receipt proves only the listed synthetic probes for this launch configuration.
"""
from contextlib import ExitStack
import copy
import hashlib
import json
import os
from pathlib import Path
import secrets
import shlex
import shutil
import socket
import stat
import time

from devbench.adapters.code_action import _file_hash

from .adapter import StudyCodeActionAdapter


PROTOCOL = "devbench.exec-preflight/1"
TIMEOUT_SEC = 45
MAX_RECORD_BYTES = 262_144
MAX_RECORDS = 64
MAX_PROBE_OUTPUT_BYTES = 8192
_PARENT_ALLOWLIST = ("CODEX_PERMISSION_PROFILE", "DEVHARNESS_WORKSPACE_ROOT", "PYTHONNOUSERSITE")
_BASE_CHECKS = {"private_read_denied", "sibling_read_denied", "protected_write_denied",
                "workspace_write_allowed", "unassigned_socket_denied", "tcp_denied",
                "isolated_python", "child_environment_matches"}

# Only this coordinator-authored source runs in the participant shell. All file
# targets are new dummy canaries except the assigned broker capability. Its value
# is sent only to that assigned local socket, and never included in output.
_PROBE_SOURCE = '''import errno
import json
import os
import socket
import sys
import time


def main(config):
    checks, errors = {}, {}

    def check(name, operation, denied=False):
        try:
            operation()
            checks[name] = not denied
        except OSError as error:
            checks[name] = denied and error.errno in (errno.EACCES, errno.EPERM)
            errors[name] = {"type": type(error).__name__, "errno": error.errno}
        except Exception as error:
            checks[name] = False
            errors[name] = {"type": type(error).__name__}

    def read_dummy(path):
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as handle:
            handle.read(1)

    def append_dummy(path):
        descriptor = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(b"probe-write")

    def connect(family, address):
        with socket.socket(family, socket.SOCK_STREAM) as connection:
            connection.settimeout(2)
            connection.connect(address)

    def handshake():
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(2)
            connection.connect(config["assigned_socket"])
            descriptor = os.open(config["capability_file"], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(descriptor, "r") as handle:
                capability = handle.read(129)
            if not 32 <= len(capability) <= 128:
                raise ValueError("Invalid capability size")
            request = {"tool": "__devbench_preflight__", "arguments": {}, "capability": capability}
            connection.sendall(json.dumps(request).encode() + b"\\n")
            deadline, response = time.monotonic() + 2, bytearray()
            while not response.endswith(b"\\n") and len(response) <= 512:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("Handshake deadline expired")
                connection.settimeout(remaining)
                block = connection.recv(513 - len(response))
                if not block:
                    break
                response.extend(block)
            if (len(response) > 512 or not response.endswith(b"\\n")
                    or json.loads(response) != {"status": "preflight_connection_verified"}):
                raise ValueError("Unrecognized preflight handshake")

    check("private_read_denied", lambda: read_dummy(config["private_canary"]), True)
    check("sibling_read_denied", lambda: read_dummy(config["sibling_canary"]), True)
    check("protected_write_denied", lambda: append_dummy(config["protected_canary"]), True)
    check("workspace_write_allowed", lambda: append_dummy(config["workspace_canary"]))
    check("unassigned_socket_denied", lambda: connect(socket.AF_UNIX, config["unassigned_socket"]), True)
    check("tcp_denied", lambda: connect(socket.AF_INET, ("127.0.0.1", config["tcp_port"])), True)
    checks["isolated_python"] = sys.flags.isolated == 1
    mismatched = sorted(name for name, value in config["child_environment"].items()
                        if os.environ.get(name) != value)
    checks["child_environment_matches"] = not mismatched
    if mismatched:
        errors["child_environment_matches"] = {"mismatched_names": mismatched}
    if config["assigned_socket"] is None:
        checks["tool_endpoint_absent"] = ("DEVHARNESS_TOOL_SOCKET" not in os.environ
                                          and "DEVHARNESS_TOOL_CAPABILITY_FILE" not in os.environ)
    else:
        check("assigned_socket_handshake_allowed", handshake)
    print(json.dumps({"protocol": config["protocol"], "nonce": config["nonce"],
                      "checks": checks, "errors": errors}, allow_nan=False, sort_keys=True))
    return 0 if all(value is True for value in checks.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main(CONFIG))
'''


def _json_bytes(value):
    return json.dumps(value, allow_nan=False, sort_keys=True, separators=(",", ":")).encode()


def _sha(value):
    return hashlib.sha256(_json_bytes(value)).hexdigest()


def _safe_path(value):
    path = Path(value)
    if not path.is_absolute() or any(part.is_symlink() for part in (path, *path.parents)):
        raise ValueError("Exec preflight requires absolute nonsymlink storage paths")
    if path.resolve() != path:
        raise ValueError("Exec preflight paths must be canonical")
    return path


def _write_new(path, data, mode=0o600):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())


def _read_small(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as handle:
        details = os.fstat(handle.fileno())
        if not stat.S_ISREG(details.st_mode) or details.st_size > MAX_RECORD_BYTES:
            raise ValueError("preflight_output_is_not_bounded_regular_file")
        data = handle.read(MAX_RECORD_BYTES + 1)
    if len(data) > MAX_RECORD_BYTES:
        raise ValueError("preflight_output_exceeds_capture_budget")
    return data


def _exact_invocation(command, python, script):
    if not isinstance(command, str) or len(command) > 16_000:
        return False
    try:
        expected = shlex.join([str(python), "-I", str(script)])
        if command.strip() == expected:
            return True
        parts = shlex.split(command)
        if (len(parts) == 3 and parts[0] in {"/bin/sh", "/bin/bash", "/bin/zsh", "/usr/bin/bash"}
                and parts[1] in {"-c", "-lc"}):
            # Match the original shell-safe serialization, not just argv after
            # quote removal: double quotes can activate command substitution.
            return parts[2].strip() == expected
        return False
    except ValueError:
        return False


def _record_allowed(record, python, script):
    if not isinstance(record, dict):
        return False
    kind = record.get("type")
    if kind in {"thread.started", "turn.started", "turn.completed"}:
        return "item" not in record
    if kind not in {"item.started", "item.updated", "item.completed"}:
        return False
    item = record.get("item")
    if not isinstance(item, dict):
        return False
    if item.get("type") in {"reasoning", "agent_message"}:
        return True
    return item.get("type") == "command_execution" and _exact_invocation(item.get("command"), python, script)


def _verify_records(records, python, script, nonce, expected_checks):
    """Accept one known JSONL lifecycle and one exact trusted command completion."""
    if not records or any(not _record_allowed(record, python, script) for record in records):
        raise ValueError("missing_or_unknown_cli_records")
    kinds = [record["type"] for record in records]
    if (kinds[0] != "thread.started" or kinds[-1] != "turn.completed"
            or any(kinds.count(kind) != 1 for kind in ("thread.started", "turn.started", "turn.completed"))
            or kinds.index("turn.started") != 1
            or not isinstance(records[0].get("thread_id"), str) or not records[0]["thread_id"]):
        raise ValueError("incomplete_or_multiple_provider_sessions")
    commands = [record for record in records if record.get("item", {}).get("type") == "command_execution"]
    starts = [record for record in commands if record["type"] == "item.started"]
    completed = [record for record in commands if record["type"] == "item.completed"]
    if len(starts) != 1 or len(completed) != 1 or commands[0] is not starts[0] or commands[-1] is not completed[0]:
        raise ValueError("missing_or_extra_command_action")
    identifier = starts[0]["item"].get("id")
    if not isinstance(identifier, str) or not identifier or any(record["item"].get("id") != identifier for record in commands):
        raise ValueError("command_identity_mismatch")
    item = completed[0]["item"]
    if (item.get("status") != "completed" or type(item.get("exit_code")) is not int or item["exit_code"] != 0
            or any(record.get(key) or record.get("item", {}).get(key)
                   for record in records for key in ("truncated", "output_truncated", "aggregated_output_truncated"))):
        raise ValueError("failed_or_truncated_command")
    output = item.get("aggregated_output")
    if not isinstance(output, str) or len(output.encode()) > MAX_PROBE_OUTPUT_BYTES:
        raise ValueError("missing_or_oversized_probe_output")

    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate_probe_json_keys")
            result[key] = value
        return result

    report = json.loads(output, object_pairs_hook=unique,
                        parse_constant=lambda value: (_ for _ in ()).throw(ValueError("nonfinite_probe_json")))
    if (not isinstance(report, dict) or set(report) != {"protocol", "nonce", "checks", "errors"}
            or report["protocol"] != PROTOCOL or report["nonce"] != nonce
            or not isinstance(report["checks"], dict) or set(report["checks"]) != expected_checks
            or not isinstance(report["errors"], dict)):
        raise ValueError("malformed_probe_report")
    if not all(value is True for value in report["checks"].values()):
        raise ValueError("failed_or_unknown_mandatory_checks")
    return report


def require_scored_model_identity(evidence):
    if (evidence.get("model_identity_verified") is not True
            or not isinstance(evidence.get("actual_model"), str) or not evidence["actual_model"]):
        raise RuntimeError("Verified model identity is unavailable; scored participant was not launched")


def run_exec_preflight(adapter, output_dir, *, allow_model_execution=False):
    """Run at most one 45-second provider session, only after explicit opt-in.

    ``output_dir`` must be below an existing private deny root. The caller must
    already protect workspace/control and start the assigned assisted broker.
    No boundaries, scientific inputs, solve.py, or scored adapter state change.
    Reusing an output directory with a reservation is rejected before dispatch.
    """
    if allow_model_execution is not True:
        raise PermissionError("Exec preflight model execution requires explicit authorization")
    if not isinstance(adapter, StudyCodeActionAdapter):
        raise ValueError("Exec preflight requires a StudyCodeActionAdapter")
    output = _safe_path(output_dir)
    boundary, workspace = adapter.boundary, _safe_path(adapter.boundary.workspace)
    control = _safe_path(workspace / "control")
    if not any(output.is_relative_to(root) and output != root for root in boundary.private_roots):
        raise ValueError("Exec preflight output must be below an existing private deny root")
    if not control.is_dir() or not any(control.is_relative_to(path) for path in boundary.protected_paths):
        raise ValueError("Exec preflight control directory must already have scored read-only protection")
    output.mkdir(mode=0o700, parents=True, exist_ok=True)
    _write_new(output / "reservation.json", _json_bytes({"protocol": PROTOCOL, "max_provider_sessions": 1}))
    started, records, created, listeners = time.monotonic(), [], [], ExitStack()
    probe, returncode, report, final_message = None, None, None, None
    evidence = {"protocol": PROTOCOL, "verified": False,
        "scope": "exact_scored_codex_exec_surface_synthetic_negative_control",
        "boundary_scope": "protected_benchmark_assets_only",
        "scientific_analysis_performed": False, "max_provider_sessions": 1,
        "timeout_sec": min(TIMEOUT_SEC, adapter.timeout_sec), "scored": False,
        "inference_dispatch_attempted": False, "model_launched": False,
        "model_identity_verified": False, "actual_model": None,
        "socket_evidence_scope": "authenticated_no_op_connection_only_not_tool_correctness",
        "sibling_probe_scope": "new_dummy_canary_in_existing_explicitly_denied_sibling_workspace",
        "checks": {}, "validation_errors": [], "artifacts": {}}

    def create(path, content, mode=0o600):
        _write_new(path, content, mode)
        details = path.lstat()
        created.append((path, details.st_dev, details.st_ino, False))

    def directory(path):
        path.mkdir(mode=0o700)
        details = path.lstat()
        created.append((path, details.st_dev, details.st_ino, True))

    try:
        executable = shutil.which(adapter.executable)
        if not executable or not adapter.python.is_file():
            raise ValueError("missing_scored_executable_or_staged_python")
        from .cli_identity import read_codex_identity
        evidence["codex_identity"] = read_codex_identity(Path(executable).resolve())
        scratch = _safe_path(workspace / ".tmp")
        if not scratch.exists():
            directory(scratch)
        nonce = secrets.token_hex(12)
        script = control / ("exec-preflight-" + nonce + ".py")
        schema = control / ("exec-preflight-" + nonce + ".schema.json")
        # The parent CLI writes this file; participant commands must not replace
        # it with a symlink before that write. The existing control directory has
        # a read-only actor grant while remaining writable by the coordinator.
        final_message = control / (".exec-preflight-" + nonce + ".txt")
        private = output / "private-canary"
        siblings = [path for path in boundary.private_roots if path.parent == workspace.parent]
        if not siblings:
            raise ValueError("no_existing_denied_sibling_workspace_available")
        sibling = _safe_path(siblings[0])
        if not sibling.is_dir():
            raise ValueError("denied_sibling_workspace_unavailable")
        sibling_canary = sibling / (".exec-preflight-" + nonce + ".canary")
        protected = control / ("exec-preflight-" + nonce + ".canary")
        writable = workspace / (".exec-preflight-" + nonce + ".canary")
        for path in (private, sibling_canary, protected, writable, final_message):
            # In particular, do not chmod the write-negative-control read-only:
            # its denial must come from the scored sandbox profile.
            create(path, b"")
        unassigned = control / ("u-" + nonce[:8])
        if len(str(unassigned).encode()) > 100:
            raise ValueError("unassigned_socket_path_too_long")
        unix = listeners.enter_context(socket.socket(socket.AF_UNIX, socket.SOCK_STREAM))
        unix.bind(str(unassigned))
        details = unassigned.lstat()
        created.append((unassigned, details.st_dev, details.st_ino, False))
        unix.listen(1)
        tcp = listeners.enter_context(socket.socket(socket.AF_INET, socket.SOCK_STREAM))
        tcp.bind(("127.0.0.1", 0))
        tcp.listen(1)
        from .cli_identity import effective_child_environment
        configured_child_environment = adapter._child_environment()
        child_environment = effective_child_environment(configured_child_environment, evidence["codex_identity"])
        expected = _BASE_CHECKS | ({"tool_endpoint_absent"} if boundary.socket_path is None
                                 else {"assigned_socket_handshake_allowed"})
        config = {"protocol": PROTOCOL, "nonce": nonce, "private_canary": str(private),
            "sibling_canary": str(sibling_canary), "protected_canary": str(protected),
            "workspace_canary": str(writable), "unassigned_socket": str(unassigned),
            "tcp_port": tcp.getsockname()[1], "child_environment": child_environment,
            "assigned_socket": str(boundary.socket_path) if boundary.socket_path is not None else None,
            "capability_file": str(adapter.tool_capability_file) if adapter.tool_capability_file is not None else None}
        source = ("CONFIG = " + repr(config) + "\n" + _PROBE_SOURCE).encode()
        schema_bytes = _json_bytes({"type": "object", "additionalProperties": False,
            "required": ["protocol", "nonce", "checks", "errors"], "properties": {
                "protocol": {"const": PROTOCOL}, "nonce": {"const": nonce},
                "checks": {"type": "object", "additionalProperties": False,
                    "required": sorted(expected), "properties": {key: {"const": True} for key in sorted(expected)}},
                "errors": {"type": "object", "additionalProperties": False, "properties": {}, "required": []}}})
        create(script, source, 0o444)
        create(schema, schema_bytes, 0o444)
        _write_new(output / "probe.py", source)
        _write_new(output / "probe_schema.json", schema_bytes)
        source_hash, schema_hash = _file_hash(script), _file_hash(schema)
        # Copy mutable capture state, not the environment or launch machinery.
        # The scored instance and its durable dispatch callback stay untouched.
        probe = copy.copy(adapter)
        probe.tool_events, probe.events, probe.command_events = [], [], []
        probe._trace_bytes, probe._session_id = 0, None
        probe._turn_usage, probe._turn_result = None, None
        probe._failed_tools, probe._pending_tools, probe._last_process_measurements = {}, {}, {}
        probe._phase = "exec_preflight"
        probe.metadata = {"billing_mode": probe.billing_mode, "cost_usd": None, "turns": [],
            "model_ids": [], "actual_model": None, "mcp_attempts": 0, "mcp_cancellations": 0,
            "inference_dispatch_attempted": False}
        probe._reset_model_identity()
        probe._model_capture = None
        probe._execution_authorized = True
        probe._start_model_capture(output / "model_identity.json")
        probe.coordinator_dispatch_callback = lambda observation: _write_new(
            output / "dispatch_checkpoint.json", _json_bytes({**observation, "scored": False, "protocol": PROTOCOL}))
        environment = probe._environment()
        command = probe._command(os.path.abspath(executable), workspace, schema, final_message, None)
        invocation = shlex.join([str(probe.python), "-I", str(script)])
        instruction = ("This is an unscored permission preflight. Execute exactly one command, shown below, "
            "then finish. Do not read other files, run other commands or tools, edit files, request permissions, "
            "or perform scientific analysis. The protected script performs synthetic checks and the assigned "
            "broker's no-op handshake. Its command output is the evidence; your final text is ignored.\n" + invocation)
        recorded_command = [part.replace(probe._model_capture.capability, "<ephemeral-local-capture-token>")
                            for part in command] if probe._model_capture is not None else command
        configuration = {"cli_command": recorded_command,
            "cli_command_redaction": "ephemeral local telemetry capability only; command_sha256 binds actual argv",
            "permission_overrides": boundary.config_overrides(),
            "configured_child_environment_allowlist": configured_child_environment,
            "child_environment_allowlist": child_environment,
            "parent_environment_allowlist": {key: environment.get(key) for key in _PARENT_ALLOWLIST}}
        source_paths = (Path(__file__), Path(__file__).with_name("adapter.py"),
                        Path(__file__).with_name("model_telemetry.py"),
                        Path(__file__).with_name("permissions.py"),
                        Path(__file__).with_name("cli_identity.py"),
                        Path(__file__).parents[1] / "adapters" / "code_action.py",
                        Path(__file__).parents[2] / "devbench_engine" / "adapters.py")
        evidence.update(source_sha256={str(path): _file_hash(path) for path in source_paths},
            probe_source_sha256=source_hash, probe_schema_sha256=schema_hash,
            configuration_sha256=_sha(configuration["permission_overrides"]),
            command_sha256=_sha(command), child_environment_allowlist_sha256=_sha(child_environment),
            parent_environment_allowlist_sha256=_sha(configuration["parent_environment_allowlist"]),
            expected_command=invocation)
        if any(value is None for value in evidence["source_sha256"].values()):
            raise ValueError("source_capture_unavailable")
        _write_new(output / "launch_configuration.json", _json_bytes(configuration))
        _write_new(output / "instruction.txt", instruction.encode())
        raw_descriptor = os.open(output / "cli_records.jsonl", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        raw_handle = listeners.enter_context(os.fdopen(raw_descriptor, "wb"))
        raw_size = 0
        original_event = probe._event

        def capture(record):
            nonlocal raw_size
            data = _json_bytes(record) + b"\n"
            raw_size += len(data)
            if raw_size > MAX_RECORD_BYTES or len(records) >= MAX_RECORDS:
                raise ValueError("preflight_cli_record_budget_exceeded")
            raw_handle.write(data)
            raw_handle.flush()
            records.append(record)
            if not _record_allowed(record, probe.python, script):
                raise ValueError("unexpected_cli_event_or_extra_action")
            if record.get("type") == "item.completed" and record.get("item", {}).get("type") == "command_execution":
                captured_source, captured_schema = _file_hash(script), _file_hash(schema)
                evidence.update(probe_source_sha256_at_command_completion=captured_source,
                                probe_schema_sha256_at_command_completion=captured_schema)
                if captured_source != source_hash or captured_schema != schema_hash:
                    raise ValueError("protected_probe_changed_at_command_completion")
            original_event(record)

        probe._event = capture
        probe._execution_authorized = True
        try:
            returncode, stdout, stderr = probe._run_process(
                command, workspace, environment, instruction, evidence["timeout_sec"], parse_events=True)
        finally:
            probe._execution_authorized = False
        _write_new(output / "stdout.txt", stdout.encode())
        _write_new(output / "stderr.txt", stderr.encode())
        _write_new(output / "agent_final_message.txt", _read_small(final_message))
        if returncode != 0 or probe.metadata.get("event_capture_complete") is not True:
            raise ValueError("failed_process_or_incomplete_cli_capture")
        evidence.update(probe_source_sha256_after=_file_hash(script), probe_schema_sha256_after=_file_hash(schema))
        if evidence["probe_source_sha256_after"] != source_hash or evidence["probe_schema_sha256_after"] != schema_hash:
            raise ValueError("protected_probe_changed_after_execution")
        from .cli_identity import verify_codex_identity
        verify_codex_identity(evidence["codex_identity"])
        if _file_hash(protected) != hashlib.sha256(b"").hexdigest():
            raise ValueError("protected_canary_was_modified")
        if _file_hash(writable) != hashlib.sha256(b"probe-write").hexdigest():
            raise ValueError("allowed_write_not_observed")
        if configuration["permission_overrides"] != boundary.config_overrides():
            raise ValueError("scored_boundary_changed")
        report = _verify_records(records, probe.python, script, nonce, expected)
        evidence["checks"] = report["checks"]
        evidence["probe_report"] = report
    except Exception as error:
        evidence["validation_errors"].append(f"{type(error).__name__}: {error}"[:1000])
    finally:
        if probe is not None:
            try:
                probe._stop_model_capture()
            except Exception as error:
                evidence["validation_errors"].append("native_identity_capture_failed:" + type(error).__name__)
            probe._execution_authorized = False
        try:
            listeners.close()
        except OSError as error:
            evidence["validation_errors"].append("capture_or_listener_close_failed:" + type(error).__name__)
        if probe is not None:
            dispatched = probe.metadata.get("inference_dispatch_attempted") is True
            observed = probe._session_id is not None
            evidence.update(inference_dispatch_attempted=dispatched,
                model_launched=True if observed else (None if dispatched else False),
                provider_session_observed=observed, session_id=probe._session_id,
                dispatch_stage=probe.metadata.get("dispatch_stage"),
                billing={"billing_mode": probe.billing_mode, "cost_usd": None,
                         "usage": probe._turn_usage, "tokens": probe._token_total(probe._turn_usage),
                         "scored": False}, process_measurements=probe._last_process_measurements,
                requested_model=probe.requested_model, actual_model=probe.metadata.get("actual_model"),
                model_identity_verified=probe.metadata.get("model_identity_verified") is True,
                model_identity_origin=probe.metadata.get("model_identity_origin"),
                model_identity_scope=probe.metadata.get("model_identity_scope"),
                model_identity_observations=probe.metadata.get("model_identity_observations", []),
                native_model_identity=probe.metadata.get("native_model_identity"),
                event_capture_complete=probe.metadata.get("event_capture_complete") is True,
                event_capture_loss=probe.metadata.get("event_capture_loss", {}))
            if returncode is None:
                returncode = probe.metadata.get("returncode", probe._last_process_measurements.get("returncode"))
            if not (output / "stderr.txt").exists():
                _write_new(output / "stderr.txt", str(probe.metadata.get("stderr_tail", "")).encode())
            if final_message is not None and not (output / "agent_final_message.txt").exists():
                try:
                    _write_new(output / "agent_final_message.txt", _read_small(final_message))
                except (OSError, ValueError) as error:
                    evidence["validation_errors"].append("final_message_capture_failed:" + type(error).__name__)
        for path, device, inode, is_directory in reversed(created):
            try:
                details = path.lstat()
                # The CLI owns this exclusively reserved output name and may
                # atomically replace it. Its text is retained, never trusted as
                # proof. All other cleanup targets keep their inode guard.
                cli_output = path == final_message and stat.S_ISREG(details.st_mode)
                if (not cli_output and (details.st_dev != device or details.st_ino != inode)
                        or stat.S_ISLNK(details.st_mode)):
                    raise ValueError("probe_cleanup_target_changed")
                path.rmdir() if is_directory else path.unlink()
            except OSError as error:
                evidence["validation_errors"].append("probe_cleanup_failed:" + type(error).__name__)
            except ValueError as error:
                evidence["validation_errors"].append(str(error))
        evidence.update(returncode=returncode, duration_sec=time.monotonic() - started,
                        cli_records_captured=len(records), verified=report is not None and not evidence["validation_errors"])
        for name in ("reservation.json", "dispatch_checkpoint.json", "probe.py", "probe_schema.json", "model_identity.json",
                     "launch_configuration.json", "instruction.txt", "cli_records.jsonl", "stdout.txt", "stderr.txt",
                     "agent_final_message.txt"):
            digest = _file_hash(output / name)
            if digest is not None:
                evidence["artifacts"][name] = digest
        _write_new(output / "evidence.json", _json_bytes(evidence))
    return evidence
