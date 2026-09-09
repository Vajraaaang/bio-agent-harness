"""Primary runner audit admission and preservation; trusted local fixtures only."""
import hashlib
import json
from pathlib import Path

import pytest

from devbench.runner import CodeActionRunner
from devbench_engine.audit_cli import restore_execution, replay_execution
from devharness_core.provenance import verify_execution
from tests.test_clinical_audit_regressions import task, LocalAdapter


def nodes(result):
    receipt=result['metadata']['audit']
    manifest=Path(receipt['manifest_path'])
    assert verify_execution(manifest, receipt['root_hash'])['valid']
    return [json.loads((manifest.parent/'nodes'/f'{node}.json').read_text())
            for node in json.loads(manifest.read_text())['node_ids']]


def test_complete_capture_survives_workspace_removal_and_excludes_private_goldens(task,tmp_path):
    seen=[]
    def answer(work):
        seen.append(work)
        assert not (work/'audit').exists() and not (work/'task_spec.snapshot.json').exists()
        return {'ok':True}
    result=CodeActionRunner(task).run(LocalAdapter(answer),tmp_path/'result')
    assert result['passed'] and not seen[0].exists()
    history=nodes(result)
    assert [n['kind'] for n in history]==['run_started','inputs_staged','execution_observed','run_finished']
    assert result['metadata']['record_format']=='devbench.code-action/2'
    assert result['metadata']['clinical_release_allowed'] is False
    assert result['metadata']['tool_capture_complete'] is False
    start=history[0]['payload'];assert start['artifacts']['task_spec.snapshot.json']['sha256']==result['metadata']['task_spec_hash']
    receipt=result['metadata']['audit'];restored=tmp_path/'restored'
    assert restore_execution(receipt['manifest_path'],receipt['root_hash'],restored)['restored']
    assert (restored/'metadata.csv').read_bytes()==(task.parent/'metadata.csv').read_bytes()
    assert not (restored/'task_spec.snapshot.json').exists() and not (restored/'runtime-source').exists()
    assert not (restored/'.venv').exists()
    final=history[-1]['payload'];assert final['status']=='success'
    outcome=json.loads((Path(receipt['manifest_path']).parent/'objects'/final['artifacts']['outcome.snapshot.json']['sha256']).read_text())
    assert outcome['passed'] and outcome['grader_result']==result['grader_result']


@pytest.mark.parametrize('operation',['raise','mutate','remove'])
def test_failure_history_retains_original_inputs_and_execution_error(task,tmp_path,operation):
    original=(task.parent/'metadata.csv').read_bytes()
    def fail(work):
        if operation=='mutate':(work/'metadata.csv').write_text('changed')
        if operation=='remove':(work/'metadata.csv').unlink()
        raise RuntimeError('original failure retained')
    result=CodeActionRunner(task).run(LocalAdapter(fail),tmp_path/'result')
    assert not result['passed'] and 'original failure retained' in result['metadata']['error']
    history=nodes(result);assert history[-1]['payload']['status']=='failed'
    stage=next(n['payload'] for n in history if n['kind']=='inputs_staged')
    assert stage['artifacts']['metadata.csv']['sha256']==hashlib.sha256(original).hexdigest()
    root=Path(result['metadata']['audit']['manifest_path']).parent
    assert (root/'objects'/stage['artifacts']['metadata.csv']['sha256']).read_bytes()==original


def test_participant_trace_and_metadata_cannot_manufacture_tool_replay(task,tmp_path):
    def answer(work):
        (work/'.devharness_trace.jsonl').write_text(json.dumps({'tool_name':'calculate_temporal_monotonicity','succeeded':True})+'\n')
        adapter.metadata={'tool_calls':[{'succeeded':True}],'audit':{'root_hash':'forged'}}
        return {'ok':True}
    adapter=LocalAdapter(answer)
    result=CodeActionRunner(task).run(adapter,tmp_path/'result')
    assert result['passed'] and result['metadata']['tool_calls']==[]
    history=nodes(result)
    assert all(n['kind'] not in ('tool_completed','tool_failed') for n in history)
    receipt=result['metadata']['audit']
    replay=replay_execution(receipt['manifest_path'],receipt['root_hash'],tmp_path/'replay')
    assert not replay['passed'] and replay['replayed_tool_count']==0
    assert 'Code-as-Action' in ' '.join(replay['errors'])
    assert not (tmp_path/'replay').exists()


