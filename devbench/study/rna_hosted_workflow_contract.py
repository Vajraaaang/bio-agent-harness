"""Offline /3 synthetic live-workflow packet and exact-action input integrity.

Preparation is default-off and grants no effect authority. Admission assumes an
external trusted launcher provides independently reviewed handles, identities
and consent. Hashes and UUID role labels establish integrity, not human identity
or an authenticated electronic signature. This synthetic engineering scope does
not establish scientific, native-isolation, deployed-schema or clinical validity.
No credentials, transport, model or workflow executor is reachable here.
"""
from uuid import uuid4

from ..analysis.rna_trace_contract import PHASES, ROLES
from .contracts import canonical_json_bytes
from .langsmith_export import _time
from .langsmith_outbox import _digest, _uuid, digest
from .rna_hosted_connection_contract import (
    BINDING_KEYS, IDENTITY_KEYS, TARGET, _disjoint_approval_paths, _path,
    read_approval_handle, validate_output_path,
)
from .rna_hosted_preflight import APPROVAL_KEYS, _expiry, _target
from .rna_hosted_trace_contract import _closed, _copy


WORKFLOW_CAPS = dict(max_spans=8, max_bytes=131072, max_mutation_requests=16,
    max_read_requests=12, max_total_requests=28, max_response_bytes=65536,
    total_deadline_seconds=180, max_delivery_attempts=1)
_CORE_CAPS = {key: value for key, value in WORKFLOW_CAPS.items()
              if key not in {'max_total_requests', 'max_response_bytes'}}
WORKFLOW_FIXTURE = dict(format='devbench.rna-workflow-fixture/3',
    fixture_id='workflow-synthetic-engineering', data_scope='synthetic',
    expected_span_count=8, expected_root_count=1, expected_child_count=7,
    requirement_source='assistant_selected_synthetic_fixture',
    scientific_assessment='not_applicable_synthetic_engineering')
WORKFLOW_CRITERIA = dict(format='devbench.rna-workflow-criteria/3',
    scope='live_synthetic_engineering_observability_transport_only',
    fixture_id='workflow-synthetic-engineering', require_frozen_topology=True,
    require_exact_revision_proof=True, require_empty_inputs_outputs=True,
    require_separate_action_approvals=True, automatic_resend=False,
    automatic_reconciliation=False, limits=_copy(WORKFLOW_CAPS),
    # The /1 verifier needs a stage slot; it does not select or execute a
    # scientific method or register this engineering fixture in legacy trials.
    verifier_stage_scope='technical_adapter_slot_only',
    verifier_criteria=dict(format='devbench.rna-hosted-criteria/1',
        sample_matrix=[dict(fixture_id='workflow-synthetic-engineering',
            stage='pre_fit_methods', allocation=0, repeat=1)],
        api_p95_upper_seconds=5, visibility_deadline_seconds=5,
        # Engineering policy selected before hosted measurement, not a user requirement.
        poll_interval_seconds=.25, dashboard_window_seconds=30,
        max_extra_model_calls=0, max_extra_fits=0, max_extra_retrievals=0,
        max_source_writes=0, max_privacy_leaks=0, required_topology_fraction=1,
        censored_samples_fail=True, limits=_copy(_CORE_CAPS)))
_CRITERIA_DIGEST = digest(canonical_json_bytes(WORKFLOW_CRITERIA))
_FIXTURE_DIGEST = digest(canonical_json_bytes(WORKFLOW_FIXTURE))
_MODE = 'experimental_live_workflow'
_AMBIGUITY = 'retain_uncertainty_without_automatic_resend_or_reconciliation'
_ACTIONS = {'target_read': ['read_project', 'read_run'],
            'live_session': ['create_run', 'update_run']}
