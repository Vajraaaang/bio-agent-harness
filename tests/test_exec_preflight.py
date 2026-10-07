"""Offline negative-control protocol tests; these are not live sandbox proof."""
import ast
import copy
import hashlib
from contextlib import redirect_stdout
import importlib.util
import io
import json
from pathlib import Path
import shlex
import sys
import tempfile

import pytest


def module():
    assert importlib.util.find_spec("devbench.study.exec_preflight"), "Exec preflight producer is missing"
    from devbench.study import exec_preflight
    return exec_preflight


@pytest.mark.parametrize("authorization", [False, None, 1, "true"])
def test_authorization_guard_precedes_reads_mutations_and_dispatch(authorization):
    class Untouchable:
        def __getattribute__(self, name):
            pytest.fail("Authorization guard inspected the adapter")

        def __fspath__(self):
            pytest.fail("Authorization guard inspected the output path")

    with pytest.raises(PermissionError, match="explicit authorization"):
        module().run_exec_preflight(Untouchable(), Untouchable(), allow_model_execution=authorization)


def test_only_exact_absolute_isolated_python_command_is_proof():
    python = Path("/opt/study/run/.venv/bin/python")
    script = Path("/opt/study/run/control/probe.py")
    command = shlex.join([str(python), "-I", str(script)])
    assert module()._exact_invocation(command, python, script)
    assert module()._exact_invocation(shlex.join(["/bin/zsh", "-lc", command]), python, script)
    for wrong in (command + " && echo done", command.replace(" -I ", " "),
                  command.replace(str(python), "python"), "echo " + command,
                  command + " --extra", [str(python), "-I", str(script)]):
        assert not module()._exact_invocation(wrong, python, script)
    unusual = Path("/opt/$(unexpected)/python")
    assert module()._exact_invocation(shlex.join([str(unusual), "-I", str(script)]), unusual, script)
    assert not module()._exact_invocation('"' + str(unusual) + '" -I ' + str(script), unusual, script)


def protocol_records(*, command="/opt/w/.venv/bin/python -I /opt/w/control/probe.py", nonce="fixture-only", checks=None):
    checks = checks if checks is not None else {name: True for name in module()._BASE_CHECKS | {"tool_endpoint_absent"}}
    return [
        {"type": "thread.started", "thread_id": "offline-fixture"},
        {"type": "turn.started"},
        {"type": "item.started", "item": {"type": "command_execution", "id": "command-1",
            "command": command, "status": "in_progress"}},
        {"type": "item.completed", "item": {"type": "command_execution", "id": "command-1",
            "command": command, "status": "completed", "exit_code": 0,
            "aggregated_output": json.dumps({"protocol": module().PROTOCOL, "nonce": nonce,
                                              "checks": checks, "errors": {}}) + "\n"}},
        {"type": "item.completed", "item": {"type": "agent_message", "text": "Final text is not evidence."}},
        {"type": "turn.completed", "model": "fixture-model", "usage": {"input_tokens": 20, "output_tokens": 10}},
    ]


def verify(records):
    return module()._verify_records(records, Path("/opt/w/.venv/bin/python"),
        Path("/opt/w/control/probe.py"), "fixture-only", module()._BASE_CHECKS | {"tool_endpoint_absent"})


def test_strict_command_protocol_accepts_fixture_but_does_not_claim_live_proof():
    report = verify(protocol_records())
    assert all(value is True for value in report["checks"].values())


