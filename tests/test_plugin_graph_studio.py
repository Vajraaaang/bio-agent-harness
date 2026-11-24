"""Offline Studio authorization and readback for Plugin graph steps."""

import json
import sys
from types import SimpleNamespace

import pytest

from devbench.engine.workflow_operations import OperationBlocked, OperationConflict
from devbench.platform.plugin_jobs import JobStore, canonical
from tests.test_scientific_workspace import setup_workspace
from tests.test_scientific_workspace_http import call, running

pytestmark = pytest.mark.wal

_SID = "a" * 32
_ID = "b" * 32
_SHA = "c" * 64
_ATTEMPT = "d" * 32


def test_plugin_execute_route_requires_offline_execution(
    workspace_tmp_path, monkeypatch
):
    app, *_ = setup_workspace(workspace_tmp_path)
    route = f"/api/scientific/adaptive/{_SID}/plugin-execute"
    body = {"plan_id": _ID, "plan_sha256": _SHA, "allow_data_execution": True}
    seen = []

    def launch(owner, session, request):
        seen.append((owner, session, request))
        return {"status": "queued"}

    monkeypatch.setitem(
        sys.modules,
        "devbench.platform.plugin_graph_studio",
        SimpleNamespace(studio_launch=launch),
    )
    try:
        with running(app) as port:
            origin = f"http://127.0.0.1:{port}"
            assert call(port, route, body=body, origin=origin)[0] == 403
            app.offline_only = True
            app.allow_data_execution = False
            assert call(port, route, body=body, origin=origin)[0] == 403
            app.allow_data_execution = True
            assert call(port, route, body=body)[0] == 403
            assert call(port, route + "?allow=true", body=body, origin=origin)[0] == 404
            assert seen == []
            status, result, _ = call(port, route, body=body, origin=origin)
            assert status == 202 and json.loads(result) == {"status": "queued"}
            assert seen == [(app, _SID, body)]
    finally:
        app.close()


