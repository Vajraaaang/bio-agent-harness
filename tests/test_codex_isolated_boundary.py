"""The diagnostic enclosure never accepts a generation command or credential mount."""
import importlib.util
from pathlib import Path
import pytest

MODULE = Path('/Users/vajraang/Documents/ChatGPT/bio-agent-harness/devbench/analysis/codex_isolated_boundary.py')

def load():
    spec = importlib.util.spec_from_file_location('isolated_boundary_under_test', MODULE)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def test_fixed_controls_and_environment_ignore_ambient_credentials(tmp_path, monkeypatch):
    mod = load()
    monkeypatch.setenv('DOCKER_HOST', 'tcp://remote:2375')
    monkeypatch.setenv('OPENAI_API_KEY', 'untrusted-secret')
    monkeypatch.setenv('HTTP_PROXY', 'http://untrusted-secret')
    fixtures = tmp_path / 'fixtures'; fixtures.mkdir()
    argv = mod.create_arguments(fixtures, name='devharness-gate1-owned')
    assert '--pull=never' in argv
    for pair in [('--network', 'none'), ('--cap-drop', 'ALL'), ('--security-opt', 'no-new-privileges=true'), ('--user', '65532:65532')]:
        assert argv[argv.index(pair[0]) + 1] == pair[1]
    assert '--read-only' in argv
    assert '--privileged' not in argv
    assert 'tcp://remote:2375' not in str(argv)
    assert mod.closed_environment(tmp_path) == {'PATH': '/usr/bin:/bin', 'HOME': str(tmp_path), 'LANG': 'C'}
    assert argv[-1] == '/fixture/control.py'
    assert 'exec' not in argv


@pytest.mark.parametrize('bad_name', ['other-owned', 'devharness-gate1-X', 'devharness-gate1-;echo', 'devharness-gate1-'])
def test_unowned_names_rejected(tmp_path, bad_name):
    with pytest.raises(ValueError): load().create_arguments(tmp_path, name=bad_name)


def test_symlink_and_nonabsolute_mounts_rejected(tmp_path):
    mod = load(); target = tmp_path / 'target'; target.mkdir()
    link = tmp_path / 'link'; link.symlink_to(target)
    for path in [link, Path('relative'), tmp_path / 'missing']:
        with pytest.raises(ValueError): mod.create_arguments(path, name='devharness-gate1-owned')


def test_unqualified_evidence_cannot_become_generation_acceptance():
    with pytest.raises(PermissionError, match='diagnostic_only'):
        load().require_generation_qualification({'controls_passed': True, 'authentication': 'chatgpt_signin'})


def test_probe_is_fixed_and_avoids_host_sources(tmp_path):
    mod = load(); fixtures = tmp_path / 'fixture'
    manifest = mod.prepare_fixtures(fixtures)
    assert set(manifest) == {'control.py', 'positive.txt', 'outside-link', 'permission-canary.txt'}
    assert (fixtures / 'outside-link').readlink() == Path(mod.HOST_SENTINEL)
    content = (fixtures / 'control.py').read_text()
    assert 'subprocess' not in content and 'OPENAI_API_KEY' not in content
    assert '198.51.100.1' in content
    with pytest.raises(FileExistsError): mod.prepare_fixtures(fixtures)


def test_fixture_permission_and_additional_file_changes_fail_closed(tmp_path):
    mod = load(); fixtures = tmp_path / 'fixture'
    mod.prepare_fixtures(fixtures)
    canary = fixtures / 'permission-canary.txt'
    canary.chmod(0o644)
    with pytest.raises(PermissionError, match='mode'): mod.fixture_manifest(fixtures)
    canary.chmod(0o600)
    (fixtures / 'extra').write_text('unexpected')
    with pytest.raises(PermissionError, match='entries'): mod.fixture_manifest(fixtures)


def record_fixture(mod, fixtures):
    from devharness_controlled.native_runtime import IMAGE_ENV
    args = mod.create_arguments(fixtures, name='devharness-gate1-owned')
    return {'Id': 'a'*64, 'Name': '/devharness-gate1-owned', 'Image': mod.IMAGE,
        'Config': {'User': '65532:65532', 'Entrypoint': ['/usr/bin/env'],
                   'Cmd': args[args.index(mod.IMAGE)+1:], 'Env': list(IMAGE_ENV)},
        'HostConfig': {'NetworkMode': 'none', 'ReadonlyRootfs': True, 'Privileged': False,
            'Memory': 536870912, 'MemorySwap': 536870912, 'NanoCpus': 1000000000,
            'PidsLimit': 32, 'PidMode': '', 'IpcMode': 'private', 'CapDrop': ['ALL'],
            'SecurityOpt': ['no-new-privileges=true'], 'LogConfig': {'Type': 'none', 'Config': {}},
            'Tmpfs': {'/tmp': 'rw,nosuid,nodev,noexec,size=16777216,mode=1777'}, 'PublishAllPorts': False},
        'Mounts': [{'Type': 'bind', 'Source': str(fixtures), 'Destination': '/fixture', 'RW': False}]}


