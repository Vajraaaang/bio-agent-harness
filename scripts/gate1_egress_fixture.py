"""Synthetic-only, credential-free bounded CONNECT relay and owned peer checks.

No public-domain mode, DNS upstream resolution, model client or authentication.
The private peer IP is selected by a separately reviewed Docker supervisor.
"""
from __future__ import annotations
import ipaddress
import json
import os
from pathlib import Path
import select
import socket
import struct
import sys
import time

HOST = 'gate1-approved.invalid'
AUTHORITY = HOST + ':443'
HEADER_MAX, BYTE_MAX = 4096, 32768
IO_SECONDS, SESSION_SECONDS, WALL_SECONDS, CONNECTION_MAX = 2, 5, 20, 12
OK = b'HTTP/1.1 200 Connection Established\r\n\r\n'
DENY = b'HTTP/1.1 403 Forbidden\r\nConnection: close\r\nContent-Length: 0\r\n\r\n'
MARK = b'gate1-owned-synthetic-peer-v1'
DNS_PACKET=struct.pack('!6H',0x4711,0x0100,1,0,0,0)+b'\x05gate1\x07invalid\x00'+struct.pack('!2H',1,1)

def parse_connect(payload):
    expected = ('CONNECT ' + AUTHORITY + ' HTTP/1.1\r\nHost: ' + AUTHORITY + '\r\n\r\n').encode('ascii')
    if not isinstance(payload, bytes) or len(payload)>HEADER_MAX or payload != expected:
        raise ValueError('connect_not_exact_synthetic_authority')
    return HOST, 443

def validate_peer(value):
    ip = ipaddress.ip_address(value)
    if (ip.version != 4 or not ip.is_private or ip.is_loopback or ip.is_link_local
            or ip.is_unspecified or ip.is_multicast or ip.is_reserved):
        raise ValueError('owned_private_ipv4_only')
    return str(ip)

def read_header(conn):
    data=b''
    while not data.endswith(b'\r\n\r\n'):
        piece=conn.recv(min(1024, HEADER_MAX+1-len(data)))
        if not piece: raise ValueError('incomplete_header')
        data+=piece
        if len(data)>HEADER_MAX: raise ValueError('oversized_header')
    parse_connect(data)

def relay(conn, peer, deadline):
    total=0
    while time.monotonic()<deadline:
        ready,_,_=select.select([conn,peer],[],[],min(.2,max(0,deadline-time.monotonic())))
        for source in ready:
            data=source.recv(min(4096,BYTE_MAX-total+1))
            if not data: return total
            if total+len(data)>BYTE_MAX: raise ValueError('tunnel_bytes_exceeded')
            target=peer if source is conn else conn
            target.sendall(data)
            total+=len(data)
    raise TimeoutError('tunnel_wall_exceeded')

