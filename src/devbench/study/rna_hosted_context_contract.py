"""Default-off /4 synthetic context-observation packets and reviewed inputs.

Profiles declare an exact bounded checkpoint, including open and capture-only
contexts. They do not establish that source events occurred: the trusted bridge
must independently compare actual retained receipts with their sanitized,
session-aliased projections. No source IDs, request bodies or simulated usage
belong in a profile. Hashes and UUID labels establish integrity, not human
identity, authenticated approval, scientific validity or clinical qualification.
The existing trusted launcher, custody and admission boundary remains required.
No credentials, transport, models or scientific execution are reachable here.
"""
from uuid import uuid4

from ..analysis.rna_trace_contract import PHASES, ROLES
from .contracts import canonical_json_bytes
from .langsmith_export import _time
from .langsmith_outbox import _digest, _uuid, digest
from .rna_hosted_connection_contract import (
    IDENTITY_KEYS, TARGET, _binding, _disjoint_approval_paths,
    _path, read_approval_handle, validate_output_path,
)
from .rna_hosted_preflight import APPROVAL_KEYS, _expiry, _target
from .rna_hosted_trace_contract import _closed, _copy


CONTEXT_CAPS = dict(max_spans=16, max_source_events=64, max_bytes=262144,
    max_mutation_requests=32, max_read_requests=20, max_total_requests=52,
    max_response_bytes=131072, total_deadline_seconds=240, max_delivery_attempts=1)
_CORE_CAPS = {key: value for key, value in CONTEXT_CAPS.items()
              if key not in {'max_source_events', 'max_total_requests', 'max_response_bytes'}}
CONTEXT_FIXTURE = dict(format='devbench.rna-context-fixture/4', data_scope='synthetic',
    fixture_id='bounded-synthetic-context-observation',
    requirement_source='assistant_selected_engineering_profile',
    scientific_assessment='not_applicable_synthetic_engineering')
CONTEXT_CRITERIA = dict(format='devbench.rna-context-criteria/4',
    scope='synthetic_engineering_context_observation_transport_only',
    require_exact_profile=True, require_empty_inputs_outputs=True,
    require_separate_action_approvals=True, require_no_extra_model_calls=True,
    require_no_extra_fits=True, usage_policy='unavailable',
    digest_policy='session_aliased_sanitized_receipts', automatic_resend=False,
    automatic_reconciliation=False, limits=_copy(CONTEXT_CAPS),
    requirement_source='assistant_selected_engineering_policy',
    scientific_assessment='not_applicable_synthetic_engineering',
    verifier_stage_scope='technical_adapter_slot_only',
    verifier_timing_provenance=dict(origin='assistant_selected_engineering_defaults',
        user_prespecified=False, selected_before_live_context_measurement=True,
        qualification_scope='technical_adapter_only', external_requirement_record=None),
    # These pre-measurement engineering defaults serve the existing verifier
    # adapter; its stage label selects no scientific method or full qualification.
    verifier_criteria=dict(format='devbench.rna-hosted-criteria/1',
        sample_matrix=[dict(fixture_id=CONTEXT_FIXTURE['fixture_id'],
            stage='pre_fit_methods', allocation=0, repeat=1)],
        api_p95_upper_seconds=5, visibility_deadline_seconds=5,
        poll_interval_seconds=.25, dashboard_window_seconds=30,
        max_extra_model_calls=0, max_extra_fits=0, max_extra_retrievals=0,
        max_source_writes=0, max_privacy_leaks=0, required_topology_fraction=1,
        censored_samples_fail=True, limits=_copy(_CORE_CAPS)))
GENERATION_CONTEXT_FIXTURE = {**_copy(CONTEXT_FIXTURE),
    'format': 'devbench.rna-context-fixture/5',
    'fixture_id': 'bounded-synthetic-generation-context-observation'}
GENERATION_CONTEXT_CRITERIA = {**_copy(CONTEXT_CRITERIA),
    'format': 'devbench.rna-context-criteria/5',
    'scope': 'synthetic_engineering_generation_context_observation_transport_only',
    'dispatch_accounting': 'observed_launch_attempt_receipts_not_model_requests',
    'reported_tokens_and_subscription_credits': 'separate_local_evidence_not_exported'}
