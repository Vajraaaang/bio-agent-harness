"""Effective task identity and bounded, non-destructive evidence restoration."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

from devbench_engine import audit_cli
from devbench_engine.runner import EvalRunner
from devharness_core.provenance import ExecutionDAG, canonical_json, verify_execution


def _task(tmp_path):
    task = {"id":"captured_task", "task":"Return 4", "metadata":{"source_kind":"synthetic"},
            "answer_schema":{"type":"object", "properties":{"value":{"type":"integer"}}, "required":["value"]},
            "grader":{"type":"equals", "config":{"field":"value", "expected":4}}}
    path = tmp_path / "task.json"
    path.write_text(json.dumps(task))
    return path


def _archived_task(result):
    receipt = result["metadata"]["audit"]
    manifest_path = Path(receipt["manifest_path"])
    manifest = json.loads(manifest_path.read_text())
    nodes = [json.loads((manifest_path.parent / "nodes" / (node_id + ".json")).read_text()) for node_id in manifest["node_ids"]]
    start = next(node["payload"] for node in nodes if node["kind"] == "run_started")
    reference = start["task_spec"]
    payload = (manifest_path.parent / "objects" / reference["sha256"]).read_bytes()
    assert verify_execution(manifest_path, receipt["root_hash"])["valid"]
    return json.loads(payload), payload, start


@pytest.mark.parametrize("change", ["replace", "delete", "during_adapter"])
def test_run_archives_and_uses_one_private_effective_task_snapshot(tmp_path, change):
    path = _task(tmp_path)
    runner = EvalRunner(str(path), audit_dir=tmp_path / "audit", condition="baseline")
    expected = canonical_json(runner.spec)
    if change == "replace":
        changed = json.loads(path.read_text())
        changed.update(id="replacement", task="Return 9")
        changed["grader"]["config"]["expected"] = 9
        path.write_text(json.dumps(changed))
    elif change == "delete":
        path.unlink()
    def control(instruction, directory):
        assert instruction == "Return 4"
        assert not any("task_spec" in p.name or p.name == "task.json" for p in Path(directory).iterdir())
        assert not any("grader" in p.name or "snapshot" in p.name for p in Path(directory).iterdir())
        if change == "during_adapter":
            runner.spec.update(task_id="mutated_in_memory", instruction="Return 9")
            runner.spec["grader"]["config"]["expected"] = 9
            runner.spec["answer_schema"]["properties"]["value"]["type"] = "string"
        return {"value":4}
    result = runner.run(control)
    assert result["passed"], result
    assert result["task_id"] == "captured_task"
    archived, payload, start = _archived_task(result)
    assert payload == expected
    assert archived["grader"]["config"]["expected"] == 4
    assert hashlib.sha256(payload).hexdigest() == result["metadata"]["task_spec_hash"]
    assert start["task_spec_hash"] == result["metadata"]["task_spec_hash"]
    assert start["task_spec_hash_scope"] == "canonical_json_normalized_task_snapshot"
    assert start["task_spec_source_path"] == str(path)


def test_explicit_configuration_changes_between_runs_get_distinct_bound_snapshots(tmp_path):
    runner = EvalRunner(str(_task(tmp_path)), audit_dir=tmp_path / "audit", condition="baseline")
    first = runner.run(lambda *_: {"value":4})
    runner.spec["grader"]["config"]["expected"] = 9
    runner.spec["instruction"] = "Return 9"
    second = runner.run(lambda instruction, _: {"value":9} if instruction == "Return 9" else {"value":0})
    assert first["passed"] and second["passed"]
    one, raw_one, _ = _archived_task(first)
    two, raw_two, _ = _archived_task(second)
    assert one["grader"]["config"]["expected"] == 4
    assert two["grader"]["config"]["expected"] == 9
    assert raw_one != raw_two


def test_clinical_hold_and_condition_use_the_configuration_at_run_start(tmp_path):
    runner = EvalRunner(str(_task(tmp_path)), audit_dir=tmp_path / "audit", condition="assisted", validation_mode="clinical")
    def control(*_):
        runner.validation_mode = "research"
        runner.condition = "baseline"
        return {"value":4}
    result = runner.run(control)
    assert not result["passed"]
    assert result["metadata"]["failure_type"] == "regulatory_release_hold"
    assert result["metadata"]["validation_mode"] == "clinical"
    assert result["metadata"]["release_status"] == "held"
    assert result["metadata"]["condition"] == "assisted"
    assert result["metadata"]["tool_capture_complete"] is True


def _bundle(tmp_path):
    source = tmp_path / "input.csv"
    source.write_text("sample_id,time\ns1,0\ns2,1\n")
    dag = ExecutionDAG(tmp_path / "ledger")
    reference = dag.add_artifact(source)
    start = dag.append("run_started", {"scope":"software fixture"})
    node = dag.append("inputs_staged", {"artifacts":{"input.csv":reference}}, parents=[start])
    receipt = dag.finalize()
    return receipt, {"manifest":Path(receipt["manifest_path"]),
                     "node":dag.root_dir / "nodes" / (node + ".json"),
                     "blob":dag.root_dir / "objects" / reference["sha256"]}


_FIFO_CHILD = """
import json, os, sys
from pathlib import Path
from devbench_engine import audit_cli
manifest, root, target, ready, output = sys.argv[1:]
original_verify = audit_cli.verify_execution
def replaced_after_verification(*args):
    report = original_verify(*args)
    assert report['valid']
    Path(target).unlink()
    os.mkfifo(target, 0o600)
    Path(ready).write_text('verified and replaced')
    return report
