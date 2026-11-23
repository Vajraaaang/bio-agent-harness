"""Bounded synthetic live workflow, owned only by the original exporter child.

Trusted launcher records establish reviewed input integrity, not authenticated
human identity. This engineering transport does not qualify a scientific result,
a deployed provider schema, an OS sandbox or a clinical release.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import ssl
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import HTTPSHandler, ProxyHandler, Request, build_opener
from uuid import UUID, uuid4, uuid5

from .contracts import canonical_json_bytes
from .langsmith_export import _NoRedirect, _time
from .langsmith_outbox import ExportOutbox, _digest, _uuid, digest
from .rna_hosted_connection import HostedConnection, _new
from .rna_hosted_qualification import STATUS_KEYS, _absolute

SESSION_FORMAT = 'devbench.rna-live-trace-session/3'
STATUS_FORMAT = 'devbench.rna-hosted-exporter-status/3'
VERIFICATION_FORMAT = 'devbench.rna-hosted-workflow-verification/3'
_OPERATION_KEYS = frozenset({'operation_id', 'span_id', 'revision', 'operation',
    'event_digest', 'redaction_policy_digest', 'source_binding_alias', 'payload_sha256'})
_STATUS_FIELDS = frozenset({'format', 'status', 'blockers', 'credential_resolutions',
    'authorize_upload', 'authorize_remote_read', 'schema_mode', 'deployed_schema_qualified',
    'packet_digest', 'counts', 'live_span_count', 'terminal_span_count', 'verified_span_count'})
_BLOCKERS = frozenset({'trusted_operator_approval_handles_absent', 'packet_already_consumed',
    'budget_exhausted', 'auth_denied', 'initialization_failed', 'delivery_uncertain',
    'delivery_rejected', 'input_drift', 'closed'})


def workflow_identity():
    from .rna_hosted_workflow_contract import workflow_installed_identity
    return workflow_installed_identity()


def validate_descriptor(value):
    from .rna_hosted_workflow_contract import read_approval_handle, validate_workflow_wire_packet
    if (not isinstance(value, dict) or set(value) != {'format', 'packet', 'approval_handles'}
            or value['format'] != 'devbench.rna-hosted-exporter-session/3'):
        raise ValueError('Closed workflow descriptor required')
    if len(canonical_json_bytes(value)) > 131072:
        raise ValueError('Bounded workflow descriptor required')
    handles = value['approval_handles']
    if not isinstance(handles, dict) or set(handles) not in (set(),
            {'source_runtime_review', 'target_read', 'live_session'}):
        raise ValueError('Three separate closed workflow handles required')
    validate_workflow_wire_packet(value['packet'], now=datetime.now(timezone.utc))
    for handle in handles.values():
        read_approval_handle(handle)
    return json.loads(canonical_json_bytes(value))


def validate_wire_operation(item, *, packet):
    """Closed pre-IPC validation; no files, runtime identity, keys or transport."""
    from .rna_langsmith_live import validate_live_run, _root_dotted_order
    from .rna_langsmith_verify import validate_remote_proof
    if not isinstance(item, dict) or set(item) != {'operation', 'payload'}:
        raise ValueError('Closed sanitized workflow operation required')
    if len(canonical_json_bytes(item)) > 8192:
        raise ValueError('Bounded sanitized workflow operation required')
    operation, payload = item['operation'], item['payload']
    if not isinstance(operation, dict) or set(operation) != _OPERATION_KEYS:
        raise ValueError('Closed workflow envelope required')
    kind = operation['operation']
    if (kind not in {'start', 'finish'} or type(operation['revision']) is not int
            or operation['revision'] != (0 if kind == 'start' else 1)):
        raise ValueError('Single start and terminal workflow revisions required')
    for key in ('operation_id', 'span_id', 'source_binding_alias'):
        _uuid(operation[key])
    for key in ('event_digest', 'redaction_policy_digest', 'payload_sha256'):
        _digest(operation[key])
    validate_live_run(payload, kind, session_format=SESSION_FORMAT)
    session = packet['session']
    if session['format'] != SESSION_FORMAT:
        raise ValueError('Exact workflow session version required')
    topology = {span['span_id']: span for span in packet['topology']}
    slot = topology.get(operation['span_id'])
    if slot is None or payload['id'] != operation['span_id']:
        raise ValueError('Frozen workflow topology slot required')
    metadata = payload['extra']['metadata']
    root = packet['topology'][0]['span_id']
    if (payload.get('parent_run_id') != slot['parent_span_id'] or payload['trace_id'] != root
            or any(metadata[key] != slot[key] for key in ('role', 'phase', 'attempt'))
            or payload.get('session_id') != session['project_id']
            or payload['session_name'] != session['project']
            or payload['tags'] != ['devbench', 'rna', 'synthetic']
            or payload['inputs'] != {} or payload['outputs'] != {}
            or any(metadata.get(key) != session[key] for key in ('source_digest', 'runtime_digest'))
            or operation['redaction_policy_digest'] != session['policy_digest']
            or operation['source_binding_alias'] != session['workflow_alias']
            or operation['payload_sha256'] != digest(canonical_json_bytes(payload))):
        raise ValueError('Exact frozen workflow operation binding required')
    proof = validate_remote_proof(payload)
    if proof['operation_id'] != operation['operation_id'] or proof['revision'] != operation['revision']:
        raise ValueError('Exact workflow revision proof required')
    if (metadata['timing_basis'] != 'observed'
            or metadata.get('transport_timing_basis') != 'local_receipt_clock'
            or metadata['kind'] == 'gap'
            or metadata['reason_code'] in {'clock_disagreement', 'invalid_clock', 'observer_absent',
                'observer_failure', 'event_cap', 'byte_cap', 'timing_unavailable'}):
        raise ValueError('Actual workflow lifecycle clock and receipts required')
    if kind == 'start' and (metadata['kind'] not in {'reservation', 'dispatch'}
            or metadata['source_status'] not in {'reserved', 'active', 'open', 'ready', 'running'}):
        raise ValueError('Actual workflow start evidence required')
    ancestors = [slot['span_id']]
    while slot['parent_span_id'] is not None:
        slot = topology[slot['parent_span_id']]
        ancestors.insert(0, slot['span_id'])
    components = payload['dotted_order'].split('.')
    if ([part[-36:] for part in components] != ancestors
            or components[-1] != _root_dotted_order(payload['start_time'], payload['id'])):
        raise ValueError('Frozen workflow ancestry order required')
    return json.loads(canonical_json_bytes(item))


def validate_status(value):
    if (not isinstance(value, dict) or set(value) != _STATUS_FIELDS
            or value['format'] != STATUS_FORMAT or value['status'] not in {'admitted', 'blocked'}
            or value['schema_mode'] != 'experimental_live_workflow'
            or value['deployed_schema_qualified'] is not False):
        raise ValueError('Closed experimental workflow status required')
    _digest(value['packet_digest'])
    if type(value['credential_resolutions']) is not int or value['credential_resolutions'] not in (0, 1):
        raise ValueError('Bounded workflow credential count required')
    if (any(type(value[key]) is not bool for key in ('authorize_upload', 'authorize_remote_read'))
            or value['authorize_upload'] != (value['status'] == 'admitted')
            or value['authorize_remote_read'] != value['authorize_upload']):
        raise ValueError('Workflow authority/status mismatch')
    if not isinstance(value['blockers'], list) or any(type(item) is not str or item not in _BLOCKERS for item in value['blockers']):
        raise ValueError('Closed workflow blockers required')
    counts = value['counts']
    if not isinstance(counts, dict) or set(counts) != set(STATUS_KEYS):
        raise ValueError('Closed workflow counts required')
    for key, count in counts.items():
        cap = 12 if key == 'read_requests' else 16
        if type(count) is not int or not 0 <= count <= cap:
            raise ValueError('Bounded workflow count required')
    for key in ('live_span_count', 'terminal_span_count', 'verified_span_count'):
        if type(value[key]) is not int or not 0 <= value[key] <= 8:
            raise ValueError('Bounded workflow span count required')
    if not value['verified_span_count'] <= value['terminal_span_count'] <= value['live_span_count']:
        raise ValueError('Workflow lifecycle counts disagree')
    return json.loads(canonical_json_bytes(value))


def validate_verification(value):
    from .rna_hosted_trace_contract import validate_reconciliation
    keys = {'format', 'packet_digest', 'verification_result', 'verified_span_count', 'receipts'}
    if (not isinstance(value, dict) or set(value) != keys or value['format'] != VERIFICATION_FORMAT
            or value['verification_result'] not in {'exact_workflow_match', 'workflow_mismatch'}):
        raise ValueError('Closed workflow verification required')
    _digest(value['packet_digest'])
    receipts = value['receipts']
    if not isinstance(receipts, list) or len(receipts) != 8:
        raise ValueError('Eight exact workflow observations required')
    for row in receipts:
        validate_reconciliation(row)
        if row['revision'] != 1 or row['reviewer_approval_digest'] is not None:
            raise ValueError('Terminal workflow observations without reconciliation authority required')
    matched = sum(row['verification_result'] == 'exact_revision_match' for row in receipts)
    if (len({row['span_id'] for row in receipts}) != 8
            or type(value['verified_span_count']) is not int or value['verified_span_count'] != matched
            or (value['verification_result'] == 'exact_workflow_match') != (matched == 8)):
        raise ValueError('Exact workflow verification accounting required')
    if len(canonical_json_bytes(value)) > 32768:
        raise ValueError('Bounded workflow verification required')
    return json.loads(canonical_json_bytes(value))


class _WorkflowRESTTransport:
    """The actual original-child workflow authorizes each current mutation."""
    def __init__(self, workflow):
        if type(workflow) is not HostedWorkflow:
            raise PermissionError('Actual admitted child workflow required')
        self.workflow = workflow
        self.session = workflow._authorize_transport()

    def __call__(self, *, method, path, payload, timeout):
        if self.workflow._authorize_transport() != self.session:
            raise PermissionError('Immutable admitted workflow session required')
        if (method not in {'POST', 'PATCH'}
                or path != ('/runs' if method == 'POST' else '/runs/' + _uuid(payload.get('id')))):
            raise ValueError('Exact workflow mutation route required')
        response = self.workflow._request(method=method, path='/api/v1' + path, payload=payload, timeout=timeout)
        if response['status_code'] == 202 or 400 <= response['status_code'] < 500:
            return {'status_code': response['status_code']}
        raise OSError('Workflow mutation unacknowledged')


class HostedWorkflow(HostedConnection):
    """Finite synthetic workflow; no caller transport, credentials or callbacks."""
    def __init__(self, descriptor):
        from . import rna_hosted_exporter as exporter
        from .rna_hosted_workflow_contract import (admit_workflow_packet, packet_digest,
            read_approval_handle, validate_workflow_packet)
        if exporter._CHILD_PROCESS_PID != os.getpid():
            raise PermissionError('Original exporter child required')
        descriptor = validate_descriptor(descriptor)
        self.packet, self.handles = descriptor['packet'], descriptor['approval_handles']
        self.root = _absolute(self.packet['evidence_path'])
        self.pid, self.packet_sha = os.getpid(), packet_digest(self.packet)
        if _absolute(Path.cwd()) != self.root.parent:
            raise ValueError('Approved child working directory required')
        self.ledger = self.root / 'connection.sqlite'
        self._credential = None
        self.credential_resolutions = 0
        self.box = self.exporter = self.verifier = self.project_receipt = None
        self._old_timer = self._old_handler = self._root_identity = None
        self.started = self.persisted = None
        self._closed = False
        self._current_operation = None
        self.starts, self.finishes, self._verified = {}, {}, set()
        self.observations = self.project_reads = 0
        self._run_reads, self._extra_polls = {}, 0
        if self.root.exists():
            review = read_approval_handle(self.handles['source_runtime_review']) if self.handles else None
            if (review is None or review.get('packet_digest') != self.packet_sha
                    or self.packet['packet_id'] not in self._consumed_ids()):
                raise PermissionError('Retained workflow cannot be reopened')
            self.admission = dict(status='blocked', blockers=['packet_already_consumed'],
                authorize_upload=False, authorize_remote_read=False)
            return
        self.identity = workflow_identity()
        review = read_approval_handle(self.handles['source_runtime_review']) if self.handles else None
        self.expected_binding = deepcopy(review['binding'] if review else self.packet['binding'])
        self.expected_target = deepcopy(review['target'] if review else self.packet['target'])
        self.packet = validate_workflow_packet(self.packet, installed_identity=self.identity,
            expected_binding=self.expected_binding, expected_target=self.expected_target, now=self._now())
        prefix = Path(sys.prefix).resolve()
        if self.root.is_relative_to(prefix) or prefix.is_relative_to(self.root):
            raise ValueError('Workflow output overlaps installed runtime')
        self.admission = admit_workflow_packet(self.packet, installed_identity=self.identity,
            expected_binding=self.expected_binding, expected_target=self.expected_target,
            now=self._now(), approval_handles=self.handles)
        if self.admission['status'] != 'admitted':
            return
        self._reserve()
        self._arm_deadline()
        try:
            self._revalidate()
            self._credential = exporter._resolve_keychain_credential()
            self.credential_resolutions = 1
            self.session = {**self.packet['session'], 'authorize_upload': True}
            self.execution_session_digest = digest(canonical_json_bytes(self.session))
            self._project_join()
        except BaseException as error:
            reason = 'initialization_failed'
            try:
                self._check_deadline()
            except TimeoutError:
                reason = 'budget_exhausted'
            self._stop(reason)
            if isinstance(error, exporter.CredentialUnavailable) and error.reason_code in exporter._CREDENTIAL_FAILURE_CODES:
                try:
                    _new(self.root / 'initialization-failure.json', dict(format='devbench.rna-credential-failure/1',
                        stage='credential_resolution', reason=error.reason_code))
                except Exception:
                    pass
            self.close()
            raise

    def _reserve(self):
        from .rna_hosted_workflow_contract import validate_output_path
        validate_output_path(self.root, approval_handles=self.handles)
        self.root.mkdir(mode=0o700, exist_ok=False)
        info = self.root.stat(follow_symlinks=False)
        self._root_identity = (info.st_dev, info.st_ino)
        fd = os.open(self.ledger, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        os.close(fd)
        self.started = time.monotonic_ns()
        with self._db() as db:
            db.execute('CREATE TABLE connection(packet_id TEXT PRIMARY KEY,packet_digest TEXT,prepared_session_digest TEXT,execution_session_digest TEXT,started_ns INTEGER,mutations INTEGER,reads INTEGER,requests INTEGER,response_bytes INTEGER,payload_bytes INTEGER,stopped_reason TEXT)')
            db.execute('INSERT INTO connection VALUES(?,?,?,?,?,0,0,0,0,0,NULL)',
                (self.packet['packet_id'], self.packet_sha, self.packet['binding']['session_digest'], None, self.started))
        _new(self.root / 'packet.json', self.packet)

    def _stop(self, reason):
        with self._db() as db:
            db.execute('UPDATE connection SET stopped_reason=coalesce(stopped_reason,?)', (reason,))

    def _revalidate(self):
        from . import rna_hosted_exporter as exporter
        from .rna_hosted_workflow_contract import validate_workflow_approvals
        if self._closed or exporter._CHILD_PROCESS_PID != self.pid or self.pid != os.getpid():
            raise PermissionError('Active original child workflow required')
        if self.admission['status'] != 'admitted':
            raise PermissionError('Admitted workflow required')
        self._check_deadline()
        with self._db() as db:
            if db.execute('SELECT stopped_reason FROM connection').fetchone()[0] is not None:
                raise PermissionError('Workflow stopped')
        if (digest(canonical_json_bytes(self.packet)) != self.packet_sha
                or workflow_identity() != self.identity):
            raise PermissionError('Immutable reviewed workflow and installed identity required')
        checked = validate_workflow_approvals(self.packet, expected_binding=self.expected_binding,
            expected_target=self.expected_target, now=self._now(), approval_handles=self.handles)
        if checked != self.admission['approval_record_digests']:
            raise PermissionError('Exact retained workflow operator records required')

    def _authorize_transport(self):
        self._revalidate()
        if self.project_receipt is None:
            raise PermissionError('Fresh child project observation required')
        self.project_receipt.validate(expected_context=self.context, now=self._now())
        return json.loads(canonical_json_bytes(self.session))

    def _request(self, *, method, path, payload, timeout):
        from .rna_hosted_tls import verified_tls_context
        self._revalidate()
        reads = self.packet['read_descriptor']
        allowed_reads = {reads['project_route'], *reads['run_routes']}
        if method == 'GET':
            if path not in allowed_reads or payload is not None:
                raise ValueError('Exact frozen workflow read required')
        else:
            current = self._current_operation
            if (current is None or method not in {'POST', 'PATCH'}
                    or method != ('POST' if current['operation']['operation'] == 'start' else 'PATCH')
                    or path != ('/api/v1/runs' if method == 'POST' else '/api/v1/runs/' + current['payload']['id'])
                    or canonical_json_bytes(payload) != canonical_json_bytes(current['payload'])):
                raise PermissionError('Exact currently emitted workflow mutation required')
            self._authorize_transport()
            with self.box._db() as db:
                row = db.execute('SELECT state,attempt_count FROM operations WHERE operation_id=?',
                    (current['operation']['operation_id'],)).fetchone()
            if row is None or row['state'] != 'sending' or row['attempt_count'] != 1:
                raise PermissionError('One actual outbox mutation attempt required')
        raw = canonical_json_bytes(payload) if payload is not None else None
        operation_bytes = len(canonical_json_bytes(self._current_operation)) if method != 'GET' else 0
        limits = self.packet['limits']
        counter = 'reads' if method == 'GET' else 'mutations'
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM connection').fetchone()
            remaining_bytes = limits['max_response_bytes'] - row['response_bytes']
            exhausted = (row[counter] >= limits['max_read_requests' if method == 'GET' else 'max_mutation_requests']
                    or row['requests'] >= limits['max_total_requests'] or remaining_bytes <= 0
                    or row['payload_bytes'] + operation_bytes > limits['max_bytes'])
            if exhausted:
                db.execute("UPDATE connection SET stopped_reason='budget_exhausted'")
            else:
                index = row['requests'] + 1
                db.execute('UPDATE connection SET ' + counter + '=' + counter + '+1,requests=requests+1,payload_bytes=payload_bytes+?', (operation_bytes,))
        if exhausted:
            raise PermissionError('Workflow request or byte cap reached')
        target = self.packet['target']
        request = Request(target['endpoint'] + path, data=raw, method=method,
            headers={'Content-Type': 'application/json', 'Accept': 'application/json',
                'x-api-key': self._credential, 'x-tenant-id': target['workspace_id']})
        bound_timeout = max(.001, min(15, float(timeout), self._check_deadline()))
        body = b''
        try:
            context = verified_tls_context()
            if context.verify_mode != ssl.CERT_REQUIRED or context.check_hostname is not True:
                raise PermissionError('Verified hostname TLS context required')
            with build_opener(ProxyHandler({}), _NoRedirect(), HTTPSHandler(context=context)).open(request, timeout=bound_timeout) as response:
                status = response.status
                body = response.read(min(65537, remaining_bytes + 1))
        except HTTPError as error:
            status = error.code
            error.close()
        except Exception as error:
            reason = error.reason if isinstance(error, URLError) else error
            family = ('tls_verification_failed' if isinstance(reason, ssl.SSLCertVerificationError)
                else 'transport_timeout' if isinstance(reason, TimeoutError) else 'transport_unavailable')
            self._retain_http_attempt(index, method, path, None, family)
            raise OSError('Workflow request unacknowledged') from None
        valid = type(status) is int and 100 <= status <= 599
        family = ('accepted' if valid and 200 <= status < 300 else 'auth_denied' if status in (401, 403)
            else 'not_found' if status == 404 else 'conflict' if status == 409
            else 'rate_limited' if status == 429 else 'client_rejected' if valid and 400 <= status < 500
            else 'server_error' if valid and status >= 500 else 'redirect' if valid and 300 <= status < 400
            else 'invalid_response')
        if len(body) > remaining_bytes:
            family = 'response_oversized'
        with self._db() as db:
            db.execute('UPDATE connection SET response_bytes=response_bytes+?', (len(body),))
            if status in (401, 403):
                db.execute("UPDATE connection SET stopped_reason='auth_denied'")
            elif len(body) > remaining_bytes:
                db.execute("UPDATE connection SET stopped_reason='budget_exhausted'")
        self._retain_http_attempt(index, method, path, status if valid else None, family)
        self._check_deadline()
        if not valid or len(body) > remaining_bytes:
            raise ValueError('Bounded workflow HTTP response required')
        return {'status_code': status, 'body': body}

    def _retain_http_attempt(self, index, method, path, status, family):
        allowed = {'accepted', 'auth_denied', 'not_found', 'conflict', 'rate_limited', 'client_rejected',
            'server_error', 'redirect', 'invalid_response', 'response_oversized', 'transport_unavailable',
            'transport_timeout', 'tls_verification_failed'}
        if family not in allowed or type(index) is not int or not 1 <= index <= 28:
            return
        kind = ('project_read' if path == self.packet['read_descriptor']['project_route']
            else 'run_read' if method == 'GET' else 'run_start' if method == 'POST' else 'run_finish')
        try:
            _new(self.root / ('http-attempt-' + str(index) + '.json'),
                dict(format='devbench.rna-http-attempt/1', request_index=index, method=method,
                    route_kind=kind, http_status=status, family=family))
        except Exception:
            pass

    def _project_join(self):
        from .rna_langsmith_binding import ProjectAttestationAuthority
        self.context = dict(format='devbench.rna-project-binding-context/2', packet_digest=self.packet_sha,
            session_digest=self.execution_session_digest, action_digest=digest(canonical_json_bytes(self.packet['target_read'])),
            source_digest=self.identity['source_digest'], runtime_digest=self.identity['runtime_digest'],
            **self.packet['target'], region=self.packet['region'], expires_at=self.packet['expires_at'],
            max_age_seconds=self.packet['read_descriptor']['max_project_age_seconds'])
        self.authority = ProjectAttestationAuthority(expected_context=self.context)
        response = self._request(method='GET', path=self.packet['read_descriptor']['project_route'], payload=None, timeout=15)
        self.project_receipt = self.authority.issue_from_response(response=response,
            request_nonce=str(uuid4()), observed_at=self._now().isoformat())
        self.project_reads += 1
        _new(self.root / ('project-read-' + str(self.project_reads) + '.json'), self.project_receipt.to_record())
        with self._db() as db:
            db.execute('UPDATE connection SET execution_session_digest=?', (self.execution_session_digest,))

    def emit(self, item):
        from .rna_langsmith_live import LiveTraceExporter
        item = validate_wire_operation(item, packet=self.packet)
        self._authorize_transport()
        operation, payload = item['operation'], item['payload']
        identity, kind = payload['id'], operation['operation']
        if self.box is not None:
            with self.box._db() as db:
                if db.execute('SELECT 1 FROM operations WHERE operation_id=? OR (span_id=? AND revision=?)',
                        (operation['operation_id'], identity, operation['revision'])).fetchone():
                    raise ValueError('Duplicate existing workflow operation refused')
                count = db.execute('SELECT count(*) FROM operations').fetchone()[0]
            if count >= 16:
                raise ValueError('Workflow operation cap reached')
        root = self.packet['topology'][0]['span_id']
        parent = payload.get('parent_run_id')
        if kind == 'start':
            if identity in self.starts or identity in self.finishes:
                raise ValueError('Duplicate workflow start refused')
            if not self.starts and identity != root:
                raise ValueError('Workflow root start must be first')
            if parent is not None:
                if parent not in self.starts or not self._acknowledged(parent, 'start'):
                    raise ValueError('Parent start must be acknowledged')
                if parent in self.finishes:
                    raise ValueError('No workflow child after terminal parent')
                from .rna_langsmith_live import _root_dotted_order
                if payload['dotted_order'] != self.starts[parent]['dotted_order'] + '.' + _root_dotted_order(payload['start_time'], identity):
                    raise ValueError('Exact retained parent order required')
        else:
            if identity not in self.starts or not self._acknowledged(identity, 'start'):
                raise ValueError('Acknowledged workflow start required before terminal revision')
            if identity in self.finishes:
                raise ValueError('Duplicate workflow terminal refused')
            if payload['dotted_order'] != self.starts[identity]['dotted_order']:
                raise ValueError('Exact retained terminal workflow order required')
            children = [row['span_id'] for row in self.packet['topology'] if row['parent_span_id'] == identity]
            if any(child not in self.finishes or not self._acknowledged(child, 'finish') for child in children):
                raise ValueError('All declared child terminal revisions must be acknowledged')
        if self.box is None:
            if kind != 'start' or identity != root:
                raise ValueError('Initial root start required')
            document = dict(record_format='devbench.rna-langsmith-export/1', privacy_policy='rna-allowlist/1',
                data_scope='synthetic', runs=[payload])
            directory = self.root / 'payload'
            directory.mkdir(mode=0o700, exist_ok=False)
            path = directory / 'payload.json'
            _new(path, document)
            self.persisted = time.monotonic_ns()
            self.box = ExportOutbox.create_live(self.root / 'outbox' / 'journal.sqlite', payload_path=path,
                expected_sha256=digest(canonical_json_bytes(document)),
                authorized_session_manifest=self.session, initial_operations=[item])
            self.exporter = LiveTraceExporter.open(authorized_session_manifest=self.session, outbox=self.box,
                sanitized_event_source=(), transport=_WorkflowRESTTransport(self))
        else:
            self.box.enqueue_operation(operation, payload, max_spans=8, max_bytes=131072)
        if kind == 'start':
            self.starts[identity] = payload
        else:
            self.finishes[identity] = item
        self._current_operation = item
        try:
            self.exporter.drain_once(max_operations=1, deadline_seconds=min(5, self._check_deadline()))
        finally:
            self._current_operation = None
        if not self._acknowledged(identity, kind):
            with self.box._db() as db:
                state = db.execute('SELECT state FROM operations WHERE operation_id=?', (operation['operation_id'],)).fetchone()[0]
            self._stop('delivery_rejected' if state in {'rejected', 'blocked'} else 'delivery_uncertain')
        return self.status()

    def _acknowledged(self, identity, kind):
        if self.box is None:
            return False
        with self.box._db() as db:
            row = db.execute('SELECT state FROM operations WHERE span_id=? AND operation=?', (identity, kind)).fetchone()
        return row is not None and row[0] == 'acknowledged'

    def _read_transport(self, *, method, path, payload, timeout):
        if method != 'GET' or payload is not None or path not in {'/runs/' + value for value in self.starts}:
            raise ValueError('Exact frozen workflow run GET required')
        identity = path.rsplit('/', 1)[1]
        count = self._run_reads.get(identity, 0)
        if count:
            if self._extra_polls >= 2:
                raise PermissionError('Workflow polling read cap reached')
            self._extra_polls += 1
        self._run_reads[identity] = count + 1
        return self._request(method='GET', path='/api/v1' + path, payload=None, timeout=timeout)

    def _make_verifier(self):
        from .rna_hosted_workflow_contract import WORKFLOW_CRITERIA
        from .rna_langsmith_verify import WorkflowRunVerifier
        limits = {key: self.packet['limits'][key] for key in ('max_spans', 'max_bytes',
            'max_mutation_requests', 'max_read_requests', 'max_delivery_attempts', 'total_deadline_seconds')}
        manifest = dict(format='devbench.rna-hosted-qualification/1', qualification_id=self.packet['packet_id'],
            mode='hosted', data_scope='synthetic', **self.identity, output_path=str(self.root),
            **self.packet['target'], live_session_digest=self.execution_session_digest,
            expires_at=self.packet['expires_at'], allowed_operations=['create_run', 'update_run', 'read_run'],
            authorize_upload=True, authorize_remote_read=True,
            authorization_record_digest=digest(canonical_json_bytes(self.admission['approval_record_digests'])), limits=limits)
        self.verifier = WorkflowRunVerifier(manifest=manifest, criteria=WORKFLOW_CRITERIA['verifier_criteria'],
            api_key=self._credential, read_transport=self._read_transport, project_receipt=self.project_receipt,
            expected_context=self.context, expected_topology=self.starts)
        self.verifier.bind_session(self.session)

    def verify(self):
        if len(self.starts) != 8 or len(self.finishes) != 8 or any(not self._acknowledged(identity, 'finish') for identity in self.finishes):
            raise ValueError('All eight complete acknowledged terminal revisions required')
        if self.observations:
            raise PermissionError('Workflow verification already attempted')
        self._revalidate()
        self._project_join()
        self._make_verifier()
        self.observations = 1
        receipts = []
        for span in self.packet['topology']:
            item = self.finishes[span['span_id']]
            try:
                result = self.verifier.observe(operation=item['operation'], expected_payload=item['payload'],
                    persisted_monotonic_ns=self.persisted)
            except Exception:
                # Retain a closed failure for every declared span, even if a
                # deadline or aged project receipt ends the remaining reads.
                result = self.verifier._receipt(item['operation'], item['payload'], self.persisted,
                    None, 'invalid_response', 'blocked')
            receipts.append(result)
            _new(self.root / ('observation-' + str(len(receipts)) + '.json'), result)
        self._verified = {row['span_id'] for row in receipts if row['verification_result'] == 'exact_revision_match'}
        result = validate_verification(dict(format=VERIFICATION_FORMAT, packet_digest=self.packet_sha,
            verification_result='exact_workflow_match' if len(self._verified) == 8 else 'workflow_mismatch',
            verified_span_count=len(self._verified), receipts=receipts))
        _new(self.root / 'verification.json', result)
        return result

    def status(self):
        counts = {key: 0 for key in STATUS_KEYS}
        if self.box is not None:
            source = self.box.status()['counts']
            for key in STATUS_KEYS[:5]:
                counts[key] = source.get(key, 0)
            counts['uncertain'] += source.get('sending', 0)
        stopped = None
        if self.ledger.exists():
            with self._db() as db:
                row = db.execute('SELECT mutations,reads,stopped_reason FROM connection').fetchone()
            if row:
                counts.update(mutation_requests=row[0], read_requests=row[1])
                stopped = row[2]
        active = self.admission['status'] == 'admitted' and not self._closed and stopped is None
        blockers = list(self.admission['blockers'])
        if self.admission['status'] == 'admitted':
            if stopped is not None:
                blockers = [stopped]
            elif self._closed:
                blockers = ['closed']
        if active:
            try:
                self._revalidate()
            except TimeoutError:
                active, blockers = False, ['budget_exhausted']
            except (ValueError, PermissionError):
                active, blockers = False, ['input_drift']
        return validate_status(dict(format=STATUS_FORMAT, status='admitted' if active else 'blocked',
            blockers=blockers, credential_resolutions=self.credential_resolutions,
            authorize_upload=active, authorize_remote_read=active, schema_mode='experimental_live_workflow',
            deployed_schema_qualified=False, packet_digest=self.packet_sha, counts=counts,
            live_span_count=len(self.starts), terminal_span_count=len(self.finishes), verified_span_count=len(self._verified)))

    def prepare(self):
        raise PermissionError('Workflow operations require explicit live emit')

    def drain(self):
        raise PermissionError('Workflow resends and implicit drain are prohibited')

    def reconcile(self, *args, **kwargs):
        raise PermissionError('Workflow reconciliation is not authorized')

    def close(self):
        self._current_operation = None
        self._closed = True
        super().close()


class WorkflowReceiptBridge:
    """Remap actual immutable local contexts into the preapproved remote slots."""
    def __init__(self, *, authorized_session_manifest, output_path):
        from .rna_langsmith_live import ReceiptOperationBridge, validate_live_session
        self.session = validate_live_session(authorized_session_manifest)
        if self.session['format'] != SESSION_FORMAT:
            raise ValueError('Frozen workflow session /3 required')
        self.path = _absolute(output_path)
        self.bridge = ReceiptOperationBridge(authorized_session_manifest=self.session,
            output_path=self.path, capability=dict(backend_kind='unknown', qualification_status='unknown', admitted=False),
            allocation=0, actual_children=0)
        self.source_event_digests = []

    def emit_new(self, *, observer, workflow_id):
        from devbench.analysis.rna_trace_contract import trace_digest, validate_trace_event
        from devbench.analysis.rna_trace_receipts import LocalTraceObserver
        if type(observer) is not LocalTraceObserver or trace_digest(workflow_id) != self.session['workflow_binding_digest']:
            raise ValueError('Actual local workflow observer required')
        events = observer.read_events(workflow_id)
        contexts = observer.read_contexts(workflow_id)
        if not isinstance(events, list) or len(events) > 16 or len(contexts) > 8:
            raise ValueError('Bounded actual workflow receipt topology required')
        slots = {(row['role'], row['phase'], row['attempt']): row for row in self.session['span_topology']}
        mapping, actual, roots = {}, {}, []
        for context in contexts:
            slot = slots.get((context['role'], context['phase'], context['attempt']))
            if slot is None or slot['span_id'] in actual or context['span_id'] in mapping:
                raise ValueError('Unapproved or duplicate actual workflow context slot')
            if context['span_id'] in {row['span_id'] for row in self.session['span_topology']}:
                raise ValueError('Fresh remote IDs must differ from actual local IDs')
            mapping[context['span_id']] = slot['span_id']
            actual[slot['span_id']] = context
            if context['parent_span_id'] is None:
                roots.append(context)
        if events and len(roots) != 1:
            raise ValueError('One actual workflow root context required')
        remote_root = self.session['span_topology'][0]['span_id']
        remapped, source_digests = [], []
        for source in events:
            event = validate_trace_event(source)
            identity = mapping.get(event['span_id'])
            context = actual.get(identity)
            slot = next((row for row in self.session['span_topology'] if row['span_id'] == identity), None)
            if (context is None or slot is None or event['trace_id'] != roots[0]['trace_id']
                    or event['parent_span_id'] != context['parent_span_id']
                    or any(event[key] != context[key] for key in ('role', 'phase', 'attempt'))
                    or mapping.get(event['parent_span_id']) != slot['parent_span_id']
                    or event['bindings'].get('invocation') != context['invocation_binding_digest']
                    or event['bindings']['workflow'] != self.session['workflow_binding_digest']
                    or any(event['bindings'].get(binding) != self.session[key] for key, binding in
                        (('source_digest', 'source'), ('runtime_digest', 'runtime'), ('policy_digest', 'policy')))):
                raise ValueError('Actual workflow context or topology binding changed')
            if (event['sequence'] not in (0, 1) or event['kind'] == 'gap'
                    or event['clock']['timing_basis'] != 'observed' or event['clock']['utc'] is None):
                raise ValueError('Actual start and terminal receipts required without gaps')
            source_digests.append(digest(canonical_json_bytes(event)))
            event.update(span_id=identity, trace_id=remote_root, parent_span_id=slot['parent_span_id'],
                event_id=str(uuid5(UUID(self.session['workflow_alias']), 'actual-event:' + event['event_id'])))
            remapped.append(event)
        class RemappedObserver:
            def read_events(self, _workflow_id):
                return deepcopy(remapped)
        emitted = self.bridge.emit_new(observer=RemappedObserver(), workflow_id=workflow_id)
        self.source_event_digests = source_digests
        return emitted


# /4 is an additive observation profile. The /3 workflow contract above is fixed.
CONTEXT_SESSION_FORMAT = 'devbench.rna-live-trace-session/4'
GENERATION_CONTEXT_SESSION_FORMAT = 'devbench.rna-live-trace-session/5'
CONTEXT_STATUS_FORMAT = 'devbench.rna-hosted-exporter-status/4'
CONTEXT_VERIFICATION_FORMAT = 'devbench.rna-hosted-context-verification/4'
_CONTEXT_BLOCKERS = _BLOCKERS | {'body_read_uncertain'}


def context_identity():
    from .rna_hosted_context_contract import context_installed_identity
    return context_installed_identity()


def _validate_context_descriptor(value, *, generation=False):
    from .rna_hosted_context_contract import read_approval_handle, validate_context_wire_packet, validate_generation_context_wire_packet
    if (not isinstance(value, dict) or set(value) != {'format', 'packet', 'approval_handles'}
            or value['format'] != 'devbench.rna-hosted-exporter-session/' + ('5' if generation else '4')
            or len(canonical_json_bytes(value)) > 131072):
        raise ValueError('Closed bounded context descriptor required')
    handles = value['approval_handles']
    if not isinstance(handles, dict) or set(handles) not in (set(),
            {'source_runtime_review', 'target_read', 'live_session'}):
        raise ValueError('Separate closed context approval handles required')
    (validate_generation_context_wire_packet if generation else validate_context_wire_packet)(value['packet'], now=datetime.now(timezone.utc))
    for handle in handles.values():
        read_approval_handle(handle)
    return json.loads(canonical_json_bytes(value))


def _validate_context_wire_operation(item, *, packet, generation=False):
    """Validate closed /4 wire content without granting effect authority."""
    from .rna_langsmith_live import validate_context_run_bindings
    if (not isinstance(item, dict) or set(item) != {'operation', 'payload'}
            or len(canonical_json_bytes(item)) > 16384):
        raise ValueError('Closed bounded context operation required')
    operation, payload = item['operation'], item['payload']
    if not isinstance(operation, dict) or set(operation) != _OPERATION_KEYS:
        raise ValueError('Closed context operation envelope required')
    if (operation['operation'] not in {'start', 'finish'}
            or type(operation['revision']) is not int
            or operation['revision'] != (0 if operation['operation'] == 'start' else 1)):
        raise ValueError('Single context observation and explicit terminal revision required')
    for key in ('operation_id', 'span_id', 'source_binding_alias'):
        _uuid(operation[key])
    for key in ('event_digest', 'redaction_policy_digest', 'payload_sha256'):
        _digest(operation[key])
    if (packet['session']['format'] != (GENERATION_CONTEXT_SESSION_FORMAT if generation else CONTEXT_SESSION_FORMAT)
            or packet['profile'] != packet['session']['context_profile']
            or len(canonical_json_bytes(payload)) > 8192
            or payload.get('id') != operation['span_id']
            or operation['payload_sha256'] != digest(canonical_json_bytes(payload))):
        raise ValueError('Exact frozen context wire binding required')
    # This copy selects validation semantics only. Actual authority is checked
    # independently by the original child before any journal or HTTP mutation.
    validate_context_run_bindings(payload, operation, {**packet['session'], 'authorize_upload': True})
    return json.loads(canonical_json_bytes(item))


def _validate_context_status(value, *, generation=False):
    keys = _STATUS_FIELDS | {'source_event_count', 'capture_event_count'} | ({'dispatch_event_count'} if generation else set())
    if (not isinstance(value, dict) or set(value) != keys
            or value['format'] != ('devbench.rna-hosted-exporter-status/5' if generation else CONTEXT_STATUS_FORMAT) or value['status'] not in {'admitted', 'blocked'}
            or value['schema_mode'] != ('experimental_generation_context_observation' if generation else 'experimental_context_observation')
            or value['deployed_schema_qualified'] is not False):
        raise ValueError('Closed experimental context status required')
    _digest(value['packet_digest'])
    if type(value['credential_resolutions']) is not int or value['credential_resolutions'] not in (0, 1):
        raise ValueError('Bounded context credential count required')
    if (any(type(value[key]) is not bool for key in ('authorize_upload', 'authorize_remote_read'))
            or value['authorize_upload'] != (value['status'] == 'admitted')
            or value['authorize_remote_read'] != value['authorize_upload']):
        raise ValueError('Context authority/status mismatch')
    if not isinstance(value['blockers'], list) or any(type(row) is not str or row not in _CONTEXT_BLOCKERS for row in value['blockers']):
        raise ValueError('Closed context blockers required')
    counts = value['counts']
    if not isinstance(counts, dict) or set(counts) != set(STATUS_KEYS):
        raise ValueError('Closed context operation counts required')
    for key, number in counts.items():
        if type(number) is not int or not 0 <= number <= (20 if key == 'read_requests' else 32):
            raise ValueError('Bounded context operation count required')
    for key, cap in (('live_span_count', 16), ('terminal_span_count', 16), ('verified_span_count', 16),
                     ('source_event_count', 64), ('capture_event_count', 64)):
        if type(value[key]) is not int or not 0 <= value[key] <= cap:
            raise ValueError('Bounded context observation count required')
    if (value['terminal_span_count'] > value['live_span_count']
            or value['verified_span_count'] > value['live_span_count']
            or value['capture_event_count'] > value['source_event_count']):
        raise ValueError('Context observation counts disagree')
    if generation and (type(value['dispatch_event_count']) is not int or not 1 <= value['dispatch_event_count'] <= value['source_event_count']):
        raise ValueError('Bounded actual generation dispatch count required')
    return json.loads(canonical_json_bytes(value))


def _validate_context_verification(value, *, generation=False):
    from .rna_hosted_trace_contract import validate_reconciliation
    from .rna_langsmith_verify import validate_context_response_diagnostic
    keys = {'format', 'packet_digest', 'verification_result', 'verified_span_count', 'receipts', 'diagnostics'}
    if (not isinstance(value, dict) or set(value) != keys
            or value['format'] != ('devbench.rna-hosted-context-verification/5' if generation else CONTEXT_VERIFICATION_FORMAT)
            or value['verification_result'] not in {'exact_context_match', 'context_mismatch'}):
        raise ValueError('Closed context verification required')
    _digest(value['packet_digest'])
    rows = value['receipts']
    if not isinstance(rows, list) or not 1 <= len(rows) <= 16:
        raise ValueError('Bounded current context observations required')
    for row in rows:
        validate_reconciliation(row)
        if row['revision'] not in (0, 1) or row['reviewer_approval_digest'] is not None:
            raise ValueError('Current context observations without reconciliation authority required')
    identities = {row['span_id'] for row in rows}
    matched = sum(row['verification_result'] == 'exact_revision_match' for row in rows)
    if (len(identities) != len(rows) or type(value['verified_span_count']) is not int
            or value['verified_span_count'] != matched
            or (value['verification_result'] == 'exact_context_match') != (matched == len(rows))):
        raise ValueError('Exact current context accounting required')
    binding_keys = ('qualification_id', 'session_digest', 'endpoint', 'workspace_id', 'project_id')
    if any(any(row[key] != rows[0][key] for key in binding_keys) for row in rows):
        raise ValueError('One retained context verification binding required')
    diagnostics = value['diagnostics']
    if not isinstance(diagnostics, list) or not len(rows) <= len(diagnostics) <= 20:
        raise ValueError('Bounded context response diagnostics required')
    for diagnostic in diagnostics:
        validate_context_response_diagnostic(diagnostic)
        if diagnostic['span_id'] not in identities:
            raise ValueError('Diagnostic outside observed context topology')
    if not identities <= {row['span_id'] for row in diagnostics} or len(canonical_json_bytes(value)) > 65536:
        raise ValueError('Complete bounded context diagnostics required')
    return json.loads(canonical_json_bytes(value))


def _actual_context_sources(observer, workflow_id, *, generation=False):
    """Validate actual local custody before allocating or matching remote aliases."""
    from devbench.analysis.rna_trace_contract import trace_digest, validate_trace_event
    from devbench.analysis.rna_trace_receipts import LocalTraceObserver
    if type(observer) is not LocalTraceObserver or type(workflow_id) is not str or not 1 <= len(workflow_id) <= 200:
        raise ValueError('Actual local context observer required')
    events, contexts = observer.read_events(workflow_id), observer.read_contexts(workflow_id)
    if not 1 <= len(contexts) <= 16 or not 1 <= len(events) <= 64:
        raise ValueError('Bounded actual context and source-event profile required')
    actual = {row['span_id']: row for row in contexts}
    roots = [row for row in contexts if row['parent_span_id'] is None]
    if len(actual) != len(contexts) or len(roots) != 1:
        raise ValueError('One unique actual context tree required')
    root = roots[0]
    if root['role'] != 'coordinator' or root['phase'] != 'workflow':
        raise ValueError('Actual coordinator workflow root required')
    grouped, ordered = {identity: [] for identity in actual}, []
    raw_ids = _context_raw_ids(events, actual)
    probe_aliases = {}
    for identity in actual:
        alias = str(uuid4())
        while alias in raw_ids or alias in probe_aliases.values():
            alias = str(uuid4())
        probe_aliases[identity] = alias
    terminal = set()
    for source in events:
        event = validate_trace_event(source)
        context = actual.get(event['span_id'])
        if (context is None or event['trace_id'] != root['trace_id']
                or event['parent_span_id'] != context['parent_span_id']
                or any(event[key] != context[key] for key in ('role', 'phase', 'attempt'))
                or event['bindings']['workflow'] != trace_digest(workflow_id)
                or event['bindings'].get('invocation') != context['invocation_binding_digest']
                or any(event['bindings'].get(binding) != context[key] or context[key] != observer.identities[key]
                    for key, binding in (('source_digest', 'source'), ('runtime_digest', 'runtime'), ('policy_digest', 'policy')))
                or event['clock']['timing_basis'] != 'observed' or event['clock']['utc'] is None
                or event['kind'] in ({'gap'} if generation else {'gap', 'dispatch'}) or event['span_id'] in terminal
                or event['kind'] == 'dispatch' and (event['dispatch_status'] != 'attempted' or event['role'] == 'numerical')):
            raise ValueError('Actual immutable context source binding required')
        group = grouped[event['span_id']]
        if event['sequence'] != len(group):
            raise ValueError('Contiguous actual source sequence required')
        if not group:
            parent = event['parent_span_id']
            if parent is not None and parent not in ordered:
                raise ValueError('Actual parent observation must precede child')
            ordered.append(event['span_id'])
        group.append(event)
        from .rna_langsmith_live import context_event_terminal_status, project_context_receipt
        # Only the closed kind/status helper determines completion. A completed
        # capture remains source evidence and never becomes an implicit finish.
        projected = project_context_receipt(event, context_alias=probe_aliases[event['span_id']],
            parent_context_alias=probe_aliases.get(event['parent_span_id']))
        if context_event_terminal_status(projected) is not None:
            terminal.add(event['span_id'])
    if set(ordered) != set(actual):
        raise ValueError('Every frozen actual context needs observed source evidence')
    return events, actual, grouped, ordered


def _prepare_context_observation(*, observer, workflow_id, generation=False):
    """Allocate fresh export aliases from retained synthetic engineering receipts.

    The returned topology/profile contains no raw context, event or trace IDs.
    It describes retained observations and grants no upload or scientific claim.
    """
    from .rna_langsmith_live import context_event_terminal_status, project_context_receipt
    from .rna_hosted_context_contract import validate_context_profile, validate_context_topology
    events, actual, grouped, ordered = _actual_context_sources(observer, workflow_id, generation=generation)
    raw_ids = _context_raw_ids(events, actual)
    allocated = set(raw_ids)
    def fresh():
        identity = str(uuid4())
        while identity in allocated:
            identity = str(uuid4())
        allocated.add(identity)
        return identity
    remote = {identity: fresh() for identity in ordered}
    aliases = {identity: fresh() for identity in ordered}
    topology, rows, terminal_ids, open_ids = [], [], [], []
    for identity in ordered:
        context = actual[identity]
        parent = context['parent_span_id']
        topology.append(dict(span_id=remote[identity], parent_span_id=remote.get(parent),
            **{key: context[key] for key in ('role', 'phase', 'attempt')}))
        projected = [project_context_receipt(event, context_alias=aliases[identity],
            parent_context_alias=aliases.get(parent)) for event in grouped[identity]]
        statuses = [context_event_terminal_status(event) for event in projected]
        terminal_status = next((status for status in reversed(statuses) if status is not None), None)
        (terminal_ids if terminal_status is not None else open_ids).append(remote[identity])
        rows.append(dict(span_id=remote[identity], context_alias=aliases[identity],
            parent_context_alias=aliases.get(parent),
            start_observed=projected[0]['kind'] in {'reservation', 'dispatch'},
            source_event_digests=[digest(canonical_json_bytes(event)) for event in projected],
            source_event_count=len(projected), capture_event_count=sum(event['kind'] == 'capture' for event in projected),
            terminal_status=terminal_status))
        if generation:
            rows[-1]['dispatch_event_count'] = sum(event['kind'] == 'dispatch' for event in projected)
    profile = dict(format='devbench.rna-context-observation-profile/' + ('5' if generation else '4'), data_scope='synthetic',
        provenance='synthetic_engineering', usage_policy='unavailable',
        digest_policy='session_aliased_sanitized_receipts', context_count=len(rows),
        source_event_count=len(events), capture_event_count=sum(event['kind'] == 'capture' for event in events),
        dispatch_event_count=sum(event['kind'] == 'dispatch' for event in events) if generation else 0, operation_count=len(rows) + len(terminal_ids),
        terminal_span_ids=terminal_ids, open_span_ids=open_ids, contexts=rows)
    validate_context_topology(topology)
    if generation:
        from .rna_hosted_context_contract import validate_generation_context_profile
        validate_generation_context_profile(profile, topology=topology)
    else:
        validate_context_profile(profile, topology=topology)
    return dict(topology=topology, profile=profile)


class ContextReceiptBridge:
    """Reconstruct a parent-qualified alias map against the frozen source profile."""
    _generation = False
    def __init__(self, *, authorized_session_manifest, output_path):
        from .rna_langsmith_live import ContextReceiptOperationBridge, validate_live_session
        self.session = validate_live_session(authorized_session_manifest)
        if self.session['format'] != (GENERATION_CONTEXT_SESSION_FORMAT if self._generation else CONTEXT_SESSION_FORMAT):
            raise ValueError('Context observation session /4 required')
        self.path = _absolute(output_path)
        self.bridge = ContextReceiptOperationBridge(authorized_session_manifest=self.session, output_path=self.path)
        self.source_event_digests = []

    def emit_new(self, *, observer, workflow_id):
        from devbench.analysis.rna_trace_contract import trace_digest
        from .rna_langsmith_live import context_event_terminal_status, project_context_receipt
        if trace_digest(workflow_id) != self.session['workflow_binding_digest']:
            raise ValueError('Frozen context workflow binding required')
        events, actual, grouped, ordered = _actual_context_sources(observer, workflow_id, generation=self._generation)
        if any(observer.identities[key] != self.session[key] for key in ('source_digest', 'runtime_digest', 'policy_digest')):
            raise ValueError('Actual context source/runtime/policy changed')
        profile = self.session['context_profile']
        if len(ordered) != profile['context_count'] or len(events) != profile['source_event_count']:
            raise ValueError('Frozen actual context/source profile changed')
        topology = {row['span_id']: row for row in self.session['span_topology']}
        frozen_ids = set(topology) | {row['context_alias'] for row in profile['contexts']}
        if frozen_ids & _context_raw_ids(events, actual):
            raise ValueError('Fresh opaque context aliases must exclude every raw observer identity')
        matched, aliases, projections = set(), {}, {}
        for identity in ordered:
            context = actual[identity]
            parent_alias = aliases.get(context['parent_span_id'])
            candidates = []
            for row in profile['contexts']:
                slot = topology[row['span_id']]
                if (row['span_id'] in matched or row['parent_context_alias'] != parent_alias
                        or any(slot[key] != context[key] for key in ('role', 'phase', 'attempt'))):
                    continue
                values = [project_context_receipt(event, context_alias=row['context_alias'],
                    parent_context_alias=parent_alias) for event in grouped[identity]]
                if ([digest(canonical_json_bytes(event)) for event in values] == row['source_event_digests']
                        and row['start_observed'] == (values[0]['kind'] in {'reservation', 'dispatch'})
                        and row['source_event_count'] == len(values)
                        and row['capture_event_count'] == sum(event['kind'] == 'capture' for event in values)
                        and (not self._generation or row['dispatch_event_count'] == sum(event['kind'] == 'dispatch' for event in values))
                        and row['terminal_status'] == next((context_event_terminal_status(event)
                            for event in reversed(values) if context_event_terminal_status(event) is not None), None)):
                    candidates.append((row, values))
            if len(candidates) != 1:
                raise ValueError('Actual context does not match one frozen parent-qualified profile')
            row, values = candidates[0]
            matched.add(row['span_id'])
            aliases[identity] = row['context_alias']
            projections[identity] = values
        remapped = [projections[event['span_id']][event['sequence']] for event in events]
        class ProjectedObserver:
            def read_events(self, _workflow_id):
                return deepcopy(remapped)
        emitted = self.bridge.emit_new(observer=ProjectedObserver(), workflow_id=workflow_id)
        self.source_event_digests = [digest(canonical_json_bytes(event)) for event in events]
        return emitted


class _ContextRESTTransport:
    def __init__(self, workflow):
        if type(workflow) not in {HostedContextObservation, HostedGenerationContextObservation}:
            raise PermissionError('Actual admitted original-child context observation required')
        self.workflow = workflow
        self.session = workflow._authorize_transport()
        if self.session['format'] != (GENERATION_CONTEXT_SESSION_FORMAT if workflow._generation else CONTEXT_SESSION_FORMAT):
            raise PermissionError('Immutable admitted context session required')

    def __call__(self, *, method, path, payload, timeout):
        if self.workflow._authorize_transport() != self.session:
            raise PermissionError('Immutable admitted context session required')
        if method not in {'POST', 'PATCH'} or path != ('/runs' if method == 'POST' else '/runs/' + _uuid(payload.get('id'))):
            raise ValueError('Exact context mutation route required')
        response = self.workflow._request(method=method, path='/api/v1' + path, payload=payload, timeout=timeout)
        if response['status_code'] == 202 or 400 <= response['status_code'] < 500:
            return {'status_code': response['status_code']}
        raise OSError('Context mutation unacknowledged')


class HostedContextObservation(HostedWorkflow):
    """Finite observations of retained contexts, including explicitly open roots."""
    _generation = False
    def __init__(self, descriptor):
        from . import rna_hosted_exporter as exporter
        from .rna_hosted_context_contract import (packet_digest, read_approval_handle,
            _admit_context_packet, _validate_context_packet)
        if exporter._CHILD_PROCESS_PID != os.getpid():
            raise PermissionError('Original exporter child required')
        descriptor = _validate_context_descriptor(descriptor, generation=self._generation)
        self.packet, self.handles = descriptor['packet'], descriptor['approval_handles']
        self.root = _absolute(self.packet['evidence_path'])
        self.pid, self.packet_sha = os.getpid(), packet_digest(self.packet)
        if _absolute(Path.cwd()) != self.root.parent:
            raise ValueError('Approved context child working directory required')
        self.ledger = self.root / 'connection.sqlite'
        self._credential = None
        self.credential_resolutions = 0
        self.box = self.exporter = self.verifier = self.project_receipt = None
        self._old_timer = self._old_handler = self._root_identity = None
        self.started = self.persisted = None
        self._closed = False
        self._current_operation = None
        self.starts, self.finishes, self._verified = {}, {}, set()
        self.observations = self.project_reads = 0
        self._run_reads, self._extra_polls = {}, 0
        self._context_read_failures = {}
        if self.root.exists():
            review = read_approval_handle(self.handles['source_runtime_review']) if self.handles else None
            if (review is None or review.get('packet_digest') != self.packet_sha
                    or self.packet['packet_id'] not in self._consumed_ids()):
                raise PermissionError('Retained context observation cannot be reopened')
            self.admission = dict(status='blocked', blockers=['packet_already_consumed'],
                authorize_upload=False, authorize_remote_read=False)
            return
        self.identity = generation_context_identity() if self._generation else context_identity()
        review = read_approval_handle(self.handles['source_runtime_review']) if self.handles else None
        self.expected_binding = deepcopy(review['binding'] if review else self.packet['binding'])
        self.expected_target = deepcopy(review['target'] if review else self.packet['target'])
        self.packet = _validate_context_packet(self.packet, installed_identity=self.identity, generation=self._generation,
            expected_binding=self.expected_binding, expected_target=self.expected_target, now=self._now())
        prefix = Path(sys.prefix).resolve()
        if self.root.is_relative_to(prefix) or prefix.is_relative_to(self.root):
            raise ValueError('Context output overlaps installed runtime')
        self.admission = _admit_context_packet(self.packet, installed_identity=self.identity, generation=self._generation,
            expected_binding=self.expected_binding, expected_target=self.expected_target,
            now=self._now(), approval_handles=self.handles)
        if self.admission['status'] != 'admitted':
            return
        self._reserve()
        self._arm_deadline()
        try:
            self._revalidate()
            self._credential = exporter._resolve_keychain_credential()
            self.credential_resolutions = 1
            self.session = {**self.packet['session'], 'authorize_upload': True}
            self.execution_session_digest = digest(canonical_json_bytes(self.session))
            self._project_join()
        except BaseException as error:
            reason = 'initialization_failed'
            try:
                self._check_deadline()
            except TimeoutError:
                reason = 'budget_exhausted'
            self._stop(reason)
            if isinstance(error, exporter.CredentialUnavailable) and error.reason_code in exporter._CREDENTIAL_FAILURE_CODES:
                try:
                    _new(self.root / 'initialization-failure.json', dict(format='devbench.rna-credential-failure/1',
                        stage='credential_resolution', reason=error.reason_code))
                except Exception:
                    pass
            self.close()
            raise

    def _revalidate(self):
        from . import rna_hosted_exporter as exporter
        from .rna_hosted_context_contract import _validate_context_approvals
        if self._closed or exporter._CHILD_PROCESS_PID != self.pid or self.pid != os.getpid():
            raise PermissionError('Active original child context observation required')
        if self.admission['status'] != 'admitted':
            raise PermissionError('Admitted context observation required')
        self._check_deadline()
        with self._db() as db:
            if db.execute('SELECT stopped_reason FROM connection').fetchone()[0] is not None:
                raise PermissionError('Context observation stopped')
        if digest(canonical_json_bytes(self.packet)) != self.packet_sha or (generation_context_identity() if self._generation else context_identity()) != self.identity:
            raise PermissionError('Immutable reviewed context and installed identity required')
        checked = _validate_context_approvals(self.packet, generation=self._generation, expected_binding=self.expected_binding,
            expected_target=self.expected_target, now=self._now(), approval_handles=self.handles)
        if checked != self.admission['approval_record_digests']:
            raise PermissionError('Exact retained context approval records required')

    def _request(self, *, method, path, payload, timeout):
        from .rna_hosted_tls import verified_tls_context
        self._revalidate()
        reads = self.packet['read_descriptor']
        if method == 'GET':
            if path not in {reads['project_route'], *reads['run_routes']} or payload is not None:
                raise ValueError('Exact frozen context GET required')
        else:
            item = self._current_operation
            if item is None:
                raise PermissionError('Exact currently emitted context mutation required')
            _validate_context_wire_operation(item, packet=self.packet, generation=self._generation)
            if (method not in {'POST', 'PATCH'}
                    or method != ('POST' if item['operation']['operation'] == 'start' else 'PATCH')
                    or path != ('/api/v1/runs' if method == 'POST' else '/api/v1/runs/' + item['payload']['id'])
                    or canonical_json_bytes(payload) != canonical_json_bytes(item['payload'])):
                raise PermissionError('Exact currently emitted context mutation required')
            self._authorize_transport()
            with self.box._db() as db:
                self.box._check_payload(db)
                row = db.execute('SELECT * FROM operations WHERE operation_id=?', (item['operation']['operation_id'],)).fetchone()
                if (row is None or row['state'] != 'sending' or row['attempt_count'] != 1
                        or row['payload'] != canonical_json_bytes(payload)
                        or row['envelope'] != canonical_json_bytes(item['operation'])):
                    raise PermissionError('One exact actual context outbox attempt required')
                self._validate_context_order(item, db=db)
        raw = canonical_json_bytes(payload) if payload is not None else None
        operation_bytes = len(canonical_json_bytes(self._current_operation)) if method != 'GET' else 0
        limits = self.packet['limits']
        counter = 'reads' if method == 'GET' else 'mutations'
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM connection').fetchone()
            remaining_bytes = limits['max_response_bytes'] - row['response_bytes']
            exhausted = (row[counter] >= limits['max_read_requests' if method == 'GET' else 'max_mutation_requests']
                or row['requests'] >= limits['max_total_requests'] or remaining_bytes <= 0
                or row['payload_bytes'] + operation_bytes > limits['max_bytes'])
            if exhausted:
                db.execute("UPDATE connection SET stopped_reason='budget_exhausted'")
            else:
                index = row['requests'] + 1
                db.execute('UPDATE connection SET ' + counter + '=' + counter + '+1,requests=requests+1,payload_bytes=payload_bytes+?', (operation_bytes,))
        if exhausted:
            raise PermissionError('Context request or byte cap reached')
        target = self.packet['target']
        request = Request(target['endpoint'] + path, data=raw, method=method,
            headers={'Content-Type': 'application/json', 'Accept': 'application/json',
                'x-api-key': self._credential, 'x-tenant-id': target['workspace_id']})
        bound_timeout = max(.001, min(15, float(timeout), self._check_deadline()))
        body = b''
        complete, oversized, observed_bytes = False, False, 0
        response = None
        try:
            context = verified_tls_context()
            if context.verify_mode != ssl.CERT_REQUIRED or context.check_hostname is not True:
                raise PermissionError('Verified hostname TLS context required')
            try:
                response_owner = build_opener(ProxyHandler({}), _NoRedirect(), _ContextHTTPSHandler(context=context)).open(request, timeout=bound_timeout)
            except HTTPError as error:
                response_owner = error  # Keep urllib's closing owner alive through the strict fp read.
            response = response_owner.fp if isinstance(response_owner, HTTPError) else response_owner
            with response_owner:
                status = response_owner.code if isinstance(response_owner, HTTPError) else response.status
                framed = _read_context_http_body(response, limit=min(131072, remaining_bytes))
                body, complete, oversized = framed['body'], framed['complete'], framed['oversized']
                observed_bytes = response._devbench_context_body_bytes
        except Exception as error:
            # A timeout/reset can follow successful bounded body reads. Charge
            # their known bytes before discarding content and denying proof.
            known_bytes = getattr(response, '_devbench_context_body_bytes', 0)
            if type(known_bytes) is not int or known_bytes < 0:
                known_bytes = 0
            if response is not None:
                with self._db() as db:
                    db.execute('UPDATE connection SET response_bytes=response_bytes+?', (known_bytes,))
                    # fp.read may consume an unreturned prefix before raising.
                    # Known bytes are a lower bound; no subsequent effect can
                    # spend the unmeasured remainder of this read allocation.
                    db.execute("UPDATE connection SET stopped_reason=coalesce(stopped_reason,'body_read_uncertain')")
                try:
                    _new(self.root / ('body-read-failure-' + str(index) + '.json'),
                        dict(format='devbench.rna-context-body-read-failure/4', request_index=index,
                            known_body_bytes=known_bytes, total_body_bytes_known=False,
                            additional_reads_authorized=False))
                except Exception:
                    pass
                if method == 'GET' and path in reads['run_routes']:
                    from .rna_langsmith_verify import validate_context_response_diagnostic
                    identity = path.rsplit('/', 1)[1]
                    self._context_read_failures[identity] = validate_context_response_diagnostic(dict(
                        span_id=identity, body_length=None, body_sha256=None, mismatch_category='body_incomplete'))
            reason = error.reason if isinstance(error, URLError) else error
            family = ('tls_verification_failed' if isinstance(reason, ssl.SSLCertVerificationError)
                else 'transport_timeout' if isinstance(reason, TimeoutError) else 'transport_unavailable')
            self._retain_http_attempt(index, method, path, None, family)
            raise OSError('Context request unacknowledged') from None
        valid = type(status) is int and 100 <= status <= 599
        family = ('accepted' if valid and 200 <= status < 300 else 'auth_denied' if status in (401, 403)
            else 'not_found' if status == 404 else 'conflict' if status == 409
            else 'rate_limited' if status == 429 else 'client_rejected' if valid and 400 <= status < 500
            else 'server_error' if valid and status >= 500 else 'redirect' if valid and 300 <= status < 400
            else 'invalid_response')
        if oversized:
            family = 'response_oversized'
        elif not complete:
            family = 'invalid_response'
        with self._db() as db:
            db.execute('UPDATE connection SET response_bytes=response_bytes+?', (observed_bytes,))
            if status in (401, 403):
                db.execute("UPDATE connection SET stopped_reason='auth_denied'")
            elif oversized:
                db.execute("UPDATE connection SET stopped_reason='budget_exhausted'")
        self._retain_http_attempt(index, method, path, status if valid else None, family)
        self._check_deadline()
        if not complete or oversized:
            if method == 'GET' and path in reads['run_routes']:
                from .rna_langsmith_verify import validate_context_response_diagnostic
                identity = path.rsplit('/', 1)[1]
                self._context_read_failures[identity] = validate_context_response_diagnostic(dict(
                    span_id=identity, body_length=None, body_sha256=None,
                    mismatch_category='body_oversized' if oversized else 'body_incomplete'))
            raise ValueError('Complete bounded context HTTP response required')
        if not valid:
            raise ValueError('Bounded context HTTP response required')
        return {'status_code': status, 'body': body}

    def _retain_http_attempt(self, index, method, path, status, family):
        allowed = {'accepted', 'auth_denied', 'not_found', 'conflict', 'rate_limited', 'client_rejected',
            'server_error', 'redirect', 'invalid_response', 'response_oversized', 'transport_unavailable',
            'transport_timeout', 'tls_verification_failed'}
        if family not in allowed or type(index) is not int or not 1 <= index <= 52:
            return
        kind = ('project_read' if path == self.packet['read_descriptor']['project_route']
            else 'run_read' if method == 'GET' else 'run_start' if method == 'POST' else 'run_finish')
        try:
            _new(self.root / ('http-attempt-' + str(index) + '.json'), dict(format='devbench.rna-http-attempt/1',
                request_index=index, method=method, route_kind=kind, http_status=status, family=family))
        except Exception:
            pass

    def _validate_context_order(self, item, *, db=None):
        """Check retained full ancestry before enqueue and again before HTTP."""
        from .rna_langsmith_live import _root_dotted_order
        identity, kind = item['payload']['id'], item['operation']['operation']
        payload = item['payload']
        parent = payload.get('parent_run_id')
        if db is None:
            if self.box is None:
                if kind != 'start' or identity != self.packet['topology'][0]['span_id']:
                    raise ValueError('Context root observation must be first')
                return
            with self.box._db() as retained:
                return self._validate_context_order(item, db=retained)
        if parent is not None:
            row = db.execute("SELECT payload,state FROM operations WHERE span_id=? AND operation='start'", (parent,)).fetchone()
            if row is None or row['state'] != 'acknowledged':
                raise ValueError('Parent context observation must be acknowledged')
            parent_start = json.loads(row['payload'])
            if payload['dotted_order'] != parent_start['dotted_order'] + '.' + _root_dotted_order(payload['start_time'], identity):
                raise ValueError('Exact retained parent context order required')
            if kind == 'start' and db.execute("SELECT 1 FROM operations WHERE span_id=? AND operation='finish'", (parent,)).fetchone():
                raise ValueError('No context observation after terminal parent')
        if kind == 'finish':
            row = db.execute("SELECT payload,state FROM operations WHERE span_id=? AND operation='start'", (identity,)).fetchone()
            if row is None or row['state'] != 'acknowledged':
                raise ValueError('Acknowledged context observation required before terminal')
            if payload['dotted_order'] != json.loads(row['payload'])['dotted_order']:
                raise ValueError('Exact retained terminal context order required')
            terminal = set(self.packet['profile']['terminal_span_ids'])
            children = [row['span_id'] for row in self.packet['topology'] if row['parent_span_id'] == identity]
            if any(not self._acknowledged(child, 'start')
                    or child in terminal and not self._acknowledged(child, 'finish') for child in children):
                raise ValueError('Declared child context revisions must be acknowledged')

    def emit(self, item):
        from .rna_langsmith_live import LiveTraceExporter
        item = _validate_context_wire_operation(item, packet=self.packet, generation=self._generation)
        self._authorize_transport()
        identity, kind = item['payload']['id'], item['operation']['operation']
        if self.box is not None:
            with self.box._db() as db:
                if db.execute('SELECT 1 FROM operations WHERE operation_id=? OR (span_id=? AND revision=?)',
                        (item['operation']['operation_id'], identity, item['operation']['revision'])).fetchone():
                    raise ValueError('Duplicate existing context operation refused')
                if db.execute('SELECT count(*) FROM operations').fetchone()[0] >= self.packet['profile']['operation_count']:
                    raise ValueError('Frozen context operation count reached')
        if kind == 'start' and identity in self.starts or kind == 'finish' and identity in self.finishes:
            raise ValueError('Duplicate context lifecycle operation refused')
        self._validate_context_order(item)
        if self.box is None:
            document = dict(record_format='devbench.rna-langsmith-export/1', privacy_policy='rna-allowlist/1',
                data_scope='synthetic', runs=[item['payload']])
            directory = self.root / 'payload'
            directory.mkdir(mode=0o700, exist_ok=False)
            path = directory / 'payload.json'
            _new(path, document)
            self.persisted = time.monotonic_ns()
            self.box = ExportOutbox.create_live(self.root / 'outbox' / 'journal.sqlite', payload_path=path,
                expected_sha256=digest(canonical_json_bytes(document)), authorized_session_manifest=self.session,
                initial_operations=[item])
            self.exporter = LiveTraceExporter.open(authorized_session_manifest=self.session, outbox=self.box,
                sanitized_event_source=(), transport=_ContextRESTTransport(self))
        else:
            self.box.enqueue_operation(item['operation'], item['payload'], max_spans=self.packet['limits']['max_spans'],
                max_bytes=self.packet['limits']['max_bytes'])
        if kind == 'start':
            self.starts[identity] = item['payload']
        else:
            self.finishes[identity] = item
        self._current_operation = item
        try:
            self.exporter.drain_once(max_operations=1, deadline_seconds=min(5, self._check_deadline()))
        finally:
            self._current_operation = None
        if not self._acknowledged(identity, kind):
            with self.box._db() as db:
                state = db.execute('SELECT state FROM operations WHERE operation_id=?', (item['operation']['operation_id'],)).fetchone()[0]
            self._stop('delivery_rejected' if state in {'rejected', 'blocked'} else 'delivery_uncertain')
        return self.status()

    def _read_transport(self, *, method, path, payload, timeout):
        if method != 'GET' or payload is not None or path not in {'/runs/' + value for value in self.starts}:
            raise ValueError('Exact current context GET required')
        identity = path.rsplit('/', 1)[1]
        count = self._run_reads.get(identity, 0)
        if count:
            if self._extra_polls >= 2:
                raise PermissionError('Context polling read cap reached')
            self._extra_polls += 1
        self._run_reads[identity] = count + 1
        return self._request(method='GET', path='/api/v1' + path, payload=None, timeout=timeout)

    def _make_verifier(self):
        from .rna_hosted_context_contract import CONTEXT_CRITERIA, GENERATION_CONTEXT_CRITERIA
        from .rna_langsmith_verify import ContextRunVerifier
        limits = {key: self.packet['limits'][key] for key in ('max_spans', 'max_bytes',
            'max_mutation_requests', 'max_read_requests', 'max_delivery_attempts', 'total_deadline_seconds')}
        manifest = dict(format='devbench.rna-hosted-qualification/1', qualification_id=self.packet['packet_id'],
            mode='hosted', data_scope='synthetic', **self.identity, output_path=str(self.root),
            **self.packet['target'], live_session_digest=self.execution_session_digest,
            expires_at=self.packet['expires_at'], allowed_operations=['create_run', 'update_run', 'read_run'],
            authorize_upload=True, authorize_remote_read=True,
            authorization_record_digest=digest(canonical_json_bytes(self.admission['approval_record_digests'])), limits=limits)
        self.verifier = ContextRunVerifier(manifest=manifest, criteria=(GENERATION_CONTEXT_CRITERIA if self._generation else CONTEXT_CRITERIA)['verifier_criteria'],
            api_key=self._credential, read_transport=self._read_transport, project_receipt=self.project_receipt,
            expected_context=self.context, expected_topology=self.starts)
        self.verifier.bind_session(self.session)

    def verify(self):
        from .rna_langsmith_verify import context_response_diagnostic
        profile = self.packet['profile']
        terminal = set(profile['terminal_span_ids'])
        if (len(self.starts) != profile['context_count'] or set(self.finishes) != terminal
                or any(not self._acknowledged(identity, 'start') for identity in self.starts)
                or any(not self._acknowledged(identity, 'finish') for identity in terminal)):
            raise ValueError('Every frozen current context revision must be acknowledged')
        if self.observations:
            raise PermissionError('Context verification already attempted')
        self._revalidate()
        self._project_join()
        self._make_verifier()
        self.observations = 1
        receipts = []
        for span in self.packet['topology']:
            identity = span['span_id']
            if identity in self.finishes:
                item = self.finishes[identity]
            else:
                with self.box._db() as db:
                    row = db.execute("SELECT envelope,payload FROM operations WHERE span_id=? AND operation='start'", (identity,)).fetchone()
                item = dict(operation=json.loads(row['envelope']), payload=json.loads(row['payload']))
            try:
                try:
                    self._revalidate()
                    self.project_receipt.validate(expected_context=self.context, now=self._now())
                except ValueError:
                    self._revalidate()
                    self._project_join()
                    self.project_receipt.claim(expected_context=self.context, consumer=self.verifier)
                    self.verifier._project_receipt = self.project_receipt
                result = self.verifier.observe(operation=item['operation'], expected_payload=item['payload'],
                    persisted_monotonic_ns=self.persisted)
            except Exception:
                with self._db() as db:
                    stopped = db.execute('SELECT stopped_reason FROM connection').fetchone()[0]
                result = self.verifier._receipt(item['operation'], item['payload'], self.persisted,
                    None, 'auth_denied' if stopped == 'auth_denied' else 'invalid_response', 'blocked')
            receipts.append(result)
            if identity in self._context_read_failures:
                failure = self._context_read_failures.pop(identity)
                positions = [index for index, row in enumerate(self.verifier.diagnostics) if row['span_id'] == identity]
                if positions:
                    self.verifier.diagnostics[positions[-1]] = failure
                else:
                    self.verifier.diagnostics.append(failure)
            if not any(row['span_id'] == identity for row in self.verifier.diagnostics):
                self.verifier.diagnostics.append(context_response_diagnostic(None, item['payload'],
                    decoder_result='invalid_response', body_complete=False))
            _new(self.root / ('observation-' + str(len(receipts)) + '.json'), result)
        self._verified = {row['span_id'] for row in receipts if row['verification_result'] == 'exact_revision_match'}
        result = _validate_context_verification(dict(format='devbench.rna-hosted-context-verification/5' if self._generation else CONTEXT_VERIFICATION_FORMAT, packet_digest=self.packet_sha,
            verification_result='exact_context_match' if len(self._verified) == profile['context_count'] else 'context_mismatch',
            verified_span_count=len(self._verified), receipts=receipts, diagnostics=self.verifier.diagnostics), generation=self._generation)
        _new(self.root / 'verification.json', result)
        return result

    def status(self):
        counts = {key: 0 for key in STATUS_KEYS}
        if self.box is not None:
            source = self.box.status()['counts']
            for key in STATUS_KEYS[:5]:
                counts[key] = source.get(key, 0)
            counts['uncertain'] += source.get('sending', 0)
        stopped = None
        if self.ledger.exists():
            with self._db() as db:
                row = db.execute('SELECT mutations,reads,stopped_reason FROM connection').fetchone()
            if row:
                counts.update(mutation_requests=row[0], read_requests=row[1])
                stopped = row[2]
        active = self.admission['status'] == 'admitted' and not self._closed and stopped is None
        blockers = list(self.admission['blockers'])
        if self.admission['status'] == 'admitted':
            if stopped is not None:
                blockers = [stopped]
            elif self._closed:
                blockers = ['closed']
        if active:
            try:
                self._revalidate()
            except TimeoutError:
                active, blockers = False, ['budget_exhausted']
            except (ValueError, PermissionError):
                active, blockers = False, ['input_drift']
        return _validate_context_status(dict(format='devbench.rna-hosted-exporter-status/5' if self._generation else CONTEXT_STATUS_FORMAT, status='admitted' if active else 'blocked',
            blockers=blockers, credential_resolutions=self.credential_resolutions,
            authorize_upload=active, authorize_remote_read=active, schema_mode='experimental_generation_context_observation' if self._generation else 'experimental_context_observation',
            deployed_schema_qualified=False, packet_digest=self.packet_sha, counts=counts,
            live_span_count=len(self.starts), terminal_span_count=len(self.finishes), verified_span_count=len(self._verified),
            source_event_count=self.packet['profile']['source_event_count'],
            capture_event_count=self.packet['profile']['capture_event_count'],
            **({'dispatch_event_count': self.packet['profile']['dispatch_event_count']} if self._generation else {})), generation=self._generation)


def _read_context_http_body(response, *, limit):
    """Read a bounded whole HTTP body; a short prefix proves no body content.

    Eligible framing is closed explicit Content-Length exhaustion or the shared
    strict chunk/trailer predicate. Close-delimited EOF proves no whole body.
    Numeric accounting survives discarded incomplete/oversized content.
    """
    from http.client import HTTPException, IncompleteRead
    from .rna_hosted_tls import strict_chunked_response_complete
    if type(limit) is not int or not 0 <= limit <= 131072:
        raise ValueError('Bounded context HTTP body limit required')
    length, chunked = getattr(response, 'length', object()), getattr(response, 'chunked', None)
    if (type(chunked) is not bool or not callable(getattr(response, 'isclosed', None))
            or length is not None and (type(length) is not int or length < 0)):
        raise ValueError('Actual HTTP response framing required')
    body = bytearray()
    response._devbench_context_body_bytes = 0
    while len(body) <= limit:
        try:
            # Single payload-byte chunk reads keep byte accounting exact when
            # the strict parser rejects framing before returning its next byte.
            value = response.read(min(1 if chunked else 65536, limit + 1 - len(body)))
        except HTTPException as error:
            if isinstance(error, IncompleteRead):
                response._devbench_context_body_bytes += min(limit + 1 - len(body), len(error.partial))
            return dict(body=b'', complete=False, oversized=False)
        if type(value) is not bytes:
            raise ValueError('Bounded HTTP body bytes required')
        response._devbench_context_body_bytes += len(value)
        body.extend(value)
        if len(body) > limit:
            return dict(body=b'', complete=False, oversized=True)
        remaining = response.length
        if (not chunked and remaining == 0 and response.isclosed()
                or chunked and strict_chunked_response_complete(response)):
            return dict(body=bytes(body), complete=True, oversized=False)
        if not value:
            return dict(body=b'', complete=False, oversized=False)
    return dict(body=b'', complete=False, oversized=True)


def _context_raw_ids(events, actual):
    identities = set()
    for context in actual.values():
        identities.update(context[key] for key in ('span_id', 'trace_id', 'parent_span_id', 'workflow_alias')
                          if context[key] is not None)
    for event in events:
        identities.update(event[key] for key in ('event_id', 'span_id', 'trace_id', 'parent_span_id')
                          if event[key] is not None)
        identities.add(event['clock']['process_alias'])
    return identities


class _ContextHTTPSHandler(HTTPSHandler):
    """Select shared strict framing on this connection only, with verified TLS."""
    def https_open(self, request):
        from http.client import HTTPSConnection
        from .rna_hosted_tls import StrictTrailerHTTPResponse
        class ContextHTTPSConnection(HTTPSConnection):
            response_class = StrictTrailerHTTPResponse
        return self.do_open(ContextHTTPSConnection, request, context=self._context)


# /5 is an explicit route. Historical /4 entrypoints retain their closed schema.
def generation_context_identity():
    from .rna_hosted_context_contract import generation_context_installed_identity
    return generation_context_installed_identity()


def validate_context_descriptor(value):
    return _validate_context_descriptor(value)


def validate_generation_context_descriptor(value):
    return _validate_context_descriptor(value, generation=True)


def validate_context_wire_operation(item, *, packet):
    return _validate_context_wire_operation(item, packet=packet)


def validate_generation_context_wire_operation(item, *, packet):
    return _validate_context_wire_operation(item, packet=packet, generation=True)


def validate_context_status(value):
    return _validate_context_status(value)


def validate_generation_context_status(value):
    return _validate_context_status(value, generation=True)


def validate_context_verification(value):
    return _validate_context_verification(value)


def validate_generation_context_verification(value):
    return _validate_context_verification(value, generation=True)


def prepare_context_observation(*, observer, workflow_id):
    return _prepare_context_observation(observer=observer, workflow_id=workflow_id)


def prepare_generation_context_observation(*, observer, workflow_id):
    """Freeze actual source dispatches; no generation, fitting, upload or read."""
    return _prepare_context_observation(observer=observer, workflow_id=workflow_id, generation=True)


class GenerationContextReceiptBridge(ContextReceiptBridge):
    _generation = True


class HostedGenerationContextObservation(HostedContextObservation):
    """Separately approved /5 observations; no generation authority is granted."""
    _generation = True
