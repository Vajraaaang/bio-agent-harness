"""Codex writes and executes a script using an explicit staged interpreter."""
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import time

from devbench_engine.adapters import CodexCLIAdapter, _read_answer, _validate_schema


def _file_hash(path: Path):
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 1_000_000:
        return None
    data = path.read_bytes()
    return hashlib.sha256(data).hexdigest() if len(data) <= 1_000_000 else None


def script_invocation(command, workspace: Path, python: Path) -> bool:
    """Recognize a direct successful Python/solve.py invocation, not prose/echo.

    Shell wrappers and an explicit leading cd are supported. Arbitrary compound
    shell syntax is deliberately not treated as proof of script execution.
    """
    if not isinstance(command, str) or len(command) > 16_000:
        return False
    try:
        parts = shlex.split(command)
        if len(parts) == 3 and Path(parts[0]).name in {"sh", "bash", "zsh"} and parts[1] in {"-c", "-lc"}:
            parts = shlex.split(parts[2])
        if len(parts) >= 4 and parts[0] == "cd" and parts[2] == "&&":
            if Path(parts[1]).resolve() != workspace:
                return False
            parts = parts[3:]
        if len(parts) != 2:
            return False
        exe, script = (Path(value) if Path(value).is_absolute() else workspace / value for value in parts)
        # Compare lexical interpreter paths: resolving venv symlinks would make
        # the host/base interpreter indistinguishable from the staged one.
        return exe.absolute() == python.absolute() and script.resolve() == workspace / "solve.py"
    except (ValueError, OSError):
        return False


class CodeActionAdapter(CodexCLIAdapter):
    """One native Codex session, a real script artifact and observed command proof."""
    backend_name = "Codex Code-as-Action"

    def __init__(self, executable="codex", timeout_sec=90, model=None, max_mem_mb=8000,
                 condition="assisted"):
        super().__init__(executable=executable, timeout_sec=timeout_sec, model=model,
                         max_mem_mb=max_mem_mb, max_turns=1, condition=condition)
        self.workspace = None
        self.python = None
        self.command_events = []

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
        if isinstance(item, dict) and record.get("type") == "item.completed":
            if item.get("type") == "command_execution":
                if len(self.command_events) >= 128:
                    raise RuntimeError("Code-action command evidence exceeds capture limit")
                command = item.get("command")
                direct = script_invocation(command, self.workspace, self.python)
                succeeded = item.get("status") == "completed" and item.get("exit_code") == 0
                self.command_events.append({"command": str(command)[:16_000],
                    "exit_code": item.get("exit_code"), "status": item.get("status"),
                    "direct_script_invocation": direct, "succeeded": succeeded,
                    "script_sha256_at_completion": _file_hash(self.workspace / "solve.py") if direct and succeeded else None,
                    "answer_sha256_at_completion": _file_hash(self.workspace / "final_discovery.json") if direct and succeeded else None,
                    "output_excerpt": str(item.get("aggregated_output", ""))[:3000]})
            elif item.get("type") == "mcp_tool_call":
                self.metadata["mcp_attempts"] += 1
                if "cancel" in str(item.get("error", item.get("result", ""))).lower():
                    self.metadata["mcp_cancellations"] += 1
        super()._event(record)

    def _observe_script_execution(self):
        """Execution evidence is independent of the enclosing CLI exit status."""
        try:
            script_hash = _file_hash(self.workspace / "solve.py")
            answer_hash = _file_hash(self.workspace / "final_discovery.json")
            self.metadata["script_execution_observed"] = any(
                event["direct_script_invocation"] and event["succeeded"] and script_hash and answer_hash
                and event["script_sha256_at_completion"] == script_hash
                and event["answer_sha256_at_completion"] == answer_hash for event in self.command_events)
        except Exception as error:
            self.metadata["script_execution_observed"] = False
            self.metadata["script_execution_observation_error"] = f"{type(error).__name__}: {error}"[:1000]

    def execute(self, instruction, workspace_dir):
        self.workspace = Path(workspace_dir).resolve()
        self.python = self.workspace / ".venv" / "bin" / "python"
        if not self.python.is_file():
            raise RuntimeError("Code-action requires a staged .venv/bin/python")
        self.tool_events, self.events, self.command_events = [], [], []
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
        original_schema = _read_answer(self.workspace, "answer_schema.json")
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
            answer = _read_answer(self.workspace)
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
