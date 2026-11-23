"""Qualify offline compute contracts using fake transport, filesystem, and clocks."""

import hashlib
import json
import stat
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, call

import pytest
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from jsonschema import Draft202012Validator


from devbench.analysis.workbench_compute_contracts import (
    MAX_FILE_BYTES, MAX_JOB_BYTES, MEMORY_RESERVE, check_direct_upload, digest_json,
    sign_manifest, sign_verification_receipt, verify_input_chunks, verify_manifest,
    verify_staged_result, verify_verification_receipt,
)
from devbench.analysis.workbench_compute_ipc import (
    UsageCache, bind_helper_socket, decode_frame, dispatch_rpc, encode_frame, receive_frame, safe_rpc_error,
    send_frame, usage_display, validate_socket_path,
)
from devbench.analysis.workbench_compute_lifecycle import OfflineRunLedger, run_watchdog, watchdog_command


SIGNING_DOMAIN = b"devbench.run-manifest/1\0"
NOW = datetime(2026, 11, 4, 12, tzinfo=timezone.utc)
PAYLOAD = b"cell,g1,g2\nA,2,3\nB,0,1\n"
SCHEMA_PATH = Path(__file__).resolve().parents[1] / (
    "docs/superpowers/specs/2026-11-04-workbench-compute-contracts.schema.json"
)


def _digest(content):
    return hashlib.sha256(content).hexdigest()


def _utc(timestamp):
    return timestamp.isoformat(timespec="seconds").replace("+00:00", "Z")


def _canonical(value):
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")


def _manifest_bytes(manifest):
    return SIGNING_DOMAIN + _canonical(manifest)


@pytest.fixture(scope="module")
def schema_validator():
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)

    def select(name):
        return Draft202012Validator(
            {"$defs": schema["$defs"], "$ref": f"#/$defs/{name}"},
            format_checker=Draft202012Validator.FORMAT_CHECKER,
        )

    return select


@dataclass
class _SyntheticRun:
    """Drive the contracts with mock transport calls and no provider client."""

    verify_key: object
    validator: object
    cli: Mock
    local: Mock
    account: str | None = "synthetic-account"
    generation: int = 3
    headless_eligibility_verified: bool = True
    qualified_deadline_enforcement: bool = True
    available_bytes: int = MEMORY_RESERVE * 2
    active_runtime: str | None = None

    def __post_init__(self):
        self.ledger = None

    def reset_connection(self, account):
        self.ledger.reset_connection(account)
        self.generation += 1
        self.account = account
        if self.active_runtime is not None:
            self.cli.cancel_and_stop(self.active_runtime)
            self.active_runtime = None

    def prepare_runtime(self, manifest, signature):
        manifest = verify_manifest({
            "format": "devbench.manifest-signature/1", "manifest": manifest,
            "manifest_sha256": digest_json(manifest), "issuer_key_id": "fixture",
            "signature_ed25519_hex": signature.hex(),
        }, trusted_keys={"fixture": self.verify_key}, validator=self.validator, now=NOW)
        if self.ledger is None:
            self.ledger = OfflineRunLedger(self.account, self.generation)
        job_id = manifest["job_id"]
        if manifest["backend"] == "local_supervised":
            self.ledger.reserve(manifest)
            self.ledger.advance(job_id, "running")
            self.local.dispatch(manifest)
            return manifest
        if not self.headless_eligibility_verified:
            raise PermissionError("HEADLESS_INELIGIBLE")
        if manifest["compute_profile"]["hard_cu_cap"] is not None:
            raise PermissionError("HARD_CU_CAP_UNAVAILABLE")
        transfer = manifest["transfer"]
        check_direct_upload(
            (entry["size_bytes"] for entry in manifest["inputs"]), self.available_bytes,
            per_file_bytes=transfer["per_file_bytes"], total_input_bytes=transfer["total_input_bytes"],
        )
        self.ledger.reserve(manifest, simulated_provider_lease=self.qualified_deadline_enforcement)
        self.ledger.advance(job_id, "allocating")
        self.active_runtime = self.cli.allocate(job_id)
        return manifest

    def execute(self, manifest, signature):
        manifest = self.prepare_runtime(manifest, signature)
        if manifest["backend"] == "local_supervised":
            return
        job_id = manifest["job_id"]
        runtime = self.active_runtime
        try:
            profile = manifest["compute_profile"]
            self.cli.preflight(runtime, profile)
            argv = watchdog_command(
                job_id, profile["deadline_at_utc"], now=NOW, limit_seconds=profile["deadline_seconds"],
            )
            self.cli.arm_watchdog(runtime, argv)
            self.ledger.advance(job_id, "ready", runtime_verified=True, watchdog_armed=True)
            self.ledger.advance(job_id, "transferring")
            for entry in manifest["inputs"]:
                self.cli.upload(runtime, entry["role"], entry["path_selector"])
                verify_input_chunks(entry, self.cli.remote_input_chunks(runtime, entry["role"]))
            self.ledger.advance(job_id, "running")
            self.cli.invoke(runtime, manifest["adapter"])
            self.ledger.advance(job_id, "exited", exit_succeeded=True)
        finally:
            self.cli.stop(runtime)
            self.active_runtime = None


