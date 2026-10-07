"""Closed software authorization; no scientific or remote execution."""
from datetime import datetime, timedelta, timezone
from uuid import uuid4
import pytest

from devbench.study import rna_hosted_trace_contract as contract


def criteria():
    return dict(format='devbench.rna-hosted-criteria/1', sample_matrix=[dict(fixture_id='synthetic-0',stage='pre_fit_methods',allocation=0,repeat=1)],api_p95_upper_seconds=5,visibility_deadline_seconds=30,poll_interval_seconds=.5,dashboard_window_seconds=30,max_extra_model_calls=0,max_extra_fits=0,max_extra_retrievals=0,max_source_writes=0,max_privacy_leaks=0,required_topology_fraction=1,censored_samples_fail=True,limits=dict(max_spans=64,max_bytes=262144,max_mutation_requests=1200,max_read_requests=2400,max_delivery_attempts=3,total_deadline_seconds=900))


def manifest(tmp_path):
    value=dict(format='devbench.rna-hosted-qualification/1',qualification_id=str(uuid4()),mode='fake',data_scope='synthetic',source_digest='a'*64,runtime_digest='b'*64,policy_digest='c'*64,criteria_digest='d'*64,fixture_manifest_digest='e'*64,output_path=str(tmp_path/'output'),endpoint=None,workspace_id=None,project_id=None,project_name=None,live_session_digest=None,expires_at=(datetime.now(timezone.utc)+timedelta(minutes=5)).isoformat(),allowed_operations=['create_run','update_run','read_run','query_runs'],authorize_upload=False,authorize_remote_read=False,authorization_record_digest=None,limits=criteria()['limits'])
    identities={k:value[k] for k in contract.IDENTITY_KEYS}
    return value,identities


def test_fake_closed_contract_is_fresh_and_secret_free(tmp_path):
    value,identities=manifest(tmp_path)
    validated=contract.validate_qualification(value,source_identity=identities,now=datetime.now(timezone.utc))
    assert validated==value and validated is not value
    assert not (tmp_path/'output').exists()


@pytest.mark.parametrize('key,bad',[('data_scope','clinical'),('data_scope','public_processed'),('output_path','relative'),('expires_at','2020-01-01T00:00:00Z'),('source_digest','f'*64),('runtime_digest','f'*64),('policy_digest','f'*64),('criteria_digest','f'*64),('fixture_manifest_digest','f'*64),('allowed_operations',['delete_project']),('authorize_upload',1),('secret','CANARY')])
def test_rejects_unreviewed_inputs(tmp_path,key,bad):
    value,identities=manifest(tmp_path);value[key]=bad
    with pytest.raises((ValueError,PermissionError)):
        contract.validate_qualification(value,source_identity=identities,now=datetime.now(timezone.utc))


def test_hosted_requires_upload_and_read_approval_and_target(tmp_path):
    value,identities=manifest(tmp_path);value['mode']='hosted'
    with pytest.raises(PermissionError):contract.validate_qualification(value,source_identity=identities,now=datetime.now(timezone.utc))
    value.update(authorize_upload=True,authorize_remote_read=True,endpoint='https://api.smith.langchain.com',workspace_id=str(uuid4()),project_id=str(uuid4()),project_name='pilot',live_session_digest='1'*64,authorization_record_digest='2'*64)
    identities.update(live_session_digest='1'*64,authorization_record_digest='2'*64)
    assert contract.validate_qualification(value,source_identity=identities,now=datetime.now(timezone.utc))==value
    value['project_id']=None
    with pytest.raises(ValueError):contract.validate_qualification(value,source_identity=identities,now=datetime.now(timezone.utc))


def test_output_symlink_overlap_and_existing_denied(tmp_path):
    value,identities=manifest(tmp_path)
    (tmp_path/'alias').symlink_to(tmp_path,target_is_directory=True)
    value['output_path']=str(tmp_path/'alias'/'new')
    with pytest.raises(ValueError):contract.validate_qualification(value,source_identity=identities,now=datetime.now(timezone.utc))
    value['output_path']=str(tmp_path/'source'/'new');identities['protected_paths']=[str(tmp_path/'source')]
    with pytest.raises(ValueError):contract.validate_qualification(value,source_identity=identities,now=datetime.now(timezone.utc))
    identities.pop('protected_paths');value['output_path']=str(tmp_path)
    with pytest.raises(ValueError):contract.validate_qualification(value,source_identity=identities,now=datetime.now(timezone.utc))


def test_criteria_rejects_loosened_invariance_and_overlimit_campaign():
    value=criteria();assert contract.validate_criteria(value)==value
    for key,bad in [('max_extra_fits',1),('censored_samples_fail',False),('required_topology_fraction',.9),('api_p95_upper_seconds',float('nan')),('extra','CANARY')]:
        with pytest.raises(ValueError):contract.validate_criteria({**value,key:bad})
    value['limits']['max_read_requests']=2401
    with pytest.raises(ValueError):contract.validate_criteria(value)


def test_result_preserves_independent_statuses_and_never_overwrites(tmp_path):
    states={key:dict(status='unverified',evidence_refs=[],limitations=['Not exercised']) for key in contract.RESULT_DIMENSIONS}
    states['software']=dict(status='passed',evidence_refs=['fake.json'],limitations=[])
    output=tmp_path/'attempt';output.mkdir()
    path=contract.write_result(output,status=states,evidence={'mode':'fake'})
    assert path.is_file()
    with pytest.raises(FileExistsError):contract.write_result(output,status=states,evidence={})
    with pytest.raises(ValueError):contract.write_result(tmp_path/'bad',status={**states,'native_budget':dict(status='passed',evidence_refs=[],limitations=[])},evidence={})
