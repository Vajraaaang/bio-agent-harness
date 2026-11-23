"""Small participant-side DevHarness client; no coordinator imports.

The executing coordinator stages this file as devharness_client.py. Results
come from a per-run Unix socket. Local trace files are never proof of tool use.
Importing this module neither contacts a service nor reads expression data.
"""
import json
import os
from pathlib import Path
import socket

MAX_REQUEST_BYTES = 256_000
MAX_RESPONSE_BYTES = 8_100_000


def call(tool, *, timeout_sec=600, **arguments):
    """Return {receipt_id, status, result, error}; inspect status before use."""
    configured = os.environ.get("DEVHARNESS_TOOL_SOCKET")
    if not configured or not Path(configured).is_absolute():
        raise RuntimeError("No absolute coordinator tool socket is configured")
    capability_file = os.environ.get("DEVHARNESS_TOOL_CAPABILITY_FILE")
    if not capability_file or not Path(capability_file).is_absolute():
        raise RuntimeError("No coordinator capability file is configured")
    with Path(capability_file).resolve().open("r") as handle:
        capability = handle.read(129)
    if not 32 <= len(capability) <= 128:
        raise RuntimeError("Invalid coordinator capability")
    if isinstance(timeout_sec, bool) or not 0 < timeout_sec <= 86_400:
        raise ValueError("Invalid tool transport timeout")
    payload = json.dumps({"tool": tool, "arguments": arguments, "capability": capability}, allow_nan=False).encode() + b"\n"
    if len(payload) > MAX_REQUEST_BYTES:
        raise ValueError("Tool request exceeds its size limit")
    chunks, size = [], 0
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(timeout_sec)
        connection.connect(str(Path(configured).resolve()))
        connection.sendall(payload)
        while True:
            block = connection.recv(min(65_536, MAX_RESPONSE_BYTES + 1 - size))
            if not block:
                break
            chunks.append(block)
            size += len(block)
            if size > MAX_RESPONSE_BYTES:
                raise ValueError("Tool response exceeds its size limit")
            if b"\n" in block:
                break
    response = b"".join(chunks)
    if not response.endswith(b"\n"):
        raise RuntimeError("Incomplete coordinator response")
    value = json.loads(response)
    if not isinstance(value, dict) or value.get("status") not in {"succeeded", "failed", "protocol_error"}:
        raise RuntimeError("Invalid coordinator response")
    return value