def test_actual_daemon_record_verifier_rejects_broadened_controls(tmp_path):
    from copy import deepcopy
    mod = load(); record = record_fixture(mod, tmp_path)
    assert mod.verify_container_record(record, tmp_path, name='devharness-gate1-owned')
    altered = []
    for key, value in [('NetworkMode', 'bridge'), ('ReadonlyRootfs', False), ('Privileged', True), ('Memory', 0),
                       ('CapAdd', ['SYS_ADMIN']), ('SecurityOpt', []), ('PidsLimit', 0), ('PidMode', 'host')]:
        copy = deepcopy(record); copy['HostConfig'][key] = value; altered.append(copy)
    copy = deepcopy(record); copy['Config']['Env'].append('OPENAI_API_KEY=untrusted'); altered.append(copy)
    copy = deepcopy(record); copy['Config']['Cmd'].append('exec'); altered.append(copy)
    copy = deepcopy(record); copy['Mounts'][0]['RW'] = True; altered.append(copy)
    copy = deepcopy(record); copy['Mounts'].append({'Source': '/host', 'Destination': '/host'}); altered.append(copy)
    for copy in altered:
        with pytest.raises(PermissionError): mod.verify_container_record(copy, tmp_path, name='devharness-gate1-owned')


def test_control_output_rejects_unexpected_fields_truthy_values_and_self_report():
    mod = load()
    keys = {'nonroot', 'positive_read', 'readonly_fixture', 'readonly_root', 'host_sentinel_unmounted',
        'symlink_escape', 'host_socket_absent', 'network_denied', 'empty_home', 'explicit_home',
        'explicit_codex_home', 'scratch_write', 'owned_permission_denied', 'only_loopback'}
    value = {'format': 'devbench.isolated-enclosure-controls/1', 'passed': True,
        'observations': {**dict.fromkeys(keys, True), 'live_generation_allowed': False, 'biological_execution_allowed': False}}
    assert mod.validate_control_output(value) == value
    from copy import deepcopy
    invalid = []
    v = deepcopy(value); v['extra'] = 'unapproved'; invalid.append(v)
    v = deepcopy(value); v['passed'] = 1; invalid.append(v)
    v = deepcopy(value); v['observations']['nonroot'] = False; invalid.append(v)
    v = deepcopy(value); v['observations']['live_generation_allowed'] = True; invalid.append(v)
    for v in invalid:
        with pytest.raises(PermissionError): mod.validate_control_output(v)


@pytest.mark.parametrize('failure', ['create', 'inspect'])
def test_runner_never_starts_on_create_or_inspect_failure(tmp_path, monkeypatch, failure, capsys):
    import hashlib, json
    runner_path = Path('/Users/vajraang/Documents/ChatGPT/bio-agent-harness/scripts/run_isolated_boundary_diagnostic.py')
    spec = importlib.util.spec_from_file_location('isolated_runner_under_test', runner_path)
    runner = importlib.util.module_from_spec(spec); spec.loader.exec_module(runner)
    mod = runner.boundary
    fixtures = tmp_path / 'fixtures'; manifest = mod.prepare_fixtures(fixtures)
    config = tmp_path / 'docker-client-v1'; config.mkdir(); (config / 'config.json').write_bytes(b'{"auths":{},"proxies":{}}\n')
    sentinel = tmp_path / 'sentinel'; sentinel.write_bytes(b'owned')
    prefix = [mod.DOCKER, '--config', str(config), '--host', mod.SOCKET]
    plan = {'fixture_directory': str(fixtures), 'fixtures': manifest,
        'module_sha256': hashlib.sha256(MODULE.read_bytes()).hexdigest(),
        'docker_argv': prefix + mod.create_arguments(fixtures, name='devharness-gate1-isolation-v1'),
        'host_sentinel': str(sentinel), 'host_sentinel_sha256': hashlib.sha256(sentinel.read_bytes()).hexdigest()}
    plan_path = tmp_path / 'plan.json'; plan_path.write_text(json.dumps(plan))
    (tmp_path / 'diagnostic-release-v1.json').write_text(json.dumps({'accepted': True, 'source_sha256': {}}))
    for name, value in [('ROOT', tmp_path), ('PLAN', plan_path), ('RESULT', tmp_path / 'result.json'),
                        ('PLAN_SHA256', hashlib.sha256(plan_path.read_bytes()).hexdigest())]:
        monkeypatch.setattr(runner, name, value)
    calls = []
    def transport(argv, **kwargs):
        arguments = argv[len(prefix):]; calls.append(arguments)
        stdout, stderr, code = b'', b'', 0
        if arguments[0] == 'create':
            stdout = b'a'*64 + b'\n'
            if failure == 'create': code, stdout = 1, b''
        elif arguments[0:2] == ['container', 'inspect']:
            code, stderr = 1, b'Error: No such container\n'
        elif arguments[0:2] == ['container', 'rm']: stdout = b'a'*64 + b'\n'
        return {'stdout': stdout, 'stderr': stderr, 'exit_code': code, 'error': None, 'process_reaped': True}
    monkeypatch.setattr(runner, '_run_process', transport)
    assert runner.main() == 1
    assert not any(c[0] == 'start' for c in calls)
    if failure == 'create': assert len(calls) == 1
    else: assert any(c[0:2] == ['container', 'rm'] for c in calls)
    result = json.loads(runner.RESULT.read_text())
    assert result['diagnostic_only'] is True
    assert 'controls_passed' not in result
