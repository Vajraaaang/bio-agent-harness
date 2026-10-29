"""Offline /2 connection packet and trusted-operator input integrity checks.

A packet is a disabled proposal. Admission assumes a trusted operator launcher
supplies independently reviewed file handles, expected identities and consent.
Hashes, UUID role labels and private files do not authenticate a human, establish
an electronic signature, or deny access to another process under the same UID.
Source/runtime/action review and operator consent must occur outside this module.
The admitted mode is an experimental synthetic bootstrap, never deployed schema,
native isolation, scientific or clinical qualification. No secret or transport
resolver is reachable here. Frozen /1 contracts retain their original meaning.
"""
import json
import os
from pathlib import Path
import stat
import sys
from uuid import uuid4

from .contracts import canonical_json_bytes
from .langsmith_export import _time
from .langsmith_outbox import _digest, _uuid, digest
from .rna_hosted_preflight import APPROVAL_KEYS, _expiry, _target
from .rna_hosted_trace_contract import _closed, _copy


ASSIGNED_WORKSPACE = Path('/Users/vajraang/Documents/ChatGPT/bio-agent-harness')
TARGET = dict(endpoint='https://api.smith.langchain.com',
    workspace_id='59a08947-1ff8-4adf-956b-a7f0fd9eb21c',
    project_id='2f749903-8bc2-4f79-a7e6-75ae493fe4d4',
    project_name='bio-harness-hosted-qualification')
CONNECTION_CAPS = dict(max_spans=1, max_bytes=32768, max_mutation_requests=2,
    max_read_requests=4, max_total_requests=6, max_response_bytes=65536,
    total_deadline_seconds=60, max_delivery_attempts=1)
_CORE_CAPS = {key: value for key, value in CONNECTION_CAPS.items()
              if key not in {'max_total_requests', 'max_response_bytes'}}
CONNECTION_FIXTURE = dict(format='devbench.rna-connection-fixture/2',
    fixture_id='connection-synthetic-arithmetic', data_scope='synthetic',
    values=[1, 2, 3], operation='sum', expected_sum=6,
    requirement_source='assistant_selected_synthetic_fixture',
    scientific_assessment='not_applicable_synthetic_arithmetic')
CONNECTION_CRITERIA = dict(format='devbench.rna-connection-criteria/2',
    scope='connection_software_only', fixture_id='connection-synthetic-arithmetic',
    require_exact_revision_proof=True, require_empty_inputs_outputs=True,
    require_separate_reviewed_reconciliation=True,
    # The legacy verifier requires a stage slot; it does not select or execute
    # a scientific method or register this fixture in the legacy fixture set.
    verifier_stage_scope='technical_adapter_slot_only',
    verifier_criteria=dict(format='devbench.rna-hosted-criteria/1',
        sample_matrix=[dict(fixture_id='connection-synthetic-arithmetic',
            stage='pre_fit_methods', allocation=0, repeat=1)],
        api_p95_upper_seconds=5, visibility_deadline_seconds=5,
        poll_interval_seconds=.05, dashboard_window_seconds=1,
        max_extra_model_calls=0, max_extra_fits=0, max_extra_retrievals=0,
        max_source_writes=0, max_privacy_leaks=0, required_topology_fraction=1,
        censored_samples_fail=True, limits=_CORE_CAPS))
_CRITERIA_DIGEST = digest(canonical_json_bytes(CONNECTION_CRITERIA))
_FIXTURE_DIGEST = digest(canonical_json_bytes(CONNECTION_FIXTURE))
IDENTITY_KEYS = frozenset({'source_digest', 'runtime_digest', 'policy_digest',
    'criteria_digest', 'fixture_manifest_digest'})
BINDING_KEYS = IDENTITY_KEYS | {'session_digest', 'payload_digest'}
_MODE = 'experimental_joined_binding_bootstrap'
_AMBIGUITY = 'retain_uncertainty_until_exact_revision_proof_and_separate_review'
_ACTIONS = {'target_read': ['read_project', 'read_run'],
            'post_run_payload': ['create_run', 'update_run']}
