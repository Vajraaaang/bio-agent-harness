"""Offline serial orchestration: real checkpoints, no external model calls."""
from copy import deepcopy
import json
from pathlib import Path
import uuid

import pytest

CHECKPOINT_SIDE_EFFECTS = []


def unsafe_checkpoint_constructor(value):
    CHECKPOINT_SIDE_EFFECTS.append(value)
    return value


def test_workflow_state_rejects_matrices_authority_and_nonfinite_values():
    from devbench.analysis.workflow_state import validate_workflow_state
    with pytest.raises(ValueError):
        validate_workflow_state({"matrix": [[1]], "allow_data_execution": True})
    with pytest.raises(ValueError):
        validate_workflow_state({"score": float("nan")})


def test_disabled_adapter_does_not_import_optional_dependencies(tmp_path):
    from devbench.analysis.langgraph_workflow import SerialRNAWorkflow
    with pytest.raises(ValueError, match="disabled"):
        SerialRNAWorkflow(None, tmp_path / "checkpoint.sqlite", enabled=False)
    assert not (tmp_path / "checkpoint.sqlite").exists()


def test_bounded_state_has_only_artifact_references(tmp_path):
    from devbench.analysis.workflow_actions import WorkflowActions
    planner = FakePlanner(tmp_path)
    actions = WorkflowActions(planner, ledger(tmp_path), actor="scientist", selector=object())
    state = actions.create_state(planner.session_id)
    assert state["phase"] == "audit"
    assert len(state["artifact_refs"]) == 2
    assert "task" not in state and '"matrix"' not in json.dumps(state)
    assert "allow_model_execution" not in state


def ledger(tmp_path):
    from devbench.analysis.workflow_operations import WorkflowOperationLedger
    return WorkflowOperationLedger(tmp_path / "operations.sqlite")


class FakePlanner:
    """Controlled planner cursor for crash/replay tests; scientific checks tested separately."""
    def __init__(self, tmp_path):
        planner, self.plan, *_ = rna_plan(tmp_path)
        self.methods = planner.methods
        self.session_id = self.plan["session_id"]
        self.calls = []
        self.fail_after_generate = False
    def get(self, session):
        assert session == self.session_id
        return deepcopy(self.plan)
    def generate_candidates(self, session, selector, **kwargs):
        self.calls.append("generate")
        self.plan["scientific_candidates"] = [{"name": "Unimplemented scientific alternative"}]
        if self.fail_after_generate:
            raise RuntimeError("Interrupted after planner mutation")
        return deepcopy(self.plan["scientific_candidates"])
    def resolve_capabilities(self, session, selector, versions, **kwargs):
        self.calls.append("resolve")
        self.plan["candidates"] = [{"name": "Unimplemented scientific alternative", "method_version_id": "fixture", "applicability": "eligible"}]
        return self.plan["candidates"]
    def design_trials(self, session, selector, **kwargs):
        self.calls.append("design")
        self.plan["trial_designs"] = [{"proposals": []}]
        return []
    def evidence_assessment(self, session):
        self.calls.append("verify")
        return {"eligible_for_recommendation": False}
    def recommend(self, session, selector, **kwargs):
        self.calls.append("recommend")
        self.plan["recommendation"] = {"choice": None, "confidence": "no_recommendation"}
        return self.plan["recommendation"]


def test_completed_operation_replays_without_second_planner_call(tmp_path):
    from devbench.analysis.workflow_actions import WorkflowActions
    planner = FakePlanner(tmp_path)
    actions = WorkflowActions(planner, ledger(tmp_path), actor="scientist", selector=object())
    state = actions.create_state(planner.session_id)
    result = actions.perform("generate", state)
    assert planner.calls == ["generate"]
    assert actions.perform("generate", state) == result
    assert planner.calls == ["generate"]
    planner.plan["external_change"] = True
    with pytest.raises(ValueError, match="stale"):
        actions.perform("generate", state)


