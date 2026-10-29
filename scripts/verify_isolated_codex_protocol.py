"""Synthetic local HTTP fixture for an isolated official CLI; no inference service.

Run only in the reviewed network-none Linux enclosure. Every response is fixed
fixture data. This cannot qualify signed-in authentication or admit generation.
"""
from __future__ import annotations

import hashlib
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import selectors
import signal
import subprocess
import sys
from threading import Thread
import time

CLIENT = Path('/client/bin/codex')
CLIENT_SHA = 'cb0a15567e9a60a5820d54b0f6ae86d504dc3805c1eab21a47f70e3eb7b73a40'
ROOT = Path('/output/protocol')
MAX_REQUEST = 1024 * 1024
MAX_CAPTURE = 128 * 1024
MARKER = 'DEVHARNESS_LOCAL_PROTOCOL_FIXTURE_NO_INFERENCE'


def tool_names(tools):
    """Keep names/types only, including nested namespace specs."""
    if not isinstance(tools, list):
        raise ValueError('tool_array_required')
    names = []
    for tool in tools:
        if not isinstance(tool, dict) or not isinstance(tool.get('type'), str):
            raise ValueError('invalid_tool_spec')
        if tool['type'] == 'namespace':
            namespace = tool.get('name')
            if not isinstance(namespace, str):
                raise ValueError('invalid_tool_namespace')
            names.extend(namespace + '.' + item for item in tool_names(tool.get('tools')))
        else:
            name = tool.get('name', tool['type'])
            if not isinstance(name, str):
                raise ValueError('invalid_tool_name')
            names.append(name)
    return sorted(names)


def fixture_events():
    message = {'id': 'msg_owned_fixture', 'type': 'message', 'role': 'assistant',
        'status': 'completed', 'content': [{'type': 'output_text', 'text': MARKER,
        'annotations': []}]}
    return [
        {'type': 'response.created', 'response': {'id': 'resp_owned_fixture'}},
        {'type': 'response.output_item.added', 'output_index': 0,
            'item': {'id': message['id'], 'type': 'message', 'role': 'assistant', 'content': []}},
        {'type': 'response.output_text.delta', 'item_id': message['id'],
            'output_index': 0, 'content_index': 0, 'delta': MARKER},
        {'type': 'response.output_item.done', 'output_index': 0, 'item': message},
        {'type': 'response.completed', 'response': {'id': 'resp_owned_fixture',
            'object': 'response', 'created_at': 0, 'status': 'completed',
            'output': [message]}},
    ]


def command(port):
    if type(port) is not int or not 1024 <= port <= 65535:
        raise ValueError('owned_loopback_port_required')
    args = [str(CLIENT), 'exec', '--ignore-user-config', '--ignore-rules',
        '--strict-config', '--ephemeral', '--json', '--skip-git-repo-check',
        '--sandbox', 'danger-full-access', '--cd', '/output/protocol/workspace',
        '--model', 'gpt-6.1-sol']
    config = ['model_provider="owned_protocol_fixture"',
        'model_providers.owned_protocol_fixture.name="Owned no-inference fixture"',
        'model_providers.owned_protocol_fixture.base_url="http://127.0.0.1:' + str(port) + '/v1"',
        'model_providers.owned_protocol_fixture.wire_api="responses"',
        'model_providers.owned_protocol_fixture.requires_openai_auth=false',
        'model_providers.owned_protocol_fixture.supports_websockets=false',
        'model_providers.owned_protocol_fixture.request_max_retries=0',
        'model_providers.owned_protocol_fixture.stream_max_retries=0',
        'model_providers.owned_protocol_fixture.stream_idle_timeout_ms=5000',
        'web_search="disabled"', 'features.shell_tool=false',
        'features.apps=false', 'features.plugins=false', 'features.hooks=false',
        'features.memories=false', 'features.multi_agent=false',
        'features.multi_agent_v2=false', 'features.code_mode=false',
        'features.code_mode_host=false', 'features.request_permissions_tool=false',
        'skills.bundled.enabled=false', 'skills.include_instructions=false',
        'project_doc_max_bytes=0', 'mcp_servers={}']
    for value in config:
        args.extend(['-c', value])
    return [*args, '-']


