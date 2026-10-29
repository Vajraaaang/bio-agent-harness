"""Independent controls for bounded claim repair; no live model calls."""
import csv
import json
from types import SimpleNamespace

import pytest

from tests.test_analysis_episodes import create, inputs, run_contrast, workspace
from tests.test_episode_synthesis import reference


def invalid_claim(observation, kind):
    if kind == "schema":
        return {"kind": "finish", "findings": [{**reference(observation), "p_value": 0.001}]}
    if kind == "fields":
        return {"kind": "finish", "findings": [reference(observation)], "alpha": 0.5}
    return {"kind": "finish", "findings": [{**reference(observation), "hypothesis_id": "absent_gene"}]}


@pytest.mark.parametrize("first,second", [("schema", "schema"), ("schema", "semantic"),
                                          ("semantic", "schema"), ("semantic", "semantic"),
                                          ("fields", "schema")])
def test_one_claim_rewrite_shared_across_schema_and_evidence_failures(workspace, first, second):
    from devbench.analysis.episodes import replay_episode, verify_episode

    episode, _ = create(workspace)
    observed = run_contrast(episode)
    before = len(episode.broker.events)
    initial = episode.step(invalid_claim(observed, first))
    assert initial["status"] == "rejected"
    assert initial["outcome"] is None
    final = episode.step(invalid_claim(observed, second))
    assert final["outcome"] == "scientific_failure"
    assert final["reason"] == "claim_rewrite_budget_exhausted"
    assert not final["harness_accepted"]
    assert len(episode.broker.events) == before
    assert verify_episode(episode.root, episode.receipt["root_hash"])["valid"]
    with pytest.raises(RuntimeError, match="Terminal"):
        episode.step({"kind": "finish", "findings": [reference(observed)]})
    replay = replay_episode(episode.root, episode.receipt["root_hash"],
                            workspace / "replayed-claim-failure", allow_data_execution=True)
    assert replay.outcome == "scientific_failure"
    assert json.loads((replay.root / "episode.json").read_text())["reason"] == "claim_rewrite_budget_exhausted"
    assert len(replay.broker.events) == before


@pytest.mark.parametrize("kind", ["schema", "semantic"])
def test_single_corrected_claim_uses_existing_statistics(workspace, kind):
    episode, _ = create(workspace)
    observed = run_contrast(episode)
    before = len(episode.broker.events)
    assert episode.step(invalid_claim(observed, kind))["status"] == "rejected"
    final = episode.step({"kind": "finish", "findings": [reference(observed)]})
    assert final["outcome"] == "completed"
    assert len(episode.broker.events) == before
    report = json.loads((episode.root / "synthesis.json").read_text())
    assert report["findings"][0]["source"]["receipt_id"] == observed["receipt_id"]


def test_claim_repair_cannot_reenter_fitting_even_after_strategy_revision(workspace):
    episode, _ = create(workspace)
    observed = run_contrast(episode)
    before = len(episode.broker.events)
    episode.step(invalid_claim(observed, "semantic"))
    episode.step({"kind": "revise", "reason": "Repair the claim only"})
    refusal = episode.step({"kind": "tool", "tool": "mean_condition_analysis"})
    assert refusal["status"] == "rejected"
    assert len(episode.broker.events) == before
    assert episode.step({"kind": "finish", "findings": [reference(observed)]})["outcome"] == "completed"


@pytest.mark.parametrize("changed", [{"alpha": 0.5}, {"batch_cols": []},
                                      {"condition_number_limit": 1_000_000}])
def test_inferential_settings_cannot_be_changed_by_an_action(workspace, changed):
    episode, _ = create(workspace)
    frozen = json.loads(json.dumps(episode.config["fixed_options"]))
    options = {**frozen["mean_condition_analysis"], **changed}
    result = episode.step({"kind": "tool", "tool": "mean_condition_analysis", "options": options})
    assert result["status"] == "rejected"
    assert not episode.broker.events
    assert episode.config["fixed_options"] == frozen
    episode.abort("test_completed_without_execution")


def test_unavailable_enrichment_cannot_be_added_after_admission(workspace):
    episode, _ = create(workspace)
    assert "regulon_enrichment" not in episode.menu
    result = episode.step({"kind": "tool", "tool": "regulon_enrichment",
                           "options": {"alpha": 0.5, "universe": ["g0"]}})
    assert result["status"] == "rejected"
    assert not episode.broker.events
    episode.abort("test_completed_without_execution")


def test_paired_design_diagnostic_supports_refusal_and_replay_without_fitting(workspace):
    from devbench.analysis.episodes import AnalysisEpisode, replay_episode, verify_episode

    task, paths = inputs(workspace, continuous=False)
    with paths["metadata"].open(newline="") as handle:
        records = list(csv.DictReader(handle))
    for index, row in enumerate(records):
        row["donor_id"] = "paired-donor-" + str(index % 4)
    with paths["metadata"].open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(records[0]))
        writer.writeheader()
        writer.writerows(records)
    episode = AnalysisEpisode.create(workspace / "paired-episode", question="Compare B and A within donors",
        assay="bulk", public_task=task, inputs=paths, allow_data_execution=True)
    for tool in ("audit_expression_quality", "validate_biological_units", "validate_design_matrix"):
        result = episode.step({"kind": "tool", "tool": tool})
        assert result["status"] == "succeeded", result
    assert result["result"]["diagnostics"]["analysis_error"]["code"] == (
        "repeated_units_across_design_coordinates_require_cluster_model")
    assert result["result"]["target_results"] == []
    final = episode.step({"kind": "refuse", "reason": "The registered independent-unit model cannot fit paired donors"})
    assert final["outcome"] == "refusal"
    assert final["refusal_supported"] is True
    assert final["scientific_correctness"] is None
    assert verify_episode(episode.root, episode.receipt["root_hash"])["valid"]
    replay = replay_episode(episode.root, episode.receipt["root_hash"],
                            workspace / "paired-replay", allow_data_execution=True)
    assert replay.outcome == "refusal"
    for run in (episode, replay):
        assert [event["tool"] for event in run.broker.events] == [
            "audit_expression_quality", "validate_biological_units", "validate_design_matrix"]
        assert all(not item["method"]["count_model"] for item in run.results)


@pytest.mark.parametrize("code,tool,error_type", [
    ("missing_numerical_dependency", "validate_design_matrix", "ImportError"),
    ("unknown_internal_error", "validate_design_matrix", "ValueError"),
    ("worker_timed_out", "validate_design_matrix", "RuntimeError"),
    ("repeated_units_across_design_coordinates_require_cluster_model", "mean_condition_analysis", "ValueError"),
    ("repeated_units_across_design_coordinates_require_cluster_model", "validate_design_matrix", "RuntimeError"),
])
def test_arbitrary_analysis_errors_do_not_earn_supported_refusal(code, tool, error_type):
    from devbench.analysis.episodes import AnalysisEpisode

    state = SimpleNamespace(supported=True, results=[{"target_results": [],
        "method": {"tool": tool, "version": "observed-bulk-1", "uses_observed_inputs_only": True},
        "diagnostics": {"analysis_error": {"code": code, "type": error_type}}}])
    assert AnalysisEpisode._refusal_supported(state) is False
