"""Independent bounded read and exact-operation reconciliation controls."""
from copy import deepcopy
from datetime import datetime,timezone
import json
import time
from uuid import uuid4
import pytest
from devbench.study import rna_langsmith_verify as verify
from devbench.study.rna_hosted_trace_contract import IDENTITY_KEYS
from devbench.study.contracts import canonical_json_bytes
from devbench.study.langsmith_outbox import digest,ExportOutbox
from tests.test_rna_hosted_trace_contract import manifest,criteria
from tests.test_rna_langsmith_live import live_fixture


def prepared(tmp_path):
    box,session,root,receipt,sanitize=live_fixture(tmp_path)
    session.update(format='devbench.rna-live-trace-session/2',project_id=str(uuid4()),workspace_id=str(uuid4()))
    item=sanitize(receipt(str(uuid4()),root,0,'active'),'start')
    item['payload']['session_id']=session['project_id']
    item['payload']=verify.bind_remote_proof(item['payload'],item['operation'])
    item['operation']['payload_sha256']=digest(canonical_json_bytes(item['payload']))
    value,identities=manifest(tmp_path)
    value.update(endpoint=session['endpoint'],workspace_id=session['workspace_id'],project_id=session['project_id'],project_name=session['project'],live_session_digest=digest(canonical_json_bytes(session)))
    return box,session,item,value


def test_uuid_bound_session_and_closed_proof(tmp_path):
    from devbench.study.rna_langsmith_live import validate_live_session,validate_live_run
    _,session,item,_=prepared(tmp_path)
    assert validate_live_session(session)==session
    assert validate_live_run(item['payload'],'start')==item['payload']
    changed=deepcopy(item['payload']);changed['extra']['metadata']['remote_proof']['prompt']='CANARY'
    with pytest.raises(ValueError):validate_live_run(changed,'start')
    changed=deepcopy(item['payload']);changed['name']='rna.coordinator.workflow'
    with pytest.raises(ValueError):validate_live_run(changed,'start')


def test_eventual_visibility_normalizes_timestamps_and_ignores_provider_additions(tmp_path):
    _,_,item,value=prepared(tmp_path);remote=deepcopy(item['payload'])
    remote['start_time']=datetime.fromisoformat(remote['start_time'].replace('Z','+00:00')).isoformat()
    remote.update(tenant_id=value['workspace_id'],provider_secret='CANARY');remote['extra']['metadata']['service_addition']='CANARY'
    responses=iter([{'status_code':404,'body':{}},{'status_code':200,'body':remote}])
    seen=[]
    def read(**kwargs):seen.append(kwargs);return next(responses)
    c=criteria();c['poll_interval_seconds']=.001
    verifier=verify.RemoteRunVerifier(manifest=value,criteria=c,api_key=None,read_transport=read)
    result=verifier.observe(operation=item['operation'],expected_payload=item['payload'],persisted_monotonic_ns=time.monotonic_ns())
    assert result['verification_result']=='exact_revision_match'
    assert result['reviewer_approval_digest'] is None and len(seen)==2
    assert 'CANARY' not in json.dumps(result) and 'CANARY' not in json.dumps(verifier.attempts)
    assert result['observation_interval']['first_matching_monotonic_ns']>=result['observation_interval']['last_nonmatching_monotonic_ns']


@pytest.mark.parametrize('changed',[{'session_id':str(uuid4())},{'tenant_id':str(uuid4())},{'parent_run_id':str(uuid4())},{'trace_id':str(uuid4())},{'inputs':{'prompt':'CANARY'}},{'name':'wrong'}])
def test_identity_or_content_mismatch_is_not_acknowledgment(tmp_path,changed):
    _,_,item,value=prepared(tmp_path);remote={**deepcopy(item['payload']),'tenant_id':value['workspace_id'],**changed}
    verifier=verify.RemoteRunVerifier(manifest=value,criteria=criteria(),api_key=None,read_transport=lambda **kw:{'status_code':200,'body':remote})
    result=verifier.observe(operation=item['operation'],expected_payload=item['payload'],persisted_monotonic_ns=time.monotonic_ns())
    assert result['verification_result']=='mismatch'
    assert 'CANARY' not in json.dumps(result)


