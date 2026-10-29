"""Synthetic-only hosted workflow admission and actual receipt projection."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import importlib
import importlib.util
import os
from uuid import uuid4

import pytest


def _api():
    name = "devbench.study.rna_hosted_workflow"
    assert importlib.util.find_spec(name) is not None, "The reviewed hosted workflow transport is unavailable"
    return importlib.import_module(name)


def _readback_engine_api():
    name = 'devbench.study.rna_hosted_context_readback'
    assert importlib.util.find_spec(name) is not None, 'Separate GET-only historical readback engine is absent'
    return importlib.import_module(name)


def test_readback_engine_denies_parent_before_descriptor_files_or_secrets(tmp_path, monkeypatch):
    from devbench.study import rna_hosted_exporter as exporter
    api = _readback_engine_api()
    calls = []
    monkeypatch.setattr(exporter, '_resolve_keychain_credential', lambda: calls.append('credential'))
    with pytest.raises(PermissionError, match='child'):
        api.HostedContextReadback({})
    assert calls == [] and list(tmp_path.iterdir()) == []


def test_readback_current_reader_preserves_expired_historical_producer_and_open_revisions(tmp_path, monkeypatch):
    from tests.test_rna_hosted_exporter_isolation import _readback_fixture
    fixture = _readback_fixture(tmp_path, monkeypatch)
    before = fixture.snapshot()
    assert datetime.fromisoformat(fixture.scope['session']['expires_at']) < datetime.now(timezone.utc)
    assert fixture.packet['reader_binding']['source_digest'] != fixture.packet['producer_binding']['source_digest']
    with fixture.channel() as channel:
        result = channel.request({'command':'verify'})
        assert result['verification_result'] == 'exact_context_match'
        assert result['current_reader_binding'] == fixture.packet['reader_binding']
        assert result['historical_producer_binding'] == fixture.packet['producer_binding']
        assert result['original_failure_preserved'] is True
        assert result['original_visibility_sla_recovered'] is False
        assert result['settled_old_outbox'] is result['full_route_qualified'] is result['science_qualified'] is False
        assert len(result['receipts']) == 3
        revisions = {row['span_id']: row['revision'] for row in result['receipts']}
        assert sorted(revisions.values()) == [0, 0, 1]
        assert channel.request({'command':'status'})['counts']['read_requests'] == 4
    assert fixture.snapshot() == before


@pytest.mark.parametrize('seal_format', ['devbench.rna-context-history-seal/1',
    'devbench.rna-nonmodel-v3-attempt-failure-seal/1'])
def test_readback_neutral_and_legacy_seals_keep_checkpoint_opaque(tmp_path, monkeypatch, seal_format):
    from tests.test_rna_hosted_exporter_isolation import _readback_fixture
    opaque = b'\x00original-checkpoint-is-evidence-not-current-authority\xff'
    fixture = _readback_fixture(tmp_path, monkeypatch, seal_format=seal_format, checkpoint_bytes=opaque)
    before = fixture.snapshot()
    assert 'session' not in fixture.packet['history']
    with fixture.channel() as channel:
        result = channel.request({'command':'verify'})
        assert result['verification_result'] == 'exact_context_match'
        assert result['original_failure_preserved'] is True
        assert result['settled_old_outbox'] is result['full_route_qualified'] is result['science_qualified'] is False
    assert fixture.snapshot() == before
    from pathlib import Path
    assert Path(fixture.packet['history']['checkpoint']['path']).read_bytes() == opaque


def test_readback_latest_ack_start_does_not_settle_later_unacknowledged_finish(tmp_path, monkeypatch):
    import json
    import sqlite3
    from pathlib import Path
    from tests.test_rna_hosted_exporter_isolation import _readback_fixture
    fixture = _readback_fixture(tmp_path, monkeypatch, unacknowledged_terminal=True)
    failed = fixture.packet['session']['context_profile']['terminal_span_ids'][0]
    assert next(row for row in fixture.packet['session']['context_profile']['contexts']
        if row['span_id'] == failed)['terminal_status'] == 'failed'
    uri = Path(fixture.packet['history']['outbox']['path']).as_uri() + '?mode=ro&immutable=1'
    def retained_finish():
        with sqlite3.connect(uri, uri=True) as db:
            row = db.execute("SELECT operation_id,state,attempt_count,payload FROM operations WHERE span_id=? AND operation='finish'", (failed,)).fetchone()
            attempts = db.execute('SELECT count(*) FROM attempts WHERE operation_id=?', (row[0],)).fetchone()[0]
            return row, attempts
    original = retained_finish()
    assert original[0][1:3] == ('prepared', 0) and original[1] == 0
    assert json.loads(original[0][3])['extra']['metadata']['source_status'] == 'failed'
    assert 'end_time' not in fixture.remote[failed]
    before = fixture.snapshot()
    with fixture.channel() as channel:
        result = channel.request({'command':'verify'})
        assert result['verification_result'] == 'exact_context_match'
        assert result['verified_span_count'] == 3
        receipt = next(row for row in result['receipts'] if row['span_id'] == failed)
        assert receipt['revision'] == 0 and receipt['operation_id'] != original[0][0]
        diagnostic = next(row for row in result['diagnostics'] if row['span_id'] == failed)
        assert diagnostic['actual_provider_status'] == 'pending'
        assert result['settled_old_outbox'] is result['full_route_qualified'] is result['science_qualified'] is False
        assert result['original_failure_preserved'] is True
    assert fixture.snapshot() == before and retained_finish() == original


def test_readback_repeated_context_slots_use_parent_and_projected_source_proofs(tmp_path, monkeypatch):
    from collections import Counter
    from tests.test_rna_hosted_exporter_isolation import _readback_fixture
    fixture = _readback_fixture(tmp_path, monkeypatch, duplicate_slots=True)
    topology = fixture.packet['topology']
    repeated = [slot for slot in topology if (slot['role'],slot['phase'],slot['attempt']) == ('lead','generate',1)]
    assert len(topology) == 3 and len(repeated) == 2
    assert Counter((slot['role'],slot['phase'],slot['attempt']) for slot in topology)[('lead','generate',1)] == 2
    assert repeated[0]['span_id'] != repeated[1]['span_id']
    profile = {row['span_id']:row for row in fixture.packet['session']['context_profile']['contexts']}
    left, right = (profile[slot['span_id']] for slot in repeated)
    assert left['context_alias'] != right['context_alias']
    assert left['source_event_digests'] != right['source_event_digests']
    before = fixture.snapshot()
    with fixture.channel() as channel:
        result = channel.request({'command':'verify'})
        assert result['verification_result'] == 'exact_context_match'
        assert result['verified_span_count'] == 3
        receipts = {row['span_id']:row for row in result['receipts']}
        assert all(receipts[slot['span_id']]['revision'] == 1 for slot in repeated)
        assert receipts[repeated[0]['span_id']]['operation_id'] != receipts[repeated[1]['span_id']]['operation_id']
        assert channel.request({'command':'status'})['counts']['read_requests'] == 4
        assert result['settled_old_outbox'] is result['full_route_qualified'] is result['science_qualified'] is False
    assert fixture.snapshot() == before


def test_readback_one_verify_intent_survives_reopen_and_never_polls(tmp_path, monkeypatch):
    from tests.test_rna_hosted_exporter_isolation import _readback_fixture
    fixture = _readback_fixture(tmp_path, monkeypatch)
    with fixture.channel() as channel:
        first = channel.request({'command':'verify'})
        before = fixture.rows()
        try:
            repeated = channel.request({'command':'verify'})
            assert repeated['status'] == 'blocked'
        except (ValueError, PermissionError):
            pass
        assert fixture.rows() == before
    before = fixture.rows()
    try:
        with fixture.channel(restart=True) as channel:
            status = channel.request({'command':'status'})
            assert status['status'] == 'blocked' and status['credential_resolutions'] == 0
            assert status['authorize_upload'] is status['authorize_remote_read'] is False
    except (ValueError, PermissionError):
        pass
    assert fixture.rows() == before


def test_readback_credential_process_crash_consumes_intent_before_any_get(tmp_path, monkeypatch):
    from pathlib import Path
    from tests.test_rna_hosted_exporter_isolation import _readback_fixture
    fixture = _readback_fixture(tmp_path, monkeypatch, mode='credential_crash')
    before = fixture.snapshot()
    with pytest.raises((ValueError, PermissionError, OSError)):
        with fixture.channel():
            pytest.fail('crashed credential producer unexpectedly admitted')
    assert Path(fixture.packet['evidence_path']).is_dir()
    rows = fixture.rows()
    assert sum(row['kind'] == 'credential' for row in rows) == 1
    assert not any(row['kind'] == 'http' for row in rows)
    try:
        with fixture.channel(restart=True) as channel:
            assert channel.request({'command':'status'})['status'] == 'blocked'
    except (ValueError, PermissionError):
        pass
    assert fixture.rows() == rows and fixture.snapshot() == before


def test_readback_concurrent_original_children_reserve_only_one_intent(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from tests.test_rna_hosted_exporter_isolation import _readback_fixture
    fixture = _readback_fixture(tmp_path, monkeypatch)
    gate = Barrier(2)
    def attempt():
        gate.wait(timeout=5)
        try:
            with fixture.channel() as channel:
                state = channel.request({'command':'status'})
                if state['status'] == 'admitted':
                    channel.request({'command':'verify'})
                return state['status']
        except (ValueError, PermissionError):
            return 'blocked'
    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = list(pool.map(lambda _: attempt(), range(2)))
    assert sorted(statuses) == ['admitted', 'blocked']
    rows = fixture.rows()
    assert sum(row['kind'] == 'credential' for row in rows) == 1
    assert sum(row['kind'] == 'http' for row in rows) == 4


def test_readback_real_approval_expiry_clips_transport_and_ipc_deadline(tmp_path, monkeypatch):
    import time
    from tests.test_rna_hosted_exporter_isolation import _readback_fixture
    fixture = _readback_fixture(tmp_path, monkeypatch, mode='project_deadline', lifetime_seconds=3)
    started = time.monotonic()
    with pytest.raises((ValueError, PermissionError, TimeoutError, OSError)):
        with fixture.channel():
            pytest.fail('readback exceeded its expiring approval')
    assert time.monotonic() - started < 7
    http = [row for row in fixture.rows() if row['kind'] == 'http']
    assert len(http) == 1 and 0 < http[0]['timeout'] <= 3
    assert http[0]['path'] == fixture.packet['read_descriptor']['project_route']


@pytest.mark.parametrize('mode', ['status_mismatch', 'incomplete', 'oversized', 'partial_error',
    'transport_timeout', 'http_error', 'http_error_incomplete', 'http_error_ambiguous'])
def test_readback_bounded_transport_failures_never_retry_or_leak_provider_data(tmp_path, monkeypatch, mode):
    from pathlib import Path
    from tests.test_rna_hosted_exporter_isolation import _readback_fixture
    fixture = _readback_fixture(tmp_path, monkeypatch, mode=mode)
    before = fixture.snapshot()
    with fixture.channel() as channel:
        result = channel.request({'command':'verify'})
        state = channel.request({'command':'status'})
        assert result['verification_result'] == 'context_mismatch'
        assert state['authorize_upload'] is False
        assert result['original_failure_preserved'] is True
        assert result['original_visibility_sla_recovered'] is False
        assert result['settled_old_outbox'] is result['full_route_qualified'] is result['science_qualified'] is False
        assert 'PRIVATE_' not in str(result) + str(state)
        assert state['counts']['read_requests'] <= 4
        assert state['counts']['response_bytes'] <= 131072
        if mode in {'oversized', 'partial_error', 'incomplete', 'http_error_incomplete', 'http_error_ambiguous'}:
            assert state['status'] == 'blocked'
            assert state['counts']['read_requests'] == 2
        if mode == 'partial_error':
            import json
            target = fixture.packet['target']
            project_bytes = len(json.dumps(dict(id=target['project_id'], tenant_id=target['workspace_id'],
                name=target['project_name'])).encode())
            assert state['counts']['response_bytes'] >= project_bytes + 7
        if mode in {'http_error_incomplete', 'http_error_ambiguous'}:
            first = result['diagnostics'][0]
            assert first['mismatch_category'] == 'body_incomplete'
            assert first['body_length'] is first['body_sha256'] is None
        for row in result['diagnostics']:
            assert row.get('actual_provider_status', 'missing') in {
                'pending', 'success', 'error', 'missing', 'unrecognized'}
        if mode == 'status_mismatch':
            assert all(row['actual_provider_status'] == 'unrecognized' for row in result['diagnostics'])
        audit = fixture.rows()
        try: channel.request({'command':'verify'})
        except (ValueError, PermissionError): pass
        assert fixture.rows() == audit
    assert fixture.snapshot() == before
    root = Path(fixture.packet['evidence_path'])
    assert not any('PRIVATE_' in path.read_text(errors='ignore') for path in root.rglob('*') if path.is_file())


def _topology():
    root = str(uuid4())
    return [dict(span_id=root, parent_span_id=None, role="coordinator", phase="workflow", attempt=1), *[
        dict(span_id=str(uuid4()), parent_span_id=root, role=role, phase=phase, attempt=1)
        for role, phase in (("coordinator", "generate"), ("coordinator", "permission_preflight"),
                            ("numerical", "run"), ("review", "verify"),
                            ("coordinator", "synthesis"), ("review", "review"),
                            ("coordinator", "invocation"))]]


def _session(topology):
    from devbench.study.rna_langsmith_live import EVENT_FIELDS
    return dict(format="devbench.rna-live-trace-session/3", workflow_alias=str(uuid4()),
        workflow_binding_digest="4" * 64, source_digest="1" * 64,
        runtime_digest="2" * 64, policy_digest="3" * 64, data_scope="synthetic",
        endpoint="https://api.smith.langchain.com", project="bio-harness-hosted-qualification",
        project_id="2f749903-8bc2-4f79-a7e6-75ae493fe4d4",
        workspace_id="59a08947-1ff8-4adf-956b-a7f0fd9eb21c",
        redaction_version="rna-allowlist/1", event_schema="devbench.rna-trace-event/1",
        allowed_event_fields=sorted(EVENT_FIELDS), max_spans=8, max_bytes=131072,
        max_delivery_attempts=1, expires_at=(datetime.now(timezone.utc) + timedelta(minutes=15)).isoformat(),
        authorize_upload=True, span_topology=topology)


def test_workflow_requires_original_exporter_child_before_any_side_effect(tmp_path, monkeypatch):
    api = _api()
    from devbench.study import rna_hosted_exporter as exporter
    calls = []
    monkeypatch.setattr(exporter, "_CHILD_PROCESS_PID", os.getpid() + 1)
    monkeypatch.setattr(exporter, "_resolve_keychain_credential", lambda: calls.append("credential"))
    with pytest.raises(PermissionError, match="child"):
        api.HostedWorkflow({})
    assert calls == [] and list(tmp_path.iterdir()) == []


def test_workflow_descriptor_refuses_secret_and_unknown_fields_before_io(tmp_path):
    api = _api()
    for descriptor in ({"format": "devbench.rna-hosted-exporter-session/3", "api_key": "PRIVATE_CANARY"},
                       {"format": "devbench.rna-hosted-exporter-session/3", "packet": {}, "approval_handles": {}, "callback": "PRIVATE_CANARY"}):
        with pytest.raises((ValueError, PermissionError)):
            api.validate_descriptor(descriptor)
    assert list(tmp_path.iterdir()) == []


def test_receipt_bridge_uses_frozen_remote_slots_without_raw_local_ids(tmp_path):
    api = _api()
    from devbench.analysis.rna_trace_receipts import LocalTraceObserver, observe_boundary
    from devbench.analysis.rna_trace_contract import trace_digest
    topology = _topology()
    session = _session(topology)
    identity = "PRIVATE_WORKFLOW_ID"
    session["workflow_binding_digest"] = trace_digest(identity)
    observer = LocalTraceObserver(tmp_path / "observer.sqlite", source_digest=session["source_digest"],
        runtime_digest=session["runtime_digest"], policy_digest=session["policy_digest"])
    bridge = api.WorkflowReceiptBridge(authorized_session_manifest=session,
        output_path=tmp_path / "sanitized.jsonl")
    observed = []
    def operation():
        observed.extend(bridge.emit_new(observer=observer, workflow_id=identity))
        return {"private_result": "PRIVATE_RESULT_CANARY"}
    observe_boundary(observer, identity, "generate", operation, binding_digest=trace_digest([identity, "generate"]))
    observed.extend(bridge.emit_new(observer=observer, workflow_id=identity))
    assert len(observed) == 3
    assert [row["operation"]["operation"] for row in observed] == ["start", "start", "finish"]
    assert observed[0]["payload"]["id"] == topology[0]["span_id"]
    assert observed[1]["payload"]["id"] == topology[1]["span_id"]
    assert observed[1]["payload"]["parent_run_id"] == topology[0]["span_id"]
    assert observed[1]["payload"]["dotted_order"].startswith(observed[0]["payload"]["dotted_order"] + ".")
    raw = (tmp_path / "sanitized.jsonl").read_text()
    assert "PRIVATE_" not in raw and str(tmp_path) not in raw
    local = {row["span_id"] for row in observer.read_contexts(identity)}
    assert not any(value in raw for value in local)
    assert bridge.emit_new(observer=observer, workflow_id=identity) == []


def test_receipt_bridge_rejects_unapproved_slot_and_keeps_original_receipts(tmp_path):
    api = _api()
    from devbench.analysis.rna_trace_receipts import LocalTraceObserver, observe_boundary
    from devbench.analysis.rna_trace_contract import trace_digest
    topology = _topology()
    session = _session(topology)
    identity = "unapproved-synthetic-slot"
    session["workflow_binding_digest"] = trace_digest(identity)
    observer = LocalTraceObserver(tmp_path / "observer.sqlite", source_digest=session["source_digest"],
        runtime_digest=session["runtime_digest"], policy_digest=session["policy_digest"])
    observe_boundary(observer, identity, "investigate", lambda: None, binding_digest=trace_digest(identity))
    before = deepcopy(observer.read_events(identity))
    bridge = api.WorkflowReceiptBridge(authorized_session_manifest=session, output_path=tmp_path / "sanitized.jsonl")
    with pytest.raises(ValueError, match="slot|topology"):
        bridge.emit_new(observer=observer, workflow_id=identity)
    assert observer.read_events(identity) == before



def _engine(tmp_path, monkeypatch, *, mutation_status=202, lost_response=False):
    """Trusted offline child instrumentation; never a production admission hook."""
    import io
    import json
    import ssl
    from types import SimpleNamespace
    from devbench.study import rna_hosted_exporter as exporter
    from devbench.study import rna_hosted_workflow_contract as contract
    from tests.test_rna_hosted_workflow_contract import make_scope, handles
    from devbench.study.rna_langsmith_live import ReceiptOperationBridge
    from tests.test_rna_trace_contract import event
    api = _api()
    scope = make_scope(tmp_path, contract)
    packet = contract.prepare_workflow_packet(**scope)
    approvals = handles(contract, packet, scope, tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(exporter, '_CHILD_PROCESS_PID', os.getpid())
    monkeypatch.setattr(api, 'workflow_identity', lambda: deepcopy(scope['installed_identity']))
    calls, remote = [], {}
    def credential():
        calls.append(('credential',))
        return 'PRIVATE_CREDENTIAL_CANARY'
    monkeypatch.setattr(exporter, '_resolve_keychain_credential', credential)
    class Response:
        def __init__(self, status, body):
            self.status = status
            self.stream = io.BytesIO(json.dumps(body).encode())
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def read(self, size):
            assert 0 <= size <= 65537
            return self.stream.read(size)
    class Opener:
        def open(self, request, timeout):
            method = request.get_method()
            path = request.full_url.split('langchain.com', 1)[1]
            assert request.get_header('X-api-key') == 'PRIVATE_CREDENTIAL_CANARY'
            assert 0 < timeout <= 15
            calls.append((method, path))
            if path == packet['read_descriptor']['project_route']:
                target = packet['target']
                return Response(200, dict(id=target['project_id'], tenant_id=target['workspace_id'], name=target['project_name']))
            if method in ('POST', 'PATCH'):
                payload = json.loads(request.data)
                remote[payload['id']] = payload
                if lost_response: raise TimeoutError('PRIVATE_PROVIDER_CANARY')
                return Response(mutation_status, {'private': 'PRIVATE_PROVIDER_CANARY'})
            body = remote.get(path.rsplit('/', 1)[1])
            if body is None: return Response(404, {})
            body = deepcopy(body)
            body.pop('session_name', None)
            body.update(status='success' if 'end_time' in body else 'pending', app_path='/synthetic-run')
            return Response(200, body)
    def opener(*handlers):
        from urllib.request import HTTPSHandler, ProxyHandler
        assert any(isinstance(handler, ProxyHandler) and handler.proxies == {} for handler in handlers)
        tls = [handler for handler in handlers if isinstance(handler, HTTPSHandler)]
        assert len(tls) == 1
        assert tls[0]._context.verify_mode == ssl.CERT_REQUIRED and tls[0]._context.check_hostname
        return Opener()
    monkeypatch.setattr(api, 'build_opener', opener)
    session = {**packet['session'], 'authorize_upload': True}
    events = []
    now = datetime.now(timezone.utc)
    for terminal in (False, True):
        for index, slot in enumerate(packet['topology'] if not terminal else reversed(packet['topology'])):
            events.append(event(event_id=str(uuid4()), trace_id=packet['topology'][0]['span_id'],
                **slot, sequence=int(terminal), kind='completion' if terminal else 'reservation',
                source_status='completed' if terminal else 'active', dispatch_status='not_dispatched',
                clock=dict(utc=(now + timedelta(microseconds=index + 100 * int(terminal))).isoformat().replace('+00:00', 'Z'),
                    monotonic_ns=index + 100 * int(terminal), process_alias=str(uuid4()), timing_basis='observed'),
                bindings=dict(workflow=session['workflow_binding_digest'], source=session['source_digest'],
                    runtime=session['runtime_digest'], policy=session['policy_digest'])))
    class Observer:
        def read_events(self, workflow_id): return deepcopy(events)
    bridge = ReceiptOperationBridge(authorized_session_manifest=session, output_path=tmp_path / 'ops.jsonl',
        capability=dict(backend_kind='unknown', qualification_status='unknown', admitted=False),
        allocation=0, actual_children=0)
    operations = bridge.emit_new(observer=Observer(), workflow_id='synthetic-engine')
    descriptor = dict(format='devbench.rna-hosted-exporter-session/3', packet=packet, approval_handles=approvals)
    return SimpleNamespace(api=api, descriptor=descriptor, packet=packet, operations=operations,
        calls=calls, remote=remote, approvals=approvals)


def test_live_engine_acknowledges_sixteen_operations_and_verifies_eight_latest_revisions(tmp_path, monkeypatch):
    fixture = _engine(tmp_path, monkeypatch)
    engine = fixture.api.HostedWorkflow(fixture.descriptor)
    try:
        for operation in fixture.operations:
            state = engine.emit(operation)
        assert state['counts']['acknowledged'] == 16
        assert state['live_span_count'] == state['terminal_span_count'] == 8
        result = engine.verify()
        assert result['verification_result'] == 'exact_workflow_match'
        assert len(result['receipts']) == 8
        assert all(row['verification_result'] == 'exact_revision_match' for row in result['receipts'])
        state = engine.status()
        assert state['verified_span_count'] == 8
        assert state['counts']['mutation_requests'] == 16
        assert state['counts']['read_requests'] == 10
        assert state['deployed_schema_qualified'] is False
        assert state['schema_mode'] == 'experimental_live_workflow'
    finally:
        engine.close()
    assert len([call for call in fixture.calls if call[0] in ('POST', 'PATCH')]) == 16
    assert fixture.calls.count(('credential',)) == 1
    assert not any('PRIVATE_' in path.read_text(errors='ignore') for path in (tmp_path / 'evidence').rglob('*') if path.is_file())
    assert engine.status()['authorize_upload'] is False


def test_live_engine_requires_parent_ack_and_all_declared_children_before_parent_finish(tmp_path, monkeypatch):
    fixture = _engine(tmp_path, monkeypatch)
    engine = fixture.api.HostedWorkflow(fixture.descriptor)
    try:
        before = list(fixture.calls)
        with pytest.raises(ValueError, match='root|parent'):
            engine.emit(fixture.operations[1])
        assert fixture.calls == before
        engine.emit(fixture.operations[0])
        with pytest.raises(ValueError, match='child|terminal'):
            engine.emit(fixture.operations[-1])
        with pytest.raises(ValueError, match='terminal|eight|complete'):
            engine.verify()
    finally:
        engine.close()


@pytest.mark.parametrize('lost_response,mutation_status', [(True, 202), (False, 500), (False, 400)])
def test_live_engine_never_resends_an_observed_or_unacknowledged_operation(tmp_path, monkeypatch, lost_response, mutation_status):
    fixture = _engine(tmp_path, monkeypatch, lost_response=lost_response, mutation_status=mutation_status)
    engine = fixture.api.HostedWorkflow(fixture.descriptor)
    try:
        engine.emit(fixture.operations[0])
        before = list(fixture.calls)
        with pytest.raises((ValueError, PermissionError), match='duplicate|existing|attempt|stopped'):
            engine.emit(fixture.operations[0])
        with pytest.raises((ValueError, PermissionError), match='parent|acknowledged|stopped'):
            engine.emit(fixture.operations[1])
        assert fixture.calls == before
    finally:
        engine.close()


def test_live_engine_refuses_duplicate_acknowledged_or_unapproved_changed_operation(tmp_path, monkeypatch):
    fixture = _engine(tmp_path, monkeypatch)
    engine = fixture.api.HostedWorkflow(fixture.descriptor)
    try:
        engine.emit(fixture.operations[0])
        before = list(fixture.calls)
        with pytest.raises(ValueError, match='duplicate|existing'):
            engine.emit(fixture.operations[0])
        changed = deepcopy(fixture.operations[1])
        changed['payload']['extra']['metadata']['role'] = 'review'
        with pytest.raises(ValueError): engine.emit(changed)
        assert fixture.calls == before
    finally:
        engine.close()


def test_live_engine_consumes_packet_before_credential_failure_and_cannot_reopen(tmp_path, monkeypatch):
    fixture = _engine(tmp_path, monkeypatch)
    from devbench.study import rna_hosted_exporter as exporter
    def unavailable(): raise RuntimeError('PRIVATE_CREDENTIAL_CANARY')
    monkeypatch.setattr(exporter, '_resolve_keychain_credential', unavailable)
    with pytest.raises(RuntimeError): fixture.api.HostedWorkflow(fixture.descriptor)
    assert (tmp_path / 'evidence' / 'connection.sqlite').is_file()
    before = list(fixture.calls)
    replay = fixture.api.HostedWorkflow(fixture.descriptor)
    assert replay.status()['blockers'] == ['packet_already_consumed']
    assert fixture.calls == before
    replay.close()


@pytest.mark.parametrize('drift', ['packet', 'handle', 'expiry', 'identity'])
def test_live_engine_rechecks_immutable_inputs_before_every_http(tmp_path, monkeypatch, drift):
    fixture = _engine(tmp_path, monkeypatch)
    engine = fixture.api.HostedWorkflow(fixture.descriptor)
    try:
        if drift == 'packet': engine.packet['target']['project_name'] = 'changed'
        elif drift == 'handle':
            from pathlib import Path
            Path(fixture.approvals['target_read']['path']).write_text('{}')
        elif drift == 'identity': monkeypatch.setattr(fixture.api, 'workflow_identity', lambda: {})
        else: monkeypatch.setattr(engine, '_now', lambda: datetime.now(timezone.utc) + timedelta(minutes=10))
        before = list(fixture.calls)
        with pytest.raises((ValueError, PermissionError, TimeoutError)):
            engine.emit(fixture.operations[0])
        assert fixture.calls == before
    finally:
        engine.close()


def test_receipt_bridge_reopening_rederives_map_and_rejects_second_actual_context_for_slot(tmp_path):
    api = _api()
    from devbench.analysis.rna_trace_receipts import LocalTraceObserver, observe_boundary
    from devbench.analysis.rna_trace_contract import trace_digest
    topology = _topology()
    session = _session(topology)
    identity = 'synthetic-recovery'
    session['workflow_binding_digest'] = trace_digest(identity)
    observer = LocalTraceObserver(tmp_path / 'observer.sqlite', source_digest=session['source_digest'],
        runtime_digest=session['runtime_digest'], policy_digest=session['policy_digest'])
    observe_boundary(observer, identity, 'generate', lambda: None, binding_digest=trace_digest([identity, 1]))
    path = tmp_path / 'ops.jsonl'
    bridge = api.WorkflowReceiptBridge(authorized_session_manifest=session, output_path=path)
    assert len(bridge.emit_new(observer=observer, workflow_id=identity)) == 3
    before = deepcopy(observer.read_events(identity))
    reopened = api.WorkflowReceiptBridge(authorized_session_manifest=session, output_path=path)
    assert reopened.emit_new(observer=observer, workflow_id=identity) == []
    assert observer.read_events(identity) == before
    observe_boundary(observer, identity, 'generate', lambda: None, binding_digest=trace_digest([identity, 2]))
    with pytest.raises(ValueError, match='slot|topology|context'):
        reopened.emit_new(observer=observer, workflow_id=identity)


def test_live_engine_rejects_changed_terminal_ancestor_order_before_persistence_or_http(tmp_path, monkeypatch):
    from devbench.study.contracts import canonical_json_bytes
    from devbench.study.langsmith_outbox import digest
    from devbench.study.rna_langsmith_live import _root_dotted_order
    from devbench.study.rna_langsmith_verify import bind_remote_proof
    fixture = _engine(tmp_path, monkeypatch)
    engine = fixture.api.HostedWorkflow(fixture.descriptor)
    try:
        engine.emit(fixture.operations[0])
        engine.emit(fixture.operations[1])
        child_id = fixture.operations[1]['payload']['id']
        terminal = deepcopy(next(item for item in fixture.operations
            if item['payload']['id'] == child_id and item['operation']['operation'] == 'finish'))
        components = terminal['payload']['dotted_order'].split('.')
        components[0] = _root_dotted_order('2001-01-01T00:00:00Z', fixture.packet['topology'][0]['span_id'])
        terminal['payload']['dotted_order'] = '.'.join(components)
        terminal['payload'] = bind_remote_proof(terminal['payload'], terminal['operation'])
        terminal['operation']['payload_sha256'] = digest(canonical_json_bytes(terminal['payload']))
        # This remains valid closed wire content: retained START identity is
        # needed to reject the changed ancestor timestamp before persistence.
        fixture.api.validate_wire_operation(terminal, packet=fixture.packet)
        before = list(fixture.calls)
        with engine.box._db() as db:
            count = db.execute('SELECT count(*) FROM operations').fetchone()[0]
        with pytest.raises(ValueError, match='order|identity|terminal'):
            engine.emit(terminal)
        assert fixture.calls == before
        with engine.box._db() as db:
            assert db.execute('SELECT count(*) FROM operations').fetchone()[0] == count
    finally:
        engine.close()


# /4 context-observation regressions are additive; the /3 tests above are frozen.
def _context_api():
    api = _api()
    for name in ('HostedContextObservation', 'ContextReceiptBridge', 'prepare_context_observation',
                 'validate_context_descriptor', 'validate_context_wire_operation',
                 'validate_context_status', 'validate_context_verification'):
        assert callable(getattr(api, name, None)), 'Additive context-observation /4 feature is absent'
    return api


def _actual_context_observer(tmp_path, *, open_child=False):
    """Actual optional-observer calls for a synthetic lifecycle counterexample.

    No participant/model/tool is invoked. Capture labels describe only this
    local engineering fixture and never establish provider usage or cost.
    """
    from devbench.analysis.rna_trace_receipts import LocalTraceObserver, observe_boundary
    from devbench.analysis.rna_trace_contract import trace_digest, canonical_bytes
    observer = LocalTraceObserver(tmp_path / 'context-observer.sqlite', source_digest='a' * 64,
        runtime_digest='b' * 64, policy_digest='c' * 64)
    workflow_id = 'PRIVATE_CONTEXT_WORKFLOW_CANARY'
    def operation():
        parent = [row for row in observer.read_contexts(workflow_id)
                  if row['role'] == 'lead' and row['phase'] == 'generate'][-1]
        child = {**parent, 'span_id': str(uuid4()), 'parent_span_id': parent['span_id'], 'phase': 'selection'}
        with observer._db() as db:
            db.execute('INSERT INTO contexts VALUES (?,?)',
                (trace_digest([parent['span_id'], 'selection']), canonical_bytes(child).decode()))
        observer._emit(parent, trace_digest(workflow_id), 'capture', 'completed', capture='verified')
        observer._emit(child, trace_digest(workflow_id), 'capture', 'completed', capture='verified')
        if not open_child or parent['attempt'] != 1:
            observer._emit(child, trace_digest(workflow_id), 'completion', 'completed', capture='verified')
        return {'private_result': 'PRIVATE_CONTEXT_RESULT_CANARY'}
    for attempt in range(1, 4):
        observe_boundary(observer, workflow_id, 'generate', operation, role='lead',
            binding_digest=trace_digest([workflow_id, attempt]))
    for phase in ('permission_preflight', 'synthesis'):
        observe_boundary(observer, workflow_id, phase, lambda: None,
            binding_digest=trace_digest([workflow_id, phase]))
    return observer, workflow_id


def test_v3_preserves_and_refuses_actual_nine_context_twenty_event_counterexample(tmp_path):
    from collections import Counter
    from devbench.analysis.rna_trace_contract import trace_digest
    observer, workflow_id = _actual_context_observer(tmp_path)
    before = deepcopy(observer.read_events(workflow_id))
    contexts = observer.read_contexts(workflow_id)
    assert len(contexts) == 9 and len(before) == 20
    assert Counter(row['kind'] for row in before) == {'reservation': 6, 'capture': 6, 'completion': 8}
    root = next(row for row in contexts if row['parent_span_id'] is None)
    assert [row['source_status'] for row in before if row['span_id'] == root['span_id']] == ['active']
    assert {row['attempt'] for row in contexts if row['role'] == 'lead' and row['phase'] == 'generate'} == {1, 2, 3}
    session = _session(_topology())
    session.update(source_digest='a' * 64, runtime_digest='b' * 64, policy_digest='c' * 64,
                   workflow_binding_digest=trace_digest(workflow_id))
    bridge = _api().WorkflowReceiptBridge(authorized_session_manifest=session, output_path=tmp_path / 'refused-v3.jsonl')
    with pytest.raises(ValueError):
        bridge.emit_new(observer=observer, workflow_id=workflow_id)
    assert observer.read_events(workflow_id) == before
    assert (tmp_path / 'refused-v3.jsonl').read_bytes() == b''


def _context_scope(tmp_path):
    from devbench.analysis.rna_trace_contract import trace_digest
    from devbench.study.contracts import canonical_json_bytes
    from devbench.study.langsmith_outbox import digest
    name = 'devbench.study.rna_hosted_context_contract'
    assert importlib.util.find_spec(name) is not None, 'Additive context-observation contract is absent'
    contract = importlib.import_module(name)
    api = _context_api()
    observer, workflow_id = _actual_context_observer(tmp_path)
    observation = api.prepare_context_observation(observer=observer, workflow_id=workflow_id)
    identity = dict(source_digest='a' * 64, runtime_digest='b' * 64, policy_digest='c' * 64,
        criteria_digest=digest(canonical_json_bytes(contract.CONTEXT_CRITERIA)),
        fixture_manifest_digest=digest(canonical_json_bytes(contract.CONTEXT_FIXTURE)))
    now = datetime.now(timezone.utc)
    expiry = (now + timedelta(minutes=5)).isoformat()
    session = dict(format='devbench.rna-live-trace-session/4', workflow_alias=str(uuid4()),
        workflow_binding_digest=trace_digest(workflow_id), data_scope='synthetic',
        **{key: identity[key] for key in ('source_digest', 'runtime_digest', 'policy_digest')},
        endpoint=contract.TARGET['endpoint'], project=contract.TARGET['project_name'],
        project_id=contract.TARGET['project_id'], workspace_id=contract.TARGET['workspace_id'],
        redaction_version='rna-allowlist/1', event_schema='devbench.rna-trace-event/1',
        allowed_event_fields=sorted(importlib.import_module('devbench.study.rna_langsmith_live').EVENT_FIELDS),
        max_spans=9, max_bytes=262144, max_delivery_attempts=1, expires_at=expiry,
        authorize_upload=False, span_topology=observation['topology'], context_profile=observation['profile'])
    packet = contract.prepare_context_packet(installed_identity=identity, target=deepcopy(contract.TARGET),
        session=session, topology=observation['topology'], profile=observation['profile'],
        expires_at=expiry, evidence_path=str(tmp_path / 'context-evidence'), now=now)
    return api, contract, identity, observer, workflow_id, packet


def _context_approvals(tmp_path, contract, packet):
    from devbench.study.contracts import canonical_json_bytes
    from devbench.study.langsmith_outbox import digest
    owner, reviewer = str(uuid4()), str(uuid4())
    records = {'source_runtime_review': dict(format='devbench.rna-context-review/4',
        packet_digest=contract.packet_digest(packet), binding=packet['binding'], target=packet['target'],
        expires_at=packet['expires_at'], approved=True, reviewer_id=reviewer, implementation_owner_id=owner)}
    for action in ('target_read', 'live_session'):
        records[action] = dict(format='devbench.rna-hosted-action-approval/4',
            proposal_digest=contract.packet_digest(packet[action]), action=action, approved=True,
            operator_id=reviewer, implementation_owner_id=owner, expires_at=packet['expires_at'])
    handles = {}
    for action, record in records.items():
        path = tmp_path / ('context-' + action + '.json')
        path.write_bytes(canonical_json_bytes(record))
        path.chmod(0o600)
        handles[action] = dict(path=str(path), sha256=digest(path.read_bytes()))
    return handles


def test_v4_preparation_freezes_actual_contexts_events_and_explicit_terminal_set(tmp_path):
    api = _context_api()
    observer, workflow_id = _actual_context_observer(tmp_path)
    before = deepcopy(observer.read_events(workflow_id))
    prepared = api.prepare_context_observation(observer=observer, workflow_id=workflow_id)
    assert set(prepared) == {'topology', 'profile'}
    topology, profile = prepared['topology'], prepared['profile']
    assert len(topology) == profile['context_count'] == 9
    assert profile['source_event_count'] == 20 and profile['capture_event_count'] == 6
    assert profile['dispatch_event_count'] == 0 and profile['operation_count'] == 17
    assert profile['open_span_ids'] == [topology[0]['span_id']]
    assert len(profile['terminal_span_ids']) == 8
    assert sum(not row['start_observed'] for row in profile['contexts']) == 3
    assert profile['usage_policy'] == 'unavailable'
    assert profile['digest_policy'] == 'session_aliased_sanitized_receipts'
    assert {row['attempt'] for row in topology if row['role'] == 'lead' and row['phase'] == 'generate'} == {1, 2, 3}
    from devbench.study.contracts import canonical_json_bytes
    raw = canonical_json_bytes(prepared).decode()
    assert 'PRIVATE_' not in raw and str(tmp_path) not in raw
    raw_ids = {row[key] for row in before for key in ('span_id', 'trace_id', 'event_id')}
    assert not any(value in raw for value in raw_ids)
    assert observer.read_events(workflow_id) == before


def test_v4_bridge_preserves_source_sequence_and_capture_only_contexts_without_finishing_open_root(tmp_path):
    api, _, _, observer, workflow_id, packet = _context_scope(tmp_path)
    before = deepcopy(observer.read_events(workflow_id))
    session = {**packet['session'], 'authorize_upload': True}
    bridge = api.ContextReceiptBridge(authorized_session_manifest=session, output_path=tmp_path / 'context-ops.jsonl')
    operations = bridge.emit_new(observer=observer, workflow_id=workflow_id)
    assert len(operations) == 17
    assert sum(item['operation']['operation'] == 'start' for item in operations) == 9
    assert sum(item['operation']['operation'] == 'finish' for item in operations) == 8
    root = packet['topology'][0]['span_id']
    root_rows = [item for item in operations if item['payload']['id'] == root]
    assert len(root_rows) == 1 and 'end_time' not in root_rows[0]['payload']
    assert root_rows[0]['payload']['extra']['metadata']['source_status'] == 'active'
    captures = [item for item in operations if item['operation']['operation'] == 'start'
                and item['payload']['extra']['metadata']['phase'] == 'selection']
    assert len(captures) == 3
    for item in captures:
        metadata = item['payload']['extra']['metadata']
        assert metadata['start_observed'] is False and metadata['source_sequence'] == 0
        assert metadata['kind'] == 'capture' and metadata['source_status'] == 'completed'
        assert 'end_time' not in item['payload'] and metadata['metrics']['elapsed_seconds'] is None
    finishes = [item for item in operations if item['operation']['operation'] == 'finish'
                and item['payload']['extra']['metadata']['phase'] == 'generate']
    assert len(finishes) == 3
    assert all(item['payload']['extra']['metadata']['source_sequence'] == 2 for item in finishes)
    assert all(item['operation']['revision'] == 1 for item in finishes)
    for item in operations:
        metadata = item['payload']['extra']['metadata']
        assert metadata['metrics']['usage_basis'] == 'unavailable'
        assert all(metadata['metrics'][key] is None for key in ('input_tokens', 'output_tokens', 'total_tokens'))
        assert metadata['metrics']['provider_cost']['basis'] == 'unavailable'
        api.validate_context_wire_operation(item, packet=packet)
    assert observer.read_events(workflow_id) == before
    assert bridge.emit_new(observer=observer, workflow_id=workflow_id) == []
    reopened = api.ContextReceiptBridge(authorized_session_manifest=session, output_path=tmp_path / 'context-ops.jsonl')
    assert reopened.emit_new(observer=observer, workflow_id=workflow_id) == []
    raw = (tmp_path / 'context-ops.jsonl').read_text()
    assert 'PRIVATE_' not in raw and str(tmp_path) not in raw
    assert not any(row['span_id'] in raw or row['event_id'] in raw for row in before)


def _context_engine(tmp_path, monkeypatch, *, mutation_status=202, lost_response=False, run_changes=None):
    import io
    import json
    import ssl
    from types import SimpleNamespace
    from urllib.request import HTTPSHandler, ProxyHandler
    from devbench.study import rna_hosted_exporter as exporter
    api, contract, identity, observer, workflow_id, packet = _context_scope(tmp_path)
    handles = _context_approvals(tmp_path, contract, packet)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(exporter, '_CHILD_PROCESS_PID', os.getpid())
    monkeypatch.setattr(api, 'context_identity', lambda: deepcopy(identity))
    calls, remote = [], {}
    def credential():
        calls.append(('credential',))
        return 'PRIVATE_CONTEXT_KEY_CANARY'
    monkeypatch.setattr(exporter, '_resolve_keychain_credential', credential)
    class Response:
        def __init__(self, status, body):
            self.status = status
            raw = json.dumps(body).encode()
            self.stream = io.BytesIO(raw)
            self.length, self.chunked = len(raw), False
        def isclosed(self): return self.length == 0
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def read(self, size):
            assert 0 <= size <= 131073
            value = self.stream.read(size)
            self.length -= len(value)
            return value
    class Opener:
        def open(self, request, timeout):
            method = request.get_method()
            path = request.full_url.split('langchain.com', 1)[1]
            assert request.get_header('X-api-key') == 'PRIVATE_CONTEXT_KEY_CANARY'
            assert 0 < timeout <= 15
            calls.append((method, path))
            if path == packet['read_descriptor']['project_route']:
                target = packet['target']
                return Response(200, dict(id=target['project_id'], tenant_id=target['workspace_id'], name=target['project_name']))
            if method in ('POST', 'PATCH'):
                payload = json.loads(request.data)
                remote[payload['id']] = payload
                if lost_response: raise TimeoutError('PRIVATE_CONTEXT_PROVIDER_CANARY')
                return Response(mutation_status, {'private': 'PRIVATE_CONTEXT_PROVIDER_CANARY'})
            body = remote.get(path.rsplit('/', 1)[1])
            if body is None: return Response(404, {})
            body = deepcopy(body)
            body.pop('session_name', None)
            body.update(status='error' if 'error' in body else 'success' if 'end_time' in body else 'pending',
                        app_path='/synthetic-context')
            body.update(run_changes or {})
            return Response(200, body)
    def opener(*handlers):
        assert any(isinstance(handler, ProxyHandler) and handler.proxies == {} for handler in handlers)
        tls = [handler for handler in handlers if isinstance(handler, HTTPSHandler)]
        assert len(tls) == 1 and tls[0]._context.verify_mode == ssl.CERT_REQUIRED and tls[0]._context.check_hostname
        return Opener()
    monkeypatch.setattr(api, 'build_opener', opener)
    session = {**packet['session'], 'authorize_upload': True}
    bridge = api.ContextReceiptBridge(authorized_session_manifest=session, output_path=tmp_path / 'context-ops.jsonl')
    operations = bridge.emit_new(observer=observer, workflow_id=workflow_id)
    descriptor = dict(format='devbench.rna-hosted-exporter-session/4', packet=packet, approval_handles=handles)
    return SimpleNamespace(api=api, packet=packet, descriptor=descriptor, operations=operations,
                           calls=calls, remote=remote, handles=handles, observer=observer, workflow_id=workflow_id)


def test_v4_actual_child_boundary_rejects_before_any_credential_or_output(tmp_path, monkeypatch):
    api = _context_api()
    from devbench.study import rna_hosted_exporter as exporter
    calls = []
    monkeypatch.setattr(exporter, '_CHILD_PROCESS_PID', os.getpid() + 1)
    monkeypatch.setattr(exporter, '_resolve_keychain_credential', lambda: calls.append('credential'))
    with pytest.raises(PermissionError, match='child'):
        api.HostedContextObservation({})
    assert calls == [] and list(tmp_path.iterdir()) == []


def test_v4_engine_observes_seventeen_operations_and_verifies_latest_revision_for_open_root(tmp_path, monkeypatch):
    fixture = _context_engine(tmp_path, monkeypatch)
    engine = fixture.api.HostedContextObservation(fixture.descriptor)
    try:
        for item in fixture.operations:
            state = engine.emit(item)
        assert state['format'] == 'devbench.rna-hosted-exporter-status/4'
        assert state['schema_mode'] == 'experimental_context_observation'
        assert state['counts']['acknowledged'] == 17
        assert state['live_span_count'] == 9 and state['terminal_span_count'] == 8
        assert state['source_event_count'] == 20 and state['capture_event_count'] == 6
        result = engine.verify()
        assert result['verification_result'] == 'exact_context_match'
        assert result['verified_span_count'] == len(result['receipts']) == 9
        root = fixture.packet['topology'][0]['span_id']
        assert next(row for row in result['receipts'] if row['span_id'] == root)['revision'] == 0
        assert engine.status()['counts']['mutation_requests'] == 17
        assert engine.status()['counts']['read_requests'] == 11
        assert engine.status()['deployed_schema_qualified'] is False
    finally:
        engine.close()
    assert fixture.calls.count(('credential',)) == 1
    assert len([call for call in fixture.calls if call[0] in ('POST', 'PATCH')]) == 17
    assert not any('PRIVATE_' in path.read_text(errors='ignore')
                   for path in (tmp_path / 'context-evidence').rglob('*') if path.is_file())
    assert engine.status()['authorize_upload'] is False


def test_v4_bridge_refuses_postfreeze_event_or_context_drift_without_rewriting_sources(tmp_path):
    from devbench.analysis.rna_trace_receipts import observe_boundary
    from devbench.analysis.rna_trace_contract import trace_digest
    api, _, _, observer, workflow_id, packet = _context_scope(tmp_path)
    session = {**packet['session'], 'authorize_upload': True}
    bridge = api.ContextReceiptBridge(authorized_session_manifest=session, output_path=tmp_path / 'context-ops.jsonl')
    bridge.emit_new(observer=observer, workflow_id=workflow_id)
    before_output = (tmp_path / 'context-ops.jsonl').read_bytes()
    observe_boundary(observer, workflow_id, 'generate', lambda: None, role='lead',
                     binding_digest=trace_digest([workflow_id, 'later-unapproved-attempt']))
    before_source = deepcopy(observer.read_events(workflow_id))
    with pytest.raises(ValueError, match='profile|frozen|context|source'):
        bridge.emit_new(observer=observer, workflow_id=workflow_id)
    assert observer.read_events(workflow_id) == before_source
    assert (tmp_path / 'context-ops.jsonl').read_bytes() == before_output


@pytest.mark.parametrize('mutation', ['unknown', 'parent', 'sequence', 'open_root_finish'])
def test_v4_engine_rejects_unapproved_or_changed_context_operation_before_http(tmp_path, monkeypatch, mutation):
    from devbench.study.contracts import canonical_json_bytes
    from devbench.study.langsmith_outbox import digest
    from devbench.study.rna_langsmith_verify import bind_remote_proof
    fixture = _context_engine(tmp_path, monkeypatch)
    engine = fixture.api.HostedContextObservation(fixture.descriptor)
    try:
        item = deepcopy(fixture.operations[0])
        if mutation == 'unknown': item['payload']['prompt'] = 'PRIVATE_CONTEXT_CANARY'
        elif mutation == 'parent': item['payload']['parent_run_id'] = str(uuid4())
        elif mutation == 'sequence': item['payload']['extra']['metadata']['source_sequence'] = 99
        else:
            item['operation'].update(operation='finish', revision=1)
            item['payload']['end_time'] = item['payload']['start_time']
            item['payload']['extra']['metadata'].update(kind='completion', source_status='completed')
        if mutation != 'unknown':
            item['payload'] = bind_remote_proof(item['payload'], item['operation'])
            item['operation']['payload_sha256'] = digest(canonical_json_bytes(item['payload']))
        before = list(fixture.calls)
        with pytest.raises((ValueError, PermissionError)):
            engine.emit(item)
        assert fixture.calls == before and engine.box is None
    finally:
        engine.close()


@pytest.mark.parametrize('lost_response,mutation_status', [(True, 202), (False, 500), (False, 400)])
def test_v4_engine_refuses_resend_after_unacknowledged_operation(tmp_path, monkeypatch, lost_response, mutation_status):
    fixture = _context_engine(tmp_path, monkeypatch, lost_response=lost_response, mutation_status=mutation_status)
    engine = fixture.api.HostedContextObservation(fixture.descriptor)
    try:
        state = engine.emit(fixture.operations[0])
        assert state['authorize_upload'] is False
        before = list(fixture.calls)
        with pytest.raises((ValueError, PermissionError)):
            engine.emit(fixture.operations[0])
        assert fixture.calls == before
    finally:
        engine.close()


def test_v4_packet_reservation_survives_credential_failure_and_replay_stays_closed(tmp_path, monkeypatch):
    fixture = _context_engine(tmp_path, monkeypatch)
    from devbench.study import rna_hosted_exporter as exporter
    def unavailable(): raise RuntimeError('PRIVATE_CONTEXT_KEY_CANARY')
    monkeypatch.setattr(exporter, '_resolve_keychain_credential', unavailable)
    with pytest.raises(RuntimeError): fixture.api.HostedContextObservation(fixture.descriptor)
    assert (tmp_path / 'context-evidence' / 'connection.sqlite').is_file()
    before = list(fixture.calls)
    replay = fixture.api.HostedContextObservation(fixture.descriptor)
    assert replay.status()['blockers'] == ['packet_already_consumed']
    assert replay.status()['authorize_upload'] is False and fixture.calls == before
    replay.close()


def test_v4_read_mismatch_retains_only_bounded_hash_length_and_closed_category(tmp_path, monkeypatch):
    fixture = _context_engine(tmp_path, monkeypatch, run_changes={'status': 'PRIVATE_CONTEXT_STATUS_CANARY'})
    engine = fixture.api.HostedContextObservation(fixture.descriptor)
    try:
        for item in fixture.operations:
            engine.emit(item)
        result = engine.verify()
        assert result['verification_result'] == 'context_mismatch'
        assert result['verified_span_count'] == 0
        assert len(result['receipts']) == len(result['diagnostics']) == 9
        for diagnostic in result['diagnostics']:
            assert set(diagnostic) == {'span_id', 'body_length', 'body_sha256', 'mismatch_category'}
            assert type(diagnostic['body_length']) is int and 0 <= diagnostic['body_length'] <= 131073
            assert len(diagnostic['body_sha256']) == 64
            assert diagnostic['mismatch_category'] == 'provider_status_mismatch'
        assert 'PRIVATE_' not in str(result)
    finally:
        engine.close()
    assert not any('PRIVATE_' in path.read_text(errors='ignore')
                   for path in (tmp_path / 'context-evidence').rglob('*') if path.is_file())


def test_v4_exporter_parent_rejects_private_operation_before_ipc(tmp_path, monkeypatch):
    from devbench.study import rna_hosted_exporter as exporter
    api, contract, _, _, _, packet = _context_scope(tmp_path)
    handles = _context_approvals(tmp_path, contract, packet)
    descriptor = dict(format='devbench.rna-hosted-exporter-session/4', packet=packet, approval_handles=handles)
    assert api.validate_context_descriptor(descriptor) == descriptor
    channel = exporter.HostedExporterChannel.__new__(exporter.HostedExporterChannel)
    channel._connection_v2 = channel._workflow_v3 = False
    channel._context_v4 = channel._bounded_connection = True
    channel._workflow_packet = packet
    calls = []
    monkeypatch.setattr(channel, '_send', lambda value: calls.append(value))
    with pytest.raises(ValueError):
        channel.request({'command': 'emit', 'operation': {'prompt': 'PRIVATE_CONTEXT_IPC_CANARY'}})
    assert calls == []


def test_v4_isolated_original_exporter_entry_observes_open_root_without_real_network(tmp_path, monkeypatch):
    import json
    import sys
    from pathlib import Path
    from devbench.study import rna_hosted_exporter as exporter
    from tests.test_rna_hosted_connection import TRUSTED_CHILD, audit_rows
    api, contract, identity, observer, workflow_id, packet = _context_scope(tmp_path)
    handles = _context_approvals(tmp_path, contract, packet)
    bridge = api.ContextReceiptBridge(authorized_session_manifest={**packet['session'], 'authorize_upload': True},
        output_path=tmp_path / 'context-isolated-ops.jsonl')
    operations = bridge.emit_new(observer=observer, workflow_id=workflow_id)
    config = tmp_path / 'context-trusted-fixture.json'
    config.write_text(json.dumps(dict(target=packet['target'], identity=identity)))
    audit = tmp_path / 'context-audit.jsonl'
    child = TRUSTED_CHILD.replace('from devbench.study import rna_hosted_connection as connection',
        'from devbench.study import rna_hosted_connection as connection\nfrom devbench.study import rna_hosted_workflow as workflow\nworkflow.context_identity = lambda: config["identity"]')
    child = child.replace('connection.build_opener=opener', 'connection.build_opener=opener\nworkflow.build_opener=opener')
    child = child.replace('<= 65537', '<= 131073')
    child = child.replace('self.stream=io.BytesIO(json.dumps(body).encode())', 'self.stream=io.BytesIO(json.dumps(body).encode());self.length=len(self.stream.getvalue());self.chunked=False')
    child = child.replace('    def __enter__(self): return self', '    def isclosed(self): return self.length == 0\n    def __enter__(self): return self')
    child = child.replace('return self.stream.read(size)', 'value=self.stream.read(size);self.length-=len(value);return value')
    monkeypatch.setattr(exporter, '_child_command', lambda: [sys.executable, '-I', '-B', '-c', child, str(config), str(audit)])
    descriptor = dict(format='devbench.rna-hosted-exporter-session/4', packet=packet, approval_handles=handles)
    with exporter.HostedExporterChannel(root=Path(packet['evidence_path']), descriptor=descriptor,
            credential_handle=exporter.keychain_credential_handle()) as channel:
        assert channel.request({'command': 'status'})['status'] == 'admitted'
        boundary = channel.request({'command': 'boundary'})
        assert boundary['pid'] != os.getpid() and boundary['credential_resolutions'] == 1
        for item in operations:
            state = channel.request({'command': 'emit', 'operation': item})
        assert state['counts']['acknowledged'] == 17
        result = channel.request({'command': 'verify'})
        assert result['verification_result'] == 'exact_context_match' and len(result['receipts']) == 9
    rows = audit_rows(audit)
    assert [row['kind'] for row in rows].count('credential') == 1
    assert not any(row['kind'] == 'forbidden' for row in rows)
    http = [row for row in rows if row['kind'] == 'http']
    assert len([row for row in http if row['method'] == 'POST']) == 9
    assert len([row for row in http if row['method'] == 'PATCH']) == 8
    assert len([row for row in http if row['method'] == 'GET']) == 11


@pytest.mark.parametrize('wire,limit,body,complete,oversized', [
    (b'HTTP/1.1 200 OK\r\nContent-Length: 10\r\n\r\nabc', 131072, b'', False, False),
    (b'HTTP/1.1 200 OK\r\nContent-Length: 3\r\n\r\nabc', 131072, b'abc', True, False),
    (b'HTTP/1.0 200 OK\r\n\r\nabc', 131072, b'', False, False),
    (b'HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n3\r\nabc\r\n0\r\n\r\n', 131072, b'abc', True, False),
    (b'HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n3\r\nab', 131072, b'', False, False),
    (b'HTTP/1.1 200 OK\r\nContent-Length: 3\r\n\r\nabc', 2, b'', False, True),
    (b'HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n3\r\nabc\r\n0\r\n', 131072, b'', False, False),
    (b'HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n3\r\nabcXX0\r\n\r\n', 131072, b'', False, False),
])
def test_v4_http_body_evidence_requires_complete_actual_framing(wire, limit, body, complete, oversized):
    import io
    from http.client import HTTPResponse
    api = _context_api()
    read = getattr(api, '_read_context_http_body', None)
    assert callable(read), 'Bounded complete-framing /4 reader is absent'
    class FakeSocket:
        def makefile(self, *args, **kwargs): return io.BytesIO(wire)
    from devbench.study.rna_hosted_tls import StrictTrailerHTTPResponse
    response_class = StrictTrailerHTTPResponse if b'Transfer-Encoding: chunked' in wire else HTTPResponse
    response = response_class(FakeSocket())
    response.begin()
    try:
        result = read(response, limit=limit)
        assert result == {'body': body, 'complete': complete, 'oversized': oversized}
    finally:
        response.close()


@pytest.mark.parametrize('status', [404, 401])
def test_v4_unread_http_error_never_claims_empty_whole_body_hash(tmp_path, monkeypatch, status):
    import io
    from urllib.error import HTTPError
    fixture = _context_engine(tmp_path, monkeypatch)
    original = fixture.api.build_opener
    body_reads = []
    class PrivateErrorBody(io.BytesIO):
        def read(self, *args, **kwargs):
            body_reads.append('read')
            raise AssertionError('The HTTP error body must remain unread')
    class ErrorOpener:
        def __init__(self, delegate): self.delegate = delegate
        def open(self, request, timeout):
            if request.get_method() == 'GET' and '/api/v1/runs/' in request.full_url:
                fixture.calls.append(('GET', request.full_url.split('langchain.com', 1)[1]))
                raise HTTPError(request.full_url, status, 'PRIVATE_CONTEXT_ERROR_CANARY', {},
                    PrivateErrorBody(b'PRIVATE_CONTEXT_BODY_CANARY'))
            return self.delegate.open(request, timeout)
    monkeypatch.setattr(fixture.api, 'build_opener', lambda *handlers: ErrorOpener(original(*handlers)))
    engine = fixture.api.HostedContextObservation(fixture.descriptor)
    try:
        for item in fixture.operations:
            engine.emit(item)
        result = engine.verify()
        assert result['verification_result'] == 'context_mismatch'
        assert len(result['receipts']) == len(result['diagnostics']) == 9
        assert body_reads == []
        for row in result['diagnostics']:
            assert row['body_length'] is row['body_sha256'] is None
        assert result['diagnostics'][0]['mismatch_category'] == 'body_incomplete'
        assert all(row['mismatch_category'] == 'body_unread' for row in result['diagnostics'][1:])
        assert engine.status()['blockers'] == ['body_read_uncertain']
        assert engine.status()['counts']['read_requests'] == 3
        assert 'PRIVATE_' not in str(result)
    finally:
        engine.close()


def test_v4_terminal_parent_preserves_profile_open_child_without_inventing_finish(tmp_path, monkeypatch):
    import sys
    original = _actual_context_observer
    monkeypatch.setattr(sys.modules[__name__], '_actual_context_observer',
                        lambda root: original(root, open_child=True))
    fixture = _context_engine(tmp_path, monkeypatch)
    topology = fixture.packet['topology']
    child = next(row['span_id'] for row in topology
                 if row['role'] == 'lead' and row['phase'] == 'selection' and row['attempt'] == 1)
    parent = next(row['parent_span_id'] for row in topology if row['span_id'] == child)
    assert child in fixture.packet['profile']['open_span_ids']
    assert parent in fixture.packet['profile']['terminal_span_ids']
    assert not any(item['payload']['id'] == child and item['operation']['operation'] == 'finish'
                   for item in fixture.operations)
    engine = fixture.api.HostedContextObservation(fixture.descriptor)
    try:
        for item in fixture.operations:
            state = engine.emit(item)
        assert state['counts']['acknowledged'] == 16
        assert state['live_span_count'] == 9 and state['terminal_span_count'] == 7
        assert 'end_time' not in fixture.remote[child] and 'end_time' in fixture.remote[parent]
        result = engine.verify()
        assert result['verification_result'] == 'exact_context_match'
        assert next(row for row in result['receipts'] if row['span_id'] == child)['revision'] == 0
        assert next(row for row in result['receipts'] if row['span_id'] == parent)['revision'] == 1
    finally:
        engine.close()


def test_v4_https_selects_strict_response_per_connection_without_global_patch(monkeypatch):
    import ssl
    from http.client import HTTPResponse, HTTPSConnection
    from devbench.study.rna_hosted_tls import StrictTrailerHTTPResponse
    api = _context_api()
    handler_type = getattr(api, '_ContextHTTPSHandler', None)
    assert callable(handler_type), 'The /4 per-connection strict HTTP handler is absent'
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    handler = handler_type(context=context)
    observed = []
    def open_connection(connection_class, request, **kwargs):
        observed.append((connection_class, request, kwargs))
        assert connection_class.response_class is StrictTrailerHTTPResponse
        assert kwargs['context'] is context
        return 'offline-strict-response'
    monkeypatch.setattr(handler, 'do_open', open_connection)
    assert handler.https_open('offline-request') == 'offline-strict-response'
    assert len(observed) == 1 and HTTPSConnection.response_class is HTTPResponse


def test_v4_bridge_refuses_profile_alias_borrowed_from_raw_context_identity(tmp_path):
    from devbench.study.contracts import canonical_json_bytes
    from devbench.study.langsmith_outbox import digest
    from devbench.study.rna_langsmith_live import project_context_receipt
    api, contract, _, observer, workflow_id, packet = _context_scope(tmp_path)
    contexts = observer.read_contexts(workflow_id)
    events = observer.read_events(workflow_id)
    profile = deepcopy(packet['profile'])
    old = profile['contexts'][0]['context_alias']
    raw_alias = contexts[0]['workflow_alias']
    profile['contexts'][0]['context_alias'] = raw_alias
    for row in profile['contexts']:
        if row['parent_context_alias'] == old:
            row['parent_context_alias'] = raw_alias
    topology = {row['span_id']: row for row in packet['topology']}
    actual_slots = {(row['role'], row['phase'], row['attempt']): row for row in contexts}
    for row in profile['contexts']:
        slot = topology[row['span_id']]
        context = actual_slots[(slot['role'], slot['phase'], slot['attempt'])]
        source = [event for event in events if event['span_id'] == context['span_id']]
        row['source_event_digests'] = [digest(canonical_json_bytes(project_context_receipt(event,
            context_alias=row['context_alias'], parent_context_alias=row['parent_context_alias']))) for event in source]
    contract.validate_context_profile(profile, topology=packet['topology'])
    session = {**packet['session'], 'context_profile': profile, 'authorize_upload': True}
    path = tmp_path / 'raw-alias-refused.jsonl'
    bridge = api.ContextReceiptBridge(authorized_session_manifest=session, output_path=path)
    before = deepcopy(observer.read_events(workflow_id))
    with pytest.raises(ValueError, match='alias|opaque|raw|fresh'):
        bridge.emit_new(observer=observer, workflow_id=workflow_id)
    assert path.read_bytes() == b'' and observer.read_events(workflow_id) == before


def test_v4_nondecoder_transport_timeout_preserves_all_closed_unread_diagnostics(tmp_path, monkeypatch):
    fixture = _context_engine(tmp_path, monkeypatch)
    original = fixture.api.build_opener
    class TimeoutOpener:
        def __init__(self, delegate): self.delegate = delegate
        def open(self, request, timeout):
            if request.get_method() == 'GET' and '/api/v1/runs/' in request.full_url:
                fixture.calls.append(('GET', request.full_url.split('langchain.com', 1)[1]))
                raise TimeoutError('PRIVATE_CONTEXT_TIMEOUT_CANARY')
            return self.delegate.open(request, timeout)
    monkeypatch.setattr(fixture.api, 'build_opener', lambda *handlers: TimeoutOpener(original(*handlers)))
    engine = fixture.api.HostedContextObservation(fixture.descriptor)
    try:
        for item in fixture.operations:
            engine.emit(item)
        result = engine.verify()
        assert result['verification_result'] == 'context_mismatch'
        assert len(result['receipts']) == len(result['diagnostics']) == 9
        assert engine.status()['counts']['read_requests'] == 11
        assert all(row['body_length'] is None and row['body_sha256'] is None
                   and row['mismatch_category'] == 'body_unread' for row in result['diagnostics'])
        assert 'PRIVATE_' not in str(result)
    finally:
        engine.close()


def test_v4_approved_outbound_aliases_stay_exact_when_private_probe_ids_change(tmp_path, monkeypatch):
    from uuid import UUID
    fixture = _context_engine(tmp_path, monkeypatch)
    approved = {row['span_id']: row for row in fixture.packet['profile']['contexts']}
    generated = []
    def private_probe_id():
        value = UUID(int=0x100000 + len(generated))
        generated.append(str(value))
        return value
    # Only private validation probes and request nonces are allocated now.
    # The prepared topology/profile already binds every outbound identity.
    monkeypatch.setattr(fixture.api, 'uuid4', private_probe_id)
    bridge = fixture.api.ContextReceiptBridge(
        authorized_session_manifest={**fixture.packet['session'], 'authorize_upload': True},
        output_path=tmp_path / 'approved-alias-reprojection.jsonl')
    reprojection = bridge.emit_new(observer=fixture.observer, workflow_id=fixture.workflow_id)
    def invariant_operation(item):
        payload = deepcopy(item['payload'])
        # A fresh journal allocates its own operation IDs. Their proof and wire
        # hashes change with that journal; approved source/alias content does not.
        payload['extra']['metadata'].pop('remote_proof')
        envelope = {key: value for key, value in item['operation'].items()
                    if key not in {'operation_id', 'payload_sha256'}}
        return {'operation': envelope, 'payload': payload}
    assert [invariant_operation(item) for item in reprojection] == [
        invariant_operation(item) for item in fixture.operations]
    engine = fixture.api.HostedContextObservation(fixture.descriptor)
    try:
        for item in reprojection:
            engine.emit(item)
        assert set(fixture.remote) == set(approved)
        for identity, payload in fixture.remote.items():
            row = approved[identity]
            metadata = payload['extra']['metadata']
            assert payload['trace_id'] == fixture.packet['topology'][0]['span_id']
            assert metadata['context_alias'] == row['context_alias']
            assert metadata['parent_context_alias'] == row['parent_context_alias']
        outbound = set(approved) | {row['context_alias'] for row in approved.values()}
        assert generated and not outbound.intersection(generated)
    finally:
        engine.close()


@pytest.mark.parametrize('failure', [TimeoutError, ConnectionResetError])
def test_v4_read_prefix_failure_charges_known_bytes_and_enforces_aggregate_cap(tmp_path, monkeypatch, failure):
    fixture = _context_engine(tmp_path, monkeypatch)
    engine = fixture.api.HostedContextObservation(fixture.descriptor)
    original = fixture.api.build_opener
    consumed = []
    class PrefixFailureResponse:
        status, chunked = 200, False
        def __init__(self):
            self.length, self.used = 200000, 0
            consumed.append(self)
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def isclosed(self): return False
        def read(self, size):
            count = min(size, 70000 - self.used)
            if not count:
                raise failure('PRIVATE_CONTEXT_PREFIX_FAILURE_CANARY')
            self.used += count
            self.length -= count
            return b'x' * count
    class PrefixFailureOpener:
        def __init__(self, delegate): self.delegate = delegate
        def open(self, request, timeout):
            if request.get_method() == 'GET' and '/api/v1/runs/' in request.full_url:
                fixture.calls.append(('GET', request.full_url.split('langchain.com', 1)[1]))
                return PrefixFailureResponse()
            return self.delegate.open(request, timeout)
    try:
        for item in fixture.operations:
            engine.emit(item)
        monkeypatch.setattr(fixture.api, 'build_opener', lambda *handlers: PrefixFailureOpener(original(*handlers)))
        # Verification refreshes one project read before these response bodies.
        result = engine.verify()
        assert result['verification_result'] == 'context_mismatch'
        assert len(result['receipts']) == 9
        assert len(consumed) == 1 and consumed[0].used == 70000
        assert all(row['body_length'] is None and row['body_sha256'] is None for row in result['diagnostics'])
        assert engine.status()['blockers'] == ['body_read_uncertain']
        assert engine.status()['counts']['read_requests'] == 3
        with engine._db() as db:
            charged = db.execute('SELECT response_bytes FROM connection').fetchone()[0]
        assert sum(response.used for response in consumed) <= charged < fixture.packet['limits']['max_response_bytes']
        import json
        failures = list((tmp_path / 'context-evidence').glob('body-read-failure-*.json'))
        assert len(failures) == 1
        accounting = json.loads(failures[0].read_bytes())
        assert set(accounting) == {'format', 'request_index', 'known_body_bytes', 'total_body_bytes_known', 'additional_reads_authorized'}
        assert accounting['known_body_bytes'] == 70000
        assert accounting['total_body_bytes_known'] is accounting['additional_reads_authorized'] is False
        assert 'PRIVATE_' not in str(result)
    finally:
        engine.close()


def _raised_framed_context_error(request, status, wire, *, captured, failure=None):
    """Exercise urllib's raised-error path with its real strict response fp."""
    import io
    from urllib.error import HTTPError
    from devbench.study.rna_hosted_tls import StrictTrailerHTTPResponse
    class BodyStream(io.BytesIO):
        body_reads = 0
        def read(self, size=-1):
            if failure is not None:
                if self.body_reads == 70000:
                    raise failure('PRIVATE_CONTEXT_ERROR_PREFIX_CANARY')
                size = min(size, 70000 - self.body_reads)
            value = super().read(size)
            self.body_reads += len(value)
            return value
    stream = BodyStream(wire)
    class FakeSocket:
        def makefile(self, *args, **kwargs): return stream
    response = StrictTrailerHTTPResponse(FakeSocket())
    response.begin()
    captured.append((response, stream))
    return HTTPError(request.full_url, status, 'PRIVATE_CONTEXT_ERROR_CANARY', response.headers, response)


