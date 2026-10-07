"""Closed, default-off synthetic hosted qualification contracts.

These integrity bindings grant no scientific execution or authenticated approval.
No credentials or workflow executors are imported here.
"""
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import re
from .contracts import canonical_json_bytes
from .langsmith_export import ENDPOINTS, _time
from .langsmith_outbox import _uuid, _digest, digest

QUALIFICATION_KEYS = frozenset({'format','qualification_id','mode','data_scope','source_digest','runtime_digest','policy_digest','criteria_digest','fixture_manifest_digest','output_path','endpoint','workspace_id','project_id','project_name','live_session_digest','expires_at','allowed_operations','authorize_upload','authorize_remote_read','authorization_record_digest','limits'})
LIMIT_KEYS = frozenset({'max_spans','max_bytes','max_mutation_requests','max_read_requests','max_delivery_attempts','total_deadline_seconds'})
CRITERIA_KEYS = frozenset({'format','sample_matrix','api_p95_upper_seconds','visibility_deadline_seconds','poll_interval_seconds','dashboard_window_seconds','max_extra_model_calls','max_extra_fits','max_extra_retrievals','max_source_writes','max_privacy_leaks','required_topology_fraction','censored_samples_fail','limits'})
SAMPLE_KEYS = frozenset({'fixture_id','stage','allocation','repeat'})
RECONCILIATION_KEYS = frozenset({'format','qualification_id','session_digest','endpoint','workspace_id','project_id','operation_id','span_id','revision','expected_payload_digest','response_class','observed_content_digest','observation_interval','verification_result','reviewer_approval_digest'})
INTERVAL_KEYS = frozenset({'clock_id','last_nonmatching_monotonic_ns','first_matching_monotonic_ns'})
REMOTE_PROOF_KEYS = frozenset({'operation_id','revision','semantic_payload_digest'})
IDENTITY_KEYS = frozenset({'source_digest','runtime_digest','policy_digest','criteria_digest','fixture_manifest_digest','live_session_digest','authorization_record_digest'})
RESULT_DIMENSIONS = frozenset({'software','http_acceptance','remote_content','api_visibility','dashboard','native_budget','native_isolation','scientific_review'})
OPERATIONS = frozenset({'create_run','update_run','read_run','query_runs'})
LIMIT_CAPS = {'max_spans':2000,'max_bytes':8_000_000,'max_mutation_requests':1200,'max_read_requests':2400,'max_delivery_attempts':3,'total_deadline_seconds':900}


def _closed(value, keys, label):
    if not isinstance(value,dict) or set(value)!=keys:
        raise ValueError('Closed '+label+' required')


def _copy(value):
    return json.loads(canonical_json_bytes(value))


def _number(value, upper, *, positive=False):
    if type(value) not in (int,float) or not math.isfinite(value) or value<0 or value>upper or (positive and value==0):
        raise ValueError('Invalid finite bound')


def validate_limits(value):
    _closed(value,LIMIT_KEYS,'qualification limits')
    for key,cap in LIMIT_CAPS.items():
        if key=='total_deadline_seconds':_number(value[key],cap,positive=True)
        elif type(value[key]) is not int or not 1<=value[key]<=cap:raise ValueError('Qualification limit exceeded')
    return _copy(value)


def absolute_path(value):
    path=Path(value)
    if not path.is_absolute() or '..' in path.parts:
        raise ValueError('Absolute normalized path required')
    for component in (path,*path.parents):
        if component.is_symlink():raise ValueError('Symlink path prohibited')
    return path


def validate_qualification(value: dict, *, source_identity: dict, now: datetime) -> dict:
    _closed(value,QUALIFICATION_KEYS,'qualification manifest')
    if value['format']!='devbench.rna-hosted-qualification/1' or value['mode'] not in {'fake','hosted'} or value['data_scope']!='synthetic':
        raise ValueError('Synthetic qualification format required')
    _uuid(value['qualification_id'])
    if not isinstance(now,datetime) or now.tzinfo is None or now.utcoffset() is None:raise ValueError('Aware comparison clock required')
    expires=_time(value['expires_at'])
    if expires is None or expires<=now or (expires-now).total_seconds()>1800:raise ValueError('Expired or overlong authorization')
    for key in ('authorize_upload','authorize_remote_read'):
        if type(value[key]) is not bool:raise ValueError('Explicit boolean authorization required')
    operations=value['allowed_operations']
    if not isinstance(operations,list) or not operations or not all(isinstance(x,str) for x in operations) or len(set(operations))!=len(operations) or not set(operations)<=OPERATIONS:raise ValueError('Unapproved operation')
    hosted=value['mode']=='hosted'
    if hosted and ((set(operations)&{'create_run','update_run'} and not value['authorize_upload']) or (set(operations)&{'read_run','query_runs'} and not value['authorize_remote_read'])):
        raise PermissionError('Explicit separate hosted upload/read authorization required')
    if not isinstance(source_identity,dict):raise ValueError('Source identity required')
    for key in IDENTITY_KEYS:
        actual=value[key]
        if actual is None and not hosted and key in {'live_session_digest','authorization_record_digest'}:pass
        else:_digest(actual)
        if key not in source_identity or source_identity[key]!=actual:raise ValueError('Source/runtime/criteria/approval identity mismatch')
    for key in ('workspace_id','project_id'):
        if value[key] is None and not hosted:pass
        else:_uuid(value[key])
    if value['endpoint'] is None and not hosted:pass
    elif value['endpoint'] not in ENDPOINTS:raise ValueError('Unapproved endpoint')
    name=value['project_name']
    if name is None and not hosted:pass
    elif not isinstance(name,str) or re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,99}',name) is None:raise ValueError('Bounded project name required')
    validate_limits(value['limits'])
    output=absolute_path(value['output_path'])
    if output.exists():raise ValueError('Fresh qualification output required')
    protected=source_identity.get('protected_paths',[])
    if not isinstance(protected,list):raise ValueError('Protected paths must be a list')
    for item in protected:
        source=absolute_path(item)
        if source.is_relative_to(output) or output.is_relative_to(source):raise ValueError('Output overlaps protected source')
    if len(canonical_json_bytes(value))>8192:raise ValueError('Qualification manifest too large')
    return _copy(value)


