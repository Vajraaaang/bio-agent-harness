"""Preparation checks only; Linux fake-child launch requires exact release."""
import hashlib
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('isolated_account_fake', ROOT / 'scripts/verify_isolated_account_probe_fake.py')
api = importlib.util.module_from_spec(spec)
spec.loader.exec_module(api)


def test_embedded_account_helper_is_exact_frozen_source():
    source = (ROOT / 'scripts/gate1_account_read_probe.py').read_text()
    assert api.HELPER_SOURCE == source
    assert hashlib.sha256(source.encode()).hexdigest() == api.HELPER_SHA256


def test_fake_child_only_uses_owned_json_stdio_protocol():
    assert 'import json,sys' in api.FAKE_SOURCE
    assert "'method':'account/read'" in api.FAKE_SOURCE
    assert "'refreshToken':False" in api.FAKE_SOURCE
    for forbidden in ('auth.json', 'open(', 'socket', 'urllib', '/client', 'thread/start', 'turn/start'):
        assert forbidden not in api.FAKE_SOURCE


def test_fixture_does_not_call_official_client_or_helper_main():
    source = Path(api.__file__).read_text()
    body = source[source.index('def run_fake_lifecycle():'):]
    assert "namespace['run_probe']()" in body
    assert "namespace['main']()" not in body
    assert "'official_client_launches': 0" in body
    assert "'actual_account_plan_observation_performed': False" in body
    assert "'native_admission_qualified': False" in body
