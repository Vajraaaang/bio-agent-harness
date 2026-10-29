"""Fixed auth-only CONNECT relay. Preparation is not authorization to launch.

This credential-free sidecar must be bound to the exact reviewed official login
command and inspected owned networks. It does not inspect TLS or enforce HTTP
paths, certificate identity, or shared-IP domain-fronting policy for arbitrary
clients. It never logs headers, payloads, tokens, exceptions or resolved IPs.
"""
from __future__ import annotations
import hashlib
import os
from pathlib import Path
import re
import ipaddress
import json
import select
import signal
import socket
import sys
import threading
import time

HOST,PORT,LISTEN_PORT='auth.openai.com',443,8443
AUTHORITY=HOST+':443'
WALL_SECONDS,HARD_WALL_SECONDS,IO_SECONDS=300,315,10
HEADER_MAX,HEADER_SECONDS=4096,2
CONNECTION_MAX,ACTIVE_MAX,DNS_MAX=32,4,16
CONNECTION_BYTES,TOTAL_BYTES=1048576,4194304
OK=b'HTTP/1.1 200 Connection Established\r\n\r\n'
DENY=b'HTTP/1.1 403 Forbidden\r\nConnection: close\r\nContent-Length: 0\r\n\r\n'
SPECIAL=tuple(ipaddress.IPv4Network(cidr)for cidr in (
    '0.0.0.0/8','10.0.0.0/8','100.64.0.0/10','127.0.0.0/8','169.254.0.0/16',
    '172.16.0.0/12','192.0.0.0/24','192.0.2.0/24','192.88.99.0/24','192.168.0.0/16',
    '198.18.0.0/15','198.51.100.0/24','203.0.113.0/24','224.0.0.0/4','240.0.0.0/4'))

NS=1000000000
CLOCK_FIELDS={'format','clock','boot_id_sha256','time_namespace','time_namespace_offsets_sha256','relay_start_ns','relay_deadline_ns'}

def clock_domain():
    # A daemon identity alone does not establish a monotonic clock domain.
    boot=Path('/proc/sys/kernel/random/boot_id').read_bytes()
    if re.fullmatch(rb'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\n',boot)is None:
        raise PermissionError('linux_boot_identity_required')
    namespace=os.readlink('/proc/self/ns/time')
    if re.fullmatch(r'time:\[[0-9]+\]',namespace)is None:raise PermissionError('linux_time_namespace_required')
    children_namespace=os.readlink('/proc/self/ns/time_for_children')
    if (re.fullmatch(r'time:\[[0-9]+\]',children_namespace)is None or children_namespace!=namespace):
        raise PermissionError('linux_time_namespace_offsets_binding_required')
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


def parse_connect(payload):
    expected=('CONNECT '+AUTHORITY+' HTTP/1.1\r\nHost: '+AUTHORITY+'\r\n\r\n').encode('ascii')
    if type(payload)is not bytes or len(payload)>HEADER_MAX or payload!=expected:
        raise ValueError('connect_authority_or_header_denied')
    return HOST,PORT


def read_header(conn,budget):
    payload=b''
    while not payload.endswith(b'\r\n\r\n'):
        conn.settimeout(remaining_timeout(budget,HEADER_SECONDS))
        piece=conn.recv(min(1024,HEADER_MAX+1-len(payload)))
        if not piece:raise ValueError('incomplete_header')
        payload+=piece
        if len(payload)>HEADER_MAX:raise ValueError('header_limit')
    parse_connect(payload)


def public_numeric_ipv4(value):
    if type(value)is not str:raise ValueError('numeric_ipv4_required')
    ip=ipaddress.IPv4Address(value)
    if (not ip.is_global or ip.is_private or ip.is_reserved or ip.is_multicast
        or ip.is_loopback or ip.is_link_local or ip.is_unspecified
        or any(ip in network for network in SPECIAL)):raise ValueError('dns_non_global_denied')
    return str(ip)


def resolve_public_ipv4(resolver=None):
    resolver=socket.getaddrinfo if resolver is None else resolver
    records=resolver(HOST,PORT,socket.AF_INET,socket.SOCK_STREAM,socket.IPPROTO_TCP)
    if type(records)is not list or not 1<=len(records)<=DNS_MAX:raise ValueError('dns_answer_limit')
    addresses=[]
    for record in records:
        if (type(record)is not tuple or len(record)!=5 or record[0]!=socket.AF_INET
            or record[1]!=socket.SOCK_STREAM or record[2]!=socket.IPPROTO_TCP
            or type(record[4])is not tuple or len(record[4])!=2 or record[4][1]!=PORT
            or type(record[4][0])is not str):raise ValueError('dns_shape_denied')
        numeric=public_numeric_ipv4(record[4][0])
        if numeric not in addresses:addresses.append(numeric)
    return addresses


