"""P2 acceptance contracts: offline engineering fixtures, never scientific proof.

The injected runner supplies controlled image/task observations; no Docker,
Nextflow, model, network, package installation or biological analysis is run.
The real MethodService and mandatory RNA authority entrypoint remain exercised.
P2 production APIs are intentionally absent when these tests are first written.
"""
from copy import deepcopy
import csv
import hashlib
import importlib
import importlib.util
import json
from pathlib import Path

import pytest

from devharness_core.provenance import canonical_json, hash_artifact


WORKFLOW_COMMIT = "30ed7741fc392127156c2fb10cfa3d69d216b54b"
IMAGE = "fixture.invalid/nfcore-differentialabundance@sha256:" + "a" * 64
REQUEST = "1" * 32
SESSION = "11111111-2222-4333-8444-555555555555"
TABLE = ("gene_id\tbaseMean\tlog2FoldChange\tlfcSE\tstat\tpvalue\tpadj\n"
         "g1\t20\t1\t0.3\t3\t0.01\t0.02\n"
         "g2\t10\t0.2\t0.5\t0.4\t0.4\t0.4\n"
         "g3\t0\tNA\tNA\tNA\tNA\tNA\n")


def _api():
    name = "devbench.analysis.community_workflow"
    assert importlib.util.find_spec(name) is not None, (
        "P2 missing: implement the governed single-image community-workflow adapter")
    return importlib.import_module(name)


def _sha(value):
    return hashlib.sha256(canonical_json(value)).hexdigest()


def inventory():
    """Declared synthetic image identity, independently observed by fixture runner."""
    return {"format": "devbench.community-workflow-inventory/1", "image": IMAGE,
        "platform": "linux/amd64", "workflow": {"name": "nf-core/differentialabundance",
            "version": "2.0.0", "commit": WORKFLOW_COMMIT, "tree_sha256": "b" * 64,
            "path": "/opt/nf-core/differentialabundance/main.nf"},
        "nextflow": {"version": "25.10.4", "sha256": "c" * 64,
                     "path": "/usr/local/bin/nextflow"},
        "plugins": [{"id": "nf-schema", "version": "2.5.1", "sha256": "d" * 64}],
        "tools": {"java": {"version": "21.0.7", "sha256": "e" * 64},
            "R": {"version": "4.5.1", "sha256": "f" * 64},
            "DESeq2": {"version": "1.48.0", "sha256": "0" * 64},
            "shinyngs": {"version": "2.4.0", "sha256": "2" * 64},
            "csvtk": {"version": "0.33.0", "sha256": "3" * 64},
            "python": {"version": "3.12.13", "sha256": "5" * 64, "path": "/opt/community/bin/python3.12", "sha256_kind": "file"},
            "pandas": {"version": "2.3.3", "sha256": "6" * 64, "path": "/opt/community/lib/python3.12/site-packages/pandas", "sha256_kind": "tree"},
            "numpy": {"version": "2.2.6", "sha256": "7" * 64, "path": "/opt/community/lib/python3.12/site-packages/numpy", "sha256_kind": "tree"}},
        "execution": {"executor": "local", "container_engine": "none", "network": "deny",
                      "conda": False, "wave": False, "daemon_socket": False},
        "evidence_scope": "synthetic image admission fixture; assets not installed or qualified"}


def plan():
    return {"format": "devbench.community-workflow-plan/1",
        "objective": {"question": "Estimate treated versus control expression differences",
            "source": {"kind": "engineering_fixture", "reference": "P2 offline test author"}},
        "assay": {"kind": "raw_counts", "orientation": "genes_by_samples",
            "gene_id_column": "gene_id", "sample_id_column": "sample",
            "provenance": "direct_counts"},
        "design": {"condition": "condition", "reference": "control", "target": "treated",
            "blocking": [], "biological_unit": "donor", "formula": "~ condition"},
        "statistics": {"normalization": "ratio", "test": "Wald", "adjustment": "BH",
            "alpha": 0.05, "independent_filtering": False, "shrinkage": "none",
            "fit_type": "parametric", "filtering_min_abundance": 0,
            "filtering_min_samples": 1, "seed": 17},
        "resources": {"cpu": 1, "memory_mb": 2048, "timeout_seconds": 30, "pids": 128,
            "scratch_bytes": 16_000_000, "max_output_bytes": 1_000_000, "max_retries": 0}}


def _raw_outputs(folder, *, table=TABLE, engine_status="completed", engine_exit=0,
                 session=SESSION, workflow_commit=WORKFLOW_COMMIT, cached=False):
    folder = Path(folder)
    result = {"format": "devbench.community-workflow-result/1",
        "engine_status": engine_status, "engine_exit_code": engine_exit,
        "nextflow_session_id": session, "workflow_commit": workflow_commit,
        "contrast": {"variable": "condition", "reference": "control", "target": "treated"},
        "method": "DESeq2", "test": "Wald", "adjustment": "BH", "shrinkage": "none",
        "gene_statistics": [
            {"gene_id": "g1", "log2FoldChange": 1.0, "pvalue": 0.01, "padj": 0.02},
            {"gene_id": "g2", "log2FoldChange": 0.2, "pvalue": 0.4, "padj": 0.4},
            {"gene_id": "g3", "log2FoldChange": None, "pvalue": None, "padj": None}],
        "scientific_qualification": "not_established", "clinical_release_allowed": False}
    (folder / "result.json").write_bytes(canonical_json(result))
    (folder / "gene-statistics.tsv").write_text(table)
    (folder / "task-evidence.json").write_bytes(canonical_json({
        "format": "devbench.community-task-evidence/1", "nextflow_session_id": session,
        "attempts": [{"task": "DESEQ2_DIFFERENTIAL", "attempt": 1,
            "status": "CACHED" if cached else ("COMPLETED" if engine_exit == 0 else "FAILED"),
            "exit": engine_exit, "stderr": "" if engine_exit == 0 else "fixture task failed"}]}))
    files = {name: hash_artifact(folder / name)["sha256"] for name in (
        "result.json", "gene-statistics.tsv", "task-evidence.json")}
    (folder / "execution-manifest.json").write_bytes(canonical_json({
        "format": "devbench.community-execution-manifest/1", "inventory_sha256": _sha(inventory()),
        "plan_sha256": _sha(plan()), "nextflow_session_id": session,
        "workflow_commit": workflow_commit, "artifacts": files, "cache_reuse": cached,
        "execution_scope": "offline controlled runner fixture"}))


def _rehash_outputs(folder):
    """Keep corruption fixtures internally hashed, isolating semantic validation."""
    target = folder / "execution-manifest.json"
    manifest = json.loads(target.read_bytes())
    manifest["artifacts"] = {name: hash_artifact(folder / name)["sha256"]
                             for name in manifest["artifacts"]}
    target.write_bytes(canonical_json(manifest))


class FixtureRunner:
    """Controlled trusted executor seam; it never calls a shell or external tool.

    set_observer receives a controller-owned durable lifecycle sink. Events are
    emitted before the corresponding simulated effect. inspect_run/cancel_run
    are controlled observations, not caller claims admitted from task files.
    """
    def __init__(self, *, mode="success", observed_inventory=None):
        self.mode, self.calls, self.inspect_calls, self.cancel_calls = mode, 0, 0, 0
        self.observed_inventory = deepcopy(inventory() if observed_inventory is None else observed_inventory)
        self.observer, self.on_started, self.observed_run = None, None, None
        self.contracts, self.bound_inputs = [], []

    def set_observer(self, observer):
        self.observer = observer

    def inspect_image(self, image):
        self.inspect_calls += 1
        if self.mode == "missing_image":
            raise FileNotFoundError("No admitted community-workflow image is installed")
        return {"repo_digests": [IMAGE], "image_id": "sha256:" + "4" * 64,
                "platform": "linux/amd64", "inventory": deepcopy(self.observed_inventory)}

    def execute(self, contract, inputs, parameters, source, lock, output_dir):
        self.calls += 1
        self.contracts.append(deepcopy(contract))
        self.bound_inputs.append({name: Path(path).read_bytes() for name, path in inputs.items()})
        assert Path(source).is_absolute() and Path(lock).is_absolute()
        assert contract["implementation"]["kind"] == "oci"
        assert contract["execution"]["network"] == "deny"
        run_id = Path(output_dir).parent.name
        self.observed_run = {"run_id": run_id, "container_name": "devharness-method-" + run_id,
            "container_id": "5" * 64, "image": IMAGE, "inventory_sha256": _sha(inventory()),
            "state": "running", "nextflow_session_id": SESSION}
        assert callable(self.observer), "Adapter must install durable lifecycle observation before dispatch"
        for event in ("container_create_intent", "container_created", "engine_session_started"):
            self.observer({"event": event, **deepcopy(self.observed_run)})
        if self.on_started is not None:
            self.on_started()
        code = 124 if self.mode == "timeout" else (2 if self.mode == "failed" else 0)
        if self.observed_run["state"] == "cancelled":
            code = 143
        _raw_outputs(output_dir, engine_status="completed" if code == 0 else "failed",
                     engine_exit=code, cached=self.mode == "cached")
        if self.mode == "malformed":
            (Path(output_dir) / "gene-statistics.tsv").write_text(TABLE.replace("0.01\t0.02", "0.01\t0.001"))
            result = json.loads((Path(output_dir) / "result.json").read_bytes())
            result["gene_statistics"][0]["padj"] = 0.001
            (Path(output_dir) / "result.json").write_bytes(canonical_json(result))
            _rehash_outputs(Path(output_dir))
        if self.mode == "extra_output":
            (Path(output_dir) / "undeclared.txt").write_text("unadmitted output")
        if self.mode == "coordinator_loss":
            raise KeyboardInterrupt("simulated coordinator loss after engine session start")
        self.observed_run["state"] = "completed" if code == 0 else (
            "cancelled" if code == 143 else "failed")
        self.observed_run["engine_exit_code"] = code
        return {"exit_code": code, "runtime": "controlled_oci_fixture", "stdout": "",
            "stderr": "" if code == 0 else "fixture task failed",
            "container_name": self.observed_run["container_name"],
            "container_id": self.observed_run["container_id"]}

    def inspect_run(self, binding):
        return deepcopy(self.observed_run)

    def cancel_run(self, binding):
        self.cancel_calls += 1
        assert binding["container_id"] == self.observed_run["container_id"]
        assert binding["container_name"] == self.observed_run["container_name"]
        self.observed_run["state"] = "cancelled"
        return deepcopy(self.observed_run)


