"""Small local continuation fixtures; no model participants or clinical data."""
import importlib.util
import json
from pathlib import Path

import pytest

from tests.test_analysis_episodes import create, workspace
from tests.test_episode_agent import ScriptedSelector, sequence, verify


REPLY = {"message": "Continue with the already declared design.", "preserves_frozen_inputs_and_settings": True}


def api():
    assert importlib.util.find_spec("devbench.analysis.episode_handoff") is not None, "Persisted episode handoff is missing"
    from devbench.analysis.episode_handoff import save_handoff, resume_handoff
    return save_handoff, resume_handoff


def paused(root, *, cellular=False, continuous=None):
    from devbench.analysis.episode_agent import drive_episode
    episode, _ = create(root, cellular=cellular, continuous=not cellular if continuous is None else continuous)
    choices = sequence(cellular)[:2] + [{"kind": "request_information", "message": "Confirm the intended interpretation"}]
    result = drive_episode(episode, ScriptedSelector(choices), allow_data_execution=True)
    assert result["state"] == "awaiting_information"
    return episode, result["handoff"]


@pytest.mark.parametrize("cellular,continuous", [(False, True), (False, False), (True, False)])
def test_restore_keeps_prior_results_budgets_and_receipts_without_recomputation(workspace, cellular, continuous):
    _, resume = api()
    from devbench.analysis.episode_agent import drive_episode
    from devharness_core.provenance import verify_execution
    episode, handoff = paused(workspace, cellular=cellular, continuous=continuous)
    root = episode.root
    old_id, old_snapshot = episode.instance_id, episode.snapshot_id
    old_events = list(episode.broker.events)
    old_bytes = episode.broker.evidence_bytes
    old_directory = episode.broker.output_dir
    old_root_bytes = (old_directory / "root.json").read_bytes()
    del episode
    restored = resume(root, handoff["manifest_path"], expected_root=handoff["root_hash"], response=REPLY, allow_data_execution=True)
    assert restored.instance_id == old_id and restored.snapshot_id == old_snapshot
    assert restored.broker.output_dir != old_directory
    assert restored.broker.events == old_events
    assert len(restored.results) == 2
    assert restored.broker.evidence_bytes >= old_bytes
    assert restored.revisions == 1 and len(restored.actions) == 3
    selector = ScriptedSelector(sequence(cellular)[2:])
    result = drive_episode(restored, selector, allow_data_execution=True)
    assert result["outcome"] == "completed"
    assert result["agent_execution"]["selections"] == 6
    assert selector.contexts[0]["selection_budget"]["decisions_remaining"] == 5
    assert selector.contexts[0]["history"][-1]["operator_message"] == REPLY["message"]
    assert len(restored.broker.events) == 4  # Past diagnostics were not rerun.
    assert (old_directory / "root.json").read_bytes() == old_root_bytes
    assert verify_execution(handoff["manifest_path"], handoff["root_hash"])["valid"]
    verify(restored)
    if not cellular and continuous:
        replay = restored.replay(workspace / "replayed-continuation", expected_root=restored.receipt["root_hash"], allow_data_execution=True)
        assert replay.outcome == "completed"
        assert replay.last_result["target_results"] == restored.last_result["target_results"]
        assert replay.revisions == restored.revisions
        verify(replay)


def test_resume_requires_data_authorization_before_any_path_access():
    _, resume = api()
    with pytest.raises(PermissionError):
        resume(None, None, expected_root=None, response=None)


@pytest.mark.parametrize("response", [
    {"message": "Change counts to normalized values", "preserves_frozen_inputs_and_settings": False},
    {**REPLY, "measurement": "continuous"}, {**REPLY, "grader_feedback": "pass"},
])
def test_metadata_or_evaluator_updates_cannot_be_smuggled_through_clarification(workspace, response):
    _, resume = api()
    episode, handoff = paused(workspace)
    with pytest.raises(ValueError):
        resume(episode.root, handoff["manifest_path"], expected_root=handoff["root_hash"], response=response, allow_data_execution=True)
    assert not list(episode.root.glob(".audit-resume-*.json"))
    episode.abort("operator_ended_fixture")


@pytest.mark.parametrize("target", ["parameters.json", "agent/policy.json", "checkpoints/action-0001/observation.json"])
def test_changed_working_files_are_rejected_before_claim(workspace, target):
    _, resume = api()
    episode, handoff = paused(workspace)
    path = episode.root / target
    original = path.read_bytes()
    path.write_bytes(original + b" ")
    with pytest.raises(ValueError, match="changed|inventory"):
        resume(episode.root, handoff["manifest_path"], expected_root=handoff["root_hash"], response=REPLY, allow_data_execution=True)
    assert not list(episode.root.glob(".audit-resume-*.json"))


