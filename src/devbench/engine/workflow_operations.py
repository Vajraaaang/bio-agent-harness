"""Durable dispatch reservations, not exactly-once external execution.

Only bounded public metadata and artifact references belong here. A surviving
reservation without a retained result blocks dispatch until explicit external
reconciliation. Cancellation prevents future dispatch; it does not kill jobs.
"""

from __future__ import annotations

import fcntl
import json
import math
import os
import random
import sqlite3
import stat
import time
from collections.abc import Callable
from contextlib import closing, contextmanager
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import TYPE_CHECKING, Any, TypeVar

from devbench.analysis.codex_job_policy import (
    JOB_POLICY_FORMAT,
    validate_job_capture,
    validate_job_limits,
    validate_job_policy,
)
from devbench.core._hashing import utf8_sha256 as _digest
from devbench.core._hashing import workflow_json as _json
from devbench.engine import dispatch_deadline
from devbench.study.model_telemetry import normalize_usage

from .method_executor import _path

if TYPE_CHECKING:
    from devbench.analysis.codex_signedin import SignedInCodexInvestigationBackend
    from devbench.analysis.rna_local_interactive import LocalInteractiveCodexBackend
    from devbench.analysis.rna_mediated_backend import MediatedInvestigationBackend


LEDGER_NAME = "rna-workflow-operations.sqlite"
LOCAL_INTERACTIVE_CODEX = "local_interactive_codex"
LOCAL_INTERACTIVE_PURPOSES = frozenset({"adaptive_controller", "adaptive_termination"})
_ReadResult = TypeVar("_ReadResult")


class OperationBlocked(RuntimeError):
    def __init__(self, state: str):
        self.state = state
        super().__init__(f"Workflow operation blocked: {state}")


class OperationConflict(RuntimeError):
    """Input identity or retained evidence disagrees with the ledger."""


def _sqlite_busy(error):
    # SQLite's extended error codes include the primary result in the low byte.
    return (
        isinstance(error, sqlite3.OperationalError)
        and getattr(error, "sqlite_errorcode", 0) & 0xFF == sqlite3.SQLITE_BUSY
    )


def _identity(workflow_id: str, node: str, attempt: int) -> tuple[str, str, int]:
    if any(
        type(item) is not str or not item.strip() or len(item.encode("utf-8")) > 256
        for item in (workflow_id, node)
    ):
        raise ValueError(
            "Workflow and node identities must be bounded nonempty strings"
        )
    if type(attempt) is not int or not 0 <= attempt <= 2147483647:
        raise ValueError("Logical attempt must be a nonnegative integer")
    return workflow_id, node, attempt


def model_reservation_ref(campaign_id: str, node: str, attempt: int) -> str:
    """Stable public reference; computing it grants neither budget nor execution."""
    return "model:" + _digest(_json(list(_identity(campaign_id, node, attempt))))


@dataclass(frozen=True)
class ModelCallReservation:
    campaign_id: str
    node: str
    attempt: int
    ref: str
    status: str
    purpose: str
    child_id: str | None
    request_digest: str
    policy_digest: str
    capability_digest: str
    _request_json: str
    _limits_json: str

    @property
    def request(self):
        return json.loads(self._request_json)

    @property
    def limits(self):
        return json.loads(self._limits_json)


_MODEL_COLUMNS = {
    "model_campaigns": [
        "campaign_id",
        "schema_version",
        "session_id",
        "status",
        "reason",
        "policy_json",
        "policy_digest",
        "capability_json",
        "capability_digest",
        "identity_json",
        "identity_digest",
        "created_at",
    ],
    "model_reservations": [
        "campaign_id",
        "node",
        "attempt",
        "schema_version",
        "ref",
        "status",
        "purpose",
        "child_id",
        "request_json",
        "request_digest",
        "limits_json",
        "limits_digest",
        "capture_json",
        "capture_digest",
        "error_type",
        "charge_basis",
    ],
    "investigations": [
        "campaign_id",
        "schema_version",
        "fork_id",
        "stage",
        "request_json",
        "request_digest",
    ],
}


def _retained_json(encoded, digest):
    try:
        value = json.loads(encoded)
        if _json(value) != encoded or _digest(encoded) != digest:
            raise ValueError("Payload integrity mismatch")
        return value
    except (ValueError, TypeError, RecursionError) as exc:
        raise OperationConflict(
            "Retained model budget record failed integrity validation"
        ) from exc


def _integer(value, name, *, minimum=0, maximum=1_000_000_000):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"Invalid model budget {name}")
    return value


def _seconds(value, name):
    if (
        type(value) not in (int, float)
        or not math.isfinite(value)
        or not 0 <= value <= 86400
    ):
        raise ValueError(f"Invalid model budget {name}")
    return value


def _hash(value):
    if (
        type(value) is not str
        or len(value) != 64
        or any(char not in "0123456789abcdef" for char in value)
    ):
        raise ValueError("Model budget identity must be a SHA-256 digest")
    return value


def _policy(value):
    _json(value)
    if type(value) is dict and value.get("format") == JOB_POLICY_FORMAT:
        return validate_job_policy(value)
    fields = {
        "format",
        "kind",
        "max_total_tokens",
        "soft_token_target",
        "max_calls",
        "max_total_seconds",
        "max_context_bytes",
        "memory_mb",
        "max_children",
        "reconciliation",
    }
    if (
        type(value) is not dict
        or set(value) != fields
        or value["format"] != "devbench.model-budget-policy/1"
    ):
        raise ValueError("Invalid model budget policy schema")
    kind = value["kind"]
    if kind not in (
        "hard_total_tokens",
        "bounded_observed_tokens",
        LOCAL_INTERACTIVE_CODEX,
    ):
        raise ValueError("Unknown model budget policy")
    token_field = (
        "max_total_tokens" if kind == "hard_total_tokens" else "soft_token_target"
    )
    other_field = (
        "soft_token_target" if kind == "hard_total_tokens" else "max_total_tokens"
    )
    _integer(value[token_field], token_field, minimum=1)
    if value[other_field] is not None:
        raise ValueError("Model budget policies cannot silently fall back")
    _integer(value["max_calls"], "max_calls", minimum=1, maximum=1024)
    if not _seconds(value["max_total_seconds"], "max_total_seconds"):
        raise ValueError("Model campaign requires a positive time bound")
    _integer(
        value["max_context_bytes"], "max_context_bytes", minimum=1, maximum=1_000_000
    )
    _integer(value["memory_mb"], "memory_mb", minimum=1, maximum=4096)
    _integer(value["max_children"], "max_children", maximum=2)
    if kind == LOCAL_INTERACTIVE_CODEX and value["max_children"]:
        raise ValueError(
            "Local interactive Codex cannot allocate investigation children"
        )
    reserve = value["reconciliation"]
    if type(reserve) is not dict or set(reserve) != {
        "tokens",
        "calls",
        "seconds",
        "context_bytes",
    }:
        raise ValueError("Invalid final parent reservation")
    for key in ("tokens", "calls", "context_bytes"):
        _integer(reserve[key], "reconciliation " + key, minimum=1)
    if (
        not _seconds(reserve["seconds"], "reconciliation seconds")
        or reserve["tokens"] > value[token_field]
        or reserve["calls"] > value["max_calls"]
        or reserve["seconds"] > value["max_total_seconds"]
        or reserve["context_bytes"] > value["max_context_bytes"]
    ):
        raise ValueError("Final parent reservation exceeds campaign")
    return json.loads(_json(value))


def _limits(value):
    if type(value) is dict and "jobs" in value:
        return validate_job_limits(value)
    if type(value) is not dict or set(value) != {
        "tokens",
        "calls",
        "seconds",
        "context_bytes",
        "memory_mb",
    }:
        raise ValueError("Invalid model call limits schema")
    for key in ("tokens", "calls", "context_bytes", "memory_mb"):
        _integer(value[key], key, minimum=1)
    if not _seconds(value["seconds"], "seconds"):
        raise ValueError("Model call requires a positive time bound")
    return json.loads(_json(value))


def _protected_allocations(policy, identity):
    allocations = identity.get("protected_parent_allocations", {})
    if type(allocations) is not dict or len(allocations) > 16:
        raise ValueError("Protected parent stages must be a bounded allocation map")
    for stage, allocation in allocations.items():
        _identity("parent-stage", stage, 0)
        _limits(allocation)
        if policy["kind"] == "generation_jobs":
            validate_job_limits(allocation)
            if (
                allocation["context_bytes"] > policy["max_context_bytes"]
                or allocation["output_bytes"] > policy["max_output_bytes"]
            ):
                raise ValueError("Protected parent stage exceeds generation job policy")
            continue
        if (
            allocation["context_bytes"] > policy["max_context_bytes"]
            or allocation["memory_mb"] > policy["memory_mb"]
        ):
            raise ValueError("Protected parent stage exceeds operational policy")
    for key in (
        ("jobs", "seconds")
        if policy["kind"] == "generation_jobs"
        else ("tokens", "calls", "seconds")
    ):
        if (
            sum(value[key] for value in allocations.values())
            > policy["reconciliation"][key]
        ):
            raise ValueError(
                "Protected parent stages exceed the retained parent reserve"
            )
    if (
        max((value["context_bytes"] for value in allocations.values()), default=0)
        > policy["reconciliation"]["context_bytes"]
    ):
        raise ValueError("Protected parent context exceeds its retained reserve")
    return allocations


def _token_floor(usage):
    return normalize_usage(usage["native_usage"])["native_usage"][
        "observed_token_lower_bound"
    ]


def _capture_progress(previous, current):
    if previous is None:
        return
    if (
        previous["launch_confirmed"]
        and not current["launch_confirmed"]
        or not set(previous["native_session_ids"]).issubset(
            current["native_session_ids"]
        )
        or current["actual_calls"] < previous["actual_calls"]
        or current["elapsed_seconds"] < previous["elapsed_seconds"]
        or _token_floor(current["usage"]) < _token_floor(previous["usage"])
    ):
        raise OperationConflict("Reconciliation cannot erase observed model work")
    old_native, new_native = (
        previous["usage"]["native_usage"],
        current["usage"]["native_usage"],
    )
    old_phases = old_native.get("phase_usage", [old_native])
    new_phases = new_native.get("phase_usage", [new_native])
    if len(new_phases) < len(old_phases):
        raise OperationConflict(
            "Reconciliation cannot discard observed accounting phases"
        )
    for old, new in zip(old_phases, new_phases, strict=False):
        old_usage, new_usage = normalize_usage(old), normalize_usage(new)
        for key in ("input_tokens", "output_tokens"):
            observed = old_usage["native_usage"].get(key)
            later = new_usage["native_usage"].get(key)
            if (
                type(observed) is int
                and observed >= 0
                and (type(later) is not int or later < observed)
            ):
                raise OperationConflict(
                    "Reconciliation cannot lower observed primary usage"
                )
        for key in (
            "normalized_total_tokens",
            "input_tokens",
            "output_tokens",
            "cached_input_tokens",
            "reasoning_tokens",
        ):
            observed, later = old_usage[key], new_usage[key]
            if observed is not None and (later is None or later < observed):
                raise OperationConflict("Reconciliation cannot downgrade known usage")


