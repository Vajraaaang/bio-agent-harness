"""Scientific stage prerequisites are independently evidenced before dispatch."""
from copy import deepcopy

import pytest

from devbench.analysis.workflow_actions import WorkflowActions
from devbench.analysis.workflow_state import SERIAL_ROUTE, digest
from tests.test_langgraph_workflow import FakePlanner, ledger


class RetainedRuntime:
    """Offline adapter whose retained source is checked independently on replay."""
    def __init__(self):
        self.calls = []
        self.records = {}
        self.identity = "offline-test-provider"

    def configuration(self):
        return {"provider": self.identity}

    def perform(self, node, planner, session_id, *, actor, selector):
        self.calls.append(node)
        planner_hash = digest(planner.get(session_id))
        envelope = {"record_ref": "test-immutable-discovery", "status": "descriptive", "stage": node,
            "material_sha256": digest({"supplied_sources": []}),
            "before_planner_hash": planner_hash, "after_planner_hash": planner_hash}
        self.records[node] = deepcopy(envelope)
        return envelope

    def verify(self, node, planner, session_id, evidence, *, plan_snapshot):
        if evidence != self.records[node] or digest(plan_snapshot) != evidence["after_planner_hash"]:
            raise ValueError("Scientific source material was altered")

    def reconcile(self, node, planner, session_id, *, before_planner_hash):
        evidence = self.records[node]
        if evidence["before_planner_hash"] != before_planner_hash:
            raise ValueError("Retained science artifact has a different invocation")
        return deepcopy(evidence)


def actions_at_question(tmp_path, runtime=None):
    planner = FakePlanner(tmp_path)
    actions = WorkflowActions(planner, ledger(tmp_path), actor="scientist", selector=object(),
        scientific_workflow=runtime)
    state = actions.create_state(planner.session_id)
    return planner, actions, actions.perform("question", actions.perform("audit", state))


def test_missing_scientific_runtime_blocks_discovery_without_fake_receipt(tmp_path):
    planner, actions, state = actions_at_question(tmp_path)
    blocked = actions.perform("discovery", state)
    assert blocked["status"] == "review_required"
    assert blocked["phase"] == "discovery"
    assert blocked["failure"]["kind"] == "scientific_workflow_unconfigured"
    assert "discovery:0" not in blocked["operation_refs"]
    assert actions.ledger.get(planner.session_id, "discovery", 0) is None
    assert planner.calls == []


def test_real_graph_stops_at_missing_discovery_runtime(tmp_path):
    from devbench.analysis.langgraph_workflow import SerialRNAWorkflow
    planner = FakePlanner(tmp_path)
    actions = WorkflowActions(planner, ledger(tmp_path), actor="scientist", selector=object())
    with SerialRNAWorkflow(actions, tmp_path / "checkpoint.sqlite") as workflow:
        result = workflow.start(actions.create_state(planner.session_id))
    assert result["phase"] == "discovery"
    assert result["status"] == "review_required"
    assert planner.calls == []


@pytest.mark.parametrize("downstream", ["generate", "candidate_review", "resolve", "implementation",
    "design", "design_review", "review", "declare", "execution_review", "run", "verify", "recommend", "conclusion_review"])
def test_missing_scientific_prerequisite_prevents_downstream_dispatch(tmp_path, downstream):
    planner, actions, state = actions_at_question(tmp_path, RetainedRuntime())
    with pytest.raises(ValueError, match="route|predecessor"):
        actions.perform(downstream, state)
    assert planner.calls == []
    assert actions.scientific_workflow.calls == []


def test_retained_discovery_replay_does_not_repeat_search(tmp_path):
    runtime = RetainedRuntime()
    planner, actions, state = actions_at_question(tmp_path, runtime)
    completed = actions.perform("discovery", state)
    assert completed["status"] == "ready"
    assert actions.perform("discovery", state) == completed
    assert runtime.calls == ["discovery"]
    assert planner.calls == []
    assert actions.check(completed)


def test_altered_retained_science_material_blocks_advance(tmp_path):
    runtime = RetainedRuntime()
    planner, actions, state = actions_at_question(tmp_path, runtime)
    completed = actions.perform("discovery", state)
    runtime.records["discovery"]["material_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="altered"):
        actions.perform("generate", completed)
    assert planner.calls == []


def test_changed_scientific_runtime_identity_blocks_advance(tmp_path):
    runtime = RetainedRuntime()
    planner, actions, state = actions_at_question(tmp_path, runtime)
    completed = actions.perform("discovery", state)
    runtime.identity = "different-provider"
    with pytest.raises(ValueError, match="identity"):
        actions.perform("generate", completed)
    assert planner.calls == []


def test_scientific_review_contracts_are_explicit_serial_nodes():
    assert SERIAL_ROUTE == ("audit", "question", "discovery", "generate", "candidate_review", "resolve",
        "implementation", "design", "design_review", "review", "declare", "execution_review", "run",
        "verify", "recommend", "conclusion_review")


def test_search_completion_reconciles_after_crash_without_second_dispatch(tmp_path):
    runtime = RetainedRuntime()
    planner, actions, state = actions_at_question(tmp_path, runtime)
    perform = runtime.perform

    def crash_after_retention(*args, **kwargs):
        perform(*args, **kwargs)
        raise RuntimeError("Crash after retaining bounded discovery")

    runtime.perform = crash_after_retention
    uncertain = actions.perform("discovery", state)
    assert uncertain["status"] == "reconciliation_required"
    assert actions.perform("discovery", state)["status"] == "reconciliation_required"
    assert runtime.calls == ["discovery"]
    recovered = actions.reconcile(uncertain)
    assert recovered["status"] == "ready"
    assert recovered["operation_refs"][-1] == "discovery:0"
    assert runtime.calls == ["discovery"]
    assert actions.check(recovered)


def test_blocked_source_evidence_never_becomes_successful_review(tmp_path):
    runtime = RetainedRuntime()
    planner, actions, state = actions_at_question(tmp_path, runtime)
    perform = runtime.perform

    def unavailable_source(*args, **kwargs):
        envelope = perform(*args, **kwargs)
        envelope["status"] = "blocked"
        runtime.records["discovery"] = deepcopy(envelope)
        return envelope

    runtime.perform = unavailable_source
    blocked = actions.perform("discovery", state)
    assert blocked["status"] == "review_required"
    assert blocked["failure"]["kind"] == "scientific_evidence_blocked"
    assert actions.check(blocked)
    with pytest.raises(ValueError, match="ready"):
        actions.perform("generate", blocked)
    assert planner.calls == []
