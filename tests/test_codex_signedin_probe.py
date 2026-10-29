"""Synthetic preparation only; every external execution entry point stays closed."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from scripts import prepare_codex_signedin_probe as probe


def write(path, value):
    path.write_text(json.dumps(value, sort_keys=True))
    return path


def evidence(tmp_path):
    root = tmp_path / "evidence"
    root.mkdir()
    executable = root / "codex"
    executable.write_text("Public synthetic executable identity; never execute.\n")
    hashed = hashlib.sha256(executable.read_bytes()).hexdigest()
    client = write(root / "client.json", {
        "invoked_path": str(executable), "resolved_binary_path": str(executable),
        "binary_sha256": hashed, "native_binary_path": str(executable), "native_binary_sha256": hashed,
        "npm_launcher_package": {"name": "@openai/codex", "version": "synthetic-test"},
        "npm_native_package": {"name": "@openai/codex", "version": "synthetic-test"},
    })
    auth = write(root / "auth.json", {"auth_mode": "ChatGPT", "exit_code": 0,
        "raw_output_retained": False, "raw_output_printed": False})
    boundary = write(root / "boundary.json", {"capabilities": {
        "packet_only_command_filesystem_verified": False, "command_network_denial_verified": False,
        "ambient_user_config_ignored_for_sandbox_verified": False,
        "ambient_rules_ignored_for_sandbox_verified": False, "native_tool_deny_verified": False}})
    runtime = write(root / "runtime-binding.json", {"fixture": "not runtime authority"})
    return client, auth, boundary, runtime


def test_preparation_retains_hold_and_never_mints_authority(tmp_path, monkeypatch):
    client, auth, boundary, runtime = evidence(tmp_path)
    monkeypatch.setattr(probe, "_installed_runtime", lambda path, workspace: {
        "binding_ref": probe.source_ref(path, workspace), "verified_installed_record_sha256": "1" * 64,
        "source_digest": "2" * 64, "runtime_digest": "3" * 64})
    monkeypatch.setattr(probe, "_engineering_identity", lambda backend, runtime: {
        "route": "codex_chatgpt", "source_digest": runtime["source_digest"], "runtime_digest": runtime["runtime_digest"],
        "backend_digest": "4" * 64, "model": backend.selector.requested_model,
        "platform": "official-codex-cli-local", "client": {"kind": "official_codex_cli", "identity_sha256": "5" * 64},
        "authentication": {"method": "chatgpt_signin", "observation_sha256": "6" * 64},
        "budget_semantics": "generation_jobs", "qualified_boundary": None})
    monkeypatch.setattr(probe, "_stage_allocations", lambda backend: {
        stage: {"jobs": 1, "seconds": 75, "context_bytes": 32768, "output_bytes": 131072}
        for stage in probe.STAGES})
    result = probe.prepare_probe(tmp_path / "packet", workspace=tmp_path, model="explicit-synthetic-model",
        runtime_binding=runtime, client_evidence=client, auth_evidence=auth, boundary_evidence=boundary)
    packet = json.loads(Path(result["packet_path"]).read_text())
    request = json.loads(Path(packet["trial_request_ref"]["path"]).read_text())
    assert packet["status"] == "HOLD"
    assert packet["allow_model_execution"] is False
    assert packet["approval_required"] is True
    assert packet["purpose"] == "workflow_generation"
    assert request["allocation"]["numeric"] is None
    assert request["allocation"]["generation"]["jobs"] == 3
    assert request["approvals"] == {"workflow_generation": None, "trial_execution": None}
    assert request["dataset"]["classification"] == "synthetic"
    assert request["requirements"] == []
    assert request["engineering"]["model"] == "explicit-synthetic-model"
    assert request["engineering"]["hosted_trace_required"] is True
    assert request["engineering"]["authentication"]["observation_sha256"] == "6" * 64
    assert packet["budget"]["policy"]["max_total_seconds"] == 300
    assert sum(row["seconds"] for row in packet["budget"]["stage_allocations"].values()) == 225
    assert packet["budget"]["observability"]["internal_provider_requests"] == "UNKNOWN_NOT_CAPPED"
    assert packet["budget"]["observability"]["spend"] == "NOT_CAPPED"
    assert packet["effects"] == {"model_jobs_launched": 0, "numeric_runs": 0, "authority_grants_created": 0}
    assert "native_boundary_unqualified" in packet["blockers"]
    assert "fresh_authentication_required_at_launch" in packet["blockers"]
    assert len(packet["stage_material_previews"]) == 3
    assert all(row["preview_only"] is True for row in packet["stage_material_previews"])
    assert all(row["prompt_ref"]["size_bytes"] <= 32768 for row in packet["stage_material_previews"])
    assert "LangSmith" in packet["qualification_requirements"]["hosted_delivery"]
    assert "without rerunning" in packet["qualification_requirements"]["independent_readback"]
    assert not list((tmp_path / "packet").glob("*grant*"))
    from devbench.analysis.methods import MethodService
    methods = MethodService(tmp_path / "packet" / "service", readonly=True)
    assert methods.list_records("run") == []
    assert methods.list_records("step") == []


@pytest.mark.parametrize("model", [None, "", "has whitespace", "$(unsafe)"])
def test_model_is_explicit_and_bounded_before_preparation(tmp_path, model):
    paths = evidence(tmp_path)
    with pytest.raises(ValueError, match="explicit.*model"):
        probe.prepare_probe(tmp_path / "packet", workspace=tmp_path, model=model,
            runtime_binding=paths[3], client_evidence=paths[0], auth_evidence=paths[1], boundary_evidence=paths[2])
    assert not (tmp_path / "packet").exists()


def test_source_ref_rejects_symlinks_and_external_paths(tmp_path):
    item = write(tmp_path / "source.json", {"synthetic": True})
    linked = tmp_path / "link.json"
    linked.symlink_to(item)
    with pytest.raises(ValueError, match="ordinary|symlink"):
        probe.source_ref(linked, tmp_path)
    with pytest.raises(ValueError, match="workspace"):
        probe.source_ref(item, tmp_path / "different")


def test_api_auth_and_changed_binary_are_rejected_before_output(tmp_path, monkeypatch):
    client, auth, boundary, runtime = evidence(tmp_path)
    monkeypatch.setattr(probe, "_installed_runtime", lambda *args: {})
    write(auth, {"auth_mode": "API key", "exit_code": 0, "raw_output_retained": False, "raw_output_printed": False})
    with pytest.raises(PermissionError, match="ChatGPT"):
        probe.prepare_probe(tmp_path / "packet", workspace=tmp_path, model="explicit-model",
            runtime_binding=runtime, client_evidence=client, auth_evidence=auth, boundary_evidence=boundary)
    assert not (tmp_path / "packet").exists()
    write(auth, {"auth_mode": "ChatGPT", "exit_code": 0, "raw_output_retained": False, "raw_output_printed": False})
    changed = json.loads(client.read_text())
    changed["native_binary_sha256"] = "0" * 64
    write(client, changed)
    with pytest.raises(PermissionError, match="client.*changed"):
        probe.prepare_probe(tmp_path / "packet", workspace=tmp_path, model="explicit-model",
            runtime_binding=runtime, client_evidence=client, auth_evidence=auth, boundary_evidence=boundary)
    assert not (tmp_path / "packet").exists()


def test_report_cannot_direct_binary_hashing_to_a_credential_path(tmp_path, monkeypatch):
    client, auth, boundary, _ = evidence(tmp_path)
    changed = json.loads(client.read_text())
    forbidden = Path("/not-a-real-home/.codex/auth.json")
    changed["native_binary_path"] = str(forbidden)
    write(client, changed)
    hashed = []
    original = probe._sha
    def guard(path, *args):
        hashed.append(Path(path))
        assert Path(path) != forbidden, "Report-supplied credential path reached file hashing"
        return original(path, *args)
    monkeypatch.setattr(probe, "_sha", guard)
    with pytest.raises(PermissionError, match="public Codex binaries"):
        probe._client_evidence(client, auth, boundary, tmp_path)
    assert forbidden not in hashed


def test_runtime_binding_requires_actual_installed_prefix_and_closed_fields(tmp_path):
    binding = write(tmp_path / "runtime.json", {"runtime_prefix": str(tmp_path), "unexpected": True})
    with pytest.raises(ValueError, match="runtime binding"):
        probe._installed_runtime(binding, tmp_path)


def test_current_unknown_auth_cannot_emit_a_concrete_packet(tmp_path, monkeypatch):
    client, auth, boundary, runtime = evidence(tmp_path)
    monkeypatch.setattr(probe, "_installed_runtime", lambda path, workspace: {})
    monkeypatch.setattr(probe, "_engineering_identity", lambda *args: {
        "authentication": {"method": "unknown", "observation_sha256": "6" * 64}})
    with pytest.raises(PermissionError, match="Current.*authentication unavailable"):
        probe.prepare_probe(tmp_path / "packet", workspace=tmp_path, model="explicit-model",
            runtime_binding=runtime, client_evidence=client, auth_evidence=auth, boundary_evidence=boundary)
    assert not (tmp_path / "packet" / "probe-packet.HOLD.json").exists()


def test_outputs_are_fresh_and_cannot_overlap_evidence(tmp_path, monkeypatch):
    client, auth, boundary, runtime = evidence(tmp_path)
    with pytest.raises(ValueError, match="fresh|overlap"):
        probe.prepare_probe(tmp_path / "evidence", workspace=tmp_path, model="explicit-model",
            runtime_binding=runtime, client_evidence=client, auth_evidence=auth, boundary_evidence=boundary)
