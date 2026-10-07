"""CLI protocol fixtures only; no OpenAI request or real sandbox qualification."""
import importlib.util
import json
from pathlib import Path
import sys

import pytest

from tests.test_analysis_episodes import workspace
from tests.test_exec_preflight import staged, fake_process


def codex_api():
    assert importlib.util.find_spec("devbench.analysis.episode_codex") is not None, "Codex episode selector is missing"
    from devbench.analysis.episode_codex import EpisodeCodexAdapter, CodexActionSelector, action_schema
    return EpisodeCodexAdapter, CodexActionSelector, action_schema


def fixture_adapter(root, monkeypatch, answer='{"kind":"finish"}', extra_events=()):
    Adapter, _, schema = codex_api()
    from devbench.study.permissions import PermissionBoundary
    from devbench.study.adapter import StudyCodeActionAdapter
    monkeypatch.setattr(StudyCodeActionAdapter, "_start_model_capture", lambda *args: None)
    actor, private = root / "actor", root / "private"
    (actor / "control").mkdir(parents=True)
    (actor / ".venv/bin").mkdir(parents=True)
    (actor / ".venv/bin/python").symlink_to(sys.executable)
    private.mkdir()
    (actor / "answer_schema.json").write_text(json.dumps(schema(["audit_expression_quality"])))
    fake = root / "fixture-codex"
    fake.write_text(f'''#!{sys.executable}
import json,sys
from pathlib import Path
if sys.argv[1:3] == ["login","status"]:
    print("Logged in using ChatGPT (offline CLI fixture)")
elif sys.argv[1] == "exec":
    print(json.dumps({{"type":"thread.started","thread_id":"fixture-session"}}))
    Path(sys.argv[sys.argv.index("--output-last-message")+1]).write_text({answer!r})
    for record in {list(extra_events)!r}: print(json.dumps(record))
    print(json.dumps({{"type":"turn.completed","model":"fixture-model","usage":{{"input_tokens":20,"output_tokens":10}}}}))
else:
    raise SystemExit(2)
''')
    fake.chmod(0o700)
    boundary = PermissionBoundary(workspace=actor, private_roots=(private,),
        protected_paths=(actor / "control", actor / ".venv", actor / "answer_schema.json"))
    return Adapter(boundary=boundary, requested_model="fixture-model", output=private / "selection",
        executable=str(fake), timeout_sec=5, max_mem_mb=256)


def test_adapter_default_denies_before_login_or_path_access(workspace, monkeypatch):
    adapter = fixture_adapter(workspace, monkeypatch)
    with pytest.raises(PermissionError):
        adapter.execute(None, None)
    assert not adapter.output.exists()


def test_adapter_reuses_codex_json_protocol_without_solve_script(workspace, monkeypatch):
    adapter = fixture_adapter(workspace, monkeypatch)
    action = adapter.execute("Choose a supported action", adapter.workspace, allow_model_execution=True)
    assert action == {"kind": "finish"}
    assert not (adapter.workspace / "solve.py").exists()
    assert not (adapter.workspace / "final_discovery.json").exists()
    assert json.loads((adapter.output / "answer.json").read_text()) == action
    assert (adapter.output / "cli-records.jsonl").is_file()
    assert adapter.metadata["model_identity_verified"] is True
    assert adapter.metadata["tokens"] == 30
    assert adapter.metadata["execution_role"] == "episode_action_selector"
    assert adapter.metadata["benchmark_condition"] is None
    command = adapter.metadata["cli_command"]
    assert "--ephemeral" in command and "--strict-config" in command
    assert "--approve-for-me" not in command
    assert not any("mcp_servers" in part for part in command)


@pytest.mark.parametrize("answer", ['{"kind":"finish","kind":"refuse"}', '{"kind":"finish","reference_answer":{}}', 'not JSON'])
def test_malformed_cli_answer_is_preserved_in_private_storage(workspace, monkeypatch, answer):
    adapter = fixture_adapter(workspace, monkeypatch, answer)
    from devbench.analysis.episode_agent import InvalidSelection
    with pytest.raises(InvalidSelection):
        adapter.execute("Choose an action", adapter.workspace, allow_model_execution=True)
    assert (adapter.output / "answer.json").read_text() == answer
    assert adapter.metadata["inference_dispatch_attempted"] is True
    assert (adapter.output / "metadata.json").is_file()
    assert adapter.metadata["response_validation_status"] == "invalid_response"
    timing = json.loads((adapter.output / "stream-timing.json").read_text())
    assert timing["completion_observed"] is True
    assert timing["first_event_elapsed_sec"] <= timing["last_event_elapsed_sec"]


