"""Integrity and single-writer tests for a resumable, still-unfinished DAG."""
import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from tests.test_provenance import provenance


def api():
    module = provenance()
    assert hasattr(module.ExecutionDAG, "checkpoint"), "Immutable DAG checkpoint is missing"
    assert hasattr(module.ExecutionDAG, "resume"), "Verified DAG resume is missing"
    return module


def pending(tmp_path):
    module = api()
    source = tmp_path / "source.csv"
    source.write_text("gene,value\ng1,3\n")
    dag = module.ExecutionDAG(tmp_path / "working")
    artifact = dag.add_artifact(source)
    first = dag.append("analysis.diagnostic", {"artifact": artifact})
    checkpoint = dag.checkpoint(tmp_path / "checkpoint", max_bytes=1_000_000, max_entries=100)
    return module, dag, first, artifact, checkpoint


def test_checkpoint_remains_verifiable_after_original_work_continues(tmp_path):
    module, dag, first, _, checkpoint = pending(tmp_path)
    original = (dag.root_dir / "nodes" / (first + ".json")).read_bytes()
    assert module.verify_execution(checkpoint["manifest_path"], checkpoint["root_hash"])["valid"]
    with pytest.raises(RuntimeError, match="checkpoint"):
        dag.append("stale-writer", {})
    resumed = module.ExecutionDAG.resume(dag.root_dir, checkpoint["manifest_path"], expected_root=checkpoint["root_hash"])
    resumed.append("analysis.completed", {}, parents=[first])
    final = resumed.finalize()
    assert (dag.root_dir / "nodes" / (first + ".json")).read_bytes() == original
    assert module.verify_execution(final["manifest_path"], final["root_hash"])["valid"]
    assert module.verify_execution(checkpoint["manifest_path"], checkpoint["root_hash"])["valid"]
    old = json.loads((tmp_path / "checkpoint/manifest.json").read_text())
    new = json.loads((tmp_path / "working/manifest.json").read_text())
    assert old["run_id"] == new["run_id"]
    assert new["node_ids"][:len(old["node_ids"])] == old["node_ids"]


@pytest.mark.parametrize("location", ["working_node", "working_object", "checkpoint_node", "wrong_root"])
def test_modified_prefix_is_rejected_before_a_resume_claim(tmp_path, location):
    module, dag, first, artifact, checkpoint = pending(tmp_path)
    if location != "wrong_root":
        path = (dag.root_dir / "objects" / artifact["sha256"] if location == "working_object"
                else (tmp_path / "checkpoint" if location == "checkpoint_node" else dag.root_dir) / "nodes" / (first + ".json"))
        path.chmod(0o600)
        path.write_bytes(b"modified")
    with pytest.raises(ValueError):
        module.ExecutionDAG.resume(dag.root_dir, checkpoint["manifest_path"],
            expected_root="0" * 64 if location == "wrong_root" else checkpoint["root_hash"])
    assert not list(tmp_path.glob(".working-resume-*.json"))


def test_resume_claim_allows_exactly_one_writer(tmp_path):
    module, dag, _, _, checkpoint = pending(tmp_path)
    def attempt():
        try:
            return module.ExecutionDAG.resume(dag.root_dir, checkpoint["manifest_path"], expected_root=checkpoint["root_hash"])
        except (ValueError, FileExistsError):
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: attempt(), range(2)))
    assert sum(result is not None for result in results) == 1
    assert len(list(tmp_path.glob(".working-resume-*.json"))) == 1


def test_successive_checkpoints_keep_all_old_receipts_and_claims(tmp_path):
    module, dag, _, _, first = pending(tmp_path)
    resumed = module.ExecutionDAG.resume(dag.root_dir, first["manifest_path"], expected_root=first["root_hash"])
    resumed.append("operator.clarified", {"message": "Keep the declared design"})
    second = resumed.checkpoint(tmp_path / "checkpoint-2", max_bytes=1_000_000, max_entries=200)
    next_writer = module.ExecutionDAG.resume(dag.root_dir, second["manifest_path"], expected_root=second["root_hash"])
    final = next_writer.finalize()
    for receipt in (first, second, final):
        assert module.verify_execution(receipt["manifest_path"], receipt["root_hash"])["valid"]
    assert len(list(tmp_path.glob(".working-resume-*.json"))) == 2


