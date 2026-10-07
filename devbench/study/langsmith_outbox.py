"""Durable, export-only journal. Ambiguous sends require reviewed reconciliation.

This module has no scientific executor or credential lookup. A callable transport
is supplied only after immutable payload and explicit delivery approval checks.
"""
from contextlib import contextmanager
import hashlib
import json
import math
import os
import re
import sqlite3
import time
from uuid import UUID, uuid4
from urllib.error import HTTPError

from .contracts import canonical_json_bytes
from .langsmith_export import ENDPOINTS, FORMAT, _absolute, _Evidence, _validate_redacted_run, _time

STATES = ("prepared", "sending", "acknowledged", "rejected", "retryable", "uncertain", "blocked", "abandoned")
MAX_STORAGE = 32_000_000


class RetryableDelivery(Exception):
    """Trusted transport attests no bytes sent or explicitly safe service rejection."""


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def _uuid(value):
    if not isinstance(value, str) or str(UUID(value)) != value:
        raise ValueError("Canonical opaque UUID required")
    return value


def _digest(value):
    if not isinstance(value, str) or re.fullmatch(r"[a-f0-9]{64}", value) is None:
        raise ValueError("SHA-256 required")
    return value


def validate_payload(payload, max_spans=2000, *, live_operation=None):
    if not isinstance(payload, dict) or payload.get("data_scope") not in {"synthetic", "public_processed"}:
        raise ValueError("Unsupported evidence scope")
    if payload.get("record_format") == "devbench.rna-langsmith-export/1":
        from .rna_langsmith_export import validate_rna_payload
        validate_rna_payload(payload, max_spans=max_spans, live_operation=live_operation)
    elif payload.get("record_format") == FORMAT:
        if payload.get("privacy_policy") != "structure_and_scalars/1":
            raise ValueError("Unsupported redaction policy")
        runs = payload.get("runs")
        if not isinstance(runs, list) or not 1 <= len(runs) <= max_spans:
            raise ValueError("Span cap exceeded")
        for run in runs:
            _validate_redacted_run(run)
            if run.get("run_type") not in {"chain", "tool"} or any(_time(run.get(k)) is None for k in ("start_time", "end_time")):
                raise ValueError("Invalid observational run")
    else:
        raise ValueError("Unsupported payload")
    seen = set()
    for run in payload["runs"]:
        identity = _uuid(run["id"])
        if identity in seen or (run.get("parent_run_id") is not None and run["parent_run_id"] not in seen):
            raise ValueError("Parents must precede unique children")
        seen.add(identity)
    if len(canonical_json_bytes(payload)) > 8_000_000:
        raise ValueError("Payload cap exceeded")
    return payload


