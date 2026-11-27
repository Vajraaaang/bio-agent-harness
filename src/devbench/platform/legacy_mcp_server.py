"""Compatibility protocol for previously installed Workbench clients."""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from contextlib import suppress
from pathlib import Path
from typing import Any

import anyio
from mcp.server import Server
from mcp.server.lowlevel.helper_types import ReadResourceContents
from mcp.server.stdio import stdio_server
from mcp.types import CallToolResult, Resource, TextContent, Tool, ToolAnnotations
from pydantic import BaseModel, ValidationError

from devbench.engine.workflow_operations import OperationBlocked, OperationConflict

from . import plugin_workflows as workflows
from .plugin_jobs import MAX_DOCUMENT_BYTES, canonical
from .plugin_mcp_output import PRIVATE_LAUNCH_HINT, output_schema, output_validator
from .plugin_workflows import (
    _PRIVATE,
    Answerability,
    Execute,
    Inspect,
    Plan,
    Plugin,
    ResearchAim,
    Run,
)

_TOOLS: dict[str, tuple[type[BaseModel], str, str, bool, str | None]] = {
    "inspect_rna_inputs": (
        Inspect,
        "inspect",
        "Inspect local RNA input headers and return aggregate metadata.",
        True,
        None,
    ),
    "check_answerability": (
        Answerability,
        "answer",
        "Evaluate the RNA question registry and supplied prerequisite evidence.",
        True,
        None,
    ),
    "plan_rna_workflow": (
        Plan,
        "plan",
        "Freeze inputs and the objective in a scientific session. Starts no run.",
        False,
        "plan-card",
    ),
    "execute_reviewed_plan": (
        Execute,
        "execute",
        "Start the exact locally reviewed plan by session ID, plan ID and SHA-256.",
        False,
        "run-card",
    ),
    "get_run_status": (
        Run,
        "status",
        "Read the local background run, task progress and Studio link.",
        True,
        "run-card",
    ),
    "get_execution_receipt": (
        Run,
        "receipt",
        "Read a verified non-private run receipt. The broker records each disclosure.",
        True,
        "receipt-card",
    ),
    "manage_research_aim": (
        ResearchAim,
        "aims",
        "Work with the bound investigation's research aims and evidence.",
        False,
        "receipt-card",
    ),
}


def _schema_locations(value: Any) -> set[str]:
    if isinstance(value, dict):
        fields = set(value.get("properties", {}))
        mapping = value.get("discriminator", {}).get("mapping", {})
        return fields.union(mapping, *(_schema_locations(v) for v in value.values()))
    if isinstance(value, list):
        return set().union(*(_schema_locations(v) for v in value))
    return set()


def _validation_diagnostics(
    model: type[BaseModel], error: ValidationError
) -> list[dict[str, str]]:
    allowed = _schema_locations(model.model_json_schema())
    dynamic_keys = {"fields", "sources"}
    diagnostics = []
    for detail in error.errors(
        include_url=False, include_context=False, include_input=False
    )[:16]:
        location = detail["loc"]
        kind = detail["type"]
        parts = [
            str(part)
            if (
                isinstance(part, int)
                or (
                    part in allowed
                    and not (i > 0 and location[i - 1] in dynamic_keys)
                    and not (kind == "extra_forbidden" and i == len(location) - 1)
                )
            )
            else "<field>"
            for i, part in enumerate(location)
        ]
        diagnostics.append({"loc": ".".join(parts), "type": kind})
    return diagnostics


def _tool_error(
    plugin: Plugin, name: str, arguments: dict[str, Any], error: Exception
) -> CallToolResult:
    if isinstance(error, OperationBlocked):
        reason = error.state
    elif isinstance(error, (ValueError, KeyError)):
        reason = "invalid_request"
    elif isinstance(
        error, (OSError, OperationConflict, RuntimeError, TypeError, csv.Error)
    ):
        reason = "local_workflow_unavailable"
    else:
        reason = "internal_tool_error"
    result = {"error": "OperationBlocked", "reason": reason}
    if isinstance(error, ValidationError):
        result["validation_errors"] = _validation_diagnostics(_TOOLS[name][0], error)
    sid = arguments.get("session_id") if isinstance(arguments, dict) else None
    if (
        reason == _PRIVATE
        and isinstance(sid, str)
        and re.fullmatch(r"[0-9a-f]{32}", sid)
    ):
        result["studio_url"] = None
        with suppress(OSError, ValueError, RuntimeError, KeyError):
            result["studio_url"] = plugin._handoff(sid, "overview")["studio_url"]
        if result["studio_url"] is None:
            # Configured folders may contain private identifiers. The full local
            # command is also available in the caller's original plan handoff.
            result["launch_hint"] = PRIVATE_LAUNCH_HINT
    if name in _TOOLS and not output_validator(name).is_valid(result):
        result = {"error": "OperationBlocked", "reason": "internal_tool_error"}
    return CallToolResult(
        isError=True,
        content=[TextContent(type="text", text=canonical(result).decode())],
        structuredContent=result,
    )


def _open_studio_handoff(
    workspace: Path, result: dict[str, Any], previous: str | None
) -> str | None:
    from .plugin_jobs import _studio_origin
    from .workbench_ui import _open_browser_async

    link = result.get("studio_url")
    if not isinstance(link, str):
        return previous
    origin = _studio_origin(workspace, offline_only=True)
    if origin is None or previous == origin or not link.startswith(origin + "?"):
        return previous
    try:
        _open_browser_async(link)
    except (OSError, RuntimeError):
        return previous
    return origin


