"""Build unregistered airway MethodContract candidates; never execute a method.

Run with the installed V6 prefix Python and -I -B. Resource values are operator
proposals, not user-prespecified values, allocations or approvals. A valid
MethodContract is a content identity, not permission or scientific acceptance.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import stat

from devharness_core.methods import MethodContract
import devharness_core.methods as installed_methods

WORKSPACE = Path('/Users/vajraang/Documents/ChatGPT/bio-agent-harness')
ROOT = WORKSPACE / 'reports/rna-airway-runtime/2026-10-22-v1/method-contract-preparation-v1'
IMAGE = 'sha256:7319bf61aef8c1a4816c88476b53decbc578057473ea4016dcd320e45eea5407'
API_PATH = WORKSPACE / 'r/airway-candidate-v6-01/lib/python3.12/site-packages/devharness_core/methods.py'
API_SHA256 = '363014040759e3259e0577fed86dfc11af65e216a168694e77326d636211eefe'
PINS = {
    'reader': (WORKSPACE / 'methods/airway-2015/reader-contract-v3.json', '4ededf19ae8b0cb8193353f34ca004c66610510b9712156bbeaaf37c4f0b9573'),
    'full': (WORKSPACE / 'methods/airway-2015/output-contract-v3.json', '4091a8d3e8ec300c92cf69a4216bacbc991949d91d7984495a38d914ba470e45'),
    'wrapper': (WORKSPACE / 'methods/airway-2015/wrapper-v2.R', '6b746a8061d1e81125d7b45a7654526d5d60b1ceede08e01e4f54bca9aa08b3c'),
    'lock': (WORKSPACE / 'reports/rna-airway-implementation/2026-10-22-v1/public-bridge/runtime-contracts-v3/runtime-environment-v3.lock.json', '3b2a1da6f348fe02e9d115aea2e31760862dfbd70bc91f4a033aadf5bb4ed3aa'),
    'workflow': (WORKSPACE / 'methods/airway-2015/compiled-v3/workflow-manifest.json', 'b568da47a515e9984418ce21620cddabec2de74027656911e4a62a1b46733c76'),
}
AUTHOR_SOURCE = '834e806bfb6020606378a08b75b749a967502fec0550de12724da9f6aa076d4c'
WORKFLOW = 'original-airway-2015-full-processed-v1'
SHA_SCHEMA = {'type': 'string', 'pattern': '^[0-9a-f]{64}$'}


def canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False) + '\n').encode()


def source_inputs():
    """Read only bounded public source/descriptor files, never study data."""
    result, receipts = {}, {}
    for name, (path, digest) in PINS.items():
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_size > 128 * 1024:
            raise ValueError('Nonregular or oversized public source: ' + name)
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != digest:
            raise ValueError('Pinned public source changed: ' + name)
        receipts[name] = {'path': str(path), 'sha256': digest, 'size_bytes': len(raw)}
        if name != 'wrapper':
            result[name] = json.loads(raw)
    if result['lock']['image'] != IMAGE:
        raise ValueError('Historical image changed')
    for profile in ('reader', 'full'):
        if result[profile]['runtime_binding']['image'] != IMAGE:
            raise ValueError('Profile image changed')
        if result[profile]['wrapper_sha256'] != PINS['wrapper'][1]:
            raise ValueError('Profile wrapper changed')
    return result, receipts


def closed_const_mapping(value):
    return {'type': 'object', 'additionalProperties': False,
            'properties': {key: {'const': item} for key, item in value.items()},
            'required': sorted(value)}


def build_candidates():
    if (Path(installed_methods.__file__) != API_PATH or
            hashlib.sha256(API_PATH.read_bytes()).hexdigest() != API_SHA256):
        raise ValueError('Exact installed V6 MethodContract API required')
    source, receipts = source_inputs()
    receipts['installed_method_api'] = {'path': str(API_PATH), 'sha256': API_SHA256,
                                         'size_bytes': API_PATH.stat().st_size}
    contracts = {}
    for profile, operation in (('reader', 'inspect_original_input'), ('full', 'full_analysis')):
        parameters = {
            'workflow_id': {'const': WORKFLOW},
            'workflow_sha256': {'const': PINS['workflow'][1]},
            'source_sha256': {'const': AUTHOR_SOURCE},
            'operation': {'const': operation},
            'sample_universe_sha256': dict(SHA_SCHEMA),
            'gene_universe_sha256': dict(SHA_SCHEMA),
            'required_package_versions': closed_const_mapping(source['lock']['source_package_versions']),
            'adapter_package_versions': closed_const_mapping(source['lock']['adapter_package_versions']),
        }
        if profile == 'full':
            parameters['row_ranges_sha256'] = dict(SHA_SCHEMA)
            parameters['retained_figure_expectation_ref'] = {
                'type': 'object', 'additionalProperties': False,
                'properties': {'path': {'type': 'string', 'pattern': '^/[^\\x00]+$'},
                               'sha256': dict(SHA_SCHEMA),
                               'size_bytes': {'type': 'integer', 'minimum': 1}},
                'required': ['path', 'sha256', 'size_bytes']}
        value = {
            'format': 'devharness.method/1',
            'method_id': 'original-airway-2015-' + profile + '-candidate-v1',
            'name': 'Original airway 2015 ' + profile + ' preparation candidate',
            'lifecycle': 'custom', 'implementation_trust': 'unreviewed',
            'inputs': {name: {'artifact_type': kind, 'required': True}
                       for name, kind in source[profile]['input_ports'].items()},
            'parameters': {'type': 'object', 'additionalProperties': False,
                           'properties': parameters, 'required': sorted(parameters)},
            'outputs': {name: {**spec, 'path': '/outputs/' + spec['path']}
                        for name, spec in source[profile]['output_ports'].items()},
            'implementation': {'kind': 'oci', 'source_sha256': PINS['wrapper'][1],
                               'entrypoint': source[profile]['entrypoint']},
            'environment': {'image': IMAGE, 'lock_sha256': PINS['lock'][1]},
            'execution': {'cpu': 1 if profile == 'reader' else 2,
                          'memory_mb': 2048 if profile == 'reader' else 4096,
                          'timeout_seconds': 120 if profile == 'reader' else 1800,
                          'pids': 64 if profile == 'reader' else 128,
                          'max_output_bytes': 33554432 if profile == 'reader' else 100000000,
                          'scratch_bytes': 134217728 if profile == 'reader' else 1073741824,
                          'network': 'deny'},
            'validation': {'comparator': 'json', 'invariants': [],
                           'counts_layer': 'counts', 'qc_metrics': []},
            'provenance': {'origin': 'Operator source-only preparation; resource limits are unapproved proposals. Published procedure is not a claim of best method or user-selected parameters.',
                           'citations': [str(PINS[profile][0]), str(PINS['lock'][0])]},
        }
        contracts[profile] = MethodContract.from_dict(value).to_dict()
    return contracts, receipts


def validate_candidate(profile, value):
    """Check both the installed schema and exact prepared scope, not just shape."""
    if profile not in {'reader', 'full'}:
        raise ValueError('Unknown candidate profile')
    contract = MethodContract.from_dict(value)
    expected, _ = build_candidates()
    if contract.to_dict() != expected[profile]:
        raise ValueError('Candidate differs from pinned source or proposed scope')
    return contract


def emit_new_json(path, value):
    if not path.is_absolute():
        raise ValueError('Absolute output path required')
    # Exclusive creation prevents replacing any frozen/existing output.
    with path.open('xb') as handle:
        handle.write(canonical(value))


def prepare(output_directory=ROOT / 'outputs'):
    if not output_directory.is_absolute():
        raise ValueError('Absolute preparation directory required')
    output_directory.mkdir(parents=True, exist_ok=False)
    contracts, receipts = build_candidates()
    for profile, value in contracts.items():
        validate_candidate(profile, value)
        emit_new_json(output_directory / (profile + '-method-candidate.json'), value)
        partial = {name: spec['const'] for name, spec in value['parameters']['properties'].items()
                   if isinstance(spec, dict) and 'const' in spec}
        # Package maps are exact constants in closed nested schemas.
        for name in ('required_package_versions', 'adapter_package_versions'):
            partial[name] = {key: spec['const'] for key, spec in value['parameters']['properties'][name]['properties'].items()}
        emit_new_json(output_directory / (profile + '-incomplete-parameters-template.json'), partial)
    hold = {
        'format': 'devbench.airway-method-preparation-hold/1', 'status': 'HOLD',
        'registered': False, 'execution_authorized': False,
        'original_data_read': False, 'r_executed': False, 'model_generation_authorized': False,
        'resource_parameter_attribution': 'operator_proposals_not_allocated_or_approved_not_user_prespecified',
        'source_inputs': receipts,
        'candidates': {key: {'version_id': MethodContract.from_dict(value).version_id,
                             'path': str(output_directory / (key + '-method-candidate.json'))}
                       for key, value in contracts.items()},
        'reader_prerequisites': ['Bind source-proven ordered sample and gene universe hashes; no template identity substitution',
                                  'Accept exact reader parameters, runtime, independent review, allocation and numerical execution grant',
                                  'Gate 1 and required generation qualifications remain separately required'],
        'full_prerequisites': ['Actual historical reader results independently accepted, including actual rowRanges bytes and digest',
                                'Actual retained-figure expectation accepted and frozen',
                                'Complete required parameter template with actual accepted rowRanges digest and accepted figure expectation; no full invocation packet is emitted',
                                'Independent original-data reader/renderer/scientific validation and exact numerical allocation',
                                'Matching generation and execution approvals checked at production boundary'],
        'validation_scope': 'Installed V6 contract and parameter schema validation only; JSON comparator is not independent scientific evaluation',
        'full_output_capacity_proposal': {
            'figure_expanded_bytes': 67108864, 'native_expanded_bytes': 33554432,
            'sum_archive_expanded_caps_bytes': 100663296,
            'installed_api_combined_output_max_bytes': 100000000,
            'proposed_combined_output_bytes': 100000000,
            'status': 'HOLD_pending_accepted_combined_budget',
            'limitation': 'Expanded archive caps are independent safety caps, not reservations. Their sum alone exceeds the combined output API ceiling before four TSVs, session and JSON. Compressed output sizes are unknown; the candidate cannot guarantee sufficient capacity. Actual combined outputs must fit the accepted ceiling; no ceiling increase is proposed.'},
    }
    emit_new_json(output_directory / 'HOLD.json', hold)
    return hold


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-directory', type=Path, default=ROOT / 'outputs')
    args = parser.parse_args()
    hold = prepare(args.output_directory)
    print(json.dumps({'status': hold['status'], 'outputs': str(args.output_directory),
                      'registered': False, 'execution_authorized': False}))


if __name__ == '__main__':
    main()