def test_expired_deadline_seals_failure_without_another_selector_or_tool(workspace, monkeypatch):
    _, resume = api()
    from devbench.analysis import episode_handoff
    episode, handoff = paused(workspace)
    state = json.loads((Path(handoff["manifest_path"]).parent.parent / "state.json").read_text())
    monkeypatch.setattr(episode_handoff, "_wall", lambda: state["driver"]["deadline_utc"] + 1)
    restored = resume(episode.root, handoff["manifest_path"], expected_root=handoff["root_hash"], response=REPLY, allow_data_execution=True)
    assert restored.outcome == "infrastructure_failure"
    assert json.loads((restored.root / "episode.json").read_text())["reason"] == "handoff_deadline_exhausted"
    assert restored.observation()["agent_execution"]["selections"] == 3
    assert not list(restored.root.glob("tool-evidence-resume-*"))
    verify(restored)


def test_same_handoff_cannot_create_two_continuations(workspace):
    _, resume = api()
    episode, handoff = paused(workspace)
    restored = resume(episode.root, handoff["manifest_path"], expected_root=handoff["root_hash"], response=REPLY, allow_data_execution=True)
    with pytest.raises((ValueError, FileExistsError, RuntimeError)):
        resume(episode.root, handoff["manifest_path"], expected_root=handoff["root_hash"], response=REPLY, allow_data_execution=True)
    restored.abort("operator_ended_fixture")
    verify(restored)


def test_restored_agent_budget_cannot_be_replaced(workspace):
    _, resume = api()
    from devbench.analysis.episode_agent import AgentLimits, drive_episode
    episode, handoff = paused(workspace)
    restored = resume(episode.root, handoff["manifest_path"], expected_root=handoff["root_hash"], response=REPLY, allow_data_execution=True)
    with pytest.raises(ValueError, match="frozen|budget"):
        drive_episode(restored, ScriptedSelector(sequence()[2:]), limits=AgentLimits(max_selections=32), allow_data_execution=True)
    restored.abort("operator_ended_fixture")


def test_broker_deadline_does_not_gain_time_spent_restoring(workspace, monkeypatch):
    _, resume = api()
    import time
    from devbench.study.biology_broker import BiologyBroker
    episode, handoff = paused(workspace)
    original_deadline = episode.broker.started + episode.broker.policy.timeout_sec
    original_init = BiologyBroker.__init__
    def delayed_init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        time.sleep(.05)  # Simulate real setup work; must consume, not renew, time.
    monkeypatch.setattr(BiologyBroker, "__init__", delayed_init)
    restored = resume(episode.root, handoff["manifest_path"], expected_root=handoff["root_hash"], response=REPLY, allow_data_execution=True)
    assert restored.broker.started + restored.broker.policy.timeout_sec <= original_deadline + .01
    restored.abort("operator_ended_fixture")


def test_failed_information_notice_is_sealed_not_left_open(workspace, monkeypatch):
    api()
    from devbench.analysis import episode_agent
    original = episode_agent.atomic_json
    def fail_notice(path, value):
        if path.name == "awaiting-information.json":
            raise OSError("injected information-notice failure")
        return original(path, value)
    monkeypatch.setattr(episode_agent, "atomic_json", fail_notice)
    episode, _ = create(workspace)
    result = episode_agent.drive_episode(episode, ScriptedSelector(sequence()[:1] + [
        {"kind": "request_information", "message": "Confirm interpretation"}]), allow_data_execution=True)
    assert result["outcome"] == "infrastructure_failure"
    verify(episode)


def test_reset_of_checkpointed_episode_terminates_it_without_damaging_checkpoint(workspace):
    api()
    from devharness_core.provenance import verify_execution
    episode, handoff = paused(workspace)
    reset = episode.reset(workspace / "reset", allow_data_execution=True)
    assert reset.state == "ready" and not reset.actions
    assert reset.config["parent_root"] == episode.receipt["root_hash"]
    assert verify_execution(handoff["manifest_path"], handoff["root_hash"])["valid"]
    verify(episode)


def test_second_handoff_preserves_both_checkpoints_and_remaining_decisions(workspace):
    _, resume = api()
    from devbench.analysis.episode_agent import drive_episode
    from devharness_core.provenance import verify_execution
    episode, first = paused(workspace)
    second_episode = resume(episode.root, first["manifest_path"], expected_root=first["root_hash"], response=REPLY, allow_data_execution=True)
    second = drive_episode(second_episode, ScriptedSelector([sequence()[2],
        {"kind": "request_information", "message": "Confirm output interpretation"}]), allow_data_execution=True)["handoff"]
    final_episode = resume(episode.root, second["manifest_path"], expected_root=second["root_hash"], response=REPLY, allow_data_execution=True)
    selector = ScriptedSelector(sequence()[3:])
    assert drive_episode(final_episode, selector, allow_data_execution=True)["outcome"] == "completed"
    assert final_episode.revisions == 2 and len(final_episode.agent_selections) == 7
    assert selector.contexts[0]["selection_budget"]["decisions_remaining"] == 3
    for checkpoint in [first, second]:
        assert verify_execution(checkpoint["manifest_path"], checkpoint["root_hash"])["valid"]
    verify(final_episode)