def test_private_output_rejects_actor_writable_location(workspace, monkeypatch):
    adapter = fixture_adapter(workspace, monkeypatch)
    Adapter, _, _ = codex_api()
    with pytest.raises(ValueError, match="private"):
        Adapter(boundary=adapter.boundary, requested_model="fixture-model", output=adapter.workspace / "answer")


def test_selector_denies_before_staging_or_model_execution(workspace):
    _, Selector, _ = codex_api()
    selector = Selector(requested_model="fixture-model", workspace_parent=workspace / "actors")
    with pytest.raises(PermissionError):
        selector.select({}, workspace / "output", timeout_seconds=5)
    assert not (workspace / "actors").exists()


def test_selector_preserves_preallocated_sibling_denials_in_supervised_configuration(workspace):
    _, Selector, _ = codex_api()
    assert "denied_roots" in __import__("inspect").signature(Selector).parameters, \
        "Sibling-root denial cannot be bound before launch"
    private, sibling = workspace / "private", workspace / "sibling"
    selector = Selector(requested_model="fixture-model", workspace_parent=workspace / "actors",
        private_root=private, denied_roots=(sibling,))
    assert selector.configuration()["denied_roots"] == [str(sibling)]


def test_registered_investigation_instruction_does_not_fall_back_to_episode_action():
    from devbench.analysis import episode_codex
    assert hasattr(episode_codex, "selection_instruction"), "Exact prompt binding helper is missing"
    with pytest.raises(ValueError, match="Unregistered"):
        episode_codex.selection_instruction({"decision_kind": "rna_unknown_investigator"})


def test_path_bound_context_overflow_blocks_before_provider_preflight(staged,monkeypatch):
    from devbench.analysis.episode_codex import selection_instruction
    from devbench.analysis.rna_investigation_contract import investigation_context
    from tests.test_rna_investigation_contract import packet,result
    p=packet()
    context=investigation_context(p)
    selector,output,probes,actions=setup_selector_protocol(staged,monkeypatch,answer=result(p,transported=False))
    assert "max_context_bytes" in __import__("inspect").signature(selector.select).parameters, "Native full-prompt bound is missing"
    selector.max_context_bytes=len(selection_instruction(context).encode())
    with pytest.raises(ValueError,match="context.*byte"):
        selector._select(context,output,timeout_seconds=5)
    assert not probes and not actions


def test_adapter_binds_exact_instruction_bytes_used_for_actual_process(workspace, monkeypatch):
    import hashlib
    from devbench_engine.adapters import CodexCLIAdapter
    original = CodexCLIAdapter._run_process
    observed = []
    def inspect_prompt(self, command, workspace, environment, instruction, *args, **kwargs):
        if len(command) > 1 and command[1] == "exec":
            observed.append(instruction)
        return original(self, command, workspace, environment, instruction, *args, **kwargs)
    monkeypatch.setattr(CodexCLIAdapter, "_run_process", inspect_prompt)
    adapter = fixture_adapter(workspace, monkeypatch)
    adapter.execute("Exact packet wrapper", adapter.workspace, allow_model_execution=True)
    assert adapter.metadata.get("provider_instruction_sha256") == hashlib.sha256(observed[-1].encode()).hexdigest()


def test_preflight_final_message_has_actor_read_only_protection(staged, monkeypatch):
    from devbench.study.exec_preflight import run_exec_preflight
    instance, output = staged
    calls = fake_process(monkeypatch)
    result = run_exec_preflight(instance, output, allow_model_execution=True)
    assert result["verified"] is True
    command = calls[0]["command"]
    target = Path(command[command.index("--output-last-message")+1])
    assert any(target.is_relative_to(path) for path in instance.boundary.protected_paths), "CLI final output is actor-writable"


def test_real_selector_supervisor_stops_before_scored_launch(workspace):
    _, Selector, _ = codex_api()
    private = workspace / "private"
    output = private / "selection"
    output.mkdir(parents=True)
    selector = Selector(requested_model="fixture-model", workspace_parent=workspace / "actors",
        private_root=private, executable=str(workspace / "missing-fixture-cli"))
    from devharness_core.watchdog import HarnessResourceInterrupt
    with pytest.raises(HarnessResourceInterrupt):
        selector.select({"observation": {"allowed_tools": []}, "history": []}, output,
            timeout_seconds=.001, memory_mb=128, allow_model_execution=True)
    evidence = json.loads((output / "evidence.json").read_text())
    assert evidence["supervision"]["supervisor_status"] == "failed"
    assert evidence["model_sessions_attempted"] == 0
    assert not (output / "selection-dispatch.json").exists()


