"""Run the adaptive controller's software protocol through a local FakeCLI."""

import os

import pytest

from devbench.analysis.checkpoints import digest
from devbench.analysis.episode_agent import (
    AdaptiveController,
    AgentLimits,
    CampaignSelectionInvoker,
)
from devbench.analysis.evidence_access import AccessPolicy, EvidenceBroker
from devbench.platform.adaptive_workflow import AdaptiveWorkflow
from devbench.platform.method_planner import ScientificMethodPlanner
from devbench.platform.workflow_actions import (
    WorkflowActions,
    create_rna_workflow,
)
from tests.test_rna_investigation_backend import protected_fake_cli_backend
from tests.test_rna_investigation_budget import _policy
from tests.test_scientific_authority_migration import _binding
from tests.test_two_agent_investigation_v24 import decision

pytestmark = pytest.mark.skipif(
    os.environ.get("DEVBENCH_RUN_LOCAL_FIXTURES") != "1",
    reason="Local adaptive FakeCLI capture requires opt-in",
)


@pytest.mark.wal
def test_adaptive_controller_retains_one_session_per_applied_decision(
    workspace_tmp_path, monkeypatch
):
    sweep = decision(
        "declare_sweep",
        sweep_id="fixture-sweep",
        members=[{"step_id": "omitted", "omission_reason": "outside_fixture_scope"}],
    )
    stop = decision()
    answer = (
        f"action={stop!r} if context['termination_required'] else {sweep!r}\n"
        "schema={}\n"
        "if '--output-schema' in args:\n"
        " schema=json.loads(Path(args[args.index('--output-schema')+1])"
        ".read_text())\n"
        "answer=json.dumps({'action':action} if 'action' in schema.get('properties',{}) "
        "else action)"
    )
    backend = protected_fake_cli_backend(
        workspace_tmp_path, monkeypatch, answer, scientific=True,
    )
    ledger = backend.ledger
    planner = ScientificMethodPlanner(backend.methods)
    plan = planner.create(
        {"scientific_objective": "Exercise bounded adaptive selection",
         "computational_task": "Synthetic controller transport"},
        {"observed": {}, "inferred": {}, "unknowns": [], "artifact_ids": []},
        actor="fixture", data_classification="synthetic",
    )
    session = plan["session_id"]
    ledger.scientific.commit(session, expected_revision=0, record=_binding())
    capability = backend.capabilities()
    allocation = {
        "tokens": 160, "calls": 1, "seconds": 15,
        "context_bytes": 65_536, "memory_mb": 256,
    }
    protected = {"scientific_review": allocation, "adaptive_termination": allocation}
    campaign_id = "adaptive-fake-cli"
    ledger.open_campaign(
        campaign_id, session_id=session, capability=capability,
        policy=_policy(
            "bounded_observed_tokens", soft_token_target=2_000, memory_mb=256,
            reconciliation={
                "tokens": 320, "calls": 2, "seconds": 30, "context_bytes": 65_536,
            },
        ),
        identity={
            "expected_plan_hash": digest(plan),
            "source_digest": capability["source_digest"],
            "runtime_digest": capability["runtime_digest"],
            "parent_call_limits": {"default": allocation},
            "protected_parent_allocations": protected,
        },
    )
    planner.attach_model_campaign(session, ledger, campaign_id, backend,
                                  expected_plan_hash=digest(plan), actor="fixture")
    workflow = planner.adaptive_workflow(session, ledger)
    broker = EvidenceBroker(ledger.scientific)
    access = AccessPolicy("fixture", 1, allow_outcomes=True, allow_statistics=True,
                          diagnostic_policy_id=digest("Software fixture access"))
    broker.register_policy(access)
    broker.register_context(
        "fixture", principal_id="fixture", lineage_id=session,
        role="controller", phase="analysis", policy_sha256=access.sha256(),
    )
    invoker = CampaignSelectionInvoker(
        planner, session, allow_model_execution=True,
        limits=AgentLimits(max_selections=2, memory_mb=256, max_context_bytes=65_536),
    )
    controller = AdaptiveController(
        invoker, broker=broker, context_id="fixture", action_catalog={},
        max_decisions=2,
    )
    actions = WorkflowActions(
        planner, ledger, actor="fixture", selector=None, controller=controller,
    )
    graph = create_rna_workflow(mode="adaptive/1", actions=actions, adaptive=workflow)
    result = graph.advance(max_steps=2)
    outcome = result["controller_outcome"]
    assert outcome["status"] == "partial", outcome["reason"]
    assert outcome["reason"] == "empty_ready_frontier"
    assert outcome["publication_granted"] is False and result["steps_dispatched"] == 0
    reopened = AdaptiveWorkflow(ledger, session)
    decisions = reopened.controller_state()["decisions"]
    assert [row["decision"]["kind"] for row in decisions] == ["declare_sweep", "stop"]
    assert decisions[0]["result"]["members"] == {"omitted": "outside_fixture_scope"}

    rows = ledger.campaign_reservations(campaign_id)
    assert len(rows) == 2 and all(row["status"] == "completed" for row in rows)
    native_ids = set()
    for record, row in zip(decisions, rows, strict=True):
        receipt = backend.verify_capture(row["capture"]["capture_ref"])
        assert receipt == row["capture"]
        assert record["evidence"]["model_capture_ref"] == receipt["capture_ref"]
        assert receipt["actual_calls"] == len(receipt["native_session_ids"]) == 1
        assert receipt["usage"]["normalized_total_tokens"] == 130
        native_ids.update(receipt["native_session_ids"])
        capture = backend._document(
            receipt["capture_ref"], artifact_type="rna_investigation_capture",
        )
        assert {"boundary-probe.json", "selection-dispatch.json",
                "model/cli-records.jsonl"} <= capture["files"].keys()
    assert len(native_ids) == 2
    campaign = ledger.get_campaign(campaign_id)
    assert campaign["verified_calls"] == 2 and campaign["verified_spent_tokens"] == 260