class ExportOutbox:
    def __init__(self, path, *, readonly=False):
        self.path = _absolute(path)
        self.readonly = readonly
        if not self.path.is_file():
            raise FileNotFoundError("Outbox does not exist")
        if self.path.stat().st_size > MAX_STORAGE:
            raise ValueError("Outbox storage ceiling exceeded")
        with self._db() as db:
            if db.execute("PRAGMA user_version").fetchone()[0] != 1:
                raise ValueError("Unsupported outbox schema")

    @classmethod
    def create(cls, path, *, payload_path, expected_sha256, endpoint, project, max_attempts=3, live_operation=None, _initial_operations=None):
        path, source = _absolute(path), _absolute(payload_path)
        if endpoint not in ENDPOINTS or not isinstance(project, str) or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,99}", project) is None:
            raise ValueError("Unsupported endpoint/project")
        if type(max_attempts) is not int or not 1 <= max_attempts <= 3:
            raise ValueError("Attempt ceiling must be 1 to 3")
        if path.exists() or path.parent == source.parent or source.is_relative_to(path.parent) or path.is_relative_to(source.parent):
            raise ValueError("Fresh disjoint outbox required")
        reader = _Evidence(source.parent)
        payload = reader.read(source)
        if reader.digest(source) != _digest(expected_sha256):
            raise ValueError("Reviewed payload mismatch")
        if live_operation not in (None, "start") or (live_operation and payload.get("record_format") != "devbench.rna-langsmith-export/1"):
            raise ValueError("Unsupported initial live operation")
        validate_payload(payload, live_operation=live_operation)
        if any(run["session_name"] != project for run in payload["runs"]):
            raise ValueError("Project binding mismatch")
        initial = _initial_operations or {}
        if _initial_operations is not None:
            if set(initial) != {run["id"] for run in payload["runs"]}:
                raise ValueError("Initial operation identity mismatch")
            keys = {"operation_id", "span_id", "revision", "operation", "event_digest", "redaction_policy_digest", "source_binding_alias", "payload_sha256"}
            for run in payload["runs"]:
                envelope = initial[run["id"]]
                if not isinstance(envelope, dict) or set(envelope) != keys or envelope["operation"] != "start" or envelope["span_id"] != run["id"] or envelope["payload_sha256"] != digest(canonical_json_bytes(run)) or type(envelope["revision"]) is not int or envelope["revision"] < 0:
                    raise ValueError("Invalid initial operation envelope")
                for key in ("operation_id", "span_id", "source_binding_alias"):
                    _uuid(envelope[key])
                for key in ("event_digest", "redaction_policy_digest", "payload_sha256"):
                    _digest(envelope[key])
        reader.unchanged()
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        os.close(fd)
        try:
            with sqlite3.connect(path) as db:
                states = repr(STATES)
                db.executescript(f"""
                PRAGMA user_version=1;
                PRAGMA journal_mode=DELETE;
                PRAGMA synchronous=FULL;
                CREATE TABLE exports(export_id TEXT PRIMARY KEY,payload_sha256 TEXT,redaction_version TEXT,
                  endpoint TEXT,project TEXT,state TEXT CHECK(state IN {states}),created_at REAL,
                  payload_path TEXT,max_attempts INTEGER,live_operation TEXT);
                CREATE TABLE spans(span_id TEXT PRIMARY KEY,parent_span_id TEXT,payload_sha256 TEXT,
                  state TEXT CHECK(state IN {states}),attempt_count INTEGER,response_class TEXT,last_receipt_digest TEXT);
                CREATE TABLE operations(operation_id TEXT PRIMARY KEY,span_id TEXT,revision INTEGER,
                  operation TEXT CHECK(operation IN ('start','finish','annotation')),payload_sha256 TEXT,
                  state TEXT CHECK(state IN {states}),payload BLOB,attempt_count INTEGER DEFAULT 0,
                  next_attempt_at REAL DEFAULT 0,envelope BLOB,UNIQUE(span_id,revision));
                CREATE TABLE attempts(attempt_id TEXT PRIMARY KEY,operation_id TEXT,started_at REAL,
                  ended_at REAL,outcome TEXT CHECK(outcome IN {states}),receipt_digest TEXT);
                CREATE TABLE leases(owner_alias TEXT PRIMARY KEY,expires_at REAL);
                CREATE TABLE live_session(manifest BLOB PRIMARY KEY,manifest_sha256 TEXT);
                CREATE TRIGGER immutable_live_session BEFORE UPDATE ON live_session
                  BEGIN SELECT RAISE(ABORT,'immutable live session'); END;
                CREATE TRIGGER immutable_operation BEFORE UPDATE OF operation_id,span_id,revision,operation,payload_sha256,payload,envelope
                  ON operations BEGIN SELECT RAISE(ABORT,'immutable operation'); END;
                CREATE TRIGGER immutable_export BEFORE UPDATE OF payload_sha256,redaction_version,endpoint,project,payload_path,max_attempts,live_operation
                  ON exports BEGIN SELECT RAISE(ABORT,'immutable export'); END;
                """)
                db.execute("INSERT INTO exports VALUES(?,?,?,?,?,?,?,?,?,?)", (str(uuid4()), expected_sha256,
                           payload["privacy_policy"], endpoint, project, "prepared", time.time(), str(source), max_attempts, live_operation))
                for run in payload["runs"]:
                    raw = canonical_json_bytes(run)
                    db.execute("INSERT INTO spans VALUES(?,?,?,?,0,NULL,NULL)", (run["id"], run.get("parent_run_id"), digest(raw), "prepared"))
                    envelope = initial.get(run["id"])
                    db.execute("INSERT INTO operations(operation_id,span_id,revision,operation,payload_sha256,state,payload,envelope) VALUES(?,?,?,'start',?,'prepared',?,?)", ((envelope or {}).get("operation_id", str(uuid4())), run["id"], (envelope or {}).get("revision",0), digest(raw), raw, canonical_json_bytes(envelope) if envelope else None))
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        return cls(path)

    @classmethod
    def create_live(cls, path, *, payload_path, expected_sha256, authorized_session_manifest, initial_operations):
        from .rna_langsmith_live import validate_live_session
        session = validate_live_session(authorized_session_manifest)
        if not isinstance(initial_operations, list) or not initial_operations:
            raise ValueError("Bound live start operations required")
        initial = {}
        initial_payloads = {}
        for item in initial_operations:
            if not isinstance(item, dict) or set(item) != {"operation", "payload"}:
                raise ValueError("Invalid bound live start operation")
            envelope = item["operation"]
            if session["format"] == "devbench.rna-live-trace-session/2":
                from .rna_langsmith_verify import validate_remote_proof
                proof = validate_remote_proof(item["payload"])
                if proof["operation_id"] != envelope.get("operation_id") or proof["revision"] != envelope.get("revision"):
                    raise ValueError("Initial remote proof operation/revision mismatch")
            if envelope.get("source_binding_alias") != session["workflow_alias"] or envelope.get("redaction_policy_digest") != session["policy_digest"]:
                raise ValueError("Initial live session binding mismatch")
            if envelope.get("span_id") in initial:
                raise ValueError("Duplicate initial live start")
            initial[envelope.get("span_id")] = envelope
            initial_payloads[envelope.get("span_id")] = item["payload"]
        # Complete authorization validation before creating durable storage.
        reader = _Evidence(_absolute(payload_path).parent)
        payload = reader.read(payload_path)
        if reader.digest(_absolute(payload_path)) != _digest(expected_sha256):
            raise ValueError("Initial live payload hash mismatch")
        validate_payload(payload, max_spans=session["max_spans"], live_operation="start")
        if payload["data_scope"] != session["data_scope"] or len(canonical_json_bytes(payload)) > session["max_bytes"]:
            raise ValueError("Initial live scope/cap mismatch")
        for run in payload["runs"]:
            if session["format"] == "devbench.rna-live-trace-session/2":
                from .rna_langsmith_verify import validate_remote_proof
                proof = validate_remote_proof(run)
                envelope = initial.get(run["id"], {})
                if (canonical_json_bytes(run) != canonical_json_bytes(initial_payloads.get(run["id"])) or
                    run.get("session_id") != session["project_id"] or
                    proof["operation_id"] != envelope.get("operation_id") or
                    proof["revision"] != envelope.get("revision")):
                    raise ValueError("Initial wire UUID/project/proof binding mismatch")
            if any(run["extra"]["metadata"].get(key) != session[key] for key in ("source_digest", "runtime_digest")):
                raise ValueError("Initial source/runtime binding mismatch")
        box = cls.create(path, payload_path=payload_path, expected_sha256=expected_sha256,
                         endpoint=session["endpoint"], project=session["project"],
                         max_attempts=session["max_delivery_attempts"], live_operation="start", _initial_operations=initial)
        with box._db() as db:
            export = box._check_payload(db)
            payload = _Evidence(_absolute(payload_path).parent).read(payload_path)
            if payload["data_scope"] != session["data_scope"]:
                raise ValueError("Live scope binding mismatch")
            for run in payload["runs"]:
                metadata = run["extra"]["metadata"]
                if any(metadata.get(key) != session[key] for key in ("source_digest", "runtime_digest")):
                    raise ValueError("Live source/runtime binding mismatch")
            raw = canonical_json_bytes(session)
            db.execute("INSERT INTO live_session VALUES(?,?)", (raw, digest(raw)))
        return box

    @classmethod
    def open(cls, path):
        box = cls(path)
        with box._db() as db:
            db.execute("BEGIN IMMEDIATE")
            if not db.execute("SELECT 1 FROM leases WHERE expires_at>?", (time.time(),)).fetchone():
                db.execute("UPDATE operations SET state='uncertain' WHERE state='sending'")
                db.execute("UPDATE spans SET state='uncertain' WHERE span_id IN (SELECT span_id FROM operations WHERE state='uncertain')")
        return box

    @classmethod
    def open_readonly(cls, path):
        return cls(path, readonly=True)

    @contextmanager
    def _db(self):
        # A live DELETE-journal database can change while observed. Ordinary
        # read-only SQLite locking detects concurrent commits; immutable mode
        # would incorrectly promise that no writer exists.
        uri = self.path.as_uri() + ("?mode=ro" if self.readonly else "?mode=rw")
        db = sqlite3.connect(uri, uri=True, timeout=0.1)
        db.row_factory = sqlite3.Row
        try:
            if not self.readonly:
                db.execute("PRAGMA synchronous=FULL")
            elif db.execute("PRAGMA journal_mode").fetchone()[0].lower()!="delete":
                raise ValueError("Read-only outbox requires existing DELETE journal mode")
            yield db
            if not self.readonly:
                db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def status(self):
        with self._db() as db:
            self._check_payload(db)
            export = dict(db.execute("SELECT * FROM exports").fetchone())
            counts = {state: 0 for state in STATES}
            for state, count in db.execute("SELECT state,count(*) FROM operations GROUP BY state"):
                counts[state] = count
            spans = [dict(row) for row in db.execute("SELECT * FROM spans ORDER BY rowid")]
        return {"format": "devbench.langsmith-outbox-status/1", "payload_sha256": export["payload_sha256"],
                "state": export["state"], "counts": counts, "spans": spans,
                "endpoint": export["endpoint"], "project": export["project"]}

    def _check_payload(self, db):
        export = dict(db.execute("SELECT * FROM exports").fetchone())
        source = _absolute(export["payload_path"])
        reader = _Evidence(source.parent)
        payload = reader.read(source)
        if reader.digest(source) != export["payload_sha256"]:
            raise ValueError("Immutable reviewed payload changed")
        validate_payload(payload, live_operation=export["live_operation"])
        binding = db.execute("SELECT * FROM live_session").fetchone()
        if binding is not None and binding["manifest_sha256"] != digest(binding["manifest"]):
            raise ValueError("Immutable live session digest mismatch")
        for row in db.execute("SELECT payload,payload_sha256,operation,envelope FROM operations"):
            if digest(row["payload"]) != row["payload_sha256"]:
                raise ValueError("Immutable operation hash mismatch")
            if row["envelope"] is not None:
                from .rna_langsmith_live import validate_live_run
                validate_live_run(json.loads(row["payload"]), row["operation"])
                envelope = json.loads(row["envelope"])
                if envelope["payload_sha256"] != row["payload_sha256"]:
                    raise ValueError("Immutable envelope binding mismatch")
                operation_payload = json.loads(row["payload"])
                if "session_id" in operation_payload:
                    from .rna_langsmith_verify import validate_remote_proof
                    proof = validate_remote_proof(operation_payload)
                    if proof["operation_id"] != envelope["operation_id"] or proof["revision"] != envelope["revision"]:
                        raise ValueError("Immutable remote proof/envelope mismatch")
        return export

    def enqueue_operation(self, operation, payload, *, max_spans=2000, max_bytes=8_000_000):
        """Add a validated live revision. Payload validation precedes any journal write."""
        if self.readonly:
            raise PermissionError("Read-only outbox")
        for value, cap in ((max_spans, 2000), (max_bytes, 8_000_000)):
            if type(value) is not int or not 1 <= value <= cap:
                raise ValueError("Invalid queue capacity")
        keys = {"operation_id", "span_id", "revision", "operation", "event_digest", "redaction_policy_digest", "source_binding_alias", "payload_sha256"}
        if not isinstance(operation, dict) or set(operation) != keys:
            raise ValueError("Closed operation envelope required")
        for key in ("operation_id", "span_id", "source_binding_alias"):
            _uuid(operation[key])
        for key in ("event_digest", "redaction_policy_digest", "payload_sha256"):
            _digest(operation[key])
        if type(operation["revision"]) is not int or operation["revision"] < 0 or operation["operation"] not in {"start", "finish", "annotation"}:
            raise ValueError("Invalid operation revision")
        raw = canonical_json_bytes(payload)
        envelope = canonical_json_bytes(operation)
        if len(raw) > 4096 or digest(raw) != operation["payload_sha256"] or payload.get("id") != operation["span_id"]:
            raise ValueError("Operation payload binding mismatch")
        from .rna_langsmith_live import validate_live_run
        validate_live_run(payload, operation["operation"])
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            export = self._check_payload(db)
            binding = db.execute("SELECT * FROM live_session").fetchone()
            if binding is None:
                raise ValueError("Live operation requires frozen session")
            from .rna_langsmith_live import validate_live_session
            session = validate_live_session(json.loads(binding["manifest"]))
            max_spans=min(max_spans,session["max_spans"])
            max_bytes=min(max_bytes,session["max_bytes"])
            if operation["source_binding_alias"] != session["workflow_alias"] or operation["redaction_policy_digest"] != session["policy_digest"] or any(payload["extra"]["metadata"].get(k) != session[k] for k in ("source_digest", "runtime_digest")):
                raise ValueError("Immutable session operation binding mismatch")
            if payload["tags"] != ["devbench", "rna", session["data_scope"]]:
                raise ValueError("Immutable session data scope mismatch")
            if session["format"] == "devbench.rna-live-trace-session/2":
                from .rna_langsmith_verify import validate_remote_proof
                proof = validate_remote_proof(payload)
                if payload.get("session_id") != session["project_id"] or proof["operation_id"] != operation["operation_id"] or proof["revision"] != operation["revision"]:
                    raise ValueError("UUID project/proof binding mismatch")
            if payload["session_name"] != export["project"]:
                raise ValueError("Operation project mismatch")
            old = db.execute("SELECT * FROM operations WHERE operation_id=? OR (span_id=? AND revision=?)", (operation["operation_id"], operation["span_id"], operation["revision"])).fetchone()
            if old:
                if old["operation_id"] != operation["operation_id"] or old["payload_sha256"] != digest(raw) or old["operation"] != operation["operation"] or old["envelope"] != envelope:
                    raise ValueError("Immutable revision conflict")
                return {"status": "existing"}
            rows = db.execute("SELECT operation,revision FROM operations WHERE span_id=? ORDER BY revision", (operation["span_id"],)).fetchall()
            if (not rows and operation["operation"] != "start") or (rows and (operation["operation"] == "start" or operation["revision"] <= rows[-1]["revision"])):
                raise ValueError("Start must precede increasing finish/annotation revisions")
            if rows:
                first = json.loads(db.execute("SELECT payload FROM operations WHERE span_id=? ORDER BY revision LIMIT 1", (operation["span_id"],)).fetchone()[0])
                if any(payload.get(key) != first.get(key) for key in ("trace_id", "parent_run_id", "name", "run_type", "session_name", "tags", "start_time")) or any(payload["extra"]["metadata"].get(key) != first["extra"]["metadata"].get(key) for key in ("role", "phase", "attempt", "source_digest", "runtime_digest")):
                    raise ValueError("Immutable live span identity changed")
            if not rows:
                parent = payload.get("parent_run_id")
                if parent is None:
                    raise ValueError("One retained workflow root required")
                if parent is not None and not db.execute("SELECT 1 FROM spans WHERE span_id=?", (parent,)).fetchone():
                    raise ValueError("Parent missing")
                parent_payload=json.loads(db.execute("SELECT payload FROM operations WHERE span_id=? AND operation='start'",(parent,)).fetchone()[0])
                if payload["trace_id"]!=parent_payload["trace_id"]:
                    raise ValueError("Child trace differs from retained parent")
                if db.execute("SELECT count(*) FROM spans").fetchone()[0] >= max_spans:
                    raise ValueError("Span cap exceeded")
                db.execute("INSERT INTO spans VALUES(?,?,?,?,0,NULL,NULL)", (operation["span_id"], parent, digest(raw), "prepared"))
            total = db.execute("SELECT coalesce(sum(length(payload)),0) FROM operations").fetchone()[0]
            if total + len(raw) > max_bytes or self.path.stat().st_size + len(raw) + 65536 > MAX_STORAGE:
                raise ValueError("Outbox capacity exceeded")
            db.execute("INSERT INTO operations(operation_id,span_id,revision,operation,payload_sha256,state,payload,envelope) VALUES(?,?,?,?,?,'prepared',?,?)", (operation["operation_id"], operation["span_id"], operation["revision"], operation["operation"], digest(raw), raw, envelope))
        return {"status": "prepared"}

    def deliver(self, *, transport, allow_upload=False, timeout_seconds=15, deadline_seconds=60, max_operations=100):
        if allow_upload is not True or self.readonly:
            raise PermissionError("Explicit upload approval and writable outbox required")
        for value, ceiling in ((timeout_seconds, 60), (deadline_seconds, 60)):
            if type(value) not in (int, float) or not math.isfinite(value) or not 0 < value <= ceiling:
                raise ValueError("Invalid delivery time bound")
        if type(max_operations) is not int or not 1 <= max_operations <= 100:
            raise ValueError("Invalid drain bound")
        owner, end = str(uuid4()), time.monotonic() + deadline_seconds
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            export = self._check_payload(db)
            bound_session = db.execute("SELECT manifest FROM live_session").fetchone()
            if bound_session is not None:
                from .rna_langsmith_live import validate_live_session
                validate_live_session(json.loads(bound_session[0]))
            if db.execute("SELECT 1 FROM leases WHERE expires_at>?", (time.time(),)).fetchone():
                raise RuntimeError("Another exporter holds the lease")
            db.execute("DELETE FROM leases")
            db.execute("UPDATE operations SET state='uncertain' WHERE state='sending'")
            db.execute("UPDATE spans SET state='uncertain' WHERE span_id IN (SELECT span_id FROM operations WHERE state='uncertain')")
            db.execute("INSERT INTO leases VALUES(?,?)", (owner, time.time() + deadline_seconds + 5))
        try:
            for _ in range(max_operations):
                remaining = end - time.monotonic()
                if remaining <= 0:
                    break
                with self._db() as db:
                    db.execute("BEGIN IMMEDIATE")
                    if bound_session is not None:
                        validate_live_session(json.loads(bound_session[0]))
                    if not db.execute("SELECT 1 FROM leases WHERE owner_alias=? AND expires_at>?", (owner, time.time())).fetchone():
                        raise RuntimeError("Exporter lease expired")
                    row = db.execute("""SELECT o.* FROM operations o JOIN spans s USING(span_id)
                        WHERE o.state IN ('prepared','retryable') AND o.next_attempt_at<=?
                        AND o.attempt_count<?
                        AND NOT EXISTS(SELECT 1 FROM operations prior WHERE prior.span_id=o.span_id AND prior.revision<o.revision AND prior.state!='acknowledged')
                        AND (s.parent_span_id IS NULL OR EXISTS(SELECT 1 FROM operations p WHERE p.span_id=s.parent_span_id AND p.operation='start' AND p.state='acknowledged'))
                        ORDER BY o.rowid LIMIT 1""", (time.time(), export["max_attempts"])).fetchone()
                    if row is None:
                        break
                    row = dict(row)
                    attempt = str(uuid4())
                    db.execute("UPDATE operations SET state='sending',attempt_count=attempt_count+1 WHERE operation_id=?", (row["operation_id"],))
                    db.execute("UPDATE spans SET state='sending',attempt_count=attempt_count+1 WHERE span_id=?", (row["span_id"],))
                    db.execute("INSERT INTO attempts VALUES(?,?,?,NULL,'sending',NULL)", (attempt, row["operation_id"], time.time()))
                outcome = "acknowledged"
                try:
                    response = transport(method="POST" if row["operation"] == "start" else "PATCH",
                              path="/runs" if row["operation"] == "start" else "/runs/" + row["span_id"],
                              payload=json.loads(row["payload"]), timeout=min(timeout_seconds, remaining))
                    if not isinstance(response, dict) or set(response) != {"status_code"} or type(response["status_code"]) is not int:
                        outcome = "uncertain"
                    elif not 200 <= response["status_code"] < 300:
                        outcome = "rejected" if 400 <= response["status_code"] < 500 else "uncertain"
                except RetryableDelivery:
                    outcome = "retryable"
                except HTTPError as error:
                    outcome = "rejected" if 400 <= error.code < 500 else "uncertain"
                except (Exception, KeyboardInterrupt):
                    outcome = "uncertain"
                if outcome == "retryable" and row["attempt_count"] + 1 >= export["max_attempts"]:
                    outcome = "blocked"
                receipt = digest(canonical_json_bytes({"operation_id": row["operation_id"], "outcome": outcome}))
                with self._db() as db:
                    db.execute("BEGIN IMMEDIATE")
                    delay = min(15, 2 ** row["attempt_count"]) if outcome == "retryable" else 0
                    db.execute("UPDATE operations SET state=?,next_attempt_at=? WHERE operation_id=?", (outcome, time.time() + delay, row["operation_id"]))
                    db.execute("UPDATE spans SET state=?,response_class=?,last_receipt_digest=? WHERE span_id=?", (outcome, outcome, receipt, row["span_id"]))
                    db.execute("UPDATE attempts SET ended_at=?,outcome=?,receipt_digest=? WHERE attempt_id=?", (time.time(), outcome, receipt, attempt))
                if outcome != "acknowledged":
                    break
        finally:
            with self._db() as db:
                db.execute("DELETE FROM leases WHERE owner_alias=?", (owner,))
                counts = dict(db.execute("SELECT state,count(*) FROM operations GROUP BY state"))
                state = "acknowledged" if set(counts) == {"acknowledged"} else "uncertain" if counts.get("uncertain") else "blocked" if counts.get("rejected") or counts.get("blocked") else "prepared"
                db.execute("UPDATE exports SET state=?", (state,))
        return self.status()

    def reconcile(self, span_id, *, remote_state, reviewed_receipt_digest):
        """Operator attests content/version, not merely existence of an ID.

        The reviewed receipt digest references separately retained verification;
        this method has no remote-read or scientific continuation authority.
        """
        if self.readonly:
            raise PermissionError("Read-only outbox")
        _uuid(span_id)
        _digest(reviewed_receipt_digest)
        if remote_state not in {"acknowledged", "absent_verified"}:
            raise ValueError("Verified content/version or verified absence required")
        state = "acknowledged" if remote_state == "acknowledged" else "retryable"
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            binding = db.execute("SELECT manifest FROM live_session").fetchone()
            if binding and json.loads(binding[0]).get("format") == "devbench.rna-live-trace-session/2":
                raise PermissionError("UUID-bound hosted session requires exact reviewed verifier receipt")
            if db.execute("SELECT 1 FROM leases WHERE expires_at>?", (time.time(),)).fetchone():
                raise RuntimeError("Active delivery cannot be reconciled")
            result = db.execute("UPDATE operations SET state=?,next_attempt_at=0 WHERE span_id=? AND state='uncertain'", (state, span_id))
            if result.rowcount != 1:
                raise ValueError("Exactly one uncertain revision required")
            db.execute("UPDATE spans SET state=?,last_receipt_digest=?,response_class='reviewed_reconciliation' WHERE span_id=?", (state, reviewed_receipt_digest, span_id))
        return self.status()


    def reconcile_reviewed(self, receipt, *, approval_record, verifier):
        """Atomic local CAS of one exact uncertain operation, never remote resend.

        The approval file must exist and bind the independently issued observation.
        A digest alone, state-only observation or eventual-consistency miss cannot
        acknowledge or release an operation. Legacy name-only replay is unchanged.
        """
        if self.readonly:
            raise PermissionError("Read-only outbox")
        from .rna_langsmith_verify import RemoteRunVerifier
        if not isinstance(verifier, RemoteRunVerifier):
            raise ValueError("Independent verifier instance required")
        identity = verifier.validate_reviewed(receipt, approval_record=approval_record)
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            export = self._check_payload(db)
            if db.execute("SELECT 1 FROM leases WHERE expires_at>?", (time.time(),)).fetchone():
                raise RuntimeError("Active delivery cannot be reconciled")
            binding = db.execute("SELECT * FROM live_session").fetchone()
            if binding is None:
                raise ValueError("Frozen UUID-bound session required")
            from .rna_langsmith_live import validate_live_session
            session = validate_live_session(json.loads(binding["manifest"]))
            if session["format"] != "devbench.rna-live-trace-session/2" or binding["manifest_sha256"] != digest(binding["manifest"]):
                raise ValueError("Frozen session hash mismatch")
            if (receipt["session_digest"] != binding["manifest_sha256"] or
                receipt["endpoint"] != export["endpoint"] or
                receipt["project_id"] != session["project_id"] or
                receipt["workspace_id"] != session["workspace_id"] or
                receipt["qualification_id"] != verifier.manifest["qualification_id"]):
                raise ValueError("Exact receipt session/target mismatch")
            rows = db.execute("SELECT * FROM operations WHERE span_id=? AND state='uncertain'", (receipt["span_id"],)).fetchall()
            if len(rows) != 1:
                raise ValueError("Exactly one uncertain revision required")
            row = rows[0]
            if (row["operation_id"] != receipt["operation_id"] or row["revision"] != receipt["revision"] or
                row["payload_sha256"] != receipt["expected_payload_digest"]):
                raise ValueError("Stale or cross-operation observation")
            result = db.execute("UPDATE operations SET state='acknowledged',next_attempt_at=0 WHERE operation_id=? AND revision=? AND payload_sha256=? AND state='uncertain'", (receipt["operation_id"],receipt["revision"],receipt["expected_payload_digest"]))
            if result.rowcount != 1:
                raise ValueError("Uncertain-operation CAS failed")
            db.execute("UPDATE spans SET state='acknowledged',last_receipt_digest=?,response_class='reviewed_exact_reconciliation' WHERE span_id=?", (digest(canonical_json_bytes(receipt)),receipt["span_id"]))
            unresolved = db.execute("SELECT 1 FROM operations WHERE state!='acknowledged'").fetchone()
            db.execute("UPDATE exports SET state=?", ("prepared" if unresolved else "acknowledged",))
        verifier.consume_reviewed(identity)
        return self.status()