@pytest.fixture
def manifest(schema_validator):
    value = {
        "manifest_version": "devbench.run-manifest/1",
        "job_id": "a" * 32,
        "project_id": "b" * 32,
        "investigation_id": "c" * 32,
        "analysis_revision": _digest(b"synthetic revision"),
        "backend": "colab",
        "account_binding": {"account_id": "synthetic-account", "connection_generation": 3},
        "inputs": [{
            "role": "counts", "path_selector": "/synthetic/counts.tsv",
            "size_bytes": len(PAYLOAD), "sha256": _digest(PAYLOAD), "classification": "synthetic",
        }],
        "adapter": {
            "name": "synthetic_qc", "version": "1.0.0", "digest": _digest(b"adapter"),
            "environment_sha256": _digest(b"locked environment"),
            "validation_policy_sha256": _digest(b"validation policy"),
        },
        "effective_parameters": {},
        "parameters_sha256": _digest(_canonical({})),
        "compute_profile": {
            "hardware": "cpu", "min_ram_gb": 12, "min_disk_gb": 25,
            "deadline_seconds": 1800, "deadline_at_utc": _utc(NOW + timedelta(seconds=1800)),
            "hard_cu_cap": None,
        },
        "transfer": {
            "mode": "direct_upload", "per_file_bytes": MAX_FILE_BYTES,
            "total_input_bytes": MAX_JOB_BYTES,
        },
        "allowed_outputs": [{
            "name": "qc_metrics.json", "required": True, "max_bytes": 1024, "format": "json",
            "validator_sha256": _digest(b"QC validator"),
        }],
        "issued_at_utc": _utc(NOW),
        "approval_expires_at_utc": _utc(NOW + timedelta(seconds=60)),
    }
    schema_validator("RunManifest").validate(value)
    return value


@pytest.fixture
def reference_gate(schema_validator):
    signer = Ed25519PrivateKey.from_private_bytes(b"\x01" * 32)
    cli = Mock()
    cli.allocate.return_value = "synthetic-vm"
    cli.remote_input_chunks.return_value = (PAYLOAD[:5], PAYLOAD[5:])
    return _SyntheticRun(signer.public_key(), schema_validator("ApprovedRun"), cli, Mock()), signer


@pytest.mark.parametrize("account", [None, "other-synthetic-account"])
def test_unauthenticated_or_wrong_account_never_allocates(manifest, reference_gate, account):
    gate, signer = reference_gate
    gate.account = account
    with pytest.raises(PermissionError, match="STALE_OR_WRONG_ACCOUNT"):
        gate.execute(manifest, signer.sign(_manifest_bytes(manifest)))
    assert gate.cli.mock_calls == gate.local.mock_calls == []


@pytest.mark.parametrize("mutation", ["missing_signature", "wrong_signer", "changed_manifest"])
def test_invalid_approval_never_calls_either_backend(manifest, reference_gate, mutation):
    gate, signer = reference_gate
    signature = signer.sign(_manifest_bytes(manifest))
    if mutation == "missing_signature":
        signature = b""
    elif mutation == "wrong_signer":
        signer = Ed25519PrivateKey.from_private_bytes(b"\x02" * 32)
        signature = signer.sign(_manifest_bytes(manifest))
    else:
        manifest["effective_parameters"]["seed"] = 10
    with pytest.raises(PermissionError, match="INVALID_APPROVAL"):
        gate.execute(manifest, signature)
    assert gate.cli.mock_calls == gate.local.mock_calls == []


