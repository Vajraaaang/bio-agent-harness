"""Bounded Colab result custody import; never computes scientific expectations."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path, PurePosixPath
import stat
import zipfile
from devharness_core.provenance import canonical_json, hash_artifact
from .colab_execution_contract import closed, digest, identity, job_sha256, validate_job, REQUIRED_VALIDATION_CHECKS


def _json(body):
    def unique(pairs):
        value={}
        for key,item in pairs:
            if key in value: raise ValueError('Duplicate remote JSON field')
            value[key]=item
        return value
    value=json.loads(body, object_pairs_hook=unique,
        parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Nonfinite remote JSON')))
    canonical_json(value)
    return value


def validate_transport_projection(value,job,stage):
    # Authenticated observation provenance is checked by MethodService before
    # calling this structural checker. Notebook self-report is insufficient.
    closed(value,('format','job_id','job_sha256','source_bundle_sha256','backend',
        'notebook_id','remote_session_id','dispatch_id','stage','result_archive_sha256','expected_root_sha256'), 'transport projection')
    if value['format']!='devbench.colab-transport-receipt/1' or value['backend']!='google_colab' or value['stage']!=stage:
        raise ValueError('Invalid remote transport stage')
    if value['job_id']!=job['job_id'] or value['job_sha256']!=job_sha256(job) or value['source_bundle_sha256']!=job['source_bundle_sha256']:
        raise ValueError('Remote transport subject mismatch')
    if value['notebook_id'] is not None:
        raise ValueError('CLI transport cannot claim notebook document identity')
    identity(value['remote_session_id'])
    identity(value['dispatch_id'],32)
    if stage=='returned':
        identity(value['result_archive_sha256']); identity(value['expected_root_sha256'])
    elif value['result_archive_sha256'] is not None or value['expected_root_sha256'] is not None:
        raise ValueError('Premature result hash')
    return value


def verify_result_archive(job, archive, expected_root, *, destination,
                          transport_projection, dispatch_projection):
    job=validate_job(job); identity(expected_root)
    archive=Path(archive); destination=Path(destination)
    if not archive.is_absolute() or not destination.is_absolute(): raise ValueError('Absolute paths required')
    for path in (archive,*archive.parents,destination,*destination.parents):
        if path.is_symlink(): raise ValueError('Remote custody path cannot use symlinks')
    metadata=archive.stat()
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink!=1: raise ValueError('Regular unlinked archive required')
    # Qualification outputs are small. The cap covers payload and structural
    # evidence, including ZIP overhead; all extraction is bounded again below.
    cap=min(job['limits']['max_output_bytes']+1_000_000, 101_000_000)
    if metadata.st_size>cap: raise ValueError('Remote archive exceeds byte bound')
    returned=validate_transport_projection(transport_projection,job,'returned')
    dispatched=validate_transport_projection(dispatch_projection,job,'dispatched')
    if returned['expected_root_sha256'] != expected_root:
        raise ValueError('Authenticated remote result root mismatch')
    for key in ('notebook_id','remote_session_id','dispatch_id'):
        if returned[key]!=dispatched[key]: raise ValueError('Remote transport session changed')
    archive_hash=hash_artifact(archive,max_bytes=cap)['sha256']
    if returned['result_archive_sha256']!=archive_hash: raise ValueError('Remote transport archive hash mismatch')
    blobs={};total=0
    with zipfile.ZipFile(archive) as bundle:
        members=bundle.infolist()
        if not 4<=len(members)<=32: raise ValueError('Remote archive member bound')
        for info in members:
            name=info.filename;path=PurePosixPath(name)
            mode=(info.external_attr>>16)&0xffff
            if (name in blobs or path.is_absolute() or '..' in path.parts or str(path)!=name or
                    len(path.parts)>2 or info.is_dir() or info.flag_bits&1 or
                    (mode and not stat.S_ISREG(mode)) or info.file_size>cap or info.compress_size>cap):
                raise ValueError('Unsafe remote archive member')
            total+=info.file_size
            if total>cap: raise ValueError('Remote archive cumulative byte bound')
            with bundle.open(info) as stream:
                body=stream.read(info.file_size+1)
            if len(body)!=info.file_size: raise ValueError('Remote member size mismatch')
            blobs[name]=body
    root=_json(blobs.get('root-receipt.json',b''))
    closed(root,('format','job_sha256','members'),'remote result root')
    if root['format']!='devbench.colab-result-root/1' or root['job_sha256']!=job_sha256(job) or digest(root)!=expected_root:
        raise ValueError('Remote result root mismatch')
    if type(root['members']) is not dict or set(root['members'])!=set(blobs)-{'root-receipt.json'}:
        raise ValueError('Remote result inventory mismatch')
    for name,ref in root['members'].items():
        closed(ref,('sha256','size_bytes'),'remote member')
        if type(ref['size_bytes']) is not int or ref['size_bytes']!=len(blobs[name]) or ref['sha256']!=hashlib.sha256(blobs[name]).hexdigest():
            raise ValueError('Remote member hash mismatch')
    result=_json(blobs['result.json']);validation=_json(blobs['validation.json']);runtime=_json(blobs['runtime.json'])
    closed(result,('format','job_id','run_id','job_sha256','method_version_id','source_bundle_sha256',
        'validator_sha256','status','exit_code','outputs','validation_sha256','runtime_sha256'),'remote result')
    if result['format']!='devbench.colab-method-result/1' or result['status']!='succeeded' or type(result['exit_code']) is not int or result['exit_code']!=0:
        raise ValueError('Remote computation did not complete successfully')
    for key in ('job_id','run_id','method_version_id','source_bundle_sha256','validator_sha256'):
        if result[key]!=job[key]: raise ValueError('Remote result binding mismatch')
    if result['job_sha256']!=job_sha256(job): raise ValueError('Remote result job mismatch')
    for key in ('validation','runtime'):
        if result[key+'_sha256']!=hashlib.sha256(blobs[key+'.json']).hexdigest(): raise ValueError('Remote evidence hash mismatch')
    closed(validation,('format','job_sha256','validator_sha256','passed','checks','numerical_execution_tier'), 'remote validation')
    if (validation['format']!='devbench.colab-remote-validation/1' or validation['job_sha256']!=job_sha256(job) or
            validation['validator_sha256']!=job['validator_sha256'] or validation['passed'] is not True or
            validation['numerical_execution_tier']!='colab' or type(validation['checks']) is not dict or set(validation['checks']) != set(REQUIRED_VALIDATION_CHECKS) or
            any(type(v) is not bool or v is not True for v in validation['checks'].values())):
        raise ValueError('Remote independent validation failed')
    closed(runtime,('format','job_sha256','backend','remote_session_id','python_version','platform','network_enforcement'), 'remote runtime')
    if (runtime['format']!='devbench.colab-runtime/1' or runtime['job_sha256']!=job_sha256(job) or runtime['backend']!='google_colab' or
            runtime['remote_session_id']!=returned['remote_session_id'] or runtime['network_enforcement']!='not_proven' or
            any(type(runtime[k]) is not str or not 1<=len(runtime[k])<=300 for k in ('python_version','platform'))):
        raise ValueError('Remote runtime evidence mismatch')
    outputs=result['outputs'];specs=job['method_contract']['outputs']
    if type(outputs) is not dict or not outputs or set(outputs)-set(specs) or any(v['required'] and k not in outputs for k,v in specs.items()):
        raise ValueError('Remote output ports mismatch')
    names=set();output_size=0
    for port,ref in outputs.items():
        closed(ref,('path','sha256','size_bytes'),'remote output')
        expected='outputs/'+PurePosixPath(specs[port]['path']).name
        if ref['path']!=expected or expected not in blobs or root['members'][expected]!={k:ref[k] for k in ('sha256','size_bytes')}:
            raise ValueError('Remote output identity mismatch')
        output_size+=ref['size_bytes'];names.add(expected)
    if output_size>job['limits']['max_output_bytes'] or set(blobs)!={'root-receipt.json','result.json','validation.json','runtime.json',*names}:
        raise ValueError('Remote output budget or inventory mismatch')
    if hash_artifact(archive,max_bytes=cap)['sha256'] != archive_hash:
        raise ValueError('Remote archive changed during verification')
    if destination.exists(): raise FileExistsError('Remote return destination must be fresh')
    destination.mkdir(mode=0o700)
    for name,body in blobs.items():
        path=destination/name
        path.parent.mkdir(mode=0o700,exist_ok=True)
        with path.open('xb') as stream:stream.write(body)
        path.chmod(0o444)
    return {'outputs':{port:destination/ref['path'] for port,ref in outputs.items()},
            'validation':validation,'execution':result,
            'evidence_paths':[destination/name for name in ('root-receipt.json','result.json','validation.json','runtime.json')]}