def _prepared(tmp_path, *, runner=None, request_plan=None, matrix=None, metadata=None):
    api = _api()
    from devbench.analysis.methods import MethodService
    methods = MethodService(tmp_path / "workspace")
    matrix_path, metadata_path = tmp_path / "counts.json", tmp_path / "samples.json"
    matrix_rows = list(csv.reader((matrix or
        "gene_id,s1,s2,s3,s4\ng1,10,12,20,22\ng2,8,10,10,12\ng3,0,0,0,0\n").splitlines()))
    metadata_rows = list(csv.DictReader((metadata or
        "sample,donor,condition\ns1,d1,control\ns2,d2,control\ns3,d3,treated\ns4,d4,treated\n").splitlines()))
    matrix_path.write_bytes(canonical_json({"gene_ids": [row[0] for row in matrix_rows[1:]],
        "sample_ids": matrix_rows[0][1:], "matrix": [[int(value) for value in row[1:]] for row in matrix_rows[1:]]}))
    metadata_path.write_bytes(canonical_json({"samples": [
        {"sample_id": row["sample"], **{key: value for key, value in row.items() if key != "sample"}}
        for row in metadata_rows]}))
    refs = {key: methods.import_artifact(path, artifact_type="json", actor="fixture-author")["artifact_id"]
            for key, path in (("matrix", matrix_path), ("metadata", metadata_path))}
    study = methods.create_analysis("P2 synthetic count comparison", actor="fixture-author")
    branch = methods.create_branch(study["analysis_id"], study["revision_id"], actor="fixture-author")
    runner = runner or FixtureRunner()
    adapter = api.CommunityWorkflowService(methods, inventory=inventory(), executor=runner)
    method = adapter.register_method(actor="fixture-author")
    step = adapter.prepare_step(branch["branch_id"], method_version_id=method["version_id"],
        inputs=refs, plan=plan() if request_plan is None else request_plan, actor="fixture-author")
    return api, adapter, methods, runner, step, study, (matrix_path, metadata_path), {}


def _authorize(prepared, *, advance=True):
    """Use real mandatory graph stages; never manufacture a dispatch capability."""
    from devbench.analysis.method_planner import ScientificMethodPlanner
    from devbench.analysis.workflow_entrypoints import workflow_action
    from tests.support.workflow_science_fixtures import RecordingScientificRuntime
    from tests.test_method_planner import Selector, candidate, trial_spec
    _, _, methods, _, step, _, _, context = prepared
    if context:
        return context
    refs = [methods.get("artifact", step["inputs"][name]) for name in ("matrix", "metadata")]
    binding = {"format": "devbench.bulk-rna-binding/1", "matrix_artifact_id": refs[0]["artifact_id"],
        "metadata_artifact_id": refs[1]["artifact_id"], "declarations": {
            "format": "devbench.bulk-rna-input/1", "representation": "raw_counts", "axes": ["gene", "sample"],
            "provenance": {"origin": "engineering_fixture"}, "biological_independence": None,
            "metadata_roles": {"condition": "condition", "biological_unit": "donor"}}}
    planner = ScientificMethodPlanner(methods)
    state = planner.create({"scientific_objective": plan()["objective"]["question"],
        "computational_task": "bulk RNA count comparison"},
        {"observed": {}, "inferred": {}, "unknowns": [],
         "artifact_ids": [ref["artifact_id"] for ref in refs]},
        actor="fixture-author", data_classification="synthetic", rna_input_contract=binding)
    scientific_runtime = RecordingScientificRuntime()
    item = candidate(methods.method(step["method_version_id"])["contract"]["name"])
    item["assumptions"], item["parameter_candidates"] = [], []
    item["method_family"], item["expected_inputs"] = "differential_expression", ["matrix", "metadata"]
    spec = trial_spec(step)
    name = item["name"]
    spec.update(id="p2-count-comparison", candidates=[name],
        inputs={name: deepcopy(step["inputs"])}, parameters={name: deepcopy(step["parameters"])},
        resource_limits={"memory_mb": 2048, "timeout_seconds": 30, "max_runs": 1})
    selector = Selector({"candidates": [item]}, {"bindings": [{"candidate": name,
        "method_version_id": step["method_version_id"], "discovery": None}]}, {"experiments": [spec]})
    context.update(planner=planner, session_id=state["session_id"], experiment_id=spec["id"],
                   scientific_workflow=scientific_runtime)
    if advance:
        for node in ("discovery", "generate", "candidate_review", "resolve", "implementation", "design", "design_review"):
            workflow_action(planner, state["session_id"], node, actor="fixture-author", selector=selector,
                version_ids=[step["method_version_id"]], scientific_workflow=scientific_runtime)
        draft = planner.get(state["session_id"])["trial_designs"][-1]["proposals"][0]
        frozen = workflow_action(planner, state["session_id"], "declare", actor="fixture-author",
            branch_id=step["branch_id"], spec=draft, scientific_workflow=scientific_runtime)
        declared_step = methods.get("step", frozen["step_ids"][name])
        step.clear()
        step.update(declared_step)
    return context


def _run(prepared, **kwargs):
    _, adapter, _, _, step, *_ = prepared
    context = _authorize(prepared)
    return adapter.run(step["step_id"], request_id=REQUEST, actor="operator",
                       allow_data_execution=True, **context, **kwargs)


def test_p2_registers_real_immutable_oci_method_with_retained_wrapper(tmp_path):
    _, adapter, methods, runner, step, *_ = _prepared(tmp_path)
    method = methods.method(step["method_version_id"])
    assert method["contract"]["implementation"]["kind"] == "oci"
    assert method["contract"]["environment"]["image"] == IMAGE
    assert method["contract"]["implementation_trust"] == "unreviewed"
    assert methods.artifact_path(method["source_artifact"]).is_file()
    assert methods.artifact_path(method["environment_artifact"]).is_file()
    assert runner.calls == 0


@pytest.mark.parametrize("change", [
    ("workflow", "commit", "f" * 40), ("workflow", "version", "dev"),
    ("nextflow", "version", "latest"), ("execution", "executor", "slurm"),
    ("execution", "container_engine", "docker"), ("execution", "network", "allow"),
    ("execution", "conda", True), ("execution", "wave", True),
    ("execution", "daemon_socket", True)])
def test_p2_inventory_cannot_widen_the_approved_workflow_or_execution_policy(tmp_path, change):
    api = _api()
    from devbench.analysis.methods import MethodService
    supplied = inventory()
    supplied[change[0]][change[1]] = change[2]
    methods = MethodService(tmp_path / "workspace")
    runner = FixtureRunner()
    with pytest.raises((ValueError, PermissionError)):
        api.CommunityWorkflowService(methods, inventory=supplied, executor=runner).register_method(actor="author")
    assert runner.calls == 0


@pytest.mark.parametrize("mutation", ["workflow_tree", "plugin", "tool", "image_digest"])
def test_p2_independent_image_inventory_drift_refuses_before_engine_dispatch(tmp_path, mutation):
    observed = inventory()
    if mutation == "workflow_tree":
        observed["workflow"]["tree_sha256"] = "9" * 64
    elif mutation == "plugin":
        observed["plugins"][0]["sha256"] = "9" * 64
    elif mutation == "tool":
        observed["tools"]["DESeq2"]["sha256"] = "9" * 64
    else:
        observed["image"] = IMAGE.replace("a" * 64, "9" * 64)
    prepared = _prepared(tmp_path, runner=FixtureRunner(observed_inventory=observed))
    with pytest.raises((ValueError, PermissionError)):
        _run(prepared)
    assert prepared[3].calls == 0


def test_p2_absent_admitted_image_is_dependency_refusal_without_install_or_dispatch(tmp_path):
    prepared = _prepared(tmp_path, runner=FixtureRunner(mode="missing_image"))
    with pytest.raises((FileNotFoundError, ValueError), match="image|admit|install"):
        _run(prepared)
    assert prepared[3].calls == 0
    assert prepared[2].list_records("run") == []


def test_p2_runtime_authority_is_required_even_for_a_prepared_frozen_step(tmp_path):
    _, adapter, methods, runner, step, *_ = _prepared(tmp_path)
    with pytest.raises(PermissionError):
        adapter.run(step["step_id"], request_id=REQUEST, actor="operator")
    assert runner.calls == 0 and methods.list_records("run") == []


@pytest.mark.parametrize("authorization", ["bool_only", "copied_json", "skipped_stages", "wrong_trial", "wrong_step"])
def test_p2_actual_adapter_route_cannot_bypass_mandatory_graph_authority(tmp_path, authorization):
    prepared = _prepared(tmp_path)
    _, adapter, methods, runner, step, *_ = prepared
    context = {} if authorization == "bool_only" else dict(
        _authorize(prepared, advance=authorization != "skipped_stages"))
    if authorization == "copied_json":
        context["scientific_workflow"] = {"status": "accepted", "allow_data_execution": True,
            "format": "test.offline-engineering-science-runtime/1"}
    elif authorization == "wrong_trial":
        context["experiment_id"] = "unfrozen-other-trial"
    elif authorization == "wrong_step":
        other = methods.add_step(step["branch_id"], step["method_version_id"], step["inputs"],
                                 step["parameters"], actor="fixture-author")
        step = other
    with pytest.raises((PermissionError, ValueError, TypeError)):
        adapter.run(step["step_id"], request_id=REQUEST, actor="operator",
                    allow_data_execution=True, **context)
    assert runner.calls == 0 and methods.list_records("run") == []


@pytest.mark.parametrize("mutation", ["normalized", "estimated_without_length", "orientation",
    "same_contrast_levels", "missing_normalization", "automatic_method", "code_formula", "retry"])
def test_p2_unsupported_or_incomplete_scientific_plan_refuses_before_execution(tmp_path, mutation):
    proposed = plan()
    if mutation == "normalized":
        proposed["assay"]["kind"] = "normalized_expression"
    elif mutation == "estimated_without_length":
        proposed["assay"].update(kind="estimated_counts", provenance="salmon")
    elif mutation == "orientation":
        proposed["assay"]["orientation"] = "samples_by_genes"
    elif mutation == "same_contrast_levels":
        proposed["design"]["target"] = "control"
    elif mutation == "missing_normalization":
        del proposed["statistics"]["normalization"]
    elif mutation == "automatic_method":
        proposed["statistics"]["test"] = "auto"
    elif mutation == "code_formula":
        proposed["design"]["formula"] = "~ condition + system('touch /outputs/pwned')"
    else:
        proposed["resources"]["max_retries"] = 4
    with pytest.raises((ValueError, PermissionError)):
        _prepared(tmp_path, request_plan=proposed)


@pytest.mark.parametrize("metadata", [
    "sample,donor,condition\ns1,d1,control\ns1,d2,control\ns3,d3,treated\ns4,d4,treated\n",
    "sample,donor,condition\ns1,d1,control\ns2,d2,control\ns3,d3,treated\n",
    "sample,donor,condition\ns1,d1,control\ns2,d1,control\ns3,d1,treated\ns4,d1,treated\n",
    "sample,donor,condition\ns1,d1,control\ns2,,control\ns3,d3,treated\ns4,d4,treated\n"])
def test_p2_sample_ids_do_not_replace_independent_unit_and_cohort_checks(tmp_path, metadata):
    with pytest.raises(ValueError):
        _prepared(tmp_path, metadata=metadata)


def test_p2_reordered_metadata_aligns_by_identifiers_and_retains_original_bytes(tmp_path):
    metadata = "sample,donor,condition\ns4,d4,treated\ns2,d2,control\ns3,d3,treated\ns1,d1,control\n"
    prepared = _prepared(tmp_path, metadata=metadata)
    run = _run(prepared)
    assert run["status"] == "succeeded", run
    retained = json.loads(prepared[3].bound_inputs[0]["metadata"])
    assert [row["sample_id"] for row in retained["samples"]] == ["s4", "s2", "s3", "s1"]
    assert prepared[3].bound_inputs[0]["metadata"] == prepared[6][1].read_bytes()
    assert prepared[2].provenance(run["run_id"])["valid"] is True


