"""One independently reviewed literal-cat mapping delta, no route admission.

Only the coordinator may release this fixed diagnostic. No launch on import.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import sys

ROOT = Path('/Users/vajraang/Documents/ChatGPT/bio-agent-harness')
OBSERVER = ROOT / 'scripts/diagnose_native_abort.py'
OBSERVER_SHA = 'b68bd3b1636e02cf9dda39577a23c4f6f570d1823d7beb3be0941171fe4877b5'
BASELINE = ROOT / 'reports/rna-native-gate1/2026-10-21-v2/abort/observed.json'
BASELINE_SHA = '3eeb78e793cf70fe5ffa6485af4bfa3475ba409c214759d15dc06f281a3ae8d2'
ATTEMPT = BASELINE.parent / 'mapping-attempt.json'
OUTPUT = BASELINE.parent / 'mapping-observed.json'
DELTA = b'\n(allow file-map-executable (literal "/bin/cat"))\n'


def digest(path):
    if path.is_symlink() or path.resolve() != path or not path.is_file():
        raise ValueError('ordinary_fixed_file_required')
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_observer():
    if digest(OBSERVER) != OBSERVER_SHA or digest(BASELINE) != BASELINE_SHA:
        raise ValueError('reviewed_observer_or_baseline_changed')
    baseline = json.loads(BASELINE.read_text())
    results = baseline['process_results']
    if (len(results) != 3 or any(item['classification'] != 'observer_check_passed' for item in results[:2])
            or results[-1]['classification'] != 'abort_after_image_replacement'
            or not baseline['fixture_unchanged'] or baseline['runner_sha256'] != OBSERVER_SHA):
        raise ValueError('validated_calibration_and_baseline_required')
    spec = importlib.util.spec_from_file_location('_mapping_observer', OBSERVER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def delta_command(observer, prior):
    baseline = observer.fixed_commands(prior)[-1][1]
    if baseline[:2] != ['/usr/bin/sandbox-exec', '-p'] or baseline[-3:] != [
            '--', '/bin/cat', str(prior.PACKET / 'inside.txt')]:
        raise ValueError('fixed_baseline_command_required')
    policy = (prior.PROPOSAL / 'candidate.sbpl').read_bytes()
    if baseline[2].encode() != policy:
        raise ValueError('baseline_policy_changed')
    candidate = baseline.copy()
    candidate[2] = (policy + DELTA).decode()
    return candidate


def classify(observer, outcome):
    state = observer.interpret('sandbox_image_transition', outcome)
    return {
        'marker_after_image_replacement': 'literal_cat_mapping_delta_restores_marker_for_this_control',
        'abort_after_image_replacement': 'literal_cat_mapping_delta_not_sufficient_for_this_control',
    }.get(state, 'mapping_control_uncertain_stop')


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 2 or args[0] != '--reviewed-sha256' or digest(Path(__file__)) != args[1]:
        raise ValueError('exact_reviewed_mapping_runner_required')
    if (sys.platform != 'darwin' or not sys.flags.isolated or not sys.flags.dont_write_bytecode
            or Path(sys.prefix).resolve() != ROOT / 'r/s6'):
        raise ValueError('isolated_pinned_darwin_runtime_required')
    if (ATTEMPT.exists() or ATTEMPT.is_symlink() or OUTPUT.exists() or OUTPUT.is_symlink()
            or OUTPUT.parent.resolve() != OUTPUT.parent):
        raise ValueError('fresh_ordinary_attempt_and_result_required')
    observer = load_observer()
    prior = observer.load_prior()
    fixture = prior.fixture_state()
    command = delta_command(observer, prior)

    def precheck():
        load_observer()
        prior.verify_pins()
        prior.interpreter_identity()
        if (digest(Path(__file__)) != args[1] or prior.fixture_state() != fixture
                or delta_command(observer, prior) != command):
            raise ValueError('mapping_custody_changed')

    precheck()
    identity = {'format': 'devbench.literal-cat-map-delta-attempt/1',
        'runner_sha256': args[1], 'observer_sha256': OBSERVER_SHA,
        'baseline_result_sha256': BASELINE_SHA, 'base_policy_sha256': prior.sha(prior.PROPOSAL / 'candidate.sbpl'),
        'delta_policy_sha256': hashlib.sha256(command[2].encode()).hexdigest(),
        'sole_delta': DELTA.decode(), 'maximum_child_processes': 1,
        'native_route_qualified': False, 'live_generation_allowed': False,
        'biological_execution_allowed': False}
    # Exclusive attempt seal precedes the only possible child. Any interrupted
    # or ambiguous attempt blocks another invocation even if no result exists.
    prior.write_json(ATTEMPT, identity)
    outcome = observer.observe(command, str(prior.PACKET))
    precheck()
    result = {**identity, 'format': 'devbench.literal-cat-map-delta-observation/1',
        'classification': classify(observer, outcome), 'process_result': outcome,
        'model_calls': 0, 'auth_calls': 0, 'native_client_executions': 0, 'fits': 0,
        'fixture_unchanged': fixture == prior.fixture_state(), 'prior_exit134_root_cause': 'unknown'}
    prior.write_json(OUTPUT, result)
    return 2 if result['classification'] == 'mapping_control_uncertain_stop' else 0


if __name__ == '__main__':
    raise SystemExit(main())
