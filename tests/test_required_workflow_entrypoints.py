"""Mandatory RNA entrypoints use real graph checkpoints without extra authority."""
from tests.support.workflow_science_fixtures import RecordingScientificRuntime
import pytest
from pathlib import Path
import tempfile

from tests.test_langgraph_workflow import executable_rna


@pytest.fixture
def protected_workspace():
    # Protected evidence intentionally cannot live in platform scratch.
    # Keep the production guard and global TMPDIR unchanged.
    root = Path(__file__).resolve().parents[1] / "reports" / "local-rna-analyst-2026-10-28" / "test-workspaces"
    root.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix="entrypoint-", dir=root))


@pytest.mark.parametrize("representation", ["raw_counts", "normalized_expression"])
def test_manual_stages_resume_exact_node_without_extra_model_calls(tmp_path, representation):
    from devbench.analysis.workflow_entrypoints import workflow_action
    planner, plan, _, branch, method, specs, selector, executor = executable_rna(tmp_path, representation)
    session = plan["session_id"]
    options = {"actor": "scientist", "selector": selector, "scientific_workflow": RecordingScientificRuntime()}
    workflow_action(planner, session, "discovery", **options)
    assert workflow_action(planner, session, "generate", **options)
    assert len(selector.contexts) == 1 and executor.calls == 0
    workflow_action(planner, session, "candidate_review", **options)
    workflow_action(planner, session, "resolve", version_ids=[method["version_id"]], **options)
    assert len(selector.contexts) == 2
    workflow_action(planner, session, "implementation", **options)
    assert workflow_action(planner, session, "design", **options) == specs
    workflow_action(planner, session, "design_review", **options)
    assert len(selector.contexts) == 3 and not planner.get(session)["experiments"]
    declared = workflow_action(planner, session, "declare", branch_id=branch["branch_id"], spec=specs[0], actor="scientist", scientific_workflow=RecordingScientificRuntime())
    assert declared["id"] == specs[0]["id"] and executor.calls == 0
    with pytest.raises(PermissionError):
        workflow_action(planner, session, "run", experiment_id=specs[0]["id"], actor="scientist", executor=executor, scientific_workflow=RecordingScientificRuntime())
    results = workflow_action(planner, session, "run", experiment_id=specs[0]["id"], actor="scientist",
                              executor=executor, allow_data_execution=True, scientific_workflow=RecordingScientificRuntime())
    assert len(results) == 1 and executor.calls == 1 and len(selector.contexts) == 3
    workflow_action(planner, session, "recommend", **options)
    assert len(selector.contexts) == 4 and planner.get(session)["approvals"] == {}


def test_manual_entrypoint_rejects_skipped_stage_before_dispatch(tmp_path):
    from devbench.analysis.workflow_entrypoints import workflow_action
    planner, plan, _, _, method, _, selector, _ = executable_rna(tmp_path)
    with pytest.raises(ValueError, match="discovery"):
        workflow_action(planner, plan["session_id"], "resolve", actor="scientist", selector=selector,
                        version_ids=[method["version_id"]], scientific_workflow=RecordingScientificRuntime())
    assert selector.contexts == []


def test_rna_cli_creation_preserves_binding_and_science_uses_checkpoint(tmp_path, capsys):
    import json
    from devbench.analysis.method_planner_cli import main
    planner, plan, *_ = executable_rna(tmp_path)
    inspection = plan["context"]["dataset_summary"]["observed"]["bulk_rna_input"]
    refs = inspection["input_artifact_refs"]
    binding = {"format": "devbench.bulk-rna-binding/1", "matrix_artifact_id": refs["matrix"]["artifact_id"],
               "metadata_artifact_id": refs["metadata"]["artifact_id"], "declarations": {
                   key: inspection[key] for key in ("format", "representation", "axes", "provenance", "biological_independence", "metadata_roles")}}
    request = {"task": plan["context"]["task"], "dataset_summary": {"observed": {}, "inferred": {}, "unknowns": [],
               "artifact_ids": plan["context"]["dataset_summary"]["artifact_ids"]}, "data_classification": "synthetic", "rna_input_contract": binding}
    path = tmp_path / "request.json"
    path.write_text(json.dumps(request))
    main(["--workspace", str(planner.methods.root), "create", "--request", str(path)])
    created = json.loads(capsys.readouterr().out)
    assert "bulk_rna_input" in created["context"]["dataset_summary"]["observed"]
    decisions = tmp_path / "decisions.json"
    decisions.write_text('{"candidates": []}')
    with pytest.raises(PermissionError, match="scientific workflow|configuration"):
        main(["--workspace", str(planner.methods.root), "--decisions", str(decisions), "science", "--plan", created["session_id"]])
    assert not (planner.methods.root / "rna-workflow-checkpoints.sqlite").exists()