_TOPOLOGY_KEYS = frozenset({'span_id', 'parent_span_id', 'role', 'phase', 'attempt'})
_PACKET_KEYS = frozenset({'format', 'packet_id', 'prepared_at', 'expires_at',
    'region', 'data_scope', 'binding', 'target', 'session', 'topology', 'limits',
    'read_descriptor', 'target_read', 'live_session', 'evidence_path',
    'privacy_policy', 'schema_mode', 'authorize_upload', 'authorize_remote_read'})
_PROPOSAL_KEYS = frozenset({'format', 'proposal_id', 'packet_id', 'action',
    'data_scope', 'binding', 'target', 'allowed_operations', 'run_ids', 'limits',
    'subject_digest', 'evidence_path', 'expires_at', 'authorize_upload',
    'authorize_remote_read', 'privacy_policy', 'ambiguous_acceptance_policy'})
_REVIEW_KEYS = frozenset({'format', 'packet_digest', 'binding', 'target',
    'expires_at', 'approved', 'reviewer_id', 'implementation_owner_id'})
_HANDLE_KEYS = frozenset({'path', 'sha256'})
_APPROVAL_NAMES = frozenset({'source_runtime_review', 'target_read', 'live_session'})


def packet_digest(packet):
    """Integrity identifier only; not approval or an authenticated signature."""
    return digest(canonical_json_bytes(packet))


def workflow_installed_identity():
    """Read bounded installed product resources and distinct /3 fixture criteria."""
    from .rna_hosted_qualification import bounded_installed_identity
    actual = bounded_installed_identity()
    identity = {key: actual[key] for key in ('source_digest', 'runtime_digest', 'policy_digest')}
    identity.update(criteria_digest=_CRITERIA_DIGEST, fixture_manifest_digest=_FIXTURE_DIGEST)
    _identity(identity)
    return identity


def _identity(value):
    _closed(value, IDENTITY_KEYS, 'workflow installed identity')
    for item in value.values():
        _digest(item)
    if value['criteria_digest'] != _CRITERIA_DIGEST or value['fixture_manifest_digest'] != _FIXTURE_DIGEST:
        raise ValueError('Workflow fixture/criteria identity mismatch')


def _binding(value):
    _closed(value, BINDING_KEYS, 'workflow reviewed binding')
    for item in value.values():
        _digest(item)


def _caps(value):
    _closed(value, frozenset(WORKFLOW_CAPS), 'immutable workflow caps')
    if any(type(value[key]) is not int or value[key] != cap for key, cap in WORKFLOW_CAPS.items()):
        raise ValueError('Immutable finite workflow caps required')


def validate_workflow_topology(topology):
    """Validate eight preallocated remote IDs without allocating observer IDs.

    Every nonroot parent must precede its child. UUID freshness is established
    by the reviewed launcher allocating fresh IDs, not by UUID syntax checks.
    The topology declares contexts, never evidence that they were executed.
    """
    try:
        if not isinstance(topology, list) or len(topology) != WORKFLOW_CAPS['max_spans']:
            raise ValueError('Exactly eight frozen workflow contexts required')
        depths, slots = {}, set()
        for index, span in enumerate(topology):
            _closed(span, _TOPOLOGY_KEYS, 'workflow topology context')
            identity = _uuid(span['span_id'])
            if (identity in depths or span['role'] not in ROLES or span['phase'] not in PHASES
                    or type(span['attempt']) is not int or span['attempt'] != 1):
                raise ValueError('Unique allowed single-attempt context required')
            slot = (span['role'], span['phase'], span['attempt'])
            if slot in slots:
                raise ValueError('Unique workflow slot required')
            slots.add(slot)
            parent = span['parent_span_id']
            if index == 0:
                if parent is not None or span['role'] != 'coordinator' or span['phase'] != 'workflow':
                    raise ValueError('Coordinator workflow root required')
                depth = 1
            else:
                _uuid(parent)
                if parent not in depths:
                    raise ValueError('Frozen parent must precede child')
                depth = depths[parent] + 1
            if depth > 8:
                raise ValueError('Workflow depth exceeded')
            depths[identity] = depth
        return _copy(topology)
    except Exception:
        raise ValueError('Invalid closed workflow topology') from None