def test_p2_public_adapter_calls_real_method_service_and_preserves_missing_statistics(tmp_path, monkeypatch):
    prepared = _prepared(tmp_path)
    _, adapter, methods, runner, step, study, paths, _ = prepared
    calls = []
    original = methods.run_step
    def recording(step_id, **kwargs):
        calls.append(step_id)
        return original(step_id, **kwargs)
    monkeypatch.setattr(methods, "run_step", recording)
    paths[0].write_text("original path changed after immutable registration")
    run = _run(prepared)
    assert calls == [step["step_id"]] and runner.calls == 1
    assert run["status"] == "succeeded", run
    assert methods.get("run", run["run_id"])["status"] == "succeeded"
    context = prepared[7]
    trial_results = context["planner"].get(context["session_id"])["results"]
    assert len(trial_results) == 1
    assert trial_results[0]["experiment_id"] == context["experiment_id"]
    assert trial_results[0]["provenance"]["run_id"] == run["run_id"]
    from devbench.analysis.workflow_operations import WorkflowOperationLedger
    operations = WorkflowOperationLedger(methods.root / "rna-workflow-operations.sqlite", readonly=True)
    assert operations.get(context["session_id"], "run", 0)["status"] == "completed"
    result = json.loads(methods.artifact_path(run["outputs"]["result"]).read_bytes())
    assert result["gene_statistics"][2]["pvalue"] is None
    assert result["gene_statistics"][2]["padj"] is None
    assert result["scientific_qualification"] == "not_established"
    assert result["clinical_release_allowed"] is False
    assert methods.analysis(study["analysis_id"])["revision_id"] == study["revision_id"]
    assert json.loads(runner.bound_inputs[0]["matrix"])["sample_ids"] == ["s1", "s2", "s3", "s4"]


def test_p2_duplicate_request_after_reconstruction_returns_retained_run_without_dispatch(tmp_path):
    prepared = _prepared(tmp_path)
    api, adapter, methods, runner, step, *_ = prepared
    first = _run(prepared)
    reopened = api.CommunityWorkflowService(methods, inventory=inventory(), executor=runner)
    second = reopened.run(step["step_id"], request_id=REQUEST, actor="operator", allow_data_execution=True,
                          **prepared[7])
    assert first["run_id"] == second["run_id"]
    assert runner.calls == 1
    assert reopened.status(REQUEST)["state"] == "completed"


def test_p2_same_request_with_different_step_conflicts_without_second_dispatch(tmp_path):
    prepared = _prepared(tmp_path)
    _, adapter, methods, runner, step, *_ = prepared
    _run(prepared)
    second = methods.add_step(step["branch_id"], step["method_version_id"], step["inputs"],
        step["parameters"], actor="author")
    with pytest.raises(ValueError, match="request|bind|conflict|different"):
        adapter.run(second["step_id"], request_id=REQUEST, actor="operator", allow_data_execution=True,
                    **prepared[7])
    assert runner.calls == 1


def test_p2_lifecycle_retains_creation_and_session_identity_before_completion(tmp_path):
    prepared = _prepared(tmp_path)
    run = _run(prepared)
    status = prepared[1].status(REQUEST)
    assert status["state"] == "completed" and status["run_id"] == run["run_id"]
    assert status["binding"]["container_id"] == "5" * 64
    assert status["binding"]["nextflow_session_id"] == SESSION
    events = [item["event"] for item in status["events"]]
    assert events.index("container_create_intent") < events.index("container_created")
    assert events.index("container_created") < events.index("engine_session_started")


@pytest.mark.parametrize("mode", ["failed", "malformed", "extra_output", "timeout"])
def test_p2_failed_or_invalid_engine_outputs_never_publish_successful_artifacts(tmp_path, mode):
    prepared = _prepared(tmp_path, runner=FixtureRunner(mode=mode))
    run = _run(prepared)
    assert run["status"] == "failed" and run["outputs"] == {}
    assert prepared[1].status(REQUEST)["state"] == "failed"
    assert prepared[3].calls == 1


def test_p2_failed_task_diagnostics_are_retained_without_successful_publication_or_retry(tmp_path):
    prepared = _prepared(tmp_path, runner=FixtureRunner(mode="failed"))
    run = _run(prepared)
    status = prepared[1].status(REQUEST)
    assert run["status"] == "failed" and run["outputs"] == {}
    assert status["engine_exit_code"] == 2 and status["failure_artifacts"]
    for ref in status["failure_artifacts"]:
        path = prepared[2].artifact_path(ref["artifact_id"])
        assert hash_artifact(path)["sha256"] == ref["sha256"]
    assert any(b"fixture task failed" in prepared[2].artifact_path(ref["artifact_id"]).read_bytes()
               for ref in status["failure_artifacts"])
    repeated = _run(prepared)
    assert repeated["run_id"] == run["run_id"] and prepared[3].calls == 1


def test_p2_cache_reuse_is_reported_separately_from_fresh_computation(tmp_path):
    prepared = _prepared(tmp_path, runner=FixtureRunner(mode="cached"))
    run = _run(prepared)
    manifest = json.loads(prepared[2].artifact_path(run["outputs"]["manifest"]).read_bytes())
    assert manifest["cache_reuse"] is True
    assert manifest.get("independent_numerical_reproduction", False) is False


def test_p2_cancel_requires_current_authority_and_observed_owned_identity(tmp_path):
    prepared = _prepared(tmp_path)
    _, adapter, _, runner, *_ = prepared
    def cancel():
        with pytest.raises(PermissionError):
            adapter.cancel(REQUEST, actor="operator")
        adapter.cancel(REQUEST, actor="operator", allow_data_execution=True, **prepared[7])
    runner.on_started = cancel
    run = _run(prepared)
    assert run["status"] != "succeeded" and run["outputs"] == {}
    assert runner.cancel_calls == 1 and runner.calls == 1
    assert adapter.status(REQUEST)["state"] == "cancelled"


@pytest.mark.parametrize("field,value", [("container_id", "6" * 64),
    ("container_name", "devharness-method-another"), ("image", IMAGE.replace("a" * 64, "7" * 64)),
    ("nextflow_session_id", "99999999-2222-4333-8444-555555555555")])
def test_p2_mismatched_runtime_identity_is_never_signaled(tmp_path, field, value):
    prepared = _prepared(tmp_path)
    _, adapter, _, runner, *_ = prepared
    def reject_unowned():
        runner.observed_run[field] = value
        with pytest.raises((PermissionError, ValueError), match="identity|own|bind|session"):
            adapter.cancel(REQUEST, actor="operator", allow_data_execution=True, **prepared[7])
    runner.on_started = reject_unowned
    _run(prepared)
    assert runner.cancel_calls == 0


def test_p2_controller_loss_reconciles_observation_without_resuming_or_redispatching(tmp_path):
    prepared = _prepared(tmp_path, runner=FixtureRunner(mode="coordinator_loss"))
    api, adapter, methods, runner, step, *_ = prepared
    with pytest.raises(KeyboardInterrupt, match="coordinator loss"):
        _run(prepared)
    reopened = api.CommunityWorkflowService(methods, inventory=inventory(), executor=runner)
    assert reopened.status(REQUEST)["state"] in {"unknown", "reconciliation_required"}
    runner.observed_run["state"] = "missing"
    result = reopened.reconcile(REQUEST, actor="operator")
    assert result["state"] in {"interrupted", "unknown"}
    with pytest.raises((ValueError, PermissionError), match="reconcil|uncertain|interrupt|attempt"):
        reopened.run(step["step_id"], request_id=REQUEST, actor="operator", allow_data_execution=True,
                     **prepared[7])
    assert runner.calls == 1 and runner.cancel_calls == 0


def test_p2_unsealed_completion_or_missing_cache_does_not_become_verified_success(tmp_path):
    prepared = _prepared(tmp_path, runner=FixtureRunner(mode="coordinator_loss"))
    api, _, methods, runner, *_ = prepared
    with pytest.raises(KeyboardInterrupt):
        _run(prepared)
    runner.observed_run.update(state="completed", engine_exit_code=0, cache_present=False)
    reopened = api.CommunityWorkflowService(methods, inventory=inventory(), executor=runner)
    result = reopened.reconcile(REQUEST, actor="operator")
    assert result["state"] != "completed"
    assert runner.calls == 1


@pytest.mark.parametrize("mutation", ["changed_contrast", "wrong_commit", "duplicate_gene",
    "nonfinite", "negative_p", "q_below_p", "false_null_zero", "stale_manifest", "nested_output"])
def test_p2_independent_ingestion_rejects_malformed_stale_or_misbound_outputs(tmp_path, mutation):
    api = _api()
    folder = tmp_path / "outputs"
    folder.mkdir()
    _raw_outputs(folder)
    if mutation == "changed_contrast":
        record = json.loads((folder / "result.json").read_bytes())
        record["contrast"]["reference"], record["contrast"]["target"] = "treated", "control"
        (folder / "result.json").write_bytes(canonical_json(record))
    elif mutation == "wrong_commit":
        _raw_outputs(folder, workflow_commit="8" * 40)
    elif mutation == "duplicate_gene":
        (folder / "gene-statistics.tsv").write_text(TABLE.replace("g2\t", "g1\t"))
    elif mutation == "nonfinite":
        (folder / "gene-statistics.tsv").write_text(TABLE.replace("0.01\t", "Inf\t"))
    elif mutation == "negative_p":
        (folder / "gene-statistics.tsv").write_text(TABLE.replace("0.01\t", "-0.01\t"))
    elif mutation == "q_below_p":
        (folder / "gene-statistics.tsv").write_text(TABLE.replace("0.01\t0.02", "0.01\t0.001"))
    elif mutation == "false_null_zero":
        record = json.loads((folder / "result.json").read_bytes())
        record["gene_statistics"][2].update(log2FoldChange=0, pvalue=0, padj=0)
        (folder / "result.json").write_bytes(canonical_json(record))
    elif mutation == "stale_manifest":
        record = json.loads((folder / "execution-manifest.json").read_bytes())
        record["plan_sha256"] = "9" * 64
        (folder / "execution-manifest.json").write_bytes(canonical_json(record))
    else:
        (folder / "nested").mkdir()
        (folder / "nested" / "not_declared.tsv").write_text(TABLE)
    if mutation != "stale_manifest":
        _rehash_outputs(folder)
    with pytest.raises(ValueError):
        api.ingest_outputs(folder, plan=plan(), inventory=inventory())


def test_p2_ingestion_preserves_na_masks_and_full_testing_family(tmp_path):
    api = _api()
    folder = tmp_path / "outputs"
    folder.mkdir()
    _raw_outputs(folder)
    result = api.ingest_outputs(folder, plan=plan(), inventory=inventory())
    assert [row["gene_id"] for row in result["gene_statistics"]] == ["g1", "g2", "g3"]
    assert result["gene_statistics"][2]["pvalue"] is None
    assert result["gene_statistics"][2]["padj"] is None
    assert len(result["gene_statistics"]) == 3
    assert result["scientific_qualification"] == "not_established"