def test_missing_or_later_revision_proof_cannot_acknowledge(tmp_path):
    _,_,item,value=prepared(tmp_path);remote={**deepcopy(item['payload']),'tenant_id':value['workspace_id']}
    remote['extra']['metadata'].pop('remote_proof')
    verifier=verify.RemoteRunVerifier(manifest=value,criteria=criteria(),api_key=None,read_transport=lambda **kw:{'status_code':200,'body':remote})
    result=verifier.observe(operation=item['operation'],expected_payload=item['payload'],persisted_monotonic_ns=time.monotonic_ns())
    assert result['verification_result']=='state_match_only'
    remote['extra']['metadata']['remote_proof']={**item['payload']['extra']['metadata']['remote_proof'],'revision':42}
    assert verifier.observe(operation=item['operation'],expected_payload=item['payload'],persisted_monotonic_ns=time.monotonic_ns())['verification_result']=='mismatch'


@pytest.mark.parametrize('code,expected',[(401,'auth_denied'),(403,'auth_denied'),(409,'conflict'),(429,'rate_limited'),(500,'server_error'),(302,'redirect')])
def test_read_failure_does_not_mutate_or_retry_unbounded(tmp_path,code,expected):
    _,_,item,value=prepared(tmp_path);calls=[]
    def read(**kw):calls.append(kw);return {'status_code':code,'body':{'message':'CANARY'}}
    verifier=verify.RemoteRunVerifier(manifest=value,criteria=criteria(),api_key=None,read_transport=read)
    result=verifier.observe(operation=item['operation'],expected_payload=item['payload'],persisted_monotonic_ns=time.monotonic_ns())
    assert result['response_class']==expected and len(calls)==1
    assert result['verification_result']=='blocked'


def test_timeout_oversize_and_query_are_bounded(tmp_path):
    _,_,item,value=prepared(tmp_path)
    def timeout(**kw):raise TimeoutError('CANARY')
    verifier=verify.RemoteRunVerifier(manifest=value,criteria=criteria(),api_key=None,read_transport=timeout)
    assert verifier.observe(operation=item['operation'],expected_payload=item['payload'],persisted_monotonic_ns=time.monotonic_ns())['response_class']=='timeout'
    verifier=verify.RemoteRunVerifier(manifest=value,criteria=criteria(),api_key=None,read_transport=lambda **kw:{'status_code':200,'body':b'x'*262145})
    assert verifier.observe(operation=item['operation'],expected_payload=item['payload'],persisted_monotonic_ns=time.monotonic_ns())['response_class']=='oversized'
    value['allowed_operations']=['query_runs'];calls=[]
    def query(**kw):calls.append(kw);return {'status_code':200,'body':{'runs':[]}}
    c=criteria();c['visibility_deadline_seconds']=.002;c['poll_interval_seconds']=.001
    verifier=verify.RemoteRunVerifier(manifest=value,criteria=c,api_key=None,read_transport=query)
    result=verifier.observe(operation=item['operation'],expected_payload=item['payload'],persisted_monotonic_ns=time.monotonic_ns())
    assert result['verification_result']=='not_visible'
    assert all(x['method']=='POST' and x['path']=='/runs/query' and x['payload']['id']==[item['payload']['id']] and x['payload']['limit']==1 for x in calls)


