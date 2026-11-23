"""Normal chat source sets compile without inventing experimental facts."""
import csv
import hashlib
import json
from pathlib import Path

import pytest

from tests.test_analysis_episodes import inputs
from tests.test_analysis_episodes import workspace as _workspace_fixture

workspace = _workspace_fixture

def api():
    from devbench.analysis import workbench_episode_intake
    return workbench_episode_intake


def record(root, *, annotations=False):
    _, paths = inputs(root, continuous=False)
    roles = ['expression', 'metadata'] + (['annotations'] if annotations else [])
    profiles = [{'source': str(paths[role]), 'sha256': hashlib.sha256(paths[role].read_bytes()).hexdigest(),
                 'size_bytes': paths[role].stat().st_size, 'observed': {}} for role in roles]
    return {'id': 'a' * 32, 'thread_id': 'b' * 32, 'revision': 2, 'question': 'Estimate B minus A',
            'sources': [p['source'] for p in profiles], 'source_profiles': profiles,
            'source_declarations': [{'role': role, **({'assay': 'bulk', 'measurement': 'non_umi_counts',
                'count_provenance': 'Original synthetic count fixture'} if role == 'expression' else {})} for role in roles],
            'scientific_context': {'bulk_episode_declarations': {
                'biological_unit': 'donor', 'population_unit_column': 'donor_id',
                'specimen_column': 'specimen_id', 'library_column': 'library_id',
                'observation_column': 'sample_id', 'condition_column': 'condition',
                'identity_status': 'declared_from_experimental_records', 'dependence': 'independent',
                'batch_columns': 'batch', 'contrast_numerator': 'B', 'contrast_denominator': 'A',
                'declaration_provenance': 'Synthetic fixture experimental record'}}}


def test_intake_compiler_entrypoint_exists():
    assert callable(getattr(api(), 'compile_bulk_intake', None)), 'bulk intake compiler is missing'


def test_unknown_units_and_contrast_are_answerable_before_planning(tmp_path, monkeypatch):
    value = record(tmp_path)
    value['scientific_context']['bulk_episode_declarations'] = {}
    from devbench.analysis import episode_planning
    monkeypatch.setattr(episode_planning, 'propose_analysis', lambda *a, **k: pytest.fail('No plan without experimental facts'))
    result = api().propose_bulk_intake(value, output_dir=tmp_path / 'compiled', selector=object())
    assert result['status'] == 'clarification'
    fields = {field['field'] for field in result['clarification_fields']}
    assert {'biological_unit', 'population_unit_column', 'contrast_numerator', 'contrast_denominator'} <= fields
    assert all(field['scope'] == 'bulk_episode_declarations' for field in result['clarification_fields'])
    assert 'specimen_column' not in fields and 'declaration_provenance' not in fields
    assert result['observed']['samples'] == 8
    facts = result['fact_provenance']
    assert set(facts) == {
        'Observed', 'Supplied', 'Derived', 'Inferred', 'Unknown/Conflicting',
    }
    assert facts['Derived']['observation_column'] == 'sample_id'
    assert 'population_unit_column' in facts['Unknown/Conflicting']
    assert result.get('dataset') is None
    assert not (tmp_path / 'compiled').exists()


def test_csv_requests_remaining_execution_facts_after_initial_confirmation(tmp_path):
    value = record(tmp_path)
    supplied = value['scientific_context']['bulk_episode_declarations']
    for field in ('specimen_column', 'library_column', 'declaration_provenance'):
        supplied.pop(field)
    result = api().compile_bulk_intake(value, output_dir=tmp_path / 'compiled')
    assert result['status'] == 'clarification'
    assert {field['field'] for field in result['clarification_fields']} == {
        'specimen_column', 'library_column', 'declaration_provenance',
    }
    assert not (tmp_path / 'compiled').exists()


def test_tsv_inspection_establishes_alignment_without_csv_execution(tmp_path):
    value = record(tmp_path)
    for index, original in enumerate(value['sources']):
        path = Path(original).with_suffix('.tsv')
        with (
            Path(original).open(newline='') as source,
            path.open('w', newline='') as target,
        ):
            csv.writer(target, delimiter='\t').writerows(csv.reader(source))
        value['sources'][index] = str(path)
        value['source_profiles'][index] = _profile(path)
    value['source_declarations'] = [{}, {}]
    result = api().inspect_matrix_intake(value)
    assert result['expression_path'] == Path(value['sources'][0])
    assert result['observation_column'] == 'sample_id'
    assert len(result['sample_ids']) == len(result['metadata_rows']) == 8
    assert result['fact_provenance']['Inferred']['source_roles'] == {
        'expression': 0, 'metadata': 1,
    }
    compiled = api().compile_bulk_intake(value, output_dir=tmp_path / 'compiled')
    assert compiled['status'] == 'unsupported'
    assert compiled['observed']['metadata_columns'] == result['metadata_columns']
    assert not (tmp_path / 'compiled').exists()


