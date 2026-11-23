"""Controlled native composer episodes; no model process or external service."""
from copy import deepcopy
import json

import pytest

from tests.test_analysis_episodes import workspace as workspace  # noqa: F401
from tests.test_workbench_episode_intake import record as intake_record


class ControlledSelector:
    def __init__(self, *, clarify=False):
        self.calls = []
        self.clarify = clarify

    def configuration(self):
        return {'backend': 'recorded_planning_answer', 'fixture': 'native-composer-episode'}

    def select(self, context, output, **kwargs):
        from devbench.analysis.episode_agent import Selection
        assert kwargs['allow_model_execution'] is False
        self.calls.append(deepcopy(context))
        evidence = {'backend': 'controlled_fixture', 'model_sessions_attempted': 0,
            'tokens': None, 'cost_usd': None, 'actual_model': None, 'model_identity_verified': False}
        if context.get('decision_kind') == 'analysis_proposal':
            return Selection({'kind': 'propose', 'workflow': 'bulk_mean_contrast',
                'reference_condition': 'A', 'comparison_condition': 'B',
                'rationale': 'Controlled local test of the declared contrast.'}, evidence)
        # Operator clarification annotations are retained alongside actions.
        history = [item for item in context['history'] if 'action' in item]
        if self.clarify and not history:
            action = {'kind': 'request_information', 'message': 'Confirm the supplied provenance annotation.'}
        else:
            tools = ['audit_expression_quality', 'validate_biological_units', 'validate_design_matrix', 'mean_condition_analysis']
            done = [item['action'].get('tool') for item in history if item['action']['kind'] == 'tool']
            if len(done) < len(tools):
                action = {'kind': 'tool', 'tool': tools[len(done)]}
            elif not any(item['action']['kind'] == 'query_evidence' for item in history):
                source = next(index for index, item in enumerate(history, 1)
                    if item['action'].get('tool') == 'mean_condition_analysis')
                action = {'kind': 'query_evidence', 'source_action': source,
                    'query': {'mode': 'gene_statistics', 'limit': 2}}
            else:
                action = {'kind': 'finish', 'disposition': 'completed',
                    'reason': 'Controlled bounded execution; biological agreement remains unevaluated.',
                    'followup_proposals': []}
        return Selection(action, evidence, decision_record={
            'question': 'What does the retained diagnostic establish?',
            'concern': 'The next observation may reveal a design or measurement failure.',
            'evidence_refs': [], 'competing_explanations': ['Supported execution', 'Unsupported experimental design'],
            'expected_discriminating_observation': 'Independent retained tool diagnostics or bounded query output.'})


def setup(workspace, *, declarations=True, clarify=False):  # noqa: F811
    from devbench.analysis.workbench_ui import WorkbenchService
    value = intake_record(workspace)
    selector = ControlledSelector(clarify=clarify)
    app = WorkbenchService(workspace/'service', [workspace/'source'],
        proposal_selector=selector, allow_data_execution=True)
    context = deepcopy(value['scientific_context'])
    if not declarations:
        context['bulk_episode_declarations'] = {}
    request = {'question': value['question'], 'sources': value['sources'],
        'source_declarations': value['source_declarations'], 'scientific_context': context,
        'analyst_profile': 'analyst_v1', 'proposal_mode': 'agent'}
    return app, selector, request


def settle(app, identity):
    app._futures[identity].result(timeout=120)


def approve(app, investigation):
    return app.investigations.action(investigation['id'], {'action': 'review',
        'expected_revision': investigation['revision'], 'reviewer': 'controlled fixture operator',
        'decision': 'approve', 'rationale': 'Software execution test only.',
        'confirmations': {key: True for key in ('question_and_contrast', 'input_provenance',
            'biological_units_and_design', 'limitations')}})


def test_normal_composer_clarifies_units_without_selector_or_job(workspace):  # noqa: F811
    app, selector, request = setup(workspace, declarations=False)
    try:
        result = app.investigations.create(request)
        assert result['status'] == 'awaiting_information', result
        assert result['job_id'] is None
        assert {'population_unit_column', 'contrast_numerator', 'contrast_denominator'} <= {
            field['field'] for field in result['clarification_fields']}
        assert not selector.calls
        assert not app.jobs()
    finally:
        app.close()


