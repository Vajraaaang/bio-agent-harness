"""Synthetic interruption, crash, and inherited-disclosure controls."""
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from devbench.engine.workflow_operations import OperationConflict, WorkflowOperationLedger
from tests.test_evidence_access_policy import setup, source
from tests.test_scientific_authority_migration import _binding

pytestmark = pytest.mark.wal


def test_commit_failure_sends_no_evidence(tmp_path):
    ledger, broker, _ = setup(tmp_path)
    source(broker)
    sent = []
    with ledger._database() as db:
        db.execute("""CREATE TRIGGER prevent_disclosure BEFORE INSERT ON scientific_disclosures
                      BEGIN SELECT RAISE(ABORT,'test commit fault'); END""")
    with pytest.raises(OperationConflict, match="ledger unavailable") as failure:
        broker.delivery("ctx", "result").transmit({"effect": 2}, sent.append)
    assert isinstance(failure.value.__cause__, sqlite3.IntegrityError)
    assert str(failure.value.__cause__) == "test commit fault"
    assert sent == []
    assert broker.exposure("ctx", "result")["possible_reservations"] == 0


def test_partial_delivery_and_missing_receipt_preserve_possible_scope(tmp_path):
    ledger, broker, _ = setup(tmp_path)
    source(broker)
    sent = []

    def partial(value):
        assert broker.exposure("ctx", "result")["possible_reservations"] == 1
        sent.append(value["effect"])
        raise TimeoutError("synthetic partial transmission")

    with pytest.raises(TimeoutError):
        broker.delivery("ctx", "result").transmit({"effect": 2, "rest": [1, 2]}, partial)
    from devbench.analysis.evidence_access import EvidenceBroker
    reopened = EvidenceBroker(WorkflowOperationLedger(ledger.path).scientific)
    exposure = reopened.exposure("ctx", "result")
    assert sent == [2] and exposure["possible_reservations"] == 1 and exposure["confirmed_reservations"] == 0
    rid = exposure["reservation_ids"][0]
    reservation = reopened.reservation(rid)
    assert reservation["state"] == "delivery_attempted"
    reopened.confirm_receipt(rid, receipt_sha256="c" * 64, confirmed_bytes=1, verify_receipt=lambda _: True)
    assert reopened.reservation(rid)["possible"]
    assert reopened.reservation(rid)["confirmed_bytes"] < reservation["content_bytes"]


def test_current_evidence_is_checked_again_at_delivery_attempt(
    workspace_tmp_path,
):
    from devbench.engine.workflow_operations import OperationBlocked

    _, broker, _ = setup(workspace_tmp_path)
    source(broker)
    checked = []

    def current(db):
        assert db.in_transaction
        checked.append("current")
        if len(checked) == 2:
            raise OperationBlocked("synthetic_evidence_invalidated")

    with pytest.raises(OperationBlocked, match="synthetic_evidence_invalidated"):
        broker.delivery("ctx", "result").release({"effect": 2}, _guard=current)

    assert len(checked) == 2
    exposure = broker.exposure("ctx", "result")
    reservation = broker.reservation(exposure["reservation_ids"][0])
    assert reservation["state"] == "reserved"


def test_zero_delivery_proof_is_scoped_and_cannot_erase_confirmed_receipt(tmp_path):
    from devbench.analysis.evidence_access import AccessDenied

    _, broker, _ = setup(tmp_path)
    source(broker)
    first = broker.reserve("ctx", "result", {"effect": 1})
    second = broker.reserve("ctx", "result", {"effect": 2})
    with pytest.raises(AccessDenied):
        broker.prove_no_delivery(first["reservation_id"], proof_sha256="c" * 64, verify_no_delivery=None)
    broker.prove_no_delivery(first["reservation_id"], proof_sha256="c" * 64, verify_no_delivery=lambda *_: True)
    assert broker.exposure("ctx", "result")["reservation_ids"] == [second["reservation_id"]]
    broker.mark_attempted(second["reservation_id"])
    broker.confirm_receipt(second["reservation_id"], receipt_sha256="d" * 64,
        confirmed_bytes=second["content_bytes"], verify_receipt=lambda _: True)
    with pytest.raises(Exception, match="confirmed"):
        broker.prove_no_delivery(second["reservation_id"], proof_sha256="c" * 64, verify_no_delivery=lambda *_: True)


