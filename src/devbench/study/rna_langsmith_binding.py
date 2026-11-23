"""Child-owned project observations for the experimental joined /2 decoder.

A serialized receipt is evidence, never authentication. Only the trusted child
may construct this authority after admission and issue from its bounded project
GET response. In-process object identity binds issuance to that child/session;
this is not an OS isolation guarantee or a portable signature.
"""
from copy import deepcopy
from datetime import datetime, timezone
import re

from .contracts import canonical_json_bytes
from .langsmith_export import _time
from .langsmith_outbox import _uuid, _digest, digest
from .rna_hosted_trace_contract import _closed

CONTEXT_FORMAT = 'devbench.rna-project-binding-context/2'
RECEIPT_FORMAT = 'devbench.rna-project-read-receipt/2'
CONTEXT_KEYS = frozenset({'format', 'packet_digest', 'session_digest', 'action_digest',
    'source_digest', 'runtime_digest', 'endpoint', 'region', 'workspace_id',
    'project_id', 'project_name', 'expires_at', 'max_age_seconds'})
PROJECT_RESPONSE_BYTES = 65536


def _timestamp(value):
    if not isinstance(value, str) or re.fullmatch(
            r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})', value) is None:
        raise ValueError('Timezone-qualified timestamp required')
    stamp = _time(value)
    if stamp is None:
        raise ValueError('Valid timestamp required')
    return stamp


def _context(value):
    _closed(value, CONTEXT_KEYS, 'joined project binding context')
    if value['format'] != CONTEXT_FORMAT:
        raise ValueError('Unsupported project binding version')
    for key in ('packet_digest', 'session_digest', 'action_digest', 'source_digest', 'runtime_digest'):
        _digest(value[key])
    for key in ('workspace_id', 'project_id'):
        _uuid(value[key])
    # The /2 bootstrap is limited to the exact endpoint/region reviewed in its
    # connection packet. Other deployments require separately reviewed scope.
    if value['endpoint'] != 'https://api.smith.langchain.com' or value['region'] != 'us':
        raise ValueError('Reviewed endpoint and region required')
    if not isinstance(value['project_name'], str) or re.fullmatch(
            r'[A-Za-z0-9][A-Za-z0-9_.-]{0,99}', value['project_name']) is None:
        raise ValueError('Bounded exact project name required')
    _timestamp(value['expires_at'])
    if type(value['max_age_seconds']) is not int or not 1 <= value['max_age_seconds'] <= 1800:
        raise ValueError('Bounded project observation age required')
    canonical_json_bytes(value)
    return deepcopy(value)


class ProjectReadReceipt:
    """Opaque process-local observation; its public record cannot recreate it."""
    __slots__ = ('_issuer', '_record')

    def __init__(self, *, issuer, record, seal):
        if seal is not issuer._seal:
            raise ValueError('Trusted project observation issuer required')
        self._issuer = issuer
        self._record = deepcopy(record)

    def to_record(self):
        return deepcopy(self._record)

    def validate(self, *, expected_context, now=None):
        return self._issuer._validate(self, expected_context=expected_context, now=now)

    def claim(self, *, expected_context, consumer, now=None):
        self.validate(expected_context=expected_context, now=now)
        if self in self._issuer._claims:
            raise ValueError('Project observation already claimed by a verifier')
        self._issuer._claims[self] = consumer


class ProjectAttestationAuthority:
    """An authority owned by one admitted child, never reconstructed from IPC."""
    def __init__(self, *, expected_context):
        self._context = _context(expected_context)
        self._seal = object()
        self._issued = {}
        self._nonces = set()
        self._claims = {}

    def issue_from_response(self, *, response, request_nonce, observed_at):
        try:
            _uuid(request_nonce)
            if request_nonce in self._nonces:
                raise ValueError('Project request nonce replay')
            observed = _timestamp(observed_at)
            now = datetime.now(timezone.utc)
            if observed > now or observed >= _timestamp(self._context['expires_at']):
                raise ValueError('Project observation outside authorization')
            if (now - observed).total_seconds() > self._context['max_age_seconds']:
                raise ValueError('Stale project response')
            _closed(response, {'status_code', 'body'}, 'bounded project response')
            if type(response['status_code']) is not int or response['status_code'] != 200:
                raise ValueError('Successful authenticated project response required')
            body = response['body']
            if type(body) not in (dict, bytes):
                raise ValueError('Bounded project object required')
            raw = body if type(body) is bytes else canonical_json_bytes(body)
            if len(raw) > PROJECT_RESPONSE_BYTES:
                raise ValueError('Project response byte ceiling exceeded')
            from .rna_langsmith_verify import _response_json
            project = _response_json(raw)
            required = {'id', 'tenant_id', 'name'}
            if not required <= set(project):
                raise ValueError('Project identity fields required')
            _uuid(project['id']); _uuid(project['tenant_id'])
            projection = {key: project[key] for key in required}
            if canonical_json_bytes(projection) != canonical_json_bytes({
                    'id': self._context['project_id'], 'tenant_id': self._context['workspace_id'],
                    'name': self._context['project_name']}):
                raise ValueError('Authenticated project target mismatch')
            record = {**self._context, 'format': RECEIPT_FORMAT,
                      'request_nonce': request_nonce,
                      'observed_at': observed.isoformat(timespec='microseconds'),
                      'observed_project': projection, 'response_digest': digest(raw)}
            receipt = ProjectReadReceipt(issuer=self, record=record, seal=self._seal)
            self._issued[receipt] = digest(canonical_json_bytes(record))
            self._nonces.add(request_nonce)
            return receipt
        except Exception:
            raise ValueError('Invalid bounded project observation') from None

    capture = issue_from_response

    def _validate(self, receipt, *, expected_context, now=None):
        try:
            if type(receipt) is not ProjectReadReceipt or receipt._issuer is not self or receipt not in self._issued:
                raise ValueError('Fresh process-local project receipt required')
            if digest(canonical_json_bytes(receipt._record)) != self._issued[receipt]:
                raise ValueError('Project receipt changed after issuance')
            expected = _context(expected_context)
            if canonical_json_bytes(expected) != canonical_json_bytes(self._context):
                raise ValueError('Independent project binding context mismatch')
            clock = datetime.now(timezone.utc) if now is None else now
            if not isinstance(clock, datetime) or clock.tzinfo is None:
                raise ValueError('Timezone-qualified verification clock required')
            observed = _timestamp(receipt._record['observed_at'])
            if clock < observed or clock >= _timestamp(expected['expires_at']):
                raise ValueError('Project receipt outside authorized interval')
            if (clock - observed).total_seconds() > expected['max_age_seconds']:
                raise ValueError('Stale project receipt')
            return deepcopy(receipt._record)
        except Exception:
            raise ValueError('Invalid trusted project receipt') from None
