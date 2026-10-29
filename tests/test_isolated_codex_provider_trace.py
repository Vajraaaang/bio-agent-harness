"""Only test local transport projection; no CLI, auth or network."""
import ast
import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location('provider_trace', Path(
    '/Users/vajraang/Documents/ChatGPT/bio-agent-harness/scripts/verify_isolated_codex_provider_trace.py'))
api = importlib.util.module_from_spec(spec)
spec.loader.exec_module(api)


def test_projection_drops_query_and_raw_headers():
    headers = {'Authorization': 'Bearer ' + api.SYNTHETIC_BEARER,
        'ChatGPT-Account-ID': api.SYNTHETIC_ACCOUNT, 'Upgrade': 'websocket',
        'Unexpected': 'do-not-retain'}
    row = api.request_projection('GET', '/v1/responses?secret=do-not-retain', headers, 'category')
    assert row == {'method': 'GET', 'path': '/v1/responses',
        'websocket_upgrade': True, 'synthetic_auth_headers_match': True,
        'response_category': 'category'}


def test_projection_bounds_paths_and_handles_bad_urls():
    for path in ['/v1/' + 'x' * 130, '/v1/non-ascii-\u00a3']:
        assert api.request_projection('GET', path, {}, 'unknown')['path'] == '<path-outside-bound>'
    assert api.request_projection('GET', 'http://[invalid', {}, 'unknown')['path'] == '<invalid-url>'


def test_trace_preserves_command_fixtures_execute_and_bounds():
    old = ast.parse(Path(
        '/Users/vajraang/Documents/ChatGPT/bio-agent-harness/scripts/verify_isolated_codex_provider.py').read_text())
    new = ast.parse(Path(api.__file__).read_text())
    for name in ('command', 'execute', 'cleanup_process', 'fixture_events', 'forged_events', 'synthetic_auth_json'):
        old_node = next(node for node in old.body if isinstance(node, ast.FunctionDef) and node.name == name)
        new_node = next(node for node in new.body if isinstance(node, ast.FunctionDef) and node.name == name)
        assert ast.dump(old_node) == ast.dump(new_node)
