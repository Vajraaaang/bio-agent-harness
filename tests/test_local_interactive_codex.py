"""Single-operator campaign controls and synthetic CLI transport."""

import json
import os
from pathlib import Path

import pytest

from devbench.analysis.checkpoints import digest
from devbench.analysis.episode_agent import AdaptiveController
from devbench.analysis.episode_codex import CodexActionSelector
from devbench.analysis.evidence_access import AccessPolicy, EvidenceBroker
from devbench.analysis.rna_investigation_backend import AuditedInvestigationBackend
from devbench.analysis.rna_local_interactive import attach_local_interactive_codex
from devbench.engine.workflow_operations import (
    LEDGER_NAME,
    OperationBlocked,
    WorkflowOperationLedger,
)
from devbench.platform.method_planner import ScientificMethodPlanner
from devbench.platform.workflow_actions import WorkflowActions, create_rna_workflow
from devharness_core.watchdog import HarnessResourceInterrupt
from tests.test_rna_investigation_backend import (
    native_backend,
    protected_fake_cli_backend,
)
from tests.test_scientific_authority_migration import _binding
from tests.test_two_agent_investigation_v24 import decision


def _plan(planner, classification="synthetic"):
    return planner.create(
        {
            "scientific_objective": "Exercise single-operator transport",
            "computational_task": "Test synthetic controller decisions",
        },
        {"observed": {}, "inferred": {}, "unknowns": [], "artifact_ids": []},
        actor="fixture",
        data_classification=classification,
    )


def _attach(planner, ledger, plan, selector, **options):
    return attach_local_interactive_codex(
        planner,
        ledger,
        plan,
        selector,
        **{"data_scope": "synthetic", "allow_model_execution": True, "actor": "fixture"}
        | options,
    )


def _selector_runtime(tmp_path):
    native = native_backend(tmp_path)
    planner = ScientificMethodPlanner(native.methods)
    return (
        planner,
        WorkflowOperationLedger(native.methods.root / LEDGER_NAME),
        native.selector,
    )


def test_local_campaign_needs_exact_current_operator_scope_and_plan(tmp_path):
    planner, ledger, selector = _selector_runtime(tmp_path)
    private = _plan(planner, "private")
    with pytest.raises(PermissionError, match="scope"):
        _attach(planner, ledger, private, selector, data_scope="private")
    public = _plan(planner, "public")
    with pytest.raises(PermissionError, match="scope"):
        _attach(planner, ledger, public, selector)
    with pytest.raises(PermissionError, match="authorization"):
        _attach(
            planner,
            ledger,
            public,
            selector,
            data_scope="public",
            allow_model_execution=False,
        )
    with pytest.raises(ValueError, match="exact current plan"):
        _attach(
            planner,
            ledger,
            {**public, "updated_at": "changed"},
            selector,
            data_scope="public",
        )


