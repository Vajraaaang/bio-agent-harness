"""Controller ownership of presentation children, without a second executor."""
import json
from pathlib import Path
import shutil
import tempfile
import threading
import time
import uuid

import pytest

from tests.test_figure_revisions import figure_parent


@pytest.fixture
def figure_service(figure_parent):
    from devbench.analysis.workbench_ui import WorkbenchService
    base = Path(__file__).resolve().parents[1] / "reports/rna-agent-readiness/2026-09-27-m2a.l9eya67z/fixtures"
    base.mkdir(exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="presentation-service-", dir=base))
    inputs = root / "inputs"
    inputs.mkdir()
    app = WorkbenchService(root / "service", [inputs], run_timeout_seconds=120)
    parent, parent_root = figure_parent
    job = uuid.uuid4().hex
    destination = app.workspace / "jobs" / job
    destination.mkdir()
    shutil.copytree(parent, destination / "bundle")
    app._jobs[job] = {"id": job, "question": "Saved-table controller fixture", "status": "completed_review_failed",
                      "completed_analysis": True, "model_sessions_attempted": 0,
                      "execution": {"bundle_root": parent_root}, "scientific_review": "not_performed_by_this_service"}
    app._update(job)
    try:
        yield app, job, parent_root
    finally:
        app.close()


def wait(app, job):
    from devbench.analysis.workbench_ui import ACTIVE
    end = time.monotonic() + 120
    while time.monotonic() < end:
        state = app.status(job)
        if state["status"] not in ACTIVE:
            return state
        time.sleep(.02)
    pytest.fail("Presentation child did not reach a terminal state")


def request(parent_root):
    return {"figure_id": "qc", "expected_parent_root": parent_root,
            "display_options": {"point_size": 12}, "author_label": "Fixture operator"}


def test_service_exposes_presentation_boundary():
    from devbench.analysis.workbench_ui import WorkbenchService
    for name in ("figures", "revise_figure", "figure_revision", "download_figure_revision"):
        assert hasattr(WorkbenchService, name), f"Missing presentation boundary: {name}"


def test_revision_duplicate_reuses_owned_child_and_preserves_parent(figure_service):
    app, parent, parent_root = figure_service
    before = app.status(parent)
    catalog = app.figures(parent)
    assert catalog["parent_root"] == parent_root
    assert len(catalog["catalog"]) == 22
    response = app.revise_figure(parent, request(parent_root))
    assert app.revise_figure(parent, request(parent_root))["id"] == response["id"]
    child = wait(app, response["id"])
    assert child["status"] == "completed_presentation", child.get("error")
    assert child["operation_kind"] == "presentation" and not child["completed_analysis"]
    assert child["model_sessions_attempted"] == 0
    assert app.status(parent) == before
    assert not child["resumable"]
    assert app.figure_revision(parent, response["id"])["id"] == response["id"]
    archive, content_type, _ = app.download_figure_revision(parent, response["id"], "archive")
    assert archive.is_file() and content_type == "application/zip"


def test_queued_cancel_is_retained_without_render_or_parent_change(figure_service, monkeypatch):
    from devbench.analysis import figure_revisions
    app, parent, parent_root = figure_service
    before = app.status(parent)
    release = threading.Event()
    app._executor.submit(release.wait)
    calls = []
    def forbidden(*args, **kwargs):
        calls.append(True)
        raise AssertionError("Cancelled child must not render")
    monkeypatch.setattr(figure_revisions, "execute_figure_revision", forbidden)
    try:
        response = app.revise_figure(parent, request(parent_root))
        app.cancel(response["id"])
        release.set()
        state = wait(app, response["id"])
        assert state["status"] == "cancelled" and not state["resumable"]
        assert app.revise_figure(parent, request(parent_root))["id"] == response["id"]
        assert not calls and app.status(parent) == before
        with pytest.raises(RuntimeError):
            app.download_figure_revision(parent, response["id"], "archive")
    finally:
        release.set()


def test_stale_or_unknown_request_fields_do_not_create_jobs(figure_service):
    app, parent, parent_root = figure_service
    before = app.jobs()
    for invalid in ({**request(parent_root), "expected_parent_root": "0" * 64},
                    {**request(parent_root), "output": "/tmp/escape"},
                    {**request(parent_root), "display_options": {"counts_layer": "normalized"}}):
        with pytest.raises(ValueError):
            app.revise_figure(parent, invalid)
    assert app.jobs() == before


def test_renderer_failure_stays_failed_without_success_download(figure_service, monkeypatch):
    from devbench.analysis import figure_revisions
    app, parent, parent_root = figure_service
    def fail(*args, **kwargs):
        raise ValueError("Deliberate renderer fixture failure")
    monkeypatch.setattr(figure_revisions, "execute_figure_revision", fail)
    child = app.revise_figure(parent, request(parent_root))["id"]
    state = wait(app, child)
    assert state["status"] == "operation_failed" and "fixture failure" in state["error"]["message"]
    assert app.status(parent)["status"] == "completed_review_failed"
    assert app.revise_figure(parent, request(parent_root))["id"] == child
    with pytest.raises(RuntimeError):
        app.download_figure_revision(parent, child, "archive")


def paused_presentation_worker(root_string):
    """Controlled cancellation window; never performs a scientific computation."""
    (Path(root_string) / ".worker-started").write_text("ready")
    time.sleep(60)
    raise AssertionError("Cancelled renderer should not reach completion")


def test_active_presentation_cancel_observes_stopped_worker(figure_service, monkeypatch):
    from devbench.analysis import figure_revisions
    app, parent, parent_root = figure_service
    before = app.status(parent)
    monkeypatch.setattr(figure_revisions, "_render_worker", paused_presentation_worker)
    child = app.revise_figure(parent, request(parent_root))["id"]
    bundle = app.workspace / "jobs" / child / "bundle"
    end = time.monotonic() + 20
    while not (bundle / ".worker-started").exists() and time.monotonic() < end:
        assert app.status(child)["status"] in {"queued", "running"}, app.status(child)
        time.sleep(.02)
    assert (bundle / ".worker-started").exists()
    app.cancel(child)
    state = wait(app, child)
    assert state["status"] == "cancelled" and state["worker_liveness"] == "stopped"
    assert state["worker_control"]["workers"]
    assert all(worker["cleanup"]["verified_stopped"] for worker in state["worker_control"]["workers"])
    assert json.loads((bundle / "revision.json").read_text())["status"] == "cancelled"
    assert not (bundle / "receipts.json").exists()
    assert app.status(parent) == before
    with pytest.raises(RuntimeError):
        app.download_figure_revision(parent, child, "archive")