_PACKET_KEYS = frozenset({'format', 'packet_id', 'prepared_at', 'expires_at',
    'region', 'data_scope', 'binding', 'target', 'session', 'operations', 'limits',
    'read_descriptor', 'target_read', 'post_run_payload', 'evidence_path',
    'privacy_policy', 'ambiguous_acceptance_policy', 'schema_mode',
    'deployed_schema_qualified', 'authorize_upload', 'authorize_remote_read'})
_PROPOSAL_KEYS = frozenset({'format', 'proposal_id', 'packet_id', 'action',
    'data_scope', 'binding', 'target', 'allowed_operations', 'run_ids', 'limits',
    'subject_digest', 'evidence_path', 'expires_at', 'authorize_upload',
    'authorize_remote_read', 'privacy_policy', 'ambiguous_acceptance_policy'})
_OPERATION_KEYS = frozenset({'operation_id', 'span_id', 'revision', 'operation',
    'event_digest', 'redaction_policy_digest', 'source_binding_alias', 'payload_sha256'})
_REVIEW_KEYS = frozenset({'format', 'packet_digest', 'binding', 'target',
    'expires_at', 'approved', 'reviewer_id', 'implementation_owner_id'})
_HANDLE_KEYS = frozenset({'path', 'sha256'})
_APPROVAL_NAMES = frozenset({'source_runtime_review', 'target_read', 'post_run_payload'})
_PROTECTED_NAMES = frozenset({'devbench', 'scripts', 'tests', 'docs',
    'devbench_engine', 'devharness_controlled', 'devharness_core',
    'devharness_server', '.git', '.aws', '.agents', '.codex', '.ssh', '.config'})
_PRIVATE_ROOTS = (Path('/private/tmp'), Path('/private/var/folders'))


def packet_digest(packet):
    """Integrity identifier only; not an approval or authenticated signature."""
    return digest(canonical_json_bytes(packet))


def connection_installed_identity():
    """Child-computed installed resources, plus this distinct fixture/criteria."""
    from .rna_hosted_qualification import bounded_installed_identity
    actual = bounded_installed_identity()
    identity = {key: actual[key] for key in ('source_digest', 'runtime_digest', 'policy_digest')}
    identity.update(criteria_digest=_CRITERIA_DIGEST, fixture_manifest_digest=_FIXTURE_DIGEST)
    _identity(identity)
    return identity


def _identity(value):
    _closed(value, IDENTITY_KEYS, 'connection installed identity')
    for item in value.values():
        _digest(item)
    if value['criteria_digest'] != _CRITERIA_DIGEST or value['fixture_manifest_digest'] != _FIXTURE_DIGEST:
        raise ValueError('Connection fixture/criteria identity mismatch')


def _binding(value):
    _closed(value, BINDING_KEYS, 'connection reviewed binding')
    for item in value.values():
        _digest(item)


def _caps(value):
    _closed(value, frozenset(CONNECTION_CAPS), 'immutable connection caps')
    if any(type(value[key]) is not int or value[key] != cap for key, cap in CONNECTION_CAPS.items()):
        raise ValueError('Immutable finite connection caps required')


def _path(value):
    if not isinstance(value, str) or not value or len(value) > 1024:
        raise ValueError('Bounded canonical assigned path required')
    path = Path(value)
    if not path.is_absolute() or '..' in path.parts or str(path) != value:
        raise ValueError('Canonical absolute path required')
    if not (path.is_relative_to(ASSIGNED_WORKSPACE) or any(path.is_relative_to(root) for root in _PRIVATE_ROOTS)):
        raise ValueError('Path outside assigned workspace or private test context')
    if any(part in _PROTECTED_NAMES or part.startswith('.venv') for part in path.parts):
        raise ValueError('Protected source or credential path prohibited')
    for component in (path, *path.parents):
        if component.is_symlink():
            raise ValueError('Symlink path prohibited')
        if (component / 'pyvenv.cfg').is_file():
            raise ValueError('Virtual environment path prohibited')
    prefix = Path(sys.prefix).resolve()
    if path.is_relative_to(prefix):
        raise ValueError('Runtime output/input overlap prohibited')
    return path


