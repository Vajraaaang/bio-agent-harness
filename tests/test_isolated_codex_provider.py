"""Local provider construction tests; no CLI process, network or actual auth."""
import base64
import importlib.util
import json
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location('provider_contract', (Path(__file__).resolve().parents[1] / 'scripts/verify_isolated_codex_provider.py'))
api = importlib.util.module_from_spec(spec)
spec.loader.exec_module(api)


def test_uses_builtin_openai_with_auth_required_by_public_provider():
    args = api.command(12345)
    assert 'model_provider="openai"' in args
    assert 'openai_base_url="http://127.0.0.1:12345/v1"' in args
    assert 'forced_login_method="chatgpt"' in args
    assert 'cli_auth_credentials_store="file"' in args
    assert not any('model_providers.' in value for value in args)
    assert not any('experimental_bearer_token' in value for value in args)
    assert not any('requires_openai_auth=false' in value for value in args)


def test_cache_is_explicitly_synthetic_and_has_no_api_key():
    cache = json.loads(api.synthetic_auth_json())
    assert cache['auth_mode'] == 'chatgpt'
    assert cache['OPENAI_API_KEY'] is None
    assert cache['tokens']['access_token'] == api.SYNTHETIC_BEARER
    header, payload, signature = cache['tokens']['id_token'].split('.')
    assert header == 'OWNED_SYNTHETIC_HEADER'
    assert signature == 'OWNED_SYNTHETIC_NOT_A_SIGNATURE'
    claims = json.loads(base64.urlsafe_b64decode(payload + '=' * (-len(payload) % 4)))
    assert claims['email'].endswith('@example.invalid')
    assert claims['https://api.openai.com/auth']['chatgpt_account_id'] == api.SYNTHETIC_ACCOUNT


def test_exact_synthetic_auth_allowed(tmp_path):
    path = tmp_path / 'auth.json'
    path.write_text(api.synthetic_auth_json())
    path.chmod(0o600)
    api.verify_synthetic_auth(path)


@pytest.mark.parametrize('mutation', ['token', 'permission', 'symlink'])
def test_foreign_or_insecure_cache_rejected(tmp_path, mutation):
    path = tmp_path / 'auth.json'
    path.write_text(api.synthetic_auth_json())
    path.chmod(0o600)
    if mutation == 'token':
        path.write_text(path.read_text().replace(api.SYNTHETIC_BEARER, 'other-token'))
    elif mutation == 'permission':
        path.chmod(0o644)
    else:
        actual = tmp_path / 'actual'
        path.rename(actual)
        path.symlink_to(actual)
    with pytest.raises(PermissionError):
        api.verify_synthetic_auth(path)


def test_request_headers_must_match_owned_synthetic_values():
    assert api.synthetic_headers_match({'Authorization': 'Bearer ' + api.SYNTHETIC_BEARER,
        'ChatGPT-Account-ID': api.SYNTHETIC_ACCOUNT})
    for headers in ({}, {'Authorization': 'Bearer actual-unknown'},
        {'Authorization': 'Bearer ' + api.SYNTHETIC_BEARER, 'ChatGPT-Account-ID': 'other-account'}):
        assert not api.synthetic_headers_match(headers)


def test_fixture_contract_preserves_live_gates_and_pinned_catalog():
    source = Path(api.__file__).read_text()
    assert "'live_generation_allowed': False" in source
    assert "'biological_execution_allowed': False" in source
    assert "'signedin_authentication_qualified': False" in source
    assert "'native_admission_qualified': False" in source
    assert "len(upgrades) == 2" in source
    assert json.loads(api.CATALOG_JSON) == json.loads((Path(__file__).resolve().parents[1] / 'reports/rna-native-gate1/2026-10-21-v2/client/public-models-0.147.0.json').read_text())
