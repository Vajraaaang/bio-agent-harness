"""P4 preparation and replay integration; small public synthetic records only."""
from copy import deepcopy
import base64
import hashlib
import io
import json
from pathlib import Path
import sys
import zipfile

import pytest

from devbench.analysis import workbench
from devbench.analysis.checkpoints import digest, sha256
from tests.test_rna_numerical_equivalence import contract, _pair, _compare, rows, _bundle, _json


def preparation(tmp_path, monkeypatch):
    """Use a tiny source archive: no full changing repository capture."""
    from scripts import build_colab_notebook as builder
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        archive.writestr('uv.lock', 'synthetic lock\n')
    content = buffer.getvalue()
    source_bundle = {'base64': base64.b64encode(content).decode(),
                     'sha256': hashlib.sha256(content).hexdigest()}
    monkeypatch.setattr(builder, 'default_source_bundle', lambda project: source_bundle)
    def extract(encoded, output, expected, verify_only=False):
        if not verify_only:
            output.mkdir()
            (output / 'uv.lock').write_text('synthetic lock\n')
    monkeypatch.setattr(builder, 'extract_source_bundle', extract)
    from devbench.analysis import runtime
    observed = {'runtime': {'python': sys.version, 'packages': {'numpy': 'synthetic-1'},
                            'all_package_versions': {'numpy': 'synthetic-1'}}}
    monkeypatch.setattr(runtime, 'observe_runtime', lambda tier: deepcopy(observed))
    data = tmp_path / 'input.txt'
    data.write_text('small synthetic input\n')
    parameters = workbench.default_parameters(data_sha256=sha256(data))
    return data, parameters, observed


def test_preparation_binds_contract_to_parameters_and_checkpoint_before_results(tmp_path, monkeypatch):
    source, parameters, _ = preparation(tmp_path, monkeypatch)
    declared = contract()
    root = tmp_path / 'prepared'
    result = workbench.prepare_bundle(source, root, parameters, equivalence_contract=declared)
    assert result['numerical_equivalence_contract_sha256'] == digest(declared)
    frozen = json.loads((root / 'checkpoint-identity.json').read_text())
    assert frozen['numerical_equivalence_contract_sha256'] == digest(declared)
    assert frozen['parameters'] == result
    assert json.loads((root / declared['declaration']['artifact']).read_text()) == declared
    assert not (root / 'statistics.csv').exists()
    assert workbench._frozen_configuration(root)[0] == result
    assert workbench.prepare_bundle(source, root, parameters, equivalence_contract=declared) == result
    changed = deepcopy(declared)
    changed['results'][0]['numeric']['padj']['atol'] = 0.001
    with pytest.raises(ValueError, match='contract|resume'):
        workbench.prepare_bundle(source, root, parameters, equivalence_contract=changed)
    _json(root / declared['declaration']['artifact'], changed)
    with pytest.raises(ValueError, match='contract'):
        workbench._frozen_configuration(root)


def test_optional_contract_leaves_legacy_parameter_and_identity_shape_unchanged(tmp_path, monkeypatch):
    source, parameters, _ = preparation(tmp_path, monkeypatch)
    original = deepcopy(parameters)
    root = tmp_path / 'prepared'
    prepared = workbench.prepare_bundle(source, root, parameters)
    assert parameters == original
    assert prepared == {**original, 'source_sha256': sha256(root / 'source.zip')}
    identity = json.loads((root / 'checkpoint-identity.json').read_text())
    assert set(identity) == {'parameters', 'data_sha256', 'source_sha256', 'packages', 'python_minor'}
    assert not (root / 'numerical-equivalence-contract.json').exists()
    assert workbench._frozen_configuration(root)[0] == prepared


@pytest.mark.parametrize('drift', ['python', 'packages', 'all_package_versions'])
def test_runtime_compatibility_rejects_drift_before_checkpoint_reuse(tmp_path, monkeypatch, drift):
    from devbench.analysis.runtime import verify_runtime_compatibility
    _, _, observed = preparation(tmp_path, monkeypatch)
    identity = {'python_minor': list(sys.version_info[:2]), 'packages': observed['runtime']['packages']}
    assert verify_runtime_compatibility(identity, observed, recorded_runtime=observed)['compatible']
    changed = deepcopy(observed)
    changed['runtime'][drift] = '9.99.1' if drift == 'python' else {'numpy': 'synthetic-2'}
    with pytest.raises(ValueError, match='runtime'):
        verify_runtime_compatibility(identity, changed, recorded_runtime=observed)


