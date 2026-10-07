"""Explicit RNA evidence preview and separately authorized export transport.

No workflow, participant, numerical worker, or grader is imported here.
"""
import os
import math
import re
from uuid import UUID, uuid4

from .contracts import canonical_json_bytes
from .langsmith_export import ENDPOINTS, MAX_FILE, _Evidence, _absolute, _hash, _post_run, _time, _write_new

FORMAT = "devbench.rna-langsmith-export/1"
POLICY = "rna-allowlist/1"
DISPLAY_ANCHOR = "2000-01-01T00:00:00+00:00"


def _project(value):
    if not isinstance(value, str) or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,99}", value) is None:
        raise ValueError("Project must be a bounded operator-supplied identifier")
    return value


def validate_rna_run(run, scope, *, live_operation=None):
    """Validate a single run; its caller separately proves retained parent binding."""
    from devbench.analysis.rna_trace_projection import validate_projected_span
    if scope not in {"synthetic", "public_processed"} or live_operation not in {None, "start", "finish", "annotation"}:
        raise ValueError("Unsupported RNA run scope or operation")
    if not isinstance(run, dict):
        raise ValueError("Invalid RNA run")
    projected = {key: value for key, value in run.items()
                 if key not in {"session_name", "session_id", "tags", "start_time", "end_time"}}
    extra = projected.get("extra")
    if not isinstance(extra, dict) or set(extra) != {"metadata"} or not isinstance(extra["metadata"], dict):
        raise ValueError("Invalid RNA metadata")
    metadata = dict(extra["metadata"])
    proof = metadata.pop("remote_proof", None)
    if "session_id" in run or proof is not None:
        if live_operation is None:
            raise ValueError("Versioned remote proof requires bound live operation")
        from .rna_langsmith_verify import validate_remote_proof
        validate_remote_proof(run)
    timing = metadata.pop("transport_timing_basis", None)
    if live_operation is None and timing != "fixed_display_anchor_only":
        raise ValueError("RNA transport timing must be explicitly labeled as display only")
    if live_operation is not None and timing not in {None, "local_receipt_clock", "fixed_display_anchor_only"}:
        raise ValueError("Unsupported live transport timing basis")
    projected["extra"] = {"metadata": metadata}
    validate_projected_span(projected)
    _project(run.get("session_name"))
    if run.get("tags") != ["devbench", "rna", scope]:
        raise ValueError("RNA tags violate redaction policy")
    if _time(run.get("start_time")) is None:
        raise ValueError("RNA display timestamp must include timezone")
    if live_operation == "start":
        if "end_time" in run:
            raise ValueError("An unfinished start cannot declare an end")
    elif live_operation == "annotation" and "end_time" not in run:
        # An annotation retains the actual wire vector and proves no completion.
        # A synthetic end time would invalidate its versioned semantic proof.
        pass
    elif _time(run.get("end_time")) is None or _time(run["end_time"]) < _time(run["start_time"]):
        raise ValueError("Invalid RNA end timestamp")
    if timing == "fixed_display_anchor_only" and any(run.get(key) != DISPLAY_ANCHOR for key in ("start_time", "end_time") if key in run):
        raise ValueError("Fixed display anchors cannot declare measured timestamps")
    return run


def validate_rna_payload(payload, max_spans=2000, *, live_operation=None):
    """Validate every server-visible field again before delivery."""
    if type(max_spans) is not int or not 1 <= max_spans <= 2000:
        raise ValueError("Invalid RNA span budget")
    if (not isinstance(payload, dict) or set(payload) != {"record_format", "data_scope", "privacy_policy", "runs"}
            or payload["record_format"] != FORMAT or payload["privacy_policy"] != POLICY
            or payload["data_scope"] not in {"synthetic", "public_processed"}):
        raise ValueError("Unsupported RNA redacted payload")
    if live_operation not in {None, "start", "finish", "annotation"}:
        raise ValueError("Unsupported delivery operation")
    runs = payload["runs"]
    if not isinstance(runs, list) or not 1 <= len(runs) <= max_spans:
        raise ValueError("RNA payload exceeds span budget")
    identities = set()
    projects = set()
    traces = set()
    roots = 0
    for run in runs:
        validate_rna_run(run, payload["data_scope"], live_operation=live_operation)
        projects.add(_project(run.get("session_name")))
        traces.add(run["trace_id"])
        identity = str(UUID(run["id"]))
        parent = run.get("parent_run_id")
        roots += parent is None
        if identity in identities or (parent is not None and parent not in identities):
            raise ValueError("RNA parents must precede unique children")
        identities.add(identity)
    if roots != 1 or len(traces) != 1:
        raise ValueError("RNA payload requires one reachable workflow root and trace")
    if len(projects) != 1 or len(canonical_json_bytes(payload)) > MAX_FILE:
        raise ValueError("RNA payload project or byte budget mismatch")
    return payload


