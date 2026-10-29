"""One fixed offline official account/read operation after accepted device login.

No credential bytes are read, copied, hashed, printed, indexed, or compared by
this supervisor. It leases metadata of the exact owned auth file only after
an independent accepted official-login/status proof. No thread or turn exists.
"""
from __future__ import annotations
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import sys
import time

W=Path('/Users/vajraang/Documents/ChatGPT/bio-agent-harness')
sys.path.insert(0,str(W))
from devbench.analysis.codex_signedin import _run_process
from devbench.analysis.codex_isolated_boundary import DOCKER,IMAGE,SOCKET,closed_environment
from devharness_controlled.native_runtime import IMAGE_ENV
ROOT=W/'reports/rna-native-gate1/2026-10-21-v2/provider/account-probe/actual-attempt-v1'
BASE=W/'scripts/run_gate1_personal_device_auth_v2.py'
BASE_SHA='c358a916324090e6f836f0de72a59f407bc03d3038956e9f2305b87341cb908b'
WORKER_SHA='c877a1f44cc9b00aafc5760b1e9697313b4d41ab9d0e63ab9a1462fe7f12e427'
CLIENT=ROOT.parents[2]/'client/package'
MANIFEST=CLIENT.parent/'package-manifest.json'
PRIVATE=W/'.devbench-codex-auth/epoch1'
AUTH=PRIVATE/'codex-home/auth.json'
DEVICE_RESULT=ROOT.parents[2]/'auth-preparation/login-attempt-v2/result.json'
DEVICE_REVIEW=ROOT.parents[2]/'reviews/personal-device-auth-postrun-v2.json'
NAME='devharness-gate1-account-read-v1'
FALSE={'live_generation_allowed':False,'biological_execution_allowed':False,'native_admission_qualified':False}
TMPFS={'/tmp':'rw,nosuid,nodev,noexec,size=16777216,mode=1777',
    '/probe-home':'rw,nosuid,nodev,noexec,size=16777216,mode=700,uid=501,gid=20'}


def load_base():
    if BASE.resolve()!=BASE or BASE.is_symlink()or hashlib.sha256(BASE.read_bytes()).hexdigest()!=BASE_SHA:
        raise PermissionError('fixed_account_enclosure_helper_changed')
    spec=importlib.util.spec_from_file_location('fixed_account_enclosure_helper',BASE)
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m


def authorize_auth(base):
    """Public proof only; call before any metadata of the credential path."""
    # A missing result/review stops here without touching the private epoch.
    proof_sha=base.digest(DEVICE_RESULT,65536);review_sha=base.digest(DEVICE_REVIEW,65536)
    proof=json.loads(DEVICE_RESULT.read_text());review=json.loads(DEVICE_REVIEW.read_text())
    frame=base.validate_login_result(proof.get('login_result'))
    if (review.get('accepted_official_device_auth_and_status')is not True
        or review.get('result_sha256')!=proof_sha
        or proof.get('action')!='device-login'or proof.get('official_login_completed')is not True
        or proof.get('owned_runtime_resources_removed')is not True or proof.get('source_custody_unchanged')is not True
        or proof.get('mutation_identity_uncertain')is not False or proof.get('error_category')is not None
        or proof.get('private_epoch_preserved_without_inspection')is not True
        or frame['official_login_completed']is not True
        or proof.get('attachment_exit_codes')!={'proxy':0,'client':0}):
        raise PermissionError('accepted_dedicated_official_device_login_and_status_required')
    return proof_sha,review_sha


def auth_metadata():
    """Known-path lstat only, no directory enumeration or credential content."""
    for path in [PRIVATE.parent,PRIVATE,PRIVATE/'codex-home']:
        info=path.lstat()
        if not stat.S_ISDIR(info.st_mode)or info.st_uid!=501 or stat.S_IMODE(info.st_mode)!=0o700:
            raise PermissionError('ordinary_owned_dedicated_auth_ancestor_required')
    info=AUTH.lstat()
    if not stat.S_ISREG(info.st_mode)or info.st_uid!=501 or stat.S_IMODE(info.st_mode)!=0o600:
        raise PermissionError('ordinary_private_owned_auth_file_required')
    # Metadata only, retained in RAM; no credential file read or hash.
    return tuple(getattr(info,key)for key in ['st_dev','st_ino','st_uid','st_gid','st_mode','st_size','st_mtime_ns','st_ctime_ns'])


