"""Synthetic /3 outbox admission and delivery boundaries; no network or secrets."""
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timedelta
from uuid import uuid4

import pytest

from devbench.study import rna_langsmith_export as export
from devbench.study import rna_langsmith_live as live
from devbench.study import rna_langsmith_verify as verify
from devbench.study.contracts import canonical_json_bytes
from devbench.study.langsmith_outbox import ExportOutbox, digest
from tests.test_rna_hosted_workflow_projection import projected


def prepared_workflow_outbox(tmp_path, *, prior_starts=()):
    session, _, _, operations, starts = projected(tmp_path)
    seed = tmp_path / 'seed' / 'payload.json'
    seed.parent.mkdir()
    raw = canonical_json_bytes(dict(record_format='devbench.rna-langsmith-export/1',
        privacy_policy='rna-allowlist/1', data_scope='synthetic', runs=[operations[0]['payload']]))
    seed.write_bytes(raw)
    box = ExportOutbox.create_live(tmp_path / 'journal' / 'outbox.sqlite',
        payload_path=seed, expected_sha256=digest(raw),
        authorized_session_manifest=session, initial_operations=[operations[0]])
    for index in prior_starts:
        box.enqueue_operation(operations[index]['operation'], operations[index]['payload'])
    return box, session, operations, starts, digest(raw)


def journal_rows(box):
    with box._db() as db:
        return {table: [tuple(row) for row in db.execute('SELECT * FROM ' + table + ' ORDER BY rowid')]
                for table in ('exports', 'spans', 'operations', 'attempts', 'leases', 'live_session')}


def track_journal_writes(monkeypatch):
    writes = []
    original = ExportOutbox._db
    @contextmanager
    def tracked(box):
        with original(box) as db:
            def capture(statement):
                verb = statement.lstrip().split(None, 1)[0].upper()
                if verb in {'INSERT', 'UPDATE', 'DELETE', 'REPLACE'}:
                    writes.append(verb)
            db.set_trace_callback(capture)
            yield db
    monkeypatch.setattr(ExportOutbox, '_db', tracked)
    return writes


def rebound(item):
    """A valid self-consistent proof does not approve a changed workflow slot."""
    item['operation']['operation_id'] = str(uuid4())
    item['payload'] = verify.bind_remote_proof(item['payload'], item['operation'])
    item['operation']['payload_sha256'] = digest(canonical_json_bytes(item['payload']))
    return item


def changed_ancestor_timestamp(payload):
    parts = payload['dotted_order'].split('.')
    stamp = datetime.strptime(parts[0][:22], '%Y%m%dT%H%M%S%fZ') + timedelta(microseconds=1)
    parts[0] = stamp.strftime('%Y%m%dT%H%M%S%fZ') + parts[0][22:]
    payload['dotted_order'] = '.'.join(parts)


def assert_rejected_before_queue_or_transport(box, session, item, monkeypatch):
    before = journal_rows(box)
    writes = track_journal_writes(monkeypatch)
    calls = []
    def transport(**kwargs):
        calls.append(kwargs)
        return {'status_code': 202}
    exporter = live.LiveTraceExporter.open(authorized_session_manifest=session, outbox=box,
        sanitized_event_source=[item], transport=transport)
    with pytest.raises((ValueError, PermissionError)):
        exporter.drain_once(max_operations=1)
    assert journal_rows(box) == before
    assert writes == []
    assert calls == []


def test_v3_outbox_queues_only_exact_retained_approved_lifecycle(tmp_path):
    box, _, operations, _, _ = prepared_workflow_outbox(tmp_path)
    for item in operations[1:]:
        assert box.enqueue_operation(item['operation'], item['payload']) == {'status': 'prepared'}
    assert len(journal_rows(box)['operations']) == 16
    assert len(journal_rows(box)['spans']) == 8


