"""Pinned catalog contract tests; no processes, sockets or live services."""
import importlib.util
import json
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location('catalog_contract', Path(
    '/Users/vajraang/Documents/ChatGPT/bio-agent-harness/scripts/verify_isolated_codex_catalog.py'))
api = importlib.util.module_from_spec(spec)
spec.loader.exec_module(api)


def test_catalog_semantics_are_exact_official_source():
    reference = Path('/Users/vajraang/Documents/ChatGPT/bio-agent-harness/reports/rna-native-gate1/2026-10-21-v2/client/public-models-0.147.0.json')
    assert json.loads(api.CATALOG_JSON) == json.loads(reference.read_bytes())
    assert len(json.loads(api.CATALOG_JSON)['models']) == 8
    assert all(row['slug'] != 'gpt-6.1-sol' for row in json.loads(api.CATALOG_JSON)['models'])
    assert 'model_catalog_json="/output/protocol/model-catalog.json"' in api.command(12345)


def test_exact_catalog_can_be_bound(tmp_path):
    path = tmp_path / 'catalog.json'
    path.write_text(api.CATALOG_JSON)
    assert api.verify_catalog(path) == api.CATALOG_SHA256


@pytest.mark.parametrize('content', ['', '{}', 'x' * 500])
def test_changed_catalog_is_rejected(tmp_path, content):
    path = tmp_path / 'catalog.json'
    path.write_text(content)
    with pytest.raises(PermissionError):
        api.verify_catalog(path)


def test_symlink_catalog_is_rejected(tmp_path):
    actual = tmp_path / 'actual'
    actual.write_text(api.CATALOG_JSON)
    path = tmp_path / 'catalog.json'
    path.symlink_to(actual)
    with pytest.raises(PermissionError):
        api.verify_catalog(path)
