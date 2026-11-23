"""Build only the staged public-software context against the local Unix socket.

This creates a local software image, never runs a scientific workflow, and never
publishes to an external registry. The admission label is sealed in a second
build because its values describe installed file content from the first build.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time


def main():
    root=Path(sys.argv[1]).resolve()
    if not root.is_absolute() or not (root/'context'/'context-software-sha256.json').is_file():
        raise ValueError('Prepared absolute public-software staging root required')
    config=root/'docker-config'; config.mkdir(exist_ok=True)
    env={k:v for k,v in os.environ.items() if not k.startswith(('DOCKER','BUILDX','BUILDKIT'))}
    env['DOCKER_CONFIG']=str(config)
    docker=['docker','--host','unix:///var/run/docker.sock']
    def run(args,timeout=120):
        return subprocess.check_output(docker+args,env=env,timeout=timeout)
    info=json.loads(run(['info','--format','{{json .}}']))
    if info['OSType']!='linux' or info['Architecture'] not in ('aarch64','arm64'):
        raise ValueError('Native local Linux arm64 daemon required')
    (root/'local-engine.json').write_bytes(json.dumps({k:info[k] for k in ('ID','ServerVersion','OSType','Architecture')},sort_keys=True).encode())
    builders=[json.loads(line) for line in run(['buildx','ls','--format','{{json .}}']).decode().splitlines()]
    if not builders or any(b['Driver']!='docker' or any(n['Endpoint']!='default' for n in b['Nodes']) for b in builders):
        raise ValueError('Only the verified local default Docker builder is permitted')
    (root/'builders-at-build.json').write_text(json.dumps(builders,sort_keys=True,indent=2))
    def build(context,tag,log):
        if shutil.disk_usage(root).free < 20*1024**3: raise RuntimeError('20GiB disk floor reached')
        args=docker+['buildx','build','--builder','default','--platform','linux/arm64','--network','none','--progress','plain','--provenance=false','--load','-t',tag,str(context)]
        with (root/log).open('wb') as output:
            p=subprocess.Popen(args,env=env,stdout=output,stderr=subprocess.STDOUT,start_new_session=True)
            start=time.monotonic()
            while p.poll() is None:
                if time.monotonic()-start>3600 or shutil.disk_usage(root).free<20*1024**3:
                    os.killpg(p.pid,signal.SIGINT);p.wait(timeout=30)
                    raise RuntimeError('Bounded local build stopped at time/disk limit')
                time.sleep(2)
            if p.returncode: raise RuntimeError('Software build failed; retained '+str(root/log))
    base='devbench-community-rna:software-20261028-dependency-correction-1'
    final='devbench-community-rna:admitted-20261028-dependency-correction-1'
    build(root/'context',base,'build-software.log')
    cid=run(['create','--network','none','--read-only','--entrypoint','/bin/true',base]).decode().strip()
    try:
        for name in ('admission.json','asset-manifest.json'):
            run(['cp',cid+':/opt/devbench/'+name,str(root/name)])
    finally: run(['rm',cid])
    seal=root/'seal-context';seal.mkdir(exist_ok=True)
    inventory=json.loads((root/'admission.json').read_bytes())
    if 'image' in inventory: raise ValueError('Self-referential image digest in installed admission')
    canonical=json.dumps(inventory,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False)
    (seal/'Dockerfile').write_text('FROM '+base+'\nLABEL devbench.community.inventory='+json.dumps(canonical)+'\n')
    build(seal,final,'build-seal.log')
    identity=json.loads(run(['image','inspect',final]))[0]
    receipt={'tag':final,'config_image_id':identity['Id'],'repo_digests':identity.get('RepoDigests',[]),
        'inventory_sha256':hashlib.sha256((root/'admission.json').read_bytes()).hexdigest(),
        'asset_manifest_sha256':hashlib.sha256((root/'asset-manifest.json').read_bytes()).hexdigest(),
        'pipeline_executed':False,'scientific_qualification':'not_established'}
    (root/'image-build-receipt.json').write_text(json.dumps(receipt,sort_keys=True,indent=2)+'\n')
    print(json.dumps(receipt,sort_keys=True))
    if not receipt['repo_digests']:
        print('Local image has no RepoDigest; do not admit using config ImageID. Coordinate a loopback registry with the parent.',file=sys.stderr)
        return 2
    return 0


if __name__=='__main__':
    code=1
    try: code=main()
    finally:
        Path(sys.argv[1],'build-terminal-status.json').write_text(json.dumps({'driver_pid':os.getpid(),'exit_code':code,'ended_unix':time.time()},sort_keys=True)+'\n')
    sys.exit(code)
