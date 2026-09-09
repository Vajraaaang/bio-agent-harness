"""Local dispatch/lease boundary tests. Doubles here do not represent native analysis."""
import copy
import threading

import pytest

from devharness_controlled import operated_native as subject
from devharness_controlled.qualification import synthetic_approvals
from devharness_controlled.run_registry import RunRegistry
from tests.test_native_execution import plan


def submission(tmp_path, **overrides):
    p = plan()
    approvals, signers = synthetic_approvals(p)
    args = dict(registry_dir=tmp_path/"registry", request_id="test-request", actor_id="test-operator",
                counts_path=tmp_path/"source-counts.csv", metadata_path=tmp_path/"source-metadata.csv",
                full_design_path=tmp_path/"source-full.csv", reduced_design_path=tmp_path/"source-reduced.csv",
                output_dir=tmp_path/"run")
    args.update(overrides)
    return p, approvals, signers, args


def test_duplicate_submission_observes_original_without_second_execution(tmp_path, monkeypatch):
    p, approvals, signers, args = submission(tmp_path)
    calls = []
    def fail(*positional, **keywords):
        calls.append(keywords)
        raise ValueError("synthetic dispatch failure before any native work")
    monkeypatch.setattr(subject, "execute_native_plan", fail)
    first = subject.submit_native_plan(p, approvals, signers, **args)
    second = subject.submit_native_plan(p, approvals, signers, **args)
    assert first["status"] == "failed" and first["started_new_execution"] is True
    assert second["status"] == "existing_request" and second["started_new_execution"] is False
    assert first["attempt_id"] == second["attempt_id"] and len(calls) == 1
    assert second["attempt"]["status"] == "failed" and second["clinical_release_allowed"] is False


@pytest.mark.parametrize("mode", ["approval", "registry", "counts", "workspace"])
def test_same_request_cannot_silently_rebind_submission(tmp_path, monkeypatch, mode):
    p, approvals, signers, args = submission(tmp_path)
    monkeypatch.setattr(subject, "execute_native_plan", lambda *a, **kw: (_ for _ in ()).throw(ValueError("fixture failure")))
    subject.submit_native_plan(p, approvals, signers, **args)
    if mode == "approval": approvals = approvals[::-1]
    if mode == "registry": signers = signers[::-1]
    if mode == "counts": args["counts_path"] = tmp_path/"different.csv"
    if mode == "workspace": args["output_dir"] = tmp_path/"different-run"
    with pytest.raises(ValueError):
        subject.submit_native_plan(p, approvals, signers, **args)


def test_concurrent_duplicate_does_not_start_or_steal_lease(tmp_path, monkeypatch):
    p, approvals, signers, args = submission(tmp_path)
    entered, release = threading.Event(), threading.Event()
    results = []
    def waiting(*a, **kw):
        entered.set()
        assert release.wait(5)
        raise ValueError("fixture stopped")
    monkeypatch.setattr(subject, "execute_native_plan", waiting)
    worker = threading.Thread(target=lambda: results.append(subject.submit_native_plan(p, approvals, signers, **args)))
    worker.start()
    try:
        assert entered.wait(5)
        duplicate = subject.submit_native_plan(p, approvals, signers, **args)
        assert duplicate["status"] == "existing_request" and duplicate["attempt"]["status"] == "started"
        assert not duplicate["started_new_execution"]
    finally:
        release.set(); worker.join(5)
    assert len(results) == 1 and not worker.is_alive()


def test_registry_failure_before_intent_stops_dispatch(tmp_path, monkeypatch):
    p, approvals, signers, args = submission(tmp_path)
    downstream = []
    def executor(*a, operation, on_checkpoint, **kw):
        on_checkpoint({"event": "intent", "plan_sha256": p.sha256(),
                       "container_name": "devharness-native-"+operation["attempt_id"], "payload": {}})
        downstream.append("create would happen here")
    original = RunRegistry.append_event
    def failing(registry, attempt_id, kind, payload, actor_id):
        if kind == "runtime_intent": raise OSError("synthetic storage failure")
        return original(registry, attempt_id, kind, payload, actor_id)
    monkeypatch.setattr(subject, "execute_native_plan", executor)
    monkeypatch.setattr(RunRegistry, "append_event", failing)
    result = subject.submit_native_plan(p, approvals, signers, **args)
    assert result["status"] == "failed" and not downstream


def test_real_refusal_is_preserved_with_operation_binding_and_no_docker(tmp_path, monkeypatch):
    p, approvals, signers, args = submission(tmp_path)
    monkeypatch.setattr("devharness_controlled.native_executor.LocalDocker", lambda: pytest.fail("Missing approval must not reach Docker"))
    result = subject.submit_native_plan(p, approvals[:1], signers, **args)
    assert result["status"] == "failed" and result["attempt"]["status"] == "failed"
    verified = subject.verify_saved_native_attempt(result["attempt"], result["registry_id"], signers)
    assert verified["status"] == "failed" and not verified["analysis_completed"]
    wrong = copy.deepcopy(result["attempt"]); wrong["submission_sha256"] = "b"*64
    with pytest.raises(ValueError, match="binding"):
        subject.verify_saved_native_attempt(wrong, result["registry_id"], signers)


def test_registry_commit_failure_leaves_sealed_result_for_reconciliation(tmp_path, monkeypatch):
    p, approvals, signers, args = submission(tmp_path)
    original = RunRegistry.append_event
    def commit_fails(registry, attempt_id, kind, payload, actor_id):
        if kind == "failed": raise OSError("synthetic terminal commit failure")
        return original(registry, attempt_id, kind, payload, actor_id)
    monkeypatch.setattr(RunRegistry, "append_event", commit_fails)
    result = subject.submit_native_plan(p, approvals[:1], signers, **args)
    assert result["status"] == "requires_reconciliation" and result["attempt"]["status"] == "started"
    assert (tmp_path/"run/receipt.json").is_file()
    repeat = subject.submit_native_plan(p, approvals[:1], signers, **args)
    assert repeat["status"] == "existing_request" and not repeat["started_new_execution"]


def test_registry_and_workspace_cannot_overlap(tmp_path):
    p, approvals, signers, args = submission(tmp_path, output_dir=tmp_path/"registry/run")
    with pytest.raises(ValueError, match="overlap"):
        subject.submit_native_plan(p, approvals, signers, **args)
