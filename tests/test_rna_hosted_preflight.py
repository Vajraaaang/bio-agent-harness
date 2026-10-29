"""Adversarial exact-action preparation; no hosted transport can be enabled."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import os
import socket
from uuid import uuid4

import pytest

from devbench.study import rna_hosted_preflight as preflight
from devbench.study.contracts import canonical_json_bytes
from devbench.study.langsmith_outbox import digest


@pytest.fixture
def scope(tmp_path):
    now = datetime(2026, 10, 7, 12, tzinfo=timezone.utc)
    binding = {key: str(index) * 64 for index, key in enumerate(sorted(preflight.BINDING_KEYS), 1)}
    target = dict(endpoint="https://api.smith.langchain.com", workspace_id=str(uuid4()),
                  project_id=str(uuid4()), project_name="existing-pilot")
    limits = dict(max_spans=64, max_bytes=262144, max_mutation_requests=64,
                  max_read_requests=64, max_delivery_attempts=3, total_deadline_seconds=90)
    return dict(binding=binding, target=target, run_ids=[str(uuid4())], limits=limits,
                expires_at=(now + timedelta(minutes=5)).isoformat(),
                evidence_path=str(tmp_path / "new-attempt"), now=now)


def prepare(scope, action="target_read"):
    return preflight.prepare_hosted_action(action=action, **scope)


def check(proposal, scope):
    return preflight.validate_hosted_action(proposal, expected_binding=scope["binding"],
                                           expected_target=scope["target"], now=scope["now"])


def approval(proposal, scope):
    return dict(format="devbench.rna-hosted-action-approval/1",
                proposal_digest=digest(canonical_json_bytes(proposal)), action=proposal["action"],
                approved=True, operator_id=str(uuid4()), implementation_owner_id=str(uuid4()),
                expires_at=scope["expires_at"])


@pytest.mark.parametrize("action", list(preflight.ACTION_OPERATIONS))
def test_distinct_actions_are_disabled_fresh_copies(scope, action):
    proposal = prepare(scope, action)
    assert proposal["authorize_upload"] is False
    assert proposal["authorize_remote_read"] is False
    assert check(proposal, scope) == proposal
    assert proposal["binding"] is not scope["binding"]
    assert proposal["limits"] is not scope["limits"]
    assert not os.path.exists(scope["evidence_path"])


@pytest.mark.parametrize("action", list(preflight.ACTION_OPERATIONS))
def test_local_record_cannot_promote_hosted_readiness(scope, action):
    proposal = prepare(scope, action)
    record = approval(proposal, scope)
    result = preflight.hosted_action_readiness(proposal, approval_record=record,
        expected_binding=scope["binding"], expected_target=scope["target"], now=scope["now"])
    assert result["status"] == "blocked"
    assert "unsupported_schema" in result["blockers"]
    assert "exporter_only_credential_channel_unavailable" in result["blockers"]
    assert "authenticated_operator_approval_unverified" in result["blockers"]
    assert result["authorize_upload"] is False
    assert result["authorize_remote_read"] is False
    assert result["approval_record_digest"] == digest(canonical_json_bytes(record))


@pytest.mark.parametrize("source_action", list(preflight.ACTION_OPERATIONS))
@pytest.mark.parametrize("destination_action", list(preflight.ACTION_OPERATIONS))
def test_approval_cannot_be_swapped_between_actions_or_same_action_instances(scope, source_action, destination_action):
    source = prepare(scope, source_action)
    destination = prepare(scope, destination_action)
    with pytest.raises(ValueError, match="exact action"):
        preflight.validate_operator_approval(approval(source, scope), proposal=destination,
            expected_binding=scope["binding"], expected_target=scope["target"], now=scope["now"])


@pytest.mark.parametrize("key", sorted(preflight.BINDING_KEYS))
def test_source_runtime_policy_criteria_fixture_and_subject_drift_rejected(scope, key):
    proposal = prepare(scope)
    proposal["binding"][key] = "f" * 64
    with pytest.raises(ValueError, match="identity mismatch"):
        check(proposal, scope)


@pytest.mark.parametrize("key,bad", [("endpoint", "https://eu.api.smith.langchain.com"),
    ("workspace_id", str(uuid4())), ("project_id", str(uuid4())), ("project_name", "renamed")])
def test_target_region_workspace_project_and_rename_drift_rejected(scope, key, bad):
    proposal = prepare(scope)
    proposal["target"][key] = bad
    with pytest.raises(ValueError, match="target mismatch"):
        check(proposal, scope)


@pytest.mark.parametrize("field,bad", [("authorize_upload", True), ("authorize_remote_read", True),
    ("authorize_upload", 0), ("authorize_remote_read", 0), ("api_key", "CANARY"),
    ("allowed_operations", ["list_projects"]), ("allowed_operations", ["create_run"]),
    ("max_response_bytes", 262145), ("max_response_bytes", True),
    ("privacy_policy", "all_text"), ("ambiguous_acceptance_policy", "ack_on_http_200"),
    ("data_scope", "clinical"), ("action", "campaign"), ("action", []),
    ("run_ids", []), ("run_ids", ["not-a-uuid"]), ("evidence_path", "relative"),
    ("evidence_path", None)])
def test_closed_proposal_rejects_widening_secrets_and_invalid_caps(scope, field, bad):
    proposal = prepare(scope)
    proposal[field] = bad
    with pytest.raises((ValueError, PermissionError)):
        check(proposal, scope)


@pytest.mark.parametrize("minutes", [-1, 0, 31])
def test_expired_and_overlong_proposals_rejected(scope, minutes):
    proposal = prepare(scope)
    proposal["expires_at"] = (scope["now"] + timedelta(minutes=minutes)).isoformat()
    with pytest.raises(ValueError, match="Expired or overlong"):
        check(proposal, scope)


def test_naive_clock_and_naive_expiry_rejected(scope):
    candidate = {**scope, "now": scope["now"].replace(tzinfo=None)}
    with pytest.raises(ValueError, match="Aware comparison"):
        prepare(candidate)
    candidate = {**scope, "expires_at": "2026-10-07T12:05:00"}
    with pytest.raises(ValueError, match="Expired or overlong"):
        prepare(candidate)


@pytest.mark.parametrize("field,bad", [("endpoint", []), ("endpoint", "http://api.smith.langchain.com"),
    ("endpoint", "https://proxy.example"), ("workspace_id", None), ("project_id", "not-a-uuid"),
    ("project_name", "existing/pilot"), ("api_key", "CANARY")])
def test_target_cannot_accept_proxy_unknown_endpoint_secrets_or_missing_identity(scope, field, bad):
    candidate = deepcopy(scope)
    candidate["target"][field] = bad
    with pytest.raises(ValueError):
        prepare(candidate)


@pytest.mark.parametrize("field,bad", [("max_spans", 2001), ("max_bytes", 8_000_001),
    ("max_mutation_requests", True), ("max_read_requests", 2401),
    ("max_delivery_attempts", 4), ("total_deadline_seconds", float("nan")),
    ("total_deadline_seconds", 901), ("extra", 1)])
def test_limits_cannot_be_widened_or_nonfinite(scope, field, bad):
    candidate = deepcopy(scope)
    candidate["limits"][field] = bad
    with pytest.raises(ValueError):
        prepare(candidate)


@pytest.mark.parametrize("field,bad", [("approved", False), ("approved", 1),
    ("operator_id", "person"), ("proposal_digest", "f" * 64),
    ("expires_at", "2026-10-07T12:00:00+00:00"),
    ("expires_at", "2026-10-07T12:06:00+00:00"), ("api_key", "CANARY")])
def test_operator_record_is_closed_expiring_and_exact(scope, field, bad):
    proposal = prepare(scope)
    record = approval(proposal, scope)
    record[field] = bad
    with pytest.raises(ValueError):
        preflight.validate_operator_approval(record, proposal=proposal,
            expected_binding=scope["binding"], expected_target=scope["target"], now=scope["now"])


def test_implementation_owner_cannot_self_approve(scope):
    proposal = prepare(scope)
    record = approval(proposal, scope)
    record["operator_id"] = record["implementation_owner_id"]
    with pytest.raises(PermissionError, match="self-approve"):
        preflight.validate_operator_approval(record, proposal=proposal,
            expected_binding=scope["binding"], expected_target=scope["target"], now=scope["now"])


def test_hash_of_full_proposal_binds_caps_reads_evidence_and_expiry(scope):
    proposal = prepare(scope)
    record = approval(proposal, scope)
    for field, bad in [("max_response_bytes", 128), ("run_ids", [str(uuid4())]),
                       ("evidence_path", scope["evidence_path"] + "-other"),
                       ("expires_at", "2026-10-07T12:04:00+00:00")]:
        changed = deepcopy(proposal)
        changed[field] = bad
        with pytest.raises(ValueError, match="exact action"):
            preflight.validate_operator_approval(record, proposal=changed,
                expected_binding=scope["binding"], expected_target=scope["target"], now=scope["now"])
    changed = deepcopy(proposal)
    changed["limits"]["max_read_requests"] = 1
    with pytest.raises(ValueError, match="exact action"):
        preflight.validate_operator_approval(record, proposal=changed,
            expected_binding=scope["binding"], expected_target=scope["target"], now=scope["now"])


def test_duplicates_and_unbounded_target_read_rejected(scope):
    run_id = scope["run_ids"][0]
    with pytest.raises(ValueError, match="Duplicate"):
        prepare({**scope, "run_ids": [run_id, run_id]}, "live_session")
    with pytest.raises(ValueError, match="One exact"):
        prepare({**scope, "run_ids": [run_id, str(uuid4())]})
    with pytest.raises(ValueError, match="bounded run IDs"):
        prepare({**scope, "run_ids": [str(uuid4()) for _ in range(65)]}, "live_session")


def test_symlink_evidence_path_rejected(scope, tmp_path):
    (tmp_path / "alias").symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ValueError, match="Symlink"):
        prepare({**scope, "evidence_path": str(tmp_path / "alias" / "new")})


def test_preparation_and_readiness_have_zero_network_or_secret_access(scope, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Preflight attempted network or environment/secret access")
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(os, "getenv", forbidden)
    monkeypatch.setattr(type(os.environ), "__getitem__", forbidden)
    for action in preflight.ACTION_OPERATIONS:
        proposal = prepare(scope, action)
        result = preflight.hosted_action_readiness(proposal,
            expected_binding=scope["binding"], expected_target=scope["target"], now=scope["now"])
        assert result["status"] == "blocked"
        assert result["approval_record_digest"] is None
        assert "separate_action_approval_absent" in result["blockers"]
