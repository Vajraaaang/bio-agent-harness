"""Isolated opt-in sanitized outbox consumer; never imported by science.

The software-fixture CLI uses an explicitly selected fake transport. Actual
delivery requires an authorized finite session and an explicit caller-supplied
transport. No selector, numerical executor, matrix or grader is reachable here.
"""
import argparse
from copy import deepcopy
from datetime import datetime, timezone
import json
import math
import os
import re
import time
from uuid import UUID, uuid5
from urllib.request import Request, build_opener, ProxyHandler

from .contracts import canonical_json_bytes
from .langsmith_export import ENDPOINTS, _absolute, _Evidence, _NoRedirect, _time
from .langsmith_outbox import ExportOutbox, _uuid, _digest, digest

SESSION_FORMAT = "devbench.rna-live-trace-session/1"
SESSION_FORMAT_V2 = "devbench.rna-live-trace-session/2"
EVENT_FIELDS = frozenset("event_id trace_id span_id parent_span_id sequence attempt role phase kind source_status dispatch_status capture_status clock metrics reason_code origin".split())


def validate_live_session(value):
    keys = {"format", "workflow_alias", "workflow_binding_digest", "data_scope", "source_digest", "runtime_digest", "policy_digest", "endpoint", "project", "workspace_id", "redaction_version", "event_schema", "allowed_event_fields", "max_spans", "max_bytes", "max_delivery_attempts", "expires_at", "authorize_upload"}
    version = value.get("format") if isinstance(value, dict) else None
    if version == SESSION_FORMAT_V2:
        keys.add("project_id")
    if not isinstance(value, dict) or set(value) != keys or version not in {SESSION_FORMAT, SESSION_FORMAT_V2}:
        raise ValueError("Closed live session manifest required")
    if value["authorize_upload"] is not True:
        raise PermissionError("Explicit live session authorization required")
    if value["data_scope"] not in {"synthetic", "public_processed"} or value["endpoint"] not in ENDPOINTS:
        raise ValueError("Unsupported session scope/endpoint")
    if version == SESSION_FORMAT_V2:
        _uuid(value["project_id"])
        _uuid(value["workspace_id"])
        if value["data_scope"] != "synthetic":
            raise ValueError("Hosted qualification requires synthetic scope")
    _uuid(value["workflow_alias"])
    if value["workspace_id"] is not None:
        _uuid(value["workspace_id"])
    for key in ("source_digest", "runtime_digest", "policy_digest", "workflow_binding_digest"):
        _digest(value[key])
    if not isinstance(value["project"], str) or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,99}", value["project"]) is None:
        raise ValueError("Bounded operator project required")
    if value["redaction_version"] != "rna-allowlist/1" or value["event_schema"] != "devbench.rna-trace-event/1":
        raise ValueError("Unsupported session policy/schema")
    fields = value["allowed_event_fields"]
    if not isinstance(fields, list) or len(fields) != len(EVENT_FIELDS) or not all(isinstance(x, str) for x in fields) or set(fields) != EVENT_FIELDS:
        raise ValueError("Frozen allowlisted event fields required")
    for key, cap in (("max_spans", 2000), ("max_bytes", 8_000_000), ("max_delivery_attempts", 3)):
        if type(value[key]) is not int or not 1 <= value[key] <= cap:
            raise ValueError("Invalid session bound")
    expires = _time(value["expires_at"])
    if expires is None or expires <= datetime.now(timezone.utc):
        raise ValueError("Live session expired")
    if len(canonical_json_bytes(value)) > 4096:
        raise ValueError("Session manifest too large")
    return json.loads(canonical_json_bytes(value))


