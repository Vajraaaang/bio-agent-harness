"""Prepare one tiny local arithmetic example for review; never contact LangSmith.

Only a verified ordinary installed wheel can supply the source/runtime identity.
The transient enabled session is used solely by the existing local sanitizer;
the retained packet and both proposals remain disabled and unapproved.
"""
import argparse
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import sys
import time
from uuid import UUID, uuid4, uuid5

from .contracts import canonical_json_bytes
from .langsmith_outbox import digest
from .rna_hosted_connection import connection_identity
from .rna_hosted_connection_contract import (
    CONNECTION_CAPS, CONNECTION_FIXTURE, TARGET, prepare_connection_packet,
)
from .rna_langsmith_live import EVENT_FIELDS, sanitize_live_event, validate_live_run
from .rna_langsmith_verify import validate_remote_proof


ASSIGNED_WORKSPACE = Path("/Users/vajraang/Documents/ChatGPT/bio-agent-harness")


def _absolute(value):
    path = Path(value)
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("Absolute normalized preparation path required")
    for component in (path, *path.parents):
        if component.is_symlink():
            raise ValueError("Symlink preparation path prohibited")
    return path


def _directory_fd(path):
    """Open every directory component without following symlinks."""
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    current = os.open("/", flags)
    try:
        for part in path.parts[1:]:
            following = os.open(part, flags, dir_fd=current)
            os.close(current)
            current = following
        return current
    except BaseException:
        os.close(current)
        raise


def _receipt(*, session, root, process_alias, sequence, utc, monotonic_ns):
    return dict(format="devbench.rna-trace-event/1",
        event_id=str(uuid5(UUID(session["workflow_alias"]), "local-sum-event:" + str(sequence))),
        trace_id=root, span_id=root, parent_span_id=None, sequence=sequence, attempt=1,
        role="coordinator", phase="workflow", kind="reservation" if sequence == 0 else "completion",
        source_status="active" if sequence == 0 else "completed",
        dispatch_status="not_dispatched", capture_status="verified",
        clock=dict(utc=utc, monotonic_ns=monotonic_ns, process_alias=process_alias,
                   timing_basis="observed"),
        bindings=dict(workflow=session["workflow_binding_digest"], source=session["source_digest"],
                      runtime=session["runtime_digest"], policy=session["policy_digest"]),
        metrics=dict(input_tokens=None, output_tokens=None, total_tokens=None,
                     elapsed_seconds=None, peak_rss_mb=None,
                     provider_cost=dict(value=None, currency=None, basis="unavailable", source_alias=None),
                     usage_basis="unavailable"),
        reason_code="none", origin="retained_authoritative_evidence")


