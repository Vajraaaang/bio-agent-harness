"""Synthetic custody tests; none qualifies or launches a signed-in client."""
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path

import pytest

from devharness_core.provenance import canonical_json
from devbench.analysis import codex_isolated_binding as binding


NOW = datetime(2026, 10, 21, 12, tzinfo=timezone.utc)


def manifest():
    return {"format": binding._FORMAT, "scope": binding._SCOPE,
        "accepted_at": "2026-10-21T00:00:00Z", "expires_at": "2099-10-22T00:00:00Z",
        "model": "gpt-6.1-sol", "bindings": {key: "a" * 64 for key in binding._BINDINGS},
        "evidence": {key: {"file": key + ".evidence.json", "sha256": hashlib.sha256(b"{}").hexdigest(),
                           "bytes": 2} for key in binding._EVIDENCE}}


def root_fixture(tmp_path, monkeypatch, value=None):
    workspace = tmp_path / "workspace"
    root = workspace / ".devbench-native-control"
    root.mkdir(parents=True, mode=0o700)
    value = manifest() if value is None else value
    for descriptor in value["evidence"].values():
        target = root / descriptor["file"]
        target.write_bytes(b"{}")
        target.chmod(0o600)
    body = canonical_json(value)
    target = root / "acceptance.json"
    target.write_bytes(body)
    target.chmod(0o600)
    monkeypatch.setattr(binding, "_current_runtime", lambda: ("a" * 64, "a" * 64))
    return binding.TrustedIsolatedConfiguration(root, workspace, hashlib.sha256(body).hexdigest())


def test_synthetic_plan_cannot_admit_or_execute(tmp_path, monkeypatch):
    config = root_fixture(tmp_path, monkeypatch)
    plan = binding.prepare_isolated_plan(config)
    assert type(plan) is binding.NonAdmittingIsolatedPlan
    assert plan.model == "gpt-6.1-sol"
    assert not hasattr(plan, "execute") and not hasattr(plan, "command")
    with pytest.raises(PermissionError, match="not_registered"):
        plan.require_native_admission()
    assert plan.recheck() == plan


@pytest.mark.parametrize("value", [None, True, {}, {"accepted": True}, "accepted"])
def test_reports_and_booleans_do_not_become_trusted_configuration(value):
    with pytest.raises(TypeError, match="exact_trusted"):
        binding.prepare_isolated_plan(value)


@pytest.mark.parametrize("category", sorted(binding._EVIDENCE))
def test_every_evidence_category_is_mandatory(category):
    value = manifest()
    del value["evidence"][category]
    with pytest.raises(PermissionError, match="complete_actual_evidence"):
        binding._validate_manifest(value, now=NOW)


@pytest.mark.parametrize("category", sorted(binding._BINDINGS))
def test_every_binding_is_mandatory(category):
    value = manifest()
    del value["bindings"][category]
    with pytest.raises(PermissionError, match="complete_bindings"):
        binding._validate_manifest(value, now=NOW)


@pytest.mark.parametrize("mutation", [
    {"scope": "synthetic_chatgpt_cache"}, {"accepted": True},
    {"accepted_at": "2099-01-01T00:00:00Z"}, {"expires_at": "2026-10-21T12:00:00Z"},
    {"expires_at": "2026-10-21T00:00:00"}, {"model": "gpt; /bin/sh"},
])
def test_incomplete_foreign_expired_or_malformed_acceptance_rejected(mutation):
    value = {**manifest(), **mutation}
    with pytest.raises(PermissionError):
        binding._validate_manifest(value, now=NOW)


@pytest.mark.parametrize("descriptor", [True, {"file": "/private/auth.json", "sha256": "a" * 64, "bytes": 2},
    {"file": "signin.evidence.json", "sha256": "a" * 64, "bytes": True},
    {"file": "signin.evidence.json", "sha256": "a" * 64, "bytes": 1_000_001},
    {"file": "signin.evidence.json", "sha256": "not-a-digest", "bytes": 2}])