def test_review_response_immediately_exposes_current_episode_state(workspace):  # noqa: F811
    app, _, request = setup(workspace)
    try:
        initial = app.investigations.create(request)
        settle(app, initial['job_id'])
        planned = app.investigations.get(initial['id'])
        approved = approve(app, planned)
        assert approved['status'] == approved['job_state']['status'] == 'approved'
        context, inheritance = app.investigations._follow_up_context(planned, {})
        declarations = context['bulk_episode_declarations']
        assert declarations['population_unit_column'] == request['scientific_context']['bulk_episode_declarations']['population_unit_column']
        assert 'contrast_numerator' not in declarations and 'contrast_denominator' not in declarations
        assert context.get('allow_model_execution') is not True
    finally:
        app.close()


def test_normal_composer_runs_real_reviewed_episode_and_projects_queries(workspace):  # noqa: F811
    app, selector, request = setup(workspace)
    originals = {path: open(path, 'rb').read() for path in request['sources']}
    try:
        initial = app.investigations.create(request)
        assert initial['job_id'] is not None, initial
        settle(app, initial['job_id'])
        planned = app.investigations.get(initial['id'])
        assert planned['status'] == 'awaiting_operator_review', planned.get('job_state')
        assert planned['job_state']['operation_kind'] == 'agentic_episode'
        assert planned['job_state']['intake_binding']['investigation_id'] == initial['id']
        approved = approve(app, planned)
        app.investigations.action(initial['id'], {'action': 'run',
            'expected_revision': approved['revision'], 'allow_data_execution': True})
        settle(app, initial['job_id'])
        final = app.investigations.get(initial['id'])
        assert final['status'] == 'completed', final.get('job_state')
        assert final['job_state']['episode_verification']['valid']
        assert final['job_state']['summary']['scientific_correctness'] is None
        assert any(turn['content'].get('query_result') for turn in final['turns'])
        assert final['artifacts'] and all(item['sha256'] for item in final['artifacts'])
        assert all(context.get('analyst_profile') == 'analyst_v1' for context in selector.calls[1:])
        assert originals == {path: open(path, 'rb').read() for path in request['sources']}
        before = len(final['turns'])
        assert len(app.investigations.get(initial['id'])['turns']) == before
        path, _, _ = app.agentic_episodes.download(initial['job_id'], 'synthesis')
        assert json.loads(path.read_text())['scientific_correctness'] is None
        # A forged response and its adjacent checksum must still fail against
        # the independently bound audit node; the adjacent receipt is mutable.
        from devbench.analysis.checkpoints import atomic_json, sha256
        query = next(app._path('jobs', initial['job_id'], 'episode', 'checkpoints').glob('query-*/query-observation.json'))
        forged = json.loads(query.read_text())
        forged['query_result']['total_matches'] = 999999
        atomic_json(query, forged)
        receipt_path = query.parent / 'receipt.json'
        receipt = json.loads(receipt_path.read_text())
        receipt['artifacts'][query.name] = sha256(query)
        atomic_json(receipt_path, receipt)
        with pytest.raises(ValueError, match='query|audit|checkpoint'):
            app.agentic_episodes.status(initial['job_id'])
    finally:
        app.close()


@pytest.mark.parametrize('cancel_before_answer', [False, True])
def test_episode_handoff_requires_explicit_answer_and_resumes_same_job(workspace, cancel_before_answer):  # noqa: F811
    app, selector, request = setup(workspace, clarify=True)
    try:
        initial = app.investigations.create(request)
        settle(app, initial['job_id'])
        approved = approve(app, app.investigations.get(initial['id']))
        app.investigations.action(initial['id'], {'action': 'run',
            'expected_revision': approved['revision'], 'allow_data_execution': True})
        settle(app, initial['job_id'])
        paused = app.investigations.get(initial['id'])
        assert paused['status'] == 'awaiting_information', paused.get('job_state')
        assert paused['job_state']['handoff'] and paused['job_state']['resumable']
        if cancel_before_answer:
            paused = app.investigations.action(initial['id'], {'action':'cancel',
                'expected_revision':paused['revision']})
            assert paused['job_state']['operation_kind'] == 'agentic_episode'
            assert paused['job_state']['handoff'] and paused['job_state']['status'] == 'cancelled'
        result = app.investigations.action(initial['id'], {'action': 'clarify_episode',
            'expected_revision': paused['revision'], 'allow_data_execution': True,
            'response': {'message': 'The annotation describes the unchanged synthetic records.',
                'preserves_frozen_inputs_and_settings': True}})
        assert result['job_id'] == initial['job_id']
        settle(app, initial['job_id'])
        final = app.investigations.get(initial['id'])
        assert final['status'] == 'completed', final.get('job_state')
        assert len(app.jobs()) == 1
    finally:
        app.close()


