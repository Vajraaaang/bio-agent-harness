"""Fixed model-host transport v2; only two source-backed CONNECT templates.

A separate process loads an exact immutable bounded relay implementation and
fixes its authority. It does not modify the accepted auth-only source or accept
any host/role argument. Public execution requires a distinct reviewed runtime.
"""
from __future__ import annotations
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

SHARED_SOURCE=Path('/input/auth_proxy.py')
SHARED_SHA256='511373e52f11e1aefe80df1d1deac375949accb2afca0a54edea24b47a4c7f89'
MODEL_HOST='chatgpt.com'
MODEL_AUTHORITY=MODEL_HOST+':443'
MODEL_FORMAT='devbench.gate1-model-proxy/2'


def verify_shared_source():
    path=SHARED_SOURCE
    if (not path.is_absolute() or path.is_symlink() or path.resolve()!=path
        or not path.is_file() or path.stat().st_size>65536
        or hashlib.sha256(path.read_bytes()).hexdigest()!=SHARED_SHA256):
        raise PermissionError('model_relay_source_binding_failed')


def model_receipt(record):
    if (type(record)is not dict or record.get('format')!='devbench.gate1-auth-proxy/1'
        or record.get('authority')!=MODEL_AUTHORITY or record.get('phase')not in {'ready','final'}
        or record.get('live_generation_allowed')is not False or record.get('biological_execution_allowed')is not False):
        raise PermissionError('model_relay_receipt_binding_failed')
    return {**record,'format':MODEL_FORMAT,'transport_role':'model-host-only'}


def parse_model_connect(payload):
    prefix=('CONNECT '+MODEL_AUTHORITY+' HTTP/1.1\r\nHost: '+MODEL_AUTHORITY+'\r\n').encode('ascii')
    known=(prefix+b'\r\n',prefix+b'Proxy-Connection: Keep-Alive\r\n\r\n')
    if type(payload)is not bytes or len(payload)>4096 or payload not in known:
        raise ValueError('model_connect_authority_or_header_denied')
    return MODEL_HOST,443


def load_relay():
    verify_shared_source()
    spec=importlib.util.spec_from_file_location('gate1_fixed_model_relay_impl',SHARED_SOURCE)
    relay=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(relay)
    relay.HOST=MODEL_HOST
    relay.AUTHORITY=MODEL_AUTHORITY
    relay.parse_connect=parse_model_connect
    base_ready,base_final=relay.ready_record,relay.final_record
    relay.ready_record=lambda:model_receipt(base_ready())
    relay.final_record=lambda budget:model_receipt(base_final(budget))
    return relay


def main():
    try:
        if len(sys.argv)!=1:raise PermissionError('fixed_no_argument_role_required')
        return load_relay().main()
    except Exception:
        print(json.dumps({'format':MODEL_FORMAT,'phase':'refused','authority':MODEL_AUTHORITY,
            'transport_role':'model-host-only','error_category':'model_relay_binding_failed',
            'live_generation_allowed':False,'biological_execution_allowed':False},sort_keys=True),flush=True)
        return 1

if __name__=='__main__':raise SystemExit(main())
