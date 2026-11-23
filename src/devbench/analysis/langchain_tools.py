"""Opt-in LangChain tool shapes for trusted, workspace-bound in-process calls.

These adapters retain ToolDispatcher validation and event capture. They do not
establish governed method authority, add retries, or replace MCP in CLI runs.
Scientific arguments/results never pass through LangChain callbacks here.
"""
from __future__ import annotations

from copy import deepcopy
import os
from typing import Sequence

from devharness_server.dispatcher import ToolDispatcher


def ensure_local_observability(config: dict | None = None) -> None:
    """Fail closed when external tracing or callbacks were inherited."""
    for variable in ("LANGSMITH_TRACING", "LANGCHAIN_TRACING_V2", "LANGCHAIN_TRACING"):
        value = os.environ.get(variable, "").strip().lower()
        if value not in ("", "false", "0", "off", "no"):
            raise PermissionError("External tracing is disabled for scientific workflow tools")
    if config is not None:
        if not isinstance(config, dict):
            raise TypeError("Runnable configuration must be a dictionary")
        if config.get("callbacks"):
            raise PermissionError("Scientific workflow callbacks are disabled")
    try:
        from langsmith import get_tracing_context
    except ImportError:
        return
    context = get_tracing_context()
    if context.get("enabled") or context.get("client") is not None or context.get("parent") is not None:
        raise PermissionError("Inherited tracing context is disabled for scientific workflow tools")


def build_dispatcher_tools(dispatcher: ToolDispatcher, *, allowed_tools: Sequence[str]) -> list:
    """Expose exactly the chosen registered tools, retaining dispatcher execution.

    Requires the optional ``workflow`` extra. The caller owns the trusted
    dispatcher and its assigned workspace; arbitrary numerical callables and an
    unrestricted dispatcher are never accepted. Returned tools are serial-only.
    """
    if not isinstance(dispatcher, ToolDispatcher):
        raise TypeError("A trusted ToolDispatcher is required")
    if dispatcher.allowed_root is None or not dispatcher.allowed_root.is_absolute():
        raise ValueError("ToolDispatcher must bind an absolute assigned workspace")
    if isinstance(allowed_tools, (str, bytes)) or not allowed_tools:
        raise ValueError("An explicit nonempty tool allowlist is required")
    names = list(allowed_tools)
    if any(not isinstance(name, str) for name in names) or len(set(names)) != len(names):
        raise ValueError("Tool allowlist must contain distinct registered names")
    ensure_local_observability()
    try:
        from langchain_core.tools import StructuredTool
    except ImportError as error:
        raise ImportError("Install the optional devharness[workflow] extra to use LangChain tools") from error
    schemas = {schema["name"]: schema for schema in dispatcher.get_schemas()}
    if any(name not in schemas for name in names):
        raise ValueError("Tool allowlist contains an unregistered tool")

    class DispatcherTool(StructuredTool):
        def invoke(self, input, config=None, **kwargs):
            ensure_local_observability(config)
            if kwargs:
                raise PermissionError("Extra invocation controls are disabled")
            if not isinstance(input, dict):
                raise TypeError("Scientific tool arguments must be a dictionary")
            return self.func(**input)

        async def ainvoke(self, input, config=None, **kwargs):
            # Deliberately serial on the calling thread. Heavy work remains
            # governed by existing supervised dispatcher workers.
            return self.invoke(input, config=config, **kwargs)

        def run(self, tool_input, callbacks=None, config=None, **kwargs):
            ensure_local_observability(config)
            if callbacks or self.callbacks:
                raise PermissionError("Scientific workflow callbacks are disabled")
            if kwargs:
                raise PermissionError("Extra run controls are disabled")
            return self.invoke(tool_input, config=config)

        async def arun(self, tool_input, callbacks=None, config=None, **kwargs):
            return self.run(tool_input, callbacks=callbacks, config=config, **kwargs)

        def with_fallbacks(self, *args, **kwargs):
            raise PermissionError("Automatic scientific tool fallbacks are disabled")

        def with_config(self, *args, **kwargs):
            # Generic Runnable bindings can regain fallback/retry composition.
            # Pass approved local config directly to invoke instead.
            raise PermissionError("Scientific tool configuration bindings are disabled")

        def with_types(self, *args, **kwargs):
            raise PermissionError("Scientific tool type bindings are disabled")

        def with_listeners(self, *args, **kwargs):
            raise PermissionError("Scientific workflow listeners are disabled")

        def with_alisteners(self, *args, **kwargs):
            raise PermissionError("Scientific workflow listeners are disabled")

        def bind(self, *args, **kwargs):
            raise PermissionError("Scientific tool argument bindings are disabled")

        def with_retry(self, *args, **kwargs):
            raise PermissionError("Automatic scientific tool retries are disabled")

        def batch(self, *args, **kwargs):
            raise PermissionError("Parallel/batched scientific tool dispatch is disabled")

        async def abatch(self, *args, **kwargs):
            raise PermissionError("Parallel/batched scientific tool dispatch is disabled")

        def batch_as_completed(self, *args, **kwargs):
            raise PermissionError("Parallel/batched scientific tool dispatch is disabled")

        def abatch_as_completed(self, *args, **kwargs):
            raise PermissionError("Parallel/batched scientific tool dispatch is disabled")

    bound_root = dispatcher.allowed_root

    def bind_execution(name):
        registered_function = dispatcher.tools[name]

        def execute(**arguments):
            ensure_local_observability()
            if dispatcher.allowed_root != bound_root:
                raise PermissionError("Dispatcher workspace binding changed")
            if dispatcher.tools.get(name) is not registered_function:
                raise PermissionError("Registered tool implementation changed")
            return dispatcher.execute(name, arguments)
        return execute

    tools = []
    for name in names:
        entry = schemas[name]
        schema = deepcopy(entry["inputSchema"])
        schema["additionalProperties"] = False

        tools.append(DispatcherTool(name=name, description=entry["description"],
                                    args_schema=schema, func=bind_execution(name),
                                    handle_tool_error=False, handle_validation_error=False))
    return tools
