"""Layer 3. Verify admission refusal and recovery of workspace-owned workers."""

import asyncio
import json
import os
import signal
import sqlite3
from contextlib import suppress
from datetime import datetime

import psutil
import pytest

from tests.test_mcp_conformance import minimal_client
from tests.test_research_daemon import (
    _admission,
    _alive,
    _audit_plan,
    _daemon,
    _freeze,
    _json,
    _ready,
    _state,
    _stdio_session,
    _supervisor,
    _tool,
    _until,
)

pytestmark = pytest.mark.wal


async def _refusals(client, inputs):
    outside = inputs.parent / "outside.csv"
    outside.write_text("gene,A,B\nred,1,2\n")
    linked = inputs / "link.csv"
    linked.symlink_to(outside)
    hardlinked = inputs / "alias.csv"
    os.link(outside, hardlinked)
    for path in (outside, linked, hardlinked):
        reg = _admission(path)
        rejected = await client.call_tool("register_investigation_input", reg)
        assert rejected.isError
        assert rejected.structuredContent["category"] == "input_scope_violation"

    counts = inputs / "counts.csv"
    counts.write_bytes(outside.read_bytes())
    admitted = await client.call_tool(
        "register_investigation_input", _admission(counts)
    )
    plan = await client.call_tool(
        "freeze_analysis_plan", _audit_plan(admitted.structuredContent)
    )
    identity = {
        k: plan.structuredContent[k] for k in ("session_id", "plan_id", "plan_sha256")
    }
    bad = await client.call_tool(
        "submit_reviewed_run", {**identity, "plan_sha256": "0" * 64}
    )
    assert bad.isError and bad.structuredContent["category"] == "plan_identity_mismatch"
    state = await client.call_tool(
        "list_investigation_state", {"session_id": identity["session_id"]}
    )
    assert not state.structuredContent["runs"]


async def _check_design(client, path, rows, units, nuisance):
    path.write_text("donor,condition,batch,library\n" + rows)
    reg = _admission(path)
    reg["input"]["file_kind"] = "metadata"
    reg["representation_class"] = "metadata_only"
    reg["provenance_declaration"]["biological_unit"] = "donor"
    grant = (
        await client.call_tool("register_investigation_input", reg)
    ).structuredContent
    aid = grant["artifact"]["artifact_id"]
    result = await client.call_tool(
        "check_answerability",
        {
            "session_id": grant["session_id"],
            "question_text": "Does group predict expression?",
            "axis_b_class": "B1",
            "assay": "bulk_rna_seq",
            "entry_point": "count_matrix",
            "artifact_ids": [aid],
            "design": {
                "metadata_artifact_id": aid,
                "biological_unit": "donor",
                "formula": "~condition",
                "batch_columns": nuisance,
                "forensics_verified": True,
                "contrast": {
                    "factor": "condition",
                    "numerator": "B",
                    "denominator": "A",
                },
            },
        },
    )
    assert not result.isError
    data = result.structuredContent
    assert data["status"] == "refused" and data["epistemic_state"] == "E_ABSTAIN"
    diagnostics = data["design_diagnostics"]
    assert diagnostics["biological_units_per_group"] == units
    if nuisance:
        assert not diagnostics["full_rank"]


def test_input_design_and_digest_refusals(workspace_tmp_path):
    with _daemon(workspace_tmp_path) as (workspace, inputs, _):

        async def run():
            async with minimal_client(workspace, inputs) as client:
                await _refusals(client, inputs)
                await _check_design(
                    client,
                    inputs / "batch.csv",
                    "d1,A,x,r1\nd2,A,x,r2\nd3,B,y,r3\nd4,B,y,r4\n",
                    {"A": 2, "B": 2},
                    ["batch"],
                )
                await _check_design(
                    client,
                    inputs / "repeat.csv",
                    "d1,A,x,r1\nd1,A,x,r2\nd2,B,x,r3\nd2,B,x,r4\n",
                    {"A": 1, "B": 1},
                    [],
                )

        asyncio.run(run())


