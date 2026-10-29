"""Publication gates are explicit software evidence, never real-trial admission."""
from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path
import socket
import tempfile
import uuid

import pytest

from scripts import freeze_rna_tracing_epoch as freezer
from scripts import run_rna_tracing_verification as runner


PUBLICATION_BASE = frozenset('tests/' + name + '.py' for name in (
    'test_rna_publication_evidence', 'test_rna_publication_comparison',
    'test_rna_scientific_trial', 'test_rna_scientific_trial_contract',
    'test_execution_contract_repair', 'test_figure_artifacts',
    'test_scientific_review', 'test_scientific_review_agent', 'test_completed_scientific_review'))
SCOPE_VERSION = 'rna-publication/1'


def selection_fixture(tmp_path, *, publication=True):
    epoch = tmp_path / 'epoch'
    epoch.mkdir()
    names = set(runner.FOCUSED_BASE)
    if publication:
        names.update(PUBLICATION_BASE)
        names.add('tests/test_rna_publication_verification.py')
    files = {}
    for name in sorted(names):
        path = epoch / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('"""Sealed synthetic selection member."""\n')
        files[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest = {'format': 'devbench.rna-tracing-source/2' if publication else 'devbench.rna-tracing-source/1', 'files': files}
    selection = {'format': 'devbench.rna-tracing-selection/2' if publication else 'devbench.rna-tracing-selection/1',
                 'scope': 'publication-software' if publication else 'software', 'kind': 'focused',
                 'source_manifest_sha256': '', 'prior_selection_sha256': runner.PRIOR_SELECTION_SHA256,
                 'allowed_fixture_classes': ['synthetic', 'local'],
                 'tests': [{'path': name, 'sha256': files[name]} for name in sorted(names)]}
    if publication:
        manifest.update(scope='publication-software', scope_version=SCOPE_VERSION)
        selection['scope_version'] = SCOPE_VERSION
    manifest_path = epoch / 'source-manifest.json'
    manifest_path.write_text(json.dumps(manifest, sort_keys=True))
    selection['source_manifest_sha256'] = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    return epoch, manifest, selection


def test_publication_scope_accepts_exact_sealed_selection(tmp_path):
    epoch, manifest, selection = selection_fixture(tmp_path)
    selected = runner.validate_selection(epoch, manifest, selection)
    assert selected == [epoch / row['path'] for row in selection['tests']]


def test_historical_scope_keeps_its_closed_v1_selection(tmp_path):
    epoch, manifest, selection = selection_fixture(tmp_path, publication=False)
    assert runner.validate_selection(epoch, manifest, selection) == [epoch / name for name in sorted(runner.FOCUSED_BASE)]


def test_publication_scope_rejects_unknown_version(tmp_path):
    epoch, manifest, selection = selection_fixture(tmp_path)
    selection['scope_version'] = 'rna-publication/99'
    with pytest.raises(ValueError, match='scope version'):
        runner.validate_selection(epoch, manifest, selection)


def test_publication_selection_cannot_be_admitted_by_historical_epoch(tmp_path):
    epoch, manifest, selection = selection_fixture(tmp_path)
    historical = {'format': 'devbench.rna-tracing-source/1', 'files': manifest['files']}
    with pytest.raises(ValueError, match='scope.*epoch|epoch.*scope'):
        runner.validate_selection(epoch, historical, selection)


def test_publication_selection_rejects_omitted_existing_target(tmp_path):
    epoch, manifest, selection = selection_fixture(tmp_path)
    selection['tests'] = [row for row in selection['tests'] if row['path'] != 'tests/test_rna_publication_evidence.py']
    with pytest.raises(ValueError, match='exact'):
        runner.validate_selection(epoch, manifest, selection)


def test_publication_selection_rejects_arbitrary_campaign(tmp_path):
    epoch, manifest, selection = selection_fixture(tmp_path)
    selection['tests'].append({'path': 'tests/test_other_campaign.py', 'sha256': 'a' * 64})
    with pytest.raises(ValueError, match='Unauthorized'):
        runner.validate_selection(epoch, manifest, selection)


def test_publication_selection_rejects_duplicate_and_tampered_test(tmp_path):
    epoch, manifest, selection = selection_fixture(tmp_path)
    duplicate = deepcopy(selection)
    duplicate['tests'].append(duplicate['tests'][0])
    with pytest.raises(ValueError, match='Duplicate'):
        runner.validate_selection(epoch, manifest, duplicate)
    (epoch / selection['tests'][0]['path']).write_text('changed')
    with pytest.raises(ValueError, match='hash mismatch'):
        runner.validate_selection(epoch, manifest, selection)


def test_freezer_requires_explicit_publication_scope_version_before_reads(tmp_path):
    with pytest.raises(ValueError, match='scope version'):
        freezer.freeze(str(tmp_path), str(tmp_path / 'report'), 'invalid',
                       scope='publication-software', scope_version='rna-publication/99')


def test_junit_counts_preserve_nonpasses_and_collection_errors(tmp_path):
    assert hasattr(runner, 'junit_counts'), 'Verification does not retain exact JUnit outcome counts'
    path = tmp_path / 'junit.xml'
    path.write_text('<testsuites><testsuite><testcase name="ok"/><testcase name="fail"><failure/></testcase>'
                    '<testcase name="collection"><error/></testcase><testcase name="skip"><skipped/></testcase>'
                    '</testsuite></testsuites>')
    assert runner.junit_counts(path) == {'tests': 4, 'passed': 1, 'failed': 1, 'errors': 1, 'skipped': 1}


def test_missing_junit_is_unknown_evidence(tmp_path):
    assert hasattr(runner, 'junit_counts'), 'Missing verification count contract'
    with pytest.raises(ValueError, match='JUnit'):
        runner.junit_counts(tmp_path / 'absent.xml')


@pytest.mark.parametrize('exit_code', [2, 3, 4, 5])
def test_incomplete_pytest_exit_never_promotes_partial_xml_counts(exit_code):
    assert hasattr(runner, 'publication_pytest_outcome'), 'Interrupted verification promotes partial XML to completed counts'
    observed = {'tests': 2, 'passed': 2, 'failed': 0, 'errors': 0, 'skipped': 0}
    result = runner.publication_pytest_outcome(exit_code, observed)
    assert result['complete_gate'] is False
    assert result['counts'] is None
    assert result['observed_junit_counts'] == observed
    assert result['status'] in ('interrupted_incomplete', 'unknown_incomplete')


@pytest.mark.parametrize('exit_code', [0, 1])
def test_completed_pytest_exit_preserves_actual_junit_nonpasses(exit_code):
    assert hasattr(runner, 'publication_pytest_outcome'), 'Missing completion classification'
    observed = {'tests': 2, 'passed': 2 - exit_code, 'failed': exit_code, 'errors': 0, 'skipped': 0}
    result = runner.publication_pytest_outcome(exit_code, observed)
    assert result['complete_gate'] is True
    assert result['counts'] == observed
    assert result['status'] == ('passed' if exit_code == 0 else 'nonpasses_retained')


def test_publication_software_guard_denies_provider_network_and_native_execution():
    assert hasattr(runner, 'PublicationSoftwareGuard'), 'Publication software guard is unavailable'
    guard = runner.PublicationSoftwareGuard()
    class Connection:
        family = socket.AF_INET
    with pytest.raises(PermissionError, match='Network'):
        guard.audit('socket.connect', (Connection(), ('203.0.113.10', 443)))
    with pytest.raises(PermissionError, match='Native'):
        guard.audit('subprocess.Popen', ('/usr/local/bin/codex', ['codex', 'exec'], None, None))
    guard.audit('socket.connect', (Connection(), ('127.0.0.1', 8010)))
    assert len(guard.events) == 2


def test_verification_scripts_are_installed_product_modules():
    prefix = Path(__import__('sys').prefix).resolve()
    assert Path(runner.__file__).resolve().is_relative_to(prefix)
    assert Path(freezer.__file__).resolve().is_relative_to(prefix)


def test_publication_trial_cli_is_installed_and_explicitly_declared():
    spec = importlib.util.find_spec('scripts.run_rna_scientific_trial')
    assert spec is not None, 'Publication trial preparation CLI is absent from the installed wheel'
    assert Path(spec.origin).resolve().is_relative_to(Path(__import__('sys').prefix).resolve())
    assert 'scripts/run_rna_scientific_trial.py' in freezer.PUBLICATION_SCRIPTS


def test_publication_existing_review_fixture_preserves_frozen_source(tmp_path):
    assert hasattr(runner, 'publication_fixture_plugin'), 'Selected completed-review fixture writes into frozen source'
    from tests.support import rna_trace_fixture_paths as helper
    root = runner.WORKSPACE
    roots = []
    alphabet = '0123456789abcdefghijklmnopqrstuvwxyz'
    for _ in range(4096):
        if len(roots) == 2:
            break
        token = uuid.uuid4().int
        candidate = root / ('.' + alphabet[token % len(alphabet)] + alphabet[(token // len(alphabet)) % len(alphabet)])
        if not candidate.exists() and candidate not in roots:
            roots.append(candidate)
    if len(roots) != 2:
        raise RuntimeError('Fresh short fixture namespace exhausted')
    epoch = tmp_path / 'epoch'
    epoch.mkdir()
    output = tmp_path / 'output'
    output.mkdir()
    parent = epoch / 'reports/completed-scientific-review/2026-09-22/workspaces'
    plugin = runner.publication_fixture_plugin(helper, epoch, output, *roots)
    plugin.pytest_configure(None)
    try:
        parent.mkdir(parents=True, exist_ok=True)
        allocated = Path(tempfile.mkdtemp(prefix='handoff-', dir=parent))
        assert allocated.is_relative_to(roots[0])
        assert not parent.exists()
    finally:
        plugin.pytest_unconfigure(None)
    evidence = json.loads((output / 'fixture-map.json').read_text())
    assert evidence['allocations'][0]['requested_dir'] == str(parent)
    assert evidence['source_resolution_changed'] is False
    assert evidence['product_semantics_changed'] is False
