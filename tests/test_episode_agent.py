"""Scripted decisions exercise real small tools; these are not live agents."""
import importlib.util
import json
from pathlib import Path

import pytest

from tests.test_analysis_episodes import create, inputs, workspace


def agent_api():
    assert importlib.util.find_spec("devbench.analysis.episode_agent") is not None, "Episode action driver is missing"
    from devbench.analysis.episode_agent import AgentLimits, Selection, drive_episode
    return AgentLimits, Selection, drive_episode


class ScriptedSelector:
    def __init__(self, actions):
        self.actions, self.contexts = iter(actions), []

    def select(self, context, output, *, timeout_seconds, memory_mb, allow_model_execution=False):
        assert allow_model_execution is False
        assert 128 <= memory_mb <= 4096
        self.contexts.append(json.loads(json.dumps(context)))
        action = next(self.actions)
        if isinstance(action, BaseException):
            raise action
        _, Selection, _ = agent_api()
        return Selection(action=action, evidence={"backend": "offline_scripted_fixture",
            "model_sessions_attempted": 0, "tokens": None, "cost_usd": None, "actual_model": None})


def sequence(cellular=False):
    return [{"kind": "tool", "tool": name} for name in (
        "audit_expression_quality", "validate_biological_units",
        "pseudobulk_membership_audit" if cellular else "validate_design_matrix",
        "cell_type_state_analysis" if cellular else "mean_condition_analysis")] + [{"kind": "finish"}]


def verify(episode):
    from devbench.analysis.episodes import verify_episode
    assert verify_episode(episode.root, episode.receipt["root_hash"])["valid"]


@pytest.mark.parametrize("cellular,continuous", [(False, True), (False, False), (True, False)])
def test_driver_real_small_supported_flow(workspace, cellular, continuous):
    _, _, drive = agent_api()
    episode, paths = create(workspace, cellular=cellular, continuous=continuous)
    original = {key: path.read_bytes() for key, path in paths.items()}
    episode.private_evaluation = {"reference_answer": "DO_NOT_EXPOSE_REFERENCE"}
    selector = ScriptedSelector(sequence(cellular))
    final = drive(episode, selector, allow_data_execution=True)
    assert final["outcome"] == "completed"
    assert final["scientific_correctness"] is None
    assert len(selector.contexts) == 5
    assert selector.contexts[-1]["history"][-1]["observation"]["status"] == "succeeded"
    assert "DO_NOT_EXPOSE_REFERENCE" not in json.dumps(selector.contexts)
    assert final["agent_execution"]["selections"] == 5
    assert final["agent_execution"]["model_sessions_attempted"] == 0
    assert final["usage"]["model"] is None and final["usage"]["tokens"] is None
    assert original == {key: path.read_bytes() for key, path in paths.items()}
    assert len(list((episode.root / "agent").glob("selection-*/selection.json"))) == 5
    verify(episode)
    if continuous and not cellular:
        replay = episode.replay(workspace / "selector-journal-replay",
            expected_root=episode.receipt["root_hash"], allow_data_execution=True)
        assert replay.outcome == "completed"
        assert replay.last_result["target_results"] == episode.last_result["target_results"]
        assert replay.observation()["agent_execution"]["model_sessions_attempted"] == 0
        verify(replay)


def test_driver_diagnostic_refusal_is_separate_from_success(workspace):
    _, _, drive = agent_api()
    episode, _ = create(workspace, confounded=True)
    selector = ScriptedSelector(sequence()[:3] + [{"kind": "refuse", "reason": "Confounded design"}])
    assert drive(episode, selector, allow_data_execution=True)["outcome"] == "refusal"
    verify(episode)


def test_information_request_stops_without_inventing_a_reply_or_resetting_budget(workspace):
    _, _, drive = agent_api()
    episode, _ = create(workspace)
    selector = ScriptedSelector([{"kind": "request_information", "message": "Confirm donor independence"}, {"kind": "finish"}])
    result = drive(episode, selector, allow_data_execution=True)
    assert result["state"] == "awaiting_information" and result["outcome"] is None
    assert len(selector.contexts) == 1 and episode.receipt is None
    assert (episode.root / "agent" / "awaiting-information.json").is_file()
    with pytest.raises(ValueError, match="fresh episode"):
        drive(episode, selector, allow_data_execution=True)
    episode.abort("operator_ended_fixture")
    verify(episode)


