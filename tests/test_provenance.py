"""Behavioral tests for durable, tamper-evident execution evidence."""

import hashlib
import importlib
import importlib.util
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest


def provenance():
    assert importlib.util.find_spec("devharness_core.provenance") is not None
    return importlib.import_module("devharness_core.provenance")


def make_run(tmp_path):
    api = provenance()
    source = tmp_path / "input.csv"
    source.write_text("gene,sample\nSox2,12\n")
    dag = api.ExecutionDAG(tmp_path / "ledger", actor_id="researcher-unverified")
    artifact = dag.add_artifact(source)
    first = dag.append("execution_started", {"input": artifact})
    dag.append("execution_finished", {"status": "succeeded"}, parents=[first])
    return api, source, dag, artifact, dag.finalize()


def rewrite(path, value):
    path.chmod(0o600)
    path.write_text(json.dumps(value))


def test_canonical_json_is_order_independent_and_does_not_coerce_keys():
    api = provenance()
    assert api.canonical_json({"b": 2, "a": "β"}) == api.canonical_json({"a": "β", "b": 2})
    assert api.canonical_json({"a": "β"}) == '{"a":"β"}'.encode()
    with pytest.raises((TypeError, ValueError)):
        api.canonical_json({1: "ambiguous"})


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_nonfinite_json_is_rejected(value):
    api = provenance()
    with pytest.raises(ValueError):
        api.canonical_json({"nested": [value]})


def test_file_artifacts_remain_available_after_source_changes_and_deletion(tmp_path):
    api, source, dag, artifact, receipt = make_run(tmp_path)
    original = source.read_bytes()
    source.write_text("changed source")
    source.unlink()
    blob = dag.root_dir / "objects" / artifact["sha256"]
    assert blob.read_bytes() == original
    report = api.verify_execution(receipt["manifest_path"], receipt["root_hash"])
    assert report["valid"] is True
    assert report["anchored"] is True
    assert report["errors"] == []


def test_directory_hash_includes_names_contents_and_empty_directories(tmp_path):
    api = provenance()
    source = tmp_path / "counts.zarr"
    source.mkdir()
    (source / "empty").mkdir()
    (source / "chunk").write_bytes(b"counts")
    first = api.hash_artifact(source)
    (source / "chunk").rename(source / "renamed")
    assert api.hash_artifact(source)["sha256"] != first["sha256"]
    (source / "renamed").rename(source / "chunk")
    (source / "empty").rmdir()
    assert api.hash_artifact(source)["sha256"] != first["sha256"]


def test_directory_artifacts_verify_from_immutable_copies(tmp_path):
    api = provenance()
    source = tmp_path / "counts.zarr"
    source.mkdir()
    (source / "group").mkdir()
    (source / "group" / "chunk").write_bytes(b"bounded stream" * 100_000)
    dag = api.ExecutionDAG(tmp_path / "ledger")
    artifact = dag.add_artifact(source)
    dag.append("input", {"artifact": artifact})
    receipt = dag.finalize()
    (source / "group" / "chunk").unlink()
    assert api.verify_execution(receipt["manifest_path"], receipt["root_hash"])["valid"]
    assert artifact["size_bytes"] == 1_400_000


@pytest.mark.parametrize("directory_link", [False, True])
def test_artifact_symlinks_are_rejected(tmp_path, directory_link):
    api = provenance()
    target = tmp_path / "target"
    target.write_bytes(b"protected")
    source = tmp_path / "source"
    if directory_link:
        source.mkdir()
        (source / "link").symlink_to(target)
    else:
        source.symlink_to(target)
    with pytest.raises(ValueError, match="[Ss]ymlink"):
        api.hash_artifact(source)
    dag = api.ExecutionDAG(tmp_path / "ledger")
    with pytest.raises(ValueError, match="[Ss]ymlink"):
        dag.add_artifact(source)


def test_duplicate_artifact_registration_never_overwrites_existing_bytes(tmp_path):
    api = provenance()
    source = tmp_path / "source"
    source.write_bytes(b"original")
    dag = api.ExecutionDAG(tmp_path / "ledger")
    first = dag.add_artifact(source)
    blob = dag.root_dir / "objects" / first["sha256"]
    before = blob.stat().st_mtime_ns
    assert dag.add_artifact(source) == first
    assert blob.stat().st_mtime_ns == before
    blob.chmod(0o600)
    blob.write_bytes(b"tampered")
    with pytest.raises(ValueError, match="[Hh]ash|[Cc]orrupt|[Tt]amper"):
        dag.add_artifact(source)
    assert blob.read_bytes() == b"tampered"


def test_unknown_parents_and_cycles_are_rejected_before_writing(tmp_path):
    api = provenance()
    dag = api.ExecutionDAG(tmp_path / "ledger")
    with pytest.raises(ValueError, match="parent"):
        dag.append("future", {}, parents=["a" * 64])
    first = dag.append("first", {})
    with pytest.raises(ValueError, match="parent"):
        dag.append("duplicate-parent", {}, parents=[first, first])
    receipt = dag.finalize()
    assert receipt["node_count"] == 1


