"""Crash qualification over real planner state and immutable offline captures.

These controls simulate process boundaries; they execute no provider or RNA fit.
"""
from tests.support.workflow_science_fixtures import RecordingScientificRuntime
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
import json
import os
import sqlite3
from threading import Event

import pytest

from devbench.analysis.workflow_actions import create_rna_workflow
from devbench.analysis.workflow_state import digest
from tests.test_langgraph_investigations import WorkflowFixtureBackend, independent_setup
from tests.test_rna_investigation_parent_integration import CapturedFixtureBackend


class InjectedCrash(RuntimeError):
    pass


class RecoveryFixtureBackend(WorkflowFixtureBackend):
    """Retain the fixture's exact response, usage and distinct child sessions."""

    def __init__(self, methods, selector):
        super().__init__(methods, selector)
        self.launches = []
        self.crash_after_capture = False
        self.crash_after_launch = False

    def investigate(self, request, **kwargs):
        self.launches.append(request["investigation_id"])
        if self.crash_after_launch:
            raise InjectedCrash("after child launch; protected capture unavailable")
        outcome = super().investigate(request, **kwargs)
        if self.crash_after_capture:
            raise InjectedCrash("actual child capture retained before ledger settlement")
        return outcome

    def verify_child_result(self, packet_ref, result_ref):
        # The stricter shared verifier proves the response, packet, schema,
        # runtime, usage and committed charge, rather than a success flag.
        return CapturedFixtureBackend.verify_child_result(self, packet_ref, result_ref)


def recovery_setup(tmp_path, allocation=1, *, stage="pre_fit_methods"):
    pytest.importorskip("langgraph")
    planner, plan, _, ledger, actions = independent_setup(tmp_path, allocation, stage=stage)
    backend = RecoveryFixtureBackend(planner.methods, actions.selector)
    workflow = create_rna_workflow(mode="independent_investigations", actions=actions,
        checkpoint_path=tmp_path / "recovery-checkpoints.sqlite", investigation_backend=backend)
    return planner, plan, backend, ledger, actions, workflow


def child_rows(ledger, identity):
    return [row for row in ledger.campaign_reservations(identity)
            if row["purpose"] == "independent_investigation"]


def fresh_recovery_objects(planner, ledger, actions):
    from devbench.analysis.method_planner import ScientificMethodPlanner
    from devbench.analysis.methods import MethodService
    from devbench.analysis.workflow_actions import WorkflowActions
    from devbench.analysis.workflow_operations import WorkflowOperationLedger
    from tests.test_rna_investigation_parent_integration import CapturedFixtureSelector
    fresh_planner = ScientificMethodPlanner(MethodService(planner.methods.root, initialize=False, reconcile=False))
    fresh_ledger = WorkflowOperationLedger(ledger.path)
    selector = CapturedFixtureSelector()
    backend = RecoveryFixtureBackend(fresh_planner.methods, selector)
    fresh_actions = WorkflowActions(fresh_planner, fresh_ledger, actor="fresh-offline-recovery",
        selector=selector, investigation_policy=actions.investigation_policy,
        investigation_evidence=actions.investigation_evidence, scientific_workflow=RecordingScientificRuntime())
    return fresh_planner, fresh_ledger, selector, backend, fresh_actions


def fail_after_callback(monkeypatch, ledger, selected_node):
    original = ledger.run

    def run(workflow_id, node, attempt, request, callback):
        if node == selected_node:
            def interrupted():
                callback()
                raise InjectedCrash("after " + selected_node + " mutation before operation commit")
            return original(workflow_id, node, attempt, request, interrupted)
        return original(workflow_id, node, attempt, request, callback)

    monkeypatch.setattr(ledger, "run", run)


