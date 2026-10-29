import importlib.util
from pathlib import Path
import pytest


def load():
    path=Path('/Users/vajraang/Documents/ChatGPT/bio-agent-harness/scripts/run_isolated_system_diagnostic.py')
    spec=importlib.util.spec_from_file_location('system_classifier_under_test',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def observation():
    return {'format':'devbench.isolated-system-classification/1','uid':65532,
        'live_generation_allowed':False,'biological_execution_allowed':False,
        'namespace_interfaces':['lo'],'sysfs_interfaces':['eth0','lo'],
        'namespace_default_route_present':False,'host_share_canary_errno':0,
        'linux_owned_canary_errno':13,'testnet_connect_errno':101}


def test_classifier_retains_mismatched_scope_instead_of_claiming_control_pass():
    value=load().validate_classification(observation())
    assert value['classification_complete']
    assert value['host_share_canary_errno']==0
    assert value['linux_owned_canary_errno']==13
    assert value['namespace_interfaces']!=value['sysfs_interfaces']
    assert 'controls_passed' not in value


@pytest.mark.parametrize('key,value',[('namespace_interfaces',['untrusted\ntext']),('sysfs_interfaces',['eth0']*65),
    ('uid','65532'),('live_generation_allowed',True),('testnet_connect_errno',None),('extra','unapproved')])
def test_classifier_rejects_unbounded_or_unauthorized_output(key,value):
    observed=observation();observed[key]=value
    with pytest.raises(PermissionError):load().validate_classification(observed)
