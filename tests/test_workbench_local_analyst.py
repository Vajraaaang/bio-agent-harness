"""Guarded Workbench bridge controls with actual small episodes; no provider."""
import json
import time
from pathlib import Path
import pytest

from tests.test_analysis_episodes import workspace, inputs
from tests.test_episode_agent import ScriptedSelector, sequence


def prepared(root):
    task, paths = inputs(root, continuous=False)
    spec = {'question':'Estimate B minus A in observed donors', 'assay':'bulk', 'public_task':task,
            'inputs':{key:str(value) for key,value in paths.items()}}
    path=root/'reviewed-spec.json';path.write_text(json.dumps(spec))
    return path, paths


def app(root, spec, **kwargs):
    from devbench.analysis.workbench_ui import WorkbenchService
    return WorkbenchService(root/'service',[root/'source'], codex_model='gpt-6.1-sol',
        agent_workspaces=root/'actors',allow_model_execution=True,model_data_scope='public',
        allow_data_execution=True,local_analyst_spec=spec,**kwargs)


def grant(bridge):
    return {'spec_sha256':bridge.configuration()['spec_sha256'],
            'allow_data_execution':True,'allow_model_execution':True}


def wait(service, job):
    until=time.monotonic()+90
    while time.monotonic()<until:
        state=service.local_analyst.status(job)
        if not state.get('operation_pending'):return state
        time.sleep(.03)
    pytest.fail('bounded fixture did not finish')


def test_bound_spec_is_explicit_and_unchanged(workspace):
    spec,paths=prepared(workspace)
    service=app(workspace,spec)
    try:
        configuration=service.configuration()['local_analyst']
        assert configuration['enabled'] and configuration['genes']==12
        spec.write_text(spec.read_text()+' ')
        with pytest.raises(ValueError,match='changed'):
            service.local_analyst.run(grant(service.local_analyst))
        assert service.jobs()==[]
    finally:service.close()


def test_input_change_and_missing_request_authority_prevent_queue(workspace):
    spec,paths=prepared(workspace)
    service=app(workspace,spec)
    try:
        with pytest.raises(PermissionError):service.local_analyst.run({})
        paths['expression'].write_text(paths['expression'].read_text()+'\n')
        with pytest.raises(ValueError,match='changed'):
            service.local_analyst.run(grant(service.local_analyst))
        assert service.jobs()==[]
    finally:service.close()


def test_existing_driver_runs_once_and_retains_verified_downloads(workspace,monkeypatch):
    spec,paths=prepared(workspace)
    service=app(workspace,spec)
    monkeypatch.setattr(service.local_analyst,'_selector',lambda episode:ScriptedSelector(sequence()))
    try:
        first=service.local_analyst.run(grant(service.local_analyst))
        assert service.local_analyst.run(grant(service.local_analyst))['id']==first['id']
        state=wait(service,first['id'])
        assert state['status']=='completed'
        assert state['episode_verification']['valid']
        assert state['summary']['family_genes']==12
        assert state['summary']['row_status_counts']=={'estimated':12}
        assert state['summary']['scientific_correctness'] is None
        path,_,_=service.local_analyst.download(first['id'],'summary')
        assert 'does not validate' in path.read_text()
        path.write_text(path.read_text()+'tamper')
        with pytest.raises(ValueError,match='changed'):
            service.local_analyst.download(first['id'],'summary')
    finally:service.close()


def test_honest_nonestimate_is_complete_without_inventing_values(workspace,monkeypatch):
    import csv
    spec,paths=prepared(workspace)
    with paths['expression'].open() as handle:rows=list(csv.reader(handle))
    rows[-1][1:]=['0']*8
    with paths['expression'].open('w',newline='') as handle:csv.writer(handle).writerows(rows)
    service=app(workspace,spec)
    monkeypatch.setattr(service.local_analyst,'_selector',lambda episode:ScriptedSelector(sequence()))
    try:
        job=service.local_analyst.run(grant(service.local_analyst))['id']
        state=wait(service,job)
        assert state['status']=='completed'
        assert state['summary']['row_status_counts']=={'estimated':11,'failed':1}
        assert state['summary']['failed_public_p_q_null']
        assert state['episode_verification']['valid']
    finally:service.close()


