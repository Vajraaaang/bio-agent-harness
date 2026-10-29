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
from uuid import UUID, uuid4, uuid5
from urllib.request import Request, build_opener, ProxyHandler

from .contracts import canonical_json_bytes
from .langsmith_export import ENDPOINTS, _absolute, _Evidence, _NoRedirect, _time
from .langsmith_outbox import ExportOutbox, _uuid, _digest, digest

SESSION_FORMAT = "devbench.rna-live-trace-session/1"
SESSION_FORMAT_V2 = "devbench.rna-live-trace-session/2"
SESSION_FORMAT_V3 = "devbench.rna-live-trace-session/3"
SESSION_FORMAT_V4 = "devbench.rna-live-trace-session/4"
SESSION_FORMAT_V5 = "devbench.rna-live-trace-session/5"
EVENT_FIELDS = frozenset("event_id trace_id span_id parent_span_id sequence attempt role phase kind source_status dispatch_status capture_status clock metrics reason_code origin".split())


def workflow_error_code(source_status):
    """Closed /3 display codes; never accept exception objects or raw error text."""
    from devbench.analysis.rna_trace_contract import SOURCE_STATUSES
    if not isinstance(source_status, str) or source_status not in SOURCE_STATUSES:
        raise ValueError("Known workflow source status required")
    return {"failed": "devbench.workflow_failed", "blocked": "devbench.workflow_blocked",
            "cancelled": "devbench.workflow_cancelled",
            "incomplete": "devbench.workflow_incomplete"}.get(source_status)


def _root_dotted_order(start_time, identity):
    """Documented root order: retained UTC start followed by its canonical UUID."""
    stamp = _time(start_time)
    if stamp is None:
        raise ValueError("Retained root start timestamp required")
    return stamp.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + _uuid(identity)


def _validate_workflow_dotted_order(run):
    """Validate only the explicit /3 canonical wire order, without inferring times."""
    order = run.get("dotted_order")
    if not isinstance(order, str):
        raise ValueError("Retained workflow dotted order required")
    parts = order.split(".")
    if not 1 <= len(parts) <= 8:
        raise ValueError("Bounded workflow dotted order required")
    identities = []
    for part in parts:
        if re.fullmatch(r"\d{8}T\d{12}Z[0-9a-f-]{36}", part) is None:
            raise ValueError("Canonical workflow order component required")
        stamp = datetime.strptime(part[:22], "%Y%m%dT%H%M%S%fZ").replace(tzinfo=timezone.utc)
        identity = _uuid(part[22:])
        if part != _root_dotted_order(stamp.isoformat(), identity):
            raise ValueError("Canonical workflow order component required")
        identities.append(identity)
    if (len(set(identities)) != len(identities) or identities[0] != run.get("trace_id")
            or parts[-1] != _root_dotted_order(run.get("start_time"), run.get("id"))):
        raise ValueError("Workflow order identity mismatch")
    parent = run.get("parent_run_id")
    if len(parts) == 1:
        if parent is not None or run.get("id") != run.get("trace_id"):
            raise ValueError("Exact workflow root order required")
    elif parent != identities[-2]:
        raise ValueError("Workflow order parent mismatch")
    return run


def validate_live_session(value):
    return _validate_session(value)


def validate_historical_context_session(value):
    """Inspect retained /5 structure without granting present effect authority.

    Original authorization and expiry remain facts in the returned copy. No
    current clock is changed and an expired session stays expired for live use.
    """
    return _validate_session(value, historical=True)


def _validate_session(value, *, historical=False):
    keys = {"format", "workflow_alias", "workflow_binding_digest", "data_scope", "source_digest", "runtime_digest", "policy_digest", "endpoint", "project", "workspace_id", "redaction_version", "event_schema", "allowed_event_fields", "max_spans", "max_bytes", "max_delivery_attempts", "expires_at", "authorize_upload"}
    version = value.get("format") if isinstance(value, dict) else None
    if version in {SESSION_FORMAT_V2, SESSION_FORMAT_V3, SESSION_FORMAT_V4, SESSION_FORMAT_V5}:
        keys.add("project_id")
    if version in {SESSION_FORMAT_V3, SESSION_FORMAT_V4, SESSION_FORMAT_V5}:
        keys.add("span_topology")
    if version in {SESSION_FORMAT_V4, SESSION_FORMAT_V5}:
        keys.add("context_profile")
    if not isinstance(value, dict) or set(value) != keys or version not in {SESSION_FORMAT, SESSION_FORMAT_V2, SESSION_FORMAT_V3, SESSION_FORMAT_V4, SESSION_FORMAT_V5}:
        raise ValueError("Closed live session manifest required")
    if historical and (version != SESSION_FORMAT_V5 or type(value["authorize_upload"]) is not bool):
        raise ValueError("Historical /5 session with original boolean authorization required")
    if not historical and value["authorize_upload"] is not True:
        raise PermissionError("Explicit live session authorization required")
    if value["data_scope"] not in {"synthetic", "public_processed"} or value["endpoint"] not in ENDPOINTS:
        raise ValueError("Unsupported session scope/endpoint")
    if version in {SESSION_FORMAT_V2, SESSION_FORMAT_V3, SESSION_FORMAT_V4, SESSION_FORMAT_V5}:
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
    if version == SESSION_FORMAT_V3:
        from .rna_hosted_workflow_contract import validate_workflow_topology
        validate_workflow_topology(value["span_topology"])
        if value["max_spans"] != 8:
            raise ValueError("Workflow session requires eight spans")
    if version in {SESSION_FORMAT_V4, SESSION_FORMAT_V5}:
        from .rna_hosted_context_contract import validate_context_topology, validate_context_profile, CONTEXT_CAPS
        validate_context_topology(value["span_topology"])
        if version == SESSION_FORMAT_V5:
            from .rna_hosted_context_contract import validate_generation_context_profile
            validate_generation_context_profile(value["context_profile"], topology=value["span_topology"])
        else:
            validate_context_profile(value["context_profile"], topology=value["span_topology"])
        if (value["max_spans"] != len(value["span_topology"])
                or value["max_bytes"] > CONTEXT_CAPS["max_bytes"] or value["max_delivery_attempts"] != 1):
            raise ValueError("Exact bounded context session required")
    expires = _time(value["expires_at"])
    if expires is None or not historical and expires <= datetime.now(timezone.utc):
        raise ValueError("Live session expired")
    if len(canonical_json_bytes(value)) > (32768 if version in {SESSION_FORMAT_V4, SESSION_FORMAT_V5} else 4096):
        raise ValueError("Session manifest too large")
    return json.loads(canonical_json_bytes(value))


