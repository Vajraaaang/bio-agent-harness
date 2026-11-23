"""Standalone remote-only runner. Notebook observations are not platform attestation."""
from __future__ import annotations
import argparse
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import signal
import stat
import subprocess
import sys
import time
from datetime import datetime, timezone
import zipfile

SOURCE_MEMBERS = ('devbench/analysis/colab_remote_worker.py', 'qualification/verifier.py',
                  'qualification/expected_values.json')
POLICY = {'scientific_execution_tier':'colab', 'local_scientific_execution':'forbidden',
          'fallback':'forbidden', 'automatic_replay':'forbidden', 'max_runs':1,
          'network_enforcement':'not_proven'}


def canonical(value):
    def check(v):
        if v is None or type(v) in (str, bool, int): return
        if type(v) is float:
            import math
            if math.isfinite(v): return
        if type(v) is list:
            for child in v: check(child)
            return
        if type(v) is dict and all(type(k) is str for k in v):
            for child in v.values(): check(child)
            return
        raise ValueError('Invalid finite JSON')
    check(value)
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode()


def digest(value): return hashlib.sha256(canonical(value)).hexdigest()


def read(path, cap=1_000_000):
    path = Path(path)
    if not path.is_absolute() or any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError('Absolute nonsymlink path required')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        meta = os.fstat(fd)
        if not stat.S_ISREG(meta.st_mode) or meta.st_nlink != 1 or meta.st_size > cap:
            raise ValueError('Bounded regular file required')
        data = bytearray()
        while len(data) <= cap:
            chunk = os.read(fd, min(65536, cap - len(data) + 1))
            if not chunk: return bytes(data)
            data.extend(chunk)
        raise ValueError('File exceeded limit')
    finally: os.close(fd)


