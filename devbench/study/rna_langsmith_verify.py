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
import re
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

# Separate from the frozen fake /1 contract above. This pins a public interface,
# not an authenticated run observation. In particular, RunSchema and RunSelect
# do not document tenant_id; the explicit workspace field below is only a
# synthetic candidate extension. No request/header/project metadata fills it.
RUN_RESPONSE_SCHEMA_FORMAT='devbench.rna-run-response-candidate/1'
RUN_RESPONSE_SCHEMA_SNAPSHOT={
    'format':RUN_RESPONSE_SCHEMA_FORMAT,
    'source_url':'https://api.smith.langchain.com/openapi.json',
    'public_openapi_sha256':'f355aaf9334d64738a8c338989deabe12280a4f1a319600645be2df9d308d6ba',
    'observed_at':'2026-10-08T17:27:17.594017+00:00','api_version':'0.1.0',
    'get_route':'/api/v1/runs/{run_id}','query_route':'/api/v1/runs/query',
    'documented_routes_deprecated':True,
    'documented_run_required':['name','run_type','trace_id','dotted_order','id','status','session_id','app_path'],
    'query_required':['runs','cursors'],
    'semantic_fields':list(REMOTE_FIELDS),
    'required_workspace_field':'tenant_id','documented_workspace_field':None,
    'scope':'synthetic_candidate_only',
    'deployment_qualified':False,'workspace_binding_qualified':False,
}
RUN_RESPONSE_SCHEMA_DIGEST=digest(canonical_json_bytes(RUN_RESPONSE_SCHEMA_SNAPSHOT))


def hosted_schema_qualified() -> bool:
    """No independently reviewed deployed run-schema evidence is admitted.

    Project metadata, the public OpenAPI and mutable snapshot flags cannot
    satisfy this gate. A future deployed evidence path needs separate review.
    """
    return False


def _response_json(body):
    def unique_object(pairs):
        result={}
        for key,value in pairs:
            if key in result:raise ValueError('Duplicate JSON field')
            result[key]=value
        return result
    def reject_constant(value):
        raise ValueError('Nonfinite JSON value')
    if type(body) not in (dict,bytes):raise ValueError('JSON object or bytes required')
    raw=body if type(body) is bytes else canonical_json_bytes(body)
    if len(raw)>MAX_RESPONSE_BYTES:raise ValueError('Response byte ceiling exceeded')
    value=json.loads(raw,object_pairs_hook=unique_object,parse_constant=reject_constant)
    # Also catches floating-point overflow such as 1e999, including additions
    # that would otherwise be discarded by the privacy projection.
    canonical_json_bytes(value)
    if not isinstance(value,dict):raise ValueError('Response object required')
    return value


def decode_run_response(body, *, schema_format, method, expected_payload,
                        workspace_id, project_id, project_name,
                        project_attestation):
    """Decode a bounded synthetic candidate, never authorize hosted transport.

    project_attestation is a closed synthetic observation used for name/UUID
    consistency, not an authenticated assertion. Every run additionally needs
    its own explicit workspace and project fields. Public RunSchema alone is
    therefore incompatible with this candidate. Only matching allowlisted
    content is returned; malformed responses have a fixed, secret-free error.
    """
    try:
        if schema_format!=RUN_RESPONSE_SCHEMA_FORMAT or method not in {'GET','POST'}:
            raise ValueError('Unsupported candidate version/route')
        _uuid(workspace_id);_uuid(project_id)
        if not isinstance(project_name,str) or re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,99}',project_name) is None:
            raise ValueError('Bounded project name required')
        _closed(project_attestation,{'id','tenant_id','name'},'synthetic project observation')
        if project_attestation!={'id':project_id,'tenant_id':workspace_id,'name':project_name}:
            raise ValueError('Project observation mismatch')
        if expected_payload.get('session_id')!=project_id:raise ValueError('Expected target mismatch')
        validate_remote_proof(expected_payload)
        observed=_response_json(body)
        if method=='POST':
            if not {'runs','cursors'}<=set(observed) or not set(observed)<={'runs','cursors','parsed_query','search_cursors'}:
                raise ValueError('Closed query envelope required')
            cursors=observed['cursors']
            if not isinstance(cursors,dict) or any(value is not None and not isinstance(value,str) for value in cursors.values()):
                raise ValueError('Invalid query cursors')
            if observed.get('parsed_query') is not None and not isinstance(observed['parsed_query'],str):
                raise ValueError('Invalid parsed query')
            if observed.get('search_cursors') is not None and not isinstance(observed['search_cursors'],dict):
                raise ValueError('Invalid search cursors')
            runs=observed['runs']
            if not isinstance(runs,list) or len(runs)>1:raise ValueError('Exact bounded query required')
            if not runs:return 'not_visible',None
            observed=runs[0]
        required=(set(REMOTE_FIELDS)-{'end_time'})|{'tenant_id','dotted_order','status','app_path'}
        if not isinstance(observed,dict) or not required<=set(observed):raise ValueError('Required run fields missing')
        for key in ('id','trace_id','session_id','tenant_id'):_uuid(observed[key])
        if observed['parent_run_id'] is not None:_uuid(observed['parent_run_id'])
        if observed['session_id']!=project_id or observed['tenant_id']!=workspace_id:
            raise ValueError('Run target mismatch')
        for key in ('name','run_type','dotted_order','status','app_path'):
            if not isinstance(observed[key],str):raise ValueError('Invalid run field type')
        if observed['run_type'] not in {'tool','chain','llm','retriever','embedding','prompt','parser'}:
            raise ValueError('Unsupported run type')
        if not isinstance(observed['tags'],list) or not all(isinstance(tag,str) for tag in observed['tags']):
            raise ValueError('Invalid run tags')
        for key in ('inputs','outputs','extra'):
            if not isinstance(observed[key],dict):raise ValueError('Invalid run object')
        for key in ('start_time','end_time'):
            if key=='end_time' and observed.get(key) is None:continue
            value=observed[key]
            if not isinstance(value,str) or re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})',value) is None or _time(value) is None:
                raise ValueError('Invalid run timestamp')
        if observed.get('end_time') is not None and _time(observed['end_time'])<_time(observed['start_time']):
            raise ValueError('Terminal time precedes start')
        expected_vector=normalized_payload(expected_payload)
        observed_vector=normalized_payload(observed)
        if set(observed_vector)!=set(expected_vector):return 'mismatch',None
        vector=_subset(observed_vector,expected_vector)
        if vector is _MISMATCH or canonical_json_bytes(vector)!=canonical_json_bytes(expected_vector):return 'mismatch',None
        if 'remote_proof' not in observed['extra']['metadata']:return 'state_match_only',vector
        proof=observed['extra']['metadata']['remote_proof']
        # Validate types against the privacy-projected semantic payload. Provider
        # metadata additions never enter evidence or the uploaded proof vector.
        vector['extra']['metadata']['remote_proof']=deepcopy(proof)
        try:validate_remote_proof(vector)
        except (ValueError,TypeError,KeyError):return 'mismatch',None
        if proof!=expected_payload['extra']['metadata']['remote_proof']:return 'mismatch',None
        return 'exact_revision_match',vector
    except Exception:
        raise ValueError('Invalid bounded candidate run response') from None


