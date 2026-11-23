"""Optional generation-job receipts and bounded, read-only local projection.

Job reservations account for coordinator launch intents. Internal provider
requests, subscription credits and cost remain unknown. Native token reports
are optional post-hoc observations, never preventive allocations or proof of
complete provider usage. This module has no client, credential or network API.
"""
from copy import deepcopy
from pathlib import Path
import uuid

from .codex_job_policy import JOB_CAPTURE_FORMAT, validate_job_capture
from .rna_trace_contract import canonical_bytes, trace_digest, validate_trace_event
from .rna_trace_receipts import InvocationTraceReceipt, LocalTraceObserver, _empty_metrics

_STAGES = frozenset({"method_scientific_planning", "method_capability_resolution", "method_trial_design"})


def prepare_job_trace(observer, workflow_id, binding, reservation, *, role="lead"):
    """Reuse the existing exact reservation sidecar without execution authority."""
    if observer is None:
        return None
    try:
        return observer.prepare_invocation(workflow_id, binding, reservation, role=role)
    except Exception:
        return None


def job_trace_receipt(reference, *, reservation, private_root):
    """Read one explicit private sidecar; optional observation cannot block work."""
    if reference is None:
        return None
    try:
        return InvocationTraceReceipt(reference, binding_digest=reservation.request_digest,
            policy_digest=reservation.policy_digest, source_digest=reservation.request["source_digest"],
            runtime_digest=reservation.request["runtime_digest"], private_root=private_root, db_timeout=0)
    except Exception:
        return None


def observe_job_launch(receipt):
    """Observe a local launch attempt, without claiming a provider request."""
    if receipt is not None:
        try:
            receipt.emit("dispatch", "active", dispatch="attempted")
        except Exception:
            pass


def complete_job_trace(observer, reference, *, reservation, capture, status):
    """Observe already-verified lifecycle; absent usage never blocks completion."""
    if observer is None or reference is None:
        return None
    try:
        if capture is not None:
            validate_job_capture(capture, capture["capture_ref"], reservation)
            if status != capture["status"]:
                raise ValueError("Observed job terminal status differs from capture")
        observer.complete_invocation(reference, reservation=reservation,
            status="failed" if status == "not_started" else status,
            capture_verified=capture is not None, db_timeout=0,
            dispatch_status=("not_dispatched" if status == "not_started" else
                "attempted" if capture is not None and capture["launch_confirmed"] else "unknown"))
    except Exception:
        if type(observer) is LocalTraceObserver:
            observer.failure_count += 1
    return None


def _reported_usage(capture):
    """Normalize only known reported counts, keeping raw telemetry local."""
    if capture is None or "usage" not in capture:
        return None
    from devbench.study.model_telemetry import normalize_usage
    usage = normalize_usage(capture["usage"])
    for key in ("input_tokens", "output_tokens", "normalized_total_tokens"):
        count = usage[key]
        if count is not None and (type(count) is not int or not 0 <= count <= 2**63 - 1):
            raise ValueError("Invalid optional reported job token count")
    return usage


def job_trace_metrics(capture):
    """Trace /1 metrics may show partial components, never complete job tokens."""
    metrics = _empty_metrics()
    usage = _reported_usage(capture)
    if usage is not None and any(usage[key] is not None for key in ("input_tokens", "output_tokens")):
        metrics.update(input_tokens=usage["input_tokens"], output_tokens=usage["output_tokens"],
                       usage_basis="observed_partial")
    return metrics


def job_trace_accounting(campaign, reservations):
    """Summarize admitted units without relabeling jobs as requests or tokens."""
    if campaign["policy"]["kind"] != "generation_jobs":
        raise ValueError("Generation job policy required")
    reported, known, unknown = 0, 0, 0
    for row in reservations:
        capture = row["capture"]
        if capture is not None and capture.get("format") != JOB_CAPTURE_FORMAT:
            raise ValueError("Unsupported generation job capture discriminator")
        usage = _reported_usage(capture)
        total = None if usage is None else usage["normalized_total_tokens"]
        if total is not None:
            reported += total
            known += 1
        elif capture is not None or row["status"] in {"reserved", "active", "uncertain"}:
            unknown += 1
    from .rna_trace_projection import JOB_ACCOUNTING_FORMAT, _validate_job_accounting
    result = dict(format=JOB_ACCOUNTING_FORMAT, policy_kind="generation_jobs",
        **{key: campaign[key] for key in ("verified_jobs", "outstanding_reserved_jobs", "unresolved_count", "qualification", "verified_seconds")},
        reported_token_subtotal=reported if known else None, reported_usage_jobs=known,
        unknown_usage_jobs=unknown)
    _validate_job_accounting(result)
    return result


