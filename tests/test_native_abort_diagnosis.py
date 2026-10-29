"""Fake kernel/process outcomes only: this suite starts no OS child."""
import importlib.util
from pathlib import Path
import signal
from types import SimpleNamespace

import pytest

ROOT = Path('/Users/vajraang/Documents/ChatGPT/bio-agent-harness')


@pytest.fixture
def runner():
    spec = importlib.util.spec_from_file_location('_abort_test', ROOT / 'scripts/diagnose_native_abort.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def clean(**changes):
    value = dict(launch_confirmed=True, registered_before_resume=True,
        exec_observed=False, exit_observed=True, fork_observed=False,
        process_reaped=True, exit_code=0, error_category=None, exact_marker=True,
        combined_pipe_bytes=31, raw_output_retained_or_hashed=False)
    value.update(changes)
    return value


@pytest.mark.parametrize('exec_seen,expected', [
    (False, 'abort_before_image_replacement'), (True, 'abort_after_image_replacement')])
def test_sigabrt_classification_is_discriminating(runner, exec_seen, expected):
    assert runner.interpret('sandbox_image_transition', clean(
        exec_observed=exec_seen, exit_code=-signal.SIGABRT, exact_marker=False)) == expected


@pytest.mark.parametrize('changed', [dict(launch_confirmed=False),
    dict(registered_before_resume=False), dict(exit_observed=False),
    dict(process_reaped=False), dict(fork_observed=True), dict(error_category='TimeoutError')])
def test_missing_lifecycle_evidence_cannot_localize_abort(runner, changed):
    assert runner.interpret('sandbox_image_transition', clean(
        **changed, exit_code=-signal.SIGABRT)) == 'observer_uncertain_stop'


@pytest.mark.parametrize('name,exec_seen,expected', [
    ('observer_no_exec', False, 'observer_check_passed'),
    ('observer_no_exec', True, 'observer_check_failed_stop'),
    ('observer_one_exec', True, 'observer_check_passed'),
    ('observer_one_exec', False, 'observer_check_failed_stop')])
def test_observer_calibration_requires_both_directions(runner, name, exec_seen, expected):
    assert runner.interpret(name, clean(exec_observed=exec_seen)) == expected


@pytest.mark.parametrize('changed', [dict(exit_code=1), dict(exact_marker=False)])
def test_calibration_requires_marker_and_success(runner, changed):
    assert runner.interpret('observer_no_exec', clean(**changed)) == 'observer_check_failed_stop'


def test_no_sandbox_child_after_failed_calibration(runner):
    calls = []
    commands = [('observer_no_exec', ['fixed']), ('observer_one_exec', ['fixed']),
        ('sandbox_image_transition', ['fixed'])]

    def fake(command, cwd):
        calls.append(command)
        return clean(exact_marker=False)

    results = runner.sequence(commands, '/fixed', fake, lambda: None)
    assert len(calls) == len(results) == 1


def test_precheck_drift_stops_following_children(runner):
    checks, calls = [], []

    def precheck():
        checks.append(True)
        if len(checks) == 2:
            raise ValueError('source drift')

    def fake(command, cwd):
        calls.append(command)
        return clean()

    with pytest.raises(ValueError, match='source drift'):
        runner.sequence([('observer_no_exec', ['fixed']), ('observer_one_exec', ['fixed'])],
            '/fixed', fake, precheck)
    assert len(calls) == 1


def test_main_rejects_missing_review_without_launch(runner, monkeypatch):
    monkeypatch.setattr(runner, 'spawn_suspended', lambda *args: pytest.fail('unexpected child'))
    with pytest.raises(ValueError, match='exact_reviewed_runner_required'):
        runner.main([])


def test_fixed_commands_bind_historical_argv_and_no_shell_configuration(runner):
    prior = runner.load_prior()
    commands = runner.fixed_commands(prior)
    assert commands[0] == ('observer_no_exec', ['/bin/cat', str(prior.PACKET / 'inside.txt')])
    assert commands[1][1] == ['/bin/sh', '-c',
        'exec /bin/cat /Users/vajraang/Documents/ChatGPT/bio-agent-harness/.nabd1/packet/inside.txt']
    protocol = prior.load_pinned('_abort_test_protocol', prior.PROPOSAL / 'inert_child_protocol.py')
    assert commands[2][1] == protocol.fixed_controls()[0][1]
    assert set(runner.ENV) == {'PATH', 'LANG', 'LC_ALL'}


@pytest.mark.parametrize('exec_seen', [False, True])
def test_observer_registers_before_resume_and_reaps_with_fake_child(runner, monkeypatch, exec_seen):
    events = []
    class Queue:
        delivered = False

        def control(self, changes, maximum, timeout):
            if changes:
                events.append('register')
                return []
            if self.delivered:
                return []
            self.delivered = True
            return [SimpleNamespace(ident=999999, flags=0, fflags=(
                runner.select.KQ_NOTE_EXIT | (runner.select.KQ_NOTE_EXEC if exec_seen else 0)))]

        def close(self):
            events.append('close_queue')

    def spawn(command, cwd, write_fd):
        events.append('spawn_suspended')
        runner.os.write(write_fd, b'DEVHARNESS_OWNED_PACKET_MARKER_V1\n')
        return 999999

    def resume(pid, sig):
        assert events == ['spawn_suspended', 'register']
        assert pid == 999999 and sig == signal.SIGCONT
        events.append('resume')

    monkeypatch.setattr(runner.select, 'kqueue', Queue)
    monkeypatch.setattr(runner, 'spawn_suspended', spawn)
    monkeypatch.setattr(runner.os, 'kill', resume)
    monkeypatch.setattr(runner.os, 'killpg', lambda *args: pytest.fail('unexpected cleanup'))
    monkeypatch.setattr(runner.os, 'waitpid', lambda pid, opts: (pid, 0))
    result = runner.observe(['/fixed/child'], '/fixed/cwd')
    assert result['process_reaped'] and result['exact_marker']
    assert result['exec_observed'] == exec_seen and result['error_category'] is None
    assert result['registered_before_resume'] and result['exit_observed']


def test_registration_failure_kills_suspended_group_and_never_resumes(runner, monkeypatch):
    killed = []
    class Queue:
        def control(self, *args):
            raise OSError('owned event registration denied')
        def close(self):
            pass
    monkeypatch.setattr(runner.select, 'kqueue', Queue)
    monkeypatch.setattr(runner, 'spawn_suspended', lambda *args: 999999)
    monkeypatch.setattr(runner.os, 'kill', lambda *args: pytest.fail('must not resume'))
    monkeypatch.setattr(runner.os, 'killpg', lambda pid, sig: killed.append((pid, sig)))
    monkeypatch.setattr(runner.os, 'waitpid', lambda pid, opts: (pid, signal.SIGKILL))
    result = runner.observe(['/fixed/child'], '/fixed/cwd')
    assert result['error_category'] == 'OSError' and result['process_reaped']
    assert not result['registered_before_resume']
    assert killed == [(999999, signal.SIGKILL)]
