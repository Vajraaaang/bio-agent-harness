"""Offline coordinator-job accounting proofs; no credentials or client launches."""

from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from threading import Barrier

import pytest

from devbench.analysis.codex_job_policy import validate_job_policy, validate_job_limits
from devbench.analysis.workflow_operations import (
    OperationBlocked, OperationConflict, WorkflowOperationLedger,
)


def _policy(**updates):
    return {"format": "devbench.model-job-budget-policy/1", "kind": "generation_jobs",
            "max_jobs": 6, "max_concurrent_jobs": 1, "max_total_seconds": 300,
            "max_context_bytes": 32768, "max_output_bytes": 131072, "max_children": 0,
            "reconciliation": {"jobs": 1, "seconds": 30, "context_bytes": 4096}, **updates}


def _capability(**updates):
    return {"backend": "enforced-offline-job-fixture", "backend_digest": "b" * 64,
            "runtime_digest": "c" * 64, "capability_status": "offline_qualified",
            "generation_job_accounting": True, "context_bytes_enforced": True,
            "output_bytes_enforced": True, "outer_timeout_enforced": True,
            "process_reaping_enforced": True, "max_concurrent_jobs": 1,
            "child_command_or_numerical_tools_allowed": False, **updates}


def _open(tmp_path, campaign="campaign", *, policy=None, identity=None, capability=None):
    ledger = WorkflowOperationLedger(tmp_path / "operations.sqlite")
    ledger.open_campaign(campaign, session_id="parent-" + campaign,
        policy=policy or _policy(), capability=capability or _capability(),
        identity=identity or {"test": "offline-only"})
    return ledger


def _limits(**updates):
    return {"jobs": 1, "seconds": 30, "context_bytes": 4096, "output_bytes": 65536, **updates}


def _request(node, campaign="campaign", **updates):
    return {"format": "devbench.model-call-request/1", "parent_session_id": "parent-" + campaign,
            "session_id": campaign + "-session-" + node,
            "invocation_id": campaign + "-invocation-" + node,
            "backend_digest": "b" * 64, "runtime_digest": "c" * 64,
            "prompt_digest": "e" * 64, "schema_digest": "f" * 64,
            "context_digest": "a" * 64, **updates}


def _reserve(ledger, node="one", campaign="campaign", *, limits=None, purpose="generation", request=None, **updates):
    return ledger.reserve_model_call(campaign, node, 0, request or _request(node, campaign),
        limits=limits or _limits(), purpose=purpose, **updates)


def _capture(tmp_path, reservation, **updates):
    request = reservation.request
    payload = {"format": "devbench.codex-job-capture/1", "reservation_ref": reservation.ref,
               "request_digest": reservation.request_digest, "launch_intent": True,
               "launch_confirmed": True, "status": "completed", "process_reaped": True,
               "job_count": 1, "elapsed_seconds": 2,
               "native_session_ids": ["native-" + request["invocation_id"]],
               "provenance": {key: request[key] for key in (
                   "session_id", "invocation_id", "backend_digest", "runtime_digest",
                   "prompt_digest", "schema_digest", "context_digest")}, **updates}
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    digest = hashlib.sha256(raw).hexdigest()
    path = tmp_path / (digest + ".json")
    path.write_bytes(raw)
    reference = {"artifact_id": path.name, "artifact_type": "rna_investigation_capture",
                 "sha256": digest, "size_bytes": len(raw)}

    def verify(ref, retained):
        actual = (tmp_path / ref["artifact_id"]).read_bytes()
        if hashlib.sha256(actual).hexdigest() != ref["sha256"] or len(actual) != ref["size_bytes"]:
            raise OperationConflict("Immutable capture changed")
        return {**json.loads(actual), "capture_ref": ref}

    return reference, verify


def _settle(tmp_path, ledger, reservation, **updates):
    reference, verify = _capture(tmp_path, reservation, **updates)
    return ledger.settle_model_call(reservation, capture=reference, verify_capture=verify)