def review_fixture(tmp_path):
    from devbench.study.rna_langsmith_live import sanitize_live_event
    from tests.test_rna_langsmith_live import session as old_session
    from tests.test_rna_trace_contract import event
    session=old_session();session.update(format='devbench.rna-live-trace-session/2',project_id=str(uuid4()),workspace_id=str(uuid4()))
    root=str(uuid4());evt=event(trace_id=root,span_id=root,parent_span_id=None,role='coordinator',phase='workflow',kind='reservation',source_status='active',dispatch_status='prepared',bindings={'workflow':'d'*64,'source':'a'*64,'runtime':'b'*64,'policy':'c'*64},clock={'utc':datetime.now(timezone.utc).isoformat().replace('+00:00','Z'),'monotonic_ns':time.monotonic_ns(),'process_alias':str(uuid4()),'timing_basis':'observed'})
    item=sanitize_live_event(evt,authorized_session_manifest=session,capability={'backend_kind':'offline_recorded','qualification_status':'offline_qualified','admitted':True},allocation=0,actual_children=0,operation='start')
    source=tmp_path/'seed'/'payload.json';source.parent.mkdir();raw=canonical_json_bytes(dict(record_format='devbench.rna-langsmith-export/1',privacy_policy='rna-allowlist/1',data_scope='synthetic',runs=[item['payload']]))
    source.write_bytes(raw)
    box=ExportOutbox.create_live(tmp_path/'journal'/'outbox.sqlite',payload_path=source,expected_sha256=digest(raw),authorized_session_manifest=session,initial_operations=[item])
    mutations=[]
    def lost(**kw):mutations.append(kw);raise TimeoutError()
    box.deliver(transport=lost,allow_upload=True);box.deliver(transport=lost,allow_upload=True)
    assert len(mutations)==1
    value,_=manifest(tmp_path);value.update(endpoint=session['endpoint'],workspace_id=session['workspace_id'],project_id=session['project_id'],project_name=session['project'],live_session_digest=digest(canonical_json_bytes(session)))
    remote={**item['payload'],'tenant_id':session['workspace_id']}
    verifier=verify.RemoteRunVerifier(manifest=value,criteria=criteria(),api_key=None,read_transport=lambda **kw:{'status_code':200,'body':remote})
    candidate=verifier.observe(operation=item['operation'],expected_payload=item['payload'],persisted_monotonic_ns=time.monotonic_ns())
    approval=dict(format='devbench.rna-remote-review/1',receipt_digest=digest(canonical_json_bytes(candidate)),approved=True,reviewer_alias=str(uuid4()))
    path=tmp_path/'approval.json';path.write_bytes(canonical_json_bytes(approval))
    reviewed={**candidate,'reviewer_approval_digest':digest(path.read_bytes())}
    return box,verifier,reviewed,path,item,source,mutations


def test_reviewed_cas_requires_issued_receipt_retained_record_and_exact_target(tmp_path):
    box,verifier,reviewed,path,item,source,mutations=review_fixture(tmp_path)
    value=verifier.manifest
    with pytest.raises(ValueError):box.reconcile_reviewed({**reviewed,'project_id':str(uuid4())},approval_record=path,verifier=verifier)
    with pytest.raises(ValueError):box.reconcile_reviewed(reviewed,approval_record=path,verifier=verify.RemoteRunVerifier(manifest=value,criteria=criteria(),api_key=None,read_transport=lambda **kw:None))
    with box._db() as db:db.execute('INSERT INTO leases VALUES(?,?)',(str(uuid4()),time.time()+60))
    with pytest.raises(RuntimeError):box.reconcile_reviewed(reviewed,approval_record=path,verifier=verifier)
    with box._db() as db:db.execute('DELETE FROM leases')
    assert box.reconcile_reviewed(reviewed,approval_record=path,verifier=verifier)['counts']['uncertain']==0
    with pytest.raises(ValueError):box.reconcile_reviewed(reviewed,approval_record=path,verifier=verifier)
    assert len(mutations)==1


