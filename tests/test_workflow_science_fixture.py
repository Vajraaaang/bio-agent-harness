"""The offline runtime proves immutable engineering receipts, not scientific merit."""
from copy import deepcopy

import pytest

from tests.test_langgraph_workflow import rna_plan
from tests.support.workflow_science_fixtures import RecordingScientificRuntime


def test_offline_receipt_is_exact_and_explicitly_non_scientific(tmp_path, monkeypatch):
    planner, plan, *_ = rna_plan(tmp_path)
    runtime = RecordingScientificRuntime()
    evidence = runtime.perform("discovery", planner, plan["session_id"], actor="fixture", selector=None)
    assert evidence["status"] == "descriptive"
    assert runtime.verify("discovery", planner, plan["session_id"], evidence) == evidence
    import json
    record = json.loads(planner.methods.artifact_path(evidence["record_ref"]["artifact_id"]).read_bytes())
    assert record["search"]["search_calls"] == record["search"]["provider_calls"] == record["search"]["model_calls"] == 0
    assert record["scientific_claims"] == record["qualification"] == record["independence"] == "not_established"
    monkeypatch.setattr(runtime, "perform", lambda *args, **kwargs: pytest.fail("Reconciliation redispatched fixture"))
    assert runtime.reconcile("discovery", planner, plan["session_id"], before_planner_hash=evidence["before_planner_hash"]) == evidence
    changed = deepcopy(evidence)
    changed["material_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="binding"):
        runtime.verify("discovery", planner, plan["session_id"], changed)
    with pytest.raises(ValueError, match="binding"):
        runtime.verify("candidate_review", planner, plan["session_id"], evidence)


def test_offline_receipt_rejects_changed_actual_artifact_bytes(tmp_path):
    planner, plan, *_ = rna_plan(tmp_path)
    runtime = RecordingScientificRuntime()
    evidence = runtime.perform("discovery", planner, plan["session_id"], actor="fixture", selector=None)
    path = planner.methods.artifact_path(evidence["record_ref"]["artifact_id"])
    path.chmod(0o600)
    path.write_bytes(b"{}")
    with pytest.raises(ValueError, match="artifact|digest|byte"):
        runtime.verify("discovery", planner, plan["session_id"], evidence)