def test_killed_stdio_client_reconnects_to_live_audit_and_receipt(workspace_tmp_path):
    from devbench.daemon import ipc
    from devbench.engine.workflow_operations import LEDGER_NAME

    with _daemon(workspace_tmp_path, idle_timeout_sec=4) as (workspace, inputs, daemon):
        counts = inputs / "counts.csv"
        counts.write_text("gene,A,B\nred,1,2\nblue,3,4\ngreen,2,7\n")
        sentinel = "PRIVATE_DO_NOT_ECHO_daemon_test"
        private = inputs / (sentinel + ".csv")
        private.write_text("gene,donor1,donor2\n" + sentinel + ",1,3\n")
        errors = workspace_tmp_path / "stderr"

        def worker_for(run_id):
            record = ipc.request(workspace, "run_status", run_id=run_id)
            if not record.get("pid"):
                assert record["status"] in {"queued", "starting"}
                return None
            worker = psutil.Process(record["pid"])
            assert os.getpgid(worker.pid) == worker.pid
            return worker

        async def reconnect():
            worker = None
            try:
                with errors.open("wb") as stderr:
                    async with _stdio_session(workspace, inputs, stderr) as first:
                        admitted = await _tool(
                            first, "register_investigation_input", _admission(counts)
                        )
                        plan = await _tool(
                            first, "freeze_analysis_plan", _audit_plan(admitted)
                        )
                        first_id = first.pid
                        identity = {
                            k: plan[k] for k in ("session_id", "plan_id", "plan_sha256")
                        }
                        started = await _tool(first, "submit_reviewed_run", identity)
                        key = {k: started[k] for k in ("session_id", "run_id")}
                        worker = _until(lambda: worker_for(key["run_id"]))
                        worker.suspend()
                    assert first.returncode == -signal.SIGKILL
                    # The active worker alone must keep the daemon alive.
                    await asyncio.sleep(4.2)
                    assert _alive(worker)
                    assert ipc.daemon_status(workspace)["pid"] == daemon["pid"]
                    async with _stdio_session(workspace, inputs, stderr) as second:
                        assert second.pid != first_id
                        state = await _tool(
                            second,
                            "list_investigation_state",
                            {"session_id": key["session_id"]},
                        )
                        assert key["run_id"] in {r["run_id"] for r in state["runs"]}
                        live = await _tool(second, "get_run_status", key)
                        assert live["status"] in {"queued", "running"}
                        assert live["progress"]["completed"] == 0
                        worker.resume()
                        worker = None
                        async with asyncio.timeout(45):
                            while True:
                                last = await _tool(second, "get_run_status", key)
                                if last["status"] not in {"queued", "running"}:
                                    break
                                await asyncio.sleep(0.1)
                        assert last["status"] == "completed", last["warnings"]
                        receipt = await _tool(second, "get_run_receipt", key)
                        assert (
                            receipt["run_id"] == key["run_id"]
                            and receipt["metrics"]["genes"] == 3
                        )
                        assert receipt["clinical_release_allowed"] is False
                        private_result = await _tool(
                            second,
                            "inspect_inputs",
                            {
                                "inputs": [
                                    {
                                        "path": str(private),
                                        "file_kind": "expression",
                                        "data_classification": "private",
                                    }
                                ]
                            },
                            expect_error=True,
                        )
                        assert sentinel not in json.dumps(private_result)
            finally:
                if worker is not None:
                    with suppress(psutil.NoSuchProcess):
                        worker.resume()

        asyncio.run(reconnect())
        assert sentinel not in errors.read_text()
        for path in (
            workspace / "methods" / LEDGER_NAME,
            workspace / "plugin-jobs.sqlite3",
        ):
            with sqlite3.connect(path) as db:
                assert db.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
                assert db.execute("PRAGMA foreign_key_check").fetchall() == []


