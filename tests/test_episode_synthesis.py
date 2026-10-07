"""Receipt-bound reporting over tiny local fixtures; no model participants."""
import json
from pathlib import Path

import pytest

from tests.test_analysis_episodes import create, inputs, run_contrast, workspace


def verify_report(episode):
    from devbench.analysis.episode_synthesis import verify_synthesis
    return verify_synthesis(episode.root, episode.receipt["root_hash"], allow_evidence_read=True)


def reference(observation, action=4):
    target = observation["result"]["target_results"][0]
    row = target["payload"]["gene_statistics"][0]
    return {"action": action, "target_id": target["target_id"], "hypothesis_id": row["hypothesis_id"]}


@pytest.mark.parametrize("cellular,continuous", [(False, True), (False, False), (True, False)])
def test_finish_resolves_finding_from_verified_result_and_replays(workspace, cellular, continuous):
    episode, _ = create(workspace, cellular=cellular, continuous=continuous)
    observed = run_contrast(episode, cellular)
    selected = reference(observed)
    final = episode.step({"kind": "finish", "findings": [selected]})
    assert final["outcome"] == "completed", final
    report = json.loads((episode.root / "synthesis.json").read_text())
    target = observed["result"]["target_results"][0]
    row = target["payload"]["gene_statistics"][0]
    assert report["evidence"]["status"] == "verified"
    assert report["scientific_correctness"] is None
    assert report["findings"][0]["estimate"] == row
    assert report["findings"][0]["source"]["action"] == 4
    assert report["findings"][0]["source"]["receipt_id"] == observed["receipt_id"]
    summary = report["targets"][0]
    assert summary["computed_contrast"] == target["payload"]["computed_contrast"]
    assert summary["limitation_codes"] == target["limitation_codes"]
    assert summary["hypothesis_family"]["attempted_count"] == len(target["payload"]["gene_statistics"])
    assert summary["hypothesis_family"]["tested_count"] == len(target["payload"]["multiplicity_scope"])
    assert summary["selection_conditional_inference"] == target["payload"]["selection_conditional_inference"]
    text = (episode.root / "summary.md").read_text()
    assert "B minus A" in text and "95%" in text and "q=" in text and "p=" in text
    assert "Scientific correctness has not been scored" in text
    assert verify_report(episode)["evidence_verified"] is True
    if continuous:
        import shutil
        from devbench.analysis.episodes import replay_episode
        from devbench.analysis.episode_synthesis import verify_synthesis
        relocated = workspace / "relocated"
        shutil.copytree(episode.root, relocated)
        assert verify_synthesis(relocated, episode.receipt["root_hash"], allow_evidence_read=True)["valid"]
        replay = replay_episode(relocated, episode.receipt["root_hash"], workspace / "replay", allow_data_execution=True)
        assert replay.outcome == "completed"
        repeated = json.loads((replay.root / "synthesis.json").read_text())
        assert repeated["findings"][0]["estimate"] == report["findings"][0]["estimate"]
        assert repeated["findings"][0]["source"]["receipt_id"] != observed["receipt_id"]
        assert verify_report(replay)["valid"]


def test_request_wording_is_escaped_and_separate_from_computed_units(workspace):
    from devbench.analysis.episodes import AnalysisEpisode
    task, paths = inputs(workspace)
    task["questions"][0].update(contrast="A minus B <script>alert(1)</script>", units="absolute molecules")
    episode = AnalysisEpisode.create(workspace / "episode", question="[Open](https://invalid.example) <img src=x>",
        assay="bulk", public_task=task, inputs=paths, allow_data_execution=True)
    observed = run_contrast(episode)
    episode.step({"kind": "finish", "findings": [reference(observed)]})
    report = json.loads((episode.root / "synthesis.json").read_text())
    target = report["targets"][0]
    assert target["requested"]["units"] == "absolute molecules"
    assert target["computed_contrast"]["effect_scale"] == "unchanged_input_expression"
    text = (episode.root / "summary.md").read_text()
    assert "<script>" not in text and "<img" not in text and "[Open](" not in text
    assert "Requested wording" in text and "unchanged input-expression" in text
    assert verify_report(episode)["valid"]


@pytest.mark.parametrize("findings", [None, "g0", [None], [{"action": True, "target_id": "mean_contrast", "hypothesis_id": "g0"}],
    [{"action": 4, "target_id": "mean_contrast", "hypothesis_id": "g0", "estimate": 900}],
    [{"action": 4, "target_id": "mean_contrast", "hypothesis_id": "g0"}] * 2,
    [{"action": i + 1, "target_id": "mean_contrast", "hypothesis_id": "g0"} for i in range(21)]])
