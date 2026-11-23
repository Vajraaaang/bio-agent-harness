"""T20 protocol mechanics and real exploratory computation; no model qualification."""
import json
from copy import deepcopy

import pytest

from devbench.analysis.checkpoints import digest
from devbench.analysis.episode_agent import AgentLimits
from devbench.engine.workflow_operations import OperationBlocked
from tests.test_rna_investigation_parent_integration import metered_setup


def decision(kind="stop", **values):
    action = {"kind": kind, "rationale": "Synthetic protocol exercise", "evidence_ids": [], **values}
    if kind == "stop":
        action.update(disposition="partial", reason="empty_ready_frontier",
                      unresolved=["Qualified inference remains blocked"],
                      resumption_conditions=["Independent qualification and current review"])
    return action


def test_controller_contract_is_additive_closed_and_registered():
    from devbench.analysis import method_planner_contract as contract
    from devbench.engine.episode_codex import selection_contract, selection_instruction

    assert hasattr(contract, "ADAPTIVE_CONTROLLER_SCHEMA"), "The typed adaptive controller contract is missing"
    schema, instructions = selection_contract({"decision_kind": "adaptive_controller"})
    assert schema == contract.ADAPTIVE_CONTROLLER_SCHEMA
    assert "No fixed sequence" in instructions
    assert "adaptive" in selection_instruction({"decision_kind": "adaptive_controller"})
    import jsonschema
    assert contract.validate_adaptive_controller_decision(decision()) == decision()
    jsonschema.validate({"action": decision()}, schema)
    for extra in ({"qualified": True}, {"clear_hold": "hold"}, {"outputs": {"estimate": 1}}):
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate({"action": {**decision(), **extra}}, schema)
    for kind in ("insert", "revise", "dispatch", "review", "schedule_check", "respond",
                 "declare_sweep", "query_literature", "retire_branch"):
        assert kind in json.dumps(schema)


def shared_runtime(tmp_path, *answers):
    from devbench.analysis import episode_agent
    assert hasattr(episode_agent, "CampaignSelectionInvoker"), "One shared trusted role invocation path is missing"
    planner, state, selector, backend, ledger, *_ = metered_setup(tmp_path, *answers)
    runtime = episode_agent.CampaignSelectionInvoker(planner, state["session_id"],
        limits=AgentLimits(max_selections=24, memory_mb=512, max_context_bytes=65536),
        allow_model_execution=False)
    return runtime, planner, state, selector, backend, ledger


def invoke(runtime, planner, selector, *, role="controller", turn=0, context=None):
    return runtime.invoke(selector, context or {"decision_kind": "adaptive_controller"},
        planner.methods.root / "t20-turns" / (role + str(turn)), role=role,
        purpose="scientific_review" if role == "challenger" else "adaptive_controller",
        node="t20-" + role, attempt=turn)


def test_mixed_roles_share_one_campaign_and_exact_capture(tmp_path):
    runtime, planner, state, selector, backend, ledger = shared_runtime(tmp_path, decision(), decision())
    first = invoke(runtime, planner, selector)
    second = invoke(runtime, planner, selector, role="challenger", context={"decision_kind": "scientific_review"})
    records = ledger.campaign_reservations(state["model_campaign"]["campaign_id"])
    assert len(records) == 2 and all(row["child_id"] is None for row in records)
    assert {row["request"]["role"] for row in records} == {"controller", "challenger"}
    assert ledger.get_campaign(records[0]["campaign_id"])["verified_spent_tokens"] == 24
    assert first.evidence["protocol_scope"] == second.evidence["protocol_scope"] == "protocol_only"
    assert first.evidence["campaign_usage"]["normalized_total_tokens"] == 12
    assert second.evidence["campaign_id"] == first.evidence["campaign_id"]


def test_unknown_usage_blocks_both_roles_and_reopen(tmp_path):
    runtime, planner, state, selector, backend, ledger = shared_runtime(tmp_path, decision(), decision())
    backend.unknown_usage = True
    with pytest.raises(OperationBlocked, match="unresolved_usage"):
        invoke(runtime, planner, selector)
    backend.unknown_usage = False
    with pytest.raises(OperationBlocked, match="unresolved_usage"):
        invoke(runtime, planner, selector, role="challenger", context={"decision_kind": "scientific_review"})
    assert len(selector.contexts) == 1
    campaign = ledger.get_campaign(state["model_campaign"]["campaign_id"])
    assert campaign["unresolved_count"] == 1 and campaign["outstanding_reserved_tokens"] > 0


def test_completed_invocation_reconciles_without_redispatch(tmp_path):
    runtime, planner, _, selector, _, ledger = shared_runtime(tmp_path, decision())
    first = invoke(runtime, planner, selector)
    second = invoke(runtime, planner, selector)
    assert first.action == second.action and len(selector.contexts) == 1
    assert len(ledger.campaign_reservations(first.evidence["campaign_id"])) == 1


def test_interrupted_reservation_without_intent_resumes_only_exact_request(tmp_path, monkeypatch):
    runtime, planner, state, selector, _, ledger = shared_runtime(tmp_path, decision())
    reserve = ledger.reserve_model_call

    def lose_reservation_acknowledgment(*args, **kwargs):
        reserve(*args, **kwargs)
        raise KeyboardInterrupt("interrupted after reservation commit")

    with monkeypatch.context() as patch:
        patch.setattr(ledger, "reserve_model_call", lose_reservation_acknowledgment)
        with pytest.raises(KeyboardInterrupt):
            invoke(runtime, planner, selector)
    campaign_id = state["model_campaign"]["campaign_id"]
    retained, = ledger.campaign_reservations(campaign_id)
    assert retained["status"] == "reserved"
    with ledger._operation_lock(("campaign-role", campaign_id, "t20-controller", 0)):
        with pytest.raises(OperationBlocked, match="active"):
            invoke(runtime, planner, selector)
    with pytest.raises(ValueError, match="exact.*request"):
        invoke(runtime, planner, selector, context={"decision_kind": "adaptive_controller", "changed": True})
    assert not selector.contexts
    result = invoke(runtime, planner, selector)
    assert result.evidence["model_reservation_ref"] == retained["ref"]
    assert len(selector.contexts) == 1
    assert len(ledger.campaign_reservations(campaign_id)) == 1
    assert ledger.get_campaign(campaign_id)["verified_spent_tokens"] == 12


def test_retained_capture_recovers_without_coordinator_selection_copy(tmp_path, monkeypatch):
    from devbench.analysis import episode_agent

    runtime, planner, _, selector, backend, ledger = shared_runtime(tmp_path, decision())
    write = episode_agent.atomic_json

    def lose_selection_copy(path, *args, **kwargs):
        if path.name == "campaign-selection.json":
            raise KeyboardInterrupt("interrupted before selection copy")
        return write(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(episode_agent, "atomic_json", lose_selection_copy)
        with pytest.raises(KeyboardInterrupt):
            invoke(runtime, planner, selector)
    retained, = ledger.campaign_reservations("parent-campaign")
    assert retained["status"] == "completed" and retained["capture"] is not None
    monkeypatch.setattr(backend, "verify_selection_capture", lambda *args, **kwargs: pytest.fail("Capture already retained"))
    result = invoke(runtime, planner, selector)
    assert result.action == decision() and result.evidence["model_capture_ref"] == retained["capture"]["capture_ref"]
    assert len(selector.contexts) == 1


def test_missing_capture_retains_reservation_and_does_not_redispatch(tmp_path, monkeypatch):
    runtime, planner, state, selector, backend, ledger = shared_runtime(tmp_path, decision())
    monkeypatch.setattr(backend, "verify_selection_capture", lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("missing capture")))
    with pytest.raises(ValueError, match="missing capture"):
        invoke(runtime, planner, selector)
    with pytest.raises(OperationBlocked, match="reconciliation"):
        invoke(runtime, planner, selector)
    assert len(selector.contexts) == 1
    assert ledger.get_campaign(state["model_campaign"]["campaign_id"])["unresolved_count"] == 1


def test_prelaunch_campaign_deadline_does_not_poison_other_role(tmp_path, monkeypatch):
    runtime, planner, state, selector, _, ledger = shared_runtime(tmp_path, decision(), decision())
    campaign_id = state["model_campaign"]["campaign_id"]
    with monkeypatch.context() as patch:
        patch.setattr(ledger, "model_call_remaining", lambda _: 0)
        with pytest.raises(OperationBlocked, match="campaign_deadline"):
            invoke(runtime, planner, selector)
    assert not selector.contexts
    discarded, = ledger.campaign_reservations(campaign_id)
    assert discarded["status"] == "cancelled" and discarded["capture"] is None
    assert ledger.get_campaign(campaign_id)["unresolved_count"] == 0

    invoke(runtime, planner, selector, role="challenger", context={"decision_kind": "scientific_review"})
    recovered = invoke(runtime, planner, selector)
    assert invoke(runtime, planner, selector) == recovered
    assert len(selector.contexts) == 2
    assert ledger.get_campaign(campaign_id)["verified_spent_tokens"] == 24


@pytest.mark.wal
def test_controller_journal_cannot_reset_limits_or_pending_decision(tmp_path):
    from devbench.engine.workflow_operations import (
        OperationConflict,
        WorkflowOperationLedger,
    )
    from devbench.platform.adaptive_workflow import AdaptiveWorkflow
    from tests.test_investigation_invalidation import make

    flow = make(tmp_path)
    assert hasattr(flow, "bind_controller"), "Revisioned controller state is missing"
    binding = {"campaign_id": "campaign", "catalog_sha256": "a" * 64, "max_decisions": 24}
    flow.bind_controller(binding)
    context = {"decision_kind": "adaptive_controller", "revision": flow.status()["revision"]}
    flow.begin_controller_decision(context, expected_revision=flow.status()["revision"])
    reopened = AdaptiveWorkflow(WorkflowOperationLedger(flow.ledger.path), "study")
    assert reopened.controller_state()["pending"]["context"] == context
    with pytest.raises(OperationConflict, match="reset"):
        reopened.bind_controller({**binding, "max_decisions": 23})
    with pytest.raises(OperationBlocked, match="pending"):
        reopened.begin_controller_decision(context, expected_revision=flow.status()["revision"])


@pytest.mark.wal
def test_controller_journal_retains_bounded_events_after_history_exceeds_one_record(tmp_path):
    from devbench.engine.workflow_operations import WorkflowOperationLedger
    from devbench.platform.adaptive_workflow import AdaptiveWorkflow
    from tests.test_investigation_invalidation import make
    flow = make(tmp_path)
    flow.bind_controller({"campaign_id": "journal-only", "catalog_sha256": "a" * 64, "max_decisions": 24})
    for sequence in range(7):
        context = {"decision_kind": "adaptive_controller", "revision": flow.status()["revision"],
                   "bounded_context": "x" * 12000}
        pending = flow.begin_controller_decision(context, expected_revision=flow.status()["revision"])
        flow.select_controller_decision(sequence, decision(), {"protocol_scope": "journal_unit_test"})
        flow.complete_controller_decision(sequence, {"status": "retained", "bounded_evidence": "y" * 12000})
        assert pending["sequence"] == sequence
    reopened = AdaptiveWorkflow(WorkflowOperationLedger(flow.ledger.path), "study")
    assert len(reopened.controller_state()["decisions"]) == 7
    with flow.ledger._database() as db:
        maximum = db.execute("SELECT max(length(payload)) FROM scientific_records WHERE kind='adaptive_event'").fetchone()[0]
    assert maximum < 65536


@pytest.mark.wal
def test_active_review_rejects_unmetered_campaign_bypass(
    workspace_tmp_path, monkeypatch
):
    from devbench.analysis import scientific_review_agent as agent
    from tests.test_active_challenger_queries import ActiveSelector, run_review
    from tests.test_rna_investigation_budget import _capability, _policy
    original = agent.request_active_review
    def bound_campaign(*args, **kwargs):
        kwargs["lifecycle"].ledger.open_campaign("bound", session_id="study", policy=_policy(),
            capability=_capability(), identity={"scope": "offline_protocol"})
        return original(*args, **kwargs)
    monkeypatch.setattr(agent, "request_active_review", bound_campaign)
    with pytest.raises(PermissionError, match="bypass"):
        run_review(workspace_tmp_path, ActiveSelector())


def test_adaptive_entrypoint_accepts_typed_controller_without_replacing_dispatch_compatibility():
    import inspect

    from devbench.analysis import episode_agent
    from devbench.platform.workflow_entrypoints import advance_adaptive_workflow

    assert hasattr(episode_agent, "AdaptiveController"), "Constructive controller orchestration is missing"
    assert "controller" in inspect.signature(advance_adaptive_workflow).parameters


from tests.test_rna_investigation_parent_integration import (
    CapturedFixtureBackend,
    CapturedFixtureSelector,
)


class ScenarioSelector(CapturedFixtureSelector):
    """Offline decisions only. Computation is dispatched by the production graph."""
    def __init__(self, script):
        super().__init__()
        self.script = list(script)
        self.controller_turns = 0

    def select(self, context, output, **kwargs):
        if context["decision_kind"] == "scientific_review":
            if getattr(self, "review_handler", None) is not None:
                action = self.review_handler(context)
            elif not context["query_results"]:
                action = {"kind": "query_evidence", "handle_id": "numeric",
                          "query": {"target_id": "descriptive", "mode": "family_summary"}}
            else:
                action = {"kind": "scientific_review", "outcome": "completed", "rationale": "Protocol-only review of actual numerical output",
                    "covered_obligation_ids": [item["obligation_id"] for item in context["obligations"]], "objections": []}
        else:
            if self.controller_turns >= len(self.script):
                from devbench.analysis.episode_agent import InvalidSelection
                self.answers = [InvalidSelection("Offline scripted selector has no further decision")]
                return super().select(context, output, **kwargs)
            item = self.script[self.controller_turns]
            self.controller_turns += 1
            action = item(context) if callable(item) else deepcopy(item)
        self.answers = [action]
        return super().select(context, output, **kwargs)


def constructive_setup(tmp_path, script, *, catalog=None, max_decisions=24, policy_updates=None):
    from devbench.analysis.episode_agent import (
        AdaptiveController,
        CampaignSelectionInvoker,
        ReviewAgentLimits,
    )
    from devbench.analysis.evidence_access import AccessPolicy, EvidenceBroker
    from devbench.analysis.scientific_review_holds import (
        ReviewEntity,
        ReviewHoldLifecycle,
    )
    from devbench.engine.methods import MethodService
    from devbench.engine.workflow_operations import LEDGER_NAME, WorkflowOperationLedger
    from devbench.platform.method_planner import ScientificMethodPlanner
    from tests.test_investigation_publication_epochs import authorize
    from tests.test_rna_investigation_budget import _policy
    from tests.test_scientific_authority_migration import _binding

    methods = MethodService(tmp_path / "workspace")
    planner = ScientificMethodPlanner(methods)
    state = planner.create({"scientific_objective": "Describe synthetic observations and neighborhood sensitivity",
        "computational_task": "exploratory_graph"}, {"observed": {"biological_unit": "synthetic_donor"},
        "inferred": {}, "unknowns": [], "artifact_ids": []}, actor="tester", data_classification="synthetic")
    session = state["session_id"]
    ledger = WorkflowOperationLedger(methods.root / LEDGER_NAME, scientific=True)
    ledger.scientific.commit(session, expected_revision=0, record=_binding())
    ledger.scientific.commit(session, expected_revision=1, record={"kind": "grant", "record_id": "grant-a",
        "binding_id": "binding-a", "grant_sha256": digest("engineering numeric grant")})
    ledger.scientific.activate_execution(session, expected_revision=2, binding_id="binding-a", grant_id="grant-a",
        max_jobs=64, authorize=authorize)
    selector = ScenarioSelector(script)
    backend = CapturedFixtureBackend(methods, selector)
    allocation = {"tokens": 20, "calls": 2, "seconds": 10, "context_bytes": 65536, "memory_mb": 512}
    protected = {"scientific_review": {**allocation, "tokens": 320, "calls": 32, "seconds": 160},
                 "adaptive_termination": allocation}
    policy = _policy(max_total_tokens=2000, max_calls=120, max_total_seconds=900,
        reconciliation={"tokens": 340, "calls": 34, "seconds": 170, "context_bytes": 65536})
    policy.update(policy_updates or {})
    ledger.open_campaign("constructive", session_id=session, policy=policy, capability=backend.capabilities(),
        identity={"expected_plan_hash": digest(state), "source_digest": backend.capability["source_digest"],
            "runtime_digest": backend.capability["runtime_digest"], "parent_call_limits": {"default": allocation},
            "protected_parent_allocations": protected})
    planner.attach_model_campaign(session, ledger, "constructive", backend,
        expected_plan_hash=digest(state), actor="tester")
    flow = planner.adaptive_workflow(session, ledger)
    broker = EvidenceBroker(ledger.scientific)
    access = AccessPolicy("controller", 1, allow_outcomes=True, allow_statistics=True,
                          diagnostic_policy_id=digest("synthetic diagnostic access"))
    broker.register_policy(access)
    broker.register_context("controller", principal_id="controller", lineage_id=session,
        role="controller", phase="analysis", policy_sha256=access.sha256())
    lifecycle = ReviewHoldLifecycle(ledger.scientific, session, adaptive_workflow=flow)
    lifecycle.register_entities((ReviewEntity(entity_id="claim", kind="claim"),))
    invoker = CampaignSelectionInvoker(planner, session,
        limits=AgentLimits(max_selections=24, memory_mb=512, max_context_bytes=65536))
    controller = AdaptiveController(invoker, broker=broker, context_id="controller", action_catalog=catalog or {},
        selector=selector, challenger=selector, max_decisions=max_decisions,
        review_limits=ReviewAgentLimits(max_selections=4, max_queries=4, max_tokens_per_invocation=20,
                                       memory_mb=512, max_context_bytes=65536))
    return planner, flow, controller, selector, lifecycle, broker, access


