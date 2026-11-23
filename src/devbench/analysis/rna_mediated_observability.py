"""Typed parent-only mediated observations; correlation never authorizes a call.

Retained wire captures and the protected campaign ledger establish usage. The
optional observer supplies local clocks and POST intent, never provider proof.
No request, response text, credential, filesystem path or private ID is exported.
"""
from copy import deepcopy
from pathlib import Path
import uuid

from .rna_trace_contract import trace_digest, validate_trace_event
from .rna_trace_receipts import LocalTraceObserver, _empty_metrics
from .workflow_operations import ModelCallReservation, OperationConflict, _digest, _json

SNAPSHOT_FORMAT = "devbench.rna-mediated-trace-snapshot/1"
_STAGES = {"method_scientific_planning", "method_capability_resolution", "method_trial_design"}


def _backend(backend):
    from .rna_mediated_backend import MediatedInvestigationBackend
    from .workflow_operations import WorkflowOperationLedger
    if type(backend) is not MediatedInvestigationBackend or type(backend.ledger) is not WorkflowOperationLedger:
        raise TypeError("Use the actual mediated backend and protected ledger")
    return backend


def _retained_campaign(backend, campaign_id, workflow_id=None):
    """Cheap observation binding after mandatory dispatch/settlement validation.

    This private helper never authorizes execution or an evidence export. The
    mandatory backend gate and ledger capture verifier remain on their existing
    execution paths, regardless of whether observation is enabled.
    """
    with backend.ledger._database() as connection:
        try:
            campaign = backend.ledger._campaign(connection, campaign_id)
        except OperationConflict as error:
            if error.args != ("Model campaign has not been admitted",):
                raise
            raise PermissionError("Mediated trace requires an admitted campaign") from None
    if (campaign is None or campaign["policy"] != backend.policy
            or campaign["capability"].get("backend") != "openai-responses-mediated"
            or campaign["capability"].get("capability_status") != "mediated_qualified"
            or campaign_id != "trial:" + campaign["identity"].get("trial_binding_sha256", "")
            or campaign["identity"].get("trial_purpose") != "workflow_generation"
            or type(campaign["identity"].get("trial_id")) is not str
            or workflow_id is not None and campaign["identity"]["trial_id"] != workflow_id):
        raise PermissionError("Mediated trace campaign, trial or current runtime binding differs")
    return campaign


def _campaign(backend, campaign_id, workflow_id=None):
    """Explicit evidence reads fully revalidate the current actual backend."""
    retained = _retained_campaign(backend, campaign_id, workflow_id)
    campaign = backend.ledger.get_campaign(campaign_id)
    if (any(campaign[key] != value for key, value in retained.items())
            or campaign["capability"] != backend.require_admission("hard_total_tokens")):
        raise PermissionError("Mediated trace current runtime differs from retained campaign")
    return campaign


def _record(backend, reservation, campaign):
    if type(reservation) is not ModelCallReservation:
        raise TypeError("Use an exact retained model reservation")
    row = backend.ledger.get_model_reservation(reservation)
    request = row["request"]
    if (row["campaign_id"] != campaign["campaign_id"] or row["child_id"] is not None
            or row["purpose"] not in _STAGES or request.get("purpose") != row["purpose"]
            or request.get("parent_session_id") != campaign["session_id"]
            or _digest(_json(request)) != row["request_digest"]
            or any(request.get(key) != campaign["capability"][key]
                   for key in ("source_digest", "runtime_digest", "backend_digest"))):
        raise PermissionError("Mediated trace reservation identity differs")
    return row