def test_p2_oversized_diagnostics_are_refused_without_unbounded_collection(tmp_path):
    api = _api()
    folder = tmp_path / "outputs"
    folder.mkdir()
    _raw_outputs(folder)
    with (folder / "task-evidence.json").open("wb") as stream:
        stream.truncate(plan()["resources"]["max_output_bytes"] + 1)
    with pytest.raises(ValueError, match="bound|size|budget|limit"):
        api.ingest_outputs(folder, plan=plan(), inventory=inventory())


def test_p2_real_worker_lossless_conversion_uses_canonical_inputs(tmp_path):
    from devbench.analysis.community_worker import convert_inputs
    prepared = _prepared(tmp_path)
    destination = tmp_path / 'converted'
    destination.mkdir()
    matrix, metadata = convert_inputs(*prepared[6], plan(), destination)
    with (destination / 'counts.tsv').open() as stream:
        rows = list(csv.reader(stream, delimiter='\t'))
    assert rows[0] == ['gene_id', 's1', 's2', 's3', 's4']
    assert [[int(v) for v in row[1:]] for row in rows[1:]] == matrix['matrix']
    assert len(metadata['samples']) == 4


def test_p2_real_oci_profile_has_independent_container_deadline_and_retains_policy(tmp_path):
    from devbench.analysis.method_executor import container_command
    prepared = _prepared(tmp_path)
    contract = prepared[2].method(prepared[4]['method_version_id'])['contract']
    command = container_command(contract, tmp_path, 'owned')
    assert any('time.sleep(30)' in argument for argument in command)
    assert '--network=none' in command and '--read-only' in command
    assert all('noexec' in argument for argument in command if argument.startswith('--tmpfs='))
    assert any('/tmp/community:' in argument and 'size=16000000' in argument for argument in command)


@pytest.mark.parametrize('exit_code', [0, 2, 124])
def test_p2_actual_local_docker_lifecycle_collects_files_even_on_nonzero_exit(tmp_path, monkeypatch, exit_code):
    from devbench.analysis import method_executor
    from devharness_controlled import native_runtime
    import io
    import tarfile
    prepared = _prepared(tmp_path)
    _, _, methods, _, step, *_ = prepared
    method = methods.method(step['method_version_id'])
    workspace = tmp_path / 'real-executor'
    output = workspace / 'outputs'
    output.mkdir(parents=True)
    calls = []
    class Docker:
        def __init__(self): self.env = {}
        def isolate_client(self, path): path.mkdir()
        def command(self, args): return args
        def _call(self, args, **kwargs):
            calls.append(args)
            if args[:2] == ['image','inspect']:
                return {'id':'sha256:'+'4'*64,'digests':[IMAGE],'volumes':{},'os':'linux'}
            if args[0] == 'create': return '5'*64
            if args[0] == 'inspect':
                return {'Id':'5'*64,'Name':'/devharness-method-real-executor',
                    'Config':{'Image':IMAGE,'Labels':{'devbench.run_id':'real-executor',
                    'devbench.inventory_sha256':_sha(inventory()),'devbench.nextflow_session_id':SESSION}},
                    'State':{'Status':'running'}}
            return ''
    monkeypatch.setattr(native_runtime, 'LocalDocker', Docker)
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode='w') as archive:
        member = tarfile.TarInfo('task-evidence.json')
        payload = b'{"diagnostic":"failure bytes"}'
        member.size = len(payload); archive.addfile(member, io.BytesIO(payload))
    def bounded(command, env, timeout, maximum):
        if 'tar' in command: return 0, stream.getvalue(), b''
        return exit_code, b'actual worker stdout', b'actual worker stderr'
    monkeypatch.setattr(method_executor, '_bounded', bounded)
    executor = method_executor.LocalDockerExecutor()
    events=[]
    executor.set_observer(events.append)
    result = executor.execute(method['contract'],
        {k:methods.artifact_path(v) for k,v in step['inputs'].items()},step['parameters'],
        methods.artifact_path(method['source_artifact']),methods.artifact_path(method['environment_artifact']),output)
    assert result['exit_code'] == exit_code
    assert (output/'task-evidence.json').read_bytes() == payload
    assert [event['event'] for event in events][:3] == ['container_create_intent','container_created','container_start_intent']
    assert calls[-1][0] == 'rm'


def test_p2_explicit_image_pins_are_not_the_synthetic_fixture_versions(tmp_path):
    from devbench.analysis.methods import MethodService
    supplied = inventory()
    supplied['platform'] = 'linux/arm64'
    supplied['tools']['java']['version'] = '21.0.9'
    supplied['tools']['R']['version'] = '4.5.2'
    supplied['tools']['DESeq2']['version'] = '1.50.0'
    supplied['tools']['shinyngs']['version'] = '2.6.0'
    supplied['tools']['csvtk']['version'] = '0.34.0'
    api = _api()
    service = api.CommunityWorkflowService(MethodService(tmp_path/'workspace'),inventory=supplied,
        executor=FixtureRunner(observed_inventory=supplied))
    assert service.register_method(actor='author')['contract']['environment']['image'] == IMAGE


def test_p2_resource_profile_is_explicit_frozen_and_bounded_per_method(tmp_path):
    from devbench.analysis.methods import MethodService
    api = _api()
    service = api.CommunityWorkflowService(MethodService(tmp_path/'workspace'),inventory=inventory(),executor=FixtureRunner())
    resources = {**plan()['resources'], 'timeout_seconds':300, 'scratch_bytes':64_000_000,'max_output_bytes':10_000_000}
    method = service.register_method(actor='author',resources=resources)
    assert method['contract']['execution']['timeout_seconds'] == 300
    assert method['contract']['parameters']['properties']['plan']['properties']['resources']['const'] == resources
    from devbench.analysis.method_executor import container_command
    command = container_command(method['contract'],tmp_path,'bounded')
    assert any('time.sleep(300)' in argument for argument in command)
    assert any('size=64000000' in argument for argument in command if '/tmp/community:' in argument)
    with pytest.raises(ValueError):
        service.register_method(actor='author',resources={**resources,'timeout_seconds':3601})


def test_p2_image_inventory_avoids_self_referential_image_digest(monkeypatch):
    from devbench.analysis.method_executor import LocalDockerExecutor
    from devharness_controlled import native_runtime
    installed = {k:v for k,v in inventory().items() if k != 'image'}
    class Docker:
        def _call(self, *args, **kwargs):
            return {'digests':[IMAGE],'id':'sha256:'+'4'*64,'os':'linux','architecture':'amd64',
                'labels':{'devbench.community.inventory':json.dumps(installed)}}
    monkeypatch.setattr(native_runtime,'LocalDocker',Docker)
    assert LocalDockerExecutor().inspect_image(IMAGE)['inventory'] == inventory()


def test_p2_worker_engine_subprocess_is_real_and_timeout_preserves_logs(tmp_path):
    from devbench.analysis.community_worker import run_engine
    import os
    import sys
    assert run_engine([sys.executable,'-c',"import sys; print('started'); print('diagnostic',file=sys.stderr);sys.exit(2)"],
        {'PATH':os.defpath},tmp_path,1) == 2
    assert (tmp_path/'stdout.log').read_text() == 'started\n'
    assert (tmp_path/'stderr.log').read_text() == 'diagnostic\n'
    assert run_engine([sys.executable,'-u','-c',"import time; print('pre-timeout',flush=True);time.sleep(20)"],
        {'PATH':os.defpath},tmp_path,.1) == 124
    assert (tmp_path/'stdout.log').read_text() == 'pre-timeout\n'


def test_p2_publication_captures_p1_typed_cohort_family_and_na_reasons(tmp_path):
    from devbench.analysis.scientific_result_contract import ScientificResult, multiplicity_check
    prepared = _prepared(tmp_path)
    run = _run(prepared)
    assert run['status'] == 'succeeded'
    assert 'typed_result' in run['outputs']
    document = json.loads(prepared[2].artifact_path(run['outputs']['typed_result']).read_bytes())
    typed = ScientificResult.model_validate_json(json.dumps(document))
    assert list(typed.cohort.sample_ids) == ['s1','s2','s3','s4']
    assert list(typed.cohort.biological_unit_ids) == ['d1','d2','d3','d4']
    assert typed.rows[2].missing_reasons['p_value'] == 'upstream_NA_cause_unresolved'
    assert typed.family.failed_hypothesis_policy == 'exclude_failed_hypotheses'
    assert multiplicity_check(typed)['status'] == 'verified'


def test_p2_valid_looking_wrong_bh_family_is_not_published(tmp_path):
    class WrongBH(FixtureRunner):
        def execute(self, contract, inputs, parameters, source, lock, output_dir):
            result=super().execute(contract,inputs,parameters,source,lock,output_dir)
            directory=Path(output_dir)
            table=directory/'gene-statistics.tsv'
            table.write_text(table.read_text().replace('0.01\t0.02','0.01\t0.03'))
            summary=json.loads((directory/'result.json').read_bytes())
            summary['gene_statistics'][0]['padj']=.03
            (directory/'result.json').write_bytes(canonical_json(summary));_rehash_outputs(directory)
            return result
    prepared=_prepared(tmp_path,runner=WrongBH())
    run=_run(prepared)
    assert run['status']=='failed' and run['outputs']=={}


def test_p2_missing_input_feature_cannot_be_laundered_by_self_consistent_outputs(tmp_path):
    class MissingFeature(FixtureRunner):
        def execute(self, contract, inputs, parameters, source, lock, output_dir):
            result=super().execute(contract,inputs,parameters,source,lock,output_dir)
            directory=Path(output_dir)
            table=directory/'gene-statistics.tsv'
            table.write_text(table.read_text().replace('g3\t0\tNA\tNA\tNA\tNA\tNA\n',''))
            summary=json.loads((directory/'result.json').read_bytes()); summary['gene_statistics'].pop()
            (directory/'result.json').write_bytes(canonical_json(summary));_rehash_outputs(directory)
            return result
    run=_run(_prepared(tmp_path,runner=MissingFeature()))
    assert run['status']=='failed' and run['outputs']=={}


def test_p2_output_collection_failure_retains_actual_engine_exit_and_logs(tmp_path, monkeypatch):
    from devbench.analysis import method_executor
    from devharness_controlled import native_runtime
    prepared=_prepared(tmp_path)
    _,_,methods,_,step,*_=prepared
    method=methods.method(step['method_version_id'])
    output=tmp_path/'collect-failed'/'outputs';output.mkdir(parents=True)
    class Docker:
        def __init__(self):self.env={}
        def isolate_client(self,path):path.mkdir()
        def command(self,args):return args
        def _call(self,args,**kwargs):
            if args[:2]==['image','inspect']:return {'id':'sha256:'+'4'*64,'digests':[IMAGE],'volumes':{},'os':'linux'}
            return '5'*64 if args[0]=='create' else ''
    monkeypatch.setattr(native_runtime,'LocalDocker',Docker)
    def bounded(command,env,timeout,maximum):
        if 'tar' in command:raise ValueError('controlled collection failure')
        return 2,b'engine started',b'actual engine failure'
    monkeypatch.setattr(method_executor,'_bounded',bounded)
    executor=method_executor.LocalDockerExecutor();executor.set_observer(lambda event:None)
    with pytest.raises(ValueError) as caught:
        executor.execute(method['contract'],{k:methods.artifact_path(v) for k,v in step['inputs'].items()},step['parameters'],
            methods.artifact_path(method['source_artifact']),methods.artifact_path(method['environment_artifact']),output)
    assert caught.value.execution['exit_code']==2
    assert caught.value.execution['stderr']=='actual engine failure'
    assert 'collection failure' in caught.value.execution['diagnostic_collection_error']


