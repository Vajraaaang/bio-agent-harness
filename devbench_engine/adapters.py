"""Subscription-authenticated Codex execution and deterministic offline adapters."""
from abc import ABC, abstractmethod
import json
import math
import os
from pathlib import Path
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from typing import Callable, Dict, List, Optional, Tuple

from devbench_engine.lifecycle import PHASE_SCHEMA, lifecycle_phases, phase_prompt, strict_schema_compatible

from devharness_core.watchdog import HarnessResourceInterrupt, MemoryWatchdog

_MAX_ANSWER_BYTES = 1_000_000
_TAIL_BYTES = 4096
_MAX_EVENT_BYTES = 1_000_000
_MAX_TRACE_BYTES = 8_000_000


def _signal_owned_process_group(process: subprocess.Popen, signum: int) -> None:
    """Signal only the private POSIX group created with start_new_session=True.

    The group can outlive its leader, unlike the psutil parent/child relation.
    This catches descendants born and orphaned between watchdog observations.
    """
    if os.name == "posix" and process.pid > 0 and process.pid != os.getpgrp():
        try:
            os.killpg(process.pid, signum)
        except (ProcessLookupError, PermissionError):
            # Darwin can report EPERM for a group with no signalable members
            # after the watchdog terminated it. Known-process cleanup still
            # follows, and open output streams are checked before success.
            pass


def _answer_limit_label(max_bytes: int) -> str:
    if type(max_bytes) is not int or not 1 <= max_bytes <= 8_000_000:
        raise ValueError("Answer byte budget must be an integer between 1 and 8000000")
    return "1 MB" if max_bytes == _MAX_ANSWER_BYTES else f"{max_bytes} bytes"


def _answer_object(value: object, *, max_bytes: int = _MAX_ANSWER_BYTES) -> dict:
    limit_label = _answer_limit_label(max_bytes)
    if not isinstance(value, dict):
        raise ValueError("final_discovery.json must contain a JSON object")
    # This also rejects overflowed JSON numbers such as 1e999.
    encoded = json.dumps(value, allow_nan=False, separators=(",", ":")).encode()
    if len(encoded) > max_bytes:
        raise ValueError("final_discovery.json exceeds " + limit_label)
    return value


