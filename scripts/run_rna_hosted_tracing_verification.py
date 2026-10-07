"""Verify one frozen hosted-tracing software epoch with installed-wheel imports.

The historical tracing runner remains unchanged. This extension reuses its
installed verification helpers but requires the complete, explicit A selection.
It neither executes a hosted pilot nor promotes native capabilities.
"""
from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import importlib.metadata
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import time
import traceback

from scripts import run_rna_tracing_verification as historical

WORKSPACE = historical.WORKSPACE
PACKAGE_ROOTS = historical.PACKAGE_ROOTS
HISTORICAL_TESTS = historical.HISTORICAL_TESTS
FOCUSED_BASE = historical.FOCUSED_BASE
PRIOR_SELECTION_SHA256 = historical.PRIOR_SELECTION_SHA256
NEW_TESTS = historical.NEW_TESTS | frozenset('tests/' + name + '.py' for name in (
    'test_rna_hosted_trace_contract', 'test_rna_langsmith_verify',
    'test_rna_hosted_qualification', 'test_rna_hosted_trace_invariance',
    'test_rna_hosted_exporter_isolation'))
RUNNER_PATH = 'scripts/run_rna_hosted_tracing_verification.py'
GENERATED_RESOURCE = 'devbench/analysis/assets/source-bundle.zip'
HELPER_PATHS = ('tests/support/rna_trace_fixture_paths.py',
                'tests/support/rna_hosted_trace_fixtures.py')
SOURCE_FORMAT = 'devbench.rna-tracing-source/1'
SELECTION_FORMAT = 'devbench.rna-hosted-tracing-selection/1'
sha = historical.sha
read_json = historical.read_json
closed = historical.closed
absolute = historical.absolute
source_state = historical.source_state
write_json = historical.write_json


def runtime_configuration() -> dict:
    """Bind interpreter, configuration and symlink identities omitted by old runner."""
    prefix = Path(sys.prefix).resolve()
    executables = {}
    for label, value in (('python', sys.executable), ('base_python', sys._base_executable)):
        path = Path(value)
        if not path.is_absolute():
            raise ValueError('Absolute interpreter identity required')
        resolved = path.resolve(strict=True)
        executables[label] = {'path': str(path), 'resolved_path': str(resolved), 'sha256': sha(resolved)}
    configuration = prefix / 'pyvenv.cfg'
    symlinks = {}
    for root in (prefix / 'bin', prefix / 'lib', prefix / 'share'):
        for path in sorted(root.rglob('*')):
            if path.is_symlink():
                symlinks[str(path.relative_to(prefix))] = {
                    'target': os.readlink(path), 'resolved_target': str(path.resolve(strict=True))}
    return {'pyvenv_cfg_sha256': sha(configuration), 'executables': executables,
            'symlinks': symlinks, 'base_prefix': sys.base_prefix,
            'python_version': sys.version, 'isolated': bool(sys.flags.isolated),
            'dont_write_bytecode': bool(sys.flags.dont_write_bytecode)}


def runtime_state() -> dict:
    state = historical.runtime_state()
    prefix = Path(sys.prefix).resolve()
    for path in sorted((prefix / 'share').rglob('*')):
        if path.is_file() and not path.is_symlink() and '__pycache__' not in path.parts:
            state['files'][str(path.relative_to(prefix))] = sha(path)
    state['configuration'] = runtime_configuration()
    return state


def reject_import_hooks() -> None:
    # Python reads .pth before this script starts. Never claim this post-start
    # check prevented hook execution; qualification setup must exclude them too.
    for path in Path(sys.prefix).rglob('*.pth'):
        for line in path.read_text().splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith('#'):
                raise ValueError('Runtime .pth import/path hooks prohibited')


def validate_selection(epoch: Path, manifest: dict, selection: dict) -> list[Path]:
    """Accept only the exact focused or historical-broad software module set."""
    closed(selection, {'format', 'scope', 'kind', 'source_manifest_sha256',
                       'prior_selection_sha256', 'allowed_fixture_classes', 'tests'}, 'selection')
    if selection['format'] != SELECTION_FORMAT or selection['scope'] != 'software':
        raise ValueError('Only hosted-tracing software selection supported')
    if selection['source_manifest_sha256'] != sha(epoch / 'source-manifest.json'):
        raise ValueError('Selection source mismatch')
    if selection['prior_selection_sha256'] != PRIOR_SELECTION_SHA256:
        raise ValueError('Historical selection provenance mismatch')
    if selection['allowed_fixture_classes'] != ['synthetic', 'local']:
        raise ValueError('Unapproved fixture classes')
    if selection['kind'] not in ('focused', 'broad'):
        raise ValueError('Unknown selection kind')
    rows = selection['tests']
    if not isinstance(rows, list) or not rows:
        raise ValueError('Explicit test paths required')
    required = (HISTORICAL_TESTS if selection['kind'] == 'broad' else FOCUSED_BASE) | NEW_TESTS
    names = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError('Selected test object required')
        closed(row, {'path', 'sha256'}, 'selected test')
        name = row['path']
        if not isinstance(name, str) or name not in required:
            raise ValueError('Unauthorized test/campaign selection')
        if manifest['files'].get(name) != row['sha256'] or sha(epoch / name) != row['sha256']:
            raise ValueError('Selected test hash mismatch')
        names.append(name)
    if len(set(names)) != len(names) or set(names) != required:
        raise ValueError('Selection must contain exact historical and all twelve tracing/hosted modules')
    return [epoch / name for name in names]


