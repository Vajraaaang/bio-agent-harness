"""Wire regressions for confirmed counts and sanitized MCP output failures."""

import asyncio
import hashlib
import json
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from devbench.analysis.evidence_access import EvidenceBroker
from devbench.daemon.ipc import daemon_status, stop_daemon
from devbench.platform import plugin_mcp
from devbench.platform.plugin_jobs import digest

pytestmark = pytest.mark.wal

_SENTINEL = "PRIVATE_PROTOCOL_REVIEW_SENTINEL"
_CONFIRMATION_NOTE = (
    "Caller confirmed suspected integer-transformed count distribution "
    "against external provenance."
)


def _serve_with_invalid_outputs():
    """Inject a defect in the test server's return value after successful inspection."""
    inspect = plugin_mcp.Plugin.inspect

    def invalid_inspect(self, args):
        result = inspect(self, args)
        case = Path(args.inputs[0].path).stem
        if case == "extra":
            result[_SENTINEL] = _SENTINEL
        elif case == "wrong-type":
            result["inputs"] = _SENTINEL
        elif case == "serialization":
            result["inputs"] = type(_SENTINEL, (), {})()
        return result

    plugin_mcp.Plugin.inspect = invalid_inspect
    raise SystemExit(plugin_mcp.main())


@asynccontextmanager
async def _session(workspace, inputs, errlog, *, invalid=False):
    module = (
        [
            "-c",
            "import tests.test_plugin_protocol_review as review; "
            "review._serve_with_invalid_outputs()",
        ]
        if invalid
        else ["-m", "devbench.platform.plugin_mcp"]
    )
    params = StdioServerParameters(
        command=sys.executable,
        args=[
            *module,
            "--no-auto-studio",
            "--workspace",
            str(workspace),
            "--input-root",
            str(inputs),
        ],
        env={
            key: os.environ[key]
            for key in (
                "PATH",
                "PYTHONPATH",
                "DYLD_LIBRARY_PATH",
                "LD_LIBRARY_PATH",
                "VECLIB_MAXIMUM_THREADS",
                "OPENBLAS_NUM_THREADS",
                "OMP_NUM_THREADS",
                "MKL_NUM_THREADS",
            )
            if key in os.environ
        },
    )
    async with stdio_client(params, errlog=errlog) as (reader, writer):
        async with ClientSession(reader, writer) as client:
            await client.initialize()
            yield client


def test_confirmed_count_receipt_reaches_wire_replay_and_studio(workspace_tmp_path):
    workspace = workspace_tmp_path / "workspace"
    inputs = workspace_tmp_path / "inputs"
    inputs.mkdir(mode=0o700)
    path = inputs / "counts.csv"
    path.write_text("gene,s1,s2,s3,s4\ng1,1,2,3,4\ng2,4,3,2,1\n")
    selected = [{"path": str(path), "data_classification": "synthetic"}]
    confirmation = {
        "source_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "slot": "feature_rows",
        "evidence": f"Reviewed the original synthetic counts. {_SENTINEL}",
    }
    assessment = {
        "request": {
            "family_id": 1,
            "question": "differential_expression",
            "assay": "bulk_rna_seq",
            "entry_point": "count_matrix",
        },
        "inputs": selected,
    }
    arguments = {
        "operation": "matrix_audit",
        "objective": "Inspect the declared count matrix",
        "inputs": selected,
        "measurement": "raw_counts",
    }

    async def check(errlog):
        async with asyncio.timeout(90), _session(workspace, inputs, errlog) as client:
            for review in (
                {},
                {"count1_confirmation": {**confirmation, "source_sha256": "0" * 64}},
                {"count1_confirmation": {**confirmation, "slot": "layers/other"}},
            ):
                held = await client.call_tool(
                    "plan_rna_workflow",
                    {**arguments, "answerability": {**assessment, **review}},
                )
                assert held.isError
                assert (
                    held.structuredContent["reason"]
                    == "suspected_transformed_integer_counts"
                )
            planned = await client.call_tool(
                "plan_rna_workflow",
                {
                    **arguments,
                    "answerability": {
                        **assessment,
                        "count1_confirmation": confirmation,
                    },
                },
            )
            assert not planned.isError, planned.structuredContent
            identity = {
                key: planned.structuredContent[key]
                for key in ("session_id", "plan_id", "plan_sha256")
            }
            launched = await client.call_tool("execute_reviewed_plan", identity)
            assert not launched.isError, launched.structuredContent
            run = launched.structuredContent
            subject = {key: run[key] for key in ("session_id", "run_id")}
            while run["status"] in {"queued", "running"}:
                await asyncio.sleep(0.2)
                reply = await client.call_tool("get_run_status", subject)
                assert not reply.isError, reply.structuredContent
                run = reply.structuredContent
            assert run["status"] == "completed", run["warnings"]
            reply = await client.call_tool("get_execution_receipt", subject)
            assert not reply.isError, reply.structuredContent
            assert _SENTINEL not in reply.model_dump_json()
            assert json.loads(reply.content[0].text) == reply.structuredContent
            return reply.structuredContent

    try:
        with (workspace_tmp_path / "server.stderr").open("w") as errlog:
            wire = asyncio.run(check(errlog))
        replay = workspace / "runs" / wire["run_id"] / "replay_bundle"
        frozen = json.loads((replay / "plan.json").read_bytes())
        assert frozen["count1_confirmation"] == confirmation
        receipt = json.loads((replay / "receipt.json").read_bytes())
        with plugin_mcp.studio_job_readback(workspace) as db:
            row = db.execute(
                "SELECT * FROM plugin_runs WHERE run_id=?", (wire["run_id"],)
            ).fetchone()
        broker = EvidenceBroker.open_existing(plugin_mcp._ledger(workspace).scientific)
        studio = plugin_mcp.studio_run_projection(
            workspace, broker, frozen, row, frozenset()
        )
        for projection in (wire, receipt, studio):
            assert projection["count1_confirmation_sha256"] == digest(confirmation)
            assert _CONFIRMATION_NOTE in projection["limitations"]
            assert _SENTINEL not in json.dumps(projection)
    finally:
        if daemon_status(workspace).get("status") == "running":
            stop_daemon(workspace)


@pytest.mark.parametrize("case", ["extra", "wrong-type", "serialization"])
def test_invalid_tool_output_is_redacted_before_sdk_validation(
    workspace_tmp_path, case
):
    workspace = workspace_tmp_path / "workspace"
    inputs = workspace_tmp_path / "inputs"
    inputs.mkdir(mode=0o700)
    path = inputs / f"{case}.csv"
    path.write_text("gene,s1,s2\ng1,1,2\n")
    log = workspace_tmp_path / "server.stderr"

    async def check(errlog):
        async with (
            asyncio.timeout(30),
            _session(workspace, inputs, errlog, invalid=True) as client,
        ):
            return await client.call_tool(
                "inspect_rna_inputs",
                {"inputs": [{"path": str(path), "data_classification": "synthetic"}]},
            )

    with log.open("w") as errlog:
        reply = asyncio.run(check(errlog))
    assert _SENTINEL not in reply.model_dump_json()
    assert _SENTINEL not in log.read_text()
    assert reply.isError
    assert reply.structuredContent == {
        "error": "OperationBlocked",
        "reason": "internal_tool_error",
    }
    assert json.loads(reply.content[0].text) == reply.structuredContent
