"""Require one security profile across two measured Docker serializations."""
from pathlib import Path
import importlib.util,json
import pytest
W=Path('/Users/vajraang/Documents/ChatGPT/bio-agent-harness')
BASE=W/'reports/rna-native-gate1/2026-10-21-v2'

def load(version='v3'):
    name='run_gate1_egress_diagnostic.py' if version=='v1'else 'run_gate1_egress_diagnostic_'+version+'.py'
    spec=importlib.util.spec_from_file_location('egress_'+version,W/'scripts'/name);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m

def measured_records(m):
    implicit=json.loads((BASE/'egress/network-profile-v2/result.json').read_text())['actual_network']
    explicit=json.loads((BASE/'egress-v2/result.json').read_text())['networks']['inside']
    return [{**r,'Name':m.NAMES['inside'],'Containers':{}} for r in (implicit,explicit)]

def test_real_measurements_prove_two_equivalent_serializations():
    implicit,explicit=measured_records(load('v2'))
    assert implicit['EnableIPv4']is explicit['EnableIPv4']is True
    assert implicit['Options']=={'com.docker.network.bridge.inhibit_ipv4':'true','com.docker.network.enable_ipv4':'true'}
    assert explicit['Options']=={'com.docker.network.bridge.inhibit_ipv4':'true'}
    with pytest.raises(PermissionError):load('v2').verify_network(explicit,'inside',set())
    old=load('v1');implicit['Name']=old.NAMES['inside']
    with pytest.raises(PermissionError):old.verify_network(implicit,'inside',set())

@pytest.mark.parametrize('record_index',[0,1])
def test_both_captured_encodings_have_same_canonical_security_profile(record_index):
    m=load();record=measured_records(m)[record_index]
    assert m.canonical_ipv4_options(record)=={'ipv4_assignment_enabled':True,'bridge_host_ipv4_inhibited':True}
    m.verify_network(record,'inside',set())

@pytest.mark.parametrize('change',[
    {'Options':{}},
    {'Options':{'com.docker.network.enable_ipv4':'true'}},
    {'Options':{'com.docker.network.bridge.inhibit_ipv4':'false'}},
    {'Options':{'com.docker.network.bridge.inhibit_ipv4':True}},
    {'Options':{'com.docker.network.bridge.inhibit_ipv4':'true','com.docker.network.enable_ipv4':'false'}},
    {'Options':{'com.docker.network.bridge.inhibit_ipv4':'true','com.docker.network.enable_ipv4':True}},
    {'Options':{'com.docker.network.bridge.inhibit_ipv4':'true','unknown':'true'}},
    {'Options':{'com.docker.network.bridge.inhibit_ipv4':'true','com.docker.network.enable_ipv4':'true','unknown':'true'}},
    {'Options':None},{'Options':[]},
    {'EnableIPv4':False},{'EnableIPv4':1},{'EnableIPv4':'true'},{'EnableIPv4':None},
    {'Internal':False},{'EnableIPv6':True},{'Driver':'overlay'},
    {'Containers':{'a'*64:{}}},
])
def test_invalid_or_unreviewed_security_profile_remains_rejected(change):
    m=load();record=measured_records(m)[0];record.update(change)
    with pytest.raises(PermissionError):m.verify_network(record,'inside',set())

def test_new_attempt_is_isolated_and_other_caps_retained():
    m=load();assert m.ROOT==BASE/'egress-v3'
    assert set(m.NAMES.values())=={'devharness-gate1-egress-v3-'+k for k in ('inside','mock','peer','proxy','client')}
    args=m.container_arguments('peer','fixed-network-id',[])
    for name,value in [('--memory','134217728'),('--memory-swap','134217728'),('--pids-limit','16'),('--cpus','1'),('--user','65534:65534'),('--dns','127.0.0.1')]:assert args[args.index(name)+1]==value
    assert '--read-only'in args and '--pull=never'in args and '--publish'not in args