@pytest.mark.parametrize("corruption", [
    "missing_command", "missing_started", "second_command", "unknown_event", "extra_file_change",
    "extra_mcp", "failed_command", "boolean_exit_code", "truncated_output", "malformed_output",
    "self_report_only", "wrong_nonce", "missing_check", "false_check", "unknown_check", "integer_check",
    "duplicate_json_keys", "nonfinite_json", "second_session", "second_turn", "changed_command_id",
])
def test_untrusted_incomplete_and_extra_actions_are_rejected(corruption):
    records = protocol_records()
    completed = records[3]["item"]
    report = json.loads(completed["aggregated_output"])
    if corruption == "missing_command":
        records = [record for record in records if record.get("item", {}).get("type") != "command_execution"]
    elif corruption == "missing_started":
        del records[2]
    elif corruption == "second_command":
        records[4:4] = copy.deepcopy(records[2:4])
    elif corruption == "unknown_event":
        records.insert(4, {"type": "future.action"})
    elif corruption in {"extra_file_change", "extra_mcp"}:
        records.insert(4, {"type": "item.completed", "item": {
            "type": "file_change" if corruption == "extra_file_change" else "mcp_tool_call"}})
    elif corruption == "failed_command":
        completed["exit_code"] = 1
    elif corruption == "boolean_exit_code":
        completed["exit_code"] = False
    elif corruption == "truncated_output":
        completed["output_truncated"] = True
    elif corruption == "malformed_output":
        completed["aggregated_output"] = "{partial"
    elif corruption == "self_report_only":
        records[4]["item"]["text"] = completed["aggregated_output"]
        completed["aggregated_output"] = ""
    elif corruption == "duplicate_json_keys":
        completed["aggregated_output"] = completed["aggregated_output"].replace('"nonce":', '"nonce":"duplicate", "nonce":')
    elif corruption == "nonfinite_json":
        completed["aggregated_output"] = completed["aggregated_output"].replace('"errors": {}', '"errors": {"value": NaN}')
    elif corruption == "second_session":
        records.insert(4, {"type": "thread.started", "thread_id": "extra"})
    elif corruption == "second_turn":
        records.insert(4, {"type": "turn.started"})
    elif corruption == "changed_command_id":
        completed["id"] = "other"
    else:
        if corruption == "wrong_nonce":
            report["nonce"] = "forged"
        elif corruption == "missing_check":
            report["checks"].pop("private_read_denied")
        elif corruption == "false_check":
            report["checks"]["private_read_denied"] = False
        elif corruption == "integer_check":
            report["checks"]["private_read_denied"] = 1
        else:
            report["checks"]["unrecognized"] = True
        completed["aggregated_output"] = json.dumps(report)
    with pytest.raises(ValueError):
        verify(records)


@pytest.fixture
def staged(monkeypatch):
    from devbench.study import permissions
    from devbench.study.adapter import StudyCodeActionAdapter

    # Test-only storage exemption. No production profile is altered or launched.
    monkeypatch.setattr(permissions, "platform_scratch_roots", lambda: ())
    # The host tool sandbox itself denies socket binding. Replace local listener
    # plumbing as well as CLI dispatch; none of this is live permission proof.
    class LocalSocketFixture:
        def __init__(self, *args):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def bind(self, address):
            if isinstance(address, str):
                Path(address).touch(exist_ok=False)

        def listen(self, backlog):
            pass

        def getsockname(self):
            return ("127.0.0.1", 12345)

        def settimeout(self, timeout):
            pass

        def connect(self, address):
            pass

    monkeypatch.setattr(module().socket, "socket", LocalSocketFixture)
    from devbench.study import cli_identity
    monkeypatch.setattr(cli_identity, "read_codex_identity", lambda path: {
        "executable_path": str(path), "executable_sha256": hashlib.sha256(Path(path).read_bytes()).hexdigest(),
        "component_sha256": {str(path): hashlib.sha256(Path(path).read_bytes()).hexdigest()},
        "version": "codex-cli 0.0.0-protocol-fixture"})
    with tempfile.TemporaryDirectory(prefix="ep-", dir="/private/tmp") as temporary:
        root = Path(temporary).resolve()
        workspace, private, sibling = root / "w", root / "coordinator/p", root / "w2"
        (workspace / "control").mkdir(parents=True)
        (workspace / ".venv/bin").mkdir(parents=True)
        (workspace / ".venv/bin/python").symlink_to(sys.executable)
        private.mkdir(parents=True)
        sibling.mkdir()
        boundary = permissions.PermissionBoundary(workspace=workspace, private_roots=(private, sibling),
            protected_paths=(workspace / "control", workspace / ".venv"))
        instance = StudyCodeActionAdapter(boundary=boundary, requested_model="fixture-model",
            executable=sys.executable, timeout_sec=90,
            coordinator_dispatch_callback=lambda event: pytest.fail("Scored dispatch callback invoked"))
        yield instance, private / "exec-preflight"