def test_revision_receives_ordinary_diagnostics_and_preserves_fixed_options(workspace):
    _, _, drive = agent_api()
    episode, _ = create(workspace)
    selector = ScriptedSelector([
        {"kind": "tool", "tool": "mean_condition_analysis"},
        {"kind": "revise", "reason": "Run diagnostics first"}, *sequence()])
    result = drive(episode, selector, allow_data_execution=True)
    assert result["outcome"] == "completed" and result["revisions"] == 1
    diagnostic = selector.contexts[1]["history"][-1]["observation"]["diagnostic"]
    assert diagnostic.startswith("required_diagnostic_not_completed:")
    verify(episode)


@pytest.mark.parametrize("error", [RuntimeError("provider unavailable"), KeyboardInterrupt("operator interruption")])
def test_selector_failure_seals_attempt_before_return_or_reraise(workspace, error):
    _, _, drive = agent_api()
    episode, _ = create(workspace)
    selector = ScriptedSelector([error])
    if isinstance(error, KeyboardInterrupt):
        with pytest.raises(KeyboardInterrupt):
            drive(episode, selector, allow_data_execution=True)
    else:
        assert drive(episode, selector, allow_data_execution=True)["outcome"] == "infrastructure_failure"
    assert episode.outcome == "infrastructure_failure"
    assert (episode.root / "agent" / "selection-0001" / "failure.json").is_file()
    verify(episode)


def test_session_budget_exhaustion_cannot_restart_or_call_selector_again(workspace):
    Limits, _, drive = agent_api()
    episode, _ = create(workspace)
    selector = ScriptedSelector(sequence())
    final = drive(episode, selector, limits=Limits(max_selections=1), allow_data_execution=True)
    assert final["outcome"] == "infrastructure_failure"
    assert final["reason"] == "selector_decision_budget_exhausted"
    assert len(selector.contexts) == 1
    verify(episode)


def test_context_budget_rejects_before_dispatch_without_silent_truncation(workspace):
    Limits, _, drive = agent_api()
    episode, _ = create(workspace)
    selector = ScriptedSelector(sequence())
    final = drive(episode, selector, limits=Limits(max_context_bytes=1024), allow_data_execution=True)
    assert final["outcome"] == "infrastructure_failure"
    assert "context" in final["reason"]
    assert not selector.contexts
    verify(episode)


def test_malformed_selected_action_is_not_a_completed_analysis(workspace):
    _, _, drive = agent_api()
    episode, _ = create(workspace)
    result = drive(episode, ScriptedSelector([None]), allow_data_execution=True)
    assert result["outcome"] == "scientific_failure"
    assert result["reason"] == "invalid_selector_response"
    verify(episode)


def test_driver_denies_before_access_or_selection():
    _, _, drive = agent_api()
    with pytest.raises(PermissionError):
        drive(None, None)


def test_trusted_deadline_can_tighten_but_not_relax_worker_limit(workspace):
    import time
    agent_api()
    episode, _ = create(workspace)
    final = episode.step({"kind": "tool", "tool": "audit_expression_quality"}, deadline=time.monotonic()-1)
    assert final["outcome"] == "infrastructure_failure"
    assert not episode.broker.events
    verify(episode)


def test_replay_of_selector_failure_remains_sealed_without_relaunching_model(workspace):
    _, _, drive = agent_api()
    episode, _ = create(workspace)
    drive(episode, ScriptedSelector([RuntimeError("provider unavailable")]), allow_data_execution=True)
    replay = episode.replay(workspace / "replay", expected_root=episode.receipt["root_hash"], allow_data_execution=True)
    assert replay.outcome == "infrastructure_failure"
    assert replay.observation()["agent_execution"]["model_sessions_attempted"] == 0
    verify(replay)


