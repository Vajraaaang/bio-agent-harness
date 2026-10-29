import ast
import importlib.util
from pathlib import Path
import pytest
W=Path('/Users/vajraang/Documents/ChatGPT/bio-agent-harness')
PATH=W/'scripts/verify_gate1_login_proxy_categories.py'

def load():
    spec=importlib.util.spec_from_file_location('login_categories_under_test',PATH)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def test_categories_emit_only_enum_no_error_values_or_hashes():
    module=load();raw=b'Error loading configuration: synthetic-untrusted-value'
    assert module.classify_error_prefixes([raw])==['configuration_load_failure']
    assert 'synthetic-untrusted-value' not in str(module.classify_error_prefixes([raw]))
    assert module.classify_error_prefixes([b'unknown bytes'])==['unclassified_bounded_output']
    with pytest.raises(ValueError):module.classify_error_prefixes([b'x'*257])


def test_fixed_login_and_environment_unchanged_from_retained_failure():
    old=ast.parse((W/'scripts/verify_gate1_login_proxy.py').read_text());new=ast.parse(PATH.read_text())
    def function(tree,name):return next(n for n in tree.body if isinstance(n,ast.FunctionDef)and n.name==name)
    assert ast.dump(function(old,'login_arguments'))==ast.dump(function(new,'login_arguments'))
    def env(tree):return next(n for n in ast.walk(function(tree,'main'))if isinstance(n,ast.Assign)and any(isinstance(t,ast.Name)and t.id=='environment'for t in n.targets))
    assert ast.dump(env(old))==ast.dump(env(new))
    assert 'time.monotonic()+15' in PATH.read_text() and 'MAX_CAPTURE=16384' in PATH.read_text()
