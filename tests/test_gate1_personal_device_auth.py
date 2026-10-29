import importlib.util
from pathlib import Path
from copy import deepcopy
import pytest
W=Path('/Users/vajraang/Documents/ChatGPT/bio-agent-harness')

def load():
    p=W/'scripts/run_gate1_personal_device_auth.py';s=importlib.util.spec_from_file_location('auth_supervisor_under_test',p)
    m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m


def login_result():
    return {'event':'login_result','format':'devbench.personal-device-login-result/1',
        'official_cli_invocations_attempted':1,'official_cli_launch_confirmed':True,'official_cli_exit_code':0,
        'process_reaped':True,'process_group_cleanup_uncertain':False,'error_category':None,'device_prompt_presented':True,
        'combined_auth_stream_bytes':600,'official_login_completed':True,'raw_auth_stream_retained':False,
        'raw_auth_stream_hash_retained':False,'authentication_cache_contents_read_by_worker':False,
        'authentication_cache_hashed_by_worker':False,'live_generation_allowed':False,'biological_execution_allowed':False,
        'native_admission_qualified':False,'official_status_invocations_attempted':1,'official_status_verified_chatgpt':True,
        'official_status_exit_code':0,'official_status_error_category':None,'official_status_process_reaped':True,
        'official_status_stream_bytes':24,'official_status_process_group_cleanup_uncertain':False}


def test_auth_result_rejects_unknown_values_or_inconsistent_success():
    m=load();value=login_result();assert m.validate_login_result(value)==value
    for key,bad in [('access_token','untrusted'),('official_status_verified_chatgpt',False),('process_reaped',False),
        ('error_category','untrusted error text'),('raw_auth_stream_retained',True),('official_cli_invocations_attempted',2)]:
        changed=deepcopy(value);changed[key]=bad
        with pytest.raises(ValueError):m.validate_login_result(changed)


def test_digest_never_opens_private_credentials_or_login_logs():
    m=load()
    for path in [m.PRIVATE/'codex-home/auth.json',m.ROOT/'auth.json',m.ROOT/'codex-login.log',Path('/Users/vajraang/.codex/auth.json')]:
        with pytest.raises(PermissionError):m.digest(path)


def test_fixed_roles_keep_private_mount_out_of_proxy_and_public_network_out_of_client():
    m=load();proxy=m.container_arguments('proxy','outside-id');client=m.container_arguments('client','inside-id')
    assert str(m.PRIVATE)not in str(proxy)and str(m.PRIVATE)in str(client)
    assert proxy[proxy.index('--user')+1]=='65534:65534'and client[client.index('--user')+1]=='501:20'
    assert proxy[proxy.index('--network')+1]=='outside-id'and client[client.index('--network')+1]=='inside-id'
    assert '--dns'not in proxy and client[client.index('--dns')+1]=='127.0.0.1'
    assert proxy[proxy.index('--dns-search')+1]=='.'and client[client.index('--dns-search')+1]=='.'
    assert '315s'in proxy and '315s'in client


def test_private_epoch_never_reuses_existing_state(tmp_path,monkeypatch):
    m=load();work=tmp_path/'workspace';work.mkdir();(work/'.gitignore').write_text('.devbench-codex-auth/\n')
    private=work/'.devbench-codex-auth/epoch1';private.parent.mkdir(mode=0o700);private.mkdir(mode=0o700)
    marker=private/'never-read-auth.json';marker.write_text('owned synthetic old state')
    monkeypatch.setattr(m,'WORKSPACE',work);monkeypatch.setattr(m,'PRIVATE',private)
    with pytest.raises(FileExistsError):m.fresh_private_state(b'public policy')
    assert marker.read_text()=='owned synthetic old state'


def test_fresh_network_profile_rejects_missing_gateway_inhibition_and_extra_members():
    m=load();guard=m.load_pinned(m.GATEWAY,m.GATEWAY_SHA,'test_exact_network_guard')
    inside={'Id':'a'*64,'Name':m.NAMES['inside'],'Driver':'bridge','Internal':True,'EnableIPv4':True,'EnableIPv6':False,
        'Options':{'com.docker.network.bridge.inhibit_ipv4':'true'},'Containers':{}}
    m.verify_network(inside,'inside','a'*64,set(),guard)
    changed=deepcopy(inside);changed['Options']={}
    with pytest.raises(PermissionError):m.verify_network(changed,'inside','a'*64,set(),guard)
    changed=deepcopy(inside);changed['Containers']={'unexpected':{}}
    with pytest.raises(PermissionError):m.verify_network(changed,'inside','a'*64,set(),guard)
    outside={**inside,'Name':m.NAMES['outside'],'Internal':False,'Options':dict(m.OUTSIDE_OPTIONS)}
    m.verify_network(outside,'outside','a'*64,set(),guard)
    outside['Options']['com.docker.network.bridge.enable_icc']='true'
    with pytest.raises(PermissionError):m.verify_network(outside,'outside','a'*64,set(),guard)


def test_tls_probe_role_has_no_private_mount_or_login_operation():
    m=load();args=m.container_arguments('probe','inside-id')
    assert str(m.PRIVATE)not in str(args)
    assert args[-1]=='tls-only'and '45s'in args
    assert args[args.index('--network')+1]=='inside-id'
    assert m.container_arguments('client','inside-id')[-1]=='device-login'
