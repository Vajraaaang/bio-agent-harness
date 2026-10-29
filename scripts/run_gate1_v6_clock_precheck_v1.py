"""Source-only HOLD: credential-free V6 production clock comparator control.

No Docker action is available without a separately accepted exact-source release.
This controller never mounts private state or invokes the official CLI.
"""
from __future__ import annotations
import ast, hashlib, importlib.util, json, os, re, stat, sys, time
from pathlib import Path

W=Path('/Users/vajraang/Documents/ChatGPT/bio-agent-harness')
ROOT=W/'reports/rna-native-gate1/2026-10-21-v2/provider/v6-clock-precheck-preparation-v1'
BASE=W/'scripts/run_gate1_personal_device_auth_v2.py'
BASE_SHA='c358a916324090e6f836f0de72a59f407bc03d3038956e9f2305b87341cb908b'
PROFILE=W/'scripts/run_gate1_cross_clock_lease_control_v2.py'
PROFILE_SHA='bdcbbda8b35db9379f46752aef65cdacfdf423086f71ea417e9aaf93761510ab'
PRODUCTION=W/'scripts/gate1_device_auth_client_v6_clockfix_v1.py'
RELAY=W/'scripts/gate1_device_auth_proxy_v6_clockfix_v1.py'
ROLES=('proxy','client')
NAMES={r:'devharness-v6-clock-precheck-v1-'+r for r in ROLES}
FALSE={'live_generation_allowed':False,'biological_execution_allowed':False,'native_admission_qualified':False,
       'authentication_inputs_used':False,'official_CLI_invoked':False,'OS_clock_modified':False}
FUNCTIONS={'clock_domain','validate_clock_binding','validate_clock_domain','session_deadlines','setup_failure'}
CONSTANTS={'NS','CLOCK_DOMAIN_FIELDS','CLOCK_FIELDS'}


