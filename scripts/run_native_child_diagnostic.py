"""One reviewed, no-model OS-child diagnostic; never grants route admission.

Only a released hash may run this supervisor. The historical candidate includes
explicit system runtime, preference and IPC exceptions; it is not packet-only.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import sys
import time

WORKSPACE = Path('/Users/vajraang/Documents/ChatGPT/bio-agent-harness')
PROPOSAL = WORKSPACE / 'reports/rna-signedin-codex-route/2026-10-19-v1/native-abort-diagnosis/native-child-canary-proposal-v1'
EPOCH = WORKSPACE / 'reports/rna-context-readback/2026-10-19-v1/installed-verification/source/signedin-8'
OUTPUT = WORKSPACE / 'reports/rna-native-gate1/2026-10-21-v1/diagnostic-run-v1'
OWNED = WORKSPACE / '.nabd1'
PACKET = OWNED / 'packet'
PYTHON = WORKSPACE / 'r/s6/bin/python'
PYTHON_TARGET = Path('/Library/Frameworks/Python.framework/Versions/3.12/bin/python3.12')
PYTHON_SHA = 'e7bdd7770f59ff7fa58d4e1074481051a462b03d4c6a6e36e07549dce546fe03'
PINS_SHA = '465b043cdf1fa911b6045317dffb45caff486497d9cbcf5802392a55232b3b10'
EPOCH_PINS = {
    'source-manifest.json': '0aac7c6b638e4545a76b18a4ee541a7dcc6565be6f3feb0cff6dbef304f9c0ef',
    'runtime-binding.json': '0c2b3cf98b0c96ab220992c7363c925c6204c384cf6257684b1820a1e7dc817e',
    'scripts/run_rna_tracing_verification.py': 'c6ca4c9f86523c4c18233ff34656b70da6b196068e7c4bf108db0a9e30c9b3a1',
}
HELPER_SHA = '0b04b8182a5baae807a565b51c00341a4ff99e80a0a1e07fad529382d5258d0c'
CONTROL_NAMES = ('positive', 'outside_read_deny', 'symlink_read_deny')
ENVIRONMENT = {'PATH': '/usr/bin:/bin', 'LANG': 'C', 'LC_ALL': 'C'}
MARKER = b'DEVHARNESS_OWNED_PACKET_MARKER_V1\n'
OUTSIDE_MARKER = b'DEVHARNESS_OWNED_ADJACENT_CANARY_V1\n'


def sha(path):
    path = Path(path)
    if path.is_symlink() or not stat.S_ISREG(path.stat().st_mode):
        raise ValueError('ordinary_file_required')
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def fixed_paths():
    for path in (WORKSPACE, PROPOSAL, EPOCH, OUTPUT.parent):
        if path.resolve() != path:
            raise ValueError('fixed_path_changed')
    if any(path.exists() or path.is_symlink() for path in (OWNED, OUTPUT)):
        raise FileExistsError('fresh_paths_required')


def verify_pins():
    if sha(PROPOSAL / 'pins.json') != PINS_SHA:
        raise ValueError('proposal_pins_changed')
    pins = json.loads((PROPOSAL / 'pins.json').read_text())
    for entry in pins['files']:
        path = Path(entry['path'])
        if path.resolve() != path or sha(path) != entry['sha256'] or path.stat().st_size != entry['bytes']:
            raise ValueError('preserved_proposal_or_evidence_changed')
    for entry in pins['system_runtime_binaries']:
        if sha(Path(entry['path'])) != entry['sha256']:
            raise ValueError('system_binary_changed')
    for relative, expected in EPOCH_PINS.items():
        if sha(EPOCH / relative) != expected:
            raise ValueError('frozen_source_changed')
    return pins


def interpreter_identity():
    links = ((PYTHON, 'python3.12'), (PYTHON.parent / 'python3.12', str(PYTHON_TARGET)))
    if (any(not path.is_symlink() or os.readlink(path) != target for path, target in links)
            or PYTHON.resolve() != PYTHON_TARGET or sha(PYTHON_TARGET) != PYTHON_SHA):
        raise ValueError('interpreter_changed')
    return {'target': str(PYTHON_TARGET), 'sha256': PYTHON_SHA}


def load_pinned(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def fixture_state():
    expected = {OWNED, PACKET, PACKET / 'inside.txt', OWNED / 'outside.txt', PACKET / 'link-outside.txt'}
    if OWNED.is_symlink() or {OWNED, *OWNED.rglob('*')} != expected:
        raise ValueError('fixture_tree_changed')
    for path in expected:
        metadata = path.lstat()
        if metadata.st_uid != os.getuid():
            raise ValueError('fixture_owner_changed')
        if path == PACKET / 'link-outside.txt':
            if not stat.S_ISLNK(metadata.st_mode) or os.readlink(path) != str(OWNED / 'outside.txt'):
                raise ValueError('fixture_symlink_changed')
        elif path in (OWNED, PACKET):
            if not stat.S_ISDIR(metadata.st_mode) or stat.S_IMODE(metadata.st_mode) != 0o700:
                raise ValueError('fixture_directory_changed')
        elif not stat.S_ISREG(metadata.st_mode) or stat.S_IMODE(metadata.st_mode) != 0o600:
            raise ValueError('fixture_file_changed')
    state = {'inside_sha256': sha(PACKET / 'inside.txt'), 'outside_sha256': sha(OWNED / 'outside.txt'),
        'symlink_target': os.readlink(PACKET / 'link-outside.txt'), 'owner_and_modes_validated': True}
    if (state['inside_sha256'] != hashlib.sha256(MARKER).hexdigest()
            or state['outside_sha256'] != hashlib.sha256(OUTSIDE_MARKER).hexdigest()):
        raise ValueError('fixture_marker_changed')
    return state


def create_fixture():
    OWNED.mkdir(mode=0o700)
    PACKET.mkdir(mode=0o700)
    for path, content in ((PACKET / 'inside.txt', MARKER), (OWNED / 'outside.txt', OUTSIDE_MARKER)):
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(content)
    (PACKET / 'link-outside.txt').symlink_to(OWNED / 'outside.txt')
    return fixture_state()


def classify(protocol, name, raw):
    """Use only exact expected cat output; generic Seatbelt failure is not denial."""
    sandbox_denied = (raw['launch_confirmed'] and raw['process_reaped'] and raw.get('error') is None
        and raw['exit_code'] == 1 and not raw['stdout'] and raw['stderr'] in (
            b'sandbox-exec: sandbox_apply: Operation not permitted\n',
            b'sandbox-exec: sandbox_apply: Permission denied\n'))
    result = protocol.sanitize_outcome(name, raw)
    result['sandbox_application_denied'] = bool(sandbox_denied)
    result['classification'] = (('positive_marker_read' if name == 'positive' else 'exact_cat_read_denial')
        if result['expected_match'] else 'sandbox_application_denied' if sandbox_denied
        else 'unexpected_or_startup_failure')
    return result


def controls_from_pins(protocol, pins):
    controls = protocol.fixed_controls()
    if tuple(name for name, _ in controls) != CONTROL_NAMES:
        raise ValueError('control_sequence_changed')
    for (name, argv), pin in zip(controls, pins['controls'], strict=True):
        identity = hashlib.sha256(json.dumps(argv, ensure_ascii=True, separators=(',', ':')).encode()).hexdigest()
        if name != pin['name'] or argv[-1] != pin['target'] or identity != pin['argv_json_sha256']:
            raise ValueError('fixed_command_changed')
    return controls


def run_controls(protocol, controls, run_process, prelaunch, postlaunch, results=None):
    results = [] if results is None else results
    for name, command in controls:
        prelaunch(command)
        raw = run_process(command, cwd=str(PACKET), environment=dict(ENVIRONMENT), prompt=b'',
            deadline=time.monotonic() + 5, maximum=16384, authorize=lambda: prelaunch(command))
        result = classify(protocol, name, raw)
        results.append(result)
        postlaunch()
        if not result['expected_match']:
            break
    return results


def write_json(path, value):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, 'w') as stream:
        json.dump(value, stream, sort_keys=True, indent=2, allow_nan=False)
        stream.write('\n')


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    result = {'format': 'devbench.native-os-child-diagnostic/1', 'status': 'blocked',
        'model_calls': 0, 'auth_calls': 0, 'native_client_executions': 0, 'fits': 0,
        'native_cli_qualified': False, 'model_route_qualified': False,
        'prior_exit134_root_cause': 'unknown', 'write_or_network_denial_qualified': False,
        'runtime_exceptions': 'Historical base retains CFPreferences, opendirectory, PowerManagement, shared memory, PTY, device and sysctl exceptions plus declared system runtime access; not packet-only.',
        'raw_stdout_stderr_retained_or_hashed': False, 'process_results': []}
    output_created = False
    verifier = snapshot = protocol = None
    observed, denied = [], []
    pending = None
    code = 2
    stage = 'reviewed_identity_precheck'
    try:
        if (len(args) != 2 or args[0] != '--reviewed-sha256' or len(args[1]) != 64
                or sha(Path(__file__)) != args[1]):
            raise ValueError('reviewed_runner_hash_required')
        if not sys.flags.isolated or not sys.flags.dont_write_bytecode or Path(sys.prefix).resolve() != WORKSPACE / 'r/s6':
            raise ValueError('isolated_pinned_runtime_required')
        fixed_paths()
        pins = verify_pins()
        interpreter_identity()
        protocol = load_pinned('_fixed_child_protocol', PROPOSAL / 'inert_child_protocol.py')
        controls = controls_from_pins(protocol, pins)
        verifier = load_pinned('_fixed_child_custody', EPOCH / 'scripts/run_rna_tracing_verification.py')
        manifest = verifier.read_json(EPOCH / 'source-manifest.json')
        binding = verifier.read_json(EPOCH / 'runtime-binding.json')
        origins = verifier.validate_runtime(EPOCH, manifest, binding)
        from devbench.analysis import codex_signedin, rna_trial_authority
        origin = Path(codex_signedin.__file__).resolve()
        if str(origin) not in origins or sha(origin) != HELPER_SHA:
            raise ValueError('installed_process_helper_changed')

        def custody():
            verify_pins()
            interpreter_identity()
            if sha(Path(__file__)) != args[1] or sha(origin) != HELPER_SHA:
                raise ValueError('runner_or_helper_changed')
            state = {'source': verifier.source_state(EPOCH, manifest), 'runtime': verifier.runtime_state(),
                'custody_source': rna_trial_authority.trial_enforcement_source_digest(),
                'custody_runtime': rna_trial_authority.trial_enforcement_runtime_digest()}
            runtime_manifest = Path(binding['runtime_manifest_path'])
            if (sha(runtime_manifest) != binding['runtime_manifest_sha256']
                    or state['runtime'] != verifier.read_json(runtime_manifest)
                    or verifier.validate_runtime(EPOCH, manifest, binding) != origins):
                raise ValueError('installed_custody_changed')
            return state

        def audit(event, values):
            nonlocal pending
            if event == 'subprocess.Popen':
                if (pending is None or len(observed) >= 3 or str(values[0]) != '/usr/bin/sandbox-exec'
                        or list(values[1]) != pending or values[2] != str(PACKET) or values[3] != ENVIRONMENT):
                    denied.append('unreviewed_process')
                    raise PermissionError('unreviewed_process')
                observed.append(list(pending))
                pending = None
            elif event in ('os.exec', 'os.system', 'os.posix_spawn'):
                denied.append('alternate_process_launch')
                raise PermissionError('alternate_process_launch')
            elif event.startswith('socket.'):
                denied.append('python_network')
                raise PermissionError('python_network')

        sys.addaudithook(audit)
        stage = 'custody_precheck'
        snapshot = custody()
        OUTPUT.mkdir(mode=0o700)
        output_created = True
        write_json(OUTPUT / 'custody-before.json', snapshot)
        stage = 'fixture_initialization'
        initial_fixture = create_fixture()
        result.update(runner_sha256=args[1], fixture_before=initial_fixture,
            installed_process_helper={'path': str(origin), 'sha256': HELPER_SHA},
            command_identities=pins['controls'], policy_sha256=protocol.POLICY_SHA)

        def prelaunch(command):
            nonlocal pending
            if (len(observed) >= len(controls) or command != controls[len(observed)][1]
                    or denied or fixture_state() != initial_fixture):
                raise PermissionError('fixed_launch_precheck_failed')
            verify_pins()
            if sha(Path(__file__)) != args[1]:
                raise PermissionError('runner_changed')
            pending = command

        def postlaunch():
            nonlocal pending
            pending = None
            if fixture_state() != initial_fixture or denied:
                raise ValueError('fixture_or_launch_changed')

        stage = 'fixed_control_sequence'
        run_controls(protocol, controls, codex_signedin._run_process, prelaunch, postlaunch, result['process_results'])
        if (len(result['process_results']) != 3 or len(observed) != 3
                or not all(item['expected_match'] for item in result['process_results'])):
            raise RuntimeError('fixed_control_sequence_stopped')
        code = 0
    except BaseException as error:
        result.update(failed_stage=stage, error_category=type(error).__name__ if type(error).__name__ in
            {'ValueError', 'PermissionError', 'FileExistsError', 'FileNotFoundError', 'OSError', 'RuntimeError', 'KeyboardInterrupt', 'SystemExit'} else 'other_discarded')
    finally:
        pending = None
        if output_created:
            try:
                after = custody()
                write_json(OUTPUT / 'custody-after.json', after)
                result['custody_unchanged'] = snapshot == after
                result['fixture_after'] = fixture_state()
                result['fixture_unchanged'] = result.get('fixture_before') == result['fixture_after']
                if not result['custody_unchanged'] or not result['fixture_unchanged'] or denied:
                    code = 2
            except BaseException:
                code = 2
                result['postcheck_error_category'] = 'custody_or_fixture_postcheck_failed'
            result.update(status='passed_os_child_read_controls_only' if code == 0 else 'blocked',
                process_count=len(observed), denied_events=denied, exit_code=code)
            write_json(OUTPUT / 'result.json', result)
        print(json.dumps({'status': result['status'], 'exit_code': code, 'process_count': len(observed),
            'result': str(OUTPUT / 'result.json') if output_created else None,
            'native_cli_qualified': False, 'raw_output_retained': False}, sort_keys=True))
    return code


if __name__ == '__main__':
    raise SystemExit(main())
