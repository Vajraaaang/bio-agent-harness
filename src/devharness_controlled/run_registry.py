"""Bounded durable local dispatch records, not an authenticated clinical ledger.

SQLite FULL synchronous transactions and append-only triggers protect normal API
use. The owner can replace this database, its locks, or its complete history.
Retain a checkpoint independently to detect such replacement. Actor IDs are
caller assertions, never evidence of human identity or electronic signatures.
Leases coordinate cooperating processes on a local filesystem only; they neither
authenticate processes nor grant authority to signal a PID or recover a container.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import os
from pathlib import Path
import re
import sqlite3
import stat
import unicodedata
from urllib.parse import quote
import uuid

from .contracts import canonical_json_bytes, parse_json_bytes
from .sqlite_schema import require_exact_schema

MAX_ATTEMPTS = 10_000
MAX_PAYLOAD_BYTES = 65_536
MAX_DATABASE_BYTES = 128 * 1024 * 1024
MAX_EVENTS_PER_ATTEMPT = 7
_TERMINAL = {"completed", "failed", "interrupted"}
_STAGES = ("registered", "started", "runtime_intent", "container_observed", "launch_authorized", "start_requested")
_TRANSITIONS = {stage: {"failed", "interrupted", _STAGES[index + 1] if index + 1 < len(_STAGES) else "completed"}
                for index, stage in enumerate(_STAGES)}
_SCHEMA = {
    "registry_metadata": "CREATE TABLE registry_metadata (singleton INTEGER PRIMARY KEY CHECK(singleton=1), schema_version TEXT NOT NULL CHECK(schema_version='local_run_registry/1'), registry_id TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL)",
    "attempts": "CREATE TABLE attempts (attempt_seq INTEGER PRIMARY KEY, attempt_id TEXT NOT NULL UNIQUE, request_id TEXT NOT NULL UNIQUE, plan_sha256 TEXT NOT NULL, submission_sha256 TEXT NOT NULL, workspace TEXT NOT NULL UNIQUE, workspace_key TEXT NOT NULL UNIQUE, actor_id TEXT NOT NULL, created_at TEXT NOT NULL)",
    "events": "CREATE TABLE events (attempt_id TEXT NOT NULL REFERENCES attempts(attempt_id), sequence INTEGER NOT NULL CHECK(sequence BETWEEN 1 AND 7), kind TEXT NOT NULL CHECK(kind IN ('registered','started','runtime_intent','container_observed','launch_authorized','start_requested','completed','failed','interrupted')), payload_json TEXT NOT NULL, actor_id TEXT NOT NULL, recorded_at TEXT NOT NULL, previous_hash TEXT, event_hash TEXT NOT NULL UNIQUE, PRIMARY KEY(attempt_id,sequence))",
}
for _table in ("registry_metadata", "attempts", "events"):
    for _action in ("UPDATE", "DELETE"):
        _name = f"{_table}_no_{_action.lower()}"
        _SCHEMA[_name] = (f"CREATE TRIGGER {_name} BEFORE {_action} ON {_table} "
                          "BEGIN SELECT RAISE(ABORT,'append-only registry'); END")


class LeaseBusyError(RuntimeError):
    """Another cooperating process currently holds this attempt's lease."""


def _text(value, label, maximum=256):
    if (type(value) is not str or not value.strip() or len(value.encode("utf-8")) > maximum
            or any(ord(char) < 32 or ord(char) == 127 for char in value)):
        raise ValueError(f"{label} must be nonblank bounded text without control characters")
    return value


def _digest(value, label="digest"):
    if type(value) is not str or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ValueError(f"{label} must be a lowercase SHA-256 digest")
    return value


def _identifier(value):
    try:
        if type(value) is not str or uuid.UUID(value).hex != value or uuid.UUID(value).version != 4:
            raise ValueError()
    except (ValueError, AttributeError) as error:
        raise ValueError("Registry IDs must be canonical lowercase UUID4 hex") from error
    return value


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _timestamp(value):
    if type(value) is not str or not re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{6}Z", value):
        raise ValueError("Noncanonical registry timestamp")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("Invalid registry timestamp") from error


