"""Offline Workbench IPC and usage contracts with injected system adapters."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import re
import stat
import struct
from threading import Lock

from devbench.engine.provenance import canonical_json


SOCKET_PATH = Path("/private/tmp/dh/colab_helper.sock")
MAX_SOCKET_PATH_BYTES = 103
MAX_FRAME_BYTES = 65_536
IPC_DEADLINE_SECONDS = 5
USAGE_TTL_SECONDS = 60
USAGE_FORMAT = "devbench.colab-usage/1"
STALE_USAGE_LABEL = "Stale · Refresh required"
IPC_METHODS = frozenset({
    "check_connection", "get_usage", "prepare_runtime", "transfer_inputs",
    "monitor_job", "collect_outputs", "stop_runtime",
})
RPC_ERROR_KINDS = frozenset({
    "INVALID_REQUEST", "NOT_AUTHORIZED", "ACCOUNT_MISMATCH", "STALE_GENERATION",
    "USAGE_UNAVAILABLE", "HEADLESS_INELIGIBLE", "HARD_CU_CAP_UNAVAILABLE",
    "HARD_RUNTIME_DEADLINE_UNAVAILABLE", "DIRECT_UPLOAD_LIMIT_EXCEEDED",
    "RUNTIME_UNAVAILABLE", "INPUT_INTEGRITY_FAILURE", "OUTPUT_VERIFICATION_FAILED",
    "CLEANUP_UNCONFIRMED", "OPERATION_FAILED",
})

_PREFIX = struct.Struct(">I")
_RPC_ID = re.compile(r"[0-9a-f]{32}")
_ACCOUNT_ID = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_.:-]{0,127}")


def validate_socket_path(path=SOCKET_PATH) -> Path:
    """Require the exact socket path and room for Darwin's terminating NUL."""
    raw = os.fspath(path)
    if not isinstance(raw, str) or "\0" in raw:
        raise ValueError("INVALID_SOCKET_PATH")
    if len(raw.encode("utf-8")) > MAX_SOCKET_PATH_BYTES:
        raise ValueError("SOCKET_PATH_TOO_LONG")
    if raw != str(SOCKET_PATH):
        raise ValueError("INVALID_SOCKET_PATH")
    return SOCKET_PATH


def bind_helper_socket(system, *, owner_uid: int, path=SOCKET_PATH):
    """Check mocked filesystem metadata before binding an unused endpoint.

    The system adapter supplies no-follow operations under its exclusive
    startup lock. This contract never unlinks an endpoint or opens a socket
    without an explicitly injected adapter.
    """
    if type(owner_uid) is not int or owner_uid < 0:
        raise ValueError("INVALID_SOCKET_OWNER")
    endpoint = validate_socket_path(path)
    directory = endpoint.parent
    temp = system.lstat(directory.parent)
    if not (stat.S_ISDIR(temp.st_mode) and temp.st_uid == 0 and temp.st_mode & stat.S_ISVTX):
        raise PermissionError("UNSAFE_SOCKET_DIRECTORY")
    system.mkdir_if_absent(directory, mode=0o700)
    info = system.lstat(directory)
    if not (stat.S_ISDIR(info.st_mode) and info.st_uid == owner_uid and stat.S_IMODE(info.st_mode) == 0o700):
        raise PermissionError("UNSAFE_SOCKET_DIRECTORY")
    try:
        system.lstat(endpoint)
    except FileNotFoundError:
        return system.bind(endpoint, mode=0o600)
    raise FileExistsError("SOCKET_ENDPOINT_EXISTS")


def _frame_size(header: bytes) -> int:
    size, = _PREFIX.unpack(header)
    if not 1 <= size <= MAX_FRAME_BYTES:
        raise ValueError("INVALID_IPC_FRAME_SIZE")
    return size


def _strict_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("DUPLICATE_IPC_KEY")
        result[key] = value
    return result


def _decode_payload(payload: bytes, validate) -> dict:
    try:
        message = json.loads(payload.decode("utf-8"), object_pairs_hook=_strict_object)
        if type(message) is not dict:
            raise ValueError("IPC_OBJECT_REQUIRED")
        canonical_json(message)  # Also rejects nonfinite numbers and invalid Unicode scalars.
    except (ValueError, RecursionError):
        raise ValueError("INVALID_IPC_JSON") from None
    if validate is not None:
        validate(message)
    return message