def test_local_campaign_keeps_unqualified_capability_and_exact_policy(tmp_path):
    planner, ledger, selector = _selector_runtime(tmp_path)
    plan = _plan(planner)
    invoker = _attach(planner, ledger, plan, selector)
    current, _, backend, campaign = invoker._runtime()
    capability = campaign["capability"]
    assert invoker.planner is planner and invoker.default_selector() is selector
    assert campaign["policy"]["kind"] == "local_interactive_codex"
    assert campaign["policy"]["max_children"] == 0
    assert campaign["hard_token_guarantee"] is False
    assert capability["backend"] == "local_interactive_codex"
    assert capability["qualification"] == "unqualified"
    assert capability["parallel_boundary_qualified"] is False
    assert capability["identity"] == "single_operator_interactive"
    for incompatible in (
        "hard_total_tokens",
        "bounded_observed_tokens",
        "generation_jobs",
    ):
        with pytest.raises(PermissionError, match="blocked_backend_capability"):
            backend.require_admission(incompatible)
    native = AuditedInvestigationBackend(planner.methods, selector)
    with pytest.raises(ValueError, match="Unknown investigation budget policy"):
        native.require_admission("local_interactive_codex")

    def open_test(campaign_id, *, policy=None, capability_override=None, runtime=None):
        return ledger.open_campaign(
            campaign_id,
            session_id=plan["session_id"],
            policy=campaign["policy"] if policy is None else policy,
            capability=capability
            if capability_override is None
            else capability_override,
            identity=campaign["identity"],
            backend=runtime,
        )

    with pytest.raises(ValueError, match="children"):
        open_test(
            "parallel",
            policy={**campaign["policy"], "max_children": 1},
            runtime=backend,
        )
    with pytest.raises(PermissionError, match="current"):
        open_test(
            "native-on-local", capability_override=native.capabilities(), runtime=native
        )
    sibling = _attach(planner, ledger, _plan(planner), selector)._runtime()[3]
    assert sibling["capability_digest"] == campaign["capability_digest"]
    reopened = WorkflowOperationLedger(ledger.path)
    with pytest.raises(
        OperationBlocked, match="current_local_interactive_authority_unavailable"
    ):
        with reopened.publication_guard(campaign["campaign_id"]):
            pytest.fail("Stored capability JSON restored local authority")
    resumed = _attach(
        ScientificMethodPlanner(planner.methods), reopened, current, selector
    )
    assert resumed._runtime()[3]["identity_digest"] == campaign["identity_digest"]
    with reopened.publication_guard(campaign["campaign_id"]):
        pass
    with pytest.raises(
        OperationBlocked, match="current_local_interactive_authority_unavailable"
    ):
        with reopened.publication_guard(sibling["campaign_id"]):
            pytest.fail("Another campaign inherited the current operator authorization")


def test_local_campaign_rejects_challenger_substitutes_and_private_structure(tmp_path):
    planner, ledger, selector = _selector_runtime(tmp_path)
    plan = _plan(planner)
    invoker = _attach(planner, ledger, plan, selector)
    context = {"decision_kind": "adaptive_controller", "termination_required": False}
    output = planner.methods.root / "role-output"

    def invoke(
        candidate, *, role="controller", purpose="adaptive_controller", payload=context
    ):
        return invoker.invoke(
            candidate,
            payload,
            output,
            role=role,
            purpose=purpose,
            node="role",
            attempt=0,
        )

    with pytest.raises(PermissionError, match="single-operator"):
        invoke(selector, role="challenger", purpose="scientific_review")
    copied = CodexActionSelector(
        requested_model=selector.requested_model,
        workspace_parent=selector.workspace_parent,
        private_root=selector.private_root,
        executable=selector.executable,
        memory_mb=selector.memory_mb,
    )
    with pytest.raises(PermissionError, match="single-operator"):
        invoke(copied)
    with pytest.raises(ValueError, match="evaluator|Evaluator"):
        invoke(selector, payload={**context, "reference_answer": {"rows": [1, 2]}})
    _, _, _, campaign = invoker._runtime()
    assert ledger.campaign_reservations(campaign["campaign_id"]) == []
    with pytest.raises(OperationBlocked, match="child_budget_exhausted"):
        ledger.reserve_investigation_fork(
            campaign["campaign_id"],
            fork_id="fork",
            stage="pre_fit_methods",
            child_ids=["child"],
            identity={},
        )


def _reserve(
    ledger, backend, campaign, identity, *, purpose="adaptive_controller", child_id=None
):
    context = {"decision_kind": "adaptive_controller", "termination_required": False}
    request = backend.parent_selection_binding(
        backend.selector,
        context,
        parent_session_id=campaign["session_id"],
        session_id=identity,
        invocation_id=identity,
    )
    return ledger.reserve_model_call(
        campaign["campaign_id"],
        identity,
        0,
        {**request, "purpose": purpose, "role": "controller"},
        limits=campaign["identity"]["parent_call_limits"]["default"],
        purpose=purpose,
        child_id=child_id,
    )