def test_v3_outbox_refuses_undeclared_child_even_with_rebound_proof(tmp_path, monkeypatch):
    box, session, operations, starts, _ = prepared_workflow_outbox(tmp_path)
    item = deepcopy(operations[1])
    identity = str(uuid4())
    item['operation']['span_id'] = identity
    item['payload']['id'] = identity
    parent = item['payload']['parent_run_id']
    item['payload']['dotted_order'] = starts[parent]['dotted_order'] + '.' + live._root_dotted_order(item['payload']['start_time'], identity)
    assert_rejected_before_queue_or_transport(box, session, rebound(item), monkeypatch)


@pytest.mark.parametrize('field', ['role', 'phase', 'parent'])
def test_v3_outbox_refuses_wrong_approved_slot_even_with_rebound_proof(tmp_path, monkeypatch, field):
    box, session, operations, starts, _ = prepared_workflow_outbox(tmp_path)
    item = deepcopy(operations[4] if field == 'parent' else operations[1])
    row = item['payload']['extra']['metadata']
    if field == 'role':
        row['role'] = 'review'
        item['payload']['name'] = 'rna.review.' + row['phase']
    elif field == 'phase':
        row['phase'] = 'question'
        item['payload']['name'] = 'rna.' + row['role'] + '.question'
    else:
        parent = session['span_topology'][0]['span_id']
        item['payload']['parent_run_id'] = parent
        item['payload']['dotted_order'] = starts[parent]['dotted_order'] + '.' + live._root_dotted_order(item['payload']['start_time'], item['payload']['id'])
    assert_rejected_before_queue_or_transport(box, session, rebound(item), monkeypatch)


def test_v3_outbox_refuses_changed_full_parent_timestamp_ancestry(tmp_path, monkeypatch):
    box, session, operations, _, _ = prepared_workflow_outbox(tmp_path, prior_starts=(3,))
    item = deepcopy(operations[4])
    original = item['payload']['dotted_order'].split('.')
    changed_ancestor_timestamp(item['payload'])
    assert item['payload']['dotted_order'].split('.')[1:] == original[1:]
    assert_rejected_before_queue_or_transport(box, session, rebound(item), monkeypatch)


def test_v3_outbox_refuses_finish_order_drift_with_retained_own_start(tmp_path, monkeypatch):
    box, session, operations, _, _ = prepared_workflow_outbox(tmp_path, prior_starts=(1,))
    item = deepcopy(operations[14])
    original_start = item['payload']['start_time']
    original_own_order = item['payload']['dotted_order'].split('.')[-1]
    changed_ancestor_timestamp(item['payload'])
    assert item['payload']['start_time'] == original_start
    assert item['payload']['dotted_order'].split('.')[-1] == original_own_order
    assert_rejected_before_queue_or_transport(box, session, rebound(item), monkeypatch)


def test_v3_generic_rna_delivery_refuses_before_environment_or_transport(tmp_path, monkeypatch):
    box, _, _, _, payload_digest = prepared_workflow_outbox(tmp_path)
    before = journal_rows(box)
    writes = track_journal_writes(monkeypatch)
    reads, calls = [], []
    class TrackingEnvironment(dict):
        def get(self, key, default=None):
            reads.append(key)
            return 'synthetic-placeholder' if key == 'LANGSMITH_API_KEY' else default
    monkeypatch.setattr(export.os, 'environ', TrackingEnvironment())
    monkeypatch.setattr(export, '_post_run', lambda *args, **kwargs: calls.append((args, kwargs)))
    with pytest.raises(PermissionError):
        export.deliver_rna_outbox(outbox_path=box.path, expected_sha256=payload_digest, allow_upload=True)
    assert reads == []
    assert calls == []
    assert writes == []
    assert journal_rows(box) == before


def test_v3_common_delivery_refuses_arbitrary_callable_before_journal_or_transport(tmp_path, monkeypatch):
    box, _, _, _, _ = prepared_workflow_outbox(tmp_path)
    before = journal_rows(box)
    writes = track_journal_writes(monkeypatch)
    calls = []
    def transport(**kwargs):
        calls.append(kwargs)
        return {'status_code': 202}
    with pytest.raises(PermissionError):
        box.deliver(transport=transport, allow_upload=True)
    assert calls == []
    assert writes == []
    assert journal_rows(box) == before
