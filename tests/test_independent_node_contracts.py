"""Offline transition guards for the independent RNA controller."""
from copy import deepcopy

import pytest

from devbench.analysis.langgraph_investigations import IndependentRNAWorkflow
from devbench.analysis.workflow_state import SERIAL_ROUTE, digest
from tests.test_langgraph_investigations import factory, independent_setup


def initial_cursor(workflow, actions, plan):
    parent = workflow._freeze_campaign(actions.create_state(plan["session_id"]))
    return {"format": "devbench.rna-independent-workflow/1", "workflow_id": parent["workflow_id"],
        "parent_session_id": parent["session_id"], "parent_cursor": parent,
        "expected_plan_hash": parent["expected_planner_hash"], "binding_digest": parent["binding_digest"],
        "policy_digest": digest(workflow.frozen_policy), "stage": workflow.policy["stage"], "decision": None,
        "allocated_children": 0, "child_ids": [], "dispatch_refs": {}, "result_refs": {},
        "reconciliation_ref": None, "phase": "audit", "status": "ready", "failure": None}


@pytest.mark.parametrize("node", ["question", "discovery", "generate", "candidate_review", "resolve", "implementation", "design", "design_review",
                                    "declare", "run", "verify", "recommend", "conclusion_review", "preliminary_explanation", "reassess", "investigation_decision", "investigation_barrier", "attach"])
def test_reordered_node_is_rejected_before_creating_operation_or_dispatch(tmp_path, node):
    planner, plan, backend, ledger, actions = independent_setup(tmp_path, 1)
    with factory()(mode="independent_investigations", actions=actions,
                   checkpoint_path=tmp_path / "guard.sqlite", investigation_backend=backend) as workflow:
        cursor = initial_cursor(workflow, actions, plan)
        state = {"cursor": cursor, "child_returns": {}}
        handler = {"investigation_decision": workflow._decision, "investigation_barrier": workflow._barrier,
                   "attach": workflow._attach}.get(node)
        if handler is None:
            selected = workflow._special_parent if node in {"preliminary_explanation", "reassess"} else workflow._parent_node
            handler = lambda state: selected(node, state)
        result = handler(state)
        assert result["cursor"]["status"] == "reconciliation_required"
        assert "node" in result["cursor"]["failure"]["message"].casefold()
        assert ledger.replay(plan["session_id"]) == []
        assert actions.selector.contexts == [] and backend.child_contexts == []
        assert planner.get(plan["session_id"]) == planner.methods.get("method_plan_snapshot", cursor["expected_plan_hash"])


@pytest.mark.parametrize("status", ["failed", "cancelled", "completed", "abstained", "reconciliation_required", "review_required"])
def test_terminal_or_suspended_cursor_cannot_reenter_audit(tmp_path, status):
    _, plan, backend, ledger, actions = independent_setup(tmp_path, 0)
    with factory()(mode="independent_investigations", actions=actions,
                   checkpoint_path=tmp_path / "terminal.sqlite", investigation_backend=backend) as workflow:
        cursor = initial_cursor(workflow, actions, plan)
        cursor["status"] = status
        result = workflow._parent_node("audit", {"cursor": cursor, "child_returns": {}})
        assert result["cursor"]["status"] == "reconciliation_required"
        assert ledger.replay(plan["session_id"]) == []


def test_forged_outer_phase_does_not_authorize_independent_model_dispatch(tmp_path):
    _, plan, backend, ledger, actions = independent_setup(tmp_path, 1)
    with factory()(mode="independent_investigations", actions=actions,
                   checkpoint_path=tmp_path / "forged.sqlite", investigation_backend=backend) as workflow:
        cursor = initial_cursor(workflow, actions, plan)
        cursor["phase"] = "attach"
        result = workflow._special_parent("reassess", {"cursor": cursor, "child_returns": {}})
        assert result["cursor"]["status"] == "reconciliation_required"
        assert ledger.replay(plan["session_id"]) == []
        assert actions.selector.contexts == []


def decision_state(workflow, actions, plan):
    state = {"cursor": initial_cursor(workflow, actions, plan), "child_returns": {}}
    for node in SERIAL_ROUTE[:SERIAL_ROUTE.index("generate") + 1]:
        state.update(workflow._parent_node(node, state))
        assert state["cursor"]["status"] == "ready"
    state.update(workflow._decision(state))
    assert state["cursor"]["status"] == "ready"
    assert state["cursor"]["child_ids"]
    return state


