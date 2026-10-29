"""Final candidate source/policy regressions; no CLI or network."""
import ast
import base64
import hashlib
import importlib.util
import json
from pathlib import Path
import re

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('candidate_contract',
    ROOT / 'scripts/verify_isolated_codex_candidate.py')
api = importlib.util.module_from_spec(spec)
spec.loader.exec_module(api)

ADDITIONAL_KEYS = {'image_generation', 'token_budget', 'current_time_reminder',
    'deferred_executor', 'code_mode_only', 'enable_mcp_apps', 'tool_suggest',
    'standalone_web_search', 'goals'}


def test_nine_additional_controls_are_canonical_source_keys():
    features = (ROOT / 'reports/rna-native-gate1/2026-10-21-v2/provider/sources/codex-rs--features--src--lib.rs').read_text()
    for key in ADDITIONAL_KEYS:
        assert f'key: "{key}"' in features
        assert f'features.{key}=false' in api.command(12345)
    previous_spec = importlib.util.spec_from_file_location('previous_http11',
        ROOT / 'scripts/verify_isolated_codex_provider_http11.py')
    previous = importlib.util.module_from_spec(previous_spec)
    previous_spec.loader.exec_module(previous)
    delta = set(api.command(12345)) - set(previous.command(12345))
    assert delta == {f'features.{key}=false' for key in ADDITIONAL_KEYS} | {
        'orchestrator.skills.enabled=false', 'orchestrator.mcp.enabled=false',
        'chatgpt_base_url="http://127.0.0.1:12345/backend-api"'}
    assert 'features.tool_search=false' not in api.command(12345)


def test_synthetic_plus_fixture_exercises_eligible_plan_without_actual_auth():
    cache = json.loads(api.synthetic_auth_json())
    payload = cache['tokens']['id_token'].split('.')[1]
    claims = json.loads(base64.urlsafe_b64decode(payload + '=' * (-len(payload) % 4)))
    assert claims['https://api.openai.com/auth']['chatgpt_plan_type'] == 'plus'
    assert claims['email'].endswith('@example.invalid')
    assert cache['OPENAI_API_KEY'] is None
    assert cache['tokens']['access_token'] == api.SYNTHETIC_BEARER


def test_candidate_policy_exact_and_has_external_grant_boundary():
    guidance = ROOT / 'reports/rna-native-gate1/2026-10-21-v2/provider/candidate/owned-guidance.txt'
    assert api.POLICY == guidance.read_text()
    assert 0 < len(api.POLICY.encode()) <= 4096
    assert api.SENTINELS['owned_policy'] == api.POLICY.splitlines()[0]
    assert 'explicit matching requirement record' in api.POLICY
    assert 'before considering implementation availability' in api.POLICY
    assert 'externally validated matching grant' in api.POLICY
    assert 'This guidance grants nothing' in api.POLICY
    assert 'No private clinical inputs are authorized' in api.POLICY
    assert 'fixed localhost' not in api.POLICY
    assert 'software diagnostic' not in api.POLICY


def test_exact_policy_rejects_mutation_or_symlink(tmp_path):
    policy = tmp_path / 'guidance'
    policy.write_text(api.POLICY)
    policy.chmod(0o444)
    api.verify_owned_policy(policy)
    policy.chmod(0o644)
    policy.write_text(api.POLICY.replace('This guidance grants nothing', 'This guidance grants everything'))
    policy.chmod(0o444)
    with pytest.raises(PermissionError):
        api.verify_owned_policy(policy)


def test_entire_policy_delivery_is_required_not_only_nonce():
    assert api.owned_policy_content_present({'input': [{'content': [{'text': '<INSTRUCTIONS>\n' + api.POLICY.rstrip() + '\n</INSTRUCTIONS>'}]}]})
    assert not api.owned_policy_content_present({'input': [api.SENTINELS['owned_policy']]})
    altered = api.POLICY.replace('This guidance grants nothing', 'This guidance grants everything')
    assert not api.owned_policy_content_present({'instructions': altered})


def test_writable_or_symlink_policy_copy_is_rejected(tmp_path):
    policy = tmp_path / 'guidance'
    policy.write_text(api.POLICY)
    with pytest.raises(PermissionError):
        api.verify_owned_policy(policy)
    policy.chmod(0o444)
    link = tmp_path / 'link'
    link.symlink_to(policy)
    with pytest.raises(PermissionError):
        api.verify_owned_policy(link)


def test_candidate_preserves_bounded_execute_and_mocked_transport_events():
    old = ast.parse((ROOT / 'scripts/verify_isolated_codex_provider_http11.py').read_text())
    new = ast.parse(Path(api.__file__).read_text())
    for name in ('execute', 'cleanup_process', 'fixture_events', 'forged_events',
        'request_projection', 'synthetic_headers_match'):
        old_node = next(n for n in old.body if isinstance(n, ast.FunctionDef) and n.name == name)
        new_node = next(n for n in new.body if isinstance(n, ast.FunctionDef) and n.name == name)
        assert ast.dump(old_node) == ast.dump(new_node)