def validate_criteria(value: dict) -> dict:
    _closed(value,CRITERIA_KEYS,'qualification criteria')
    if value['format']!='devbench.rna-hosted-criteria/1':raise ValueError('Unsupported criteria format')
    validate_limits(value['limits'])
    matrix=value['sample_matrix']
    if not isinstance(matrix,list) or not 1<=len(matrix)<=30:raise ValueError('Bounded explicit sample matrix required')
    seen=set()
    for sample in matrix:
        _closed(sample,SAMPLE_KEYS,'sample selection')
        if not isinstance(sample['fixture_id'],str) or re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,99}',sample['fixture_id']) is None:raise ValueError('Bounded fixture identity required')
        if sample['stage'] not in {'pre_fit_methods','post_fit_explanation'} or type(sample['allocation']) is not int or sample['allocation'] not in (0,1,2) or type(sample['repeat']) is not int or not 1<=sample['repeat']<=5:raise ValueError('Unsupported sample selection')
        identity=canonical_json_bytes(sample)
        if identity in seen:raise ValueError('Duplicate sample selection')
        seen.add(identity)
    for key,cap in [('api_p95_upper_seconds',5),('visibility_deadline_seconds',30),('poll_interval_seconds',30),('dashboard_window_seconds',30)]:_number(value[key],cap,positive=True)
    if value['poll_interval_seconds']>value['visibility_deadline_seconds']:raise ValueError('Poll interval exceeds sample deadline')
    for key in ('max_extra_model_calls','max_extra_fits','max_extra_retrievals','max_source_writes','max_privacy_leaks'):
        if type(value[key]) is not int or value[key]!=0:raise ValueError('Zero-added-execution/privacy invariant required')
    if type(value['required_topology_fraction']) not in (int,float) or value['required_topology_fraction']!=1 or value['censored_samples_fail'] is not True:raise ValueError('All topology and censored failures required')
    return _copy(value)


def validate_reconciliation(value):
    _closed(value,RECONCILIATION_KEYS,'remote reconciliation receipt')
    if value['format']!='devbench.rna-remote-reconciliation/1':raise ValueError('Unsupported receipt')
    for key in ('qualification_id','operation_id','span_id'):_uuid(value[key])
    for key in ('session_digest','expected_payload_digest'):_digest(value[key])
    for key in ('observed_content_digest','reviewer_approval_digest'):
        if value[key] is not None:_digest(value[key])
    for key in ('workspace_id','project_id'):
        if value[key] is not None:_uuid(value[key])
    if value['endpoint'] is not None and value['endpoint'] not in ENDPOINTS:raise ValueError('Receipt target denied')
    if type(value['revision']) is not int or value['revision']<0:raise ValueError('Invalid revision')
    if value['verification_result'] not in {'exact_revision_match','state_match_only','mismatch','not_visible','blocked'}:raise ValueError('Unsupported verification result')
    if value['response_class'] not in {'matched','state_only','mismatch','not_visible','auth_denied','conflict','rate_limited','server_error','timeout','oversized','redirect','invalid_response','budget_exhausted','unsupported_schema'}:raise ValueError('Unsupported response class')
    interval=value['observation_interval'];_closed(interval,INTERVAL_KEYS,'observer interval');_uuid(interval['clock_id'])
    previous=interval['last_nonmatching_monotonic_ns'];first=interval['first_matching_monotonic_ns']
    if type(previous) is not int or previous<0 or (first is not None and (type(first) is not int or first<previous)):raise ValueError('Invalid monotonic interval')
    if value['verification_result']=='exact_revision_match' and (first is None or value['observed_content_digest'] is None):raise ValueError('Exact content needs retained observation')
    return _copy(value)


def write_result(output: Path, *, status: dict, evidence: dict) -> Path:
    _closed(status,RESULT_DIMENSIONS,'independent result statuses')
    for key,item in status.items():
        _closed(item,{'status','evidence_refs','limitations'},'dimension result')
        if item['status'] not in {'passed','failed','blocked','unverified'} or not all(isinstance(v,list) and all(isinstance(x,str) and len(x)<=1024 for x in v) for v in (item['evidence_refs'],item['limitations'])):raise ValueError('Invalid dimension result')
        if item['status']=='passed' and not item['evidence_refs']:raise ValueError('Pass requires actual evidence')
        if key in {'native_budget','native_isolation','scientific_review'} and item['status']=='passed':raise ValueError('Hosted results cannot promote independent native/scientific claims')
    if not isinstance(evidence,dict):raise ValueError('Evidence object required')
    raw=canonical_json_bytes({'format':'devbench.rna-hosted-result/1','status':status,'evidence':evidence})
    if len(raw)>2_000_000:raise ValueError('Bounded result required')
    directory=absolute_path(output);directory.mkdir(parents=True,exist_ok=True)
    target=directory/'result.json'
    fd=os.open(target,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'wb') as stream:stream.write(raw);stream.flush();os.fsync(stream.fileno())
    return target
