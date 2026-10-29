"""Fixed official-client/local-fixture diagnostic, never a live model launcher."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import time

WORKSPACE = Path('/Users/vajraang/Documents/ChatGPT/bio-agent-harness')
sys.path.insert(0, str(WORKSPACE))
from devbench.analysis.codex_isolated_boundary import DOCKER, IMAGE, SOCKET, closed_environment
from devbench.analysis.codex_signedin import _run_process
from devharness_controlled.native_runtime import IMAGE_ENV
ROOT = WORKSPACE / 'reports/rna-native-gate1/2026-10-21-v2/isolation/protocol-v1'
CLIENT = ROOT.parent.parent / 'client/package'
MANIFEST = ROOT.parent.parent / 'client/package-manifest.json'
NAME = 'devharness-gate1-protocol-v1'
UID, GID = 501, 20
COMMAND = ['-i', 'PATH=/client/bin:/usr/bin:/bin', 'HOME=/output', 'CODEX_HOME=/output/codex-home',
    'LANG=C.UTF-8', 'DEVBENCH_LOCAL_PROTOCOL_ONLY=1', '/usr/bin/timeout', '--signal=KILL', '45s',
    '/usr/bin/python3', '-I', '-B', '/input/protocol.py']


def digest(path):
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 300000000:
        raise PermissionError('ordinary_bounded_file_required')
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1048576), b''): h.update(chunk)
    return h.hexdigest()


def arguments():
    args = ['create', '--pull=never', '--name', NAME, '--platform', 'linux/amd64', '--network', 'none',
        '--read-only', '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges=true', '--pids-limit', '64',
        '--memory', '1073741824', '--memory-swap', '1073741824', '--cpus', '1', '--user', f'{UID}:{GID}',
        '--ipc', 'private', '--log-driver', 'none', '--tmpfs', '/tmp:rw,nosuid,nodev,noexec,size=67108864,mode=1777']
    for source, destination, readonly in [(CLIENT, '/client', True), (ROOT / 'input', '/input', True), (ROOT / 'output', '/output', False)]:
        args.extend(['--mount', f'type=bind,src={source},dst={destination}' + (',readonly' if readonly else '')])
    return [*args, '--entrypoint', '/usr/bin/env', IMAGE, *COMMAND]


def verify_record(record):
    c, h = record.get('Config', {}), record.get('HostConfig', {})
    expected = {'NetworkMode': 'none', 'ReadonlyRootfs': True, 'Privileged': False, 'Memory': 1073741824,
        'MemorySwap': 1073741824, 'NanoCpus': 1000000000, 'PidsLimit': 64, 'PidMode': '', 'IpcMode': 'private',
        'PublishAllPorts': False}
    valid = (record.get('Name') == '/' + NAME and record.get('Image') == IMAGE
        and re.fullmatch(r'[0-9a-f]{64}', record.get('Id', '')) is not None
        and c.get('User') == f'{UID}:{GID}' and c.get('Entrypoint') == ['/usr/bin/env'] and c.get('Cmd') == COMMAND
        and type(c.get('Env')) is list and len(c['Env']) == len(IMAGE_ENV) and set(c['Env']) == set(IMAGE_ENV)
        and all(h.get(k) == v and type(h.get(k)) is type(v) for k,v in expected.items())
        and h.get('CapDrop') == ['ALL'] and h.get('SecurityOpt') == ['no-new-privileges=true']
        and h.get('LogConfig') == {'Type': 'none', 'Config': {}}
        and h.get('Tmpfs') == {'/tmp': 'rw,nosuid,nodev,noexec,size=67108864,mode=1777'}
        and all(not h.get(k) for k in ('CapAdd','Binds','Devices','DeviceRequests','PortBindings','VolumesFrom')))
    mounts = record.get('Mounts', [])
    valid = valid and len(mounts) == 3
    for source, destination, rw in [(CLIENT, '/client', False), (ROOT / 'input', '/input', False), (ROOT / 'output', '/output', True)]:
        found = [m for m in mounts if m.get('Destination') == destination]
        valid = valid and len(found) == 1 and found[0].get('Source') == str(source) and found[0].get('RW') is rw and found[0].get('Type') == 'bind'
    if not valid: raise PermissionError('actual_protocol_enclosure_mismatch')


def verify_custody(release):
    for directory in (ROOT, ROOT/'input', ROOT/'output', ROOT/'docker-client', CLIENT):
        if directory.is_symlink() or not directory.is_dir() or directory.resolve() != directory:
            raise PermissionError('ordinary_absolute_mount_directories_required')
    for path, expected in release['source_sha256'].items():
        if digest(Path(path)) != expected: raise PermissionError('reviewed_protocol_source_changed')
    expected_members = {item['name'] for item in json.loads(MANIFEST.read_text())['files']}
    actual_members = {str(p.relative_to(CLIENT)) for p in CLIENT.rglob('*')}
    if actual_members != expected_members: raise PermissionError('official_package_members_changed')
    for item in json.loads(MANIFEST.read_text())['files']:
        p = CLIENT / item['name']
        if p.is_symlink() or (item['type'] == 'directory' and not p.is_dir()): raise PermissionError('official_package_type_changed')
        if item['type'] == 'file' and (p.stat().st_size != item['bytes'] or digest(p) != item['sha256']): raise PermissionError('official_package_changed')
    config = ROOT / 'docker-client/config.json'
    if config.is_symlink() or not config.is_file() or {p.name for p in config.parent.iterdir()} != {'config.json'}:
        raise PermissionError('ordinary_closed_config_required')
    if config.read_bytes() != b'{"auths":{},"proxies":{}}\n': raise PermissionError('closed_transport_config_changed')
    output = ROOT / 'output'
    if output.is_symlink() or output.stat().st_uid != UID or stat.S_IMODE(output.stat().st_mode) != 0o700:
        raise PermissionError('owned_private_output_required')
    if {p.name for p in (ROOT / 'input').iterdir()} != {'protocol.py'}: raise PermissionError('fixed_protocol_fixture_only')


def main():
    target = ROOT / 'result.json'
    if target.exists() or target.is_symlink(): raise FileExistsError('one_shot_protocol_result_exists')
    release = json.loads((ROOT / 'release.json').read_text())
    if release.get('accepted') is not True: raise PermissionError('independent_protocol_release_required')
    verify_custody(release)
    if list((ROOT / 'output').iterdir()): raise PermissionError('fresh_output_required')
    prefix = [DOCKER, '--config', str(ROOT / 'docker-client'), '--host', SOCKET]
    result = {'format': 'devbench.isolated-codex-protocol-supervisor/1', 'calls': [],
        'live_generation_allowed': False, 'biological_execution_allowed': False,
        'native_admission_qualified': False, 'signedin_authentication_qualified': False}
    created = None
    def call(args):
        verify_custody(release)
        raw = _run_process(prefix+args, cwd=ROOT, environment=closed_environment(ROOT/'docker-client'), prompt=b'',
            deadline=time.monotonic()+50, maximum=262144, authorize=lambda: None)
        result['calls'].append({'operation': args[0], 'exit_code': raw['exit_code'], 'error': raw['error'],
            'process_reaped': raw['process_reaped'], 'stdout_bytes': len(raw['stdout']), 'stderr_bytes': len(raw['stderr'])})
        return raw
    def ok(raw): return raw['exit_code']==0 and raw['error'] is None and raw['process_reaped']
    try:
        raw = call(arguments()); candidate = raw['stdout'].decode().strip()
        if not ok(raw) or re.fullmatch(r'[0-9a-f]{64}', candidate) is None: raise RuntimeError('create_failed')
        created = candidate; result['container_id'] = created
        raw = call(['container','inspect',created])
        if not ok(raw): raise RuntimeError('inspect_failed')
        record = json.loads(raw['stdout'])[0]; verify_record(record)
        if record['Id']!=created or record['State'].get('Status')!='created' or record['State'].get('Running') is not False: raise PermissionError('created_state_mismatch')
        result['actual_enclosure_verified'] = True
        raw = call(['start','--attach',created])
        # Child failure is retained through its owned JSON result, not reclassified as success.
        result['attached_exit_code'] = raw['exit_code']
        result['attached_reaped'] = raw['process_reaped']
        result['attached_launch_confirmed'] = raw['launch_confirmed']
        result['attached_error'] = raw['error']
        raw = call(['container','inspect',created])
        if not ok(raw): raise RuntimeError('postrun_inspect_failed')
        record = json.loads(raw['stdout'])[0]; verify_record(record)
        result['container_stopped'] = record['State'].get('Running') is False
        result['container_exit_code'] = record['State'].get('ExitCode')
        observed = ROOT/'output/protocol/result.json'
        result['fixture_result_present'] = observed.is_file() and not observed.is_symlink()
        if result['fixture_result_present']:
            if observed.stat().st_size > 1048576: raise PermissionError('fixture_result_bound')
            result['fixture_result_sha256'] = digest(observed)
    except Exception as error:
        result['error_category'] = type(error).__name__ if type(error).__name__ in {'RuntimeError','PermissionError','ValueError','JSONDecodeError','OSError','KeyError'} else 'unknown_protocol_failure'
    finally:
        try:
            if created:
                raw=call(['container','rm','--force',created]);result['owned_container_removed']=ok(raw) and raw['stdout'].decode().strip()==created
                raw=call(['container','inspect',created]);result['removal_verified']=raw['exit_code']==1 and raw['error'] is None and raw['process_reaped'] and b'No such container' in raw['stderr']
            elif result['calls']: result['cleanup_uncertain']=True
            verify_custody(release);result['source_custody_unchanged']=True
        except Exception: result['cleanup_or_custody_uncertain']=True
        with os.fdopen(os.open(target,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600),'w') as stream:
            stream.write(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,sort_keys=True))
    complete = (result.get('attached_exit_code') == 0 and result.get('container_exit_code') == 0
        and result.get('attached_reaped') is True and result.get('attached_launch_confirmed') is True
        and result.get('attached_error') is None and result.get('fixture_result_present') is True
        and result.get('container_stopped') is True and result.get('owned_container_removed') is True
        and result.get('removal_verified') is True and result.get('source_custody_unchanged') is True
        and not any(result.get(k) for k in ('error_category','cleanup_uncertain','cleanup_or_custody_uncertain')))
    return 0 if complete else 1


if __name__=='__main__': raise SystemExit(main())