def _disjoint_approval_paths(path, approval_handles):
    handles = approval_handles.values() if isinstance(approval_handles, dict) else approval_handles
    for handle in handles:
        _closed(handle, _HANDLE_KEYS, 'trusted operator input handle')
        source = _path(handle['path'])
        if source.is_relative_to(path) or path.is_relative_to(source):
            raise ValueError('Output overlaps approval input')


def validate_output_path(value, *, approval_handles=()):
    """Require fresh output in the workspace/private test context, disjoint inputs.

    This check performs no creation. The engine must create exclusively and
    durably reserve its packet before resolving credentials. Subsequent effects
    use its internal admitted packet and separately recheck operator inputs.
    """
    path = _path(str(value))
    if path.exists() or path == ASSIGNED_WORKSPACE or path in _PRIVATE_ROOTS:
        raise ValueError('Fresh new-only connection output required')
    _disjoint_approval_paths(path, approval_handles)
    return path


def read_approval_handle(handle):
    """Read one trusted-launcher JSON input with bounded nofollow integrity checks.

    Accepting a file does not establish its independent review, operator origin,
    or human consent. The launcher must supply the hash through a trusted path.
    Arbitrary host paths are rejected before opening; file content never logs.
    """
    descriptor = None
    try:
        _closed(handle, _HANDLE_KEYS, 'trusted operator input handle')
        expected = _digest(handle['sha256'])
        path = _path(handle['path'])
        descriptor = os.open('/', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        for component in path.parts[1:-1]:
            next_descriptor = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                      dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_descriptor
        file_descriptor = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                                  dir_fd=descriptor)
        with os.fdopen(file_descriptor, 'rb') as stream:
            before = os.fstat(stream.fileno())
            if (not stat.S_ISREG(before.st_mode) or before.st_uid != os.getuid()
                    or stat.S_IMODE(before.st_mode) not in (0o400, 0o600)
                    or before.st_nlink != 1 or not 1 <= before.st_size <= 32768):
                raise ValueError('Private bounded regular input required')
            raw = stream.read(32769)
            after = os.fstat(stream.fileno())
        fields = ('st_dev', 'st_ino', 'st_mode', 'st_uid', 'st_nlink', 'st_size', 'st_mtime_ns', 'st_ctime_ns')
        if (len(raw) > 32768 or any(getattr(before, key) != getattr(after, key) for key in fields)
                or digest(raw) != expected):
            raise ValueError('Operator input integrity mismatch')
        def unique(pairs):
            result = {}
            for key, item in pairs:
                if key in result:
                    raise ValueError('Duplicate operator input field')
                result[key] = item
            return result
        def nonfinite(_):
            raise ValueError('Nonfinite operator input')
        record = json.loads(raw, object_pairs_hook=unique, parse_constant=nonfinite)
        canonical_json_bytes(record)
        if not isinstance(record, dict):
            raise ValueError('Operator input object required')
        return record
    except Exception:
        raise ValueError('Invalid private operator input') from None
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _session_operations(packet, identity, now):
    from .rna_langsmith_live import validate_live_session, validate_live_run, _root_dotted_order
    from .rna_langsmith_verify import validate_remote_proof
    session = packet['session']
    if not isinstance(session, dict) or session.get('format') != 'devbench.rna-live-trace-session/2' or session.get('authorize_upload') is not False:
        raise ValueError('Disabled prepared /2 session required')
    # Inspect existing structural rules without making the prepared session an
    # executable authority. The admitted child derives a separate execution hash.
    validate_live_session({**session, 'authorize_upload': True})
    if (session['data_scope'] != 'synthetic' or session['expires_at'] != packet['expires_at']
            or any(session[key] != identity[key] for key in ('source_digest', 'runtime_digest', 'policy_digest'))
            or session['endpoint'] != TARGET['endpoint'] or session['project'] != TARGET['project_name']
            or session['project_id'] != TARGET['project_id'] or session['workspace_id'] != TARGET['workspace_id']
            or any(session[key] != CONNECTION_CAPS[key] for key in ('max_spans', 'max_bytes', 'max_delivery_attempts'))):
        raise ValueError('Exact prepared session binding required')
    operations = packet['operations']
    if not isinstance(operations, list) or len(operations) != 2 or len(canonical_json_bytes(operations)) > CONNECTION_CAPS['max_bytes']:
        raise ValueError('One bounded root start and finish required')
    root, start_time, seen_ids, seen_events = None, None, set(), set()
    for revision, item in enumerate(operations):
        _closed(item, {'operation', 'payload'}, 'connection operation')
        envelope, payload = item['operation'], item['payload']
        _closed(envelope, _OPERATION_KEYS, 'bound operation envelope')
        operation = ('start', 'finish')[revision]
        if envelope['operation'] != operation or type(envelope['revision']) is not int or envelope['revision'] != revision:
            raise ValueError('Exact start/finish revisions required')
        for key in ('operation_id', 'span_id', 'source_binding_alias'):
            _uuid(envelope[key])
        for key in ('event_digest', 'redaction_policy_digest', 'payload_sha256'):
            _digest(envelope[key])
        validate_live_run(payload, operation)
        proof = validate_remote_proof(payload)
        metadata = payload['extra']['metadata']
        root = root or _uuid(payload['id'])
        start_time = start_time or payload['start_time']
        if (payload['id'] != root or payload['trace_id'] != root or payload.get('parent_run_id') is not None
                or payload.get('dotted_order') != _root_dotted_order(start_time, root)
                or payload['session_id'] != TARGET['project_id'] or payload['session_name'] != TARGET['project_name']
                or payload['inputs'] != {} or payload['outputs'] != {}
                or payload['start_time'] != start_time or envelope['span_id'] != root
                or envelope['source_binding_alias'] != session['workflow_alias']
                or envelope['redaction_policy_digest'] != identity['policy_digest']
                or envelope['payload_sha256'] != packet_digest(payload)
                or proof['operation_id'] != envelope['operation_id'] or proof['revision'] != revision
                or any(metadata[key] != identity[key] for key in ('source_digest', 'runtime_digest'))
                or type(metadata['sequence']) is not int or metadata['sequence'] != revision
                or metadata['allocation'] != 0 or metadata['actual_children'] != 0
                or envelope['operation_id'] in seen_ids or envelope['event_digest'] in seen_events):
            raise ValueError('Exact synthetic root operation binding required')
        for stamp in ('start_time', 'end_time'):
            if stamp in payload and _time(payload[stamp]) > _time(packet['expires_at']):
                raise ValueError('Operation time outlives frozen session')
        seen_ids.add(envelope['operation_id'])
        seen_events.add(envelope['event_digest'])
    return root