@pytest.mark.parametrize("constraint,error", [
    ("expired", "APPROVAL_EXPIRED"), ("headless", "HEADLESS_INELIGIBLE"),
    ("hard_cu_cap", "HARD_CU_CAP_UNAVAILABLE"),
])
def test_signed_run_still_requires_enforceable_admission(manifest, reference_gate, constraint, error):
    gate, signer = reference_gate
    if constraint == "expired":
        manifest["approval_expires_at_utc"] = _utc(NOW)
    elif constraint == "headless":
        gate.headless_eligibility_verified = False
    else:
        manifest["compute_profile"]["hard_cu_cap"] = 1
    with pytest.raises(PermissionError, match=error):
        gate.execute(manifest, signer.sign(_manifest_bytes(manifest)))
    assert gate.cli.mock_calls == gate.local.mock_calls == []


def test_same_signed_job_cannot_allocate_again_after_failed_reply(manifest, reference_gate):
    gate, signer = reference_gate
    signature = signer.sign(_manifest_bytes(manifest))
    gate.cli.allocate.side_effect = TimeoutError("synthetic allocation reply lost")
    with pytest.raises(TimeoutError):
        gate.prepare_runtime(manifest, signature)
    with pytest.raises(PermissionError, match="JOB_ALREADY_RESERVED"):
        gate.prepare_runtime(manifest, signature)
    gate.cli.allocate.assert_called_once_with(manifest["job_id"])


@pytest.mark.parametrize("account", [None, "synthetic-account"], ids=["disconnect", "reconnect"])
def test_generation_bump_revokes_pending_and_cancels_active(manifest, reference_gate, account):
    gate, signer = reference_gate
    pending = deepcopy(manifest)
    pending["job_id"] = "d" * 32
    pending_signature = signer.sign(_manifest_bytes(pending))
    gate.prepare_runtime(manifest, signer.sign(_manifest_bytes(manifest)))
    gate.reset_connection(account)
    assert gate.generation == 4
    assert gate.ledger.connection_generation == 4
    assert gate.ledger.get(manifest["job_id"]).phase == "cancel_requested"
    assert gate.active_runtime is None
    assert gate.cli.mock_calls == [call.allocate(manifest["job_id"]), call.cancel_and_stop("synthetic-vm")]
    gate.cli.reset_mock()
    with pytest.raises(PermissionError, match="STALE_OR_WRONG_ACCOUNT"):
        gate.execute(pending, pending_signature)
    assert gate.cli.mock_calls == gate.local.mock_calls == []


@pytest.mark.parametrize(
    "sizes,available_bytes",
    [([8_000_001], MEMORY_RESERVE * 2), ([8_000_000, 8_000_000, 1], MEMORY_RESERVE * 2),
     ([4], MEMORY_RESERVE + 131_091)],
    ids=["file", "aggregate", "base64-rounded-memory"],
)
def test_upload_limit_rejection_precedes_allocation(manifest, reference_gate, sizes, available_bytes):
    gate, signer = reference_gate
    gate.available_bytes = available_bytes
    manifest["inputs"] = [dict(manifest["inputs"][0], role=f"counts_{index}", size_bytes=size)
                          for index, size in enumerate(sizes)]
    with pytest.raises(ValueError, match="DIRECT_UPLOAD_LIMIT_EXCEEDED"):
        gate.execute(manifest, signer.sign(_manifest_bytes(manifest)))
    assert gate.cli.mock_calls == gate.local.mock_calls == []


def test_exact_upload_limits_are_admissible():
    # Four raw bytes become eight encoded bytes. Exactly 131,092 bytes fit.
    check_direct_upload([4], MEMORY_RESERVE + 131_092)
    check_direct_upload([8_000_000, 8_000_000], MEMORY_RESERVE * 2)