@pytest.mark.parametrize('chunked', [False, True], ids=['content-length', 'strict-chunked'])
def test_context_raised_framed_404_polls_to_exact_revision_without_resending_mutations(tmp_path, monkeypatch, chunked):
    fixture = _context_engine(tmp_path, monkeypatch)
    engine = fixture.api.HostedContextObservation(fixture.descriptor)
    original = fixture.api.build_opener
    root_path = fixture.packet['read_descriptor']['run_routes'][0]
    errors = []
    wire = (b'HTTP/1.1 404 Not Found\r\nTransfer-Encoding: chunked\r\n\r\n2\r\n{}\r\n0\r\n\r\n'
            if chunked else b'HTTP/1.1 404 Not Found\r\nContent-Length: 2\r\n\r\n{}')
    class EventualOpener:
        def __init__(self, delegate): self.delegate = delegate
        def open(self, request, timeout):
            if request.get_method() == 'GET' and request.full_url.endswith(root_path) and not errors:
                fixture.calls.append(('GET', root_path))
                raise _raised_framed_context_error(request, 404, wire, captured=errors)
            return self.delegate.open(request, timeout)
    try:
        for item in fixture.operations:
            engine.emit(item)
        mutations = [call for call in fixture.calls if call[0] in ('POST', 'PATCH')]
        monkeypatch.setattr(fixture.api, 'build_opener', lambda *handlers: EventualOpener(original(*handlers)))
        result = engine.verify()
        assert result['verification_result'] == 'exact_context_match'
        assert result['verified_span_count'] == 9
        assert fixture.calls.count(('GET', root_path)) == 2
        assert [row['response_class'] for row in engine.verifier.attempts[:2]] == ['not_visible', 'matched']
        assert engine.verifier.attempts[0]['status_code'] == 404
        assert engine.verifier.attempts[0]['bytes_read'] == 2
        assert engine.status()['counts']['read_requests'] == 12 <= fixture.packet['limits']['max_read_requests']
        assert engine.status()['counts']['mutation_requests'] == len(mutations) == 17
        assert [call for call in fixture.calls if call[0] in ('POST', 'PATCH')] == mutations
        assert engine._extra_polls == 1
        assert all(response.isclosed() and stream.closed for response, stream in errors)
        with pytest.raises(PermissionError, match='already attempted'):
            engine.verify()
        assert 'PRIVATE_' not in str(result)
    finally:
        engine.close()


