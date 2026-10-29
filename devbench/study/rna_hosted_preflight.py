"""Disabled, exact-target proposals for the three distinct hosted pilot actions.

No proposal or local operator record enables transport. Record hashes establish
integrity only, not authenticated approval. Deployment/schema qualification and
an actual reviewed exporter-only credential channel remain unavailable.
"""
from datetime import datetime
import re
from uuid import uuid4

from .contracts import canonical_json_bytes
from .langsmith_export import ENDPOINTS, _time
from .langsmith_outbox import _digest, _uuid, digest
from .rna_hosted_trace_contract import _closed, _copy, absolute_path, validate_limits

BINDING_KEYS = frozenset({"source_digest", "runtime_digest", "policy_digest",
    "criteria_digest", "fixture_manifest_digest", "subject_digest"})
TARGET_KEYS = frozenset({"endpoint", "workspace_id", "project_id", "project_name"})
PROPOSAL_KEYS = frozenset({"format", "proposal_id", "action", "data_scope",
    "binding", "target", "run_ids", "allowed_operations", "limits",
    "max_response_bytes", "expires_at", "evidence_path", "authorize_upload",
    "authorize_remote_read", "privacy_policy", "ambiguous_acceptance_policy"})
APPROVAL_KEYS = frozenset({"format", "proposal_digest", "action", "approved",
    "operator_id", "implementation_owner_id", "expires_at"})
ACTION_OPERATIONS = {
    "target_read": ["read_run", "query_runs"],
    "post_run_payload": ["create_run"],
    "live_session": ["create_run", "update_run"],
}


def _expiry(value, now):
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("Aware comparison clock required")
    expires = _time(value)
    if expires is None or not 0 < (expires - now).total_seconds() <= 1800:
        raise ValueError("Expired or overlong proposal/approval")
    return expires


def _binding(value):
    _closed(value, BINDING_KEYS, "hosted action identity")
    for item in value.values():
        _digest(item)


def _target(value):
    _closed(value, TARGET_KEYS, "existing hosted target")
    if not isinstance(value["endpoint"], str) or value["endpoint"] not in ENDPOINTS:
        raise ValueError("Unapproved endpoint")
    _uuid(value["workspace_id"])
    _uuid(value["project_id"])
    name = value["project_name"]
    if not isinstance(name, str) or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,99}", name) is None:
        raise ValueError("Bounded existing project name required")


def prepare_hosted_action(*, action, binding, target, run_ids, limits,
                          expires_at, evidence_path, now):
    """Prepare an inspectable proposal without network, secrets or filesystem writes.

    ``subject_digest`` binds the exact sanitized payload, live session descriptor,
    or read descriptor. Targets must be supplied explicitly; no project discovery
    or trial/campaign authorization is inferred. Each action needs its own record.
    """
    if not isinstance(action, str) or action not in ACTION_OPERATIONS:
        raise ValueError("Distinct hosted pilot action required")
    proposal = dict(format="devbench.rna-hosted-action-proposal/1",
        proposal_id=str(uuid4()), action=action, data_scope="synthetic",
        binding=_copy(binding), target=_copy(target), run_ids=_copy(run_ids),
        allowed_operations=ACTION_OPERATIONS[action].copy(), limits=_copy(limits),
        max_response_bytes=262144, expires_at=expires_at, evidence_path=str(evidence_path),
        authorize_upload=False, authorize_remote_read=False,
        privacy_policy="rna-allowlist/1",
        ambiguous_acceptance_policy="retain_uncertainty_until_exact_revision_proof")
    return validate_hosted_action(proposal, expected_binding=binding,
                                  expected_target=target, now=now)


