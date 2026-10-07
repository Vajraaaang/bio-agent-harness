"""Research review records, not authenticated approvals or semantic planning."""
import json
from pathlib import Path

import pytest

from tests.test_analysis_episodes import inputs, run_contrast, workspace


def api():
    from devbench.analysis.episode_proposals import prepare_proposal, record_review, begin_reviewed_episode, verify_bound_review
    return prepare_proposal, record_review, begin_reviewed_episode, verify_bound_review


def prepared(root, *, cellular=False, continuous=True):
    prepare, _, _, _ = api()
    task, paths = inputs(root, cellular=cellular, continuous=continuous)
    spec = {"question": "Estimate B minus A with declared donor replication", "assay": "scrna" if cellular else "bulk",
            "public_task": task, "inputs": {k: str(v) for k, v in paths.items()}}
    receipt = prepare(root / "proposal", spec, allow_data_read=True)
    return receipt, paths


def reviewed(root, proposal, decision="approve"):
    _, review, _, _ = api()
    return review(root / "review", proposal["manifest_path"], expected_root=proposal["root_hash"],
        reviewer="fixture-operator", decision=decision, rationale="Local control-path fixture; no independent scientific qualification.",
        confirmations={key: True for key in ("question_and_contrast", "input_provenance", "biological_units_and_design", "limitations")},
        allow_review=True)


@pytest.mark.parametrize("permission", [False, None, 0, 1, "true"])
def test_review_workflow_default_denies_before_paths(permission):
    prepare, review, begin, _ = api()
    with pytest.raises(PermissionError):
        prepare(None, None, allow_data_read=permission)
    with pytest.raises(PermissionError):
        review(None, None, expected_root=None, reviewer=None, decision=None, rationale=None, confirmations=None, allow_review=permission)
    with pytest.raises(PermissionError):
        begin(None, None, None, expected_proposal_root=None, expected_review_root=None, allow_data_execution=permission)


@pytest.mark.parametrize("cellular,continuous", [(False, True), (False, False), (True, False)])
def test_review_binds_real_small_episode_inputs_settings_and_replay(workspace, cellular, continuous):
    _, _, begin, verify = api()
    proposal, paths = prepared(workspace, cellular=cellular, continuous=continuous)
    review = reviewed(workspace, proposal)
    episode = begin(workspace / "episode", proposal["manifest_path"], review["manifest_path"],
        expected_proposal_root=proposal["root_hash"], expected_review_root=review["root_hash"], allow_data_execution=True)
    assert episode.actions == []
    assert episode.observation()["review_status"] == "recorded_operator_research_review"
    assert verify(episode.root, episode.review_binding_sha256, episode.config, episode.snapshot)["review_scope"] == "caller_asserted_research_review"
    result = run_contrast(episode, cellular)
    assert episode.step({"kind": "finish"})["outcome"] == "completed"
    binding = json.loads((episode.root / "review-binding.json").read_text())
    assert binding["proposal_root"] == proposal["root_hash"]
    assert binding["review_root"] == review["root_hash"]
    assert binding["clinical_release_allowed"] is False
    if continuous:
        # Original reviewed paths are intentionally gone; replay uses the
        # episode's own snapshot and embedded review proof.
        for path in paths.values():
            path.unlink()
        replay = episode.replay(workspace / "replay", expected_root=episode.receipt["root_hash"], allow_data_execution=True)
        assert replay.outcome == "completed"
        assert replay.last_result["target_results"] == result["result"]["target_results"]
        assert replay.observation()["review_status"] == "recorded_operator_research_review"


def test_rejected_review_cannot_start_episode(workspace):
    _, _, begin, _ = api()
    proposal, _ = prepared(workspace)
    review = reviewed(workspace, proposal, "reject")
    with pytest.raises(ValueError, match="approved"):
        begin(workspace / "denied", proposal["manifest_path"], review["manifest_path"],
            expected_proposal_root=proposal["root_hash"], expected_review_root=review["root_hash"], allow_data_execution=True)
    assert not (workspace / "denied").exists()


def test_changed_inputs_after_review_are_rejected_before_episode_creation(workspace):
    _, _, begin, _ = api()
    proposal, paths = prepared(workspace)
    review = reviewed(workspace, proposal)
    paths["metadata"].write_text(paths["metadata"].read_text() + "\n")
    with pytest.raises(ValueError, match="input.*changed"):
        begin(workspace / "denied", proposal["manifest_path"], review["manifest_path"],
            expected_proposal_root=proposal["root_hash"], expected_review_root=review["root_hash"], allow_data_execution=True)
    assert not (workspace / "denied").exists()