def fake_process(monkeypatch, *, mutate=None, complete=True, process_error=None):
    from devbench_engine.adapters import CodexCLIAdapter
    from devbench.study.adapter import StudyCodeActionAdapter
    # This fixture simulates CLI stdout; it cannot emit native CLI telemetry.
    monkeypatch.setattr(StudyCodeActionAdapter, "_start_model_capture", lambda *args: None)

    calls = []

    def run(self, command, workspace, environment, instruction, timeout_sec, parse_events=True):
        calls.append({"command": command, "environment": environment, "instruction": instruction,
                      "timeout_sec": timeout_sec, "instance": self})
        assert parse_events is True
        invocation = instruction.splitlines()[-1]
        python, isolated, script = shlex.split(invocation)
        assert isolated == "-I"
        config = ast.literal_eval(Path(script).read_text().splitlines()[0].removeprefix("CONFIG = "))
        assert python == str(self.python)
        compile(Path(script).read_text(), script, "exec")
        expected = module()._BASE_CHECKS | ({"tool_endpoint_absent"} if config["assigned_socket"] is None
                                         else {"assigned_socket_handshake_allowed"})
        events = protocol_records(command=invocation, nonce=config["nonce"],
                                  checks={name: True for name in expected})
        Path(config["workspace_canary"]).write_bytes(b"probe-write")
        Path(command[command.index("--output-last-message") + 1]).write_text("Fixture final response.")
        if mutate is not None:
            mutate(events, Path(script), config)
        try:
            for event in events:
                self._event(event)
            if process_error is not None:
                raise process_error
        except Exception:
            self.metadata["event_capture_complete"] = False
            raise
        finally:
            self._last_process_measurements = {"stdout_bytes": 123, "stderr_bytes": 0, "returncode": 0}
        self.metadata["event_capture_complete"] = complete
        self.metadata["event_capture_loss"] = {"malformed_event_lines": 0 if complete else 1}
        return 0, "", ""

    monkeypatch.setattr(CodexCLIAdapter, "_run_process", run)
    return calls


def test_producer_reuses_launch_surface_and_keeps_preflight_usage_separate(staged, monkeypatch):
    instance, output = staged
    original_metadata = copy.deepcopy(instance.metadata)
    monkeypatch.setenv("UNRELATED_SECRET", "must-not-appear-in-evidence")
    calls = fake_process(monkeypatch)
    evidence = module().run_exec_preflight(instance, output, allow_model_execution=True)
    assert evidence["verified"] is True, evidence["validation_errors"]
    assert evidence["model_identity_verified"] is True
    assert evidence["actual_model"] == "fixture-model"
    assert evidence["model_identity_origin"] == "codex_cli_turn_completed_model"
    errors_schema = json.loads((output / "probe_schema.json").read_text())["properties"]["errors"]
    assert errors_schema["additionalProperties"] is False
    assert len(calls) == 1 and calls[0]["timeout_sec"] <= 60
    assert calls[0]["environment"] == instance._environment()
    assert calls[0]["instance"] is not instance
    command = calls[0]["command"]
    script = Path(shlex.split(evidence["expected_command"])[2])
    schema = script.with_suffix(".schema.json")
    final = Path(command[command.index("--output-last-message") + 1])
    assert command == instance._command(str(Path(sys.executable).absolute()), instance.workspace, schema, final, None)
    assert evidence["billing"] == {"billing_mode": "chatgpt_subscription", "cost_usd": None,
                                  "usage": {"input_tokens": 20, "output_tokens": 10}, "tokens": 30, "scored": False}
    assert instance.metadata == original_metadata
    assert instance._execution_authorized is False
    assert evidence["inference_dispatch_attempted"] is True
    assert json.loads((output / "dispatch_checkpoint.json").read_text())["scored"] is False
    assert json.loads((output / "evidence.json").read_text()) == evidence
    for path in output.iterdir():
        if path.is_file():
            assert "must-not-appear-in-evidence" not in path.read_text()
    assert not list((instance.workspace / "control").iterdir())
    assert not list(instance.workspace.glob(".exec-preflight-*"))
    assert not (output / "private-canary").exists()
    assert not list((instance.workspace.parent / "w2").iterdir())