def test_selector_memory_cap_is_forwarded_by_coordinator(workspace):
    Limits, _, drive = agent_api()
    episode, _ = create(workspace)
    class CheckMemory(ScriptedSelector):
        def select(self, context, output, **kwargs):
            assert kwargs["memory_mb"] == 128
            return super().select(context, output, **kwargs)
    selector = CheckMemory([{"kind": "request_information", "message": "Confirm design"}])
    assert drive(episode, selector, limits=Limits(memory_mb=128), allow_data_execution=True)["state"] == "awaiting_information"
    episode.abort("operator_ended_fixture")


def test_terminal_abort_cannot_modify_sealed_evidence(workspace):
    agent_api()
    episode, _ = create(workspace)
    episode.abort("fixture_interruption")
    before = (episode.root / "audit" / "ledger.json").read_bytes()
    with pytest.raises(RuntimeError, match="Terminal"):
        episode.abort("second_abort")
    assert (episode.root / "audit" / "ledger.json").read_bytes() == before
    verify(episode)


def test_damaged_selector_evidence_does_not_hide_original_failed_attempt(workspace):
    _, _, drive = agent_api()
    episode, _ = create(workspace)
    class DamagedCapture:
        def select(self, context, output, **kwargs):
            (output / "evidence.json").write_text("{damaged capture")
            raise TimeoutError("provider process interrupted")
    final = drive(episode, DamagedCapture(), allow_data_execution=True)
    assert final["outcome"] == "infrastructure_failure"
    assert (episode.root / "agent/selection-0001/evidence.json").read_text() == "{damaged capture"
    assert final["agent_execution"]["model_sessions_attempted"] is None
    verify(episode)


def test_invalid_selector_accounting_cannot_produce_success_or_break_sealing(workspace):
    _, Selection, drive = agent_api()
    episode, _ = create(workspace)
    class InvalidAccounting:
        def select(self, context, output, **kwargs):
            return Selection({"kind": "finish"}, {"model_sessions_attempted": True, "tokens": -10})
    final = drive(episode, InvalidAccounting(), allow_data_execution=True)
    assert final["outcome"] == "infrastructure_failure"
    assert final["usage"]["tokens"] is None
    verify(episode)


def test_selector_observes_its_actual_decision_budget_separately_from_tool_budget(workspace):
    Limits, _, drive = agent_api()
    episode, _ = create(workspace)
    selector = ScriptedSelector([{"kind": "request_information", "message": "Confirm experimental units"}])
    drive(episode, selector, limits=Limits(max_selections=3, timeout_seconds=30), allow_data_execution=True)
    context = selector.contexts[0]
    assert context["observation"]["steps_remaining"] == 32
    assert context["selection_budget"]["decisions_remaining"] == 3
    assert 0 < context["selection_budget"]["seconds_remaining"] <= 30
    assert context["selection_budget"]["memory_mb"] == 2048
    episode.abort("operator_ended_fixture")


def test_context_write_failure_after_a_selection_seals_prior_execution(workspace, monkeypatch):
    _, _, drive = agent_api()
    from devbench.analysis import episode_agent
    episode, _ = create(workspace)
    selector = ScriptedSelector(sequence())
    original = episode_agent.atomic_json
    def fail_context(path, value):
        if path.parent.name == "selection-0002" and path.name == "context.json":
            raise OSError("injected second-selection context write failure")
        return original(path, value)
    monkeypatch.setattr(episode_agent, "atomic_json", fail_context)
    result = drive(episode, selector, allow_data_execution=True)
    assert result["outcome"] == "infrastructure_failure"
    assert len(selector.contexts) == 1
    assert (episode.root / "agent/selection-0002/failure.json").is_file()
    verify(episode)


def test_no_selector_dispatch_after_episode_action_budget_is_exhausted(workspace):
    Limits, _, drive = agent_api()
    from devbench.analysis.episodes import EpisodeLimits
    episode, _ = create(workspace, limits=EpisodeLimits(max_steps=1))
    selector = ScriptedSelector(sequence())
    result = drive(episode, selector, limits=Limits(max_selections=2), allow_data_execution=True)
    assert result["outcome"] == "scientific_failure"
    assert result["reason"] == "action_budget_exhausted"
    assert len(selector.contexts) == 1
    assert not (episode.root / "agent/selection-0002").exists()
    verify(episode)
