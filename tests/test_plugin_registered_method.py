"""Synthetic checks for binding registered plugin methods to actual graph inputs."""

import json
import os
from types import SimpleNamespace

import pytest

from devbench.engine.workflow_operations import OperationBlocked

pytestmark = pytest.mark.wal


@pytest.fixture
def method_session(workspace_tmp_path):
    from devbench.engine.methods import MethodService
    from devbench.platform.method_planner import ScientificMethodPlanner
    from devbench.platform.plugin_mcp import Plan, Plugin, _ledger
    from tests.test_exploratory_execution_profile import register
    from tests.test_investigation_invalidation import artifact

    workspace = workspace_tmp_path / "workspace"
    inputs = workspace_tmp_path / "inputs"
    inputs.mkdir(mode=0o700)
    matrix = inputs / "expression.csv"
    matrix.write_text("gene_id,s1,s2,s3,s4\ngene_a,1,2,3,4\ngene_b,3,1,4,2\n")
    plugin = Plugin(workspace, inputs, mounted_image_id=None)
    staged = plugin.plan(
        Plan.model_validate(
            {
                "operation": "matrix_audit",
                "inputs": [
                    {
                        "path": str(matrix),
                        "role": "expression",
                        "data_classification": "synthetic",
                    },
                ],
                "objective": "Inspect the synthetic example",
            }
        )
    )
    session_id = staged["session_id"]
    methods = MethodService(workspace / "methods", reconcile=False)
    planner = ScientificMethodPlanner(methods)
    ledger = _ledger(workspace)
    worker = planner.exploratory_executor(session_id, ledger, ())
    flow = planner.adaptive_workflow(session_id, ledger)
    input_artifact = artifact("input").model_copy(
        update={"sha256": worker.training.put(b'{"x":[1,2,3]}')}
    )
    flow.register_artifact(input_artifact)
    method = register(
        methods,
        workspace_tmp_path / "registered",
        "registered_plugin_test",
        ("data",),
        ("out",),
        """
import json
from pathlib import Path
source = json.loads(Path("/inputs/data").read_text())
Path("/outputs/out.json").write_text(json.dumps({"n": len(source["x"])}))
""",
    )
    return plugin, session_id, flow, method, input_artifact


def _freeze(fixture, **updates):
    from devbench.platform.plugin_registered_method import freeze_registered_method

    plugin, session_id, _, method, input_artifact = fixture
    request = {
        "plan_id": "2" * 32,
        "method_version_id": method["version_id"],
        "step_name": "Count fixture records",
        "parameters": {"threshold": 1},
        "input_versions": {"data": input_artifact.version_id},
    }
    return freeze_registered_method(
        plugin.jobs.workspace,
        session_id,
        **{**request, **updates},
    )


def test_freeze_binds_registered_method_and_input_bytes_without_authorizing(
    method_session,
):
    from devbench.engine.method_executor import EXPLORATORY_IMAGE_ID
    from devbench.platform.plugin_jobs import digest

    _, session_id, flow, method, input_artifact = method_session
    revision = flow.status()["revision"]
    frozen = _freeze(method_session)
    step = frozen["step"]
    assert frozen["session_id"] == session_id
    assert step["step_id"] == "plugin-" + "2" * 32
    assert step["purpose"] == "exploratory"
    assert step["oci_method_version_id"] == method["version_id"]
    assert step["parameters_sha256"] == digest({"threshold": 1})
    assert step["environment_sha256"] == digest(
        {**method["contract"]["environment"], "image_id": EXPLORATORY_IMAGE_ID}
    )
    assert frozen["pins"] == [
        {
            "port": "data",
            "version_id": input_artifact.version_id,
            "sha256": input_artifact.sha256,
            "graph_sha256": None,
        },
    ]
    assert flow.status()["revision"] == revision
    assert flow.authority.attempts(session_id) == []