def test_queued_planning_rejects_changed_selected_snapshot_before_selector(workspace, monkeypatch):  # noqa: F811
    from pathlib import Path
    app, selector, request = setup(workspace)
    pending = []
    monkeypatch.setattr(app, '_submit', lambda identity, operation: pending.append(operation))
    try:
        result = app.investigations.create(request)
        path = Path(request['sources'][0])
        path.write_text(path.read_text() + '\n')
        with pytest.raises(ValueError, match='snapshot|changed'):
            pending[0]()
        assert not selector.calls
        assert app._job(result['job_id']).get('proposal_receipt') is None
    finally:
        app.close()


def test_proposal_cannot_publish_different_bytes_after_intake_recheck(workspace, monkeypatch):  # noqa: F811
    from pathlib import Path
    app, selector, request = setup(workspace)
    pending = []
    monkeypatch.setattr(app, '_submit', lambda identity, operation: pending.append(operation))
    def changed_selector():
        path = Path(request['sources'][0])
        old = path.read_text()
        changed = old.replace(',10,', ',11,', 1)
        assert changed != old
        path.write_text(changed)
        return selector
    monkeypatch.setattr(app.agentic_episodes, '_selector', changed_selector)
    try:
        result = app.investigations.create(request)
        with pytest.raises(ValueError, match='selected intake snapshots'):
            pending[0]()
        assert selector.calls
        assert app._job(result['job_id']).get('proposal_receipt') is None
    finally:
        app.close()


def test_episode_owner_rejects_ordinary_scientific_dispatch_and_projection():
    from types import SimpleNamespace
    from devbench.analysis.workbench_investigations import InvestigationService
    service = InvestigationService.__new__(InvestigationService)
    service.app = SimpleNamespace(scientific=SimpleNamespace(catalog=lambda: pytest.fail('Competing callback ran')))
    record = {'analyst_profile': 'analyst_v1', 'scientific_operations': ['competing'],
              'scientific_session_id': 'competing-session',
              'scientific_context': {'auto_synthesis': True},
              'scientific_plan': {'experiments': [{'status': 'completed'}]}}
    with pytest.raises(RuntimeError, match='driver|episode'):
        service._scientific(record, {'action': 'recommend'})
    service._sync_scientific(record)
    assert service._synthesis_descriptor(record) is None


def test_live_query_projection_has_one_action_and_one_outcome_across_polls():
    from types import SimpleNamespace
    from devbench.analysis.workbench_investigations import InvestigationService
    state = {'id': 'j', 'investigation_id': 'i', 'thread_id': 't', 'status': 'running',
        'agent_selections': [{'action': {'kind': 'query_evidence'}, 'decision_record': {'concern': 'count support'}}]}
    service = InvestigationService.__new__(InvestigationService)
    service.app = SimpleNamespace(agentic_episodes=SimpleNamespace(status=lambda job: deepcopy(state)))
    service._turn = lambda record, kind, title, content, **kwargs: record['turns'].append({'kind': kind, 'content': content})
    record = {'id': 'i', 'thread_id': 't', 'job_id': 'j', 'turns': []}
    service._sync_episode(record)
    state['agent_selections'][0].update(query_result={'total_matches': 12}, query_receipt={'action': 1})
    service._sync_episode(record)
    service._sync_episode(record)
    assert len([turn for turn in record['turns'] if turn['kind'] == 'analyst_action']) == 1
    assert len([turn for turn in record['turns'] if turn['content'].get('query_result')]) == 1


