"""Loopback scientific routes keep the existing browser boundary."""

import http.client
import json
import threading
from contextlib import contextmanager

import pytest

from devbench.analysis.workbench_ui import make_server
from tests.test_scientific_workspace import setup_workspace


@pytest.fixture
def tmp_path(workspace_tmp_path):
    return workspace_tmp_path


@contextmanager
def running(app):
    server = make_server(app, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address[1]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def call(port, path, *, body=None, origin=None, timeout=10):
    headers = {}
    if origin is not None:
        headers["Origin"] = origin
    encoded = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        encoded = json.dumps(body)
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    try:
        connection.request(
            "POST" if body is not None else "GET", path, body=encoded, headers=headers
        )
        response = connection.getresponse()
        return response.status, response.read(), response.getheader("Content-Type")
    finally:
        connection.close()


@pytest.mark.wal
def test_workflow_page_link_keeps_loopback_and_frame_boundary(tmp_path):
    app, *_ = setup_workspace(tmp_path)
    sid = "a" * 32
    try:
        with running(app) as port:
            connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
            try:
                connection.request("GET", f"/?session_id={sid}&view=qc")
                response = connection.getresponse()
                assert response.status == 200
                assert response.getheader("X-Frame-Options") == "DENY"
                assert b'data-workflow-band' in response.read()
            finally:
                connection.close()
            for query in (
                "session_id=invalid&view=qc",
                f"session_id={sid}&view=admin",
                f"session_id={sid}&view=qc&view=evidence",
                f"session_id={sid}&view=qc&unexpected=1",
            ):
                assert call(port, "/?" + query)[0] == 400
            assert call(
                port, f"/?session_id={sid}&view=qc", origin="https://example.invalid",
            )[0] == 403
    finally:
        app.close()


@pytest.mark.wal
def test_empty_studio_catalog_does_not_create_plugin_storage(tmp_path):
    app, *_ = setup_workspace(tmp_path)
    try:
        with running(app) as port:
            status, body, _ = call(port, "/api/scientific/sessions")
            assert status == 200 and json.loads(body) == {"sessions": []}
            assert not (app.workspace / "plugin-jobs.sqlite3").exists()
    finally:
        app.close()


@pytest.mark.wal
def test_retained_comparison_figure_uses_the_scoped_studio_route(tmp_path, monkeypatch):
    import hashlib
    import sys
    from types import SimpleNamespace

    app, *_ = setup_workspace(tmp_path)
    app.offline_only = True
    session_id = "a" * 32
    figure = b"verified figure bytes"
    checksum = hashlib.sha256(figure).hexdigest()
    calls = []

    def read(owner, sid, sha256):
        calls.append((owner, sid, sha256))
        return figure

    monkeypatch.setitem(sys.modules, "devbench.platform.workbench_retained_comparison",
                        SimpleNamespace(local_comparison_figure=read))
    route = f"/api/scientific/adaptive/{session_id}/comparison/{checksum}.png"
    try:
        with running(app) as port:
            assert call(port, route, origin="https://example.invalid")[0] == 403
            assert call(port, route + "?other=1")[0] == 404
            assert not calls
            status, content, media = call(port, route)
            assert status == 200
            assert (content, media) == (figure, "image/png")
            assert calls == [(app, session_id, checksum)]
    finally:
        app.close()


def _fixture_adaptive_controller(
    planner, ledger, lineage, broker, *,
    step_id="bounded-qc", thesis_lifecycle=None,
):
    from devbench.analysis.checkpoints import digest
    from devbench.analysis.episode_agent import (
        AdaptiveController,
        AgentLimits,
        CampaignSelectionInvoker,
    )
    from devbench.analysis.evidence_access import AccessPolicy
    from tests.test_rna_investigation_budget import _policy
    from tests.test_rna_investigation_parent_integration import CapturedFixtureBackend
    from tests.test_two_agent_investigation_v24 import ScenarioSelector, decision

    selector = ScenarioSelector([decision("dispatch", step_id=step_id)])
    backend = CapturedFixtureBackend(planner.methods, selector)
    plan_sha256 = digest(planner.get(lineage))
    call_limit = {
        "tokens": 20,
        "calls": 2,
        "seconds": 10,
        "context_bytes": 65536,
        "memory_mb": 512,
    }
    policy = _policy(
        max_total_tokens=2000,
        max_calls=120,
        max_total_seconds=900,
        reconciliation={
            "tokens": 340,
            "calls": 34,
            "seconds": 170,
            "context_bytes": 65536,
        },
    )
    ledger.open_campaign(
        "workbench-campaign",
        session_id=lineage,
        policy=policy,
        capability=backend.capabilities(),
        identity={
            "expected_plan_hash": plan_sha256,
            "source_digest": backend.capability["source_digest"],
            "runtime_digest": backend.capability["runtime_digest"],
            "parent_call_limits": {"default": call_limit},
            "protected_parent_allocations": {
                "scientific_review": call_limit,
                "adaptive_termination": call_limit,
            },
        },
    )
    planner.attach_model_campaign(
        lineage,
        ledger,
        "workbench-campaign",
        backend,
        expected_plan_hash=plan_sha256,
        actor="tester",
    )
    access = AccessPolicy(
        "workbench-controller", 1, allow_outcomes=True, allow_statistics=True
    )
    broker.register_policy(access)
    broker.register_context(
        "controller",
        principal_id="controller",
        lineage_id=lineage,
        role="controller",
        phase="analysis",
        policy_sha256=access.sha256(),
    )
    invoker = CampaignSelectionInvoker(
        planner, lineage, limits=AgentLimits(max_context_bytes=65536, memory_mb=512)
    )
    return AdaptiveController(
        invoker,
        broker=broker,
        context_id="controller",
        action_catalog={},
        selector=selector,
        challenger=selector,
        thesis_lifecycle=thesis_lifecycle,
    ), selector


def test_scientific_routes_preserve_origin_revision_and_source_read_boundaries(
    tmp_path,
):
    app, methods, _, step, _ = setup_workspace(tmp_path)
    try:
        with running(app) as port:
            before = methods.database.read_bytes()
            assert call(port, "/api/scientific")[0] == 200
            assert methods.database.read_bytes() == before
            request = {"action": "initialize", "expected_revision": 0}
            assert call(port, "/api/scientific", body=request)[0] == 403
            assert (
                call(
                    port,
                    "/api/scientific",
                    body=request,
                    origin="https://foreign.invalid",
                )[0]
                == 403
            )
            origin = "http://127.0.0.1:" + str(port)
            assert call(port, "/api/scientific", body=request, origin=origin)[0] == 200
            revision = app.scientific.catalog()["revision"]
            create = {
                "action": "create_study",
                "name": "HTTP study",
                "aim": "Verify local boundaries",
                "expected_revision": revision,
            }
            assert call(port, "/api/scientific", body=create, origin=origin)[0] == 200
            assert call(port, "/api/scientific", body=create, origin=origin)[0] == 409
            before = methods.database.read_bytes()
            status, data, media = call(
                port, "/api/scientific/methods/" + step["method_version_id"]
            )
            preview = json.loads(data)
            assert status == 200 and media.startswith("application/json")
            assert preview["execution_started"] is False
            assert "source_text" not in preview
            assert preview["source_status"] == "restricted_audit_entitlement_required"
            assert len(preview["source_sha256"]) == 64
            assert methods.database.read_bytes() == before
            assert methods.list_records("run") == []
    finally:
        app.close()


def test_exact_version_link_never_follows_latest(tmp_path):
    app, _, _, _, _ = setup_workspace(tmp_path)
    try:
        with running(app) as port:
            reference = (
                "object_type=dataset&object_id=" + "f" * 32 + "&version_id=" + "a" * 64
            )
            assert (
                call(port, "/?" + reference)[0] == 200
            )  # Shell only, no scientific content.
            status, data, _ = call(port, "/api/scientific/resolve?" + reference)
            assert status == 404 and "unavailable" in json.loads(data)["message"]
            assert call(port, "/api/jobs")[0] == 200
    finally:
        app.close()


@pytest.mark.wal
@pytest.mark.parametrize("use_controller", [False, True])
def test_adaptive_workbench_routes_use_current_graph_and_exact_startup_scope(
    tmp_path, use_controller
):
    from devbench.analysis.evidence_access import AccessPolicy, EvidenceBroker
    from devbench.engine.workflow_operations import LEDGER_NAME, WorkflowOperationLedger
    from tests.test_investigation_invalidation import Worker, digest
    from tests.test_investigation_invalidation import step as adaptive_step
    from tests.test_investigation_publication_epochs import authorize
    from tests.test_scientific_authority_migration import _binding
    from tests.test_scientific_publication_policy import register_adaptive_table
    from tests.test_scientific_workspace import action, scientific_context

    app, methods, _, method_step, inputs = setup_workspace(tmp_path)
    if use_controller:
        method_selector = app.scientific.selector
        app.close()
        from devbench.analysis.workbench_ui import WorkbenchService

        actors = tmp_path / "actors"
        actors.mkdir(mode=0o700)
        app = WorkbenchService(
            tmp_path / "adaptive-browser",
            [inputs],
            enable_scientific=True,
            scientific_workspace=methods.root,
            allow_data_execution=True,
            codex_model="fixture-only-model",
            method_selector=method_selector,
            agent_workspaces=actors,
            allow_model_execution=True,
            model_data_scope="synthetic",
        )
    try:
        _, dataset, analysis = scientific_context(app, method_step)
        plan = action(
            app, "create_plan", dataset_ref=dataset["ref"], analysis_ref=analysis["ref"]
        )
        lineage = plan["session_id"]
        path = "/api/scientific/adaptive/" + lineage
        with running(app) as port:
            origin = "http://127.0.0.1:" + str(port)
            status, body, _ = call(port, path)
            assert status == 200 and json.loads(body)["mode"] == "serial"
            ledger = WorkflowOperationLedger(
                methods.root / LEDGER_NAME, scientific=True
            )
            authority = ledger.scientific
            authority.commit(lineage, expected_revision=0, record=_binding())
            authority.commit(
                lineage,
                expected_revision=1,
                record={
                    "kind": "grant",
                    "record_id": "grant-a",
                    "binding_id": "binding-a",
                    "grant_sha256": digest("engineering grant"),
                },
            )
            authority.activate_execution(
                lineage,
                expected_revision=2,
                binding_id="binding-a",
                grant_id="grant-a",
                max_jobs=5,
                authorize=authorize,
            )
            flow = app.scientific.planner().adaptive_workflow(lineage, ledger)
            flow.insert(adaptive_step("bounded-qc"))
            status, body, _ = call(port, path)
            assert (
                status == 200
                and json.loads(body)["graph_status"] == "current_delivery_required"
            )
            assert call(port, path + "/replay")[0] == 404

            from devbench.platform.workbench_adaptive import WorkbenchAdaptiveBinding

            broker = EvidenceBroker(authority)
            policy = AccessPolicy("workbench-readback", 1, allow_outcomes=True)
            broker.register_policy(policy)
            broker.register_context(
                "viewer",
                principal_id="scientist",
                lineage_id=lineage,
                role="scientist",
                phase="readback",
                policy_sha256=policy.sha256(),
            )
            broker.register_source(
                "graph",
                content_sha256=digest("graph source"),
                roots=(digest("root"),),
                classification="structural",
            )
            worker = Worker()
            controller, selector = (
                _fixture_adaptive_controller(
                    app.scientific.planner(), ledger, lineage, broker
                )
                if use_controller
                else (None, None)
            )
            registry, table, publication_delivery = register_adaptive_table(
                tmp_path / "retained-publication"
            )
            replay = registry.export_replay(
                table.subject_id, delivery=publication_delivery, audience="public"
            )
            package = app.workspace / "retained-replay.zip"
            package.write_bytes(replay["bytes"])
            app.bind_adaptive_workflow(
                WorkbenchAdaptiveBinding(
                    lineage,
                    broker.delivery("viewer", "graph"),
                    executor=worker,
                    controller=controller,
                    replay_package=package,
                    replay_root_sha256=replay["root_sha256"],
                )
            )
            status, body, _ = call(port, path)
            view = json.loads(body)
            assert status == 200 and view["mode"] == "adaptive/1"
            assert view["graph"]["entries"][0]["step_id"] == "bounded-qc"
            assert view["graph"]["entries"][0]["status"] == "ready"
            assert view["qualification"] == "not_established"
            assert view["actions"]["advance"] is True
            assert worker.calls == []

            status, body, _ = call(port, "/api/scientific/sessions")
            assert status == 200
            studio = next(
                row for row in json.loads(body)["sessions"]
                if row["session_id"] == lineage
            )
            assert studio["origin"] == "studio" and studio["step_count"] == 1

            status, body, _ = call(port, path)
            assert status == 200 and json.loads(body)["revision"] == view["revision"]

            request = {
                "action": "advance",
                "expected_revision": view["revision"],
                "allow_data_execution": True,
                "allow_model_execution": use_controller,
            }
            assert call(port, path, body=request)[0] == 403
            assert (
                call(
                    port,
                    path,
                    body={**request, "allow_data_execution": False},
                    origin=origin,
                )[0]
                == 403
            )
            assert (
                call(
                    port,
                    path,
                    body={**request, "expected_revision": view["revision"] - 1},
                    origin=origin,
                )[0]
                == 409
            )
            if use_controller:
                assert (
                    call(
                        port,
                        path,
                        body={**request, "allow_model_execution": False},
                        origin=origin,
                    )[0]
                    == 403
                )
                assert selector.controller_turns == 0
            assert worker.calls == []
            status, body, _ = call(port, path, body=request, origin=origin)
            assert status == 200 and json.loads(body)["steps_dispatched"] == 1, body
            assert worker.calls == ["bounded-qc"]
            if use_controller:
                assert selector.controller_turns == 1
            assert call(port, path, body=request, origin=origin)[0] == 409
            status, body, _ = call(port, path)
            assert (
                status == 200
                and json.loads(body)["graph"]["entries"][0]["status"] == "succeeded"
            )
            assert b"private" not in body and b"staged_outputs" not in body
            cursor = json.loads(body)["revision"]
            status, body, _ = call(
                port,
                path,
                body={"action": "cancel", "expected_revision": cursor},
                origin=origin,
            )
            cancelled = json.loads(body)
            assert status == 200 and cancelled["status"] == "cancel_requested"
            assert cancelled["cleanup_status"] == "unverified"
            assert (
                call(
                    port,
                    path,
                    body={**request, "expected_revision": cancelled["revision"]},
                    origin=origin,
                )[0]
                == 409
            )
            assert worker.calls == ["bounded-qc"]
            status, body, _ = call(port, path + "/replay")
            receipt = json.loads(body)
            assert (
                status == 200
                and receipt["verified"] is True
                and receipt["current_use"] == "unavailable"
            )
            assert receipt["model_sessions"] == receipt["numerical_stage_launches"] == 0
            assert (
                "publication" not in receipt and package.read_bytes() == replay["bytes"]
            )
    finally:
        app.close()


@pytest.mark.wal
@pytest.mark.parametrize("read_kind", ["recognition", "revision"])
def test_standalone_workbench_adaptive_reads_retry_wal_busy(
    tmp_path, monkeypatch, read_kind
):
    import sqlite3
    from types import SimpleNamespace

    from devbench.engine import workflow_operations
    from devbench.engine.workflow_operations import LEDGER_NAME, WorkflowOperationLedger
    from devbench.platform.adaptive_workflow import AdaptiveWorkflow
    from devbench.platform.workbench_adaptive import _adaptive_ledger, _graph_revision
    from tests.test_investigation_invalidation import step
    from tests.test_scientific_authority_migration import _binding

    ledger = WorkflowOperationLedger(tmp_path / LEDGER_NAME, scientific=True)
    lineage = "workbench-test"
    ledger.scientific.commit(lineage, expected_revision=0, record=_binding())
    flow = AdaptiveWorkflow(ledger, lineage)
    flow.insert(step("retained-qc"))
    scientific = SimpleNamespace(root=tmp_path)

    def read():
        if read_kind == "recognition":
            return _adaptive_ledger(scientific, lineage).path
        return _graph_revision(flow)

    expected = read()
    original_database = WorkflowOperationLedger._database
    # Recognition checks the read-only handle's schema before its graph query.
    failing_connection = 2 if read_kind == "recognition" else 1
    connections = 0

    @contextmanager
    def transient_database(self, *, read_transaction=False):
        nonlocal connections
        with original_database(self, read_transaction=read_transaction) as db:
            connections += 1
            if connections == failing_connection:
                error = sqlite3.OperationalError("database is locked")
                error.sqlite_errorcode = sqlite3.SQLITE_BUSY
                raise error
            yield db

    waits = []
    with monkeypatch.context() as scope:
        scope.setattr(WorkflowOperationLedger, "_database", transient_database)
        scope.setattr(workflow_operations.time, "sleep", waits.append)
        assert read() == expected
    assert len(waits) == 1

    if read_kind == "revision":
        with ledger._database() as db:
            db.execute("BEGIN IMMEDIATE")
            flow.insert(step("uncommitted-qc"), connection=db)
            assert _graph_revision(flow, connection=db) == expected + 1
            db.rollback()
        assert _graph_revision(flow) == expected


@pytest.mark.wal
def test_local_startup_attaches_the_exact_retained_controller(tmp_path, monkeypatch):
    from devbench.analysis.evidence_access import AccessPolicy, EvidenceBroker
    from devbench.analysis.rna_diagnostic_policy import DiagnosticQualificationAuthority
    from devbench.analysis.scientific_review_holds import ReviewHoldLifecycle
    from devbench.engine.exploratory_oci import ExploratoryOCI
    from devbench.engine.workflow_operations import LEDGER_NAME, WorkflowOperationLedger
    from devbench.platform.workbench_adaptive import local_startup_binding
    from tests.test_investigation_invalidation import digest, step
    from tests.test_investigation_publication_epochs import authorize
    from tests.test_scientific_authority_migration import _binding
    from tests.test_scientific_workspace import action, scientific_context

    app, methods, _, method_step, _ = setup_workspace(tmp_path)
    try:
        _, dataset, analysis = scientific_context(app, method_step)
        plan = action(
            app, "create_plan", dataset_ref=dataset["ref"], analysis_ref=analysis["ref"]
        )
        lineage = plan["session_id"]
        planner = app.scientific.planner()
        ledger = WorkflowOperationLedger(methods.root / LEDGER_NAME, scientific=True)
        authority = ledger.scientific
        authority.commit(lineage, expected_revision=0, record=_binding())
        authority.commit(
            lineage,
            expected_revision=1,
            record={
                "kind": "grant",
                "record_id": "grant-a",
                "binding_id": "binding-a",
                "grant_sha256": digest("controller grant"),
            },
        )
        authority.activate_execution(
            lineage,
            expected_revision=2,
            binding_id="binding-a",
            grant_id="grant-a",
            max_jobs=1,
            authorize=authorize,
        )
        flow = planner.adaptive_workflow(lineage, ledger)
        flow.insert(step("retained-qc"))
        startup = app.workspace / "adaptive-startup.json"
        startup.write_text(
            json.dumps(
                {
                    "format": "devbench.workbench-adaptive-startup/1",
                    "session_id": lineage,
                    "context_id": "viewer",
                    "source_id": "graph",
                    "parameters": [],
                }
            )
        )
        with pytest.raises(PermissionError, match="existing disclosure authority"):
            local_startup_binding(app, startup)

        broker = EvidenceBroker(authority)
        policy = AccessPolicy("startup-readback", 1, allow_outcomes=True)
        broker.register_policy(policy)
        broker.register_context(
            "viewer",
            principal_id="scientist",
            lineage_id=lineage,
            role="scientist",
            phase="readback",
            policy_sha256=policy.sha256(),
        )
        broker.register_source(
            "graph",
            content_sha256=digest("graph"),
            roots=(digest("root"),),
            classification="structural",
        )
        manual = local_startup_binding(app, startup)
        assert manual.manual_local_oci and type(manual.executor) is ExploratoryOCI
        assert manual.controller is None

        controller, selector = _fixture_adaptive_controller(
            planner, ledger, lineage, broker
        )
        retained = controller.bind(flow)
        with pytest.raises(PermissionError, match="bound controller"):
            local_startup_binding(app, startup)

        binding = local_startup_binding(app, startup, controller=controller)
        assert binding.controller is controller
        assert binding.manual_local_oci is False
        assert type(binding.executor) is ExploratoryOCI
        assert binding.executor.ledger.path == ledger.path
        assert binding.delivery.context_id == "viewer"
        assert binding.delivery.source_id == "graph"
        mounted_config = json.loads(startup.read_text())
        startup.write_text(
            json.dumps(
                {
                    **mounted_config,
                    "format": "devbench.workbench-adaptive-startup/2",
                    "mounted_image_id": "sha256:" + "1" * 64,
                }
            )
        )
        with pytest.raises(PermissionError, match="attached controller"):
            local_startup_binding(app, startup)
        with pytest.raises(PermissionError, match="review and diagnostic"):
            local_startup_binding(app, startup, controller=controller)
        lifecycle = ReviewHoldLifecycle(authority, lineage)
        diagnostics = DiagnosticQualificationAuthority(lambda *_: None)
        mounted = local_startup_binding(
            app,
            startup,
            controller=controller,
            review_lifecycle=lifecycle,
            review_binding=lambda _: _binding(),
            diagnostic_qualification=diagnostics,
        )
        assert mounted.tier_a_executor.mounted_image_id == "sha256:" + "1" * 64
        assert mounted.tier_a_executor.ledger is ledger
        assert mounted.review_lifecycle is lifecycle
        assert mounted.diagnostic_qualification is diagnostics
        startup.write_text(json.dumps(mounted_config))
        with monkeypatch.context() as scope:
            scope.setattr(app, "allow_data_execution", False)
            with pytest.raises(PermissionError, match="data execution permission"):
                local_startup_binding(app, startup, controller=controller)
            with pytest.raises(PermissionError, match="data execution permission"):
                binding.require_owner(app.scientific)
        app.bind_adaptive_workflow(binding)
        assert flow.controller_state() == retained
        assert selector.controller_turns == 0
        with pytest.raises(PermissionError, match="bound controller"):
            manual.require_owner(app.scientific)
    finally:
        app.close()


def test_workbench_release_view_is_offline_and_held(tmp_path):
    app, _, _, _, _ = setup_workspace(tmp_path)
    try:
        with running(app) as port:
            path = "/api/scientific/release-evaluation"
            status, body, _ = call(port, path)
            assert status == 200
            value = json.loads(body)
            assert value["admission"] == "blocked"
            assert value["gates"] == dict.fromkeys(
                ("R0", "R1", "X8", "X9", "clinical_release"), "blocked"
            )
            assert value["model_calls"] == value["numerical_jobs"] == 0
            assert "protocol" not in value and "ledger" not in value
            status, body, _ = call(port, "/api/scientific/diagnostic-policies")
            assert status == 200
            assert {row["family"] for row in json.loads(body)["policies"]} == {
                "Q-DESIGN",
                "Q-INFLUENCE",
                "Q-QL",
                "Q-REFERENCE",
                "Q-GRAPH",
            }
            assert all(
                row["qualification"] == "unqualified"
                for row in json.loads(body)["policies"]
            )
    finally:
        app.close()


@pytest.mark.wal
def test_workbench_cli_attaches_current_retained_disclosure_without_execution_authority(
    tmp_path,
):
    import os
    import re
    import select
    import signal
    import subprocess
    import sys
    from pathlib import Path

    from devbench.analysis.evidence_access import AccessPolicy, EvidenceBroker
    from devbench.engine.workflow_operations import LEDGER_NAME, WorkflowOperationLedger
    from tests.test_investigation_invalidation import digest, step
    from tests.test_scientific_authority_migration import _binding
    from tests.test_scientific_workspace import action, scientific_context

    app, methods, _, method_step, inputs = setup_workspace(tmp_path)
    try:
        _, dataset, analysis = scientific_context(app, method_step)
        plan = action(
            app, "create_plan", dataset_ref=dataset["ref"], analysis_ref=analysis["ref"]
        )
        lineage = plan["session_id"]
        ledger = WorkflowOperationLedger(methods.root / LEDGER_NAME, scientific=True)
        authority = ledger.scientific
        authority.commit(lineage, expected_revision=0, record=_binding())
        flow = app.scientific.planner().adaptive_workflow(lineage, ledger)
        flow.insert(step("retained-qc"))
        broker = EvidenceBroker(authority)
        policy = AccessPolicy("cli-readback", 1, allow_outcomes=True)
        broker.register_policy(policy)
        broker.register_context(
            "viewer",
            principal_id="scientist",
            lineage_id=lineage,
            role="scientist",
            phase="readback",
            policy_sha256=policy.sha256(),
        )
        broker.register_source(
            "graph",
            content_sha256=digest("retained graph source"),
            roots=(digest("retained root"),),
            classification="structural",
        )
        startup = app.workspace / "adaptive-startup.json"
        startup.write_text(
            json.dumps(
                {
                    "format": "devbench.workbench-adaptive-startup/1",
                    "session_id": lineage,
                    "context_id": "viewer",
                    "source_id": "graph",
                }
            )
        )
    finally:
        app.close()

    command = [
        sys.executable,
        "-P",
        "-m",
        "devbench.analysis.workbench_ui",
        "--workspace",
        str(app.workspace),
        "--input-root",
        str(inputs),
        "--enable-scientific",
        "--scientific-workspace",
        str(methods.root),
        "--adaptive-workflow",
        str(startup),
        "--offline-only",
        "--port",
        "0",
    ]
    with subprocess.Popen(
        command,
        cwd=Path(__file__).resolve().parents[1],
        env={**os.environ, "DEVBENCH_RUNS_ROOT": str(tmp_path / ".runs")},
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    ) as process:
        try:
            ready, _, _ = select.select([process.stdout], [], [], 45)
            startup_line = process.stdout.readline(200) if ready else b""
            match = re.fullmatch(rb"Open http://127\.0\.0\.1:(\d+)/\n", startup_line)
            if match is None:
                if process.poll() is not None:
                    pytest.fail(process.stderr.read(2000).decode(errors="replace"))
                pytest.fail("Workbench did not start its loopback service")
            port = int(match[1])
            liveness = app.workspace / "studio-server.json"
            address = json.loads(liveness.read_text())
            assert address["url"] == f"http://127.0.0.1:{port}/"
            assert address["pid"] == process.pid and address["offline_only"] is True
            assert liveness.stat().st_mode & 0o777 == 0o600
            path = "/api/scientific/adaptive/" + lineage
            status, data, _ = call(port, path)
            graph = json.loads(data)
            assert status == 200 and graph["graph_status"] == "available"
            assert graph["graph"]["entries"][0]["step_id"] == "retained-qc"
            assert graph["actions"] == {
                "advance": False,
                "resume": False,
                "cancel": False,
                "model_required": False,
                "model_available": False,
                "run_checked_step": None,
                "run_operator_steps": [],
            }
            request = {
                "action": "advance",
                "expected_revision": graph["revision"],
                "allow_data_execution": True,
            }
            assert (
                call(port, path, body=request, origin="http://127.0.0.1:" + str(port))[
                    0
                ]
                == 403
            )
            broker.register_policy(
                AccessPolicy("cli-readback", 2, allow_outcomes=False)
            )
            assert call(port, path)[0] == 403
        finally:
            if process.poll() is None:
                process.send_signal(signal.SIGTERM)
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
    assert not (app.workspace / "studio-server.json").exists()


@pytest.mark.wal
def test_workbench_dispatches_an_owner_bound_reviewed_trajectory(workspace_tmp_path):
    import os

    from devbench.analysis.evidence_access import AccessPolicy, EvidenceBroker
    from devbench.analysis.workbench_ui import WorkbenchService
    from devbench.core.rna_answerability import (
        AnswerabilityRequest,
        AttestedMeasurement,
        DesignEvidence,
        RuntimeEvidence,
    )
    from devbench.engine.tier_a_executor import TierAExecutor
    from devbench.engine.workflow_operations import (
        LEDGER_NAME,
        OperationBlocked,
        OperationConflict,
        WorkflowOperationLedger,
    )
    from devbench.platform.adaptive_workflow import ArtifactVersion
    from devbench.platform.thesis_aims import ThesisAimLifecycle
    from devbench.platform.thesis_workflow import ThesisWorkflow
    from devbench.platform.workbench_adaptive import (
        WorkbenchAdaptiveBinding,
        _graph_revision,
    )
    from devbench.platform.workbench_thesis import bind_workbench_donor_trajectory
    from devharness_controlled.native_contracts import reviewed_trajectory_kernel_images
    from tests.test_investigation_invalidation import digest
    from tests.test_investigation_publication_epochs import authorize
    from tests.test_scientific_authority_migration import _binding
    from tests.test_scientific_workspace import action, scientific_context
    from tests.test_thesis_aims import _aim, _approve
    from tests.test_tier_a_donor_trajectory import (
        _REPRESENTATIONS,
        _candidate_evidence,
        _published_trajectory,
        _sources,
    )

    image = os.environ.get("DEVBENCH_RNA_KERNEL_TEST_IMAGE", "")
    if image not in reviewed_trajectory_kernel_images():
        pytest.skip("Select a pinned reviewed trajectory image")
    app, methods, _, method_step, inputs = setup_workspace(workspace_tmp_path)
    method_selector = app.scientific.selector
    app.close()
    actors = workspace_tmp_path / "actors"
    actors.mkdir(mode=0o700)
    app = WorkbenchService(
        workspace_tmp_path / "trajectory-browser",
        [inputs],
        enable_scientific=True,
        scientific_workspace=methods.root,
        allow_data_execution=True,
        codex_model="fixture-only-model",
        method_selector=method_selector,
        agent_workspaces=actors,
        allow_model_execution=True,
        model_data_scope="synthetic",
    )
    try:
        _, dataset, analysis = scientific_context(app, method_step)
        plan = action(
            app,
            "create_plan",
            dataset_ref=dataset["ref"],
            analysis_ref=analysis["ref"],
        )
        lineage = plan["session_id"]
        ledger = WorkflowOperationLedger(methods.root / LEDGER_NAME, scientific=True)
        authority = ledger.scientific
        authority.commit(lineage, expected_revision=0, record=_binding())
        authority.commit(
            lineage,
            expected_revision=1,
            record={
                "kind": "grant",
                "record_id": "grant-a",
                "binding_id": "binding-a",
                "grant_sha256": digest("trajectory engineering grant"),
            },
        )
        authority.activate_execution(
            lineage,
            expected_revision=2,
            binding_id="binding-a",
            grant_id="grant-a",
            max_jobs=5,
            authorize=authorize,
        )
        flow = app.scientific.planner().adaptive_workflow(lineage, ledger)
        executor = TierAExecutor(
            ledger, allow_data_execution=True, mounted_image_id=image
        )
        source, _ = _sources(
            workspace_tmp_path / "trajectory-data", {"intent": "confirmatory"},
        )
        reviewed = _candidate_evidence(source)
        links, pins = {}, {}
        for port, (kind, representation) in _REPRESENTATIONS.items():
            item = ArtifactVersion(
                version_id=port,
                sha256=executor.training.put_file(source[port]),
                kind=kind,
                representation=representation,
                biological_unit="donor",
                unit_membership_sha256=digest("paired donor roster"),
            )
            flow.register_artifact(item)
            links[port] = (item.version_id, None)
            pins[port] = item.sha256
        step = executor.register_single_cell_step(
            {"operation": "fit_donor_trajectory", "parameters": {}},
            step_id="fit-trajectory",
            branch_id="trajectory",
            biological_unit="donor",
            sources=links,
        )
        aims = ThesisAimLifecycle(
            ledger, lineage, authorize_operator=lambda actor: actor == "researcher"
        )
        aims.propose(_aim(
            "trajectory",
            kind="confirmatory",
            input_sha256s=tuple(pins.values()),
            evaluation_sha256s=[pins["trajectory_expression"]],
            multiplicity={
                "family_id": "trajectory-testing",
                "aim_ids": ["trajectory"],
                "hypotheses_sha256": reviewed.hypotheses_sha256(),
                "fdr": 0.05,
            },
        ))
        _approve(aims, "trajectory")
        axis = digest("ordered cell inventory")
        measurements = (
            AttestedMeasurement(
                kind="normalized_expression",
                artifact_sha256=reviewed.normalized_expression_sha256,
                receipt_sha256=reviewed.normalization_receipt_sha256,
                observation_axis_sha256=axis,
                feature_axis_sha256=reviewed.feature_axis_sha256,
            ),
            AttestedMeasurement(
                kind="inferred_pseudotime",
                artifact_sha256=reviewed.coordinates_sha256,
                receipt_sha256=reviewed.axis_receipt_sha256,
                observation_axis_sha256=axis,
            ),
        )
        design = DesignEvidence(
            biological_unit="donor",
            receipt_sha256=reviewed.design_receipt_sha256,
            observation_axis_sha256=axis,
            estimability="estimable",
            independent_units_per_group=(8, 8),
        )
        runtime = RuntimeEvidence(
            question="donor_trajectory",
            tier="tier_a",
            operation_id=step.operation_id,
            runtime_sha256=executor.environment_sha256,
        )
        host_receipts = (*measurements, design, runtime, reviewed)
        thesis = ThesisWorkflow(
            ledger, lineage, verify_attestation=lambda _db, item: item in host_receipts,
        )
        broker = EvidenceBroker(authority)
        policy = AccessPolicy("trajectory-readback", 1, allow_outcomes=True)
        broker.register_policy(policy)
        broker.register_context(
            "viewer",
            principal_id="scientist",
            lineage_id=lineage,
            role="scientist",
            phase="readback",
            policy_sha256=policy.sha256(),
        )
        broker.register_source(
            "graph",
            content_sha256=digest("trajectory graph"),
            roots=(digest("root"),),
            classification="structural",
        )
        controller, selector = _fixture_adaptive_controller(
            app.scientific.planner(), ledger, lineage, broker,
            step_id=step.step_id, thesis_lifecycle=thesis,
        )
        app.bind_adaptive_workflow(WorkbenchAdaptiveBinding(
            lineage, broker.delivery("viewer", "graph"), controller=controller,
            tier_a_executor=executor,
        ))
        revision = _graph_revision(flow)
        args = {
            "step": step,
            "aim_id": "trajectory",
            "request": AnswerabilityRequest(
                family_id=7, question="donor_trajectory",
                assay="single_nucleus_rna_seq", entry_point="annotated_object",
            ),
            "measurements": measurements,
            "design": design,
        }
        with pytest.raises(OperationConflict, match="revision changed"):
            bind_workbench_donor_trajectory(
                app, lineage, **args, trajectory_evidence=reviewed,
                expected_revision=revision - 1,
            )
        unverified = reviewed.model_copy(update={"review_receipt_sha256": "5" * 64})
        with pytest.raises(OperationBlocked, match="trajectory_evidence_unverified"):
            bind_workbench_donor_trajectory(
                app, lineage, **args, trajectory_evidence=unverified,
                expected_revision=revision,
            )
        assert _graph_revision(flow) == revision
        assert flow.history(step.step_id) == ()
        held = bind_workbench_donor_trajectory(
            app, lineage, **{**args, "measurements": measurements[:1]},
            expected_revision=revision,
        )
        assert held["decision"]["missing_measurements"] == [["inferred_pseudotime"]]
        assert held["step_id"] is None
        receipt = bind_workbench_donor_trajectory(
            app, lineage, **args, trajectory_evidence=reviewed,
            expected_revision=held["revision"],
        )
        assert receipt["step_id"] == step.step_id
        assert receipt["decision"]["status"] == "ready"
        assert selector.controller_turns == 0

        with running(app) as port:
            status, body, _ = call(
                port,
                "/api/scientific/adaptive/" + lineage,
                origin=f"http://127.0.0.1:{port}",
                body={
                    "action": "advance",
                    "expected_revision": receipt["revision"],
                    "allow_data_execution": True,
                    "allow_model_execution": True,
                },
            )
        assert status == 200, body.decode()
        assert json.loads(body)["steps_dispatched"] == 1
        result = _published_trajectory(executor, flow.history(step.step_id)[0])
        assert result["trajectory_evidence_sha256"] == reviewed.sha256()
        assert result["claim_scope"] == "candidate_research_inference"
        assert result["clinical_release_allowed"] is False
    finally:
        app.close()