def test_permission_success_does_not_invent_missing_model_identity(staged, monkeypatch):
    instance, output = staged
    def remove_model(events, script, config):
        events[-1].pop("model")
    fake_process(monkeypatch, mutate=remove_model)
    evidence = module().run_exec_preflight(instance, output, allow_model_execution=True)
    assert evidence["verified"] is True
    assert evidence["model_identity_verified"] is False
    assert evidence["actual_model"] is None


@pytest.mark.parametrize("identity", [False, None, True])
def test_scored_gate_requires_confirmed_model_identity(identity):
    probe={"verified":True,"model_identity_verified":identity,"actual_model":"fixture-model"}
    if identity is True:
        module().require_scored_model_identity(probe)
    else:
        with pytest.raises(RuntimeError,match="model identity"):
            module().require_scored_model_identity(probe)


@pytest.mark.parametrize("complete", [False, None])
def test_incomplete_capture_cannot_pass_even_with_correct_command_output(staged, monkeypatch, complete):
    instance, output = staged
    fake_process(monkeypatch, complete=complete)
    evidence = module().run_exec_preflight(instance, output, allow_model_execution=True)
    assert evidence["model_identity_verified"] is False
    assert evidence["verified"] is False
    assert (output / "evidence.json").is_file()
    assert (output / "cli_records.jsonl").is_file()


def test_timeout_retains_unscored_checkpoint_usage_and_cleans_canaries(staged, monkeypatch):
    from devharness_core.watchdog import HarnessResourceInterrupt

    instance, output = staged
    fake_process(monkeypatch, process_error=HarnessResourceInterrupt("fixture timeout"))
    evidence = module().run_exec_preflight(instance, output, allow_model_execution=True)
    assert evidence["verified"] is False
    assert evidence["inference_dispatch_attempted"] is True
    assert evidence["billing"]["tokens"] == 30
    assert (output / "evidence.json").is_file()
    assert not list((instance.workspace / "control").iterdir())


def test_changed_trusted_script_is_rejected(staged, monkeypatch):
    def mutate(events, script, config):
        script.chmod(0o600)
        script.write_text("print('forged')")

    instance, output = staged
    fake_process(monkeypatch, mutate=mutate)
    evidence = module().run_exec_preflight(instance, output, allow_model_execution=True)
    assert evidence["verified"] is False
    assert any("changed_at_command_completion" in error for error in evidence["validation_errors"])


def test_same_output_cannot_dispatch_a_second_session(staged, monkeypatch):
    instance, output = staged
    calls = fake_process(monkeypatch)
    module().run_exec_preflight(instance, output, allow_model_execution=True)
    with pytest.raises(FileExistsError):
        module().run_exec_preflight(instance, output, allow_model_execution=True)
    assert len(calls) == 1


def test_unprotected_control_is_rejected_without_profile_changes(staged, monkeypatch):
    from devbench.study.permissions import PermissionBoundary

    instance, output = staged
    instance.boundary = PermissionBoundary(workspace=instance.workspace,
                                         private_roots=instance.boundary.private_roots)
    calls = fake_process(monkeypatch)
    with pytest.raises(ValueError, match="already have scored read-only"):
        module().run_exec_preflight(instance, output, allow_model_execution=True)
    assert calls == [] and not output.exists()


def test_output_must_be_covered_by_existing_private_deny(staged, monkeypatch):
    instance, _ = staged
    calls = fake_process(monkeypatch)
    output = instance.workspace / "would-be-readable"
    with pytest.raises(ValueError, match="private deny"):
        module().run_exec_preflight(instance, output, allow_model_execution=True)
    assert calls == [] and not output.exists()


def test_missing_real_sibling_is_blocked_before_dispatch(staged, monkeypatch):
    from devbench.study.permissions import PermissionBoundary

    instance, output = staged
    instance.boundary = PermissionBoundary(workspace=instance.workspace,
        private_roots=(output.parent,), protected_paths=instance.boundary.protected_paths)
    calls = fake_process(monkeypatch)
    evidence = module().run_exec_preflight(instance, output, allow_model_execution=True)
    assert evidence["verified"] is False
    assert calls == []
    assert any("sibling" in error for error in evidence["validation_errors"])