def test_root_parent_cannot_be_filled_by_service(tmp_path):
    _,_,item,value=prepared(tmp_path);item['payload']['parent_run_id']=None
    item['payload']=verify.bind_remote_proof(item['payload'],item['operation']);item['operation']['payload_sha256']=digest(canonical_json_bytes(item['payload']))
    remote={**deepcopy(item['payload']),'tenant_id':value['workspace_id'],'parent_run_id':str(uuid4())}
    verifier=verify.RemoteRunVerifier(manifest=value,criteria=criteria(),api_key=None,read_transport=lambda **kw:{'status_code':200,'body':remote})
    assert verifier.observe(operation=item['operation'],expected_payload=item['payload'],persisted_monotonic_ns=time.monotonic_ns())['verification_result']=='mismatch'


def test_read_authentication_failure_stops_later_reads(tmp_path):
    _,_,item,value=prepared(tmp_path);calls=[]
    def read(**kw):calls.append(kw);return {'status_code':401,'body':{}}
    verifier=verify.RemoteRunVerifier(manifest=value,criteria=criteria(),api_key=None,read_transport=read)
    for _ in range(2):
        assert verifier.observe(operation=item['operation'],expected_payload=item['payload'],persisted_monotonic_ns=time.monotonic_ns())['verification_result']=='blocked'
    assert len(calls)==1


def test_expiry_and_late_matching_read_do_not_pass(tmp_path,monkeypatch):
    _,_,item,value=prepared(tmp_path);value['expires_at']='2000-01-01T00:00:00Z'
    verifier=verify.RemoteRunVerifier(manifest=value,criteria=criteria(),api_key=None,read_transport=lambda **kw:pytest.fail('Expired read'))
    assert verifier.observe(operation=item['operation'],expected_payload=item['payload'],persisted_monotonic_ns=time.monotonic_ns())['verification_result']=='blocked'
    value['expires_at']='2099-01-01T00:00:00Z'
    c=criteria();c['visibility_deadline_seconds']=.001;c['poll_interval_seconds']=.001
    def late(**kw):
        time.sleep(.005)
        return {'status_code':200,'body':{**item['payload'],'tenant_id':value['workspace_id']}}
    verifier=verify.RemoteRunVerifier(manifest=value,criteria=c,api_key=None,read_transport=late)
    assert verifier.observe(operation=item['operation'],expected_payload=item['payload'],persisted_monotonic_ns=time.monotonic_ns())['verification_result']=='not_visible'


def test_explicit_rest_read_transport_has_bounded_allowlisted_requests(tmp_path,monkeypatch):
    _,_,item,value=prepared(tmp_path);value.update(mode='hosted',authorize_remote_read=True)
    requests=[];sizes=[]
    class Response:
        status=200
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def read(self,size):sizes.append(size);return b'{}'
    class Opener:
        def open(self,request,*,timeout):requests.append(request);return Response()
    monkeypatch.setattr(verify,'build_opener',lambda *handlers:Opener())
    transport=verify.RESTReadTransport(manifest=value,api_key='fake-placeholder',allowed_run_ids=[item['payload']['id']],allowed_trace_ids=[item['payload']['trace_id']])
    assert transport(method='GET',path='/runs/'+item['payload']['id'],payload=None,timeout=1)['body']==b'{}'
    query=dict(id=[item['payload']['id']],session=[value['project_id']],trace=[item['payload']['trace_id']],select=list(verify.REMOTE_FIELDS),limit=1)
    transport(method='POST',path='/runs/query',payload=query,timeout=1)
    assert sizes==[262145,262145]
    assert [x.get_method() for x in requests]==['GET','POST']
    for kwargs in [dict(method='GET',path='/runs/'+str(uuid4()),payload=None),dict(method='POST',path='/runs',payload=item['payload']),dict(method='POST',path='/runs/query',payload={**query,'limit':2}),dict(method='GET',path='/sessions',payload=None)]:
        with pytest.raises((ValueError,PermissionError)):transport(timeout=1,**kwargs)
    assert len(requests)==2


