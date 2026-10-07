"""Local coordinator fixtures; no live model/provider is invoked."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import hashlib
import json
import tempfile
import time
from types import SimpleNamespace

import pytest


def helpers():
    # Pytest uses importlib mode; load the existing metadata-only fixtures by
    # their explicit local path rather than relying on an importable tests package.
    import importlib.util
    spec=importlib.util.spec_from_file_location('biology_execution_fixtures',Path(__file__).with_name('test_biology_execution.py'))
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


@pytest.fixture
def directory():
    root=Path(__file__).resolve().parents[1]/'tmp'
    root.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=root,prefix='p-') as path:
        yield Path(path)


def state(directory):
    (directory/'records.json').write_text('{"records":[]}')
    (directory/'execution.json').write_text(json.dumps({'plan_identity':'a'*64,'aggregate_path':str(directory/'records.json')}))


@pytest.mark.parametrize('decision',['continue','stop'])
def test_checkpoint_requires_matching_release_and_never_overwrites(directory,decision):
    from devbench.study.checkpoint import wait_for_checkpoint,release_checkpoint
    state(directory)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future=pool.submit(wait_for_checkpoint,directory,'first',timeout_sec=2)
        deadline=time.monotonic()+1
        while not (directory/'checkpoint.json').exists() and time.monotonic()<deadline:time.sleep(.005)
        payload=(directory/'checkpoint.json').read_bytes();digest=hashlib.sha256(payload).hexdigest()
        with pytest.raises(ValueError,match='mismatch'):release_checkpoint(directory,'0'*64,decision)
        assert not future.done()
        result=release_checkpoint(directory,digest,decision)
        assert result['status']=='decision_written'
        assert future.result(timeout=1)['decision']==decision
        with pytest.raises(FileExistsError):release_checkpoint(directory,digest,decision)


def test_checkpoint_expiry_does_not_restart_or_continue(directory):
    from devbench.study.checkpoint import wait_for_checkpoint,release_checkpoint
    state(directory)
    result=wait_for_checkpoint(directory,'first',timeout_sec=.02)
    assert result['decision']=='expired'
    with pytest.raises(ValueError,match='expired'):release_checkpoint(directory,result['checkpoint_sha256'],'continue')


def test_waiting_coordinator_rechecks_snapshot_before_continuing(directory):
    from devbench.study.checkpoint import wait_for_checkpoint,_fresh
    state(directory)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future=pool.submit(wait_for_checkpoint,directory,'first',timeout_sec=2)
        deadline=time.monotonic()+1
        while not (directory/'checkpoint.json').exists() and time.monotonic()<deadline:time.sleep(.005)
        digest=hashlib.sha256((directory/'checkpoint.json').read_bytes()).hexdigest()
        (directory/'checkpoint-records.json').write_text('{"changed":true}')
        _fresh(directory/'checkpoint-decision.json',{'checkpoint_sha256':digest,'decision':'continue'})
        with pytest.raises(ValueError,match='snapshot changed'):future.result(timeout=1)


def test_evaluation_only_preparation_never_dispatches_calibration(directory,monkeypatch):
    plan=helpers().plan
    from devbench.study import biology_preparation,supervision
    study=plan()
    evaluation=study.recipes[0]
    calibration=evaluation.model_copy(update={'role':'calibration','recipe_id':'reserved-calibration','generation_seed':456})
    study=study.model_copy(update={'recipes':(evaluation,calibration)})
    original=study.model_dump_json();calls=[]
    def refused(worker,arguments,*args,**kwargs):
        calls.append(arguments[0].recipe_id)
        raise RuntimeError('intentional local preparation-control failure')
    monkeypatch.setattr(supervision,'supervise_coordinator',refused)
    result=biology_preparation.prepare_biology_study_data(study,directory/'prepared',allow_data_execution=True,evaluation_only=True)
    assert calls==[evaluation.recipe_id]
    assert result['planned_cohorts']==1 and result['excluded_recipe_ids']==['reserved-calibration']
    assert result['status']=='preparation_incomplete' and study.model_dump_json()==original


@pytest.mark.parametrize('decision,expected_workers',[('stop',1),('continue',3),('expired',1),('pin-mismatch',0)])
def test_real_coordinator_loop_honors_first_worker_checkpoint(directory,monkeypatch,decision,expected_workers):
    plan,bundle_for=helpers().plan,helpers().bundle_for
    from devbench.study import biology_execution as runner,execution,cli_identity,supervision,checkpoint
    from devbench.study.contracts import content_sha256
    from devbench.study.supervision import write_coordinator_json
    study=plan();bundle=bundle_for(study);calls=[];all_workers=[]
    exe=Path('/usr/bin/true')
    digest=hashlib.sha256(exe.read_bytes()).hexdigest()
    monkeypatch.setattr(cli_identity,'read_codex_identity',lambda *a:{'version':'fixture-only','executable_sha256':digest,'component_sha256':{str(exe):digest}})
    monkeypatch.setattr(cli_identity,'verify_codex_identity',lambda *a:None)
    monkeypatch.setattr(execution,'_source_identity',lambda:'e'*64)
    mask={key:runner.initial_biology_attempt_records(study,{bundle.cohort_id:bundle})[0].model_dump(mode='json')[key] for key in runner._REFERENCE_FIELDS}
    mask['thresholds_sha256']=content_sha256(study.thresholds.model_dump(mode='json'))
    task=__import__('devbench.study.biology_planning',fromlist=['schedule_biology_attempts']).schedule_biology_attempts(study)[0]
    prepared={'task_id':task.task_identity,'input_sha256':bundle.input_sha256,'source_sha256':'e'*64,
        'reference_availability':mask,'artifacts':{}}
    monkeypatch.setattr(runner,'_validated_worker_result',lambda *a:prepared)
    monkeypatch.setattr(runner,'_read_preparation',lambda p:(
        SimpleNamespace(task_id=task.task_identity,input_sha256=bundle.input_sha256,gene_universe_sha256='f'*64,thresholds_sha256=mask['thresholds_sha256']),
        {'task_id':task.task_identity,'input_sha256':bundle.input_sha256,'gene_universe_sha256':'f'*64}))
    def supervise(worker,*args,**kwargs):
        all_workers.append(worker)
        if worker is runner._biology_attempt_worker:calls.append(worker)
        return SimpleNamespace(failure_kind=None,exitcode=0,interrupted=False,as_dict=lambda:{})
    monkeypatch.setattr(supervision,'supervise_coordinator',supervise)
    def collected(observation,arguments):
        record=arguments[2].model_copy(update={'completion_status':'blocked'})
        path=arguments[7]/'record.json'
        write_coordinator_json(path,record.model_dump(mode='json'),allow_data_execution=True)
        return record,False,{'record_path':str(path),'record_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
            'safe_row_count':0,'safe_scalar_count':0,'execution_detail_path':None,'execution_detail_sha256':None}
    monkeypatch.setattr(runner,'_collect_attempt',collected)
    def gate(*a,**kw):
        assert len(calls)==1
        return {'decision':decision,'checkpoint_sha256':'a'*64}
    monkeypatch.setattr(checkpoint,'wait_for_checkpoint',gate)
    result=runner.execute_biology_study(study,{bundle.cohort_id:bundle},directory/'live',
        workspace_root=directory/'w',allow_data_execution=True,allow_exec_preflight=True,
        codex_executable=str(exe),expected_codex_sha256='0'*64 if decision=='pin-mismatch' else digest,pause_after_attempts=1)
    assert len(calls)==expected_workers,result['errors']
    assert result['all_model_slots_processed'] is (decision=='continue')
    if decision=='pin-mismatch':
        assert not all_workers and any('approved build' in message for message in result['errors'])


def test_small_real_evaluation_preparation_leaves_calibration_unprepared(directory):
    from devbench.study.biology_preparation import prepare_biology_study_data
    study=helpers().plan()
    first=study.recipes[0]
    first=first.model_copy(update={'parameters':first.parameters.model_copy(update={'num_genes':32})})
    reserved=first.model_copy(update={'role':'calibration','recipe_id':'reserved-calibration','generation_seed':456})
    study=study.model_copy(update={'recipes':(first,reserved)})
    before=study.sha256()
    result=prepare_biology_study_data(study,directory/'real-fixture',allow_data_execution=True,evaluation_only=True)
    assert result['status']=='prepared' and result['prepared_cohorts']==1
    manifest=json.loads(Path(result['bundles']).read_text())
    assert set(manifest['cohorts'])=={first.recipe_id}
    assert manifest['excluded_recipe_ids']==[reserved.recipe_id]
    assert len(list((directory/'real-fixture/cohorts').iterdir()))==1
    assert study.sha256()==before