def validate_live_run(run, operation, *, session_format=None):
    if session_format in {SESSION_FORMAT_V4, SESSION_FORMAT_V5}:
        return _validate_context_wire_run(run, operation, generation=session_format == SESSION_FORMAT_V5)
    if session_format not in {None, SESSION_FORMAT, SESSION_FORMAT_V2, SESSION_FORMAT_V3}:
        raise ValueError("Unsupported live session format")
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
    if session_format == SESSION_FORMAT_V3:
        metadata = candidate.get("extra", {}).get("metadata", {})
        if (operation not in {"start", "finish"} or metadata.get("kind") == "gap"
                or metadata.get("timing_basis") != "observed"
                or metadata.get("transport_timing_basis") != "local_receipt_clock"
                or type(metadata.get("allocation")) is not int or metadata["allocation"] != 0
                or type(metadata.get("actual_children")) is not int or metadata["actual_children"] != 0):
            raise ValueError("Workflow qualification requires observed lifecycle receipts")
        if operation == "start" and (metadata.get("kind") not in {"reservation", "dispatch"}
                or metadata.get("source_status") not in {"reserved", "active", "open", "ready"}):
            raise ValueError("Workflow qualification requires retained start evidence")
        _validate_workflow_dotted_order(candidate)
        validate_rna_run(candidate, scope, live_operation=operation, session_format=session_format)
    else:
        validate_rna_run(candidate, scope, live_operation=operation)
    if operation == "finish" and candidate["extra"]["metadata"]["source_status"] not in {"completed", "failed", "cancelled", "incomplete", "abstained", "blocked", "skipped", "closed"}:
        raise ValueError("Finish requires retained terminal evidence")
    return run


def sanitize_live_event(event, *, authorized_session_manifest, capability, allocation,
                        actual_children, operation, start_time=None, parent_dotted_order=None):
    """Trusted local bridge: validate a receipt, construct a new allowlisted run.

    Callers retain the raw receipt locally; only the returned closed operation
    belongs in the separate exporter's sanitized input stream. Identity is
    allocated before dispatch and retained verbatim across later revisions.
    """
    from devbench.analysis.rna_trace_contract import validate_trace_event
    from devbench.analysis.rna_trace_projection import validate_projected_span
    session = validate_live_session(authorized_session_manifest)
    if session["format"] in {SESSION_FORMAT_V4, SESSION_FORMAT_V5}:
        raise ValueError("Use the explicit context observation bridge")
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
    if session["format"] == SESSION_FORMAT_V3:
        _validate_workflow_event(event, session)
        if (operation not in {"start", "finish"} or unavailable or skewed
                or type(allocation) is not int or allocation != 0
                or type(actual_children) is not int or actual_children != 0
                or (operation == "finish" and start_time is None)
                or (operation == "start" and start_time is not None and start_time != stamp)):
            raise ValueError("Workflow qualification requires retained lifecycle timing")
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
    if session["format"] == SESSION_FORMAT_V3 and operation == "finish":
        error = workflow_error_code(event["source_status"])
        if error is not None:
            run["error"] = error
    if session["format"] in {SESSION_FORMAT_V2, SESSION_FORMAT_V3}:
        run["session_id"] = session["project_id"]
        if run["id"] == run["trace_id"] and run.get("parent_run_id") is None:
            run.pop("parent_run_id", None)
            run["dotted_order"] = _root_dotted_order(run["start_time"], run["id"])
        elif session["format"] == SESSION_FORMAT_V3:
            if not isinstance(parent_dotted_order, str):
                raise ValueError("Retained parent workflow order required")
            run["dotted_order"] = parent_dotted_order + "." + _root_dotted_order(run["start_time"], run["id"])
    envelope = dict(operation_id=str(uuid5(UUID(session["workflow_alias"]), "operation:" + event["event_id"])),
                    span_id=event["span_id"], revision=event["sequence"], operation=operation,
                    event_digest=digest(canonical_json_bytes(event)),
                    redaction_policy_digest=session["policy_digest"],
                    source_binding_alias=session["workflow_alias"],
                    payload_sha256=digest(canonical_json_bytes(run)))
    if session["format"] in {SESSION_FORMAT_V2, SESSION_FORMAT_V3}:
        from .rna_langsmith_verify import bind_remote_proof
        run = bind_remote_proof(run, envelope)
        envelope["payload_sha256"] = digest(canonical_json_bytes(run))
    if session["format"] == SESSION_FORMAT_V3:
        validate_live_run(run, operation, session_format=SESSION_FORMAT_V3)
    return {"operation": envelope, "payload": run}