@pytest.mark.parametrize('status,outcome,read_count', [(401, 'auth_denied', 3), (403, 'auth_denied', 3), (429, 'rate_limited', 11)])
def test_context_raised_framed_error_preserves_status_and_never_retries_mutations(tmp_path, monkeypatch, status, outcome, read_count):
    fixture = _context_engine(tmp_path, monkeypatch)
    engine = fixture.api.HostedContextObservation(fixture.descriptor)
    original = fixture.api.build_opener
    errors = []
    body = b'{"detail":"PRIVATE_CONTEXT_BODY_CANARY"}'
    wire = b'HTTP/1.1 ' + str(status).encode() + b' Error\r\nContent-Length: ' + str(len(body)).encode() + b'\r\n\r\n' + body
    class ErrorOpener:
        def __init__(self, delegate): self.delegate = delegate
        def open(self, request, timeout):
            if request.get_method() == 'GET' and '/api/v1/runs/' in request.full_url:
                fixture.calls.append(('GET', request.full_url.split('langchain.com', 1)[1]))
                raise _raised_framed_context_error(request, status, wire, captured=errors)
            return self.delegate.open(request, timeout)
    try:
        for item in fixture.operations:
            engine.emit(item)
        mutations = [call for call in fixture.calls if call[0] in ('POST', 'PATCH')]
        monkeypatch.setattr(fixture.api, 'build_opener', lambda *handlers: ErrorOpener(original(*handlers)))
        with engine._db() as db:
            before_bytes = db.execute('SELECT response_bytes FROM connection').fetchone()[0]
        result = engine.verify()
        assert result['verification_result'] == 'context_mismatch'
        assert all(row['response_class'] == outcome and row['status_code'] == status
                   and row['bytes_read'] == len(body) for row in engine.verifier.attempts)
        assert len(errors) == (1 if status in (401, 403) else 9)
        assert engine.status()['counts']['read_requests'] == read_count
        assert engine.status()['blockers'] == (['auth_denied'] if status in (401, 403) else [])
        assert [call for call in fixture.calls if call[0] in ('POST', 'PATCH')] == mutations
        assert engine.status()['counts']['mutation_requests'] == len(mutations) == 17
        assert all(response.isclosed() and stream.closed for response, stream in errors)
        with engine._db() as db:
            assert db.execute('SELECT response_bytes FROM connection').fetchone()[0] >= before_bytes + len(body) * len(errors)
        assert 'PRIVATE_' not in str(result)
    finally:
        engine.close()


