"""Verify a frozen RNA tracing epoch through ordinary installed wheel imports.

This runner never resolves product modules from source, discovers test directories,
or alters preserved runtimes. Every attempt gets a fresh retained report.
"""
from __future__ import annotations

import argparse
import base64
import csv
import io
import hashlib
import importlib.metadata
import importlib.util
import json
import os
from pathlib import Path
import sys
import time
import traceback
import zipfile

WORKSPACE = Path('/Users/vajraang/Documents/ChatGPT/bio-agent-harness')
PACKAGE_ROOTS = ('devbench', 'devbench_engine', 'devharness', 'devharness_controlled',
                 'devharness_server', 'devharness_core', 'benchmarks', 'experiments', 'scripts')
NEW_TESTS = frozenset('tests/' + name + '.py' for name in (
    'test_rna_trace_contract', 'test_rna_trace_projection', 'test_rna_langsmith_export',
    'test_langsmith_outbox', 'test_rna_trace_invariance', 'test_rna_trace_receipts',
    'test_rna_langsmith_live'))
PRIOR_SELECTION_SHA256 = 'f586143a784f6e35af3864b55f6b2f6bfef407e7817dc4017291b1317c2db0ed'
FOCUSED_BASE = frozenset('tests/' + name + '.py' for name in (
    'test_analysis_bundle', 'test_source_bundle_verification', 'test_langsmith_export', 'test_rna_investigation_backend',
    'test_rna_investigation_budget', 'test_rna_investigation_replay',
    'test_langgraph_investigations'))
# Frozen historical selection names are inserted below; no retained test code is imported.
HISTORICAL_TESTS = frozenset(('tests/test_analysis_bundle.py', 'tests/test_analysis_checkpoints.py', 'tests/test_analysis_episodes.py', 'tests/test_analysis_notebooks.py', 'tests/test_analysis_scorecard.py', 'tests/test_annotation_judgment_policy.py', 'tests/test_benchmarks.py', 'tests/test_biology_broker_contracts.py', 'tests/test_biology_bulk_contracts.py', 'tests/test_biology_bulk_tools.py', 'tests/test_biology_dataset_contracts.py', 'tests/test_biology_metrics_contracts.py', 'tests/test_biology_pipeline.py', 'tests/test_biology_scoring_contracts.py', 'tests/test_biology_singlecell_contracts.py', 'tests/test_biology_singlecell_tools.py', 'tests/test_cli_app_layout.py', 'tests/test_cli_capture_contracts.py', 'tests/test_cli_identity.py', 'tests/test_discovery_baselines.py', 'tests/test_discovery_claims.py', 'tests/test_discovery_review.py', 'tests/test_discovery_scoring.py', 'tests/test_episode_agent.py', 'tests/test_episode_cli.py', 'tests/test_episode_codex.py', 'tests/test_episode_planning.py', 'tests/test_episode_proposals.py', 'tests/test_episode_synthesis.py', 'tests/test_exec_preflight.py', 'tests/test_execution_contract_repair.py', 'tests/test_extensible_method_bounds.py', 'tests/test_extensible_methods.py', 'tests/test_figure_artifacts.py', 'tests/test_figure_catalog.py', 'tests/test_figure_reporting.py', 'tests/test_figure_revisions.py', 'tests/test_langchain_tools.py', 'tests/test_langgraph_example.py', 'tests/test_langgraph_investigations.py', 'tests/test_langgraph_workflow.py', 'tests/test_method_action_scope.py', 'tests/test_method_authoring.py', 'tests/test_method_authoring_contract.py', 'tests/test_method_comparison_provenance.py', 'tests/test_method_contracts.py', 'tests/test_method_diagnostic_outputs.py', 'tests/test_method_diagnostic_profiles.py', 'tests/test_method_observation_selection.py', 'tests/test_method_planner.py', 'tests/test_method_planner_cli.py', 'tests/test_method_planner_codex.py', 'tests/test_method_planner_contract.py', 'tests/test_method_planner_guards.py', 'tests/test_method_planner_recovery.py', 'tests/test_method_planner_scientific_links.py', 'tests/test_method_planner_scientific_safeguards.py', 'tests/test_method_profile_admission.py', 'tests/test_method_selection_evaluation.py', 'tests/test_method_selection_pilot_runner.py', 'tests/test_method_sources.py', 'tests/test_method_store_access.py', 'tests/test_method_verification_scope.py', 'tests/test_model_telemetry.py', 'tests/test_planted_discovery.py', 'tests/test_reasoning_review.py', 'tests/test_rna_independent_postfit_workflow.py', 'tests/test_rna_investigation_backend.py', 'tests/test_rna_investigation_budget.py', 'tests/test_rna_investigation_contract.py', 'tests/test_rna_investigation_evaluation.py', 'tests/test_rna_investigation_evaluation_isolation.py', 'tests/test_rna_investigation_parent_integration.py', 'tests/test_rna_investigation_policy.py', 'tests/test_rna_investigation_projection.py', 'tests/test_rna_investigation_reconciliation.py', 'tests/test_rna_investigation_recovery.py', 'tests/test_rna_investigation_replay.py', 'tests/test_rna_investigation_science.py', 'tests/test_runtime_identity.py', 'tests/test_scientific_integrity_regressions.py', 'tests/test_scientific_judgment_contract.py', 'tests/test_scientific_objects.py', 'tests/test_scientific_plan_context.py', 'tests/test_scientific_review_agent.py', 'tests/test_scientific_workspace.py', 'tests/test_scientific_workspace_asset.py', 'tests/test_scientific_workspace_http.py', 'tests/test_source_bundle_verification.py', 'tests/test_sqlite_schema_guards.py', 'tests/test_study_adapter.py', 'tests/test_study_permissions.py', 'tests/test_study_supervision.py', 'tests/test_tools.py', 'tests/test_watchdog.py', 'tests/test_watchdog_growth_window.py', 'tests/test_watchdog_observations.py', 'tests/test_workbench_organization_audit_asset.py', 'tests/test_workbench_planning.py', 'tests/test_workbench_proposals.py', 'tests/test_workbench_ui.py', 'tests/test_workbench_ui_agent_asset.py', 'tests/test_workbench_ui_figures.py', 'tests/test_workbench_ui_organization.py', 'tests/test_workbench_ui_organization_asset.py', 'tests/test_workbench_ui_planning.py', 'tests/test_workbench_ui_recovery.py', 'tests/test_workbench_ui_workspace_asset.py', 'tests/test_workbench_view_state_recovery.py', 'tests/test_worker_cancellation.py', 'tests/test_worker_limit_context.py', 'tests/test_workflow_operations.py'))