GENERATION_CONTEXT_CRITERIA['verifier_criteria']['sample_matrix'][0]['fixture_id'] = GENERATION_CONTEXT_FIXTURE['fixture_id']
_CRITERIA_DIGEST = digest(canonical_json_bytes(CONTEXT_CRITERIA))
_FIXTURE_DIGEST = digest(canonical_json_bytes(CONTEXT_FIXTURE))
_MODE = 'experimental_context_observation'
_AMBIGUITY = 'retain_uncertainty_without_automatic_resend_or_reconciliation'
_TERMINAL_STATUSES = frozenset({'completed', 'failed', 'cancelled', 'incomplete',
    'abstained', 'blocked', 'skipped', 'closed'})
_TOPOLOGY_KEYS = frozenset({'span_id', 'parent_span_id', 'role', 'phase', 'attempt'})
_PROFILE_KEYS = frozenset({'format', 'data_scope', 'provenance', 'usage_policy',
    'digest_policy', 'context_count', 'source_event_count', 'capture_event_count',
    'dispatch_event_count', 'operation_count', 'terminal_span_ids', 'open_span_ids', 'contexts'})
_CONTEXT_KEYS = frozenset({'span_id', 'context_alias', 'parent_context_alias',
    'start_observed', 'source_event_digests', 'source_event_count',
    'capture_event_count', 'terminal_status'})
_PACKET_KEYS = frozenset({'format', 'packet_id', 'prepared_at', 'expires_at',
    'region', 'data_scope', 'binding', 'target', 'session', 'topology', 'profile',
    'limits', 'read_descriptor', 'target_read', 'live_session', 'evidence_path',
    'privacy_policy', 'schema_mode', 'authorize_upload', 'authorize_remote_read'})
_PROPOSAL_KEYS = frozenset({'format', 'proposal_id', 'packet_id', 'action',
    'data_scope', 'binding', 'target', 'allowed_operations', 'run_ids', 'limits',
    'subject_digest', 'evidence_path', 'expires_at', 'authorize_upload',
    'authorize_remote_read', 'privacy_policy', 'ambiguous_acceptance_policy'})
_REVIEW_KEYS = frozenset({'format', 'packet_digest', 'binding', 'target',
    'expires_at', 'approved', 'reviewer_id', 'implementation_owner_id'})
_HANDLE_KEYS = frozenset({'path', 'sha256'})
_APPROVAL_NAMES = frozenset({'source_runtime_review', 'target_read', 'live_session'})
_ACTIONS = {'target_read': ['read_project', 'read_run'],
            'live_session': ['create_run', 'update_run']}


def packet_digest(value):
    """Integrity identifier only; not consent or an authenticated signature."""
    return digest(canonical_json_bytes(value))


def context_installed_identity():
    """Reuse bounded installed resources and bind distinct /4 fixed policy."""
    from .rna_hosted_qualification import bounded_installed_identity
    actual = bounded_installed_identity()
    identity = {key: actual[key] for key in ('source_digest', 'runtime_digest', 'policy_digest')}
    identity.update(criteria_digest=_CRITERIA_DIGEST, fixture_manifest_digest=_FIXTURE_DIGEST)
    _identity(identity)
    return identity


def generation_context_installed_identity():
    """Bind installed custody to the separate default-off /5 engineering policy."""
    from .rna_hosted_qualification import bounded_installed_identity
    actual = bounded_installed_identity()
    identity = {key: actual[key] for key in ('source_digest', 'runtime_digest', 'policy_digest')}
    identity.update(criteria_digest=packet_digest(GENERATION_CONTEXT_CRITERIA),
        fixture_manifest_digest=packet_digest(GENERATION_CONTEXT_FIXTURE))
    _identity(identity, generation=True)
    return identity


def _identity(value, *, generation=False):
    _closed(value, IDENTITY_KEYS, 'context installed identity')
    for item in value.values():
        _digest(item)
    criteria, fixture = ((packet_digest(GENERATION_CONTEXT_CRITERIA), packet_digest(GENERATION_CONTEXT_FIXTURE))
        if generation else (_CRITERIA_DIGEST, _FIXTURE_DIGEST))
    if value['criteria_digest'] != criteria or value['fixture_manifest_digest'] != fixture:
        raise ValueError('Context fixture/criteria identity mismatch')