def checkout_state(manifest: dict) -> dict:
    """Bind all owned files, including untracked implementation, without git discovery."""
    files = {}
    for relative, expected in manifest['files'].items():
        if relative == GENERATED_RESOURCE:
            continue  # Regenerated inside the epoch; bound by source/wheel/RECORD below.
        path = absolute(str(WORKSPACE / relative))
        files[relative] = sha(path)
        if files[relative] != expected:
            raise ValueError('Checkout changed since frozen epoch: ' + relative)
    return files


def resource_binding(origin_map: dict, source: dict) -> dict:
    expected = source[GENERATED_RESOURCE]
    installed = [path for path, digest in origin_map.items()
        if path.endswith('/' + GENERATED_RESOURCE) and digest == expected]
    if len(installed) != 1:
        raise ValueError('Exactly one matching installed source resource required')
    return {GENERATED_RESOURCE: {'source_sha256': expected, 'installed_files': installed}}


def dependency_records(before: dict) -> list[dict]:
    """Verify ordinary dependency RECORDs against the frozen runtime inventory."""
    prefix = Path(sys.prefix).resolve()
    rows = []
    for distribution in importlib.metadata.distributions():
        direct = distribution.read_text('direct_url.json')
        if direct and json.loads(direct).get('dir_info', {}).get('editable'):
            raise ValueError('Editable dependency installation prohibited')
        record = next((p for p in distribution.files or () if str(p).endswith('.dist-info/RECORD')), None)
        if record is None:
            raise ValueError('Dependency RECORD missing: ' + distribution.metadata['Name'])
        record_path = Path(distribution.locate_file(record)).resolve()
        if not record_path.is_relative_to(prefix):
            raise ValueError('Dependency RECORD outside qualified runtime')
        text = distribution.read_text('RECORD') or ''
        checked = 0
        for row in csv.reader(io.StringIO(text)):
            if len(row) != 3:
                raise ValueError('Malformed dependency RECORD')
            relative, encoded, size = row
            if not encoded:
                if relative.endswith('.dist-info/RECORD') or relative.endswith('.pyc'):
                    continue
                raise ValueError('Unhashed dependency file')
            if not encoded.startswith('sha256='):
                raise ValueError('Unsupported dependency RECORD hash')
            path = Path(distribution.locate_file(relative)).resolve()
            if not path.is_relative_to(prefix):
                raise ValueError('Dependency file outside qualified runtime')
            digest = base64.urlsafe_b64decode(encoded.split('=', 1)[1] + '===').hex()
            actual = before['files'].get(str(path.relative_to(prefix)))
            # Ordinary wheels may have prefix/share assets. The historical runtime
            # inventory covers bin/lib; independently bind other RECORD paths.
            if actual is None:
                actual = sha(path)
            if actual != digest or path.stat().st_size != int(size):
                raise ValueError('Dependency RECORD content mismatch: ' + distribution.metadata['Name'])
            checked += 1
        rows.append({'distribution': distribution.metadata['Name'], 'version': distribution.version,
                     'record_path': str(record_path), 'record_sha256': sha(record_path),
                     'verified_files': checked})
    return sorted(rows, key=lambda row: row['distribution'].lower())


def module_origins(origin_map: dict, epoch: Path, manifest: dict) -> list[dict]:
    rows = historical.module_origins(origin_map, epoch)
    for row in rows:
        if row['kind'] == 'frozen_test_helper':
            relative = str(Path(row['path']).relative_to(epoch))
            if manifest['files'].get(relative) != row['sha256']:
                raise ValueError('Unbound or modified frozen helper: ' + relative)
    # Reject every checkout-origin import, including unqualified test helpers.
    for name, module in sys.modules.copy().items():
        location = getattr(module, '__file__', None)
        if not location:
            continue
        path = Path(location).resolve()
        if path.is_relative_to(WORKSPACE) and not path.is_relative_to(epoch) and str(path) not in origin_map:
            if path.is_relative_to(Path(sys.prefix).resolve()):
                continue  # Ordinary dependencies are bound by their RECORD and runtime inventory.
            raise ValueError('Module borrowed from mutable workspace: ' + name)
    return rows