def validate_live_run(run, operation):
    if operation not in {"start", "finish", "annotation"} or not isinstance(run, dict):
        raise ValueError("Unsupported live operation")
    from .rna_langsmith_export import validate_rna_run
    candidate = json.loads(canonical_json_bytes(run))
    for key in ("start_time","end_time"):
        if key in candidate:
            stamp=_time(candidate[key])
            if stamp is None or stamp.timestamp()<0:
                raise ValueError("Invalid live transport timestamp")
    if operation == "start":
        if "end_time" in candidate:
            raise ValueError("Live start cannot invent completion")
    elif operation == "finish" and _time(candidate.get("end_time")) is None:
        raise ValueError("Finish requires retained terminal time")
    scope = candidate.get("extra", {}).get("metadata", {}).get("data_scope")
    if scope is None:
        tags = candidate.get("tags", [])
        scope = "synthetic" if "synthetic" in tags else "public_processed" if "public_processed" in tags else None
    validate_rna_run(candidate, scope, live_operation=operation)
    if operation == "finish" and candidate["extra"]["metadata"]["source_status"] not in {"completed", "failed", "cancelled", "incomplete", "abstained", "blocked", "skipped", "closed"}:
        raise ValueError("Finish requires retained terminal evidence")
    return run


def sanitize_live_event(event, *, authorized_session_manifest, capability, allocation,
                        actual_children, operation, start_time=None):
    """Trusted local bridge: validate a receipt, construct a new allowlisted run.

    Callers retain the raw receipt locally; only the returned closed operation
    belongs in the separate exporter's sanitized input stream. Identity is
    allocated before dispatch and retained verbatim across later revisions.
    """
    from devbench.analysis.rna_trace_contract import validate_trace_event
    from devbench.analysis.rna_trace_projection import validate_projected_span
    session = validate_live_session(authorized_session_manifest)
    event = validate_trace_event(event)
    if event["bindings"]["workflow"] != session["workflow_binding_digest"]:
        raise ValueError("Receipt belongs to another workflow")
    for key, binding in (("source_digest", "source"), ("runtime_digest", "runtime"), ("policy_digest", "policy")):
        if event["bindings"].get(binding) != session[key]:
            raise ValueError("Receipt source/runtime/policy mismatch")
    if operation not in {"start", "finish", "annotation"}:
        raise ValueError("Invalid lifecycle operation")
    stamp = event["clock"]["utc"]
    unavailable = stamp is None or event["clock"]["timing_basis"] != "observed"
    skewed = stamp is not None and start_time is not None and _time(stamp) < _time(start_time)
    if operation == "finish" and (unavailable or skewed):
        operation = "annotation"
    if start_time is None:
        start_time = stamp if not unavailable else "2000-01-01T00:00:00+00:00"
    if _time(start_time) is None:
        raise ValueError("Retained start timestamp required")
    metadata = {key: event[key] for key in ("role", "phase", "kind", "source_status", "dispatch_status", "capture_status", "reason_code", "origin", "attempt", "sequence")}
    metadata.update(timing_basis=event["clock"]["timing_basis"],
                    evidence_alias=str(uuid5(UUID(session["workflow_alias"]), "evidence:" + event["span_id"])),
                    metrics=event["metrics"], capability=capability, allocation=allocation,
                    actual_children=actual_children, complete=False,
                    investigation_stage="unspecified",
                    source_digest=session["source_digest"], runtime_digest=session["runtime_digest"])
    if skewed:
        metadata["reason_code"] = "clock_disagreement"
    elif unavailable:
        metadata["reason_code"] = "invalid_clock" if event["reason_code"] == "invalid_clock" else "timing_unavailable"
    # Cost receipt references use fresh session aliases rather than raw identity.
    if metadata["metrics"]["provider_cost"]["source_alias"] is not None:
        metadata["metrics"]["provider_cost"]["source_alias"] = str(uuid5(UUID(session["workflow_alias"]), "cost:" + metadata["metrics"]["provider_cost"]["source_alias"]))
    run = dict(id=event["span_id"], trace_id=event["trace_id"], parent_run_id=event["parent_span_id"],
               name="rna." + event["role"] + "." + event["phase"],
               run_type="tool" if event["role"] == "numerical" else "chain",
               inputs={}, outputs={}, extra={"metadata": metadata})
    validate_projected_span(run)
    if not unavailable:
        metadata["transport_timing_basis"] = "local_receipt_clock"
    elif start_time == "2000-01-01T00:00:00+00:00":
        metadata["transport_timing_basis"] = "fixed_display_anchor_only"
    run.update(session_name=session["project"], tags=["devbench", "rna", session["data_scope"]], start_time=start_time)
    if operation == "finish":
        run["end_time"] = stamp
    validate_live_run(run, operation)
    envelope = dict(operation_id=str(uuid5(UUID(session["workflow_alias"]), "operation:" + event["event_id"])),
                    span_id=event["span_id"], revision=event["sequence"], operation=operation,
                    event_digest=digest(canonical_json_bytes(event)),
                    redaction_policy_digest=session["policy_digest"],
                    source_binding_alias=session["workflow_alias"],
                    payload_sha256=digest(canonical_json_bytes(run)))
    if session["format"] == SESSION_FORMAT_V2:
        from .rna_langsmith_verify import bind_remote_proof
        run["session_id"] = session["project_id"]
        run = bind_remote_proof(run, envelope)
        envelope["payload_sha256"] = digest(canonical_json_bytes(run))
    return {"operation": envelope, "payload": run}


