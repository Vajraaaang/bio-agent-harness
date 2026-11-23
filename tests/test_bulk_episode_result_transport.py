"""Large synthetic transport fixtures; no large scientific fit or live model."""
from dataclasses import replace
import json

import pytest

from devbench.study.broker import BrokerPolicy, ToolBroker
from devbench.study.evidence import verify_receipts
from tests.test_analysis_episodes import workspace, create, inputs


class LargeFixtureBroker(ToolBroker):
    @property
    def allowed_tools(self):
        return frozenset({'fixture_payload'})

    def _invoke_tool(self, tool, arguments):
        return {'transport_fixture': 'x'*8_100_000}


def dispatched_payload(size: int) -> dict:
    return {'transport_fixture': 'x'*size}


class DispatchLargeFixtureBroker(LargeFixtureBroker):
    def _invoke_tool(self, tool, arguments):
        self.dispatcher.register(dispatched_payload)
        return json.loads(self.dispatcher.execute('dispatched_payload',{'size':33_000_000}))


def make_broker(root, **kwargs):
    source=root/'input.json';source.write_text('{}')
    p=BrokerPolicy('enforced','a'*64,'raw_counts',('g',),False,60,'b'*64,**kwargs)
    return LargeFixtureBroker(p,{str(source):source},root/'receipts',allow_data_execution=True)


def test_legacy_limit_rejects_and_measures_large_result(workspace):
    broker=make_broker(workspace)
    response=broker.execute('fixture_payload',{})
    assert response['status']=='failed'
    assert broker.events[-1]['result_size_bytes'] > 8_000_000
    assert broker.events[-1]['result_limit_bytes']==8_000_000
    broker.close()


def test_explicit_result_bound_is_required_by_writer_and_independent_reader(workspace):
    broker=make_broker(workspace,max_result_bytes=64_000_000)
    response=broker.execute('fixture_payload',{})
    assert response['status']=='succeeded'
    receipt=broker.close()
    args=dict(expected_receipt=receipt,expected_input_sha256='a'*64,
              expected_implementation_sha256='b'*64,allow_evidence_read=True)
    with pytest.raises(ValueError,match='bounded|budget'):
        verify_receipts(broker.output_dir,**args)
    verified=verify_receipts(broker.output_dir,max_result_bytes=64_000_000,**args)
    assert len(verified.results[response['receipt_id']]['transport_fixture'])==8_100_000
    path=broker.output_dir/(response['receipt_id']+'.json')
    path.write_bytes(path.read_bytes().replace(b'xxx',b'yyy',1))
    with pytest.raises(ValueError,match='hash'):
        verify_receipts(broker.output_dir,max_result_bytes=64_000_000,**args)


@pytest.mark.parametrize('limit',[True,0,64_000_001,8_000_000.0])
def test_result_budget_cannot_be_unbounded_or_noninteger(limit):
    with pytest.raises(ValueError,match='result'):
        BrokerPolicy('enforced','a'*64,'raw_counts',('g',),False,60,'b'*64,max_result_bytes=limit)


def test_full_dispatched_backend_capture_uses_same_explicit_bulk_bound(workspace):
    source=workspace/'input.json';source.write_text('{}')
    policy=BrokerPolicy('enforced','a'*64,'raw_counts',('g',),False,60,'b'*64,max_result_bytes=64_000_000)
    broker=DispatchLargeFixtureBroker(policy,{str(source):source},workspace/'receipts',allow_data_execution=True)
    response=broker.execute('fixture_payload',{})
    assert response['status']=='succeeded',response['error']
    event=broker.events[-1]
    assert 32_000_000 < event['backend_size_bytes'] <= 64_000_000
    assert event['backend_limit_bytes']==64_000_000
    receipt=broker.close()
    evidence=verify_receipts(broker.output_dir,expected_receipt=receipt,expected_input_sha256='a'*64,
        expected_implementation_sha256='b'*64,allow_evidence_read=True,max_result_bytes=64_000_000,
        max_evidence_bytes=128_000_000)
    assert len(evidence.results[response['receipt_id']]['transport_fixture'])==33_000_000
    captured=json.loads((broker.output_dir/event['backend_evidence']['file']).read_text())
    assert captured[0]['tool_name']=='dispatched_payload'
    assert captured[0]['succeeded'] is True
    assert captured[0]['result']==evidence.results[response['receipt_id']]


def padded_audit(monkeypatch, padded_tool='audit_expression_quality'):
    from devbench.study.biology_broker import BiologyBroker
    original=BiologyBroker._invoke_tool
    def invoke(self,tool,arguments):
        result=original(self,tool,arguments)
        if tool==padded_tool:
            result['diagnostics']['transport_fixture_padding']='x'*17_000_000
        return result
    monkeypatch.setattr(BiologyBroker,'_invoke_tool',invoke)


