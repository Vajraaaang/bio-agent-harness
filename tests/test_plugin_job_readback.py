"""MCP run reads must leave other jobs and historical ledgers untouched."""

import fcntl
import json
import os
import sqlite3
import subprocess
import sys
from contextlib import closing

import psutil
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


def _managed_run(store, status, worker_pid=None, created=None):
    sid, plan_id, rid = "a" * 32, "b" * 32, "c" * 32
    _, sha = store.freeze(
        {
            "session_id": sid,
            "plan_id": plan_id,
            "operation": "matrix_audit",
            "data_classification": "public",
            "profiles": [],
        }
    )
    with store.authority.daemon_transaction() as db:
        db.execute(
            "INSERT INTO scientific_daemon_runs(run_id,session_id,plan_id,plan_sha256,"
            "profile,pool,memory_mb,deadline_sec,status,started_at,updated_at,"
            "pid,create_time,receipt_json,finished_at) VALUES "
            "(?,?,?,?,'audit','cpu',0,30,?,1,2,?,?,?,?)",
            (
                rid,
                sid,
                plan_id,
                sha,
                status,
                worker_pid,
                created,
                "{}" if status == "finishing" else None,
                2 if status == "finishing" else None,
            ),
        )
    store.project_run(store.managed_run(rid))
    return sid, rid


@pytest.mark.parametrize(
    ("status", "visible"),
    [
        ("queued", "queued"),
        ("starting", "starting"),
        ("running", "running"),
        ("finishing", "running"),
        ("cancelling", "cancelling"),
    ],
)
def test_read_run_preserves_live_owner_transitions(
    workspace_tmp_path, monkeypatch, status, visible
):
    from devbench.daemon import ipc

    store = JobStore(workspace_tmp_path)
    sid, rid = _managed_run(store, status)
    monkeypatch.setattr(ipc, "daemon_status", lambda _: {"status": "running"})
    observed = store.read_run(sid, rid)
    assert observed["status"] == visible
    assert observed["cleanup_state"] == "pending"
    assert observed["receipt_json"] is observed["finished_at"] is None
    with pytest.raises(OperationBlocked, match="unknown_run"):
        store.read_run("d" * 32, rid)


@pytest.mark.parametrize("status", ["starting", "running", "finishing", "cancelling"])
def test_stopped_owner_and_lost_worker_project_interruption_without_writes(
    workspace_tmp_path, monkeypatch, status
):
    from devbench.daemon import ipc
    from devbench.platform.plugin_mcp import Plugin, Run

    inputs = workspace_tmp_path / "inputs"
    inputs.mkdir()
    plugin = Plugin(workspace_tmp_path / "workspace", inputs, None)
    store = plugin.jobs
    sid, rid = _managed_run(
        store, status, os.getpid(), psutil.Process().create_time() - 2
    )
    ledger = store.workspace / "methods" / LEDGER_NAME
    assert ipc.daemon_status(store.workspace)["status"] == "stopped"

    def do_not_contact_daemon(*_args, **_kwargs):
        pytest.fail("Status attempted to contact or start a daemon")

    monkeypatch.setattr(ipc, "start_daemon", do_not_contact_daemon)
    monkeypatch.setattr(ipc, "request", do_not_contact_daemon)
    with (
        closing(store._connect(readonly=True)) as shadow,
        closing(sqlite3.connect(ledger.as_uri() + "?mode=ro", uri=True)) as authority,
    ):

        def snapshot():
            return (
                list(map(tuple, shadow.execute("SELECT * FROM plugin_runs"))),
                authority.execute("SELECT * FROM scientific_daemon_runs").fetchall(),
                tuple(
                    db.execute("PRAGMA data_version").fetchone()[0]
                    for db in (shadow, authority)
                ),
            )

        before = snapshot()
        observed = store.read_run(sid, rid)
        assert observed["status"] == "interrupted"
        assert observed["error_text"] == "worker_not_running"
        assert observed["cleanup_state"] == "unconfirmed"
        assert observed["receipt_json"] is observed["finished_at"] is None
        public = plugin.status(Run(session_id=sid, run_id=rid))
        assert public["status"] == "interrupted" and public["stale"]
        assert public["warnings"] == ["worker_not_running"]
        assert snapshot() == before
    assert not (store.workspace / ".devbench-daemon").exists()