def encode_frame(message: dict, *, validate=None) -> bytes:
    """Encode one strict JSON object. A supplied validator checks its wire schema."""
    if type(message) is not dict:
        raise ValueError("IPC_OBJECT_REQUIRED")
    if validate is not None:
        validate(message)
    payload = canonical_json(message)
    if not 1 <= len(payload) <= MAX_FRAME_BYTES:
        raise ValueError("INVALID_IPC_FRAME_SIZE")
    return _PREFIX.pack(len(payload)) + payload


def decode_frame(frame: bytes, *, validate=None) -> dict:
    """Decode one complete frame and reject trailing bytes."""
    if len(frame) < _PREFIX.size:
        raise ValueError("TRUNCATED_IPC_FRAME")
    size = _frame_size(frame[:_PREFIX.size])
    if len(frame) != _PREFIX.size + size:
        raise ValueError("IPC_FRAME_LENGTH_MISMATCH")
    return _decode_payload(frame[_PREFIX.size:], validate)


def _remaining(deadline, monotonic) -> float:
    remaining = deadline - monotonic()
    if not math.isfinite(remaining) or remaining <= 0:
        raise TimeoutError("IPC_DEADLINE_EXCEEDED")
    return remaining


def _read_exact(stream, size, deadline, monotonic) -> bytes:
    received = bytearray()
    while len(received) < size:
        stream.settimeout(_remaining(deadline, monotonic))
        chunk = stream.recv(size - len(received))
        _remaining(deadline, monotonic)
        if not chunk:
            raise ValueError("TRUNCATED_IPC_FRAME")
        received.extend(chunk)
    if len(received) != size:
        raise ValueError("IPC_FRAME_LENGTH_MISMATCH")
    return bytes(received)


def receive_frame(stream, *, monotonic, validate=None) -> dict:
    """Read header and payload under one five-second monotonic deadline."""
    deadline = monotonic() + IPC_DEADLINE_SECONDS
    header = _read_exact(stream, _PREFIX.size, deadline, monotonic)
    payload = _read_exact(stream, _frame_size(header), deadline, monotonic)
    return _decode_payload(payload, validate)


def send_frame(stream, message: dict, *, monotonic, validate=None) -> None:
    """Write a frame under one deadline even when the stream writes partially."""
    frame = memoryview(encode_frame(message, validate=validate))
    deadline = monotonic() + IPC_DEADLINE_SECONDS
    while frame:
        stream.settimeout(_remaining(deadline, monotonic))
        sent = stream.send(frame)
        _remaining(deadline, monotonic)
        if type(sent) is not int or not 0 < sent <= len(frame):
            raise ValueError("IPC_SEND_FAILED")
        frame = frame[sent:]


def safe_rpc_error(request_id, kind: str) -> dict:
    """Construct an RPC error from fixed values without copying provider text."""
    if not isinstance(request_id, str) or not _RPC_ID.fullmatch(request_id):
        request_id = None
    if not isinstance(kind, str) or kind not in RPC_ERROR_KINDS:
        kind = "OPERATION_FAILED"
    code, message = (-32600, "Invalid Request") if kind == "INVALID_REQUEST" else (
        -32000, "Workbench operation failed",
    )
    return {
        "jsonrpc": "2.0", "id": request_id,
        "error": {"code": code, "message": message, "data": {"kind": kind}},
    }


def dispatch_rpc(request, handlers: Mapping, *, validate_request, validate_response) -> dict:
    """Route a decoded request after the caller has authenticated the peer."""
    request_id = request.get("id") if type(request) is dict else None
    try:
        validate_request(request)
        method = request["method"]
        if method not in IPC_METHODS:
            return safe_rpc_error(request_id, "INVALID_REQUEST")
    except Exception:
        return safe_rpc_error(request_id, "INVALID_REQUEST")

    try:
        result = {"method": method, "value": handlers[method](request["params"])}
        response = {"jsonrpc": "2.0", "id": request_id, "result": result}
        validate_response(response)
    except Exception as error:
        kind = error.args[0] if len(error.args) == 1 else "OPERATION_FAILED"
        return safe_rpc_error(request_id, kind)
    return response