def test_open_remote_span_null_end_time_and_later_terminal_state_are_distinguished(tmp_path):
    _,_,item,value=prepared(tmp_path);remote={**deepcopy(item['payload']),'tenant_id':value['workspace_id'],'end_time':None}
    verifier=verify.RemoteRunVerifier(manifest=value,criteria=criteria(),api_key=None,read_transport=lambda **kw:{'status_code':200,'body':remote})
    assert verifier.observe(operation=item['operation'],expected_payload=item['payload'],persisted_monotonic_ns=time.monotonic_ns())['verification_result']=='exact_revision_match'
    # Later terminal state cannot prove this exact start revision's remote proof.
    remote['end_time']=datetime.now(timezone.utc).isoformat()
    remote['extra']['metadata']['remote_proof']['revision']+=1
    assert verifier.observe(operation=item['operation'],expected_payload=item['payload'],persisted_monotonic_ns=time.monotonic_ns())['verification_result']=='mismatch'


def test_incomplete_stale_or_missing_finish_is_not_exact_content(tmp_path):
    _,_,item,value=prepared(tmp_path);finish=deepcopy(item)
    finish['operation'].update(operation='finish',revision=1,operation_id=str(uuid4()))
    finish['payload']['end_time']=datetime.now(timezone.utc).isoformat()
    finish['payload']['extra']['metadata']['source_status']='completed'
    finish['payload']=verify.bind_remote_proof(finish['payload'],finish['operation']);finish['operation']['payload_sha256']=digest(canonical_json_bytes(finish['payload']))
    remote={**item['payload'],'tenant_id':value['workspace_id']}
    verifier=verify.RemoteRunVerifier(manifest=value,criteria=criteria(),api_key=None,read_transport=lambda **kw:{'status_code':200,'body':remote})
    assert verifier.observe(operation=finish['operation'],expected_payload=finish['payload'],persisted_monotonic_ns=time.monotonic_ns())['verification_result']=='mismatch'


def test_rest_read_refuses_fake_credentials_and_ambient_network_scope(tmp_path,monkeypatch):
    _,_,item,value=prepared(tmp_path)
    monkeypatch.setenv('LANGSMITH_API_KEY','CANARY')
    monkeypatch.setattr(verify,'build_opener',lambda *x:pytest.fail('Fake secret transport creation'))
    with pytest.raises(PermissionError):verify.RESTReadTransport(manifest=value,api_key=None,allowed_run_ids=[item['payload']['id']],allowed_trace_ids=[item['payload']['trace_id']])


def test_read_budget_and_hosted_unknown_schema_never_guess_api_fields(tmp_path):
    _,_,item,value=prepared(tmp_path);calls=[]
    def read(**kw):calls.append(kw);return {'status_code':404,'body':{}}
    value['limits']['max_read_requests']=1
    verifier=verify.RemoteRunVerifier(manifest=value,criteria=criteria(),api_key=None,read_transport=read)
    for _ in range(2):assert verifier.observe(operation=item['operation'],expected_payload=item['payload'],persisted_monotonic_ns=time.monotonic_ns())['verification_result']=='not_visible'
    assert len(calls)==1
    value.update(mode='hosted',authorize_remote_read=True)
    verifier=verify.RemoteRunVerifier(manifest=value,criteria=criteria(),api_key='fake-placeholder',read_transport=lambda **kw:pytest.fail('Unqualified hosted schema'))
    result=verifier.observe(operation=item['operation'],expected_payload=item['payload'],persisted_monotonic_ns=time.monotonic_ns())
    assert result['response_class']=='unsupported_schema' and verifier.read_requests==0


def test_fake_bounded_query_matches_one_exact_attested_span(tmp_path):
    _,_,item,value=prepared(tmp_path);value['allowed_operations']=['query_runs']
    remote={**deepcopy(item['payload']),'tenant_id':value['workspace_id']}
    verifier=verify.RemoteRunVerifier(manifest=value,criteria=criteria(),api_key=None,read_transport=lambda **kw:{'status_code':200,'body':{'runs':[remote]}})
    assert verifier.observe(operation=item['operation'],expected_payload=item['payload'],persisted_monotonic_ns=time.monotonic_ns())['verification_result']=='exact_revision_match'
    verifier=verify.RemoteRunVerifier(manifest=value,criteria=criteria(),api_key=None,read_transport=lambda **kw:{'status_code':200,'body':{'runs':[remote,remote]}})
    assert verifier.observe(operation=item['operation'],expected_payload=item['payload'],persisted_monotonic_ns=time.monotonic_ns())['verification_result']=='blocked'


