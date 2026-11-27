"""MCP adapter. Research services run on the workspace daemon."""

from __future__ import annotations

from importlib import import_module
from time import monotonic

import anyio
import mcp.types as mt
from mcp.server.lowlevel import Server
from mcp.server.lowlevel.helper_types import ReadResourceContents

from devbench.engine.dispatch_deadline import check_deadline as check_deadline
from devbench.engine.dispatch_deadline import deadline as _DEADLINE
from devbench.engine.workflow_operations import OperationBlocked, OperationConflict
from devbench.platform.plugin_jobs import canonical
from devbench.platform.plugin_mcp_ui import CARD_NAMES, MIME, RESOURCE_META, card_html

from .schemas import CATEGORIES, INPUTS, PAYLOAD_BYTES, output_schema, validator

_MODULES = {
    "inspect_runtime_capabilities": "runtime",
    "inspect_inputs": "intake",
    "register_investigation_input": "intake",
    "check_answerability": "answerability",
    "list_kernels_and_methods": "registry",
    "list_investigation_state": "state",
    "query_artifact_table": "query",
    "get_run_status": "planning",
    "get_run_receipt": "receipts",
    "manage_research_aim": "aims",
    "freeze_analysis_plan": "planning",
    "submit_reviewed_run": "planning",
    "cancel_run": "planning",
}
_READ_ONLY = frozenset(
    {
        "inspect_runtime_capabilities",
        "inspect_inputs",
        "check_answerability",
        "list_kernels_and_methods",
        "list_investigation_state",
        "query_artifact_table",
        "get_run_status",
        "get_run_receipt",
    }
)
_CARDS = {
    "get_run_status": "run-card",
    "cancel_run": "run-card",
    "submit_reviewed_run": "run-card",
    "get_run_receipt": "receipt-card",
    "manage_research_aim": "receipt-card",
    "freeze_analysis_plan": "plan-card",
}
_REMEDIATION = {
    "awaiting_review": "Approve the exact frozen plan locally.",
    "private_data_local_studio_only": "Review locally in offline-only Studio.",
    "input_scope_violation": "Select an accessible registered input.",
    "resource_quota_exceeded": "Narrow the inspection or request a smaller projection.",
    "plan_identity_mismatch": "Retry with the retained digest.",
    "stale_revision_conflict": "Read the current revision and retry.",
    "scientific_refusal": "Review the design and retained evidence before proceeding.",
    "awaiting_information": "Supply the required declarations or verified evidence.",
    "runtime_prerequisite_missing": "Check installed runtime capabilities.",
    "invalid_request": "Use the declared tool fields and types.",
}
_DESCRIPTIONS = {
    "inspect_runtime_capabilities": (
        "Probe local engines, installed images, packages and worker capacity."
    ),
    "inspect_inputs": (
        "Inspect verified public or synthetic input structure and count distributions."
    ),
    "register_investigation_input": (
        "Admit a digest-bound input; omit session_id to start an investigation."
    ),
    "check_answerability": (
        "Check biological units, design rank and evidence for the requested contrast."
    ),
    "list_kernels_and_methods": (
        "Discover available kernels, scoped methods and their input contracts."
    ),
    "list_investigation_state": (
        "Read the current investigation revision, branches, artifacts and holds."
    ),
    "query_artifact_table": (
        "Project a bounded page or aggregate from a verified table or plot sidecar."
    ),
    "get_run_status": (
        "Read queue state, progress and checkpoint availability for one run."
    ),
    "get_run_receipt": (
        "Verify the terminal receipt, evidence limits and figure-to- sidecar bindings."
    ),
    "manage_research_aim": (
        "Read or revise an aim, branch, review, literature binding or research package."
    ),
    "freeze_analysis_plan": (
        "Freeze code or a curated kernel with exact inputs, purpose and provenance."
    ),
    "submit_reviewed_run": (
        "Enqueue the exact approved plan or resume its verified checkpoint."
    ),
    "cancel_run": "Cancel a run and report the daemon's verified cleanup state.",
}


def _packet(data, error=False):
    return mt.CallToolResult(
        isError=error,
        structuredContent=data,
        content=[mt.TextContent(type="text", text=canonical(data).decode())],
    )


def _error(ctx, category, args=None, diagnostics=None):
    data = {
        "error": "OperationBlocked",
        "reason": category,
        "category": category,
        "remediation": _REMEDIATION[category],
    }
    sid = (args or {}).get("session_id")
    if category == "private_data_local_studio_only":
        import re

        safe_sid = (
            sid if isinstance(sid, str) and re.fullmatch("[0-9a-f]{32}", sid) else ""
        )
        data.update(ctx.handoff(safe_sid, private=True))
    if category == "awaiting_review" and args and sid:
        import re
        import shlex

        if all(
            isinstance(args.get(k), str) and re.fullmatch(p, args[k])
            for k, p in (
                ("session_id", "[a-f0-9]{32}"),
                ("plan_id", "[a-f0-9]{32}"),
                ("plan_sha256", "[a-f0-9]{64}"),
            )
        ):
            data["launch_hint"] = shlex.join(
                [
                    "devbench",
                    "approve",
                    "--workspace",
                    "<workspace>",
                    "--session-id",
                    sid,
                    "--plan-id",
                    args["plan_id"],
                    "--plan-sha256",
                    args["plan_sha256"],
                ]
            )
    if diagnostics:
        data["validation_errors"] = diagnostics
    return _packet(data, True)


