"""Real dispatcher behavior through optional LangChain tools."""
import json
import pytest

pytest.importorskip("langchain_core", reason="Optional workflow extra is not installed")
from jsonschema.exceptions import ValidationError
from devharness_server.dispatcher import ToolDispatcher, capture_tool_events


def adapter(dispatcher, names):
    try:
        from devbench.analysis.langchain_tools import build_dispatcher_tools
    except ImportError as error:
        pytest.fail(f"Optional adapter not implemented: {error}")
    return build_dispatcher_tools(dispatcher, allowed_tools=names)


@pytest.fixture
def dispatcher(tmp_path):
    dispatcher = ToolDispatcher(allowed_root=tmp_path)
    def fixture_read(input_path: str, count: int = 1):
        "Read fixture metadata under trusted dispatcher validation."
        return {"count": count, "path": input_path}
    dispatcher.register(fixture_read)
    return dispatcher


def test_explicit_selection_exact_schema_and_actual_capture(dispatcher, tmp_path):
    from langchain_core.tools import StructuredTool
    tool, = adapter(dispatcher, ["fixture_read"])
    assert isinstance(tool, StructuredTool)
    schema = next(s["inputSchema"] for s in dispatcher.get_schemas() if s["name"] == "fixture_read")
    schema["additionalProperties"] = False
    assert tool.args_schema == schema
    with capture_tool_events() as events:
        value = tool.invoke({"input_path": str(tmp_path / "counts.tsv"), "count": 2})
    assert json.loads(value)["count"] == 2
    assert len(events) == 1 and events[0]["succeeded"] is True


@pytest.mark.parametrize("arguments", [{"count": "2"}, {"count": 2, "extra": 3}])
def test_schema_errors_keep_dispatcher_evidence(dispatcher, tmp_path, arguments):
    tool, = adapter(dispatcher, ["fixture_read"])
    with capture_tool_events() as events, pytest.raises(ValidationError):
        tool.invoke({"input_path": str(tmp_path / "counts.tsv"), **arguments})
    assert len(events) == 1 and events[0]["succeeded"] is False


def test_path_escape_is_still_rejected(dispatcher, tmp_path):
    tool, = adapter(dispatcher, ["fixture_read"])
    with capture_tool_events() as events, pytest.raises(PermissionError):
        tool.invoke({"input_path": str(tmp_path / ".." / "outside.tsv")})
    assert len(events) == 1 and events[0]["succeeded"] is False


@pytest.mark.parametrize("names", [[], ["missing"], ["fixture_read", "fixture_read"]])
def test_explicit_allowlist_is_required(dispatcher, names):
    with pytest.raises((ValueError, TypeError)):
        adapter(dispatcher, names)


def test_unbounded_dispatcher_rejected():
    with pytest.raises(ValueError):
        adapter(ToolDispatcher(), ["validate_design_matrix"])


@pytest.mark.parametrize("variable", ["LANGSMITH_TRACING", "LANGCHAIN_TRACING_V2", "LANGCHAIN_TRACING"])
def test_export_environment_rejected_at_creation(dispatcher, monkeypatch, variable):
    monkeypatch.setenv(variable, "true")
    with pytest.raises(PermissionError, match="tracing"):
        adapter(dispatcher, ["fixture_read"])


def test_export_changes_rejected_at_call(dispatcher, tmp_path, monkeypatch):
    tool, = adapter(dispatcher, ["fixture_read"])
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    with capture_tool_events() as events, pytest.raises(PermissionError):
        tool.invoke({"input_path": str(tmp_path / "counts.tsv")})
    assert events == []


def test_callback_rejected_before_it_can_receive_data(dispatcher, tmp_path):
    from langchain_core.callbacks import BaseCallbackHandler
    calls = []
    class Spy(BaseCallbackHandler):
        def on_tool_start(self, *args, **kwargs):
            calls.append(args)
    tool, = adapter(dispatcher, ["fixture_read"])
    with pytest.raises(PermissionError):
        tool.invoke({"input_path": str(tmp_path / "counts.tsv")}, config={"callbacks": [Spy()]})
    assert calls == []


def test_run_callback_rejected(dispatcher, tmp_path):
    tool, = adapter(dispatcher, ["fixture_read"])
    with pytest.raises(PermissionError):
        tool.run({"input_path": str(tmp_path / "counts.tsv")}, callbacks=[object()])


