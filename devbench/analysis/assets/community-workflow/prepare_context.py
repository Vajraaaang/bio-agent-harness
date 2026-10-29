"""Stage only public, checksum-locked software for the local OCI recipe.

Run with an absolute empty staging directory. No repository-wide Docker context,
Docker configuration, scientific data, or credentials are read by this script.
"""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import shutil
import sys
import tarfile
import urllib.request

HERE = Path(__file__).resolve().parent
ASSETS = {
    'upstream.tar.gz': ('https://api.github.com/repos/nf-core/differentialabundance/tarball/30ed7741fc392127156c2fb10cfa3d69d216b54b', '241c5de9229b5322afe894bd5d7a7987ae04ceba3454a06d61bef2a30c8624d8'),
    'nextflow-25.10.4-dist': ('https://github.com/nextflow-io/nextflow/releases/download/v25.10.4/nextflow-25.10.4-dist', '60db869a7618783e3ee60183a2b0c6c50ffc67221eabec60800b904991786aaa'),
    'nf-schema-2.5.1.zip': ('https://github.com/nextflow-io/nf-schema/releases/download/2.5.1/nf-schema-2.5.1.zip', 'f3833d0c29a51dc5e3759e00a6af87fcb1e989c94d1c1e7995d06e3f7090c461'),
    'micromamba-linux-aarch64.tar.bz2': ('https://micro.mamba.pm/api/micromamba/linux-aarch64/2.3.3', '02ed4fe982c8ecbf9c5b1759c07ee29063994842b65fdaab439db77b9b00510a'),
}


def fetch(item):
    target, url, expected = item
    if shutil.disk_usage(target.parent).free < 20 * 1024**3:
        raise RuntimeError('Workspace free space below the approved 20GiB floor')
    if not target.exists():
        temporary = target.with_name(target.name + '.partial')
        with urllib.request.urlopen(url, timeout=120) as response, temporary.open('wb') as stream:
            shutil.copyfileobj(response, stream)
        temporary.rename(target)
    actual = hashlib.sha256(target.read_bytes()).hexdigest()
    if actual != expected:
        raise ValueError('Software checksum mismatch: ' + target.name)
    return target.name, actual


def main():
    root = Path(sys.argv[1])
    if not root.is_absolute() or root.is_symlink():
        raise ValueError('Absolute staging directory required')
    root.mkdir(parents=True, exist_ok=True)
    (root/'packages').mkdir(exist_ok=True)
    packages = json.loads((HERE/'package-lock.linux-arm64.json').read_bytes())
    items = [(root/name, url, sha) for name, (url, sha) in ASSETS.items()]
    items += [(root/'packages'/p['url'].rsplit('/', 1)[1], p['url'], p['sha256']) for p in packages]
    with ThreadPoolExecutor(max_workers=6) as pool:
        hashes = dict(pool.map(fetch, items))
    with tarfile.open(root/'micromamba-linux-aarch64.tar.bz2') as archive:
        with archive.extractfile('bin/micromamba') as source, (root/'micromamba').open('wb') as target:
            shutil.copyfileobj(source,target)
    for name in ('Dockerfile', 'collect_inventory.py', 'software_preflight.py', 'nextflow-launcher', 'package-lock.linux-arm64.json'):
        shutil.copyfile(HERE/name, root/name)
    explicit = '@EXPLICIT\n' + '\n'.join('file:///packages/' + p['url'].rsplit('/',1)[1] + '#' + p['md5'] for p in packages) + '\n'
    (root/'environment-explicit.txt').write_text(explicit)
    (root/'context-software-sha256.json').write_text(json.dumps(hashes, sort_keys=True, indent=2)+'\n')
    print(json.dumps({'packages':len(packages), 'compressed_package_bytes':sum(p['size'] for p in packages), 'context':str(root)}))


if __name__ == '__main__':
    main()