def test_capture_before_journal_commit_is_verified_and_charged_once(tmp_path):
    planner, plan, backend, ledger, actions, workflow = recovery_setup(tmp_path)
    identity = plan["session_id"]
    backend.crash_after_capture = True
    try:
        interrupted = workflow.start(actions.create_state(identity))
        assert interrupted["status"] == "reconciliation_required"
        assert len(backend.launches) == 1
        assert child_rows(ledger, identity)[0]["status"] == "active"
        assert workflow.resume(identity) == interrupted
        recovered = workflow.reconcile(identity)
        assert recovered["status"] == "ready"
        charged = child_rows(ledger, identity)[0]
        assert charged["status"] == "completed"
        assert charged["capture"]["usage"]["normalized_total_tokens"] == 12
        assert recovered["result_refs"]
        assert workflow.resume(identity)["status"] == "review_required"
        assert len(backend.launches) == 1
        assert len(planner.get(identity)["investigation_evidence"]) == 1
    finally:
        workflow.close()


def test_uncaptured_launch_remains_blocked_without_automatic_retry(tmp_path):
    planner, plan, backend, ledger, actions, workflow = recovery_setup(tmp_path)
    identity = plan["session_id"]
    backend.crash_after_launch = True
    try:
        interrupted = workflow.start(actions.create_state(identity))
        assert interrupted["status"] == "reconciliation_required"
        assert child_rows(ledger, identity)[0]["status"] in {"active", "uncertain"}
        for _ in range(2):
            assert workflow.resume(identity)["status"] == "reconciliation_required"
            assert workflow.reconcile(identity)["status"] == "reconciliation_required"
        assert len(backend.launches) == 1
        assert ledger.get_campaign(identity)["unresolved_count"] == 1
        assert not planner.get(identity).get("investigation_evidence")
    finally:
        workflow.close()


def test_raw_capture_before_terminal_outcome_loss_retains_explicit_uncertainty(tmp_path, monkeypatch):
    planner, plan, backend, ledger, actions, workflow = recovery_setup(tmp_path)
    identity = plan["session_id"]
    verify = backend.verify_selection_capture

    def interrupted(output, *, bindings, reservation):
        verified = verify(output, bindings=bindings, reservation=reservation)
        if ledger.get_model_reservation(reservation)["purpose"] == "independent_investigation":
            raise InjectedCrash("raw capture retained; terminal outcome was never indexed")
        return verified

    monkeypatch.setattr(backend, "verify_selection_capture", interrupted)
    try:
        cursor = workflow.start(actions.create_state(identity))
        assert cursor["status"] == "reconciliation_required"
        row = child_rows(ledger, identity)[0]
        assert row["status"] == "uncertain" and row["capture"] is None
        captures = [record for record in planner.methods.list_records("artifact")
                    if record["artifact_type"] == "rna_investigation_capture"]
        assert len(captures) == 2  # Actual parent and child immutable captures.
        assert workflow.reconcile(identity)["status"] == "reconciliation_required"
        assert workflow.resume(identity)["status"] == "reconciliation_required"
        assert ledger.get_campaign(identity)["outstanding_reserved_tokens"] == row["limits"]["tokens"]
        assert cursor["result_refs"] == {}
        assert not planner.get(identity).get("investigation_evidence")
        assert len(backend.launches) == 1
    finally:
        workflow.close()


def test_accepted_charged_reassessment_recovers_without_another_parent_call(tmp_path, monkeypatch):
    planner, plan, backend, ledger, actions, workflow = recovery_setup(tmp_path)
    identity = plan["session_id"]
    fail_after_callback(monkeypatch, ledger, "reassess")
    try:
        interrupted = workflow.start(actions.create_state(identity))
        assert interrupted["phase"] == "reassess"
        assert interrupted["status"] == "reconciliation_required"
        before = deepcopy(planner.get(identity))
        calls = len(actions.selector.contexts)
        assert before["candidate_reassessment"]
        assert before["selection_history"][-1]["status"] == "accepted"
        reservation = ledger.get_model_reservation(before["selection_history"][-1]["model_reservation_ref"])
        assert reservation["status"] == "completed"
        recovered = workflow.reconcile(identity)
        assert recovered["status"] == "ready"
        assert ledger.get(identity, "reassess", 0)["status"] == "completed"
        assert len(actions.selector.contexts) == calls
        assert planner.get(identity) == before
        monkeypatch.undo()
        assert workflow.resume(identity)["status"] == "review_required"
        assert len(backend.launches) == 1
    finally:
        workflow.close()


