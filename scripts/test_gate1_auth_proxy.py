"""Auth-only relay preparation tests: synthetic sockets and fake DNS only."""
from pathlib import Path
import importlib.util,socket,time
import pytest
SOURCE=Path('/Users/vajraang/Documents/ChatGPT/bio-agent-harness/scripts/gate1_auth_proxy.py')

def load():
    spec=importlib.util.spec_from_file_location('gate1_auth_proxy',SOURCE);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m

def answer(ip):return(socket.AF_INET,socket.SOCK_STREAM,socket.IPPROTO_TCP,'',(ip,443))

def test_only_fixed_exact_connect():
    m=load();assert m.parse_connect(b'CONNECT auth.openai.com:443 HTTP/1.1\r\nHost: auth.openai.com:443\r\n\r\n')==('auth.openai.com',443)

@pytest.mark.parametrize('payload',[
    b'CONNECT api.openai.com:443 HTTP/1.1\r\nHost: api.openai.com:443\r\n\r\n',
    b'CONNECT auth.openai.com:80 HTTP/1.1\r\nHost: auth.openai.com:80\r\n\r\n',
    b'CONNECT auth.openai.com.evil:443 HTTP/1.1\r\nHost: auth.openai.com.evil:443\r\n\r\n',
    b'CONNECT 127.0.0.1:443 HTTP/1.1\r\nHost: 127.0.0.1:443\r\n\r\n',
    b'GET https://auth.openai.com/ HTTP/1.1\r\nHost: auth.openai.com\r\n\r\n',
    b'CONNECT auth.openai.com:443 HTTP/1.1\r\nHost: auth.openai.com:443\r\nAuthorization: secret\r\n\r\n',
    b'CONNECT auth.openai.com:443 HTTP/1.1\r\nHost: auth.openai.com:443\r\nProxy-Authorization: secret\r\n\r\n',
    b'CONNECT auth.openai.com:443 HTTP/1.1\r\nHost: evil:443\r\n\r\n',
    b'CONNECT auth.openai.com:443 HTTP/1.1\r\nHost: auth.openai.com:443\r\n\r\nextra',
    b'x'*4097,
])
def test_other_connect_forms_rejected(payload):
    with pytest.raises(ValueError):load().parse_connect(payload)

@pytest.mark.parametrize('ip',['0.0.0.0','10.1.2.3','100.64.1.1','127.0.0.1','169.254.169.254','172.16.1.1','192.0.0.8','192.0.2.1','192.88.99.1','192.168.1.1','198.18.0.1','198.51.100.1','203.0.113.1','224.0.0.1','240.0.0.1','255.255.255.255','::1','garbage'])
def test_non_global_or_special_ipv4_dns_answer_rejected(ip):
    with pytest.raises(ValueError):load().resolve_public_ipv4(lambda *args,**kwargs:[answer(ip)])

def test_all_dns_answers_checked_before_connection():
    m=load()
    with pytest.raises(ValueError):m.resolve_public_ipv4(lambda *args,**kwargs:[answer('93.184.216.34'),answer('127.0.0.1')])

def test_fixed_hostname_ipv4_resolver_and_bound():
    m=load();calls=[]
    def resolver(*args,**kwargs):calls.append((args,kwargs));return[answer('93.184.216.34'),answer('93.184.216.35')]
    assert m.resolve_public_ipv4(resolver)==['93.184.216.34','93.184.216.35']
    assert calls==[(('auth.openai.com',443,socket.AF_INET,socket.SOCK_STREAM,socket.IPPROTO_TCP),{})]
    for answers in ([],[answer('93.184.216.34')]*17):
        with pytest.raises(ValueError):m.resolve_public_ipv4(lambda *args:answers)

@pytest.mark.parametrize('bad',[
    (socket.AF_INET6,socket.SOCK_STREAM,socket.IPPROTO_TCP,'',('::1',443,0,0)),
    (socket.AF_INET,socket.SOCK_DGRAM,socket.IPPROTO_UDP,'',('93.184.216.34',443)),
    (socket.AF_INET,socket.SOCK_STREAM,socket.IPPROTO_TCP,'',('93.184.216.34',80)),
])
def test_wrong_dns_family_type_port_rejected(bad):
    with pytest.raises(ValueError):load().resolve_public_ipv4(lambda *args:[bad])

def test_fixed_caps_and_metadata_exclude_payloads():
    m=load()
    assert(m.WALL_SECONDS,m.HARD_WALL_SECONDS,m.IO_SECONDS,m.HEADER_MAX,m.CONNECTION_MAX,m.ACTIVE_MAX,m.CONNECTION_BYTES,m.TOTAL_BYTES,m.DNS_MAX)==(300,315,10,4096,32,4,1048576,4194304,16)
    ready=m.ready_record();assert set(ready)=={'format','phase','authority','listen_port','limits','live_generation_allowed','biological_execution_allowed'}
    assert ready['phase']=='ready' and ready['authority']=='auth.openai.com:443'
    assert ready['live_generation_allowed']is ready['biological_execution_allowed']is False

def test_global_and_per_connection_byte_budget_is_strict():
    m=load();b=m.Budget(time.monotonic()+10)
    assert b.reserve_bytes(0,m.CONNECTION_BYTES)is True
    assert b.reserve_bytes(m.CONNECTION_BYTES,1)is False
    for _ in range(3):assert b.reserve_bytes(0,m.CONNECTION_BYTES)is True
    assert b.reserve_bytes(0,1)is False
    assert b.snapshot()['tunnel_bytes_reserved']==m.TOTAL_BYTES