def test_streamed_remote_sha256_mismatch_stops_before_invocation(manifest, reference_gate):
    gate, signer = reference_gate
    gate.cli.remote_input_chunks.return_value = (PAYLOAD[:5], b"X", PAYLOAD[6:])
    with pytest.raises(ValueError, match="INPUT_INTEGRITY_FAILURE"):
        gate.execute(manifest, signer.sign(_manifest_bytes(manifest)))
    assert [entry[0] for entry in gate.cli.mock_calls] == [
        "allocate", "preflight", "arm_watchdog", "upload", "remote_input_chunks", "stop",
    ]
    assert gate.local.mock_calls == []


@pytest.mark.parametrize("seconds", [0, -1, 1801])
def test_watchdog_rejects_deadline_outside_approved_window(manifest, reference_gate, seconds):
    gate, signer = reference_gate
    manifest["compute_profile"]["deadline_at_utc"] = _utc(NOW + timedelta(seconds=seconds))
    with pytest.raises(PermissionError, match="APPROVAL_EXPIRED|INVALID_WATCHDOG_DEADLINE"):
        gate.execute(manifest, signer.sign(_manifest_bytes(manifest)))
    assert gate.cli.mock_calls == []


@pytest.mark.parametrize("job_id,deadline,error", [
    ("../synthetic", "2026-11-04T12:30:00Z", "INVALID_WATCHDOG_JOB_ID"),
    ("a" * 32, "2026-11-04T12:30:00Z\nextra", "INVALID_WATCHDOG_DEADLINE"),
])
def test_watchdog_arguments_cannot_extend_the_fixed_command(job_id, deadline, error):
    with pytest.raises(ValueError, match=error):
        watchdog_command(job_id, deadline, now=NOW, limit_seconds=1800)


def test_watchdog_fixed_command_arms_before_adapter(manifest, reference_gate):
    gate, signer = reference_gate
    gate.execute(manifest, signer.sign(_manifest_bytes(manifest)))
    assert gate.cli.mock_calls == [
        call.allocate(manifest["job_id"]),
        call.preflight("synthetic-vm", manifest["compute_profile"]),
        call.arm_watchdog("synthetic-vm", (
            "/opt/devbench/venv/bin/python", "-I", "-m", "devbench_colab_watchdog",
            "--job-id", "a" * 32, "--deadline-utc", "2026-11-04T12:30:00Z",
        )),
        call.upload("synthetic-vm", "counts", "/synthetic/counts.tsv"),
        call.remote_input_chunks("synthetic-vm", "counts"),
        call.invoke("synthetic-vm", manifest["adapter"]),
        call.stop("synthetic-vm"),
    ]


def test_watchdog_attempts_vm_stop_even_if_group_signal_fails():
    fake_system = Mock()
    fake_system.process.terminate_owned_group.side_effect = ProcessLookupError()
    with pytest.raises(ProcessLookupError):
        run_watchdog(
            remaining_seconds=1, monotonic=Mock(side_effect=[0, 1]),
            sleep=fake_system.clock.sleep, terminate_group=fake_system.process.terminate_owned_group,
            unassign=fake_system.provider.unassign_owned_vm,
        )
    assert fake_system.mock_calls == [
        call.clock.sleep(1), call.process.terminate_owned_group(), call.provider.unassign_owned_vm(),
    ]


def test_unqualified_vm_termination_blocks_before_allocation(manifest, reference_gate):
    gate, signer = reference_gate
    gate.qualified_deadline_enforcement = False
    with pytest.raises(PermissionError, match="HARD_RUNTIME_DEADLINE_UNAVAILABLE"):
        gate.execute(manifest, signer.sign(_manifest_bytes(manifest)))
    assert gate.cli.mock_calls == gate.local.mock_calls == []