def test_attachment_mutation_recovers_from_verified_bundle(tmp_path, monkeypatch):
    planner, plan, backend, ledger, actions, workflow = recovery_setup(tmp_path)
    identity = plan["session_id"]
    fail_after_callback(monkeypatch, ledger, "attach")
    try:
        interrupted = workflow.start(actions.create_state(identity))
        assert interrupted["phase"] == "attach"
        assert interrupted["status"] == "reconciliation_required"
        assert ledger.get(identity, "attach", 0)["status"] == "uncertain"
        retained = deepcopy(planner.get(identity))
        calls = len(actions.selector.contexts)
        recovered = workflow.reconcile(identity)
        assert recovered["status"] == "ready"
        assert recovered["reconciliation_ref"] == retained["investigation_evidence"][0]["bundle_ref"]
        assert ledger.get(identity, "attach", 0)["status"] == "completed"
        assert planner.get(identity) == retained
        assert len(actions.selector.contexts) == calls
        assert len(backend.launches) == 1
    finally:
        workflow.close()


def test_lost_checkpoint_reconstructs_attached_evidence_without_dispatch(tmp_path, monkeypatch):
    planner, plan, backend, ledger, actions, workflow = recovery_setup(tmp_path)
    identity = plan["session_id"]
    fail_after_callback(monkeypatch, ledger, "attach")
    interrupted = workflow.start(actions.create_state(identity))
    assert interrupted["phase"] == "attach"
    retained = deepcopy(planner.get(identity))
    calls = len(actions.selector.contexts)
    checkpoint_path = workflow.checkpoint_path
    workflow.close()
    # Only this generated checkpoint is lost. Planner/capture/ledger evidence
    # stays untouched and must establish the exact recovered effects.
    with sqlite3.connect(checkpoint_path) as db:
        db.execute("DELETE FROM checkpoints WHERE thread_id=?", (identity,))
        db.execute("DELETE FROM writes WHERE thread_id=?", (identity,))
    restored = create_rna_workflow(mode="independent_investigations", actions=actions,
        checkpoint_path=checkpoint_path, investigation_backend=backend)
    try:
        recovered = restored.reconcile(identity)
        assert recovered["status"] == "ready"
        assert recovered["phase"] == "attach"
        assert recovered["expected_plan_hash"] == digest(retained)
        assert recovered["reconciliation_ref"] == retained["investigation_evidence"][0]["bundle_ref"]
        assert len(actions.selector.contexts) == calls
        assert len(backend.launches) == 1
        assert planner.get(identity) == retained
    finally:
        restored.close()


def test_reservation_commit_loss_retains_admission_without_launch(tmp_path, monkeypatch):
    planner, plan, backend, ledger, actions, workflow = recovery_setup(tmp_path)
    identity = plan["session_id"]
    reserve = ledger.reserve_model_call

    def interrupted(*args, **kwargs):
        reservation = reserve(*args, **kwargs)
        if kwargs.get("purpose") == "independent_investigation":
            raise InjectedCrash("child reservation committed; launch was not reached")
        return reservation

    monkeypatch.setattr(ledger, "reserve_model_call", interrupted)
    try:
        cursor = workflow.start(actions.create_state(identity))
        assert cursor["status"] == "reconciliation_required"
        row = child_rows(ledger, identity)[0]
        assert row["status"] == "reserved"
        assert row["capture"] is None
        assert ledger.get_campaign(identity)["outstanding_reserved_tokens"] == row["limits"]["tokens"]
        assert workflow.resume(identity)["status"] == "reconciliation_required"
        assert backend.launches == []
        assert not planner.get(identity).get("investigation_evidence")
    finally:
        workflow.close()


