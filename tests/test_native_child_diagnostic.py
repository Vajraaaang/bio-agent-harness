"""No OS child launches: fixed supervisor preconditions and fake output controls."""
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

WORKSPACE = Path('/Users/vajraang/Documents/ChatGPT/bio-agent-harness')


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def runner():
    return load('_test_native_diagnostic', WORKSPACE / 'scripts/run_native_child_diagnostic.py')


@pytest.fixture
def protocol(runner):
    return load('_test_inert_protocol', runner.PROPOSAL / 'inert_child_protocol.py')


def raw(stdout=b'', stderr=b'', exit_code=0, error=None, launched=True, reaped=True):
    return dict(stdout=stdout, stderr=stderr, exit_code=exit_code, error=error,
        launch_confirmed=launched, process_reaped=reaped)


def denial(runner, name):
    target = runner.OWNED / 'outside.txt' if name == 'outside_read_deny' else runner.PACKET / 'link-outside.txt'
    return b'cat: ' + str(target).encode() + b': Operation not permitted\n'


def test_exact_pins_and_commands_are_unchanged(runner, protocol):
    pins = runner.verify_pins()
    controls = runner.controls_from_pins(protocol, pins)
    assert tuple(name for name, _ in controls) == runner.CONTROL_NAMES
    assert all(command[0] == '/usr/bin/sandbox-exec' and command[-2] == '/bin/cat' for _, command in controls)


def test_changed_proposal_pins_failclosed(runner, monkeypatch):
    monkeypatch.setattr(runner, 'PINS_SHA', '0' * 64)
    with pytest.raises(ValueError, match='proposal_pins_changed'):
        runner.verify_pins()


def test_changed_control_identity_failsclosed(runner, protocol):
    pins = json.loads((runner.PROPOSAL / 'pins.json').read_text())
    pins['controls'][0]['argv_json_sha256'] = '0' * 64
    with pytest.raises(ValueError, match='fixed_command_changed'):
        runner.controls_from_pins(protocol, pins)


@pytest.mark.parametrize('which', ['OWNED', 'OUTPUT'])
def test_existing_fixture_or_output_is_never_reused(runner, tmp_path, monkeypatch, which):
    monkeypatch.setattr(runner, 'OWNED', tmp_path / 'owned')
    monkeypatch.setattr(runner, 'OUTPUT', tmp_path / 'output')
    getattr(runner, which).mkdir()
    with pytest.raises(FileExistsError, match='fresh_paths_required'):
        runner.fixed_paths()


def test_fixture_exclusive_owneronly_and_integrity(runner, tmp_path, monkeypatch):
    monkeypatch.setattr(runner, 'OWNED', tmp_path / 'owned')
    monkeypatch.setattr(runner, 'PACKET', tmp_path / 'owned/packet')
    before = runner.create_fixture()
    assert before == runner.fixture_state()
    with pytest.raises(FileExistsError):
        runner.create_fixture()
    (runner.OWNED / 'outside.txt').write_bytes(b'changed')
    with pytest.raises(ValueError, match='fixture_marker_changed'):
        runner.fixture_state()


def test_symlink_target_and_modes_are_validated(runner, tmp_path, monkeypatch):
    monkeypatch.setattr(runner, 'OWNED', tmp_path / 'owned')
    monkeypatch.setattr(runner, 'PACKET', tmp_path / 'owned/packet')
    runner.create_fixture()
    link = runner.PACKET / 'link-outside.txt'
    link.unlink()
    link.symlink_to(runner.PACKET / 'inside.txt')
    with pytest.raises(ValueError, match='fixture_symlink_changed'):
        runner.fixture_state()


