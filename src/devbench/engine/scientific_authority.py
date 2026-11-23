"""Additive scientific authority and transactional outbox for the operation ledger."""

import hashlib
import io
import json
import math
import re
import sqlite3
import time
import uuid
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path

from .workflow_operations import OperationBlocked, OperationConflict, _digest, _json

SCHEMA = {
    "scientific_schema": (
        "CREATE TABLE scientific_schema (version INTEGER PRIMARY KEY CHECK(version=1))"
    ),
    "scientific_records": (
        "CREATE TABLE scientific_records (\n"
        "        lineage_id TEXT NOT NULL, revision INTEGER NOT NULL "
        "CHECK(revision>0),\n"
        "        record_id TEXT NOT NULL, kind TEXT NOT NULL, binding_id TEXT, "
        "publication_unit TEXT,\n"
        "        payload TEXT NOT NULL, sha256 TEXT NOT NULL,\n"
        "        PRIMARY KEY(lineage_id,revision), UNIQUE(lineage_id,record_id),\n"
        "        UNIQUE(lineage_id,binding_id,publication_unit))"
    ),
    "scientific_outbox": (
        "CREATE TABLE scientific_outbox (\n"
        "        lineage_id TEXT NOT NULL, revision INTEGER NOT NULL, sha256 TEXT NOT "
        "NULL,\n"
        "        acknowledged INTEGER NOT NULL DEFAULT 0 CHECK(acknowledged IN "
        "(0,1)),\n"
        "        PRIMARY KEY(lineage_id,revision),\n"
        "        FOREIGN KEY(lineage_id,revision) REFERENCES "
        "scientific_records(lineage_id,revision))"
    ),
}
RECORD_LOOKUP_INDEX = "scientific_records_record_id_kind"
_RECORD_LOOKUP_SQL = (
    f"CREATE INDEX IF NOT EXISTS {RECORD_LOOKUP_INDEX} "
    "ON scientific_records(record_id, kind)"
)

EXECUTION_SCHEMA = {
    "scientific_execution_schema": (
        "CREATE TABLE scientific_execution_schema (version INTEGER PRIMARY KEY "
        "CHECK(version=1))"
    ),
    "scientific_execution": """CREATE TABLE scientific_execution (
        lineage_id TEXT PRIMARY KEY, state_json TEXT NOT NULL, sha256 TEXT NOT NULL)""",
    "scientific_attempts": (
        "CREATE TABLE scientific_attempts (\n"
        "        lineage_id TEXT NOT NULL, attempt_id TEXT NOT NULL, state_json TEXT "
        "NOT NULL, sha256 TEXT NOT NULL,\n"
        "        PRIMARY KEY(lineage_id,attempt_id))"
    ),
}
DISCLOSURE_SCHEMA = {
    "scientific_disclosure_schema": (
        "CREATE TABLE scientific_disclosure_schema (version INTEGER PRIMARY KEY "
        "CHECK(version=1))"
    ),
    **{
        name: (
            f"CREATE TABLE {name} (identity TEXT PRIMARY KEY, "
            "payload TEXT NOT NULL, sha256 TEXT NOT NULL)"
        )
        for name in (
            "scientific_access_policies",
            "scientific_access_contexts",
            "scientific_access_sources",
            "scientific_disclosures",
        )
    },
    "scientific_disclosure_clock": (
        "CREATE TABLE scientific_disclosure_clock (singleton INTEGER PRIMARY KEY "
        "CHECK(singleton=1), watermark INTEGER NOT NULL)"
    ),
}
_PROJECTION_TABLE = "scientific_projection_checkpoints"
_PROJECTION_SQL = (
    "CREATE TABLE scientific_projection_checkpoints (\n"
    "        lineage_id TEXT NOT NULL, stream TEXT NOT NULL, checkpoint_revision "
    "INTEGER NOT NULL CHECK(checkpoint_revision>0),\n"
    "        state_json TEXT NOT NULL, PRIMARY KEY(lineage_id,stream),\n"
    "        FOREIGN KEY(lineage_id,checkpoint_revision) REFERENCES "
    "scientific_records(lineage_id,revision))"
)
_PROJECTION_STREAMS = {
    "adaptive": ("adaptive_event", "%"),
    "literature": ("execution_event", "literature-%"),
    "review": ("execution_event", "review-%"),
    "thesis_aims": ("execution_event", "thesis-aim-%"),
}
_CHECKPOINT_INTERVAL = 256
_CHECKPOINT_MAX_BYTES = 16 * 1024**2
_ID = re.compile(r"[A-Za-z0-9_.:-]{1,128}\Z")
_SHA = re.compile(r"[a-f0-9]{64}\Z")
_BINDING_HASHES = {
    "cohort_sha256",
    "design_sha256",
    "procedure_sha256",
    "family_allocation_sha256",
    "exposure_basis_sha256",
    "policy_sha256",
}
_FIELDS = {
    "binding": _BINDING_HASHES,
    "grant": {"binding_id", "grant_sha256"},
    "hold": {"binding_id", "reason_code"},
    "hold_resolution": {"binding_id", "hold_id", "verification_sha256"},
    "output": {
        "binding_id",
        "publication_unit",
        "artifact_sha256",
        "verification_sha256",
    },
    "execution_event": {"event", "state_sha256"},
    "disclosure_event": {"event", "state_sha256"},
    "adaptive_event": {"event", "state_sha256"},
}


def require_patched_wal():
    version = sqlite3.sqlite_version_info
    backported = (
        version[:2] == (3, 44)
        and version[2] >= 6
        or version[:2] == (3, 50)
        and version[2] >= 7
    )
    if version < (3, 51, 3) and not backported:
        raise OperationConflict(
            "Scientific WAL requires patched SQLite (3.51.3+ or a documented backport)"
        )


