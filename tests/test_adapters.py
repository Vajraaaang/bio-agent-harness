"""Offline adapter contracts using tiny executable scripts, never model inference."""
import importlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import time
import types

import psutil
import pytest

from devharness_core.watchdog import HarnessResourceInterrupt


def _adapters():
    assert importlib.util.find_spec("devbench_engine.adapters") is not None, "execution adapter module is missing"
    return importlib.import_module("devbench_engine.adapters")


def _fake_cli(tmp_path, body, login_status="Logged in using ChatGPT", login_exit=0, login_delay=0):
    executable = tmp_path / "fake-codex"
    executable.write_text(
        "#!" + sys.executable + "\nimport json, os, sys, time, subprocess\nfrom pathlib import Path\n"
        "if sys.argv[1:] == ['login', 'status']:\n    time.sleep(" + str(login_delay) + ")\n    print(" + repr(login_status) + ")\n    sys.exit(" + str(login_exit) + ")\n"
        "work = Path.cwd()\nprompt = sys.stdin.read()\n" + body + "\n"
    )
    executable.chmod(0o700)
    workspace = tmp_path / "task"
    workspace.mkdir(exist_ok=True)
    (workspace / "answer_schema.json").write_text('{"type":"object"}')
    return str(executable), workspace


def test_agent_interface_is_abstract():
    with pytest.raises(TypeError):
        _adapters().AgentAdapter()


def test_mock_callback_receives_absolute_path_and_writes_artifact(tmp_path):
    seen = []

    def callback(instruction, workspace):
        seen.append((instruction, workspace))
        return {"biological_rationale": "deterministic"}

    adapter = _adapters().InProcessMockAdapter(callback)
    answer = adapter.execute("analyze", tmp_path)
    assert seen == [("analyze", tmp_path.resolve())]
    assert json.loads((tmp_path / "final_discovery.json").read_text()) == answer
    assert adapter.metadata["billing_mode"] == "mock"
    assert adapter.metadata["cost_usd"] == 0.0


def test_mock_default_imports_reference_lazily(tmp_path, monkeypatch):
    reference = types.ModuleType("experiments.reference_agent")
    reference.reference_agent = lambda instruction, workspace: {"instruction": instruction}
    monkeypatch.setitem(sys.modules, "experiments.reference_agent", reference)
    assert _adapters().InProcessMockAdapter().execute("analysis", tmp_path) == {"instruction": "analysis"}


def test_mock_captures_real_dispatch_events(tmp_path):
    from devharness_server.dispatcher import ToolDispatcher

    def callback(instruction, workspace):
        ToolDispatcher().execute("calculate_temporal_monotonicity", {"stage_values": {"E8.5": 1, "E10.5": 2}})
        return {"ok": True}

    adapter = _adapters().InProcessMockAdapter(callback)
    adapter.execute("analyze", tmp_path)
    assert len(adapter.tool_events) == 1
    assert adapter.tool_events[0]["tool_name"] == "calculate_temporal_monotonicity"
    assert adapter.tool_events[0]["succeeded"] is True


@pytest.mark.parametrize("answer", [[], {"invalid": float("nan")}])
def test_mock_rejects_non_object_or_non_finite_answer(tmp_path, answer):
    with pytest.raises(ValueError):
        _adapters().InProcessMockAdapter(lambda *_: answer).execute("analyze", tmp_path)


