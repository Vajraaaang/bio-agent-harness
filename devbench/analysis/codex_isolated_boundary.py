"""Fixed credential-free enclosure diagnostics; never a production launcher.

This tests actual Linux enclosure behavior without invoking a model or reading
host configuration. Production signed-in admission remains in codex_signedin
and rejects all unqualified acceptance objects, including these diagnostics.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import stat

IMAGE = 'sha256:c34c4ecaf9f1ef909b0c2478f5643530e9dec02c9db9cb8d05f406b9208d8b0e'
SOCKET = 'unix:///Users/vajraang/.docker/run/docker.sock'
DOCKER = '/Applications/Docker.app/Contents/Resources/bin/docker'
HOST_SENTINEL = '/Users/vajraang/Documents/ChatGPT/bio-agent-harness/reports/rna-native-gate1/2026-10-21-v2/isolation/owned-host-sentinel.txt'

# No subprocess, dynamic code, external request, auth cache or host discovery.
CONTROL = '''import errno,json,os,socket
from pathlib import Path
r={}
r['nonroot']=os.getuid()==65532
r['positive_read']=Path('/fixture/positive.txt').read_bytes()==b'owned-gate1-positive\\n'
for name,path in [('readonly_fixture','/fixture/forbidden-write'),('readonly_root','/gate1-forbidden-write')]:
    try:
        Path(path).write_bytes(b'negative-control')
        r[name]=False
    except OSError as e:
        r[name]=e.errno in (errno.EROFS,errno.EACCES)
for name,path in [('host_sentinel_unmounted','/Users/vajraang/Documents/ChatGPT/bio-agent-harness/reports/rna-native-gate1/2026-10-21-v2/isolation/owned-host-sentinel.txt'),('symlink_escape','/fixture/outside-link'),('host_socket_absent','/var/run/docker.sock')]:
    try:
        Path(path).read_bytes()
        r[name]=False
    except OSError as e:
        r[name]=e.errno in (errno.ENOENT,errno.EACCES)
for name,address in [('network_denied',('198.51.100.1',9))]:
    s=socket.socket();s.settimeout(.2)
    try:
        s.connect(address);r[name]=False
    except OSError as e:
        r[name]=e.errno in (errno.ENETUNREACH,errno.EHOSTUNREACH,errno.EACCES)
    finally:s.close()
home=Path('/tmp/home');home.mkdir(mode=0o700)
r['empty_home']=not list(home.iterdir())
r['explicit_home']=os.environ.get('HOME')==str(home)
r['explicit_codex_home']=os.environ.get('CODEX_HOME')=='/tmp/home/.codex'
p=home/'positive-write';p.write_bytes(b'owned');r['scratch_write']=p.read_bytes()==b'owned';p.unlink()
try:
    Path('/fixture/permission-canary.txt').read_bytes();r['owned_permission_denied']=False
except OSError as e:r['owned_permission_denied']=e.errno==errno.EACCES
r['only_loopback']=sorted(p.name for p in Path('/sys/class/net').iterdir())==['lo']
r['live_generation_allowed']=False
r['biological_execution_allowed']=False
print(json.dumps({'format':'devbench.isolated-enclosure-controls/1','observations':r,'passed':all(v for k,v in r.items() if not k.endswith('_allowed'))},sort_keys=True))
'''


def _directory(value):
    path = Path(value)
    if (not path.is_absolute() or not path.is_dir() or path.resolve() != path
            or any(c in str(path) for c in ',\n\r\x00')):
        raise ValueError('Existing absolute non-symlink owned directory required')
    return path


def closed_environment(home):
    """For Docker transport only: no inherited credentials, config or proxies."""
    return {'PATH': '/usr/bin:/bin', 'HOME': str(_directory(home)), 'LANG': 'C'}


def prepare_fixtures(directory):
    path = Path(directory)
    if not path.is_absolute() or path.parent.resolve() != path.parent:
        raise ValueError('Absolute owned fixture path required')
    path.mkdir(mode=0o755, exist_ok=False)
    for name, content in {'control.py': CONTROL.encode(), 'positive.txt': b'owned-gate1-positive\n'}.items():
        target = path / name
        target.write_bytes(content)
        target.chmod(0o444)
    canary = path / 'permission-canary.txt'
    canary.write_bytes(b'owned-permission-negative\n')
    canary.chmod(0o600)
    (path / 'outside-link').symlink_to(HOST_SENTINEL)
    return fixture_manifest(path)


def fixture_manifest(directory):
    path = _directory(directory)
    expected = {'control.py': 0o444, 'positive.txt': 0o444, 'permission-canary.txt': 0o600, 'outside-link': None}
    if {p.name for p in path.iterdir()} != set(expected):
        raise PermissionError('Unexpected owned fixture entries')
    result = {}
    for name, mode in expected.items():
        p = path / name
        meta = p.lstat()
        if meta.st_uid != os.geteuid() or (name == 'permission-canary.txt' and meta.st_uid == 65532):
            raise PermissionError('Fixture owner mismatch')
        if mode is None:
            if not stat.S_ISLNK(meta.st_mode) or str(p.readlink()) != HOST_SENTINEL:
                raise PermissionError('Fixture symlink changed')
            result[name] = {'symlink_target': str(p.readlink()), 'uid': meta.st_uid}
        else:
            if not stat.S_ISREG(meta.st_mode) or stat.S_IMODE(meta.st_mode) != mode or meta.st_size > 16384:
                raise PermissionError('Fixture type, mode or bound mismatch')
            result[name] = {'sha256': hashlib.sha256(p.read_bytes()).hexdigest(), 'size': meta.st_size,
                            'mode': mode, 'uid': meta.st_uid}
    return result


def create_arguments(fixtures, *, name):
    """The command is fixed; callers cannot insert a model, shell or auth mount."""
    if not isinstance(name, str) or re.fullmatch(r'devharness-gate1-[a-z0-9][a-z0-9-]{0,63}', name) is None:
        raise ValueError('Owned Gate 1 diagnostic name required')
    fixtures = _directory(fixtures)
    return ['create', '--pull=never', '--name', name, '--platform', 'linux/amd64',
            '--network', 'none', '--read-only', '--cap-drop', 'ALL',
            '--security-opt', 'no-new-privileges=true', '--pids-limit', '32',
            '--memory', '536870912', '--memory-swap', '536870912', '--cpus', '1',
            '--user', '65532:65532', '--ipc', 'private', '--log-driver', 'none',
            '--tmpfs', '/tmp:rw,nosuid,nodev,noexec,size=16777216,mode=1777',
            '--mount', f'type=bind,src={fixtures},dst=/fixture,readonly',
            '--entrypoint', '/usr/bin/env', IMAGE, '-i', 'HOME=/tmp/home',
            'CODEX_HOME=/tmp/home/.codex', 'PATH=/usr/bin:/bin:/usr/local/bin',
            'LANG=C', 'TMPDIR=/tmp', '/usr/bin/timeout', '--signal=KILL', '10s',
            '/usr/bin/python3', '-I', '-B', '/fixture/control.py']


def require_generation_qualification(evidence):
    """No diagnostic observation can authorize production execution."""
    raise PermissionError('diagnostic_only:no_signedin_generation_qualification')


def verify_container_record(record, fixtures, *, name):
    """Check actual daemon state before starting this one fixed diagnostic."""
    from devharness_controlled.native_runtime import IMAGE_ENV
    args = create_arguments(fixtures, name=name)
    image_index = args.index(IMAGE)
    config, host = record.get('Config', {}), record.get('HostConfig', {})
    expected = {'NetworkMode': 'none', 'ReadonlyRootfs': True, 'Privileged': False,
                'Memory': 536870912, 'MemorySwap': 536870912, 'NanoCpus': 1000000000,
                'PidsLimit': 32, 'PidMode': '', 'IpcMode': 'private'}
    valid = (record.get('Name') == '/' + name and record.get('Image') == IMAGE
        and re.fullmatch(r'[0-9a-f]{64}', record.get('Id', '')) is not None
        and config.get('User') == '65532:65532'
        and config.get('Entrypoint') == ['/usr/bin/env']
        and config.get('Cmd') == args[image_index + 1:]
        and type(config.get('Env')) is list and len(config['Env']) == len(IMAGE_ENV)
        and set(config['Env']) == set(IMAGE_ENV)
        and all(host.get(k) == v and type(host.get(k)) is type(v) for k, v in expected.items())
        and host.get('CapDrop') == ['ALL']
        and host.get('SecurityOpt') == ['no-new-privileges=true']
        and host.get('LogConfig') == {'Type': 'none', 'Config': {}}
        and host.get('Tmpfs') == {'/tmp': 'rw,nosuid,nodev,noexec,size=16777216,mode=1777'}
        and all(not host.get(k) for k in ('CapAdd', 'Binds', 'Devices', 'DeviceRequests', 'PortBindings', 'VolumesFrom'))
        and host.get('PublishAllPorts') is False)
    mounts = record.get('Mounts', [])
    valid = valid and len(mounts) == 1
    if valid:
        m = mounts[0]
        valid = (m.get('Type') == 'bind' and m.get('Source') == str(_directory(fixtures))
                 and m.get('Destination') == '/fixture' and m.get('RW') is False)
    if not valid:
        raise PermissionError('isolated_diagnostic_container_identity_or_controls_mismatch')
    return True


def validate_control_output(value):
    expected = {'nonroot', 'positive_read', 'readonly_fixture', 'readonly_root', 'host_sentinel_unmounted',
        'symlink_escape', 'host_socket_absent', 'network_denied', 'empty_home', 'explicit_home',
        'explicit_codex_home', 'scratch_write', 'owned_permission_denied', 'only_loopback',
        'live_generation_allowed', 'biological_execution_allowed'}
    if (type(value) is not dict or set(value) != {'format', 'observations', 'passed'}
        or value['format'] != 'devbench.isolated-enclosure-controls/1'
        or type(value['passed']) is not bool or type(value['observations']) is not dict
        or set(value['observations']) != expected
        or any(type(v) is not bool for v in value['observations'].values())
        or value['observations']['live_generation_allowed'] is not False
        or value['observations']['biological_execution_allowed'] is not False):
        raise PermissionError('unexpected_control_response')
    computed = all(v for k, v in value['observations'].items() if not k.endswith('_allowed'))
    if value['passed'] is not computed:
        raise PermissionError('inconsistent_control_response')
    return value
