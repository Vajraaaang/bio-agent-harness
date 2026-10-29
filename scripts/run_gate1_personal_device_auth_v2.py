"""Fixed reviewed personal device login through an auth-only relay.

Challenge codes stay in a private local panel and never enter retained output.
Private credential state is never traversed, read, hashed, copied or indexed.
No model command, signer or generation/execution grant is supported here.
"""
from __future__ import annotations
import hashlib
from http.server import BaseHTTPRequestHandler,HTTPServer
import html
import importlib.util
import ipaddress
import json
import os
from pathlib import Path
import re
import selectors
import secrets
import signal
import stat
import subprocess
import sys
import time
from threading import Lock,Thread,Timer

WORKSPACE=Path('/Users/vajraang/Documents/ChatGPT/bio-agent-harness')
sys.path.insert(0,str(WORKSPACE))
from devbench.analysis.codex_isolated_boundary import DOCKER,IMAGE,SOCKET,closed_environment
from devbench.analysis.codex_signedin import _run_process,_leader_exited
from devharness_controlled.native_runtime import IMAGE_ENV
ROOT=WORKSPACE/'reports/rna-native-gate1/2026-10-21-v2/auth-preparation/login-attempt-v2'
CLIENT=ROOT.parent.parent/'client/package'
MANIFEST=CLIENT.parent/'package-manifest.json'
PRIVATE=WORKSPACE/'.devbench-codex-auth/epoch1'
MODE='device-login'
TLS_ROOT=ROOT.parent/'tls-attempt-v3'
NAMES={key:'devharness-gate1-auth-v2-'+key for key in ['inside','outside','proxy','probe','client']}
GATEWAY=WORKSPACE/'scripts/run_gate1_egress_diagnostic_v3.py'
GATEWAY_SHA='b8ae2677d114eed3dd97dc71b4ad99115cc12aa434fa91c07034ea199316c532'
FALSE_FLAGS={'live_generation_allowed':False,'biological_execution_allowed':False,'native_admission_qualified':False}
_CLOSE_ATTACHMENT=None
OUTSIDE_OPTIONS={'com.docker.network.bridge.enable_ip_masquerade':'true','com.docker.network.bridge.enable_icc':'false'}


def digest(path,maximum=300000000):
    path=Path(path)
    resolved=path.resolve()
    private=WORKSPACE/'.devbench-codex-auth'
    allowed=(resolved==Path(DOCKER)or resolved==WORKSPACE/'.gitignore'or any(resolved.is_relative_to(WORKSPACE/name)for name in ['scripts','devbench','devharness_controlled','reports/rna-native-gate1/2026-10-21-v2']))
    if not allowed or resolved.is_relative_to(private)or path.name in ['auth.json','codex-login.log']or 'codex-home'in path.parts:
        raise PermissionError('credential_or_unapproved_source_digest_forbidden')
    if path.is_symlink() or not path.is_file() or path.stat().st_size>maximum:raise PermissionError('ordinary_bounded_public_file_required')
    h=hashlib.sha256()
    with path.open('rb')as stream:
        for part in iter(lambda:stream.read(1048576),b''):h.update(part)
    return h.hexdigest()


def load_pinned(path,expected,name):
    if digest(path)!=expected:raise PermissionError('reviewed_public_helper_changed')
    spec=importlib.util.spec_from_file_location(name,path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def fresh_private_state(policy):
    """Initialize only a fresh fixed epoch; never inspect an old cache."""
    ignore=(WORKSPACE/'.gitignore').read_text().splitlines()
    if '.devbench-codex-auth/'not in ignore or any(x.startswith('!.devbench-codex-auth')for x in ignore):
        raise PermissionError('private_root_ignore_rule_missing')
    if PRIVATE.exists() or PRIVATE.is_symlink():raise FileExistsError('fresh_private_auth_epoch_required')
    parent=PRIVATE.parent
    if not parent.exists():parent.mkdir(mode=0o700)
    info=parent.lstat()
    if parent.resolve()!=parent or not stat.S_ISDIR(info.st_mode)or info.st_uid!=501 or stat.S_IMODE(info.st_mode)!=0o700:
        raise PermissionError('private_parent_owner_or_mode_mismatch')
    PRIVATE.mkdir(mode=0o700)
    for name in ['home','codex-home','workspace']:(PRIVATE/name).mkdir(mode=0o700)
    for name,data in [('AGENTS.override.md',policy),('config.toml',b'# Owned official personal login only.\n')]:
        path=PRIVATE/'codex-home'/name
        with os.fdopen(os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o400),'wb')as stream:stream.write(data)