def test_unadmitted_child_dispatch_stops_before_backend_read_or_call(tmp_path, monkeypatch):
    _, plan, backend, ledger, actions = independent_setup(tmp_path, 1)
    with factory()(mode="independent_investigations", actions=actions,
                   checkpoint_path=tmp_path / "child.sqlite", investigation_backend=backend) as workflow:
        cursor = decision_state(workflow, actions, plan)["cursor"]
        admitted = cursor["child_ids"][0]
        reservations = ledger.campaign_reservations(plan["session_id"])
        monkeypatch.setattr(backend, "read_dispatch", lambda reference: pytest.fail("Unadmitted child reached backend"))
        with pytest.raises(ValueError, match="decision|admitted"):
            workflow._launch_child({"investigation_id": "f" * 32, "workflow_id": plan["session_id"],
                                    "dispatch_ref": cursor["dispatch_refs"][admitted]})
        assert backend.child_contexts == []
        assert ledger.campaign_reservations(plan["session_id"]) == reservations


def test_barrier_requires_actual_child_evidence_not_a_success_status(tmp_path):
    _, plan, backend, ledger, actions = independent_setup(tmp_path, 1)
    with factory()(mode="independent_investigations", actions=actions,
                   checkpoint_path=tmp_path / "barrier.sqlite", investigation_backend=backend) as workflow:
        state = decision_state(workflow, actions, plan)
        child = state["cursor"]["child_ids"][0]
        state["child_returns"] = {child: {"status": "completed", "result_ref": None}}
        result = workflow._barrier(state)
        assert result["cursor"]["status"] == "reconciliation_required"
        assert "evidence" in result["cursor"]["failure"]["message"]
        assert ledger.get(plan["session_id"], "investigation_barrier", 0) is None
        assert backend.child_contexts == []


def test_skipping_barrier_cannot_attach_even_if_outer_phase_is_forged(tmp_path):
    _, plan, backend, ledger, actions = independent_setup(tmp_path, 1)
    with factory()(mode="independent_investigations", actions=actions,
                   checkpoint_path=tmp_path / "skip-barrier.sqlite", investigation_backend=backend) as workflow:
        state = decision_state(workflow, actions, plan)
        state["cursor"]["phase"] = "investigation_barrier"
        result = workflow._attach(state)
        assert result["cursor"]["status"] == "reconciliation_required"
        assert ledger.get(plan["session_id"], "attach", 0) is None
        assert backend.child_contexts == []


def test_postfit_decision_cannot_skip_required_preliminary_explanation():
    from types import SimpleNamespace
    workflow = IndependentRNAWorkflow.__new__(IndependentRNAWorkflow)
    workflow.readonly = False
    workflow.policy = {"stage": "post_fit_explanation"}
    workflow.actions = SimpleNamespace(planner=SimpleNamespace(get=lambda identity: {"results": [{"status": "success"}]}))
    workflow._completed_node = lambda *args: {"result": {}}
    workflow._check = lambda cursor: None
    workflow._verified_evidence = lambda plan: {"assessment_available": True}
    cursor = {"workflow_id": "a" * 32, "parent_session_id": "a" * 32, "stage": "post_fit_explanation",
        "phase": "verify", "status": "ready", "failure": None,
        "parent_cursor": {"status": "ready", "phase": "verify", "operation_refs": ["verify:0"],
                          "trial_ids": [], "trial_index": 0}}
    with pytest.raises(ValueError, match="preliminary"):
        workflow._check_node_entry("investigation_decision", cursor)


def test_crashed_barrier_commit_is_reconciled_without_redispatch(tmp_path, monkeypatch):
    _, plan, backend, ledger, actions = independent_setup(tmp_path, 1)
    original = ledger.run
    def crash(workflow_id, node, attempt, request, callback):
        if node == "investigation_barrier":
            def interrupted():
                callback()
                raise RuntimeError("Barrier evidence verified before commit was lost")
            return original(workflow_id, node, attempt, request, interrupted)
        return original(workflow_id, node, attempt, request, callback)
    with factory()(mode="independent_investigations", actions=actions,
                   checkpoint_path=tmp_path / "crash-barrier.sqlite", investigation_backend=backend) as workflow:
        monkeypatch.setattr(ledger, "run", crash)
        stopped = workflow.start(actions.create_state(plan["session_id"]))
        assert stopped["status"] == "reconciliation_required"
        assert ledger.get(plan["session_id"], "investigation_barrier", 0)["status"] == "uncertain"
        calls = deepcopy(actions.selector.contexts)
        children = deepcopy(backend.child_contexts)
        monkeypatch.setattr(ledger, "run", original)
        recovered = workflow.reconcile(plan["session_id"])
        assert recovered["status"] == "ready"
        assert ledger.get(plan["session_id"], "investigation_barrier", 0)["status"] == "completed"
        assert actions.selector.contexts == calls and backend.child_contexts == children