@pytest.mark.wal
def test_controller_context_is_accepted_by_native_public_boundary(tmp_path):
    from devbench.analysis.episodes import _public_only
    from devbench.platform.workflow_actions import WorkflowActions
    from tests.test_investigation_invalidation import step
    planner, flow, controller, *_ = constructive_setup(tmp_path, [], catalog={"numeric": step("numeric")})
    flow.insert(step("existing"))
    controller.bind(flow)
    actions = WorkflowActions(planner, flow.ledger, actor="tester", selector=None, controller=controller)
    _public_only(actions.adaptive_controller_context(flow))


@pytest.mark.wal
def test_controller_reuses_attempted_context_disclosure_after_restart(tmp_path, monkeypatch):
    from devbench.analysis.episode_agent import AdaptiveController
    from devbench.platform.workflow_actions import WorkflowActions, create_rna_workflow

    planner, flow, first, selector, _, broker, _ = constructive_setup(tmp_path, [decision()])
    actions = WorkflowActions(planner, flow.ledger, actor="tester", selector=None, controller=first)
    graph = create_rna_workflow(mode="adaptive/1", actions=actions, adaptive=flow)
    with monkeypatch.context() as patch:
        def lost_controller_commit(*args, **kwargs):
            raise KeyboardInterrupt("interrupted after model settlement")

        patch.setattr(flow, "select_controller_decision", lost_controller_commit)
        with pytest.raises(KeyboardInterrupt):
            graph.advance()

    second = AdaptiveController(first.invoker, broker=broker, context_id="controller", action_catalog={},
        selector=selector, challenger=selector, review_limits=first.review_limits)
    actions = WorkflowActions(planner, flow.ledger, actor="tester", selector=None, controller=second)
    outcome = create_rna_workflow(mode="adaptive/1", actions=actions, adaptive=flow).advance()
    assert outcome["controller_outcome"]["reason"] == "empty_ready_frontier"
    assert len(selector.contexts) == 1
    selection = flow.controller_state()["decisions"][0]["evidence"]
    exposure = broker.exposure("controller", selection["controller_disclosure_source_id"])
    assert exposure["reservation_ids"] == [selection["controller_disclosure_reservation_id"]]


@pytest.mark.wal
def test_scientific_cancellation_blocks_role_activation_while_campaign_is_open(tmp_path, monkeypatch):
    planner, flow, controller, selector, *_ = constructive_setup(tmp_path, [decision()])
    ledger = flow.ledger
    activate = ledger.activate_model_call

    def cancel_after_last_budget_read(reservation):
        flow.authority.cancel_execution(flow.lineage_id, actor="operator")
        assert ledger.get_campaign("constructive")["status"] == "open"
        return activate(reservation)

    monkeypatch.setattr(ledger, "activate_model_call", cancel_after_last_budget_read)
    with pytest.raises(OperationBlocked, match="scientific_cancelled"):
        invoke(controller.invoker, planner, selector)
    retained, = ledger.campaign_reservations("constructive")
    assert retained["status"] == "cancelled" and retained["capture"] is None
    assert not selector.contexts


@pytest.mark.wal
@pytest.mark.parametrize("duplicate", [False, True])
def test_controller_recovers_from_one_invalid_selection_and_stops_on_duplicate(tmp_path, duplicate):
    from devbench.platform.workflow_actions import WorkflowActions, create_rna_workflow

    invalid = {**decision(), "evidence_ids": ["unentitled-evidence"]}
    second = {**invalid, "rationale": "Same unauthorized action, different phrasing"} if duplicate else decision()
    planner, flow, controller, selector, *_ = constructive_setup(tmp_path, [invalid, second])
    actions = WorkflowActions(planner, flow.ledger, actor="tester", selector=None, controller=controller)
    outcome = create_rna_workflow(mode="adaptive/1", actions=actions, adaptive=flow).advance()["controller_outcome"]

    assert len(selector.contexts) == 2
    feedback = selector.contexts[1]["selection_feedback"]
    assert feedback["reason"] == "controller_cited_unentitled_evidence"
    assert "unentitled-evidence" not in json.dumps(feedback)
    assert flow.ledger.get_campaign("constructive")["verified_spent_tokens"] == 24
    if duplicate:
        assert outcome["reason"] == "controller_repeated_invalid_selection"
        assert len(flow.controller_state()["pending"]["rejected_selections"]) == 2
    else:
        assert outcome["reason"] == "empty_ready_frontier"
        assert len(flow.controller_state()["decisions"][0]["rejected_selections"]) == 1


@pytest.mark.wal
def test_controller_diagnostic_route_and_completion_share_one_commit(tmp_path, monkeypatch):
    from devbench.analysis.rna_diagnostic_policy import (
        registered_rna_diagnostic_policies,
    )
    from devbench.analysis.scientific_review_holds import (
        ReviewObligation,
        obligations_digest,
    )
    from devbench.platform.adaptive_workflow import AdaptiveWorkflow
    from devbench.platform.workflow_actions import WorkflowActions, create_rna_workflow
    from tests.test_review_hold_lifecycle import binding
    from tests.test_rna_diagnostic_policy_v24 import context, graph, state

    choose = {**decision("route_diagnostic", action_id="check-graph"), "evidence_ids": ["graph-evidence"]}
    planner, flow, controller, selector, lifecycle, broker, _ = constructive_setup(tmp_path, [choose])
    source = digest(graph())
    lifecycle.register_evidence("graph-evidence", source, relevant_entity_ids=("claim",))
    broker.register_source("graph-source", content_sha256=source, roots=(source,), classification="diagnostic")
    obligation = ReviewObligation(obligation_id="graph-review", entity_id="claim", stage="claim",
                                  evidence_ids=("graph-evidence",))
    current = binding(obligations_sha256=obligations_digest((obligation,)))
    lifecycle.register_obligations((obligation,), binding=current)
    policy = registered_rna_diagnostic_policies()["Q-GRAPH"]
    allowed = {"check-graph": {"policy": policy, "inputs": graph(), "context": context(),
        "state": state(affected_use="claim", resources_available=False, obligations=[
            {"obligation_id": "graph-review", "threat": "graph_isolated_nodes"}]),
        "input_evidence_id": "graph-evidence"}}
    actions = WorkflowActions(planner, flow.ledger, actor="tester", selector=None, controller=controller,
        review_lifecycle=lifecycle, review_binding=lambda _: current, diagnostic_actions=allowed)
    workflow = create_rna_workflow(mode="adaptive/1", actions=actions, adaptive=flow)
    complete = AdaptiveWorkflow.complete_controller_decision

    def fail_before_journal(self, sequence, result, **options):
        if result and result.get("kind") == "route_diagnostic":
            assert options["connection"].in_transaction
            raise RuntimeError("synthetic journal failure")
        return complete(self, sequence, result, **options)

    with monkeypatch.context() as patch:
        patch.setattr(AdaptiveWorkflow, "complete_controller_decision", fail_before_journal)
        status = workflow.advance()
        assert status["decisions"][0]["status"] == "reconciliation_required"
        assert not flow.controller_state()["decisions"]
        assert not any(row["action"] == "diagnostic_route" for row in lifecycle.state()["history"])
    status = workflow.advance()
    retained = [row for row in lifecycle.state()["history"] if row["action"] == "diagnostic_route"]
    record = status["decisions"][0]
    assert record.get("route") == "unavailable", record
    assert record["kind"] == "route_diagnostic"
    assert len(retained) == len(selector.contexts) == 1
    assert record["diagnostic_route_sha256"] == digest(retained[0]["detail"]["route"])


@pytest.mark.wal
def test_adaptive_graph_checks_ui_revision_before_selecting(tmp_path):
    from devbench.engine.workflow_operations import OperationConflict
    from devbench.platform.workflow_actions import WorkflowActions, create_rna_workflow

    planner, flow, controller, selector, *_ = constructive_setup(tmp_path, [decision()])
    actions = WorkflowActions(planner, flow.ledger, actor="tester", selector=None, controller=controller)
    workflow = create_rna_workflow(mode="adaptive/1", actions=actions, adaptive=flow)
    expected = flow.status()["revision"]
    flow.record_basis("corrected-design", "fact", digest("synthetic revision"))
    with pytest.raises(OperationConflict, match="revision changed"):
        workflow.advance(expected_revision=expected)
    assert not selector.contexts and flow.controller_state()["pending"] is None
    assert workflow.advance(expected_revision=flow.status()["revision"])["controller_outcome"]["reason"] == "empty_ready_frontier"


@pytest.mark.wal
@pytest.mark.parametrize("historical_state", ["pending", "completed"])
def test_historical_pending_context_without_source_ancestry_cannot_dispatch(tmp_path, historical_state):
    from devbench.platform.workflow_actions import WorkflowActions
    planner, flow, controller, selector, *_ = constructive_setup(tmp_path, [decision()])
    controller.bind(flow)
    actions = WorkflowActions(planner, flow.ledger, actor="tester", selector=None, controller=controller)
    context = actions.adaptive_controller_context(flow)
    context.pop("disclosure_ancestry")
    flow.begin_controller_decision(context, expected_revision=context["revision"])
    if historical_state == "completed":
        flow.select_controller_decision(0, decision(), {"protocol_scope": "historical_protocol_fixture"})
        flow.complete_controller_decision(0, {"status": "retained"})
    with pytest.raises(OperationBlocked, match="current_controller_disclosure_binding_required"):
        actions.select_adaptive(flow)
    assert not selector.contexts and not flow.ledger.campaign_reservations("constructive")


@pytest.mark.wal
def test_reserved_review_and_termination_survive_controller_exhaustion(tmp_path):
    planner, flow, controller, selector, *_ = constructive_setup(tmp_path, [decision(), decision(), decision()],
        policy_updates={"max_calls": 36})
    runtime = controller.invoker
    invoke(runtime, planner, selector)
    with pytest.raises(OperationBlocked, match="budget_exhausted"):
        invoke(runtime, planner, selector, turn=1)
    review = runtime.invoke(selector, {"decision_kind": "adaptive_controller"}, planner.methods.root / "reserved-review",
        role="challenger", purpose="scientific_review", node="reserved-review", attempt=0)
    termination = runtime.invoke(selector, {"decision_kind": "adaptive_controller"}, planner.methods.root / "reserved-stop",
        role="controller", purpose="adaptive_termination", node="reserved-stop", attempt=0)
    assert review.evidence["campaign_id"] == termination.evidence["campaign_id"] == "constructive"
    assert flow.ledger.get_campaign("constructive")["verified_calls"] == 6


def test_cancellation_during_role_call_charges_capture_without_acceptance(tmp_path, monkeypatch):
    runtime, planner, state, selector, backend, ledger = shared_runtime(tmp_path, decision())
    original = selector.select
    def cancel_after_capture(*args, **kwargs):
        value = original(*args, **kwargs)
        ledger.cancel_campaign(state["model_campaign"]["campaign_id"])
        return value
    monkeypatch.setattr(selector, "select", cancel_after_capture)
    with pytest.raises(OperationBlocked, match="cancelled"):
        invoke(runtime, planner, selector)
    assert ledger.get_campaign(state["model_campaign"]["campaign_id"])["verified_spent_tokens"] == 12
    assert len(selector.contexts) == 1


@pytest.mark.wal
def test_controller_cannot_apply_selection_after_graph_changes(tmp_path):
    from devbench.platform.workflow_actions import WorkflowActions
    from tests.test_investigation_invalidation import step
    planner, flow, controller, *_ = constructive_setup(tmp_path, [decision("insert", action_id="new")],
                                                       catalog={"new": step("new")})
    controller.bind(flow)
    actions = WorkflowActions(planner, flow.ledger, actor="tester", selector=None, controller=controller)
    pending = actions.select_adaptive(flow)
    flow.insert(step("concurrent"))
    result = actions.apply_adaptive_decision(flow, pending)
    assert result["status"] == "rejected" and result["reason"] == "controller_evidence_changed"
    assert "new" not in flow.ready()


@pytest.mark.wal
@pytest.mark.parametrize("row_count", [3, 50])
def test_controller_queries_large_accepted_result_and_continues_issued_cursor(
    tmp_path, monkeypatch, row_count
):
    from hashlib import sha256
    from io import BytesIO
    from types import SimpleNamespace

    from devbench.platform.adaptive_workflow import request_digest
    from devbench.platform.workflow_actions import WorkflowActions
    from tests.test_investigation_invalidation import Worker, step

    def choose(context):
        assert "gene-000" not in json.dumps(context)
        scientific = context["scientific_state"]
        assert scientific["individual_gene_rows_in_prompt"] == 0
        assert scientific["artifact_refs"]["records"][0]["sha256"] == identity
        handle = context["query_handles"][0]
        assert handle["content_status"] == "available"
        query = {"mode": "filtered", "target_id": "family", "limit": 1}
        if context["query_results"]:
            page = context["query_results"][0]["page"]
            assert "rows" not in page and page["omitted_gene_rows"] == 1
            assert page["source_sha256"] == digest(json.loads(payload))
            query["cursor"] = page["query_receipt"]["next_cursor"]
        return decision("query_evidence", handle_id=handle["handle_id"], query=query)

    planner, flow, controller, _, _, broker, _ = constructive_setup(tmp_path, [choose, choose])
    rows = [
        {
            "gene_id": f"gene-{index:03d}", "hypothesis_id": f"gene-{index:03d}",
            "estimate_status": "estimated", "values": [1.0], "p_value": 0.2,
        }
        for index in range(row_count)
    ]
    payload = json.dumps({"targets": [{"target_id": "family", "payload": {"gene_statistics": rows}}]}).encode()
    identity = sha256(payload).hexdigest()
    assert (len(payload) > 4096) == (row_count == 50)

    class LargeResultWorker(Worker):
        def run(self, envelope):
            receipt = super().run(envelope)
            return receipt.model_copy(update={"outputs": {"out": receipt.outputs["out"].model_copy(update={"sha256": identity})}})

    worker = LargeResultWorker()
    worker.training = SimpleNamespace(open=lambda identity_arg: BytesIO({identity: payload}[identity_arg]))
    flow.insert(step("large-result"))
    assert flow.dispatch("large-result", worker, actor="tester")["status"] == "succeeded"
    controller.bind(flow)
    actions = WorkflowActions(planner, flow.ledger, actor="tester", selector=None, executor=worker, controller=controller)
    pending = actions.select_adaptive(flow)
    complete = flow.complete_controller_decision

    def lose_journal(*args, **kwargs):
        raise RuntimeError("synthetic controller journal interruption")

    with monkeypatch.context() as patch:
        patch.setattr(flow, "complete_controller_decision", lose_journal)
        with pytest.raises(RuntimeError, match="journal interruption"):
            actions.apply_adaptive_decision(flow, pending)
    reservation_id = request_digest(["controller-query", flow.lineage_id, 0, pending["context_sha256"]])[:32]
    retained = broker.reservation(reservation_id)
    assert retained["state"] == "delivery_attempted" and flow.complete_controller_decision == complete
    first = actions.apply_adaptive_decision(flow, pending)["page"]
    assert first["query_receipt"]["disclosure_reservation_id"] == reservation_id
    assert broker.reservation(reservation_id) == retained
    second = actions.apply_adaptive_decision(flow, actions.select_adaptive(flow))["page"]
    assert [item["rows"][0]["gene_id"] for item in (first, second)] == ["gene-000", "gene-001"]
    assert all(broker.verify_query_page(item) for item in (first, second))
    assert second["query_receipt"]["offset"] == 1


@pytest.mark.wal
def test_controller_rebuilds_retained_scientific_history_from_one_snapshot(
    tmp_path, monkeypatch
):
    from devbench.engine.workflow_operations import WorkflowOperationLedger
    from devbench.platform.adaptive_workflow import AdaptiveWorkflow
    from devbench.platform.workflow_actions import WorkflowActions
    from tests.test_investigation_invalidation import Worker, step
    from tests.test_review_hold_lifecycle import objection, open_cycle

    planner, flow, controller, _, review, broker, _ = constructive_setup(
        tmp_path, [decision("dispatch", step_id="absent")],
    )
    flow.insert(step("fit"))
    failed = flow.dispatch("fit", Worker(fail=("fit",)), actor="tester")
    flow.hold("fit", "Q-DESIGN", evidence_sha256=digest("rank failure"))
    flow.hold("fit", None, evidence_sha256=digest("rank checked"))
    flow.hold("fit", "Q-INFLUENCE", evidence_sha256=digest("influence check"))
    review.register_evidence(
        "source", digest("synthetic review basis"), relevant_entity_ids=("claim",)
    )
    open_cycle(review, entity_ids=("claim",))
    held = review.admit_objection("design-1", objection(entity="claim"))["hold_id"]
    broker.register_source(
        "source", content_sha256=digest({"declared_unit": "donor"}),
        roots=(digest("synthetic input"),), classification="structural",
    )
    disclosure = broker.reserve("controller", "source", {"declared_unit": "donor"})
    broker.mark_attempted(disclosure["reservation_id"])
    controller.bind(flow)
    actions = WorkflowActions(
        planner, flow.ledger, actor="tester", selector=None, controller=controller
    )
    rejected = actions.apply_adaptive_decision(flow, actions.select_adaptive(flow))
    assert rejected["status"] == "rejected"

    reopened = AdaptiveWorkflow(flow.ledger, flow.lineage_id)
    concurrent = AdaptiveWorkflow(
        WorkflowOperationLedger(flow.ledger.path), flow.lineage_id
    )
    project = reopened.projection_snapshot
    before = reopened.status()["revision"]

    def interleaved_projection(*, connection=None):
        assert connection is not None and connection.in_transaction
        concurrent.hold("fit", None, evidence_sha256=digest("later reconsidered"))
        return project(connection=connection)

    with monkeypatch.context() as patch:
        patch.setattr(reopened, "projection_snapshot", interleaved_projection)
        context = actions.adaptive_controller_context(reopened)

    assert context["revision"] == before < reopened.status()["revision"]
    scientific = context["scientific_state"]
    failures = scientific["failed_or_rejected_attempts"]["records"]
    assert any(
        row.get("receipt_sha256") == failed["receipt_sha256"]
        and row["status"] == "failed" and row["reason"] == "synthetic_failure"
        for row in failures
    )
    assert any(row["status"] == "rejected" for row in failures)
    assert any(row["hold_id"] == held for row in scientific["review_holds"]["records"])
    q_holds = scientific["qualification_holds"]["records"]
    assert {(row["reason"], row["status"]) for row in q_holds} == {
        ("Q-DESIGN", "open"), ("Q-DESIGN", "released"), ("Q-INFLUENCE", "open"),
    }
    assert context["blocked"]["fit"] == "Q-INFLUENCE"
    assert any(row.get("reservation_id") == disclosure["reservation_id"]
               and row["content_sha256"] == disclosure["content_sha256"]
               for row in scientific["exposure_history"]["records"])
    assert "declared_unit" not in json.dumps(scientific)


