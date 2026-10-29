"""Bounded read-only RNA evidence projection, with newly constructed remote fields."""
from copy import deepcopy
import hashlib
from datetime import datetime
from pathlib import Path
import uuid

from .rna_trace_contract import (canonical_bytes, trace_digest, validate_trace_event,
    validate_cost_record, _closed, _enum, _uuid, _digest, _integer, ROLES, PHASES,
    KINDS, SOURCE_STATUSES, DISPATCH_STATUSES, CAPTURE_STATUSES, REASONS)
from .workflow_state import SERIAL_ROUTE

SNAPSHOT_FORMAT = "devbench.rna-trace-snapshot/1"
PROJECTION_FORMAT = "devbench.rna-trace-projection/1"
JOB_SNAPSHOT_FORMAT = "devbench.rna-trace-snapshot/2"
JOB_PROJECTION_FORMAT = "devbench.rna-trace-projection/2"
JOB_ACCOUNTING_FORMAT = "devbench.codex-job-trace-accounting/1"
CAPABILITY_KINDS = {"codex-cli", "offline_recorded", "offline_replay", "offline-fixture", "enforced-offline-fixture", "recorded", "unknown", "openai-responses-mediated"}
QUALIFICATIONS = {"source_inspected_not_multiagent_qualified", "offline_qualified", "offline_recorded_qualified", "offline_replay_qualified", "unknown", "mediated_qualified"}


def _capability(value, *, generation_jobs=False):
    _closed(value, {"backend_kind", "qualification_status", "admitted"}, "capability summary")
    _enum(value["backend_kind"], {"codex-chatgpt-cli", "enforced-offline-job-fixture"} if generation_jobs else CAPABILITY_KINDS, "backend kind")
    _enum(value["qualification_status"], {"unqualified", "offline_qualified"} if generation_jobs else QUALIFICATIONS, "qualification status")
    if type(value["admitted"]) is not bool:
        raise ValueError("Invalid admission summary")
    if value["backend_kind"] == "codex-cli" and value["admitted"] and value["qualification_status"] == "source_inspected_not_multiagent_qualified":
        raise ValueError("Tracing cannot admit an unqualified native backend")


def _counts(value):
    for key in ("allocation", "actual_children"):
        _integer(value[key], key)
        if value[key] > 2:
            raise ValueError("Tracing cannot expand child allocation")
    if value["actual_children"] > value["allocation"]:
        raise ValueError("Actual children exceed allocation")
    if type(value["complete"]) is not bool:
        raise ValueError("Invalid completeness")


def validate_projected_span(value):
    """Validate the entire server-visible projection; arbitrary metadata fails closed."""
    _closed(value, {"id", "trace_id", "parent_run_id", "name", "run_type", "inputs", "outputs", "extra"}, "remote span")
    for key in ("id", "trace_id"):
        _uuid(value[key], key)
    _uuid(value["parent_run_id"], "parent run", True)
    if value["parent_run_id"] == value["id"]:
        raise ValueError("Self-parented remote span")
    _enum(value["run_type"], {"chain", "tool"}, "run type")
    if value["inputs"] != {} or value["outputs"] != {}:
        raise ValueError("RNA trace excludes model/scientific inputs and outputs")
    _closed(value["extra"], {"metadata"}, "remote extra")
    row = value["extra"]["metadata"]
    _closed(row, {"role", "phase", "kind", "source_status", "dispatch_status", "capture_status", "reason_code", "timing_basis", "origin", "evidence_alias", "attempt", "sequence", "metrics", "capability", "allocation", "actual_children", "complete", "source_digest", "runtime_digest", "investigation_stage"}, "remote metadata")
    for key, allowed in (("role", ROLES), ("phase", PHASES), ("kind", KINDS), ("source_status", SOURCE_STATUSES), ("dispatch_status", DISPATCH_STATUSES), ("capture_status", CAPTURE_STATUSES), ("reason_code", REASONS), ("timing_basis", {"observed", "reconstructed", "display_anchor", "unavailable"}), ("origin", {"retained_authoritative_evidence", "optional_observer_receipt"})):
        _enum(row[key], allowed, key)
    if value["name"] != "rna." + row["role"] + "." + row["phase"]:
        raise ValueError("Run names must be fixed role/phase enums")
    _uuid(row["evidence_alias"], "evidence alias")
    _enum(row["investigation_stage"], {"pre_fit_methods", "post_fit_explanation", "unspecified"}, "investigation stage")
    _digest(row["source_digest"], "exported source", nullable=True)
    _digest(row["runtime_digest"], "exported runtime", nullable=True)
    _capability(row["capability"], generation_jobs=type(row["capability"]) is dict and row["capability"].get("backend_kind") in {"codex-chatgpt-cli", "enforced-offline-job-fixture"})
    _counts(row)
    # Reuse the finite, closed metrics validator without importing scientific execution.
    validate_trace_event({"format": "devbench.rna-trace-event/1", "event_id": value["id"], "trace_id": value["trace_id"], "span_id": value["id"], "parent_span_id": value["parent_run_id"], **{key: row[key] for key in ("sequence", "attempt", "role", "phase", "kind", "source_status", "dispatch_status", "capture_status", "metrics", "reason_code", "origin")}, "clock": {"utc": "2000-01-01T00:00:00Z" if row["timing_basis"] == "observed" else None, "monotonic_ns": None, "process_alias": value["id"], "timing_basis": row["timing_basis"]}, "bindings": {"workflow": "0" * 64}})
    return deepcopy(value)