def _session(packet, identity):
    from .rna_langsmith_live import validate_live_session
    session = packet['session']
    if (not isinstance(session, dict)
            or session.get('format') != 'devbench.rna-live-trace-session/3'
            or session.get('authorize_upload') is not False):
        raise ValueError('Disabled prepared /3 session required')
    # Structural inspection does not enable the prepared manifest. The admitted
    # engine must derive and retain its own separately authorized execution copy.
    validate_live_session({**session, 'authorize_upload': True})
    topology = validate_workflow_topology(packet['topology'])
    if (session['span_topology'] != topology or session['data_scope'] != 'synthetic'
            or session['expires_at'] != packet['expires_at']
            or any(session[key] != identity[key] for key in ('source_digest', 'runtime_digest', 'policy_digest'))
            or session['endpoint'] != TARGET['endpoint'] or session['project'] != TARGET['project_name']
            or session['project_id'] != TARGET['project_id'] or session['workspace_id'] != TARGET['workspace_id']
            or any(session[key] != WORKFLOW_CAPS[key] for key in ('max_spans', 'max_bytes', 'max_delivery_attempts'))):
        raise ValueError('Exact prepared workflow session required')
    return topology


def _read_descriptor(topology):
    identities = [span['span_id'] for span in topology]
    return dict(format='devbench.rna-workflow-read/3', root_run_id=identities[0],
        run_ids=identities, project_route='/api/v1/sessions/' + TARGET['project_id'] + '?include_stats=false',
        run_routes=['/api/v1/runs/' + identity for identity in identities],
        max_project_age_seconds=30, recovery_policy='no_automatic_resend_or_reconciliation')


def _proposal(packet, action, proposal_id):
    subject = packet['read_descriptor'] if action == 'target_read' else packet['session']
    return dict(format='devbench.rna-hosted-action-proposal/3', proposal_id=proposal_id,
        packet_id=packet['packet_id'], action=action, data_scope='synthetic',
        binding=_copy(packet['binding']), target=_copy(packet['target']),
        allowed_operations=_ACTIONS[action].copy(),
        run_ids=[span['span_id'] for span in packet['topology']], limits=_copy(packet['limits']),
        subject_digest=packet_digest(subject), evidence_path=packet['evidence_path'],
        expires_at=packet['expires_at'], authorize_upload=False, authorize_remote_read=False,
        privacy_policy='rna-allowlist/1', ambiguous_acceptance_policy=_AMBIGUITY)


def prepare_workflow_packet(*, installed_identity, target, session, topology,
                            expires_at, evidence_path, now):
    """Prepare a disabled topology proposal without writes, secrets or effects.

    Starts and finishes are emitted only after separate live-session approval;
    they cannot be supplied as precomputed operations in this packet.
    """
    try:
        _identity(installed_identity)
        _expiry(expires_at, now)
        binding = {**installed_identity, 'session_digest': packet_digest(session),
                   'payload_digest': packet_digest(topology)}
        packet = dict(format='devbench.rna-hosted-workflow/3', packet_id=str(uuid4()),
            prepared_at=now.isoformat(), expires_at=expires_at, region='us', data_scope='synthetic',
            binding=_copy(binding), target=_copy(target), session=_copy(session), topology=_copy(topology),
            limits=_copy(WORKFLOW_CAPS), read_descriptor=_read_descriptor(topology),
            evidence_path=str(evidence_path), privacy_policy='rna-allowlist/1',
            schema_mode=_MODE, authorize_upload=False, authorize_remote_read=False)
        for action in _ACTIONS:
            packet[action] = _proposal(packet, action, str(uuid4()))
        return validate_workflow_packet(packet, installed_identity=installed_identity,
            expected_binding=binding, expected_target=target, now=now)
    except Exception:
        raise ValueError('Invalid closed workflow packet') from None


