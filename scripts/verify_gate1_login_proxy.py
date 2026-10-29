"""Official login proxy-selection negative; no upstream, auth or inference.

The fixed loopback relay first serves an owned reachability control, then only
rejects CONNECT requests. It has no DNS or upstream-connection implementation.
"""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import re
import selectors
import signal
import socket
import socketserver
import subprocess
import sys
from threading import Thread
import time

CLIENT=Path('/client/bin/codex')
CLIENT_SHA='cb0a15567e9a60a5820d54b0f6ae86d504dc3805c1eab21a47f70e3eb7b73a40'
ROOT=Path('/output/protocol')
AUTHORITY='auth.openai.com:443'
MAX_HEADER=4096
MAX_CAPTURE=16384


def login_arguments():
    return [str(CLIENT),'--strict-config','-c','cli_auth_credentials_store="file"',
        '-c','forced_login_method="chatgpt"','login','--device-auth']


def inspect_connect(data):
    if type(data)is not bytes or len(data)>MAX_HEADER or not data.endswith(b'\r\n\r\n'):
        raise ValueError('bounded_connect_header_required')
    try: lines=data.decode('ascii').split('\r\n')
    except UnicodeDecodeError:raise ValueError('ascii_connect_header_required') from None
    first=lines[0].split(' ')
    if len(first)!=3 or first[0]!='CONNECT' or first[2]!='HTTP/1.1':raise ValueError('fixed_connect_method_required')
    names=[];hosts=[]
    for line in lines[1:-2]:
        name,separator,value=line.partition(':')
        if not separator or re.fullmatch(r'[A-Za-z-]{1,64}',name)is None:raise ValueError('bounded_header_name_required')
        names.append(name.lower())
        if name.lower()=='host':hosts.append(value.strip())
    if len(names)>16:raise ValueError('header_count_bound')
    known=first[1]==AUTHORITY
    return {'authority':AUTHORITY if known else 'unexpected_authority','header_names':sorted(names),
        'matches_supported_header_grammar':known and names==['host'] and hosts==[AUTHORITY]}


def execute_login(environment):
    process=subprocess.Popen(login_arguments(),cwd=str(ROOT/'workspace'),env=environment,stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,stderr=subprocess.PIPE,start_new_session=True,close_fds=True)
    count=0;prompt_seen=False;error=None
    marker=b'Enter this one-time code'
    tails={process.stdout.fileno():b'',process.stderr.fileno():b''}
    try:
        end=time.monotonic()+15
        with selectors.DefaultSelector()as selector:
            for stream in [process.stdout,process.stderr]:os.set_blocking(stream.fileno(),False);selector.register(stream,selectors.EVENT_READ)
            while selector.get_map():
                if time.monotonic()>=end:raise TimeoutError('login_proxy_deadline')
                for key,_ in selector.select(.05):
                    chunk=os.read(key.fd,4096)
                    if not chunk:selector.unregister(key.fileobj);continue
                    count+=len(chunk)
                    if count>MAX_CAPTURE:raise ValueError('login_capture_bound')
                    # Only fixed public marker detection; no raw bytes/digests retained.
                    scan=tails[key.fd]+chunk
                    prompt_seen=prompt_seen or marker in scan
                    tails[key.fd]=scan[-(len(marker)-1):]
        while os.waitid(os.P_PID,process.pid,os.WEXITED|os.WNOWAIT|os.WNOHANG)is None:
            if time.monotonic()>=end:raise TimeoutError('login_exit_deadline')
            time.sleep(.005)
    except Exception as failure:error=type(failure).__name__
    finally:
        uncertain=False
        try:os.killpg(process.pid,signal.SIGKILL)
        except ProcessLookupError:pass
        except OSError:uncertain=True
        try:process.wait(timeout=2)
        except subprocess.TimeoutExpired:uncertain=True
        for stream in [process.stdout,process.stderr]:stream.close()
    return {'exit_code':process.returncode,'combined_stream_bytes':count,'raw_stream_retained':False,
        'raw_stream_hash_retained':False,'device_prompt_observed':prompt_seen,
        'error_category':error,'process_reaped':process.returncode is not None,'group_cleanup_uncertain':uncertain}


