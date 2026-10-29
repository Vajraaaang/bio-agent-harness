"""Source/owned-env checks only; never execute Docker or any helper."""
import ast
import hashlib
import importlib.util
import json
from pathlib import Path
W=Path('/Users/vajraang/Documents/ChatGPT/bio-agent-harness')
BOUNDARY_SHA='690fd930ee9f390480f85629ab540f5445d0f98164100cf2add25efe4744f69d'


def test_closed_transport_environment_ignores_ambient_helper_search_and_credentials(tmp_path,monkeypatch):
    p=W/'devbench/analysis/codex_isolated_boundary.py'
    assert hashlib.sha256(p.read_bytes()).hexdigest()==BOUNDARY_SHA
    s=importlib.util.spec_from_file_location('fixed_transport_environment_under_test',p)
    m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
    monkeypatch.setenv('PATH','/owned-synthetic/Resources/bin:/usr/local/bin')
    monkeypatch.setenv('DOCKER_CONFIG','/owned-synthetic/config')
    monkeypatch.setenv('DOCKER_CONTEXT','owned-synthetic-context')
    monkeypatch.setenv('HOME','/owned-synthetic/home')
    owned=tmp_path/'empty-owned-docker-config';owned.mkdir()
    assert m.closed_environment(owned)=={'PATH':'/usr/bin:/bin','HOME':str(owned),'LANG':'C'}
    assert m.DOCKER=='/Applications/Docker.app/Contents/Resources/bin/docker'


def test_both_frozen_controllers_bind_every_docker_child_to_same_closed_environment():
    for name in ['run_gate1_personal_device_auth_v2.py','run_gate1_account_read_probe.py']:
        source=(W/'scripts'/name).read_text();tree=ast.parse(source)
        main=next(n for n in tree.body if isinstance(n,ast.FunctionDef)and n.name=='main')
        assignments={target.id:node.value for node in ast.walk(main)if isinstance(node,ast.Assign)
            for target in node.targets if isinstance(target,ast.Name)and target.id in ['prefix','environment']}
        prefix=assignments['prefix'];assert isinstance(prefix,ast.List)
        assert ast.unparse(prefix)=="[DOCKER, '--config', str(ROOT / 'docker-client'), '--host', SOCKET]"
        assert ast.unparse(assignments['environment'])=="closed_environment(ROOT / 'docker-client')"
        transport=[n for n in ast.walk(main)if isinstance(n,ast.Call)and isinstance(n.func,ast.Name)and n.func.id=='_run_process']
        assert len(transport)==1
        assert next(k.value for k in transport[0].keywords if k.arg=='environment').id=='environment'
        if 'personal_device' in name:
            cls=next(n for n in tree.body if isinstance(n,ast.ClassDef)and n.name=='Attachment')
            launch=next(n for n in ast.walk(cls)if isinstance(n,ast.Call)and ast.unparse(n.func)=='subprocess.Popen')
            assert next(k.value for k in launch.keywords if k.arg=='env').id=='environment'
            attach=next(n for n in ast.walk(main)if isinstance(n,ast.Call)and isinstance(n.func,ast.Name)and n.func.id=='Attachment')
            assert attach.args[1].id=='environment'


def test_unreleased_v4_and_epoch3_proposals_pin_exact_safe_environment_source():
    b=W/'reports/rna-native-gate1/2026-10-21-v2'
    for path in [b/'auth-preparation/login-attempt-v4/proposed-release.json',b/'provider/account-probe/epoch3-attempt-v2/proposed-release.json']:
        value=json.loads(path.read_text())
        assert value['accepted']is False and value['source_preparation_only']is True
        assert value['source_sha256'][str(W/'devbench/analysis/codex_isolated_boundary.py')]==BOUNDARY_SHA
