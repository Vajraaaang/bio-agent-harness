"""Small local episode controls; no model participant or biological qualification."""
import csv
import importlib.util
import json
from pathlib import Path
import tempfile

import pytest


def api():
    assert importlib.util.find_spec("devbench.analysis.episodes") is not None, "Reusable episode entrypoint is missing"
    from devbench.analysis.episodes import AnalysisEpisode, EpisodeLimits, verify_episode
    return AnalysisEpisode, EpisodeLimits, verify_episode


@pytest.fixture
def workspace():
    # Broker evidence must be outside platform-wide scratch, even in fixtures.
    base = Path(__file__).resolve().parents[1] / "reports" / "episode-verification" / "workspaces"
    base.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix="fixture-", dir=base))


def inputs(root, *, cellular=False, continuous=True, confounded=False):
    source = root / "source"
    source.mkdir()
    n = 32 if cellular else 8
    ids = [f"s{i}" for i in range(n)]
    rows = [{"sample_id": sample, "cell_id": sample,
             "donor_id": f"d{i // 4}" if cellular else f"d{i}",
             "specimen_id": f"p{i // 4}" if cellular else f"p{i}",
             "library_id": f"l{i // 4}" if cellular else f"l{i}",
             "condition": "A" if i < n // 2 else "B",
             ("capture_batch" if cellular else "batch"): ("x" if i < n // 2 else "y") if confounded else "x"}
            for i, sample in enumerate(ids)]
    with (source / "metadata.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    genes = ["markerA", "markerB", *[f"g{i}" for i in range(12)]] if cellular else [f"g{i}" for i in range(12)]
    with (source / "expression.csv").open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["gene_id", *ids])
        for j, gene in enumerate(genes):
            if cellular and j < 2:
                values = [80 if (i % 4 < 2) == (j == 0) else 1 for i in range(n)]
            elif continuous:
                values = [1.0 + (i % 2) + (4 if i >= n // 2 else 0) + j * .1 for i in range(n)]
            else:
                values = [10 + (i * 3 + j * 5) % 9 + (j % 3 if i >= n // 2 else 0) for i in range(n)]
            writer.writerow([gene, *values])
    annotations = {"cell_annotation_reference": {
        "reference_identity": "fixture-marker-panel", "reference_scope": "planted broad-type markers",
        "markers": [{"label": "typeA", "gene_ids": ["markerA"]},
                    {"label": "typeB", "gene_ids": ["markerB"]}], "query_cell_labels_supplied": False}} if cellular else {}
    (source / "annotations.json").write_text(json.dumps(annotations))
    task = {"measurement": "umi_counts" if cellular else "continuous" if continuous else "raw_counts",
            "population_unit_label": "donor_id", "observation_id_label": "sample_id",
            "context": {"biological_unit": "donor", "condition_labels": ["A", "B"]},
            "analysis_columns": {"condition": "condition", "time": None if cellular else "numeric_time", "dose": None,
                "covariate": None, "population_unit": "donor_id", "batches": ["capture_batch" if cellular else "batch"]},
            "questions": [{"target_id": "cell_state_effect" if cellular else "mean_contrast",
                "estimand": "Within-type donor contrast" if cellular else "Donor condition contrast",
                "units": "log2 count-rate contrast" if cellular or not continuous else "input units",
                "contrast": "B minus A", "reference_population": "declared independent donors",
                "prediction_reference": "observed donor population"}], "gene_ids": genes}
    return task, {"expression": source / "expression.csv", "metadata": source / "metadata.csv",
                  "annotations": source / "annotations.json"}


def create(root, *, cellular=False, continuous=True, confounded=False, **kwargs):
    Episode, Limits, _ = api()
    task, paths = inputs(root, cellular=cellular, continuous=continuous, confounded=confounded)
    episode = Episode.create(root / "episode", question="Estimate B minus A while respecting donors",
        assay="scrna" if cellular else "bulk", public_task=task, inputs=paths,
        allow_data_execution=True, **kwargs)
    return episode, paths


def run_contrast(episode, cellular=False):
    for tool in ("audit_expression_quality", "validate_biological_units",
                 "pseudobulk_membership_audit" if cellular else "validate_design_matrix",
                 "cell_type_state_analysis" if cellular else "mean_condition_analysis"):
        observation = episode.step({"kind": "tool", "tool": tool})
        assert observation["status"] == "succeeded", observation
    return observation


def test_episode_entrypoint_exists():
    api()


def test_default_denial_precedes_path_access():
    Episode, _, _ = api()
    with pytest.raises(PermissionError):
        Episode.create(None, question=None, assay=None, public_task=None, inputs=None)


@pytest.mark.parametrize("cellular,continuous", [(False, True), (False, False), (True, False)])
def test_real_supported_flow_binds_snapshot_and_keeps_correctness_separate(workspace, cellular, continuous):
    _, _, verify = api()
    episode, paths = create(workspace, cellular=cellular, continuous=continuous)
    original = {name: path.read_bytes() for name, path in paths.items()}
    result = run_contrast(episode, cellular)
    final = episode.step({"kind": "finish"})
    assert final["outcome"] == "completed", final
    assert final["harness_accepted"] is True
    assert final["scientific_correctness"] is None
    assert final["usage"]["tokens"] is None and final["usage"]["cost"] is None
    assert verify(episode.root, episode.receipt["root_hash"])["valid"]
    assert original == {name: path.read_bytes() for name, path in paths.items()}
    if continuous:
        assert result["result"]["target_results"][0]["payload"]["gene_statistics"][0]["values"][0] == pytest.approx(4)


def test_prerequisites_and_fixed_parameters_are_not_agent_discretion(workspace):
    episode, _ = create(workspace)
    refused = episode.step({"kind": "tool", "tool": "mean_condition_analysis"})
    assert refused["state"] == "needs_revision"
    assert refused["status"] == "rejected"
    episode.step({"kind": "revise", "reason": "Run the required diagnostics first"})
    rejected = episode.step({"kind": "tool", "tool": "validate_design_matrix", "options": {"condition_number_limit": 100000}})
    assert rejected["status"] == "rejected"
    assert not episode.broker.events


def test_refusal_is_diagnostic_backed_not_a_free_success_label(workspace):
    episode, _ = create(workspace, confounded=True)
    for name in ("audit_expression_quality", "validate_biological_units", "validate_design_matrix"):
        episode.step({"kind": "tool", "tool": name})
    final = episode.step({"kind": "refuse", "reason": "Requested adjusted contrast is not identifiable"})
    assert final["outcome"] == "refusal"
    assert final["refusal_supported"] is True
    assert final["scientific_correctness"] is None
    assert final["harness_accepted"] is True


def test_unsupported_refusal_and_information_request(workspace):
    Episode, _, _ = api()
    task, paths = inputs(workspace)
    episode = Episode.create(workspace / "episode", question="Analyze nuclei", assay="snrna",
        public_task=task, inputs=paths, allow_data_execution=True)
    waiting = episode.step({"kind": "request_information", "message": "Please confirm assay and count provenance"})
    assert waiting["state"] == "awaiting_information"
    final = episode.step({"kind": "refuse", "reason": "This assay/input route is not supported in this release"})
    assert final["outcome"] == "refusal" and final["refusal_supported"]


def test_revision_budget_is_explicit_and_does_not_reset_history(workspace):
    _, Limits, _ = api()
    episode, _ = create(workspace, limits=Limits(max_revisions=1))
    episode.step({"kind": "tool", "tool": "unknown"})
    episode.step({"kind": "revise", "reason": "Use a registered method"})
    episode.step({"kind": "tool", "tool": "unknown"})
    final = episode.step({"kind": "revise", "reason": "Try once more"})
    assert final["outcome"] == "scientific_failure"
    assert final["revisions"] == 1
    assert len(episode.actions) == 4


def test_real_worker_timeout_is_infrastructure_failure(workspace):
    _, Limits, _ = api()
    episode, _ = create(workspace, limits=Limits(stage_timeout_seconds=.001))
    result = episode.step({"kind": "tool", "tool": "audit_expression_quality"})
    assert result["outcome"] == "infrastructure_failure"
    assert result["harness_accepted"] is False


def test_interrupted_step_retains_failure_and_can_reset(workspace, monkeypatch):
    episode, _ = create(workspace)
    def interrupt(*args, **kwargs):
        raise KeyboardInterrupt("injected control-plane interruption")
    monkeypatch.setattr(episode.broker, "execute", interrupt)
    with pytest.raises(KeyboardInterrupt):
        episode.step({"kind": "tool", "tool": "audit_expression_quality"})
    assert episode.outcome == "infrastructure_failure"
    assert list((episode.root / "checkpoints" / "failures").glob("*.json"))
    reset = episode.reset(workspace / "reset", allow_data_execution=True)
    assert reset.observation()["state"] == "ready"
    assert reset.snapshot_id == episode.snapshot_id
    assert not reset.actions
    assert reset.limits == episode.limits
    run_contrast(reset)
    assert reset.step({"kind": "finish"})["outcome"] == "completed"


def test_snapshot_mutation_cannot_produce_accepted_results(workspace):
    episode, _ = create(workspace)
    path = episode.input_paths["metadata"]
    path.chmod(0o600)
    path.write_text("modified")
    result = episode.step({"kind": "tool", "tool": "audit_expression_quality"})
    assert result["outcome"] == "infrastructure_failure"
    assert result["harness_accepted"] is False


def test_completed_episode_reexecutes_in_clean_workspace(workspace):
    episode, _ = create(workspace)
    observed = run_contrast(episode)
    episode.step({"kind": "finish"})
    replay = episode.replay(workspace / "replay", expected_root=episode.receipt["root_hash"], allow_data_execution=True)
    assert replay.outcome == "completed"
    assert replay.snapshot_id == episode.snapshot_id
    assert replay.root != episode.root
    assert replay.receipt["root_hash"] != episode.receipt["root_hash"]
    assert replay.last_result["target_results"] == observed["result"]["target_results"]


def test_no_unearned_completion_or_unverified_refusal(workspace):
    episode, _ = create(workspace)
    final = episode.step({"kind": "refuse", "reason": "I prefer not to analyze"})
    assert final["outcome"] == "scientific_failure"
    assert final["refusal_supported"] is False


def test_evaluation_feedback_is_not_an_action_or_trace_input(workspace):
    episode, _ = create(workspace)
    response = episode.step({"kind": "tool", "tool": "audit_expression_quality", "reward": 1, "reference_answer": "hidden"})
    assert response["status"] == "rejected"
    assert "hidden" not in json.dumps(response)
    assert not episode.broker.events


@pytest.mark.parametrize("action", [{"kind": []}, {"kind": "tool", "tool": []},
                                  {"kind": "tool", "tool": {"name": "audit_expression_quality"}}])
def test_malformed_action_types_are_recorded_rejections(workspace, action):
    episode, _ = create(workspace)
    response = episode.step(action)
    assert response["status"] == "rejected"
    assert response["state"] == "needs_revision"
    assert not episode.broker.events


def test_nested_evaluator_fields_never_enter_replay_trace(workspace):
    episode, _ = create(workspace)
    response = episode.step({"kind": "tool", "tool": "audit_expression_quality",
                             "options": {"reference_answer": "protected-answer-sentinel", "reward": 1}})
    assert response["status"] == "rejected"
    assert "protected-answer-sentinel" not in json.dumps(episode.actions)
    assert all("protected-answer-sentinel" not in p.read_text() for p in (episode.root / "audit" / "nodes").glob("*.json"))


def test_replay_uses_verified_persisted_actions_not_mutable_memory(workspace):
    episode, _ = create(workspace)
    run_contrast(episode)
    episode.step({"kind": "finish"})
    episode.actions.clear()
    replay = episode.replay(workspace / "replay", expected_root=episode.receipt["root_hash"], allow_data_execution=True)
    assert replay.outcome == "completed"
    assert len(replay.actions) == 5


@pytest.mark.parametrize("cellular", [False, True])
def test_declared_condition_column_is_bound_into_tools(workspace, cellular):
    Episode, _, _ = api()
    task, paths = inputs(workspace, cellular=cellular, continuous=not cellular)
    text = paths["metadata"].read_text()
    paths["metadata"].write_text(text.replace(",condition,", ",treatment,"))
    task["analysis_columns"]["condition"] = "treatment"
    episode = Episode.create(workspace / "episode", question="Compare declared treatment groups",
        assay="scrna" if cellular else "bulk", public_task=task, inputs=paths, allow_data_execution=True)
    run_contrast(episode, cellular)
    assert episode.step({"kind": "finish"})["outcome"] == "completed"


def test_observation_exposes_frozen_action_parameters_and_honest_usage(workspace):
    episode, _ = create(workspace)
    observation = episode.observation()
    assert observation["terminated"] is False
    assert observation["tool_catalog"]["mean_condition_analysis"]["fixed_options"]["condition_col"] == "condition"
    assert observation["usage"]["elapsed_sec"] >= 0
    assert observation["usage"]["RSS_MB"] is None
    assert observation["usage"]["tokens"] is None


def test_terminal_usage_does_not_keep_growing_after_completion(workspace, monkeypatch):
    import devbench.analysis.episodes as module
    episode, _ = create(workspace)
    final = episode.step({"kind": "refuse", "reason": "No supported refusal evidence"})
    later = module.time.monotonic() + 1000
    monkeypatch.setattr(module.time, "monotonic", lambda: later)
    assert episode.observation()["usage"]["elapsed_sec"] == final["usage"]["elapsed_sec"]


def test_failed_finalization_cannot_leave_an_accepted_in_memory_episode(workspace, monkeypatch):
    import devbench.analysis.episodes as module
    episode, _ = create(workspace)
    run_contrast(episode)
    original = module.atomic_json
    def fail_terminal_record(path, value):
        if Path(path).name == "episode.json":
            raise OSError("injected output storage failure")
        return original(path, value)
    monkeypatch.setattr(module, "atomic_json", fail_terminal_record)
    with pytest.raises(OSError, match="storage failure"):
        episode.step({"kind": "finish"})
    assert episode.outcome == "infrastructure_failure"
    assert episode.observation()["harness_accepted"] is False
    assert episode.receipt is None
