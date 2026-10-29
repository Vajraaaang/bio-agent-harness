"""Pre-selection design checks use declared cohorts and original registered bytes."""
from copy import deepcopy
from pathlib import Path
import tempfile
import json

import pytest

from devbench.analysis.method_planner import ScientificMethodPlanner
from devbench.analysis.workflow_actions import WorkflowActions
from devbench.analysis.workflow_state import digest, planning_binding
from tests.test_langgraph_workflow import ledger
from tests.test_extensible_methods import prepared


def design(ids=None):
    return {'format': 'devbench.rna-design/1', 'objective': {'intent': 'infer'},
        'design': {'contrasts': [{'field': 'condition', 'reference': 'A', 'target': 'B'}],
                   'covariates': [], 'chronological_field': None, 'chronological_order': []},
        'selected_sample_ids': ids or ['s1', 's2', 's3', 's4'],
        'source': {'kind': 'fixture', 'reference': 'test specification', 'statement': 'Compare A and B in independent donors.'}}


def prepared_plan(tmp_path, *, rows=None, declaration=None, independence=True):
    methods, *_ = prepared(tmp_path)
    rows = rows or [{'sample_id': f's{i+1}', 'donor_id': f'd{i+1}', 'condition': c, 'batch': 'x'}
                    for i, c in enumerate(['A', 'A', 'B', 'B'])]
    matrix = {'gene_ids': ['g1', 'g2'], 'sample_ids': [r['sample_id'] for r in rows],
              'matrix': [[10 + i for i in range(len(rows))], [20 + i for i in range(len(rows))]]}
    refs = []
    for name, value in [('matrix', matrix), ('metadata', {'samples': rows})]:
        path = tmp_path / (name + '.json')
        path.write_text(json.dumps(value))
        refs.append(methods.import_artifact(path, artifact_type='json', actor='fixture'))
    binding = {'format': 'devbench.bulk-rna-binding/1', 'matrix_artifact_id': refs[0]['artifact_id'],
        'metadata_artifact_id': refs[1]['artifact_id'], 'declarations': {'format': 'devbench.bulk-rna-input/1',
        'representation': 'raw_counts', 'axes': ['gene', 'sample'], 'provenance': {'origin': 'synthetic'},
        'biological_independence': {'independent_units': True} if independence else None,
        'metadata_roles': {'biological_unit': 'donor_id', 'condition': 'condition', 'batch': 'batch'}}}
    planner = ScientificMethodPlanner(methods)
    plan = planner.create({'scientific_objective': 'Compare A and B', 'computational_task': 'bulk RNA'},
        {'observed': {}, 'inferred': {}, 'unknowns': [], 'artifact_ids': [r['artifact_id'] for r in refs]},
        actor='fixture', data_classification='synthetic', rna_input_contract=binding,
        rna_design=design() if declaration is None else declaration)
    actions = WorkflowActions(planner, ledger(tmp_path), actor='fixture', selector=None)
    return planner, plan, actions


def test_paired_donors_stop_before_any_selection_and_replay(tmp_path, monkeypatch):
    rows = [{'sample_id': f's{i+1}', 'donor_id': f'd{i%2}', 'condition': c, 'batch': 'x'}
            for i, c in enumerate(['A', 'A', 'B', 'B'])]
    planner, plan, actions = prepared_plan(tmp_path, rows=rows)
    monkeypatch.setattr(planner, 'generate_candidates', lambda *a, **k: pytest.fail('selection must not run'))
    before = actions.create_state(plan['session_id'])
    stopped = actions.perform('audit', before)
    assert stopped['status'] == 'abstained'
    assert stopped['outcome_ref']['kind'] == 'abstention'
    assert actions.verify_terminal(stopped) == stopped
    assert actions.perform('audit', before) == stopped
    with pytest.raises(ValueError, match='ready'):
        actions.perform('question', stopped)
    evidence = actions.ledger.get(plan['session_id'], 'audit', 0)['result']['evidence']
    assert 'repeated_biological_units:donor_id' in evidence['blockers']
    assert evidence['selected_sample_ids'] == ['s1', 's2', 's3', 's4']


def test_selection_precedes_missing_extra_condition_audit(tmp_path):
    rows = [{'sample_id': f's{i+1}', 'donor_id': f'd{i+1}', 'condition': c, 'batch': 'x'}
            for i, c in enumerate(['A', 'A', 'B', 'B'])]
    rows.append({'sample_id': 's5', 'donor_id': None, 'condition': 'C', 'batch': None})
    planner, plan, actions = prepared_plan(tmp_path, rows=rows)
    result = actions.perform('audit', actions.create_state(plan['session_id']))
    assert result['status'] == 'review_required'
    evidence = actions.ledger.get(plan['session_id'], 'audit', 0)['result']['evidence']
    assert evidence['sample_count'] == evidence['unit_count'] == 4
    assert evidence['blockers'] == []
    assert evidence['information_required'] == ['selected_cohort_requires_registered_execution_inputs']
    assert evidence['selected_sample_ids_sha256'] == digest(design()['selected_sample_ids'])


