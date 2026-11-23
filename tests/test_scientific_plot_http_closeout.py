"""Actual loopback plotting interfaces; synthetic software evidence, no provider."""
from contextlib import contextmanager
import hashlib
import http.client
import io
import json
import threading
import zipfile

import pytest

from devbench.analysis.workbench_projects import WorkbenchProjectHub
from devbench.analysis.workbench_ui import WorkbenchService, make_server
from tests.test_episode_agent import ScriptedSelector, sequence
from tests.test_workbench_local_analyst import app, grant, prepared, wait


@contextmanager
def running(service, **kwargs):
    server = make_server(service, port=0, **kwargs)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_port
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def call(port, path, *, body=None, origin=None, project=None):
    headers = {}
    if origin is not None:
        headers['Origin'] = origin
    if project is not None:
        headers['X-Workbench-Project'] = project
    if body is not None:
        headers['Content-Type'] = 'application/json'
    connection = http.client.HTTPConnection('127.0.0.1', port, timeout=60)
    try:
        connection.request('POST' if body is not None else 'GET', path,
                           body=None if body is None else json.dumps(body), headers=headers)
        response = connection.getresponse()
        return response.status, response.read(), dict(response.getheaders())
    finally:
        connection.close()


def hashes(root):
    return {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(root.rglob('*')) if path.is_file()}


@pytest.fixture(scope='module')
def episode_http(module_workspace_tmp_path):
    root = module_workspace_tmp_path
    spec, _ = prepared(root)
    service = app(root, spec)
    selector = ScriptedSelector(sequence())
    service.local_analyst._selector = lambda episode: selector
    hub = None
    try:
        with running(service) as port:
            origin = 'http://127.0.0.1:' + str(port)
            status, raw, _ = call(port, '/api/local-analyst',
                                  body=grant(service.local_analyst), origin=origin)
            assert status == 202, raw
            identity = json.loads(raw)['id']
            state = wait(service, identity)
            assert state['status'] == 'completed'
            assert state['episode_verification']['valid']
            assert state['summary']['scientific_correctness'] is None
            assert len(selector.contexts) == 5
            route = '/api/local-analyst/' + identity
            status, raw, _ = call(port, route + '/scientific-plots')
            assert status == 200, raw
            workspace = json.loads(raw)
            assert workspace['format'] == 'devbench.scientific-plot-workspace/1'

            def empty_factory(workspace, data, actors, executor):
                return WorkbenchService(workspace, [data], agent_workspaces=actors,
                                        shared_executor=executor, enable_scientific=True)

            hub = WorkbenchProjectHub(root / 'project-catalog', empty_factory,
                                      existing={'retained': service})
            empty = hub.create({'name': 'No saved results'})
            with running(service, project_hub=hub, default_project_id=empty['id']) as project_port:
                parent_root = service.workspace / 'jobs' / identity
                baseline = hashes(parent_root)
                parent_state = service._job(identity)
                yield {'root': root, 'service': service, 'port': port, 'origin': origin,
                       'job': identity, 'route': route, 'workspace': workspace,
                       'parent_root': parent_root, 'baseline': baseline, 'state': parent_state,
                       'hub': hub, 'empty': empty, 'project_port': project_port}
                assert hashes(parent_root) == baseline
                assert service._job(identity) == parent_state
                assert len(selector.contexts) == 5
    finally:
        if hub is not None:
            hub.close()
        else:
            service.close()


