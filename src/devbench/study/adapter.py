"""Study launch configuration over the existing live Code-as-Action path.

Construction is configuration-only. The coordinator owns permission preflight,
input staging, broker lifetime, and acceptance; this class does not certify
filesystem isolation or infer tool adoption from a participant-written trace.
"""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
from typing import get_args

from devbench.adapters.code_action import CodeActionAdapter
from devbench_engine.adapters import CodexCLIAdapter

from .contracts import CONDITIONS, ReasoningEffort
from .permissions import PermissionBoundary, _toml


ARTIFACT_MAX_BYTES = 8_000_000
MODEL_IDENTITY_EVENT_LIMIT = 128
_THREAD_VARIABLES = ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
                     "NUMEXPR_NUM_THREADS", "VECLIB_MAXIMUM_THREADS")
_TOOL_VARIABLES = ("DEVHARNESS_TOOL_SOCKET", "DEVHARNESS_TOOL_CAPABILITY_FILE")


class StudyCodeActionAdapter(CodeActionAdapter):
    """Pinned, single-session study participant with explicit child environment.

    ``requested_model`` records the exact requested identifier, not proof of the
    provider's runtime model. Original CLI events and script/answer hashes remain
    captured by CodeActionAdapter. Optional and enforced conditions use the same
    broker client and transport; their acceptance rules belong to the coordinator.
    """
    backend_name = "Study Codex Code-as-Action"

    def __init__(self, *, boundary: PermissionBoundary, requested_model: str,
                 condition="baseline", reasoning_effort=None, tool_capability_file=None,
                 executable="codex", timeout_sec=90, max_mem_mb=8000, thread_limit=1,
                 coordinator_dispatch_callback=None, model_identity_output=None):
        if not isinstance(boundary, PermissionBoundary):
            raise ValueError("A study PermissionBoundary is required")
        if (not isinstance(requested_model, str) or not 1 <= len(requested_model) <= 200
                or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_./:-]*", requested_model) is None):
            raise ValueError("An exact requested model identifier is required")
        if condition not in CONDITIONS:
            raise ValueError("Unknown study condition")
        if reasoning_effort is not None and reasoning_effort not in get_args(ReasoningEffort):
            raise ValueError("Unknown reasoning effort")
        if type(thread_limit) is not int or not 1 <= thread_limit <= 128:
            raise ValueError("Thread limit must be an integer from 1 to 128")
        if coordinator_dispatch_callback is not None and not callable(coordinator_dispatch_callback):
            raise ValueError("The coordinator dispatch callback must be callable")
        if condition == "baseline":
            if boundary.socket_path is not None or tool_capability_file is not None:
                raise ValueError("The baseline condition cannot receive a tool endpoint")
            capability = None
        else:
            if boundary.socket_path is None or tool_capability_file is None:
                raise ValueError("Assisted conditions require a tool socket and capability file")
            capability = Path(tool_capability_file)
            if not capability.is_absolute():
                raise ValueError("The tool capability file must be absolute")
            capability = capability.resolve()
            protected = (*boundary.protected_paths, boundary.socket_path.parent)
            if (not capability.is_relative_to(boundary.workspace)
                    or not any(capability.is_relative_to(path) and capability != path for path in protected)):
                raise ValueError("The tool capability file must be below a protected workspace directory")
        # Initialize the historical base with its baseline configuration, then
        # retain the study condition for observed attempt metadata.
        super().__init__(executable=executable, timeout_sec=timeout_sec, model=requested_model,
                         max_mem_mb=max_mem_mb, condition="baseline", artifact_max_bytes=ARTIFACT_MAX_BYTES)
        self.boundary = boundary
        self.requested_model = requested_model
        self.condition = condition
        self.reasoning_effort = reasoning_effort
        self.thread_limit = thread_limit
        self.tool_capability_file = capability
        self.workspace = boundary.workspace
        self.python = self.workspace / ".venv" / "bin" / "python"
        self._schema_enforcement = "local"
        self.coordinator_dispatch_callback = coordinator_dispatch_callback
        self.model_identity_output = Path(model_identity_output) if model_identity_output is not None else None
        self._model_capture = None
        self._execution_authorized = False
        self.require_command_starts = True
        self._reset_model_identity()

    def _reset_model_identity(self):
        self._provider_model_observations = []
        self._provider_completion_count = 0
        self._provider_model_error = False
        self._provider_model_missing = False
        self._native_completion_verified = False
        self._provider_process_succeeded = False
        self._update_model_identity()

    def _update_model_identity(self):
        identifiers = list(dict.fromkeys(item["model"] for item in self._provider_model_observations))
        observed = identifiers[0] if len(identifiers) == 1 else None
        self.metadata.update(
            requested_model=self.requested_model, actual_model=observed, model_ids=identifiers,
            model_identity_verified=(observed is not None and not self._provider_model_error
                                     and (not self._provider_model_missing or self._native_completion_verified)
                                     and self._provider_completion_count > 0
                                     and self._provider_process_succeeded
                                     and self.metadata.get("event_capture_complete") is True),
            model_identity_origin=("codex_cli_completion_telemetry" if any(
                item["origin"] == "codex_cli_completion_telemetry" for item in self._provider_model_observations)
                else "codex_cli_turn_completed_model"),
            model_identity_scope="CLI-reported model identifier; no cryptographic attestation",
            model_identity_observations=list(self._provider_model_observations),
            model_identity_event_limit=MODEL_IDENTITY_EVENT_LIMIT,
        )

    def _model(self, model):
        # The historical parser also visits item.model, which is not a trusted
        # identity source for study runs. _event handles only provider completions.
        pass

    def _event(self, record):
        try:
            super()._event(record)
            event_type = record.get("type")
            if event_type in {"error", "turn.failed"}:
                self._provider_model_error = True
            if event_type == "turn.completed":
                self._provider_completion_count += 1
                if self._provider_completion_count > MODEL_IDENTITY_EVENT_LIMIT:
                    raise RuntimeError("Provider model identity event capture exceeds its limit")
                model = record.get("model")
                if model is None:
                    self._provider_model_missing = True
                elif (not isinstance(model, str) or not 1 <= len(model) <= 300
                        or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_./:-]*", model) is None):
                    self._provider_model_error = True
                else:
                    self._provider_model_observations.append(
                        {"origin": "codex_cli_turn_completed_model", "model": model})
        except Exception:
            self._provider_model_error = True
            raise
        finally:
            self._update_model_identity()

    def _run_process(self, command, workspace, environment, instruction, timeout_sec, parse_events=True):
        measured = parse_events and len(command) > 1 and command[1] == "exec"
        if not measured:
            return super()._run_process(command, workspace, environment, instruction, timeout_sec,
                                        parse_events=parse_events)
        if not self._execution_authorized:
            raise PermissionError("Study data/model execution is disabled without explicit authorization")
        self._provider_process_succeeded = False
        # The historical execute path sets this optimistically before reaching
        # us. A failed durable checkpoint must not count as a model invocation.
        self.metadata.update(inference_dispatch_attempted=False,
                             dispatch_stage="checkpoint_pending_before_launch")
        try:
            if self.coordinator_dispatch_callback is not None:
                try:
                    self.coordinator_dispatch_callback({
                        "stage": "model_dispatch", "requested_model": self.requested_model,
                        "condition": self.condition, "origin": "coordinator_before_codex_cli_exec",
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    })
                except Exception as error:
                    raise RuntimeError("Coordinator dispatch checkpoint failed before CLI launch") from error
            self.metadata.update(inference_dispatch_attempted=True, dispatch_stage="model_launch_attempted")
            result = super()._run_process(command, workspace, environment, instruction, timeout_sec,
                                           parse_events=parse_events)
            self._provider_process_succeeded = result[0] == 0
            return result
        except BaseException:
            if not self.metadata["inference_dispatch_attempted"]:
                self.metadata["dispatch_stage"] = "checkpoint_failed_before_launch"
            self._provider_model_error = True
            raise
        finally:
            self._update_model_identity()

    def _child_environment(self):
        environment = {
            "PATH": str(self.python.parent) + os.pathsep + os.pathsep.join(
                ("/usr/bin", "/bin", "/usr/sbin", "/sbin")),
            "DEVHARNESS_PYTHON": str(self.python),
            "TMPDIR": str(self.workspace / ".tmp"),
            "PYTHONNOUSERSITE": "1",
            **{name: str(self.thread_limit) for name in _THREAD_VARIABLES},
        }
        if self.condition in {"optional", "enforced"}:
            environment.update(DEVHARNESS_TOOL_SOCKET=str(self.boundary.socket_path),
                               DEVHARNESS_TOOL_CAPABILITY_FILE=str(self.tool_capability_file))
        return environment

    def _environment(self):
        # The parent CLI keeps its existing subscription login and managed
        # sandbox settings. Its child commands inherit none of that environment.
        environment = super()._environment()
        for name in _TOOL_VARIABLES:
            environment.pop(name, None)
        if self._model_capture is not None:
            from .model_telemetry import TOKEN_ENV
            environment = {key:value for key,value in environment.items() if not key.startswith(("OTEL_", "OTLP_"))}
            environment[TOKEN_ENV] = self._model_capture.capability
            for name in ("NO_PROXY", "no_proxy"):
                environment[name] = ",".join(filter(None, (environment.get(name), "127.0.0.1,localhost,::1")))
        return self.boundary.execution_environment(environment)

    def _start_model_capture(self, output):
        if output is None:
            return
        path = Path(output)
        if self._execution_authorized is not True:
            raise PermissionError("Native identity capture requires execution authorization")
        if (not path.is_absolute() or path.is_relative_to(self.workspace)
                or not any(path.is_relative_to(root) for root in self.boundary.private_roots)):
            raise ValueError("Identity capture must remain in coordinator-private storage")
        if self._model_capture is not None:
            raise RuntimeError("Native identity capture is already active")
        from .model_telemetry import NativeModelCapture
        self._model_capture = NativeModelCapture(path)

    def _stop_model_capture(self):
        if self._model_capture is None:
            return
        capture, self._model_capture = self._model_capture, None
        receipt = capture.close(self._session_id, self._turn_usage)
        command = self.metadata.get("cli_command")
        if isinstance(command, list):
            import hashlib
            self.metadata["cli_command_sha256"] = hashlib.sha256(json.dumps(command, separators=(",", ":")).encode()).hexdigest()
            self.metadata["cli_command"] = [part.replace(capture.capability, "<ephemeral-local-capture-token>") for part in command]
            self.metadata["cli_command_redaction"] = "ephemeral local telemetry capability only"
        self.metadata["native_model_identity"] = receipt
        if (receipt["capture_complete"] and receipt["usage_matches_cli"] and receipt["completion_model"]
                and self._provider_completion_count == 1):
            self._native_completion_verified = True
            self._provider_model_observations.append({"origin":"codex_cli_completion_telemetry",
                                                       "model":receipt["completion_model"]})
        self._update_model_identity()

    def _check_workspace(self, workspace):
        path = Path(workspace)
        if not path.is_absolute() or path.resolve() != self.boundary.workspace:
            raise ValueError("The execution workspace must match the study permission boundary")

    def _command(self, executable, workspace, schema, output, resume):
        self._check_workspace(workspace)
        if self.model != self.requested_model:
            raise ValueError("The requested model cannot change after configuration")
        if resume is not None:
            raise ValueError("Study participants require fresh sessions; resume is unsupported")
        condition = self.condition
        try:
            self.condition = "baseline"
            # Bypass only CodeActionAdapter's legacy launch macro. Its execution
            # implementation, event capture, and artifact provenance stay intact.
            command = CodexCLIAdapter._command(self, executable, workspace, schema, output, None)
        finally:
            self.condition = condition
        index = command.index("--sandbox")
        del command[index:index + 2]
        command.remove("--ignore-user-config")
        environment = self._child_environment()
        arguments = [*self.boundary.exec_arguments(), "-c",
                     'shell_environment_policy.inherit="none"', "-c",
                     "shell_environment_policy.include_only=" + json.dumps(sorted(environment)), "-c",
                     "shell_environment_policy.set=" + _toml(environment)]
        if self.reasoning_effort is not None:
            arguments.extend(["-c", "model_reasoning_effort=" + _toml(self.reasoning_effort)])
        if self._model_capture is not None:
            arguments.extend(self._model_capture.arguments())
        command[-1:-1] = arguments
        return command

    def execute(self, instruction, workspace_dir, *, allow_data_execution=False):
        """Launch only after the coordinator explicitly authorizes execution.

        The coordinator must first verify the exact boundary with its standalone
        probe. This opt-in flag is authorization, not evidence of that preflight.
        """
        if allow_data_execution is not True:
            raise PermissionError("Study data/model execution is disabled without explicit authorization")
        self._reset_model_identity()
        self._check_workspace(workspace_dir)
        scratch = self.boundary.workspace / ".tmp"
        if scratch.is_symlink() or scratch.resolve() != scratch:
            raise ValueError("Participant TMPDIR must remain inside its own workspace")
        scratch.mkdir(mode=0o700, exist_ok=True)
        self._execution_authorized = True
        try:
            self._start_model_capture(self.model_identity_output)
            return super().execute(instruction, workspace_dir)
        finally:
            try:
                self._stop_model_capture()
            finally:
                self._execution_authorized = False
                self._update_model_identity()
            self.metadata.update(
                requested_reasoning_effort=self.reasoning_effort,
                permission_boundary=self.boundary.describe(),
                child_environment_variable_names=sorted(self._child_environment()),
                tool_evidence_source="coordinator_owned_broker_receipts",
            )