def _read_descriptor(root):
    return dict(format='devbench.rna-connection-read/2', run_id=root,
        project_route='/api/v1/sessions/' + TARGET['project_id'] + '?include_stats=false',
        run_route='/api/v1/runs/' + root, max_project_age_seconds=30,
        recovery_policy='exact_proof_then_separate_reviewed_reconciliation')


def _proposal(packet, action, proposal_id):
    subject = packet['read_descriptor'] if action == 'target_read' else packet['operations']
    return dict(format='devbench.rna-hosted-action-proposal/2', proposal_id=proposal_id,
        packet_id=packet['packet_id'], action=action, data_scope='synthetic',
        binding=_copy(packet['binding']), target=_copy(packet['target']),
        allowed_operations=_ACTIONS[action].copy(),
        run_ids=[packet['operations'][0]['payload']['id']], limits=_copy(packet['limits']),
        subject_digest=packet_digest(subject), evidence_path=packet['evidence_path'],
        expires_at=packet['expires_at'], authorize_upload=False, authorize_remote_read=False,
        privacy_policy='rna-allowlist/1', ambiguous_acceptance_policy=_AMBIGUITY)


def prepare_connection_packet(*, installed_identity, target, session, operations,
                              expires_at, evidence_path, now):
    """Prepare a disabled inspectable packet without files, network or secrets."""
    _identity(installed_identity)
    _expiry(expires_at, now)
    root = operations[0]['payload']['id'] if isinstance(operations, list) and operations else ''
    binding = {**installed_identity, 'session_digest': packet_digest(session),
               'payload_digest': packet_digest(operations)}
    packet = dict(format='devbench.rna-hosted-connection/2', packet_id=str(uuid4()),
        prepared_at=now.isoformat(), expires_at=expires_at, region='us', data_scope='synthetic',
        binding=_copy(binding), target=_copy(target), session=_copy(session), operations=_copy(operations),
        limits=_copy(CONNECTION_CAPS), read_descriptor=_read_descriptor(root),
        evidence_path=str(evidence_path), privacy_policy='rna-allowlist/1',
        ambiguous_acceptance_policy=_AMBIGUITY, schema_mode=_MODE,
        deployed_schema_qualified=False, authorize_upload=False, authorize_remote_read=False)
    for action in _ACTIONS:
        packet[action] = _proposal(packet, action, str(uuid4()))
    return validate_connection_packet(packet, installed_identity=installed_identity,
        expected_binding=binding, expected_target=target, now=now)