def network_arguments(key):
    base=['network','create','--driver','bridge','--ipv4=true','--ipv6=false']
    if key=='inside':return base+['--internal','--opt','com.docker.network.bridge.inhibit_ipv4=true',NAMES[key]]
    if key=='outside':
        for k,v in OUTSIDE_OPTIONS.items():base+=['--opt',k+'='+v]
        return base+[NAMES[key]]
    raise PermissionError('fixed_auth_network_role_required')


def verify_network(record,key,identity,members,guard):
    valid=(record.get('Id')==identity and record.get('Name')==NAMES[key]and record.get('Driver')=='bridge'
        and record.get('EnableIPv4')is True and record.get('EnableIPv6')is False
        and record.get('Internal')is (key=='inside')and set(record.get('Containers',{}))==set(members))
    options=record.get('Options',{})
    if key=='inside':valid=valid and guard.canonical_ipv4_options(record)
    else:valid=valid and options in [OUTSIDE_OPTIONS,{**OUTSIDE_OPTIONS,'com.docker.network.enable_ipv4':'true'}]
    if not valid:raise PermissionError('actual_auth_topology_mismatch')


def container_arguments(role,network):
    proxy=role=='proxy'
    if role not in ['proxy','probe','client']:raise PermissionError('fixed_auth_container_role_required')
    args=['create','--pull=never','--platform','linux/amd64','--name',NAMES[role],'--network',network,'--read-only',
        '--cap-drop','ALL','--security-opt','no-new-privileges=true','--memory',str(134217728 if role!='client'else 1073741824),
        '--memory-swap',str(134217728 if role!='client'else 1073741824),'--cpus','1','--pids-limit',str(16 if role!='client'else 64),
        '--user','65534:65534'if proxy else '501:20','--dns-search','.','--dns-option','timeout:1','--dns-option','attempts:1',
        '--ipc','private','--log-driver','none','--tmpfs','/tmp:rw,nosuid,nodev,noexec,size='+('4194304'if proxy else '16777216')+',mode=1777']
    if proxy:args+=['--mount',f'type=bind,src={ROOT / "input/auth_proxy.py"},dst=/input/auth_proxy.py,readonly']
    else:
        args+=['--dns','127.0.0.1']
        for source,destination,readonly in [(ROOT/'input','/input',True),(CLIENT,'/client',True),*([(PRIVATE,'/private',False)]if role=='client'else [])]:
            args+=['--mount',f'type=bind,src={source},dst={destination}'+(',readonly'if readonly else '')]
    command=['-i','PATH=/usr/bin:/bin','HOME=/tmp','LANG=C.UTF-8']
    if not proxy:command+=['DEVBENCH_AUTH_ONLY=1']
    command+=['/usr/bin/timeout','--signal=KILL','45s'if role=='probe'else '315s','/usr/bin/python3','-I','-B','/input/auth_proxy.py'if proxy else '/input/login.py']
    if not proxy:command+=['tls-only'if role=='probe'else 'device-login']
    return args+['--entrypoint','/usr/bin/env',IMAGE,*command]


