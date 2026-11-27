"""Layer 1. Exercise the public protocol with an extension-free SDK client."""

import asyncio
import json
import sys
from contextlib import asynccontextmanager

import httpx
import pytest
from jsonschema import Draft202012Validator
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client

from devbench.daemon.auth import read_mcp_token
from devbench.mcp import schemas
from tests.test_mcp_13_tool_control_plane import NAMES
from tests.test_research_daemon import _admission, _audit_plan, _daemon, _http

pytestmark = pytest.mark.wal


@asynccontextmanager
async def minimal_client(workspace, inputs):
    parameters = StdioServerParameters(
        command=sys.executable,
        args=[
            "-I",
            "-B",
            "-m",
            "devbench.platform.cli",
            "mcp",
            "--workspace",
            str(workspace),
            "--input-root",
            str(inputs),
        ],
    )
    async with (
        stdio_client(parameters) as (read, write),
        ClientSession(read, write) as client,
    ):
        init = await client.initialize()
        assert init.capabilities.prompts is None
        assert init.capabilities.tasks is None
        assert not (await client.list_resources()).resources
        yield client


# Each override changes the type of a declared field in an otherwise successful
# request. A validation error must precede any lookup, approval or mutation.
WRONG_TYPE = {
    "inspect_runtime_capabilities": {"include_images": "false"},
    "inspect_inputs": {"inspect_layers": "true"},
    "register_investigation_input": {"expected_revision": "0"},
    "check_answerability": {"question_text": 123},
    "list_kernels_and_methods": {"include_parameter_schema": "false"},
    "list_investigation_state": {"artifact_limit": "1"},
    "query_artifact_table": {"limit": "1"},
    "get_run_status": {"run_id": 123},
    "get_run_receipt": {"include_figure_preview": "false"},
    "manage_research_aim": {"expected_revision": "0"},
    "freeze_analysis_plan": {"resource_override": {"gpu": "false"}},
    "submit_reviewed_run": {"resume_from_run_id": 123},
    "cancel_run": {"reason": 123},
}


class ContractClient:
    def __init__(self, client, tools):
        self.client = client
        self.validators = {t.name: Draft202012Validator(t.outputSchema) for t in tools}
        self.successes = set()

    async def call(self, name, args, *, error=False):
        async with asyncio.timeout(5):
            reply = await self.client.call_tool(name, args)
        assert reply.isError is error, (name, reply.structuredContent)
        payload = reply.structuredContent
        self.validators[name].validate(payload)
        assert json.loads(reply.content[0].text) == payload
        assert (
            len(reply.model_dump_json(by_alias=True, exclude_none=True).encode())
            <= 65536
        )
        if not error and name not in self.successes:
            self.successes.add(name)
            invalid = await self.call(name, {**args, **WRONG_TYPE[name]}, error=True)
            assert (
                invalid["category"] == "invalid_request"
                and invalid["validation_errors"]
            )
        return payload


async def _contracts(client):
    tools = (await client.list_tools()).tools
    assert tuple(tool.name for tool in tools) == NAMES
    wire = ContractClient(client, tools)
    read_only = set(NAMES) - {
        "register_investigation_input",
        "manage_research_aim",
        "freeze_analysis_plan",
        "submit_reviewed_run",
        "cancel_run",
    }
    for tool in tools:
        assert tool.inputSchema == schemas.INPUTS[tool.name]
        assert tool.outputSchema == schemas.output_schema(tool.name)
        Draft202012Validator.check_schema(tool.inputSchema)
        Draft202012Validator.check_schema(tool.outputSchema)
        assert tool.inputSchema["additionalProperties"] is False
        assert tool.meta is None
        hints = tool.annotations
        assert hints.readOnlyHint is (tool.name in read_only)
        assert hints.openWorldHint is hints.destructiveHint is False
        assert hints.idempotentHint is (
            tool.name not in {"freeze_analysis_plan", "manage_research_aim"}
        )
        err = await wire.call(tool.name, {"unexpected": True}, error=True)
        assert err["category"] == "invalid_request" and err["validation_errors"]
    return wire