@pytest.mark.wal
def test_controller_exposure_compaction_preserves_full_inventory_identity(tmp_path):
    from devbench.platform.workflow_actions import WorkflowActions

    planner, flow, controller, _, _, broker, access = constructive_setup(tmp_path, [])
    source = "synthetic-input"
    root = digest("synthetic experiment")
    broker.register_source(
        source, content_sha256=root, roots=(root,), classification="structural"
    )
    for index in range(10):
        record = broker.reserve("controller", source, {"sample_count": index})
        broker.mark_attempted(record["reservation_id"])
    broker.register_context(
        "blinded", principal_id="challenger", lineage_id=flow.lineage_id,
        role="challenger", phase="design_initial", policy_sha256=access.sha256(),
    )
    secret = {"observed": "separately scoped synthetic reviewer observation"}
    withheld = broker.reserve("blinded", source, secret)
    broker.mark_attempted(withheld["reservation_id"])

    actions = WorkflowActions(
        planner, flow.ledger, actor="tester", selector=None, controller=controller
    )
    scientific = actions.adaptive_controller_context(flow)["scientific_state"]
    exposure = scientific["exposure_history"]
    assert exposure["total"] == 11 and exposure["event_count"] == 22
    assert exposure["omitted"] == exposure["total"] - len(exposure["records"]) > 0
    assert exposure["records"][-1]["content_status"] == "withheld_by_access_policy"
    assert withheld["reservation_id"] not in json.dumps(exposure)
    assert withheld["content_sha256"] not in json.dumps(exposure)
    broker.confirm_receipt(
        record["reservation_id"], receipt_sha256=digest("synthetic receipt"),
        confirmed_bytes=record["content_bytes"], verify_receipt=lambda _: True,
    )
    changed_state = actions.adaptive_controller_context(flow)["scientific_state"]
    changed = changed_state["exposure_history"]
    assert changed["total"] == exposure["total"] and changed["event_count"] == 23
    assert changed["sha256"] != exposure["sha256"]
    assert changed["events_sha256"] != exposure["events_sha256"]


@pytest.mark.wal
def test_controller_rechecks_historical_exposure_custody_before_delivery(tmp_path):
    from devbench.analysis.evidence_access import AccessDenied
    from devbench.engine.partition_authority import PartitionAuthority
    from devbench.platform.workflow_actions import WorkflowActions

    planner, flow, controller, selector, _, broker, _ = constructive_setup(
        tmp_path, [decision()]
    )
    body = {"sample_count": 5}
    identity = digest(body)
    broker.register_source("former-input", content_sha256=identity,
        roots=(digest("synthetic experiment"),), classification="structural")
    broker.delivery("controller", "former-input").release(body)
    controller.bind(flow)
    actions = WorkflowActions(
        planner, flow.ledger, actor="tester", selector=None, controller=controller
    )
    context = actions.adaptive_controller_context(flow)
    assert identity in json.dumps(context["scientific_state"]["exposure_history"])

    partition = PartitionAuthority(flow.ledger, digest("synthetic partition"))
    partition.protect_outputs([identity])
    with pytest.raises(AccessDenied, match="Protected evaluation custody"):
        controller.select(flow, context)
    assert not selector.contexts


@pytest.mark.wal
def test_pipeline_input_references_survive_controller_delivery(tmp_path):
    from devbench.core.publication_families import FamilySpec
    from devbench.engine.provenance import canonical_json
    from devbench.engine.tier_a_executor import TierAExecutor
    from devbench.platform.workflow_actions import WorkflowActions
    from tests.test_adaptive_pipeline_compiler import _proposal
    from tests.test_investigation_invalidation import artifact

    def choose(context):
        offered = {
            item["version_id"]: item["sha256"] for item in context["pipeline_inputs"]
        }
        assert offered == identities
        assert "fixture-feature-one" not in json.dumps(context)
        pointers = context["scientific_state"]["artifact_refs"]["records"]
        assert {item["version_id"]: item["sha256"] for item in pointers} == offered
        return {**_proposal(), "evidence_ids": sorted(offered)}

    planner, flow, controller, selector, _, broker, _ = constructive_setup(
        tmp_path, [choose]
    )
    tier_a = TierAExecutor(flow.ledger, allow_data_execution=True)
    family = FamilySpec(
        family_id="synthetic-family", population="bulk", numerator="case",
        denominator="control", feature_ids=("fixture-feature-one",),
    )
    identities = {}
    for name, kind, representation, raw in (
        ("expression-v1", "counts", "COUNT-1", b"fixture expression"),
        ("metadata-v1", "table", "metadata_csv", b"fixture metadata"),
        (
            "family-v1", "table", "family_spec_json",
            canonical_json(family.model_dump(mode="json")),
        ),
    ):
        identities[name] = tier_a.training.put(raw)
        reference = artifact(
            name, kind=kind, representation=representation,
            permitted_uses=("admitted_research_inference",),
        ).model_copy(update={"sha256": identities[name]})
        flow.register_artifact(reference)
        broker.register_source(
            name, content_sha256=identities[name],
            roots=(digest("synthetic input root"),), classification="structural",
        )
    controller.bind(flow)
    actions = WorkflowActions(planner, flow.ledger, actor="tester", selector=None,
        controller=controller, tier_a_executor=tier_a)
    pending = actions.select_adaptive(flow)
    result = actions.apply_adaptive_decision(flow, pending)
    assert len(selector.contexts) == 1
    assert result == {
        "status": "applied", "kind": "propose_pipeline_step",
        "step_id": "case-control-fit",
    }


@pytest.mark.wal
def test_legacy_pending_context_is_retained_without_a_new_model_delivery(tmp_path):
    from devbench.platform.workflow_actions import WorkflowActions

    planner, flow, controller, selector, *_ = constructive_setup(tmp_path, [decision()])
    controller.bind(flow)
    actions = WorkflowActions(
        planner, flow.ledger, actor="tester", selector=None, controller=controller
    )
    historical = actions.adaptive_controller_context(flow)
    historical.pop("scientific_state")
    historical["query_results"] = [
        {"page": {"rows": [{"gene_id": "synthetic-gene", "value": 3}]}}
    ]
    pending = flow.begin_controller_decision(
        historical, expected_revision=historical["revision"]
    )
    with pytest.raises(
        OperationBlocked, match="current_compact_controller_context_required"
    ):
        actions.select_adaptive(flow)
    assert flow.controller_state()["pending"] == pending
    assert not selector.contexts


def test_controller_context_page_cursor_restores_only_same_collection_inventory():
    from devbench.platform.workflow_actions import WorkflowActions

    rows = [{"action_id": f"check-{index}"} for index in range(11)]
    state = {"decisions": []}
    page, first = WorkflowActions._controller_page(state, "diagnostic_actions", rows, page_size=4)
    assert page["total"] == 11 and first == rows[:4]
    offset, cumulative = page["next_cursor"].split(":")
    state["decisions"].append({"decision": {"kind": "page_context"}, "result": {"status": "paged",
        "section": "diagnostic_actions", "offset": int(offset), "digest": cumulative}})
    next_page, second = WorkflowActions._controller_page(state, "diagnostic_actions", rows, page_size=4)
    assert next_page["previous_cursor"] == f"0:{cumulative}" and second == rows[4:8]
    changed, visible = WorkflowActions._controller_page(state, "diagnostic_actions", rows[:-1], page_size=4)
    assert changed["digest"] != cumulative and changed["offset"] == 0 and visible == first


@pytest.mark.wal
def test_partial_stop_cannot_abandon_ready_computation(tmp_path):
    from devbench.platform.workflow_actions import WorkflowActions, create_rna_workflow
    from tests.test_investigation_invalidation import step
    planner, flow, controller, *_ = constructive_setup(tmp_path, [decision()])
    flow.insert(step("available"))
    actions = WorkflowActions(planner, flow.ledger, actor="tester", selector=None, controller=controller)
    result = create_rna_workflow(mode="adaptive/1", actions=actions, adaptive=flow).advance()
    assert result["controller_outcome"] is None
    assert result["decisions"][0]["reason"] == "useful_ready_computation_remaining"


@pytest.mark.wal
def test_exhausted_controller_finishes_finitely_with_precise_remaining_work(tmp_path):
    from devbench.platform.workflow_actions import WorkflowActions, create_rna_workflow
    from tests.test_investigation_invalidation import step
    planner, flow, controller, *_ = constructive_setup(tmp_path, [decision(), decision()], max_decisions=2)
    flow.insert(step("available"))
    actions = WorkflowActions(planner, flow.ledger, actor="tester", selector=None, controller=controller)
    result = create_rna_workflow(mode="adaptive/1", actions=actions, adaptive=flow).advance(max_steps=4)
    outcome = result["controller_outcome"]
    assert outcome["reason"] == "controller_decision_budget_exhausted"
    assert outcome["remaining_ready_steps"] == ["available"]
    assert outcome["resumption_conditions"] and outcome["publication_granted"] is False
    assert "ceiling" in " ".join(outcome["resumption_conditions"])


@pytest.mark.wal
def test_controller_targeted_literature_uses_custody_without_challenger_assessment(
    workspace_tmp_path, monkeypatch
):
    from devbench.analysis import literature_adapter as literature
    from devbench.analysis import method_sources
    from devbench.platform.workflow_actions import WorkflowActions, create_rna_workflow
    from tests.test_literature_claim_support import PUBMED, claim
    assert hasattr(literature, "EntitledControllerLiteratureQuery"), "Controller Literature entitlement is missing"
    request = literature.LiteratureRequest(operation="fetch", provider="pubmed", identifier="123").model_dump(mode="json")
    script = [
        decision(
            "query_literature", handle_id="targeted", claim_id="claim", request=request
        ),
        decision(),
    ]
    planner, flow, controller, selector, lifecycle, broker, _ = constructive_setup(
        workspace_tmp_path, script
    )
    monkeypatch.setattr(literature.LiteratureCapability, "verify", lambda self: {"version": "0.1.5", "client_sha256": literature.PINNED_CLIENTS})
    calls = []
    def fetch(host, target, *args, **kwargs):
        calls.append((host, target))
        return None, PUBMED, "application/xml"
    monkeypatch.setattr(method_sources, "_fetch_hop", fetch)
    plugin = literature.LiteratureCapability(
        plugin_root=str(workspace_tmp_path / "synthetic-plugin")
    )
    service = literature.LiteratureService(
        flow.authority, flow.lineage_id, planner.methods.root / "literature",
        broker=broker, capability=plugin, allow_network=True, data_scope="synthetic",
    )
    binding = literature.LiteratureClaimBinding(claim_id="claim", claim_sha256=digest(claim().model_dump(mode="json")),
        evidence_sha256=digest("synthetic numerics"), review_binding_sha256=digest("synthetic review"), obligation_id="literature",
        context=literature.BiologicalContext(organism="human", tissue="airway", intervention="dexamethasone",
                                            direction="decreased", uncertainty="observational"))
    service.bind_claim(binding)
    handle = literature.EntitledControllerLiteratureQuery("targeted", service, "controller", (binding,))
    controller.literature_handles = (handle,)
    actions = WorkflowActions(planner, flow.ledger, actor="tester", selector=None, controller=controller)
    result = create_rna_workflow(mode="adaptive/1", actions=actions, adaptive=flow).advance(max_steps=3)
    retained = flow.controller_state()["decisions"][0]["result"]
    assert retained["status"] == "available" and len(calls) == 1
    assert service.replay(retained["snapshot_id"])["responses"][0]["content_sha256"] == __import__("hashlib").sha256(PUBMED).hexdigest()
    assert selector.contexts[-1]["literature_evidence"][0]["snapshot_id"] == retained["snapshot_id"]
    assert result["controller_outcome"]["publication_granted"] is False
    with pytest.raises(PermissionError, match="assess"):
        handle.assess({}, claim=claim(), context_id="controller")


@pytest.mark.wal
def test_reopened_selected_decision_applies_once_without_new_campaign_spend(tmp_path):
    from devbench.engine.workflow_operations import WorkflowOperationLedger
    from devbench.platform.adaptive_workflow import AdaptiveWorkflow
    from devbench.platform.workflow_actions import WorkflowActions
    from tests.test_investigation_invalidation import step
    planner, flow, controller, selector, *_ = constructive_setup(tmp_path, [decision("insert", action_id="new")],
        catalog={"new": step("new")})
    controller.bind(flow)
    actions = WorkflowActions(planner, flow.ledger, actor="tester", selector=None, controller=controller)
    pending = actions.select_adaptive(flow)
    restored = AdaptiveWorkflow(WorkflowOperationLedger(flow.ledger.path), flow.lineage_id)
    controller.bind(restored)
    same = actions.select_adaptive(restored)
    assert pending == same
    assert actions.apply_adaptive_decision(restored, same)["status"] == "applied"
    assert len(selector.contexts) == 1 and len(flow.ledger.campaign_reservations("constructive")) == 1


@pytest.mark.wal
def test_partial_outcome_resumes_only_after_changed_evidence_without_reset(tmp_path):
    from devbench.platform.workflow_actions import WorkflowActions, create_rna_workflow
    from tests.test_investigation_invalidation import step
    planner, flow, controller, selector, *_ = constructive_setup(tmp_path, [decision(), decision("insert", action_id="later")],
        catalog={"later": step("later")})
    actions = WorkflowActions(planner, flow.ledger, actor="tester", selector=None, controller=controller)
    create_rna_workflow(mode="adaptive/1", actions=actions, adaptive=flow).advance()
    assert hasattr(controller, "resume"), "Finite outcomes lack a guarded resumption path"
    with pytest.raises(OperationBlocked, match="changed_evidence"):
        controller.resume(flow)
    flow.insert(step("new-evidence-action"))
    controller.resume(flow)
    create_rna_workflow(mode="adaptive/1", actions=actions, adaptive=flow).advance()
    assert len(flow.controller_state()["decisions"]) == len(selector.contexts) == 2
    assert len(flow.ledger.campaign_reservations("constructive")) == 2


@pytest.mark.wal
def test_cancelled_controller_returns_finite_outcome_without_another_call(tmp_path):
    from devbench.platform.workflow_actions import WorkflowActions, create_rna_workflow
    from tests.test_investigation_invalidation import step
    planner, flow, controller, selector, *_ = constructive_setup(tmp_path, [decision("insert", action_id="first")],
        catalog={"first": step("first")})
    actions = WorkflowActions(planner, flow.ledger, actor="tester", selector=None, controller=controller)
    create_rna_workflow(mode="adaptive/1", actions=actions, adaptive=flow).advance()
    flow.ledger.cancel_campaign("constructive")
    result = create_rna_workflow(mode="adaptive/1", actions=actions, adaptive=flow).advance()
    assert result["controller_outcome"]["reason"] == "cancelled"
    assert "new explicitly authorized campaign" in " ".join(result["controller_outcome"]["resumption_conditions"])
    assert len(selector.contexts) == 1


@pytest.mark.wal
def test_cancellation_after_selection_blocks_structural_mutation(tmp_path):
    from devbench.platform.workflow_actions import WorkflowActions
    from tests.test_investigation_invalidation import step
    planner, flow, controller, *_ = constructive_setup(tmp_path, [decision("insert", action_id="new")], catalog={"new": step("new")})
    controller.bind(flow)
    actions = WorkflowActions(planner, flow.ledger, actor="tester", selector=None, controller=controller)
    pending = actions.select_adaptive(flow)
    flow.ledger.cancel_campaign("constructive")
    result = actions.apply_adaptive_decision(flow, pending)
    assert result["status"] == "rejected" and result["reason"] == "cancelled"
    assert not flow.projection_snapshot()["steps"]


@pytest.mark.wal
def test_structural_decision_checks_revision_inside_mutation_transaction(tmp_path, monkeypatch):
    from devbench.platform.workflow_actions import WorkflowActions
    from tests.test_investigation_invalidation import step
    planner, flow, controller, *_ = constructive_setup(tmp_path, [decision("insert", action_id="new")], catalog={"new": step("new")})
    controller.bind(flow)
    actions = WorkflowActions(planner, flow.ledger, actor="tester", selector=None, controller=controller)
    pending = actions.select_adaptive(flow)
    original = flow.complete_controller_decision
    changed = []
    def race(*args, **kwargs):
        if not changed:
            changed.append(True)
            flow.insert(step("concurrent"))
        return original(*args, **kwargs)
    monkeypatch.setattr(flow, "complete_controller_decision", race)
    result = actions.apply_adaptive_decision(flow, pending)
    assert result["status"] == "rejected"
    assert "new" not in flow.ready()