def verify_container(record,role,identity,networks,initial):
    if role=='proxy':
        host=record.get('HostConfig',{})
        if 'Dns'not in host or(host['Dns']is not None and not(type(host['Dns'])is list and len(host['Dns'])==0)):
            raise PermissionError('explicit_unset_proxy_dns_required')
        record=dict(record);record['HostConfig']=dict(host);record['HostConfig']['Dns']=[]
    proxy=role=='proxy';c,h=record.get('Config',{}),record.get('HostConfig',{})
    args=container_arguments(role,initial);command=args[args.index(IMAGE)+1:]
    expected={'NetworkMode':initial,'ReadonlyRootfs':True,'Privileged':False,'Memory':134217728 if role!='client'else 1073741824,
        'MemorySwap':134217728 if role!='client'else 1073741824,'NanoCpus':1000000000,'PidsLimit':16 if role!='client'else 64,
        'PidMode':'','IpcMode':'private','PublishAllPorts':False}
    valid=(record.get('Id')==identity and record.get('Name')=='/'+NAMES[role]and record.get('Image')==IMAGE
        and c.get('User')==('65534:65534'if proxy else '501:20')and c.get('Entrypoint')==['/usr/bin/env']and c.get('Cmd')==command
        and type(c.get('Env'))is list and len(c['Env'])==len(IMAGE_ENV)and set(c['Env'])==set(IMAGE_ENV)
        and all(type(h.get(k))is type(v)and h.get(k)==v for k,v in expected.items())
        and h.get('CapDrop')==['ALL']and h.get('SecurityOpt')==['no-new-privileges=true']
        and h.get('DnsSearch')==['.']and h.get('DnsOptions')==['timeout:1','attempts:1']
        and h.get('Dns')==([]if proxy else ['127.0.0.1'])and h.get('LogConfig')=={'Type':'none','Config':{}}
        and h.get('Tmpfs')=={'/tmp':'rw,nosuid,nodev,noexec,size='+('4194304'if proxy else '16777216')+',mode=1777'}
        and all(not h.get(k)for k in ['CapAdd','Binds','Devices','DeviceRequests','PortBindings','VolumesFrom','ExtraHosts'])
        and set(record.get('NetworkSettings',{}).get('Networks',{}))==set(networks))
    expected_mounts=[(ROOT/'input/auth_proxy.py','/input/auth_proxy.py',False)]if proxy else [(ROOT/'input','/input',False),(CLIENT,'/client',False),*([(PRIVATE,'/private',True)]if role=='client'else [])]
    mounts=record.get('Mounts',[]);valid=valid and len(mounts)==len(expected_mounts)
    for source,destination,rw in expected_mounts:
        found=[m for m in mounts if m.get('Destination')==destination]
        valid=valid and len(found)==1 and found[0].get('Source')==str(source)and found[0].get('Type')=='bind'and found[0].get('RW')is rw
    if not valid:raise PermissionError('actual_auth_container_profile_mismatch')


class PrivateChallenge:
    """Memory-only user surface; no challenge enters stdout, disk or hashes."""
    def __init__(self,code,ttl=300):
        if type(code)is not str or re.fullmatch('[A-Z0-9-]{4,32}',code)is None or not any(c.isalnum()for c in code):raise ValueError('bounded_device_challenge_required')
        if type(ttl)is not int or not 1<=ttl<=300:raise ValueError('bounded_challenge_ttl_required')
        self.lock=Lock();self.code=code;self.closed=False;self.deadline=time.monotonic()+ttl
        self.path='/'+secrets.token_urlsafe(32)
        owner=self
        class Handler(BaseHTTPRequestHandler):
            protocol_version='HTTP/1.1'
            def log_message(self,*_args):pass
            def setup(self):super().setup();self.connection.settimeout(1)
            def do_GET(self):
                host='127.0.0.1:'+str(owner.server.server_address[1])
                if self.headers.get_all('Host')!=[host] or self.path!=owner.path:
                    self.send_response(404);self.send_header('Content-Length','0');self.send_header('Connection','close');self.end_headers();return
                with owner.lock:
                    available=not owner.closed and time.monotonic()<owner.deadline and owner.code is not None
                    code=owner.code if available else None
                if code is None:body=b'Login panel expired.';status=410
                else:
                    body=('<!doctype html><html><meta charset="utf-8"><title>Official device login</title><body>'
                        '<h1>Complete your personal Codex login</h1><p>Open the official page, sign in, and enter this one-time code:</p><p><strong>'
                        +html.escape(code)+' </strong></p><p><a href="https://auth.openai.com/codex/device" target="_blank" rel="noopener noreferrer">Open official sign-in page</a></p>'
                        '<p>This local panel expires within five minutes. Continue only if you started this login.</p></body></html>').encode();status=200
                self.send_response(status)
                for key,value in [('Content-Type','text/html; charset=utf-8'),('Cache-Control','no-store, max-age=0'),('Pragma','no-cache'),
                    ('Referrer-Policy','no-referrer'),('X-Content-Type-Options','nosniff'),('X-Frame-Options','DENY'),
                    ('Content-Security-Policy',"default-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"),
                    ('Content-Length',str(len(body))),('Connection','close')]:self.send_header(key,value)
                self.end_headers();self.wfile.write(body)
        class SilentServer(HTTPServer):
            def handle_error(self,*_args):pass
        self.server=SilentServer(('127.0.0.1',0),Handler)
        self.thread=Thread(target=lambda:self.server.serve_forever(poll_interval=.05),daemon=True);self.thread.start()
        self.timer=Timer(ttl,self.close);self.timer.daemon=True;self.timer.start()
    @property
    def url(self):return 'http://127.0.0.1:'+str(self.server.server_address[1])+self.path
    def close(self):
        with self.lock:
            if self.closed:return
            self.closed=True;self.code=None
        self.timer.cancel();self.server.shutdown();self.server.server_close();self.thread.join(timeout=2)


