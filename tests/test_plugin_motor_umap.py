"""Real local Plugin and offline Studio contract for a shared motor UMAP."""

import asyncio
import hashlib
import http.client
import json
import os
import sys
import time
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import anndata
import numpy as np
import pandas as pd
import psutil
import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from scipy import sparse

pytestmark = pytest.mark.wal


def _stage_file(path, stage, seed):
    random = np.random.default_rng(seed)
    counts = sparse.csr_matrix(random.poisson(2, size=(42, 56)))
    labels = ["Neuroblast: Motor neuron"] * (42 - seed * 5)
    labels += ["Neuron: Motor neuron"] * (seed * 5)
    obs = pd.DataFrame(
        {"age": [stage] * 42, "celltypes": labels},
        index=[f"cell-{i}" for i in range(42)],
    )
    var = pd.DataFrame(
        {"highly_variable": np.ones(56, dtype=bool)},
        index=[f"Gene{i}" for i in range(56)],
    )
    cells = anndata.AnnData(counts, obs=obs, var=var, layers={"counts": counts})
    cells.write_h5ad(path)


def test_motor_umap_roundtrip(workspace_tmp_path):
    from devbench.platform.plugin_jobs import JobStore

    inputs = workspace_tmp_path / "registered-inputs"
    inputs.mkdir()
    workspace = workspace_tmp_path / "studio"
    marker = workspace / "studio-server.json"
    browser_calls = workspace_tmp_path / "browser.log"
    loose = workspace_tmp_path / "external"
    loose.mkdir()
    paths = [loose / f"stage-{stage}.h5ad" for stage in ("e09", "e10", "e11")]
    for i, path in enumerate(paths):
        _stage_file(path, f"e{i + 9}", i)
    selected = [{"path": str(path)} for path in paths]
    entrypoint = (
        "from pathlib import Path; "
        "from devbench.platform import workbench_ui, plugin_mcp; "
        f"out=Path({str(browser_calls)!r}); "
        "workbench_ui._open_browser_async=lambda url: "
        "out.write_text((out.read_text() if out.exists() else '') + url + '\\n'); "
        "raise SystemExit(plugin_mcp.main())"
    )
    parameters = StdioServerParameters(
        command=sys.executable,
        args=[
            "-I",
            "-B",
            "-c",
            entrypoint,
            "--input-root",
            str(inputs),
            "--workspace",
            str(workspace),
            "--allow-external-input-files",
        ],
        cwd=workspace_tmp_path,
        env={
            key: value
            for key, value in os.environ.items()
            if key in {"PATH", "DYLD_LIBRARY_PATH", "LD_LIBRARY_PATH"}
        },
    )

    async def request():
        async with stdio_client(parameters) as (reader, writer):
            async with ClientSession(reader, writer) as client:
                await client.initialize()
                assert not marker.exists()
                old = await client.call_tool(
                    "plan_rna_workflow",
                    {
                        "operation": "matrix_audit",
                        "objective": "Prior private intake",
                        "inputs": selected,
                    },
                )
                assert old.structuredContent["status"] == "local_studio_only"
                state = json.loads(marker.read_text())
                assert state["workspace"] == str(workspace)
                assert state["offline_only"] is True
                assert browser_calls.read_text().splitlines() == [
                    old.structuredContent["studio_url"]
                ]
                result = await client.call_tool(
                    "plan_rna_workflow",
                    {
                        "operation": "shared_motor_umap",
                        "objective": "Compare motor states",
                        "inputs": selected,
                    },
                )
                assert not result.isError, result.structuredContent
                response = result.structuredContent
                assert response["status"] == "local_studio_only"
                assert all(str(path) not in json.dumps(response) for path in paths)
                denied = await client.call_tool(
                    "execute_reviewed_plan",
                    {
                        key: response[key]
                        for key in ("session_id", "plan_id", "plan_sha256")
                    },
                )
                assert (
                    denied.structuredContent["reason"]
                    == "private_data_local_studio_only"
                )
                assert json.loads(marker.read_text()) == state
                assert browser_calls.read_text().splitlines() == [
                    old.structuredContent["studio_url"]
                ]
                return response

    try:
        response = asyncio.run(request())
        frozen = JobStore(workspace).plan(response["session_id"], response["plan_id"])
        assert frozen["operation"] == "shared_motor_umap"
        assert [p["source"] for p in frozen["profiles"]] == list(map(str, paths))
        assert frozen["data_classification"] == "private"
        assert frozen["design"]["parameters"] == {
            "stage_column": "age",
            "label_column": "celltypes",
            "counts_layer": "counts",
        }
        _run_through_studio(response)
    finally:
        if marker.is_file():
            state = json.loads(marker.read_text())
            assert state["workspace"] == str(workspace)
            try:
                process = psutil.Process(state["pid"])
                assert Path(process.cwd()) == workspace
                process.terminate()
                process.wait(timeout=5)
            except psutil.NoSuchProcess:
                pass


def _run_through_studio(response):
    url = urlsplit(response["studio_url"])
    assert url.scheme == "http" and url.hostname == "127.0.0.1" and url.port
    assert parse_qs(url.query) == {
        "session_id": [response["session_id"]],
        "view": ["workflows"],
    }
    origin = f"{url.scheme}://{url.netloc}"
    connection = http.client.HTTPConnection(url.hostname, url.port, timeout=6)
    api = "/api/scientific/adaptive/" + response["session_id"]

    def get_view(*, allow_revision_change=False):
        connection.request("GET", api)
        reply = connection.getresponse()
        payload = json.loads(reply.read())
        if (
            allow_revision_change
            and reply.status == 409
            and payload.get("message") == "adaptive_view_revision_changed"
        ):
            return None
        assert reply.status == 200, payload
        return payload["shared_motor_umap"]

    def post(allow, sha):
        connection.request(
            "POST",
            api + "/motor-umap",
            json.dumps(
                {
                    "plan_id": response["plan_id"],
                    "plan_sha256": sha,
                    "allow_data_execution": allow,
                }
            ),
            headers={"Origin": origin, "Content-Type": "application/json"},
        )
        reply = connection.getresponse()
        return reply.status, json.loads(reply.read())

    try:
        assert get_view()["status"] == "awaiting_review"
        assert post(False, response["plan_sha256"])[0] == 403
        assert post(True, "0" * 64)[0] != 202
        status, _ = post(True, response["plan_sha256"])
        assert status == 202
        deadline = time.monotonic() + 150
        view = None
        while time.monotonic() < deadline:
            current = get_view(allow_revision_change=True)
            if current is not None:
                view = current
                if view["status"] not in {"queued", "running"}:
                    break
            time.sleep(0.25)
        assert view is not None and view["status"] == "completed", view
        assert view["cells"] == 126
        assert [stage["neuron"] for stage in view["stages"]] == [0, 5, 10]
        for kind in ("umap", "markers", "coordinates", "composition", "parameters"):
            artifact = view["artifacts"][kind]
            connection.request("GET", artifact["url"])
            reply = connection.getresponse()
            data = reply.read()
            assert reply.status == 200
            assert hashlib.sha256(data).hexdigest() == artifact["sha256"]
        status, rerun = post(True, response["plan_sha256"])
        assert status == 202
        assert rerun["run_id"] == view["run_id"]
    finally:
        connection.close()
