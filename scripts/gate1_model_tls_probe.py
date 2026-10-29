"""Credential-free fixed model-authority TLS preflight, with no HTTP payload.

Only a separately reviewed owned-network runtime may launch this helper. It
reads no client configuration/cache and never invokes Codex or a provider API.
"""
from __future__ import annotations
import ipaddress
import json
import socket
import ssl
import sys
import time

HOST='chatgpt.com'
AUTHORITY=HOST+':443'
PROXY_PORT=8443
WALL_SECONDS,IO_SECONDS=15,5
CONNECT=('CONNECT '+AUTHORITY+' HTTP/1.1\r\nHost: '+AUTHORITY+'\r\n\r\n').encode('ascii')
OK=b'HTTP/1.1 200 Connection Established\r\n\r\n'
OWNED_RANGES=tuple(ipaddress.IPv4Network(value)for value in ('10.0.0.0/8','172.16.0.0/12','192.168.0.0/16'))


def owned_proxy_ip(value):
    if type(value)is not str:raise ValueError('owned_proxy_ipv4_required')
    ip=ipaddress.IPv4Address(value)
    if not any(ip in network for network in OWNED_RANGES):raise ValueError('owned_proxy_ipv4_required')
    return str(ip)


def receipt(passed,error_category=None):
    return {'format':'devbench.gate1-model-tls-preflight/1','authority':AUTHORITY,'passed':passed,
        'proxy_connect_verified':passed,'peer_certificate_verified':passed,'tls_version_acceptable':passed,
        'application_http_sent':False,'credential_cache_accessed':False,'error_category':error_category,
        'live_generation_allowed':False,'biological_execution_allowed':False}


def perform_probe(proxy_ip,socket_factory=None,context_factory=None):
    proxy_ip=owned_proxy_ip(proxy_ip)
    context=(ssl.create_default_context if context_factory is None else context_factory)()
    if context.check_hostname is not True or context.verify_mode!=ssl.CERT_REQUIRED:
        raise PermissionError('verified_peer_certificate_required')
    deadline=time.monotonic()+WALL_SECONDS
    def bound():
        remaining=deadline-time.monotonic()
        if remaining<=0:raise TimeoutError('model_tls_preflight_deadline')
        return min(IO_SECONDS,remaining)
    factory=socket.socket if socket_factory is None else socket_factory
    with factory(socket.AF_INET,socket.SOCK_STREAM)as raw:
        raw.settimeout(bound());raw.connect((proxy_ip,PROXY_PORT))
        raw.settimeout(bound());raw.sendall(CONNECT)
        response=b''
        while not response.endswith(b'\r\n\r\n'):
            raw.settimeout(bound())
            piece=raw.recv(min(1024,4097-len(response)))
            if not piece:raise ValueError('incomplete_proxy_connect')
            response+=piece
            if len(response)>4096:raise ValueError('proxy_connect_header_limit')
        if response!=OK:raise ValueError('fixed_proxy_connect_not_accepted')
        raw.settimeout(bound())
        with context.wrap_socket(raw,server_hostname=HOST)as tls:
            if tls.version()not in {'TLSv1.2','TLSv1.3'}:raise ValueError('tls_version_not_qualified')
            # Deliberately no write to the TLS application stream.
    return receipt(True)


def main():
    try:
        if len(sys.argv)!=2:raise ValueError('one_owned_proxy_ipv4_argument_required')
        record=perform_probe(sys.argv[1])
    except ssl.SSLCertVerificationError:record=receipt(False,'peer_certificate_failed')
    except ssl.SSLError:record=receipt(False,'tls_handshake_failed')
    except PermissionError:record=receipt(False,'certificate_policy_refused')
    except ValueError:record=receipt(False,'transport_profile_refused')
    except OSError:record=receipt(False,'transport_io_failed')
    except Exception:record=receipt(False,'transport_internal_error')
    print(json.dumps(record,sort_keys=True,separators=(',',':')),flush=True)
    return 0 if record['passed']is True else 1

if __name__=='__main__':raise SystemExit(main())