def prepare_rna_export(*, snapshot_path, output_dir, data_scope, project,
                       expected_snapshot_sha256, allow_evidence_read=False,
                       max_spans=2000, export_namespace=None):
    """Write a fresh, hash-bound preview without credentials or network access."""
    if allow_evidence_read is not True:
        raise PermissionError("RNA preview requires explicit evidence-read authorization")
    if data_scope not in {"synthetic", "public_processed"}:
        raise ValueError("Unsupported RNA evidence scope")
    project = _project(project)
    if not isinstance(expected_snapshot_sha256, str) or re.fullmatch(r"[a-f0-9]{64}", expected_snapshot_sha256) is None:
        raise ValueError("An exact snapshot SHA-256 is required")
    if type(max_spans) is not int or not 1 <= max_spans <= 2000:
        raise ValueError("Invalid RNA span budget")
    namespace = str(UUID(export_namespace)) if export_namespace is not None else None
    source, output = _absolute(snapshot_path), _absolute(output_dir)
    if output.exists() or output.is_relative_to(source.parent) or source.parent.is_relative_to(output):
        raise ValueError("RNA preview output must be fresh and disjoint from evidence")
    reader = _Evidence(source.parent)
    snapshot = reader.read(source)
    if reader.digest(source) != expected_snapshot_sha256:
        raise ValueError("Snapshot differs from the exact reviewed SHA-256")
    from devbench.analysis.rna_trace_projection import project_rna_trace, _validate_snapshot
    _validate_snapshot(snapshot)
    if namespace is None and snapshot.get("observer_events"):
        from devbench.analysis.rna_trace_contract import validate_trace_event
        events = [validate_trace_event(event) for event in snapshot["events"]]
        roots = {(event["span_id"], event["trace_id"]) for event in events
                 if event["parent_span_id"] is None}
        if len(roots) != 1:
            raise ValueError("Observer preview requires one retained workflow root")
        namespace = next(iter(roots))[1]
    namespace = namespace or str(uuid4())
    projection = project_rna_trace(snapshot, export_namespace=namespace,
                                   limits={"max_spans": max_spans, "max_bytes": MAX_FILE})
    runs = []
    for span in projection["spans"]:
        run = dict(span)
        run["extra"] = {"metadata": {**span["extra"]["metadata"],
                                       "transport_timing_basis": "fixed_display_anchor_only"}}
        run.update(session_name=project, tags=["devbench", "rna", data_scope],
                   start_time=DISPLAY_ANCHOR, end_time=DISPLAY_ANCHOR)
        runs.append(run)
    payload = validate_rna_payload({"record_format": FORMAT, "data_scope": data_scope,
                                   "privacy_policy": POLICY, "runs": runs}, max_spans)
    reader.unchanged()
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    digest = _write_new(output / "payload.json", payload)
    evidence_hash = _write_new(output / "local-evidence.json", {
        "export_namespace": namespace, "payload_sha256": digest, "source_files": reader.files,
        "references": projection["local_evidence"], "accounting": projection["accounting"],
        "uploaded": False})
    manifest = {"record_format": "devbench.rna-export-manifest/1", "status": "prepared",
                "snapshot_sha256": expected_snapshot_sha256, "payload_sha256": digest,
                "local_evidence_sha256": evidence_hash, "export_namespace": namespace,
                "spans": len(runs), "complete": projection["complete"], "uploaded": False,
                "timing": "fixed_display_anchor_only"}
    manifest_hash = _write_new(output / "export-manifest.json", manifest)
    return {**manifest, "payload_path": str(output / "payload.json"),
            "local_evidence_path": str(output / "local-evidence.json"),
            "manifest_path": str(output / "export-manifest.json"), "manifest_sha256": manifest_hash}


def deliver_rna_outbox(*, outbox_path, expected_sha256, allow_upload=False,
                       timeout_seconds=15, deadline_seconds=60):
    """Credentials are accessed only after immutable outbox validation."""
    if allow_upload is not True:
        raise PermissionError("RNA delivery requires explicit --authorize-upload")
    if not isinstance(expected_sha256, str) or re.fullmatch(r"[a-f0-9]{64}", expected_sha256) is None:
        raise ValueError("An exact payload SHA-256 is required")
    for value in (timeout_seconds, deadline_seconds):
        if type(value) not in (int, float) or not math.isfinite(value) or not 0 < value <= 60:
            raise ValueError("Delivery bounds must be finite and between zero and 60 seconds")
    from .langsmith_outbox import ExportOutbox
    outbox = ExportOutbox.open_readonly(outbox_path)
    status = outbox.status()
    if status["payload_sha256"] != expected_sha256:
        raise ValueError("Outbox differs from the exact reviewed payload SHA-256")
    endpoint = status["endpoint"]
    if endpoint not in ENDPOINTS:
        raise ValueError("Unsupported HTTPS LangSmith endpoint")
    key = os.environ.get("LANGSMITH_API_KEY")
    if not key:
        raise RuntimeError("LANGSMITH_API_KEY is required for separately authorized delivery")
    workspace = os.environ.get("LANGSMITH_WORKSPACE_ID")
    if workspace:
        workspace = str(UUID(workspace))

    def transport(*, method, path, payload, timeout):
        if method != "POST" or path != "/runs":
            raise ValueError("Only post-run delivery is supported by this command")
        _post_run(endpoint, key, workspace, payload, timeout)
        return {"status_code": 200}

    return ExportOutbox.open(outbox_path).deliver(transport=transport, allow_upload=True,
        timeout_seconds=timeout_seconds, deadline_seconds=deadline_seconds)