@pytest.mark.wal
@pytest.mark.parametrize("boundary", ["wrong_context", "private", "stale", "cancelled"])
def test_controller_literature_rejects_wrong_private_stale_cancelled_scope(
    workspace_tmp_path, monkeypatch, boundary
):
    from devbench.analysis.evidence_access import AccessDenied, AccessPolicy
    from devbench.analysis.literature_adapter import (
        EntitledControllerLiteratureQuery,
        LiteratureRequest,
    )
    from tests.test_investigation_publication_epochs import authorize
    from tests.test_literature_claim_support import service
    instance, binding, calls, _ = service(workspace_tmp_path, monkeypatch)
    with instance.ledger._database() as db:
        _, policy = instance.broker._context_policy(db, "ctx")
    instance.broker.register_context("controller", principal_id="controller", lineage_id="study", role="controller",
        phase="analysis", policy_sha256=policy.sha256())
    if boundary == "wrong_context":
        with pytest.raises(PermissionError, match="analysis context"):
            EntitledControllerLiteratureQuery("bad", instance, "ctx", (binding,))
        return
    if boundary == "private":
        instance.data_scope = "private_clinical"
        handle = EntitledControllerLiteratureQuery("targeted", instance, "controller", (binding,))
        result = handle.query(LiteratureRequest(operation="fetch", provider="pubmed", identifier="123").model_dump(mode="json"),
            claim_id="claim", request_id="blocked-private")
        assert result["status"] == "unavailable" and result["failure"] == "PermissionError" and not calls
        return
    handle = EntitledControllerLiteratureQuery("targeted", instance, "controller", (binding,))
    if boundary == "stale":
        instance.broker.register_policy(AccessPolicy(policy.policy_id, policy.version + 1,
            allow_outcomes=True, allow_statistics=True))
    else:
        with instance.ledger._database() as db:
            revision = instance.authority._revision(db, "study")
        instance.authority.commit("study", expected_revision=revision, record={"kind": "grant", "record_id": "grant-a",
            "binding_id": "binding-a", "grant_sha256": digest("synthetic permission")})
        instance.authority.activate_execution("study", expected_revision=revision + 1, binding_id="binding-a", grant_id="grant-a",
            max_jobs=4, authorize=authorize)
        instance.authority.cancel_execution("study", actor="operator")
    with pytest.raises((PermissionError, AccessDenied, OperationBlocked)):
        handle.query(LiteratureRequest(operation="fetch", provider="pubmed", identifier="123").model_dump(mode="json"),
            claim_id="claim", request_id="blocked")
    assert not calls


def test_default_codex_roles_keep_common_private_root_and_exact_profile(tmp_path):
    from devbench.engine.episode_codex import CodexActionSelector
    runtime, planner, _, _, backend, _ = shared_runtime(tmp_path)
    original = CodexActionSelector(requested_model="explicit-test-model", workspace_parent=tmp_path / "actors",
        private_root=planner.methods.root, reasoning_effort="high", memory_mb=512, denied_roots=(tmp_path / "denied",))
    backend.selector = original
    controller, challenger = runtime.default_selector(), runtime.default_selector()
    assert controller is not challenger and controller.private_root == challenger.private_root == planner.methods.root
    assert controller.configuration() == challenger.configuration() == original.configuration()
    with pytest.raises(PermissionError, match="authorized"):
        runtime.invoke(controller, {"decision_kind": "adaptive_controller"}, planner.methods.root / "blocked-native",
            role="controller", purpose="adaptive_controller", node="native", attempt=0)


def test_role_transport_clamps_current_reviewer_deadline(tmp_path):
    runtime, planner, _, selector, _, _ = shared_runtime(tmp_path, decision())
    runtime.invoke(selector, {"decision_kind": "scientific_review"}, planner.methods.root / "short-review",
        role="challenger", purpose="scientific_review", node="short-review", attempt=0, timeout_seconds=1.5,
        token_limit=20, memory_mb=512, max_context_bytes=65536)
    assert selector.forwarded_limits["timeout_seconds"] <= 1.5


@pytest.mark.wal
def test_irreducible_disagreement_reports_exact_hold_and_resumption(tmp_path):
    from devbench.platform.workflow_actions import WorkflowActions, create_rna_workflow
    from tests.test_review_hold_lifecycle import objection, open_cycle
    stopped = {**decision(), "disposition": "unresolved", "reason": "irreducible_disagreement"}
    planner, flow, controller, _, lifecycle, *_ = constructive_setup(tmp_path, [stopped])
    lifecycle.register_evidence("source", digest("synthetic observed disagreement"), relevant_entity_ids=("claim",))
    open_cycle(lifecycle, entity_ids=("claim",))
    held = lifecycle.admit_objection("design-1", objection(entity="claim"))
    actions = WorkflowActions(planner, flow.ledger, actor="tester", selector=None, controller=controller,
        review_lifecycle=lifecycle, review_binding=lambda _: None)
    result = create_rna_workflow(mode="adaptive/1", actions=actions, adaptive=flow).advance()
    outcome = result["controller_outcome"]
    assert outcome["status"] == "unresolved" and outcome["reason"] == "irreducible_disagreement"
    assert outcome["open_holds"][0]["hold_id"] == held["hold_id"]
    assert outcome["open_holds"][0]["resumption_condition"] == "The exact objection is resolved"
    assert lifecycle.state()["holds"][held["hold_id"]]["status"] == "open"


def test_interrupted_role_invocation_is_reconciled_without_redispatch(tmp_path, monkeypatch):
    runtime, planner, _, selector, _, ledger = shared_runtime(tmp_path, KeyboardInterrupt("synthetic interrupted provider"))
    calls = []
    original = selector.select
    def interrupted(*args, **kwargs):
        calls.append(True)
        return original(*args, **kwargs)
    monkeypatch.setattr(selector, "select", interrupted)
    with pytest.raises(KeyboardInterrupt):
        invoke(runtime, planner, selector)
    with pytest.raises(OperationBlocked, match="reconciliation"):
        invoke(runtime, planner, selector)
    assert calls == [True]
    campaign = ledger.get_campaign("parent-campaign")
    assert campaign["verified_spent_tokens"] == 12 and campaign["verified_calls"] == 2


@pytest.mark.wal
def test_reconciliation_rejects_an_outcome_from_a_later_step_generation(tmp_path, monkeypatch):
    """Guard-only projection fault injection; no numerical receipt is created."""
    from devbench.platform.workflow_actions import WorkflowActions
    from tests.test_investigation_invalidation import step
    planner, flow, controller, *_ = constructive_setup(tmp_path, [decision("dispatch", step_id="numeric")])
    flow.insert(step("numeric"))
    controller.bind(flow)
    actions = WorkflowActions(planner, flow.ledger, actor="tester", selector=None, controller=controller)
    pending = actions.select_adaptive(flow)
    context = deepcopy(pending["context"])
    context["graph"]["steps"][0]["generation"] += 1
    context["ready"] = []
    monkeypatch.setattr(actions, "adaptive_controller_context", lambda _: context)
    status = flow.status()
    monkeypatch.setattr(flow, "status", lambda: {**status, "outcomes": {"numeric": "succeeded"}})
    monkeypatch.setattr(flow, "history", lambda _: pytest.fail("Later-generation outcome was accepted as the pending decision"))
    result = actions.apply_adaptive_decision(flow, pending)
    assert result["reason"] == "controller_evidence_changed"


@pytest.mark.wal
def test_reconciliation_recovers_exact_selected_dispatch_without_using_history_tip(tmp_path, monkeypatch):
    from devbench.platform.workflow_actions import WorkflowActions
    from tests.test_investigation_invalidation import Worker, step

    planner, flow, controller, selector, *_ = constructive_setup(tmp_path, [decision("dispatch", step_id="numeric")])
    flow.insert(step("numeric"))
    controller.bind(flow)
    actions = WorkflowActions(planner, flow.ledger, actor="tester", selector=None, controller=controller)
    pending = actions.select_adaptive(flow)
    accepted = flow.dispatch("numeric", Worker(), actor="tester")
    monkeypatch.setattr(flow, "history", lambda _: pytest.fail("Reconciliation must read the exact generation"))
    recovered = actions.apply_adaptive_decision(flow, pending)
    assert recovered == {**accepted, "reconciled": True}
    assert len(selector.contexts) == 1


@pytest.mark.wal
def test_dispatch_only_compatibility_keeps_historical_result_and_projection_shape(tmp_path):
    from types import SimpleNamespace

    from devbench.platform.workflow_actions import create_rna_workflow
    from tests.test_investigation_invalidation import make
    flow = make(tmp_path)
    result = create_rna_workflow(mode="adaptive/1", actions=SimpleNamespace(controller=None), adaptive=flow).advance()
    assert "controller_outcome" not in result and "decisions" not in result
    assert "controller_outcome" not in flow.projection_snapshot()


def controller_narrative_fixture(tmp_path):
    from devbench.platform.workflow_actions import WorkflowActions, create_rna_workflow
    marker = "CONTROLLER_PREFERRED_INTERPRETATION_729"
    stopped = {**decision(), "rationale": marker, "resumption_conditions": [marker + "_continuation"]}
    planner, flow, controller, _, _, broker, _ = constructive_setup(tmp_path, [
        decision("declare_sweep", sweep_id=marker + "_sweep", members=[
            {"step_id": marker + "_omitted", "omission_reason": marker + "_reason"}]), stopped])
    actions = WorkflowActions(planner, flow.ledger, actor="tester", selector=None, controller=controller)
    result = create_rna_workflow(mode="adaptive/1", actions=actions, adaptive=flow).advance(max_steps=2)
    assert result["controller_outcome"]["rationale"] == marker
    return flow, broker, marker


@pytest.mark.wal
@pytest.mark.parametrize("phase", ["design_initial", "publication_initial"])
def test_initial_structural_projection_withholds_outcome_and_sweep_narrative(tmp_path, phase):
    from devbench.analysis.evidence_access import AccessDenied, AccessPolicy
    from devbench.analysis.rna_investigation_projection import project_adaptive_dag
    flow, broker, marker = controller_narrative_fixture(tmp_path)
    policy = AccessPolicy("structural", 1)
    broker.register_policy(policy)
    broker.register_context("initial", principal_id="initial", lineage_id=flow.lineage_id,
        role="challenger", phase=phase, policy_sha256=policy.sha256())
    broker.register_source("structure", content_sha256=digest("DAG structure"), roots=(digest("structure"),), classification="structural")
    delivery = broker.delivery("initial", "structure")
    with pytest.raises(AccessDenied):
        delivery.release({"controller_narrative": marker})
    page = project_adaptive_dag(flow, evidence_delivery=delivery)
    assert marker not in json.dumps(page)
    assert page["inventory_counts"]["sweep_members"] == 1
    assert page["entries"][0]["status"] == "omitted"
    assert page["controller_outcome"]["status"] == "partial"
    reservation = broker.reservation(page["disclosure_reservation_id"])
    assert reservation["content_sha256"] == digest(page)
    assert "controller_narrative" not in reservation["scope"]["tags"]


@pytest.mark.wal
@pytest.mark.parametrize("entitled", [True, False])
def test_later_narrative_projection_requires_original_scope_and_exact_source(tmp_path, entitled):
    from devbench.analysis.evidence_access import AccessPolicy
    from devbench.analysis.rna_investigation_projection import (
        adaptive_controller_narrative,
        project_adaptive_dag,
    )
    flow, broker, marker = controller_narrative_fixture(tmp_path)
    structure_root = digest("structure")
    policy = AccessPolicy("later", 1, allow_outcomes=True, allow_statistics=True,
                          allowed_roots=() if entitled else (structure_root,))
    broker.register_policy(policy)
    broker.register_context("later", principal_id="later", lineage_id=flow.lineage_id,
        role="challenger", phase="narrative_reveal", policy_sha256=policy.sha256())
    broker.register_source("structure", content_sha256=digest("DAG structure"), roots=(structure_root,), classification="structural")
    narrative = adaptive_controller_narrative(flow)
    origin = flow.controller_state()["decisions"][-1]["evidence"]["controller_disclosure_source_id"]
    with flow.ledger._database() as db:
        source = broker._source(db, origin)
    broker.register_source("narrative", content_sha256=digest(narrative), roots=tuple(source["roots"]),
        parents=(origin,), tags=("controller_narrative",), classification="unknown")
    page = project_adaptive_dag(flow, evidence_delivery=broker.delivery("later", "structure"),
                                narrative_delivery=broker.delivery("later", "narrative"))
    assert (marker in json.dumps(page)) is entitled
    assert page["inventory_counts"]["sweep_members"] == 1
    reservation = broker.reservation(page["disclosure_reservation_id"])
    assert reservation["content_sha256"] == digest(page)
    if entitled:
        assert page["controller_narrative"] == narrative
        assert set(source["roots"]) <= set(reservation["scope"]["roots"])
        assert "controller_narrative" in reservation["scope"]["tags"]


@pytest.mark.wal
def test_structural_projection_rechecks_current_graph_before_delivery(tmp_path, monkeypatch):
    from devbench.analysis.evidence_access import AccessPolicy
    from devbench.analysis.rna_investigation_projection import project_adaptive_dag
    from tests.test_investigation_invalidation import step
    flow, broker, _ = controller_narrative_fixture(tmp_path)
    policy = AccessPolicy("structural", 1)
    broker.register_policy(policy)
    broker.register_context("initial", principal_id="initial", lineage_id=flow.lineage_id,
        role="challenger", phase="design_initial", policy_sha256=policy.sha256())
    broker.register_source("structure", content_sha256=digest("DAG structure"), roots=(digest("structure"),), classification="structural")
    original = broker.mark_attempted
    def intervene(*args, **kwargs):
        flow.insert(step("concurrent"))
        return original(*args, **kwargs)
    monkeypatch.setattr(broker, "mark_attempted", intervene)
    with pytest.raises(OperationBlocked, match="projection_changed"):
        project_adaptive_dag(flow, evidence_delivery=broker.delivery("initial", "structure"))


@pytest.mark.wal
@pytest.mark.parametrize("scope", ["restricted", "unknown", "entitled", "entitled_but_omitted",
                                   "query_origin", "wrong_query_context", "late_revocation"])
def test_implicit_outcome_requires_actual_external_hold_origins(tmp_path, monkeypatch, scope):
    from devbench.analysis.evidence_access import AccessDenied, AccessPolicy
    from devbench.analysis.rna_investigation_projection import (
        adaptive_controller_narrative,
        project_adaptive_dag,
    )
    from devbench.analysis.scientific_review_holds import ResolutionPredicate
    from devbench.platform.workflow_actions import WorkflowActions, create_rna_workflow
    from tests.test_investigation_invalidation import step
    from tests.test_review_hold_lifecycle import objection, open_cycle
    marker = "SYNTHETIC_EXTERNAL_WITHHELD_REVIEW_7349"
    planner, flow, controller, selector, lifecycle, broker, access = constructive_setup(tmp_path,
        [decision("insert", action_id="pending")], catalog={"pending": step("pending")})
    actions = WorkflowActions(planner, flow.ledger, actor="tester", selector=None, controller=controller)
    create_rna_workflow(mode="adaptive/1", actions=actions, adaptive=flow).advance()
    assert len(selector.contexts) == 1 and marker not in json.dumps(selector.contexts)
    payload, external_root = {"external_review_fact": marker}, digest("external independent review scope")
    if scope != "unknown":
        broker.register_source("external", content_sha256=digest(payload), roots=(external_root,), classification="unknown")
    if scope in {"query_origin", "wrong_query_context"}:
        context_id = "ctx-design-1" if scope == "query_origin" else "unrelated-review"
        broker.register_context(context_id, principal_id=context_id, lineage_id=flow.lineage_id,
            role="challenger", phase="design_initial", policy_sha256=access.sha256())
        open_cycle(lifecycle, entity_ids=("claim",), entitled_handle_ids=("external-handle",))
        page = broker.release_query_page(context_id, "external", digest("query"), access.sha256(),
            lambda identity, *_: {**payload, "disclosure_reservation_id": identity, "next_offset": None})
        lifecycle.record_query_evidence("design-1", "source", digest(page),
            source_handle_id="external-handle", relevant_entity_ids=("claim",))
    else:
        lifecycle.register_evidence("source", digest(payload), relevant_entity_ids=("claim",))
        open_cycle(lifecycle, entity_ids=("claim",))
    held = lifecycle.admit_objection("design-1", objection(entity="claim", requested_resolution=marker,
        predicate=ResolutionPredicate(kind="CounterevidenceResolved", owner_id="reviewer",
            evidence_required=("source",), resumption_condition=marker + " resolved")))
    assert held["status"] == "held"
    flow.ledger.cancel_campaign("constructive")
    outcome = create_rna_workflow(mode="adaptive/1", actions=actions, adaptive=flow).advance()["controller_outcome"]
    assert outcome["reason"] == "cancelled" and marker in json.dumps(outcome) and len(selector.contexts) == 1
    origin = flow.controller_state()["decisions"][-1]["evidence"]["controller_disclosure_source_id"]
    with flow.ledger._database() as db:
        original = broker._source(db, origin)
    structure_root = digest("structure")
    entitled = scope not in {"restricted", "unknown"}
    policy = AccessPolicy("later", 1, allow_outcomes=True, allow_statistics=True,
        allowed_roots=tuple(sorted({structure_root, *original["roots"], *([external_root] if entitled else [])})))
    broker.register_policy(policy)
    broker.register_context("later", principal_id="later", lineage_id=flow.lineage_id,
        role="challenger", phase="narrative_reveal", policy_sha256=policy.sha256())
    broker.register_source("structure", content_sha256=digest("structure"), roots=(structure_root,), classification="structural")
    structural = project_adaptive_dag(flow, evidence_delivery=broker.delivery("later", "structure"))
    assert marker not in json.dumps(structural) and structural["controller_outcome"]["open_hold_count"] == 1
    material = adaptive_controller_narrative(flow)
    parents = (origin, "external") if scope in {"entitled", "query_origin", "wrong_query_context", "late_revocation"} else (origin,)
    broker.register_source("narrative", content_sha256=digest(material), roots=tuple(original["roots"]),
        parents=parents, tags=("controller_narrative",), classification="unknown")
    def project():
        return project_adaptive_dag(flow, evidence_delivery=broker.delivery("later", "structure"),
                                     narrative_delivery=broker.delivery("later", "narrative"))
    if scope == "late_revocation":
        original_mark = broker.mark_attempted
        def revoke(*args, **kwargs):
            broker.register_source("late-external-peer", content_sha256=digest(payload),
                roots=(digest("new denied scope"),), classification="unknown")
            return original_mark(*args, **kwargs)
        monkeypatch.setattr(broker, "mark_attempted", revoke)
        with pytest.raises(AccessDenied):
            project()
    else:
        page = project()
        assert (marker in json.dumps(page)) is (scope in {"entitled", "query_origin"})
        assert broker.reservation(page["disclosure_reservation_id"])["content_sha256"] == digest(page)


