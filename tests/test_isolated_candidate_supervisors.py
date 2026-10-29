"""Exact thin-shim and preparation checks; no container/client launch."""
import importlib.util
from pathlib import Path

import pytest

ROOT = Path('/Users/vajraang/Documents/ChatGPT/bio-agent-harness')
CASES = [('run_isolated_codex_candidate.py', 'candidate-v8'),
    ('run_isolated_account_probe_fake.py', 'account-probe-fake-v1')]


def load(filename):
    spec = importlib.util.spec_from_file_location('fixed_shim_' + filename.replace('.', '_'),
        ROOT / 'scripts' / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize('filename,slug', CASES)
def test_shim_only_changes_fixed_root_and_name_and_preserves_original_profile(filename, slug):
    shim = load(filename)
    original = shim.load_supervisor()
    assert original.ROOT == ROOT / 'reports/rna-native-gate1/2026-10-21-v2/isolation' / slug
    assert original.NAME == 'devharness-gate1-' + slug
    assert original.CLIENT == ROOT / 'reports/rna-native-gate1/2026-10-21-v2/client/package'
    assert original.COMMAND[-1] == '/input/protocol.py'
    args = original.arguments()
    assert args[args.index('--network') + 1] == 'none'
    assert args[args.index('--user') + 1] == '501:20'
    assert '--read-only' in args and '--pull=never' in args
    assert '/private' not in str(args) and '.devbench-codex-auth' not in str(args)


@pytest.mark.parametrize('filename,slug', CASES)
@pytest.mark.parametrize('symlink', [False, True])
def test_changed_or_symlinked_original_is_rejected(filename, slug, symlink, tmp_path, monkeypatch):
    shim = load(filename)
    candidate = tmp_path / 'changed'
    if symlink:
        candidate.symlink_to(shim.SUPERVISOR)
    else:
        candidate.write_text('raise AssertionError("must never execute")')
    monkeypatch.setattr(shim, 'SUPERVISOR', candidate)
    with pytest.raises(PermissionError):
        shim.load_supervisor()
