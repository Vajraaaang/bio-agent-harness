"""One fixed credential-free chatgpt.com TLS handshake; no application HTTP."""
from __future__ import annotations
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import sys
import time

W=Path('/Users/vajraang/Documents/ChatGPT/bio-agent-harness')
sys.path.insert(0,str(W))
from devbench.analysis.codex_signedin import _run_process,_leader_exited
from devbench.analysis.codex_isolated_boundary import DOCKER,IMAGE,SOCKET,closed_environment
from devharness_controlled.native_runtime import IMAGE_ENV
ROOT=W/'reports/rna-native-gate1/2026-10-21-v2/provider-transport/tls-attempt-v1'
BASE=W/'scripts/run_gate1_personal_device_auth_v2.py'
BASE_SHA='c358a916324090e6f836f0de72a59f407bc03d3038956e9f2305b87341cb908b'
NAMES={key:'devharness-gate1-model-tls-v1-'+key for key in ['inside','outside','proxy','probe']}
FALSE={'live_generation_allowed':False,'biological_execution_allowed':False,'native_admission_qualified':False}
PROXY_IP=None
base=None


def load(path,sha,name):
    import hashlib
    if path.resolve()!=path or path.is_symlink()or not path.is_file()or path.stat().st_size>100000:
        raise PermissionError('ordinary_pinned_public_helper_required')
    if hashlib.sha256(path.read_bytes()).hexdigest()!=sha:raise PermissionError('fixed_helper_changed')
    spec=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m


def command(role):
    if role not in ['proxy','probe']:raise PermissionError('fixed_model_tls_role_required')
    result=['-i','PATH=/usr/bin:/bin','HOME=/tmp','LANG=C.UTF-8','/usr/bin/timeout','--signal=KILL',
        '315s'if role=='proxy'else'25s','/usr/bin/python3','-I','-B',
        '/input/model_proxy_v2.py'if role=='proxy'else'/input/model_tls_probe.py']
    if role=='probe':
        if PROXY_IP is None:raise PermissionError('actual_inspected_proxy_ip_required')
        result.append(str(PROXY_IP))
    return result


def mounts(role):
    names=['auth_proxy.py','model_proxy_v2.py']if role=='proxy'else['model_tls_probe.py']
    return [(ROOT/'input'/name,'/input/'+name)for name in names]


def arguments(role,network):
    args=['create','--pull=never','--platform','linux/amd64','--name',NAMES[role],'--network',network,
        '--read-only','--cap-drop','ALL','--security-opt','no-new-privileges=true','--memory','134217728',
        '--memory-swap','134217728','--cpus','1','--pids-limit','16','--user','65534:65534',
        '--dns-search','.','--dns-option','timeout:1','--dns-option','attempts:1','--ipc','private',
        '--log-driver','none','--tmpfs','/tmp:rw,nosuid,nodev,noexec,size=4194304,mode=1777']
    if role=='probe':args+=['--dns','127.0.0.1']
    for source,target in mounts(role):args+=['--mount',f'type=bind,src={source},dst={target},readonly']
    return args+['--entrypoint','/usr/bin/env',IMAGE,*command(role)]


def verify_container(record,role,identity,networks,initial):
    c,h=record.get('Config',{}),record.get('HostConfig',{})
    if role=='proxy':dns_ok='Dns'in h and(h['Dns']is None or(type(h['Dns'])is list and len(h['Dns'])==0))
    else:dns_ok=h.get('Dns')==['127.0.0.1']
    expected={'NetworkMode':initial,'ReadonlyRootfs':True,'Privileged':False,'Memory':134217728,'MemorySwap':134217728,
        'NanoCpus':1000000000,'PidsLimit':16,'PidMode':'','IpcMode':'private','PublishAllPorts':False}
    valid=(record.get('Id')==identity and record.get('Name')=='/'+NAMES[role]and record.get('Image')==IMAGE
        and c.get('User')=='65534:65534'and c.get('Entrypoint')==['/usr/bin/env']and c.get('Cmd')==command(role)
        and type(c.get('Env'))is list and len(c['Env'])==len(IMAGE_ENV)and set(c['Env'])==set(IMAGE_ENV)
        and all(type(h.get(k))is type(v)and h.get(k)==v for k,v in expected.items())
        and h.get('CapDrop')==['ALL']and h.get('SecurityOpt')==['no-new-privileges=true']and dns_ok
        and h.get('DnsSearch')==['.']and h.get('DnsOptions')==['timeout:1','attempts:1']
        and h.get('LogConfig')=={'Type':'none','Config':{}}
        and h.get('Tmpfs')=={'/tmp':'rw,nosuid,nodev,noexec,size=4194304,mode=1777'}
        and all(not h.get(k)for k in ['CapAdd','Binds','Devices','DeviceRequests','PortBindings','VolumesFrom','ExtraHosts'])
        and set(record.get('NetworkSettings',{}).get('Networks',{}))==set(networks))
    actual=record.get('Mounts',[]);expected_mounts=mounts(role);valid=valid and len(actual)==len(expected_mounts)
    for source,destination in expected_mounts:
        found=[m for m in actual if m.get('Destination')==destination]
        valid=valid and len(found)==1 and found[0].get('Source')==str(source)and found[0].get('Type')=='bind'and found[0].get('RW')is False
    if not valid:raise PermissionError('actual_model_tls_container_profile_mismatch')


