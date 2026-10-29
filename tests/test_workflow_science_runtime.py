"""Offline runtime tests exercise the real discovery and combined-review services."""
from copy import deepcopy
import json

import pytest

from tests.test_analysis_episodes import workspace
from tests.test_workflow_scientific_reviews import setup
from devbench.analysis.checkpoints import digest


def runtime_fixture(workspace, **kwargs):
    from devbench.analysis.workflow_science import ScientificWorkflowRuntime
    planner, reviewer, selector, diagnostic = setup(workspace, **kwargs)
    original = planner.get
    def get(session):
        state = original(session)
        with planner.methods._db() as db:
            if db.execute("SELECT 1 FROM records WHERE kind='method_plan_snapshot' AND id=?", (digest(state),)).fetchone() is None:
                planner.methods._put(db, 'method_plan_snapshot', digest(state), state)
        return state
    planner.get = get
    runtime = ScientificWorkflowRuntime(discovery_provider=None, discovery_request={"mode": "supplied_only",
        "reason": "Offline synthetic control", "query": None, "allowed_hosts": [], "acquire_indices": []},
        reviewer=reviewer, review_policy={})
    return planner, runtime, selector, diagnostic


def test_real_review_static_verification_and_no_duplicate_dispatch(workspace):
    from devbench.analysis.workflow_science import verify_scientific_evidence
    planner, runtime, selector, _ = runtime_fixture(workspace)
    evidence = runtime.perform('candidate_review', planner, 'synthetic-session', actor='fixture', selector=selector)
    assert evidence['status'] == 'accepted' and selector.calls == 1
    verify_scientific_evidence('candidate_review', planner, 'synthetic-session', evidence,
        plan_snapshot=planner.get('synthetic-session'))
    assert runtime.reconcile('candidate_review', planner, 'synthetic-session',
        before_planner_hash=evidence['before_planner_hash']) == evidence
    assert runtime.perform('candidate_review', planner, 'synthetic-session', actor='fixture', selector=selector) == evidence
    assert selector.calls == 1


def test_review_mutation_and_frozen_configuration_rejected(workspace):
    planner, runtime, selector, diagnostic = runtime_fixture(workspace)
    evidence = runtime.perform('candidate_review', planner, 'synthetic-session', actor='fixture', selector=selector)
    runtime.review_policy['allow_descriptive'] = True
    with pytest.raises(ValueError, match='configuration|policy'):
        runtime.verify('candidate_review', planner, 'synthetic-session', evidence)
    runtime.review_policy.clear()
    diagnostic.write_text('{"value": 0.8}')
    with pytest.raises(ValueError, match='changed'):
        runtime.verify('candidate_review', planner, 'synthetic-session', evidence)


def test_supplied_discovery_is_real_envelope_and_has_no_registry(workspace):
    planner, runtime, selector, _ = runtime_fixture(workspace)
    planner.state['scientific_candidates'] = None
    planner.state['selection_in_progress'] = None
    evidence = runtime.perform('discovery', planner, 'synthetic-session', actor='fixture', selector=selector)
    assert evidence['status'] == 'descriptive'
    context = runtime.generation_context(planner, 'synthetic-session')
    assert set(context) == {'record_ref', 'before_planner_hash', 'after_planner_hash', 'status'}
    from devbench.analysis.workflow_discovery import load_discovery
    assert 'registry' not in json.dumps(load_discovery(planner, context, session_id='synthetic-session')['context'])
    assert selector.calls == 0


def test_uncertain_dispatch_cannot_be_retried(workspace):
    planner, runtime, selector, _ = runtime_fixture(workspace)
    def fail(*args):
        raise RuntimeError('simulated crash')
    runtime.reviewer.claim_packet_builder = fail
    # Builder identity remains the same frozen runtime type, but real dispatch fails.
    with pytest.raises(RuntimeError, match='simulated crash'):
        runtime.perform('candidate_review', planner, 'synthetic-session', actor='fixture', selector=selector)
    with pytest.raises(ValueError, match='uncertain|reconcil'):
        runtime.perform('candidate_review', planner, 'synthetic-session', actor='fixture', selector=selector)
    assert selector.calls == 0


