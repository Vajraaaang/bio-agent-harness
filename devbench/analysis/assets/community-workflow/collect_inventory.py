"""Build-time installed file inventory; never claims scientific qualification."""
import hashlib
import json
from pathlib import Path
import subprocess
import importlib


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode()


def sha(path):
    h=hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024**2), b''): h.update(block)
    return h.hexdigest()


def tree(root):
    root=Path(root)
    return {str(p.relative_to(root)):sha(p) for p in sorted(root.rglob('*')) if p.is_file() and not p.is_symlink()}


def tree_sha(root):
    return hashlib.sha256(canonical(tree(root))).hexdigest()


def main():
    r=Path('/opt/community/lib/R/library')
    packages={}
    for name in ('DESeq2','shinyngs','limma','BiocParallel'):
        version=subprocess.check_output(['Rscript','-e',f'cat(as.character(packageVersion("{name}")))'],text=True).strip()
        packages[name]={'version':version,'sha256':tree_sha(r/name),'path':str(r/name),'sha256_kind':'tree'}
    packages.update({
        'java':{'version':'21.0.9','sha256':sha(Path('/opt/community/lib/jvm/bin/java'))},
        'R':{'version':'4.5.1','sha256':sha(Path('/opt/community/lib/R/bin/exec/R'))},
        'csvtk':{'version':'0.37.0','sha256':sha(Path('/opt/community/bin/csvtk'))},
        'python':{'version':'3.12.13','sha256':sha(Path('/opt/community/bin/python3.12'))},
    })
    for name,path in {'java':'/opt/community/lib/jvm/bin/java','R':'/opt/community/lib/R/bin/exec/R',
            'csvtk':'/opt/community/bin/csvtk','python':'/opt/community/bin/python3.12'}.items():
        packages[name].update(path=path,sha256_kind='file')
    for name in ('pandas','numpy'):
        module=importlib.import_module(name)
        path=Path(module.__file__).parent
        packages[name]={'version':module.__version__,'sha256':tree_sha(path),'path':str(path),'sha256_kind':'tree'}
    inventory={'format':'devbench.community-workflow-inventory/1','platform':'linux/arm64',
        'workflow':{'name':'nf-core/differentialabundance','version':'2.0.0','commit':'30ed7741fc392127156c2fb10cfa3d69d216b54b','tree_sha256':tree_sha('/opt/nf-core/differentialabundance'),'path':'/opt/nf-core/differentialabundance/main.nf','tree_root':'/opt/nf-core/differentialabundance'},
        'nextflow':{'version':'25.10.4','sha256':sha(Path('/opt/nextflow/nextflow-25.10.4-dist')),'path':'/usr/local/bin/nextflow','sha256_path':'/opt/nextflow/nextflow-25.10.4-dist'},
        'plugins':[{'id':'nf-schema','version':'2.5.1','sha256':tree_sha('/opt/nextflow/plugins/nf-schema-2.5.1'),'path':'/opt/nextflow/plugins/nf-schema-2.5.1','sha256_kind':'tree'}],
        'tools':packages,'execution':{'executor':'local','container_engine':'none','network':'deny','conda':False,'wave':False,'daemon_socket':False},
        'evidence_scope':'Installed public software inventory and immutable image admission; scientific qualification and clinical release not established'}
    manifest={}
    for root in ('/opt/community','/opt/nextflow','/opt/nf-core','/opt/devbench','/usr/local/bin'):
        for relative,digest in tree(root).items():
            path=str(Path(root)/relative)
            if path not in ('/opt/devbench/admission.json','/opt/devbench/asset-manifest.json'): manifest[path]=digest
    if not 1 <= len(manifest) <= 50000: raise ValueError(f'Asset count outside worker bound: {len(manifest)}')
    Path('/opt/devbench/asset-manifest.json').write_bytes(canonical(manifest))
    Path('/opt/devbench/admission.json').write_bytes(canonical(inventory))
    print(json.dumps({'installed_regular_files':len(manifest),'inventory_sha256':hashlib.sha256(canonical(inventory)).hexdigest()}))


if __name__=='__main__': main()