def _profile(path, observed=None):
    return {
        'source': str(path),
        'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
        'size_bytes': path.stat().st_size,
        'observed': observed or {},
    }


def test_h5ad_intake_uses_the_bound_observation_index_and_text_metadata(tmp_path):
    import anndata as ad
    import numpy as np
    import pandas as pd

    path = tmp_path / 'logical.h5ad'
    obs = pd.DataFrame(
        {'capture_batch': pd.Categorical(['B1', 'B2', 'B1']),
         'dose': [0.2, 0.3, 0.4]},
        index=['cell-1', 'cell-2', 'cell-3'],
    )
    ad.AnnData(np.ones((3, 2)), obs=obs).write_h5ad(path)
    source = {'source': str(path), 'profile': _profile(path)}
    result = api().inspect_matrix_intake(source)
    assert result['sample_ids'] == ['cell-1', 'cell-2', 'cell-3']
    assert result['gene_count'] == 2
    assert result['metadata_rows'][1][result['observation_column']] == 'cell-2'
    assert result['metadata_rows'][1]['capture_batch'] == 'B2'
    assert result['measurement'] is None
    assert result['fact_provenance']['Observed']['expression_values_validated'] is False
    assert result['fact_provenance']['Supplied']['source_declarations'] == [{}]


def test_native_hdf5_intake_reads_the_stored_axes(tmp_path):
    import h5py

    path = tmp_path / 'expression.h5'
    with h5py.File(path, 'w') as handle:
        handle.create_dataset('expression', shape=(2, 3), dtype='i4')
        handle.create_dataset('gene_names', data=[b'g1', b'g2'])
        handle.create_dataset('sample_names', data=[b's1', b's2', b's3'])
    result = api().inspect_matrix_intake(
        {'source': str(path), 'profile': _profile(path)},
    )
    assert result['gene_count'] == 2
    assert result['sample_ids'] == ['s1', 's2', 's3']
    assert result['metadata_rows'] == [] and result['observation_column'] is None


def test_ambiguous_table_alignment_needs_explicit_id_choice(tmp_path):
    value = record(tmp_path)
    value['scientific_context']['bulk_episode_declarations'] = {}
    path = Path(value['sources'][1])
    with path.open(newline='') as handle:
        reader = csv.DictReader(handle)
        names, rows = [*reader.fieldnames, 'id_alias'], list(reader)
    for row, reversed_row in zip(rows, reversed(rows), strict=True):
        row['id_alias'] = reversed_row['sample_id']
    with path.open('w', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=names)
        writer.writeheader()
        writer.writerows(rows)
    value['source_profiles'][1] = _profile(path)
    with pytest.raises(ValueError, match='observation.*column'):
        api().inspect_matrix_intake(value)
    result = api().compile_bulk_intake(value, output_dir=tmp_path / 'compiled')
    assert result['status'] == 'clarification'
    assert 'observation_column' in {
        field['field'] for field in result['clarification_fields']
    }


def test_10x_axes_require_bound_feature_and_barcode_companions(tmp_path):
    matrix = tmp_path / 'matrix.mtx'
    features = tmp_path / 'features.tsv'
    barcodes = tmp_path / 'barcodes.tsv'
    matrix.write_text(
        '%%MatrixMarket matrix coordinate integer general\n2 3 1\n1 1 5\n',
    )
    features.write_text('f1\tG1\tGene Expression\nf2\tG2\tGene Expression\n')
    barcodes.write_text('s1\ns2\ns3\n')
    declared = [{'role': 'expression', 'measurement': 'umi_counts'}, {'role': 'other'}]
    source = {'sources': [str(matrix), str(features)],
              'source_profiles': [_profile(matrix), _profile(features)],
              'source_declarations': declared}
    with pytest.raises(ValueError, match='companion'):
        api().inspect_matrix_intake(source)
    source['sources'].append(str(barcodes))
    source['source_profiles'].append(_profile(barcodes))
    declared.append({'role': 'other'})
    inspected = api().inspect_matrix_intake(source)
    assert inspected['sample_ids'] == ['s1', 's2', 's3']
    assert inspected['gene_count'] == 2
    assert inspected['measurement'] == 'umi_counts'
    assert inspected['metadata_rows'] == [] and inspected['observation_column'] is None
    barcodes.write_text('s3\ns2\ns1\n')
    with pytest.raises(ValueError, match='hash mismatch'):
        api().inspect_matrix_intake(source)