def _validate_snapshot(snapshot):
    base = {"format", "workflow_binding", "events", "capability", "allocation", "actual_children", "complete", "gaps"}
    generation_jobs = type(snapshot) is dict and snapshot.get("format") == JOB_SNAPSHOT_FORMAT
    if generation_jobs:
        base.add("accounting_kind")
    optional = {"investigation_stage", "observer_events", "ledger_accounting"}
    if type(snapshot) is not dict or not base <= set(snapshot) or not set(snapshot) <= base | optional:
        raise ValueError("Invalid closed snapshot fields")
    _enum(snapshot.get("investigation_stage", "unspecified"), {"pre_fit_methods", "post_fit_explanation", "unspecified"}, "investigation stage")
    if generation_jobs:
        if snapshot["accounting_kind"] != "generation_jobs" or "ledger_accounting" not in snapshot:
            raise ValueError("Generation job snapshot requires its exact accounting discriminator")
        _validate_job_accounting(snapshot["ledger_accounting"])
        if snapshot["complete"] and (snapshot["ledger_accounting"]["qualification"] != "complete"
                or snapshot["ledger_accounting"]["unresolved_count"] or snapshot["ledger_accounting"]["outstanding_reserved_jobs"]):
            raise ValueError("Unresolved job lifecycle cannot claim complete capture")
    elif "ledger_accounting" in snapshot:
        ledger = snapshot["ledger_accounting"]
        _closed(ledger, {"verified_spent_tokens", "verified_calls", "unresolved_count", "unknown_usage_count", "qualification", "policy_kind"}, "retained ledger accounting")
        for key in ("verified_spent_tokens", "verified_calls", "unresolved_count", "unknown_usage_count"):
            _integer(ledger[key], key)
        _enum(ledger["qualification"], {"complete", "pending", "incomplete", "violated"}, "ledger accounting qualification")
        _enum(ledger["policy_kind"], {"hard_total_tokens", "bounded_observed_tokens"}, "ledger policy kind")
    if "observer_events" in snapshot:
        if type(snapshot["observer_events"]) is not list or not 1 <= len(snapshot["observer_events"]) <= 2000:
            raise ValueError("Invalid retained observer event count")
        for event in snapshot["observer_events"]:
            validate_trace_event(event)
    if snapshot["format"] not in {SNAPSHOT_FORMAT, JOB_SNAPSHOT_FORMAT}:
        raise ValueError("Unsupported RNA snapshot format")
    _digest(snapshot["workflow_binding"], "workflow binding")
    _capability(snapshot["capability"], generation_jobs=generation_jobs)
    _counts(snapshot)
    if generation_jobs and (snapshot["allocation"] or snapshot["actual_children"]):
        raise ValueError("Generation job traces are parent-only")
    if type(snapshot["events"]) is not list or len(snapshot["events"]) > 10000:
        raise ValueError("Invalid snapshot event count")
    if type(snapshot["gaps"]) is not list or len(snapshot["gaps"]) > 32:
        raise ValueError("Invalid snapshot gaps")
    for reason in snapshot["gaps"]:
        _enum(reason, REASONS - {"none"}, "gap reason")
    if snapshot["gaps"] and snapshot["complete"]:
        raise ValueError("A gapped snapshot cannot claim complete capture")
    if len(canonical_bytes(snapshot)) > 8 * 1024 * 1024:
        raise ValueError("Snapshot exceeds byte cap")


def _validate_job_accounting(value):
    _closed(value, {"format", "policy_kind", "verified_jobs", "outstanding_reserved_jobs",
        "unresolved_count", "qualification", "reported_token_subtotal", "reported_usage_jobs",
        "unknown_usage_jobs", "verified_seconds"}, "generation job accounting")
    if value["format"] != JOB_ACCOUNTING_FORMAT or value["policy_kind"] != "generation_jobs":
        raise ValueError("Unsupported generation job accounting discriminator")
    for key in ("verified_jobs", "outstanding_reserved_jobs", "unresolved_count", "reported_usage_jobs", "unknown_usage_jobs"):
        _integer(value[key], key)
    _integer(value["reported_token_subtotal"], "reported token subtotal", nullable=True)
    from .rna_trace_contract import _number
    _number(value["verified_seconds"], "verified job elapsed seconds")
    _enum(value["qualification"], {"complete", "pending", "incomplete", "violated"}, "job lifecycle qualification")
    if ((value["reported_usage_jobs"] == 0) != (value["reported_token_subtotal"] is None)
            or value["reported_usage_jobs"] > value["verified_jobs"]
            or value["reported_usage_jobs"] + value["unknown_usage_jobs"] != value["verified_jobs"] + value["outstanding_reserved_jobs"]):
        raise ValueError("Generation job usage summary is inconsistent")