def test_api_callback_requires_an_explicit_key_and_writes_artifact(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-be-used")
    with pytest.raises(ValueError, match="explicit"):
        _adapters().APIAdapter(lambda *_: {}, api_key="")
    seen = []

    def callback(instruction, workspace, api_key):
        seen.append((instruction, workspace, api_key))
        return {"ok": True}

    adapter = _adapters().APIAdapter(callback, api_key="explicit-test-key")
    assert adapter.execute("analysis", tmp_path) == {"ok": True}
    assert seen == [("analysis", tmp_path.resolve(), "explicit-test-key")]
    assert json.loads((tmp_path / "final_discovery.json").read_text()) == {"ok": True}
    assert adapter.metadata["billing_mode"] == "api"
    assert adapter.metadata["cost_usd"] is None
    assert "explicit-test-key" not in json.dumps(adapter.metadata)


@pytest.mark.parametrize("status, code", [("Logged in using an API key", 0), ("Not logged in", 1), ("unknown status", 0)])
def test_cli_checks_subscription_login_before_any_exec(tmp_path, status, code):
    executable, work = _fake_cli(tmp_path, "(work / 'exec-started').write_text('unsafe')", status, code)
    with pytest.raises(RuntimeError, match="ChatGPT"):
        _adapters().CodexCLIAdapter(executable=executable).execute("analyze", work)
    assert not (work / "exec-started").exists()


def test_cli_command_authentication_and_stdin_contract(tmp_path, monkeypatch):
    executable, work = _fake_cli(tmp_path, """
answer = {"argv": sys.argv[1:], "prompt": prompt, "cwd": str(work),
          "keys": {key: os.environ.get(key) for key in ["OPENAI_API_KEY", "CODEX_API_KEY", "ANTHROPIC_API_KEY"]}}
(work / "final_discovery.json").write_text(json.dumps(answer))
print(json.dumps({"type": "turn.completed", "usage": {"input_tokens": 12, "output_tokens": 3}}))
""")
    for key in ("OPENAI_API_KEY", "CODEX_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.setenv(key, "must-not-reach-child")
    adapter = _adapters().CodexCLIAdapter(executable=executable, model="test-model")
    instruction = "literal $(no-shell) `command`\nanalysis"
    answer = adapter.execute(instruction, work)
    args = answer["argv"]
    assert args[:3] == ["exec", "--sandbox", "workspace-write"]
    assert args[args.index("--cd") + 1] == str(work.resolve())
    assert args[-1] == "-"
    assert "--prompt" not in args
    assert "--skip-git-repo-check" in args and "--json" in args and "--ignore-user-config" in args
    assert args[args.index("--color") + 1] == "never"
    assert args[args.index("--model") + 1] == "test-model"
    assert "--output-schema" not in args  # The permissive fixture uses mandatory local validation.
    assert args[args.index("--output-last-message") + 1] == str(work / "final_discovery.json")
    config = [args[index + 1] for index, value in enumerate(args[:-1]) if value == "-c"]
    assert 'forced_login_method="chatgpt"' in config
    assert 'model_provider="openai"' in config
    assert "mcp_servers.devharness.command=" + json.dumps(os.path.abspath(sys.executable)) in config
    assert 'mcp_servers.devharness.args=["-m", "devharness_server.server"]' in config
    assert "mcp_servers.devharness.cwd=" + json.dumps(str(work)) in config
    assert answer["prompt"].startswith(instruction + "\n")
    assert "NO Markdown" in answer["prompt"]
    assert answer["cwd"] == str(work.resolve())
    assert set(answer["keys"].values()) == {None}
    assert adapter.metadata["billing_mode"] == "chatgpt_subscription"
    assert adapter.metadata["cost_usd"] is None
    assert adapter.metadata["usage"] == {"input_tokens": 12, "output_tokens": 3}
    assert adapter.metadata["returncode"] == 0


def test_cli_missing_binary_has_offline_recovery_guidance(tmp_path):
    with pytest.raises(RuntimeError, match="--backend mock"):
        _adapters().CodexCLIAdapter(executable="devharness-no-such-codex-923871").execute("analyze", tmp_path)


def test_cli_captures_only_completed_devharness_mcp_events(tmp_path):
    events = [
        {"type": "item.completed", "item": {"type": "mcp_tool_call", "server": "devharness", "tool": "validate_design_matrix", "arguments": {"formula_str": "age + batch"}, "status": "completed", "result": {"content": [{"type": "text", "text": '{"is_full_rank": false}'}]}}},
        {"type": "item.completed", "item": {"type": "mcp_tool_call", "server": "devharness", "tool": "fit_temporal_trajectory", "arguments": "{\"time_col\":\"age\"}", "status": "completed", "result": {"structuredContent": {"genes": ["Sox2"]}}}},
        {"type": "item.completed", "item": {"type": "mcp_tool_call", "server": "devharness", "tool": "failed_tool", "arguments": {}, "status": "failed", "error": {"message": "failure"}, "result": None}},
        {"type": "item.completed", "item": {"type": "mcp_tool_call", "server": "other", "tool": "ignored", "status": "completed", "result": {}}},
        {"type": "item.started", "item": {"type": "mcp_tool_call", "server": "devharness", "tool": "ignored", "result": {}}},
        {"type": "item.completed", "item": {"type": "agent_message", "text": '{"tool_name":"forged"}'}},
    ]
    executable, work = _fake_cli(tmp_path,
        "(work / 'final_discovery.json').write_text('{\"ok\":true}')\n"
        + "\n".join("print(" + repr(json.dumps(event)) + ")" for event in events))
    adapter = _adapters().CodexCLIAdapter(executable=executable)
    adapter.execute("analyze", work)
    assert [event["tool_name"] for event in adapter.tool_events] == ["validate_design_matrix", "fit_temporal_trajectory", "failed_tool"]
    assert adapter.tool_events[0]["result"] == {"is_full_rank": False}
    assert adapter.tool_events[1]["arguments"] == {"time_col": "age"}
    assert adapter.tool_events[1]["result"] == {"genes": ["Sox2"]}
    assert [event["succeeded"] for event in adapter.tool_events] == [True, True, False]


def test_cli_diagnostics_are_bounded(tmp_path):
    executable, work = _fake_cli(tmp_path, """
sys.stdout.write('x' * 2000000 + '\\n')
sys.stderr.write('e' * 200000 + ' tail-marker')
(work / "final_discovery.json").write_text('{"ok":true}')
""")
    adapter = _adapters().CodexCLIAdapter(executable=executable)
    assert adapter.execute("analyze", work) == {"ok": True}
    assert len(adapter.metadata["stdout_tail"]) <= 4096
    assert len(adapter.metadata["stderr_tail"]) <= 4096
    assert adapter.metadata["stderr_tail"].endswith("tail-marker")


def test_cli_removes_stale_artifact_and_requires_a_fresh_answer(tmp_path):
    executable, work = _fake_cli(tmp_path, "pass")
    (work / "final_discovery.json").write_text('{"stale":true}')
    with pytest.raises(RuntimeError, match="final_discovery.json"):
        _adapters().CodexCLIAdapter(executable=executable).execute("analyze", work)


@pytest.mark.parametrize("content", ['[]', '{"x":NaN}', '{"x":1e999}', '{"broken":', '"' + 'x' * 1000001 + '"'],
                         ids=["array", "nan", "infinity", "malformed", "oversized"])
def test_cli_rejects_invalid_or_oversized_answer(tmp_path, content):
    executable, work = _fake_cli(tmp_path, "(work / 'final_discovery.json').write_text(" + repr(content) + ")")
    with pytest.raises(ValueError):
        _adapters().CodexCLIAdapter(executable=executable).execute("analyze", work)


def test_cli_rejects_answer_symlink(tmp_path):
    outside = tmp_path / "outside.json"
    outside.write_text('{"outside":true}')
    executable, work = _fake_cli(tmp_path, "(work / 'final_discovery.json').symlink_to(" + repr(str(outside)) + ")")
    with pytest.raises(ValueError, match="regular file|symlink"):
        _adapters().CodexCLIAdapter(executable=executable).execute("analyze", work)
    assert outside.read_text() == '{"outside":true}'


def test_cli_nonzero_exit_reports_failure_even_with_artifact(tmp_path):
    executable, work = _fake_cli(tmp_path, "(work / 'final_discovery.json').write_text('{}')\nsys.exit(7)")
    adapter = _adapters().CodexCLIAdapter(executable=executable)
    with pytest.raises(RuntimeError, match="7"):
        adapter.execute("analyze", work)
    assert adapter.metadata["returncode"] == 7


def test_cli_timeout_terminates_descendants(tmp_path):
    executable, work = _fake_cli(tmp_path, """
child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
(work / "child.pid").write_text(str(child.pid))
time.sleep(60)
""")
    adapter = _adapters().CodexCLIAdapter(executable=executable, timeout_sec=0.5)
    started = time.monotonic()
    with pytest.raises(HarnessResourceInterrupt, match="timed out"):
        adapter.execute("analyze", work)
    assert time.monotonic() - started < 5
    pid = int((work / "child.pid").read_text())
    assert not psutil.pid_exists(pid) or psutil.Process(pid).status() == psutil.STATUS_ZOMBIE


def test_cli_memory_watchdog_interrupts_worker(tmp_path):
    executable, work = _fake_cli(tmp_path, "allocation = bytearray(160 * 1024 * 1024)\ntime.sleep(60)")
    with pytest.raises(HarnessResourceInterrupt, match="memory"):
        _adapters().CodexCLIAdapter(executable=executable, max_mem_mb=80).execute("analyze", work)


@pytest.mark.parametrize("limit", [float("nan"), float("inf"), float("-inf")], ids=["nan", "inf", "negative_inf"])
@pytest.mark.parametrize("name", ["timeout_sec", "max_mem_mb"])
def test_cli_rejects_nonfinite_resource_limits(name, limit):
    with pytest.raises(ValueError, match="finite"):
        _adapters().CodexCLIAdapter(**{name: limit})


def test_cli_login_and_execution_share_one_deadline(tmp_path, monkeypatch):
    executable, work = _fake_cli(tmp_path, "pass")
    adapters = _adapters()
    clock = {"now": 0.0}
    budgets = []
    monkeypatch.setattr(adapters, "time", types.SimpleNamespace(monotonic=lambda: clock["now"]))
    adapter = adapters.CodexCLIAdapter(executable=executable, timeout_sec=10)

    def run_process(command, workspace, environment, instruction, timeout_sec, parse_events=True):
        budgets.append(timeout_sec)
        if not parse_events:
            clock["now"] = 4.0
            return 0, "Logged in using ChatGPT", ""
        (workspace / "final_discovery.json").write_text('{}')
        return 0, "", ""

    monkeypatch.setattr(adapter, "_run_process", run_process)
    adapter.execute("analyze", work)
    assert budgets == [10, 6]


def test_cli_token_total_does_not_count_cached_tokens_twice(tmp_path):
    executable, work = _fake_cli(tmp_path, """
(work / "final_discovery.json").write_text('{}')
print(json.dumps({"type": "turn.completed", "usage": {"input_tokens": 12, "cached_input_tokens": 7, "output_tokens": 3}}))
""")
    adapter = _adapters().CodexCLIAdapter(executable=executable)
    adapter.execute("analyze", work)
    assert adapter.metadata["tokens"] == 15
    assert adapter.metadata["usage"]["cached_input_tokens"] == 7
    assert adapter.metadata["cost_usd"] is None


@pytest.mark.skipif(os.name != "posix", reason="POSIX process-group lifecycle")
def test_cli_success_cleans_unobserved_children_holding_output(tmp_path):
    executable, work = _fake_cli(tmp_path, """
child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
(work / "child.pid").write_text(str(child.pid))
(work / "final_discovery.json").write_text('{"ok":true}')
os._exit(0)
""")
    adapter = _adapters().CodexCLIAdapter(executable=executable)
    started = time.monotonic()
    try:
        assert adapter.execute("analyze", work) == {"ok": True}
        pid = int((work / "child.pid").read_text())
        assert not psutil.pid_exists(pid) or psutil.Process(pid).status() == psutil.STATUS_ZOMBIE
        assert time.monotonic() - started < 3
    finally:
        # A red regression test must not leave its deliberately leaked sleeper.
        if (work / "child.pid").exists():
            try:
                child = psutil.Process(int((work / "child.pid").read_text()))
                child.kill()
                child.wait(timeout=2)
            except (psutil.NoSuchProcess, psutil.TimeoutExpired):
                pass


def _fake_claude(tmp_path, body, auth=None, auth_exit=0):
    auth = auth if auth is not None else {"loggedIn": True, "authMethod": "claude.ai", "apiProvider": "firstParty", "subscriptionType": "max"}
    executable = tmp_path / "fake-claude"
    executable.write_text(
        "#!" + sys.executable + "\nimport json, os, sys, time, subprocess\nfrom pathlib import Path\n"
        "if sys.argv[1:] == ['auth', 'status']:\n    print(" + repr(json.dumps(auth)) + ")\n    sys.exit(" + str(auth_exit) + ")\n"
        "work = Path.cwd()\nprompt = sys.stdin.read()\n" + body + "\n"
    )
    executable.chmod(0o700)
    workspace = tmp_path / "task"
    workspace.mkdir(exist_ok=True)
    (workspace / "answer_schema.json").write_text('{"type":"object"}')
    return str(executable), workspace


@pytest.mark.parametrize("auth", [
    {"loggedIn": False},
    {"loggedIn": True, "authMethod": "api_key", "apiProvider": "firstParty"},
    {"loggedIn": True, "authMethod": "claude.ai", "apiProvider": "bedrock"},
    {"loggedIn": True, "authMethod": "unknown"},
    {"loggedIn": "true", "authMethod": "claude.ai"},
])
def test_claude_requires_verified_subscription_auth(tmp_path, auth):
    executable, work = _fake_claude(tmp_path, "(work / 'exec-started').write_text('unsafe')", auth=auth)
    adapter = _adapters().ClaudeCLIAdapter(executable=executable)
    with pytest.raises(RuntimeError, match="subscription"):
        adapter.execute("analyze", work)
    assert not (work / "exec-started").exists()
    assert adapter.metadata["preflight_failure"] == "subscription_authentication"


def test_claude_subscription_command_and_structured_output(tmp_path, monkeypatch):
    executable, work = _fake_claude(tmp_path, '''
args = sys.argv[1:]
config = json.loads(Path(args[args.index("--mcp-config") + 1]).read_text())
answer = {"argv": args, "prompt": prompt, "config": config, "cwd": str(work),
          "keys": {key: os.environ.get(key) for key in ["ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "OPENAI_API_KEY", "CODEX_API_KEY", "CLAUDE_CODE_USE_BEDROCK", "ANTHROPIC_BASE_URL", "CLAUDE_CODE_OAUTH_TOKEN"]}}
print(json.dumps({"type": "system", "subtype": "init", "session_id": args[args.index("--session-id") + 1], "model": "claude-test-resolved"}))
print(json.dumps({"type": "assistant", "message": {"model": "claude-test-resolved", "content": [{"type": "thinking", "thinking": "HIDDEN-REASONING-MARKER"}, {"type": "text", "text": "Analysis complete."}]}}))
print(json.dumps({"type": "result", "subtype": "success", "is_error": False, "structured_output": answer,
                  "usage": {"input_tokens": 12, "output_tokens": 3, "cache_read_input_tokens": 7, "cache_creation_input_tokens": 2}, "total_cost_usd": 0.2}))
''')
    for key in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "OPENAI_API_KEY", "CODEX_API_KEY", "CLAUDE_CODE_USE_BEDROCK", "ANTHROPIC_BASE_URL", "CLAUDE_CODE_OAUTH_TOKEN"):
        monkeypatch.setenv(key, "must-not-reach-child")
    adapter = _adapters().ClaudeCLIAdapter(executable=executable, model="test-model")
    answer = adapter.execute("literal $(no-shell) `command`\nanalysis", work)
    args = answer["argv"]
    assert "-p" in args and "--verbose" in args and "--restricted" in args
    assert args[args.index("--output-format") + 1] == "stream-json"
    assert args[args.index("--permission-mode") + 1] == "dontAsk"
    assert args[args.index("--setting-sources") + 1] == ""
    assert args[args.index("--tools") + 1] == "Read,Write"
    assert "--strict-mcp-config" in args
    assert "mcp__devharness__*" in args[args.index("--allowedTools") + 1]
    assert not {"--bare", "--dangerously-skip-permissions", "--allow-dangerously-skip-permissions", "bypassPermissions"}.intersection(args)
    assert args[args.index("--model") + 1] == "test-model"
    assert "--json-schema" not in args  # The permissive fixture uses mandatory local validation.
    assert adapter.metadata["turns"][0]["schema_enforcement"] == "local"
    assert set(answer["keys"].values()) == {None}
    assert answer["config"]["mcpServers"] == {"devharness": {"command": os.path.abspath(sys.executable), "args": ["-m", "devharness_server.server"], "cwd": str(work)}}
    assert answer["prompt"].startswith("literal $(no-shell)")
    assert json.loads((work / "final_discovery.json").read_text()) == answer
    assert adapter.metadata["billing_mode"] == "claude_subscription"
    assert adapter.metadata["cost_usd"] is None
    assert adapter.metadata["reported_usage_cost_usd"] == 0.2
    assert adapter.metadata["model_ids"] == ["claude-test-resolved"]
    assert adapter.metadata["tokens"] == 24
    assert adapter.metadata["usage"]["cache_read_input_tokens"] == 7
    assert "HIDDEN-REASONING-MARKER" not in json.dumps(adapter.metadata) + json.dumps(adapter.events)
    assert adapter.metadata["turns"][0]["stdout_bytes"] > 0
    assert adapter.metadata["turns"][0]["memory_measurement"] == "sampled_process_tree_rss"


def test_claude_observable_tool_errors_and_correction(tmp_path):
    records = [
        {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "one", "name": "mcp__devharness__fit_temporal_trajectory", "input": {"formula_str": "age + batch"}}]}},
        {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "one", "is_error": True, "content": "rank deficient"}]}},
        {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "two", "name": "mcp__devharness__fit_temporal_trajectory", "input": {"formula_str": "age"}}]}},
        {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "two", "content": [{"type": "text", "text": '{"genes":["Sox2"]}'}]}]}},
        {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "unknown", "content": '{"forged":true}'}]}},
        {"type": "assistant", "message": {"content": [{"type": "text", "text": '{"tool_name":"forged"}'}]}},
        {"type": "result", "subtype": "success", "structured_output": {"ok": True}},
    ]
    executable, work = _fake_claude(tmp_path, "\n".join("print(" + repr(json.dumps(record)) + ")" for record in records))
    adapter = _adapters().ClaudeCLIAdapter(executable=executable)
    adapter.execute("analyze", work)
    assert [event["succeeded"] for event in adapter.tool_events] == [False, True]
    assert adapter.tool_events[0]["error"] == "rank deficient"
    assert adapter.tool_events[1]["result"] == {"genes": ["Sox2"]}
    assert adapter.metadata["turns"][0]["tool_errors"] == 1
    assert adapter.metadata["turns"][0]["self_corrections"] == 1
    assert any(event["type"] == "self_correction" for event in adapter.events)


