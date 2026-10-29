"""Fixed credential-free two-guest production-relative-lease control, HOLD."""
from __future__ import annotations
import hashlib,importlib.util,json,os,re,stat,sys,time
from pathlib import Path
W=Path('/Users/vajraang/Documents/ChatGPT/bio-agent-harness')
ROOT=W/'reports/rna-native-gate1/2026-10-21-v2/provider/clock-lease-control/v1'
BASE=W/'scripts/run_gate1_personal_device_auth_v2.py'
BASE_SHA='c358a916324090e6f836f0de72a59f407bc03d3038956e9f2305b87341cb908b'
ROLES=('slow-negative','positive')
NAMES={r:'devharness-clock-lease-v1-'+r for r in ROLES}
NS=1000000000
FALSE={'live_generation_allowed':False,'biological_execution_allowed':False,'native_admission_qualified':False,
    'authentication_inputs_used':False,'official_CLI_invoked':False,'OS_clock_modified':False}
COMMON={'guest.py','lease_subset.py','production_client.py','sources.json','session.json'}
base=None


def load(path,sha,name):
    if path.resolve()!=path or path.is_symlink()or not path.is_file()or path.stat().st_size>100000 or hashlib.sha256(path.read_bytes()).hexdigest()!=sha:
        raise PermissionError('exact_public_control_helper_required')
    spec=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m


def command(role):
    if role not in ROLES:raise PermissionError('fixed_clock_control_role_required')
    return ['-i','PATH=/usr/bin:/bin','HOME=/tmp','LANG=C.UTF-8','/usr/bin/timeout','--signal=KILL','45s',
        '/usr/bin/python3','-I','-B','/input/guest.py',role]


def arguments(role):
    return ['create','--pull=never','--platform','linux/amd64','--name',NAMES[role],'--network','none','--read-only',
        '--cap-drop','ALL','--security-opt','no-new-privileges=true','--memory','134217728','--memory-swap','134217728',
        '--cpus','1','--pids-limit','16','--user','501:20','--ipc','private','--log-driver','none',
        '--tmpfs','/tmp:rw,nosuid,nodev,noexec,size=16777216,mode=1777',
        '--mount',f'type=bind,src={ROOT / role},dst=/input,readonly','--entrypoint','/usr/bin/env',base.IMAGE,*command(role)]


def verify(record,role,identity):
    c,h=record.get('Config',{}),record.get('HostConfig',{})
    expected={'NetworkMode':'none','ReadonlyRootfs':True,'Privileged':False,'Memory':134217728,'MemorySwap':134217728,
        'NanoCpus':1000000000,'PidsLimit':16,'PidMode':'','IpcMode':'private','PublishAllPorts':False}
    valid=(record.get('Id')==identity and record.get('Name')=='/'+NAMES[role]and record.get('Image')==base.IMAGE
        and c.get('User')=='501:20'and c.get('Entrypoint')==['/usr/bin/env']and c.get('Cmd')==command(role)
        and type(c.get('Env'))is list and len(c['Env'])==len(base.IMAGE_ENV)and set(c['Env'])==set(base.IMAGE_ENV)
        and all(type(h.get(k))is type(v)and h.get(k)==v for k,v in expected.items())
        and h.get('CapDrop')==['ALL']and h.get('SecurityOpt')==['no-new-privileges=true']
        and h.get('LogConfig')=={'Type':'none','Config':{}}and h.get('Tmpfs')=={'/tmp':'rw,nosuid,nodev,noexec,size=16777216,mode=1777'}
        and all(not h.get(k)for k in ['CapAdd','Binds','Devices','DeviceRequests','PortBindings','VolumesFrom','ExtraHosts'])
        and set(record.get('NetworkSettings',{}).get('Networks',{}))=={'none'})
    mounts=record.get('Mounts',[])
    valid=valid and len(mounts)==1 and mounts[0].get('Source')==str(ROOT/role)and mounts[0].get('Destination')=='/input'and mounts[0].get('Type')=='bind'and mounts[0].get('RW')is False
    if not valid:raise PermissionError('actual_clock_control_profile_mismatch')