def json_file(path):
    def unique(pairs):
        out = {}
        for k, v in pairs:
            if k in out: raise ValueError('Duplicate JSON field')
            out[k] = v
        return out
    value = json.loads(read(path), object_pairs_hook=unique,
        parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Nonfinite JSON')))
    canonical(value)
    return value


def closed(value, keys):
    if type(value) is not dict or set(value) != set(keys): raise ValueError('Closed metadata required')


def check_ref(path, ref):
    data = read(path)
    if type(ref['size_bytes']) is not int or len(data) != ref['size_bytes'] or hashlib.sha256(data).hexdigest() != ref['sha256']:
        raise ValueError('Approved artifact changed')


def remote_guard():
    # This prevents accidental execution on a local host. An authenticated Google
    # transport observation must independently establish actual remote origin.
    try: found = importlib.util.find_spec('google.colab') is not None
    except (ModuleNotFoundError, ValueError): found = False
    if sys.platform != 'linux' or not found or not Path('/content').is_dir():
        raise PermissionError('Scientific execution requires a Google Colab runtime')


def verify_approval(payload):
    """Check exact signed local approval without opening any signed host path."""
    job, request, grant, issuer = (json_file(payload / n) for n in ('job.json','request.json','grant.json','issuer.json'))
    closed(job, ('format','job_id','run_id','step_id','method_version_id','method_contract','source',
        'environment_lock','inputs','parameters','source_bundle_sha256','validator_sha256','scope','limits','actor','correlation','policy'))
    if job['format'] != 'devbench.colab-method-job/1' or job['scope'] != 'synthetic_colab_qualification' or job['policy'] != POLICY:
        raise PermissionError('Remote-only synthetic scope required')
    if type(job['policy']['max_runs']) is not int: raise ValueError('Exact one-run integer required')
    limits = job['limits']
    closed(limits, ('cpu','memory_mb','timeout_seconds','pids','max_output_bytes','network'))
    if (limits != job['method_contract']['execution'] or limits['cpu'] != 1
            or type(limits['memory_mb']) is not int or not 128 <= limits['memory_mb'] <= 512
            or type(limits['timeout_seconds']) not in (int,float) or not 0 < limits['timeout_seconds'] <= 120
            or type(limits['pids']) is not int or not 1 <= limits['pids'] <= 16
            or type(limits['max_output_bytes']) is not int or not 1 <= limits['max_output_bytes'] <= 2_097_152
            or limits['network'] != 'allow'):
        raise PermissionError('Bounded synthetic Colab allocation required')
    method = job['method_contract']
    if (method['implementation']['kind'] != 'colab' or method['implementation']['source_sha256'] != job['source']['sha256']
            or method['implementation']['entrypoint'] != ['python3','/method/source.py']
            or method['environment']['lock_sha256'] != job['environment_lock']['sha256']
            or job['parameters'] != {} or set(method['outputs']) != {'numerical','plot'}
            or method['outputs']['numerical']['path'] != '/outputs/numerical.json'
            or method['outputs']['plot']['path'] != '/outputs/plot.svg'):
        raise PermissionError('Explicit synthetic method binding required')
    for k in ('job_id','run_id','step_id'):
        if type(job[k]) is not str or re.fullmatch('[0-9a-f]{32}', job[k]) is None: raise ValueError('Invalid job identity')
    closed(request, ('format','scope','qualification_id','job_id','job_ref','job_sha256','user_authorization_ref',
        'roles','workflow_ref','bindings','allocation','restrictions','expires_at'))
    if (request['format'] != 'devbench.colab-synthetic-qualification-request/1' or request['scope'] != job['scope']
            or request['job_id'] != job['job_id'] or request['job_sha256'] != digest(job)
            or request['allocation'] != {'limits':job['limits'],'max_runs':1}
            or request['restrictions'] != {'local_scientific_compute':False,'local_fallback':False,'model_generation':False,'airway':False}
            or any(type(x) is not bool for x in request['restrictions'].values())):
        raise PermissionError('Approved synthetic request differs')
    if type(request['allocation']['max_runs']) is not int: raise ValueError('Exact one-run integer required')
    closed(request['roles'], ('operator','reviewer'))
    if request['roles']['operator'] != job['actor'] or request['roles']['operator'] == request['roles']['reviewer']:
        raise PermissionError('Independent roles required')
    b = request['bindings']
    closed(b, ('method_version_id','step_id','source_sha256','environment_lock_sha256','validator_ref','expected_values_ref','runtime_policy_ref','source_bundle_ref'))
    if (b['method_version_id'] != job['method_version_id'] or b['step_id'] != job['step_id']
            or b['source_sha256'] != job['source']['sha256'] or b['environment_lock_sha256'] != job['environment_lock']['sha256']
            or b['validator_ref']['sha256'] != job['validator_sha256'] or b['source_bundle_ref']['sha256'] != job['source_bundle_sha256']):
        raise PermissionError('Approved job binding differs')
    expected = {'format':'devbench.rna-trial-authority-grant/1', 'issuer_id':issuer.get('issuer_id'),
        'grant_id':grant.get('grant_id'), 'trial_id':request['qualification_id'], 'purpose':'trial_execution',
        'binding_sha256':digest({'purpose':'trial_execution','request':request}), 'approver_id':request['roles']['operator'],
        'roles_sha256':digest(request['roles']), 'allocation_sha256':digest(request['allocation']),
        'engineering_sha256':digest(b), 'expires_at':request['expires_at']}
    closed(issuer, ('issuer_id','public_key_hex'))
    closed(grant, (*expected, 'signature'))
    if {k:v for k,v in grant.items() if k != 'signature'} != expected: raise PermissionError('Grant subject differs')
    expires = datetime.fromisoformat(expected['expires_at'].replace('Z','+00:00'))
    if expires.tzinfo is None or expires <= datetime.now(timezone.utc): raise PermissionError('Grant expired')
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    Ed25519PublicKey.from_public_bytes(bytes.fromhex(issuer['public_key_hex'])).verify(bytes.fromhex(grant['signature']), canonical(expected))
    for name,ref in [('job.json',request['job_ref']), ('authorization.json',request['user_authorization_ref']),
                     ('workflow.json',request['workflow_ref']), ('runtime-policy.json',b['runtime_policy_ref']),
                     ('source-bundle.zip',b['source_bundle_ref']), ('qualification/verifier.py',b['validator_ref']),
                     ('qualification/expected_values.json',b['expected_values_ref']),
                     ('method.py',job['source']), ('environment-lock.json',job['environment_lock'])]:
        check_ref(payload / name, ref)
    if json_file(payload / 'runtime-policy.json') != POLICY: raise PermissionError('Runtime policy differs')
    if set(job['inputs']) != {'fixture'}: raise PermissionError('Only the declared synthetic fixture input is allowed')
    check_ref(payload / 'inputs/fixture.json', job['inputs']['fixture'])
    with zipfile.ZipFile(io.BytesIO(read(payload / 'source-bundle.zip'))) as archive:
        infos = archive.infolist()
        if len(infos) != len(SOURCE_MEMBERS) or {i.filename for i in infos} != set(SOURCE_MEMBERS):
            raise ValueError('Minimal source inventory differs')
        for info in infos:
            if info.file_size > 1_000_000 or info.is_dir() or info.flag_bits & 1: raise ValueError('Unsafe source member')
            with archive.open(info) as stream: body = stream.read(1_000_001)
            if body != read(payload / info.filename): raise ValueError('Extracted source changed')
    lock = json_file(payload / 'environment-lock.json')
    if (lock.get('runtime') != 'google_colab' or lock.get('scope') != 'synthetic_colab_qualification_only'
            or lock.get('dependency_installation') != 'forbidden' or lock.get('scientific_dependencies') != 'python_standard_library'
            or not tuple(lock['python_minimum']) <= sys.version_info[:2] < tuple(lock['python_maximum_exclusive'])):
        raise PermissionError('Remote environment differs')
    return job


def claim_once(job):
    root = Path('/content/devbench_colab_job_claims')
    root.mkdir(mode=0o700, exist_ok=True)
    if root.is_symlink() or not root.is_dir(): raise PermissionError('Claim root is unsafe')
    path = root / job['job_id']
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(canonical({'job_sha256':digest(job),'replay_forbidden':True}))
        stream.flush(); os.fsync(stream.fileno())
    return path


def bounded_child(argv, cwd, limits, deadline):
    import resource
    def bound():
        cap = limits['memory_mb'] * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (cap, cap))
        cap = limits['max_output_bytes']
        resource.setrlimit(resource.RLIMIT_FSIZE, (cap, cap))
        seconds = max(1, int(deadline - time.monotonic()) + 1)
        resource.setrlimit(resource.RLIMIT_CPU, (seconds, seconds))
    env = {'PATH':'/usr/bin:/bin', 'LANG':'C.UTF-8', 'HOME':str(cwd),
           **{k:'1' for k in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS')}}
    remaining = deadline - time.monotonic()
    if remaining <= 0: raise TimeoutError('Approved wall budget expired')
    process = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True, preexec_fn=bound)
    try: return process.wait(timeout=max(0.001, deadline-time.monotonic()))
    except subprocess.TimeoutExpired:
        try: os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError: pass
        process.wait(timeout=5)
        raise TimeoutError('Approved wall budget expired') from None


def write_zip(path, members):
    with path.open('xb') as target:
        with zipfile.ZipFile(target, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            for name, body in sorted(members.items()):
                info = zipfile.ZipInfo(name, date_time=(1980,1,1,0,0,0))
                info.create_system = 3; info.external_attr = (stat.S_IFREG | 0o444) << 16
                info.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(info, body)


def run_payload(payload, remote_session_id):
    remote_guard()
    payload = Path(payload)
    if not payload.is_absolute() or not payload.is_relative_to('/content'):
        raise PermissionError('Payload must remain in the remote content directory')
    if type(remote_session_id) is not str or re.fullmatch('[0-9a-f]{64}', remote_session_id) is None:
        raise ValueError('Transport session binding required')
    job = verify_approval(payload)
    claim_once(job)
    work = payload.parent / (job['job_id'] + '-execution')
    work.mkdir(mode=0o700)
    deadline = time.monotonic() + job['limits']['timeout_seconds']
    stage, exit_code = 'method', None
    try:
        exit_code = bounded_child([sys.executable,'-I','-B',str(payload/'method.py'), '--input',
            str(payload/'inputs/fixture.json'),'--output-directory',str(work/'outputs')], work, job['limits'], deadline)
        if exit_code != 0: raise RuntimeError('method_failed')
        stage = 'validation'
        exit_code = bounded_child([sys.executable,'-I','-B',str(payload/'qualification/verifier.py'),
            '--input',str(payload/'inputs/fixture.json'),'--result',str(work/'outputs/numerical.json'),
            '--plot',str(work/'outputs/plot.svg'),'--expected',str(payload/'qualification/expected_values.json'),
            '--output',str(work/'validation.json'),'--job',str(payload/'job.json'),
            '--validator-sha256',job['validator_sha256']], work, job['limits'], deadline)
        if exit_code != 0: raise RuntimeError('validation_failed')
        validation = json_file(work/'validation.json')
        if validation.get('passed') is not True or validation.get('job_sha256') != digest(job) or validation.get('validator_sha256') != job['validator_sha256']:
            raise RuntimeError('validation_failed')
        stage = 'packaging'
        outputs = {}
        members = {'validation.json':read(work/'validation.json')}
        for port,spec in job['method_contract']['outputs'].items():
            name = PurePosixPath(spec['path']).name
            body = read(work/'outputs'/name, job['limits']['max_output_bytes'])
            path = 'outputs/' + name
            members[path] = body
            outputs[port] = {'path':path,'sha256':hashlib.sha256(body).hexdigest(),'size_bytes':len(body)}
        if sum(r['size_bytes'] for r in outputs.values()) > job['limits']['max_output_bytes']:
            raise RuntimeError('output_limit')
        runtime = {'format':'devbench.colab-runtime/1','job_sha256':digest(job),'backend':'google_colab',
            'remote_session_id':remote_session_id,'python_version':platform.python_version(),
            'platform':platform.platform()[:300], 'network_enforcement':'not_proven'}
        members['runtime.json'] = canonical(runtime)
        result = {'format':'devbench.colab-method-result/1','job_id':job['job_id'],'run_id':job['run_id'],
            'job_sha256':digest(job),'method_version_id':job['method_version_id'],
            'source_bundle_sha256':job['source_bundle_sha256'],'validator_sha256':job['validator_sha256'],
            'status':'succeeded','exit_code':0,'outputs':outputs,
            'validation_sha256':hashlib.sha256(members['validation.json']).hexdigest(),
            'runtime_sha256':hashlib.sha256(members['runtime.json']).hexdigest()}
        members['result.json'] = canonical(result)
        root = {'format':'devbench.colab-result-root/1','job_sha256':digest(job),
            'members':{n:{'sha256':hashlib.sha256(b).hexdigest(),'size_bytes':len(b)} for n,b in members.items()}}
        members['root-receipt.json'] = canonical(root)
        archive = work/'result.zip'
        write_zip(archive, members)
        receipt = {'format':'devbench.colab-job-return/1','job_id':job['job_id'],'job_sha256':digest(job),
            'archive_path':str(archive),'archive_sha256':hashlib.sha256(read(archive,job['limits']['max_output_bytes']+1_000_000)).hexdigest(),
            'root_sha256':digest(root),'status':'succeeded','replay_forbidden':True}
        (work/'return.json').write_bytes(canonical(receipt))
        return receipt
    except Exception as error:
        category = 'deadline' if isinstance(error, TimeoutError) else ('method_failed' if stage=='method' else 'validation_failed' if stage=='validation' else 'output_limit')
        failure = {'format':'devbench.colab-job-failure/1','job_id':job['job_id'],'job_sha256':digest(job),
            'stage':stage,'category':category,'exit_code':exit_code,'replay_forbidden':True}
        (work/'failure.json').write_bytes(canonical(failure))
        print(canonical(failure).decode(), flush=True)
        raise RuntimeError('Remote job failed; claim remains consumed') from None


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--payload',type=Path,required=True)
    parser.add_argument('--remote-session-id',required=True)
    args=parser.parse_args()
    print(canonical(run_payload(args.payload,args.remote_session_id)).decode(),flush=True)


if __name__ == '__main__': main()