def test_finding_schema_and_controller_reject_unbounded_or_invented_claims(findings):
    from jsonschema import Draft202012Validator
    from devbench.analysis.episode_codex import action_schema
    from devbench.analysis.episode_synthesis import validate_finding_requests
    with pytest.raises(ValueError):
        validate_finding_requests(findings)
    assert not Draft202012Validator(action_schema([])).is_valid({"kind": "finish", "findings": findings})


def test_selector_schema_still_accepts_plain_finish_and_bounded_references():
    from jsonschema import Draft202012Validator
    from devbench.analysis.episode_codex import action_schema
    validator = Draft202012Validator(action_schema([]))
    assert validator.is_valid({"kind": "finish"})
    assert validator.is_valid({"kind": "finish", "findings": []})
    assert validator.is_valid({"kind": "finish", "findings": [{"action": 4, "target_id": "mean_contrast", "hypothesis_id": "g0"}]})


def test_a_diagnostic_or_stale_action_cannot_supply_a_finding(workspace):
    episode, _ = create(workspace)
    observed = run_contrast(episode)
    invalid = episode.step({"kind": "finish", "findings": [reference(observed, action=1)]})
    assert invalid["status"] == "rejected"
    episode.step({"kind": "revise", "reason": "Select the actual fitted result"})
    final = episode.step({"kind": "finish", "findings": [reference(observed)]})
    assert final["outcome"] == "completed"
    assert verify_report(episode)["valid"]


def test_corrupt_tool_result_withholds_report_and_prevents_acceptance(workspace):
    from devbench.analysis.episodes import verify_episode
    episode, _ = create(workspace)
    observed = run_contrast(episode)
    path = episode.broker.output_dir / (observed["receipt_id"] + ".json")
    path.write_bytes(path.read_bytes() + b" ")
    final = episode.step({"kind": "finish"})
    assert final["outcome"] == "infrastructure_failure"
    assert final["harness_accepted"] is False
    report = json.loads((episode.root / "synthesis.json").read_text())
    assert report["evidence"]["status"] == "withheld"
    assert not report["findings"] and not report["targets"]
    assert verify_episode(episode.root, episode.receipt["root_hash"])["valid"]
    verified = verify_report(episode)
    assert verified["valid"] and not verified["evidence_verified"]


@pytest.mark.parametrize("filename", ["synthesis.json", "summary.md"])
def test_modified_sealed_report_is_not_verified(workspace, filename):
    episode, _ = create(workspace)
    run_contrast(episode)
    episode.step({"kind": "finish"})
    path = episode.root / filename
    path.write_text(path.read_text() + " altered")
    with pytest.raises(ValueError, match="artifact_changed"):
        verify_report(episode)


def test_refusal_report_and_no_tool_interruption_remain_distinct(workspace, monkeypatch):
    episode, _ = create(workspace, confounded=True)
    for tool in ("audit_expression_quality", "validate_biological_units", "validate_design_matrix"):
        episode.step({"kind": "tool", "tool": tool})
    episode.step({"kind": "refuse", "reason": "No identifiable adjusted contrast"})
    report = json.loads((episode.root / "synthesis.json").read_text())
    assert report["outcome"] == "refusal" and not report["findings"]
    design_event = next(event for event in report["tool_events"] if event["tool"] == "validate_design_matrix")
    assert design_event["design_check"]["condition_number_validated"] is False
    assert report["scientific_correctness"] is None
    assert verify_report(episode)["valid"]
    other = workspace / "interrupted"
    other.mkdir()
    interrupted, _ = create(other)
    def stop(*args, **kwargs):
        raise KeyboardInterrupt("injected interruption")
    monkeypatch.setattr(interrupted.broker, "execute", stop)
    with pytest.raises(KeyboardInterrupt):
        interrupted.step({"kind": "tool", "tool": "audit_expression_quality"})
    report = json.loads((interrupted.root / "synthesis.json").read_text())
    assert report["outcome"] == "infrastructure_failure" and not report["findings"]
    assert verify_report(interrupted)["valid"]


def test_resumed_generation_is_not_counted_twice(workspace):
    from tests.test_episode_handoff import REPLY, paused
    from devbench.analysis.episode_handoff import resume_handoff
    original, handoff = paused(workspace)
    restored = resume_handoff(original.root, handoff["manifest_path"], expected_root=handoff["root_hash"],
                             response=REPLY, allow_data_execution=True)
    restored.step({"kind": "tool", "tool": "validate_design_matrix"})
    observed = restored.step({"kind": "tool", "tool": "mean_condition_analysis"})
    restored.step({"kind": "finish", "findings": [reference(observed, action=5)]})
    report = json.loads((restored.root / "synthesis.json").read_text())
    assert len(report["tool_events"]) == 4
    assert report["findings"][0]["source"]["action"] == 5
    assert report["findings"][0]["source"]["result_path"].startswith("tool-evidence-resume-")
    assert verify_report(restored)["valid"]


