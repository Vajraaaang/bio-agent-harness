"""Exercise backend selection at the local adaptive workflow boundary."""

import pytest

from devbench.engine.exploratory_oci import ExploratoryOCI
from devbench.engine.tier_a_executor import TierAExecutor
from devbench.engine.workflow_operations import OperationBlocked
from devbench.platform.workflow_actions import WorkflowActions
from tests.test_admitted_research_inference import research_step
from tests.test_investigation_invalidation import Worker, make, step

pytestmark = pytest.mark.wal


@pytest.fixture
def configured_tier_a(monkeypatch):
    def bind(flow):
        executor = object.__new__(TierAExecutor)
        executor.ledger = flow.ledger
        monkeypatch.setattr(
            executor,
            "run",
            lambda _: pytest.fail("Tier A launch was not authorized"),
        )
        return executor

    return bind


def test_diagnostic_compatibility_executor_is_retained_with_tier_a(
    workspace_tmp_path,
    configured_tier_a,
):
    flow = make(workspace_tmp_path)
    flow.insert(step("check", purpose="checked_observation"))
    diagnostic = Worker()
    actions = WorkflowActions(
        None,
        flow.ledger,
        actor="tester",
        selector=None,
        allow_data_execution=True,
        executor=diagnostic,
        tier_a_executor=configured_tier_a(flow),
    )

    assert actions.perform_adaptive(flow, "check")["status"] == "succeeded"
    assert diagnostic.calls == ["check"]


def test_unsupported_research_operation_is_refused_before_admission(
    workspace_tmp_path,
    configured_tier_a,
    monkeypatch,
):
    flow = make(workspace_tmp_path)
    node = research_step()
    flow.insert(node)
    compatibility = Worker()
    actions = WorkflowActions(
        None,
        flow.ledger,
        actor="tester",
        selector=None,
        allow_data_execution=True,
        executor=compatibility,
        tier_a_executor=configured_tier_a(flow),
    )
    monkeypatch.setattr(
        actions,
        "prepare_research_admission",
        lambda *_: pytest.fail("Unsupported operation reached research admission"),
    )

    with pytest.raises(OperationBlocked, match="research_operation"):
        actions.perform_adaptive(flow, node.step_id)

    assert not compatibility.calls
    assert flow.ledger.scientific.attempts(flow.lineage_id) == []


def test_oci_executor_requires_an_oci_method_before_dispatch(
    workspace_tmp_path,
    monkeypatch,
):
    flow = make(workspace_tmp_path)
    flow.insert(step("check", purpose="checked_observation"))
    oci = object.__new__(ExploratoryOCI)
    oci.ledger = flow.ledger
    actions = WorkflowActions(
        None,
        flow.ledger,
        actor="tester",
        selector=None,
        allow_data_execution=True,
        executor=oci,
    )
    monkeypatch.setattr(
        flow,
        "dispatch",
        lambda *args, **kwargs: pytest.fail("Incompatible dispatch"),
    )

    with pytest.raises(PermissionError, match="OCI.*method"):
        actions.perform_adaptive(flow, "check")

    assert flow.ledger.scientific.attempts(flow.lineage_id) == []


@pytest.mark.parametrize("executor_slot", ["executor", "tier_a_executor"])
def test_tier_a_does_not_accept_a_bulk_step_with_exploratory_purpose(
    workspace_tmp_path,
    configured_tier_a,
    executor_slot,
):
    from devbench.core.contrast_design import TIER_A_BULK_OPERATION

    flow = make(workspace_tmp_path)
    node = step("check")
    body = node.model_dump()
    body["operation_id"] = TIER_A_BULK_OPERATION
    body["profile"]["operations"][0]["operation_id"] = TIER_A_BULK_OPERATION
    flow.insert(type(node).model_validate(body))
    actions = WorkflowActions(
        None,
        flow.ledger,
        actor="tester",
        selector=None,
        allow_data_execution=True,
        **{executor_slot: configured_tier_a(flow)},
    )

    with pytest.raises(PermissionError, match="registered step executor"):
        actions.perform_adaptive(flow, node.step_id)

    assert flow.ledger.scientific.attempts(flow.lineage_id) == []
