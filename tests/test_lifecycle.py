"""Real subprocess contracts for four staged CLI turns, without model inference."""
import importlib
import json
import time

import pytest

from tests.test_adapters import _fake_claude, _fake_cli


@pytest.mark.parametrize("backend", ["claude", "codex"])
@pytest.mark.parametrize("condition", ["assisted", "baseline"])
def test_four_stage_session_and_phase_schemas(tmp_path, backend, condition):
    body = '''
args = sys.argv[1:]
log_path = work / "invocations.json"
log = json.loads(log_path.read_text()) if log_path.exists() else []
phase = ["exploration", "diagnostics", "modeling", "synthesis"][len(log)]
is_claude = "-p" in args
schema = json.loads(args[args.index("--json-schema") + 1]) if is_claude else json.loads(Path(args[args.index("--output-schema") + 1]).read_text())
log.append({"args": args, "prompt": prompt, "schema": schema})
log_path.write_text(json.dumps(log))
answer = {"phase_summary": "Observed phase " + phase, "next_steps": ["continue"]} if phase != "synthesis" else {"ok": True}
if is_claude:
    session = args[args.index("--session-id") + 1] if "--session-id" in args else args[args.index("--resume") + 1]
    print(json.dumps({"type": "system", "subtype": "init", "session_id": session, "model": "claude-resolved"}))
    print(json.dumps({"type": "result", "subtype": "success", "session_id": session, "structured_output": answer, "usage": {"input_tokens": 10, "output_tokens": 2}}))
else:
    print(json.dumps({"type": "thread.started", "thread_id": "session-test-id"}))
    Path(args[args.index("--output-last-message") + 1]).write_text(json.dumps(answer))
    print(json.dumps({"type": "turn.completed", "usage": {"input_tokens": 10, "output_tokens": 2}}))
'''
    executable, work = (_fake_claude if backend == "claude" else _fake_cli)(tmp_path, body)
    (work / "answer_schema.json").write_text('{"type":"object","properties":{"ok":{"type":"boolean"}},"required":["ok"],"additionalProperties":false}')
    adapters = importlib.import_module("devbench_engine.adapters")
    adapter_type = adapters.ClaudeCLIAdapter if backend == "claude" else adapters.CodexCLIAdapter
    adapter = adapter_type(executable=executable, lifecycle="four_stage", condition=condition)
    assert adapter.execute("Analyze the task and return final_discovery.json.", work) == {"ok": True}
    log = json.loads((work / "invocations.json").read_text())
    assert len(log) == 4
    assert [turn["phase"] for turn in adapter.metadata["turns"]] == ["exploration", "diagnostics", "modeling", "synthesis"]
    for index, invocation in enumerate(log):
        phase = adapter.metadata["turns"][index]["phase"]
        assert phase in invocation["prompt"]
        if index < 3:
            assert set(invocation["schema"]["required"]) == {"phase_summary", "next_steps"}
            assert "Do not write final_discovery.json" in invocation["prompt"]
        else:
            assert invocation["schema"]["required"] == ["ok"]
        if index:
            if backend == "claude":
                assert invocation["args"][invocation["args"].index("--resume") + 1] == log[0]["args"][log[0]["args"].index("--session-id") + 1]
                assert "--session-id" not in invocation["args"]
            else:
                assert "resume" in invocation["args"] and "session-test-id" in invocation["args"]
                # Parent exec flags must precede the resume subcommand.
                assert invocation["args"].index("--sandbox") < invocation["args"].index("resume")
        else:
            assert "--resume" not in invocation["args"] and "resume" not in invocation["args"]
        assert adapter.metadata["turns"][index]["tokens"] == 12
    assert adapter.metadata["continuity"] == "native_session_resume"
    assert adapter.metadata["tokens"] == 48
    assert adapter.metadata["usage"] == {"input_tokens": 40, "output_tokens": 8}


def test_codex_missing_session_uses_explicit_bounded_state(tmp_path):
    executable, work = _fake_cli(tmp_path, '''
args = sys.argv[1:]
log_path = work / "invocations.json"
log = json.loads(log_path.read_text()) if log_path.exists() else []
log.append({"args": args, "prompt": prompt})
log_path.write_text(json.dumps(log))
answer = {"phase_summary": "Earlier observed evidence", "next_steps": ["fit"]} if len(log) < 4 else {"ok": True}
Path(args[args.index("--output-last-message") + 1]).write_text(json.dumps(answer))
''')
    adapters = importlib.import_module("devbench_engine.adapters")
    adapter = adapters.CodexCLIAdapter(executable=executable, max_turns=4)
    adapter.execute("Analyze.", work)
    log = json.loads((work / "invocations.json").read_text())
    assert "Earlier observed evidence" in log[1]["prompt"]
    assert all("resume" not in invocation["args"] for invocation in log)
    assert adapter.metadata["continuity"] == "bounded_explicit_state"
    assert all(turn["usage"] is None and turn["tokens"] is None for turn in adapter.metadata["turns"])