def test_failed_operation_never_redispatches_and_requires_reconciliation(tmp_path):
    from devbench.analysis.workflow_actions import WorkflowActions
    planner = FakePlanner(tmp_path)
    actions = WorkflowActions(planner, ledger(tmp_path), actor="scientist", selector=object())
    state = actions.create_state(planner.session_id)
    planner.fail_after_generate = True
    result = actions.perform("generate", state)
    assert result["status"] in {"failed", "reconciliation_required"}
    again = actions.perform("generate", state)
    assert again["status"] in {"failed", "reconciliation_required"}
    assert planner.calls == ["generate"]


def test_actual_sqlite_interrupt_survives_reconstruction(tmp_path):
    pytest.importorskip("langgraph")
    from devbench.analysis.workflow_actions import WorkflowActions
    from devbench.analysis.langgraph_workflow import SerialRNAWorkflow
    from langgraph.types import Command
    planner = FakePlanner(tmp_path)
    operations = ledger(tmp_path)
    actions = WorkflowActions(planner, operations, actor="scientist", selector=object())
    state = actions.create_state(planner.session_id)
    checkpoint = tmp_path / "checkpoint.sqlite"
    with SerialRNAWorkflow(actions, checkpoint, enabled=True) as workflow:
        result = workflow.start(state)
        assert "__interrupt__" in result
        assert planner.calls == ["generate", "resolve", "design"]
    with SerialRNAWorkflow(actions, checkpoint, enabled=True) as workflow:
        result = workflow.resume(state["workflow_id"])
        assert result["status"] == "abstained"
        assert planner.calls == ["generate", "resolve", "design", "verify", "recommend"]
        assert "Unimplemented" not in json.dumps(result)


def rna_plan(tmp_path, representation="raw_counts"):
    from tests.test_extensible_methods import prepared
    from tests.test_execution_contract_repair import declaration
    from devbench.analysis.method_planner import ScientificMethodPlanner
    methods, analysis, branch, step, *_ = prepared(tmp_path)
    matrix_path = tmp_path / "matrix.json"
    matrix_path.write_text(json.dumps({"gene_ids": ["g1", "g2"], "sample_ids": ["s1", "s2"],
        "matrix": [[1, 2], [3, 4]] if representation == "raw_counts" else [[-1.5, 2.3], [0.0, 4.1]]}))
    metadata_path = tmp_path / "metadata.json"
    metadata_path.write_text(json.dumps({"samples": [{"sample_id": "s2", "donor": "d1"}, {"sample_id": "s1", "donor": "d1"}]}))
    refs = [methods.import_artifact(path, artifact_type="json", actor="scientist") for path in (matrix_path, metadata_path)]
    binding = {"format": "devbench.bulk-rna-binding/1", "matrix_artifact_id": refs[0]["artifact_id"],
               "metadata_artifact_id": refs[1]["artifact_id"], "declarations": declaration(representation)}
    binding["declarations"]["metadata_roles"] = {"biological_unit": "donor"}
    planner = ScientificMethodPlanner(methods)
    plan = planner.create({"scientific_objective": "Describe RNA structure", "computational_task": "bulk RNA"},
        {"observed": {}, "inferred": {}, "unknowns": [], "artifact_ids": [ref["artifact_id"] for ref in refs]},
        actor="scientist", data_classification="synthetic", rna_input_contract=binding)
    return planner, plan, analysis, branch, step


def test_new_state_cannot_bypass_abandoned_operation(tmp_path):
    from devbench.analysis.workflow_actions import WorkflowActions
    planner = FakePlanner(tmp_path)
    actions = WorkflowActions(planner, ledger(tmp_path), actor="scientist", selector=object())
    state = actions.create_state(planner.session_id)
    planner.fail_after_generate = True
    actions.perform("generate", state)
    with pytest.raises(ValueError, match="resume|existing"):
        actions.create_state(planner.session_id)
    assert planner.calls == ["generate"]