def test_missing_independence_is_information_needed(tmp_path):
    _, plan, actions = prepared_plan(tmp_path, independence=False)
    result = actions.perform('audit', actions.create_state(plan['session_id']))
    assert result['status'] == 'review_required'
    assert result['failure']['kind'] == 'rna_design_information_required'
    assert actions.check(result)


@pytest.mark.parametrize('changes,reason', [
    ({'batch': ['x', 'x', 'y', 'y']}, 'complete_confounding_with_categorical_batch:condition'),
    ({'condition': ['A', 'A', 'A', 'A']}, 'invalid_contrast:condition'),
])
def test_invalid_design_is_terminal(tmp_path, changes, reason):
    rows = [{'sample_id': f's{i+1}', 'donor_id': f'd{i+1}', 'condition': c, 'batch': 'x'}
            for i, c in enumerate(['A', 'A', 'B', 'B'])]
    for column, values in changes.items():
        for row, value in zip(rows, values): row[column] = value
    _, plan, actions = prepared_plan(tmp_path, rows=rows)
    stopped = actions.perform('audit', actions.create_state(plan['session_id']))
    assert stopped['status'] == 'abstained'
    assert reason in actions.ledger.get(plan['session_id'], 'audit', 0)['result']['evidence']['blockers']
    actions.verify_terminal(stopped)


def test_design_source_and_selection_are_bound(tmp_path):
    planner, plan, actions = prepared_plan(tmp_path)
    changed = deepcopy(plan)
    changed['rna_design']['source']['statement'] = 'Changed objective'
    assert planning_binding(changed) != planning_binding(plan)
    before = actions.create_state(plan['session_id'])
    actions.perform('audit', before)
    record = planner.methods.get('artifact', plan['context']['dataset_summary']['artifact_ids'][1])
    path = planner.methods.artifact_path(record['artifact_id'])
    path.chmod(0o600)
    path.write_text('{}')
    with pytest.raises(ValueError):
        actions.perform('audit', before)


@pytest.mark.parametrize('field,value', [('selected_sample_ids', []), ('parameters', {'alpha': .05}), ('method', 'nb')])
def test_closed_method_free_design_rejects_invalid_fields(tmp_path, field, value):
    declaration = design()
    declaration[field] = value
    with pytest.raises(ValueError):
        prepared_plan(tmp_path, declaration=declaration)


def test_forged_ready_cursor_cannot_skip_refusal(tmp_path):
    rows = [{'sample_id': f's{i+1}', 'donor_id': f'd{i%2}', 'condition': c, 'batch': 'x'}
            for i, c in enumerate(['A', 'A', 'B', 'B'])]
    _, plan, actions = prepared_plan(tmp_path, rows=rows)
    stopped = actions.perform('audit', actions.create_state(plan['session_id']))
    with pytest.raises(ValueError, match='evidence|outcome'):
        actions.perform('question', {**stopped, 'status': 'ready'})


def test_direct_planner_cannot_select_before_design_audit(tmp_path):
    from tests.test_method_planner import Selector
    planner, plan, _ = prepared_plan(tmp_path)
    selector = Selector({'candidates': []})
    with pytest.raises(ValueError, match='design audit'):
        planner.generate_candidates(plan['session_id'], selector, actor='fixture')
    assert selector.contexts == []


def test_declared_unit_cannot_hide_repeated_donor_column(tmp_path):
    rows = [{'sample_id': f's{i+1}', 'donor_id': 'same', 'condition': c, 'batch': 'x'}
            for i, c in enumerate(['A', 'A', 'B', 'B'])]
    _, plan, actions = prepared_plan(tmp_path, rows=rows)
    stopped = actions.perform('audit', actions.create_state(plan['session_id']))
    assert stopped['status'] == 'abstained'


def test_unknown_selected_sample_cannot_advance(tmp_path):
    _, plan, actions = prepared_plan(tmp_path, declaration=design(['unknown']))
    result = actions.perform('audit', actions.create_state(plan['session_id']))
    assert result['status'] == 'review_required'


def test_legacy_plan_has_no_design_audit_claim(tmp_path):
    from tests.test_langgraph_workflow import rna_plan
    planner, plan, *_ = rna_plan(tmp_path)
    actions = WorkflowActions(planner, ledger(tmp_path), actor='fixture', selector=None)
    result = actions.perform('audit', actions.create_state(plan['session_id']))
    assert result['status'] == 'ready'
    assert 'evidence' not in actions.ledger.get(plan['session_id'], 'audit', 0)['result']


def test_verified_design_reaches_selector_without_invented_method(tmp_path):
    from tests.test_method_planner import Selector
    from devbench.analysis.workflow_entrypoints import LEDGER_NAME
    from devbench.analysis.workflow_operations import WorkflowOperationLedger
    planner, plan, _ = prepared_plan(tmp_path)
    actions = WorkflowActions(planner, WorkflowOperationLedger(planner.methods.root / LEDGER_NAME), actor='fixture', selector=None)
    actions.perform('audit', actions.create_state(plan['session_id']))
    selector = Selector({'candidates': []})
    planner.generate_candidates(plan['session_id'], selector, actor='fixture')
    context = selector.contexts[0]
    assert context['rna_design'] == design()
    assert context['rna_design_audit']['status'] == 'passed'
    assert 'test' not in context['rna_design']['design']


