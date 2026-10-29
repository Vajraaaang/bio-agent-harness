"""Real graph/ledger metadata tests with fake channels; NEVER remote acceptance.

No scientific fixture/source/validator is imported, evaluated or replayed here.
"""
import hashlib
import importlib.metadata
import json
from pathlib import Path

import pytest

REPO = Path('/Users/vajraang/Documents/ChatGPT/bio-agent-harness')
ROOT = REPO / 'reports/rna-colab-integration/2026-10-22-v1/candidate'

from devbench.analysis import colab_workflow as adapter
from devbench.analysis.methods import MethodService
from devbench.analysis.rna_trial_authority import AuthenticatedTrialAuthority
from devbench.analysis.colab_transport import OfficialColabTransport
from devbench.analysis.workflow_operations import WorkflowOperationLedger
from devbench.analysis.workflow_actions import create_rna_workflow
from devbench.analysis.workflow_protocol_qualification import ProtocolQualificationProfile
from devbench.analysis.checkpoints import digest
from tests.test_workflow_protocol_qualification import engineering_candidate_validator


def source_ref(path):
    raw = path.read_bytes()
    return {'path': str(path), 'sha256': hashlib.sha256(raw).hexdigest(), 'size_bytes': len(raw)}


def prepared(tmp_path):
    service = MethodService(tmp_path / 'workspace')
    # Source assets are opaque bytes; no import, fit, verifier or plot executes.
    source = tmp_path / 'source.py'; source.write_bytes((ROOT/'qualification/method.py').read_bytes())
    lock = tmp_path / 'lock.json'; lock.write_bytes((ROOT/'qualification/environment-lock.json').read_bytes())
    fixture = tmp_path / 'input.json'; fixture.write_bytes((ROOT/'qualification/fixture.json').read_bytes())
    verifier = tmp_path / 'verifier.py'; verifier.write_bytes((ROOT/'qualification/verifier.py').read_bytes())
    expectations = tmp_path / 'expectations.json'; expectations.write_bytes((ROOT/'qualification/expected_values.json').read_bytes())
    art = service.import_artifact(fixture, artifact_type='json', actor='test')
    contract = {'format':'devharness.method/1', 'method_id':'colab_workflow_metadata_test',
        'name':'Inert workflow fixture', 'lifecycle':'custom','implementation_trust':'unreviewed',
        'inputs':{'fixture':{'artifact_type':'json','required':True}},
        'parameters':{'type':'object','additionalProperties':False,'properties':{}},
        'outputs':{'receipt':{'artifact_type':'json','path':'/outputs/receipt.json','required':True}},
        'implementation':{'kind':'colab','source_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),
                          'entrypoint':['python3','/method/source.py']},
        'environment':{'runtime':'google_colab','lock_sha256':hashlib.sha256(lock.read_bytes()).hexdigest()},
        'execution':{'cpu':1,'memory_mb':256,'timeout_seconds':30,'pids':16,'max_output_bytes':10000,'network':'allow'},
        'validation':{'comparator':'json','invariants':[],'counts_layer':'counts','qc_metrics':[]},
        'provenance':{'origin':'inert metadata tests, not remote evidence'}}
    method = service.register_method(contract, source=source, environment_lock=lock, actor='test')
    analysis = service.create_analysis('software metadata only', actor='test')
    branch = service.create_branch(analysis['analysis_id'], analysis['revision_id'], actor='test')
    step = service.add_step(branch['branch_id'], method['version_id'], {'fixture':art['artifact_id']}, {}, actor='test')
    job = service.prepare_remote_step(step['step_id'], run_id='1'*32, job_id='2'*32,
        source_bundle_sha256='3'*64, validator_sha256=source_ref(verifier)['sha256'],
        scope=adapter.SCOPE, limits=contract['execution'], actor='test')
    # Exact class identities only; no authority signing, auth, private transport
    # constructor, dispatch or credentials are touched by metadata fixtures.
    authority = object.__new__(AuthenticatedTrialAuthority)
    transport = object.__new__(OfficialColabTransport)
    transport.service = service; transport.state = 'new'; transport.cleanup = {'stop_requested':False,'presence':'unknown'}
    execution = adapter.ColabQualificationExecution(service=service, authority=authority, transport=transport,
        job=job, request={}, grant={}, export_directory=tmp_path / 'export')
    plan = adapter.ColabQualificationPlan(execution=execution, actor='test')
    ledger = WorkflowOperationLedger(tmp_path / 'operations.sqlite')
    manifest = tmp_path/'candidate-manifest.json'
    manifest.write_text('{"format":"test.inert-candidate-manifest/1"}')
    profile = ProtocolQualificationProfile(job_id=job['job_id'], job_sha256=digest(job),
        source_bundle_sha256=job['source_bundle_sha256'], candidate_manifest_ref=source_ref(manifest),
        candidate_sha256=source_ref(manifest)['sha256'], sources={
            'method': source_ref(source), 'environment_lock': source_ref(lock), 'fixture': source_ref(fixture),
            'verifier': source_ref(verifier), 'expectations': source_ref(expectations)})
    actions = adapter.ColabQualificationActions(plan, ledger, actor='test', profile=profile)
    return service, plan, ledger, actions


