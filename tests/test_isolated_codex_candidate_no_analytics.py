"""One source-supported guard delta; no client or container launch."""
import ast
import importlib.util
from pathlib import Path

ROOT = Path('/Users/vajraang/Documents/ChatGPT/bio-agent-harness')


def load(name):
    path = ROOT / 'scripts' / (name + '.py')
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_only_added_configuration_is_explicit_analytics_optout():
    previous = load('verify_isolated_codex_candidate')
    corrected = load('verify_isolated_codex_candidate_no_analytics')
    args = corrected.command(12345)
    assert args.count('analytics.enabled=false') == 1
    marker = args.index('analytics.enabled=false')
    assert args[marker - 1] == '-c'
    assert args[:marker - 1] + args[marker + 1:] == previous.command(12345)
    assert corrected.POLICY == previous.POLICY
    assert corrected.CATALOG_JSON == previous.CATALOG_JSON


def test_all_other_functions_and_bounds_are_unchanged():
    previous = ast.parse((ROOT / 'scripts/verify_isolated_codex_candidate.py').read_text())
    corrected = ast.parse((ROOT / 'scripts/verify_isolated_codex_candidate_no_analytics.py').read_text())
    functions = {node.name: node for node in previous.body if isinstance(node, ast.FunctionDef)}
    for node in corrected.body:
        if isinstance(node, ast.FunctionDef) and node.name != 'command':
            assert ast.dump(node) == ast.dump(functions[node.name])


def test_pinned_source_disables_queue_and_flush_when_analytics_false():
    source_root = ROOT / 'reports/rna-native-gate1/2026-10-21-v2/provider/analytics/sources'
    client = (source_root / 'codex-rs--analytics--src--client.rs').read_text()
    adapter = (source_root / 'codex-rs--app-server--src--analytics_utils.rs').read_text()
    config = (source_root / 'codex-rs--config--src--types.rs').read_text()
    assert 'queue: (analytics_enabled != Some(false))' in client
    assert 'pub fn disabled() -> Self {\n        Self { queue: None }' in client
    assert 'let Some(queue) = self.queue.as_ref() else {\n            return;' in client
    assert 'config.analytics_enabled' in adapter
    assert 'pub struct AnalyticsConfigToml {' in config
    assert 'pub enabled: Option<bool>' in config