def test_review_is_bound_to_exact_proposal(workspace):
    _, _, begin, _ = api()
    first, _ = prepared(workspace)
    review = reviewed(workspace, first)
    second_dir = workspace / "second"
    second_dir.mkdir()
    second, _ = prepared(second_dir)
    with pytest.raises(ValueError, match="proposal"):
        begin(workspace / "denied", second["manifest_path"], review["manifest_path"],
            expected_proposal_root=second["root_hash"], expected_review_root=review["root_hash"], allow_data_execution=True)


def test_review_requires_explicit_assertions_and_does_not_create_signing_keys(workspace):
    _, review, _, _ = api()
    proposal, _ = prepared(workspace)
    with pytest.raises(ValueError, match="confirmations"):
        review(workspace / "denied", proposal["manifest_path"], expected_root=proposal["root_hash"],
               reviewer="fixture-operator", decision="approve", rationale="Check", confirmations={}, allow_review=True)
    assert not (workspace / "denied").exists()
    assert not list(workspace.rglob("*.pem"))


def test_proposal_loader_binds_its_captured_manifest_to_the_retained_root(workspace, monkeypatch):
    import hashlib
    from devbench.analysis import episode_proposals as records
    from devbench.analysis.checkpoints import digest
    proposal, _ = prepared(workspace)
    manifest_path = Path(proposal["manifest_path"])
    record_path = workspace / "proposal/proposal.json"
    altered = json.loads(record_path.read_text())
    altered["status"] = "substituted_after_or_before_verification"
    content = json.dumps(altered).encode()
    manifest = json.loads(manifest_path.read_text())
    manifest["analysis_fingerprint"] = digest(altered)
    manifest["artifacts"][0]["sha256"] = hashlib.sha256(content).hexdigest()
    original = records._read
    def inconsistent(path, limit):
        if path == manifest_path:
            return json.dumps(manifest).encode()
        if path == record_path:
            return content
        return original(path, limit)
    monkeypatch.setattr(records, "_read", inconsistent)
    with pytest.raises(ValueError, match="[Mm]anifest"):
        records._load(manifest_path, proposal["root_hash"], "proposal.json")


def test_mid_episode_review_tamper_is_not_accepted(workspace):
    _, _, begin, _ = api()
    proposal, _ = prepared(workspace)
    review = reviewed(workspace, proposal)
    episode = begin(workspace / "episode", proposal["manifest_path"], review["manifest_path"],
        expected_proposal_root=proposal["root_hash"], expected_review_root=review["root_hash"], allow_data_execution=True)
    path = episode.root / "review-binding.json"
    path.write_bytes(path.read_bytes() + b" ")
    result = episode.step({"kind": "tool", "tool": "audit_expression_quality"})
    assert result["outcome"] == "infrastructure_failure" and not result["harness_accepted"]
    assert not episode.broker.events


def test_review_binding_survives_clarification(workspace):
    from devbench.analysis.episode_handoff import save_handoff, resume_handoff
    from tests.test_episode_handoff import REPLY
    _, _, begin, verify = api()
    proposal, _ = prepared(workspace)
    review = reviewed(workspace, proposal)
    episode = begin(workspace / "episode", proposal["manifest_path"], review["manifest_path"],
        expected_proposal_root=proposal["root_hash"], expected_review_root=review["root_hash"], allow_data_execution=True)
    episode.step({"kind": "request_information", "message": "Confirm interpretation without changing the reviewed inputs"})
    handoff = save_handoff(episode)
    resumed = resume_handoff(episode.root, handoff["manifest_path"], expected_root=handoff["root_hash"], response=REPLY, allow_data_execution=True)
    assert resumed.review_binding_sha256 == episode.review_binding_sha256
    assert verify(resumed.root, resumed.review_binding_sha256, resumed.config, resumed.snapshot)["review_scope"] == "caller_asserted_research_review"
    run_contrast(resumed)
    assert resumed.step({"kind": "finish"})["outcome"] == "completed"


