import importlib.util
import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import pytest
W=Path('/Users/vajraang/Documents/ChatGPT/bio-agent-harness')


def load():
    p=W/'scripts/run_gate1_account_read_probe.py';s=importlib.util.spec_from_file_location('account_supervisor_under_test',p)
    m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m


def frame():
    return {'format':'devbench.official-account-plan-read/1','account_type':'chatgpt','plan_type':'plus','requires_openai_auth':True,
        'allowed_personal_plan_observed':True,'accepted_account_plan_observation':True,'initialize_succeeded':True,
        'account_read_succeeded':True,'refresh_token_requested':False,'official_cli_invocations_attempted':1,
        'official_cli_launch_confirmed':True,'official_cli_exit_code':0,'process_reaped':True,'process_group_cleanup_uncertain':False,
        'error_category':None,'stream_bytes':1024,'raw_rpc_or_stderr_retained':False,'authentication_cache_read_by_worker':False,
        'authentication_cache_hashed_by_worker':False,'thread_requests':0,'turn_requests':0,'live_model_calls':0,
        'native_admission_qualified':False,'live_generation_allowed':False,'biological_execution_allowed':False}


def record(m,state='created'):
    return {'Id':'a'*64,'Name':'/'+m.NAME,'Image':m.IMAGE,'Config':{'User':'501:20','Entrypoint':['/usr/bin/env'],
        'Cmd':m.command(),'Env':list(m.IMAGE_ENV)},'HostConfig':{'NetworkMode':'none','ReadonlyRootfs':True,'Privileged':False,
        'Memory':1073741824,'MemorySwap':1073741824,'NanoCpus':1000000000,'PidsLimit':64,'PidMode':'','IpcMode':'private',
        'PublishAllPorts':False,'CapDrop':['ALL'],'SecurityOpt':['no-new-privileges=true'],'Dns':['127.0.0.1'],
        'DnsSearch':['.'],'DnsOptions':['timeout:1','attempts:1'],'LogConfig':{'Type':'none','Config':{}},'Tmpfs':dict(m.TMPFS)},
        'NetworkSettings':{'Networks':{'none':{}}},'Mounts':[{'Source':str(s),'Destination':d,'Type':'bind','RW':False}for s,d in m.mounts()],
        'State':{'Status':state,'Running':False,'ExitCode':0}}


def test_missing_auth_proof_stops_before_cache_metadata_or_docker(monkeypatch,tmp_path):
    m=load();monkeypatch.setattr(m.sys,'argv',['fixed-account']);monkeypatch.setattr(m,'DEVICE_RESULT',tmp_path/'absent-public-proof')
    monkeypatch.setattr(m,'auth_metadata',lambda:pytest.fail('private metadata must not be checked'))
    monkeypatch.setattr(m,'_run_process',lambda *_a,**_kw:pytest.fail('Docker must not be invoked'))
    with pytest.raises((FileNotFoundError,PermissionError)):m.main()


def test_proof_gate_requires_independent_acceptance_and_redacted_official_status(monkeypatch,tmp_path):
    m=load();base=m.load_base();proof={'action':'device-login','official_login_completed':True,'owned_runtime_resources_removed':True,
        'source_custody_unchanged':True,'mutation_identity_uncertain':False,'private_epoch_preserved_without_inspection':True,
        'attachment_exit_codes':{'proxy':0,'client':0}}
    # Reuse only public synthetic status shape; no account/auth state involved.
    spec=importlib.util.spec_from_file_location('public_status_fixture',W/'tests/test_gate1_personal_device_auth.py')
    t=importlib.util.module_from_spec(spec);spec.loader.exec_module(t);proof['login_result']=t.login_result()
    p=tmp_path/'public-result.json';r=tmp_path/'public-review.json';p.write_text(json.dumps(proof));r.write_text(json.dumps({'accepted_official_device_auth_and_status':True,'result_sha256':'fixed-public-digest'}))
    monkeypatch.setattr(m,'DEVICE_RESULT',p);monkeypatch.setattr(m,'DEVICE_REVIEW',r)
    base.digest=lambda *_args:'fixed-public-digest'
    assert m.authorize_auth(base)==('fixed-public-digest','fixed-public-digest')
    proof['login_result']['official_status_verified_chatgpt']=False;p.write_text(json.dumps(proof))
    with pytest.raises(ValueError):m.authorize_auth(base)


def test_profile_has_only_fixed_offline_credential_mount_and_bounded_ephemeral_home():
    m=load();args=m.arguments();assert args[args.index('--network')+1]=='none'and args[args.index('--user')+1]=='501:20'
    assert '25s'in args and '/probe-home:rw,nosuid,nodev,noexec,size=16777216,mode=700,uid=501,gid=20'in args
    assert len(m.mounts())==3 and m.mounts()[-1]==(m.AUTH,'/probe-home/auth.json')
    assert '/.codex/'not in str(args)and '/private'not in str(m.AUTH)
    m.verify_container(record(m),'a'*64)