def test_payloads_and_artifact_references_are_detached_from_caller_mutations(tmp_path):
    api = provenance()
    source = tmp_path / "source"
    source.write_bytes(b"original")
    dag = api.ExecutionDAG(tmp_path / "ledger")
    artifact = dag.add_artifact(source)
    payload = {"nested": ["original"], "artifact": artifact}
    node_id = dag.append("input", payload)
    payload["nested"].append("changed")
    artifact["sha256"] = "f" * 64
    receipt = dag.finalize()
    node = json.loads((dag.root_dir / "nodes" / (node_id + ".json")).read_text())
    assert node["payload"]["nested"] == ["original"]
    assert api.verify_execution(receipt["manifest_path"], receipt["root_hash"])["valid"]


def test_finalization_is_idempotent_and_closes_the_run(tmp_path):
    api, source, dag, artifact, receipt = make_run(tmp_path)
    assert dag.finalize() == receipt
    with pytest.raises(RuntimeError, match="finalized"):
        dag.append("late", {})
    with pytest.raises(RuntimeError, match="finalized"):
        dag.add_artifact(source)
    with pytest.raises(FileExistsError):
        api.ExecutionDAG(dag.root_dir)


@pytest.mark.parametrize("what", ["artifact", "node", "deleted_node"])
def test_tampered_or_deleted_evidence_fails_verification(tmp_path, what):
    api, source, dag, artifact, receipt = make_run(tmp_path)
    if what == "artifact":
        path = dag.root_dir / "objects" / artifact["sha256"]
        path.chmod(0o600)
        path.write_bytes(b"tampered")
    else:
        path = next((dag.root_dir / "nodes").glob("*.json"))
        if what == "deleted_node":
            path.unlink()
        else:
            node = json.loads(path.read_text())
            node["payload"] = {"forged": True}
            rewrite(path, node)
    report = api.verify_execution(receipt["manifest_path"], receipt["root_hash"])
    assert not report["valid"]
    assert report["errors"]


def test_trusted_root_detects_rewritten_history_even_if_local_hashes_are_recomputed(tmp_path):
    api, source, dag, artifact, receipt = make_run(tmp_path)
    manifest_path = Path(receipt["manifest_path"])
    manifest = json.loads(manifest_path.read_text())
    removed = manifest["node_ids"].pop()
    (dag.root_dir / "nodes" / (removed + ".json")).unlink()
    manifest["node_count"] -= 1
    manifest.pop("root_hash")
    manifest["root_hash"] = hashlib.sha256(api.canonical_json(manifest)).hexdigest()
    rewrite(manifest_path, manifest)
    root_file = dag.root_dir / "root.sha256"
    root_file.chmod(0o600)
    root_file.write_text(manifest["root_hash"] + "\n")
    assert api.verify_execution(manifest_path)["valid"] is True
    report = api.verify_execution(manifest_path, receipt["root_hash"])
    assert report["valid"] is False
    assert any("trusted root" in error for error in report["errors"])


def test_cyclic_and_missing_parent_links_fail_even_after_node_hash_rewrite(tmp_path):
    api, source, dag, artifact, receipt = make_run(tmp_path)
    manifest_path = Path(receipt["manifest_path"])
    manifest = json.loads(manifest_path.read_text())
    old_id = manifest["node_ids"][0]
    path = dag.root_dir / "nodes" / (old_id + ".json")
    node = json.loads(path.read_text())
    node.pop("node_id")
    node["parents"] = [manifest["node_ids"][1]]
    new_id = hashlib.sha256(api.canonical_json(node)).hexdigest()
    node["node_id"] = new_id
    path.unlink()
    (dag.root_dir / "nodes" / (new_id + ".json")).write_text(json.dumps(node))
    manifest["node_ids"][0] = new_id
    manifest.pop("root_hash")
    manifest["root_hash"] = hashlib.sha256(api.canonical_json(manifest)).hexdigest()
    rewrite(manifest_path, manifest)
    root_file = dag.root_dir / "root.sha256"
    root_file.chmod(0o600)
    root_file.write_text(manifest["root_hash"] + "\n")
    report = api.verify_execution(manifest_path)
    assert not report["valid"]
    assert any("parent" in error for error in report["errors"])


def test_node_id_path_traversal_is_rejected(tmp_path):
    api, source, dag, artifact, receipt = make_run(tmp_path)
    manifest_path = Path(receipt["manifest_path"])
    manifest = json.loads(manifest_path.read_text())
    manifest["node_ids"][0] = "../../secret"
    rewrite(manifest_path, manifest)
    report = api.verify_execution(manifest_path)
    assert report["valid"] is False
    assert any("node ID" in error for error in report["errors"])


def test_verifier_rejects_duplicate_json_keys(tmp_path):
    api, source, dag, artifact, receipt = make_run(tmp_path)
    path = Path(receipt["manifest_path"])
    path.chmod(0o600)
    path.write_text('{"root_hash":"first","root_hash":"second"}')
    report = api.verify_execution(path)
    assert not report["valid"]
    assert any("Duplicate" in error for error in report["errors"])