def test_settlement_commit_loss_reuses_capture_without_double_charge(tmp_path, monkeypatch):
    planner, plan, backend, ledger, actions, workflow = recovery_setup(tmp_path)
    identity = plan["session_id"]
    settle = ledger.settle_model_call
    crashed = []

    def interrupted(reservation, **kwargs):
        receipt = settle(reservation, **kwargs)
        if ledger.get_model_reservation(reservation)["purpose"] == "independent_investigation" and not crashed:
            crashed.append(True)
            raise InjectedCrash("actual usage settled before graph return")
        return receipt

    monkeypatch.setattr(ledger, "settle_model_call", interrupted)
    try:
        cursor = workflow.start(actions.create_state(identity))
        assert cursor["status"] == "reconciliation_required"
        charged = deepcopy(child_rows(ledger, identity)[0])
        assert charged["status"] == "completed"
        recovered = workflow.reconcile(identity)
        assert recovered["status"] == "ready"
        assert child_rows(ledger, identity)[0] == charged
        assert len(backend.launches) == 1
        assert ledger.get_campaign(identity)["verified_spent_tokens"] == 24
    finally:
        workflow.close()


def test_child_result_commit_before_checkpoint_loss_never_redispatches(tmp_path, monkeypatch):
    planner, plan, backend, ledger, actions, workflow = recovery_setup(tmp_path)
    identity = plan["session_id"]
    launch = workflow._launch_child
    crashed = []

    def interrupted(state):
        outcome = launch(state)
        if not crashed:
            crashed.append(True)
            raise InjectedCrash("child result and usage committed; checkpoint return lost")
        return outcome

    monkeypatch.setattr(workflow, "_launch_child", interrupted)
    # Recompile so the injected process boundary is the callable the graph uses.
    workflow._compile()
    try:
        with pytest.raises(InjectedCrash, match="checkpoint return lost"):
            workflow.start(actions.create_state(identity))
        assert child_rows(ledger, identity)[0]["status"] == "completed"
        assert workflow.resume(identity)["status"] == "review_required"
        assert len(backend.launches) == 1
        assert len(planner.get(identity)["investigation_evidence"]) == 1
    finally:
        workflow.close()


@pytest.mark.parametrize("contender", ["start", "resume"])
def test_same_parent_start_resume_race_has_one_actual_invocation(tmp_path, monkeypatch, contender):
    planner, plan, backend, ledger, actions, workflow = recovery_setup(tmp_path)
    identity = plan["session_id"]
    initial = actions.create_state(identity)
    second = create_rna_workflow(mode="independent_investigations", actions=actions,
        checkpoint_path=workflow.checkpoint_path, investigation_backend=backend)
    entered, release = Event(), Event()
    select = actions.selector.select

    def hold(context, output, **kwargs):
        if not entered.is_set():
            entered.set()
            assert release.wait(10), "Test failed to release its synthetic selector"
        return select(context, output, **kwargs)

    monkeypatch.setattr(actions.selector, "select", hold)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            running = pool.submit(workflow.start, initial)
            assert entered.wait(10)
            try:
                with pytest.raises(ValueError, match="lease.*active"):
                    second.start(deepcopy(initial)) if contender == "start" else second.resume(identity)
            finally:
                release.set()
            assert running.result(timeout=10)["status"] == "review_required"
        assert len(backend.launches) == 1
        assert len(actions.selector.contexts) == 3
        assert len(child_rows(ledger, identity)) == 1
    finally:
        release.set()
        second.close()
        workflow.close()


