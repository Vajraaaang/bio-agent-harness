"""Minimal approved Colab job packaging. No scientific code is imported here."""
from __future__ import annotations
import base64
import hashlib
import importlib.util
import inspect
import io
import json
from pathlib import Path
import stat
import zipfile

from devharness_core.provenance import canonical_json
from .colab_execution_contract import validate_job, job_sha256

SOURCE_MEMBERS = ('devbench/analysis/colab_remote_worker.py', 'qualification/verifier.py',
                  'qualification/expected_values.json')
PAYLOAD_MEMBERS = (*SOURCE_MEMBERS, 'source-bundle.zip', 'method.py', 'environment-lock.json',
                   'inputs/fixture.json', 'job.json', 'request.json', 'grant.json', 'issuer.json',
                   'authorization.json', 'workflow.json', 'runtime-policy.json')


def _builder():
    from . import colab_bundle_helpers
    return colab_bundle_helpers


def _read(path, cap=1_000_000):
    path = Path(path)
    if not path.is_absolute() or any(part.is_symlink() for part in (path, *path.parents)):
        raise ValueError('Absolute nonsymlink artifact path required')
    return _builder()._read_source_file(path, cap)


def _zip(members):
    total = sum(len(v) for v in members.values())
    if total > 8_000_000 or len(members) > 32: raise ValueError('Minimal job package exceeded limit')
    stream=io.BytesIO()
    with zipfile.ZipFile(stream,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=9) as archive:
        for name,body in sorted(members.items()):
            entry=zipfile.ZipInfo(name,date_time=(1980,1,1,0,0,0))
            entry.create_system=3;entry.external_attr=(stat.S_IFREG|0o444)<<16
            entry.compress_type=zipfile.ZIP_DEFLATED
            archive.writestr(entry,body)
    payload=stream.getvalue()
    if len(payload)>8_000_000: raise ValueError('Compressed job exceeded limit')
    return payload


def build_method_source_bundle(source_root, destination=None):
    """Freeze only the runner, independent verifier and declared expectation file."""
    source_root=Path(source_root)
    if not source_root.is_absolute(): raise ValueError('Absolute source root required')
    members={name:_read(source_root/name) for name in SOURCE_MEMBERS}
    payload=_zip(members)
    receipt={'sha256':hashlib.sha256(payload).hexdigest(),'size_bytes':len(payload),
             'files':list(SOURCE_MEMBERS)}
    if destination is not None:
        destination=Path(destination)
        if not destination.is_absolute() or any(p.is_symlink() for p in (destination,*destination.parents)):
            raise ValueError('Absolute nonsymlink bundle destination required')
        with destination.open('xb') as stream: stream.write(payload)
        destination.chmod(0o444)
        receipt['path']=str(destination)
    else: receipt['payload']=payload
    return receipt


def _match(data, ref):
    if type(ref.get('size_bytes')) is not int or len(data)!=ref['size_bytes'] or hashlib.sha256(data).hexdigest()!=ref['sha256']:
        raise ValueError('Frozen package artifact identity mismatch')
    return data


