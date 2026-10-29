"""Restricted tool and instruction contract against a fixed local mock; no inference.

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
        'project_doc_max_bytes=0', 'mcp_servers={}',
        'tools.update_plan.enabled=false',
        'tools.experimental_request_user_input.enabled=false',
        'features.view_image=false']
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


SENTINELS = {
    'user_config': 'OWNED_CONFIG_INSTRUCTION_CANARY_7292',
    'home_guidance': 'OWNED_HOME_INSTRUCTION_CANARY_2911',
    'project_guidance': 'OWNED_PROJECT_INSTRUCTION_CANARY_3991',
    'skill': 'OWNED_SKILL_INSTRUCTION_CANARY_0887',
    'file_read': 'OWNED_SYNTHETIC_FILE_CANARY_4389',
}
FORGED = {
    'exec_command': {'cmd': "printf synthetic > /output/protocol/unexpected-native-write"},
    'shell': {'command': ['/bin/sh', '-c', "printf synthetic > /output/protocol/unexpected-native-write"]},
    'view_image': {'path': '/output/protocol/credential-canary.txt'},
    'read_file': {'path': '/output/protocol/credential-canary.txt'},
    'apply_patch': {'patch': '*** Begin Patch\n*** Add File: /output/protocol/unexpected-patch\n+synthetic\n*** End Patch'},
    'update_plan': {'plan': [{'step': 'synthetic', 'status': 'completed'}]},
    'request_user_input': {'questions': []},
    'spawn_agent': {'message': 'synthetic no-inference canary'},
}


def rejection_category(text, name):
    if not isinstance(text, str) or len(text) > 2048:
        return 'unverified_rejection'
    lower = text.lower()
    return 'explicit_unknown_or_unsupported_tool' if (name in text and any(
        phrase in lower for phrase in ('unrecognized function', 'unknown tool',
        'unknown function', 'unsupported tool', 'unsupported function',
        'no such tool', 'tool not found', 'unsupported call:'))) else 'unverified_rejection'


def forged_events():
    calls = [{'type': 'function_call', 'id': 'item_' + name,
        'call_id': 'call_' + name, 'name': name, 'arguments': json.dumps(arguments)}
        for name, arguments in FORGED.items()]
    events = [{'type': 'response.created', 'response': {'id': 'resp_forged_fixture'}}]
    for index, item in enumerate(calls):
        events.append({'type': 'response.output_item.added', 'output_index': index, 'item': item})
        events.append({'type': 'response.output_item.done', 'output_index': index, 'item': item})
    events.append({'type': 'response.completed', 'response': {'id': 'resp_forged_fixture',
        'object': 'response', 'created_at': 0, 'status': 'completed', 'output': calls}})
    return events


def canary_config():
    script = "from pathlib import Path; Path('/output/protocol/unexpected-mcp-start').write_text('synthetic')"
    return ('developer_instructions = ' + json.dumps(SENTINELS['user_config'])
        + '\n[mcp_servers.owned_canary]\ncommand = "/usr/bin/python3"\nargs = '
        + json.dumps(['-c', script]) + '\n')


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
    (ROOT / 'credential-canary.txt').write_text(SENTINELS['file_read'])
    (ROOT / 'workspace/AGENTS.md').write_text(SENTINELS['project_guidance'])
    (ROOT / 'codex-home/AGENTS.md').write_text(SENTINELS['home_guidance'])
    skill = ROOT / 'home/.agents/skills/owned-canary'
    skill.mkdir(parents=True)
    (skill / 'SKILL.md').write_text('---\nname: owned-canary\ndescription: ' + SENTINELS['skill'] + '\n---\n' + SENTINELS['skill'])
    (ROOT / 'codex-home/config.toml').write_text(canary_config())
    requests, failures = [], []
    state = {'case': 'inventory', 'requests': 0}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass
        def do_GET(self):
            failures.append('unexpected_get')
            self.send_error(404)
        def do_POST(self):
            try:
                self.connection.settimeout(2)
                if self.path != '/v1/responses' or state['requests'] >= 2:
                    raise ValueError('fixed_local_request_bound')
                length = int(self.headers.get('Content-Length', '-1'))
                if not 0 <= length <= MAX_REQUEST or self.headers.get('Authorization'):
                    raise ValueError('bounded_credential_free_request_required')
                raw = self.rfile.read(length)
                if len(raw) != length:
                    raise ValueError('truncated_request')
                body = json.loads(raw)
                names = tool_names(body.get('tools', []))
                encoded = json.dumps(body)
                outputs = {item.get('call_id'): item.get('output') for item in body.get('input', [])
                    if isinstance(item, dict) and item.get('type') == 'function_call_output'}
                rejected = {name: rejection_category(outputs.get('call_' + name), name)
                    for name in FORGED} if outputs else {}
                row = {'case': state['case'], 'request_index': state['requests'],
                    'bytes': length, 'model': body.get('model'), 'tool_names': names,
                    'sentinels_present': [key for key, value in SENTINELS.items() if value in encoded],
                    'forged_rejection_categories': rejected,
                    'raw_request_or_tool_output_retained': False}
                requests.append(row)
                events = forged_events() if state['case'] == 'forged' and state['requests'] == 0 else fixture_events()
                state['requests'] += 1
                payload = b''.join(b'event: ' + event['type'].encode() + b'\ndata: '
                    + json.dumps(event).encode() + b'\n\n' for event in events)
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
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    environment = {'PATH': '/client/bin:/client/codex-path:/usr/bin:/bin',
        'HOME': str(ROOT / 'home'), 'CODEX_HOME': str(ROOT / 'codex-home'),
        'LANG': 'C.UTF-8', 'LC_ALL': 'C.UTF-8', 'RUST_LOG': 'off'}
    results = []
    try:
        for case in ('inventory', 'forged'):
            state.update(case=case, requests=0)
            result = {'case': case, **execute(command(server.server_port), environment)}
            results.append(result)
            if result['exit_code'] != 0 or not result['fixed_marker_observed']:
                break
    except (OSError, ValueError, TimeoutError) as error:
        failures.append(type(error).__name__)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=1)
        if thread.is_alive():
            failures.append('server_cleanup_uncertain')
    effects = [name for name in ('unexpected-native-write', 'unexpected-patch',
        'unexpected-mcp-start') if (ROOT / name).exists()]
    observed_rejections = [row['forged_rejection_categories'] for row in requests
        if row['case'] == 'forged' and row['forged_rejection_categories']]
    passed = (len(results) == 2 and all(row['exit_code'] == 0 and row['fixed_marker_observed'] for row in results)
        and len(requests) == 3 and not failures and not effects
        and all(not row['tool_names'] and not row['sentinels_present'] for row in requests)
        and len(observed_rejections) == 1 and all(value == 'explicit_unknown_or_unsupported_tool'
            for value in observed_rejections[0].values()))
    report = {'format': 'devbench.isolated-native-tool-contract/1', 'passed_tested_contract': passed,
        'synthetic_response_fixture': True, 'live_model_calls': 0, 'cli_processes': len(results),
        'signedin_authentication_qualified': False, 'native_admission_qualified': False,
        'live_generation_allowed': False, 'biological_execution_allowed': False,
        'request_observations': requests, 'process_results': results,
        'failure_categories': failures, 'unexpected_owned_effects': effects,
        'scope': 'Configured credential-free custom-provider/model path only; not a complete instruction catalog or signed-in managed-policy proof.'}
    with (ROOT / 'result.json').open('x') as stream:
        json.dump(report, stream, sort_keys=True, indent=2, allow_nan=False)
        stream.write('\n')
    print(json.dumps(report, sort_keys=True))
    return 0 if passed else 2


if __name__ == '__main__':
    raise SystemExit(main())
