"""Bounded independent content observations, never scientific continuation.

GET /runs/{id} and read-only POST /runs/query are documented by the LangSmith
API and https://docs.langchain.com/langsmith/export-traces. ID lookup can ignore
project filters, so every response is checked locally. Hosted deployment schema
and workspace-response binding remain unqualified: the hosted path fails closed
before any request. No account/project listing, credential lookup or raw logging.
"""
from copy import deepcopy
from datetime import datetime, timezone
import json
import math
import time
from uuid import uuid4
from typing import Callable
from urllib.request import Request, build_opener, ProxyHandler
from urllib.error import HTTPError
from .langsmith_export import ENDPOINTS, _NoRedirect
from .contracts import canonical_json_bytes
from .langsmith_outbox import _uuid, _digest, digest
from .langsmith_export import _time
from .rna_hosted_trace_contract import (REMOTE_PROOF_KEYS, validate_criteria,
    validate_reconciliation, validate_limits, _closed, _copy, OPERATIONS)

MAX_RESPONSE_BYTES=262144
REMOTE_FIELDS=('id','trace_id','parent_run_id','session_id','name','run_type','tags','start_time','end_time','inputs','outputs','extra')
# Reviewed local schema snapshot: official read routes and bounded query fields;
# deployment compatibility and independent workspace attestation are not proven.
NORMALIZER_FORMAT="devbench.rna-remote-normalizer/1"
READ_SCHEMA_SNAPSHOT={'format':'devbench.langsmith-read-schema/1','references':['https://api.smith.langchain.com/redoc','https://docs.langchain.com/langsmith/export-traces'],'get_route':'/runs/{run_id}','query_route':'/runs/query','query_keys':['id','session','trace','select','limit'],'query_limit':1,'response_fields':list(REMOTE_FIELDS),'deployment_qualified':False,'workspace_binding_qualified':False}
READ_SCHEMA_DIGEST=digest(canonical_json_bytes(READ_SCHEMA_SNAPSHOT))


def normalized_payload(payload):
    """Frozen allowlisted semantic vector; unknown provider fields are excluded."""
    result={key:deepcopy(payload[key]) for key in REMOTE_FIELDS if key in payload}
    for key in ('start_time','end_time'):
        if key == 'end_time' and result.get(key) is None:
            result.pop(key, None)
            continue
        if key in result:
            stamp=_time(result[key])
            if stamp is None:raise ValueError('Timestamp representation unsupported')
            result[key]=stamp.astimezone(timezone.utc).isoformat(timespec='microseconds')
    extra=result.get('extra')
    if not isinstance(extra,dict) or not isinstance(extra.get('metadata'),dict):raise ValueError('Metadata missing')
    result['extra']={'metadata':deepcopy(extra['metadata'])}
    result['extra']['metadata'].pop('remote_proof',None)
    return result


def semantic_digest(payload):
    return digest(canonical_json_bytes(normalized_payload(payload)))


def validate_remote_proof(payload):
    proof=payload.get('extra',{}).get('metadata',{}).get('remote_proof')
    _closed(proof,REMOTE_PROOF_KEYS,'versioned remote proof')
    _uuid(proof['operation_id']);_digest(proof['semantic_payload_digest'])
    if type(proof['revision']) is not int or proof['revision']<0:raise ValueError('Invalid proof revision')
    _uuid(payload.get('session_id'))
    if proof['semantic_payload_digest']!=semantic_digest(payload):raise ValueError('Semantic proof binding mismatch')
    return proof


def bind_remote_proof(payload: dict, operation: dict) -> dict:
    candidate=deepcopy(payload)
    if candidate.get('id')!=operation.get('span_id'):raise ValueError('Proof span identity mismatch')
    _uuid(operation.get('operation_id'))
    if type(operation.get('revision')) is not int or operation['revision']<0:raise ValueError('Invalid proof revision')
    _uuid(candidate.get('session_id'))
    candidate['extra']['metadata']['remote_proof']={'operation_id':operation['operation_id'],'revision':operation['revision'],'semantic_payload_digest':semantic_digest(candidate)}
    return candidate


