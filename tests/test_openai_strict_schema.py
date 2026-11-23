"""Inspect Codex output schemas at the CLI boundary with offline fixtures."""

import ast
import json
from collections import Counter
from collections.abc import Iterator
from importlib.util import find_spec
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from devbench.analysis.episode_codex import EpisodeCodexAdapter, selection_contract
from devbench_engine.adapters import ClaudeCLIAdapter, CodexCLIAdapter
from devbench_engine.lifecycle import PHASE_SCHEMA
from tests.test_adapters import _fake_claude, _fake_cli
from tests.test_episode_codex import fixture_adapter

_UNSUPPORTED = {
    "minLength",
    "maxLength",
    "minItems",
    "maxItems",
    "minimum",
    "maximum",
    "exclusiveMinimum",
    "exclusiveMaximum",
    "uniqueItems",
    "maxProperties",
    "minProperties",
    "pattern",
    "format",
    "multipleOf",
    "propertyNames",
    "default",
    "oneOf",
}


def _schema_nodes(schema: dict) -> Iterator[dict]:
    yield schema
    for keyword in ("properties", "$defs", "definitions"):
        for child in schema.get(keyword, {}).values():
            yield from _schema_nodes(child)
    if "items" in schema:
        yield from _schema_nodes(schema["items"])
    for child in schema.get("anyOf", []):
        yield from _schema_nodes(child)


def _assert_strict_transport(schema: dict):
    Draft202012Validator.check_schema(schema)
    for node in _schema_nodes(schema):
        assert not _UNSUPPORTED.intersection(node)
        if node.get("type") == "object" or "properties" in node:
            assert node["additionalProperties"] is False
            assert set(node.get("required", [])) == set(node.get("properties", {}))


def _transport_projection_calls(package: str) -> Counter[str]:
    spec = find_spec(package)
    assert spec is not None and spec.origin is not None
    root = Path(spec.origin).resolve().parent
    excluded = ("grader", "grading", "golden", "private", "secret", "credential")
    calls: Counter[str] = Counter()
    for path in root.rglob("*.py"):
        relative = path.relative_to(root)
        if (
            path.is_symlink()
            or any(term in str(relative).casefold() for term in excluded)
            or {"tasks", "task_definitions", "fixtures"} & set(relative.parts)
        ):
            continue
        with path.open("rb") as stream:
            source = stream.read(2_000_001)
        assert len(source) <= 2_000_000
        if b"to_openai_strict_schema" not in source:
            continue
        tree = ast.parse(source, filename=str(path))
        names = {"to_openai_strict_schema"}
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                names.update(
                    item.asname
                    for item in node.names
                    if item.name == "to_openai_strict_schema" and item.asname
                )
        module = ".".join((package, *relative.with_suffix("").parts))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if (isinstance(node.func, ast.Name) and node.func.id in names) or (
                isinstance(node.func, ast.Attribute)
                and node.func.attr == "to_openai_strict_schema"
            ):
                calls[module] += 1
    return calls


def test_all_strict_projection_boundaries_have_transport_checks():
    """A new production call site must be added to the transport gate."""
    packages = (
        "devbench",
        "devbench_engine",
        "devharness_server",
        "devharness_core",
        "devharness_controlled",
    )
    observed = sum(
        (_transport_projection_calls(package) for package in packages), Counter()
    )
    assert observed == {
        "devbench_engine.adapters": 1,  # Phase/final transport fixture below.
        "devbench.analysis.codex_signedin": 1,  # Named-schema and selector fixtures.
    }


def test_named_thesis_schemas_are_strict_at_every_projected_node():
    from devbench.analysis.method_authoring_contract import IMPLEMENTATION_SCHEMA
    from devbench.analysis.method_planner_contract import ADAPTIVE_CONTROLLER_SCHEMA
    from devbench_engine.adapters import to_openai_strict_schema

    for definition in (PHASE_SCHEMA, IMPLEMENTATION_SCHEMA, ADAPTIVE_CONTROLLER_SCHEMA):
        _assert_strict_transport(to_openai_strict_schema(definition))


