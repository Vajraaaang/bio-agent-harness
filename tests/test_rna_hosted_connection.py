"""Actual child-entry connection wiring, with pre-import native/network denial.

These are offline software tests. Trusted local operator files establish an
explicit integrity/trust assumption, not authenticated human approval.
"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import time
from uuid import uuid4

import pytest


def connection_fixture(tmp_path):
    import importlib.util
    assert importlib.util.find_spec('devbench.study.rna_hosted_connection') is not None, 'Actual child connection engine absent'
    assert importlib.util.find_spec('devbench.study.rna_hosted_connection_contract') is not None, 'Closed connection contract absent'
    from devbench.study import rna_hosted_exporter as exporter
    assert hasattr(exporter,'_native_credential_command'), 'Fixed bounded native helper command absent'
    from devbench.study.rna_hosted_connection import connection_identity
    from devbench.study.rna_hosted_connection_contract import prepare_connection_packet, TARGET
    from devbench.study.rna_langsmith_live import EVENT_FIELDS, sanitize_live_event
    from tests.test_rna_trace_contract import event
    identity = connection_identity()
    now = datetime.now(timezone.utc)
    expiry = (now + timedelta(minutes=5)).isoformat()
    target = dict(TARGET)
    session = dict(format='devbench.rna-live-trace-session/2', workflow_alias=str(uuid4()),
        workflow_binding_digest='d'*64, data_scope='synthetic',
        **{k: identity[k] for k in ('source_digest', 'runtime_digest', 'policy_digest')},
        endpoint=target['endpoint'], project=target['project_name'],
        workspace_id=target['workspace_id'], project_id=target['project_id'],
        redaction_version='rna-allowlist/1', event_schema='devbench.rna-trace-event/1',
        allowed_event_fields=sorted(EVENT_FIELDS), max_spans=1, max_bytes=32768,
        max_delivery_attempts=1, expires_at=expiry, authorize_upload=True)
    span = str(uuid4())
    operations = []
    for revision, operation in enumerate(('start', 'finish')):
        evt = event(event_id=str(uuid4()), trace_id=span, span_id=span, parent_span_id=None,
            sequence=revision, kind='reservation' if revision == 0 else 'completion',
            source_status='active' if revision == 0 else 'completed', dispatch_status='attempted',
            bindings={'workflow': session['workflow_binding_digest'], 'source': identity['source_digest'],
                      'runtime': identity['runtime_digest'], 'policy': identity['policy_digest']},
            clock={'utc': (now + timedelta(milliseconds=revision)).isoformat().replace('+00:00','Z'),
                   'monotonic_ns': time.monotonic_ns(), 'process_alias': str(uuid4()),
                   'timing_basis': 'observed'})
        operations.append(sanitize_live_event(evt, authorized_session_manifest=session,
            capability={'backend_kind':'offline_recorded','qualification_status':'offline_qualified','admitted':True},
            allocation=0, actual_children=0, operation=operation,
            start_time=operations[0]['payload']['start_time'] if operations else None))
    session['authorize_upload'] = False
    packet = prepare_connection_packet(installed_identity=identity, target=target,
        session=session, operations=operations, expires_at=expiry,
        evidence_path=str(tmp_path / 'child'), now=now)
    return packet


def trusted_handles(tmp_path, packet):
    from devbench.study.contracts import canonical_json_bytes
    from devbench.study.rna_hosted_connection_contract import packet_digest
    owner, reviewer = str(uuid4()), str(uuid4())
    review = dict(format='devbench.rna-connection-review/2', packet_digest=packet_digest(packet),
        binding=packet['binding'], target=packet['target'], expires_at=packet['expires_at'],
        approved=True, reviewer_id=reviewer, implementation_owner_id=owner)
    records = {'source_runtime_review': review}
    for action in ('target_read', 'post_run_payload'):
        proposal = packet[action]
        records[action] = dict(format='devbench.rna-hosted-action-approval/2',
            proposal_digest=hashlib.sha256(canonical_json_bytes(proposal)).hexdigest(),
            action=action, approved=True, operator_id=reviewer,
            implementation_owner_id=owner, expires_at=packet['expires_at'])
    handles = {}
    for name, record in records.items():
        path = tmp_path / (name + '.json')
        raw = canonical_json_bytes(record)
        path.write_bytes(raw)
        path.chmod(0o600)
        handles[name] = {'path': str(path), 'sha256': hashlib.sha256(raw).hexdigest()}
    return handles


# Only this trusted test script instruments the child. There is no production
# test mode, readiness override, qualification override, or alternate entry.
TRUSTED_CHILD = r'''
import ctypes, io, json, os, pathlib, socket, subprocess, sys, urllib.request
real_popen = subprocess.Popen
config = json.loads(pathlib.Path(sys.argv[1]).read_text())
audit = pathlib.Path(sys.argv[2])
def log(kind, **fields):
    with audit.open('a') as stream: stream.write(json.dumps(dict(kind=kind, **fields))+'\n')
def denied(*args, **kwargs):
    log('forbidden')
    raise AssertionError('offline child native/network denied')
subprocess.Popen = denied
subprocess.run = denied
ctypes.CDLL = denied
os.system = denied
os.popen = denied
socket.socket.connect = denied
socket.socket.connect_ex = denied
socket.create_connection = denied
urllib.request.urlopen = denied
from devbench.study import rna_hosted_qualification as driver
from devbench.study import rna_hosted_exporter as exporter
from devbench.study import rna_langsmith_live as live
from devbench.study import rna_hosted_connection as connection
def producer(command, **kwargs):
    assert command == exporter._native_credential_command()
    assert kwargs['env'] == exporter._native_credential_environment() and kwargs['close_fds']
    log('credential')
    code="import sys;sys.stdout.write('DUMMY-ONLY-CHILD-CREDENTIAL\\n')"
    if config.get('hold_credential'): code="import time;time.sleep(20);"+code
    return real_popen([sys.executable,'-I','-B','-c',code], **kwargs)
exporter.subprocess.Popen = producer
remote = {}
class Response:
    def __init__(self, status, body): self.status=status;self.stream=io.BytesIO(json.dumps(body).encode())
    def __enter__(self): return self
    def __exit__(self, *args): return False
    def read(self, size=-1):
        assert 0 <= size <= 65537
        if config.get('hold_http_body'):
            import time;time.sleep(90)
        return self.stream.read(size)
class Opener:
    def open(self, request, timeout):
        assert request.get_header('X-api-key') == 'DUMMY-ONLY-CHILD-CREDENTIAL'
        assert 0 < timeout <= 15
        method=request.get_method();path=request.full_url.split('langchain.com',1)[1]
        log('http', method=method, path=path)
        target=config['target']
        if method=='GET' and path=='/api/v1/sessions/'+target['project_id']+'?include_stats=false':
            body=dict(id=target['project_id'],tenant_id=target['workspace_id'],name=target['project_name'])
            body.update(config.get('project_changes',{}));return Response(200,body)
        if method in ('POST','PATCH'):
            if config.get('auth_status') and config.get('auth_method')==method:
                return Response(config['auth_status'],{'error':'DUMMY-PRIVATE-PROVIDER-ERROR'})
            if config.get('mutation_http_error'):
                from urllib.error import HTTPError
                raise HTTPError(request.full_url,config['mutation_http_error'],'DUMMY-PRIVATE-ERROR-TEXT',{},
                    io.BytesIO(b'{"api_key":"DUMMY-SECRET-BODY","error":"DUMMY-PRIVATE-ERROR"}'))
            body=json.loads(request.data)
            if config.get('reject_null_parent') and 'parent_run_id' in body and body['parent_run_id'] is None:
                return Response(400,{'error':'DUMMY-PRIVATE-PROVIDER-ERROR'})
            if config.get('mutation_status'):
                return Response(config['mutation_status'],{'error':'DUMMY-PRIVATE-PROVIDER-ERROR'})
            remote[body['id']]=body
            if config.get('lost_response') and method=='PATCH':
                raise TimeoutError('DUMMY-PRIVATE-PROVIDER-ERROR')
            return Response(202, {})
        if method=='GET' and path.startswith('/api/v1/runs/'):
            if config.get('auth_status') and config.get('auth_method')=='GET':
                return Response(config['auth_status'],{'error':'DUMMY-PRIVATE-PROVIDER-ERROR'})
            body=remote.get(path.rsplit('/',1)[1])
            if body is None:return Response(404,{})
            body=dict(body);body.pop('session_name',None)
            body.update(dotted_order=body.get('dotted_order','synthetic-order'), app_path='/synthetic-run',
                        status='success' if 'end_time' in body else 'pending')
            assert 'tenant_id' not in body
            body.update(config.get('run_changes',{}));return Response(200,body)
        raise AssertionError('unapproved route')
def opener(*handlers):
    assert any(isinstance(h,urllib.request.ProxyHandler) and h.proxies == {} for h in handlers)
    return Opener()
live.build_opener=opener
connection.build_opener=opener
raise SystemExit(driver.main(['--hosted-exporter-child']))
'''


def child_channel(tmp_path, monkeypatch, *, approved=True, **config_changes):
    from devbench.study import rna_hosted_exporter as exporter
    packet = connection_fixture(tmp_path)
    handles = trusted_handles(tmp_path, packet) if approved else {}
    config = tmp_path / 'trusted-fixture.json'
    config.write_text(json.dumps(dict(target=packet['target'], **config_changes)))
    audit = tmp_path / 'audit.jsonl'
    monkeypatch.setattr(exporter, '_child_command', lambda: [
        sys.executable, '-I', '-B', '-c', TRUSTED_CHILD, str(config), str(audit)])
    channel = exporter.HostedExporterChannel(root=tmp_path / 'child',
        descriptor={'format':'devbench.rna-hosted-exporter-session/2',
                    'packet':packet,'approval_handles':handles},
        credential_handle=exporter.keychain_credential_handle())
    return channel, packet, audit


def audit_rows(path):
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def test_connection_default_off_actual_entry_never_resolves_or_requests(tmp_path, monkeypatch):
    with child_channel(tmp_path, monkeypatch, approved=False)[0] as channel:
        state = channel.request({'command':'status'})
        assert state['status'] == 'blocked' and state['credential_resolutions'] == 0
        assert channel.request({'command':'boundary'})['credential_resolutions'] == 0
    assert audit_rows(tmp_path / 'audit.jsonl') == []


def test_connection_actual_entry_positive_exact_two_mutations_and_joined_read(tmp_path, monkeypatch):
    import sqlite3
    channel, packet, audit = child_channel(tmp_path, monkeypatch)
    with channel:
        assert channel.request({'command':'status'})['status'] == 'admitted'
        boundary = channel.request({'command':'boundary'})
        assert boundary['pid'] != os.getpid() and boundary['credential_resolutions'] == 1
        assert boundary['tracing_enabled'] is boundary['inherited_context'] is False
        channel.request({'command':'prepare'})
        state = channel.request({'command':'drain'})
        assert state['counts']['acknowledged'] == 2
        receipt = channel.request({'command':'verify'})
        assert receipt['verification_result'] == 'exact_revision_match'
        channel.request({'command':'drain'})
        assert state['deployed_schema_qualified'] is False
    rows = audit_rows(audit)
    assert [r['kind'] for r in rows].count('credential') == 1
    assert [r['method'] for r in rows if r['kind']=='http'] == ['GET','POST','PATCH','GET']
    journal=tmp_path/'child'/'outbox'/'journal.sqlite'
    with sqlite3.connect('file:'+str(journal)+'?mode=ro',uri=True) as db:
        source=Path(db.execute('SELECT payload_path FROM exports').fetchone()[0])
    assert source.is_file() and source.parent!=journal.parent
    assert not journal.is_relative_to(source.parent) and not source.is_relative_to(journal.parent)
    assert all('DUMMY-' not in p.read_text(errors='ignore') for p in (tmp_path / 'child').rglob('*') if p.is_file())


@pytest.mark.parametrize('changes', [dict(tenant_id=str(uuid4())),dict(id=str(uuid4())),dict(name='renamed')])
def test_connection_wrong_project_blocks_mutations_after_bounded_project_read(tmp_path, monkeypatch, changes):
    with pytest.raises(ValueError):
        child_channel(tmp_path, monkeypatch, project_changes=changes)
    rows=audit_rows(tmp_path / 'audit.jsonl')
    assert [r['method'] for r in rows if r['kind']=='http'] == ['GET']
    assert not any(r['kind']=='forbidden' for r in rows)


def test_connection_accepted_response_loss_is_not_resent_and_needs_review(tmp_path, monkeypatch):
    channel, packet, audit=child_channel(tmp_path, monkeypatch, lost_response=True)
    with channel:
        channel.request({'command':'prepare'})
        state=channel.request({'command':'drain'})
        assert state['counts']['uncertain'] == 1 and state['counts']['acknowledged'] == 1
        channel.request({'command':'drain'})
        receipt=channel.request({'command':'verify'})
        assert receipt['verification_result']=='exact_revision_match'
        assert channel.request({'command':'status'})['counts']['uncertain']==1
        review={ 'format':'devbench.rna-remote-review/1', 'receipt_digest': hashlib.sha256(
            json.dumps(receipt,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()).hexdigest(),
            'approved':True,'reviewer_alias':str(uuid4())}
        review_path=tmp_path / 'reviewed-reconciliation.json'
        raw=json.dumps(review,sort_keys=True,separators=(',',':')).encode();review_path.write_bytes(raw)
        review_path.chmod(0o600)
        receipt['reviewer_approval_digest']=hashlib.sha256(raw).hexdigest()
        state=channel.request({'command':'reconcile','receipt':receipt,
            'approval_handle':{'path':str(review_path),'sha256':hashlib.sha256(raw).hexdigest()}})
        assert state['counts']['uncertain']==0 and state['counts']['acknowledged']==2
        with pytest.raises(ValueError):
            channel.request({'command':'reconcile','receipt':receipt,
                'approval_handle':{'path':str(review_path),'sha256':hashlib.sha256(raw).hexdigest()}})
    rows=audit_rows(audit)
    assert [r['method'] for r in rows if r['kind']=='http'].count('PATCH')==1
    assert 'DUMMY-' not in json.dumps(state)


def test_connection_closed_commands_cannot_replace_packet_operations(tmp_path, monkeypatch):
    channel, _, _=child_channel(tmp_path, monkeypatch)
    with channel:
        for request in ({'command':'prepare','operations':[]},
                        {'command':'drain','api_key':'DUMMY-RAW'},
                        {'command':'verify','path':'/api/v1/runs/other'},
                        {'command':'fault','kind':'accept_then_timeout'}):
            with pytest.raises(ValueError):channel.request(request)
        assert channel.request({'command':'status'})['counts']['mutation_requests']==0


@pytest.mark.parametrize('status',[401,403])
@pytest.mark.parametrize('method',['POST','PATCH','GET'])
def test_connection_auth_denial_stops_all_later_http(tmp_path,monkeypatch,status,method):
    channel,_,audit=child_channel(tmp_path,monkeypatch,auth_status=status,auth_method=method)
    with channel:
        channel.request({'command':'prepare'})
        channel.request({'command':'drain'})
        if method=='GET':
            receipt=channel.request({'command':'verify'})
            assert receipt['response_class']=='auth_denied'
        state=channel.request({'command':'status'})
        assert state['status']=='blocked' and state['blockers']==['auth_denied']
        before=len(audit_rows(audit))
        assert channel.request({'command':'drain'})['status']=='blocked'
        assert channel.request({'command':'verify'})['status']=='blocked'
        assert len(audit_rows(audit))==before


@pytest.mark.parametrize('location',['receipt','approval_handle'])
def test_connection_nested_secret_rejected_before_parent_ipc(monkeypatch,location):
    from devbench.study.rna_hosted_exporter import HostedExporterChannel
    channel=HostedExporterChannel.__new__(HostedExporterChannel)
    channel._connection_v2=True
    monkeypatch.setattr(channel,'_exchange_bytes',lambda value:pytest.fail('malformed IPC sent'))
    request={'command':'reconcile','receipt':{},'approval_handle':{}}
    request[location]['api_key']='DUMMY-RAW-NEVER-IPC'
    with pytest.raises(ValueError) as caught:channel.request(request)
    assert 'DUMMY-' not in str(caught.value)


@pytest.mark.parametrize('location',['packet','session','payload'])
def test_connection_nested_packet_secret_rejected_before_process_or_files(tmp_path,monkeypatch,location):
    from devbench.study import rna_hosted_exporter as exporter
    packet=connection_fixture(tmp_path)
    target={'packet':packet,'session':packet['session'],'payload':packet['operations'][0]['payload']}[location]
    target['api_key']='DUMMY-RAW-NEVER-SPAWN'
    handles=trusted_handles(tmp_path,packet)
    monkeypatch.setattr(exporter.subprocess,'Popen',lambda *args,**kwargs:pytest.fail('Malformed descriptor spawned'))
    with pytest.raises(ValueError) as caught:
        exporter.HostedExporterChannel(root=tmp_path/'child',
            descriptor={'format':'devbench.rna-hosted-exporter-session/2','packet':packet,'approval_handles':handles},
            credential_handle=exporter.keychain_credential_handle())
    assert 'DUMMY-' not in str(caught.value)
    assert not (tmp_path/'child').exists()


def test_connection_legacy_rest_still_rejects_v2_without_admission(tmp_path):
    from devbench.study.rna_langsmith_live import RESTLiveTransport
    packet=connection_fixture(tmp_path)
    with pytest.raises(PermissionError):
        RESTLiveTransport(authorized_session_manifest={**packet['session'],'authorize_upload':True},api_key='dummy')


@pytest.mark.parametrize('hold,minimum,maximum,reads,reason',[
    ('hold_credential',4,15,0,'initialization_failed'),
    ('hold_http_body',58,72,1,'budget_exhausted')])
def test_connection_real_child_wall_bounds_dummy_producer_and_held_http_body(
        tmp_path,hold,minimum,maximum,reads,reason):
    """Controlled POSIX software deadline; no general OS preemption claim."""
    import sqlite3
    import subprocess
    from devbench.study.rna_hosted_exporter import keychain_credential_handle
    from devbench.study.rna_hosted_qualification import minimal_environment
    packet=connection_fixture(tmp_path);handles=trusted_handles(tmp_path,packet)
    config=tmp_path/'trusted-fixture.json'
    config.write_text(json.dumps({'target':packet['target'],hold:True}))
    audit=tmp_path/'audit.jsonl'
    value={'command':'initialize','descriptor':{'format':'devbench.rna-hosted-exporter-session/2',
        'packet':packet,'approval_handles':handles},'credential_handle':keychain_credential_handle()}
    before=time.monotonic()
    process=subprocess.Popen([sys.executable,'-I','-B','-c',TRUSTED_CHILD,str(config),str(audit)],
        stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
        cwd=tmp_path,env=minimal_environment(),close_fds=True)
    try:
        stdout,stderr=process.communicate(json.dumps(value).encode()+b'\n',timeout=75)
    finally:
        if process.poll() is None:process.kill();process.wait(timeout=3)
    elapsed=time.monotonic()-before
    assert minimum<=elapsed<=maximum
    assert process.returncode==0
    assert stdout==b'{"error":"Exporter operation rejected"}\n' and stderr==b''
    assert b'DUMMY-' not in stdout+stderr
    with sqlite3.connect(tmp_path/'child'/'connection.sqlite') as db:
        row=db.execute('SELECT packet_id,reads,mutations,requests,stopped_reason FROM connection').fetchone()
    assert row==(packet['packet_id'],reads,0,reads,reason)
    if hold=='hold_credential':
        failure=json.loads((tmp_path/'child'/'initialization-failure.json').read_bytes())
        assert failure=={'format':'devbench.rna-credential-failure/1','stage':'credential_resolution',
            'reason':'credential_lookup_timeout'}
    rows=audit_rows(audit)
    assert sum(row['kind']=='credential' for row in rows)==1
    assert all(row.get('method','GET')=='GET' for row in rows)
    assert not any(row['kind']=='forbidden' for row in rows)


@pytest.mark.parametrize('field', ['source_digest','runtime_digest','policy_digest','criteria_digest','fixture_manifest_digest'])
def test_connection_source_drift_cannot_self_approve_or_resolve(tmp_path, monkeypatch, field):
    from devbench.study import rna_hosted_exporter as exporter
    packet=connection_fixture(tmp_path)
    packet['binding'][field]='e'*64
    handles=trusted_handles(tmp_path,packet)
    config=tmp_path/'trusted-fixture.json';config.write_text(json.dumps({'target':packet['target']}))
    audit=tmp_path/'audit.jsonl'
    monkeypatch.setattr(exporter,'_child_command',lambda:[sys.executable,'-I','-B','-c',
        TRUSTED_CHILD,str(config),str(audit)])
    with pytest.raises(ValueError):
        exporter.HostedExporterChannel(root=tmp_path/'child',
            descriptor={'format':'devbench.rna-hosted-exporter-session/2','packet':packet,'approval_handles':handles},
            credential_handle=exporter.keychain_credential_handle())
    assert audit_rows(audit)==[]


def test_connection_consumed_packet_restart_cannot_resolve_again(tmp_path, monkeypatch):
    from devbench.study import rna_hosted_exporter as exporter
    channel,packet,audit=child_channel(tmp_path,monkeypatch,lost_response=True)
    with channel:
        channel.request({'command':'prepare'})
        retained=channel.request({'command':'drain'})['counts']
        assert retained['uncertain']==1 and retained['acknowledged']==1
    journal=tmp_path/'child'/'outbox'/'journal.sqlite'
    original_journal=journal.read_bytes()
    handles={name:{'path':str(tmp_path/(name+'.json')),
        'sha256':hashlib.sha256((tmp_path/(name+'.json')).read_bytes()).hexdigest()}
        for name in ('source_runtime_review','target_read','post_run_payload')}
    with exporter.HostedExporterChannel(root=tmp_path/'child',restart=True,
        descriptor={'format':'devbench.rna-hosted-exporter-session/2','packet':packet,'approval_handles':handles},
        credential_handle=exporter.keychain_credential_handle()) as restarted:
        state=restarted.request({'command':'status'})
        assert state['status']=='blocked' and state['counts']==retained
        assert restarted.request({'command':'boundary'})['credential_resolutions']==0
    assert journal.read_bytes()==original_journal
    assert sum(row['kind']=='credential' for row in audit_rows(audit))==1
    assert [row['method'] for row in audit_rows(audit) if row['kind']=='http']==['GET','POST','PATCH']


def _identity_fixture(tmp_path,monkeypatch):
    import base64
    from importlib.metadata import PathDistribution
    from types import SimpleNamespace
    from devbench.study import rna_hosted_qualification as driver
    prefix=tmp_path/'installed';prefix.mkdir()
    metadata=prefix/'devharness-0.dist-info';metadata.mkdir()
    package=prefix/'devbench'/'analysis';(package/'assets').mkdir(parents=True)
    module=package/'__init__.py';module.write_bytes(b'')
    archive=package/'assets'/'source-bundle.zip';archive.write_bytes(b'synthetic-resource')
    record=metadata/'RECORD'
    encoded=base64.urlsafe_b64encode(hashlib.sha256(archive.read_bytes()).digest()).decode().rstrip('=')
    record.write_text('devbench/analysis/assets/source-bundle.zip,sha256='+encoded+',18\n')
    dist=PathDistribution(metadata)
    monkeypatch.setattr(driver,'distribution',lambda name:dist)
    monkeypatch.setattr(driver.sys,'prefix',str(prefix))
    monkeypatch.setattr(driver.importlib.util,'find_spec',lambda name:SimpleNamespace(origin=str(module)))
    monkeypatch.setattr(PathDistribution,'files',property(lambda self:pytest.fail('Unbounded RECORD reader called')))
    return driver,record,archive


def test_bounded_connection_identity_preserves_hashes_and_allows_installed_hardlinks(tmp_path,monkeypatch):
    driver,record,archive=_identity_fixture(tmp_path,monkeypatch)
    os.link(archive,archive.parent/'allowed-installed-hardlink')
    identity=driver.bounded_installed_identity()
    assert identity['source_digest']==hashlib.sha256(archive.read_bytes()).hexdigest()
    assert identity['runtime_digest']==hashlib.sha256(record.read_bytes()).hexdigest()


@pytest.mark.parametrize('mutation',['record_oversize','record_symlink','archive_symlink','archive_fifo','archive_oversize'])
def test_bounded_connection_identity_rejects_nonregular_or_oversized_inputs_before_reads(tmp_path,monkeypatch,mutation):
    driver,record,archive=_identity_fixture(tmp_path,monkeypatch)
    if mutation=='record_oversize':record.write_bytes(b'x'*(2*1024*1024+1))
    elif mutation=='record_symlink':
        target=record.with_name('private-fixture-record');record.rename(target);record.symlink_to(target)
    elif mutation=='archive_symlink':
        target=archive.with_name('private-fixture-archive');archive.rename(target);archive.symlink_to(target)
    elif mutation=='archive_fifo':archive.unlink();os.mkfifo(archive)
    elif mutation=='archive_oversize':
        with archive.open('wb') as stream:stream.truncate(64*1024*1024+1)
    with pytest.raises((ValueError,OSError)):
        driver.bounded_installed_identity()