def test_p2_installed_component_hashes_are_bound_to_verified_asset_manifest():
    from devbench.analysis.community_worker import verify_components
    supplied = inventory()
    assets = {'/workflow/main.nf':'1'*64,'/nextflow/dist':'2'*64,
        '/plugin/library.jar':'3'*64,**{'/tool/'+k:'4'*64 for k in supplied['tools']}}
    supplied['workflow'].update(tree_root='/workflow',tree_sha256=_sha({'main.nf':'1'*64}))
    supplied['nextflow'].update(sha256_path='/nextflow/dist',sha256='2'*64)
    supplied['plugins'][0].update(path='/plugin',sha256_kind='tree',sha256=_sha({'library.jar':'3'*64}))
    for key,tool in supplied['tools'].items():tool.update(path='/tool/'+key,sha256_kind='file',sha256='4'*64)
    verify_components(supplied, assets)
    supplied['tools']['DESeq2']['sha256']='9'*64
    with pytest.raises(ValueError, match='component|identity|hash'):
        verify_components(supplied, assets)


def test_p2_retained_method_lock_must_match_current_admission_before_dispatch(tmp_path):
    prepared=_prepared(tmp_path)
    prepared[1].inventory['tools']['R']['sha256']='9'*64
    prepared[3].observed_inventory=deepcopy(prepared[1].inventory)
    with pytest.raises(ValueError,match='lock|admi|inventory'):
        _run(prepared)
    assert prepared[3].calls==0


def test_p2_registered_method_name_cannot_launder_an_unrelated_worker(tmp_path):
    prepared=_prepared(tmp_path)
    _,adapter,methods,_,step,*_=prepared
    method=methods.method(step['method_version_id'])
    contract=deepcopy(method['contract'])
    unrelated=tmp_path/'unrelated-worker.py';unrelated.write_text("print('unrelated worker')\n")
    contract['implementation']['source_sha256']=hash_artifact(unrelated)['sha256']
    alternate=methods.register_method(contract,source=unrelated,
        environment_lock=methods.artifact_path(method['environment_artifact']),actor='author')
    with pytest.raises(ValueError,match='source|worker|admi'):
        adapter.prepare_step(step['branch_id'],method_version_id=alternate['version_id'],inputs=step['inputs'],plan=plan(),actor='author')


def test_p2_explicit_exploratory_rlog_is_supported_and_does_not_change_count_model(tmp_path):
    from devbench.analysis.community_worker import resolve_transform
    proposed=plan()
    proposed['statistics']['exploratory_transformation']={'method':'rlog','source':{'kind':'engineering_fixture','reference':'Pinned rlog control for80gene execution compatibility; not biological method selection'}}
    prepared=_prepared(tmp_path,request_plan=proposed)
    resolved=resolve_transform(proposed)
    assert resolved['parameter']=='deseq2_vs_method' and resolved['value']=='rlog'
    assert resolved['source']==proposed['statistics']['exploratory_transformation']['source']
    assert prepared[4]['parameters']['plan']['statistics']['test']=='Wald'
    assert prepared[4]['parameters']['plan']['statistics']['normalization']=='ratio'
    assert proposed['statistics']['fit_type']=='parametric'
    default=resolve_transform(plan())
    assert default['value']=='vst' and default['source']['kind']=='inherited_upstream_default'
    assert 'exploratory_transformation' not in plan()['statistics']


def test_p2_explicit_transform_output_resolution_must_match_frozen_plan(tmp_path):
    api=_api()
    proposed=plan()
    proposed['statistics']['exploratory_transformation']={'method':'rlog','source':{'kind':'engineering_fixture','reference':'80gene fixture compatibility'}}
    folder=tmp_path/'outputs';folder.mkdir();_raw_outputs(folder)
    manifest=json.loads((folder/'execution-manifest.json').read_bytes())
    manifest['plan_sha256']=_sha(proposed)
    manifest['resolved_configuration']={'deseq2_vs_method':{'parameter':'deseq2_vs_method','value':'vst','source':{'kind':'engineering_fixture','reference':'wrong transform'}}}
    (folder/'execution-manifest.json').write_bytes(canonical_json(manifest))
    with pytest.raises(ValueError,match='transform|configuration|resolution'):
        api.ingest_outputs(folder,plan=proposed,inventory=inventory())


def test_p2_late_engine_observation_cannot_erase_accepted_cancellation(tmp_path):
    class LateObservation(FixtureRunner):
        def execute(self,*args):
            result=super().execute(*args)
            self.on_late()
            self.observer({'event':'engine_session_started',**deepcopy(self.observed_run)})
            return result
    runner=LateObservation();prepared=_prepared(tmp_path,runner=runner)
    runner.on_late=lambda:prepared[1].cancel(REQUEST,actor='operator',allow_data_execution=True,**prepared[7])
    run=_run(prepared)
    assert run['status']=='failed' and run['outputs']=={}
    assert prepared[1].status(REQUEST)['state']=='cancelled'


def test_p2_cancellation_after_validation_before_publication_blocks_all_artifacts(tmp_path,monkeypatch):
    import devbench.analysis.methods as owning
    prepared=_prepared(tmp_path)
    original=owning.validate_outputs
    def validated(*args):
        value=original(*args)
        prepared[1].cancel(REQUEST,actor='operator',allow_data_execution=True,**prepared[7])
        return value
    monkeypatch.setattr(owning,'validate_outputs',validated)
    run=_run(prepared)
    assert run['status']=='failed' and run['outputs']=={}
    assert prepared[1].status(REQUEST)['state']=='cancelled'
    assert not [a for a in prepared[2].list_records('artifact') if a['generated_by']==run['run_id']]


def test_p2_cancellation_after_completed_publication_is_honestly_already_completed(tmp_path):
    prepared=_prepared(tmp_path);run=_run(prepared)
    result=prepared[1].cancel(REQUEST,actor='operator',allow_data_execution=True,**prepared[7])
    assert result['state']=='completed' and result['cancellation_outcome']=='already_completed'
    assert prepared[3].cancel_calls==0 and run['status']=='succeeded'


def test_p2_engine_code_is_not_invented_from_preflight_worker_exit(tmp_path):
    class BeforeEngine(FixtureRunner):
        def execute(self,contract,inputs,parameters,source,lock,output_dir):
            self.calls+=1
            self.observer({'event':'container_create_intent','run_id':Path(output_dir).parent.name,
                'container_name':'devharness-method-'+Path(output_dir).parent.name,'image':IMAGE,
                'inventory_sha256':_sha(inventory()),'nextflow_session_id':SESSION})
            return {'exit_code':2,'runtime':'preflight_fixture','stderr':'admission failed before engine'}
    prepared=_prepared(tmp_path,runner=BeforeEngine());run=_run(prepared)
    status=prepared[1].status(REQUEST)
    assert status['worker_exit_code']==2 and status['engine_exit_code'] is None
    assert status['engine_started'] is False and run['outputs']=={}


def test_p2_parser_failure_preserves_actual_successful_engine_code(tmp_path):
    class ParserFailed(FixtureRunner):
        def execute(self,*args):
            result=super().execute(*args)
            result.update(exit_code=2,engine_exit_code=0,engine_started=True)
            return result
    prepared=_prepared(tmp_path,runner=ParserFailed());run=_run(prepared)
    status=prepared[1].status(REQUEST)
    assert run['status']=='failed' and status['worker_exit_code']==2 and status['engine_exit_code']==0


def test_p2_attempt_two_contradicts_frozen_zero_retry_policy(tmp_path):
    folder=tmp_path/'outputs';folder.mkdir();_raw_outputs(folder)
    path=folder/'task-evidence.json';tasks=json.loads(path.read_bytes());tasks['attempts'][0]['attempt']=2
    path.write_bytes(canonical_json(tasks));_rehash_outputs(folder)
    with pytest.raises(ValueError,match='retry|attempt'):_api().ingest_outputs(folder,plan=plan(),inventory=inventory())


def test_p2_partial_na_retains_finite_estimates_without_guessing_cause(tmp_path):
    folder=tmp_path/'outputs';folder.mkdir()
    table=TABLE.replace('g1\t20\t1\t0.3\t3\t0.01\t0.02','g1\t20\t1\t0.3\t3\tNA\tNA')
    table=table.replace('g2\t10\t0.2\t0.5\t0.4\t0.4\t0.4','g2\t10\t0.2\t0.5\t0.4\t0.4\t0.4')
    _raw_outputs(folder,table=table)
    result=json.loads((folder/'result.json').read_bytes());result['gene_statistics'][0].update(pvalue=None,padj=None)
    (folder/'result.json').write_bytes(canonical_json(result));_rehash_outputs(folder)
    parsed=_api().ingest_outputs(folder,plan=plan(),inventory=inventory())
    assert parsed['gene_statistics'][0]=={'gene_id':'g1','log2FoldChange':1.0,'pvalue':None,'padj':None}


def test_p2_p1_partial_state_is_additive_and_requires_real_partial_missingness():
    from devbench.analysis.scientific_result_contract import ResultRow
    row=ResultRow(feature_id='g1',status='partially_available',effect=1.,standard_error=.3,p_value=None,
        adjusted_p_value=None,missing_reasons={'p_value':'upstream_NA_cause_unresolved','adjusted_p_value':'upstream_NA_cause_unresolved'})
    assert row.effect==1. and row.p_value is None
    with pytest.raises(ValueError):
        ResultRow(feature_id='g1',status='partially_available',effect=1.,standard_error=.3,p_value=.1,adjusted_p_value=.2,missing_reasons={})


def test_p2_simple_upstream_contrast_does_not_include_incompatible_formula_column(tmp_path):
    from devbench.analysis.community_worker import convert_inputs
    prepared=_prepared(tmp_path);out=tmp_path/'conversion';out.mkdir()
    convert_inputs(*prepared[6],plan(),out)
    with (out/'contrasts.csv').open() as f:rows=list(csv.reader(f))
    assert rows==[['id','variable','reference','target'],['comparison','condition','control','treated']]


def test_p2_task_profile_overrides_matching_selectors_and_declares_resource_limits():
    from devbench.analysis.community_worker import build_config
    config=build_config(plan()['resources'])
    assert "withName: '.*'" in config and 'resourceLimits' in config
    assert "memory = '1024 MB'" in config and "time = '22 sec'" in config
    assert "maxRetries = 0" in config and "errorStrategy = 'terminate'" in config