@pytest.mark.parametrize('wire,known_bytes,diagnostic,blockers', [
    (b'HTTP/1.1 404 Not Found\r\nContent-Length: 10\r\n\r\nabc', 3, 'body_incomplete', []),
    (b'HTTP/1.1 404 Not Found\r\nTransfer-Encoding: chunked\r\n\r\n3\r\nabcXX0\r\n\r\n', 3, 'body_incomplete', []),
    (b'HTTP/1.1 404 Not Found\r\nTransfer-Encoding: chunked\r\n\r\n3\r\nabc\r\n0\r\n', 3, 'body_incomplete', []),
    (b'HTTP/1.1 404 Not Found\r\nContent-Length: 131073\r\n\r\n' + b'x' * 131073, None, 'body_oversized', ['budget_exhausted']),
], ids=['truncated-length', 'bad-chunk', 'missing-trailer-end', 'oversized'])
def test_context_raised_http_error_rejects_bad_body_and_charges_consumed_bytes(tmp_path, monkeypatch, wire, known_bytes, diagnostic, blockers):
    fixture = _context_engine(tmp_path, monkeypatch)
    engine = fixture.api.HostedContextObservation(fixture.descriptor)
    path = fixture.packet['read_descriptor']['run_routes'][0]
    errors = []
    class ErrorOpener:
        def open(self, request, timeout):
            fixture.calls.append(('GET', path))
            raise _raised_framed_context_error(request, 404, wire, captured=errors)
    try:
        with engine._db() as db:
            before_bytes = db.execute('SELECT response_bytes FROM connection').fetchone()[0]
        remaining = fixture.packet['limits']['max_response_bytes'] - before_bytes
        monkeypatch.setattr(fixture.api, 'build_opener', lambda *handlers: ErrorOpener())
        with pytest.raises(ValueError, match='Complete bounded'):
            engine._request(method='GET', path=path, payload=None, timeout=1)
        expected_bytes = min(131072, remaining) + 1 if known_bytes is None else known_bytes
        with engine._db() as db:
            assert db.execute('SELECT response_bytes FROM connection').fetchone()[0] == before_bytes + expected_bytes
        assert engine._context_read_failures[path.rsplit('/', 1)[1]]['mismatch_category'] == diagnostic
        assert engine.status()['blockers'] == blockers
        assert engine.status()['counts']['read_requests'] == 2
        assert engine.status()['counts']['mutation_requests'] == 0
        assert len(errors) == 1 and errors[0][0].isclosed() and errors[0][1].closed
        if blockers:
            with pytest.raises(PermissionError, match='stopped'):
                engine._request(method='GET', path=path, payload=None, timeout=1)
            assert len(errors) == 1
    finally:
        engine.close()


