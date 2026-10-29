"""Fixed one-shot owned Linux enclosure test; no Codex/model/biology execution."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import sys
import time

WORKSPACE = Path('/Users/vajraang/Documents/ChatGPT/bio-agent-harness')
sys.path.insert(0, str(WORKSPACE))
from devbench.analysis import codex_isolated_boundary as boundary
from devbench.analysis.codex_signedin import _run_process

ROOT = WORKSPACE / 'reports/rna-native-gate1/2026-10-21-v2/isolation'
PLAN = ROOT / 'control-plan-v1.json'
PLAN_SHA256 = 'ffc8aee8ed34c0bd320dd30824ba9a9e69d582d8ce1f677b7b0a1f5eb0b461de'
RESULT = ROOT / 'control-result-v1.json'
MODULE = WORKSPACE / 'devbench/analysis/codex_isolated_boundary.py'


def main():
    if RESULT.exists() or RESULT.is_symlink():
        raise FileExistsError('one_shot_diagnostic_result_exists')
    if hashlib.sha256(PLAN.read_bytes()).hexdigest() != PLAN_SHA256:
        raise PermissionError('reviewed_plan_changed')
    plan = json.loads(PLAN.read_text())
    fixtures = Path(plan['fixture_directory'])
    if hashlib.sha256(MODULE.read_bytes()).hexdigest() != plan['module_sha256']:
        raise PermissionError('reviewed_module_changed')
    if boundary.fixture_manifest(fixtures) != plan['fixtures']:
        raise PermissionError('owned_fixtures_changed')
    config = ROOT / 'docker-client-v1'
    if (config / 'config.json').read_bytes() != b'{"auths":{},"proxies":{}}\n':
        raise PermissionError('closed_docker_config_changed')
    prefix = [boundary.DOCKER, '--config', str(config), '--host', boundary.SOCKET]
    release = json.loads((ROOT / 'diagnostic-release-v1.json').read_text())
    if release.get('accepted') is not True:
        raise PermissionError('independent_release_missing')
    def verify_custody():
        for path, expected in release['source_sha256'].items():
            source = Path(path)
            if source.is_symlink() or hashlib.sha256(source.read_bytes()).hexdigest() != expected:
                raise PermissionError('reviewed_source_changed')
        if hashlib.sha256(PLAN.read_bytes()).hexdigest() != PLAN_SHA256:
            raise PermissionError('reviewed_plan_changed')
        if boundary.fixture_manifest(fixtures) != plan['fixtures']:
            raise PermissionError('owned_fixture_custody_changed')
        if (config / 'config.json').read_bytes() != b'{"auths":{},"proxies":{}}\n':
            raise PermissionError('closed_config_custody_changed')
    verify_custody()
    created = None
    result = {'format': 'devbench.isolated-enclosure-result/1', 'calls': [],
              'live_generation_allowed': False, 'biological_execution_allowed': False}
    def call(arguments):
        verify_custody()
        raw = _run_process(prefix + arguments, cwd=ROOT, environment=boundary.closed_environment(config),
            prompt=b'', deadline=time.monotonic() + 15, maximum=32768, authorize=lambda: None)
        result['calls'].append({'operation': arguments[0], 'exit_code': raw['exit_code'],
            'error': raw['error'], 'process_reaped': raw['process_reaped'],
            'stdout_bytes': len(raw['stdout']), 'stderr_bytes': len(raw['stderr'])})
        return raw
    def successful(raw):
        return raw['exit_code'] == 0 and raw['error'] is None and raw['process_reaped']
    try:
        args = boundary.create_arguments(fixtures, name='devharness-gate1-isolation-v1')
        if prefix + args != plan['docker_argv']:
            raise PermissionError('reviewed_command_changed')
        raw = call(args)
        value = raw['stdout'].decode().strip()
        if not successful(raw) or len(value) != 64 or any(c not in '0123456789abcdef' for c in value):
            raise RuntimeError('owned_container_create_failed')
        created = value
        raw = call(['container', 'inspect', created])
        if not successful(raw):
            raise RuntimeError('owned_container_inspect_failed')
        record = json.loads(raw['stdout'])[0]
        boundary.verify_container_record(record, fixtures, name='devharness-gate1-isolation-v1')
        if (record['Id'] != created or record['State'].get('Running') is not False
                or record['State'].get('Status') != 'created'):
            raise PermissionError('unexpected_created_container_state')
        result['inspected_controls_valid'] = True
        result['container_id'] = created
        raw = call(['start', '--attach', created])
        if not successful(raw):
            raise RuntimeError('owned_control_process_failed')
        observed = boundary.validate_control_output(json.loads(raw['stdout']))
        result['control_output'] = observed
        raw = call(['container', 'inspect', created])
        if not successful(raw):
            raise RuntimeError('owned_postrun_inspect_failed')
        record = json.loads(raw['stdout'])[0]
        boundary.verify_container_record(record, fixtures, name='devharness-gate1-isolation-v1')
        result['container_stopped'] = record['State'].get('Running') is False
        result['container_exit_code'] = record['State'].get('ExitCode')
        result['controls_passed'] = bool(observed.get('passed') and result['container_stopped'] and result['container_exit_code'] == 0)
    except Exception as error:
        result['failure'] = type(error).__name__ if type(error).__name__ in {'RuntimeError', 'PermissionError', 'ValueError', 'JSONDecodeError', 'KeyError', 'UnicodeDecodeError'} else 'unclassified_diagnostic_failure'
    finally:
        try:
            if created:
                raw = call(['container', 'rm', '--force', created])
                result['owned_container_removed'] = successful(raw) and raw['stdout'].decode().strip() == created
                raw = call(['container', 'inspect', created])
                result['removal_verified'] = (raw['exit_code'] == 1 and raw['error'] is None
                    and raw['process_reaped'] and b'No such container' in raw['stderr'])
            elif result['calls']:
                result['cleanup_uncertain'] = True
            result['fixture_custody_unchanged'] = boundary.fixture_manifest(fixtures) == plan['fixtures']
            result['host_sentinel_custody_unchanged'] = hashlib.sha256(Path(plan['host_sentinel']).read_bytes()).hexdigest() == plan['host_sentinel_sha256']
        except Exception:
            result['cleanup_or_custody_uncertain'] = True
            result['fixture_custody_unchanged'] = False
            result['host_sentinel_custody_unchanged'] = False
        result['diagnostic_only'] = True
        with os.fdopen(os.open(RESULT, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600), 'w') as stream:
            stream.write(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, sort_keys=True))
    return 0 if (result.get('controls_passed') and result.get('owned_container_removed')
        and result.get('removal_verified') and result['fixture_custody_unchanged']
        and result['host_sentinel_custody_unchanged']) else 1


if __name__ == '__main__':
    raise SystemExit(main())