async def _exercise(client, path):
    wire = await _contracts(client)
    runtime = await wire.call(
        "inspect_runtime_capabilities",
        {"include_images": False, "include_packages": False},
    )
    assert runtime["sqlite_wal_patched"]
    reg = _admission(path)
    entry = {k: v for k, v in reg["input"].items() if k != "investigation_role"}
    assert (await wire.call("inspect_inputs", {"inputs": [entry]}))["inputs"]
    admitted = await wire.call("register_investigation_input", reg)
    session = {"session_id": admitted["session_id"]}
    assert (await wire.call("list_investigation_state", session))["artifacts"]
    for bad_integer in ("1", True, 1.0):
        err = await wire.call(
            "list_investigation_state",
            {**session, "artifact_limit": bad_integer},
            error=True,
        )
        assert err["category"] == "invalid_request"
    await wire.call("manage_research_aim", {**session, "action": "list"})
    await wire.call(
        "check_answerability",
        {
            **session,
            "question_text": "What measurements are available?",
            "assay": "bulk_rna_seq",
            "entry_point": "count_matrix",
        },
    )
    await wire.call("list_kernels_and_methods", {"kernel_ids": ["matrix_audit"]})
    metadata = path.with_name("samples.csv")
    metadata.write_text(
        "unit,group\n" + "".join(f"unit-{i},{'x' * 600}\n" for i in range(200))
    )
    meta = _admission(metadata)
    meta["input"]["file_kind"] = "metadata"
    meta["representation_class"] = "metadata_only"
    meta.update(**session, expected_revision=admitted["revision"])
    ref = (await wire.call("register_investigation_input", meta))["artifact"]
    page = await wire.call(
        "query_artifact_table",
        {
            **session,
            "artifact_id": ref["artifact_id"],
            "artifact_sha256": ref["sha256"],
            "limit": 1,
        },
    )
    assert page["returned_rows"] == 1 and page["next_offset"] == 1
    large = await wire.call(
        "query_artifact_table",
        {
            **session,
            "artifact_id": ref["artifact_id"],
            "artifact_sha256": ref["sha256"],
            "limit": 200,
            "columns": ["unit", "group"],
        },
    )
    assert 0 < large["returned_rows"] < 200
    assert large["truncated"] and large["next_offset"] == large["returned_rows"]
    plan = await wire.call("freeze_analysis_plan", _audit_plan(admitted))
    identity = {k: plan[k] for k in ("session_id", "plan_id", "plan_sha256")}
    submitted = await wire.call("submit_reviewed_run", identity)
    key = {**session, "run_id": submitted["run_id"]}
    assert (await wire.call("submit_reviewed_run", identity))["run_id"] == key["run_id"]
    async with asyncio.timeout(60):
        while (status := await wire.call("get_run_status", key))["status"] in {
            "queued",
            "running",
        }:
            await asyncio.sleep(0.1)
    assert status["status"] == "completed"
    receipt = await wire.call("get_run_receipt", key)
    assert receipt["clinical_release_allowed"] is False
    cancel_plan = await wire.call(
        "freeze_analysis_plan",
        {**_audit_plan(admitted), "objective": "Verify cancellation"},
    )
    identity = {k: cancel_plan[k] for k in identity}
    active = await wire.call("submit_reviewed_run", identity)
    cancellation = {**session, "run_id": active["run_id"], "reason": "Fixture complete"}
    assert (await wire.call("cancel_run", cancellation))["status"] == "cancelled"
    async with asyncio.timeout(30):
        while (await wire.call("cancel_run", cancellation))[
            "cleanup_state"
        ] != "confirmed":
            await asyncio.sleep(0.1)
    final = await wire.call("cancel_run", cancellation)
    assert final["status"] == "already_terminal"
    held = await wire.call(
        "freeze_analysis_plan",
        {
            **_audit_plan(admitted),
            "epistemic_purpose": "confirmatory",
        },
    )
    identity = {k: held[k] for k in identity}
    assert held["approval_required"] != "none"
    blocked = await wire.call("submit_reviewed_run", identity, error=True)
    assert blocked["category"] == "awaiting_review"
    assert "devbench approve" in blocked["launch_hint"]
    assert held["plan_sha256"] in blocked["launch_hint"]
    assert wire.successes == set(NAMES)


def test_extension_free_sdk_client(workspace_tmp_path):
    with _daemon(workspace_tmp_path) as (workspace, inputs, _):
        counts = inputs / "counts.csv"
        counts.write_text("gene,A,B\nred,1,2\nblue,3,4\ngreen,2,7\n")

        async def run():
            async with minimal_client(workspace, inputs) as client:
                await _exercise(client, counts)

        asyncio.run(run())


def test_streamable_http_requires_opt_in_bearer_and_origin(workspace_tmp_path):
    with _daemon(workspace_tmp_path, enable_streamable_http=True) as (
        workspace,
        _,
        meta,
    ):
        origin = meta["http_url"].rstrip("/")
        bearer = {"Authorization": f"Bearer {read_mcp_token(workspace)}"}
        assert _http(origin, "/mcp", method="POST")[0] == 401
        assert (
            _http(
                origin,
                "/mcp",
                method="POST",
                headers={**bearer, "Origin": "https://other.invalid"},
            )[0]
            == 403
        )

        async def run():
            async with (
                httpx.AsyncClient(
                    headers={**bearer, "Origin": origin}, trust_env=False
                ) as http,
                streamable_http_client(origin + "/mcp", http_client=http) as (
                    read,
                    write,
                    _,
                ),
                ClientSession(read, write) as client,
            ):
                await client.initialize()
                assert tuple(t.name for t in (await client.list_tools()).tools) == NAMES

        asyncio.run(run())
