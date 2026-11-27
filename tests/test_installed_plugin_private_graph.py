"""Installed DevHarness acceptance through the offline Studio execution graph."""

import asyncio
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit

import anndata
import numpy as np
import pandas as pd
import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from scipy.sparse import csr_matrix

from devbench.analysis.workbench_motor_programs import MARKERS
from devbench.platform.adaptive_workflow import AdaptiveWorkflow
from devbench.platform.plugin_mcp import _ledger
from tests.test_scientific_workspace_http import authenticated_studio, call

pytestmark = pytest.mark.wal
_CONFIGURE = (
    Path(__file__).resolve().parents[1]
    / "integrations/codex/devharness/scripts/configure.py"
)


def _install_configured_plugin(root, inputs, workspace):
    codex = shutil.which("codex")
    if codex is None:
        pytest.skip("Installing the local fixture requires the Codex CLI")
    marketplace = root / "marketplace"
    exported = marketplace / "devharness"
    registry = root / "test-registry"
    registry.mkdir(mode=0o700)

    def run(command, env=None):
        process = subprocess.run(
            command, cwd=root, env=env, capture_output=True, text=True, timeout=90
        )
        assert process.returncode == 0, process.stderr[-2000:]
        return json.loads(process.stdout)

    run(
        [
            sys.executable,
            str(_CONFIGURE),
            "--python",
            sys.executable,
            "--input-root",
            str(inputs),
            "--workspace",
            str(workspace),
            "--output",
            str(exported),
            "--write-marketplace",
            str(marketplace),
        ]
    )
    env = {
        key: os.environ[key] for key in ("PATH", "LANG", "LC_ALL") if key in os.environ
    }
    env["CODEX_HOME"] = str(registry)
    source = run(
        [codex, "plugin", "marketplace", "add", str(marketplace), "--json"], env
    )
    assert source["marketplaceName"] == "devharness-local"
    run([codex, "plugin", "add", "devharness@devharness-local", "--json"], env)
    (manifest,) = (registry / "plugins" / "cache").rglob(".mcp.json")
    assert manifest.read_bytes() == (exported / ".mcp.json").read_bytes()
    return json.loads(manifest.read_text())["mcpServers"]["devharness"], manifest


@contextmanager
def _offline_studio(settings, inputs, workspace):
    from devbench.daemon import ipc

    prior = ipc.daemon_status(workspace)
    marker = workspace / "studio-server.json"
    env = {**os.environ, **settings.get("env", {})}
    with (workspace.parent / "studio.log").open("w+") as log:
        process = subprocess.Popen(
            [
                settings["command"],
                "-I",
                "-B",
                "-m",
                "devbench.platform.cli",
                "workbench",
                "--input-root",
                str(inputs),
                "--workspace",
                str(workspace),
                "--enable-scientific",
                "--allow-data-execution",
                "--offline-only",
                "--port",
                "0",
            ],
            cwd=workspace,
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        try:
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline and process.poll() in {None, 0}:
                if marker.is_file():
                    state = json.loads(marker.read_text())
                    assert state["workspace"] == str(workspace)
                    assert state["offline_only"] is True
                    url = urlsplit(state["url"])
                    assert (
                        url.scheme == "http"
                        and url.hostname == "127.0.0.1"
                        and url.port
                    )
                    assert call(url.port, "/api/config")[0] == 401
                    with authenticated_studio(url.port, workspace):
                        assert (
                            call(
                                url.port,
                                "/api/config",
                                origin="https://example.invalid",
                            )[0]
                            == 403
                        )
                        yield url.port
                    return
                time.sleep(0.1)
            log.seek(0)
            pytest.fail(f"Offline Studio did not start: {log.read(3000)}")
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=10)
            if (
                prior["status"] == "stopped"
                and ipc.daemon_status(workspace)["status"] == "running"
            ):
                ipc.stop_daemon(workspace)