@pytest.mark.parametrize("representation", ["raw_counts", "normalized_expression"])
def test_real_planner_rna_audit_preserves_values_alignment_and_unknowns(tmp_path, representation):
    from devbench.analysis.workflow_actions import WorkflowActions
    planner, plan, *_ = rna_plan(tmp_path, representation)
    actions = WorkflowActions(planner, ledger(tmp_path), actor="scientist", selector=object())
    state = actions.create_state(plan["session_id"])
    after = actions.perform("audit", state)
    assert after["status"] == "ready"
    assert after["expected_planner_hash"] == state["expected_planner_hash"]
    observed = planner.get(plan["session_id"])["context"]["dataset_summary"]["observed"]["bulk_rna_input"]
    assert observed["representation"] == representation
    assert observed["metadata_alignment"]["reordered"]
    assert observed["metadata_catalog"]["donor"]["distinct_count"] == 1
    assert observed["biological_independence"] is None


def test_run_requires_retained_declaration_and_exact_trial_cursor(tmp_path):
    from devbench.analysis.workflow_actions import WorkflowActions
    planner = FakePlanner(tmp_path)
    actions = WorkflowActions(planner, ledger(tmp_path), actor="scientist", selector=object(), allow_data_execution=True)
    state = actions.create_state(planner.session_id)
    state.update(phase="run", trial_ids=["made-up-trial"])
    with pytest.raises(ValueError, match="declaration|cursor"):
        actions.perform("run", state)
    assert planner.calls == []


def test_tracing_rechecked_after_construction(tmp_path, monkeypatch):
    pytest.importorskip("langgraph")
    from devbench.analysis.workflow_actions import WorkflowActions
    from devbench.analysis.langgraph_workflow import SerialRNAWorkflow
    planner = FakePlanner(tmp_path)
    actions = WorkflowActions(planner, ledger(tmp_path), actor="scientist", selector=object())
    state = actions.create_state(planner.session_id)
    with SerialRNAWorkflow(actions, tmp_path / "checkpoint.sqlite", enabled=True) as workflow:
        monkeypatch.setenv("LANGSMITH_TRACING", "true")
        with pytest.raises(PermissionError, match="trac"):
            workflow.start(state)
    assert planner.calls == []


def executable_rna(tmp_path, representation="raw_counts", trials=1):
    from tests.test_extensible_methods import custom, FixtureExecutor
    from tests.test_method_planner import candidate, Selector, trial_spec, recommendation
    planner, plan, analysis, branch, _ = rna_plan(tmp_path, representation)
    contract, source, lock = custom(tmp_path)
    contract["inputs"]["metadata"] = {"artifact_type": "json", "required": True}
    method = planner.methods.register_method(contract, source=source, environment_lock=lock, actor="author")
    ids = plan["context"]["dataset_summary"]["artifact_ids"]
    step = {"inputs": {"dataset": ids[0], "metadata": ids[1]}, "method_version_id": method["version_id"]}
    item = candidate()
    item["assumptions"] = []
    specs = [trial_spec(step) for _ in range(trials)]
    for index, spec in enumerate(specs):
        spec["id"] = f"trial-{index}"
    selector = Selector({"candidates": [item]}, {"bindings": [{"candidate": "Unknown QC", "method_version_id": method["version_id"], "discovery": None}]},
        {"experiments": specs}, recommendation(None))
    class CountingExecutor(FixtureExecutor):
        calls = 0
        def execute(self, *args):
            self.calls += 1
            return super().execute(*args)
    return planner, plan, analysis, branch, method, specs, selector, CountingExecutor()