def test_native_sized_selection_capture_stays_in_receipts_not_live_record(workspace):  # noqa: F811
    from devbench.analysis.checkpoints import sha256
    from devbench.analysis.episode_agent import Selection
    from devbench.analysis.workbench_investigations import MAX_RECORD_BYTES

    class NativeSizedSelector(ControlledSelector):
        def select(self, context, output, **kwargs):
            choice = super().select(context, output, **kwargs)
            if choice.action['kind'] == 'finish':
                queries = sum(item.get('action', {}).get('kind') == 'query_evidence' for item in context['history'])
                if queries < 4:
                    choice = Selection({'kind': 'query_evidence', 'source_action': 4,
                        'query': {'mode': 'gene_statistics', 'limit': 2, 'offset': queries * 2}},
                        choice.evidence, choice.decision_record)
            evidence = {**choice.evidence,
                'actual_model': 'controlled-fixture-model', 'model_identity_verified': True,
                'model_sessions_attempted': 2, 'tokens': 1234, 'cost_usd': None,
                'selection': {'session_id': 'fixture-session', 'tokens': 1000,
                    'usage': {'input_tokens': 800, 'output_tokens': 200}},
                'preflight': {'session_id': 'fixture-preflight', 'actual_model': 'controlled-fixture-model',
                    'billing': {'tokens': 234, 'usage': {'input_tokens': 200, 'output_tokens': 34}}},
                'staged_environment': {'packages': {f'package-{i}': '1.0' for i in range(6000)}},
                'runtime_roots': ['/controlled/runtime/' + str(i) for i in range(2000)]}
            return Selection(choice.action, evidence, choice.decision_record)

    app, _, request = setup(workspace)
    app.proposal_selector = NativeSizedSelector()
    try:
        initial = app.investigations.create(request)
        settle(app, initial['job_id'])
        approved = approve(app, app.investigations.get(initial['id']))
        app.investigations.action(initial['id'], {'action': 'run',
            'expected_revision': approved['revision'], 'allow_data_execution': True})
        settle(app, initial['job_id'])
        final = app.investigations.get(initial['id'])
        assert final['status'] == 'completed', final.get('job_state')
        selections = final['job_state']['agent_selections']
        assert len(selections) == 9
        root = app._path('jobs', initial['job_id'], 'episode')
        retained = {}
        for selection in selections:
            assert set(selection) <= {'action', 'decision_record', 'selection_ref', 'evidence',
                'query_result', 'query_receipt_ref', 'query_outcome', 'status', 'state'}
            reference = selection['selection_ref']
            path = root / reference['path']
            assert reference['sha256'] == sha256(path)
            assert reference['size_bytes'] == path.stat().st_size > 150_000
            retained[path] = path.read_bytes()
            original = json.loads(retained[path])
            expected = dict(original['action'])
            if 'reason' in expected:
                expected['reason'] = 'operation_unavailable'
            assert selection['action'] == expected
            assert selection['decision_record'] == original['decision_record']
            assert 'staged_environment' not in selection['evidence']
            assert 'runtime_roots' not in selection['evidence']
            for key in ('actual_model', 'model_identity_verified', 'model_sessions_attempted', 'tokens', 'cost_usd'):
                assert selection['evidence'][key] == original['evidence'][key]
            assert selection['evidence']['selection']['session_id'] == 'fixture-session'
            assert selection['evidence']['selection']['usage'] == {'input_tokens': 800, 'output_tokens': 200}
            assert selection['evidence']['preflight']['billing'] == original['evidence']['preflight']['billing']
            assert len(json.dumps(selection).encode()) < 24 * 1024
        query_results = []
        for number, selection in enumerate(selections, 1):
            if 'query_result' not in selection:
                continue
            query_path = root / 'checkpoints' / f'query-{number:04d}' / 'query-observation.json'
            query = json.loads(query_path.read_text())
            assert selection['query_result']['matched_rows'] == query['query_result']['matched_rows']
            assert 'rows' not in selection['query_result']
            assert selection['query_receipt_ref']['action'] == query['query_receipt']['action']
            assert len(query_path.read_bytes()) <= 12 * 1024
            retained[query_path] = query_path.read_bytes()
            query_results.append(selection['query_result'])
        assert len(query_results) == 4
        assert [turn['content']['query_result'] for turn in final['turns']
            if turn['kind'] == 'analyst_query'] == query_results
        turns = deepcopy(final['turns'])
        persisted = app.investigations._path(initial['id'])
        for _ in range(3):
            assert app.investigations.get(initial['id'])['turns'] == turns
            assert persisted.stat().st_size <= MAX_RECORD_BYTES
        assert retained == {path: path.read_bytes() for path in retained}
        assert app.investigations._load(initial['id'])['status'] == 'completed'
    finally:
        app.close()


def test_record_write_checks_actual_indented_bytes_before_replacing(workspace):  # noqa: F811
    from devbench.analysis.workbench_investigations import MAX_RECORD_BYTES
    app, _, request = setup(workspace, declarations=False)
    try:
        initial = app.investigations.create(request)
        path = app.investigations._path(initial['id'])
        before = path.read_bytes()
        record = app.investigations._load(initial['id'])
        # This fits the old compact-copy guard but its retained representation
        # exceeds the reader's bound because of nested indentation overhead.
        record['view_padding'] = [{'a': {'b': 'x'}} for _ in range(45_000)]
        assert len(json.dumps(record).encode()) < MAX_RECORD_BYTES
        assert len((json.dumps(record, indent=2) + '\n').encode()) > MAX_RECORD_BYTES
        revision = record['revision']
        with pytest.raises(ValueError, match='retained.*size bound'):
            app.investigations._save(record)
        assert path.read_bytes() == before
        assert record['revision'] == revision
        assert app.investigations._load(initial['id'])['revision'] == revision
    finally:
        app.close()