def test_only_verified_offline_graph_display_can_serve_graph_artifacts(
    workspace_tmp_path, monkeypatch
):
    from devbench.platform import workbench_ui

    app, *_ = setup_workspace(workspace_tmp_path)
    png = b"verified graph image"
    coordinates = b"verified graph coordinates"
    route = f"/api/scientific/adaptive/{_SID}/motor-umap"
    hashes = frozenset({_SHA})
    verified = {
        "umap": (png, "image/png", None),
        "coordinates": (coordinates, "text/csv", None),
    }

    def graph_artifact(owner, session_id, kind, identity):
        assert owner is app and session_id == _SID and identity == _SHA
        return verified.get(kind)

    monkeypatch.setattr(workbench_ui, "_publication_hashes", lambda _app: hashes)
    monkeypatch.setitem(
        sys.modules,
        "devbench.platform.plugin_motor_umap",
        SimpleNamespace(
            studio_graph_artifact=graph_artifact,
            studio_artifact=lambda *_args: (png, "image/png", None),
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "devbench.platform.workbench_plotly",
        SimpleNamespace(
            motor_umap_figure=lambda values: {"verified": values},
            render_plotly_frame=lambda figure, theme: (
                figure["verified"] + b" " + theme.encode(),
                "test-nonce",
            ),
        ),
    )
    try:
        with running(app) as port:
            image = f"{route}/umap/{_SHA}"
            plot = f"{route}/{_SHA}/plotly?theme=dark"
            assert call(port, image)[0] == 403
            assert call(port, plot)[0] == 403
            app.offline_only = True
            assert call(port, image) == (200, png, "image/png")
            assert call(port, plot) == (
                200,
                coordinates + b" dark",
                "text/html; charset=utf-8",
            )
            verified.clear()
            assert call(port, image)[0] == 403
            assert call(port, plot)[0] == 403
    finally:
        app.close()


def test_local_graph_presentation_respects_foreign_ledger_embargo(
    workspace_tmp_path, monkeypatch
):
    from devbench.analysis import scientific_publication
    from devbench.engine import partition_authority
    from devbench.engine.workflow_operations import LEDGER_NAME, WorkflowOperationLedger
    from devbench.platform.workbench_http import _require_local_graph_source

    foreign = workspace_tmp_path / "foreign"
    foreign.mkdir(mode=0o700)
    ledger = WorkflowOperationLedger(foreign / LEDGER_NAME, scientific=True)
    app = SimpleNamespace(workspace=workspace_tmp_path)
    monkeypatch.setattr(
        scientific_publication,
        "_visible_publication_ledgers",
        lambda _owner: (ledger.path,),
    )
    monkeypatch.setattr(
        partition_authority,
        "embargoed",
        lambda _db, identity: identity == _SHA,
    )
    with pytest.raises(PermissionError, match="publication_source_withheld"):
        _require_local_graph_source(app, _SHA)


@pytest.fixture
def planned(workspace_tmp_path, monkeypatch):
    from devbench.platform import plugin_graph_studio as studio

    app = SimpleNamespace(
        workspace=workspace_tmp_path,
        offline_only=True,
        allow_data_execution=True,
    )
    jobs = JobStore(app.workspace)
    runtime = {"code_sha256": _SHA}
    plan = {
        "format": "devbench.plugin-plan/1",
        "plan_id": _ID,
        "session_id": _SID,
        "operation": "motor_program_comparison",
        "data_classification": "private",
        "graph_runtime": runtime,
    }
    _, checksum = jobs.freeze(plan)
    monkeypatch.setattr(studio, "_check_scope", lambda *args: None)
    monkeypatch.setitem(
        sys.modules,
        "devbench.platform.plugin_graph",
        graph := SimpleNamespace(
            registered_plugin_runtime=lambda value: runtime,
            require_plugin_dependencies=lambda *args: None,
            verify_registered_plugin_result=lambda *args: None,
        ),
    )
    return app, jobs, plan, checksum, graph


def test_exact_consent_and_dependency_before_job_launch(planned, monkeypatch):
    from devbench.platform.plugin_graph_studio import studio_launch

    app, _, _, checksum, graph = planned
    request = {"plan_id": _ID, "plan_sha256": checksum, "allow_data_execution": True}
    calls = []

    def launch(_jobs, sid, plan_id, sha):
        calls.append((sid, plan_id, sha))
        return {
            "run_id": _ATTEMPT,
            "session_id": sid,
            "plan_id": plan_id,
            "plan_sha256": sha,
            "status": "queued",
            "stage_json": "{}",
            "pid": None,
            "receipt_json": None,
            "error_text": None,
        }

    def wait_for_dependency(*args):
        raise OperationBlocked("plugin_dependency_not_complete")

    monkeypatch.setattr(JobStore, "launch", launch)
    with pytest.raises(PermissionError):
        studio_launch(app, _SID, {**request, "allow_data_execution": 1})
    with pytest.raises(PermissionError):
        studio_launch(app, _SID, {**request, "extra": True})
    with pytest.raises(OperationBlocked, match="plan_identity_mismatch"):
        studio_launch(app, _SID, {**request, "plan_sha256": "0" * 64})
    monkeypatch.setattr(graph, "require_plugin_dependencies", wait_for_dependency)
    with pytest.raises(OperationBlocked, match="plugin_dependency_not_complete"):
        studio_launch(app, _SID, request)
    assert calls == []
    monkeypatch.setattr(graph, "require_plugin_dependencies", lambda *args: None)
    assert studio_launch(app, _SID, request)["run_id"] == _ATTEMPT
    assert calls == [(_SID, _ID, checksum)]


def test_readback_only_emits_current_verified_private_result(planned, monkeypatch):
    from devbench.platform.plugin_graph_studio import studio_view

    app, jobs, plan, checksum, graph = planned
    assert studio_view(app, _SID)["plans"][0]["can_run"] is True
    jobs.freeze({**plan, "plan_id": "e" * 32, "operation": "unknown_private"})
    result = {
        "metrics": {"profiles": 8},
        "summary_tables": [{"caption": "test", "columns": ["profiles"], "rows": [[8]]}],
        "graph_execution": {"receipt_sha256": _SHA},
    }
    stored = {**result, "limitations": ["unverified text"]}
    with jobs._write() as db:
        db.execute(
            "INSERT INTO plugin_runs VALUES (?, ?, ?, ?, 'completed', NULL, '{}', "
            "?, NULL, 0, 1, 1)",
            (_ATTEMPT, _SID, _ID, checksum, canonical(stored).decode()),
        )
    seen = []

    def verify(*args):
        seen.append(args)
        return result

    monkeypatch.setattr(graph, "verify_registered_plugin_result", verify)
    view = studio_view(app, _SID)["plans"]
    assert [row["plan_id"] for row in view] == [_ID]
    assert seen == [(app.workspace, _ATTEMPT, plan, stored)]
    assert view[0]["status"] == "completed" and view[0]["can_run"] is False
    assert view[0]["summary_tables"] == result["summary_tables"]
    assert "limitations" not in view[0]

    def unverifiable(*args):
        raise OperationBlocked("adaptive_receipt_no_longer_current")

    monkeypatch.setattr(graph, "verify_registered_plugin_result", unverifiable)
    row = studio_view(app, _SID)["plans"][0]
    assert (
        row["status"] == "unavailable"
        and row["error"] == "verified_receipt_unavailable"
    )
    assert not ({"metrics", "summary_tables", "graph_execution"} & row.keys())
    app.offline_only = False
    assert studio_view(app, _SID) is None


def test_registered_local_solver_uses_same_offline_readback(planned):
    from devbench.platform.plugin_graph_studio import studio_view

    app, jobs, plan, _, _ = planned
    local_id = "e" * 32
    jobs.freeze(
        {
            **plan,
            "plan_id": local_id,
            "operation": "bulk_deconvolution",
            "design": {"parameters": {"source": "/private/data.tsv", "iterations": 15}},
        }
    )
    local = next(
        row for row in studio_view(app, _SID)["plans"] if row["plan_id"] == local_id
    )
    assert local["can_run"] is True and local["parameters"] == {"iterations": 15}
    app.offline_only = False
    assert studio_view(app, _SID) is None


def test_adaptive_poll_retries_only_fresh_view_revision_races(monkeypatch):
    from devbench.platform import workbench_adaptive as studio

    error_type = getattr(studio, "_StudioReadbackChanged", OperationConflict)
    attempts = []

    def project(app, session_id, *, cursor):
        attempts.append(cursor)
        if len(attempts) == 1:
            raise error_type("the worker committed during Studio readback")
        return {"session_id": session_id}

    monkeypatch.setattr(studio, "_adaptive_view_once", project, raising=False)
    assert studio.adaptive_view(object(), _SID) == {"session_id": _SID}
    assert attempts == [None, None]
    attempts.clear()
    with pytest.raises(OperationConflict, match="committed"):
        studio.adaptive_view(object(), _SID, cursor="bound-cursor")
    assert attempts == ["bound-cursor"]

    def refuse(*args, **kwargs):
        attempts.append(None)
        raise OperationConflict("another conflict")

    monkeypatch.setattr(studio, "_adaptive_view_once", refuse)
    attempts.clear()
    with pytest.raises(OperationConflict, match="another conflict"):
        studio.adaptive_view(object(), _SID)
    assert attempts == [None]


def test_adaptive_poll_retries_revision_change_before_disclosure(monkeypatch):
    from devbench.platform import workbench_adaptive as studio

    attempts = []

    def project(app, session_id, *, cursor):
        attempts.append(cursor)
        if len(attempts) == 1:
            raise OperationBlocked("adaptive_projection_changed_before_delivery")
        return {"session_id": session_id}

    monkeypatch.setattr(studio, "_adaptive_view_once", project)
    assert studio.adaptive_view(object(), _SID) == {"session_id": _SID}
    assert attempts == [None, None]
    attempts.clear()
    with pytest.raises(OperationBlocked, match="adaptive_projection_changed"):
        studio.adaptive_view(object(), _SID, cursor="bound-cursor")
    assert attempts == ["bound-cursor"]
