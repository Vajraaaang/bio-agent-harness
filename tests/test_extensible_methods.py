"""Synthetic checks for execution, scientific review and canonical acceptance."""
import importlib
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from devharness_core.provenance import hash_artifact


def service(tmp_path):
    api = importlib.import_module("devbench.analysis.methods")
    return api.MethodService(tmp_path / "workspace")


def custom(tmp_path, *, invariants=(), comparator="json"):
    source = tmp_path / "source.py"
    source.write_text("# A previously unknown user implementation\n")
    lock = tmp_path / "environment.lock"
    lock.write_text("fixture-runtime=1\n")
    return {
        "format": "devharness.method/1", "method_id": "custom.unknown", "name": "Unknown QC",
        "lifecycle": "custom", "implementation_trust": "unreviewed",
        "inputs": {"dataset": {"artifact_type": "json", "required": True}},
        "parameters": {"type": "object", "additionalProperties": False,
                       "properties": {"threshold": {"type": "number", "exclusiveMinimum": 0}},
                       "required": ["threshold"]},
        "outputs": {"dataset": {"artifact_type": "json", "path": "/outputs/result.json", "required": True}},
        "implementation": {"kind": "oci", "source_sha256": hash_artifact(source)["sha256"],
                           "entrypoint": ["python", "/method/main.py"]},
        "environment": {"image": "fixture.invalid/python@sha256:" + "a" * 64,
                        "lock_sha256": hash_artifact(lock)["sha256"]},
        "execution": {"cpu": 1, "memory_mb": 256, "timeout_seconds": 5, "pids": 32,
                      "max_output_bytes": 1000000, "network": "deny"},
        "validation": {"comparator": comparator, "invariants": list(invariants),
                       "counts_layer": "X", "qc_metrics": []},
        "provenance": {"origin": "test_fixture"},
    }, source, lock


class FixtureExecutor:
    """Test-only executor; never exposed through the public CLI."""
    def execute(self, contract, inputs, parameters, source, lock, output_dir):
        (output_dir / "result.json").write_text(json.dumps({"threshold": parameters["threshold"]}))
        return {"exit_code": 0, "runtime": "synthetic_fixture"}


def prepared(tmp_path):
    api = service(tmp_path)
    contract, source, lock = custom(tmp_path)
    method = api.register_method(contract, source=source, environment_lock=lock, actor="author")
    data = tmp_path / "data.json"
    data.write_text('{"cells": 10}')
    artifact = api.import_artifact(data, artifact_type="json", actor="author")
    analysis = api.create_analysis("Synthetic study", actor="researcher")
    branch = api.create_branch(analysis["analysis_id"], analysis["revision_id"], actor="researcher")
    step = api.add_step(branch["branch_id"], method["version_id"], {"dataset": artifact["artifact_id"]},
                        {"threshold": 4}, actor="researcher")
    return api, analysis, branch, step, data, source


def test_custom_run_does_not_approve_itself_and_replays_retained_inputs(tmp_path):
    api, analysis, branch, step, data, source = prepared(tmp_path)
    data.write_text("changed original input")
    source.write_text("changed original source")
    run = api.run_step(step["step_id"], actor="researcher", executor=FixtureExecutor())
    assert run["status"] == "succeeded"
    assert run["validation"]["passed"] is True
    assert api.analysis(analysis["analysis_id"])["revision_id"] == analysis["revision_id"]
    proposal = api.propose(run["run_id"], actor="researcher", rationale="Compare the alternative")
    assert proposal["status"] == "pending"
    with pytest.raises(ValueError, match="review"):
        api.approve(proposal["proposal_id"], actor="researcher", rationale="", acknowledge_warnings=True)
    approved = api.approve(proposal["proposal_id"], actor="researcher", rationale="Reviewed the scientific preview",
                           acknowledge_warnings=True)
    assert approved["revision_id"] != analysis["revision_id"]
    assert api.method(step["method_version_id"])["contract"]["implementation_trust"] == "unreviewed"
    repeat = api.reproduce(run["run_id"], actor="researcher", executor=FixtureExecutor())
    assert repeat["run_id"] != run["run_id"]
    assert repeat["reproduces"] == run["run_id"]
    assert {k: api.get("artifact", v)["sha256"] for k, v in repeat["outputs"].items()} == {
        k: api.get("artifact", v)["sha256"] for k, v in run["outputs"].items()}
    assert api.provenance(run["run_id"])["valid"] is True