def test_completed_local_job_without_usage_settles_and_does_not_invent_tokens(tmp_path):
    ledger = _open(tmp_path)
    reservation = _reserve(ledger)
    ledger.activate_model_call(reservation)
    receipt = _settle(tmp_path, ledger, reservation)
    assert receipt["status"] == "completed"
    assert "usage" not in receipt["capture"]
    campaign = ledger.get_campaign("campaign")
    assert campaign["verified_jobs"] == 1
    assert campaign["outstanding_reserved_jobs"] == 0
    assert campaign["qualification"] == "complete"
    assert not {"verified_calls", "verified_spent_tokens", "hard_token_guarantee"} & set(campaign)
    assert ledger.activate_model_call(_reserve(ledger, "two")).status == "active"


@pytest.mark.parametrize("usage", [{}, {"output_tokens": 17}, {"total_tokens": 55},
                                    {"normalized_total_tokens": None, "native_usage": {}}])
def test_optional_reported_usage_is_retained_without_changing_accounting_unit(tmp_path, usage):
    ledger = _open(tmp_path)
    reservation = _reserve(ledger)
    ledger.activate_model_call(reservation)
    record = _settle(tmp_path, ledger, reservation, usage=usage)
    assert record["status"] == "completed"
    assert record["capture"]["usage"] == usage
    assert ledger.get_campaign("campaign")["accounting_kind"] == "generation_jobs"
    assert ledger.get_campaign("campaign")["verified_jobs"] == 1


@pytest.mark.parametrize("update", [
    {"max_total_tokens": 1}, {"max_calls": 1}, {"max_internal_requests": 1},
    {"max_credits": 1}, {"max_spend_usd": 1}, {"max_concurrent_jobs": 2},
    {"max_children": 1}, {"kind": "hard_total_tokens"},
])
def test_job_policy_rejects_unsupported_required_caps_and_parallelism(update):
    with pytest.raises(ValueError):
        validate_job_policy(_policy(**update))


@pytest.mark.parametrize("updates", [{"jobs": 0}, {"jobs": 2}, {"jobs": True},
    {"tokens": 1}, {"calls": 1}, {"seconds": 0}, {"seconds": float("inf")}])
def test_exactly_one_job_per_closed_reservation(updates):
    with pytest.raises(ValueError):
        validate_job_limits(_limits(**updates))


def test_reservation_is_atomic_and_idempotent_in_existing_workflow_ledger(tmp_path):
    ledger = _open(tmp_path)
    reservation = _reserve(ledger)
    assert _reserve(ledger) == reservation
    assert ledger.get("campaign", "one", 0)["request_digest"] == reservation.request_digest
    assert len(ledger.campaign_reservations("campaign")) == 1
    with pytest.raises(OperationConflict):
        _reserve(ledger, limits=_limits(seconds=31))
    with pytest.raises(OperationConflict):
        _reserve(ledger, request=_request("one", prompt_digest="1" * 64))


def test_job_limit_keeps_reconciliation_slot_and_failed_launch_never_refunds(tmp_path):
    ledger = _open(tmp_path, policy=_policy(max_jobs=2))
    first = _reserve(ledger)
    ledger.activate_model_call(first)
    failed = _settle(tmp_path, ledger, first, status="not_started", launch_confirmed=False,
        native_session_ids=[], elapsed_seconds=0)
    assert failed["status"] == "failed"
    assert failed["charge_basis"] == "verified_launch_intent"
    with pytest.raises(OperationBlocked, match="budget_exhausted"):
        _reserve(ledger, "retry")
    parent = _reserve(ledger, "reconcile", purpose="parent_reconciliation")
    ledger.activate_model_call(parent)
    _settle(tmp_path, ledger, parent)
    assert ledger.get_campaign("campaign")["verified_jobs"] == 2
    assert ledger.get_campaign("campaign")["reconciliation_held"] == {"jobs": 0, "seconds": 0, "context_bytes": 0}
    with pytest.raises(OperationBlocked, match="budget_exhausted"):
        _reserve(ledger, "third")


