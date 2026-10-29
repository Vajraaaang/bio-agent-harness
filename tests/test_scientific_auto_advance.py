"""Governed multi-node advancement; engineering fixtures are not scientific reviews."""
from copy import deepcopy

import pytest

from tests.test_scientific_workspace import tmp_path, action, settled
from tests.test_langgraph_workflow import executable_rna
from tests.support.workflow_science_fixtures import RecordingScientificRuntime


def advance(planner, plan, selector, method, **options):
    from devbench.analysis.workflow_entrypoints import advance_workflow_to_gate
    return advance_workflow_to_gate(planner, plan["session_id"], actor="software-verifier", selector=selector,
        version_ids=[method["version_id"]], scientific_workflow=options.pop("runtime", RecordingScientificRuntime()), **options)


def test_planning_runs_real_nodes_once_and_stops_at_unacknowledged_review(tmp_path):
    from devbench.analysis.workflow_operations import WorkflowOperationLedger
    from devbench.analysis.workflow_state import SERIAL_ROUTE
    from devbench.analysis.workflow_entrypoints import LEDGER_NAME
    planner, plan, _, _, method, _, selector, executor = executable_rna(tmp_path)
    result = advance(planner, plan, selector, method)
    assert result["status"] == "awaiting_operator_review" and result["gate"] == "review"
    assert result["next_node"] == "review" and result["required_action"] == "declare_trial"
    assert [row["node"] for row in result["steps"]] == list(SERIAL_ROUTE[:9])
    assert all(row["status"] == "completed" for row in result["steps"])
    ledger = WorkflowOperationLedger(planner.methods.root / LEDGER_NAME)
    before = ledger.replay(plan["session_id"])
    assert ledger.get(plan["session_id"], "review", 0) is None
    assert ledger.get(plan["session_id"], "declare", 0) is None
    assert executor.calls == 0 and len(selector.contexts) == 3
    assert planner.get(plan["session_id"])["experiments"] == []
    repeated = advance(planner, plan, selector, method)
    assert repeated["gate"] == "review" and len(selector.contexts) == 3
    assert ledger.replay(plan["session_id"]) == before
    from devbench.analysis.workflow_entrypoints import CHECKPOINT_NAME
    from devbench.analysis.workflow_actions import WorkflowActions
    from devbench.analysis.langgraph_workflow import SerialRNAWorkflow
    actions = WorkflowActions(planner, ledger, actor="software-verifier", selector=selector,
        version_ids=[method["version_id"]], scientific_workflow=RecordingScientificRuntime())
    with SerialRNAWorkflow(actions, planner.methods.root / CHECKPOINT_NAME, stepwise=True) as workflow:
        snapshot = workflow.graph.get_state(workflow._config(plan["session_id"]))
        assert snapshot.next == ("review",)
        assert snapshot.interrupts and snapshot.interrupts[0].value["phase"] == "review"


def test_declaration_and_runtime_gate_are_never_automatically_acknowledged(tmp_path):
    from devbench.analysis.workflow_entrypoints import workflow_action, LEDGER_NAME
    from devbench.analysis.workflow_operations import WorkflowOperationLedger
    planner, plan, _, branch, method, specs, selector, executor = executable_rna(tmp_path)
    advance(planner, plan, selector, method)
    workflow_action(planner, plan["session_id"], "declare", actor="operator",
        branch_id=branch["branch_id"], spec=specs[0], scientific_workflow=RecordingScientificRuntime())
    result = advance(planner, plan, selector, method)
    assert result["gate"] == "execution_review" and result["required_action"] == "run_trial"
    ledger = WorkflowOperationLedger(planner.methods.root / LEDGER_NAME)
    assert ledger.get(plan["session_id"], "execution_review", 0) is None
    assert ledger.get(plan["session_id"], "run", 0) is None
    assert executor.calls == 0 and len(selector.contexts) == 3


