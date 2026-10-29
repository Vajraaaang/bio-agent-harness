import importlib.util
from pathlib import Path
from copy import deepcopy
import pytest

PATH = Path('/Users/vajraang/Documents/ChatGPT/bio-agent-harness/scripts/run_isolated_codex_protocol.py')

def load():
    spec = importlib.util.spec_from_file_location('isolated_supervisor_under_test', PATH)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


def record(module):
    return {'Id': 'a'*64, 'Name': '/'+module.NAME, 'Image': module.IMAGE,
        'Config': {'User': '501:20', 'Entrypoint': ['/usr/bin/env'], 'Cmd': module.COMMAND, 'Env': list(module.IMAGE_ENV)},
        'HostConfig': {'NetworkMode':'none','ReadonlyRootfs':True,'Privileged':False,'Memory':1073741824,
            'MemorySwap':1073741824,'NanoCpus':1000000000,'PidsLimit':64,'PidMode':'','IpcMode':'private',
            'PublishAllPorts':False,'CapDrop':['ALL'],'SecurityOpt':['no-new-privileges=true'],
            'LogConfig':{'Type':'none','Config':{}},'Tmpfs':{'/tmp':'rw,nosuid,nodev,noexec,size=67108864,mode=1777'}},
        'Mounts':[{'Type':'bind','Source':str(source),'Destination':destination,'RW':rw}
            for source,destination,rw in [(module.CLIENT,'/client',False),(module.ROOT/'input','/input',False),(module.ROOT/'output','/output',True)]]}


def test_fixed_profile_does_not_expose_credentials_or_socket():
    module = load(); args=module.arguments()
    assert '--pull=never' in args and '--read-only' in args
    assert args[args.index('--user')+1]=='501:20'
    assert '--network' in args and args[args.index('--network')+1]=='none'
    mounts=[args[i+1] for i,x in enumerate(args) if x=='--mount']
    assert len(mounts)==3 and not any('docker.sock' in x or '.codex' in x for x in mounts)
    assert module.COMMAND[-1]=='/input/protocol.py'
    assert module.COMMAND[0]=='-i'


def test_actual_protocol_profile_rejects_widened_network_or_mounts():
    module=load(); good=record(module);module.verify_record(good)
    for key,value in [('NetworkMode','bridge'),('Privileged',True),('CapAdd',['SYS_ADMIN']),('ReadonlyRootfs',False)]:
        bad=deepcopy(good);bad['HostConfig'][key]=value
        with pytest.raises(PermissionError): module.verify_record(bad)
    for index in (0,1):
        bad=deepcopy(good);bad['Mounts'][index]['RW']=True
        with pytest.raises(PermissionError): module.verify_record(bad)
    bad=deepcopy(good);bad['Config']['Env'].append('OPENAI_API_KEY=untrusted')
    with pytest.raises(PermissionError): module.verify_record(bad)


def test_digest_refuses_symlinks_and_large_files(tmp_path):
    module=load(); target=tmp_path/'file';target.write_bytes(b'owned')
    link=tmp_path/'link';link.symlink_to(target)
    with pytest.raises(PermissionError): module.digest(link)
    with target.open('wb') as stream: stream.truncate(300000001)
    with pytest.raises(PermissionError): module.digest(target)


@pytest.mark.parametrize('problem', ['late_custody_drift', 'attached_transport_failure'])
def test_protocol_supervisor_rejects_late_custody_or_transport_failure(tmp_path, monkeypatch, problem):
    import json
    module=load(); monkeypatch.setattr(module,'ROOT',tmp_path)
    (tmp_path/'output').mkdir();(tmp_path/'docker-client').mkdir()
    (tmp_path/'release.json').write_text(json.dumps({'accepted':True,'source_sha256':{}}))
    checks=[]
    def custody(_release):
        checks.append(True)
        if problem=='late_custody_drift' and len(checks)>=8:raise PermissionError('changed')
    monkeypatch.setattr(module,'verify_custody',custody)
    created=record(module);created['State']={'Status':'created','Running':False}
    stopped=deepcopy(created);stopped['State']={'Status':'exited','Running':False,'ExitCode':0}
    inspections=[]
    def transport(argv,**_kwargs):
        args=argv[5:]
        result={'stdout':b'','stderr':b'','exit_code':0,'error':None,'process_reaped':True,'launch_confirmed':True}
        if args[0]=='create':result['stdout']=b'a'*64+b'\n'
        elif args[0:2]==['container','inspect']:
            inspections.append(True)
            if len(inspections)<3:result['stdout']=json.dumps([created if len(inspections)==1 else stopped]).encode()
            else:result.update(exit_code=1,stderr=b'No such container')
        elif args[0]=='start':
            output=tmp_path/'output/protocol';output.mkdir();(output/'result.json').write_text('{"status":"observed_protocol_only"}')
            if problem=='attached_transport_failure':result.update(error='TimeoutError',process_reaped=False)
        elif args[0:2]==['container','rm']:result['stdout']=b'a'*64+b'\n'
        return result
    monkeypatch.setattr(module,'_run_process',transport)
    assert module.main()==1
    result=json.loads((tmp_path/'result.json').read_text())
    assert result['owned_container_removed'] and result['removal_verified']
    if problem=='late_custody_drift':assert result['cleanup_or_custody_uncertain']
    else:assert result['attached_error']=='TimeoutError' and not result['attached_reaped']