@pytest.mark.parametrize("body", [
    'print(json.dumps({"type":"result","subtype":"success","structured_output":[]}))',
    'print(json.dumps({"type":"result","subtype":"success","structured_output":{"ok":float("nan")}}))',
    'print(json.dumps({"type":"result","subtype":"error_max_turns","is_error":True,"structured_output":{}}))',
    'print(json.dumps({"type":"result","subtype":"success","structured_output":{"wrong":True}}))',
])
def test_claude_requires_successful_schema_valid_final_result(tmp_path, body):
    executable, work = _fake_claude(tmp_path, body)
    (work / "answer_schema.json").write_text('{"type":"object","properties":{"ok":{"type":"boolean"}},"required":["ok"],"additionalProperties":false}')
    with pytest.raises((ValueError, RuntimeError)):
        _adapters().ClaudeCLIAdapter(executable=executable).execute("analyze", work)
    assert not (work / "final_discovery.json").exists()


def test_claude_baseline_does_not_register_domain_tools(tmp_path):
    executable, work = _fake_claude(tmp_path, '''
args = sys.argv[1:]
config = json.loads(Path(args[args.index("--mcp-config") + 1]).read_text())
print(json.dumps({"type":"result","subtype":"success","structured_output":{"argv":args,"config":config}}))
''')
    adapter = _adapters().ClaudeCLIAdapter(executable=executable, condition="baseline")
    answer = adapter.execute("analyze", work)
    args = answer["argv"]
    assert args[args.index("--tools") + 1] == "Read,Write,Edit,Bash"
    assert "mcp__devharness" not in " ".join(args)
    assert answer["config"] == {"mcpServers": {}}
    assert adapter.metadata["condition"] == "baseline"