def test_lifecycle_does_not_run_later_phases_after_error(tmp_path):
    executable, work = _fake_claude(tmp_path, '''
path = work / "count"
count = int(path.read_text()) if path.exists() else 0
path.write_text(str(count + 1))
print(json.dumps({"type":"result","subtype":"error_during_execution","is_error":True,"errors":["quota exhausted"]}))
''')
    adapter = importlib.import_module("devbench_engine.adapters").ClaudeCLIAdapter(executable=executable, max_turns=4)
    with pytest.raises(RuntimeError, match="quota exhausted"):
        adapter.execute("Analyze.", work)
    assert (work / "count").read_text() == "1"
    assert adapter.metadata["turns"][0]["phase"] == "exploration"
    assert adapter.metadata["turns"][0]["errors"]


@pytest.mark.parametrize("options", [{"max_turns": 0}, {"max_turns": 2}, {"max_turns": True}, {"lifecycle": "unknown"}, {"condition": "unknown"}, {"lifecycle": "four_stage", "max_turns": 1}])
def test_cli_rejects_unsupported_lifecycle_configuration(options):
    adapters = importlib.import_module("devbench_engine.adapters")
    for adapter_type in (adapters.CodexCLIAdapter, adapters.ClaudeCLIAdapter):
        with pytest.raises(ValueError):
            adapter_type(**options)


def test_missing_later_usage_does_not_report_partial_tokens_as_total(tmp_path):
    executable, work = _fake_cli(tmp_path, '''
args = sys.argv[1:]
count_path = work / "count"
count = int(count_path.read_text()) + 1 if count_path.exists() else 1
count_path.write_text(str(count))
answer = {"phase_summary":"Observed evidence", "next_steps":[]} if count < 4 else {"ok":True}
Path(args[args.index("--output-last-message") + 1]).write_text(json.dumps(answer))
if count == 1:
    print(json.dumps({"type":"turn.completed","usage":{"input_tokens":10,"output_tokens":2}}))
''')
    adapter = importlib.import_module("devbench_engine.adapters").CodexCLIAdapter(executable=executable, max_turns=4)
    adapter.execute("Analyze.", work)
    assert adapter.metadata.get("tokens") is None
    assert adapter.metadata.get("usage") is None
    assert adapter.metadata["usage_complete"] is False
    assert adapter.metadata["turns"][0]["tokens"] == 12


@pytest.mark.parametrize("schema,compatible", [
    ({"type":"object", "properties":{"ok":{"type":"boolean"}}, "required":["ok"], "additionalProperties":False}, True),
    ({"type":"object"}, False),
    ({"type":"object", "properties":{"optional":{"type":"number"}}, "required":[], "additionalProperties":False}, False),
    ({"type":"object", "properties":{"pseudotime":{"type":"object", "additionalProperties":{"type":"number"}}}, "required":["pseudotime"], "additionalProperties":False}, False),
    ({"type":"object", "properties":{"values":{"type":"array"}}, "required":["values"], "additionalProperties":False}, False),
    ({"type":"object", "properties":{"values":{"type":"array", "items":{"type":"number"}}}, "required":["values"], "additionalProperties":False}, True),
    ({"type":"object", "properties":{"node":{"$ref":"#/$defs/node"}}, "required":["node"], "additionalProperties":False,
      "$defs":{"node":{"type":"object", "properties":{"value":{"type":"number"}}, "required":["value"], "additionalProperties":False}}}, True),
    ({"type":"object", "properties":{"node":{"$ref":"#/$defs/node"}}, "required":["node"], "additionalProperties":False,
      "$defs":{"node":{"type":"object", "additionalProperties":{"type":"number"}}}}, False),
    ({"type":"object", "properties":{"node":{"$ref":"https://example.invalid/schema"}}, "required":["node"], "additionalProperties":False}, False),
    ({"type":"object", "properties":{"node":{"$ref":"#/$defs/missing"}}, "required":["node"], "additionalProperties":False}, False),
    ({"type":"object", "properties":{"next":{"anyOf":[{"$ref":"#"},{"type":"null"}]}}, "required":["next"], "additionalProperties":False}, True),
])
def test_strict_schema_transport_compatibility(schema, compatible):
    lifecycle = importlib.import_module("devbench_engine.lifecycle")
    assert lifecycle.strict_schema_compatible(schema) is compatible


def test_schema_transport_walk_is_bounded_and_does_not_change_input():
    lifecycle = importlib.import_module("devbench_engine.lifecycle")
    schema = {"type":"number"}
    for _ in range(100):
        schema = {"type":"object", "properties":{"child":schema}, "required":["child"], "additionalProperties":False}
    before = json.dumps(schema)
    assert lifecycle.strict_schema_compatible(schema) is False
    assert json.dumps(schema) == before
    assert lifecycle.strict_schema_compatible(lifecycle.PHASE_SCHEMA) is True