@pytest.mark.parametrize('fault',['init','capture','finalize'])
def test_audit_failure_cannot_pass_or_erase_original_error(task,tmp_path,monkeypatch,fault):
    import devbench.evidence as evidence
    calls=[]
    def execute(work):
        calls.append(True)
        raise RuntimeError('original adapter failure')
    def broken(*args,**kwargs):raise OSError('audit failure '+fault)
    if fault=='init':monkeypatch.setattr(evidence,'ExecutionDAG',broken)
    elif fault=='capture':monkeypatch.setattr(evidence,'copy_artifact',broken)
    else:monkeypatch.setattr(evidence.ExecutionDAG,'finalize',broken)
    result=CodeActionRunner(task).run(LocalAdapter(execute),tmp_path/'result')
    assert not result['passed'] and not result['metadata']['accepted_final']
    assert result['metadata']['failure_type']=='audit_error'
    assert len(calls)==(1 if fault=='finalize' else 0)
    if fault=='finalize':assert 'original adapter failure' in result['metadata']['error']
    assert 'audit failure' in result['metadata']['audit_error']
    if fault=='capture':assert nodes(result)[-1]['payload']['status']=='failed'
    else:assert 'audit' not in result['metadata']
    assert (tmp_path/'result/run.json').is_file()


def test_caller_condition_mutation_does_not_relabel_the_execution(task,tmp_path):
    runner=CodeActionRunner(task,condition='baseline')
    def answer(work):
        runner.condition='assisted'
        runner.spec['grader']['expected']=False
        return {'ok':True}
    result=runner.run(LocalAdapter(answer),tmp_path/'result')
    assert result['passed'] and result['condition']==result['metadata']['condition']=='baseline'
    assert nodes(result)[0]['payload']['condition']=='baseline'


def test_environment_failure_is_recorded_without_executing(task,tmp_path,monkeypatch):
    import devbench.runner as runner
    def fail():raise ValueError('ambiguous distribution identity')
    monkeypatch.setattr(runner,'_environment',fail)
    calls=[]
    result=CodeActionRunner(task).run(LocalAdapter(lambda work:calls.append(True)),tmp_path/'result')
    assert not calls and not result['passed'] and not result['metadata']['accepted_final']
    assert 'ambiguous distribution identity' in result['metadata']['error']
    assert nodes(result)[-1]['payload']['status']=='failed'


def test_tampering_with_retained_outcome_is_detected(task,tmp_path):
    result=CodeActionRunner(task).run(LocalAdapter(lambda work:{'ok':True}),tmp_path/'result')
    last=nodes(result)[-1]['payload'];receipt=result['metadata']['audit']
    blob=Path(receipt['manifest_path']).parent/'objects'/last['artifacts']['outcome.snapshot.json']['sha256']
    blob.chmod(0o600);blob.write_text('{"passed":true}')
    assert not verify_execution(receipt['manifest_path'],receipt['root_hash'])['valid']


@pytest.mark.parametrize('limit',['bytes','members'])
def test_input_capture_limit_refuses_execution_and_retains_failure_receipt(task,tmp_path,monkeypatch,limit):
    import devbench.evidence as evidence
    monkeypatch.setattr(evidence,'INPUT_BYTES' if limit=='bytes' else 'INPUT_MEMBERS',1)
    called=[]
    result=CodeActionRunner(task).run(LocalAdapter(lambda work:called.append(True)),tmp_path/'result')
    assert not called and not result['passed'] and not result['metadata']['accepted_final']
    assert result['metadata']['failure_type']=='audit_error'
    assert nodes(result)[-1]['payload']['status']=='failed'


def test_finalization_failure_overrides_an_otherwise_passing_grade(task,tmp_path,monkeypatch):
    from devbench.evidence import ExecutionDAG
    def fail(*args,**kwargs):raise OSError('receipt storage failed')
    monkeypatch.setattr(ExecutionDAG,'finalize',fail)
    result=CodeActionRunner(task).run(LocalAdapter(lambda work:{'ok':True}),tmp_path/'result')
    assert not result['passed'] and result['score']==0 and result['grader_result'] is None
    assert result['metadata']['primary_grader_result']['passed']
    assert not result['metadata']['accepted_final'] and 'audit' not in result['metadata']
    assert 'receipt storage failed' in result['metadata']['audit_error']


def test_private_snapshot_change_between_copy_and_ledger_is_rejected(task,tmp_path,monkeypatch):
    import devbench.evidence as evidence
    original=evidence.copy_artifact
    def changed(source,destination,**kwargs):
        ref=original(source,destination,**kwargs)
        if destination.name=='metadata.csv':destination.write_text('changed private snapshot')
        return ref
    monkeypatch.setattr(evidence,'copy_artifact',changed)
    called=[]
    result=CodeActionRunner(task).run(LocalAdapter(lambda work:called.append(True)),tmp_path/'result')
    assert not called and not result['passed']
    assert any(reason in result['metadata']['error'] for reason in ('Private snapshot changed', 'Artifact byte limit'))
    assert nodes(result)[-1]['payload']['status']=='failed'
