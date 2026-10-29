import importlib.util
from pathlib import Path
import pytest
W=Path('/Users/vajraang/Documents/ChatGPT/bio-agent-harness')

@pytest.mark.parametrize('script,root,name',[
    ('run_gate1_login_proxy_categories.py','auth-preparation/proxy-compat-v2','devharness-gate1-login-proxy-v2'),
    ('run_isolated_codex_provider_http11.py','isolation/provider-v7','devharness-gate1-provider-v7')])
def test_followup_only_changes_fixed_root_and_owned_name(script,root,name):
    spec=importlib.util.spec_from_file_location('fixed_followup_under_test',W/'scripts'/script)
    shim=importlib.util.module_from_spec(spec);spec.loader.exec_module(shim);supervisor=shim.load_supervisor()
    assert supervisor.ROOT==W/'reports/rna-native-gate1/2026-10-21-v2'/root
    assert supervisor.NAME==name and shim.SUPERVISOR_SHA256=='9e55a98ebf82a4027d63ce51bb38db455d983a55e789916c5314c51aaf1e2373'
    assert supervisor.COMMAND[-1]=='/input/protocol.py' and supervisor.COMMAND[0]=='-i'
    args=supervisor.arguments();assert args[args.index('--network')+1]=='none'
    assert '--read-only' in args and '--pull=never'in args
    assert args[args.index('--user')+1]=='501:20'
