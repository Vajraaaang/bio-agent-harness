"""Anchored restore and numerical replay without model inference or network calls."""

import importlib
import importlib.util
import json
from pathlib import Path

import pytest

from devharness_core.provenance import ExecutionDAG


def audit_api():
    assert importlib.util.find_spec("devbench_engine.audit_cli") is not None
    return importlib.import_module("devbench_engine.audit_cli")


def capture(arguments):
    from devharness_server.dispatcher import ToolDispatcher, capture_tool_events
    with capture_tool_events() as events:
        try:
            ToolDispatcher().execute("calculate_temporal_monotonicity", arguments)
        except Exception:
            pass
    return events[0]


def bundle(tmp_path, *, events=None, code_hash=None, environment=None,
           include_workspace=True, inputs_key="metadata.csv", unregistered=False):
    from devbench_engine.runner import _code_fingerprint, _environment
    work = tmp_path / "original"
    work.mkdir(exist_ok=True)
    metadata = work / "metadata.csv"
    metadata.write_text("sample_id,embryonic_age\ns1,E8.5\ns2,E9.5\n")
    counts = work / "counts.zarr"
    counts.mkdir(exist_ok=True)
    (counts / "empty").mkdir(exist_ok=True)
    (counts / "chunk").write_bytes(b"stable captured counts")
    dag = ExecutionDAG(tmp_path / "ledger")
    parent = dag.append("run_started", {"code_hash": code_hash or _code_fingerprint(),
                        "environment": _environment() if environment is None else environment})
    inputs = {inputs_key: dag.add_artifact(metadata), "counts.zarr": dag.add_artifact(counts)}
    if unregistered:
        inputs[inputs_key] = {**inputs[inputs_key], "name": "unregistered.csv"}
    payload = {"artifacts": inputs}
    if include_workspace:
        payload["workspace"] = str(work)
    parent = dag.append("inputs_staged", payload, parents=[parent])
    for event in events or []:
        parent = dag.append("tool_completed" if event["succeeded"] else "tool_failed", event, parents=[parent])
    dag.append("agent_turn", {"text": "Never execute this captured agent text."}, parents=[parent])
    receipt = dag.finalize()
    return receipt, work