def execute(argv, environment):
    """One child, finite deadline/output; kill its group on uncertainty."""
    output = bytearray()
    process = subprocess.Popen(argv, env=environment, cwd=str(ROOT),
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        start_new_session=True, close_fds=True)
    completed = False
    try:
        process.stdin.write(b'Return the fixed local protocol fixture marker. No tools.\n')
        process.stdin.close()
        os.set_blocking(process.stdout.fileno(), False)
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            end = time.monotonic() + 20
            while selector.get_map():
                if time.monotonic() >= end:
                    raise TimeoutError('local_fixture_deadline')
                for key, _ in selector.select(0.05):
                    chunk = os.read(key.fd, min(8192, MAX_CAPTURE + 1 - len(output)))
                    if not chunk:
                        selector.unregister(key.fileobj)
                    output.extend(chunk)
                    if len(output) > MAX_CAPTURE:
                        raise ValueError('local_fixture_output_bound')
        code = process.wait(timeout=1)
        completed = True
        return {'exit_code': code, 'combined_capture_bytes': len(output),
            'fixed_marker_observed': MARKER.encode() in output,
            'raw_capture_retained': False}
    finally:
        cleanup_process(process, completed)
        process.stdout.close()


def cleanup_process(process, completed):
    # On timeout the leader may be an unreaped zombie while descendants retain
    # its pipe. Kill the owned group before polling/reaping can release its PID.
    if not completed:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait(timeout=2)


def main():
    if sys.platform != 'linux' or os.environ.get('DEVBENCH_LOCAL_PROTOCOL_ONLY') != '1':
        raise PermissionError('reviewed_network_none_linux_enclosure_required')
    if any(os.environ.get(k) for k in ('OPENAI_API_KEY', 'CODEX_API_KEY', 'CODEX_ACCESS_TOKEN')):
        raise PermissionError('credential_free_fixture_required')
    h = hashlib.sha256()
    with CLIENT.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    if h.hexdigest() != CLIENT_SHA:
        raise ValueError('official_client_changed')
    ROOT.mkdir(mode=0o700)
    for part in ('home', 'codex-home', 'workspace'):
        (ROOT / part).mkdir(mode=0o700)
    requests, failures = [], []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            failures.append('unexpected_get')
            self.send_error(404)

        def do_POST(self):
            try:
                self.connection.settimeout(2)
                if self.path != '/v1/responses' or requests:
                    raise ValueError('one_fixed_responses_request_required')
                length = int(self.headers.get('Content-Length', '-1'))
                if not 0 <= length <= MAX_REQUEST or self.headers.get('Authorization'):
                    raise ValueError('bounded_credential_free_request_required')
                data = self.rfile.read(length)
                if len(data) != length:
                    raise ValueError('truncated_request')
                body = json.loads(data)
                names = tool_names(body.get('tools', []))
                requests.append({'bytes': length, 'model': body.get('model'),
                    'tool_names': names, 'input_sha256': hashlib.sha256(data).hexdigest(),
                    'raw_request_retained': False})
                payload = b''.join(b'event: ' + event['type'].encode() + b'\ndata: '
                    + json.dumps(event).encode() + b'\n\n' for event in fixture_events())
                self.send_response(200)
                self.send_header('Content-Type', 'text/event-stream')
                self.send_header('Content-Length', str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
            except (ValueError, TypeError, KeyError, OSError):
                failures.append('invalid_local_request')
                try:
                    self.send_error(400)
                except OSError:
                    pass

    server = HTTPServer(('127.0.0.1', 0), Handler)
    server.timeout = 0.2
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    environment = {'PATH': '/client/bin:/client/codex-path:/usr/bin:/bin',
        'HOME': str(ROOT / 'home'), 'CODEX_HOME': str(ROOT / 'codex-home'),
        'LANG': 'C.UTF-8', 'LC_ALL': 'C.UTF-8', 'RUST_LOG': 'off'}
    result = {'format': 'devbench.isolated-official-cli-local-protocol/1',
        'synthetic_response_fixture': True, 'live_model_calls': 0,
        'signedin_authentication_qualified': False, 'native_admission_qualified': False,
        'live_generation_allowed': False, 'biological_execution_allowed': False}
    try:
        result.update(execute(command(server.server_port), environment))
    except (OSError, ValueError, TimeoutError) as error:
        result['error_category'] = type(error).__name__
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=1)
        if thread.is_alive():
            failures.append('server_cleanup_uncertain')
    result.update(requests=requests, server_failures=failures,
        status='observed_protocol_only' if len(requests) == 1 and not failures
            and result.get('exit_code') == 0 else 'failed_local_protocol')
    target = ROOT / 'result.json'
    with target.open('x') as stream:
        json.dump(result, stream, sort_keys=True, indent=2, allow_nan=False)
        stream.write('\n')
    print(json.dumps(result, sort_keys=True))
    return 0 if result['status'] == 'observed_protocol_only' else 2


if __name__ == '__main__':
    raise SystemExit(main())
