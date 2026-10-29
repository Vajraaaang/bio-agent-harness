"""Fixed mapping-delta validation; fake execution only, no OS children."""
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path('/Users/vajraang/Documents/ChatGPT/bio-agent-harness')


@pytest.fixture
def runner():
    spec = importlib.util.spec_from_file_location('_mapping_test', ROOT / 'scripts/diagnose_native_abort_mapping.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_delta_changes_only_literal_cat_executable_mapping(runner):
    observer = runner.load_observer()
    prior = observer.load_prior()
    before = observer.fixed_commands(prior)[-1][1]
    after = runner.delta_command(observer, prior)
    assert after[:2] == before[:2] and after[3:] == before[3:]
    assert after[2].encode() == before[2].encode() + runner.DELTA
    assert runner.DELTA == b'\n(allow file-map-executable (literal "/bin/cat"))\n'


@pytest.mark.parametrize('field', ['OBSERVER_SHA', 'BASELINE_SHA'])
def test_changed_observer_or_calibration_rejected(runner, monkeypatch, field):
    monkeypatch.setattr(runner, field, '0' * 64)
    with pytest.raises(ValueError, match='reviewed_observer_or_baseline_changed'):
        runner.load_observer()


@pytest.mark.parametrize('value,expected', [
    ('marker_after_image_replacement', 'literal_cat_mapping_delta_restores_marker_for_this_control'),
    ('abort_after_image_replacement', 'literal_cat_mapping_delta_not_sufficient_for_this_control'),
    ('abort_before_image_replacement', 'mapping_control_uncertain_stop'),
    ('observer_uncertain_stop', 'mapping_control_uncertain_stop'),
    ('unclassified_stop', 'mapping_control_uncertain_stop')])
def test_mapping_classification_never_grants_route(runner, value, expected):
    class Observer:
        @staticmethod
        def interpret(name, outcome):
            return value
    assert runner.classify(Observer, {}) == expected


def test_review_required_before_any_launch(runner, monkeypatch):
    monkeypatch.setattr(runner, 'load_observer', lambda: pytest.fail('unexpected observer access'))
    with pytest.raises(ValueError, match='exact_reviewed_mapping_runner_required'):
        runner.main([])


def test_attempt_seal_blocks_retry_before_observer_or_child(runner, monkeypatch, tmp_path):
    attempt = tmp_path / 'attempt.json'
    attempt.write_text('{}')
    monkeypatch.setattr(runner, 'ATTEMPT', attempt)
    monkeypatch.setattr(runner, 'load_observer', lambda: pytest.fail('must block before observer'))
    reviewed = runner.digest(Path(runner.__file__))
    with pytest.raises(ValueError, match='fresh_ordinary_attempt_and_result_required'):
        runner.main(['--reviewed-sha256', reviewed])


def test_one_fake_child_requires_seal_and_preserves_gate(runner, monkeypatch, tmp_path):
    attempt, output = tmp_path / 'attempt.json', tmp_path / 'result.json'
    monkeypatch.setattr(runner, 'ATTEMPT', attempt)
    monkeypatch.setattr(runner, 'OUTPUT', output)
    observer = runner.load_observer()
    calls = []

    def fake(command, cwd):
        assert attempt.exists() and not output.exists()
        calls.append(command)
        return dict(launch_confirmed=True, registered_before_resume=True,
            exec_observed=True, exit_observed=True, fork_observed=False,
            process_reaped=True, exit_code=-6, error_category=None,
            exact_marker=False, combined_pipe_bytes=0, raw_output_retained_or_hashed=False)

    monkeypatch.setattr(observer, 'observe', fake)
    monkeypatch.setattr(runner, 'load_observer', lambda: observer)
    reviewed = runner.digest(Path(runner.__file__))
    assert runner.main(['--reviewed-sha256', reviewed]) == 0
    assert len(calls) == 1
    result = json.loads(output.read_text())
    assert result['classification'] == 'literal_cat_mapping_delta_not_sufficient_for_this_control'
    assert result['live_generation_allowed'] is False
    assert result['biological_execution_allowed'] is False
    assert result['native_route_qualified'] is False
    with pytest.raises(ValueError, match='fresh_ordinary_attempt_and_result_required'):
        runner.main(['--reviewed-sha256', reviewed])
    assert len(calls) == 1
