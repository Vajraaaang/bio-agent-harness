"""Validate HTTP framing in memory; no sockets, auth or subprocesses."""
import ast
from http.server import BaseHTTPRequestHandler
import importlib.util
from io import BytesIO
from pathlib import Path

spec = importlib.util.spec_from_file_location('provider_http11', Path(
    '/Users/vajraang/Documents/ChatGPT/bio-agent-harness/scripts/verify_isolated_codex_provider_http11.py'))
api = importlib.util.module_from_spec(spec)
spec.loader.exec_module(api)


def test_426_has_http11_status_line_before_client_status_dispatch():
    class Handler(BaseHTTPRequestHandler):
        protocol_version = api.MOCK_HTTP_VERSION
        def log_message(self, *_args):
            pass
    handler = object.__new__(Handler)
    handler.request_version = 'HTTP/1.1'
    handler.requestline = 'GET /v1/responses HTTP/1.1'
    handler.command = 'GET'
    handler.wfile = BytesIO()
    handler.send_error(426)
    response = handler.wfile.getvalue()
    assert response.startswith(b'HTTP/1.1 426 ')
    assert b'Connection: close\r\n' in response
    assert b'Content-Length: ' in response
    assert BaseHTTPRequestHandler.protocol_version == 'HTTP/1.0'
    handler.protocol_version = 'HTTP/1.0'
    handler.wfile = BytesIO()
    handler.send_error(426)
    assert handler.wfile.getvalue().startswith(b'HTTP/1.0 426 ')


def test_actual_nested_handler_uses_validated_version():
    source = ast.parse(Path(api.__file__).read_text())
    main = next(n for n in source.body if isinstance(n, ast.FunctionDef) and n.name == 'main')
    handler = next(n for n in main.body if isinstance(n, ast.ClassDef) and n.name == 'Handler')
    binding = next(n for n in handler.body if isinstance(n, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == 'protocol_version' for t in n.targets))
    assert isinstance(binding.value, ast.Name) and binding.value.id == 'MOCK_HTTP_VERSION'
    assert api.MOCK_HTTP_VERSION == 'HTTP/1.1'


def test_http11_fix_preserves_trace_command_events_and_twenty_second_bound():
    old = ast.parse(Path(
        '/Users/vajraang/Documents/ChatGPT/bio-agent-harness/scripts/verify_isolated_codex_provider_trace.py').read_text())
    new = ast.parse(Path(api.__file__).read_text())
    for name in ('command', 'execute', 'cleanup_process', 'fixture_events', 'forged_events',
        'synthetic_auth_json', 'request_projection'):
        old_node = next(n for n in old.body if isinstance(n, ast.FunctionDef) and n.name == name)
        new_node = next(n for n in new.body if isinstance(n, ast.FunctionDef) and n.name == name)
        assert ast.dump(old_node) == ast.dump(new_node)