class Attachment:
    """Bounded JSON metadata transport; challenge frames never enter journals."""
    def __init__(self,command,environment,maximum):
        self.process=subprocess.Popen(command,cwd=str(ROOT),env=environment,stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,stderr=subprocess.PIPE,start_new_session=True,close_fds=True)
        self.selector=selectors.DefaultSelector();self.buffer=bytearray();self.total=0;self.maximum=maximum
        for stream,label in [(self.process.stdout,'stdout'),(self.process.stderr,'stderr')]:
            os.set_blocking(stream.fileno(),False);self.selector.register(stream,selectors.EVENT_READ,label)
    def pump(self,seconds=.1):
        records=[]
        for key,_ in self.selector.select(seconds):
            data=os.read(key.fd,4096)
            if not data:self.selector.unregister(key.fileobj);continue
            self.total+=len(data)
            if self.total>self.maximum:raise ValueError('public_metadata_stream_bound')
            if key.data=='stderr':continue
            self.buffer.extend(data)
            if len(self.buffer)>8192:raise ValueError('public_metadata_line_bound')
            while b'\n'in self.buffer:
                line,_,rest=self.buffer.partition(b'\n');self.buffer=bytearray(rest)
                if line:records.append(json.loads(line))
        return records
    def close(self):
        if _CLOSE_ATTACHMENT is None:raise PermissionError('reviewed_attachment_cleanup_not_bound')
        return _CLOSE_ATTACHMENT(self)



def validate_login_result(frame):
    fixed_false={'raw_auth_stream_retained','raw_auth_stream_hash_retained','authentication_cache_contents_read_by_worker',
        'authentication_cache_hashed_by_worker',*FALSE_FLAGS}
    bools={'official_cli_launch_confirmed','process_reaped','process_group_cleanup_uncertain','device_prompt_presented',
        'official_login_completed','official_status_verified_chatgpt','official_status_process_reaped','official_status_process_group_cleanup_uncertain'}
    ints={'official_cli_invocations_attempted':1,'combined_auth_stream_bytes':32768,
        'official_status_invocations_attempted':1,'official_status_stream_bytes':8192}
    errors={'error_category','official_status_error_category'}
    expected={'event','format','official_cli_exit_code','official_status_exit_code'}|fixed_false|bools|set(ints)|errors
    if type(frame)is not dict or set(frame)!=expected or frame['event']!='login_result'or frame['format']!='devbench.personal-device-login-result/1':raise ValueError('exact_public_auth_result_schema_required')
    if any(frame[k]is not False for k in fixed_false)or any(type(frame[k])is not bool for k in bools):raise ValueError('fixed_auth_result_boolean_required')
    if any(type(frame[k])is not int or not 0<=frame[k]<=maximum for k,maximum in ints.items()):raise ValueError('bounded_auth_result_count_required')
    if frame['official_cli_invocations_attempted']!=1 or frame['official_cli_launch_confirmed']is not True:raise ValueError('one_official_login_attempt_required')
    for key in ['official_cli_exit_code','official_status_exit_code']:
        if frame[key]is not None and(type(frame[key])is not int or not -255<=frame[key]<=255):raise ValueError('fixed_auth_exit_code_required')
    for key in errors:
        if frame[key]not in [None,'Cancelled','OSError','TimeoutError','ValueError','PermissionError','ProcessLookupError','FileNotFoundError']:raise ValueError('fixed_auth_error_category_required')
    if frame['official_login_completed']and not(frame['official_cli_exit_code']==0 and frame['process_reaped']and not frame['process_group_cleanup_uncertain']and frame['error_category']is None and frame['device_prompt_presented']and frame['official_status_invocations_attempted']==1 and frame['official_status_verified_chatgpt']and frame['official_status_exit_code']==0 and frame['official_status_process_reaped']and not frame['official_status_process_group_cleanup_uncertain']and frame['official_status_error_category']is None):raise ValueError('consistent_official_auth_completion_required')
    return frame


def verified_tls_fact(proof):
    value=proof.get('tls_probe',{})
    return (type(value)is dict and set(value)=={'event','hostname_verified','certificate_chain_verified',
        'http_application_bytes_sent','tls_version','actual_cli_tls_validation_is_separate'}
        and value['event']=='tls_positive'and value['hostname_verified']is True
        and value['certificate_chain_verified']is True and type(value['http_application_bytes_sent'])is int
        and value['http_application_bytes_sent']==0 and value['tls_version']in ['TLSv1.2','TLSv1.3']
        and value['actual_cli_tls_validation_is_separate']is True)


