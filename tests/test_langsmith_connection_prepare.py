"""Tiny local arithmetic preparation never grants hosted execution authority."""
from datetime import datetime, timezone
import importlib
import json
from pathlib import Path
import socket
import stat
import subprocess
from uuid import UUID

import pytest

from devbench.study.contracts import canonical_json_bytes
from devbench.study.langsmith_outbox import digest


@pytest.fixture
def prepare_module(monkeypatch, tmp_path):
    try:
        module = importlib.import_module("devbench.study.rna_connection_prepare")
    except ModuleNotFoundError as error:
        if error.name != "devbench.study.rna_connection_prepare":
            raise
        return None, None, None
    contract = importlib.import_module("devbench.study.rna_hosted_connection_contract")
    identity = dict(source_digest="a" * 64, runtime_digest="b" * 64,
        policy_digest="c" * 64,
        criteria_digest=digest(canonical_json_bytes(contract.CONNECTION_CRITERIA)),
        fixture_manifest_digest=digest(canonical_json_bytes(contract.CONNECTION_FIXTURE)))
    monkeypatch.setattr(module, "connection_identity", lambda: dict(identity))
    monkeypatch.setattr(module, "ASSIGNED_WORKSPACE", tmp_path)
    return module, contract, identity


def _read(directory, name):
    return json.loads((directory / name).read_text())


def test_preparation_retains_observed_sum_disabled_packet_and_exact_operations(tmp_path, prepare_module):
    module, contract, identity = prepare_module
    assert module is not None, "Tiny preparation API is missing"
    output = tmp_path / "tiny-prepared"
    before = datetime.now(timezone.utc)
    result = module.prepare_connection_test(output_path=output)
    after = datetime.now(timezone.utc)
    assert result == output / "review.json"
    packet = _read(output, "packet.json")
    observation = _read(output, "local_observation.json")
    review = _read(output, "review.json")
    assert observation["values"] == [1, 2, 3]
    assert observation["result"] == 6
    assert observation["requirement_source"] == "assistant_selected_synthetic_fixture"
    assert before <= datetime.fromisoformat(observation["start_time"].replace("Z", "+00:00")) <= after
    assert before <= datetime.fromisoformat(observation["end_time"].replace("Z", "+00:00")) <= after
    assert observation["start_time"] <= observation["end_time"]
    assert observation["timing_basis"] == "observed_local_utc"
    assert review["status"] == "prepared_unapproved"
    assert review["packet_sha256"] == digest((output / "packet.json").read_bytes())
    assert review["source_runtime_review"] is None
    assert review["operator_approvals"] == {}
    assert review["authorize_upload"] is False
    assert review["authorize_remote_read"] is False
    assert review["remote_execution"] == "not_attempted"
    assert review["scientific_assessment"] == "not_applicable_synthetic_arithmetic"
    assert review["installed_identity"] == identity
    assert packet["target"] == contract.TARGET
    assert packet["limits"] == contract.CONNECTION_CAPS
    assert packet["session"]["authorize_upload"] is False
    assert not (output / "remote-evidence").exists()
    assert packet["evidence_path"] == str(output / "remote-evidence")
    assert len(packet["operations"]) == 2
    root = packet["operations"][0]["payload"]["id"]
    assert str(UUID(root)) == root
    for revision, operation in enumerate(packet["operations"]):
        envelope, payload = operation["operation"], operation["payload"]
        assert envelope["operation"] == ("start", "finish")[revision]
        assert envelope["revision"] == revision
        assert payload["id"] == payload["trace_id"] == root
        assert "parent_run_id" not in payload
        assert payload["inputs"] == payload["outputs"] == {}
        assert payload["start_time"] == observation["start_time"]
        if revision:
            assert payload["end_time"] == observation["end_time"]
        else:
            assert "end_time" not in payload
        module.validate_live_run(payload, envelope["operation"])
        module.validate_remote_proof(payload)
    assert review["operation_ids"] == [row["operation"]["operation_id"] for row in packet["operations"]]
    assert review["proposed_routes"] == [
        {"method": "GET", "path": "/api/v1/sessions/" + contract.TARGET["project_id"] + "?include_stats=false"},
        {"method": "POST", "path": "/api/v1/runs"},
        {"method": "PATCH", "path": "/api/v1/runs/" + root},
        {"method": "GET", "path": "/api/v1/runs/" + root}]
    assert review["expected_evidence"]["proof_revisions"] == [0, 1]
    assert review["expected_evidence"]["span_count"] == 1
    assert review["expected_evidence"]["terminal_source_status"] == "completed"
    assert review["expected_evidence"]["actual_remote_status"] == "unverified"