def _checked_tool_success(name: str, result: dict[str, Any]) -> CallToolResult:
    # Validate the actual JSON that crosses the wire. The SDK can include the
    # offending instance in error text if handed an invalid tuple response.
    try:
        payload = canonical(result).decode()
        structured = json.loads(payload)
        if (
            not isinstance(structured, dict)
            or "error" in structured
            or not output_validator(name).is_valid(structured)
        ):
            raise ValueError("Invalid tool result")
        return CallToolResult(
            content=[TextContent(type="text", text=payload)],
            structuredContent=structured,
        )
    except Exception:
        raise OperationBlocked("internal_tool_error") from None


def build_server(
    plugin: Plugin,
    *,
    auto_studio: bool = True,
    open_browser: bool = False,
    plan_model=Plan,
) -> Server:
    from .plugin_mcp_ui import CARD_NAMES, MIME, RESOURCE_META, card_html

    server: Server = Server("devharness-rna", version="0.2.0")
    tools = dict(_TOOLS)
    tools["plan_rna_workflow"] = (plan_model, *_TOOLS["plan_rna_workflow"][1:])
    opened_studio = None

    @server.list_tools()
    async def list_tools():
        return [
            Tool(
                name=name,
                description=description,
                inputSchema=model.model_json_schema(),
                outputSchema=output_schema(name),
                annotations=ToolAnnotations(
                    readOnlyHint=readonly,
                    destructiveHint=False,
                    openWorldHint=name == "execute_reviewed_plan"
                    or (
                        name == "manage_research_aim"
                        and plugin.literature_root is not None
                    ),
                    idempotentHint=readonly or name == "execute_reviewed_plan",
                ),
                _meta={"ui": {"resourceUri": f"ui://devharness/{card}"}}
                if card
                else None,
            )
            for name, (model, _, description, readonly, card) in tools.items()
        ]

    @server.list_resources()
    async def list_resources():
        return [
            Resource(
                uri=f"ui://devharness/{card}",
                name=title,
                mimeType=MIME,
                _meta=RESOURCE_META,
            )
            for card, title in CARD_NAMES.items()
        ]

    @server.read_resource()
    async def read_resource(uri):
        address = str(uri)
        card = address.removeprefix("ui://devharness/")
        if address == card or card not in CARD_NAMES:
            raise ValueError("Unknown RNA card")
        return [
            ReadResourceContents(
                content=card_html(card), mime_type=MIME, meta=RESOURCE_META
            )
        ]

    @server.call_tool(validate_input=False)
    async def call_tool(
        name: str,
        arguments: dict[str, Any],
    ) -> CallToolResult:
        nonlocal opened_studio
        result: dict[str, Any]
        try:
            model, handler, _, readonly, _ = tools[name]
            if len(canonical(arguments)) > MAX_DOCUMENT_BYTES:
                raise ValueError("Request exceeds the byte budget")
            args = model.model_validate_json(canonical(arguments), strict=True)
            if auto_studio and not readonly:
                # A failed optional startup still leaves a manual Studio handoff.
                with suppress(OSError, ValueError):
                    await anyio.to_thread.run_sync(
                        workflows.ensure_offline_studio,
                        plugin.jobs.workspace,
                        plugin.input_root,
                        plugin.mounted_image_id,
                    )
            result = await anyio.to_thread.run_sync(getattr(plugin, handler), args)
            response = _checked_tool_success(name, result)
        except Exception as error:
            # The SDK otherwise echoes unexpected exception text over STDIO.
            return _tool_error(plugin, name, arguments, error)
        if open_browser:
            opened_studio = _open_studio_handoff(
                plugin.jobs.workspace, result, opened_studio
            )
        return response

    return server


def main(argv: list[str] | None = None, *, plan_model=Plan) -> int:
    parser = argparse.ArgumentParser(prog="devbench plugin-mcp", description=__doc__)
    parser.add_argument("--input-root", required=True, type=Path)
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument(
        "--allow-external-input-files",
        action="store_true",
        help="Allow explicitly named files outside the configured input root.",
    )
    parser.add_argument("--mounted-image-id")
    parser.add_argument("--allow-literature-network", action="store_true")
    parser.add_argument("--literature-plugin-root", type=Path)
    parser.add_argument(
        "--no-auto-studio",
        action="store_true",
        help="Leave Companion Studio startup to the operator.",
    )
    browser = parser.add_mutually_exclusive_group()
    browser.add_argument(
        "--open-browser",
        action="store_true",
        help="Open an offline Studio handoff in the browser.",
    )
    browser.add_argument(
        "--no-auto-browser",
        action="store_false",
        dest="open_browser",
        help="Keep Studio links passive (default).",
    )
    parser.set_defaults(open_browser=False)
    args = parser.parse_args(argv)
    if args.allow_literature_network != (args.literature_plugin_root is not None):
        parser.error(
            "Literature requires explicit network permission and a pinned client root"
        )
    try:
        plugin = Plugin(
            args.workspace,
            args.input_root,
            args.mounted_image_id,
            args.literature_plugin_root,
            allow_external_files=args.allow_external_input_files,
        )
        server = build_server(
            plugin,
            auto_studio=not args.no_auto_studio,
            open_browser=args.open_browser,
            plan_model=plan_model,
        )

        async def serve():
            async with stdio_server() as (reader, writer):
                await server.run(reader, writer, server.create_initialization_options())

        anyio.run(serve)
    except (OSError, ValueError, RuntimeError) as error:
        print(f"devbench plugin-mcp: {type(error).__name__}: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