def test_barrier_rejects_success_without_captured_result(tmp_path, monkeypatch):
    _, plan, backend, ledger, actions = independent_setup(tmp_path, 1)
    with factory()(mode="independent_investigations", actions=actions,
                   checkpoint_path=tmp_path / "missing-result.sqlite", investigation_backend=backend) as workflow:
        state = decision_state(workflow, actions, plan)
        child = state["cursor"]["child_ids"][0]
        monkeypatch.setattr(backend, "reconcile_capture", lambda reference: {
            "status": "completed", "result_ref": None, "capture_ref": None})
        state["child_returns"] = {child: {"status": "completed", "result_ref": None}}
        result = workflow._barrier(state)
        assert result["cursor"]["status"] == "reconciliation_required"
        assert ledger.get(plan["session_id"], "investigation_barrier", 0) is None
        assert backend.child_contexts == []


def test_ready_label_cannot_override_retained_blocked_investigation_decision():
    workflow = IndependentRNAWorkflow.__new__(IndependentRNAWorkflow)
    workflow.readonly = False
    workflow.policy = {"stage": "pre_fit_methods"}
    retained = {"decision": {"status": "blocked"}, "child_ids": [], "dispatch_refs": {}}
    workflow._completed_node = lambda *args: {"result": retained}
    workflow._check = lambda cursor: None
    cursor = {**retained, "stage": "pre_fit_methods", "phase": "investigation_decision", "status": "ready", "failure": None,
        "parent_cursor": {"status": "ready", "phase": "generate", "operation_refs": ["generate:0"],
                          "trial_ids": [], "trial_index": 0}}
    with pytest.raises(ValueError, match="blocked"):
        workflow._check_node_entry("candidate_review", cursor)


def test_independent_bridge_requires_exact_completed_special_operations(tmp_path):
    import devbench.analysis.langgraph_investigations as module
    assert hasattr(module, "verify_parent_bridge"), "Independent special steps need an evidence-verified snapshot bridge"
    planner, plan, backend, ledger, actions = independent_setup(tmp_path, 1)
    with factory()(mode="independent_investigations", actions=actions,
                   checkpoint_path=tmp_path / "bridge.sqlite", investigation_backend=backend) as workflow:
        paused = workflow.start(actions.create_state(plan["session_id"]))
        assert paused["status"] == "review_required", paused.get("failure")
        before_hash = ledger.get(plan["session_id"], "generate", 0)["result"]["after_planner_hash"]
        after_hash = ledger.get(plan["session_id"], "reassess", 0)["result"]["after_plan_hash"]
        assert module.verify_parent_bridge(planner, ledger, plan["session_id"], before_hash, after_hash) is True
        with pytest.raises(ValueError):
            module.verify_parent_bridge(planner, ledger, plan["session_id"], before_hash, "f" * 64)
        with pytest.raises(ValueError):
            module.verify_parent_bridge(planner, ledger, plan["session_id"], after_hash, before_hash)


def test_child_checks_current_planner_snapshot_before_model_dispatch(tmp_path, monkeypatch):
    planner, plan, backend, ledger, actions = independent_setup(tmp_path, 1)
    with factory()(mode="independent_investigations", actions=actions,
                   checkpoint_path=tmp_path / "stale-child.sqlite", investigation_backend=backend) as workflow:
        state = decision_state(workflow, actions, plan)
        cursor = state["cursor"]
        child = cursor["child_ids"][0]
        changed = deepcopy(planner.get(plan["session_id"]))
        changed["scientific_candidates"][0]["name"] = "Unrecorded changed scientific plan"
        monkeypatch.setattr(planner, "get", lambda identity: deepcopy(changed))
        before = ledger.campaign_reservations(plan["session_id"])
        with pytest.raises(ValueError, match="snapshot|stale"):
            workflow._launch_child({"investigation_id": child, "workflow_id": plan["session_id"],
                                    "dispatch_ref": cursor["dispatch_refs"][child]})
        assert backend.child_contexts == []
        assert ledger.campaign_reservations(plan["session_id"]) == before