@pytest.mark.parametrize("backend", ["claude", "codex"])
@pytest.mark.parametrize("condition", ["assisted", "baseline"])
@pytest.mark.parametrize("strict", [False, True])
def test_schema_transport_policy_is_shared_and_final_validation_is_mandatory(tmp_path, backend, condition, strict):
    schema = {"type":"object", "properties":{"ok":{"type":"boolean"}}, "required":["ok"], "additionalProperties":False} if strict else {
        "type":"object", "properties":{"pseudotime":{"type":"object", "additionalProperties":{"type":"number"}}, "optional_note":{"type":"string"}},
        "required":["pseudotime"], "additionalProperties":False}
    answer = {"ok":True} if strict else {"pseudotime":{"cell-a":0.1,"cell-b":0.9}}
    body = '''
args = sys.argv[1:]
(work / "invocation.json").write_text(json.dumps({"args":args,"prompt":prompt}))
answer = ''' + repr(answer) + '''
if "-p" in args:
    if "--json-schema" in args:
        print(json.dumps({"type":"result","subtype":"success","structured_output":answer}))
    else:
        print(json.dumps({"type":"result","subtype":"success","result":json.dumps(answer)}))
else:
    Path(args[args.index("--output-last-message") + 1]).write_text(json.dumps(answer))
'''
    executable, work = (_fake_claude if backend == "claude" else _fake_cli)(tmp_path, body)
    original = json.dumps(schema)
    (work / "answer_schema.json").write_text(original)
    adapters = importlib.import_module("devbench_engine.adapters")
    adapter = (adapters.ClaudeCLIAdapter if backend == "claude" else adapters.CodexCLIAdapter)(executable=executable, condition=condition)
    assert adapter.execute("Analyze.", work) == answer
    assert (work / "answer_schema.json").read_text() == original
    invocation = json.loads((work / "invocation.json").read_text())
    flag = "--json-schema" if backend == "claude" else "--output-schema"
    assert (flag in invocation["args"]) is strict
    assert adapter.metadata["turns"][0]["schema_enforcement"] == ("provider_and_local" if strict else "local")
    if not strict:
        assert "answer_schema.json" in invocation["prompt"] and "NO Markdown" in invocation["prompt"]


@pytest.mark.parametrize("backend", ["claude", "codex"])
@pytest.mark.parametrize("text_answer", ['{"pseudotime":{"cell-a":"bad"}}', '```json\n{"pseudotime":{}}\n```', '{"pseudotime":{}} trailing', '{"pseudotime":{"cell-a":NaN}}', '{"pseudotime":{},"pseudotime":{}}'])
def test_local_enforcement_rejects_invalid_or_non_plain_json_and_removes_artifact(tmp_path, backend, text_answer):
    body = '''
args = sys.argv[1:]
text_answer = ''' + repr(text_answer) + '''
(work / "final_discovery.json").write_text(text_answer)
if "-p" in args:
    print(json.dumps({"type":"result","subtype":"success","result":text_answer}))
'''
    executable, work = (_fake_claude if backend == "claude" else _fake_cli)(tmp_path, body)
    (work / "answer_schema.json").write_text(json.dumps({"type":"object","properties":{"pseudotime":{"type":"object","additionalProperties":{"type":"number"}}},"required":["pseudotime"],"additionalProperties":False}))
    adapters = importlib.import_module("devbench_engine.adapters")
    adapter = (adapters.ClaudeCLIAdapter if backend == "claude" else adapters.CodexCLIAdapter)(executable=executable)
    with pytest.raises(ValueError):
        adapter.execute("Analyze.", work)
    assert not (work / "final_discovery.json").exists()
    assert adapter.metadata["turns"][0]["schema_enforcement"] == "local"
    assert adapter.metadata["turns"][0]["errors"]


@pytest.mark.parametrize("backend", ["claude", "codex"])
def test_local_validation_uses_original_schema_snapshot(tmp_path, backend):
    body = '''
args = sys.argv[1:]
(work / "answer_schema.json").write_text('{"type":"object"}')
answer = {"pseudotime":{"cell-a":"wrong type"}}
(work / "final_discovery.json").write_text(json.dumps(answer))
if "-p" in args:
    print(json.dumps({"type":"result","subtype":"success","result":json.dumps(answer)}))
'''
    executable, work = (_fake_claude if backend == "claude" else _fake_cli)(tmp_path, body)
    (work / "answer_schema.json").write_text(json.dumps({"type":"object","properties":{"pseudotime":{"type":"object","additionalProperties":{"type":"number"}}},"required":["pseudotime"],"additionalProperties":False}))
    adapters = importlib.import_module("devbench_engine.adapters")
    adapter = (adapters.ClaudeCLIAdapter if backend == "claude" else adapters.CodexCLIAdapter)(executable=executable)
    with pytest.raises(ValueError, match="schema"):
        adapter.execute("Analyze.", work)
    assert not (work / "final_discovery.json").exists()


def test_strict_schema_requires_a_resolvable_object_root():
    compatible = importlib.import_module("devbench_engine.lifecycle").strict_schema_compatible
    assert compatible({"type":"string"}) is False
    assert compatible({"$ref":"#"}) is False
    assert compatible({"$ref":"#/$defs/root", "$defs":{"root":{"type":"object", "properties":{}, "required":[], "additionalProperties":False}}}) is True