def _path(value):
    raw = os.fspath(value)
    if not isinstance(raw, str) or not Path(raw).is_absolute():
        raise ValueError("Registry and workspace paths must be absolute")
    _text(raw, "path", 4096)
    path = Path(raw)
    if str(path) != raw or path.resolve(strict=False) != path:
        raise ValueError("Paths must be canonical and contain no symlink components")
    # macOS resolve() does not recover actual case on case-insensitive volumes.
    # F_GETPATH obtains the kernel's spelling of the closest existing ancestor.
    if hasattr(fcntl, "F_GETPATH"):
        existing = path
        while not existing.exists():
            existing = existing.parent
        descriptor = os.open(existing, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
        try:
            actual = os.fsdecode(fcntl.fcntl(descriptor, fcntl.F_GETPATH, b"\0" * 1024).split(b"\0", 1)[0])
        finally:
            os.close(descriptor)
        if actual != str(existing):
            raise ValueError("Paths must use canonical filesystem spelling")
    return path


def _workspace_key(workspace):
    # Conservatively reject case/normalization aliases even before a workspace
    # exists. This is also enforced on case-sensitive systems for portability.
    return unicodedata.normalize("NFC", workspace).casefold()


def _private_directory(path):
    info = path.lstat()
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o700):
        raise ValueError("Registry directory must be owned by the current user with mode 0700")
    return info


def _private_file(path, *, missing=False, maximum=MAX_DATABASE_BYTES):
    try:
        info = path.lstat()
    except FileNotFoundError:
        if missing:
            return None
        raise ValueError("Required registry file is missing") from None
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o600 or info.st_size > maximum):
        raise ValueError("Registry files must be bounded owned mode-0600 regular files without symlinks or hard links")
    return info


def _payload(value):
    if type(value) is not dict:
        raise ValueError("Event payload must be a JSON object")
    try:
        encoded = canonical_json_bytes(value)
    except RecursionError as error:
        raise ValueError("Event payload nesting is too deep") from error
    if len(encoded) > MAX_PAYLOAD_BYTES:
        raise ValueError("Event payload exceeds 65536-byte limit")
    return encoded.decode("utf-8")