def test_cancelled_allocation_can_only_close_after_confirmed_absence(manifest):
    ledger = OfflineRunLedger("synthetic-account", 3)
    job_id = manifest["job_id"]
    with pytest.raises(PermissionError, match="HARD_RUNTIME_DEADLINE_UNAVAILABLE"):
        ledger.reserve(manifest)
    ledger.reserve(manifest, simulated_provider_lease=True)
    ledger.advance(job_id, "allocating")
    with pytest.raises(PermissionError, match="RUNTIME_OR_WATCHDOG_UNVERIFIED"):
        ledger.advance(job_id, "ready", runtime_verified=True)
    ledger.reset_connection(None)
    with pytest.raises(PermissionError, match="STALE_GENERATION"):
        ledger.advance(job_id, "ready", runtime_verified=True, watchdog_armed=True)
    ledger.advance(job_id, "cleanup_pending")
    with pytest.raises(PermissionError, match="CLEANUP_UNCONFIRMED"):
        ledger.advance(job_id, "closed")
    assert ledger.advance(job_id, "closed", absence_confirmed=True).outcome == "cancelled"


@pytest.mark.parametrize("phase", ["allocate", "invoke"])
def test_colab_failures_never_switch_to_local(manifest, reference_gate, phase):
    gate, signer = reference_gate
    getattr(gate.cli, phase).side_effect = RuntimeError("synthetic provider failure")
    with pytest.raises(RuntimeError, match="synthetic provider failure"):
        gate.execute(manifest, signer.sign(_manifest_bytes(manifest)))
    assert gate.cli.allocate.call_count == 1
    assert gate.cli.stop.call_count == (phase == "invoke")
    assert gate.local.mock_calls == []


def test_local_failure_never_switches_to_colab(manifest, reference_gate, schema_validator):
    gate, signer = reference_gate
    manifest.update(
        backend="local_supervised", account_binding=None,
        transfer={"mode": "local", "per_file_bytes": None, "total_input_bytes": None},
    )
    schema_validator("RunManifest").validate(manifest)
    gate.local.dispatch.side_effect = RuntimeError("synthetic local failure")
    with pytest.raises(RuntimeError, match="synthetic local failure"):
        gate.execute(manifest, signer.sign(_manifest_bytes(manifest)))
    assert gate.cli.mock_calls == []


@pytest.mark.parametrize("directory_uid,directory_mode", [(501, 0o700), (502, 0o700), (501, 0o755)])
def test_default_socket_path_requires_owned_0700_directory(directory_uid, directory_mode):
    system = Mock()
    system.lstat.side_effect = [
        SimpleNamespace(st_mode=stat.S_IFDIR | 0o1777, st_uid=0),
        SimpleNamespace(st_mode=stat.S_IFDIR | directory_mode, st_uid=directory_uid),
        FileNotFoundError(),
    ]
    directory = Path("/private/tmp/dh")
    if (directory_uid, directory_mode) == (501, 0o700):
        bind_helper_socket(system, owner_uid=501)
        system.bind.assert_called_once_with(Path("/private/tmp/dh/colab_helper.sock"), mode=0o600)
    else:
        with pytest.raises(PermissionError):
            bind_helper_socket(system, owner_uid=501)
        system.bind.assert_not_called()
    system.mkdir_if_absent.assert_called_once_with(directory, mode=0o700)
    assert len(bytes(validate_socket_path())) == 33
    with pytest.raises(ValueError):
        validate_socket_path(Path("/private/tmp/dh") / ("s" * 100))


def test_ipc_frame_uses_big_endian_byte_length_and_closed_schema(schema_validator):
    validator = schema_validator("IPCFrame")
    request = {"jsonrpc": "2.0", "id": "a" * 32, "method": "check_connection", "params": {}}
    frame = encode_frame(request, validate=validator.validate)
    assert int.from_bytes(frame[:4], "big") == len(frame) - 4
    assert decode_frame(frame, validate=validator.validate) == request
    with pytest.raises(ValueError, match="IPC_FRAME_LENGTH_MISMATCH"):
        decode_frame(frame + b" ")
    with pytest.raises(ValueError, match="INVALID_IPC_FRAME_SIZE"):
        decode_frame((65_537).to_bytes(4, "big"))
    with pytest.raises(ValueError, match="INVALID_IPC_FRAME_SIZE"):
        encode_frame({"x": "a" * (65_536 - 7)})
    assert len(encode_frame({"x": "a" * (65_536 - 8)})) == 65_540


