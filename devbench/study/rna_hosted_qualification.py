"""Default-off synthetic qualification driver and bounded fake exporter process.

The controller never accepts, searches for or forwards a real credential. Hosted
mode is blocked until a reviewed exporter-only private channel is implemented.
The fake subprocess owns only its outbox and sanitized evidence; receipt replay
has no workflow-resume, model, retrieval or numerical execution authority.
"""
from __future__ import annotations
import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import selectors
import importlib.util
import base64
import csv
from importlib.metadata import distribution
import subprocess
import stat
import sys
import time
from uuid import uuid4

IPC_BYTES = 262144
_IPC_DEADLINE_SECONDS = 15
_IPC_CLOSE_TIMEOUT_SECONDS = 3
STATUS_KEYS = ('prepared','acknowledged','uncertain','rejected','blocked','mutation_requests','read_requests')
BOUND_POLICY = dict(format='devbench.model-budget-policy/1',kind='hard_total_tokens',max_total_tokens=1000,soft_token_target=None,max_calls=30,max_total_seconds=500,max_context_bytes=128000,memory_mb=512,max_children=2,reconciliation=dict(tokens=160,calls=8,seconds=80,context_bytes=128000))
IDENTITY_KEYS = ('source_digest','runtime_digest','policy_digest','criteria_digest',
                 'fixture_manifest_digest','live_session_digest','authorization_record_digest')


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(',',':'), ensure_ascii=False, allow_nan=False).encode()