def _integer(value, lower, upper):
    if type(value) is not int or not lower <= value <= upper:
        raise ValueError('Bounded exact context integer required')
    return value


def validate_context_topology(topology):
    """Check a finite parent-ordered tree without fixing phase or context count.

    Context aliases in the profile distinguish repeated role/phase/attempt
    combinations. A source attempt never authorizes another delivery attempt.
    """
    try:
        if not isinstance(topology, list) or not 1 <= len(topology) <= CONTEXT_CAPS['max_spans']:
            raise ValueError('Finite context topology required')
        depths = {}
        for index, span in enumerate(topology):
            _closed(span, _TOPOLOGY_KEYS, 'context topology row')
            identity = _uuid(span['span_id'])
            _integer(span['attempt'], 1, 2**31 - 1)
            if identity in depths or span['role'] not in ROLES or span['phase'] not in PHASES:
                raise ValueError('Unique allowed context required')
            parent = span['parent_span_id']
            if index == 0:
                if parent is not None or span['role'] != 'coordinator' or span['phase'] != 'workflow':
                    raise ValueError('Coordinator workflow root required')
                depth = 1
            else:
                _uuid(parent)
                if parent not in depths:
                    raise ValueError('Context parent must precede child')
                depth = depths[parent] + 1
            if depth > 16:
                raise ValueError('Context depth exceeded')
            depths[identity] = depth
        return _copy(topology)
    except Exception:
        raise ValueError('Invalid closed context topology') from None


def _validate_context_profile(profile, *, topology, generation=False):
    """Validate declared sanitized receipt counts and exact lifecycle partition.

    Digests alone cannot establish receipt kinds or provenance. The bridge must
    check them against actual sanitized projections before producing operations;
    capture status is never sufficient evidence of an explicit terminal event.
    """
    try:
        topology = validate_context_topology(topology)
        _closed(profile, _PROFILE_KEYS, 'context observation profile')
        if (profile['format'] != 'devbench.rna-context-observation-profile/' + ('5' if generation else '4')
                or profile['data_scope'] != 'synthetic' or profile['provenance'] != 'synthetic_engineering'
                or profile['usage_policy'] != 'unavailable'
                or profile['digest_policy'] != 'session_aliased_sanitized_receipts'):
            raise ValueError('Synthetic unavailable-usage context profile required')
        count = len(topology)
        _integer(profile['context_count'], 1, CONTEXT_CAPS['max_spans'])
        _integer(profile['source_event_count'], 1, CONTEXT_CAPS['max_source_events'])
        _integer(profile['capture_event_count'], 0, CONTEXT_CAPS['max_source_events'])
        _integer(profile['dispatch_event_count'], 1 if generation else 0, CONTEXT_CAPS['max_source_events'] if generation else 0)
        _integer(profile['operation_count'], 1, CONTEXT_CAPS['max_mutation_requests'])
        rows = profile['contexts']
        if profile['context_count'] != count or not isinstance(rows, list) or len(rows) != count:
            raise ValueError('Exact context profile row count required')
        identities = {span['span_id'] for span in topology}
        aliases, seen_aliases, seen_events = {}, set(), set()
        sources, captures, dispatches, terminals, opened = 0, 0, 0, [], []
        for span, row in zip(topology, rows):
            _closed(row, _CONTEXT_KEYS | ({'dispatch_event_count'} if generation else set()), 'context observation row')
            if row['span_id'] != span['span_id']:
                raise ValueError('Exact ordered topology/profile binding required')
            alias = _uuid(row['context_alias'])
            if alias in identities or alias in seen_aliases:
                raise ValueError('Unique separate context alias required')
            seen_aliases.add(alias)
            parent = span['parent_span_id']
            if row['parent_context_alias'] != (None if parent is None else aliases[parent]):
                raise ValueError('Exact parent-qualified context alias required')
            aliases[span['span_id']] = alias
            if type(row['start_observed']) is not bool:
                raise ValueError('Explicit source start-observation flag required')
            source_count = _integer(row['source_event_count'], 1, CONTEXT_CAPS['max_source_events'])
            capture_count = _integer(row['capture_event_count'], 0, source_count)
            dispatch_count = _integer(row['dispatch_event_count'], 0, source_count) if generation else 0
            status = row['terminal_status']
            if status is not None and status not in _TERMINAL_STATUSES:
                raise ValueError('Known explicit terminal status required')
            if (not row['start_observed'] and capture_count == 0
                    or capture_count + max(int(row['start_observed']), dispatch_count) + int(status is not None) > source_count):
                raise ValueError('Separate start/capture/terminal receipt counts required')
            events = row['source_event_digests']
            if not isinstance(events, list) or len(events) != source_count:
                raise ValueError('Exact sanitized source-event digests required')
            for event_digest in events:
                _digest(event_digest)
                if event_digest in seen_events:
                    raise ValueError('Sanitized source event replay prohibited')
                seen_events.add(event_digest)
            sources += source_count
            captures += capture_count
            dispatches += dispatch_count
            (opened if status is None else terminals).append(span['span_id'])
        if (profile['source_event_count'] != sources or profile['capture_event_count'] != captures
                or profile['dispatch_event_count'] != dispatches
                or profile['operation_count'] != count + len(terminals)
                or profile['terminal_span_ids'] != terminals or profile['open_span_ids'] != opened):
            raise ValueError('Exact source counts and lifecycle partition required')
        return _copy(profile)
    except Exception:
        raise ValueError('Invalid closed context profile') from None