def test_partial_reads_and_writes_share_a_five_second_deadline():
    message = {"ok": True}
    frame = encode_frame(message)
    reader = Mock()
    reader.recv.side_effect = [frame[:2], frame[2:4], frame[4:]]
    assert receive_frame(reader, monotonic=lambda: 0) == message
    slow_reader = Mock()
    slow_reader.recv.return_value = frame[:4]
    with pytest.raises(TimeoutError, match="IPC_DEADLINE_EXCEEDED"):
        receive_frame(slow_reader, monotonic=Mock(side_effect=[0, 0, 4, 5]))
    slow_reader.recv.assert_called_once_with(4)
    writer = Mock()
    writer.send.side_effect = [2, len(frame) - 2]
    send_frame(writer, message, monotonic=lambda: 0)
    assert [bytes(item.args[0]) for item in writer.send.call_args_list] == [frame, frame[2:]]


def test_rpc_rejects_unknown_methods_and_rebuilds_provider_errors(schema_validator):
    request = {"jsonrpc": "2.0", "id": "a" * 32, "method": "check_connection", "params": {}}
    handler = Mock(side_effect=RuntimeError("synthetic authorization code and provider URL"))
    validate_response = schema_validator("IpcResponse").validate
    validate_request = schema_validator("IpcRequest").validate
    response = dispatch_rpc(request, {"check_connection": handler},
                            validate_request=validate_request, validate_response=validate_response)
    assert response == safe_rpc_error(request["id"], "OPERATION_FAILED")
    validate_response(response)
    handler.assert_called_once_with({})
    handler.reset_mock()
    request["method"] = "arbitrary_command"
    response = dispatch_rpc(request, {"arbitrary_command": handler},
                            validate_request=validate_request, validate_response=validate_response)
    assert response == safe_rpc_error(request["id"], "INVALID_REQUEST")
    handler.assert_not_called()


def test_approved_run_envelope_has_valid_shape_and_exact_manifest_binding(
    manifest, reference_gate, schema_validator,
):
    gate, signer = reference_gate
    envelope = sign_manifest(manifest, issuer_key_id="synthetic-test-issuer", private_key=signer)
    schema_validator("ApprovedRun").validate(envelope)
    schema_validator("ApprovalSignature").validate({key: value for key, value in envelope.items() if key != "manifest"})
    gate.verify_key.verify(bytes.fromhex(envelope["signature_ed25519_hex"]), _manifest_bytes(manifest))
    manifest["backend"] = "local_supervised"
    assert _digest(_canonical(manifest)) != envelope["manifest_sha256"]
    with pytest.raises(InvalidSignature):
        gate.verify_key.verify(bytes.fromhex(envelope["signature_ed25519_hex"]), _manifest_bytes(manifest))


def test_signed_input_digest_tamper_is_rejected(manifest, schema_validator):
    signer = Ed25519PrivateKey.from_private_bytes(b"\x03" * 32)
    approved = sign_manifest(manifest, issuer_key_id="synthetic", private_key=signer)
    trust = {"synthetic": signer.public_key()}
    verifier = schema_validator("ApprovedRun")
    assert verify_manifest(approved, trusted_keys=trust, validator=verifier, now=NOW) == manifest
    approved["manifest"]["inputs"][0]["sha256"] = "f" * 64
    approved["manifest_sha256"] = _digest(_canonical(approved["manifest"]))
    with pytest.raises(PermissionError, match="INVALID_APPROVAL"):
        verify_manifest(approved, trusted_keys=trust, validator=verifier, now=NOW)


@pytest.mark.parametrize("field", ["message", "raw_provider_error", "authorization_code"])
def test_ipc_error_schema_accepts_only_closed_safe_fields(schema_validator, field):
    validator = schema_validator("IpcResponse")
    error = safe_rpc_error("a" * 32, "NOT_AUTHORIZED")
    validator.validate(error)
    error["error"][field] = "synthetic provider text"
    assert not validator.is_valid(error)