@pytest.mark.parametrize('bad', [
    dict(stderr=b'dyld: Library not loaded\n', exit_code=-6),
    dict(stderr=b'cat: missing: No such file or directory\n', exit_code=1),
    dict(stderr=b'unknown secret\n', exit_code=1),
    dict(exit_code=1, error='TimeoutError'),
    dict(exit_code=1, error='ProcessReapingUncertain', reaped=False),
    dict(exit_code=1, launched=False),
])
def test_startup_unknown_timeout_and_launch_uncertainty_never_pass(runner, protocol, bad):
    outcome = runner.classify(protocol, 'outside_read_deny', raw(**bad))
    assert not outcome['expected_match']
    assert outcome['classification'] == 'unexpected_or_startup_failure'
    assert 'stdout' not in outcome and 'stderr' not in outcome
    assert not outcome['raw_stdout_stderr_retained_or_hashed']
    assert 'unknown secret' not in json.dumps(outcome)


def test_exact_denial_requires_clean_execution(runner, protocol):
    for error in ('TimeoutError', 'ProcessGroupTerminationUncertain'):
        assert not runner.classify(protocol, 'outside_read_deny', raw(
            stderr=denial(runner, 'outside_read_deny'), exit_code=1, error=error))['expected_match']


def test_sequence_positive_then_two_denies_and_bounded_call(runner, protocol):
    controls = runner.controls_from_pins(protocol, runner.verify_pins())
    seen = []
    def fake(command, **kwargs):
        name = controls[len(seen)][0]
        seen.append(name)
        assert kwargs['prompt'] == b'' and kwargs['maximum'] == 16384
        assert kwargs['environment'] == runner.ENVIRONMENT and kwargs['cwd'] == str(runner.PACKET)
        kwargs['authorize']()
        return raw(stdout=runner.MARKER) if name == 'positive' else raw(stderr=denial(runner, name), exit_code=1)
    results = runner.run_controls(protocol, controls, fake, lambda _: None, lambda: None)
    assert seen == list(runner.CONTROL_NAMES)
    assert len(results) == 3 and all(item['expected_match'] for item in results)


@pytest.mark.parametrize('fail_index', [0, 1, 2])
def test_first_unexpected_result_stops_without_retry(runner, protocol, fail_index):
    controls = runner.controls_from_pins(protocol, runner.verify_pins())
    seen = []
    def fake(command, **kwargs):
        name = controls[len(seen)][0]
        index = len(seen)
        seen.append(name)
        if index == fail_index:
            return raw(stderr=b'unrecognized startup failure\n', exit_code=1)
        return raw(stdout=runner.MARKER) if name == 'positive' else raw(stderr=denial(runner, name), exit_code=1)
    results = runner.run_controls(protocol, controls, fake, lambda _: None, lambda: None)
    assert len(seen) == fail_index + 1 and not results[-1]['expected_match']


def test_partial_evidence_survives_postcheck_failure(runner, protocol):
    controls = runner.controls_from_pins(protocol, runner.verify_pins())
    results = []
    def fail():
        raise ValueError('fixture changed')
    with pytest.raises(ValueError):
        runner.run_controls(protocol, controls, lambda *_args, **_kwargs: raw(stdout=runner.MARKER),
            lambda _: None, fail, results)
    assert len(results) == 1


@pytest.mark.parametrize('message', [b'Operation not permitted', b'Permission denied'])
def test_exact_sandbox_application_denied_is_scalar_not_cat_denial(runner, protocol, message):
    exact = b'sandbox-exec: sandbox_apply: ' + message + b'\n'
    result = runner.classify(protocol, 'positive', raw(stderr=exact, exit_code=1))
    assert result['sandbox_application_denied']
    assert result['classification'] == 'sandbox_application_denied'
    assert not result['expected_match']
    assert 'sandbox_apply' not in json.dumps({key: value for key, value in result.items() if key != 'classification'})
    for changed in (b'prefix' + exact, exact + b'suffix', exact.rstrip()):
        result = runner.classify(protocol, 'positive', raw(stderr=changed, exit_code=1))
        assert not result['sandbox_application_denied']
        assert result['classification'] == 'unexpected_or_startup_failure'
    result = runner.classify(protocol, 'positive', raw(stderr=exact, exit_code=1, reaped=False))
    assert not result['sandbox_application_denied']