def check_schema(connection):
    found = dict(
        connection.execute(
            "SELECT name,sql FROM sqlite_master WHERE name GLOB 'scientific_*'"
        )
    )
    if not found:
        return False
    # Existing read-only ledgers can predate this performance-only index.
    index = found.pop(RECORD_LOOKUP_INDEX, None)
    if index is not None and index != _RECORD_LOOKUP_SQL.replace("IF NOT EXISTS ", ""):
        raise OperationConflict("Invalid scientific records lookup index")
    expected = dict(SCHEMA)
    if _PROJECTION_TABLE in found:
        expected[_PROJECTION_TABLE] = _PROJECTION_SQL
    if found.keys() & EXECUTION_SCHEMA.keys():
        expected.update(EXECUTION_SCHEMA)
    if found.keys() & DISCLOSURE_SCHEMA.keys():
        expected.update(DISCLOSURE_SCHEMA)
    if found != expected or [
        row[0] for row in connection.execute("SELECT version FROM scientific_schema")
    ] != [1]:
        raise OperationConflict("Incomplete or unsupported scientific authority schema")
    if found.keys() & EXECUTION_SCHEMA.keys() and [
        row[0]
        for row in connection.execute("SELECT version FROM scientific_execution_schema")
    ] != [1]:
        raise OperationConflict("Unsupported scientific execution schema")
    if found.keys() & DISCLOSURE_SCHEMA.keys() and [
        row[0]
        for row in connection.execute(
            "SELECT version FROM scientific_disclosure_schema"
        )
    ] != [1]:
        raise OperationConflict("Unsupported scientific disclosure schema")
    return True


def install_schema(connection):
    """Caller holds the migration transaction. SQLite DDL rolls back on failure."""
    if not check_schema(connection):
        for sql in SCHEMA.values():
            connection.execute(sql)
        connection.execute("INSERT INTO scientific_schema VALUES (1)")
    connection.execute(_RECORD_LOOKUP_SQL)


def require_same_ledger_transaction(connection, ledger):
    """Require an active SQLite transaction on the canonical main database."""
    message = "Operation requires the same ledger transaction"
    if not isinstance(connection, sqlite3.Connection) or not connection.in_transaction:
        raise PermissionError(message)
    path = ledger if isinstance(ledger, Path) else ledger.path
    main = next(
        (
            row[2]
            for row in connection.execute("PRAGMA database_list")
            if row[1] == "main"
        ),
        None,
    )
    if main is None or main != str(path.resolve()):
        raise PermissionError(message)


def _matches(pattern, value):
    return type(value) is str and pattern.fullmatch(value) is not None


def _record_payload(record):
    if (
        type(record) is not dict
        or type(record.get("kind")) is not str
        or record["kind"] not in _FIELDS
    ):
        raise ValueError("Unknown scientific record kind")
    required = {"kind", "record_id"} | _FIELDS[record["kind"]]
    optional = {"dependencies"} | (
        {"attempt_id"} if record["kind"] == "output" else set()
    )
    optional |= (
        {"state"}
        if record["kind"] in {"execution_event", "disclosure_event", "adaptive_event"}
        else set()
    )
    if "checkpoint" in record:
        optional.add("checkpoint")
        marker = record["checkpoint"]
        identity = record.get("record_id")
        if (
            type(marker) is not dict
            or marker.keys()
            != {"checkpoint_revision", "records_sha256", "state_sha256"}
            or type(marker["checkpoint_revision"]) is not int
            or marker["checkpoint_revision"] < 1
            or not all(
                _matches(_SHA, marker[key])
                for key in ("records_sha256", "state_sha256")
            )
            or not _matches(_ID, identity)
            or not any(
                record["kind"] == kind and identity.startswith(pattern[:-1])
                for kind, pattern in _PROJECTION_STREAMS.values()
            )
        ):
            raise ValueError("Invalid scientific projection checkpoint marker")
    if not required <= record.keys() or record.keys() - required - optional:
        raise ValueError("Invalid scientific record fields")
    for key in required - {"kind"}:
        if not _matches(_SHA if key.endswith("_sha256") else _ID, record[key]):
            raise ValueError("Invalid scientific record field: " + key)
    if "attempt_id" in record and not _matches(_ID, record["attempt_id"]):
        raise ValueError("Invalid output attempt identity")
    if record["kind"] in {"execution_event", "disclosure_event", "adaptive_event"} and (
        type(record.get("state")) is not dict
        or _digest(_json(record["state"])) != record["state_sha256"]
    ):
        raise ValueError("Invalid scientific execution event")
    deps = record.get("dependencies", [])
    if (
        type(deps) is not list
        or len(deps) > 64
        or any(
            type(dep) is not dict
            or dep.keys() != {"record_id", "kind", "sha256"}
            or not _matches(_ID, dep["record_id"])
            or type(dep["kind"]) is not str
            or dep["kind"] not in _FIELDS
            or not _matches(_SHA, dep["sha256"])
            for dep in deps
        )
        or len({dep["record_id"] for dep in deps}) != len(deps)
    ):
        raise ValueError("Invalid typed scientific dependencies")
    return _json(
        {**record, "dependencies": sorted(deps, key=lambda dep: dep["record_id"])}
    )


def _row(row):
    payload = json.loads(row["payload"])
    if (
        _record_payload(payload) != row["payload"]
        or _digest(row["payload"]) != row["sha256"]
        or any(
            payload.get(key) != row[key]
            for key in ("record_id", "kind", "binding_id", "publication_unit")
        )
    ):
        raise OperationConflict("Scientific record integrity mismatch")
    return {
        "lineage_id": row["lineage_id"],
        "revision": row["revision"],
        "sha256": row["sha256"],
        "record": payload,
    }


@dataclass(frozen=True)
class ProjectionHead:
    revision: int
    records_sha256: str
    tail_events: int

    def snapshot(self, state):
        """Encode a due snapshot without growing the authoritative event bound."""
        if self.tail_events < _CHECKPOINT_INTERVAL:
            return None
        # Reducers depend on insertion order for recent cycles and artifact
        # ancestry. The snapshot preserves that order through JSON round trips.
        encoder = json.JSONEncoder(
            ensure_ascii=False, allow_nan=False, separators=(",", ":")
        )
        size = 0
        with io.StringIO() as body:
            for chunk in encoder.iterencode(state()):
                size += len(chunk.encode("utf-8"))
                if size > _CHECKPOINT_MAX_BYTES:
                    return None  # Large projections may continue with streamed replay.
                body.write(chunk)
            raw = body.getvalue()
        return (
            {
                "checkpoint_revision": self.revision,
                "records_sha256": self.records_sha256,
                "state_sha256": _digest(raw),
            },
            raw,
        )