def test_verifier_rejects_symlinked_blob_without_reading_target(tmp_path):
    api, source, dag, artifact, receipt = make_run(tmp_path)
    blob = dag.root_dir / "objects" / artifact["sha256"]
    blob.unlink()
    blob.symlink_to(source)
    assert api.verify_execution(receipt["manifest_path"])["valid"] is False


def test_all_filesystem_entry_points_require_absolute_paths(tmp_path, monkeypatch):
    api = provenance()
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValueError, match="absolute"):
        api.ExecutionDAG(Path("ledger"))
    with pytest.raises(ValueError, match="absolute"):
        api.hash_artifact(Path("input.csv"))
    assert not api.verify_execution(Path("manifest.json"))["valid"]


def test_artifact_containing_ledger_is_rejected(tmp_path):
    api = provenance()
    dag = api.ExecutionDAG(tmp_path / "ledger")
    with pytest.raises(ValueError, match="overlap"):
        dag.add_artifact(tmp_path)


def test_parallel_appends_have_unique_contiguous_sequence_numbers(tmp_path):
    api = provenance()
    dag = api.ExecutionDAG(tmp_path / "ledger")
    with ThreadPoolExecutor(max_workers=4) as pool:
        ids = list(pool.map(lambda index: dag.append("tool_call", {"index": index}), range(20)))
    assert len(set(ids)) == 20
    receipt = dag.finalize()
    assert receipt["node_count"] == 20
    assert api.verify_execution(receipt["manifest_path"], receipt["root_hash"])["valid"]


def test_analysis_fingerprint_is_stable_but_sensitive_to_scientific_inputs(tmp_path):
    api = provenance()
    arguments = dict(inputs={"counts": "a" * 64}, code={"tree": "b" * 64},
                     environment={"python": "3.12"}, parameters={"spline_df": 4},
                     seeds={"numpy": 42}, method={"name": "NB", "version": "1"})
    first = api.analysis_fingerprint(**arguments)
    assert len(first) == 64
    assert first == api.analysis_fingerprint(**dict(reversed(list(arguments.items()))))
    changed = {**arguments, "parameters": {"spline_df": 5}}
    assert first != api.analysis_fingerprint(**changed)
    dag = api.ExecutionDAG(tmp_path / "ledger")
    dag.append("analysis", arguments)
    receipt = dag.finalize(analysis_fingerprint=first)
    assert receipt["analysis_fingerprint"] == first
    assert api.verify_execution(receipt["manifest_path"], receipt["root_hash"])["valid"]


def test_readiness_never_mislabels_self_attestation_as_clinical_release():
    api = provenance()
    missing = api.evaluate_controls({})
    assert missing["clinical_release_allowed"] is False
    assert missing["status"] == "not_qualified"
    evidence = {key: {"status": "verified", "reference": "controlled-evidence-001"}
                for key in missing["required_controls"]}
    report = api.evaluate_controls(evidence)
    assert report["missing_controls"] == []
    assert report["status"] == "ready_for_independent_review"
    assert report["clinical_release_allowed"] is False
    assert report["compliance_certified"] is False


def test_unreadable_directories_are_not_silently_hashed_as_empty(tmp_path, monkeypatch):
    api = provenance()
    source = tmp_path / "unreadable"
    source.mkdir()
    original_scandir = api.os.scandir
    def denied(path):
        if Path(path) == source:
            raise PermissionError("Synthetic permission denial")
        return original_scandir(path)
    monkeypatch.setattr(api.os, "scandir", denied)
    with pytest.raises(PermissionError):
        api.hash_artifact(source)


@pytest.mark.parametrize("field,value", [("size_bytes", None), ("name", ".")])
def test_invalid_artifact_metadata_fails_even_when_manifest_is_rehashed(tmp_path, field, value):
    api, source, dag, artifact, receipt = make_run(tmp_path)
    manifest_path = Path(receipt["manifest_path"])
    manifest = json.loads(manifest_path.read_text())
    manifest["artifacts"][0][field] = value
    manifest.pop("root_hash")
    manifest["root_hash"] = hashlib.sha256(api.canonical_json(manifest)).hexdigest()
    rewrite(manifest_path, manifest)
    root_file = dag.root_dir / "root.sha256"
    root_file.chmod(0o600)
    root_file.write_text(manifest["root_hash"] + "\n")
    assert api.verify_execution(manifest_path)["valid"] is False


def test_artifact_content_digest_is_independent_of_source_location(tmp_path):
    api = provenance()
    first, second = tmp_path / "one", tmp_path / "two"
    first.mkdir()
    second.mkdir()
    (first / "chunk").write_bytes(b"same")
    (second / "chunk").write_bytes(b"same")
    assert api.hash_artifact(first)["sha256"] == api.hash_artifact(second)["sha256"]


def test_reference_data_licensing_is_an_explicit_qualification_control():
    api = provenance()
    report = api.evaluate_controls({})
    assert "reference_data_licensing" in report["required_controls"]
    assert "reference_data_licensing" in report["missing_controls"]
    assert report["clinical_release_allowed"] is False