class RunRegistry:
    """An explicitly selected, current-user private local SQLite registry.

    Construction may recover a SQLite hot journal after a crash. Read methods
    subsequently use read-only connections; they never claim to repair evidence.
    The registry accepts at most 10000 attempts, 7 events per attempt, and 64 KiB
    per payload, subject also to its 128 MiB database bound. Use another explicit
    registry when capacity is exhausted; no historical rows are purged.
    """
    def __init__(self, directory):
        self.directory = _path(directory)
        try:
            self.directory.mkdir(mode=0o700)
        except FileExistsError:
            pass
        info = _private_directory(self.directory)
        self._directory_identity = (info.st_dev, info.st_ino)
        self.database_path = self.directory / "runs.sqlite3"
        self.lease_directory = self.directory / "leases"
        try:
            self.lease_directory.mkdir(mode=0o700)
        except FileExistsError:
            pass
        lease_info = _private_directory(self.lease_directory)
        self._lease_directory_identity = (lease_info.st_dev, lease_info.st_ino)
        try:
            descriptor = os.open(self.database_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY
                                 | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        except FileExistsError:
            pass
        else:
            os.fsync(descriptor)
            os.close(descriptor)
            self._sync_directory()
        with self._connection(write=True, initializing=True) as connection:
            existing = connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='registry_metadata'").fetchone()
            if existing is None:
                if connection.execute("SELECT name FROM sqlite_master LIMIT 1").fetchone():
                    raise ValueError("Unrecognized registry schema")
                for sql in _SCHEMA.values():
                    connection.execute(sql)
                connection.execute("INSERT INTO registry_metadata VALUES (1,?,?,?)",
                                   ("local_run_registry/1", uuid.uuid4().hex, _now()))
            self._schema(connection)
        self._sync_directory()

    def _sync_directory(self):
        descriptor = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    @property
    def registry_id(self):
        """Durable UUID4 hex identifier of this local registry, not its operator."""
        with self._connection() as connection:
            return self._schema(connection)["registry_id"]

    def _paths(self):
        if _path(self.directory) != self.directory:
            raise ValueError("Registry path changed")
        info = _private_directory(self.directory)
        if (info.st_dev, info.st_ino) != self._directory_identity:
            raise ValueError("Registry directory was replaced")
        lease_info = _private_directory(self.lease_directory)
        if (lease_info.st_dev, lease_info.st_ino) != self._lease_directory_identity:
            raise ValueError("Registry lease directory was replaced")
        _private_file(self.database_path)
        for suffix in ("-journal", "-wal", "-shm"):
            _private_file(Path(str(self.database_path) + suffix), missing=True)

    @contextmanager
    def _connection(self, *, write=False, initializing=False):
        self._paths()
        mode = "rw" if write else "ro"
        connection = sqlite3.connect(f"file:{quote(str(self.database_path), safe='/')}?mode={mode}",
                                     uri=True, timeout=10, isolation_level=None)
        connection.row_factory = sqlite3.Row
        try:
            connection.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 1024 * 1024)
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA trusted_schema=OFF")
            if write:
                connection.execute("PRAGMA synchronous=FULL")
                # DELETE avoids persistent WAL/shm credentials or unbounded WAL growth.
                if connection.execute("PRAGMA journal_mode").fetchone()[0] != "delete":
                    raise ValueError("Registry requires DELETE journal mode")
                page_size = connection.execute("PRAGMA page_size").fetchone()[0]
                connection.execute(f"PRAGMA max_page_count={MAX_DATABASE_BYTES // page_size}")
                connection.execute("BEGIN IMMEDIATE")
            else:
                connection.execute("PRAGMA query_only=ON")
                connection.execute("BEGIN")
            if not initializing:
                self._schema(connection)
            yield connection
            self._paths()
            connection.execute("COMMIT")
        except BaseException:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        finally:
            connection.close()

    @staticmethod
    def _schema(connection):
        require_exact_schema(connection,_SCHEMA)
        metadata = connection.execute("SELECT * FROM registry_metadata LIMIT 2").fetchall()
        if len(metadata) != 1 or metadata[0]["schema_version"] != "local_run_registry/1":
            raise ValueError("Invalid registry metadata")
        _identifier(metadata[0]["registry_id"])
        _timestamp(metadata[0]["created_at"])
        return dict(metadata[0])

    @staticmethod
    def _event_document(registry_id, attempt_id, sequence, kind, payload, actor_id, recorded_at, previous_hash):
        return {"schema_version": "local_run_event/1", "registry_id": registry_id,
                "attempt_id": attempt_id, "sequence": sequence, "kind": kind,
                "payload": payload, "actor_id": actor_id, "recorded_at": recorded_at,
                "previous_hash": previous_hash}

    def _insert_event(self, connection, attempt_id, kind, payload, actor_id, recorded_at, prior=None):
        registry_id = self._schema(connection)["registry_id"]
        sequence = 1 if prior is None else prior["sequence"] + 1
        previous_hash = None if prior is None else prior["event_hash"]
        document = self._event_document(registry_id, attempt_id, sequence, kind, payload, actor_id, recorded_at, previous_hash)
        event_hash = hashlib.sha256(canonical_json_bytes(document)).hexdigest()
        connection.execute("INSERT INTO events VALUES (?,?,?,?,?,?,?,?)",
                           (attempt_id, sequence, kind, _payload(payload), actor_id, recorded_at, previous_hash, event_hash))
        return {**document, "event_hash": event_hash}

    def _attempt(self, connection, attempt_id):
        row = connection.execute("SELECT * FROM attempts WHERE attempt_id=?", (attempt_id,)).fetchone()
        if row is None:
            raise KeyError(f"Unknown attempt: {attempt_id}")
        attempt = dict(row)
        _identifier(attempt["attempt_id"])
        _text(attempt["request_id"], "request_id")
        _text(attempt["actor_id"], "actor_id")
        _digest(attempt["plan_sha256"], "plan_sha256")
        _digest(attempt["submission_sha256"], "submission_sha256")
        if type(attempt["attempt_seq"]) is not int or attempt["attempt_seq"] < 1:
            raise ValueError("Invalid attempt sequence")
        # Historical path syntax is verified without requiring historical data to exist.
        workspace = attempt["workspace"]
        if type(workspace) is not str or not Path(workspace).is_absolute() or str(Path(workspace)) != workspace or ".." in Path(workspace).parts:
            raise ValueError("Invalid recorded workspace")
        if attempt["workspace_key"] != _workspace_key(workspace):
            raise ValueError("Invalid recorded workspace key")
        previous_time = _timestamp(attempt["created_at"])
        metadata = self._schema(connection)
        if previous_time < _timestamp(metadata["created_at"]):
            raise ValueError("Registry attempt chronology precedes registry creation")
        registry_id = metadata["registry_id"]
        rows = connection.execute("SELECT * FROM events WHERE attempt_id=? ORDER BY sequence LIMIT ?", (attempt_id, MAX_EVENTS_PER_ATTEMPT + 1)).fetchall()
        if not rows or len(rows) > MAX_EVENTS_PER_ATTEMPT:
            raise ValueError("Invalid registry event count")
        events = []
        for sequence, event in enumerate(rows, 1):
            payload = parse_json_bytes(event["payload_json"].encode("utf-8"))
            if _payload(payload) != event["payload_json"]:
                raise ValueError("Noncanonical registry event payload")
            if event["sequence"] != sequence:
                raise ValueError("Registry event sequence is incomplete")
            _text(event["actor_id"], "actor_id")
            when = _timestamp(event["recorded_at"])
            if when < previous_time:
                raise ValueError("Registry event chronology is invalid")
            if sequence == 1:
                expected = {"request_id": attempt["request_id"], "plan_sha256": attempt["plan_sha256"], "submission_sha256": attempt["submission_sha256"], "workspace": workspace}
                if (event["kind"] != "registered" or payload != expected or event["actor_id"] != attempt["actor_id"] or event["recorded_at"] != attempt["created_at"]):
                    raise ValueError("Registry registered event does not bind the attempt")
            elif event["kind"] not in _TRANSITIONS.get(events[-1]["kind"], set()):
                raise ValueError("Invalid registry lifecycle transition")
            if event["kind"] == "completed":
                _digest(payload.get("execution_root_hash"), "execution root")
            previous_hash = None if sequence == 1 else events[-1]["event_hash"]
            if event["previous_hash"] != previous_hash:
                raise ValueError("Registry previous hash mismatch")
            document = self._event_document(registry_id, attempt_id, sequence, event["kind"], payload, event["actor_id"], event["recorded_at"], previous_hash)
            expected_hash = hashlib.sha256(canonical_json_bytes(document)).hexdigest()
            if event["event_hash"] != expected_hash:
                raise ValueError("Registry event hash mismatch")
            events.append({**document, "event_hash": expected_hash})
            previous_time = when
        return {**attempt, "status": events[-1]["kind"], "events": events,
                "event_root_hash": events[-1]["event_hash"], "identity_scope": "caller_asserted_operator"}

    def register(self, request_id, plan_sha256, workspace, actor_id, *, submission_sha256):
        """Atomically return one attempt for a request, binding plan and workspace.

        A retry never updates the first actor attribution. A different caller may
        look up the same request; this is local idempotency, not authorization.
        Workspace uniqueness also rejects NFC/casefold aliases on all platforms,
        including not-yet-created paths and case-sensitive filesystems.
        """
        request_id = _text(request_id, "request_id")
        actor_id = _text(actor_id, "actor_id")
        plan_sha256 = _digest(plan_sha256, "plan_sha256")
        submission_sha256 = _digest(submission_sha256, "submission_sha256")
        workspace = str(_path(workspace))
        with self._connection(write=True) as connection:
            existing = connection.execute("SELECT attempt_id,plan_sha256,submission_sha256,workspace FROM attempts WHERE request_id=?", (request_id,)).fetchone()
            if existing:
                if existing["plan_sha256"] != plan_sha256 or existing["submission_sha256"] != submission_sha256 or existing["workspace"] != workspace:
                    raise ValueError("request_id conflict: plan, submission or workspace differs")
                return {**self._attempt(connection, existing["attempt_id"]), "created": False}
            if connection.execute("SELECT 1 FROM attempts WHERE workspace_key=?", (_workspace_key(workspace),)).fetchone():
                raise ValueError("workspace is already bound to another attempt")
            if connection.execute("SELECT COUNT(*) FROM attempts").fetchone()[0] >= MAX_ATTEMPTS:
                raise ValueError("Registry attempt capacity is exhausted")
            attempt_id, recorded_at = uuid.uuid4().hex, _now()
            connection.execute("INSERT INTO attempts (attempt_id,request_id,plan_sha256,submission_sha256,workspace,workspace_key,actor_id,created_at) VALUES (?,?,?,?,?,?,?,?)", (attempt_id, request_id, plan_sha256, submission_sha256, workspace, _workspace_key(workspace), actor_id, recorded_at))
            self._insert_event(connection, attempt_id, "registered", {"request_id": request_id, "plan_sha256": plan_sha256, "submission_sha256": submission_sha256, "workspace": workspace}, actor_id, recorded_at)
            return {**self._attempt(connection, attempt_id), "created": True}

    def get(self, attempt_id):
        """Return a hash-verified attempt and its bounded event history."""
        _identifier(attempt_id)
        with self._connection() as connection:
            return self._attempt(connection, attempt_id)

    def list_attempts(self, limit=100, after_attempt_id=None):
        """Return up to 1000 verified summaries in registration order."""
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("limit must be an integer between 1 and 1000")
        with self._connection() as connection:
            after_sequence = 0
            if after_attempt_id is not None:
                _identifier(after_attempt_id)
                after_sequence = self._attempt(connection, after_attempt_id)["attempt_seq"]
            rows = connection.execute("SELECT attempt_id FROM attempts WHERE attempt_seq>? ORDER BY attempt_seq LIMIT ?", (after_sequence, limit)).fetchall()
            return [{key: value for key, value in self._attempt(connection, row["attempt_id"]).items() if key != "events"} for row in rows]

    def append_event(self, attempt_id, kind, payload, actor_id):
        """Append an allowed transition, or return an identical terminal commit."""
        _identifier(attempt_id)
        _text(actor_id, "actor_id")
        if type(kind) is not str or kind not in {*_STAGES[1:], *_TERMINAL}:
            raise ValueError("Unsupported event kind")
        payload_json = _payload(payload)
        # Freeze mutable caller data before acquiring the transaction.
        payload = parse_json_bytes(payload_json.encode("utf-8"))
        with self._connection(write=True) as connection:
            attempt = self._attempt(connection, attempt_id)
            prior = attempt["events"][-1]
            if prior["kind"] in _TERMINAL:
                if prior["kind"] == kind and canonical_json_bytes(prior["payload"]) == payload_json.encode("utf-8") and prior["actor_id"] == actor_id:
                    return prior
                raise ValueError("Attempt is terminal; conflicting terminal commits are forbidden")
            if kind not in _TRANSITIONS[prior["kind"]]:
                raise ValueError("Illegal registry lifecycle transition")
            if kind == "completed":
                _digest(payload.get("execution_root_hash"), "execution root")
            recorded_at = _now()
            if _timestamp(recorded_at) < _timestamp(prior["recorded_at"]):
                raise ValueError("Clock moved backwards; registry transition refused")
            return self._insert_event(connection, attempt_id, kind, payload, actor_id, recorded_at, prior)

    def _checkpoint(self, connection):
        metadata = self._schema(connection)
        check = connection.execute("PRAGMA quick_check(1)").fetchall()
        if len(check) != 1 or check[0][0] != "ok" or connection.execute("PRAGMA foreign_key_check").fetchone():
            raise ValueError("SQLite registry integrity check failed")
        rows = connection.execute("SELECT attempt_id FROM attempts ORDER BY attempt_seq LIMIT ?", (MAX_ATTEMPTS + 1,)).fetchall()
        if len(rows) > MAX_ATTEMPTS:
            raise ValueError("Registry attempt count exceeds its bound")
        attempts, event_count = [], 0
        for sequence, row in enumerate(rows, 1):
            attempt = self._attempt(connection, row["attempt_id"])
            if attempt["attempt_seq"] != sequence:
                raise ValueError("Registry attempt sequence is incomplete")
            event_count += len(attempt["events"])
            attempts.append({"attempt_id": attempt["attempt_id"], "attempt_seq": attempt["attempt_seq"],
                             "event_count": len(attempt["events"]), "event_root_hash": attempt["event_root_hash"]})
        if connection.execute("SELECT COUNT(*) FROM events").fetchone()[0] != event_count:
            raise ValueError("Registry contains unbound events")
        document = {"schema_version": "local_run_checkpoint/1", "registry_id": metadata["registry_id"],
                    "registry_created_at": metadata["created_at"], "attempt_count": len(attempts),
                    "event_count": event_count, "attempts": attempts,
                    "identity_scope": "caller_asserted_operator", "clinical_release_allowed": False}
        return {**document, "checkpoint_sha256": hashlib.sha256(canonical_json_bytes(document)).hexdigest()}

    def export_checkpoint(self):
        """Return a verified JSON-serializable checkpoint without writing files.

        Valid later appends change the current digest but preserve this anchored
        prefix. Store the checkpoint independently to anchor historical integrity.
        """
        with self._connection() as connection:
            return self._checkpoint(connection)

    def verify_integrity(self, expected_checkpoint=None):
        """Verify current chains and optionally preserve an external historical prefix.

        Later valid events and attempts are permitted. A successful anchored check
        attests only that the supplied checkpoint is preserved, not that later
        events were independently retained or authenticated.
        """
        with self._connection() as connection:
            checkpoint = self._checkpoint(connection)
            if expected_checkpoint is not None:
                self._verify_checkpoint_prefix(connection, checkpoint, expected_checkpoint)
        return {"valid": True, "checkpoint_sha256": checkpoint["checkpoint_sha256"],
                "attempt_count": checkpoint["attempt_count"], "event_count": checkpoint["event_count"],
                "externally_anchored": expected_checkpoint is not None,
                "anchored_checkpoint_sha256": None if expected_checkpoint is None else expected_checkpoint["checkpoint_sha256"],
                "later_event_count": None if expected_checkpoint is None else checkpoint["event_count"] - expected_checkpoint["event_count"],
                "later_attempt_count": None if expected_checkpoint is None else checkpoint["attempt_count"] - expected_checkpoint["attempt_count"],
                "identity_authenticated": False, "clinical_release_allowed": False}

    @staticmethod
    def _verify_checkpoint_prefix(connection, current, expected):
        def reject():
            raise ValueError("Registry checkpoint prefix mismatch or malformed checkpoint")

        if type(expected) is not dict or set(expected) != set(current):
            reject()
        # External snapshots are at most ~2 MiB at the registry's attempt bound.
        raw = canonical_json_bytes(expected)
        if len(raw) > 4 * 1024 * 1024:
            reject()
        body = {key: value for key, value in expected.items() if key != "checkpoint_sha256"}
        if hashlib.sha256(canonical_json_bytes(body)).hexdigest() != expected["checkpoint_sha256"]:
            reject()
        for key in ("schema_version", "registry_id", "registry_created_at", "identity_scope"):
            if expected[key] != current[key]:
                reject()
        if expected["clinical_release_allowed"] is not False:
            reject()
        if (type(expected["attempt_count"]) is not int or type(expected["event_count"]) is not int
                or not 0 <= expected["attempt_count"] <= current["attempt_count"]
                or not 0 <= expected["event_count"] <= current["event_count"]
                or type(expected["attempts"]) is not list
                or len(expected["attempts"]) != expected["attempt_count"]):
            reject()
        event_count = 0
        for anchor, present in zip(expected["attempts"], current["attempts"], strict=False):
            if type(anchor) is not dict or set(anchor) != set(present):
                reject()
            if (anchor["attempt_id"] != present["attempt_id"] or type(anchor["attempt_seq"]) is not int
                    or anchor["attempt_seq"] != present["attempt_seq"]
                    or type(anchor["event_count"]) is not int or not 1 <= anchor["event_count"] <= present["event_count"]):
                reject()
            event = connection.execute("SELECT event_hash FROM events WHERE attempt_id=? AND sequence=?", (anchor["attempt_id"], anchor["event_count"])).fetchone()
            if event is None or event["event_hash"] != anchor["event_root_hash"]:
                reject()
            event_count += anchor["event_count"]
        if event_count != expected["event_count"]:
            reject()

    @contextmanager
    def lease(self, attempt_id):
        """Hold a nonblocking local flock; release on close or process termination.

        Never unlink lease files: replacing a live lock inode breaks exclusion.
        No PID is recorded or trusted. Executed child programs do not inherit the
        descriptor (CLOEXEC); an unexecuted fork still inherits it, as POSIX does.
        """
        self.get(attempt_id)
        path = self.lease_directory / f"{attempt_id}.lock"
        _private_file(path, missing=True, maximum=0)
        try:
            descriptor = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, 0o600)
        except OSError as error:
            raise ValueError("Lease must be an owned regular file without symlinks") from error
        try:
            info = os.fstat(descriptor)
            current = _private_file(path, maximum=0)
            if (info.st_dev, info.st_ino) != (current.st_dev, current.st_ino):
                raise ValueError("Lease file changed while opening")
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise LeaseBusyError(f"Attempt lease is already held: {attempt_id}") from error
            yield {"attempt_id": attempt_id, "lease_path": str(path), "scope": "cooperating_local_processes"}
        finally:
            os.close(descriptor)