@pytest.mark.parametrize("change", ["policy", "evidence", "runtime", "source"])
def test_resume_rejects_changed_frozen_request_and_runtime(tmp_path, change):
    planner, plan, backend, ledger, actions, workflow = recovery_setup(tmp_path)
    identity = plan["session_id"]
    try:
        assert workflow.start(actions.create_state(identity))["status"] == "review_required"
        calls = len(actions.selector.contexts)
        if change == "policy":
            actions.investigation_policy["max_context_bytes"] -= 1
        elif change == "evidence":
            actions.investigation_evidence["question_record"]["objective"] += " changed"
        else:
            backend.capability[change + "_digest"] = "e" * 64
        with pytest.raises(ValueError, match="changed|stale|binding"):
            workflow.resume(identity)
        assert len(actions.selector.contexts) == calls
        assert len(backend.launches) == 1
    finally:
        workflow.close()


def test_reconciliation_rejects_caller_fabricated_child_answer(tmp_path, monkeypatch):
    planner, plan, backend, ledger, actions, workflow = recovery_setup(tmp_path)
    identity = plan["session_id"]
    backend.crash_after_capture = True
    try:
        cursor = workflow.start(actions.create_state(identity))
        dispatch = cursor["dispatch_refs"][cursor["child_ids"][0]]
        actual = backend.reconcile_capture(dispatch)
        fabricated = backend.read_result(actual["result_ref"])
        fabricated["uncertainty"].append("A caller substituted this statement after capture")
        forged_ref = backend._retain(fabricated, "rna_investigation_result")
        monkeypatch.setattr(backend, "reconcile_capture", lambda reference: {**actual, "result_ref": forged_ref})
        with pytest.raises(ValueError, match="actual|capture|response|binding"):
            workflow.reconcile(identity)
        assert len(backend.launches) == 1
        assert not planner.get(identity).get("investigation_evidence")
    finally:
        workflow.close()


def test_cancel_before_any_launch_preserves_zero_calls(tmp_path, monkeypatch):
    planner, plan, backend, ledger, actions, workflow = recovery_setup(tmp_path)
    identity = plan["session_id"]
    freeze = workflow._freeze_campaign

    def cancel(parent):
        frozen = freeze(parent)
        ledger.cancel_campaign(identity)
        return frozen

    monkeypatch.setattr(workflow, "_freeze_campaign", cancel)
    try:
        cursor = workflow.start(actions.create_state(identity))
        assert cursor["status"] == "cancelled"
        assert backend.launches == []
        assert actions.selector.contexts == []
        assert ledger.campaign_reservations(identity) == []
        assert ledger.get_campaign(identity)["verified_spent_tokens"] == 0
    finally:
        workflow.close()


def test_active_completion_after_cancel_charges_usage_without_parent_synthesis(tmp_path, monkeypatch):
    planner, plan, backend, ledger, actions, workflow = recovery_setup(tmp_path)
    identity = plan["session_id"]
    investigate = backend.investigate

    def late_completion(request, **kwargs):
        outcome = investigate(request, **kwargs)
        ledger.cancel_campaign(identity)
        return outcome

    monkeypatch.setattr(backend, "investigate", late_completion)
    try:
        cursor = workflow.start(actions.create_state(identity))
        assert cursor["status"] == "cancelled"
        row = child_rows(ledger, identity)[0]
        assert row["status"] == "completed"
        assert row["capture"]["usage"]["normalized_total_tokens"] == 12
        assert ledger.get_campaign(identity)["verified_spent_tokens"] == 24
        assert len(actions.selector.contexts) == 1
        assert len(backend.launches) == 1
        assert not planner.get(identity).get("candidate_reassessment")
        assert planner.get(identity)["recommendation"] == plan["recommendation"]
    finally:
        workflow.close()