@pytest.mark.parametrize("age,remaining,expected", [
    (59, 0, "0"), (59, None, "Unavailable"), (60, 10, "Stale · Refresh required"),
])
def test_usage_cache_distinguishes_zero_missing_and_stale(schema_validator, age, remaining, expected):
    binding = {"account_id": "synthetic-account", "connection_generation": 3}
    read_usage = Mock(return_value={
        "remaining_cu": remaining, "aggregate_cu_per_hour": None, "active_runtimes": None,
        "headless_eligibility": "unknown",
    })
    cache = UsageCache(read_usage, monotonic=Mock(side_effect=[100, 100 + age]), utc_now=lambda: NOW)
    cache.get(binding)
    snapshot = cache.get(binding)
    schema_validator("UsageSnapshot").validate(snapshot)
    assert usage_display(snapshot) == expected
    read_usage.assert_called_once_with(binding)


def _verify_qc(port, body):
    assert port["name"] == "qc_metrics.json"
    # A=(2, 3), B=(0, 1) are fixed independent reference data.
    if json.loads(body) != {"library_sums": [5, 1], "detected_features": [2, 1]}:
        raise ValueError("QC_ORACLE_MISMATCH")


@pytest.mark.parametrize("mutation,error", [
    (None, None), ("missing", "RESULT_INVENTORY_MISMATCH"),
    ("undeclared", "RESULT_INVENTORY_MISMATCH"), ("wrong_tier", "RESULT_BINDING_MISMATCH"),
    ("tampered_bytes", "RESULT_INTEGRITY_FAILURE"), ("wrong_arithmetic", "QC_ORACLE_MISMATCH"),
    ("wrong_observation", "RESULT_BINDING_MISMATCH"),
])
def test_local_verifier_checks_colab_outputs_and_preserves_tier(manifest, schema_validator, mutation, error):
    metrics = {"library_sums": [4, 1] if mutation == "wrong_arithmetic" else [5, 1], "detected_features": [2, 1]}
    body = _canonical(metrics)
    result = {
        "format": "devbench.compute-result/1", "job_id": manifest["job_id"],
        "manifest_sha256": _digest(_canonical(manifest)), "dispatch_id": "e" * 32,
        "runtime_id": "f" * 32, "execution_tier": "colab",
        "runtime_observation_sha256": _digest(b"synthetic observation"), "exit_code": 0,
        "outputs": [{"name": "qc_metrics.json", "sha256": _digest(body), "size_bytes": len(body)}],
    }
    staged = {"qc_metrics.json": body}
    if mutation == "missing":
        staged.clear()
    elif mutation == "undeclared":
        staged["extra.json"] = b"{}"
    elif mutation == "wrong_tier":
        result.update(execution_tier="local_supervised", runtime_id=None)
    elif mutation == "tampered_bytes":
        staged["qc_metrics.json"] = body.replace(b"5", b"4")
    elif mutation == "wrong_observation":
        result["runtime_observation_sha256"] = _digest(b"other observation")
    validator = schema_validator("ResultManifest")

    def verify():
        return verify_staged_result(
            manifest, result, staged, validator=validator, expected_dispatch_id="e" * 32,
            expected_runtime_id="f" * 32, expected_runtime_observation_sha256=_digest(b"synthetic observation"),
            validate_output=_verify_qc,
        )

    if error is not None:
        with pytest.raises(ValueError, match=error):
            verify()
        return

    verified = verify()
    signer = Ed25519PrivateKey.from_private_bytes(b"\x04" * 32)
    receipt = sign_verification_receipt({
        "format": "devbench.compute-verification/1", "execution_tier": verified["execution_tier"],
        "result_root_sha256": digest_json(verified), "manifest_sha256": verified["manifest_sha256"],
        "outputs": verified["outputs"], "issuer_key_id": "verifier", "checked_at_utc": _utc(NOW),
        "verifier_sha256": _digest(b"validator"), "checks_sha256": _digest(b"checks"),
    }, private_key=signer)
    trust = {"verifier": signer.public_key()}
    validator = schema_validator("VerificationReceipt")
    assert verify_verification_receipt(receipt, verified, trusted_keys=trust, validator=validator)["execution_tier"] == "colab"
    receipt["execution_tier"] = "local_supervised"
    with pytest.raises(PermissionError, match="INVALID_VERIFICATION_RECEIPT"):
        verify_verification_receipt(receipt, verified, trusted_keys=trust, validator=validator)
    assert result["execution_tier"] == "colab"