def test_companion_contrast_cannot_disagree_with_design(tmp_path):
    from tests.test_rna_scientific_contract import registered_bulk_contract, TASK
    planner, summary, binding, companion = registered_bulk_contract(tmp_path)
    companion['statistical_plan']['contrast'] = {'factor': 'condition', 'numerator': 'C', 'denominator': 'D'}
    with pytest.raises(ValueError, match='contrast'):
        planner.create(TASK, summary, actor='fixture', rna_input_contract=binding,
            scientific_contract=companion, rna_design=design(['library-1']))


def test_companion_blocking_unknown_remains_information_needed(tmp_path):
    from tests.test_rna_scientific_contract import registered_bulk_contract, TASK
    planner, summary, binding, companion = registered_bulk_contract(tmp_path)
    declaration = design(['library-1'])
    declaration['objective']['intent'] = 'descriptive'
    declaration['design']['contrasts'] = []
    plan = planner.create(TASK, summary, actor='fixture', rna_input_contract=binding,
        scientific_contract=companion, rna_design=declaration)
    actions = WorkflowActions(planner, ledger(tmp_path), actor='fixture', selector=None)
    stopped = actions.perform('audit', actions.create_state(plan['session_id']))
    assert stopped['status'] == 'review_required'
    evidence = actions.ledger.get(plan['session_id'], 'audit', 0)['result']['evidence']
    assert 'scientific_contract_unknown:biological_units' in evidence['information_required']


def test_audit_is_independent_of_later_scientific_runtime(tmp_path):
    from tests.support.workflow_science_fixtures import RecordingScientificRuntime
    planner, plan, actions = prepared_plan(tmp_path)
    ready = actions.perform('audit', actions.create_state(plan['session_id']))
    later = WorkflowActions(planner, actions.ledger, actor='fixture', selector=None,
        scientific_workflow=RecordingScientificRuntime())
    assert later.check(ready)


def test_inferential_run_without_execution_bridge_is_refused(tmp_path):
    from devbench.analysis.workflow_entrypoints import LEDGER_NAME
    from devbench.analysis.workflow_operations import WorkflowOperationLedger
    planner, plan, _ = prepared_plan(tmp_path)
    actions = WorkflowActions(planner, WorkflowOperationLedger(planner.methods.root / LEDGER_NAME), actor='fixture', selector=None)
    actions.perform('audit', actions.create_state(plan['session_id']))
    with pytest.raises(ValueError, match='execution input bridge'):
        planner.run_trial(plan['session_id'], 'not-an-experiment', actor='fixture', allow_data_execution=True)


def test_reversed_bridge_contrast_does_not_authorize_audited_design(tmp_path):
    from devbench.analysis.rna_design_audit import require_rna_design_execution
    from devbench.analysis.rna_execution_inputs import prepare_execution_inputs
    from tests.test_rna_execution_inputs import inputs, ports
    # Sealed evidence intentionally rejects OS scratch. Use a temporary protected
    # fixture workspace explicitly, without changing TMPDIR or policy.
    with tempfile.TemporaryDirectory(prefix=".rna-design-test-", dir=Path(__file__).resolve().parents[1]) as folder:
        workspace = Path(folder)
        service, binding, *_ = inputs(workspace)
        selected = ports(infer=True)
        selected['design']['contrasts'] = [{'field': 'group', 'reference': 'B', 'target': 'A'}]
        bridge = prepare_execution_inputs(binding, selected, artifact_service=service, output=workspace / 'bridge')
        declaration = design()
        declaration['design']['contrasts'][0]['field'] = 'group'
        plan = {'rna_design': declaration, 'rna_design_sha256': digest(declaration),
            'rna_execution_inputs': {'record_ref': bridge, 'dataset_binding': binding}}
        with pytest.raises(ValueError, match='design'):
            require_rna_design_execution(plan, service)


def test_registered_reader_does_not_certify_inferential_formula_semantics(tmp_path):
    from devbench.analysis.rna_design_audit import require_rna_design_execution
    from devbench.analysis.rna_execution_inputs import prepare_execution_inputs
    from tests.test_rna_execution_inputs import inputs, registered_ports
    with tempfile.TemporaryDirectory(prefix='.rna-design-test-', dir=Path(__file__).resolve().parents[1]) as folder:
        workspace = Path(folder)
        service, binding, *_ = inputs(workspace)
        selected = registered_ports(service, infer=True)
        selected['parameters']['formula_str'] = '~ C(batch)'
        bridge = prepare_execution_inputs(binding, selected, artifact_service=service, output=workspace / 'bridge')
        declaration = design()
        declaration['design']['contrasts'][0]['field'] = 'group'
        plan = {'rna_design': declaration, 'rna_design_sha256': digest(declaration),
            'rna_execution_inputs': {'record_ref': bridge, 'dataset_binding': binding}}
        with pytest.raises(ValueError, match='inferential_design_to_method_binding_not_implemented'):
            require_rna_design_execution(plan, service)
