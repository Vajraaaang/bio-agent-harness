"""Offline method-planner selector protocol; no provider calls or OS qualification."""

import json
from pathlib import Path

import pytest

from tests.test_episode_codex import fixture_adapter, setup_selector_protocol, staged, workspace


PURPOSES = [
    ("method_scientific_planning", "SCIENTIFIC_SCHEMA", "SCIENTIFIC_PLANNER_INSTRUCTIONS"),
    ("method_capability_resolution", "RESOLUTION_SCHEMA", "CAPABILITY_PLANNER_INSTRUCTIONS"),
    ("method_recommendation", "RECOMMENDATION_SCHEMA", "RECOMMENDATION_INSTRUCTIONS"),
    ("method_trial_design", "TRIAL_DESIGN_SCHEMA", "TRIAL_DESIGN_INSTRUCTIONS"),
    ("method_implementation_draft", "IMPLEMENTATION_SCHEMA", "IMPLEMENTATION_INSTRUCTIONS"),
]
ANSWERS = {
    "method_scientific_planning": {"candidates": []},
    "method_capability_resolution": {"bindings": []},
    "method_recommendation": {"choice": None, "confidence": "no_recommendation",
                              "scientific_confidence": "no_recommendation", "implementation_confidence": "unknown",
                              "rationale": "No valid trial evidence yet", "params": {}, "approval_actions": []},
    "method_trial_design": {"experiments": []},
    "method_implementation_draft": {"source_code": "raise RuntimeError('DRAFT_MUST_NOT_EXECUTE')\n",
                                    "algorithm_summary": "Synthetic draft, not validated",
                                    "introduced_choices": [], "source_references": []},
}


def _planner_adapter(root, monkeypatch, purpose, extra_events=()):
    from devbench.analysis.episode_codex import EpisodeCodexAdapter
    base = fixture_adapter(root, monkeypatch, extra_events=extra_events)
    return EpisodeCodexAdapter(boundary=base.boundary, requested_model="fixture-model", purpose=purpose,
                               output=base.output, executable=base.executable, timeout_sec=5, max_mem_mb=256)


@pytest.mark.parametrize("purpose,_,__", PURPOSES)
def test_method_planner_adapter_requires_explicit_authorization(workspace, monkeypatch, purpose, _, __):
    adapter = _planner_adapter(workspace, monkeypatch, purpose)
    with pytest.raises(PermissionError, match="explicit"):
        adapter.execute(None, None)
    assert not adapter.output.exists()


@pytest.mark.parametrize("purpose,_,__", PURPOSES)
def test_method_planner_adapter_cannot_place_answers_in_actor_workspace(workspace, monkeypatch, purpose, _, __):
    from devbench.analysis.episode_codex import EpisodeCodexAdapter
    base = fixture_adapter(workspace, monkeypatch)
    with pytest.raises(ValueError, match="private"):
        EpisodeCodexAdapter(boundary=base.boundary, requested_model="fixture-model", purpose=purpose,
                            output=base.workspace / "answer")


@pytest.mark.parametrize("purpose,_,__", PURPOSES)
@pytest.mark.parametrize("kind", ["command_execution", "file_change", "mcp_tool_call", "web_search"])
def test_each_method_planner_purpose_rejects_tool_activity(workspace, monkeypatch, purpose, _, __, kind):
    adapter = _planner_adapter(workspace, monkeypatch, purpose, extra_events=[{
        "type": "item.started", "item": {"id": "forbidden", "type": kind},
    }])
    with pytest.raises(RuntimeError, match="selector.*tool|capture"):
        adapter.execute("Inspect the supplied context only", adapter.workspace, allow_model_execution=True)
    assert kind in (adapter.output / "cli-records.jsonl").read_text()
    metadata = json.loads((adapter.output / "metadata.json").read_text())
    assert metadata["event_capture_complete"] is False
    assert metadata["execution_role"] == "scientific_method_planner_selector"
    assert metadata["tool_evidence_source"] == "no_scientific_execution_during_planning"


def _context(purpose):
    value = {"decision_kind": purpose, "question": "How should these QC tails be assessed?"}
    if purpose == "method_capability_resolution":
        value.update(frozen_candidates=[{"candidate_id": "qc-4mad"}], available_method_contracts=[])
    elif purpose == "method_recommendation":
        value.update(frozen_plan={"plan_id": "fixture-plan"}, actual_run_evidence=[])
    elif purpose in {"method_trial_design", "method_implementation_draft"}:
        value.update(frozen_candidates=[{"candidate_id": "qc-4mad"}], available_method_contracts=[])
    return value


def _capture_bound_schema(monkeypatch):
    from devbench.analysis.episode_codex import EpisodeCodexAdapter
    original = EpisodeCodexAdapter._command
    bound = []

    def command(self, executable, actor, schema, output, resume):
        result = original(self, executable, actor, schema, output, resume)
        if self._phase != "exec_preflight":
            bound.append({"schema": json.loads(Path(schema).read_bytes()), "command": result,
                          "actor": str(actor), "schema_path": str(schema)})
        return result

    monkeypatch.setattr(EpisodeCodexAdapter, "_command", command)
    return bound