def command():
    return ['-i','PATH=/usr/bin:/bin','HOME=/tmp','LANG=C.UTF-8','DEVBENCH_ACCOUNT_READ_ONLY=1',
        '/usr/bin/timeout','--signal=KILL','25s','/usr/bin/python3','-I','-B','/input/account_probe.py']


def mounts():return [(ROOT/'input/account_probe.py','/input/account_probe.py'),(CLIENT,'/client'),(AUTH,'/probe-home/auth.json')]


def arguments():
    args=['create','--pull=never','--platform','linux/amd64','--name',NAME,'--network','none','--read-only',
        '--cap-drop','ALL','--security-opt','no-new-privileges=true','--memory','1073741824','--memory-swap','1073741824',
        '--cpus','1','--pids-limit','64','--user','501:20','--ipc','private','--log-driver','none',
        '--dns','127.0.0.1','--dns-search','.','--dns-option','timeout:1','--dns-option','attempts:1']
    for target,options in TMPFS.items():args+=['--tmpfs',target+':'+options]
    for source,target in mounts():args+=['--mount',f'type=bind,src={source},dst={target},readonly']
    return args+['--entrypoint','/usr/bin/env',IMAGE,*command()]


def verify_container(record,identity):
    c,h=record.get('Config',{}),record.get('HostConfig',{})
    expected={'NetworkMode':'none','ReadonlyRootfs':True,'Privileged':False,'Memory':1073741824,'MemorySwap':1073741824,
        'NanoCpus':1000000000,'PidsLimit':64,'PidMode':'','IpcMode':'private','PublishAllPorts':False}
    valid=(record.get('Id')==identity and record.get('Name')=='/'+NAME and record.get('Image')==IMAGE
        and c.get('User')=='501:20'and c.get('Entrypoint')==['/usr/bin/env']and c.get('Cmd')==command()
        and type(c.get('Env'))is list and len(c['Env'])==len(IMAGE_ENV)and set(c['Env'])==set(IMAGE_ENV)
        and all(type(h.get(k))is type(v)and h.get(k)==v for k,v in expected.items())
        and h.get('CapDrop')==['ALL']and h.get('SecurityOpt')==['no-new-privileges=true']
        and h.get('Dns')==['127.0.0.1']and h.get('DnsSearch')==['.']and h.get('DnsOptions')==['timeout:1','attempts:1']
        and h.get('LogConfig')=={'Type':'none','Config':{}}and h.get('Tmpfs')==TMPFS
        and all(not h.get(k)for k in ['CapAdd','Binds','Devices','DeviceRequests','PortBindings','VolumesFrom','ExtraHosts'])
        and set(record.get('NetworkSettings',{}).get('Networks',{}))=={'none'})
    actual=record.get('Mounts',[]);valid=valid and len(actual)==len(mounts())
    for source,target in mounts():
        found=[m for m in actual if m.get('Destination')==target]
        valid=valid and len(found)==1 and found[0].get('Source')==str(source)and found[0].get('Type')=='bind'and found[0].get('RW')is False
    if not valid:raise PermissionError('actual_account_read_container_profile_mismatch')