def test_projection_rewrites_schema_nodes_and_preserves_property_names_and_values():
    from devbench_engine.adapters import to_openai_strict_schema

    schema = {
        "type": "object",
        "minProperties": 2,
        "maxProperties": 2,
        "propertyNames": {"minLength": 1},
        "properties": {
            "pattern": {
                "type": "array",
                "minItems": 1,
                "maxItems": 2,
                "uniqueItems": True,
                "items": {
                    "oneOf": [
                        {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 3,
                            "pattern": "^a",
                            "format": "email",
                            "default": "a",
                        },
                        {"$ref": "#/$defs/minimum"},
                    ]
                },
            },
            "const": {
                "type": "object",
                "const": {"pattern": "literal", "oneOf": [], "format": "literal"},
                "properties": {
                    "pattern": {"type": "string"},
                    "oneOf": {"type": "array", "items": {"type": "string"}},
                    "format": {"type": "string"},
                },
                "required": ["pattern", "oneOf", "format"],
                "additionalProperties": True,
            },
        },
        "required": ["pattern", "const"],
        "$defs": {
            "minimum": {
                "type": "integer",
                "minimum": 1,
                "maximum": 10,
                "exclusiveMinimum": 0,
                "exclusiveMaximum": 11,
                "multipleOf": 2,
            }
        },
    }
    original = json.dumps(schema)
    projected = to_openai_strict_schema(schema)

    assert projected["properties"]["pattern"]["items"] == {
        "anyOf": [{"type": "string"}, {"$ref": "#/$defs/minimum"}],
    }
    assert projected["$defs"] == {"minimum": {"type": "integer"}}
    value_schema = projected["properties"]["const"]
    assert value_schema["additionalProperties"] is False
    assert value_schema["const"] == {
        "pattern": "literal",
        "oneOf": [],
        "format": "literal",
    }
    assert value_schema["properties"]["format"] == {"type": "string"}
    assert projected["additionalProperties"] is False
    for node in _schema_nodes(projected):
        assert not _UNSUPPORTED.intersection(node)
    assert json.dumps(schema) == original


def test_coexisting_unions_keep_canonical_conjunction_for_local_validation():
    from devbench_engine.adapters import to_openai_strict_schema

    schema = {
        "type": "object",
        "required": ["value"],
        "additionalProperties": False,
        "properties": {
            "value": {
                "anyOf": [{"type": "integer"}, {"type": "string"}],
                "oneOf": [{"type": "integer", "multipleOf": 2}, {"type": "boolean"}],
            }
        },
    }
    projected = to_openai_strict_schema(schema)

    _assert_strict_transport(projected)
    assert projected["properties"]["value"] == {
        "anyOf": schema["properties"]["value"]["anyOf"],
    }
    assert Draft202012Validator(schema).is_valid({"value": 2})
    assert Draft202012Validator(projected).is_valid({"value": 2})
    assert not Draft202012Validator(schema).is_valid({"value": 1})
    assert Draft202012Validator(projected).is_valid({"value": 1})


def test_codex_keeps_an_explicit_schema_flag_without_a_projection(tmp_path):
    class CanonicalTransportFixture(CodexCLIAdapter):
        _uses_openai_schema_transport = False

    executable, work = _fake_cli(
        tmp_path,
        """
args = sys.argv[1:]
schema = Path(args[args.index("--output-schema") + 1])
(work / "transport.json").write_bytes(schema.read_bytes())
output = Path(args[args.index("--output-last-message") + 1])
output.write_text(json.dumps({"value": 8}))
""",
    )
    canonical = {
        "type": "object",
        "required": ["value"],
        "additionalProperties": False,
        "properties": {"value": {"type": "integer", "minimum": 8}},
    }
    (work / "answer_schema.json").write_text(json.dumps(canonical))
    adapter = CanonicalTransportFixture(executable=executable, condition="baseline")

    assert adapter.execute("Return the fixture response.", work) == {"value": 8}
    assert json.loads((work / "transport.json").read_text()) == canonical
    assert not list(work.glob(".devbench-cli-*"))


