"""Independent-review regressions for evidence loss and bounded CAS copying."""
import hashlib,json
from pathlib import Path

import pytest

from devbench.runner import CodeActionRunner
from devbench.evidence import CodeActionEvidence
from devharness_core.provenance import ExecutionDAG,verify_execution
from tests.test_clinical_audit_regressions import task,LocalAdapter


def test_observation_write_failure_retains_the_original_adapter_failure(task,tmp_path,monkeypatch):
    original=Path.write_text
    def fail(path,*args,**kwargs):
        if path.name=='cli-observations.json':raise OSError('OBSERVATION_STORAGE_FAILURE')
        return original(path,*args,**kwargs)
    monkeypatch.setattr(Path,'write_text',fail)
    def operation(work):raise RuntimeError('ORIGINAL_ADAPTER_FAILURE')
    result=CodeActionRunner(task).run(LocalAdapter(operation),tmp_path/'result')
    metadata=result['metadata']
    assert not result['passed'] and not metadata['accepted_final']
    assert metadata['failure_type']=='audit_error'
    assert 'ORIGINAL_ADAPTER_FAILURE' in metadata['error']
    assert 'OBSERVATION_STORAGE_FAILURE' in metadata['audit_error']
    receipt=metadata['audit'];assert verify_execution(receipt['manifest_path'],receipt['root_hash'])['valid']
    manifest=json.loads(Path(receipt['manifest_path']).read_text())
    last=json.loads((Path(receipt['manifest_path']).parent/'nodes'/(manifest['node_ids'][-1]+'.json')).read_text())
    outcome=Path(receipt['manifest_path']).parent/'objects'/last['payload']['artifacts']['outcome.snapshot.json']['sha256']
    assert 'ORIGINAL_ADAPTER_FAILURE' in outcome.read_text() and 'OBSERVATION_STORAGE_FAILURE' in outcome.read_text()


def test_private_snapshot_growth_cannot_exceed_the_cas_capture_limit(tmp_path,monkeypatch):
    import devbench.evidence as evidence
    work=tmp_path/'work';work.mkdir();out=tmp_path/'output';out.mkdir()
    data=b'original';(work/'metadata.csv').write_bytes(data)
    original=evidence.copy_artifact
    def grow(source,destination,**kwargs):
        ref=original(source,destination,**kwargs)
        destination.write_bytes(data+b'x'*64)
        return ref
    monkeypatch.setattr(evidence,'INPUT_BYTES',16)
    monkeypatch.setattr(evidence,'copy_artifact',grow)
    ledger=CodeActionEvidence(out)
    with pytest.raises(ValueError):ledger.capture_inputs(work,{'metadata.csv':hashlib.sha256(data).hexdigest()},{},'0'*64)
    assert sum(p.stat().st_size for p in (out/'audit/objects').iterdir())<=16


@pytest.mark.parametrize('limit',[-1,True,1.5])
def test_cas_optional_limits_reject_invalid_values(tmp_path,limit):
    source=tmp_path/'data';source.write_bytes(b'1234')
    dag=ExecutionDAG(tmp_path/'audit')
    for keyword in ('max_bytes','max_entries'):
        with pytest.raises(ValueError):dag.add_artifact(source,**{keyword:limit})
    assert not list((tmp_path/'audit/objects').iterdir())


def test_cas_bounds_enforce_directory_total_and_keep_default_identity(tmp_path):
    source=tmp_path/'data';source.mkdir()
    (source/'a').write_bytes(b'1234');(source/'b').write_bytes(b'5678')
    bounded=ExecutionDAG(tmp_path/'bounded');plain=ExecutionDAG(tmp_path/'plain')
    with pytest.raises(ValueError):bounded.add_artifact(source,max_bytes=7,max_entries=2)
    with pytest.raises(ValueError):bounded.add_artifact(source,max_bytes=8,max_entries=1)
    assert not list((tmp_path/'bounded/objects').iterdir())
    assert bounded.add_artifact(source,max_bytes=8,max_entries=2)==plain.add_artifact(source)
    receipt=bounded.finalize();assert verify_execution(receipt['manifest_path'],receipt['root_hash'])['valid']


def test_retained_output_growth_cannot_overrun_its_receipted_size(tmp_path,monkeypatch):
    out=tmp_path/'out';out.mkdir();artifact=out/'artifact.txt';artifact.write_bytes(b'original')
    ledger=CodeActionEvidence(out);original=ledger.dag.add_artifact
    def grow(path,**kwargs):
        if path==artifact:path.write_bytes(b'original'+b'x'*64)
        return original(path,**kwargs)
    monkeypatch.setattr(ledger.dag,'add_artifact',grow)
    result={'passed':True,'grader_result':{'passed':True},'metadata':{}}
    with pytest.raises(ValueError):ledger.finish(result,{'artifact.txt':{'preserved':True,'sha256':hashlib.sha256(b'original').hexdigest(),'bytes':8,'path':str(artifact)}})
    assert sum(p.stat().st_size for p in (out/'audit/objects').iterdir())<=8


def test_captured_outcome_bytes_must_equal_the_computed_result(tmp_path,monkeypatch):
    out=tmp_path/'out';out.mkdir();ledger=CodeActionEvidence(out);original=ledger.dag.add_artifact
    def alter(path,**kwargs):
        if path.name=='outcome.snapshot.json':path.write_bytes(b' '*path.stat().st_size)
        return original(path,**kwargs)
    monkeypatch.setattr(ledger.dag,'add_artifact',alter)
    with pytest.raises(ValueError,match='outcome'):
        ledger.finish({'passed':True,'grader_result':{'passed':True},'metadata':{}},{})


@pytest.mark.parametrize('origin',['adapter','environment'])
def test_chained_persistence_failure_keeps_the_original_failure_category(task,tmp_path,monkeypatch,origin):
    import devbench.runner as runner
    from devbench.evidence import ExecutionDAG
    original=Path.write_text
    def fail_observation(path,*args,**kwargs):
        if path.name=='cli-observations.json':raise OSError('OBSERVATION_STORAGE_FAILURE')
        return original(path,*args,**kwargs)
    def fail_finalize(*args,**kwargs):raise OSError('FINALIZATION_STORAGE_FAILURE')
    def fail_environment():raise ValueError('ORIGINAL_ENVIRONMENT_FAILURE')
    def operation(work):raise RuntimeError('ORIGINAL_ADAPTER_FAILURE')
    monkeypatch.setattr(Path,'write_text',fail_observation)
    monkeypatch.setattr(ExecutionDAG,'finalize',fail_finalize)
    if origin=='environment':monkeypatch.setattr(runner,'_environment',fail_environment)
    result=CodeActionRunner(task).run(LocalAdapter(operation),tmp_path/'result')
    m=result['metadata'];assert not result['passed'] and not m['accepted_final']
    assert m['failure_type']=='audit_error' and 'audit' not in m
    assert m['execution_failure_type']==('agent_error' if origin=='adapter' else 'runtime_identity_error')
    assert ('ORIGINAL_ADAPTER_FAILURE' if origin=='adapter' else 'ORIGINAL_ENVIRONMENT_FAILURE') in m['error']
    assert any('FINALIZATION_STORAGE_FAILURE' in error for error in m['persistence_errors'])
    if origin=='adapter':assert any('OBSERVATION_STORAGE_FAILURE' in error for error in m['persistence_errors'])
