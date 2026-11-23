"""Codex writes and executes a script using an explicit staged interpreter."""
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import stat
import time

from devbench_engine.adapters import CodexCLIAdapter, _read_answer, _validate_schema
from .execution_capture import script_invocation


def _file_hash(path: Path, *, max_bytes=1_000_000):
    if type(max_bytes) is not int or not 1 <= max_bytes <= 8_000_000:
        raise ValueError("Invalid artifact byte budget")
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as handle:
            details = os.fstat(handle.fileno())
            if not stat.S_ISREG(details.st_mode) or details.st_size > max_bytes:
                return None
            data = handle.read(max_bytes + 1)
        return hashlib.sha256(data).hexdigest() if len(data) <= max_bytes else None
    except OSError:
        return None


class CodeActionAdapter(CodexCLIAdapter):
    """One native Codex session, a real script artifact and observed command proof."""
    backend_name = "Codex Code-as-Action"

    def __init__(self, executable="codex", timeout_sec=90, model=None, max_mem_mb=8000,
                 condition="assisted", artifact_max_bytes=1_000_000):
        if type(artifact_max_bytes) is not int or not 1 <= artifact_max_bytes <= 8_000_000:
            raise ValueError("Invalid code-action artifact byte budget")
        super().__init__(executable=executable, timeout_sec=timeout_sec, model=model,
                         max_mem_mb=max_mem_mb, max_turns=1, condition=condition)
        self.workspace = None
        self.python = None
        self.command_events = []
        self.artifact_max_bytes = artifact_max_bytes
        self.execution_observer = None
        self._command_contexts = {}
        self._command_sequence = 0
        self.require_command_starts = False

    def _execution_context(self, item, *, started):
        key = item.get("id")
        if key is not None and key in self._command_contexts:
            return self._command_contexts[key]
        self._command_sequence += 1
        if self._command_sequence > 128:
            raise RuntimeError("Code-action execution windows exceed capture limit")
        context = {"execution_id": f"execution-{self._command_sequence:04d}",
                   "sequence": self._command_sequence, "cli_item_id": key,
                   "command_sha256": hashlib.sha256(str(item.get("command", "")).encode()).hexdigest(),
                   "start_observed": started,
                   "script_invocation_candidate": script_invocation(item.get("command"), self.workspace, self.python)}
        if key is not None:
            self._command_contexts[key] = context
        if started and self.execution_observer is not None:
            self.execution_observer("started", dict(context))
        return context

    def _environment(self):
        environment = super()._environment()
        environment.pop("PYTHONPATH", None)
        environment.pop("PYTHONHOME", None)
        environment["PYTHONNOUSERSITE"] = "1"
        environment["DEVHARNESS_WORKSPACE_ROOT"] = str(self.workspace)
        environment["PATH"] = str(self.python.parent) + os.pathsep + environment.get("PATH", "")
        return environment

    def _command(self, executable, workspace, schema, output, resume):
        # Use the established subscription CLI flags with no registered MCP.
        condition = self.condition
        try:
            self.condition = "baseline"
            command = super()._command(executable, workspace, schema, output, resume)
            # Automatic review still evaluates escalation requests and retains
            # workspace-write restrictions. This CLI macro conflicts with an
            # explicit --sandbox argument; it selects workspace-write itself.
            index = command.index("--sandbox")
            del command[index:index + 2]
            command.insert(2, "--approve-for-me")
            return command
        finally:
            self.condition = condition

    def _event(self, record):
        item = record.get("item", {})
        if (isinstance(item, dict) and record.get("type") == "item.started"
                and item.get("type") == "command_execution"):
            self._execution_context(item, started=True)
        if isinstance(item, dict) and record.get("type") == "item.completed":
            if item.get("type") == "command_execution":
                if len(self.command_events) >= 128:
                    raise RuntimeError("Code-action command evidence exceeds capture limit")
                command = item.get("command")
                direct = script_invocation(command, self.workspace, self.python)
                succeeded = item.get("status") == "completed" and item.get("exit_code") == 0
                context = self._execution_context(item, started=False)
                if (context["start_observed"] and context["command_sha256"] !=
                        hashlib.sha256(str(command).encode()).hexdigest()):
                    raise RuntimeError("CLI command changed between its start and completion")
                event = {**context, "command": str(command)[:16_000],
                    "exit_code": item.get("exit_code"), "status": item.get("status"),
                    "direct_script_invocation": direct, "succeeded": succeeded,
                    "script_sha256_at_completion": _file_hash(self.workspace / "solve.py", max_bytes=self.artifact_max_bytes) if direct and succeeded else None,
                    "answer_sha256_at_completion": _file_hash(self.workspace / "final_discovery.json", max_bytes=self.artifact_max_bytes) if direct and succeeded else None,
                    "output_excerpt": str(item.get("aggregated_output", ""))[:3000]}
                self.command_events.append(event)
                if self.execution_observer is not None:
                    self.execution_observer("completed", event)
            elif item.get("type") == "mcp_tool_call":
                self.metadata["mcp_attempts"] += 1
                if "cancel" in str(item.get("error", item.get("result", ""))).lower():
                    self.metadata["mcp_cancellations"] += 1
        super()._event(record)

    def _observe_script_execution(self):
        """Execution evidence is independent of the enclosing CLI exit status."""
        try:
            script_hash = _file_hash(self.workspace / "solve.py", max_bytes=self.artifact_max_bytes)
            answer_hash = _file_hash(self.workspace / "final_discovery.json", max_bytes=self.artifact_max_bytes)
            executions = [event for event in self.command_events if event["direct_script_invocation"]]
            successful = [event for event in executions if event["succeeded"]]
            last = successful[-1] if successful else None
            bound = bool(last and script_hash and answer_hash
                         and (not self.require_command_starts or last.get("start_observed") is True)
                         and last["script_sha256_at_completion"] == script_hash
                         and last["answer_sha256_at_completion"] == answer_hash)
            self.metadata.update(script_execution_observed=bound,
                script_executions=[{key: event.get(key) for key in (
                    "execution_id", "sequence", "cli_item_id", "start_observed", "command_sha256",
                    "succeeded", "exit_code", "script_sha256_at_completion", "answer_sha256_at_completion")}
                    for event in executions],
                last_successful_execution_id=last.get("execution_id") if last else None,
                final_execution_id=last.get("execution_id") if bound else None)
        except Exception as error:
            self.metadata["script_execution_observed"] = False
            self.metadata["script_execution_observation_error"] = f"{type(error).__name__}: {error}"[:1000]

    def execute(self, instruction, workspace_dir):
        self.workspace = Path(workspace_dir).resolve()
        self.python = self.workspace / ".venv" / "bin" / "python"
        if not self.python.is_file():
            raise RuntimeError("Code-action requires a staged .venv/bin/python")
        self.tool_events, self.events, self.command_events = [], [], []
        self._command_contexts, self._command_sequence = {}, 0
        self._trace_bytes = 0
        self._session_id = None
        self._phase = "code_action"
        self._turn_usage = self._turn_result = None
        self._failed_tools, self._pending_tools = {}, {}
        self._last_process_measurements = {}
        self._schema_enforcement = "local"
        self.metadata = {"billing_mode": self.billing_mode, "cost_usd": None,
            "condition": self.condition, "turns": [], "model_ids": [], "actual_model": None,
            "requested_model": self.model, "continuity": "single_turn",
            "mcp_attempts": 0, "mcp_cancellations": 0, "script_execution_observed": False,
            "inference_dispatch_attempted": False,
            "evidence_scope": "CLI command completions plus matching script/answer hashes; workspace SDK trace is untrusted"}
        original_schema = _read_answer(self.workspace, "answer_schema.json", max_bytes=self.artifact_max_bytes)
        executable = shutil.which(self.executable)
        if not executable:
            raise RuntimeError("Codex executable unavailable")
        executable = os.path.abspath(executable)
        environment = self._environment()
        started = time.monotonic()
        error = None
        answer = None
        try:
            self._preflight(executable, self.workspace, environment, min(self.timeout_sec, 15))
            remaining = self.timeout_sec - (time.monotonic() - started)
            if remaining <= 0:
                raise RuntimeError("Deadline exhausted during login preflight")
            prompt = ("Read TASK_README.md and sdk_cheatsheet.py. Write solve.py to analyze the supplied data, "
                      "execute it with the exact staged Python command below, and save final_discovery.json. "
                      "Use the provided environment immediately; avoid environment hunting and planning documents. "
                      "Preserve the inputs/schema. No MCP calls or permission bypasses. Finish within the task deadline.\n"
                      + shlex.join([str(self.python), str(self.workspace / "solve.py")]))
            (self.workspace / "agent_prompt.txt").write_text(prompt)
            command = self._command(executable, self.workspace, self.workspace / "answer_schema.json",
                                    self.workspace / "agent_final_message.txt", None)
            self.metadata["cli_command"] = command
            self.metadata["inference_dispatch_attempted"] = True
            try:
                status, _, _ = self._run_process(command, self.workspace, environment, prompt, remaining)
            finally:
                self._observe_script_execution()
            if status != 0:
                raise RuntimeError("Codex exited with status " + str(status))
            if not self.metadata["script_execution_observed"]:
                raise RuntimeError("No successful direct solve.py execution with matching captured script and answer hashes")
            answer = _read_answer(self.workspace, max_bytes=self.artifact_max_bytes)
            _validate_schema(answer, original_schema)
            return answer
        except Exception as caught:
            error = caught
            raise
        finally:
            self._finish_turn(started, 0, error, "new_session", answer)
            self.metadata.update(duration_sec=time.monotonic() - started, session_id=self._session_id,
                                 commands=self.command_events, observable_events=self.events,
                                 inference_session_observed=self._session_id is not None)