def _validate_workflow_event(event, session):
    slots = {row["span_id"]: row for row in session["span_topology"]}
    slot = slots.get(event["span_id"])
    if (slot is None or event["trace_id"] != session["span_topology"][0]["span_id"]
            or any(event[key] != slot[key] for key in ("parent_span_id", "role", "phase", "attempt"))):
        raise ValueError("Receipt differs from frozen workflow topology")


CONTEXT_SOURCE_FORMAT = "devbench.rna-context-source-event/4"
_CONTEXT_TERMINAL = frozenset({"completed", "failed", "cancelled", "incomplete", "abstained", "blocked", "skipped", "closed"})
_CONTEXT_EXTRA = frozenset({"context_alias", "parent_context_alias", "start_observed", "source_sequence", "source_event_count", "capture_event_count", "source_event_digests", "provenance"})
_CONTEXT_BASE_METADATA = frozenset("role phase kind source_status dispatch_status capture_status reason_code origin attempt sequence timing_basis evidence_alias metrics capability allocation actual_children complete investigation_stage source_digest runtime_digest transport_timing_basis remote_proof".split())
_CONTEXT_SOURCE_KEYS = frozenset("format context_alias parent_context_alias role phase attempt source_sequence kind source_status dispatch_status capture_status reason_code clock".split())


def _context_metrics():
    return dict(input_tokens=None, output_tokens=None, total_tokens=None, elapsed_seconds=None,
        peak_rss_mb=None, usage_basis="unavailable",
        provider_cost=dict(value=None, currency=None, basis="unavailable", source_alias=None))


def _validate_context_receipt(value):
    from devbench.analysis.rna_trace_contract import validate_trace_event
    if (not isinstance(value, dict) or set(value) != _CONTEXT_SOURCE_KEYS
            or value.get("format") != CONTEXT_SOURCE_FORMAT):
        raise ValueError("Closed aliased context receipt required")
    alias = _uuid(value["context_alias"])
    parent = value["parent_context_alias"]
    if parent is not None:
        _uuid(parent)
    if parent == alias or type(value["source_sequence"]) is not int or not 0 <= value["source_sequence"] <= 2**31-1:
        raise ValueError("Invalid context source identity/sequence")
    if type(value["attempt"]) is not int or not 1 <= value["attempt"] <= 2**31-1:
        raise ValueError("Invalid context source attempt")
    clock = value["clock"]
    if (not isinstance(clock, dict) or set(clock) != {"utc", "timing_basis"}
            or clock["timing_basis"] != "observed" or clock["utc"] is None):
        raise ValueError("Observed context clock required")
    validate_trace_event(dict(format="devbench.rna-trace-event/1", event_id=alias,
        trace_id=alias, span_id=alias, parent_span_id=parent, sequence=value["source_sequence"],
        **{key:value[key] for key in ("role","phase","attempt","kind","source_status","dispatch_status","capture_status","reason_code")},
        origin="optional_observer_receipt", clock=dict(utc=clock["utc"], monotonic_ns=None,
            process_alias=alias, timing_basis="observed"), bindings=dict(workflow="0"*64), metrics=_context_metrics()))
    return json.loads(canonical_json_bytes(value))


def project_context_receipt(event, *, context_alias, parent_context_alias):
    """Project only closed observations; raw identities, bindings and usage stay local."""
    from devbench.analysis.rna_trace_contract import validate_trace_event
    event = validate_trace_event(event)
    raw_ids = {event[key] for key in ("event_id","span_id","trace_id","parent_span_id")}
    raw_ids.add(event["clock"]["process_alias"])
    if context_alias in raw_ids or parent_context_alias is not None and parent_context_alias in raw_ids:
        raise ValueError("Fresh context aliases required")
    value = dict(format=CONTEXT_SOURCE_FORMAT, context_alias=context_alias,
        parent_context_alias=parent_context_alias, source_sequence=event["sequence"],
        **{key:event[key] for key in ("role","phase","attempt","kind","source_status","dispatch_status","capture_status","reason_code")},
        clock={key:event["clock"][key] for key in ("utc","timing_basis")})
    return _validate_context_receipt(value)