def test_trial_generation_retains_graph_declaration_pause_without_execution(protected_workspace):
    tmp_path = protected_workspace
    from tests.test_rna_scientific_trial import generated
    from devbench.analysis.workflow_operations import WorkflowOperationLedger
    request, planner, selector = generated(tmp_path)
    ledger = WorkflowOperationLedger(planner.methods.root / "rna-workflow-operations.sqlite")
    identity = request["workflow"]["planner_session_id"]
    assert ledger.get(identity, "audit", 0)["status"] == "completed"
    assert ledger.get(identity, "declare", 0)["status"] == "completed"
    assert ledger.get(identity, "run", 0) is None
    assert len(selector.contexts) == 3 and planner.get(identity)["approvals"] == {}


def test_recommend_cannot_implicitly_authorize_trial_design(tmp_path):
    from devbench.analysis.workflow_entrypoints import workflow_action
    planner, plan, _, _, method, _, selector, _ = executable_rna(tmp_path)
    session = plan["session_id"]
    workflow_action(planner, session, "discovery", actor="scientist", selector=selector, scientific_workflow=RecordingScientificRuntime())
    workflow_action(planner, session, "generate", actor="scientist", selector=selector, scientific_workflow=RecordingScientificRuntime())
    workflow_action(planner, session, "candidate_review", actor="scientist", selector=selector, scientific_workflow=RecordingScientificRuntime())
    workflow_action(planner, session, "resolve", actor="scientist", selector=selector, version_ids=[method["version_id"]], scientific_workflow=RecordingScientificRuntime())
    with pytest.raises(ValueError, match="implementation|design"):
        workflow_action(planner, session, "recommend", actor="scientist", selector=selector, scientific_workflow=RecordingScientificRuntime())
    assert len(selector.contexts) == 2


def test_guarded_execution_refusal_is_retained_without_worker_or_recommendation(tmp_path):
    from devbench.analysis.workflow_entrypoints import workflow_action
    planner, plan, _, branch, method, specs, selector, executor = executable_rna(tmp_path)
    session = plan["session_id"]
    for node in ("discovery", "generate", "candidate_review", "resolve", "implementation", "design", "design_review"):
        workflow_action(planner, session, node, actor="scientist", selector=selector, version_ids=[method["version_id"]], scientific_workflow=RecordingScientificRuntime())
    workflow_action(planner, session, "declare", actor="scientist", branch_id=branch["branch_id"], spec=specs[0], scientific_workflow=RecordingScientificRuntime())
    def refuse(_descriptor):
        raise PermissionError("Current trial approval expired")
    results = workflow_action(planner, session, "run", actor="operator", allow_data_execution=True,
        experiment_id=specs[0]["id"], executor=executor, before_dispatch=refuse, scientific_workflow=RecordingScientificRuntime())
    assert executor.calls == 0 and len(selector.contexts) == 3
    assert results[0]["status"] == "failed"
    assert "trial_admission_denied_no_dispatch" in results[0]["warnings"]
    with pytest.raises(ValueError, match="recovery"):
        workflow_action(planner, session, "recommend", actor="scientist", selector=selector, scientific_workflow=RecordingScientificRuntime())


def test_rna_workspace_resume_preserves_scientific_trial_definition(protected_workspace):
    tmp_path = protected_workspace
    from devbench.analysis.workbench_ui import WorkbenchService
    from tests.test_scientific_workspace import action, settled
    planner, original, _, _, method, specs, selector, executor = executable_rna(tmp_path)
    inputs = tmp_path / "input-roots"
    inputs.mkdir()
    app = WorkbenchService(tmp_path / "browser", [inputs], enable_scientific=True,
        scientific_workspace=planner.methods.root, method_selector=selector, method_executor=executor,
        allow_data_execution=True, scientific_workflow=RecordingScientificRuntime())
    try:
        action(app, "initialize")
        study = action(app, "create_study", name="RNA software check", aim="Check graph-controlled RNA execution")
        dataset = action(app, "register_dataset", study_id=study["study_id"], name="Synthetic RNA",
            members={key: {"artifact_id": value} for key, value in specs[0]["inputs"]["Unknown QC"].items()},
            metadata={"observed": {}, "inferred": {}, "unknowns": [], "data_classification": "synthetic"})
        analysis = action(app, "create_analysis", study_id=study["study_id"], name="RNA route",
            objective="Describe RNA structure", computational_task="bulk RNA")
        inspection = original["context"]["dataset_summary"]["observed"]["bulk_rna_input"]
        refs = inspection["input_artifact_refs"]
        binding = {"format": "devbench.bulk-rna-binding/1", "matrix_artifact_id": refs["matrix"]["artifact_id"],
            "metadata_artifact_id": refs["metadata"]["artifact_id"], "declarations": {
                key: inspection[key] for key in ("format", "representation", "axes", "provenance", "biological_independence", "metadata_roles")}}
        plan = action(app, "create_plan", analysis_ref=analysis["ref"], dataset_ref=dataset["ref"], rna_input_contract=binding)
        for name in ("discovery", "science", "candidate_review", "resolve", "implementation", "design_trials", "design_review"):
            fields = {"method_version_ids": [method["version_id"]]} if name == "resolve" else {}
            settled(app, action(app, name, session_id=plan["session_id"], **fields))
        declared = action(app, "declare_trial", session_id=plan["session_id"], spec=specs[0])
        assert declared["analysis_ref"] and executor.calls == 0
        result = settled(app, action(app, "run_trial", session_id=plan["session_id"], experiment_id=specs[0]["id"],
            idempotency_key="explicit-rna-run", allow_data_execution=True))
        assert executor.calls == 1 and len(result["results"]) == 1
        assert len(selector.contexts) == 3
        with pytest.raises(ValueError, match="completed parent"):
            action(app, "rerun", analysis_ref=declared["analysis_ref"], dataset_ref=dataset["ref"],
                idempotency_key="rna-rerun-before-recommendation", allow_data_execution=True)
        assert executor.calls == 1
    finally:
        app.close()