def test_codex_baseline_does_not_register_domain_tools(tmp_path):
    executable, work = _fake_cli(tmp_path, "(work / 'final_discovery.json').write_text(json.dumps({'argv': sys.argv[1:]}))")
    answer = _adapters().CodexCLIAdapter(executable=executable, condition="baseline").execute("analyze", work)
    assert "mcp_servers.devharness" not in " ".join(answer["argv"])


def test_claude_reuses_memory_watchdog_and_process_cleanup(tmp_path):
    executable, work = _fake_claude(tmp_path, "allocation = bytearray(160 * 1024 * 1024)\ntime.sleep(60)")
    with pytest.raises(HarnessResourceInterrupt, match="memory"):
        _adapters().ClaudeCLIAdapter(executable=executable, max_mem_mb=80).execute("analyze", work)


def test_claude_records_model_usage_and_permission_denials(tmp_path):
    executable, work = _fake_claude(tmp_path, '''
print(json.dumps({"type":"system","subtype":"init","model":"claude-actual-version"}))
print(json.dumps({"type":"result","subtype":"success","structured_output":{"ok":True},
                  "modelUsage":{"claude-actual-version":{"inputTokens":8,"outputTokens":2,"cacheReadInputTokens":3,"costUSD":0.1}},
                  "permission_denials":[{"tool_name":"Bash","tool_use_id":"denied","tool_input":{"command":"python analysis.py"}}]}))
''')
    adapter = _adapters().ClaudeCLIAdapter(executable=executable)
    adapter.execute("analyze", work)
    assert adapter.metadata["actual_model"] == "claude-actual-version"
    assert adapter.metadata["model_usage"]["claude-actual-version"]["cacheReadInputTokens"] == 3
    assert any(event["type"] == "permission_denied" and event["tool_name"] == "Bash" for event in adapter.events)
    assert adapter.metadata["turns"][0]["errors"]


