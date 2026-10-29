"""One-job synthetic remote qualification subject; no scientific computation."""
from __future__ import annotations
from copy import deepcopy
from datetime import datetime, timezone
import re
from devharness_core.provenance import canonical_json
from devbench.study.evidence import decode_json
from .checkpoints import digest
from .rna_scientific_trial_contract import _instant, bounded_source
from .rna_approval_setup import _name, _public_material
from .colab_execution_contract import validate_job, POLICY

FORMAT = 'devbench.colab-synthetic-qualification-request/1'
SCOPE = 'synthetic_colab_qualification'
_RESTRICTIONS = {'local_scientific_compute':False,'local_fallback':False,'model_generation':False,'airway':False}
_FIELDS = {'format','scope','qualification_id','job_id','job_ref','job_sha256','user_authorization_ref','roles',
           'workflow_ref','bindings','allocation','restrictions','expires_at'}
_BINDINGS = {'method_version_id','step_id','source_sha256','environment_lock_sha256','validator_ref',
             'expected_values_ref','runtime_policy_ref','source_bundle_ref'}


def _closed(value, fields, label):
    if type(value) is not dict or set(value) != fields:
        raise ValueError('Use the closed Colab ' + label)
    return value


def _hex(value, length=64):
    if type(value) is not str or re.fullmatch('[0-9a-f]{' + str(length) + '}', value) is None:
        raise ValueError('Use an exact lowercase Colab identity')
    return value


def _source(ref):
    _closed(ref, {'path','sha256','size_bytes'}, 'source reference')
    if type(ref['path']) is not str or not ref['path'].startswith('/') or len(ref['path']) > 4096:
        raise ValueError('Use an absolute bounded Colab source path')
    _hex(ref['sha256'])
    if type(ref['size_bytes']) is not int or not 0 <= ref['size_bytes'] <= 1_000_000:
        raise ValueError('Use a bounded exact Colab source size')
    return ref


def validate_colab_qualification_request(request, *, workspace=None, job=None, now=None):
    """Validate closed metadata; optional custody checks read/hash opaque bytes only."""
    import json
    request = json.loads(canonical_json(request))
    _public_material(request)
    _closed(request, _FIELDS, 'qualification request')
    if request['format'] != FORMAT or request['scope'] != SCOPE:
        raise PermissionError('Only the explicit one-job synthetic Colab bootstrap is permitted')
    for key in ('qualification_id','job_id'): _hex(request[key],32)
    _hex(request['job_sha256'])
    _closed(request['roles'], {'operator','reviewer'}, 'roles')
    for role in request['roles'].values(): _name(role)
    if request['roles']['operator'] == request['roles']['reviewer']:
        raise PermissionError('Independent qualification reviewer must be distinct')
    _closed(request['bindings'], _BINDINGS, 'qualification bindings')
    bindings = request['bindings']
    for key in ('method_version_id','source_sha256','environment_lock_sha256'): _hex(bindings[key])
    _hex(bindings['step_id'],32)
    refs = [request[key] for key in ('job_ref','user_authorization_ref','workflow_ref')]
    refs += [bindings[key] for key in ('validator_ref','expected_values_ref','runtime_policy_ref','source_bundle_ref')]
    for ref in refs: _source(ref)
    if type(request['restrictions']) is not dict or set(request['restrictions']) != set(_RESTRICTIONS) or any(
            type(value) is not bool or value is not False for value in request['restrictions'].values()):
        raise PermissionError('Local science, fallback, model generation and airway remain prohibited')
    _closed(request['allocation'], {'limits','max_runs'}, 'one-run allocation')
    if type(request['allocation']['max_runs']) is not int or request['allocation']['max_runs'] != 1:
        raise PermissionError('Qualification authorizes exactly one preventive reservation')
    if type(request['allocation']['limits']) is not dict:
        raise ValueError('Use exact remote limits')
    _name(request['expires_at'])
    current = now if now is not None else datetime.now(timezone.utc)
    if not isinstance(current, datetime) or current.tzinfo is None or _instant(request['expires_at']) <= current:
        raise PermissionError('Colab qualification approval expired')
    if job is not None:
        job = validate_job(job)
        if (request['job_id'] != job['job_id'] or request['job_sha256'] != digest(job)
                or bindings['step_id'] != job['step_id'] or bindings['method_version_id'] != job['method_version_id']
                or bindings['source_sha256'] != job['source']['sha256']
                or bindings['environment_lock_sha256'] != job['environment_lock']['sha256']
                or bindings['validator_ref']['sha256'] != job['validator_sha256']
                or bindings['source_bundle_ref']['sha256'] != job['source_bundle_sha256']
                or canonical_json(request['allocation']['limits']) != canonical_json(job['limits']) or request['roles']['operator'] != job['actor']):
            raise PermissionError('Colab qualification differs from its exact frozen job')
    if workspace is not None:
        for ref in refs: bounded_source(ref, workspace)
        retained_job = decode_json(bounded_source(request['job_ref'], workspace))
        if job is None: job = validate_job(retained_job)
        if retained_job != job or digest(retained_job) != request['job_sha256']:
            raise PermissionError('Retained job custody differs')
        # This is schema verification, not a claim of remote runtime enforcement.
        policy = decode_json(bounded_source(bindings['runtime_policy_ref'], workspace))
        if canonical_json(policy) != canonical_json(POLICY):
            raise PermissionError('The explicit remote-only one-run policy changed')
        validate_colab_qualification_request(request, job=job, now=current)
    return deepcopy(request)


def colab_qualification_binding(request):
    return digest({'purpose':'trial_execution','request':request})


def unsigned_colab_qualification_grant_subject(request, *, issuer_id, grant_id, now=None):
    request = validate_colab_qualification_request(request, now=now)
    return {'format':'devbench.rna-trial-authority-grant/1','issuer_id':_name(issuer_id),'grant_id':_name(grant_id),
        'trial_id':request['qualification_id'],'purpose':'trial_execution','binding_sha256':colab_qualification_binding(request),
        'approver_id':request['roles']['operator'],'roles_sha256':digest(request['roles']),
        'allocation_sha256':digest(request['allocation']),'engineering_sha256':digest(request['bindings']),
        'expires_at':request['expires_at']}