@pytest.mark.parametrize("stage", ["prepare", "review", "begin"])
def test_output_cannot_be_created_inside_original_directory_input(workspace, stage):
    from devharness_core.provenance import hash_artifact
    prepare, review_record, begin, _ = api()
    task, paths = inputs(workspace)
    directory = workspace / "expression.zarr"
    directory.mkdir()
    (directory / "opaque-fixture-data").write_bytes(b"directory identity fixture; no numerical execution")
    paths["expression"] = directory
    spec = {"question": "Fixture path safety", "assay": "bulk", "public_task": task,
            "inputs": {key: str(path) for key, path in paths.items()}}
    before = hash_artifact(directory)["sha256"]
    if stage == "prepare":
        with pytest.raises(ValueError, match="disjoint"):
            prepare(directory / "proposal", spec, allow_data_read=True)
    else:
        proposal = prepare(workspace / "proposal", spec, allow_data_read=True)
        if stage == "review":
            with pytest.raises(ValueError, match="disjoint"):
                reviewed(directory, proposal)
        else:
            review = reviewed(workspace, proposal)
            with pytest.raises(ValueError, match="disjoint"):
                begin(directory / "episode", proposal["manifest_path"], review["manifest_path"],
                    expected_proposal_root=proposal["root_hash"], expected_review_root=review["root_hash"], allow_data_execution=True)
    assert hash_artifact(directory)["sha256"] == before


def test_unsealed_sibling_file_is_not_copied_as_review_evidence(workspace):
    _, _, begin, _ = api()
    proposal, _ = prepared(workspace)
    review = reviewed(workspace, proposal)
    (workspace / "proposal/extra.json").write_text('{"unreviewed": "do-not-copy"}')
    episode = begin(workspace / "episode", proposal["manifest_path"], review["manifest_path"],
        expected_proposal_root=proposal["root_hash"], expected_review_root=review["root_hash"], allow_data_execution=True)
    assert not (episode.root / "plan-review/proposal/extra.json").exists()
    episode.abort("fixture_complete")


def test_reviewed_reset_retains_review_record_and_still_needs_permission(workspace):
    _, _, begin, _ = api()
    proposal, _ = prepared(workspace)
    review = reviewed(workspace, proposal)
    episode = begin(workspace / "episode", proposal["manifest_path"], review["manifest_path"],
        expected_proposal_root=proposal["root_hash"], expected_review_root=review["root_hash"], allow_data_execution=True)
    with pytest.raises(PermissionError):
        episode.reset(workspace / "denied-reset")
    reset = episode.reset(workspace / "reset", allow_data_execution=True)
    assert reset.observation()["review_status"] == "recorded_operator_research_review"
    assert reset.review_binding_sha256 == episode.review_binding_sha256
    reset.abort("fixture_complete")


def test_reset_and_replay_cannot_write_inside_the_original_episode(workspace):
    from tests.test_analysis_episodes import create
    from devbench.analysis.episodes import replay_episode, verify_episode
    episode, _ = create(workspace)
    with pytest.raises(ValueError, match="disjoint"):
        episode.reset(episode.root / "nested-reset", allow_data_execution=True)
    assert episode.outcome is None
    episode.abort("fixture_complete")
    retained = episode.receipt["root_hash"]
    with pytest.raises(ValueError, match="disjoint"):
        replay_episode(episode.root, retained, episode.root / "nested-replay", allow_data_execution=True)
    assert verify_episode(episode.root, retained)["valid"]


@pytest.mark.parametrize("operation", ["reset", "replay"])
def test_review_cannot_disappear_after_verification_and_before_clone_binding(workspace, monkeypatch, operation):
    from devbench.analysis.episodes import AnalysisEpisode
    _, _, begin, _ = api()
    proposal, _ = prepared(workspace)
    review = reviewed(workspace, proposal)
    original = begin(workspace / "episode", proposal["manifest_path"], review["manifest_path"],
        expected_proposal_root=proposal["root_hash"], expected_review_root=review["root_hash"], allow_data_execution=True)
    if operation == "replay":
        original.abort("fixture_complete")
    create = AnalysisEpisode.create
    def remove_after_clone(cls, *args, **kwargs):
        clone = create(*args, **kwargs)
        (original.root / "review-binding.json").unlink()
        return clone
    monkeypatch.setattr(AnalysisEpisode, "create", classmethod(remove_after_clone))
    with pytest.raises((ValueError, OSError)):
        if operation == "reset":
            original.reset(workspace / "clone", allow_data_execution=True)
        else:
            original.replay(workspace / "clone", expected_root=original.receipt["root_hash"], allow_data_execution=True)
    final = json.loads((workspace / "clone/episode.json").read_text())
    assert final["outcome"] == "infrastructure_failure" and not final["harness_accepted"]
    assert final["steps"] == 0
