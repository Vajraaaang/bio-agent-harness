import ast
import importlib.util
from pathlib import Path
W=Path('/Users/vajraang/Documents/ChatGPT/bio-agent-harness')

def load(name):
    spec=importlib.util.spec_from_file_location('corrected_login_under_test',W/'scripts'/name)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def test_corrected_login_removes_only_source_rejected_flag():
    old=load('verify_gate1_login_proxy_categories.py');new=load('verify_gate1_login_proxy_corrected.py')
    assert new.login_arguments()==[x for x in old.login_arguments()if x!='--strict-config']
    assert new.login_arguments()[-2:]==['login','--device-auth']
    def env(path):
        tree=ast.parse(path.read_text())
        return next(n for n in ast.walk(tree)if isinstance(n,ast.Assign)and any(isinstance(t,ast.Name)and t.id=='environment'for t in n.targets))
    assert ast.dump(env(W/'scripts/verify_gate1_login_proxy_categories.py'))==ast.dump(env(W/'scripts/verify_gate1_login_proxy_corrected.py'))
    assert 'time.monotonic()+15' in (W/'scripts/verify_gate1_login_proxy_corrected.py').read_text()


def test_dispatcher_proves_login_rejection_and_exact_retained_byte_count():
    source=(W/'reports/rna-native-gate1/2026-10-21-v2/auth-preparation/source/public-cli-main-0.147.0.rs').read_text()
    assert 'Some(Subcommand::Login(_)) => Some("login")' in source
    assert '`--strict-config` is not supported for `codex {subcommand}`' in source
    assert len(b'Error: `--strict-config` is not supported for `codex login`\n')==60


def test_corrected_preparation_supersedes_without_auth_or_private_state_creation():
    plan=load('prepare_gate1_device_auth_v2.py').prepare_plan()
    assert '--strict-config' not in plan['arguments'] and plan['arguments'][-2:]==['login','--device-auth']
    assert plan['launch_allowed'] is False and plan['private_root_created_by_preparation'] is False
    assert plan['supersedes_plan'].endswith('/device-auth-plan-v1.json')


def test_corrected_supervisor_keeps_exact_network_none_profile():
    supervisor=load('run_gate1_login_proxy_corrected.py').load_supervisor()
    assert supervisor.NAME=='devharness-gate1-login-proxy-v3' and str(supervisor.ROOT).endswith('/proxy-compat-v3')
    args=supervisor.arguments();assert args[args.index('--network')+1]=='none' and '--read-only'in args
