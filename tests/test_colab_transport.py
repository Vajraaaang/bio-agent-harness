"""Software/custody tests using fake transport operations; no numerical execution."""
import copy
import hashlib
import importlib
import importlib.util
import json
from pathlib import Path
import sys
import types
import zipfile

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load(name, path):
    if name.startswith(("devbench.", "devharness_core.")):
        return importlib.import_module(name)
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


import devharness_core
import devbench.analysis
_load('devharness_core.methods', ROOT / 'devharness_core/methods.py')
_load('devbench.analysis.colab_execution_contract', ROOT / 'devbench/analysis/colab_execution_contract.py')
methods = _load('devbench.analysis.methods', ROOT / 'devbench/analysis/methods.py')
transport = _load('devbench.analysis.colab_transport', ROOT / 'devbench/analysis/colab_transport.py')
metadata_tests = _load('colab_metadata_fixture_helpers', ROOT / 'tests/test_colab_remote_methods.py')
methods = metadata_tests.methods


def fixture(tmp_path, monkeypatch):
    service, job = metadata_tests.job(tmp_path)
    payload = tmp_path / 'payload.zip'; payload.write_bytes(b'inert software payload')
    notebook = tmp_path / 'job.ipynb'; notebook.write_text('{"cells":[]}')
    execution = tmp_path / 'execution.py'; execution.write_text('# inert remote submission source\n')
    body_hash = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
    ref = lambda p: {'path':str(p),'sha256':body_hash(p),'size_bytes':p.stat().st_size}
    export = {'format':'devbench.colab-method-export/1','job_id':job['job_id'],
      'job_sha256':transport.job_sha256(job),'source_bundle_sha256':job['source_bundle_sha256'],
      'payload':ref(payload),'notebook':ref(notebook),'execution_file':ref(execution),
      'remote_payload_name':'colab-source.zip','remote_result_name':'colab-result.zip',
      'scientific_execution_tier':'colab','local_fallback':'forbidden','automatic_replay':'forbidden'}
    record = {'status':'remote_pending','reservation':{'software_fixture':True},'export':export}
    monkeypatch.setattr(service, '_remote_job', lambda _: (copy.deepcopy(job), copy.deepcopy(record)))
    claims = []
    def claim(identity, **kwargs):
        if claims: raise PermissionError('already claimed')
        claims.append((identity,kwargs))
        return {'format':'devbench.colab-transport-claim/1','job_id':identity,
                'job_sha256':transport.job_sha256(job),**kwargs}
    monkeypatch.setattr(service, 'claim_remote_transport', claim, raising=False)
    def revalidate(identity):
        return {'format':'devbench.colab-transport-claim/1','job_id':identity,
                'job_sha256':transport.job_sha256(job),**claims[0][1]}
    monkeypatch.setattr(service, 'revalidate_remote_dispatch', revalidate, raising=False)
    obj = transport.OfficialColabTransport(private_root=tmp_path/'owned-private', service=service)
    calls = []
    monkeypatch.setattr(obj, '_cli', lambda command,*args,**kwargs: calls.append((command,args,kwargs)))
    monkeypatch.setattr(obj, '_observe', lambda operation,**_: 'absent' if operation=='verify' else 'present')
    return obj, job, record, calls, claims


def test_official_public_source_pins_are_current():
    transport._verify_pins()


def test_observation_cannot_be_created_from_notebook_or_dict():
    with pytest.raises(PermissionError):
        transport.ColabTransportObservation(body={'accepted':True})


def test_constructor_creates_only_owned_private_state(tmp_path):
    service = methods.MethodService(tmp_path/'service')
    obj = transport.OfficialColabTransport(private_root=tmp_path/'private',service=service)
    assert obj.state == 'new' and obj.events == []
    assert obj.private_root.stat().st_mode & 0o777 == 0o700
    assert (obj.home/'.config/colab-cli/settings.json').read_bytes() == b'{"enable_update_check":false}'
    assert not obj.config.exists()
    with pytest.raises(FileExistsError):
        transport.OfficialColabTransport(private_root=obj.private_root,service=service)


def test_constructor_rejects_generic_service_before_private_state(tmp_path):
    with pytest.raises(TypeError):
        transport.OfficialColabTransport(private_root=tmp_path/'private',service=object())
    assert not (tmp_path/'private').exists()


