"""Fresh default-off GET-only authority over independently retained /5 history.

The producer binding describes historical facts, never current upload authority.
This module checks proposals and private launcher-supplied review inputs. The
original installed child must independently check every historical relationship
against the externally retained seal before trusting a producer binding. UUID
actor labels and file hashes do not authenticate humans or electronic signatures.
No credential, HTTP, model, numerical worker or historical database is opened.
"""
from datetime import datetime, timezone
from uuid import uuid4

from .contracts import canonical_json_bytes
from .langsmith_export import _time
from .langsmith_outbox import _digest, _uuid, digest
from .rna_hosted_connection_contract import (
    IDENTITY_KEYS, TARGET, _path, _disjoint_approval_paths,
    read_approval_handle, validate_output_path,
)
from .rna_hosted_preflight import _expiry, _target
from .rna_hosted_trace_contract import _closed, _copy


READBACK_CAPS = dict(max_spans=16, max_source_events=64,
    max_mutation_requests=0, max_read_requests=17, max_total_requests=17,
    max_response_bytes=131072, total_deadline_seconds=60,
    request_timeout_seconds=15, project_max_age_seconds=30, max_delivery_attempts=0)
READBACK_FIXTURE = dict(format='devbench.rna-context-readback-fixture/1',
    fixture_id='retained-synthetic-generation-context-readback', data_scope='synthetic',
    requirement_source='assistant_selected_engineering_profile',
    scientific_assessment='not_applicable_synthetic_engineering')
READBACK_CRITERIA = dict(format='devbench.rna-context-readback-criteria/1',
    scope='current_readback_of_retained_synthetic_contexts_only',
    limits=_copy(READBACK_CAPS), require_independent_historical_root=True,
    require_current_reader_custody=True, require_fresh_project_receipt=True,
    require_exact_original_payload_and_latest_acknowledged_proof=True,
    original_failure_and_visibility_sla_unchanged=True, automatic_retry=False,
    automatic_reconciliation=False, full_route_qualified=False, science_qualified=False,
    requirement_source='assistant_selected_engineering_policy')
PRODUCER_BINDING_KEYS = frozenset({'source_digest', 'runtime_digest', 'policy_digest',
    'packet_digest', 'session_digest', 'topology_digest', 'operations_digest',
    'latest_acknowledged_digest', 'failure_seal_digest'})
_HISTORY_NAMES = frozenset({'packet','operations','outbox','source','checkpoint',
    'failure_seal','source_runtime_review','target_read','live_session'})
_PACKET_KEYS = frozenset({'format','packet_id','nonce','prepared_at','expires_at',
    'region','data_scope','reader_binding','producer_binding','target','session',
    'topology','history','limits','read_descriptor','readback','evidence_path',
    'privacy_policy','schema_mode','authorize_upload','authorize_remote_read'})
_PROPOSAL_KEYS = frozenset({'format','proposal_id','packet_id','nonce','action',
    'data_scope','reader_binding','producer_binding','target','run_ids',
    'allowed_operations','limits','subject_digest','evidence_path','expires_at',
    'authorize_upload','authorize_remote_read','privacy_policy','expected_historical_root'})
_COMMON_REVIEW_KEYS = frozenset({'reader_binding','producer_binding','target',
    'evidence_path','nonce','expected_historical_root','run_ids','limits','expires_at',
    'approved','implementation_owner_id'})
_REVIEW_KEYS = _COMMON_REVIEW_KEYS | {'format','packet_digest','reviewer_id'}
_APPROVAL_KEYS = _COMMON_REVIEW_KEYS | {'format','proposal_digest','action','operator_id'}
_HANDLE_KEYS = frozenset({'path','sha256'})
_MODE = 'experimental_historical_context_readback'


def packet_digest(value):
    return digest(canonical_json_bytes(value))


def _identity(value):
    _closed(value, IDENTITY_KEYS, 'current installed readback identity')
    for item in value.values():
        _digest(item)
    if (value['criteria_digest'] != packet_digest(READBACK_CRITERIA)
            or value['fixture_manifest_digest'] != packet_digest(READBACK_FIXTURE)):
        raise ValueError('Readback policy/fixture identity mismatch')