def normalized_payload(payload):
    """Frozen allowlisted semantic vector; unknown provider fields are excluded."""
    result={key:deepcopy(payload[key]) for key in REMOTE_FIELDS if key in payload}
    if ('session_id' in payload and payload.get('id') == payload.get('trace_id')
            and 'parent_run_id' not in payload):
        result['parent_run_id']=None
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
            if not isinstance(observed_proof,dict) or type(observed_proof.get('revision')) is not int:return 'mismatch',None
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


# Separate public-interface profile for the child-owned /2 bootstrap. These
# hashes pin public documentation, not an authenticated RunSchema observation.
JOINED_RUN_RESPONSE_SCHEMA_FORMAT='devbench.rna-joined-run-response/2'
JOINED_REMOTE_FIELDS=REMOTE_FIELDS+('dotted_order','status','app_path')
JOINED_RUN_RESPONSE_SCHEMA_SNAPSHOT={
    'format':JOINED_RUN_RESPONSE_SCHEMA_FORMAT,
    'source_url':'https://api.smith.langchain.com/openapi.json',
    'semantics_url':'https://docs.langchain.com/langsmith/run-data-format',
    'public_openapi_sha256':'c141116a05c5628093ef2278413bf748e4bc417de18aa6d4bdba18f8f44a0dc0',
    'public_projection_sha256':'d84319c23a6812fd87d1f6cb0f980742c2f53aec783b91770cffab7067646859',
    'observed_at':'2026-10-08T19:15:23.903417+00:00',
    'get_route':'/api/v1/runs/{run_id}','query_route':'/api/v1/runs/query',
    'run_project_field':'session_id','project_workspace_field':'tenant_id',
    'semantic_fields':list(REMOTE_FIELDS),
    'scope':'experimental_joined_binding_bootstrap',
    'deployment_qualified':False,
}
JOINED_RUN_RESPONSE_SCHEMA_DIGEST=digest(canonical_json_bytes(JOINED_RUN_RESPONSE_SCHEMA_SNAPSHOT))
JOINED_WORKFLOW_RESPONSE_SCHEMA_FORMAT='devbench.rna-joined-workflow-run-response/3'
JOINED_WORKFLOW_REMOTE_FIELDS=JOINED_REMOTE_FIELDS+('error',)
JOINED_WORKFLOW_RESPONSE_SCHEMA_SNAPSHOT={
    **deepcopy(JOINED_RUN_RESPONSE_SCHEMA_SNAPSHOT),
    'format':JOINED_WORKFLOW_RESPONSE_SCHEMA_FORMAT,
    'scope':'experimental_live_workflow',
    'expected_topology':'eight_retained_start_payloads',
    'error_source_url':'https://raw.githubusercontent.com/langchain-ai/langsmith-sdk/main/js/src/schemas.ts',
    'read_fields':list(JOINED_WORKFLOW_REMOTE_FIELDS),
    'write_error_codes':{'failed':'devbench.workflow_failed','blocked':'devbench.workflow_blocked',
                         'cancelled':'devbench.workflow_cancelled','incomplete':'devbench.workflow_incomplete'},
    'error_binding':'derived_from_proof_bound_source_status_checked_separately_and_in_full_wire_payload_sha256',
}
JOINED_WORKFLOW_RESPONSE_SCHEMA_DIGEST=digest(canonical_json_bytes(JOINED_WORKFLOW_RESPONSE_SCHEMA_SNAPSHOT))


