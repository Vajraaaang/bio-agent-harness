"""Durable local plan custody and isolated workers for the RNA plugin."""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import os
import sqlite3
import stat
import subprocess
import sys
import threading
import time
import traceback
from contextlib import closing, contextmanager
from contextvars import ContextVar
from functools import cached_property
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlsplit

import psutil

from devbench.engine.scientific_authority import verify_frozen_plan
from devbench.engine.workflow_operations import OperationBlocked
from devbench.engine.workspace import read_bounded_file
from devbench.study.permissions import require_protected_storage

ACTIVE = {"queued", "running"}
# Compatibility ceiling for registered kernels. The supervisor owns deadlines.
MAX_SECONDS = 72 * 60 * 60
MAX_DOCUMENT_BYTES = 256 * 1024


_STUDIO_APPROVAL = ContextVar("devbench_studio_approval", default=None)


@contextmanager
def studio_approval(
    session_id, plan_id, plan_sha256, *, actor_id, auth_method, rationale=""
):
    """Bind the Studio operator's exact confirmation to the checked launch handler."""
    identity = (session_id, plan_id, plan_sha256)
    grant = dict(
        actor_id=actor_id,
        surface="studio",
        auth_method=auth_method,
        rationale=rationale,
    )
    token = _STUDIO_APPROVAL.set((identity, grant))
    try:
        yield
    finally:
        _STUDIO_APPROVAL.reset(token)


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
    allow_data_execution: bool = False,
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
    if allow_data_execution or mounted_image_id is not None:
        launch.append("--allow-data-execution")
    if mounted_image_id is not None:
        launch += ["--mounted-image-id", mounted_image_id]
    origin = _studio_origin(workspace, offline_only=True)
    if origin is None and not allow_data_execution:
        try:
            if _public_handoff(workspace, sid):
                origin = _studio_origin(workspace)
        except (OSError, sqlite3.Error, ValueError, TypeError, OperationBlocked):
            # Unverified scope cannot authorize a link to an online Studio.
            pass
    url = (
        origin + "?" + urlencode({"session_id": sid, "view": view}) if origin else None
    )
    return {"studio_url": url, "launch_hint": shlex.join(launch)}


def _public_handoff(workspace: Path, sid: str) -> bool:
    """An online deep link is only suitable for a public, untainted session."""
    from .plugin_mcp import studio_job_readback

    with studio_job_readback(workspace) as db:
        if db is None:
            return False
        seen = False
        for row in db.execute("SELECT * FROM plugin_plans WHERE session_id=?", (sid,)):
            seen = True
            plan = JobStore._plan(row)
            # Pre-classification identity records carry no data or executable plan.
            if set(plan) == {"session_id", "plan_id"}:
                continue
            if JobStore.protected_inputs(db, plan):
                return False
        return seen