def _execution_script(sha256, job):
    """Actual CLI bootstrap; imports no scientific source in the notebook kernel."""
    builder=_builder()
    helper_source='\n'.join(inspect.getsource(getattr(builder,n)) for n in ('_read_source_file','extract_source_bundle'))
    bootstrap = """# No dependency installs, Codex login, model calls or local fallback.
import base64, hashlib, io, json, os, stat, subprocess, sys, zipfile
import importlib.util
from pathlib import Path, PurePosixPath
try:
    IS_COLAB = importlib.util.find_spec('google.colab') is not None
except (ModuleNotFoundError, ValueError):
    IS_COLAB = False
if sys.platform != 'linux' or not IS_COLAB or not Path('/content').is_dir():
    raise PermissionError('This scientific job is remote-only; run it in Google Colab')
"""
    bootstrap += 'ALLOWED_MEMBERS = '+repr(PAYLOAD_MEMBERS)+'\n'
    bootstrap += 'def allowed_bundle_path(name):\n    return type(name) is str and name in ALLOWED_MEMBERS\n'
    bootstrap += helper_source+'\n'
    bootstrap += 'PAYLOAD_SHA256 = '+repr(sha256)+'\n'
    bootstrap += "PAYLOAD_BYTES = _read_source_file(Path('/content/colab-source.zip'), 8_000_000)\n"
    bootstrap += "if hashlib.sha256(PAYLOAD_BYTES).hexdigest() != PAYLOAD_SHA256: raise ValueError('Approved outer payload hash differs')\n"
    bootstrap += "PAYLOAD = extract_source_bundle(base64.b64encode(PAYLOAD_BYTES).decode('ascii'), Path('/content/devbench_colab_jobs') / "+repr(job['job_id'])+", PAYLOAD_SHA256)\n"
    bootstrap += """def unique(pairs):
    out={}
    for key,value in pairs:
        if key in out: raise ValueError('Duplicate transport context field')
        out[key]=value
    return out
context=json.loads(_read_source_file(Path('/content/transport-context.json'), 512), object_pairs_hook=unique)
if type(context) is not dict or set(context) != {'remote_session_id'}:
    raise PermissionError('Exact authenticated transport context required')
REMOTE_SESSION_ID=context['remote_session_id']
if type(REMOTE_SESSION_ID) is not str or len(REMOTE_SESSION_ID) != 64 or any(v not in '0123456789abcdef' for v in REMOTE_SESSION_ID):
    raise PermissionError('An authenticated transport session binding is required')
command=[sys.executable,'-I','-B',str(PAYLOAD/'devbench/analysis/colab_remote_worker.py'),
         '--payload',str(PAYLOAD),'--remote-session-id',REMOTE_SESSION_ID]
execution=subprocess.run(command,check=True,timeout=140,stdin=subprocess.DEVNULL,
                         stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True)
receipt=json.loads(execution.stdout,object_pairs_hook=unique)
if receipt.get('format')!='devbench.colab-job-return/1' or receipt.get('status')!='succeeded':
    raise RuntimeError('Remote result did not succeed')
result_path=Path(receipt['archive_path'])
"""
    bootstrap += "if result_path != Path('/content/devbench_colab_jobs') / "+repr(job['job_id']+'-execution')+" / 'result.zip': raise ValueError('Remote result path differs')\n"
    bootstrap += """result_bytes=_read_source_file(result_path,3_097_152)
if hashlib.sha256(result_bytes).hexdigest()!=receipt['archive_sha256']:
    raise ValueError('Remote result archive hash differs')
with Path('/content/colab-result.zip').open('xb') as target:
    target.write(result_bytes)
print(json.dumps(receipt,sort_keys=True,separators=(',',':')))
"""
    return bootstrap


def _notebook(execution, sha256, job):
    markdown = ('# One approved synthetic Colab integration job\n\n'
        'The authenticated transport uploads colab-source.zip and transport-context.json. '
        'This notebook reuses the reviewed bounded source extractor and executes the same CLI bootstrap. '
        'All numerical work and independent plot validation run in Colab. No airway data, dependency installs, '
        'Codex authentication, model generation, local computation, fallback or replay is permitted. '
        'A durable remote claim prevents Run All replay in this VM; local authority and transport prevent redispatch. '
        'Runtime observations are not platform attestation. CPU allocation, process-count isolation, '
        'network denial and whole-VM confinement are not proven. This Python fixture does not qualify historical airway R.')
    return {'nbformat':4,'nbformat_minor':5,'metadata':{
        'kernelspec':{'display_name':'Python 3','language':'python','name':'python3'},
        'devbench_colab_job':{'job_id':job['job_id'],'job_sha256':job_sha256(job),'payload_sha256':sha256,
            'scientific_execution_tier':'colab','local_fallback':'forbidden','accepted':False}},
        'cells':[{'id':'colab-method-00','cell_type':'markdown','metadata':{},'source':markdown.splitlines(keepends=True)},
                 {'id':'colab-method-01','cell_type':'code','metadata':{},'execution_count':None,'outputs':[],
                  'source':execution.splitlines(keepends=True)}]}