def _validate_workflow_expected_topology(expected_payload, expected_topology, expected_context):
    from .rna_langsmith_live import (SESSION_FORMAT_V3, validate_live_run,
                                     _root_dotted_order)
    from .rna_hosted_workflow_contract import validate_workflow_topology
    if not isinstance(expected_topology, dict) or len(expected_topology) != 8:
        raise ValueError('Eight independently retained workflow starts required')
    starts=deepcopy(expected_topology)
    pending=dict(starts);ordered=[];seen=set()
    while pending:
        ready=[identity for identity,run in pending.items()
               if isinstance(run,dict) and (run.get('parent_run_id') is None or run.get('parent_run_id') in seen)]
        if not ready:raise ValueError('Reachable retained workflow starts required')
        for identity in ready:
            run=pending.pop(identity)
            _uuid(identity)
            if run.get('id')!=identity or 'end_time' in run:
                raise ValueError('Canonical retained workflow start required')
            validate_live_run(run,'start',session_format=SESSION_FORMAT_V3)
            if (run['session_id']!=expected_context['project_id']
                    or any(run['extra']['metadata'].get(key)!=expected_context[key]
                           for key in ('source_digest','runtime_digest'))):
                raise ValueError('Retained workflow start context mismatch')
            row=run['extra']['metadata']
            ordered.append(dict(span_id=identity,parent_span_id=run.get('parent_run_id'),
                **{key:row[key] for key in ('role','phase','attempt')}))
            parent=run.get('parent_run_id')
            own=_root_dotted_order(run['start_time'],identity)
            order=own if parent is None else starts[parent]['dotted_order']+'.'+own
            if run['dotted_order']!=order:
                raise ValueError('Independent workflow ancestry order mismatch')
            seen.add(identity)
    validate_workflow_topology(ordered)
    root=ordered[0]['span_id']
    if any(run['trace_id']!=root for run in starts.values()):
        raise ValueError('Retained workflow trace mismatch')
    start=starts.get(expected_payload.get('id'))
    if start is None:
        raise ValueError('Expected workflow span absent from retained starts')
    for key in ('id','trace_id','parent_run_id','session_id','start_time','dotted_order'):
        if expected_payload.get(key)!=start.get(key):
            raise ValueError('Immutable expected workflow identity/order mismatch')
    if any(expected_payload['extra']['metadata'].get(key)!=start['extra']['metadata'][key]
           for key in ('role','phase','attempt')):
        raise ValueError('Expected workflow slot mismatch')
    validate_live_run(expected_payload,'finish' if 'end_time' in expected_payload else 'start',
                      session_format=SESSION_FORMAT_V3)
    return starts


def decode_joined_run_response(body, *, schema_format, method, expected_payload,
                               project_receipt, expected_context, now=None):
    """Join every run to a fresh child-owned authenticated project observation.

    This provider-compatible profile never adds tenant_id to a run or its proof.
    Its typed receipt is a process-local trust seam, not a signed JSON assertion.
    Matching content is projected to the independently expected semantic vector.
    """
    return _decode_joined_run_response(body,schema_format=schema_format,method=method,
        expected_payload=expected_payload,project_receipt=project_receipt,
        expected_context=expected_context,now=now)


def decode_joined_workflow_run_response(body, *, schema_format, method, expected_payload,
                                        project_receipt, expected_context, expected_topology, now=None):
    """Explicit /3 decoder, bound to all independently retained parent starts.

    Proof v1 retains its frozen semantic fields, which exclude top-level error.
    The /3 code is derived from proof-bound source_status and checked separately;
    the immutable operation's full wire SHA also includes the error field.
    """
    try:
        if schema_format!=JOINED_WORKFLOW_RESPONSE_SCHEMA_FORMAT:
            raise ValueError('Unsupported workflow joined schema')
        return _decode_joined_run_response(body,schema_format=schema_format,method=method,
            expected_payload=expected_payload,project_receipt=project_receipt,
            expected_context=expected_context,now=now,workflow_topology=expected_topology)
    except Exception:
        raise ValueError('Invalid bounded joined workflow run response') from None