def validated_mediated_trace_receipt(backend, reservation, reference):
    """Validate the actual observer and reservation before dereferencing a sidecar.

    Invalid optional observations are dropped; they cannot alter admission,
    provider execution, settlement, retries or the scientific outcome.
    """
    observer = getattr(backend, "trace_observer", None)
    if reference is None or type(observer) is not LocalTraceObserver:
        return None
    try:
        _backend(backend)
        campaign = _retained_campaign(backend, reservation.campaign_id)
        _record(backend, reservation, campaign)
        expected = {"source_digest": campaign["capability"]["source_digest"],
                    "runtime_digest": campaign["capability"]["runtime_digest"],
                    "policy_digest": campaign["policy_digest"]}
        if (type(reference) is not dict
                or set(reference) != {"path", "sha256", "binding_digest", *expected}
                or type(reference["path"]) is not str
                or Path(reference["path"]).parent != observer.sidecar_dir
                or observer.identities != expected
                or reference["binding_digest"] != reservation.request_digest
                or any(reference[key] != value for key, value in expected.items())):
            raise ValueError("Mediated sidecar differs from its actual observer")
        from .rna_trace_receipts import InvocationTraceReceipt
        receipt = InvocationTraceReceipt(reference, binding_digest=reservation.request_digest,
            private_root=observer.path.parent, db_timeout=0, **expected)
        if (receipt.observer.path != observer.path
                or receipt.workflow_digest != trace_digest(campaign["identity"]["trial_id"])
                or receipt.context["phase"] != reservation.purpose
                or receipt.context["role"] != "lead"
                or receipt.context["attempt"] != reservation.attempt + 1):
            raise ValueError("Mediated sidecar trial or invocation topology differs")
        return receipt
    except Exception:
        observer.failure_count += 1
        return None


def _capture(backend, reservation, row):
    retained = row["capture"]
    if retained is None:
        return None
    verified = backend.ledger._verified_capture(
        backend.verify_capture(retained["capture_ref"], reservation), retained["capture_ref"], reservation)
    if verified != retained:
        raise ValueError("Retained mediated usage differs from independently verified wire capture")
    return verified


def _metrics(capture):
    metrics = _empty_metrics()
    if capture is not None:
        usage = capture["usage"]
        metrics.update(input_tokens=usage["input_tokens"], output_tokens=usage["output_tokens"],
            total_tokens=usage["normalized_total_tokens"], usage_basis="verified_complete"
            if usage["normalized_total_tokens"] is not None else "observed_partial"
            if usage["input_tokens"] is not None or usage["output_tokens"] is not None else "unavailable")
    return metrics


def settle_mediated_observation(backend, reservation, reference, capture_ref=None):
    """Observe the already-settled ledger; never settle or infer provider cost."""
    receipt = validated_mediated_trace_receipt(backend, reservation, reference)
    if receipt is None:
        return
    try:
        campaign = _retained_campaign(backend, reservation.campaign_id)
        row = _record(backend, reservation, campaign)
        if row["status"] in {"reserved", "active"}:
            raise ValueError("Mediated observation requires settled or uncertain accounting")
        # Mandatory settle_model_call already verified the raw wire response and
        # committed this exact receipt. Re-check its immutable shape/binding,
        # without repeating runtime authorization or raw capture I/O here.
        capture = row["capture"]
        if capture is not None:
            capture = backend.ledger._verified_capture(capture, capture["capture_ref"], reservation)
        if capture_ref is not None and (capture is None or capture_ref != capture["capture_ref"]):
            raise ValueError("Mediated observation capture differs from ledger")
        metrics = _metrics(capture)
        dispatch = "attempted" if capture is not None and capture["launch_confirmed"] else "unknown"
        if capture is not None:
            receipt.emit("capture", row["status"], phase="selection", dispatch=dispatch,
                         capture="verified", metrics=metrics)
        receipt.emit("completion" if row["status"] == "completed" else "failure", row["status"],
            phase="selection", dispatch=dispatch, capture="verified" if capture is not None else "unavailable",
            metrics=metrics, reason="uncertain" if row["status"] == "uncertain" else
            "failed" if row["status"] == "failed" else "none")
    except Exception:
        receipt.observer.failure_count += 1