def test_review_stale_current_material_and_wrong_snapshot_rejected(workspace):
    from devbench.analysis.workflow_science import verify_scientific_evidence
    planner, runtime, selector, _ = runtime_fixture(workspace)
    evidence = runtime.perform('candidate_review', planner, 'synthetic-session', actor='fixture', selector=selector)
    snapshot = planner.get('synthetic-session')
    altered = deepcopy(snapshot)
    altered['scientific_candidates'][0]['name'] = 'different'
    with pytest.raises(ValueError, match='snapshot'):
        verify_scientific_evidence('candidate_review', planner, 'synthetic-session', evidence, plan_snapshot=altered)
    planner.state = altered
    with pytest.raises(ValueError, match='material|stale'):
        runtime.verify('candidate_review', planner, 'synthetic-session', evidence, plan_snapshot=snapshot)


def test_real_serial_graph_dispatches_all_three_actual_scientific_reviews(workspace):
    from devbench.analysis.workflow_science import ScientificWorkflowRuntime
    from devbench.analysis.workflow_actions import WorkflowActions
    from devbench.analysis.langgraph_workflow import SerialRNAWorkflow
    from tests.test_langgraph_workflow import rna_plan, ledger
    from tests.test_method_planner import Selector as PlannerSelector, candidate, recommendation
    planner, plan, analysis, _, _ = rna_plan(workspace)
    # The real reviewer uses a real verified synthetic diagnostic, bound to each
    # stage's actual material. No source/search or scientific qualification is claimed.
    review_root = workspace / 'review-fixture'
    review_root.mkdir()
    _, reviewer, review_selector, _ = setup(review_root)
    item = candidate('Unavailable synthetic procedure')
    item['assumptions'] = []
    selector = PlannerSelector({'candidates': [item]}, {'bindings': [
        {'candidate': item['name'], 'method_version_id': None, 'discovery': None}]},
        recommendation(None))
    runtime = ScientificWorkflowRuntime(discovery_provider=None,
        discovery_request={'mode': 'supplied_only', 'reason': 'Describe the supplied synthetic input only',
            'query': None, 'allowed_hosts': [], 'acquire_indices': []}, reviewer=reviewer, review_policy={})
    actions = WorkflowActions(planner, ledger(workspace), actor='scientist', selector=selector,
        scientific_workflow=runtime)
    state = actions.create_state(plan['session_id'])
    with SerialRNAWorkflow(actions, workspace / 'actual-science-checkpoints.sqlite') as workflow:
        result = workflow.start(state)
        while '__interrupt__' in result:
            result = workflow.resume(state['workflow_id'])
        assert result['status'] == 'abstained', json.dumps(result, indent=2)
        assert workflow.resume(state['workflow_id'])['status'] == 'abstained'
    assert len(selector.contexts) == 3
    assert review_selector.calls == 3
    assert planner.methods.list_records('run') == []
    assert planner.get(plan['session_id'])['approvals'] == {}
    assert planner.methods.analysis(analysis['analysis_id'])['revision_id'] == analysis['revision_id']
    assert 'registry' not in selector.contexts[0]
    for node in ('discovery', 'candidate_review', 'implementation', 'design_review', 'conclusion_review'):
        receipt = actions.ledger.get(state['workflow_id'], node, 0)
        assert receipt['status'] == 'completed'


@pytest.mark.parametrize('lost', ['index', 'outer_record'])
def test_recovery_reuses_sealed_review_without_provider_dispatch(workspace, lost):
    planner, runtime, selector, _ = runtime_fixture(workspace)
    evidence = runtime.perform('candidate_review', planner, 'synthetic-session', actor='fixture', selector=selector)
    operation = runtime._operation('candidate_review', planner, 'synthetic-session', evidence['before_planner_hash'])
    (operation / 'completed.json').unlink()
    if lost == 'outer_record':
        (operation / 'evidence.json').unlink()
    recovered = runtime.reconcile('candidate_review', planner, 'synthetic-session',
        before_planner_hash=evidence['before_planner_hash'])
    assert recovered['status'] == 'accepted' and selector.calls == 1
    runtime.verify('candidate_review', planner, 'synthetic-session', recovered)


