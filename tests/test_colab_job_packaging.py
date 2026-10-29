"""Packaging/hash/schema software checks only; no scientific module is imported."""
import ast
from datetime import datetime, timedelta, timezone
import hashlib
import importlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import zipfile

import pytest
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization
from devharness_core.provenance import canonical_json

ROOT=Path('/Users/vajraang/Documents/ChatGPT/bio-agent-harness')
ASSET_ROOT=Path('/Users/vajraang/Documents/ChatGPT/bio-agent-harness/reports/rna-colab-integration/2026-10-22-v1/candidate')


def load(name,path):
    if name.startswith(("devbench.", "devharness_core.")):
        return importlib.import_module(name)
    spec=importlib.util.spec_from_file_location(name,ROOT/path)
    module=importlib.util.module_from_spec(spec);sys.modules[name]=module;spec.loader.exec_module(module)
    return module

import devbench.analysis
load('devharness_core.methods','devharness_core/methods.py')
load('devbench.analysis.colab_execution_contract','devbench/analysis/colab_execution_contract.py')
pack=load('devbench.analysis.colab_job','devbench/analysis/colab_job.py')


def fixture(tmp_path):
    from devharness_core.methods import MethodContract
    from devbench.analysis.colab_execution_contract import POLICY
    from devbench.analysis.colab_qualification_contract import unsigned_colab_qualification_grant_subject
    def ref(name,body):
        path=tmp_path/name
        payload=body if type(body) is bytes else canonical_json(body)
        path.write_bytes(payload)
        return {'path':str(path),'sha256':hashlib.sha256(payload).hexdigest(),'size_bytes':len(payload)}
    source=ref('source.py',b'# inert packaging fixture: never executed\n')
    lock=ref('lock.json',{'runtime':'metadata-only-test'})
    input_ref=ref('input.json',{'kind':'opaque fixture bytes; never numerically evaluated'})
    bundle=pack.build_method_source_bundle(ASSET_ROOT,destination=tmp_path/'source.zip')
    bundle_ref={k:bundle[k] for k in ('path','sha256','size_bytes')}
    value={'format':'devharness.method/1','method_id':'colab.packaging.fixture','name':'Inert packaging metadata',
        'lifecycle':'custom','implementation_trust':'unreviewed','inputs':{'fixture':{'artifact_type':'json','required':True}},
        'parameters':{'type':'object','additionalProperties':False,'properties':{}},
        'outputs':{'numerical':{'artifact_type':'json','path':'/outputs/numerical.json','required':True},
                   'plot':{'artifact_type':'file','path':'/outputs/plot.svg','required':True}},
        'implementation':{'kind':'colab','source_sha256':source['sha256'],'entrypoint':['python3','/method/source.py']},
        'environment':{'runtime':'google_colab','lock_sha256':lock['sha256']},
        'execution':{'cpu':1,'memory_mb':512,'timeout_seconds':120,'pids':16,'max_output_bytes':2097152,'network':'allow'},
        'validation':{'comparator':'json','invariants':[],'counts_layer':'counts','qc_metrics':[]},
        'provenance':{'origin':'software-only packaging test'}}
    method=MethodContract.from_dict(value)
    verifier=ref('verifier-ref.py',(ASSET_ROOT/'qualification/verifier.py').read_bytes())
    expected=ref('expected-ref.json',(ASSET_ROOT/'qualification/expected_values.json').read_bytes())
    job={'format':'devbench.colab-method-job/1','job_id':'1'*32,'run_id':'2'*32,'step_id':'3'*32,
        'method_version_id':method.version_id,'method_contract':value,'source':{k:source[k] for k in ('sha256','size_bytes')},
        'environment_lock':{k:lock[k] for k in ('sha256','size_bytes')},
        'inputs':{'fixture':{**{k:input_ref[k] for k in ('sha256','size_bytes')},'artifact_type':'json'}},
        'parameters':{},'source_bundle_sha256':bundle['sha256'],'validator_sha256':verifier['sha256'],
        'scope':'synthetic_colab_qualification','limits':value['execution'],'actor':'operator','correlation':None,'policy':POLICY}
    request={'format':'devbench.colab-synthetic-qualification-request/1','scope':job['scope'],
        'qualification_id':'4'*32,'job_id':job['job_id'],'job_ref':ref('job.json',job),'job_sha256':hashlib.sha256(canonical_json(job)).hexdigest(),
        'user_authorization_ref':ref('authorization.json',{'scope':'one synthetic qualification'}),
        'roles':{'operator':'operator','reviewer':'reviewer'},'workflow_ref':ref('workflow.json',{'step_id':job['step_id']}),
        'bindings':{'method_version_id':method.version_id,'step_id':job['step_id'],'source_sha256':source['sha256'],
            'environment_lock_sha256':lock['sha256'],'validator_ref':verifier,'expected_values_ref':expected,
            'runtime_policy_ref':ref('policy.json',POLICY),'source_bundle_ref':bundle_ref},
        'allocation':{'limits':value['execution'],'max_runs':1},
        'restrictions':{'local_scientific_compute':False,'local_fallback':False,'model_generation':False,'airway':False},
        'expires_at':(datetime.now(timezone.utc)+timedelta(hours=1)).isoformat()}
    key=Ed25519PrivateKey.generate()  # Ephemeral metadata-test issuer, never enrolled or used remotely.
    issuer={'issuer_id':'software-test','public_key_hex':key.public_key().public_bytes(serialization.Encoding.Raw,serialization.PublicFormat.Raw).hex()}
    subject=unsigned_colab_qualification_grant_subject(request,issuer_id=issuer['issuer_id'],grant_id='one')
    grant={**subject,'signature':key.sign(canonical_json(subject)).hex()}
    return job,{'source':Path(source['path']),'environment_lock':Path(lock['path']),
        'inputs':{'fixture':Path(input_ref['path'])},'request':request,'grant':grant,'issuer':issuer}