@pytest.mark.parametrize("kind", ["command_execution", "file_change", "mcp_tool_call", "web_search", "unknown_new_tool"])
def test_selector_rejects_non_action_tool_activity_and_preserves_record(workspace, monkeypatch, kind):
    adapter = fixture_adapter(workspace, monkeypatch, extra_events=[{
        "type": "item.started", "item": {"id": "x", "type": kind}}])
    with pytest.raises(RuntimeError, match="selector.*tool|capture"):
        adapter.execute("Choose one action", adapter.workspace, allow_model_execution=True)
    assert kind in (adapter.output / "cli-records.jsonl").read_text()
    assert adapter.metadata["event_capture_complete"] is False


def setup_selector_protocol(staged, monkeypatch, *, preflight_ok=True, action_model="fixture-model", answer=None):
    """Replace provider/OS evidence explicitly; test coordinator routing only."""
    instance, old_output = staged
    _, Selector, _ = codex_api()
    from devbench import staging
    from devbench.study import execution, permissions
    from devbench_engine.adapters import CodexCLIAdapter
    def stage(path, *, assisted):
        assert assisted is False
        (path / ".venv/bin").mkdir(parents=True)
        (path / ".venv/bin/python").symlink_to(sys.executable)
        return {"python": str(path / ".venv/bin/python"), "runtime_source_hashes": {}, "linked_dependency_entries": []}
    monkeypatch.setattr(staging, "stage_python", stage)
    monkeypatch.setattr(execution, "_runtime_roots", lambda *args: ())
    monkeypatch.setattr(permissions, "probe_boundary", lambda boundary, *args, **kwargs: {
        "verified": True, "configuration": boundary.config_overrides(), "evidence_scope": "offline_protocol_fixture"})
    preflight_calls = fake_process(monkeypatch, complete=preflight_ok)
    preflight_run = CodexCLIAdapter._run_process
    action_calls = []
    def run(self, command, workspace, environment, instruction, timeout_sec, parse_events=True):
        if not parse_events:
            return 0, "Logged in using ChatGPT (offline fixture)", ""
        if self._phase == "exec_preflight":
            return preflight_run(self, command, workspace, environment, instruction, timeout_sec, parse_events)
        action_calls.append({"command": command, "instruction": instruction})
        Path(command[command.index("--output-last-message")+1]).write_text(json.dumps(answer or {"kind": "finish"}))
        self._event({"type": "thread.started", "thread_id": "fixture-action"})
        self._event({"type": "turn.completed", "model": action_model, "usage": {"input_tokens": 20, "output_tokens": 10}})
        self.metadata["event_capture_complete"] = True
        return 0, "", ""
    monkeypatch.setattr(CodexCLIAdapter, "_run_process", run)
    private = old_output.parent
    output = private / "selector"
    output.mkdir()
    selector = Selector(requested_model="fixture-model", workspace_parent=instance.workspace.parent,
        private_root=private, executable=sys.executable)
    return selector, output, preflight_calls, action_calls


def test_selector_routes_permission_preflight_before_action_and_accounts_both(staged, monkeypatch):
    selector, output, probes, actions = setup_selector_protocol(staged, monkeypatch)
    choice = selector._select({"observation": {"allowed_tools": []}, "history": []}, output, timeout_seconds=5)
    assert choice.action == {"kind": "finish"}
    assert len(probes) == len(actions) == 1
    assert choice.evidence["model_sessions_attempted"] == 2  # simulated dispatches only
    assert choice.evidence["tokens"] == 60
    assert choice.evidence["model_identity_verified"] is True
    assert "private_truth" not in actions[0]["instruction"]
    assert (output / "exec-preflight/evidence.json").is_file()
    assert (output / "selection-dispatch.json").is_file()
    timing = json.loads((output / "phase-timing.json").read_text())
    assert timing["protocol"] == "devbench.selection-timing/1"
    assert timing["status"] == "completed"
    assert timing["error_classification"] is None
    assert {row["phase"] for row in timing["phases"]} >= {
        "schema_context", "local_setup", "permission_probe", "provider_preflight",
        "instruction_preparation", "provider_action", "response_validation", "cleanup"}
    assert all(row["duration_sec"] >= 0 for row in timing["phases"])
    assert timing["timeout_seconds"] == 5
    assert timing["budget"]["deadline_exhausted"] is False


