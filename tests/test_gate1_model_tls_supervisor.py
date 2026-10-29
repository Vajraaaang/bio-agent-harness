import importlib.util
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import pytest
W=Path('/Users/vajraang/Documents/ChatGPT/bio-agent-harness')


def load():
    p=W/'scripts/run_gate1_model_tls_probe.py';s=importlib.util.spec_from_file_location('model_tls_supervisor_under_test',p)
    m=importlib.util.module_from_spec(s);s.loader.exec_module(m);m.PROXY_IP='172.20.0.2';return m


def record(m,role):
    h={'NetworkMode':'n'*64,'ReadonlyRootfs':True,'Privileged':False,'Memory':134217728,'MemorySwap':134217728,
        'NanoCpus':1000000000,'PidsLimit':16,'PidMode':'','IpcMode':'private','PublishAllPorts':False,
        'CapDrop':['ALL'],'SecurityOpt':['no-new-privileges=true'],'Dns':None if role=='proxy'else['127.0.0.1'],
        'DnsSearch':['.'],'DnsOptions':['timeout:1','attempts:1'],'LogConfig':{'Type':'none','Config':{}},
        'Tmpfs':{'/tmp':'rw,nosuid,nodev,noexec,size=4194304,mode=1777'}}
    return {'Id':'a'*64,'Name':'/'+m.NAMES[role],'Image':m.IMAGE,'Config':{'User':'65534:65534','Entrypoint':['/usr/bin/env'],
        'Cmd':m.command(role),'Env':list(m.IMAGE_ENV)},'HostConfig':h,'NetworkSettings':{'Networks':{'inside':{}}},
        'Mounts':[{'Source':str(p),'Destination':d,'Type':'bind','RW':False}for p,d in m.mounts(role)]}


def test_only_public_files_no_credentials_client_or_application_command():
    m=load()
    for role in ['proxy','probe']:
        args=m.arguments(role,'n'*64)
        assert '--pull=never'in args and args[args.index('--user')+1]=='65534:65534'
        assert '.devbench-codex-auth'not in str(args)and '/client'not in str(args)and 'codex'not in str(m.command(role))
        assert m.command(role)[-1]=='172.20.0.2'if role=='probe'else m.command(role)[-1]=='/input/model_proxy_v2.py'
    assert len(m.mounts('probe'))==1 and len(m.mounts('proxy'))==2


@pytest.mark.parametrize('role',['proxy','probe'])
def test_actual_profile_requires_exact_mount_network_env_and_limits(role):
    m=load();value=record(m,role);m.verify_container(value,role,'a'*64,['inside'],'n'*64)
    for change in ['mount','env','network','memory','dns','cap']:
        bad=deepcopy(value)
        if change=='mount':bad['Mounts'].append({'Source':'/owned-extra','Destination':'/extra','RW':False,'Type':'bind'})
        if change=='env':bad['Config']['Env'].append('HTTP_PROXY=untrusted')
        if change=='network':bad['NetworkSettings']['Networks']['untrusted']={}
        if change=='memory':bad['HostConfig']['Memory']=True
        if change=='dns':bad['HostConfig']['Dns']=['8.8.8.8']
        if change=='cap':bad['HostConfig']['CapAdd']=['NET_ADMIN']
        with pytest.raises(PermissionError):m.verify_container(bad,role,'a'*64,['inside'],'n'*64)


def test_unset_proxy_dns_normalization_is_narrow_and_probe_dns_exact():
    m=load();value=record(m,'proxy')
    for good in [None,[]]:value['HostConfig']['Dns']=good;m.verify_container(value,'proxy','a'*64,['inside'],'n'*64)
    for bad in ['',False,0,{},['127.0.0.1']]:
        value['HostConfig']['Dns']=bad
        with pytest.raises(PermissionError):m.verify_container(value,'proxy','a'*64,['inside'],'n'*64)
    value=record(m,'probe');value['HostConfig']['Dns']=None
    with pytest.raises(PermissionError):m.verify_container(value,'probe','a'*64,['inside'],'n'*64)


def test_model_receipts_fixed_scope_strict_types_and_counter_caps():
    m=load();shared=m.load(W/'scripts/gate1_auth_proxy.py','511373e52f11e1aefe80df1d1deac375949accb2afca0a54edea24b47a4c7f89','test_model_proxy_schema')
    ready=m.proxy_template(shared,'ready');assert m.validate_proxy(ready,shared)=='ready'
    final=m.proxy_template(shared,'final');assert m.validate_proxy(final,shared)=='final'
    for key,bad in [('authority','auth.openai.com:443'),('live_generation_allowed',0),('transport_role','auth-only')]:
        value=deepcopy(ready);value[key]=bad
        with pytest.raises(ValueError):m.validate_proxy(value,shared)
    for bad in [True,33]:
        value=deepcopy(final);value['counters']['connections_seen']=bad
        with pytest.raises(ValueError):m.validate_proxy(value,shared)


def test_positive_probe_receipt_denies_bool_aliases_application_http_and_credentials():
    m=load();p=m.load(W/'scripts/gate1_model_tls_probe.py','a6dc72f236aed75f322ad4d01826b5c40b6fa91a9c858f0992e918da3214e196','test_model_probe_schema')
    value=p.receipt(True);assert m.validate_probe(value,p)==value
    for key,bad in [('passed',1),('application_http_sent',True),('credential_cache_accessed',True),('authority','auth.openai.com:443')]:
        v=deepcopy(value);v[key]=bad
        with pytest.raises(ValueError):m.validate_probe(v,p)


def test_first_create_failure_never_starts_or_removes_ambient_resources(tmp_path,monkeypatch):
    m=load();root=tmp_path/'owned';root.mkdir(mode=0o700);(root/'input').mkdir(mode=0o700);(root/'docker-client').mkdir(mode=0o700)
    (root/'docker-client/config.json').write_bytes(b'{"auths":{},"proxies":{}}\n')
    (root/'docker-client/config.json').chmod(0o600)
    for target,source in [('auth_proxy.py','gate1_auth_proxy.py'),('model_proxy_v2.py','gate1_model_proxy_v2.py'),('model_tls_probe.py','gate1_model_tls_probe.py')]:
        (root/'input'/target).write_bytes((W/'scripts'/source).read_bytes())
        (root/'input'/target).chmod(0o444)
    (root/'release.json').write_text(json.dumps({'accepted':True,'scope':'credential-free-chatgpt-tls-probe-only','source_sha256':{},'prerequisite_sha256':{}}))
    monkeypatch.setattr(m,'ROOT',root);monkeypatch.setattr(m.sys,'argv',['fixed-model-tls'])
    calls=[]
    def fail(command,**kwargs):
        calls.append(command)
        return {'exit_code':1,'error':None,'process_reaped':True,'stdout':b'','stderr':b'owned synthetic denied'}
    monkeypatch.setattr(m,'_run_process',fail)
    assert m.main()==1 and len(calls)==1 and calls[0][5:7]==['network','create']
    receipt=json.loads((root/'result.json').read_text())
    assert receipt['mutation_identity_uncertain']is True and receipt['owned_runtime_resources_removed']is True
    assert receipt['official_cli_invocations']==0 and receipt['private_state_created']is False
    (root/'result.json').unlink()
    with pytest.raises(FileExistsError):m.main()
    assert len(calls)==1