def validate_context_profile(profile, *, topology):
    return _validate_context_profile(profile, topology=topology)


def validate_generation_context_profile(profile, *, topology):
    """Exact retained dispatch counts; no model-request, token or credit inference."""
    return _validate_context_profile(profile, topology=topology, generation=True)


def _limits(profile):
    count = profile['context_count']
    mutations = profile['operation_count']
    reads = min(CONTEXT_CAPS['max_read_requests'], count + 4)
    return {**CONTEXT_CAPS, 'max_spans': count,
        'max_source_events': profile['source_event_count'], 'max_mutation_requests': mutations,
        'max_read_requests': reads, 'max_total_requests': mutations + reads}


def _caps(value, profile):
    _closed(value, frozenset(CONTEXT_CAPS), 'narrowed context caps')
    expected = _limits(profile)
    if any(type(value[key]) is not int or value[key] != cap for key, cap in expected.items()):
        raise ValueError('Exact finite profile-narrowed context caps required')


def _session(packet, identity, *, generation=False):
    from .rna_langsmith_live import validate_live_session
    session = packet['session']
    if (not isinstance(session, dict) or session.get('format') != 'devbench.rna-live-trace-session/' + ('5' if generation else '4')
            or session.get('authorize_upload') is not False):
        raise ValueError('Disabled prepared /4 session required')
    # Structural inspection creates no executable authority; the admitted child
    # retains a separately authorized execution copy after exact-action review.
    validate_live_session({**session, 'authorize_upload': True})
    if (session['span_topology'] != packet['topology'] or session['context_profile'] != packet['profile']
            or session['data_scope'] != 'synthetic' or session['expires_at'] != packet['expires_at']
            or any(session[key] != identity[key] for key in ('source_digest', 'runtime_digest', 'policy_digest'))
            or session['endpoint'] != TARGET['endpoint'] or session['project'] != TARGET['project_name']
            or session['project_id'] != TARGET['project_id'] or session['workspace_id'] != TARGET['workspace_id']
            or session['max_spans'] != packet['profile']['context_count']
            or session['max_bytes'] != CONTEXT_CAPS['max_bytes']
            or session['max_delivery_attempts'] != 1):
        raise ValueError('Exact prepared context session required')


def _read_descriptor(topology, *, generation=False):
    identities = [span['span_id'] for span in topology]
    return dict(format='devbench.rna-context-read/' + ('5' if generation else '4'), root_run_id=identities[0],
        run_ids=identities, project_route='/api/v1/sessions/' + TARGET['project_id'] + '?include_stats=false',
        run_routes=['/api/v1/runs/' + identity for identity in identities],
        max_project_age_seconds=30, recovery_policy='no_automatic_resend_or_reconciliation')


