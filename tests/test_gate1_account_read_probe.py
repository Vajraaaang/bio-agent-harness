"""Account-only protocol and lifecycle tests; never launch the official client."""
import importlib.util
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('account_plan_probe', ROOT / 'scripts/gate1_account_read_probe.py')
api = importlib.util.module_from_spec(spec)
spec.loader.exec_module(api)


@pytest.mark.parametrize('plan', ['free', 'plus', 'pro'])
def test_account_projection_retains_only_whitelisted_nonsecret_scalars(plan):
    result = api.project_account({'account': {'type': 'chatgpt', 'planType': plan,
        'email': 'private@example.invalid', 'accessToken': 'DO_NOT_RETAIN'},
        'requiresOpenaiAuth': True, 'other': 'DO_NOT_RETAIN'})
    assert result == {'account_type': 'chatgpt', 'plan_type': plan,
        'requires_openai_auth': True, 'allowed_personal_plan_observed': True}
    assert 'private' not in json.dumps(result)
    assert 'DO_NOT_RETAIN' not in json.dumps(result)


@pytest.mark.parametrize('plan', ['business', 'enterprise', 'edu', 'team', 'prolite', 'go', 'unknown', 'private-string'])
def test_every_unqualified_plan_fails_closed_without_retaining_raw_value(plan):
    result = api.project_account({'account': {'type': 'chatgpt', 'planType': plan},
        'requiresOpenaiAuth': True})
    assert result['plan_type'] == 'unqualified'
    assert result['allowed_personal_plan_observed'] is False


def test_protocol_only_sends_initialize_then_initialized_and_account_read():
    request = api.initialize_request()
    assert request['method'] == 'initialize'
    assert request['params']['capabilities']['experimentalApi'] is False
    protocol = api.Protocol()
    followup = protocol.accept({'id': 1, 'result': {'userAgent': 'fixed'}})
    assert followup == [{'method': 'initialized'},
        {'id': 2, 'method': 'account/read', 'params': {'refreshToken': False}}]
    assert protocol.accept({'id': 2, 'result': {'account': {'type': 'chatgpt',
        'planType': 'plus', 'email': 'discard@example.invalid'}, 'requiresOpenaiAuth': True}}) == []
    assert protocol.account['plan_type'] == 'plus'


@pytest.mark.parametrize('message', [
    {'id': True, 'result': {}}, {'id': 3, 'result': {}}, {'id': 1, 'error': {'message': 'private'}},
    {'id': 10, 'method': 'account/chatgptAuthTokens/refresh', 'params': {}},
])
def test_unknown_failed_or_server_initiated_requests_are_rejected(message):
    with pytest.raises(ValueError):
        api.Protocol().accept(message)


def test_argv_and_environment_are_closed_and_no_endpoint_overrides():
    args = api.arguments()
    assert args[0] == '/client/bin/codex'
    assert args[-2:] == ['app-server', '--stdio']
    assert '--strict-config' in args
    assert '--ignore-user-config' not in args
    assert '--ignore-rules' not in args
    assert not any(value in args for value in ('exec', 'login', '--remote-control'))
    assert not any('base_url' in value for value in args)
    env = api.environment()
    assert env['CODEX_HOME'] == '/probe-home'
    assert env['CODEX_INTERNAL_APP_SERVER_REMOTE_CONTROL_DISABLED'] == '1'
    assert not any(key in env for key in ('OPENAI_API_KEY', 'CODEX_API_KEY',
        'CODEX_ACCESS_TOKEN', 'HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY'))


@pytest.mark.skipif(not hasattr(api.os, 'waitid'), reason='Linux waitid WNOWAIT lifecycle is unavailable on this host')
def test_fake_stdio_lifecycle_discards_rpc_email_and_never_calls_official_client(tmp_path, monkeypatch):
    fake = tmp_path / 'fake_rpc.py'
    fake.write_text('''import json,sys
request=json.loads(sys.stdin.readline())
assert request['method']=='initialize'
print(json.dumps({'id':1,'result':{'userAgent':'fixed'}}),flush=True)
assert json.loads(sys.stdin.readline())=={'method':'initialized'}
assert json.loads(sys.stdin.readline())=={'id':2,'method':'account/read','params':{'refreshToken':False}}
print(json.dumps({'id':2,'result':{'account':{'type':'chatgpt','planType':'plus','email':'discard@example.invalid'},'requiresOpenaiAuth':True}}),flush=True)
assert sys.stdin.read()==''
''')
    monkeypatch.setattr(api, 'WORKSPACE', tmp_path)
    monkeypatch.setattr(api, 'arguments', lambda: [sys.executable, '-I', str(fake)])
    result = api.run_probe()
    assert result['accepted_account_plan_observation'] is True
    assert result['process_reaped'] is True
    assert result['official_cli_exit_code'] == 0
    assert result['process_group_cleanup_uncertain'] is False
    assert 'discard' not in json.dumps(result)
    assert result['raw_rpc_or_stderr_retained'] is False
    assert result['thread_requests'] == result['turn_requests'] == result['live_model_calls'] == 0


def test_fake_hung_process_is_killed_and_reaped(tmp_path, monkeypatch):
    monkeypatch.setattr(api, 'WORKSPACE', tmp_path)
    monkeypatch.setattr(api, 'arguments', lambda: [sys.executable, '-I', '-c', 'import time;time.sleep(60)'])
    monkeypatch.setattr(api, 'DEADLINE_SECONDS', 0.1)
    result = api.run_probe()
    assert result['accepted_account_plan_observation'] is False
    assert result['error_category'] == 'TimeoutError'
    assert result['process_reaped'] is True
    assert result['official_cli_exit_code'] != 0
