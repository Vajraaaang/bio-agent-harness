"""Offline Studio authorization and readback for Plugin graph steps."""

import json
import os
import sys
import time
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


@pytest.fixture
def exploratory_studio(workspace_tmp_path, monkeypatch):
    from devbench.platform.plugin_mcp import Plan, Plugin

    inputs = workspace_tmp_path / "inputs"
    inputs.mkdir(mode=0o700)
    source = inputs / "matrix.csv"
    source.write_text("gene_id,d1,d2\nprivate-fixture-sentinel,2,4\n")
    monkeypatch.setattr(Plugin, "_handoff", lambda *a, **kw: {})
    plugin = Plugin(workspace_tmp_path / "workspace", inputs, None)
    result = plugin.plan(
        Plan.model_validate(
            {
                "operation": "matrix_audit",
                "objective": "Inspect a local synthetic fixture",
                "inputs": [
                    {
                        "role": "expression",
                        "path": str(source),
                        "data_classification": "private",
                    }
                ],
            }
        )
    )
    session = result["session_id"]
    app = SimpleNamespace(
        offline_only=True, allow_data_execution=True, workspace=plugin.jobs.workspace
    )
    body = {
        "source_code": "from pathlib import Path\nimport json\n"
        "text = Path('/inputs/data').read_text()\n"
        "Path('/outputs/out.json').write_text(json.dumps([len(text)]))\n",
        "base_image_id": "devharness-explore-py",
        "step_name": "Summarize a local file",
        "parameters": {},
        "input_bindings": {"data": plugin.jobs.plan(session)["profiles"][0]["sha256"]},
        "expected_outputs": {
            "out": {
                "artifact_type": "json",
                "path": "/outputs/out.json",
                "required": True,
            }
        },
        "resource_profile": {
            "cpus": 1,
            "memory_mb": 512,
            "pids_limit": 64,
            "max_output_bytes": 100000,
            "timeout_sec": 30,
        },
    }
    return plugin, app, session, body


def test_private_script_freeze_and_review(exploratory_studio):
    from devbench.platform.plugin_exploratory_studio import (
        studio_freeze_exploration,
        studio_script_review,
    )

    plugin, app, session, body = exploratory_studio
    app.offline_only = False
    with pytest.raises(PermissionError):
        studio_freeze_exploration(app, session, body)
    app.offline_only = True
    with pytest.raises(OperationBlocked, match="unavailable"):
        studio_freeze_exploration(
            app, session, {**body, "input_bindings": {"data": "f" * 64}}
        )
    identity = studio_freeze_exploration(app, session, body)
    pid, sha = identity["plan_id"], identity["plan_sha256"]
    assert studio_script_review(app, session, pid, sha)["script"] == body["source_code"]
    import hashlib

    assert plugin.jobs.has_private_input(
        {hashlib.sha256(body["source_code"].encode()).hexdigest()}
    )
    assert plugin.jobs.authority.plan_approval(session, pid, sha) is None
    with pytest.raises(OperationBlocked, match="private_data_local_studio_only"):
        plugin._allow_external(session, pid)


def _await_exploration(jobs, session, run_id):
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        run = jobs.run(session, run_id)
        if run["status"] not in {"queued", "running", "finishing"}:
            assert run["status"] == "completed", run["error_text"]
            return json.loads(run["receipt_json"])
        time.sleep(0.15)
    pytest.fail("Registered exploratory worker did not finish")


