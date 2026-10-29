"""Fixed official account/read probe; source preparation, no admission authority.

Only the official client reads the separately mounted owned authentication file.
This worker never reads, copies or hashes credentials and never starts a turn.
Run only after independent release in a network-none account-only enclosure.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import selectors
import signal
import stat
import subprocess
import sys
import time

CLIENT = Path('/client/bin/codex')
CLIENT_SHA256 = 'cb0a15567e9a60a5820d54b0f6ae86d504dc3805c1eab21a47f70e3eb7b73a40'
PROBE_HOME = Path('/probe-home')
USER_HOME = Path('/tmp/account-probe-user')
WORKSPACE = Path('/tmp/account-probe-workspace')
DEADLINE_SECONDS = 20
MAX_STREAM_BYTES = 98304
MAX_LINE_BYTES = 32768
MAX_RPC_FRAMES = 32
ALLOWED_PLANS = frozenset({'free', 'plus', 'pro'})
PUBLIC_POLICY = ('Only the fixed official account/read operation is authorized here.\n'
    'Do not start threads, turns, models, tools, analysis, workflows, grants, or production admission.\n')


def arguments():
    args = [str(CLIENT), '--strict-config']
    controls = ['model_provider="openai"', 'cli_auth_credentials_store="file"',
        'forced_login_method="chatgpt"', 'features.apps=false', 'features.plugins=false',
        'features.enable_mcp_apps=false', 'features.hooks=false', 'features.memories=false',
        'features.shell_tool=false', 'features.view_image=false', 'features.image_generation=false',
        'features.code_mode=false', 'features.code_mode_only=false', 'features.code_mode_host=false',
        'features.multi_agent=false', 'features.multi_agent_v2=false', 'features.goals=false',
        'features.token_budget=false', 'features.current_time_reminder=false',
        'features.deferred_executor=false', 'features.request_permissions_tool=false',
        'features.use_agent_identity=false', 'orchestrator.skills.enabled=false',
        'orchestrator.mcp.enabled=false', 'skills.bundled.enabled=false',
        'skills.include_instructions=false', 'project_doc_max_bytes=0', 'mcp_servers={}',
        'web_search="disabled"', 'tools.update_plan.enabled=false',
        'tools.experimental_request_user_input.enabled=false']
    for value in controls:
        args.extend(['-c', value])
    return [*args, 'app-server', '--stdio']


def environment():
    # This pinned internal disabled-start marker is consumed by the official CLI
    # before threads start. The removed remote_control feature flag is ineffective.
    return {'PATH': '/client/bin:/client/codex-path:/usr/bin:/bin',
        'HOME': str(USER_HOME), 'CODEX_HOME': str(PROBE_HOME),
        'LANG': 'C.UTF-8', 'LC_ALL': 'C.UTF-8', 'RUST_LOG': 'off',
        'CODEX_INTERNAL_APP_SERVER_REMOTE_CONTROL_DISABLED': '1'}


def initialize_request():
    return {'id': 1, 'method': 'initialize', 'params': {
        'clientInfo': {'name': 'devbench_account_read_probe', 'title': None, 'version': '1'},
        'capabilities': {'experimentalApi': False, 'requestAttestation': False}}}


def account_read_request():
    return {'id': 2, 'method': 'account/read', 'params': {'refreshToken': False}}


def project_account(result):
    """Whitelist scalars only. Email, extra fields and raw RPC never escape RAM."""
    if not isinstance(result, dict) or type(result.get('requiresOpenaiAuth')) is not bool:
        raise ValueError('invalid_account_response')
    account = result.get('account')
    if not isinstance(account, dict):
        raise ValueError('chatgpt_account_required')
    if account.get('type') != 'chatgpt' or type(account.get('planType')) is not str:
        raise ValueError('personal_chatgpt_account_required')
    plan = account['planType']
    # Unknown/unqualified plan strings are not retained, even in a failed report.
    return {'account_type': 'chatgpt', 'plan_type': plan if plan in ALLOWED_PLANS else 'unqualified',
        'requires_openai_auth': result['requiresOpenaiAuth'],
        'allowed_personal_plan_observed': plan in ALLOWED_PLANS and result['requiresOpenaiAuth']}


class Protocol:
    """One initialize handshake and one account read; no generic RPC dispatcher."""
    def __init__(self):
        self.initialized = False
        self.account = None
        self.frames = 0

    def accept(self, message):
        self.frames += 1
        if self.frames > MAX_RPC_FRAMES or not isinstance(message, dict):
            raise ValueError('rpc_frame_bound')
        if 'method' in message:
            if 'id' in message:
                raise ValueError('server_request_not_authorized')
            # Notifications may contain account data. Discard them without logs.
            return []
        expected_id = 2 if self.initialized else 1
        if (type(message.get('id')) is not int or message.get('id') != expected_id
                or 'error' in message or not isinstance(message.get('result'), dict)):
            raise ValueError('unexpected_or_failed_rpc_response')
        if not self.initialized:
            self.initialized = True
            return [{'method': 'initialized'}, account_read_request()]
        if self.account is not None:
            raise ValueError('duplicate_account_response')
        self.account = project_account(message['result'])
        return []


def prepare_public_home():
    info = PROBE_HOME.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != 501 or stat.S_IMODE(info.st_mode) != 0o700:
        raise PermissionError('fresh_owned_probe_tmpfs_required')
    # Metadata of the one known mount only; never list or read credential state.
    auth = PROBE_HOME / 'auth.json'
    info = auth.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != 501
            or stat.S_IMODE(info.st_mode) != 0o600
            or not os.statvfs(auth).f_flag & os.ST_RDONLY):
        raise PermissionError('readonly_owned_auth_file_mount_required')
    for path in (USER_HOME, WORKSPACE):
        path.mkdir(mode=0o700)
    for name, text in [('config.toml', '# Fixed official account/read probe only.\n'),
        ('AGENTS.override.md', PUBLIC_POLICY)]:
        with os.fdopen(os.open(PROBE_HOME / name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o400), 'w') as stream:
            stream.write(text)


def run_probe():
    protocol = Protocol()
    count = 0
    error = None
    uncertain = False
    process = subprocess.Popen(arguments(), env=environment(), cwd=str(WORKSPACE),
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        start_new_session=True, close_fds=True)
    deadline = time.monotonic() + DEADLINE_SECONDS
    pending = bytearray()
    stdin_closed = False
    try:
        process.stdin.write(json.dumps(initialize_request()).encode() + b'\n')
        process.stdin.flush()
        with selectors.DefaultSelector() as selector:
            for stream, name in [(process.stdout, 'stdout'), (process.stderr, 'stderr')]:
                os.set_blocking(stream.fileno(), False)
                selector.register(stream, selectors.EVENT_READ, name)
            while selector.get_map():
                if time.monotonic() >= deadline:
                    raise TimeoutError('account_read_deadline')
                for key, _ in selector.select(0.05):
                    piece = os.read(key.fd, 4096)
                    if not piece:
                        selector.unregister(key.fileobj)
                        continue
                    count += len(piece)
                    if count > MAX_STREAM_BYTES:
                        raise ValueError('account_read_stream_bound')
                    if key.data == 'stderr':
                        continue
                    pending.extend(piece)
                    while b'\n' in pending:
                        raw, _, rest = pending.partition(b'\n')
                        pending[:] = rest
                        if len(raw) > MAX_LINE_BYTES:
                            raise ValueError('rpc_line_bound')
                        for outbound in protocol.accept(json.loads(raw)):
                            process.stdin.write(json.dumps(outbound).encode() + b'\n')
                            process.stdin.flush()
                        if protocol.account is not None and not stdin_closed:
                            process.stdin.close()
                            stdin_closed = True
                    if len(pending) > MAX_LINE_BYTES:
                        raise ValueError('rpc_line_bound')
            if pending or protocol.account is None:
                raise ValueError('incomplete_account_rpc')
            while os.waitid(os.P_PID, process.pid, os.WEXITED | os.WNOWAIT | os.WNOHANG) is None:
                if time.monotonic() >= deadline:
                    raise TimeoutError('account_read_exit_deadline')
                time.sleep(0.005)
    except Exception as failure:
        error = type(failure).__name__
    finally:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except OSError:
            uncertain = True
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            uncertain = True
        for stream in (process.stdin, process.stdout, process.stderr):
            if not stream.closed:
                stream.close()
        pending.clear()
    account = protocol.account or {'account_type': None, 'plan_type': None,
        'requires_openai_auth': None, 'allowed_personal_plan_observed': False}
    accepted = (account['allowed_personal_plan_observed'] and process.returncode == 0
        and error is None and not uncertain)
    return {'format': 'devbench.official-account-plan-read/1', **account,
        'accepted_account_plan_observation': accepted,
        'initialize_succeeded': protocol.initialized, 'account_read_succeeded': protocol.account is not None,
        'refresh_token_requested': False, 'official_cli_invocations_attempted': 1,
        'official_cli_launch_confirmed': True,
        'official_cli_exit_code': process.returncode, 'process_reaped': process.returncode is not None,
        'process_group_cleanup_uncertain': uncertain, 'error_category': error, 'stream_bytes': count,
        'raw_rpc_or_stderr_retained': False, 'authentication_cache_read_by_worker': False,
        'authentication_cache_hashed_by_worker': False, 'thread_requests': 0, 'turn_requests': 0,
        'live_model_calls': 0, 'native_admission_qualified': False,
        'live_generation_allowed': False, 'biological_execution_allowed': False}


def main():
    invocations = 0
    try:
        if len(sys.argv) != 1 or sys.platform != 'linux' or os.environ.get('DEVBENCH_ACCOUNT_READ_ONLY') != '1':
            raise PermissionError('reviewed_network_none_account_enclosure_required')
        digest = hashlib.sha256()
        with CLIENT.open('rb') as stream:
            for piece in iter(lambda: stream.read(1048576), b''):
                digest.update(piece)
        if digest.hexdigest() != CLIENT_SHA256:
            raise PermissionError('pinned_official_client_changed')
        prepare_public_home()
        invocations = 1
        result = run_probe()
    except Exception as failure:
        result = {'format': 'devbench.official-account-plan-read/1',
            'account_type': None, 'plan_type': None, 'requires_openai_auth': None,
            'allowed_personal_plan_observed': False, 'accepted_account_plan_observation': False,
            'initialize_succeeded': False, 'account_read_succeeded': False,
            'refresh_token_requested': False, 'official_cli_invocations_attempted': invocations,
            'official_cli_launch_confirmed': False, 'official_cli_exit_code': None,
            'process_reaped': False, 'process_group_cleanup_uncertain': False,
            'error_category': type(failure).__name__, 'stream_bytes': 0,
            'raw_rpc_or_stderr_retained': False, 'authentication_cache_read_by_worker': False,
            'authentication_cache_hashed_by_worker': False, 'thread_requests': 0, 'turn_requests': 0,
            'live_model_calls': 0, 'native_admission_qualified': False,
            'live_generation_allowed': False, 'biological_execution_allowed': False}
    print(json.dumps(result, sort_keys=True, allow_nan=False))
    return 0 if result['accepted_account_plan_observation'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
