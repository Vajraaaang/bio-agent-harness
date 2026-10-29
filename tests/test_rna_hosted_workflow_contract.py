"""Default-off /3 synthetic topology preparation and exact-action approval."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import importlib
import json
from pathlib import Path
from uuid import uuid4

import pytest

from devbench.study.contracts import canonical_json_bytes
from devbench.study.langsmith_outbox import digest
from devbench.study.rna_langsmith_live import EVENT_FIELDS


def _api():
    name = "devbench.study.rna_hosted_workflow_contract"
    assert importlib.util.find_spec(name) is not None, "Hosted workflow contract feature is absent"
    return importlib.import_module(name)


def make_scope(tmp_path, contract):
    now = datetime.now(timezone.utc)
    expires = (now + timedelta(minutes=5)).isoformat()
    identity = dict(source_digest="a" * 64, runtime_digest="b" * 64,
        policy_digest="c" * 64,
        criteria_digest=digest(canonical_json_bytes(contract.WORKFLOW_CRITERIA)),
        fixture_manifest_digest=digest(canonical_json_bytes(contract.WORKFLOW_FIXTURE)))
    root = str(uuid4())
    slots = [("lead", "audit"), ("lead", "question"),
        ("investigator", "selection"), ("numerical", "run"),
        ("review", "verify"), ("lead", "synthesis"), ("review", "review")]
    topology = [dict(span_id=root, parent_span_id=None, role="coordinator",
                     phase="workflow", attempt=1)]
    topology.extend(dict(span_id=str(uuid4()), parent_span_id=root, role=role,
                         phase=phase, attempt=1) for role, phase in slots)
    session = dict(format="devbench.rna-live-trace-session/3", workflow_alias=str(uuid4()),
        workflow_binding_digest="d" * 64, data_scope="synthetic",
        **{key: identity[key] for key in ("source_digest", "runtime_digest", "policy_digest")},
        endpoint=contract.TARGET["endpoint"], project=contract.TARGET["project_name"],
        project_id=contract.TARGET["project_id"], workspace_id=contract.TARGET["workspace_id"],
        redaction_version="rna-allowlist/1", event_schema="devbench.rna-trace-event/1",
        allowed_event_fields=sorted(EVENT_FIELDS), max_spans=8, max_bytes=131072,
        max_delivery_attempts=1, expires_at=expires, authorize_upload=False,
        span_topology=deepcopy(topology))
    return dict(installed_identity=identity, target=deepcopy(contract.TARGET),
        session=session, topology=topology, expires_at=expires,
        evidence_path=str(tmp_path / "evidence"), now=now)


def checked(contract, packet, scope, **changes):
    kwargs = dict(installed_identity=scope["installed_identity"],
        expected_binding={**scope["installed_identity"],
            "session_digest": digest(canonical_json_bytes(scope["session"])),
            "payload_digest": digest(canonical_json_bytes(scope["topology"]))},
        expected_target=scope["target"], now=scope["now"])
    kwargs.update(changes)
    return contract.validate_workflow_packet(packet, **kwargs)


def handles(contract, packet, scope, directory):
    owner, reviewer = str(uuid4()), str(uuid4())
    records = {"source_runtime_review": dict(format="devbench.rna-workflow-review/3",
        packet_digest=contract.packet_digest(packet), binding=packet["binding"],
        target=packet["target"], expires_at=packet["expires_at"], approved=True,
        reviewer_id=reviewer, implementation_owner_id=owner)}
    for action in ("target_read", "live_session"):
        records[action] = dict(format="devbench.rna-hosted-action-approval/3",
            proposal_digest=contract.packet_digest(packet[action]), action=action,
            approved=True, operator_id=reviewer, implementation_owner_id=owner,
            expires_at=scope["expires_at"])
    result = {}
    for action, record in records.items():
        path = directory / (action + ".json")
        path.write_bytes(canonical_json_bytes(record))
        path.chmod(0o600)
        result[action] = dict(path=str(path), sha256=digest(path.read_bytes()))
    return result


def admitted(contract, packet, scope, approval_handles, **changes):
    kwargs = dict(installed_identity=scope["installed_identity"],
        expected_binding=packet["binding"], expected_target=scope["target"],
        now=scope["now"], approval_handles=approval_handles)
    kwargs.update(changes)
    return contract.admit_workflow_packet(packet, **kwargs)


def rewrite_record(approval_handles, action, update):
    path = Path(approval_handles[action]["path"])
    record = json.loads(path.read_bytes())
    update(record)
    path.write_bytes(canonical_json_bytes(record))
    approval_handles[action]["sha256"] = digest(path.read_bytes())


def test_packet_freezes_eight_remote_spans_and_is_default_off(tmp_path):
    contract = _api()
    scope = make_scope(tmp_path, contract)
    packet = contract.prepare_workflow_packet(**scope)
    assert checked(contract, packet, scope) == packet
    assert packet["format"] == "devbench.rna-hosted-workflow/3"
    assert packet["topology"] == packet["session"]["span_topology"] == scope["topology"]
    assert len(packet["topology"]) == 8
    assert "operations" not in packet
    assert packet["authorize_upload"] is packet["authorize_remote_read"] is False
    assert packet["session"]["authorize_upload"] is False
    assert packet["data_scope"] == "synthetic"
    assert packet["schema_mode"] == "experimental_live_workflow"
    assert packet["target"] == contract.TARGET
    assert packet["binding"]["payload_digest"] == contract.packet_digest(scope["topology"])
    assert packet["limits"] == dict(max_spans=8, max_bytes=131072,
        max_mutation_requests=16, max_read_requests=12, max_total_requests=28,
        max_response_bytes=65536, total_deadline_seconds=180, max_delivery_attempts=1)
    for action, operations in (("target_read", ["read_project", "read_run"]),
                               ("live_session", ["create_run", "update_run"])):
        proposal = packet[action]
        assert proposal["format"] == "devbench.rna-hosted-action-proposal/3"
        assert proposal["action"] == action
        assert proposal["allowed_operations"] == operations
        assert proposal["run_ids"] == [item["span_id"] for item in scope["topology"]]
        assert proposal["authorize_upload"] is proposal["authorize_remote_read"] is False
        assert proposal["proposal_id"] != packet["packet_id"]
    assert packet["target_read"]["proposal_id"] != packet["live_session"]["proposal_id"]
    assert not Path(scope["evidence_path"]).exists()
    blocked = admitted(contract, packet, scope, {})
    assert blocked["status"] == "blocked"
    assert blocked["blockers"] == ["trusted_operator_approval_handles_absent"]
    assert blocked["authorize_upload"] is blocked["authorize_remote_read"] is False


def test_read_descriptor_only_allows_frozen_project_and_exact_run_gets(tmp_path):
    contract = _api()
    scope = make_scope(tmp_path, contract)
    packet = contract.prepare_workflow_packet(**scope)
    descriptor = packet["read_descriptor"]
    ids = [item["span_id"] for item in scope["topology"]]
    assert descriptor["root_run_id"] == ids[0]
    assert descriptor["run_ids"] == ids
    assert descriptor["project_route"] == "/api/v1/sessions/" + contract.TARGET["project_id"] + "?include_stats=false"
    assert descriptor["run_routes"] == ["/api/v1/runs/" + value for value in ids]
    assert descriptor["recovery_policy"] == "no_automatic_resend_or_reconciliation"
    assert "query_route" not in descriptor


def test_distinct_static_identity_reuses_bounded_installed_resources(monkeypatch):
    contract = _api()
    qualification = importlib.import_module("devbench.study.rna_hosted_qualification")
    monkeypatch.setattr(qualification, "bounded_installed_identity", lambda: dict(
        source_digest="a" * 64, runtime_digest="b" * 64, policy_digest="c" * 64))
    identity = contract.workflow_installed_identity()
    assert identity == dict(source_digest="a" * 64, runtime_digest="b" * 64,
        policy_digest="c" * 64,
        criteria_digest=contract.packet_digest(contract.WORKFLOW_CRITERIA),
        fixture_manifest_digest=contract.packet_digest(contract.WORKFLOW_FIXTURE))
    connection = importlib.import_module("devbench.study.rna_hosted_connection_contract")
    assert identity["criteria_digest"] != contract.packet_digest(connection.CONNECTION_CRITERIA)
    assert identity["fixture_manifest_digest"] != contract.packet_digest(connection.CONNECTION_FIXTURE)
    assert contract.WORKFLOW_FIXTURE["data_scope"] == "synthetic"
    assert contract.WORKFLOW_FIXTURE["scientific_assessment"] == "not_applicable_synthetic_engineering"
    assert connection.CONNECTION_CAPS == dict(max_spans=1, max_bytes=32768,
        max_mutation_requests=2, max_read_requests=4, max_total_requests=6,
        max_response_bytes=65536, total_deadline_seconds=60, max_delivery_attempts=1)


def test_legacy_verifier_criteria_are_frozen_technical_adapter_slots_only():
    contract = _api()
    from devbench.study.rna_hosted_trace_contract import validate_criteria
    criteria = contract.WORKFLOW_CRITERIA
    assert criteria.get("verifier_stage_scope") == "technical_adapter_slot_only"
    expected = dict(format="devbench.rna-hosted-criteria/1",
        sample_matrix=[dict(fixture_id=contract.WORKFLOW_FIXTURE["fixture_id"],
                           stage="pre_fit_methods", allocation=0, repeat=1)],
        api_p95_upper_seconds=5, visibility_deadline_seconds=5,
        poll_interval_seconds=.25, dashboard_window_seconds=30,
        max_extra_model_calls=0, max_extra_fits=0, max_extra_retrievals=0,
        max_source_writes=0, max_privacy_leaks=0, required_topology_fraction=1,
        censored_samples_fail=True, limits={key: value for key, value in
            contract.WORKFLOW_CAPS.items() if key not in {"max_total_requests", "max_response_bytes"}})
    assert criteria.get("verifier_criteria") == expected
    assert validate_criteria(criteria["verifier_criteria"]) == expected


def test_prepare_validate_and_blocked_admission_do_not_read_operator_inputs(tmp_path, monkeypatch):
    contract = _api()
    scope = make_scope(tmp_path, contract)
    def forbidden(*args, **kwargs):
        pytest.fail("Offline preparation/validation accessed an operator input")
    monkeypatch.setattr(contract, "read_approval_handle", forbidden)
    packet = contract.prepare_workflow_packet(**scope)
    checked(contract, packet, scope)
    contract.validate_workflow_wire_packet(packet, now=scope["now"])
    assert admitted(contract, packet, scope, {})["status"] == "blocked"


@pytest.mark.parametrize("key", ["source_digest", "runtime_digest", "policy_digest",
    "criteria_digest", "fixture_manifest_digest", "session_digest", "payload_digest"])
def test_independent_binding_must_match(tmp_path, key):
    contract = _api()
    scope = make_scope(tmp_path, contract)
    packet = contract.prepare_workflow_packet(**scope)
    with pytest.raises(ValueError):
        checked(contract, packet, scope, expected_binding={**packet["binding"], key: "f" * 64})


@pytest.mark.parametrize("key,bad", [("workspace_id", str(uuid4())),
    ("project_id", str(uuid4())), ("project_name", "other"),
    ("endpoint", "https://eu.api.smith.langchain.com")])
def test_target_cannot_drift(tmp_path, key, bad):
    contract = _api()
    scope = make_scope(tmp_path, contract)
    packet = contract.prepare_workflow_packet(**scope)
    with pytest.raises(ValueError):
        checked(contract, packet, scope, expected_target={**scope["target"], key: bad})


@pytest.mark.parametrize("key,bad", [("max_spans", 9), ("max_spans", True),
    ("max_bytes", 131073), ("max_mutation_requests", 17), ("max_read_requests", 13),
    ("max_total_requests", 29), ("max_response_bytes", 65537),
    ("total_deadline_seconds", 181), ("total_deadline_seconds", float("inf")),
    ("total_deadline_seconds", float("nan")), ("max_delivery_attempts", 2)])
def test_workflow_budgets_are_exact_finite_caps(tmp_path, key, bad):
    contract = _api()
    scope = make_scope(tmp_path, contract)
    packet = contract.prepare_workflow_packet(**scope)
    packet["limits"][key] = bad
    with pytest.raises(ValueError):
        checked(contract, packet, scope)


@pytest.mark.parametrize("mutation", ["few", "many", "unknown", "duplicate_id", "parent_after",
    "two_roots", "missing_parent", "root_role", "root_phase", "root_parent", "slot",
    "role", "phase", "attempt_bool", "attempt_zero", "retry", "id_bool", "self_parent"])
def test_malformed_or_unbounded_topology_is_rejected(tmp_path, mutation):
    contract = _api()
    scope = make_scope(tmp_path, contract)
    topology = scope["topology"]
    if mutation == "few": topology.pop()
    elif mutation == "many": topology.append({**topology[-1], "span_id": str(uuid4())})
    elif mutation == "unknown": topology[1]["private_prompt"] = "CANARY"
    elif mutation == "duplicate_id": topology[1]["span_id"] = topology[0]["span_id"]
    elif mutation == "parent_after": topology[1]["parent_span_id"] = topology[2]["span_id"]
    elif mutation == "two_roots": topology[1]["parent_span_id"] = None
    elif mutation == "missing_parent": topology[1]["parent_span_id"] = str(uuid4())
    elif mutation == "root_role": topology[0]["role"] = "lead"
    elif mutation == "root_phase": topology[0]["phase"] = "audit"
    elif mutation == "root_parent": topology[0]["parent_span_id"] = str(uuid4())
    elif mutation == "slot": topology[2].update(role=topology[1]["role"], phase=topology[1]["phase"])
    elif mutation == "role": topology[1]["role"] = "model"
    elif mutation == "phase": topology[1]["phase"] = "upload"
    elif mutation == "attempt_bool": topology[1]["attempt"] = True
    elif mutation == "attempt_zero": topology[1]["attempt"] = 0
    elif mutation == "retry": topology[1]["attempt"] = 2
    elif mutation == "id_bool": topology[1]["span_id"] = True
    elif mutation == "self_parent": topology[1]["parent_span_id"] = topology[1]["span_id"]
    scope["session"]["span_topology"] = deepcopy(topology)
    with pytest.raises((ValueError, PermissionError)) as caught:
        contract.prepare_workflow_packet(**scope)
    assert "CANARY" not in str(caught.value)


def test_nested_parent_precedes_child_topology_is_allowed(tmp_path):
    contract = _api()
    scope = make_scope(tmp_path, contract)
    for index in range(1, len(scope["topology"])):
        scope["topology"][index]["parent_span_id"] = scope["topology"][index - 1]["span_id"]
    scope["session"]["span_topology"] = deepcopy(scope["topology"])
    packet = contract.prepare_workflow_packet(**scope)
    assert checked(contract, packet, scope) == packet


@pytest.mark.parametrize("key,bad", [("format", "devbench.rna-live-trace-session/2"),
    ("authorize_upload", True), ("data_scope", "public_processed"),
    ("max_spans", 1), ("max_bytes", 32768), ("max_delivery_attempts", 2),
    ("private_path", "/private/source.csv"), ("prompt", "CANARY"),
    ("source_digest", "f" * 64), ("project_id", str(uuid4()))])
def test_session_is_exact_synthetic_disabled_closed_and_bound(tmp_path, key, bad):
    contract = _api()
    scope = make_scope(tmp_path, contract)
    scope["session"][key] = bad
    with pytest.raises((ValueError, PermissionError)):
        contract.prepare_workflow_packet(**scope)


def test_session_topology_cannot_differ_from_packet(tmp_path):
    contract = _api()
    scope = make_scope(tmp_path, contract)
    scope["session"]["span_topology"][1]["span_id"] = str(uuid4())
    with pytest.raises(ValueError):
        contract.prepare_workflow_packet(**scope)


@pytest.mark.parametrize("key,bad", [("region", "eu"), ("schema_mode", "qualified"),
    ("data_scope", "clinical"), ("api_key", "CANARY"), ("operations", []),
    ("raw_data", [1, 2, 3]), ("authorize_upload", True),
    ("authorize_remote_read", True), ("evidence_path", "relative"),
    ("privacy_policy", "raw"), ("packet_id", True)])
def test_wire_rejects_unknown_private_fields_and_default_on(tmp_path, key, bad):
    contract = _api()
    scope = make_scope(tmp_path, contract)
    packet = contract.prepare_workflow_packet(**scope)
    packet[key] = bad
    with pytest.raises((ValueError, PermissionError)) as caught:
        contract.validate_workflow_wire_packet(packet, now=scope["now"])
    assert "CANARY" not in str(caught.value)


@pytest.mark.parametrize("action,key,bad", [("target_read", "action", "live_session"),
    ("live_session", "allowed_operations", ["create_project"]),
    ("live_session", "authorize_upload", True), ("target_read", "authorize_remote_read", True),
    ("target_read", "run_ids", [str(uuid4())]), ("live_session", "subject_digest", "f" * 64),
    ("live_session", "api_key", "CANARY")])
def test_action_proposal_is_exact_and_purpose_bound(tmp_path, action, key, bad):
    contract = _api()
    scope = make_scope(tmp_path, contract)
    packet = contract.prepare_workflow_packet(**scope)
    packet[action][key] = bad
    with pytest.raises(ValueError):
        checked(contract, packet, scope)


def test_fresh_output_clock_expiry_and_replay_rules(tmp_path):
    contract = _api()
    scope = make_scope(tmp_path, contract)
    packet = contract.prepare_workflow_packet(**scope)
    with pytest.raises(ValueError):
        checked(contract, packet, scope, now=scope["now"] + timedelta(minutes=6))
    with pytest.raises(ValueError):
        checked(contract, packet, scope, now=scope["now"] - timedelta(seconds=1))
    with pytest.raises(PermissionError):
        admitted(contract, packet, scope, {}, consumed_packet_ids=[packet["packet_id"]])
    Path(scope["evidence_path"]).mkdir()
    assert checked(contract, packet, scope, fresh_output=False) == packet
    assert contract.validate_workflow_wire_packet(packet, now=scope["now"]) == packet
    with pytest.raises(ValueError):
        checked(contract, packet, scope)


def test_three_separate_exact_inputs_admit_without_mutating_packet(tmp_path):
    contract = _api()
    scope = make_scope(tmp_path, contract)
    packet = contract.prepare_workflow_packet(**scope)
    approvals = handles(contract, packet, scope, tmp_path)
    result = admitted(contract, packet, scope, approvals)
    assert result["format"] == "devbench.rna-workflow-admission/3"
    assert result["status"] == "admitted"
    assert result["authorize_upload"] is result["authorize_remote_read"] is True
    assert set(result["approval_record_digests"]) == set(approvals)
    assert packet["authorize_upload"] is packet["session"]["authorize_upload"] is False
    assert not Path(scope["evidence_path"]).exists()
    assert contract.validate_workflow_approvals(packet, expected_binding=packet["binding"],
        expected_target=scope["target"], now=scope["now"], approval_handles=approvals) == result["approval_record_digests"]


@pytest.mark.parametrize("mutation", ["partial", "swapped", "same_path", "self_review", "self_operator",
    "owner_drift", "expiry_drift", "wrong_purpose", "old_action", "old_review", "unapproved",
    "packet_digest", "proposal_digest", "public_input", "unknown_handle"])
def test_unreviewed_crosspurpose_and_corrupt_inputs_cannot_admit(tmp_path, mutation):
    contract = _api()
    scope = make_scope(tmp_path, contract)
    packet = contract.prepare_workflow_packet(**scope)
    approvals = handles(contract, packet, scope, tmp_path)
    if mutation == "partial": approvals.pop("live_session")
    elif mutation == "swapped": approvals["target_read"], approvals["live_session"] = approvals["live_session"], approvals["target_read"]
    elif mutation == "same_path": approvals["target_read"] = approvals["live_session"]
    elif mutation == "self_review": rewrite_record(approvals, "source_runtime_review", lambda r: r.update(reviewer_id=r["implementation_owner_id"]))
    elif mutation == "self_operator": rewrite_record(approvals, "live_session", lambda r: r.update(operator_id=r["implementation_owner_id"]))
    elif mutation == "owner_drift": rewrite_record(approvals, "target_read", lambda r: r.update(implementation_owner_id=str(uuid4())))
    elif mutation == "expiry_drift": rewrite_record(approvals, "live_session", lambda r: r.update(expires_at=(scope["now"] + timedelta(minutes=6)).isoformat()))
    elif mutation == "wrong_purpose": rewrite_record(approvals, "target_read", lambda r: r.update(action="live_session"))
    elif mutation == "old_action": rewrite_record(approvals, "live_session", lambda r: r.update(format="devbench.rna-hosted-action-approval/2"))
    elif mutation == "old_review": rewrite_record(approvals, "source_runtime_review", lambda r: r.update(format="devbench.rna-connection-review/2"))
    elif mutation == "unapproved": rewrite_record(approvals, "live_session", lambda r: r.update(approved=1))
    elif mutation == "packet_digest": rewrite_record(approvals, "source_runtime_review", lambda r: r.update(packet_digest="f" * 64))
    elif mutation == "proposal_digest": rewrite_record(approvals, "target_read", lambda r: r.update(proposal_digest="f" * 64))
    elif mutation == "public_input": Path(approvals["target_read"]["path"]).chmod(0o644)
    elif mutation == "unknown_handle": approvals["target_read"]["api_key"] = "CANARY"
    with pytest.raises((ValueError, PermissionError)) as caught:
        admitted(contract, packet, scope, approvals)
    assert "CANARY" not in str(caught.value)


# Additive /4 counterexamples and contract cases. Legacy /3 cases above stay intact.
def _context_api():
    name = "devbench.study.rna_hosted_context_contract"
    assert importlib.util.find_spec(name) is not None, "Context-observation /4 contract feature is absent"
    return importlib.import_module(name)


@pytest.mark.parametrize("counterexample", ["nine_contexts", "source_attempt_two"])
def test_v4_counterexample_v3_rejects_larger_tree_and_source_attempts(tmp_path, counterexample):
    contract = _api()
    scope = make_scope(tmp_path, contract)
    if counterexample == "nine_contexts":
        scope["topology"].append(dict(span_id=str(uuid4()),
            parent_span_id=scope["topology"][3]["span_id"], role="investigator",
            phase="review", attempt=1))
        assert len(scope["topology"]) == 9
    else:
        scope["topology"][3]["attempt"] = 2
        assert len(scope["topology"]) == 8
    scope["session"]["span_topology"] = deepcopy(scope["topology"])
    with pytest.raises(ValueError, match="workflow packet"):
        contract.prepare_workflow_packet(**scope)


def _v3_source_receipts(tmp_path, kinds):
    from devbench.analysis.rna_trace_contract import trace_digest
    from devbench.analysis.rna_trace_receipts import LocalTraceObserver
    contract = _api()
    scope = make_scope(tmp_path, contract)
    workflow = "synthetic-context-counterexample"
    session = {**scope["session"], "workflow_binding_digest": trace_digest(workflow),
               "authorize_upload": True}
    observer = LocalTraceObserver(tmp_path / "counterexample.sqlite",
        source_digest=session["source_digest"], runtime_digest=session["runtime_digest"],
        policy_digest=session["policy_digest"])
    with observer._db() as db:
        context = observer._context(db, workflow, "lead", "audit", 1,
                                    trace_digest([workflow, "synthetic-context"]))
    for kind in kinds:
        observer._emit(context, trace_digest(workflow), kind,
            "reserved" if kind == "reservation" else "completed", dispatch="not_dispatched")
    return session, observer, workflow, context


def test_v4_counterexample_v3_rejects_sequence_two_without_changing_receipts(tmp_path):
    from devbench.study.rna_hosted_workflow import WorkflowReceiptBridge
    session, observer, workflow, context = _v3_source_receipts(tmp_path,
        ("reservation", "capture", "completion"))
    before = observer.read_events(workflow)
    assert [row["sequence"] for row in before if row["span_id"] == context["span_id"]] == [0, 1, 2]
    bridge = WorkflowReceiptBridge(authorized_session_manifest=session,
                                   output_path=tmp_path / "sequence-counterexample.jsonl")
    with pytest.raises(ValueError, match="start and terminal receipts"):
        bridge.emit_new(observer=observer, workflow_id=workflow)
    assert observer.read_events(workflow) == before


def test_v4_counterexample_v3_rejects_capture_only_context_without_inventing_start(tmp_path):
    from devbench.study.rna_hosted_workflow import WorkflowReceiptBridge
    session, observer, workflow, context = _v3_source_receipts(tmp_path,
        ("capture", "completion"))
    before = observer.read_events(workflow)
    assert [row["kind"] for row in before if row["span_id"] == context["span_id"]] == ["capture", "completion"]
    bridge = WorkflowReceiptBridge(authorized_session_manifest=session,
                                   output_path=tmp_path / "capture-counterexample.jsonl")
    with pytest.raises(ValueError, match="cannot fill a receipt gap"):
        bridge.emit_new(observer=observer, workflow_id=workflow)
    assert observer.read_events(workflow) == before


def make_context_scope(tmp_path, contract, count=9, *, root_closed=False):
    """Synthetic unit-profile metadata, never a retained hosted checkpoint."""
    now = datetime.now(timezone.utc)
    expires = (now + timedelta(minutes=5)).isoformat()
    identity = dict(source_digest="a" * 64, runtime_digest="b" * 64,
        policy_digest="c" * 64,
        criteria_digest=digest(canonical_json_bytes(contract.CONTEXT_CRITERIA)),
        fixture_manifest_digest=digest(canonical_json_bytes(contract.CONTEXT_FIXTURE)))
    specifications = [(None, "coordinator", "workflow", 1, True, "completed" if root_closed else None),
        (0, "coordinator", "generate", 1, True, "failed"),
        (0, "coordinator", "permission_preflight", 1, True, "completed"),
        (0, "lead", "method_scientific_planning", 1, True, "completed"),
        (3, "lead", "selection", 1, False, "completed"),
        (0, "lead", "method_capability_resolution", 2, True, "completed"),
        (5, "lead", "selection", 2, False, "completed"),
        (0, "lead", "method_trial_design", 3, True, "completed"),
        (7, "lead", "selection", 3, False, "completed"),
        (0, "numerical", "run", 1, True, "completed"),
        (0, "review", "verify", 1, True, "completed"),
        (0, "review", "review", 1, True, "completed"),
        (0, "lead", "synthesis", 1, True, "completed"),
        (0, "coordinator", "recommend", 1, True, "completed"),
        (0, "investigator", "investigate", 1, True, "completed"),
        (0, "lead", "review", 1, True, "completed")][:count]
    topology, rows = [], []
    for index, (parent, role, phase, attempt, start, terminal) in enumerate(specifications):
        span, alias = str(uuid4()), str(uuid4())
        parent_span = None if parent is None else topology[parent]["span_id"]
        parent_alias = None if parent is None else rows[parent]["context_alias"]
        topology.append(dict(span_id=span, parent_span_id=parent_span,
                             role=role, phase=phase, attempt=attempt))
        kinds = (["reservation"] if start else ["capture"])
        if phase.startswith("method_"):
            kinds.append("capture")
        if terminal is not None:
            kinds.append("failure" if terminal == "failed" else "completion")
        event_digests = []
        for sequence, kind in enumerate(kinds):
            status = terminal if kind in {"completion", "failure"} else "completed" if kind == "capture" else "active"
            projection = dict(format="devbench.rna-context-source-event/4",
                context_alias=alias, parent_context_alias=parent_alias, role=role,
                phase=phase, attempt=attempt, source_sequence=sequence, kind=kind,
                source_status=status, dispatch_status="not_dispatched",
                capture_status="verified" if kind == "capture" else "unavailable",
                reason_code="none", clock=dict(utc=(now + timedelta(microseconds=10 * index + sequence)).isoformat(),
                                               timing_basis="observed"))
            event_digests.append(digest(canonical_json_bytes(projection)))
        rows.append(dict(span_id=span, context_alias=alias,
            parent_context_alias=parent_alias, start_observed=start,
            source_event_digests=event_digests, source_event_count=len(kinds),
            capture_event_count=kinds.count("capture"), terminal_status=terminal))
    terminal_ids = [row["span_id"] for row in rows if row["terminal_status"] is not None]
    profile = dict(format="devbench.rna-context-observation-profile/4",
        data_scope="synthetic", provenance="synthetic_engineering", usage_policy="unavailable",
        digest_policy="session_aliased_sanitized_receipts", context_count=len(rows),
        source_event_count=sum(row["source_event_count"] for row in rows),
        capture_event_count=sum(row["capture_event_count"] for row in rows),
        dispatch_event_count=0, operation_count=len(rows) + len(terminal_ids),
        terminal_span_ids=terminal_ids,
        open_span_ids=[row["span_id"] for row in rows if row["terminal_status"] is None],
        contexts=rows)
    session = dict(format="devbench.rna-live-trace-session/4", workflow_alias=str(uuid4()),
        workflow_binding_digest="d" * 64, data_scope="synthetic",
        **{key: identity[key] for key in ("source_digest", "runtime_digest", "policy_digest")},
        endpoint=contract.TARGET["endpoint"], project=contract.TARGET["project_name"],
        project_id=contract.TARGET["project_id"], workspace_id=contract.TARGET["workspace_id"],
        redaction_version="rna-allowlist/1", event_schema="devbench.rna-trace-event/1",
        allowed_event_fields=sorted(EVENT_FIELDS), max_spans=count, max_bytes=262144,
        max_delivery_attempts=1, expires_at=expires, authorize_upload=False,
        span_topology=deepcopy(topology), context_profile=deepcopy(profile))
    return dict(installed_identity=identity, target=deepcopy(contract.TARGET),
        session=session, topology=topology, profile=profile, expires_at=expires,
        evidence_path=str(tmp_path / "context-evidence"), now=now)


def context_checked(contract, packet, scope, **changes):
    kwargs = dict(installed_identity=scope["installed_identity"],
        expected_binding={**scope["installed_identity"],
            "session_digest": contract.packet_digest(scope["session"]),
            "payload_digest": contract.packet_digest(dict(topology=scope["topology"], profile=scope["profile"]))},
        expected_target=scope["target"], now=scope["now"])
    kwargs.update(changes)
    return contract.validate_context_packet(packet, **kwargs)


def context_handles(contract, packet, scope, directory):
    owner, reviewer = str(uuid4()), str(uuid4())
    records = {"source_runtime_review": dict(format="devbench.rna-context-review/4",
        packet_digest=contract.packet_digest(packet), binding=packet["binding"],
        target=packet["target"], expires_at=packet["expires_at"], approved=True,
        reviewer_id=reviewer, implementation_owner_id=owner)}
    for action in ("target_read", "live_session"):
        records[action] = dict(format="devbench.rna-hosted-action-approval/4",
            proposal_digest=contract.packet_digest(packet[action]), action=action,
            approved=True, operator_id=reviewer, implementation_owner_id=owner,
            expires_at=scope["expires_at"])
    result = {}
    for action, record in records.items():
        path = directory / ("context-" + action + ".json")
        path.write_bytes(canonical_json_bytes(record))
        path.chmod(0o600)
        result[action] = dict(path=str(path), sha256=digest(path.read_bytes()))
    return result


def context_admitted(contract, packet, scope, approval_handles, **changes):
    kwargs = dict(installed_identity=scope["installed_identity"],
        expected_binding=packet["binding"], expected_target=scope["target"],
        now=scope["now"], approval_handles=approval_handles)
    kwargs.update(changes)
    return contract.admit_context_packet(packet, **kwargs)


@pytest.mark.parametrize("count,root_closed", [(1, False), (9, False), (16, False), (16, True)])
def test_v4_context_packet_preserves_exact_dynamic_profile_and_narrows_caps(tmp_path, count, root_closed):
    contract = _context_api()
    scope = make_context_scope(tmp_path, contract, count, root_closed=root_closed)
    packet = contract.prepare_context_packet(**scope)
    assert context_checked(contract, packet, scope) == packet
    assert packet["format"] == "devbench.rna-hosted-context/4"
    assert packet["schema_mode"] == "experimental_context_observation"
    assert packet["profile"] == packet["session"]["context_profile"] == scope["profile"]
    assert packet["topology"] == packet["session"]["span_topology"] == scope["topology"]
    assert "operations" not in packet
    operations = count + len(scope["profile"]["terminal_span_ids"])
    reads = min(20, count + 4)
    assert packet["limits"] == dict(max_spans=count,
        max_source_events=scope["profile"]["source_event_count"], max_bytes=262144,
        max_mutation_requests=operations, max_read_requests=reads,
        max_total_requests=operations + reads, max_response_bytes=131072,
        total_deadline_seconds=240, max_delivery_attempts=1)
    assert contract.CONTEXT_CAPS == dict(max_spans=16, max_source_events=64,
        max_bytes=262144, max_mutation_requests=32, max_read_requests=20,
        max_total_requests=52, max_response_bytes=131072,
        total_deadline_seconds=240, max_delivery_attempts=1)
    assert packet["authorize_upload"] is packet["authorize_remote_read"] is False
    assert packet["session"]["authorize_upload"] is False
    assert not Path(scope["evidence_path"]).exists()
    assert (scope["topology"][0]["span_id"] in packet["profile"]["open_span_ids"]) is not root_closed


def test_v4_synthetic_nine_context_profile_keeps_capture_only_children_and_open_root(tmp_path):
    contract = _context_api()
    scope = make_context_scope(tmp_path, contract)
    packet = contract.prepare_context_packet(**scope)
    profile = packet["profile"]
    assert profile["context_count"] == 9 and profile["source_event_count"] == 20
    assert profile["capture_event_count"] == 6 and profile["dispatch_event_count"] == 0
    assert profile["operation_count"] == 17
    assert len(profile["terminal_span_ids"]) == 8 and len(profile["open_span_ids"]) == 1
    assert [row["terminal_status"] for row in profile["contexts"][:3]] == [None, "failed", "completed"]
    assert len([row for row in profile["contexts"] if row["start_observed"] is False]) == 3
    assert {row["attempt"] for row in packet["topology"]} == {1, 2, 3}
    assert profile["usage_policy"] == "unavailable"
    assert profile["digest_policy"] == "session_aliased_sanitized_receipts"
    assert "tokens" not in canonical_json_bytes(profile).decode()
    assert "metrics" not in canonical_json_bytes(profile).decode()
    ids = [row["span_id"] for row in scope["topology"]]
    assert packet["read_descriptor"]["run_ids"] == ids
    assert packet["read_descriptor"]["run_routes"] == ["/api/v1/runs/" + value for value in ids]


def test_v4_larger_stored_profile_can_name_later_phases_without_executing_them(tmp_path):
    contract = _context_api()
    scope = make_context_scope(tmp_path, contract, 16)
    packet = contract.prepare_context_packet(**scope)
    assert {"run", "verify", "review", "synthesis"} <= {row["phase"] for row in packet["topology"]}
    assert packet["profile"]["provenance"] == "synthetic_engineering"


def test_v4_parent_qualified_aliases_allow_equal_slots_under_different_parents(tmp_path):
    contract = _context_api()
    scope = make_context_scope(tmp_path, contract)
    for row in scope["topology"]:
        if row["phase"] == "selection":
            row["attempt"] = 1
    scope["session"]["span_topology"] = deepcopy(scope["topology"])
    packet = contract.prepare_context_packet(**scope)
    assert len({row["context_alias"] for row in packet["profile"]["contexts"]}) == 9
    assert len({row["parent_context_alias"] for row in packet["profile"]["contexts"] if not row["start_observed"]}) == 3


@pytest.mark.parametrize("mutation", ["too_many", "duplicate_id", "parent_after", "missing_parent",
    "second_root", "root_role", "root_phase", "attempt_zero", "attempt_bool", "attempt_overflow",
    "role", "phase", "private_field"])
def test_v4_closed_bounded_topology_rejects_invalid_structure(tmp_path, mutation):
    contract = _context_api()
    scope = make_context_scope(tmp_path, contract, 16)
    topology = scope["topology"]
    if mutation == "too_many": topology.append({**topology[-1], "span_id": str(uuid4())})
    elif mutation == "duplicate_id": topology[1]["span_id"] = topology[0]["span_id"]
    elif mutation == "parent_after": topology[1]["parent_span_id"] = topology[2]["span_id"]
    elif mutation == "missing_parent": topology[1]["parent_span_id"] = str(uuid4())
    elif mutation == "second_root": topology[1]["parent_span_id"] = None
    elif mutation == "root_role": topology[0]["role"] = "lead"
    elif mutation == "root_phase": topology[0]["phase"] = "generate"
    elif mutation == "attempt_zero": topology[1]["attempt"] = 0
    elif mutation == "attempt_bool": topology[1]["attempt"] = True
    elif mutation == "attempt_overflow": topology[1]["attempt"] = 2**31
    elif mutation == "role": topology[1]["role"] = "provider"
    elif mutation == "phase": topology[1]["phase"] = "unknown"
    elif mutation == "private_field": topology[1]["local_span_id"] = "CANARY"
    with pytest.raises(ValueError) as caught:
        contract.validate_context_topology(topology)
    assert "CANARY" not in str(caught.value)


@pytest.mark.parametrize("mutation", ["profile_format", "unknown_field", "usage", "digest_policy",
    "provenance", "scope", "dispatch", "contexts", "source_events", "captures", "operations",
    "terminal_set", "open_set", "row_count", "duplicate_alias", "remote_alias", "parent_alias",
    "start_bool", "digest_count", "duplicate_digest", "row_captures", "terminal_status",
    "invent_root_terminal", "private_row", "oversized_events"])
def test_v4_profile_is_closed_exact_synthetic_and_independently_counted(tmp_path, mutation):
    contract = _context_api()
    scope = make_context_scope(tmp_path, contract)
    profile, rows = scope["profile"], scope["profile"]["contexts"]
    if mutation == "profile_format": profile["format"] = "devbench.rna-context-observation-profile/3"
    elif mutation == "unknown_field": profile["raw_data"] = "CANARY"
    elif mutation == "usage": profile["usage_policy"] = "provider_observed"
    elif mutation == "digest_policy": profile["digest_policy"] = "raw_local_receipts"
    elif mutation == "provenance": profile["provenance"] = "live_model_qualified"
    elif mutation == "scope": profile["data_scope"] = "public_processed"
    elif mutation == "dispatch": profile["dispatch_event_count"] = 1
    elif mutation == "contexts": profile["context_count"] = 8
    elif mutation == "source_events": profile["source_event_count"] += 1
    elif mutation == "captures": profile["capture_event_count"] -= 1
    elif mutation == "operations": profile["operation_count"] += 1
    elif mutation == "terminal_set": profile["terminal_span_ids"] = [rows[0]["span_id"], *profile["terminal_span_ids"]]
    elif mutation == "open_set": profile["open_span_ids"] = []
    elif mutation == "row_count": rows.pop()
    elif mutation == "duplicate_alias": rows[1]["context_alias"] = rows[0]["context_alias"]
    elif mutation == "remote_alias": rows[1]["context_alias"] = rows[1]["span_id"]
    elif mutation == "parent_alias": rows[4]["parent_context_alias"] = rows[0]["context_alias"]
    elif mutation == "start_bool": rows[4]["start_observed"] = 0
    elif mutation == "digest_count": rows[1]["source_event_digests"].pop()
    elif mutation == "duplicate_digest": rows[1]["source_event_digests"][0] = rows[0]["source_event_digests"][0]
    elif mutation == "row_captures": rows[4]["capture_event_count"] = 3
    elif mutation == "terminal_status": rows[1]["terminal_status"] = "success"
    elif mutation == "invent_root_terminal":
        rows[0]["terminal_status"] = "completed"
        profile["terminal_span_ids"].insert(0, rows[0]["span_id"])
        profile["open_span_ids"] = []
        profile["operation_count"] += 1
    elif mutation == "private_row": rows[0]["local_context_id"] = "CANARY"
    elif mutation == "oversized_events":
        rows[0]["source_event_digests"] = [digest(canonical_json_bytes(index)) for index in range(65)]
        rows[0]["source_event_count"] = 65
        profile["source_event_count"] = sum(row["source_event_count"] for row in rows)
    with pytest.raises(ValueError) as caught:
        contract.validate_context_profile(profile, topology=scope["topology"])
    assert "CANARY" not in str(caught.value)


@pytest.mark.parametrize("key", ["source_digest", "runtime_digest", "policy_digest",
    "criteria_digest", "fixture_manifest_digest", "session_digest", "payload_digest"])
def test_v4_packet_requires_independent_binding(tmp_path, key):
    contract = _context_api()
    scope = make_context_scope(tmp_path, contract)
    packet = contract.prepare_context_packet(**scope)
    with pytest.raises(ValueError):
        context_checked(contract, packet, scope, expected_binding={**packet["binding"], key: "f" * 64})


@pytest.mark.parametrize("key,bad", [("max_spans", 16), ("max_source_events", 64),
    ("max_mutation_requests", 32), ("max_read_requests", 20), ("max_total_requests", 52),
    ("max_bytes", 262145), ("max_response_bytes", 131073), ("total_deadline_seconds", 241),
    ("max_delivery_attempts", 2), ("max_spans", True), ("total_deadline_seconds", float("inf"))])
def test_v4_packet_cannot_widen_its_profile_narrowed_limits(tmp_path, key, bad):
    contract = _context_api()
    scope = make_context_scope(tmp_path, contract)
    packet = contract.prepare_context_packet(**scope)
    packet["limits"][key] = bad
    with pytest.raises(ValueError):
        context_checked(contract, packet, scope)


@pytest.mark.parametrize("location,key,bad", [("packet", "operations", []),
    ("packet", "api_key", "CANARY"), ("packet", "authorize_upload", True),
    ("packet", "authorize_remote_read", True), ("packet", "schema_mode", "qualified"),
    ("session", "format", "devbench.rna-live-trace-session/3"),
    ("session", "authorize_upload", True), ("session", "max_spans", 16),
    ("session", "local_path", "/private/source"), ("session", "prompt", "CANARY"),
    ("live_session", "action", "target_read"), ("target_read", "allowed_operations", ["query_runs"])])
def test_v4_wire_refuses_private_fields_default_on_and_crosspurpose(tmp_path, location, key, bad):
    contract = _context_api()
    scope = make_context_scope(tmp_path, contract)
    packet = contract.prepare_context_packet(**scope)
    (packet if location == "packet" else packet[location])[key] = bad
    with pytest.raises((ValueError, PermissionError)) as caught:
        contract.validate_context_wire_packet(packet, now=scope["now"])
    assert "CANARY" not in str(caught.value)


def test_v4_blocked_default_and_separate_reviewed_handles_preserve_disabled_packet(tmp_path, monkeypatch):
    contract = _context_api()
    scope = make_context_scope(tmp_path, contract)
    packet = contract.prepare_context_packet(**scope)
    blocked = context_admitted(contract, packet, scope, {})
    assert blocked["status"] == "blocked"
    assert blocked["authorize_upload"] is blocked["authorize_remote_read"] is False
    approvals = context_handles(contract, packet, scope, tmp_path)
    result = context_admitted(contract, packet, scope, approvals)
    assert result["format"] == "devbench.rna-context-admission/4" and result["status"] == "admitted"
    assert result["authorize_upload"] is result["authorize_remote_read"] is True
    assert packet["authorize_upload"] is packet["session"]["authorize_upload"] is False
    assert contract.validate_context_approvals(packet, expected_binding=packet["binding"],
        expected_target=scope["target"], now=scope["now"], approval_handles=approvals) == result["approval_record_digests"]
    assert not Path(scope["evidence_path"]).exists()
    def forbidden(*args, **kwargs):
        pytest.fail("Offline context preparation read an operator input")
    monkeypatch.setattr(contract, "read_approval_handle", forbidden)
    assert contract.prepare_context_packet(**scope)["authorize_upload"] is False
    assert contract.validate_context_wire_packet(packet, now=scope["now"]) == packet


@pytest.mark.parametrize("mutation", ["partial", "swapped", "old_review", "old_action", "self_review",
    "self_operator", "expiry_drift", "public_input"])
def test_v4_approvals_do_not_inherit_v3_authority_or_allow_corruption(tmp_path, mutation):
    contract = _context_api()
    scope = make_context_scope(tmp_path, contract)
    packet = contract.prepare_context_packet(**scope)
    approvals = context_handles(contract, packet, scope, tmp_path)
    if mutation == "partial": approvals.pop("live_session")
    elif mutation == "swapped": approvals["target_read"], approvals["live_session"] = approvals["live_session"], approvals["target_read"]
    elif mutation == "old_review": rewrite_record(approvals, "source_runtime_review", lambda r: r.update(format="devbench.rna-workflow-review/3"))
    elif mutation == "old_action": rewrite_record(approvals, "live_session", lambda r: r.update(format="devbench.rna-hosted-action-approval/3"))
    elif mutation == "self_review": rewrite_record(approvals, "source_runtime_review", lambda r: r.update(reviewer_id=r["implementation_owner_id"]))
    elif mutation == "self_operator": rewrite_record(approvals, "live_session", lambda r: r.update(operator_id=r["implementation_owner_id"]))
    elif mutation == "expiry_drift": rewrite_record(approvals, "live_session", lambda r: r.update(expires_at=(scope["now"] + timedelta(minutes=6)).isoformat()))
    elif mutation == "public_input": Path(approvals["target_read"]["path"]).chmod(0o644)
    with pytest.raises((ValueError, PermissionError)):
        context_admitted(contract, packet, scope, approvals)


def test_v4_packet_refuses_expiry_drift_replay_existing_output_and_wrong_target(tmp_path):
    contract = _context_api()
    scope = make_context_scope(tmp_path, contract)
    packet = contract.prepare_context_packet(**scope)
    with pytest.raises(ValueError):
        context_checked(contract, packet, scope, now=scope["now"] + timedelta(minutes=6))
    with pytest.raises(ValueError):
        context_checked(contract, packet, scope, now=scope["now"] - timedelta(seconds=1))
    with pytest.raises(PermissionError):
        context_admitted(contract, packet, scope, {}, consumed_packet_ids=[packet["packet_id"]])
    with pytest.raises(ValueError):
        context_checked(contract, packet, scope, expected_target={**scope["target"], "project_id": str(uuid4())})
    Path(scope["evidence_path"]).mkdir()
    assert context_checked(contract, packet, scope, fresh_output=False) == packet
    assert contract.validate_context_wire_packet(packet, now=scope["now"]) == packet
    with pytest.raises(ValueError):
        context_checked(contract, packet, scope)


def test_v4_identity_is_bounded_installed_and_distinct_from_v3(monkeypatch):
    contract = _context_api()
    qualification = importlib.import_module("devbench.study.rna_hosted_qualification")
    monkeypatch.setattr(qualification, "bounded_installed_identity", lambda: dict(
        source_digest="a" * 64, runtime_digest="b" * 64, policy_digest="c" * 64))
    identity = contract.context_installed_identity()
    assert identity == dict(source_digest="a" * 64, runtime_digest="b" * 64,
        policy_digest="c" * 64, criteria_digest=contract.packet_digest(contract.CONTEXT_CRITERIA),
        fixture_manifest_digest=contract.packet_digest(contract.CONTEXT_FIXTURE))
    legacy = _api()
    assert identity["criteria_digest"] != contract.packet_digest(legacy.WORKFLOW_CRITERIA)
    assert identity["fixture_manifest_digest"] != contract.packet_digest(legacy.WORKFLOW_FIXTURE)


def test_v4_actual_identity_covers_context_contract_and_modified_runtime_modules():
    """GREEN must bind ordinary installed origins, RECORD and reproduction bytes."""
    import base64
    import csv
    import hashlib
    import io
    from importlib.metadata import distribution
    import sys
    import zipfile
    contract = _context_api()
    identity = contract.context_installed_identity()
    dist = distribution("devharness")
    record = Path(dist._path) / "RECORD"
    with record.open("rb") as stream:
        record_bytes = stream.read(2 * 1024 * 1024 + 1)
    assert len(record_bytes) <= 2 * 1024 * 1024
    assert hashlib.sha256(record_bytes).hexdigest() == identity["runtime_digest"]
    rows = {name: (expected, size) for name, expected, size in csv.reader(io.StringIO(record_bytes.decode()))}
    asset = Path(dist.locate_file("devbench/analysis/assets/source-bundle.zip"))
    with asset.open("rb") as stream:
        archive_bytes = stream.read(64 * 1024 * 1024 + 1)
    assert len(archive_bytes) <= 64 * 1024 * 1024
    assert hashlib.sha256(archive_bytes).hexdigest() == identity["source_digest"]
    required = ("rna_hosted_context_contract", "rna_hosted_workflow", "rna_hosted_exporter",
        "rna_langsmith_live", "rna_langsmith_verify", "langsmith_outbox", "rna_langsmith_export")
    prefix = Path(sys.prefix).resolve()
    with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
        for module in required:
            name = "devbench/study/" + module + ".py"
            expected, size = rows[name]
            installed = Path(dist.locate_file(name)).resolve()
            origin = Path(importlib.util.find_spec("devbench.study." + module).origin).resolve()
            assert origin == installed and installed.is_relative_to(prefix)
            with installed.open("rb") as stream:
                contents = stream.read(2_000_001)
            assert len(contents) <= 2_000_000 and len(contents) == int(size)
            encoded = base64.urlsafe_b64encode(hashlib.sha256(contents).digest()).decode().rstrip("=")
            assert expected == "sha256=" + encoded
            assert archive.getinfo(name).file_size <= 2_000_000
            with archive.open(name) as stream:
                assert stream.read(2_000_001) == contents


def test_v4_joined_verifier_criteria_are_an_explicit_technical_adapter():
    contract = _context_api()
    from devbench.study.rna_hosted_trace_contract import validate_criteria
    assert contract.CONTEXT_CRITERIA.get("verifier_stage_scope") == "technical_adapter_slot_only"
    assert contract.CONTEXT_CRITERIA.get("verifier_timing_provenance") == dict(
        origin="assistant_selected_engineering_defaults", user_prespecified=False,
        selected_before_live_context_measurement=True,
        qualification_scope="technical_adapter_only", external_requirement_record=None)
    expected = dict(format="devbench.rna-hosted-criteria/1",
        sample_matrix=[dict(fixture_id=contract.CONTEXT_FIXTURE["fixture_id"],
                           stage="pre_fit_methods", allocation=0, repeat=1)],
        api_p95_upper_seconds=5, visibility_deadline_seconds=5,
        poll_interval_seconds=.25, dashboard_window_seconds=30,
        max_extra_model_calls=0, max_extra_fits=0, max_extra_retrievals=0,
        max_source_writes=0, max_privacy_leaks=0, required_topology_fraction=1,
        censored_samples_fail=True, limits={key: value for key, value in contract.CONTEXT_CAPS.items()
            if key not in {"max_source_events", "max_total_requests", "max_response_bytes"}})
    assert contract.CONTEXT_CRITERIA.get("verifier_criteria") == expected
    assert validate_criteria(contract.CONTEXT_CRITERIA["verifier_criteria"]) == expected


def _readback_api():
    name = "devbench.study.rna_hosted_context_readback_contract"
    assert importlib.util.find_spec(name) is not None, "Separate GET-only historical context readback contract is absent"
    return importlib.import_module(name)


def make_readback_archive(tmp_path, count=9, *,
        seal_format="devbench.rna-context-history-seal/1", checkpoint_bytes=None,
        unacknowledged_terminal=False, duplicate_slots=False):
    """Construct an archive using only already retained public /5 interfaces.

    The construction clock ends before current admission. SQLite ACK rows are
    explicit synthetic fixture state. No hosted upload or prior evidence opens.
    """
    from unittest.mock import patch
    from types import SimpleNamespace
    import sqlite3
    from devbench.study import rna_hosted_context_contract as old
    from devbench.study import rna_langsmith_live as live
    from devbench.analysis import rna_trace_receipts as receipts
    from devbench.analysis.rna_trace_contract import trace_digest
    from devbench.study.rna_hosted_workflow import (
        prepare_generation_context_observation, GenerationContextReceiptBridge,
    )
    from devbench.study.langsmith_outbox import ExportOutbox
    assert 3 <= count <= 16
    assert type(unacknowledged_terminal) is bool
    assert type(duplicate_slots) is bool and (not duplicate_slots or count == 3)
    historical_now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    class HistoricalClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return historical_now if tz is not None else historical_now.replace(tzinfo=None)
    root = tmp_path / "historical-producer"
    root.mkdir()
    original_identity = dict(source_digest="a" * 64, runtime_digest="b" * 64, policy_digest="c" * 64)
    original_identity.update(criteria_digest=old.packet_digest(old.GENERATION_CONTEXT_CRITERIA),
        fixture_manifest_digest=old.packet_digest(old.GENERATION_CONTEXT_FIXTURE))
    with patch.object(live, "datetime", HistoricalClock), patch.object(receipts, "datetime", HistoricalClock):
        observer = receipts.LocalTraceObserver(root / "source" / "observer.sqlite",
            **{key: original_identity[key] for key in ("source_digest", "runtime_digest", "policy_digest")})
        workflow_id = "synthetic-readback-history"
        binding = dict(source_digest=original_identity["source_digest"], runtime_digest=original_identity["runtime_digest"],
            invocation_id=str(uuid4()), purpose="generate")
        reservation = SimpleNamespace(request=binding, request_digest=trace_digest(binding),
            attempt=0, policy_digest=original_identity["policy_digest"])
        reference = observer.prepare_invocation(workflow_id, binding, reservation)
        assert reference is not None
        invocation = receipts.InvocationTraceReceipt(reference, binding_digest=reservation.request_digest,
            private_root=observer.path.parent,
            **{key: original_identity[key] for key in ("source_digest", "runtime_digest", "policy_digest")})
        invocation.emit("dispatch", "active", dispatch="attempted")
        capture_phases = ("selection", "audit", "question", "design", "declare", "resolve", "verify",
            "review", "synthesis", "recommend", "investigate", "attach", "reassess", "execution_review")
        for phase in (() if duplicate_slots else capture_phases[:count - 2]):
            invocation.emit("capture", "completed", phase=phase, capture="verified", dispatch="not_dispatched")
        invocation.emit("failure", "failed", reason="failed", dispatch="attempted")
        assert observer.failure_count == 0 and invocation.observer.failure_count == 0
        if duplicate_slots:
            # Repeated slots are legitimate /5 contexts. Their distinct raw
            # invocation bindings and observed times survive as distinct source
            # proofs without exporting the private binding itself.
            historical_now += timedelta(seconds=1)
            second_binding = {**binding, "invocation_id": str(uuid4())}
            second_reservation = SimpleNamespace(request=second_binding,
                request_digest=trace_digest(second_binding), attempt=0,
                policy_digest=original_identity["policy_digest"])
            second_reference = observer.prepare_invocation(workflow_id, second_binding, second_reservation)
            assert second_reference is not None
            second = receipts.InvocationTraceReceipt(second_reference,
                binding_digest=second_reservation.request_digest, private_root=observer.path.parent,
                **{key: original_identity[key] for key in ("source_digest", "runtime_digest", "policy_digest")})
            second.emit("dispatch", "active", dispatch="attempted")
            second.emit("failure", "failed", reason="failed", dispatch="attempted")
            assert observer.failure_count == 0 and second.observer.failure_count == 0
        observation = prepare_generation_context_observation(observer=observer, workflow_id=workflow_id)
        topology, profile = observation["topology"], observation["profile"]
        assert len(topology) == count and profile["source_event_count"] == (7 if duplicate_slots else count + 2)
        session = dict(format="devbench.rna-live-trace-session/5", workflow_alias=str(uuid4()),
            workflow_binding_digest=trace_digest(workflow_id), data_scope="synthetic",
            **{key: original_identity[key] for key in ("source_digest", "runtime_digest", "policy_digest")},
            endpoint=old.TARGET["endpoint"], project=old.TARGET["project_name"],
            project_id=old.TARGET["project_id"], workspace_id=old.TARGET["workspace_id"],
            redaction_version="rna-allowlist/1", event_schema="devbench.rna-trace-event/1",
            allowed_event_fields=sorted(EVENT_FIELDS), max_spans=count, max_bytes=262144,
            max_delivery_attempts=1, expires_at=(historical_now + timedelta(minutes=5)).isoformat(),
            authorize_upload=False, span_topology=topology, context_profile=profile)
        packet = old.prepare_generation_context_packet(installed_identity=original_identity,
            target=old.TARGET, session=session, topology=session["span_topology"], profile=profile,
            evidence_path=str(root / "original-output"), expires_at=session["expires_at"], now=historical_now)
        session = {**deepcopy(session), "authorize_upload": True}
        bridge = GenerationContextReceiptBridge(authorized_session_manifest=session,
            output_path=root / "operations.jsonl")
        operations = bridge.emit_new(observer=observer, workflow_id=workflow_id)
        seed = root / "seed" / "payload.json"
        seed.parent.mkdir()
        seed.write_bytes(canonical_json_bytes(dict(record_format="devbench.rna-langsmith-export/1",
            privacy_policy="rna-allowlist/1", data_scope="synthetic", runs=[operations[0]["payload"]])))
        outbox = root / "journal" / "outbox.sqlite"
        box = ExportOutbox.create_live(outbox, payload_path=seed,
            expected_sha256=digest(seed.read_bytes()), authorized_session_manifest=session,
            initial_operations=[operations[0]])
        for item in operations[1:]:
            box.enqueue_operation(item["operation"], item["payload"], max_spans=count, max_bytes=262144)
        # Seed the synthetic historical ACK archive. Current readback exercises
        # the real readonly SQLite schema; it never invokes upload authority.
        with sqlite3.connect(outbox) as db:
            for item in operations:
                op = item["operation"]
                if unacknowledged_terminal and op["operation"] == "finish":
                    continue
                receipt_digest = digest(canonical_json_bytes(dict(operation_id=op["operation_id"], status_code=202)))
                db.execute("UPDATE operations SET state='acknowledged',attempt_count=1 WHERE operation_id=?", (op["operation_id"],))
                db.execute("INSERT INTO attempts VALUES(?,?,?,?,?,?)", (str(uuid4()), op["operation_id"],
                    historical_now.timestamp(), historical_now.timestamp(), "acknowledged", receipt_digest))
                db.execute("UPDATE spans SET state='acknowledged',attempt_count=1,response_class='accepted',last_receipt_digest=? WHERE span_id=?", (receipt_digest, op["span_id"]))
            db.execute("UPDATE exports SET state=?", ("prepared" if unacknowledged_terminal else "acknowledged",))
            acknowledged = {row[0] for row in db.execute("SELECT operation_id FROM operations WHERE state='acknowledged'")}
        db.close()
        expected_acknowledged = sum(not (unacknowledged_terminal and item["operation"]["operation"] == "finish") for item in operations)
        assert box.status()["counts"]["acknowledged"] == expected_acknowledged
    latest = {item["payload"]["id"]: item for item in operations if item["operation"]["operation_id"] in acknowledged}
    latest_items = [latest[slot["span_id"]] for slot in session["span_topology"]]
    documents = dict(packet=packet)
    owner, reviewer = str(uuid4()), str(uuid4())
    documents["source_runtime_review"] = dict(format="devbench.rna-context-review/5",
        packet_digest=digest(canonical_json_bytes(packet)), binding=packet["binding"], target=packet["target"],
        approved=True, reviewer_id=reviewer, implementation_owner_id=owner, expires_at=session["expires_at"])
    for action in ("target_read", "live_session"):
        documents[action] = dict(format="devbench.rna-hosted-action-approval/5",
            proposal_digest=digest(canonical_json_bytes(packet[action])), action=action, approved=True,
            operator_id=reviewer, implementation_owner_id=owner, expires_at=session["expires_at"])
    history = dict(root=str(root))
    for name, document in documents.items():
        path = root / (name + ".json")
        path.write_bytes(canonical_json_bytes(document))
    checkpoint = root / "checkpoint.bin"
    checkpoint.write_bytes(checkpoint_bytes if checkpoint_bytes is not None else canonical_json_bytes(
        dict(format="devbench.rna-context-history-checkpoint/1", note="Opaque retained engineering checkpoint")))
    paths = {name: root / (name + ".json") for name in documents}
    paths.update(operations=root / "operations.jsonl", outbox=outbox, source=observer.path, checkpoint=checkpoint)
    for name, path in paths.items():
        history[name] = dict(path=str(path), sha256=digest(path.read_bytes()))
    seal = dict(format=seal_format, status="failed",
        original_api_exact_revision_count=1, original_api_total_count=count,
        original_status_values_retained=False, artifacts=[])
    for path in sorted(root.rglob("*")):
        if path.is_file():
            seal["artifacts"].append(dict(path=str(path), sha256=digest(path.read_bytes()), bytes=path.stat().st_size))
    seal_path = root / "failure_seal.json"
    seal_path.write_bytes(canonical_json_bytes(seal))
    history["failure_seal"] = dict(path=str(seal_path), sha256=digest(seal_path.read_bytes()))
    producer = {key: session[key] for key in ("source_digest", "runtime_digest", "policy_digest")}
    producer.update(packet_digest=history["packet"]["sha256"], session_digest=digest(canonical_json_bytes(session)),
        topology_digest=digest(canonical_json_bytes(session["span_topology"])),
        operations_digest=history["operations"]["sha256"], latest_acknowledged_digest=digest(canonical_json_bytes(latest_items)),
        failure_seal_digest=history["failure_seal"]["sha256"])
    now = datetime.now(timezone.utc)
    return dict(producer_binding=producer, target=deepcopy(old.TARGET), session=session,
        topology=deepcopy(session["span_topology"]), history=history,
        evidence_path=str(tmp_path / "fresh-readback"), expires_at=(now + timedelta(minutes=5)).isoformat(), now=now)


def make_readback_scope(tmp_path, count=9, *,
        seal_format="devbench.rna-context-history-seal/1", checkpoint_bytes=None,
        unacknowledged_terminal=False, duplicate_slots=False):
    archive = make_readback_archive(tmp_path, count=count, seal_format=seal_format,
        checkpoint_bytes=checkpoint_bytes, unacknowledged_terminal=unacknowledged_terminal,
        duplicate_slots=duplicate_slots)
    return dict(installed_identity=_readback_api().readback_installed_identity(), **archive)


def test_context_readback_old_public_archive_builder_selfcheck(tmp_path):
    """Runs on the original product without any new readback API present."""
    import sqlite3
    from devbench.analysis.rna_trace_receipts import LocalTraceObserver
    from devbench.study.rna_hosted_context_contract import validate_generation_context_profile
    from devbench.study.rna_langsmith_live import validate_live_session
    archive = make_readback_archive(tmp_path, count=9)
    history, session = archive["history"], archive["session"]
    assert "session" not in history
    source, outbox = Path(history["source"]["path"]), Path(history["outbox"]["path"])
    observer = LocalTraceObserver.open_existing(source)
    events = observer.read_events("synthetic-readback-history")
    contexts = observer.read_contexts("synthetic-readback-history")
    assert len(events) == 11 and len(contexts) == 9
    assert session["workflow_binding_digest"] == events[0]["bindings"]["workflow"]
    assert validate_generation_context_profile(session["context_profile"], topology=session["span_topology"]) == session["context_profile"]
    with pytest.raises(ValueError, match="expired"):
        validate_live_session(session)
    with sqlite3.connect(outbox.as_uri() + "?mode=ro", uri=True) as db:
        retained, retained_digest = db.execute("SELECT manifest,manifest_sha256 FROM live_session").fetchone()
        assert json.loads(retained) == session
        assert digest(retained) == retained_digest == archive["producer_binding"]["session_digest"]
        assert db.execute("SELECT count(*) FROM operations WHERE state='acknowledged'").fetchone()[0] == 10
        seed_path, seed_digest = db.execute("SELECT payload_path,payload_sha256 FROM exports").fetchone()
        seed = Path(seed_path)
        assert digest(seed.read_bytes()) == seed_digest
        assert seed.parent != outbox.parent and not seed.is_relative_to(outbox.parent)
        assert not outbox.is_relative_to(seed.parent)
    db.close()
    seal = json.loads(Path(history["failure_seal"]["path"]).read_bytes())
    assert seal["format"] == "devbench.rna-context-history-seal/1"
    members = {row["path"]: row for row in seal["artifacts"]}
    root = Path(history["root"])
    expected_files = {str(path) for path in root.rglob("*") if path.is_file()} - {history["failure_seal"]["path"]}
    assert set(members) == expected_files
    assert str(seed) in members and str(source) in members and str(outbox) in members
    assert any(".sidecars/" in name for name in members)
    for name, member in members.items():
        assert digest(Path(name).read_bytes()) == member["sha256"]
        assert Path(name).stat().st_size == member["bytes"]


def readback_handles(tmp_path, contract, packet):
    owner, reviewer = str(uuid4()), str(uuid4())
    common = dict(reader_binding=packet["reader_binding"], producer_binding=packet["producer_binding"],
        target=packet["target"], evidence_path=packet["evidence_path"], nonce=packet["nonce"],
        expected_historical_root=packet["producer_binding"]["failure_seal_digest"],
        run_ids=packet["read_descriptor"]["run_ids"], limits=packet["limits"],
        expires_at=packet["expires_at"], approved=True, implementation_owner_id=owner)
    records = dict(source_runtime_review=dict(format="devbench.rna-context-readback-review/1",
        packet_digest=contract.packet_digest(packet), reviewer_id=reviewer, **common),
        readback=dict(format="devbench.rna-context-readback-action-approval/1",
            proposal_digest=contract.packet_digest(packet["readback"]), action="readback", operator_id=reviewer, **common))
    result = {}
    for name, record in records.items():
        path = tmp_path / ("readback-" + name + ".json")
        path.write_bytes(canonical_json_bytes(record)); path.chmod(0o600)
        result[name] = dict(path=str(path), sha256=digest(path.read_bytes()))
    return result


def readback_checked(contract, packet, scope, **changes):
    kwargs = dict(installed_identity=scope["installed_identity"],
        expected_producer_binding=scope["producer_binding"], expected_target=scope["target"], now=scope["now"])
    kwargs.update(changes)
    return contract.validate_context_readback_packet(packet, **kwargs)


def readback_admitted(contract, packet, scope, handles, **changes):
    kwargs = dict(installed_identity=scope["installed_identity"], expected_producer_binding=scope["producer_binding"],
        expected_target=scope["target"], now=scope["now"], approval_handles=handles)
    kwargs.update(changes)
    return contract.admit_context_readback_packet(packet, **kwargs)


@pytest.mark.parametrize("count", [3, 9, 16])
def test_context_readback_fresh_reader_preserves_expired_producer_and_generic_profile(tmp_path, count):
    contract = _readback_api()
    scope = make_readback_scope(tmp_path, count=count)
    before = {path: (path.read_bytes(), path.stat()) for path in Path(scope["history"]["root"]).rglob("*") if path.is_file()}
    packet = contract.prepare_context_readback_packet(**scope)
    assert readback_checked(contract, packet, scope) == packet
    assert packet["format"] == "devbench.rna-hosted-context-readback/1"
    assert packet["reader_binding"] == scope["installed_identity"]
    assert packet["producer_binding"] == scope["producer_binding"]
    assert "session" not in packet["history"]
    assert packet["reader_binding"]["source_digest"] != packet["producer_binding"]["source_digest"]
    assert packet["session"] == scope["session"]
    assert packet["session"]["expires_at"] < packet["prepared_at"]
    assert packet["session"]["authorize_upload"] is True  # Original fact, never current authority.
    assert packet["authorize_upload"] is packet["authorize_remote_read"] is False
    assert packet["readback"]["allowed_operations"] == ["read_project", "read_run"]
    assert "live_session" not in packet and "target_read" not in packet
    ids = [slot["span_id"] for slot in scope["topology"]]
    descriptor = packet["read_descriptor"]
    assert descriptor["run_ids"] == ids
    assert descriptor["run_routes"] == ["/api/v1/runs/" + identity for identity in ids]
    assert descriptor["project_route"] == "/api/v1/sessions/" + scope["target"]["project_id"] + "?include_stats=false"
    assert "query_route" not in descriptor
    assert packet["limits"] == dict(max_spans=count, max_source_events=count + 2,
        max_mutation_requests=0, max_read_requests=count + 1, max_total_requests=count + 1,
        max_response_bytes=131072, total_deadline_seconds=60, request_timeout_seconds=15,
        project_max_age_seconds=30, max_delivery_attempts=0)
    assert not Path(scope["evidence_path"]).exists()
    for path, (raw, stat) in before.items():
        assert path.read_bytes() == raw
        assert path.stat() == stat


@pytest.mark.parametrize("binding,field", [("reader", "source_digest"), ("reader", "runtime_digest"),
    ("reader", "policy_digest"), ("producer", "source_digest"), ("producer", "runtime_digest"),
    ("producer", "policy_digest"), ("producer", "packet_digest"), ("producer", "session_digest"),
    ("producer", "topology_digest"), ("producer", "operations_digest"),
    ("producer", "latest_acknowledged_digest"), ("producer", "failure_seal_digest")])
def test_context_readback_independent_reader_producer_and_seal_bindings_cannot_drift(tmp_path, binding, field):
    contract = _readback_api(); scope = make_readback_scope(tmp_path, count=3)
    packet = contract.prepare_context_readback_packet(**scope)
    argument = "installed_identity" if binding == "reader" else "expected_producer_binding"
    original = scope["installed_identity"] if binding == "reader" else scope["producer_binding"]
    with pytest.raises(ValueError):
        readback_checked(contract, packet, scope, **{argument: {**original, field: "0" * 64}})


@pytest.mark.parametrize("mutation", ["upload", "read", "session_expiry", "producer_rebase", "topology",
    "history_ref", "history_root", "read_route", "run_allowlist", "mutations", "polls", "source_events", "body_cap", "deadline"])
def test_context_readback_packet_rejects_authority_history_topology_and_budget_substitution(tmp_path, mutation):
    contract = _readback_api(); scope = make_readback_scope(tmp_path, count=3)
    packet = contract.prepare_context_readback_packet(**scope)
    if mutation == "upload": packet["authorize_upload"] = True
    elif mutation == "read": packet["authorize_remote_read"] = True
    elif mutation == "session_expiry": packet["session"]["expires_at"] = packet["expires_at"]
    elif mutation == "producer_rebase": packet["session"]["runtime_digest"] = packet["reader_binding"]["runtime_digest"]
    elif mutation == "topology": packet["topology"][1]["parent_span_id"] = None
    elif mutation == "history_ref": packet["history"]["operations"]["sha256"] = "0" * 64
    elif mutation == "history_root": packet["history"]["root"] = str(tmp_path)
    elif mutation == "read_route": packet["read_descriptor"]["run_routes"][0] = "/api/v1/runs/query"
    elif mutation == "run_allowlist": packet["read_descriptor"]["run_ids"].append(str(uuid4()))
    elif mutation == "mutations": packet["limits"]["max_mutation_requests"] = 1
    elif mutation == "polls": packet["limits"]["max_read_requests"] += 1
    elif mutation == "source_events": packet["limits"]["max_source_events"] += 1
    elif mutation == "body_cap": packet["limits"]["max_response_bytes"] += 1
    else: packet["limits"]["total_deadline_seconds"] += 1
    with pytest.raises(ValueError): readback_checked(contract, packet, scope)


def test_context_readback_only_fresh_purpose_specific_private_approvals_admit(tmp_path):
    contract = _readback_api(); scope = make_readback_scope(tmp_path, count=3)
    packet = contract.prepare_context_readback_packet(**scope)
    blocked = readback_admitted(contract, packet, scope, {})
    assert blocked["status"] == "blocked"
    assert blocked["authorize_upload"] is blocked["authorize_remote_read"] is False
    handles = readback_handles(tmp_path, contract, packet)
    result = readback_admitted(contract, packet, scope, handles)
    assert result["status"] == "admitted" and result["authorize_remote_read"] is True
    assert result["authorize_upload"] is False
    with pytest.raises((ValueError, PermissionError)):
        readback_admitted(contract, packet, scope, handles, consumed_packet_ids=[packet["packet_id"]])
    with pytest.raises(ValueError):
        readback_admitted(contract, packet, scope, handles, now=scope["now"] + timedelta(minutes=6))


@pytest.mark.parametrize("mutation", ["old_handles", "producer", "reader", "nonce", "seal", "output", "runs", "limits", "expired", "self_review", "mode", "hardlink", "symlink", "alias"])
def test_context_readback_approval_cannot_self_endorse_reuse_or_substitute_scope(tmp_path, mutation):
    import os
    contract = _readback_api(); scope = make_readback_scope(tmp_path, count=3)
    packet = contract.prepare_context_readback_packet(**scope)
    handles = readback_handles(tmp_path, contract, packet)
    if mutation == "old_handles":
        handles = {name: scope["history"][name] for name in ("source_runtime_review", "target_read", "live_session")}
    elif mutation == "mode": Path(handles["readback"]["path"]).chmod(0o644)
    elif mutation == "hardlink": os.link(handles["readback"]["path"], tmp_path / "alias.json")
    elif mutation == "symlink":
        alias = tmp_path / "symlink.json"; alias.symlink_to(handles["readback"]["path"])
        handles["readback"]["path"] = str(alias)
    elif mutation == "alias": handles["readback"] = handles["source_runtime_review"]
    else:
        def update(record):
            if mutation == "producer": record["producer_binding"]["runtime_digest"] = "0" * 64
            elif mutation == "reader": record["reader_binding"]["source_digest"] = "0" * 64
            elif mutation == "nonce": record["nonce"] = str(uuid4())
            elif mutation == "seal": record["expected_historical_root"] = "0" * 64
            elif mutation == "output": record["evidence_path"] = str(tmp_path / "elsewhere")
            elif mutation == "runs": record["run_ids"] = [str(uuid4())]
            elif mutation == "limits": record["limits"]["max_total_requests"] += 1
            elif mutation == "expired": record["expires_at"] = scope["session"]["expires_at"]
            else: record["operator_id"] = record["implementation_owner_id"]
        rewrite_record(handles, "readback", update)
    with pytest.raises((ValueError, PermissionError)):
        readback_admitted(contract, packet, scope, handles)


def test_context_readback_rehashed_history_cannot_replace_independently_approved_root(tmp_path):
    contract = _readback_api(); scope = make_readback_scope(tmp_path, count=3)
    original = contract.prepare_context_readback_packet(**scope)
    protected_handles = readback_handles(tmp_path, contract, original)
    protected_bytes = {name: Path(handle["path"]).read_bytes() for name, handle in protected_handles.items()}
    original_root = scope["producer_binding"]["failure_seal_digest"]
    forged = deepcopy(scope)
    old_seal = json.loads(Path(scope["history"]["failure_seal"]["path"]).read_bytes())
    old_seal["original_api_exact_revision_count"] = old_seal["original_api_total_count"]
    path = Path(scope["history"]["root"]) / "self-endorsed-seal.json"
    path.write_bytes(canonical_json_bytes(old_seal))
    forged["history"]["failure_seal"] = dict(path=str(path), sha256=digest(path.read_bytes()))
    forged["producer_binding"]["failure_seal_digest"] = forged["history"]["failure_seal"]["sha256"]
    assert forged["producer_binding"]["failure_seal_digest"] != original_root
    forged_packet = contract.prepare_context_readback_packet(**forged)
    # The launcher's reviewed producer root remains independent of packet refs.
    with pytest.raises(ValueError):
        readback_admitted(contract, forged_packet, scope, protected_handles)
    # Even rebasing an untrusted expected-binding argument cannot replace the
    # protected new review/action records which still bind the original root.
    with pytest.raises((ValueError, PermissionError)):
        readback_admitted(contract, forged_packet, forged, protected_handles)
    for name, handle in protected_handles.items():
        assert Path(handle["path"]).read_bytes() == protected_bytes[name]


@pytest.mark.parametrize("destination", ["historical_root", "new_child", "original_outbox_parent"])
def test_context_readback_output_cannot_overlap_original_history_even_with_agreeing_scope(tmp_path, destination):
    contract = _readback_api(); scope = make_readback_scope(tmp_path, count=3)
    root = Path(scope["history"]["root"])
    output = root if destination == "historical_root" else root / "new-readback" if destination == "new_child" else Path(scope["history"]["outbox"]["path"]).parent
    before = {path: path.read_bytes() for path in root.rglob("*") if path.is_file()}
    with pytest.raises(ValueError):
        contract.prepare_context_readback_packet(**{**scope, "evidence_path": str(output)})
    assert {path: path.read_bytes() for path in root.rglob("*") if path.is_file()} == before
    assert not (root / "new-readback").exists()
