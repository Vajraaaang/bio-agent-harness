"""Exercise Companion Studio startup over the configured MCP transport."""

import asyncio
import http.client
import json
import os
import sys
from urllib.parse import urlsplit

import psutil
import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

pytestmark = pytest.mark.wal


@pytest.mark.parametrize("automatic", [True, False])
def test_first_tool_starts_and_opens_offline_studio(workspace_tmp_path, automatic):
    inputs = workspace_tmp_path / "inputs"
    inputs.mkdir(mode=0o700)
    workspace = workspace_tmp_path / "workspace"
    browser_calls = workspace_tmp_path / "browser.log"
    marker = workspace / "studio-server.json"
    matrix = inputs / "counts.csv"
    matrix.write_text("gene,s1,s2\nA,1,4\nB,2,5\n")
    entrypoint = (
        "from pathlib import Path; "
        "from devbench.platform import workbench_ui, plugin_mcp; "
        f"out=Path({str(browser_calls)!r}); "
        "workbench_ui._open_browser_async=lambda url: "
        "out.write_text((out.read_text() if out.exists() else '') + url + '\\n'); "
        "raise SystemExit(plugin_mcp.main())"
    )
    params = StdioServerParameters(
        command=sys.executable,
        args=[
            "-c",
            entrypoint,
            "--input-root",
            str(inputs),
            "--workspace",
            str(workspace),
        ]
        + ([] if automatic else ["--no-auto-studio"]),
        env={
            k: os.environ[k]
            for k in ("PYTHONPATH", "DYLD_LIBRARY_PATH", "PATH")
            if k in os.environ
        },
    )
    request = {
        "operation": "matrix_audit",
        "objective": "Inspect this synthetic matrix",
        "measurement": "non_umi_counts",
        "inputs": [{"path": str(matrix), "data_classification": "synthetic"}],
    }

    async def roundtrip():
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as client:
                await client.initialize()
                assert (await client.list_tools()).tools
                assert not marker.exists()
                plan = await client.call_tool("plan_rna_workflow", request)
                assert not plan.isError, plan.structuredContent
                detail = plan.structuredContent
                if not automatic:
                    assert detail["studio_url"] is None
                    assert not marker.exists() and not browser_calls.exists()
                    return
                assert detail["studio_url"], detail
                state = json.loads(marker.read_text())
                assert state["offline_only"] is True
                assert browser_calls.read_text().splitlines() == [detail["studio_url"]]
                url = urlsplit(detail["studio_url"])
                assert url.hostname == "127.0.0.1"
                connection = http.client.HTTPConnection(
                    url.hostname, url.port, timeout=3
                )
                try:
                    connection.request("GET", "/api/config")
                    response = connection.getresponse()
                    assert response.status == 200
                    assert json.loads(response.read())["offline_only"] is True
                finally:
                    connection.close()
                followup = await client.call_tool(
                    "inspect_rna_inputs", {"inputs": request["inputs"]}
                )
                assert not followup.isError
                assert json.loads(marker.read_text())["pid"] == state["pid"]
                assert browser_calls.read_text().splitlines() == [detail["studio_url"]]
                frozen = {
                    key: detail[key] for key in ("session_id", "plan_id", "plan_sha256")
                }
                run = await client.call_tool("execute_reviewed_plan", frozen)
                assert not run.isError, run.structuredContent
                run_key = {
                    key: run.structuredContent[key] for key in ("session_id", "run_id")
                }
                for _ in range(60):
                    status = await client.call_tool("get_run_status", run_key)
                    assert not status.isError, status.structuredContent
                    if status.structuredContent["status"] not in {"queued", "running"}:
                        break
                    await asyncio.sleep(0.1)
                assert status.structuredContent["status"] == "completed", (
                    status.structuredContent
                )
                assert (
                    f"session_id={detail['session_id']}&view=overview"
                    in status.structuredContent["studio_url"]
                )
                receipt = await client.call_tool("get_execution_receipt", run_key)
                assert not receipt.isError, receipt.structuredContent
                assert receipt.structuredContent["run_id"] == run_key["run_id"]
                assert json.loads(marker.read_text())["pid"] == state["pid"]
                assert browser_calls.read_text().splitlines() == [detail["studio_url"]]

    try:
        asyncio.run(roundtrip())
    finally:
        if marker.is_file():
            pid = json.loads(marker.read_text())["pid"]
            process = psutil.Process(pid)
            process.terminate()
            process.wait(timeout=5)
