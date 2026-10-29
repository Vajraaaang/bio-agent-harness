"""Metadata/state tests only. No scientific fixture is executed or replayed."""
import copy
import hashlib
import importlib
import importlib.util
from pathlib import Path
import sys

import pytest

ROOT = Path('/Users/vajraang/Documents/ChatGPT/bio-agent-harness')


@pytest.fixture(autouse=True)
def _isolate_candidate_freeze_for_engineering_remote_state_tests(monkeypatch):
    """Unit state/ledger tests only; no installed candidate qualification claim."""
    import devbench.analysis.workflow_candidate_freeze as freeze
    monkeypatch.setattr(freeze, 'validate_candidate_manifest',
        lambda *args, **kwargs: {'engineering_unit_stub':True, 'execution_qualified':False})

def _load(name, rel):
    if name.startswith(("devbench.", "devharness_core.")):
        return importlib.import_module(name)
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module

import devharness_core
import devbench.analysis
_load('devharness_core.methods', 'devharness_core/methods.py')
contract = _load('devbench.analysis.colab_execution_contract', 'devbench/analysis/colab_execution_contract.py')
methods = _load('devbench.analysis.methods', 'devbench/analysis/methods.py')


def setup_step(tmp_path):
    service = methods.MethodService(tmp_path / 'workspace')
    source = tmp_path / 'source.py'; source.write_text('# inert source for metadata tests\n')
    lock = tmp_path / 'lock.json'; lock.write_text('{}')
    value = {'format':'devharness.method/1','method_id':'colab_metadata_fixture','name':'Metadata fixture',
      'lifecycle':'custom','implementation_trust':'unreviewed','inputs':{},
      'parameters':{'type':'object','additionalProperties':False,'properties':{}},
      'outputs':{'receipt':{'artifact_type':'json','path':'/outputs/receipt.json','required':True}},
      'implementation':{'kind':'colab','source_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),
                        'entrypoint':['python3','/method/source.py']},
      'environment':{'runtime':'google_colab','lock_sha256':hashlib.sha256(lock.read_bytes()).hexdigest()},
      'execution':{'cpu':1,'memory_mb':256,'timeout_seconds':30,'pids':16,'max_output_bytes':10000,'network':'allow'},
      'validation':{'comparator':'json','invariants':[],'counts_layer':'counts','qc_metrics':[]},
      'provenance':{'origin':'software fixture, not scientific execution'}}
    method = service.register_method(value,source=source,environment_lock=lock,actor='test')
    analysis = service.create_analysis('metadata test', actor='test')
    branch = service.create_branch(analysis['analysis_id'],analysis['revision_id'],actor='test')
    step = service.add_step(branch['branch_id'],method['version_id'],{},{},actor='test')
    return service, step, value


def job(tmp_path):
    service,step,value=setup_step(tmp_path)
    return service,service.prepare_remote_step(step['step_id'],run_id='1'*32,job_id='2'*32,
        source_bundle_sha256='3'*64,validator_sha256='4'*64,scope=contract.SCOPE,
        limits=value['execution'],actor='test')


def test_colab_run_step_rejects_default_and_injected_executor_before_run(tmp_path):
    service,step,_ = setup_step(tmp_path)
    class Trap:
        def execute(self,*args,**kwargs):
            raise AssertionError('local execution reached')
    for executor in (None,Trap()):
        with pytest.raises(PermissionError,match='local execution is forbidden'):
            service.run_step(step['step_id'],actor='test',executor=executor)
    assert service.list_records('run') == []


def test_prepare_is_canonical_metadata_only(tmp_path):
    service,value=job(tmp_path)
    assert contract.validate_job(value)==value
    assert contract.job_sha256(value)==contract.digest(value)
    assert service.list_records('run')==[]
    assert value['policy']['fallback']=='forbidden'
    assert value['policy']['automatic_replay']=='forbidden'


@pytest.mark.parametrize('key,value',[('scope','airway'),('job_id','../escape'),
    ('validator_sha256','invalid'),('source_bundle_sha256',False),('actor','')])
def test_job_rejects_widened_or_malformed_subject(tmp_path,key,value):
    _,document=job(tmp_path);document[key]=value
    with pytest.raises((ValueError,PermissionError,TypeError)):
        contract.validate_job(document)


def test_no_generic_authority_or_receipt_callback(tmp_path):
    service,document=job(tmp_path)
    with pytest.raises(PermissionError,match='exact execution grant authority'):
        service.reserve_remote_step(document,request={},grant={},authority=object(),actor='test')
    assert service.list_records('run')==[]


def test_reconcile_never_docker_for_remote_pending(tmp_path,monkeypatch):
    service,_=job(tmp_path)
    record={'run_id':'a'*32,'status':'remote_pending','backend':'google_colab'}
    with service._db() as db:service._put(db,'run',record['run_id'],record)
    def trap(*args,**kwargs):raise AssertionError('Docker reached')
    monkeypatch.setattr(methods,'LocalDockerExecutor',trap)
    assert service.reconcile()==[]
    assert service.get('run',record['run_id'])==record

imports = _load('devbench.analysis.colab_result_import', 'devbench/analysis/colab_result_import.py')