def trap(*args, **kwargs):
    raise AssertionError('Scientific, model or local execution reached')


@pytest.fixture(autouse=True)
def poison_scientific_paths(monkeypatch):
    import devbench.analysis.method_executor as local
    import devbench.analysis.rna_input_contract as inputs
    import devbench.analysis.method_planner as scientific
    monkeypatch.setattr(local.LocalDockerExecutor, 'execute', trap)
    monkeypatch.setattr(inputs, 'inspect_registered_bulk_rna_input', trap)
    monkeypatch.setattr(scientific.ScientificMethodPlanner, 'generate_candidates', trap)
    monkeypatch.setattr(scientific.ScientificMethodPlanner, 'resolve_capabilities', trap)
    monkeypatch.setattr(scientific.ScientificMethodPlanner, 'design_trials', trap)
    monkeypatch.setattr(scientific.ScientificMethodPlanner, 'run_trial', trap)


def test_current_graph_dependency_versions_are_exact():
    expected = {'langgraph':'1.2.12','langgraph-checkpoint-sqlite':'3.1.1','langchain-core':'1.6.6'}
    for distribution, version in expected.items():
        assert importlib.metadata.version(distribution) == version
    import devbench.analysis.langgraph_workflow as graph
    assert Path(graph.__file__).resolve() == REPO / 'devbench/analysis/langgraph_workflow.py'


def test_actual_current_graph_requires_all_protocol_reviews_before_separate_execution_approval(tmp_path, monkeypatch):
    service, plan, ledger, actions = prepared(tmp_path)
    monkeypatch.setattr(adapter.ColabQualificationExecution, 'execute_once', trap)
    with create_rna_workflow(mode='single_agent', actions=actions, checkpoint_path=tmp_path/'checkpoint.sqlite') as flow:
        first = flow.start(actions.create_state(plan.session_id))
        while '__interrupt__' in first:
            first = flow.resume(plan.session_id)
        assert first['phase'] == 'run' and first['status'] == 'failed'
        assert first['failure']['kind'] == 'PermissionError'
        nodes = [reference.split(':')[0] for reference in first['operation_refs']]
        assert nodes == ['audit','question','discovery','generate','candidate_review','resolve',
                         'implementation','design','design_review','review','declare','execution_review']
        assert len(first['trial_ids']) == 1 and first['outcome_ref'] is None
        assert ledger.get(plan.session_id, 'run', 0) is None
        assert service.list_records('run') == []
        assert plan.get(plan.session_id)['recommendation'] is None


def test_generic_controller_selector_and_model_permission_rejected(tmp_path):
    service, plan, ledger, actions = prepared(tmp_path)
    with pytest.raises(TypeError):
        adapter.ColabQualificationPlan(execution=object(), actor='test')
    with pytest.raises(TypeError):
        adapter.create_colab_qualification_workflow(actions=object(), checkpoint_path=tmp_path/'checkpoint.sqlite')
    state = actions.create_state(plan.session_id)
    actions.allow_model_execution = True
    with pytest.raises(PermissionError, match='forbidden'): actions.check(state)


def test_job_mutation_before_audit_cannot_dispatch(tmp_path):
    service, plan, ledger, actions = prepared(tmp_path)
    plan.execution.job['source_bundle_sha256'] = '9'*64
    result = actions.perform('audit', actions.create_state(plan.session_id))
    assert result['status'] == 'reconciliation_required'
    assert service.list_records('run') == []


def test_controller_is_one_use_and_sanitizes_provider_failure(tmp_path, monkeypatch):
    service, plan, _, _ = prepared(tmp_path)
    # Explicit unit isolation of controller failure handling, not graph admission.
    monkeypatch.setattr(adapter, '_require_graph_run_context', lambda execution: None)
    def fail(*args, **kwargs): raise RuntimeError('secret-provider-url-or-token')
    monkeypatch.setattr(MethodService, 'reserve_remote_step', fail)
    with pytest.raises(RuntimeError, match='no redispatch') as captured:
        plan.execution.execute_once()
    assert 'secret' not in str(captured.value)
    with pytest.raises(PermissionError, match='spent'): plan.execution.execute_once()
    assert service.list_records('run') == []


