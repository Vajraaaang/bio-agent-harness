"""Scientific cancellation and acceptance share one durable order."""
import time

import pytest

from devbench.engine.workflow_operations import OperationBlocked, WorkflowOperationLedger
from tests.test_scientific_authority_migration import _binding

pytestmark = pytest.mark.wal


def authorize(basis):
    return {"authorized": True, "authorization_sha256": "f" * 64, "expires_at": time.time() + 600}


def start(path):
    ledger = WorkflowOperationLedger(path, scientific=True)
    authority = ledger.scientific
    authority.commit("study", expected_revision=0, record=_binding())
    authority.commit("study", expected_revision=1, record={
        "kind": "grant", "record_id": "grant-a", "binding_id": "binding-a", "grant_sha256": "b" * 64})
    authority.activate_execution("study", expected_revision=2, binding_id="binding-a",
                                 grant_id="grant-a", max_jobs=8, authorize=authorize)
    return ledger, authority


def reserve(authority, unit="family-a"):
    state = authority.execution("study")
    return authority.reserve_attempt("study", publication_unit=unit,
        request_sha256="c" * 64, environment_sha256="d" * 64, backend_id="synthetic-provider",
        expected_epoch=state["epoch"], binding_id=state["binding_id"], expected_revision=state["revision"])


def stage(authority, token):
    authority.stage_output(token, artifact_sha256="a" * 64, verification_sha256="e" * 64,
                           verify_output=lambda _: True)


def succeed(authority, token):
    authority.dispatch_attempt(token, lambda _: "job-" + token["attempt_id"])
    authority.reconcile_attempt(token, job_id="job-" + token["attempt_id"], execution_state="succeeded",
        cleanup_state="unknown", usage={"cpu_millis": 12}, receipt_sha256="e" * 64,
        verify_observation=lambda _: True)


def test_accept_then_cancel_retains_exactly_one_output(tmp_path):
    _, authority = start(tmp_path / "operations.sqlite")
    token = reserve(authority)
    succeed(authority, token)
    stage(authority, token)
    accepted = authority.accept_output(token)
    assert accepted["acceptance_state"] == "accepted"
    cancelled = authority.cancel_execution("study", actor="operator")
    assert cancelled["status"] == "cancelled"
    assert authority.accept_output(token)["output_record_id"] == accepted["output_record_id"]
    assert authority.execution("study")["reserved_jobs"] == 1


def test_cancel_then_late_completion_is_audit_only_and_resume_revokes_old_token(tmp_path):
    ledger, authority = start(tmp_path / "operations.sqlite")
    token = reserve(authority)
    authority.dispatch_attempt(token, lambda _: "job-a")
    authority.cancel_execution("study", actor="operator")
    authority = WorkflowOperationLedger(ledger.path).scientific
    authority.reconcile_attempt(token, job_id="job-a", execution_state="succeeded",
        cleanup_state="unknown", usage={}, receipt_sha256="e" * 64, verify_observation=lambda _: True)
    stage(authority, token)
    assert authority.accept_output(token)["acceptance_state"] == "audit_only"
    state = authority.execution("study")
    with pytest.raises(OperationBlocked, match="authorization"):
        authority.resume_execution("study", expected_revision=state["revision"], binding_id="binding-a",
            grant_id="grant-a", max_jobs=8, authorize=None)
    resumed = authority.resume_execution("study", expected_revision=state["revision"], binding_id="binding-a",
        grant_id="grant-a", max_jobs=8, authorize=authorize)
    with pytest.raises(OperationBlocked, match="epoch"):
        authority.dispatch_attempt(token, lambda _: pytest.fail("old attempt dispatched"))
    reused = authority.reserve_attempt("study", publication_unit="family-a", request_sha256="c" * 64,
        environment_sha256="d" * 64, backend_id="synthetic-provider", reuse_from=token["attempt_id"],
        expected_epoch=resumed["epoch"], binding_id="binding-a", expected_revision=resumed["revision"])
    with pytest.raises(OperationBlocked, match="not_ready"):
        authority.accept_output(reused)
    stage(authority, reused)
    assert authority.accept_output(reused)["acceptance_state"] == "accepted"
    assert authority.execution("study")["reserved_jobs"] == 2
    assert authority.attempt(token)["cleanup_state"] == "unknown"


