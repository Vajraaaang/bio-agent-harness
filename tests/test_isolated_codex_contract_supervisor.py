from pathlib import Path
import hashlib
import importlib.util
import pytest

PATH=Path('/Users/vajraang/Documents/ChatGPT/bio-agent-harness/scripts/run_isolated_codex_contract.py')

def load():
    spec=importlib.util.spec_from_file_location('contract_shim_under_test',PATH)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def test_contract_reuses_exact_released_profile_with_only_owned_root_and_name_changed():
    shim=load();original=shim.load_supervisor()
    assert hashlib.sha256(shim.SUPERVISOR.read_bytes()).hexdigest()==shim.SUPERVISOR_SHA256
    assert original.ROOT==shim.ROOT and original.NAME=='devharness-gate1-contract-v2'
    assert original.CLIENT==Path('/Users/vajraang/Documents/ChatGPT/bio-agent-harness/reports/rna-native-gate1/2026-10-21-v2/client/package')
    assert original.MANIFEST==original.CLIENT.parent/'package-manifest.json'
    args=original.arguments()
    assert args[-len(original.COMMAND):]==original.COMMAND
    assert original.COMMAND[-1]=='/input/protocol.py'
    assert '--pull=never' in args and '--read-only' in args
    assert args[args.index('--network')+1]=='none'
    assert args[args.index('--user')+1]=='501:20'
    assert not any('protocol-v1' in x for x in args)


def test_contract_rejects_changed_original_before_loading(tmp_path,monkeypatch):
    shim=load();changed=tmp_path/'original.py';changed.write_text("raise AssertionError('must not import changed code')")
    monkeypatch.setattr(shim,'SUPERVISOR',changed)
    with pytest.raises(PermissionError,match='released_original_supervisor_changed'):shim.load_supervisor()


def test_contract_rejects_symlink_to_original(tmp_path,monkeypatch):
    shim=load();link=tmp_path/'link';link.symlink_to(shim.SUPERVISOR)
    monkeypatch.setattr(shim,'SUPERVISOR',link)
    with pytest.raises(PermissionError):shim.load_supervisor()
