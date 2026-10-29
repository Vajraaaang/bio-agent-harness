import importlib.util
from pathlib import Path
import pytest
W=Path('/Users/vajraang/Documents/ChatGPT/bio-agent-harness')

def load():
    p=W/'scripts/gate1_device_auth_client.py';s=importlib.util.spec_from_file_location('auth_client_under_test',p)
    m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m


def test_session_proxy_accepts_only_private_numeric_binding_no_url_or_credentials():
    m=load();assert m.session_proxy({'proxy_ip':'172.28.0.2'})=='http://172.28.0.2:8443'
    for value in [{'proxy_ip':'127.0.0.1'},{'proxy_ip':'169.254.169.254'},{'proxy_ip':'8.8.8.8'},
        {'proxy_ip':'auth.openai.com'},{'proxy_ip':'172.28.0.2','token':'untrusted'}]:
        with pytest.raises((PermissionError,ValueError)):m.session_proxy(value)


def test_fixed_public_helper_load_pins_corrected_login_only(monkeypatch):
    m=load();monkeypatch.setattr(m,'HELPER',W/'scripts/prepare_gate1_device_auth_v2.py')
    preparation=m.load_preparation();assert preparation.login_arguments()[-2:]==['login','--device-auth']
    assert '--strict-config'not in preparation.login_arguments() and 'exec'not in preparation.login_arguments()


def test_changed_helper_rejected_before_import(tmp_path,monkeypatch):
    m=load();p=tmp_path/'changed';p.write_text("raise AssertionError('do not import')")
    monkeypatch.setattr(m,'HELPER',p)
    with pytest.raises(PermissionError):m.load_preparation()


def test_tls_positive_sends_connect_only_and_verifies_exact_hostname(monkeypatch):
    import time
    m=load();sent=[];names=[];header=iter(b'HTTP/1.1 200 Connection Established\r\n\r\n')
    class Transport:
        def __enter__(self):return self
        def __exit__(self,*_args):pass
        def sendall(self,data):sent.append(data)
        def recv(self,_count):return bytes([next(header)])
        def settimeout(self,_timeout):pass
    class Encrypted:
        def __enter__(self):return self
        def __exit__(self,*_args):pass
        def version(self):return 'TLSv1.3'
    class Context:
        def wrap_socket(self,_transport,server_hostname):names.append(server_hostname);return Encrypted()
    ctx=Context()
    monkeypatch.setattr(m.socket,'create_connection',lambda *_args,**_kwargs:Transport())
    monkeypatch.setattr(m.ssl,'create_default_context',lambda:ctx)
    result=m.tls_only_positive('172.28.0.2',time.monotonic()+10)
    assert sent==[b'CONNECT auth.openai.com:443 HTTP/1.1\r\nHost: auth.openai.com:443\r\n\r\n']
    assert names==['auth.openai.com']and ctx.check_hostname is True and ctx.verify_mode==m.ssl.CERT_REQUIRED
    assert result['http_application_bytes_sent']==0 and result['certificate_chain_verified']


@pytest.mark.parametrize('raw,verified',[(b'Logged in using ChatGPT\n',True),(b'Logged in using an API key - synthetic-private-value\n',False)])
def test_official_status_projects_only_method_verdict_and_never_raw_cache_or_output(monkeypatch,raw,verified):
    from types import SimpleNamespace
    m=load();events=[];reads={11:iter([raw,b'']),12:iter([b''])}
    class Stream:
        def __init__(self,fd):self.fd=fd
        def fileno(self):return self.fd
        def close(self):pass
    class Process:
        pid=12345;returncode=None;stdout=Stream(11);stderr=Stream(12)
        def wait(self,timeout):events.append('reap');self.returncode=0;return 0
    class Selector:
        def __init__(self):self.values={}
        def __enter__(self):return self
        def __exit__(self,*_args):pass
        def register(self,stream,*_args):self.values[stream.fd]=SimpleNamespace(fd=stream.fd,fileobj=stream)
        def unregister(self,stream):self.values.pop(stream.fd)
        def get_map(self):return self.values
        def select(self,*_args):return [(key,None)for key in self.values.values()]
    arguments=[]
    def spawn(args,**_kwargs):arguments.append(args);return Process()
    monkeypatch.setattr(m.subprocess,'Popen',spawn);monkeypatch.setattr(m.selectors,'DefaultSelector',Selector)
    monkeypatch.setattr(m.os,'set_blocking',lambda *_args:None);monkeypatch.setattr(m.os,'read',lambda fd,_max:next(reads[fd]))
    monkeypatch.setattr(m.os,'waitid',lambda *_args:object(),raising=False)
    for name,value in [('P_PID',1),('WEXITED',4),('WNOWAIT',0x1000000),('WNOHANG',1)]:monkeypatch.setattr(m.os,name,value,raising=False)
    monkeypatch.setattr(m.os,'killpg',lambda *_args:events.append('kill'))
    preparation=SimpleNamespace(login_arguments=lambda:['/client/bin/codex','login','--device-auth'],login_environment=lambda _proxy:{})
    result=m.official_status(preparation,'http://172.28.0.2:8443')
    assert arguments==[['/client/bin/codex','login','status']]
    assert result['official_status_verified_chatgpt']is verified
    assert 'synthetic-private-value'not in str(result)and 'Logged in using'not in str(result)
    assert events==['kill','reap']