class SanitizedOperationFile:
    """Bounded append-only tailer. Incomplete writes wait for a later poll."""
    def __init__(self, path):
        self.path = _absolute(path)
        self.prefix = b""
        self.offset = 0

    def __iter__(self):
        return self

    def __next__(self):
        descriptor = os.open(_absolute(self.path), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as stream:
            import stat
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > 8_000_000:
                raise ValueError("Bounded sanitized operation stream required")
            raw = stream.read(8_000_001)
        if len(raw) > 8_000_000 or not raw.startswith(self.prefix):
            raise ValueError("Sanitized stream changed or exceeded capacity")
        end = raw.find(b"\n", self.offset)
        if end < 0:
            raise StopIteration
        record = raw[self.offset:end]
        if len(record) > 8192:
            raise ValueError("Sanitized operation record too large")
        value = json.loads(record)
        if not isinstance(value, dict) or set(value) != {"operation", "payload"}:
            raise ValueError("Closed sanitized operation required")
        self.offset = end + 1
        self.prefix = raw[:self.offset]
        return value


class ReceiptOperationBridge:
    """Trusted coordinator bridge, separate from the exporter process.

    The bridge reads bounded closed observer receipts through their public
    read-only interface. It retains no scientific data and writes only newly
    constructed sanitized operations. Reopening rebuilds lifecycle state from
    the append-only sanitized stream without changing receipts.
    """
    def __init__(self, *, authorized_session_manifest, output_path, capability,
                 allocation, actual_children):
        self.session = validate_live_session(authorized_session_manifest)
        self.path = _absolute(output_path)
        self.capability, self.allocation, self.actual_children = capability, allocation, actual_children
        self.events, self.starts, self.last_sequences = {}, {}, {}
        self.templates, self.revisions = {}, {}
        if not self.path.exists():
            fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            os.close(fd)
        elif self.path.stat().st_size:
            with self.path.open("rb") as stream:
                stream.seek(-1,os.SEEK_END)
                if stream.read(1)!=b"\n":
                    raise ValueError("Incomplete sanitized bridge stream")
        for item in SanitizedOperationFile(self.path):
            envelope, run = item["operation"], item["payload"]
            validate_live_run(run,envelope["operation"])
            if envelope["source_binding_alias"] != self.session["workflow_alias"] or envelope["redaction_policy_digest"] != self.session["policy_digest"] or digest(canonical_json_bytes(run)) != envelope["payload_sha256"]:
                raise ValueError("Bridge stream session/content mismatch")
            self.events[envelope["operation_id"]] = envelope["event_digest"]
            self.last_sequences[run["id"]] = run["extra"]["metadata"]["sequence"]
            self.revisions[run["id"]] = envelope["revision"]
            self.templates[run["id"]] = run
            if envelope["operation"] == "start":
                self.starts[run["id"]] = run["start_time"]

    def emit_new(self, *, observer, workflow_id):
        validate_live_session(self.session)
        emitted = []
        events = observer.read_events(workflow_id)
        if not isinstance(events,list) or len(events)>2000:
            raise ValueError("Bounded observer receipt list required")
        for source_event in events:
            event=deepcopy(source_event)
            event_key = str(uuid5(UUID(self.session["workflow_alias"]), "operation:" + event["event_id"]))
            event_digest = digest(canonical_json_bytes(event))
            if event_key in self.events:
                if self.events[event_key] != event_digest:
                    raise ValueError("Changed immutable observer event")
                continue
            identity = event["span_id"]
            original = deepcopy(event)
            gap = False
            if identity not in self.starts:
                if event["kind"] not in {"reservation", "dispatch"} or event["source_status"] not in {"reserved", "active", "open", "ready"}:
                    parent = event["parent_span_id"]
                    if parent not in self.templates:
                        raise ValueError("Missing retained parent for structural gap")
                    # A point record lacks live start evidence. Annotate its
                    # retained parent as a gap; never invent a child dispatch
                    # or measured duration to repair an optional observer.
                    template = self.templates[parent]
                    row = template["extra"]["metadata"]
                    event.update(span_id=parent,parent_span_id=template["parent_run_id"],
                                 role=row["role"],phase=row["phase"],attempt=row["attempt"],
                                 sequence=row["sequence"],metrics=deepcopy(row["metrics"]),
                                 kind="gap",source_status=row["source_status"],
                                 dispatch_status=row["dispatch_status"],capture_status=row["capture_status"],
                                 reason_code="observer_absent")
                    event["metrics"]["elapsed_seconds"] = None
                    operation, gap = "annotation", True
                    identity = parent
                else:
                    operation = "start"
                if event["parent_span_id"] is not None and event["parent_span_id"] not in self.starts:
                    raise ValueError("Live parent start evidence missing")
            else:
                if event["sequence"] <= self.last_sequences[identity]:
                    raise ValueError("Observer receipt sequence conflict")
                operation = "finish" if event["source_status"] in {"completed","failed","cancelled","incomplete","abstained","blocked","skipped","closed"} else "annotation"
            item = sanitize_live_event(event, authorized_session_manifest=self.session,
                                       capability=self.capability, allocation=self.allocation,
                                       actual_children=self.actual_children, operation=operation,
                                       start_time=self.starts.get(identity))
            if gap:
                # The parent template is already remote-sanitized. Retain its
                # existing cost receipt alias instead of UUID5-aliasing again.
                item["payload"]["extra"]["metadata"]["metrics"]["provider_cost"]["source_alias"] = row["metrics"]["provider_cost"]["source_alias"]
                item["operation"]["payload_sha256"] = digest(canonical_json_bytes(item["payload"]))
            item["operation"]["event_digest"] = event_digest
            item["operation"]["operation_id"] = event_key
            item["operation"]["revision"] = self.revisions.get(identity,-1)+1
            if self.session["format"] == SESSION_FORMAT_V2:
                from .rna_langsmith_verify import bind_remote_proof
                item["payload"] = bind_remote_proof(item["payload"], item["operation"])
                item["operation"]["payload_sha256"] = digest(canonical_json_bytes(item["payload"]))
            raw = canonical_json_bytes(item)+b"\n"
            if self.path.stat().st_size+len(raw)>min(8_000_000,self.session["max_bytes"]):
                raise ValueError("Sanitized operation stream capacity exceeded")
            descriptor=os.open(_absolute(self.path),os.O_WRONLY|os.O_APPEND|os.O_NOFOLLOW)
            with os.fdopen(descriptor,"wb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            self.events[event_key]=event_digest
            self.last_sequences[original["span_id"]]=original["sequence"]
            self.revisions[identity]=item["operation"]["revision"]
            self.templates[identity]=item["payload"]
            if operation=="start":
                self.starts[identity]=item["payload"]["start_time"]
            emitted.append(item)
        return emitted


class RESTLiveTransport:
    """Same allowlisted endpoint, no proxies/redirects, sanitized error boundary."""
    def __init__(self, *, authorized_session_manifest, api_key):
        self.session = validate_live_session(authorized_session_manifest)
        if self.session["format"] == SESSION_FORMAT_V2:
            raise PermissionError("Hosted /2 project resolution and read schema remain unqualified")
        if not isinstance(api_key, str) or not api_key:
            raise ValueError("Exporter credential required")
        self._api_key = api_key

    def __call__(self, *, method, path, payload, timeout):
        validate_live_session(self.session)
        if method not in {"POST", "PATCH"} or (method == "POST" and path != "/runs"):
            raise ValueError("Unsupported REST operation")
        if method == "PATCH" and path != "/runs/" + _uuid(payload.get("id")):
            raise ValueError("Patch identity mismatch")
        validate_live_run(payload,"start" if method=="POST" else "finish" if "end_time" in payload else "annotation")
        if payload["session_name"] != self.session["project"] or payload["tags"] != ["devbench","rna",self.session["data_scope"]] or any(payload["extra"]["metadata"].get(key) != self.session[key] for key in ("source_digest","runtime_digest")):
            raise ValueError("REST session binding mismatch")
        headers = {"Content-Type": "application/json", "x-api-key": self._api_key}
        if self.session["workspace_id"]:
            headers["x-tenant-id"] = self.session["workspace_id"]
        request = Request(self.session["endpoint"] + path, data=canonical_json_bytes(payload), headers=headers, method=method)
        with build_opener(ProxyHandler({}), _NoRedirect()).open(request, timeout=timeout) as response:
            status = response.status
            response.read(65536)
        if not 200 <= status < 300:
            raise OSError("Remote operation unacknowledged")
        return {"status_code": status}


class LiveTraceExporter:
    @classmethod
    def open(cls, *, authorized_session_manifest, outbox, sanitized_event_source, transport):
        session = validate_live_session(authorized_session_manifest)
        if not isinstance(outbox, ExportOutbox) or outbox.readonly or not callable(transport):
            raise ValueError("Writable export outbox and trusted transport required")
        status = outbox.status()
        if status["endpoint"] != session["endpoint"] or status["project"] != session["project"]:
            raise ValueError("Outbox session endpoint/project mismatch")
        with outbox._db() as db:
            bound_session = db.execute("SELECT * FROM live_session").fetchone()
            session_raw = canonical_json_bytes(session)
            if bound_session is None or bound_session["manifest"] != session_raw or bound_session["manifest_sha256"] != digest(session_raw):
                raise ValueError("Frozen live session binding mismatch")
            export = dict(db.execute("SELECT * FROM exports").fetchone())
            payload = _Evidence(_absolute(export["payload_path"]).parent).read(export["payload_path"])
            if payload["data_scope"] != session["data_scope"] or export["max_attempts"] > session["max_delivery_attempts"]:
                raise ValueError("Session scope/attempt mismatch")
            if db.execute("SELECT count(*) FROM spans").fetchone()[0] > session["max_spans"] or db.execute("SELECT coalesce(sum(length(payload)),0) FROM operations").fetchone()[0] > session["max_bytes"]:
                raise ValueError("Session capacity exceeded")
        instance = cls()
        instance.session, instance.outbox, instance.transport = session, outbox, transport
        instance.source, instance.stopped = iter(sanitized_event_source), False
        return instance

    def drain_once(self, *, max_operations=100, deadline_seconds=5):
        validate_live_session(self.session)
        if self.stopped:
            raise RuntimeError("Exporter shut down")
        if type(max_operations) is not int or not 1 <= max_operations <= 100 or type(deadline_seconds) not in (int, float) or not math.isfinite(deadline_seconds) or not 0 < deadline_seconds <= 5:
            raise ValueError("Invalid live drain bounds")
        start = time.monotonic()
        for _ in range(max_operations):
            if time.monotonic() - start >= deadline_seconds:
                break
            try:
                item = next(self.source)
            except StopIteration:
                break
            if not isinstance(item, dict) or set(item) != {"operation", "payload"}:
                raise ValueError("Closed sanitized source operation required")
            operation, payload = item["operation"], item["payload"]
            if operation.get("source_binding_alias") != self.session["workflow_alias"] or operation.get("redaction_policy_digest") != self.session["policy_digest"]:
                raise ValueError("Session operation binding mismatch")
            validate_live_run(payload, operation.get("operation"))
            metadata = payload.get("extra", {}).get("metadata", {})
            for key in ("source_digest", "runtime_digest"):
                if metadata.get(key) != self.session[key]:
                    raise ValueError("Session source/runtime mismatch")
            if metadata.get("data_scope", self.session["data_scope"]) != self.session["data_scope"]:
                raise ValueError("Session event scope mismatch")
            self.outbox.enqueue_operation(operation, payload, max_spans=self.session["max_spans"],
                                          max_bytes=self.session["max_bytes"])
        remaining = deadline_seconds - (time.monotonic() - start)
        if remaining <= 0:
            return self.outbox.status()
        return self.outbox.deliver(transport=self.transport, allow_upload=True,
                                   timeout_seconds=min(remaining, 5), deadline_seconds=remaining,
                                   max_operations=max_operations)

    def shutdown(self, *, deadline_seconds=5):
        if type(deadline_seconds) not in (int, float) or not math.isfinite(deadline_seconds) or not 0 < deadline_seconds <= 5:
            raise ValueError("Invalid shutdown bound")
        self.stopped = True
        return self.outbox.status()


def main(argv=None):
    parser = argparse.ArgumentParser(description="Separate synthetic fake-transport outbox consumer")
    parser.add_argument("--session", required=True)
    parser.add_argument("--outbox", required=True)
    parser.add_argument("--fake-transport-receipts", required=True)
    parser.add_argument("--operations")
    parser.add_argument("--poll-seconds", type=float, default=0)
    args = parser.parse_args(argv)
    manifest_path = _absolute(args.session)
    session = validate_live_session(_Evidence(manifest_path.parent).read(manifest_path))
    if not math.isfinite(args.poll_seconds) or not 0 <= args.poll_seconds <= 60:
        raise ValueError("Bounded software poll interval required")
    if session["data_scope"] != "synthetic":
        raise ValueError("Software fake transport requires synthetic scope")
    receipts = _absolute(args.fake_transport_receipts)
    fd = os.open(receipts, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as stream:
        def fake_transport(**kwargs):
            stream.write(canonical_json_bytes({"method": kwargs["method"], "span_id": kwargs["payload"]["id"], "acknowledged_at": time.time(), "exporter_pid": os.getpid()}) + b"\n")
            stream.flush()
            os.fsync(stream.fileno())
            return {"status_code": 200}
        exporter = LiveTraceExporter.open(authorized_session_manifest=session,
                                         outbox=ExportOutbox.open(args.outbox),
                                         sanitized_event_source=SanitizedOperationFile(args.operations) if args.operations else (),
                                         transport=fake_transport)
        until = time.monotonic() + args.poll_seconds
        while True:
            result = exporter.drain_once()
            if time.monotonic() >= until:
                break
            time.sleep(min(.1, until-time.monotonic()))
        exporter.shutdown()
    print(json.dumps({"status": result["state"], "counts": result["counts"]}, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
