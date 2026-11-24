"""Offline Studio custody and projection for a local analysis report."""

import hashlib
import json

import pytest

from tests.test_scientific_workspace import setup_workspace
from tests.test_scientific_workspace_http import call, running
from tests.test_workbench_studio_thesis import _run


@pytest.mark.wal
def test_attachment_is_bound_to_current_private_run(workspace_tmp_path, monkeypatch):
    from devbench.platform import workbench_local_analysis
    from devbench.platform.plugin_jobs import digest
    from devbench.platform.workbench_local_analysis import local_analysis_view, retain_local_analysis

    app, *_ = setup_workspace(workspace_tmp_path)
    session_id, run_id = "a" * 32, "b" * 32
    inputs = [item * 64 for item in "cde"]
    plan = {"profiles": [{"sha256": value} for value in inputs]}
    receipt = {"session_id": session_id, "run_id": run_id}
    record = (plan, "f" * 64, {"status": "completed"})
    monkeypatch.setattr(workbench_local_analysis, "_studio_record",
                        lambda app, sid: record if sid == session_id else None)
    monkeypatch.setattr(workbench_local_analysis, "_studio_receipt", lambda app, *record: receipt)
    image = b"\x89PNG\r\n\x1a\nlocal analysis"
    image_sha = hashlib.sha256(image).hexdigest()
    bundle = workspace_tmp_path / "report"
    bundle.mkdir()
    (bundle / "composition.png").write_bytes(image)
    manifest = {
        "format": "devbench.local-analysis/1",
        "session_id": session_id,
        "source_receipt_sha256": digest(receipt),
        "input_sha256": inputs,
        "title": "Developmental analysis",
        "summary": "Maturation programs differed across the two intervals.",
        "findings": [{"title": "State mixture", "text": "Both components changed."}],
        "tables": [{"caption": "Composition", "columns": ["Stage", "Cells"],
                    "rows": [["e09", "42"]]}],
        "figures": [{"title": "Composition", "filename": "composition.png", "sha256": image_sha}],
    }
    path = bundle / "analysis.json"
    path.write_text(json.dumps(manifest))
    route = f"/api/scientific/adaptive/{session_id}/local-analysis/{image_sha}.png"
    try:
        assert local_analysis_view(app, session_id) is None
        with pytest.raises(PermissionError):
            retain_local_analysis(app, path)
        app.offline_only = True
        retain_local_analysis(app, path)
        retain_local_analysis(app, path)
        (bundle / "composition.png").unlink()
        assert local_analysis_view(app, "1" * 32) is None
        view = local_analysis_view(app, session_id)
        assert view["title"] == manifest["title"]
        assert view["figures"] == [{"title": "Composition", "sha256": image_sha}]
        assert str(bundle) not in json.dumps(view)
        with running(app) as port:
            assert call(port, route) == (200, image, "image/png")
            assert call(port, route, origin="https://example.invalid")[0] == 403
            assert call(port, route.replace(session_id, "1" * 32))[0] == 404
            receipt["run_id"] = "2" * 32
            assert local_analysis_view(app, session_id) is None
            assert call(port, route)[0] == 404
    finally:
        app.close()


def test_motor_overview_leads_with_session_analysis():
    _run(r"""
const model = {
  session_id: sessionId,
  shared_motor_umap: {status:'completed', cells:42, stages:[{stage:'e09'}]},
  local_analysis: {
    title:'Developmental analysis across e09–e11', summary:'Between-stage maturation.',
    findings:[{title:'State mixture',text:'Both components changed.'}],
    tables:[{caption:'Descriptive effects',columns:['Program','Change'],rows:[['Synaptic','+0.16']]}],
    figures:[],
  },
};
WorkbenchStudio.renderSession(container,'overview',model,{});
assert.ok(textIn(container).includes('Developmental analysis across e09–e11'));
assert.ok(textIn(container).includes('Between-stage maturation.'));
assert.ok(textIn(container).includes('Shared motor-neuron UMAP'));
WorkbenchStudio.renderSession(container,'findings',model,{});
assert.ok(textIn(container).includes('State mixture'));
assert.ok(textIn(container).includes('Descriptive effects'));
assert.ok(textIn(container).includes('Motor-neuron composition by stage'));
""")