def test_inherited_tracing_context_rejected(dispatcher):
    from langsmith import tracing_context
    with tracing_context(enabled=True):
        with pytest.raises(PermissionError):
            adapter(dispatcher, ["fixture_read"])


def test_batch_does_not_schedule_parallel_execution(dispatcher, tmp_path):
    tool, = adapter(dispatcher, ["fixture_read"])
    with pytest.raises(PermissionError):
        tool.batch([{"input_path": str(tmp_path / "counts.tsv")}])


def test_tool_function_error_is_not_suppressed(dispatcher):
    def fixture_fail():
        raise RuntimeError("scientific worker failed")
    dispatcher.register(fixture_fail)
    tool, = adapter(dispatcher, ["fixture_fail"])
    with capture_tool_events() as events, pytest.raises(RuntimeError, match="scientific worker failed"):
        tool.invoke({})
    assert len(events) == 1 and not events[0]["succeeded"]


def test_arguments_cannot_change_allowlisted_dispatch_target(dispatcher, tmp_path):
    calls = []
    def forbidden(input_path: str):
        calls.append(input_path)
        return {}
    dispatcher.register(forbidden)
    tool, = adapter(dispatcher, ["fixture_read"])
    with pytest.raises(ValidationError):
        tool.invoke({"input_path": str(tmp_path / "counts.tsv"), "_name": "forbidden"})
    assert calls == []


def test_async_is_single_dispatch(dispatcher, tmp_path):
    import asyncio
    tool, = adapter(dispatcher, ["fixture_read"])
    with capture_tool_events() as events:
        result = asyncio.run(tool.ainvoke({"input_path": str(tmp_path / "counts.tsv")}))
    assert json.loads(result)["count"] == 1
    assert len(events) == 1


def test_missing_optional_dependency_has_actionable_error(dispatcher, monkeypatch):
    import builtins
    from devbench.analysis.langchain_tools import build_dispatcher_tools
    original_import = builtins.__import__
    def without_optional(name, *args, **kwargs):
        if name.startswith("langchain_core"):
            raise ImportError("simulated optional absence")
        return original_import(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", without_optional)
    with pytest.raises(ImportError, match=r"devharness\[workflow\]"):
        build_dispatcher_tools(dispatcher, allowed_tools=["fixture_read"])


def test_automatic_retry_wrapper_rejected(dispatcher):
    tool, = adapter(dispatcher, ["fixture_read"])
    with pytest.raises(PermissionError):
        tool.with_retry()


def test_workspace_binding_cannot_be_removed_after_creation(dispatcher, tmp_path):
    tool, = adapter(dispatcher, ["fixture_read"])
    dispatcher.allowed_root = None
    with capture_tool_events() as events, pytest.raises(PermissionError):
        tool.invoke({"input_path": str(tmp_path / "counts.tsv")})
    assert events == []


def test_registered_implementation_cannot_change_after_creation(dispatcher, tmp_path):
    tool, = adapter(dispatcher, ["fixture_read"])
    calls = []
    def replacement(**kwargs):
        calls.append(kwargs)
        return {}
    dispatcher.tools["fixture_read"] = replacement
    with pytest.raises(PermissionError):
        tool.invoke({"input_path": str(tmp_path / "counts.tsv")})
    assert calls == []


@pytest.mark.parametrize("route", ["direct", "bound_config", "bound_arguments"])
def test_fallback_routes_cannot_repeat_failed_scientific_work(dispatcher, route):
    calls = []
    def failing_science():
        calls.append("attempt")
        raise RuntimeError("scientific failure")
    dispatcher.register(failing_science)
    tool, = adapter(dispatcher, ["failing_science"])
    with capture_tool_events() as events, pytest.raises(PermissionError):
        selected = tool
        if route == "bound_config":
            selected = tool.with_config({"tags": ["local"]})
        elif route == "bound_arguments":
            selected = tool.bind()
        selected.with_fallbacks([tool]).invoke({})
    assert calls == [] and events == []


def test_bound_configuration_cannot_reenable_retry(dispatcher):
    tool, = adapter(dispatcher, ["fixture_read"])
    with pytest.raises(PermissionError):
        tool.with_config({}).with_retry()


@pytest.mark.parametrize("method", ["with_types", "with_listeners", "with_alisteners"])
def test_generic_binding_and_listener_convenience_is_disabled(dispatcher, method):
    tool, = adapter(dispatcher, ["fixture_read"])
    with pytest.raises(PermissionError):
        getattr(tool, method)()