def main():
    if sys.platform!='linux' or os.environ.get('DEVBENCH_LOCAL_PROTOCOL_ONLY')!='1':raise PermissionError('reviewed_network_none_enclosure_required')
    digest=hashlib.sha256()
    with CLIENT.open('rb')as stream:
        for chunk in iter(lambda:stream.read(1048576),b''):digest.update(chunk)
    if digest.hexdigest()!=CLIENT_SHA:raise PermissionError('official_client_changed')
    ROOT.mkdir(mode=0o700)
    for name in ['home','codex-home','workspace']:(ROOT/name).mkdir(mode=0o700)
    (ROOT/'codex-home/AGENTS.override.md').write_bytes(b'Only the fixed official login proxy negative is authorized. No model generation or scientific execution.\n')
    observed=[];failures=[];ready=[]
    class Handler(socketserver.BaseRequestHandler):
        def handle(self):
            self.request.settimeout(1);data=b''
            try:
                while b'\r\n\r\n' not in data:
                    part=self.request.recv(min(1024,MAX_HEADER+1-len(data)))
                    if not part:raise ValueError('truncated_header')
                    data+=part
                    if len(data)>MAX_HEADER:raise ValueError('header_bound')
                if data==b'GET /owned-ready HTTP/1.1\r\nHost: owned-ready.invalid\r\n\r\n':
                    ready.append(True);self.request.sendall(b'HTTP/1.1 204 No Content\r\nContent-Length: 0\r\nConnection: close\r\n\r\n');return
                if len(observed)>=4:raise ValueError('connection_count_bound')
                observed.append(inspect_connect(data))
                self.request.sendall(b'HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\nConnection: close\r\n\r\n')
            except Exception:failures.append('bounded_proxy_request_failure')
    class Server(socketserver.TCPServer):allow_reuse_address=False
    server=Server(('127.0.0.1',0),Handler);server.timeout=.2
    thread=Thread(target=server.serve_forever,daemon=True);thread.start()
    result={'format':'devbench.official-login-proxy-negative/1','auth_issuer_requests':0,'upstream_connections':0,
        'live_model_calls':0,'live_generation_allowed':False,'biological_execution_allowed':False,
        'signin_qualified':False,'tls_qualified':False,'public_egress_qualified':False,'native_admission_qualified':False}
    try:
        with socket.create_connection(('127.0.0.1',server.server_address[1]),timeout=1)as control:
            control.sendall(b'GET /owned-ready HTTP/1.1\r\nHost: owned-ready.invalid\r\n\r\n')
            result['owned_proxy_reachability_passed']=control.recv(256).startswith(b'HTTP/1.1 204 ')
        if not result['owned_proxy_reachability_passed'] or ready!=[True]:raise RuntimeError('positive_control_failed')
        proxy='http://127.0.0.1:'+str(server.server_address[1])
        environment={'PATH':'/client/bin:/client/codex-path:/usr/bin:/bin','HOME':str(ROOT/'home'),
            'CODEX_HOME':str(ROOT/'codex-home'),'HTTPS_PROXY':proxy,'HTTP_PROXY':proxy,'NO_PROXY':'',
            'LANG':'C.UTF-8','LC_ALL':'C.UTF-8','RUST_LOG':'off'}
        result['login_process']=execute_login(environment)
    except Exception as error:result['error_category']=type(error).__name__
    finally:server.shutdown();server.server_close();thread.join(timeout=1)
    result.update(connect_observations=observed,proxy_failure_categories=failures)
    child=result.get('login_process',{})
    result['passed_proxy_selection_negative']=(result.get('owned_proxy_reachability_passed')is True
        and 1<=len(observed)<=4 and all(x['matches_supported_header_grammar']for x in observed) and not failures
        and child.get('exit_code')not in (None,0) and child.get('error_category')is None
        and child.get('process_reaped')is True and child.get('group_cleanup_uncertain')is False and child.get('device_prompt_observed')is False)
    with (ROOT/'result.json').open('x')as stream:stream.write(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,sort_keys=True))
    return 0 if result['passed_proxy_selection_negative'] else 2


if __name__=='__main__':raise SystemExit(main())
