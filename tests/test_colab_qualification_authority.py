"""Approval-state/schema checks only; never execute a scientific method or verifier."""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from devharness_core.provenance import canonical_json
from devbench.analysis.checkpoints import digest
from devbench.analysis.colab_qualification_contract import (
    validate_colab_qualification_request, unsigned_colab_qualification_grant_subject)
from devbench.analysis.colab_execution_authority import ColabQualificationContext
from devbench.analysis.rna_trial_authority import AuthenticatedTrialAuthority

NOW = datetime(2026, 10, 22, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _isolate_candidate_freeze_for_engineering_authority_tests(monkeypatch):
    """Isolate grant/profile metadata; these mocks never qualify a candidate.

    Actual source/wheel/RECORD/origin verification is a separate required test
    against the final installed candidate. The production verifier is unchanged.
    """
    import devbench.analysis.workflow_candidate_freeze as freeze
    monkeypatch.setattr(freeze, 'validate_candidate_manifest',
        lambda *args, **kwargs: {'engineering_unit_stub':True, 'execution_qualified':False})


def fixture(tmp_path):
    from devbench.analysis.methods import MethodService
    from devbench.analysis.colab_execution_contract import POLICY
    def ref(name, body):
        path = tmp_path / name
        data = body if type(body) is bytes else canonical_json(body)
        path.write_bytes(data)
        return {'path': str(path), 'sha256': hashlib.sha256(data).hexdigest(), 'size_bytes': len(data)}
    assets = Path('/Users/vajraang/Documents/ChatGPT/bio-agent-harness/reports/rna-colab-integration/2026-10-22-v1/candidate/qualification')
    source = ref('method.py', (assets/'method.py').read_bytes())
    lock = ref('lock.json', (assets/'environment-lock.json').read_bytes())
    fixture_ref = ref('fixture.json', (assets/'fixture.json').read_bytes())
    contract = {'format':'devharness.method/1','method_id':'colab.synthetic','name':'Metadata fixture',
        'lifecycle':'custom','implementation_trust':'unreviewed','inputs':{'fixture':{'artifact_type':'json','required':True}},
        'parameters':{'type':'object','additionalProperties':False,'properties':{}},
        'outputs':{'result':{'artifact_type':'json','path':'/outputs/result.json','required':True}},
        'implementation':{'kind':'colab','source_sha256':source['sha256'],'entrypoint':['python','/method/main.py']},
        'environment':{'runtime':'google_colab','lock_sha256':lock['sha256']},
        'execution':{'cpu':1,'memory_mb':256,'timeout_seconds':30,'pids':32,'max_output_bytes':1000000,'network':'allow'},
        'validation':{'comparator':'json','invariants':[],'counts_layer':'X','qc_metrics':[]},
        'provenance':{'origin':'software_test'}}
    service = MethodService(tmp_path / 'methods')
    method = service.register_method(contract, source=source['path'], environment_lock=lock['path'], actor='operator')
    analysis = service.create_analysis('Metadata only', actor='operator')
    branch = service.create_branch(analysis['analysis_id'], analysis['revision_id'], actor='operator')
    fixture_artifact = service.import_artifact(fixture_ref['path'], artifact_type='json', actor='operator')
    step = service.add_step(branch['branch_id'], method['version_id'], {'fixture':fixture_artifact['artifact_id']}, {}, actor='operator')
    validator = ref('validator.py', (assets/'verifier.py').read_bytes())
    expectations = ref('expected.json', (assets/'expected_values.json').read_bytes())
    bundle = ref('source.zip', b'opaque fixture, packaging not computation')
    job = {'format':'devbench.colab-method-job/1','job_id':'1'*32,'run_id':'2'*32,'step_id':step['step_id'],
        'method_version_id':method['version_id'],'method_contract':contract,
        'source':{k:source[k] for k in ('sha256','size_bytes')},
        'environment_lock':{k:lock[k] for k in ('sha256','size_bytes')},
        'inputs':{'fixture':{**{k:fixture_ref[k] for k in ('sha256','size_bytes')},'artifact_type':'json'}},'parameters':{},
        'source_bundle_sha256':bundle['sha256'],'validator_sha256':validator['sha256'],
        'scope':'synthetic_colab_qualification','limits':contract['execution'],'actor':'operator','correlation':None,'policy':POLICY}
    job_ref = ref('job.json', job)
    from devbench.analysis.workflow_protocol_qualification import ProtocolQualificationProfile
    candidate = ref('candidate-unit-manifest.json', {'format':'engineering-unit-placeholder/1',
        'execution_qualified':False, 'independent_candidate_verification':'required'})
    profile = ProtocolQualificationProfile(job_id=job['job_id'], job_sha256=digest(job),
        source_bundle_sha256=job['source_bundle_sha256'], candidate_manifest_ref=candidate,
        candidate_sha256=candidate['sha256'], sources={'method':source,'fixture':fixture_ref,
            'verifier':validator,'expectations':expectations,'environment_lock':lock})
    request = {'format':'devbench.colab-synthetic-qualification-request/1','scope':'synthetic_colab_qualification',
        'qualification_id':'3'*32,'job_id':job['job_id'],'job_ref':job_ref,'job_sha256':digest(job),
        'user_authorization_ref':ref('authorization.json',{'instruction':'one bounded synthetic Colab qualification'}),
        'roles':{'operator':'operator','reviewer':'reviewer'},'workflow_ref':ref('workflow.json',profile.model_dump(mode='json')),
        'bindings':{'method_version_id':method['version_id'],'step_id':step['step_id'],
            'source_sha256':source['sha256'],'environment_lock_sha256':lock['sha256'],'validator_ref':validator,
            'expected_values_ref':expectations,
            'runtime_policy_ref':ref('policy.json',POLICY),'source_bundle_ref':bundle},
        'allocation':{'limits':deepcopy(job['limits']),'max_runs':1},
        'restrictions':{'local_scientific_compute':False,'local_fallback':False,'model_generation':False,'airway':False},
        'expires_at':'2026-10-23T00:00:00Z'}
    private = Ed25519PrivateKey.generate()  # Ephemeral software-test key, never an enrolled issuer.
    context = ColabQualificationContext(service, approved_request_sha256=digest(request), approved_job_sha256=digest(job))
    authority = AuthenticatedTrialAuthority(tmp_path/'authority.sqlite', workspace=tmp_path,
        issuer_keys={'test':private.public_key()}, runtime_verifier=lambda _: (_ for _ in ()).throw(AssertionError('native route called')),
        clock=lambda:NOW, colab_qualification_context=context)
    payload = unsigned_colab_qualification_grant_subject(request, issuer_id='test', grant_id='one', now=NOW)
    grant = {**payload, 'signature':private.sign(canonical_json(payload)).hex()}
    return request, job, authority, grant, private


def test_exact_one_use_atomic_reservation(tmp_path):
    request, job, authority, grant, _ = fixture(tmp_path)
    result = authority.consume_and_reserve_colab_qualification(request, grant, dispatch_id=job['job_id'], allocation=job['limits'], job=job)
    assert set(result) == {'format','job_id','job_sha256','grant_id','dispatch_id','request_sha256','allocation'}
    assert result['request_sha256'] == digest(request)
    with sqlite3.connect(authority.path) as db:
        assert db.execute('select count(*) from grants').fetchone()[0] == 1
        assert db.execute('select count(*) from dispatches').fetchone()[0] == 1
    with pytest.raises(PermissionError):
        authority.consume_and_reserve_colab_qualification(request, grant, dispatch_id=job['job_id'], allocation=job['limits'], job=job)


@pytest.mark.parametrize('change', ['scope','extra','bool_run','fallback','expiry','job','signature','allocation','dispatch','bool_limit'])
def test_invalid_requests_leave_no_consumption(tmp_path, change):
    request, job, authority, grant, _ = fixture(tmp_path)
    allocation, dispatch = job['limits'], job['job_id']
    if change=='scope': request['scope']='airway'
    if change=='extra': request['native_accepted']=True
    if change=='bool_run': request['allocation']['max_runs']=True
    if change=='fallback': request['restrictions']['local_fallback']=True
    if change=='expiry': request['expires_at']='2026-10-21T00:00:00Z'
    if change=='job': job['job_id']='4'*32
    if change=='signature': grant['signature']='00'*64
    if change=='allocation': allocation={**allocation,'timeout_seconds':31}
    if change=='dispatch': dispatch='4'*32
    if change=='bool_limit': request['allocation']['limits']['cpu']=True
    with pytest.raises((PermissionError,ValueError)):
        authority.consume_and_reserve_colab_qualification(request, grant, dispatch_id=dispatch, allocation=allocation, job=job)
    with sqlite3.connect(authority.path) as db:
        assert db.execute('select count(*) from grants').fetchone()[0] == 0
        assert db.execute('select count(*) from dispatches').fetchone()[0] == 0


def test_changed_bounded_custody_rejected(tmp_path):
    request, job, authority, grant, _ = fixture(tmp_path)
    Path(request['bindings']['validator_ref']['path']).write_text('changed')
    with pytest.raises((PermissionError,ValueError)):
        authority.consume_and_reserve_colab_qualification(request, grant, dispatch_id=job['job_id'], allocation=job['limits'], job=job)


def test_expired_after_approval_denied(tmp_path):
    request, job, authority, grant, _ = fixture(tmp_path)
    authority.clock=lambda:datetime(2026,10,24,tzinfo=timezone.utc)
    with pytest.raises(PermissionError):
        authority.consume_and_reserve_colab_qualification(request, grant, dispatch_id=job['job_id'], allocation=job['limits'], job=job)


def test_unconfigured_authority_cannot_bootstrap(tmp_path):
    request, job, authority, grant, _=fixture(tmp_path)
    authority.colab_qualification_context=None
    with pytest.raises(PermissionError):
        authority.consume_and_reserve_colab_qualification(request,grant,dispatch_id=job['job_id'],allocation=job['limits'],job=job)


def test_postinsert_custody_failure_rolls_back_both_tables(tmp_path,monkeypatch):
    request,job,authority,grant,_=fixture(tmp_path)
    original=authority._validated_colab_qualification
    count=[0]
    def check(*args,**kwargs):
        count[0]+=1
        if count[0]==3:
            raise PermissionError('fixture post-insert custody failure')
        return original(*args,**kwargs)
    monkeypatch.setattr(authority,'_validated_colab_qualification',check)
    with pytest.raises(PermissionError,match='post-insert'):
        authority.consume_and_reserve_colab_qualification(request,grant,dispatch_id=job['job_id'],allocation=job['limits'],job=job)
    with sqlite3.connect(authority.path) as db:
        assert db.execute('select count(*) from grants').fetchone()[0]==0
        assert db.execute('select count(*) from dispatches').fetchone()[0]==0


def test_changed_grant_id_cannot_reuse_binding(tmp_path):
    request,job,authority,grant,private=fixture(tmp_path)
    authority.consume_and_reserve_colab_qualification(request,grant,dispatch_id=job['job_id'],allocation=job['limits'],job=job)
    payload=unsigned_colab_qualification_grant_subject(request,issuer_id='test',grant_id='two',now=NOW)
    changed={**payload,'signature':private.sign(canonical_json(payload)).hex()}
    with pytest.raises(PermissionError,match='replay|consumed'):
        authority.consume_and_reserve_colab_qualification(request,changed,dispatch_id=job['job_id'],allocation=job['limits'],job=job)


def test_public_export_does_not_consume_and_detaches(tmp_path):
    request,job,authority,grant,_=fixture(tmp_path)
    exported=authority.export_colab_public_approval(request,grant,job=job)
    assert exported['issuer']['issuer_id']=='test'
    assert len(exported['issuer']['public_key_hex'])==64
    exported['request']['roles']['operator']='changed'
    assert request['roles']['operator']=='operator'
    with sqlite3.connect(authority.path) as db:
        assert db.execute('select count(*) from grants').fetchone()[0]==0


def test_signer_rejects_unapproved_subject_before_any_key_access(tmp_path,monkeypatch):
    from devbench.analysis import rna_grant_signing as signer
    request,job,authority,grant,_=fixture(tmp_path)
    def denied():
        raise AssertionError('Keychain access is forbidden in local software tests')
    monkeypatch.setattr(signer,'_MacOSExistingResearchItem',denied)
    payload={key:value for key,value in grant.items() if key!='signature'}
    with pytest.raises(PermissionError,match='deliberate approval'):
        signer.sign_approved_colab_qualification(request,payload,authority=authority,proposal={},
            approved_subject_sha256='0'*64,accepted_fingerprint_sha256='0'*64)


def test_context_rejects_arbitrary_service_or_callback(tmp_path):
    with pytest.raises(TypeError):
        ColabQualificationContext(object(),approved_request_sha256='1'*64,approved_job_sha256='2'*64)


def test_signing_preflight_rechecks_custody_after_yield(tmp_path):
    request,job,authority,grant,_=fixture(tmp_path)
    payload={key:value for key,value in grant.items() if key!='signature'}
    with pytest.raises((PermissionError,ValueError)):
        with authority.colab_qualification_signing_preflight(request,payload=payload,job=job):
            Path(request['workflow_ref']['path']).write_text('drifted while signing')


@pytest.mark.parametrize('module',['colab_job.py','colab_transport.py','colab_remote_worker.py','colab_bundle_helpers.py','colab_workflow.py'])
@pytest.mark.parametrize('failure',['missing','changed'])
def test_new_transport_helpers_require_record_custody(tmp_path,module,failure):
    import base64
    import csv
    from devbench.analysis.rna_trial_authority import _CUSTODY_PATHS, verify_installed_distribution_custody
    relative='devbench/analysis/'+module
    assert relative in _CUSTODY_PATHS
    metadata=tmp_path/'synthetic-0.dist-info'
    metadata.mkdir()
    (metadata/'METADATA').write_text('Name: synthetic\nVersion: 0\n')
    rows=[]
    for name in (*_CUSTODY_PATHS,'synthetic-0.dist-info/METADATA'):
        path=tmp_path/name
        path.parent.mkdir(parents=True,exist_ok=True)
        if not path.exists(): path.write_bytes(b'# metadata custody fixture; never executed\n')
        body=path.read_bytes()
        rows.append([name,'sha256='+base64.urlsafe_b64encode(hashlib.sha256(body).digest()).decode().rstrip('='),str(len(body))])
    rows.append(['synthetic-0.dist-info/RECORD','',''])
    def write_record():
        with (metadata/'RECORD').open('w',newline='') as handle:
            csv.writer(handle).writerows(rows)
    write_record()
    class Distribution:
        @property
        def files(self): return [Path(row[0]) for row in rows]
        def locate_file(self,name): return tmp_path/name
    distribution=Distribution()
    verify_installed_distribution_custody(distribution,runtime_root=tmp_path,required_paths=_CUSTODY_PATHS)
    if failure=='missing':
        rows[:]=[row for row in rows if row[0]!=relative]
        write_record()
    else:
        (tmp_path/relative).write_bytes(b'changed authoritative helper')
    with pytest.raises(PermissionError,match='required enforcement|RECORD|custody'):
        verify_installed_distribution_custody(distribution,runtime_root=tmp_path,required_paths=_CUSTODY_PATHS)


@pytest.mark.parametrize('change', ['opaque_workflow', 'candidate_digest', 'profile_job', 'model_permission', 'expectations'])
def test_even_signed_approval_requires_exact_typed_profile_and_frozen_candidate(tmp_path, change):
    """Approved opaque labels cannot bypass per-node and whole-candidate custody."""
    from dataclasses import replace
    request, job, authority, _, private = fixture(tmp_path)
    path = Path(request['workflow_ref']['path'])
    profile = json.loads(path.read_bytes())
    if change == 'opaque_workflow': profile = {'step_id': job['step_id']}
    elif change == 'candidate_digest': profile['candidate_sha256'] = 'f' * 64
    elif change == 'profile_job': profile['job_id'] = 'f' * 32
    elif change == 'model_permission': profile['allow_model_execution'] = True
    else:
        alternate = tmp_path / 'alternate-expectations.json'
        alternate.write_bytes(b'{"format":"unapproved"}')
        raw = alternate.read_bytes()
        profile['sources']['expectations'] = {'path':str(alternate), 'sha256':hashlib.sha256(raw).hexdigest(), 'size_bytes':len(raw)}
    raw = canonical_json(profile)
    path.write_bytes(raw)
    request['workflow_ref'] = {'path':str(path), 'sha256':hashlib.sha256(raw).hexdigest(), 'size_bytes':len(raw)}
    # This test deliberately refreshes request-level approval and signature;
    # the concrete typed-profile check must independently reject the subject.
    authority.colab_qualification_context = replace(authority.colab_qualification_context,
        approved_request_sha256=digest(request))
    payload = unsigned_colab_qualification_grant_subject(request, issuer_id='test', grant_id='invalid-profile', now=NOW)
    grant = {**payload, 'signature':private.sign(canonical_json(payload)).hex()}
    with pytest.raises((ValueError, PermissionError)):
        authority.consume_and_reserve_colab_qualification(request, grant,
            dispatch_id=job['job_id'], allocation=job['limits'], job=job)
    with sqlite3.connect(authority.path) as db:
        assert db.execute('select count(*) from grants').fetchone()[0] == 0
        assert db.execute('select count(*) from dispatches').fetchone()[0] == 0