@pytest.mark.parametrize("purpose,schema_name,prompt_name", PURPOSES)
def test_method_modes_bind_their_real_schema_and_reject_an_episode_answer(staged, monkeypatch, purpose, schema_name, prompt_name):
    from devbench.analysis.episode_agent import InvalidSelection
    from devbench_engine.adapters import strict_schema_compatible
    selector, output, probes, calls = setup_selector_protocol(staged, monkeypatch, answer={"kind": "finish"})
    bound = _capture_bound_schema(monkeypatch)
    context = _context(purpose)
    with pytest.raises(InvalidSelection):
        selector._select(context, output, timeout_seconds=5)
    if purpose in {"method_trial_design", "method_implementation_draft"}:
        from devbench.analysis import method_authoring_contract as contract
    else:
        from devbench.analysis import method_planner_contract as contract
    expected = getattr(contract, schema_name)
    assert len(probes) == len(calls) == len(bound) == 1
    actor = Path(bound[0]["actor"])
    assert json.loads((actor / "answer_schema.json").read_bytes()) == expected
    assert bound[0]["schema"] == expected
    assert json.loads((actor / "control" / "context.json").read_bytes()) == context
    assert getattr(contract, prompt_name) in calls[0]["instruction"]
    assert json.dumps(expected, ensure_ascii=False, allow_nan=False) in calls[0]["instruction"]
    if purpose == "method_scientific_planning":
        assert "available_method_contracts" not in calls[0]["instruction"]
    command = bound[0]["command"]
    if strict_schema_compatible(expected):
        assert command[command.index("--output-schema") + 1] == bound[0]["schema_path"]
    else:
        assert "--output-schema" not in command
    assert "--strict-config" in command and "--ephemeral" in command
    assert not any("mcp_servers" in part for part in command)
    assert not actor.is_relative_to(selector.private_root)
    assert not selector.private_root.is_relative_to(actor)
    assert not (actor / "solve.py").exists()
    evidence = json.loads((output / "evidence.json").read_bytes())
    assert evidence["scope"] == "exploratory_method_planning_not_scientific_approval"
    assert evidence["model_sessions_attempted"] == 2
    assert evidence["selection"]["execution_role"] == "scientific_method_planner_selector"
    enforcement = "provider_and_local" if strict_schema_compatible(expected) else "local"
    assert evidence["selection"]["turns"][-1]["schema_enforcement"] == enforcement


@pytest.mark.parametrize("purpose,_,__", PURPOSES)
def test_method_planner_selector_authorization_precedes_staging(workspace, purpose, _, __):
    from devbench.analysis.episode_codex import CodexActionSelector
    selector = CodexActionSelector(requested_model="fixture-model", workspace_parent=workspace / "actors")
    with pytest.raises(PermissionError):
        selector.select(_context(purpose), workspace / "output", timeout_seconds=5)
    assert not (workspace / "actors").exists()


@pytest.mark.parametrize("purpose,_,__", PURPOSES)
def test_method_planner_valid_response_preserves_provider_identity_and_budget_evidence(staged, monkeypatch, purpose, _, __):
    answer = ANSWERS[purpose]
    selector, output, probes, calls = setup_selector_protocol(staged, monkeypatch, answer=answer)
    selection = selector._select(_context(purpose), output, timeout_seconds=5)
    assert selection.action == answer
    assert len(probes) == len(calls) == 1
    evidence = selection.evidence
    assert evidence["scope"] == "exploratory_method_planning_not_scientific_approval"
    assert evidence["model_sessions_attempted"] == 2
    assert evidence["tokens"] == 60
    assert evidence["actual_model"] == "fixture-model"
    assert evidence["model_identity_verified"] is True
    assert evidence["selection"]["event_capture_complete"] is True
    assert evidence["selection"]["execution_role"] == "scientific_method_planner_selector"
    assert evidence["selection"]["benchmark_condition"] is None
    assert evidence["selection"]["tool_evidence_source"] == "no_scientific_execution_during_planning"
    assert (output / "exec-preflight" / "evidence.json").is_file()
    assert (output / "selection-dispatch.json").is_file()


@pytest.mark.parametrize("purpose,_,__", PURPOSES)
def test_method_planner_failed_preflight_cannot_launch_the_planning_turn(staged, monkeypatch, purpose, _, __):
    selector, output, probes, calls = setup_selector_protocol(staged, monkeypatch, preflight_ok=False,
                                                             answer=ANSWERS[purpose])
    with pytest.raises(RuntimeError, match="preflight_failed"):
        selector._select(_context(purpose), output, timeout_seconds=5)
    assert len(probes) == 1 and calls == []
    evidence = json.loads((output / "evidence.json").read_bytes())
    assert evidence["model_sessions_attempted"] == 1
    assert evidence["model_identity_verified"] is False


@pytest.mark.parametrize("purpose,_,__", PURPOSES)
def test_method_planner_provider_identity_change_is_not_accepted(staged, monkeypatch, purpose, _, __):
    selector, output, probes, calls = setup_selector_protocol(staged, monkeypatch, action_model="different-fixture",
                                                             answer=ANSWERS[purpose])
    with pytest.raises(RuntimeError, match="model_identity_changed"):
        selector._select(_context(purpose), output, timeout_seconds=5)
    assert len(probes) == len(calls) == 1
    assert json.loads((output / "evidence.json").read_bytes())["model_identity_verified"] is False


def test_implementation_text_is_returned_without_execution_or_actor_source_files(staged, monkeypatch):
    answer = ANSWERS["method_implementation_draft"]
    selector, output, probes, calls = setup_selector_protocol(staged, monkeypatch, answer=answer)
    selection = selector._select(_context("method_implementation_draft"), output, timeout_seconds=5)
    assert selection.action["source_code"] == answer["source_code"]
    assert "Return proposed source code as JSON text only" in calls[0]["instruction"]
    assert "write code, or seek" not in calls[0]["instruction"]
    actor = Path(selection.evidence["workspace"])
    assert not (actor / "main.py").exists() and not (actor / "draft.py").exists()
    assert len(probes) == len(calls) == 1
    assert selection.evidence["selection"]["tool_evidence_source"] == "no_scientific_execution_during_planning"
