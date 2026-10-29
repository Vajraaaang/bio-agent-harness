"""Bounded bulk bridge semantics; no statistical fits or model calls."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile

import pytest

from devbench.analysis.methods import MethodService
from devbench.analysis.rna_execution_inputs import prepare_execution_inputs, load_execution_inputs
from devbench.analysis.workflow_state import digest


@pytest.fixture
def protected_workspace():
    with tempfile.TemporaryDirectory(prefix='.bulk-binding-test-', dir=Path(__file__).resolve().parents[1]) as folder:
        yield Path(folder)


def bulk_binding(workspace, *, continuous=False):
    methods = MethodService(workspace / 'methods')
    ids = [f's{i}' for i in range(8)]
    genes = ['g1', 'g2']
    hierarchy = {'population_unit': 'donor', 'population_unit_label': 'donor_id',
        'specimen_label': 'specimen_id', 'library_label': 'library_id', 'identity_status': 'declared'}
    rows = [{'sample_id': sample, 'donor_id': f'd{i}', 'specimen_id': f'p{i}', 'library_id': f'l{i}',
        'condition': 'A' if i < 4 else 'B', 'batch': 'x' if i % 2 else 'y'} for i, sample in enumerate(ids)]
    matrix = {'gene_ids': genes, 'sample_ids': ids,
        'matrix': [[float(10+i) if continuous else 10+i for i in range(8)],
                   [float(20+i) if continuous else 20+i for i in range(8)]]}
    refs = {}
    for name, value in [('matrix', matrix), ('metadata', {'samples': rows})]:
        path = workspace / (name + '.json')
        path.write_text(json.dumps(value))
        refs[name] = methods.import_artifact(path, artifact_type='json', actor='fixture')
    binding = {'format': 'devbench.bulk-rna-binding/1', 'matrix_artifact_id': refs['matrix']['artifact_id'],
        'metadata_artifact_id': refs['metadata']['artifact_id'], 'declarations': {'format': 'devbench.bulk-rna-input/1',
        'representation': 'normalized_expression' if continuous else 'raw_counts', 'axes': ['gene', 'sample'],
        'provenance': {'origin': 'hand-authored software fixture'},
        'biological_independence': {'independent_units': True, 'unit_hierarchy': hierarchy},
        'metadata_roles': {'biological_unit': 'donor_id', 'condition': 'condition', 'batch': 'batch'}}}
    design = {'format': 'devbench.rna-design/1', 'objective': {'intent': 'infer'},
        'design': {'contrasts': [{'field': 'condition', 'reference': 'A', 'target': 'B'}],
            'covariates': ['batch'], 'chronological_field': None, 'chronological_order': []},
        'selected_sample_ids': ids, 'source': {'kind': 'fixture', 'reference': 'bulk_binding_test',
            'statement': 'Independent-donor B minus A with recorded batch.'}}
    public = {'measurement': 'continuous' if continuous else 'raw_counts', 'observation_id_label': 'sample_id',
        'gene_ids': genes, 'unit_hierarchy': hierarchy,
        'analysis_columns': {'condition': 'condition', 'population_unit': 'donor_id', 'batches': ['batch'],
            'time': None, 'dose': None, 'covariate': None},
        'context': {'requested_condition_contrast': {'denominator': 'A', 'numerator': 'B'}, 'condition_labels': ['A', 'B']},
        'questions': [{'target_id': 'mean_contrast', 'estimand': 'Condition mean contrast over observed biological units',
            'units': 'input units' if continuous else 'log2 count-rate contrast',
            'contrast': 'B minus A', 'reference_population': 'observed biological units',
            'prediction_reference': 'observed biological units'}]}
    options = {'condition_col': 'condition', 'reference_condition': 'A', 'comparison_condition': 'B',
        'minimum_units': 3, 'condition_number_limit': 1000, 'batch_cols': ['batch']}
    version = methods.register_dispatcher('bulk_condition_contrast', actor='fixture')['version_id']
    ports = {'format': 'devbench.rna-execution-ports/2', 'ports': {
        'counts': {'source': 'matrix', 'artifact_type': 'file', 'encoding': 'csv', 'required': True, 'parameter': 'counts_path'},
        'metadata': {'source': 'metadata', 'artifact_type': 'table', 'encoding': 'csv', 'required': True, 'parameter': 'metadata_path'}},
        'accepted_representations': [binding['declarations']['representation']], 'objective': design['objective'],
        'design': design['design'], 'string_columns': sorted(rows[0]), 'method_version_id': version,
        'parameters': {'public_task': public, 'options': options}}
    return methods, binding, ports, design


def plan_for(methods, binding, ports, declaration, workspace):
    from devbench.analysis.rna_input_contract import inspect_registered_bulk_rna_input
    bridge = prepare_execution_inputs(binding, ports, artifact_service=methods, output=workspace / 'bridge')
    inspection = inspect_registered_bulk_rna_input(methods, binding)
    plan = {'rna_design': declaration, 'rna_design_sha256': digest(declaration),
        'context': {'dataset_summary': {'observed': {'bulk_rna_input': inspection}}},
        'rna_execution_inputs': {'record_ref': bridge, 'dataset_binding': binding, 'workspace': str(workspace)}}
    return plan, bridge


@pytest.mark.parametrize('continuous', [False, True])
def test_bulk_loader_roundtrip_and_semantics(protected_workspace, continuous):
    from devbench.analysis.rna_design_audit import require_rna_design_execution, rna_design_execution_parameters
    methods, binding, ports, declaration = bulk_binding(protected_workspace, continuous=continuous)
    plan, bridge = plan_for(methods, binding, ports, declaration, protected_workspace)
    record = require_rna_design_execution(plan, methods)
    assert record == load_execution_inputs(bridge, artifact_service=methods, dataset_binding=binding)
    actual = rna_design_execution_parameters(plan, methods)
    assert actual['method_version_id'] == ports['method_version_id']
    assert actual['parameters'] == ports['parameters']
    assert actual['inputs'] == {key: value['artifact_id'] for key, value in record['execution_refs'].items()}
    assert methods.list_records('run') == []


def test_direction_mismatch_refuses_even_self_consistent_bridge(protected_workspace):
    from devbench.analysis.rna_design_audit import require_rna_design_execution
    methods, binding, ports, declaration = bulk_binding(protected_workspace)
    ports['parameters']['options'].update(reference_condition='B', comparison_condition='A')
    ports['parameters']['public_task']['context'].update(requested_condition_contrast={'denominator': 'B', 'numerator': 'A'}, condition_labels=['B','A'])
    ports['parameters']['public_task']['questions'][0]['contrast'] = 'A minus B'
    plan, _ = plan_for(methods, binding, ports, declaration, protected_workspace)
    with pytest.raises(ValueError, match='contrast|direction'):
        require_rna_design_execution(plan, methods)


def test_family_cannot_be_shrunk(protected_workspace):
    methods, binding, ports, declaration = bulk_binding(protected_workspace)
    ports['parameters']['public_task']['gene_ids'] = ['g1']
    with pytest.raises(ValueError, match='gene|family|feature'):
        plan_for(methods, binding, ports, declaration, protected_workspace)


def test_dropped_declared_batch_refuses(protected_workspace):
    from devbench.analysis.rna_design_audit import require_rna_design_execution
    methods, binding, ports, declaration = bulk_binding(protected_workspace)
    ports['parameters']['public_task']['analysis_columns']['batches'] = []
    ports['parameters']['options'].pop('batch_cols')
    with pytest.raises(ValueError, match='batch'):
        plan, _ = plan_for(methods, binding, ports, declaration, protected_workspace)
        require_rna_design_execution(plan, methods)


def test_source_hierarchy_cannot_be_replaced(protected_workspace):
    from devbench.analysis.rna_design_audit import require_rna_design_execution
    methods, binding, ports, declaration = bulk_binding(protected_workspace)
    ports['parameters']['public_task']['unit_hierarchy']['population_unit'] = 'organism'
    # Do not mutate original source declaration through a shared Python fixture reference.
    binding['declarations']['biological_independence']['unit_hierarchy'] = {
        **binding['declarations']['biological_independence']['unit_hierarchy'], 'population_unit': 'donor'}
    plan, _ = plan_for(methods, binding, ports, declaration, protected_workspace)
    with pytest.raises(ValueError, match='hierarchy'):
        require_rna_design_execution(plan, methods)


def replace_document(methods, binding, workspace, name, change):
    value = json.loads(methods.artifact_path(binding[name + '_artifact_id']).read_bytes())
    change(value)
    path = workspace / ('changed-' + name + '.json')
    path.write_text(json.dumps(value))
    binding[name + '_artifact_id'] = methods.import_artifact(path, artifact_type='json', actor='fixture')['artifact_id']


@pytest.mark.parametrize('continuous', [False, True])
def test_large_integer_precision_loss_is_rejected(protected_workspace, continuous):
    methods, binding, ports, declaration = bulk_binding(protected_workspace, continuous=continuous)
    replace_document(methods, binding, protected_workspace, 'matrix', lambda document: document['matrix'][0].__setitem__(0, 9007199254740993))
    with pytest.raises(ValueError, match='integer|precision|counts'):
        plan_for(methods, binding, ports, declaration, protected_workspace)


def test_numeric_metadata_is_not_silently_stringified(protected_workspace):
    methods, binding, ports, declaration = bulk_binding(protected_workspace)
    def change(document):
        for row in document['samples']: row['age'] = 1
    replace_document(methods, binding, protected_workspace, 'metadata', change)
    ports['string_columns'].append('age')
    with pytest.raises(ValueError, match='string'):
        plan_for(methods, binding, ports, declaration, protected_workspace)


def test_integer_valued_float_counts_are_not_converted(protected_workspace):
    methods, binding, ports, declaration = bulk_binding(protected_workspace)
    replace_document(methods, binding, protected_workspace, 'matrix', lambda document: document['matrix'][0].__setitem__(0, 10.0))
    with pytest.raises(ValueError, match='integer storage'):
        plan_for(methods, binding, ports, declaration, protected_workspace)


def test_missing_source_hierarchy_cannot_be_invented_at_execution(protected_workspace):
    from devbench.analysis.rna_design_audit import require_rna_design_execution
    methods, binding, ports, declaration = bulk_binding(protected_workspace)
    binding['declarations']['biological_independence'].pop('unit_hierarchy')
    plan, _ = plan_for(methods, binding, ports, declaration, protected_workspace)
    with pytest.raises(ValueError, match='hierarchy'):
        require_rna_design_execution(plan, methods)


def test_unrelated_covariate_is_not_reinterpreted_as_batch(protected_workspace):
    from devbench.analysis.rna_design_audit import require_rna_design_execution
    methods, binding, ports, declaration = bulk_binding(protected_workspace)
    declaration['design']['covariates'] = ['batch', 'age']
    def change(document):
        for i, row in enumerate(document['samples']): row['age'] = str(i % 2)
    replace_document(methods, binding, protected_workspace, 'metadata', change)
    ports['string_columns'].append('age')
    # A nonconfounded nonbatch term has no supported semantic mapping.
    with pytest.raises(ValueError, match='covariate|Inference preconditions'):
        plan, _ = plan_for(methods, binding, ports, declaration, protected_workspace)
        require_rna_design_execution(plan, methods)


def test_unrelated_original_source_declarations_are_not_swapped(protected_workspace):
    from devbench.analysis.rna_design_audit import require_rna_design_execution
    methods, binding, ports, declaration = bulk_binding(protected_workspace)
    plan, _ = plan_for(methods, binding, ports, declaration, protected_workspace)
    plan['context']['dataset_summary']['observed']['bulk_rna_input']['provenance'] = {'origin': 'another input'}
    with pytest.raises(ValueError, match='original audited source'):
        require_rna_design_execution(plan, methods)


def test_changed_derived_bytes_refuse_before_dispatch(protected_workspace):
    from devbench.analysis.rna_design_audit import require_rna_design_execution
    methods, binding, ports, declaration = bulk_binding(protected_workspace)
    plan, bridge = plan_for(methods, binding, ports, declaration, protected_workspace)
    record = load_execution_inputs(bridge, artifact_service=methods)
    path = methods.artifact_path(record['execution_refs']['counts']['artifact_id'])
    path.chmod(0o600)
    path.write_text('gene_id,other\ng1,0\n')
    with pytest.raises(ValueError):
        require_rna_design_execution(plan, methods)
    assert methods.list_records('run') == []


def test_target_unit_alias_cannot_override_checked_measurement_units(protected_workspace):
    methods, binding, ports, declaration = bulk_binding(protected_workspace)
    ports['parameters']['public_task']['questions'][0]['measurement_units'] = 'clinical survival benefit'
    with pytest.raises(ValueError, match='units|vocabulary|target'):
        plan_for(methods, binding, ports, declaration, protected_workspace)