def _proposal(packet, action, proposal_id, *, generation=False):
    subject = packet['read_descriptor'] if action == 'target_read' else packet['session']
    return dict(format='devbench.rna-hosted-action-proposal/' + ('5' if generation else '4'), proposal_id=proposal_id,
        packet_id=packet['packet_id'], action=action, data_scope='synthetic',
        binding=_copy(packet['binding']), target=_copy(packet['target']),
        allowed_operations=_ACTIONS[action].copy(), run_ids=[span['span_id'] for span in packet['topology']],
        limits=_copy(packet['limits']), subject_digest=packet_digest(subject),
        evidence_path=packet['evidence_path'], expires_at=packet['expires_at'],
        authorize_upload=False, authorize_remote_read=False, privacy_policy='rna-allowlist/1',
        ambiguous_acceptance_policy=_AMBIGUITY)


def _prepare_context_packet(*, installed_identity, target, session, topology, profile,
                           expires_at, evidence_path, now, generation=False):
    """Prepare a disabled profile proposal without files, network or secrets."""
    try:
        _identity(installed_identity, generation=generation)
        _expiry(expires_at, now)
        topology = validate_context_topology(topology)
        profile = _validate_context_profile(profile, topology=topology, generation=generation)
        binding = {**installed_identity, 'session_digest': packet_digest(session),
            'payload_digest': packet_digest(dict(topology=topology, profile=profile))}
        packet = dict(format='devbench.rna-hosted-context/' + ('5' if generation else '4'), packet_id=str(uuid4()),
            prepared_at=now.isoformat(), expires_at=expires_at, region='us', data_scope='synthetic',
            binding=_copy(binding), target=_copy(target), session=_copy(session),
            topology=topology, profile=profile, limits=_limits(profile),
            read_descriptor=_read_descriptor(topology, generation=generation), evidence_path=str(evidence_path),
            privacy_policy='rna-allowlist/1', schema_mode=_mode(generation),
            authorize_upload=False, authorize_remote_read=False)
        for action in _ACTIONS:
            packet[action] = _proposal(packet, action, str(uuid4()), generation=generation)
        return _validate_context_packet(packet, installed_identity=installed_identity,
            expected_binding=binding, expected_target=target, now=now, generation=generation)
    except Exception:
        raise ValueError('Invalid closed context packet') from None


def _packet_structure(packet, *, now, fresh_output, generation=False):
    try:
        _closed(packet, _PACKET_KEYS, 'context packet')
        if (packet['format'] != 'devbench.rna-hosted-context/' + ('5' if generation else '4') or packet['region'] != 'us'
                or packet['data_scope'] != 'synthetic' or packet['schema_mode'] != _mode(generation)
                or packet['authorize_upload'] is not False or packet['authorize_remote_read'] is not False
                or packet['privacy_policy'] != 'rna-allowlist/1'):
            raise ValueError('Disabled experimental synthetic context packet required')
        _uuid(packet['packet_id'])
        _binding(packet['binding'])
        identity = {key: packet['binding'][key] for key in IDENTITY_KEYS}
        _identity(identity, generation=generation)
        _target(packet['target'])
        if packet['target'] != TARGET:
            raise ValueError('Exact existing context target required')
        expires = _expiry(packet['expires_at'], now)
        prepared = _time(packet['prepared_at'])
        if prepared is None or prepared > now or not 0 < (expires - prepared).total_seconds() <= 1800:
            raise ValueError('Invalid frozen preparation/expiry interval')
        validate_context_topology(packet['topology'])
        profile = _validate_context_profile(packet['profile'], topology=packet['topology'], generation=generation)
        _caps(packet['limits'], profile)
        if fresh_output:
            validate_output_path(packet['evidence_path'])
        else:
            _path(packet['evidence_path'])
        _session(packet, identity, generation=generation)
        if (packet['binding']['session_digest'] != packet_digest(packet['session'])
                or packet['binding']['payload_digest'] != packet_digest(dict(topology=packet['topology'], profile=profile))
                or packet['read_descriptor'] != _read_descriptor(packet['topology'], generation=generation)):
            raise ValueError('Exact context session/profile/read binding required')
        seen = {packet['packet_id']}
        for action in _ACTIONS:
            proposal = packet[action]
            _closed(proposal, _PROPOSAL_KEYS, 'disabled /4 action proposal')
            proposal_id = _uuid(proposal['proposal_id'])
            _caps(proposal['limits'], profile)
            if (proposal_id in seen or proposal != _proposal(packet, action, proposal_id, generation=generation)
                    or proposal['authorize_upload'] is not False or proposal['authorize_remote_read'] is not False):
                raise ValueError('Distinct exact disabled context action required')
            seen.add(proposal_id)
        if len(canonical_json_bytes(packet)) > 131072:
            raise ValueError('Context packet ceiling exceeded')
        return _copy(packet)
    except Exception:
        raise ValueError('Invalid closed context packet') from None


