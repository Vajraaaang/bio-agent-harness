"""Offline /5 preparation preserves actual dispatch receipts without live effects."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest


def _observer(tmp_path):
    from devbench.analysis.rna_trace_receipts import LocalTraceObserver
    from devbench.analysis.rna_trace_contract import trace_digest
    observer = LocalTraceObserver(tmp_path / 'signedin-observer.sqlite',
        source_digest='a' * 64, runtime_digest='b' * 64, policy_digest='c' * 64)
    workflow_id = 'PRIVATE_SIGNEDIN_WORKFLOW_CANARY'
    for attempt in (1, 2):
        with observer._db() as db:
            context = observer._context(db, workflow_id, 'lead', 'generate', attempt,
                trace_digest([workflow_id, attempt]))
        observer._emit(context, trace_digest(workflow_id), 'reservation', 'reserved', dispatch='prepared')
        observer._emit(context, trace_digest(workflow_id), 'dispatch', 'running', dispatch='attempted')
        observer._emit(context, trace_digest(workflow_id), 'capture', 'completed', capture='verified')
        observer._emit(context, trace_digest(workflow_id), 'completion', 'completed', capture='verified')
    return observer, workflow_id


def _prepared(tmp_path):
    from devbench.study.rna_hosted_workflow import prepare_generation_context_observation
    observer, workflow_id = _observer(tmp_path)
    return observer, workflow_id, prepare_generation_context_observation(observer=observer, workflow_id=workflow_id)


def _packet(tmp_path):
    from devbench.analysis.rna_trace_contract import trace_digest
    from devbench.study import rna_hosted_context_contract as contract
    from devbench.study.rna_langsmith_live import EVENT_FIELDS
    observer, workflow_id, prepared = _prepared(tmp_path)
    now = datetime.now(timezone.utc)
    expiry = (now + timedelta(minutes=5)).isoformat()
    identity = dict(observer.identities,
        criteria_digest=contract.packet_digest(contract.GENERATION_CONTEXT_CRITERIA),
        fixture_manifest_digest=contract.packet_digest(contract.GENERATION_CONTEXT_FIXTURE))
    session = dict(format='devbench.rna-live-trace-session/5', workflow_alias=str(uuid4()),
        workflow_binding_digest=trace_digest(workflow_id), data_scope='synthetic', **observer.identities,
        endpoint=contract.TARGET['endpoint'], project=contract.TARGET['project_name'],
        project_id=contract.TARGET['project_id'], workspace_id=contract.TARGET['workspace_id'],
        redaction_version='rna-allowlist/1', event_schema='devbench.rna-trace-event/1',
        allowed_event_fields=sorted(EVENT_FIELDS), max_spans=prepared['profile']['context_count'],
        max_bytes=262144, max_delivery_attempts=1, expires_at=expiry, authorize_upload=False,
        span_topology=prepared['topology'], context_profile=prepared['profile'])
    packet = contract.prepare_generation_context_packet(installed_identity=identity, target=deepcopy(contract.TARGET),
        session=session, topology=prepared['topology'], profile=prepared['profile'], expires_at=expiry,
        evidence_path=str(tmp_path / 'signedin-evidence'), now=now)
    return observer, workflow_id, prepared, identity, packet, now


def test_v5_preserves_actual_dispatch_tree_counts_and_source_privacy(tmp_path):
    from devbench.study import rna_hosted_workflow as api
    from devbench.study.contracts import canonical_json_bytes
    observer, workflow_id = _observer(tmp_path)
    before = deepcopy(observer.read_events(workflow_id))
    with pytest.raises(ValueError):
        api.prepare_context_observation(observer=observer, workflow_id=workflow_id)
    prepared = api.prepare_generation_context_observation(observer=observer, workflow_id=workflow_id)
    profile = prepared['profile']
    assert profile['format'] == 'devbench.rna-context-observation-profile/5'
    assert profile['context_count'] == 3 and profile['source_event_count'] == 9
    assert profile['capture_event_count'] == 2 and profile['dispatch_event_count'] == 2
    assert profile['operation_count'] == 5 and len(profile['open_span_ids']) == 1
    assert [row['dispatch_event_count'] for row in profile['contexts']] == [0, 1, 1]
    assert profile['usage_policy'] == 'unavailable'
    raw = canonical_json_bytes(prepared).decode()
    assert 'PRIVATE_' not in raw and str(tmp_path) not in raw
    assert not any(event[key] in raw for event in before for key in ('event_id', 'span_id', 'trace_id'))
    assert observer.read_events(workflow_id) == before


@pytest.mark.parametrize('mutation', ['count', 'row_count', 'extra', 'v4'])
def test_v5_profile_rejects_count_drift_private_fields_and_v4_relabel(tmp_path, mutation):
    from devbench.study import rna_hosted_context_contract as contract
    _, _, prepared = _prepared(tmp_path)
    profile = deepcopy(prepared['profile'])
    if mutation == 'count': profile['dispatch_event_count'] = 1
    elif mutation == 'row_count': profile['contexts'][1]['dispatch_event_count'] = 0
    elif mutation == 'extra': profile['contexts'][1]['prompt'] = 'PRIVATE_PROMPT_CANARY'
    else: profile['format'] = 'devbench.rna-context-observation-profile/4'
    with pytest.raises(ValueError):
        contract.validate_generation_context_profile(profile, topology=prepared['topology'])
    with pytest.raises(ValueError):
        contract.validate_context_profile(prepared['profile'], topology=prepared['topology'])


def test_v5_packet_has_distinct_default_off_action_binding_and_no_approval_side_effect(tmp_path):
    from devbench.study import rna_hosted_context_contract as contract
    from devbench.study import rna_hosted_workflow as api
    _, _, prepared, identity, packet, now = _packet(tmp_path)
    assert packet['format'] == 'devbench.rna-hosted-context/5'
    assert packet['schema_mode'] == 'experimental_generation_context_observation'
    assert packet['limits']['max_source_events'] == 9 and packet['limits']['max_mutation_requests'] == 5
    assert packet['live_session']['format'] == 'devbench.rna-hosted-action-proposal/5'
    assert packet['target_read']['format'] == 'devbench.rna-hosted-action-proposal/5'
    assert packet['authorize_upload'] is packet['authorize_remote_read'] is False
    descriptor = dict(format='devbench.rna-hosted-exporter-session/5', packet=packet, approval_handles={})
    assert api.validate_generation_context_descriptor(descriptor) == descriptor
    admission = contract.admit_generation_context_packet(packet, installed_identity=identity,
        expected_binding=packet['binding'], expected_target=packet['target'], now=now, approval_handles={})
    assert admission['status'] == 'blocked' and admission['authorize_upload'] is False
    assert not (tmp_path / 'signedin-evidence').exists()
    with pytest.raises(ValueError):
        contract.validate_context_wire_packet(packet, now=now)


def test_v5_bridge_preserves_dispatch_digests_and_rejects_source_mutation(tmp_path):
    from devbench.study import rna_hosted_workflow as api
    _, workflow_id, _, _, packet, _ = _packet(tmp_path)
    from devbench.analysis.rna_trace_receipts import LocalTraceObserver
    observer = LocalTraceObserver.open_existing(tmp_path / 'signedin-observer.sqlite')
    before = deepcopy(observer.read_events(workflow_id))
    bridge = api.GenerationContextReceiptBridge(authorized_session_manifest={**packet['session'], 'authorize_upload': True},
        output_path=tmp_path / 'signedin-operations.jsonl')
    operations = bridge.emit_new(observer=observer, workflow_id=workflow_id)
    assert len(operations) == 5
    assert bridge.emit_new(observer=observer, workflow_id=workflow_id) == []
    for item in operations:
        api.validate_generation_context_wire_operation(item, packet=packet)
        metadata = item['payload']['extra']['metadata']
        assert metadata['dispatch_event_count'] in (0, 1)
        assert item['payload']['inputs'] == item['payload']['outputs'] == {}
        assert metadata['metrics']['total_tokens'] is None
        assert metadata['metrics']['provider_cost']['value'] is None
    assert observer.read_events(workflow_id) == before
    context = observer.read_contexts(workflow_id)[0]
    observer._emit(context, __import__('devbench.analysis.rna_trace_contract', fromlist=['trace_digest']).trace_digest(workflow_id),
        'capture', 'completed', capture='verified')
    with pytest.raises(ValueError):
        bridge.emit_new(observer=observer, workflow_id=workflow_id)


def test_v5_default_off_child_never_resolves_credentials_or_creates_evidence(tmp_path, monkeypatch):
    import os
    from devbench.study import rna_hosted_exporter as exporter
    from devbench.study import rna_hosted_workflow as api
    _, _, _, identity, packet, _ = _packet(tmp_path)
    calls = []
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(exporter, '_CHILD_PROCESS_PID', os.getpid())
    monkeypatch.setattr(exporter, '_resolve_keychain_credential', lambda: calls.append('credential'))
    monkeypatch.setattr(api, 'generation_context_identity', lambda: deepcopy(identity))
    descriptor = dict(format='devbench.rna-hosted-exporter-session/5', packet=packet, approval_handles={})
    hosted = api.HostedGenerationContextObservation(descriptor)
    status = hosted.status()
    assert status['status'] == 'blocked' and status['credential_resolutions'] == 0
    assert status['dispatch_event_count'] == 2
    assert calls == [] and not (tmp_path / 'signedin-evidence').exists()
    assert exporter._validate_descriptor(descriptor) == descriptor
    from devbench.study.contracts import canonical_json_bytes
    assert exporter._decode_response(canonical_json_bytes(status) + b'\n', command='status', context_v5=True) == status
    with pytest.raises(ValueError):
        exporter._decode_response(canonical_json_bytes(status) + b'\n', command='status', context_v4=True)


def test_v5_outbox_retains_closed_operations_and_refuses_generic_transport(tmp_path):
    from devbench.study import rna_hosted_workflow as api
    from devbench.study.contracts import canonical_json_bytes
    from devbench.study.langsmith_outbox import ExportOutbox, digest
    observer, workflow_id, _, _, packet, _ = _packet(tmp_path)
    session = {**packet['session'], 'authorize_upload': True}
    operations = api.GenerationContextReceiptBridge(authorized_session_manifest=session,
        output_path=tmp_path / 'signedin-outbox-operations.jsonl').emit_new(observer=observer, workflow_id=workflow_id)
    document = dict(record_format='devbench.rna-langsmith-export/1', privacy_policy='rna-allowlist/1',
        data_scope='synthetic', runs=[operations[0]['payload']])
    seed = tmp_path / 'signedin-seed' / 'payload.json'
    seed.parent.mkdir()
    seed.write_bytes(canonical_json_bytes(document))
    box = ExportOutbox.create_live(tmp_path / 'signedin-outbox' / 'journal.sqlite', payload_path=seed,
        expected_sha256=digest(seed.read_bytes()), authorized_session_manifest=session, initial_operations=operations[:1])
    for item in operations[1:]:
        box.enqueue_operation(item['operation'], item['payload'])
    with box._db() as db:
        assert db.execute('SELECT count(*) FROM spans').fetchone()[0] == 3
        assert db.execute('SELECT count(*) FROM operations').fetchone()[0] == 5
    calls = []
    def generic(**kwargs):
        calls.append(kwargs)
        return {'status_code': 202}
    with pytest.raises(PermissionError):
        box.deliver(transport=generic, allow_upload=True)
    assert calls == []
    ExportOutbox.open_readonly(box.path).status()


def test_v5_joined_readback_requires_v5_schema_and_preserves_dispatch_count(tmp_path):
    from devbench.study import rna_hosted_workflow as api
    from devbench.study import rna_langsmith_verify as verify
    from devbench.study.rna_langsmith_binding import ProjectAttestationAuthority
    from devbench.study.contracts import canonical_json_bytes
    from devbench.study.langsmith_outbox import digest
    observer, workflow_id, _, _, packet, now = _packet(tmp_path)
    session = {**packet['session'], 'authorize_upload': True}
    operations = api.GenerationContextReceiptBridge(authorized_session_manifest=session,
        output_path=tmp_path / 'signedin-readback-operations.jsonl').emit_new(observer=observer, workflow_id=workflow_id)
    starts = {item['payload']['id']: item['payload'] for item in operations if item['operation']['operation'] == 'start'}
    expected = operations[-1]['payload']
    context = dict(format='devbench.rna-project-binding-context/2', packet_digest='d' * 64,
        session_digest=digest(canonical_json_bytes(session)), action_digest='e' * 64,
        source_digest=session['source_digest'], runtime_digest=session['runtime_digest'],
        endpoint=session['endpoint'], region='us', workspace_id=session['workspace_id'],
        project_id=session['project_id'], project_name=session['project'], expires_at=session['expires_at'], max_age_seconds=30)
    authority = ProjectAttestationAuthority(expected_context=context)
    receipt = authority.issue_from_response(response={'status_code': 200, 'body': dict(id=session['project_id'],
        tenant_id=session['workspace_id'], name=session['project'])}, request_nonce=str(uuid4()), observed_at=now.isoformat())
    remote = {**deepcopy(expected), 'status': 'success', 'app_path': '/synthetic', 'error': None}
    arguments = dict(method='GET', expected_payload=expected, project_receipt=receipt, expected_context=context,
        expected_topology=starts, expected_session=session, now=now)
    result, vector = verify.decode_joined_context_run_response(canonical_json_bytes(remote),
        schema_format=verify.JOINED_GENERATION_CONTEXT_RESPONSE_SCHEMA_FORMAT, **arguments)
    assert result == 'exact_revision_match' and vector['extra']['metadata']['dispatch_event_count'] == 1
    with pytest.raises(ValueError):
        verify.decode_joined_context_run_response(canonical_json_bytes(remote),
            schema_format=verify.JOINED_CONTEXT_RESPONSE_SCHEMA_FORMAT, **arguments)
    remote['extra']['metadata']['dispatch_event_count'] = 0
    assert verify.decode_joined_context_run_response(canonical_json_bytes(remote),
        schema_format=verify.JOINED_GENERATION_CONTEXT_RESPONSE_SCHEMA_FORMAT, **arguments)[0] == 'mismatch'
