"""No-launch thin-shim checks for the one-guard analytics correction."""
import importlib.util
from pathlib import Path

import pytest

ROOT = Path('/Users/vajraang/Documents/ChatGPT/bio-agent-harness')


def load():
    spec = importlib.util.spec_from_file_location('fixed_candidate_v9_shim',
        ROOT / 'scripts/run_isolated_codex_candidate_no_analytics.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_network_none_profile_is_the_original_with_only_new_root_and_name():
    shim = load()
    supervisor = shim.load_supervisor()
    assert supervisor.ROOT == ROOT / 'reports/rna-native-gate1/2026-10-21-v2/isolation/candidate-v9'
    assert supervisor.NAME == 'devharness-gate1-candidate-v9'
    assert supervisor.COMMAND[-1] == '/input/protocol.py'
    args = supervisor.arguments()
    assert args[args.index('--network') + 1] == 'none'
    assert '--read-only' in args and '--pull=never' in args
    assert '.devbench-codex-auth' not in str(args)


@pytest.mark.parametrize('symlink', [False, True])
def test_changed_original_is_not_imported(tmp_path, monkeypatch, symlink):
    shim = load()
    changed = tmp_path / 'changed'
    if symlink:
        changed.symlink_to(shim.SUPERVISOR)
    else:
        changed.write_text('raise AssertionError("must not execute")')
    monkeypatch.setattr(shim, 'SUPERVISOR', changed)
    with pytest.raises(PermissionError):
        shim.load_supervisor()