def test_rna_followup_rejects_direct_built_plan_before_numeric_side_effect(tmp_path):
    from devbench.analysis.workflow_entrypoints import governed_followup
    planner, plan, *_ = executable_rna(tmp_path)
    calls = []
    with pytest.raises(ValueError, match="completed parent"):
        governed_followup(planner, plan["session_id"], kind="full", request_id="direct-bypass",
            actor="scientist", operation=lambda: calls.append("numeric"), verify=lambda value: None)
    assert calls == []


def test_rna_full_execution_uses_followup_graph_and_replays_without_duplicate_work(tmp_path, monkeypatch):
    from devbench.analysis.workflow_entrypoints import workflow_action, workflow_full_execution
    from tests.test_method_planner import recommendation
    planner, plan, _, branch, method, specs, selector, executor = executable_rna(tmp_path)
    from copy import deepcopy
    import json
    from devbench.analysis import methods as method_module
    contract = deepcopy(method["contract"])
    contract.update(method_id="fixture.rna_alternative", name="Alternative QC")
    alternate = planner.methods.register_method(contract,
        source=planner.methods.artifact_path(method["source_artifact"]),
        environment_lock=planner.methods.artifact_path(method["environment_artifact"]), actor="scientist")
    candidate = deepcopy(selector.answers[0]["candidates"][0])
    candidate["name"] = "Alternative QC"
    selector.answers[0]["candidates"].append(candidate)
    selector.answers[1]["bindings"].append({"candidate": "Alternative QC", "method_version_id": alternate["version_id"], "discovery": None})
    spec = specs[0]
    spec["candidates"].append("Alternative QC")
    spec["inputs"]["Alternative QC"] = deepcopy(spec["inputs"]["Unknown QC"])
    spec["parameters"]["Alternative QC"] = {"threshold": 3}
    spec["resource_limits"]["max_runs"] = 2
    spec["metrics"] = [{"name": "reference_error", "category": "calibration", "direction": "lower_better",
        "interpretation": "Synthetic coordinator arithmetic test only; no biological validation"}]
    spec["metric_sources"] = {"reference_error": {"kind": "validation", "field": "/scientific_metrics/reference_error"}}
    original_validation = method_module.validate_outputs
    def numerical_comparator(contract, inputs, outputs):
        result = original_validation(contract, inputs, outputs)
        before = json.loads(inputs["dataset"].read_bytes())
        after = json.loads(outputs["dataset"].read_bytes())
        reference = sum(sum(row) for row in before["matrix"])
        result["scientific_metrics"] = {"reference_error": abs(float(after["threshold"]) - reference)}
        result["comparison_measurement_provenance"] = {"/scientific_metrics/reference_error": {
            "origin": "devharness.method_validation/1", "basis": "immutable_input_reference_comparison",
            "prerequisites": {"immutable_input_reference_loaded": True, "finite_comparison_values": True},
            "reference": "Synthetic matrix sum for software control-flow test, not biological ground truth", "raw_counts_recomputed": False}}
        return result
    monkeypatch.setattr(method_module, "validate_outputs", numerical_comparator)
    answer = recommendation()
    answer["tests_run"] = [spec["id"]]
    selector.answers[-1] = answer
    session = plan["session_id"]
    for node in ("discovery", "generate", "candidate_review", "resolve", "implementation", "design", "design_review"):
        workflow_action(planner, session, node, actor="scientist", selector=selector,
                        version_ids=[method["version_id"], alternate["version_id"]], scientific_workflow=RecordingScientificRuntime())
    workflow_action(planner, session, "declare", actor="scientist", branch_id=branch["branch_id"], spec=specs[0], scientific_workflow=RecordingScientificRuntime())
    workflow_action(planner, session, "run", actor="scientist", allow_data_execution=True,
                    experiment_id=specs[0]["id"], executor=executor, scientific_workflow=RecordingScientificRuntime())
    workflow_action(planner, session, "recommend", actor="scientist", selector=selector, scientific_workflow=RecordingScientificRuntime())
    workflow_action(planner, session, "conclusion_review", actor="scientist", selector=selector, scientific_workflow=RecordingScientificRuntime())
    planner.approve_recommendation(session, actor="reviewer", rationale="Approve this synthetic software-only fixture")
    planner.prepare_full_execution(session, branch["branch_id"], specs[0]["inputs"]["Unknown QC"], actor="scientist")
    planner.approve_full_execution(session, actor="reviewer", rationale="Approve exact full fixture execution")
    result = workflow_full_execution(planner, session, actor="scientist", allow_data_execution=True, executor=executor, scientific_workflow=RecordingScientificRuntime())
    assert executor.calls == 3 and result["status"] == "succeeded"
    replay = workflow_full_execution(planner, session, actor="scientist", allow_data_execution=True, executor=executor, scientific_workflow=RecordingScientificRuntime())
    assert replay == result and executor.calls == 3
    planner.prepare_full_execution(session, branch["branch_id"], specs[0]["inputs"]["Unknown QC"], actor="scientist")
    planner.approve_full_execution(session, actor="reviewer", rationale="Approve a distinct exact full fixture step")
    monkeypatch.setattr(planner, "run_full", lambda *args, **kwargs: result)
    with pytest.raises(ValueError, match="differs from its retained"):
        workflow_full_execution(planner, session, actor="scientist", allow_data_execution=True, executor=executor, scientific_workflow=RecordingScientificRuntime())
    assert executor.calls == 3