def test_controller_actual_interface_order_with_fake_channels_only(tmp_path, monkeypatch):
    service, plan, _, _ = prepared(tmp_path)
    # Explicit unit isolation of channel ordering, not graph admission.
    monkeypatch.setattr(adapter, '_require_graph_run_context', lambda execution: None)
    calls = []
    def reserve(self, job, **kwargs):
        calls.append('reserve'); assert kwargs['authority'] is plan.execution.authority
    def export(self, job_id, destination, **kwargs):
        calls.append('export'); assert job_id == plan.execution.job['job_id']
    def dispatch(self, job_id): calls.append('dispatch'); return 'fake-dispatch'
    def record(self, job_id, observation, **kwargs):
        calls.append('record'); assert observation == 'fake-dispatch'
    class SoftwareOnlyReturnedObservation:
        def verify(self, **kwargs):
            calls.append('verify-return'); return {'expected_root_sha256':'a'*64}
    def collect(self): calls.append('collect'); return tmp_path/'fake.zip', SoftwareOnlyReturnedObservation()
    def import_result(self, job_id, archive, expected_root, **kwargs):
        calls.append('import'); assert expected_root == 'a'*64
        return {'software_fixture_only':True}
    monkeypatch.setattr(MethodService, 'reserve_remote_step', reserve)
    monkeypatch.setattr(MethodService, 'export_remote_job', export)
    monkeypatch.setattr(MethodService, 'record_remote_dispatch', record)
    monkeypatch.setattr(MethodService, 'import_remote_result', import_result)
    monkeypatch.setattr(OfficialColabTransport, 'dispatch', dispatch)
    monkeypatch.setattr(OfficialColabTransport, 'collect', collect)
    assert plan.execution.execute_once() == {'software_fixture_only':True}
    assert calls == ['reserve','export','dispatch','record','collect','verify-return','import']
    with pytest.raises(PermissionError): plan.execution.execute_once()


def test_all_sixteen_nodes_and_checkpoint_resume_with_software_receipts_only(tmp_path, monkeypatch):
    """Control-flow proof only; fake remote result is never acceptance evidence."""
    from devbench.analysis.workflow_state import SERIAL_ROUTE
    service, plan, ledger, actions = prepared(tmp_path)
    calls = []
    def fake_remote(self):
        adapter._require_graph_run_context(self)
        calls.append(self.job['job_id'])
        return {'status': 'succeeded', 'run_id': self.job['run_id']}
    def fake_assessment(self, session):
        assert session == self.session_id
        assert len(self.get(session)['results']) == 1
        return {'round_trip_observed': True, 'integration_accepted': False,
                'independent_verification_required': True, 'scientific_admission': False}
    monkeypatch.setattr(adapter.ColabQualificationExecution, 'execute_once', fake_remote)
    monkeypatch.setattr(adapter.ColabQualificationPlan, 'evidence_assessment', fake_assessment)
    actions.allow_data_execution = True  # Test channel only; no real approval is issued.
    checkpoint = tmp_path/'full-metadata-route.sqlite'
    with create_rna_workflow(mode='single_agent', actions=actions, checkpoint_path=checkpoint) as flow:
        result = flow.start(actions.create_state(plan.session_id))
        interruptions = []
        while '__interrupt__' in result:
            interruptions.append(result['phase'])
            result = flow.resume(plan.session_id)
        assert result['status'] == 'completed', result
        assert [ref.split(':')[0] for ref in result['operation_refs']] == list(SERIAL_ROUTE)
        # Interrupts occur before review/execution_review, retaining predecessors.
        assert interruptions == ['design_review', 'declare']
    with create_rna_workflow(mode='single_agent', actions=actions, checkpoint_path=checkpoint) as flow:
        assert flow.resume(plan.session_id) == result
    assert calls == [plan.execution.job['job_id']]
    assert adapter._active_qualification_run.get() is None
    assert service.list_records('run') == []
    for node in ('discovery','candidate_review','implementation','design_review','conclusion_review'):
        record = ledger.get(plan.session_id, node, 0)
        proof = actions.scientific_workflow.verify(node, plan, plan.session_id,
            record['result']['evidence'], plan_snapshot=service.get('method_plan_snapshot', record['result']['after_planner_hash']))
        assert proof['qualification_status'] == 'protocol_only'
        assert proof['scientifically_qualified'] is False


