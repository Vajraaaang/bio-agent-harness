"""Pure protocol construction tests: no sockets, subprocesses or model calls."""
import importlib.util
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location('owned_protocol', (Path(__file__).resolve().parents[1] / 'scripts/verify_isolated_codex_protocol.py'))
api = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(api)


@pytest.mark.parametrize('port', [0, 80, 65536, True, '1234'])
def test_only_unprivileged_integer_loopback_port(port):
    with pytest.raises(ValueError):
        api.command(port)


def test_local_custom_provider_has_no_external_or_auth_route():
    command = api.command(12345)
    assert command[0] == '/client/bin/codex'
    assert 'model_provider="owned_protocol_fixture"' in command
    assert 'model_providers.owned_protocol_fixture.requires_openai_auth=false' in command
    assert [x for x in command if 'base_url=' in x] == [
        'model_providers.owned_protocol_fixture.base_url="http://127.0.0.1:12345/v1"']
    assert command[-1] == '-'
    assert 'model_providers.owned_protocol_fixture.request_max_retries=0' in command


def test_reports_nested_tools_instead_of_assuming_empty():
    tools = [{'type': 'function', 'name': 'update_plan'},
        {'type': 'namespace', 'name': 'functions', 'tools': [
            {'type': 'function', 'name': 'read_file'}]}, {'type': 'web_search'}]
    assert api.tool_names(tools) == ['functions.read_file', 'update_plan', 'web_search']


@pytest.mark.parametrize('tools', [None, {}, [None], [{'name': 'x'}],
    [{'type': 'namespace', 'name': 1, 'tools': []}]])
def test_rejects_unknown_inventory_shape(tools):
    with pytest.raises(ValueError):
        api.tool_names(tools)


def test_response_is_fixed_fixture_without_real_usage_claim():
    events = api.fixture_events()
    assert events[-1]['type'] == 'response.completed'
    assert events[-1]['response']['output'][0]['content'][0]['text'] == api.MARKER
    assert 'usage' not in events[-1]['response']


def test_uncertain_cleanup_kills_group_before_reaping_even_if_leader_exited(monkeypatch):
    calls = []
    class Process:
        pid = 123456
        def poll(self):
            pytest.fail('must not release zombie PID before owned group cleanup')
        def wait(self, timeout):
            calls.append(('wait', timeout))
    monkeypatch.setattr(api.os, 'killpg', lambda pid, sig: calls.append(('killpg', pid, sig)))
    api.cleanup_process(Process(), False)
    assert calls == [('killpg', 123456, api.signal.SIGKILL), ('wait', 2)]


def test_completed_cleanup_does_not_target_released_process_group(monkeypatch):
    monkeypatch.setattr(api.os, 'killpg', lambda *_: pytest.fail('completed PID is no longer owned'))
    api.cleanup_process(object(), True)