def test_failed_notebook_keeps_output_and_comparison_cannot_claim_equivalence(tmp_path):
    from devbench.analysis.notebooks import execute_notebook, write_notebook
    from devbench.analysis.bundles import seal_bundle
    declared = contract(historical=True)
    reference, reference_root = _bundle(tmp_path / 'reference', declared)
    from tests.test_rna_numerical_equivalence import _prepared
    failed = _prepared(tmp_path / 'failed', declared, parameters={'parent_root': reference_root})
    write_notebook(failed / 'notebook.template.ipynb', ["print('retained output')", "raise RuntimeError('P4 failed replay')"])
    with pytest.raises(Exception, match='P4 failed replay'):
        execute_notebook(failed / 'notebook.template.ipynb', failed / 'notebook.ipynb', cell_timeout=30)
    book = json.loads((failed / 'notebook.ipynb').read_text())
    assert book['cells'][0]['outputs'][0]['text'] == ['retained output\n']
    assert book['cells'][1]['outputs'][-1]['ename'] == 'RuntimeError'
    failed_root = seal_bundle(failed, status='failed')['manifest_sha256']
    result = _compare((reference, failed, reference_root, failed_root), declared)
    assert result['equivalent'] is None and result['status'] == 'not_comparable'


@pytest.mark.parametrize('damage', ['duplicate_header', 'duplicate_json_key', 'json_numeric_identifier'])
def test_ambiguous_record_encodings_are_not_comparable(tmp_path, damage):
    declared = contract(kind='json_records' if damage != 'duplicate_header' else 'csv', historical=True)
    pair = _pair(tmp_path, declared)
    from devbench.analysis.bundles import seal_bundle
    root = pair[1]
    (root / 'receipts.json').unlink()
    (root / 'root-receipt.json').unlink()
    if damage == 'duplicate_header':
        (root / 'statistics.csv').write_text('gene_id,log2FC,padj,direction,log2FC\n001,1,0.01,up,1\n')
    elif damage == 'duplicate_json_key':
        (root / 'statistics.json').write_text('[{"gene_id":"001","log2FC":1,"padj":0.01,"direction":"up","log2FC":2}]')
    else:
        changed = rows()
        changed[0]['gene_id'] = 1
        _json(root / 'statistics.json', changed)
    new_root = seal_bundle(root, status='completed_review_passed')['manifest_sha256']
    result = _compare((pair[0], root, pair[2], new_root), declared)
    assert result['status'] == 'not_comparable'


def test_comparison_cli_emits_separate_receipt_without_bundle_writes(tmp_path, capsys):
    from devbench.analysis.numerical_equivalence import main
    declared = contract(historical=True)
    pair = _pair(tmp_path, declared)
    path = tmp_path / 'comparison-contract.json'
    _json(path, declared)
    main(['--reference', str(pair[0]), '--replay', str(pair[1]), '--expected-reference-root', pair[2],
          '--expected-replay-root', pair[3], '--contract', str(path)])
    result = json.loads(capsys.readouterr().out)
    assert result['status'] == 'equivalent' and result['prespecified'] is False


def test_worker_rejects_runtime_drift_before_creating_checkpoint_state(tmp_path, monkeypatch):
    source, parameters, observed = preparation(tmp_path, monkeypatch)
    root = tmp_path / 'prepared'
    workbench.prepare_bundle(source, root, parameters)
    from devbench.analysis import runtime
    changed = deepcopy(observed)
    changed['runtime']['python'] = '9.99.0'
    monkeypatch.setattr(runtime, 'observe_runtime', lambda tier: changed)
    with pytest.raises(ValueError, match='runtime_python'):
        workbench._stage_worker(str(root), 'qc')
    assert not (root / 'checkpoints').exists()