@pytest.mark.wal
def test_review_factory_preserves_campaign_gate_without_reusing_legacy_root(
    workspace_tmp_path,
):
    from devbench.engine.episode_codex import CodexActionSelector
    from tests.test_workflow_scientific_reviews import setup as review_setup
    planner, flow, controller, *_ = constructive_setup(
        workspace_tmp_path / "campaign", []
    )
    (workspace_tmp_path / "legacy").mkdir(mode=0o700)
    _, legacy, _, _ = review_setup(workspace_tmp_path / "legacy")
    _, ledger, backend, campaign = controller.invoker._runtime()
    request = {"format": "devbench.model-call-request/1", "parent_session_id": flow.lineage_id,
        "session_id": "root-probe", "invocation_id": "root-probe", "purpose": "scientific_review",
        "backend_digest": campaign["capability"]["backend_digest"], "runtime_digest": campaign["capability"]["runtime_digest"],
        "prompt_digest": "1" * 64, "schema_digest": "2" * 64, "context_digest": digest({})}
    reservation = ledger.reserve_model_call("constructive", "root-probe", 0, request,
        limits=campaign["identity"]["parent_call_limits"]["default"], purpose="scientific_review")
    gate = backend.model_dispatch_gate(reservation)
    original = CodexActionSelector(
        requested_model="explicit-fixture-model",
        workspace_parent=workspace_tmp_path / "actors",
        private_root=planner.methods.root, memory_mb=512, protected_model_gate=gate,
    )
    legacy.selector = original
    selected = legacy._selector_for_request(planner.methods.root / "review-request", model_invoker=controller.invoker)
    assert selected.private_root == original.private_root
    assert selected.protected_model_gate == original.protected_model_gate == gate
    with pytest.raises((ValueError, PermissionError)):
        legacy._selector_for_request(planner.methods.root / "legacy-request")
    with pytest.raises(TypeError):
        legacy._selector_for_request(planner.methods.root / "untrusted-request", model_invoker=object())
    assert original.private_root == planner.methods.root and original.protected_model_gate == gate


def _admit_omitted_lifecycle_hold(selector, workflow_options):
    from devbench.analysis.scientific_review_holds import (
        ResolutionPredicate,
        ReviewObjection,
    )

    first = []

    def challenger(context):
        if not first:
            first.append(context["review_cycle_id"])
        if not context["query_results"]:
            return {
                "kind": "query_evidence", "handle_id": "numeric",
                "query": {"target_id": "descriptive", "mode": "family_summary"},
            }
        value = {
            "kind": "scientific_review", "outcome": "completed",
            "rationale": "Synthetic review",
            "covered_obligation_ids": [
                item["obligation_id"] for item in context["obligations"]
            ],
            "objections": [],
        }
        if (
            context["review_cycle_id"] == first[0]
            and context["phase"] == "evidence_first"
        ):
            source_id = context["query_handles"][0]["source_id"]
            predicate = ResolutionPredicate(
                kind="CounterevidenceResolved", owner_id="challenger",
                evidence_required=(source_id,),
                resumption_condition="Fresh explicit challenger resolution",
            )
            objection = ReviewObjection(
                category="COUNTEREVIDENCE", affected_entity_ids=("claim",),
                evidence_ids=(source_id,),
                requested_resolution="Resolve the real retained discrepancy",
                severity="material", necessity="An unresolved interpretation remains",
                predicate=predicate,
            )
            value["objections"] = [objection.model_dump(mode="json")]
        return value

    selector.review_handler = challenger
    workflow_options.pop("review_lifecycle")
    workflow_options.pop("review_binding")


def _inject_completion_race(monkeypatch, mode, qc):
    from devbench.analysis.episode_agent import AdaptiveController

    original = AdaptiveController.require_current_completion_reviews
    injected = []

    def intervene(self, workflow, **options):
        original(self, workflow, **options)
        if injected:
            return
        injected.append(True)
        if mode == "race_graph":
            workflow.insert(qc.model_copy(update={
                "step_id": "post-review-work", "branch_id": "post-review-work"
            }))
        else:
            workflow.ledger.cancel_campaign("constructive")

    monkeypatch.setattr(
        AdaptiveController, "require_current_completion_reviews", intervene
    )
    return injected


def _assert_completion_review_outcome(
    mode, outcome, flow, lifecycle, controller, selector, before, injected
):
    assert len(lifecycle.state()["cycles"]) == 2
    if mode == "current":
        assert outcome["status"] == "completed_exploratory"
        assert not outcome["remaining_ready_steps"]
        assert outcome["publication_granted"] is False
        active = controller._review_capabilities[2]
        assert lifecycle.blocking_entities(
            active.packet.binding, touchpoint="publication", entity_ids=("claim",)
        ) == ("claim",)
    elif mode == "changed_computation":
        assert flow.current_use("qc", "out", purpose="query").sha256 != before.sha256
        assert len(flow.history("qc")) == 2
        assert outcome is None or outcome["status"] != "completed_exploratory"
    elif mode in {"changed_fact", "missing_capability"}:
        assert outcome is None
        rejection = flow.controller_state()["decisions"][-1]["result"]
        expected = {
            "changed_fact": "current_adaptive_review_material_required",
            "missing_capability": "current_active_review_capability_required",
        }
        reviews = [
            {key: item["result"].get(key) for key in ("status", "reason", "detail")}
            for item in flow.controller_state()["decisions"]
            if item["decision"]["kind"] == "review"
        ]
        assert rejection["reason"] == expected[mode], reviews
        assert "same cumulative campaign" in rejection["resumption_condition"]
    elif mode in {"race_graph", "race_cancel"}:
        assert injected == [True]
        assert outcome is None or outcome["status"] != "completed_exploratory"
        if mode == "race_cancel":
            assert outcome["status"] == "partial" and outcome["reason"] == "cancelled"
    elif mode == "omitted_lifecycle_hold":
        assert outcome is None or outcome["status"] != "completed_exploratory"
        holds = [
            hold for hold in lifecycle.state()["holds"].values()
            if hold["status"] == "open"
        ]
        assert holds
        assert selector.contexts[-1]["open_holds"][0]["hold_id"] == holds[0]["hold_id"]
    else:
        assert outcome["remaining_ready_steps"] == ["followup"]
        assert outcome["status"] in {"partial", "unresolved"}


@pytest.mark.wal
@pytest.mark.skipif(__import__("os").environ.get("DEVBENCH_T15_DOCKER") != "1", reason="explicit local OCI opt-in")
@pytest.mark.parametrize("mode", ["current", "changed_computation", "changed_fact", "missing_capability", "ready_at_ceiling",
                                  "race_graph", "race_cancel", "omitted_lifecycle_hold", "foreign_lifecycle"])
def test_completion_requires_current_reviews_and_no_pending_work(
    workspace_tmp_path, monkeypatch, mode
):
    """Durable form of the independent spec-review completion reproductions."""
    from devbench.platform.workflow_entrypoints import advance_adaptive_workflow
    from tests.test_exploratory_execution_profile import bound_step, register
    from tests.test_investigation_invalidation import artifact, link, step
    stopped = {**decision(), "disposition": "completed_exploratory", "reason": "all_requested_work_done",
               "unresolved": [], "resumption_conditions": []}
    script = [decision("dispatch", step_id="qc"), decision("review", touchpoint="design"),
              decision("review", touchpoint="publication")]
    if mode == "changed_computation":
        script += [cited("revise", action_id="changed", reason="diagnostic_revisit"), decision("dispatch", step_id="qc")]
    elif mode == "ready_at_ceiling":
        script += [decision("insert", action_id="followup")]
    script += [stopped]
    planner, flow, controller, selector, lifecycle, broker, access = constructive_setup(
        workspace_tmp_path, script, max_decisions=5 if mode == "ready_at_ceiling" else 8
    )
    worker = planner.exploratory_executor(flow.lineage_id, flow.ledger, [{"threshold": value} for value in (1, 2)])
    source = artifact("observations", kind="continuous_matrix").model_copy(update={
        "sha256": worker.training.put(b'{"values":[0,1,4,10]}')})
    flow.register_artifact(source)
    program = """import json, statistics
from pathlib import Path
data=json.loads(Path('/inputs/data').read_bytes())
p=json.loads(Path('/method/parameters.json').read_bytes())
result={'targets':[{'target_id':'descriptive','payload':{'gene_statistics':[{'gene_id':'g1','estimate_status':'descriptive','values':[statistics.mean(data['values'])*p['threshold']]}]}}]}
Path('/outputs/out.json').write_text(json.dumps(result,sort_keys=True,separators=(',',':')))
"""
    method = register(
        planner.methods, workspace_tmp_path / "actual-method", "completion",
        ("data",), ("out",), program,
    )
    qc = bound_step(method, step("qc", link("data", source.version_id, kind="continuous_matrix", immutable=True)))
    flow.insert(qc)
    controller.action_catalog = {"changed": qc.model_copy(update={"parameters_sha256": digest({"threshold": 2})}),
                                "followup": qc.model_copy(update={"step_id": "followup", "branch_id": "followup"})}
    controller.review_builder = review_builder_for(flow, lifecycle, broker, access, worker)
    kwargs = dict(actor="tester", executor=worker, ledger=flow.ledger, controller=controller, allow_data_execution=True,
                  review_lifecycle=lifecycle, review_binding=lambda _: None)
    if mode == "omitted_lifecycle_hold":
        _admit_omitted_lifecycle_hold(selector, kwargs)
    advance_adaptive_workflow(planner, flow.lineage_id, max_steps=3, **kwargs)
    before = flow.current_use("qc", "out", purpose="query")
    injected = None
    if mode == "changed_fact":
        flow.record_basis("unit-declaration", "fact", digest("corrected declared unit"))
    elif mode == "missing_capability":
        controller._review_capabilities.clear()
    elif mode in {"race_graph", "race_cancel"}:
        injected = _inject_completion_race(monkeypatch, mode, qc)
    elif mode == "foreign_lifecycle":
        from devbench.analysis.scientific_review_holds import ReviewHoldLifecycle
        from devbench.platform.workflow_actions import WorkflowActions
        calls = len(selector.contexts)
        actions = WorkflowActions(planner, flow.ledger, actor="tester", selector=None, controller=controller,
            review_lifecycle=ReviewHoldLifecycle(flow.authority, "foreign"), review_binding=lambda _: None)
        with pytest.raises(PermissionError, match="another authoritative workflow"):
            actions.adaptive_controller_context(flow)
        assert len(selector.contexts) == calls
        return
    result = advance_adaptive_workflow(planner, flow.lineage_id, max_steps=len(script) - 3, **kwargs)
    _assert_completion_review_outcome(
        mode, result["controller_outcome"], flow, lifecycle, controller, selector,
        before, injected,
    )


@pytest.mark.wal
@pytest.mark.skipif(__import__("os").environ.get("DEVBENCH_T15_DOCKER") != "1", reason="explicit local OCI opt-in")
@pytest.mark.parametrize("scope", ["eligible", "restricted_output", "restricted_ancestor", "restricted_decision",
                                   "restricted_partition", "late_output_peer", "canonical_peer", "late_canonical_peer"])
def test_controller_output_delivery_preserves_authoritative_source_ancestry(tmp_path, scope):
    from devbench.analysis.evidence_access import AccessDenied, AccessPolicy
    from devbench.platform.workflow_actions import WorkflowActions
    from devbench.platform.workflow_entrypoints import advance_adaptive_workflow
    from tests.test_exploratory_execution_profile import bound_step, register
    from tests.test_investigation_invalidation import artifact, link, step
    planner, flow, controller, selector, _, broker, _ = constructive_setup(tmp_path,
        [decision("dispatch", step_id="numeric"), decision()])
    allowed_root = digest(planner.get(flow.lineage_id)["context"])
    excluded_root = digest("synthetic source outside controller grant")
    access = AccessPolicy("scoped", 1, allow_outcomes=True, allow_statistics=True,
                          diagnostic_policy_id=digest("synthetic"), allowed_roots=(allowed_root,))
    broker.register_policy(access)
    broker.register_context("scoped-controller", principal_id="scoped-controller", lineage_id=flow.lineage_id,
        role="controller", phase="analysis", policy_sha256=access.sha256())
    controller.context_id = "scoped-controller"
    worker = planner.exploratory_executor(flow.lineage_id, flow.ledger, [{"threshold": 1}])
    source = artifact("input", kind="continuous_matrix").model_copy(update={"sha256": worker.training.put(b'{"values":[1,3]}')})
    flow.register_artifact(source)
    broker.register_source("input-custody", content_sha256=source.sha256,
        roots=(excluded_root if scope == "restricted_ancestor" else allowed_root,), classification="unknown")
    program = """import json
from pathlib import Path
data=json.loads(Path('/inputs/data').read_bytes())
Path('/outputs/out.json').write_text(json.dumps({'synthetic_withheld_mean':sum(data['values'])/len(data['values'])},sort_keys=True,separators=(',',':')))
"""
    if scope in {"canonical_peer", "late_canonical_peer"}:
        program = program.replace(",sort_keys=True,separators=(',',':')", "")
    method = register(planner.methods, tmp_path / "actual-method", "source-scope", ("data",), ("out",), program)
    flow.insert(bound_step(method, step("numeric", link("data", source.version_id, kind="continuous_matrix", immutable=True))))
    advance_adaptive_workflow(planner, flow.lineage_id, actor="tester", executor=worker, ledger=flow.ledger,
        controller=controller, allow_data_execution=True, max_steps=1)
    output = flow.current_use("numeric", "out", purpose="query")
    with worker.training.open(output.sha256) as stream:
        value = json.load(stream)
    if scope == "canonical_peer":
        assert digest(value) != output.sha256
    broker.register_source("output-custody", content_sha256=digest(value) if scope == "canonical_peer" else output.sha256,
        roots=(excluded_root if scope in {"restricted_output", "canonical_peer"} else allowed_root,), classification="unknown")
    if scope == "restricted_decision":
        broker.register_source("decision-custody", content_sha256=digest(selector.contexts[0]),
            roots=(excluded_root,), classification="unknown")
    elif scope == "restricted_partition":
        from devbench.engine.partition_authority import PartitionAuthority
        cohort = digest("synthetic partition custody")
        manifest = {"cohort_sha256": cohort, "source_sha256": digest("full synthetic source"),
            "inventory_sha256": digest("synthetic inventory"), "count_path": "X",
            "training": {"sha256": digest("separate synthetic training"), "units_sha256": digest(["train"])},
            "evaluation": {"sha256": source.sha256, "units_sha256": digest(["evaluation"])}}
        # The earlier real computation remains recorded as prior exposure.
        # Protection now withholds later disclosure; it cannot qualify the split.
        assert PartitionAuthority(flow.ledger, cohort).seal(manifest) == "compromised"
    actions = WorkflowActions(planner, flow.ledger, actor="tester", selector=None, controller=controller, executor=worker)
    if scope == "eligible":
        actions.select_adaptive(flow)
        assert value in [item.get("value") for item in selector.contexts[-1]["evidence"].values()]
    else:
        if scope in {"late_output_peer", "late_canonical_peer"}:
            context = actions.adaptive_controller_context(flow)
            broker.register_source("late-peer", content_sha256=digest(value) if scope == "late_canonical_peer" else output.sha256,
                                   roots=(excluded_root,), classification="unknown")
            with pytest.raises((AccessDenied, OperationBlocked)):
                controller.select(flow, context)
            assert len(selector.contexts) == 1
        elif scope == "restricted_decision":
            with pytest.raises((AccessDenied, OperationBlocked)):
                actions.select_adaptive(flow)
            assert len(selector.contexts) == 1
        else:
            actions.select_adaptive(flow)
            assert value not in [item.get("value") for item in selector.contexts[-1]["evidence"].values()]
            assert any(item.get("content_status") == "withheld_by_access_policy" for item in selector.contexts[-1]["evidence"].values())


@pytest.mark.wal
@pytest.mark.skipif(__import__("os").environ.get("DEVBENCH_T15_DOCKER") != "1", reason="explicit local OCI opt-in")
def test_mixed_scope_keeps_eligible_computation_and_withholds_other_output(tmp_path):
    from devbench.analysis.evidence_access import AccessPolicy
    from devbench.platform.workflow_entrypoints import advance_adaptive_workflow
    from tests.test_exploratory_execution_profile import bound_step, register
    from tests.test_investigation_invalidation import artifact, link, step
    planner, flow, controller, selector, _, broker, _ = constructive_setup(tmp_path, [
        decision("dispatch", step_id="restricted"), decision("dispatch", step_id="eligible"), decision()])
    allowed = digest(planner.get(flow.lineage_id)["context"])
    policy = AccessPolicy("mixed", 1, allow_outcomes=True, allow_statistics=True, allowed_roots=(allowed,))
    broker.register_policy(policy)
    broker.register_context("mixed", principal_id="mixed", lineage_id=flow.lineage_id, role="controller",
        phase="analysis", policy_sha256=policy.sha256())
    controller.context_id = "mixed"
    worker = planner.exploratory_executor(flow.lineage_id, flow.ledger, [{"threshold": value} for value in (1, 2)])
    source = artifact("input", kind="continuous_matrix").model_copy(update={"sha256": worker.training.put(b'{"values":[1,3]}')})
    flow.register_artifact(source)
    broker.register_source("input", content_sha256=source.sha256, roots=(allowed,), classification="unknown")
    program = """import json
from pathlib import Path
data=json.loads(Path('/inputs/data').read_bytes()); p=json.loads(Path('/method/parameters.json').read_bytes())
Path('/outputs/out.json').write_text(json.dumps({'mean':sum(data['values'])/len(data['values'])*p['threshold']},sort_keys=True,separators=(',',':')))
"""
    method = register(planner.methods, tmp_path / "method", "mixed", ("data",), ("out",), program)
    for identity, value in (("restricted", 1), ("eligible", 2)):
        node = bound_step(method, step(identity, link("data", "input", kind="continuous_matrix", immutable=True)))
        flow.insert(node.model_copy(update={"parameters_sha256": digest({"threshold": value})}))
    kwargs = dict(actor="tester", executor=worker, ledger=flow.ledger, controller=controller, allow_data_execution=True)
    advance_adaptive_workflow(planner, flow.lineage_id, max_steps=1, **kwargs)
    withheld = flow.current_use("restricted", "out", purpose="query")
    broker.register_source("withheld", content_sha256=withheld.sha256, roots=(digest("not granted"),), classification="unknown")
    result = advance_adaptive_workflow(planner, flow.lineage_id, max_steps=2, **kwargs)
    assert len(flow.history("eligible")) == 1
    values = [item.get("value") for item in selector.contexts[-1]["evidence"].values()]
    assert {"mean": 4.0} in values and {"mean": 2.0} not in values
    assert any(item.get("content_status") == "withheld_by_access_policy" for item in selector.contexts[-1]["evidence"].values())
    assert result["controller_outcome"]["publication_granted"] is False