def _studio_origin(workspace: Path, *, offline_only: bool = False) -> str | None:
    from devbench.daemon.ipc import daemon_status

    try:
        daemon = daemon_status(workspace)
        if daemon.get("status") == "running" and daemon.get("input_root"):
            if offline_only and not daemon.get("offline_only"):
                return None
            return daemon["http_url"].rstrip("/") + "/"
    except (OSError, ValueError, RuntimeError):
        return None
    # Compatibility for live pre-daemon Studio servers.
    try:
        state = json.loads(read_bounded_file(workspace / "studio-server.json", 4096))
        target = urlsplit(state["url"])
        pid = state["pid"]
        if (
            state["workspace"] == str(workspace)
            and (not offline_only or state.get("offline_only") is True)
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
            return f"http://{target.netloc}/"
    except (OSError, ValueError, TypeError, KeyError):
        pass
    return None


def ensure_offline_studio(
    workspace: Path, input_root: Path, mounted_image_id: str | None
) -> str | None:
    """Make Studio available. The MCP handoff publishes its URL passively."""
    from devbench.daemon.ipc import DaemonError, daemon_status, start_daemon

    if not _studio_origin(workspace, offline_only=True):
        options = {"mounted_image_id": mounted_image_id} if mounted_image_id else {}
        try:
            owner = daemon_status(workspace)
            if owner.get("status") == "running" and not owner.get("offline_only"):
                return None
            start_daemon(
                workspace,
                input_root=input_root,
                offline_only=True,
                _mcp_bootstrap=True,
                **options,
            )
        except DaemonError:
            # Optional Studio startup cannot weaken a live owner's configuration
            # or prevent the legacy MCP handler from evaluating its own request.
            return None
    # The legacy caller interprets a returned origin as permission to open a browser.
    return None


def fastq_cpu_slots(plan):
    """The mounted count-model implementation requires two CPUs."""
    quantification_only = (
        plan.get("design", {}).get("parameters", {}).get("quantification_only", False)
    )
    return 1 if quantification_only is True else 2


def require_execution_policy(authority, plan, sha, protected, *, offline_only):
    if not protected:
        return
    if not offline_only:
        raise OperationBlocked("private_data_local_studio_only")
    if authority.plan_approval(plan["session_id"], plan["plan_id"], sha) is None:
        raise OperationBlocked("explicit_plan_approval_required")


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
        self._heartbeat_stop = threading.Event()

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

    def _connect(self, *, readonly: bool = False) -> sqlite3.Connection:
        mode = "ro" if readonly else "rw"
        db = sqlite3.connect(f"{self.path.as_uri()}?mode={mode}", uri=True, timeout=10)
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

    @staticmethod
    def _has_private_input(db, fingerprints):
        hashes = sorted({value for value in fingerprints if value is not None})
        if not hashes:
            return False
        # Scope includes the same H5AD, FASTQ inventory and lineage-root identities
        # delivered by existing MCP tools. Retained declarations are sticky.
        return (
            db.execute(
                """WITH wanted AS (SELECT value AS sha FROM json_each(?)),
            sources AS (
                SELECT p.plan_json AS plan, item.value AS item
                FROM plugin_plans p, json_each(p.plan_json,'$.profiles') item
                UNION ALL
                SELECT p.plan_json, item.value
                FROM plugin_plans p, json_each(p.plan_json,'$.fastq.inventory') item
                UNION ALL
                SELECT p.plan_json, json_object('sha256',item.value)
                FROM plugin_plans p, json_each(p.plan_json,'$.session_roots') item)
            SELECT 1 FROM sources WHERE json_extract(item,'$.sha256') IN (SELECT sha
                FROM wanted) AND (
                coalesce(json_extract(plan,'$.data_classification'),'unclassified')
                    NOT IN ('public','synthetic') OR
                coalesce(json_extract(item,'$.data_classification'),
                    json_extract(plan,'$.data_classification'),'unclassified')
                    NOT IN ('public','synthetic')) LIMIT 1""",
                (canonical(hashes).decode(),),
            ).fetchone()
            is not None
        )

    def has_private_input(self, fingerprints: set[str | None]) -> bool:
        with closing(self._connect(readonly=True)) as db:
            return self._has_private_input(db, fingerprints)

    @staticmethod
    def protected_inputs(db, plan):
        public = {"public", "synthetic"}
        classification = plan.get("data_classification")
        fastq = plan.get("fastq", {})
        rows = [*plan.get("profiles", ()), *fastq.get("inventory", ())]
        declarations = [*rows, *plan.get("input_declarations", ())]
        if (
            classification not in public
            or fastq.get("private") is True
            or any(
                item.get("clinical_metadata") is True
                or item.get("data_classification", classification) not in public
                for item in declarations
            )
        ):
            return True
        roots = {*plan.get("session_roots", ()), *(item.get("sha256") for item in rows)}
        return JobStore._has_private_input(db, roots)

    @contextmanager
    def frozen_policy(self, sid, plan_id, sha):
        """Fence custody changes until the caller finishes the admission decision."""
        with self._write() as db:
            row = db.execute(
                "SELECT * FROM plugin_plans WHERE session_id=? AND plan_id=?",
                (sid, plan_id),
            ).fetchone()
            try:
                plan = self._plan(row, plan_id, sha)
                verify_frozen_plan(plan, sid, plan_id, sha)
                protected = self.protected_inputs(db, plan)
            except (AttributeError, TypeError, ValueError, KeyError) as error:
                raise OperationBlocked("invalid_frozen_data_scope") from error
            yield plan, protected

    def plan(self, sid: str, plan_id: str | None = None) -> dict[str, Any]:
        with closing(self._connect(readonly=True)) as db:
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

    @cached_property
    def authority(self):
        from devbench.engine.workflow_operations import (
            LEDGER_NAME,
            WorkflowOperationLedger,
        )

        ledger = WorkflowOperationLedger(
            self.workspace / "methods" / LEDGER_NAME, scientific=True
        )
        authority = ledger.scientific
        authority.initialize_daemon()
        return authority

    def approve(
        self,
        sid,
        plan_id,
        sha,
        *,
        actor_id,
        surface,
        auth_method,
        rationale="",
        require_public=False,
    ):
        return self.authority.record_plan_approval(
            session_id=sid,
            plan_id=plan_id,
            plan_sha256=sha,
            frozen_plan=self.plan(sid, plan_id),
            actor_id=actor_id,
            surface=surface,
            auth_method=auth_method,
            rationale=rationale,
            public_plan_policy=self.frozen_policy(sid, plan_id, sha)
            if require_public
            else None,
        )

    def unsettled_legacy_runs(self):
        """Original workers write these rows until their process exits."""
        with closing(self._connect()) as db:
            for row in db.execute(
                "SELECT * FROM plugin_runs WHERE status IN "
                "('running','queued','interrupted') AND "
                "coalesce(json_extract(stage_json,'$.daemon_managed'),0)=0"
            ):
                yield dict(row)

    def legacy_run(self, sid, plan_id):
        with closing(self._connect()) as db:
            row = db.execute(
                "SELECT * FROM plugin_runs WHERE session_id=? AND plan_id=?",
                (sid, plan_id),
            ).fetchone()
        if row is not None and not json.loads(row["stage_json"]).get("daemon_managed"):
            return dict(row)
        return None

    def launch(self, sid: str, plan_id: str, sha: str) -> dict[str, Any]:
        from devbench.daemon import ipc
        from devbench.daemon.supervisor import local_supervisor

        plan = self.plan(sid, plan_id)
        if not hmac.compare_digest(digest(plan), sha):
            raise OperationBlocked("plan_identity_mismatch")
        consent = _STUDIO_APPROVAL.get()
        if consent is not None:
            identity, grant = consent
            if identity != (sid, plan_id, sha):
                raise OperationBlocked("plan_identity_mismatch")
            self.approve(sid, plan_id, sha, **grant)
        if previous := self.legacy_run(sid, plan_id):
            return previous
        if supervisor := local_supervisor(self.workspace):
            return supervisor.submit(sid, plan_id, sha)
        if ipc.daemon_status(self.workspace).get("status") != "running":
            # Starting a reviewed job alone must not take an interactive Studio lock.
            ipc.start_daemon(self.workspace, offline_only=True, defer_studio=True)
        return ipc.request(
            self.workspace, "submit", session_id=sid, plan_id=plan_id, plan_sha256=sha
        )

    @staticmethod
    def visible_run(run):
        fields = dict(run)
        if "cleanup_state" not in fields:
            legacy_cleanup = json.loads(fields["stage_json"]).get("cleanup_state")
            if legacy_cleanup is not None:
                fields["cleanup_state"] = legacy_cleanup
        if fields["status"] == "finishing":
            fields.update(status="running", receipt_json=None, finished_at=None)
        return fields

    def project_run(self, run):
        """Replay the current authority row into the legacy one-row-per-plan view."""
        fields = self.visible_run(run)
        if fields["generation"] == "legacy" and fields["cleanup_state"] != "confirmed":
            return
        stage = json.loads(fields["stage_json"])
        stage.update(
            daemon_managed=True,
            process_created_at=fields["create_time"],
            cleanup_state=fields["cleanup_state"],
        )
        fields["stage_json"] = canonical(stage).decode()
        with self._write() as db:
            db.execute(
                """INSERT INTO plugin_runs VALUES (
                    :run_id,:session_id,:plan_id,:plan_sha256,:status,:pid,:stage_json,
                    :receipt_json,:error_text,:started_at,:updated_at,:finished_at)
                ON CONFLICT(plan_id) DO UPDATE SET
                    run_id=excluded.run_id, status=excluded.status, pid=excluded.pid,
                    stage_json=excluded.stage_json, receipt_json=excluded.receipt_json,
                    error_text=excluded.error_text, started_at=excluded.started_at,
                    updated_at=excluded.updated_at, finished_at=excluded.finished_at
                WHERE excluded.started_at>plugin_runs.started_at OR
                    (excluded.run_id=plugin_runs.run_id AND
                     excluded.updated_at>=plugin_runs.updated_at)""",
                fields,
            )

    def managed_run(self, rid):
        row = self.authority.daemon_read(
            lambda db: db.execute(
                "SELECT * FROM scientific_daemon_runs WHERE run_id=?", (rid,)
            ).fetchone()
        )
        return dict(row) if row else None

    def run(self, sid: str, rid: str) -> dict[str, Any]:
        if self.managed_run(rid) is None:
            self.reconcile()
        return self.read_run(sid, rid)

    def _committed_run(self, sid: str, rid: str) -> dict[str, Any]:
        from devbench.engine.workflow_operations import (
            LEDGER_NAME,
            WorkflowOperationLedger,
        )

        def managed(db):
            if db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' "
                "AND name='scientific_daemon_runs'"
            ).fetchone():
                return db.execute(
                    "SELECT * FROM scientific_daemon_runs WHERE run_id=?", (rid,)
                ).fetchone()
            return None

        path = self.workspace / "methods" / LEDGER_NAME
        row = None
        if path.exists() or path.is_symlink():
            row = WorkflowOperationLedger(path, readonly=True)._read(managed)
        if row is None:
            with closing(self._connect(readonly=True)) as db:
                row = db.execute(
                    "SELECT * FROM plugin_runs WHERE session_id=? AND run_id=?",
                    (sid, rid),
                ).fetchone()
        if row is None or row["session_id"] != sid:
            raise OperationBlocked("unknown_run")
        return dict(row)

    def read_run(self, sid: str, rid: str) -> dict[str, Any]:
        """Observe a managed or legacy run without initializing or reconciling jobs."""
        row = self._committed_run(sid, rid)
        if "create_time" in row:
            created = row["create_time"]
        else:
            stage = json.loads(row["stage_json"])
            if not stage.get("daemon_managed"):
                return self.visible_run({**row, **_legacy_interruption(row)})
            created = stage.get("process_created_at")
        fields = self.visible_run(row)
        # A queued reservation survives a restart and has no worker until dispatch.
        expects_worker = fields["status"] in {"starting", "running", "cancelling"}
        if expects_worker and _orphaned_worker(self.workspace, row["pid"], created):
            # The supervisor can finish cleanup and exit between the first snapshot
            # and the liveness probe. Prefer its newer committed state.
            fresh = self._committed_run(sid, rid)
            if fresh != row:
                return self.visible_run(fresh)
            fields.update(
                status="interrupted",
                error_text="worker_not_running",
                cleanup_state="unconfirmed",
                receipt_json=None,
                finished_at=None,
            )
        return fields

    @staticmethod
    def _owned(db, rid):
        row = db.execute(
            "SELECT * FROM scientific_daemon_runs WHERE run_id=? AND pid=? "
            "AND status='running'",
            (rid, os.getpid()),
        ).fetchone()
        if row is None or row["create_time"] != psutil.Process().create_time():
            raise OperationBlocked("worker_ownership_unavailable")
        return dict(row)

    def start(self, rid: str) -> dict[str, Any]:
        # The parent holds BEGIN IMMEDIATE across spawn and process identification.
        # An uncommitted/rolled-back launch can never start scientific work.
        with self.authority.daemon_transaction() as db:
            row = self._owned(db, rid)
            with self.frozen_policy(
                row["session_id"], row["plan_id"], row["plan_sha256"]
            ) as (plan, protected):
                # Check once more in the admitted process before any input reads.
                require_execution_policy(
                    self.authority,
                    plan,
                    row["plan_sha256"],
                    protected,
                    offline_only=json.loads(row["stage_json"]).get("_offline_only")
                    is True,
                )
        if row["resume_from_run_id"]:
            self.resume_checkpoint(rid)  # Check again inside the admitted worker.
        self._start_heartbeat(row)
        return plan

    def remaining_deadline_sec(self, rid):
        """The positive remaining wall time for this identity-fenced worker."""
        with self.authority.daemon_transaction() as db:
            row = self._owned(db, rid)
        remaining = row["dispatched_at"] + row["deadline_sec"] - time.time()
        if remaining <= 0:
            raise TimeoutError("plugin_worker_deadline_exceeded")
        return math.ceil(remaining)

    def nextflow_resources(self, rid):
        """Supply the native runtime with the exact reservation owned by this worker."""
        with self.authority.daemon_transaction() as db:
            row = self._owned(db, rid)
        plan = self.plan(row["session_id"], row["plan_id"])
        verify_frozen_plan(plan, row["session_id"], row["plan_id"], row["plan_sha256"])
        slots = json.loads(row["stage_json"]).get("_cpu_slots")
        memory = row["memory_mb"]
        if (
            plan.get("operation") != "qc-salmon"
            or row["pool"] != "cpu"
            or slots != fastq_cpu_slots(plan)
            or memory % 1024
            or not 1024 <= memory <= 1024**2
        ):
            raise OperationBlocked("nextflow_reservation_mismatch")
        return {"cpus": slots, "memory_gb": memory // 1024}

    def bind_nextflow_runtime(self, rid, attempt):
        from devbench.daemon.supervisor import nextflow_docker_binding

        # Native container access requires the original reviewed attempt directory.
        attempt = Path(attempt)
        with self.authority.daemon_transaction() as db:
            row = self._owned(db, rid)
        if (
            json.loads(row["stage_json"]).get("nextflow_attempt") != str(attempt)
            or attempt.parent != self.workspace / "runs" / rid / "nextflow"
            or directory(attempt) != attempt
        ):
            raise OperationBlocked("nextflow_attempt_identity_unverified")
        binding = nextflow_docker_binding(attempt)
        self.stage(rid, nextflow_docker=binding)
        return binding

    def _start_heartbeat(self, row):
        from devbench.daemon.supervisor import worker_heartbeat

        interval = json.loads(row["stage_json"]).get("_heartbeat_sec", 10)
        worker_heartbeat(self.workspace, row)

        def pulse():
            while not self._heartbeat_stop.wait(interval):
                try:
                    worker_heartbeat(self.workspace, row)
                except OSError:
                    # The supervisor reports a stale pulse without killing this worker.
                    continue

        threading.Thread(
            target=pulse, name="devbench-worker-heartbeat", daemon=True
        ).start()

    def stage(self, rid: str, **fields: Any) -> None:
        from devbench.daemon.supervisor import observe_children, write_progress

        if any(key.startswith("_") for key in fields):
            raise ValueError("Worker cannot set reserved progress fields")
        with self.authority.daemon_transaction() as db:
            row = self._owned(db, rid)
            stage = {**json.loads(row["stage_json"]), **fields}
            observe_children(row, stage)
            encoded = canonical(stage)
            if len(encoded) > MAX_DOCUMENT_BYTES:
                raise ValueError("Stage exceeds its size limit")
            row.update(stage_json=encoded.decode(), updated_at=time.time())
            db.execute(
                "UPDATE scientific_daemon_runs SET stage_json=?,updated_at=? "
                "WHERE run_id=?",
                (row["stage_json"], row["updated_at"], rid),
            )
            write_progress(self.workspace, row)
        self.project_run(row)

    def checkpoint(self, rid, *, step, state):
        from devbench.daemon.supervisor import save_checkpoint

        with self.authority.daemon_transaction() as db:
            row = self._owned(db, rid)
            manifest = save_checkpoint(self.workspace, row, step, state)
            db.execute(
                "UPDATE scientific_daemon_runs SET checkpoint_sha256=?,updated_at=? "
                "WHERE run_id=?",
                (manifest["sha256"], time.time(), rid),
            )
        return manifest

    def resume_checkpoint(self, rid):
        from devbench.daemon.supervisor import verified_checkpoint

        with self.authority.daemon_transaction() as db:
            row = self._owned(db, rid)
            if not row["resume_from_run_id"]:
                return None
            previous = db.execute(
                "SELECT * FROM scientific_daemon_runs WHERE run_id=?",
                (row["resume_from_run_id"],),
            ).fetchone()
            return verified_checkpoint(
                self.workspace,
                previous,
                row,
                publication_retry=row["run_id"] == row["resume_from_run_id"],
            )

    def finish(
        self,
        rid: str,
        *,
        receipt: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        from devbench.daemon.supervisor import write_progress

        self._heartbeat_stop.set()
        status = "completed" if error is None else "failed"
        if receipt is not None and receipt.get("_terminal_status") == "awaiting_review":
            status, receipt = "awaiting_review", None
        record = canonical(receipt).decode() if receipt is not None else None
        if record is not None and len(record) > 768 * 1024:
            raise ValueError("Receipt exceeds its storage budget")
        moment = time.time()
        with self.authority.daemon_transaction() as db:
            row = self._owned(db, rid)
            stage = json.loads(row["stage_json"])
            stage["_worker_terminal_status"] = status
            stage_json = canonical(stage).decode()
            row.update(
                status="finishing",
                stage_json=stage_json,
                receipt_json=record,
                error_text=error,
                updated_at=moment,
                finished_at=moment,
            )
            db.execute(
                "UPDATE scientific_daemon_runs SET status='finishing',stage_json=?,"
                "receipt_json=?,error_text=?,updated_at=?,finished_at=? WHERE run_id=?",
                (stage_json, record, error, moment, moment, rid),
            )
            write_progress(self.workspace, row)
        self.project_run(row)

    def reconcile(self) -> None:
        """Only reconcile unmanaged rows from installations predating the daemon."""
        with self._write() as db:
            for row in db.execute(
                "SELECT run_id, pid, stage_json, status FROM plugin_runs "
                "WHERE status IN ('running','queued') AND "
                "coalesce(json_extract(stage_json,'$.daemon_managed'),0)=0"
            ).fetchall():
                update = _legacy_interruption(row)
                if not update:
                    continue
                db.execute(
                    "UPDATE plugin_runs SET status=:status, error_text=:error_text, "
                    "stage_json=:stage_json,updated_at=:now,finished_at=:now "
                    "WHERE run_id=:run_id",
                    {**update, "now": time.time(), "run_id": row["run_id"]},
                )


def _legacy_interruption(row) -> dict[str, str]:
    if row["status"] not in ACTIVE:
        return {}
    stage = json.loads(row["stage_json"])
    if stage.get("daemon_managed") or _worker_alive(
        row["pid"], stage.get("process_created_at")
    ):
        return {}
    # A missing worker cannot confirm that descendants and containers stopped.
    return {
        "status": "interrupted",
        "error_text": "worker_not_running",
        "stage_json": canonical({**stage, "cleanup_state": "unconfirmed"}).decode(),
    }


def _orphaned_worker(workspace: Path, pid: int | None, created: float | None) -> bool:
    """A live supervisor owns state transitions even while a worker exits."""
    from devbench.daemon.ipc import daemon_status
    from devbench.daemon.supervisor import local_supervisor

    if local_supervisor(workspace) is not None or _worker_alive(pid, created):
        return False
    # Read the lifetime lock and PID generation. No RPC, startup or reconciliation.
    return daemon_status(workspace)["status"] == "stopped"


def _worker_alive(pid: int | None, created: float | None) -> bool:
    if not pid:
        return False
    try:
        process = psutil.Process(pid)
        return (
            created is None or created == process.create_time()
        ) and process.status() != psutil.STATUS_ZOMBIE
    except psutil.AccessDenied:
        # An inaccessible process cannot be recorded as lost.
        return True
    except psutil.NoSuchProcess:
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


def _input_stamp(path):
    info = path.stat(follow_symlinks=False)
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise OperationBlocked("input_snapshot_changed")
    return [info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns]


def _verify_input_prefix(jobs, rid, plan, restored):
    """Resume a multi-file snapshot check without rehashing identity-stable files."""
    from devbench.core._files import hash_bounded_file

    root = Path(plan["input_root"])
    verified = {}
    cached = restored.get("verified", {}) if restored else {}
    for index, profile in enumerate(plan.get("profiles", ())):
        if not profile.get("sha256") or not profile.get("source"):
            continue
        path = Path(profile["source"])
        if path.resolve() != path or not (
            path.is_relative_to(root) or plan.get("allow_external_input_files") is True
        ):
            raise OperationBlocked("input_snapshot_changed")
        if path.is_dir():  # The registered workflow validates structured stores.
            continue
        stamp = _input_stamp(path)
        item = {"stamp": stamp, "sha256": profile["sha256"]}
        key = str(index)
        if cached.get(key) != item and (
            hash_bounded_file(path, stamp[2]) != profile["sha256"]
            or _input_stamp(path) != stamp
        ):
            raise OperationBlocked("input_snapshot_changed")
        verified[key] = item
        jobs.checkpoint(
            rid,
            step="verify_inputs",
            state={
                "worker_protocol": "devbench.plugin-worker/1",
                "phase": "verified_inputs",
                "verified": verified,
            },
        )


def _verify_retained_receipt(jobs, rid, plan, receipt):
    from devbench.analysis.evidence_access import EvidenceBroker

    from . import plugin_mcp
    from .plugin_graph import verify_registered_plugin_result
    from .plugin_registered_method import verify_registered_method_result
    from .plugin_rna_solvers import LOCAL_OPERATIONS

    if (
        receipt.get("run_id") != rid
        or receipt.get("session_id") != plan["session_id"]
        or receipt.get("clinical_release_allowed") is not False
    ):
        raise OperationBlocked("receipt_identity_mismatch")
    plugin_mcp._verify_replay(
        jobs.workspace / "runs" / rid / "replay_bundle",
        digest(plan),
        receipt,
    )
    if plan["operation"] in LOCAL_OPERATIONS:
        plugin_mcp._verify_solver_result(jobs.workspace, rid, receipt)
    if plan["operation"] == "registered_exploratory":
        verify_registered_method_result(jobs.workspace, rid, plan, receipt)
    if plan.get("graph_runtime") is not None:
        verify_registered_plugin_result(jobs.workspace, rid, plan, receipt)
    ledger = jobs.authority.ledger
    broker = EvidenceBroker.open_existing(ledger.scientific)
    source = "plugin-receipt-" + rid
    retained = ledger._read(lambda db: broker._source(db, source))
    if retained["content_sha256"] != digest(receipt):
        raise OperationBlocked("receipt_identity_mismatch")
    guard = plugin_mcp._adaptive_receipt_guard(ledger, plan, receipt)
    if guard is not None:
        ledger._read(guard)
    return receipt


def verify_native_resume_plan(plan, state):
    params = plan.get("design", {}).get("parameters", {})
    steps = params.get("steps")
    if (
        plan.get("operation") != "single_cell"
        or not isinstance(steps, list)
        or not 1 <= len(steps) <= 16
        or params.get("composition") is not None
        or params.get("pseudobulk") is not None
        or state.get("lineage_id") != plan.get("session_id")
        or state.get("plan_id") != plan.get("plan_id")
        or state.get("plan_sha256") != digest(plan)
    ):
        raise OperationBlocked("native_step_checkpoint_mismatch")


def _compute_or_restore(jobs, rid, plan):
    from .plugin_mcp import run_frozen_plan

    checkpoint = jobs.resume_checkpoint(rid)
    restored = checkpoint["state"] if checkpoint else None
    if restored is not None:
        if restored.get("worker_protocol") != "devbench.plugin-worker/1":
            raise OperationBlocked("unsupported_worker_checkpoint")
        if restored["phase"] == "publish_receipt":
            return _verify_retained_receipt(jobs, rid, plan, restored["receipt"])
        if restored["phase"] == "native_steps":
            verify_native_resume_plan(plan, restored)
        elif restored["phase"] != "verified_inputs":
            raise OperationBlocked("scientific_step_replay_unverified")
    if not restored or restored["phase"] != "native_steps":
        _verify_input_prefix(jobs, rid, plan, restored)
        jobs.checkpoint(
            rid,
            step="scientific_dispatch",
            state={
                "worker_protocol": "devbench.plugin-worker/1",
                "phase": "scientific_dispatch",
            },
        )
    # The native adapter independently verifies and consumes its accepted prefix.
    receipt = run_frozen_plan(jobs, rid, plan)
    if receipt.get("replay_bundle_sha256") and receipt.get("run_id") == rid:
        jobs.checkpoint(
            rid,
            step="publish_receipt",
            state={
                "worker_protocol": "devbench.plugin-worker/1",
                "phase": "publish_receipt",
                "receipt": receipt,
            },
        )
    return receipt


def worker_main() -> None:
    workspace, rid = Path(sys.argv[1]), sys.argv[2]
    jobs = JobStore(workspace)
    try:
        plan = jobs.start(rid)
        receipt = _compute_or_restore(jobs, rid, plan)
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
