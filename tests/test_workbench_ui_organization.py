"""Local organization is durable metadata, never execution or scientific evidence."""
import json
import threading
import urllib.error
import urllib.request

import pytest

from tests.test_workbench_ui import root, service


def add_record(app, job="a" * 32, **fields):
    app._path("jobs", job).mkdir()
    app._jobs[job] = {"id": job, "question": "Recorded question", "status": "approved", **fields}
    app._update(job)
    return job


def test_project_and_ui_state_survive_restart_without_changing_run_records(root, monkeypatch):
    app = service(root)
    job = add_record(app)
    before = app._path("jobs", job, "state.json").read_bytes()
    original = app.workspace_state()
    assert original["project"]["scope"] == "single_user_local"
    monkeypatch.setattr(app, "_submit", lambda *_: pytest.fail("Metadata must not launch work"))
    saved = app.workspace_state({"expected_revision": original["revision"], "name": "My research",
        "ui": {"selected_job": job, "view": "review", "question_draft": "Unsubmitted question",
               "search": "batch", "tab": "review", "zoom": "native"}})
    assert saved["revision"] == original["revision"] + 1
    assert app._path("jobs", job, "state.json").read_bytes() == before
    assert app.token not in app._path("workspace.json").read_text()
    app.close()
    reopened = service(root)
    try:
        assert reopened.workspace_state() == saved
        assert reopened.workspace_state()["project"]["id"] == original["project"]["id"]
        assert reopened.status(job)["status"] == "approved"
    finally:
        reopened.close()


@pytest.mark.parametrize("changes", [
    {"ui": {"token": "secret"}}, {"token": "secret"}, {"ui": {"view": "arbitrary"}},
    {"ui": {"zoom": "arbitrary"}}, {"ui": {"question_draft": "x" * 8001}},
    {"ui": {"search": "x" * 501}}, {"ui": {"selected_job": "../outside"}},
    {"ui": {"selected_revision": "../outside"}}, {"ui": {"figure": []}},
    {"ui": {"inspector_open": "false"}}, {"name": " "}, {"name": "x" * 121},
    {"expected_revision": True}, {"ui": {"parent_root": "wrong"}},
])
def test_workspace_state_rejects_unknown_secret_unbounded_and_invalid_fields(root, changes):
    app = service(root)
    try:
        before = app.workspace_state()
        with pytest.raises(ValueError):
            app.workspace_state({"expected_revision": before["revision"], **changes})
        assert app.workspace_state() == before
    finally:
        app.close()


def test_workspace_state_conflict_does_not_overwrite_newer_draft(root):
    app = service(root)
    try:
        initial = app.workspace_state()
        saved = app.workspace_state({"expected_revision": initial["revision"], "ui": {"question_draft": "First window"}})
        with pytest.raises(RuntimeError, match="another window"):
            app.workspace_state({"expected_revision": initial["revision"], "ui": {"question_draft": "Stale window"}})
        assert app.workspace_state() == saved
    finally:
        app.close()


def test_navigation_uses_reconciled_states_and_distinguishes_render_attempts(root):
    app = service(root)
    try:
        parent = add_record(app, status="completed_review_failed", cache_worker_liveness="unknown",
            proposal={"external_input": {"path": "/recorded/cells.h5ad", "sha256": "b" * 64, "size_bytes": 100}},
            execution_history=[{"attempt": 1, "status": "completed_review_failed", "elapsed_sec": 2.5}])
        child = add_record(app, "c" * 32, status="completed_presentation", operation_kind="presentation",
            parent_job=parent, figure_id="qc", operation_pending=True, execution_history=[{"attempt": 1, "elapsed_sec": .5}])
        summaries = {row["id"]: row for row in app.jobs()}
        assert summaries[parent]["status"] == "recovery_required"
        assert summaries[parent]["input"]["sha256"] == "b" * 64
        assert summaries[parent]["operation_kind"] == "analysis"
        assert summaries[parent]["execution_history"][0]["attempt"] == 1
        assert summaries[child]["status"] == "finalizing"
        assert summaries[child]["operation_kind"] == "presentation"
        assert summaries[child]["parent_job"] == parent
        assert summaries[child]["figure_id"] == "qc"
        assert app.status(parent)["resumable"] is False
        # A harmless draft remains writable while computation is blocked.
        initial = app.workspace_state()
        app.workspace_state({"expected_revision": initial["revision"], "ui": {"question_draft": "Investigate interruption"}})
        with pytest.raises(RuntimeError, match="liveness"):
            app._require_known_workers()
    finally:
        app.close()


def test_workspace_metadata_is_not_followed_through_a_symlink(root):
    app = service(root)
    app.workspace_state()
    app.close()
    outside = root / "preserve.json"
    outside.write_text('{"preserve":true}')
    metadata = root / "workspace/workspace.json"
    metadata.unlink()
    metadata.symlink_to(outside)
    with pytest.raises(ValueError):
        service(root)
    assert json.loads(outside.read_text()) == {"preserve": True}


def test_workspace_api_keeps_auth_origin_and_revision_boundaries(root):
    from devbench.analysis.workbench_ui import make_server
    app = service(root)
    server = make_server(app, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    origin = "http://127.0.0.1:" + str(server.server_port)
    def call(body=None, token=None, request_origin=None):
        headers = {"Content-Type": "application/json"}
        if token is not None:
            headers["X-Workbench-Token"] = token
        if request_origin is not None:
            headers["Origin"] = request_origin
        request = urllib.request.Request(origin + "/api/workspace",
            data=json.dumps(body).encode() if body is not None else None, headers=headers)
        with urllib.request.urlopen(request, timeout=3) as response:
            return json.load(response)
    try:
        with pytest.raises(urllib.error.HTTPError) as error:
            call()
        assert error.value.code == 403
        before = call(token=app.token)
        for wrong_origin in (None, "https://untrusted.invalid"):
            with pytest.raises(urllib.error.HTTPError) as error:
                call({"expected_revision": before["revision"], "ui": {"question_draft": "Denied"}}, app.token, wrong_origin)
            assert error.value.code == 403
        saved = call({"expected_revision": before["revision"], "ui": {"question_draft": "Retained"}}, app.token, origin)
        assert saved["ui"]["question_draft"] == "Retained"
        with pytest.raises(urllib.error.HTTPError) as error:
            call({"expected_revision": before["revision"], "ui": {}}, app.token, origin)
        assert error.value.code == 409
        assert call(token=app.token) == saved
        assert app.jobs() == []
    finally:
        server.shutdown()
        server.server_close()
        thread.join(3)
        app.close()