def review_builder_for(flow, lifecycle, broker, access, worker, *, extra_artifact_step=None):
    from devbench.analysis.scientific_result_contract import (
        ClaimSpec,
        ClaimValidationPlan,
    )
    from devbench.analysis.scientific_review import ActiveReviewPacket
    from devbench.analysis.scientific_review_agent import EntitledReviewQuery
    from devbench.analysis.scientific_review_holds import (
        ReviewBinding,
        ReviewObligation,
        obligations_digest,
    )
    from devbench.analysis.workflow_scientific_reviews import ActiveWorkflowReview

    def build(workflow, touchpoint, sequence):
        context, reveal = "review-initial-" + str(sequence), "review-reveal-" + str(sequence)
        broker.register_context(context, principal_id="challenger-" + str(sequence), lineage_id=workflow.lineage_id, role="challenger",
            phase="design_initial" if touchpoint == "design" else "publication_initial", policy_sha256=access.sha256())
        broker.register_context(reveal, principal_id="challenger-" + str(sequence), lineage_id=workflow.lineage_id, role="challenger",
            phase="narrative_reveal", policy_sha256=access.sha256(), parents=(context,))
        artifact = workflow.current_use("qc", "out", purpose="query")
        with worker.training.open(artifact.sha256) as stream:
            data = json.load(stream)
        source = "actual-qc-" + str(sequence)
        broker.register_source(source, content_sha256=digest(data), roots=(artifact.sha256,), classification="diagnostic")
        handle = EntitledReviewQuery("numeric", data, {}, broker.delivery(context, source))
        graph = workflow.projection_snapshot()
        graph["steps"] = [{key: value for key, value in row.items() if key != "role"} for row in graph["steps"]]
        evidence = {"accepted_qc_sha256": artifact.sha256, "graph": graph}
        additional = ()
        if extra_artifact_step is not None and workflow.status()["outcomes"].get(extra_artifact_step) == "succeeded":
            extra = workflow.current_use(extra_artifact_step, "out", purpose="query")
            with worker.training.open(extra.sha256) as stream:
                evidence["artifacts"] = {"actual-check": json.load(stream)}
            additional = ("actual-check",)
        exposure = {"numeric": broker.exposure(context, source)}
        claim = ClaimSpec(claim_id="claim", revision=1, question="What do the observed synthetic measurements show?",
            population="Synthetic donors", biological_unit="donor", estimand="Observed mean", scale="continuous",
            contrast="none", direction="not_applicable", claim_class="checked_observation", effect_fields=("mean",),
            uncertainty_fields=(), family_id=None, exposure="unknown", evidence_ids=(source, *additional), required_checks=("output_integrity",))
        plan = ClaimValidationPlan(claim_ids=("claim",), competing_explanations=("Neighborhood sensitivity",),
            checks=("output_integrity",), independence_dimension="Fresh actor; shared synthetic data", required_evidence=(source,),
            stopping_conditions=("No additional admitted useful action",), limitations=("No inferential qualification",))
        narrative = "The synthetic observations support a descriptive output only."
        obligations = (ReviewObligation(obligation_id="output_integrity", entity_id="claim", stage="pre_fit"),)
        binding = ReviewBinding(**{key + "_sha256": digest(key) for key in ("facts", "inputs", "design", "procedure", "policy")},
            evidence_sha256=digest(evidence), exposure_sha256=digest(exposure), obligations_sha256=obligations_digest(obligations),
            claim_sha256=digest({"controller_narrative": narrative, "validation_plan": plan.model_dump(mode="json"),
                                "claims": [claim.model_dump(mode="json")]}))
        packet = ActiveReviewPacket(binding=binding, touchpoint=touchpoint, question=claim.question, evidence=evidence,
            prior_exposure=exposure, query_handles=(handle.descriptor(),), controller_narrative=narrative,
            validation_plan=plan, claims=(claim,))
        lifecycle.register_obligations(obligations, binding=binding)
        return ActiveWorkflowReview(packet, lifecycle, broker, context, reveal, "controller", query_handles=(handle,))
    return build


def cited(kind, **fields):
    return lambda context: {**decision(kind, **fields), "evidence_ids": list(context["evidence"])[:16]}


def _advance_with_review_interruption(graph, monkeypatch, mode):
    import sqlite3

    from devbench.analysis import scientific_review_agent
    from devbench.platform.adaptive_workflow import AdaptiveWorkflow

    with monkeypatch.context() as patch:
        if mode == "interrupted-review-journal":
            complete = AdaptiveWorkflow.complete_controller_decision

            def fail_between_review_and_journal(workflow, sequence, result, **options):
                if sequence == 1 and result and result["status"] == "completed":
                    raise sqlite3.OperationalError("synthetic store interruption")
                return complete(workflow, sequence, result, **options)

            patch.setattr(
                AdaptiveWorkflow, "complete_controller_decision",
                fail_between_review_and_journal,
            )
        elif mode == "unstarted-review":
            def interrupt_before_cycle(output, *_args, **_kwargs):
                output.mkdir()
                (output / "setup-error.json").write_text(
                    '{"error":"synthetic setup interruption"}', encoding="utf-8"
                )
                raise OSError("synthetic review setup interruption")

            patch.setattr(
                scientific_review_agent, "request_active_review", interrupt_before_cycle
            )
        return graph.advance()


@pytest.mark.wal
@pytest.mark.parametrize(
    "mode",
    ["new-controller", "cleared-active-controller", "interrupted-review-journal",
     "unstarted-review"],
)
def test_current_reviews_resume_once_on_fresh_controller(
    workspace_tmp_path, monkeypatch, mode
):
    from devbench.analysis.episode_agent import AdaptiveController
    from devbench.analysis.scientific_result_contract import (
        ClaimSpec,
        ClaimValidationPlan,
    )
    from devbench.analysis.scientific_review import ActiveReviewPacket
    from devbench.analysis.scientific_review_agent import EntitledReviewQuery
    from devbench.analysis.scientific_review_holds import (
        ReviewBinding,
        ReviewObligation,
        obligations_digest,
    )
    from devbench.analysis.workflow_scientific_reviews import ActiveWorkflowReview
    from devbench.engine.workflow_operations import WorkflowOperationLedger
    from devbench.platform.workflow_actions import WorkflowActions, create_rna_workflow

    stopped = {**decision(), "disposition": "completed_exploratory", "reason": "all_requested_work_done",
               "unresolved": [], "resumption_conditions": []}
    script = [
        decision("review", touchpoint="design"),
        decision("review", touchpoint="publication"),
        stopped,
    ]
    planner, flow, controller, selector, lifecycle, broker, access = constructive_setup(
        workspace_tmp_path, script
    )

    def review_builder(workflow, touchpoint, sequence):
        context_id, reveal_id = f"initial-{sequence}", f"reveal-{sequence}"
        principal = f"challenger-{sequence}"
        broker.register_context(context_id, principal_id=principal, lineage_id=workflow.lineage_id,
            role="challenger", phase=f"{touchpoint}_initial", policy_sha256=access.sha256())
        broker.register_context(reveal_id, principal_id=principal, lineage_id=workflow.lineage_id,
            role="challenger", phase="narrative_reveal", policy_sha256=access.sha256(), parents=(context_id,))
        result = {"targets": [{"target_id": "descriptive", "payload": {"gene_statistics": [
            {"gene_id": "g1", "estimate_status": "descriptive", "values": [4.0]}]}}]}
        broker.register_source("synthetic-summary", content_sha256=digest(result), roots=(digest("fixture-input"),),
                               classification="diagnostic")
        handle = EntitledReviewQuery("numeric", result, {}, broker.delivery(context_id, "synthetic-summary"))
        claim = ClaimSpec(claim_id="claim", revision=1, question="What does the synthetic summary describe?",
            population="Synthetic donors", biological_unit="donor", estimand="Observed mean", scale="continuous",
            contrast="none", direction="not_applicable", claim_class="checked_observation", effect_fields=("mean",),
            uncertainty_fields=(), family_id=None, exposure="unknown", evidence_ids=("synthetic-summary",),
            required_checks=("integrity",))
        validation = ClaimValidationPlan(claim_ids=("claim",), competing_explanations=("Finite synthetic panel",),
            checks=("integrity",), independence_dimension="Procedure exercise", required_evidence=("synthetic-summary",),
            stopping_conditions=("Required reviews recorded",), limitations=("Synthetic only",))
        narrative = "The panel contains a synthetic descriptive summary."
        evidence = {"summary_sha256": digest(result)}
        exposure = {"numeric": broker.exposure(context_id, "synthetic-summary")}
        obligations = (ReviewObligation(obligation_id="integrity", entity_id="claim", stage="pre_fit"),)
        binding = ReviewBinding(**{key + "_sha256": digest(key) for key in ("facts", "inputs", "design", "procedure", "policy")},
            evidence_sha256=digest(evidence), exposure_sha256=digest(exposure), obligations_sha256=obligations_digest(obligations),
            claim_sha256=digest({"controller_narrative": narrative, "validation_plan": validation.model_dump(mode="json"),
                                 "claims": [claim.model_dump(mode="json")]}))
        lifecycle.register_obligations(obligations, binding=binding)
        packet = ActiveReviewPacket(binding=binding, touchpoint=touchpoint, question=claim.question, evidence=evidence,
            prior_exposure=exposure, query_handles=(handle.descriptor(),), controller_narrative=narrative,
            validation_plan=validation, claims=(claim,))
        return ActiveWorkflowReview(packet, lifecycle, broker, context_id, reveal_id, "controller", query_handles=(handle,))

    controller.review_builder = review_builder
    actions = WorkflowActions(planner, flow.ledger, actor="tester", selector=None, controller=controller)
    graph = create_rna_workflow(mode="adaptive/1", actions=actions, adaptive=flow)
    graph.advance()

    applied = _advance_with_review_interruption(graph, monkeypatch, mode)

    interrupted = mode in {"interrupted-review-journal", "unstarted-review"}
    if interrupted:
        assert applied["decisions"][0]["status"] == "reconciliation_required"
        assert flow.controller_state()["pending"]["sequence"] == 1
    if mode == "unstarted-review":
        review_root = planner.methods.root / "adaptive-reviews" / flow.lineage_id
        assert not (review_root / "1-material.json").exists()
        assert not (review_root / "1").exists()
        assert len(list(review_root.glob("1-unstarted-*/review/setup-error.json"))) == 1
        assert len(lifecycle.state()["cycles"]) == 1
    else:
        assert len(lifecycle.state()["cycles"]) == 2
    reviews = [row["result"] for row in flow.controller_state()["decisions"]]
    assert [row["status"] for row in reviews] == ["completed"] * (1 if interrupted else 2), [row.get("detail") for row in reviews]
    calls_before = len(selector.contexts)

    if mode != "cleared-active-controller":
        ledger = WorkflowOperationLedger(flow.ledger.path, scientific=True)
        flow = planner.adaptive_workflow(flow.lineage_id, ledger)
        controller = AdaptiveController(controller.invoker, broker=broker, context_id="controller", action_catalog={},
            selector=selector, challenger=selector, review_builder=review_builder, review_limits=controller.review_limits)
    else:
        controller._review_capabilities.clear()
    actions = WorkflowActions(planner, flow.ledger, actor="tester", selector=None, controller=controller)
    result = create_rna_workflow(mode="adaptive/1", actions=actions, adaptive=flow).advance(max_steps=2 if interrupted else 1)
    assert len(selector.contexts) == calls_before + (4 if mode == "unstarted-review" else 1)
    assert len(lifecycle.state()["cycles"]) == 2
    if mode != "cleared-active-controller":
        assert result["controller_outcome"]["status"] == "completed_exploratory"
        assert result["controller_outcome"]["publication_granted"] is False
    else:
        assert result["controller_outcome"] is None
        assert result["decisions"][0]["reason"] == "current_active_review_capability_required"


@pytest.mark.wal
@pytest.mark.skipif(__import__("os").environ.get("DEVBENCH_T15_DOCKER") != "1", reason="explicit local OCI opt-in")
@pytest.mark.parametrize("interrupt_after_numeric_acceptance", [False, True])
def test_productive_controller_refines_graph_sensitivity_sweep_and_independent_branch(
    workspace_tmp_path, monkeypatch, interrupt_after_numeric_acceptance
):
    from devbench.platform.workflow_entrypoints import advance_adaptive_workflow
    from tests.test_exploratory_execution_profile import bound_step, register
    from tests.test_investigation_invalidation import artifact, link, step

    script = [decision("dispatch", step_id="qc"), decision("review", touchpoint="design"),
        decision("dispatch", step_id="graph"), decision("dispatch", step_id="cluster"),
        cited("revise", action_id="refine", reason="diagnostic_revisit"),
        decision("dispatch", step_id="graph"), decision("dispatch", step_id="cluster"),
        decision("insert", action_id="sensitivity"), decision("dispatch", step_id="sensitivity"),
        decision("insert", action_id="independent"), decision("dispatch", step_id="independent"),
        decision("insert", action_id="failed"), decision("dispatch", step_id="failed"),
        decision("declare_sweep", sweep_id="neighbors", members=[
            {"step_id": "graph", "omission_reason": None}, {"step_id": "sensitivity", "omission_reason": None},
            {"step_id": "failed", "omission_reason": None}, {"step_id": "omitted", "omission_reason": "unadmitted_parameter"}]),
        decision("review", touchpoint="publication"), decision()]
    planner, flow, controller, selector, lifecycle, broker, access = constructive_setup(
        workspace_tmp_path, script
    )
    worker = planner.exploratory_executor(flow.lineage_id, flow.ledger, [{"threshold": value} for value in (1, 2, 3)])
    source = artifact("observations", kind="continuous_matrix").model_copy(update={
        "sha256": worker.training.put(json.dumps({"values": [0, 1, 4, 10], "gene_id": "g1"}).encode())})
    flow.register_artifact(source)
    common = """import json, statistics
from pathlib import Path
def read(port): return json.loads(Path('/inputs/' + port).read_bytes())
def write(value): Path('/outputs/out.json').write_text(json.dumps(value, allow_nan=False))
parameters = json.loads(Path('/method/parameters.json').read_bytes())
"""
    programs = {
        "qc": """data = read('data')
write({'targets':[{'target_id':'descriptive','payload':{'gene_statistics':[
    {'gene_id':data['gene_id'],'estimate_status':'descriptive','values':[statistics.mean(data['values'])]}]}}]})
""",
        "graph": """values = read('data')['values']; k = int(parameters['threshold'])
write({'k':k, 'edges':{str(i): sorted((j for j in range(len(values)) if j != i), key=lambda j:(abs(values[i]-values[j]),j))[:k]
    for i in range(len(values))}})
""",
        "cluster": """graph = read('data'); write({'degrees':{key:len(value) for key,value in graph['edges'].items()}, 'k':graph['k']})
""",
        "independent": "write({'median':statistics.median(read('data')['values']), 'scope':'descriptive'})\n",
        "failed": "raise ValueError('controlled numerical sweep failure')\n",
    }
    nodes = {}
    for name, code in programs.items():
        kind = "graph" if name == "graph" else "table"
        input_kind = "graph" if name == "cluster" else "continuous_matrix"
        record = register(
            planner.methods, workspace_tmp_path / ("method-" + name), name,
            ("data",), ("out",), common + code,
        )
        upstream = link("data", "graph", kind="graph") if name == "cluster" else link("data", source.version_id, kind=input_kind, immutable=True)
        nodes[name] = bound_step(record, step(name, upstream, kind=kind))
    for name in ("qc", "graph", "cluster"):
        flow.insert(nodes[name])
    flow.insert(step("inference", link("data", source.version_id, kind="continuous_matrix", immutable=True), primary=True))
    controller.action_catalog = {
        "refine": nodes["graph"].model_copy(update={"parameters_sha256": digest({"threshold": 2})}),
        "sensitivity": nodes["graph"].model_copy(update={"step_id": "sensitivity", "branch_id": "sensitivity",
                                                          "parameters_sha256": digest({"threshold": 3})}),
        "independent": nodes["independent"], "failed": nodes["failed"]}
    controller.review_builder = review_builder_for(flow, lifecycle, broker, access, worker)
    kwargs = dict(actor="tester", executor=worker, ledger=flow.ledger, controller=controller,
                  allow_data_execution=True, review_lifecycle=lifecycle, review_binding=lambda _: None)
    if interrupt_after_numeric_acceptance:
        from devbench.platform.adaptive_workflow import AdaptiveWorkflow
        original = AdaptiveWorkflow.complete_controller_decision
        def interrupted(self, sequence, result, **options):
            if sequence == 0 and result is not None and result.get("status") == "succeeded":
                raise KeyboardInterrupt("Synthetic interruption after actual OCI acceptance")
            return original(self, sequence, result, **options)
        with monkeypatch.context() as patch:
            patch.setattr(AdaptiveWorkflow, "complete_controller_decision", interrupted)
            with pytest.raises(KeyboardInterrupt):
                advance_adaptive_workflow(planner, flow.lineage_id, max_steps=1, **kwargs)
    initial = advance_adaptive_workflow(planner, flow.lineage_id, max_steps=4, **kwargs)
    assert initial["steps_dispatched"] == (2 if interrupt_after_numeric_acceptance else 3), initial
    qc = flow.current_use("qc", "out", purpose="cache")
    graph_before = flow.current_use("graph", "out", purpose="cache")
    cluster_before = flow.current_use("cluster", "out", purpose="cache")
    with worker.training.open(qc.sha256) as stream:
        qc_bytes = stream.read()
    final = advance_adaptive_workflow(planner, flow.lineage_id, max_steps=24, **kwargs)
    assert final["controller_outcome"]["status"] == "partial", final
    assert final["controller_outcome"]["publication_granted"] is False
    assert flow.current_use("graph", "out", purpose="cache").sha256 != graph_before.sha256
    assert flow.current_use("cluster", "out", purpose="cache").sha256 != cluster_before.sha256
    assert flow.current_use("qc", "out", purpose="cache") == qc
    with worker.training.open(qc.sha256) as stream:
        assert stream.read() == qc_bytes
    assert len(flow.history("qc")) == 1 and len(flow.history("graph")) == len(flow.history("cluster")) == 2
    assert flow.current_use("sensitivity", "out", purpose="cache").sha256 != flow.current_use("graph", "out", purpose="cache").sha256
    independent = flow.current_use("independent", "out", purpose="cache")
    with worker.training.open(independent.sha256) as stream:
        assert json.load(stream) == {"median": 2.5, "scope": "descriptive"}
    assert flow.status()["blocked"]["inference"] == "adaptive_primary_requires_qualified_procedure"
    assert {row["status"] for row in flow.projection_snapshot()["sweep_members"]} == {"succeeded", "failed", "omitted"}
    cycles = lifecycle.state()["cycles"]
    assert {cycle["touchpoint"] for cycle in cycles.values()} == {"design", "publication"}
    assert all(cycle["phase"] == "completed" and cycle["usage"]["queries"] == 1 for cycle in cycles.values())
    review_contexts = [ctx for ctx in selector.contexts if ctx["decision_kind"] == "scientific_review"]
    assert len(review_contexts) == 6
    assert all("controller_narrative" not in ctx for ctx in review_contexts if ctx["phase"] == "evidence_first")
    assert all("controller_narrative" in ctx for ctx in review_contexts if ctx["phase"] == "narrative_reveal")
    assert not any("literature" in item["decision"]["kind"] for item in flow.controller_state()["decisions"])
    reservations = flow.ledger.campaign_reservations("constructive")
    assert len(reservations) == 22 and {item["request"]["role"] for item in reservations} == {"controller", "challenger"}
    assert flow.ledger.get_campaign("constructive")["verified_spent_tokens"] == 22 * 12