def test_fifo_cpu_pool_progress_profiles_and_third_job(workspace_tmp_path):
    with _supervisor(workspace_tmp_path, cpu_slots=2, max_concurrent_runs=2) as (
        sup,
        store,
        runs,
    ):
        first, second, third = [_freeze(store) for _ in range(3)]
        a = sup.submit(**first, profile="qc")["run_id"]
        b = sup.submit(**second, profile="standard")["run_id"]
        c = sup.submit(**third, profile="long")["run_id"]
        a_root, b_root = _ready(runs, a), _ready(runs, b)
        assert sup.status(a)["status"] == sup.status(b)["status"] == "running"
        assert sup.status(c)["status"] == "queued"
        assert sup.submit(**third, profile="long")["run_id"] == c
        progress = _json(
            store.workspace / ".devbench-daemon" / "runs" / a / "progress.json"
        )
        assert progress["step"] == "waiting_for_release" and progress["percent"] == 50
        assert (
            progress["rss_mb"] >= 0
            and datetime.fromisoformat(progress["updated_at_utc"]).tzinfo
        )
        assert sup.status(a)["deadline_sec"] == 300
        assert sup.status(b)["deadline_sec"] == 14_400
        assert sup.status(c)["deadline_sec"] == 259_200
        (a_root / "release").touch()
        _state(sup, a, "completed")
        _ready(runs, c)
        assert sup.status(c)["status"] == sup.status(b)["status"] == "running"
        (b_root / "release").touch()
        (runs / c / "release").touch()
        for rid in (b, c):
            _state(sup, rid, "completed")
        _until(lambda: sup.active_count() == 0)


def test_worker_leader_crash_reaps_owned_descendant(workspace_tmp_path):
    with _supervisor(workspace_tmp_path) as (sup, store, runs):
        rid = sup.submit(**_freeze(store, resist_term=True))["run_id"]
        root = _ready(runs, rid)
        leader = psutil.Process(sup.status(rid)["pid"])
        child = psutil.Process(int((root / "child.pid").read_text()))
        assert os.getpgid(child.pid) == leader.pid
        try:
            leader.kill()
            interrupted = _state(sup, rid, "interrupted")
            assert interrupted["cleanup_state"] == "confirmed"
            assert not _alive(child)
        finally:
            # The exact birth-checked test child is still ours if cleanup regresses.
            if _alive(child):
                child.kill()


def _killed_after_container_create(stage, binding):
    """Crashing fixture process drives a real daemon create before losing its PID."""
    from devbench.engine import mounted_kernel_oci as oci
    from devbench.engine.container_backend import DockerBackend

    class OwnedFixtureDocker(oci.LocalDocker):
        def _call(self, args, **kwargs):
            result = super()._call(args, **kwargs)
            if args[0] == "create":
                os.kill(os.getpid(), signal.SIGKILL)
            return result

    oci.run_mounted_container(
        stage,
        **binding,
        timeout=15,
        memory_mb=512,
        backend=DockerBackend(runtime_factory=OwnedFixtureDocker),
    )


def test_killed_worker_leaves_no_docker_orphan(workspace_tmp_path):
    import multiprocessing
    from uuid import uuid4

    from devbench.engine import mounted_kernel_oci as oci
    from devbench.engine.container_backend import DockerBackend
    from devbench.platform.workbench_ui import _detect_mounted_image

    image = os.environ.get("DEVBENCH_RNA_KERNEL_TEST_IMAGE") or _detect_mounted_image()
    if not image:
        pytest.skip("No pinned local mounted Docker image")
    attempt = uuid4().hex
    stage = workspace_tmp_path / ".work"
    stage.mkdir()
    (stage / "inputs").mkdir()
    (stage / "outputs").mkdir()
    binding = {"attempt_id": attempt, "image_id": image, "request_sha": "b" * 64}
    worker = multiprocessing.get_context("spawn").Process(
        target=_killed_after_container_create, args=(stage, binding)
    )
    worker.start()
    try:
        worker.join(40)
    finally:
        if worker.is_alive():
            worker.kill()
            worker.join(5)
        proof = oci.recover_mounted_container(stage, **binding, backend=DockerBackend())
    assert worker.exitcode == -signal.SIGKILL
    assert proof["disposition"] == "removed" and proof["cleanup_confirmed"]
    with DockerBackend().session(stage) as runtime:
        assert oci._container_ids(runtime, f"name=^/{proof['name']}$") == []
