import importlib.util
from pathlib import Path
W=Path('/Users/vajraang/Documents/ChatGPT/bio-agent-harness')

def load():
    p=W/'scripts/inspect_gate1_auth_proxy_metadata.py';s=importlib.util.spec_from_file_location('metadata_probe_under_test',p)
    m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m


def test_projection_identifies_nullable_dns_without_retaining_unknown_values():
    m=load();runtime=m.load_supervisor();network='a'*64;container='b'*64
    args=runtime.container_arguments('proxy',network)
    record={'Id':container,'Name':'/'+runtime.NAMES['proxy'],'Image':runtime.IMAGE,
        'Config':{'User':'65534:65534','Entrypoint':['/usr/bin/env'],'Cmd':args[args.index(runtime.IMAGE)+1:],
            'Env':list(m.IMAGE_ENV)+['UNTRUSTED_KEY=synthetic-private-marker']},
        'HostConfig':{'NetworkMode':network,'Dns':None,'DnsSearch':['.'],'DnsOptions':['timeout:1','attempts:1']},
        'NetworkSettings':{'Networks':{runtime.NAMES['inside']: {}}},'Mounts':[]}
    value=m.projection(record,runtime,network,container)
    assert 'Dns'in value['mismatch_fields']
    assert value['host_fields']['Dns']['observed']=={'type':'NoneType','value':None}
    assert value['host_fields']['DnsSearch']['matches_frozen_expectation']
    assert 'synthetic-private-marker'not in str(value)and 'UNTRUSTED_KEY'not in str(value)


def test_metadata_profile_remains_internal_and_no_private_mount_or_guest_start():
    m=load();runtime=m.load_supervisor()
    args=runtime.network_arguments('inside');assert '--internal'in args and '--ipv6=false'in args
    proxy=runtime.container_arguments('proxy','owned-inside-id')
    assert str(runtime.PRIVATE)not in str(proxy)
    assert all(name.startswith('devharness-gate1-auth-metadata-v1-')for name in runtime.NAMES.values())
    source=(W/'scripts/inspect_gate1_auth_proxy_metadata.py').read_text()
    assert "call(['start'"not in source and "call(['attach'"not in source and "Attachment("not in source


def test_shape_discards_unapproved_strings_and_nested_contents():
    m=load();assert m.shape('synthetic-private-marker')=={'type':'str','length':24}
    assert m.shape({'key':'synthetic-private-marker'})=={'type':'dict','length':1}
    assert m.shape(['unexpected-search-domain'])=={'type':'list','length':1}


def test_attempt_seal_blocks_replay_even_if_result_was_removed(tmp_path,monkeypatch):
    import json
    import pytest
    m=load();monkeypatch.setattr(m,'ROOT',tmp_path)
    (tmp_path/'input').mkdir();(tmp_path/'input/auth_proxy.py').write_text('# public synthetic fixture')
    (tmp_path/'docker-client').mkdir();(tmp_path/'docker-client/config.json').write_bytes(b'{"auths":{},"proxies":{}}\n')
    (tmp_path/'release.json').write_text(json.dumps({'accepted':True,'scope':'auth-proxy-create-inspect-only','source_sha256':{}}))
    (tmp_path/'attempt.json').write_text('{"owned_prior_attempt":true}')
    def forbidden_transport(*_args,**_kwargs):raise AssertionError('replay must stop before Docker')
    monkeypatch.setattr(m,'_run_process',forbidden_transport)
    with pytest.raises(FileExistsError):m.main()
    assert not(tmp_path/'result.json').exists()