def test_declared_intake_preserves_originals_and_creates_only_unknown_annotation(tmp_path):
    value = record(tmp_path)
    original = {path: open(path, 'rb').read() for path in value['sources']}
    result = api().compile_bulk_intake(value, output_dir=tmp_path / 'compiled')
    assert result['status'] == 'ready'
    task = result['dataset']['public_task']
    assert 'questions' not in task and 'gene_ids' not in task
    assert task['measurement'] == 'raw_counts'
    assert task['unit_hierarchy']['identity_status'] == 'declared_from_experimental_records'
    assert task['context']['requested_condition_contrast'] == {'numerator': 'B', 'denominator': 'A'}
    assert task['context']['annotation_status'] == 'unknown_empty_artifact'
    assert json.loads(open(result['dataset']['inputs']['annotations']).read()) == {}
    assert result['binding']['source_order'] == value['sources']
    assert result['binding']['source_declarations_sha256']
    assert result['binding']['annotation']['sha256'] == hashlib.sha256(b'{}\n').hexdigest()
    assert result['attributed_declarations']['population_unit_column']['origin'] == 'user_declaration'
    assert original == {path: open(path, 'rb').read() for path in value['sources']}


@pytest.mark.parametrize('change', ['axis', 'duplicate', 'ambiguous', 'changed'])
def test_bad_or_changed_input_never_compiles(tmp_path, change):
    value = record(tmp_path)
    path = value['sources'][1]
    if change == 'ambiguous':
        value['scientific_context']['bulk_episode_declarations']['condition_column'] = 'missing_condition'
    else:
        with open(path, newline='') as handle:
            reader = csv.DictReader(handle)
            columns, rows = reader.fieldnames, list(reader)
        if change == 'axis': rows[0]['sample_id'] = 'foreign'
        if change == 'duplicate': rows[0]['sample_id'] = rows[1]['sample_id']
        if change == 'changed': rows[0]['condition'] = 'changed'
        with open(path, 'w', newline='') as handle:
            writer = csv.DictWriter(handle, fieldnames=columns)
            writer.writeheader(); writer.writerows(rows)
        if change != 'changed':
            value['source_profiles'][1]['sha256'] = hashlib.sha256(open(path, 'rb').read()).hexdigest()
            value['source_profiles'][1]['size_bytes'] = __import__('pathlib').Path(path).stat().st_size
    result = api().compile_bulk_intake(value, output_dir=tmp_path / 'compiled')
    assert result['status'] in {'clarification', 'unsupported'}
    assert result.get('dataset') is None
    assert not (tmp_path / 'compiled').exists()


def test_paired_and_metadata_attribution_survive_compilation(tmp_path):
    value = record(tmp_path, annotations=True)
    supplied = value['scientific_context']['bulk_episode_declarations']
    supplied['dependence'] = 'paired'
    supplied['population_unit_column'] = {'value': 'donor_id', 'origin': 'metadata_declaration', 'source': 'Public study manifest'}
    result = api().compile_bulk_intake(value, output_dir=tmp_path / 'compiled')
    assert result['status'] == 'ready'
    assert result['dataset']['public_task']['analysis_columns']['blocking'] == ['donor_id']
    assert result['attributed_declarations']['population_unit_column']['origin'] == 'metadata_declaration'
    assert result['dataset']['inputs']['annotations'] == value['sources'][2]


def test_actual_reusable_proposal_api_runs_only_after_compilation(workspace):
    from tests.test_episode_planning import Selector
    value = record(workspace)
    result = api().propose_bulk_intake(value, output_dir=workspace / 'compiled', selector=Selector(), allow_data_read=True)
    assert result['status'] == 'ready'
    assert result['planning']['status'] == 'proposal_ready_for_review'
    assert result['planning']['proposal_receipt'] is not None