def test_cancel_before_reservation_and_generic_output_bypass(tmp_path):
    _, authority = start(tmp_path / "operations.sqlite")
    with pytest.raises(OperationBlocked, match="attempt"):
        authority.commit("study", expected_revision=3, record={
            "record_id": "bypass", "kind": "output", "binding_id": "binding-a", "publication_unit": "family-a",
            "artifact_sha256": "a" * 64, "verification_sha256": "e" * 64}, verify_output=lambda _: True)
    authority.cancel_execution("study", actor="operator")
    with pytest.raises(OperationBlocked, match="cancelled"):
        reserve(authority)


@pytest.mark.parametrize("first", ["accept", "cancel"])
def test_transaction_barriers_define_both_concurrent_orders(tmp_path, monkeypatch, first):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    ledger, authority = start(tmp_path / "operations.sqlite")
    other = WorkflowOperationLedger(ledger.path).scientific
    token = reserve(authority)
    succeed(authority, token)
    stage(authority, token)
    entered, release, second_started = Event(), Event(), Event()
    if first == "accept":
        original = authority._append_execution_record
        def insert(db, lineage, record):
            value = original(db, lineage, record)
            if record["kind"] == "output":
                entered.set()
                assert release.wait(5)
            return value
        monkeypatch.setattr(authority, "_append_execution_record", insert)
        def primary(): return authority.accept_output(token)
        def secondary():
            second_started.set()
            return other.cancel_execution("study", actor="operator")
    else:
        original = authority._event
        def emit(db, lineage, kind, state):
            value = original(db, lineage, kind, state)
            if kind == "execution_cancelled":
                entered.set()
                assert release.wait(5)
            return value
        monkeypatch.setattr(authority, "_event", emit)
        def primary(): return authority.cancel_execution("study", actor="operator")
        def secondary():
            second_started.set()
            return other.accept_output(token)
    with ThreadPoolExecutor(2) as pool:
        a = pool.submit(primary)
        assert entered.wait(5)
        b = pool.submit(secondary)
        try:
            assert second_started.wait(5)
        finally:
            release.set()
        a.result(10)
        b.result(10)
    attempt = other.attempt(token)
    assert attempt["acceptance_state"] == ("accepted" if first == "accept" else "audit_only")
    assert other.execution("study")["status"] == "cancelled"


def test_stale_decision_cannot_reserve_under_a_resumed_epoch(tmp_path):
    _, authority = start(tmp_path / "operations.sqlite")
    stale = authority.execution("study")
    cancelled = authority.cancel_execution("study", actor="operator")
    authority.resume_execution("study", expected_revision=cancelled["revision"],
        binding_id="binding-a", grant_id="grant-a", max_jobs=8, authorize=authorize)
    with pytest.raises(OperationBlocked, match="expected_epoch"):
        authority.reserve_attempt("study", publication_unit="family-a",
            request_sha256="c" * 64, environment_sha256="d" * 64, backend_id="synthetic-provider",
            expected_epoch=stale["epoch"], binding_id=stale["binding_id"], expected_revision=stale["revision"])
    assert authority.execution("study")["reserved_jobs"] == 0


def test_staged_reuse_remains_recoverable_across_repeated_cancel_resume(tmp_path):
    _, authority = start(tmp_path / "operations.sqlite")
    token = reserve(authority)
    succeed(authority, token)
    stage(authority, token)
    for _ in range(2):
        authority.cancel_execution("study", actor="operator")
        assert authority.accept_output(token)["acceptance_state"] == "audit_only"
        resumed = authority.resume_execution("study", expected_revision=authority.execution("study")["revision"],
            binding_id="binding-a", grant_id="grant-a", max_jobs=8, authorize=authorize)
        token = authority.reserve_attempt("study", publication_unit="family-a",
            request_sha256="c" * 64, environment_sha256="d" * 64, backend_id="synthetic-provider",
            expected_epoch=resumed["epoch"], binding_id="binding-a", expected_revision=resumed["revision"],
            reuse_from=token["attempt_id"])
        with pytest.raises(OperationBlocked, match="not_ready"):
            authority.accept_output(token)
        stage(authority, token)
    assert authority.accept_output(token)["acceptance_state"] == "accepted"
    assert authority.execution("study")["reserved_jobs"] == 3