def _read_answer(workspace: Path, filename: str = "final_discovery.json", *, max_bytes: int = _MAX_ANSWER_BYTES) -> dict:
    limit_label = _answer_limit_label(max_bytes)
    artifact = workspace / filename
    try:
        descriptor = os.open(artifact, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError as error:
        raise RuntimeError("CLI did not produce " + filename) from error
    except OSError as error:
        raise ValueError("final_discovery.json must be a regular file, not a symlink") from error
    with os.fdopen(descriptor, "rb") as handle:
        details = os.fstat(handle.fileno())
        if not stat.S_ISREG(details.st_mode):
            raise ValueError("final_discovery.json must be a regular file")
        if details.st_size > max_bytes:
            raise ValueError("final_discovery.json exceeds " + limit_label)
        content = handle.read(max_bytes + 1)
    if len(content) > max_bytes:
        raise ValueError("final_discovery.json exceeds " + limit_label)
    return _plain_json_object(content, max_bytes=max_bytes)


def _fresh_workspace(workspace_dir: Path) -> Path:
    workspace = Path(workspace_dir).expanduser().resolve(strict=True)
    if not workspace.is_dir():
        raise ValueError("Task workspace must be a directory")
    (workspace / "final_discovery.json").unlink(missing_ok=True)
    return workspace


class AgentAdapter(ABC):
    """Execute one instruction and expose adapter-observed diagnostics."""

    def __init__(self) -> None:
        self.tool_events: List[dict] = []
        self.events: List[dict] = []
        self.metadata: Dict = {}

    @abstractmethod
    def execute(self, instruction: str, workspace_dir: Path) -> dict:
        """Return the task answer and write final_discovery.json in the workspace."""


class InProcessMockAdapter(AgentAdapter):
    """Run a trusted deterministic callback without model inference."""

    def __init__(self, callback: Optional[Callable[[str, Path], dict]] = None) -> None:
        super().__init__()
        self.callback = callback

    def execute(self, instruction: str, workspace_dir: Path) -> dict:
        from devharness_server.dispatcher import capture_tool_events

        self.tool_events = []
        self.metadata = {"billing_mode": "mock", "cost_usd": 0.0}
        workspace = _fresh_workspace(workspace_dir)
        callback = self.callback
        if callback is None:
            from experiments.reference_agent import reference_agent
            callback = reference_agent
        started = time.monotonic()
        try:
            with capture_tool_events() as events:
                self.tool_events = events
                answer = _answer_object(callback(instruction, workspace))
            # Replacing a temporary file cannot follow a callback-created symlink.
            with tempfile.NamedTemporaryFile(mode="w", dir=workspace, delete=False, encoding="utf-8") as handle:
                temporary = Path(handle.name)
                json.dump(answer, handle, allow_nan=False)
            try:
                os.replace(temporary, workspace / "final_discovery.json")
            finally:
                temporary.unlink(missing_ok=True)
            return answer
        finally:
            self.metadata["duration_sec"] = time.monotonic() - started


class APIAdapter(InProcessMockAdapter):
    """Opt-in callback integration; the callback owns its API client and billing.

    Keys must be supplied explicitly. This adapter never searches environment
    variables or performs a built-in provider request.
    """

    def __init__(self, callback: Callable[[str, Path, str], dict], api_key: str) -> None:
        if not isinstance(api_key, str) or not api_key.strip():
            raise ValueError("APIAdapter requires an explicitly supplied nonempty API key")
        if not callable(callback):
            raise TypeError("APIAdapter requires a callable API integration")
        super().__init__(lambda instruction, workspace: callback(instruction, workspace, api_key))

    def execute(self, instruction: str, workspace_dir: Path) -> dict:
        try:
            return super().execute(instruction, workspace_dir)
        finally:
            self.metadata.update(billing_mode="api", cost_usd=None)


def _decoded_object(value: object) -> Optional[dict]:
    try:
        if isinstance(value, str):
            value = json.loads(value)
        if isinstance(value, dict):
            json.dumps(value, allow_nan=False)
            return value
    except (TypeError, ValueError):
        pass
    return None


class _OutputCapture:
    """Drain a pipe without retaining terminal dumps or unbounded event lines."""

    def __init__(self, stream, on_event: Optional[Callable[[dict], None]] = None) -> None:
        self.stream = stream
        self.on_event = on_event
        self.tail = bytearray()
        self.bytes_seen = 0
        self.dropped_event_lines = 0
        self.malformed_event_lines = 0
        self.non_object_event_lines = 0
        self.error: Optional[Exception] = None
        self.thread = threading.Thread(target=self._run, daemon=True, name="devbench-cli-output")

    def _run(self) -> None:
        pending = bytearray()
        dropping = False
        try:
            while True:
                block = self.stream.read1(8192)
                if not block:
                    break
                self.bytes_seen += len(block)
                # Raw stream JSON can contain private thinking blocks. Keep only
                # the event parser's observable summaries for inference stdout.
                if self.on_event is None:
                    self.tail.extend(block)
                    del self.tail[:-_TAIL_BYTES]
                if self.on_event is None:
                    continue
                pieces = block.split(b"\n")
                for index, piece in enumerate(pieces):
                    if not dropping:
                        pending.extend(piece)
                        if len(pending) > _MAX_EVENT_BYTES:
                            pending.clear()
                            dropping = True
                            self.dropped_event_lines += 1
                    if index < len(pieces) - 1:
                        if not dropping:
                            self._event(bytes(pending))
                        pending.clear()
                        dropping = False
            if pending and not dropping:
                self._event(bytes(pending))
        except Exception as error:
            self.error = error

    def _event(self, line: bytes) -> None:
        try:
            record = json.loads(line)
            json.dumps(record, allow_nan=False)
        except (ValueError, UnicodeError):
            self.malformed_event_lines += 1
            return
        if isinstance(record, dict):
            self.on_event(record)
        else:
            self.non_object_event_lines += 1

    def text(self) -> str:
        return bytes(self.tail).decode("utf-8", errors="replace")[-_TAIL_BYTES:]


def local_schema_prompt(instruction, schema_path):
    """One pure native/local response wrapper shared with admission counters."""
    return (instruction + "\nReturn exactly one plain JSON object matching " + str(schema_path)
            + ". NO Markdown, code fences, prose, or trailing text. The original workspace "
            "answer_schema.json is authoritative; preserve its dynamic map keys and optional fields. "
            "Every answer is validated locally against that original schema before acceptance.")


class CodexCLIAdapter(AgentAdapter):
    """Run the local CLI using ChatGPT login; subscription usage limits apply.

    JSONL output supplies actual MCP events. Answer-reported histories are never
    used as evidence. Diagnostics remain bounded and no API price is inferred.
    One timeout budget covers login verification and execution, followed by
    bounded process cleanup.
    """

    backend_name = "Codex CLI"
    billing_mode = "chatgpt_subscription"

    def __init__(self, executable: str = "codex", timeout_sec: float = 300,
                 model: Optional[str] = None, max_mem_mb: float = 8000,
                 max_turns: Optional[int] = None, lifecycle: Optional[str] = None,
                 condition: str = "assisted") -> None:
        super().__init__()
        timeout_sec, max_mem_mb = float(timeout_sec), float(max_mem_mb)
        if not math.isfinite(timeout_sec) or not math.isfinite(max_mem_mb) or timeout_sec <= 0 or max_mem_mb <= 0:
            raise ValueError("Timeout and memory limit must be finite and positive")
        if condition not in ("assisted", "baseline"):
            raise ValueError("condition must be assisted or baseline")
        self.phases = lifecycle_phases(max_turns, lifecycle)
        self.condition = condition
        self.executable = executable
        self.timeout_sec = timeout_sec
        self.model = model
        self.max_mem_mb = max_mem_mb
        self._trace_bytes = 0
        self._session_id = None
        self._turn_usage = None
        self._turn_result = None
        self._pending_tools = {}
        self._failed_tools = {}
        self._last_process_measurements = {}
        self._phase = "synthesis"
        self._schema_enforcement = "provider_and_local"
        self._active_schema_definition = {}

    def _observe(self, event: dict) -> None:
        event = dict(event, phase=self._phase)
        self._trace_bytes += len(json.dumps(event, allow_nan=False).encode())
        if self._trace_bytes > _MAX_TRACE_BYTES or len(self.events) >= 2048:
            raise RuntimeError(self.backend_name + " observable event trace exceeds its bounded capture budget")
        self.events.append(event)

    def _model(self, model: object) -> None:
        if isinstance(model, str) and model and model not in self.metadata["model_ids"]:
            self.metadata["model_ids"].append(model)
            if self.metadata.get("actual_model") is None:
                self.metadata["actual_model"] = model

    def _usage(self, usage: object) -> None:
        if isinstance(usage, dict):
            self._turn_usage = {key: value for key, value in usage.items()
                                if isinstance(value, int) and not isinstance(value, bool) and value >= 0}

    def _token_total(self, usage: Optional[dict]) -> Optional[int]:
        if usage is None or "input_tokens" not in usage or "output_tokens" not in usage:
            return None
        return usage["input_tokens"] + usage["output_tokens"]

    def _completed_tool(self, event: dict, call_id: Optional[str] = None, domain: bool = True) -> None:
        if domain:
            self._trace_bytes += len(json.dumps(event, allow_nan=False).encode())
            if self._trace_bytes > _MAX_TRACE_BYTES or len(self.tool_events) >= 256:
                raise RuntimeError(self.backend_name + " tool event trace exceeds its bounded capture budget")
            self.tool_events.append(event)
        summary = {"type": "tool_result", "call_id": call_id, "tool_name": event["tool_name"],
                   "succeeded": event["succeeded"], "result_summary": json.dumps(event.get("result", {}), allow_nan=False)[:1000]}
        if event.get("error"):
            summary["error"] = str(event["error"])[:2000]
        self._observe(summary)
        previous = self._failed_tools.get(event["tool_name"])
        if event["succeeded"]:
            if previous is not None:
                if previous.get("arguments") != event.get("arguments"):
                    self._observe({"type": "self_correction", "tool_name": event["tool_name"],
                                   "failed_call_id": previous.get("call_id"), "successful_call_id": call_id,
                                   "basis": "observed failure followed by changed arguments and successful result"})
                self._failed_tools.pop(event["tool_name"], None)
        else:
            self._failed_tools[event["tool_name"]] = dict(event, call_id=call_id)

    def _event(self, record: dict) -> None:
        event_type = record.get("type")
        if event_type == "thread.started" and isinstance(record.get("thread_id"), str):
            self._session_id = record["thread_id"]
            self._observe({"type": "session_started", "session_id": self._session_id})
            return
        if event_type == "turn.completed":
            self._usage(record.get("usage"))
            self._model(record.get("model"))
            return
        if event_type in ("error", "turn.failed"):
            self._observe({"type": "error", "error": str(record.get("error", record.get("message", "CLI turn failed")))[:2000]})
            return
        item = record.get("item")
        if not isinstance(item, dict):
            return
        self._model(item.get("model"))
        if event_type == "item.completed" and item.get("type") == "agent_message":
            self._observe({"type": "response_summary", "text": str(item.get("text", ""))[:2000]})
            return
        if item.get("type") == "command_execution" and event_type == "item.completed":
            succeeded = item.get("status") == "completed" and item.get("exit_code") == 0
            event = {"tool_name": "command_execution", "arguments": {"command": item.get("command")},
                     "result": {"exit_code": item.get("exit_code"), "output_excerpt": str(item.get("aggregated_output", ""))[:1000]},
                     "succeeded": succeeded}
            if not succeeded:
                event["error"] = str(item.get("aggregated_output", item.get("error", "Command failed")))[:2000]
            self._completed_tool(event, item.get("id"), domain=False)
            return
        if event_type == "item.completed" and item.get("type") == "file_change":
            event = {"tool_name": "file_change", "arguments": {}, "result": {},
                     "succeeded": item.get("status") == "completed" and not item.get("error")}
            if not event["succeeded"]:
                event["error"] = str(item.get("error", "File change failed"))[:2000]
            self._completed_tool(event, item.get("id"), domain=False)
            return
        if item.get("type") != "mcp_tool_call" or not isinstance(item.get("tool"), str):
            return
        if item.get("server") != "devharness":
            return
        arguments = _decoded_object(item.get("arguments"))
        if event_type == "item.started":
            self._observe({"type": "tool_use", "call_id": item.get("id"), "tool_name": item["tool"],
                           "arguments_summary": json.dumps(arguments or {})[:1000]})
            return
        if event_type != "item.completed":
            return
        result, is_error, error_text = _tool_result(item.get("result"))
        event = {"tool_name": item["tool"], "arguments": arguments or {}, "result": result or {},
                 "succeeded": (item.get("status") == "completed" and not item.get("error")
                               and not is_error and arguments is not None and result is not None)}
        if not event["succeeded"]:
            event["error"] = str(item.get("error") or error_text or "Tool completion did not contain a successful structured result")[:2000]
        self._completed_tool(event, item.get("id"))

    def _environment(self) -> Dict[str, str]:
        environment = os.environ.copy()
        for key in list(environment):
            if key.startswith(("ANTHROPIC_", "CLAUDE_", "OPENAI_")) or key == "CODEX_API_KEY":
                environment.pop(key, None)
        return environment

    def _preflight(self, executable: str, workspace: Path, environment: dict, budget: float) -> None:
        status, stdout, stderr = self._run_process(
            [executable, "login", "status"], workspace, environment, "", budget, parse_events=False)
        if status != 0 or "logged in using chatgpt" not in (stdout + "\n" + stderr).lower():
            self.metadata["preflight_failure"] = "subscription_authentication"
            raise RuntimeError("Codex CLI requires an active ChatGPT login. Run codex login and choose ChatGPT, or use --backend mock.")
        self.metadata["login_status"] = "chatgpt_verified"

    def _prepare_options(self, directory: Path, workspace: Path) -> None:
        pass

    def _command(self, executable: str, workspace: Path, schema: Path, output: Path,
                 resume: Optional[str]) -> List[str]:
        command = [executable, "exec", "--sandbox", "workspace-write", "--cd", str(workspace),
                   "--skip-git-repo-check", "--json", "--color", "never", "--ignore-user-config",
                   "-c", 'forced_login_method="chatgpt"', "-c", 'model_provider="openai"']
        if self.condition == "assisted":
            command.extend(["-c", "mcp_servers.devharness.command=" + json.dumps(os.path.abspath(sys.executable)),
                            "-c", 'mcp_servers.devharness.args=["-m", "devharness_server.server"]',
                            "-c", "mcp_servers.devharness.cwd=" + json.dumps(str(workspace))])
        if self.model is not None:
            command.extend(["--model", self.model])
        if resume is not None:
            # --sandbox, --cd and --color belong to `exec`, before `resume`.
            command.append("resume")
        if self._schema_enforcement == "provider_and_local":
            command.extend(["--output-schema", str(schema)])
        command.extend(["--output-last-message", str(output)])
        if resume is not None:
            command.append(resume)
        command.append("-")
        return command

    def _turn_answer(self, output: Path, schema: Path) -> dict:
        answer = _read_answer(output.parent, output.name)
        _validate_schema(answer, self._active_schema_definition)
        return answer

    def _output_path(self, workspace: Path, temporary: Path, phase: str, final: bool) -> Path:
        return workspace / "final_discovery.json" if final else temporary / (phase + ".json")

    def _discard_failed_output(self, output: Path) -> None:
        output.unlink(missing_ok=True)

    def _finish_turn(self, started: float, event_start: int, error: Optional[Exception],
                     continuity: str, answer: Optional[dict]) -> None:
        observed = self.events[event_start:]
        errors = [event.get("error", "Tool failed") for event in observed
                  if event["type"] in ("error", "permission_denied") or (event["type"] == "tool_result" and not event["succeeded"])]
        if error is not None and str(error)[:2000] not in errors:
            errors.append(str(error)[:2000])
        turn = {"phase": self._phase, "elapsed_sec": time.monotonic() - started,
                "usage": self._turn_usage, "tokens": self._token_total(self._turn_usage),
                "errors": errors, "tool_calls": sum(event["type"] == "tool_result" for event in observed),
                "tool_errors": sum(event["type"] == "tool_result" and not event["succeeded"] for event in observed),
                "self_corrections": sum(event["type"] == "self_correction" for event in observed),
                "continuity": continuity, "schema_enforcement": self._schema_enforcement,
                **self._last_process_measurements}
        if answer is not None and len(self.phases) > 1 and self._phase != "synthesis":
            turn["response_summary"] = answer
        self.metadata["turns"].append(turn)
        usages = [item["usage"] for item in self.metadata["turns"]]
        self.metadata["usage_complete"] = all(usage is not None for usage in usages)
        if self.metadata["usage_complete"]:
            self.metadata["usage"] = {key: sum(usage.get(key, 0) for usage in usages)
                                      for key in set().union(*(usage.keys() for usage in usages))}
            tokens = [item["tokens"] for item in self.metadata["turns"]]
            if all(value is not None for value in tokens):
                self.metadata["tokens"] = sum(tokens)
            else:
                self.metadata.pop("tokens", None)
        else:
            self.metadata.pop("usage", None)
            self.metadata.pop("tokens", None)
        self.metadata["tool_errors"] = sum(item["tool_errors"] for item in self.metadata["turns"])
        self.metadata["self_corrections"] = sum(item["self_corrections"] for item in self.metadata["turns"])

    def execute(self, instruction: str, workspace_dir: Path) -> dict:
        self.tool_events, self.events = [], []
        self._trace_bytes = 0
        self._session_id = None
        self._failed_tools, self._pending_tools = {}, {}
        self.metadata = {"billing_mode": self.billing_mode, "cost_usd": None, "condition": self.condition,
                         "turns": [], "model_ids": [], "actual_model": None, "requested_model": self.model,
                         "continuity": "single_turn", "memory_measurement": "sampled_process_tree_rss"}
        executable = shutil.which(self.executable)
        if executable is None:
            self.metadata["preflight_failure"] = "missing_executable"
            raise RuntimeError(self.backend_name + " executable was not found on PATH. Install the CLI and sign in with a subscription, or use --backend mock.")
        executable = os.path.abspath(executable)
        workspace = _fresh_workspace(workspace_dir)
        schema = workspace / "answer_schema.json"
        if schema.is_symlink() or not schema.is_file():
            raise ValueError("Task workspace must contain a regular answer_schema.json file")
        original_schema = _read_answer(workspace, "answer_schema.json")
        environment = self._environment()
        started = time.monotonic()
        carry_forward = None
        try:
            self._preflight(executable, workspace, environment, min(self.timeout_sec, 15))
            with tempfile.TemporaryDirectory(prefix=".devbench-cli-", dir=workspace) as temporary:
                temporary = Path(temporary)
                self._prepare_options(temporary, workspace)
                phase_schema = temporary / "phase_schema.json"
                phase_schema.write_text(json.dumps(PHASE_SCHEMA), encoding="utf-8")
                final_schema = temporary / "answer_schema.json"
                final_schema.write_text(json.dumps(original_schema, allow_nan=False), encoding="utf-8")
                for index, phase in enumerate(self.phases):
                    remaining = self.timeout_sec - (time.monotonic() - started)
                    if remaining <= 0:
                        raise HarnessResourceInterrupt(self.backend_name + " timed out during login verification or lifecycle")
                    self._phase = phase
                    self._turn_usage, self._turn_result = None, None
                    self._last_process_measurements = {}
                    final = index == len(self.phases) - 1
                    output = self._output_path(workspace, temporary, phase, final)
                    output.unlink(missing_ok=True)
                    self._active_schema_definition = original_schema if final else PHASE_SCHEMA
                    compatible = strict_schema_compatible(self._active_schema_definition)
                    active_schema = (final_schema if compatible else schema) if final else phase_schema
                    self._schema_enforcement = "provider_and_local" if compatible else "local"
                    resume = self._session_id if index else None
                    continuity = "new_session" if not index else ("native_session_resume" if resume else "bounded_explicit_state")
                    if index:
                        self.metadata["continuity"] = continuity
                    prompt = instruction if len(self.phases) == 1 else phase_prompt(
                        instruction, phase, self.condition, carry_forward if index and not resume else None)
                    if self._schema_enforcement == "local":
                        prompt = local_schema_prompt(prompt, active_schema)
                    command = self._command(executable, workspace, active_schema, output, resume)
                    turn_started, event_start = time.monotonic(), len(self.events)
                    answer, error = None, None
                    try:
                        status, _, _ = self._run_process(command, workspace, environment, prompt, remaining)
                        if status != 0:
                            raise RuntimeError(self.backend_name + " exited with status {}; inspect adapter metadata diagnostics".format(status))
                        answer = self._turn_answer(output, active_schema)
                        carry_forward = answer
                    except Exception as caught:
                        error = caught
                        self._discard_failed_output(output)
                        self._observe({"type": "error", "error": str(caught)[:2000], "source": "adapter"})
                        raise
                    finally:
                        self._finish_turn(turn_started, event_start, error, continuity, answer)
                    if final:
                        return answer
        finally:
            self.metadata["duration_sec"] = time.monotonic() - started
            if self._session_id:
                self.metadata["session_id"] = self._session_id

    def _process_observation(self, phase: str, error=None) -> None:
        """Optional payload-free lifecycle hook for protected coordinators."""

    def _run_process(self, command: List[str], workspace: Path, environment: Dict[str, str],
                     instruction: str, timeout_sec: float,
                     parse_events: bool = True) -> Tuple[int, str, str]:
        started = time.monotonic()
        process = None
        monitor = None
        captures: List[_OutputCapture] = []
        try:
            self._process_observation("process_startup")
            # A temporary input file avoids deadlocking a large prompt on stdin
            # while stdout/stderr need draining, and never invokes a shell.
            with tempfile.TemporaryFile() as prompt:
                prompt.write(instruction.encode("utf-8"))
                prompt.seek(0)
                process = subprocess.Popen(command, cwd=str(workspace), env=environment, stdin=prompt,
                                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
                monitor = MemoryWatchdog(process.pid, max_mem_mb=self.max_mem_mb).start()
                self._process_observation("wait")
                captures = [_OutputCapture(process.stdout, self._event if parse_events else None),
                            _OutputCapture(process.stderr)]
                for capture in captures:
                    capture.thread.start()
                while process.poll() is None:
                    if monitor.triggered:
                        raise HarnessResourceInterrupt(monitor.reason)
                    if time.monotonic() - started >= timeout_sec:
                        raise HarnessResourceInterrupt(self.backend_name + " timed out after {:.3g} seconds".format(timeout_sec))
                    for capture in captures:
                        if capture.error is not None:
                            raise RuntimeError(self.backend_name + " output capture failed: " + str(capture.error))
                    time.sleep(0.025)
                if monitor.triggered:
                    raise HarnessResourceInterrupt(monitor.reason)
        except KeyboardInterrupt as error:
            raise HarnessResourceInterrupt(self.backend_name + " execution was interrupted") from error
        finally:
            try:
                self._process_observation("process_cleanup", sys.exc_info()[1])
            except BaseException as observation_error:
                # Telemetry failure must never prevent owned-process cleanup.
                self.metadata["process_observation_error"] = type(observation_error).__name__
            if process is not None:
                _signal_owned_process_group(process, signal.SIGTERM)
            if monitor is not None:
                monitor.stop()
                monitor.terminate_tree()
                self.metadata["peak_rss_mb"] = max(self.metadata.get("peak_rss_mb", 0), monitor.peak_rss_mb)
            if process is not None:
                _signal_owned_process_group(process, signal.SIGKILL)
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=2)
                self.metadata["returncode"] = process.returncode
            for capture in captures:
                capture.thread.join(timeout=2)
                if not capture.thread.is_alive():
                    capture.stream.close()
            self._last_process_measurements = {
                "peak_rss_mb": monitor.peak_rss_mb if monitor is not None else None,
                "memory_measurement": "sampled_process_tree_rss",
                "stdout_bytes": captures[0].bytes_seen if captures else 0,
                "stderr_bytes": captures[1].bytes_seen if captures else 0,
                "returncode": process.returncode if process is not None else None,
            }
            if captures and parse_events:
                safe_tail = json.dumps(self.events[-4:], allow_nan=False)[-_TAIL_BYTES:]
                self.metadata.update(stdout_tail=safe_tail, stderr_tail=captures[1].text())
                counts = {name: getattr(captures[0], name) for name in (
                    "dropped_event_lines", "malformed_event_lines", "non_object_event_lines")}
                complete = not any(counts.values()) and all(
                    item.error is None and not item.thread.is_alive() for item in captures)
                self.metadata["event_capture_complete"] = self.metadata.get("event_capture_complete", True) and complete
                previous = self.metadata.get("event_capture_loss", {})
                self.metadata["event_capture_loss"] = {name: previous.get(name, 0) + count for name, count in counts.items()}
                self._last_process_measurements["event_capture_complete"] = complete
            self.metadata["duration_sec"] = time.monotonic() - started
        for capture in captures:
            if capture.error is not None:
                raise RuntimeError(self.backend_name + " output capture failed: " + str(capture.error))
            if capture.thread.is_alive():
                raise RuntimeError(self.backend_name + " output stream did not close after process cleanup")
        if self.metadata.get("process_observation_error"):
            raise RuntimeError(self.backend_name + " process lifecycle capture failed")
        return process.returncode, captures[0].text(), captures[1].text()



def _plain_json_object(content: object, *, max_bytes: int = _MAX_ANSWER_BYTES) -> dict:
    """Accept exactly one finite JSON object, with no fences or duplicate keys."""
    limit_label = _answer_limit_label(max_bytes)
    if not isinstance(content, (str, bytes)):
        raise ValueError("CLI result must contain a plain JSON object")
    if len(content if isinstance(content, bytes) else content.encode("utf-8")) > max_bytes:
        raise ValueError("final_discovery.json exceeds " + limit_label)

    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("CLI JSON contains duplicate object keys")
            result[key] = value
        return result

    return _answer_object(json.loads(content, object_pairs_hook=unique_object), max_bytes=max_bytes)


def _validate_schema(answer: dict, schema: dict) -> None:
    import jsonschema

    try:
        # Validate the pre-execution snapshot; a model cannot weaken this gate
        # by editing the staged answer_schema.json during a turn.
        jsonschema.validate(answer, schema)
    except (jsonschema.ValidationError, jsonschema.SchemaError) as error:
        raise ValueError("CLI answer does not match the output schema: " + error.message[:1000]) from error


def _tool_result(envelope: object) -> Tuple[Optional[dict], bool, str]:
    """Decode MCP envelopes and Claude's text/list result representations."""
    if isinstance(envelope, dict):
        is_error = bool(envelope.get("isError", envelope.get("is_error", False)))
        result = _decoded_object(envelope.get("structuredContent", envelope.get("structured_content")))
        if result is not None:
            return result, is_error, str(envelope.get("error", ""))[:2000]
        result, nested_error, text = _tool_result(envelope.get("content"))
        return result, is_error or nested_error, text
    if isinstance(envelope, list):
        text = []
        result = None
        for block in envelope:
            if isinstance(block, dict) and block.get("type") == "text":
                value = block.get("text", "")
                decoded = _decoded_object(value)
                if decoded is not None and result is None:
                    result = decoded
                text.append(str(value)[:2000])
        return result, False, "\n".join(text)[:2000]
    if isinstance(envelope, str):
        return _decoded_object(envelope), False, envelope[:2000]
    return None, False, ""


def _write_answer(answer: dict, output: Path) -> None:
    # Atomic replacement prevents following a tool-created output symlink.
    with tempfile.NamedTemporaryFile(mode="w", dir=output.parent, delete=False, encoding="utf-8") as handle:
        temporary = Path(handle.name)
        json.dump(answer, handle, allow_nan=False)
    try:
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)