_MISMATCH=object()


def _subset(observed, expected):
    if isinstance(expected,dict):
        if not isinstance(observed,dict) or not set(expected)<=set(observed) or (not expected and observed):return _MISMATCH
        result={}
        for key,value in expected.items():
            result[key]=_subset(observed[key],value)
            if result[key] is _MISMATCH:return _MISMATCH
        return result
    return deepcopy(observed) if type(observed) is type(expected) and observed==expected else _MISMATCH


class RemoteRunVerifier:
    def __init__(self, *, manifest: dict, criteria: dict, api_key: str | None, read_transport: Callable) -> None:
        # Parent validates source/runtime identities before process construction.
        from .rna_hosted_trace_contract import QUALIFICATION_KEYS
        _closed(manifest,QUALIFICATION_KEYS,'qualification manifest')
        self.manifest=_copy(manifest);self.criteria=validate_criteria(criteria)
        validate_limits(manifest['limits'])
        if manifest['mode'] not in {'fake','hosted'} or manifest['data_scope']!='synthetic':raise ValueError('Synthetic verification required')
        operations=manifest['allowed_operations']
        if not isinstance(operations,list) or not set(operations)<=OPERATIONS:raise ValueError('Bounded reads required')
        if not callable(read_transport):raise ValueError('Explicit read transport required')
        if manifest['mode']=='hosted':
            if manifest['authorize_remote_read'] is not True:raise PermissionError('Separate remote read approval required')
            if not isinstance(api_key,str) or not api_key:raise ValueError('Explicit exporter credential required')
        # A fake instance never retains even an explicitly supplied credential.
        self._api_key=api_key if manifest['mode']=='hosted' else None
        self.transport=read_transport;self.attempts=[];self.read_requests=0
        self.clock_id=str(uuid4());self.started=time.monotonic_ns();self._issued={};self._used=set()
        self._stopped_reason=None
        self._session_digest=manifest['live_session_digest']
        self._target={k:manifest[k] for k in ('endpoint','workspace_id','project_id')}
        if self._session_digest is not None:_digest(self._session_digest)

    def bind_session(self, session: dict) -> None:
        from .rna_langsmith_live import validate_live_session
        session=validate_live_session(session)
        if session['format']!='devbench.rna-live-trace-session/2':raise ValueError('UUID-bound session required')
        session_digest=digest(canonical_json_bytes(session))
        if self._session_digest is not None and self._session_digest!=session_digest:raise ValueError('Immutable session digest mismatch')
        for key in self._target:
            if self._target[key] is not None and self._target[key]!=session[key]:raise ValueError('Session target mismatch')
            self._target[key]=session[key]
        if self.manifest['project_name'] is not None and session['project']!=self.manifest['project_name']:raise ValueError('Session project name mismatch')
        self._session_digest=session_digest

    def _receipt(self, operation, expected_payload, previous, first, outcome, result, observed=None):
        receipt=dict(format='devbench.rna-remote-reconciliation/1',qualification_id=self.manifest['qualification_id'],session_digest=self._session_digest,**self._target,operation_id=operation['operation_id'],span_id=operation['span_id'],revision=operation['revision'],expected_payload_digest=digest(canonical_json_bytes(expected_payload)),response_class=outcome,observed_content_digest=digest(canonical_json_bytes(observed)) if observed is not None else None,observation_interval=dict(clock_id=self.clock_id,last_nonmatching_monotonic_ns=previous,first_matching_monotonic_ns=first),verification_result=result,reviewer_approval_digest=None)
        receipt=validate_reconciliation(receipt)
        self._issued[digest(canonical_json_bytes(receipt))]=_copy(receipt)
        return receipt

    def observe(self, *, operation: dict, expected_payload: dict, persisted_monotonic_ns: int) -> dict:
        from .rna_langsmith_live import validate_live_run
        if self._session_digest is None:raise ValueError('Retained session binding required')
        if type(persisted_monotonic_ns) is not int or persisted_monotonic_ns<0 or persisted_monotonic_ns>time.monotonic_ns():raise ValueError('Trusted persistence clock required')
        validate_live_run(expected_payload,operation['operation'])
        proof=validate_remote_proof(expected_payload)
        if expected_payload['session_id']!=self._target['project_id'] or proof['operation_id']!=operation['operation_id'] or proof['revision']!=operation['revision'] or operation['payload_sha256']!=digest(canonical_json_bytes(expected_payload)):raise ValueError('Exact operation wire binding required')
        previous=persisted_monotonic_ns
        expires=_time(self.manifest["expires_at"])
        if self._stopped_reason is not None:
            return self._receipt(operation,expected_payload,previous,None,self._stopped_reason,"blocked")
        if expires is None or expires<=datetime.now(timezone.utc):
            return self._receipt(operation,expected_payload,previous,None,"budget_exhausted","blocked")
        if self.manifest['mode']=='hosted':
            return self._receipt(operation,expected_payload,previous,None,'unsupported_schema','blocked')
        allowed=self.manifest['allowed_operations']
        if 'read_run' in allowed:method,path,payload='GET','/runs/'+_uuid(operation['span_id']),None
        elif 'query_runs' in allowed:method,path,payload='POST','/runs/query',dict(id=[operation['span_id']],session=[self._target['project_id']],trace=[expected_payload['trace_id']],select=list(REMOTE_FIELDS),limit=1)
        else:raise PermissionError('Exact run read not approved')
        deadline=min(time.monotonic_ns()+int(self.criteria['visibility_deadline_seconds']*1e9),self.started+int(min(self.manifest['limits']['total_deadline_seconds'],self.criteria['limits']['total_deadline_seconds'])*1e9))
        cap=min(self.manifest['limits']['max_read_requests'],self.criteria['limits']['max_read_requests'])
        while self.read_requests<cap and time.monotonic_ns()<deadline:
            if expires<=datetime.now(timezone.utc):
                return self._receipt(operation,expected_payload,previous,None,"budget_exhausted","blocked")
            timeout=max(.001,min(15,(deadline-time.monotonic_ns())/1e9))
            self.read_requests+=1;status=None;size=0;outcome='invalid_response';body=None
            try:
                response=self.transport(method=method,path=path,payload=_copy(payload) if payload is not None else None,timeout=timeout)
                if not isinstance(response,dict) or set(response)!={'status_code','body'} or type(response['status_code']) is not int:raise ValueError('Bounded response contract required')
                status=response['status_code'];body=response['body']
                raw=body if isinstance(body,bytes) else canonical_json_bytes(body);size=len(raw)
                if size>MAX_RESPONSE_BYTES:outcome='oversized'
                elif status in (401,403):outcome='auth_denied'
                elif status==404:outcome='not_visible'
                elif status==409:outcome='conflict'
                elif status==429:outcome='rate_limited'
                elif 300<=status<400:outcome='redirect'
                elif status>=500:outcome='server_error'
                elif not 200<=status<300:outcome='invalid_response'
                else:
                    if isinstance(body,bytes):body=json.loads(body)
                    if method=='POST':
                        if not isinstance(body,dict) or not isinstance(body.get('runs'),list) or len(body['runs'])>1:raise ValueError('Exact bounded query result required')
                        body=body['runs'][0] if body['runs'] else None
                    outcome='not_visible' if body is None else 'candidate'
            except TimeoutError:outcome='timeout'
            except Exception:outcome='invalid_response'
            observed_at=time.monotonic_ns()
            self.attempts.append({'attempt':self.read_requests,'method':method,'response_class':outcome,'status_code':status,'bytes_read':min(size,MAX_RESPONSE_BYTES+1),'observer_monotonic_ns':observed_at})
            if outcome=="auth_denied":
                self._stopped_reason=outcome
                return self._receipt(operation,expected_payload,previous,None,outcome,"blocked")
            if observed_at>deadline:
                return self._receipt(operation,expected_payload,previous,None,"not_visible","not_visible")
            if expires<=datetime.now(timezone.utc):
                return self._receipt(operation,expected_payload,previous,None,"budget_exhausted","blocked")
            if outcome=='candidate':
                result,normalized=self._compare(body,expected_payload)
                outcome={'exact_revision_match':'matched','state_match_only':'state_only','mismatch':'mismatch'}[result]
                self.attempts[-1]['response_class']=outcome
                return self._receipt(operation,expected_payload,previous,observed_at if result!='mismatch' else None,outcome,result,normalized)
            if outcome!='not_visible':return self._receipt(operation,expected_payload,previous,None,outcome,'blocked')
            previous=observed_at
            delay=min(self.criteria['poll_interval_seconds'],max(0,(deadline-time.monotonic_ns())/1e9))
            if delay:time.sleep(delay)
        return self._receipt(operation,expected_payload,previous,None,'budget_exhausted' if self.read_requests>=cap else 'not_visible','not_visible')

    def _compare(self, observed, expected):
        try:
            if not isinstance(observed,dict) or observed.get('session_id')!=self._target['project_id'] or observed.get('tenant_id')!=self._target['workspace_id']:return 'mismatch',None
            expected_vector=normalized_payload(expected)
            observed_vector=normalized_payload(observed)
            # Don't let unknown provider metadata enter retained evidence.
            vector=_subset(observed_vector,expected_vector)
            if vector!=expected_vector:return 'mismatch',None
            observed_proof=observed.get('extra',{}).get('metadata',{}).get('remote_proof')
            if observed_proof is None:return 'state_match_only',vector
            if observed_proof!=expected['extra']['metadata']['remote_proof']:return 'mismatch',vector
            if set(observed_proof)!=REMOTE_PROOF_KEYS:return 'mismatch',None
            vector['extra']['metadata']['remote_proof']=deepcopy(observed_proof)
            return 'exact_revision_match',vector
        except Exception:return 'mismatch',None

    def validate_reviewed(self, receipt: dict, *, approval_record) -> str:
        from .rna_hosted_trace_contract import absolute_path
        receipt=validate_reconciliation(receipt)
        if receipt['verification_result']!='exact_revision_match':raise ValueError('Only exact observed revision may be acknowledged')
        candidate={**receipt,'reviewer_approval_digest':None};identity=digest(canonical_json_bytes(candidate))
        if identity not in self._issued or identity in self._used or self._issued[identity]!=candidate:raise ValueError('Fresh verifier-issued receipt required')
        path=absolute_path(approval_record)
        import os,stat
        fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
        with os.fdopen(fd,'rb') as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):raise ValueError('Regular retained review required')
            raw=stream.read(8193)
        if len(raw)>8192:raise ValueError('Review record too large')
        approval=json.loads(raw);_closed(approval,{'format','receipt_digest','approved','reviewer_alias'},'retained operator review')
        if approval['format']!='devbench.rna-remote-review/1' or approval['approved'] is not True or approval['receipt_digest']!=identity:raise ValueError('Review record does not approve candidate')
        _uuid(approval['reviewer_alias'])
        if receipt['reviewer_approval_digest']!=digest(raw):raise ValueError('Retained review digest mismatch')
        return identity

    def consume_reviewed(self, identity):
        self._used.add(identity)


