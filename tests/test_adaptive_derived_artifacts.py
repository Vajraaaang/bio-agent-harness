"""Verify immutable derivations through the workflow ledger and its readback."""

import pytest

from devbench.engine.workflow_operations import (
    OperationBlocked,
    WorkflowOperationLedger,
)
from devbench.platform.adaptive_workflow import AdaptiveWorkflow
from tests.test_investigation_invalidation import (
    Worker,
    artifact,
    digest,
    link,
    make,
    step,
)

pytestmark = pytest.mark.wal


def test_revision_invalidates_derived_inputs_and_preserves_audit(
    workspace_tmp_path, monkeypatch,
):
    from devbench.engine import scientific_authority

    monkeypatch.setattr(scientific_authority, "_CHECKPOINT_INTERVAL", 2)
    flow = make(workspace_tmp_path)
    worker = Worker()
    qc = step("qc")
    flow.insert(qc)
    checked = flow.dispatch("qc", worker, actor="operator")
    metrics = checked["outputs"]["out"]["version_id"]
    flow.register_artifact(artifact("profile"), parents=(metrics,))
    flow.register_artifact(artifact("derived"), parents=("profile",))
    fit = step("fit", link("source", "derived", immutable=True))
    flow.insert(fit)
    result = flow.dispatch("fit", worker, actor="operator")
    assert result["status"] == "succeeded"
    provenance = flow.disclosure_provenance("fit", "out")
    assert provenance["artifacts"]["profile"]["parents"] == [metrics]
    assert provenance["artifacts"]["derived"]["parents"] == ["profile"]
    assert checked["outputs"]["out"]["sha256"] in provenance["dependencies"]

    flow.hold("qc", "Q-DESIGN", evidence_sha256=digest("scope under review"))
    with pytest.raises(OperationBlocked, match="held_or_retired"):
        flow.current_use("fit", "out", purpose="export")
    flow.hold("qc", None, evidence_sha256=digest("scope resolved"))
    flow.revise(qc, reason="diagnostic_revisit", evidence_sha256=digest("changed QC"))
    with pytest.raises(OperationBlocked, match="derived_parent_not_current"):
        flow.register_artifact(artifact("late"), parents=(metrics,))
    reader = WorkflowOperationLedger(flow.ledger.path, readonly=True, inert=True)
    reopened = AdaptiveWorkflow(reader, flow.lineage_id)
    with pytest.raises(OperationBlocked, match="current_use_unavailable"):
        reopened.current_use("fit", "out", purpose="export")
    assert reopened.history("fit") == (result,)
    assert reopened.history("qc") == (checked,)


def test_derived_artifact_needs_known_current_parents(workspace_tmp_path):
    flow = make(workspace_tmp_path)
    with pytest.raises(ValueError, match="already be registered"):
        flow.register_artifact(artifact("derived"), parents=("missing",))
    parent = artifact("parent")
    flow.register_artifact(parent)
    with pytest.raises(ValueError, match="distinct parents"):
        flow.register_artifact(artifact("derived"), parents=("parent", "parent"))
    flow.register_artifact(artifact("derived"), parents=("parent",))


def test_derived_use_remains_visible_to_partition_authority(workspace_tmp_path):
    from devbench.engine.partition_authority import PartitionAuthority

    flow = make(workspace_tmp_path)
    original = artifact("original")
    flow.register_artifact(original)
    flow.register_artifact(artifact("derived"), parents=(original.version_id,))
    flow.insert(step("fit", link("source", "derived", immutable=True)))
    assert flow.dispatch("fit", Worker(), actor="operator")["status"] == "succeeded"

    cohort = digest("fixture cohort")
    authority = PartitionAuthority(flow.ledger, cohort)
    assert authority.seal({
        "cohort_sha256": cohort,
        "source_sha256": original.sha256,
        "inventory_sha256": digest("fixture inventory"),
        "count_path": "X",
        "training": {"sha256": digest("train"), "units_sha256": digest("train units")},
        "evaluation": {"sha256": digest("eval"), "units_sha256": digest("eval units")},
    }) == "compromised"
    with pytest.raises(OperationBlocked, match="protected_partition_delivery_denied"):
        flow.current_use("fit", "out", purpose="export")
    with pytest.raises(PermissionError, match="protected parents"):
        flow.register_artifact(artifact("late"), parents=(original.version_id,))