def prepare_job_observation(*, observer, workflow_id):
    """Prepare disabled hosted aliases only; retain every actual source event."""
    if type(observer) is not LocalTraceObserver:
        raise TypeError("Actual local job observer required")
    events = observer.read_events(workflow_id)
    contexts = observer.read_contexts(workflow_id)
    from devbench.study import rna_hosted_workflow
    prepare = getattr(rna_hosted_workflow, "prepare_generation_context_observation", None)
    dispatches = sum(row["kind"] == "dispatch" for row in events)
    if prepare is None and dispatches:
        prepared = dict(blockers=["signed_in_dispatch_profile_not_approved"])
    else:
        prepared = (prepare or rna_hosted_workflow.prepare_context_observation)(observer=observer, workflow_id=workflow_id)
        prepared["blockers"] = []
    if events != observer.read_events(workflow_id) or contexts != observer.read_contexts(workflow_id):
        raise ValueError("Job observations changed during disabled preparation")
    return dict(format="devbench.codex-job-observation-preparation/1", authorize_upload=False,
        authorize_remote_read=False, source_event_count=len(events), context_count=len(contexts),
        dispatch_event_count=dispatches, source_events_digest=trace_digest(events), **prepared)


def read_codex_job_trace_snapshot(*, backend, campaign_id, workflow_id,
                                  observer_path=None, allow_evidence_read):
    """Read retained job evidence without launching a client or inspecting auth.

    Backend qualification is the retained capability, not a new claim from a
    completed lifecycle. Raw capture verification remains local and read-only.
    """
    if allow_evidence_read is not True:
        raise PermissionError("Explicit generation job evidence read authorization required")
    from .codex_signedin import SignedInCodexInvestigationBackend
    from .workflow_operations import WorkflowOperationLedger, _digest, _json
    from .rna_trace_projection import _absolute, _source_identity
    if type(backend) is not SignedInCodexInvestigationBackend or type(backend.ledger) is not WorkflowOperationLedger:
        raise TypeError("Actual signed-in backend and protected ledger required")
    if backend.methods.readonly is not True or backend.ledger.readonly is not True:
        raise PermissionError("Job trace requires read-only existing stores")
    stores = [_absolute(backend.methods.database), _absolute(backend.ledger.path)]
    initial = _source_identity(stores)
    campaign = backend.ledger.get_campaign(campaign_id)
    if (campaign is None or campaign["policy"]["kind"] != "generation_jobs"
            or campaign["policy"] != backend.policy
            or campaign["capability"].get("backend") != "codex-chatgpt-cli"
            or campaign["capability"].get("capability_status") not in {"unqualified", "offline_qualified"}
            or campaign["identity"].get("trial_id") != workflow_id
            or campaign["identity"].get("trial_purpose") != "workflow_generation"
            or campaign_id != "trial:" + campaign["identity"].get("trial_binding_sha256", "")):
        raise PermissionError("Retained job campaign, route or generation purpose differs")
    rows = backend.ledger.campaign_reservations(campaign_id)
    if not 1 <= len(rows) <= 3:
        raise ValueError("Bounded parent-only generation job reservations required")
    paths = list(stores)
    for row in rows:
        request = row["request"]
        if (row["child_id"] is not None or request.get("purpose") not in _STAGES
                or request.get("parent_session_id") != campaign["session_id"]
                or _digest(_json(request)) != row["request_digest"]
                or any(request.get(key) != campaign["capability"][key]
                       for key in ("source_digest", "runtime_digest", "backend_digest"))):
            raise ValueError("Retained job invocation differs from its campaign")
        if row["capture"] is not None:
            ref = row["capture"]["capture_ref"]
            record = backend.methods.get("artifact", ref["artifact_id"])
            if (record["artifact_type"] != "rna_investigation_capture" or record["sha256"] != ref["sha256"]
                    or ref["artifact_type"] != record["artifact_type"] or ref["size_bytes"] != record["size_bytes"]):
                raise ValueError("Job capture artifact identity differs")
            paths.append(_absolute(backend.methods.root / "objects" / record["sha256"]))
    if observer_path is not None:
        observer_path = _absolute(observer_path)
        if observer_path in paths:
            raise ValueError("Observer must be disjoint from job evidence stores")
        paths.append(observer_path)
    if 4 * sum(path.stat().st_size for path in paths) > 64 * 1024 * 1024:
        raise ValueError("Job evidence exceeds aggregate read bound")
    before = _source_identity(paths)
    if any(before[key] != value for key, value in initial.items()):
        raise ValueError("Job stores changed during evidence read")
    for row in rows:
        if row["capture"] is not None:
            reservation = backend.ledger._reservation(row, campaign)
            ref = row["capture"]["capture_ref"]
            verified = validate_job_capture(backend.verify_capture(ref, reservation), ref, reservation)
            if verified != row["capture"]:
                raise ValueError("Retained job receipt differs from independently verified capture")
    observer = LocalTraceObserver.open_existing(observer_path) if observer_path is not None else None
    snapshot = _job_snapshot(campaign=campaign, rows=rows, workflow_id=workflow_id, observer=observer)
    if before != _source_identity(paths) or campaign != backend.ledger.get_campaign(campaign_id):
        raise ValueError("Job evidence changed during read-only snapshot")
    return snapshot


