"""Actual Docker metadata regression; reject all unreviewed profile variation."""
from pathlib import Path
import importlib.util,json
import pytest
W=Path('/Users/vajraang/Documents/ChatGPT/bio-agent-harness')
OBSERVED=W/'reports/rna-native-gate1/2026-10-21-v2/egress/network-profile-v2/result.json'

def load(version='v2'):
    name='run_gate1_egress_diagnostic_v2.py'if version=='v2'else'run_gate1_egress_diagnostic.py'
    spec=importlib.util.spec_from_file_location('egress_'+version,W/'scripts'/name);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m

def captured(m):
    actual=json.loads(OBSERVED.read_text())['actual_network']
    return {**actual,'Name':m.NAMES['inside'],'Containers':{}}

def test_captured_engine_profile_identifies_original_rejection():
    m=load('v1');actual=captured(m)
    assert actual['Options']=={'com.docker.network.bridge.inhibit_ipv4':'true','com.docker.network.enable_ipv4':'true'}
    assert actual['EnableIPv4']is True
    with pytest.raises(PermissionError):m.verify_network(actual,'inside',set())

def test_captured_engine_profile_is_exactly_accepted_v2():
    m=load();m.verify_network(captured(m),'inside',set())

@pytest.mark.parametrize('change',[
    {'Options':{'com.docker.network.bridge.inhibit_ipv4':'true','com.docker.network.enable_ipv4':'false'}},
    {'Options':{'com.docker.network.bridge.inhibit_ipv4':'false','com.docker.network.enable_ipv4':'true'}},
    {'Options':{'com.docker.network.bridge.inhibit_ipv4':'true','com.docker.network.enable_ipv4':'true','unreviewed':'true'}},
    {'Options':{'com.docker.network.bridge.inhibit_ipv4':'true'}},
    {'EnableIPv4':False},{'EnableIPv4':1},{'Internal':False},{'EnableIPv6':True},
    {'Containers':{'a'*64:{}}},
])
def test_any_unreviewed_profile_variation_rejected(change):
    m=load();actual=captured(m);actual.update(change)
    with pytest.raises(PermissionError):m.verify_network(actual,'inside',set())

def test_fresh_root_names_and_ipv4flag():
    m=load();assert m.ROOT==W/'reports/rna-native-gate1/2026-10-21-v2/egress-v2'
    assert set(m.NAMES.values())=={'devharness-gate1-egress-v2-'+k for k in ('inside','mock','peer','proxy','client')}
    source=(W/'scripts/run_gate1_egress_diagnostic_v2.py').read_text()
    assert "'--internal','--ipv4=true','--ipv6=false'"in source