def test_freeze_rejects_unknown_input_and_changed_parameter_contract(method_session):
    with pytest.raises(OperationBlocked, match="registered_method_input_unavailable"):
        _freeze(method_session, input_versions={"data": "other-session-input"})
    with pytest.raises(ValueError):
        _freeze(method_session, parameters={"threshold": -1})


def test_plugin_native_label_does_not_substitute_for_completed_graph_receipt(
    method_session,
):
    from devbench.platform.plugin_graph import _RESULT

    _, _, flow, _, source = method_session
    impersonation = source.model_copy(
        update={
            "version_id": "plugin-" + "a" * 32 + "-result",
            "representation": _RESULT,
        }
    )
    flow.register_artifact(impersonation)
    with pytest.raises(
        OperationBlocked, match="registered_method_native_source_unavailable"
    ):
        _freeze(method_session, input_versions={"data": impersonation.version_id})


def test_worker_cannot_execute_without_exact_job_launch(method_session):
    from devbench.platform.plugin_registered_method import run_registered_method

    plugin, session_id, flow, _, _ = method_session
    plan = {
        **plugin.jobs.plan(session_id),
        "plan_id": "2" * 32,
        "operation": "registered_exploratory",
        "registered_method": _freeze(method_session),
    }
    plugin.jobs.freeze(plan)
    with pytest.raises(OperationBlocked, match="registered_method_approval_required"):
        run_registered_method(
            plugin.jobs,
            "3" * 32,
            plan,
            verify_sources=lambda: pytest.fail("unapproved run"),
        )
    assert flow.authority.attempts(session_id) == []


@pytest.mark.skipif(
    os.environ.get("DEVBENCH_T15_DOCKER") != "1",
    reason="explicit local Docker smoke opt-in",
)
def test_exact_job_runs_registered_method_and_rechecks_oci_outputs(
    method_session, monkeypatch
):
    from devbench.engine.methods import MethodService
    from devbench.platform import plugin_jobs
    from devbench.platform.method_planner import ScientificMethodPlanner
    from devbench.platform.plugin_registered_method import (
        OPERATION,
        run_registered_method,
        verify_registered_method_result,
    )

    plugin, session_id, flow, _, _ = method_session
    plan = {
        **plugin.jobs.plan(session_id),
        "plan_id": "2" * 32,
        "operation": OPERATION,
        "registered_method": _freeze(method_session),
    }
    plan_id, checksum = plugin.jobs.freeze(plan)
    # Exercise dispatch synchronously in the test process. The actual registered
    # graph executor still starts a verified isolated OCI worker.
    monkeypatch.setattr(
        plugin_jobs,
        "_spawn_worker",
        lambda *_: SimpleNamespace(pid=os.getpid(), wait=lambda: None),
    )
    rid = plugin.jobs.launch(session_id, plan_id, checksum)["run_id"]
    execution = run_registered_method(
        plugin.jobs, rid, plugin.jobs.start(rid), verify_sources=lambda: None
    )
    receipt = {
        **execution,
        "run_id": rid,
        "session_id": session_id,
        "operation": OPERATION,
    }
    plugin.jobs.finish(rid, receipt=receipt)
    graph = verify_registered_method_result(plugin.jobs.workspace, rid, plan, receipt)
    output = flow.current_use(graph["step_id"], "out", purpose="cache")
    worker = ScientificMethodPlanner(
        MethodService(plugin.jobs.workspace / "methods", reconcile=False)
    ).exploratory_executor(session_id, flow.ledger, ())
    with worker.training.open(output.sha256) as stream:
        assert json.load(stream) == {"n": 3}
    assert graph["accepted_record_id"]
    assert plugin.jobs.launch(session_id, plan_id, checksum)["run_id"] == rid
    with pytest.raises(OperationBlocked, match="registered_method_receipt_changed"):
        verify_registered_method_result(
            plugin.jobs.workspace,
            rid,
            plan,
            {**receipt, "metrics": {"outputs": 7}},
        )