def test_failed_permission_preflight_never_launches_action(staged, monkeypatch):
    selector, output, probes, actions = setup_selector_protocol(staged, monkeypatch, preflight_ok=False)
    with pytest.raises(RuntimeError, match="preflight_failed"):
        selector._select({"observation": {"allowed_tools": []}, "history": []}, output, timeout_seconds=5)
    assert len(probes) == 1 and not actions
    assert json.loads((output / "evidence.json").read_text())["model_sessions_attempted"] == 1
    timing = json.loads((output / "phase-timing.json").read_text())
    assert timing["status"] == "failed"
    assert timing["failure_phase"] == "provider_preflight"
    assert timing["error_classification"] == "provider_preflight_failed"


def test_supervisor_retains_invalid_response_failure(staged, monkeypatch):
    from devharness_core import watchdog
    from devbench.analysis.episode_agent import InvalidSelection
    selector, output, probes, actions = setup_selector_protocol(staged, monkeypatch,
        answer={"kind": "unknown"})

    def supervised(worker, args, **kwargs):
        kwargs["diagnostics"].update(supervisor_status="completed")
        return worker(*args)

    monkeypatch.setattr(watchdog, "run_sandboxed", supervised)
    with pytest.raises(InvalidSelection):
        selector.select({"observation": {"allowed_tools": []}, "history": []}, output,
            timeout_seconds=5, allow_model_execution=True)
    timing = json.loads((output / "phase-timing.json").read_text())
    assert timing["status"] == "failed"
    assert timing["error_classification"] == "invalid_response"
    assert len(probes) == len(actions) == 1
    assert timing["budget"]["deadline_seconds"] == 5
    assert timing["budget"]["remaining_seconds"] >= 0


def test_cancelled_selection_has_distinct_classification(workspace, monkeypatch):
    from devharness_core import watchdog
    _, Selector, _ = codex_api()
    output = workspace / "private" / "cancelled"
    output.mkdir(parents=True)
    selector = Selector(requested_model="fixture-model", workspace_parent=workspace / "actors",
        private_root=output.parent)
    calls = []

    def cancelled(*args, **kwargs):
        calls.append(1)
        raise watchdog.WorkerCancelled("Offline coordinator cancellation")

    monkeypatch.setattr(watchdog, "run_sandboxed", cancelled)
    with pytest.raises(watchdog.WorkerCancelled):
        selector.select({}, output, timeout_seconds=5, allow_model_execution=True)
    timing = json.loads((output / "phase-timing.json").read_text())
    assert timing["status"] == "failed"
    assert timing["error_classification"] == "cancelled"
    assert len(calls) == 1


def test_offline_slow_stream_records_payload_free_checkpoints(workspace, monkeypatch):
    import time
    adapter = fixture_adapter(workspace, monkeypatch, extra_events=[{
        "type": "item.started", "item": {"id": "x", "type": "agent_message", "text": "secret-fixture-text"}}])
    executable = Path(adapter.executable)
    source = executable.read_text().replace("import json,sys", "import json,sys,time")
    source = source.replace("for record in", "sys.stdout.flush()\n    time.sleep(.12)\n    for record in")
    executable.write_text(source)
    phases = []
    adapter.phase_callback = phases.append
    started = time.monotonic()
    adapter.execute("Choose an action", adapter.workspace, allow_model_execution=True)
    timing_path = adapter.output / "stream-timing.json"
    timing = json.loads(timing_path.read_text())
    assert time.monotonic() - started >= .12
    assert timing["last_event_elapsed_sec"] - timing["first_event_elapsed_sec"] >= .10
    assert "secret-fixture-text" not in timing_path.read_text()
    assert "provider_action_process_startup" in phases
    assert "provider_action_wait" in phases
    assert "provider_action_stream" in phases
    assert "provider_action_process_cleanup" in phases


def test_timing_unknown_event_does_not_copy_untrusted_text(workspace, monkeypatch):
    adapter = fixture_adapter(workspace, monkeypatch,
        extra_events=[{"type": "secret-fixture-event-text"}])
    with pytest.raises(RuntimeError):
        adapter.execute("Choose an action", adapter.workspace, allow_model_execution=True)
    timing = (adapter.output / "stream-timing.json").read_text()
    assert "secret-fixture-event-text" not in timing


