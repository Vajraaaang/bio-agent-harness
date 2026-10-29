from pathlib import Path
import importlib.util
import pytest
W=Path('/Users/vajraang/Documents/ChatGPT/bio-agent-harness')

def load():
    spec=importlib.util.spec_from_file_location('tls_v3_under_test',W/'scripts/run_gate1_auth_tls_probe_v3.py')
    shim=importlib.util.module_from_spec(spec);spec.loader.exec_module(shim);return shim


@pytest.mark.parametrize('value',[None,[]])
def test_only_explicit_unset_proxy_dns_normalizes_without_mutating_record(value):
    shim=load();record={'HostConfig':{'Dns':value,'Other':'unchanged'}}
    result=shim.normalize_proxy_unset_dns(record,'proxy')
    assert result['HostConfig']['Dns']==[] and result['HostConfig']['Other']=='unchanged'
    assert record['HostConfig']['Dns']==value


@pytest.mark.parametrize('value',['',False,0,{},['8.8.8.8'],['127.0.0.1'],['']])
def test_other_falsy_or_nonempty_proxy_dns_is_rejected(value):
    with pytest.raises(PermissionError):load().normalize_proxy_unset_dns({'HostConfig':{'Dns':value}},'proxy')


def test_client_and_probe_dns_are_never_normalized():
    shim=load()
    for role in ['client','probe']:
        record={'HostConfig':{'Dns':None}}
        assert shim.normalize_proxy_unset_dns(record,role)is record
    with pytest.raises(PermissionError):shim.normalize_proxy_unset_dns({'HostConfig':{}},'proxy')


def test_v3_reuses_frozen_profile_and_fresh_tls_only_namespace():
    shim=load();runtime=shim.load_supervisor()
    assert shim.SUPERVISOR_SHA256=='d2987a1f9f3ab53dffb3e311826e5733e205589f40caa8de08a734ba23a5550a'
    assert runtime.MODE=='tls-only'and str(runtime.ROOT).endswith('/tls-attempt-v3')
    assert all(x.startswith('devharness-gate1-auth-tls-v3-')for x in runtime.NAMES.values())
    args=runtime.container_arguments('probe','inside-id');assert args[-1]=='tls-only'and str(runtime.PRIVATE)not in str(args)
    assert args[args.index('--dns')+1]=='127.0.0.1'


def test_real_frozen_guard_accepts_only_proxy_unset_defaults_and_keeps_client_loopback():
    shim=load();m=shim.load_supervisor();identity='a'*64;network='b'*64
    def record(role):
        proxy=role=='proxy';small=role!='client';args=m.container_arguments(role,network)
        mounts=[(m.ROOT/'input/auth_proxy.py','/input/auth_proxy.py',False)]if proxy else[(m.ROOT/'input','/input',False),(m.CLIENT,'/client',False),*([(m.PRIVATE,'/private',True)]if role=='client'else[])]
        return {'Id':identity,'Name':'/'+m.NAMES[role],'Image':m.IMAGE,
            'Config':{'User':'65534:65534'if proxy else'501:20','Entrypoint':['/usr/bin/env'],'Cmd':args[args.index(m.IMAGE)+1:],'Env':list(m.IMAGE_ENV)},
            'HostConfig':{'NetworkMode':network,'ReadonlyRootfs':True,'Privileged':False,'Memory':134217728 if small else 1073741824,
                'MemorySwap':134217728 if small else 1073741824,'NanoCpus':1000000000,'PidsLimit':16 if small else 64,'PidMode':'','IpcMode':'private','PublishAllPorts':False,
                'CapDrop':['ALL'],'SecurityOpt':['no-new-privileges=true'],'DnsSearch':['.'],'DnsOptions':['timeout:1','attempts:1'],
                'Dns':None if proxy else['127.0.0.1'],'LogConfig':{'Type':'none','Config':{}},
                'Tmpfs':{'/tmp':'rw,nosuid,nodev,noexec,size='+('4194304'if proxy else'16777216')+',mode=1777'}},
            'NetworkSettings':{'Networks':{m.NAMES['inside']: {}}},
            'Mounts':[{'Source':str(source),'Destination':destination,'Type':'bind','RW':rw}for source,destination,rw in mounts]}
    for role in ['proxy','client','probe']:
        value=record(role);m.verify_container(value,role,identity,[m.NAMES['inside']],network)
        if role!='proxy':
            value['HostConfig']['Dns']=None
            with pytest.raises(PermissionError):m.verify_container(value,role,identity,[m.NAMES['inside']],network)