@pytest.mark.parametrize("invalid_phase", [False, True])
def test_codex_projects_each_phase_and_validates_original_contract(
    tmp_path, invalid_phase
):
    executable, work = _fake_cli(
        tmp_path,
        f"""
args = sys.argv[1:]
transport = Path(args[args.index("--output-schema") + 1])
output = Path(args[args.index("--output-last-message") + 1])
(work / (output.stem + "-transport.json")).write_bytes(transport.read_bytes())
answer = {{"value": 8}} if output.stem == "final_discovery" else {{
    "phase_summary": "x" * {12001 if invalid_phase else 1}, "next_steps": []}}
output.write_text(json.dumps(answer))
print(json.dumps({{"type": "thread.started", "thread_id": "fixture-session"}}))
""",
    )
    final_schema = {
        "type": "object",
        "properties": {
            "value": {
                "oneOf": [
                    {"type": "integer", "minimum": 8},
                    {"type": "integer", "maximum": 2},
                ]
            }
        },
        "required": ["value"],
        "additionalProperties": False,
    }
    canonical = work / "answer_schema.json"
    canonical.write_text(json.dumps(final_schema))
    adapter = CodexCLIAdapter(executable=executable, max_turns=4, condition="baseline")

    if invalid_phase:
        with pytest.raises(ValueError, match="does not match the output schema"):
            adapter.execute("Return the fixture response.", work)
        emitted = json.loads((work / "exploration-transport.json").read_text())
        response = {"phase_summary": "x" * 12001, "next_steps": []}
        assert Draft202012Validator(emitted).is_valid(response)
        assert not (work / "final_discovery.json").exists()
        assert not (work / "diagnostics-transport.json").exists()
        _assert_strict_transport(emitted)
    else:
        assert adapter.execute("Return the fixture response.", work) == {"value": 8}
        for phase in ("exploration", "diagnostics", "modeling", "final_discovery"):
            emitted = json.loads((work / (phase + "-transport.json")).read_text())
            _assert_strict_transport(emitted)
        final = json.loads((work / "final_discovery-transport.json").read_text())
        assert final["properties"]["value"] == {
            "anyOf": [{"type": "integer"}, {"type": "integer"}],
        }
    assert PHASE_SCHEMA["properties"]["phase_summary"]["maxLength"] == 12000
    assert json.loads(canonical.read_text()) == final_schema
    assert not list(work.glob(".devbench-cli-*"))


@pytest.mark.parametrize(
    ("purpose", "answer"),
    [
        (
            "method_implementation_draft",
            {
                "source_code": "pass\n",
                "algorithm_summary": "Synthetic draft",
                "introduced_choices": [],
                "source_references": [],
            },
        ),
        (
            "adaptive_controller",
            {
                "action": {
                    "kind": "stop",
                    "disposition": "partial",
                    "reason": "fixture",
                    "unresolved": [],
                    "resumption_conditions": [],
                    "rationale": "Synthetic stop",
                    "evidence_ids": [],
                }
            },
        ),
    ],
)
def test_strict_selector_launches_with_projection(
    workspace_tmp_path, monkeypatch, purpose, answer
):
    base = fixture_adapter(workspace_tmp_path, monkeypatch, answer=json.dumps(answer))
    canonical = base.workspace / "answer_schema.json"
    schema, _ = selection_contract({"decision_kind": purpose})
    original = json.dumps(schema)
    canonical.write_text(original)
    adapter = EpisodeCodexAdapter(
        boundary=base.boundary,
        requested_model="fixture-model",
        purpose=purpose,
        output=base.output,
        executable=base.executable,
        timeout_sec=5,
        max_mem_mb=256,
    )
    launches = []
    run_process = CodexCLIAdapter._run_process

    def capture(
        self,
        command,
        workspace,
        environment,
        instruction,
        timeout_sec,
        parse_events=True,
    ):
        if parse_events:
            path = Path(command[command.index("--output-schema") + 1])
            launches.append((command, path, json.loads(path.read_text())))
        return run_process(
            self,
            command,
            workspace,
            environment,
            instruction,
            timeout_sec,
            parse_events,
        )

    monkeypatch.setattr(CodexCLIAdapter, "_run_process", capture)

    selected = adapter.execute(
        "Return the fixture action.", base.workspace, allow_model_execution=True
    )
    assert selected == answer
    [(command, path, emitted)] = launches
    _assert_strict_transport(emitted)
    assert path != canonical and not path.exists()
    assert adapter.metadata["cli_command"] == command
    assert canonical.read_text() == original
    assert json.dumps(schema) == original


def test_claude_transport_keeps_canonical_bounds(tmp_path):
    executable, work = _fake_claude(
        tmp_path,
        """
args = sys.argv[1:]
(work / "transport.json").write_text(args[args.index("--json-schema") + 1])
print(json.dumps({
    "type": "result", "subtype": "success", "structured_output": {"label": "a"},
}))
""",
    )
    canonical = {
        "type": "object",
        "properties": {"label": {"type": "string", "maxLength": 2}},
        "required": ["label"],
        "additionalProperties": False,
    }
    (work / "answer_schema.json").write_text(json.dumps(canonical))
    adapter = ClaudeCLIAdapter(executable=executable, condition="baseline")

    assert adapter.execute("Return the fixture response.", work) == {"label": "a"}
    assert json.loads((work / "transport.json").read_text()) == canonical