def test_cleanup_telemetry_failure_does_not_leave_process_alive(workspace, monkeypatch):
    import psutil
    adapter = fixture_adapter(workspace, monkeypatch)
    script = Path(adapter.executable)
    script.write_text(script.read_text().replace("import json,sys", "import json,sys,os").replace(
        'elif sys.argv[1] == "exec":',
        'elif sys.argv[1] == "exec":\n    Path("fixture-provider.pid").write_text(str(os.getpid()))'))
    def telemetry(phase):
        if phase == "provider_action_process_cleanup":
            raise OSError("offline timing storage failure")
    adapter.phase_callback = telemetry
    with pytest.raises(RuntimeError, match="lifecycle capture failed"):
        adapter.execute("Choose an action", adapter.workspace, allow_model_execution=True)
    pid = int((adapter.workspace / "fixture-provider.pid").read_text())
    assert not psutil.pid_exists(pid)


def test_local_setup_failure_is_retained_and_does_not_dispatch(staged, monkeypatch):
    from devbench import staging
    selector, output, probes, actions = setup_selector_protocol(staged, monkeypatch)
    def failed_setup(*args, **kwargs):
        raise RuntimeError("offline staging failure")
    monkeypatch.setattr(staging, "stage_python", failed_setup)
    with pytest.raises(RuntimeError, match="offline staging failure"):
        selector._select({"observation": {"allowed_tools": []}}, output, timeout_seconds=5)
    timing = json.loads((output / "phase-timing.json").read_text())
    assert timing["status"] == "failed"
    assert timing["error_classification"] == "local_setup_failed"
    assert timing["failure_phase"] == "local_setup"
    assert not probes and not actions


def controlled_selection_wait(configuration, context, output, timeout_seconds, previous_workspaces):
    """Importable offline worker; no CLI/provider or scientific execution."""
    import subprocess
    import time
    from devbench.analysis.episode_codex import _SelectionTiming
    timing = _SelectionTiming(output, timeout_seconds)
    timing.advance(context["phase"])
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(20)"])
    (output / "controlled-child.pid").write_text(str(child.pid))
    while True:
        time.sleep(.02)


@pytest.mark.parametrize("phase", ["local_setup", "provider_action_stream"])
def test_deadline_retains_phase_and_stops_controlled_descendant(workspace, monkeypatch, phase):
    import psutil
    from devbench.analysis import episode_codex
    from devharness_core.watchdog import HarnessResourceInterrupt
    _, Selector, _ = codex_api()
    output = workspace / "private" / "deadline"
    output.mkdir(parents=True)
    selector = Selector(requested_model="fixture-model", workspace_parent=workspace / "actors",
        private_root=output.parent)
    monkeypatch.setattr(episode_codex, "_selection_worker", controlled_selection_wait)
    with pytest.raises(HarnessResourceInterrupt, match="timed out|deadline"):
        selector.select({"phase": phase}, output, timeout_seconds=2, memory_mb=512,
            allow_model_execution=True)
    evidence = json.loads((output / "evidence.json").read_text())
    timing = evidence["phase_timing"]
    assert timing["status"] == "failed"
    assert timing["failure_phase"] == phase
    assert timing["error_classification"] == "deadline_exhausted"
    assert timing["budget"]["remaining_seconds"] == 0
    assert timing["budget"]["deadline_seconds"] == 2
    assert evidence["model_sessions_attempted"] == 0
    assert evidence["supervision"]["cleanup"]["verified_stopped"] is True
    pid = int((output / "controlled-child.pid").read_text())
    if psutil.pid_exists(pid):
        assert psutil.Process(pid).status() == psutil.STATUS_ZOMBIE


def test_coordinator_cancellation_stops_active_worker_without_retry(workspace, monkeypatch):
    import threading
    import time
    from devbench.analysis import episode_codex
    from devharness_core.watchdog import WorkerCancelled, cancel_workers_on
    _, Selector, _ = codex_api()
    output = workspace / "private" / "cancel-active"
    output.mkdir(parents=True)
    selector = Selector(requested_model="fixture-model", workspace_parent=workspace / "actors",
        private_root=output.parent)
    monkeypatch.setattr(episode_codex, "_selection_worker", controlled_selection_wait)
    stop = threading.Event()
    def cancel_after_startup():
        until = time.monotonic() + 4
        while not (output / "controlled-child.pid").exists() and time.monotonic() < until:
            time.sleep(.01)
        stop.set()
    canceller = threading.Thread(target=cancel_after_startup)
    canceller.start()
    try:
        with cancel_workers_on(stop), pytest.raises(WorkerCancelled):
            selector.select({"phase": "provider_action_stream"}, output,
                timeout_seconds=5, memory_mb=512, allow_model_execution=True)
    finally:
        canceller.join(timeout=5)
    evidence = json.loads((output / "evidence.json").read_text())
    assert evidence["phase_timing"]["error_classification"] == "cancelled"
    assert evidence["phase_timing"]["budget"]["deadline_exhausted"] is False
    assert evidence["supervision"]["supervisor_status"] == "cancelled"
    assert evidence["supervision"]["cleanup"]["verified_stopped"] is True
    assert evidence["model_sessions_attempted"] == 0