def validate_context_wire_packet(packet, *, now):
    """Reject unknown/private wire fields without granting effect authority."""
    return _packet_structure(packet, now=now, fresh_output=False)


def _validate_context_packet(packet, *, installed_identity, expected_binding,
                            expected_target, now, fresh_output=True, generation=False):
    """Compare independently installed and separately reviewed exact bindings."""
    try:
        _closed(packet, _PACKET_KEYS, 'context packet')
        _identity(installed_identity, generation=generation)
        _binding(expected_binding)
        _binding(packet['binding'])
        _target(expected_target)
        if (type(fresh_output) is not bool or packet['binding'] != expected_binding
                or any(packet['binding'][key] != installed_identity[key] for key in IDENTITY_KEYS)
                or packet['target'] != expected_target or expected_target != TARGET):
            raise ValueError('Independent installed/reviewed context mismatch')
        return _packet_structure(packet, now=now, fresh_output=fresh_output, generation=generation)
    except Exception:
        raise ValueError('Invalid closed context packet') from None


def _review(record, packet, expected_binding, expected_target, now, *, generation=False):
    _closed(record, _REVIEW_KEYS, 'independent context review input')
    if (record['format'] != 'devbench.rna-context-review/' + ('5' if generation else '4') or record['approved'] is not True
            or record['packet_digest'] != packet_digest(packet) or record['binding'] != expected_binding
            or record['target'] != expected_target or record['expires_at'] != packet['expires_at']):
        raise ValueError('Independent exact context review required')
    _digest(record['packet_digest'])
    _uuid(record['reviewer_id'])
    _uuid(record['implementation_owner_id'])
    _expiry(record['expires_at'], now)
    if record['reviewer_id'] == record['implementation_owner_id']:
        raise PermissionError('Implementation owner cannot self-review')


def _approval(record, proposal, owner, now, *, generation=False):
    _closed(record, APPROVAL_KEYS, 'separate /4 operator input')
    if (record['format'] != 'devbench.rna-hosted-action-approval/' + ('5' if generation else '4') or record['approved'] is not True
            or record['proposal_digest'] != packet_digest(proposal) or record['action'] != proposal['action']
            or record['expires_at'] != proposal['expires_at'] or record['implementation_owner_id'] != owner):
        raise ValueError('Exact separately approved /4 action required')
    _digest(record['proposal_digest'])
    _uuid(record['operator_id'])
    _uuid(record['implementation_owner_id'])
    _expiry(record['expires_at'], now)
    if record['operator_id'] == record['implementation_owner_id']:
        raise PermissionError('Implementation owner cannot self-approve')


def _admit_context_packet(packet, *, installed_identity, expected_binding,
                         expected_target, now, approval_handles, consumed_packet_ids=(), generation=False):
    """Check trusted-launcher inputs; reservation and effect limits stay external."""
    packet = _validate_context_packet(packet, installed_identity=installed_identity,
        expected_binding=expected_binding, expected_target=expected_target, now=now, generation=generation)
    if not isinstance(consumed_packet_ids, (list, tuple, set, frozenset)):
        raise ValueError('Consumed context packet identities required')
    for identity in consumed_packet_ids:
        _uuid(identity)
    if len(set(consumed_packet_ids)) != len(consumed_packet_ids) or packet['packet_id'] in consumed_packet_ids:
        raise PermissionError('Context packet replay prohibited')
    result = dict(format='devbench.rna-context-admission/' + ('5' if generation else '4'), status='blocked',
        packet_digest=packet_digest(packet), blockers=['trusted_operator_approval_handles_absent'],
        approval_record_digests={}, authorize_upload=False, authorize_remote_read=False,
        schema_mode=_mode(generation), deployed_schema_qualified=False)
    if approval_handles == {}:
        return result
    approvals = _validate_context_approvals(packet, expected_binding=expected_binding,
        expected_target=expected_target, now=now, approval_handles=approval_handles, generation=generation)
    result.update(status='admitted', blockers=[], approval_record_digests=approvals,
                  authorize_upload=True, authorize_remote_read=True)
    return result


