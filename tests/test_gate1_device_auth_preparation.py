import importlib.util
from pathlib import Path
import pytest

PATH=Path('/Users/vajraang/Documents/ChatGPT/bio-agent-harness/scripts/prepare_gate1_device_auth.py')

def load():
    spec=importlib.util.spec_from_file_location('auth_preparation_under_test',PATH)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def test_preparation_has_exact_login_only_argv_and_retains_admission_hold():
    module=load();plan=module.prepare_plan()
    assert plan['arguments'][-2:]==['login','--device-auth']
    assert not any(x in plan['arguments'] for x in ['exec','resume','--api-key','--ignore-user-config','--ignore-rules'])
    assert plan['launch_allowed'] is False and plan['transport']['qualified_profile_binding'] is None
    assert plan['transport']['allowed_authority']=='auth.openai.com:443'
    assert plan['transport']['model_hosts_allowed'] is False
    assert plan['production_native_admission_qualified'] is False
    assert plan['private_root_created_by_preparation'] is False


def test_environment_never_inherits_credentials_or_endpoints(monkeypatch):
    module=load();monkeypatch.setenv('OPENAI_API_KEY','untrusted');monkeypatch.setenv('CODEX_HOME','/untrusted')
    env=module.login_environment('http://gate1-auth-relay:8443')
    assert env['CODEX_HOME']=='/private/codex-home' and 'OPENAI_API_KEY' not in env
    assert env['NO_PROXY']=='' and env['HTTPS_PROXY']=='http://gate1-auth-relay:8443'


@pytest.mark.parametrize('url',['https://remote:443','http://user:secret@relay:8443','http://relay:8443/path','http://relay:8443\nOPENAI_API_KEY=x'])
def test_unqualified_transport_shapes_cannot_add_credentials_or_overrides(url):
    with pytest.raises(PermissionError):load().login_environment(url)


def test_prompt_extractor_shows_only_exact_official_user_challenge():
    module=load()
    sample=(b'Welcome to Codex\n1. Open this link in your browser and sign in to your account\n'
        b'   \x1b[94mhttps://auth.openai.com/codex/device\x1b[0m\n'
        b'2. Enter this one-time code (expires in 15 minutes)\n   \x1b[94mABCD-EFGH\x1b[0m\n')
    assert module.extract_device_prompt(sample)=={'verification_url':module.VERIFICATION_URL,'user_code':'ABCD-EFGH'}
    for invalid in [sample.replace(b'auth.openai.com',b'untrusted.invalid'),sample+sample,sample.replace(b'ABCD-EFGH',b'access_token=untrusted'),b'raw unknown auth error',b'x'*8193]:
        with pytest.raises(ValueError):module.extract_device_prompt(invalid)