def _decode_joined_run_response(body, *, schema_format, method, expected_payload,
                                project_receipt, expected_context, now=None, workflow_topology=None,
                                context_session=None, historical_producer_context=None):
    try:
        from .rna_langsmith_binding import ProjectReadReceipt, _timestamp
        expected_schema=((JOINED_GENERATION_CONTEXT_RESPONSE_SCHEMA_FORMAT if context_session.get("format") == "devbench.rna-live-trace-session/5" else JOINED_CONTEXT_RESPONSE_SCHEMA_FORMAT) if context_session is not None else
            JOINED_RUN_RESPONSE_SCHEMA_FORMAT if workflow_topology is None else JOINED_WORKFLOW_RESPONSE_SCHEMA_FORMAT)
        if schema_format!=expected_schema or method not in {'GET','POST'}:
            raise ValueError('Unsupported joined version/route')
        if type(project_receipt) is not ProjectReadReceipt:
            raise ValueError('Typed child-owned project receipt required')
        attestation=project_receipt.validate(expected_context=expected_context,now=now)
        project=attestation['observed_project']
        if expected_payload.get('session_id')!=project['id']:
            raise ValueError('Expected payload project mismatch')
        if context_session is not None:
            _validate_context_expected_topology(expected_payload,workflow_topology,context_session,
                historical_producer_context if historical_producer_context is not None else expected_context,
                historical=historical_producer_context is not None)
        elif workflow_topology is not None:
            _validate_workflow_expected_topology(expected_payload,workflow_topology,expected_context)
        validate_remote_proof(expected_payload)
        observed=_response_json(body)
        if method=='POST':
            if not {'runs','cursors'}<=set(observed) or not set(observed)<={'runs','cursors','parsed_query','search_cursors'}:
                raise ValueError('Closed joined query envelope required')
            cursors=observed['cursors']
            if not isinstance(cursors,dict) or any(value is not None and not isinstance(value,str) for value in cursors.values()):
                raise ValueError('Invalid joined query cursors')
            if observed.get('parsed_query') is not None and not isinstance(observed['parsed_query'],str):
                raise ValueError('Invalid parsed query')
            if observed.get('search_cursors') is not None and not isinstance(observed['search_cursors'],dict):
                raise ValueError('Invalid search cursors')
            runs=observed['runs']
            if not isinstance(runs,list) or len(runs)>1:
                raise ValueError('Exact bounded joined query required')
            if not runs:return 'not_visible',None
            observed=runs[0]
        root_parent_optional=(expected_payload.get('id')==expected_payload.get('trace_id')
            and 'parent_run_id' not in expected_payload)
        required=set(JOINED_REMOTE_FIELDS)-{'end_time'}
        if root_parent_optional:required.discard('parent_run_id')
        if not isinstance(observed,dict) or not required<=set(observed):
            raise ValueError('Required joined run fields missing')
        for key in ('id','trace_id','session_id'):_uuid(observed[key])
        if root_parent_optional:
            if (observed['id']!=expected_payload['id'] or observed['trace_id']!=expected_payload['trace_id']
                    or observed.get('parent_run_id') is not None):
                raise ValueError('Exact hosted root identity required')
        elif observed['parent_run_id'] is not None:_uuid(observed['parent_run_id'])
        if observed['session_id']!=project['id'] or project['id']!=expected_context['project_id'] or project['tenant_id']!=expected_context['workspace_id']:
            raise ValueError('Run/project/workspace join mismatch')
        if 'tenant_id' in observed:
            _uuid(observed['tenant_id'])
            if observed['tenant_id']!=project['tenant_id']:
                raise ValueError('Optional observed workspace mismatch')
        if historical_producer_context is not None and any(observed.get(key)!=expected_payload.get(key)
                for key in ('id','trace_id','parent_run_id','session_id')):
            raise ValueError('Exact historical context identity required')
        for key in ('name','run_type','dotted_order','status','app_path'):
            if not isinstance(observed[key],str):raise ValueError('Invalid joined run string')
        if 'dotted_order' in expected_payload:
            from .rna_langsmith_live import _root_dotted_order
            if workflow_topology is None and (expected_payload.get('id')!=expected_payload.get('trace_id')
                    or expected_payload.get('parent_run_id') is not None
                    or expected_payload['dotted_order']!=_root_dotted_order(expected_payload.get('start_time'),expected_payload.get('id'))):
                raise ValueError('Invalid expected root order')
            if observed['dotted_order']!=expected_payload['dotted_order']:
                return 'mismatch',None
        if workflow_topology is not None:
            from .rna_langsmith_live import workflow_error_code
            error=workflow_error_code(expected_payload['extra']['metadata']['source_status']) if 'end_time' in expected_payload else None
            if ((error is None and 'error' in expected_payload)
                    or (error is not None and expected_payload.get('error')!=error)):
                raise ValueError('Exact derived expected workflow error required')
            if observed.get('error')!=error:
                return 'mismatch',None
            status='error' if error is not None else 'success' if 'end_time' in expected_payload else 'pending'
            if observed['status']!=status:
                return 'mismatch',None
        if observed['run_type'] not in {'tool','chain','llm','retriever','embedding','prompt','parser'}:
            raise ValueError('Unsupported joined run type')
        if not isinstance(observed['tags'],list) or not all(isinstance(tag,str) for tag in observed['tags']):
            raise ValueError('Invalid joined run tags')
        for key in ('inputs','outputs','extra'):
            # Provider null is compatible only with independently known empty IO.
            if (key!='extra' and observed[key] is None
                    and type(expected_payload.get(key)) is dict and expected_payload[key]=={}):
                observed[key]={}
            if not isinstance(observed[key],dict):raise ValueError('Invalid joined run object')
        for key in ('start_time','end_time'):
            if key=='end_time' and observed.get(key) is None:continue
            value=observed[key]
            if isinstance(value,str) and re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?',value):
                # Never infer an ambient timezone: bind components to expected UTC.
                expected_stamp=_timestamp(expected_payload.get(key)).astimezone(timezone.utc)
                if datetime.fromisoformat(value)!=expected_stamp.replace(tzinfo=None):
                    raise ValueError('Timezone-free run timestamp mismatch')
                observed[key]=expected_stamp.isoformat(timespec='microseconds')
            else:_timestamp(value)
        if observed.get('end_time') is not None and _time(observed['end_time'])<_time(observed['start_time']):
            raise ValueError('Terminal time precedes start')
        expected_vector=normalized_payload(expected_payload)
        observed_vector=normalized_payload(observed)
        if set(observed_vector)!=set(expected_vector):return 'mismatch',None
        vector=_subset(observed_vector,expected_vector)
        if vector is _MISMATCH or canonical_json_bytes(vector)!=canonical_json_bytes(expected_vector):
            return 'mismatch',None
        if 'remote_proof' not in observed['extra']['metadata']:
            return 'state_match_only',vector
        proof=observed['extra']['metadata']['remote_proof']
        vector['extra']['metadata']['remote_proof']=deepcopy(proof)
        try:validate_remote_proof(vector)
        except (ValueError,TypeError,KeyError):return 'mismatch',None
        if canonical_json_bytes(proof)!=canonical_json_bytes(expected_payload['extra']['metadata']['remote_proof']):
            return 'mismatch',None
        return 'exact_revision_match',vector
    except Exception:
        raise ValueError('Invalid bounded joined run response') from None