def test_auth_cache_paths_and_invalid_descriptors_are_rejected(descriptor):
    value = manifest()
    value["evidence"]["signin"] = descriptor
    with pytest.raises(PermissionError):
        binding._validate_manifest(value, now=NOW)


def test_root_must_be_operator_controlled_protected_and_absolute(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    tmp_path.chmod(0o755)
    with pytest.raises(PermissionError, match="protected_configuration"):
        binding.TrustedIsolatedConfiguration(tmp_path, workspace, "a" * 64)
    with pytest.raises(PermissionError, match="absolute_ordinary"):
        binding.TrustedIsolatedConfiguration(Path("relative"), workspace, "a" * 64)
    with pytest.raises(PermissionError, match="operator_control_root"):
        binding.TrustedIsolatedConfiguration(workspace, workspace, "a" * 64)


def test_reserved_workspace_operator_control_directory_is_allowed(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    control = workspace / ".devbench-native-control"
    control.mkdir(parents=True, mode=0o700)
    monkeypatch.setattr(binding, "__file__", str(workspace / "devbench/analysis/codex_isolated_binding.py"))
    assert binding.TrustedIsolatedConfiguration(control, workspace, "a" * 64).root == control
    report = workspace / "reports"
    report.mkdir(mode=0o700)
    with pytest.raises(PermissionError, match="operator_control_root"):
        binding.TrustedIsolatedConfiguration(report, workspace, "a" * 64)


def test_installed_package_location_cannot_change_workspace_exclusion(tmp_path, monkeypatch):
    workspace = tmp_path / "task-workspace"
    data = workspace / "task-data"
    data.mkdir(parents=True, mode=0o700)
    monkeypatch.setattr(binding, "__file__", str(tmp_path / "venv/lib/python3.11/site-packages/devbench/analysis/codex_isolated_binding.py"))
    with pytest.raises(PermissionError, match="operator_control_root"):
        binding.TrustedIsolatedConfiguration(data, workspace, "a" * 64)


@pytest.mark.parametrize("mutation", ["root_digest", "evidence_bytes", "evidence_symlink", "permissions", "inventory", "runtime", "source"])
def test_recheck_detects_custody_and_runtime_changes(tmp_path, monkeypatch, mutation):
    config = root_fixture(tmp_path, monkeypatch)
    plan = binding.prepare_isolated_plan(config)
    target = config.root / "signin.evidence.json"
    if mutation == "root_digest":
        with (config.root / "acceptance.json").open("ab") as stream:
            stream.write(b"\n")
    elif mutation == "evidence_bytes":
        target.write_bytes(b"[]")
    elif mutation == "evidence_symlink":
        target.unlink()
        target.symlink_to(config.root / "client.evidence.json")
    elif mutation == "permissions":
        config.root.chmod(0o755)
    elif mutation == "inventory":
        (config.root / "auth.json").write_bytes(b"unapproved")
    else:
        changed = ("b" * 64, "a" * 64) if mutation == "source" else ("a" * 64, "b" * 64)
        monkeypatch.setattr(binding, "_current_runtime", lambda: changed)
    with pytest.raises(PermissionError):
        plan.recheck()


def test_constructor_has_no_credential_reader_or_process_launcher():
    # The contract intentionally exposes preparation only; this assertion catches
    # accidentally shipping an execution hook under the nonadmitting class.
    assert not hasattr(binding.NonAdmittingIsolatedPlan, "run")
    assert not hasattr(binding.NonAdmittingIsolatedPlan, "execute")


def test_nonregular_evidence_is_rejected_without_blocking(tmp_path, monkeypatch):
    config = root_fixture(tmp_path, monkeypatch)
    target = config.root / "signin.evidence.json"
    target.unlink()
    os.mkfifo(target, mode=0o600)
    with pytest.raises(PermissionError, match="protected_bounded_evidence"):
        binding.prepare_isolated_plan(config)
