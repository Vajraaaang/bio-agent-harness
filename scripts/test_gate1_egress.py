"""Credential-free synthetic-only CONNECT relay tests."""
import importlib.util
from pathlib import Path
import pytest

SOURCE = Path('/Users/vajraang/Documents/ChatGPT/bio-agent-harness/scripts/gate1_egress_fixture.py')

def load():
    spec = importlib.util.spec_from_file_location('gate1_egress_fixture', SOURCE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_allowed_connect():
    assert load().parse_connect(b'CONNECT gate1-approved.invalid:443 HTTP/1.1\r\nHost: gate1-approved.invalid:443\r\n\r\n') == ('gate1-approved.invalid',443)

@pytest.mark.parametrize('payload', [
    b'CONNECT evil.invalid:443 HTTP/1.1\r\nHost: evil.invalid:443\r\n\r\n',
    b'CONNECT gate1-approved.invalid:80 HTTP/1.1\r\nHost: gate1-approved.invalid:80\r\n\r\n',
    b'GET https://gate1-approved.invalid/ HTTP/1.1\r\nHost: gate1-approved.invalid\r\n\r\n',
    b'CONNECT 127.0.0.1:443 HTTP/1.1\r\nHost: 127.0.0.1:443\r\n\r\n',
    b'CONNECT 169.254.169.254:443 HTTP/1.1\r\nHost: 169.254.169.254:443\r\n\r\n',
    b'CONNECT gate1-approved.invalid.evil:443 HTTP/1.1\r\nHost: gate1-approved.invalid.evil:443\r\n\r\n',
    b'CONNECT gate1-approved.invalid:443 HTTP/1.1\r\nHost: gate1-approved.invalid:443\r\nAuthorization: secret\r\n\r\n',
    b'CONNECT gate1-approved.invalid:443 HTTP/1.1\r\nHost: gate1-approved.invalid:443\r\nProxy-Authorization: secret\r\n\r\n',
    b'CONNECT gate1-approved.invalid:443 HTTP/1.1\r\nHost: evil.invalid:443\r\n\r\n',
    b'CONNECT gate1-approved.invalid:443 HTTP/1.1\r\nHost: gate1-approved.invalid:443\r\n\r\npipelined',
    b'CONNECT gate1-approved.invalid:443 HTTP/1.0\r\nHost: gate1-approved.invalid:443\r\n\r\n',
    b'x'*4097,
])
def test_bad_connect_is_rejected(payload):
    with pytest.raises(ValueError): load().parse_connect(payload)

@pytest.mark.parametrize('value',['localhost','127.0.0.1','0.0.0.0','169.254.169.254','224.0.0.1','8.8.8.8','::1','bad'])
def test_peer_is_private_synthetic_ipv4_only(value):
    with pytest.raises(ValueError): load().validate_peer(value)

def test_peer_owned_private_address():
    assert load().validate_peer('172.30.0.2') == '172.30.0.2'

def test_bounds_are_fixed():
    m=load()
    assert (m.HEADER_MAX,m.BYTE_MAX,m.IO_SECONDS,m.SESSION_SECONDS,m.WALL_SECONDS,m.CONNECTION_MAX)==(4096,32768,2,5,20,12)

def supervisor():
    path=SOURCE.with_name('run_gate1_egress_diagnostic.py')
    spec=importlib.util.spec_from_file_location('gate1_egress_supervisor',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module

def test_fixed_container_limits_and_mounts():
    m=supervisor();args=m.container_arguments('peer','network-id',[])
    assert args[args.index('--memory')+1]=='134217728'
    assert args[args.index('--pids-limit')+1]=='16'
    assert args[args.index('--dns')+1]=='127.0.0.1'
    assert args[args.index('--pull=never')]=='--pull=never'
    assert args.count('--mount')==1
    assert ',readonly' in args[args.index('--mount')+1]
    assert '--publish' not in args and '--privileged' not in args

def test_network_record_requires_internal_and_inhibited_gateway():
    m=supervisor();record={'Id':'a'*64,'Name':m.NAMES['inside'],'Driver':'bridge','Internal':True,'EnableIPv6':False,'Options':{'com.docker.network.bridge.inhibit_ipv4':'true'},'Containers':{}}
    m.verify_network(record,'inside',set())
    for key,value in [('Internal',False),('EnableIPv6',True),('Options',{}),('Containers',{'b'*64:{}})]:
        bad=dict(record);bad[key]=value
        with pytest.raises(PermissionError):m.verify_network(bad,'inside',set())

def test_fragmented_valid_header():
    import socket
    m=load();a,b=socket.socketpair()
    with a,b:
        b.sendall(b'CONNECT gate1-approved.invalid:443 HTTP/1.1\r\nHost: gate1-approved.invalid:443\r\n\r\n')
        m.read_header(a)

def test_header_bound_rejects_before_forward():
    import socket
    m=load();a,b=socket.socketpair()
    with a,b:
        b.sendall(b'x'*4097)
        with pytest.raises(ValueError):m.read_header(a)

def test_tunnel_byte_bound():
    import socket,time
    m=load();m.BYTE_MAX=1024;client_a,client_b=socket.socketpair();peer_a,peer_b=socket.socketpair()
    with client_a,client_b,peer_a,peer_b:
        client_b.sendall(b'x'*(m.BYTE_MAX+1))
        with pytest.raises(ValueError,match='tunnel_bytes_exceeded'):
            m.relay(client_a,peer_a,time.monotonic()+1)

def test_tunnel_wall_bound():
    import socket,time
    m=load();a,b=socket.socketpair();c,d=socket.socketpair()
    with a,b,c,d:
        with pytest.raises(TimeoutError):m.relay(a,c,time.monotonic()-.1)

@pytest.mark.parametrize('bad',[{}, {'checks':{}}, {'role':'client','checks':{'pass':True}}])
def test_incomplete_observation_rejected(bad):
    with pytest.raises(ValueError):supervisor().validate_observed(bad)

def test_observation_bool_types_and_exact_controls():
    import inspect
    m=supervisor()
    keys={'allowlisted_mock_connect_echo','disallowed_host_denied','disallowed_port_denied','non_connect_denied','auth_header_denied','metadata_host_denied','direct_peer_tcp_denied','direct_peer_dns_no_response','external_name_not_resolved','no_ipv4_default_route','no_docker_socket','no_host_home','capabilities_empty'}
    valid={'role':'client','synthetic_only':True,'checks':{k:True for k in keys},'routes':'','resolver':'','live_generation_allowed':False,'biological_execution_allowed':False}
    m.validate_observed(valid)
    for value in (1,'true',False,None):
        bad={**valid,'checks':{**valid['checks'],'capabilities_empty':value}}
        with pytest.raises(ValueError):m.validate_observed(bad)
    bad={**valid,'checks':{}}
    with pytest.raises(ValueError):m.validate_observed(bad)

def test_udp_control_uses_fixed_synthetic_packet():
    m=load()
    assert m.DNS_PACKET.hex()=='47110100000100000000000005676174653107696e76616c69640000010001'

def test_attempt_seal_precedes_transport_and_rejects_replay(monkeypatch):
    import json,tempfile
    m=supervisor()
    with tempfile.TemporaryDirectory(prefix='unit-attempt-',dir=str(m.ROOT)) as folder:
        root=Path(folder);(root/'docker-client').mkdir()
        (root/'release.json').write_text(json.dumps({'accepted':True,'scope':'synthetic-owned-peer-only','source_sha256':{}}))
        monkeypatch.setattr(m,'ROOT',root)
        monkeypatch.setattr(m,'verify_custody',lambda release:None)
        calls=[]
        def fake(*args,**kwargs):
            calls.append(args[0]);assert(root/'attempt.json').is_file()
            raise RuntimeError('fake_no_docker_started')
        monkeypatch.setattr(m,'_run_process',fake)
        assert m.main()==1
        assert len(calls)==1
        assert(root/'attempt.json').is_file()
        (root/'result.json').unlink()
        with pytest.raises(FileExistsError):m.main()
        assert len(calls)==1

def test_dns_lookup_retry_bounds_are_fixed():
    m=supervisor();args=m.container_arguments('peer','network-id',[])
    assert args.count('--dns-option')==2
    assert args[args.index('--dns-option')+1]=='timeout:1'
    assert args[args.index('--dns-option',args.index('--dns-option')+1)+1]=='attempts:1'
