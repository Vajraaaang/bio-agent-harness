"""Install a fresh hosted epoch wheel and bind its extended runtime inventory."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess

from datetime import datetime, timezone


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def write_new(path, value):
    with path.open('x') as stream:
        json.dump(value, stream, sort_keys=True, indent=2)
        stream.write('\n')


def main():
    parser = argparse.ArgumentParser()
    for name in ('epoch-root', 'report-root', 'runtime-prefix', 'build-python'):
        parser.add_argument('--' + name, required=True)
    args = parser.parse_args()
    epoch, report, runtime, build_python = map(Path, (args.epoch_root, args.report_root,
        args.runtime_prefix, args.build_python))
    workspace = Path('/Users/vajraang/Documents/ChatGPT/bio-agent-harness')
    for path in (epoch, report, runtime, build_python):
        if not path.is_absolute() or '..' in path.parts:
            raise ValueError('Absolute path required')
    for path in (epoch, report, runtime):
        if path != path.resolve() or not path.is_relative_to(workspace):
            raise ValueError('Nonsymlink assigned-workspace storage required')
    if runtime != workspace / '.venv-rna-hosted-qualification':
        raise ValueError('Only the fresh hosted qualification runtime may be installed')
    if not epoch.is_relative_to(report / 'source') or report == workspace:
        raise ValueError('Epoch must belong to its fresh report root')
    preparation_path = report / 'runtime-preparation-approved.json'
    if preparation_path.is_symlink() or preparation_path.stat().st_size > 2_000_000:
        raise ValueError('Bounded approved runtime preparation required')
    preparation = json.loads(preparation_path.read_bytes())
    if preparation.get('runtime') != str(runtime) or preparation.get('status') != 'approved':
        raise ValueError('Independent runtime preparation approval required')
    expected_build = workspace / '.venv-rna-hosted-qualification-build/bin/python'
    if build_python != expected_build or preparation.get('build_python') != str(expected_build):
        raise ValueError('Only the independently approved fresh build interpreter may be used')
    proofs = preparation.get('proofs')
    if not isinstance(proofs, dict) or not proofs:
        raise ValueError('Retained independent preparation proofs required')
    for name, expected in proofs.items():
        path = Path(name)
        if not path.is_absolute() or path != path.resolve() or not path.is_relative_to(report) or sha(path) != expected:
            raise ValueError('Preparation proof changed before wheel installation')
    preparation_digest = sha(preparation_path)
    # Validate all sealed source files before executing even the build backend.
    manifest = json.loads((epoch / 'source-manifest.json').read_bytes())
    if set(manifest) != {'format', 'files'} or manifest['format'] != 'devbench.rna-tracing-source/1':
        raise ValueError('Sealed source manifest required')
    for name, expected in manifest['files'].items():
        path = epoch / name
        if Path(name).is_absolute() or '..' in Path(name).parts or path != path.resolve() or not path.is_relative_to(epoch) or sha(path) != expected:
            raise ValueError('Epoch source mismatch before build')
    source_manifest_digest = sha(epoch / 'source-manifest.json')
    work = report / 'build' / epoch.name
    wheels = report / 'wheels' / epoch.name
    wheels.mkdir(parents=True, exist_ok=False)
    shutil.copytree(epoch, work)
    cache = report / 'installation-cache'
    commands = [[str(build_python), '-I', '-B', '-m', 'pip', 'wheel', '--no-deps',
        '--no-build-isolation', '--no-index', '--wheel-dir', str(wheels), str(work)]]
    evidence = {'started_at': datetime.now(timezone.utc).isoformat(), 'epoch': str(epoch),
        'source_manifest_sha256': source_manifest_digest,
        'runtime_preparation_sha256': preparation_digest, 'commands': []}
    for command in commands:
        result = subprocess.run(command, capture_output=True, text=True)
        evidence['commands'].append({'argv': command, 'exit_code': result.returncode,
            'stdout': result.stdout, 'stderr': result.stderr})
        if result.returncode:
            write_new(report / ('install-' + epoch.name + '-failed.json'), evidence)
            raise RuntimeError('Epoch wheel build failed; retained command evidence')
    matches = list(wheels.glob('devharness-*.whl'))
    if len(matches) != 1:
        raise ValueError('Exactly one project wheel required')
    wheel = matches[0]
    if sha(epoch / 'source-manifest.json') != source_manifest_digest or any(
            sha(epoch / name) != expected for name, expected in manifest['files'].items()):
        raise ValueError('Sealed source changed during wheel build')
    if sha(preparation_path) != preparation_digest:
        raise ValueError('Runtime approval changed during build')
    command = ['/opt/homebrew/bin/uv', '--cache-dir', str(cache), 'pip', 'install',
        '--python', str(runtime / 'bin/python'), '--no-deps', '--reinstall', str(wheel)]
    result = subprocess.run(command, capture_output=True, text=True)
    evidence['commands'].append({'argv': command, 'exit_code': result.returncode,
        'stdout': result.stdout, 'stderr': result.stderr})
    if result.returncode:
        write_new(report / ('install-' + epoch.name + '-failed.json'), evidence)
        raise RuntimeError('Epoch wheel install failed; retained command evidence')
    inventory = report / ('runtime-' + epoch.name + '.json')
    # The frozen runner supplies the exact inventory contract. It imports only
    # stdlib until its main entrypoint is explicitly called.
    probe = '''import importlib.util,json,importlib.metadata,hashlib
from pathlib import Path
p=Path({runner!r})
s=importlib.util.spec_from_file_location('_rna_verification_inventory',p)
m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
out=Path({inventory!r});out.write_text(json.dumps(m.runtime_state(),sort_keys=True,indent=2)+'\\n')
d=importlib.metadata.distribution('devharness')
r=next(p for p in d.files if str(p).endswith('.dist-info/RECORD'))
print(hashlib.sha256(Path(d.locate_file(r)).read_bytes()).hexdigest())
'''.format(runner=str(epoch / 'scripts/run_rna_hosted_tracing_verification.py'), inventory=str(inventory))
    command = [str(runtime / 'bin/python'), '-I', '-B', '-c', probe]
    result = subprocess.run(command, capture_output=True, text=True)
    evidence['commands'].append({'argv': command[:4] + ['<retained inventory probe>'],
        'exit_code': result.returncode, 'stdout': result.stdout, 'stderr': result.stderr})
    if result.returncode:
        write_new(report / ('install-' + epoch.name + '-failed.json'), evidence)
        raise RuntimeError('Inventory failed; retained command evidence')
    binding = {'format': 'devbench.rna-tracing-runtime/1', 'distribution': 'devharness',
        'source_manifest_sha256': sha(epoch / 'source-manifest.json'), 'runtime_prefix': str(runtime),
        'wheel_path': str(wheel), 'wheel_sha256': sha(wheel), 'record_sha256': result.stdout.strip(),
        'runtime_manifest_path': str(inventory), 'runtime_manifest_sha256': sha(inventory)}
    write_new(epoch / 'runtime-binding.json', binding)
    evidence.update(binding=binding, completed_at=datetime.now(timezone.utc).isoformat())
    write_new(report / ('install-' + epoch.name + '.json'), evidence)
    print(json.dumps({'epoch': str(epoch), 'wheel_sha256': binding['wheel_sha256'],
        'source_manifest_sha256': binding['source_manifest_sha256'], 'status': 'installed'}))


if __name__ == '__main__':
    main()
