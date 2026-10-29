"""Create/inspect/remove one owned, never-started proxy on an internal bridge.

No public bridge, process, DNS/TLS/auth request, private mount or credential read
is supported. Unknown daemon strings and environment values are never retained.
"""
from __future__ import annotations
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import sys
import time
W=Path('/Users/vajraang/Documents/ChatGPT/bio-agent-harness')
sys.path.insert(0,str(W))
from devbench.analysis.codex_isolated_boundary import DOCKER,SOCKET,closed_environment
from devbench.analysis.codex_signedin import _run_process
from devharness_controlled.native_runtime import IMAGE_ENV
SUPERVISOR=W/'scripts/run_gate1_personal_device_auth.py'
SUPERVISOR_SHA='d2987a1f9f3ab53dffb3e311826e5733e205589f40caa8de08a734ba23a5550a'
ROOT=W/'reports/rna-native-gate1/2026-10-21-v2/auth-preparation/proxy-metadata-v1'


def load_supervisor():
    if SUPERVISOR.is_symlink()or hashlib.sha256(SUPERVISOR.read_bytes()).hexdigest()!=SUPERVISOR_SHA:raise PermissionError('frozen_auth_supervisor_changed')
    spec=importlib.util.spec_from_file_location('fixed_metadata_only_auth_profile',SUPERVISOR)
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
    m.ROOT=ROOT;m.NAMES={k:'devharness-gate1-auth-metadata-v1-'+k for k in ['inside','outside','proxy','probe','client']}
    return m


def shape(value):
    result={'type':type(value).__name__}
    if value is None or type(value)is bool:result['value']=value
    elif type(value)is int and -1<=value<=17179869184:result['value']=value
    elif value in [[],['.'],['timeout:1','attempts:1'],['127.0.0.1']]:result['value']=value
    elif type(value)in [str,list,dict]:result['length']=min(len(value),1000000)
    return result


def projection(record,m,network,container):
    config,host=record.get('Config',{}),record.get('HostConfig',{})
    args=m.container_arguments('proxy',network);command=args[args.index(m.IMAGE)+1:]
    expected={'NetworkMode':network,'ReadonlyRootfs':True,'Privileged':False,'Memory':134217728,'MemorySwap':134217728,
        'NanoCpus':1000000000,'PidsLimit':16,'PidMode':'','IpcMode':'private','PublishAllPorts':False,
        'CapDrop':['ALL'],'SecurityOpt':['no-new-privileges=true'],'Dns':[],'DnsSearch':['.'],
        'DnsOptions':['timeout:1','attempts:1'],'LogConfig':{'Type':'none','Config':{}},
        'Tmpfs':{'/tmp':'rw,nosuid,nodev,noexec,size=4194304,mode=1777'}}
    fields={key:{'matches_frozen_expectation':type(host.get(key))is type(value)and host.get(key)==value,
        'observed':shape(host.get(key)),'expected':shape(value)}for key,value in expected.items()}
    env=config.get('Env');approved=list(IMAGE_ENV)
    config_checks={'image_matches':record.get('Image')==m.IMAGE,'name_matches':record.get('Name')=='/'+m.NAMES['proxy'],
        'id_matches':record.get('Id')==container,'user_matches':config.get('User')=='65534:65534',
        'entrypoint_matches':config.get('Entrypoint')==['/usr/bin/env'],'command_matches':config.get('Cmd')==command,
        'environment_matches':type(env)is list and len(env)==len(approved)and set(env)==set(approved),
        'expected_network_names_match':set(record.get('NetworkSettings',{}).get('Networks',{}))=={m.NAMES['inside']}}
    mounts=record.get('Mounts',[])
    config_checks['mounts_match']=(len(mounts)==1 and mounts[0].get('Source')==str(ROOT/'input/auth_proxy.py')
        and mounts[0].get('Destination')=='/input/auth_proxy.py'and mounts[0].get('Type')=='bind'and mounts[0].get('RW')is False)
    for key in ['CapAdd','Binds','Devices','DeviceRequests','PortBindings','VolumesFrom','ExtraHosts']:
        config_checks['absent_'+key]=not host.get(key)
    mismatch=[key for key,value in fields.items()if not value['matches_frozen_expectation']]+[key for key,value in config_checks.items()if not value]
    return {'host_fields':fields,'config_checks':config_checks,'mismatch_fields':mismatch,
        'raw_record_retained':False,'unknown_values_retained':False,'environment_values_retained':False}


