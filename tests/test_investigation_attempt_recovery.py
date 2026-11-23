"""Scientific attempt uncertainty, retained staging, and durable restart."""

import pytest

from devbench.engine.workflow_operations import OperationBlocked, OperationConflict, WorkflowOperationLedger
from tests.test_investigation_publication_epochs import authorize, reserve, stage, start, succeed

pytestmark = pytest.mark.wal


def test_submission_loss_needs_owned_reconciliation_before_retry(tmp_path):
    ledger, authority = start(tmp_path / "operations.sqlite")
    token = reserve(authority)

    def lost(_):
        raise RuntimeError("lost submission response")

    with pytest.raises(RuntimeError, match="lost submission"):
        authority.dispatch_attempt(token, lost)
    authority = WorkflowOperationLedger(ledger.path).scientific
    assert authority.attempt(token)["execution_state"] == "submission_uncertain"
    assert authority.attempts("study")[0]["token"] == token
    with pytest.raises(OperationBlocked, match="completion_unknown"):
        reserve(authority)
    with pytest.raises(OperationBlocked, match="submission_uncertain"):
        authority.dispatch_attempt(token, lambda _: pytest.fail("duplicate external dispatch"))
    authority.reconcile_attempt(token, job_id=None, execution_state="noncompletion_confirmed",
        cleanup_state="confirmed", usage={}, receipt_sha256="e" * 64, verify_observation=lambda _: True)
    retry = reserve(authority)
    assert retry["attempt_id"] != token["attempt_id"]
    assert authority.execution("study")["reserved_jobs"] == 2


def test_reconciliation_cannot_swap_job_erase_charges_or_imply_acceptance(tmp_path):
    _, authority = start(tmp_path / "operations.sqlite")
    token = reserve(authority)
    succeed(authority, token)
    with pytest.raises(OperationConflict, match="owned job"):
        authority.reconcile_attempt(token, job_id="different-job", execution_state="succeeded",
            cleanup_state="confirmed", usage={"cpu_millis": 12}, receipt_sha256="e" * 64,
            verify_observation=lambda _: True)
    with pytest.raises(OperationConflict, match="erase usage"):
        authority.reconcile_attempt(token, job_id="job-" + token["attempt_id"], execution_state="succeeded",
            cleanup_state="confirmed", usage={}, receipt_sha256="e" * 64, verify_observation=lambda _: True)
    observed = authority.attempt(token)
    assert observed["acceptance_state"] == "absent"
    assert observed["cleanup_state"] == "unknown" and observed["usage"]["cpu_millis"] == 12


def test_changed_hold_during_verification_keeps_staged_bytes_for_audit(tmp_path):
    _, authority = start(tmp_path / "operations.sqlite")
    token = reserve(authority)
    succeed(authority, token)

    def verify(staged):
        revision = authority.execution("study")["revision"]
        authority.commit("study", expected_revision=revision, record={
            "kind": "hold", "record_id": "new-review-hold", "binding_id": "binding-a", "reason_code": "new_evidence"})
        return True

    authority.stage_output(token, artifact_sha256="a" * 64, verification_sha256="e" * 64, verify_output=verify)
    outcome = authority.accept_output(token)
    assert outcome["acceptance_state"] == "audit_only"
    assert outcome["artifact_sha256"] == "a" * 64 and "scientific_hold" in outcome["rejection_reason"]


def test_acceptance_and_outbox_rollback_together_then_retry(tmp_path):
    ledger, authority = start(tmp_path / "operations.sqlite")
    token = reserve(authority)
    succeed(authority, token)
    stage(authority, token)
    with ledger._database() as db:
        db.execute("""CREATE TRIGGER reject_acceptance BEFORE INSERT ON scientific_outbox
            WHEN EXISTS (SELECT 1 FROM scientific_records r WHERE r.lineage_id=NEW.lineage_id
                         AND r.revision=NEW.revision AND r.kind='output')
            BEGIN SELECT RAISE(ABORT,'acceptance fault'); END""")
    with pytest.raises(OperationConflict):
        authority.accept_output(token)
    assert authority.attempt(token)["acceptance_state"] == "staged"
    with ledger._database() as db:
        assert db.execute("SELECT count(*) FROM scientific_records WHERE kind='output'").fetchone()[0] == 0
        db.execute("DROP TRIGGER reject_acceptance")
    assert authority.accept_output(token)["acceptance_state"] == "accepted"
    assert sum(row["record"]["kind"] == "output" for row in authority.pending("study")) == 1