def test_codex_records_tool_errors_beyond_domain_mcp(tmp_path):
    records = [
        {"type":"item.completed","item":{"type":"command_execution","id":"cmd1","command":"python analysis.py","exit_code":1,"status":"completed","aggregated_output":"ValueError: bad design"}},
        {"type":"error","message":"stream interrupted"},
        {"type":"item.completed","item":{"type":"file_change","id":"f1","status":"failed","error":"write denied"}},
    ]
    executable, work = _fake_cli(tmp_path, "(work / 'final_discovery.json').write_text('{}')\n" + "\n".join("print(" + repr(json.dumps(record)) + ")" for record in records))
    adapter = _adapters().CodexCLIAdapter(executable=executable)
    adapter.execute("analyze", work)
    assert len(adapter.metadata["turns"][0]["errors"]) == 3
    assert adapter.metadata["actual_model"] is None
    assert adapter.metadata["turns"][0]["tool_errors"] == 2


def test_claude_does_not_leave_tool_written_artifact_after_invalid_result(tmp_path):
    executable, work = _fake_claude(tmp_path, '''
(work / "final_discovery.json").write_text('{"premature":true}')
print(json.dumps({"type":"result","subtype":"error_during_execution","is_error":True,"errors":["execution failed"]}))
''')
    with pytest.raises(RuntimeError):
        _adapters().ClaudeCLIAdapter(executable=executable).execute("analyze", work)
    assert not (work / "final_discovery.json").exists()