def validate_hosted_action(proposal, *, expected_binding, expected_target, now):
    """Recheck a proposal against separately held exact identity and target."""
    _closed(proposal, PROPOSAL_KEYS, "disabled hosted action proposal")
    if proposal["format"] != "devbench.rna-hosted-action-proposal/1" or proposal["data_scope"] != "synthetic":
        raise ValueError("Synthetic hosted proposal required")
    _uuid(proposal["proposal_id"])
    action = proposal["action"]
    if not isinstance(action, str) or action not in ACTION_OPERATIONS:
        raise ValueError("Distinct hosted pilot action required")
    if proposal["allowed_operations"] != ACTION_OPERATIONS[action]:
        raise ValueError("Exact action operation allowlist required")
    if proposal["authorize_upload"] is not False or proposal["authorize_remote_read"] is not False:
        raise PermissionError("Proposals cannot enable hosted actions")
    for binding in (proposal["binding"], expected_binding):
        _binding(binding)
    if proposal["binding"] != expected_binding:
        raise ValueError("Source/runtime/policy/criteria/fixture/subject identity mismatch")
    for target in (proposal["target"], expected_target):
        _target(target)
    if proposal["target"] != expected_target:
        raise ValueError("Hosted target mismatch")
    limits = validate_limits(proposal["limits"])
    ids = proposal["run_ids"]
    if not isinstance(ids, list) or not 1 <= len(ids) <= limits["max_spans"]:
        raise ValueError("Exact bounded run IDs required")
    for run_id in ids:
        _uuid(run_id)
    if len(set(ids)) != len(ids):
        raise ValueError("Duplicate run ID")
    if action == "target_read" and len(ids) != 1:
        raise ValueError("One exact bounded target read required")
    if type(proposal["max_response_bytes"]) is not int or not 1 <= proposal["max_response_bytes"] <= 262144:
        raise ValueError("Bounded sanitized response required")
    _expiry(proposal["expires_at"], now)
    if not isinstance(proposal["evidence_path"], str) or len(proposal["evidence_path"]) > 1024:
        raise ValueError("Bounded absolute evidence path required")
    absolute_path(proposal["evidence_path"])
    if proposal["privacy_policy"] != "rna-allowlist/1" or proposal["ambiguous_acceptance_policy"] != "retain_uncertainty_until_exact_revision_proof":
        raise ValueError("Privacy and ambiguous acceptance safeguards required")
    if len(canonical_json_bytes(proposal)) > 32768:
        raise ValueError("Bounded hosted action proposal required")
    return _copy(proposal)


def validate_operator_approval(record, *, proposal, expected_binding, expected_target, now):
    """Validate local record integrity; this is not operator authentication.

    A payload approval cannot stand in for the read or live-session record.
    The exact record is retained separately from the disabled proposal.
    """
    validate_hosted_action(proposal, expected_binding=expected_binding,
                           expected_target=expected_target, now=now)
    _closed(record, APPROVAL_KEYS, "separate hosted action operator record")
    if record["format"] != "devbench.rna-hosted-action-approval/1" or record["approved"] is not True:
        raise ValueError("Explicit action-specific operator record required")
    _digest(record["proposal_digest"])
    if record["action"] != proposal["action"] or record["proposal_digest"] != digest(canonical_json_bytes(proposal)):
        raise ValueError("Approval does not bind this exact action proposal")
    _uuid(record["operator_id"])
    _uuid(record["implementation_owner_id"])
    if record["operator_id"] == record["implementation_owner_id"]:
        raise PermissionError("Implementation owner cannot self-approve")
    if _expiry(record["expires_at"], now) > _time(proposal["expires_at"]):
        raise ValueError("Approval outlives exact proposal")
    return digest(canonical_json_bytes(record))


def hosted_action_readiness(proposal, *, expected_binding, expected_target, now,
                            approval_record=None):
    """Return bound blockers, never execution authority or a hosted gate pass."""
    validate_hosted_action(proposal, expected_binding=expected_binding,
                           expected_target=expected_target, now=now)
    approval_digest = None
    blockers = ["unsupported_schema", "exporter_only_credential_channel_unavailable",
                "independent_source_runtime_gate_unverified",
                "authenticated_operator_approval_unverified"]
    if approval_record is None:
        blockers.append("separate_action_approval_absent")
    else:
        approval_digest = validate_operator_approval(approval_record, proposal=proposal,
            expected_binding=expected_binding, expected_target=expected_target, now=now)
    if proposal["action"] != "target_read":
        blockers.append("separate_bounded_read_attestation_unverified")
    return dict(format="devbench.rna-hosted-action-readiness/1", status="blocked",
        action=proposal["action"], proposal_digest=digest(canonical_json_bytes(proposal)),
        approval_record_digest=approval_digest, authorize_upload=False,
        authorize_remote_read=False, blockers=blockers)
