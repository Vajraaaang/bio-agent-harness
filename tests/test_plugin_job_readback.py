"""MCP run reads must leave other jobs and historical ledgers untouched."""

import sqlite3
from contextlib import closing

import pytest

from devbench.engine.workflow_operations import (
    LEDGER_NAME,
    OperationBlocked,
    WorkflowOperationLedger,
)
from devbench.platform.plugin_jobs import JobStore

pytestmark = pytest.mark.wal


@pytest.mark.parametrize("existing_ledger", [False, True])
def test_legacy_run_read_projects_worker_loss_without_writes(
    workspace_tmp_path, existing_ledger
):
    store = JobStore(workspace_tmp_path)
    ledger_path = store.workspace / "methods" / LEDGER_NAME
    if existing_ledger:
        WorkflowOperationLedger(ledger_path, scientific=True)
    sid = "a" * 32
    for key in ("b", "c"):
        rid = key * 32
        pid, sha = store.freeze({"session_id": sid, "plan_id": rid})
        with closing(sqlite3.connect(store.path)) as db, db:
            db.execute(
                "INSERT INTO plugin_runs VALUES (?, ?, ?, ?, 'running', NULL, '{}', "
                "NULL, NULL, 1, 1, NULL)",
                (rid, sid, pid, sha),
            )

    observed = store.read_run(sid, "b" * 32)
    assert observed["status"] == "interrupted"
    assert observed["cleanup_state"] == "unconfirmed"
    assert observed["error_text"] == "worker_not_running"
    assert observed["finished_at"] is None
    with pytest.raises(OperationBlocked, match="unknown_run"):
        store.read_run("d" * 32, "b" * 32)

    with closing(sqlite3.connect(store.path.as_uri() + "?mode=ro", uri=True)) as db:
        assert (
            db.execute(
                "SELECT status, stage_json, error_text, updated_at, finished_at "
                "FROM plugin_runs"
            ).fetchall()
            == [("running", "{}", None, 1, None)] * 2
        )
    assert ledger_path.exists() is existing_ledger
    if existing_ledger:
        with closing(
            sqlite3.connect(ledger_path.as_uri() + "?mode=ro", uri=True)
        ) as db:
            assert (
                db.execute(
                    "SELECT name FROM sqlite_master WHERE name='scientific_daemon_runs'"
                ).fetchone()
                is None
            )


def test_read_run_prefers_authoritative_managed_status(workspace_tmp_path):
    store = JobStore(workspace_tmp_path)
    sid, pid, rid = "a" * 32, "b" * 32, "c" * 32
    _, sha = store.freeze({"session_id": sid, "plan_id": pid})
    with store.authority.daemon_transaction() as db:
        db.execute(
            "INSERT INTO scientific_daemon_runs(run_id,session_id,plan_id,plan_sha256,"
            "profile,pool,memory_mb,deadline_sec,status,started_at,updated_at,"
            "receipt_json,finished_at) VALUES (?,?,?,?,'audit','cpu',0,30,'finishing',"
            "1,2,'{}',2)",
            (rid, sid, pid, sha),
        )
    observed = store.read_run(sid, rid)
    assert observed["status"] == "running"
    assert observed["receipt_json"] is observed["finished_at"] is None
    with pytest.raises(OperationBlocked, match="unknown_run"):
        store.read_run("d" * 32, rid)
