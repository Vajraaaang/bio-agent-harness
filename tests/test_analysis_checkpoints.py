import json

import pytest

from devbench.analysis.checkpoints import CheckpointStore


def test_completed_stage_resumes_but_corruption_is_refused(tmp_path):
    store = CheckpointStore(tmp_path, {"input": "123", "code": "abc", "tier": "colab"})
    calls = []
    def produce(folder):
        calls.append(1)
        (folder / "result.txt").write_text("observed output")
    output = store.run("first", produce)
    store.run("first", produce)
    assert len(calls) == 1
    (output / "result.txt").write_text("tampered")
    with pytest.raises(ValueError, match="artifact_mismatch"):
        store.run("first", produce)
    with pytest.raises(ValueError, match="identity_mismatch"):
        CheckpointStore(tmp_path, {"input": "different"})


def test_failed_attempt_is_retained_and_dependency_change_refused(tmp_path):
    store = CheckpointStore(tmp_path, {"input": "123"})
    def fail(folder):
        (folder / "result.txt").write_text("partial")
        raise RuntimeError("interrupted")
    with pytest.raises(RuntimeError, match="interrupted"):
        store.run("first", fail)
    assert not (tmp_path / "first/receipt.json").exists()
    assert len(list((tmp_path / "failures").glob("*.json"))) == 1
    def finish(folder):
        (folder / "result.txt").write_text("complete")
    store.run("first", finish)
    store.run("second", finish, dependencies=("first",))
    receipt = tmp_path / "first/receipt.json"
    value = json.loads(receipt.read_text())
    value["metadata"]["changed"] = True
    receipt.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="dependency_mismatch"):
        store.run("second", finish, dependencies=("first",))


def test_path_escape_is_refused(tmp_path):
    store = CheckpointStore(tmp_path, {})
    with pytest.raises(ValueError):
        store.path("../outside")