def test_concurrent_stale_proposals_cannot_both_advance_canonical_plan(tmp_path):
    api, analysis, branch, step, *_ = prepared(tmp_path)
    run = api.run_step(step["step_id"], actor="researcher", executor=FixtureExecutor())
    proposals = [api.propose(run["run_id"], actor="researcher", rationale="Alternative") for _ in range(2)]
    def accept(proposal):
        try:
            return api.approve(proposal["proposal_id"], actor="reviewer", rationale="Checked",
                               acknowledge_warnings=True)["revision_id"]
        except ValueError as error:
            return str(error)
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(accept, proposals))
    assert sum("stale" in item for item in outcomes) == 1
    assert len(api.analysis(analysis["analysis_id"])["steps"]) == 1


@pytest.mark.parametrize("failure", ["missing", "exit", "nonfinite", "oversized", "symlink"])
def test_failed_execution_cannot_publish_outputs_or_proposals(tmp_path, failure):
    api, _, _, step, *_ = prepared(tmp_path)
    class Broken(FixtureExecutor):
        def execute(self, contract, inputs, parameters, source, lock, output_dir):
            target = output_dir / "result.json"
            if failure == "exit":
                super().execute(contract, inputs, parameters, source, lock, output_dir)
                return {"exit_code": 1, "runtime": "fixture"}
            if failure == "nonfinite":
                target.write_text('{"estimate": NaN}')
            if failure == "oversized":
                target.write_bytes(b"x" * 1000001)
            if failure == "symlink":
                target.symlink_to(inputs["dataset"])
            return {"exit_code": 0, "runtime": "fixture"}
    run = api.run_step(step["step_id"], actor="researcher", executor=Broken())
    assert run["status"] == "failed"
    assert run["outputs"] == {}
    assert run["error"]
    with pytest.raises(ValueError, match="succeeded|successful"):
        api.propose(run["run_id"], actor="researcher", rationale="Not allowed")


def test_parameter_validation_precedes_any_execution(tmp_path):
    api, _, branch, step, *_ = prepared(tmp_path)
    with pytest.raises(ValueError):
        api.add_step(branch["branch_id"], step["method_version_id"], step["inputs"],
                     {"threshold": -1}, actor="scientist")


def test_retained_artifact_tampering_is_detected_before_execution(tmp_path):
    api, _, _, step, *_ = prepared(tmp_path)
    artifact = api.artifact_path(step["inputs"]["dataset"])
    artifact.chmod(0o600)
    artifact.write_text("tampered")
    with pytest.raises(ValueError, match="artifact|digest|hash"):
        api.run_step(step["step_id"], actor="scientist", executor=FixtureExecutor())


def test_scientific_preview_detects_count_changes_and_sample_imbalance(tmp_path):
    ad = pytest.importorskip("anndata")
    comparator = importlib.import_module("devbench.analysis.method_validation")
    original = ad.AnnData(np.array([[1, 0], [2, 3], [4, 5], [6, 7]], dtype=float),
                         obs=pd.DataFrame({"sample": ["a", "a", "b", "b"], "total": [1, 5, 9, 13]},
                                          index=["c1", "c2", "c3", "c4"]),
                         var=pd.DataFrame(index=["g1", "g2"]))
    before, after = tmp_path / "before.h5ad", tmp_path / "after.h5ad"
    original.write_h5ad(before)
    original[[0, 1], :].copy().write_h5ad(after)
    policy = {"comparator": "single_cell_qc", "counts_layer": "X", "sample_key": "sample", "qc_metrics": ["total"],
              "invariants": ["observation_ids_unique", "retained_observation_ids_subset_of_input",
                             "variables_unchanged", "retained_count_matrix_unchanged"]}
    preview = comparator.single_cell_preview(before, after, policy)
    assert preview["passed"] is True
    assert preview["removed_ids"] == ["c3", "c4"]
    assert preview["samples"]["b"]["retained"] == 0
    assert preview["warnings"]
    changed = original[[0, 1], :].copy()
    changed.X[0, 0] = 42
    changed.write_h5ad(after)
    invalid = comparator.single_cell_preview(before, after, policy)
    assert invalid["passed"] is False
    assert invalid["invariants"]["retained_count_matrix_unchanged"] is False


