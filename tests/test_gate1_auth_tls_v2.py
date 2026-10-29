from pathlib import Path
import importlib.util
W=Path('/Users/vajraang/Documents/ChatGPT/bio-agent-harness')


def test_fresh_tls_v2_changes_only_owned_root_names_and_outer_permission():
    spec=importlib.util.spec_from_file_location('tls_v2_under_test',W/'scripts/run_gate1_auth_tls_probe_v2.py')
    shim=importlib.util.module_from_spec(spec);spec.loader.exec_module(shim);runtime=shim.load_supervisor()
    assert shim.SUPERVISOR_SHA256=='d2987a1f9f3ab53dffb3e311826e5733e205589f40caa8de08a734ba23a5550a'
    assert runtime.MODE=='tls-only'and runtime.ROOT==W/'reports/rna-native-gate1/2026-10-21-v2/auth-preparation/tls-attempt-v2'
    assert all(value.startswith('devharness-gate1-auth-tls-v2-')for value in runtime.NAMES.values())
    args=runtime.container_arguments('probe','owned-inside-id')
    assert args[-1]=='tls-only'and str(runtime.PRIVATE)not in str(args)
    assert args[args.index('--network')+1]=='owned-inside-id'