class RESTReadTransport:
    """Explicit credential-bearing bounded read route transport.

    This low-level route does not attest deployment schema or workspace authority;
    constructing it cannot unblock RemoteRunVerifier's hosted qualification gate.
    No proxy, redirect, credential discovery or unbounded response is permitted.
    """
    def __init__(self, *, manifest: dict, api_key: str, allowed_run_ids: list,
                 allowed_trace_ids: list):
        from .rna_hosted_trace_contract import QUALIFICATION_KEYS
        _closed(manifest, QUALIFICATION_KEYS, "qualification manifest")
        if manifest["mode"] != "hosted" or manifest["authorize_remote_read"] is not True:
            raise PermissionError("Explicit hosted read scope required")
        if manifest["data_scope"] != "synthetic" or manifest["endpoint"] not in ENDPOINTS:
            raise ValueError("Approved synthetic endpoint required")
        for key in ("project_id", "workspace_id"):
            _uuid(manifest[key])
        if not isinstance(api_key, str) or not api_key:
            raise ValueError("Explicit exporter credential required")
        for ids in (allowed_run_ids, allowed_trace_ids):
            if not isinstance(ids, list) or not ids or len(ids)>manifest["limits"]["max_spans"] or len(set(ids))!=len(ids):
                raise ValueError("Bounded immutable UUID allowlist required")
            for identity in ids:
                _uuid(identity)
        validate_limits(manifest["limits"])
        self.manifest=_copy(manifest)
        self._api_key=api_key
        self.runs=frozenset(allowed_run_ids)
        self.traces=frozenset(allowed_trace_ids)
        self.requests=0
        self.started=time.monotonic()
        self._stopped=False
        self._opener=build_opener(ProxyHandler({}), _NoRedirect())

    def __call__(self, *, method: str, path: str, payload: dict | None,
                 timeout: float) -> dict:
        expires=_time(self.manifest["expires_at"])
        remaining=self.manifest["limits"]["total_deadline_seconds"]-(time.monotonic()-self.started)
        if self._stopped or expires is None or expires<=datetime.now(timezone.utc) or remaining<=0 or self.requests>=self.manifest["limits"]["max_read_requests"]:
            raise PermissionError("Read authorization expired, stopped or exhausted")
        if type(timeout) not in (int,float) or not math.isfinite(timeout) or not 0<timeout<=15:
            raise ValueError("Finite bounded request timeout required")
        allowed=self.manifest["allowed_operations"]
        if method=="GET":
            if "read_run" not in allowed or payload is not None or path not in {"/runs/"+identity for identity in self.runs}:
                raise PermissionError("Exact allowlisted run GET required")
        elif method=="POST" and path=="/runs/query":
            if "query_runs" not in allowed:
                raise PermissionError("Read-only query approval required")
            _closed(payload, {"id","session","trace","select","limit"}, "bounded run query")
            if (not isinstance(payload["id"],list) or len(payload["id"])!=1 or payload["id"][0] not in self.runs or
                payload["session"]!=[self.manifest["project_id"]] or
                not isinstance(payload["trace"],list) or len(payload["trace"])!=1 or payload["trace"][0] not in self.traces or
                payload["select"]!=list(REMOTE_FIELDS) or type(payload["limit"]) is not int or payload["limit"]!=1):
                raise ValueError("Exact bounded project/run/trace query required")
        else:
            raise PermissionError("Only bounded read GET or query POST allowed")
        raw=canonical_json_bytes(payload) if payload is not None else None
        if raw is not None and len(raw)>4096:
            raise ValueError("Read request byte ceiling exceeded")
        headers={"Content-Type":"application/json","x-api-key":self._api_key,"x-tenant-id":self.manifest["workspace_id"]}
        request=Request(self.manifest["endpoint"]+path,data=raw,headers=headers,method=method)
        self.requests+=1
        try:
            with self._opener.open(request,timeout=min(timeout,remaining)) as response:
                status=response.status
                body=response.read(MAX_RESPONSE_BYTES+1)
        except HTTPError as error:
            status=error.code
            body=error.read(MAX_RESPONSE_BYTES+1)
            error.close()
        except Exception:
            # Do not expose credential-bearing URL/error text to callers.
            raise TimeoutError("Remote read unavailable") from None
        if status in (401,403):
            self._stopped=True
        return {"status_code":status,"body":body}