def test_p2_worker_records_real_engine_code_immediately_even_on_timeout(tmp_path):
    from devbench.analysis.community_worker import run_engine
    import sys,os
    assert run_engine([sys.executable,'-c','import time;time.sleep(20)'],{'PATH':os.defpath},tmp_path,.05)==124
    state=json.loads((tmp_path/'engine-state.json').read_bytes())
    assert state['engine_started'] is True and state['engine_exit_code'] is not None
    assert state['outcome']=='timeout' and state['engine_exit_code'] != 124


def test_p2_raw_execution_evidence_keeps_actual_bytes_ids_and_absence(tmp_path):
    from devbench.analysis.community_worker import capture_execution_evidence, parse_trace_text
    import base64
    raw='task_id\thash\tname\tstatus\texit\tattempt\tcpus\tmemory\ttime\terror_action\n1\tab/cdef\tDESEQ2\tCOMPLETED\t0\t1\t1\t1073741824\t22000\t-\n'
    (tmp_path/'trace.tsv').write_text(raw)
    (tmp_path/'stdout.log').write_bytes(b'actual stdout\x00bytes')
    (tmp_path/'params.json').write_text('{"input":"/tmp/community/samples.csv"}')
    (tmp_path/'local.config').write_text('process.executor="local"\n')
    (tmp_path/'invocation.json').write_text(json.dumps({'argv':['nextflow','run','pinned.nf'],'environment':{'NXF_OFFLINE':'true'}}))
    evidence=capture_execution_evidence(tmp_path,maximum=100000)
    assert base64.b64decode(evidence['trace']['content_base64'])==raw.encode()
    assert evidence['logs']['stderr']['status']=='absent'
    assert evidence['actual_resolved_config']['status']=='not_obtained'
    assert evidence['invocation']['argv']==['nextflow','run','pinned.nf']
    normalized=parse_trace_text(raw)
    assert normalized[0]['task_id']=='1' and normalized[0]['hash']=='ab/cdef'
    assert normalized[0]['resources']['memory']['value']=='1073741824'


def _owned_binding():
    return {'run_id':'f'*32,'container_name':'devharness-method-'+'f'*32,'container_id':'5'*64,
        'image':IMAGE,'inventory_sha256':_sha(inventory()),'nextflow_session_id':SESSION}


def test_p2_transport_exception_containing_no_such_is_not_absence(monkeypatch):
    from devbench.analysis.method_executor import LocalDockerExecutor
    from devharness_controlled import native_runtime
    class Docker:
        def _call(self,*args,**kwargs):raise RuntimeError('No such transport endpoint permission')
    monkeypatch.setattr(native_runtime,'LocalDocker',Docker)
    with pytest.raises(RuntimeError,match='transport'):LocalDockerExecutor().inspect_run(_owned_binding())


def test_p2_positive_empty_daemon_listing_establishes_absence(monkeypatch):
    from devbench.analysis.method_executor import LocalDockerExecutor
    from devharness_controlled import native_runtime
    class Docker:
        def _call(self,args,**kwargs):
            assert args[:2]==['container','ls']
            return ''
    monkeypatch.setattr(native_runtime,'LocalDocker',Docker)
    assert LocalDockerExecutor().inspect_run(_owned_binding())['state']=='missing'


def test_p2_response_loss_recovers_id_only_after_owned_observation_without_rerun(tmp_path):
    class LostCreate(FixtureRunner):
        def execute(self,contract,inputs,parameters,source,lock,output_dir):
            self.calls+=1;run_id=Path(output_dir).parent.name
            self.observed_run={**_owned_binding(),'run_id':run_id,'container_name':'devharness-method-'+run_id,'state':'created'}
            event={**self.observed_run,'event':'container_create_intent'};event.pop('container_id')
            self.observer(event)
            raise RuntimeError('daemon created container but response was lost')
    prepared=_prepared(tmp_path,runner=LostCreate());run=_run(prepared)
    assert run['status']=='failed'
    assert prepared[1].status(REQUEST)['state'] in {'unknown','reconciliation_required'}
    reopened=_api().CommunityWorkflowService(prepared[2],inventory=inventory(),executor=prepared[3])
    observed=reopened.reconcile(REQUEST,actor='operator')
    assert observed['binding']['container_id']=='5'*64 and observed['state']!='completed'
    assert prepared[3].calls==1
    cancelled=reopened.cancel(REQUEST,actor='operator',allow_data_execution=True,**prepared[7])
    assert cancelled['state']=='cancelled' and prepared[3].cancel_calls==1


@pytest.mark.parametrize('foreign',['run_id','inventory_sha256','nextflow_session_id','image'])
def test_p2_lost_id_name_reuse_requires_all_ownership_labels(monkeypatch,foreign):
    from devbench.analysis.method_executor import LocalDockerExecutor
    from devharness_controlled import native_runtime
    binding=_owned_binding();binding.pop('container_id')
    actual={**binding,'container_id':'6'*64};actual[foreign]='foreign'
    calls=[]
    class Docker:
        def _call(self,args,**kwargs):
            calls.append(args)
            if args[:2]==['container','ls']:return '6'*64
            return {'Id':actual['container_id'],'Name':'/'+actual['container_name'],
                'Config':{'Image':actual['image'],'Labels':{'devbench.'+k:actual[k] for k in ('run_id','inventory_sha256','nextflow_session_id')}},'State':{'Status':'running'}}
    monkeypatch.setattr(native_runtime,'LocalDocker',Docker)
    with pytest.raises(PermissionError,match='own|identity|bind'):LocalDockerExecutor().cancel_run(binding)
    assert not any(args[0] in {'stop','rm','exec'} for args in calls)


def test_p2_cancellation_collects_live_tmpfs_before_stopping_supervisor(tmp_path,monkeypatch):
    from devbench.analysis import method_executor
    from devharness_controlled import native_runtime
    import io,tarfile
    prepared=_prepared(tmp_path);methods=prepared[2];step=prepared[4];method=methods.method(step['method_version_id'])
    output=tmp_path/('a'*32)/'outputs';output.mkdir(parents=True)
    live={'state':'running','files':{},'binding':None};calls=[]
    class Docker:
        def __init__(self):self.env={}
        def isolate_client(self,path):path.mkdir()
        def command(self,args):return args
        def _call(self,args,**kwargs):
            calls.append(args)
            if args[:2]==['image','inspect']:return {'id':'sha256:'+'4'*64,'digests':[IMAGE],'volumes':{},'os':'linux'}
            if args[0]=='create':return '5'*64
            if args[:2]==['container','ls']:return '5'*64
            if args[0]=='inspect':
                b=live['binding'];return {'Id':b['container_id'],'Name':'/'+b['container_name'],'Config':{'Image':IMAGE,
                    'Labels':{'devbench.'+k:b[k] for k in ('run_id','inventory_sha256','nextflow_session_id')}},'State':{'Status':live['state']}}
            if args[0]=='stop':live['state']='exited';live['files'].clear();return ''
            if args[0]=='exec' and kwargs.get('json_result'):return {'state':'cancelled','worker_exit_code':143}
            return ''
    monkeypatch.setattr(native_runtime,'LocalDocker',Docker)
    executor=method_executor.LocalDockerExecutor()
    def observer(event):live['binding']={k:v for k,v in event.items() if k!='event'}
    executor.set_observer(observer)
    def bounded(command,env,timeout,maximum):
        if 'tar' in command:
            assert live['state']=='running','tmpfs disappeared when supervisor stopped'
            stream=io.BytesIO()
            with tarfile.open(fileobj=stream,mode='w') as archive:
                for name,payload in live['files'].items():
                    member=tarfile.TarInfo(name);member.size=len(payload);archive.addfile(member,io.BytesIO(payload))
            return 0,stream.getvalue(),b''
        live['files']['task-evidence.json']=canonical_json({'diagnostic':'written before stop','nextflow_session_id':live['binding']['nextflow_session_id'],
            'engine_session_observed':True,'engine_state':{'engine_started':True,'engine_exit_code':-15,'outcome':'cancelled'},'worker_exit_code':143})
        outcome=executor.cancel_run(live['binding'])
        assert outcome['state']=='cancelled'
        return 143,b'',b'cancelled'
    monkeypatch.setattr(method_executor,'_bounded',bounded)
    result=executor.execute(method['contract'],{k:methods.artifact_path(v) for k,v in step['inputs'].items()},step['parameters'],
        methods.artifact_path(method['source_artifact']),methods.artifact_path(method['environment_artifact']),output)
    assert result['exit_code']==143 and result['engine_exit_code']==-15
    assert b'written before stop' in (output/'task-evidence.json').read_bytes()
    assert calls[-1][0]=='rm'


@pytest.mark.parametrize('mutation',[None,'raw_hash','normalized_hash','argv','parameters','supplied_config','resolved_claim'])
def test_p2_raw_runtime_evidence_must_correspond_to_normalized_and_frozen_execution(tmp_path,mutation):
    from devbench.analysis.community_worker import capture_execution_evidence,parse_trace_text,build_config,build_parameters
    import base64
    folder=tmp_path/'outputs';folder.mkdir();_raw_outputs(folder)
    source=tmp_path/'runtime';source.mkdir()
    raw='task_id\thash\tname\tstatus\texit\tattempt\tcpus\tmemory\ttime\terror_action\n1\tab/cdef\tDESEQ2_DIFFERENTIAL\tCOMPLETED\t0\t1\t1\t1073741824\t22000\t-\n'
    (source/'trace.tsv').write_text(raw)
    (source/'params.json').write_bytes(canonical_json(build_parameters(plan(),Path('/tmp/community'))))
    (source/'local.config').write_text(build_config(plan()['resources']))
    argv=['/usr/local/bin/nextflow','-C','/tmp/community/local.config','-log','/tmp/community/nextflow.log','run',
        '/opt/nf-core/differentialabundance/main.nf','-offline','-params-file','/tmp/community/params.json','-work-dir','/tmp/community/work']
    (source/'invocation.json').write_text(json.dumps({'argv':argv,'environment':{'NXF_OFFLINE':'true','NXF_UUID':SESSION}}))
    (source/'stdout.log').write_text('actual stdout');(source/'stderr.log').write_text('')
    (source/'nextflow.log').write_text('Session UUID: '+SESSION)
    path=folder/'task-evidence.json';tasks=json.loads(path.read_bytes())
    tasks.update(attempts=parse_trace_text(raw),execution_evidence=capture_execution_evidence(source),
        producer={'name':'Nextflow','version':'25.10.4'},engine_state={'engine_started':True,'engine_exit_code':0,'outcome':'completed'},
        engine_session_observed=True,worker_exit_code=0)
    if mutation=='raw_hash':tasks['execution_evidence']['trace']['captured_sha256']='9'*64
    elif mutation=='normalized_hash':tasks['attempts'][0]['hash']='different'
    elif mutation=='argv':tasks['execution_evidence']['invocation']['argv'][-1]='/unrelated'
    elif mutation in {'parameters','supplied_config'}:
        key='supplied_parameters' if mutation=='parameters' else 'supplied_config'
        capsule=tasks['execution_evidence'][key]
        payload=base64.b64decode(capsule['content_base64']).replace(b'Wald',b'LRT') if mutation=='parameters' else b'process.executor="slurm"'
        capsule.update(content_base64=base64.b64encode(payload).decode(),captured_sha256=hashlib.sha256(payload).hexdigest(),
            complete_sha256=hashlib.sha256(payload).hexdigest(),captured_size_bytes=len(payload),original_size_bytes=len(payload))
    elif mutation=='resolved_claim':tasks['execution_evidence']['actual_resolved_config']={'status':'obtained','source':'copied intended config'}
    path.write_bytes(canonical_json(tasks));_rehash_outputs(folder)
    if mutation is None:
        assert len(_api().ingest_outputs(folder,plan=plan(),inventory=inventory())['gene_statistics'])==3
    else:
        with pytest.raises(ValueError,match='evidence|trace|runtime|config|invocation|parameter'):
            _api().ingest_outputs(folder,plan=plan(),inventory=inventory())