def test_query_status_rejects_adjacent_checksums_without_audit_binding(tmp_path):
    from types import SimpleNamespace
    from devbench.analysis.workbench_agentic import InvestigationEpisodeController
    from devbench.analysis.checkpoints import atomic_json, sha256
    root = tmp_path / 'jobs' / 'j' / 'episode'
    selection = root / 'agent' / 'selection-0002' / 'selection.json'
    selection.parent.mkdir(parents=True)
    atomic_json(selection, {'action': {'kind': 'query_evidence', 'source_action': 1,
        'query': {'mode': 'gene_statistics', 'limit': 2}}})
    query = root / 'checkpoints' / 'query-0002' / 'query-observation.json'
    query.parent.mkdir(parents=True)
    atomic_json(query, {'query_result': {'total_matches': 999999}, 'query_receipt': {}})
    atomic_json(query.parent / 'receipt.json', {'artifacts': {query.name: sha256(query)}})
    row = {'id': 'j', 'operation_kind': 'agentic_episode', 'analyst_profile': 'analyst_v1',
           'status': 'completed', 'episode_receipt': {'root_hash': 'a' * 64}}
    app = SimpleNamespace(_job=lambda identity: deepcopy(row), status=lambda identity: deepcopy(row),
        _path=lambda *parts: tmp_path.joinpath(*parts))
    with pytest.raises(ValueError, match='query|audit|checkpoint'):
        InvestigationEpisodeController(app).status('j')


def test_selection_capture_cannot_supply_unverified_numeric_query_view(tmp_path):
    from types import SimpleNamespace
    from devbench.analysis.workbench_agentic import InvestigationEpisodeController
    from devbench.analysis.checkpoints import atomic_json
    selection = tmp_path / 'jobs' / 'j' / 'episode' / 'agent' / 'selection-0002' / 'selection.json'
    selection.parent.mkdir(parents=True)
    atomic_json(selection, {'action': {'kind': 'query_evidence', 'source_action': 1,
        'query': {'mode': 'gene_statistics', 'limit': 2}}, 'evidence': {},
        'query_result': {'total_matches': 999999, 'warnings': []}, 'query_receipt': {'action': 2}})
    row = {'id': 'j', 'operation_kind': 'agentic_episode', 'analyst_profile': 'analyst_v1', 'status': 'running'}
    app = SimpleNamespace(_job=lambda identity: deepcopy(row), status=lambda identity: deepcopy(row),
        _path=lambda *parts: tmp_path.joinpath(*parts))
    observed = InvestigationEpisodeController(app).status('j')['agent_selections'][0]
    assert 'query_result' not in observed and 'query_receipt' not in observed


