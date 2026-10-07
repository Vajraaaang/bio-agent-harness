"""Study launch configuration and guidance only; no data, tools, or model turns."""
import importlib.util
from pathlib import Path
import tomllib

import pytest

from devbench.study.permissions import PermissionBoundary


WORKSPACE = Path("/opt/devbench/participants/run_1")


def module():
    assert importlib.util.find_spec("devbench.study.adapter"), "Study adapter module is missing"
    from devbench.study import adapter
    return adapter


def adapter(condition="baseline", **kwargs):
    assisted = condition in {"optional", "enforced"}
    boundary = PermissionBoundary(
        workspace=WORKSPACE,
        private_roots=(Path("/opt/devbench/coordinator"),),
        runtime_roots=(Path("/opt/python/lib"),),
        protected_paths=(WORKSPACE / "inputs", WORKSPACE / "control"),
        socket_path=WORKSPACE / "control" / "tools.sock" if assisted else None,
    )
    return module().StudyCodeActionAdapter(
        boundary=boundary, requested_model="pinned-model-2026-09-11", condition=condition,
        tool_capability_file=WORKSPACE / "control" / "capability" if assisted else None,
        **kwargs,
    )


def launch_configuration(instance):
    command = instance._command("/opt/codex", WORKSPACE, WORKSPACE / "answer_schema.json",
                                WORKSPACE / "agent_final_message.txt", None)
    settings = tomllib.loads("\n".join(command[index + 1] for index, value in enumerate(command)
                                      if value == "-c"))
    return command, settings


@pytest.mark.parametrize("condition", ["baseline", "optional", "enforced"])
def test_all_conditions_use_exact_model_and_named_strict_boundary(condition):
    instance = adapter(condition, reasoning_effort="high")
    command, settings = launch_configuration(instance)
    assert instance.artifact_max_bytes == 8_000_000
    assert command[command.index("--model") + 1] == "pinned-model-2026-09-11"
    assert settings["model_reasoning_effort"] == "high"
    assert settings["default_permissions"] == "devbench_study"
    assert settings["approval_policy"] == "never"
    assert settings["skills"]["include_instructions"] is False
    assert settings["allow_login_shell"] is False
    assert settings["features"]["shell_snapshot"] is False
    assert settings["permissions"]["devbench_study"]["filesystem"][":root"] == "deny"
    assert "--strict-config" in command and "--ignore-rules" in command
    assert command.count("--ignore-user-config") == 1
    assert not {"--sandbox", "--approve-for-me", "--full-auto"}.intersection(command)
    assert not any("mcp_servers" in value or "dangerously" in value for value in command)
    assert instance.condition == condition


def test_unspecified_reasoning_effort_does_not_claim_or_override_it():
    _, settings = launch_configuration(adapter())
    assert "model_reasoning_effort" not in settings


@pytest.mark.parametrize("condition", ["baseline", "optional", "enforced"])
def test_child_commands_receive_only_explicit_execution_environment(condition, monkeypatch):
    monkeypatch.setenv("UNRELATED_SECRET", "configuration-sentinel")
    monkeypatch.setenv("PYTHONPATH", "/ambient/python")
    monkeypatch.setenv("DEVHARNESS_TOOL_SOCKET", "/ambient/tools.sock")
    _, settings = launch_configuration(adapter(condition, thread_limit=2))
    shell = settings["shell_environment_policy"]
    assert shell["inherit"] == "none"
    assert set(shell["include_only"]) == set(shell["set"])
    environment = shell["set"]
    assert "UNRELATED_SECRET" not in environment and "PYTHONPATH" not in environment
    assert "HOME" not in environment and "CODEX_HOME" not in environment
    assert environment["PATH"] == str(WORKSPACE / ".venv/bin") + ":/usr/bin:/bin:/usr/sbin:/sbin"
    assert environment["DEVHARNESS_PYTHON"] == str(WORKSPACE / ".venv/bin/python")
    assert environment["TMPDIR"] == str(WORKSPACE / ".tmp")
    assert environment["PYTHONNOUSERSITE"] == "1"
    for name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
                 "NUMEXPR_NUM_THREADS", "VECLIB_MAXIMUM_THREADS"):
        assert environment[name] == "2"
    tool_keys = {"DEVHARNESS_TOOL_SOCKET", "DEVHARNESS_TOOL_CAPABILITY_FILE"}
    if condition == "baseline":
        assert not tool_keys.intersection(environment)
    else:
        assert environment["DEVHARNESS_TOOL_SOCKET"] == str(WORKSPACE / "control/tools.sock")
        assert environment["DEVHARNESS_TOOL_CAPABILITY_FILE"] == str(WORKSPACE / "control/capability")