def test_multi_trial_numeric_failure_retains_receipt_and_leaves_later_trial_undeployed(protected_workspace, monkeypatch):
    tmp_path = protected_workspace
    from copy import deepcopy
    from tests import test_rna_scientific_trial as trial_tests
    original_fixture = trial_tests.fixture
    def two_trials(path):
        request, planner, selector, branch, method, exposure = original_fixture(path)
        request["allocation"]["max_runs"] = 2
        second = deepcopy(selector.answers[2]["answer"]["experiments"][0])
        second["id"] = "unstarted-second-trial"
        selector.answers[2]["answer"]["experiments"].append(second)
        return request, planner, selector, branch, method, exposure
    monkeypatch.setattr(trial_tests, "fixture", two_trials)
    request, planner, _ = trial_tests.generated(tmp_path)
    class FailingExecutor:
        calls = 0
        def execute(self, *args):
            self.calls += 1
            raise RuntimeError("Synthetic first trial failed")
    executor = FailingExecutor()
    receipt = trial_tests.execute_trial(request, planner=planner, workspace=tmp_path, actor="participant",
        authority=trial_tests.FakeAuthority(), executor=executor, now=trial_tests.NOW, scientific_workflow=RecordingScientificRuntime())
    assert receipt["status"] == "completed_with_failures" and executor.calls == 1
    assert len(receipt["results"]) == 1 and receipt["results"][0]["status"] == "failed"
    state = planner.get(request["workflow"]["planner_session_id"])
    assert state["experiments"][1]["status"] == "declared"


def test_rna_entrypoint_requires_explicit_scientific_runtime_before_model_dispatch(tmp_path):
    from devbench.analysis.workflow_entrypoints import workflow_action
    planner, plan, _, _, _, _, selector, _ = executable_rna(tmp_path)
    with pytest.raises(PermissionError, match="trusted scientific workflow"):
        workflow_action(planner, plan["session_id"], "generate", actor="scientist", selector=selector)
    assert selector.contexts == []


def test_rna_discovery_freezes_source_acquisition_before_dispatch(tmp_path, monkeypatch):
    from devbench.analysis.workflow_entrypoints import workflow_action, workflow_source
    planner, plan, *_rest = executable_rna(tmp_path)
    selector = _rest[-2]
    runtime = RecordingScientificRuntime()
    workflow_action(planner, plan["session_id"], "discovery", actor="scientist", selector=selector,
                    scientific_workflow=runtime)
    calls = []
    monkeypatch.setattr(planner, "retrieve_evidence", lambda *args, **kwargs: calls.append((args, kwargs)))
    with pytest.raises(ValueError, match="new planning cycle"):
        workflow_source(planner, plan["session_id"], "https://example.org/paper", actor="scientist", allowed_hosts=["example.org"])
    assert calls == []