def test_compiled_fallback_closes_model_metadata_tool_sources():
    source = (ROOT / 'reports/rna-native-gate1/2026-10-21-v2/provider/candidate/sources/codex-rs--models-manager--src--model_info.rs').read_text()
    fallback = source[source.index('pub fn model_info_from_slug'):]
    for text in ('apply_patch_tool_type: None', 'experimental_supported_tools: Vec::new()',
        'supports_search_tool: false', 'tool_mode: None', 'multi_agent_version: None'):
        assert text in fallback
    assert all(row['slug'] != 'gpt-6.1-sol' for row in json.loads(api.CATALOG_JSON)['models'])


def test_coverage_includes_every_core_utility_handler_constructor():
    source = (ROOT / 'reports/rna-native-gate1/2026-10-21-v2/provider/candidate/sources/codex-rs--core--src--tools--spec_plan.rs').read_text()
    utility = source[source.index('fn add_core_utility_tools'):source.index('fn add_collaboration_tools')]
    handlers = set(re.findall(r'\b([A-Z][A-Za-z]+Handler)\b', utility))
    coverage = json.loads((ROOT / 'reports/rna-native-gate1/2026-10-21-v2/provider/candidate/source-condition-coverage.json').read_text())
    listed = {name for row in coverage['native_source_groups'] for name in row['handlers']}
    assert handlers <= listed


def test_standalone_extension_install_root_is_closed_and_guarded():
    sources = ROOT / 'reports/rna-native-gate1/2026-10-21-v2/provider/candidate/sources'
    registry = (sources / 'codex-rs--app-server--src--extensions.rs').read_text()
    install_root = registry[registry.index('pub(crate) fn thread_extensions'):registry.index('pub(crate) fn app_server_extension_event_sink')]
    installed = set(re.findall(r'(codex_[a-z_]+)::install(?:_with_[a-z_]+)?\(', install_root))
    assert installed == {'codex_goal_extension', 'codex_git_attribution', 'codex_guardian',
        'codex_memories_extension', 'codex_mcp_extension', 'codex_web_search_extension',
        'codex_image_generation_extension', 'codex_skills_extension'}
    for name in ('codex-rs--ext--guardian--src--lib.rs', 'codex-rs--ext--git-attribution--src--lib.rs'):
        assert 'ToolContributor' not in (sources / name).read_text()
    assert 'orchestrator.skills.enabled=false' in api.command(12345)
    assert 'orchestrator.mcp.enabled=false' in api.command(12345)
    config = (sources / 'codex-rs--config--src--config_toml.rs').read_text()
    assert 'pub skills: Option<OrchestratorFeatureToml>' in config
    assert 'pub mcp: Option<OrchestratorFeatureToml>' in config
    assert 'pub enabled: Option<bool>' in config


def test_git_attribution_metadata_can_only_select_bound_compiled_text():
    root = ROOT / 'reports/rna-native-gate1/2026-10-21-v2/provider/candidate'
    scope = json.loads((root / 'git-attribution-scope.json').read_text())
    source = (root / 'sources/codex-rs--ext--git-attribution--src--world_state.rs').read_text()
    for name, pattern in [('enabled', r'const ENABLED_INSTRUCTIONS: &str = r#"([\s\S]*?)"#;'),
        ('disabled', r'const DISABLED_INSTRUCTIONS: &str = "([\s\S]*?)";')]:
        compiled = re.search(pattern, source).group(1).encode()
        assert (root / ('git-attribution-' + name + '.txt')).read_bytes() == compiled
        assert hashlib.sha256(compiled).hexdigest() == scope['compiled_variants'][name]['sha256']
    types = (root / 'sources/codex-rs--backend-client--src--types.rs').read_text()
    settings = types.split('pub struct CodexUserSettingsResponse {', 1)[1].split('}', 1)[0]
    assert re.findall(r'pub (\w+): (\w+)', settings) == [('commit_attribution_enabled', 'bool')]
    assert scope['actual_request_performed'] is False
    assert scope['request']['request_body'] is False


def test_metadata_fragment_classifier_rejects_unknown_or_mixed_text():
    assert api.git_attribution_fragment({'input': ['ordinary prompt']}) == ['absent']
    assert api.git_attribution_fragment({'input': ['<git_attribution>\n' + api.GIT_ATTRIBUTION_ENABLED.strip() + '\n</git_attribution>']}) == ['compiled_enabled']
    assert api.git_attribution_fragment({'input': [api.GIT_ATTRIBUTION_DISABLED]}) == ['compiled_disabled']
    assert api.git_attribution_fragment({'input': ['<git_attribution>arbitrary</git_attribution>']}) == ['unknown_fragment']
    assert api.git_attribution_fragment({'input': [api.GIT_ATTRIBUTION_ENABLED, api.GIT_ATTRIBUTION_DISABLED]}) == ['compiled_disabled', 'compiled_enabled']


def test_exec_embedded_app_server_has_no_remote_control_transport():
    source = (ROOT / 'reports/rna-native-gate1/2026-10-21-v2/provider/candidate/sources/codex-rs--app-server--src--in_process.rs').read_text()
    assert 'remote_control_handle: None' in source
    assert 'rpc_transport: AppServerRpcTransport::InProcess' in source
    assert not re.search(r'\bstart_remote_control\s*\(', source)