def test_activation_slot_is_global_across_campaigns_and_ledger_connections(tmp_path):
    one = _open(tmp_path, "one")
    two = _open(tmp_path, "two")
    first = _reserve(one, campaign="one")
    second = _reserve(two, campaign="two")
    one.activate_model_call(first)
    assert two.model_call_remaining(second) == 0
    with pytest.raises(OperationBlocked, match="job_concurrency_exhausted"):
        two.activate_model_call(second)
    _settle(tmp_path, one, first)
    assert two.activate_model_call(second).status == "active"


def test_competing_coordinators_cannot_activate_two_jobs(tmp_path):
    ledgers = [_open(tmp_path, "a"), _open(tmp_path, "b")]
    reservations = [_reserve(ledger, campaign=name) for ledger, name in zip(ledgers, ("a", "b"))]
    barrier = Barrier(2)

    def activate(index):
        barrier.wait()
        try:
            ledgers[index].activate_model_call(reservations[index])
            return "active"
        except OperationBlocked as exc:
            return exc.state

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(activate, (0, 1)))
    assert results.count("active") == 1
    assert set(results) <= {"active", "job_concurrency_exhausted", "ledger_busy"}
    records = [ledger.get_model_reservation(reservation) for ledger, reservation in zip(ledgers, reservations)]
    assert sum(record["status"] == "active" for record in records) == 1


@pytest.mark.parametrize("capture_updates", [{"status": "uncertain"}, {"process_reaped": False}])
def test_uncertain_or_unreaped_job_holds_global_slot_until_exact_reconciliation(tmp_path, capture_updates):
    one = _open(tmp_path, "one")
    two = _open(tmp_path, "two")
    first = _reserve(one, campaign="one")
    second = _reserve(two, campaign="two")
    one.activate_model_call(first)
    unsettled = _settle(tmp_path, one, first, **capture_updates)
    assert unsettled["status"] == "uncertain"
    assert one.get_campaign("one")["verified_jobs"] == 1
    assert one.get_campaign("one")["qualification"] == "incomplete"
    with pytest.raises(OperationBlocked, match="unresolved_job_lifecycle"):
        two.activate_model_call(second)
    reopened = WorkflowOperationLedger(tmp_path / "operations.sqlite")
    with pytest.raises(OperationBlocked, match="unresolved_job_lifecycle"):
        _reserve(reopened, "new", campaign="two")
    reference, verify = _capture(tmp_path, first, elapsed_seconds=3)
    assert one.reconcile_model_call(first, capture=reference, verify_capture=verify)["status"] == "completed"
    assert two.activate_model_call(second).status == "active"


def test_surviving_activation_never_retries_or_returns_budget(tmp_path):
    ledger = _open(tmp_path)
    reservation = _reserve(ledger)
    ledger.activate_model_call(reservation)
    reopened = WorkflowOperationLedger(tmp_path / "operations.sqlite")
    with pytest.raises(OperationBlocked, match="uncertain"):
        reopened.activate_model_call(reservation)
    assert reopened.get_model_reservation(reservation)["status"] == "uncertain"
    assert reopened.get_campaign("campaign")["outstanding_reserved_jobs"] == 1
    with pytest.raises(OperationBlocked, match="unresolved_job_lifecycle"):
        _reserve(reopened, "next")


def test_cancellation_preserves_active_debit_and_blocks_future_launch(tmp_path):
    one = _open(tmp_path, "one")
    two = _open(tmp_path, "two")
    active = _reserve(one, campaign="one")
    unlaunched = _reserve(one, "unlaunched", campaign="one")
    other = _reserve(two, campaign="two")
    one.activate_model_call(active)
    one.cancel_campaign("one")
    assert one.get_model_reservation(unlaunched)["status"] == "cancelled"
    assert one.get_campaign("one")["outstanding_reserved_jobs"] == 1
    with pytest.raises(OperationBlocked, match="job_concurrency_exhausted"):
        two.activate_model_call(other)
    with pytest.raises(OperationBlocked, match="cancelled"):
        one.activate_model_call(unlaunched)
    _settle(tmp_path, one, active)
    assert one.get_campaign("one")["verified_jobs"] == 1
    assert two.activate_model_call(other).status == "active"


