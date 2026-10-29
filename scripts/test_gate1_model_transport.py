"""No-network model transport preparation checks; no credentials or inference."""
from pathlib import Path
import importlib.util,json,socket,ssl
import pytest
W=Path('/Users/vajraang/Documents/ChatGPT/bio-agent-harness')

def load(name):
    path=W/'scripts'/name;spec=importlib.util.spec_from_file_location(name.replace('.','_'),path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m

def model(monkeypatch):
    m=load('gate1_model_proxy.py');monkeypatch.setattr(m,'SHARED_SOURCE',W/'scripts/gate1_auth_proxy.py');return m

def test_model_role_accepts_only_fixed_model_authority(monkeypatch):
    m=model(monkeypatch);relay=m.load_relay()
    good=b'CONNECT chatgpt.com:443 HTTP/1.1\r\nHost: chatgpt.com:443\r\n\r\n'
    assert relay.parse_connect(good)==('chatgpt.com',443)
    for host in [b'auth.openai.com',b'api.openai.com',b'chatgpt.com.evil',b'127.0.0.1']:
        with pytest.raises(ValueError):relay.parse_connect(good.replace(b'chatgpt.com',host))

def test_auth_role_is_unchanged_and_separate(monkeypatch):
    m=model(monkeypatch);relay=m.load_relay();auth=load('gate1_auth_proxy.py')
    assert auth.HOST=='auth.openai.com' and relay.HOST=='chatgpt.com'
    assert auth.ready_record()['format']=='devbench.gate1-auth-proxy/1'
    with pytest.raises(ValueError):auth.parse_connect(b'CONNECT chatgpt.com:443 HTTP/1.1\r\nHost: chatgpt.com:443\r\n\r\n')

def test_shared_source_mismatch_refused_before_import(monkeypatch):
    m=model(monkeypatch);monkeypatch.setattr(m,'SHARED_SHA256','0'*64)
    with pytest.raises(PermissionError):m.load_relay()

def test_model_metadata_is_distinct_and_caps_preserved(monkeypatch):
    import time
    m=model(monkeypatch);relay=m.load_relay();ready=relay.ready_record();final=relay.final_record(relay.Budget(time.monotonic()+1))
    assert ready['format']==final['format']=='devbench.gate1-model-proxy/1'
    assert ready['authority']==final['authority']=='chatgpt.com:443'
    assert ready['transport_role']==final['transport_role']=='model-host-only'
    assert ready['limits']['wall_seconds']==300 and ready['limits']['hard_wrapper_seconds']==315
    assert ready['limits']['total_tunnel_bytes']==4194304
    assert ready['live_generation_allowed']is ready['biological_execution_allowed']is False

def test_model_dns_uses_only_model_host(monkeypatch):
    m=model(monkeypatch);relay=m.load_relay();calls=[]
    def fake(*args):calls.append(args);return[(socket.AF_INET,socket.SOCK_STREAM,socket.IPPROTO_TCP,'',('93.184.216.34',443))]
    assert relay.resolve_public_ipv4(fake)==['93.184.216.34']
    assert calls[0][0]=='chatgpt.com'

class FakeSocket:
    def __init__(self):self.sent=[];self.target=None
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def settimeout(self,value):assert 0<value<=15
    def connect(self,target):self.target=target
    def sendall(self,payload):self.sent.append(payload)
    def recv(self,size):return b'HTTP/1.1 200 Connection Established\r\n\r\n'
    def close(self):pass

class FakeTLS:
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def version(self):return'TLSv1.3'

class FakeContext:
    check_hostname=True
    verify_mode=ssl.CERT_REQUIRED
    def __init__(self):self.server_name=None
    def wrap_socket(self,raw,server_hostname):self.server_name=server_hostname;return FakeTLS()

def test_tls_probe_sends_connect_only_and_uses_fixed_sni():
    m=load('gate1_model_tls_probe.py');raw=FakeSocket();ctx=FakeContext()
    result=m.perform_probe('172.18.0.2',socket_factory=lambda *args:raw,context_factory=lambda:ctx)
    assert result['passed']is True and result['application_http_sent']is False
    assert raw.target==('172.18.0.2',8443) and ctx.server_name=='chatgpt.com'
    assert raw.sent==[b'CONNECT chatgpt.com:443 HTTP/1.1\r\nHost: chatgpt.com:443\r\n\r\n']
    assert result['live_generation_allowed']is result['biological_execution_allowed']is False

@pytest.mark.parametrize('ip',['127.0.0.1','0.0.0.0','169.254.169.254','8.8.8.8','::1','bad'])
def test_probe_rejects_non_owned_proxy_address_before_socket(ip):
    m=load('gate1_model_tls_probe.py')
    with pytest.raises(ValueError):m.perform_probe(ip,socket_factory=lambda *args:pytest.fail('forbidden proxy address touched socket'),context_factory=FakeContext)

def test_probe_refuses_disabled_certificate_validation():
    m=load('gate1_model_tls_probe.py');ctx=FakeContext();ctx.verify_mode=ssl.CERT_NONE
    with pytest.raises(PermissionError):m.perform_probe('172.18.0.2',socket_factory=lambda *args:pytest.fail('unverified TLS touched socket'),context_factory=lambda:ctx)

def test_exact_public_source_binds_default_chatgpt_endpoint():
    r=W/'reports/rna-native-gate1/2026-10-21-v2/provider-transport'
    source=(r/'public-model-provider-info-0.147.0.rs').read_text()
    assert 'pub const CHATGPT_CODEX_BASE_URL: &str = "https://chatgpt.com/backend-api/codex";'in source
    assert 'AuthMode::Chatgpt'in source and '.unwrap_or_else(|| default_base_url.to_string())'in source
    config=(r/'public-core-config-0.147.0.rs').read_text()
    assert '.unwrap_or_else(|| "openai".to_string())'in config

def test_tls_probe_accepts_fragmented_bounded_connect_response():
    m=load('gate1_model_tls_probe.py')
    class Fragmented(FakeSocket):
        def __init__(self):super().__init__();self.chunks=iter([b'HTTP/1.1 200 ',b'Connection Established\r\n',b'\r\n'])
        def recv(self,size):return next(self.chunks)
    assert m.perform_probe('172.18.0.2',socket_factory=lambda *args:Fragmented(),context_factory=FakeContext)['passed']is True

def test_tls_probe_rejects_unexpected_proxy_response_before_tls():
    m=load('gate1_model_tls_probe.py')
    class Denied(FakeSocket):
        def recv(self,size):return b'HTTP/1.1 403 Forbidden\r\n\r\n'
    class NoTLS(FakeContext):
        def wrap_socket(self,*args,**kwargs):pytest.fail('TLS began after rejected CONNECT')
    with pytest.raises(ValueError):m.perform_probe('172.18.0.2',socket_factory=lambda *args:Denied(),context_factory=NoTLS)

def test_tls_probe_rejects_disabled_hostname_check():
    m=load('gate1_model_tls_probe.py');ctx=FakeContext();ctx.check_hostname=False
    with pytest.raises(PermissionError):m.perform_probe('172.18.0.2',socket_factory=lambda *args:pytest.fail('socket created without hostname checking'),context_factory=lambda:ctx)
