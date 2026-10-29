"""Reviewed owned-PID image-transition observation; never admits a native route.

No child starts on import. The diagnostic requires its exact reviewed SHA-256.
Apple START_SUSPENDED permits registering NOTE_EXEC before child userspace.
Only fixed local marker reads run; no client, model, auth, or biology executes.
"""
from __future__ import annotations

import ctypes
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import select
import signal
import sys
import time

ROOT = Path('/Users/vajraang/Documents/ChatGPT/bio-agent-harness')
PRIOR = ROOT / 'scripts/run_native_child_diagnostic.py'
PRIOR_SHA = 'de73efb95bb7a16b0b53d25851271817ee2f6f5a7bb7066d03de5eda27fb43c2'
OUTPUT = ROOT / 'reports/rna-native-gate1/2026-10-21-v2/abort/observed.json'
SH_SHA = 'a3a314699fd6128c288842c9bbe3c8c82bd9dc22abfa7efad22ac4f6047d5d4f'
ENV = {'PATH': '/usr/bin:/bin', 'LANG': 'C', 'LC_ALL': 'C'}
LIMIT = 16384
SECONDS = 5


def load_prior():
    if PRIOR.is_symlink() or hashlib.sha256(PRIOR.read_bytes()).hexdigest() != PRIOR_SHA:
        raise ValueError('prior_supervisor_changed')
    spec = importlib.util.spec_from_file_location('_abort_prior', PRIOR)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.verify_pins()
    module.interpreter_identity()
    module.fixture_state()
    return module


def fixed_commands(prior):
    protocol = prior.load_pinned('_abort_protocol', prior.PROPOSAL / 'inert_child_protocol.py')
    if prior.sha('/bin/sh') != SH_SHA:
        raise ValueError('fixed_shell_changed')
    command = prior.controls_from_pins(protocol, prior.verify_pins())[0][1]
    marker = str(prior.PACKET / 'inside.txt')
    return (
        ('observer_no_exec', ['/bin/cat', marker]),
        ('observer_one_exec', ['/bin/sh', '-c',
            'exec /bin/cat /Users/vajraang/Documents/ChatGPT/bio-agent-harness/.nabd1/packet/inside.txt']),
        ('sandbox_image_transition', command),
    )


def spawn_suspended(command, cwd, pipe_write):
    """Darwin public ABI, exact argv/environment; all other FDs close on spawn."""
    lib = ctypes.CDLL('/usr/lib/libSystem.B.dylib', use_errno=True)
    ptr = ctypes.POINTER(ctypes.c_void_p)
    cstrs = ctypes.POINTER(ctypes.c_char_p)
    signatures = {
        'posix_spawnattr_init': [ptr], 'posix_spawnattr_destroy': [ptr],
        'posix_spawnattr_setflags': [ptr, ctypes.c_short],
        'posix_spawnattr_setpgroup': [ptr, ctypes.c_int],
        'posix_spawn_file_actions_init': [ptr], 'posix_spawn_file_actions_destroy': [ptr],
        'posix_spawn_file_actions_adddup2': [ptr, ctypes.c_int, ctypes.c_int],
        'posix_spawn_file_actions_addopen': [ptr, ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_uint],
        'posix_spawn_file_actions_addchdir_np': [ptr, ctypes.c_char_p],
        'posix_spawn': [ctypes.POINTER(ctypes.c_int), ctypes.c_char_p, ptr, ptr, cstrs, cstrs],
    }
    for name, arguments in signatures.items():
        getattr(lib, name).argtypes = arguments
        getattr(lib, name).restype = ctypes.c_int

    def checked(name, *args):
        error = getattr(lib, name)(*args)
        if error:
            raise OSError(error, 'fixed_spawn_operation_failed')

    attr, actions, pid = ctypes.c_void_p(), ctypes.c_void_p(), ctypes.c_int()
    attr_ready = actions_ready = False
    try:
        checked('posix_spawnattr_init', ctypes.byref(attr))
        attr_ready = True
        # Values verified in the installed public Darwin sys/spawn.h.
        checked('posix_spawnattr_setflags', ctypes.byref(attr), 0x0080 | 0x0002 | 0x4000)
        checked('posix_spawnattr_setpgroup', ctypes.byref(attr), 0)
        checked('posix_spawn_file_actions_init', ctypes.byref(actions))
        actions_ready = True
        checked('posix_spawn_file_actions_addopen', ctypes.byref(actions), 0, b'/dev/null', os.O_RDONLY, 0)
        for target in (1, 2):
            checked('posix_spawn_file_actions_adddup2', ctypes.byref(actions), pipe_write, target)
        checked('posix_spawn_file_actions_addchdir_np', ctypes.byref(actions), os.fsencode(cwd))
        argv = (ctypes.c_char_p * (len(command) + 1))(*map(os.fsencode, command), None)
        values = [os.fsencode(key + '=' + value) for key, value in ENV.items()]
        environment = (ctypes.c_char_p * (len(values) + 1))(*values, None)
        checked('posix_spawn', ctypes.byref(pid), os.fsencode(command[0]),
                ctypes.byref(actions), ctypes.byref(attr), argv, environment)
        return pid.value
    finally:
        if actions_ready:
            lib.posix_spawn_file_actions_destroy(ctypes.byref(actions))
        if attr_ready:
            lib.posix_spawnattr_destroy(ctypes.byref(attr))


