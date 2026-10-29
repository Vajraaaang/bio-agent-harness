"""Pinned fork CONNECT grammar; no client, public connection or inference."""
from pathlib import Path
import importlib.util
import pytest
W=Path('/Users/vajraang/Documents/ChatGPT/bio-agent-harness')
ROOT=W/'reports/rna-native-gate1/2026-10-21-v2/provider-transport'
WIRE=(ROOT/'header-compat-v2/fork-connect-wire-source-derived.http').read_bytes()
HOST_ONLY=b'CONNECT chatgpt.com:443 HTTP/1.1\r\nHost: chatgpt.com:443\r\n\r\n'

def load(name):
    spec=importlib.util.spec_from_file_location(name.replace('.','_'),W/'scripts'/name);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m

def relay(monkeypatch,version='v2'):
    m=load('gate1_model_proxy_v2.py'if version=='v2'else'gate1_model_proxy.py');monkeypatch.setattr(m,'SHARED_SOURCE',W/'scripts/gate1_auth_proxy.py');return m.load_relay()

def test_source_derived_wire_matches_exact_pinned_fork_builder():
    source=(ROOT/'ws-proxy-source-assessment-v1/tungstenite-proxy.rs').read_text()
    assert 'format!("CONNECT {authority} HTTP/1.1\\r\\n")'in source
    assert 'format!("Host: {authority}\\r\\n")'in source
    assert 'b"Proxy-Connection: Keep-Alive\\r\\n"'in source
    assert WIRE==b'CONNECT chatgpt.com:443 HTTP/1.1\r\nHost: chatgpt.com:443\r\nProxy-Connection: Keep-Alive\r\n\r\n'

def test_old_model_parser_rejects_exact_fork_wire(monkeypatch):
    with pytest.raises(ValueError):relay(monkeypatch,'v1').parse_connect(WIRE)

@pytest.mark.parametrize('payload',[HOST_ONLY,WIRE])
def test_v2_accepts_only_two_known_complete_templates(monkeypatch,payload):
    assert relay(monkeypatch).parse_connect(payload)==('chatgpt.com',443)

@pytest.mark.parametrize('payload',[
    WIRE.replace(b'chatgpt.com',b'api.openai.com'),WIRE.replace(b'chatgpt.com',b'auth.openai.com'),
    WIRE.replace(b'chatgpt.com',b'chatgpt.com.evil'),WIRE.replace(b':443',b':80'),
    WIRE.replace(b'Keep-Alive',b'close'),WIRE.replace(b'Keep-Alive',b'anything'),
    WIRE.replace(b'Proxy-Connection: Keep-Alive\r\n',b'Proxy-Connection: Keep-Alive\r\nProxy-Connection: Keep-Alive\r\n'),
    WIRE.replace(b'Host: chatgpt.com:443\r\n',b'Host: chatgpt.com:443\r\nHost: chatgpt.com:443\r\n'),
    WIRE[:-2]+b'Proxy-Authorization: synthetic-only\r\n\r\n',
    WIRE[:-2]+b'Authorization: synthetic-only\r\n\r\n',WIRE[:-2]+b'User-Agent: unexpected\r\n\r\n',
    WIRE.replace(b'Host: chatgpt.com:443\r\nProxy-Connection: Keep-Alive\r\n',b'Proxy-Connection: Keep-Alive\r\nHost: chatgpt.com:443\r\n'),
    WIRE+b'extra',WIRE.replace(b'CONNECT',b'GET'),WIRE.replace(b'HTTP/1.1',b'HTTP/1.0'),b'x'*4097,
])
def test_v2_rejects_all_other_header_forms(monkeypatch,payload):
    with pytest.raises(ValueError):relay(monkeypatch).parse_connect(payload)

def test_auth_source_and_policy_remain_host_only(monkeypatch):
    auth=load('gate1_auth_proxy.py');payload=WIRE.replace(b'chatgpt.com',b'auth.openai.com')
    with pytest.raises(ValueError):auth.parse_connect(payload)
    assert auth.parse_connect(HOST_ONLY.replace(b'chatgpt.com',b'auth.openai.com'))==('auth.openai.com',443)
    assert auth.HOST=='auth.openai.com'

def test_v2_distinct_receipt_and_all_shared_caps_unchanged(monkeypatch):
    r=relay(monkeypatch);old=relay(monkeypatch,'v1')
    assert r.ready_record()['format']=='devbench.gate1-model-proxy/2'
    for name in ['WALL_SECONDS','HARD_WALL_SECONDS','IO_SECONDS','HEADER_MAX','HEADER_SECONDS','CONNECTION_MAX','ACTIVE_MAX','DNS_MAX','CONNECTION_BYTES','TOTAL_BYTES','SPECIAL']:
        assert getattr(r,name)==getattr(old,name)
    assert r.ready_record()['limits']==old.ready_record()['limits']

@pytest.mark.parametrize('payload',[HOST_ONLY,WIRE])
def test_inherited_bounded_header_reader_uses_model_only_v2_parser(monkeypatch,payload):
    import socket,time
    r=relay(monkeypatch);a,b=socket.socketpair();budget=r.Budget(time.monotonic()+2)
    with a,b:
        a.settimeout(.5);b.settimeout(.5);b.sendall(payload)
        r.read_header(a,budget)
