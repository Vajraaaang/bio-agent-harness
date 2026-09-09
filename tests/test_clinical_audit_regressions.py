"""Research execution integrity regressions; no model inference or clinical data."""
import hashlib
import json
from pathlib import Path
import shlex
import subprocess
import sys

import pytest

import devbench.runner as runner_module
from devbench.adapters.code_action import CodeActionAdapter
from devbench.runner import CodeActionRunner
from devharness_core.watchdog import HarnessResourceInterrupt


def digest(data):
    return hashlib.sha256(data).hexdigest()


class LocalAdapter:
    timeout_sec = 60
    model = None

    def __init__(self, operation):
        self.operation, self.metadata = operation, {}

    def execute(self, instruction, workspace):
        self.instruction = instruction
        return self.operation(workspace)


@pytest.fixture
def task(tmp_path, monkeypatch):
    """Keep real staging/runner behavior; replace only costly dependency staging."""
    spec = {
        "task_id": "audit_regression", "instruction": "Original instruction",
        "metadata": {"source_kind": "synthetic"},
        "files": [{"source": "metadata.csv", "destination": "metadata.csv", "source_kind": "synthetic"}],
        "answer_schema": {"type": "object", "required": ["ok"], "properties": {"ok": {"type": "boolean"}}},
        "grader": {"type": "equals", "field": "ok", "expected": True},
    }
    path = tmp_path / "task.json"
    path.write_text(json.dumps(spec))
    (tmp_path / "metadata.csv").write_text("sample,age\ns1,1\ns2,2\n")

    def stage_runtime(workspace, assisted=True):
        directory = workspace / ".venv/bin"
        directory.mkdir(parents=True)
        (directory / "python").symlink_to(sys.executable)
        source = workspace / ".venv/runtime.py"
        source.write_text("RUNTIME = 'original'\n")
        return {"python": str(directory / "python"),
                "runtime_source_hashes": {".venv/runtime.py": digest(source.read_bytes())}}

    monkeypatch.setattr(runner_module, "stage_python", stage_runtime)
    monkeypatch.setattr(runner_module, "source_fingerprint", lambda: "synthetic-regression-scope")
    return path


@pytest.mark.parametrize("change", ["mutated", "removed", "unchanged"])
def test_adapter_failure_keeps_input_integrity_and_original_error(task, tmp_path, change):
    def fail(workspace):
        file = workspace / "metadata.csv"
        if change == "mutated":
            file.write_text("sample,age\ns1,200\ns2,100\n")
        elif change == "removed":
            file.unlink()
        raise RuntimeError("original execution failure")

    result = CodeActionRunner(task).run(LocalAdapter(fail), tmp_path / "result")
    metadata = result["metadata"]
    assert not result["passed"] and not metadata["accepted_final"]
    assert result["grader_result"] is None and result["score"] == 0
    assert metadata["failure_type"] == "agent_error"
    assert "original execution failure" in metadata["error"]
    integrity = metadata["input_integrity"]
    entry = integrity["protected_inputs"]["metadata.csv"]
    assert entry["expected_sha256"] == metadata["input_hashes"]["metadata.csv"]
    assert integrity["passed"] is (change == "unchanged")
    assert entry["status"] == {"mutated": "changed", "removed": "missing", "unchanged": "unchanged"}[change]
    if change == "mutated":
        assert entry["observed_sha256"] != entry["expected_sha256"]
        preserved = entry["preserved_artifact"]
        assert preserved["preserved"] and preserved["matches_observed_sha256"]
        assert digest(Path(preserved["path"]).read_bytes()) == entry["observed_sha256"]
    elif change == "removed":
        assert entry["observed_sha256"] is None and entry["error"]
    assert (tmp_path / "result/run.json").is_file()


@pytest.mark.parametrize("change", ["mutated", "removed"])
def test_adapter_failure_checks_sdk_sources(task, tmp_path, change):
    def fail(workspace):
        file = workspace / ".venv/runtime.py"
        if change == "mutated":
            file.write_text("RUNTIME = 'changed'\n")
        else:
            file.unlink()
        raise RuntimeError("SDK execution failure")

    result = CodeActionRunner(task).run(LocalAdapter(fail), tmp_path / "result")
    metadata = result["metadata"]
    assert metadata["failure_type"] == "agent_error" and "SDK execution failure" in metadata["error"]
    assert not metadata["input_integrity"]["passed"] and not result["passed"]
    entry = metadata["input_integrity"]["runtime_sources"][".venv/runtime.py"]
    assert entry["status"] == ("changed" if change == "mutated" else "missing")


def test_integrity_read_error_does_not_erase_execution_failure(task, tmp_path, monkeypatch):
    original_hash = runner_module.hash_artifact
    failed = False

    def hash_with_read_error(path):
        if failed and Path(path).name == "metadata.csv":
            raise PermissionError("synthetic final hash denial")
        return original_hash(path)

    def fail(workspace):
        nonlocal failed
        failed = True
        raise RuntimeError("keep this execution error")

    monkeypatch.setattr(runner_module, "hash_artifact", hash_with_read_error)
    result = CodeActionRunner(task).run(LocalAdapter(fail), tmp_path / "result")
    assert "keep this execution error" in result["metadata"]["error"]
    integrity = result["metadata"]["input_integrity"]
    assert not integrity["passed"] and not result["passed"]
    assert integrity["protected_inputs"]["metadata.csv"]["status"] == "error"
    assert "synthetic final hash denial" in integrity["protected_inputs"]["metadata.csv"]["error"]
    assert (tmp_path / "result/run.json").is_file()