def test_query_rejection_is_visible_once_bound_and_tamper_checked(workspace):  # noqa: F811
    import csv
    from pathlib import Path
    from devbench.analysis.checkpoints import atomic_json, digest
    from devbench.analysis.episode_agent import Selection

    class RejectedQuerySelector(ControlledSelector):
        def select(self, context, output, **kwargs):
            choice = super().select(context, output, **kwargs)
            if choice.action['kind'] == 'query_evidence':
                return Selection({'kind': 'query_evidence', 'source_action': 4,
                    'query': {'mode': 'paired', 'gene_ids': ['g0'],
                        'pairs': [{'reference': 'sample_not_in_matrix', 'comparison': 's4'}]}},
                    choice.evidence, choice.decision_record)
            if choice.action['kind'] == 'finish':
                queries = [item for item in context['history'] if item.get('action', {}).get('kind') == 'query_evidence']
                if len(queries) == 1:
                    return Selection({'kind': 'query_evidence', 'source_action': 4,
                        'query': {'mode': 'paired', 'layer': 'normalized', 'gene_ids': ['g0']}},
                        choice.evidence, choice.decision_record)
            return choice

    app, _, request = setup(workspace)
    app.proposal_selector = RejectedQuerySelector()
    # This generated fixture explicitly declares four donors observed once in
    # each condition. The successful correction uses those retained pairings.
    metadata = Path(request['sources'][1])
    with metadata.open(newline='') as handle:
        rows = list(csv.DictReader(handle))
    for number, row in enumerate(rows):
        row['donor_id'] = 'd' + str(number % 4)
    with metadata.open('w', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    request['scientific_context']['bulk_episode_declarations']['dependence'] = 'paired'
    try:
        initial = app.investigations.create(request)
        settle(app, initial['job_id'])
        approved = approve(app, app.investigations.get(initial['id']))
        app.investigations.action(initial['id'], {'action': 'run',
            'expected_revision': approved['revision'], 'allow_data_execution': True})
        settle(app, initial['job_id'])
        final = app.investigations.get(initial['id'])
        assert final['status'] == 'completed', final.get('job_state')
        assert final['job_state']['episode_verification']['valid']
        selection = final['job_state']['agent_selections'][4]
        outcome = selection['query_outcome']
        diagnostic = 'evidence_query_rejected:evidence_query_sample_axis_mismatch'
        assert outcome['format'] == 'devbench.query-rejection-view/1'
        assert outcome['status'] == 'rejected' and outcome['state'] == 'needs_revision'
        assert outcome['message'] == outcome['reason'] == diagnostic
        assert outcome['action'] == 5 and outcome['source_action'] == 4
        assert outcome['query_sha256'] == digest(selection['action']['query'])
        assert 'query_receipt' not in selection and 'query_result' not in selection
        root = app._path('jobs', initial['job_id'], 'episode')
        reference = outcome['audit_ref']
        node_path = root / reference['path']
        node = json.loads(node_path.read_text())
        assert reference['node_id'] == node['node_id']
        assert node['kind'] == 'episode.action_rejected' and node['payload']['diagnostic'] == diagnostic
        successful = final['job_state']['agent_selections'][5]
        assert successful['query_receipt_ref']['action'] == 6 and 'query_result' in successful
        assert successful['query_result']['pairing_declarations']['distinct_declared_units'] == 4
        query_turns = [turn['content'] for turn in final['turns'] if turn['kind'] == 'analyst_query']
        assert len(query_turns) == 2
        assert query_turns[0]['status'] == 'rejected' and query_turns[0]['message'] == diagnostic
        assert query_turns[0]['query_outcome'] == outcome
        assert 'query_result' not in query_turns[0] and 'query_receipt' not in query_turns[0]
        turns = deepcopy(final['turns'])
        for _ in range(3):
            assert app.investigations.get(initial['id'])['turns'] == turns
        node['payload']['diagnostic'] = 'forged rejection'
        atomic_json(node_path, node)
        with pytest.raises(ValueError, match='query|audit'):
            app.agentic_episodes.status(initial['job_id'])
    finally:
        app.close()


def test_proposal_information_answer_creates_linked_revised_investigation(workspace):  # noqa: F811
    from devbench.analysis.episode_agent import Selection
    class PlanningClarifier(ControlledSelector):
        def select(self, context, output, **kwargs):
            if context.get('decision_kind') == 'analysis_proposal' and not self.calls:
                self.calls.append(deepcopy(context))
                return Selection({'kind': 'request_information', 'message': 'Is the objective descriptive association?'},
                    {'backend': 'controlled_fixture', 'model_sessions_attempted': 0, 'tokens': None,
                     'cost_usd': None, 'actual_model': None, 'model_identity_verified': False})
            return super().select(context, output, **kwargs)
    app, _, request = setup(workspace)
    app.proposal_selector = PlanningClarifier()
    try:
        first = app.investigations.create(request)
        settle(app, first['job_id'])
        paused = app.investigations.get(first['id'])
        assert paused['status'] == 'awaiting_information'
        assert not paused['job_state'].get('handoff')
        child = app.investigations.action(first['id'], {'action': 'clarify_episode',
            'expected_revision': paused['revision'], 'allow_data_execution': True,
            'response': {'message': 'Yes, the objective is descriptive association.',
                'preserves_frozen_inputs_and_settings': True}})
        assert child['id'] != first['id'] and child['parent_investigation'] == first['id']
        assert child['thread_id'] == first['thread_id'] and child['job_id'] != first['job_id']
        assert 'descriptive association' in child['question']
        settle(app, child['job_id'])
        assert app.investigations.get(child['id'])['status'] == 'awaiting_operator_review'
        assert app.investigations.get(first['id'])['status'] == 'awaiting_information'
    finally:
        app.close()