def context_event_terminal_status(event):
    value = _validate_context_receipt(event)
    return value["source_status"] if value["kind"] in {"completion","failure","cancel"} and value["source_status"] in _CONTEXT_TERMINAL else None


def _validate_context_dotted_order(run):
    order = run.get("dotted_order")
    if not isinstance(order, str) or not 1 <= len(order.split(".")) <= 16:
        raise ValueError("Bounded context dotted order required")
    parts = order.split("."); identities = []
    for part in parts:
        if re.fullmatch(r"\d{8}T\d{12}Z[0-9a-f-]{36}", part) is None:
            raise ValueError("Canonical context order required")
        stamp = datetime.strptime(part[:22], "%Y%m%dT%H%M%S%fZ").replace(tzinfo=timezone.utc)
        identity = _uuid(part[22:]); identities.append(identity)
        if part != _root_dotted_order(stamp.isoformat(), identity):
            raise ValueError("Canonical context order required")
    if (len(set(identities)) != len(identities) or identities[0] != run.get("trace_id")
            or parts[-1] != _root_dotted_order(run.get("start_time"), run.get("id"))
            or (len(parts)==1 and (run.get("parent_run_id") is not None or run.get("id")!=run.get("trace_id")))
            or (len(parts)>1 and run.get("parent_run_id")!=identities[-2])):
        raise ValueError("Exact context ancestry order required")
    return run


def _context_run_receipt(run, operation):
    row = run["extra"]["metadata"]
    return _validate_context_receipt(dict(format=CONTEXT_SOURCE_FORMAT,
        context_alias=row["context_alias"], parent_context_alias=row["parent_context_alias"],
        source_sequence=row["source_sequence"],
        **{key:row[key] for key in ("role","phase","attempt","kind","source_status","dispatch_status","capture_status","reason_code")},
        clock=dict(utc=run["start_time"] if operation=="start" else run["end_time"], timing_basis="observed")))


def _validate_context_wire_run(run, operation, *, generation=False):
    from devbench.analysis.rna_trace_projection import validate_projected_span
    from .rna_langsmith_verify import validate_remote_proof
    if operation not in {"start","finish"} or not isinstance(run, dict):
        raise ValueError("Explicit context lifecycle required")
    required = {"id","trace_id","name","run_type","inputs","outputs","extra","session_name","session_id","tags","start_time","dotted_order"}
    allowed = required | {"parent_run_id","end_time","error"}
    if not required <= set(run) or not set(run) <= allowed or len(canonical_json_bytes(run)) > 8192:
        raise ValueError("Closed bounded context run required")
    _uuid(run["session_id"])
    extra = run["extra"]
    if not isinstance(extra, dict) or set(extra)!={"metadata"} or not isinstance(extra["metadata"],dict):
        raise ValueError("Closed context metadata required")
    row = extra["metadata"]
    context_extra = _CONTEXT_EXTRA | ({"dispatch_event_count"} if generation else set())
    if set(row) != _CONTEXT_BASE_METADATA | context_extra:
        raise ValueError("Closed context metadata required")
    _uuid(row["context_alias"])
    if row["parent_context_alias"] is not None: _uuid(row["parent_context_alias"])
    for key in ("source_digest","runtime_digest"): _digest(row[key])
    if (type(row["start_observed"]) is not bool or row["provenance"]!="synthetic_engineering"
            or row["evidence_alias"]!=row["context_alias"] or row["metrics"]!=_context_metrics()
            or row["capability"]!={"backend_kind":"unknown","qualification_status":"unknown","admitted":False}
            or any(type(row[key]) is not int or row[key]!=0 for key in ("allocation","actual_children"))
            or row["complete"] is not False or row["origin"]!="optional_observer_receipt"
            or row["investigation_stage"]!="unspecified" or row["timing_basis"]!="observed"
            or row["sequence"]!=row["source_sequence"]
            or row["transport_timing_basis"]!=("local_receipt_clock" if row["start_observed"] else "first_observed_receipt")):
        raise ValueError("Synthetic context observation metadata required")
    if (type(row["source_event_count"]) is not int or not 1<=row["source_event_count"]<=64
            or type(row["capture_event_count"]) is not int or not 0<=row["capture_event_count"]<=row["source_event_count"]
            or not isinstance(row["source_event_digests"],list) or len(row["source_event_digests"])!=row["source_event_count"]):
        raise ValueError("Bounded context source accounting required")
    if generation and (type(row["dispatch_event_count"]) is not int or not 0 <= row["dispatch_event_count"] <= row["source_event_count"]):
        raise ValueError("Bounded actual dispatch receipt count required")
    for value in row["source_event_digests"]: _digest(value)
    if len(set(row["source_event_digests"]))!=len(row["source_event_digests"]):
        raise ValueError("Unique context source observations required")
    start = _time(run["start_time"])
    if start is None or start.timestamp()<0 or run["tags"]!=["devbench","rna","synthetic"] or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,99}",run["session_name"]) is None:
        raise ValueError("Bounded context project and clock required")
    if operation=="start":
        if "end_time" in run or "error" in run or row["start_observed"]!=(row["kind"] in ({"reservation", "dispatch"} if generation else {"reservation"})):
            raise ValueError("Context start is only its retained first observation")
    elif (_time(run.get("end_time")) is None or _time(run["end_time"])<start
            or context_event_terminal_status(_context_run_receipt(run,operation)) is None):
        raise ValueError("Explicit retained context terminal required")
    error = workflow_error_code(row["source_status"]) if operation=="finish" else None
    if (error is None and "error" in run) or (error is not None and run.get("error")!=error):
        raise ValueError("Exact context error code required")
    if row["kind"] in ({"gap"} if generation else {"dispatch", "gap"}):
        raise ValueError("Context observations cannot claim dispatch or fill gaps")
    _validate_context_dotted_order(run)
    _context_run_receipt(run,operation)
    validate_remote_proof(run)
    base = {key:deepcopy(run[key]) for key in ("id","trace_id","name","run_type","inputs","outputs")}
    base["parent_run_id"] = run.get("parent_run_id")
    base["extra"] = {"metadata":{key:deepcopy(value) for key,value in row.items()
        if key not in context_extra | {"transport_timing_basis","remote_proof"}}}
    validate_projected_span(base)
    return run


