"""Node contracts must reject invalid routes before any model or executor call."""
from tests.support.workflow_science_fixtures import RecordingScientificRuntime
from copy import deepcopy

import pytest

from devbench.analysis.workflow_actions import WorkflowActions
from devbench.analysis.langgraph_workflow import SerialRNAWorkflow
from tests.test_langgraph_workflow import FakePlanner, ledger


def setup_actions(tmp_path):
    planner = FakePlanner(tmp_path)
    actions = WorkflowActions(planner, ledger(tmp_path), actor="scientist", selector=object(), scientific_workflow=RecordingScientificRuntime())
    return planner, actions, actions.create_state(planner.session_id)


@pytest.mark.parametrize("node", ["question", "discovery", "generate", "candidate_review", "resolve", "implementation",
                                 "design", "design_review", "review", "declare", "execution_review", "run",
                                 "verify", "recommend", "conclusion_review"])
def test_node_cannot_skip_prerequisites(tmp_path, node):
    planner, actions, state = setup_actions(tmp_path)
    with pytest.raises(ValueError, match="node|predecessor|route|operation|cursor"):
        actions.perform(node, state)
    assert planner.calls == []
    assert actions.ledger.replay(state["workflow_id"]) == []


def test_forged_phase_and_operation_names_need_actual_completed_evidence(tmp_path):
    planner, actions, state = setup_actions(tmp_path)
    state.update(phase="question", operation_refs=["audit:0", "question:0"])
    with pytest.raises(ValueError, match="evidence|retained|completed|route"):
        actions.perform("generate", state)
    assert planner.calls == []


@pytest.mark.parametrize("status", ["failed", "completed", "abstained", "reconciliation_required"])
def test_node_cannot_dispatch_from_nonready_state(tmp_path, status):
    planner, actions, state = setup_actions(tmp_path)
    state["status"] = status
    with pytest.raises(ValueError, match="ready|status|route"):
        actions.perform("audit", state)
    assert planner.calls == []


def test_serial_start_requires_unused_initial_cursor(tmp_path):
    planner, actions, state = setup_actions(tmp_path)
    state["phase"] = "recommend"
    with SerialRNAWorkflow(actions, tmp_path / "checkpoints.sqlite", enabled=True) as workflow:
        with pytest.raises(ValueError, match="initial|unused|audit"):
            workflow.start(state)
    assert planner.calls == []


def test_review_gates_have_durable_completion_evidence(tmp_path):
    planner, actions, state = setup_actions(tmp_path)
    with SerialRNAWorkflow(actions, tmp_path / "checkpoints.sqlite", enabled=True) as workflow:
        workflow.start(state)
        assert actions.ledger.get(state["workflow_id"], "review", 0) is None
        result = workflow.resume(state["workflow_id"])
        assert result["status"] == "abstained"
    for node in ("review", "execution_review"):
        retained = actions.ledger.get(state["workflow_id"], node, 0)
        assert retained is not None and retained["status"] == "completed"
    assert planner.calls == ["generate", "resolve", "design", "verify", "recommend"]


def test_readonly_node_rejects_planner_mutation_before_completion(tmp_path, monkeypatch):
    planner, actions, state = setup_actions(tmp_path)
    def invalid_audit(plan):
        planner.plan["unexpected"] = "mutation"
    monkeypatch.setattr(actions, "_audit", invalid_audit)
    result = actions.perform("audit", state)
    assert result["status"] == "reconciliation_required"
    retained = actions.ledger.get(state["workflow_id"], "audit", 0)
    assert retained["status"] != "completed"


def test_next_node_rejects_state_disagreeing_with_retained_output(tmp_path):
    planner, actions, state = setup_actions(tmp_path)
    state = actions.perform("audit", state)
    state = actions.perform("question", state)
    state["trial_ids"] = ["invented-trial"]
    with pytest.raises(ValueError, match="trial|output|cursor"):
        actions.perform("generate", state)
    assert planner.calls == []


def test_completed_node_replay_preserves_single_dispatch(tmp_path):
    planner, actions, state = setup_actions(tmp_path)
    state = actions.perform("audit", state)
    state = actions.perform("question", state)
    state = actions.perform("discovery", state)
    before = deepcopy(state)
    result = actions.perform("generate", before)
    assert actions.perform("generate", before) == result
    assert planner.calls == ["generate"]


def test_model_route_cannot_change_between_nodes(tmp_path):
    planner, actions, state = setup_actions(tmp_path)
    class Selector:
        model = "first"
        def configuration(self):
            return {"backend": "recorded_fixture", "model": self.model}
    actions.selector = Selector()
    state = actions.perform("audit", state)
    state = actions.perform("question", state)
    actions.selector.model = "different"
    with pytest.raises(ValueError, match="selector|identity|route"):
        actions.perform("generate", state)
    assert planner.calls == []
    assert actions.ledger.get(state["workflow_id"], "generate", 0) is None


def test_next_node_rejects_adopting_an_unrecorded_planner_change(tmp_path):
    from devbench.analysis.workflow_state import digest
    planner, actions, state = setup_actions(tmp_path)
    for node in ("audit", "question", "discovery", "generate", "candidate_review"):
        state = actions.perform(node, state)
    planner.plan["scientific_candidates"] = [{"name": "Unrecorded replacement"}]
    state["expected_planner_hash"] = digest(planner.get(planner.session_id))
    with pytest.raises(ValueError, match="hash|chain|binding|bridge"):
        actions.perform("resolve", state)
    assert planner.calls == ["generate"]
    assert actions.ledger.get(state["workflow_id"], "resolve", 0) is None