def test_p2_delayed_cancel_ack_preserves_diagnostics_persisted_while_it_waited(tmp_path):
    import threading
    entered=threading.Event();release=threading.Event();errors=[]
    class WaitingCancel(FixtureRunner):
        def cancel_run(self,binding):
            entered.set()
            assert release.wait(10)
            return super().cancel_run(binding)
    runner=WaitingCancel();prepared=_prepared(tmp_path,runner=runner)
    def cancel():
        try:prepared[1].cancel(REQUEST,actor='operator',allow_data_execution=True,**prepared[7])
        except Exception as error:errors.append(error)
    thread=threading.Thread(target=cancel)
    runner.on_started=lambda:(thread.start(),entered.wait(10))
    try:
        run=_run(prepared)
        before=prepared[1].status(REQUEST)['failure_artifacts']
        assert before and run['outputs']=={}
    finally:release.set();thread.join(10)
    assert not thread.is_alive() and not errors
    status=prepared[1].status(REQUEST)
    assert status['state']=='cancelled' and status['failure_artifacts']==before


def test_p2_pre_engine_cancellation_does_not_launch_an_engine(tmp_path,monkeypatch):
    import devbench.analysis.community_worker as worker
    (tmp_path/'cancel.request').write_text('cancel')
    monkeypatch.setattr(worker.subprocess,'Popen',lambda *a,**k:pytest.fail('No engine may launch after this preflight cancellation'))
    assert worker.run_engine(['nextflow'],{},tmp_path,5)==143
    state=json.loads((tmp_path/'engine-state.json').read_bytes())
    assert state['engine_started'] is False and state['engine_exit_code'] is None


def test_p2_cancel_during_atomic_release_is_rejected_after_completed_commit(tmp_path,monkeypatch):
    import threading
    prepared=_prepared(tmp_path);methods=prepared[2]
    started=threading.Event();finished=threading.Event();outcomes=[];errors=[]
    def cancel():
        started.set()
        try:outcomes.append(prepared[1].cancel(REQUEST,actor='operator',allow_data_execution=True,**prepared[7]))
        except Exception as error:errors.append(error)
        finally:finished.set()
    thread=threading.Thread(target=cancel);original=methods._import_artifact;begun=False
    def importing(*args,**kwargs):
        nonlocal begun
        if kwargs.get('generated_by') is not None and not begun:
            begun=True;thread.start();assert started.wait(5)
            assert not finished.wait(.05),'Cancellation acknowledgement must serialize with publication'
        return original(*args,**kwargs)
    monkeypatch.setattr(methods,'_import_artifact',importing)
    try:run=_run(prepared)
    finally:
        if thread.ident is not None:thread.join(10)
    assert run['status']=='succeeded' and not errors and not thread.is_alive()
    assert outcomes[0]['cancellation_outcome']=='already_completed' and outcomes[0]['state']=='completed'
    assert prepared[3].cancel_calls==0


def test_p2_hard_deadline_lost_tmpfs_is_explicitly_unavailable(tmp_path,monkeypatch):
    from devbench.analysis import method_executor
    from devharness_controlled import native_runtime
    prepared=_prepared(tmp_path);methods=prepared[2];step=prepared[4];method=methods.method(step['method_version_id'])
    output=tmp_path/'deadline-lost'/'outputs';output.mkdir(parents=True)
    class Docker:
        def __init__(self):self.env={}
        def isolate_client(self,path):path.mkdir()
        def command(self,args):return args
        def _call(self,args,**kwargs):
            if args[:2]==['image','inspect']:return {'id':'sha256:'+'4'*64,'digests':[IMAGE],'volumes':{},'os':'linux'}
            return '5'*64 if args[0]=='create' else ''
    monkeypatch.setattr(native_runtime,'LocalDocker',Docker)
    def bounded(command,env,timeout,maximum):
        return (1,b'',b'container is stopped; tmpfs is unavailable') if 'tar' in command else (124,b'worker was active',b'independent deadline')
    monkeypatch.setattr(method_executor,'_bounded',bounded)
    executor=method_executor.LocalDockerExecutor();executor.set_observer(lambda event:None)
    with pytest.raises(ValueError) as caught:
        executor.execute(method['contract'],{k:methods.artifact_path(v) for k,v in step['inputs'].items()},step['parameters'],
            methods.artifact_path(method['source_artifact']),methods.artifact_path(method['environment_artifact']),output)
    assert caught.value.execution['exit_code']==124 and caught.value.execution['engine_exit_code'] is None
    assert caught.value.execution['diagnostic_collection']['status']=='unavailable'
    assert not list(output.iterdir()) and not (output.parent/'community-diagnostic-collection.json').exists()


def test_p2_ingestion_failure_retains_actual_successful_worker_execution(tmp_path):
    prepared=_prepared(tmp_path,runner=FixtureRunner(mode='malformed'))
    run=_run(prepared)
    assert run['status']=='failed' and run['outputs']=={}
    assert run['execution']['exit_code']==0
    assert run['execution']['runtime']=='controlled_oci_fixture'
    assert prepared[1].status(REQUEST)['worker_exit_code']==0


def test_p2_pending_cancellation_finishes_after_positive_absence_without_recovery_claim(tmp_path):
    class PendingCancel(FixtureRunner):
        def cancel_run(self,binding):
            self.cancel_calls+=1
            return {**self.observed_run,'state':'cancellation_pending'}
    prepared=_prepared(tmp_path,runner=PendingCancel(mode='coordinator_loss'))
    with pytest.raises(KeyboardInterrupt):_run(prepared)
    prepared[1].cancel(REQUEST,actor='operator',allow_data_execution=True,**prepared[7])
    prepared[3].observed_run['state']='missing'
    result=prepared[1].reconcile(REQUEST,actor='operator')
    assert result['state']=='cancelled'
    assert 'diagnostic_loss' in result and prepared[3].calls==1


def test_p2_shared_executor_keeps_two_run_observers_request_scoped(tmp_path,monkeypatch):
    from devbench.analysis import method_executor
    from devharness_controlled import native_runtime
    import threading,io,tarfile
    prepared=_prepared(tmp_path);methods=prepared[2];step=prepared[4];method=methods.method(step['method_version_id'])
    class Docker:
        def __init__(self):self.env={}
        def isolate_client(self,path):path.mkdir()
        def command(self,args):return args
        def _call(self,args,**kwargs):
            if args[:2]==['image','inspect']:return {'id':'sha256:'+'4'*64,'digests':[IMAGE],'volumes':{},'os':'linux'}
            if args[0]=='create':
                name=next(arg.split('=',1)[1] for arg in args if arg.startswith('--name='))
                return name[-1]*64
            return ''
    monkeypatch.setattr(native_runtime,'LocalDocker',Docker)
    stream=io.BytesIO()
    with tarfile.open(fileobj=stream,mode='w') as archive:
        payload=b'{"software_control":"two concurrent observers"}';member=tarfile.TarInfo('task-evidence.json')
        member.size=len(payload);archive.addfile(member,io.BytesIO(payload))
    monkeypatch.setattr(method_executor,'_bounded',lambda command,env,timeout,maximum:
        (0,stream.getvalue(),b'') if 'tar' in command else (0,b'',b''))
    executor=method_executor.LocalDockerExecutor();registered=[threading.Event(),threading.Event()]
    observed={'a':[],'b':[]};errors=[]
    def run(letter,index):
        try:
            def observer(event):
                assert event['run_id']==letter*32,'An event crossed the run identity boundary'
                observed[letter].append(event['event'])
            executor.set_observer(observer);registered[index].set()
            assert registered[1-index].wait(5)
            output=tmp_path/(letter*32)/'outputs';output.mkdir(parents=True)
            result=executor.execute(method['contract'],{k:methods.artifact_path(v) for k,v in step['inputs'].items()},step['parameters'],
                methods.artifact_path(method['source_artifact']),methods.artifact_path(method['environment_artifact']),output)
            assert result['exit_code']==0
        except BaseException as error:errors.append(error)
    threads=[threading.Thread(target=run,args=(letter,index)) for index,letter in enumerate(('a','b'))]
    for thread in threads:thread.start()
    for thread in threads:thread.join(10)
    assert not errors and all(not thread.is_alive() for thread in threads)
    assert all(events[:2]==['container_create_intent','container_created'] for events in observed.values())


def test_p2_readonly_authority_observation_retries_only_transient_ledger_busy(tmp_path,monkeypatch):
    import devbench.analysis.workflow_operations as operations
    prepared=_prepared(tmp_path);_run(prepared)
    original=operations.WorkflowOperationLedger;calls=[]
    def observing(*args,**kwargs):
        calls.append(kwargs)
        if len(calls)<3:raise operations.OperationBlocked('ledger_busy')
        return original(*args,**kwargs)
    monkeypatch.setattr(operations,'WorkflowOperationLedger',observing)
    result=prepared[1].cancel(REQUEST,actor='operator',allow_data_execution=True,**prepared[7])
    assert result['cancellation_outcome']=='already_completed'
    assert len(calls)==3 and all(item.get('readonly') is True for item in calls)
    assert prepared[3].calls==1 and prepared[3].cancel_calls==0


def test_p2_persistent_ledger_unavailability_remains_bounded_and_fail_closed(tmp_path,monkeypatch):
    import devbench.analysis.workflow_operations as operations
    import time
    prepared=_prepared(tmp_path);_run(prepared);calls=[]
    def unavailable(*args,**kwargs):
        calls.append(kwargs);raise operations.OperationBlocked('ledger_busy')
    monkeypatch.setattr(operations,'WorkflowOperationLedger',unavailable)
    started=time.monotonic()
    with pytest.raises(operations.OperationBlocked):
        prepared[1].cancel(REQUEST,actor='operator',allow_data_execution=True,**prepared[7])
    assert time.monotonic()-started<1 and len(calls)<=6
    assert prepared[1].status(REQUEST)['state']=='completed' and prepared[3].cancel_calls==0