def _packet_structure(packet, *, now, fresh_output):
    """Shared privacy/consistency checks, without attesting installation identity."""
    try:
        _closed(packet, _PACKET_KEYS, 'connection packet')
        if (packet['format'] != 'devbench.rna-hosted-connection/2' or packet['region'] != 'us'
                or packet['data_scope'] != 'synthetic' or packet['schema_mode'] != _MODE
                or packet['deployed_schema_qualified'] is not False
                or packet['authorize_upload'] is not False or packet['authorize_remote_read'] is not False
                or packet['privacy_policy'] != 'rna-allowlist/1'
                or packet['ambiguous_acceptance_policy'] != _AMBIGUITY):
            raise ValueError('Disabled experimental synthetic connection required')
        _uuid(packet['packet_id'])
        _binding(packet['binding'])
        declared_identity = {key: packet['binding'][key] for key in IDENTITY_KEYS}
        _identity(declared_identity)
        _target(packet['target'])
        if packet['target'] != TARGET:
            raise ValueError('Exact existing connection target required')
        expires = _expiry(packet['expires_at'], now)
        prepared = _time(packet['prepared_at'])
        if prepared is None or prepared > now or not 0 < (expires - prepared).total_seconds() <= 1800:
            raise ValueError('Invalid frozen preparation/expiry interval')
        _caps(packet['limits'])
        if fresh_output:
            validate_output_path(packet['evidence_path'])
        else:
            _path(packet['evidence_path'])
        root = _session_operations(packet, declared_identity, now)
        if (packet['binding']['session_digest'] != packet_digest(packet['session'])
                or packet['binding']['payload_digest'] != packet_digest(packet['operations'])
                or packet['read_descriptor'] != _read_descriptor(root)):
            raise ValueError('Session/payload/read binding mismatch')
        proposal_ids = set()
        for action in _ACTIONS:
            proposal = packet[action]
            _closed(proposal, _PROPOSAL_KEYS, 'disabled /2 action proposal')
            proposal_id = _uuid(proposal['proposal_id'])
            _caps(proposal['limits'])
            if (proposal['authorize_upload'] is not False or proposal['authorize_remote_read'] is not False
                    or proposal_id in proposal_ids or proposal_id == packet['packet_id']
                    or proposal != _proposal(packet, action, proposal_id)):
                raise ValueError('Exact distinct disabled action proposal required')
            proposal_ids.add(proposal_id)
        if len(canonical_json_bytes(packet)) > 65536:
            raise ValueError('Connection packet ceiling exceeded')
        return _copy(packet)
    except Exception:
        raise ValueError('Invalid closed connection packet') from None


def validate_connection_wire_packet(packet, *, now):
    """Reject secret/unknown wire fields before IPC, without granting admission.

    Declared digest consistency is not installed-source or operator verification.
    The child must independently validate identities and operator inputs before
    effects. Existing destinations remain inspectable for a zero-effect replay
    rejection; they can never satisfy fresh initial admission through this helper.
    """
    return _packet_structure(packet, now=now, fresh_output=False)


def validate_connection_packet(packet, *, installed_identity, expected_binding,
                               expected_target, now):
    """Compare against child-computed installation and separately reviewed inputs."""
    try:
        _closed(packet, _PACKET_KEYS, 'connection packet')
        _identity(installed_identity)
        _binding(expected_binding)
        _binding(packet['binding'])
        _target(expected_target)
        if (packet['binding'] != expected_binding
                or any(packet['binding'][key] != installed_identity[key] for key in IDENTITY_KEYS)
                or packet['target'] != expected_target or expected_target != TARGET):
            raise ValueError('Independent installed/reviewed identity/target mismatch')
        return _packet_structure(packet, now=now, fresh_output=True)
    except Exception:
        raise ValueError('Invalid closed connection packet') from None