def _packet_structure(packet, *, now, fresh_output):
    try:
        _closed(packet, _PACKET_KEYS, 'workflow packet')
        if (packet['format'] != 'devbench.rna-hosted-workflow/3' or packet['region'] != 'us'
                or packet['data_scope'] != 'synthetic' or packet['schema_mode'] != _MODE
                or packet['authorize_upload'] is not False or packet['authorize_remote_read'] is not False
                or packet['privacy_policy'] != 'rna-allowlist/1'):
            raise ValueError('Disabled experimental synthetic workflow required')
        _uuid(packet['packet_id'])
        _binding(packet['binding'])
        declared_identity = {key: packet['binding'][key] for key in IDENTITY_KEYS}
        _identity(declared_identity)
        _target(packet['target'])
        if packet['target'] != TARGET:
            raise ValueError('Exact existing workflow target required')
        expires = _expiry(packet['expires_at'], now)
        prepared = _time(packet['prepared_at'])
        if prepared is None or prepared > now or not 0 < (expires - prepared).total_seconds() <= 1800:
            raise ValueError('Invalid frozen preparation/expiry interval')
        _caps(packet['limits'])
        if fresh_output:
            validate_output_path(packet['evidence_path'])
        else:
            _path(packet['evidence_path'])
        topology = _session(packet, declared_identity)
        if (packet['binding']['session_digest'] != packet_digest(packet['session'])
                or packet['binding']['payload_digest'] != packet_digest(topology)
                or packet['read_descriptor'] != _read_descriptor(topology)):
            raise ValueError('Workflow session/topology/read binding mismatch')
        proposal_ids = set()
        for action in _ACTIONS:
            proposal = packet[action]
            _closed(proposal, _PROPOSAL_KEYS, 'disabled /3 action proposal')
            proposal_id = _uuid(proposal['proposal_id'])
            _caps(proposal['limits'])
            if (proposal['authorize_upload'] is not False or proposal['authorize_remote_read'] is not False
                    or proposal_id in proposal_ids or proposal_id == packet['packet_id']
                    or proposal != _proposal(packet, action, proposal_id)):
                raise ValueError('Exact distinct disabled workflow action required')
            proposal_ids.add(proposal_id)
        if len(canonical_json_bytes(packet)) > 65536:
            raise ValueError('Workflow packet ceiling exceeded')
        return _copy(packet)
    except Exception:
        raise ValueError('Invalid closed workflow packet') from None


def validate_workflow_wire_packet(packet, *, now):
    """Reject unknown/private wire fields without attesting source or consent."""
    return _packet_structure(packet, now=now, fresh_output=False)


def validate_workflow_packet(packet, *, installed_identity, expected_binding,
                             expected_target, now, fresh_output=True):
    """Compare with independently computed installation and reviewed bindings."""
    try:
        _closed(packet, _PACKET_KEYS, 'workflow packet')
        _identity(installed_identity)
        _binding(expected_binding)
        _binding(packet['binding'])
        _target(expected_target)
        if (type(fresh_output) is not bool or packet['binding'] != expected_binding
                or any(packet['binding'][key] != installed_identity[key] for key in IDENTITY_KEYS)
                or packet['target'] != expected_target or expected_target != TARGET):
            raise ValueError('Independent installed/reviewed workflow mismatch')
        return _packet_structure(packet, now=now, fresh_output=fresh_output)
    except Exception:
        raise ValueError('Invalid closed workflow packet') from None


def _review(record, packet, expected_binding, expected_target, now):
    _closed(record, _REVIEW_KEYS, 'independent workflow review input')
    if (record['format'] != 'devbench.rna-workflow-review/3' or record['approved'] is not True
            or record['packet_digest'] != packet_digest(packet) or record['binding'] != expected_binding
            or record['target'] != expected_target or record['expires_at'] != packet['expires_at']):
        raise ValueError('Independent exact workflow review required')
    _digest(record['packet_digest'])
    _uuid(record['reviewer_id'])
    _uuid(record['implementation_owner_id'])
    _expiry(record['expires_at'], now)
    if record['reviewer_id'] == record['implementation_owner_id']:
        raise PermissionError('Implementation owner cannot self-review')