@pytest.mark.parametrize("representation", ["raw_counts", "normalized_expression"])
def test_real_serial_planner_two_trials_checkpoint_restart_and_no_promotions(tmp_path, representation):
    pytest.importorskip("langgraph")
    from devbench.analysis.workflow_actions import WorkflowActions
    from devbench.analysis.langgraph_workflow import SerialRNAWorkflow
    planner, plan, analysis, branch, method, specs, selector, executor = executable_rna(tmp_path, representation, trials=2)
    operations = ledger(tmp_path)
    choices = [{"draft_id": spec["id"], "branch_id": branch["branch_id"]} for spec in specs]
    actions = WorkflowActions(planner, operations, actor="scientist", selector=selector, version_ids=[method["version_id"]])
    state = actions.create_state(plan["session_id"])
    checkpoint = tmp_path / "checkpoint.sqlite"
    with SerialRNAWorkflow(actions, checkpoint, enabled=True) as workflow:
        result = workflow.start(state)
        assert "__interrupt__" in result
        assert len(selector.contexts) == 3 and executor.calls == 0
        assert "registry" not in selector.contexts[0]
    actions.declarations = choices
    with SerialRNAWorkflow(actions, checkpoint, enabled=True) as workflow:
        declared = workflow.resume(state["workflow_id"])
        assert "__interrupt__" in declared
        assert len(planner.get(plan["session_id"])["experiments"]) == 2
        assert executor.calls == 0
    actions.allow_data_execution, actions.executor = True, executor
    with SerialRNAWorkflow(actions, checkpoint, enabled=True) as workflow:
        final = workflow.resume(state["workflow_id"])
        assert final["status"] == "abstained"
        assert len(selector.contexts) == 4 and executor.calls == 2
        assert workflow.resume(state["workflow_id"])["status"] == "abstained"
    actual = planner.get(plan["session_id"])
    assert len(actual["results"]) == 2 and all(row["status"] == "success" for row in actual["results"])
    assert actual["approvals"] == {}
    assert planner.methods.analysis(analysis["analysis_id"])["revision_id"] == analysis["revision_id"]
    assert actual["context"]["dataset_summary"]["observed"]["bulk_rna_input"]["representation"] == representation


def test_checkpoint_resume_never_restores_runtime_execution_authority(tmp_path):
    pytest.importorskip("langgraph")
    from devbench.analysis.workflow_actions import WorkflowActions
    from devbench.analysis.langgraph_workflow import SerialRNAWorkflow
    planner, plan, _, branch, method, specs, selector, executor = executable_rna(tmp_path)
    actions = WorkflowActions(planner, ledger(tmp_path), actor="scientist", selector=selector,
        version_ids=[method["version_id"]], declarations=[{"draft_id": specs[0]["id"], "branch_id": branch["branch_id"]}])
    state = actions.create_state(plan["session_id"])
    with SerialRNAWorkflow(actions, tmp_path / "checkpoint.sqlite", enabled=True) as workflow:
        workflow.start(state)
        workflow.resume(state["workflow_id"])
        result = workflow.resume(state["workflow_id"])
        assert result["status"] == "failed" and result["failure"]["kind"] == "PermissionError"
    assert executor.calls == 0
    assert all(row["status"] == "declared" for row in planner.get(plan["session_id"])["experiments"])