def test_retained_coordinator_handoff_does_not_forward_new_flags(tmp_path, monkeypatch):
    declared = contract(historical=True)
    original, original_root = _bundle(tmp_path / 'reference', declared, parameters={'model_path': None})
    from devbench.analysis import runtime
    observed = json.loads((original / 'runtime.json').read_text())
    monkeypatch.setattr(runtime, 'observe_runtime', lambda tier: observed)
    calls = []
    monkeypatch.setattr(workbench.subprocess, 'run', lambda *args, **kwargs: calls.append((args, kwargs)))
    argv = ['--replay-bundle', str(original), '--expected-root', original_root,
            '--data', str(tmp_path / 'relocated-input'), '--out', str(tmp_path / 'replay')]
    workbench.main(argv)
    assert len(calls) == 1
    assert calls[0][0][0] == [sys.executable, '-m', 'devbench.analysis.workbench', *argv]
    assert calls[0][1]['cwd'] == original / 'source'
    assert calls[0][1]['env']['PYTHONPATH'] == str(original / 'source')


def test_replay_cli_retains_runtime_failure_before_any_execution(tmp_path, monkeypatch):
    declared = contract(historical=True)
    original, original_root = _bundle(tmp_path / 'reference', declared, parameters={'model_path': None})
    from devbench.analysis import runtime
    observed = json.loads((original / 'runtime.json').read_text())
    observed['runtime']['packages']['numpy'] = 'different'
    monkeypatch.setattr(runtime, 'observe_runtime', lambda tier: observed)
    def forbidden(*args, **kwargs):
        pytest.fail('Runtime drift reached retained coordinator execution')
    monkeypatch.setattr(workbench.subprocess, 'run', forbidden)
    output = tmp_path / 'replay'
    with pytest.raises(ValueError, match='runtime_packages'):
        workbench.main(['--replay-bundle', str(original), '--expected-root', original_root,
                        '--data', str(tmp_path / 'relocated-input'), '--out', str(output)])
    failure = json.loads((output / 'replay-preflight-failure.json').read_text())
    assert failure['status'] == 'rejected_before_execution'
    assert failure['reference_root_sha256'] == original_root
    assert failure['type'] == 'ValueError'
    assert not (output / 'receipts.json').exists()


def test_replay_cli_reports_equivalence_separately_after_bound_preparation(tmp_path, monkeypatch, capsys):
    source, parameters, _ = preparation(tmp_path, monkeypatch)
    declared = contract()
    original = tmp_path / 'original'
    workbench.prepare_bundle(source, original, parameters, equivalence_contract=declared)
    from tests.test_rna_numerical_equivalence import _statistics
    from devbench.analysis.bundles import seal_bundle, verify_bundle
    _statistics(original, declared, rows())
    original_root = seal_bundle(original, status='completed_review_passed')['manifest_sha256']
    monkeypatch.setattr(workbench, '__file__', str(original / 'source/devbench/analysis/workbench.py'))
    def execute(root):
        _statistics(root, declared, list(reversed(rows())), pretty=True)
        result = seal_bundle(root, status='completed_review_passed')
        return result
    monkeypatch.setattr(workbench, 'execute_bundle', execute)
    output = tmp_path / 'replay'
    workbench.main(['--replay-bundle', str(original), '--expected-root', original_root,
                    '--data', str(source), '--out', str(output)])
    result = json.loads(capsys.readouterr().out)
    assert result['numerical_equivalence']['status'] == 'equivalent'
    assert result['numerical_equivalence']['prespecified'] is True
    assert verify_bundle(original, original_root)['verified']
    assert verify_bundle(output, result['manifest_sha256'])['verified']
    assert not (output / 'numerical-equivalence-receipt.json').exists()