def test_missing_remote_parent_is_not_filled_from_expected_root(tmp_path):
    _,_,item,value=prepared(tmp_path);item['payload']['parent_run_id']=None
    item['payload']=verify.bind_remote_proof(item['payload'],item['operation']);item['operation']['payload_sha256']=digest(canonical_json_bytes(item['payload']))
    remote={**deepcopy(item['payload']),'tenant_id':value['workspace_id']};remote.pop('parent_run_id')
    verifier=verify.RemoteRunVerifier(manifest=value,criteria=criteria(),api_key=None,read_transport=lambda **kw:{'status_code':200,'body':remote})
    assert verifier.observe(operation=item['operation'],expected_payload=item['payload'],persisted_monotonic_ns=time.monotonic_ns())['verification_result']=='mismatch'


def test_auth_denied_after_deadline_is_blocked_not_censored(tmp_path):
    _,_,item,value=prepared(tmp_path);c=criteria();c['visibility_deadline_seconds']=.001;c['poll_interval_seconds']=.001
    def denied(**kw):time.sleep(.005);return {'status_code':403,'body':{}}
    verifier=verify.RemoteRunVerifier(manifest=value,criteria=c,api_key=None,read_transport=denied)
    result=verifier.observe(operation=item['operation'],expected_payload=item['payload'],persisted_monotonic_ns=time.monotonic_ns())
    assert result['verification_result']=='blocked' and result['response_class']=='auth_denied'


def test_outbox_initial_uuid_session_requires_exact_operation_proof(tmp_path):
    box,session,root,receipt,sanitize=live_fixture(tmp_path)
    session.update(format='devbench.rna-live-trace-session/2',project_id=str(uuid4()),workspace_id=str(uuid4()))
    from devbench.study.rna_langsmith_live import sanitize_live_event
    item=sanitize_live_event(receipt(root,None,0,'active','workflow'),authorized_session_manifest=session,capability={'backend_kind':'offline_recorded','qualification_status':'offline_qualified','admitted':True},allocation=0,actual_children=0,operation='start')
    # Proof is internally valid but identifies another immutable operation.
    item['payload']=verify.bind_remote_proof(item['payload'],{**item['operation'],'operation_id':str(uuid4())})
    item['operation']['payload_sha256']=digest(canonical_json_bytes(item['payload']))
    raw=canonical_json_bytes(dict(record_format='devbench.rna-langsmith-export/1',privacy_policy='rna-allowlist/1',data_scope='synthetic',runs=[item['payload']]))
    source=tmp_path/'v2seed'/'payload.json';source.parent.mkdir();source.write_bytes(raw)
    with pytest.raises(ValueError):ExportOutbox.create_live(tmp_path/'v2journal'/'box.sqlite',payload_path=source,expected_sha256=digest(raw),authorized_session_manifest=session,initial_operations=[item])
    assert not (tmp_path/'v2journal'/'box.sqlite').exists()



def test_source_drift_and_digest_only_review_do_not_release_uncertainty(tmp_path):
    box,verifier,reviewed,path,item,source,mutations=review_fixture(tmp_path)
    raw=source.read_bytes();source.write_bytes(raw+b" ")
    with pytest.raises(ValueError):box.reconcile_reviewed(reviewed,approval_record=path,verifier=verifier)
    source.write_bytes(raw)
    assert box.status()['counts']['uncertain']==1
    path.write_bytes(canonical_json_bytes(dict(format='devbench.rna-remote-review/1',receipt_digest='f'*64,approved=True,reviewer_alias=str(uuid4()))))
    with pytest.raises(ValueError):box.reconcile_reviewed({**reviewed,'reviewer_approval_digest':digest(path.read_bytes())},approval_record=path,verifier=verifier)
    assert box.status()['counts']['uncertain']==1 and len(mutations)==1