def fake_return(tmp_path, document, *, mutate=None):
    """Opaque JSON custody fixture. It never qualifies remote computation."""
    import zipfile
    from devharness_core.provenance import canonical_json
    root=contract.job_sha256(document)
    blobs={'outputs/receipt.json':canonical_json({'software_fixture':True})}
    validation={'format':'devbench.colab-remote-validation/1','job_sha256':root,
        'validator_sha256':document['validator_sha256'],'passed':True,
        'checks':{name:True for name in contract.REQUIRED_VALIDATION_CHECKS},'numerical_execution_tier':'colab'}
    runtime={'format':'devbench.colab-runtime/1','job_sha256':root,'backend':'google_colab',
        'remote_session_id':'5'*64,'python_version':'fixture','platform':'fixture','network_enforcement':'not_proven'}
    blobs['validation.json']=canonical_json(validation);blobs['runtime.json']=canonical_json(runtime)
    result={'format':'devbench.colab-method-result/1', **{key:document[key] for key in
        ('job_id','run_id','method_version_id','source_bundle_sha256','validator_sha256')},
        'job_sha256':root,'status':'succeeded','exit_code':0,
        'outputs':{'receipt':{'path':'outputs/receipt.json','sha256':hashlib.sha256(blobs['outputs/receipt.json']).hexdigest(),
            'size_bytes':len(blobs['outputs/receipt.json'])}},
        'validation_sha256':hashlib.sha256(blobs['validation.json']).hexdigest(),
        'runtime_sha256':hashlib.sha256(blobs['runtime.json']).hexdigest()}
    if mutate: mutate(result)
    blobs['result.json']=canonical_json(result)
    root_receipt={'format':'devbench.colab-result-root/1','job_sha256':root,
        'members':{name:{'sha256':hashlib.sha256(body).hexdigest(),'size_bytes':len(body)} for name,body in blobs.items()}}
    expected=contract.digest(root_receipt);blobs['root-receipt.json']=canonical_json(root_receipt)
    archive=tmp_path/'return.zip'
    with zipfile.ZipFile(archive,'w') as bundle:
        for name,body in blobs.items():
            info=zipfile.ZipInfo(name);info.external_attr=0o100444<<16;bundle.writestr(info,body)
    projection={'format':'devbench.colab-transport-receipt/1','job_id':document['job_id'],'job_sha256':root,
        'source_bundle_sha256':document['source_bundle_sha256'],'backend':'google_colab',
        'notebook_id':None,'remote_session_id':'5'*64,'dispatch_id':'7'*32,
        'stage':'returned','result_archive_sha256':hashlib.sha256(archive.read_bytes()).hexdigest(),'expected_root_sha256':expected}
    dispatched={**projection,'stage':'dispatched','result_archive_sha256':None,'expected_root_sha256':None}
    return archive,expected,projection,dispatched


def test_structural_result_import_does_no_local_scientific_validation(tmp_path,monkeypatch):
    _,document=job(tmp_path)
    archive,root,returned,dispatched=fake_return(tmp_path,document)
    monkeypatch.setattr(methods,'validate_outputs',lambda *_: (_ for _ in ()).throw(AssertionError('local validation')))
    result=imports.verify_result_archive(document,archive,root,destination=tmp_path/'imported',
        transport_projection=returned,dispatch_projection=dispatched)
    assert result['outputs']['receipt'].is_file()
    assert result['validation']['passed'] is True


@pytest.mark.parametrize('change',[lambda r:r.update(job_id='9'*32),
    lambda r:r.update(exit_code=False),lambda r:r.update(status='failed'),
    lambda r:r.update(extra='untrusted'),lambda r:r['outputs']['receipt'].update(path='../escape')])
def test_structural_import_rejects_altered_result(tmp_path,change):
    _,document=job(tmp_path)
    archive,root,returned,dispatched=fake_return(tmp_path,document,mutate=change)
    with pytest.raises((ValueError,KeyError)):
        imports.verify_result_archive(document,archive,root,destination=tmp_path/'imported',
            transport_projection=returned,dispatch_projection=dispatched)
    assert not (tmp_path/'imported').exists()


def test_import_rejects_changed_transport_session(tmp_path):
    _,document=job(tmp_path)
    archive,root,returned,dispatched=fake_return(tmp_path,document)
    returned['remote_session_id']='8'*64
    with pytest.raises(ValueError,match='session changed'):
        imports.verify_result_archive(document,archive,root,destination=tmp_path/'imported',
            transport_projection=returned,dispatch_projection=dispatched)


def test_colab_network_claim_cannot_impersonate_denial(tmp_path):
    _,document=job(tmp_path)
    document['method_contract']['execution']['network']='deny'
    document['limits']['network']='deny'
    document['method_version_id']=methods.MethodContract.from_dict(document['method_contract']).version_id
    with pytest.raises(PermissionError,match='honestly declare'):
        contract.validate_job(document)