def validate_context_run_bindings(payload, operation, session):
    return _validate_context_run_bindings(payload, operation, session)


def validate_historical_context_run_bindings(payload, operation, session):
    """Pure retained operation/proof checks; never a live session admission."""
    return _validate_context_run_bindings(payload, operation, session, historical=True)


def _validate_context_run_bindings(payload, operation, session, *, historical=False):
    from .rna_langsmith_verify import validate_remote_proof
    session = validate_historical_context_session(session) if historical else validate_live_session(session)
    if session["format"] not in {SESSION_FORMAT_V4, SESSION_FORMAT_V5}:
        raise ValueError("Stored context session /4 required")
    keys = {"operation_id","span_id","revision","operation","event_digest","redaction_policy_digest","source_binding_alias","payload_sha256"}
    if not isinstance(operation,dict) or set(operation)!=keys:
        raise ValueError("Closed context operation required")
    for key in ("operation_id","span_id","source_binding_alias"): _uuid(operation[key])
    for key in ("event_digest","redaction_policy_digest","payload_sha256"): _digest(operation[key])
    generation = session["format"] == SESSION_FORMAT_V5
    _validate_context_wire_run(payload,operation["operation"], generation=generation)
    profile = {row["span_id"]:row for row in session["context_profile"]["contexts"]}
    topology = {row["span_id"]:row for row in session["span_topology"]}
    slot, context = topology.get(payload["id"]), profile.get(payload["id"])
    if slot is None or context is None:
        raise ValueError("Unapproved context identity")
    row = payload["extra"]["metadata"]
    proof = validate_remote_proof(payload)
    revision = 0 if operation["operation"]=="start" else 1
    if (type(operation["revision"]) is not int or operation["revision"]!=revision
            or proof["revision"]!=revision or proof["operation_id"]!=operation["operation_id"]
            or operation["span_id"]!=payload["id"] or payload["trace_id"]!=session["span_topology"][0]["span_id"]
            or payload.get("parent_run_id")!=slot["parent_span_id"]
            or any(row[key]!=slot[key] for key in ("role","phase","attempt"))
            or any(row[key]!=context[key] for key in ("context_alias","parent_context_alias","start_observed","source_event_count","capture_event_count","source_event_digests"))
            or generation and row["dispatch_event_count"] != context["dispatch_event_count"]
            or operation["source_binding_alias"]!=session["workflow_alias"]
            or operation["redaction_policy_digest"]!=session["policy_digest"]
            or operation["payload_sha256"]!=digest(canonical_json_bytes(payload))
            or payload["session_id"]!=session["project_id"] or payload["session_name"]!=session["project"]
            or any(row[key]!=session[key] for key in ("source_digest","runtime_digest"))):
        raise ValueError("Exact frozen context operation binding required")
    source_digest = digest(canonical_json_bytes(_context_run_receipt(payload,operation["operation"])))
    expected_digest = context["source_event_digests"][0 if revision==0 else -1]
    if operation["event_digest"]!=source_digest or source_digest!=expected_digest:
        raise ValueError("Aliased source observation binding mismatch")
    if revision==1 and (context["terminal_status"] is None or row["source_status"]!=context["terminal_status"]):
        raise ValueError("Context terminal set differs from frozen profile")
    return payload


