"""One official personal device login; filtered challenge is user-display only.

Run only inside the independently released auth-only enclosure. The supervisor
must discard challenge events from journals, captures, hashes and telemetry.
This worker never reads or hashes an authentication cache or starts a model.
"""
from __future__ import annotations
import hashlib
import importlib.util
import ipaddress
import json
import os
import re
from pathlib import Path
import selectors
import signal
import socket
import ssl
import subprocess
import sys
import time

CLIENT=Path('/client/bin/codex')
CLIENT_SHA='cb0a15567e9a60a5820d54b0f6ae86d504dc3805c1eab21a47f70e3eb7b73a40'
HELPER=Path('/input/preparation.py')
HELPER_SHA='e160a6bd22035ba7ad7d2ea3c0864994c2053dd21dcb83aa08f08698a6e57cf6'
PRIVATE=Path('/private')
DEADLINE_SECONDS=300
MAX_STREAM_BYTES=32768

NS=1000000000
CLOCK_FIELDS={'format','clock','boot_id_sha256','time_namespace','time_namespace_offsets_sha256','relay_start_ns','relay_deadline_ns'}

def clock_domain():
    # A daemon identity alone does not establish a monotonic clock domain.
    boot=Path('/proc/sys/kernel/random/boot_id').read_bytes()
    if re.fullmatch(rb'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\n',boot)is None:
        raise PermissionError('linux_boot_identity_required')
    namespace=os.readlink('/proc/self/ns/time')
    if re.fullmatch(r'time:\[[0-9]+\]',namespace)is None:raise PermissionError('linux_time_namespace_required')
    offsets=Path('/proc/self/timens_offsets').read_bytes()
    if len(offsets)>4096 or re.fullmatch(rb' *monotonic +[-0-9]+ +[0-9]+\n *boottime +[-0-9]+ +[0-9]+\n',offsets)is None:
        raise PermissionError('linux_time_namespace_offsets_required')
    return {'boot_id_sha256':hashlib.sha256(boot).hexdigest(),'time_namespace':namespace,
        'time_namespace_offsets_sha256':hashlib.sha256(offsets).hexdigest()}

def validate_clock_binding(value):
    if (type(value)is not dict or set(value)!=CLOCK_FIELDS or value.get('format')!='devbench.auth-relay-clock/1'
        or value.get('clock')!='CLOCK_MONOTONIC'
        or any(type(value.get(k))is not str or re.fullmatch('[0-9a-f]{64}',value[k])is None for k in ['boot_id_sha256','time_namespace_offsets_sha256'])
        or type(value.get('time_namespace'))is not str or re.fullmatch(r'time:\[[0-9]+\]',value['time_namespace'])is None
        or any(type(value.get(k))is not int or not 0<=value[k]<2**63 for k in ['relay_start_ns','relay_deadline_ns'])
        or value['relay_deadline_ns']-value['relay_start_ns']!=300*NS):
        raise PermissionError('exact_relay_clock_binding_required')
    return value


def session_deadlines(session,now_ns=None,domain=None):
    if type(session)is not dict or set(session)!={'proxy_ip','clock_binding'}:raise PermissionError('exact_bound_public_session_required')
    binding=validate_clock_binding(session['clock_binding'])
    actual=clock_domain()if domain is None else domain
    if type(actual)is not dict or set(actual)!={'boot_id_sha256','time_namespace','time_namespace_offsets_sha256'} or any(binding[k]!=v for k,v in actual.items()):
        raise PermissionError('same_linux_monotonic_domain_required')
    now=time.monotonic_ns()if now_ns is None else now_ns
    if type(now)is not int or not binding['relay_start_ns']<=now<binding['relay_deadline_ns']-7*NS:
        raise TimeoutError('relay_login_budget_exhausted')
    return binding['relay_deadline_ns']-7*NS,binding['relay_deadline_ns']

def require_login_budget(deadline):
    if time.monotonic_ns()>=deadline:raise TimeoutError('relay_login_budget_exhausted')