@pytest.mark.wal
@pytest.mark.skipif(__import__("os").environ.get("DEVBENCH_T15_DOCKER") != "1", reason="explicit local OCI opt-in")
def test_requested_resolving_computation_runs_and_fresh_challenger_resolves_hold(
    workspace_tmp_path,
):
    from devbench.analysis.scientific_review_holds import (
        CheckRequest,
        ResolutionPredicate,
        ReviewObjection,
    )
    from devbench.platform.workflow_entrypoints import advance_adaptive_workflow
    from tests.test_exploratory_execution_profile import bound_step, register
    from tests.test_investigation_invalidation import artifact, link, step

    def held_action(kind):
        def choose(context):
            hold = context["open_holds"][0]
            fields = {"hold_id": hold["hold_id"]}
            if kind == "schedule_check":
                fields["action_id"] = "check"
            prefix = "check-output-" if kind == "respond" else "actual-qc-"
            return {**decision(kind, **fields), "evidence_ids": [key for key in context["evidence"] if key.startswith(prefix)]}
        return choose
    script = [decision("dispatch", step_id="qc"), decision("review", touchpoint="design"), decision(),
        held_action("schedule_check"), decision("dispatch", step_id="check"), held_action("respond"),
        decision("review", touchpoint="design"), decision("review", touchpoint="publication"), decision()]
    planner, flow, controller, selector, lifecycle, broker, access = constructive_setup(
        workspace_tmp_path, script
    )
    lifecycle.registered_checks = {"synthetic": "preprocessing"}
    worker = planner.exploratory_executor(flow.lineage_id, flow.ledger, [{"threshold": 1}])
    source = artifact("source", kind="continuous_matrix").model_copy(update={"sha256": worker.training.put(b'{"values":[0,1,4,10]}')})
    flow.register_artifact(source)
    common = """import json, statistics
from pathlib import Path
data=json.loads(Path('/inputs/data').read_bytes())
"""
    qc_program = common + """
out={'targets':[{'target_id':'descriptive','payload':{'gene_statistics':[
    {'gene_id':'g1','estimate_status':'descriptive','values':[statistics.mean(data['values'])]}]}}]}
Path('/outputs/out.json').write_text(json.dumps(out, sort_keys=True, separators=(',',':')))
"""
    qc = register(
        planner.methods, workspace_tmp_path / "qc-method", "qc-check",
        ("data",), ("out",), qc_program,
    )
    check_program = common + """
mean=data['targets'][0]['payload']['gene_statistics'][0]['values'][0]
Path('/outputs/out.json').write_text(json.dumps({'mean_squared':mean**2,'scope':'synthetic check'}, sort_keys=True, separators=(',',':')))
"""
    check = register(
        planner.methods, workspace_tmp_path / "check-method", "resolve",
        ("data",), ("out",), check_program,
    )
    flow.insert(bound_step(qc, step("qc", link("data", "source", kind="continuous_matrix", immutable=True))))
    controller.action_catalog = {"check": bound_step(check, step("check", link("data", "qc")))}
    controller.review_builder = review_builder_for(flow, lifecycle, broker, access, worker, extra_artifact_step="check")
    first_cycle = []
    def review(context):
        cycle = context["review_cycle_id"]
        if not first_cycle:
            first_cycle.append(cycle)
        if not context["query_results"]:
            return {"kind": "query_evidence", "handle_id": "numeric",
                    "query": {"target_id": "descriptive", "mode": "family_summary"}}
        action = {"kind": "scientific_review", "outcome": "completed", "rationale": "Offline protocol-only check assessment",
            "covered_obligation_ids": [item["obligation_id"] for item in context["obligations"]], "objections": []}
        if cycle == first_cycle[0] and context["phase"] == "evidence_first":
            source_id = context["query_handles"][0]["source_id"]
            predicate = ResolutionPredicate(kind="CounterevidenceResolved", owner_id="challenger",
                evidence_required=("actual-check",), resumption_condition="Fresh review of accepted resolving computation")
            request = CheckRequest(threat="Descriptive conclusion needs a numerical consistency check", affected_claim_ids=("claim",),
                required_evidence=("actual-check",), registered_check_id="synthetic", predicate=predicate,
                action_kind="preprocessing", input_evidence_ids=(source_id,))
            action["objections"] = [ReviewObjection(category="MISSING_CHECK", affected_entity_ids=("claim",),
                evidence_ids=(source_id,), requested_resolution="Run the registered resolving computation",
                severity="material", necessity="An available check is needed for this stated conclusion", predicate=predicate,
                check_request=request).model_dump(mode="json")]
        elif cycle != first_cycle[0] and context["phase"] == "narrative_reveal" and context["open_holds"]:
            hold = context["open_holds"][0]
            action["resolution_assessments"] = [{"hold_id": hold["hold_id"], "binding_sha256": context["binding_sha256"],
                "predicate_sha256": hold["predicate_sha256"], "satisfied": True,
                "rationale": "The new accepted numerical check addresses this synthetic protocol predicate",
                "evidence_ids": ["actual-check"]}]
        return action
    selector.review_handler = review
    final = advance_adaptive_workflow(planner, flow.lineage_id, actor="tester", executor=worker,
        ledger=flow.ledger, controller=controller, max_steps=24, allow_data_execution=True,
        review_lifecycle=lifecycle, review_binding=lambda _: None)
    assert final["controller_outcome"] is not None, final
    decisions = flow.controller_state()["decisions"]
    assert decisions[2]["result"]["reason"] == "necessary_resolving_computation_available"
    hold = next(iter(lifecycle.state()["holds"].values()))
    assert hold["status"] == "resolved", hold
    assert hold["checks"][0]["status"] == "completed"
    assert hold["resolution"]["kind"] == "challenger_withdrawal"
    assert len(hold["responses"]) == 1 and hold["responses"][0]["reviewed"]
    output = flow.current_use("check", "out", purpose="query")
    with worker.training.open(output.sha256) as stream:
        assert json.load(stream)["mean_squared"] == 14.0625
    assert len(flow.history("check")) == 1
    assert len(lifecycle.state()["cycles"]) == 3



@pytest.mark.wal
@pytest.mark.parametrize('include_revision_alternative', [False, True])
def test_valid_pinned_check_remains_schedulable_with_revision_alternative(tmp_path, include_revision_alternative):
    from devbench.analysis.scientific_review_holds import CheckRequest, ReviewObjection
    from devbench.platform.workflow_actions import WorkflowActions, create_rna_workflow
    from tests.test_investigation_invalidation import artifact, link, step
    from tests.test_review_hold_lifecycle import objection, open_cycle

    source = artifact('source')
    current = step('qc', link('data', source.version_id, immutable=True))
    eligible = step('new-check', link('data', source.version_id, immutable=True))
    revised = current.model_copy(update={'parameters_sha256': digest('alternative numerical parameter')})
    catalog = {'new-check': eligible}
    if include_revision_alternative:
        catalog = {'refine': revised, **catalog}
    def choose(context):
        return decision('schedule_check', action_id='new-check', hold_id=context['open_holds'][0]['hold_id'],
            evidence_ids=['source'])
    planner, flow, controller, selector, lifecycle, broker, *_ = constructive_setup(tmp_path, [choose], catalog=catalog)
    flow.register_artifact(source)
    flow.insert(current)
    lifecycle.registered_checks = {'synthetic': 'preprocessing'}
    lifecycle.register_evidence('source', source.sha256, relevant_entity_ids=('claim',))
    broker.register_source('source-custody', content_sha256=source.sha256, roots=(source.sha256,), classification='unknown')
    open_cycle(lifecycle, entity_ids=('claim',))
    original = objection(entity='claim')
    request = CheckRequest(threat='Synthetic consistency discrepancy', affected_claim_ids=('claim',),
        required_evidence=('source',), registered_check_id='synthetic', predicate=original.predicate,
        action_kind='preprocessing', input_evidence_ids=('source',))
    held = lifecycle.admit_objection('design-1', ReviewObjection(**{**original.model_dump(mode='json'),
        'category': 'MISSING_CHECK', 'check_request': request.model_dump(mode='json')}))
    assert held['status'] == 'held'
    before = (flow.projection_snapshot(), lifecycle.state(), flow.ledger.campaign_reservations("constructive"))
    assert lifecycle.check_candidate(held["hold_id"], flow, eligible, actor="tester") is True
    assert (flow.projection_snapshot(), lifecycle.state(), flow.ledger.campaign_reservations("constructive")) == before
    actions = WorkflowActions(planner, flow.ledger, actor='tester', selector=None, controller=controller,
        review_lifecycle=lifecycle, review_binding=lambda _: None)
    outcome = create_rna_workflow(mode='adaptive/1', actions=actions, adaptive=flow).advance(max_steps=1)
    assert outcome['decisions'][0] == {'status': 'applied', 'kind': 'schedule_check', 'step_id': 'new-check'}
    assert lifecycle.state()['holds'][held['hold_id']]['checks'][0]['status'] == 'scheduled'
    assert selector.contexts[0]['available_resolving_checks'] == [{'hold_id': held['hold_id'], 'action_id': 'new-check'}]


@pytest.mark.wal
@pytest.mark.parametrize('pins_requested_evidence', [True, False])
def test_stop_gate_requires_a_genuinely_schedulable_check(tmp_path, pins_requested_evidence):
    from devbench.analysis.scientific_review_holds import CheckRequest, ReviewObjection
    from devbench.platform.workflow_actions import WorkflowActions, create_rna_workflow
    from tests.test_investigation_invalidation import artifact, link, step
    from tests.test_review_hold_lifecycle import objection, open_cycle

    required, other = artifact('required-input'), artifact('other-input')
    chosen = required if pins_requested_evidence else other
    candidate = step('check', link('data', chosen.version_id, immutable=True))
    planner, flow, controller, selector, lifecycle, broker, *_ = constructive_setup(tmp_path, [decision()], catalog={'check': candidate})
    for item in (required, other):
        flow.register_artifact(item)
    lifecycle.registered_checks = {'synthetic': 'preprocessing'}
    lifecycle.register_evidence('source', required.sha256, relevant_entity_ids=('claim',))
    broker.register_source('required-source', content_sha256=required.sha256, roots=(required.sha256,), classification='unknown')
    open_cycle(lifecycle, entity_ids=('claim',))
    original = objection(entity='claim')
    request = CheckRequest(threat='Synthetic unresolved consistency', affected_claim_ids=('claim',),
        required_evidence=('source',), registered_check_id='synthetic', predicate=original.predicate,
        action_kind='preprocessing', input_evidence_ids=('source',))
    held = lifecycle.admit_objection('design-1', ReviewObjection(**{**original.model_dump(mode='json'),
        'category': 'MISSING_CHECK', 'check_request': request.model_dump(mode='json')}))
    actions = WorkflowActions(planner, flow.ledger, actor='tester', selector=None, controller=controller,
        review_lifecycle=lifecycle, review_binding=lambda _: None)
    if not pins_requested_evidence:
        with pytest.raises(OperationBlocked, match='resolving_check_input_evidence_not_pinned'):
            actions.schedule_review_check(held['hold_id'], flow, candidate)
    output = create_rna_workflow(mode='adaptive/1', actions=actions, adaptive=flow).advance(max_steps=1)
    if pins_requested_evidence:
        assert output['controller_outcome'] is None
        assert output['decisions'][0]['reason'] == 'necessary_resolving_computation_available'
        assert actions.schedule_review_check(held['hold_id'], flow, candidate)['status'] == 'scheduled'
    else:
        assert output['controller_outcome'] is not None, output['decisions']
        assert output['controller_outcome']['reason'] == 'empty_ready_frontier'
        assert selector.contexts[0]['available_resolving_checks'] == []


@pytest.mark.wal
@pytest.mark.parametrize('disposition', ['partial', 'unresolved'])
def test_terminal_reason_observes_cancellation_in_final_transaction(tmp_path, monkeypatch, disposition):
    from contextlib import contextmanager

    from devbench.analysis import method_planner_recovery
    from devbench.platform.workflow_actions import WorkflowActions

    stopped = {**decision(), 'disposition': disposition}
    planner, flow, controller, selector, *_ = constructive_setup(tmp_path, [stopped])
    controller.bind(flow)
    actions = WorkflowActions(planner, flow.ledger, actor='tester', selector=None, controller=controller)
    pending = actions.select_adaptive(flow)
    real_lease = method_planner_recovery._plan_lease
    @contextmanager
    def cancel_at_final_lease(*args, **kwargs):
        with real_lease(*args, **kwargs):
            flow.ledger.cancel_campaign('constructive')
            yield
    monkeypatch.setattr(method_planner_recovery, '_plan_lease', cancel_at_final_lease)
    outcome = actions.apply_adaptive_decision(flow, pending)
    assert flow.ledger.get_campaign('constructive')['status'] == 'cancelled'
    assert len(selector.contexts) == 1
    assert outcome['reason'] == 'cancelled', outcome
    assert 'new explicitly authorized campaign' in ' '.join(outcome['resumption_conditions'])


@pytest.mark.wal
@pytest.mark.parametrize('origin', ['direct_denied', 'query_denied', 'query_entitled', 'unknown'])
def test_hold_origin_entitlement_on_first_controller_turn(tmp_path, origin):
    from devbench.analysis.evidence_access import AccessDenied, AccessPolicy
    from devbench.analysis.scientific_review_holds import ResolutionPredicate
    from devbench.platform.workflow_actions import WorkflowActions, create_rna_workflow
    from tests.test_review_hold_lifecycle import objection, open_cycle

    marker = 'VERIFIER_EXTERNAL_HOLD_FACT_629204'
    planner, flow, controller, selector, lifecycle, broker, reviewer_policy = constructive_setup(tmp_path, [decision()])
    planner_root = digest(planner.get(flow.lineage_id)['context'])
    external_root = digest('external evidence outside controller data scope')
    allowed = (planner_root, external_root) if origin == 'query_entitled' else (planner_root,)
    policy = AccessPolicy('verifier-controller-policy', 1, allow_outcomes=True, allow_statistics=True,
        allowed_roots=allowed)
    broker.register_policy(policy)
    broker.register_context('verifier-controller', principal_id='verifier-controller', lineage_id=flow.lineage_id,
        role='controller', phase='analysis', policy_sha256=policy.sha256())
    controller.context_id = 'verifier-controller'
    payload = {'review_fact': marker}
    query_receipt = None
    if origin != 'unknown':
        broker.register_source('independent-external-source', content_sha256=digest(payload),
            roots=(external_root,), classification='unknown')
        if origin in {'direct_denied', 'query_denied'}:
            with pytest.raises(AccessDenied, match='roots entitlement'):
                broker.delivery('verifier-controller', 'independent-external-source').release(payload)
    if origin.startswith('query_'):
        broker.register_context('ctx-design-1', principal_id='independent-reviewer', lineage_id=flow.lineage_id,
            role='challenger', phase='design_initial', policy_sha256=reviewer_policy.sha256())
        open_cycle(lifecycle, entity_ids=('claim',), entitled_handle_ids=('external-query-handle',))
        page = broker.release_query_page('ctx-design-1', 'independent-external-source', digest('independent query'),
            reviewer_policy.sha256(), lambda identity, *_: {
                **payload, 'disclosure_reservation_id': identity, 'next_offset': None})
        query_receipt = broker.reservation(page['disclosure_reservation_id'])
        assert query_receipt['state'] == 'delivery_attempted'
        assert query_receipt['content_sha256'] == digest(page)
        lifecycle.record_query_evidence('design-1', 'source', digest(page),
            relevant_entity_ids=('claim',), source_handle_id='external-query-handle')
    else:
        lifecycle.register_evidence('source', digest(payload), relevant_entity_ids=('claim',))
        open_cycle(lifecycle, entity_ids=('claim',))
    admitted = lifecycle.admit_objection('design-1', objection(entity='claim', requested_resolution=marker,
        predicate=ResolutionPredicate(kind='CounterevidenceResolved', owner_id='reviewer',
            evidence_required=('source',), resumption_condition=marker + ' resolved')))
    assert admitted['status'] == 'held'
    actions = WorkflowActions(planner, flow.ledger, actor='tester', selector=None, controller=controller)
    result = create_rna_workflow(mode='adaptive/1', actions=actions, adaptive=flow).advance(max_steps=1)
    emitted = bool(selector.contexts) and marker in json.dumps(selector.contexts[0])
    state = flow.controller_state()
    delivery = None
    if state['decisions']:
        delivery = broker.reservation(state['decisions'][0]['evidence']['controller_disclosure_reservation_id'])
    details = {
        'origin': origin, 'controller_calls': len(selector.contexts), 'marker_disclosed': emitted,
        'external_root': external_root, 'allowed_roots': list(allowed),
        'query_source': query_receipt and query_receipt['source_id'],
        'query_roots': query_receipt and query_receipt['scope']['roots'],
        'controller_disclosure_roots': delivery and delivery['scope']['roots'],
        'controller_disclosure_parents': selector.contexts[0]['disclosure_ancestry']['parents'] if selector.contexts else [],
        'outcome_reason': result['controller_outcome']['reason'],
    }
    assert emitted is (origin == 'query_entitled'), details