def test_package_path_prefix_is_recorded_without_changing_permission_grants(staged, monkeypatch):
    from devbench.study import cli_identity

    instance, output = staged
    original_effective = cli_identity.effective_child_environment
    prefix = instance.workspace.parent / "pinned-cli-path"
    prefix.mkdir()

    def effective(environment, identity):
        assert identity["component_sha256"]
        return original_effective(environment, {"runtime_path_prepends": [str(prefix)]})

    monkeypatch.setattr(cli_identity, "effective_child_environment", effective)
    before = instance.boundary.config_overrides()
    fake_process(monkeypatch)
    evidence = module().run_exec_preflight(instance, output, allow_model_execution=True)
    assert evidence["verified"] is True, evidence["validation_errors"]
    configuration = json.loads((output / "launch_configuration.json").read_text())
    configured = configuration["configured_child_environment_allowlist"]
    effective = configuration["child_environment_allowlist"]
    assert configured == instance._child_environment()
    assert effective == {**configured, "PATH": str(prefix) + ":" + configured["PATH"]}
    assert configuration["permission_overrides"] == before
    assert instance.boundary.config_overrides() == before


def test_trusted_script_reports_environment_mismatch_names_without_values(staged, monkeypatch):
    instance, output = staged
    observed = []

    def mutate(events, script, config):
        config["child_environment"] = {"DIAGNOSTIC_SENTINEL": "expected-private-value"}
        monkeypatch.setenv("DIAGNOSTIC_SENTINEL", "actual-private-value")
        namespace = {"__name__": "offline_source_test"}
        exec(compile(script.read_text(), str(script), "exec"), namespace)
        capture = io.StringIO()
        with redirect_stdout(capture):
            returncode = namespace["main"](config)
        report = json.loads(capture.getvalue())
        assert returncode == 1 and report["checks"]["child_environment_matches"] is False
        assert report["errors"]["child_environment_matches"] == {"mismatched_names": ["DIAGNOSTIC_SENTINEL"]}
        assert "expected-private-value" not in capture.getvalue()
        assert "actual-private-value" not in capture.getvalue()
        observed.append(report)
        events[3]["item"].update(exit_code=returncode, aggregated_output=capture.getvalue())

    fake_process(monkeypatch, mutate=mutate)
    evidence = module().run_exec_preflight(instance, output, allow_model_execution=True)
    assert observed and evidence["verified"] is False


def test_trusted_script_rejects_unrestricted_fixture_io(staged, monkeypatch):
    instance, output = staged
    observed = []

    def mutate(events, script, config):
        assert Path(config["sibling_canary"]).parent == instance.workspace.parent / "w2"
        namespace = {"__name__": "offline_source_test"}
        exec(compile(script.read_text(), str(script), "exec"), namespace)
        capture = io.StringIO()
        with redirect_stdout(capture):
            returncode = namespace["main"](config)
        assert returncode == 1
        report = json.loads(capture.getvalue())
        for name in ("private_read_denied", "sibling_read_denied", "protected_write_denied",
                     "tcp_denied", "unassigned_socket_denied"):
            assert report["checks"][name] is False
        observed.append(report)
        events[3]["item"].update(exit_code=returncode, aggregated_output=capture.getvalue())

    fake_process(monkeypatch, mutate=mutate)
    evidence = module().run_exec_preflight(instance, output, allow_model_execution=True)
    assert observed and evidence["verified"] is False