def _invalid(error):
    # The schema path belongs to the server. The instance path and message can
    # carry arbitrary private keys and values even on an additional-property error.
    path = list(error.absolute_schema_path)
    fields = [path[i + 1] for i, part in enumerate(path[:-1]) if part == "properties"]
    return {"loc": ".".join(fields) or "$", "type": str(error.validator)}


def _category(error):
    if isinstance(error, OperationBlocked):
        reason = error.state
        if reason == "ledger_busy":
            return "resource_quota_exceeded"
        if reason in CATEGORIES["enum"]:
            return reason
        if reason in {
            "explicit_plan_approval_required",
            "outcome_blind_critic_required",
        }:
            return "awaiting_review"
        if reason in {"unknown_run", "adaptive_protected_partition_delivery_denied"}:
            return "input_scope_violation"
        return "scientific_refusal"
    if isinstance(error, OperationConflict):
        return "stale_revision_conflict"
    if isinstance(error, PermissionError):
        return "input_scope_violation"
    if isinstance(error, (ImportError, FileNotFoundError)):
        return "runtime_prerequisite_missing"
    return "invalid_request"


def _fit_result(name, data):
    def within_budget():
        return (
            len(
                _packet(data).model_dump_json(by_alias=True, exclude_none=True).encode()
            )
            <= PAYLOAD_BYTES - 128
        )

    if name == "query_artifact_table":
        while data["rows"] and not within_budget():
            removed = max(1, len(data["rows"]) // 4)
            del data["rows"][-removed:]
            data["returned_rows"] = len(data["rows"])
            data["truncated"] = True
            if data["rows"]:
                # The next offset advances only by rows actually returned.
                data["next_offset"] = data["_offset"] + len(data["rows"])
        if (
            not data["rows"]
            and data["total_matching_rows"] > data["_offset"]
            and data["truncated"]
        ):
            raise OperationBlocked("resource_quota_exceeded")
        del data["_offset"]
    if not within_budget():
        raise OperationBlocked("resource_quota_exceeded")
    if not validator(name, output=True).is_valid(data):
        raise ValueError("Invalid output contract")
    return _packet(data)


def dispatch(ctx, name, arguments, meta=None):
    if name not in INPUTS or not isinstance(arguments, dict):
        return _error(ctx, "invalid_request")
    try:
        import json

        if (
            len(
                json.dumps(
                    arguments,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    allow_nan=False,
                ).encode()
            )
            > 18 * 1024 * 1024
        ):
            raise OperationBlocked("resource_quota_exceeded")
        invalid = next(validator(name).iter_errors(arguments), None)
        if invalid is not None:
            return _error(ctx, "invalid_request", diagnostics=[_invalid(invalid)])
        if (
            name == "submit_reviewed_run"
            and meta
            and "devbench/nativeConfirmation" in meta
        ):
            from .approval import accept_native_confirmation

            ctx.require_public(arguments["session_id"])
            accept_native_confirmation(
                ctx, arguments, meta["devbench/nativeConfirmation"]
            )
        module = import_module("devbench.mcp." + _MODULES[name])
        result = getattr(module, name)(ctx, arguments)
        if name == "query_artifact_table":
            result["_offset"] = arguments.get("offset", 0)
        return _fit_result(name, result)
    except Exception as exc:
        return _error(ctx, _category(exc), arguments)


def _ui_supported(server):
    params = server.request_context.session.client_params
    capabilities = params.capabilities.model_dump(by_alias=True) if params else {}
    return "io.modelcontextprotocol/ui" in (capabilities.get("extensions") or {})


def build_server(ctx):
    server = Server("devharness-rna")

    @server.list_tools()
    async def list_tools():
        ui = _ui_supported(server)
        return [
            mt.Tool(
                name=name,
                description=_DESCRIPTIONS[name],
                inputSchema=schema,
                outputSchema=output_schema(name),
                annotations=mt.ToolAnnotations(
                    readOnlyHint=name in _READ_ONLY,
                    destructiveHint=False,
                    idempotentHint=name
                    not in {"manage_research_aim", "freeze_analysis_plan"},
                    openWorldHint=False,
                ),
                _meta={"ui": {"resourceUri": "ui://devharness/" + _CARDS[name]}}
                if ui and name in _CARDS
                else None,
            )
            for name, schema in INPUTS.items()
        ]

    @server.list_resources()
    async def list_resources():
        if not _ui_supported(server):
            return []
        return [
            mt.Resource(
                uri="ui://devharness/" + card,
                name=title,
                mimeType=MIME,
                _meta=RESOURCE_META,
            )
            for card, title in CARD_NAMES.items()
        ]

    @server.read_resource()
    async def read_resource(uri):
        card = str(uri).removeprefix("ui://devharness/")
        if not _ui_supported(server) or card not in CARD_NAMES:
            raise ValueError("Unknown research card")
        return [
            ReadResourceContents(
                content=card_html(card), mime_type=MIME, meta=RESOURCE_META
            )
        ]

    async def call_tool(request):
        args = request.params.arguments or {}
        name = request.params.name
        meta = request.params.meta
        deadline = _DEADLINE.set(monotonic() + 4.65)
        try:
            with anyio.fail_after(4.85):
                reply = await anyio.to_thread.run_sync(
                    dispatch,
                    ctx,
                    name,
                    args,
                    meta.model_dump(by_alias=True) if meta else None,
                    abandon_on_cancel=name in _READ_ONLY,
                )
        except TimeoutError:
            reply = _error(ctx, "resource_quota_exceeded")
        finally:
            _DEADLINE.reset(deadline)
        return mt.ServerResult(reply)

    # SDK's default validation embeds the rejected JSON instance in error text.
    server.request_handlers[mt.CallToolRequest] = call_tool
    return server
