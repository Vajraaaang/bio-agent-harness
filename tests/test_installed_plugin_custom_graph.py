"""Accepted native output reused by a registered method over the installed plugin."""

import asyncio
import json
import os

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from devbench.engine.methods import MethodService
from devbench.platform.adaptive_workflow import AdaptiveWorkflow
from devbench.platform.method_planner import ScientificMethodPlanner
from devbench.platform.plugin_mcp import _ledger
from devbench.platform.plugin_registered_method import OPERATION
from tests.test_exploratory_execution_profile import register
from tests.test_installed_plugin_private_graph import (
    _install_configured_plugin,
    _matched_outcome,
    _offline_studio,
    _projection,
)
from tests.test_installed_plugin_public_graph import _approved_plan, _complete, _rpc

pytestmark = [
    pytest.mark.wal,
    pytest.mark.skipif(
        os.environ.get("DEVBENCH_T15_DOCKER") != "1",
        reason="explicit local Docker acceptance opt-in",
    ),
]


def test_installed_plugin_runs_registered_oci_and_projects_the_same_graph(
    workspace_tmp_path,
):
    inputs = workspace_tmp_path / "inputs"
    workspace = workspace_tmp_path / "workspace"
    inputs.mkdir(mode=0o700)
    workspace.mkdir(mode=0o700)
    source = inputs / "expression.csv"
    source.write_text("gene_id,s1,s2,s3,s4\ngene_a,1,2,3,4\ngene_b,3,1,4,2\n")
    settings, _ = _install_configured_plugin(workspace_tmp_path, inputs, workspace)
    parameters = StdioServerParameters(
        **{**settings, "args": [*settings["args"], "--no-auto-studio"]}
    )

    async def exercise(port):
        async with (
            asyncio.timeout(180),
            stdio_client(parameters) as (reader, writer),
            ClientSession(reader, writer) as client,
        ):
            await client.initialize()
            baseline = await _rpc(
                client,
                "plan_rna_workflow",
                {
                    "operation": "matrix_audit",
                    "inputs": [
                        {"path": str(source), "data_classification": "synthetic"}
                    ],
                    "measurement": "raw_counts",
                    "objective": "Audit synthetic counts",
                },
            )
            initial, _ = await _complete(client, baseline)
            sid = initial["session_id"]
            producer, _ = _matched_outcome(workspace, sid, initial)
            artifact = producer["outputs"]["result"]
            ledger = _ledger(workspace)
            methods = MethodService(workspace / "methods", reconcile=False)
            executor = ScientificMethodPlanner(methods).exploratory_executor(
                sid, ledger, ()
            )
            assert not (executor.training.root / artifact["sha256"]).exists()
            method = register(
                methods,
                workspace_tmp_path / "registered",
                "installed_registered_plugin",
                ("audit",),
                ("out",),
                """
import json
from pathlib import Path
value = json.loads(Path("/inputs/audit").read_text())
metrics = value["metrics"]
Path("/outputs/out.json").write_text(
    json.dumps({"genes": metrics["genes"], "profiles": metrics["profiles"]})
)
""",
            )
            payload = {
                "operation": OPERATION,
                "session_id": sid,
                "objective": "Summarize the approved audit output via registered OCI",
                "method_version_id": method["version_id"],
                "step_name": "Summarize audited counts",
                "parameters": {"threshold": 1},
                "input_versions": {"audit": artifact["version_id"]},
            }
            custom = await _rpc(client, "plan_rna_workflow", payload)
            assert custom["session_id"] == sid
            with executor.training.open(artifact["sha256"]):
                pass
            from devbench.platform.plugin_jobs import JobStore

            approval = _approved_plan(custom)
            JobStore(workspace).approve(
                sid,
                custom["plan_id"],
                custom["plan_sha256"],
                actor_id="synthetic-test-operator",
                surface="cli",
                auth_method="local_os_user",
            )
            await _rpc(
                client,
                "execute_reviewed_plan",
                {**approval, "plan_sha256": "0" * 64},
                blocked="plan_identity_mismatch",
            )
            receipt, _ = await _complete(client, custom)
            assert receipt["claim_scope"] == "exploratory_only"
            assert (
                receipt["registered_method_outputs"]["out"]["claim_tier"]
                == "L1_execution_verified"
            )
            proof = receipt["graph_execution"]
            flow = AdaptiveWorkflow(ledger, sid)
            outcome = flow._read_projection(
                lambda db: flow._state(db).outcome(proof["step_id"])
            )
            assert outcome["accepted_record_id"] == proof["accepted_record_id"]
            assert outcome["receipt_sha256"] == proof["receipt_sha256"]
            assert outcome["consumed"][0]["version_id"] == artifact["version_id"]
            assert outcome["consumed"][0]["sha256"] == artifact["sha256"]
            output = flow.current_use(proof["step_id"], "out", purpose="cache")
            with executor.training.open(output.sha256) as stream:
                assert json.load(stream) == {"genes": 2, "profiles": 4}
            assert (
                receipt["registered_method_outputs"]["out"]["sha256"] == output.sha256
            )
            projection = await _projection(port, f"/api/scientific/adaptive/{sid}")
            run = next(
                item
                for item in projection["plugin_session"]["runs"]
                if item["run_id"] == receipt["run_id"]
            )
            assert run["status"] == "completed"
            assert run["operation"] == OPERATION
            assert run.get("graph_execution") == proof, run.get("warnings")
            assert run["summary_tables"] == receipt["summary_tables"]
            resumed = await _rpc(client, "execute_reviewed_plan", approval)
            assert resumed["run_id"] == receipt["run_id"]

    with _offline_studio(settings, inputs, workspace) as port:
        asyncio.run(exercise(port))