def test_one_completed_one_cancelled_child_retains_partial_capture(tmp_path, monkeypatch):
    planner, plan, backend, ledger, actions, workflow = recovery_setup(tmp_path, allocation=2)
    identity = plan["session_id"]
    complete = Event()
    activate, settle = ledger.activate_model_call, ledger.settle_model_call
    first_child = []

    def ordered_activate(reservation):
        row = ledger.get_model_reservation(reservation)
        if row["purpose"] == "independent_investigation":
            if not first_child:
                first_child.append(row["child_id"])
            elif row["child_id"] != first_child[0]:
                assert complete.wait(10), "First synthetic child did not finish"
                ledger.cancel_campaign(identity)
        return activate(reservation)

    def observe_settle(reservation, **kwargs):
        receipt = settle(reservation, **kwargs)
        row = ledger.get_model_reservation(reservation)
        if row["purpose"] == "independent_investigation" and row["child_id"] == first_child[0]:
            complete.set()
        return receipt

    monkeypatch.setattr(ledger, "activate_model_call", ordered_activate)
    monkeypatch.setattr(ledger, "settle_model_call", observe_settle)
    try:
        cursor = workflow.start(actions.create_state(identity))
        assert cursor["status"] == "cancelled"
        rows = child_rows(ledger, identity)
        assert sorted(row["status"] for row in rows) == ["cancelled", "completed"]
        assert sum(row["capture"] is not None for row in rows) == 1
        assert len(backend.launches) == 1
        assert len(actions.selector.contexts) == 1
        assert not planner.get(identity).get("candidate_reassessment")
        assert planner.get(identity)["recommendation"] == plan["recommendation"]
    finally:
        complete.set()
        workflow.close()


@pytest.mark.parametrize("field", ["session_id", "invocation_id", "schema_digest", "context_digest", "request"])
def test_new_labels_or_request_bytes_cannot_reuse_completed_child_admission(tmp_path, field):
    from devbench.analysis.workflow_operations import OperationConflict
    planner, plan, backend, ledger, actions, workflow = recovery_setup(tmp_path)
    identity = plan["session_id"]
    try:
        assert workflow.start(actions.create_state(identity))["status"] == "review_required"
        retained = child_rows(ledger, identity)[0]
        changed = deepcopy(retained["request"])
        if field == "request":
            changed["request"]["budget_policy"] = "bounded_observed_tokens"
        else:
            changed[field] = "a" * 64 if field.endswith("digest") else "new-cursor-session"
        with pytest.raises((ValueError, OperationConflict), match="different|binding|identity|request|reused"):
            ledger.reserve_model_call(identity, retained["node"], retained["attempt"], changed,
                limits=retained["limits"], purpose=retained["purpose"], child_id=retained["child_id"])
        assert child_rows(ledger, identity)[0] == retained
        assert len(backend.launches) == 1
    finally:
        workflow.close()


def test_insufficient_parent_reconciliation_capacity_admits_no_children(tmp_path):
    planner, plan, backend, ledger, actions, workflow = recovery_setup(tmp_path)
    identity = plan["session_id"]
    # Reconstruct the controller after setting this explicitly frozen fixture
    # allocation; the child cannot borrow the parent's missing capacity.
    workflow.close()
    actions.investigation_policy["budget_policy"]["reconciliation"]["tokens"] = 1
    # A zero-child baseline never requests the fixture's reassessment answer.
    del actions.selector.answers[1]
    workflow = create_rna_workflow(mode="independent_investigations", actions=actions,
        checkpoint_path=tmp_path / "capacity-checkpoints.sqlite", investigation_backend=backend)
    try:
        cursor = workflow.start(actions.create_state(identity))
        assert cursor["status"] in {"review_required", "blocked", "reconciliation_required"}
        assert cursor["allocated_children"] == 0
        assert cursor["decision"]["status"] == "not_triggered"
        assert backend.launches == []
        assert child_rows(ledger, identity) == []
        calls = len(actions.selector.contexts)
        assert calls <= 2
        assert not planner.get(identity).get("candidate_reassessment")
        resumed = workflow.resume(identity)
        assert resumed["status"] in {"abstained", "blocked", "reconciliation_required"}
        assert backend.launches == []
    finally:
        workflow.close()


@pytest.mark.skipif(os.environ.get("DEVBENCH_RUN_LOCAL_FIXTURES") != "1",
                    reason="The real local numerical fixture requires explicit opt-in")