def test_synthesis_requires_completed_trial_and_explicit_phase(tmp_path):
    from devbench.analysis.workflow_entrypoints import workflow_action
    planner, plan, _, branch, method, specs, selector, executor = executable_rna(tmp_path)
    assert advance(planner, plan, selector, method, phase="synthesis")["status"] == "awaiting_information"
    assert selector.contexts == []
    advance(planner, plan, selector, method)
    workflow_action(planner, plan["session_id"], "declare", actor="operator",
        branch_id=branch["branch_id"], spec=specs[0], scientific_workflow=RecordingScientificRuntime())
    workflow_action(planner, plan["session_id"], "run", actor="operator", experiment_id=specs[0]["id"],
        allow_data_execution=True, executor=executor, scientific_workflow=RecordingScientificRuntime())
    planning = advance(planner, plan, selector, method)
    assert planning["status"] == "awaiting_information" and len(selector.contexts) == 3
    result = advance(planner, plan, selector, method, phase="synthesis")
    assert result["status"] == "abstained" and result["gate"] is None
    assert [row["node"] for row in result["steps"]][-3:] == ["verify", "recommend", "conclusion_review"]
    assert executor.calls == 1 and len(selector.contexts) == 4
    assert result["scientific_qualification"] == "not_established"
    assert advance(planner, plan, selector, method, phase="synthesis")["status"] == "abstained"
    assert executor.calls == 1 and len(selector.contexts) == 4


def test_missing_runtime_or_model_authority_returns_explicit_requirement(tmp_path):
    planner, plan, _, _, method, _, selector, _ = executable_rna(tmp_path)
    missing = advance(planner, plan, selector, method, runtime=None)
    assert missing["status"] == "awaiting_information"
    assert missing["required_action"] == "configure_scientific_workflow"
    assert not (planner.methods.root / "rna-workflow-checkpoints.sqlite").exists()
    class LiveSelector:
        def configuration(self):
            return {"backend": "codex-cli"}
        def select(self, *args, **kwargs):
            pytest.fail("A model call without current explicit authority is forbidden")
    result = advance(planner, plan, LiveSelector(), method)
    assert result["status"] == "awaiting_information" and result["next_node"] == "generate"
    assert result["required_action"] == "authorize_model_execution"
    assert [row["node"] for row in result["steps"]] == ["audit", "question", "discovery"]


def test_uncertain_stage_is_retained_and_never_redispatched(tmp_path):
    planner, plan, _, _, method, _, selector, _ = executable_rna(tmp_path)
    class FailingRuntime(RecordingScientificRuntime):
        calls = 0
        def perform(self, node, *args, **kwargs):
            if node == "candidate_review":
                self.calls += 1
                raise RuntimeError("Deliberately interrupted scientific reviewer")
            return super().perform(node, *args, **kwargs)
    runtime = FailingRuntime()
    result = advance(planner, plan, selector, method, runtime=runtime)
    assert result["status"] == "recovery_required"
    assert result["controller_status"] == "reconciliation_required"
    assert result["steps"][-1]["node"] == "candidate_review"
    assert result["steps"][-1]["status"] == "uncertain"
    assert runtime.calls == 1 and len(selector.contexts) == 1
    again = advance(planner, plan, selector, method, runtime=runtime)
    assert again["status"] == "recovery_required" and runtime.calls == 1


def test_scientific_model_stages_require_current_campaign_permission(tmp_path):
    planner, plan, _, _, method, _, selector, _ = executable_rna(tmp_path)
    class ModelFlaggedFixture(RecordingScientificRuntime):
        # This remains an offline engineering fixture, but exercises the same
        # permission boundary as a separately enabled review-model runtime.
        allow_review_model_execution = True
        calls = []
        def perform(self, node, *args, **kwargs):
            self.calls.append(node)
            return super().perform(node, *args, **kwargs)
    runtime = ModelFlaggedFixture()
    stopped = advance(planner, plan, selector, method, runtime=runtime)
    assert stopped["next_node"] == "candidate_review"
    assert stopped["required_action"] == "authorize_model_execution"
    assert runtime.calls == ["discovery"]
    completed = advance(planner, plan, selector, method, runtime=runtime, allow_model_execution=True)
    assert completed["gate"] == "review"
    assert runtime.calls == ["discovery", "candidate_review", "implementation", "design_review"]