class Budget:
    def __init__(self,deadline):
        self.deadline=deadline
        self.deadline_ns=int(deadline*NS)
        self.lock=threading.Lock()
        self.stopped=threading.Event()
        self.reason=None
        self.operator_requested=False
        self.counts={key:0 for key in ('connections_seen','active_connections','peak_active_connections',
            'admitted_tunnels','rejected_requests','dns_denied','connect_failed','io_errors',
            'connection_byte_caps','tunnel_bytes_reserved','busy_denials','internal_errors')}

    def stop(self,reason):
        if reason not in {'wall_deadline','connection_cap','total_byte_cap','operator_stop','internal_error'}:
            raise ValueError('fixed_stop_reason_required')
        with self.lock:
            if self.reason is None:self.reason=reason
        self.stopped.set()

    def admit_connection(self):
        with self.lock:
            if self.counts['connections_seen']>=CONNECTION_MAX:return False
            self.counts['connections_seen']+=1
            return True

    def start_active(self):
        with self.lock:
            if self.counts['active_connections']>=ACTIVE_MAX:return False
            self.counts['active_connections']+=1
            self.counts['peak_active_connections']=max(self.counts['peak_active_connections'],self.counts['active_connections'])
            return True

    def finish_active(self):
        with self.lock:self.counts['active_connections']-=1

    def increment(self,key):
        if key not in self.counts:raise ValueError('fixed_counter_required')
        with self.lock:self.counts[key]+=1

    def reserve_bytes(self,connection_used,count):
        if type(count)is not int or count<0:raise ValueError('invalid_byte_reservation')
        with self.lock:
            if connection_used+count>CONNECTION_BYTES:
                self.counts['connection_byte_caps']+=1
                return False
            if self.counts['tunnel_bytes_reserved']+count>TOTAL_BYTES:
                if self.reason is None:self.reason='total_byte_cap'
                self.stopped.set()
                return False
            self.counts['tunnel_bytes_reserved']+=count
            return True

    def snapshot(self):
        with self.lock:return dict(self.counts)


def remaining_timeout(budget,maximum):
    remaining=(budget.deadline_ns-time.monotonic_ns())/NS
    if remaining<=0 or budget.stopped.is_set() or budget.operator_requested:raise TimeoutError('bounded_session_ended')
    return min(maximum,remaining)


def connect_numeric(address,budget):
    # address is already validated; no resolver or environment proxy is consulted.
    address=public_numeric_ipv4(address)
    upstream=socket.socket(socket.AF_INET,socket.SOCK_STREAM)
    try:
        upstream.settimeout(remaining_timeout(budget,IO_SECONDS))
        upstream.connect((address,PORT))
        return upstream
    except BaseException:
        upstream.close()
        raise


def relay(conn,upstream,budget):
    used=0
    last_activity=time.monotonic()
    while not budget.stopped.is_set():
        timeout=remaining_timeout(budget,min(.2,IO_SECONDS))
        if time.monotonic()-last_activity>=IO_SECONDS:return
        ready,_,_=select.select([conn,upstream],[],[],timeout)
        for source in ready:
            source.settimeout(remaining_timeout(budget,IO_SECONDS))
            data=source.recv(min(4096,CONNECTION_BYTES-used+1))
            if not data:return
            if not budget.reserve_bytes(used,len(data)):return
            target=upstream if source is conn else conn
            target.settimeout(remaining_timeout(budget,IO_SECONDS))
            target.sendall(data)
            used+=len(data)
            last_activity=time.monotonic()


def reject(conn,budget):
    conn.settimeout(remaining_timeout(budget,HEADER_SECONDS))
    conn.sendall(DENY)


def handle_connection(conn,budget):
    try:
        with conn:
            conn.settimeout(remaining_timeout(budget,HEADER_SECONDS))
            try:read_header(conn,budget)
            except (OSError,ValueError):
                budget.increment('rejected_requests')
                try:reject(conn,budget)
                except OSError:pass
                return
            remaining_timeout(budget,IO_SECONDS)
            try:addresses=resolve_public_ipv4()
            except (OSError,ValueError):
                budget.increment('dns_denied')
                try:reject(conn,budget)
                except OSError:pass
                return
            try:upstream=connect_numeric(addresses[0],budget)
            except OSError:
                budget.increment('connect_failed')
                try:reject(conn,budget)
                except OSError:pass
                return
            with upstream:
                conn.settimeout(remaining_timeout(budget,HEADER_SECONDS));conn.sendall(OK)
                budget.increment('admitted_tunnels')
                relay(conn,upstream,budget)
    except OSError:budget.increment('io_errors')
    except Exception:
        budget.increment('internal_errors');budget.stop('internal_error')
    finally:budget.finish_active()


