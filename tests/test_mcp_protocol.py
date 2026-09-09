"""Real local stdio contract checks; no model participant or clinical inputs."""
import asyncio
import json
import sys
from datetime import timedelta

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from devharness_server.dispatcher import ToolDispatcher


async def _with_session(workspace, check):
    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-m", "devharness_server.server"],
        cwd=str(workspace),
        env={key: "1" for key in (
            "OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
            "VECLIB_MAXIMUM_THREADS", "NUMEXPR_NUM_THREADS",
        )},
    )
    async with stdio_client(parameters) as (reader, writer):
        async with ClientSession(reader, writer, read_timeout_seconds=timedelta(seconds=30)) as session:
            await session.initialize()
            await check(session)


def _run(workspace, check):
    asyncio.run(asyncio.wait_for(_with_session(workspace, check), timeout=45))


def test_stdio_declares_strict_inputs_and_structured_outputs(tmp_path):
    async def check(session):
        actual = {tool.name: tool for tool in (await session.list_tools()).tools}
        expected = {tool["name"]: tool for tool in ToolDispatcher().get_schemas()}
        assert set(actual) == set(expected) and len(actual) == 10
        for name, tool in actual.items():
            assert tool.inputSchema.get("additionalProperties") is False, name
            assert tool.inputSchema["required"] == expected[name]["inputSchema"]["required"]
            assert tool.outputSchema and tool.outputSchema["type"] == "object", name
            for parameter, schema in expected[name]["inputSchema"]["properties"].items():
                assert tool.inputSchema["properties"][parameter].get("default") == schema.get("default")
    _run(tmp_path, check)


@pytest.mark.parametrize("values,direction", [
    ({"E8.5": 1, "E10.5": 2}, "increasing"),
    ({"E8.5": 2, "E10.5": 1}, "decreasing"),
])
def test_stdio_returns_same_json_text_and_structured_result(tmp_path, values, direction):
    async def check(session):
        result = await session.call_tool("calculate_temporal_monotonicity", {"stage_values": values})
        assert not result.isError
        assert result.structuredContent["direction"] == direction
        assert len(result.content) == 1
        assert json.loads(result.content[0].text) == result.structuredContent
        json.dumps(result.structuredContent, allow_nan=False)
    _run(tmp_path, check)


@pytest.mark.parametrize("arguments", [
    {},
    {"stage_values": {}},
    {"stage_values": {"E8.5": 1}},
    {"stage_values": {"E8.5": 1, "E10.5": 2}, "ignored_option": True},
    {"stage_values": '{"E8.5": 1, "E10.5": 2}'},
    {"stage_values": {"E8.5": "1", "E10.5": "2"}},
])
def test_stdio_rejects_invalid_json_arguments_without_coercion(tmp_path, arguments):
    async def check(session):
        result = await session.call_tool("calculate_temporal_monotonicity", arguments)
        assert result.isError
        assert result.structuredContent is None
        followup = await session.call_tool("calculate_temporal_monotonicity", {
            "stage_values": {"E8.5": 1, "E10.5": 2},
        })
        assert not followup.isError and followup.structuredContent["direction"] == "increasing"
    _run(tmp_path, check)


def test_stdio_preserves_workspace_refusal_and_dispatcher_errors(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    external = tmp_path / "external.csv"
    external.write_text("sample_id,embryonic_age\ns1,E8.5\ns2,E10.5\n")
    link = workspace / "external-link.csv"
    link.symlink_to(external)

    async def check(session):
        for metadata in (str(external), str(link), "relative.csv"):
            result = await session.call_tool("validate_design_matrix", {
                "metadata_csv_path": metadata, "formula_str": "1 + numeric_time",
            })
            assert result.isError and result.structuredContent is None
            assert "outside the assigned workspace" in result.content[0].text
        missing = await session.call_tool("validate_design_matrix", {
            "metadata_csv_path": str(workspace / "missing.csv"), "formula_str": "1 + numeric_time",
        })
        assert missing.isError and missing.structuredContent is None
        unknown = await session.call_tool("nonexistent_tool", {})
        assert unknown.isError and unknown.structuredContent is None
        local = workspace / "metadata.csv"
        local.write_text("sample_id,embryonic_age\ns1,E8.5\ns2,E10.5\ns3,E12.5\n")
        valid = await session.call_tool("validate_design_matrix", {
            "metadata_csv_path": str(local), "formula_str": "1 + numeric_time",
        })
        assert not valid.isError and valid.structuredContent["is_full_rank"]
        assert json.loads(valid.content[0].text) == valid.structuredContent
    _run(workspace, check)


def test_raw_dispatcher_failures_and_success_remain_captured():
    from mcp.shared.memory import create_connected_server_and_client_session
    from devharness_server.dispatcher import capture_tool_events
    from devharness_server.server import create_server

    async def check():
        bad = {"stage_values": {"E8.5": "1", "E10.5": "2"}}
        with capture_tool_events() as events:
            async with create_connected_server_and_client_session(create_server()) as client:
                result = await client.call_tool("calculate_temporal_monotonicity", bad)
                assert result.isError and result.structuredContent is None
                result = await client.call_tool("calculate_temporal_monotonicity", {
                    "stage_values": {"E8.5": 1, "E10.5": 2},
                })
                assert not result.isError
        assert len(events) == 2
        assert events[0]["arguments"] == bad
        assert events[0]["succeeded"] is False
        assert events[1]["succeeded"] is True
    asyncio.run(asyncio.wait_for(check(), timeout=30))