def load_preparation():
    if HELPER.is_symlink() or hashlib.sha256(HELPER.read_bytes()).hexdigest()!=HELPER_SHA:
        raise PermissionError('fixed_public_preparation_changed')
    spec=importlib.util.spec_from_file_location('fixed_device_preparation',HELPER)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def session_proxy(data):
    if type(data)is not dict or set(data)!={'proxy_ip'} or type(data['proxy_ip'])is not str:
        raise PermissionError('fixed_inspected_session_binding_required')
    value=ipaddress.IPv4Address(data['proxy_ip'])
    if not any(value in ipaddress.IPv4Network(n)for n in ['10.0.0.0/8','172.16.0.0/12','192.168.0.0/16']):
        raise PermissionError('inspected_internal_relay_address_required')
    return 'http://'+str(value)+':8443'


def tls_only_positive(proxy_ip,deadline):
    session_proxy({'proxy_ip':proxy_ip})
    authority='auth.openai.com:443'
    context=ssl.create_default_context()
    context.check_hostname=True
    context.verify_mode=ssl.CERT_REQUIRED
    remaining=deadline-time.monotonic()
    if remaining<=0:raise TimeoutError('auth_window_exhausted_before_tls')
    with socket.create_connection((proxy_ip,8443),timeout=min(10,remaining))as transport:
        transport.sendall(('CONNECT '+authority+' HTTP/1.1\r\nHost: '+authority+'\r\n\r\n').encode('ascii'))
        header=b''
        while not header.endswith(b'\r\n\r\n'):
            if time.monotonic()>=deadline:raise TimeoutError('tls_probe_header_deadline')
            transport.settimeout(min(10,max(.001,deadline-time.monotonic())))
            if len(header)>=4096:raise ValueError('tls_probe_header_bound')
            piece=transport.recv(1)
            if not piece:raise ValueError('tls_probe_proxy_closed')
            header+=piece
        if header!=b'HTTP/1.1 200 Connection Established\r\n\r\n':
            raise PermissionError('tls_probe_connect_denied')
        with context.wrap_socket(transport,server_hostname='auth.openai.com')as encrypted:
            version=encrypted.version()
            if version not in ['TLSv1.2','TLSv1.3']:raise PermissionError('verified_modern_tls_required')
    return {'event':'tls_positive','hostname_verified':True,'certificate_chain_verified':True,
        'http_application_bytes_sent':0,'tls_version':version,'actual_cli_tls_validation_is_separate':True}


def emit(event):
    print(json.dumps(event,sort_keys=True,separators=(',',':')),flush=True)