def main():
    target=ROOT/'result.json'
    if target.exists()or target.is_symlink():raise FileExistsError('one_shot_metadata_result_exists')
    release=json.loads((ROOT/'release.json').read_text())
    if release.get('accepted')is not True or release.get('scope')!='auth-proxy-create-inspect-only':raise PermissionError('exact_metadata_release_required')
    m=load_supervisor();guard=m.load_pinned(m.GATEWAY,m.GATEWAY_SHA,'fixed_metadata_internal_guard')
    def custody():
        for path,expected in release['source_sha256'].items():
            if m.digest(Path(path))!=expected:raise PermissionError('metadata_source_changed')
        if {p.name for p in(ROOT/'input').iterdir()}!={'auth_proxy.py'}:raise PermissionError('proxy_public_source_only')
        if(ROOT/'docker-client/config.json').read_bytes()!=b'{"auths":{},"proxies":{}}\n':raise PermissionError('closed_docker_config_required')
    custody()
    with os.fdopen(os.open(ROOT/'attempt.json',os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600),'w')as stream:
        stream.write(json.dumps({'format':'devbench.auth-proxy-metadata-intent/1','never_start_guest':True,'public_networks_allowed':False,'private_mounts_allowed':False})+'\n');stream.flush();os.fsync(stream.fileno())
    prefix=[DOCKER,'--config',str(ROOT/'docker-client'),'--host',SOCKET]
    result={'format':'devbench.auth-proxy-metadata-only/1','calls':[],'never_started':True,'public_networks_created':0,
        'guest_processes_started':0,'private_state_created':False,'auth_requests':0,'tls_sessions':0,
        'live_generation_allowed':False,'biological_execution_allowed':False,'mutation_identity_uncertain':False}
    network=None;container=None
    def call(args,missing=False):
        custody();raw=_run_process(prefix+args,cwd=ROOT,environment=closed_environment(ROOT/'docker-client'),prompt=b'',
            deadline=time.monotonic()+12,maximum=65536,authorize=lambda:None)
        result['calls'].append({'operation':args[0],'exit_code':raw['exit_code'],'error':raw['error'],'process_reaped':raw['process_reaped'],'stdout_bytes':len(raw['stdout']),'stderr_bytes':len(raw['stderr'])})
        if missing:
            marker=b'No such container'if args[0]=='container'else b'not found'
            if raw['exit_code']!=1 or raw['error']is not None or not raw['process_reaped']or marker not in raw['stderr']or args[-1].encode()not in raw['stderr']:raise RuntimeError('metadata_resource_absence_not_verified')
        elif raw['exit_code']!=0 or raw['error']is not None or not raw['process_reaped']:raise RuntimeError('bounded_metadata_call_failed')
        return raw['stdout']
    def identity(data):
        value=data.decode().strip()
        if re.fullmatch('[0-9a-f]{64}',value)is None:raise RuntimeError('owned_metadata_id_required')
        return value
    try:
        result['mutation_identity_uncertain']=True;network=identity(call(m.network_arguments('inside')));result['mutation_identity_uncertain']=False
        net=json.loads(call(['network','inspect',network]))[0];m.verify_network(net,'inside',network,set(),guard)
        result['mutation_identity_uncertain']=True;container=identity(call(m.container_arguments('proxy',network)));result['mutation_identity_uncertain']=False
        record=json.loads(call(['container','inspect',container]))[0]
        result['projection']=projection(record,m,network,container)
        result['created_not_started']=(record.get('Id')==container and record.get('State',{}).get('Status')=='created'and record.get('State',{}).get('Running')is False)
        try:m.verify_container(record,'proxy',container,[m.NAMES['inside']],network);result['original_guard_accepts']=True
        except Exception as error:result['original_guard_accepts']=False;result['guard_error_category']=type(error).__name__
        result['metadata_collected']=True
    except Exception as error:result['error_category']=type(error).__name__
    finally:
        clean=True
        if container:
            try:call(['container','rm','--force',container]);call(['container','inspect',container],True)
            except Exception:clean=False
        if network:
            try:call(['network','rm',network]);call(['network','inspect',network],True)
            except Exception:clean=False
        result['owned_resources_removed']=clean;result['owned_container_id']=container;result['owned_network_id']=network
        try:custody();result['source_custody_unchanged']=True
        except Exception:result['source_custody_unchanged']=False
        with os.fdopen(os.open(target,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600),'w')as stream:stream.write(json.dumps(result,indent=2)+'\n')
    print(json.dumps({key:result[key]for key in ['metadata_collected','original_guard_accepts','owned_resources_removed','source_custody_unchanged']if key in result}))
    return 0 if result.get('metadata_collected')and result.get('created_not_started')and result['owned_resources_removed']and result['source_custody_unchanged']and not result['mutation_identity_uncertain']else 1


if __name__=='__main__':raise SystemExit(main())