def test_local_campaign_serializes_reservations_and_stops_on_ambiguous_usage(tmp_path):
    planner, ledger, selector = _selector_runtime(tmp_path)
    left = _attach(planner, ledger, _plan(planner), selector)
    right = _attach(planner, ledger, _plan(planner), selector)
    _, _, left_backend, left_campaign = left._runtime()
    _, _, right_backend, right_campaign = right._runtime()
    with pytest.raises(OperationBlocked, match="child_budget_exhausted"):
        _reserve(ledger, left_backend, left_campaign, "child", child_id="child")
    with pytest.raises(OperationBlocked, match="local_interactive_role_not_admitted"):
        _reserve(
            ledger, left_backend, left_campaign, "review", purpose="scientific_review"
        )
    first = _reserve(ledger, left_backend, left_campaign, "first")
    with pytest.raises(
        OperationBlocked, match="local_interactive_concurrency_exhausted"
    ):
        _reserve(ledger, right_backend, right_campaign, "second")
    ledger.activate_model_call(first)
    ledger.mark_model_call_uncertain(first, error_type="FixtureLostCapture")
    with pytest.raises(
        OperationBlocked, match="local_interactive_requires_reconciliation"
    ):
        _reserve(ledger, right_backend, right_campaign, "second")
    assert ledger.get_model_reservation(first)["status"] == "uncertain"


def test_local_watchdog_interruption_is_a_reconciliation_hold(tmp_path, monkeypatch):
    planner, ledger, selector = _selector_runtime(tmp_path)
    invoker = _attach(planner, ledger, _plan(planner), selector)
    launches = []

    def interrupt(*args, _before_start, **kwargs):
        _before_start()
        launches.append(True)
        raise HarnessResourceInterrupt("Worker memory growth limit exceeded")

    monkeypatch.setattr(selector, "select", interrupt)

    def choose():
        return invoker.invoke(
            selector,
            {"decision_kind": "adaptive_controller", "termination_required": False},
            planner.methods.root / "role-output",
            role="controller",
            purpose="adaptive_controller",
            node="role",
            attempt=0,
        )

    with pytest.raises(OperationBlocked, match="model_call_requires_reconciliation"):
        choose()
    campaign = invoker._runtime()[3]
    [row] = ledger.campaign_reservations(campaign["campaign_id"])
    assert row["status"] == "uncertain"
    assert row["error_type"] == "HarnessResourceInterrupt"
    with pytest.raises(OperationBlocked):
        choose()
    assert len(launches) == 1


@pytest.mark.skipif(
    os.environ.get("DEVBENCH_RUN_LOCAL_FIXTURES") != "1",
    reason="Local protected FakeCLI capture requires opt-in",
)
def test_local_campaign_reconciles_real_fake_cli_capture_without_redispatch(
    workspace_tmp_path,
    monkeypatch,
):
    action = decision()
    fixture = protected_fake_cli_backend(
        workspace_tmp_path,
        monkeypatch,
        f"action={action!r}\n"
        "schema={}\n"
        "if '--output-schema' in args:\n"
        " schema=json.loads(Path(args[args.index('--output-schema')+1]).read_text())\n"
        "answer=json.dumps({'action':action} "
        "if 'action' in schema.get('properties',{}) "
        "else action)",
    )
    planner, ledger, selector = (
        ScientificMethodPlanner(fixture.methods),
        fixture.ledger,
        fixture.selector,
    )
    invoker = _attach(planner, ledger, _plan(planner), selector)
    destination = Path(fixture.methods.root) / "local-test"
    context = {"decision_kind": "adaptive_controller", "termination_required": True}

    def choose():
        return invoker.invoke(
            selector,
            context,
            destination,
            role="controller",
            purpose="adaptive_termination",
            node="termination",
            attempt=0,
        )

    first = choose()
    [evidence_file] = list(destination.rglob("evidence.json"))
    evidence = json.loads(evidence_file.read_text())
    monitor = evidence["supervision"]["monitor"]
    assert monitor["growth_window_seconds"] == 0.5
    assert monitor["growth_limit_MiB_per_second"] == 1500
    child_monitor = evidence["selection"]["process_watchdog"]
    assert child_monitor["growth_window_seconds"] == monitor["growth_window_seconds"]
    assert child_monitor["growth_limit_MiB_per_second"] == 1500
    assert child_monitor["RSS_limit_MiB"] == monitor["RSS_limit_MiB"]
    _, _, backend, campaign = invoker._runtime()
    [row] = ledger.campaign_reservations(campaign["campaign_id"])
    assert row["status"] == "completed"
    assert row["capture"]["actual_calls"] == 1
    assert row["capture"]["usage"]["normalized_total_tokens"] == 130
    assert first.action == {"action": action}
    assert first.evidence["backend"] == "local_interactive_codex"
    assert first.evidence["protocol_scope"] == "single_operator_interactive"
    assert digest(first.action) == digest(
        backend._capture_action(row["capture"]["capture_ref"])
    )
    monkeypatch.setattr(
        selector, "select", lambda *a, **kw: pytest.fail("Selection was relaunched")
    )
    assert choose() == first