def observe(command, cwd):
    """Only the spawned PID is watched; bounded pipes, no host process inventory."""
    read_fd, write_fd = os.pipe()
    queue = select.kqueue()
    pid = None
    reaped = False
    output = bytearray()
    outcome = {'launch_confirmed': False, 'registered_before_resume': False,
        'exec_observed': False, 'exit_observed': False, 'fork_observed': False,
        'process_reaped': False, 'exit_code': None, 'error_category': None}
    deadline = time.monotonic() + SECONDS
    try:
        pid = spawn_suspended(command, cwd, write_fd)
        outcome['launch_confirmed'] = True
        os.close(write_fd)
        write_fd = None
        os.set_blocking(read_fd, False)
        requested = select.KQ_NOTE_EXEC | select.KQ_NOTE_EXIT | select.KQ_NOTE_FORK
        queue.control([select.kevent(pid, filter=select.KQ_FILTER_PROC,
            flags=select.KQ_EV_ADD | select.KQ_EV_CLEAR, fflags=requested)], 0, 0)
        outcome['registered_before_resume'] = True
        os.kill(pid, signal.SIGCONT)
        eof = False
        while not (outcome['exit_observed'] and eof):
            if time.monotonic() >= deadline:
                raise TimeoutError('owned_child_deadline')
            for event in queue.control(None, 4, 0):
                if event.ident != pid or event.flags & select.KQ_EV_ERROR:
                    raise RuntimeError('unexpected_owned_pid_event')
                outcome['exec_observed'] |= bool(event.fflags & select.KQ_NOTE_EXEC)
                outcome['exit_observed'] |= bool(event.fflags & select.KQ_NOTE_EXIT)
                outcome['fork_observed'] |= bool(event.fflags & select.KQ_NOTE_FORK)
                if outcome['fork_observed']:
                    raise RuntimeError('unexpected_fork')
            if not eof and select.select([read_fd], [], [], 0.01)[0]:
                chunk = os.read(read_fd, min(4096, LIMIT + 1 - len(output)))
                eof = not chunk
                output.extend(chunk)
                if len(output) > LIMIT:
                    raise ValueError('owned_pipe_bound')
        waited, status = os.waitpid(pid, os.WNOHANG)
        if waited != pid:
            raise RuntimeError('exit_event_without_reap')
        reaped = True
        outcome.update(process_reaped=True, exit_code=os.waitstatus_to_exitcode(status))
    except BaseException as error:
        outcome['error_category'] = type(error).__name__ if type(error).__name__ in (
            'OSError', 'RuntimeError', 'TimeoutError', 'ValueError', 'KeyboardInterrupt') else 'other_discarded'
    finally:
        if pid and not reaped:
            try:
                os.killpg(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            cleanup_end = time.monotonic() + 1
            while time.monotonic() < cleanup_end:
                waited, status = os.waitpid(pid, os.WNOHANG)
                if waited == pid:
                    outcome.update(process_reaped=True, exit_code=os.waitstatus_to_exitcode(status))
                    break
                time.sleep(0.01)
        queue.close()
        os.close(read_fd)
        if write_fd is not None:
            os.close(write_fd)
    outcome['combined_pipe_bytes'] = len(output)
    outcome['exact_marker'] = bytes(output) == b'DEVHARNESS_OWNED_PACKET_MARKER_V1\n'
    outcome['raw_output_retained_or_hashed'] = False
    return outcome


def interpret(name, outcome):
    clean = (outcome['launch_confirmed'] and outcome['registered_before_resume']
        and outcome['exit_observed'] and outcome['process_reaped']
        and not outcome['fork_observed'] and outcome['error_category'] is None)
    if not clean:
        return 'observer_uncertain_stop'
    if name.startswith('observer_'):
        expected_exec = name == 'observer_one_exec'
        return ('observer_check_passed' if outcome['exit_code'] == 0 and outcome['exact_marker']
            and outcome['exec_observed'] == expected_exec else 'observer_check_failed_stop')
    if outcome['exit_code'] == -signal.SIGABRT:
        return 'abort_after_image_replacement' if outcome['exec_observed'] else 'abort_before_image_replacement'
    return 'marker_after_image_replacement' if (outcome['exec_observed']
        and outcome['exit_code'] == 0 and outcome['exact_marker']) else 'unclassified_stop'


def sequence(commands, cwd, run, precheck):
    outcomes = []
    for name, command in commands:
        precheck()
        outcome = run(command, cwd)
        outcome.update(name=name, classification=interpret(name, outcome))
        outcomes.append(outcome)
        precheck()
        if name.startswith('observer_') and outcome['classification'] != 'observer_check_passed':
            break
    return outcomes


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    own = Path(__file__)
    if (len(args) != 2 or args[0] != '--reviewed-sha256'
            or hashlib.sha256(own.read_bytes()).hexdigest() != args[1]):
        raise ValueError('exact_reviewed_runner_required')
    if sys.platform != 'darwin' or not sys.flags.isolated or not sys.flags.dont_write_bytecode:
        raise ValueError('isolated_darwin_runtime_required')
    if Path(sys.prefix).resolve() != ROOT / 'r/s6' or OUTPUT.exists() or OUTPUT.is_symlink():
        raise ValueError('fixed_runtime_and_fresh_result_required')
    if OUTPUT.parent.resolve() != OUTPUT.parent or not OUTPUT.parent.is_dir():
        raise ValueError('ordinary_output_parent_required')
    prior = load_prior()
    before = prior.fixture_state()
    commands = fixed_commands(prior)

    def precheck():
        prior.verify_pins()
        prior.interpreter_identity()
        if (prior.fixture_state() != before or fixed_commands(prior) != commands
                or hashlib.sha256(own.read_bytes()).hexdigest() != args[1]):
            raise ValueError('reviewed_identity_or_fixture_changed')

    result = {'format': 'devbench.owned-native-image-transition/1', 'runner_sha256': args[1],
        'model_calls': 0, 'auth_calls': 0, 'native_client_executions': 0, 'fits': 0,
        'native_route_qualified': False, 'live_generation_allowed': False,
        'biological_execution_allowed': False, 'prior_exit134_root_cause': 'unknown',
        'process_results': sequence(commands, str(prior.PACKET), observe, precheck)}
    result['fixture_unchanged'] = before == prior.fixture_state()
    prior.write_json(OUTPUT, result)
    decisive = {'abort_before_image_replacement', 'abort_after_image_replacement',
        'marker_after_image_replacement'}
    return 0 if (len(result['process_results']) == 3
        and result['process_results'][-1]['classification'] in decisive) else 2


if __name__ == '__main__':
    raise SystemExit(main())