def test_uncheckpointed_future_stage_cannot_be_injected(workspace):
    _, resume = api()
    episode, handoff = paused(workspace)
    extra = episode.root / "checkpoints/action-0004"
    extra.mkdir()
    (extra / "receipt.json").write_text("{}")
    with pytest.raises(ValueError, match="inventory"):
        resume(episode.root, handoff["manifest_path"], expected_root=handoff["root_hash"], response=REPLY, allow_data_execution=True)
    assert not list(episode.root.glob(".audit-resume-*.json"))


def test_clock_rollback_is_not_a_budget_extension(workspace, monkeypatch):
    _, resume = api()
    from devbench.analysis import episode_handoff
    episode, handoff = paused(workspace)
    state = json.loads((Path(handoff["manifest_path"]).parent.parent / "state.json").read_text())
    monkeypatch.setattr(episode_handoff, "_wall", lambda: state["saved_at_utc"]-1)
    with pytest.raises(ValueError, match="clock"):
        resume(episode.root, handoff["manifest_path"], expected_root=handoff["root_hash"], response=REPLY, allow_data_execution=True)
    assert not list(episode.root.glob(".audit-resume-*.json"))


def test_failed_prefix_copy_preserves_cumulative_charge_and_marks_capture_incomplete(workspace):
    save, resume = api()
    from tests.test_analysis_episodes import run_contrast
    from devbench.study.evidence import verify_receipts
    episode, _ = create(workspace)
    run_contrast(episode)
    episode.step({"kind": "request_information", "message": "Confirm interpretation"})
    # Simulate previously charged storage without creating a 128 MB padding file.
    episode.broker.evidence_bytes = episode.broker.policy.max_evidence_bytes - 2000
    handoff = save(episode)
    old_receipt = episode.broker.final_receipt
    used = episode.broker.evidence_bytes
    restored = resume(episode.root, handoff["manifest_path"], expected_root=handoff["root_hash"], response=REPLY, allow_data_execution=True)
    assert restored.outcome == "infrastructure_failure"
    final = json.loads((episode.root / "episode.json").read_text())
    assert final["broker_receipt"]["capture_complete"] is False
    assert final["broker_receipt"]["event_count"] == 4
    assert restored.broker.evidence_bytes >= used
    verified = verify_receipts(episode.broker.output_dir, expected_receipt=old_receipt,
        expected_input_sha256=episode.snapshot_id, expected_implementation_sha256=episode.source_id, allow_evidence_read=True)
    assert len(verified.events) == 4
    verify(restored)


def test_scientific_abort_keeps_its_type_across_pending_handoff(workspace):
    api()
    episode, _ = paused(workspace)
    assert episode.abort("operator_reports_unsupported_analysis", scientific=True)["outcome"] == "scientific_failure"
    verify(episode)


def test_evaluation_registration_survives_without_exposing_private_context(workspace):
    _, resume = api()
    from devbench.analysis.episode_agent import drive_episode
    episode, _ = create(workspace)
    episode.register_evaluation("ab" * 32)  # Commitment fixture; no evaluator or golden is read.
    episode.private_evaluation = {"reference_answer": "PRIVATE_CONTEXT_SENTINEL"}
    identity = episode.instance_id
    selector = ScriptedSelector([{"kind": "request_information", "message": "Confirm interpretation"}])
    handoff = drive_episode(episode, selector, allow_data_execution=True)["handoff"]
    restored = resume(episode.root, handoff["manifest_path"], expected_root=handoff["root_hash"], response=REPLY, allow_data_execution=True)
    assert restored.instance_id == identity and restored.evaluation_plan_sha256 == "ab" * 32
    assert not hasattr(restored, "private_evaluation")
    assert "PRIVATE_CONTEXT_SENTINEL" not in json.dumps(selector.contexts)
    assert "PRIVATE_CONTEXT_SENTINEL" not in (Path(handoff["manifest_path"]).parent.parent / "state.json").read_text()
    restored.abort("operator_ended_fixture")
    verify(restored)


def test_concurrent_episode_continuations_return_only_one_writer(workspace):
    _, resume = api()
    from concurrent.futures import ThreadPoolExecutor
    episode, handoff = paused(workspace)
    def attempt():
        try:
            return resume(episode.root, handoff["manifest_path"], expected_root=handoff["root_hash"], response=REPLY, allow_data_execution=True)
        except (ValueError, OSError, RuntimeError):
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: attempt(), range(2)))
    winners = [result for result in results if result is not None]
    assert len(winners) == 1 and winners[0].state == "ready"
    winners[0].abort("operator_ended_fixture")
    verify(winners[0])


def test_invalid_explicit_deadline_is_not_converted_into_a_terminal_failure(workspace):
    api()
    import time
    episode, _ = create(workspace)
    episode.controller_deadline = time.monotonic()+30
    with pytest.raises(ValueError, match="deadline"):
        episode.step({"kind": "tool", "tool": "audit_expression_quality"}, deadline=True)
    assert episode.outcome is None and not episode.actions
    episode.abort("operator_ended_fixture")