@pytest.mark.parametrize('kind',['missing','stopped','collected','pending'])
def test_p2_cancel_observation_survives_reconstruction_with_loss_or_receipt(tmp_path,kind):
    collection={'status':'collected_while_supervisor_live','container_id':'5'*64,'nextflow_session_id':SESSION,
        'image':IMAGE,'files':{},'reused_previously_collected_bytes':False}
    class ObservedCancel(FixtureRunner):
        def cancel_run(self,binding):
            self.cancel_calls+=1
            if kind=='pending':return {**self.observed_run,'state':'cancellation_pending','diagnostic_status':'dispatch_effect_in_flight'}
            if kind=='collected':return {**self.observed_run,'state':'cancelled','diagnostic_collection':collection}
            return {**self.observed_run,'state':'cancelled','diagnostic_loss':kind+'_tmpfs_unavailable',
                'diagnostic_status':'not_recovered','engine_exit_code':999,'worker_exit_code':999}
    prepared=_prepared(tmp_path,runner=ObservedCancel(mode='coordinator_loss'))
    with pytest.raises(KeyboardInterrupt):_run(prepared)
    request=prepared[2].get('community_request',REQUEST)
    request.update(engine_exit_code=0,worker_exit_code=2)
    prepared[1]._save(request)
    result=prepared[1].cancel(REQUEST,actor='operator',allow_data_execution=True,**prepared[7])
    reopened=_api().CommunityWorkflowService(prepared[2],inventory=inventory(),executor=prepared[3])
    retained=reopened.status(REQUEST)
    assert retained['engine_exit_code']==0 and retained['worker_exit_code']==2
    assert retained['cancellation_observation']['state']==('cancellation_pending' if kind=='pending' else 'cancelled')
    if kind=='pending':
        assert retained['state']!='cancelled' and retained['diagnostic_status']=='dispatch_effect_in_flight'
    elif kind=='collected':
        assert result['diagnostic_collection']==collection and retained['diagnostic_collection']==collection
        assert reopened.reconcile(REQUEST,actor='operator')['diagnostic_collection']==collection
    else:
        assert retained['diagnostic_loss']==kind+'_tmpfs_unavailable'
        assert reopened.reconcile(REQUEST,actor='operator')['diagnostic_loss']==kind+'_tmpfs_unavailable'


@pytest.mark.parametrize('boundary',['saved_create_intent','create_rpc_inflight','create_response','start_response','before_worker_exec','worker_exec_inflight'])
def test_p2_cancel_settles_effect_boundaries_without_launch_after_terminal_ack(tmp_path,monkeypatch,boundary):
    from devbench.analysis import method_executor
    from devharness_controlled import native_runtime
    import io,tarfile
    prepared=_prepared(tmp_path);methods=prepared[2];step=prepared[4];method=methods.method(step['method_version_id'])
    output=tmp_path/('e'*32)/'outputs';output.mkdir(parents=True)
    executor=method_executor.LocalDockerExecutor();current={};record={};effects=[];outcomes=[];fired=False;flag=False
    payloads={}
    def cancel_at(point):
        nonlocal fired
        if point==boundary and not fired:
            fired=True;outcomes.append(executor.cancel_run(deepcopy(current)))
    class Docker:
        def __init__(self):self.env={}
        def isolate_client(self,path):path.mkdir()
        def command(self,args):return args
        def _call(self,args,**kwargs):
            nonlocal flag
            if args[:2]==['image','inspect']:return {'id':'sha256:'+'4'*64,'digests':[IMAGE],'volumes':{},'os':'linux'}
            if args[:2]==['container','ls']:return '5'*64 if record else ''
            if args[0]=='inspect':
                assert record
                return {'Id':'5'*64,'Name':'/'+current['container_name'],'Config':{'Image':IMAGE,
                    'Labels':{'devbench.'+key:current[key] for key in ('run_id','inventory_sha256','nextflow_session_id')}},'State':{'Status':record['state']}}
            if args[0]=='create':
                cancel_at('create_rpc_inflight')
                effects.append(('create',bool(outcomes and outcomes[-1]['state']=='cancelled')))
                record['state']='created';cancel_at('create_response');return '5'*64
            if args[0]=='start':
                effects.append(('start',bool(outcomes and outcomes[-1]['state']=='cancelled')))
                if not record:raise RuntimeError('Cannot start an object that was removed')
                record['state']='running';cancel_at('start_response');return ''
            if args[0]=='exec':
                if 'cancel.request' in args[-1]:flag=True
                if kwargs.get('json_result'):return {'state':'cancellation_pending'}
                return ''
            if args[0]=='stop':record['state']='exited';payloads.clear();return ''
            if args[0]=='rm':
                if not record:raise RuntimeError('Cannot remove a missing object twice')
                record.clear();return ''
            return ''
    monkeypatch.setattr(native_runtime,'LocalDocker',Docker)
    def observer(event):
        current.clear();current.update({key:value for key,value in event.items() if key!='event'})
        if event['event']=='container_create_intent':cancel_at('saved_create_intent')
        if event['event']=='container_started':cancel_at('before_worker_exec')
    executor.set_observer(observer)
    def bounded(command,env,timeout,maximum):
        if 'tar' in command:
            stream=io.BytesIO()
            with tarfile.open(fileobj=stream,mode='w') as archive:
                for name,payload in payloads.items():
                    member=tarfile.TarInfo(name);member.size=len(payload);archive.addfile(member,io.BytesIO(payload))
            return 0,stream.getvalue(),b''
        cancel_at('worker_exec_inflight')
        effects.append(('worker_exec',bool(outcomes and outcomes[-1]['state']=='cancelled')))
        code=143 if flag else 0
        payloads['task-evidence.json']=canonical_json({'nextflow_session_id':current['nextflow_session_id'],
            'engine_state':{'engine_started':False,'engine_exit_code':None,'outcome':'not_started'},'worker_exit_code':code})
        return code,b'',b''
    monkeypatch.setattr(method_executor,'_bounded',bounded)
    error=None;result=None
    try:
        result=executor.execute(method['contract'],{key:methods.artifact_path(value) for key,value in step['inputs'].items()},step['parameters'],
            methods.artifact_path(method['source_artifact']),methods.artifact_path(method['environment_artifact']),output)
    except Exception as caught:error=caught
    assert fired and outcomes
    assert not [effect for effect,after_terminal in effects if after_terminal],effects
    if boundary in {'saved_create_intent','create_rpc_inflight','create_response'}:
        assert not any(effect in {'start','worker_exec'} for effect,_ in effects),effects
    elif boundary in {'start_response','before_worker_exec'}:
        assert not any(effect=='worker_exec' for effect,_ in effects),effects
    else:
        assert outcomes[0]['state']=='cancellation_pending'
    assert error is None and result['cancelled'] is True


def test_p2_reconcile_cannot_finish_cancellation_while_create_may_still_materialize(tmp_path):
    class InflightCreate(FixtureRunner):
        def execute(self,contract,inputs,parameters,source,lock,output_dir):
            self.calls+=1;run_id=Path(output_dir).parent.name
            self.observed_run={'run_id':run_id,'container_name':'devharness-method-'+run_id,'image':IMAGE,
                'inventory_sha256':_sha(inventory()),'nextflow_session_id':SESSION,'state':'missing',
                'dispatch_settled':False,'dispatch_phase':'creating'}
            self.observer({'event':'container_create_intent',**self.observed_run})
            raise KeyboardInterrupt('controller lost while create RPC may still materialize')
        def cancel_run(self,binding):
            self.cancel_calls+=1
            return {**self.observed_run,'state':'cancellation_pending','diagnostic_status':'create_effect_not_settled'}
    prepared=_prepared(tmp_path,runner=InflightCreate())
    with pytest.raises(KeyboardInterrupt):_run(prepared)
    prepared[1].cancel(REQUEST,actor='operator',allow_data_execution=True,**prepared[7])
    result=prepared[1].reconcile(REQUEST,actor='operator')
    assert result['state']=='cancellation_requested'
    assert result['cancellation_observation']['state']=='cancellation_pending'
    assert 'diagnostic_loss' not in result and prepared[3].calls==1
    assert result['events'][-1]['dispatch_phase']=='creating'


def test_p2_cancel_refreshes_second_verified_owned_observation_before_removal(tmp_path,monkeypatch):
    from devbench.analysis.method_executor import LocalDockerExecutor
    from devharness_controlled import native_runtime
    binding=_owned_binding();binding.pop('container_id')
    output=tmp_path/'outputs';output.mkdir()
    binding.update(output_dir=str(output),max_output_bytes=1000000)
    calls=[];lookups=0
    class Docker:
        def _call(self,args,**kwargs):
            nonlocal lookups
            calls.append(args)
            if args[:2]==['container','ls']:
                lookups+=1
                return '' if lookups==1 else '5'*64
            if args[0]=='inspect':
                return {'Id':'5'*64,'Name':'/'+binding['container_name'],'Config':{'Image':IMAGE,
                    'Labels':{'devbench.'+key:binding[key] for key in ('run_id','inventory_sha256','nextflow_session_id')}},'State':{'Status':'created'}}
            return ''
    monkeypatch.setattr(native_runtime,'LocalDocker',Docker)
    result=LocalDockerExecutor().cancel_run(binding)
    assert result['state']=='cancelled' and result['container_id']=='5'*64
    assert result['diagnostic_status']=='engine_never_started'
    assert calls[-1]==['rm','--force','--volumes','5'*64]
    assert result['output_dir']==str(output) and result['max_output_bytes']==1000000


@pytest.mark.parametrize('missing',['python','pandas','numpy'])
def test_p2_required_python_postprocessing_dependency_refuses_before_dispatch(tmp_path,missing):
    from devbench.analysis.methods import MethodService
    supplied=inventory();supplied['tools'].pop(missing)
    runner=FixtureRunner()
    with pytest.raises(ValueError,match='tool|Python|python|dependency'):
        _api().CommunityWorkflowService(MethodService(tmp_path/'workspace'),inventory=supplied,executor=runner).register_method(actor='author')
    assert runner.calls==0


@pytest.mark.parametrize('tool',['pandas','numpy'])
def test_p2_observed_python_postprocessing_component_drift_refuses_engine(tmp_path,tool):
    observed=inventory();observed['tools'][tool]['sha256']='9'*64
    prepared=_prepared(tmp_path,runner=FixtureRunner(observed_inventory=observed))
    with pytest.raises(ValueError,match='inventory|admission'):_run(prepared)
    assert prepared[3].calls==0


@pytest.mark.parametrize('mutation',[None,'missing_pandas','missing_numpy','version_pandas','version_numpy','path_pandas','path_numpy'])
def test_p2_worker_python_import_preflight_preserves_exact_identity_before_engine(monkeypatch,mutation):
    import sys
    import types
    import devbench.analysis.community_worker as worker
    supplied=inventory()
    supplied['tools']['python']['path']=str(Path(sys.executable).resolve())
    original_import=__import__('importlib').import_module
    def importing(name,*args,**kwargs):
        if name in {'pandas','numpy'}:
            if mutation=='missing_'+name:raise ModuleNotFoundError("No module named '"+name+"'")
            version='0.0.0' if mutation=='version_'+name else supplied['tools'][name]['version']
            path='/different/'+name if mutation=='path_'+name else supplied['tools'][name]['path']
            return types.SimpleNamespace(__version__=version,__file__=path+'/__init__.py')
        return original_import(name,*args,**kwargs)
    monkeypatch.setattr(worker.importlib,'import_module',importing)
    monkeypatch.setattr(worker.platform,'python_version',lambda:supplied['tools']['python']['version'])
    if mutation is None:
        worker.verify_python_dependencies(supplied)
    else:
        with pytest.raises(ValueError,match='Python|python|pandas|numpy|import|identity'):
            worker.verify_python_dependencies(supplied)