def _projection_checkpoint(db, lineage, stream):
    if db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (_PROJECTION_TABLE,),
    ).fetchone():
        checkpoint = db.execute(
            f"""SELECT checkpoint_revision,
            CASE WHEN length(CAST(state_json AS BLOB)) <= ? THEN state_json END
            FROM {_PROJECTION_TABLE} WHERE lineage_id=? AND stream=?""",
            (_CHECKPOINT_MAX_BYTES, lineage, stream),
        ).fetchone()
        if checkpoint is not None:
            revision, raw = checkpoint
            return revision, raw
    return 0, None


def _projection_event(row):
    if row["payload"] is None:
        raise OperationConflict("Scientific projection event exceeds the record bound")
    verified = _row(row)
    if verified["sha256"] != row["outbox_digest"]:
        raise OperationConflict("Scientific projection event outbox binding changed")
    return verified


def load_scientific_projection(db, lineage, stream, *, initial, apply, restore=None):
    """Verify every retained stream record and fold only events after the checkpoint."""
    if not db.in_transaction:
        db.execute("BEGIN")
    checkpoint_revision, raw = _projection_checkpoint(db, lineage, stream)

    chain = hashlib.sha256(
        _json(["devbench.scientific-projection/1", lineage, stream]).encode("utf-8")
    )
    revision, tail_events = 0, 0
    state = initial()
    restored = False
    kind, pattern = _PROJECTION_STREAMS[stream]
    rows = db.execute(
        (
            "SELECT r.lineage_id,r.revision,r.record_id,r.kind,r.binding_id,r.publicat"
            "ion_unit,\n"
            "        CASE WHEN length(CAST(r.payload AS BLOB)) <= 65536 THEN "
            "r.payload END AS payload,\n"
            "        r.sha256,o.sha256 AS outbox_digest FROM scientific_records r\n"
            "        LEFT JOIN scientific_outbox o ON r.lineage_id=o.lineage_id AND "
            "r.revision=o.revision\n"
            "        WHERE r.lineage_id=? AND r.kind=? AND r.record_id LIKE ? ORDER "
            "BY r.revision"
        ),
        (lineage, kind, pattern),
    )
    for row in rows:
        verified = _projection_event(row)
        marker = verified["record"].get("checkpoint")
        if marker is not None:
            if (
                marker["checkpoint_revision"] != revision
                or marker["records_sha256"] != chain.hexdigest()
                or revision > checkpoint_revision
            ):
                raise OperationConflict(
                    "Scientific projection checkpoint history changed"
                )
            if revision == checkpoint_revision:
                if not isinstance(raw, str) or _digest(raw) != marker["state_sha256"]:
                    raise OperationConflict(
                        "Scientific projection checkpoint snapshot changed"
                    )
                document = json.loads(raw)
                state = document if restore is None else restore(document)
                restored = True
                tail_events = 0
        revision = verified["revision"]
        chain.update(_json([revision, verified["sha256"]]).encode("utf-8"))
        if revision > checkpoint_revision:
            if checkpoint_revision and not restored:
                raise OperationConflict(
                    "Scientific projection checkpoint marker is missing"
                )
            apply(state, verified)
            tail_events += 1
    if checkpoint_revision and not restored:
        raise OperationConflict("Scientific projection checkpoint marker is missing")
    return state, ProjectionHead(revision, chain.hexdigest(), tail_events)


def append_scientific_projection_event(
    authority, db, lineage, stream, record, snapshot=None
):
    """Commit a periodic snapshot and its canonical marker with the next event."""
    if snapshot is None:
        return authority._append_execution_record(db, lineage, record)
    marker, raw = snapshot
    marked = {**record, "checkpoint": marker}
    try:
        _record_payload(marked)
    except ValueError:
        # An existing event can consume the full bounded record allowance.
        # Defer the checkpoint instead of reducing that allowance.
        return authority._append_execution_record(db, lineage, record)
    kind, pattern = _PROJECTION_STREAMS[stream]
    latest = db.execute(
        (
            "SELECT revision FROM scientific_records\n"
            "        WHERE lineage_id=? AND kind=? AND record_id LIKE ? ORDER BY "
            "revision DESC LIMIT 1"
        ),
        (lineage, kind, pattern),
    ).fetchone()
    if latest is None or latest[0] != marker["checkpoint_revision"]:
        raise OperationConflict("Scientific projection checkpoint revision changed")
    db.execute("SAVEPOINT projection_checkpoint")
    try:
        revision = authority._append_execution_record(db, lineage, marked)
        db.execute(
            _PROJECTION_SQL.replace("CREATE TABLE ", "CREATE TABLE IF NOT EXISTS ", 1)
        )
        db.execute(
            f"INSERT INTO {_PROJECTION_TABLE} VALUES (?,?,?,?)\n"
            "            ON CONFLICT(lineage_id,stream) DO UPDATE SET\n"
            "            checkpoint_revision=excluded.checkpoint_revision, "
            "state_json=excluded.state_json",
            (lineage, stream, marker["checkpoint_revision"], raw),
        )
    except BaseException:
        db.execute("ROLLBACK TO projection_checkpoint")
        raise
    finally:
        db.execute("RELEASE projection_checkpoint")
    return revision


def _lookup(connection, lineage, record_id):
    row = connection.execute(
        "SELECT * FROM scientific_records WHERE lineage_id=? AND record_id=?",
        (lineage, record_id),
    ).fetchone()
    return _row(row) if row is not None else None


def _check_lineage(lineage, revision=None):
    if not _matches(_ID, lineage) or (
        revision is not None and (type(revision) is not int or revision < 0)
    ):
        raise ValueError("Invalid scientific lineage or revision")


def _validate_commit_references(db, lineage, record, binding):
    for dep in record["dependencies"]:
        parent = _lookup(db, lineage, dep["record_id"])
        if (
            parent is None
            or parent["sha256"] != dep["sha256"]
            or parent["record"]["kind"] != dep["kind"]
        ):
            raise OperationConflict(
                "Scientific dependency does not match its retained record"
            )
    if binding is not None:
        parent = _lookup(db, lineage, binding)
        if parent is None or parent["record"]["kind"] != "binding":
            raise OperationConflict("Scientific record has no retained binding")
    if record["kind"] == "hold_resolution":
        hold = _lookup(db, lineage, record["hold_id"])
        if (
            hold is None
            or hold["record"]["kind"] != "hold"
            or hold["record"]["binding_id"] != binding
        ):
            raise OperationConflict("Resolution does not match a retained hold")