def _validate_context_approvals(packet, *, expected_binding, expected_target, now,
                               approval_handles, generation=False):
    """Recheck exact /4 inputs without pretending an owned output is still fresh."""
    packet = _packet_structure(packet, now=now, fresh_output=False, generation=generation)
    _binding(expected_binding)
    _target(expected_target)
    if packet['binding'] != expected_binding or packet['target'] != expected_target or expected_target != TARGET:
        raise ValueError('Independent reviewed context binding required')
    _closed(approval_handles, _APPROVAL_NAMES, 'separate trusted context handles')
    for handle in approval_handles.values():
        _closed(handle, _HANDLE_KEYS, 'trusted operator input handle')
        _digest(handle['sha256'])
    if len({handle['path'] for handle in approval_handles.values()}) != 3:
        raise ValueError('Distinct operator input paths required')
    _disjoint_approval_paths(_path(packet['evidence_path']), approval_handles)
    records = {name: read_approval_handle(handle) for name, handle in approval_handles.items()}
    review = records['source_runtime_review']
    _review(review, packet, expected_binding, expected_target, now, generation=generation)
    for action in _ACTIONS:
        _approval(records[action], packet[action], review['implementation_owner_id'], now, generation=generation)
    return {name: packet_digest(record) for name, record in records.items()}


def _mode(generation):
    return 'experimental_generation_context_observation' if generation else _MODE


def prepare_context_packet(*, installed_identity, target, session, topology, profile,
                           expires_at, evidence_path, now):
    return _prepare_context_packet(installed_identity=installed_identity, target=target,
        session=session, topology=topology, profile=profile, expires_at=expires_at,
        evidence_path=evidence_path, now=now)


def prepare_generation_context_packet(*, installed_identity, target, session, topology, profile,
                                      expires_at, evidence_path, now):
    return _prepare_context_packet(installed_identity=installed_identity, target=target,
        session=session, topology=topology, profile=profile, expires_at=expires_at,
        evidence_path=evidence_path, now=now, generation=True)


def validate_context_packet(packet, *, installed_identity, expected_binding,
                            expected_target, now, fresh_output=True):
    return _validate_context_packet(packet, installed_identity=installed_identity,
        expected_binding=expected_binding, expected_target=expected_target, now=now,
        fresh_output=fresh_output)


def validate_generation_context_packet(packet, *, installed_identity, expected_binding,
                                       expected_target, now, fresh_output=True):
    return _validate_context_packet(packet, installed_identity=installed_identity,
        expected_binding=expected_binding, expected_target=expected_target, now=now,
        fresh_output=fresh_output, generation=True)


def validate_generation_context_wire_packet(packet, *, now):
    return _packet_structure(packet, now=now, fresh_output=False, generation=True)


def admit_context_packet(packet, *, installed_identity, expected_binding,
                         expected_target, now, approval_handles, consumed_packet_ids=()):
    return _admit_context_packet(packet, installed_identity=installed_identity,
        expected_binding=expected_binding, expected_target=expected_target, now=now,
        approval_handles=approval_handles, consumed_packet_ids=consumed_packet_ids)


def admit_generation_context_packet(packet, *, installed_identity, expected_binding,
                                    expected_target, now, approval_handles, consumed_packet_ids=()):
    return _admit_context_packet(packet, installed_identity=installed_identity,
        expected_binding=expected_binding, expected_target=expected_target, now=now,
        approval_handles=approval_handles, consumed_packet_ids=consumed_packet_ids, generation=True)


def validate_context_approvals(packet, *, expected_binding, expected_target, now, approval_handles):
    return _validate_context_approvals(packet, expected_binding=expected_binding,
        expected_target=expected_target, now=now, approval_handles=approval_handles)


def validate_generation_context_approvals(packet, *, expected_binding, expected_target, now, approval_handles):
    return _validate_context_approvals(packet, expected_binding=expected_binding,
        expected_target=expected_target, now=now, approval_handles=approval_handles, generation=True)
