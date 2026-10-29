"""Adversarial P0 admission and retained-binding checks; synthetic software evidence."""
from copy import deepcopy
import json

import pytest

from devbench.analysis.method_planner import ScientificMethodPlanner
from tests.test_method_planner import Selector
from tests.test_rna_scientific_contract import TASK, prepared_contract, scientific_contract, validate
from tests.test_rna_scrna_input_contract import registered
from tests.test_rna_scientific_contract_entrypoints import _browser_companion
from tests.test_scientific_workspace import action, scientific_context, setup_workspace, tmp_path


def _poison(companion, key):
    companion['requirements'] = [{'name': 'scientific_constraint', 'value': {'nested': [{'declared': {key: []}}]},
        'origin': 'assistant', 'source_id': 'request', 'source_excerpt': TASK['scientific_objective']}]
    return companion


@pytest.mark.parametrize('key', ['tools', 'Registry', 'allowed_tools', 'method_inventory', 'capability', 'availability'])
def test_capability_keys_rejected_before_plan_persistence(tmp_path, key):
    planner, summary, companion = prepared_contract(tmp_path)
    with pytest.raises(ValueError, match='capability|registry'):
        planner.create(TASK, summary, actor='software-verifier', scientific_contract=_poison(companion, key))
    assert planner.methods.list_records('method_plan') == []
    assert planner.methods.list_records('method_plan_snapshot') == []


def test_cli_rejects_poison_before_real_store_persistence(tmp_path):
    from devbench.analysis.method_planner_cli import main
    planner, summary, companion = prepared_contract(tmp_path)
    request = tmp_path / 'poison-request.json'
    request.write_text(json.dumps({'task': TASK, 'dataset_summary': summary,
        'scientific_contract': _poison(companion, 'tools')}))
    with pytest.raises(ValueError, match='capability|registry'):
        main(['--workspace', str(planner.methods.root), 'create', '--request', str(request)])
    assert planner.methods.list_records('method_plan') == []
    assert planner.methods.list_records('method_plan_snapshot') == []


def test_browser_rejects_poison_before_real_store_persistence(tmp_path):
    app, methods, _, step, _ = setup_workspace(tmp_path)
    try:
        _, dataset, analysis = scientific_context(app, step)
        companion = _browser_companion(app, methods, step, analysis)
        _poison(companion, 'tools')
        companion['requirements'][0]['source_excerpt'] = companion['objective']['statement']
        with pytest.raises(ValueError, match='capability|registry'):
            action(app, 'create_plan', analysis_ref=analysis['ref'], dataset_ref=dataset['ref'],
                scientific_contract=companion)
        assert methods.list_records('method_plan') == []
        assert methods.list_records('method_plan_snapshot') == []
        assert not methods.list_records('run') and not app.scientific.futures
    finally:
        app.close()


def _unit(identity, kind='subject', parents=(), independence='independent'):
    return {'unit_id': identity, 'kind': kind, 'parent_ids': list(parents), 'independence': independence,
        'evidence_source_ids': ['request'], 'condition': None, 'time': None}


@pytest.mark.parametrize('kind', ['donor', 'subject'])
def test_nested_independent_units_and_siblings_rejected(tmp_path, kind):
    planner, summary, companion = prepared_contract(tmp_path)
    companion['biological_units'] = [_unit('parent'), _unit('child1', kind, ['parent']),
        _unit('child2', kind, ['parent'])]
    with pytest.raises(ValueError, match='Independent|independent'):
        validate(planner, summary, companion)


@pytest.mark.parametrize('parent_kind', ['specimen', 'sample', 'library', 'capture', 'technical_replicate'])
@pytest.mark.parametrize('independence', ['dependent', 'unknown'])
def test_donor_cannot_descend_from_lower_level_unit(tmp_path, parent_kind, independence):
    planner, summary, companion = prepared_contract(tmp_path)
    companion['biological_units'] = [_unit('parent', parent_kind, independence='dependent'),
        _unit('donor', 'donor', ['parent'], independence)]
    with pytest.raises(ValueError, match='parent|hierarchy'):
        validate(planner, summary, companion)


@pytest.mark.parametrize('independence', ['dependent', 'unknown'])
def test_valid_hierarchy_retains_nested_unknown_or_dependent_identity(tmp_path, independence):
    planner, summary, companion = prepared_contract(tmp_path)
    companion['biological_units'] = [_unit('subject'), _unit('donor', 'donor', ['subject'], independence),
        _unit('specimen', 'specimen', ['donor'], 'dependent'),
        _unit('capture', 'capture', ['specimen'], 'dependent'),
        _unit('library', 'library', ['capture'], 'dependent')]
    assert validate(planner, summary, companion)['biological_units'] == companion['biological_units']


def _scrna_plan(tmp_path):
    methods, artifact, binding = registered(tmp_path)
    planner = ScientificMethodPlanner(methods)
    companion = scientific_contract(methods, [artifact['artifact_id']])
    companion['assay'].update(representation='direct_counts', orientation='cell_by_feature',
        count_provenance='Synthetic integer software fixture')
    state = planner.create(TASK, {'observed': {}, 'inferred': {}, 'unknowns': [],
        'artifact_ids': [artifact['artifact_id']]}, actor='software-verifier',
        rna_input_contract=binding, scientific_contract=companion)
    return planner, state


@pytest.mark.parametrize('entrypoint', ['get', 'export', 'select', 'direct_select'])
def test_retained_scrna_contradiction_rejected_on_read_export_and_selection(tmp_path, entrypoint):
    planner, state = _scrna_plan(tmp_path)
    changed = deepcopy(state)
    changed['context']['dataset_summary']['observed']['scrna_input']['declarations']['measurement'] = 'normalized_expression'
    with planner.methods._db() as db:
        planner.methods._put(db, 'method_plan', state['session_id'], changed, update=True)
    selector = Selector({'candidates': []})
    with pytest.raises(ValueError, match='contradict|AnnData'):
        if entrypoint == 'get':
            planner.get(state['session_id'])
        elif entrypoint == 'export':
            planner.export_scientific_contract(state['session_id'])
        elif entrypoint == 'select':
            planner.generate_candidates(state['session_id'], selector, actor='software-verifier')
        else:
            planner._select(changed, selector, {'decision_kind': 'method_candidates', **changed['context']},
                actor='software-verifier', allow_model_execution=False)
    assert selector.contexts == []
    assert not planner.methods.list_records('run')


def test_unchanged_scrna_companion_loads_and_selects_without_invented_facts(tmp_path):
    planner, state = _scrna_plan(tmp_path)
    assert planner.get(state['session_id']) == state
    assert planner.export_scientific_contract(state['session_id'])['scientific_contract'] == state['scientific_contract']
    selector = Selector({'candidates': []})
    planner.generate_candidates(state['session_id'], selector, actor='software-verifier')
    assert selector.contexts[0]['scientific_contract']['assay']['orientation'] == 'cell_by_feature'
    assert 'execution' not in selector.contexts[0]['scientific_contract']
    assert not planner.methods.list_records('run')