def test_successful_return_with_changed_input_cannot_pass(task, tmp_path):
    def mutate(workspace):
        (workspace / "metadata.csv").write_text("sample,age\ns1,200\n")
        return {"ok": True}

    result = CodeActionRunner(task).run(LocalAdapter(mutate), tmp_path / "result")
    assert result["metadata"]["failure_type"] == "input_integrity_error"
    assert not result["metadata"]["input_integrity"]["passed"]
    assert not result["passed"] and not result["metadata"]["accepted_final"]
    assert result["grader_result"] is None


def test_changed_input_preservation_has_an_aggregate_byte_bound(task, tmp_path):
    def fail(workspace):
        (workspace / "metadata.csv").write_bytes(b"x" * 5_000_000)
        (workspace / ".venv/runtime.py").write_bytes(b"y" * 5_000_000)
        raise RuntimeError("failed with changed inputs")

    result = CodeActionRunner(task).run(LocalAdapter(fail), tmp_path / "result")
    integrity = result["metadata"]["input_integrity"]
    receipts = [entry["preserved_artifact"] for group in ("protected_inputs", "runtime_sources")
                for entry in integrity[group].values() if entry["status"] == "changed"]
    copied = sum(receipt.get("bytes", 0) for receipt in receipts)
    assert copied == integrity["preserved_bytes"] == 5_000_000
    assert copied <= integrity["preservation_byte_limit"] == 8_000_000
    assert sum(receipt["preserved"] for receipt in receipts) == 1
    assert not result["passed"] and not result["metadata"]["accepted_final"]


def test_preservation_error_does_not_replace_execution_error(task, tmp_path, monkeypatch):
    original_copy = runner_module._bounded_copy

    def failing_copy(source, destination, limit=8_000_000):
        if "changed_inputs" in destination.parts:
            raise PermissionError("synthetic preservation denial")
        return original_copy(source, destination, limit)

    def fail(workspace):
        (workspace / "metadata.csv").write_text("changed data")
        raise RuntimeError("original execution error")

    monkeypatch.setattr(runner_module, "_bounded_copy", failing_copy)
    result = CodeActionRunner(task).run(LocalAdapter(fail), tmp_path / "result")
    metadata = result["metadata"]
    assert "original execution error" in metadata["error"]
    receipt = metadata["input_integrity"]["protected_inputs"]["metadata.csv"]["preserved_artifact"]
    assert not receipt["preserved"] and "synthetic preservation denial" in receipt["reason"]
    assert (tmp_path / "result/run.json").is_file()


def test_task_hash_binds_archived_execution_snapshot_after_source_drift(task, tmp_path):
    runner = CodeActionRunner(task)
    original = json.loads(task.read_text())
    updated = dict(original, instruction="Unused changed instruction", grader={"type": "equals", "field": "ok", "expected": False})
    task.write_text(json.dumps(updated))

    def answer(workspace):
        assert not (workspace / "task_spec.snapshot.json").exists()
        assert "grader" not in json.loads((workspace / "answer_schema.json").read_text())
        return {"ok": True}

    adapter = LocalAdapter(answer)
    result = runner.run(adapter, tmp_path / "result")
    snapshot = (tmp_path / "result/task_spec.snapshot.json").read_bytes()
    assert json.loads(snapshot) == original
    assert adapter.instruction == original["instruction"] and result["passed"]
    assert result["metadata"]["task_spec_hash"] == digest(snapshot)
    assert result["metadata"]["task_spec_hash"] != digest(task.read_bytes())
    assert result["metadata"]["task_spec_hash_scope"] == "canonical_json_normalized_task_snapshot"
    assert result["metadata"]["artifacts"]["task_spec.snapshot.json"]["scope"] == "coordinator_only"


@pytest.mark.parametrize("failure", ["nonzero_exit", "resource_interrupt"])
def test_real_script_proof_survives_subsequent_cli_failure(task, tmp_path, monkeypatch, failure):
    adapter = CodeActionAdapter(executable=sys.executable, timeout_sec=30)
    monkeypatch.setattr(adapter, "_preflight", lambda *args, **kwargs: None)

    def simulated_transport(command, workspace, environment, prompt, remaining):
        script = workspace / "solve.py"
        script.write_text("from pathlib import Path\nPath('final_discovery.json').write_text('{\"ok\":true}')\n")
        invocation = [str(adapter.python), str(script)]
        completed = subprocess.run(invocation, cwd=workspace, capture_output=True, text=True, timeout=10)
        assert completed.returncode == 0
        adapter._event({"type": "item.completed", "item": {"type": "command_execution",
            "command": shlex.join(invocation), "status": "completed", "exit_code": completed.returncode,
            "aggregated_output": completed.stdout + completed.stderr}})
        if failure == "resource_interrupt":
            raise HarnessResourceInterrupt("synthetic interruption after successful script")
        return 1, "", ""

    monkeypatch.setattr(adapter, "_run_process", simulated_transport)
    result = CodeActionRunner(task).run(adapter, tmp_path / "result")
    metadata = result["metadata"]
    assert metadata["script_execution_observed"]
    assert not result["passed"] and not metadata["accepted_final"]
    assert result["grader_result"] is None and result["score"] == 0
    assert metadata["failure_type"] == ("resource_interrupt" if failure == "resource_interrupt" else "agent_error")
    assert metadata["input_integrity"]["passed"]
    event = metadata["adapter_observations"]["commands"][0]
    assert event["direct_script_invocation"] and event["succeeded"]
    for name, key in [("solve.py", "script_sha256_at_completion"), ("final_discovery.json", "answer_sha256_at_completion")]:
        assert event[key] == digest((tmp_path / "result" / name).read_bytes())
