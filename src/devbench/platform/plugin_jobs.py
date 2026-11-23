"""Durable local plan custody and isolated workers for the RNA plugin."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import signal
import sqlite3
import stat
import subprocess
import sys
import threading
import time
import traceback
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlsplit
from uuid import uuid4

import psutil

from devbench.engine.workflow_operations import OperationBlocked
from devbench.engine.workspace import read_bounded_file
from devbench.study.permissions import require_protected_storage

ACTIVE = {"queued", "running"}
MAX_SECONDS = 3600
MAX_DOCUMENT_BYTES = 256 * 1024


def canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def directory(value: Path, *, exists: bool = True) -> Path:
    if (
        not value.is_absolute()
        or value == Path("/")
        or value.resolve(strict=exists) != value
    ):
        raise ValueError("Use a canonical absolute directory")
    if exists and not value.is_dir():
        raise ValueError("Use an existing directory")
    return value


def studio_handoff(
    workspace: Path,
    input_root: Path,
    sid: str,
    view: str,
    *,
    mounted_image_id: str | None = None,
) -> dict[str, Any]:
    import shlex

    launch = [
        "devbench",
        "workbench",
        "--offline-only",
        "--workspace",
        str(workspace),
        "--input-root",
        str(input_root),
        "--enable-scientific",
    ]
    if mounted_image_id is not None:
        launch += ["--allow-data-execution", "--mounted-image-id", mounted_image_id]
    url = None
    try:
        state = json.loads(read_bounded_file(workspace / "studio-server.json", 4096))
        target = urlsplit(state["url"])
        pid = state["pid"]
        if (
            state["workspace"] == str(workspace)
            and type(pid) is int
            and pid > 0
            and psutil.pid_exists(pid)
            and target.scheme == "http"
            and target.hostname == "127.0.0.1"
            and target.port is not None
            and target.netloc == f"127.0.0.1:{target.port}"
            and target.path in {"", "/"}
            and not target.query
            and not target.fragment
        ):
            url = f"http://{target.netloc}/?" + urlencode(
                {"session_id": sid, "view": view}
            )
    except (OSError, ValueError, TypeError, KeyError):
        pass
    return {"studio_url": url, "launch_hint": shlex.join(launch)}


class JobStore:
    def __init__(self, workspace: Path):
        self.workspace = directory(workspace, exists=False)
        self.workspace.mkdir(mode=0o700, parents=True, exist_ok=True)
        require_protected_storage(self.workspace)
        self.path = workspace / "plugin-jobs.sqlite3"
        try:
            descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            pass
        else:
            os.close(descriptor)
        for path in (
            self.path,
            self.path.with_name(self.path.name + "-wal"),
            self.path.with_name(self.path.name + "-shm"),
        ):
            try:
                info = path.lstat()
            except FileNotFoundError:
                if path == self.path:
                    raise
                continue
            if (
                not stat.S_ISREG(info.st_mode)
                or stat.S_IMODE(info.st_mode) != 0o600
                or info.st_uid != os.getuid()
                or info.st_nlink != 1
            ):
                raise PermissionError(
                    "Plugin job storage requires owner-only regular files"
                )
        with closing(self._connect()) as db:
            if db.execute("PRAGMA journal_mode=WAL").fetchone()[0] != "wal":
                raise RuntimeError("Plugin job storage requires SQLite WAL")
        with self._write() as db:
            self._initialize_schema(db)
        self.reconcile()

    @staticmethod
    def _initialize_schema(db: sqlite3.Connection) -> None:
        legacy = db.execute(
            "SELECT 1 FROM pragma_index_list('plugin_plans') AS idx "
            "WHERE idx.origin='u' AND "
            "(SELECT group_concat(name) FROM pragma_index_info(idx.name))='session_id'"
        ).fetchone()
        if legacy:
            # Rename the referencing table first. SQLite updates its foreign
            # key when the parent is renamed. Copy back in parent-first order.
            db.execute("ALTER TABLE plugin_runs RENAME TO plugin_runs_legacy")
            db.execute("ALTER TABLE plugin_plans RENAME TO plugin_plans_legacy")
        db.execute("""
            CREATE TABLE IF NOT EXISTS plugin_plans (
                plan_id TEXT PRIMARY KEY, session_id TEXT NOT NULL,
                plan_sha256 TEXT NOT NULL, plan_json TEXT NOT NULL,
                created_at REAL NOT NULL
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS plugin_runs (
                run_id TEXT PRIMARY KEY, session_id TEXT NOT NULL,
                plan_id TEXT NOT NULL UNIQUE REFERENCES plugin_plans(plan_id),
                plan_sha256 TEXT NOT NULL, status TEXT NOT NULL, pid INTEGER,
                stage_json TEXT NOT NULL, receipt_json TEXT, error_text TEXT,
                started_at REAL NOT NULL, updated_at REAL NOT NULL, finished_at REAL
            )
        """)
        if legacy:
            db.execute("INSERT INTO plugin_plans SELECT * FROM plugin_plans_legacy")
            db.execute("INSERT INTO plugin_runs SELECT * FROM plugin_runs_legacy")
            db.execute("DROP TABLE plugin_runs_legacy")
            db.execute("DROP TABLE plugin_plans_legacy")
            if db.execute("PRAGMA foreign_key_check").fetchone() is not None:
                raise RuntimeError("Plugin job migration failed its foreign key check")
        db.execute(
            "CREATE INDEX IF NOT EXISTS idx_plugin_plans_session_created "
            "ON plugin_plans(session_id, created_at DESC)"
        )

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path.as_uri() + "?mode=rw", uri=True, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        return db

    @contextmanager
    def _write(self):
        with closing(self._connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            yield db

    def freeze(self, plan: dict[str, Any]) -> tuple[str, str]:
        encoded = canonical(plan)
        if len(encoded) > MAX_DOCUMENT_BYTES:
            raise ValueError("Plan exceeds its immutable document budget")
        plan_id, sha = plan["plan_id"], hashlib.sha256(encoded).hexdigest()
        with self._write() as db:
            db.execute(
                "INSERT INTO plugin_plans VALUES (?, ?, ?, ?, ?)",
                (plan_id, plan["session_id"], sha, encoded.decode(), time.time()),
            )
        return plan_id, sha

    @staticmethod
    def _plan(
        row: sqlite3.Row | None, plan_id: str | None = None, sha: str | None = None
    ) -> dict[str, Any]:
        if row is None or (plan_id is not None and row["plan_id"] != plan_id):
            raise OperationBlocked("plan_identity_mismatch")
        encoded = row["plan_json"].encode()
        actual = hashlib.sha256(encoded).hexdigest()
        if not hmac.compare_digest(actual, row["plan_sha256"]) or (
            sha is not None and not hmac.compare_digest(actual, sha)
        ):
            raise OperationBlocked("plan_identity_mismatch")
        plan = json.loads(encoded)
        if plan["session_id"] != row["session_id"] or plan["plan_id"] != row["plan_id"]:
            raise OperationBlocked("plan_identity_mismatch")
        return plan

    def has_private_input(self, fingerprints: set[str | None]) -> bool:
        hashes = sorted(value for value in fingerprints if value is not None)
        if not hashes:
            return False
        with closing(self._connect()) as db:
            return (
                db.execute(
                    """
                SELECT 1
                FROM plugin_plans AS plan,
                     json_each(plan.plan_json, '$.profiles') AS item
                WHERE json_extract(plan.plan_json, '$.data_classification') = 'private'
                  AND json_extract(item.value, '$.sha256') IN (
                      SELECT value FROM json_each(?)
                  )
                LIMIT 1
                """,
                    (canonical(hashes).decode(),),
                ).fetchone()
                is not None
            )

    def plan(self, sid: str, plan_id: str | None = None) -> dict[str, Any]:
        with closing(self._connect()) as db:
            if plan_id is not None:
                row = db.execute(
                    "SELECT * FROM plugin_plans WHERE session_id=? AND plan_id=?",
                    (sid, plan_id),
                ).fetchone()
            else:
                row = db.execute(
                    "SELECT * FROM plugin_plans WHERE session_id=? "
                    "ORDER BY created_at DESC, rowid DESC LIMIT 1",
                    (sid,),
                ).fetchone()
            return self._plan(row)

    def launch(self, sid: str, plan_id: str, sha: str) -> dict[str, Any]:
        self.reconcile()
        with self._write() as db:
            self._plan(
                db.execute(
                    "SELECT * FROM plugin_plans WHERE session_id=? AND plan_id=?",
                    (sid, plan_id),
                ).fetchone(),
                plan_id,
                sha,
            )
            previous = db.execute(
                "SELECT * FROM plugin_runs WHERE plan_id=?",
                (plan_id,),
            ).fetchone()
            if previous is not None:
                return dict(previous)
            if db.execute(
                "SELECT 1 FROM plugin_runs WHERE status IN ('queued', 'running')",
            ).fetchone():
                raise OperationBlocked("local_job_already_active")
            rid = uuid4().hex
            moment = time.time()
            db.execute(
                "INSERT INTO plugin_runs VALUES (?, ?, ?, ?, 'queued', NULL, ?, "
                "NULL, NULL, ?, ?, NULL)",
                (rid, sid, plan_id, sha, "{}", moment, moment),
            )
            try:
                process = _spawn_worker(self.workspace, rid)
            except OSError:
                db.execute(
                    "UPDATE plugin_runs SET status='failed', "
                    "error_text=?, finished_at=? "
                    "WHERE run_id=?",
                    ("worker_launch_failed", time.time(), rid),
                )
            else:
                try:
                    created = psutil.Process(process.pid).create_time()
                except psutil.Error:
                    created = None
                threading.Thread(target=process.wait, daemon=True).start()
                db.execute(
                    "UPDATE plugin_runs SET pid=?, stage_json=? WHERE run_id=?",
                    (process.pid, json.dumps({"process_created_at": created}), rid),
                )
            row = db.execute(
                "SELECT * FROM plugin_runs WHERE run_id=?", (rid,)
            ).fetchone()
            return dict(row)

    def run(self, sid: str, rid: str) -> dict[str, Any]:
        self.reconcile()
        with closing(self._connect()) as db:
            row = db.execute(
                "SELECT * FROM plugin_runs WHERE session_id=? AND run_id=?",
                (sid, rid),
            ).fetchone()
        if row is None:
            raise OperationBlocked("unknown_run")
        return dict(row)

    def start(self, rid: str) -> dict[str, Any]:
        with self._write() as db:
            run = db.execute(
                "SELECT * FROM plugin_runs WHERE run_id=? AND pid=? "
                "AND status='queued'",
                (rid, os.getpid()),
            ).fetchone()
            if run is None:
                raise OperationBlocked("worker_ownership_unavailable")
            plan = self._plan(
                db.execute(
                    "SELECT * FROM plugin_plans WHERE session_id=? AND plan_id=?",
                    (run["session_id"], run["plan_id"]),
                ).fetchone(),
                run["plan_id"],
                run["plan_sha256"],
            )
            db.execute(
                "UPDATE plugin_runs SET status='running', updated_at=? WHERE run_id=?",
                (time.time(), rid),
            )
        return plan

    def stage(self, rid: str, **fields: Any) -> None:
        with self._write() as db:
            row = db.execute(
                "SELECT stage_json FROM plugin_runs "
                "WHERE run_id=? AND pid=? AND status='running'",
                (rid, os.getpid()),
            ).fetchone()
            if row is None:
                raise OperationBlocked("worker_ownership_unavailable")
            stage = {**json.loads(row["stage_json"]), **fields}
            db.execute(
                "UPDATE plugin_runs SET stage_json=?, updated_at=? WHERE run_id=?",
                (canonical(stage).decode(), time.time(), rid),
            )

    def finish(
        self,
        rid: str,
        *,
        receipt: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        status = "completed" if error is None else "failed"
        if receipt is not None and receipt.get("_terminal_status") == "awaiting_review":
            status, receipt = "awaiting_review", None
        record = canonical(receipt) if receipt is not None else None
        if record is not None and len(record) > 768 * 1024:
            raise ValueError("Receipt exceeds its storage budget")
        moment = time.time()
        with self._write() as db:
            db.execute(
                "UPDATE plugin_runs SET status=?, receipt_json=?, error_text=?, "
                "updated_at=?, finished_at=? WHERE run_id=? "
                "AND pid=? AND status='running'",
                (
                    status,
                    record.decode() if record else None,
                    error,
                    moment,
                    moment,
                    rid,
                    os.getpid(),
                ),
            )

    def reconcile(self) -> None:
        with self._write() as db:
            for row in db.execute(
                "SELECT run_id, pid, stage_json FROM plugin_runs "
                "WHERE status IN ('running', 'queued')",
            ).fetchall():
                if _worker_alive(
                    row["pid"],
                    json.loads(row["stage_json"]).get(
                        "process_created_at",
                    ),
                ):
                    continue
                now = time.time()
                db.execute(
                    "UPDATE plugin_runs SET status='interrupted', error_text=?, "
                    "updated_at=?, finished_at=? WHERE run_id=?",
                    ("worker_not_running", now, now, row["run_id"]),
                )


def _worker_alive(pid: int | None, created: float | None) -> bool:
    if not pid:
        return False
    try:
        process = psutil.Process(pid)
        return (
            created is None or created == process.create_time()
        ) and process.status() != psutil.STATUS_ZOMBIE
    except psutil.Error:
        return False


def _spawn_worker(workspace: Path, rid: str) -> subprocess.Popen:
    # Use the launching distribution even for a source checkout using a shared
    # interpreter. Isolated mode ignores inherited PYTHONPATH and user site data.
    source = Path(__file__).resolve().parents[2]
    roots = [str(source)]
    if source.name == "src" and (source.parent / "pyproject.toml").is_file():
        roots.append(str(source.parent))
    entrypoint = (
        f"import sys; sys.path[:0] = {roots!r}; "
        "from devbench.platform.plugin_jobs import worker_main; worker_main()"
    )
    env = {
        key: os.environ[key]
        for key in (
            "PATH",
            "HOME",
            "LANG",
            "LC_ALL",
            "TMPDIR",
            "DYLD_LIBRARY_PATH",
            "LD_LIBRARY_PATH",
        )
        if key in os.environ
    }
    env.update(
        {
            key: "1"
            for key in (
                "OPENBLAS_NUM_THREADS",
                "OMP_NUM_THREADS",
                "MKL_NUM_THREADS",
                "VECLIB_MAXIMUM_THREADS",
                "NUMBA_NUM_THREADS",
            )
        }
    )
    env["NUMBA_THREADING_LAYER"] = "workqueue"
    return subprocess.Popen(
        [sys.executable, "-I", "-B", "-c", entrypoint, str(workspace), rid],
        env=env,
        cwd=workspace,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def _timeout(_signum, _frame):
    raise TimeoutError("plugin_worker_deadline_exceeded")


def worker_main() -> None:
    from .plugin_mcp import run_frozen_plan

    workspace, rid = Path(sys.argv[1]), sys.argv[2]
    jobs = JobStore(workspace)
    plan = jobs.start(rid)
    signal.signal(signal.SIGALRM, _timeout)
    signal.alarm(MAX_SECONDS)
    try:
        receipt = run_frozen_plan(jobs, rid, plan)
    except OperationBlocked as error:
        jobs.finish(rid, error=error.state)
    except TimeoutError:
        jobs.finish(rid, error="plugin_worker_deadline_exceeded")
    except Exception as error:
        diagnostics = workspace / "runs" / rid
        diagnostics.mkdir(mode=0o700, parents=True, exist_ok=True)
        descriptor = os.open(
            diagnostics / "worker-error.log",
            os.O_CREAT | os.O_EXCL | os.O_WRONLY,
            0o600,
        )
        with os.fdopen(descriptor, "w") as stream:
            traceback.print_exc(file=stream)
        jobs.finish(rid, error=f"worker_failed_{type(error).__name__}")
    else:
        jobs.finish(rid, receipt=receipt)
    finally:
        signal.alarm(0)