def test_cancelled_unactivated_reservation_has_no_launch_intent(tmp_path):
    ledger = _open(tmp_path)
    reservation = _reserve(ledger)
    ledger.cancel_campaign("campaign")
    record = ledger.get_model_reservation(reservation)
    assert record["status"] == "cancelled"
    assert record["charge_basis"] == "verified_unlaunched"
    assert ledger.get_campaign("campaign")["verified_jobs"] == 0
    assert ledger.get_campaign("campaign")["outstanding_reserved_jobs"] == 0


def test_capture_before_activation_cannot_create_or_refund_a_launch_intent(tmp_path):
    ledger = _open(tmp_path)
    reservation = _reserve(ledger)
    with pytest.raises(OperationConflict, match="precedes durable launch intent"):
        _settle(tmp_path, ledger, reservation, status="not_started", launch_confirmed=False,
            native_session_ids=[], elapsed_seconds=0)
    assert ledger.get_model_reservation(reservation)["status"] == "uncertain"
    assert ledger.get_campaign("campaign")["outstanding_reserved_jobs"] == 1


def test_three_protected_stages_preserve_job_and_wall_time_headroom(tmp_path):
    stages = {name: _limits(seconds=60) for name in ("planning", "resolution", "design")}
    ledger = _open(tmp_path, policy=_policy(max_jobs=3,
        reconciliation={"jobs": 3, "seconds": 180, "context_bytes": 4096}),
        identity={"protected_parent_allocations": stages})
    with pytest.raises(OperationBlocked, match="budget_exhausted"):
        _reserve(ledger, "unprotected")
    for index, stage in enumerate(stages):
        reservation = _reserve(ledger, stage, purpose="parent_reconciliation",
            limits=_limits(seconds=60), request=_request(stage, purpose=stage))
        campaign = ledger.get_campaign("campaign")
        assert campaign["reconciliation_held"]["jobs"] == 2 - index
        assert campaign["reconciliation_held"]["seconds"] == (2 - index) * 60
        assert 0 < ledger.model_call_remaining(reservation) <= 60
        ledger.activate_model_call(reservation)
        _settle(tmp_path, ledger, reservation)
    assert ledger.get_campaign("campaign")["verified_jobs"] == 3


@pytest.mark.parametrize("limits", [{"context_bytes": 32769}, {"output_bytes": 131073}])
def test_context_and_output_limits_are_preventive_per_job(tmp_path, limits):
    ledger = _open(tmp_path)
    with pytest.raises(OperationBlocked, match="operational_limit_exceeded"):
        _reserve(ledger, limits=_limits(**limits))
    assert ledger.campaign_reservations("campaign") == []


@pytest.mark.parametrize("updates", [{"job_count": 0}, {"launch_intent": False}, {"usage": None},
    {"actual_calls": 1}, {"status": "cancelled"}, {"request_digest": "0" * 64}])
def test_capture_schema_or_identity_failure_retains_uncertain_lease(tmp_path, updates):
    ledger = _open(tmp_path)
    reservation = _reserve(ledger)
    ledger.activate_model_call(reservation)
    with pytest.raises(OperationConflict):
        _settle(tmp_path, ledger, reservation, **updates)
    assert ledger.get_model_reservation(reservation)["status"] == "uncertain"
    assert ledger.get_campaign("campaign")["outstanding_reserved_jobs"] == 1


def test_native_sessions_cannot_be_reused_across_jobs(tmp_path):
    ledger = _open(tmp_path)
    first = _reserve(ledger)
    ledger.activate_model_call(first)
    _settle(tmp_path, ledger, first, native_session_ids=["shared"])
    second = _reserve(ledger, "two")
    ledger.activate_model_call(second)
    with pytest.raises(OperationConflict, match="reused"):
        _settle(tmp_path, ledger, second, native_session_ids=["shared"])
    assert ledger.get_model_reservation(second)["status"] == "uncertain"