def sha(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f'Expected regular file: {path}')
    result = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(chunk)
    return result.hexdigest()


def read_json(path: Path) -> dict:
    if path.stat().st_size > 4 * 1024 * 1024:
        raise ValueError('Manifest exceeds bounded read')
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError('Manifest object required')
    return value


def closed(value: dict, keys: set[str], name: str) -> None:
    if set(value) != keys:
        raise ValueError(f'{name}: unexpected or missing fields')


def absolute(value: str) -> Path:
    path = Path(value)
    if not path.is_absolute() or path != path.resolve() or not path.is_relative_to(WORKSPACE):
        raise ValueError('Absolute nonsymlink assigned-workspace path required')
    return path


def source_state(epoch: Path, manifest: dict) -> dict:
    actual = {}
    for relative, expected in manifest['files'].items():
        path = epoch / relative
        if Path(relative).is_absolute() or '..' in Path(relative).parts or not path.resolve().is_relative_to(epoch):
            raise ValueError('Source manifest path escapes epoch')
        actual[relative] = sha(path)
        if actual[relative] != expected:
            raise ValueError(f'Source hash mismatch: {relative}')
    if any(p.is_symlink() for p in epoch.rglob('*')):
        raise ValueError('Symlink in frozen source prohibited')
    expected_paths = set(manifest['files']) | {'source-manifest.json', 'runtime-binding.json'}
    extra = {str(p.relative_to(epoch)) for p in epoch.rglob('*') if p.is_file()} - expected_paths
    if extra:
        raise ValueError('Unmanifested source file: ' + sorted(extra)[0])
    return actual


def runtime_state() -> dict:
    prefix = Path(sys.prefix).resolve()
    files = {}
    for base in (prefix / 'bin', prefix / 'lib'):
        for path in sorted(base.rglob('*')):
            if path.is_file() and not path.is_symlink() and '__pycache__' not in path.parts:
                files[str(path.relative_to(prefix))] = sha(path)
    return {'prefix': str(prefix), 'files': files,
            'packages': sorted([d.metadata['Name'], d.version] for d in importlib.metadata.distributions())}