def readback_installed_identity():
    """Recompute current ordinary installed custody; never import an old wheel."""
    from .rna_hosted_qualification import bounded_installed_identity
    actual = bounded_installed_identity()
    result = {key: actual[key] for key in ('source_digest','runtime_digest','policy_digest')}
    result.update(criteria_digest=packet_digest(READBACK_CRITERIA),
        fixture_manifest_digest=packet_digest(READBACK_FIXTURE))
    _identity(result)
    return result


def _producer(value):
    _closed(value, PRODUCER_BINDING_KEYS, 'independently reviewed historical producer')
    for item in value.values():
        _digest(item)


def _limits(session):
    count = len(session['span_topology'])
    return {**READBACK_CAPS, 'max_spans': count,
        'max_source_events': session['context_profile']['source_event_count'],
        'max_read_requests': count + 1, 'max_total_requests': count + 1}


def _read_descriptor(topology):
    identities = [row['span_id'] for row in topology]
    return dict(format='devbench.rna-context-readback-read/1',
        root_run_id=identities[0], run_ids=identities,
        project_route='/api/v1/sessions/' + TARGET['project_id'] + '?include_stats=false',
        run_routes=['/api/v1/runs/' + identity for identity in identities],
        max_project_age_seconds=30, recovery_policy='no_automatic_resend_or_reconciliation')


def _history_paths(history, output):
    """Check bounded references only; member bytes remain the child's duty."""
    _closed(history, _HISTORY_NAMES | {'root'}, 'historical evidence references')
    root = _path(history['root'])
    paths = []
    for name in _HISTORY_NAMES:
        reference = history[name]
        _closed(reference, _HANDLE_KEYS, 'historical member reference')
        _digest(reference['sha256'])
        path = _path(reference['path'])
        if not path.is_relative_to(root) or path == root:
            raise ValueError('Historical member must lie within the frozen evidence root')
        paths.append(path)
    if len(set(paths)) != len(paths):
        raise ValueError('Distinct historical member references required')
    if output.is_relative_to(root) or root.is_relative_to(output):
        raise ValueError('Readback output overlaps original historical evidence')


def _proposal(packet, proposal_id):
    subject = {key: packet[key] for key in ('reader_binding','producer_binding',
        'target','session','topology','history','limits','read_descriptor','nonce','evidence_path')}
    return dict(format='devbench.rna-context-readback-action-proposal/1',
        proposal_id=proposal_id, packet_id=packet['packet_id'], nonce=packet['nonce'], action='readback',
        data_scope='synthetic', reader_binding=_copy(packet['reader_binding']),
        producer_binding=_copy(packet['producer_binding']), target=_copy(packet['target']),
        run_ids=packet['read_descriptor']['run_ids'].copy(), allowed_operations=['read_project','read_run'],
        limits=_copy(packet['limits']), subject_digest=packet_digest(subject),
        evidence_path=packet['evidence_path'], expires_at=packet['expires_at'],
        authorize_upload=False, authorize_remote_read=False, privacy_policy='rna-allowlist/1',
        expected_historical_root=packet['producer_binding']['failure_seal_digest'])