def test_multiple_uncertain_revisions_and_legacy_escape_are_rejected(tmp_path):
    box,verifier,reviewed,path,item,source,mutations=review_fixture(tmp_path)
    finish=deepcopy(item)
    finish['operation'].update(operation='finish',revision=1,operation_id=str(uuid4()))
    finish['payload']['end_time']=datetime.now(timezone.utc).isoformat()
    finish['payload']['extra']['metadata']['source_status']='completed'
    finish['payload']=verify.bind_remote_proof(finish['payload'],finish['operation']);finish['operation']['payload_sha256']=digest(canonical_json_bytes(finish['payload']))
    box.enqueue_operation(finish['operation'],finish['payload'])
    with box._db() as db:db.execute("UPDATE operations SET state='uncertain' WHERE operation_id=?",(finish['operation']['operation_id'],))
    with pytest.raises(ValueError):box.reconcile_reviewed(reviewed,approval_record=path,verifier=verifier)
    with pytest.raises(PermissionError):box.reconcile(item['payload']['id'],remote_state='absent_verified',reviewed_receipt_digest='f'*64)
    assert box.status()['counts']['uncertain']==2 and len(mutations)==1


def test_initial_wire_and_item_proofs_cannot_diverge_before_storage(tmp_path):
    box,session,root,receipt,sanitize=live_fixture(tmp_path)
    session.update(format='devbench.rna-live-trace-session/2',project_id=str(uuid4()),workspace_id=str(uuid4()))
    from devbench.study.rna_langsmith_live import sanitize_live_event
    item=sanitize_live_event(receipt(root,None,0,'active','workflow'),authorized_session_manifest=session,capability={'backend_kind':'offline_recorded','qualification_status':'offline_qualified','admitted':True},allocation=0,actual_children=0,operation='start')
    wire=verify.bind_remote_proof(item['payload'],{**item['operation'],'operation_id':str(uuid4())})
    item['operation']['payload_sha256']=digest(canonical_json_bytes(wire))
    raw=canonical_json_bytes(dict(record_format='devbench.rna-langsmith-export/1',privacy_policy='rna-allowlist/1',data_scope='synthetic',runs=[wire]))
    source=tmp_path/'wireseed'/'payload.json';source.parent.mkdir();source.write_bytes(raw)
    with pytest.raises(ValueError):ExportOutbox.create_live(tmp_path/'wirejournal'/'box.sqlite',payload_path=source,expected_sha256=digest(raw),authorized_session_manifest=session,initial_operations=[item])
    assert not (tmp_path/'wirejournal'/'box.sqlite').exists()


def test_v2_annotation_without_terminal_time_preserves_actual_wire_proof(tmp_path):
    from devbench.study.rna_langsmith_live import sanitize_live_event,validate_live_run
    box,session,root,receipt,sanitize=live_fixture(tmp_path)
    session.update(format='devbench.rna-live-trace-session/2',project_id=str(uuid4()),workspace_id=str(uuid4()))
    event=receipt(root,None,1,'cancelled','workflow')
    item=sanitize_live_event(event,authorized_session_manifest=session,capability={'backend_kind':'offline_recorded','qualification_status':'offline_qualified','admitted':True},allocation=0,actual_children=0,operation='annotation',start_time=event['clock']['utc'])
    before=canonical_json_bytes(item['payload'])
    assert 'end_time' not in item['payload']
    assert validate_live_run(item['payload'],'annotation')==item['payload']
    assert canonical_json_bytes(item['payload'])==before
    assert verify.validate_remote_proof(item['payload'])['semantic_payload_digest']==verify.semantic_digest(item['payload'])
