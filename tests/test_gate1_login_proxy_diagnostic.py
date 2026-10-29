from pathlib import Path
import importlib.util
import pytest

PATH=Path('/Users/vajraang/Documents/ChatGPT/bio-agent-harness/scripts/verify_gate1_login_proxy.py')

def load():
    spec=importlib.util.spec_from_file_location('login_proxy_fixture_under_test',PATH)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def test_fixed_login_has_no_inference_or_unqualified_issuer_override():
    module=load();args=module.login_arguments()
    assert args[-2:]==['login','--device-auth']
    assert 'exec' not in args and '--ignore-user-config' not in args
    assert not any('base_url' in x or 'issuer' in x for x in args)


def test_relay_projects_only_known_authority_and_header_names():
    module=load()
    projected=module.inspect_connect(b'CONNECT auth.openai.com:443 HTTP/1.1\r\nHost: auth.openai.com:443\r\n\r\n')
    assert projected=={'authority':'auth.openai.com:443','header_names':['host'],'matches_supported_header_grammar':True}
    value=module.inspect_connect(b'CONNECT untrusted.invalid:443 HTTP/1.1\r\nHost: untrusted.invalid:443\r\n\r\n')
    assert value['authority']=='unexpected_authority'
    assert 'untrusted.invalid' not in str(value)


def test_auth_header_values_are_never_retained():
    value=load().inspect_connect(b'CONNECT auth.openai.com:443 HTTP/1.1\r\nHost: auth.openai.com:443\r\nProxy-Authorization: untrusted-synthetic-value\r\n\r\n')
    assert value['matches_supported_header_grammar'] is False
    assert 'untrusted-synthetic-value' not in str(value)
    assert 'proxy-authorization' in value['header_names']


@pytest.mark.parametrize('raw',[b'x'*4097,b'GET / HTTP/1.1\r\nHost: x\r\n\r\n',b'CONNECT auth.openai.com:443 HTTP/1.1\r\nBad Header: x\r\n\r\n'])
def test_malformed_or_oversized_proxy_headers_rejected_without_dump(raw):
    with pytest.raises(ValueError):load().inspect_connect(raw)


def test_login_proxy_shim_reuses_exact_network_none_enclosure():
    path=Path('/Users/vajraang/Documents/ChatGPT/bio-agent-harness/scripts/run_gate1_login_proxy_diagnostic.py')
    spec=importlib.util.spec_from_file_location('login_proxy_shim_under_test',path)
    shim=importlib.util.module_from_spec(spec);spec.loader.exec_module(shim)
    supervisor=shim.load_supervisor()
    assert supervisor.NAME=='devharness-gate1-login-proxy-v1'
    assert str(supervisor.ROOT).endswith('/auth-preparation/proxy-compat-v1')
    args=supervisor.arguments()
    assert args[args.index('--network')+1]=='none'
    assert supervisor.COMMAND[-1]=='/input/protocol.py' and '--read-only' in args
    assert supervisor.CLIENT==Path('/Users/vajraang/Documents/ChatGPT/bio-agent-harness/reports/rna-native-gate1/2026-10-21-v2/client/package')


@pytest.mark.parametrize('uncertain',[False,True])
def test_login_group_target_remains_unreaped_until_cleanup(monkeypatch,uncertain):
    from types import SimpleNamespace
    module=load();events=[]
    class Stream:
        def __init__(self,fd):self.fd=fd
        def fileno(self):return self.fd
        def close(self):pass
    class Process:
        pid=12345;returncode=None
        stdout=Stream(11);stderr=Stream(12)
        def wait(self,timeout):events.append('reap');self.returncode=1;return 1
    process=Process()
    class Selector:
        def __init__(self):self.values={}
        def __enter__(self):return self
        def __exit__(self,*_args):pass
        def register(self,stream,*_args):self.values[stream.fd]=SimpleNamespace(fd=stream.fd,fileobj=stream)
        def unregister(self,stream):self.values.pop(stream.fd)
        def get_map(self):return self.values
        def select(self,*_args):
            if uncertain:raise TimeoutError('owned synthetic uncertainty')
            return [(key,None)for key in self.values.values()]
    monkeypatch.setattr(module.subprocess,'Popen',lambda *_args,**_kwargs:process)
    monkeypatch.setattr(module.selectors,'DefaultSelector',Selector)
    monkeypatch.setattr(module.os,'set_blocking',lambda *_args:None)
    reads={11:iter([b'Enter this one-',b'time code',b'']),12:iter([b''])}
    monkeypatch.setattr(module.os,'read',lambda fd,_maximum:next(reads[fd]))
    monkeypatch.setattr(module.os,'waitid',lambda *_args:events.append('unreaped_observation')or object(),raising=False)
    for name,value in [('P_PID',1),('WEXITED',4),('WNOWAIT',0x1000000),('WNOHANG',1)]:
        monkeypatch.setattr(module.os,name,value,raising=False)
    monkeypatch.setattr(module.os,'killpg',lambda *_args:events.append('kill_group'))
    result=module.execute_login({})
    assert events.index('kill_group')<events.index('reap')
    assert result['process_reaped'] is True and result['group_cleanup_uncertain'] is False
    assert (result['error_category']=='TimeoutError')is uncertain
    assert result['device_prompt_observed'] is (not uncertain)