@pytest.mark.wal
@pytest.mark.parametrize("origin", ["uncited_query", "page_alias", "wrong_context_alias"])
def test_hold_query_custody_cannot_be_replaced_by_a_citation_or_page_alias(tmp_path, origin):
    from devbench.analysis.evidence_access import AccessPolicy
    from devbench.analysis.scientific_review_holds import ResolutionPredicate
    from devbench.platform.workflow_actions import WorkflowActions, create_rna_workflow
    from tests.test_investigation_invalidation import step
    from tests.test_review_hold_lifecycle import objection, open_cycle

    marker = "SYNTHETIC_RESTRICTED_QUERY_FACT_83245"
    planner, flow, controller, selector, lifecycle, broker, access = constructive_setup(tmp_path,
        [decision("insert", action_id="independent")], catalog={"independent": step("independent")})
    planner_root = digest(planner.get(flow.lineage_id)["context"])
    policy = AccessPolicy("restricted-controller", 1, allow_outcomes=True, allow_statistics=True,
                          allowed_roots=(planner_root,))
    broker.register_policy(policy)
    broker.register_context("restricted-controller", principal_id="restricted-controller", lineage_id=flow.lineage_id,
        role="controller", phase="analysis", policy_sha256=policy.sha256())
    controller.context_id = "restricted-controller"
    broker.register_source("external", content_sha256=digest({"fact": marker}),
        roots=(digest("independent restricted external scope"),), classification="unknown")
    review_context = "other-review" if origin == "wrong_context_alias" else "ctx-design-1"
    broker.register_context(review_context, principal_id="challenger", lineage_id=flow.lineage_id,
        role="challenger", phase="design_initial", policy_sha256=access.sha256())
    if origin == "uncited_query":
        safe = digest("public registered observation")
        broker.register_source("safe", content_sha256=safe, roots=(planner_root,), classification="structural")
        lifecycle.register_evidence("source", safe, relevant_entity_ids=("claim",))
    open_cycle(lifecycle, entity_ids=("claim",), entitled_handle_ids=("external-handle",))
    page = broker.release_query_page(review_context, "external", digest("query"), access.sha256(),
        lambda identity, *_: {"fact": marker, "disclosure_reservation_id": identity, "next_offset": None})
    if origin != "uncited_query":
        lifecycle.record_query_evidence("design-1", "source", digest(page),
            relevant_entity_ids=("claim",), source_handle_id="external-handle")
        broker.register_source("innocent-page-alias", content_sha256=digest(page),
            roots=(planner_root,), classification="structural")
    held = lifecycle.admit_objection("design-1", objection(entity="claim", requested_resolution=marker,
        predicate=ResolutionPredicate(kind="CounterevidenceResolved", owner_id="reviewer",
            evidence_required=("source",), resumption_condition=marker + " resolved")))
    actions = WorkflowActions(planner, flow.ledger, actor="tester", selector=None, controller=controller)
    output = create_rna_workflow(mode="adaptive/1", actions=actions, adaptive=flow).advance(max_steps=1)
    assert len(selector.contexts) == 1
    assert marker not in json.dumps(selector.contexts)
    assert selector.contexts[0]["open_holds"] == [{"hold_id": held["hold_id"], "cycle_id": "design-1",
        "status": "open", "scope": ["claim"], "content_status": "withheld_by_access_policy"}]
    assert output["decisions"][0] == {"status": "applied", "kind": "insert", "step_id": "independent"}


@pytest.mark.wal
@pytest.mark.parametrize("scope", ["units", "populations", "statistics", "parent_root"])
def test_controller_hold_prose_checks_transitive_source_scope(tmp_path, scope):
    from devbench.analysis.evidence_access import AccessPolicy
    from devbench.analysis.scientific_review_holds import ResolutionPredicate
    from devbench.platform.workflow_actions import WorkflowActions
    from tests.test_review_hold_lifecycle import objection, open_cycle

    planner, flow, controller, _, lifecycle, broker, _ = constructive_setup(tmp_path, [])
    marker = "SYNTHETIC_SCOPE_RESTRICTED_HOLD_56281"
    root = digest(planner.get(flow.lineage_id)["context"])
    policy_fields = {"allowed_" + scope: ("admitted",)} if scope in {"units", "populations"} else {}
    policy = AccessPolicy("scoped-controller", 1, allow_outcomes=True, allow_statistics=scope != "statistics",
                          allowed_roots=(root,), **policy_fields)
    broker.register_policy(policy)
    broker.register_context("scoped-controller", principal_id="scoped-controller", lineage_id=flow.lineage_id,
        role="controller", phase="analysis", policy_sha256=policy.sha256())
    controller.context_id = "scoped-controller"
    source_fields = {scope: ("withheld",)} if scope in {"units", "populations"} else {}
    if scope == "statistics":
        source_fields["tags"] = ("inferential_statistics",)
    if scope == "parent_root":
        broker.register_source("ancestor", content_sha256=digest("restricted ancestor"),
            roots=(digest("outside root"),), classification="unknown")
        source_fields["parents"] = ("ancestor",)
    broker.register_source("source", content_sha256=digest(marker), roots=(root,), classification="unknown", **source_fields)
    lifecycle.register_evidence("source", digest(marker), relevant_entity_ids=("claim",))
    open_cycle(lifecycle, entity_ids=("claim",))
    lifecycle.admit_objection("design-1", objection(entity="claim", requested_resolution=marker,
        predicate=ResolutionPredicate(kind="CounterevidenceResolved", owner_id="reviewer",
            evidence_required=("source",), resumption_condition=marker + " resolved")))
    context = WorkflowActions(planner, flow.ledger, actor="tester", selector=None, controller=controller).adaptive_controller_context(flow)
    assert marker not in json.dumps(context)
    assert context["open_holds"][0]["content_status"] == "withheld_by_access_policy"


@pytest.mark.wal
@pytest.mark.parametrize("change", ["query", "source_scope", "review_policy"])
def test_hold_origin_is_rechecked_at_actual_controller_delivery(tmp_path, monkeypatch, change):
    from devbench.analysis.evidence_access import AccessPolicy
    from devbench.analysis.scientific_review_holds import ResolutionPredicate
    from devbench.platform.workflow_actions import WorkflowActions, create_rna_workflow
    from tests.test_review_hold_lifecycle import objection, open_cycle

    planner, flow, controller, selector, lifecycle, broker, access = constructive_setup(tmp_path, [decision()])
    marker = "SYNTHETIC_LATE_WITHHELD_HOLD_68214"
    root = digest(planner.get(flow.lineage_id)["context"])
    policy = AccessPolicy("recipient", 1, allow_outcomes=True, allow_statistics=True, allowed_roots=(root,))
    broker.register_policy(policy)
    broker.register_context("recipient", principal_id="recipient", lineage_id=flow.lineage_id,
        role="controller", phase="analysis", policy_sha256=policy.sha256())
    controller.context_id = "recipient"
    broker.register_context("ctx-design-1", principal_id="reviewer", lineage_id=flow.lineage_id,
        role="challenger", phase="design_initial", policy_sha256=access.sha256())
    broker.register_source("safe", content_sha256=digest(marker), roots=(root,), classification="unknown")
    page = broker.release_query_page("ctx-design-1", "safe", digest("initial query"), access.sha256(),
        lambda identity, *_: {"fact": marker, "disclosure_reservation_id": identity, "next_offset": None})
    open_cycle(lifecycle, entity_ids=("claim",), entitled_handle_ids=("handle",))
    lifecycle.record_query_evidence("design-1", "source", digest(page),
        relevant_entity_ids=("claim",), source_handle_id="handle")
    lifecycle.admit_objection("design-1", objection(entity="claim", requested_resolution=marker,
        predicate=ResolutionPredicate(kind="CounterevidenceResolved", owner_id="reviewer",
            evidence_required=("source",), resumption_condition=marker + " resolved")))
    actions = WorkflowActions(planner, flow.ledger, actor="tester", selector=None, controller=controller)
    assert marker in json.dumps(actions.adaptive_controller_context(flow))
    original = broker.mark_attempted
    changed = []
    def intervene(reservation_id, **options):
        if broker.reservation(reservation_id)["context_id"] == "recipient" and not changed:
            changed.append(change)
            if change == "review_policy":
                broker.register_policy(AccessPolicy("controller", 2, allow_outcomes=True, allow_statistics=True))
            else:
                broker.register_source("late", content_sha256=digest(marker if change == "source_scope" else "late query"),
                    roots=(digest("new denied root"),), classification="unknown")
                if change == "query":
                    broker.release_query_page("ctx-design-1", "late", digest("late query"), access.sha256(),
                        lambda identity, *_: {"fact": "late query", "disclosure_reservation_id": identity, "next_offset": None})
        return original(reservation_id, **options)
    monkeypatch.setattr(broker, "mark_attempted", intervene)
    output = create_rna_workflow(mode="adaptive/1", actions=actions, adaptive=flow).advance(max_steps=1)
    assert changed == [change]
    assert selector.contexts == []
    assert output["controller_outcome"]["status"] in {"partial", "unresolved"}


@pytest.mark.wal
@pytest.mark.parametrize('when,deny_ancestor', [('before_context',True), ('after_context',True), ('after_context',False)])
def test_prepared_controller_context_revalidates_new_query_alias_ancestry(tmp_path, when, deny_ancestor):
    from devbench.analysis.evidence_access import AccessDenied, AccessPolicy
    from devbench.analysis.scientific_review_holds import ResolutionPredicate
    from devbench.platform.workflow_actions import WorkflowActions
    from tests.test_review_hold_lifecycle import objection, open_cycle

    planner, flow, controller, selector, review, broker, review_policy = constructive_setup(tmp_path, [decision()])
    marker='INDEPENDENT_QUERY_ALIAS_ORIGIN_98153'
    root=digest(planner.get(flow.lineage_id)['context'])
    policy=AccessPolicy('controller-narrow',1,allow_outcomes=True,allow_statistics=True,allowed_roots=(root,))
    broker.register_policy(policy)
    broker.register_context('controller-narrow',principal_id='controller-narrow',lineage_id=flow.lineage_id,
        role='controller',phase='analysis',policy_sha256=policy.sha256())
    controller.context_id='controller-narrow'
    broker.register_context('ctx-design-1',principal_id='reviewer',lineage_id=flow.lineage_id,
        role='challenger',phase='design_initial',policy_sha256=review_policy.sha256())
    broker.register_source('query-source',content_sha256=digest(marker),roots=(root,),classification='unknown')
    page=broker.release_query_page('ctx-design-1','query-source',digest('query'),review_policy.sha256(),
        lambda identity,*_: {'observed_fact':marker,'disclosure_reservation_id':identity,'next_offset':None})
    open_cycle(review,entity_ids=('claim',),entitled_handle_ids=('query',))
    review.record_query_evidence('design-1','source',digest(page),relevant_entity_ids=('claim',),source_handle_id='query')
    review.admit_objection('design-1',objection(entity='claim',requested_resolution=marker,
        predicate=ResolutionPredicate(kind='CounterevidenceResolved',owner_id='reviewer',evidence_required=('source',),resumption_condition=marker)))
    controller.bind(flow)
    actions=WorkflowActions(planner,flow.ledger,actor='tester',selector=None,controller=controller)
    def register_alias():
        broker.register_source('new-parent',content_sha256=digest('parent-content'),roots=(root,),classification='unknown')
        broker.register_source('new-alias',content_sha256=digest(marker),roots=(root,),parents=('new-parent',),classification='unknown')
        if deny_ancestor:
            broker.register_source('parent-scope-correction',content_sha256=digest('parent-content'),roots=(digest('outside-controller-grant'),),classification='unknown')
    if when=='before_context':
        register_alias()
    context=actions.adaptive_controller_context(flow)
    if when=='after_context':
        register_alias()
        with flow.ledger._database() as db:
            assert digest(broker._source(db,'query-source'))==context['disclosure_ancestry']['sources']['query-source']
    with flow.ledger._database() as db:
        recipient,current=broker._context_policy(db,'controller-narrow')
        if deny_ancestor:
            with pytest.raises(AccessDenied,match='roots entitlement'):
                broker._check(recipient,current,broker._source(db,'new-parent'),set())
        else:
            broker._check(recipient,current,broker._source(db,'new-parent'),set())
    try:
        controller.select(flow,context)
    except (AccessDenied, OperationBlocked):
        pass
    emitted=marker in json.dumps(selector.contexts)
    assert emitted is (not deny_ancestor), {'when':when,'denied_ancestor':deny_ancestor,'controller_calls':len(selector.contexts),'emitted':emitted}


@pytest.mark.wal
@pytest.mark.parametrize('registration,deny_ancestor', [('before_context', True), ('at_delivery', True), ('at_delivery', False)])
def test_late_query_source_alias_parent_scope_is_rechecked(tmp_path, monkeypatch, registration, deny_ancestor):
    from devbench.analysis.evidence_access import AccessDenied, AccessPolicy
    from devbench.analysis.scientific_review_holds import ResolutionPredicate
    from devbench.platform.workflow_actions import WorkflowActions, create_rna_workflow
    from tests.test_review_hold_lifecycle import objection, open_cycle

    planner, flow, controller, selector, lifecycle, broker, access = constructive_setup(tmp_path, [decision()])
    marker = 'VERIFIER_LATE_PARENT_SCOPE_836293'
    allowed_root = digest(planner.get(flow.lineage_id)['context'])
    denied_root = digest('not granted to controller')
    policy = AccessPolicy('recipient', 1, allow_outcomes=True, allow_statistics=True, allowed_roots=(allowed_root,))
    broker.register_policy(policy)
    broker.register_context('recipient', principal_id='recipient', lineage_id=flow.lineage_id,
        role='controller', phase='analysis', policy_sha256=policy.sha256())
    controller.context_id = 'recipient'
    broker.register_context('ctx-design-1', principal_id='reviewer', lineage_id=flow.lineage_id,
        role='challenger', phase='design_initial', policy_sha256=access.sha256())
    broker.register_source('safe', content_sha256=digest(marker), roots=(allowed_root,), classification='unknown')
    page = broker.release_query_page('ctx-design-1', 'safe', digest('initial query'), access.sha256(),
        lambda identity, *_: {'fact': marker, 'disclosure_reservation_id': identity, 'next_offset': None})
    open_cycle(lifecycle, entity_ids=('claim',), entitled_handle_ids=('handle',))
    lifecycle.record_query_evidence('design-1', 'source', digest(page), relevant_entity_ids=('claim',), source_handle_id='handle')
    lifecycle.admit_objection('design-1', objection(entity='claim', requested_resolution=marker,
        predicate=ResolutionPredicate(kind='CounterevidenceResolved', owner_id='reviewer',
            evidence_required=('source',), resumption_condition=marker + ' resolved')))
    with flow.ledger._database() as db:
        before_source = digest(broker._source(db, 'safe'))
    def add_alias_then_restrict_parent():
        broker.register_source('ancestor', content_sha256=digest('ancestor bytes'), roots=(allowed_root,), classification='unknown')
        broker.register_source('late-source-alias', content_sha256=digest(marker), roots=(allowed_root,),
            parents=('ancestor',), classification='unknown')
        if deny_ancestor:
            broker.register_source('restricted-ancestor-peer', content_sha256=digest('ancestor bytes'),
                roots=(denied_root,), classification='unknown')
    if registration == 'before_context':
        add_alias_then_restrict_parent()
    original_mark = broker.mark_attempted
    changed = []
    def intervene(reservation_id, **options):
        if registration == 'at_delivery' and broker.reservation(reservation_id)['context_id'] == 'recipient' and not changed:
            add_alias_then_restrict_parent()
            changed.append(True)
        return original_mark(reservation_id, **options)
    monkeypatch.setattr(broker, 'mark_attempted', intervene)
    actions = WorkflowActions(planner, flow.ledger, actor='tester', selector=None, controller=controller)
    outcome = create_rna_workflow(mode='adaptive/1', actions=actions, adaptive=flow).advance(max_steps=1)
    with flow.ledger._database() as db:
        after_source = digest(broker._source(db, 'safe'))
        recipient, current_policy = broker._context_policy(db, 'recipient')
        if deny_ancestor:
            with pytest.raises(AccessDenied, match='roots entitlement'):
                broker._check(recipient, current_policy, broker._source(db, 'ancestor'), set())
        else:
            broker._check(recipient, current_policy, broker._source(db, 'ancestor'), set())
    details = {'registration': registration, 'controller_calls': len(selector.contexts),
        'marker_disclosed': marker in json.dumps(selector.contexts), 'direct_source_witness_unchanged': before_source == after_source,
        'outcome_reason': (outcome.get('controller_outcome') or {}).get('reason'),
        'sources': list(selector.contexts[0]['disclosure_ancestry']['sources']) if selector.contexts else []}
    assert (marker in json.dumps(selector.contexts)) is (not deny_ancestor), details