def rna_workspace(tmp_path):
    from devbench.analysis.workbench_ui import WorkbenchService
    planner, original, _, _, method, specs, selector, executor = executable_rna(tmp_path)
    inputs = tmp_path / "browser-inputs"
    inputs.mkdir()
    app = WorkbenchService(tmp_path / "browser", [inputs], enable_scientific=True,
        scientific_workspace=planner.methods.root, method_selector=selector, method_executor=executor,
        scientific_workflow=RecordingScientificRuntime(), allow_data_execution=True)
    action(app, "initialize")
    study = action(app, "create_study", name="Synthetic graph verification", aim="Test guarded advancement")
    dataset = action(app, "register_dataset", study_id=study["study_id"], name="Synthetic RNA",
        members={key: {"artifact_id": value} for key, value in specs[0]["inputs"]["Unknown QC"].items()},
        metadata={"observed": {}, "inferred": {}, "unknowns": [], "data_classification": "synthetic"})
    analysis = action(app, "create_analysis", study_id=study["study_id"], name="RNA control test",
        objective="Inspect synthetic RNA structure", computational_task="bulk RNA")
    inspection = original["context"]["dataset_summary"]["observed"]["bulk_rna_input"]
    refs = inspection["input_artifact_refs"]
    binding = {"format": "devbench.bulk-rna-binding/1", "matrix_artifact_id": refs["matrix"]["artifact_id"],
        "metadata_artifact_id": refs["metadata"]["artifact_id"], "declarations": {key: inspection[key] for key in
            ("format", "representation", "axes", "provenance", "biological_independence", "metadata_roles")}}
    plan = action(app, "create_plan", analysis_ref=analysis["ref"], dataset_ref=dataset["ref"], rna_input_contract=binding)
    return app, plan, selector, method


def test_workspace_campaign_is_idempotent_and_never_accepts_data_grants(tmp_path):
    app, plan, selector, method = rna_workspace(tmp_path)
    try:
        body = {"action": "advance_to_gate", "expected_revision": app.scientific.catalog()["revision"],
            "actor": "synthetic-verifier", "session_id": plan["session_id"], "idempotency_key": "campaign-1",
            "method_version_ids": [method["version_id"]]}
        first = app.scientific.action(body)
        duplicate = app.scientific.action(deepcopy(body))
        assert duplicate["operation_id"] == first["operation_id"]
        result = settled(app, first)
        assert result["status"] == "awaiting_operator_review"
        assert len(selector.contexts) == 3
        from devbench.analysis.workflow_entrypoints import LEDGER_NAME
        ledger_path = app.scientific.root / LEDGER_NAME
        retained_bytes = ledger_path.read_bytes()
        steps = [row for row in app.scientific.catalog()["workflow_steps"] if row["session_id"] == plan["session_id"]]
        assert [row["node"] for row in steps] == ["audit", "question", "discovery", "generate", "candidate_review",
            "resolve", "implementation", "design", "design_review"]
        assert all(row["status"] == "completed" for row in steps)
        assert ledger_path.read_bytes() == retained_bytes
        assert app.scientific.action(deepcopy(body))["operation_id"] == first["operation_id"]
        with pytest.raises(ValueError, match="Conflicting idempotency"):
            app.scientific.action({**body, "phase": "synthesis"})
        with pytest.raises(ValueError, match="declared fields"):
            action(app, "advance_to_gate", session_id=plan["session_id"], idempotency_key="forbidden-grant", allow_data_execution=True)
        with pytest.raises(RuntimeError, match="revision changed"):
            app.scientific.action({**body, "idempotency_key": "new-stale-campaign"})
        for fields in ({"phase": []}, {"method_version_ids": [{}]}, {"allow_model_execution": 1}):
            with pytest.raises(ValueError):
                action(app, "advance_to_gate", session_id=plan["session_id"], idempotency_key="malformed-campaign", **fields)
        assert not app.scientific.catalog()["runs"]
    finally:
        app.close()