def test_verify_cli_reports_unanchored_integrity(tmp_path, capsys):
    api = audit_api()
    receipt, _ = bundle(tmp_path)
    assert api.main(["verify", "--manifest", receipt["manifest_path"]]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["valid"] is True
    assert report["anchored"] is False
    assert report["compliance_certified"] is False


def test_verify_cli_fails_wrong_trusted_root(tmp_path, capsys):
    api = audit_api()
    receipt, _ = bundle(tmp_path)
    assert api.main(["verify", "--manifest", receipt["manifest_path"], "--expected-root", "a" * 64]) == 1
    assert json.loads(capsys.readouterr().out)["valid"] is False


def test_restore_retains_file_directory_and_empty_member(tmp_path):
    api = audit_api()
    receipt, work = bundle(tmp_path)
    (work / "metadata.csv").unlink()
    (work / "counts.zarr" / "chunk").unlink()
    output = tmp_path / "restored"
    report = api.restore_execution(receipt["manifest_path"], receipt["root_hash"], output)
    assert report["restored"] is True
    assert report["anchored"] is True
    assert (output / "metadata.csv").read_text().startswith("sample_id,")
    assert (output / "counts.zarr" / "chunk").read_bytes() == b"stable captured counts"
    assert (output / "counts.zarr" / "empty").is_dir()
    assert Path(report["artifacts"]["metadata.csv"]).is_absolute()


def test_restore_requires_external_root_and_does_not_create_output(tmp_path):
    api = audit_api()
    receipt, _ = bundle(tmp_path)
    output = tmp_path / "restored"
    with pytest.raises(api.AuditError, match="root"):
        api.restore_execution(receipt["manifest_path"], None, output)
    assert not output.exists()


def test_restore_never_overwrites_nonempty_destination(tmp_path):
    api = audit_api()
    receipt, _ = bundle(tmp_path)
    output = tmp_path / "restored"
    output.mkdir()
    retained = output / "metadata.csv"
    retained.write_text("preserve this")
    with pytest.raises(api.AuditError, match="empty"):
        api.restore_execution(receipt["manifest_path"], receipt["root_hash"], output)
    assert retained.read_text() == "preserve this"


def test_restore_rejects_symlink_destination(tmp_path):
    api = audit_api()
    receipt, _ = bundle(tmp_path)
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "link"
    link.symlink_to(target, target_is_directory=True)
    with pytest.raises(api.AuditError, match="[Ss]ymlink"):
        api.restore_execution(receipt["manifest_path"], receipt["root_hash"], link)
    assert list(target.iterdir()) == []


def test_restore_refuses_tampered_artifacts_before_writing(tmp_path):
    api = audit_api()
    receipt, _ = bundle(tmp_path)
    root = Path(receipt["manifest_path"]).parent
    manifest = json.loads(Path(receipt["manifest_path"]).read_text())
    blob = root / "objects" / manifest["artifacts"][0]["sha256"]
    blob.chmod(0o600)
    blob.write_bytes(b"tampered")
    output = tmp_path / "restored"
    with pytest.raises(api.AuditError, match="verification"):
        api.restore_execution(receipt["manifest_path"], receipt["root_hash"], output)
    assert not output.exists()


@pytest.mark.parametrize("options", [{"inputs_key": "../outside"}, {"unregistered": True}])
def test_restore_rejects_unsafe_or_unregistered_staged_references(tmp_path, options):
    api = audit_api()
    receipt, _ = bundle(tmp_path, **options)
    output = tmp_path / "restored"
    with pytest.raises(api.AuditError):
        api.restore_execution(receipt["manifest_path"], receipt["root_hash"], output)
    assert not output.exists()


def test_numerical_replay_reproduces_successes_and_expected_failures(tmp_path):
    api = audit_api()
    success = capture({"stage_values": {"E8.5": 1, "E9.5": 3, "E10.5": 2}})
    failure = capture({"stage_values": {"E8.5": 1, "8.5": 2}})
    assert success["succeeded"] and not failure["succeeded"]
    receipt, _ = bundle(tmp_path, events=[success, failure])
    output = tmp_path / "replayed"
    report = api.replay_execution(receipt["manifest_path"], receipt["root_hash"], output)
    assert report["passed"] is True
    assert report["runtime_match"] is True
    assert report["replayed_tool_count"] == 2
    assert [item["expected_success"] for item in report["comparisons"]] == [True, False]
    assert report["model_calls"] == 0
    assert report["scope"] == "captured_numerical_tools_only"
    assert Path(report["report_path"]).is_file()


@pytest.mark.parametrize("delta,passed", [(1e-10, True), (1e-3, False)])
def test_replay_compares_finite_numbers_with_explicit_tolerances(tmp_path, delta, passed):
    api = audit_api()
    event = capture({"stage_values": {"E8.5": 1, "E9.5": 2}})
    event["result"]["score"] += delta
    receipt, _ = bundle(tmp_path, events=[event])
    report = api.replay_execution(receipt["manifest_path"], receipt["root_hash"], tmp_path / "replayed")
    assert report["passed"] is passed
    assert report["atol"] == 1e-8
    assert report["rtol"] == 1e-8


def test_replay_does_not_ignore_structural_boolean_or_method_changes(tmp_path):
    api = audit_api()
    event = capture({"stage_values": {"E8.5": 1, "E9.5": 2}})
    event["result"]["is_monotonic"] = 1
    event["result"]["method"] = "forged-method"
    receipt, _ = bundle(tmp_path, events=[event])
    report = api.replay_execution(receipt["manifest_path"], receipt["root_hash"], tmp_path / "replayed")
    assert report["passed"] is False
    assert report["comparisons"][0]["mismatches"]


@pytest.mark.parametrize("options", [{"code_hash": "0" * 64}, {"environment": {"packages": {}}}])
def test_replay_refuses_code_or_environment_mismatch_before_restore(tmp_path, options):
    api = audit_api()
    event = capture({"stage_values": {"E8.5": 1, "E9.5": 2}})
    receipt, _ = bundle(tmp_path, events=[event], **options)
    output = tmp_path / "replayed"
    report = api.replay_execution(receipt["manifest_path"], receipt["root_hash"], output)
    assert report["passed"] is False
    assert report["status"] == "unreplayable"
    assert report["runtime_match"] is False
    assert not output.exists()


def test_replay_remaps_captured_paths_and_normalizes_expected_error_strings(tmp_path):
    api = audit_api()
    original = tmp_path / "original"
    event = capture({"stage_values": {"E8.5": 1, "E9.5": 2},
                     "metadata_csv_path": str(original / "metadata.csv")})
    assert not event["succeeded"]  # Schema error: no worker or numerical fit runs.
    receipt, _ = bundle(tmp_path, events=[event])
    output = tmp_path / "replayed"
    report = api.replay_execution(receipt["manifest_path"], receipt["root_hash"], output)
    assert report["passed"] is True
    assert report["path_mapping"][str(original / "metadata.csv")] == str(output / "metadata.csv")


@pytest.mark.parametrize("reference", ["missing.tsv", "metadata.csv"])
def test_replay_refuses_uncaptured_external_paths_even_if_basename_matches(tmp_path, reference):
    api = audit_api()
    external = tmp_path / "external" / reference
    event = capture({"stage_values": {"E8.5": 1, "E9.5": 2}, "reference_path": str(external)})
    receipt, _ = bundle(tmp_path, events=[event])
    output = tmp_path / "replayed"
    report = api.replay_execution(receipt["manifest_path"], receipt["root_hash"], output)
    assert report["passed"] is False
    assert report["status"] == "unreplayable"
    assert any("external" in error for error in report["errors"])
    assert not output.exists()


def test_replay_refuses_path_calls_without_original_workspace_binding(tmp_path):
    api = audit_api()
    event = capture({"stage_values": {"E8.5": 1},
                     "metadata_csv_path": str(tmp_path / "original" / "metadata.csv")})
    receipt, _ = bundle(tmp_path, events=[event], include_workspace=False)
    report = api.replay_execution(receipt["manifest_path"], receipt["root_hash"], tmp_path / "replayed")
    assert report["status"] == "unreplayable"
    assert any("workspace" in error for error in report["errors"])


def test_replay_rejects_unknown_tools_and_does_not_execute_agent_text(tmp_path):
    api = audit_api()
    event = {"tool_name": "shell", "arguments": {"command": "claude"},
             "succeeded": False, "error": "Unknown tool"}
    receipt, _ = bundle(tmp_path, events=[event])
    report = api.replay_execution(receipt["manifest_path"], receipt["root_hash"], tmp_path / "replayed")
    assert report["status"] == "unreplayable"
    assert report["model_calls"] == 0
    assert report["replayed_tool_count"] == 0


def test_replay_requires_at_least_one_captured_numerical_event(tmp_path):
    api = audit_api()
    receipt, _ = bundle(tmp_path)
    report = api.replay_execution(receipt["manifest_path"], receipt["root_hash"], tmp_path / "replayed")
    assert report["status"] == "unreplayable"
    assert report["passed"] is False


def test_restore_cli_returns_local_artifact_manifest(tmp_path, capsys):
    api = audit_api()
    receipt, _ = bundle(tmp_path)
    output = tmp_path / "restored"
    assert api.main(["restore", "--manifest", receipt["manifest_path"],
                     "--expected-root", receipt["root_hash"], "--output-dir", str(output)]) == 0
    assert json.loads(capsys.readouterr().out)["restored"] is True


def test_replay_cli_rejects_nonfinite_tolerances(tmp_path, capsys):
    api = audit_api()
    receipt, _ = bundle(tmp_path)
    assert api.main(["replay", "--manifest", receipt["manifest_path"],
                     "--expected-root", receipt["root_hash"], "--output-dir", str(tmp_path / "replay"),
                     "--atol", "nan"]) == 1
    assert json.loads(capsys.readouterr().out)["passed"] is False


def test_missing_runtime_dependency_is_an_explicit_unreplayable_result(tmp_path, monkeypatch):
    api = audit_api()
    receipt, _ = bundle(tmp_path)
    def unavailable(_bundle):
        raise ImportError("Required numerical dependency is unavailable")
    monkeypatch.setattr(api, "_runtime_matches", unavailable)
    report = api.replay_execution(receipt["manifest_path"], receipt["root_hash"], tmp_path / "replayed")
    assert report["passed"] is False
    assert report["status"] == "unreplayable"
    assert any("dependency" in error for error in report["errors"])