@pytest.mark.parametrize("condition", ["optional", "enforced"])
def test_assisted_probe_preserves_assigned_endpoint_without_retaining_capability(staged, monkeypatch, condition):
    from devbench.study.adapter import StudyCodeActionAdapter
    from devbench.study.permissions import PermissionBoundary

    baseline, output = staged
    boundary = PermissionBoundary(workspace=baseline.workspace, private_roots=baseline.boundary.private_roots,
        protected_paths=baseline.boundary.protected_paths, socket_path=baseline.workspace / "control/tools.sock")
    capability = baseline.workspace / "control/capability"
    sentinel = "fixture-capability-must-not-be-retained" * 2
    capability.write_text(sentinel)
    instance = StudyCodeActionAdapter(boundary=boundary, requested_model="fixture-model", condition=condition,
        executable=sys.executable, tool_capability_file=capability)
    fake_process(monkeypatch)
    evidence = module().run_exec_preflight(instance, output, allow_model_execution=True)
    assert evidence["verified"] is True, evidence["validation_errors"]
    assert evidence["checks"]["assigned_socket_handshake_allowed"] is True
    assert "tool_endpoint_absent" not in evidence["checks"]
    for path in output.iterdir():
        assert sentinel not in path.read_text()
    assert capability.read_text() == sentinel


@pytest.mark.parametrize("response", [b'{"status":"preflight_connection_verified"}\n',
                                      b'{"status":"wrong"}\n', b'partial'])
def test_trusted_handshake_requires_exact_no_op_response(staged, monkeypatch, response):
    baseline, output = staged
    received = []
    socket_class = module().socket.socket

    class HandshakeSocketFixture(socket_class):
        def sendall(self, content):
            received.append(json.loads(content))

        def recv(self, limit):
            value = getattr(self, "remaining", response)
            self.remaining = b""
            return value

    monkeypatch.setattr(module().socket, "socket", HandshakeSocketFixture)
    seen = []

    def mutate(events, script, config):
        capability = baseline.workspace / "control/capability"
        capability.write_text("x" * 40)
        config.update(assigned_socket=str(baseline.workspace / "control/tools.sock"),
                      capability_file=str(capability))
        namespace = {"__name__": "offline_source_test"}
        exec(compile(script.read_text(), str(script), "exec"), namespace)
        capture = io.StringIO()
        with redirect_stdout(capture):
            namespace["main"](config)
        result = json.loads(capture.getvalue())
        assert "x" * 40 not in capture.getvalue()
        seen.append(result["checks"]["assigned_socket_handshake_allowed"])
        capability.unlink()

    fake_process(monkeypatch, mutate=mutate)
    module().run_exec_preflight(baseline, output, allow_model_execution=True)
    assert seen == [response == b'{"status":"preflight_connection_verified"}\n']
    assert received == [{"tool": "__devbench_preflight__", "arguments": {}, "capability": "x" * 40}]


def test_event_budget_failure_is_retained_without_retry(staged, monkeypatch):
    instance, output = staged
    monkeypatch.setattr(module(), "MAX_RECORDS", 3)
    calls = fake_process(monkeypatch)
    evidence = module().run_exec_preflight(instance, output, allow_model_execution=True)
    assert evidence["verified"] is False and len(calls) == 1
    assert evidence["cli_records_captured"] == 3
    assert any("record_budget" in error for error in evidence["validation_errors"])


def test_cleanup_never_follows_replaced_canary_symlink(staged, monkeypatch):
    instance, output = staged
    target = instance.workspace / "untouched"
    target.write_text("preserve")

    def mutate(events, script, config):
        path = Path(config["workspace_canary"])
        path.unlink()
        path.symlink_to(target)

    fake_process(monkeypatch, mutate=mutate)
    evidence = module().run_exec_preflight(instance, output, allow_model_execution=True)
    assert evidence["verified"] is False
    assert "probe_cleanup_target_changed" in evidence["validation_errors"]
    assert target.read_text() == "preserve"


def test_cli_may_atomically_replace_its_reserved_final_message(staged, monkeypatch):
    instance, output = staged

    def mutate(events, script, config):
        final = instance.workspace / "control" / (".exec-preflight-" + config["nonce"] + ".txt")
        replacement = final.with_suffix(".replacement")
        replacement.write_text("Atomic CLI output, still not proof.")
        replacement.replace(final)

    fake_process(monkeypatch, mutate=mutate)
    evidence = module().run_exec_preflight(instance, output, allow_model_execution=True)
    assert evidence["verified"] is True, evidence["validation_errors"]
    assert (output / "agent_final_message.txt").read_text() == "Atomic CLI output, still not proof."
    assert not list(instance.workspace.glob(".exec-preflight-*"))