def test_dispatch_spends_claim_before_provision_and_does_not_exec(tmp_path,monkeypatch):
    obj,job,record,calls,claims = fixture(tmp_path,monkeypatch)
    observation = obj.dispatch(job['job_id'])
    value = observation.verify(job=job,stage='dispatched')
    assert type(observation) is transport.ColabTransportObservation
    assert claims and [c[0] for c in calls] == ['new','upload','upload']
    assert value['notebook_id'] is None and len(value['remote_session_id']) == 64
    assert len(value['dispatch_id']) == 32 and value['expected_root_sha256'] is None
    assert value['result_archive_sha256'] is None
    with pytest.raises(PermissionError): obj.dispatch(job['job_id'])


def test_missing_consumed_grant_never_calls_cli(tmp_path,monkeypatch):
    obj,job,record,calls,claims = fixture(tmp_path,monkeypatch)
    record['reservation'] = None
    with pytest.raises(PermissionError): obj.dispatch(job['job_id'])
    assert calls == [] and claims == [] and obj.state == 'spent'


def test_changed_export_never_calls_cli(tmp_path,monkeypatch):
    obj,job,record,calls,claims = fixture(tmp_path,monkeypatch)
    Path(record['export']['payload']['path']).write_bytes(b'changed')
    with pytest.raises(ValueError): obj.dispatch(job['job_id'])
    assert calls == [] and claims == []


def test_duplicate_persistent_claim_never_provisions_again(tmp_path,monkeypatch):
    obj,job,record,calls,claims = fixture(tmp_path,monkeypatch)
    claims.append(('spent',{}))
    with pytest.raises(PermissionError): obj.dispatch(job['job_id'])
    assert calls == []


def test_assignment_observation_unknown_stops_without_receipt(tmp_path,monkeypatch):
    obj,job,record,calls,claims = fixture(tmp_path,monkeypatch)
    monkeypatch.setattr(obj,'_observe',lambda *_args,**_kwargs:'unknown')
    with pytest.raises(RuntimeError): obj.dispatch(job['job_id'])
    assert [c[0] for c in calls] == ['new','stop']
    assert obj.cleanup == {'stop_requested':True,'presence':'unknown'}
    assert not (obj.private_root/'witness-dispatched.json').exists()


def test_witness_tampering_and_wrong_stage_rejected(tmp_path,monkeypatch):
    obj,job,record,calls,claims = fixture(tmp_path,monkeypatch)
    observed = obj.dispatch(job['job_id'])
    with pytest.raises(PermissionError): observed.verify(job=job,stage='returned')
    (obj.private_root/'witness-dispatched.json').write_bytes(b'{}')
    with pytest.raises(PermissionError): observed.verify(job=job,stage='dispatched')


def test_expired_before_execution_stops_and_never_submits(tmp_path,monkeypatch):
    obj,job,record,calls,claims = fixture(tmp_path,monkeypatch)
    obj.dispatch(job['job_id']); record['status']='remote_dispatched'
    def denied(_): raise PermissionError('expired approval')
    monkeypatch.setattr(obj.service,'revalidate_remote_dispatch',denied)
    with pytest.raises(PermissionError): obj.collect()
    assert [c[0] for c in calls] == ['new','upload','upload','stop']
    assert obj.state == 'spent' and obj.cleanup['presence']=='absent'


def test_cli_zero_does_not_imply_result_success(tmp_path,monkeypatch):
    obj,job,record,calls,claims = fixture(tmp_path,monkeypatch)
    obj.dispatch(job['job_id']); record['status']='remote_dispatched'
    monkeypatch.setattr(obj,'_observe',lambda operation,**_:'absent' if operation=='verify' else 'unknown')
    with pytest.raises(RuntimeError): obj.collect()
    assert sum(c[0]=='exec' for c in calls)==1 and calls[-1][0]=='stop'
    with pytest.raises(PermissionError): obj.collect()


def test_fake_channel_metadata_return_has_closed_witness(tmp_path,monkeypatch):
    obj,job,record,calls,claims = fixture(tmp_path,monkeypatch)
    obj.dispatch(job['job_id']); record['status']='remote_dispatched'
    original = obj._cli
    def fake(command,*args,**kwargs):
        original(command,*args,**kwargs)
        if command=='download':
            with zipfile.ZipFile(Path(args[1]),'w') as archive:
                archive.writestr('root-receipt.json',b'{"format":"software-only-fixture"}')
    monkeypatch.setattr(obj,'_cli',fake)
    path,receipt = obj.collect()
    value = receipt.verify(job=job,stage='returned')
    assert value['result_archive_sha256']==hashlib.sha256(path.read_bytes()).hexdigest()
    assert value['expected_root_sha256'] and obj.cleanup['presence']=='absent'
    assert sum(c[0]=='exec' for c in calls)==1
    assert not {'endpoint','token','url','email'} & set(value)