def _job_accounting(ledger):
    _validate_job_accounting(ledger)
    result = dict(policy_kind="generation_jobs", ledger_qualification=ledger["qualification"],
        job_accounting_basis="preventively_reserved", verified_jobs=ledger["verified_jobs"],
        outstanding_reserved_jobs=ledger["outstanding_reserved_jobs"], unresolved_jobs=ledger["unresolved_count"],
        job_lifecycle_complete=ledger["qualification"] == "complete" and not ledger["unresolved_count"] and not ledger["outstanding_reserved_jobs"],
        internal_model_requests=None, internal_model_requests_basis="unknown", verified_calls=None,
        total_tokens=None, observed_token_subtotal=ledger["reported_token_subtotal"],
        usage_basis="reported_optional" if ledger["reported_usage_jobs"] else "unavailable",
        reported_usage_jobs=ledger["reported_usage_jobs"], unknown_usage_jobs=ledger["unknown_usage_jobs"],
        preventive_token_enforcement=False, credit_usage=None, credit_usage_basis="unknown",
        provider_cost_total=None, provider_cost_subtotals=[], provider_cost_basis="unknown",
        duration_seconds=ledger["verified_seconds"], duration_basis="verified_local_job_lifecycle")
    canonical_bytes(result)
    return result


def _accounting(events, complete_capture, ledger=None):
    # Only represented call attempts contribute; structural/container nodes never duplicate usage.
    rows = [row for row in events if row["phase"] in {"selection", "permission_preflight"} and row["dispatch_status"] == "attempted"]
    unknown = sum(row["metrics"]["usage_basis"] != "verified_complete" for row in rows)
    reservations = [row for row in events if row["kind"] == "reservation"]
    unresolved = sum(row["source_status"] in {"active", "uncertain"} for row in reservations)
    unknown += unresolved
    complete = bool(rows) and not unknown and complete_capture

    cost_groups = {}
    unresolved_cost = 0
    for row in rows:
        cost = row["metrics"]["provider_cost"]
        if cost["basis"] == "unavailable":
            unresolved_cost += 1
            continue
        key = (cost["currency"], cost["basis"])
        cost_groups[key] = cost_groups.get(key, 0) + cost["value"]
        if cost["basis"].startswith("observed_partial"):
            unresolved_cost += 1
    if ledger is not None:
        unresolved_cost += max(0, ledger["verified_calls"] - len(rows))
    costs = [{"currency": currency, "basis": basis, "value": amount} for (currency, basis), amount in sorted(cost_groups.items())]
    cost_scope_complete = not unresolved and (ledger is None or ledger["qualification"] == "complete" and not ledger["unresolved_count"])
    total_cost = costs[0] if rows and len(costs) == 1 and not unresolved_cost and cost_scope_complete else None
    ledger_complete = ledger is not None and ledger["qualification"] == "complete" and not ledger["unresolved_count"] and not ledger["unknown_usage_count"]
    accounting = {"total_tokens": ledger["verified_spent_tokens"] if ledger_complete else None,
                  "observed_token_subtotal": ledger["verified_spent_tokens"] if ledger is not None else None,
                  "usage_basis": "verified_complete" if ledger_complete else "retained_verified_subtotal" if ledger is not None else "unavailable",
                  "unresolved_usage_calls": ledger["unknown_usage_count"] if ledger is not None else unknown,
                  "verified_calls": ledger["verified_calls"] if ledger is not None else None,
                  "policy_kind": ledger["policy_kind"] if ledger is not None else None,
                  "ledger_qualification": ledger["qualification"] if ledger is not None else "unavailable",
                  "provider_cost_total": total_cost, "provider_cost_subtotals": costs,
                  "unresolved_cost_calls": unresolved_cost, "duration_seconds": None, "duration_basis": "unavailable"}
    canonical_bytes(accounting)  # Reject arithmetic overflow rather than exporting Infinity.
    return accounting