def test_oci_command_has_required_isolation_and_no_shell_interpolation(tmp_path):
    executor = importlib.import_module("devbench.analysis.method_executor")
    contract, source, lock = custom(tmp_path)
    for name in ("inputs", "method", "outputs"):
        (tmp_path / name).mkdir()
    command = executor.container_command(contract, tmp_path, "method-fixture")
    assert command[0] == "create"
    for flag in ("--read-only", "--cap-drop=ALL", "--security-opt=no-new-privileges:true", "--network=none",
                 "--pull=never", "--no-healthcheck"):
        assert flag in command
    assert any(item.startswith("--pids-limit=") for item in command)
    assert any(item.startswith("--memory=") for item in command)
    assert "--entrypoint=/bin/sh" in command
    assert command[-1] == "while :; do sleep 1; done"
    assert any(item.startswith("--tmpfs=/outputs:") for item in command)
    assert all("docker.sock" not in item for item in command)


def test_acceptance_includes_unapproved_upstream_steps_and_their_previews(tmp_path):
    api, analysis, branch, step, *_ = prepared(tmp_path)
    first = api.run_step(step["step_id"], actor="scientist", executor=FixtureExecutor())
    second_step = api.add_step(branch["branch_id"], step["method_version_id"], first["outputs"],
                               {"threshold": 5}, actor="scientist")
    second = api.run_step(second_step["step_id"], actor="scientist", executor=FixtureExecutor())
    proposal = api.propose(second["run_id"], actor="scientist", rationale="Review the whole candidate")
    assert [change["run_id"] for change in proposal["changes"]] == [first["run_id"], second["run_id"]]
    assert len(proposal["dependency_previews"]) == 1
    approved = api.approve(proposal["proposal_id"], actor="reviewer", rationale="Reviewed both transformations",
                           acknowledge_warnings=True)
    assert [item["step_id"] for item in approved["steps"]] == [step["step_id"], second_step["step_id"]]


def test_registered_csv_inputs_retain_their_format_suffix(tmp_path, monkeypatch):
    from devharness_server import method_adapter
    api = service(tmp_path)
    method = api.register_dispatcher("audit_expression_quality", actor="scientist")
    source = tmp_path / "counts.csv"
    source.write_text("gene,s1,s2\ng1,1,2\ng2,2,3\n")
    artifact = api.import_artifact(source, artifact_type="file", actor="scientist")
    metadata = tmp_path / "metadata.csv"
    metadata.write_text("sample_id,embryonic_age\ns1,1\ns2,2\n")
    meta = api.import_artifact(metadata, artifact_type="table", actor="scientist")
    analysis = api.create_analysis("Registered methods", actor="scientist")
    branch = api.create_branch(analysis["analysis_id"], analysis["revision_id"], actor="scientist")
    step = api.add_step(branch["branch_id"], method["version_id"],
                        {"counts": artifact["artifact_id"], "metadata": meta["artifact_id"]}, {}, actor="scientist")
    def check_contract(contract, inputs, params, output_dir):
        assert inputs["counts"].suffix == ".csv"
        assert inputs["counts"].read_bytes() == source.read_bytes()
        (output_dir / "result.json").write_text('{"checked":true}')
        return {"exit_code": 0, "runtime": "trusted_dispatcher", "tool_events": []}
    monkeypatch.setattr(method_adapter, "execute_dispatcher", check_contract)
    run = api.run_step(step["step_id"], actor="scientist")
    assert run["status"] == "succeeded", run.get("error")