def test_failed_bounded_copy_does_not_promote_partial_checkpoint(tmp_path):
    module = api()
    dag = module.ExecutionDAG(tmp_path / "working")
    dag.append("started", {})
    with pytest.raises(ValueError):
        dag.checkpoint(tmp_path / "partial", max_bytes=1, max_entries=100)
    assert not (tmp_path / "partial/manifest.json").exists()
    dag.append("copy_failed", {})
    receipt = dag.finalize()
    assert module.verify_execution(receipt["manifest_path"], receipt["root_hash"])["valid"]


def test_finished_store_cannot_be_reopened_from_an_old_checkpoint(tmp_path):
    module, dag, _, _, checkpoint = pending(tmp_path)
    resumed = module.ExecutionDAG.resume(dag.root_dir, checkpoint["manifest_path"], expected_root=checkpoint["root_hash"])
    resumed.finalize()
    with pytest.raises((ValueError, FileExistsError, RuntimeError)):
        module.ExecutionDAG.resume(dag.root_dir, checkpoint["manifest_path"], expected_root=checkpoint["root_hash"])


def test_checkpoint_cannot_overlap_its_working_store(tmp_path):
    module = api()
    dag = module.ExecutionDAG(tmp_path / "working")
    with pytest.raises(ValueError, match="overlap"):
        dag.checkpoint(dag.root_dir / "child", max_bytes=1_000_000, max_entries=100)


def test_resume_rejects_uncheckpointed_new_nodes(tmp_path):
    module, dag, first, _, checkpoint = pending(tmp_path)
    (dag.root_dir / "nodes" / ("a" * 64 + ".json")).write_text("{}")
    with pytest.raises(ValueError):
        module.ExecutionDAG.resume(dag.root_dir, checkpoint["manifest_path"], expected_root=checkpoint["root_hash"])


@pytest.mark.parametrize("name", ["manifest.json", "root.sha256"])
def test_dangling_finalization_marker_blocks_resume_before_claim(tmp_path, name):
    module, dag, _, _, checkpoint = pending(tmp_path)
    (dag.root_dir / name).symlink_to(tmp_path / "missing-finalization-file")
    with pytest.raises(ValueError, match="finalized"):
        module.ExecutionDAG.resume(dag.root_dir, checkpoint["manifest_path"], expected_root=checkpoint["root_hash"])
    assert not list(tmp_path.glob(".working-resume-*.json"))


@pytest.mark.parametrize("dimension", ["bytes", "entries"])
def test_checkpoint_limits_include_its_manifest_and_root_receipt(tmp_path, dimension):
    module = api()
    dag = module.ExecutionDAG(tmp_path / "working")
    original = module.hash_artifact(dag.root_dir)
    with pytest.raises(module.ArtifactLimitError):
        dag.checkpoint(tmp_path / "too-small", max_bytes=original["size_bytes"] if dimension == "bytes" else 1_000_000,
            max_entries=len(original["entries"]) if dimension == "entries" else 100)
    assert not (tmp_path / "too-small/manifest.json").exists()


def test_exact_completed_checkpoint_limit_succeeds(tmp_path):
    module = api()
    first = module.ExecutionDAG(tmp_path / "first")
    first.checkpoint(tmp_path / "measured", max_bytes=1_000_000, max_entries=100)
    measured = module.hash_artifact(tmp_path / "measured")
    second = module.ExecutionDAG(tmp_path / "second")
    receipt = second.checkpoint(tmp_path / "bounded", max_bytes=measured["size_bytes"], max_entries=len(measured["entries"]))
    actual = module.hash_artifact(tmp_path / "bounded")
    assert actual["size_bytes"] <= measured["size_bytes"]
    assert len(actual["entries"]) <= len(measured["entries"])
    assert module.verify_execution(receipt["manifest_path"], receipt["root_hash"])["valid"]
