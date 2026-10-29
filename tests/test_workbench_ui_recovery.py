"""UI controller stop/recovery proofs using the existing native workflow."""
import json
import os
from pathlib import Path
import threading
import time

import pytest

from tests.test_workbench_ui_planning import root, setup, wait, approval, RecordedSelector


def controls(app):
    assert hasattr(app,'cancel') and hasattr(app,'resume'), 'UI controller has no bounded stop/recovery path'


def test_terminal_publication_waits_for_owning_operation_cleanup(root):
    app,request=setup(root,RecordedSelector(),allow_data_execution=True)
    try:
        job=app.register(request)['id'];assert wait(app,job)['status']=='awaiting_operator_review'
        # Fault injection at the window between result publication and cleanup.
        app._update(job,operation_pending=True)
        assert app.status(job)['status']=='finalizing'
        with pytest.raises(RuntimeError):app.review(job,approval())
        assert app.status(job)['resumable'] is False
        app._update(job,operation_pending=False)
    finally:app.close()


def test_queued_cancel_has_no_selector_dispatch_and_stays_cached(root):
    selector=RecordedSelector();app,request=setup(root,selector,allow_data_execution=True)
    release=threading.Event()
    try:
        controls(app)
        app._executor.submit(release.wait)
        job=app.register(request)['id']
        assert app.status(job)['cancellable'] is True
        app.cancel(job);release.set()
        state=wait(app,job)
        assert state['status']=='cancelled' and state['worker_liveness']=='stopped',state
        assert selector.calls==[] and state['resumable'] is False
        assert app.register(request)['id']==job
        assert app.status(job)['status']=='cancelled'
        with pytest.raises(RuntimeError):app.run(job)
    finally:release.set();app.close()


def test_restart_with_unknown_workers_requires_reconciliation(root):
    selector=RecordedSelector({'kind':'refuse','reason':'Unsupported contrast'})
    app,request=setup(root,selector)
    try:
        controls(app)
        job=app.register(request)['id'];wait(app,job)
        app._update(job,status='running')  # Simulated abrupt lost controller state.
        parameters={k:v for k,v in app.parameters.items() if k not in
            {'workflow','prompt','execution_tier','data_sha256','parent_root','model_path','model_sha256','integration_decision'}}
    finally:app.close()

    from devbench.analysis.workbench_ui import WorkbenchService
    app=WorkbenchService(root/'service',[root/'inputs'],parameter_overrides=parameters,
        run_timeout_seconds=180,proposal_selector=selector,allow_data_execution=True)
    try:
        state=app.status(job)
        assert state['status']=='recovery_required' and state['resumable'] is False
        assert state['worker_liveness']=='unknown'
        with pytest.raises(RuntimeError):app.resume(job)
        with pytest.raises(RuntimeError):app.register({**request,'question':'Do not start while ownership is uncertain'})
    finally:app.close()


def test_cached_request_cannot_start_a_reader_while_liveness_is_unknown(root,monkeypatch):
    from devharness_core import watchdog
    app,request=setup(root,RecordedSelector({'kind':'refuse','reason':'Unsupported contrast'}))
    try:
        job=app.register(request)['id'];assert wait(app,job)['status']=='refusal'
        app._update(job,status='recovery_required',worker_liveness='unknown')
        def forbidden(*args,**kwargs):pytest.fail('Cached lookup started an unadmitted worker')
        monkeypatch.setattr(watchdog,'run_sandboxed',forbidden)
        with pytest.raises(RuntimeError,match='liveness'):app.register(request)
    finally:app.close()


def test_resume_rejects_a_rebound_input_before_numerical_dispatch(root,monkeypatch):
    import shutil
    from devbench.analysis import workbench_proposals as native
    app,request=setup(root,RecordedSelector(),allow_data_execution=True)
    calls=[]
    try:
        job=app.register(request)['id'];assert wait(app,job)['status']=='awaiting_operator_review'
        app.review(job,approval());record=wait(app,job)
        proposal,review=record['proposal_receipt'],record['review_receipt']
        bundle=app.workspace/'jobs'/job/'bundle'
        native.prepare_reviewed_workbench(bundle,proposal['manifest_path'],review['manifest_path'],
            expected_proposal_root=proposal['root_hash'],expected_review_root=review['root_hash'],allow_data_execution=True)
        outside=root/'outside.h5ad';shutil.copyfile(request['source'],outside)
        (bundle/'bindings.json').write_text(json.dumps({'data':str(outside)}))
        app._update(job,status='cancelled',worker_liveness='stopped',operation_pending=False,
            execution_history=[{'attempt':1,'elapsed_sec':1.0,'status':'cancelled','budget_seconds':180}])
        def forbidden(*args,**kwargs):
            calls.append(True)
            raise RuntimeError('Rebound data reached numerical dispatch')
        monkeypatch.setattr(native,'execute_reviewed_workbench',forbidden)
        with pytest.raises(ValueError,match='roots|binding|source'):app.resume(job)
        assert calls==[]
    finally:app.close()


def qc_then_wait(root, session_id):
    """Controlled interruption after an actual native QC stage, not fake results."""
    os.environ['DEVBENCH_REVIEWED_WORKBENCH_SESSION']=session_id
    from devbench.analysis.workbench import run_stage
    run_stage(Path(root),'qc')
    time.sleep(120)


def test_cancel_after_real_qc_resumes_verified_checkpoint_with_remaining_budget(root,monkeypatch):
    from devbench.analysis import workbench_proposals as native
    from devbench.analysis.checkpoints import sha256
    app,request=setup(root,RecordedSelector(),allow_data_execution=True)
    original=native._execute_worker
    try:
        controls(app)
        job=app.register(request)['id'];assert wait(app,job)['status']=='awaiting_operator_review'
        app.review(job,approval());assert wait(app,job)['status']=='approved'
        monkeypatch.setattr(native,'_execute_worker',qc_then_wait)
        app.run(job)
        checkpoint=app.workspace/'jobs'/job/'bundle/checkpoints/qc/receipt.json'
        end=time.monotonic()+90
        while not checkpoint.exists() and time.monotonic()<end:
            state=app.status(job)
            assert state['status'] in {'preparing','running'},state
            time.sleep(.05)
        assert checkpoint.exists(),'Real QC did not checkpoint'
        before=sha256(checkpoint)
        app.cancel(job);stopped=wait(app,job)
        assert stopped['status']=='cancelled' and stopped['resumable'] is True,stopped
        assert stopped['worker_liveness']=='stopped'
        first=stopped['execution_history'][0]
        assert first['status']=='cancelled'
        assert 0 < stopped['remaining_execution_seconds'] < 180
        with pytest.raises(RuntimeError):app.run(job)
        monkeypatch.setattr(native,'_execute_worker',original)
        app.resume(job);finished=wait(app,job,timeout_seconds=app.run_timeout_seconds)
        assert finished['status']=='completed_review_passed',finished.get('execution',finished)
        assert sha256(checkpoint)==before
        assert len(finished['execution_history'])==2
        assert finished['execution_history'][1]['budget_seconds'] < first['budget_seconds']
        assert app.results(job)['verification']
        archive,content_type,_=app.download(job,'archive')
        assert archive.is_file() and content_type=='application/zip'
    finally:app.close()