@pytest.mark.skipif(
    os.environ.get("DEVBENCH_T15_DOCKER") != "1", reason="live Docker opt-in"
)
def test_offline_operator_inspects_verified_private_output(exploratory_studio):
    from devbench.daemon.supervisor import Supervisor
    from devbench.platform.plugin_exploratory_studio import (
        studio_freeze_exploration,
        studio_output_preview,
    )
    from devbench.platform.plugin_graph_studio import studio_launch
    from devbench.platform.plugin_jobs import studio_approval

    plugin, app, sid, body = exploratory_studio
    body = {
        **body,
        "source_code": "from pathlib import Path\n"
        "Path('/outputs/table.csv').write_text('feature,total\\nlocal-report,17\\n')",
        "expected_outputs": {
            "summary": {
                "artifact_type": "table",
                "required": True,
                "path": "/outputs/table.csv",
                "columns": ["feature", "total"],
            }
        },
    }
    frozen = studio_freeze_exploration(app, sid, body)
    identity = {k: frozen[k] for k in ("plan_id", "plan_sha256")}
    query = {**identity, "port": "summary"}
    with pytest.raises(OperationBlocked, match="verified_receipt_unavailable"):
        studio_output_preview(app, sid, query)
    supervisor = Supervisor(app.workspace, offline_only=True)
    supervisor.start()
    try:
        with studio_approval(
            sid,
            identity["plan_id"],
            identity["plan_sha256"],
            actor_id="fixture-operator",
            auth_method="session_cookie",
        ):
            run = studio_launch(app, sid, {**identity, "allow_data_execution": True})
        receipt = _await_exploration(plugin.jobs, sid, run["run_id"])
        preview = studio_output_preview(app, sid, query)
        assert (
            preview["sha256"]
            == receipt["registered_method_outputs"]["summary"]["sha256"]
        )
        assert preview["text"] == "feature,total\nlocal-report,17\n"
        with pytest.raises(OperationBlocked, match="private_output_unavailable"):
            studio_output_preview(app, sid, {**query, "port": "other"})
        with pytest.raises(OperationBlocked, match="plan_identity_mismatch"):
            studio_output_preview(app, sid, {**query, "plan_sha256": "f" * 64})
        with pytest.raises(OperationBlocked, match="private_data_local_studio_only"):
            plugin._allow_external(sid, identity["plan_id"])
        app.evidence_delivery = object()
        with pytest.raises(PermissionError):
            studio_output_preview(app, sid, query)
        app.evidence_delivery, app.offline_only = None, False
        with pytest.raises(PermissionError):
            studio_output_preview(app, sid, query)
    finally:
        supervisor.stop_if_idle()


@pytest.mark.skipif(
    os.environ.get("DEVBENCH_T15_DOCKER") != "1", reason="live Docker opt-in"
)
def test_local_and_rebound_template_execute_under_offline_approval(exploratory_studio):
    from devbench.daemon.supervisor import Supervisor
    from devbench.platform.plugin_exploratory_studio import (
        _bind_inputs,
        _local_method,
        studio_freeze_exploration,
        studio_script_review,
    )
    from devbench.platform.plugin_graph_studio import studio_launch, studio_view
    from devbench.platform.plugin_jobs import studio_approval
    from devbench.platform.plugin_mcp import Plan

    plugin, app, private, body = exploratory_studio
    jobs = plugin.jobs
    public_source = plugin.input_root / "public.csv"
    public_source.write_text("gene_id,p1,p2\npublic,2,3\n")
    public = plugin.plan(
        Plan.model_validate(
            {
                "operation": "matrix_audit",
                "objective": "Synthetic template test",
                "inputs": [
                    {
                        "path": str(public_source),
                        "role": "expression",
                        "data_classification": "synthetic",
                    }
                ],
            }
        )
    )["session_id"]
    seed = jobs.plan(public)
    method = _local_method(app.workspace, body, ("data",))
    versions = _bind_inputs(
        app.workspace, seed, method, {"data": seed["profiles"][0]["sha256"]}
    )
    frozen = plugin.plan(
        Plan.model_validate(
            {
                "operation": "registered_exploratory",
                "objective": "Test public fixture",
                "session_id": public,
                "method_version_id": method["version_id"],
                "step_name": "Synthetic fixture",
                "input_versions": versions,
                "parameters": {},
            }
        )
    )
    pin = {k: frozen[k] for k in ("plan_id", "plan_sha256")}
    handoff = {k: body[k] for k in ("step_name", "parameters", "input_bindings")} | {
        "template": {**pin, "studio_url": "http://127.0.0.1:8000/"}
    }
    with pytest.raises(OperationBlocked, match="tested_template_required"):
        studio_freeze_exploration(app, private, handoff)
    supervisor = Supervisor(app.workspace, offline_only=True)
    supervisor.start()
    try:
        jobs.approve(
            public,
            *pin.values(),
            actor_id="fixture-operator",
            surface="cli",
            auth_method="local_os_user",
        )
        public_run = jobs.launch(public, *pin.values())
        _await_exploration(jobs, public, public_run["run_id"])
        for draft in (handoff, body):
            local = studio_freeze_exploration(app, private, draft)
            pid, sha = local["plan_id"], local["plan_sha256"]
            review = studio_script_review(app, private, pid, sha)
            assert review["script"] == body["source_code"]
            with studio_approval(
                private,
                pid,
                sha,
                actor_id="fixture-operator",
                auth_method="session_cookie",
            ):
                run = studio_launch(
                    app,
                    private,
                    {"plan_id": pid, "plan_sha256": sha, "allow_data_execution": True},
                )
            receipt = _await_exploration(jobs, private, run["run_id"])
            output = receipt["registered_method_outputs"]["out"]
            assert (
                receipt["claim_tier"] == "L1_execution_verified"
                and output["data_classification"] == "private"
            )
            assert jobs.has_private_input({output["sha256"]})
            grant = jobs.authority.plan_approval(private, pid, sha)
            assert (
                json.loads(grant["rationale"])["script_sha256"]
                == review["script_sha256"]
            )
            assert studio_view(app, private)["plans"][-1]["status"] == "completed"
            with pytest.raises(
                OperationBlocked, match="private_data_local_studio_only"
            ):
                plugin._allow_external(private, pid)
    finally:
        supervisor.stop_if_idle()


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