def test_suggestions_cannot_establish_biological_identity(tmp_path):
    value = record(tmp_path)
    value['scientific_context']['bulk_episode_declarations']['population_unit_column'] = {
        'value': 'donor_id', 'origin': 'assistant_suggestion', 'source': 'column name'}
    result = api().compile_bulk_intake(value, output_dir=tmp_path / 'compiled')
    assert result['status'] == 'clarification'
    assert 'population_unit_column' in {field['field'] for field in result['clarification_fields']}


def test_normalized_measurements_remain_continuous_and_unknown_batches_need_answer(tmp_path):
    value = record(tmp_path)
    value['source_declarations'][0]['measurement'] = 'normalized_expression'
    result = api().compile_bulk_intake(value, output_dir=tmp_path / 'compiled')
    assert result['dataset']['public_task']['measurement'] == 'continuous'
    assert result['dataset']['input_kind'] == 'normalized_expression'
    value['scientific_context']['bulk_episode_declarations']['batch_columns'] = 'unknown'
    result = api().compile_bulk_intake(value, output_dir=tmp_path / 'other')
    assert result['status'] == 'clarification'
    assert [field['field'] for field in result['clarification_fields']] == ['batch_columns']


@pytest.mark.parametrize('defect', ['format', 'metadata_bound', 'duplicate_role', 'hierarchy'])
def test_limits_and_unsupported_transports_do_not_create_artifacts(tmp_path, defect):
    from pathlib import Path
    value = record(tmp_path)
    if defect == 'format':
        old = Path(value['sources'][0]); new = old.with_suffix('.tsv'); old.rename(new)
        value['sources'][0] = value['source_profiles'][0]['source'] = str(new)
    elif defect == 'metadata_bound':
        path = Path(value['sources'][1]); path.write_text(path.read_text() + 'x' * 1_000_001)
        value['source_profiles'][1].update(size_bytes=path.stat().st_size, sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    elif defect == 'duplicate_role':
        value['source_declarations'][1]['role'] = 'expression'
    else:
        value['scientific_context']['bulk_episode_declarations']['specimen_column'] = 'donor_id'
    result = api().compile_bulk_intake(value, output_dir=tmp_path / 'compiled')
    assert result['status'] == 'unsupported'
    assert not (tmp_path / 'compiled').exists()


def test_owned_unknown_annotation_is_idempotent_but_tampering_is_rejected(tmp_path):
    from pathlib import Path
    value = record(tmp_path)
    first = api().compile_bulk_intake(value, output_dir=tmp_path / 'compiled')
    second = api().compile_bulk_intake(value, output_dir=tmp_path / 'compiled')
    assert first == second and first['status'] == 'ready'
    Path(first['dataset']['inputs']['annotations']).write_text('{"symbols": "invented"}')
    result = api().compile_bulk_intake(value, output_dir=tmp_path / 'compiled')
    assert result['status'] == 'unsupported'


def test_planner_clarification_is_preserved_without_creating_an_episode(workspace):
    from tests.test_episode_planning import Selector
    result = api().propose_bulk_intake(record(workspace), output_dir=workspace / 'compiled',
        selector=Selector({'kind': 'request_information', 'message': 'Which scientific outcome is intended?'}),
        allow_data_read=True)
    assert result['planning']['status'] == 'awaiting_information'
    assert result['planning']['proposal_receipt'] is None
    assert not (workspace / 'compiled' / 'episode').exists()


def test_paired_metadata_and_ordered_snapshots_are_preserved(tmp_path):
    from pathlib import Path
    value = record(tmp_path)
    path = Path(value['sources'][1])
    with path.open(newline='') as handle:
        reader = csv.DictReader(handle); columns, rows = reader.fieldnames, list(reader)
    for index, row in enumerate(rows):
        row['donor_id'] = 'paired-' + str(index % 4)
    with path.open('w', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=columns); writer.writeheader(); writer.writerows(rows)
    value['source_profiles'][1].update(sha256=hashlib.sha256(path.read_bytes()).hexdigest(), size_bytes=path.stat().st_size)
    value['scientific_context']['bulk_episode_declarations']['dependence'] = 'paired'
    result = api().compile_bulk_intake(value, output_dir=tmp_path / 'compiled')
    assert result['status'] == 'ready'
    assert result['dataset']['public_task']['analysis_columns']['blocking'] == ['donor_id']
    assert result['binding']['revision'] == 2
    reversed_record = dict(value, sources=list(reversed(value['sources'])))
    assert api().compile_bulk_intake(reversed_record, output_dir=tmp_path / 'other')['status'] == 'unsupported'
