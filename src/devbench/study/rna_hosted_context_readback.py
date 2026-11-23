"""One current GET-only observation of independently sealed historical /5 runs.

Original packets, sessions, journals and failures are read-only evidence. Fresh
private review files and the launcher's independently retained seal root admit
only this reader. Local hashes and actor labels do not authenticate a human or
qualify a native worker, scientific method, clinical release, or original SLA.
"""
from copy import deepcopy
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import signal
import sqlite3
import ssl
import stat
import time
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener
from uuid import uuid4

from .contracts import canonical_json_bytes
from .langsmith_export import _NoRedirect
from .langsmith_outbox import _digest, _uuid, digest
from .rna_hosted_qualification import _absolute
from .rna_hosted_context_readback_contract import (
    admit_context_readback_packet, packet_digest, read_approval_handle,
    readback_installed_identity, validate_context_readback_approvals,
    validate_context_readback_wire_packet)
from .rna_hosted_workflow import _ContextHTTPSHandler, _read_context_http_body

DESCRIPTOR_FORMAT = 'devbench.rna-hosted-context-readback-session/1'
STATUS_FORMAT = 'devbench.rna-hosted-context-readback-status/1'
VERIFICATION_FORMAT = 'devbench.rna-hosted-context-readback-verification/1'
_FLAGS = dict(original_failure_preserved=True, original_visibility_sla_recovered=False,
    settled_old_outbox=False, full_route_qualified=False, science_qualified=False)
_BLOCKERS = frozenset({'trusted_operator_approval_handles_absent','packet_already_consumed',
    'budget_exhausted','body_read_uncertain','auth_denied','input_drift','closed',
    'verification_already_attempted','initialization_failed','transport_timeout','transport_unavailable'})
_STAT = ('st_dev','st_ino','st_size','st_mtime_ns','st_ctime_ns','st_mode','st_nlink','st_uid')
_ARTIFACT_BYTES, _TOTAL_HISTORY_BYTES, _MAX_HISTORY_REFS = 8 * 1024 * 1024, 64 * 1024 * 1024, 512


