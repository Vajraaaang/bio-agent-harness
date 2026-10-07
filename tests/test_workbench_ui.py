"""Local scRNA application controls; no provider, cloud, or biological qualification."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request

import pytest


BASE = Path(__file__).resolve().parents[1] / "reports/workbench-ui/2026-09-22"


def api():
    assert importlib.util.find_spec("devbench.analysis.workbench_ui") is not None, "Operational scRNA UI service is missing"
    from devbench.analysis import workbench_ui
    return workbench_ui


@pytest.fixture
def root(request):
    (BASE / "fixtures").mkdir(parents=True, exist_ok=True)
    if request.node.name.startswith("test_real_small_fixture"):
        # Retain real execution evidence, including the later intentional tamper.
        yield Path(tempfile.mkdtemp(prefix="real-flow-", dir=BASE / "fixtures"))
        return
    with tempfile.TemporaryDirectory(prefix="controls-", dir=BASE / "fixtures") as path:
        yield Path(path)


def service(root, **kwargs):
    inputs = root / "inputs"
    inputs.mkdir(exist_ok=True)
    return api().WorkbenchService(root / "workspace", [inputs], **kwargs)


def request(source):
    return {"source": str(source), "expected_sha256": "a" * 64,
        "question": "Inspect QC, clustering and available annotation",
        "declarations": {"assay": "scrna", "measurement": "umi_counts",
            "count_provenance": "Artificial software fixture",
            "capture_unit_key": "batch", "capture_units_confirmed": False,
            "annotation_provenance": "Unavailable; do not claim cell identity"}}


def wait(app, job):
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        state = app.status(job)
        if state["status"] not in api().ACTIVE:
            return state
        time.sleep(.02)
    pytest.fail("Bounded fixture service did not finish")


def test_service_module_exists():
    api()


@pytest.mark.parametrize("body", [b'{"a":1,"a":2}', b'{"a":NaN}', b'[]', b'{', b'{"a":Infinity}'])
def test_strict_json_rejects_ambiguous_or_nonfinite_documents(body):
    with pytest.raises(ValueError):
        api().decode_json(body)


def test_strict_json_enforces_size_and_depth():
    with pytest.raises(ValueError, match="size"):
        api().decode_json(b" " * 65537)
    with pytest.raises(ValueError, match="depth"):
        api().decode_json(('{"a":' * 30 + '0' + '}' * 30).encode())


def test_workspace_is_dedicated_protected_and_disjoint(root):
    inputs = root / "inputs"
    inputs.mkdir()
    with pytest.raises(ValueError, match="disjoint"):
        api().WorkbenchService(inputs / "output", [inputs])
    with pytest.raises(ValueError, match="scratch"):
        api().WorkbenchService(Path(tempfile.gettempdir()).resolve() / "forbidden-ui-workspace", [inputs])
    target = root / "occupied"
    target.mkdir()
    (target / "other.txt").write_text("preserve")
    with pytest.raises(ValueError, match="dedicated"):
        api().WorkbenchService(target, [inputs])
    assert (target / "other.txt").read_text() == "preserve"


def test_input_containment_rejects_outside_symlinks_and_non_h5ad(root):
    app = service(root)
    outside = root / "outside.h5ad"
    outside.write_bytes(b"fixture")
    link = root / "inputs/link.h5ad"
    link.symlink_to(outside)
    for source in (outside, link, root / "inputs/../outside.h5ad"):
        with pytest.raises(ValueError):
            app.register(request(source))
    text = root / "inputs/data.csv"
    text.write_text("not an H5AD")
    with pytest.raises(ValueError, match="H5AD"):
        app.register(request(text))
    app.close()


def test_profile_is_frozen_and_cannot_widen_resource_caps(root):
    with pytest.raises(ValueError, match="ceiling"):
        service(root, parameter_overrides={"memory_mb": 16384})
    app = service(root, parameter_overrides={"memory_mb": 1024, "correction": "none"})
    params = app.configuration()["parameters"]
    params["memory_mb"] = 100000
    assert app.configuration()["parameters"]["memory_mb"] == 1024
    source = root / "inputs/cells.h5ad"
    source.write_bytes(b"fixture")
    with pytest.raises(ValueError, match="fields"):
        app.register({**request(source), "parameters": {"memory_mb": 99999}})
    app.close()


def test_execution_requires_startup_permission(root):
    app = service(root)
    with pytest.raises(PermissionError, match="startup"):
        app.run("a" * 32)
    app.close()


def test_invalid_operator_review_is_rejected_before_access(root):
    app = service(root)
    with pytest.raises(ValueError, match="confirmations"):
        app.review("a" * 32, {"reviewer": "Operator", "decision": "approve",
            "rationale": "Review", "confirmations": {}})
    app.close()


@pytest.mark.parametrize("failure", [InterruptedError("Fixture interruption"), ValueError("Fixture invalid count declaration")])
def test_preparation_failures_are_retained_without_success(root, monkeypatch, failure):
    from devbench.analysis import workbench_proposals
    def fail(output, **kwargs):
        output.mkdir()
        (output / "failure.json").write_text(json.dumps({"reason": str(failure)}))
        raise failure
    monkeypatch.setattr(workbench_proposals, "prepare_workbench_proposal", fail)
    app = service(root)
    source = root / "inputs/cells.h5ad"
    source.write_bytes(b"fixture boundary only; not a matrix")
    job = app.register(request(source))["id"]
    state = wait(app, job)
    assert state["status"] == "operation_failed"
    assert state["completed_analysis"] is False
    assert state["error"]["type"] == type(failure).__name__
    assert (app.workspace / "jobs" / job / "proposal/failure.json").is_file()
    with pytest.raises(RuntimeError, match="unavailable"):
        app.results(job)
    app.close()


def test_restart_preserves_interrupted_evidence_and_rotates_token(root, monkeypatch):
    from devbench.analysis import workbench_proposals
    monkeypatch.setattr(workbench_proposals, "prepare_workbench_proposal",
        lambda *a, **kw: (_ for _ in ()).throw(InterruptedError("Fixture stop")))
    app = service(root)
    source = root / "inputs/cells.h5ad"
    source.write_bytes(b"small fixture")
    job = app.register(request(source))["id"]
    wait(app, job)
    app._update(job, status="running")  # An abruptly stopped service's persisted index.
    retained = app.workspace / "jobs" / job / "native-failure.json"
    retained.write_text('{"status":"unfinished"}')
    token = app.token
    app.close()
    reopened = service(root)
    assert reopened.token != token
    assert reopened.status(job)["status"] == "recovery_required"
    assert reopened.status(job)["worker_liveness"] == "unknown"
    assert reopened.status(job)["completed_analysis"] is False
    assert retained.read_text() == '{"status":"unfinished"}'
    reopened.close()


def test_workspace_lock_rejects_second_process_until_shutdown(root):
    app = service(root)
    script = ("from devbench.analysis.workbench_ui import WorkbenchService; "
        "from pathlib import Path; import sys; "
        "app = WorkbenchService(Path(sys.argv[1]), [Path(sys.argv[2])]); app.close()")
    command = [sys.executable, "-c", script, str(app.workspace), str(root / "inputs")]
    denied = subprocess.run(command, cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=20)
    assert denied.returncode != 0 and "already owned" in denied.stderr
    app.close()
    accepted = subprocess.run(command, cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=20)
    assert accepted.returncode == 0, accepted.stderr


def test_workspace_lock_is_held_until_executor_finishes(root, monkeypatch):
    from devbench.analysis import workbench_proposals
    started, release = threading.Event(), threading.Event()
    def blocked(*args, **kwargs):
        started.set()
        release.wait(5)
        raise InterruptedError("Fixture released during shutdown")
    monkeypatch.setattr(workbench_proposals, "prepare_workbench_proposal", blocked)
    app = service(root)
    source = root / "inputs/cells.h5ad"
    source.write_bytes(b"boundary fixture only")
    app.register(request(source))
    assert started.wait(3)
    closing = threading.Thread(target=app.close)
    closing.start()
    try:
        with pytest.raises(RuntimeError, match="already owned"):
            service(root)
    finally:
        release.set()
        closing.join(5)
    assert not closing.is_alive()
    reopened = service(root)
    reopened.close()


def test_output_directory_symlink_cannot_redirect_jobs(root):
    app = service(root)
    outside = root / "outside"
    outside.mkdir()
    (app.workspace / "jobs").rmdir()
    (app.workspace / "jobs").symlink_to(outside, target_is_directory=True)
    source = root / "inputs/cells.h5ad"
    source.write_bytes(b"fixture")
    with pytest.raises(ValueError, match="canonical"):
        app.register(request(source))
    assert not list(outside.iterdir())
    app.close()


@pytest.mark.parametrize("outcome", ["infrastructure_failure", "execution_failure_unclassified"])
def test_native_failure_status_is_preserved_and_export_denied(root, monkeypatch, outcome):
    """Substituted backend failures test transport/state, never numerical success."""
    from devbench.analysis import workbench_proposals
    def receipt(output):
        output.mkdir(parents=True)
        manifest = output / "manifest.json"
        manifest.write_text('{"fixture":"substituted backend receipt"}')
        return {"manifest_path": str(manifest), "root_hash": "a" * 64}
    def prepare(output, **kwargs):
        value = receipt(output)
        (output / "proposal.json").write_text(json.dumps({"status": "awaiting_operator_review",
            "configuration": {"parameters": kwargs["parameters"]}}))
        return value
    def execute(bundle, output, **kwargs):
        output.mkdir()
        value = {"status": outcome, "reason": "Retained native fixture failure", "bundle_root": None, "archive": None}
        (output / "result.json").write_text(json.dumps(value))
        return value
    monkeypatch.setattr(workbench_proposals, "prepare_workbench_proposal", prepare)
    monkeypatch.setattr(workbench_proposals, "record_workbench_review", lambda output, *a, **kw: receipt(output))
    def prepare_native(output, *args, **kwargs):
        output.mkdir()
        # The actual preparation publishes this binding before execution.
        # Keep the fixture complete so it tests propagation of the native
        # failure, not the new pre-dispatch source-binding guard.
        (output / "bindings.json").write_text(json.dumps({"data": str(root / "inputs/cells.h5ad")}))
    monkeypatch.setattr(workbench_proposals, "prepare_reviewed_workbench", prepare_native)
    monkeypatch.setattr(workbench_proposals, "execute_reviewed_workbench", execute)
    app = service(root, allow_data_execution=True)
    source = root / "inputs/cells.h5ad"
    source.write_bytes(b"boundary fixture only")
    job = app.register(request(source))["id"]
    wait(app, job)
    app.review(job, {"reviewer": "fixture", "rationale": "Boundary test", "decision": "approve",
        "confirmations": {key: True for key in api().CONFIRMATIONS}})
    assert wait(app, job)["status"] == "approved"
    app.run(job)
    state = wait(app, job)
    assert state["status"] == outcome and state["completed_analysis"] is False
    assert state["execution"]["reason"] == "Retained native fixture failure"
    assert (app.workspace / "jobs" / job / "execution/result.json").is_file()
    with pytest.raises(RuntimeError, match="unavailable"):
        app.download(job, "archive")
    with pytest.raises(RuntimeError, match="approved"):
        app.run(job)
    app.close()


def test_denied_unknown_routes_and_cross_origin_or_token_requests(root):
    app = service(root)
    server = api().make_server(app, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    origin = "http://127.0.0.1:" + str(server.server_address[1])
    def call(path, **headers):
        with urllib.request.urlopen(urllib.request.Request(origin + path, headers=headers), timeout=3) as response:
            return response.read(), response.headers
    try:
        page, headers = call("/")
        assert b"__NONCE__" not in page
        assert "frame-ancestors 'none'" in headers["Content-Security-Policy"]
        assert app.token.encode() not in page
        for headers in ({}, {"X-Workbench-Token": "wrong"},
                {"X-Workbench-Token": app.token, "Origin": "https://untrusted.invalid"},
                {"X-Workbench-Token": app.token, "Host": "untrusted.invalid"}):
            with pytest.raises(urllib.error.HTTPError) as error:
                call("/api/config", **headers)
            assert error.value.code == 403
        body, _ = call("/api/config", **{"X-Workbench-Token": app.token})
        assert json.loads(body)["model_access"] is False
        for path in ("/api/unknown", "/api/config?extra=1", "/../../AGENTS.md"):
            with pytest.raises(urllib.error.HTTPError):
                call(path, **{"X-Workbench-Token": app.token})
        for origin_header in (None, "https://untrusted.invalid"):
            headers = {"X-Workbench-Token": app.token, "Content-Type": "application/json"}
            if origin_header:
                headers["Origin"] = origin_header
            mutation = urllib.request.Request(origin + "/api/jobs", data=b"{}", headers=headers, method="POST")
            with pytest.raises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(mutation, timeout=3)
            assert error.value.code == 403
    finally:
        server.shutdown()
        server.server_close()
        thread.join(3)
        app.close()


def test_page_is_self_contained_and_records_operator_plans():
    page = api().ASSET.read_text()
    assert "https://" not in page and "http://" not in page
    assert "Operator-created plan" in page
    assert "No live agent" in page
    assert "innerHTML" not in page
    assert "<label" in page and "aria-live" in page


def test_real_small_fixture_flow_and_tamper_detection(root):
    """Real existing numerical pipeline; run only after the shared source freeze."""
    from tests.test_workbench_proposals import fixture
    from devbench.analysis.checkpoints import sha256
    inputs = root / "inputs"
    inputs.mkdir()
    source, parameters, declarations = fixture(inputs)
    overrides = {key: value for key, value in parameters.items()
        if key not in {"data_sha256", "workflow", "prompt", "execution_tier", "parent_root", "model_path", "model_sha256"}}
    app = service(root, parameter_overrides=overrides, allow_data_execution=True, run_timeout_seconds=180)
    before = sha256(source)
    job = app.register({**request(source), "expected_sha256": before, "declarations": declarations})["id"]
    try:
        state = wait(app, job)
        assert state["status"] == "awaiting_operator_review", state
        assert state["proposal"]["preview"]["cells"] == 80
        assert state["proposal"]["configuration"]["parameters"]["memory_mb"] == 1024
        assert state["plan_author"] == "operator_created_recorded_plan"
        app.review(job, {"reviewer": "fixture operator", "decision": "approve",
            "rationale": "Small software control, not independent scientific review",
            "confirmations": {key: True for key in api().CONFIRMATIONS}})
        assert wait(app, job)["status"] == "approved"
        app.run(job)
        state = wait(app, job)
        assert state["status"] == "completed_review_passed", state
        result = app.results(job)
        assert result["verification"]["verified"]
        assert result["report"]["cells_input"] == 80
        assert result["scientific_review"] == "not_performed_by_this_service"
        archive, _, _ = app.download(job, "archive")
        assert archive.is_file() and archive.suffix == ".zip"
        (root / "verified-before-tamper.json").write_text(json.dumps({
            "job_id": job, "status": state["status"], "verification": result["verification"],
            "archive": str(archive), "archive_sha256": sha256(archive),
            "input_sha256": before, "model_sessions_attempted": 0,
            "scope": "real small numerical workflow; operator fixture review; no browser or live model"}, indent=2))
        assert sha256(source) == before
        stored_matrices = list(app.workspace.rglob("*.h5ad"))
        assert all(sha256(path) != before for path in stored_matrices), "Original input was copied into output"
        assert all(path.name == "selected.h5ad" and path.parent.name == "selected" for path in stored_matrices)
        assert state["proposal"]["external_input"]["copied_into_proposal"] is False
        assert json.loads((app.workspace / "jobs" / job / "bundle/bindings.json").read_text())["data"] == str(source)
        with pytest.raises(ValueError):
            app.download(job, "../state.json")
        report = app.workspace / "jobs" / job / "bundle/report.json"
        report.write_text("{}")
        with pytest.raises(ValueError, match="mismatch"):
            app.results(job)
        with pytest.raises(ValueError, match="mismatch"):
            app.download(job, "archive")
    finally:
        app.close()