def test_report_verification_requires_evidence_authorization():
    from devbench.analysis.episode_synthesis import verify_synthesis
    with pytest.raises(PermissionError):
        verify_synthesis(None, None)


def test_older_computation_cannot_replace_latest_result(workspace):
    episode, _ = create(workspace)
    old = run_contrast(episode)
    new = episode.step({"kind": "tool", "tool": "mean_condition_analysis"})
    refused = episode.step({"kind": "finish", "findings": [reference(old)]})
    assert refused["status"] == "rejected"
    episode.step({"kind": "revise", "reason": "Use the latest matching result"})
    assert episode.step({"kind": "finish", "findings": [reference(new, action=5)]})["outcome"] == "completed"
    assert verify_report(episode)["valid"]


def test_failed_hypothesis_is_reported_without_an_invented_estimate(workspace):
    import csv
    from devbench.analysis.episodes import AnalysisEpisode

    task, paths = inputs(workspace, continuous=False)
    with paths["expression"].open(newline="") as handle:
        rows = list(csv.reader(handle))
    rows[-1][1:] = ["0"] * (len(rows[-1]) - 1)
    with paths["expression"].open("w", newline="") as handle:
        csv.writer(handle).writerows(rows)
    episode = AnalysisEpisode.create(workspace / "episode", question="Inspect all attempted hypotheses", assay="bulk",
        public_task=task, inputs=paths, allow_data_execution=True)
    observed = run_contrast(episode)
    failed = observed["result"]["target_results"][0]["payload"]["gene_statistics"][-1]
    assert failed["estimate_status"] != "estimated"
    final = episode.step({"kind": "finish", "findings": [{"action": 4, "target_id": "mean_contrast", "hypothesis_id": failed["hypothesis_id"]}]})
    assert final["outcome"] == "scientific_failure" and not final["harness_accepted"]
    report = json.loads((episode.root / "synthesis.json").read_text())
    assert report["targets"][0]["hypothesis_family"]["attempted_count"] == len(task["gene_ids"])
    assert report["targets"][0]["hypothesis_family"]["tested_count"] == len(task["gene_ids"]) - 1
    assert report["findings"][0]["estimate"] == failed
    assert "No estimate or test reported" in (episode.root / "summary.md").read_text()
    assert verify_report(episode)["valid"]


def test_reporting_failure_preserves_the_original_infrastructure_reason(workspace):
    episode, _ = create(workspace)
    run_contrast(episode)
    episode.broker.capture_error = "injected capture failure"
    final = episode.abort("provider_interrupted_before_synthesis")
    assert final["outcome"] == "infrastructure_failure"
    assert final["reason"] == "tool_evidence_capture_incomplete"
    report = json.loads((episode.root / "synthesis.json").read_text())
    assert report["evidence"]["status"] == "withheld"
    assert verify_report(episode)["valid"]


def test_observation_cannot_be_transplanted_to_an_earlier_rejected_action(workspace):
    episode, _ = create(workspace)
    assert episode.step({"kind": "tool", "tool": "mean_condition_analysis"})["status"] == "rejected"
    episode.step({"kind": "revise", "reason": "Run prerequisites"})
    observed = run_contrast(episode)
    old = episode.root / "checkpoints/action-0006/observation.json"
    false = episode.root / "checkpoints/action-0001/observation.json"
    false.parent.mkdir()
    false.write_bytes(old.read_bytes())
    old.unlink()
    result = episode.step({"kind": "finish", "findings": [reference(observed, action=1)]})
    assert result.get("outcome") != "completed"
    assert result.get("status") == "rejected" or result.get("outcome") == "infrastructure_failure"
    if episode.outcome is None:
        episode.abort("checkpoint_observation_tampered")
    assert not episode.observation()["harness_accepted"]


def test_malformed_observation_withholds_report_but_seals_failure(workspace):
    episode, _ = create(workspace)
    run_contrast(episode)
    (episode.root / "checkpoints/action-0004/observation.json").write_text("[]")
    result = episode.step({"kind": "finish"})
    assert result["outcome"] == "infrastructure_failure" and not result["harness_accepted"]
    assert episode.receipt is not None
    report = json.loads((episode.root / "synthesis.json").read_text())
    assert report["evidence"]["status"] == "withheld" and not report["findings"]
    assert verify_report(episode)["valid"]
