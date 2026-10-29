"""Owned guidance contract tests; no server, client or container launch."""
import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location('closed_home', Path(
    '/Users/vajraang/Documents/ChatGPT/bio-agent-harness/scripts/verify_isolated_codex_closed_home.py'))
api = importlib.util.module_from_spec(spec)
spec.loader.exec_module(api)


def test_nonempty_exact_owned_policy_can_be_bound(tmp_path):
    path = tmp_path / 'AGENTS.override.md'
    path.write_text(api.POLICY)
    assert len(api.verify_owned_policy(path)) == 64
    assert api.SENTINELS['owned_policy'] in api.POLICY
    assert api.SENTINELS['home_guidance'] not in api.POLICY


@pytest.mark.parametrize('content', ['', ' ', 'modified policy', 'x' * 4097])
def test_empty_modified_or_oversized_override_is_rejected(tmp_path, content):
    path = tmp_path / 'AGENTS.override.md'
    path.write_text(content)
    with pytest.raises(PermissionError):
        api.verify_owned_policy(path)


def test_override_symlink_is_rejected(tmp_path):
    actual = tmp_path / 'actual'
    actual.write_text(api.POLICY)
    path = tmp_path / 'AGENTS.override.md'
    path.symlink_to(actual)
    with pytest.raises(PermissionError):
        api.verify_owned_policy(path)