def test_concurrent_joinable_pages_reclassify_before_second_delivery(tmp_path):
    from devbench.analysis.evidence_access import AccessDenied

    _, broker, _ = setup(tmp_path, allow_outcomes=False, allow_statistics=False)
    source(broker, "masked", tags=("masked_expression",), classification="diagnostic")
    source(broker, "mapping", tags=("target_mapping",), classification="structural")
    barrier = Barrier(2)

    def disclose(name):
        barrier.wait(timeout=5)
        try:
            return broker.reserve("ctx", name, {"ids": ["u1"]}, expected_watermark=0)["classification"]
        except AccessDenied:
            return "denied"

    with ThreadPoolExecutor(2) as pool:
        futures = [pool.submit(disclose, name) for name in ("masked", "mapping")]
        outcomes = [f.result(10) for f in futures]
    assert outcomes.count("denied") == 1
    assert len(broker.exposure("ctx", "masked")["reservation_ids"]) == 1


def test_crash_after_reservation_retains_wal_and_exposure(tmp_path):
    path = tmp_path / "operations.sqlite"
    code = """
import os, sqlite3, sys
from pathlib import Path
from tests.test_evidence_access_policy import setup, source
_, broker, _ = setup(Path(sys.argv[1]))
source(broker)
keep = sqlite3.connect(str(Path(sys.argv[1])/'operations.sqlite'))
keep.execute('BEGIN')
keep.execute('SELECT watermark FROM scientific_disclosure_clock').fetchone()
broker.reserve('ctx','result',{'effect':1})
os._exit(0)
"""
    subprocess.run([sys.executable, "-c", code, str(tmp_path)], check=True, timeout=30)
    assert path.with_name(path.name + "-wal").stat().st_size > 0
    from devbench.analysis.evidence_access import EvidenceBroker
    broker = EvidenceBroker(WorkflowOperationLedger(path).scientific)
    exposure = broker.exposure("ctx", "result")
    assert exposure["classification"] == "target_outcome"
    assert broker.reservation(exposure["reservation_ids"][0])["state"] == "reserved"


def test_disclosure_does_not_change_frozen_binding_or_its_identity(tmp_path):
    ledger, broker, _ = setup(tmp_path)
    source(broker)
    original = ledger.scientific.get("study", "binding-a")
    for _ in range(2):
        broker.delivery("ctx", "result").release({"effect": 1})
    assert ledger.scientific.get("study", "binding-a") == original
    assert ledger.scientific.get("study", "binding-a")["record"]["exposure_basis_sha256"] == _binding()["exposure_basis_sha256"]


def test_controller_transport_observes_committed_reservation(workspace):
    from devbench.analysis.episode_agent import drive_episode
    from tests.test_analysis_episodes import create
    from tests.test_episode_agent import ScriptedSelector

    _, broker, _ = setup(workspace / "access")
    source(broker)
    episode, _ = create(workspace)
    original = ScriptedSelector([{"kind": "request_information", "message": "Confirm the target"}])

    class Selector:
        configuration = original.configuration

        def select(self, *args, **kwargs):
            assert broker.exposure("ctx", "result")["possible_reservations"] == 1
            return original.select(*args, **kwargs)

    result = drive_episode(episode, Selector(), allow_data_execution=True, evidence_delivery=broker.delivery("ctx", "result"))
    assert result["state"] == "awaiting_information"
    episode.abort("synthetic disclosure test completed")


@pytest.fixture
def workspace(workspace_tmp_path):
    return workspace_tmp_path