def validate_runtime(epoch: Path, manifest: dict, binding: dict) -> dict:
    closed(binding, {'format', 'source_manifest_sha256', 'runtime_prefix', 'distribution',
                     'wheel_path', 'wheel_sha256', 'record_sha256',
                     'runtime_manifest_path', 'runtime_manifest_sha256'}, 'runtime binding')
    if binding['format'] != 'devbench.rna-tracing-runtime/1' or binding['distribution'] != 'devharness':
        raise ValueError('Unsupported runtime binding')
    if binding['source_manifest_sha256'] != sha(epoch / 'source-manifest.json'):
        raise ValueError('Runtime source binding mismatch')
    if absolute(binding['runtime_prefix']) != Path(sys.prefix).resolve():
        raise ValueError('Wrong analysis runtime')
    if not sys.flags.isolated or not sys.flags.dont_write_bytecode:
        raise ValueError('Runner requires Python -I -B')
    wheel = absolute(binding['wheel_path'])
    if sha(wheel) != binding['wheel_sha256']:
        raise ValueError('Wheel hash mismatch')
    distribution = importlib.metadata.distribution('devharness')
    record = next((p for p in distribution.files or () if str(p).endswith('.dist-info/RECORD')), None)
    if record is None or sha(Path(distribution.locate_file(record))) != binding['record_sha256']:
        raise ValueError('Installed RECORD mismatch')
    if any(str(p).endswith('direct_url.json') for p in distribution.files or ()):
        direct = distribution.read_text('direct_url.json')
        if direct and json.loads(direct).get('dir_info', {}).get('editable'):
            raise ValueError('Editable installation prohibited')
    for row in csv.reader(io.StringIO(distribution.read_text('RECORD') or '')):
        if len(row) != 3:
            raise ValueError('Malformed installed RECORD')
        relative, encoded, size = row
        if not encoded:
            if not relative.endswith('.dist-info/RECORD') and not relative.endswith('.pyc'):
                raise ValueError('Unhashed installed distribution file')
            continue
        if not encoded.startswith('sha256='):
            raise ValueError('Unsupported installed RECORD hash')
        installed = Path(distribution.locate_file(relative)).resolve()
        if not installed.is_relative_to(Path(sys.prefix).resolve()):
            raise ValueError('Installed RECORD file outside runtime')
        expected = base64.urlsafe_b64decode(encoded.split('=', 1)[1] + '===').hex()
        if sha(installed) != expected or installed.stat().st_size != int(size):
            raise ValueError('Installed distribution RECORD content mismatch')
    origin_map = {}
    with zipfile.ZipFile(wheel) as archive:
        for name in archive.namelist():
            if name.endswith('/') or '.dist-info/' in name or '.data/' in name:
                continue
            if name.split('/')[0] not in PACKAGE_ROOTS:
                raise ValueError('Unexpected wheel product root')
            expected = hashlib.sha256(archive.read(name)).hexdigest()
            if manifest['files'].get(name) != expected:
                raise ValueError(f'Wheel does not match source: {name}')
            installed = Path(distribution.locate_file(name)).resolve()
            if not installed.is_relative_to(Path(sys.prefix).resolve()) or sha(installed) != expected:
                raise ValueError(f'Installed wheel file mismatch: {name}')
            origin_map[str(installed)] = expected
    for name in PACKAGE_ROOTS:
        spec = importlib.util.find_spec(name)
        if spec is None or spec.origin is None or str(Path(spec.origin).resolve()) not in origin_map:
            raise ValueError(f'Product package origin not in installed wheel: {name}')
    for entry in distribution.entry_points:
        if entry.group == 'console_scripts':
            executable = Path(sys.prefix) / 'bin' / entry.name
            if not executable.is_file() or entry.value.split(':')[0].split('.')[0] not in PACKAGE_ROOTS:
                raise ValueError('Unbound console entrypoint')
    for path in Path(sys.prefix).rglob('*.pth'):
        content = path.read_text()
        if str(WORKSPACE) in content or 'editable' in content.lower():
            raise ValueError('Source borrowing through .pth prohibited')
    return origin_map