def test_preparation_never_uses_network_or_credential_environment(tmp_path, prepare_module, monkeypatch):
    module, _, _ = prepare_module
    assert module is not None, "Tiny preparation API is missing"
    monkeypatch.setenv("LANGSMITH_API_KEY", "UNREAD_PREPARE_SECRET_CANARY")
    monkeypatch.setenv("LANGCHAIN_API_KEY", "UNREAD_PREPARE_SECRET_CANARY")
    def denied(*args, **kwargs):
        pytest.fail("Preparation attempted a network operation")
    monkeypatch.setattr(socket, "create_connection", denied)
    monkeypatch.setattr(socket.socket, "connect", denied)
    output = tmp_path / "prepared"
    module.prepare_connection_test(output_path=output)
    for path in output.iterdir():
        assert "UNREAD_PREPARE_SECRET_CANARY" not in path.read_text()
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(output.stat().st_mode) == 0o700


def test_preparation_refuses_existing_or_outside_output(tmp_path, prepare_module):
    module, _, _ = prepare_module
    assert module is not None, "Tiny preparation API is missing"
    output = tmp_path / "prepared"
    output.mkdir()
    sentinel = output / "keep"
    sentinel.write_text("retained")
    with pytest.raises((ValueError, FileExistsError)):
        module.prepare_connection_test(output_path=output)
    assert sentinel.read_text() == "retained"
    with pytest.raises(ValueError):
        module.prepare_connection_test(output_path=tmp_path.parent / "outside")


def test_preparation_rejects_symlink_parent(tmp_path, prepare_module):
    module, _, _ = prepare_module
    assert module is not None, "Tiny preparation API is missing"
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)
    with pytest.raises(ValueError):
        module.prepare_connection_test(output_path=link / "prepared")
    assert not (real / "prepared").exists()


def test_prepare_cli_exposes_only_preparation(prepare_module, tmp_path):
    module, _, _ = prepare_module
    assert module is not None, "Tiny preparation API is missing"
    parser = module.build_parser()
    options = {option for action in parser._actions for option in action.option_strings}
    assert options == {"-h", "--help", "--output"}
    for forbidden in ("--approve", "--execute", "--api-key", "--key-selector"):
        with pytest.raises(SystemExit):
            parser.parse_args(["--output", str(tmp_path / "prepared"), forbidden])


def test_real_installed_identity_preparation_without_child_secret_or_http(tmp_path, monkeypatch):
    """Positive integration uses the ordinary installed wheel, not fixture identities."""
    try:
        module = importlib.import_module("devbench.study.rna_connection_prepare")
    except ModuleNotFoundError as error:
        if error.name != "devbench.study.rna_connection_prepare":
            raise
        module = None
    assert module is not None, "Tiny preparation API is missing"
    from devbench.study import rna_hosted_exporter
    def denied(*args, **kwargs):
        pytest.fail("Preparation attempted child, credential resolution, or network")
    monkeypatch.setattr(subprocess, "Popen", denied)
    monkeypatch.setattr(subprocess, "run", denied)
    monkeypatch.setattr(socket, "create_connection", denied)
    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(rna_hosted_exporter, "_resolve_keychain_credential", denied)
    identity = module.connection_identity()
    assert Path(module.__file__).is_relative_to(Path(__import__("sys").prefix))
    assert tmp_path.is_relative_to(module.ASSIGNED_WORKSPACE)
    output = tmp_path / "real-installed-prepared"
    module.prepare_connection_test(output_path=output)
    review = _read(output, "review.json")
    packet = _read(output, "packet.json")
    assert review["installed_identity"] == identity
    assert all(packet["binding"][key] == value for key, value in identity.items())
    assert review["remote_execution"] == "not_attempted"
    assert review["operator_approvals"] == {}
    assert review["source_runtime_review"] is None
    assert review["authorize_upload"] is False
    assert review["authorize_remote_read"] is False
    assert _read(output, "local_observation.json")["result"] == 6