def test_http_bundle_preserves_exact_workspace_request_and_checked_source(episode_http, monkeypatch):
    from devbench.analysis import workbench
    from devbench.analysis.scientific_plot_revisions import load_scientific_revision_bundle
    value = episode_http
    service, workspace = value['service'], value['workspace']

    def forbidden(*args, **kwargs):
        pytest.fail('Presentation HTTP route launched an analysis')

    for name in ('run_stage', 'execute_bundle', 'prepare_bundle'):
        monkeypatch.setattr(workbench, name, forbidden)
    selected = next(panel for panel in workspace['panels'] if panel['marks'])
    request = {'panel_id': selected['id'], 'scope': 'complete',
               'reference_lines': [{'axis': 'x', 'value': 0, 'label': 'Display guide'}]}
    status, bundle, headers = call(value['port'], value['route'] + '/scientific-bundle', origin=value['origin'], body=request)
    assert status == 200, bundle[:2000]
    assert headers['Content-Type'] == 'application/zip'
    assert headers['Content-Disposition'] == 'attachment; filename="scientific-presentation.zip"'
    root = headers['X-Plot-Revision-Root']
    loaded = load_scientific_revision_bundle(bundle, root, storage_root=value['root'])
    assert loaded['verified'] and loaded['root_hash'] == root
    assert loaded['workspace'] == workspace and loaded['request'] == request
    source_path, _, _ = service.local_analyst.download(value['job'], 'result')
    source_hash = hashlib.sha256(source_path.read_bytes()).hexdigest()
    binding = loaded['source_binding']
    assert binding['verification'] == 'local_file_hash_checked'
    assert binding['source_sha256'] == binding['source_artifact']['sha256'] == source_hash
    assert binding['source_sha256'] == workspace['source']['sha256']
    assert binding['run_id'] == workspace['source']['run_id'] == value['job']
    assert binding['artifact_id'] is None
    assert loaded['receipt']['status'] == 'completed_presentation'
    assert loaded['receipt']['numerical_stage_launches'] == loaded['receipt']['model_sessions'] == 0
    with zipfile.ZipFile(io.BytesIO(bundle)) as archive:
        assert binding['workspace_sha256'] == hashlib.sha256(archive.read('payload/workspace.json')).hexdigest()
        assert loaded['receipt']['request_sha256'] == hashlib.sha256(archive.read('payload/request.json')).hexdigest()
        assert archive.read('payload/figure.pdf').startswith(b'%PDF-')
        assert archive.read('payload/figure.png').startswith(b'\x89PNG')
        assert archive.read('payload/figure.svg').startswith(b'<?xml')
        assert str(source_path).encode() not in archive.read('payload/source-binding.json')
        assert all(hashlib.sha256(archive.read(member)).hexdigest() != source_hash
                   for member in archive.namelist())
        saved_geometry = archive.read('payload/workspace.json')
        assert all(field not in saved_geometry for field in (b'"p_value"', b'"q_value"', b'"intervals"', b'"raw_counts"'))
    assert hashes(value['parent_root']) == value['baseline']
    assert service._job(value['job']) == value['state']


def test_http_catalog_separates_data_from_test_only_software_ledger(episode_http, monkeypatch):
    from devbench.analysis import scientific_plot_verification as module
    value = episode_http
    ledger = value['root'] / 'TEST-ONLY-catalog-ledger.json'
    monkeypatch.setattr(module, 'LEDGER', ledger)

    def catalog(route):
        status, raw, _ = call(value['port'], route)
        assert status == 200, raw
        return json.loads(raw)

    global_catalog = catalog('/api/plot-capabilities')
    dataset_catalog = catalog(value['route'] + '/plot-capabilities')
    assert global_catalog['workspace_ref'] is None
    assert not global_catalog['scientific_validity_established']
    assert all(not row['eligibility']['eligible'] for row in global_catalog['capabilities'])
    assert dataset_catalog['verification_status'] == 'verification_ledger_absent'
    available = next(row for row in dataset_catalog['capabilities'] if row['data_availability']['available'])
    assert available['eligibility']['reason'] == 'software_verification_required'
    identity = dataset_catalog['implementation_sha256']
    record = {'capability': available['id'], 'scope': 'software_rendering', 'status': 'passed',
              'tested_workspace_sha256': dataset_catalog['workspace_ref']['sha256'],
              'source_kind': 'synthetic', 'implementation_sha256': identity,
              'checks': {key: 'passed' for key in
                         ('source_binding', 'axis_units', 'full_range', 'browser_rendering', 'exports')},
              'evidence_refs': [{'path': 'TEST-ONLY-not-production-evidence', 'sha256': 'b' * 64}]}
    # This injected record tests gating only; it is not rendering/browser evidence.
    fixture = {'format': 'devbench.plot-verification-ledger/1',
               'implementation_sha256': identity, 'records': [record]}
    ledger.write_text(json.dumps(fixture))
    verified = catalog(value['route'] + '/plot-capabilities')
    assert verified['verification_status'] == 'local_software_evidence_bound'
    assert {row['id'] for row in verified['capabilities'] if row['eligibility']['eligible']} == {available['id']}
    assert not verified['scientific_validity_established']
    assert all(not row['eligibility']['eligible'] for row in catalog('/api/plot-capabilities')['capabilities'])
    fixture['implementation_sha256'] = '0' * 64
    ledger.write_text(json.dumps(fixture))
    stale = catalog(value['route'] + '/plot-capabilities')
    assert stale['verification_status'] == 'implementation_changed_since_verification'
    assert all(not row['eligibility']['eligible'] for row in stale['capabilities'])


