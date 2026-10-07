"""Coordinator protection and evidence identity regressions; no model calls."""
from pathlib import Path
import json
import os
import socket
import tempfile
from types import SimpleNamespace

import pytest


def test_private_source_roots_cover_generators_controls_and_controlled_engine():
    from devbench.study import execution, public_execution
    for module in (execution, public_execution):
        assert {"benchmarks", "devharness_controlled"} <= set(module._SOURCE_PACKAGES)


def test_evidence_key_uses_explicit_time_without_alternate_defaults():
    from devbench.study.evidence_keys import evidence_key
    from devbench.study.contracts import content_sha256
    arguments = {"counts_path": "/opt/input/counts.zarr", "metadata_path": "/opt/input/metadata.csv", "time_col": "collection_age"}
    expected = content_sha256({"input": "a"*64, **arguments})
    assert evidence_key("quality", "a"*64, arguments) == expected
    for bad in (None, "", False):
        with pytest.raises(ValueError, match="time_col"):
            evidence_key("quality", "a"*64, {**arguments, "time_col": bad})
    with pytest.raises(ValueError, match="time_col"):
        evidence_key("quality", "a"*64, {key:value for key,value in arguments.items() if key != "time_col"})


def test_broker_uses_its_configured_time_when_expanding_missing_argument():
    from devbench.study.broker import ToolBroker, BrokerPolicy
    from devbench.study.evidence_keys import evidence_key
    broker = object.__new__(ToolBroker)
    broker.policy = BrokerPolicy(condition="enforced", input_sha256="a"*64, implementation_sha256="b"*64,
        population_inference_allowed=False, measurement_type="continuous", gene_ids=("g1",), time_col="collection_age", timeout_sec=30)
    arguments = {"counts_path": "/opt/counts.zarr", "metadata_path": "/opt/metadata.csv", "formula_str": "1 + numeric_time"}
    complete = {**arguments, "time_col": "collection_age"}
    assert broker._quality_key(arguments) == evidence_key("quality", "a"*64, complete)
    assert broker._design_key(arguments) == evidence_key("design", "a"*64, complete)


def test_all_live_commands_require_explicit_preflight_authorization_before_reads():
    from devbench.study.cli import build_parser, _run_command
    for command, ordinary_flag, workspace_flag in (("run", "--authorize-data-execution", "--workspace-parent"),
            ("biology-run", "--authorize-data-execution", "--workspace-root"),
            ("public-run", "--authorize-live-participants", "--workspace-root")):
        args = build_parser().parse_args([command, ordinary_flag, "--plan", "/missing", "--bundles", "/missing",
                                         "--output", "/missing", workspace_flag, "/missing"])
        with pytest.raises(PermissionError, match="preflight"):
            _run_command(args)


@pytest.mark.skipif(os.environ.get("DEVBENCH_RUN_LOCAL_FIXTURES") != "1", reason="Local socket fixture requires opt-in")
def test_transport_probe_authenticates_without_domain_tool_or_protocol_error():
    from devbench.study.transport import BrokerTransport
    class NoAnalysisBroker:
        allowed_tools = frozenset()
        started = None  # Preflight handshake must work before analysis activation.
        policy = SimpleNamespace(timeout_sec=30)
        def execute(self, *args):
            pytest.fail("A transport preflight must never execute an analysis")
    root = Path(__file__).resolve().parents[1] / "tmp"
    root.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=root, prefix="pt-") as directory:
        transport = BrokerTransport(NoAnalysisBroker(), Path(directory) / "s.sock", allow_data_execution=True)
        try:
            capability = transport.capability_file.read_text()
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                client.settimeout(2)
                client.connect(str(transport.path))
                client.sendall(json.dumps({"capability": capability, "tool": "__devbench_preflight__", "arguments": {}}).encode()+b"\n")
                response = json.loads(client.recv(4096))
            assert response == {"status": "preflight_connection_verified"}
        finally:
            result = transport.close()
        assert result["preflight_handshakes"] == 1
        assert result["transport_error_count"] == 0 and result["transport_stopped"]