def project_rna_trace(snapshot: dict, *, export_namespace: str, limits: dict) -> dict:
    _uuid(export_namespace, "export namespace")
    _validate_snapshot(snapshot)
    if type(limits) is not dict or not set(limits) <= {"max_spans", "max_bytes", "max_events"}:
        raise ValueError("Invalid projection limits")
    caps = {"max_spans": 2000, "max_bytes": 8 * 1024 * 1024, "max_events": 2000, **limits}
    for key, maximum in (("max_spans", 2000), ("max_events", 2000), ("max_bytes", 8 * 1024 * 1024)):
        _integer(caps[key], key, 1)
        if caps[key] > maximum:
            raise ValueError("Projection limit exceeds approved ceiling")
    namespace = uuid.UUID(export_namespace)
    live_identity = bool(snapshot.get("observer_events"))
    def alias(kind, local):
        if live_identity and kind in {"span", "trace"}:
            return local
        return str(uuid.uuid5(namespace, kind + ":" + local))
    unique, by_span = {}, {}
    for value in snapshot["events"]:
        event = validate_trace_event(value)
        if snapshot["format"] == JOB_SNAPSHOT_FORMAT and (event["metrics"]["usage_basis"] == "verified_complete"
                or event["role"] in {"investigator", "numerical"}
                or event["phase"] in {"permission_preflight", "selection"}):
            raise ValueError("Generation job traces cannot claim complete provider usage or internal call phases")
        if event["bindings"]["workflow"] != snapshot["workflow_binding"]:
            raise ValueError("Event belongs to a different verified workflow")
        old = unique.get(event["event_id"])
        if old is not None:
            if old != event:
                raise ValueError("Conflicting duplicate event")
            continue
        unique[event["event_id"]] = event
        history = by_span.setdefault(event["span_id"], [])
        if history:
            previous = history[-1]
            if event["sequence"] <= previous["sequence"]:
                raise ValueError("Nonmonotonic span event sequence")
            for key in ("trace_id", "parent_span_id", "role", "phase", "attempt", "bindings"):
                if event[key] != previous[key]:
                    raise ValueError("Span identity or immutable binding changed")
        history.append(event)
    if not by_span:
        raise ValueError("Snapshot has no evidence root")
    roots = [identity for identity, history in by_span.items() if history[0]["parent_span_id"] is None]
    if len(roots) != 1:
        raise ValueError("RNA trace requires one root")
    root = roots[0]
    trace = by_span[root][0]["trace_id"]
    if live_identity and export_namespace != trace:
        raise ValueError("Observer reconstruction must retain its live namespace")
    pending = set(by_span)
    ordered = []
    while pending:
        ready = sorted(identity for identity in pending if by_span[identity][0]["parent_span_id"] is None or by_span[identity][0]["parent_span_id"] in ordered)
        if not ready:
            raise ValueError("RNA trace has an orphan or cycle")
        for identity in ready:
            if by_span[identity][0]["trace_id"] != trace:
                raise ValueError("RNA events belong to multiple traces")
            ordered.append(identity)
            pending.remove(identity)
    complete = snapshot["complete"] and not snapshot["gaps"]
    gaps = list(snapshot["gaps"])
    if len(unique) > caps["max_events"] or len(ordered) > caps["max_spans"]:
        gaps.append("event_cap")
        complete = False
    original_ids = {row["event_id"] for row in snapshot.get("observer_events", [])}
    measured_intervals = {}
    for identity in ordered:
        history = by_span[identity]
        actual = [row for row in history if row["clock"]["timing_basis"] == "observed"
                  and row["clock"]["monotonic_ns"] is not None
                  and (not original_ids or row["event_id"] in original_ids)]
        starts = [row for row in actual if row["kind"] in {"reservation", "dispatch"}]
        finishes = [row for row in actual if row["kind"] in {"completion", "failure", "cancel"}]
        if not starts or not finishes:
            continue
        first, last = starts[0], finishes[-1]
        if first["clock"]["process_alias"] != last["clock"]["process_alias"]:
            continue
        delta = last["clock"]["monotonic_ns"] - first["clock"]["monotonic_ns"]
        if delta < 0 or last["sequence"] <= first["sequence"]:
            gaps.append("invalid_clock")
            complete = False
            continue
        duration = delta / 1_000_000_000
        canonical_bytes(duration)
        measured_intervals[identity] = duration
        if first["clock"]["utc"] and last["clock"]["utc"]:
            wall = (datetime.fromisoformat(last["clock"]["utc"].replace("Z", "+00:00")) - datetime.fromisoformat(first["clock"]["utc"].replace("Z", "+00:00"))).total_seconds()
            # One-second engineering diagnostic tolerance; monotonic remains
            # the measurement and wall time never creates global ordering.
            if abs(wall - duration) > 1:
                gaps.append("clock_disagreement")
                complete = False
    retained = ordered[: min(caps["max_spans"], caps["max_events"])]
    spans, local = [], {}
    for identity in retained:
        event = by_span[identity][-1]
        evidence_alias = alias("evidence", identity)
        metrics = deepcopy(event["metrics"])
        if identity in measured_intervals:
            metrics["elapsed_seconds"] = measured_intervals[identity]
        cost = metrics["provider_cost"]
        if cost["source_alias"] is not None:
            cost["source_alias"] = alias("cost", cost["source_alias"])
        metadata = {key: event[key] for key in ("role", "phase", "kind", "source_status", "dispatch_status", "capture_status", "reason_code", "origin", "attempt", "sequence")}
        metadata.update(investigation_stage=snapshot.get("investigation_stage", "unspecified"), source_digest=event["bindings"].get("source"), runtime_digest=event["bindings"].get("runtime"), timing_basis="observed" if identity in measured_intervals else event["clock"]["timing_basis"], evidence_alias=evidence_alias, metrics=metrics, capability=deepcopy(snapshot["capability"]), allocation=snapshot["allocation"], actual_children=snapshot["actual_children"], complete=complete)
        span = {"id": alias("span", identity), "trace_id": alias("trace", trace), "parent_run_id": alias("span", event["parent_span_id"]) if event["parent_span_id"] else None, "name": "rna." + event["role"] + "." + event["phase"], "run_type": "tool" if event["role"] == "numerical" else "chain", "inputs": {}, "outputs": {}, "extra": {"metadata": metadata}}
        validate_projected_span(span)
        if len(canonical_bytes(spans + [span])) > caps["max_bytes"]:
            gaps.append("byte_cap")
            complete = False
            break
        spans.append(span)
        local[evidence_alias] = {"local_span_id": identity, "events": deepcopy(by_span[identity])}
    if not spans:
        raise ValueError("Projection cap cannot retain an evidence root")
    if not complete:
        for span in spans:
            span["extra"]["metadata"]["complete"] = False
    # A bounded completeness record lives in projection metadata rather than an invented executed span.
    generation_jobs = snapshot["format"] == JOB_SNAPSHOT_FORMAT
    return {"format": JOB_PROJECTION_FORMAT if generation_jobs else PROJECTION_FORMAT, "spans": spans, "local_evidence": {"namespace": export_namespace, "workflow_binding": snapshot["workflow_binding"], "aliases": local, "gaps": sorted(set(gaps)), "omitted_spans": len(ordered) - len(spans), "authority": "local_verified_evidence_not_external_root_attestation", "observer_events": deepcopy(snapshot.get("observer_events", []))}, "complete": complete, "accounting": _job_accounting(snapshot["ledger_accounting"]) if generation_jobs else _accounting([by_span[identity][-1] for identity in ordered], complete, snapshot.get("ledger_accounting"))}