def _approval(record, proposal, owner, now):
    _closed(record, APPROVAL_KEYS, 'separate /3 operator input')
    if (record['format'] != 'devbench.rna-hosted-action-approval/3' or record['approved'] is not True
            or record['proposal_digest'] != packet_digest(proposal) or record['action'] != proposal['action']
            or record['expires_at'] != proposal['expires_at'] or record['implementation_owner_id'] != owner):
        raise ValueError('Exact separately approved /3 action required')
    _digest(record['proposal_digest'])
    _uuid(record['operator_id'])
    _uuid(record['implementation_owner_id'])
    _expiry(record['expires_at'], now)
    if record['operator_id'] == record['implementation_owner_id']:
        raise PermissionError('Implementation owner cannot self-approve')


def admit_workflow_packet(packet, *, installed_identity, expected_binding,
                          expected_target, now, approval_handles, consumed_packet_ids=()):
    """Check exact trusted-launcher inputs without consuming or executing them.

    The engine must durably reserve the packet before resolving a key and enforce
    wall/HTTP/byte limits. An ambiguous send grants no resend or reconciliation.
    """
    packet = validate_workflow_packet(packet, installed_identity=installed_identity,
        expected_binding=expected_binding, expected_target=expected_target, now=now)
    if not isinstance(consumed_packet_ids, (list, tuple, set, frozenset)):
        raise ValueError('Consumed packet identity set required')
    for identity in consumed_packet_ids:
        _uuid(identity)
    if len(set(consumed_packet_ids)) != len(consumed_packet_ids) or packet['packet_id'] in consumed_packet_ids:
        raise PermissionError('Workflow packet replay prohibited')
    result = dict(format='devbench.rna-workflow-admission/3', status='blocked',
        packet_digest=packet_digest(packet), blockers=['trusted_operator_approval_handles_absent'],
        approval_record_digests={}, authorize_upload=False, authorize_remote_read=False,
        schema_mode=_MODE, deployed_schema_qualified=False)
    if approval_handles == {}:
        return result
    approval_digests = validate_workflow_approvals(packet, expected_binding=expected_binding,
        expected_target=expected_target, now=now, approval_handles=approval_handles)
    result.update(status='admitted', blockers=[], authorize_upload=True, authorize_remote_read=True,
        approval_record_digests=approval_digests)
    return result


def validate_workflow_approvals(packet, *, expected_binding, expected_target, now,
                                approval_handles):
    """Recheck /3 action handles for the engine's immutable admitted packet.

    This helper grants no execution authority and permits a reserved output path;
    packet reservation, deadline, receipts and budgets remain engine duties.
    """
    packet = _packet_structure(packet, now=now, fresh_output=False)
    _binding(expected_binding)
    _target(expected_target)
    if (packet['binding'] != expected_binding or packet['target'] != expected_target
            or expected_target != TARGET):
        raise ValueError('Independent reviewed workflow binding required')
    _closed(approval_handles, _APPROVAL_NAMES, 'separate trusted workflow handles')
    for handle in approval_handles.values():
        _closed(handle, _HANDLE_KEYS, 'trusted operator input handle')
        _digest(handle['sha256'])
    if len({handle['path'] for handle in approval_handles.values()}) != 3:
        raise ValueError('Distinct operator input paths required')
    _disjoint_approval_paths(_path(packet['evidence_path']), approval_handles)
    records = {name: read_approval_handle(handle) for name, handle in approval_handles.items()}
    review = records['source_runtime_review']
    _review(review, packet, expected_binding, expected_target, now)
    for action in _ACTIONS:
        _approval(records[action], packet[action], review['implementation_owner_id'], now)
    return {name: packet_digest(record) for name, record in records.items()}