def test_direct_controller_rejects_before_reservation_without_spending(tmp_path, monkeypatch):
    service, plan, _, _ = prepared(tmp_path)
    calls = []
    def forbidden(*args, **kwargs):
        calls.append('dispatch-side-effect')
        pytest.fail('Direct qualification controller must never reserve/export/dispatch')
    monkeypatch.setattr(MethodService, 'reserve_remote_step', forbidden)
    monkeypatch.setattr(MethodService, 'export_remote_job', forbidden)
    monkeypatch.setattr(OfficialColabTransport, 'dispatch', forbidden)
    with pytest.raises(PermissionError, match='active.*graph|graph.*active'):
        plan.execution.execute_once()
    assert calls == []
    assert plan.execution.used is False


def test_direct_actions_cannot_execute_after_complete_prerequisite_receipts(tmp_path, monkeypatch):
    from devbench.analysis.workflow_state import SERIAL_ROUTE
    service, plan, ledger, actions = prepared(tmp_path)
    state = actions.create_state(plan.session_id)
    for node in SERIAL_ROUTE[:SERIAL_ROUTE.index('run')]:
        state = actions.perform(node, state)
        assert state['status'] == 'ready', state
    actions.allow_data_execution = True
    calls = []
    def forbidden(*args, **kwargs):
        calls.append('reserve')
        pytest.fail('Direct WorkflowActions execution must not reach Colab reservation')
    monkeypatch.setattr(MethodService, 'reserve_remote_step', forbidden)
    result = actions.perform('run', state)
    assert result['status'] == 'reconciliation_required'
    assert ledger.get(plan.session_id, 'run', 0)['status'] == 'uncertain'
    assert calls == [] and plan.execution.used is False
    assert adapter._active_qualification_run.get() is None


def test_direct_serial_controller_and_public_scope_cannot_mint_graph_admission(tmp_path):
    from devbench.analysis.langgraph_workflow import SerialRNAWorkflow
    _, plan, _, actions = prepared(tmp_path)
    state = actions.create_state(plan.session_id)
    with SerialRNAWorkflow(actions, tmp_path/'direct-serial.sqlite') as direct:
        direct._qualification_factory_token = True
        with pytest.raises(PermissionError, match='factory-admitted'):
            with actions.graph_run_scope(direct, state):
                pytest.fail('Boolean marker must not admit direct controllers')
    with create_rna_workflow(mode='single_agent', actions=actions, checkpoint_path=tmp_path/'factory.sqlite') as flow:
        with pytest.raises(PermissionError, match='active|callback|invocation'):
            with actions.graph_run_scope(flow, state):
                pytest.fail('Public scope cannot mint an actual active graph callback')
    assert plan.execution.used is False


def test_manual_node_callback_at_pending_run_cannot_replace_start_resume(tmp_path, monkeypatch):
    """Pause the real graph at run, then call its node outside invocation scope."""
    from devbench.analysis.langgraph_workflow import SerialRNAWorkflow
    from langgraph.types import interrupt
    _, plan, _, actions = prepared(tmp_path)
    actions.allow_data_execution = True
    original = SerialRNAWorkflow._node
    captured, calls = [], []
    def pause_before_run(self, node, state):
        if node == 'run':
            captured.append(dict(state))
            interrupt({'engineering_control': 'pause before run'})
        return original(self, node, state)
    def fake_remote(self):
        adapter._require_graph_run_context(self)
        calls.append(self.job['job_id'])
        return {'status': 'succeeded', 'run_id': self.job['run_id']}
    monkeypatch.setattr(SerialRNAWorkflow, '_node', pause_before_run)
    monkeypatch.setattr(adapter.ColabQualificationExecution, 'execute_once', fake_remote)
    with create_rna_workflow(mode='single_agent', actions=actions, checkpoint_path=tmp_path/'pending-run.sqlite') as flow:
        result = flow.start(actions.create_state(plan.session_id))
        while not captured:
            assert '__interrupt__' in result
            result = flow.resume(plan.session_id)
        assert flow.graph.get_state(flow._config(plan.session_id)).next == ('run',)
        monkeypatch.setattr(SerialRNAWorkflow, '_node', original)
        manual = flow._node('run', captured[0])
        assert manual['status'] == 'failed'
        assert manual['failure']['kind'] == 'PermissionError'
    assert calls == [] and plan.execution.used is False