def test_unknown_cleanup_prevents_accepted_return(tmp_path,monkeypatch):
    obj,job,record,calls,claims = fixture(tmp_path,monkeypatch)
    obj.dispatch(job['job_id']); record['status']='remote_dispatched'
    original = obj._cli
    def fake(command,*args,**kwargs):
        original(command,*args,**kwargs)
        if command=='download':
            with zipfile.ZipFile(Path(args[1]),'w') as archive: archive.writestr('root-receipt.json',b'{}')
    monkeypatch.setattr(obj,'_cli',fake)
    monkeypatch.setattr(obj,'_observe',lambda operation,**_:'unknown' if operation=='verify' else 'present')
    with pytest.raises(RuntimeError): obj.collect()
    assert not (obj.private_root/'witness-returned.json').exists()


def test_forbidden_local_or_arbitrary_command_rejected_before_popen(tmp_path,monkeypatch):
    obj,job,record,calls,claims = fixture(tmp_path,monkeypatch)
    obj._deadline=transport.time.monotonic()+10
    monkeypatch.setattr(transport,'_verify_pins',lambda:None)
    with pytest.raises(PermissionError): obj._run(['-c','print("arbitrary")'],seconds=1)


def test_fixed_helpers_compile_without_running_auth_or_computation():
    compile(transport._RESOURCE_WRAPPER,'resource-wrapper','exec')
    compile(transport._OPAQUE_HELPER,'opaque-helper','exec')


def test_command_plan_is_fixed_and_explicit_oauth2(tmp_path,monkeypatch):
    obj,job,record,calls,claims=fixture(tmp_path,monkeypatch)
    obj._job=job;obj._bind_export(record['export'])
    plans=[]
    monkeypatch.setattr(obj,'_run',lambda arguments,**kwargs:plans.append((arguments,kwargs)))
    fixed=transport.OfficialColabTransport._cli
    fixed(obj,'new')
    fixed(obj,'exec','--timeout','90','-f',str(obj.execution_file),seconds=100)
    assert plans[0][0]==['-m','colab_cli.cli','--auth','oauth2','--config',str(obj.config),'new','-s',obj.session_name]
    with pytest.raises(PermissionError): fixed(obj,'rm','content/anything')
    with pytest.raises(PermissionError): fixed(obj,'upload',str(obj.bundle),'content/other.zip')
    with pytest.raises(PermissionError): fixed(obj,'exec','--timeout','999','-f',str(obj.execution_file))
    assert len(plans)==2


@pytest.mark.parametrize('operation,present,fail,expected', [
    ('capture',True,False,'present'), ('verify',False,False,'absent'),
    ('verify',False,True,'unknown'), ('inventory',True,False,'present'),
])
def test_opaque_official_helper_projects_only_closed_status(tmp_path,monkeypatch,capsys,
                                                          operation,present,fail,expected):
    endpoint = 'inert-private-endpoint-marker'
    entry = types.SimpleNamespace(endpoint=endpoint)
    def assignments():
        if fail: raise SystemExit('never-return-raw-auth-error')
        return [entry] if present else []
    state = types.SimpleNamespace(store=types.SimpleNamespace(get=lambda _:entry),
                                  client=types.SimpleNamespace(list_assignments=assignments))
    common = types.ModuleType('colab_cli.common'); common.state=state
    auth = types.ModuleType('colab_cli.auth'); auth.AuthProvider=types.SimpleNamespace(OAUTH2='oauth2')
    contents = types.ModuleType('colab_cli.contents')
    class Contents:
        def __init__(self,_): pass
        def _request(self,*_args,**_kwargs): return {'type':'file','size':10}
    contents.ContentsClient=Contents
    for name,module in (('colab_cli.common',common),('colab_cli.auth',auth),('colab_cli.contents',contents)):
        monkeypatch.setitem(sys.modules,name,module)
    private = tmp_path/'endpoint.json'
    if operation!='capture': private.write_text(json.dumps({'endpoint':endpoint}))
    monkeypatch.setattr(sys,'argv',['fixed',operation,str(tmp_path/'sessions.json'),'fixed-name',str(private),'100'])
    exec(compile(transport._OPAQUE_HELPER,'fixed-owned-observation','exec'),{'__name__':'__main__'})
    output = capsys.readouterr()
    value = json.loads(output.out)
    assert value['presence']==expected and output.err==''
    assert endpoint not in output.out and 'never-return-raw-auth-error' not in output.out
    assert set(value)==({'presence','bounded_result_inventory'} if operation=='inventory' else {'presence'})