def test_parent_login_environment_preserves_managed_sandbox_settings(monkeypatch):
    monkeypatch.setenv("CODEX_SANDBOX", "managed-sentinel")
    monkeypatch.setenv("CODEX_PERMISSION_PROFILE", "inherited-profile")
    monkeypatch.setenv("HOME", "/existing-login-home")
    monkeypatch.setenv("DEVHARNESS_TOOL_CAPABILITY_FILE", "/ambient/capability")
    environment = adapter()._environment()
    assert environment["CODEX_SANDBOX"] == "managed-sentinel"
    assert environment["CODEX_PERMISSION_PROFILE"] == "devbench_study"
    assert environment["HOME"] == "/existing-login-home"
    assert "DEVHARNESS_TOOL_CAPABILITY_FILE" not in environment


@pytest.mark.parametrize("value", [None, "", "  model", "model\nother", "-other-flag"])
def test_missing_or_ambiguous_requested_model_is_rejected(value):
    with pytest.raises(ValueError, match="model"):
        module().StudyCodeActionAdapter(boundary=PermissionBoundary(workspace=WORKSPACE),
                                       requested_model=value)


def test_baseline_cannot_receive_a_tool_endpoint():
    boundary = PermissionBoundary(workspace=WORKSPACE, socket_path=WORKSPACE / "control/tools.sock")
    with pytest.raises(ValueError, match="baseline"):
        module().StudyCodeActionAdapter(boundary=boundary, requested_model="pinned")


def test_assisted_condition_requires_a_protected_capability_file():
    boundary = PermissionBoundary(workspace=WORKSPACE, socket_path=WORKSPACE / "control/tools.sock")
    with pytest.raises(ValueError, match="capability"):
        module().StudyCodeActionAdapter(boundary=boundary, requested_model="pinned", condition="optional")
    with pytest.raises(ValueError, match="protected"):
        module().StudyCodeActionAdapter(boundary=boundary, requested_model="pinned", condition="optional",
                                       tool_capability_file=WORKSPACE / "unprotected-capability")


@pytest.mark.parametrize("kwargs", [{"reasoning_effort": "guess"}, {"thread_limit": 0},
                                     {"thread_limit": True}, {"condition": "assisted"}])
def test_invalid_study_configuration_is_rejected(kwargs):
    kwargs = dict(kwargs)
    condition = kwargs.pop("condition", "baseline")
    with pytest.raises(ValueError):
        adapter(condition, **kwargs)


def test_command_cannot_silently_change_model_or_workspace_or_resume():
    instance = adapter()
    instance.model = "different-model"
    with pytest.raises(ValueError, match="model"):
        launch_configuration(instance)
    instance = adapter()
    with pytest.raises(ValueError, match="workspace"):
        instance._command("/opt/codex", Path("/elsewhere"), WORKSPACE / "answer_schema.json",
                          WORKSPACE / "agent_final_message.txt", None)
    with pytest.raises(ValueError, match="resume"):
        instance._command("/opt/codex", WORKSPACE, WORKSPACE / "answer_schema.json",
                          WORKSPACE / "agent_final_message.txt", "existing-session")