@pytest.mark.parametrize('file', ['parameters.json', 'checkpoint-identity.json', 'runtime.json'])
def test_invalid_identity_shapes_are_not_comparable_receipts(tmp_path, file):
    declared = contract(historical=True)
    pair = _pair(tmp_path, declared)
    root = pair[1]
    _json(root / file, [])
    # Re-seal only this synthetic test fixture; comparator must not mutate it.
    (root / 'receipts.json').unlink()
    (root / 'root-receipt.json').unlink()
    from devbench.analysis.bundles import seal_bundle
    if file == 'parameters.json':
        # Bundle sealing itself needs valid parameters; retain its custody hash
        # by editing the synthetic manifest and retaining the resulting new root.
        manifest = json.loads((pair[0] / 'receipts.json').read_text())
        manifest['parent_root'] = pair[2]
        manifest['artifacts'] = {str(path.relative_to(root)): sha256(path) for path in root.iterdir() if path.is_file()}
        _json(root / 'receipts.json', manifest)
        _json(root / 'root-receipt.json', {'manifest_sha256': sha256(root / 'receipts.json')})
        # Custody format validation refuses invalid parameters before comparison.
        with pytest.raises((ValueError, AttributeError)):
            _compare((pair[0], root, pair[2], sha256(root / 'receipts.json')), declared)
        return
    new_root = seal_bundle(root, status='completed_review_passed')['manifest_sha256']
    result = _compare((pair[0], root, pair[2], new_root), declared)
    assert result['status'] == 'not_comparable'


def test_declared_results_must_be_sealed_manifest_artifacts(tmp_path):
    declared = contract()
    declared['results'][0]['path'] = 'bindings.json'
    pair = _pair(tmp_path, declared)
    from tests.test_rna_numerical_equivalence import _statistics
    changed = rows()
    changed[0]['log2FC'] = 987.0
    for root in pair[:2]:
        _statistics(root, declared, changed)
    from devbench.analysis.bundles import verify_bundle
    assert verify_bundle(pair[0], pair[2])['verified']
    assert verify_bundle(pair[1], pair[3])['verified']
    result = _compare(pair, declared)
    assert result['status'] == 'not_comparable' and result['equivalent'] is None


@pytest.mark.parametrize('artifact', ['statistics.csv', 'parameters.json', 'checkpoint-identity.json',
                                     'runtime.json', 'numerical-equivalence-contract.json', 'receipts.json'])
def test_every_parsed_capture_matches_retained_manifest_after_verification(tmp_path, monkeypatch, artifact):
    from devbench.analysis import numerical_equivalence as module
    from tests.test_rna_numerical_equivalence import _statistics
    declared = contract()
    pair = _pair(tmp_path, declared)
    verify = module.verify_bundle
    def mutate_after_verification(root, expected):
        result = verify(root, expected)
        if artifact == 'statistics.csv':
            changed = rows()
            changed[0]['log2FC'] = 654.0
            _statistics(root, declared, changed)
        else:
            with (root / artifact).open('ab') as handle:
                handle.write(b'\n')
        return result
    monkeypatch.setattr(module, 'verify_bundle', mutate_after_verification)
    result = _compare(pair, declared)
    assert result['status'] == 'not_comparable' and result['equivalent'] is None
    assert 'mismatch' in result['reason']


def test_tiny_nonzero_exponents_cannot_underflow_to_zero_equivalence(tmp_path):
    declared = contract()
    declared['results'][0]['numeric']['log2FC'] = {'atol': 0.0, 'rtol': 0.0}
    left, right = rows(), rows()
    left[0]['log2FC'] = '1e-10000000'
    right[0]['log2FC'] = 0.0
    result = _compare(_pair(tmp_path, declared, left, right), declared)
    assert result['status'] == 'not_comparable' and result['equivalent'] is None


def test_exact_tolerance_boundary_does_not_round_long_finite_numeric_cells(tmp_path):
    declared = contract()
    declared['results'][0]['numeric']['log2FC'] = {'atol': 1.0, 'rtol': 0.0}
    left, right = rows(), rows()
    left[0]['log2FC'] = 0.0
    right[0]['log2FC'] = '1.' + '0' * 150 + '1'
    result = _compare(_pair(tmp_path, declared, left, right), declared)
    assert result['status'] == 'mismatch' and result['equivalent'] is False


