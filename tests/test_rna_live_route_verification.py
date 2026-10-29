"""Closed additive live-route software selection preserves the historical /1 gate."""
from copy import deepcopy
import hashlib
import json

import pytest

from scripts import freeze_rna_tracing_epoch as freezer
from scripts import run_rna_tracing_verification as runner


LIVE_VERSION = 'rna-live-route/1'
MEDIATED_VERSION = 'rna-live-route/2'
OBSERVABILITY_VERSION = 'rna-live-route/3'
CONTEXT_VERSION = 'rna-live-route/4'
CONTEXT_SOURCES = frozenset({'devbench/study/rna_hosted_context_contract.py'})
LIVE_SOURCES = frozenset({
    'devbench/study/rna_hosted_workflow_contract.py',
    'devbench/study/rna_hosted_workflow.py', 'devbench/study/rna_hosted_tls.py',
    'devbench/study/rna_hosted_connection_contract.py',
    'devbench/study/rna_connection_prepare.py',
    'devbench/study/langsmith_outbox.py', 'devbench/study/rna_langsmith_export.py',
    'devbench/study/rna_langsmith_live.py', 'devbench/study/rna_langsmith_verify.py',
    'devbench/study/rna_hosted_exporter.py'})
MEDIATED_SOURCES = frozenset('devbench/analysis/' + name + '.py' for name in (
    'rna_mediated_backend', 'rna_route_qualification', 'method_planner',
    'workflow_operations', 'rna_trial_authority', 'rna_scientific_trial'))
MEDIATED_TESTS = frozenset({'tests/test_rna_mediated_backend.py', 'tests/test_rna_route_qualification.py'})
OBSERVABILITY_SOURCES = frozenset('devbench/analysis/' + name + '.py' for name in (
    'rna_mediated_observability', 'rna_trace_receipts', 'rna_trace_projection',
    'rna_trace_contract', 'methods'))
OBSERVABILITY_TESTS = frozenset({
    'tests/test_rna_mediated_observability.py', 'tests/test_rna_mediated_capture_registration.py'})
HISTORICAL_NEW_TESTS = frozenset('tests/' + name + '.py' for name in (
    'test_rna_publication_profiles', 'test_rna_publication_sources', 'test_rna_reference_extraction',
    'test_rna_execution_inputs', 'test_rna_comparison_outputs', 'test_rna_publication_metrics',
    'test_rna_publication_report', 'test_rna_publication_figures', 'test_rna_publication_verification',
    'test_rna_scientific_trial_v2', 'test_rna_trial_authority', 'test_rna_trial_dispatch_guard',
    'test_rna_publication_authority', 'test_rna_publication_comparison_v2',
    'test_rna_assessment_workflow_binding', 'test_rna_execution_route_qualification',
    'test_rna_execution_route_observability', 'test_rna_hosted_qualification',
    'test_rna_hosted_trace_invariance', 'test_rna_langsmith_verify'))