def read_mediated_rna_trace_snapshot(*, backend, campaign_id, workflow_id, observer_path=None,
                                     allow_evidence_read):
    """Read exact parent-only retained evidence through the actual readonly backend."""
    if allow_evidence_read is not True:
        raise PermissionError("Explicit mediated evidence read authorization required")
    _backend(backend)
    if backend.methods.readonly is not True or backend.ledger.readonly is not True:
        raise PermissionError("Mediated trace requires readonly existing stores")
    from .rna_trace_projection import _absolute, _source_identity
    stores = [Path(backend.methods.database), backend.ledger.path]
    initial = _source_identity(stores)
    campaign = _campaign(backend, campaign_id, workflow_id)
    rows = backend.ledger.campaign_reservations(campaign_id)
    if not rows or len(rows) > 3:
        raise ValueError("Mediated trace requires bounded retained parent reservations")
    artifacts = []
    for row in rows:
        if row["capture"] is not None:
            ref = row["capture"]["capture_ref"]
            artifact = backend.methods.get("artifact", ref["artifact_id"])
            if artifact["artifact_type"] != "rna_mediated_capture" or artifact["sha256"] != ref["sha256"]:
                raise ValueError("Mediated capture artifact identity differs")
            artifacts.append(_absolute(backend.methods.root / "objects" / artifact["sha256"]))
    observer = None
    if observer_path is not None:
        observer_path = _absolute(observer_path)
        if observer_path in stores or observer_path in artifacts:
            raise ValueError("Observer must be disjoint from authoritative stores")
    paths = stores + artifacts + ([observer_path] if observer_path is not None else [])
    if 4 * sum(_absolute(path).stat().st_size for path in paths) > 64 * 1024 * 1024:
        raise ValueError("Mediated evidence exceeds aggregate read bound")
    before = _source_identity(paths)
    if (any(before[key] != value for key, value in initial.items())
            or 4 * sum(value[0] for value in before.values()) > 64 * 1024 * 1024):
        raise ValueError("Mediated evidence changed or exceeds aggregate read bound")
    if observer_path is not None:
        observer = LocalTraceObserver.open_existing(observer_path)
    captures = {}
    for row in rows:
        reservation = backend.ledger._reservation(row, campaign)
        _record(backend, reservation, campaign)
        captures[row["request_digest"]] = _capture(backend, reservation, row)
    if len(captures) != len(rows):
        raise ValueError("Mediated reservations require distinct exact invocation bindings")
    reservations_by_digest = {row["request_digest"]: row for row in rows}
    capability = campaign["capability"]
    expected = dict(source_digest=capability["source_digest"], runtime_digest=capability["runtime_digest"],
                    policy_digest=campaign["policy_digest"])
    binding = trace_digest(dict(workflow=workflow_id, campaign=campaign_id, identity=campaign["identity"],
                                policy=campaign["policy_digest"], capability=campaign["capability_digest"]))
    namespace = uuid.uuid5(uuid.NAMESPACE_OID, binding)
    uid = lambda key: str(uuid.uuid5(namespace, key))
    contexts, originals, events = [], [], []
    observer_incomplete = False
    if observer is not None:
        if observer.identities != expected:
            raise PermissionError("Observer runtime or policy differs from campaign")
        contexts, originals = observer.read_contexts(workflow_id), observer.read_events(workflow_id)
        roots = [row for row in contexts if row["parent_span_id"] is None]
        if (len(roots) != 1 or roots[0]["invocation_binding_digest"] != trace_digest(workflow_id)
                or roots[0]["role"] != "coordinator" or roots[0]["phase"] != "workflow"):
            raise ValueError("Mediated observer lacks an exact unique trial root")
        root, trace = roots[0]["span_id"], roots[0]["trace_id"]
        index = {row["span_id"]: row for row in contexts}
        for context in contexts:
            if (context["trace_id"] != trace or any(context[key] != value for key, value in expected.items())
                    or context["parent_span_id"] is not None and context["parent_span_id"] not in index
                    or context["role"] == "investigator"
                    or context["role"] == "lead" and context["invocation_binding_digest"] not in captures):
                raise ValueError("Observer topology lacks exact mediated campaign support")
            if context["role"] == "lead":
                row = reservations_by_digest[context["invocation_binding_digest"]]
                if (context["phase"] not in {row["purpose"], "selection"}
                        or context["attempt"] != row["attempt"] + 1):
                    raise ValueError("Observer model context differs from exact retained invocation")
        for original in originals:
            context = index.get(original["span_id"])
            if (context is None or context["trace_id"] != trace
                    or any(context[key] != value for key, value in expected.items())
                    or any(original[key] != context[key] for key in
                           ("trace_id", "parent_span_id", "role", "phase", "attempt"))
                    or original["bindings"].get("workflow") != trace_digest(workflow_id)
                    or original["bindings"].get("invocation") != context["invocation_binding_digest"]
                    or any(original["bindings"].get(key) != expected[key + "_digest"]
                           for key in ("source", "runtime", "policy"))):
                raise ValueError("Observer context or event binding differs")
            if original["role"] == "investigator":
                raise ValueError("Parent-only mediated traces cannot contain investigators")
            allowed_metrics = _empty_metrics()
            if original["phase"] == "selection":
                if context["invocation_binding_digest"] not in captures:
                    raise ValueError("Observed selection lacks a retained reservation")
                if original["capture_status"] == "verified":
                    if captures[context["invocation_binding_digest"]] is None:
                        raise ValueError("Observed verified capture lacks authoritative wire evidence")
                    allowed_metrics = _metrics(captures[context["invocation_binding_digest"]])
            if original["metrics"] != allowed_metrics:
                raise ValueError("Observer metrics lack exact retained capture support")
            normalized = deepcopy(original)
            normalized["bindings"]["workflow"] = binding
            events.append(validate_trace_event(normalized))
        represented = {row["span_id"] for row in events}
        for context in contexts:
            if context["span_id"] in represented:
                continue
            observer_incomplete = True
            # Retain contexts even when the optional writer lost all events.
            # Their lifecycle and clocks remain explicitly unknown.
            events.append(validate_trace_event(dict(format="devbench.rna-trace-event/1",
                event_id=uid("missing-context:" + context["span_id"]), trace_id=trace,
                span_id=context["span_id"], parent_span_id=context["parent_span_id"], sequence=0,
                attempt=context["attempt"], role=context["role"], phase=context["phase"], kind="gap",
                source_status="incomplete", dispatch_status="unknown", capture_status="unavailable",
                clock=dict(utc=None, monotonic_ns=None, process_alias=uid("process"), timing_basis="unavailable"),
                bindings=dict(workflow=binding, invocation=context["invocation_binding_digest"],
                    source=expected["source_digest"], runtime=expected["runtime_digest"], policy=expected["policy_digest"]),
                metrics=_empty_metrics(), reason_code="observer_failure", origin="retained_authoritative_evidence")))
    else:
        root, trace = uid("root"), uid("trace")

    def emit(key, parent, role, phase, status, *, invocation=None, capture=None, kind="verification", attempt=1):
        candidates = [row for row in contexts if row["role"] == role and row["phase"] == phase
                      and row["invocation_binding_digest"] == invocation and row["attempt"] == attempt]
        if len(candidates) > 1:
            raise ValueError("Ambiguous mediated observer invocation")
        span = root if parent is None else candidates[0]["span_id"] if candidates else uid(key)
        history = [row for row in events if row["span_id"] == span]
        if history:
            parent = history[-1]["parent_span_id"]
            if kind == "capture" and any(row["kind"] == "capture" for row in history):
                kind = "verification"
        refs = dict(workflow=binding, source=expected["source_digest"], runtime=expected["runtime_digest"],
                    policy=expected["policy_digest"])
        if history:
            # Bindings identify a span and remain immutable across revisions.
            refs = deepcopy(history[-1]["bindings"])
        elif invocation is not None:
            refs["invocation"] = invocation
        if capture is not None and not history:
            refs["capture"] = trace_digest(capture)
        # Authoritative reconstruction has no historical clock or measured duration.
        event = dict(format="devbench.rna-trace-event/1", event_id=uid("event:" + key), trace_id=trace,
            span_id=span, parent_span_id=parent, sequence=history[-1]["sequence"] + 1 if history else 0,
            attempt=history[-1]["attempt"] if history else attempt, role=role, phase=phase, kind=kind,
            source_status=status, dispatch_status="attempted" if capture else "unknown",
            capture_status="verified" if capture else "unavailable",
            clock=dict(utc=None, monotonic_ns=None, process_alias=uid("process"), timing_basis="unavailable"),
            bindings=refs, metrics=_metrics(capture), reason_code="uncertain" if status == "uncertain" else "none",
            origin="retained_authoritative_evidence")
        events.append(validate_trace_event(event))
        return span

    generation = [row for row in originals if row["role"] == "coordinator" and row["phase"] == "generate"
                  and row["bindings"].get("invocation") == campaign["identity"]["trial_binding_sha256"]
                  and row["kind"] in {"completion", "failure", "cancel"}]
    # A generation child and the call ledger cannot close the broader workflow.
    # Preserve the actual root history; only an actual root event can finish it.
    root_history = [row for row in events if row["span_id"] == root]
    status = root_history[-1]["source_status"] if root_history else "incomplete"
    if not root_history:
        emit("root", None, "coordinator", "workflow", "incomplete")
    for row in rows:
        invocation = row["request_digest"]
        parent = emit("reservation:" + row["ref"], root, "lead", row["purpose"], row["status"],
                      invocation=invocation, attempt=row["attempt"] + 1)
        capture = captures[invocation]
        emit("selection:" + row["ref"], parent, "lead", "selection", row["status"], invocation=invocation,
             capture=capture, kind="capture" if capture is not None else "failure", attempt=row["attempt"] + 1)
    unknown = sum(row["capture"] is None and row["status"] in {"active", "uncertain"}
                  or row["capture"] is not None and row["capture"]["usage"]["normalized_total_tokens"] is None for row in rows)
    attempts = {row["span_id"] for row in originals if row["phase"] == "selection" and row["kind"] == "dispatch"
                and row["dispatch_status"] == "attempted"}
    confirmed = sum(capture is not None and capture["launch_confirmed"] for capture in captures.values())
    gaps = ["observer_absent"] if observer is None else []
    if observer_incomplete:
        gaps.append("observer_failure")
    if not generation:
        gaps.append("result_missing")
    if observer is not None and len(attempts) != len(rows):
        gaps.append("capture_partial")
    if unknown:
        gaps.append("usage_unavailable")
    gaps.extend(row["reason_code"] for row in originals if row["kind"] == "gap")
    if before != _source_identity(paths) or campaign != _campaign(backend, campaign_id, workflow_id):
        raise ValueError("Mediated evidence or runtime changed during snapshot")
    return dict(format=SNAPSHOT_FORMAT, workflow_binding=binding, events=events,
        **({"observer_events": deepcopy(originals)} if originals else {}),
        capability=dict(backend_kind="openai-responses-mediated", qualification_status="mediated_qualified", admitted=True),
        allocation=0, actual_children=0, complete=not gaps and status == "completed" and
        {row["purpose"] for row in rows} == _STAGES, gaps=sorted(set(gaps)),
        ledger_accounting=dict(verified_spent_tokens=campaign["verified_spent_tokens"], verified_calls=campaign["verified_calls"],
            unresolved_count=campaign["unresolved_count"], unknown_usage_count=unknown,
            qualification=campaign["qualification"], policy_kind=campaign["policy"]["kind"]),
        submission_accounting=dict(ledger_accounted_reservations=len(rows),
            confirmed_provider_responses=confirmed, unknown_submission_reservations=sum(
                row["capture"] is None and row["status"] in {"active", "uncertain"} for row in rows),
            observed_post_attempts=len(attempts)))


def project_mediated_rna_trace(*, backend, campaign_id, workflow_id, observer_path=None,
                               allow_evidence_read, export_namespace, limits):
    """Project only a freshly verified typed mediated snapshot, never caller JSON."""
    snapshot = read_mediated_rna_trace_snapshot(backend=backend, campaign_id=campaign_id,
        workflow_id=workflow_id, observer_path=observer_path, allow_evidence_read=allow_evidence_read)
    from .rna_trace_projection import SNAPSHOT_FORMAT as native_format, project_rna_trace
    submission = snapshot.pop("submission_accounting")
    snapshot["format"] = native_format
    result = project_rna_trace(snapshot, export_namespace=export_namespace, limits=limits)
    result["submission_accounting"] = submission
    return result