def publish(role,record):
    pending=ROOT/role/'lease.pending.json';target=ROOT/role/'lease.json'
    with os.fdopen(os.open(pending,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o444),'wb')as f:
        f.write((json.dumps(record,sort_keys=True,separators=(',',':'))+'\n').encode());f.flush();os.fsync(f.fileno())
    try:os.link(pending,target,follow_symlinks=False)
    finally:pending.unlink()
    return hashlib.sha256(target.read_bytes()).hexdigest()


def relative_event(kind,cutoff,**values):return {'event':kind,'host_relative_to_cutoff_ns':time.monotonic_ns()-cutoff,**values}


def control_passed(result):
    return (result.get('positive_control_passed')is True and result.get('negative_host_containment_passed')is True
        and result.get('cutoff_stop_verified')is True and result.get('absence_verified')is True
        and result.get('distinct_guest_clock_identity_observed')is True and result.get('external_sigkill_exit_observed')is True
        and result.get('cleanup_uncertain')is False and result.get('mutation_identity_uncertain')is False
        and result.get('source_custody_unchanged')is True and not result.get('error_category'))


def main():
    global base
    if len(sys.argv)!=1:raise PermissionError('fixed_no_argument_clock_control_required')
    base=load(BASE,BASE_SHA,'fixed_clock_enclosure_helpers');base.ROOT=ROOT
    release=json.loads((ROOT/'release.json').read_text())
    if release.get('accepted')is not True or release.get('scope')!='credential-free-production-cross-clock-lease-control':
        raise PermissionError('independent_clock_control_release_required')
    if any((ROOT/p).exists()or(ROOT/p).is_symlink()for p in ['attempt.json','result.json']):raise FileExistsError('fresh_clock_control_required')
    for path,sha in release['prerequisite_sha256'].items():
        if base.digest(Path(path))!=sha:raise PermissionError('clock_prerequisite_changed')
    lease_hashes={}
    def custody():
        for directory in [ROOT,ROOT/'common',ROOT/'docker-client',*(ROOT/r for r in ROLES)]:
            info=directory.lstat()
            if directory.resolve()!=directory or not stat.S_ISDIR(info.st_mode)or info.st_uid!=501 or stat.S_IMODE(info.st_mode)!=0o700:
                raise PermissionError('ordinary_owned_clock_fixture_directory_required')
        for path,sha in release['source_sha256'].items():
            if base.digest(Path(path))!=sha:raise PermissionError('clock_source_changed')
        for role in ROLES:
            if {p.name for p in(ROOT/role).iterdir()}!=COMMON|({'lease.json'}if role in lease_hashes else set()):raise PermissionError('exact_public_clock_fixture_set_required')
            if role in lease_hashes and base.digest(ROOT/role/'lease.json')!=lease_hashes[role]:raise PermissionError('immutable_lease_changed')
        config=ROOT/'docker-client/config.json'
        if {p.name for p in config.parent.iterdir()}!={'config.json'}or config.is_symlink()or config.read_bytes()!=b'{"auths":{},"proxies":{}}\n':raise PermissionError('closed_owned_clock_Docker_config_required')
    custody();manifest=json.loads((ROOT/'common/sources.json').read_text())
    if manifest.get('production_frozen')is not True:raise PermissionError('frozen_production_helpers_required')
    subset=load(ROOT/'common/lease_subset.py',manifest['subset_sha256'],'fixed_clock_AST_subset')
    host=subset.extracted(ROOT/'common/production_host.py','host',manifest['host']['sha256'],manifest['host']['ast_sha256'])
    client=subset.extracted(ROOT/'common/production_client.py','client',manifest['client']['sha256'],manifest['client']['ast_sha256'])
    cleanup=load(W/'scripts/gate1_attachment_cleanup.py','09f0f2f1db54a8cc9e31f6326a524f476366bca502ea2254bf60f5eed0564db1','fixed_clock_attachment_cleanup');base._CLOSE_ATTACHMENT=cleanup.close_attachment
    prefix=[base.DOCKER,'--config',str(ROOT/'docker-client'),'--host',base.SOCKET];environment=base.closed_environment(ROOT/'docker-client')
    normal_end=time.monotonic()+90;ids={};attachments={};cutoff=None;pending={};sessions={};requests={};states={};software_results=set()
    result={'format':'devbench.production-cross-clock-lease-control/1',**FALSE,'calls':[],'events':[],'guest_states':{},
        'allocation_source':'operator-selected synthetic software control; not an authentication or study grant',
        'host_lease_seconds':30,'guest_hard_seconds':45,'normal_host_seconds':90,'mutation_identity_uncertain':False,'cleanup_uncertain':False,'cutoff_stop_verified':False}
    def call(args,clean=False,missing=False):
        if not clean:custody()
        else:
            if base.digest(Path(base.DOCKER))!=release['source_sha256'][base.DOCKER]:raise PermissionError('pinned_cleanup_Docker_changed')
            config=ROOT/'docker-client/config.json'
            if config.is_symlink()or config.read_bytes()!=b'{"auths":{},"proxies":{}}\n':raise PermissionError('closed_cleanup_Docker_config_changed')
        start=time.monotonic_ns()
        limit=time.monotonic()+12 if clean else min(normal_end,time.monotonic()+12,*([cutoff/NS]if cutoff is not None else[]))
        raw=base._run_process(prefix+args,cwd=ROOT,environment=environment,prompt=b'',deadline=limit,maximum=65536,authorize=lambda:None)
        result['calls'].append({'operation':args[0],'exit_code':raw['exit_code'],'error':raw['error'],'process_reaped':raw['process_reaped'],
            'stdout_bytes':len(raw['stdout']),'stderr_bytes':len(raw['stderr']),
            'started_relative_to_cutoff_ns':None if cutoff is None else start-cutoff,'duration_ns':time.monotonic_ns()-start})
        if missing:
            if raw['exit_code']!=1 or raw['error']is not None or not raw['process_reaped']or b'No such container'not in raw['stderr']or args[-1].encode()not in raw['stderr']:
                raise RuntimeError('owned_clock_guest_absence_unconfirmed')
        elif raw['exit_code']!=0 or raw['error']is not None or not raw['process_reaped']:raise RuntimeError('bounded_clock_transport_failed')
        return raw['stdout']
    def inspect(role,clean=False):
        v=json.loads(call(['container','inspect',ids[role]],clean))[0];verify(v,role,ids[role]);s=v.get('State',{})
        state={'running':s.get('Running')if type(s.get('Running'))is bool else None,'exit_code':s.get('ExitCode')if type(s.get('ExitCode'))is int else None,
            'pid':s.get('Pid')if type(s.get('Pid'))is int else None,'oom_killed':s.get('OOMKilled')if type(s.get('OOMKilled'))is bool else None,
            'error_empty':type(s.get('Error'))is str and s['Error']=='',
            'status':s.get('Status')if s.get('Status')in ['created','running','exited','dead']else'unclassified'}
        result['guest_states'][role]=state;return state
    def frame(role,value):
        if type(value)is not dict or type(value.get('event'))is not str:raise ValueError('fixed_control_frame_required')
        event=value['event'];now=time.monotonic_ns()
        if event=='auth_lease_request':
            if role in requests or role in software_results:raise PermissionError('single_pre_result_lease_request_required')
            host['validate_lease_request'](value,sessions[role]['attempt_nonce'])
            record=host['lease_record'](value,sessions[role]['attempt_nonce'],cutoff,now)
            requests[role]=record;pending[role]=(now+250000000,record)
            result['events'].append(relative_event('request_observed',cutoff,role=role));return
        if value.get('role')!=role:raise ValueError('fixed_control_role_frame_required')
        if event=='clock_observation':
            if set(value)!={'event','role','time_namespace','boot_sha256','offsets_sha256','monotonic_ns'}or role in states:raise ValueError('fixed_clock_observation_required')
            if type(value['monotonic_ns'])is not int or not 0<=value['monotonic_ns']<2**63:raise ValueError('bounded_public_clock_counter_required')
            for key in ['boot_sha256','offsets_sha256']:
                if value[key]is not None and(type(value[key])is not str or re.fullmatch('[0-9a-f]{64}',value[key])is None):raise ValueError('bounded_public_clock_digest_required')
            if value['time_namespace']is not None and(type(value['time_namespace'])is not str or re.fullmatch(r'time:\[\d+\]',value['time_namespace'])is None):raise ValueError('bounded_public_namespace_required')
            states[role]={'clock':value};result.setdefault('clock_observations',{})[role]=value;return
        if role not in requests or role not in lease_hashes or role not in states:raise ValueError('request_reply_before_control_receipt_required')
        if event=='lease_applied':
            keys={'event','role','request_origin_ns','request_emitted_ns','applied_ns','login_cutoff_ns','lease_end_ns','remaining_ns','delivery_consumed_ns','application_rate_denominator'}
            if set(value)!=keys or any(type(value[k])is not int or not 0<=value[k]<2**63 for k in keys-{'event','role'}):raise ValueError('exact_lease_application_receipt_required')
            record=requests[role];a=value['request_origin_ns'];b=value['request_emitted_ns'];c=value['applied_ns']
            login,end=client['client_lease_deadlines'](record,record['attempt_nonce'],record['request_nonce'],a,c)
            if not(a<=b<=c and value['remaining_ns']==record['remaining_ns']and value['login_cutoff_ns']==login and value['lease_end_ns']==end and value['delivery_consumed_ns']==c-a
                and value['application_rate_denominator']==(1 if role=='positive'else 2)):raise ValueError('actual_production_request_origin_or_math_failed')
            if role=='positive'and c-a<200000000:raise ValueError('delivery_delay_not_observed_as_consumed')
            states[role]['lease_applied']=value;result.setdefault('lease_applications',{})[role]=value;return
        if event=='software_result':
            if role!='slow-negative'or set(value)!={'event','role','software_completed','guest_expected_to_linger'}or value['software_completed']is not True or value['guest_expected_to_linger']is not True or role in software_results:
                raise ValueError('fixed_negative_software_result_required')
            software_results.add(role);result['events'].append(relative_event('negative_result_received_before_linger',cutoff));return
        if event=='slow_clock_still_active':
            if role!='slow-negative'or set(value)!={'event','role','remaining_application_ns'}or type(value['remaining_application_ns'])is not int or not 0<value['remaining_application_ns']<=300*NS:raise ValueError('bounded_owned_slow_clock_receipt_required')
            states[role]['slow_active']=True;result['events'].append(relative_event('negative_still_active',cutoff));return
        if event=='login_window_expired':
            if set(value)!={'event','role','application_elapsed_ns','real_elapsed_ns'}or any(type(value[k])is not int or not 0<=value[k]<2**63 for k in ['application_elapsed_ns','real_elapsed_ns']):raise ValueError('fixed_expiry_receipt_required')
            if role!='positive'or 'expiry'in states[role]or now>cutoff:raise ValueError('positive_expiry_missing_or_after_host_cutoff')
            states[role]['expiry']=value;result['events'].append(relative_event('positive_expiry_receipt',cutoff));return
        raise ValueError('unexpected_bounded_control_event')
    try:
        with os.fdopen(os.open(ROOT/'attempt.json',os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600),'w')as f:
            f.write(json.dumps({'format':'devbench.clock-control-attempt/1',**FALSE})+'\n');f.flush();os.fsync(f.fileno())
        for role in ROLES:
            result['mutation_identity_uncertain']=True;identity=call(arguments(role)).decode().strip()
            if re.fullmatch('[0-9a-f]{64}',identity)is None:raise RuntimeError('owned_clock_guest_identity_missing')
            ids[role]=identity;result['mutation_identity_uncertain']=False
            state=inspect(role)
            if state['status']!='created'or state['running']is not False:raise PermissionError('fresh_created_clock_guest_required')
            sessions[role]=json.loads((ROOT/role/'session.json').read_text())
        cutoff=time.monotonic_ns()+30*NS  # Before authority guest attach, not after readiness.
        result['events'].append(relative_event('host_cutoff_established_before_authority_attach',cutoff))
        for role in ROLES:
            custody();attachments[role]=base.Attachment(prefix+['start','--attach',ids[role]],environment,16384)
        previous=None
        while True:
            now=time.monotonic_ns();action=host['host_deadline_action'](now,cutoff)
            if action!=previous:result['events'].append(relative_event('host_state_transition',cutoff,action=action,negative_software_result_received='slow-negative'in software_results));previous=action
            if action=='stop-auth-authority':break
            if time.monotonic()>=normal_end:raise TimeoutError('normal_control_deadline_exhausted')
            for role in ROLES:
                for value in attachments[role].pump(.01):frame(role,value)
            now=time.monotonic_ns()
            for role,(ready,record)in list(pending.items()):
                if host['host_deadline_action'](time.monotonic_ns(),cutoff)=='stop-auth-authority':break
                if now>=ready:
                    custody();lease_hashes[role]=publish(role,record);del pending[role]
                    result['events'].append(relative_event('immutable_reply_published',cutoff,role=role))
        # Independent host cutoff after the synthetic final result; no blocking wait first.
        result['events'].append(relative_event('negative_stop_requested',cutoff))
        call(['kill','--signal','KILL',ids['slow-negative']],True)
        result['negative_exact_id_kill_ACK']=True
        result['events'].append(relative_event('negative_stop_call_returned',cutoff))
        result['negative_state_after_stop']=inspect('slow-negative',True)
        stopped=result['negative_state_after_stop']
        result['cutoff_stop_verified']=(stopped['running']is False and stopped['status']=='exited'and stopped['pid']==0 and stopped['oom_killed']is False and stopped['error_empty']is True)
        result['external_sigkill_exit_observed']=stopped['exit_code']==137
        result['events'].append(relative_event('negative_exit_observed',cutoff))
        result['positive_state_after_cutoff']=inspect('positive',True)
        result['positive_control_passed']=('expiry'in states.get('positive',{})and result['positive_state_after_cutoff']['running']is False and result['positive_state_after_cutoff']['exit_code']==0)
        result['negative_host_containment_passed']=('slow-negative'in software_results and states.get('slow-negative',{}).get('slow_active')is True and result['cutoff_stop_verified'])
    except BaseException as error:
        result['error_category']='Cancelled'if isinstance(error,(KeyboardInterrupt,SystemExit))else type(error).__name__
    finally:
        for role,identity in reversed(list(ids.items())):
            try:call(['container','rm','--force',identity],True);call(['container','inspect',identity],True,True)
            except Exception:result['cleanup_uncertain']=True
        result['absence_verified']=not result['cleanup_uncertain']
        if cutoff is not None:result['events'].append(relative_event('cleanup_absence_checks_completed',cutoff))
        result['attachment_exit_codes']={};result['attachment_close_receipts']={}
        for role,attachment in attachments.items():
            try:
                if not attachment.close():result['cleanup_uncertain']=True
            except Exception:result['cleanup_uncertain']=True
            result['attachment_exit_codes'][role]=attachment.process.returncode
            result['attachment_close_receipts'][role]=getattr(attachment,'_closure_receipt',{'closed_verified':False})
        try:custody();result['source_custody_unchanged']=True
        except Exception:result['source_custody_unchanged']=False
        result['host_cutoff_containment_uncertain']=not result['cutoff_stop_verified']
        observations=result.get('clock_observations',{})
        if set(observations)==set(ROLES):
            result['guest_clock_identity_comparison']={key:(None if any(observations[r][key]is None for r in ROLES)
                else observations[ROLES[0]][key]==observations[ROLES[1]][key])for key in ['time_namespace','boot_sha256','offsets_sha256']}
        comparisons=result.get('guest_clock_identity_comparison',{})
        complete=len(comparisons)==3 and all(type(v)is bool for v in comparisons.values())
        result['distinct_guest_clock_identity_observed']=any(v is False for v in comparisons.values())if complete else None
        if result['distinct_guest_clock_identity_observed']is not True:result['qualification_hold_reason']='clock_identity_equal_or_unknown'
        result['absolute_monotonic_counters_compared_across_domains']=False
        with os.fdopen(os.open(ROOT/'result.json',os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600),'w')as f:f.write(json.dumps(result,indent=2)+'\n')
    passed=control_passed(result)
    print(json.dumps({'credential_free_clock_lease_controls_passed':passed,**FALSE}),flush=True)
    return 0 if passed else 1


if __name__=='__main__':raise SystemExit(main())
