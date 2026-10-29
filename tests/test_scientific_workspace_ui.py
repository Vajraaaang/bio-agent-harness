"""Unified local UI routes retain scientific and authenticated read boundaries."""
import json
import pytest

from tests.test_scientific_workspace import tmp_path, setup_workspace
from tests.test_scientific_workspace_http import running, call


def test_unified_discovery_and_thread_routes_keep_existing_scientific_reads_inert(tmp_path):
    app, methods, _, _, _ = setup_workspace(tmp_path)
    try:
        before = methods.database.read_bytes()
        with running(app) as port:
            for route in ["/api/inputs", "/api/investigations", "/api/scientific", "/api/jobs"]:
                assert call(port, route)[0] == 403
                status, _, _ = call(port, route, token=app.token)
                assert status == 200
            status, body, _ = call(port, "/api/inputs", token=app.token)
            catalog = json.loads(body)
            assert status == 200 and isinstance(catalog["inputs"], list)
            assert json.loads(call(port, "/api/investigations", token=app.token)[1])["investigations"] == []
            assert methods.database.read_bytes() == before
            assert app.jobs() == [] and methods.list_records("run") == []
    finally:
        app.close()


def test_inspection_requires_same_origin_and_rejects_unregistered_sources(tmp_path):
    app, methods, _, _, _ = setup_workspace(tmp_path)
    try:
        with running(app) as port:
            request = {"source": "/unregistered/source.h5ad"}
            assert call(port, "/api/inputs/inspect", token=app.token, body=request)[0] == 403
            assert call(port, "/api/inputs/inspect", token=app.token, body=request,
                        origin="https://foreign.invalid")[0] == 403
            origin = "http://127.0.0.1:" + str(port)
            assert call(port, "/api/inputs/inspect", token=app.token, body=request, origin=origin)[0] == 400
            assert call(port, "/api/inputs/inspect", token=app.token,
                        body={"source": "/unregistered/source.h5ad", "allow_model_execution": True}, origin=origin)[0] == 400
            assert app.jobs() == [] and methods.list_records("run") == []
    finally:
        app.close()


@pytest.mark.parametrize("filename,contents", [("matrix.json", '{"counts":[[1,2],[3,4]]}'),
                                              ("samples.csv", "sample,batch\na,A\nb,B\n")])
def test_actual_unified_route_admits_non_h5ad_without_widening_legacy_route(tmp_path, filename, contents):
    app, methods, _, _, inputs = setup_workspace(tmp_path)
    source = inputs / filename
    source.write_text(contents)
    try:
        with pytest.raises(ValueError, match="H5AD"):
            app._input(str(source))
        with running(app) as port:
            origin = "http://127.0.0.1:" + str(port)
            status, body, _ = call(port, "/api/investigations", token=app.token,
                body={"question": "Which methods suit these measurements?", "source": str(source)}, origin=origin)
            value = json.loads(body)
            assert status == 202, value
            assert value["status"] == "awaiting_information" and value["job_id"] is None
            assert value["profile"]["observed"]["file_type"] == source.suffix[1:]
            assert len(value["profile"]["sha256"]) == 64
            assert app.jobs() == [] and methods.list_records("run") == []
    finally:
        app.close()


def test_real_unified_question_clarification_approval_and_synthesis(tmp_path):
    """Fresh real numeric software journey; run after all source writers freeze."""
    from devbench.analysis.workbench_ui import WorkbenchService, CONFIRMATIONS
    from devbench.analysis.checkpoints import sha256
    from tests.test_workbench_proposals import fixture
    from tests.test_workbench_ui import wait
    inputs = tmp_path / "unified-inputs"
    inputs.mkdir()
    source, parameters, declarations = fixture(inputs)
    overrides = {key: value for key, value in parameters.items() if key not in {
        "data_sha256", "workflow", "prompt", "execution_tier", "parent_root", "model_path", "model_sha256", "integration_decision"}}
    app = WorkbenchService(tmp_path / "unified-service", [inputs], parameter_overrides=overrides,
                           allow_data_execution=True, run_timeout_seconds=180)
    original = sha256(source)
    try:
        record = app.investigations.create({"question": "Inspect cell quality and batch structure", "source": str(source)})
        assert record["status"] == "awaiting_information" and record["job_id"] is None
        assert record["profile"]["sha256"] == original
        assert record["profile"]["observed"]["cells"] == 80
        assert record["profile"]["observed"]["genes"] == 60
        record = app.investigations.action(record["id"], {"action": "clarify", "expected_revision": record["revision"],
                                                        "declarations": declarations})
        assert wait(app, record["job_id"])["status"] == "awaiting_operator_review"
        record = app.investigations.get(record["id"])
        record = app.investigations.action(record["id"], {"action": "review", "expected_revision": record["revision"],
            "reviewer": "Synthetic integration verifier", "decision": "approve", "rationale": "Software verification only",
            "confirmations": {key: True for key in CONFIRMATIONS}})
        assert wait(app, record["job_id"])["status"] == "approved"
        record = app.investigations.get(record["id"])
        record = app.investigations.action(record["id"], {"action": "run", "expected_revision": record["revision"],
                                                        "allow_data_execution": True})
        assert wait(app, record["job_id"])["status"] == "completed_review_passed"
        final = app.investigations.get(record["id"])
        assert final["synthesis"]["supported_findings"]["metrics"]["cells_input"] == 80
        assert final["synthesis"]["uncertainties"]
        assert final["artifacts"] and final["job_state"]["model_sessions_attempted"] == 0
        assert sha256(source) == original
    finally:
        app.close()