class JoinedRunVerifier(RemoteRunVerifier):
    """Bounded experimental observations under a fresh admitted child receipt.

    This separate /2 path leaves the legacy hosted gate unchanged and produces
    the existing reconciliation contract used by reviewed outbox transitions.
    """
    _query_fields=JOINED_REMOTE_FIELDS

    def __init__(self, *, manifest, criteria, api_key, read_transport,
                 project_receipt, expected_context):
        from .rna_langsmith_binding import ProjectReadReceipt, _context
        super().__init__(manifest=manifest,criteria=criteria,api_key=api_key,read_transport=read_transport)
        context=_context(expected_context)
        for key in ('session_digest','source_digest','runtime_digest','endpoint','workspace_id','project_id','project_name'):
            manifest_key='live_session_digest' if key=='session_digest' else key
            if context[key]!=self.manifest[manifest_key]:
                raise ValueError('Independent verifier context mismatch')
        if type(project_receipt) is not ProjectReadReceipt:
            raise ValueError('Typed child project observation required')
        project_receipt.claim(expected_context=context,consumer=self)
        self._project_receipt=project_receipt
        self._expected_context=context

    def bind_session(self, session):
        if digest(canonical_json_bytes(session))!=self._expected_context['session_digest']:
            raise ValueError('Joined verifier session changed')
        super().bind_session(session)

    def _validate_expected_run(self, expected_payload, operation):
        from .rna_langsmith_live import validate_live_run
        validate_live_run(expected_payload,operation['operation'])

    def _decode_response(self, raw, method, expected_payload):
        return decode_joined_run_response(raw,schema_format=JOINED_RUN_RESPONSE_SCHEMA_FORMAT,
            method=method,expected_payload=expected_payload,project_receipt=self._project_receipt,
            expected_context=self._expected_context)

    def observe(self, *, operation, expected_payload, persisted_monotonic_ns):
        if self._session_digest is None:raise ValueError('Retained joined session required')
        if type(persisted_monotonic_ns) is not int or not 0<=persisted_monotonic_ns<=time.monotonic_ns():
            raise ValueError('Trusted persistence clock required')
        self._validate_expected_run(expected_payload,operation)
        proof=validate_remote_proof(expected_payload)
        if (expected_payload['session_id']!=self._target['project_id'] or
            expected_payload['id']!=operation['span_id'] or
            proof['operation_id']!=operation['operation_id'] or
            type(operation['revision']) is not int or proof['revision']!=operation['revision'] or
            operation['payload_sha256']!=digest(canonical_json_bytes(expected_payload))):
            raise ValueError('Exact joined operation wire binding required')
        previous=persisted_monotonic_ns
        expires=_time(self.manifest['expires_at'])
        if self._stopped_reason is not None:
            return self._receipt(operation,expected_payload,previous,None,self._stopped_reason,'blocked')
        if expires is None or expires<=datetime.now(timezone.utc):
            return self._receipt(operation,expected_payload,previous,None,'budget_exhausted','blocked')
        self._project_receipt.validate(expected_context=self._expected_context)
        allowed=self.manifest['allowed_operations']
        if 'read_run' in allowed:method,path,payload='GET','/runs/'+_uuid(operation['span_id']),None
        elif 'query_runs' in allowed:
            method,path,payload='POST','/runs/query',dict(id=[operation['span_id']],session=[self._target['project_id']],trace=[expected_payload['trace_id']],select=list(self._query_fields),limit=1)
        else:raise PermissionError('Exact joined run read not approved')
        deadline=min(time.monotonic_ns()+int(self.criteria['visibility_deadline_seconds']*1e9),self.started+int(min(self.manifest['limits']['total_deadline_seconds'],self.criteria['limits']['total_deadline_seconds'])*1e9))
        cap=min(self.manifest['limits']['max_read_requests'],self.criteria['limits']['max_read_requests'])
        while self.read_requests<cap and time.monotonic_ns()<deadline:
            if expires<=datetime.now(timezone.utc):
                return self._receipt(operation,expected_payload,previous,None,'budget_exhausted','blocked')
            self._project_receipt.validate(expected_context=self._expected_context)
            timeout=max(.001,min(15,(deadline-time.monotonic_ns())/1e9))
            self.read_requests+=1;status=None;size=0;outcome='invalid_response';result=None;vector=None
            try:
                response=self.transport(method=method,path=path,payload=_copy(payload) if payload is not None else None,timeout=timeout)
                _closed(response,{'status_code','body'},'bounded joined response')
                status=response['status_code']
                if type(status) is not int or not 100<=status<=599:raise ValueError('Invalid response status')
                body=response['body']
                raw=body if type(body) is bytes else canonical_json_bytes(body);size=len(raw)
                if size>MAX_RESPONSE_BYTES:outcome='oversized'
                elif status in (401,403):outcome='auth_denied'
                elif status==404:outcome='not_visible'
                elif status==409:outcome='conflict'
                elif status==429:outcome='rate_limited'
                elif 300<=status<400:outcome='redirect'
                elif status>=500:outcome='server_error'
                elif not 200<=status<300:outcome='invalid_response'
                else:
                    result,vector=self._decode_response(raw,method,expected_payload)
                    outcome={'exact_revision_match':'matched','state_match_only':'state_only','mismatch':'mismatch','not_visible':'not_visible'}[result]
            except TimeoutError:outcome='timeout'
            except Exception:outcome='invalid_response'
            observed_at=time.monotonic_ns()
            self.attempts.append({'attempt':self.read_requests,'method':method,'response_class':outcome,'status_code':status if type(status) is int else None,'bytes_read':min(size,MAX_RESPONSE_BYTES+1),'observer_monotonic_ns':observed_at})
            if outcome=='auth_denied':
                self._stopped_reason=outcome
                return self._receipt(operation,expected_payload,previous,None,outcome,'blocked')
            if observed_at>deadline:
                return self._receipt(operation,expected_payload,previous,None,'not_visible','not_visible')
            if expires<=datetime.now(timezone.utc):
                return self._receipt(operation,expected_payload,previous,None,'budget_exhausted','blocked')
            # Recheck after transport as a response cannot extend receipt age.
            self._project_receipt.validate(expected_context=self._expected_context)
            if result in {'exact_revision_match','state_match_only','mismatch'}:
                return self._receipt(operation,expected_payload,previous,observed_at if result!='mismatch' else None,outcome,result,vector)
            if outcome!='not_visible':
                return self._receipt(operation,expected_payload,previous,None,outcome,'blocked')
            previous=observed_at
            delay=min(self.criteria['poll_interval_seconds'],max(0,(deadline-time.monotonic_ns())/1e9))
            if delay:time.sleep(delay)
        return self._receipt(operation,expected_payload,previous,None,'budget_exhausted' if self.read_requests>=cap else 'not_visible','not_visible')