def test_http_nested_metadata_download_and_stream_delivery(workspace, monkeypatch):
    import hashlib
    import http.client
    from devbench.analysis.evidence_access import AccessPolicy
    from tests.test_scientific_workspace import setup_workspace
    from tests.test_scientific_workspace_http import running, call

    app, *_ = setup_workspace(workspace)
    _, broker, _ = setup(workspace / "access", role="scientist", phase="readback")
    source(broker)
    app.evidence_delivery = broker.delivery("ctx", "result")
    binary = workspace / "synthetic.bin"
    binary.write_bytes(b"synthetic result bytes")
    monkeypatch.setattr(app.scientific, "download", lambda *_: (binary, "application/octet-stream", "synthetic.bin"))
    monkeypatch.setattr(app.investigations, "events", lambda *_, since=0: {
        "events": [{"sequence": 1, "effect": 1.0}] if since == 0 else [], "terminal": True})
    try:
        with running(app) as port:
            status, raw, _ = call(port, "/api/scientific")
            assert status == 200
            exposure = broker.exposure("ctx", "result")
            reservation = broker.reservation(exposure["reservation_ids"][-1])
            assert reservation["content_sha256"] == hashlib.sha256(raw).hexdigest()
            assert reservation["state"] == "delivery_attempted"
            assert call(port, "/api/scientific/artifacts/" + "a" * 64)[1] == binary.read_bytes()
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            conn.request("GET", "/api/investigations/" + "a" * 32 + "/events?stream=true", headers={"Connection": "close"})
            response = conn.getresponse()
            assert response.status == 200
            assert response.readline().startswith(b"event: step")
            assert response.readline().startswith(b"data:")
            assert response.readline() == b"\n"
            assert response.readline() == b"event: terminal\n"
            assert response.readline() == b"data: {}\n"
            assert response.readline() == b"\n"
            conn.close()
            before = broker.exposure("ctx", "result")["possible_reservations"]
            broker.register_policy(AccessPolicy("policy", 2, allow_outcomes=True, allow_statistics=False))
            broker.register_context("restricted", principal_id="scientist", lineage_id="study", role="scientist",
                                    phase="readback", policy_sha256=AccessPolicy("policy", 2, allow_outcomes=True).sha256())
            app.evidence_delivery = broker.delivery("restricted", "result")
            monkeypatch.setattr(app.scientific, "catalog", lambda: {"validations": [{"statistics": {"q_budget": .01}}]})
            monkeypatch.setattr(app.scientific, "resolve", lambda *_: {"review": {"PValue": .01}})
            monkeypatch.setattr(app.scientific, "action", lambda *_: {"operation": {"p_value": .01}})
            monkeypatch.setattr(app, "status", lambda *_: {"synthesis": {"p_value": .01}})
            paths = ("/api/scientific", "/api/scientific/resolve?object_type=result&object_id=a&version_id=b",
                     "/api/jobs/" + "a" * 32,
                     "/api/investigations/" + "a" * 32 + "/events?stream=true")
            for path in paths:
                code, body, _ = call(port, path)
                assert code == 200 and b"0.01" not in body
                assert b"q_budget" not in body and b"PValue" not in body and b"p_value" not in body
            code, body, _ = call(port, "/api/scientific", body={}, origin=f"http://127.0.0.1:{port}")
            assert code == 200 and b"0.01" not in body and b"p_value" not in body
            # Three metadata reads, one action, and the closed stream envelope
            # plus its single event are reserved under the restricted scope.
            assert broker.exposure("restricted", "result")["possible_reservations"] == before + 6
    finally:
        app.close()


def test_no_delivery_proof_cannot_clear_a_concurrent_attempt(tmp_path):
    _, broker, _ = setup(tmp_path)
    source(broker)
    identity = broker.reserve("ctx", "result", {"effect": 1})["reservation_id"]

    def verified_before_attempt(snapshot, _proof):
        assert snapshot["state"] == "reserved"
        broker.mark_attempted(identity)
        return True

    with pytest.raises(OperationConflict, match="changed"):
        broker.prove_no_delivery(identity, proof_sha256="c" * 64, verify_no_delivery=verified_before_attempt)
    assert broker.reservation(identity)["state"] == "delivery_attempted"
    assert broker.exposure("ctx", "result")["possible_reservations"] == 1


def test_reviewer_transport_and_return_each_have_a_reservation(workspace):
    from tests.test_scientific_review import packet
    from tests.test_scientific_review_agent import Selector, request

    receipt, _ = packet(workspace)
    _, broker, policy = setup(workspace / "access", role="challenger", phase="narrative_reveal")
    source(broker)
    broker.register_context("return", principal_id="operator", lineage_id="study", role="scientist",
                            phase="readback", policy_sha256=policy.sha256())

    def verify_request(_):
        ids = broker.exposure("ctx", "result")["reservation_ids"]
        assert len(ids) == 1 and broker.reservation(ids[0])["state"] == "delivery_attempted"
        assert broker.exposure("return", "result")["possible_reservations"] == 0

    result = request(workspace, receipt, Selector(mutate=verify_request),
                     evidence_delivery=broker.delivery("ctx", "result"),
                     return_delivery=broker.delivery("return", "result"))
    assert result["status"] == "review_recorded"
    assert broker.exposure("return", "result")["possible_reservations"] == 1