@pytest.mark.wal
@pytest.mark.skipif(
    os.environ.get("DEVBENCH_RUN_LOCAL_FIXTURES") != "1",
    reason="Local protected FakeCLI capture requires opt-in",
)
def test_local_controller_applies_a_brokered_synthetic_action(
    workspace_tmp_path,
    monkeypatch,
):
    sweep = decision(
        "declare_sweep",
        sweep_id="local-sweep",
        members=[{"step_id": "omitted", "omission_reason": "outside_fixture_scope"}],
    )
    stop = decision()
    fake = protected_fake_cli_backend(
        workspace_tmp_path,
        monkeypatch,
        f"action={stop!r} if context['termination_required'] else {sweep!r}\n"
        "answer=json.dumps({'action':action})",
        scientific=True,
    )
    ledger, planner = fake.ledger, ScientificMethodPlanner(fake.methods)
    plan = _plan(planner)
    session = plan["session_id"]
    ledger.scientific.commit(session, expected_revision=0, record=_binding())
    invoker = _attach(planner, ledger, plan, fake.selector)
    workflow = planner.adaptive_workflow(session, ledger)
    broker = EvidenceBroker(ledger.scientific)
    access = AccessPolicy(
        "fixture",
        1,
        allow_outcomes=True,
        allow_statistics=True,
        diagnostic_policy_id=digest("Synthetic single-operator access"),
    )
    broker.register_policy(access)
    broker.register_context(
        "fixture",
        principal_id="fixture",
        lineage_id=session,
        role="controller",
        phase="analysis",
        policy_sha256=access.sha256(),
    )
    controller = AdaptiveController(
        invoker,
        broker=broker,
        context_id="fixture",
        action_catalog={},
        max_decisions=2,
    )
    with pytest.raises(PermissionError, match="bound selector"):
        AdaptiveController(
            invoker,
            broker=broker,
            context_id="fixture",
            action_catalog={},
            challenger=fake.selector,
        )
    actions = WorkflowActions(
        planner,
        ledger,
        actor="fixture",
        selector=None,
        controller=controller,
    )
    graph = create_rna_workflow(mode="adaptive/1", actions=actions, adaptive=workflow)
    result = graph.advance(max_steps=2)
    state = workflow.controller_state()
    assert result["controller_outcome"]["reason"] == "empty_ready_frontier"
    assert result["controller_outcome"]["publication_granted"] is False
    assert state["binding"]["scope"] == "local_interactive_codex"
    assert state["binding"]["qualification_status"] == "not_established"
    assert state["binding"]["challenger_sha256"] is None
    assert state["binding"]["max_review_cycles"] == 0
    assert [row["decision"]["kind"] for row in state["decisions"]] == [
        "declare_sweep",
        "stop",
    ]
    for row in state["decisions"]:
        assert row["evidence"]["protocol_scope"] == "single_operator_interactive"
        assert row["evidence"]["controller_disclosure_reservation_id"]
        assert row["evidence"]["model_sessions_attempted"] == 1