def export_method_job(job, destination, *, source, environment_lock, inputs, request, grant, issuer):
    """Build one immutable package after the exact authority has reserved its job.

    The final payload/notebook hashes are separate from the preapproval source
    bundle hash, avoiding a grant/archive identity cycle.
    """
    from .colab_qualification_contract import (validate_colab_qualification_request,
        unsigned_colab_qualification_grant_subject)
    job=validate_job(job)
    request=validate_colab_qualification_request(request,job=job)
    bundle_ref=request['bindings']['source_bundle_ref']
    bundle_payload=_match(_read(bundle_ref['path']),bundle_ref)
    bundle={'payload':bundle_payload,'sha256':hashlib.sha256(bundle_payload).hexdigest()}
    if bundle['sha256']!=job['source_bundle_sha256']:raise ValueError('Reviewed minimal source bundle changed')
    if set(inputs)!={'fixture'}:raise ValueError('Only the declared synthetic fixture input is allowed')
    if type(issuer) is not dict or set(issuer)!={'issuer_id','public_key_hex'}:raise ValueError('Exact public issuer required')
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    expected=unsigned_colab_qualification_grant_subject(request,issuer_id=issuer['issuer_id'],grant_id=grant.get('grant_id'))
    if type(grant) is not dict or set(grant)!={*expected,'signature'} or {k:v for k,v in grant.items() if k!='signature'}!=expected:
        raise PermissionError('Frozen signed execution grant required')
    Ed25519PublicKey.from_public_bytes(bytes.fromhex(issuer['public_key_hex'])).verify(bytes.fromhex(grant['signature']),canonical_json(expected))
    # All public approval fields are frozen verbatim. Host references are never
    # interpreted as remote paths; their bytes receive fixed package names.
    with zipfile.ZipFile(io.BytesIO(bundle['payload'])) as archive:
        infos=archive.infolist()
        if len(infos)!=len(SOURCE_MEMBERS) or {info.filename for info in infos}!=set(SOURCE_MEMBERS):
            raise ValueError('Reviewed minimal source inventory changed')
        members={}
        for info in infos:
            mode=info.external_attr>>16
            if info.is_dir() or info.flag_bits&1 or info.file_size>1_000_000 or (stat.S_IFMT(mode) not in (0,stat.S_IFREG)):
                raise ValueError('Unsafe reviewed source member')
            with archive.open(info) as stream:body=stream.read(1_000_001)
            if len(body)!=info.file_size or len(body)>1_000_000:raise ValueError('Reviewed source member exceeded its bound')
            members[info.filename]=body
    members.update({'source-bundle.zip':bundle['payload'], 'method.py':_match(_read(source),job['source']),
        'environment-lock.json':_match(_read(environment_lock),job['environment_lock']),
        'inputs/fixture.json':_match(_read(inputs['fixture']),job['inputs']['fixture']),
        'job.json':canonical_json(job),'request.json':canonical_json(request),
        'grant.json':canonical_json(grant),'issuer.json':canonical_json(issuer)})
    for name,ref in [('job.json',request['job_ref']),('authorization.json',request['user_authorization_ref']),
                     ('workflow.json',request['workflow_ref']),('runtime-policy.json',request['bindings']['runtime_policy_ref'])]:
        body=_match(_read(ref['path']),ref)
        if name=='job.json' and body!=members[name]:raise ValueError('Signed job bytes must be canonical')
        members[name]=body
    for name,ref in [('source-bundle.zip',request['bindings']['source_bundle_ref']),
                     ('qualification/verifier.py',request['bindings']['validator_ref']),
                     ('qualification/expected_values.json',request['bindings']['expected_values_ref'])]:_match(members[name],ref)
    if set(members)!=set(PAYLOAD_MEMBERS):raise ValueError('Minimal payload inventory differs')
    payload=_zip(members);payload_hash=hashlib.sha256(payload).hexdigest()
    execution=_execution_script(payload_hash,job).encode('utf-8')
    notebook=canonical_json(_notebook(execution.decode('utf-8'),payload_hash,job))
    destination=Path(destination)
    if not destination.is_absolute() or any(p.is_symlink() for p in (destination,*destination.parents)):
        raise ValueError('Absolute nonsymlink export destination required')
    destination.mkdir(mode=0o700)
    for name,body in (('job-payload.zip',payload),('job.py',execution),('job.ipynb',notebook)):
        with (destination/name).open('xb') as stream:stream.write(body)
        (destination/name).chmod(0o444)
    def ref(name,body):
        return {'path':str(destination/name),'sha256':hashlib.sha256(body).hexdigest(),'size_bytes':len(body)}
    return {'format':'devbench.colab-method-export/1','job_id':job['job_id'],'job_sha256':job_sha256(job),
        'source_bundle_sha256':bundle['sha256'],'payload':ref('job-payload.zip',payload),
        'execution_file':ref('job.py',execution),'notebook':ref('job.ipynb',notebook),
        'remote_payload_name':'colab-source.zip','remote_result_name':'colab-result.zip',
        'scientific_execution_tier':'colab','local_fallback':'forbidden','automatic_replay':'forbidden'}