def test_accepted_charged_explanation_nomination_recovers_without_refit(tmp_path, monkeypatch):
    from tests.test_rna_independent_postfit_workflow import _case
    case = _case(tmp_path, allocation=0)
    planner, plan, backend, ledger, actions, executor = (
        case[key] for key in ("planner", "plan", "backend", "ledger", "actions", "executor"))
    identity = plan["session_id"]
    workflow = create_rna_workflow(mode="independent_investigations", actions=actions,
        checkpoint_path=tmp_path / "actual-fit-recovery.sqlite", investigation_backend=backend)
    fail_after_callback(monkeypatch, ledger, "preliminary_explanation")
    try:
        assert workflow.start(actions.create_state(identity))["status"] == "review_required"
        assert workflow.resume(identity)["status"] == "review_required"
        interrupted = workflow.resume(identity)
        assert interrupted["phase"] == "preliminary_explanation"
        assert interrupted["status"] == "reconciliation_required"
        before = deepcopy(planner.get(identity))
        calls = len(actions.selector.contexts)
        assert before["preliminary_explanation"]
        assert before["selection_history"][-1]["status"] == "accepted"
        charge = ledger.get_model_reservation(before["selection_history"][-1]["model_reservation_ref"])
        assert charge["status"] == "completed" and charge["capture"] is not None
        assert charge["capture"]["actual_calls"] == 2
        numerical = deepcopy(before["results"])
        run = planner.methods.get("run", numerical[0]["provenance"]["run_id"])
        statistics_path = planner.methods.artifact_path(run["outputs"]["statistics"])
        statistics_bytes = statistics_path.read_bytes()
        statistics = json.loads(statistics_bytes)
        assert statistics["family"] == "gaussian" and statistics["normalization_method"] == "none"
        assert statistics["design"]["is_full_rank"] is True
        assert all(row["p_value"] <= row["q_value"] for row in statistics["genes"].values())
        recovered = workflow.reconcile(identity)
        assert recovered["status"] == "ready"
        assert ledger.get(identity, "preliminary_explanation", 0)["status"] == "completed"
        assert planner.get(identity) == before
        assert len(actions.selector.contexts) == calls
        assert planner.get(identity)["results"] == numerical
        assert statistics_path.read_bytes() == statistics_bytes
        assert executor.executions == 1 and executor.calls == ["fit_temporal_trajectory"]
        assert backend.child_contexts == []
    finally:
        workflow.close()


def test_malformed_actual_child_output_is_retained_without_retry_or_success(tmp_path, monkeypatch):
    import tests.test_langgraph_investigations as fixtures
    from tests.test_rna_investigation_parent_integration import CapturedFixtureSelector
    planner, plan, backend, ledger, actions, workflow = recovery_setup(tmp_path)
    identity = plan["session_id"]

    class MalformedChildSelector(CapturedFixtureSelector):
        def select(self, context, output, **kwargs):
            self.answers = [{"malformed": "This is an actual captured invalid child response"}]
            return super().select(context, output, **kwargs)

    monkeypatch.setattr(fixtures, "CapturedFixtureSelector", MalformedChildSelector)
    try:
        cursor = workflow.start(actions.create_state(identity))
        assert cursor["status"] == "reconciliation_required"
        row = child_rows(ledger, identity)[0]
        assert row["status"] == "completed"
        assert row["capture"]["usage"]["normalized_total_tokens"] == 12
        raw = planner.methods.root / "offline-fixture-children" / row["child_id"] / "capture" / "response.json"
        assert json.loads(raw.read_bytes())["malformed"]
        assert not cursor["result_refs"]
        assert not planner.get(identity).get("investigation_evidence")
        assert workflow.resume(identity)["status"] == "reconciliation_required"
        assert len(backend.launches) == 1
        # Transport completion and a valid proposal are different facts.
        # Known actual usage is charged even when proposal validation fails.
        campaign = ledger.get_campaign(identity)
        assert campaign["verified_spent_tokens"] == 24
        assert campaign["outstanding_reserved_tokens"] == 0
    finally:
        workflow.close()