def test_execute_is_disabled_before_filesystem_or_cli_work(monkeypatch):
    from devbench.adapters.code_action import CodeActionAdapter
    monkeypatch.setattr(CodeActionAdapter, "execute", lambda *args: pytest.fail("Base launch reached"))
    with pytest.raises(PermissionError, match="disabled"):
        adapter().execute("Do not run", Path("/not-a-workspace"))


def test_execution_proof_still_uses_live_code_action_capture(monkeypatch):
    from devbench.adapters.code_action import CodeActionAdapter
    cls = module().StudyCodeActionAdapter
    assert cls._observe_script_execution is CodeActionAdapter._observe_script_execution
    captured = []
    monkeypatch.setattr(CodeActionAdapter, "_event", lambda self, event: captured.append(event))
    event = {"type": "thread.started", "thread_id": "protocol-only"}
    adapter()._event(event)
    assert captured == [event]


def capture_protocol(instance, monkeypatch, events, *, complete=True, returncode=0, error=None):
    """Exercise event plumbing with a replaced process method; never spawn a CLI."""
    from devbench_engine.adapters import CodexCLIAdapter

    def captured_process(self, *args, **kwargs):
        for event in events:
            self._event(event)
        self.metadata["event_capture_complete"] = self.metadata.get("event_capture_complete", True) and complete
        if error is not None:
            raise error
        return returncode, "", ""

    monkeypatch.setattr(CodexCLIAdapter, "_run_process", captured_process)
    instance._execution_authorized = True
    return instance._run_process(["/unused/codex", "exec"], WORKSPACE, {}, "", 1)


def test_requested_model_is_never_observed_identity():
    instance = adapter()
    assert instance.metadata["actual_model"] is None
    assert instance.metadata["model_ids"] == []
    assert instance.metadata["model_identity_verified"] is False


@pytest.mark.parametrize("usage_matches", [True, False])
def test_native_completion_identity_requires_cli_usage_correlation(monkeypatch, usage_matches):
    instance=adapter()
    capture_protocol(instance,monkeypatch,[{"type":"thread.started","thread_id":"session-1"},
        {"type":"turn.completed","usage":{"input_tokens":20,"output_tokens":10}}])
    assert instance.metadata["model_identity_verified"] is False
    class Capture:
        def close(self,session_id,usage):
            assert session_id=="session-1" and usage=={"input_tokens":20,"output_tokens":10}
            return {"capture_complete":True,"usage_matches_cli":usage_matches,"completion_model":"runtime-model"}
    instance._model_capture=Capture()
    instance._stop_model_capture()
    assert instance.metadata["model_identity_verified"] is usage_matches
    assert instance.metadata["actual_model"] == ("runtime-model" if usage_matches else None)


def test_native_capture_secret_stays_out_of_child_environment(monkeypatch):
    from types import SimpleNamespace
    from devbench.study.model_telemetry import TOKEN_ENV
    instance=adapter()
    instance._model_capture=SimpleNamespace(capability="private-capture-capability")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT","https://unrelated.invalid")
    environment=instance._environment()
    assert environment[TOKEN_ENV]=="private-capture-capability"
    assert "OTEL_EXPORTER_OTLP_ENDPOINT" not in environment
    assert TOKEN_ENV not in instance._child_environment()


def test_only_provider_completion_model_is_identity_evidence(monkeypatch):
    instance = adapter()
    capture_protocol(instance, monkeypatch, [
        {"type": "item.completed", "model": "untrusted-top-item", "item": {
            "type": "agent_message", "model": "participant-supplied",
            "text": '{"model": "participant-text"}'}},
        {"type": "turn.completed", "model": "provider-reported-model"},
    ])
    assert instance.metadata["actual_model"] == "provider-reported-model"
    assert instance.metadata["model_ids"] == ["provider-reported-model"]
    assert instance.metadata["model_identity_verified"] is True
    assert instance.metadata["model_identity_origin"] == "codex_cli_turn_completed_model"
    assert instance.metadata["requested_model"] == "pinned-model-2026-09-11"
    assert instance.metadata["model_identity_scope"] == "CLI-reported model identifier; no cryptographic attestation"