def test_connection_and_active_caps():
    m=load();b=m.Budget(time.monotonic()+10)
    for _ in range(m.CONNECTION_MAX):assert b.admit_connection()is True
    assert b.admit_connection()is False
    assert b.snapshot()['connections_seen']==m.CONNECTION_MAX
    for _ in range(m.ACTIVE_MAX):assert b.start_active()is True
    assert b.start_active()is False
    for _ in range(m.ACTIVE_MAX):b.finish_active()
    assert b.snapshot()['active_connections']==0

def test_rejected_authority_never_calls_dns(monkeypatch):
    m=load();a,b=socket.socketpair();state=m.Budget(time.monotonic()+5)
    monkeypatch.setattr(m,'resolve_public_ipv4',lambda:pytest.fail('DNS called before authority admitted'))
    with a,b:
        b.sendall(b'CONNECT api.openai.com:443 HTTP/1.1\r\nHost: api.openai.com:443\r\n\r\n')
        assert state.start_active()
        m.handle_connection(a,state)
        assert b.recv(512)==m.DENY
    assert state.snapshot()['rejected_requests']==1

def test_allowed_connect_uses_validated_numeric_address_and_relays_synthetic_bytes(monkeypatch):
    import threading
    m=load();server,client=socket.socketpair();upstream,peer=socket.socketpair();state=m.Budget(time.monotonic()+3);calls=[]
    monkeypatch.setattr(m,'resolve_public_ipv4',lambda:['93.184.216.34'])
    def connector(address,budget):calls.append(address);return upstream
    monkeypatch.setattr(m,'connect_numeric',connector)
    assert state.start_active()
    worker=threading.Thread(target=m.handle_connection,args=(server,state));worker.start()
    try:
        with client,peer:
            client.settimeout(1);peer.settimeout(1)
            client.sendall(b'CONNECT auth.openai.com:443 HTTP/1.1\r\nHost: auth.openai.com:443\r\n\r\n')
            assert client.recv(512)==m.OK
            client.sendall(b'synthetic-request');assert peer.recv(512)==b'synthetic-request'
            peer.sendall(b'synthetic-response');assert client.recv(512)==b'synthetic-response'
    finally:
        worker.join(2)
        assert not worker.is_alive()
    assert calls==['93.184.216.34']
    stats=state.snapshot();assert stats['admitted_tunnels']==1 and stats['active_connections']==0
    assert stats['tunnel_bytes_reserved']==len(b'synthetic-requestsynthetic-response')

def test_denied_dns_never_connects(monkeypatch):
    m=load();a,b=socket.socketpair();state=m.Budget(time.monotonic()+2)
    def deny():raise ValueError('non-global synthetic answer')
    monkeypatch.setattr(m,'resolve_public_ipv4',deny)
    monkeypatch.setattr(m,'connect_numeric',lambda *args:pytest.fail('connect called with denied DNS'))
    with a,b:
        b.settimeout(.5);b.sendall(b'CONNECT auth.openai.com:443 HTTP/1.1\r\nHost: auth.openai.com:443\r\n\r\n')
        assert state.start_active();m.handle_connection(a,state);assert b.recv(512)==m.DENY
    assert state.snapshot()['dns_denied']==1

def test_concurrent_reservations_cannot_exceed_global_cap():
    import threading
    m=load();state=m.Budget(time.monotonic()+2);accepted=[]
    def reserve():accepted.append(state.reserve_bytes(0,m.CONNECTION_BYTES))
    workers=[threading.Thread(target=reserve)for _ in range(12)]
    for worker in workers:worker.start()
    for worker in workers:worker.join(1);assert not worker.is_alive()
    assert sum(accepted)==4 and state.snapshot()['tunnel_bytes_reserved']==m.TOTAL_BYTES

def test_expired_deadline_rejects_before_new_network_call():
    m=load();state=m.Budget(time.monotonic()-.1)
    with pytest.raises(TimeoutError):m.remaining_timeout(state,10)

def test_final_metadata_contains_only_counters_and_fixed_scope():
    m=load();state=m.Budget(time.monotonic()+1);state.stop('operator_stop');record=m.final_record(state)
    assert set(record)=={'format','phase','authority','stop_reason','counters','live_generation_allowed','biological_execution_allowed'}
    assert record['phase']=='final' and record['stop_reason']=='operator_stop'
    assert all(type(value)is int for value in record['counters'].values())
    assert len(__import__('json').dumps(m.ready_record()))+len(__import__('json').dumps(record))<4096

def test_numeric_connector_rechecks_global_address_before_creating_socket(monkeypatch):
    m=load();state=m.Budget(time.monotonic()+1)
    monkeypatch.setattr(m.socket,'socket',lambda *args:pytest.fail('socket created for forbidden numeric address'))
    with pytest.raises(ValueError):m.connect_numeric('127.0.0.1',state)

def test_expired_header_never_reads_socket():
    m=load();state=m.Budget(time.monotonic()-.1)
    class NoRead:
        def settimeout(self,value):pytest.fail('expired header touched socket')
        def recv(self,size):pytest.fail('expired header read socket')
    with pytest.raises(TimeoutError):m.read_header(NoRead(),state)

def test_operator_stop_prevents_more_network_activity():
    m=load();state=m.Budget(time.monotonic()+10);state.operator_requested=True
    with pytest.raises(TimeoutError):m.remaining_timeout(state,10)