def validate_selection(epoch: Path, manifest: dict, selection: dict) -> list[Path]:
    closed(selection, {'format', 'scope', 'kind', 'source_manifest_sha256',
                       'prior_selection_sha256', 'allowed_fixture_classes', 'tests'}, 'selection')
    if selection['format'] != 'devbench.rna-tracing-selection/1' or selection['scope'] != 'software':
        raise ValueError('Only software selection supported')
    if selection['source_manifest_sha256'] != sha(epoch / 'source-manifest.json'):
        raise ValueError('Selection source mismatch')
    if selection['prior_selection_sha256'] != PRIOR_SELECTION_SHA256:
        raise ValueError('Prior selection provenance mismatch')
    if selection['allowed_fixture_classes'] != ['synthetic', 'local']:
        raise ValueError('Unapproved fixture classes')
    if selection['kind'] not in ('focused', 'broad'):
        raise ValueError('Unknown selection kind')
    rows = selection['tests']
    if not isinstance(rows, list) or not rows:
        raise ValueError('Explicit test paths required')
    names = []
    for row in rows:
        closed(row, {'path', 'sha256'}, 'selected test')
        name = row['path']
        if name not in HISTORICAL_TESTS | NEW_TESTS | FOCUSED_BASE:
            raise ValueError('Unauthorized test/campaign selection')
        if manifest['files'].get(name) != row['sha256'] or sha(epoch / name) != row['sha256']:
            raise ValueError('Selected test hash mismatch')
        names.append(name)
    if len(set(names)) != len(names):
        raise ValueError('Duplicate test selection')
    implemented = {name for name in NEW_TESTS if name in manifest['files']}
    required = (HISTORICAL_TESTS if selection['kind'] == 'broad' else FOCUSED_BASE) | implemented
    # Phase 0..3 focused gate may omit explicitly later-stage receipt/live modules.
    if set(names) != required:
        raise ValueError('Selection must contain exact qualified names plus implemented tracing modules')
    return [epoch / name for name in names]


def module_origins(origin_map: dict, epoch: Path) -> list[dict]:
    rows = []
    for name, module in sorted(sys.modules.copy().items()):
        location = getattr(module, '__file__', None)
        if not location:
            continue
        path = Path(location).resolve()
        if name.split('.')[0] in PACKAGE_ROOTS:
            if str(path) not in origin_map:
                raise ValueError(f'Product module borrowed from outside installed wheel: {name}')
            rows.append({'module': name, 'path': str(path), 'sha256': sha(path), 'kind': 'installed_product'})
        elif path.is_relative_to(epoch):
            relative = str(path.relative_to(epoch))
            if not relative.startswith('tests/'):
                continue
            rows.append({'module': name, 'path': str(path), 'sha256': sha(path), 'kind': 'frozen_test_helper'})
    return rows


def bind_frozen_tests(epoch: Path) -> None:
    """Bind only the hash-verified test package, including multiprocessing spawn.

    Spawn reexecutes this script as __mp_main__ before unpickling targets. The
    package path is explicitly confined to frozen tests; product lookup remains
    ordinary installed imports and sys.path is never modified.
    """
    manifest = read_json(epoch / 'source-manifest.json')
    closed(manifest, {'format', 'files'}, 'spawn source manifest')
    if manifest['format'] != 'devbench.rna-tracing-source/1':
        raise ValueError('Unknown frozen test manifest format')
    binding = read_json(epoch / 'runtime-binding.json')
    if binding['source_manifest_sha256'] != sha(epoch / 'source-manifest.json'):
        raise ValueError('Spawn source binding mismatch')
    if absolute(binding['runtime_prefix']) != Path(sys.prefix).resolve():
        raise ValueError('Spawn runtime prefix mismatch')
    if manifest['files'].get('scripts/run_rna_tracing_verification.py') != sha(Path(__file__).resolve()):
        raise ValueError('Spawn runner binding mismatch')
    for relative, expected in manifest['files'].items():
        if not relative.startswith('tests/'):
            continue
        path = epoch / relative
        if '..' in Path(relative).parts or not path.resolve().is_relative_to(epoch / 'tests'):
            raise ValueError('Frozen test package path escapes epoch')
        if sha(path) != expected:
            raise ValueError('Frozen spawn test/helper hash mismatch')
    if 'tests/__init__.py' not in manifest['files']:
        raise ValueError('Frozen tests package missing')
    spec = importlib.util.spec_from_file_location('tests', epoch / 'tests/__init__.py',
        submodule_search_locations=[str(epoch / 'tests')])
    package = importlib.util.module_from_spec(spec)
    sys.modules['tests'] = package
    spec.loader.exec_module(package)


