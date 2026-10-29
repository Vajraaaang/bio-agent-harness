"""Offline exact connection preparation and trusted-launcher admission contracts."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import importlib
import json
import os
from pathlib import Path
from uuid import uuid4

import pytest

from devbench.study.contracts import canonical_json_bytes
from devbench.study.langsmith_outbox import digest
from devbench.study.rna_langsmith_live import EVENT_FIELDS, sanitize_live_event
from tests.test_rna_trace_contract import event


def _api():
    name = "devbench.study.rna_hosted_connection_contract"
    assert importlib.util.find_spec(name) is not None, "Connection contract feature is absent"
    return importlib.import_module(name)


def make_scope(tmp_path, contract):
    now = datetime.now(timezone.utc)
    identity = dict(source_digest="a" * 64, runtime_digest="b" * 64,
        policy_digest="c" * 64,
        criteria_digest=digest(canonical_json_bytes(contract.CONNECTION_CRITERIA)),
        fixture_manifest_digest=digest(canonical_json_bytes(contract.CONNECTION_FIXTURE)))
    expires = (now + timedelta(minutes=5)).isoformat()
    session = dict(format="devbench.rna-live-trace-session/2", workflow_alias=str(uuid4()),
        workflow_binding_digest="d" * 64, data_scope="synthetic",
        **{key: identity[key] for key in ("source_digest", "runtime_digest", "policy_digest")},
        endpoint=contract.TARGET["endpoint"], project=contract.TARGET["project_name"],
        project_id=contract.TARGET["project_id"], workspace_id=contract.TARGET["workspace_id"],
        redaction_version="rna-allowlist/1", event_schema="devbench.rna-trace-event/1",
        allowed_event_fields=sorted(EVENT_FIELDS), max_spans=1, max_bytes=32768,
        max_delivery_attempts=1, expires_at=expires, authorize_upload=True)
    root = str(uuid4())
    operations = []
    for sequence, operation in enumerate(("start", "finish")):
        observed = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        receipt = event(event_id=str(uuid4()), trace_id=root, span_id=root,
            sequence=sequence, kind="reservation" if not sequence else "completion",
            source_status="active" if not sequence else "completed",
            dispatch_status="prepared" if not sequence else "attempted",
            clock=dict(utc=observed, monotonic_ns=sequence, process_alias=str(uuid4()),
                       timing_basis="observed"),
            bindings=dict(workflow=session["workflow_binding_digest"],
                source=identity["source_digest"], runtime=identity["runtime_digest"],
                policy=identity["policy_digest"]))
        operations.append(sanitize_live_event(receipt, authorized_session_manifest=session,
            capability=dict(backend_kind="offline_recorded", qualification_status="offline_qualified",
                            admitted=True), allocation=0, actual_children=0,
            operation=operation, start_time=operations[0]["payload"]["start_time"] if sequence else None))
    session["authorize_upload"] = False
    return dict(installed_identity=identity, target=deepcopy(contract.TARGET), session=session,
        operations=operations, expires_at=expires, evidence_path=str(tmp_path / "evidence"),
        now=datetime.now(timezone.utc))


def checked(contract, packet, scope, **changes):
    kwargs = dict(installed_identity=scope["installed_identity"],
        expected_binding={**scope["installed_identity"],
            "session_digest": digest(canonical_json_bytes(scope["session"])),
            "payload_digest": digest(canonical_json_bytes(scope["operations"]))},
        expected_target=scope["target"], now=scope["now"])
    kwargs.update(changes)
    return contract.validate_connection_packet(packet, **kwargs)


def handles(contract, packet, scope, directory):
    owner, reviewer = str(uuid4()), str(uuid4())
    records = {"source_runtime_review": dict(format="devbench.rna-connection-review/2",
        packet_digest=contract.packet_digest(packet), binding=packet["binding"],
        target=packet["target"], expires_at=packet["expires_at"], approved=True,
        reviewer_id=reviewer, implementation_owner_id=owner)}
    for action in ("target_read", "post_run_payload"):
        records[action] = dict(format="devbench.rna-hosted-action-approval/2",
            proposal_digest=digest(canonical_json_bytes(packet[action])),
            action=action, approved=True, operator_id=reviewer,
            implementation_owner_id=owner, expires_at=scope["expires_at"])
    result = {}
    for action, record in records.items():
        path = directory / (action + ".json")
        path.write_bytes(canonical_json_bytes(record))
        path.chmod(0o600)
        result[action] = dict(path=str(path), sha256=digest(path.read_bytes()))
    return result


def admitted(contract, packet, scope, approval_handles, **changes):
    kwargs = dict(installed_identity=scope["installed_identity"], expected_binding=packet["binding"],
                  expected_target=scope["target"], now=scope["now"], approval_handles=approval_handles)
    kwargs.update(changes)
    return contract.admit_connection_packet(packet, **kwargs)


def test_packet_is_closed_disabled_exact_target_and_new_static_fixture(tmp_path):
    contract = _api()
    scope = make_scope(tmp_path, contract)
    packet = contract.prepare_connection_packet(**scope)
    assert checked(contract, packet, scope) == packet
    assert packet["format"] == "devbench.rna-hosted-connection/2"
    assert packet["region"] == "us"
    assert packet["session"]["authorize_upload"] is False
    assert packet["target"]["workspace_id"] == "59a08947-1ff8-4adf-956b-a7f0fd9eb21c"
    assert packet["target"]["project_id"] == "2f749903-8bc2-4f79-a7e6-75ae493fe4d4"
    assert packet["target"]["project_name"] == "bio-harness-hosted-qualification"
    assert packet["limits"] == contract.CONNECTION_CAPS
    assert packet["schema_mode"] == "experimental_joined_binding_bootstrap"
    assert packet["deployed_schema_qualified"] is False
    assert contract.CONNECTION_FIXTURE["format"] == "devbench.rna-connection-fixture/2"
    assert contract.CONNECTION_FIXTURE["values"] == [1, 2, 3]
    assert contract.CONNECTION_FIXTURE["expected_sum"] == 6
    assert contract.CONNECTION_FIXTURE["requirement_source"] == "assistant_selected_synthetic_fixture"
    assert packet["target_read"]["action"] == "target_read"
    assert packet["post_run_payload"]["action"] == "post_run_payload"
    for proposal in (packet["target_read"], packet["post_run_payload"]):
        assert proposal["authorize_upload"] is False
        assert proposal["authorize_remote_read"] is False
    assert not Path(scope["evidence_path"]).exists()


def test_preparation_makes_fresh_ids_and_independent_copies(tmp_path):
    contract = _api()
    scope = make_scope(tmp_path, contract)
    left = contract.prepare_connection_packet(**scope)
    right = contract.prepare_connection_packet(**scope)
    assert left["packet_id"] != right["packet_id"]
    assert left["target_read"]["proposal_id"] != right["target_read"]["proposal_id"]
    left["operations"][0]["payload"]["inputs"]["unrequested"] = "CANARY"
    assert scope["operations"][0]["payload"]["inputs"] == {}


def test_empty_approval_handles_remain_prepared_blocked(tmp_path):
    contract = _api()
    scope = make_scope(tmp_path, contract)
    packet = contract.prepare_connection_packet(**scope)
    result = admitted(contract, packet, scope, {})
    assert result["status"] == "blocked"
    assert result["authorize_upload"] is False
    assert result["authorize_remote_read"] is False
    assert "trusted_operator_approval_handles_absent" in result["blockers"]


def test_separately_supplied_private_operator_inputs_admit_experimental_only(tmp_path):
    contract = _api()
    scope = make_scope(tmp_path, contract)
    packet = contract.prepare_connection_packet(**scope)
    result = admitted(contract, packet, scope, handles(contract, packet, scope, tmp_path))
    assert result["status"] == "admitted"
    assert result["authorize_upload"] is True
    assert result["authorize_remote_read"] is True
    assert result["deployed_schema_qualified"] is False
    assert result["schema_mode"] == "experimental_joined_binding_bootstrap"
    assert set(result["approval_record_digests"]) == {"source_runtime_review", "target_read", "post_run_payload"}


@pytest.mark.parametrize("key", ["source_digest", "runtime_digest", "policy_digest", "criteria_digest", "fixture_manifest_digest"])
def test_child_computed_installed_identity_must_match(tmp_path, key):
    contract = _api()
    scope = make_scope(tmp_path, contract)
    packet = contract.prepare_connection_packet(**scope)
    changed = {**scope["installed_identity"], key: "f" * 64}
    with pytest.raises(ValueError):
        checked(contract, packet, scope, installed_identity=changed)


@pytest.mark.parametrize("key", ["source_digest", "runtime_digest", "policy_digest", "criteria_digest", "fixture_manifest_digest", "session_digest", "payload_digest"])
def test_independent_review_binding_must_match(tmp_path, key):
    contract = _api()
    scope = make_scope(tmp_path, contract)
    packet = contract.prepare_connection_packet(**scope)
    changed = {**packet["binding"], key: "f" * 64}
    with pytest.raises(ValueError):
        checked(contract, packet, scope, expected_binding=changed)


@pytest.mark.parametrize("key,bad", [("workspace_id", "11111111-1111-4111-8111-111111111111"), ("project_id", "22222222-2222-4222-8222-222222222222"),
    ("project_name", "renamed"), ("endpoint", "https://eu.api.smith.langchain.com")])
def test_independent_target_must_match_exact_target(tmp_path, key, bad):
    contract = _api()
    scope = make_scope(tmp_path, contract)
    packet = contract.prepare_connection_packet(**scope)
    with pytest.raises(ValueError):
        checked(contract, packet, scope, expected_target={**scope["target"], key: bad})


@pytest.mark.parametrize("key,bad", [("max_spans", 2), ("max_bytes", 32769),
    ("max_mutation_requests", 3), ("max_read_requests", 5), ("max_total_requests", 7),
    ("max_response_bytes", 65537), ("total_deadline_seconds", 61),
    ("max_delivery_attempts", 2), ("total_deadline_seconds", float("inf")),
    ("total_deadline_seconds", float("nan")), ("max_spans", True)])
def test_packet_caps_are_immutable_and_finite(tmp_path, key, bad):
    contract = _api()
    scope = make_scope(tmp_path, contract)
    packet = contract.prepare_connection_packet(**scope)
    packet["limits"][key] = bad
    with pytest.raises(ValueError):
        checked(contract, packet, scope)


@pytest.mark.parametrize("key,bad", [("region", "eu"), ("schema_mode", "qualified"),
    ("deployed_schema_qualified", True), ("data_scope", "clinical"), ("api_key", "CANARY"),
    ("ambiguous_acceptance_policy", "resend"), ("packet_id", True), ("evidence_path", "relative")])
def test_unknown_fields_and_claim_widening_rejected(tmp_path, key, bad):
    contract = _api()
    scope = make_scope(tmp_path, contract)
    packet = contract.prepare_connection_packet(**scope)
    packet[key] = bad
    with pytest.raises((ValueError, PermissionError)) as caught:
        checked(contract, packet, scope)
    assert "CANARY" not in str(caught.value)


@pytest.mark.parametrize("mutation", ["reverse", "duplicate", "revision_bool", "id_bool", "payload_swap", "child", "privacy", "source", "proof", "session_enable"])
def test_exact_one_root_operations_cannot_be_swapped_or_widened(tmp_path, mutation):
    contract = _api()
    scope = make_scope(tmp_path, contract)
    candidate = deepcopy(scope)
    operations = candidate["operations"]
    if mutation == "reverse": operations.reverse()
    elif mutation == "duplicate": operations[1] = deepcopy(operations[0])
    elif mutation == "revision_bool": operations[1]["operation"]["revision"] = True
    elif mutation == "id_bool": operations[0]["operation"]["operation_id"] = True
    elif mutation == "payload_swap": operations[1]["payload"] = deepcopy(operations[0]["payload"])
    elif mutation == "child": operations[1]["payload"]["parent_run_id"] = str(uuid4())
    elif mutation == "privacy": operations[0]["payload"]["inputs"] = {"prompt": "CANARY"}
    elif mutation == "source": operations[0]["payload"]["extra"]["metadata"]["source_digest"] = "f" * 64
    elif mutation == "proof": operations[1]["payload"]["extra"]["metadata"]["remote_proof"]["revision"] = True
    elif mutation == "session_enable": candidate["session"]["authorize_upload"] = True
    with pytest.raises((ValueError, PermissionError)):
        contract.prepare_connection_packet(**candidate)


@pytest.mark.parametrize("which", ["target_read", "post_run_payload"])
def test_proposal_changes_cannot_bypass_packet_binding(tmp_path, which):
    contract = _api()
    scope = make_scope(tmp_path, contract)
    packet = contract.prepare_connection_packet(**scope)
    packet[which]["expires_at"] = (scope["now"] + timedelta(minutes=10)).isoformat()
    with pytest.raises(ValueError):
        checked(contract, packet, scope)


def test_expiry_future_clock_and_replay_rejected(tmp_path):
    contract = _api()
    scope = make_scope(tmp_path, contract)
    packet = contract.prepare_connection_packet(**scope)
    with pytest.raises(ValueError):
        checked(contract, packet, scope, now=scope["now"] + timedelta(minutes=6))
    with pytest.raises(ValueError):
        checked(contract, packet, scope, now=scope["now"] - timedelta(minutes=1))
    inputs = handles(contract, packet, scope, tmp_path)
    with pytest.raises((ValueError, PermissionError)):
        admitted(contract, packet, scope, inputs, consumed_packet_ids=[packet["packet_id"]])


def test_partial_swapped_and_self_review_approvals_rejected(tmp_path):
    contract = _api()
    scope = make_scope(tmp_path, contract)
    packet = contract.prepare_connection_packet(**scope)
    inputs = handles(contract, packet, scope, tmp_path)
    with pytest.raises(ValueError):
        admitted(contract, packet, scope, {"target_read": inputs["target_read"]})
    swapped = {**inputs, "target_read": inputs["post_run_payload"]}
    with pytest.raises(ValueError):
        admitted(contract, packet, scope, swapped)
    path = Path(inputs["source_runtime_review"]["path"])
    review = json.loads(path.read_bytes())
    review["reviewer_id"] = review["implementation_owner_id"]
    path.write_bytes(canonical_json_bytes(review))
    inputs["source_runtime_review"]["sha256"] = digest(path.read_bytes())
    with pytest.raises((ValueError, PermissionError)):
        admitted(contract, packet, scope, inputs)


@pytest.mark.parametrize("mutation", ["digest", "public", "symlink", "parent_symlink", "hardlink", "fifo", "oversize", "duplicates", "nonfinite", "unknown_handle_field"])
def test_private_handle_integrity_and_bounded_file_rules(tmp_path, mutation):
    contract = _api()
    scope = make_scope(tmp_path, contract)
    packet = contract.prepare_connection_packet(**scope)
    inputs = handles(contract, packet, scope, tmp_path)
    handle = inputs["target_read"]
    path = Path(handle["path"])
    if mutation == "digest": handle["sha256"] = "f" * 64
    elif mutation == "public": path.chmod(0o644)
    elif mutation == "symlink":
        link = tmp_path / "alias.json"; link.symlink_to(path); handle["path"] = str(link)
    elif mutation == "parent_symlink":
        link = tmp_path / "aliasdir"; link.symlink_to(tmp_path, target_is_directory=True)
        handle["path"] = str(link / path.name)
    elif mutation == "hardlink": os.link(path, tmp_path / "other.json")
    elif mutation == "fifo":
        path.unlink(); os.mkfifo(path, 0o600)
    elif mutation == "oversize": path.write_bytes(b" " * 32769)
    elif mutation == "duplicates": path.write_bytes(b'{"approved":true,"approved":false}')
    elif mutation == "nonfinite": path.write_bytes(b'{"n":1e999}')
    elif mutation == "unknown_handle_field": handle["api_key"] = "CANARY"
    if mutation in {"oversize", "duplicates", "nonfinite"}: handle["sha256"] = digest(path.read_bytes())
    with pytest.raises((ValueError, PermissionError, OSError)) as caught:
        admitted(contract, packet, scope, inputs)
    assert "CANARY" not in str(caught.value)


def test_read_approval_helper_returns_fresh_copy(tmp_path):
    contract = _api()
    scope = make_scope(tmp_path, contract)
    packet = contract.prepare_connection_packet(**scope)
    handle = handles(contract, packet, scope, tmp_path)["source_runtime_review"]
    record = contract.read_approval_handle(handle)
    assert record["binding"] == packet["binding"]
    record["binding"]["source_digest"] = "f" * 64
    assert contract.read_approval_handle(handle)["binding"] == packet["binding"]


@pytest.mark.parametrize("action,key,bad", [
    ("target_read", "approved", 1), ("post_run_payload", "operator_id", True),
    ("post_run_payload", "proposal_digest", "f" * 64),
    ("target_read", "action", "post_run_payload"),
    ("target_read", "api_key", "CANARY"),
    ("source_runtime_review", "approved", 1),
    ("source_runtime_review", "packet_digest", "f" * 64),
    ("source_runtime_review", "reviewer_id", True),
    ("source_runtime_review", "expires_at", "2000-01-01T00:00:00+00:00")])
def test_operator_input_corruption_cannot_enable_admission(tmp_path, action, key, bad):
    contract = _api()
    scope = make_scope(tmp_path, contract)
    packet = contract.prepare_connection_packet(**scope)
    inputs = handles(contract, packet, scope, tmp_path)
    path = Path(inputs[action]["path"])
    record = json.loads(path.read_bytes())
    record[key] = bad
    path.write_bytes(canonical_json_bytes(record))
    inputs[action]["sha256"] = digest(path.read_bytes())
    with pytest.raises((ValueError, PermissionError)) as caught:
        admitted(contract, packet, scope, inputs)
    assert "CANARY" not in str(caught.value)