class ContextReceiptOperationBridge:
    """Stored /4 projection of all retained source observations, with no new execution."""
    def __init__(self, *, authorized_session_manifest, output_path):
        self.session = validate_live_session(authorized_session_manifest)
        if self.session["format"] not in {SESSION_FORMAT_V4, SESSION_FORMAT_V5}:
            raise ValueError("Context bridge requires /4")
        self.path = _absolute(output_path)
        self.start_payloads, self.items = {}, {}
        self._append_uncertain = False
        if not self.path.exists():
            fd=os.open(self.path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600);os.close(fd)
        else:
            size=self.path.stat().st_size
            if size>self.session["max_bytes"]:
                raise ValueError("Context operation capacity exceeded")
            if size:
                descriptor=os.open(self.path,os.O_RDONLY|os.O_NOFOLLOW)
                try:
                    os.lseek(descriptor,-1,os.SEEK_END)
                    if os.read(descriptor,1)!=b"\n":
                        raise ValueError("Incomplete context operation stream")
                finally:
                    os.close(descriptor)
        for item in SanitizedOperationFile(self.path):
            self._retain(item)

    def _retain(self,item, *, commit=True):
        if not isinstance(item,dict) or set(item)!={"operation","payload"}:
            raise ValueError("Closed context stream item required")
        op,run=item["operation"],item["payload"]
        validate_context_run_bindings(run,op,self.session)
        key=(run["id"],op["operation"])
        if key in self.items or len(self.items)>=self.session["context_profile"]["operation_count"]:
            raise ValueError("Duplicate or overlimit context operation")
        parent=run.get("parent_run_id")
        if parent is not None and (parent not in self.start_payloads or run["dotted_order"]!=self.start_payloads[parent]["dotted_order"]+"."+_root_dotted_order(run["start_time"],run["id"])):
            raise ValueError("Retained context parent order required")
        if op["operation"]=="start":
            if commit:
                self.start_payloads[run["id"]]=deepcopy(run)
        else:
            start=self.start_payloads.get(run["id"])
            if start is None or any(run.get(k)!=start.get(k) for k in ("start_time","dotted_order","parent_run_id","trace_id")):
                raise ValueError("Immutable retained context start required")
        if commit:
            self.items[key]=deepcopy(item)

    def emit_new(self, *, observer, workflow_id):
        from .rna_langsmith_verify import bind_remote_proof
        if self._append_uncertain:
            raise ValueError("Uncertain context append requires independent recovery")
        validate_live_session(self.session)
        supplied=observer.read_events(workflow_id)
        if not isinstance(supplied,list) or len(supplied)!=self.session["context_profile"]["source_event_count"]:
            raise ValueError("Exact retained context receipt set required")
        events=[_validate_context_receipt(item) for item in supplied]
        by_alias={row["context_alias"]:[] for row in self.session["context_profile"]["contexts"]}
        for item in events:
            if item["context_alias"] not in by_alias: raise ValueError("Unapproved source context alias")
            by_alias[item["context_alias"]].append(item)
        generation = self.session["format"] == SESSION_FORMAT_V5
        candidates=[];starts={};terminal_sources=[]
        for slot,context in zip(self.session["span_topology"],self.session["context_profile"]["contexts"]):
            retained=by_alias[context["context_alias"]]
            if (not retained or any(item["kind"] in ({"gap"} if generation else {"dispatch", "gap"}) for item in retained)
                    or [digest(canonical_json_bytes(item)) for item in retained]!=context["source_event_digests"]
                    or len(retained)!=context["source_event_count"]
                    or sum(item["kind"]=="capture" for item in retained)!=context["capture_event_count"]
                    or generation and sum(item["kind"]=="dispatch" for item in retained)!=context["dispatch_event_count"]
                    or context["start_observed"]!=(retained[0]["kind"] in ({"reservation", "dispatch"} if generation else {"reservation"}))
                    or any(any(item[k]!=slot[k] for k in ("role","phase","attempt")) or item["parent_context_alias"]!=context["parent_context_alias"] for item in retained)
                    or any(a["source_sequence"]>=b["source_sequence"] for a,b in zip(retained,retained[1:]))):
                raise ValueError("Retained source context differs from frozen profile")
            terminals=[item for item in retained if context_event_terminal_status(item) is not None]
            if (len(terminals)>1 or (terminals and terminals[0] is not retained[-1])
                    or (context_event_terminal_status(retained[-1]) if terminals else None)!=context["terminal_status"]):
                raise ValueError("Explicit context terminal evidence required")
            item=self._construct(slot,context,retained[0],"start",starts,None)
            starts[slot["span_id"]]=item["payload"];candidates.append(item)
            if terminals: terminal_sources.append((slot,context,terminals[0]))
        for slot,context,event in reversed(terminal_sources):
            candidates.append(self._construct(slot,context,event,"finish",starts,starts[slot["span_id"]]["start_time"]))
        emitted=[]
        for item in candidates:
            key=(item["payload"]["id"],item["operation"]["operation"])
            if key in self.items:
                if canonical_json_bytes(item)!=canonical_json_bytes(self.items[key]): raise ValueError("Changed immutable context projection")
                continue
            raw=canonical_json_bytes(item)+b"\n"
            if len(raw)>8192 or self.path.stat().st_size+len(raw)>self.session["max_bytes"]:
                raise ValueError("Context operation capacity exceeded")
            self._retain(item, commit=False)
            descriptor=os.open(self.path,os.O_WRONLY|os.O_APPEND|os.O_NOFOLLOW)
            stream=None
            try:
                stream=os.fdopen(descriptor,"wb")
                with stream:
                    if stream.write(raw)!=len(raw):
                        raise OSError("Incomplete context operation append")
                    stream.flush();os.fsync(stream.fileno())
            except BaseException:
                # Bytes may already exist. Preserve them and forbid blind retry.
                self._append_uncertain=True
                if stream is None:
                    os.close(descriptor)
                raise
            self._retain(item)
            emitted.append(deepcopy(item))
        return emitted

    def _construct(self,slot,context,event,operation,starts,start_time):
        from .rna_langsmith_verify import bind_remote_proof
        key=(slot["span_id"],operation);revision=0 if operation=="start" else 1
        existing=self.items.get(key)
        opid=existing["operation"]["operation_id"] if existing else str(uuid4())
        row={k:event[k] for k in ("role","phase","attempt","kind","source_status","dispatch_status","capture_status","reason_code")}
        row.update(origin="optional_observer_receipt",sequence=event["source_sequence"],source_sequence=event["source_sequence"],
            timing_basis="observed",evidence_alias=context["context_alias"],metrics=_context_metrics(),
            capability=dict(backend_kind="unknown",qualification_status="unknown",admitted=False),allocation=0,actual_children=0,
            complete=False,investigation_stage="unspecified",source_digest=self.session["source_digest"],runtime_digest=self.session["runtime_digest"],
            transport_timing_basis="local_receipt_clock" if context["start_observed"] else "first_observed_receipt",
            provenance="synthetic_engineering",**{k:deepcopy(context[k]) for k in ("context_alias","parent_context_alias","start_observed","source_event_count","capture_event_count","source_event_digests")})
        if self.session["format"] == SESSION_FORMAT_V5:
            row["dispatch_event_count"] = context["dispatch_event_count"]
        run=dict(id=slot["span_id"],trace_id=self.session["span_topology"][0]["span_id"],
            name="rna."+slot["role"]+"."+slot["phase"],run_type="tool" if slot["role"]=="numerical" else "chain",
            inputs={},outputs={},extra=dict(metadata=row),session_name=self.session["project"],session_id=self.session["project_id"],
            tags=["devbench","rna","synthetic"],start_time=start_time or event["clock"]["utc"])
        parent=slot["parent_span_id"]
        own=_root_dotted_order(run["start_time"],run["id"])
        if parent is None: run["dotted_order"]=own
        else: run.update(parent_run_id=parent,dotted_order=starts[parent]["dotted_order"]+"."+own)
        if operation=="finish":
            run["end_time"]=event["clock"]["utc"]
            error=workflow_error_code(event["source_status"])
            if error is not None: run["error"]=error
        envelope=dict(operation_id=opid,span_id=slot["span_id"],revision=revision,operation=operation,
            event_digest=digest(canonical_json_bytes(event)),redaction_policy_digest=self.session["policy_digest"],
            source_binding_alias=self.session["workflow_alias"],payload_sha256="0"*64)
        run=bind_remote_proof(run,envelope);envelope["payload_sha256"]=digest(canonical_json_bytes(run))
        validate_context_run_bindings(run,envelope,self.session)
        return dict(operation=envelope,payload=run)


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
        if self.session["format"] in {SESSION_FORMAT_V4, SESSION_FORMAT_V5}:
            raise ValueError("Use the explicit context observation bridge")
        self.path = _absolute(output_path)
        self.capability, self.allocation, self.actual_children = capability, allocation, actual_children
        self.events, self.starts, self.last_sequences = {}, {}, {}
        self.templates, self.revisions = {}, {}
        if self.session["format"] == SESSION_FORMAT_V3 and (type(allocation) is not int
                or allocation != 0 or type(actual_children) is not int or actual_children != 0):
            raise ValueError("Workflow qualification has no agent children")
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
            validate_live_run(run,envelope["operation"], session_format=self.session["format"])
            if envelope["source_binding_alias"] != self.session["workflow_alias"] or envelope["redaction_policy_digest"] != self.session["policy_digest"] or digest(canonical_json_bytes(run)) != envelope["payload_sha256"]:
                raise ValueError("Bridge stream session/content mismatch")
            if self.session["format"] == SESSION_FORMAT_V3:
                self._validate_workflow_item(item)
            self.events[envelope["operation_id"]] = envelope["event_digest"]
            self.last_sequences[run["id"]] = run["extra"]["metadata"]["sequence"]
            self.revisions[run["id"]] = envelope["revision"]
            self.templates[run["id"]] = run
            if envelope["operation"] == "start":
                self.starts[run["id"]] = run["start_time"]

    def _validate_workflow_item(self, item):
        envelope, run = item["operation"], item["payload"]
        if not isinstance(envelope, dict) or set(envelope) != {"operation_id", "span_id",
                "revision", "operation", "event_digest", "redaction_policy_digest",
                "source_binding_alias", "payload_sha256"}:
            raise ValueError("Closed workflow operation required")
        from .rna_langsmith_verify import validate_remote_proof
        proof = validate_remote_proof(run)
        row = run["extra"]["metadata"]
        _validate_workflow_event(dict(span_id=run["id"], trace_id=run["trace_id"],
            parent_span_id=run.get("parent_run_id"),
            **{key: row[key] for key in ("role", "phase", "attempt")}), self.session)
        if (len(self.events) >= 16 or type(envelope["revision"]) is not int
                or envelope["span_id"] != run["id"] or proof["operation_id"] != envelope["operation_id"]
                or proof["revision"] != envelope["revision"]
                or envelope["revision"] != self.revisions.get(run["id"], -1) + 1
                or envelope["revision"] not in {0, 1}
                or run.get("session_id") != self.session["project_id"]
                or any(row.get(key) != self.session[key] for key in ("source_digest", "runtime_digest"))):
            raise ValueError("Workflow operation bounds or binding mismatch")
        parent = run.get("parent_run_id")
        if parent is not None:
            if parent not in self.starts:
                raise ValueError("Workflow parent start evidence missing")
            expected_order = self.templates[parent]["dotted_order"] + "." + _root_dotted_order(run["start_time"], run["id"])
            if run["dotted_order"] != expected_order:
                raise ValueError("Retained workflow parent order mismatch")
        if envelope["operation"] == "start":
            if run["id"] in self.starts:
                raise ValueError("Duplicate workflow start")
        elif (envelope["operation"] != "finish" or run["id"] not in self.starts
                or run["start_time"] != self.starts[run["id"]]
                or run["dotted_order"] != self.templates[run["id"]]["dotted_order"]):
            raise ValueError("Immutable workflow start/order required")

    def emit_new(self, *, observer, workflow_id):
        validate_live_session(self.session)
        emitted = []
        events = observer.read_events(workflow_id)
        if not isinstance(events,list) or len(events)>(16 if self.session["format"] == SESSION_FORMAT_V3 else 2000):
            raise ValueError("Bounded observer receipt list required")
        for source_event in events:
            event=deepcopy(source_event)
            if self.session["format"] == SESSION_FORMAT_V3:
                _validate_workflow_event(event, self.session)
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
                    if self.session["format"] == SESSION_FORMAT_V3:
                        raise ValueError("Workflow qualification cannot fill a receipt gap")
                    parent = event["parent_span_id"]
                    if parent not in self.templates:
                        raise ValueError("Missing retained parent for structural gap")
                    # A point record lacks live start evidence. Annotate its
                    # retained parent as a gap; never invent a child dispatch
                    # or measured duration to repair an optional observer.
                    template = self.templates[parent]
                    row = template["extra"]["metadata"]
                    event.update(span_id=parent,parent_span_id=template.get("parent_run_id"),
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
                                       start_time=self.starts.get(identity),
                                       parent_dotted_order=self.templates.get(event["parent_span_id"], {}).get("dotted_order"))
            if gap:
                # The parent template is already remote-sanitized. Retain its
                # existing cost receipt alias instead of UUID5-aliasing again.
                item["payload"]["extra"]["metadata"]["metrics"]["provider_cost"]["source_alias"] = row["metrics"]["provider_cost"]["source_alias"]
                item["operation"]["payload_sha256"] = digest(canonical_json_bytes(item["payload"]))
            item["operation"]["event_digest"] = event_digest
            item["operation"]["operation_id"] = event_key
            item["operation"]["revision"] = self.revisions.get(identity,-1)+1
            if self.session["format"] in {SESSION_FORMAT_V2, SESSION_FORMAT_V3}:
                from .rna_langsmith_verify import bind_remote_proof
                item["payload"] = bind_remote_proof(item["payload"], item["operation"])
                item["operation"]["payload_sha256"] = digest(canonical_json_bytes(item["payload"]))
            if self.session["format"] == SESSION_FORMAT_V3:
                self._validate_workflow_item(item)
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
        if self.session["format"] in {SESSION_FORMAT_V2, SESSION_FORMAT_V3, SESSION_FORMAT_V4, SESSION_FORMAT_V5}:
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