def bind_frozen_tests(epoch: Path) -> None:
    """Bind hash-verified test package for importlib collection and spawn."""
    manifest = read_json(epoch / 'source-manifest.json')
    closed(manifest, {'format', 'files'}, 'spawn source manifest')
    if manifest['format'] != SOURCE_FORMAT:
        raise ValueError('Unknown frozen test manifest format')
    binding = read_json(epoch / 'runtime-binding.json')
    if binding['source_manifest_sha256'] != sha(epoch / 'source-manifest.json'):
        raise ValueError('Spawn source binding mismatch')
    if absolute(binding['runtime_prefix']) != Path(sys.prefix).resolve():
        raise ValueError('Spawn runtime prefix mismatch')
    if manifest['files'].get(RUNNER_PATH) != sha(Path(__file__).resolve()):
        raise ValueError('Spawn runner binding mismatch')
    for relative, expected in manifest['files'].items():
        if not relative.startswith('tests/'):
            continue
        path = epoch / relative
        if '..' in Path(relative).parts or not path.resolve().is_relative_to(epoch / 'tests') or sha(path) != expected:
            raise ValueError('Frozen spawn test/helper hash mismatch')
    if 'tests/__init__.py' not in manifest['files']:
        raise ValueError('Frozen tests package missing')
    spec = importlib.util.spec_from_file_location('tests', epoch / 'tests/__init__.py',
        submodule_search_locations=[str(epoch / 'tests')])
    package = importlib.util.module_from_spec(spec)
    sys.modules['tests'] = package
    spec.loader.exec_module(package)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('epoch-root', 'selection', 'output', 'short-fixture-root', 'short-workspace-root'):
        parser.add_argument('--' + name, required=True)
    parser.add_argument('--scope', required=True, choices=['software'])
    parser.add_argument('--allow-bounded-local-fixtures', action='store_true')
    args = parser.parse_args(argv)
    epoch, selection_path, output, fixture_root, actor_root = map(absolute, (
        args.epoch_root, args.selection, args.output, args.short_fixture_root, args.short_workspace_root))
    roots = [epoch, output, fixture_root, actor_root, Path(sys.prefix).resolve()]
    if any(a.is_relative_to(b) or b.is_relative_to(a) for i, a in enumerate(roots) for b in roots[i + 1:]):
        raise ValueError('Source/runtime/output/fixture storage must be disjoint')
    if output.exists() or fixture_root.exists() or actor_root.exists():
        raise ValueError('Fresh output and fixture roots required')
    if selection_path.is_relative_to(output) or selection_path.is_relative_to(fixture_root) or selection_path.is_relative_to(actor_root):
        raise ValueError('Selection must remain outside writable fixture/output storage')
    output.mkdir(parents=True, mode=0o700)
    started = time.monotonic_ns()
    write_json(output / 'start.json', {'started_utc_epoch': time.time(), 'started_monotonic_ns': started,
        'argv': list(sys.argv if argv is None else argv), 'scope': args.scope,
        'python': sys.executable, 'epoch_root': str(epoch)})
    code = 2
    result = {'status': 'blocked', 'scope': 'software', 'hosted': 'unverified',
              'dashboard': 'unverified', 'native_budget': 'unverified', 'native_isolation': 'unverified',
              'scientific_review': 'unverified'}
    before = source_before = checkout_before = origin_map = manifest = None
    identity_files = {}
    try:
        if not args.allow_bounded_local_fixtures:
            raise PermissionError('Required bounded software fixtures were not authorized')
        if os.environ.get('PYTHONPATH'):
            raise ValueError('PYTHONPATH prohibited, even under isolated Python')
        reject_import_hooks()
        manifest = read_json(epoch / 'source-manifest.json')
        closed(manifest, {'format', 'files'}, 'source manifest')
        if manifest['format'] != SOURCE_FORMAT:
            raise ValueError('Unknown source format')
        source_before = source_state(epoch, manifest)
        if source_before.get(RUNNER_PATH) != sha(Path(__file__).resolve()):
            raise ValueError('Runner is not bound to frozen epoch')
        checkout_before = checkout_state(manifest)
        runtime_binding = read_json(epoch / 'runtime-binding.json')
        identity_files = {str(path): sha(path) for path in (
            epoch / 'source-manifest.json', epoch / 'runtime-binding.json', selection_path,
            absolute(runtime_binding['wheel_path']), absolute(runtime_binding['runtime_manifest_path']))}
        write_json(output / 'identity-files-before.json', {'files': identity_files})
        origin_map = historical.validate_runtime(epoch, manifest, runtime_binding)
        selected = validate_selection(epoch, manifest, read_json(selection_path))
        selection_digest = sha(selection_path)
        before = runtime_state()
        frozen_runtime = absolute(runtime_binding['runtime_manifest_path'])
        if sha(frozen_runtime) != runtime_binding['runtime_manifest_sha256']:
            raise ValueError('Frozen runtime manifest hash mismatch')
        if frozen_runtime.stat().st_size > 32 * 1024 * 1024:
            raise ValueError('Runtime manifest exceeds bounded read')
        if json.loads(frozen_runtime.read_text()) != before:
            raise ValueError('Runtime drift since frozen inventory')
        write_json(output / 'dependency-records.json', {'records': dependency_records(before)})
        write_json(output / 'runtime-before.json', before)
        write_json(output / 'source-before.json', source_before)
        write_json(output / 'checkout-before.json', checkout_before)
        helpers = {}
        for relative in HELPER_PATHS:
            path = epoch / relative
            if source_before.get(relative) != sha(path):
                raise ValueError('Frozen fixture helper missing or mismatched')
            helpers[relative] = {'path': str(path), 'sha256': sha(path)}
        write_json(output / 'helper-bindings.json', {'helpers': helpers})
        write_json(output / 'resource-bindings.json', {'resources': resource_binding(origin_map, source_before)})
        helper_path = epoch / HELPER_PATHS[0]
        spec = importlib.util.spec_from_file_location('rna_trace_fixture_paths', helper_path)
        helper = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = helper
        spec.loader.exec_module(helper)
        plugin = helper.FixturePaths(epoch, output, fixture_root, actor_root)
        os.environ['PYTEST_DISABLE_PLUGIN_AUTOLOAD'] = '1'
        os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
        os.environ['DEVBENCH_RUN_LOCAL_FIXTURES'] = '1'
        bind_frozen_tests(epoch)
        import devbench.analysis.bundles
        module_origins(origin_map, epoch, manifest)
        import pytest
        code = int(pytest.main(['--import-mode=importlib', '--continue-on-collection-errors',
            '-p', 'no:cacheprovider', '--basetemp=' + str(output / 'tmp'),
            '--junitxml=' + str(output / 'junit.xml'), *map(str, selected)], plugins=[plugin]))
        result.update(status='passed' if code == 0 else 'nonpasses_retained', pytest_exit_code=code,
            selected_tests=[str(p) for p in selected], selection_sha256=selection_digest,
            selection_unchanged=sha(selection_path) == selection_digest,
            source_manifest_sha256=sha(epoch / 'source-manifest.json'),
            wheel_sha256=runtime_binding['wheel_sha256'])
        if not result['selection_unchanged']:
            raise ValueError('Selection changed during verification')
    except Exception as error:
        code = 2
        result.update(error_type=type(error).__name__, error=str(error), status='blocked')
        (output / 'runner-error.txt').write_text(traceback.format_exc())
    finally:
        try:
            if identity_files:
                identity_after = {name: sha(Path(name)) for name in identity_files}
                write_json(output / 'identity-files-after.json', {'files': identity_after})
                result['identity_files_unchanged'] = identity_files == identity_after
                if not result['identity_files_unchanged']:
                    raise ValueError('Source/runtime/selection manifest or wheel changed during verification')
            if origin_map is not None:
                write_json(output / 'module-origins.json', {'origins': module_origins(origin_map, epoch, manifest)})
            if before is not None:
                after = runtime_state()
                write_json(output / 'runtime-after.json', after)
                write_json(output / 'dependency-records-after.json', {'records': dependency_records(after)})
                result['runtime_unchanged'] = before == after
                if not result['runtime_unchanged']:
                    raise ValueError('Runtime changed during verification')
            if source_before is not None:
                after_source = source_state(epoch, manifest)
                write_json(output / 'source-after.json', after_source)
                result['source_unchanged'] = source_before == after_source
            if checkout_before is not None:
                after_checkout = checkout_state(manifest)
                write_json(output / 'checkout-after.json', after_checkout)
                result['checkout_unchanged'] = checkout_before == after_checkout
        except Exception as error:
            result.update(verification_error=str(error), status='blocked_identity_verification')
            code = 2
        result['elapsed_seconds'] = (time.monotonic_ns() - started) / 1_000_000_000
        result['runner_exit_code'] = code
        write_json(output / 'result.json', result)
    return code


if __name__ == '__mp_main__':
    bind_frozen_tests(Path(__file__).resolve().parents[1])

if __name__ == '__main__':
    raise SystemExit(main())