def digest(path):
    resolved=path.resolve()
    allowed=(resolved==Path('/Applications/Docker.app/Contents/Resources/bin/docker')or any(resolved.is_relative_to(W/name)for name in ['scripts','devbench','devharness_controlled','reports/rna-native-gate1/2026-10-21-v2']))
    if not allowed or path.name in ['auth.json','codex-login.log']or 'codex-home'in path.parts:
        raise PermissionError('credential_or_unapproved_source_digest_forbidden')
    if resolved!=path or path.is_symlink()or not path.is_file()or path.stat().st_size>300000000:
        raise PermissionError('ordinary_bounded_public_source_required')
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path,sha,name):
    if digest(path)!=sha:raise PermissionError('exact_public_control_helper_required')
    spec=importlib.util.spec_from_file_location(name,path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module


def extract_production(source,role='client'):
    """Extract exact production AST nodes without importing authentication code."""
    if role not in ROLES:raise PermissionError('fixed_AST_extraction_role_required')
    functions=FUNCTIONS if role=='client'else {'clock_domain'}
    constants=CONSTANTS if role=='client'else {'NS'}
    tree=ast.parse(source);nodes=[];found=set()
    for node in tree.body:
        if isinstance(node,ast.FunctionDef)and node.name in functions:
            nodes.append(node);found.add(node.name)
        elif isinstance(node,ast.Assign)and len(node.targets)==1 and isinstance(node.targets[0],ast.Name)and node.targets[0].id in constants:
            nodes.append(node);found.add(node.targets[0].id)
    if found!=functions|constants:raise ValueError('complete_exact_production_clock_subset_required')
    imports=ast.parse('import hashlib, os, re, time\nfrom pathlib import Path\n').body
    return ast.unparse(ast.fix_missing_locations(ast.Module(body=imports+nodes,type_ignores=[])))+'\n'


def validate_receipt(value,role,session=None):
    if role=='proxy':
        if type(value)is not dict or set(value)!={'event','role','clock_binding'}or value['event']!='clock_precheck_proxy'or value['role']!=role:
            raise ValueError('exact_proxy_clock_receipt_required')
    elif role=='client':
        keys={'event','role','proxy_clock_domain','client_clock_domain','positive_deadlines','positive_failure','negative_failure','negative_fixture_offset_sha256','OS_clock_modified','official_cli_invocations_attempted'}
        if type(value)is not dict or set(value)!=keys or value['event']!='clock_precheck_client'or value['role']!=role or value['OS_clock_modified']is not False or type(value['official_cli_invocations_attempted'])is not int or value['official_cli_invocations_attempted']!=0:
            raise ValueError('exact_client_clock_receipt_required')
        if value['positive_deadlines']is not None and(type(value['positive_deadlines'])is not list or len(value['positive_deadlines'])!=2 or any(type(x)is not int or not 0<=x<2**63 for x in value['positive_deadlines'])):raise ValueError('bounded_production_deadline_receipt_required')
        if type(value['negative_fixture_offset_sha256'])is not str or re.fullmatch('[0-9a-f]{64}',value['negative_fixture_offset_sha256'])is None:raise ValueError('bounded_synthetic_offset_digest_required')
    else:raise ValueError('fixed_control_role_required')
    return value


def control_passed(result):
    return all(result.get(k)is True for k in ['positive_control_passed','negative_control_passed','absence_verified','attachments_closed','source_custody_unchanged'])and all(result.get(k)is False for k in ['cleanup_uncertain','mutation_identity_uncertain'])and result.get('error_category')is None


def main():
    if len(sys.argv)!=1:raise PermissionError('fixed_no_argument_control_required')
    release=json.loads((ROOT/'release.json').read_text())
    if release.get('accepted')is not True or release.get('scope')!='credential-free-v6-minimal-clock-precheck':
        raise PermissionError('independent_clock_precheck_release_required')
    if any((ROOT/p).exists()or(ROOT/p).is_symlink()for p in ['attempt.json','result.json','client/session.json']):raise FileExistsError('fresh_clock_precheck_required')
    for p,sha in {**release['source_sha256'],**release['prerequisite_sha256']}.items():
        if digest(Path(p))!=sha:raise PermissionError('reviewed_control_source_changed')
    base=load(BASE,BASE_SHA,'v6_clock_precheck_base');base.ROOT=ROOT
    profile=load(PROFILE,PROFILE_SHA,'v6_clock_precheck_profile');profile.ROOT=ROOT;profile.base=base;profile.ROLES=ROLES;profile.NAMES=NAMES
    def command(role):
        if role not in ROLES:raise PermissionError('fixed_clock_precheck_role_required')
        return ['-i','PATH=/usr/bin:/bin','HOME=/tmp','LANG=C.UTF-8','/usr/bin/timeout','--signal=KILL','5s','/usr/bin/python3','-I','-B','/input/guest.py',role]
    profile.command=command
    cleanup=load(W/'scripts/gate1_attachment_cleanup.py','09f0f2f1db54a8cc9e31f6326a524f476366bca502ea2254bf60f5eed0564db1','v6_clock_precheck_cleanup');base._CLOSE_ATTACHMENT=cleanup.close_attachment
    public_session_sha=None
    def custody():
        for directory in [ROOT,ROOT/'proxy',ROOT/'client',ROOT/'docker-client']:
            info=directory.lstat()
            if directory.resolve()!=directory or not stat.S_ISDIR(info.st_mode)or info.st_uid!=501 or stat.S_IMODE(info.st_mode)!=0o700:raise PermissionError('owned_closed_public_directory_required')
        for p,sha in release['source_sha256'].items():
            if digest(Path(p))!=sha:raise PermissionError('reviewed_control_source_changed')
        for role in ROLES:
            names={'guest.py','clock_subset.py'}|({'session.json'}if role=='client'and public_session_sha else set())
            if {p.name for p in(ROOT/role).iterdir()}!=names:raise PermissionError('exact_public_fixture_set_required')
        if public_session_sha and digest(ROOT/'client/session.json')!=public_session_sha:raise PermissionError('immutable_public_session_changed')
        config=ROOT/'docker-client/config.json'
        if {p.name for p in config.parent.iterdir()}!={'config.json'}or config.is_symlink()or config.read_bytes()!=b'{"auths":{},"proxies":{}}\n':raise PermissionError('closed_owned_Docker_config_required')
    custody()
    # This import contains only exact production clock functions, no CLI entry point.
    subset=load(ROOT/'client/clock_subset.py',release['source_sha256'][str(ROOT/'client/clock_subset.py')],'v6_clock_precheck_subset')
    for role,source in [('client',PRODUCTION),('proxy',RELAY)]:
        if (ROOT/role/'clock_subset.py').read_text()!=extract_production(source.read_text(),role):raise PermissionError('exact_production_AST_extraction_required')
    prefix=[base.DOCKER,'--config',str(ROOT/'docker-client'),'--host',base.SOCKET];environment=base.closed_environment(ROOT/'docker-client')
    end=time.monotonic()+60;ids={};attachments={};records={}
    result={'format':'devbench.v6-minimal-clock-precheck/1',**FALSE,'calls':[],'guest_states':{},'cleanup_uncertain':False,'mutation_identity_uncertain':False,
            'allocation_source':'operator-selected software control; no authentication or scientific grant','host_normal_seconds':60,'guest_hard_seconds':5,'cleanup_call_seconds':12,
            'negative_fixture_scope':'synthetic public offset-hash binding mismatch; actual clocks unchanged'}
    def call(args,clean=False,missing=False):
        if not clean:custody()
        else:
            if digest(Path(base.DOCKER))!=release['source_sha256'][base.DOCKER]:raise PermissionError('pinned_cleanup_Docker_changed')
            config=ROOT/'docker-client/config.json'
            if config.is_symlink()or config.read_bytes()!=b'{"auths":{},"proxies":{}}\n':raise PermissionError('closed_cleanup_Docker_config_changed')
        raw=base._run_process(prefix+args,cwd=ROOT,environment=environment,prompt=b'',deadline=time.monotonic()+12 if clean else min(end,time.monotonic()+12),maximum=65536,authorize=lambda:None)
        result['calls'].append({'operation':args[0],'exit_code':raw['exit_code'],'error':raw['error'],'process_reaped':raw['process_reaped'],'stdout_bytes':len(raw['stdout']),'stderr_bytes':len(raw['stderr'])})
        if missing:
            if raw['exit_code']!=1 or raw['error']is not None or not raw['process_reaped']or b'No such container'not in raw['stderr']or args[-1].encode()not in raw['stderr']:raise RuntimeError('exact_owned_absence_unconfirmed')
        elif raw['exit_code']!=0 or raw['error']is not None or not raw['process_reaped']:raise RuntimeError('bounded_control_transport_failed')
        return raw['stdout']
    def inspect(role,clean=False):
        value=json.loads(call(['container','inspect',ids[role]],clean))[0];profile.verify(value,role,ids[role]);s=value.get('State',{})
        state={'Status':s.get('Status')if s.get('Status')in ['created','running','exited','dead']else 'unclassified',
            'Running':s.get('Running')if type(s.get('Running'))is bool else None,
            'ExitCode':s.get('ExitCode')if type(s.get('ExitCode'))is int else None,
            'Pid':s.get('Pid')if type(s.get('Pid'))is int else None,
            'OOMKilled':s.get('OOMKilled')if type(s.get('OOMKilled'))is bool else None,'error_empty':type(s.get('Error'))is str and s['Error']==''}
        result['guest_states'][role]=state;return state
    try:
        with os.fdopen(os.open(ROOT/'attempt.json',os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600),'w')as f:f.write(json.dumps({'format':'devbench.v6-clock-precheck-attempt/1',**FALSE})+'\n')
        for role in ROLES:
            result['mutation_identity_uncertain']=True;identity=call(profile.arguments(role)).decode().strip()
            if re.fullmatch('[0-9a-f]{64}',identity)is None:raise RuntimeError('owned_guest_identity_missing')
            ids[role]=identity;result['mutation_identity_uncertain']=False
            state=inspect(role)
            if state['Status']!='created'or state['Running']is not False:raise PermissionError('fresh_created_guest_required')
            attachments[role]=base.Attachment(prefix+['start','--attach',identity],environment,16384)
            while True:
                if time.monotonic()>=end:raise TimeoutError('host_control_deadline_exhausted')
                for frame in attachments[role].pump(.05):
                    if role in records:raise ValueError('single_control_receipt_required')
                    records[role]=validate_receipt(frame,role)
                state=inspect(role)
                if state['Running']is False:
                    drain_end=min(end,time.monotonic()+1)
                    while attachments[role].selector.get_map()and time.monotonic()<drain_end:
                        for frame in attachments[role].pump(.05):
                            if role in records:raise ValueError('single_control_receipt_required')
                            records[role]=validate_receipt(frame,role)
                    if attachments[role].selector.get_map()or attachments[role].buffer:raise RuntimeError('complete_public_control_transport_required')
                    if state!={'Status':'exited','Running':False,'ExitCode':0,'Pid':0,'OOMKilled':False,'error_empty':True}or role not in records:raise RuntimeError('successful_control_guest_required')
                    break
                time.sleep(.05)
            if role=='proxy':
                binding=subset.validate_clock_binding(records[role]['clock_binding'])
                session={'proxy_ip':'127.0.0.1','clock_binding':binding} # Literal unused by clock-only production functions.
                target=ROOT/'client/session.json'
                with os.fdopen(os.open(target,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o444),'w')as f:f.write(json.dumps(session,sort_keys=True)+'\n');f.flush();os.fsync(f.fileno())
                public_session_sha=digest(target)
        client=records['client'];actual=subset.validate_clock_domain(client['client_clock_domain']);proxy=subset.validate_clock_domain(client['proxy_clock_domain'])
        expected_proxy={k:binding[k]for k in subset.CLOCK_DOMAIN_FIELDS}
        if proxy!=expected_proxy:raise ValueError('actual_proxy_tuple_required')
        expected=[binding['relay_deadline_ns']-7*subset.NS,binding['relay_deadline_ns']]
        if client['positive_failure']is not None:
            allowed_failures=[subset.setup_failure(PermissionError('same_linux_monotonic_domain_required'),session,actual),subset.setup_failure(TimeoutError('relay_login_budget_exhausted'),session,actual)]
            if client['positive_failure']not in allowed_failures or client['positive_deadlines']is not None:raise ValueError('closed_positive_clock_failure_required')
        result['positive_control_passed']=client['positive_failure']is None and client['positive_deadlines']==expected and all(proxy[k]==actual[k]for k in ['boot_id_sha256','time_namespace_offsets_sha256'])
        mutant=dict(binding);mutant['time_namespace_offsets_sha256']='0'*64 if binding['time_namespace_offsets_sha256']!='0'*64 else '1'*64
        negative_session={'proxy_ip':'127.0.0.1','clock_binding':mutant}
        expected_failure=subset.setup_failure(PermissionError('same_linux_monotonic_domain_required'),negative_session,actual)
        result['negative_control_passed']=client['negative_failure']==expected_failure and client['negative_fixture_offset_sha256']==mutant['time_namespace_offsets_sha256']
        result['clock_observations']={'proxy':proxy,'client':actual};result['namespace_ids_equal']=proxy['time_namespace']==actual['time_namespace'];result['control_receipts']=records
    except BaseException as error:result['error_category']='Cancelled'if isinstance(error,(KeyboardInterrupt,SystemExit))else type(error).__name__
    finally:
        for role,identity in reversed(list(ids.items())):
            try:call(['container','rm','--force',identity],True);call(['container','inspect',identity],True,True)
            except Exception:result['cleanup_uncertain']=True
        result['absence_verified']=not result['cleanup_uncertain'];result['attachment_close_receipts']={}
        for role,attachment in attachments.items():
            try:
                if not attachment.close():result['cleanup_uncertain']=True
            except Exception:result['cleanup_uncertain']=True
            result['attachment_close_receipts'][role]=getattr(attachment,'_closure_receipt',{'closed_verified':False})
        result['attachments_closed']=all(v.get('closed_verified')is True for v in result['attachment_close_receipts'].values())and set(attachments)==set(ROLES)
        try:custody();result['source_custody_unchanged']=True
        except Exception:result['source_custody_unchanged']=False
        with os.fdopen(os.open(ROOT/'result.json',os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600),'w')as f:f.write(json.dumps(result,indent=2)+'\n')
    passed=control_passed(result);print(json.dumps({'credential_free_v6_clock_precheck_passed':passed,**FALSE}),flush=True);return 0 if passed else 1


if __name__=='__main__':raise SystemExit(main())