def test_http_refuses_analysis_path_injection_and_keeps_same_origin(episode_http):
    value = episode_http
    bundle_route = value['route'] + '/scientific-bundle'
    request = {'panel_id': value['workspace']['panels'][0]['id'], 'scope': 'complete'}
    for route in ('/api/plot-capabilities', value['route'] + '/scientific-plots',
                  value['route'] + '/plot-capabilities'):
        assert call(value['port'], route, origin='http://unrelated.invalid')[0] == 403
    for origin in (None, 'http://unrelated.invalid'):
        assert call(value['port'], bundle_route, body=request, origin=origin)[0] == 403
    for extra in ({'normalization': 'new'}, {'fit': {}}, {'bandwidth': .2},
                  {'source_path': '/unregistered/source.json'}, {'parameters': {'alpha': .05}}):
        status, raw, headers = call(value['port'], bundle_route, body={**request, **extra}, origin=value['origin'])
        assert status == 400, raw
        assert 'X-Plot-Revision-Root' not in headers
    request = {**request, 'reference_lines': [{'axis': 'x', 'value': 0, 'label': 'p-value 0.000001'}]}
    status, raw, headers = call(value['port'], bundle_route, body=request, origin=value['origin'])
    assert status == 403 and b'0.000001' not in raw and 'X-Plot-Revision-Root' not in headers
    assert call(value['port'], value['route'] + '/scientific-plots?source_path=/unregistered')[0] == 404
    assert hashes(value['parent_root']) == value['baseline']


def test_http_selected_project_cannot_read_foreign_or_absent_saved_results(episode_http):
    value = episode_http
    hub, port = value['hub'], value['project_port']
    source_project = value['service'].workspace_state()['project']['id']
    empty_project = value['empty']['id']
    status, raw, _ = call(port, value['route'] + '/scientific-plots', project=source_project)
    assert status == 200 and json.loads(raw) == value['workspace']
    assert call(port, '/api/local-analyst/' + '0' * 32 + '/scientific-plots', project=source_project)[0] == 404
    for route in (value['route'] + '/scientific-plots', value['route'] + '/plot-capabilities',
                  '/api/local-analyst/' + '0' * 32 + '/scientific-plots'):
        assert call(port, route, project=empty_project)[0] == 404
    request = {'panel_id': value['workspace']['panels'][0]['id'], 'scope': 'complete'}
    assert call(port, value['route'] + '/scientific-bundle', project=empty_project,
                body=request, origin='http://127.0.0.1:' + str(port))[0] == 404
    status, raw, _ = call(port, '/api/plot-capabilities', project=empty_project)
    assert status == 200
    assert all(not row['eligibility']['eligible'] for row in json.loads(raw)['capabilities'])
    assert hub.resolve(empty_project).jobs() == []
    assert hashes(value['parent_root']) == value['baseline']