def _absolute(path):
    value = Path(path)
    if not value.is_absolute() or ".." in value.parts:
        raise ValueError("RNA trace paths must be absolute and traversal-free")
    for parent in (value, *value.parents):
        if parent.is_symlink():
            raise ValueError("RNA trace paths cannot contain symlinks")
    return value


def _source_identity(paths):
    budget = 64 * 1024 * 1024
    identity = {}
    for path in paths:
        path = _absolute(path)
        if not path.is_file():
            raise ValueError("Read-only RNA trace source does not exist")
        stat = path.stat()
        if stat.st_size > budget:
            raise ValueError("RNA trace aggregate read cap exceeded")
        data = path.read_bytes()
        budget -= len(data)
        identity[str(path)] = (stat.st_size, stat.st_mtime_ns, hashlib.sha256(data).hexdigest())
        for suffix in ("-wal", "-shm", "-journal"):
            sidecar = Path(str(path) + suffix)
            if sidecar.exists():
                raise ValueError("RNA trace requires quiescent retained databases")
    return identity


def read_rna_trace_snapshot(*, actions, checkpoint_path, investigation_backend, workflow_id: str,
                            allow_evidence_read: bool, campaign_id=None) -> dict:
    if allow_evidence_read is not True:
        raise PermissionError("Explicit RNA evidence read authorization required")
    from .codex_signedin import SignedInCodexInvestigationBackend
    if type(investigation_backend) is SignedInCodexInvestigationBackend:
        if (campaign_id is None or actions.ledger is not investigation_backend.ledger
                or actions.planner.methods is not investigation_backend.methods):
            raise PermissionError("Exact signed-in job campaign and source stores required")
        from .codex_job_trace import read_codex_job_trace_snapshot
        return read_codex_job_trace_snapshot(backend=investigation_backend, campaign_id=campaign_id,
            workflow_id=workflow_id, observer_path=getattr(actions, "trace_observer_path", None),
            allow_evidence_read=True)
    if campaign_id is not None:
        raise ValueError("Explicit job campaign requires the signed-in job loader")
    methods, ledger = actions.planner.methods, actions.ledger
    if methods.readonly is not True or ledger.readonly is not True:
        raise PermissionError("RNA trace snapshots require read-only existing stores")
    checkpoint = _absolute(checkpoint_path)
    before = _source_identity([checkpoint, methods.database, ledger.path])
    from .langgraph_investigations import IndependentRNAWorkflow
    # Bound every immutable artifact path before the underlying verifier can read it.
    # This temporary local wrapper does not intercept model/tool APIs or alter store content.
    original_artifact_path = methods.artifact_path
    had_override = "artifact_path" in methods.__dict__
    prior_override = methods.__dict__.get("artifact_path")
    artifact_identities = {}
    # Reserve both source identity passes and four artifact-length reads per
    # path access (verification plus bounded document readers). This caps the
    # API-level evidence allowance, not opaque SQLite/kernel page-cache traffic.
    remaining = 64 * 1024 * 1024 - 2 * sum(value[0] for value in before.values())
    if remaining < 0:
        raise ValueError("RNA trace aggregate verification read cap exceeded")
    def bounded_artifact_path(artifact_id):
        nonlocal remaining
        record = methods.get("artifact", artifact_id)
        path = _absolute(methods.root / "objects" / record["sha256"])
        stat = path.stat()
        if 4 * stat.st_size > remaining:
            raise ValueError("RNA trace aggregate evidence read cap exceeded")
        remaining -= 4 * stat.st_size
        identity = (stat.st_ino, stat.st_size, stat.st_mtime_ns)
        previous = artifact_identities.setdefault(path, identity)
        if previous != identity:
            raise ValueError("RNA trace artifact changed during snapshot")
        verified_path = original_artifact_path(artifact_id)
        if Path(verified_path) != path:
            raise ValueError("RNA trace artifact escaped retained store")
        return verified_path
    methods.artifact_path = bounded_artifact_path
    workflow = None
    try:
        workflow = IndependentRNAWorkflow.open_replay(actions, checkpoint, investigation_backend=investigation_backend)
        cursor = workflow.replay(workflow_id)
        campaign = ledger.get_campaign(workflow_id)
        reservations = ledger.campaign_reservations(workflow_id)
        if campaign is None:
            raise ValueError("RNA trace has no admitted retained campaign")
        binding = trace_digest({"workflow_id": workflow_id, "cursor": cursor, "campaign_identity": campaign["identity"], "policy": campaign["policy_digest"], "capability": campaign["capability_digest"]})
        namespace = uuid.uuid5(uuid.NAMESPACE_OID, binding)
        def uid(key):
            return str(uuid.uuid5(namespace, key))
        capability = campaign["capability"]
        status = capability.get("capability_status", "unknown")
        if status not in QUALIFICATIONS:
            raise ValueError("Unknown retained backend qualification")
        backend_kind = capability.get("backend", "unknown")
        if backend_kind not in CAPABILITY_KINDS:
            raise ValueError("Unknown retained backend kind")
        summary = {"backend_kind": backend_kind, "qualification_status": status, "admitted": True}
        root = uid("root")
        events = []
        def emit(key, parent, role, phase, source_status, kind="verification", *, capture=None, request=None, reason="none", metrics=None, dispatch="not_dispatched"):
            refs = {"workflow": binding, "policy": campaign["policy_digest"], "source": capability["source_digest"], "runtime": capability["runtime_digest"]}
            if request:
                refs["invocation"] = trace_digest(request)
            if capture:
                refs["capture"] = trace_digest(capture)
            event = {"format": "devbench.rna-trace-event/1", "event_id": uid("event:" + key), "trace_id": root, "span_id": uid(key), "parent_span_id": parent, "sequence": 0, "attempt": 1, "role": role, "phase": phase, "kind": kind, "source_status": source_status, "dispatch_status": dispatch, "capture_status": "verified" if capture else "unavailable", "clock": {"utc": None, "monotonic_ns": None, "process_alias": uid("process:" + key), "timing_basis": "unavailable"}, "bindings": refs, "metrics": metrics or {"input_tokens": None, "output_tokens": None, "total_tokens": None, "elapsed_seconds": None, "peak_rss_mb": None, "provider_cost": {"value": None, "currency": None, "basis": "unavailable", "source_alias": None}, "usage_basis": "unavailable"}, "reason_code": reason, "origin": "retained_authoritative_evidence"}
            events.append(validate_trace_event(event))
            return event["span_id"]
        emit("root", None, "coordinator", "workflow", cursor["status"])
        events[-1]["bindings"].update(policy=campaign["policy_digest"], source=capability["source_digest"], runtime=capability["runtime_digest"])
        decision = emit("decision", root, "coordinator", "investigation_decision", "completed" if cursor.get("decision") else "incomplete", reason="not_allocated" if cursor.get("decision") and cursor["allocated_children"] == 0 else "none" if cursor.get("decision") else "result_missing")
        dispatched_children = set()
        for row in reservations:
            child = row.get("child_id")
            capture = row.get("capture")
            if child and capture and capture["actual_calls"]:
                dispatched_children.add(child)
            request = row["request"]
            phase = request.get("purpose", row["purpose"])
            if phase not in PHASES:
                phase = "investigate" if child else "invocation"
            key = "reservation:" + row["ref"]
            attempt = emit(key, decision if child else root, "investigator" if child else "lead", phase, row["status"], "reservation", request=request, capture=capture, reason="uncertain" if row["status"] == "uncertain" else "none")
            events[-1]["attempt"] = row["attempt"] + 1
            if capture is not None:
                manifest = investigation_backend._document(capture["capture_ref"], artifact_type="rna_investigation_capture")
                files = manifest.get("files", {})
                offline = manifest.get("format") == "offline-fixture.model-capture/1"
                if offline:
                    if capability.get("capability_status") != "offline_qualified" or capability.get("backend") == "codex-cli":
                        raise ValueError("Offline capture cannot establish native qualification")
                    expected_fields = {"format", "scope", "reservation_ref", "request_digest", "bindings", "context_ref", "response_ref", "usage_ref", "response_digest", "status", "unknown_usage"}
                    expected_sessions = ["offline-preflight-" + row["ref"][-32:], "offline-action-" + row["ref"][-32:]]
                    if (set(manifest) != expected_fields or manifest["scope"] != "fixture_only"
                            or manifest["reservation_ref"] != row["ref"] or manifest["request_digest"] != row["request_digest"]
                            or capture["actual_calls"] != 2 or capture["native_session_ids"] != expected_sessions):
                        raise ValueError("Unsupported offline fixture phase evidence")
                    # These are software-fixture phase identities, not native
                    # dispatch checkpoints. Keep their dispatch status unknown.
                    files = {"exec-preflight/dispatch_checkpoint.json": True, "selection-dispatch.json": True}
                    aggregate = capture["usage"]
                    events[-1]["metrics"].update(input_tokens=aggregate["input_tokens"], output_tokens=aggregate["output_tokens"], total_tokens=aggregate["normalized_total_tokens"], usage_basis="verified_complete" if aggregate["normalized_total_tokens"] is not None else "observed_partial" if aggregate["input_tokens"] is not None or aggregate["output_tokens"] is not None else "unavailable")
                actual = [("permission_preflight", "exec-preflight/dispatch_checkpoint.json"), ("selection", "selection-dispatch.json")]
                represented = 0
                for call_phase, marker in actual:
                    if marker not in files:
                        continue
                    represented += 1
                    usage = None
                    if not offline:
                        native_phases = capture["usage"].get("native_usage", {}).get("phase_usage", [])
                        if len(native_phases) == capture["actual_calls"]:
                            from devbench.study.model_telemetry import normalize_usage
                            usage = normalize_usage(native_phases[represented - 1])
                    call_metrics = None
                    if usage is not None:
                        input_tokens, output_tokens = usage["input_tokens"], usage["output_tokens"]
                        total = usage["normalized_total_tokens"]
                        call_metrics = {"input_tokens": input_tokens, "output_tokens": output_tokens, "total_tokens": total, "elapsed_seconds": None, "peak_rss_mb": None, "provider_cost": {"value": None, "currency": None, "basis": "unavailable", "source_alias": None}, "usage_basis": "verified_complete" if total is not None else "observed_partial" if input_tokens is not None or output_tokens is not None else "unavailable"}
                    emit(key + ":" + call_phase, attempt, "investigator" if child else "lead", call_phase, capture["status"], "capture", request=request, capture=capture, dispatch="unknown" if offline else "attempted", reason="none" if call_metrics and call_metrics["usage_basis"] == "verified_complete" else "usage_unavailable", metrics=call_metrics)
                    events[-1]["attempt"] = row["attempt"] + 1
                if represented != capture["actual_calls"]:
                    raise ValueError("Trace dispatch evidence does not match verified actual calls")
        for child_id, dispatch_ref in cursor["dispatch_refs"].items():
            outcome = investigation_backend.reconcile_capture(dispatch_ref)
            status = outcome["status"]
            if status not in SOURCE_STATUSES:
                raise ValueError("Unknown retained investigator outcome")
            emit("child-outcome:" + child_id, decision, "investigator", "investigate", status,
                 "completion" if status in {"completed", "abstained"} else "failure" if status == "failed" else "reconcile",
                 request=dispatch_ref, capture=outcome.get("result_ref"),
                 reason="result_missing" if status == "incomplete" and outcome.get("result_ref") is None else "uncertain" if status == "uncertain" else "failed" if status == "failed" else "none")
        # Existing numerical operation receipts are referenced once, never refit or duplicated per investigator.
        parent_cursor = cursor["parent_cursor"]
        for ref in parent_cursor.get("operation_refs", []):
            node, attempt_text = ref.rsplit(":", 1)
            if node not in SERIAL_ROUTE:
                raise ValueError("Unknown retained workflow phase")
            if any(row["node"] == node and row["attempt"] == int(attempt_text) for row in reservations):
                continue
            record = ledger.get(workflow_id, node, int(attempt_text))
            if record is None:
                raise ValueError("Retained numerical operation missing")
            emit("operation:" + ref, root, "numerical" if node == "run" else "review" if node in {"verify", "candidate_review", "design_review", "conclusion_review"} else "coordinator", node, record["status"], "verification", request=record["request"])
            events[-1]["attempt"] = int(attempt_text) + 1
        if cursor.get("reconciliation_ref") is not None:
            barrier = emit("barrier", root, "coordinator", "investigation_barrier", "completed", request=cursor["result_refs"])
            emit("attachment", root, "coordinator", "attach", "completed", request=cursor["reconciliation_ref"])
        allocation = cursor["allocated_children"]
        ledger_accounting = {"verified_spent_tokens": campaign["verified_spent_tokens"], "verified_calls": campaign["verified_calls"], "unresolved_count": campaign["unresolved_count"], "unknown_usage_count": sum(row["status"] in {"active", "uncertain"} if row["capture"] is None else row["capture"]["actual_calls"] > 0 and row["capture"]["usage"]["normalized_total_tokens"] is None for row in reservations), "qualification": campaign["qualification"], "policy_kind": campaign["policy"]["kind"]}
        snapshot = {"ledger_accounting": ledger_accounting, "investigation_stage": cursor["stage"], "format": SNAPSHOT_FORMAT, "workflow_binding": binding, "events": events, "capability": summary, "allocation": allocation, "actual_children": len(dispatched_children), "complete": False, "gaps": ["observer_absent", "timing_unavailable", "usage_unavailable"]}
        observer_path = getattr(actions, "trace_observer_path", None)
        if observer_path is not None:
            from .rna_trace_receipts import LocalTraceObserver
            observer_path = _absolute(observer_path)
            if observer_path in {checkpoint, Path(methods.database), Path(ledger.path)}:
                raise ValueError("Observer evidence must be disjoint from RNA source stores")
            observer_before = _source_identity([observer_path])
            if 4 * sum(value[0] for value in observer_before.values()) > remaining:
                raise ValueError("RNA trace aggregate observer read cap exceeded")
            remaining -= 4 * sum(value[0] for value in observer_before.values())
            observer = LocalTraceObserver.open_existing(observer_path)
            expected = {"source_digest": capability["source_digest"], "runtime_digest": capability["runtime_digest"], "policy_digest": campaign["policy_digest"]}
            if observer.identities != expected:
                raise ValueError("Observer source/runtime/policy binding differs from retained campaign")
            original_events = observer.read_events(workflow_id)
            contexts = observer.read_contexts(workflow_id)
            original_roots = [row for row in contexts if row["parent_span_id"] is None]
            if len(original_roots) != 1 or not original_events:
                raise ValueError("Observer receipt topology has no unique root")
            live_root = original_roots[0]
            if live_root["phase"] != "workflow" or live_root["invocation_binding_digest"] != trace_digest(workflow_id):
                raise ValueError("Observer root belongs to a different workflow")
            context_index = {row["span_id"]: row for row in contexts}
            merged = []
            for original in original_events:
                original = validate_trace_event(original)
                context = context_index.get(original["span_id"])
                if context is None or any(original[key] != context[key] for key in ("trace_id", "span_id", "parent_span_id", "role", "phase", "attempt")):
                    raise ValueError("Observer event differs from its retained context")
                if any(context[key] != expected[key] for key in expected) or original["bindings"].get("invocation") != context["invocation_binding_digest"]:
                    raise ValueError("Observer invocation/source/runtime/policy context binding mismatch")
                if original["bindings"].get("workflow") != trace_digest(workflow_id) or any(original["bindings"].get(key) != expected[key + "_digest"] for key in ("source", "runtime", "policy")):
                    raise ValueError("Observer event identity binding mismatch")
                normalized = deepcopy(original)
                normalized["bindings"]["workflow"] = binding
                merged.append(normalized)
            # Append independently verified terminal/resource facts onto matching live spans.
            # Preserve original receipts in snapshot observer_events; no source bytes are rewritten.
            normalized_by_span = {}
            for row in merged:
                normalized_by_span.setdefault(row["span_id"], []).append(row)
            id_map = {root: live_root["span_id"]}
            for row in events[1:]:
                candidates = [context for context in contexts if context["phase"] == row["phase"] and context["role"] == row["role"] and context["attempt"] == row["attempt"] and context["invocation_binding_digest"] == row["bindings"].get("invocation")]
                id_map[row["span_id"]] = candidates[0]["span_id"] if len(candidates) == 1 else str(uuid.uuid5(uuid.UUID(live_root["trace_id"]), "retained:" + row["span_id"]))
            for row in events:
                span_id = id_map[row["span_id"]]
                history = normalized_by_span.get(span_id, [])
                if history:
                    last = history[-1]
                    if row["source_status"] not in {"completed", "failed", "cancelled", "abstained", "incomplete", "uncertain"} and row["capture_status"] != "verified":
                        continue
                    revised = deepcopy(last)
                    revised.update(event_id=str(uuid.uuid5(uuid.UUID(live_root["trace_id"]), "retained-event:" + row["event_id"])), sequence=last["sequence"] + 1, kind=row["kind"], source_status=row["source_status"], dispatch_status=row["dispatch_status"], capture_status=row["capture_status"], metrics=deepcopy(row["metrics"]), reason_code=row["reason_code"])
                    # No historical duration is attached to an observer timestamp.
                    merged.append(validate_trace_event(revised))
                    history.append(revised)
                else:
                    revised = deepcopy(row)
                    revised.update(trace_id=live_root["trace_id"], span_id=span_id, parent_span_id=id_map[row["parent_span_id"]] if row["parent_span_id"] else None)
                    merged.append(validate_trace_event(revised))
            snapshot["events"] = merged
            snapshot["observer_events"] = deepcopy(original_events)
            snapshot["gaps"] = [reason for reason in snapshot["gaps"] if reason != "observer_absent"]
            if observer_before != _source_identity([observer_path]):
                raise ValueError("Observer receipts changed during snapshot read")
        _validate_snapshot(snapshot)
        if before != _source_identity([checkpoint, methods.database, ledger.path]):
            raise ValueError("RNA trace source changed during read-only projection")
        for path, identity in artifact_identities.items():
            stat = path.stat()
            if (stat.st_ino, stat.st_size, stat.st_mtime_ns) != identity:
                raise ValueError("RNA trace artifact changed during snapshot")
        return snapshot
    finally:
        if workflow is not None:
            workflow.connection.close()
        if had_override:
            methods.artifact_path = prior_override
        else:
            del methods.__dict__["artifact_path"]