def _sha(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _absolute(value: str | Path) -> Path:
    path=Path(value)
    if not path.is_absolute() or path.resolve()!=path or path.is_symlink():
        raise ValueError('Absolute nonsymlink path required')
    return path


def frozen_fixture_manifest() -> dict:
    builder=Path(importlib.util.find_spec('devbench.study.rna_hosted_fixtures').origin).resolve()
    return {'format':'devbench.rna-hosted-fixtures/1','data_scope':'synthetic',
        'builder_sha256':hashlib.sha256(builder.read_bytes()).hexdigest(),
        'fixtures':[{'fixture_id':stage+'-'+str(allocation),'stage':stage,'allocation':allocation,
            'asset_sha256':_sha({'stage':stage,'allocation':allocation,'version':1,
                'genes':['zeta','alpha','middle'] if stage=='post_fit_explanation' else ['g1','g2'],
                'samples':12 if stage=='post_fit_explanation' else 8})}
            for stage in ('pre_fit_methods','post_fit_explanation') for allocation in (0,1,2)]}


def installed_identity() -> dict:
    """Independent resource/RECORD identity; source checkouts are not a runtime."""
    package=Path(importlib.util.find_spec('devbench.analysis').origin).resolve().parent
    archive=package/'assets'/'source-bundle.zip'
    record=next((Path(distribution('devharness').locate_file(item)).resolve()
        for item in distribution('devharness').files or () if str(item).endswith('.dist-info/RECORD')),None)
    if not archive.is_file() or record is None or not record.is_file():
        raise ValueError('Matching ordinary installed wheel/resource identity required')
    dist=distribution('devharness')
    for row in csv.reader(record.open(newline='')):
        name,expected,size=row
        if not expected:continue
        if not expected.startswith('sha256='):raise ValueError('Unsupported runtime RECORD hash')
        path=Path(dist.locate_file(name)).resolve()
        # RECORD may name interpreter entrypoints outside package directory;
        # verify only this distribution's exact declared regular files.
        if not path.is_relative_to(Path(sys.prefix).resolve()) or not path.is_file():raise ValueError('Runtime RECORD asset outside installed prefix or missing')
        actual=base64.urlsafe_b64encode(hashlib.sha256(path.read_bytes()).digest()).decode().rstrip('=')
        if actual!=expected.split('=',1)[1] or size and path.stat().st_size!=int(size):
            raise ValueError('Runtime RECORD asset drift')
    return dict(source_digest=hashlib.sha256(archive.read_bytes()).hexdigest(),
        runtime_digest=hashlib.sha256(record.read_bytes()).hexdigest(),policy_digest=_sha(BOUND_POLICY),
        live_session_digest=None,authorization_record_digest=None,
        protected_paths=[str(Path(sys.prefix).resolve()),str(package.parent),str(record.parent)])


def prepare_fake_inputs(output:Path, *, sample_matrix:list[dict]|None=None) -> tuple[dict,dict]:
    """Prepare finite fake scope bound to the actual installed wheel identity."""
    identity=installed_identity()
    limits=dict(max_spans=64,max_bytes=262144,max_mutation_requests=1200,max_read_requests=2400,
                max_delivery_attempts=3,total_deadline_seconds=900)
    criteria=dict(format='devbench.rna-hosted-criteria/1',sample_matrix=sample_matrix or [
        dict(fixture_id='pre_fit_methods-0',stage='pre_fit_methods',allocation=0,repeat=1)],
        api_p95_upper_seconds=5,visibility_deadline_seconds=30,poll_interval_seconds=.5,
        dashboard_window_seconds=30,max_extra_model_calls=0,max_extra_fits=0,max_extra_retrievals=0,
        max_source_writes=0,max_privacy_leaks=0,required_topology_fraction=1,censored_samples_fail=True,limits=limits)
    identity.pop('protected_paths')
    manifest=dict(format='devbench.rna-hosted-qualification/1',qualification_id=str(uuid4()),mode='fake',
        data_scope='synthetic',output_path=str(_absolute(output)),endpoint=None,workspace_id=None,
        project_id=None,project_name=None,expires_at=(datetime.now(timezone.utc)+__import__('datetime').timedelta(minutes=15)).isoformat(),
        allowed_operations=['create_run','update_run','read_run'],authorize_upload=False,authorize_remote_read=False,
        criteria_digest=_sha(criteria),fixture_manifest_digest=_sha(frozen_fixture_manifest()),limits=limits,**identity)
    return manifest,criteria


def validate_inputs(manifest:dict,criteria:dict,*,protected_paths:list[str]|None=None) -> tuple[dict,dict]:
    from devbench.study.rna_hosted_trace_contract import validate_qualification,validate_criteria
    criteria=validate_criteria(criteria)
    identity=installed_identity()
    identity['criteria_digest']=_sha(criteria)
    identity['fixture_manifest_digest']=_sha(frozen_fixture_manifest())
    identity['protected_paths']+=protected_paths or []
    manifest=validate_qualification(manifest,source_identity=identity,now=datetime.now(timezone.utc))
    if manifest['mode']!='fake':raise PermissionError('Hosted exporter-only credential channel unavailable')
    approved={r['fixture_id']:(r['stage'],r['allocation']) for r in frozen_fixture_manifest()['fixtures']}
    for sample in criteria['sample_matrix']:
        if approved.get(sample['fixture_id'])!=(sample['stage'],sample['allocation']):raise ValueError('Unapproved fixture identity')
    return manifest,criteria


def _regular_bytes(path:Path) -> bytes:
    path=_absolute(path)
    descriptor=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    with os.fdopen(descriptor,'rb') as stream:
        info=os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size>IPC_BYTES:
            raise ValueError('Bounded regular JSON file required')
        raw=stream.read(IPC_BYTES+1)
    if len(raw)>IPC_BYTES:raise ValueError('Bounded JSON input required')
    return raw


def _read(path:Path) -> dict:
    value=json.loads(_regular_bytes(path))
    _canonical(value)
    if not isinstance(value,dict):raise ValueError('JSON object required')
    return value


def _read_operations(path:Path) -> list[dict]:
    value=json.loads(_regular_bytes(path));_canonical(value)
    if not isinstance(value,list):raise ValueError('Operation list required')
    return value


def _new(path: Path, value: object) -> None:
    with _absolute(path).open('xb') as stream:
        stream.write(_canonical(value)); stream.flush(); os.fsync(stream.fileno())


def minimal_environment() -> dict[str,str]:
    """Construct before process creation; never inspect parent environment."""
    return {'LANG':'C.UTF-8', 'LC_ALL':'C.UTF-8', 'PYTHONHASHSEED':'0',
            'PYTHONDONTWRITEBYTECODE':'1','LANGSMITH_TRACING':'false',
            'LANGCHAIN_TRACING_V2':'false','OPENBLAS_NUM_THREADS':'1','OMP_NUM_THREADS':'1'}


def _command() -> list[str]:
    return [str(Path(sys.executable).absolute()),'-I','-B',str(Path(__file__).resolve())]


class FakeExporterChannel:
    """No network transport or credentials; fresh env and bounded JSON pipe."""
    def __init__(self, *, root: Path, restart: bool = False) -> None:
        self.root=_absolute(root)
        if restart:
            if not self.root.is_dir():raise ValueError('Retained exporter root required')
        else:self.root.mkdir(parents=True,exist_ok=False,mode=0o700)
        self.process=subprocess.Popen(_command()+['--exporter-root',str(self.root)],
            stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,bufsize=0,
            env=minimal_environment(),cwd=self.root,close_fds=True)
        os.set_blocking(self.process.stdin.fileno(),False)
        os.set_blocking(self.process.stdout.fileno(),False)
        self._closed=False

    def __enter__(self): return self
    def __exit__(self,*args): self.close()

    def request(self,value:dict) -> dict:
        if not isinstance(value,dict): raise ValueError('Closed IPC request required')
        command=value.get('command')
        keys={'boundary':{'command'},'initialize':{'command','manifest','criteria'},
              'prepare':{'command','session','operations'},'drain':{'command'},'status':{'command'},
              'verify':{'command'},'reconcile':{'command','receipt','approval_record'},
              'fault':{'command','kind'}}
        if command not in keys or set(value)!=keys[command]: raise ValueError('Closed IPC request required')
        raw=_canonical(value)+b'\n'
        if len(raw)>IPC_BYTES: raise ValueError('IPC request byte cap exceeded')
        if self._closed: raise RuntimeError('Exporter closed')
        deadline=time.monotonic()+_IPC_DEADLINE_SECONDS;response=b'';offset=0
        with selectors.DefaultSelector() as selector:
            selector.register(self.process.stdin,selectors.EVENT_WRITE)
            while offset<len(raw):
                remaining=deadline-time.monotonic()
                if remaining<=0 or not selector.select(remaining):
                    self.close();raise TimeoutError('Exporter IPC deadline')
                try:
                    written=os.write(self.process.stdin.fileno(),raw[offset:offset+8192])
                except BlockingIOError:
                    continue
                except BrokenPipeError:
                    self.close();raise ValueError('Exporter process exited')
                if written<=0:
                    self.close();raise ValueError('Exporter pipe closed')
                offset+=written
            selector.unregister(self.process.stdin)
            selector.register(self.process.stdout,selectors.EVENT_READ)
            while not response.endswith(b'\n'):
                remaining=deadline-time.monotonic()
                if remaining<=0 or not selector.select(remaining):
                    self.close();raise TimeoutError('Exporter IPC deadline')
                try:
                    chunk=os.read(self.process.stdout.fileno(),min(8192,IPC_BYTES+1-len(response)))
                except BlockingIOError:
                    continue
                if not chunk:
                    self.close();raise ValueError('Exporter process exited')
                response+=chunk
                if len(response)>IPC_BYTES:
                    self.close();raise ValueError('Exporter IPC response cap')
        decoded=json.loads(response)
        if set(decoded)=={'error'}: raise ValueError(decoded['error'])
        return decoded

    def close(self) -> None:
        if self._closed:return
        self._closed=True
        self.process.stdin.close()
        try:self.process.wait(timeout=_IPC_CLOSE_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            self.process.kill();self.process.wait(timeout=_IPC_CLOSE_TIMEOUT_SECONDS)
        self.process.stdout.close()


def _fake_exporter(root: Path) -> int:
    """Only this process owns fake remote state and writable delivery journals."""
    manifest=criteria=None; boxes=[];remote={}; verifiers={}; mutations=reads=0;fault=None;persisted={}; started=time.monotonic()
    def status():
        counts={key:0 for key in STATUS_KEYS}
        for box,session in boxes:
            source=box.status()['counts']
            for key in STATUS_KEYS[:5]: counts[key]+=source[key]
            counts['uncertain']+=source['sending']
        counts.update(mutation_requests=mutations,read_requests=reads)
        return counts
    while True:
        raw=sys.stdin.buffer.readline(IPC_BYTES+1)
        if not raw:return 0
        try:
            if len(raw)>IPC_BYTES or not raw.endswith(b'\n'):raise ValueError('IPC byte cap')
            value=json.loads(raw); command=value['command']
            if command=='boundary':
                # Values below inspect only this newly constructed child env.
                result={'pid':os.getpid(),'credential_present':bool(os.environ.get('DEVBENCH_FAKE_EXPORTER_CANARY')),
                        'tracing_enabled':os.environ.get('LANGSMITH_TRACING')=='true',
                        'inherited_context':any(k in os.environ for k in ('LANGSMITH_PARENT','LANGCHAIN_SESSION'))}
            elif command=='initialize':
                if manifest is not None:raise ValueError('Exporter already initialized')
                manifest,criteria=value['manifest'],value['criteria']
                campaign_path=root/'campaign.json'
                if campaign_path.exists():
                    retained=_read(campaign_path)
                    if retained['manifest']!=manifest or retained['criteria']!=criteria:raise ValueError('Retained campaign scope drift')
                    if retained['started_monotonic_ns']>time.monotonic_ns():raise ValueError('Campaign monotonic clock changed')
                    started=retained['started_monotonic_ns']/1e9
                else:
                    _new(campaign_path,{'manifest':manifest,'criteria':criteria,'started_monotonic_ns':int(started*1e9)})
                reads=len(list(root.glob('read-*.json')))
                os.environ['DEVBENCH_FAKE_EXPORTER_CANARY']='fake-only-'+os.urandom(32).hex()
                if manifest['mode']!='fake':raise PermissionError('Fake process cannot send hosted requests')
                from devbench.study.langsmith_outbox import ExportOutbox
                for directory in sorted(root.glob('sample-*')):
                    if (directory/'session.json').exists() and (directory/'outbox'/'journal.sqlite').exists():
                        boxes.append((ExportOutbox.open(directory/'outbox'/'journal.sqlite'),_read(directory/'session.json')))
                        persisted.update(_read(directory/'persistence.json'))
                for path in sorted(root.glob('mutation-*.json'),key=lambda item:int(item.stem.split('-')[-1])):
                    record=_read(path);mutations+=1
                    if 'payload' in record:remote[record['payload']['id']]=record['payload']
                result=status()
            elif manifest is None: raise ValueError('Exporter uninitialized')
            elif datetime.fromisoformat(manifest['expires_at'].replace('Z','+00:00'))<=datetime.now(timezone.utc):
                raise ValueError('Qualification session expired')
            elif time.monotonic()-started>manifest['limits']['total_deadline_seconds']:
                raise ValueError('Qualification total deadline exceeded')
            elif command=='prepare':
                duplicate=False
                for directory in sorted(root.glob('sample-*')):
                    if (directory/'session.json').is_file() and _read(directory/'session.json')==value['session']:
                        if _read_operations(directory/'operations.json')!=value['operations']:raise ValueError('Retained session content drift')
                        duplicate=True
                if duplicate:
                    sys.stdout.buffer.write(_canonical(status())+b'\n');sys.stdout.buffer.flush();continue
                if fault=='before_persist':os._exit(70)
                limits={key:min(manifest['limits'][key],criteria['limits'][key]) for key in manifest['limits']}
                from devbench.study.langsmith_outbox import ExportOutbox
                from devbench.study.rna_langsmith_live import validate_live_session,validate_live_run
                from devbench.study.rna_langsmith_verify import validate_remote_proof
                session=validate_live_session(value['session']); operations=value['operations']
                if len(operations)>manifest['limits']['max_spans']*8:raise ValueError('Operation cap')
                if not isinstance(operations,list):raise ValueError('Closed operation list required')
                envelope_keys={'operation_id','span_id','revision','operation','event_digest','redaction_policy_digest','source_binding_alias','payload_sha256'}
                for item in operations:
                    if not isinstance(item,dict) or set(item)!={'operation','payload'} or set(item['operation'])!=envelope_keys:raise ValueError('Closed operation required')
                    envelope,payload=item['operation'],item['payload']
                    validate_live_run(payload,envelope['operation']);proof=validate_remote_proof(payload)
                    if envelope['payload_sha256']!=_sha(payload) or proof['operation_id']!=envelope['operation_id'] or proof['revision']!=envelope['revision']:raise ValueError('Immutable operation proof differs')
                    if payload['session_id']!=session['project_id'] or payload['session_name']!=session['project'] or envelope['source_binding_alias']!=session['workflow_alias'] or envelope['redaction_policy_digest']!=session['policy_digest']:raise ValueError('Operation session differs')
                    if any(payload['extra']['metadata'].get(key)!=session[key] for key in ('source_digest','runtime_digest')):raise ValueError('Operation source differs')
                starts=[row for row in operations if row['operation']['operation']=='start']
                if not starts:raise ValueError('Actual retained starts required')
                if sum(len(b.status()['spans']) for b,_ in boxes)+len(starts)>limits['max_spans']:raise ValueError('Campaign span cap')
                if sum(item['bytes'] for item in persisted.values())+len(_canonical(operations))>limits['max_bytes']:raise ValueError('Campaign byte cap')
                directory=root/('sample-'+str(len(boxes)));directory.mkdir()
                payload={'record_format':'devbench.rna-langsmith-export/1','privacy_policy':'rna-allowlist/1',
                         'data_scope':'synthetic','runs':[row['payload'] for row in starts]}
                payload_path=directory/'payload'/'approved.json';payload_path.parent.mkdir()
                _new(payload_path,payload)
                stamp=time.monotonic_ns()  # Conservative boundary before first durable outbox commit.
                box=ExportOutbox.create_live(directory/'outbox'/'journal.sqlite',payload_path=payload_path,
                    expected_sha256=_sha(payload),authorized_session_manifest=session,initial_operations=starts)
                for row in operations:
                    box.enqueue_operation(row['operation'],row['payload'],max_spans=manifest['limits']['max_spans'],
                                          max_bytes=manifest['limits']['max_bytes'])
                for item in operations:persisted[item['operation']['operation_id']]={'monotonic_ns':stamp,'bytes':len(_canonical(item['payload']))}
                _new(directory/'persistence.json',persisted)
                _new(directory/'operations.json',operations)
                _new(directory/'session.json',session)
                boxes.append((box,session))
                if fault in ('after_persist_before_send','post_persist_before_send'):os._exit(71)
                result=status()
            elif command=='fault':
                if value['kind'] not in ('accept_then_timeout','ack_then_timeout','before_persist','after_persist_before_send','post_persist_before_send','after_response_before_ack',None):raise ValueError('Unknown fake fault')
                fault=value['kind'];result=status()
            elif command=='drain':
                def transport(**kwargs):
                    nonlocal mutations,fault
                    if datetime.fromisoformat(manifest['expires_at'].replace('Z','+00:00'))<=datetime.now(timezone.utc) or time.monotonic()-started>min(manifest['limits']['total_deadline_seconds'],criteria['limits']['total_deadline_seconds']):raise ValueError('Delivery expired')
                    operation='create_run' if kwargs['method']=='POST' else 'update_run'
                    if operation not in manifest['allowed_operations']:raise PermissionError('Mutation not authorized')
                    if mutations>=min(manifest['limits']['max_mutation_requests'],criteria['limits']['max_mutation_requests']):raise ValueError('Mutation cap reached')
                    mutations+=1;payload=deepcopy(kwargs['payload']);remote[payload['id']]=payload
                    _new(root/('mutation-'+str(mutations)+'.json'),{'method':kwargs['method'],'span_id':payload['id'],'payload':payload,'persisted_monotonic_ns':time.monotonic_ns()})
                    if fault=='after_response_before_ack':os._exit(72)
                    if fault:
                        fault=None;raise TimeoutError('Simulated accepted response loss')
                    return {'status_code':200}
                for box,session in boxes:
                    box.deliver(transport=transport,allow_upload=True,timeout_seconds=1,deadline_seconds=5,max_operations=100)
                result=status()
            elif command=='status':result=status()
            elif command=='verify':
                from devbench.study.rna_langsmith_verify import RemoteRunVerifier
                pending=[]
                for box,session in boxes:
                    with box._db() as db:
                        for row in db.execute("SELECT * FROM operations WHERE state='uncertain'"):
                            pending.append((box,session,dict(row)))
                if len(pending)!=1:
                    raise ValueError('Explicit sole uncertain operation selection required')
                box,session,row=pending[0]
                def read_transport(**kwargs):
                    nonlocal reads
                    if datetime.fromisoformat(manifest['expires_at'].replace('Z','+00:00'))<=datetime.now(timezone.utc) or time.monotonic()-started>min(manifest['limits']['total_deadline_seconds'],criteria['limits']['total_deadline_seconds']):raise ValueError('Read session expired')
                    if reads>=min(manifest['limits']['max_read_requests'],criteria['limits']['max_read_requests']):raise ValueError('Read cap reached')
                    reads+=1;identity=kwargs['path'].split('/')[-1]
                    _new(root/('read-'+str(reads)+'.json'),{'method':kwargs['method'],'span_id':identity,'observed_monotonic_ns':time.monotonic_ns()})
                    body=deepcopy(remote.get(identity))
                    if body is not None:body.update(session_id=session['project_id'],tenant_id=session['workspace_id'])
                    return {'status_code':200 if body else 404,'body':body or {}}
                verifier=RemoteRunVerifier(manifest=manifest,criteria=criteria,api_key=None,read_transport=read_transport)
                verifier.bind_session(session)
                envelope=json.loads(row['envelope']);payload=json.loads(row['payload'])
                result=verifier.observe(operation=envelope,expected_payload=payload,persisted_monotonic_ns=persisted[envelope['operation_id']]['monotonic_ns'])
                verifiers[envelope['operation_id']]=(box,verifier)
                _new(root/('observation-'+str(reads)+'.json'),result)
            elif command=='reconcile':
                receipt=value['receipt'];record=value['approval_record']
                if set(record)!={'format','receipt_digest','approved','reviewer_alias'}:raise ValueError('Closed operator review required')
                path=root/('review-'+str(uuid4())+'.json');_new(path,record)
                if receipt.get('reviewer_approval_digest')!=_sha(record):raise ValueError('Review digest differs')
                box,verifier=verifiers[receipt['operation_id']]
                box.reconcile_reviewed(receipt,approval_record=path,verifier=verifier)
                result=status()
            else:raise ValueError('Unknown command')
            response=result
        except Exception as error:
            # Closed type-only boundary; errors can contain private provider data.
            response={'error':'Qualification operation rejected: '+type(error).__name__}
        sys.stdout.buffer.write(_canonical(response)+b'\n');sys.stdout.buffer.flush()


def _session(case:dict,manifest:dict) -> dict:
    observer=case['observer'];events=observer.read_events(case['identity'])
    roots=[e for e in events if e['parent_span_id'] is None]
    root=roots[0];capability=case['backend'].capabilities()
    if root['bindings']['policy']!=manifest['policy_digest']:raise ValueError('Observed policy differs from frozen budget policy')
    return dict(format='devbench.rna-live-trace-session/2',workflow_alias=root['trace_id'],
        workflow_binding_digest=root['bindings']['workflow'],data_scope='synthetic',
        source_digest=capability['source_digest'],runtime_digest=capability['runtime_digest'],
        policy_digest=root['bindings']['policy'],endpoint='https://api.smith.langchain.com',
        project='fake-qualification',project_id=str(uuid4()),workspace_id=str(uuid4()),
        redaction_version='rna-allowlist/1',event_schema='devbench.rna-trace-event/1',
        allowed_event_fields=['event_id','trace_id','span_id','parent_span_id','sequence','attempt','role','phase','kind',
            'source_status','dispatch_status','capture_status','clock','metrics','reason_code','origin'],
        max_spans=manifest['limits']['max_spans'],max_bytes=manifest['limits']['max_bytes'],
        max_delivery_attempts=manifest['limits']['max_delivery_attempts'],expires_at=manifest['expires_at'],authorize_upload=True)


def _science_manifest(root:Path) -> dict:
    files={}
    for path in sorted(root.rglob('*')):
        if path.is_symlink():raise ValueError('Unexpected synthetic source symlink')
        if path.is_file():
            if path.stat().st_size>16_000_000 or len(files)>=2000:raise ValueError('Bounded scientific fixture manifest required')
            files[str(path)]=hashlib.sha256(path.read_bytes()).hexdigest()
    return files


def _fixture_worker(root:Path,sample:dict,manifest:dict) -> dict:
    from devbench.study.rna_hosted_fixtures import run_fixture,scientific_accounting
    from devbench.study.rna_langsmith_live import ReceiptOperationBridge
    from devbench.study.rna_langsmith_verify import bind_remote_proof
    off=run_fixture(root=root/'off',stage=sample['stage'],allocation=sample['allocation'],trace=False,software_identity=manifest)
    on=run_fixture(root=root/'on',stage=sample['stage'],allocation=sample['allocation'],trace=True,software_identity=manifest)
    before=scientific_accounting(on)
    science_before=_science_manifest(on['root'])
    if scientific_accounting(off)!=before:raise ValueError('Off/on scientific execution differs')
    session=_session(on,manifest)
    bridge=ReceiptOperationBridge(authorized_session_manifest=session,output_path=root/'sanitized.jsonl',
        capability={'backend_kind':'offline_recorded','qualification_status':'offline_qualified','admitted':True},
        allocation=sample['allocation'],actual_children=len(on['backend'].child_contexts))
    operations=bridge.emit_new(observer=on['observer'],workflow_id=on['identity'])
    for row in operations:
        row['payload']=bind_remote_proof(row['payload'],row['operation'])
        row['operation']['payload_sha256']=_sha(row['payload'])
    if scientific_accounting(on)!=before:raise ValueError('Export projection altered accounting')
    science_after=_science_manifest(on['root'])
    if science_before!=science_after:raise ValueError('Export projection altered scientific files')
    _new(root/'source-integrity.json',{'before':science_before,'after':science_after})
    _new(root/'accounting.json',{'off':scientific_accounting(off),'on':before})
    events=on['observer'].read_events(on['identity'])
    result={'session':session,'operations':operations,'accounting':before,'event_count':len(events),
            'phases':sorted({e['phase'] for e in events}),'process_pid':os.getpid(),
            'native_qualified':False,'scientific_reviewed':False,
            'source_integrity':{'before_sha256':_sha(science_before),'after_sha256':_sha(science_after),'unchanged':True},
            'process_environment':{'credential_present':any(k in os.environ for k in ('LANGSMITH_API_KEY','OPENAI_API_KEY','DEVBENCH_FAKE_EXPORTER_CANARY')),
                'tracing_enabled':os.environ.get('LANGSMITH_TRACING')=='true',
                'inherited_context':any(k in os.environ for k in ('LANGSMITH_PARENT','LANGCHAIN_SESSION'))}}
    _new(root/'fixture-result.json',result)
    return result


def isolated_fixture_factory(*,root:Path,sample:dict,manifest:dict,timeout_seconds:float|None=None) -> dict:
    root=_absolute(root);root.mkdir(parents=True,exist_ok=False)
    request=root/'request.json';_new(request,{'sample':sample,'manifest':manifest})
    process=subprocess.run(_command()+['--fixture-root',str(root),'--fixture-request',str(request)],
        env=minimal_environment(),cwd=root,close_fds=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
        timeout=min(300,timeout_seconds if timeout_seconds is not None else manifest['limits']['total_deadline_seconds']))
    if process.returncode:raise RuntimeError('Bounded fixture process failed; retained attempt preserved')
    return _read(root/'fixture-result.json')


class QualificationDriver:
    def __init__(self,*,manifest:dict,criteria:dict,exporter_channel:object,fixture_factory:object) -> None:
        self.manifest,self.criteria=validate_inputs(manifest,criteria)
        self.channel,self.factory=exporter_channel,fixture_factory
        self.output=_absolute(manifest['output_path'])
        self.output.mkdir(parents=True,exist_ok=False,mode=0o700)
        _new(self.output/'manifest.json',manifest);_new(self.output/'criteria.json',criteria)
        self.channel.request({'command':'initialize','manifest':manifest,'criteria':criteria})
        self.fixtures=[];self.prepared=False;self.started=time.monotonic()

    def _remaining(self) -> float:
        expiry=datetime.fromisoformat(self.manifest['expires_at'].replace('Z','+00:00'))
        cap=min(self.manifest['limits']['total_deadline_seconds'],self.criteria['limits']['total_deadline_seconds'])
        remaining=min(cap-(time.monotonic()-self.started),(expiry-datetime.now(timezone.utc)).total_seconds())
        if remaining<=0:raise PermissionError('Qualification expired before fixture or delivery')
        return remaining

    def drain(self) -> dict:
        self._remaining()
        if not self.prepared:
            for index,sample in enumerate(self.criteria['sample_matrix']):
                remaining=self._remaining()
                retained=self.output/('fixture-summary-'+str(index)+'.json')
                if retained.exists():
                    result=_read(retained)
                else:
                    root=self.output/('fixture-'+str(index))
                    if root.exists():raise PermissionError('Interrupted fixture preserved; numerical reexecution blocked')
                    result=self.factory(root=root,sample=sample,manifest=self.manifest,timeout_seconds=remaining)
                    _new(retained,result)
                self.channel.request({'command':'prepare','session':result['session'],'operations':result['operations']})
                summary={key:result[key] for key in ('accounting','event_count','phases','process_pid')}
                if summary not in self.fixtures:self.fixtures.append(summary)
            self.prepared=True
        self._remaining()
        self.channel.request({'command':'drain'})
        return self.status()

    def status(self) -> dict:
        value=self.channel.request({'command':'status'})
        if set(value)!=set(STATUS_KEYS) or any(type(x)is not int or x<0 for x in value.values()):raise ValueError('Closed exporter status required')
        return value

    def verify_remote(self) -> dict:
        return self.channel.request({'command':'verify'})

    def reconcile_reviewed(self,receipt:dict) -> dict:
        # An operator file must already exist; the driver never approves a read.
        path=self.output/'reviews'/(receipt['reviewer_approval_digest']+'.json')
        record=_read(path)
        return self.channel.request({'command':'reconcile','receipt':receipt,'approval_record':record})


def main(argv:list[str]|None=None) -> int:
    parser=argparse.ArgumentParser(description='Default-off bounded synthetic tracing qualification')
    parser.add_argument('--mode',choices=('fake','hosted'),default='fake')
    parser.add_argument('--manifest');parser.add_argument('--criteria');parser.add_argument('--output')
    parser.add_argument('--allow-upload',action='store_true')
    parser.add_argument('--exporter-root');parser.add_argument('--fixture-root');parser.add_argument('--fixture-request')
    args=parser.parse_args(argv)
    if args.exporter_root:return _fake_exporter(_absolute(args.exporter_root))
    if args.fixture_root:
        value=_read(_absolute(args.fixture_request));_fixture_worker(_absolute(args.fixture_root),value['sample'],value['manifest']);return 0
    if not all((args.manifest,args.criteria,args.output)):parser.error('--manifest, --criteria and --output required')
    manifest_path,criteria_path,output=map(_absolute,(args.manifest,args.criteria,args.output))
    manifest,criteria=_read(manifest_path),_read(criteria_path)
    manifest,criteria=validate_inputs(manifest,criteria,protected_paths=[str(manifest_path),str(criteria_path)])
    if manifest['mode']!=args.mode or manifest['output_path']!=str(output):raise ValueError('CLI manifest binding mismatch')
    if args.mode=='hosted':
        if not args.allow_upload:raise PermissionError('Hosted mutations require --allow-upload')
        raise PermissionError('Hosted exporter-only credential channel unavailable')
    # Exporter root is disjoint from scientific fixture/source evidence.
    with FakeExporterChannel(root=output.parent/(output.name+'-exporter')) as channel:
        driver=QualificationDriver(manifest=manifest,criteria=criteria,exporter_channel=channel,fixture_factory=isolated_fixture_factory)
        state=driver.drain()
        passed=not any(state[key] for key in ('prepared','uncertain','rejected','blocked'))
        from devbench.study.rna_hosted_trace_contract import write_result
        dimensions=('software','http_acceptance','remote_content','api_visibility','dashboard','native_budget','native_isolation','scientific_review')
        statuses={key:{'status':('passed' if passed else 'failed') if key=='software' else 'unverified',
            'evidence_refs':['manifest.json','criteria.json'] if key=='software' else [],
            'limitations':['Fake transport only; no hosted service, native worker or independent scientific qualification']} for key in dimensions}
        write_result(output,status=statuses,evidence={'exporter_status':state,'fixtures':driver.fixtures,'cost':None})
    return 0 if passed else 1


if __name__=='__main__':
    raise SystemExit(main())