def live_selection(tmp_path, *, kind='focused', version=LIVE_VERSION):
    assert getattr(runner, 'LIVE_ROUTE_SCOPE_VERSION', None) == LIVE_VERSION, 'Explicit live-route software scope is absent'
    epoch = tmp_path / 'epoch'
    epoch.mkdir()
    names = runner.LIVE_ROUTE_TESTS | (runner.NEW_TESTS if kind == 'hosted' else
        (runner.HISTORICAL_TESTS if kind == 'broad' else runner.FOCUSED_BASE) | runner.PUBLICATION_BASE_TESTS)
    sources = LIVE_SOURCES
    if version in (MEDIATED_VERSION, OBSERVABILITY_VERSION, CONTEXT_VERSION):
        names |= MEDIATED_TESTS
        sources |= MEDIATED_SOURCES
    if version in (OBSERVABILITY_VERSION, CONTEXT_VERSION):
        names |= OBSERVABILITY_TESTS
        sources |= OBSERVABILITY_SOURCES
    if version == CONTEXT_VERSION:
        sources |= CONTEXT_SOURCES
    files = {}
    for name in sorted(names | sources):
        path = epoch / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('"""Sealed synthetic selection member."""\n')
        files[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest = dict(format='devbench.rna-tracing-source/2', scope='publication-software',
                    scope_version=version, files=files)
    path = epoch / 'source-manifest.json'
    path.write_text(json.dumps(manifest, sort_keys=True))
    selection = dict(format='devbench.rna-tracing-selection/2', scope='publication-software',
                     scope_version=version, kind=kind,
                     source_manifest_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                     prior_selection_sha256=runner.PRIOR_SELECTION_SHA256,
                     allowed_fixture_classes=['synthetic', 'local'],
                     tests=[dict(path=name, sha256=files[name]) for name in sorted(names)])
    return epoch, manifest, selection


def test_live_route_version_is_explicit_and_historical_inventory_is_unchanged():
    assert getattr(freezer, 'LIVE_ROUTE_SCOPE_VERSION', None) == LIVE_VERSION, 'Additive live-route freezer scope is absent'
    assert freezer.PUBLICATION_SCOPE_VERSION == runner.PUBLICATION_SCOPE_VERSION == 'rna-publication/1'
    assert freezer.PUBLICATION_NEW_TESTS == runner.PUBLICATION_NEW_TESTS == HISTORICAL_NEW_TESTS
    assert freezer.LIVE_ROUTE_TESTS == runner.LIVE_ROUTE_TESTS
    assert freezer.LIVE_ROUTE_SOURCES == runner.LIVE_ROUTE_SOURCES == LIVE_SOURCES
    assert freezer.MEDIATED_ROUTE_SCOPE_VERSION == runner.MEDIATED_ROUTE_SCOPE_VERSION == MEDIATED_VERSION
    assert freezer.MEDIATED_ROUTE_SOURCES == runner.MEDIATED_ROUTE_SOURCES == MEDIATED_SOURCES
    assert freezer.MEDIATED_ROUTE_TESTS == runner.MEDIATED_ROUTE_TESTS == MEDIATED_TESTS
    assert freezer.MEDIATED_OBSERVABILITY_SCOPE_VERSION == runner.MEDIATED_OBSERVABILITY_SCOPE_VERSION == OBSERVABILITY_VERSION
    assert freezer.MEDIATED_OBSERVABILITY_SOURCES == runner.MEDIATED_OBSERVABILITY_SOURCES == OBSERVABILITY_SOURCES
    assert freezer.MEDIATED_OBSERVABILITY_TESTS == runner.MEDIATED_OBSERVABILITY_TESTS == OBSERVABILITY_TESTS
    assert {'tests/test_rna_hosted_tls.py', 'tests/test_rna_hosted_workflow_contract.py',
            'tests/test_rna_hosted_workflow_projection.py', 'tests/test_rna_hosted_workflow.py',
            'tests/test_rna_live_route_verification.py'} <= freezer.LIVE_ROUTE_TESTS
    assert {'tests/test_rna_langsmith_binding.py', 'tests/test_rna_langsmith_schema.py'} <= freezer.LIVE_ROUTE_TESTS
    assert {'devbench/study/rna_hosted_tls.py', 'devbench/study/rna_hosted_workflow.py',
            'devbench/study/rna_hosted_workflow_contract.py'} <= freezer.LIVE_ROUTE_SOURCES


def test_live_route_selection_accepts_exact_versioned_module_closure(tmp_path):
    epoch, manifest, selection = live_selection(tmp_path)
    assert runner.validate_selection(epoch, manifest, selection) == [epoch / row['path'] for row in selection['tests']]


def test_live_route_selection_rejects_omitted_live_module(tmp_path):
    epoch, manifest, selection = live_selection(tmp_path)
    selection['tests'] = [row for row in selection['tests'] if row['path'] != 'tests/test_rna_hosted_tls.py']
    with pytest.raises(ValueError, match='exact'):
        runner.validate_selection(epoch, manifest, selection)


@pytest.mark.parametrize('missing_source', sorted(LIVE_SOURCES))
def test_live_route_manifest_rejects_each_missing_product_source(tmp_path, missing_source):
    epoch, manifest, selection = live_selection(tmp_path)
    del manifest['files'][missing_source]
    (epoch / 'source-manifest.json').write_text(json.dumps(manifest, sort_keys=True))
    selection['source_manifest_sha256'] = hashlib.sha256((epoch / 'source-manifest.json').read_bytes()).hexdigest()
    with pytest.raises(ValueError, match='required product source closure'):
        runner.validate_selection(epoch, manifest, selection)


@pytest.mark.parametrize('kind', ['focused', 'hosted'])
def test_mediated_scope_requires_complete_hosted_and_mediated_selection(tmp_path, kind):
    epoch, manifest, selection = live_selection(tmp_path, version=MEDIATED_VERSION, kind=kind)
    assert runner.validate_selection(epoch, manifest, selection) == [epoch / row['path'] for row in selection['tests']]
    selection['tests'] = [row for row in selection['tests'] if row['path'] != 'tests/test_rna_mediated_backend.py']
    with pytest.raises(ValueError, match='exact'):
        runner.validate_selection(epoch, manifest, selection)


@pytest.mark.parametrize('missing_source', sorted(MEDIATED_SOURCES))
def test_mediated_manifest_rejects_each_missing_product_source(tmp_path, missing_source):
    epoch, manifest, selection = live_selection(tmp_path, version=MEDIATED_VERSION)
    del manifest['files'][missing_source]
    (epoch / 'source-manifest.json').write_text(json.dumps(manifest, sort_keys=True))
    selection['source_manifest_sha256'] = hashlib.sha256((epoch / 'source-manifest.json').read_bytes()).hexdigest()
    with pytest.raises(ValueError, match='required product source closure'):
        runner.validate_selection(epoch, manifest, selection)


@pytest.mark.parametrize('kind', ['focused', 'hosted'])
@pytest.mark.parametrize('omitted', sorted(MEDIATED_TESTS | OBSERVABILITY_TESTS))
def test_observability_scope_keeps_both_mediated_and_observability_modules_mandatory(tmp_path, kind, omitted):
    epoch, manifest, selection = live_selection(tmp_path, version=OBSERVABILITY_VERSION, kind=kind)
    assert runner.validate_selection(epoch, manifest, selection) == [epoch / row['path'] for row in selection['tests']]
    selection['tests'] = [row for row in selection['tests'] if row['path'] != omitted]
    with pytest.raises(ValueError, match='exact'):
        runner.validate_selection(epoch, manifest, selection)


@pytest.mark.parametrize('missing_source', sorted(OBSERVABILITY_SOURCES))
def test_observability_manifest_rejects_each_missing_product_source(tmp_path, missing_source):
    epoch, manifest, selection = live_selection(tmp_path, version=OBSERVABILITY_VERSION)
    del manifest['files'][missing_source]
    (epoch / 'source-manifest.json').write_text(json.dumps(manifest, sort_keys=True))
    selection['source_manifest_sha256'] = hashlib.sha256((epoch / 'source-manifest.json').read_bytes()).hexdigest()
    with pytest.raises(ValueError, match='required product source closure'):
        runner.validate_selection(epoch, manifest, selection)


@pytest.mark.parametrize('version', [LIVE_VERSION, MEDIATED_VERSION])
def test_earlier_live_versions_cannot_admit_new_observability_tests(tmp_path, version):
    epoch, manifest, selection = live_selection(tmp_path, version=OBSERVABILITY_VERSION)
    manifest['scope_version'] = selection['scope_version'] = version
    if version == LIVE_VERSION:
        selection['tests'] = [row for row in selection['tests'] if row['path'] not in MEDIATED_TESTS]
    (epoch / 'source-manifest.json').write_text(json.dumps(manifest, sort_keys=True))
    selection['source_manifest_sha256'] = hashlib.sha256((epoch / 'source-manifest.json').read_bytes()).hexdigest()
    with pytest.raises(ValueError, match='Unauthorized'):
        runner.validate_selection(epoch, manifest, selection)


def test_hosted_selection_is_exact_and_cannot_omit_trace_regressions(tmp_path):
    epoch, manifest, selection = live_selection(tmp_path, kind='hosted')
    assert runner.validate_selection(epoch, manifest, selection) == [epoch / row['path'] for row in selection['tests']]
    selection['tests'] = [row for row in selection['tests'] if row['path'] != 'tests/test_rna_trace_contract.py']
    with pytest.raises(ValueError, match='exact'):
        runner.validate_selection(epoch, manifest, selection)


def test_historical_publication_scope_rejects_hosted_selection_kind(tmp_path):
    epoch, manifest, selection = live_selection(tmp_path, kind='hosted')
    manifest['scope_version'] = selection['scope_version'] = 'rna-publication/1'
    (epoch / 'source-manifest.json').write_text(json.dumps(manifest, sort_keys=True))
    selection['source_manifest_sha256'] = hashlib.sha256((epoch / 'source-manifest.json').read_bytes()).hexdigest()
    with pytest.raises(ValueError, match='Unknown selection kind'):
        runner.validate_selection(epoch, manifest, selection)


def test_live_route_selection_rejects_unknown_version_and_scope_mismatch(tmp_path):
    epoch, manifest, selection = live_selection(tmp_path)
    unknown = deepcopy(selection)
    unknown['scope_version'] = 'rna-live-route/99'
    with pytest.raises(ValueError, match='scope version'):
        runner.validate_selection(epoch, manifest, unknown)
    manifest['scope_version'] = 'rna-publication/1'
    with pytest.raises(ValueError, match='version.*epoch|epoch.*version'):
        runner.validate_selection(epoch, manifest, selection)


def test_context_observation_scope_is_additive_and_has_one_explicit_source():
    assert getattr(freezer, 'CONTEXT_OBSERVATION_SCOPE_VERSION', None) == CONTEXT_VERSION
    assert getattr(runner, 'CONTEXT_OBSERVATION_SCOPE_VERSION', None) == CONTEXT_VERSION
    assert freezer.CONTEXT_OBSERVATION_SOURCES == runner.CONTEXT_OBSERVATION_SOURCES == CONTEXT_SOURCES


@pytest.mark.parametrize('kind', ['focused', 'broad', 'hosted'])
def test_context_observation_keeps_inherited_exact_selection(tmp_path, kind):
    epoch, manifest, selection = live_selection(tmp_path, version=CONTEXT_VERSION, kind=kind)
    assert runner.validate_selection(epoch, manifest, selection) == [epoch / row['path'] for row in selection['tests']]
    selection['tests'] = [row for row in selection['tests']
                          if row['path'] != 'tests/test_rna_mediated_observability.py']
    with pytest.raises(ValueError, match='exact'):
        runner.validate_selection(epoch, manifest, selection)


@pytest.mark.parametrize('kind', ['focused', 'broad', 'hosted'])
def test_context_observation_rejects_omitted_context_contract_source(tmp_path, kind):
    epoch, manifest, selection = live_selection(tmp_path, version=CONTEXT_VERSION, kind=kind)
    del manifest['files']['devbench/study/rna_hosted_context_contract.py']
    (epoch / 'source-manifest.json').write_text(json.dumps(manifest, sort_keys=True))
    selection['source_manifest_sha256'] = hashlib.sha256((epoch / 'source-manifest.json').read_bytes()).hexdigest()
    with pytest.raises(ValueError, match='required product source closure'):
        runner.validate_selection(epoch, manifest, selection)


@pytest.mark.parametrize('version', [LIVE_VERSION, MEDIATED_VERSION, OBSERVABILITY_VERSION])
def test_earlier_live_source_closures_do_not_require_context_contract(tmp_path, version):
    _, manifest, _ = live_selection(tmp_path, version=version)
    assert 'devbench/study/rna_hosted_context_contract.py' not in manifest['files']
    assert runner.source_scope(manifest) == 'publication-software'


@pytest.mark.parametrize('source_version,selection_version', [
    (OBSERVABILITY_VERSION, CONTEXT_VERSION), (CONTEXT_VERSION, OBSERVABILITY_VERSION)])
def test_context_observation_version_must_match_source(tmp_path, source_version, selection_version):
    epoch, manifest, selection = live_selection(tmp_path, version=CONTEXT_VERSION)
    manifest['scope_version'] = source_version
    selection['scope_version'] = selection_version
    (epoch / 'source-manifest.json').write_text(json.dumps(manifest, sort_keys=True))
    selection['source_manifest_sha256'] = hashlib.sha256((epoch / 'source-manifest.json').read_bytes()).hexdigest()
    with pytest.raises(ValueError, match='version.*epoch|epoch.*version'):
        runner.validate_selection(epoch, manifest, selection)


def test_historical_publication_selection_cannot_include_live_route_modules(tmp_path):
    epoch, manifest, selection = live_selection(tmp_path)
    manifest['scope_version'] = selection['scope_version'] = 'rna-publication/1'
    (epoch / 'source-manifest.json').write_text(json.dumps(manifest, sort_keys=True))
    selection['source_manifest_sha256'] = hashlib.sha256((epoch / 'source-manifest.json').read_bytes()).hexdigest()
    with pytest.raises(ValueError, match='Unauthorized'):
        runner.validate_selection(epoch, manifest, selection)