def _packet_structure(packet, *, now, fresh_output):
    from .rna_langsmith_live import validate_historical_context_session
    _closed(packet, _PACKET_KEYS, 'readback packet')
    if (packet['format'] != 'devbench.rna-hosted-context-readback/1'
            or packet['region'] != 'us' or packet['data_scope'] != 'synthetic'
            or packet['schema_mode'] != _MODE or packet['privacy_policy'] != 'rna-allowlist/1'
            or packet['authorize_upload'] is not False or packet['authorize_remote_read'] is not False):
        raise ValueError('Disabled synthetic readback packet required')
    _uuid(packet['packet_id']); _uuid(packet['nonce'])
    if packet['packet_id'] == packet['nonce']:
        raise ValueError('Distinct fresh packet and read nonce required')
    _identity(packet['reader_binding']); _producer(packet['producer_binding'])
    _target(packet['target'])
    if packet['target'] != TARGET:
        raise ValueError('Exact reviewed readback target required')
    expires = _expiry(packet['expires_at'], now)
    prepared = _time(packet['prepared_at'])
    if prepared is None or prepared > now or not 0 < (expires - prepared).total_seconds() <= 1800:
        raise ValueError('Bounded fresh readback preparation interval required')
    # The original authorized manifest is retained inside the sealed readonly
    # outbox. The child compares this exact canonical value with that manifest;
    # a newly fabricated historical session file supplies no additional trust.
    session = validate_historical_context_session(packet['session'])
    producer, history = packet['producer_binding'], packet['history']
    if (packet['topology'] != session['span_topology']
            or any(session[key] != producer[key] for key in ('source_digest','runtime_digest','policy_digest'))
            or session['endpoint'] != packet['target']['endpoint']
            or session['project'] != packet['target']['project_name']
            or any(session[key] != packet['target'][key] for key in ('project_id','workspace_id'))
            or packet_digest(session) != producer['session_digest']
            or packet_digest(packet['topology']) != producer['topology_digest']):
        raise ValueError('Exact original historical session/topology/target binding required')
    expected_limits = _limits(session)
    _closed(packet['limits'], set(expected_limits), 'exact GET-only readback limits')
    if any(type(packet['limits'][key]) is not int or packet['limits'][key] != cap for key, cap in expected_limits.items()):
        raise ValueError('Exact finite GET-only readback limits required')
    if packet['read_descriptor'] != _read_descriptor(packet['topology']):
        raise ValueError('Exact project and run GET allowlist required')
    output = validate_output_path(packet['evidence_path']) if fresh_output else _path(packet['evidence_path'])
    _history_paths(history, output)
    for name, binding_key in (('packet','packet_digest'),('operations','operations_digest'),
            ('failure_seal','failure_seal_digest')):
        if history[name]['sha256'] != producer[binding_key]:
            raise ValueError('Historical reference and producer digest mismatch')
    _closed(packet['readback'], _PROPOSAL_KEYS, 'fresh readback action proposal')
    proposal_id = _uuid(packet['readback']['proposal_id'])
    if (proposal_id in {packet['packet_id'],packet['nonce']}
            or canonical_json_bytes(packet['readback']) != canonical_json_bytes(_proposal(packet, proposal_id))
            or len(canonical_json_bytes(packet)) > 131072):
        raise ValueError('Exact distinct bounded readback proposal required')
    return _copy(packet)


def prepare_context_readback_packet(*, installed_identity, producer_binding, target,
        session, topology, history, evidence_path, expires_at, now=None):
    """Propose a fresh read without opening history or enabling any effect."""
    now = datetime.now(timezone.utc) if now is None else now
    packet = dict(format='devbench.rna-hosted-context-readback/1', packet_id=str(uuid4()), nonce=str(uuid4()),
        prepared_at=now.isoformat(), expires_at=expires_at, region='us', data_scope='synthetic',
        reader_binding=_copy(installed_identity), producer_binding=_copy(producer_binding),
        target=_copy(target), session=_copy(session), topology=_copy(topology), history=_copy(history),
        limits=_limits(session), read_descriptor=_read_descriptor(topology), evidence_path=str(evidence_path),
        privacy_policy='rna-allowlist/1', schema_mode=_MODE, authorize_upload=False, authorize_remote_read=False)
    packet['readback'] = _proposal(packet, str(uuid4()))
    return validate_context_readback_packet(packet, installed_identity=installed_identity,
        expected_producer_binding=producer_binding, expected_target=target, now=now)


def validate_context_readback_wire_packet(packet, *, now=None, fresh_output=False):
    try:
        if type(fresh_output) is not bool:
            raise ValueError('Explicit output freshness required')
        return _packet_structure(packet, now=datetime.now(timezone.utc) if now is None else now,
            fresh_output=fresh_output)
    except Exception:
        raise ValueError('Invalid closed historical context readback packet') from None


def validate_context_readback_packet(packet, *, installed_identity, expected_producer_binding,
        expected_target, now=None, fresh_output=True):
    try:
        _identity(installed_identity); _producer(expected_producer_binding); _target(expected_target)
        if (packet['reader_binding'] != installed_identity or packet['producer_binding'] != expected_producer_binding
                or packet['target'] != expected_target):
            raise ValueError('Independent current reader and original producer bindings required')
        return validate_context_readback_wire_packet(packet, now=now, fresh_output=fresh_output)
    except Exception:
        raise ValueError('Invalid independently bound context readback packet') from None