def test_http_route_keeps_origin_and_minimal_request_controls(workspace):
    import threading
    import urllib.request
    import urllib.error
    from devbench.analysis.workbench_ui import make_server
    spec,_=prepared(workspace)
    service=app(workspace,spec)
    server=make_server(service,port=0)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    base='http://127.0.0.1:'+str(server.server_address[1])
    try:
        headers = {}
        with urllib.request.urlopen(urllib.request.Request(base+'/api/local-analyst',headers=headers)) as response:
            assert json.load(response)['job'] is None
        body=json.dumps(grant(service.local_analyst)).encode()
        headers.update({'Content-Type':'application/json','Origin':'http://unrelated.invalid'})
        with pytest.raises(urllib.error.HTTPError) as denied:
            urllib.request.urlopen(urllib.request.Request(base+'/api/local-analyst',data=body,headers=headers))
        assert denied.value.code==403 and service.jobs()==[]
        headers['Origin']=base
        with pytest.raises(urllib.error.HTTPError) as denied:
            urllib.request.urlopen(urllib.request.Request(base+'/api/local-analyst',data=b'{"spec":{}}',headers=headers))
        assert denied.value.code==403 and service.jobs()==[]
    finally:
        server.shutdown();server.server_close();service.close()


def test_existing_queue_cancellation_remains_available(workspace,monkeypatch):
    spec,_=prepared(workspace)
    service=app(workspace,spec)
    monkeypatch.setattr(service.local_analyst,'_selector',lambda episode:ScriptedSelector(sequence()))
    try:
        identity=service.local_analyst.run(grant(service.local_analyst))['id']
        service.cancel(identity)
        state=wait(service,identity)
        assert state['status']=='cancelled'
        assert state['worker_liveness']=='stopped'
    finally:service.close()


def test_replaced_symlink_is_rejected_even_with_identical_bytes(workspace,monkeypatch):
    spec,paths=prepared(workspace)
    service=app(workspace,spec)
    try:
        monkeypatch.setattr(service.local_analyst,'_selector',lambda episode:ScriptedSelector(sequence()))
        original=paths['metadata'];replacement=workspace/'retained-metadata.csv'
        original.rename(replacement);original.symlink_to(replacement)
        with pytest.raises(ValueError,match='symlink|canonical|bounded'):
            service.local_analyst.run(grant(service.local_analyst))
        assert service.jobs()==[]
    finally:service.close()


@pytest.mark.parametrize('role',['metadata','annotations'])
def test_auxiliary_input_keeps_existing_five_mb_bound(workspace,role):
    spec,paths=prepared(workspace)
    paths[role].write_bytes(b'x'*5_000_001)
    with pytest.raises(ValueError,match='bounded|budget'):
        app(workspace,spec)


def test_reviewed_limits_cannot_widen_workbench_startup_profile(workspace):
    spec,_=prepared(workspace)
    value=json.loads(spec.read_text());value['limits']={'memory_mb':4096}
    spec.write_text(json.dumps(value))
    with pytest.raises(ValueError,match='resource|startup|ceiling'):
        app(workspace,spec,parameter_overrides={'memory_mb':1024})


def test_setup_failure_seals_existing_episode_before_task_failure(workspace,monkeypatch):
    spec,_=prepared(workspace)
    service=app(workspace,spec)
    def fail(episode):raise RuntimeError('fixture selector setup failed')
    monkeypatch.setattr(service.local_analyst,'_selector',fail)
    try:
        job=service.local_analyst.run(grant(service.local_analyst))['id']
        state=wait(service,job)
        assert state['status']=='operation_failed'
        assert state['episode_receipt']['root_hash']
        assert state.get('summary',{}).get('failed_public_p_q_null') is None
        from devbench.analysis.episodes import verify_episode
        assert verify_episode(service._path('jobs',job,'episode'),state['episode_receipt']['root_hash'])['valid']
    finally:service.close()


def test_derived_selector_preserves_private_and_input_denials(workspace):
    from types import SimpleNamespace
    spec,_=prepared(workspace)
    service=app(workspace,spec)
    try:
        prior=workspace/'other-private';prior.mkdir()
        service.proposal_selector.denied_roots=(prior,)
        selected=service.local_analyst._selector(SimpleNamespace(root=service.workspace/'jobs'/'episode'))
        assert selected.private_root==service.workspace
        assert prior in selected.denied_roots
        assert all(path in selected.denied_roots for path in service.input_roots)
    finally:service.close()