def test_real_approval_reservation_failure_stays_charged_and_sealed(tmp_path):
    import sqlite3
    _load('devbench.analysis.colab_qualification_contract','devbench/analysis/colab_qualification_contract.py')
    _load('devbench.analysis.colab_execution_authority','devbench/analysis/colab_execution_authority.py')
    _load('devbench.analysis.rna_trial_authority','devbench/analysis/rna_trial_authority.py')
    authority_tests=_load('colab_authority_fixture','tests/test_colab_qualification_authority.py')
    request,document,authority,grant,_=authority_tests.fixture(tmp_path)
    service=authority.colab_qualification_context.service
    reserved=service.reserve_remote_step(document,request=request,grant=grant,authority=authority,actor='operator')
    assert reserved['status']=='remote_pending'
    assert reserved['backend']=='google_colab'
    with pytest.raises(PermissionError,match='already reserved'):
        service.reserve_remote_step(document,request=request,grant=grant,authority=authority,actor='operator')
    failed=service.finalize_remote_failure(document['job_id'],category='operator_stop',uncertain=True,actor='operator')
    assert failed['status']=='remote_uncertain'
    assert failed['approval_charge_retained'] is True
    assert service.provenance(document['run_id'])['valid'] is True
    assert service.reconcile()==[]
    with sqlite3.connect(authority.path) as db:
        assert db.execute('select count(*) from grants').fetchone()[0]==1
        assert db.execute('select count(*) from dispatches').fetchone()[0]==1


@pytest.mark.parametrize('mutation',[lambda d:d['policy'].update(max_runs=True),lambda d:d['limits'].update(cpu=True)])
def test_job_rejects_boolean_integer_equivalence(tmp_path,mutation):
    _,document=job(tmp_path);mutation(document)
    with pytest.raises((ValueError,PermissionError)):
        contract.validate_job(document)


def test_notebook_json_cannot_mint_authenticated_transport_receipt(tmp_path):
    transport=_load('devbench.analysis.colab_transport','devbench/analysis/colab_transport.py')
    service,document=job(tmp_path)
    with pytest.raises(PermissionError,match='authenticated Colab'):
        service._remote_transport_projection({'accepted':True},document,'returned')
    with pytest.raises(PermissionError,match='trusted transport'):
        transport.ColabTransportObservation(body={'accepted':True},owner=object())


def test_remote_claim_one_use_revalidation_expiry_and_restart(tmp_path):
    from datetime import datetime,timezone
    _load('devbench.analysis.colab_qualification_contract','devbench/analysis/colab_qualification_contract.py')
    _load('devbench.analysis.colab_execution_authority','devbench/analysis/colab_execution_authority.py')
    _load('devbench.analysis.rna_trial_authority','devbench/analysis/rna_trial_authority.py')
    authority_tests=_load('colab_authority_fixture','tests/test_colab_qualification_authority.py')
    request,document,authority,grant,_=authority_tests.fixture(tmp_path)
    service=authority.colab_qualification_context.service
    record=service.reserve_remote_step(document,request=request,grant=grant,authority=authority,actor='operator')
    # Opaque export metadata fixture: no package runner or scientific code invoked.
    with service._db() as db:
        record['export']={'software_fixture':True}
        service._put(db,'run',document['run_id'],record,update=True)
    claim=service.claim_remote_transport(document['job_id'],'a'*32,'b'*64)
    assert service.revalidate_remote_dispatch(document['job_id'])==claim
    with pytest.raises(PermissionError,match='already spent'):
        service.claim_remote_transport(document['job_id'],'c'*32,'d'*64)
    restarted=methods.MethodService(service.root)
    with pytest.raises(PermissionError,match='live trusted'):
        restarted.revalidate_remote_dispatch(document['job_id'])
    authority.clock=lambda:datetime(2026,10,24,tzinfo=timezone.utc)
    with pytest.raises(PermissionError):
        service.revalidate_remote_dispatch(document['job_id'])


@pytest.mark.parametrize('operation',['export','failure'])
def test_remote_terminal_and_export_operations_share_import_lease(tmp_path,operation):
    import fcntl
    _load('devbench.analysis.colab_qualification_contract','devbench/analysis/colab_qualification_contract.py')
    _load('devbench.analysis.colab_execution_authority','devbench/analysis/colab_execution_authority.py')
    _load('devbench.analysis.rna_trial_authority','devbench/analysis/rna_trial_authority.py')
    authority_tests=_load('colab_authority_fixture','tests/test_colab_qualification_authority.py')
    request,document,authority,grant,_=authority_tests.fixture(tmp_path)
    service=authority.colab_qualification_context.service
    record=service.reserve_remote_step(document,request=request,grant=grant,authority=authority,actor='operator')
    lease_path=service.root/'runs'/document['run_id']/'remote-operation-lease'
    with lease_path.open('ab') as lease:
        fcntl.flock(lease,fcntl.LOCK_EX|fcntl.LOCK_NB)
        with pytest.raises(PermissionError,match='Another remote operation'):
            if operation=='export':service.export_remote_job(document['job_id'],tmp_path/'export',actor='operator')
            else:service.finalize_remote_failure(document['job_id'],category='operator_stop',uncertain=True,actor='operator')
    assert service.get('run',document['run_id'])==record
    assert not (tmp_path/'export').exists()