def proxy_template(shared,phase):
    shared.HOST='chatgpt.com';shared.AUTHORITY='chatgpt.com:443'
    record=shared.ready_record()if phase=='ready'else shared.final_record(shared.Budget(0))
    return {**record,'format':'devbench.gate1-model-proxy/2','transport_role':'model-host-only'}


def same_typed(value,expected):
    if type(value)is not type(expected):return False
    if type(expected)is dict:return set(value)==set(expected)and all(same_typed(value[k],v)for k,v in expected.items())
    if type(expected)is list:return len(value)==len(expected)and all(same_typed(v,e)for v,e in zip(value,expected))
    return value==expected


def validate_proxy(value,shared):
    ready=proxy_template(shared,'ready')
    if same_typed(value,ready):return 'ready'
    fixed=proxy_template(shared,'final');caps={'connections_seen':32,'active_connections':4,'peak_active_connections':4,
        'admitted_tunnels':32,'rejected_requests':32,'dns_denied':32,'connect_failed':32,'io_errors':32,
        'connection_byte_caps':32,'tunnel_bytes_reserved':4194304,'busy_denials':32,'internal_errors':64}
    if(type(value)is not dict or set(value)!=set(fixed)or value.get('phase')!='final'
        or any(not same_typed(value.get(k),fixed[k])for k in set(fixed)-{'counters','stop_reason'})
        or value.get('stop_reason')not in ['operator_stop','wall_deadline','connection_cap','total_byte_cap','internal_error']
        or type(value.get('counters'))is not dict or set(value['counters'])!=set(caps)
        or any(type(value['counters'][k])is not int or not 0<=value['counters'][k]<=v for k,v in caps.items())):
        raise ValueError('strict_fixed_model_proxy_receipt_required')
    return 'final'


def validate_probe(value,probe):
    if value!=probe.receipt(True):raise ValueError('strict_positive_model_tls_receipt_required')
    # Python equality aliases bools and ints; enforce receipt field types exactly.
    if type(value)is not dict or any(type(value[k])is not type(v)for k,v in probe.receipt(True).items()):
        raise ValueError('strict_positive_model_tls_receipt_types_required')
    return value