def main():
    global _CLOSE_ATTACHMENT
    if MODE not in ['tls-only','device-login']:raise PermissionError('fixed_auth_action_required')
    target=ROOT/'result.json'
    if target.exists()or target.is_symlink()or(MODE=='device-login'and(PRIVATE.exists()or PRIVATE.is_symlink())):raise FileExistsError('fresh_auth_attempt_and_private_epoch_required')
    release=json.loads((ROOT/'release.json').read_text())
    expected_scope='official-personal-device-auth-tls-probe-only'if MODE=='tls-only'else'official-personal-device-auth-login-only'
    if release.get('accepted')is not True or release.get('scope')!=expected_scope:raise PermissionError('exact_auth_runtime_release_required')
    for path,expected in release['prerequisite_sha256'].items():
        if digest(Path(path))!=expected:raise PermissionError('reviewed_auth_prerequisite_changed')
    negative=json.loads((ROOT.parent/'proxy-compat-v3/output/protocol/result.json').read_text())
    if negative.get('passed_proxy_selection_negative')is not True:raise PermissionError('actual_official_proxy_compatibility_required')
    if MODE=='device-login':
        proof=json.loads((TLS_ROOT/'result.json').read_text())
        acceptance=json.loads((ROOT.parent.parent/'reviews/auth-tls-postrun-v3.json').read_text())
        if acceptance.get('accepted_tls_observation')is not True or not verified_tls_fact(proof) or proof.get('owned_runtime_resources_removed')is not True or proof.get('source_custody_unchanged')is not True:
            raise PermissionError('independently_accepted_actual_tls_proof_required')
    guard=load_pinned(GATEWAY,GATEWAY_SHA,'fixed_qualified_network_guard')
    preparation=load_pinned(ROOT/'input/preparation.py','e160a6bd22035ba7ad7d2ea3c0864994c2053dd21dcb83aa08f08698a6e57cf6','fixed_auth_preparation')
    proxy_module=load_pinned(ROOT/'input/auth_proxy.py','511373e52f11e1aefe80df1d1deac375949accb2afca0a54edea24b47a4c7f89','fixed_auth_relay')
    session_written=False;session_digest=None
    def custody():
        for directory in [ROOT,ROOT/'input',ROOT/'docker-client',CLIENT]:
            if directory.resolve()!=directory or not directory.is_dir():raise PermissionError('ordinary_fixed_auth_directory_required')
        for path,expected in release['source_sha256'].items():
            if digest(Path(path))!=expected:raise PermissionError('reviewed_auth_source_changed')
        expected={'login.py','preparation.py','auth_proxy.py'}|({'session.json'}if session_written else set())
        if session_written and digest(ROOT/'input/session.json')!=session_digest:raise PermissionError('public_auth_session_binding_changed')
        if {p.name for p in(ROOT/'input').iterdir()}!=expected:raise PermissionError('exact_public_auth_input_set_required')
        if {p.name for p in(ROOT/'docker-client').iterdir()}!={'config.json'}or(ROOT/'docker-client/config.json').is_symlink():raise PermissionError('only_ordinary_closed_docker_config_required')
        if(ROOT/'docker-client/config.json').read_bytes()!=b'{"auths":{},"proxies":{}}\n':raise PermissionError('empty_owned_docker_config_required')
    custody()
    cleanup=load_pinned(WORKSPACE/'scripts/gate1_attachment_cleanup.py','09f0f2f1db54a8cc9e31f6326a524f476366bca502ea2254bf60f5eed0564db1','fixed_owned_attachment_cleanup')
    _CLOSE_ATTACHMENT=cleanup.close_attachment
    # Full public package verification only; private state is excluded by construction.
    manifest=json.loads(MANIFEST.read_text())
    if {str(p.relative_to(CLIENT))for p in CLIENT.rglob('*')}!={x['name']for x in manifest['files']}:raise PermissionError('official_package_inventory_changed')
    for entry in manifest['files']:
        p=CLIENT/entry['name']
        if p.is_symlink()or(entry['type']=='file'and(p.stat().st_size!=entry['bytes']or digest(p)!=entry['sha256'])):raise PermissionError('official_package_bytes_changed')
    prefix=[DOCKER,'--config',str(ROOT/'docker-client'),'--host',SOCKET];environment=closed_environment(ROOT/'docker-client')
    result={'format':'devbench.personal-device-auth-supervisor/1',**FALSE_FLAGS,'calls':[],'guest_states':{},'attachment_close_observations':{},'stage':'prepared',
        'action':MODE,'private_root':str(PRIVATE)if MODE=='device-login'else None,'private_state_excluded_from_all_indexes':True,'private_cache_read_or_hashed':False,
        'live_model_calls':0,'challenge_retained_or_hashed':False,'mutation_identity_uncertain':False}
    networks={};containers={};attachments={};challenge_ui=None;initial={};network_sets={};deadline=time.monotonic()+420
    def call(args,cleanup=False,missing=False):
        custody()
        raw=_run_process(prefix+args,cwd=ROOT,environment=environment,prompt=b'',
            deadline=min(time.monotonic()+12,deadline)if not cleanup else time.monotonic()+12,maximum=65536,authorize=lambda:None)
        result['calls'].append({'operation':args[0],'exit_code':raw['exit_code'],'error':raw['error'],
            'process_reaped':raw['process_reaped'],'stdout_bytes':len(raw['stdout']),'stderr_bytes':len(raw['stderr'])})
        if missing:
            marker=b'No such container'if args[0]=='container'else b'not found'
            if raw['exit_code']!=1 or raw['error']is not None or not raw['process_reaped']or marker not in raw['stderr']or args[-1].encode()not in raw['stderr']:raise RuntimeError('owned_removal_not_confirmed')
        elif raw['exit_code']!=0 or raw['error']is not None or not raw['process_reaped']:raise RuntimeError('bounded_auth_transport_failed')
        return raw['stdout']
    def identity(data):
        value=data.decode().strip()
        if re.fullmatch('[0-9a-f]{64}',value)is None:raise RuntimeError('owned_id_missing')
        return value
    def netcheck(key,members):
        value=json.loads(call(['network','inspect',networks[key]]))[0];verify_network(value,key,networks[key],members,guard)
    def inspect(role):
        value=json.loads(call(['container','inspect',containers[role]]))[0]
        state=value.get('State',{})
        result['guest_states'][role]={'running':state.get('Running')if type(state.get('Running'))is bool else None,
            'exit_code':state.get('ExitCode')if type(state.get('ExitCode'))is int else None,
            'status':state.get('Status')if state.get('Status')in ['created','running','exited','dead','restarting','paused']else'unclassified'}
        verify_container(value,role,containers[role],network_sets[role],initial[role])
        for key,endpoint in value['NetworkSettings']['Networks'].items():
            expected=networks['inside'if key==NAMES['inside']else'outside']
            if (endpoint.get('NetworkID')and endpoint.get('NetworkID')!=expected)or(value['State'].get('Running')is True and endpoint.get('NetworkID')!=expected):raise PermissionError('running_auth_endpoint_identity_mismatch')
        return value
    def create(role,key):
        result['mutation_identity_uncertain']=True
        containers[role]=identity(call(container_arguments(role,networks[key])));result['mutation_identity_uncertain']=False
        initial[role]=networks[key];network_sets[role]=[NAMES[key]]
        value=inspect(role)
        if value['State'].get('Status')!='created'or value['State'].get('Running')is not False:raise PermissionError('fresh_created_auth_container_required')
    def attach(role,maximum):
        custody();attachments[role]=Attachment(prefix+['start','--attach',containers[role]],environment,maximum)
    def proxy_frame(value):
        if value==proxy_module.ready_record():return 'ready'
        template=proxy_module.final_record(proxy_module.Budget(0))
        if(type(value)is not dict or set(value)!=set(template)or value.get('phase')!='final'
            or value.get('format')!=template['format']or value.get('authority')!=template['authority']
            or value.get('live_generation_allowed')is not False or value.get('biological_execution_allowed')is not False
            or value.get('stop_reason')not in ['operator_stop','wall_deadline','connection_cap','total_byte_cap','internal_error']
            or type(value.get('counters'))is not dict or set(value['counters'])!=set(template['counters'])
            or any(type(v)is not int or not 0<=v<=4194304 for v in value['counters'].values())):raise ValueError('fixed_public_proxy_metadata_required')
        caps={'connections_seen':32,'active_connections':4,'peak_active_connections':4,'admitted_tunnels':32,
            'rejected_requests':32,'dns_denied':32,'connect_failed':32,'io_errors':32,'connection_byte_caps':32,
            'tunnel_bytes_reserved':4194304,'busy_denials':32,'internal_errors':64}
        if any(value['counters'][k]>maximum for k,maximum in caps.items()):raise ValueError('auth_proxy_runtime_counter_cap_exceeded')
        result['proxy_final']=value;return 'final'
    try:
        with os.fdopen(os.open(ROOT/'attempt.json',os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600),'w')as stream:
            stream.write(json.dumps({'format':'devbench.auth-only-launch-intent/1',**FALSE_FLAGS,'action':MODE,'private_epoch':str(PRIVATE)if MODE=='device-login'else None,'official_cli':'login --device-auth + redacted login status'if MODE=='device-login'else'not_invoked_tls_only','human_window_max_seconds':300})+'\n')
        for key in ['inside','outside']:
            result['stage']='create_'+key+'_network'
            result['mutation_identity_uncertain']=True;networks[key]=identity(call(network_arguments(key)));result['mutation_identity_uncertain']=False;netcheck(key,set())
        result['stage']='create_proxy'
        create('proxy','outside');call(['network','connect',networks['inside'],containers['proxy']]);network_sets['proxy']=[NAMES['inside'],NAMES['outside']];inspect('proxy')
        result['stage']='start_proxy_and_wait_ready'
        attach('proxy',4096);ready=False;ready_deadline=time.monotonic()+15
        while not ready:
            if time.monotonic()>=ready_deadline:raise TimeoutError('auth_proxy_readiness_deadline')
            if not attachments['proxy'].selector.get_map()and _leader_exited(attachments['proxy'].process.pid):raise RuntimeError('auth_proxy_ended_before_ready')
            for value in attachments['proxy'].pump():
                if proxy_frame(value)!='ready':raise ValueError('proxy_failed_before_login')
                ready=True
        proxy_record=inspect('proxy');proxy_ip=proxy_record['NetworkSettings']['Networks'][NAMES['inside']]['IPAddress']
        address=ipaddress.IPv4Address(proxy_ip)
        if not address.is_private:raise PermissionError('inspected_internal_proxy_ip_required')
        session=ROOT/'input/session.json'
        with os.fdopen(os.open(session,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o444),'w')as stream:stream.write(json.dumps({'proxy_ip':str(address)})+'\n')
        session_digest=digest(session);session_written=True
        result['public_session_binding_sha256']=session_digest
        if MODE=='tls-only':
            create('probe','inside');netcheck('inside',{containers['proxy']});netcheck('outside',{containers['proxy']})
            attach('probe',4096);probe_deadline=time.monotonic()+35
            while 'tls_probe'not in result:
                if time.monotonic()>=probe_deadline:raise TimeoutError('tls_only_probe_deadline')
                for value in attachments['proxy'].pump(0):
                    if proxy_frame(value)=='final':raise RuntimeError('auth_relay_ended_before_tls_probe')
                if not attachments['probe'].selector.get_map()and _leader_exited(attachments['probe'].process.pid):raise RuntimeError('tls_probe_ended_without_verified_metadata')
                for frame in attachments['probe'].pump():
                    if(type(frame)is not dict or set(frame)!={'event','hostname_verified','certificate_chain_verified','http_application_bytes_sent','tls_version','actual_cli_tls_validation_is_separate'}or frame.get('event')!='tls_positive'or frame.get('hostname_verified')is not True or frame.get('certificate_chain_verified')is not True or type(frame.get('http_application_bytes_sent'))is not int or frame['http_application_bytes_sent']!=0 or frame.get('tls_version')not in ['TLSv1.2','TLSv1.3']or frame.get('actual_cli_tls_validation_is_separate')is not True):raise ValueError('strict_tls_only_positive_required')
                    result['tls_probe']=frame
            call(['wait',containers['probe']]);state=inspect('probe')['State']
            if state.get('Running')is not False or state.get('ExitCode')!=0:raise RuntimeError('tls_probe_guest_exit_failed')
            if not attachments['probe'].close()or attachments['probe'].process.returncode!=0:raise RuntimeError('tls_probe_transport_reap_failed')
            result['tls_probe_transport_exit_code']=attachments['probe'].process.returncode
            del attachments['probe']
            result['tls_only_probe_completed']=True
        else:
            result['stage']='initialize_private_epoch'
            result['private_epoch_initialization_attempted']=True
            fresh_private_state(preparation.PUBLIC_POLICY);result['private_epoch_created']=True
            create('client','inside');netcheck('inside',{containers['proxy']});netcheck('outside',{containers['proxy']})
            result['stage']='login_and_official_status'
            attach('client',16384);client_result=False;client_deadline=time.monotonic()+320
            while not client_result:
                if time.monotonic()>=client_deadline:raise TimeoutError('auth_client_deadline')
                for frame in attachments['proxy'].pump(0):
                    proxy_frame(frame)
                if not attachments['client'].selector.get_map()and _leader_exited(attachments['client'].process.pid):raise RuntimeError('auth_client_ended_without_filtered_result')
                for frame in attachments['client'].pump():
                    if type(frame)is not dict or type(frame.get('event'))is not str:raise ValueError('filtered_auth_event_required')
                    event=frame['event']
                    if event=='device_authorization':
                        if set(frame)!={'event','verification_url','user_code'}or frame['verification_url']!='https://auth.openai.com/codex/device'or re.fullmatch('[A-Z0-9-]{4,32}',frame['user_code'])is None:raise ValueError('fixed_user_only_device_challenge_required')
                        if MODE!='device-login':raise ValueError('auth_action_required_before_challenge')
                        if result.get('device_challenge_presented'):raise ValueError('one_device_challenge_only')
                        challenge_ui=PrivateChallenge(frame['user_code'],ttl=300)
                        print('Your one-time code is visible only in this private local panel: '+challenge_ui.url,flush=True)
                        print('Complete the official device sign-in shown there. The agent must not read or screenshot this panel.',flush=True)
                        result['device_challenge_presented']=True
                    elif event=='waiting_for_user':
                        if not result.get('device_challenge_presented'):raise ValueError('challenge_required_before_user_wait')
                        if set(frame)!={'event','remaining_seconds'}or type(frame['remaining_seconds'])is not int or not 0<=frame['remaining_seconds']<=300:raise ValueError('bounded_login_heartbeat_required')
                        print('Waiting for your official device login; '+str(frame['remaining_seconds'])+' seconds remain.',flush=True)
                    elif event=='login_result':
                        result['login_result']=validate_login_result(frame);client_result=True
                    elif event=='auth_setup_failed':
                        result['client_setup_failed_category']=frame.get('error_category')if frame.get('error_category')in ['OSError','TimeoutError','SSLError','PermissionError','ValueError']else'bounded_tls_probe_failure';client_result=True
                    else:raise ValueError('unrecognized_auth_event')
            result['stage']='observe_auth_guest_exit'
            client_state=inspect('client')['State']
            if client_state.get('Running')is True:call(['wait',containers['client']]);client_state=inspect('client')['State']
            result['client_stopped']=client_state.get('Running')is False
            result['client_container_exit_code']=client_state.get('ExitCode')
        result['stage']='stop_relay_and_collect_final'
        if 'proxy_final'not in result:call(['kill','--signal','TERM',containers['proxy']])
        end=time.monotonic()+15
        while 'proxy_final'not in result:
            if time.monotonic()>=end:raise TimeoutError('auth_proxy_final_deadline')
            for value in attachments['proxy'].pump():proxy_frame(value)
        result['official_login_completed']=MODE=='device-login'and result.get('login_result',{}).get('official_login_completed')is True
    except BaseException as failure:
        result['error_stage']=result.get('stage','unclassified')
        safe={'tls_probe_guest_exit_failed','tls_probe_transport_reap_failed','auth_client_ended_without_filtered_result','bounded_auth_transport_failed','actual_auth_container_profile_mismatch'}
        result['error_code']=str(failure)if str(failure)in safe else'bounded_auth_supervisor_failure'
        result['error_category']='Cancelled'if isinstance(failure,(KeyboardInterrupt,SystemExit))else type(failure).__name__
    finally:
        clean=True
        if challenge_ui is not None:
            try:challenge_ui.close()
            except Exception:clean=False
        result['attachment_exit_codes']={}
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
        result['private_epoch_preserved_without_inspection']=result.get('private_epoch_created',False)
        with os.fdopen(os.open(target,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600),'w')as stream:stream.write(json.dumps(result,indent=2)+'\n')
    completed=result.get('tls_only_probe_completed')is True if MODE=='tls-only'else result.get('official_login_completed')is True
    print(json.dumps({'action':MODE,'action_completed':completed,'official_login_completed':result.get('official_login_completed',False),'runtime_cleanup_verified':result['owned_runtime_resources_removed'],'source_custody_unchanged':result['source_custody_unchanged'],**FALSE_FLAGS}),flush=True)
    return 0 if completed and result['owned_runtime_resources_removed']and result['source_custody_unchanged']and not result.get('error_category')and not result['mutation_identity_uncertain']and all(value==0 for value in result['attachment_exit_codes'].values())and result.get('proxy_final',{}).get('counters',{}).get('internal_errors')==0 else 1


if __name__=='__main__':raise SystemExit(main())
