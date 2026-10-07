"""One bounded coordinator checkpoint, without restarting or retrying attempts."""
import hashlib
import json
import os
from pathlib import Path
import secrets
import sys
import time

from .contracts import canonical_json_bytes
from .evidence import _read, decode_json
from .permissions import require_protected_storage


def _fresh(path, value):
    from .supervision import write_coordinator_json
    payload=canonical_json_bytes(value)+b'\n'
    write_coordinator_json(path,value,allow_data_execution=True)
    return hashlib.sha256(payload).hexdigest()


def _verify_snapshots(directory, checkpoint):
    for name,key in (('checkpoint-execution.json','execution_snapshot_sha256'),('checkpoint-records.json','records_snapshot_sha256')):
        if hashlib.sha256(_read(directory/name,64_000_000)).hexdigest()!=checkpoint[key]:
            raise ValueError('First-attempt snapshot changed')


def wait_for_checkpoint(directory, attempt_id, *, timeout_sec=300):
    """Pause this live coordinator; a release file cannot restart a dead process."""
    if type(timeout_sec) not in (int,float) or not 0<timeout_sec<=3600:
        raise ValueError('Checkpoint timeout must be positive and at most 3600 seconds')
    directory=require_protected_storage(Path(directory))
    decision_path=directory/'checkpoint-decision.json'
    if decision_path.exists():raise ValueError('A stale checkpoint decision must not be reused')
    records=decode_json(_read(directory/'records.json',64_000_000))
    execution=decode_json(_read(directory/'execution.json',64_000_000))
    records_hash=_fresh(directory/'checkpoint-records.json',records)
    execution['aggregate_path']=str(directory/'checkpoint-records.json')
    execution['aggregate_sha256']=records_hash
    execution_hash=_fresh(directory/'checkpoint-execution.json',execution)
    checkpoint={'record_format':'devbench.coordinator-checkpoint/1','attempt_id':attempt_id,
        'plan_identity':execution['plan_identity'],'nonce':secrets.token_hex(24),'coordinator_pid':os.getpid(),
        'execution_snapshot_sha256':execution_hash,'records_snapshot_sha256':records_hash,
        'expires_monotonic':time.monotonic()+timeout_sec,'scope':'first-attempt checkpoint, not a resumable process or a new budget'}
    checkpoint_hash=_fresh(directory/'checkpoint.json',checkpoint)
    print(json.dumps({'event':'coordinator_checkpoint_ready','checkpoint':str(directory/'checkpoint.json'),
        'checkpoint_sha256':checkpoint_hash,'attempt_id':attempt_id}),file=sys.stderr,flush=True)
    while time.monotonic()<checkpoint['expires_monotonic']:
        if decision_path.exists():
            decision=decode_json(_read(decision_path,10_000))
            if (not isinstance(decision,dict) or set(decision)!={'checkpoint_sha256','decision'}
                    or decision['checkpoint_sha256']!=checkpoint_hash or decision['decision'] not in ('continue','stop')):
                raise ValueError('Checkpoint decision does not match the retained first-attempt receipt')
            _verify_snapshots(directory,checkpoint)
            return {'decision':decision['decision'],'checkpoint_sha256':checkpoint_hash,
                    'decision_sha256':hashlib.sha256(_read(decision_path,10_000)).hexdigest()}
        time.sleep(min(.1,max(0,checkpoint['expires_monotonic']-time.monotonic())))
    return {'decision':'expired','checkpoint_sha256':checkpoint_hash,'decision_sha256':None}


def release_checkpoint(directory, expected_sha256, decision):
    if decision not in ('continue','stop'):raise ValueError('Decision must be continue or stop')
    directory=require_protected_storage(Path(directory))
    raw=_read(directory/'checkpoint.json',20_000)
    if hashlib.sha256(raw).hexdigest()!=expected_sha256:
        raise ValueError('Checkpoint receipt mismatch')
    checkpoint=decode_json(raw)
    if checkpoint.get('record_format')!='devbench.coordinator-checkpoint/1':raise ValueError('Invalid checkpoint')
    if time.monotonic()>=checkpoint['expires_monotonic']:raise ValueError('Checkpoint expired; never restart or retry automatically')
    _verify_snapshots(directory,checkpoint)
    digest=_fresh(directory/'checkpoint-decision.json',{'checkpoint_sha256':expected_sha256,'decision':decision})
    return {'status':'decision_written','decision':decision,'decision_sha256':digest,
            'scope':'releases only the existing waiting coordinator; does not launch or restart a process'}