def _binding_key(account_binding: Mapping) -> tuple[str, int]:
    if not isinstance(account_binding, Mapping) or set(account_binding) != {"account_id", "connection_generation"}:
        raise ValueError("INVALID_ACCOUNT_BINDING")
    account_id = account_binding["account_id"]
    generation = account_binding["connection_generation"]
    if not isinstance(account_id, str) or not _ACCOUNT_ID.fullmatch(account_id):
        raise ValueError("INVALID_ACCOUNT_BINDING")
    if type(generation) is not int or not 0 <= generation <= 2**53 - 1:
        raise ValueError("INVALID_ACCOUNT_BINDING")
    return account_id, generation


def _usage_number(value):
    if type(value) not in (int, float) or value < 0:
        return None
    if type(value) is float and not math.isfinite(value):
        return None
    return value


def _usage_snapshot(binding, raw, checked_at) -> dict:
    if not isinstance(raw, Mapping):
        raise ValueError("INVALID_USAGE_SNAPSHOT")
    if not isinstance(checked_at, datetime) or checked_at.utcoffset() is None:
        raise ValueError("INVALID_USAGE_TIMESTAMP")
    account_id, generation = binding
    if raw.get("account_id", account_id) != account_id or raw.get("connection_generation", generation) != generation:
        raise ValueError("ACCOUNT_MISMATCH")
    runtimes = raw.get("active_runtimes")
    eligibility = raw.get("headless_eligibility")
    return {
        "format": USAGE_FORMAT,
        "account_id": account_id,
        "checked_at_utc": checked_at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "connection_generation": generation,
        "remaining_cu": _usage_number(raw.get("remaining_cu")),
        "aggregate_cu_per_hour": _usage_number(raw.get("aggregate_cu_per_hour")),
        "active_runtimes": runtimes if type(runtimes) is int and 0 <= runtimes <= 2**53 - 1 else None,
        "is_stale": False,
        "headless_eligibility": eligibility if eligibility in ("verified", "denied", "unknown") else "unknown",
    }


class UsageCache:
    """Account-bound read-only cache with no runtime or authentication method.

    The caller supplies the current verified binding and an allowlisted read
    adapter. It must invalidate the cache when that binding is revoked.
    """

    def __init__(self, read_usage, *, monotonic, utc_now):
        self._read_usage = read_usage
        self._monotonic = monotonic
        self._utc_now = utc_now
        self._lock = Lock()
        self._epoch = 0
        self._cache = None

    def invalidate(self) -> None:
        with self._lock:
            self._epoch += 1
            self._cache = None

    def get(self, account_binding: Mapping, *, refresh: bool = False) -> dict:
        if type(refresh) is not bool:
            raise ValueError("INVALID_USAGE_REFRESH")
        binding = _binding_key(account_binding)
        with self._lock:
            prior = self._cache
            if prior is not None and prior[0] != binding:
                prior = None
            if prior is not None and not refresh:
                _, checked, snapshot = prior
                return {**snapshot, "is_stale": snapshot["is_stale"] or self._monotonic() - checked >= USAGE_TTL_SECONDS}
            self._epoch += 1
            epoch = self._epoch

        try:
            account_id, generation = binding
            raw = self._read_usage({"account_id": account_id, "connection_generation": generation})
            snapshot = _usage_snapshot(binding, raw, self._utc_now())
            checked = self._monotonic()
        except Exception:
            if prior is None:
                raise LookupError("USAGE_UNAVAILABLE") from None
            _, checked, snapshot = prior
            snapshot = {**snapshot, "is_stale": True}

        with self._lock:
            if self._epoch != epoch:
                raise LookupError("STALE_GENERATION")
            self._cache = binding, checked, snapshot
            return dict(snapshot)


def usage_display(snapshot: Mapping) -> str:
    if snapshot["is_stale"]:
        return STALE_USAGE_LABEL
    remaining = snapshot["remaining_cu"]
    if remaining is None:
        return "Unavailable"
    return "0" if remaining == 0 else str(remaining)
