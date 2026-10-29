"""Published request-schema and guarded hosted-root regressions; no live service."""
from copy import deepcopy
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
from uuid import uuid4

import jsonschema
import pytest

from tests.test_rna_hosted_connection import connection_fixture,child_channel,audit_rows


def published_schema():
    path=Path(__file__).resolve().parent/'fixtures/langsmith_run_request_schema.json'
    raw=path.read_bytes()
    assert hashlib.sha256(raw).hexdigest()=='6552c3361f96134245156bb2da5539fd147046a995bd0db0a2112b09d38be6f6'
    value=json.loads(raw)
    assert value['public_projection_sha256']=='d84319c23a6812fd87d1f6cb0f980742c2f53aec783b91770cffab7067646859'
    assert '$ref' not in json.dumps(value['schema']), 'No external schema resolution allowed'
    return jsonschema.Draft202012Validator(value['schema'])


@pytest.mark.parametrize('index',[0,1])
def test_hosted_root_start_finish_conform_to_published_request_schema(tmp_path,index):
    from devbench.study.contracts import canonical_json_bytes
    from devbench.study.rna_langsmith_verify import validate_remote_proof
    packet=connection_fixture(tmp_path);item=packet['operations'][index];run=item['payload']
    errors=[{'path':list(error.path),'validator':error.validator} for error in published_schema().iter_errors(run)]
    assert errors==[], 'Approved hosted root violates published request schema'
    assert 'parent_run_id' not in run
    expected_order=datetime.fromisoformat(run['start_time'].replace('Z','+00:00')).astimezone(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+run['id']
    assert run.get('dotted_order')==expected_order, 'Hosted root requires the documented trace/order pair'
    assert packet['operations'][0]['payload'].get('dotted_order')==packet['operations'][1]['payload'].get('dotted_order')
    assert run['inputs']==run['outputs']=={}
    assert item['operation']['payload_sha256']==hashlib.sha256(canonical_json_bytes(run)).hexdigest()
    assert validate_remote_proof(run)['revision']==index


def test_legacy_root_parent_and_hosted_nonnull_child_parent_unchanged(tmp_path):
    from tests.test_rna_langsmith_live import session as old_session
    from tests.test_rna_trace_contract import event
    from devbench.study.rna_langsmith_live import sanitize_live_event
    packet=connection_fixture(tmp_path)
    def make(session,*,parent):
        root=packet['operations'][0]['payload']['id'];identity=root if parent is None else str(uuid4())
        evt=event(event_id=str(uuid4()),trace_id=root,span_id=identity,parent_span_id=parent,
            role='coordinator' if parent is None else 'investigator',phase='workflow' if parent is None else 'invocation',
            kind='reservation',source_status='active',dispatch_status='attempted',
            bindings={'workflow':session['workflow_binding_digest'],'source':session['source_digest'],
                      'runtime':session['runtime_digest'],'policy':session['policy_digest']},
            clock={'utc':datetime.now(timezone.utc).isoformat().replace('+00:00','Z'),
                   'monotonic_ns':None,'process_alias':str(uuid4()),'timing_basis':'observed'})
        return sanitize_live_event(evt,authorized_session_manifest=session,
            capability={'backend_kind':'offline_recorded','qualification_status':'unknown','admitted':False},
            allocation=0,actual_children=0,operation='start')['payload']
    legacy=make(old_session(),parent=None)
    assert 'parent_run_id' in legacy and legacy['parent_run_id'] is None
    assert 'dotted_order' not in legacy
    parent=packet['operations'][0]['payload']['id']
    child=make({**packet['session'],'authorize_upload':True},parent=parent)
    assert child['parent_run_id']==parent
    assert 'dotted_order' not in child


@pytest.mark.parametrize('observed_parent',['absent','null'])
def test_joined_hosted_root_normalizes_omitted_or_null_parent_only_for_exact_root(tmp_path,observed_parent):
    from tests.test_rna_langsmith_binding import issue,decode
    from devbench.study.rna_langsmith_verify import bind_remote_proof,semantic_digest
    packet=connection_fixture(tmp_path);item=deepcopy(packet['operations'][1]);expected=item['payload']
    expected.pop('parent_run_id',None)
    expected=bind_remote_proof(expected,item['operation'])
    now=datetime.now(timezone.utc)
    context=dict(format='devbench.rna-project-binding-context/2',packet_digest='d'*64,session_digest='e'*64,
        action_digest='f'*64,source_digest=packet['binding']['source_digest'],runtime_digest=packet['binding']['runtime_digest'],
        **packet['target'],region='us',expires_at=packet['expires_at'],max_age_seconds=30)
    project=dict(id=packet['target']['project_id'],tenant_id=packet['target']['workspace_id'],name=packet['target']['project_name'])
    receipt,_=issue(context,project,now)
    remote={**deepcopy(expected),'dotted_order':expected.get('dotted_order','synthetic-order'),'status':'success','app_path':'/synthetic-run'}
    if observed_parent=='null':remote['parent_run_id']=None
    try:result,vector=decode(remote,expected,receipt,context,now)
    except ValueError:pytest.fail('Hosted root optional-parent normalization absent')
    assert result=='exact_revision_match'
    assert semantic_digest(remote)==semantic_digest(expected)
    remote['parent_run_id']=str(uuid4())
    try:wrong=decode(remote,expected,receipt,context,now)
    except ValueError:pass
    else:assert wrong[0]=='mismatch'


@pytest.mark.parametrize('bad_order',['DUMMY-SECRET', '20000101T000000000000Z00000000-0000-0000-0000-000000000000'])
def test_hosted_root_order_privacy_rejects_noncanonical_value(tmp_path,bad_order):
    from devbench.study.rna_langsmith_live import validate_live_run
    packet=connection_fixture(tmp_path);run=deepcopy(packet['operations'][1]['payload'])
    assert 'dotted_order' in run, 'Canonical hosted root order missing'
    run['dotted_order']=bad_order
    with pytest.raises(ValueError):validate_live_run(run,'finish')


def test_joined_hosted_root_wrong_order_cannot_acknowledge(tmp_path):
    from tests.test_rna_langsmith_binding import issue,decode
    packet=connection_fixture(tmp_path);expected=packet['operations'][1]['payload']
    assert 'dotted_order' in expected, 'Canonical hosted root order missing'
    now=datetime.now(timezone.utc)
    context=dict(format='devbench.rna-project-binding-context/2',packet_digest='d'*64,session_digest='e'*64,
        action_digest='f'*64,source_digest=packet['binding']['source_digest'],runtime_digest=packet['binding']['runtime_digest'],
        **packet['target'],region='us',expires_at=packet['expires_at'],max_age_seconds=30)
    project=dict(id=packet['target']['project_id'],tenant_id=packet['target']['workspace_id'],name=packet['target']['project_name'])
    receipt,_=issue(context,project,now)
    remote={**deepcopy(expected),'dotted_order':'synthetic-order','status':'success','app_path':'/synthetic-run'}
    assert decode(remote,expected,receipt,context,now)[0]=='mismatch'


def test_joined_nonnull_child_missing_parent_still_rejected(tmp_path):
    from tests.test_rna_langsmith_binding import joined,issue,decode
    remote,expected,_,context,project,now=joined(tmp_path)
    assert expected['parent_run_id'] is not None
    receipt,_=issue(context,project,now)
    remote.pop('parent_run_id')
    with pytest.raises(ValueError):decode(remote,expected,receipt,context,now)


@pytest.mark.parametrize('branch',['response','http_error'])
def test_actual_child_rejected_post_retains_numeric_status_without_provider_text(tmp_path,monkeypatch,branch):
    changes={'mutation_status':400} if branch=='response' else {'mutation_http_error':400}
    channel,_,audit=child_channel(tmp_path,monkeypatch,**changes)
    with channel:
        channel.request({'command':'prepare'})
        state=channel.request({'command':'drain'})
        assert state['counts']['rejected']==1 and state['counts']['mutation_requests']==1
        channel.request({'command':'drain'})
    path=tmp_path/'child'/'http-attempt-2.json'
    assert path.is_file(), 'Sanitized numeric request result absent'
    record=json.loads(path.read_bytes())
    assert record=={'format':'devbench.rna-http-attempt/1','request_index':2,'method':'POST',
        'route_kind':'run_start','http_status':400,'family':'client_rejected'}
    assert 'DUMMY-' not in json.dumps(record)
    assert all('DUMMY-' not in path.read_text(errors='ignore') for path in (tmp_path/'child').rglob('*') if path.is_file())
    assert [row['method'] for row in audit_rows(audit) if row['kind']=='http']==['GET','POST']


def test_actual_child_provider_rejects_null_root_and_accepts_corrected_approved_bytes(tmp_path,monkeypatch):
    channel,packet,audit=child_channel(tmp_path,monkeypatch,reject_null_parent=True)
    with channel:
        channel.request({'command':'prepare'})
        state=channel.request({'command':'drain'})
        assert state['counts']['acknowledged']==2 and state['counts']['rejected']==0
        assert channel.request({'command':'verify'})['verification_result']=='exact_revision_match'
    assert all('parent_run_id' not in row['payload'] for row in packet['operations'])
    records=[json.loads(path.read_bytes()) for path in sorted((tmp_path/'child').glob('http-attempt-*.json'))]
    assert [row['http_status'] for row in records]==[200,202,202,200]
    assert [row['method'] for row in audit_rows(audit) if row['kind']=='http']==['GET','POST','PATCH','GET']
