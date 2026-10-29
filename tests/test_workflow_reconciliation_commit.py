"""Reconciliation ledger commits survive a crash before the graph checkpoint."""
from tests.support.workflow_science_fixtures import RecordingScientificRuntime
from copy import deepcopy

import pytest

from devbench.analysis.langgraph_workflow import SerialRNAWorkflow
from devbench.analysis.workflow_actions import WorkflowActions
from tests.test_langgraph_workflow import executable_rna, ledger


def interrupted_reconciliation(tmp_path, monkeypatch, node):
    planner, plan, _, branch, method, specs, selector, executor = executable_rna(tmp_path)
    name = {"generate": "generate_candidates", "declare": "declare_trial",
            "run": "run_trial", "recommend": "recommend"}[node]
    original = getattr(planner, name)

    def lost_result(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("Lost API result after successful completion")

    monkeypatch.setattr(planner, name, lost_result)
    actions = WorkflowActions(planner, ledger(tmp_path), actor="scientist", selector=selector,
        version_ids=[method["version_id"]], declarations=[{"draft_id": specs[0]["id"], "branch_id": branch["branch_id"]}],
        allow_data_execution=True, executor=executor, scientific_workflow=RecordingScientificRuntime())
    checkpoint = tmp_path / "checkpoint.sqlite"
    with SerialRNAWorkflow(actions, checkpoint) as workflow:
        result = workflow.start(actions.create_state(plan["session_id"]))
        while "__interrupt__" in result:
            result = workflow.resume(plan["session_id"])
        assert result["phase"] == node and result["status"] == "reconciliation_required"

        def crash_before_checkpoint(*args, **kwargs):
            raise RuntimeError("Crash before reconciliation checkpoint")

        monkeypatch.setattr(workflow.graph, "update_state", crash_before_checkpoint)
        with pytest.raises(RuntimeError, match="before reconciliation checkpoint"):
            workflow.reconcile(plan["session_id"])
    assert actions.ledger.get(plan["session_id"], node, 0)["status"] == "completed"
    return planner, actions, checkpoint, plan["session_id"], selector, executor


@pytest.mark.parametrize("node", ["generate", "declare", "run", "recommend"])
def test_reconciliation_commit_replays_without_redispatch(tmp_path, monkeypatch, node):
    planner, actions, checkpoint, workflow_id, selector, executor = interrupted_reconciliation(tmp_path, monkeypatch, node)
    calls = len(selector.contexts), executor.calls
    with SerialRNAWorkflow(actions, checkpoint) as workflow:
        result = workflow.reconcile(workflow_id)
        assert (len(selector.contexts), executor.calls) == calls
        assert result["status"] in {"ready", "completed", "abstained"}
        result = workflow.resume(workflow_id)
        while "__interrupt__" in result:
            result = workflow.resume(workflow_id)
    assert result["status"] == "abstained"
    assert len(selector.contexts) == 4 and executor.calls == 1


@pytest.mark.parametrize("changed", ["binding", "artifacts", "result"])
def test_reconciliation_commit_rejects_changed_retained_completion(tmp_path, monkeypatch, changed):
    planner, actions, checkpoint, workflow_id, selector, executor = interrupted_reconciliation(tmp_path, monkeypatch, "generate")
    original = actions.ledger.get

    def changed_completion(identity, node, attempt):
        record = original(identity, node, attempt)
        if node == "generate" and record is not None:
            record = deepcopy(record)
            if changed == "binding":
                record["request"]["binding_digest"] = "0" * 64
            elif changed == "artifacts":
                record["request"]["artifact_refs"] = []
            else:
                record["result"]["after_planner_hash"] = "0" * 64
        return record

    monkeypatch.setattr(actions.ledger, "get", changed_completion)
    calls = len(selector.contexts), executor.calls
    with SerialRNAWorkflow(actions, checkpoint) as workflow:
        with pytest.raises(ValueError, match="binding|retained|completion|cursor|evidence"):
            workflow.reconcile(workflow_id)
    assert (len(selector.contexts), executor.calls) == calls
