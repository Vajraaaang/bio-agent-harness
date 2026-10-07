"""Local lease/recovery checks without launching models or scientific work."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import fcntl
from threading import Event
import uuid

import pytest

from devbench.analysis.methods import MethodService, _digest


def _planner(tmp_path):
    from devbench.analysis.method_planner import ScientificMethodPlanner
    methods = MethodService(tmp_path / "workspace")
    planner = ScientificMethodPlanner(methods)
    state = planner.create({"scientific_objective": "Synthetic recovery check", "computational_task": "quality_control"},
                           {"observed": {}, "inferred": {}, "unknowns": []}, actor="scientist",
                           data_classification="synthetic")
    analysis = methods.create_analysis("Unchanged canonical analysis", actor="scientist")
    return planner, state, analysis


def _orphan(planner, state):
    changed = deepcopy(state)
    changed["selection_in_progress"] = "interrupted-selection"
    changed["selection_history"] = [{"attempt": "prior", "status": "accepted"},
                                    {"attempt": "interrupted-selection", "status": "running"}]
    declaration = {"id": "interrupted-pilot", "candidates": ["A", "B"], "status": "declared",
                   "metrics": [{"name": "runtime_seconds"}], "parameters": {"A": {}, "B": {}}}
    declaration["declaration_sha256"] = _digest(declaration)
    declaration["status"] = "running"
    changed["experiments"] = [declaration, {"id": "not-started", "status": "declared", "candidates": ["C"]}]
    changed["full_execution"] = {"step_id": "retained-step", "status": "running", "recommendation_sha256": "a" * 64}
    changed["approvals"] = {"approve_full_execution": {"status": "approved", "step_id": "retained-step"}}
    planner._save(state, changed, actor="fixture", action="fixture_interruption_state")
    return changed


def test_orphan_recovery_marks_failures_without_changing_declarations_approvals_or_canonical_plan(tmp_path):
    from devbench.analysis.method_planner_recovery import recover_plan
    planner, state, analysis = _planner(tmp_path)
    orphan = _orphan(planner, state)
    method_run_id = uuid.uuid4().hex
    directory = planner.methods.root / "runs" / method_run_id
    directory.mkdir()
    (directory / "lease").touch()
    with planner.methods._db() as db:
        planner.methods._put(db, "run", method_run_id, {"run_id": method_run_id, "status": "running",
                                                       "outputs": {}, "backend": "trusted_coordinator"})

    report = recover_plan(planner, state["session_id"])
    recovered = planner.get(state["session_id"])
    assert report["recovered"] is True and report["active_operation"] is False
    assert method_run_id in report["recovered_method_runs"]
    assert planner.methods.get("run", method_run_id)["status"] == "interrupted"
    assert recovered["selection_in_progress"] is None
    assert recovered["selection_history"][-1]["status"] == "interrupted"
    assert recovered["selection_history"][0] == orphan["selection_history"][0]
    experiment = recovered["experiments"][0]
    assert experiment["status"] == "interrupted"
    assert {key: value for key, value in experiment.items() if key != "status"} == {
        key: value for key, value in orphan["experiments"][0].items() if key != "status"}
    assert recovered["experiments"][1] == orphan["experiments"][1]
    assert [row["candidate"] for row in recovered["results"]] == ["A", "B"]
    for row in recovered["results"]:
        assert row["status"] == "failed" and row["experiment_id"] == "interrupted-pilot"
        assert row["metric_values"] == {} and row["artifacts"] == [] and row["warnings"] == []
        assert row["failures"]
        assert row["provenance"] == {"declaration_sha256": experiment["declaration_sha256"]}
    assert recovered["full_execution"]["status"] == "interrupted"
    assert recovered["approvals"] == orphan["approvals"]
    assert planner.methods.analysis(analysis["analysis_id"]) == analysis
    assert recover_plan(planner, state["session_id"])["recovered"] is False
    assert planner.get(state["session_id"])["results"] == recovered["results"]
    with planner.methods._db() as db:
        snapshot = planner.methods._get(db, "method_plan_snapshot", _digest(recovered))
        audit = db.execute("SELECT payload FROM audit ORDER BY sequence DESC LIMIT 1").fetchone()[0]
    assert snapshot == recovered and "restart-reconciliation" in audit


def test_an_active_plan_lease_is_never_recovered(tmp_path, monkeypatch):
    from devbench.analysis.method_planner_recovery import recover_plan
    planner, state, _ = _planner(tmp_path)
    orphan = _orphan(planner, state)
    directory = planner.methods.root / "planner-leases"
    directory.mkdir()
    monkeypatch.setattr(planner.methods, "reconcile", lambda: pytest.fail("Active operation must not trigger reconciliation"))
    with (directory / state["session_id"]).open("wb") as lease:
        fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
        report = recover_plan(planner, state["session_id"])
        assert report["recovered"] is False and report["active_operation"] is True
        assert planner.get(state["session_id"]) == orphan


def test_concurrent_operations_on_one_plan_fail_without_waiting(tmp_path):
    from devbench.analysis.method_planner_recovery import plan_operation
    planner, state, _ = _planner(tmp_path)
    entered, release = Event(), Event()

    @plan_operation
    def operation(self, session_id):
        entered.set()
        assert release.wait(5)
        return "finished"

    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(operation, planner, state["session_id"])
        try:
            assert entered.wait(3)
            with pytest.raises(ValueError, match="progress|concurrent|active"):
                operation(planner, state["session_id"])
        finally:
            release.set()
        assert first.result(timeout=3) == "finished"


def test_decorator_recovers_before_work_and_releases_after_success_or_failure(tmp_path):
    from devbench.analysis.method_planner_recovery import plan_operation, recover_plan
    planner, state, _ = _planner(tmp_path)
    _orphan(planner, state)

    @plan_operation
    def operation(self, session_id, *, fail=False):
        current = self.get(session_id)
        assert current["selection_in_progress"] is None
        assert current["experiments"][0]["status"] == "interrupted"
        if fail:
            raise RuntimeError("operation failed after lease acquisition")
        return "finished"

    assert operation(planner, state["session_id"]) == "finished"
    with pytest.raises(RuntimeError, match="operation failed"):
        operation(planner, state["session_id"], fail=True)
    report = recover_plan(planner, state["session_id"])
    assert report["active_operation"] is False and report["recovered"] is False
    assert operation(planner, state["session_id"]) == "finished"


def test_unknown_plan_is_rejected_before_a_lease_path_is_created(tmp_path):
    from devbench.analysis.method_planner_recovery import plan_operation, recover_plan
    planner, _, _ = _planner(tmp_path)

    @plan_operation
    def never_enter(self, session_id):
        pytest.fail("Unknown plan reached the operation")

    for operation in (recover_plan, never_enter):
        with pytest.raises(ValueError):
            operation(planner, "../../unregistered-plan")
    assert not (planner.methods.root / "planner-leases").exists()