class WorkflowRunVerifier(JoinedRunVerifier):
    """Explicit /3 lifecycle observations against a frozen eight-span workflow."""
    _query_fields=JOINED_WORKFLOW_REMOTE_FIELDS

    def __init__(self, *, expected_topology, **kwargs):
        context=kwargs['expected_context']
        if not isinstance(expected_topology,dict) or not expected_topology:
            raise ValueError('Retained workflow start topology required')
        first=next(iter(expected_topology.values()))
        self._workflow_topology=_validate_workflow_expected_topology(first,expected_topology,context)
        self._workflow_session_bound=False
        super().__init__(**kwargs)

    def bind_session(self, session):
        from .rna_langsmith_live import SESSION_FORMAT_V3, validate_live_session
        session=validate_live_session(session)
        if (session['format']!=SESSION_FORMAT_V3
                or digest(canonical_json_bytes(session))!=self._expected_context['session_digest']):
            raise ValueError('Immutable workflow /3 session required')
        slots={row['span_id']:row for row in session['span_topology']}
        if set(slots)!=set(self._workflow_topology):
            raise ValueError('Workflow session/start topology mismatch')
        for identity,start in self._workflow_topology.items():
            row=start['extra']['metadata']
            expected=dict(span_id=identity,parent_span_id=start.get('parent_run_id'),
                          **{key:row[key] for key in ('role','phase','attempt')})
            if slots[identity]!=expected:
                raise ValueError('Workflow session/start slot mismatch')
        if (self._session_digest!=self._expected_context['session_digest']
                or any(session[key]!=value for key,value in self._target.items())
                or session['project']!=self.manifest['project_name']):
            raise ValueError('Workflow verifier session target mismatch')
        self._workflow_session_bound=True

    def _validate_expected_run(self, expected_payload, operation):
        from .rna_langsmith_live import SESSION_FORMAT_V3, validate_live_run
        if not self._workflow_session_bound:
            raise ValueError('Retained workflow /3 session binding required')
        validate_live_run(expected_payload,operation['operation'],session_format=SESSION_FORMAT_V3)
        _validate_workflow_expected_topology(expected_payload,self._workflow_topology,self._expected_context)

    def _decode_response(self, raw, method, expected_payload):
        return decode_joined_workflow_run_response(raw,schema_format=JOINED_WORKFLOW_RESPONSE_SCHEMA_FORMAT,
            method=method,expected_payload=expected_payload,project_receipt=self._project_receipt,
            expected_context=self._expected_context,expected_topology=self._workflow_topology)


JOINED_CONTEXT_RESPONSE_SCHEMA_FORMAT='devbench.rna-joined-context-run-response/4'
JOINED_CONTEXT_REMOTE_FIELDS=JOINED_WORKFLOW_REMOTE_FIELDS
JOINED_CONTEXT_RESPONSE_SCHEMA_SNAPSHOT={
    **deepcopy(JOINED_WORKFLOW_RESPONSE_SCHEMA_SNAPSHOT),
    'format':JOINED_CONTEXT_RESPONSE_SCHEMA_FORMAT,
    'scope':'stored_synthetic_context_observations',
    'expected_topology':'retained_context_starts_and_frozen_observation_profile',
    'source_sequence_is_remote_revision':False,
    'capture_is_terminal':False,
    'usage_policy':'unavailable',
}
JOINED_CONTEXT_RESPONSE_SCHEMA_DIGEST=digest(canonical_json_bytes(JOINED_CONTEXT_RESPONSE_SCHEMA_SNAPSHOT))
JOINED_GENERATION_CONTEXT_RESPONSE_SCHEMA_FORMAT='devbench.rna-joined-context-run-response/5'
JOINED_GENERATION_CONTEXT_RESPONSE_SCHEMA_SNAPSHOT={
    **deepcopy(JOINED_CONTEXT_RESPONSE_SCHEMA_SNAPSHOT),
    'format':JOINED_GENERATION_CONTEXT_RESPONSE_SCHEMA_FORMAT,
    'scope':'stored_synthetic_generation_context_observations',
    'dispatch_accounting':'observed_launch_attempt_receipts_not_model_requests',
}
JOINED_GENERATION_CONTEXT_RESPONSE_SCHEMA_DIGEST=digest(canonical_json_bytes(JOINED_GENERATION_CONTEXT_RESPONSE_SCHEMA_SNAPSHOT))
_CONTEXT_RESPONSE_BYTES=131072
_CONTEXT_DIAGNOSTIC_CATEGORIES=frozenset({'match','provider_status_mismatch','provider_error_mismatch',
    'identity_mismatch','order_mismatch','timing_mismatch','content_mismatch','proof_mismatch',
    'schema_mismatch','body_oversized','body_incomplete','body_unread'})


def _context_expected_operation(payload, session):
    proof=validate_remote_proof(payload)
    operation='finish' if 'end_time' in payload else 'start'
    from .rna_langsmith_live import _context_run_receipt
    return dict(operation_id=proof['operation_id'],span_id=payload['id'],revision=proof['revision'],operation=operation,
        event_digest=digest(canonical_json_bytes(_context_run_receipt(payload,operation))),
        redaction_policy_digest=session['policy_digest'],source_binding_alias=session['workflow_alias'],
        payload_sha256=digest(canonical_json_bytes(payload)))