def test_context_raised_framed_mutation_error_is_attempted_once(tmp_path, monkeypatch):
    fixture = _context_engine(tmp_path, monkeypatch)
    engine = fixture.api.HostedContextObservation(fixture.descriptor)
    errors = []
    wire = b'HTTP/1.1 429 Too Many Requests\r\nContent-Length: 2\r\n\r\n{}'
    class ErrorOpener:
        def open(self, request, timeout):
            assert request.get_method() == 'POST'
            fixture.calls.append(('POST', '/api/v1/runs'))
            raise _raised_framed_context_error(request, 429, wire, captured=errors)
    try:
        monkeypatch.setattr(fixture.api, 'build_opener', lambda *handlers: ErrorOpener())
        state = engine.emit(fixture.operations[0])
        assert state['blockers'] == ['delivery_rejected']
        assert state['counts']['mutation_requests'] == 1
        assert state['counts']['rejected'] == 1
        assert fixture.calls.count(('POST', '/api/v1/runs')) == 1
        assert len(errors) == 1 and errors[0][0].isclosed() and errors[0][1].closed
        with pytest.raises(PermissionError, match='stopped'):
            engine.emit(fixture.operations[0])
        assert fixture.calls.count(('POST', '/api/v1/runs')) == 1
    finally:
        engine.close()