def test_restart_marks_an_unsealed_dead_run_interrupted(tmp_path):
    api, analysis, branch, step, *_ = prepared(tmp_path)
    class Interrupted(FixtureExecutor):
        def execute(self, *args):
            raise KeyboardInterrupt("simulate lost coordinator")
    with pytest.raises(KeyboardInterrupt):
        api.run_step(step["step_id"], actor="scientist", executor=Interrupted())
    reopened = service(tmp_path)
    with reopened._db() as db:
        run_id = db.execute("SELECT id FROM records WHERE kind='run'").fetchone()[0]
    run = reopened.get("run", run_id)
    assert run["status"] == "interrupted"
    assert run["outputs"] == {}
    assert reopened.analysis(analysis["analysis_id"])["revision_id"] == analysis["revision_id"]
    assert reopened.audit()["valid"]


def test_cli_uses_the_same_service(tmp_path, capsys):
    cli = importlib.import_module("devbench.analysis.method_cli")
    args = ["--workspace", str(tmp_path / "workspace"), "--actor", "scientist"]
    assert cli.main([*args, "create-analysis", "--name", "CLI study"]) == 0
    analysis = json.loads(capsys.readouterr().out)
    assert service(tmp_path).analysis(analysis["analysis_id"]) == analysis


def test_workbench_cli_routes_method_operations_without_breaking_existing_modes(tmp_path, capsys):
    from devbench.analysis.workbench import main
    assert main(["methods", "--workspace", str(tmp_path / "workspace"), "create-analysis", "--name", "Integrated"]) == 0
    assert json.loads(capsys.readouterr().out)["name"] == "Integrated"


@pytest.mark.parametrize("mutate_count", [False, True])
def test_h5ad_branch_runs_apply_scientific_validation_before_publication(tmp_path, mutate_count):
    ad = pytest.importorskip("anndata")
    api = service(tmp_path)
    contract, source, lock = custom(tmp_path, comparator="single_cell_qc", invariants=(
        "observation_ids_unique", "retained_observation_ids_subset_of_input",
        "variables_unchanged", "retained_count_matrix_unchanged"))
    contract["inputs"]["dataset"]["artifact_type"] = "anndata"
    contract["outputs"]["dataset"].update(artifact_type="anndata", path="/outputs/result.h5ad")
    contract["validation"].update(sample_key="sample", qc_metrics=["total"])
    method = api.register_method(contract, source=source, environment_lock=lock, actor="scientist")
    data = ad.AnnData(np.array([[1, 2], [2, 3], [3, 4], [100, 100]], dtype=float),
        obs=pd.DataFrame({"sample": ["a", "a", "b", "b"], "total": [3, 5, 7, 200]},
                         index=["c1", "c2", "c3", "c4"]), var=pd.DataFrame(index=["g1", "g2"]))
    data_path = tmp_path / "input.h5ad"
    data.write_h5ad(data_path)
    original_hash = hash_artifact(data_path)["sha256"]
    artifact = api.import_artifact(data_path, artifact_type="anndata", actor="scientist")
    analysis = api.create_analysis("AnnData method", actor="scientist")
    branch = api.create_branch(analysis["analysis_id"], analysis["revision_id"], actor="scientist")
    step = api.add_step(branch["branch_id"], method["version_id"], {"dataset": artifact["artifact_id"]},
                        {"threshold": 4}, actor="scientist")
    class H5Fixture:
        def execute(self, contract, inputs, parameters, source, lock, output_dir):
            candidate = ad.read_h5ad(inputs["dataset"])[[0, 1, 2], :].copy()
            if mutate_count:
                candidate.X[0, 0] = 99
            candidate.write_h5ad(output_dir / "result.h5ad")
            return {"exit_code": 0, "runtime": "synthetic_h5ad_fixture"}
    run = api.run_step(step["step_id"], actor="scientist", executor=H5Fixture())
    assert run["validation"] is not None, run.get("error")
    assert run["validation"]["passed"] is not mutate_count
    assert run["status"] == ("failed" if mutate_count else "succeeded")
    assert bool(run["outputs"]) is not mutate_count
    assert api.analysis(analysis["analysis_id"])["revision_id"] == analysis["revision_id"]
    assert hash_artifact(data_path)["sha256"] == original_hash