@pytest.mark.parametrize("events", [
    [],
    [{"type": "turn.completed"}],
    [{"type": "turn.completed", "model": "provider-a"},
     {"type": "turn.completed", "model": "provider-b"}],
    [{"type": "item.completed", "item": {"type": "agent_message", "model": "untrusted"}}],
])
def test_absent_or_conflicting_provider_model_remains_unknown(monkeypatch, events):
    instance = adapter()
    capture_protocol(instance, monkeypatch, events)
    assert instance.metadata["actual_model"] is None
    assert instance.metadata["model_identity_verified"] is False


@pytest.mark.parametrize("complete,returncode", [(False, 0), (None, 0), (True, 1)])
def test_partial_or_failed_capture_cannot_verify_model(monkeypatch, complete, returncode):
    instance = adapter()
    capture_protocol(instance, monkeypatch,
                     [{"type": "turn.completed", "model": "provider-a"}],
                     complete=complete, returncode=returncode)
    assert instance.metadata["actual_model"] == "provider-a"
    assert instance.metadata["model_identity_verified"] is False


@pytest.mark.parametrize("event", [
    {"type": "error", "message": "protocol-only error"},
    {"type": "turn.failed", "error": "protocol-only failure"},
    {"type": "turn.completed", "model": None},
    {"type": "turn.completed", "model": "x" * 301},
])
def test_event_errors_or_missing_model_completion_prevent_verification(monkeypatch, event):
    instance = adapter()
    capture_protocol(instance, monkeypatch,
                     [{"type": "turn.completed", "model": "provider-a"}, event])
    assert instance.metadata["model_identity_verified"] is False


def test_reader_error_after_model_event_cannot_verify_identity(monkeypatch):
    instance = adapter()
    with pytest.raises(RuntimeError, match="capture failed"):
        capture_protocol(instance, monkeypatch,
                         [{"type": "turn.completed", "model": "provider-a"}],
                         error=RuntimeError("capture failed"))
    assert instance.metadata["model_identity_verified"] is False


def test_provider_model_event_capture_is_bounded(monkeypatch):
    instance = adapter()
    limit = module().MODEL_IDENTITY_EVENT_LIMIT
    with pytest.raises(RuntimeError, match="model identity.*limit"):
        capture_protocol(instance, monkeypatch,
                         [{"type": "turn.completed", "model": "provider-a"}] * (limit + 1))
    assert len(instance.metadata["model_identity_observations"]) == limit
    assert instance.metadata["model_identity_verified"] is False


def test_dispatch_callback_is_config_only_until_authorized_model_process(monkeypatch):
    from devbench_engine.adapters import CodexCLIAdapter
    checkpoints = []
    instance = adapter(coordinator_dispatch_callback=checkpoints.append)
    launch_configuration(instance)
    assert checkpoints == []
    monkeypatch.setattr(CodexCLIAdapter, "_run_process", lambda *args, **kwargs: (0, "", ""))
    instance._run_process(["/unused/codex", "login", "status"], WORKSPACE, {}, "", 1,
                          parse_events=False)
    assert checkpoints == []
    with pytest.raises(PermissionError, match="disabled"):
        instance._run_process(["/unused/codex", "exec"], WORKSPACE, {}, "", 1)
    assert checkpoints == []
    capture_protocol(instance, monkeypatch, [])
    assert len(checkpoints) == 1
    assert checkpoints[0]["stage"] == "model_dispatch"
    assert checkpoints[0]["requested_model"] == "pinned-model-2026-09-11"
    assert checkpoints[0]["condition"] == "baseline"
    assert checkpoints[0]["origin"] == "coordinator_before_codex_cli_exec"
    assert set(checkpoints[0]) == {"stage", "requested_model", "condition", "origin", "timestamp"}