def test_reconcile_completed_planner_effect_after_lost_ledger_write(tmp_path, monkeypatch):
    pytest.importorskip("langgraph")
    from devbench.analysis.workflow_actions import WorkflowActions
    from devbench.analysis.langgraph_workflow import SerialRNAWorkflow
    planner, plan, _, _, method, _, selector, _ = executable_rna(tmp_path)
    original = planner.generate_candidates
    def lost_result(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("Coordinator lost result after successful API completion")
    monkeypatch.setattr(planner, "generate_candidates", lost_result)
    actions = WorkflowActions(planner, ledger(tmp_path), actor="scientist", selector=selector, version_ids=[method["version_id"]])
    state = actions.create_state(plan["session_id"])
    with SerialRNAWorkflow(actions, tmp_path / "checkpoint.sqlite", enabled=True) as workflow:
        result = workflow.start(state)
        assert result["status"] == "reconciliation_required"
        assert len(selector.contexts) == 1
        reconciled = workflow.reconcile(state["workflow_id"])
        assert reconciled["status"] == "ready"
        resumed = workflow.resume(state["workflow_id"])
        assert "__interrupt__" in resumed
        assert len(selector.contexts) == 3


def test_terminal_resume_rejects_changed_plan(tmp_path):
    pytest.importorskip("langgraph")
    from devbench.analysis.workflow_actions import WorkflowActions
    from devbench.analysis.langgraph_workflow import SerialRNAWorkflow
    planner = FakePlanner(tmp_path)
    actions = WorkflowActions(planner, ledger(tmp_path), actor="scientist", selector=object())
    state = actions.create_state(planner.session_id)
    with SerialRNAWorkflow(actions, tmp_path / "checkpoint.sqlite", enabled=True) as workflow:
        workflow.start(state)
        workflow.resume(state["workflow_id"])
        planner.plan["changed"] = True
        with pytest.raises(ValueError, match="stale"):
            workflow.resume(state["workflow_id"])


@pytest.mark.parametrize("node,method_name", [("generate", "generate_candidates"), ("resolve", "resolve_capabilities"),
    ("design", "design_trials"), ("declare", "declare_trial"), ("run", "run_trial"), ("recommend", "recommend")])
def test_completed_api_effects_reconcile_without_new_model_or_execution(tmp_path, monkeypatch, node, method_name):
    pytest.importorskip("langgraph")
    from devbench.analysis.workflow_actions import WorkflowActions
    from devbench.analysis.langgraph_workflow import SerialRNAWorkflow
    planner, plan, _, branch, method, specs, selector, executor = executable_rna(tmp_path)
    for ports in specs[0]["inputs"].values():
        for name, identity in list(ports.items()):
            ports[name] = planner._trial_input_reference(identity)
    original = getattr(planner, method_name)
    count = 0
    def lost_result(*args, **kwargs):
        nonlocal count
        count += 1
        original(*args, **kwargs)
        raise RuntimeError("Lost coordinator result after completed API effect")
    monkeypatch.setattr(planner, method_name, lost_result)
    actions = WorkflowActions(planner, ledger(tmp_path), actor="scientist", selector=selector,
        version_ids=[method["version_id"]], declarations=[{"draft_id": specs[0]["id"], "branch_id": branch["branch_id"]}],
        allow_data_execution=True, executor=executor)
    state = actions.create_state(plan["session_id"])
    checkpoint = tmp_path / "checkpoint.sqlite"
    with SerialRNAWorkflow(actions, checkpoint, enabled=True) as workflow:
        result = workflow.start(state)
        while "__interrupt__" in result:
            result = workflow.resume(state["workflow_id"])
        assert result["phase"] == node and result["status"] == "reconciliation_required"
        before = (len(selector.contexts), executor.calls)
        result = workflow.reconcile(state["workflow_id"])
        assert (len(selector.contexts), executor.calls) == before
        result = workflow.resume(state["workflow_id"])
        while "__interrupt__" in result:
            result = workflow.resume(state["workflow_id"])
        assert result["status"] == "abstained"
        assert count == 1 and len(selector.contexts) == 4 and executor.calls == 1


def test_completed_ledger_replays_after_crash_before_graph_checkpoint(tmp_path, monkeypatch):
    pytest.importorskip("langgraph")
    from devbench.analysis.workflow_actions import WorkflowActions
    from devbench.analysis.langgraph_workflow import SerialRNAWorkflow
    planner, plan, _, branch, method, specs, selector, executor = executable_rna(tmp_path)
    actions = WorkflowActions(planner, ledger(tmp_path), actor="scientist", selector=selector,
        version_ids=[method["version_id"]], declarations=[{"draft_id": specs[0]["id"], "branch_id": branch["branch_id"]}],
        allow_data_execution=True, executor=executor)
    state = actions.create_state(plan["session_id"])
    checkpoint = tmp_path / "checkpoint.sqlite"
    class CoordinatorCrash(BaseException):
        pass
    with SerialRNAWorkflow(actions, checkpoint, enabled=True) as workflow:
        workflow.start(state)
        workflow.resume(state["workflow_id"])
        original = workflow._node
        def crash_after_run(node, cursor):
            result = original(node, cursor)
            if node == "run":
                raise CoordinatorCrash()
            return result
        monkeypatch.setattr(workflow, "_node", crash_after_run)
        with pytest.raises(CoordinatorCrash):
            workflow.resume(state["workflow_id"])
    assert executor.calls == 1
    with SerialRNAWorkflow(actions, checkpoint, enabled=True) as workflow:
        final = workflow.resume(state["workflow_id"])
        assert final["status"] == "abstained"
    assert executor.calls == 1 and len(selector.contexts) == 4


def test_terminal_resume_rejects_forged_success_and_outcome_reference(tmp_path):
    pytest.importorskip("langgraph")
    from devbench.analysis.workflow_actions import WorkflowActions
    from devbench.analysis.langgraph_workflow import SerialRNAWorkflow
    planner = FakePlanner(tmp_path)
    actions = WorkflowActions(planner, ledger(tmp_path), actor="scientist", selector=object())
    state = actions.create_state(planner.session_id)
    with SerialRNAWorkflow(actions, tmp_path / "checkpoint.sqlite", enabled=True) as workflow:
        workflow.start(state)
        final = workflow.resume(state["workflow_id"])
        final["status"] = "completed"
        final["outcome_ref"]["kind"] = "recommendation"
        workflow.graph.update_state(workflow._config(state["workflow_id"]), final, as_node="recommend")
        with pytest.raises(ValueError, match="outcome|terminal|retained"):
            workflow.resume(state["workflow_id"])


def test_cancellation_prevents_following_nodes_without_killing_active_work(tmp_path):
    pytest.importorskip("langgraph")
    from devbench.analysis.workflow_actions import WorkflowActions
    from devbench.analysis.langgraph_workflow import SerialRNAWorkflow
    planner = FakePlanner(tmp_path)
    operations = ledger(tmp_path)
    actions = WorkflowActions(planner, operations, actor="scientist", selector=object())
    state = actions.create_state(planner.session_id)
    with SerialRNAWorkflow(actions, tmp_path / "checkpoint.sqlite", enabled=True) as workflow:
        workflow.start(state)
        operations.cancel(state["workflow_id"])
        final = workflow.resume(state["workflow_id"])
        assert final["status"] == "cancelled"
    assert planner.calls == ["generate", "resolve", "design"]


def test_concurrent_real_resume_rejects_second_invocation_before_declaration(tmp_path, monkeypatch):
    pytest.importorskip("langgraph")
    from concurrent.futures import ThreadPoolExecutor
    import threading
    from devbench.analysis.workflow_actions import WorkflowActions
    from devbench.analysis.langgraph_workflow import SerialRNAWorkflow
    planner, plan, _, branch, method, specs, selector, executor = executable_rna(tmp_path)
    actions = WorkflowActions(planner, ledger(tmp_path), actor="scientist", selector=selector,
        version_ids=[method["version_id"]], declarations=[{"draft_id": specs[0]["id"], "branch_id": branch["branch_id"]}])
    state = actions.create_state(plan["session_id"])
    checkpoint = tmp_path / "checkpoint.sqlite"
    entered, release = threading.Event(), threading.Event()
    original = planner.declare_trial
    declarations = 0
    def blocked_declaration(*args, **kwargs):
        nonlocal declarations
        declarations += 1
        entered.set()
        assert release.wait(5)
        return original(*args, **kwargs)
    monkeypatch.setattr(planner, "declare_trial", blocked_declaration)
    with SerialRNAWorkflow(actions, checkpoint, enabled=True) as first, SerialRNAWorkflow(actions, checkpoint, enabled=True) as second:
        first.start(state)
        with ThreadPoolExecutor(max_workers=1) as pool:
            running = pool.submit(first.resume, state["workflow_id"])
            assert entered.wait(5)
            try:
                with pytest.raises(ValueError, match="active|lease"):
                    second.resume(state["workflow_id"])
            finally:
                release.set()
            assert "__interrupt__" in running.result(timeout=5)
    assert declarations == 1 and len(selector.contexts) == 3 and executor.calls == 0


def test_run_reconciliation_preserves_exact_controller_correlations(tmp_path, monkeypatch):
    pytest.importorskip("langgraph")
    from devbench.analysis.workflow_actions import WorkflowActions
    from devbench.analysis.langgraph_workflow import SerialRNAWorkflow
    planner, plan, _, branch, method, specs, selector, executor = executable_rna(tmp_path)
    binding = {"Unknown QC": {"controller_id": uuid.uuid4().hex, "correlation": {
        "project_id": "a" * 32, "scientific_run_id": "b" * 32, "attempt_id": "c" * 32,
        "analysis_version_id": "d" * 64, "dataset_version_ids": ["e" * 64]}}}
    original = planner.run_trial
    def lost_result(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("Lost coordinator result after controller-bound execution")
    monkeypatch.setattr(planner, "run_trial", lost_result)
    actions = WorkflowActions(planner, ledger(tmp_path), actor="scientist", selector=selector,
        version_ids=[method["version_id"]], declarations=[{"draft_id": specs[0]["id"], "branch_id": branch["branch_id"]}],
        allow_data_execution=True, executor=executor, execution_bindings=binding)
    state = actions.create_state(plan["session_id"])
    with SerialRNAWorkflow(actions, tmp_path / "checkpoint.sqlite", enabled=True) as workflow:
        workflow.start(state)
        workflow.resume(state["workflow_id"])
        assert workflow.resume(state["workflow_id"])["status"] == "reconciliation_required"
        workflow.reconcile(state["workflow_id"])
        assert workflow.resume(state["workflow_id"])["status"] == "abstained"
    assert executor.calls == 1
    actual = planner.get(plan["session_id"])
    assert actual["execution_correlations"] == {specs[0]["id"]: binding}
    assert actual["results"][0]["provenance"]["run_id"] == binding["Unknown QC"]["controller_id"]


def test_checkpoint_serializer_blocks_arbitrary_constructor_execution(tmp_path):
    pytest.importorskip("langgraph")
    import ormsgpack
    from langgraph.checkpoint.serde.jsonplus import EXT_CONSTRUCTOR_SINGLE_ARG
    from devbench.analysis.workflow_actions import WorkflowActions
    from devbench.analysis.langgraph_workflow import SerialRNAWorkflow
    planner = FakePlanner(tmp_path)
    actions = WorkflowActions(planner, ledger(tmp_path), actor="scientist", selector=object())
    CHECKPOINT_SIDE_EFFECTS.clear()
    payload = ormsgpack.packb(ormsgpack.Ext(EXT_CONSTRUCTOR_SINGLE_ARG,
        ormsgpack.packb([__name__, "unsafe_checkpoint_constructor", "should-not-execute"])))
    with SerialRNAWorkflow(actions, tmp_path / "checkpoint.sqlite", enabled=True) as workflow:
        workflow.graph.checkpointer.serde.loads_typed(("msgpack", payload))
    assert CHECKPOINT_SIDE_EFFECTS == []


def test_noop_design_reconciles_only_when_no_eligible_implementation(tmp_path, monkeypatch):
    pytest.importorskip("langgraph")
    from tests.test_method_planner import candidate, Selector, recommendation
    from devbench.analysis.workflow_actions import WorkflowActions
    from devbench.analysis.langgraph_workflow import SerialRNAWorkflow
    from devbench.analysis import workflow_operations
    planner, plan, *_ = rna_plan(tmp_path)
    item = candidate("Unimplemented scientific alternative")
    item["assumptions"] = []
    selector = Selector({"candidates": [item]}, {"bindings": [{"candidate": item["name"], "method_version_id": None, "discovery": None}]},
        recommendation(None))
    actions = WorkflowActions(planner, ledger(tmp_path), actor="scientist", selector=selector)
    state = actions.create_state(plan["session_id"])
    original_json = workflow_operations._json
    failed = False
    def lost_design_result(value):
        nonlocal failed
        if not failed and type(value) is dict and value.get("update", {}).get("phase") == "design":
            failed = True
            raise RuntimeError("Lost no-op design result before ledger persistence")
        return original_json(value)
    monkeypatch.setattr(workflow_operations, "_json", lost_design_result)
    with SerialRNAWorkflow(actions, tmp_path / "checkpoint.sqlite", enabled=True) as workflow:
        result = workflow.start(state)
        assert result["phase"] == "design" and result["status"] == "reconciliation_required"
        assert len(selector.contexts) == 2
        reconciled = workflow.reconcile(state["workflow_id"])
        assert reconciled["status"] == "ready"
        assert len(selector.contexts) == 2
        assert "__interrupt__" in workflow.resume(state["workflow_id"])
        assert workflow.resume(state["workflow_id"])["status"] == "abstained"
    assert len(selector.contexts) == 3


def test_checkpoint_database_rejects_hardlink_alias(tmp_path):
    pytest.importorskip("langgraph")
    import os
    from devbench.analysis.workflow_actions import WorkflowActions
    from devbench.analysis.langgraph_workflow import SerialRNAWorkflow
    planner = FakePlanner(tmp_path)
    actions = WorkflowActions(planner, ledger(tmp_path), actor="scientist", selector=object())
    checkpoint = tmp_path / "checkpoint.sqlite"
    with SerialRNAWorkflow(actions, checkpoint, enabled=True):
        pass
    alias = tmp_path / "alias.sqlite"
    os.link(checkpoint, alias)
    with pytest.raises(ValueError, match="hardlink|single|checkpoint"):
        SerialRNAWorkflow(actions, alias, enabled=True)
    assert planner.calls == []


@pytest.mark.parametrize("operation", ["resume", "reconcile"])
def test_checkpoint_database_replacement_rejected_before_dispatch(tmp_path, operation):
    pytest.importorskip("langgraph")
    from devbench.analysis.workflow_actions import WorkflowActions
    from devbench.analysis.langgraph_workflow import SerialRNAWorkflow
    planner = FakePlanner(tmp_path)
    actions = WorkflowActions(planner, ledger(tmp_path), actor="scientist", selector=object())
    state = actions.create_state(planner.session_id)
    checkpoint = tmp_path / "checkpoint.sqlite"
    with SerialRNAWorkflow(actions, checkpoint, enabled=True) as workflow:
        workflow.start(state)
        replacement = tmp_path / "replacement.sqlite"
        replacement.write_bytes(checkpoint.read_bytes())
        replacement.replace(checkpoint)
        with pytest.raises(ValueError, match="replaced|identity"):
            getattr(workflow, operation)(state["workflow_id"])
    assert planner.calls == ["generate", "resolve", "design"]


def test_full_invocation_lease_rejects_simultaneous_start(tmp_path):
    pytest.importorskip("langgraph")
    import fcntl
    from devbench.analysis.workflow_actions import WorkflowActions
    from devbench.analysis.langgraph_workflow import SerialRNAWorkflow
    planner = FakePlanner(tmp_path)
    actions = WorkflowActions(planner, ledger(tmp_path), actor="scientist", selector=object())
    state = actions.create_state(planner.session_id)
    with SerialRNAWorkflow(actions, tmp_path / "checkpoint.sqlite", enabled=True) as workflow:
        with workflow.invocation_lease(state["workflow_id"]):
            with pytest.raises(ValueError, match="active|lease"):
                workflow.start(state)
    assert planner.calls == []
