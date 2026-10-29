import importlib.util
from pathlib import Path
import pytest
W=Path('/Users/vajraang/Documents/ChatGPT/bio-agent-harness')


def load():
    p=W/'scripts/run_gate1_personal_device_auth_v3.py';s=importlib.util.spec_from_file_location('auth_v3_shim_under_test',p)
    m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m


def test_fresh_shim_changes_only_root_names_private_epoch():
    m=load();controller=m.prepared_controller()
    spec=importlib.util.spec_from_file_location('unchanged_auth_v2_reference',m.CONTROLLER)
    old=importlib.util.module_from_spec(spec);spec.loader.exec_module(old)
    changed={name for name in vars(old)if name.isupper()and getattr(old,name)!=getattr(controller,name)}
    assert changed=={'ROOT','NAMES','PRIVATE'}
    assert controller.MODE=='device-login'and str(controller.PRIVATE).endswith('/epoch2')
    assert controller.ROOT==m.ROOT and controller.NAMES==m.NAMES
    assert controller.TLS_ROOT==old.TLS_ROOT and controller.CLIENT==old.CLIENT
    assert controller.container_arguments('client','inside-id')[-1]=='device-login'
    assert '315s'in controller.container_arguments('client','inside-id')
    assert '315s'in controller.container_arguments('proxy','outside-id')


def test_changed_controller_is_rejected_without_private_access(tmp_path,monkeypatch):
    m=load();p=tmp_path/'changed_public.py';p.write_text('# owned synthetic changed source')
    monkeypatch.setattr(m,'CONTROLLER',p)
    with pytest.raises(PermissionError):m.prepared_controller()


def test_unexpected_arguments_do_not_launch_controller(monkeypatch):
    m=load();monkeypatch.setattr(m.sys,'argv',['fixed-auth','unexpected'])
    monkeypatch.setattr(m,'prepared_controller',lambda:pytest.fail('no launch'))
    with pytest.raises(PermissionError):m.main()
