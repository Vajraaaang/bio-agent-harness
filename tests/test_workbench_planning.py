"""Offline Codex-to-workbench proposal connection, not a live comparison."""
import json
from pathlib import Path
import subprocess
import sys

import pytest

from tests.test_analysis_episodes import workspace
from tests.test_episode_planning import Selector
from tests.test_workbench_proposals import fixture, reviewed, begin


DECISION = {"kind": "propose", "workflow": "scrna_qc_integration_annotation/1",
            "rationale": "Inspect quality and structure with the supplied fixed workflow."}


def dataset(root):
    source, parameters, declarations = fixture(root)
    return {"transport": "backed_workbench", "source": str(source), "parameters": parameters,
            "declarations": declarations, "run_timeout_seconds": 180}


def propose(root, data, decision=None, **kwargs):
    from devbench.analysis.episode_planning import propose_analysis
    selector = Selector(decision or DECISION)
    result = propose_analysis(root / "planning", question="Check cell quality, clusters and available annotation",
        dataset=data, selector=selector, allow_data_read=True, **kwargs)
    return result, selector


def test_backed_request_uses_common_planner_and_returns_reviewable_workbench_proposal(workspace):
    from devbench.analysis.workbench_proposals import execute_reviewed_workbench
    from devharness_core.provenance import verify_execution
    data = dataset(workspace)
    result, selector = propose(workspace, data)
    assert result["status"] == "proposal_ready_for_review", result
    context = selector.calls[0]
    assert context["workflow_family"] == "backed_workbench"
    assert context["observed"]["cells"] == 80 and context["observed"]["genes"] == 60
    assert "condition_levels" not in context["observed"]
    assert str(workspace) not in json.dumps(context)
    assert "source_sha256" not in json.dumps(context)
    assert "seed" not in context["fixed_settings"]
    assert not list((workspace / "planning").rglob("*.h5ad"))
    proposal = result["proposal_receipt"]
    record = json.loads((workspace / "planning/proposal/proposal.json").read_text())
    assert record["format"] == "devbench.workbench-proposal/1"
    assert record["authoring"]["root_hash"] == result["authoring_receipt"]["root_hash"]
    assert record["configuration"]["parameters"] == data["parameters"]
    review = reviewed(workspace, proposal)
    begin(workspace, proposal, review)
    run = execute_reviewed_workbench(workspace / "bundle", workspace / "execution", allow_data_execution=True)
    assert run["status"] == "completed_review_passed", run
    assert verify_execution(result["receipt"]["manifest_path"], result["receipt"]["root_hash"])["valid"]


def test_missing_capture_evidence_allows_information_request_without_a_proposal(workspace):
    data = dataset(workspace)
    data["declarations"]["capture_units_confirmed"] = False
    result, selector = propose(workspace, data, {"kind": "request_information", "message": "Confirm capture-library mapping."})
    assert result["status"] == "awaiting_information"
    assert selector.calls[0]["allowed_workflows"] == []
    assert result["proposal_receipt"] is None


@pytest.mark.parametrize("addition", [{"reference_condition": "A"}, {"parameters": {"min_genes": 1}},
    {"memory_mb": 100000}, {"workflow": "bulk_mean_contrast"}])
def test_workbench_actor_cannot_rewrite_scope_settings_or_limits(workspace, addition):
    result, _ = propose(workspace, dataset(workspace), {**DECISION, **addition})
    assert result["status"] == "invalid_proposal" and result["proposal_receipt"] is None


def test_a_refusal_is_not_turned_into_automatic_workbench_execution(workspace):
    result, _ = propose(workspace, dataset(workspace), {"kind": "refuse", "reason": "This recipe does not answer the causal question."})
    assert result["status"] == "refusal"
    assert result["refusal_correctness"] is None
    assert result["analysis_executed"] is False


def test_h5ad_transport_does_not_silently_use_table_episode_limits(workspace):
    from devbench.analysis.episodes import EpisodeLimits
    with pytest.raises(ValueError, match="workbench|Workbench"):
        propose(workspace, dataset(workspace), episode_limits=EpisodeLimits())


def test_review_checks_the_agent_authoring_origin_again(workspace):
    data = dataset(workspace)
    result, _ = propose(workspace, data)
    context = workspace / "planning/context.json"
    context.write_bytes(context.read_bytes() + b"\n")
    with pytest.raises(ValueError, match="authoring.*changed|verification"):
        reviewed(workspace, result["proposal_receipt"])


def test_cli_recorded_workbench_decision_uses_existing_entrypoint(workspace):
    request = {"question": "Inspect QC and clustering", "dataset": dataset(workspace)}
    (workspace / "request.json").write_text(json.dumps(request))
    (workspace / "decision.json").write_text(json.dumps(DECISION))
    command = [sys.executable, "-m", "devbench.analysis.episode_cli", "--plan-request", str(workspace / "request.json"),
        "--plan-answer", str(workspace / "decision.json"), "--allow-data-execution", "--output", str(workspace / "planning")]
    executed = subprocess.run(command, capture_output=True, text=True, timeout=30)
    assert executed.returncode == 0, executed.stderr
    result = json.loads(executed.stdout)
    assert result["status"] == "proposal_ready_for_review"
    assert result["model_sessions_attempted"] == 0 and result["analysis_executed"] is False