def test_restart_never_resumes_and_authorization_cannot_race_revision(tmp_path):
    ledger, authority = start(tmp_path / "operations.sqlite")
    authority.cancel_execution("study", actor="operator")
    authority = WorkflowOperationLedger(ledger.path).scientific
    state = authority.execution("study")
    assert state["status"] == "cancelled"

    def changed(basis):
        authority.commit("study", expected_revision=state["revision"], record={
            "kind": "grant", "record_id": "grant-new", "binding_id": "binding-a", "grant_sha256": "a" * 64})
        return authorize(basis)

    with pytest.raises(OperationConflict, match="revision changed"):
        authority.resume_execution("study", expected_revision=state["revision"], binding_id="binding-a",
            grant_id="grant-a", max_jobs=8, authorize=changed)
    assert authority.execution("study")["status"] == "cancelled"


def test_accepted_output_survives_projection_ack_loss(tmp_path):
    from devbench.engine.methods import MethodService

    ledger, authority = start(tmp_path / "operations.sqlite")
    token = reserve(authority)
    succeed(authority, token)
    stage(authority, token)
    accepted = authority.accept_output(token)
    methods = MethodService(tmp_path / "methods")

    def applied_then_lost(event):
        methods.project_scientific_record(event)
        if event["record"]["kind"] == "output":
            raise RuntimeError("acceptance projection ack lost")

    with pytest.raises(RuntimeError, match="ack lost"):
        authority.drain("study", applied_then_lost)
    authority = WorkflowOperationLedger(ledger.path).scientific
    assert authority.attempt(token)["output_record_id"] == accepted["output_record_id"]
    authority.drain("study", methods.project_scientific_record)
    assert authority.pending("study") == []
    assert sum(row["record"]["kind"] == "output" for row in methods.list_records("scientific_projection_event")) == 1


def test_acceptance_survives_process_crash_and_replay_is_idempotent(tmp_path):
    import json
    import subprocess
    import sys

    path = tmp_path / "operations.sqlite"
    token_path = tmp_path / "token.json"
    code = """
import json, os, sqlite3, sys
from pathlib import Path
from tests.test_investigation_publication_epochs import start, reserve, succeed, stage
ledger, authority = start(Path(sys.argv[1]))
token = reserve(authority)
succeed(authority, token)
stage(authority, token)
retained = sqlite3.connect(sys.argv[1])
retained.execute('BEGIN')
retained.execute('SELECT count(*) FROM scientific_records').fetchone()
authority.accept_output(token)
Path(sys.argv[2]).write_text(json.dumps(token))
os._exit(0)
"""
    subprocess.run([sys.executable, "-c", code, str(path), str(token_path)], check=True, timeout=30)
    assert path.with_name(path.name + "-wal").stat().st_size > 0
    authority = WorkflowOperationLedger(path).scientific
    token = json.loads(token_path.read_text())
    assert authority.attempt(token)["acceptance_state"] == "accepted"
    before = authority.execution("study")["revision"]
    authority.accept_output(token)
    assert authority.execution("study")["revision"] == before


def test_expired_grant_prevents_dispatch_and_acceptance(tmp_path, monkeypatch):
    from devbench.engine import scientific_authority

    _, authority = start(tmp_path / "operations.sqlite")
    token = reserve(authority)
    succeed(authority, token)
    stage(authority, token)
    undispatched = reserve(authority, unit="family-b")
    expiry = authority.execution("study")["expires_at"]
    monkeypatch.setattr(scientific_authority.time, "time", lambda: expiry + 1)
    with pytest.raises(OperationBlocked, match="grant_expired"):
        authority.dispatch_attempt(undispatched, lambda _: pytest.fail("expired grant dispatched"))
    assert authority.accept_output(token)["acceptance_state"] == "audit_only"
    assert "grant_expired" in authority.attempt(token)["rejection_reason"]


def test_staging_cannot_replace_an_atomic_output_group(tmp_path):
    _, authority = start(tmp_path / "operations.sqlite")
    token = reserve(authority)
    succeed(authority, token)
    stage(authority, token)
    with pytest.raises(OperationConflict, match="replace a staged"):
        authority.stage_output(token, artifact_sha256="b" * 64, verification_sha256="e" * 64,
                               verify_output=lambda _: True)
    assert authority.attempt(token)["artifact_sha256"] == "a" * 64