class ClaudeCLIAdapter(CodexCLIAdapter):
    """Local Claude Code with verified subscription OAuth, never API-key billing.

    User settings and external MCP servers are disabled explicitly. The CLI's
    normal keychain OAuth remains available; no credential files are inspected.
    Inference uses restricted tools and dontAsk rather than permission bypass.
    """

    backend_name = "Claude CLI"
    billing_mode = "claude_subscription"

    def __init__(self, executable: str = "claude", timeout_sec: float = 300,
                 model: Optional[str] = None, max_mem_mb: float = 8000,
                 max_turns: Optional[int] = None, lifecycle: Optional[str] = None,
                 condition: str = "assisted") -> None:
        super().__init__(executable, timeout_sec, model, max_mem_mb, max_turns, lifecycle, condition)
        self._mcp_config = None

    def _preflight(self, executable: str, workspace: Path, environment: dict, budget: float) -> None:
        status, stdout, _ = self._run_process(
            [executable, "auth", "status"], workspace, environment, "", budget, parse_events=False)
        auth = _decoded_object(stdout)
        if (status != 0 or auth is None or auth.get("loggedIn") is not True
                or auth.get("authMethod") != "claude.ai" or auth.get("apiProvider") != "firstParty"
                or auth.get("apiKeySource") or auth.get("isApiKey") or auth.get("isCloud")):
            self.metadata["preflight_failure"] = "subscription_authentication"
            raise RuntimeError("Claude CLI requires a verified Claude subscription login (claude.ai OAuth, first-party provider). "
                               "Run claude auth login in your local terminal; unavailable keychain authentication is a preflight failure. "
                               "No model run was started. Use --backend mock for offline tests.")
        self.metadata.update(login_status="claude_subscription_verified", auth_method="claude.ai",
                             subscription_type=auth.get("subscriptionType"),
                             cost_note="Subscription charge is unknown. CLI reported usage-equivalent cost is not an account charge.")

    def _prepare_options(self, directory: Path, workspace: Path) -> None:
        servers = {}
        if self.condition == "assisted":
            servers["devharness"] = {"command": os.path.abspath(sys.executable),
                                     "args": ["-m", "devharness_server.server"], "cwd": str(workspace)}
        self._mcp_config = directory / "mcp.json"
        self._mcp_config.write_text(json.dumps({"mcpServers": servers}), encoding="utf-8")
        self._session_id = str(uuid.uuid4())

    def _command(self, executable: str, workspace: Path, schema: Path, output: Path,
                 resume: Optional[str]) -> List[str]:
        tools = "Read,Write" if self.condition == "assisted" else "Read,Write,Edit,Bash"
        allowed = tools + (",mcp__devharness__*" if self.condition == "assisted" else "")
        command = [executable, "-p", "--output-format", "stream-json", "--verbose",
                   "--restricted", "--permission-mode", "dontAsk", "--permission-prompts", "none",
                   "--setting-sources", "", "--strict-mcp-config", "--mcp-config", str(self._mcp_config),
                   "--tools", tools, "--allowedTools", allowed, "--disable-slash-commands", "--no-chrome"]
        if self._schema_enforcement == "provider_and_local":
            command.extend(["--json-schema", schema.read_text(encoding="utf-8")])
        command.extend(["--resume", resume] if resume else ["--session-id", self._session_id])
        if self.model is not None:
            command.extend(["--model", self.model])
        return command

    def _token_total(self, usage: Optional[dict]) -> Optional[int]:
        total = super()._token_total(usage)
        if total is None:
            return None
        # Claude reports uncached input separately from cache reads and writes.
        return total + usage.get("cache_read_input_tokens", 0) + usage.get("cache_creation_input_tokens", 0)

    def _event(self, record: dict) -> None:
        event_type = record.get("type")
        if event_type == "error":
            self._observe({"type": "error", "error": str(record.get("error", record.get("message", "CLI error")))[:2000]})
            return
        if event_type == "system":
            if record.get("subtype") == "error":
                self._observe({"type": "error", "error": str(record.get("error", record.get("message", "CLI system error")))[:2000]})
            if record.get("subtype") == "init":
                if record.get("apiKeySource") not in (None, "none"):
                    raise RuntimeError("Claude CLI reported API-key authentication during subscription execution")
                session_id = record.get("session_id")
                if isinstance(session_id, str):
                    if self._session_id is not None and self._session_id != session_id:
                        raise RuntimeError("Claude CLI resumed a different session than requested")
                    self._session_id = session_id
                self._model(record.get("model"))
                self._observe({"type": "session_started", "session_id": self._session_id})
            return
        if event_type == "result":
            self._turn_result = {"subtype": record.get("subtype"), "is_error": record.get("is_error"),
                                 "structured_output": record.get("structured_output"),
                                 "has_structured_output": "structured_output" in record,
                                 "result": record.get("result"), "errors": record.get("errors")}
            self._usage(record.get("usage"))
            model_usage = record.get("modelUsage")
            if isinstance(model_usage, dict):
                captured = self.metadata.setdefault("model_usage", {})
                for model, details in model_usage.items():
                    self._model(model)
                    if isinstance(details, dict):
                        target = captured.setdefault(model, {})
                        for key, value in details.items():
                            if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0:
                                # Capacity fields describe the model; token and cost fields are additive.
                                target[key] = value if key in ("contextWindow", "maxOutputTokens") else target.get(key, 0) + value
            denials = record.get("permission_denials")
            if isinstance(denials, list):
                for denial in denials:
                    if isinstance(denial, dict):
                        self._observe({"type": "permission_denied", "tool_name": str(denial.get("tool_name", "unknown")),
                                       "call_id": denial.get("tool_use_id"), "error": "CLI permission denied"})
            cost = record.get("total_cost_usd")
            if isinstance(cost, (int, float)) and not isinstance(cost, bool) and math.isfinite(cost) and cost >= 0:
                self.metadata["reported_usage_cost_usd"] = self.metadata.get("reported_usage_cost_usd", 0) + cost
            if record.get("is_error") or record.get("subtype") != "success":
                self._observe({"type": "error", "error": str(record.get("errors") or record.get("result") or record.get("subtype"))[:2000]})
            return
        message = record.get("message")
        if event_type not in ("assistant", "user") or not isinstance(message, dict):
            return
        self._model(message.get("model"))
        content = message.get("content")
        if not isinstance(content, list):
            return
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use" and event_type == "assistant":
                call_id, name = block.get("id"), block.get("name")
                if not isinstance(call_id, str) or not isinstance(name, str):
                    continue
                if call_id in self._pending_tools:
                    continue
                if len(self._pending_tools) >= 256:
                    raise RuntimeError("Claude CLI pending tool calls exceed the bounded capture budget")
                self._pending_tools[call_id] = {"name": name, "arguments": _decoded_object(block.get("input"))}
                self._observe({"type": "tool_use", "call_id": call_id, "tool_name": name,
                               "arguments_summary": json.dumps(block.get("input", {}), allow_nan=False)[:1000]})
            elif block.get("type") == "tool_result" and event_type == "user":
                call_id = block.get("tool_use_id")
                pending = self._pending_tools.pop(call_id, None)
                if pending is None:
                    continue
                domain = pending["name"].startswith("mcp__devharness__")
                result, nested_error, text = _tool_result(block.get("content"))
                is_error = bool(block.get("is_error")) or nested_error
                event = {"tool_name": pending["name"].removeprefix("mcp__devharness__"),
                         "arguments": pending["arguments"] or {}, "result": result if result is not None else {"text_excerpt": text[:1000]},
                         "succeeded": not is_error and (not domain or result is not None)}
                if not event["succeeded"]:
                    event["error"] = text or "Tool did not return a successful structured result"
                self._completed_tool(event, call_id, domain=domain)
            elif block.get("type") == "text" and event_type == "assistant":
                self._observe({"type": "response_summary", "text": str(block.get("text", ""))[:2000]})
            # Thinking/signature/redacted_thinking blocks are never retained.

    def _turn_answer(self, output: Path, schema: Path) -> dict:
        result = self._turn_result
        if result is None:
            raise RuntimeError("Claude CLI did not return a completed structured result")
        if result.get("is_error") or result.get("subtype") != "success":
            raise RuntimeError("Claude CLI turn failed: " + str(result.get("errors") or result.get("subtype"))[:2000])
        answer = (_answer_object(result.get("structured_output")) if result.get("has_structured_output")
                  else _plain_json_object(result.get("result")))
        _validate_schema(answer, self._active_schema_definition)
        _write_answer(answer, output)
        return answer