@pytest.mark.parametrize('field',['mount','network','tmpfs','env','caps','dns'])
def test_actual_profile_rejects_boundary_drift(field):
    m=load();r=record(m)
    if field=='mount':r['Mounts'][-1]['RW']=True
    if field=='network':r['NetworkSettings']['Networks']['outside']={}
    if field=='tmpfs':r['HostConfig']['Tmpfs']['/probe-home']='rw,size=16777216,mode=777'
    if field=='env':r['Config']['Env'].append('OPENAI_API_KEY=owned-synthetic')
    if field=='caps':r['HostConfig']['CapAdd']=['SYS_ADMIN']
    if field=='dns':r['HostConfig']['Dns']=['8.8.8.8']
    with pytest.raises(PermissionError):m.verify_container(r,'a'*64)


def test_result_projection_rejects_pii_bool_aliases_and_inconsistent_success():
    m=load();assert m.validate_result(frame())==frame()
    for key,bad in [('email','owned-synthetic@example.invalid'),('thread_requests',False),('plan_type','business'),
        ('official_cli_exit_code',True),('authentication_cache_read_by_worker',True),('process_reaped',False)]:
        v=frame();v[key]=bad
        with pytest.raises(ValueError):m.validate_result(v)


def test_symlink_or_wrong_mode_auth_metadata_rejected_without_contents(monkeypatch,tmp_path):
    m=load();private=tmp_path/'private/epoch1'
    private.parent.mkdir(mode=0o700);private.mkdir(mode=0o700);(private/'codex-home').mkdir(mode=0o700)
    auth=private/'codex-home/auth.json';auth.symlink_to(tmp_path/'never-read')
    monkeypatch.setattr(m,'PRIVATE',private);monkeypatch.setattr(m,'AUTH',auth)
    with pytest.raises(PermissionError):m.auth_metadata()
    auth.unlink();auth.write_text('owned synthetic content');auth.chmod(0o644)
    with pytest.raises(PermissionError):m.auth_metadata()


def fake_main(m,tmp_path,monkeypatch,drift=False):
    root=tmp_path/'owned';root.mkdir();(root/'input').mkdir();(root/'docker-client').mkdir();client=tmp_path/'public-client';client.mkdir()
    (root/'input/account_probe.py').write_text('# synthetic public placeholder')
    (root/'docker-client/config.json').write_bytes(b'{"auths":{},"proxies":{}}\n')
    (root/'release.json').write_text(json.dumps({'accepted':True,'scope':'offline-official-account-read-only','source_sha256':{},'prerequisite_sha256':{}}))
    manifest=tmp_path/'manifest.json';manifest.write_text(json.dumps({'files':[]}))
    monkeypatch.setattr(m,'ROOT',root);monkeypatch.setattr(m,'CLIENT',client);monkeypatch.setattr(m,'MANIFEST',manifest)
    monkeypatch.setattr(m,'authorize_auth',lambda _base:('accepted-public-proof','accepted-public-review'))
    monkeypatch.setattr(m,'auth_metadata',lambda:('owned-metadata-only',));monkeypatch.setattr(m.sys,'argv',['fixed-account'])
    calls=[];inspects=0;removed=False
    def execute(cmd,**kwargs):
        nonlocal inspects,removed
        args=cmd[5:];calls.append(args);out=b'';err=b'';exit=0
        if args[0]=='create':out=('a'*64+'\n').encode()
        elif args[:2]==['container','rm']:removed=True
        elif args[:2]==['container','inspect']:
            inspects+=1
            if removed:exit=1;err=b'No such container: '+args[-1].encode()
            else:
                r=record(m,'created'if inspects==1 else'exited')
                if drift:r['Mounts'][-1]['RW']=True
                out=json.dumps([r]).encode()
        elif args[0]=='start':out=json.dumps(frame()).encode()
        return {'exit_code':exit,'error':None,'process_reaped':True,'stdout':out,'stderr':err}
    monkeypatch.setattr(m,'_run_process',execute)
    return root,calls


def test_prestart_profile_failure_removes_known_id_without_start(monkeypatch,tmp_path):
    m=load();root,calls=fake_main(m,tmp_path,monkeypatch,True)
    assert m.main()==1 and not any(args[0]=='start'for args in calls)
    assert ['container','rm','--force','a'*64]in calls
    result=json.loads((root/'result.json').read_text());assert result['owned_container_removed']is True


def test_projected_success_and_replay_denial_without_credential_content(monkeypatch,tmp_path):
    m=load();root,calls=fake_main(m,tmp_path,monkeypatch)
    assert m.main()==0
    result=json.loads((root/'result.json').read_text());assert result['account_observation_accepted']is True
    assert result['credential_contents_read_or_hashed']is False and result['private_directory_enumerated']is False
    count=len(calls);(root/'result.json').unlink()
    with pytest.raises(FileExistsError):m.main()
    assert len(calls)==count


def test_auth_metadata_drift_after_start_does_not_block_exact_id_cleanup(monkeypatch,tmp_path):
    m=load();root,calls=fake_main(m,tmp_path,monkeypatch)
    original=m._run_process;started=False
    def execute(cmd,**kwargs):
        nonlocal started
        result=original(cmd,**kwargs)
        if cmd[5]=='start':started=True
        return result
    def metadata():
        if started:raise PermissionError('owned synthetic lease changed')
        return ('owned-metadata-only',)
    monkeypatch.setattr(m,'_run_process',execute);monkeypatch.setattr(m,'auth_metadata',metadata)
    assert m.main()==1
    assert ['container','rm','--force','a'*64]in calls
    result=json.loads((root/'result.json').read_text())
    assert result['owned_container_removed']is True and result['source_and_auth_metadata_custody_unchanged']is False
