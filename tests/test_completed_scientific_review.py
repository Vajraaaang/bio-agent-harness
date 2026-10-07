"""Recorded analyst -> real analysis -> joined scientific-review handoffs."""
import inspect
import json
from pathlib import Path
import subprocess
import sys
import tempfile

import pytest

from tests.test_analysis_episodes import run_contrast
from tests.test_episode_planning import Selector as Analyst, dataset
from tests.test_scientific_review import research
from tests.test_scientific_review_agent import Selector as Reviewer


def api():
    from devbench.analysis.scientific_review import prepare_packet
    assert "proposal_packet_manifest" in inspect.signature(prepare_packet).parameters, \
        "Completed-result packets cannot carry their verified proposal-time evidence"
    return prepare_packet


@pytest.fixture
def workspace():
    parent = Path(__file__).resolve().parents[1] / "reports/completed-scientific-review/2026-09-22/workspaces"
    parent.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix="handoff-", dir=parent))


@pytest.mark.parametrize("route", ["bulk_continuous", "bulk_counts", "cellular_counts", "backed_scrna"])
def test_completed_analysis_reaches_reviewer_with_bound_proposal_evidence(workspace, route):
    from devbench.analysis.episode_planning import propose_analysis
    from devbench.analysis.scientific_review import verify_packet, verify_scientific_review
    from devbench.analysis.scientific_review_agent import request_review
    prepare = api()
    cellular, backed = route == "cellular_counts", route == "backed_scrna"
    if backed:
        from tests.test_workbench_planning import dataset as backed_dataset, DECISION
        declaration, analyst = backed_dataset(workspace), Analyst(DECISION)
    else:
        declaration = dataset(workspace, cellular=cellular, continuous=route == "bulk_continuous")
        analyst = Analyst()
        if cellular:
            analyst.decision["workflow"] = "cellular_state_contrast"
    planned = propose_analysis(workspace / "planning", question="Inspect the supported biological question",
        dataset=declaration, selector=analyst, allow_data_read=True)
    assert planned["status"] == "proposal_ready_for_review", planned
    proposal = planned["proposal_receipt"]
    prior = prepare(workspace / "proposal-packet", subject_kind="proposal",
        subject_path=proposal["manifest_path"], expected_root=proposal["root_hash"],
        research=research(), allow_evidence_read=True)
    if backed:
        from tests.test_workbench_proposals import reviewed, begin
        from devbench.analysis.workbench_proposals import execute_reviewed_workbench
        operator = reviewed(workspace, proposal)
        begin(workspace, proposal, operator)
        execution = execute_reviewed_workbench(workspace / "bundle", workspace / "execution", allow_data_execution=True)
        assert execution["status"] == "completed_review_passed", execution
        result_path, result_root, kind = workspace / "bundle", execution["bundle_root"], "workbench"
    else:
        from tests.test_episode_proposals import reviewed
        from devbench.analysis.episode_proposals import begin_reviewed_episode
        operator = reviewed(workspace, proposal)
        episode = begin_reviewed_episode(workspace / "episode", proposal["manifest_path"], operator["manifest_path"],
            expected_proposal_root=proposal["root_hash"], expected_review_root=operator["root_hash"], allow_data_execution=True)
        observed = run_contrast(episode, cellular)
        target = observed["result"]["target_results"][0]
        estimate = next(row for row in target["payload"]["gene_statistics"] if row["estimate_status"] == "estimated")
        assert episode.step({"kind": "finish", "findings": [{"action": len(episode.actions),
            "target_id": target["target_id"], "hypothesis_id": estimate["hypothesis_id"]}]})["outcome"] == "completed"
        result_path, result_root, kind = episode.root, episode.receipt["root_hash"], "episode"
    kwargs = dict(subject_kind=kind, subject_path=result_path, expected_root=result_root,
        research=research(), allow_evidence_read=True, proposal_packet_manifest=prior["manifest_path"],
        expected_proposal_packet_root=prior["root_hash"])
    if route == "bulk_continuous":
        request_file = workspace / "packet-request.json"
        request_file.write_text(json.dumps({"operation": "prepare_packet",
            **{key: str(value) if isinstance(value, Path) else value for key, value in kwargs.items()
               if key != "allow_evidence_read"}}))
        command = [sys.executable, "-m", "devbench.analysis.episode_cli", "--evidence-request", str(request_file),
                   "--allow-evidence-read", "--output", str(workspace / "completed-packet")]
        process = subprocess.run(command, capture_output=True, text=True, timeout=30)
        (workspace / "packet-cli.json").write_text(json.dumps({"command": command, "exit_code": process.returncode,
            "stdout": process.stdout, "stderr": process.stderr}, indent=2))
        assert process.returncode == 0, process.stderr
        packet = json.loads(process.stdout)
    else:
        packet = prepare(workspace / "completed-packet", **kwargs)
    value = json.loads((workspace / "completed-packet/packet.json").read_text())
    assert value["subject"]["proposal_packet_root"] == prior["root_hash"]
    assert value["sections"]["admission"]["format"] == "devbench.proposal-admission/1"
    assert value["sections"]["admission"]["evidence_time"] == "proposal_before_execution"
    assert value["sections"]["design"]["proposal_observations"]
    selector = Reviewer()
    reviewed_result = request_review(workspace / "scientific-review-run", packet["manifest_path"],
        expected_root=packet["root_hash"], reviewer_label="recorded-fixture-reviewer", selector=selector,
        allow_evidence_read=True, allow_review=True)
    assert reviewed_result["status"] == "review_recorded", reviewed_result
    assert reviewed_result["model_sessions_attempted"] == 0
    context = selector.calls[0]
    assert context["sections"]["admission"]["format"] == "devbench.proposal-admission/1"
    assert context["sections"]["design"]["proposal_observations"]
    assert str(workspace) not in json.dumps(context)
    numerical = context["sections"]["numerical_results"]
    if backed:
        assert numerical["cells_input"] == 80
    else:
        assert numerical["findings"][0]["estimate"]["estimate_status"] == "estimated"
    review = reviewed_result["review_receipt"]
    assert verify_scientific_review(review["manifest_path"], review["root_hash"],
        packet_manifest=packet["manifest_path"], packet_root=packet["root_hash"], allow_evidence_read=True)["valid"]
    if route == "bulk_continuous":
        request_file, answer_file = workspace / "review-request.json", workspace / "review-answer.json"
        request_file.write_text(json.dumps({"packet_manifest": packet["manifest_path"], "expected_root": packet["root_hash"],
                                          "reviewer_label": "recorded-completed-result-review"}))
        answer_file.write_text(json.dumps(selector.decision))
        command = [sys.executable, "-m", "devbench.analysis.episode_cli", "--agent-review-request", str(request_file),
            "--review-answer", str(answer_file), "--allow-evidence-read", "--allow-review-record",
            "--output", str(workspace / "cli-review")]
        process = subprocess.run(command, capture_output=True, text=True, timeout=30)
        (workspace / "review-cli.json").write_text(json.dumps({"command": command, "exit_code": process.returncode,
            "stdout": process.stdout, "stderr": process.stderr}, indent=2))
        assert process.returncode == 0, process.stderr
        cli_review = json.loads(process.stdout)
        assert cli_review["status"] == "review_recorded" and cli_review["model_sessions_attempted"] == 0
    evidence = {"route": route, "workspace": str(workspace), "proposal": proposal,
        "proposal_packet": prior, "result": {"path": str(result_path), "root_hash": result_root},
        "result_packet": packet, "review_result": reviewed_result, "provider_sessions": 0,
        "scope": "recorded decisions and real small numerical workflow; not live or qualified review"}
    (workspace / "handoff-evidence.json").write_text(json.dumps(evidence, indent=2) + "\n")

    # Output must remain disjoint from both old and new sealed evidence.
    with pytest.raises(ValueError, match="disjoint"):
        prepare(workspace / "proposal-packet/forbidden-output", **kwargs)
    # A result packet is not a substitute for a proposal packet, even when
    # its own root verifies. No experiment needs to be repeated for this check.
    wrong = {**kwargs, "proposal_packet_manifest": packet["manifest_path"],
             "expected_proposal_packet_root": packet["root_hash"]}
    with pytest.raises(ValueError, match="proposal"):
        prepare(workspace / "wrong-kind", **wrong)
    from tests.test_scientific_review import packet as other_packet
    other = workspace / "other"; other.mkdir()
    foreign, _ = other_packet(other)
    wrong = {**kwargs, "proposal_packet_manifest": foreign["manifest_path"],
             "expected_proposal_packet_root": foreign["root_hash"]}
    with pytest.raises(ValueError, match="bind"):
        prepare(workspace / "wrong-proposal", **wrong)
    # Joined evidence verifies after relocation without reopening its origins.
    import shutil
    relocated = workspace / "relocated-packet"
    shutil.copytree(workspace / "completed-packet", relocated)
    assert verify_packet(relocated / "audit/manifest.json", packet["root_hash"], allow_evidence_read=True)["valid"]


@pytest.mark.parametrize("fields", [
    {"proposal_packet_manifest": "/not-read"},
    {"expected_proposal_packet_root": "a" * 64},
])
def test_partial_proposal_packet_binding_is_rejected_before_subject_reads(fields):
    prepare = api()
    with pytest.raises(ValueError, match="together"):
        prepare(None, subject_kind="episode", subject_path=None, expected_root=None,
                research=None, allow_evidence_read=True, **fields)