def validate_result(value):
    false_fields={'refresh_token_requested','raw_rpc_or_stderr_retained','authentication_cache_read_by_worker',
        'authentication_cache_hashed_by_worker',*FALSE}
    zeros={'thread_requests','turn_requests','live_model_calls'}
    bools={'allowed_personal_plan_observed','accepted_account_plan_observation','initialize_succeeded','account_read_succeeded',
        'official_cli_launch_confirmed','process_reaped','process_group_cleanup_uncertain'}
    fields={'format','account_type','plan_type','requires_openai_auth','official_cli_invocations_attempted',
        'official_cli_exit_code','error_category','stream_bytes'}|false_fields|zeros|bools
    if type(value)is not dict or set(value)!=fields or value['format']!='devbench.official-account-plan-read/1':
        raise ValueError('exact_account_read_projection_required')
    if any(value[k]is not False for k in false_fields)or any(type(value[k])is not int or value[k]!=0 for k in zeros):
        raise ValueError('no_model_or_credential_content_operation_required')
    if any(type(value[k])is not bool for k in bools)or value['account_type']not in [None,'chatgpt']or value['plan_type']not in [None,'free','plus','pro','unqualified']:
        raise ValueError('fixed_account_projection_scalars_required')
    if value['requires_openai_auth']is not None and type(value['requires_openai_auth'])is not bool:raise ValueError('fixed_auth_required_boolean')
    if type(value['official_cli_invocations_attempted'])is not int or not 0<=value['official_cli_invocations_attempted']<=1:
        raise ValueError('one_official_account_probe_required')
    if type(value['stream_bytes'])is not int or not 0<=value['stream_bytes']<=102400:raise ValueError('account_read_stream_bound_required')
    if value['error_category']not in [None,'OSError','PermissionError','ValueError','TimeoutError','ProcessLookupError','JSONDecodeError','BrokenPipeError','FileNotFoundError']:
        raise ValueError('fixed_account_read_error_category_required')
    if value['official_cli_exit_code']is not None and(type(value['official_cli_exit_code'])is not int or not -255<=value['official_cli_exit_code']<=255):
        raise ValueError('bounded_account_probe_exit_code_required')
    if value['accepted_account_plan_observation']and not(value['account_type']=='chatgpt'and value['plan_type']in ['free','plus','pro']
        and value['requires_openai_auth']is True and all(value[k]is True for k in ['allowed_personal_plan_observed','initialize_succeeded',
            'account_read_succeeded','official_cli_launch_confirmed','process_reaped'])and value['process_group_cleanup_uncertain']is False
        and value['official_cli_invocations_attempted']==1 and value['official_cli_exit_code']==0 and value['error_category']is None):
        raise ValueError('consistent_official_personal_plan_observation_required')
    return value


