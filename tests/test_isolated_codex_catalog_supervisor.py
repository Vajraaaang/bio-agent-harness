import importlib.util
from pathlib import Path
import pytest

PATH=Path('/Users/vajraang/Documents/ChatGPT/bio-agent-harness/scripts/run_isolated_codex_catalog.py')

def load():
    spec=importlib.util.spec_from_file_location('catalog_shim_under_test',PATH)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def test_catalog_reuses_original_profile_with_fixed_new_root_and_name():
    shim=load();supervisor=shim.load_supervisor()
    assert supervisor.ROOT==Path('/Users/vajraang/Documents/ChatGPT/bio-agent-harness/reports/rna-native-gate1/2026-10-21-v2/isolation/catalog-v4')
    assert supervisor.NAME=='devharness-gate1-catalog-v4'
    assert supervisor.COMMAND[-1]=='/input/protocol.py' and supervisor.COMMAND[0]=='-i'
    assert supervisor.CLIENT==Path('/Users/vajraang/Documents/ChatGPT/bio-agent-harness/reports/rna-native-gate1/2026-10-21-v2/client/package')
    args=supervisor.arguments()
    assert args[args.index('--network')+1]=='none' and '--read-only' in args and '--pull=never' in args
    assert args[args.index('--user')+1]=='501:20'
    assert all('contract-v2' not in x and 'closed-home-v3' not in x and 'protocol-v1' not in x for x in args)


@pytest.mark.parametrize('symlink',[False,True])
def test_catalog_rejects_changed_or_symlinked_original(tmp_path,monkeypatch,symlink):
    shim=load();candidate=tmp_path/'candidate'
    if symlink:candidate.symlink_to(shim.SUPERVISOR)
    else:candidate.write_text("raise AssertionError('do not import')")
    monkeypatch.setattr(shim,'SUPERVISOR',candidate)
    with pytest.raises(PermissionError):shim.load_supervisor()