def test_independent_graph_contains_mandatory_scientific_stages(tmp_path):
    _, _, backend, _, actions = independent_setup(tmp_path, 0)
    with factory()(mode="independent_investigations", actions=actions,
                   checkpoint_path=tmp_path / "scientific-stages.sqlite", investigation_backend=backend) as workflow:
        required = {"discovery", "candidate_review", "implementation", "design_review", "conclusion_review"}
        assert required <= set(workflow.graph.nodes)
        graph = workflow.graph.get_graph()
        edges = {(edge.source, edge.target) for edge in graph.edges}
        assert {("question", "discovery"), ("discovery", "generate"), ("generate", "investigation_decision"), ("reassess", "candidate_review"),
                ("investigation_decision", "candidate_review"), ("candidate_review", "resolve"), ("resolve", "implementation"), ("implementation", "design"), ("design", "design_review"),
                ("design_review", "review"), ("recommend", "conclusion_review"), ("conclusion_review", "__end__")} <= edges


def test_independent_graph_blocks_missing_scientific_runtime_before_candidate_dispatch(tmp_path):
    _, plan, backend, ledger, actions = independent_setup(tmp_path, 0)
    actions.scientific_workflow = None
    with factory()(mode="independent_investigations", actions=actions,
                   checkpoint_path=tmp_path / "missing-science.sqlite", investigation_backend=backend) as workflow:
        stopped = workflow.start(actions.create_state(plan["session_id"]))
        assert stopped["status"] in {"failed", "reconciliation_required", "review_required"}, stopped
        assert stopped["phase"] in {"audit", "discovery"}, stopped.get("failure")
        assert stopped["failure"]["kind"] == "scientific_workflow_unconfigured"
        assert ledger.get(plan["session_id"], "generate", 0) is None
        assert backend.child_contexts == [] and actions.selector.contexts == []


def test_independent_recommendation_is_provisional_until_conclusion_review(tmp_path):
    _, plan, backend, ledger, actions = independent_setup(tmp_path, 0)
    with factory()(mode="independent_investigations", actions=actions,
                   checkpoint_path=tmp_path / "conclusion-review.sqlite", investigation_backend=backend) as workflow:
        paused = workflow.start(actions.create_state(plan["session_id"]))
        assert paused["status"] == "review_required", paused.get("failure")
        final = workflow.resume(plan["session_id"])
        assert final["phase"] == "conclusion_review", final.get("failure")
        assert final["status"] == "abstained", final.get("failure")
        assert ledger.get(plan["session_id"], "recommend", 0)["result"]["update"]["status"] == "ready"
        assert ledger.get(plan["session_id"], "conclusion_review", 0)["result"]["update"]["status"] == "abstained"
        assert final["parent_cursor"]["operation_refs"][-1] == "conclusion_review:0"


def test_candidate_review_covers_reassessed_candidates_before_registry_exposure(tmp_path):
    planner, plan, backend, ledger, actions = independent_setup(tmp_path, 1)
    with factory()(mode="independent_investigations", actions=actions,
                   checkpoint_path=tmp_path / "review-final-candidates.sqlite", investigation_backend=backend) as workflow:
        paused = workflow.start(actions.create_state(plan["session_id"]))
        assert paused["status"] == "review_required", paused.get("failure")
        reassessed = ledger.get(plan["session_id"], "reassess", 0)
        review = ledger.get(plan["session_id"], "candidate_review", 0)
        assert review["request"]["expected_planner_hash"] == reassessed["result"]["after_plan_hash"]
        snapshot = planner.methods.get("method_plan_snapshot", review["request"]["expected_planner_hash"])
        assert snapshot["scientific_candidates"] == planner.methods.get(
            "method_plan_snapshot", reassessed["result"]["after_plan_hash"])["scientific_candidates"]
        assert snapshot["registry"] == {} and snapshot["candidates"] == []
        graph = workflow.graph.get_graph()
        edges = {(edge.source, edge.target) for edge in graph.edges}
        assert ("reassess", "candidate_review") in edges
        assert ("reassess", "resolve") not in edges
        assert ("investigation_decision", "resolve") not in edges