def validate_context_readback_approvals(packet, *, installed_identity, expected_producer_binding,
        expected_target, approval_handles, now=None):
    now = datetime.now(timezone.utc) if now is None else now
    packet = validate_context_readback_packet(packet, installed_identity=installed_identity,
        expected_producer_binding=expected_producer_binding, expected_target=expected_target,
        now=now, fresh_output=False)
    _closed(approval_handles, {'source_runtime_review','readback'}, 'fresh separate readback approval handles')
    for handle in approval_handles.values():
        _closed(handle, _HANDLE_KEYS, 'private readback approval handle'); _digest(handle['sha256'])
    paths = [_path(handle['path']) for handle in approval_handles.values()]
    if len(set(paths)) != 2:
        raise ValueError('Distinct current review/action paths required')
    _disjoint_approval_paths(_path(packet['evidence_path']), approval_handles)
    historical_root = _path(packet['history']['root'])
    if any(path.is_relative_to(historical_root) for path in paths):
        raise ValueError('Historical approvals cannot provide fresh readback authority')
    records = {name: read_approval_handle(handle) for name, handle in approval_handles.items()}
    common = dict(reader_binding=packet['reader_binding'], producer_binding=packet['producer_binding'],
        target=packet['target'], evidence_path=packet['evidence_path'], nonce=packet['nonce'],
        expected_historical_root=expected_producer_binding['failure_seal_digest'],
        run_ids=packet['read_descriptor']['run_ids'], limits=packet['limits'],
        expires_at=packet['expires_at'], approved=True)
    owner = records['source_runtime_review'].get('implementation_owner_id')
    _uuid(owner)
    for name, record in records.items():
        review = name == 'source_runtime_review'
        _closed(record, _REVIEW_KEYS if review else _APPROVAL_KEYS, 'fresh exact readback approval record')
        actor_key = 'reviewer_id' if review else 'operator_id'
        actor = _uuid(record[actor_key]); _uuid(record['implementation_owner_id'])
        expected = {**common, 'implementation_owner_id':owner, actor_key:actor,
            'format':'devbench.rna-context-readback-review/1' if review else 'devbench.rna-context-readback-action-approval/1'}
        if review:
            expected['packet_digest'] = packet_digest(packet)
        else:
            expected.update(proposal_digest=packet_digest(packet['readback']), action='readback')
        if canonical_json_bytes(record) != canonical_json_bytes(expected) or record['approved'] is not True:
            raise ValueError('Exact independent readback scope and approval required')
        _expiry(record['expires_at'], now)
        if actor == owner:
            raise PermissionError('Implementation owner cannot self-review or self-approve')
    return {name: packet_digest(record) for name, record in records.items()}


def admit_context_readback_packet(packet, *, installed_identity, expected_producer_binding,
        expected_target, approval_handles, now=None, consumed_packet_ids=(), fresh_output=True):
    now = datetime.now(timezone.utc) if now is None else now
    packet = validate_context_readback_packet(packet, installed_identity=installed_identity,
        expected_producer_binding=expected_producer_binding, expected_target=expected_target,
        now=now, fresh_output=fresh_output)
    if not isinstance(consumed_packet_ids, (list,tuple,set,frozenset)):
        raise ValueError('Consumed readback packet identities required')
    for value in consumed_packet_ids:
        _uuid(value)
    if len(set(consumed_packet_ids)) != len(consumed_packet_ids) or packet['packet_id'] in consumed_packet_ids:
        raise PermissionError('Readback packet replay prohibited')
    result = dict(format='devbench.rna-context-readback-admission/1', status='blocked',
        packet_digest=packet_digest(packet), blockers=['trusted_operator_approval_handles_absent'],
        approval_record_digests={}, authorize_upload=False, authorize_remote_read=False,
        full_route_qualified=False, science_qualified=False)
    if approval_handles == {}:
        return result
    approvals = validate_context_readback_approvals(packet, installed_identity=installed_identity,
        expected_producer_binding=expected_producer_binding, expected_target=expected_target,
        approval_handles=approval_handles, now=now)
    result.update(status='admitted', blockers=[], approval_record_digests=approvals, authorize_remote_read=True)
    return result