class WorkflowOperationLedger:
    def __init__(
        self,
        path: str | Path,
        *,
        readonly: bool = False,
        scientific: bool = False,
        inert: bool = False,
    ):
        from .scientific_authority import install_schema, require_patched_wal

        if type(scientific) is not bool:
            raise ValueError("Scientific migration requires an explicit Boolean")
        self.path = _path(path)
        if type(inert) is not bool or inert and not readonly:
            raise ValueError("Inert ledger inspection requires readonly=True")
        self.inert = inert
        self._inert_state = None
        self.readonly = readonly
        self._scientific_requested = scientific
        self._scientific_enabled = False
        if scientific:
            require_patched_wal()
        self._mediated_backends: dict[str, MediatedInvestigationBackend] = {}
        self._job_backends: dict[str, SignedInCodexInvestigationBackend] = {}
        self._local_interactive_backends: dict[str, LocalInteractiveCodexBackend] = {}
        self.trace_observer = None
        self._transaction_lock = RLock()
        self._observer_scope_lock = RLock()
        existing = self._prepare_storage()
        with self._database() as connection:
            if existing:
                self._validate_existing_schema(connection)
            else:
                self._create_initial_schema(connection)
        if scientific and not self._scientific_enabled:
            self._writable()
            with self._database() as connection:
                if connection.execute("PRAGMA journal_mode=WAL").fetchone()[0] != "wal":
                    raise OperationConflict("Could not activate scientific WAL")
                connection.execute("BEGIN IMMEDIATE")
                install_schema(connection)
            self._scientific_enabled = True

    def _prepare_storage(self):
        existing = self.path.exists()
        self._file_identity = None
        if existing:
            self._check_resource_identity()
        if self.inert:
            self._inert_state = self._checkpointed_identity()
        if self.readonly and not existing:
            raise OperationConflict("Read-only workflow ledger does not exist")
        if existing and (not self.path.is_file() or self.path.stat().st_size == 0):
            raise OperationConflict("Existing workflow ledger is empty or invalid")
        if not self.readonly:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock_dir = self.path.with_name(self.path.name + ".locks")
        if self.lock_dir.is_symlink():
            raise ValueError("Workflow lock directory cannot be a symlink")
        if not self.readonly:
            self.lock_dir.mkdir(exist_ok=True)
        return existing

    def _validate_existing_schema(self, connection):
        from .scientific_authority import (
            RECORD_LOOKUP_INDEX,
            check_schema,
            install_schema,
        )

        if connection.execute("PRAGMA user_version").fetchone()[0] != 1:
            raise OperationConflict("Unsupported workflow ledger schema")
        expected = {
            "workflows": ["workflow_id", "cancelled"],
            "operations": [
                "workflow_id",
                "node",
                "attempt",
                "status",
                "request_json",
                "request_digest",
                "result_json",
                "result_digest",
                "error_type",
            ],
        }
        for table, columns in expected.items():
            info = connection.execute(f"PRAGMA table_info({table})")
            if [row[1] for row in info] != columns:
                raise OperationConflict("Workflow ledger schema is incomplete")
        self._model_schema(connection)
        self._scientific_enabled = check_schema(connection)
        if not self._scientific_enabled:
            return
        if (
            not self.inert
            and connection.execute("PRAGMA journal_mode").fetchone()[0] != "wal"
        ):
            raise OperationConflict("Scientific authority requires WAL")
        if self.inert and not self._uses_wal():
            raise OperationConflict(
                "Checkpointed scientific authority requires a WAL database"
            )
        if (
            not self.readonly
            and not connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='index' AND name=?",
                (RECORD_LOOKUP_INDEX,),
            ).fetchone()
        ):
            connection.execute("BEGIN IMMEDIATE")
            install_schema(connection)

    @staticmethod
    def _create_initial_schema(connection):
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "CREATE TABLE workflows (workflow_id TEXT PRIMARY KEY, "
            "cancelled INTEGER NOT NULL CHECK(cancelled IN (0,1)))"
        )
        connection.execute(
            "CREATE TABLE operations (\n"
            "                    workflow_id TEXT NOT NULL, node TEXT NOT NULL, "
            "attempt INTEGER NOT NULL,\n"
            "                    status TEXT NOT NULL "
            "CHECK(status IN ('active','uncertain','failed','completed')),\n"
            "                    request_json TEXT NOT NULL, "
            "request_digest TEXT NOT NULL,\n"
            "                    result_json TEXT, result_digest TEXT, "
            "error_type TEXT,\n"
            "                    PRIMARY KEY(workflow_id,node,attempt))"
        )
        connection.execute("PRAGMA user_version=1")

    @property
    def scientific(self):
        from .scientific_authority import ScientificAuthority

        if not self._scientific_enabled:
            raise OperationBlocked("scientific_authority_unavailable")
        return ScientificAuthority(self)

    def _uses_wal(self):
        # Inspect the journal-mode header before opening SQLite so an unpatched
        # runtime never attaches to an existing WAL database.
        with os.fdopen(
            os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), "rb"
        ) as source:
            return source.read(20)[18:20] == b"\x02\x02"

    @staticmethod
    def _require_legacy_lineage(connection, workflow_id):
        from .scientific_authority import check_schema

        if (
            connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
            and check_schema(connection)
            and connection.execute(
                "SELECT 1 FROM scientific_records WHERE lineage_id=? LIMIT 1",
                (workflow_id,),
            ).fetchone()
        ):
            raise OperationBlocked("scientific_lineage")

    def _check_resource_identity(self):
        try:
            _path(self.path)
            info = self.path.stat()
        except (OSError, ValueError) as exc:
            raise OperationConflict(
                "Workflow ledger resource identity changed"
            ) from exc
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise OperationConflict(
                "Workflow ledger must be a single-linked regular file"
            )
        identity = (info.st_dev, info.st_ino)
        if self._file_identity is not None and self._file_identity != identity:
            raise OperationConflict("Workflow ledger resource identity changed")
        self._file_identity = identity

    def _checkpointed_identity(self):
        """An immutable SQLite reader is safe only for a stable checkpointed file.

        Nonempty WAL or rollback journals are never ignored. This is historical
        inspection, not a shortcut for observing a live authority writer.
        """
        from devbench.study.evidence import _file_sha256

        found = {}
        for suffix in ("", "-wal", "-shm", "-journal"):
            path = self.path.with_name(self.path.name + suffix)
            try:
                info = path.lstat()
            except FileNotFoundError as error:
                if not suffix:
                    raise OperationConflict(
                        "Checkpointed ledger is unavailable"
                    ) from error
                found[suffix] = None
                continue
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_nlink != 1
                or suffix in {"-wal", "-journal"}
                and info.st_size
            ):
                raise OperationConflict(
                    "Inert read requires a checkpoint without live WAL or journal"
                )
            try:
                digest = _file_sha256(path, 64 * 1024**2 if not suffix else 1024**2)
            except (ValueError, OSError) as error:
                raise OperationConflict(
                    "Checkpointed ledger changed while reading"
                ) from error
            after = path.stat()

            def signature(value):
                return (
                    value.st_dev,
                    value.st_ino,
                    value.st_mode,
                    value.st_size,
                    value.st_mtime_ns,
                    value.st_ctime_ns,
                )

            if signature(info) != signature(after):
                raise OperationConflict("Checkpointed ledger changed while reading")
            found[suffix] = (signature(after), digest)
        return found

    @contextmanager
    def _database(self, *, read_transaction=False):
        # Share bookkeeping safely between sibling threads, while independent
        # objects/processes still observe SQLite's explicit busy state.
        bounded = dispatch_deadline.deadline.get() != float("inf")
        timeout = dispatch_deadline.remaining(60) if bounded else -1
        if not self._transaction_lock.acquire(timeout=timeout):
            raise OperationBlocked("resource_quota_exceeded")
        try:
            with self._connection(read_transaction=read_transaction) as connection:
                yield connection
        finally:
            self._transaction_lock.release()

    def _validate_wal_sidecars(self):
        from .scientific_authority import require_patched_wal

        require_patched_wal()
        for suffix in ("-wal", "-shm"):
            sidecar = self.path.with_name(self.path.name + suffix)
            try:
                info = sidecar.lstat()
                if stat.S_ISREG(info.st_mode) and info.st_nlink == 0:
                    # The last SQLite connection can unlink a sidecar while
                    # lstat is reading its inode. Validate the current name.
                    info = sidecar.lstat()
            except FileNotFoundError:
                continue
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise OperationConflict("Unsafe scientific WAL sidecar")

    def _configure_connection(self, connection, *, wal, timeout, read_transaction):
        self._check_resource_identity()
        end = dispatch_deadline.deadline.get()
        if end != float("inf"):
            connection.set_progress_handler(lambda: int(time.monotonic() >= end), 2000)
        connection.row_factory = sqlite3.Row
        if wal or self._scientific_requested:
            connection.execute("PRAGMA foreign_keys=ON")
        if not self.readonly:
            connection.execute("PRAGMA synchronous=FULL")
            if connection.execute("PRAGMA synchronous").fetchone()[0] != 2:
                raise OperationConflict("Workflow ledger requires FULL durability")
        if connection.execute("PRAGMA busy_timeout").fetchone()[0] != int(
            timeout * 1000
        ):
            raise OperationConflict("Workflow ledger busy timeout was not applied")
        if (
            self._scientific_enabled
            and not self.inert
            and connection.execute("PRAGMA journal_mode").fetchone()[0] != "wal"
        ):
            raise OperationConflict("Scientific authority lost WAL")
        if read_transaction:
            connection.execute("PRAGMA query_only=ON")

    @contextmanager
    def _connection(self, *, read_transaction=False):
        try:
            if self.inert and self._checkpointed_identity() != self._inert_state:
                raise OperationConflict("Checkpointed ledger changed before read")
            wal = False
            if self._file_identity is not None:
                self._check_resource_identity()
                wal = self._uses_wal()
            if wal or self._scientific_requested:
                self._validate_wal_sidecars()
            database = (
                self.path.as_uri() + "?mode=ro" if self.readonly else str(self.path)
            )
            if self.inert:
                database += "&immutable=1"
            # Independent live rollback connections share SQLite's bounded wait
            # for transient locks. Exhausted waits fail closed below.
            timeout = 10 if wal or self._scientific_requested or not self.inert else 0
            if read_transaction:
                timeout = 0.25
            timeout = dispatch_deadline.remaining(timeout)
            # ATTACH uses mode=rw URIs too. Enable URI handling explicitly on
            # writers even when SQLite was built without SQLITE_USE_URI.
            with closing(
                sqlite3.connect(database, timeout=timeout, uri=True)
            ) as connection:
                self._configure_connection(
                    connection,
                    wal=wal,
                    timeout=timeout,
                    read_transaction=read_transaction,
                )
                with connection:
                    yield connection
            if self.inert and self._checkpointed_identity() != self._inert_state:
                raise OperationConflict("Checkpointed ledger changed during read")
        except sqlite3.OperationalError as exc:
            if _sqlite_busy(exc) or "locked" in str(exc) or "busy" in str(exc):
                raise OperationBlocked("ledger_busy") from exc
            raise OperationConflict("Workflow ledger unavailable or corrupt") from exc
        except sqlite3.DatabaseError as exc:
            raise OperationConflict("Workflow ledger unavailable or corrupt") from exc

    def _read(self, query: Callable[[sqlite3.Connection], _ReadResult]) -> _ReadResult:
        """Read a materialized result, restarting live scientific snapshots on busy."""
        if self.inert or not self._scientific_enabled:
            with self._database() as connection:
                return query(connection)
        retries = 0
        while True:
            try:
                with self._database(read_transaction=True) as connection:
                    connection.execute("BEGIN")
                    return query(connection)
            except OperationBlocked as error:
                if (
                    retries == 2
                    or error.state != "ledger_busy"
                    or not _sqlite_busy(error.__cause__)
                ):
                    raise
            # Release the connection and the local lock before waiting.
            delay = 0.01 * 2**retries
            time.sleep(random.uniform(delay, 2 * delay))
            retries += 1

    @contextmanager
    def _operation_lock(self, key):
        self._check_resource_identity()
        _path(self.lock_dir)
        lock_path = self.lock_dir / (_digest(_json(list(key))) + ".lock")
        descriptor = os.open(
            str(lock_path), os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600
        )
        try:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise OperationBlocked("active") from exc
            self._check_resource_identity()
            yield
        finally:
            os.close(descriptor)

    @staticmethod
    def _record(row):
        if row is None:
            return None
        record = dict(row)
        try:
            for prefix in ("request", "result"):
                encoded = record.pop(prefix + "_json")
                digest = record[prefix + "_digest"]
                if encoded is None:
                    if digest is not None or (
                        prefix == "result" and record["status"] == "completed"
                    ):
                        raise ValueError("Missing retained payload")
                    record[prefix] = None
                else:
                    decoded = json.loads(encoded)
                    if _json(decoded) != encoded or _digest(encoded) != digest:
                        raise ValueError("Payload integrity mismatch")
                    record[prefix] = decoded
            if record["status"] not in ("active", "uncertain", "failed", "completed"):
                raise ValueError("Invalid state")
        except (ValueError, TypeError, KeyError, RecursionError) as exc:
            raise OperationConflict(
                "Retained workflow record failed integrity validation"
            ) from exc
        return record

    def get(self, workflow_id: str, node: str, attempt: int):
        key = _identity(workflow_id, node, attempt)
        return self._read(
            lambda db: self._record(
                db.execute(
                    "SELECT * FROM operations WHERE workflow_id=? AND node=? AND "
                    "attempt=?",
                    key,
                ).fetchone()
            )
        )

    def replay(self, workflow_id: str):
        _identity(workflow_id, "replay", 0)

        def read(db):
            rows = db.execute(
                "SELECT * FROM operations WHERE workflow_id=? ORDER BY attempt,node",
                (workflow_id,),
            ).fetchall()
            return [self._record(row) for row in rows]

        return self._read(read)

    def cancel(self, workflow_id: str) -> None:
        self._writable()
        _identity(workflow_id, "cancel", 0)
        with self._database() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._require_legacy_lineage(connection, workflow_id)
            connection.execute(
                "INSERT INTO workflows VALUES (?,1) ON CONFLICT(workflow_id) DO "
                "UPDATE SET cancelled=1",
                (workflow_id,),
            )

        self._observe(
            workflow_id,
            "cancel",
            0,
            _digest(_json({"workflow_id": workflow_id})),
            "cancel",
            "cancelled",
        )

    def is_cancelled(self, workflow_id: str) -> bool:
        _identity(workflow_id, "cancel", 0)

        def read(db):
            row = db.execute(
                "SELECT cancelled FROM workflows WHERE workflow_id=?", (workflow_id,)
            ).fetchone()
            return bool(row and row[0])

        return self._read(read)

    def _state(self, key, state: str, error_type: str | None = None):
        self._writable()
        with self._database() as connection:
            changed = connection.execute(
                "UPDATE operations SET status=?, error_type=? WHERE workflow_id=? "
                "AND node=? AND attempt=? AND status='active'",
                (state, error_type, *key),
            )
            if changed.rowcount != 1:
                raise OperationConflict("Workflow operation state changed unexpectedly")

    def _observe(self, workflow_id, node, attempt, request_digest, kind, status):
        observer = getattr(self, "trace_observer", None)
        if observer is not None and not self.readonly:
            try:
                observer.observe_operation(
                    workflow_id, node, attempt, request_digest, kind, status
                )
            except Exception:
                pass  # Optional observation never repairs or retries execution.

    def run(
        self,
        workflow_id: str,
        node: str,
        attempt: int,
        request: Any,
        callback: Callable[[], Any],
    ):
        self._writable()
        key = _identity(workflow_id, node, attempt)
        encoded = _json(request)
        digest = _digest(encoded)
        with self._operation_lock(key):
            record = self.get(*key)
            if record is not None:
                if record["request_digest"] != digest:
                    raise OperationConflict(
                        "Logical operation key reused with different input"
                    )
                if record["status"] == "completed":
                    return record["result"]
                if record["status"] == "active":
                    self._state(key, "uncertain")
                    raise OperationBlocked("uncertain")
                raise OperationBlocked(record["status"])
            with self._database() as connection:
                connection.execute("BEGIN IMMEDIATE")
                self._require_legacy_lineage(connection, workflow_id)
                cancellation = connection.execute(
                    "SELECT cancelled FROM workflows WHERE workflow_id=?",
                    (workflow_id,),
                ).fetchone()
                if cancellation and cancellation[0]:
                    raise OperationBlocked("cancelled")
                connection.execute(
                    "INSERT INTO operations VALUES (?,?,?,'active',?,?,NULL,NULL,NULL)",
                    (*key, encoded, digest),
                )
            self._observe(*key, digest, "reservation", "active")
            try:
                result = callback()
            except BaseException as exc:
                self._state(key, "uncertain", type(exc).__name__[:128])
                self._observe(*key, digest, "failure", "uncertain")
                raise
            try:
                result_json = _json(result)
                with self._database() as connection:
                    changed = connection.execute(
                        "UPDATE operations SET "
                        "status='completed',result_json=?,result_digest=?,error_type=NULL"
                        " WHERE workflow_id=? AND node=? AND attempt=? AND "
                        "status='active' AND request_digest=? AND request_json=?",
                        (result_json, _digest(result_json), *key, digest, encoded),
                    )
                    if changed.rowcount != 1:
                        raise OperationConflict(
                            "Workflow completion did not update its reserved "
                            "active operation"
                        )
            except BaseException:
                self._state(key, "uncertain")
                raise
            self._observe(*key, digest, "completion", "completed")
            return json.loads(result_json)

    def reconcile(self, workflow_id: str, node: str, attempt: int, result: Any):
        """Retain externally verified completion; never dispatch or retry work."""
        self._writable()
        key = _identity(workflow_id, node, attempt)
        result_json = _json(result)
        with self._operation_lock(key):
            record = self.get(*key)
            if record is None:
                raise OperationConflict("Cannot reconcile an unreserved operation")
            if record["status"] == "completed":
                if record["result_digest"] != _digest(result_json):
                    raise OperationConflict("Conflicting reconciliation result")
                return record["result"]
            if record["status"] not in ("active", "uncertain"):
                raise OperationBlocked(record["status"])
            with self._database() as connection:
                changed = connection.execute(
                    "UPDATE operations SET "
                    "status='completed',result_json=?,result_digest=?,error_type=NULL"
                    " WHERE workflow_id=? AND node=? AND attempt=? AND status=? AND "
                    "request_digest=?",
                    (
                        result_json,
                        _digest(result_json),
                        *key,
                        record["status"],
                        record["request_digest"],
                    ),
                )
                if changed.rowcount != 1:
                    raise OperationConflict(
                        "Workflow reconciliation state changed unexpectedly"
                    )
            self._observe(*key, record["request_digest"], "reconcile", "completed")
            return json.loads(result_json)

    def _writable(self):
        if self.readonly:
            raise OperationBlocked("readonly")

    @staticmethod
    def _model_schema(connection, *, create=False):
        found = {
            table: [row[1] for row in connection.execute(f"PRAGMA table_info({table})")]
            for table in _MODEL_COLUMNS
        }
        if not any(found.values()):
            if not create:
                return False
            connection.execute(
                "CREATE TABLE model_campaigns (\n"
                "                campaign_id TEXT PRIMARY KEY, schema_version "
                "INTEGER NOT NULL CHECK(schema_version=1),\n"
                "                session_id TEXT NOT NULL, status TEXT NOT NULL "
                "CHECK(status IN ('open','cancelled','blocked')),\n"
                "                reason TEXT, policy_json TEXT NOT NULL, "
                "policy_digest TEXT NOT NULL,\n"
                "                capability_json TEXT NOT NULL, capability_digest "
                "TEXT NOT NULL,\n"
                "                identity_json TEXT NOT NULL, identity_digest TEXT "
                "NOT NULL, created_at REAL NOT NULL)"
            )
            connection.execute(
                "CREATE TABLE model_reservations (\n"
                "                campaign_id TEXT NOT NULL, node TEXT NOT NULL, "
                "attempt INTEGER NOT NULL,\n"
                "                schema_version INTEGER NOT NULL "
                "CHECK(schema_version=1), ref TEXT NOT NULL UNIQUE,\n"
                "                status TEXT NOT NULL CHECK(status IN "
                "('reserved','active','completed','failed','cancelled','uncertain')),\n"
                "                purpose TEXT NOT NULL, child_id TEXT, request_json"
                " TEXT NOT NULL, request_digest TEXT NOT NULL,\n"
                "                limits_json TEXT NOT NULL, limits_digest TEXT NOT "
                "NULL, capture_json TEXT, capture_digest TEXT,\n"
                "                error_type TEXT, charge_basis TEXT NOT NULL,\n"
                "                PRIMARY KEY(campaign_id,node,attempt))"
            )
            connection.execute(
                "CREATE TABLE investigations (\n"
                "                campaign_id TEXT PRIMARY KEY, schema_version "
                "INTEGER NOT NULL CHECK(schema_version=1),\n"
                "                fork_id TEXT NOT NULL, stage TEXT NOT NULL, "
                "request_json TEXT NOT NULL, request_digest TEXT NOT NULL)"
            )
            return True
        if found != _MODEL_COLUMNS:
            raise OperationConflict(
                "Model campaign schema is incomplete or unsupported"
            )
        return True

    @staticmethod
    def _campaign_row(row):
        if row is None:
            return None
        record = dict(row)
        if record["schema_version"] != 1 or record["status"] not in (
            "open",
            "cancelled",
            "blocked",
        ):
            raise OperationConflict("Invalid retained model campaign")
        if (
            type(record["created_at"]) not in (int, float)
            or not math.isfinite(record["created_at"])
            or record["created_at"] < 0
        ):
            raise OperationConflict("Invalid retained model campaign clock")
        for name in ("policy", "capability", "identity"):
            record[name] = _retained_json(
                record.pop(name + "_json"), record[name + "_digest"]
            )
        try:
            _policy(record["policy"])
            if (
                type(record["capability"]) is not dict
                or type(record["identity"]) is not dict
            ):
                raise ValueError("Invalid campaign metadata")
            _protected_allocations(record["policy"], record["identity"])
        except (ValueError, TypeError, KeyError) as exc:
            raise OperationConflict("Invalid retained model campaign") from exc
        return record

    @staticmethod
    def _model_record(row):
        if row is None:
            return None
        record = dict(row)
        if record["schema_version"] != 1 or record["status"] not in (
            "reserved",
            "active",
            "completed",
            "failed",
            "cancelled",
            "uncertain",
        ):
            raise OperationConflict("Invalid retained model reservation")
        for name in ("request", "limits"):
            record[name] = _retained_json(
                record.pop(name + "_json"), record[name + "_digest"]
            )
        encoded = record.pop("capture_json")
        if encoded is None:
            if record["capture_digest"] is not None or record["status"] in (
                "completed",
                "failed",
            ):
                raise OperationConflict("Missing verified model capture")
            record["capture"] = None
        else:
            record["capture"] = _retained_json(encoded, record["capture_digest"])
        if record["ref"] != model_reservation_ref(
            record["campaign_id"], record["node"], record["attempt"]
        ):
            raise OperationConflict("Model reservation reference changed")
        try:
            _limits(record["limits"])
        except (ValueError, TypeError) as exc:
            raise OperationConflict("Invalid retained model limits") from exc
        return record

    @staticmethod
    def _reservation(record, campaign):
        return ModelCallReservation(
            record["campaign_id"],
            record["node"],
            record["attempt"],
            record["ref"],
            record["status"],
            record["purpose"],
            record["child_id"],
            record["request_digest"],
            campaign["policy_digest"],
            campaign["capability_digest"],
            _json(record["request"]),
            _json(record["limits"]),
        )

    def _model_rows(self, connection, campaign_id):
        records = [
            self._model_record(row)
            for row in connection.execute(
                "SELECT * FROM model_reservations WHERE campaign_id=? ORDER BY "
                "attempt,node",
                (campaign_id,),
            )
        ]
        campaign = self._campaign(connection, campaign_id) if records else None
        for record in records:
            job_policy = campaign["policy"]["kind"] == "generation_jobs"
            if job_policy != ("jobs" in record["limits"]):
                raise OperationConflict(
                    "Retained allocation has the wrong accounting unit"
                )
            if job_policy and record["capture"] is not None:
                try:
                    validate_job_capture(
                        record["capture"],
                        record["capture"]["capture_ref"],
                        self._reservation(record, campaign),
                    )
                except (ValueError, TypeError, KeyError) as exc:
                    raise OperationConflict(
                        "Invalid retained generation job capture"
                    ) from exc
            operation = self._record(
                connection.execute(
                    "SELECT * FROM operations WHERE workflow_id=? AND node=? AND "
                    "attempt=?",
                    (campaign_id, record["node"], record["attempt"]),
                ).fetchone()
            )
            expected_state = {
                "reserved": "active",
                "active": "active",
                "uncertain": "uncertain",
                "completed": "completed",
                "failed": "failed",
                "cancelled": "failed",
            }[record["status"]]
            if (
                operation is None
                or operation["request_digest"] != record["request_digest"]
                or operation["status"] != expected_state
            ):
                raise OperationConflict(
                    "Model reservation and workflow operation disagree"
                )
            if (
                record["capture"] is not None
                and operation["result_digest"] != record["capture_digest"]
            ):
                raise OperationConflict("Retained model capture and operation disagree")
        return records

    def _totals(self, campaign, records):
        if campaign["policy"]["kind"] == "generation_jobs":
            return self._job_totals(campaign, records)
        totals = {
            "verified_spent_tokens": 0,
            "outstanding_reserved_tokens": 0,
            "verified_calls": 0,
            "outstanding_reserved_calls": 0,
            "verified_seconds": 0,
            "outstanding_reserved_seconds": 0,
            "unresolved_count": 0,
            "verified_token_lower_bound": 0,
        }
        for record in records:
            limits, capture = record["limits"], record["capture"]
            unresolved = record["status"] in ("reserved", "active", "uncertain")
            tokens = capture["usage"]["normalized_total_tokens"] if capture else None
            floor = _token_floor(capture["usage"]) if capture else 0
            totals["verified_token_lower_bound"] += floor
            calls = capture["actual_calls"] if capture else 0
            seconds = capture["elapsed_seconds"] if capture else 0
            if tokens is not None:
                totals["verified_spent_tokens"] += tokens
            elif unresolved:
                totals["outstanding_reserved_tokens"] += max(limits["tokens"], floor)
            totals["verified_calls"] += calls
            totals["verified_seconds"] += seconds
            if unresolved:
                totals["outstanding_reserved_calls"] += max(0, limits["calls"] - calls)
                totals["outstanding_reserved_seconds"] += max(
                    0, limits["seconds"] - seconds
                )
            totals["unresolved_count"] += record["status"] == "uncertain"
        held = WorkflowOperationLedger._parent_hold(campaign, records)
        totals["reconciliation_held"] = dict(held)
        totals["hard_token_guarantee"] = (
            campaign["policy"]["kind"] == "hard_total_tokens"
            and self._accepted_capability(campaign["capability"], campaign["policy"])
            and campaign["reason"] != "backend_limit_violation"
        )
        if campaign["reason"] in (
            "backend_limit_violation",
            "soft_token_target_exceeded",
        ):
            totals["qualification"] = "violated"
        elif totals["unresolved_count"]:
            totals["qualification"] = "incomplete"
        elif not records or any(
            row["status"] in ("reserved", "active") for row in records
        ):
            totals["qualification"] = "pending"
        else:
            totals["qualification"] = "complete"
        return totals

    @classmethod
    def replay_model_totals(cls, campaign, records):
        """Recompute captured usage without opening storage or restoring a backend."""
        reader = object.__new__(cls)
        reader._job_backends = {}
        reader._mediated_backends = {}
        reader._local_interactive_backends = {}
        return reader._totals(campaign, records)

    @staticmethod
    def _job_totals(campaign, records):
        totals = {
            "accounting_kind": "generation_jobs",
            "verified_jobs": 0,
            "outstanding_reserved_jobs": 0,
            "verified_seconds": 0,
            "outstanding_reserved_seconds": 0,
            "unresolved_count": 0,
        }
        for record in records:
            capture = record["capture"]
            unresolved = record["status"] in ("reserved", "active", "uncertain")
            # Cancellation before activation creates no launch intent. Every
            # retained capture, including not_started, permanently debits a job.
            if capture:
                totals["verified_jobs"] += capture["job_count"]
            elif unresolved:
                totals["outstanding_reserved_jobs"] += record["limits"]["jobs"]
            seconds = capture["elapsed_seconds"] if capture else 0
            totals["verified_seconds"] += seconds
            if unresolved:
                totals["outstanding_reserved_seconds"] += max(
                    0, record["limits"]["seconds"] - seconds
                )
            totals["unresolved_count"] += record["status"] == "uncertain"
        totals["reconciliation_held"] = WorkflowOperationLedger._parent_hold(
            campaign, records
        )
        if campaign["reason"] == "backend_limit_violation":
            totals["qualification"] = "violated"
        elif totals["unresolved_count"]:
            totals["qualification"] = "incomplete"
        elif not records or any(
            row["status"] in ("reserved", "active") for row in records
        ):
            totals["qualification"] = "pending"
        else:
            totals["qualification"] = "complete"
        return totals

    @staticmethod
    def _parent_hold(campaign, records):
        reserve = campaign["policy"]["reconciliation"]
        job_policy = campaign["policy"]["kind"] == "generation_jobs"
        count_key = "jobs" if job_policy else "calls"
        additive = ("jobs", "seconds") if job_policy else ("tokens", "calls", "seconds")
        if campaign["status"] != "open":
            return {key: 0 for key in reserve}
        records = [
            row for row in records if row["charge_basis"] != "verified_unlaunched"
        ]
        allocations = _protected_allocations(campaign["policy"], campaign["identity"])
        if not allocations:
            consumed = [
                row
                for row in records
                if row["child_id"] is None and row["purpose"] == "parent_reconciliation"
            ]
            held = {
                key: max(0, reserve[key] - sum(row["limits"][key] for row in consumed))
                for key in reserve
            }
            if (
                consumed
                and sum(row["limits"][count_key] for row in consumed)
                >= reserve[count_key]
                and all(
                    row["status"] in ("completed", "failed", "cancelled")
                    for row in consumed
                )
            ):
                return {key: 0 for key in reserve}
            return held
        held = {key: 0 for key in reserve}
        finished = []
        for stage, allocation in allocations.items():
            consumed = [
                row
                for row in records
                if row["child_id"] is None and row["request"].get("purpose") == stage
            ]
            complete = (
                bool(consumed)
                and sum(row["limits"][count_key] for row in consumed)
                >= allocation[count_key]
                and all(
                    row["status"] in ("completed", "failed", "cancelled")
                    for row in consumed
                )
            )
            finished.append(complete)
            if complete:
                continue
            for key in additive:
                held[key] += max(
                    0, allocation[key] - sum(row["limits"][key] for row in consumed)
                )
            held["context_bytes"] = max(
                held["context_bytes"], allocation["context_bytes"]
            )
        if not all(finished):
            for key in additive:
                held[key] += max(
                    0,
                    reserve[key]
                    - sum(allocation[key] for allocation in allocations.values()),
                )
        return held

    @staticmethod
    def _future_parent_seconds(
        campaign,
        records,
        *,
        record=None,
        request=None,
        limits=None,
        purpose=None,
        child_id=None,
    ):
        records = [
            row for row in records if row["charge_basis"] != "verified_unlaunched"
        ]
        held = WorkflowOperationLedger._parent_hold(campaign, records)
        count_key = (
            "jobs" if campaign["policy"]["kind"] == "generation_jobs" else "calls"
        )
        if record is not None:
            request, limits, purpose, child_id = (
                record["request"],
                record["limits"],
                record["purpose"],
                record["child_id"],
            )
        future = held["seconds"]
        allocations = _protected_allocations(campaign["policy"], campaign["identity"])
        stage = request.get("purpose") if request else None
        if child_id is None and type(stage) is str and stage in allocations:
            allocation = allocations[stage]
            consumed = [
                row
                for row in records
                if row["child_id"] is None and row["request"].get("purpose") == stage
            ]
            remaining_calls = max(
                0,
                allocation[count_key]
                - sum(row["limits"][count_key] for row in consumed),
            )
            own_unused_seconds = max(
                0,
                allocation["seconds"]
                - sum(row["limits"]["seconds"] for row in consumed),
            )
            if record is None:
                own_share = (
                    own_unused_seconds
                    if limits[count_key] >= remaining_calls
                    else min(limits["seconds"], own_unused_seconds)
                )
                future = max(0, future - own_share)
            elif remaining_calls == 0:
                # Until completion, this call still owns its unused allocation;
                # those seconds are not an additional future parent invocation.
                future = max(0, future - own_unused_seconds)
        elif (
            child_id is None and not allocations and purpose == "parent_reconciliation"
        ):
            if record is None:
                future = (
                    max(0, future - limits["seconds"])
                    if held[count_key] > limits[count_key]
                    else 0
                )
            elif held[count_key] == 0:
                future = 0
        current_ref = record["ref"] if record is not None else None
        pending = [
            row
            for row in records
            if row["child_id"] is None
            and row["ref"] != current_ref
            and (
                (
                    type(row["request"].get("purpose")) is str
                    and row["request"]["purpose"] in allocations
                )
                if allocations
                else row["purpose"] == "parent_reconciliation"
            )
        ]
        # Reservation transfers time from an unassigned hold to a concrete
        # future parent lease. It does not make that time available to children.
        future += sum(
            row["limits"]["seconds"] for row in pending if row["status"] == "reserved"
        )
        future += max(
            (row["limits"]["seconds"] for row in pending if row["status"] == "active"),
            default=0,
        )
        return future

    def _campaign(self, connection, campaign_id):
        campaign = self._campaign_row(
            connection.execute(
                "SELECT * FROM model_campaigns WHERE campaign_id=?", (campaign_id,)
            ).fetchone()
        )
        if campaign is None:
            raise OperationConflict("Model campaign has not been admitted")
        return campaign

    def get_campaign(self, campaign_id: str):
        _identity(campaign_id, "campaign", 0)

        def read(connection):
            if not self._model_schema(connection):
                return None
            row = connection.execute(
                "SELECT * FROM model_campaigns WHERE campaign_id=?", (campaign_id,)
            ).fetchone()
            if row is None:
                return None
            campaign = self._campaign_row(row)
            records = self._model_rows(connection, campaign_id)
            fork = connection.execute(
                "SELECT * FROM investigations WHERE campaign_id=?", (campaign_id,)
            ).fetchone()
            campaign["fork"] = self._fork_record(fork)
            campaign["deadline_epoch"] = (
                campaign["created_at"] + campaign["policy"]["max_total_seconds"]
            )
            return {**campaign, **self._totals(campaign, records)}

        return self._read(read)

    def campaign_reservations(self, campaign_id: str):
        _identity(campaign_id, "campaign", 0)

        def read(db):
            if not self._model_schema(db):
                return []
            return self._model_rows(db, campaign_id)

        return self._read(read)

    def get_model_reservation(self, reservation, node=None, attempt=None):
        def read(connection):
            if not self._model_schema(connection):
                return None
            record, _ = self._checked_reservation(
                connection, reservation, node, attempt
            )
            return record

        return self._read(read)

    def model_call_deadline(self, reservation, node=None, attempt=None):
        """Read the original absolute campaign deadline without creating a grant."""

        def read(connection):
            _, campaign = self._checked_reservation(
                connection, reservation, node, attempt
            )
            return campaign["created_at"] + campaign["policy"]["max_total_seconds"]

        return self._read(read)

    @staticmethod
    def _role_execution_cancelled(connection, record):
        if record["request"].get("role") not in {"controller", "challenger"}:
            return False
        from .scientific_authority import ScientificAuthority

        execution = ScientificAuthority._load_state(
            connection, record["request"]["parent_session_id"]
        )
        return execution is not None and execution["status"] == "cancelled"

    def model_call_remaining(self, reservation, node=None, attempt=None):
        """Protected launchers must clamp each actual launch to this remainder."""
        with self._database() as connection:
            record, campaign = self._checked_reservation(
                connection, reservation, node, attempt
            )
            cancellation = connection.execute(
                "SELECT cancelled FROM workflows WHERE workflow_id=?",
                (campaign["campaign_id"],),
            ).fetchone()
            if (
                record["status"] not in ("reserved", "active")
                or campaign["status"] != "open"
                or (cancellation and cancellation[0])
                or self._role_execution_cancelled(connection, record)
                or self._totals(
                    campaign, self._model_rows(connection, campaign["campaign_id"])
                )["unresolved_count"]
            ):
                return 0
            if campaign["policy"]["kind"] == "generation_jobs":
                if not self._accepted_capability(
                    campaign["capability"], campaign["policy"]
                ):
                    return 0
                try:
                    self._job_concurrency(connection, exclude_ref=record["ref"])
                except OperationBlocked:
                    return 0
            deadline = campaign["created_at"] + campaign["policy"]["max_total_seconds"]
            future = self._future_parent_seconds(
                campaign,
                self._model_rows(connection, campaign["campaign_id"]),
                record=record,
            )
            return min(
                record["limits"]["seconds"], max(0, deadline - time.time() - future)
            )

    def _checked_reservation(self, connection, reservation, node=None, attempt=None):
        if isinstance(reservation, ModelCallReservation):
            key = _identity(
                reservation.campaign_id, reservation.node, reservation.attempt
            )
            row = connection.execute(
                "SELECT * FROM model_reservations WHERE campaign_id=? AND node=? AND"
                " attempt=?",
                key,
            ).fetchone()
        elif node is not None and attempt is not None:
            key = _identity(reservation, node, attempt)
            row = connection.execute(
                "SELECT * FROM model_reservations WHERE campaign_id=? AND node=? AND"
                " attempt=?",
                key,
            ).fetchone()
        elif type(reservation) is str and reservation.startswith("model:"):
            row = connection.execute(
                "SELECT * FROM model_reservations WHERE ref=?", (reservation,)
            ).fetchone()
        else:
            raise ValueError("Use a retained model reservation or its exact reference")
        record = self._model_record(row)
        if record is None:
            raise OperationConflict("Model reservation does not exist")
        campaign = self._campaign(connection, record["campaign_id"])
        if isinstance(reservation, ModelCallReservation):
            current = self._reservation(record, campaign)
            if any(
                getattr(current, key) != getattr(reservation, key)
                for key in (
                    "campaign_id",
                    "node",
                    "attempt",
                    "ref",
                    "purpose",
                    "child_id",
                    "request_digest",
                    "policy_digest",
                    "capability_digest",
                    "_request_json",
                    "_limits_json",
                )
            ):
                raise OperationConflict("Model reservation identity changed")
        self._model_rows(connection, record["campaign_id"])
        return record, campaign

    @staticmethod
    def _capability_available(capability, policy):
        if policy["kind"] == "generation_jobs":
            return (
                capability.get("backend") == "enforced-offline-job-fixture"
                and capability.get("capability_status") == "offline_qualified"
                and all(
                    capability.get(key) is True
                    for key in (
                        "generation_job_accounting",
                        "context_bytes_enforced",
                        "output_bytes_enforced",
                        "outer_timeout_enforced",
                        "process_reaping_enforced",
                    )
                )
                and capability.get("max_concurrent_jobs") == 1
                and capability.get("child_command_or_numerical_tools_allowed") is False
            )
        keys = (
            "context_bytes_enforced",
            "outer_timeout_enforced",
            "memory_enforced",
            "request_count_enforced",
            "all_attributable_requests_accounted",
        )
        return (
            capability.get("capability_status")
            in ("offline_qualified", "native_qualified")
            and capability.get("usage_capture") == "verified_complete"
            and all(capability.get(key) is True for key in keys)
            and capability.get("child_command_or_numerical_tools_allowed") is False
            and (
                not policy["max_children"]
                or capability.get("parallel_boundary_qualified") is True
            )
            and (
                policy["kind"] != "hard_total_tokens"
                or capability.get("enforced_total_token_cap") is True
            )
        )

    def _accepted_capability(self, capability, policy, *, campaign_id=None):
        if LOCAL_INTERACTIVE_CODEX in (capability.get("backend"), policy["kind"]):
            return self._accepted_local_interactive(capability, policy, campaign_id)
        if policy["kind"] == "generation_jobs":
            if capability.get("backend") != "codex-chatgpt-cli":
                return self._capability_available(capability, policy)
            backend = self._job_backends.get(capability.get("backend_digest"))
            if backend is None:
                return False
            from devbench.analysis.codex_signedin import (
                SignedInCodexInvestigationBackend,
            )

            try:
                return (
                    type(backend) is SignedInCodexInvestigationBackend
                    and policy == backend.policy
                    and backend.require_admission("generation_jobs") == capability
                )
            except PermissionError:
                return False
        if capability.get("backend") != "openai-responses-mediated":
            return self._capability_available(capability, policy)
        backend = self._mediated_backends.get(capability.get("backend_digest"))
        if backend is None:
            return False  # Stored capability JSON never reconstructs live authority.
        from devbench.analysis.rna_mediated_backend import MediatedInvestigationBackend

        return (
            type(backend) is MediatedInvestigationBackend
            and policy == backend.policy
            and policy["kind"] == "hard_total_tokens"
            and policy["max_children"] == 0
            and backend.require_admission(policy["kind"]) == capability
        )

    def _accepted_local_interactive(self, capability, policy, campaign_id):
        if not (capability.get("backend") == policy["kind"] == LOCAL_INTERACTIVE_CODEX):
            return False
        backend = self._local_interactive_backends.get(campaign_id)
        return (
            backend is not None
            and policy == backend.policy
            and backend.require_admission(LOCAL_INTERACTIVE_CODEX) == capability
        )

    def _validate_local_interactive(self, backend, capability, policy, identity):
        from devbench.analysis.rna_local_interactive import LocalInteractiveCodexBackend

        if (
            type(backend) is not LocalInteractiveCodexBackend
            or policy != backend.policy
            or identity
            != backend.campaign_identity(_hash(identity.get("expected_plan_hash")))
            or backend.require_admission(LOCAL_INTERACTIVE_CODEX) != capability
        ):
            raise PermissionError(
                "An exact current local interactive backend is required"
            )
        backend.bind_ledger(self)

    def _register_campaign_backend(self, backend, capability, policy):
        if backend is None:
            return
        if policy["kind"] == "generation_jobs":
            from devbench.analysis.codex_signedin import (
                SignedInCodexInvestigationBackend,
            )

            if (
                type(backend) is not SignedInCodexInvestigationBackend
                or backend.require_admission("generation_jobs") != capability
            ):
                raise PermissionError(
                    "An exact current accepted generation job backend is required"
                )
            self._job_backends[capability["backend_digest"]] = backend
        else:
            from devbench.analysis.rna_mediated_backend import (
                MediatedInvestigationBackend,
            )

            if (
                type(backend) is not MediatedInvestigationBackend
                or backend.require_admission(policy["kind"]) != capability
            ):
                raise PermissionError(
                    "An exact current accepted mediated backend is required"
                )
            self._mediated_backends[capability["backend_digest"]] = backend

    def open_campaign(
        self,
        campaign_id: str,
        *,
        session_id: str,
        policy: dict,
        capability: dict,
        identity: dict,
        backend=None,
    ):
        """Bind one admitted campaign before any parent or child model request."""
        self._writable()
        _identity(campaign_id, session_id, 0)
        policy = _policy(policy)
        if type(capability) is not dict or type(identity) is not dict:
            raise ValueError(
                "Campaign capability and identity must be bounded public objects"
            )
        _protected_allocations(policy, identity)
        for key in ("backend_digest", "runtime_digest"):
            _hash(capability.get(key))
        encoded = [_json(value) for value in (policy, capability, identity)]
        digests = [_digest(value) for value in encoded]
        local_enrollment = (
            policy["kind"] == LOCAL_INTERACTIVE_CODEX and backend is not None
        )
        if local_enrollment:
            self._validate_local_interactive(backend, capability, policy, identity)
        else:
            self._register_campaign_backend(backend, capability, policy)
        blocked = not local_enrollment and not self._accepted_capability(
            capability,
            policy,
            campaign_id=campaign_id,
        )
        blocking_reason = "blocked_backend_capability" if blocked else None
        with self._database() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._model_schema(connection, create=True)
            row = connection.execute(
                "SELECT * FROM model_campaigns WHERE campaign_id=?", (campaign_id,)
            ).fetchone()
            if row:
                existing = self._campaign_row(row)
                if existing["session_id"] != session_id or any(
                    existing[name + "_digest"] != digest
                    for name, digest in zip(
                        ("policy", "capability", "identity"),
                        digests,
                        strict=True,
                    )
                ):
                    raise OperationConflict(
                        "Model campaign key reused with different admission"
                    )
                blocked = existing["status"] == "blocked" or (
                    policy["kind"] == LOCAL_INTERACTIVE_CODEX and blocked
                )
                blocking_reason = existing["reason"]
            else:
                cancellation = connection.execute(
                    "SELECT cancelled FROM workflows WHERE workflow_id=?",
                    (campaign_id,),
                ).fetchone()
                if cancellation and cancellation[0]:
                    raise OperationBlocked("cancelled")
                connection.execute(
                    "INSERT INTO model_campaigns VALUES (?,1,?,?,?,?,?,?,?,?,?,?)",
                    (
                        campaign_id,
                        session_id,
                        "blocked" if blocked else "open",
                        "blocked_backend_capability" if blocked else None,
                        encoded[0],
                        digests[0],
                        encoded[1],
                        digests[1],
                        encoded[2],
                        digests[2],
                        time.time(),
                    ),
                )
        if blocked:
            raise OperationBlocked(blocking_reason or "campaign_blocked")
        if local_enrollment:
            self._local_interactive_backends[campaign_id] = backend
        return self.get_campaign(campaign_id)

    @staticmethod
    def _fork_record(row):
        if row is None:
            return None
        record = dict(row)
        if record["schema_version"] != 1 or record["stage"] not in (
            "pre_fit_methods",
            "post_fit_explanation",
        ):
            raise OperationConflict("Invalid retained investigation fork")
        record["request"] = _retained_json(
            record.pop("request_json"), record["request_digest"]
        )
        return record

    def reserve_investigation_fork(
        self,
        campaign_id: str,
        *,
        fork_id: str,
        stage: str,
        child_ids: list[str],
        identity: dict,
    ):
        self._writable()
        _identity(campaign_id, fork_id, 0)
        if stage not in ("pre_fit_methods", "post_fit_explanation"):
            raise ValueError("Invalid investigation stage")
        if (
            type(child_ids) is not list
            or len(child_ids) > 2
            or len(set(child_ids)) != len(child_ids)
        ):
            raise ValueError("An investigation has zero, one or two distinct children")
        for child in child_ids:
            _identity(campaign_id, child, 0)
        if type(identity) is not dict:
            raise ValueError("Investigation identity must be a bounded public object")
        encoded = _json(
            {
                "fork_id": fork_id,
                "stage": stage,
                "child_ids": child_ids,
                "identity": identity,
            }
        )
        digest = _digest(encoded)
        with self._database() as connection:
            connection.execute("BEGIN IMMEDIATE")
            campaign = self._campaign(connection, campaign_id)
            existing = self._fork_record(
                connection.execute(
                    "SELECT * FROM investigations WHERE campaign_id=?", (campaign_id,)
                ).fetchone()
            )
            if existing:
                if existing["request_digest"] != digest:
                    raise OperationConflict(
                        "Only one frozen investigation fork is admitted per campaign"
                    )
                return existing
            self._dispatchable(connection, campaign)
            if (
                campaign["policy"]["kind"] == LOCAL_INTERACTIVE_CODEX
                or len(child_ids) > campaign["policy"]["max_children"]
            ):
                raise OperationBlocked("child_budget_exhausted")
            connection.execute(
                "INSERT INTO investigations VALUES (?,1,?,?,?,?)",
                (campaign_id, fork_id, stage, encoded, digest),
            )
            return self._fork_record(
                connection.execute(
                    "SELECT * FROM investigations WHERE campaign_id=?", (campaign_id,)
                ).fetchone()
            )

    def _dispatchable(self, connection, campaign):
        cancellation = connection.execute(
            "SELECT cancelled FROM workflows WHERE workflow_id=?",
            (campaign["campaign_id"],),
        ).fetchone()
        if campaign["status"] == "cancelled" or (cancellation and cancellation[0]):
            raise OperationBlocked("cancelled")
        if campaign["status"] != "open":
            raise OperationBlocked(campaign["reason"] or "campaign_blocked")
        if campaign["capability"].get(
            "backend"
        ) == "openai-responses-mediated" and not self._accepted_capability(
            campaign["capability"], campaign["policy"]
        ):
            raise OperationBlocked("current_mediated_authority_unavailable")
        if campaign["policy"][
            "kind"
        ] == LOCAL_INTERACTIVE_CODEX and not self._accepted_capability(
            campaign["capability"],
            campaign["policy"],
            campaign_id=campaign["campaign_id"],
        ):
            raise OperationBlocked("current_local_interactive_authority_unavailable")
        if campaign["policy"]["kind"] == "generation_jobs":
            if not self._accepted_capability(
                campaign["capability"], campaign["policy"]
            ):
                raise OperationBlocked("current_job_authority_unavailable")
            self._job_concurrency(connection, uncertain_only=True)
        if (
            time.time() - campaign["created_at"]
            >= campaign["policy"]["max_total_seconds"]
        ):
            raise OperationBlocked("campaign_deadline_exhausted")
        if self._totals(
            campaign, self._model_rows(connection, campaign["campaign_id"])
        )["unresolved_count"]:
            raise OperationBlocked("unresolved_usage")

    def _job_concurrency(self, connection, *, exclude_ref=None, uncertain_only=False):
        """One coordinator slot across all job campaigns in this ledger."""
        for row in connection.execute("SELECT * FROM model_campaigns"):
            campaign = self._campaign_row(row)
            if campaign["policy"]["kind"] != "generation_jobs":
                continue
            for record in self._model_rows(connection, campaign["campaign_id"]):
                if record["ref"] == exclude_ref:
                    continue
                if record["status"] == "uncertain":
                    raise OperationBlocked("unresolved_job_lifecycle")
                if not uncertain_only and record["status"] == "active":
                    raise OperationBlocked("job_concurrency_exhausted")

    def _local_interactive_slot(self, connection, *, exclude_ref=None):
        """Hold one local interactive invocation slot across this workspace."""
        for row in connection.execute("SELECT * FROM model_campaigns"):
            campaign = self._campaign_row(row)
            if campaign["policy"]["kind"] != LOCAL_INTERACTIVE_CODEX:
                continue
            for record in self._model_rows(connection, campaign["campaign_id"]):
                if record["ref"] == exclude_ref:
                    continue
                if record["status"] == "uncertain":
                    raise OperationBlocked("local_interactive_requires_reconciliation")
                if record["status"] in {"reserved", "active"}:
                    raise OperationBlocked("local_interactive_concurrency_exhausted")

    @contextmanager
    def publication_guard(self, campaign_id: str):
        """Linearize a bounded coordinator CAS against cancellation.

        The caller may perform its short scientific-state commit and read-only
        identity checks here. Model calls, retrieval and numerical work must
        remain outside this scope. This lock grants no actor permission.
        """
        self._writable()
        _identity(campaign_id, "publication", 0)
        with self._database() as connection:
            connection.execute("BEGIN IMMEDIATE")
            campaign = self._campaign(connection, campaign_id)
            self._dispatchable(connection, campaign)
            if not self._accepted_capability(
                campaign["capability"], campaign["policy"], campaign_id=campaign_id
            ):
                raise OperationBlocked("blocked_backend_capability")
            yield

    @staticmethod
    def _check_request(request, campaign):
        if type(request) is not dict:
            raise ValueError("Model request must be a bounded public object")
        _json(request)
        if request.get("parent_session_id") != campaign["session_id"]:
            raise OperationConflict(
                "Model request belongs to a different parent session"
            )
        _identity(request.get("session_id"), request.get("invocation_id"), 0)
        for name in (
            "backend_digest",
            "runtime_digest",
            "prompt_digest",
            "schema_digest",
            "context_digest",
        ):
            _hash(request.get(name))
        if any(
            request[key] != campaign["capability"][key]
            for key in ("backend_digest", "runtime_digest")
        ):
            raise OperationConflict(
                "Model request backend/runtime identity differs from admission"
            )

    def reserve_model_call(
        self,
        campaign_id: str,
        node: str,
        attempt: int,
        request: dict,
        *,
        limits: dict,
        purpose: str,
        child_id: str | None = None,
    ):
        self._writable()
        key = _identity(campaign_id, node, attempt)
        _identity(campaign_id, purpose, 0)
        if child_id is not None:
            _identity(campaign_id, child_id, 0)
            if purpose == "parent_reconciliation":
                raise OperationBlocked("child_cannot_reconcile_parent")
        limits = _limits(limits)
        request_json, limits_json = _json(request), _json(limits)
        digest, limits_digest = _digest(request_json), _digest(limits_json)
        with self._database() as connection:
            connection.execute("BEGIN IMMEDIATE")
            campaign = self._campaign(connection, campaign_id)
            self._check_request(request, campaign)
            if campaign["policy"]["kind"] == "generation_jobs":
                validate_job_limits(limits)
            elif "jobs" in limits:
                raise ValueError("Token policies cannot consume generation job limits")
            existing = self._model_record(
                connection.execute(
                    "SELECT * FROM model_reservations WHERE campaign_id=? AND node=?"
                    " AND attempt=?",
                    key,
                ).fetchone()
            )
            if existing:
                if (
                    existing["request_digest"] != digest
                    or existing["limits_digest"] != limits_digest
                    or existing["purpose"] != purpose
                    or existing["child_id"] != child_id
                ):
                    raise OperationConflict(
                        "Model operation key reused with different input or allocation"
                    )
                self._model_rows(connection, campaign_id)
                return self._reservation(existing, campaign)
            self._dispatchable(connection, campaign)
            records = self._model_rows(connection, campaign_id)
            if campaign["policy"]["kind"] == "generation_jobs":
                return self._reserve_job_call(
                    connection,
                    campaign,
                    key,
                    request,
                    limits,
                    purpose,
                    child_id,
                    request_json,
                    digest,
                    limits_json,
                    limits_digest,
                    records,
                )
            self._check_token_reservation(
                connection,
                campaign_id,
                campaign,
                request,
                limits,
                purpose,
                child_id,
                records,
            )
            return self._insert_model_reservation(
                connection,
                campaign,
                key,
                purpose,
                child_id,
                request_json,
                digest,
                limits_json,
                limits_digest,
            )

    @staticmethod
    def _check_token_parent_allocation(
        protected,
        request,
        limits,
        purpose,
        child_id,
        records,
    ):
        stage = request.get("purpose")
        protected_call = stage in protected if type(stage) is str else False
        if protected_call:
            if child_id is not None:
                raise OperationBlocked("child_cannot_consume_parent_reserve")
            allocated = protected[stage]
            consumed = [
                row
                for row in records
                if row["child_id"] is None
                and row["request"].get("purpose") == stage
                and row["charge_basis"] != "verified_unlaunched"
            ]
            if any(
                sum(row["limits"][key] for row in consumed) + limits[key]
                > allocated[key]
                for key in ("tokens", "calls", "seconds")
            ) or any(
                limits[key] > allocated[key] for key in ("context_bytes", "memory_mb")
            ):
                raise OperationBlocked("protected_parent_allocation_exceeded")
        elif protected and purpose == "parent_reconciliation":
            raise OperationBlocked("protected_parent_stage_not_allocated")
        return protected_call

    def _check_model_call_deadline(
        self,
        campaign,
        records,
        request,
        limits,
        purpose,
        child_id,
    ):
        remaining = (
            campaign["created_at"]
            + campaign["policy"]["max_total_seconds"]
            - time.time()
        )
        if limits["seconds"] > remaining:
            raise OperationBlocked("call_exceeds_campaign_deadline")
        future = self._future_parent_seconds(
            campaign,
            records,
            request=request,
            limits=limits,
            purpose=purpose,
            child_id=child_id,
        )
        if limits["seconds"] > remaining - future:
            raise OperationBlocked("call_exceeds_parent_headroom")

    @staticmethod
    def _check_model_invocation_identity(connection, request):
        collision = connection.execute(
            """SELECT ref FROM model_reservations
            WHERE json_extract(request_json,'$.session_id')=?
               OR json_extract(request_json,'$.invocation_id')=? LIMIT 1""",
            (request["session_id"], request["invocation_id"]),
        ).fetchone()
        if collision:
            raise OperationConflict(
                "Model session/invocation identity already belongs to "
                "another reservation"
            )

    def _check_token_reservation(
        self,
        connection,
        campaign_id,
        campaign,
        request,
        limits,
        purpose,
        child_id,
        records,
    ):
        policy = campaign["policy"]
        if policy["kind"] == LOCAL_INTERACTIVE_CODEX:
            self._check_local_interactive_reservation(
                connection,
                campaign,
                request,
                limits,
                purpose,
                child_id,
            )
        protected = _protected_allocations(campaign["policy"], campaign["identity"])
        protected_call = self._check_token_parent_allocation(
            protected,
            request,
            limits,
            purpose,
            child_id,
            records,
        )
        self._check_model_call_deadline(
            campaign,
            records,
            request,
            limits,
            purpose,
            child_id,
        )
        self._check_model_invocation_identity(connection, request)
        if child_id is not None:
            fork = self._fork_record(
                connection.execute(
                    "SELECT * FROM investigations WHERE campaign_id=?",
                    (campaign_id,),
                ).fetchone()
            )
            if not fork or child_id not in fork["request"]["child_ids"]:
                raise OperationBlocked("child_not_allocated")
            if any(row["child_id"] == child_id for row in records):
                raise OperationConflict(
                    "Allocated child already has its one model invocation"
                )
        if (
            limits["context_bytes"] > policy["max_context_bytes"]
            or limits["memory_mb"] > policy["memory_mb"]
        ):
            raise OperationBlocked("operational_limit_exceeded")
        totals = self._totals(campaign, records)
        held = dict(totals["reconciliation_held"])
        if protected_call or (not protected and purpose == "parent_reconciliation"):
            for dimension in ("tokens", "calls", "seconds"):
                held[dimension] = max(0, held[dimension] - limits[dimension])
        token_cap = (
            policy["max_total_tokens"]
            if policy["kind"] == "hard_total_tokens"
            else policy["soft_token_target"]
        )
        if (
            totals["verified_spent_tokens"]
            + totals["outstanding_reserved_tokens"]
            + held["tokens"]
            + limits["tokens"]
            > token_cap
            or totals["verified_calls"]
            + totals["outstanding_reserved_calls"]
            + held["calls"]
            + limits["calls"]
            > policy["max_calls"]
            or totals["verified_seconds"]
            + totals["outstanding_reserved_seconds"]
            + held["seconds"]
            + limits["seconds"]
            > policy["max_total_seconds"]
        ):
            raise OperationBlocked("budget_exhausted")

    def _check_local_interactive_reservation(
        self,
        connection,
        campaign,
        request,
        limits,
        purpose,
        child_id,
    ):
        if child_id is not None:
            raise OperationBlocked("child_budget_exhausted")
        if (
            purpose not in LOCAL_INTERACTIVE_PURPOSES
            or request.get("purpose") != purpose
            or request.get("role") != "controller"
        ):
            raise OperationBlocked("local_interactive_role_not_admitted")
        maximum = campaign["identity"]["parent_call_limits"]["default"]
        if limits["calls"] != 1 or any(limits[key] > maximum[key] for key in limits):
            raise OperationBlocked("operational_limit_exceeded")
        self._local_interactive_slot(connection)

    def _reserve_job_call(
        self,
        connection,
        campaign,
        key,
        request,
        limits,
        purpose,
        child_id,
        request_json,
        digest,
        limits_json,
        limits_digest,
        records,
    ):
        if child_id is not None:
            raise OperationBlocked("child_budget_exhausted")
        policy = campaign["policy"]
        protected = _protected_allocations(policy, campaign["identity"])
        stage = request.get("purpose")
        protected_call = type(stage) is str and stage in protected
        if protected_call:
            allocated = protected[stage]
            consumed = [
                row
                for row in records
                if row["child_id"] is None and row["request"].get("purpose") == stage
            ]
            if any(
                sum(row["limits"][dimension] for row in consumed) + limits[dimension]
                > allocated[dimension]
                for dimension in ("jobs", "seconds")
            ) or any(
                limits[dimension] > allocated[dimension]
                for dimension in ("context_bytes", "output_bytes")
            ):
                raise OperationBlocked("protected_parent_allocation_exceeded")
        elif protected and purpose == "parent_reconciliation":
            raise OperationBlocked("protected_parent_stage_not_allocated")
        if (
            limits["context_bytes"] > policy["max_context_bytes"]
            or limits["output_bytes"] > policy["max_output_bytes"]
        ):
            raise OperationBlocked("operational_limit_exceeded")
        self._check_model_call_deadline(
            campaign,
            records,
            request,
            limits,
            purpose,
            child_id,
        )
        totals = self._totals(campaign, records)
        held = dict(totals["reconciliation_held"])
        if protected_call or (not protected and purpose == "parent_reconciliation"):
            for dimension in ("jobs", "seconds"):
                held[dimension] = max(0, held[dimension] - limits[dimension])
        if (
            totals["verified_jobs"]
            + totals["outstanding_reserved_jobs"]
            + held["jobs"]
            + 1
            > policy["max_jobs"]
            or totals["verified_seconds"]
            + totals["outstanding_reserved_seconds"]
            + held["seconds"]
            + limits["seconds"]
            > policy["max_total_seconds"]
        ):
            raise OperationBlocked("budget_exhausted")
        self._check_model_invocation_identity(connection, request)
        return self._insert_model_reservation(
            connection,
            campaign,
            key,
            purpose,
            child_id,
            request_json,
            digest,
            limits_json,
            limits_digest,
        )

    def _insert_model_reservation(
        self,
        connection,
        campaign,
        key,
        purpose,
        child_id,
        request_json,
        digest,
        limits_json,
        limits_digest,
    ):
        if connection.execute(
            "SELECT 1 FROM operations WHERE workflow_id=? AND node=? AND attempt=?", key
        ).fetchone():
            raise OperationConflict(
                "Existing v1 operation cannot be treated as a new model grant"
            )
        connection.execute(
            "INSERT INTO operations VALUES (?,?,?,'active',?,?,NULL,NULL,NULL)",
            (*key, request_json, digest),
        )
        connection.execute(
            "INSERT INTO model_reservations VALUES "
            "(?,?,?,1,?,'reserved',?,?,?,?,?,?,NULL,NULL,NULL,'reserved_bound')",
            (
                *key,
                model_reservation_ref(*key),
                purpose,
                child_id,
                request_json,
                digest,
                limits_json,
                limits_digest,
            ),
        )
        record = self._model_record(
            connection.execute(
                "SELECT * FROM model_reservations WHERE campaign_id=? AND node=? AND"
                " attempt=?",
                key,
            ).fetchone()
        )
        return self._reservation(record, campaign)

    @staticmethod
    def _model_state(
        connection, record, state, *, capture=None, error_type=None, charge_basis=None
    ):
        key = (record["campaign_id"], record["node"], record["attempt"])
        encoded = _json(capture) if capture is not None else None
        digest = _digest(encoded) if encoded is not None else None
        changed = connection.execute(
            "UPDATE model_reservations SET "
            "status=?,capture_json=?,capture_digest=?,\n"
            "            error_type=?,charge_basis=? WHERE campaign_id=? AND node=?"
            " AND attempt=? AND status=? AND request_digest=?",
            (
                state,
                encoded,
                digest,
                error_type,
                charge_basis or record["charge_basis"],
                *key,
                record["status"],
                record["request_digest"],
            ),
        )
        operation_state = {
            "reserved": "active",
            "active": "active",
            "completed": "completed",
            "failed": "failed",
            "cancelled": "failed",
            "uncertain": "uncertain",
        }[state]
        changed_operation = connection.execute(
            "UPDATE operations SET "
            "status=?,result_json=?,result_digest=?,error_type=?\n"
            "            WHERE workflow_id=? AND node=? AND attempt=? AND "
            "request_digest=?",
            (
                operation_state,
                encoded,
                digest,
                error_type,
                *key,
                record["request_digest"],
            ),
        )
        if changed.rowcount != 1 or changed_operation.rowcount != 1:
            raise OperationConflict(
                "Model lifecycle transition did not update its exact reserved operation"
            )

    def activate_model_call(self, reservation):
        """Commit launch intent once. A surviving intent is uncertain, never retried."""
        self._writable()
        blocked = None
        with self._database() as connection:
            connection.execute("BEGIN IMMEDIATE")
            record, campaign = self._checked_reservation(connection, reservation)
            if record["status"] == "active":
                self._model_state(
                    connection, record, "uncertain", charge_basis="unresolved_launch"
                )
                blocked = "uncertain"
            elif record["status"] != "reserved":
                blocked = record["status"]
            else:
                self._dispatchable(connection, campaign)
                if self._role_execution_cancelled(connection, record):
                    raise OperationBlocked("scientific_cancelled")
                if campaign["policy"]["kind"] == "generation_jobs":
                    self._job_concurrency(connection, exclude_ref=record["ref"])
                elif campaign["policy"]["kind"] == LOCAL_INTERACTIVE_CODEX:
                    self._local_interactive_slot(connection, exclude_ref=record["ref"])
                future = self._future_parent_seconds(
                    campaign,
                    self._model_rows(connection, campaign["campaign_id"]),
                    record=record,
                )
                if (
                    time.time() + future
                    >= campaign["created_at"] + campaign["policy"]["max_total_seconds"]
                ):
                    raise OperationBlocked("protected_parent_headroom_exhausted")
                self._model_state(
                    connection, record, "active", charge_basis="launched_bound"
                )
                record["status"] = "active"
                result = self._reservation(record, campaign)
        if blocked:
            raise OperationBlocked(blocked)
        return result

    def mark_model_call_uncertain(self, reservation, *, error_type=None):
        self._writable()
        if error_type is not None and (
            type(error_type) is not str or not error_type or len(error_type) > 128
        ):
            raise ValueError("Model error type must be a bounded type name")
        with self._database() as connection:
            connection.execute("BEGIN IMMEDIATE")
            record, _ = self._checked_reservation(connection, reservation)
            if record["status"] in ("reserved", "active"):
                self._model_state(
                    connection,
                    record,
                    "uncertain",
                    error_type=error_type,
                    charge_basis="unresolved_launch",
                )
            elif record["status"] != "uncertain":
                raise OperationBlocked(record["status"])

    def cancel_unlaunched_model_call(self, reservation):
        """Retain a cancelled reservation whose launch was never activated."""
        self._writable()
        if not isinstance(reservation, ModelCallReservation):
            raise TypeError("Cancellation requires the exact model reservation")
        with self._database() as connection:
            connection.execute("BEGIN IMMEDIATE")
            record, _ = self._checked_reservation(connection, reservation)
            if record["status"] == "reserved":
                self._model_state(
                    connection, record, "cancelled", charge_basis="verified_unlaunched"
                )
            elif record["status"] != "cancelled":
                raise OperationBlocked(record["status"])

    def cancel_campaign(self, campaign_id: str):
        """Cancel future launch; an active process and its eventual charges survive."""
        self._writable()
        _identity(campaign_id, "cancel", 0)
        with self._database() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._campaign(connection, campaign_id)
            records = self._model_rows(connection, campaign_id)
            connection.execute(
                "INSERT INTO workflows VALUES (?,1) ON CONFLICT(workflow_id) DO "
                "UPDATE SET cancelled=1",
                (campaign_id,),
            )
            connection.execute(
                "UPDATE model_campaigns SET status='cancelled' WHERE campaign_id=?",
                (campaign_id,),
            )
            for record in records:
                if record["status"] == "reserved":
                    self._model_state(
                        connection,
                        record,
                        "cancelled",
                        charge_basis="verified_unlaunched",
                    )

    @staticmethod
    def _verified_capture(value, capture, reservation):
        if "jobs" in reservation.limits:
            try:
                _json(value)
                return validate_job_capture(value, capture, reservation)
            except (ValueError, TypeError, KeyError, OverflowError) as exc:
                raise OperationConflict(
                    "Invalid verified generation job capture"
                ) from exc
        fields = {
            "format",
            "reservation_ref",
            "request_digest",
            "session_id",
            "invocation_id",
            "backend_digest",
            "runtime_digest",
            "prompt_digest",
            "schema_digest",
            "context_digest",
            "status",
            "capture_ref",
            "usage",
            "actual_calls",
            "elapsed_seconds",
            "launch_confirmed",
        }
        fields.add("native_session_ids")
        if (
            type(value) is not dict
            or set(value) != fields
            or value["format"] != "devbench.model-call-capture/1"
        ):
            raise OperationConflict("Capture verifier returned an unsupported receipt")
        _json(value)
        try:
            if type(capture) is not dict or set(capture) != {
                "artifact_id",
                "sha256",
                "artifact_type",
                "size_bytes",
            }:
                raise ValueError("An immutable artifact reference is required")
            _identity(capture["artifact_id"], capture["artifact_type"], 0)
            _hash(capture["sha256"])
            _integer(
                capture["size_bytes"], "capture size", minimum=1, maximum=16_000_000
            )
        except (ValueError, TypeError) as exc:
            raise OperationConflict(
                "Invalid immutable model capture reference"
            ) from exc
        if (
            value["reservation_ref"] != reservation.ref
            or value["request_digest"] != reservation.request_digest
            or value["capture_ref"] != capture
            or any(
                value[key] != reservation.request[key]
                for key in (
                    "session_id",
                    "invocation_id",
                    "backend_digest",
                    "runtime_digest",
                    "prompt_digest",
                    "schema_digest",
                    "context_digest",
                )
            )
        ):
            raise OperationConflict(
                "Verified capture does not match the exact reserved invocation"
            )
        if (
            value["status"] not in ("completed", "failed", "cancelled")
            or type(value["launch_confirmed"]) is not bool
        ):
            raise OperationConflict("Verified capture has an invalid terminal state")
        sessions = value["native_session_ids"]
        if (
            type(sessions) is not list
            or len(sessions) > 1024
            or any(
                type(session) is not str
                or not session
                or len(session.encode("utf-8")) > 256
                for session in sessions
            )
            or len(sessions) != len(set(sessions))
            or (value["launch_confirmed"] and not sessions)
            or (not value["launch_confirmed"] and sessions)
        ):
            raise OperationConflict(
                "Verified capture lacks distinct actual native session identities"
            )
        WorkflowOperationLedger._verify_token_capture_usage(value, sessions)
        return json.loads(_json(value))

    @staticmethod
    def _verify_token_capture_usage(value, sessions):
        usage = value["usage"]
        usage_fields = {
            "normalization",
            "native_usage",
            "normalized_total_tokens",
            "input_tokens",
            "output_tokens",
            "cached_input_tokens",
            "reasoning_tokens",
        }
        if (
            type(usage) is not dict
            or set(usage) != usage_fields
            or usage["normalization"] != "input_output_subsets/1"
            or type(usage["native_usage"]) is not dict
        ):
            raise OperationConflict("Unverified model usage normalization")
        try:
            if usage != normalize_usage(usage["native_usage"]):
                raise ValueError(
                    "Receipt usage differs from canonical native normalization"
                )
            for key in usage_fields - {"normalization", "native_usage"}:
                if usage[key] is not None:
                    _integer(usage[key], key, maximum=128_000_000_000)
            _integer(value["actual_calls"], "actual_calls", maximum=1024)
            _seconds(value["elapsed_seconds"], "elapsed_seconds")
            if value["launch_confirmed"] and (
                value["actual_calls"] < 1 or value["actual_calls"] < len(sessions)
            ):
                raise ValueError(
                    "Actual model calls understate observed native sessions"
                )
            if not value["launch_confirmed"] and (
                value["actual_calls"] != 0
                or usage["normalized_total_tokens"] != 0
                or value["status"] == "completed"
            ):
                raise ValueError("Unlaunched capture cannot claim model work")
        except (ValueError, TypeError) as exc:
            raise OperationConflict("Invalid verified model usage") from exc

    def settle_model_call(self, reservation, *, capture, verify_capture: Callable):
        """Settle only from an exact retained capture, never a claimed response."""
        self._writable()
        if not callable(verify_capture):
            raise TypeError("A trusted capture verifier is required")
        record = self.get_model_reservation(reservation)
        key = (record["campaign_id"], record["node"], record["attempt"])
        with self._operation_lock(key):
            with self._database() as connection:
                retained, campaign = self._checked_reservation(connection, reservation)
                typed = self._reservation(retained, campaign)
            try:
                verified = self._verified_capture(
                    verify_capture(capture, typed), capture, typed
                )
            except BaseException:
                if retained["status"] in ("reserved", "active"):
                    self.mark_model_call_uncertain(
                        typed, error_type="CaptureVerificationFailed"
                    )
                raise
            try:
                return self._commit_model_capture(typed, verified)
            except BaseException:
                # Acknowledgement loss can occur on either side of COMMIT. Reuse
                # a verified terminal row; otherwise retain its entire lease.
                try:
                    current = self.get_model_reservation(typed)
                    if current["status"] in ("reserved", "active"):
                        self.mark_model_call_uncertain(
                            typed, error_type="SettlementCommitUncertain"
                        )
                except (OperationConflict, OperationBlocked):
                    pass
                raise

    def _commit_model_capture(self, reservation, verified):
        encoded = _json(verified)
        key = (reservation.campaign_id, reservation.node, reservation.attempt)
        with self._database() as connection:
            connection.execute("BEGIN IMMEDIATE")
            retained, campaign = self._checked_reservation(connection, reservation)
            if campaign["policy"]["kind"] == "generation_jobs":
                return self._commit_job_capture(
                    connection, reservation, retained, campaign, verified
                )
            if retained["status"] in ("completed", "failed", "cancelled"):
                if retained["capture_digest"] != _digest(encoded):
                    raise OperationConflict("Conflicting verified model settlement")
                return retained
            if retained["status"] == "reserved" and verified["launch_confirmed"]:
                raise OperationConflict(
                    "Capture claims launch before durable activation"
                )
            _capture_progress(retained["capture"], verified)
            sessions = set(verified["native_session_ids"])
            for row in connection.execute(
                """SELECT capture_json,capture_digest FROM model_reservations
                    WHERE ref<>? AND capture_json IS NOT NULL""",
                (reservation.ref,),
            ):
                previous = _retained_json(row["capture_json"], row["capture_digest"])
                if sessions.intersection(previous["native_session_ids"]):
                    raise OperationConflict(
                        "Captured native session is reused across model invocations"
                    )
            tokens = verified["usage"]["normalized_total_tokens"]
            floor = _token_floor(verified["usage"])
            state = verified["status"] if tokens is not None else "uncertain"
            self._model_state(
                connection,
                retained,
                state,
                capture=verified,
                charge_basis="verified_actual"
                if tokens is not None
                else "unresolved_usage",
            )
            limits, policy = retained["limits"], campaign["policy"]
            violation = (
                verified["actual_calls"] > limits["calls"]
                or verified["elapsed_seconds"] > limits["seconds"]
                or (policy["kind"] == "hard_total_tokens" and floor > limits["tokens"])
                or (
                    verified["launch_confirmed"]
                    and time.time()
                    > campaign["created_at"] + policy["max_total_seconds"]
                )
            )
            totals = self._totals(
                campaign, self._model_rows(connection, campaign["campaign_id"])
            )
            reason = (
                "backend_limit_violation"
                if violation
                else "soft_token_target_exceeded"
                if policy["kind"]
                in ("bounded_observed_tokens", LOCAL_INTERACTIVE_CODEX)
                and totals["verified_token_lower_bound"] > policy["soft_token_target"]
                else None
            )
            if reason:
                connection.execute(
                    "UPDATE model_campaigns SET status=CASE WHEN status='cancelled' "
                    "THEN status ELSE 'blocked' END,reason=? WHERE campaign_id=?",
                    (reason, campaign["campaign_id"]),
                )
            result = self._model_record(
                connection.execute(
                    "SELECT * FROM model_reservations WHERE campaign_id=? AND node=?"
                    " AND attempt=?",
                    key,
                ).fetchone()
            )
        return result

    def _commit_job_capture(
        self, connection, reservation, retained, campaign, verified
    ):
        encoded = _json(verified)
        if retained["status"] in ("completed", "failed", "cancelled"):
            if retained["capture_digest"] != _digest(encoded):
                raise OperationConflict(
                    "Conflicting verified generation job settlement"
                )
            return retained
        if retained["status"] == "reserved":
            raise OperationConflict(
                "Generation job capture precedes durable launch intent"
            )
        previous = retained["capture"]
        if previous is not None:
            if (
                previous["launch_confirmed"]
                and not verified["launch_confirmed"]
                or previous["process_reaped"]
                and not verified["process_reaped"]
                or not set(previous["native_session_ids"]).issubset(
                    verified["native_session_ids"]
                )
                or verified["elapsed_seconds"] < previous["elapsed_seconds"]
                or (
                    "response_sha256" in previous
                    and verified.get("response_sha256") != previous["response_sha256"]
                )
                or (
                    "usage" in previous
                    and not self._job_usage_progress(
                        previous["usage"], verified.get("usage")
                    )
                )
            ):
                raise OperationConflict(
                    "Reconciliation cannot erase observed generation job work"
                )
        sessions = set(verified["native_session_ids"])
        for row in connection.execute(
            """SELECT capture_json,capture_digest FROM model_reservations
                WHERE ref<>? AND capture_json IS NOT NULL""",
            (reservation.ref,),
        ):
            other = _retained_json(row["capture_json"], row["capture_digest"])
            if sessions.intersection(other["native_session_ids"]):
                raise OperationConflict(
                    "Captured native session is reused across model invocations"
                )
        # Token availability has no bearing on the observed local lifecycle.
        known_terminal = (
            verified["status"] in ("completed", "failed", "not_started")
            and verified["process_reaped"]
        )
        state = (
            ("failed" if verified["status"] == "not_started" else verified["status"])
            if known_terminal
            else "uncertain"
        )
        self._model_state(
            connection,
            retained,
            state,
            capture=verified,
            charge_basis="verified_launch_intent"
            if known_terminal
            else "unresolved_job_lifecycle",
        )
        limits = retained["limits"]
        provenance = verified["provenance"]
        violation = (
            verified["elapsed_seconds"] > limits["seconds"]
            or provenance.get("context_bytes", 0) > limits["context_bytes"]
            or provenance.get("output_bytes", 0) > limits["output_bytes"]
        )
        if violation:
            connection.execute(
                "UPDATE model_campaigns SET status=CASE WHEN status='cancelled' "
                "THEN status ELSE 'blocked' END,\n"
                "                reason='backend_limit_violation' WHERE "
                "campaign_id=?",
                (campaign["campaign_id"],),
            )
        return self._model_record(
            connection.execute(
                "SELECT * FROM model_reservations WHERE campaign_id=? AND node=? AND"
                " attempt=?",
                (reservation.campaign_id, reservation.node, reservation.attempt),
            ).fetchone()
        )

    @staticmethod
    def _job_usage_progress(previous, current):
        if type(current) is not dict:
            return False
        for key, value in previous.items():
            later = current.get(key)
            if value is None:
                continue  # An unavailable optional measurement may become known.
            if type(value) is dict:
                if not WorkflowOperationLedger._job_usage_progress(value, later):
                    return False
            elif type(value) in (int, float):
                if type(later) not in (int, float) or later < value:
                    return False
            elif later != value:
                return False
        return True

    def reconcile_model_call(self, reservation, *, capture, verify_capture: Callable):
        """Explicit exact-capture reconciliation; performs no model dispatch."""
        return self.settle_model_call(
            reservation, capture=capture, verify_capture=verify_capture
        )