def _validate_context_expected_topology(expected_payload, expected_topology, expected_session, expected_context, *, historical=False):
    from .rna_langsmith_live import SESSION_FORMAT_V4, SESSION_FORMAT_V5, validate_live_session, validate_context_run_bindings, _root_dotted_order
    if historical:
        from .rna_langsmith_live import validate_historical_context_session, validate_historical_context_run_bindings
        session=validate_historical_context_session(expected_session)
        validate_bindings=validate_historical_context_run_bindings
    else:
        session=validate_live_session(expected_session)
        validate_bindings=validate_context_run_bindings
    if (session['format'] not in {SESSION_FORMAT_V4, SESSION_FORMAT_V5} or not isinstance(expected_topology,dict)
            or set(expected_topology)!={row['span_id'] for row in session['span_topology']}
            or digest(canonical_json_bytes(session))!=expected_context['session_digest']
            or any(session[key]!=expected_context[key] for key in ('source_digest','runtime_digest','project_id','workspace_id'))):
        raise ValueError('Exact context session and retained starts required')
    starts=deepcopy(expected_topology)
    for slot in session['span_topology']:
        run=starts[slot['span_id']]
        if run.get('id')!=slot['span_id'] or 'end_time' in run:
            raise ValueError('Canonical context START required')
        validate_bindings(run,_context_expected_operation(run,session),session)
        parent=slot['parent_span_id'];own=_root_dotted_order(run['start_time'],run['id'])
        order=own if parent is None else starts[parent]['dotted_order']+'.'+own
        if run['dotted_order']!=order:
            raise ValueError('Retained full context ancestry required')
    start=starts.get(expected_payload.get('id'))
    if start is None or any(expected_payload.get(key)!=start.get(key) for key in
            ('id','trace_id','parent_run_id','session_id','start_time','dotted_order')):
        raise ValueError('Immutable context start identity required')
    validate_bindings(expected_payload,_context_expected_operation(expected_payload,session),session)
    return starts


def decode_joined_context_run_response(body, *, schema_format, method, expected_payload,
        project_receipt, expected_context, expected_topology, expected_session, now=None, body_complete=True):
    """Explicit /4 typed-project readback; captures do not assert context completion."""
    try:
        if (schema_format!=(JOINED_GENERATION_CONTEXT_RESPONSE_SCHEMA_FORMAT if expected_session.get("format") == "devbench.rna-live-trace-session/5" else JOINED_CONTEXT_RESPONSE_SCHEMA_FORMAT) or body_complete is not True
                or type(body) not in (bytes,dict)
                or len(body if type(body) is bytes else canonical_json_bytes(body))>_CONTEXT_RESPONSE_BYTES):
            raise ValueError('Complete bounded context response required')
        return _decode_joined_run_response(body,schema_format=schema_format,method=method,
            expected_payload=expected_payload,project_receipt=project_receipt,expected_context=expected_context,
            workflow_topology=expected_topology,context_session=expected_session,now=now)
    except Exception:
        raise ValueError('Invalid bounded joined context response') from None


def decode_historical_joined_context_run_response(body, *, schema_format, method, expected_payload,
        project_receipt, reader_context, producer_context, expected_topology, expected_session,
        now=None, body_complete=True):
    """Compare old /5 payloads using a fresh typed current-reader project receipt.

    The caller must independently establish the historical producer from its
    approved seal. This pure comparison cannot authenticate serialized history
    or authorize a read, upload, outbox transition, or original timing recovery.
    """
    try:
        from .rna_langsmith_binding import _context
        reader, producer = _context(reader_context), _context(producer_context)
        if (schema_format != JOINED_GENERATION_CONTEXT_RESPONSE_SCHEMA_FORMAT or method != 'GET'
                or body_complete is not True or type(body) not in (bytes, dict)
                or len(body if type(body) is bytes else canonical_json_bytes(body)) > _CONTEXT_RESPONSE_BYTES
                or reader['max_age_seconds'] > 30
                or any(reader[key] != producer[key] for key in
                    ('endpoint','region','project_id','workspace_id','project_name'))
                or expected_session.get('project') != producer['project_name']
                or expected_session.get('endpoint') != producer['endpoint']):
            raise ValueError('Bounded historical comparison and independent exact project required')
        return _decode_joined_run_response(body, schema_format=schema_format, method=method,
            expected_payload=expected_payload, project_receipt=project_receipt, expected_context=reader,
            workflow_topology=expected_topology, context_session=expected_session,
            historical_producer_context=producer, now=now)
    except Exception:
        raise ValueError('Invalid bounded historical joined context response') from None


def validate_context_response_diagnostic(row):
    try:
        _closed(row,{'span_id','body_length','body_sha256','mismatch_category'},'context response diagnostic')
        _uuid(row['span_id'])
        category=row['mismatch_category']
        if category not in _CONTEXT_DIAGNOSTIC_CATEGORIES:
            raise ValueError('Unsupported category')
        if category in {'body_oversized','body_incomplete','body_unread'}:
            if row['body_length'] is not None or row['body_sha256'] is not None:
                raise ValueError('Prefix is not a whole body')
        else:
            if type(row['body_length']) is not int or not 0<=row['body_length']<=_CONTEXT_RESPONSE_BYTES:
                raise ValueError('Bounded whole body length required')
            _digest(row['body_sha256'])
        return _copy(row)
    except Exception:
        raise ValueError('Invalid closed context response diagnostic') from None