@pytest.mark.parametrize("before_delivery", [False, True])
def test_fresh_adaptive_poll_distinguishes_graph_revision_conflict(
    workspace_tmp_path, monkeypatch, before_delivery
):
    from devbench.platform import workbench_adaptive as studio

    app, *_ = setup_workspace(workspace_tmp_path)
    route = f"/api/scientific/adaptive/{_SID}"
    race = (
        OperationBlocked("adaptive_projection_changed_before_delivery")
        if before_delivery
        else studio._StudioReadbackChanged("A worker committed during readback")
    )
    outcomes = iter([race, race, race, None, race, OperationConflict("Other conflict")])
    cursors = []

    def project(owner, session_id, *, cursor):
        assert owner is app and session_id == _SID
        cursors.append(cursor)
        failure = next(outcomes)
        if failure is not None:
            raise failure
        return {"session_id": _SID}

    monkeypatch.setattr(studio, "_adaptive_view_once", project)
    try:
        with running(app) as port:
            status, body, _ = call(port, route)
            assert status == 409
            assert json.loads(body)["message"] == "adaptive_view_revision_changed"
            assert call(port, route)[0] == 200
            for path in (route + "?cursor=bound-cursor", route):
                status, body, _ = call(port, path)
                assert status == 409
                assert json.loads(body)["message"] == "operation_unavailable"
        assert cursors == [None, None, None, None, "bound-cursor", None]
    finally:
        app.close()


@pytest.mark.parametrize(
    ("status", "pid", "orphan", "expected"),
    [
        ("queued", None, True, "queued"),
        ("running", 123, False, "running"),
        ("running", 123, True, "interrupted"),
    ],
)
def test_daemon_run_view_uses_supervisor_liveness(
    tmp_path, monkeypatch, status, pid, orphan, expected
):
    from devbench.platform import plugin_graph_studio as studio

    calls = []

    def check(workspace, worker_pid, created):
        calls.append((workspace, worker_pid, created))
        return orphan

    monkeypatch.setattr(studio, "_orphaned_worker", check)
    run = {
        "session_id": _SID,
        "plan_id": _ID,
        "plan_sha256": _SHA,
        "run_id": _ATTEMPT,
        "status": status,
        "pid": pid,
        "error_text": None,
        "stage_json": json.dumps({"daemon_managed": True, "process_created_at": 17}),
    }
    view = studio._run_view(tmp_path, run, _SHA, run)
    assert view["status"] == expected
    assert view.get("error") == (
        "worker_not_running" if expected == "interrupted" else None
    )
    assert calls == ([] if status == "queued" else [(tmp_path, pid, 17)])