def test_large_observation_projection_preserves_complete_evidence_and_rejects_tamper(workspace,monkeypatch):
    from devbench.analysis.episode_agent import project_observation, verify_projected_history
    padded_audit(monkeypatch)
    episode,_=create(workspace,continuous=False)
    response=episode.step({'kind':'tool','tool':'audit_expression_quality'})
    try:
        assert response['status']=='succeeded'
        projected=project_observation(episode,response)
        summary=projected['result_summary']
        assert summary['individual_gene_rows_in_prompt']==0
        assert summary['full_result_retained'] is True
        assert len(json.dumps(projected))<128000
        source=episode.root/'checkpoints/action-0001/observation.json'
        assert source.stat().st_size>16_000_000
        assert len(json.loads(source.read_text())['result']['diagnostics']['transport_fixture_padding'])==17_000_000
        history=[{'action':{'kind':'tool','tool':'audit_expression_quality'},'observation':projected}]
        verify_projected_history(episode,history)
        altered=json.loads(json.dumps(history));altered[0]['observation']['result_summary']['omitted_gene_rows']=999
        with pytest.raises(ValueError,match='projection'):
            verify_projected_history(episode,altered)
        artifact=episode.root/'agent/observations/action-0001.json'
        artifact_original=artifact.read_bytes();artifact.write_bytes(artifact_original+b' ')
        with pytest.raises(ValueError,match='projection'):
            verify_projected_history(episode,history)
        artifact.write_bytes(artifact_original)
        original=source.read_bytes();source.write_bytes(original+b' ')
        with pytest.raises(ValueError,match='changed|mismatch|projection'):
            verify_projected_history(episode,history)
        source.write_bytes(original)
    finally:
        if episode.outcome is None:
            episode.abort('synthetic_transport_check_no_fit')


def test_large_full_observation_survives_compact_driver_handoff(workspace,monkeypatch):
    from devbench.analysis.episode_agent import drive_episode
    from devbench.analysis.episode_handoff import resume_handoff
    from devbench.analysis.episodes import verify_episode
    from tests.test_episode_agent import ScriptedSelector
    padded_audit(monkeypatch)
    episode,_=create(workspace,continuous=False)
    selector=ScriptedSelector([{'kind':'tool','tool':'audit_expression_quality'},
                              {'kind':'request_information','message':'Confirm interpretation'}])
    response=drive_episode(episode,selector,allow_data_execution=True)
    assert response['state']=='awaiting_information'
    assert 'transport_fixture_padding' not in json.dumps(selector.contexts[-1])
    receipt=response['handoff']
    restored=resume_handoff(episode.root,receipt['manifest_path'],expected_root=receipt['root_hash'],
        response={'message':'Continue','preserves_frozen_inputs_and_settings':True},allow_data_execution=True)
    assert len(restored.results[0]['diagnostics']['transport_fixture_padding'])==17_000_000
    restored.abort('synthetic_transport_check_no_fit')
    assert verify_episode(restored.root,restored.receipt['root_hash'])['valid']


@pytest.mark.parametrize('known_adjustment',[True,False])
def test_projection_counts_entire_typed_family_and_preserves_failed_nulls(workspace,monkeypatch,known_adjustment):
    import csv
    from collections import Counter
    from devbench.analysis.episodes import AnalysisEpisode, verify_episode
    from devbench.analysis.episode_agent import project_observation
    padded_audit(monkeypatch,'mean_condition_analysis')
    if not known_adjustment:
        from devbench.study.biology_broker import BiologyBroker
        padded=BiologyBroker._invoke_tool
        def unknown(self,tool,arguments):
            result=padded(self,tool,arguments)
            if tool=='mean_condition_analysis':
                result['diagnostics']['gene_test_adjustment_family']['adjustment']='unknown_fixture_policy'
            return result
        monkeypatch.setattr(BiologyBroker,'_invoke_tool',unknown)
    task,paths=inputs(workspace,continuous=False)
    with paths['expression'].open(newline='') as f:
        rows=list(csv.reader(f))
    rows[-1][1:]=['0']*8
    with paths['expression'].open('w',newline='') as f:csv.writer(f).writerows(rows)
    episode=AnalysisEpisode.create(workspace/'episode-fit',question='Estimate B minus A',assay='bulk',
        public_task=task,inputs=paths,allow_data_execution=True)
    for tool in ('audit_expression_quality','validate_biological_units','validate_design_matrix','mean_condition_analysis'):
        response=episode.step({'kind':'tool','tool':tool})
        assert response['status']=='succeeded', response
    full=response['result']['target_results'][0]['payload']['gene_statistics']
    original=json.loads(json.dumps(full))
    projected=project_observation(episode,response)
    summary=projected['result_summary'];target=summary['targets'][0]
    assert summary['omitted_gene_rows']==len(task['gene_ids'])==12
    assert target['full_attempted_BH_denominator']==(12 if known_adjustment else None)
    assert target['status_counts']==dict(Counter(row['estimate_status'] for row in full))
    assert target['tested_gene_rows']==sum(row['p_value'] is not None for row in full)
    assert full[-1]['p_value'] is None and full[-1]['q_value'] is None
    assert full[-1]['estimate_status']=='failed'
    assert episode.last_result['target_results'][0]['payload']['gene_statistics']==original
    assert summary['diagnostics']['designs'][0]['rank']>0
    final=episode.step({'kind':'finish'})
    assert final['outcome']=='completed'
    assert final['scientific_correctness'] is None
    assert verify_episode(episode.root,episode.receipt['root_hash'])['valid']


def test_biology_broker_retains_actual_registered_dispatch_evidence(workspace):
    episode,_=create(workspace,continuous=False)
    try:
        response=episode.step({'kind':'tool','tool':'audit_expression_quality'})
        assert response['status']=='succeeded'
        event=episode.broker.events[-1]
        assert event['backend_evidence']['calls']==1
        captured=json.loads((episode.broker.output_dir/event['backend_evidence']['file']).read_text())
        assert captured[0]['tool_name']=='coordinator_observed_bulk_analysis'
        assert captured[0]['arguments']['tool_name']=='audit_expression_quality'
        assert captured[0]['arguments']['allow_data_execution'] is True
        assert captured[0]['succeeded'] is True
        assert captured[0]['result']==response['result']
        assert 'coordinator_observed_bulk_analysis' not in episode.menu
    finally:
        if episode.outcome is None:episode.abort('synthetic_dispatch_evidence_check')