def prepare_connection_test(output_path):
    """Compute sum([1, 2, 3]) locally and exclusively write a disabled review packet."""
    workspace = _absolute(ASSIGNED_WORKSPACE)
    output = _absolute(output_path)
    if output == workspace or not output.is_relative_to(workspace):
        raise ValueError("Preparation output must be inside the assigned workspace")
    if output.is_relative_to(Path(sys.prefix).absolute()):
        raise ValueError("Preparation output overlaps the installed runtime")
    if output.exists():
        raise ValueError("Fresh preparation output required")

    # No operator record, credential handle, transport or exporter is accepted.
    identity = connection_identity()
    workflow_alias = str(uuid4())
    root = str(uuid5(UUID(workflow_alias), "local-sum-root"))
    process_alias = str(uuid4())
    expires = (datetime.now(timezone.utc) + timedelta(minutes=20)).isoformat()
    session = dict(format="devbench.rna-live-trace-session/2", workflow_alias=workflow_alias,
        workflow_binding_digest=digest(canonical_json_bytes(dict(
            installed_identity=identity, fixture=CONNECTION_FIXTURE, workflow_alias=workflow_alias))),
        data_scope="synthetic", source_digest=identity["source_digest"],
        runtime_digest=identity["runtime_digest"], policy_digest=identity["policy_digest"],
        endpoint=TARGET["endpoint"], project=TARGET["project_name"],
        project_id=TARGET["project_id"], workspace_id=TARGET["workspace_id"],
        redaction_version="rna-allowlist/1", event_schema="devbench.rna-trace-event/1",
        allowed_event_fields=sorted(EVENT_FIELDS), max_spans=CONNECTION_CAPS["max_spans"],
        max_bytes=CONNECTION_CAPS["max_bytes"],
        max_delivery_attempts=CONNECTION_CAPS["max_delivery_attempts"],
        expires_at=expires, authorize_upload=True)

    # These are observed local execution clocks, not proposed future run times.
    start = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    start_ns = time.monotonic_ns()
    values = [1, 2, 3]
    local_result = sum(values)
    end_ns = time.monotonic_ns()
    end = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    if end < start:
        raise ValueError("Observed local clock moved backwards")
    if values != CONNECTION_FIXTURE["values"] or local_result != CONNECTION_FIXTURE["expected_sum"]:
        raise ValueError("Static synthetic fixture differs from actual local arithmetic")
    operations = []
    receipts = []
    for sequence, operation in enumerate(("start", "finish")):
        receipt = _receipt(session=session, root=root, process_alias=process_alias,
            sequence=sequence, utc=(start, end)[sequence], monotonic_ns=(start_ns, end_ns)[sequence])
        receipts.append(receipt)
        sanitized = sanitize_live_event(receipt, authorized_session_manifest=session,
            capability=dict(backend_kind="offline_recorded", qualification_status="unknown", admitted=False),
            allocation=0, actual_children=0, operation=operation,
            start_time=start if sequence else None)
        validate_live_run(sanitized["payload"], operation)
        validate_remote_proof(sanitized["payload"])
        operations.append(sanitized)
    session["authorize_upload"] = False
    packet = prepare_connection_packet(installed_identity=identity, target=TARGET,
        session=session, operations=operations, expires_at=expires,
        evidence_path=str(output / "remote-evidence"), now=datetime.now(timezone.utc))
    packet_bytes = canonical_json_bytes(packet)
    observation = dict(format="devbench.rna-connection-local-observation/1", values=values,
        result=local_result, requirement_source="assistant_selected_synthetic_fixture",
        start_time=start, end_time=end, timing_basis="observed_local_utc",
        scope="local_arithmetic_only", receipts=receipts)
    review = dict(format="devbench.rna-connection-preparation-review/1",
        status="prepared_unapproved", packet_path=str(output / "packet.json"),
        packet_sha256=digest(packet_bytes),
        local_observation_path=str(output / "local_observation.json"),
        installed_identity=identity, target=TARGET, limits=CONNECTION_CAPS,
        source_runtime_review=None, operator_approvals={},
        authorize_upload=False, authorize_remote_read=False,
        remote_execution="not_attempted", scientific_assessment="not_applicable_synthetic_arithmetic",
        requirement_source="assistant_selected_synthetic_fixture",
        operation_ids=[row["operation"]["operation_id"] for row in operations],
        proposed_routes=[
            dict(method="GET", path="/api/v1/sessions/" + TARGET["project_id"] + "?include_stats=false"),
            dict(method="POST", path="/api/v1/runs"),
            dict(method="PATCH", path="/api/v1/runs/" + root),
            dict(method="GET", path="/api/v1/runs/" + root)],
        expected_evidence=dict(proof_revisions=[0, 1], span_count=1, root_id=root,
            parent_run_id=None, terminal_source_status="completed", actual_remote_status="unverified",
            proofs=[validate_remote_proof(row["payload"]) for row in operations],
            workspace_binding="run.session_id -> authenticated_project.id -> authenticated_project.tenant_id",
            statuses=["transport_acknowledgment", "exact_remote_content", "api_visibility", "dashboard_visibility"],
            native_isolation="unverified", scientific_validity="not_assessed"),
        limitations=["No hosted request, credential lookup, model, fit, retrieval, or dataset execution occurred.",
            "Local arithmetic does not establish deployed schema compatibility, performance, or scientific validity.",
            "Independent source/runtime review and separate operator approvals are absent.",
            "Proposed routes and expected evidence are review material, not executable approval."])
    documents = {"packet.json": packet_bytes,
        "local_observation.json": canonical_json_bytes(observation),
        "review.json": canonical_json_bytes(review)}
    if any(len(raw) > 32768 for raw in documents.values()):
        raise ValueError("Bounded preparation record required")
    parent_fd = _directory_fd(output.parent)
    try:
        os.mkdir(output.name, mode=0o700, dir_fd=parent_fd)
        directory_fd = os.open(output.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)
        try:
            os.fchmod(directory_fd, 0o700)
            for name, raw in documents.items():
                fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                             0o600, dir_fd=directory_fd)
                with os.fdopen(fd, "wb") as stream:
                    os.fchmod(stream.fileno(), 0o600)
                    stream.write(raw)
                    stream.flush()
                    os.fsync(stream.fileno())
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        os.close(parent_fd)
    return output / "review.json"


def build_parser():
    parser = argparse.ArgumentParser(description="Prepare one local synthetic connection review; no hosted requests.")
    parser.add_argument("--output", required=True, type=Path,
                        help="Fresh private output folder within the assigned workspace")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    review = prepare_connection_test(args.output)
    print(str(review))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