def _fixture_days(inputs):
    genes = [*MARKERS, *(f"SyntheticGene{index}" for index in range(48))]
    paths = []
    for index, day in enumerate(("e9", "e10", "e11")):
        counts = np.random.default_rng(72 + index).poisson(2, size=(42, len(genes)))
        counts[:, : len(MARKERS)] = 0
        counts[:, [genes.index(gene) for gene in ("Isl1", "Tubb3")]] = 2
        counts[:20, [genes.index(gene) for gene in ("Phox2b", "Tbx20")]] = 3
        counts[20:, [genes.index(gene) for gene in ("Mnx1", "Lhx3")]] = 3
        matrix = csr_matrix(counts)
        labels = ["Neuroblast: Motor neuron"] * (42 - 5 * index) + [
            "Neuron: Motor neuron"
        ] * (5 * index)
        obs = pd.DataFrame(
            {"age": [day] * 42, "celltypes": labels, "predicted_labels": labels},
            index=[f"{day}-cell-{row}" for row in range(42)],
        )
        var = pd.DataFrame(
            {
                "gene_ids": [f"ENSMUSG{gene:011}" for gene in range(len(genes))],
                "highly_variable": [True] * len(genes),
            },
            index=genes,
        )
        path = inputs / f"day-{day}.h5ad"
        anndata.AnnData(matrix, obs=obs, var=var, layers={"counts": matrix}).write_h5ad(
            path
        )
        paths.append(path)
    return paths


def _matched_outcome(workspace, session_id, view):
    proof = view["graph_execution"]
    flow = AdaptiveWorkflow(_ledger(workspace), session_id)
    outcome = flow._read_projection(
        lambda db: flow._state(db).outcome(proof["step_id"])
    )
    assert outcome["status"] == "succeeded"
    assert outcome["attempt_id"] == proof["attempt_id"]
    assert outcome["accepted_record_id"] == proof["accepted_record_id"]
    assert outcome["receipt_sha256"] == proof["receipt_sha256"]
    assert outcome["outputs"]["result"]["sha256"] == proof["result_sha256"]
    file = workspace / "plugin-graph" / proof["attempt_id"] / "result.json"
    raw = file.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == proof["result_sha256"]
    return outcome, json.loads(raw)


async def _projection(port, api):
    # A graph commit between the Studio revision reads can invalidate readback.
    for _ in range(20):
        status, raw, _ = call(port, api)
        if status != 409:
            assert status == 200, raw[:900]
            return json.loads(raw)
        await asyncio.sleep(0.15)
    pytest.fail(f"Studio readback kept returning conflicts: {raw[:900]}")


def _approve(port, api, plan, **overrides):
    body = {
        "plan_id": plan["plan_id"],
        "plan_sha256": plan["plan_sha256"],
        "allow_data_execution": True,
        **overrides,
    }
    status, raw, _ = call(
        port,
        api + "/plugin-execute",
        body=body,
        origin=f"http://127.0.0.1:{port}",
    )
    return status, json.loads(raw)


async def _complete(port, api, plan):
    status, result = _approve(port, api, plan)
    assert status == 202, result
    while True:
        item = next(
            item
            for item in (await _projection(port, api))["private_workflow"]["plans"]
            if item["plan_id"] == plan["plan_id"]
        )
        if item["status"] not in {"queued", "running"}:
            assert item["status"] == "completed", (
                item["operation"],
                item["status"],
                item.get("error"),
            )
            assert item["run_id"] == result["run_id"]
            return item
        await asyncio.sleep(0.2)


async def _check_studio_figures(port, api, consumer):
    figures = (await _projection(port, api))["shared_motor_umap"]
    assert figures["cells"] == 126
    assert [row["neuron"] for row in figures["stages"]] == [0, 5, 10]
    assert set(figures["artifacts"]) == {
        "umap",
        "markers",
        "coordinates",
        "composition",
        "parameters",
    }
    for name, artifact in figures["artifacts"].items():
        status, raw, _ = call(port, artifact["url"])
        assert status == 200, (name, raw[:400])
        assert hashlib.sha256(raw).hexdigest() == consumer["outputs"][name]["sha256"]
    coordinates = consumer["outputs"]["coordinates"]["sha256"]
    status, page, _ = call(port, f"{api}/motor-umap/{coordinates}/plotly?theme=dark")
    assert status == 200, page[:400]
    assert b'<html lang="en" data-theme="dark">' in page
    composition = figures["artifacts"]["composition"]["url"]
    assert call(port, composition, origin="https://example.invalid")[0] == 403