def test_capture_corruption_and_provenance_drift_keep_the_reserved_debit(tmp_path):
    ledger = _open(tmp_path)
    reservation = _reserve(ledger)
    ledger.activate_model_call(reservation)
    reference, verify = _capture(tmp_path, reservation)
    (tmp_path / reference["artifact_id"]).write_text("{}")
    with pytest.raises(OperationConflict, match="changed"):
        ledger.settle_model_call(reservation, capture=reference, verify_capture=verify)
    assert ledger.get_model_reservation(reservation)["status"] == "uncertain"
    bad_provenance = {key: reservation.request[key] for key in (
        "session_id", "invocation_id", "backend_digest", "runtime_digest", "prompt_digest",
        "schema_digest", "context_digest")}
    bad_provenance["context_digest"] = "0" * 64
    with pytest.raises(OperationConflict, match="Invalid verified generation job capture"):
        _settle(tmp_path, ledger, reservation, provenance=bad_provenance)
    assert ledger.get_campaign("campaign")["outstanding_reserved_jobs"] == 1


def test_reconciliation_cannot_erase_observed_job_or_usage(tmp_path):
    ledger = _open(tmp_path)
    reservation = _reserve(ledger)
    ledger.activate_model_call(reservation)
    _settle(tmp_path, ledger, reservation, status="uncertain", elapsed_seconds=5,
            usage={"input_tokens": 15})
    with pytest.raises(OperationConflict, match="erase"):
        _settle(tmp_path, ledger, reservation, elapsed_seconds=6, usage={"input_tokens": 14})
    assert ledger.get_model_reservation(reservation)["capture"]["usage"] == {"input_tokens": 15}


def test_reconciliation_may_complete_previously_unavailable_optional_usage(tmp_path):
    ledger = _open(tmp_path)
    reservation = _reserve(ledger)
    ledger.activate_model_call(reservation)
    _settle(tmp_path, ledger, reservation, status="uncertain", usage={"input_tokens": None, "output_tokens": 4})
    record = _settle(tmp_path, ledger, reservation, elapsed_seconds=3,
                     usage={"input_tokens": 8, "output_tokens": 4})
    assert record["status"] == "completed"
    assert record["capture"]["usage"] == {"input_tokens": 8, "output_tokens": 4}


def test_stored_live_capability_json_never_reconstructs_backend_authority(tmp_path):
    ledger = WorkflowOperationLedger(tmp_path / "operations.sqlite")
    with pytest.raises(OperationBlocked, match="blocked_backend_capability"):
        ledger.open_campaign("campaign", session_id="parent-campaign", policy=_policy(),
            capability=_capability(backend="codex-chatgpt-cli", capability_status="native_qualified",
                native_boundary_qualified=True), identity={"test": "no-live-authority"})
    assert ledger.campaign_reservations("campaign") == []
    assert ledger.replay("campaign") == []


def test_confirmed_backend_bound_violation_blocks_campaign_without_hiding_completed_job(tmp_path):
    ledger = _open(tmp_path)
    reservation = _reserve(ledger)
    ledger.activate_model_call(reservation)
    result = _settle(tmp_path, ledger, reservation, elapsed_seconds=31)
    assert result["status"] == "completed"
    assert ledger.get_campaign("campaign")["reason"] == "backend_limit_violation"
    assert ledger.get_campaign("campaign")["qualification"] == "violated"
    with pytest.raises(OperationBlocked, match="backend_limit_violation"):
        _reserve(ledger, "next")


def test_late_terminal_reconciliation_does_not_claim_unobserved_late_execution(tmp_path, monkeypatch):
    from devbench.analysis import workflow_operations

    ledger = _open(tmp_path)
    reservation = _reserve(ledger)
    ledger.activate_model_call(reservation)
    _settle(tmp_path, ledger, reservation, status="uncertain", process_reaped=False)
    created = ledger.get_campaign("campaign")["created_at"]
    monkeypatch.setattr(workflow_operations.time, "time", lambda: created + 301)
    reference, verify = _capture(tmp_path, reservation, elapsed_seconds=3)
    result = ledger.reconcile_model_call(reservation, capture=reference, verify_capture=verify)
    assert result["status"] == "completed"
    assert ledger.get_campaign("campaign")["reason"] is None
    assert ledger.get_campaign("campaign")["qualification"] == "complete"
    with pytest.raises(OperationBlocked, match="campaign_deadline_exhausted"):
        _reserve(ledger, "next")