def _json(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('Duplicate evidence key')
            result[key] = value
        return result
    def reject(_):
        raise ValueError('Finite evidence JSON required')
    value = json.loads(raw, object_pairs_hook=unique, parse_constant=reject)
    canonical_json_bytes(value)
    return value


def _directory(path):
    """Anchor each parent component without following a replaced symlink."""
    path = _absolute(Path(path))
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    directory = os.open(path.anchor, flags)
    try:
        for part in path.parts[1:]:
            child = os.open(part, flags, dir_fd=directory)
            os.close(directory)
            directory = child
        return directory
    except BaseException:
        os.close(directory)
        raise


def _read(path, expected, *, cap=_ARTIFACT_BYTES):
    path = _absolute(Path(path)); _digest(expected)
    parent = _directory(path.parent)
    try:
        descriptor = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        with os.fdopen(descriptor, 'rb') as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or not 0 <= before.st_size <= cap:
                raise ValueError('Bounded single-link historical file required')
            raw = stream.read(cap + 1)
            after = os.fstat(stream.fileno())
            current = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
            if (len(raw) != before.st_size or digest(raw) != expected
                    or any(getattr(before, key) != getattr(after, key)
                        or getattr(before, key) != getattr(current, key) for key in _STAT)):
                raise ValueError('Immutable historical file required')
            return raw, {key: getattr(before, key) for key in _STAT}
    finally:
        os.close(parent)


@contextmanager
def _readonly_db(path):
    # No ExportOutbox/observer constructor is used: both may own write paths.
    path = _absolute(Path(path))
    for suffix in ('-wal','-shm','-journal'):
        if os.path.lexists(str(path) + suffix):
            raise ValueError('Historical SQLite companions must be absent')
    db = sqlite3.connect(path.as_uri() + '?mode=ro&immutable=1', uri=True, timeout=0)
    try:
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA query_only=ON')
        db.execute('PRAGMA trusted_schema=OFF')
        yield db
    finally:
        db.close()


def validate_context_readback_descriptor(value):
    try:
        if (type(value) is not dict or set(value) != {'format','packet','approval_handles','expected_historical_root'}
                or value['format'] != DESCRIPTOR_FORMAT or len(canonical_json_bytes(value)) > 196608):
            raise ValueError()
        _digest(value['expected_historical_root'])
        validate_context_readback_wire_packet(value['packet'], fresh_output=False)
        handles = value['approval_handles']
        if type(handles) is not dict or set(handles) not in (set(), {'source_runtime_review','readback'}):
            raise ValueError()
        for handle in handles.values():
            if type(handle) is not dict or set(handle) != {'path','sha256'}:
                raise ValueError()
            _absolute(Path(handle['path'])); _digest(handle['sha256'])
        return _json(canonical_json_bytes(value))
    except Exception:
        raise ValueError('Closed GET-only readback descriptor required') from None


def _source_graph(source, source_reference, session, sealed, read_member):
    """Join canonical observer/sidecar receipts, independent of checkpoint labels.

    The historical checkpoint is retained and hash checked by the outer seal
    join. Its opaque contents grant no authority and need not be relabeled.
    """
    from .rna_langsmith_live import project_context_receipt
    if not source.startswith(b'SQLite format 3\x00'):
        raise ValueError('Original sealed observer SQLite source required')
    from devbench.analysis.rna_trace_contract import validate_trace_context, validate_trace_event
    identities = {key: session[key] for key in ('source_digest','runtime_digest','policy_digest')}
    observer_path = source_reference['path']
    read_member(observer_path, source_reference['sha256'])
    with _readonly_db(observer_path) as db:
        settings = db.execute('SELECT value FROM settings LIMIT 2').fetchall()
        events = [validate_trace_event(_json(row[0])) for row in db.execute(
            'SELECT event FROM events WHERE workflow=? ORDER BY rowid LIMIT 65', (session['workflow_binding_digest'],))]
        traces = {event['trace_id'] for event in events}
        if len(traces) != 1:
            raise ValueError('One exact retained source workflow required')
        contexts = [validate_trace_context(_json(row[0])) for row in db.execute(
            "SELECT context FROM contexts WHERE json_extract(context,'$.trace_id')=? LIMIT 17", (next(iter(traces)),))]
    profile = session['context_profile']
    if (len(settings) != 1 or _json(settings[0][0])['identities'] != identities
            or len(contexts) != profile['context_count'] or len(events) != profile['source_event_count']):
        raise ValueError('Complete original observer/profile correspondence required')
    actual = {row['span_id']:row for row in contexts}
    if len(actual) != len(contexts):
        raise ValueError('Distinct original context identities required')
    raw_slots, matched = {}, set()
    for slot, approved in zip(session['span_topology'], profile['contexts']):
        parent = slot['parent_span_id']
        raw_parent = raw_slots[parent]['span_id'] if parent else None
        candidates = []
        for context in contexts:
            if (context['span_id'] in matched or context['parent_span_id'] != raw_parent
                    or any(context[key] != slot[key] for key in ('role','phase','attempt'))):
                continue
            retained = [event for event in events if event['span_id'] == context['span_id']]
            projected = [project_context_receipt(event, context_alias=approved['context_alias'],
                parent_context_alias=approved['parent_context_alias']) for event in retained]
            if [packet_digest(event) for event in projected] == approved['source_event_digests']:
                candidates.append((context, retained))
        if len(candidates) != 1:
            raise ValueError('One parent-qualified original context/source proof required')
        context, retained = candidates[0]
        raw_slots[slot['span_id']] = context
        matched.add(context['span_id'])
        if any(context[key] != value for key, value in identities.items()):
            raise ValueError('Historical context producer mismatch')
        for event in retained:
            if (any(event['bindings'][name] != session[key] for name,key in
                    (('source','source_digest'),('runtime','runtime_digest'),('policy','policy_digest')))
                    or event['bindings']['workflow'] != session['workflow_binding_digest']):
                raise ValueError('Original source event binding mismatch')
    if matched != set(actual):
        raise ValueError('One-to-one original context/source correspondence required')
    sidecars = Path(observer_path).with_name(Path(observer_path).name + '.sidecars')
    sealed_sidecars = {path for path in sealed if Path(path).parent == sidecars}
    directory = _directory(sidecars)
    try:
        current_sidecars = {str(sidecars / name) for name in os.listdir(directory)}
    finally:
        os.close(directory)
    if current_sidecars != sealed_sidecars:
        raise ValueError('Complete immutable observer sidecar inventory required')
    for context in contexts:
        path = str(sidecars / (context['span_id'] + '.json'))
        if path not in sealed_sidecars:
            continue  # Capture-only contexts have no invocation sidecar.
        item = _json(read_member(path, sealed[path]['sha256']))
        if (type(item) is not dict or set(item) != {'context','workflow_digest','observer_path'}
                or validate_trace_context(item['context']) != context
                or item['workflow_digest'] != session['workflow_binding_digest']
                or item['observer_path'] != observer_path):
            raise ValueError('Original invocation sidecar/source correspondence required')


def _seal_inventory(seal):
    """Closed neutral seal, plus the explicitly retained legacy /1 adapter."""
    neutral = {'format','status','original_api_exact_revision_count','original_api_total_count',
        'original_status_values_retained','artifacts'}
    legacy = neutral | {'actual_HTTP','actual_UI','actual_outbox','actual_outcome','actual_readback',
        'artifact_count','artifact_read_byte_ceiling','custody','effects','historical_limits','holds',
        'narrative','observation_dates_are_retained_not_remeasured','original_source_manifest',
        'postprocessed_at_utc','private_approved_action_handles','roles','scope','seal_limit',
        'seal_started_at_utc','sealed_at_utc','software_index'}
    if type(seal) is not dict or seal.get('status') not in {'failed','FAILED'}:
        raise ValueError('Original failed history seal required')
    version = seal.get('format')
    if version == 'devbench.rna-context-history-seal/1':
        if set(seal) != neutral:
            raise ValueError('Closed neutral history seal required')
        exact, count = seal['original_api_exact_revision_count'], seal['original_api_total_count']
        if (type(exact) is not int or type(count) is not int or not 0 <= exact <= count <= 16
                or type(seal['original_status_values_retained']) is not bool):
            raise ValueError('Bounded declared historical observations required')
    elif version == 'devbench.rna-nonmodel-v3-attempt-failure-seal/1':
        if not {'format','status','artifacts'} <= set(seal) or not set(seal) <= legacy:
            raise ValueError('Known legacy history seal adapter required')
    else:
        raise ValueError('Unsupported historical seal version')
    artifacts = seal['artifacts']
    if type(artifacts) is not list or not 1 <= len(artifacts) <= _MAX_HISTORY_REFS:
        raise ValueError('Bounded complete history seal inventory required')
    if 'artifact_count' in seal and (type(seal['artifact_count']) is not int or seal['artifact_count'] != len(artifacts)):
        raise ValueError('Complete declared legacy inventory required')
    return artifacts


def _historical_join(packet, expected_root):
    """Derive producer facts only after independently rooted artifact custody."""
    from .rna_langsmith_live import validate_historical_context_session, validate_historical_context_run_bindings
    history = packet['history']
    root_reference = history['failure_seal']
    if root_reference['sha256'] != expected_root:
        raise PermissionError('Independent historical root mismatch')
    seal_raw, _ = _read(root_reference['path'], expected_root, cap=524288)
    seal = _json(seal_raw)
    artifacts = _seal_inventory(seal)
    sealed, total = {}, 0
    for reference in artifacts:
        if type(reference) is not dict or not {'path','sha256','bytes'} <= set(reference):
            raise ValueError('Closed sealed member identity required')
        path = str(_absolute(Path(reference['path']))); _digest(reference['sha256'])
        size = reference['bytes']
        if path in sealed or type(size) is not int or not 0 <= size <= _ARTIFACT_BYTES:
            raise ValueError('Distinct bounded sealed artifacts required')
        total += size
        if total > _TOTAL_HISTORY_BYTES:
            raise ValueError('Complete history exceeds supported byte ceiling')
        sealed[path] = reference
    # Establish full root membership before reading any historical member.
    for name, reference in history.items():
        if name in {'root','failure_seal'}:
            continue
        if sealed.get(reference['path'], {}).get('sha256') != reference['sha256']:
            raise ValueError('Historical member absent from independent root')
    snapshots = {}
    def read_member(path, expected):
        reference = sealed.get(path)
        if reference is None or reference['sha256'] != expected:
            raise ValueError('Historical graph member outside independent root')
        raw, info = _read(path, expected)
        if len(raw) != reference['bytes']:
            raise ValueError('Historical member size mismatch')
        if 'stat' in reference and any(reference['stat'].get(key) != info[key] for key in _STAT):
            raise ValueError('Original sealed file metadata changed')
        snapshots[path] = info
        return raw
    # Unsupported larger histories fail; no partial custody claim is issued.
    for path, reference in sealed.items():
        read_member(path, reference['sha256'])
    documents = {name: read_member(ref['path'], ref['sha256']) for name, ref in history.items()
        if name not in {'root','failure_seal'}}
    original = _json(documents['packet'])
    with _readonly_db(history['outbox']['path']) as db:
        stored_sessions = db.execute('SELECT manifest,manifest_sha256 FROM live_session LIMIT 2').fetchall()
        stored_ops = db.execute('SELECT * FROM operations LIMIT 33').fetchall()
        exports = db.execute('SELECT * FROM exports LIMIT 2').fetchall()
        attempts = db.execute('SELECT operation_id,outcome,receipt_digest FROM attempts LIMIT 33').fetchall()
    if len(stored_sessions) != 1:
        raise ValueError('One immutable original outbox session required')
    session = validate_historical_context_session(_json(stored_sessions[0]['manifest']))
    if (stored_sessions[0]['manifest'] != canonical_json_bytes(session)
            or stored_sessions[0]['manifest_sha256'] != packet_digest(session)):
        raise ValueError('Canonical original outbox manifest and digest required')
    if (session['authorize_upload'] is not True or session != packet['session']
            or original.get('format') != 'devbench.rna-hosted-context/5'
            or original.get('authorize_upload') is not False or original.get('authorize_remote_read') is not False
            or original['session'] != {**session, 'authorize_upload':False}
            or original['topology'] != packet['topology'] or original['profile'] != session['context_profile']
            or original['target'] != packet['target']):
        raise ValueError('Original disabled packet and separately active retained session required')
    operations = [_json(line) for line in documents['operations'].splitlines() if line]
    if len(operations) != session['context_profile']['operation_count'] or len(operations) > 32:
        raise ValueError('Complete bounded original operation stream required')
    starts, finishes, by_operation = {}, {}, {}
    for item in operations:
        if type(item) is not dict or set(item) != {'operation','payload'}:
            raise ValueError('Closed original operation stream required')
        op, run = item['operation'], item['payload']
        validate_historical_context_run_bindings(run, op, session)
        if op['operation_id'] in by_operation:
            raise ValueError('Duplicate historical operation')
        by_operation[op['operation_id']] = item
        parent = run.get('parent_run_id')
        if parent is not None and (parent not in starts or not run['dotted_order'].startswith(starts[parent]['dotted_order'] + '.')):
            raise ValueError('Retained original parent start required')
        if op['operation'] == 'start':
            if run['id'] in starts:
                raise ValueError('Duplicate original start')
            starts[run['id']] = run
        else:
            if run['id'] not in starts or run['id'] in finishes:
                raise ValueError('One original terminal revision required')
            if any(run.get(key) != starts[run['id']].get(key) for key in ('start_time','dotted_order','trace_id','parent_run_id')):
                raise ValueError('Original terminal identity drift')
            finishes[run['id']] = item
    if (set(starts) != set(packet['read_descriptor']['run_ids'])
            or set(finishes) != set(session['context_profile']['terminal_span_ids'])):
        raise ValueError('Complete original topology and explicit terminal set required')
    if (len(stored_ops) != len(operations) or len(exports) != 1 or len(attempts) > len(operations)
            or any(row['operation_id'] not in by_operation for row in attempts)):
        raise ValueError('Complete original outbox operation/attempt inventory required')
    latest, acknowledged_starts, seen = {}, set(), set()
    for row in stored_ops:
        item = dict(operation=_json(row['envelope']), payload=_json(row['payload']))
        if (row['operation_id'] in seen or by_operation.get(row['operation_id']) != item
                or row['payload_sha256'] != packet_digest(item['payload'])
                or row['span_id'] != item['payload']['id'] or row['revision'] != item['operation']['revision']
                or row['operation'] != item['operation']['operation']):
            raise ValueError('Exact full original operation stream required')
        seen.add(row['operation_id'])
        matching = [attempt for attempt in attempts if attempt['operation_id'] == row['operation_id']]
        if (type(row['attempt_count']) is not int or row['attempt_count'] not in (0,1)
                or len(matching) != row['attempt_count']):
            raise ValueError('Unambiguous original delivery attempt inventory required')
        if row['state'] != 'acknowledged':
            if (row['state'] not in {'prepared','sending','retryable','uncertain','rejected','blocked','abandoned'}
                    or any(attempt['outcome'] == 'acknowledged' for attempt in matching)):
                raise ValueError('Ambiguous original acknowledgement refused')
            continue  # Preserve later unacknowledged rows; never settle or compare them.
        if row['attempt_count'] != 1 or matching[0]['outcome'] != 'acknowledged':
            raise ValueError('Original acknowledged attempt proof required')
        _digest(matching[0]['receipt_digest'])
        if row['operation'] == 'start':
            acknowledged_starts.add(row['span_id'])
        previous = latest.get(row['span_id'])
        if previous is None or item['operation']['revision'] > previous['operation']['revision']:
            latest[row['span_id']] = item
    if seen != set(by_operation) or acknowledged_starts != set(starts) or set(latest) != set(starts):
        raise ValueError('One independently acknowledged original start per span required')
    export = exports[0]
    seed = _json(read_member(export['payload_path'], export['payload_sha256']))
    if (seed.get('runs') != [starts[packet['topology'][0]['span_id']]]
            or seed.get('data_scope') != 'synthetic' or export['max_attempts'] != 1
            or export['endpoint'] != session['endpoint'] or export['project'] != session['project']):
        raise ValueError('Original sealed outbox source payload mismatch')
    for action in ('source_runtime_review','target_read','live_session'):
        record = _json(documents[action])
        if (record.get('approved') is not True or record.get('expires_at') != original['expires_at']
                or record.get('implementation_owner_id') in {record.get('reviewer_id'),record.get('operator_id')}):
            raise ValueError('Original historical approvals must match original scope')
        if action == 'source_runtime_review':
            if (record.get('format') != 'devbench.rna-context-review/5'
                    or record.get('packet_digest') != history['packet']['sha256']
                    or record.get('binding') != original['binding'] or record.get('target') != original['target']):
                raise ValueError('Original review binding mismatch')
        elif (record.get('format') != 'devbench.rna-hosted-action-approval/5'
                or record.get('action') != action or record.get('proposal_digest') != packet_digest(original[action])):
            raise ValueError('Original historical action approval mismatch')
    _source_graph(documents['source'], history['source'], session, sealed, read_member)
    latest_items = [latest[slot['span_id']] for slot in packet['topology']]
    producer = {key:session[key] for key in ('source_digest','runtime_digest','policy_digest')}
    producer.update(packet_digest=digest(documents['packet']), session_digest=packet_digest(session),
        topology_digest=packet_digest(packet['topology']), operations_digest=digest(documents['operations']),
        latest_acknowledged_digest=packet_digest(latest_items), failure_seal_digest=expected_root)
    if producer != packet['producer_binding']:
        raise ValueError('Independently derived historical producer mismatch')
    # SQLite and every sealed member remain byte/stat identical after inspection.
    for path, reference in sealed.items():
        read_member(path, reference['sha256'])
    historical_action = dict(action_digest=packet_digest(original['target_read']),
        max_age_seconds=original['read_descriptor']['max_project_age_seconds'])
    return producer, latest_items, starts, snapshots, historical_action


def validate_context_readback_status(value):
    keys = {'format','status','blockers','credential_resolutions','authorize_upload','authorize_remote_read',
        'schema_mode','deployed_schema_qualified','packet_digest','counts'}
    if (type(value) is not dict or set(value) != keys or value['format'] != STATUS_FORMAT
            or value['status'] not in {'admitted','blocked'}
            or value['authorize_upload'] is not False or value['deployed_schema_qualified'] is not False
            or value['schema_mode'] != 'experimental_historical_context_readback'
            or type(value['authorize_remote_read']) is not bool
            or value['authorize_remote_read'] != (value['status'] == 'admitted')
            or type(value['credential_resolutions']) is not int or value['credential_resolutions'] not in (0,1)
            or type(value['blockers']) is not list or any(item not in _BLOCKERS for item in value['blockers'])):
        raise ValueError('Closed readback status required')
    _digest(value['packet_digest'])
    if type(value['counts']) is not dict or set(value['counts']) != {'read_requests','response_bytes'}:
        raise ValueError('Closed readback counts required')
    for key, cap in (('read_requests',17),('response_bytes',131072)):
        if type(value['counts'][key]) is not int or not 0 <= value['counts'][key] <= cap:
            raise ValueError('Bounded readback counts required')
    return _json(canonical_json_bytes(value))


def validate_context_readback_verification(value):
    from .rna_hosted_context_readback_contract import _identity, _producer
    from .rna_langsmith_verify import validate_context_response_diagnostic
    keys = {'format','packet_digest','verification_result','verified_span_count','receipts','diagnostics',
        'current_reader_binding','historical_producer_binding', *_FLAGS}
    if (type(value) is not dict or set(value) != keys or value['format'] != VERIFICATION_FORMAT
            or value['verification_result'] not in {'exact_context_match','context_mismatch'}
            or any(value[key] is not item for key, item in _FLAGS.items())):
        raise ValueError('Closed current readback verification required')
    _digest(value['packet_digest']); _identity(value['current_reader_binding']); _producer(value['historical_producer_binding'])
    receipts, diagnostics = value['receipts'], value['diagnostics']
    if type(receipts) is not list or not 1 <= len(receipts) <= 16 or type(diagnostics) is not list or len(diagnostics) != len(receipts):
        raise ValueError('One bounded receipt/diagnostic per original span required')
    seen, exact = set(), 0
    for row, diagnostic in zip(receipts, diagnostics):
        if type(row) is not dict or set(row) != {'span_id','revision','operation_id','expected_payload_sha256','verification_result'}:
            raise ValueError('Closed current readback receipt required')
        _uuid(row['span_id']); _uuid(row['operation_id']); _digest(row['expected_payload_sha256'])
        if row['span_id'] in seen or type(row['revision']) is not int or row['revision'] not in (0,1) or row['verification_result'] not in {'exact_revision_match','state_match_only','mismatch','not_visible','invalid_response'}:
            raise ValueError('Distinct bounded current readback observations required')
        seen.add(row['span_id']); exact += row['verification_result'] == 'exact_revision_match'
        if type(diagnostic) is not dict or set(diagnostic) != {'span_id','body_length','body_sha256','mismatch_category','actual_provider_status'}:
            raise ValueError('Closed current readback diagnostic required')
        if diagnostic['actual_provider_status'] not in {'pending','success','error','missing','unrecognized'} or diagnostic['span_id'] != row['span_id']:
            raise ValueError('Closed provider status required')
        validate_context_response_diagnostic({key:item for key, item in diagnostic.items() if key != 'actual_provider_status'})
    if type(value['verified_span_count']) is not int or value['verified_span_count'] != exact or (value['verification_result'] == 'exact_context_match') != (exact == len(receipts)):
        raise ValueError('Readback verification count mismatch')
    return _json(canonical_json_bytes(value))


class HostedContextReadback:
    """Child-owned one-use authority with fresh append-only effect evidence."""
    def __init__(self, descriptor):
        from . import rna_hosted_exporter as exporter
        if exporter._CHILD_PROCESS_PID != os.getpid():
            raise PermissionError('Original exporter child required')
        descriptor = validate_context_readback_descriptor(descriptor)
        self.packet, self.handles = descriptor['packet'], descriptor['approval_handles']
        self.pid, self.packet_sha = os.getpid(), packet_digest(self.packet)
        self.root = _absolute(Path(self.packet['evidence_path']))
        if _absolute(Path.cwd()) != self.root.parent:
            raise ValueError('Approved readback child working directory required')
        self.expected_root = descriptor['expected_historical_root']
        self.started, self.credential_resolutions = time.monotonic(), 0
        self.read_requests = self.response_bytes = 0
        self._credential = None
        self._closed = self._verified = False
        self._blocker = None
        self._root_fd = self._parent_fd = None
        self._old_timer = self._old_handler = None
        self.admission = dict(status='blocked', blockers=['trusted_operator_approval_handles_absent'],
            authorize_upload=False, authorize_remote_read=False)
        if self.root.exists():
            self._blocker = 'packet_already_consumed'
            return
        if not self.handles:
            return
        review = read_approval_handle(self.handles['source_runtime_review'])
        if (review.get('expected_historical_root') != self.expected_root
                or self.packet['producer_binding']['failure_seal_digest'] != self.expected_root
                or review.get('producer_binding', {}).get('failure_seal_digest') != self.expected_root):
            raise PermissionError('Independent protected historical root required')
        self.identity = readback_installed_identity()
        self.expected_producer, self.expected_target = deepcopy(review['producer_binding']), deepcopy(review['target'])
        # Private current records are validated before opening historical members.
        approval_digests = validate_context_readback_approvals(self.packet, installed_identity=self.identity,
            expected_producer_binding=self.expected_producer, expected_target=self.expected_target,
            approval_handles=self.handles)
        producer, self.latest, self.starts, self.history_snapshot, self.historical_action = _historical_join(self.packet, self.expected_root)
        if producer != self.expected_producer:
            raise PermissionError('Protected original producer must match independently sealed history')
        self.admission = admit_context_readback_packet(self.packet, installed_identity=self.identity,
            expected_producer_binding=producer, expected_target=self.expected_target,
            approval_handles=self.handles)
        if self.admission['status'] != 'admitted':
            return
        self.approval_digests = approval_digests
        try:
            self._reserve()
            self._arm_deadline()
            self._revalidate()
            self._credential = exporter._resolve_keychain_credential()
            self.credential_resolutions = 1
            self._project_join()
        except BaseException:
            self._stop('initialization_failed')
            self.close()
            raise

    def _remaining(self):
        expiry = datetime.fromisoformat(self.packet['expires_at'].replace('Z','+00:00'))
        remaining = min(60 - (time.monotonic() - self.started), (expiry - datetime.now(timezone.utc)).total_seconds())
        if remaining <= 0:
            self._stop('budget_exhausted')
            raise TimeoutError('Readback deadline exhausted')
        return remaining

    def _arm_deadline(self):
        self._old_handler = signal.getsignal(signal.SIGALRM)
        self._old_timer = signal.getitimer(signal.ITIMER_REAL)
        def deadline(signum, frame):
            self._stop('budget_exhausted')
            raise TimeoutError('Readback deadline exhausted')
        signal.signal(signal.SIGALRM, deadline)
        signal.setitimer(signal.ITIMER_REAL, self._remaining())

    def _new(self, name, value):
        if self._root_fd is None or '/' in name or name in {'.','..'}:
            raise PermissionError('Active anchored readback output required')
        raw = canonical_json_bytes(value)
        if len(raw) > 131072:
            raise ValueError('Bounded readback evidence required')
        descriptor = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=self._root_fd)
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(raw); stream.flush(); os.fsync(stream.fileno())
        os.fsync(self._root_fd)

    def _reserve(self):
        self._parent_fd = _directory(self.root.parent)
        self._parent_stat = os.fstat(self._parent_fd)
        os.mkdir(self.root.name, mode=0o700, dir_fd=self._parent_fd)
        os.fsync(self._parent_fd)
        self._root_fd = os.open(self.root.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=self._parent_fd)
        self._root_stat = os.fstat(self._root_fd)
        self._new('readback-intent.json', dict(format='devbench.rna-context-readback-intent/1',
            packet_id=self.packet['packet_id'], nonce=self.packet['nonce'], packet_digest=self.packet_sha,
            expected_historical_root=self.expected_root, current_reader_binding=self.identity,
            historical_producer_binding=self.expected_producer, approval_record_digests=self.approval_digests,
            limits=self.packet['limits'], authorize_upload=False))

    def _revalidate(self):
        from . import rna_hosted_exporter as exporter
        if self._closed or self._blocker or self.pid != os.getpid() or exporter._CHILD_PROCESS_PID != self.pid:
            raise PermissionError('Active original readback child required')
        self._remaining()
        parent = _directory(self.root.parent)
        try:
            parent_stat = os.fstat(parent)
        finally:
            os.close(parent)
        current = os.stat(self.root.name, dir_fd=self._parent_fd, follow_symlinks=False)
        if ((parent_stat.st_dev,parent_stat.st_ino) != (self._parent_stat.st_dev,self._parent_stat.st_ino)
                or not stat.S_ISDIR(current.st_mode) or current.st_mode != self._root_stat.st_mode
                or current.st_uid != self._root_stat.st_uid
                or (current.st_dev,current.st_ino) != (self._root_stat.st_dev,self._root_stat.st_ino)
                or packet_digest(self.packet) != self.packet_sha or readback_installed_identity() != self.identity):
            self._stop('input_drift')
            raise PermissionError('Current reader custody changed')
        approvals = validate_context_readback_approvals(self.packet, installed_identity=self.identity,
            expected_producer_binding=self.expected_producer, expected_target=self.expected_target,
            approval_handles=self.handles)
        if approvals != self.approval_digests:
            self._stop('input_drift')
            raise PermissionError('Fresh readback approvals changed')
        producer, latest, starts, snapshot, historical_action = _historical_join(self.packet, self.expected_root)
        if (producer != self.expected_producer or latest != self.latest or starts != self.starts
                or snapshot != self.history_snapshot or historical_action != self.historical_action):
            self._stop('input_drift')
            raise PermissionError('Original historical graph changed')

    def _stop(self, reason):
        if self._blocker is None:
            self._blocker = reason
            if self._root_fd is not None:
                self._new('stopped.json', dict(format='devbench.rna-context-readback-stop/1',
                    reason=reason, counts=dict(read_requests=self.read_requests,response_bytes=self.response_bytes),
                    automatic_retry=False, remote_get_cancellation_confirmed=False))

    def _request(self, *, method, path, payload=None, timeout=15):
        reads = self.packet['read_descriptor']
        if method != 'GET' or payload is not None or path not in {reads['project_route'], *reads['run_routes']}:
            raise PermissionError('Exact approved historical GET required')
        self._revalidate()
        remaining_bytes = 131072 - self.response_bytes
        if self.read_requests >= self.packet['limits']['max_read_requests'] or remaining_bytes <= 0:
            self._stop('budget_exhausted')
            raise PermissionError('Readback request/body budget exhausted')
        self.read_requests += 1
        index = self.read_requests
        self._new('request-' + str(index).zfill(4) + '-intent.json',
            dict(format='devbench.rna-context-readback-request-intent/1', packet_digest=self.packet_sha,
                request_index=index, method=method, path=path))
        target = self.packet['target']
        request = Request(target['endpoint'] + path, method='GET', headers={
            'Accept':'application/json','x-api-key':self._credential,'x-tenant-id':target['workspace_id']})
        response, status, body, complete, known, oversized = None, None, b'', False, 0, False
        family, total_known = 'transport_unavailable', True
        try:
            from .rna_hosted_tls import verified_tls_context
            context = verified_tls_context()
            if context.verify_mode != ssl.CERT_REQUIRED or context.check_hostname is not True:
                raise PermissionError('Verified hostname TLS required')
            bound_timeout = max(.001, min(15, float(timeout), self._remaining()))
            try:
                owner = build_opener(ProxyHandler({}), _NoRedirect(), _ContextHTTPSHandler(context=context)).open(request, timeout=bound_timeout)
            except HTTPError as error:
                owner = error
            response = owner.fp if isinstance(owner, HTTPError) else owner
            with owner:
                status = owner.code if isinstance(owner, HTTPError) else response.status
                # Reserve the shared reader's one-byte oversize probe inside
                # the aggregate cap, rather than authorizing a 131073rd byte.
                framed = _read_context_http_body(response, limit=remaining_bytes - 1)
                body, complete, oversized = framed['body'], framed['complete'], framed['oversized']
                known = response._devbench_context_body_bytes
            if type(status) is not int or not 100 <= status <= 599:
                status = None
            family = ('response_oversized' if oversized else 'body_incomplete' if not complete else
                'accepted' if status == 200 else 'auth_denied' if status in (401,403) else
                'not_found' if status == 404 else 'rate_limited' if status == 429 else
                'redirect' if status is not None and 300 <= status < 400 else 'provider_rejected')
        except Exception as error:
            known = getattr(response, '_devbench_context_body_bytes', 0)
            if type(known) is not int or not 0 <= known <= remaining_bytes:
                known = 0
            total_known = response is None
            family = 'transport_timeout' if isinstance(error, TimeoutError) else 'body_incomplete' if response is not None else 'transport_unavailable'
            self._stop('body_read_uncertain' if response is not None else 'transport_timeout' if isinstance(error, TimeoutError) else 'transport_unavailable')
        finally:
            self.response_bytes += known
            self._new('request-' + str(index).zfill(4) + '-result.json',
                dict(format='devbench.rna-context-readback-request-result/1', request_index=index,
                    http_status=status, family=family, known_body_bytes=known, total_body_bytes_known=total_known,
                    complete=complete, body_length=len(body) if complete else None,
                    body_sha256=digest(body) if complete else None, automatic_retry=False))
        if oversized:
            self._stop('budget_exhausted')
        elif not complete:
            self._stop('body_read_uncertain')
        elif status in (401,403):
            self._stop('auth_denied')
        self._remaining()
        return dict(status_code=status, body=body if complete else None, complete=complete, oversized=oversized)

    def _project_join(self):
        from .rna_langsmith_binding import ProjectAttestationAuthority
        self.reader_context = dict(format='devbench.rna-project-binding-context/2',
            packet_digest=self.packet_sha, session_digest=self.packet_sha,
            action_digest=packet_digest(self.packet['readback']), source_digest=self.identity['source_digest'],
            runtime_digest=self.identity['runtime_digest'], **self.packet['target'], region='us',
            expires_at=self.packet['expires_at'], max_age_seconds=30)
        producer = self.expected_producer
        self.producer_context = {**self.reader_context, 'packet_digest':producer['packet_digest'],
            'session_digest':producer['session_digest'], 'source_digest':producer['source_digest'],
            'runtime_digest':producer['runtime_digest'], 'expires_at':self.packet['session']['expires_at'],
            **self.historical_action}
        self.authority = ProjectAttestationAuthority(expected_context=self.reader_context)
        response = self._request(method='GET', path=self.packet['read_descriptor']['project_route'])
        self.project_receipt = self.authority.issue_from_response(
            response={key:response[key] for key in ('status_code','body')},
            request_nonce=str(uuid4()), observed_at=datetime.now(timezone.utc).isoformat())
        self._new('project-read.json', self.project_receipt.to_record())

    def verify(self):
        from .rna_langsmith_verify import (decode_historical_joined_context_run_response,
            context_readback_response_diagnostic, JOINED_GENERATION_CONTEXT_RESPONSE_SCHEMA_FORMAT)
        if self._verified:
            raise PermissionError('One current readback verification allowed')
        self._revalidate()
        self._verified = True
        self._new('verification-intent.json', dict(format='devbench.rna-context-readback-verification-intent/1',
            packet_digest=self.packet_sha, nonce=self.packet['nonce']))
        receipts, diagnostics = [], []
        for item in self.latest:
            operation, payload = item['operation'], item['payload']
            result, body, complete = 'invalid_response', None, False
            if not self._blocker:
                try:
                    self.project_receipt.validate(expected_context=self.reader_context)
                    response = self._request(method='GET', path='/api/v1/runs/' + payload['id'])
                    body, complete = response['body'], response['complete']
                    if complete and response['status_code'] == 200:
                        result, _ = decode_historical_joined_context_run_response(body,
                            schema_format=JOINED_GENERATION_CONTEXT_RESPONSE_SCHEMA_FORMAT, method='GET',
                            expected_payload=payload, project_receipt=self.project_receipt,
                            reader_context=self.reader_context, producer_context=self.producer_context,
                            expected_topology=self.starts, expected_session=self.packet['session'])
                    elif response['status_code'] == 404:
                        result = 'not_visible'
                except Exception:
                    if not self._blocker:
                        self._stop('input_drift')
            diagnostic = context_readback_response_diagnostic(body, payload,
                decoder_result=result, body_complete=complete)
            if body is None and self._blocker in {'body_read_uncertain','budget_exhausted'} and len(receipts) + 2 == self.read_requests:
                diagnostic['mismatch_category'] = 'body_incomplete'
            receipts.append(dict(span_id=payload['id'], revision=operation['revision'],
                operation_id=operation['operation_id'], expected_payload_sha256=operation['payload_sha256'],
                verification_result=result))
            diagnostics.append(diagnostic)
        exact = sum(row['verification_result'] == 'exact_revision_match' for row in receipts)
        result = validate_context_readback_verification(dict(format=VERIFICATION_FORMAT,
            packet_digest=self.packet_sha, verification_result='exact_context_match' if exact == len(receipts) else 'context_mismatch',
            verified_span_count=exact, receipts=receipts, diagnostics=diagnostics,
            current_reader_binding=self.identity, historical_producer_binding=self.expected_producer, **_FLAGS))
        self._new('readback-verification.json', result)
        self._stop('verification_already_attempted')
        return result

    def status(self):
        blocker = self._blocker or ('closed' if self._closed else None)
        admitted = self.admission['status'] == 'admitted' and blocker is None
        return validate_context_readback_status(dict(format=STATUS_FORMAT,
            status='admitted' if admitted else 'blocked', blockers=[blocker] if blocker else self.admission['blockers'],
            credential_resolutions=self.credential_resolutions, authorize_upload=False, authorize_remote_read=admitted,
            schema_mode='experimental_historical_context_readback', deployed_schema_qualified=False,
            packet_digest=self.packet_sha, counts=dict(read_requests=self.read_requests,response_bytes=self.response_bytes)))

    def close(self):
        if self._closed:
            return
        self._credential = None
        if self._old_handler is not None:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, self._old_handler)
            if self._old_timer is not None and self._old_timer[0] > 0:
                signal.setitimer(signal.ITIMER_REAL, *self._old_timer)
        for descriptor in (self._root_fd,self._parent_fd):
            if descriptor is not None:
                os.close(descriptor)
        self._root_fd = self._parent_fd = None
        self._closed = True