def test_changed_provider_model_is_not_accepted(staged, monkeypatch):
    selector, output, probes, actions = setup_selector_protocol(staged, monkeypatch, action_model="different-fixture")
    with pytest.raises(RuntimeError, match="model_identity_changed"):
        selector._select({"observation": {"allowed_tools": []}, "history": []}, output, timeout_seconds=5)
    assert len(probes) == len(actions) == 1
    assert json.loads((output / "evidence.json").read_text())["model_identity_verified"] is False


def test_proposal_selector_reuses_probes_and_records_its_distinct_role(staged, monkeypatch):
    answer = {"kind": "propose", "workflow": "bulk_mean_contrast", "reference_condition": "A",
              "comparison_condition": "B", "rationale": "Offline proposal protocol fixture"}
    selector, output, probes, calls = setup_selector_protocol(staged, monkeypatch, answer=answer)
    context = {"decision_kind": "analysis_proposal", "allowed_workflows": ["bulk_mean_contrast"],
               "observed": {"condition_levels": ["A", "B"]}, "question": "B minus A"}
    selection = selector._select(context, output, timeout_seconds=5)
    assert selection.action == answer
    assert len(probes) == len(calls) == 1
    assert selection.evidence["model_sessions_attempted"] == 2  # simulated protocol events
    assert selection.evidence["selection"]["execution_role"] == "episode_proposal_selector"
    assert selection.evidence["selection"]["tool_evidence_source"] == "no_scientific_execution_during_planning"
    assert "cannot approve itself" in calls[0]["instruction"]
    assert json.loads((output / "model/metadata.json").read_text())["condition"] == "proposal_selector"


def test_proposal_response_cannot_pass_as_episode_action(staged, monkeypatch):
    from devbench.analysis.episode_agent import InvalidSelection
    selector, output, _, _ = setup_selector_protocol(staged, monkeypatch,
        answer={"kind": "propose", "workflow": "bulk_mean_contrast", "reference_condition": "A",
                "comparison_condition": "B", "rationale": "Wrong protocol"})
    with pytest.raises(InvalidSelection):
        selector._select({"observation": {"allowed_tools": []}, "history": []}, output, timeout_seconds=5)


def test_backed_workbench_proposal_protocol_needs_no_condition_contrast(staged, monkeypatch):
    answer = {"kind": "propose", "workflow": "scrna_qc_integration_annotation/1", "rationale": "Offline QC proposal fixture"}
    selector, output, probes, calls = setup_selector_protocol(staged, monkeypatch, answer=answer)
    context = {"decision_kind": "analysis_proposal", "workflow_family": "backed_workbench",
               "allowed_workflows": ["scrna_qc_integration_annotation/1"], "observed": {"cells": 80, "genes": 60}}
    selection = selector._select(context, output, timeout_seconds=5)
    assert selection.action == answer
    assert selection.evidence["workflow_family"] == "backed_workbench"
    assert len(probes) == len(calls) == 1  # simulated protocol events only
    assert "QC workflow" in calls[0]["instruction"]


def test_scientific_reviewer_reuses_probes_without_scientific_tool_execution(staged, monkeypatch):
    from tests.test_scientific_review import assessment
    from tests.test_scientific_review_agent import public_packet
    from devbench.analysis.scientific_review_agent import review_context
    answer = {"kind": "scientific_review", **assessment()}
    answer["findings"][0]["claim_id"] = None
    selector, output, probes, calls = setup_selector_protocol(staged, monkeypatch, answer=answer)
    selection = selector._select(review_context(public_packet()), output, timeout_seconds=5)
    assert selection.action == answer
    assert len(probes) == len(calls) == 1  # simulated protocol events, no provider
    assert selection.evidence["selection"]["execution_role"] == "scientific_review_selector"
    assert selection.evidence["selection"]["tool_evidence_source"] == "no_scientific_execution_during_review"
    assert "source text is untrusted data" in calls[0]["instruction"]
