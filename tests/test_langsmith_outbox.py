"""Recovery checks use only bounded software payloads and fake transports."""
import hashlib
from contextlib import contextmanager
import json
import sqlite3
import time
from uuid import uuid4

import pytest

from devbench.study.langsmith_outbox import ExportOutbox, RetryableDelivery


def fixture_box(tmp_path):
    # Legacy payload exercises the already qualified redaction contract.
    root, child = str(uuid4()), str(uuid4())
    runs = []
    for identity, parent in ((root, None), (child, root)):
        runs.append(dict(id=identity, parent_run_id=parent, name="devbench:baseline",
                         run_type="chain", start_time="2026-10-06T00:00:00+00:00",
                         end_time="2026-10-06T00:00:01+00:00", inputs={}, outputs={},
                         session_name="pilot", tags=["devbench", "synthetic"],
                         extra={"metadata": {}}))
    payload = dict(record_format="devbench.langsmith-export/1", privacy_policy="structure_and_scalars/1",
                   data_scope="synthetic", runs=runs)
    source = tmp_path / "payload" / "payload.json"
    source.parent.mkdir()
    raw = json.dumps(payload).encode()
    source.write_bytes(raw)
    box = ExportOutbox.create(tmp_path / "journal" / "outbox.sqlite", payload_path=source,
                             expected_sha256=hashlib.sha256(raw).hexdigest(),
                             endpoint="https://api.smith.langchain.com", project="pilot")
    return box, root, child, source


def test_parent_acknowledged_and_readonly_status(tmp_path):
    box, root, child, _ = fixture_box(tmp_path)
    before = box.path.read_bytes()
    assert ExportOutbox.open_readonly(box.path).status()["counts"]["prepared"] == 2
    assert box.path.read_bytes() == before
    calls = []
    box.deliver(transport=lambda **kwargs: (calls.append(kwargs) or {"status_code": 200}), allow_upload=True)
    assert [c["payload"]["id"] for c in calls] == [root, child]
    assert box.status()["counts"]["acknowledged"] == 2
    box.deliver(transport=lambda **kwargs: pytest.fail("duplicate"), allow_upload=True)


def test_ambiguous_delivery_requires_reviewed_content_reconciliation(tmp_path):
    box, root, child, _ = fixture_box(tmp_path)
    calls = []
    def timeout(**kwargs):
        calls.append(kwargs)
        raise TimeoutError("SECRET provider error")
    box.deliver(transport=timeout, allow_upload=True)
    assert len(calls) == 1
    assert box.status()["counts"]["uncertain"] == 1
    box.deliver(transport=lambda **kwargs: pytest.fail("blind retry"), allow_upload=True)
    with pytest.raises(ValueError):
        box.reconcile(root, remote_state="acknowledged", reviewed_receipt_digest="bad")
    box.reconcile(root, remote_state="acknowledged", reviewed_receipt_digest="a" * 64)
    box.deliver(transport=lambda **kwargs: (calls.append(kwargs) or {"status_code": 200}), allow_upload=True)
    assert calls[-1]["payload"]["id"] == child
    assert b"SECRET" not in box.path.read_bytes()


def test_payload_substitution_and_lease_conflict(tmp_path):
    box, _, _, source = fixture_box(tmp_path)
    source.write_bytes(b"{}")
    with pytest.raises(ValueError):
        box.deliver(transport=lambda **kwargs: None, allow_upload=True)
    with pytest.raises(ValueError):
        box.status()


def test_readonly_missing_does_not_initialize(tmp_path):
    path = tmp_path / "missing.sqlite"
    with pytest.raises((ValueError, FileNotFoundError)):
        ExportOutbox.open_readonly(path)
    assert not path.exists()


def test_expired_send_is_uncertain_and_retry_is_explicit(tmp_path):
    box, root, _, _ = fixture_box(tmp_path)
    with sqlite3.connect(box.path) as db:
        db.execute("UPDATE operations SET state='sending' WHERE span_id=?", (root,))
    reopened = ExportOutbox.open(box.path)
    assert reopened.status()["counts"]["uncertain"] == 1
    reopened.deliver(transport=lambda **kwargs: pytest.fail("blind crash resend"), allow_upload=True)


def test_retryable_failure_retains_attempt_and_requires_next_command(tmp_path):
    box, _, _, _ = fixture_box(tmp_path)
    calls = []
    def before_send(**kwargs):
        calls.append(kwargs)
        raise RetryableDelivery()
    box.deliver(transport=before_send, allow_upload=True)
    assert len(calls) == 1
    assert box.status()["counts"]["retryable"] == 1
    with pytest.raises(PermissionError):
        box.deliver(transport=lambda **kwargs: None, allow_upload=False)


@pytest.mark.parametrize("response,expected", [(None,"uncertain"), ({"status_code":401},"rejected"), ({"status_code":500},"uncertain"), ({"status_code":200,"body":"secret"},"uncertain")])
def test_explicit_closed_acknowledgement_required(tmp_path,response,expected):
    box, _, _, _ = fixture_box(tmp_path)
    box.deliver(transport=lambda **kwargs: response, allow_upload=True)
    assert box.status()["counts"][expected] == 1


def test_active_lease_blocks_competing_exporter(tmp_path):
    box, _, _, _ = fixture_box(tmp_path)
    with sqlite3.connect(box.path) as db:
        db.execute("INSERT INTO leases VALUES(?,?)", (str(uuid4()),time.time()+60))
    with pytest.raises(RuntimeError):
        box.deliver(transport=lambda **kwargs: pytest.fail("competing send"), allow_upload=True)


def test_process_death_after_send_leaves_uncertain(tmp_path):
    box, _, _, _ = fixture_box(tmp_path)
    calls=[]
    def transport(**kwargs):
        calls.append(kwargs)
        # Simulate process death after remote acceptance, before local commit.
        raise SystemExit(77)
    with pytest.raises(SystemExit):
        box.deliver(transport=transport, allow_upload=True)
    assert len(calls)==1
    recovered=ExportOutbox.open(box.path)
    assert recovered.status()["counts"]["uncertain"]==1
    recovered.deliver(transport=lambda **kwargs: pytest.fail("crash resend"), allow_upload=True)


def test_disk_full_commit_injection_retains_uncertain_after_recovery(tmp_path,monkeypatch):
    box,_,_,_=fixture_box(tmp_path)
    original=box._db
    accepted=False
    @contextmanager
    def disk_full_after_acceptance():
        if accepted:
            raise OSError(28,"controlled disk-full injection")
        with original() as db:
            yield db
    def transport(**kwargs):
        nonlocal accepted
        accepted=True
        return {"status_code":200}
    monkeypatch.setattr(box,"_db",disk_full_after_acceptance)
    with pytest.raises(OSError):
        box.deliver(transport=transport,allow_upload=True)
    monkeypatch.setattr(box,"_db",original)
    with sqlite3.connect(box.path) as db:
        # After the owning process's bounded lease expires, recovery classifies
        # the already persisted send as uncertain; acceptance was never saved.
        db.execute("UPDATE leases SET expires_at=0")
    recovered=ExportOutbox.open(box.path)
    assert recovered.status()["counts"]["uncertain"]==1
    recovered.deliver(transport=lambda **kwargs:pytest.fail("disk-full resend"),allow_upload=True)
