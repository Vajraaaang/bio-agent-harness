"""Prepare a fixed official device-login operation; this script cannot launch it.

The public auth-only egress mechanism and CLI proxy compatibility must be
independently qualified before a separate released supervisor may use this plan.
No authentication cache, host credentials, model, signer or private state is read.
"""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import re

WORKSPACE = Path('/Users/vajraang/Documents/ChatGPT/bio-agent-harness')
REPORT = WORKSPACE / 'reports/rna-native-gate1/2026-10-21-v2/auth-preparation'
CLIENT_ROOT = REPORT.parent / 'client'
PRIVATE_ROOT = WORKSPACE / '.devbench-codex-auth/epoch1'
CLIENT_SHA256 = 'cb0a15567e9a60a5820d54b0f6ae86d504dc3805c1eab21a47f70e3eb7b73a40'
SOURCE_PINS = {
    'public-device-auth-0.147.0.rs': '78bfe08f18fa89c84b261c5e11269239a06bb60cb0a964fbe8532ccede4ba328',
    'public-login-server-0.147.0.rs': 'c93e0bda0d9ed7003e3935982c2432303dcadd5714ef5721394f4aea57d83202'}
VERIFICATION_URL = 'https://auth.openai.com/codex/device'
PUBLIC_POLICY = (b'Only the fixed official ChatGPT device-authorization login is authorized in this environment.\n'
    b'Do not generate workflows, infer scientific findings, execute biological analysis, bootstrap signing keys, '
    b'use API keys, copy host authentication or authorize production admission.\n')


def login_arguments():
    """Exact login parser: exec-only clean-config flags are deliberately absent."""
    return ['/client/bin/codex', '--strict-config', '-c', 'cli_auth_credentials_store="file"',
        '-c', 'forced_login_method="chatgpt"', 'login', '--device-auth']


def login_environment(proxy_url):
    """Construct only public transport settings; never merge ambient environment."""
    if type(proxy_url) is not str or re.fullmatch(r'http://(?:[a-z][a-z0-9-]{0,62}|(?:[0-9]{1,3}\.){3}[0-9]{1,3}):[0-9]{1,5}', proxy_url) is None:
        raise PermissionError('qualified_internal_auth_proxy_url_required')
    # This constructor does not qualify the supplied proxy or authorize launch.
    return {'PATH': '/client/bin:/client/codex-path:/usr/bin:/bin', 'HOME': '/private/home',
        'CODEX_HOME': '/private/codex-home', 'LANG': 'C.UTF-8', 'LC_ALL': 'C.UTF-8',
        'HTTPS_PROXY': proxy_url, 'HTTP_PROXY': proxy_url, 'NO_PROXY': '', 'RUST_LOG': 'off'}


def extract_device_prompt(raw):
    """In-memory user-only display values; caller must not log/write this result.

    Reject arbitrary auth/error output rather than showing raw streams. No digest
    of raw streams or this short-lived challenge belongs in reports or telemetry.
    """
    if type(raw) is not bytes or len(raw) > 8192:
        raise ValueError('bounded_official_device_prompt_required')
    try: value = raw.decode('utf-8')
    except UnicodeDecodeError: raise ValueError('official_device_prompt_encoding') from None
    value = re.sub(r'\x1b\[[0-9;]*m', '', value)
    if any(ord(c) < 32 and c not in '\n\r\t' for c in value):
        raise ValueError('unexpected_terminal_control')
    pattern = (r'1\. Open this link in your browser and sign in to your account\s+'
        + re.escape(VERIFICATION_URL) + r'\s+2\. Enter this one-time code '
        + r'\(expires in 15 minutes\)\s+([A-Z0-9-]{4,32})\s')
    matches = re.findall(pattern, value)
    if len(matches) != 1 or not any(c.isalnum() for c in matches[0]):
        raise ValueError('fixed_official_device_prompt_not_found')
    return {'verification_url': VERIFICATION_URL, 'user_code': matches[0]}


def prepare_plan():
    for name, expected in SOURCE_PINS.items():
        source = CLIENT_ROOT / name
        if source.is_symlink() or hashlib.sha256(source.read_bytes()).hexdigest() != expected:
            raise PermissionError('official_public_auth_source_changed')
    return {'format': 'devbench.personal-device-auth-preparation/1',
        'operation': 'official_chatgpt_device_auth_login_only', 'launch_allowed': False,
        'launch_blockers': ['auth_only_public_egress_profile_not_qualified', 'actual_cli_proxy_compatibility_not_qualified',
            'independent_exact_supervisor_release_not_present', 'human_device_confirmation_not_completed'],
        'client': {'version': '0.147.0', 'package': str(CLIENT_ROOT / 'package'),
            'native_sha256': CLIENT_SHA256, 'public_source_sha256': SOURCE_PINS},
        'arguments': login_arguments(), 'private_root': str(PRIVATE_ROOT),
        'private_root_gitignored': True, 'private_root_created_by_preparation': False,
        'private_state_rules': ['fresh_owned_non_symlink_directories_0700', 'file_credentials_store_client_owned',
            'no_host_authentication_copy_or_read', 'no_authentication_cache_read_or_hash',
            'exclude_entire_private_root_from_reports_indexes_traces_and_git', 'preserve_successful_login_cache_private'],
        'public_owned_policy': {'filename': 'AGENTS.override.md', 'sha256': hashlib.sha256(PUBLIC_POLICY).hexdigest(),
            'bytes': len(PUBLIC_POLICY)},
        'transport': {'allowed_authority': 'auth.openai.com:443', 'tls': 'official_end_to_end_no_interception',
            'device_usercode_path': '/api/accounts/deviceauth/usercode', 'device_poll_path': '/api/accounts/deviceauth/token',
            'token_exchange_path': '/oauth/token', 'opaque_connect_cannot_restrict_paths': True,
            'model_hosts_allowed': False, 'qualified_profile_binding': None},
        'interaction': {'verification_url': VERIFICATION_URL, 'device_code': 'not_requested',
            'display': 'user_only_ephemeral_terminal_or_ui_no_report_or_telemetry',
            'browser_confirmation': 'human_in_personal_browser_outside_container', 'raw_auth_stream_retention': False,
            'raw_auth_stream_hash_retention': False},
        'required_supervision': {'deadline_seconds': 960, 'combined_pipe_bytes': 65536,
            'stdin': 'empty', 'process_group_reap_and_exact_container_cleanup': True,
            'outer_enclosure_and_auth_relay': 'must_bind_exact_independently_qualified_profile_before_launch',
            'coordinator_retries': 0, 'official_same_flow_polling_max_seconds': 900},
        'login_result': 'not_started', 'live_model_calls': 0, 'live_generation_allowed': False,
        'biological_execution_allowed': False, 'production_native_admission_qualified': False,
        'generation_grant_created': False, 'signer_bootstrapped': False}


def main():
    REPORT.mkdir(mode=0o700, exist_ok=True)
    target = REPORT / 'device-auth-plan-v1.json'
    plan = prepare_plan()
    with os.fdopen(os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600), 'w') as stream:
        stream.write(json.dumps(plan, indent=2, allow_nan=False) + '\n')
    print(json.dumps({'plan': str(target), 'launch_allowed': False, 'private_root_created': False,
        'auth_requests': 0, 'live_model_calls': 0}))


if __name__ == '__main__':
    raise SystemExit(main())
