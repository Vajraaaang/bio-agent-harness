"""Executed paired design audits over synthetic metadata, never airway counts."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile

import pytest

from devbench.analysis.methods import MethodService
from devbench.analysis.rna_design_audit import audit_rna_design, validate_rna_design
from devbench.analysis.rna_execution_inputs import audit_execution_design, _ports
from devbench.analysis.rna_input_contract import inspect_registered_bulk_rna_input, validate_bulk_rna_input
from devbench.analysis.workflow_state import digest


def paired_case(*, paired=True, library_as_unit=False, confounded=False):
    rows = [{'sample_id': f's{i}', 'library_id': f'l{i}', 'specimen_id': f'p{i}',
             'cell': f'line{i // 2}', 'condition': 'A' if i % 2 == 0 else 'B',
             'batch': ('x' if i % 2 == 0 else 'y') if confounded else 'x'} for i in range(8)]
    unit = 'library_id' if library_as_unit else 'cell'
    hierarchy = {'population_unit': 'donor_derived_cell_line', 'population_unit_label': unit,
                 'specimen_label': 'specimen_id', 'library_label': 'library_id', 'identity_status': 'declared'}
    source = {'format': 'devbench.bulk-rna-input/1', 'representation': 'raw_counts', 'axes': ['gene', 'sample'],
              'provenance': {'origin': 'hand-authored synthetic metadata; not airway'},
              'biological_independence': {'independent_units': True, 'unit_hierarchy': hierarchy},
              'metadata_roles': {'condition': 'condition', 'biological_unit': unit, 'batch': 'batch'}}
    design = {'format': 'devbench.rna-design/1', 'objective': {'intent': 'infer'},
              'design': {'contrasts': [{'field': 'condition', 'reference': 'A', 'target': 'B'}],
                         'covariates': ['batch'], 'chronological_field': None, 'chronological_order': []},
              'selected_sample_ids': [r['sample_id'] for r in rows],
              'source': {'kind': 'fixture', 'reference': 'paired-design-audit',
                         'statement': 'Compare two conditions in four declared paired biological units.'}}
    if paired:
        design['design']['blocking'] = [unit]
    matrix = {'gene_ids': ['g1', 'g2'], 'sample_ids': design['selected_sample_ids'],
              'matrix': [[12, 19, 15, 22, 11, 20, 16, 24], [35, 30, 31, 28, 33, 31, 36, 32]]}
    inspection = validate_bulk_rna_input(source, gene_ids=matrix['gene_ids'], sample_ids=matrix['sample_ids'],
                                         metadata_rows=rows, matrix=matrix['matrix'])
    return rows, source, design, matrix, inspection


def test_declared_pair_is_identifiable_without_count_fit():
    _, _, declaration, _, inspection = paired_case()
    assert validate_rna_design(declaration) == declaration
    audit = audit_execution_design(declaration, inspection)
    assert audit['blockers'] == []
    assert (audit['rank'], audit['columns'], audit['residual_degrees_of_freedom']) == (5, 5, 3)
    assert (audit['sample_count'], audit['unit_count']) == (8, 4)
    assert audit['blocking_columns'] == ['cell']
    assert audit['biological_unit_observations'] == {'line0': 2, 'line1': 2, 'line2': 2, 'line3': 2}


def test_library_labels_do_not_hide_repeated_cell_lines():
    _, _, declaration, _, inspection = paired_case(paired=False, library_as_unit=True)
    audit = audit_execution_design(declaration, inspection)
    assert 'repeated_biological_units:cell' in audit['blockers']


def test_repeated_units_without_declared_block_still_refuse():
    _, _, declaration, _, inspection = paired_case(paired=False)
    audit = audit_execution_design(declaration, inspection)
    assert 'repeated_biological_units:cell' in audit['blockers']


def test_declared_pair_does_not_remove_confounded_batch():
    _, _, declaration, _, inspection = paired_case(confounded=True)
    audit = audit_execution_design(declaration, inspection)
    assert 'complete_confounding_with_categorical_batch:condition' in audit['blockers']
    assert 'design_rank_deficient_or_complete_confounding' in audit['blockers']


def test_block_must_be_declared_biological_unit():
    _, _, declaration, _, inspection = paired_case()
    declaration['design']['blocking'] = ['library_id']
    audit = audit_execution_design(declaration, inspection)
    assert 'blocking_column_must_match_biological_unit' in audit['blockers']


def test_each_paired_unit_requires_both_contrast_levels():
    rows, source, declaration, matrix, _ = paired_case()
    rows[1]['condition'] = 'A'
    inspection = validate_bulk_rna_input(source, gene_ids=matrix['gene_ids'], sample_ids=matrix['sample_ids'],
                                         metadata_rows=rows, matrix=matrix['matrix'])
    audit = audit_execution_design(declaration, inspection)
    assert 'paired_unit_missing_contrast_level:cell' in audit['blockers']


def test_cell_identity_does_not_mean_single_cells_are_replicates():
    _, _, declaration, _, inspection = paired_case()
    inspection['biological_independence']['unit_hierarchy']['population_unit'] = 'cell'
    audit = audit_execution_design(declaration, inspection)
    assert any('biological' in reason or 'membership' in reason for reason in audit['blockers'])


def test_block_declaration_is_bounded_and_explicit():
    _, _, declaration, _, _ = paired_case()
    for invalid in ['cell', ['cell', 'batch'], [None], ['']]:
        changed = deepcopy(declaration)
        changed['design']['blocking'] = invalid
        with pytest.raises(ValueError, match='block|Design'):
            validate_rna_design(changed)


def test_registered_paired_audit_retains_evidence_and_no_authority(tmp_path):
    rows, source, declaration, matrix, _ = paired_case()
    methods = MethodService(tmp_path / 'methods')
    refs = {}
    for name, document in [('matrix', matrix), ('metadata', {'samples': rows})]:
        path = tmp_path / (name + '.json')
        path.write_text(json.dumps(document))
        refs[name] = methods.import_artifact(path, artifact_type='json', actor='fixture')
    binding = {'format': 'devbench.bulk-rna-binding/1', 'matrix_artifact_id': refs['matrix']['artifact_id'],
               'metadata_artifact_id': refs['metadata']['artifact_id'], 'declarations': source}
    inspection = inspect_registered_bulk_rna_input(methods, binding)
    plan = {'rna_design': declaration, 'rna_design_sha256': digest(declaration),
            'context': {'dataset_summary': {'observed': {'bulk_rna_input': inspection}}}}
    audit = audit_rna_design(plan, methods)
    assert audit['status'] == 'passed'
    assert audit['scientific_validity_established'] is False
    assert audit['input_artifact_refs'] == inspection['input_artifact_refs']
    assert audit['model_execution_authority'] == 'not_granted_by_design_audit'
    assert 'model_execution_authority_granted' not in audit
    assert methods.list_records('run') == []


def test_paired_execution_bridge_preserves_block_in_registered_adapter():
    from tests.test_rna_bulk_execution_binding import bulk_binding, plan_for, replace_document
    from devbench.analysis.rna_design_audit import require_rna_design_execution
    with tempfile.TemporaryDirectory(prefix='.paired-binding-', dir=Path(__file__).resolve().parents[1]) as folder:
        workspace = Path(folder)
        methods, binding, ports, declaration = bulk_binding(workspace)
        def pair(document):
            for i, row in enumerate(document['samples']):
                row['donor_id'] = f'd{i % 4}'
                row['batch'] = 'x'
        replace_document(methods, binding, workspace, 'metadata', pair)
        declaration['design']['blocking'] = ['donor_id']
        ports['design'] = declaration['design']
        ports['parameters']['public_task']['analysis_columns']['blocking'] = ['donor_id']
        ports['parameters']['options']['block_cols'] = ['donor_id']
        assert _ports(ports)['design']['blocking'] == ['donor_id']
        plan, _ = plan_for(methods, binding, ports, declaration, workspace)
        record = require_rna_design_execution(plan, methods)
        from devbench.analysis.rna_execution_inputs import _read_source
        receipt = _read_source(record['transformation_receipt_ref'], workspace=workspace)
        assert receipt['design_audit']['unit_count'] == 4
        assert receipt['design_audit']['rank'] == 5
        assert methods.list_records('run') == []


def test_technical_memberships_do_not_increase_paired_residual_df():
    rows, source, declaration, matrix, _ = paired_case()
    copies = [{**row, 'sample_id': row['sample_id'] + 'b', 'library_id': row['library_id'] + 'b'} for row in rows]
    rows += copies
    matrix['sample_ids'] += [row['sample_id'] for row in copies]
    matrix['matrix'] = [values + values for values in matrix['matrix']]
    inspection = validate_bulk_rna_input(source, gene_ids=matrix['gene_ids'], sample_ids=matrix['sample_ids'],
                                         metadata_rows=rows, matrix=matrix['matrix'])
    audit = audit_execution_design(declaration, inspection)
    assert audit['blockers'] == []
    assert audit['sample_count'] == 16
    assert audit['analysis_observation_count'] == 8
    assert audit['unit_count'] == 4
    assert audit['residual_degrees_of_freedom'] == 3


def test_within_pair_nuisance_disagreement_is_not_hidden_by_aggregation():
    rows, source, declaration, matrix, _ = paired_case()
    rows.append({**rows[0], 'sample_id': 'technical-copy', 'library_id': 'technical-copy', 'batch': 'y'})
    matrix['sample_ids'].append('technical-copy')
    matrix['matrix'] = [values + [values[0]] for values in matrix['matrix']]
    inspection = validate_bulk_rna_input(source, gene_ids=matrix['gene_ids'], sample_ids=matrix['sample_ids'],
                                         metadata_rows=rows, matrix=matrix['matrix'])
    audit = audit_execution_design(declaration, inspection)
    assert 'repeated_units_across_design_coordinates_require_cluster_model' in audit['blockers']


def test_paired_design_reaches_existing_durable_workflow_and_registered_core():
    """The future authorized route is executable today with offline synthetic decisions."""
    from devbench.analysis.method_planner import ScientificMethodPlanner
    from devbench.analysis.rna_execution_inputs import load_execution_inputs
    from devbench.analysis.workflow_entrypoints import audit_workflow_design, workflow_action
    from tests.test_declared_bulk_analyst_workflow import _inputs, DeclaredBulkSelector, ACTOR, GENES
    from tests.test_rna_bulk_execution_binding import replace_document
    from tests.support.workflow_science_fixtures import RecordingScientificRuntime
    from devharness_core.provenance import canonical_json

    evidence_root = Path(__file__).resolve().parents[1] / 'reports' / 'rna-process-2026-10-28'
    root = Path(tempfile.mkdtemp(prefix='paired-workflow-', dir=evidence_root))
    methods, binding, declaration, parameters, _ = _inputs(root, continuous=False)
    def pair(document):
        for i, row in enumerate(document['samples']):
            row['donor_id'] = f'donor_{i % 4}'
    replace_document(methods, binding, root, 'metadata', pair)
    declaration['design']['blocking'] = ['donor_id']
    declaration['source']['statement'] = 'Four synthetic paired donors, eight library memberships.'
    parameters['public_task']['analysis_columns']['blocking'] = ['donor_id']
    parameters['options']['block_cols'] = ['donor_id']
    planner = ScientificMethodPlanner(methods)
    plan = planner.create({'scientific_objective': 'Estimate the condition contrast within four synthetic paired donors',
                           'computational_task': 'bulk RNA paired synthetic workflow verification'},
        {'observed': {}, 'inferred': {}, 'unknowns': [],
         'artifact_ids': [binding['matrix_artifact_id'], binding['metadata_artifact_id']]},
        actor=ACTOR, data_classification='synthetic', evaluation_purpose='execution_verification',
        rna_input_contract=binding, rna_design=declaration)
    session = plan['session_id']
    audit = audit_workflow_design(planner, session, actor=ACTOR)
    assert audit['status'] == 'ready', audit
    assert audit['audit']['rank'] == 5 and audit['audit']['unit_count'] == 4
    selector = DeclaredBulkSelector(False)
    options = {'actor': ACTOR, 'selector': selector, 'scientific_workflow': RecordingScientificRuntime()}
    def act(node, **extra):
        return workflow_action(planner, session, node, **options, **extra)
    act('discovery')
    act('generate')
    act('candidate_review')
    selector.version = methods.register_dispatcher('bulk_condition_contrast', actor=ACTOR)['version_id']
    act('resolve', version_ids=[selector.version])
    act('implementation')
    ports = {'format': 'devbench.rna-execution-ports/2', 'ports': {
        'counts': {'source': 'matrix', 'artifact_type': 'file', 'encoding': 'csv', 'required': True, 'parameter': 'counts_path'},
        'metadata': {'source': 'metadata', 'artifact_type': 'table', 'encoding': 'csv', 'required': True, 'parameter': 'metadata_path'}},
        'accepted_representations': ['raw_counts'], 'objective': declaration['objective'], 'design': declaration['design'],
        'string_columns': ['batch', 'condition', 'donor_id', 'library_id', 'sample_id', 'specimen_id'],
        'method_version_id': selector.version, 'parameters': parameters}
    bridge = planner.prepare_execution_inputs(session, ports, output=root / 'execution-inputs', allow_data_execution=True)
    record = load_execution_inputs(bridge, artifact_service=methods, dataset_binding=binding)
    selector.inputs = {key: value['artifact_id'] for key, value in record['execution_refs'].items()}
    selector.parameters = parameters
    drafts = act('design', execution_inputs_ref=bridge, execution_workspace=root)
    act('design_review')
    analysis = methods.create_analysis('Paired synthetic workflow proof', actor=ACTOR)
    branch = methods.create_branch(analysis['analysis_id'], analysis['revision_id'], actor=ACTOR)
    act('declare', branch_id=branch['branch_id'], spec=drafts[0])
    with pytest.raises(PermissionError, match='authorization|authority'):
        act('run', experiment_id='declared-bulk-proof')
    results = act('run', experiment_id='declared-bulk-proof', allow_data_execution=True)
    assert len(results) == 1 and results[0]['status'] == 'success', results
    run = methods.list_records('run')[0]
    assert run['execution']['tool_events'][0]['tool_name'] == 'bulk_condition_contrast'
    result = json.loads(methods.artifact_path(run['outputs']['result']).read_bytes())
    units = result['diagnostics']['biological_units']
    assert (units['analysis_unit_count'], units['analysis_observation_count']) == (4, 8)
    assert result['diagnostics']['designs'][0]['rank'] == 5
    rows = result['target_results'][0]['payload']['gene_statistics']
    assert [row['gene_id'] for row in rows] == GENES
    assert all(row['p_value'] is not None and 0 <= row['p_value'] <= row['q_value'] <= 1 for row in rows)
    assert result['method']['count_model'] == 'negative_binomial_empirical_bayes_dispersion'
    assert all(row['evidence']['model_execution'] is False for row in planner.get(session)['selection_history'])
    summary = {'format': 'test.paired-durable-workflow/1', 'session_id': session,
               'execution': 'verified_synthetic', 'biological_units': 4, 'library_memberships': 8,
               'design_rank': 5, 'residual_degrees_of_freedom': 3,
               'estimated_genes': len(rows), 'model_sessions': 0, 'biological_agreement': 'not_run',
               'scientific_correctness': None, 'scientific_trial_passed': False}
    (root / 'proof-summary.json').write_bytes(canonical_json(summary))


@pytest.mark.parametrize('malformed', [['not-a-mapping'], 'donor_id', 1])
def test_malformed_declared_hierarchy_is_controlled_refusal(malformed):
    _, _, declaration, _, inspection = paired_case()
    inspection['biological_independence']['unit_hierarchy'] = malformed
    audit = audit_execution_design(declaration, inspection)
    assert 'biological_unit_hierarchy_invalid' in audit['blockers']


def test_custom_specimen_cannot_cross_declared_biological_units():
    rows, source, declaration, matrix, _ = paired_case()
    source['biological_independence']['unit_hierarchy']['specimen_label'] = 'biospecimen'
    for row in rows:
        row['biospecimen'] = 'one-specimen'
    inspection = validate_bulk_rna_input(source, gene_ids=matrix['gene_ids'], sample_ids=matrix['sample_ids'],
                                         metadata_rows=rows, matrix=matrix['matrix'])
    audit = audit_execution_design(declaration, inspection)
    assert 'specimen_crosses_biological_units' in audit['blockers']


def test_custom_library_cannot_cross_specimens():
    rows, source, declaration, matrix, _ = paired_case()
    source['biological_independence']['unit_hierarchy']['library_label'] = 'read_library'
    for row in rows:
        row['read_library'] = 'one-library'
    inspection = validate_bulk_rna_input(source, gene_ids=matrix['gene_ids'], sample_ids=matrix['sample_ids'],
                                         metadata_rows=rows, matrix=matrix['matrix'])
    audit = audit_execution_design(declaration, inspection)
    assert 'library_crosses_specimens' in audit['blockers']


@pytest.mark.parametrize('status', [[], {}])
def test_malformed_hierarchy_identity_status_is_controlled_refusal(status):
    _, _, declaration, _, inspection = paired_case()
    inspection['biological_independence']['unit_hierarchy']['identity_status'] = status
    audit = audit_execution_design(declaration, inspection)
    assert 'independent_biological_units_unknown:hierarchy_identity_status' in audit['blockers']
    assert 'paired_biological_unit_hierarchy_required' in audit['blockers']


def test_physical_library_cannot_be_reused_across_paired_conditions():
    rows, source, declaration, matrix, _ = paired_case()
    for row in rows:
        row['specimen_id'] = 'specimen_' + row['cell']
        row['library_id'] = 'physical_library_' + row['cell']
    inspection = validate_bulk_rna_input(source, gene_ids=matrix['gene_ids'], sample_ids=matrix['sample_ids'],
                                         metadata_rows=rows, matrix=matrix['matrix'])
    audit = audit_execution_design(declaration, inspection)
    assert 'library_crosses_design_coordinates' in audit['blockers']


def test_shared_specimen_with_distinct_libraries_can_be_paired():
    rows, source, declaration, matrix, _ = paired_case()
    for row in rows:
        row['specimen_id'] = 'specimen_' + row['cell']
    inspection = validate_bulk_rna_input(source, gene_ids=matrix['gene_ids'], sample_ids=matrix['sample_ids'],
                                         metadata_rows=rows, matrix=matrix['matrix'])
    audit = audit_execution_design(declaration, inspection)
    assert audit['blockers'] == []
    assert (audit['unit_count'], audit['rank'], audit['residual_degrees_of_freedom']) == (4, 5, 3)
