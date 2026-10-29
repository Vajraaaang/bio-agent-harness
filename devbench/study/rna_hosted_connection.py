"""Default-off experimental connection, owned by the original exporter child.

Trusted launcher-selected operator files supply independent expectations. Their
hashes establish integrity, not cryptographic human authentication. Public API
schemas plus a fresh project join permit a separately approved bootstrap; they
do not establish deployed run-schema, OS isolation, or scientific qualification.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import signal
import sqlite3
import ssl
import stat
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener, ProxyHandler
from uuid import uuid4

from .contracts import canonical_json_bytes
from .langsmith_export import _NoRedirect, _time
from .langsmith_outbox import ExportOutbox, digest
from .rna_hosted_qualification import STATUS_KEYS, _absolute


STATUS_FORMAT = 'devbench.rna-hosted-exporter-status/2'
STATUS_FIELDS = frozenset({'format','status','blockers','credential_resolutions',
    'authorize_upload','authorize_remote_read','schema_mode','deployed_schema_qualified',
    'packet_digest','counts'})


def connection_identity() -> dict:
    """Hash exact installed product RECORD/resources and fixed connection inputs."""
    from .rna_hosted_connection_contract import connection_installed_identity
    return connection_installed_identity()


def validate_descriptor(value) -> dict:
    from .rna_hosted_connection_contract import read_approval_handle,validate_connection_wire_packet
    if (not isinstance(value,dict) or set(value) != {'format','packet','approval_handles'}
            or value['format'] != 'devbench.rna-hosted-exporter-session/2'):
        raise ValueError('Closed connection descriptor required')
    if len(canonical_json_bytes(value)) > 131072 or not isinstance(value['packet'],dict):
        raise ValueError('Bounded connection descriptor required')
    handles = value['approval_handles']
    if not isinstance(handles,dict) or set(handles) not in (set(),
            {'source_runtime_review','target_read','post_run_payload'}):
        raise ValueError('Separate closed operator handles required')
    # Parent validation reads only the explicitly selected trusted local records.
    # The child revalidates them and its own independent installed identity.
    for handle in handles.values(): read_approval_handle(handle)
    validate_connection_wire_packet(value['packet'],now=datetime.now(timezone.utc))
    return json.loads(canonical_json_bytes(value))


def validate_status(value) -> dict:
    if not isinstance(value,dict) or set(value) != STATUS_FIELDS:
        raise ValueError('Closed connection status required')
    if (value['format'] != STATUS_FORMAT or value['status'] not in {'admitted','blocked'}
            or value['schema_mode'] != 'experimental_joined_binding_bootstrap'
            or value['deployed_schema_qualified'] is not False):
        raise ValueError('Experimental connection status required')
    from .langsmith_outbox import _digest
    _digest(value['packet_digest'])
    if type(value['credential_resolutions']) is not int or value['credential_resolutions'] not in (0,1):
        raise ValueError('Bounded credential count required')
    if any(type(value[k]) is not bool for k in ('authorize_upload','authorize_remote_read')):
        raise ValueError('Closed authority indicators required')
    if value['authorize_upload'] != (value['status']=='admitted') or value['authorize_remote_read'] != value['authorize_upload']:
        raise ValueError('Connection authority/status mismatch')
    blockers = value['blockers']
    allowed = {'trusted_operator_approval_handles_absent','source_runtime_review_absent',
        'target_read_approval_absent','post_run_payload_approval_absent',
        'packet_already_consumed','budget_exhausted','auth_denied','initialization_failed'}
    if not isinstance(blockers,list) or any(type(x) is not str or x not in allowed for x in blockers):
        raise ValueError('Closed blockers required')
    counts = value['counts']
    if not isinstance(counts,dict) or set(counts) != set(STATUS_KEYS):
        raise ValueError('Closed connection counts required')
    for key,item in counts.items():
        cap = 4 if key=='read_requests' else 2
        if type(item) is not int or not 0 <= item <= cap:
            raise ValueError('Bounded connection counts required')
    return json.loads(canonical_json_bytes(value))


def _new(path, value):
    raw = canonical_json_bytes(value)
    if len(raw) > 65536:
        raise ValueError('Connection artifact cap exceeded')
    fd = os.open(_absolute(path), os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd,'wb') as stream:
        stream.write(raw); stream.flush(); os.fsync(stream.fileno())


class HostedConnection:
    """No caller-supplied credentials, operations, routes, or admission flags."""
    def __init__(self, descriptor):
        from .rna_hosted_connection_contract import (admit_connection_packet,
            validate_connection_packet, read_approval_handle, packet_digest)
        from . import rna_hosted_exporter as exporter
        if exporter._CHILD_PROCESS_PID != os.getpid():
            raise PermissionError('Original exporter child required')
        descriptor = validate_descriptor(descriptor)
        self.packet = descriptor['packet']; self.handles = descriptor['approval_handles']
        self.root = _absolute(self.packet['evidence_path']); self.pid = os.getpid()
        if _absolute(Path.cwd()) != self.root.parent:
            raise ValueError('Approved child working directory required')
        self._credential = None; self.credential_resolutions = 0
        self.box = self.exporter = self.verifier = self.project_receipt = None
        self._old_timer = self._old_handler = None
        self.started = None; self.persisted = None; self.observations = 0
        self._root_identity = None
        self.packet_sha = packet_digest(self.packet)
        self.ledger = self.root / 'connection.sqlite'
        if self.root.exists():
            # A consumed packet can report a closed read-only replay outcome;
            # it cannot reopen credential/network or workflow authority.
            review = read_approval_handle(self.handles['source_runtime_review']) if self.handles else None
            if (review is None or review.get('packet_digest') != self.packet_sha
                    or self.packet.get('packet_id') not in self._consumed_ids()):
                raise PermissionError('Retained connection cannot be reopened')
            self.admission=dict(status='blocked',blockers=['packet_already_consumed'],
                authorize_upload=False,authorize_remote_read=False)
            retained=self.root/'outbox'/'journal.sqlite'
            if retained.exists():
                self.box=ExportOutbox.open_readonly(retained)
            return
        identity = connection_identity()
        # Independently held review values, not a packet comparing to itself.
        review = read_approval_handle(self.handles['source_runtime_review']) if self.handles else None
        expected_binding = review['binding'] if review else self.packet.get('binding')
        expected_target = review['target'] if review else self.packet.get('target')
        self.packet = validate_connection_packet(self.packet, installed_identity=identity,
            expected_binding=expected_binding, expected_target=expected_target, now=self._now())
        prefix = Path(sys.prefix).resolve()
        if self.root.is_relative_to(prefix) or prefix.is_relative_to(self.root):
            raise ValueError('Connection output overlaps installed runtime')
        self.identity = identity
        self.expected_binding = expected_binding; self.expected_target = expected_target
        self.admission = admit_connection_packet(self.packet, installed_identity=identity,
            expected_binding=expected_binding, expected_target=expected_target, now=self._now(),
            approval_handles=self.handles)
        if self.admission['status'] != 'admitted':
            return
        # This reservation is durable and new-only even if credential lookup or
        # the subsequent project GET fails. No replay reopens the effect window.
        self._reserve()
        self._arm_deadline()
        try:
            self._revalidate()
            self._credential = exporter._resolve_keychain_credential()
            self.credential_resolutions = 1
            self.session = {**self.packet['session'], 'authorize_upload':True}
            self.execution_session_digest = digest(canonical_json_bytes(self.session))
            self._project_join()
        except BaseException as error:
            with self._db() as db:
                reason='initialization_failed'
                try: self._check_deadline()
                except TimeoutError: reason='budget_exhausted'
                db.execute('UPDATE connection SET stopped_reason=coalesce(stopped_reason,?)',(reason,))
            if (isinstance(error,exporter.CredentialUnavailable)
                    and error.reason_code in exporter._CREDENTIAL_FAILURE_CODES):
                try:
                    _new(self.root/'initialization-failure.json',dict(format='devbench.rna-credential-failure/1',
                        stage='credential_resolution',reason=error.reason_code))
                except Exception:
                    pass  # Failed diagnostic retention cannot expose private errors or reopen authority.
            self.close()
            raise

    @staticmethod
    def _now(): return datetime.now(timezone.utc)

    @contextmanager
    def _db(self):
        root_info = self.root.stat(follow_symlinks=False)
        if (not stat.S_ISDIR(root_info.st_mode) or stat.S_IMODE(root_info.st_mode)!=0o700
                or root_info.st_uid!=os.getuid() or self._root_identity is not None
                and (root_info.st_dev,root_info.st_ino)!=self._root_identity):
            raise ValueError('Stable private child root required')
        path = _absolute(self.ledger)
        info = path.stat(follow_symlinks=False)
        if (not stat.S_ISREG(info.st_mode) or info.st_size > 1024*1024
                or info.st_uid!=os.getuid() or stat.S_IMODE(info.st_mode)!=0o600 or info.st_nlink!=1):
            raise ValueError('Bounded regular connection journal required')
        db = sqlite3.connect(path, timeout=1)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA synchronous=FULL')
        try:
            with db: yield db
        finally:
            db.close()

    def _consumed_ids(self):
        if not self.ledger.exists(): return []
        with self._db() as db:
            rows = db.execute('SELECT packet_id FROM connection').fetchall()
            if len(rows)>1: raise ValueError('One connection journal required')
            return [row[0] for row in rows]

    def _reserve(self):
        from .rna_hosted_connection_contract import validate_output_path
        validate_output_path(self.root, approval_handles=self.handles)
        self.root.mkdir(mode=0o700,exist_ok=False)
        info=self.root.stat(follow_symlinks=False)
        self._root_identity=(info.st_dev,info.st_ino)
        fd = os.open(self.ledger, os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW, 0o600)
        os.close(fd)
        self.started = time.monotonic_ns()
        with self._db() as db:
            db.execute('CREATE TABLE connection(packet_id TEXT PRIMARY KEY,packet_digest TEXT,prepared_session_digest TEXT,execution_session_digest TEXT,started_ns INTEGER,mutations INTEGER,reads INTEGER,requests INTEGER,response_bytes INTEGER,payload_bytes INTEGER,stopped_reason TEXT)')
            db.execute('INSERT INTO connection VALUES(?,?,?,?,?,0,0,0,0,?,NULL)',
                (self.packet['packet_id'],self.packet_sha,self.packet['binding']['session_digest'],None,
                 self.started,len(canonical_json_bytes(self.packet['operations']))))
        # Retain only the reviewed closed packet, never credentials or raw HTTP.
        _new(self.root/'packet.json', self.packet)

    def _arm_deadline(self):
        if not hasattr(signal,'setitimer'):
            raise PermissionError('Bounded POSIX child deadline required')
        self._old_handler = signal.getsignal(signal.SIGALRM)
        self._old_timer = signal.getitimer(signal.ITIMER_REAL)
        def deadline(_signum,_frame): raise TimeoutError('Connection wall deadline')
        signal.signal(signal.SIGALRM,deadline)
        remaining = max(.001,self.packet['limits']['total_deadline_seconds']-(time.monotonic_ns()-self.started)/1e9)
        signal.setitimer(signal.ITIMER_REAL,remaining)

    def _check_deadline(self):
        if self.started is None or self._now() >= _time(self.packet['expires_at']):
            raise TimeoutError('Connection expired')
        remaining = self.packet['limits']['total_deadline_seconds']-(time.monotonic_ns()-self.started)/1e9
        if remaining <= 0: raise TimeoutError('Connection wall deadline')
        return remaining

    def _revalidate(self):
        from .rna_hosted_connection_contract import validate_connection_approvals
        self._check_deadline()
        with self._db() as db:
            if db.execute('SELECT stopped_reason FROM connection').fetchone()[0] is not None:
                raise PermissionError('Connection stopped')
        if digest(canonical_json_bytes(self.packet))!=self.packet_sha:
            raise PermissionError('Immutable reviewed packet required')
        checked = validate_connection_approvals(self.packet,
            expected_binding=self.expected_binding,expected_target=self.expected_target,
            now=self._now(),approval_handles=self.handles)
        if checked != self.admission['approval_record_digests']:
            raise PermissionError('Exact retained operator records required')

    def _request(self, *, method, path, payload, timeout):
        """Reserve each request before HTTP; never retain provider bodies/errors."""
        self._revalidate()
        target = self.packet['target']; root = self.packet['operations'][0]['payload']['id']
        allowed = {('GET',self.packet['read_descriptor']['project_route']),
            ('GET','/api/v1/runs/'+root),('POST','/api/v1/runs'),
            ('PATCH','/api/v1/runs/'+root)}
        if (method,path) not in allowed or (method=='GET' and payload is not None):
            raise ValueError('Pinned connection route required')
        if method != 'GET':
            kind = 'start' if method=='POST' else 'finish'
            expected = next(item['payload'] for item in self.packet['operations'] if item['operation']['operation']==kind)
            if canonical_json_bytes(payload) != canonical_json_bytes(expected):
                raise ValueError('Immutable approved operation required')
            if self.project_receipt is None:
                raise PermissionError('Fresh authenticated project join required')
            self.project_receipt.validate(expected_context=self.context,now=self._now())
        raw = canonical_json_bytes(payload) if payload is not None else None
        limits = self.packet['limits']; counter='reads' if method=='GET' else 'mutations'
        cap = limits['max_read_requests' if method=='GET' else 'max_mutation_requests']
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM connection').fetchone()
            if row[counter]>=cap or row['requests']>=limits['max_total_requests']:
                raise PermissionError('Connection request cap reached')
            remaining_bytes = limits['max_response_bytes'] - row['response_bytes']
            if remaining_bytes <= 0: raise PermissionError('Connection response cap reached')
            request_index=row['requests']+1
            db.execute('UPDATE connection SET '+counter+'='+counter+'+1,requests=requests+1')
        headers = {'Content-Type':'application/json','Accept':'application/json',
            'x-api-key':self._credential,'x-tenant-id':target['workspace_id']}
        request = Request(target['endpoint']+path,data=raw,headers=headers,method=method)
        bound_timeout = max(.001,min(15,float(timeout),self._check_deadline()))
        body = b''
        try:
            with build_opener(ProxyHandler({}),_NoRedirect()).open(request,timeout=bound_timeout) as response:
                status=response.status
                body=response.read(min(65537,remaining_bytes+1))
        except HTTPError as error:
            status=error.code
            error.close()
        except Exception as error:
            reason=error.reason if isinstance(error,URLError) else error
            family=('tls_verification_failed' if isinstance(reason,ssl.SSLCertVerificationError)
                else 'transport_timeout' if isinstance(reason,TimeoutError) else 'transport_unavailable')
            self._retain_http_attempt(request_index,method,path,None,family)
            raise OSError('Connection request unacknowledged') from None
        with self._db() as db:
            db.execute('UPDATE connection SET response_bytes=response_bytes+?',(len(body),))
            if status in (401,403):
                db.execute("UPDATE connection SET stopped_reason='auth_denied'")
        valid_status=type(status) is int and 100<=status<=599
        family=('accepted' if valid_status and 200<=status<300
            else 'auth_denied' if status in (401,403)
            else 'not_found' if status==404 else 'conflict' if status==409
            else 'rate_limited' if status==429
            else 'client_rejected' if valid_status and 400<=status<500
            else 'server_error' if valid_status and status>=500
            else 'redirect' if valid_status and 300<=status<400 else 'invalid_response')
        if len(body)>remaining_bytes:family='response_oversized'
        self._retain_http_attempt(request_index,method,path,status if valid_status else None,family)
        self._check_deadline()
        if len(body)>remaining_bytes:
            raise ValueError('Connection response cap exceeded')
        if type(status) is not int:
            raise ValueError('Closed HTTP response required')
        return {'status_code':status,'body':body}

    def _retain_http_attempt(self,index,method,path,status,family):
        """Closed numeric transport evidence only; never request/response content."""
        allowed={'accepted','auth_denied','not_found','conflict','rate_limited','client_rejected',
            'server_error','redirect','invalid_response','response_oversized','transport_unavailable',
            'transport_timeout','tls_verification_failed'}
        if family not in allowed or type(index) is not int or not 1<=index<=6:
            return
        kind=('project_read' if path==self.packet['read_descriptor']['project_route']
            else 'run_read' if method=='GET' else 'run_start' if method=='POST' else 'run_finish')
        try:
            _new(self.root/('http-attempt-'+str(index)+'.json'),dict(format='devbench.rna-http-attempt/1',
                request_index=index,method=method,route_kind=kind,http_status=status,family=family))
        except Exception:
            pass  # Diagnostic I/O failure never leaks raw errors or reopens delivery authority.

    def _project_join(self):
        from .rna_langsmith_binding import ProjectAttestationAuthority
        target=self.packet['target']
        self.context=dict(format='devbench.rna-project-binding-context/2',
            packet_digest=self.packet_sha,session_digest=self.execution_session_digest,
            action_digest=digest(canonical_json_bytes(self.packet['target_read'])),
            source_digest=self.identity['source_digest'],runtime_digest=self.identity['runtime_digest'],
            **target,region=self.packet['region'],expires_at=self.packet['expires_at'],
            max_age_seconds=self.packet['read_descriptor']['max_project_age_seconds'])
        self.authority=ProjectAttestationAuthority(expected_context=self.context)
        response=self._request(method='GET',path=self.packet['read_descriptor']['project_route'],payload=None,timeout=15)
        self.project_receipt=self.authority.issue_from_response(response=response,
            request_nonce=str(uuid4()),observed_at=self._now().isoformat())
        _new(self.root/'project-read.json',self.project_receipt.to_record())
        with self._db() as db:
            db.execute('UPDATE connection SET execution_session_digest=?',(self.execution_session_digest,))

    def _authorize_transport(self):
        from . import rna_hosted_exporter as exporter
        if exporter._CHILD_PROCESS_PID != self.pid or self.pid != os.getpid() or self.project_receipt is None:
            raise PermissionError('Admitted original child connection required')
        self._revalidate()
        self.project_receipt.validate(expected_context=self.context,now=self._now())
        return json.loads(canonical_json_bytes(self.session))

    def prepare(self):
        from .rna_langsmith_live import LiveTraceExporter, _admitted_rest_transport
        self._authorize_transport()
        if self.box is not None: return self.status()
        operations=self.packet['operations']; initial=[operations[0]]
        payload=dict(record_format='devbench.rna-langsmith-export/1',privacy_policy='rna-allowlist/1',
            data_scope='synthetic',runs=[initial[0]['payload']])
        directory=self.root/'payload';directory.mkdir(mode=0o700,exist_ok=False)
        path=directory/'payload.json';_new(path,payload)
        self.persisted=time.monotonic_ns()
        self.box=ExportOutbox.create_live(self.root/'outbox'/'journal.sqlite',payload_path=path,
            expected_sha256=digest(canonical_json_bytes(payload)),authorized_session_manifest=self.session,
            initial_operations=initial)
        for item in operations:
            self.box.enqueue_operation(item['operation'],item['payload'],max_spans=1,max_bytes=32768)
        self.exporter=LiveTraceExporter.open(authorized_session_manifest=self.session,outbox=self.box,
            sanitized_event_source=(),transport=_admitted_rest_transport(self))
        return self.status()

    def drain(self):
        self._authorize_transport()
        if self.exporter is None: raise ValueError('Prepared connection required')
        self.exporter.drain_once(max_operations=2,deadline_seconds=min(5,self._check_deadline()))
        return self.status()

    def _read_transport(self, *, method,path,payload,timeout):
        if method!='GET' or path!='/runs/'+self.packet['operations'][0]['payload']['id'] or payload is not None:
            raise ValueError('Exact approved run GET required')
        return self._request(method=method,path='/api/v1'+path,payload=None,timeout=timeout)

    def _make_verifier(self):
        from .rna_hosted_connection_contract import CONNECTION_CRITERIA
        from .rna_langsmith_verify import JoinedRunVerifier
        limits={k:self.packet['limits'][k] for k in ('max_spans','max_bytes','max_mutation_requests',
            'max_read_requests','max_delivery_attempts','total_deadline_seconds')}
        manifest=dict(format='devbench.rna-hosted-qualification/1',qualification_id=self.packet['packet_id'],
            mode='hosted',data_scope='synthetic',**self.identity,output_path=str(self.root),
            **self.packet['target'],live_session_digest=self.execution_session_digest,
            expires_at=self.packet['expires_at'],allowed_operations=['create_run','update_run','read_run'],
            authorize_upload=True,authorize_remote_read=True,
            authorization_record_digest=digest(canonical_json_bytes(self.admission['approval_record_digests'])),limits=limits)
        self.verifier=JoinedRunVerifier(manifest=manifest,criteria=CONNECTION_CRITERIA['verifier_criteria'],
            api_key=self._credential,read_transport=self._read_transport,project_receipt=self.project_receipt,
            expected_context=self.context)
        self.verifier.bind_session(self.session)

    def verify(self):
        self._authorize_transport()
        if self.box is None: raise ValueError('Prepared connection required')
        if self.verifier is None: self._make_verifier()
        with self.box._db() as db:
            uncertain=db.execute("SELECT * FROM operations WHERE state IN ('uncertain','sending')").fetchall()
            if len(uncertain)>1: raise ValueError('One exact uncertain revision required')
            row=uncertain[0] if uncertain else db.execute('SELECT * FROM operations ORDER BY revision DESC LIMIT 1').fetchone()
        result=self.verifier.observe(operation=json.loads(row['envelope']),expected_payload=json.loads(row['payload']),
            persisted_monotonic_ns=self.persisted)
        self.observations+=1
        _new(self.root/('observation-'+str(self.observations)+'.json'),result)
        return result

    def reconcile(self,receipt,approval_handle):
        from .rna_hosted_connection_contract import read_approval_handle
        self._authorize_transport()
        if self.box is None or self.verifier is None: raise ValueError('Fresh observed proof required')
        read_approval_handle(approval_handle)
        if receipt.get('reviewer_approval_digest')!=approval_handle['sha256']:
            raise ValueError('Separate exact review handle required')
        self.box.reconcile_reviewed(receipt,approval_record=approval_handle['path'],verifier=self.verifier)
        return self.status()

    def status(self):
        counts={k:0 for k in STATUS_KEYS}
        if self.box is not None:
            source=self.box.status()['counts']
            for key in STATUS_KEYS[:5]: counts[key]=source.get(key,0)
            counts['uncertain']+=source.get('sending',0)
        if self.ledger.exists():
            with self._db() as db:
                row=db.execute('SELECT mutations,reads,stopped_reason FROM connection').fetchone()
                if row: counts.update(mutation_requests=row[0],read_requests=row[1])
        active=self.admission['status']=='admitted'
        blockers=self.admission['blockers']
        if self.ledger.exists() and row and row[2] is not None:
            active=False;blockers=[row[2]]
        if active:
            try: self._check_deadline()
            except TimeoutError:
                active=False;blockers=['budget_exhausted']
        return validate_status(dict(format=STATUS_FORMAT,status='admitted' if active else 'blocked',
            blockers=blockers,credential_resolutions=self.credential_resolutions,
            authorize_upload=active,authorize_remote_read=active,
            schema_mode='experimental_joined_binding_bootstrap',deployed_schema_qualified=False,
            packet_digest=self.packet_sha,counts=counts))

    def close(self):
        self._credential=None
        if self.verifier is not None: self.verifier._api_key=None
        if self._old_timer is not None:
            signal.setitimer(signal.ITIMER_REAL,0)
            signal.signal(signal.SIGALRM,self._old_handler)
            signal.setitimer(signal.ITIMER_REAL,*self._old_timer)
            self._old_timer=None