class _AdmittedRESTLiveTransport:
    """Private /2 path: authority is a live original-child connection instance.

    The legacy constructor remains closed for /2. No caller-supplied readiness
    boolean, target, API key, opener, or payload allowlist admits this path.
    """
    def __init__(self, connection):
        from .rna_hosted_connection import HostedConnection
        if not isinstance(connection,HostedConnection):
            raise PermissionError('Admitted child connection required')
        self._connection=connection
        self.session=connection._authorize_transport()
        if self.session['format'] != SESSION_FORMAT_V2:
            raise PermissionError('Admitted UUID-bound connection required')

    def __call__(self, *, method,path,payload,timeout):
        if self._connection._authorize_transport() != self.session:
            raise PermissionError('Immutable admitted session required')
        operation='start' if method=='POST' else 'finish'
        if method not in {'POST','PATCH'} or path != ('/runs' if method=='POST' else '/runs/'+_uuid(payload.get('id'))):
            raise ValueError('Exact admitted mutation route required')
        validate_live_run(payload,operation)
        response=self._connection._request(method=method,path='/api/v1'+path,payload=payload,timeout=timeout)
        # The reviewed public interface documents exactly 202 for both writes.
        # Unexpected success shapes/codes retain uncertainty after a sent request.
        if response['status_code'] != 202:
            if 400 <= response['status_code'] < 500:
                return {'status_code':response['status_code']}
            raise OSError('Connection mutation unacknowledged')
        return {'status_code':202}


def _admitted_rest_transport(connection):
    return _AdmittedRESTLiveTransport(connection)


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
            validate_live_run(payload, operation.get("operation"), session_format=self.session["format"])
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
