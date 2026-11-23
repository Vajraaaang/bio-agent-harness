"""Actual local bulk episode -> read-only plots, without a provider call."""
import hashlib
import json

from tests.test_analysis_episodes import workspace as workspace  # noqa: F401
from tests.test_workbench_local_analyst import prepared, app, grant, wait
from tests.test_episode_agent import ScriptedSelector, sequence


def test_saved_episode_exposes_applicable_plots_without_mutating_run(workspace, monkeypatch):  # noqa: F811
    from devbench.analysis.bulk_plot_service import episode_bulk_workspace, bulk_plot_pdf
    from devbench.analysis.episodes import verify_episode
    spec, _ = prepared(workspace)
    service = app(workspace, spec)
    monkeypatch.setattr(service.local_analyst, "_selector", lambda episode: ScriptedSelector(sequence()))
    try:
        identity = service.local_analyst.run(grant(service.local_analyst))["id"]
        state = wait(service, identity)
        assert state["status"] == "completed"
        before = service.local_analyst.status(identity)
        data = episode_bulk_workspace(service.local_analyst, identity)
        assert data["coverage"]["gene_count"] == 12
        assert data["coverage"]["sample_count"] == 8
        for kind in ("ma", "volcano", "dispersion", "pca", "expression", "pvalue_histogram"):
            assert data["availability"][kind]["status"] == "available", (kind, data["availability"][kind])
        assert data["pairing"]["status"] == "unpaired"
        assert data["availability"]["paired_expression"]["status"] == "unavailable"
        path, _, _ = service.local_analyst.download(identity, "result")
        assert data["source"]["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
        pdf = bulk_plot_pdf(data, {"kind": "ma", "scope": "complete"})
        assert pdf.startswith(b"%PDF-")
        assert service.local_analyst.status(identity) == before
        assert verify_episode(service._path("jobs", identity, "episode"), state["episode_receipt"]["root_hash"])["valid"]
    finally:
        service.close()


def test_bulk_http_routes_keep_origin_and_export_scope(workspace, monkeypatch):  # noqa: F811
    import threading
    import urllib.error
    import urllib.request
    import pytest
    from devbench.analysis.workbench_ui import make_server
    spec, _ = prepared(workspace)
    service = app(workspace, spec)
    monkeypatch.setattr(service.local_analyst, "_selector", lambda episode: ScriptedSelector(sequence()))
    server = make_server(service, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = 'http://127.0.0.1:' + str(server.server_address[1])
    try:
        identity = service.local_analyst.run(grant(service.local_analyst))["id"]
        assert wait(service, identity)["status"] == "completed"
        url = base + '/api/local-analyst/' + identity + '/bulk-plots'
        headers = {}
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers)) as response:
            data = json.load(response)
        assert data['source']['run_id'] == identity and len(data['genes']) == 12
        assert all(row['p_value'] is None and row['q_value'] is None and row['raw_counts'] is None
                   for row in data['genes'])
        assert data['availability']['volcano']['status'] == 'unavailable'
        headers.update({'Content-Type': 'application/json', 'Origin': base})
        request = urllib.request.Request(base + '/api/local-analyst/' + identity + '/bulk-pdf',
            data=json.dumps({'kind': 'volcano', 'scope': 'complete'}).encode(), headers=headers)
        with pytest.raises(urllib.error.HTTPError) as withheld:
            urllib.request.urlopen(request)
        assert withheld.value.code == 403
        request = urllib.request.Request(base + '/api/local-analyst/' + identity + '/bulk-pdf',
            data=json.dumps({'kind': 'ma', 'scope': 'complete'}).encode(), headers=headers)
        with urllib.request.urlopen(request) as response:
            assert response.headers['Content-Type'] == 'application/pdf'
            assert response.read().startswith(b'%PDF-')
        headers['Origin'] = 'http://unrelated.invalid'
        request = urllib.request.Request(base + '/api/local-analyst/' + identity + '/bulk-pdf',
            data=b'{"kind":"ma"}', headers=headers)
        with pytest.raises(urllib.error.HTTPError) as denied:
            urllib.request.urlopen(request)
        assert denied.value.code == 403
    finally:
        server.shutdown()
        server.server_close()
        service.close()