def test_source_bundle_minimal_deterministic_and_no_scientific_import():
    first=pack.build_method_source_bundle(ASSET_ROOT);second=pack.build_method_source_bundle(ASSET_ROOT)
    assert first['payload']==second['payload']
    with zipfile.ZipFile(io.BytesIO(first['payload'])) as archive:
        assert set(archive.namelist())==set(pack.SOURCE_MEMBERS)
    assert not any('qualification.method' in name or 'qualification.verifier' in name for name in sys.modules)


def test_export_frozen_archive_and_thin_notebook_only(tmp_path):
    job,args=fixture(tmp_path)
    receipt=pack.export_method_job(job,tmp_path/'export',**args)
    with zipfile.ZipFile(receipt['payload']['path']) as archive:
        assert set(archive.namelist())==set(pack.PAYLOAD_MEMBERS)
        assert json.loads(archive.read('job.json'))==job
        assert not any(name.startswith(('tests/','benchmarks/','datasets/','credentials/')) for name in archive.namelist())
    notebook=json.loads(Path(receipt['notebook']['path']).read_bytes())
    assert notebook['metadata']['devbench_colab_job']['accepted'] is False
    source=''.join(''.join(cell['source']) for cell in notebook['cells'])
    assert 'extract_source_bundle' in source and "context['remote_session_id']" in source
    assert 'colab_run.py' not in source and '!codex' not in source
    for cell in notebook['cells']:
        if cell['cell_type']=='code':ast.parse(''.join(cell['source']))
    execution=Path(receipt['execution_file']['path']).read_text()
    assert execution==''.join(notebook['cells'][1]['source'])
    assert "Path('/content/colab-source.zip')" in execution
    assert "Path('/content/colab-result.zip').open('xb')" in execution
    # Existing infrastructure's bounded functions are reused without alteration;
    # only its broad benchmark allowlist is replaced by this job's exact set.
    from devbench.analysis import colab_bundle_helpers
    shared=ast.parse(Path(colab_bundle_helpers.__file__).read_text())
    generated=ast.parse(execution)
    for name in ('_read_source_file','extract_source_bundle'):
        old=next(node for node in shared.body if isinstance(node,ast.FunctionDef) and node.name==name)
        new=next(node for node in generated.body if isinstance(node,ast.FunctionDef) and node.name==name)
        assert ast.dump(old,include_attributes=False)==ast.dump(new,include_attributes=False)
    with pytest.raises(FileExistsError):pack.export_method_job(job,tmp_path/'export',**args)


@pytest.mark.parametrize('change',['source','input','grant','issuer','job','validator'])
def test_package_rejects_changed_artifacts_and_authority(tmp_path,change):
    job,args=fixture(tmp_path)
    if change=='source':args['source'].write_bytes(b'changed')
    if change=='input':args['inputs']['fixture'].write_bytes(b'changed')
    if change=='grant':args['grant']['signature']='00'*64
    if change=='issuer':args['issuer']['issuer_id']='other'
    if change=='job':job['job_id']='5'*32
    if change=='validator':args['request']['bindings']['validator_ref']['sha256']='6'*64
    with pytest.raises((ValueError,PermissionError,InvalidSignature)):pack.export_method_job(job,tmp_path/'export',**args)
    assert not (tmp_path/'export').exists()


def test_bundle_refuses_symlink_and_existing_destination(tmp_path):
    path=tmp_path/'bundle.zip'
    pack.build_method_source_bundle(ASSET_ROOT,path)
    with pytest.raises(FileExistsError):pack.build_method_source_bundle(ASSET_ROOT,path)
    link=tmp_path/'link.zip';link.symlink_to(path)
    with pytest.raises(ValueError):pack.build_method_source_bundle(ASSET_ROOT,link)


def test_remote_runner_guards_local_host_before_scientific_work():
    worker=load('devbench.analysis.colab_remote_worker','devbench/analysis/colab_remote_worker.py')
    with pytest.raises(PermissionError):worker.remote_guard()
    source=(ROOT/'devbench/analysis/colab_remote_worker.py').read_text()
    assert "os.O_EXCL" in source and "'/content/devbench_colab_job_claims'" in source
    assert 'LocalDockerExecutor' not in source


def test_frozen_shared_helpers_have_exact_upstream_source_provenance():
    from devbench.analysis import colab_bundle_helpers
    # Provenance refers to the retained upstream epoch, not today's mutable builder.
    original=(ROOT/'reports/rna-execution-route-qualification/2026-10-18-v1/before/scripts/build_colab_notebook.py').read_bytes()
    assert hashlib.sha256(original).hexdigest()==colab_bundle_helpers.UPSTREAM_SHA256
    old=original.decode('utf-8')
    new=Path(colab_bundle_helpers.__file__).read_text()
    for name in ('allowed_bundle_path','_read_source_file','extract_source_bundle'):
        first=next(n for n in ast.parse(old).body if isinstance(n,ast.FunctionDef) and n.name==name)
        second=next(n for n in ast.parse(new).body if isinstance(n,ast.FunctionDef) and n.name==name)
        assert ast.dump(first,include_attributes=False)==ast.dump(second,include_attributes=False)
        assert ast.get_source_segment(old,first)==ast.get_source_segment(new,second)
    # Production packaging no longer imports mutable checkout scripts.
    product=ast.parse((ROOT/'devbench/analysis/colab_job.py').read_text())
    assert not any(isinstance(n,ast.ImportFrom) and (n.module or '').startswith('scripts') for n in ast.walk(product))