@pytest.mark.parametrize("status", ["queued", "running"])
def test_stopped_owner_preserves_pending_queue_and_live_worker(
    workspace_tmp_path, status
):
    store = JobStore(workspace_tmp_path)
    pid, created = (
        (os.getpid(), psutil.Process().create_time())
        if status == "running"
        else (None, None)
    )
    sid, rid = _managed_run(store, status, pid, created)
    assert store.read_run(sid, rid)["status"] == status


def test_completion_committed_during_owner_probe_wins_over_interruption(
    workspace_tmp_path, monkeypatch
):
    from devbench.daemon import ipc

    store = JobStore(workspace_tmp_path)
    sid, rid = _managed_run(store, "finishing")

    def finish_and_stop(_workspace):
        # The actual owner finished cleanup after the reader took its first snapshot.
        with store.authority.daemon_transaction() as db:
            db.execute(
                "UPDATE scientific_daemon_runs SET status='completed', "
                "cleanup_state='confirmed', updated_at=3 WHERE run_id=?",
                (rid,),
            )
        return {"status": "stopped"}

    monkeypatch.setattr(ipc, "daemon_status", finish_and_stop)
    observed = store.read_run(sid, rid)
    assert observed["status"] == "completed"
    assert observed["cleanup_state"] == "confirmed"
    assert observed["receipt_json"] == "{}"


def test_reused_daemon_pid_cannot_vouch_for_missing_worker(workspace_tmp_path):
    from devbench.daemon import auth

    store = JobStore(workspace_tmp_path)
    sid, rid = _managed_run(store, "running")
    with auth.state_directory(store.workspace, create=True) as directory:
        marker = {"pid": os.getpid(), "create_time": psutil.Process().create_time() - 2}
        auth.write_file(directory, "daemon.json", json.dumps(marker).encode())
        fd = os.open("daemon.lock", os.O_CREAT | os.O_RDWR, 0o600, dir_fd=directory)
        with os.fdopen(fd, "rb") as locked:
            fcntl.flock(locked, fcntl.LOCK_EX)
            assert store.read_run(sid, rid)["status"] == "interrupted"


def test_detached_managed_mirror_tracks_worker_exit_without_authority_writes(
    workspace_tmp_path,
):
    store = JobStore(workspace_tmp_path)
    sid, plan, rid = "a" * 32, "b" * 32, "c" * 32
    _, sha = store.freeze({"session_id": sid, "plan_id": plan})
    with subprocess.Popen(
        [sys.executable, "-c", "import sys; sys.stdin.buffer.read(1)"],
        stdin=subprocess.PIPE,
    ) as worker:
        stage = json.dumps(
            {
                "daemon_managed": True,
                "process_created_at": psutil.Process(worker.pid).create_time(),
                "cleanup_state": "pending",
            }
        )
        with closing(store._connect()) as db, db:
            db.execute(
                "INSERT INTO plugin_runs VALUES (?, ?, ?, ?, 'running', ?, ?, NULL, "
                "NULL, 1, 1, NULL)",
                (rid, sid, plan, sha, worker.pid, stage),
            )
        assert store.read_run(sid, rid)["status"] == "running"
        worker.communicate(b"x", timeout=5)
    observed = store.read_run(sid, rid)
    assert (observed["status"], observed["cleanup_state"]) == (
        "interrupted",
        "unconfirmed",
    )
    with closing(store._connect(readonly=True)) as db:
        assert tuple(
            db.execute("SELECT status, stage_json FROM plugin_runs").fetchone()
        ) == (
            "running",
            stage,
        )
    assert not (store.workspace / "methods" / LEDGER_NAME).exists()