def test_loader_freezes_paths_before_future_packet_exists_and_binds_actual_stage_bytes(workspace):
    from devbench.analysis.workflow_science import load_scientific_workflow_config
    from devbench.analysis.workflow_scientific_reviews import stage_material
    planner, runtime, selector, _ = runtime_fixture(workspace)
    planner.state['scientific_candidates'] = None
    planner.state['selection_in_progress'] = None
    packet_path = workspace / 'future-candidate-claim.json'
    config_path = workspace / 'science.json'
    config_path.write_text(json.dumps({'provider': 'supplied_only',
        'discovery_request': runtime.discovery_request, 'review_policy': {}, 'reviewer': {
            'claim_packets': {'candidate_suitability': str(packet_path)},
            'reviewer_label': 'first', 'evaluation_policy': runtime.reviewer.evaluation_policy.model_dump(mode='json'),
            'expected_policy_sha256': runtime.reviewer.expected_policy_sha256}}))
    configured = load_scientific_workflow_config(config_path, methods_root=planner.methods.root, selector=selector)
    identity = configured.configuration()
    assert not packet_path.exists()
    assert not any(key.startswith('allow_') for key in identity)
    configured.perform('discovery', planner, 'synthetic-session', actor='fixture', selector=selector)
    assert selector.calls == 0
    planner.state['scientific_candidates'] = [{'name': 'synthetic candidate'}]
    material = stage_material(planner.get('synthetic-session'), 'candidate_suitability')
    packet = runtime.reviewer.claim_packet_builder(planner, 'synthetic-session', 'candidate_suitability', material)
    packet_path.write_text(packet.model_dump_json())
    assert configured.configuration() == identity
    evidence = configured.perform('candidate_review', planner, 'synthetic-session', actor='fixture', selector=selector)
    assert evidence['status'] == 'accepted' and selector.calls == 1
    packet_path.write_text(packet.model_dump_json() + '\n')
    with pytest.raises(ValueError, match='changed|input'):
        configured.verify('candidate_review', planner, 'synthetic-session', evidence)


def test_implementation_unavailable_is_explicit_descriptive_and_never_dispatches(workspace):
    from tests.test_method_planner import resolved, Selector
    from devbench.analysis.workflow_science import ScientificWorkflowRuntime
    planner, plan, _, _, _, _ = resolved(workspace)
    selector = Selector()
    runtime = ScientificWorkflowRuntime(discovery_provider=None, discovery_request={}, reviewer=None, review_policy={})
    evidence = runtime.perform('implementation', planner, plan['session_id'], actor='scientist', selector=selector)
    assert evidence['status'] == 'descriptive' and selector.contexts == []
    record = runtime.verify('implementation', planner, plan['session_id'], evidence)
    assert record['service_evidence']['unavailable'][0]['candidate'] == 'Unavailable preferred method'
    assert not record['service_evidence']['execution_granted']


def test_implementation_actual_draft_remains_blocked_and_source_bytes_checked(workspace):
    from tests.test_method_planner import resolved, Selector
    from tests.test_workflow_implementation import request
    from tests.test_method_authoring import implementation
    from devbench.analysis.workflow_science import ScientificWorkflowRuntime
    planner, plan, _, _, _, step = resolved(workspace)
    selector = Selector(implementation())
    runtime = ScientificWorkflowRuntime(discovery_provider=None, discovery_request={}, reviewer=None,
        review_policy={}, implementation_requests=[request(planner, step, workspace)])
    evidence = runtime.perform('implementation', planner, plan['session_id'], actor='scientist', selector=selector)
    assert evidence['status'] == 'blocked' and len(selector.contexts) == 1
    record = runtime.verify('implementation', planner, plan['session_id'], evidence)
    draft = record['service_evidence']['drafts'][0]
    method = planner.methods.method(draft['method_version_id'])
    assert method['contract']['implementation_trust'] == 'unreviewed'
    source = planner.methods.artifact_path(method['source_artifact'])
    source.chmod(0o600)
    source.write_text('changed')
    with pytest.raises(ValueError, match='changed|hash mismatch'):
        runtime.verify('implementation', planner, plan['session_id'], evidence)


