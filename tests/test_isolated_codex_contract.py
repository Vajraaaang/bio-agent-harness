"""Pure construction tests; no client/server/container execution."""
import importlib.util
import json
from pathlib import Path
import tomllib

import pytest

spec = importlib.util.spec_from_file_location('owned_contract', Path(
    '/Users/vajraang/Documents/ChatGPT/bio-agent-harness/scripts/verify_isolated_codex_contract.py'))
api = importlib.util.module_from_spec(spec)
spec.loader.exec_module(api)


def test_supported_toggles_address_all_three_observed_tools():
    command = api.command(12345)
    for setting in ('tools.update_plan.enabled=false',
        'tools.experimental_request_user_input.enabled=false', 'features.view_image=false'):
        assert setting in command
    assert 'model_provider="owned_protocol_fixture"' in command


def test_owned_canary_config_and_python_are_well_formed():
    config = tomllib.loads(api.canary_config())
    assert config['developer_instructions'] == api.SENTINELS['user_config']
    canary = config['mcp_servers']['owned_canary']
    assert canary['command'] == '/usr/bin/python3' and canary['args'][0] == '-c'
    compile(canary['args'][1], '<owned-inert-canary>', 'exec')
    assert '/output/protocol/unexpected-mcp-start' in canary['args'][1]


def test_forged_calls_are_fixed_known_tools_and_synthetic_paths():
    events = api.forged_events()
    calls = events[-1]['response']['output']
    assert len(calls) == 8
    assert {item['name'] for item in calls} == set(api.FORGED)
    assert len({item['call_id'] for item in calls}) == 8
    for item in calls:
        assert json.loads(item['arguments']) == api.FORGED[item['name']]
    assert '/output/protocol/' in json.dumps(calls)
    assert 'usage' not in events[-1]['response']


@pytest.mark.parametrize('text', [None, '', 'Plan updated', 'Error reading file',
    'Unknown tool other_tool', 'x' * 3000])
def test_generic_failure_or_success_is_not_tool_rejection(text):
    assert api.rejection_category(text, 'exec_command') == 'unverified_rejection'


@pytest.mark.parametrize('text', ["Unrecognized function name 'exec_command'",
    "Unknown tool exec_command", "unsupported function exec_command", "unsupported call: exec_command"])
def test_exact_tool_name_and_explicit_unknown_are_required(text):
    assert api.rejection_category(text, 'exec_command') == 'explicit_unknown_or_unsupported_tool'