def main():
    global base,PROXY_IP
    if len(sys.argv)!=1:raise PermissionError('fixed_no_argument_model_tls_action_required')
    base=load(BASE,BASE_SHA,'fixed_model_tls_enclosure_helpers')
    base.ROOT=ROOT;base.NAMES=NAMES
    cleanup=load(W/'scripts/gate1_attachment_cleanup.py','09f0f2f1db54a8cc9e31f6326a524f476366bca502ea2254bf60f5eed0564db1','fixed_model_tls_attachment_cleanup')
    base._CLOSE_ATTACHMENT=cleanup.close_attachment
    release=json.loads((ROOT/'release.json').read_text())
    if release.get('accepted')is not True or release.get('scope')!='credential-free-chatgpt-tls-probe-only':raise PermissionError('exact_model_tls_runtime_release_required')
    if any((ROOT/name).exists()or(ROOT/name).is_symlink()for name in ['attempt.json','result.json']):raise FileExistsError('fresh_model_tls_attempt_required')
    def custody():
        for directory in [ROOT,ROOT/'input',ROOT/'docker-client']:
            info=directory.lstat()
            if directory.resolve()!=directory or not stat.S_ISDIR(info.st_mode)or info.st_uid!=501 or stat.S_IMODE(info.st_mode)!=0o700:
                raise PermissionError('ordinary_private_owned_model_tls_directory_required')
        for path,expected in release['source_sha256'].items():
            if base.digest(Path(path))!=expected:raise PermissionError('reviewed_model_tls_source_changed')
        if {p.name for p in(ROOT/'input').iterdir()}!={'auth_proxy.py','model_proxy_v2.py','model_tls_probe.py'}:
            raise PermissionError('exact_public_model_tls_input_set_required')
        for path in(ROOT/'input').iterdir():
            info=path.lstat()
            if not stat.S_ISREG(info.st_mode)or info.st_uid!=501 or stat.S_IMODE(info.st_mode)!=0o444:
                raise PermissionError('ordinary_fixed_readonly_model_tls_input_required')
        config=ROOT/'docker-client/config.json'
        info=config.lstat()
        if ({p.name for p in config.parent.iterdir()}!={'config.json'}or not stat.S_ISREG(info.st_mode)
            or info.st_uid!=501 or stat.S_IMODE(info.st_mode)!=0o600 or config.read_bytes()!=b'{"auths":{},"proxies":{}}\n'):
            raise PermissionError('closed_owned_docker_config_required')
    custody()
    for path,expected in release['prerequisite_sha256'].items():
        if base.digest(Path(path))!=expected:raise PermissionError('reviewed_model_tls_prerequisite_changed')
    guard=load(base.GATEWAY,base.GATEWAY_SHA,'fixed_model_tls_network_guard')
    shared=load(ROOT/'input/auth_proxy.py','511373e52f11e1aefe80df1d1deac375949accb2afca0a54edea24b47a4c7f89','fixed_model_tls_receipt_template')
    probe=load(ROOT/'input/model_tls_probe.py','a6dc72f236aed75f322ad4d01826b5c40b6fa91a9c858f0992e918da3214e196','fixed_model_tls_probe_schema')
    prefix=[DOCKER,'--config',str(ROOT/'docker-client'),'--host',SOCKET];environment=closed_environment(ROOT/'docker-client')
    result={'format':'devbench.model-tls-supervisor/1',**FALSE,'calls':[],'stage':'prepared','guest_states':{},
        'mutation_identity_uncertain':False,'authority':'chatgpt.com:443','credential_cache_accessed':False,
        'official_cli_invocations':0,'application_http_sent':False,'private_state_created':False}
    deadline=time.monotonic()+120;networks={};containers={};initial={};network_sets={};attachments={}
    def call(args,clean=False,missing=False):
        custody();raw=_run_process(prefix+args,cwd=ROOT,environment=environment,prompt=b'',maximum=65536,
            deadline=min(time.monotonic()+12,deadline)if not clean else time.monotonic()+12,authorize=lambda:None)
        result['calls'].append({'operation':args[0],'exit_code':raw['exit_code'],'error':raw['error'],'process_reaped':raw['process_reaped'],
            'stdout_bytes':len(raw['stdout']),'stderr_bytes':len(raw['stderr'])})
        if missing:
            marker=b'No such container'if args[0]=='container'else b'not found'
            if raw['exit_code']!=1 or raw['error']is not None or not raw['process_reaped']or marker not in raw['stderr']or args[-1].encode()not in raw['stderr']:
                raise RuntimeError('owned_removal_not_confirmed')
        elif raw['exit_code']!=0 or raw['error']is not None or not raw['process_reaped']:raise RuntimeError('bounded_model_tls_transport_failed')
        return raw['stdout']
    def identity(data):
        value=data.decode().strip()
        if re.fullmatch('[0-9a-f]{64}',value)is None:raise RuntimeError('owned_id_missing')
        return value
    def netcheck(key,members):base.verify_network(json.loads(call(['network','inspect',networks[key]]))[0],key,networks[key],members,guard)
    def inspect(role):
        value=json.loads(call(['container','inspect',containers[role]]))[0];state=value.get('State',{})
        result['guest_states'][role]={'running':state.get('Running')if type(state.get('Running'))is bool else None,
            'exit_code':state.get('ExitCode')if type(state.get('ExitCode'))is int else None,
            'status':state.get('Status')if state.get('Status')in ['created','running','exited','dead','paused','restarting']else'unclassified'}
        verify_container(value,role,containers[role],network_sets[role],initial[role])
        for name,endpoint in value['NetworkSettings']['Networks'].items():
            expected=networks['inside'if name==NAMES['inside']else'outside']
            if(endpoint.get('NetworkID')and endpoint['NetworkID']!=expected)or(state.get('Running')is True and endpoint.get('NetworkID')!=expected):
                raise PermissionError('running_model_tls_endpoint_identity_mismatch')
        return value
    def create(role,key):
        result['mutation_identity_uncertain']=True;containers[role]=identity(call(arguments(role,networks[key])));result['mutation_identity_uncertain']=False
        initial[role]=networks[key];network_sets[role]=[NAMES[key]];value=inspect(role)
        if value['State'].get('Status')!='created'or value['State'].get('Running')is not False:raise PermissionError('fresh_created_model_tls_container_required')
    def attach(role):
        custody();attachments[role]=base.Attachment(prefix+['start','--attach',containers[role]],environment,4096)
    def frames():
        for value in attachments['proxy'].pump(0):
            if validate_proxy(value,shared)=='final':result['proxy_final']=value
    try:
        with os.fdopen(os.open(ROOT/'attempt.json',os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600),'w')as f:
            f.write(json.dumps({'format':'devbench.model-tls-attempt/1',**FALSE,'authority':'chatgpt.com:443','http_application_bytes':0})+'\n');f.flush();os.fsync(f.fileno())
        for key in ['inside','outside']:
            result['stage']='create_'+key;result['mutation_identity_uncertain']=True
            networks[key]=identity(call(base.network_arguments(key)));result['mutation_identity_uncertain']=False;netcheck(key,set())
        result['stage']='create_proxy';create('proxy','outside');call(['network','connect',networks['inside'],containers['proxy']])
        network_sets['proxy']=[NAMES['inside'],NAMES['outside']];inspect('proxy')
        result['stage']='await_proxy_ready';attach('proxy');ready=False;end=time.monotonic()+15
        while not ready:
            if time.monotonic()>=end:raise TimeoutError('model_proxy_readiness_deadline')
            if not attachments['proxy'].selector.get_map()and _leader_exited(attachments['proxy'].process.pid):raise RuntimeError('model_proxy_ended_before_ready')
            for value in attachments['proxy'].pump():
                if validate_proxy(value,shared)!='ready':raise ValueError('model_proxy_ended_before_probe')
                ready=True
        record=inspect('proxy');PROXY_IP=probe.owned_proxy_ip(record['NetworkSettings']['Networks'][NAMES['inside']]['IPAddress'])
        result['stage']='create_probe';create('probe','inside');netcheck('inside',{containers['proxy']});netcheck('outside',{containers['proxy']})
        result['stage']='probe_tls';attach('probe');end=time.monotonic()+30
        while 'tls_probe'not in result:
            if time.monotonic()>=end:raise TimeoutError('model_tls_probe_deadline')
            frames()
            if 'proxy_final'in result:raise RuntimeError('model_proxy_ended_before_probe')
            if not attachments['probe'].selector.get_map()and _leader_exited(attachments['probe'].process.pid):raise RuntimeError('model_probe_ended_without_receipt')
            for value in attachments['probe'].pump():result['tls_probe']=validate_probe(value,probe)
        result['stage']='observe_probe_exit';call(['wait',containers['probe']]);state=inspect('probe')['State']
        if state.get('Running')is not False or type(state.get('ExitCode'))is not int or state['ExitCode']!=0:raise RuntimeError('model_probe_guest_exit_failed')
        if not attachments['probe'].close()or attachments['probe'].process.returncode!=0:raise RuntimeError('model_probe_transport_reap_failed')
        result['tls_only_probe_completed']=True
        result['stage']='stop_proxy';frames()
        if 'proxy_final'not in result:call(['kill','--signal','TERM',containers['proxy']])
        end=time.monotonic()+15
        while 'proxy_final'not in result:
            if time.monotonic()>=end:raise TimeoutError('model_proxy_final_deadline')
            for value in attachments['proxy'].pump():
                if validate_proxy(value,shared)=='final':result['proxy_final']=value
    except BaseException as failure:
        result['error_stage']=result['stage'];result['error_category']='Cancelled'if isinstance(failure,(KeyboardInterrupt,SystemExit))else type(failure).__name__
    finally:
        clean=True;result['attachment_exit_codes']={};result['attachment_close_observations']={}
        for role,attachment in attachments.items():
            try:
                if not attachment.close():clean=False
            except Exception:clean=False
            result['attachment_exit_codes'][role]=attachment.process.returncode
            result['attachment_close_observations'][role]=getattr(attachment,'_closure_receipt',{'closed_verified':False,'receipt_missing':True})
        for role,identifier in reversed(list(containers.items())):
            try:call(['container','rm','--force',identifier],True);call(['container','inspect',identifier],True,True)
            except Exception:clean=False
        for key,identifier in reversed(list(networks.items())):
            try:call(['network','rm',identifier],True);call(['network','inspect',identifier],True,True)
            except Exception:clean=False
        result['owned_runtime_resources_removed']=clean
        try:custody();result['source_custody_unchanged']=True
        except Exception:result['source_custody_unchanged']=False
        with os.fdopen(os.open(ROOT/'result.json',os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600),'w')as f:f.write(json.dumps(result,indent=2)+'\n')
    passed=(result.get('tls_only_probe_completed')is True and result['owned_runtime_resources_removed']and result['source_custody_unchanged']
        and not result.get('error_category')and not result['mutation_identity_uncertain']and all(v==0 for v in result['attachment_exit_codes'].values())
        and result.get('proxy_final',{}).get('counters',{}).get('internal_errors')==0)
    print(json.dumps({'credential_free_model_tls_probe_passed':passed,**FALSE}),flush=True)
    return 0 if passed else 1


if __name__=='__main__':raise SystemExit(main())