@pytest.mark.parametrize("stage,allocation", [("pre_fit_methods", 0), ("pre_fit_methods", 1), ("post_fit_explanation", 0)])
def test_terminal_checkpoint_loss_reconstructs_with_fresh_runtime_objects(tmp_path, stage, allocation):
    planner, plan, backend, ledger, actions, workflow = recovery_setup(tmp_path, allocation, stage=stage)
    identity = plan["session_id"]
    assert workflow.start(actions.create_state(identity))["status"] == "review_required"
    terminal = workflow.resume(identity)
    assert terminal["status"] == "abstained"
    actual = deepcopy(planner.get(identity))
    charges = deepcopy(ledger.campaign_reservations(identity))
    checkpoint = workflow.checkpoint_path
    workflow.close()
    with sqlite3.connect(checkpoint) as db:
        db.execute("DELETE FROM checkpoints WHERE thread_id=?", (identity,))
        db.execute("DELETE FROM writes WHERE thread_id=?", (identity,))
    fresh_planner, fresh_ledger, fresh_selector, fresh_backend, fresh_actions = fresh_recovery_objects(planner, ledger, actions)
    assert fresh_planner._model_campaigns == {}
    assert not fresh_actions.allow_model_execution and fresh_actions.investigation_authority is None
    with create_rna_workflow(mode="independent_investigations", actions=fresh_actions,
            checkpoint_path=checkpoint, investigation_backend=fresh_backend) as restored:
        recovered = restored.reconcile(identity)
        assert recovered["status"] == "abstained"
        assert recovered["expected_plan_hash"] == digest(actual)
        assert recovered["result_refs"] == terminal["result_refs"]
        assert recovered["reconciliation_ref"] == terminal["reconciliation_ref"]
        assert restored.resume(identity)["status"] == "abstained"
    assert fresh_planner.get(identity) == actual
    assert fresh_ledger.campaign_reservations(identity) == charges
    assert fresh_selector.contexts == [] and fresh_backend.launches == []


@pytest.mark.parametrize("node", ["attach", "reassess"])
def test_uncertain_parent_checkpoint_loss_reconstructs_with_no_saved_authority(tmp_path, monkeypatch, node):
    planner, plan, backend, ledger, actions, workflow = recovery_setup(tmp_path)
    identity = plan["session_id"]
    fail_after_callback(monkeypatch, ledger, node)
    interrupted = workflow.start(actions.create_state(identity))
    assert interrupted["phase"] == node and interrupted["status"] == "reconciliation_required"
    actual = deepcopy(planner.get(identity))
    charges = deepcopy(ledger.campaign_reservations(identity))
    checkpoint = workflow.checkpoint_path
    workflow.close()
    with sqlite3.connect(checkpoint) as db:
        db.execute("DELETE FROM checkpoints WHERE thread_id=?", (identity,))
        db.execute("DELETE FROM writes WHERE thread_id=?", (identity,))
    fresh_planner, fresh_ledger, selector, fresh_backend, fresh_actions = fresh_recovery_objects(planner, ledger, actions)
    assert not fresh_actions.allow_model_execution and fresh_actions.investigation_authority is None
    with create_rna_workflow(mode="independent_investigations", actions=fresh_actions,
            checkpoint_path=checkpoint, investigation_backend=fresh_backend) as restored:
        recovered = restored.reconcile(identity)
        assert recovered["status"] == "ready"
        assert recovered["phase"] == node
        assert recovered["expected_plan_hash"] == digest(actual)
    assert fresh_planner.get(identity) == actual
    assert fresh_ledger.get(identity, node, 0)["status"] == "completed"
    assert fresh_ledger.campaign_reservations(identity) == charges
    assert selector.contexts == [] and fresh_backend.launches == []