def _job_snapshot(*, campaign, rows, workflow_id, observer=None):
    from .rna_trace_projection import JOB_SNAPSHOT_FORMAT, _validate_snapshot
    capability = campaign["capability"]
    expected = dict(source_digest=capability["source_digest"], runtime_digest=capability["runtime_digest"],
                    policy_digest=campaign["policy_digest"])
    binding = trace_digest(dict(workflow=workflow_id, campaign=campaign["campaign_id"],
        identity=campaign["identity"], policy=campaign["policy_digest"], capability=campaign["capability_digest"]))
    namespace = uuid.uuid5(uuid.NAMESPACE_OID, binding)
    uid = lambda value: str(uuid.uuid5(namespace, value))
    root, trace, events, originals, gaps = uid("root"), uid("root"), [], [], []
    by_digest = {row["request_digest"]: row for row in rows}
    if len(by_digest) != len(rows):
        raise ValueError("Distinct job invocation bindings required")
    contexts = []
    if observer is not None:
        if observer.identities != expected:
            raise ValueError("Job observer runtime or policy differs from campaign")
        contexts, originals = observer.read_contexts(workflow_id), observer.read_events(workflow_id)
        roots = [row for row in contexts if row["parent_span_id"] is None]
        if (len(roots) != 1 or roots[0]["role"] != "coordinator" or roots[0]["phase"] != "workflow"
                or roots[0]["invocation_binding_digest"] != trace_digest(workflow_id)):
            raise ValueError("Job observer requires its exact workflow root")
        root, trace = roots[0]["span_id"], roots[0]["trace_id"]
        index = {row["span_id"]: row for row in contexts}
        for context in contexts:
            if (context["trace_id"] != trace or any(context[key] != value for key, value in expected.items())
                    or context["parent_span_id"] is not None and context["parent_span_id"] not in index
                    or context["role"] in {"investigator", "numerical"}):
                raise ValueError("Job observer topology differs from parent-only generation")
            if context["role"] == "lead":
                row = by_digest.get(context["invocation_binding_digest"])
                if (row is None or context["phase"] != row["request"]["purpose"]
                        or context["attempt"] != row["attempt"] + 1):
                    raise ValueError("Observed job context lacks its retained reservation")
        for original in originals:
            original = validate_trace_event(original)
            context = index.get(original["span_id"])
            if (context is None or any(original[key] != context[key]
                    for key in ("trace_id", "parent_span_id", "role", "phase", "attempt"))
                    or original["bindings"].get("workflow") != trace_digest(workflow_id)
                    or original["bindings"].get("invocation") != context["invocation_binding_digest"]
                    or any(original["bindings"].get(key) != expected[key + "_digest"] for key in ("source", "runtime", "policy"))):
                raise ValueError("Job observer event binding differs from retained context")
            allowed_metrics = _empty_metrics()
            if context["role"] == "lead" and original["capture_status"] == "verified":
                row = by_digest[context["invocation_binding_digest"]]
                if row["capture"] is None:
                    raise ValueError("Observed verified job capture lacks retained evidence")
                allowed_metrics = job_trace_metrics(row["capture"])
            if original["metrics"] not in (_empty_metrics(), allowed_metrics):
                raise ValueError("Job observer metrics lack exact retained reported usage")
            normalized = deepcopy(original)
            normalized["bindings"]["workflow"] = binding
            events.append(normalized)
            if original["kind"] == "gap":
                gaps.append(original["reason_code"])
        represented = {row["span_id"] for row in originals}
        for context in contexts:
            if context["span_id"] in represented:
                continue
            gaps.append("observer_failure")
            events.append(validate_trace_event(dict(format="devbench.rna-trace-event/1",
                event_id=uid("missing-context:" + context["span_id"]),
                **{key: context[key] for key in ("trace_id", "span_id", "parent_span_id", "attempt", "role", "phase")},
                sequence=0, kind="gap", source_status="uncertain", dispatch_status="unknown",
                capture_status="unavailable", reason_code="observer_failure",
                clock=dict(utc=None, monotonic_ns=None, process_alias=uid("process"), timing_basis="unavailable"),
                bindings=dict(workflow=binding, invocation=context["invocation_binding_digest"],
                    source=expected["source_digest"], runtime=expected["runtime_digest"], policy=expected["policy_digest"]),
                metrics=_empty_metrics(), origin="retained_authoritative_evidence")))

    def emit(identity, parent, phase, status, *, invocation=None, capture=None, attempt=1):
        histories = [event for event in events if event["span_id"] == identity]
        refs = dict(workflow=binding, source=expected["source_digest"], runtime=expected["runtime_digest"], policy=expected["policy_digest"])
        if invocation is not None:
            refs["invocation"] = invocation
        if capture is not None:
            refs["capture"] = trace_digest(capture)
        event = dict(format="devbench.rna-trace-event/1", event_id=uid("verified:" + identity),
            trace_id=trace, span_id=identity, parent_span_id=parent, sequence=len(histories), attempt=attempt,
            role="coordinator" if parent is None else "lead", phase=phase, kind="verification",
            source_status=status, dispatch_status="attempted" if capture and capture["launch_confirmed"] else "unknown",
            capture_status="verified" if capture else "unavailable", reason_code="uncertain" if status == "uncertain" else "none",
            clock=dict(utc=None, monotonic_ns=None, process_alias=uid("process"), timing_basis="unavailable"),
            bindings=refs, metrics=job_trace_metrics(capture), origin="retained_authoritative_evidence")
        if histories:
            # Preserve immutable invocation identity for the live root and span.
            event["bindings"] = deepcopy(histories[-1]["bindings"])
        events.append(validate_trace_event(event))

    lifecycle_complete = campaign["qualification"] == "complete" and not campaign["outstanding_reserved_jobs"] and not campaign["unresolved_count"]
    status = ("uncertain" if campaign["unresolved_count"] else "failed" if any(row["status"] in {"failed", "cancelled"} for row in rows)
              else "completed" if lifecycle_complete else "incomplete")
    emit(root, None, "workflow", status)
    for row in rows:
        context = next((context for context in contexts if context["role"] == "lead"
            and context["invocation_binding_digest"] == row["request_digest"]), None)
        if (observer is not None and row["capture"] is not None and row["capture"]["launch_confirmed"]
                and (context is None or not any(event["span_id"] == context["span_id"]
                    and event["kind"] == "dispatch" and event["dispatch_status"] == "attempted" for event in originals))):
            gaps.append("observer_failure")
        emit(context["span_id"] if context else uid("reservation:" + row["ref"]),
            context["parent_span_id"] if context else root, row["request"]["purpose"], row["status"],
            invocation=row["request_digest"], capture=row["capture"], attempt=row["attempt"] + 1)
        if row["status"] in {"reserved", "active", "uncertain"}:
            gaps.append("uncertain" if row["status"] == "uncertain" else "capture_missing")
    snapshot = dict(format=JOB_SNAPSHOT_FORMAT, accounting_kind="generation_jobs", workflow_binding=binding,
        events=events, capability=dict(backend_kind=capability["backend"], qualification_status=capability["capability_status"], admitted=True),
        allocation=0, actual_children=0, complete=lifecycle_complete and not gaps,
        gaps=sorted(set(gaps)), ledger_accounting=job_trace_accounting(campaign, rows))
    if originals:
        snapshot["observer_events"] = deepcopy(originals)
    _validate_snapshot(snapshot)
    return snapshot


def project_codex_job_trace(*, backend, campaign_id, workflow_id, export_namespace, limits,
                            observer_path=None, allow_evidence_read):
    """Project only an independently reverified job snapshot, without upload."""
    from .rna_trace_projection import project_rna_trace
    snapshot = read_codex_job_trace_snapshot(backend=backend, campaign_id=campaign_id,
        workflow_id=workflow_id, observer_path=observer_path, allow_evidence_read=allow_evidence_read)
    return project_rna_trace(snapshot, export_namespace=export_namespace, limits=limits)