def run_login(preparation,proxy,deadline):
    require_login_budget(deadline)
    environment=preparation.login_environment(proxy)
    # Only known public policy/config files are inspected before login begins.
    home=PRIVATE/'codex-home'
    if {p.name for p in home.iterdir()}!={'AGENTS.override.md','config.toml'}:
        raise PermissionError('fresh_client_state_required')
    policy=home/'AGENTS.override.md'
    if policy.is_symlink() or policy.read_bytes()!=preparation.PUBLIC_POLICY:
        raise PermissionError('owned_public_login_policy_changed')
    require_login_budget(deadline)
    process=subprocess.Popen(preparation.login_arguments(),cwd=str(PRIVATE/'workspace'),env=environment,
        stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,start_new_session=True,close_fds=True)
    attempted=True;count=0;prompt_seen=False;stdout_prompt=bytearray();error=None
    heartbeat=time.monotonic()+30
    try:
        with selectors.DefaultSelector()as selector:
            for stream,label in [(process.stdout,'stdout'),(process.stderr,'stderr')]:
                os.set_blocking(stream.fileno(),False);selector.register(stream,selectors.EVENT_READ,label)
            while selector.get_map():
                now=time.monotonic()
                if time.monotonic_ns()>=deadline:raise TimeoutError('bounded_login_window_ended')
                if prompt_seen and now>=heartbeat:
                    emit({'event':'waiting_for_user','remaining_seconds':max(0,int((deadline-time.monotonic_ns())//NS))});heartbeat=now+30
                for key,_ in selector.select(.05):
                    chunk=os.read(key.fd,4096)
                    if not chunk:selector.unregister(key.fileobj);continue
                    count+=len(chunk)
                    if count>MAX_STREAM_BYTES:raise ValueError('auth_stream_byte_limit')
                    if key.data=='stdout' and not prompt_seen:
                        stdout_prompt.extend(chunk)
                        if len(stdout_prompt)>8192:raise ValueError('bounded_device_prompt_required')
                        try:challenge=preparation.extract_device_prompt(bytes(stdout_prompt))
                        except ValueError:continue
                        require_login_budget(deadline)
                        emit({'event':'device_authorization',**challenge,'remaining_seconds':max(0,int((deadline-time.monotonic_ns())//NS))})
                        stdout_prompt.clear();prompt_seen=True;heartbeat=time.monotonic()+30
            while os.waitid(os.P_PID,process.pid,os.WEXITED|os.WNOWAIT|os.WNOHANG)is None:
                if time.monotonic_ns()>=deadline:raise TimeoutError('login_exit_deadline')
                time.sleep(.005)
    except BaseException as failure:
        error='Cancelled' if isinstance(failure,(KeyboardInterrupt,SystemExit))else type(failure).__name__
    finally:
        uncertain=False
        try:os.killpg(process.pid,signal.SIGKILL)
        except ProcessLookupError:pass
        except OSError:uncertain=True
        try:process.wait(timeout=2)
        except subprocess.TimeoutExpired:uncertain=True
        for stream in [process.stdout,process.stderr]:stream.close()
        stdout_prompt.clear()
    return {'event':'login_result','format':'devbench.personal-device-login-result/1',
        'official_cli_invocations_attempted':int(attempted),'official_cli_launch_confirmed':True,
        'official_cli_exit_code':process.returncode,'process_reaped':process.returncode is not None,
        'process_group_cleanup_uncertain':uncertain,'error_category':error,
        'device_prompt_presented':prompt_seen,'combined_auth_stream_bytes':count,
        'official_login_completed':process.returncode==0 and error is None and not uncertain,
        'raw_auth_stream_retained':False,'raw_auth_stream_hash_retained':False,
        'authentication_cache_contents_read_by_worker':False,'authentication_cache_hashed_by_worker':False,
        'live_generation_allowed':False,'biological_execution_allowed':False,'native_admission_qualified':False}


def official_status(preparation,proxy,relay_deadline):
    """Only the official client reads its new cache; raw output stays in RAM."""
    deadline=min(time.monotonic_ns()+5*NS,relay_deadline)
    if time.monotonic_ns()>=deadline:raise TimeoutError('relay_status_budget_exhausted')
    args=preparation.login_arguments()[:-1]+['status']
    process=subprocess.Popen(args,cwd=str(PRIVATE/'workspace'),env=preparation.login_environment(proxy),
        stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,start_new_session=True,close_fds=True)
    output=bytearray();error=None;uncertain=False
    try:
        with selectors.DefaultSelector()as selector:
            for stream in [process.stdout,process.stderr]:os.set_blocking(stream.fileno(),False);selector.register(stream,selectors.EVENT_READ)
            while selector.get_map():
                if time.monotonic_ns()>=deadline:raise TimeoutError('official_status_deadline')
                for key,_ in selector.select(.05):
                    piece=os.read(key.fd,4096)
                    if not piece:selector.unregister(key.fileobj);continue
                    output.extend(piece)
                    if len(output)>8192:raise ValueError('official_status_byte_bound')
            while os.waitid(os.P_PID,process.pid,os.WEXITED|os.WNOWAIT|os.WNOHANG)is None:
                if time.monotonic_ns()>=deadline:raise TimeoutError('official_status_exit_deadline')
                time.sleep(.005)
    except Exception as failure:error=type(failure).__name__
    finally:
        try:os.killpg(process.pid,signal.SIGKILL)
        except ProcessLookupError:pass
        except OSError:uncertain=True
        try:process.wait(timeout=2)
        except subprocess.TimeoutExpired:uncertain=True
        for stream in [process.stdout,process.stderr]:stream.close()
    verified=process.returncode==0 and error is None and not uncertain and bytes(output).strip()==b'Logged in using ChatGPT'
    size=len(output);output.clear()
    return {'official_status_invocations_attempted':1,'official_status_verified_chatgpt':verified,
        'official_status_exit_code':process.returncode,'official_status_error_category':error,
        'official_status_process_reaped':process.returncode is not None,'official_status_stream_bytes':size,
        'official_status_process_group_cleanup_uncertain':uncertain}


def setup_failure(error):
    known={'same_linux_monotonic_domain_required','relay_login_budget_exhausted','exact_relay_clock_binding_required',
        'exact_bound_public_session_required','linux_boot_identity_required','linux_time_namespace_required',
        'linux_time_namespace_offsets_required','bounded_public_session_binding_required','duplicate_public_session_field'}
    return {'event':'auth_setup_failed','error_category':type(error).__name__,
        'reason_code':str(error)if str(error)in known else 'fixed_auth_setup_rejected',
        'official_cli_invocations_attempted':0,'live_generation_allowed':False,'biological_execution_allowed':False,
        'native_admission_qualified':False}


def unique_public_fields(pairs):
    value={}
    for key,item in pairs:
        if key in value:raise ValueError('duplicate_public_session_field')
        value[key]=item
    return value


def main():
    if len(sys.argv)!=2 or sys.argv[1]not in ['tls-only','device-login']:raise PermissionError('fixed_auth_operation_required')
    operation=sys.argv[1]
    # No independent 300-second client timer: consume the relay's bound deadline.
    if sys.platform!='linux' or os.environ.get('DEVBENCH_AUTH_ONLY')!='1':
        raise PermissionError('released_auth_only_linux_enclosure_required')
    try:
        digest=hashlib.sha256()
        with CLIENT.open('rb')as stream:
            for piece in iter(lambda:stream.read(1048576),b''):digest.update(piece)
        if digest.hexdigest()!=CLIENT_SHA:raise PermissionError('pinned_official_login_client_changed')
        preparation=load_preparation()
        with Path('/input/session.json').open('rb')as stream:raw=stream.read(4097)
        if len(raw)>4096:raise PermissionError('bounded_public_session_binding_required')
        session=json.loads(raw,object_pairs_hook=unique_public_fields)
        deadline,relay_deadline=session_deadlines(session)
        proxy=session_proxy({'proxy_ip':session['proxy_ip']})
        if operation=='tls-only':deadline=min(deadline/NS,time.monotonic()+30)
    except (PermissionError,TimeoutError,FileNotFoundError,ValueError,OSError)as error:
        emit(setup_failure(error));return 1
    if operation=='tls-only':
        try:emit(tls_only_positive(session['proxy_ip'],deadline));return 0
        except Exception as error:
            emit({'event':'auth_setup_failed','error_category':type(error).__name__,
                'official_cli_invocations_attempted':0,'live_generation_allowed':False,'biological_execution_allowed':False})
            return 1
    try:result=run_login(preparation,proxy,deadline)
    except TimeoutError as error:
        emit(setup_failure(error));return 1
    status={'official_status_invocations_attempted':0,'official_status_verified_chatgpt':False,
        'official_status_exit_code':None,'official_status_error_category':None,'official_status_process_reaped':False,
        'official_status_stream_bytes':0,'official_status_process_group_cleanup_uncertain':False}
    if result['official_login_completed']:
        status['official_status_invocations_attempted']=1
        try:status=official_status(preparation,proxy,relay_deadline)
        except Exception as error:status['official_status_error_category']=type(error).__name__
    result.update(status)
    result['official_login_completed']=result['official_login_completed'] and result['official_status_verified_chatgpt']
    emit(result)
    return 0 if result['official_login_completed']else 1


if __name__=='__main__':raise SystemExit(main())
