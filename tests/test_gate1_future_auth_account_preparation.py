import importlib.util
import json
from pathlib import Path
import pytest
W=Path('/Users/vajraang/Documents/ChatGPT/bio-agent-harness')


def load(name):
    p=W/'scripts'/name;s=importlib.util.spec_from_file_location('future_source_'+name.replace('.','_'),p)
    m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m


def test_auth_v4_only_changes_root_names_future_epoch():
    m=load('run_gate1_personal_device_auth_v4.py');current=m.prepared_controller()
    spec=importlib.util.spec_from_file_location('frozen_auth_reference',m.CONTROLLER);old=importlib.util.module_from_spec(spec);spec.loader.exec_module(old)
    changes={name for name in vars(old)if name.isupper()and getattr(old,name)!=getattr(current,name)}
    assert changes=={'ROOT','NAMES','PRIVATE'}
    assert str(current.PRIVATE).endswith('/epoch3')and str(current.ROOT).endswith('/login-attempt-v4')
    assert current.MODE=='device-login'and '315s'in current.container_arguments('client','inside-id')


def test_account_binding_keeps_networknone_and_only_literal_epoch3_auth_mount():
    m=load('run_gate1_account_read_probe_v2.py');current=m.prepared_controller()
    assert current.AUTH==m.PRIVATE/'codex-home/auth.json'and current.DEVICE_RESULT==m.DEVICE_RESULT
    assert current.command()[-1]=='/input/account_probe.py'and '25s'in current.command()
    args=current.arguments();assert args[args.index('--network')+1]=='none'
    assert current.mounts()[-1]==(m.AUTH,'/probe-home/auth.json')
    assert current.CLIENT==m.REPORTS/'client/package'


def test_missing_actual_v4_success_stops_before_private_metadata_or_docker(monkeypatch):
    m=load('run_gate1_account_read_probe_v2.py');current=m.prepared_controller()
    monkeypatch.setattr(current.sys,'argv',['fixed-account'])
    monkeypatch.setattr(current,'auth_metadata',lambda:pytest.fail('private metadata forbidden'))
    monkeypatch.setattr(current,'_run_process',lambda *_a,**_kw:pytest.fail('Docker forbidden'))
    with pytest.raises((PermissionError,FileNotFoundError)):current.main()


@pytest.mark.parametrize('missing',['private_root','private_epoch_initialization_attempted','private_epoch_created','private_epoch_preserved_without_inspection'])
def test_epoch_crosslink_rejects_missing_or_other_epoch_using_public_negative_only(monkeypatch,missing):
    m=load('run_gate1_account_read_probe_v2.py')
    # This intentionally invalid public fixture is never a successful proof/review.
    invalid={'private_root':'/owned-invalid-epoch','private_epoch_initialization_attempted':False,
        'private_epoch_created':False,'private_epoch_preserved_without_inspection':False}
    invalid.pop(missing,None)
    monkeypatch.setattr(m,'public_json',lambda _enclosure,path:invalid if path==m.DEVICE_RESULT else {'accepted':False})
    with pytest.raises(PermissionError):m.crosslink_epoch(object())


def test_changed_public_controller_and_extra_arguments_rejected_without_private_access(tmp_path,monkeypatch):
    for name in ['run_gate1_personal_device_auth_v4.py','run_gate1_account_read_probe_v2.py']:
        m=load(name);p=tmp_path/name;p.write_text('# owned invalid public source')
        monkeypatch.setattr(m,'CONTROLLER',p)
        with pytest.raises(PermissionError):m.prepared_controller()
        monkeypatch.setattr(m.sys,'argv',['fixed','unexpected'])
        monkeypatch.setattr(m,'prepared_controller',lambda:pytest.fail('no controller launch'))
        with pytest.raises(PermissionError):m.main()