def server_socket(port):
    sock=socket.socket(socket.AF_INET,socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
    sock.bind(('0.0.0.0',port));sock.listen(4);sock.settimeout(.2)
    return sock

def proxy(peer_ip):
    peer_ip=validate_peer(peer_ip)
    with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as udp:
        udp.settimeout(IO_SECONDS);udp.sendto(DNS_PACKET,(peer_ip,9553))
        response,_=udp.recvfrom(512)
        if response!=DNS_PACKET:raise ValueError('owned_udp_positive_control_failed')
    counters={'accepted':0,'rejected':0,'bounded_errors':0,'tunnel_bytes':0}
    end=time.monotonic()+WALL_SECONDS
    with server_socket(8443) as listener:
        while sum(counters[k] for k in ('accepted','rejected','bounded_errors'))<CONNECTION_MAX and time.monotonic()<end:
            try: conn,_=listener.accept()
            except socket.timeout: continue
            with conn:
                conn.settimeout(IO_SECONDS)
                try: read_header(conn)
                except (ValueError,OSError):
                    counters['rejected']+=1
                    try: conn.sendall(DENY)
                    except OSError: pass
                    continue
                try:
                    with socket.create_connection((peer_ip,9443),timeout=IO_SECONDS) as upstream:
                        conn.sendall(OK)
                        counters['accepted']+=1
                        counters['tunnel_bytes']+=relay(conn,upstream,min(end,time.monotonic()+SESSION_SECONDS))
                except (OSError,ValueError): counters['bounded_errors']+=1
    print(json.dumps({'role':'proxy','synthetic_only':True,**counters}),flush=True)

def peer():
    end=time.monotonic()+WALL_SECONDS
    with server_socket(9443) as listener, socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as udp:
        udp.bind(('0.0.0.0',9553));udp.settimeout(IO_SECONDS)
        while time.monotonic()<end:
            ready,_,_=select.select([listener,udp],[],[],.2)
            for incoming in ready:
                if incoming is udp:
                    data,address=udp.recvfrom(512)
                    if data==DNS_PACKET:udp.sendto(DNS_PACKET,address)
                    continue
                conn,_=listener.accept()
                with conn:
                    conn.settimeout(IO_SECONDS)
                    try:
                        data=conn.recv(len(MARK)+1)
                        if data==MARK:conn.sendall(MARK)
                    except OSError:pass
    print(json.dumps({'role':'peer','synthetic_only':True}),flush=True)

def transact(proxy_ip,payload,allowed=False):
    with socket.create_connection((proxy_ip,8443),timeout=IO_SECONDS) as conn:
        conn.settimeout(IO_SECONDS);conn.sendall(payload)
        response=conn.recv(256)
        if allowed:
            if response!=OK: return False
            conn.sendall(MARK)
            return conn.recv(256)==MARK
        return response==DENY

def client(proxy_ip,peer_ip):
    proxy_ip,peer_ip=validate_peer(proxy_ip),validate_peer(peer_ip)
    end=time.monotonic()+2
    while True:
        try:
            with socket.create_connection((proxy_ip,8443),timeout=.2): pass
            break
        except OSError:
            if time.monotonic()>=end: raise
            time.sleep(.05)
    good=('CONNECT '+AUTHORITY+' HTTP/1.1\r\nHost: '+AUTHORITY+'\r\n\r\n').encode()
    checks={'allowlisted_mock_connect_echo':transact(proxy_ip,good,True)}
    for key,payload in {
        'disallowed_host_denied':good.replace(HOST.encode(),b'gate1-denied.invalid'),
        'disallowed_port_denied':good.replace(b':443',b':80'),
        'non_connect_denied':good.replace(b'CONNECT ',b'GET '),
        'auth_header_denied':good[:-2]+b'Authorization: synthetic-only\r\n\r\n',
        'metadata_host_denied':good.replace(HOST.encode(),b'169.254.169.254'),
    }.items(): checks[key]=transact(proxy_ip,payload)
    try:
        with socket.create_connection((peer_ip,9443),timeout=.5): checks['direct_peer_tcp_denied']=False
    except OSError: checks['direct_peer_tcp_denied']=True
    # Bounded synthetic DNS payload directed only at owned peer, never a public resolver.
    packet=DNS_PACKET
    with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as udp:
        udp.settimeout(.5)
        try:
            udp.sendto(packet,(peer_ip,9553));udp.recvfrom(512);checks['direct_peer_dns_no_response']=False
        except OSError: checks['direct_peer_dns_no_response']=True
    try: socket.getaddrinfo('gate1-no-external-resolution.invalid',443);checks['external_name_not_resolved']=False
    except socket.gaierror: checks['external_name_not_resolved']=True
    routes=Path('/proc/net/route').read_text()
    checks['no_ipv4_default_route']=not any(line.split()[1]=='00000000' for line in routes.splitlines()[1:])
    checks['no_docker_socket']=not Path('/var/run/docker.sock').exists()
    checks['no_host_home']=not Path('/Users/vajraang').exists()
    checks['capabilities_empty']=any(line.strip()=='CapEff:\t0000000000000000' for line in Path('/proc/self/status').read_text().splitlines())
    print(json.dumps({'role':'client','synthetic_only':True,'checks':checks,'routes':routes,'resolver':Path('/etc/resolv.conf').read_text(),'live_generation_allowed':False,'biological_execution_allowed':False}),flush=True)
    return 0 if all(checks.values()) else 1

def main():
    role=sys.argv[1]
    if role=='peer' and len(sys.argv)==2: peer();return 0
    if role=='proxy' and len(sys.argv)==3: proxy(sys.argv[2]);return 0
    if role=='client' and len(sys.argv)==4:
        time.sleep(4)  # Supervisor inspects actual running topology before output.
        return client(sys.argv[2],sys.argv[3])
    raise ValueError('fixed_role_required')

if __name__=='__main__': raise SystemExit(main())