def ready_record(clock_binding):
    validate_clock_binding(clock_binding)
    return {'format':'devbench.gate1-auth-proxy/1','phase':'ready','clock_binding':clock_binding,'authority':AUTHORITY,'listen_port':LISTEN_PORT,
        'limits':{'wall_seconds':WALL_SECONDS,'hard_wrapper_seconds':HARD_WALL_SECONDS,'idle_io_seconds':IO_SECONDS,
            'header_bytes':HEADER_MAX,'header_io_seconds':HEADER_SECONDS,'connections':CONNECTION_MAX,
            'active_connections':ACTIVE_MAX,'per_connection_tunnel_bytes':CONNECTION_BYTES,
            'total_tunnel_bytes':TOTAL_BYTES,'ipv4_dns_records':DNS_MAX},
        'live_generation_allowed':False,'biological_execution_allowed':False}


def validate_ready(value):
    if type(value)is not dict or 'clock_binding'not in value:raise ValueError('bound_relay_ready_required')
    binding=validate_clock_binding(value['clock_binding'])
    expected=ready_record(binding)
    def exact(a,b):
        return type(a)is type(b)and (set(a)==set(b)and all(exact(a[k],b[k])for k in a)if type(a)is dict else a==b)
    if not exact(value,expected):raise ValueError('exact_relay_ready_required')
    return binding

def final_record(budget):
    return {'format':'devbench.gate1-auth-proxy/1','phase':'final','authority':AUTHORITY,
        'stop_reason':budget.reason or 'wall_deadline','counters':budget.snapshot(),
        'live_generation_allowed':False,'biological_execution_allowed':False}


def emit(record):
    print(json.dumps(record,sort_keys=True,separators=(',',':')),flush=True)


def serve(budget,clock_binding):
    workers=[]
    with socket.socket(socket.AF_INET,socket.SOCK_STREAM)as listener:
        listener.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
        listener.bind(('0.0.0.0',LISTEN_PORT));listener.listen(ACTIVE_MAX);listener.settimeout(.2)
        emit(ready_record(clock_binding))
        try:
            while not budget.stopped.is_set() and not budget.operator_requested and time.monotonic_ns()<budget.deadline_ns:
                if budget.snapshot()['connections_seen']>=CONNECTION_MAX:budget.stop('connection_cap');break
                try:conn,_=listener.accept()
                except socket.timeout:continue
                if not budget.admit_connection():conn.close();budget.stop('connection_cap');break
                if not budget.start_active():
                    budget.increment('busy_denials')
                    with conn:
                        try:reject(conn,budget)
                        except OSError:pass
                    continue
                worker=threading.Thread(target=handle_connection,args=(conn,budget),daemon=True)
                try:worker.start()
                except Exception:
                    budget.finish_active();conn.close();raise
                workers.append(worker)
        finally:
            if budget.operator_requested:budget.stop('operator_stop')
            elif not budget.stopped.is_set():budget.stop('wall_deadline')
            join_deadline=time.monotonic()+IO_SECONDS
            for worker in workers:worker.join(max(0,join_deadline-time.monotonic()))


def main():
    start=time.monotonic_ns()
    binding={'format':'devbench.auth-relay-clock/1','clock':'CLOCK_MONOTONIC',**clock_domain(),
        'relay_start_ns':start,'relay_deadline_ns':start+WALL_SECONDS*NS}
    budget=Budget(0)
    budget.deadline_ns=binding['relay_deadline_ns']
    budget.deadline=budget.deadline_ns/NS
    def operator_stop(signum,frame):budget.operator_requested=True
    signal.signal(signal.SIGTERM,operator_stop)
    try:
        if len(sys.argv)!=1:raise ValueError('fixed_no_argument_command_required')
        serve(budget,binding)
    except Exception:
        budget.increment('internal_errors');budget.stop('internal_error')
    emit(final_record(budget))
    return 1 if budget.reason=='internal_error'else 0

if __name__=='__main__':raise SystemExit(main())