def write_json(path: Path, value: dict) -> None:
    with path.open('x') as stream:
        json.dump(value, stream, indent=2, sort_keys=True, allow_nan=False)
        stream.write('\n')


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
    if any(a.is_relative_to(b) or b.is_relative_to(a) for i, a in enumerate(roots) for b in roots[i+1:]):
        raise ValueError('Source/runtime/output/fixture storage must be disjoint')
    if output.exists() or fixture_root.exists() or actor_root.exists():
        raise ValueError('Fresh output and fixture roots required; attempts are never overwritten')
    output.mkdir(parents=True, mode=0o700)
    started = time.time()
    write_json(output / 'start.json', {'started_utc_epoch': started, 'argv': sys.argv,
        'scope': args.scope, 'python': sys.executable, 'epoch_root': str(epoch)})
    code = 2
    result = {'status': 'blocked', 'scope': 'software'}
    before = None
    source_before = None
    origin_map = None
    try:
        if not args.allow_bounded_local_fixtures:
            raise PermissionError('Required bounded software fixtures were not authorized')
        manifest = read_json(epoch / 'source-manifest.json')
        closed(manifest, {'format', 'files'}, 'source manifest')
        if manifest['format'] != 'devbench.rna-tracing-source/1':
            raise ValueError('Unknown source format')
        source_before = source_state(epoch, manifest)
        if source_before.get('scripts/run_rna_tracing_verification.py') != sha(Path(__file__).resolve()):
            raise ValueError('Runner is not bound to frozen epoch')
        origin_map = validate_runtime(epoch, manifest, read_json(epoch / 'runtime-binding.json'))
        selected = validate_selection(epoch, manifest, read_json(selection_path))
        before = runtime_state()
        runtime_binding = read_json(epoch / 'runtime-binding.json')
        frozen_runtime = absolute(runtime_binding['runtime_manifest_path'])
        if sha(frozen_runtime) != runtime_binding['runtime_manifest_sha256']:
            raise ValueError('Frozen runtime manifest hash mismatch')
        # Runtime inventories can exceed generic manifest bound (many RECORD assets).
        if frozen_runtime.stat().st_size > 32 * 1024 * 1024:
            raise ValueError('Runtime manifest exceeds bounded read')
        if json.loads(frozen_runtime.read_text()) != before:
            raise ValueError('Runtime drift since frozen inventory')
        write_json(output / 'runtime-before.json', before)
        write_json(output / 'source-before.json', source_before)
        helper_path = epoch / 'tests/support/rna_trace_fixture_paths.py'
        if source_before.get('tests/support/rna_trace_fixture_paths.py') != sha(helper_path):
            raise ValueError('Frozen fixture helper missing')
        spec = importlib.util.spec_from_file_location('rna_trace_fixture_paths', helper_path)
        helper = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = helper
        spec.loader.exec_module(helper)
        plugin = helper.FixturePaths(epoch, output, fixture_root, actor_root)
        # Pytest owns test/helper imports; product imports remain installed wheel imports.
        os.environ['PYTEST_DISABLE_PLUGIN_AUTOLOAD'] = '1'
        os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
        os.environ['DEVBENCH_RUN_LOCAL_FIXTURES'] = '1'
        bind_frozen_tests(epoch)
        # Stabilize the real installed bundle hash alias before tests temporarily
        # mock checkpoint.sha256 during metadata-only planning. This prevents
        # lazy module import from retaining a test mock across later fixtures.
        import devbench.analysis.bundles
        module_origins(origin_map, epoch)
        import pytest
        code = int(pytest.main(['--import-mode=importlib', '--continue-on-collection-errors', '-p', 'no:cacheprovider', '--basetemp=' + str(output / 'tmp'),
            '--junitxml=' + str(output / 'junit.xml'), *map(str, selected)], plugins=[plugin]))
        result = {'status': 'passed' if code == 0 else 'nonpasses_retained', 'pytest_exit_code': code,
                  'scope': 'software', 'selected_tests': [str(p) for p in selected]}
    except Exception as error:
        result['error_type'] = type(error).__name__
        result['error'] = str(error)
        (output / 'runner-error.txt').write_text(traceback.format_exc())
    finally:
        try:
            if origin_map is not None:
                write_json(output / 'module-origins.json', {'origins': module_origins(origin_map, epoch)})
            if before is not None:
                after = runtime_state()
                write_json(output / 'runtime-after.json', after)
                result['runtime_unchanged'] = before == after
                if not result['runtime_unchanged']:
                    code = 2
            if source_before is not None:
                after_source = source_state(epoch, manifest)
                write_json(output / 'source-after.json', after_source)
                result['source_unchanged'] = source_before == after_source
            result['elapsed_seconds'] = time.time() - started
        except Exception as error:
            result['verification_error'] = str(error)
            code = 2
        if code == 2 and ('verification_error' in result or result.get('runtime_unchanged') is False):
            result['status'] = 'blocked_identity_verification'
        result['runner_exit_code'] = code
        write_json(output / 'result.json', result)
    return code


if __name__ == '__mp_main__':
    bind_frozen_tests(Path(__file__).resolve().parents[1])


if __name__ == '__main__':
    raise SystemExit(main())
