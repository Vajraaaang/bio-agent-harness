"""Closed remote-job metadata. Validation performs JSON/schema/hash work only."""
from __future__ import annotations
import hashlib
import json
import re
from devharness_core.methods import MethodContract
from devharness_core.provenance import canonical_json

JOB_FORMAT = 'devbench.colab-method-job/1'
SCOPE = 'synthetic_colab_qualification'
REQUIRED_VALIDATION_CHECKS = ('fixture_matches_predeclared_literals', 'independent_exact_normal_equations', 'numerical_result_matches_literals', 'plot_matches_input_and_fit', 'plot_contains_no_active_content', 'rejects_singular_input', 'rejects_nonfinite_input', 'rejects_mismatched_result', 'rejects_mismatched_plot', 'rejects_active_plot', 'validator_source_bound', 'job_validator_bound')
POLICY = {'scientific_execution_tier': 'colab', 'local_scientific_execution': 'forbidden',
          'fallback': 'forbidden', 'automatic_replay': 'forbidden', 'max_runs': 1,
          'network_enforcement': 'not_proven'}


def digest(value):
    return hashlib.sha256(canonical_json(value)).hexdigest()


def closed(value, keys, label):
    if type(value) is not dict or set(value) != set(keys):
        raise ValueError('Invalid closed ' + label)
    return value


def identity(value, length=64):
    if type(value) is not str or re.fullmatch('[0-9a-f]{' + str(length) + '}', value) is None:
        raise ValueError('Invalid remote identity')
    return value


def validate_job(value):
    value = json.loads(canonical_json(value))
    closed(value, ('format','job_id','run_id','step_id','method_version_id','method_contract',
           'source','environment_lock','inputs','parameters','source_bundle_sha256','validator_sha256',
           'scope','limits','actor','correlation','policy'), 'Colab job')
    if value['format'] != JOB_FORMAT or value['scope'] != SCOPE or canonical_json(value['policy']) != canonical_json(POLICY):
        raise PermissionError('Only the bounded remote-only synthetic qualification is admitted')
    for key in ('job_id','run_id','step_id'):
        identity(value[key], 32)
    for key in ('method_version_id','source_bundle_sha256','validator_sha256'):
        identity(value[key])
    method = MethodContract.from_dict(value['method_contract'])
    contract = method.to_dict()
    if contract['implementation']['kind'] != 'colab' or method.version_id != value['method_version_id']:
        raise ValueError('Remote job must bind an explicit Colab method')
    if canonical_json(method.validate_parameters(value['parameters'])) != canonical_json(value['parameters']):
        raise ValueError('Remote parameters changed')
    if contract['execution']['network'] != 'allow':
        raise PermissionError('Synthetic Colab bootstrap must honestly declare unproven network confinement')
    if canonical_json(value['limits']) != canonical_json(contract['execution']):
        raise ValueError('Remote allocation must equal the method resource profile')
    for name, expected in (('source',contract['implementation']['source_sha256']),
                           ('environment_lock',contract['environment']['lock_sha256'])):
        ref = closed(value[name], ('sha256','size_bytes'), name)
        identity(ref['sha256'])
        if ref['sha256'] != expected or type(ref['size_bytes']) is not int or not 0 <= ref['size_bytes'] <= 1_000_000:
            raise ValueError('Invalid remote source or environment binding')
    inputs = value['inputs']
    specs = contract['inputs']
    if type(inputs) is not dict or set(inputs) - set(specs) or any(s['required'] and n not in inputs for n,s in specs.items()):
        raise ValueError('Invalid remote input ports')
    for name,ref in inputs.items():
        closed(ref, ('sha256','size_bytes','artifact_type'), 'input')
        identity(ref['sha256'])
        if type(ref['size_bytes']) is not int or not 0 <= ref['size_bytes'] <= 100_000_000 or ref['artifact_type'] != specs[name]['artifact_type']:
            raise ValueError('Invalid remote input artifact')
    if type(value['actor']) is not str or not value['actor'].strip() or len(value['actor']) > 200:
        raise ValueError('Invalid remote actor')
    correlation = value['correlation']
    if correlation is not None:
        closed(correlation, ('project_id','scientific_run_id','attempt_id','analysis_version_id','dataset_version_ids'), 'correlation')
        for key in ('project_id','scientific_run_id','attempt_id','analysis_version_id'):
            if type(correlation[key]) is not str or re.fullmatch('[0-9a-f]{32,64}', correlation[key]) is None:
                raise ValueError('Invalid correlation identity')
        if type(correlation['dataset_version_ids']) is not list or not 1 <= len(correlation['dataset_version_ids']) <= 32:
            raise ValueError('Invalid correlation versions')
        for item in correlation['dataset_version_ids']:
            identity(item)
    return value


def job_sha256(value):
    return digest(validate_job(value))