def test_dispatch_callback_failure_stops_before_process_and_keeps_identity_unverified(monkeypatch):
    from devbench_engine.adapters import CodexCLIAdapter

    def failed_checkpoint(event):
        raise OSError("checkpoint unavailable")

    instance = adapter(coordinator_dispatch_callback=failed_checkpoint)
    instance._execution_authorized = True
    # CodeActionAdapter sets this before calling the study process wrapper.
    instance.metadata["inference_dispatch_attempted"] = True
    monkeypatch.setattr(CodexCLIAdapter, "_run_process", lambda *args, **kwargs: pytest.fail("Process reached"))
    with pytest.raises(RuntimeError, match="checkpoint failed before CLI launch"):
        instance._run_process(["/unused/codex", "exec"], WORKSPACE, {}, "", 1)
    assert instance.metadata["model_identity_verified"] is False
    assert instance.metadata["inference_dispatch_attempted"] is False
    assert instance.metadata["dispatch_stage"] == "checkpoint_failed_before_launch"


def test_successful_checkpoint_marks_attempt_only_when_process_invocation_begins(monkeypatch):
    from devbench_engine.adapters import CodexCLIAdapter
    observed = []
    instance = adapter(coordinator_dispatch_callback=lambda event: observed.append(
        ("checkpoint", instance.metadata["inference_dispatch_attempted"])))
    instance._execution_authorized = True
    instance.metadata["inference_dispatch_attempted"] = True

    def process(self, *args, **kwargs):
        observed.append(("process", self.metadata["inference_dispatch_attempted"]))
        raise OSError("launch attempted but executable unavailable")

    monkeypatch.setattr(CodexCLIAdapter, "_run_process", process)
    with pytest.raises(OSError, match="launch attempted"):
        instance._run_process(["/unused/codex", "exec"], WORKSPACE, {}, "", 1)
    assert observed == [("checkpoint", False), ("process", True)]
    assert instance.metadata["inference_dispatch_attempted"] is True
    assert instance.metadata["dispatch_stage"] == "model_launch_attempted"


def test_noncallable_dispatch_callback_is_rejected():
    with pytest.raises(ValueError, match="callback"):
        adapter(coordinator_dispatch_callback="not-a-function")


def test_guidance_shares_science_and_keeps_optional_adoption_voluntary():
    assert importlib.util.find_spec("devbench.study.guidance"), "Study guidance module is missing"
    from devbench.study.guidance import COMMON_GUIDANCE, participant_guidance, sdk_cheatsheet
    for condition in ("baseline", "optional", "enforced"):
        assert COMMON_GUIDANCE in participant_guidance(condition)
        assert "answer_schema.json" in participant_guidance(condition)
        assert "four" not in participant_guidance(condition)
        compile(sdk_cheatsheet(condition), "sdk_cheatsheet.py", "exec")
    optional = " ".join(participant_guidance("optional").split())
    assert "no tool use" in optional and "hybrid" in optional
    assert "no direct correctness reward" in optional and "correct" in optional
    assert "devharness_client" not in sdk_cheatsheet("baseline")
    assert "devharness_client" in sdk_cheatsheet("optional")
    enforced = participant_guidance("enforced")
    for name in ("fit_family", "audit_quality", "validate_design", "receipt_id", "tool_analysis_id"):
        assert name in enforced
    assert "inspect_study_design" in enforced and "blocking" in enforced
    assisted = sdk_cheatsheet("enforced")
    for name in ("formula_str", "family_q_value", "no_information", "reference_batch",
                 "batch_reference_policy", "biological_unit_reference"):
        assert name in assisted


def test_guidance_rejects_unknown_conditions():
    from devbench.study.guidance import participant_guidance, sdk_cheatsheet
    for function in (participant_guidance, sdk_cheatsheet):
        with pytest.raises(ValueError, match="condition"):
            function("assisted")
