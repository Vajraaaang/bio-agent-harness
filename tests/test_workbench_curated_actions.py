"""Browser declarations for the bound single-cell executor."""

import pytest

from devbench.analysis.evidence_access import AccessPolicy, EvidenceBroker
from devbench.analysis.workbench_ui import WorkbenchService
from devbench.engine.tier_a_executor import TierAExecutor
from devbench.engine.workflow_operations import (
    LEDGER_NAME,
    OperationBlocked,
    OperationConflict,
    WorkflowOperationLedger,
)
from devbench.platform.adaptive_workflow import ArtifactVersion
from devbench.platform.workbench_adaptive import WorkbenchAdaptiveBinding
from devharness_controlled.native_contracts import single_cell_kernel_images
from tests.test_investigation_invalidation import digest
from tests.test_investigation_publication_epochs import authorize
from tests.test_scientific_authority_migration import _binding
from tests.test_scientific_workspace import action, scientific_context, setup_workspace
from tests.test_scientific_workspace_http import _fixture_adaptive_controller

pytestmark = pytest.mark.wal


@pytest.fixture
def curated(workspace_tmp_path):
    root = workspace_tmp_path
    initial, methods, _, method_step, inputs = setup_workspace(root)
    selector = initial.scientific.selector
    initial.close()
    actors = root / "actors"
    actors.mkdir(mode=0o700)
    app = WorkbenchService(
        root / "curated-browser",
        [inputs],
        enable_scientific=True,
        scientific_workspace=methods.root,
        allow_data_execution=True,
        allow_model_execution=True,
        model_data_scope="synthetic",
        codex_model="fixture-only-model",
        method_selector=selector,
        agent_workspaces=actors,
    )
    try:
        _, dataset, analysis = scientific_context(app, method_step)
        plan = action(
            app, "create_plan", dataset_ref=dataset["ref"], analysis_ref=analysis["ref"]
        )
        session_id = plan["session_id"]
        ledger = WorkflowOperationLedger(methods.root / LEDGER_NAME, scientific=True)
        authority = ledger.scientific
        authority.commit(session_id, expected_revision=0, record=_binding())
        authority.commit(
            session_id,
            expected_revision=1,
            record={
                "kind": "grant",
                "record_id": "grant-a",
                "binding_id": "binding-a",
                "grant_sha256": digest("single-cell declaration grants"),
            },
        )
        authority.activate_execution(
            session_id,
            expected_revision=2,
            binding_id="binding-a",
            grant_id="grant-a",
            max_jobs=5,
            authorize=authorize,
        )
        flow = app.scientific.planner().adaptive_workflow(session_id, ledger)
        executor = TierAExecutor(
            ledger,
            allow_data_execution=True,
            mounted_image_id=sorted(single_cell_kernel_images())[0],
        )
        flow.register_artifact(
            ArtifactVersion(
                version_id="counts",
                sha256=executor.training.put(b"declaration-only fixture"),
                kind="counts",
                representation="raw_counts_h5ad",
                biological_unit="donor",
                unit_membership_sha256=digest("units"),
                measurement_history=("raw_counts",),
            )
        )
        broker = EvidenceBroker(authority)
        policy = AccessPolicy("curated-viewer", 1, allow_outcomes=True)
        broker.register_policy(policy)
        broker.register_context(
            "viewer",
            principal_id="scientist",
            lineage_id=session_id,
            role="scientist",
            phase="readback",
            policy_sha256=policy.sha256(),
        )
        broker.register_source(
            "graph",
            content_sha256=digest("graph"),
            roots=(digest("graph-root"),),
            classification="structural",
        )
        controller, controller_selector = _fixture_adaptive_controller(
            app.scientific.planner(),
            ledger,
            session_id,
            broker,
            step_id="batch-qc",
        )
        app.bind_adaptive_workflow(
            WorkbenchAdaptiveBinding(
                session_id,
                broker.delivery("viewer", "graph"),
                controller=controller,
                tier_a_executor=executor,
            )
        )
        yield app, flow, controller_selector
    finally:
        app.close()


def queue(revision):
    return {
        "action": "queue",
        "expected_revision": revision,
        "operation": "scrna_batch_qc",
        "step_id": "batch-qc",
        "branch_id": "single-cell",
        "biological_unit": "donor",
        "parameters": {"batch_key": "batch", "min_genes": 10},
        "sources": {"expression": {"id": "counts", "port": None}},
        "allow_data_execution": True,
    }


def test_queue_exposes_typed_choices_and_preserves_separate_execution(curated):
    from devbench.platform.workbench_curated_actions import curated_action, curated_view

    app, flow, selector = curated
    session_id = flow.lineage_id
    before = curated_view(app, session_id)
    qc = next(row for row in before["operations"] if row["id"] == "scrna_batch_qc")
    assert qc["available"]
    assert qc["parameters_schema"]["properties"]["batch_key"]["type"] == "string"
    assert before["sources"] == [
        {
            "id": "counts",
            "port": None,
            "kind": "counts",
            "biological_unit": "donor",
            "representation": "raw_counts_h5ad",
        }
    ]
    assert (
        curated_action(app, session_id, queue(before["revision"]))["step_id"]
        == "batch-qc"
    )
    after = curated_view(app, session_id)
    assert after["revision"] > before["revision"]
    assert after["steps"][0]["status"] == "ready"
    assert after["steps"][0]["generation"] == 1
    assert after["steps"][0]["can_revise_qc"] is False
    assert flow.authority.attempts(session_id) == []
    assert selector.controller_turns == 0


def test_invalid_or_ungranted_queue_and_stale_qc_revision_do_not_change_graph(curated):
    from devbench.platform.workbench_curated_actions import curated_action, curated_view

    app, flow, _ = curated
    session_id = flow.lineage_id
    revision = curated_view(app, session_id)["revision"]
    request = queue(revision)
    with pytest.raises(PermissionError):
        curated_action(app, session_id, {**request, "allow_data_execution": False})
    with pytest.raises(ValueError):
        curated_action(
            app,
            session_id,
            {**request, "parameters": {"batch_key": "batch", "min_genes": "1"}},
        )
    with pytest.raises(ValueError):
        curated_action(app, session_id, {**request, "purpose": "qualified_inference"})
    with pytest.raises(OperationConflict):
        curated_action(app, session_id, {**request, "expected_revision": revision - 1})
    assert curated_view(app, session_id)["revision"] == revision
    curated_action(app, session_id, request)
    with pytest.raises(OperationBlocked):
        curated_view(app, session_id, qc_step="batch-qc", generation=1)