def _review(record, packet, expected_binding, expected_target, now):
    _closed(record, _REVIEW_KEYS, 'independent connection review input')
    if (record['format'] != 'devbench.rna-connection-review/2' or record['approved'] is not True
            or record['packet_digest'] != packet_digest(packet) or record['binding'] != expected_binding
            or record['target'] != expected_target or record['expires_at'] != packet['expires_at']):
        raise ValueError('Independent exact packet review required')
    _digest(record['packet_digest'])
    _uuid(record['reviewer_id'])
    _uuid(record['implementation_owner_id'])
    _expiry(record['expires_at'], now)
    if record['reviewer_id'] == record['implementation_owner_id']:
        raise PermissionError('Implementation owner cannot self-review')


def _approval(record, proposal, owner, now):
    # The same integrity rules as existing operator records, explicitly /2;
    # /1 read and post-run scopes are not projected into broader operations.
    _closed(record, APPROVAL_KEYS, 'separate /2 operator input')
    if (record['format'] != 'devbench.rna-hosted-action-approval/2' or record['approved'] is not True
            or record['proposal_digest'] != packet_digest(proposal) or record['action'] != proposal['action']
            or record['expires_at'] != proposal['expires_at'] or record['implementation_owner_id'] != owner):
        raise ValueError('Exact separately approved /2 action required')
    _digest(record['proposal_digest'])
    _uuid(record['operator_id'])
    _uuid(record['implementation_owner_id'])
    _expiry(record['expires_at'], now)
    if record['operator_id'] == record['implementation_owner_id']:
        raise PermissionError('Implementation owner cannot self-approve')


def admit_connection_packet(packet, *, installed_identity, expected_binding,
                            expected_target, now, approval_handles, consumed_packet_ids=()):
    """Admit only trusted-launcher separately supplied exact-action inputs.

    This does not consume authority. The engine must durably reserve the packet
    before key resolution, and enforce the frozen deadlines and budgets. An
    ambiguous send remains uncertain; admission never grants an automatic resend.
    """
    packet = validate_connection_packet(packet, installed_identity=installed_identity,
        expected_binding=expected_binding, expected_target=expected_target, now=now)
    if not isinstance(consumed_packet_ids, (list, tuple, set, frozenset)):
        raise ValueError('Consumed packet identity set required')
    for identity in consumed_packet_ids:
        _uuid(identity)
    if len(set(consumed_packet_ids)) != len(consumed_packet_ids) or packet['packet_id'] in consumed_packet_ids:
        raise PermissionError('Connection packet replay prohibited')
    result = dict(format='devbench.rna-connection-admission/2', status='blocked',
        packet_digest=packet_digest(packet), blockers=['trusted_operator_approval_handles_absent'],
        approval_record_digests={}, authorize_upload=False, authorize_remote_read=False,
        schema_mode=_MODE, deployed_schema_qualified=False)
    if approval_handles == {}:
        return result
    approval_digests = validate_connection_approvals(packet, expected_binding=expected_binding,
        expected_target=expected_target, now=now, approval_handles=approval_handles)
    result.update(status='admitted', blockers=[], authorize_upload=True, authorize_remote_read=True,
        approval_record_digests=approval_digests)
    return result


def validate_connection_approvals(packet, *, expected_binding, expected_target, now,
                                  approval_handles):
    """Recheck exact trusted operator inputs for an internally admitted packet.

    This is not initial packet admission and never grants authority. The reviewed
    engine retains its immutable packet and checks deadline/caps/receipts and its
    owned reserved root separately, without pretending that root is still fresh.
    """
    _binding(expected_binding)
    _target(expected_target)
    if (packet['binding'] != expected_binding or packet['target'] != expected_target
            or expected_target != TARGET):
        raise ValueError('Independent reviewed connection binding required')
    _expiry(packet['expires_at'], now)
    _closed(approval_handles, _APPROVAL_NAMES, 'separate trusted operator handles')
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