def test_installed_plugin_composes_two_private_studio_graph_operations(
    workspace_tmp_path,
):
    inputs, workspace = (workspace_tmp_path / name for name in ("inputs", "workspace"))
    inputs.mkdir(mode=0o700)
    workspace.mkdir(mode=0o700)
    paths = _fixture_days(inputs)
    source_sha = [hashlib.sha256(path.read_bytes()).hexdigest() for path in paths]
    declarations = [
        {"path": str(path), "data_classification": "private"} for path in paths
    ]
    settings, _ = _install_configured_plugin(workspace_tmp_path, inputs, workspace)
    # Invoke the installed optional recipe with the workspace configuration
    # from the installed plugin. The research control plane has no motor ops.
    args = list(settings["args"])
    entry = args.index("devbench.platform.plugin_mcp")
    args[entry] = "examples.motor_programs.server"
    parameters = StdioServerParameters(
        **{**settings, "args": [*args, "--no-auto-studio"]}
    )

    async def exercise(port):
        origin = f"http://127.0.0.1:{port}"
        async with asyncio.timeout(220), stdio_client(parameters) as (reader, writer):
            async with ClientSession(reader, writer) as client:
                await client.initialize()
                upstream = await client.call_tool(
                    "plan_rna_workflow",
                    {
                        "operation": "motor_program_comparison",
                        "objective": "Screen synthetic motor marker profiles by day",
                        "inputs": declarations,
                    },
                )
                assert not upstream.isError, upstream.structuredContent
                first = upstream.structuredContent
                sid = first["session_id"]
                identity = {key: first[key] for key in ("plan_id", "plan_sha256")}
                downstream = await client.call_tool(
                    "plan_rna_workflow",
                    {
                        "operation": "shared_motor_umap",
                        "objective": "Plot synthetic motor states in shared space",
                        "inputs": declarations,
                        "session_id": sid,
                        "depends_on": [identity],
                    },
                )
                assert not downstream.isError, downstream.structuredContent
                second = downstream.structuredContent
                assert second["session_id"] == sid
                for plan in (first, second):
                    assert plan["status"] == "local_studio_only"
                    assert (
                        plan["studio_url"]
                        == f"{origin}/?session_id={sid}&view=workflows"
                    )
                    assert all(str(path) not in json.dumps(plan) for path in paths)
                denied = await client.call_tool(
                    "execute_reviewed_plan", {"session_id": sid, **identity}
                )
                assert (
                    denied.structuredContent["reason"]
                    == "private_data_local_studio_only"
                )

                api = f"/api/scientific/adaptive/{sid}"

                before = (await _projection(port, api))["private_workflow"]["plans"]
                assert [(p["status"], p["can_run"]) for p in before] == [
                    ("awaiting_review", True),
                    ("blocked", False),
                ], [(p["operation"], p.get("error")) for p in before]
                assert before[1]["error"] == "plugin_dependency_not_completed"
                assert _approve(port, api, first, allow_data_execution=False)[0] == 403
                assert _approve(port, api, first, plan_sha256="0" * 64)[0] != 202
                assert _approve(port, api, second)[0] != 202
                assert (await _projection(port, api))["private_workflow"][
                    "plans"
                ] == before

                first_done = await _complete(port, api, first)
                producer, material = _matched_outcome(workspace, sid, first_done)
                assert first_done["metrics"] == {
                    "sources": 3,
                    "observations_screened": 126,
                }
                assert first_done["summary_tables"] == material["summary_tables"]
                artifact = material["result_artifact"]
                raw = (workspace / artifact["path"]).read_bytes()
                assert len(raw) == artifact["size_bytes"]
                assert (
                    hashlib.sha256(raw).hexdigest()
                    == producer["outputs"]["analysis"]["sha256"]
                )
                assert json.loads(raw)["clinical_release_allowed"] is False

                second_done = await _complete(port, api, second)
                consumer, _ = _matched_outcome(workspace, sid, second_done)
                pinned = [
                    p for p in consumer["consumed"] if p["port"] == "prerequisite_0"
                ]
                assert [p["sha256"] for p in pinned] == [
                    producer["outputs"]["result"]["sha256"]
                ]
                await _check_studio_figures(port, api, consumer)
                for plan, original in ((first, first_done), (second, second_done)):
                    status, resumed = _approve(port, api, plan)
                    assert status == 202 and resumed["run_id"] == original["run_id"]
                    assert resumed["graph_execution"] == original["graph_execution"]
                    private = await client.call_tool(
                        "get_execution_receipt",
                        {"session_id": sid, "run_id": original["run_id"]},
                    )
                    assert (
                        private.structuredContent["reason"]
                        == "private_data_local_studio_only"
                    )
                workflow = AdaptiveWorkflow(_ledger(workspace), sid)
                dispatches = workflow._read_projection(
                    lambda db: set(workflow._state(db).dispatches)
                )
                assert dispatches == {producer["attempt_id"], consumer["attempt_id"]}

    with _offline_studio(settings, inputs, workspace) as port:
        asyncio.run(exercise(port))
    assert [
        hashlib.sha256(path.read_bytes()).hexdigest() for path in paths
    ] == source_sha