audit_cli.verify_execution = replaced_after_verification
try:
    audit_cli.restore_execution(manifest, root, output)
except Exception as error:
    print(json.dumps({'rejected':True, 'error':str(error)}), flush=True)
else:
    print(json.dumps({'rejected':False}), flush=True)
"""


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="Requires POSIX FIFOs")
@pytest.mark.parametrize("position", ["manifest", "node", "blob"])
def test_restore_rejects_fifo_replaced_after_successful_integrity_verification(tmp_path, position):
    receipt, targets = _bundle(tmp_path)
    target, ready = targets[position], tmp_path / "ready"
    child = subprocess.Popen([sys.executable, "-u", "-c", _FIFO_CHILD, receipt["manifest_path"], receipt["root_hash"],
                              str(target), str(ready), str(tmp_path / "output")],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        deadline = time.monotonic() + 30
        while not ready.exists() and child.poll() is None and time.monotonic() < deadline:
            time.sleep(.01)
        assert ready.exists(), "Child did not finish startup and verified replacement"
        try:
            stdout, stderr = child.communicate(timeout=2)
        except subprocess.TimeoutExpired:
            pytest.fail("Restore blocked on a FIFO after successful verification")
        assert child.returncode == 0, stderr
        result = json.loads(stdout)
        assert result["rejected"] and "regular file" in result["error"]
    finally:
        if child.poll() is None: child.kill()
        child.communicate(timeout=5)
        target.unlink(missing_ok=True)


@pytest.mark.parametrize("foreign_destination", [False, True])
def test_growing_restore_never_writes_over_declared_size_or_removes_foreign_destination(tmp_path, monkeypatch, foreign_destination):
    source, destination = tmp_path / "blob", tmp_path / "restored"
    original = b"a" * 16
    source.write_bytes(original)
    observed = {"written":0, "mutated":False}
    fdopen = os.fdopen
    class Sink:
        def __init__(self, stream): self.stream = stream
        def __enter__(self): return self
        def __exit__(self, *args): return self.stream.__exit__(*args)
        def __getattr__(self, name): return getattr(self.stream, name)
        def write(self, data):
            count = self.stream.write(data)
            observed["written"] += count
            if not observed["mutated"]:
                with source.open("ab") as stream: stream.write(b"b" * 17)
                if foreign_destination:
                    if destination.exists(): destination.rename(tmp_path / "original-partial")
                    destination.write_bytes(b"foreign file; preserve")
                observed["mutated"] = True
            return count
    def wrap(descriptor, mode, *args, **kwargs):
        stream = fdopen(descriptor, mode, *args, **kwargs)
        return Sink(stream) if mode == "wb" else stream
    monkeypatch.setattr(audit_cli.os, "fdopen", wrap)
    monkeypatch.setattr(audit_cli, "_CHUNK_BYTES", 4)
    with pytest.raises(ValueError):
        audit_cli._copy_blob(source, destination, tmp_path, hashlib.sha256(original).hexdigest(), len(original))
    assert observed["mutated"]
    assert observed["written"] <= len(original)
    if foreign_destination:
        assert destination.read_bytes() == b"foreign file; preserve"
    else:
        assert not destination.exists()


def test_existing_restore_destination_is_preserved(tmp_path):
    source, destination = tmp_path / "blob", tmp_path / "restored"
    source.write_bytes(b"original")
    destination.write_bytes(b"existing")
    with pytest.raises(FileExistsError):
        audit_cli._copy_blob(source, destination, tmp_path, hashlib.sha256(b"original").hexdigest(), 8)
    assert destination.read_bytes() == b"existing"


def test_restore_refuses_destination_created_while_valid_content_is_copied(tmp_path, monkeypatch):
    source, destination = tmp_path / "blob", tmp_path / "restored"
    original = b"original"
    source.write_bytes(original)
    fdopen = os.fdopen
    class Sink:
        def __init__(self, stream): self.stream = stream
        def __enter__(self): return self
        def __exit__(self, *args): return self.stream.__exit__(*args)
        def __getattr__(self, name): return getattr(self.stream, name)
        def write(self, data):
            count = self.stream.write(data)
            if destination.exists(): destination.rename(tmp_path / "original-partial")
            destination.write_bytes(b"foreign")
            return count
    def wrap(descriptor, mode, *args, **kwargs):
        stream = fdopen(descriptor, mode, *args, **kwargs)
        return Sink(stream) if mode == "wb" else stream
    monkeypatch.setattr(audit_cli.os, "fdopen", wrap)
    with pytest.raises(FileExistsError):
        audit_cli._copy_blob(source, destination, tmp_path, hashlib.sha256(original).hexdigest(), len(original))
    assert destination.read_bytes() == b"foreign"