def main():
    if len(sys.argv)!=1:raise PermissionError('fixed_no_argument_account_read_only_action_required')
    base=load_base();auth_proof=authorize_auth(base)  # Must precede any private lstat/Docker.
    release=json.loads((ROOT/'release.json').read_text())
    if release.get('accepted')is not True or release.get('scope')!='offline-official-account-read-only':raise PermissionError('exact_account_read_runtime_release_required')
    if any((ROOT/name).exists()or(ROOT/name).is_symlink()for name in ['attempt.json','result.json']):raise FileExistsError('fresh_account_read_attempt_required')
    for path,expected in release['prerequisite_sha256'].items():
        if base.digest(Path(path))!=expected:raise PermissionError('reviewed_account_read_prerequisite_changed')
    metadata=auth_metadata()
    def custody(check_auth=True):
        if check_auth and(authorize_auth(base)!=auth_proof or auth_metadata()!=metadata):raise PermissionError('dedicated_auth_metadata_lease_changed')
        for directory in [ROOT,ROOT/'input',ROOT/'docker-client',CLIENT]:
            if directory.resolve()!=directory or not directory.is_dir():raise PermissionError('ordinary_account_public_directory_required')
        for path,expected in release['source_sha256'].items():
            if base.digest(Path(path))!=expected:raise PermissionError('reviewed_account_read_source_changed')
        if {p.name for p in(ROOT/'input').iterdir()}!={'account_probe.py'}:raise PermissionError('exact_public_account_read_input_set_required')
        config=ROOT/'docker-client/config.json'
        if {p.name for p in config.parent.iterdir()}!={'config.json'}or config.is_symlink()or config.read_bytes()!=b'{"auths":{},"proxies":{}}\n':
            raise PermissionError('closed_owned_docker_config_required')
    custody();manifest=json.loads(MANIFEST.read_text())
    if {str(p.relative_to(CLIENT))for p in CLIENT.rglob('*')}!={x['name']for x in manifest['files']}:raise PermissionError('official_package_inventory_changed')
    for entry in manifest['files']:
        p=CLIENT/entry['name']
        if p.is_symlink()or(entry['type']=='file'and(p.stat().st_size!=entry['bytes']or base.digest(p)!=entry['sha256'])):
            raise PermissionError('official_package_bytes_changed')
    prefix=[DOCKER,'--config',str(ROOT/'docker-client'),'--host',SOCKET];environment=closed_environment(ROOT/'docker-client')
    result={'format':'devbench.offline-account-read-supervisor/1',**FALSE,'calls':[],'stage':'prepared',
        'credential_contents_read_or_hashed':False,'private_directory_enumerated':False,'auth_metadata_only_checked':True,
        'mutation_identity_uncertain':False,'account_observation_accepted':False}
    identity=None;deadline=time.monotonic()+60
    def call(args,maximum=65536,clean=False,missing=False):
        custody(check_auth=not clean);raw=_run_process(prefix+args,cwd=ROOT,environment=environment,prompt=b'',maximum=maximum,
            deadline=(time.monotonic()+12 if clean else min(deadline,time.monotonic()+(40 if args[0]=='start'else 12))),authorize=lambda:None)
        result['calls'].append({'operation':args[0],'exit_code':raw['exit_code'],'error':raw['error'],'process_reaped':raw['process_reaped'],
            'stdout_bytes':len(raw['stdout']),'stderr_bytes':len(raw['stderr'])})
        if missing:
            if raw['exit_code']!=1 or raw['error']is not None or not raw['process_reaped']or b'No such container'not in raw['stderr']or args[-1].encode()not in raw['stderr']:
                raise RuntimeError('owned_account_container_removal_not_confirmed')
        elif raw['error']is not None or not raw['process_reaped']or(raw['exit_code']!=0 and args[0]!='start'):
            raise RuntimeError('bounded_account_read_transport_failed')
        return raw
    def inspect():
        v=json.loads(call(['container','inspect',identity])['stdout'])[0];verify_container(v,identity)
        state=v.get('State',{});result['guest_state']={'running':state.get('Running')if type(state.get('Running'))is bool else None,
            'status':state.get('Status')if state.get('Status')in ['created','running','exited','dead']else'unclassified',
            'exit_code':state.get('ExitCode')if type(state.get('ExitCode'))is int else None}
        return state
    try:
        with os.fdopen(os.open(ROOT/'attempt.json',os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600),'w')as f:
            f.write(json.dumps({'format':'devbench.offline-account-read-attempt/1',**FALSE,'action':'initialize+account/read refreshToken=false'})+'\n');f.flush();os.fsync(f.fileno())
        result['stage']='create';result['mutation_identity_uncertain']=True;raw=call(arguments())['stdout'].decode().strip()
        if re.fullmatch('[0-9a-f]{64}',raw)is None:raise RuntimeError('created_account_container_id_missing')
        identity=raw;result['mutation_identity_uncertain']=False;state=inspect()
        if state.get('Status')!='created'or state.get('Running')is not False:raise PermissionError('fresh_created_account_container_required')
        result['prestart_profile_verified']=True;result['stage']='official_account_read'
        raw=call(['start','--attach',identity],4096)
        if raw['stderr']:raise ValueError('unexpected_account_wrapper_stderr')
        frame=validate_result(json.loads(raw['stdout']));result['account_read']=frame
        state=inspect()
        if state.get('Running')is not False or type(state.get('ExitCode'))is not int or state['ExitCode']!=raw['exit_code']:
            raise RuntimeError('account_read_guest_exit_mismatch')
        result['account_observation_accepted']=frame['accepted_account_plan_observation']is True and raw['exit_code']==0
    except BaseException as error:
        result['error_stage']=result['stage'];result['error_category']='Cancelled'if isinstance(error,(KeyboardInterrupt,SystemExit))else type(error).__name__
    finally:
        clean=True
        if identity is not None:
            try:call(['container','rm','--force',identity],clean=True);call(['container','inspect',identity],clean=True,missing=True)
            except Exception:clean=False
        result['owned_container_removed']=clean
        try:custody();result['source_and_auth_metadata_custody_unchanged']=True
        except Exception:result['source_and_auth_metadata_custody_unchanged']=False
        with os.fdopen(os.open(ROOT/'result.json',os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600),'w')as f:f.write(json.dumps(result,indent=2)+'\n')
    passed=(result['account_observation_accepted']and result['owned_container_removed']and result['source_and_auth_metadata_custody_unchanged']
        and not result.get('error_category')and not result['mutation_identity_uncertain'])
    print(json.dumps({'offline_account_observation_accepted':passed,**FALSE}),flush=True)
    return 0 if passed else 1


if __name__=='__main__':raise SystemExit(main())