@pytest.mark.parametrize('failure', [TimeoutError, ConnectionResetError])
def test_context_raised_http_error_prefix_failure_charges_bytes_and_stops_effects(tmp_path, monkeypatch, failure):
    import json
    fixture = _context_engine(tmp_path, monkeypatch)
    engine = fixture.api.HostedContextObservation(fixture.descriptor)
    path = fixture.packet['read_descriptor']['run_routes'][0]
    errors = []
    wire = b'HTTP/1.1 404 Not Found\r\nContent-Length: 200000\r\n\r\n' + b'x' * 70000
    class ErrorOpener:
        def open(self, request, timeout):
            fixture.calls.append(('GET', path))
            raise _raised_framed_context_error(request, 404, wire, captured=errors, failure=failure)
    try:
        with engine._db() as db:
            before_bytes = db.execute('SELECT response_bytes FROM connection').fetchone()[0]
        monkeypatch.setattr(fixture.api, 'build_opener', lambda *handlers: ErrorOpener())
        with pytest.raises(OSError, match='unacknowledged'):
            engine._request(method='GET', path=path, payload=None, timeout=1)
        with engine._db() as db:
            assert db.execute('SELECT response_bytes FROM connection').fetchone()[0] == before_bytes + 70000
        assert engine.status()['blockers'] == ['body_read_uncertain']
        assert engine.status()['counts']['read_requests'] == 2
        assert engine.status()['counts']['mutation_requests'] == 0
        assert len(errors) == 1 and errors[0][0].isclosed() and errors[0][1].closed
        failures = list((tmp_path / 'context-evidence').glob('body-read-failure-*.json'))
        assert len(failures) == 1
        accounting = json.loads(failures[0].read_bytes())
        assert accounting['known_body_bytes'] == 70000
        assert accounting['total_body_bytes_known'] is accounting['additional_reads_authorized'] is False
        with pytest.raises(PermissionError, match='stopped'):
            engine._request(method='GET', path=path, payload=None, timeout=1)
        assert len(errors) == 1
    finally:
        engine.close()
