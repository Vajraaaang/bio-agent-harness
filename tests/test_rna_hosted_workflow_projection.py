"""Synthetic /3 nested workflow projection; no hosted calls or scientific claim."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from uuid import uuid4
import time

import pytest

from devbench.study import rna_langsmith_live as live
from devbench.study import rna_langsmith_verify as verify
from devbench.study.contracts import canonical_json_bytes
from devbench.study.langsmith_outbox import digest
from tests.test_rna_trace_contract import event
from devbench.study.rna_langsmith_binding import ProjectAttestationAuthority


SCHEMA = 'devbench.rna-joined-workflow-run-response/3'
CAPABILITY = dict(backend_kind='unknown', qualification_status='unknown', admitted=False)


def workflow_scope():
    identities = [str(uuid4()) for _ in range(8)]
    slots = [('coordinator', 'workflow'), ('investigator', 'audit'),
             ('investigator', 'question'), ('investigator', 'design'),
             ('numerical', 'run'), ('review', 'verify'),
             ('coordinator', 'recommend'), ('coordinator', 'synthesis')]
    topology = [dict(span_id=identity, parent_span_id=None if not index else identities[3] if index == 4 else identities[0],
                     role=role, phase=phase, attempt=1)
                for index, (identity, (role, phase)) in enumerate(zip(identities, slots))]
    now = datetime.now(timezone.utc)
    session = dict(format='devbench.rna-live-trace-session/3', workflow_alias=str(uuid4()),
        workflow_binding_digest='d' * 64, data_scope='synthetic',
        source_digest='a' * 64, runtime_digest='b' * 64, policy_digest='c' * 64,
        endpoint='https://api.smith.langchain.com', project='synthetic-workflow',
        project_id=str(uuid4()), workspace_id=str(uuid4()),
        redaction_version='rna-allowlist/1', event_schema='devbench.rna-trace-event/1',
        allowed_event_fields=sorted(live.EVENT_FIELDS), max_spans=8, max_bytes=131072,
        max_delivery_attempts=1, expires_at=(now + timedelta(minutes=5)).isoformat(),
        authorize_upload=True, span_topology=topology)
    receipts = []
    for terminal in (False, True):
        for index, slot in enumerate(topology if not terminal else reversed(topology)):
            stamp = now + timedelta(microseconds=index + (100 if terminal else 0))
            receipts.append(event(event_id=str(uuid4()), trace_id=identities[0],
                **slot, sequence=int(terminal),
                kind='completion' if terminal else 'reservation',
                source_status='completed' if terminal else 'active',
                dispatch_status='not_dispatched', capture_status='unavailable',
                clock=dict(utc=stamp.isoformat().replace('+00:00', 'Z'), monotonic_ns=index + (100 if terminal else 0),
                           process_alias=str(uuid4()), timing_basis='observed'),
                bindings=dict(workflow=session['workflow_binding_digest'],
                    source=session['source_digest'], runtime=session['runtime_digest'], policy=session['policy_digest'])))
    return session, receipts, now


class Observer:
    def __init__(self, events):
        self.events = events

    def read_events(self, workflow_id):
        return deepcopy(self.events)


def projected(tmp_path):
    session, receipts, now = workflow_scope()
    bridge = live.ReceiptOperationBridge(authorized_session_manifest=session,
        output_path=tmp_path / 'operations.jsonl', capability=CAPABILITY,
        allocation=0, actual_children=0)
    operations = bridge.emit_new(observer=Observer(receipts), workflow_id='local-workflow')
    starts = {item['payload']['id']: item['payload'] for item in operations if item['operation']['operation'] == 'start'}
    return session, receipts, now, operations, starts


def decoder():
    value = getattr(verify, 'decode_joined_workflow_run_response', None)
    assert callable(value), 'Closed nested workflow /3 decoder is missing'
    assert verify.JOINED_WORKFLOW_RESPONSE_SCHEMA_FORMAT == SCHEMA
    return value


def decoder_scope(session, now):
    context = dict(format='devbench.rna-project-binding-context/2',
        session_digest=digest(canonical_json_bytes(session)), packet_digest='e' * 64,
        action_digest='f' * 64, source_digest=session['source_digest'],
        runtime_digest=session['runtime_digest'], endpoint=session['endpoint'], region='us',
        workspace_id=session['workspace_id'], project_id=session['project_id'],
        project_name=session['project'], expires_at=session['expires_at'], max_age_seconds=60)
    project = dict(id=session['project_id'], tenant_id=session['workspace_id'], name=session['project'])
    authority = ProjectAttestationAuthority(expected_context=context)
    receipt = authority.issue_from_response(response={'status_code': 200, 'body': project},
        request_nonce=str(uuid4()), observed_at=now.isoformat())
    return context, receipt


def remote(payload):
    return {**deepcopy(payload), 'status': 'success' if 'end_time' in payload else 'pending',
            'app_path': '/synthetic-run'}


def test_v3_closed_session_accepts_eight_opaque_parent_preceding_slots():
    session, _, _ = workflow_scope()
    assert live.validate_live_session(session) == session
    assert live.SESSION_FORMAT_V3 == session['format']


@pytest.mark.parametrize('mutation', ['extra', 'duplicate', 'future_parent', 'second_root', 'role', 'attempt', 'count', 'span_cap'])
def test_v3_session_rejects_unclosed_ambiguous_or_unbounded_topology(mutation):
    session, _, _ = workflow_scope()
    rows = session['span_topology']
    if mutation == 'extra': rows[1]['prompt'] = 'CANARY'
    elif mutation == 'duplicate': rows[1].update(role=rows[2]['role'], phase=rows[2]['phase'])
    elif mutation == 'future_parent': rows[1]['parent_span_id'] = rows[-1]['span_id']
    elif mutation == 'second_root': rows[1]['parent_span_id'] = None
    elif mutation == 'role': rows[1]['role'] = 'CANARY'
    elif mutation == 'attempt': rows[1]['attempt'] = True
    elif mutation == 'count': rows.pop()
    else: session['max_spans'] = 9
    with pytest.raises(ValueError) as caught:
        live.validate_live_session(session)
    assert 'CANARY' not in str(caught.value)


def test_v3_bridge_projects_actual_receipts_to_immutable_nested_orders(tmp_path):
    session, receipts, _, operations, starts = projected(tmp_path)
    assert len(operations) == 16 and len(starts) == 8
    root = session['span_topology'][0]['span_id']
    for item, retained in zip(operations, receipts):
        payload = item['payload']
        assert payload['trace_id'] == root
        assert payload['id'] == retained['span_id']
        assert payload.get('parent_run_id') == retained['parent_span_id']
        assert payload['start_time'] == starts[payload['id']]['start_time']
        suffix = live._root_dotted_order(payload['start_time'], payload['id'])
        expected = suffix if payload['id'] == root else starts[payload['parent_run_id']]['dotted_order'] + '.' + suffix
        assert payload['dotted_order'] == expected
        assert item['operation']['operation'] in {'start', 'finish'}
        assert payload['extra']['metadata']['actual_children'] == 0
        assert payload['inputs'] == payload['outputs'] == {}
        assert verify.validate_remote_proof(payload)
        if 'end_time' in payload: assert payload['end_time'] == retained['clock']['utc']
    assert 'parent_run_id' not in starts[root]
    assert live.validate_live_run(operations[-1]['payload'], 'finish', session_format=live.SESSION_FORMAT_V3) == operations[-1]['payload']
    with pytest.raises(ValueError):
        live.validate_live_run(operations[4]['payload'], 'start')
    before = (tmp_path / 'operations.jsonl').read_bytes()
    reopened = live.ReceiptOperationBridge(authorized_session_manifest=session,
        output_path=tmp_path / 'operations.jsonl', capability=CAPABILITY,
        allocation=0, actual_children=0)
    assert reopened.emit_new(observer=Observer(receipts), workflow_id='local-workflow') == []
    assert (tmp_path / 'operations.jsonl').read_bytes() == before


@pytest.mark.parametrize('mutation', ['missing_start', 'annotation', 'unavailable', 'skewed', 'wrong_trace', 'wrong_slot'])
def test_v3_bridge_refuses_gaps_annotations_invented_timing_or_identity(tmp_path, mutation):
    session, receipts, _ = workflow_scope()
    if mutation == 'missing_start': receipts = [receipts[0], receipts[9]]
    elif mutation == 'annotation': receipts[8]['source_status'] = 'active'
    elif mutation == 'unavailable': receipts[0]['clock'].update(utc=None, timing_basis='unavailable')
    elif mutation == 'skewed': receipts[8]['clock']['utc'] = '2000-01-01T00:00:00Z'
    elif mutation == 'wrong_trace': receipts[0]['trace_id'] = str(uuid4())
    else: receipts[1]['phase'] = 'synthesis'
    bridge = live.ReceiptOperationBridge(authorized_session_manifest=session,
        output_path=tmp_path / 'operations.jsonl', capability=CAPABILITY, allocation=0, actual_children=0)
    with pytest.raises(ValueError):
        bridge.emit_new(observer=Observer(receipts), workflow_id='local-workflow')


def test_v3_decoder_matches_root_and_nested_child_using_independent_starts(tmp_path):
    session, _, now, operations, starts = projected(tmp_path)
    context, receipt = decoder_scope(session, now)
    for item in operations:
        result, vector = decoder()(remote(item['payload']), schema_format=SCHEMA, method='GET',
            expected_payload=item['payload'], project_receipt=receipt,
            expected_context=context, expected_topology=starts, now=now)
        assert result == 'exact_revision_match'
        assert vector['extra']['metadata']['remote_proof'] == item['payload']['extra']['metadata']['remote_proof']


@pytest.mark.parametrize('mutation', ['id', 'trace_id', 'parent_run_id', 'dotted_order', 'end_time', 'status', 'proof'])
def test_v3_decoder_refuses_observed_identity_order_terminal_or_proof_drift(tmp_path, mutation):
    session, _, now, operations, starts = projected(tmp_path)
    context, receipt = decoder_scope(session, now)
    item = operations[11]  # finish for the nested numerical run
    observed = remote(item['payload'])
    if mutation in {'id', 'trace_id', 'parent_run_id'}: observed[mutation] = str(uuid4())
    elif mutation == 'dotted_order': observed[mutation] = starts[session['span_topology'][0]['span_id']]['dotted_order']
    elif mutation == 'end_time': observed[mutation] = None
    elif mutation == 'status': observed[mutation] = 'pending'
    else: observed['extra']['metadata']['remote_proof']['revision'] += 1
    assert decoder()(observed, schema_format=SCHEMA, method='GET',
        expected_payload=item['payload'], project_receipt=receipt,
        expected_context=context, expected_topology=starts, now=now) == ('mismatch', None)


@pytest.mark.parametrize('mutation', ['missing_parent', 'parent_order', 'foreign_trace', 'serialized_receipt', 'stale_receipt'])
def test_v3_decoder_requires_frozen_full_topology_and_typed_fresh_project_read(tmp_path, mutation):
    session, _, now, operations, starts = projected(tmp_path)
    context, receipt = decoder_scope(session, now)
    item = operations[4]
    if mutation == 'missing_parent': starts.pop(item['payload']['parent_run_id'])
    elif mutation == 'parent_order': starts[item['payload']['parent_run_id']]['dotted_order'] += '.CANARY'
    elif mutation == 'foreign_trace': starts[item['payload']['parent_run_id']]['trace_id'] = str(uuid4())
    elif mutation == 'serialized_receipt': receipt = receipt.to_record()
    else: now += timedelta(seconds=61)
    with pytest.raises(ValueError):
        decoder()(remote(item['payload']), schema_format=SCHEMA, method='GET',
            expected_payload=item['payload'], project_receipt=receipt,
            expected_context=context, expected_topology=starts, now=now)


def test_v3_decoder_query_is_closed_and_v2_stays_root_only(tmp_path):
    session, _, now, operations, starts = projected(tmp_path)
    context, receipt = decoder_scope(session, now)
    item = operations[4]
    kwargs = dict(schema_format=SCHEMA, method='POST', expected_payload=item['payload'],
                  project_receipt=receipt, expected_context=context, expected_topology=starts, now=now)
    assert decoder()({'runs': [remote(item['payload'])], 'cursors': {}}, **kwargs)[0] == 'exact_revision_match'
    with pytest.raises(ValueError):
        decoder()({'runs': [remote(item['payload']), remote(item['payload'])], 'cursors': {}}, **kwargs)
    with pytest.raises(ValueError):
        verify.decode_joined_run_response(remote(item['payload']),
            schema_format=verify.JOINED_RUN_RESPONSE_SCHEMA_FORMAT, method='GET',
            expected_payload=item['payload'], project_receipt=receipt, expected_context=context, now=now)


def verifier_inputs(tmp_path, session):
    limits = dict(max_spans=8, max_bytes=131072, max_mutation_requests=16,
                  max_read_requests=1, max_delivery_attempts=1, total_deadline_seconds=60)
    criteria = dict(format='devbench.rna-hosted-criteria/1',
        sample_matrix=[dict(fixture_id='synthetic-0', stage='pre_fit_methods', allocation=0, repeat=1)],
        api_p95_upper_seconds=5, visibility_deadline_seconds=30, poll_interval_seconds=.001,
        dashboard_window_seconds=30, max_extra_model_calls=0, max_extra_fits=0,
        max_extra_retrievals=0, max_source_writes=0, max_privacy_leaks=0,
        required_topology_fraction=1, censored_samples_fail=True, limits=limits)
    manifest = dict(format='devbench.rna-hosted-qualification/1', qualification_id=str(uuid4()),
        mode='fake', data_scope='synthetic', source_digest=session['source_digest'],
        runtime_digest=session['runtime_digest'], policy_digest=session['policy_digest'],
        criteria_digest=digest(canonical_json_bytes(criteria)), fixture_manifest_digest='e' * 64,
        output_path=str(tmp_path / 'evidence'), endpoint=session['endpoint'],
        workspace_id=session['workspace_id'], project_id=session['project_id'],
        project_name=session['project'], live_session_digest=digest(canonical_json_bytes(session)),
        expires_at=session['expires_at'], allowed_operations=['read_run'],
        authorize_upload=False, authorize_remote_read=False,
        authorization_record_digest=None, limits=limits)
    return manifest, criteria


def test_v3_workflow_verifier_subclass_observes_exact_nested_revision_and_consumes_budget(tmp_path):
    cls = getattr(verify, 'WorkflowRunVerifier', None)
    assert isinstance(cls, type), 'Versioned nested workflow verifier is missing'
    assert issubclass(cls, verify.JoinedRunVerifier)
    session, _, now, operations, starts = projected(tmp_path)
    context, receipt = decoder_scope(session, now)
    manifest, criteria = verifier_inputs(tmp_path, session)
    item = operations[11]
    calls = []
    def read(**kwargs):
        calls.append(kwargs)
        return dict(status_code=200, body=remote(item['payload']))
    verifier = cls(manifest=manifest, criteria=criteria, api_key=None, read_transport=read,
        project_receipt=receipt, expected_context=context, expected_topology=starts)
    verifier.bind_session(session)
    result = verifier.observe(operation=item['operation'], expected_payload=item['payload'],
                              persisted_monotonic_ns=time.monotonic_ns())
    assert result['verification_result'] == 'exact_revision_match'
    assert calls[0]['path'] == '/runs/' + item['payload']['id']
    assert verifier.observe(operation=item['operation'], expected_payload=item['payload'],
                            persisted_monotonic_ns=time.monotonic_ns())['response_class'] == 'budget_exhausted'
    assert len(calls) == 1


def test_v2_joined_verifier_refuses_v3_session(tmp_path):
    session, _, now = workflow_scope()
    context, receipt = decoder_scope(session, now)
    manifest, criteria = verifier_inputs(tmp_path, session)
    verifier = verify.JoinedRunVerifier(manifest=manifest, criteria=criteria, api_key=None,
        read_transport=lambda **kwargs: pytest.fail('Unbound /2 verifier must not read'),
        project_receipt=receipt, expected_context=context)
    with pytest.raises(ValueError):
        verifier.bind_session(session)


# /4 context-observation additions. All earlier /3 test bytes remain intact.
def context_source_fixture(*, fail_lead=False):
    session, _, now = workflow_scope()
    remote_ids = [str(uuid4()) for _ in range(9)]
    aliases = [str(uuid4()) for _ in range(9)]
    raw_ids = [str(uuid4()) for _ in range(9)]
    parents = [None, 0, 0, 1, 2, 0, 0, 0, 0]
    slots = [('coordinator', 'workflow', 1), ('lead', 'generate', 2),
             ('lead', 'generate', 3), ('investigator', 'selection', 1),
             ('investigator', 'selection', 1), ('review', 'review', 1),
             ('numerical', 'run', 1), ('coordinator', 'synthesis', 1),
             ('coordinator', 'invocation', 1)]
    topology = [dict(span_id=remote_ids[index], parent_span_id=None if parent is None else remote_ids[parent],
        role=slot[0], phase=slot[1], attempt=slot[2]) for index, (parent, slot) in enumerate(zip(parents, slots))]
    starts = ['reservation', 'reservation', 'reservation', 'capture', 'capture',
              'reservation', 'reservation', 'reservation', 'capture']
    schedule = [(index, 0, kind) for index, kind in enumerate(starts)]
    schedule += [(index, 1, 'capture') for index in (1, 2, 6)]
    schedule += [(index, 2 if index in (1, 2, 6) else 1,
                  'failure' if fail_lead and index == 2 else 'completion') for index in range(8, 0, -1)]
    raw_events, projected = [], []
    for ordinal, (index, sequence, kind) in enumerate(schedule):
        parent = parents[index]
        stamp = (now + timedelta(microseconds=ordinal)).isoformat().replace('+00:00', 'Z')
        status = 'active' if kind == 'reservation' else 'failed' if kind == 'failure' else 'completed'
        reason = 'failed' if kind == 'failure' else 'none'
        clock = dict(utc=stamp, monotonic_ns=ordinal, process_alias=str(uuid4()), timing_basis='observed')
        raw = event(event_id=str(uuid4()), trace_id=raw_ids[0], span_id=raw_ids[index],
            parent_span_id=None if parent is None else raw_ids[parent], role=slots[index][0],
            phase=slots[index][1], attempt=slots[index][2], sequence=sequence,
            kind=kind, source_status=status, dispatch_status='prepared' if kind == 'reservation' else 'unknown',
            capture_status='verified' if kind == 'capture' else 'unavailable', reason_code=reason,
            clock=clock, bindings=dict(workflow=session['workflow_binding_digest'], source=session['source_digest'],
                runtime=session['runtime_digest'], policy=session['policy_digest'], invocation='e' * 64))
        raw['metrics'].update(input_tokens=210, output_tokens=0, total_tokens=210, usage_basis='verified_complete')
        raw_events.append(raw)
        projected.append(dict(format='devbench.rna-context-source-event/4', context_alias=aliases[index],
            parent_context_alias=None if parent is None else aliases[parent],
            role=raw['role'], phase=raw['phase'], attempt=raw['attempt'], source_sequence=sequence,
            kind=kind, source_status=status, dispatch_status=raw['dispatch_status'],
            capture_status=raw['capture_status'], reason_code=reason, clock=dict(utc=stamp, timing_basis='observed')))
    contexts = []
    for index, row in enumerate(topology):
        retained = [item for item in projected if item['context_alias'] == aliases[index]]
        contexts.append(dict(span_id=row['span_id'], context_alias=aliases[index],
            parent_context_alias=None if parents[index] is None else aliases[parents[index]],
            start_observed=starts[index] == 'reservation',
            source_event_digests=[digest(canonical_json_bytes(item)) for item in retained],
            source_event_count=len(retained), capture_event_count=sum(item['kind'] == 'capture' for item in retained),
            terminal_status=None if index == 0 else 'failed' if fail_lead and index == 2 else 'completed'))
    profile = dict(format='devbench.rna-context-observation-profile/4', data_scope='synthetic',
        provenance='synthetic_engineering', usage_policy='unavailable',
        digest_policy='session_aliased_sanitized_receipts', context_count=9, source_event_count=20,
        capture_event_count=6, dispatch_event_count=0, operation_count=17,
        terminal_span_ids=remote_ids[1:], open_span_ids=[remote_ids[0]], contexts=contexts)
    session.update(format='devbench.rna-live-trace-session/4', span_topology=topology,
        context_profile=profile, max_spans=9, max_bytes=262144)
    return dict(session=session, topology=topology, profile=profile, raw_events=raw_events,
                projected_events=projected, raw_ids=raw_ids, aliases=aliases, now=now)


def context_projected(tmp_path, *, fail_lead=False):
    api = getattr(live, 'project_context_receipt', None)
    assert callable(api), 'Explicit /4 context source projection is missing'
    cls = getattr(live, 'ContextReceiptOperationBridge', None)
    assert isinstance(cls, type), 'Explicit /4 context receipt bridge is missing'
    scope = context_source_fixture(fail_lead=fail_lead)
    actual = [api(raw, context_alias=expected['context_alias'], parent_context_alias=expected['parent_context_alias'])
              for raw, expected in zip(scope['raw_events'], scope['projected_events'])]
    assert actual == scope['projected_events']
    bridge = cls(authorized_session_manifest=scope['session'], output_path=tmp_path / 'context-operations.jsonl')
    operations = bridge.emit_new(observer=Observer(actual), workflow_id='synthetic-context-fixture')
    scope['operations'] = operations
    scope['starts'] = {item['payload']['id']: item['payload'] for item in operations if item['operation']['operation'] == 'start'}
    return scope


def test_v4_context_bridge_preserves_nine_contexts_twenty_source_events_and_open_root(tmp_path):
    scope = context_projected(tmp_path)
    assert live.SESSION_FORMAT_V4 == scope['session']['format']
    assert live.validate_live_session(scope['session']) == scope['session']
    assert len(scope['operations']) == 17 and len(scope['starts']) == 9
    assert scope['profile']['source_event_count'] == 20 and scope['profile']['capture_event_count'] == 6
    root = scope['topology'][0]['span_id']
    assert 'end_time' not in scope['starts'][root]
    assert all(item['operation']['operation'] != 'finish' for item in scope['operations'] if item['payload']['id'] == root)
    terminal = next(item for item in scope['operations'] if item['payload']['id'] == scope['topology'][1]['span_id']
                    and item['operation']['operation'] == 'finish')
    assert terminal['payload']['extra']['metadata']['source_sequence'] == 2
    assert terminal['operation']['revision'] == 1
    for item in scope['operations']:
        payload = item['payload']; row = payload['extra']['metadata']
        assert row['metrics']['input_tokens'] is None and row['metrics']['total_tokens'] is None
        assert row['metrics']['provider_cost']['value'] is None
        assert row['metrics']['usage_basis'] == 'unavailable' and row['actual_children'] == 0
        assert payload['inputs'] == payload['outputs'] == {} and 'status' not in payload
        assert item['operation']['payload_sha256'] == digest(canonical_json_bytes(payload))
    raw = canonical_json_bytes(scope['operations']).decode()
    assert not any(identity in raw for identity in scope['raw_ids'])
    assert 'invocation_binding_digest' not in raw


def test_v4_capture_first_contexts_preserve_nested_parents_without_false_starts(tmp_path):
    scope = context_projected(tmp_path)
    children = [scope['starts'][scope['topology'][index]['span_id']] for index in (3, 4)]
    assert children[0]['parent_run_id'] != children[1]['parent_run_id']
    assert all(child['extra']['metadata']['attempt'] == 1 for child in children)
    assert all(child['extra']['metadata']['start_observed'] is False for child in children)
    assert all(child['extra']['metadata']['kind'] == 'capture' for child in children)
    assert all(child['extra']['metadata']['transport_timing_basis'] == 'first_observed_receipt' for child in children)
    assert all('end_time' not in child for child in children)
    assert all(child['extra']['metadata']['metrics']['elapsed_seconds'] is None for child in children)


def test_v4_context_decoder_matches_open_pending_and_explicit_terminal_only(tmp_path):
    scope = context_projected(tmp_path)
    decoder = getattr(verify, 'decode_joined_context_run_response', None)
    assert callable(decoder), 'Explicit /4 context decoder is missing'
    context, receipt = decoder_scope(scope['session'], scope['now'])
    latest = {item['payload']['id']: item for item in scope['operations']}
    for item in latest.values():
        result, _ = decoder(remote(item['payload']), schema_format='devbench.rna-joined-context-run-response/4',
            method='GET', expected_payload=item['payload'], project_receipt=receipt, expected_context=context,
            expected_topology=scope['starts'], expected_session=scope['session'], now=scope['now'])
        assert result == 'exact_revision_match'
    root = scope['topology'][0]['span_id']
    assert remote(latest[root]['payload'])['status'] == 'pending'


def test_v3_boundary_still_rejects_context_observation_topology_and_source_attempts():
    scope = context_source_fixture()
    old = deepcopy(scope['session']); old['format'] = 'devbench.rna-live-trace-session/3'
    with pytest.raises(ValueError): live.validate_live_session(old)
    old.pop('context_profile')
    with pytest.raises(ValueError): live.validate_live_session(old)
    old['span_topology'] = old['span_topology'][:8]; old['max_spans'] = 8
    with pytest.raises(ValueError): live.validate_live_session(old)


def test_v4_outbox_queues_exact_contexts_and_rejects_undeclared_ids_and_generic_delivery(tmp_path):
    from devbench.study.langsmith_outbox import ExportOutbox
    scope = context_projected(tmp_path)
    first = scope['operations'][0]
    seed = tmp_path / 'context-seed' / 'payload.json'; seed.parent.mkdir()
    raw = canonical_json_bytes(dict(record_format='devbench.rna-langsmith-export/1',
        privacy_policy='rna-allowlist/1', data_scope='synthetic', runs=[first['payload']]))
    seed.write_bytes(raw)
    box = ExportOutbox.create_live(tmp_path / 'context-journal' / 'outbox.sqlite', payload_path=seed,
        expected_sha256=digest(raw), authorized_session_manifest=scope['session'], initial_operations=[first])
    wrong = deepcopy(scope['operations'][1]); identity = str(uuid4())
    wrong['operation'].update(span_id=identity, operation_id=str(uuid4()))
    wrong['payload']['id'] = identity
    root = first['payload']['id']
    wrong['payload']['dotted_order'] = first['payload']['dotted_order'] + '.' + live._root_dotted_order(wrong['payload']['start_time'], identity)
    wrong['payload'] = verify.bind_remote_proof(wrong['payload'], wrong['operation'])
    wrong['operation']['payload_sha256'] = digest(canonical_json_bytes(wrong['payload']))
    before = box.path.read_bytes()
    with pytest.raises(ValueError): box.enqueue_operation(wrong['operation'], wrong['payload'])
    assert box.path.read_bytes() == before
    for item in scope['operations'][1:]:
        assert box.enqueue_operation(item['operation'], item['payload']) == {'status': 'prepared'}
    with box._db() as db:
        assert db.execute('SELECT count(*) FROM spans').fetchone()[0] == 9
        assert db.execute('SELECT count(*) FROM operations').fetchone()[0] == 17
    calls = []
    def generic(**kwargs): calls.append(kwargs); return {'status_code': 202}
    before = box.path.read_bytes()
    with pytest.raises(PermissionError): box.deliver(transport=generic, allow_upload=True)
    assert calls == [] and box.path.read_bytes() == before


@pytest.mark.parametrize('failure_point', ['open', 'partial_write', 'fsync'])
def test_v4_context_bridge_commits_memory_only_after_durable_append(tmp_path, monkeypatch, failure_point):
    import json
    scope = context_source_fixture()
    source = [live.project_context_receipt(raw, context_alias=expected['context_alias'],
               parent_context_alias=expected['parent_context_alias'])
              for raw, expected in zip(scope['raw_events'], scope['projected_events'])]
    path = tmp_path / 'failure-operations.jsonl'
    bridge = live.ContextReceiptOperationBridge(authorized_session_manifest=scope['session'], output_path=path)
    observer = Observer(source)
    original_open, original_fdopen = live.os.open, live.os.fdopen
    attempts = []
    with monkeypatch.context() as injected:
        if failure_point == 'open':
            def fail_append_open(name, flags, *args, **kwargs):
                if flags & live.os.O_APPEND:
                    attempts.append('open')
                    raise OSError('synthetic append open failure')
                return original_open(name, flags, *args, **kwargs)
            injected.setattr(live.os, 'open', fail_append_open)
        elif failure_point == 'partial_write':
            class PartialWriter:
                def __init__(self, stream): self.stream = stream
                def __enter__(self): return self
                def __exit__(self, *args): return self.stream.__exit__(*args)
                def write(self, raw):
                    attempts.append('partial_write')
                    self.stream.write(raw[:len(raw)//2]); self.stream.flush()
                    raise OSError('synthetic partial append failure')
                def flush(self): return self.stream.flush()
                def fileno(self): return self.stream.fileno()
            def fail_partial_write(*args, **kwargs):
                return PartialWriter(original_fdopen(*args, **kwargs))
            injected.setattr(live.os, 'fdopen', fail_partial_write)
        else:
            def fail_fsync(descriptor):
                attempts.append('fsync')
                raise OSError('synthetic fsync failure')
            injected.setattr(live.os, 'fsync', fail_fsync)
        with pytest.raises(OSError):
            bridge.emit_new(observer=observer, workflow_id='synthetic-durability-fixture')
    assert attempts == [failure_point]
    assert bridge.items == {} and bridge.start_payloads == {}
    retained = path.read_bytes()
    if failure_point == 'open':
        assert retained == b''
        emitted = bridge.emit_new(observer=observer, workflow_id='synthetic-durability-fixture')
        assert len(emitted) == 17
        records = [json.loads(line) for line in path.read_bytes().splitlines()]
        assert len(records) == 17
        parents = set()
        for item in records:
            run = item['payload']
            if item['operation']['operation'] == 'start':
                assert run.get('parent_run_id') is None or run['parent_run_id'] in parents
                parents.add(run['id'])
        reopened = live.ContextReceiptOperationBridge(authorized_session_manifest=scope['session'], output_path=path)
        assert reopened.emit_new(observer=observer, workflow_id='synthetic-durability-fixture') == []
    else:
        assert retained  # Preserve ambiguous bytes as failure evidence.
        assert retained.endswith(b'\n') is (failure_point == 'fsync')
        with pytest.raises(ValueError, match='uncertain|recovery'):
            bridge.emit_new(observer=observer, workflow_id='synthetic-durability-fixture')
        assert path.read_bytes() == retained
        assert bridge.items == {} and bridge.start_payloads == {}


@pytest.mark.parametrize('remote_state', ['acknowledged', 'absent_verified'])
def test_v4_uncertain_outbox_rejects_legacy_reconciliation_flags_without_mutation(tmp_path, remote_state):
    from devbench.study.langsmith_outbox import ExportOutbox
    scope = context_projected(tmp_path)
    first = scope['operations'][0]
    seed = tmp_path / 'reconcile-seed' / 'payload.json'; seed.parent.mkdir()
    raw = canonical_json_bytes(dict(record_format='devbench.rna-langsmith-export/1',
        privacy_policy='rna-allowlist/1', data_scope='synthetic', runs=[first['payload']]))
    seed.write_bytes(raw)
    box = ExportOutbox.create_live(tmp_path / 'reconcile-journal' / 'outbox.sqlite', payload_path=seed,
        expected_sha256=digest(raw), authorized_session_manifest=scope['session'], initial_operations=[first])
    # A local fixture models an ambiguous attempt; no transport is invoked.
    with box._db() as db:
        db.execute("UPDATE operations SET state='uncertain',attempt_count=1")
        db.execute("UPDATE spans SET state='uncertain',attempt_count=1")
    before = box.path.read_bytes()
    with pytest.raises(PermissionError):
        box.reconcile(first['payload']['id'], remote_state=remote_state, reviewed_receipt_digest='f' * 64)
    assert box.path.read_bytes() == before
    with box._db() as db:
        operation = db.execute('SELECT state,attempt_count FROM operations').fetchone()
        span = db.execute('SELECT state,last_receipt_digest,response_class FROM spans').fetchone()
    assert tuple(operation) == ('uncertain', 1)
    assert tuple(span) == ('uncertain', None, None)


def test_v4_reviewed_reconciliation_rejects_before_invoking_verifier_callback(tmp_path):
    from devbench.study.langsmith_outbox import ExportOutbox
    scope = context_projected(tmp_path)
    first = scope['operations'][0]
    seed = tmp_path / 'reviewed-seed' / 'payload.json'; seed.parent.mkdir()
    raw = canonical_json_bytes(dict(record_format='devbench.rna-langsmith-export/1',
        privacy_policy='rna-allowlist/1', data_scope='synthetic', runs=[first['payload']]))
    seed.write_bytes(raw)
    box = ExportOutbox.create_live(tmp_path / 'reviewed-journal' / 'outbox.sqlite', payload_path=seed,
        expected_sha256=digest(raw), authorized_session_manifest=scope['session'], initial_operations=[first])
    with box._db() as db:
        db.execute("UPDATE operations SET state='uncertain',attempt_count=1")
        db.execute("UPDATE spans SET state='uncertain',attempt_count=1")
    callbacks = []
    class ForbiddenVerifier(verify.RemoteRunVerifier):
        def __init__(self): pass
        def validate_reviewed(self, *args, **kwargs):
            callbacks.append('invoked')
            raise AssertionError('Unsupported verifier callback must not execute')
    before = box.path.read_bytes()
    approval = tmp_path / 'absent-review-approval.json'
    with pytest.raises(PermissionError):
        box.reconcile_reviewed({}, approval_record=approval, verifier=ForbiddenVerifier())
    assert callbacks == [] and not approval.exists()
    assert box.path.read_bytes() == before
