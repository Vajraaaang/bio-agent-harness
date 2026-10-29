"""One-shot reviewed Docker synthetic relay diagnostic; no public network mode."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time

WORKSPACE=Path('/Users/vajraang/Documents/ChatGPT/bio-agent-harness')
sys.path.insert(0,str(WORKSPACE))
from devbench.analysis.codex_isolated_boundary import DOCKER,IMAGE,SOCKET,closed_environment
from devbench.analysis.codex_signedin import _run_process
from devharness_controlled.native_runtime import IMAGE_ENV
ROOT=WORKSPACE/'reports/rna-native-gate1/2026-10-21-v2/egress-v3'
NAMES={key:'devharness-gate1-egress-v3-'+key for key in ('inside','mock','peer','proxy','client')}
POLICY={'live_generation_allowed':False,'biological_execution_allowed':False,'production_admission_released':False,'public_egress_qualified':False}

def digest(path):
    if path.is_symlink() or not path.is_file() or path.stat().st_size>100000000:raise PermissionError('ordinary_bounded_source_required')
    return hashlib.sha256(path.read_bytes()).hexdigest()

def verify_custody(release):
    for directory in (ROOT,ROOT/'input',ROOT/'docker-client'):
        if directory.is_symlink() or directory.resolve()!=directory or not directory.is_dir():raise PermissionError('ordinary_owned_directory_required')
    for name,expected in release['source_sha256'].items():
        if digest(Path(name))!=expected:raise PermissionError('reviewed_source_changed')
    if {p.name for p in (ROOT/'input').iterdir()}!={'fixture.py'}:raise PermissionError('fixed_input_only')
    if {p.name for p in (ROOT/'docker-client').iterdir()}!={'config.json'}:raise PermissionError('fixed_docker_config_only')
    if (ROOT/'docker-client/config.json').read_bytes()!=b'{"auths":{},"proxies":{}}\n':raise PermissionError('closed_docker_config_required')

def container_arguments(role,network,extra):
    command=['-i','PATH=/usr/bin:/bin','HOME=/tmp','LANG=C.UTF-8','/usr/bin/timeout','--signal=KILL','25s','/usr/bin/python3','-I','-B','/input/fixture.py',role,*extra]
    args=['create','--pull=never','--platform','linux/amd64','--name',NAMES[role],'--network',network,
        '--read-only','--cap-drop','ALL','--security-opt','no-new-privileges=true','--memory','134217728',
        '--memory-swap','134217728','--cpus','1','--pids-limit','16','--user','65534:65534',
        '--dns','127.0.0.1','--dns-option','timeout:1','--dns-option','attempts:1','--ipc','private','--log-driver','none',
        '--tmpfs','/tmp:rw,nosuid,nodev,noexec,size=4194304,mode=1777',
        '--mount',f'type=bind,src={ROOT / "input"},dst=/input,readonly',
        '--entrypoint','/usr/bin/env',IMAGE,*command]
    return args

def verify_container(record,role,networks,extra,initial_network_id):
    c,h=record['Config'],record['HostConfig']
    expected={'ReadonlyRootfs':True,'Privileged':False,'Memory':134217728,'MemorySwap':134217728,
        'NanoCpus':1000000000,'PidsLimit':16,'PidMode':'','IpcMode':'private','PublishAllPorts':False,'NetworkMode':initial_network_id}
    args=container_arguments(role,'unused',extra);command=args[args.index(IMAGE)+1:]
    valid=(record.get('Name')=='/'+NAMES[role] and record.get('Image')==IMAGE and c.get('User')=='65534:65534'
        and c.get('Entrypoint')==['/usr/bin/env'] and c.get('Cmd')==command
        and type(c.get('Env'))is list and len(c['Env'])==len(IMAGE_ENV) and set(c['Env'])==set(IMAGE_ENV)
        and all(type(h.get(k))is type(v) and h.get(k)==v for k,v in expected.items())
        and h.get('CapDrop')==['ALL'] and h.get('SecurityOpt')==['no-new-privileges=true']
        and h.get('Dns')==['127.0.0.1'] and h.get('DnsOptions')==['timeout:1','attempts:1'] and h.get('LogConfig')=={'Type':'none','Config':{}}
        and h.get('Tmpfs')=={'/tmp':'rw,nosuid,nodev,noexec,size=4194304,mode=1777'}
        and all(not h.get(k) for k in ('CapAdd','Binds','Devices','DeviceRequests','PortBindings','VolumesFrom','ExtraHosts'))
        and set(record['NetworkSettings']['Networks'])==set(networks))
    mounts=record.get('Mounts',[])
    valid=valid and len(mounts)==1 and mounts[0].get('Source')==str(ROOT/'input') and mounts[0].get('Destination')=='/input' and mounts[0].get('RW')is False and mounts[0].get('Type')=='bind'
    if not valid:raise PermissionError('actual_egress_enclosure_mismatch')

def canonical_ipv4_options(record):
    """Normalize only the two measured, equivalent IPv4 serializations.

    EnableIPv4 is authoritative. An optional redundant driver label must agree;
    no unknown option, coercion or absent host-gateway inhibition is accepted.
    """
    options=record.get('Options')
    inhibit='com.docker.network.bridge.inhibit_ipv4'
    enabled='com.docker.network.enable_ipv4'
    if (record.get('EnableIPv4')is not True or type(options)is not dict
        or set(options)not in ({inhibit},{inhibit,enabled})
        or type(options.get(inhibit))is not str or options[inhibit]!='true'
        or (enabled in options and (type(options[enabled])is not str or options[enabled]!='true'))):
        raise PermissionError('actual_internal_network_mismatch')
    return {'ipv4_assignment_enabled':True,'bridge_host_ipv4_inhibited':True}

def verify_network(record,key,members):
    canonical_ipv4_options(record)
    if not(record.get('Name')==NAMES[key] and record.get('Driver')=='bridge' and record.get('Internal')is True
        and record.get('EnableIPv6')is False and set(record.get('Containers',{}))==members):
        raise PermissionError('actual_internal_network_mismatch')

def validate_observed(value):
    keys={'allowlisted_mock_connect_echo','disallowed_host_denied','disallowed_port_denied','non_connect_denied','auth_header_denied','metadata_host_denied','direct_peer_tcp_denied','direct_peer_dns_no_response','external_name_not_resolved','no_ipv4_default_route','no_docker_socket','no_host_home','capabilities_empty'}
    if (type(value)is not dict or set(value)!={'role','synthetic_only','checks','routes','resolver','live_generation_allowed','biological_execution_allowed'}
        or value['role']!='client' or value['synthetic_only']is not True or value['live_generation_allowed']is not False or value['biological_execution_allowed']is not False
        or type(value['checks'])is not dict or set(value['checks'])!=keys or any(type(v)is not bool or v is not True for v in value['checks'].values())
        or type(value['routes'])is not str or len(value['routes'])>8192 or type(value['resolver'])is not str or len(value['resolver'])>8192):raise ValueError('strict_synthetic_observation_required')


def main():
    target=ROOT/'result.json'
    if target.exists() or target.is_symlink():raise FileExistsError('one_shot_egress_result_exists')
    release=json.loads((ROOT/'release.json').read_text())
    if release.get('accepted')is not True or release.get('scope')!='synthetic-owned-peer-only':raise PermissionError('independent_exact_release_required')
    verify_custody(release)
    attempt={'format':'devbench.synthetic-egress-attempt/1','release_sha256':digest(ROOT/'release.json'),**POLICY}
    with os.fdopen(os.open(ROOT/'attempt.json',os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600),'w')as stream:stream.write(json.dumps(attempt,sort_keys=True)+'\n')
    prefix=[DOCKER,'--config',str(ROOT/'docker-client'),'--host',SOCKET]
    result={'format':'devbench.synthetic-egress-diagnostic/1',**POLICY,'calls':[],'networks':{},'containers':{},'mutation_identity_uncertain':False}
    created_networks={};created_containers={};specs={}
    deadline=time.monotonic()+75
    def call(args,cleanup=False,missing=False):
        verify_custody(release)
        raw=_run_process(prefix+args,cwd=ROOT,environment=closed_environment(ROOT/'docker-client'),prompt=b'',deadline=min(time.monotonic()+12,deadline if not cleanup else time.monotonic()+12),maximum=131072,authorize=lambda:None)
        result['calls'].append({'operation':args[0],'exit_code':raw['exit_code'],'error':raw['error'],'process_reaped':raw['process_reaped'],'stdout_bytes':len(raw['stdout']),'stderr_bytes':len(raw['stderr'])})
        if missing:
            marker=b'No such container' if args[0]=='container' else b'not found'
            if raw['exit_code']!=1 or raw['error']is not None or not raw['process_reaped'] or marker not in raw['stderr'] or args[-1].encode() not in raw['stderr']:raise RuntimeError('owned_removal_not_verified')
            return raw['stdout']
        if raw['exit_code']!=0 or raw['error']is not None or not raw['process_reaped']:raise RuntimeError('bounded_docker_operation_failed')
        return raw['stdout']
    def ident(data):
        value=data.decode().strip()
        if re.fullmatch('[0-9a-f]{64}',value)is None:raise RuntimeError('fresh_owned_id_required')
        return value
    def inspect(role):
        record=json.loads(call(['container','inspect',created_containers[role]]))[0]
        nets,extra,initial=specs[role];verify_container(record,role,nets,extra,initial)
        for key,value in record['NetworkSettings']['Networks'].items():
            network_id=created_networks['inside' if key==NAMES['inside'] else 'mock']
            if value.get('NetworkID') and value['NetworkID']!=network_id:raise PermissionError('actual_endpoint_network_id_mismatch')
            if record['State'].get('Running')is True and value.get('NetworkID')!=network_id:raise PermissionError('running_endpoint_network_id_required')
        if record['Id']!=created_containers[role]:raise PermissionError('owned_id_changed')
        result['containers'][role]=record
        return record
    def create(role,key,extra):
        result['mutation_identity_uncertain']=True
        data=call(container_arguments(role,created_networks[key],extra))
        created_containers[role]=ident(data);result['mutation_identity_uncertain']=False
        specs[role]=([NAMES[key]],extra,created_networks[key])
        record=inspect(role)
        if record['State'].get('Status')!='created' or record['State'].get('Running')is not False:raise PermissionError('prestart_created_required')
        return record
    def network_check(key,members):
        record=json.loads(call(['network','inspect',created_networks[key]]))[0]
        if record['Id']!=created_networks[key]:raise PermissionError('owned_network_id_changed')
        result['networks'][key]=record  # Preserve actual owned metadata before comparison.
        verify_network(record,key,members)
    try:
        for key in ('inside','mock'):
            result['mutation_identity_uncertain']=True
            created_networks[key]=ident(call(['network','create','--driver','bridge','--internal','--ipv4=true','--ipv6=false','--opt','com.docker.network.bridge.inhibit_ipv4=true','--label','devbench.owned=gate1-egress-v3',NAMES[key]]))
            result['mutation_identity_uncertain']=False
            network_check(key,set())
        create('peer','mock',[])
        call(['start',created_containers['peer']])
        peer_ip=inspect('peer')['NetworkSettings']['Networks'][NAMES['mock']]['IPAddress']
        create('proxy','mock',[peer_ip])
        call(['network','connect',created_networks['inside'],created_containers['proxy']])
        specs['proxy']=([NAMES['mock'],NAMES['inside']],[peer_ip],created_networks['mock'])
        inspect('proxy')
        call(['start',created_containers['proxy']])
        proxy_ip=inspect('proxy')['NetworkSettings']['Networks'][NAMES['inside']]['IPAddress']
        create('client','inside',[proxy_ip,peer_ip])
        network_check('inside',{created_containers['proxy']})
        network_check('mock',{created_containers['proxy'],created_containers['peer']})
        call(['start',created_containers['client']])
        if inspect('client')['State'].get('Running')is not True:raise RuntimeError('client_not_running_for_topology')
        network_check('inside',{created_containers['proxy'],created_containers['client']})
        raw=call(['attach','--no-stdin',created_containers['client']])
        observed=json.loads(raw.decode());validate_observed(observed);result['observed']=observed
        for role in ('client','proxy','peer'):
            state=inspect(role)['State']
            if role=='client' and (state.get('Running')is not False or state.get('ExitCode')!=0):raise RuntimeError('client_exit_failed')
            if role!='client' and state.get('Running')is not True:raise RuntimeError('synthetic_server_exited_early')
        network_check('inside',{created_containers['proxy']})
        network_check('mock',{created_containers['proxy'],created_containers['peer']})
        result['synthetic_controls_passed']=True
    except Exception as error:
        result['error_category']=type(error).__name__
        result['error_code']=str(error) if str(error) in {'bounded_docker_operation_failed','negative_or_positive_control_failed','actual_internal_network_mismatch','actual_egress_enclosure_mismatch','prestart_created_required'} else 'bounded_diagnostic_failure'
    finally:
        cleanup=True
        for role,identity in reversed(list(created_containers.items())):
            try:
                call(['container','rm','--force',identity],True)
                call(['container','inspect',identity],True,True)
            except Exception:cleanup=False
        for key,identity in reversed(list(created_networks.items())):
            try:
                call(['network','rm',identity],True)
                call(['network','inspect',identity],True,True)
            except Exception:cleanup=False
        result['owned_resources_removed']=cleanup
        result['fresh_owned_container_ids']=created_containers;result['fresh_owned_network_ids']=created_networks
        try:verify_custody(release);result['source_custody_unchanged']=True
        except Exception:result['source_custody_unchanged']=False
        with os.fdopen(os.open(target,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600),'w')as stream:stream.write(json.dumps(result,indent=2)+'\n')
    print(json.dumps({k:result[k] for k in ('synthetic_controls_passed','error_category','error_code','owned_resources_removed','source_custody_unchanged')if k in result}))
    return 0 if result.get('synthetic_controls_passed')is True and result['owned_resources_removed'] and result['source_custody_unchanged'] and not result['mutation_identity_uncertain'] else 1

if __name__=='__main__':raise SystemExit(main())