@pytest.mark.parametrize('provider,expected', [
    ('supplied_only', None),
    ('crossref', 'crossref'),
    ({'kind': 'github_repositories', 'owners': ['method-author'], 'max_results': 2}, 'github_repository'),
    ({'kind': 'official_documentation', 'index_url': 'https://docs.example.org/index.html',
        'official_hosts': ['docs.example.org'], 'index_format': 'html', 'max_results': 2}, 'official_documentation'),
])
def test_fixed_config_provider_factories_do_not_search_or_grant_authority(workspace, provider, expected):
    from devbench.analysis.workflow_science import load_scientific_workflow_config
    planner, runtime, selector, _ = runtime_fixture(workspace)
    config_path = workspace / 'provider.json'
    request = runtime.discovery_request if expected is None else {
        'query': 'synthetic method', 'allowed_hosts': ['docs.example.org'], 'acquire_indices': []}
    config = {'provider': provider, 'discovery_request': request, 'review_policy': {}, 'reviewer': {
        'claim_packets': {}, 'reviewer_label': 'first',
        'evaluation_policy': runtime.reviewer.evaluation_policy.model_dump(mode='json'),
        'expected_policy_sha256': runtime.reviewer.expected_policy_sha256}}
    config_path.write_text(json.dumps(config))
    configured = load_scientific_workflow_config(config_path, methods_root=planner.methods.root, selector=selector)
    identity = configured.configuration()['discovery_provider']
    assert identity is None if expected is None else identity['provider'] == expected
    assert selector.calls == 0
    assert configured.allow_network is False and configured.allow_review_model_execution is False
    config['allow_network'] = True
    config_path.write_text(json.dumps(config))
    with pytest.raises(ValueError, match='schema'):
        load_scientific_workflow_config(config_path, methods_root=planner.methods.root, selector=selector)


def test_configured_recorded_combined_decision_passes_actual_review_verifier(workspace):
    from devbench.analysis.workflow_science import load_scientific_workflow_config
    from devbench.analysis.workflow_scientific_reviews import stage_material
    from devbench.analysis.scientific_judgment import packet_sha256
    planner, runtime, selector, _ = runtime_fixture(workspace)
    packet = runtime.reviewer.claim_packet_builder(planner, 'synthetic-session', 'candidate_suitability',
        stage_material(planner.get('synthetic-session'), 'candidate_suitability'))
    packet_path, decisions_path, config_path = (workspace / name for name in ('packet.json', 'decisions.json', 'config.json'))
    packet_path.write_text(packet.model_dump_json())
    decision = selector.select({'claim_review': {'packet_sha256': packet_sha256(packet), 'claim_id': packet.claim_id}}, None).action
    decisions_path.write_text(json.dumps([decision]))
    config_path.write_text(json.dumps({'provider': 'supplied_only', 'discovery_request': runtime.discovery_request,
        'review_policy': {}, 'reviewer': {'claim_packets': {'candidate_suitability': str(packet_path)},
        'decisions': str(decisions_path), 'reviewer_label': 'first',
        'evaluation_policy': runtime.reviewer.evaluation_policy.model_dump(mode='json'),
        'expected_policy_sha256': runtime.reviewer.expected_policy_sha256}}))
    configured = load_scientific_workflow_config(config_path, methods_root=planner.methods.root, selector=object())
    evidence = configured.perform('candidate_review', planner, 'synthetic-session', actor='fixture', selector=object())
    record = configured.verify('candidate_review', planner, 'synthetic-session', evidence)
    assert evidence['status'] == 'accepted'
    assert configured.reviewer.selector.position == 1
    assert record['configuration']['reviewer']['selector']['backend'] == 'recorded_fixture'