def test_missing_input_digest_cannot_establish_equivalence(tmp_path):
    declared = contract(historical=True)
    pair = _pair(tmp_path, declared)
    from devbench.analysis.bundles import seal_bundle
    roots = []
    for root in pair[:2]:
        params = json.loads((root / 'parameters.json').read_text())
        params['data_sha256'] = None
        identity = json.loads((root / 'checkpoint-identity.json').read_text())
        identity['parameters'] = params
        identity['data_sha256'] = None
        _json(root / 'parameters.json', params)
        _json(root / 'checkpoint-identity.json', identity)
        (root / 'receipts.json').unlink()
        (root / 'root-receipt.json').unlink()
        roots.append(seal_bundle(root, status='completed_review_passed')['manifest_sha256'])
    # Keep replay lineage valid so only the missing data identity is at issue.
    right = pair[1]
    params = json.loads((right / 'parameters.json').read_text())
    params['parent_root'] = roots[0]
    identity = json.loads((right / 'checkpoint-identity.json').read_text())
    identity['parameters'] = params
    _json(right / 'parameters.json', params)
    _json(right / 'checkpoint-identity.json', identity)
    (right / 'receipts.json').unlink()
    (right / 'root-receipt.json').unlink()
    roots[1] = seal_bundle(right, status='completed_review_passed')['manifest_sha256']
    result = _compare((pair[0], pair[1], *roots), declared)
    assert result['status'] == 'not_comparable' and result['equivalent'] is None


def _literal_json_pair(tmp_path, literal, replay_value, *, declared=None, extra_literal=None):
    """Retain an actual unquoted JSON numeric token, without Python float parsing."""
    from tests.test_rna_numerical_equivalence import _prepared
    from devbench.analysis.bundles import seal_bundle
    declared = contract(kind='json_records') if declared is None else declared
    reference = _prepared(tmp_path / 'reference', declared)
    values = rows()
    values[0]['log2FC'] = '__EXACT_NUMERIC_TOKEN__'
    if extra_literal is not None:
        values[0]['unused'] = {'nested': '__UNUSED_NUMERIC_TOKEN__'}
    content = json.dumps(values, allow_nan=False).replace('"__EXACT_NUMERIC_TOKEN__"', literal)
    if extra_literal is not None:
        content = content.replace('"__UNUSED_NUMERIC_TOKEN__"', extra_literal)
    (reference / 'statistics.json').write_text(content)
    reference_root = seal_bundle(reference, status='completed_review_passed')['manifest_sha256']
    replay_values = rows()
    replay_values[0]['log2FC'] = replay_value
    replay, replay_root = _bundle(tmp_path / 'replay', declared, replay_values,
                                  parameters={'parent_root': reference_root})
    return (reference, replay, reference_root, replay_root), declared


@pytest.mark.parametrize('literal,replay_value,expected', [
    ('1e-10000000', 0.0, 'not_comparable'),
    ('1.' + '0' * 150 + '1', 1.0, 'mismatch'),
    ('1.' + '0' * 400, 1.0, 'not_comparable'),
    ('5e-324', 0.0, 'mismatch'),
    ('1e308', 1e308, 'equivalent'),
])
def test_json_numeric_tokens_are_exact_and_bounded_before_float_conversion(tmp_path, literal, replay_value, expected):
    declared = contract(kind='json_records')
    declared['results'][0]['numeric']['log2FC'] = {'atol': 0.0, 'rtol': 0.0}
    pair, declared = _literal_json_pair(tmp_path, literal, replay_value, declared=declared)
    result = _compare(pair, declared)
    assert result['status'] == expected
    assert result['equivalent'] is (None if expected == 'not_comparable' else expected == 'equivalent')
    json.dumps(result, allow_nan=False)


def test_json_exact_numeric_token_cannot_round_across_decision_boundary(tmp_path):
    declared = contract(kind='json_records')
    declared['results'][0]['numeric']['log2FC'] = {'atol': 1.0, 'rtol': 0.0,
        'decision': {'operator': '<', 'threshold': 0.05}}
    pair, declared = _literal_json_pair(tmp_path, '0.049999999999999999999999999999999999', 0.05,
                                       declared=declared)
    assert _compare(pair, declared)['status'] == 'mismatch'


@pytest.mark.parametrize('literal', ['NaN', 'Infinity', '-Infinity', '1e309'])
def test_json_whole_artifact_finiteness_includes_undeclared_nested_columns(tmp_path, literal):
    pair, declared = _literal_json_pair(tmp_path, '1.0', 1.0, extra_literal=literal)
    result = _compare(pair, declared)
    assert result['status'] == 'not_comparable' and result['equivalent'] is None
    json.dumps(result, allow_nan=False)
