"""Unscored accounting and phase-specific handshake checks; no live models."""
import json
import os
from pathlib import Path
import socket
import tempfile
import time
from types import SimpleNamespace

import pytest

from devbench.study.preflight_only import preflight_accounting


def test_probe_accounting_keeps_unknown_cost_and_partial_dispatch(tmp_path):
    first, second, third = (tmp_path / name for name in ("first", "second", "not_started"))
    first.mkdir(); second.mkdir()
    (first / "evidence.json").write_text(json.dumps({"inference_dispatch_attempted": True,
        "billing": {"tokens": 17, "cost_usd": None}, "duration_sec": 2.5}))
    (second / "dispatch_checkpoint.json").write_text('{"scored":false}')
    result = preflight_accounting([first, second, third])
    assert result["confirmed_provider_session_attempts"] == 2
    assert result["tokens"] is None and result["cost_usd"] is None
    assert result["duration_sec"] is None


def test_unstarted_and_unknown_dispatch_are_distinct(tmp_path):
    unknown = tmp_path / "unknown"
    unknown.mkdir()
    result = preflight_accounting([tmp_path / "not_started", unknown])
    assert result["confirmed_provider_session_attempts"] == 0
    assert result["unknown_dispatch_slots"] == 1 and result["tokens"] is None


def test_interrupted_evidence_write_is_unknown_instead_of_breaking_finalization(tmp_path):
    (tmp_path / "evidence.json").write_text('{"inference_dispatch_attempted":')
    result = preflight_accounting([tmp_path])
    assert result["invalid_evidence_slots"] == 1 and result["unknown_dispatch_slots"] == 1
    assert result["tokens"] is None and result["cost_usd"] is None


@pytest.mark.skipif(os.environ.get("DEVBENCH_RUN_LOCAL_FIXTURES") != "1", reason="Local socket fixture requires opt-in")
def test_handshake_after_analysis_is_rejected_without_inflating_probe_count():
    from devbench.study.transport import BrokerTransport
    class Broker:
        allowed_tools = frozenset()
        started = None
        policy = SimpleNamespace(timeout_sec=30)
        def execute(self, *args):
            pytest.fail("Handshake must never invoke scientific tools")
    root = Path(__file__).resolve().parents[1] / "tmp"
    root.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=root, prefix="hs-") as directory:
        broker = Broker()
        transport = BrokerTransport(broker, Path(directory) / "s.sock", allow_data_execution=True)
        def request():
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                client.settimeout(2)
                client.connect(str(transport.path))
                client.sendall(json.dumps({"tool":"__devbench_preflight__", "arguments":{},
                                          "capability":transport.capability_file.read_text()}).encode()+b"\n")
                return json.loads(client.recv(4096))
        try:
            assert request()["status"] == "preflight_connection_verified"
            broker.started = time.monotonic()
            assert request()["status"] == "protocol_error"
        finally:
            result = transport.close()
        assert result["preflight_handshakes"] == 1
        assert result["transport_error_count"] == 1 and result["transport_stopped"]