def context_response_diagnostic(body, expected_payload, *, decoder_result, body_complete=True):
    """Retain whole-body evidence and a finite category, never raw provider values."""
    from .rna_langsmith_live import _validate_context_wire_run, workflow_error_code
    operation='finish' if 'end_time' in expected_payload else 'start'
    _validate_context_wire_run(expected_payload,operation, generation="dispatch_event_count" in expected_payload.get("extra", {}).get("metadata", {}))
    if type(decoder_result) is not str or decoder_result not in {'exact_revision_match','state_match_only','mismatch','not_visible','invalid_response'} or type(body_complete) is not bool:
        raise ValueError('Closed context diagnostic result required')
    row=dict(span_id=expected_payload['id'],body_length=None,body_sha256=None,mismatch_category='body_unread')
    if body is None or type(body) is not bytes:
        return validate_context_response_diagnostic(row)
    if len(body)>_CONTEXT_RESPONSE_BYTES:
        row['mismatch_category']='body_oversized';return validate_context_response_diagnostic(row)
    if not body_complete:
        row['mismatch_category']='body_incomplete';return validate_context_response_diagnostic(row)
    row.update(body_length=len(body),body_sha256=digest(body),mismatch_category='schema_mismatch')
    try:
        observed=_response_json(body)
        if 'id' not in observed and 'runs' in observed:
            if not isinstance(observed['runs'],list) or len(observed['runs'])!=1:
                return validate_context_response_diagnostic(row)
            observed=observed['runs'][0]
        if not isinstance(observed,dict):return validate_context_response_diagnostic(row)
        error=workflow_error_code(expected_payload['extra']['metadata']['source_status']) if operation=='finish' else None
        status='error' if error is not None else 'success' if operation=='finish' else 'pending'
        if observed.get('status')!=status:category='provider_status_mismatch'
        elif observed.get('error')!=error:category='provider_error_mismatch'
        elif any(observed.get(key)!=expected_payload.get(key) for key in ('id','trace_id','parent_run_id','session_id')):category='identity_mismatch'
        elif observed.get('dotted_order')!=expected_payload['dotted_order']:category='order_mismatch'
        else:
            category='match'
            from .rna_langsmith_binding import _timestamp
            for key in ('start_time','end_time'):
                value=observed.get(key)
                if key=='end_time' and value is None and key not in expected_payload:continue
                expected=_timestamp(expected_payload.get(key)).astimezone(timezone.utc)
                if isinstance(value,str) and re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?',value):
                    if datetime.fromisoformat(value)!=expected.replace(tzinfo=None):category='timing_mismatch';break
                    observed[key]=expected.isoformat(timespec='microseconds')
                elif _timestamp(value).astimezone(timezone.utc)!=expected:category='timing_mismatch';break
            if category=='match':
                for key in ('inputs','outputs'):
                    if observed.get(key) is None and expected_payload[key]=={}:observed[key]={}
                expected_vector=normalized_payload(expected_payload)
                candidate=_subset(normalized_payload(observed),expected_vector)
                if candidate is _MISMATCH or canonical_json_bytes(candidate)!=canonical_json_bytes(expected_vector):category='content_mismatch'
                else:
                    proof=observed.get('extra',{}).get('metadata',{}).get('remote_proof')
                    if canonical_json_bytes(proof)!=canonical_json_bytes(expected_payload['extra']['metadata']['remote_proof']):category='proof_mismatch'
                    elif decoder_result!='exact_revision_match':category='schema_mismatch'
        row['mismatch_category']=category
    except Exception:
        pass
    return validate_context_response_diagnostic(row)


def context_readback_response_diagnostic(body, expected_payload, *, decoder_result, body_complete=True):
    """Add a closed provider-status observation without retaining provider text."""
    row = context_response_diagnostic(body, expected_payload, decoder_result=decoder_result,
        body_complete=body_complete)
    status = 'missing'
    if type(body) is bytes and body_complete is True and len(body) <= _CONTEXT_RESPONSE_BYTES:
        try:
            observed = _response_json(body)
            value = observed.get('status') if isinstance(observed, dict) else None
            if value is not None:
                status = value if type(value) is str and value in {'pending','success','error'} else 'unrecognized'
        except Exception:
            status = 'unrecognized'
    row['actual_provider_status'] = status
    return row


class ContextRunVerifier(JoinedRunVerifier):
    """Stored /4 session verifier with separately retained closed read diagnostics."""
    _query_fields=JOINED_CONTEXT_REMOTE_FIELDS

    def __init__(self, *, expected_topology, **kwargs):
        if not isinstance(expected_topology,dict) or not 1<=len(expected_topology)<=16:
            raise ValueError('Bounded retained context starts required')
        self._context_starts=deepcopy(expected_topology)
        self._context_session=None;self.diagnostics=[]
        transport=kwargs['read_transport']
        def complete_bytes_transport(**arguments):
            response=transport(**arguments)
            if not isinstance(response,dict) or type(response.get('body')) is not bytes:
                raise ValueError('Verified complete response bytes required')
            return response
        kwargs['read_transport']=complete_bytes_transport
        super().__init__(**kwargs)

    def bind_session(self, session):
        first=next(iter(self._context_starts.values()))
        _validate_context_expected_topology(first,self._context_starts,session,self._expected_context)
        self._context_session=_copy(session)

    def _validate_expected_run(self, expected_payload, operation):
        from .rna_langsmith_live import validate_context_run_bindings
        if self._context_session is None:raise ValueError('Bound context session /4 required')
        validate_context_run_bindings(expected_payload,operation,self._context_session)
        _validate_context_expected_topology(expected_payload,self._context_starts,self._context_session,self._expected_context)

    def _decode_response(self, raw, method, expected_payload):
        result='invalid_response'
        try:
            result,vector=decode_joined_context_run_response(raw,schema_format=(JOINED_GENERATION_CONTEXT_RESPONSE_SCHEMA_FORMAT if self._context_session["format"] == "devbench.rna-live-trace-session/5" else JOINED_CONTEXT_RESPONSE_SCHEMA_FORMAT),
                method=method,expected_payload=expected_payload,project_receipt=self._project_receipt,
                expected_context=self._expected_context,expected_topology=self._context_starts,
                expected_session=self._context_session)
            return result,vector
        finally:
            self.diagnostics.append(context_response_diagnostic(raw,expected_payload,decoder_result=result))