class ScientificAuthority:
    """Store authoritative records. Execution and public delivery gates are separate."""

    def __init__(self, ledger):
        self.ledger = ledger

    def commit(self, lineage_id, *, expected_revision, record, verify_output=None):
        self.ledger._writable()
        _check_lineage(lineage_id, expected_revision)
        if expected_revision is None:
            raise ValueError("Scientific commits require an expected revision")
        payload = _record_payload(record)
        record = json.loads(payload)
        digest = _digest(payload)
        kind = record["kind"]
        if kind in {"output", "execution_event", "disclosure_event", "adaptive_event"}:
            raise OperationBlocked("scientific_attempt_required")
        binding = record.get("binding_id")
        unit = record.get("publication_unit")
        with self.ledger._database() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = _lookup(db, lineage_id, record["record_id"])
            if existing is not None:
                if existing["sha256"] != digest:
                    raise OperationConflict("Scientific record identity was reused")
                return existing
            latest = db.execute(
                (
                    "SELECT coalesce(max(revision),0) FROM scientific_records WHERE "
                    "lineage_id=?"
                ),
                (lineage_id,),
            ).fetchone()[0]
            if latest != expected_revision:
                raise OperationConflict("Scientific revision changed")
            if latest == 0:
                if kind != "binding":
                    raise OperationConflict(
                        "A scientific lineage must start with its binding"
                    )
                if (
                    db.execute(
                        "SELECT 1 FROM workflows WHERE workflow_id=?", (lineage_id,)
                    ).fetchone()
                    or db.execute(
                        "SELECT 1 FROM operations WHERE workflow_id=? LIMIT 1",
                        (lineage_id,),
                    ).fetchone()
                ):
                    raise OperationConflict(
                        "Existing legacy workflow cannot become a scientific lineage"
                    )
            _validate_commit_references(db, lineage_id, record, binding)
            revision = latest + 1
            try:
                db.execute(
                    "INSERT INTO scientific_records VALUES (?,?,?,?,?,?,?,?)",
                    (
                        lineage_id,
                        revision,
                        record["record_id"],
                        kind,
                        binding,
                        unit,
                        payload,
                        digest,
                    ),
                )
            except sqlite3.IntegrityError as error:
                raise OperationConflict(
                    "Conflicting scientific record or output unit"
                ) from error
            db.execute(
                "INSERT INTO scientific_outbox VALUES (?,?,?,0)",
                (lineage_id, revision, digest),
            )
            return {
                "lineage_id": lineage_id,
                "revision": revision,
                "sha256": digest,
                "record": record,
            }

    def get(self, lineage_id, record_id):
        _check_lineage(lineage_id)
        if not _matches(_ID, record_id):
            raise ValueError("Invalid scientific record identity")
        return self.ledger._read(lambda db: _lookup(db, lineage_id, record_id))

    def pending(self, lineage_id, *, limit=64):
        _check_lineage(lineage_id)
        if type(limit) is not int or not 1 <= limit <= 128:
            raise ValueError("Invalid outbox page size")

        def read(db):
            rows = db.execute(
                (
                    "SELECT r.*, o.sha256 AS outbox_digest FROM scientific_outbox o\n"
                    "                LEFT JOIN scientific_records r ON "
                    "r.lineage_id=o.lineage_id AND r.revision=o.revision\n"
                    "                WHERE o.lineage_id=? AND o.acknowledged=0 ORDER "
                    "BY o.revision LIMIT ?"
                ),
                (lineage_id, limit),
            ).fetchall()
            if any(row["sha256"] != row["outbox_digest"] for row in rows):
                raise OperationConflict("Scientific outbox binding changed")
            return [_row(row) for row in rows]

        return self.ledger._read(read)

    def drain(self, lineage_id, project, *, limit=64):
        """Project outside the SQLite transaction. Consumers must be idempotent."""
        self.ledger._writable()
        events = self.pending(lineage_id, limit=limit)
        for event in events:
            if project(deepcopy(event)) is False:
                raise OperationBlocked("scientific_projection_unacknowledged")
            with self.ledger._database() as db:
                changed = db.execute(
                    """UPDATE scientific_outbox SET acknowledged=1
                    WHERE lineage_id=? AND revision=? AND sha256=?""",
                    (lineage_id, event["revision"], event["sha256"]),
                )
                if changed.rowcount != 1:
                    raise OperationConflict(
                        "Scientific outbox event changed during projection"
                    )
        return len(events)

    @contextmanager
    def _execution_tx(self, lineage_id):
        self.ledger._writable()
        _check_lineage(lineage_id)
        with self.ledger._database() as db:
            db.execute("BEGIN IMMEDIATE")
            if not check_schema(db):
                raise OperationConflict("Scientific authority schema unavailable")
            db.execute(_RECORD_LOOKUP_SQL)
            if not db.execute(
                "SELECT 1 FROM sqlite_master WHERE name='scientific_execution_schema'"
            ).fetchone():
                for sql in EXECUTION_SCHEMA.values():
                    db.execute(sql)
                db.execute("INSERT INTO scientific_execution_schema VALUES (1)")
            yield db

    @staticmethod
    def _revision(db, lineage):
        return db.execute(
            (
                "SELECT coalesce(max(revision),0) FROM scientific_records WHERE "
                "lineage_id=?"
            ),
            (lineage,),
        ).fetchone()[0]

    @staticmethod
    def _load_state(db, lineage, attempt_id=None):
        table = "scientific_execution" if attempt_id is None else "scientific_attempts"
        if not db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone():
            return None
        args = (lineage,) if attempt_id is None else (lineage, attempt_id)
        clause = (
            "lineage_id=?" if attempt_id is None else "lineage_id=? AND attempt_id=?"
        )
        row = db.execute(
            f"SELECT state_json,sha256 FROM {table} WHERE {clause}", args
        ).fetchone()
        if row is None:
            return None
        value = json.loads(row[0])
        if _json(value) != row[0] or _digest(row[0]) != row[1]:
            raise OperationConflict("Scientific execution state integrity mismatch")
        return value

    @staticmethod
    def _save_state(db, lineage, state, attempt_id=None):
        raw = _json(state)
        digest = _digest(raw)
        if attempt_id is None:
            db.execute(
                (
                    "INSERT INTO scientific_execution VALUES (?,?,?)\n"
                    "                ON CONFLICT(lineage_id) DO UPDATE SET "
                    "state_json=excluded.state_json, sha256=excluded.sha256"
                ),
                (lineage, raw, digest),
            )
        else:
            db.execute(
                (
                    "INSERT INTO scientific_attempts VALUES (?,?,?,?)\n"
                    "                ON CONFLICT(lineage_id,attempt_id) DO UPDATE SET "
                    "state_json=excluded.state_json,sha256=excluded.sha256"
                ),
                (lineage, attempt_id, raw, digest),
            )

    @staticmethod
    def _append_execution_record(db, lineage, record):
        payload = _record_payload(record)
        digest = _digest(payload)
        revision = ScientificAuthority._revision(db, lineage) + 1
        db.execute(
            "INSERT INTO scientific_records VALUES (?,?,?,?,?,?,?,?)",
            (
                lineage,
                revision,
                record["record_id"],
                record["kind"],
                record.get("binding_id"),
                record.get("publication_unit"),
                payload,
                digest,
            ),
        )
        db.execute(
            "INSERT INTO scientific_outbox VALUES (?,?,?,0)",
            (lineage, revision, digest),
        )
        return revision

    def _event(self, db, lineage, event, state):
        return self._append_execution_record(
            db,
            lineage,
            {
                "kind": "execution_event",
                "record_id": "event-" + uuid.uuid4().hex,
                "event": event,
                "state_sha256": _digest(_json(state)),
                "state": state,
            },
        )

    @staticmethod
    def _binding_grant(db, lineage, binding_id, grant_id):
        binding = _lookup(db, lineage, binding_id)
        grant = _lookup(db, lineage, grant_id)
        active = db.execute(
            """SELECT record_id FROM scientific_records
            WHERE lineage_id=? AND kind='binding' ORDER BY revision DESC LIMIT 1""",
            (lineage,),
        ).fetchone()
        if (
            binding is None
            or active is None
            or active[0] != binding_id
            or binding["record"]["kind"] != "binding"
        ):
            raise OperationBlocked("scientific_binding_stale")
        if (
            grant is None
            or grant["record"]["kind"] != "grant"
            or grant["record"]["binding_id"] != binding_id
        ):
            raise OperationBlocked("scientific_grant_invalid")
        if db.execute(
            (
                "SELECT 1 FROM scientific_records h WHERE h.lineage_id=? AND "
                "h.binding_id=?\n"
                "                AND h.kind='hold' AND NOT EXISTS (SELECT 1 FROM "
                "scientific_records r\n"
                "                    WHERE r.lineage_id=h.lineage_id AND "
                "r.binding_id=h.binding_id AND r.kind='hold_resolution'\n"
                "                    AND json_extract(r.payload,'$.hold_id')=h.record_"
                "id)"
            ),
            (lineage, binding_id),
        ).fetchone():
            raise OperationBlocked("scientific_hold")
        return binding, grant

    def _admit_epoch(
        self,
        lineage_id,
        *,
        expected_revision,
        binding_id,
        grant_id,
        max_jobs,
        authorize,
        resume,
    ):
        _check_lineage(lineage_id, expected_revision)
        if (
            expected_revision is None
            or not _matches(_ID, binding_id)
            or not _matches(_ID, grant_id)
        ):
            raise ValueError(
                "Execution admission requires exact binding, grant and revision"
            )
        if not callable(authorize):
            raise OperationBlocked("scientific_authorization_required")
        if type(max_jobs) is not int or not 1 <= max_jobs <= 128:
            raise ValueError("Execution admission requires a finite job budget")
        with self._execution_tx(lineage_id) as db:
            prior = self._load_state(db, lineage_id)
            binding, grant = self._binding_grant(db, lineage_id, binding_id, grant_id)
            if self._revision(db, lineage_id) != expected_revision:
                raise OperationConflict("Scientific revision changed")
            if (resume and (prior is None or prior["status"] != "cancelled")) or (
                not resume and prior is not None
            ):
                raise OperationBlocked("scientific_resume_state")
            if prior is not None and max_jobs != prior["max_jobs"]:
                raise OperationConflict("Resume cannot reset the cumulative job budget")
            basis = {
                "lineage_id": lineage_id,
                "binding_id": binding_id,
                "binding_sha256": binding["sha256"],
                "grant_id": grant_id,
                "grant_sha256": grant["sha256"],
                "max_jobs": max_jobs,
                "expected_revision": expected_revision,
                "prior_epoch": prior["epoch"] if prior else 0,
            }
        proof = authorize(deepcopy(basis))
        if (
            type(proof) is not dict
            or set(proof) != {"authorized", "authorization_sha256", "expires_at"}
            or proof["authorized"] is not True
            or not _matches(_SHA, proof["authorization_sha256"])
            or type(proof["expires_at"]) not in (int, float)
            or not math.isfinite(proof["expires_at"])
            or proof["expires_at"] <= time.time()
        ):
            raise OperationBlocked("scientific_authorization_invalid")
        with self._execution_tx(lineage_id) as db:
            if self._revision(db, lineage_id) != expected_revision:
                raise OperationConflict(
                    "Scientific revision changed during authorization"
                )
            self._binding_grant(db, lineage_id, binding_id, grant_id)
            current = self._load_state(db, lineage_id)
            if current != prior:
                raise OperationConflict("Scientific epoch changed during authorization")
            state = {
                "epoch": basis["prior_epoch"] + 1,
                "status": "active",
                "binding_id": binding_id,
                "grant_id": grant_id,
                "max_jobs": max_jobs,
                "reserved_jobs": prior["reserved_jobs"] if prior else 0,
                "authorization_sha256": proof["authorization_sha256"],
                "expires_at": proof["expires_at"],
            }
            self._save_state(db, lineage_id, state)
            revision = self._event(
                db,
                lineage_id,
                "execution_resumed" if resume else "execution_activated",
                state,
            )
            return {**state, "revision": revision}

    def activate_execution(self, lineage_id, **options):
        return self._admit_epoch(lineage_id, resume=False, **options)

    def resume_execution(self, lineage_id, **options):
        return self._admit_epoch(lineage_id, resume=True, **options)

    def execution(self, lineage_id):
        _check_lineage(lineage_id)

        def read(db):
            state = self._load_state(db, lineage_id)
            if state is None:
                raise OperationBlocked("scientific_execution_unavailable")
            return {**state, "revision": self._revision(db, lineage_id)}

        return self.ledger._read(read)

    def cancel_execution(self, lineage_id, *, actor, expected_revision=None):
        if not _matches(_ID, actor):
            raise ValueError("Cancellation requires an attributed actor")
        with self._execution_tx(lineage_id) as db:
            state = self._load_state(db, lineage_id)
            if state is None:
                raise OperationBlocked("scientific_execution_unavailable")
            if (
                expected_revision is not None
                and self._revision(db, lineage_id) != expected_revision
            ):
                raise OperationConflict("Scientific revision changed")
            if state["status"] == "cancelled":
                return {**state, "revision": self._revision(db, lineage_id)}
            state.update(
                status="cancelled", epoch=state["epoch"] + 1, cancelled_by=actor
            )
            self._save_state(db, lineage_id, state)
            return {
                **state,
                "revision": self._event(db, lineage_id, "execution_cancelled", state),
            }

    def _active(self, db, lineage, state, token=None):
        if state is None:
            raise OperationBlocked("scientific_execution_unavailable")
        if token is not None and token["epoch"] != state["epoch"]:
            raise OperationBlocked("scientific_epoch_revoked")
        if state["status"] != "active":
            raise OperationBlocked("scientific_cancelled")
        if state["expires_at"] <= time.time():
            raise OperationBlocked("scientific_grant_expired")
        self._binding_grant(db, lineage, state["binding_id"], state["grant_id"])

    @staticmethod
    def _checked_attempt(db, token):
        fields = {
            "lineage_id",
            "attempt_id",
            "epoch",
            "binding_id",
            "publication_unit",
            "request_sha256",
            "environment_sha256",
            "backend_id",
        }
        if (
            type(token) is not dict
            or set(token) != fields
            or type(token["epoch"]) is not int
            or token["epoch"] < 1
            or any(
                not _matches(_SHA if key.endswith("_sha256") else _ID, token[key])
                for key in fields - {"epoch"}
            )
        ):
            raise ValueError("Invalid scientific attempt token")
        attempt = ScientificAuthority._load_state(
            db, token["lineage_id"], token["attempt_id"]
        )
        if attempt is None or attempt["token"] != token:
            raise OperationConflict("Unknown or mismatched scientific attempt")
        return attempt

    def _related_attempts(self, db, lineage_id, state, publication_unit):
        prior_attempts = db.execute(
            "SELECT attempt_id FROM scientific_attempts WHERE lineage_id=?",
            (lineage_id,),
        ).fetchall()
        related = {}
        for row in prior_attempts:
            previous = self._load_state(db, lineage_id, row[0])
            prior = previous["token"]
            if (prior["binding_id"], prior["publication_unit"]) == (
                state["binding_id"],
                publication_unit,
            ):
                related[prior["attempt_id"]] = previous
        return related

    @staticmethod
    def _reuse_root(previous, related):
        seen = set()
        while previous["reused_attempt_id"] is not None:
            parent = previous["reused_attempt_id"]
            if parent in seen or parent not in related:
                raise OperationConflict("Invalid staged reuse ancestry")
            seen.add(parent)
            previous = related[parent]
        return previous["token"]["attempt_id"]

    def _staged_reuse(self, related, reuse_from, state, operation):
        reuse = related.get(reuse_from)
        if reuse_from is not None and reuse is None:
            raise OperationBlocked("scientific_staged_reuse_invalid")

        def compatible(previous):
            prior = previous["token"]
            return (
                not previous.get("adaptive")
                and previous["execution_state"] == "succeeded"
                and previous["acceptance_state"] != "accepted"
                and prior["epoch"] != state["epoch"]
                and previous["artifact_sha256"] is not None
                and all(prior[key] == value for key, value in operation.items())
            )

        if reuse is not None and (
            not compatible(reuse) or reuse["verification_sha256"] is None
        ):
            raise OperationBlocked("scientific_staged_reuse_invalid")
        for previous in related.values():
            if previous["execution_state"] == "noncompletion_confirmed":
                continue
            if (
                reuse is not None
                and compatible(previous)
                and previous["artifact_sha256"] == reuse["artifact_sha256"]
                and self._reuse_root(previous, related)
                == self._reuse_root(reuse, related)
            ):
                continue
            raise OperationBlocked("scientific_attempt_completion_unknown")
        return reuse

    def reserve_attempt(
        self,
        lineage_id,
        *,
        publication_unit,
        request_sha256,
        environment_sha256,
        backend_id,
        expected_epoch,
        binding_id,
        expected_revision,
        reuse_from=None,
        _adaptive_reservation=None,
    ):
        _check_lineage(lineage_id, expected_revision)
        if (
            type(expected_epoch) is not int
            or expected_epoch < 1
            or not _matches(_ID, binding_id)
            or expected_revision is None
        ):
            raise ValueError(
                "Reservation requires the caller's exact epoch, binding and revision"
            )
        if (
            not _matches(_ID, publication_unit)
            or not _matches(_ID, backend_id)
            or not _matches(_SHA, request_sha256)
            or not _matches(_SHA, environment_sha256)
        ):
            raise ValueError("Attempt reservation requires exact operation identities")
        with self._execution_tx(lineage_id) as db:
            state = self._load_state(db, lineage_id)
            self._active(db, lineage_id, state)
            if state["epoch"] != expected_epoch or state["binding_id"] != binding_id:
                raise OperationBlocked("scientific_expected_epoch_revoked")
            if self._revision(db, lineage_id) != expected_revision:
                raise OperationConflict(
                    "Scientific revision changed before reservation"
                )
            if state["reserved_jobs"] >= state["max_jobs"]:
                raise OperationBlocked("scientific_budget_exhausted")
            if db.execute(
                """SELECT 1 FROM scientific_records WHERE lineage_id=? AND binding_id=?
                    AND publication_unit=? AND kind='output'""",
                (lineage_id, state["binding_id"], publication_unit),
            ).fetchone():
                raise OperationBlocked("scientific_output_already_accepted")
            related = self._related_attempts(db, lineage_id, state, publication_unit)
            operation = {
                "request_sha256": request_sha256,
                "environment_sha256": environment_sha256,
                "backend_id": backend_id,
            }
            reuse = self._staged_reuse(related, reuse_from, state, operation)
            token = {
                "lineage_id": lineage_id,
                "attempt_id": uuid.uuid4().hex,
                "epoch": state["epoch"],
                "binding_id": state["binding_id"],
                "publication_unit": publication_unit,
                **operation,
            }
            attempt = {
                "token": token,
                "grant_id": state["grant_id"],
                "execution_state": "succeeded" if reuse else "reserved",
                "external_job_id": reuse["external_job_id"] if reuse else None,
                "acceptance_state": "absent",
                "artifact_sha256": reuse["artifact_sha256"] if reuse else None,
                "verification_sha256": None,
                "cleanup_state": reuse["cleanup_state"] if reuse else "pending",
                "usage": {},
                "observation_sha256": None,
                "reused_attempt_id": reuse_from,
            }
            if _adaptive_reservation is not None:
                if reuse_from is not None:
                    raise OperationBlocked(
                        "adaptive_staged_reuse_requires_reverification"
                    )
                attempt["adaptive"] = True
            state["reserved_jobs"] += 1
            self._save_state(db, lineage_id, state)
            self._save_state(db, lineage_id, attempt, token["attempt_id"])
            if _adaptive_reservation is not None:
                _adaptive_reservation(db, deepcopy(token))
            self._event(
                db,
                lineage_id,
                "attempt_reserved",
                {"attempt": attempt, "reserved_jobs": state["reserved_jobs"]},
            )
            return token

    def attempt(self, token):
        return self.ledger._read(lambda db: self._checked_attempt(db, token))

    def attempts(self, lineage_id):
        """Recover owned attempt tokens after the caller loses a response."""
        _check_lineage(lineage_id)

        def read(db):
            if self._load_state(db, lineage_id) is None:
                return []
            rows = db.execute(
                (
                    "SELECT attempt_id FROM scientific_attempts WHERE lineage_id=? "
                    "ORDER BY attempt_id LIMIT 129"
                ),
                (lineage_id,),
            ).fetchall()
            if len(rows) > 128:
                raise OperationConflict(
                    "Scientific attempt inventory exceeds its reserved bound"
                )
            results = []
            for row in rows:
                attempt = self._load_state(db, lineage_id, row[0])
                if (attempt["token"]["attempt_id"], attempt["token"]["lineage_id"]) != (
                    row[0],
                    lineage_id,
                ):
                    raise OperationConflict(
                        "Scientific attempt inventory identity changed"
                    )
                results.append(self._checked_attempt(db, attempt["token"]))
            return results

        return self.ledger._read(read)

    def dispatch_attempt(self, token, submit, *, _adaptive_guard=None):
        with self._execution_tx(token["lineage_id"]) as db:
            attempt = self._checked_attempt(db, token)
            self._active(
                db,
                token["lineage_id"],
                self._load_state(db, token["lineage_id"]),
                token,
            )
            if attempt["execution_state"] != "reserved":
                raise OperationBlocked("scientific_submission_uncertain")
            if attempt.get("adaptive"):
                if _adaptive_guard is None:
                    raise OperationBlocked(
                        "adaptive_dispatch_requires_current_use_check"
                    )
                _adaptive_guard(db)
            attempt["execution_state"] = "submission_uncertain"
            self._save_state(db, token["lineage_id"], attempt, token["attempt_id"])
            self._event(db, token["lineage_id"], "submission_intent", attempt)
        job = submit(deepcopy(token))
        if not _matches(_ID, job):
            raise OperationConflict("Provider returned an invalid owned job identity")
        with self._execution_tx(token["lineage_id"]) as db:
            attempt = self._checked_attempt(db, token)
            if attempt["execution_state"] == "noncompletion_confirmed":
                raise OperationConflict(
                    "Provider completion contradicts verified noncompletion"
                )
            if attempt["external_job_id"] not in (None, job):
                raise OperationConflict("Scientific job identity changed")
            attempt.update(external_job_id=job)
            self._save_state(db, token["lineage_id"], attempt, token["attempt_id"])
            self._event(db, token["lineage_id"], "job_identified", attempt)
        return job

    @staticmethod
    def _verify_provider_observation(
        token,
        *,
        job_id,
        execution_state,
        cleanup_state,
        usage,
        receipt_sha256,
        verify_observation,
        _no_container_binding,
        _require_reserved,
    ):
        if type(_no_container_binding) is not bool or (
            _no_container_binding
            and (
                execution_state != "noncompletion_confirmed"
                or cleanup_state != "confirmed"
                or job_id is not None
            )
        ):
            raise ValueError(
                "Unlaunched OCI reconciliation requires confirmed noncompletion "
                "and cleanup"
            )
        if type(_require_reserved) is not bool or (
            _require_reserved
            and (
                execution_state != "noncompletion_confirmed"
                or cleanup_state != "confirmed"
                or job_id is not None
            )
        ):
            raise ValueError(
                "Unsubmitted attempt recovery requires confirmed noncompletion"
            )
        if (
            job_id is not None
            and not _matches(_ID, job_id)
            or execution_state
            not in {
                "running",
                "succeeded",
                "failed",
                "cancelled",
                "noncompletion_confirmed",
            }
            or cleanup_state not in {"pending", "unknown", "confirmed", "failed"}
            or not _matches(_SHA, receipt_sha256)
            or type(usage) is not dict
            or usage.keys() - {"cpu_millis", "wall_millis", "cost_microunits"}
            or any(type(value) is not int or value < 0 for value in usage.values())
        ):
            raise ValueError("Invalid provider reconciliation evidence")
        observation = {
            "token": token,
            "job_id": job_id,
            "execution_state": execution_state,
            "cleanup_state": cleanup_state,
            "usage": usage,
            "receipt_sha256": receipt_sha256,
        }
        if _no_container_binding:
            observation["no_container_binding"] = True
        if (
            not callable(verify_observation)
            or verify_observation(deepcopy(observation)) is not True
        ):
            raise OperationBlocked("scientific_observation_unverified")

    def reconcile_attempt(
        self,
        token,
        *,
        job_id,
        execution_state,
        cleanup_state,
        usage,
        receipt_sha256,
        verify_observation,
        _no_container_binding=False,
        _require_reserved=False,
    ):
        self._verify_provider_observation(
            token,
            job_id=job_id,
            execution_state=execution_state,
            cleanup_state=cleanup_state,
            usage=usage,
            receipt_sha256=receipt_sha256,
            verify_observation=verify_observation,
            _no_container_binding=_no_container_binding,
            _require_reserved=_require_reserved,
        )
        with self._execution_tx(token["lineage_id"]) as db:
            attempt = self._checked_attempt(db, token)
            if _require_reserved and attempt["execution_state"] != "reserved":
                raise OperationBlocked("scientific_submission_intent_already_present")
            if _no_container_binding and (
                not attempt.get("adaptive")
                or token["backend_id"] != "local_exploratory_oci_1"
            ):
                raise OperationBlocked("scientific_oci_noncompletion_proof_required")
            if (
                attempt["execution_state"] == "reserved"
                and execution_state != "noncompletion_confirmed"
            ):
                raise OperationBlocked("scientific_submission_intent_missing")
            if attempt["external_job_id"] not in (None, job_id):
                raise OperationConflict("Reconciliation job differs from the owned job")
            if job_id is None and execution_state != "noncompletion_confirmed":
                raise OperationConflict("Reconciliation requires an owned job identity")
            terminal = {"succeeded", "failed", "cancelled", "noncompletion_confirmed"}
            if (
                attempt["execution_state"] in terminal
                and execution_state != attempt["execution_state"]
            ):
                raise OperationConflict(
                    "Reconciliation cannot change a terminal observation"
                )
            if (
                any(
                    key not in usage or usage[key] < value
                    for key, value in attempt["usage"].items()
                )
                or attempt["cleanup_state"] == "confirmed"
                and cleanup_state != "confirmed"
            ):
                raise OperationConflict(
                    "Reconciliation cannot erase usage or release evidence"
                )
            attempt.update(
                external_job_id=job_id,
                execution_state=execution_state,
                cleanup_state=cleanup_state,
                usage=usage,
                observation_sha256=receipt_sha256,
            )
            if _no_container_binding:
                attempt["no_container_binding"] = True
            self._save_state(db, token["lineage_id"], attempt, token["attempt_id"])
            self._event(db, token["lineage_id"], "attempt_reconciled", attempt)
            return attempt

    def stage_output(
        self, token, *, artifact_sha256, verification_sha256, verify_output
    ):
        if not _matches(_SHA, artifact_sha256) or not _matches(
            _SHA, verification_sha256
        ):
            raise ValueError(
                "Staging requires exact immutable manifest and verification digests"
            )
        staged = {
            "token": token,
            "artifact_sha256": artifact_sha256,
            "verification_sha256": verification_sha256,
        }
        if not callable(verify_output) or verify_output(deepcopy(staged)) is not True:
            raise OperationBlocked("scientific_output_unverified")
        with self._execution_tx(token["lineage_id"]) as db:
            attempt = self._checked_attempt(db, token)
            if (
                attempt["artifact_sha256"] is not None
                and attempt["artifact_sha256"] != artifact_sha256
            ) or (
                attempt["verification_sha256"] is not None
                and attempt["verification_sha256"] != verification_sha256
            ):
                raise OperationConflict("Cannot replace a staged scientific output")
            if attempt["acceptance_state"] == "accepted":
                return attempt
            attempt.update(
                artifact_sha256=artifact_sha256, verification_sha256=verification_sha256
            )
            if attempt["acceptance_state"] != "audit_only":
                state = self._load_state(db, token["lineage_id"])
                if state["status"] != "active" or state["epoch"] != token["epoch"]:
                    attempt.update(
                        acceptance_state="audit_only",
                        rejection_reason="scientific_epoch_revoked",
                    )
                else:
                    attempt["acceptance_state"] = "staged"
            self._save_state(db, token["lineage_id"], attempt, token["attempt_id"])
            self._event(db, token["lineage_id"], "output_staged", attempt)
            return attempt

    def accept_output(self, token, *, _adaptive_guard=None, _adaptive_accept=None):
        with self._execution_tx(token["lineage_id"]) as db:
            attempt = self._checked_attempt(db, token)
            if attempt["acceptance_state"] in {"accepted", "audit_only"}:
                return attempt
            if (
                attempt["acceptance_state"] != "staged"
                or attempt["execution_state"] != "succeeded"
            ):
                raise OperationBlocked("scientific_output_not_ready")
            if attempt.get("adaptive") and (
                _adaptive_guard is None or _adaptive_accept is None
            ):
                raise OperationBlocked("adaptive_acceptance_requires_current_use_check")
            try:
                if attempt.get("adaptive"):
                    _adaptive_guard(db)
                state = self._load_state(db, token["lineage_id"])
                self._active(db, token["lineage_id"], state, token)
                if (state["binding_id"], state["grant_id"]) != (
                    token["binding_id"],
                    attempt["grant_id"],
                ):
                    raise OperationBlocked("scientific_attempt_binding_stale")
                if db.execute(
                    (
                        "SELECT 1 FROM scientific_records WHERE lineage_id=? AND "
                        "binding_id=?\n"
                        "                        AND publication_unit=? AND "
                        "kind='output'"
                    ),
                    (
                        token["lineage_id"],
                        token["binding_id"],
                        token["publication_unit"],
                    ),
                ).fetchone():
                    raise OperationBlocked("scientific_output_already_accepted")
            except (OperationBlocked, OperationConflict) as error:
                attempt.update(
                    acceptance_state="audit_only", rejection_reason=str(error)
                )
            else:
                record_id = "accepted-" + token["attempt_id"]
                self._append_execution_record(
                    db,
                    token["lineage_id"],
                    {
                        "kind": "output",
                        "record_id": record_id,
                        "binding_id": token["binding_id"],
                        "publication_unit": token["publication_unit"],
                        "artifact_sha256": attempt["artifact_sha256"],
                        "verification_sha256": attempt["verification_sha256"],
                        "attempt_id": token["attempt_id"],
                    },
                )
                if attempt.get("adaptive"):
                    _adaptive_accept(db, record_id)
                attempt.update(acceptance_state="accepted", output_record_id=record_id)
            self._save_state(db, token["lineage_id"], attempt, token["attempt_id"])
            self._event(
                db,
                token["lineage_id"],
                "output_" + attempt["acceptance_state"],
                attempt,
            )
            return attempt