def test_cancellation_and_missing_selector_stop_without_fabricated_stages(tmp_path):
    from devbench.analysis.workflow_entrypoints import LEDGER_NAME
    from devbench.analysis.workflow_operations import WorkflowOperationLedger
    planner, plan, _, _, method, _, _, _ = executable_rna(tmp_path)
    result = advance(planner, plan, None, method)
    assert result["required_action"] == "configure_planning_selector"
    assert [row["node"] for row in result["steps"]] == ["audit", "question", "discovery"]
    ledger = WorkflowOperationLedger(planner.methods.root / LEDGER_NAME)
    before = ledger.replay(plan["session_id"])
    ledger.cancel(plan["session_id"])
    cancelled = advance(planner, plan, None, method)
    assert cancelled["status"] == "cancelled"
    assert ledger.replay(plan["session_id"]) == before
    assert ledger.get(plan["session_id"], "generate", 0) is None


def test_campaign_rechecks_current_runtime_before_each_node(tmp_path):
    from devbench.analysis.workflow_entrypoints import LEDGER_NAME
    from devbench.analysis.workflow_operations import WorkflowOperationLedger
    planner, plan, _, _, method, _, selector, _ = executable_rna(tmp_path)
    def current_runtime():
        path = planner.methods.root / LEDGER_NAME
        if path.exists():
            ledger = WorkflowOperationLedger(path, readonly=True)
            if ledger.get(plan["session_id"], "discovery", 0) is not None:
                raise RuntimeError("Source changed after discovery")
    with pytest.raises(RuntimeError, match="Source changed after discovery"):
        advance(planner, plan, selector, method, before_advance=current_runtime)
    ledger = WorkflowOperationLedger(planner.methods.root / LEDGER_NAME)
    assert selector.contexts == [] and ledger.get(plan["session_id"], "generate", 0) is None
    assert ledger.get(plan["session_id"], "discovery", 0)["status"] == "completed"


def test_synthesis_readiness_is_a_verified_nonexecuting_observation(tmp_path):
    from devbench.analysis.workflow_entrypoints import synthesis_ready, workflow_action, LEDGER_NAME, CHECKPOINT_NAME
    planner, plan, _, branch, method, specs, selector, executor = executable_rna(tmp_path)
    options = {"scientific_workflow": RecordingScientificRuntime(), "selector": selector}
    assert synthesis_ready(planner, plan["session_id"], **options) is False
    assert not (planner.methods.root / CHECKPOINT_NAME).exists()
    advance(planner, plan, selector, method)
    assert synthesis_ready(planner, plan["session_id"], **options) is False
    workflow_action(planner, plan["session_id"], "declare", actor="operator",
        branch_id=branch["branch_id"], spec=specs[0], scientific_workflow=RecordingScientificRuntime())
    assert synthesis_ready(planner, plan["session_id"], **options) is False
    workflow_action(planner, plan["session_id"], "run", actor="operator", experiment_id=specs[0]["id"],
        allow_data_execution=True, executor=executor, scientific_workflow=RecordingScientificRuntime())
    before = {name: (planner.methods.root / name).read_bytes() for name in (LEDGER_NAME, CHECKPOINT_NAME)}
    assert synthesis_ready(planner, plan["session_id"], **options) is True
    assert synthesis_ready(planner, plan["session_id"], **options) is True
    assert all((planner.methods.root / name).read_bytes() == value for name, value in before.items())
    assert executor.calls == 1 and len(selector.contexts) == 3
