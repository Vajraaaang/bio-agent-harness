"""Secret-free local observation controls; no hosted or scientific qualification."""
from tests.support.workflow_science_fixtures import RecordingScientificRuntime
import pytest

from devbench.analysis import rna_trace_receipts as receipts
from devbench.analysis.rna_trace_contract import canonical_bytes, trace_digest, validate_trace_event
from devbench.analysis.rna_trace_projection import project_rna_trace


IDENTITIES = dict(source_digest="1" * 64, runtime_digest="2" * 64, policy_digest="3" * 64)


def _observe(*args, **kwargs):
    helper = getattr(receipts, "observe_boundary", None)
    assert callable(helper), "The publication route has no optional existing-observer boundary hook"
    return helper(*args, **kwargs)


def _observer(root):
    return receipts.LocalTraceObserver(root / "observer.sqlite", **IDENTITIES)


def _call(observer, identity, phase, operation, **kwargs):
    return _observe(observer, identity, phase, operation,
                    binding_digest=trace_digest([identity, phase]), **kwargs)


def test_default_none_preserves_exact_result_exception_and_one_call(tmp_path):
    calls = []
    value = {"private_value": object()}
    assert _call(None, "synthetic", "generate", lambda: calls.append(1) or value) is value
    error = PermissionError("PRIVATE_FAILURE_CANARY")
    def denied():
        calls.append(2)
        raise error
    with pytest.raises(PermissionError) as observed:
        _call(None, "synthetic", "permission_preflight", denied, admission=True)
    assert observed.value is error and calls == [1, 2]
    assert list(tmp_path.iterdir()) == []


def test_generation_denial_and_separate_execution_have_distinct_roots(tmp_path):
    observer = _observer(tmp_path)
    denied_id, separate_id = "denied-synthetic-trial", "separate-synthetic-execution"
    _call(observer, denied_id, "generate", lambda: {"workflow": "PRIVATE_QUESTION_CANARY"})
    error = PermissionError("PRIVATE_APPROVAL_CANARY")
    calls = []
    def admission():
        calls.append("admission")
        raise error
    def denied_run():
        return _call(observer, denied_id, "permission_preflight", admission, admission=True)
    with pytest.raises(PermissionError) as caught:
        _call(observer, denied_id, "run", denied_run, role="numerical", finalize_root=True)
    assert caught.value is error and calls == ["admission"]
    denied = observer.read_events(denied_id)
    admission_end = [row for row in denied if row["phase"] == "permission_preflight"][-1]
    assert admission_end["source_status"] == "blocked"
    assert admission_end["dispatch_status"] == "not_dispatched"
    assert admission_end["reason_code"] == "admission_blocked"
    run = [row for row in denied if row["phase"] == "run"]
    assert run[-1]["kind"] == "failure" and run[-1]["source_status"] == "blocked"
    assert run[-1]["dispatch_status"] == "unknown"
    assert not any(row["kind"] in {"dispatch", "completion"} for row in run)
    assert [row for row in denied if row["phase"] == "workflow"][-1]["source_status"] == "blocked"
    _call(observer, separate_id, "run", lambda: {"value": 4}, role="numerical", finalize_root=True)
    separate = observer.read_events(separate_id)
    assert {row["trace_id"] for row in denied}.isdisjoint({row["trace_id"] for row in separate})
    assert b"PRIVATE_" not in canonical_bytes(denied + separate)
    assert observer.failure_count == 0


def test_same_root_correlates_phases_but_no_completion_before_actual_finish(tmp_path):
    observer = _observer(tmp_path)
    identity = "synthetic-publication-chain"
    order = []
    for phase in ("generate", "run", "verify", "synthesis", "review"):
        _call(observer, identity, phase, lambda phase=phase: order.append(phase))
    events = observer.read_events(identity)
    roots = [row for row in observer.read_contexts(identity) if row["parent_span_id"] is None]
    assert len(roots) == 1 and len({row["trace_id"] for row in events}) == 1
    root = roots[0]["span_id"]
    assert all(row["parent_span_id"] == root for row in events if row["phase"] != "workflow")
    assert not any(row["kind"] == "completion" for row in events if row["phase"] == "workflow")
    _call(observer, identity, "verify", lambda: None, finalize_root=True)
    assert observer.read_events(identity)[-1]["span_id"] == root
    assert observer.read_events(identity)[-1]["source_status"] == "completed"
    assert order == ["generate", "run", "verify", "synthesis", "review"]


def test_restart_keeps_ids_history_and_new_attempts_separate(tmp_path):
    observer = _observer(tmp_path)
    identity = "restart-synthetic"
    _call(observer, identity, "generate", lambda: None)
    before = observer.read_events(identity)
    reopened = receipts.LocalTraceObserver.open_existing(observer.path)
    assert reopened.read_events(identity) == before
    _call(reopened, identity, "generate", lambda: None)
    after = reopened.read_events(identity)
    assert after[:len(before)] == before
    contexts = [row for row in reopened.read_contexts(identity) if row["phase"] == "generate"]
    assert sorted(row["attempt"] for row in contexts) == [1, 2]
    assert len({row["span_id"] for row in contexts}) == 2
    assert len({row["trace_id"] for row in contexts}) == 1
    assert reopened.failure_count == 0


@pytest.mark.parametrize("error", [RuntimeError("PRIVATE_TOOL_CANARY"), PermissionError("PRIVATE_FS_CANARY"), KeyboardInterrupt()])
def test_nonadmission_exception_is_never_invented_no_dispatch(tmp_path, error):
    observer = _observer(tmp_path)
    calls = []
    def operation():
        calls.append(1)
        raise error
    with pytest.raises(type(error)) as caught:
        _call(observer, "failure-synthetic", "run", operation, role="numerical", finalize_root=True)
    assert caught.value is error and calls == [1]
    events = observer.read_events("failure-synthetic")
    assert events[-1]["kind"] == "failure"
    assert events[-1]["dispatch_status"] == "unknown"
    assert not any(row["kind"] == "completion" for row in events)
    assert b"PRIVATE_" not in canonical_bytes(events)


def test_returned_partial_denial_does_not_claim_no_previous_dispatch(tmp_path):
    observer = _observer(tmp_path)
    value = {"status": "admission_denied", "results": [{"status": "success"}]}
    assert _call(observer, "partial-synthetic", "run", lambda: value,
                 role="numerical", finalize_root=True) is value
    end = observer.read_events("partial-synthetic")[-1]
    assert end["kind"] == "failure" and end["source_status"] == "blocked"
    assert end["dispatch_status"] == "unknown"


def test_broken_observer_never_changes_result_or_retries_operation(tmp_path, monkeypatch):
    observer = _observer(tmp_path)
    monkeypatch.setattr(observer, "_db", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("PRIVATE_STORAGE_CANARY")))
    calls = []
    result = {"status": "success"}
    assert _call(observer, "broken-synthetic", "run", lambda: calls.append(1) or result) is result
    assert calls == [1] and observer.failure_count >= 1


@pytest.mark.parametrize("operation_error", [None, RuntimeError("PRIVATE_OPERATION_CANARY")])
def test_storage_failure_after_operation_preserves_actual_outcome(tmp_path, monkeypatch, operation_error):
    observer = _observer(tmp_path)
    value, calls = {"actual_output": object()}, []
    def operation():
        calls.append(1)
        monkeypatch.setattr(observer, "_db", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("PRIVATE_FLUSH_CANARY")))
        if operation_error is not None:
            raise operation_error
        return value
    if operation_error is None:
        assert _call(observer, "flush-synthetic", "run", operation) is value
    else:
        with pytest.raises(RuntimeError) as caught:
            _call(observer, "flush-synthetic", "run", operation)
        assert caught.value is operation_error
    assert calls == [1] and observer.failure_count >= 1


def test_projection_is_closed_readonly_and_preserves_unknown_usage(tmp_path):
    observer = _observer(tmp_path)
    identity = "projection-synthetic"
    _call(observer, identity, "generate", lambda: {"question": "PRIVATE_PAYLOAD_CANARY"})
    _call(observer, identity, "verify", lambda: None, finalize_root=True)
    events = observer.read_events(identity)
    for row in events:
        validate_trace_event(row)
    snapshot = dict(format="devbench.rna-trace-snapshot/1", workflow_binding=trace_digest(identity),
        events=events, observer_events=events, capability={"backend_kind": "offline_recorded",
        "qualification_status": "offline_recorded_qualified", "admitted": False}, allocation=0,
        actual_children=0, complete=False, gaps=["usage_unavailable"])
    root = next(row for row in events if row["parent_span_id"] is None)
    before = observer.path.read_bytes()
    projected = project_rna_trace(snapshot, export_namespace=root["trace_id"], limits={})
    assert projected["accounting"]["total_tokens"] is None
    assert projected["accounting"]["provider_cost_total"] is None
    assert all(row["inputs"] == row["outputs"] == {} for row in projected["spans"])
    assert {row["id"] for row in projected["spans"]} == {row["span_id"] for row in events}
    assert b"PRIVATE_" not in canonical_bytes(projected)
    assert observer.path.read_bytes() == before
    assert observer.read_events(identity) == events


def test_actual_generation_and_denied_trial_share_observer_without_tool_dispatch(tmp_path):
    from devbench.analysis import rna_scientific_trial as trial_api
    from tests.test_rna_execution_route_qualification import _case
    from tests.test_rna_scientific_trial import NOW

    # These observer identity labels belong to this local software fixture.
    # The trial authority separately verifies its real installed enforcement.
    observer = _observer(tmp_path / "local-observations")
    case = _case(tmp_path, governed_bridge=True, trace_observer=observer)
    request, planner = case["request"], case["planner"]
    identity = request["trial_id"]
    generated = observer.read_events(identity)
    assert any(row["phase"] == "generate" and row["kind"] == "completion" for row in generated)
    assert any(row["phase"] == "permission_preflight" and row["kind"] == "completion"
               and row["dispatch_status"] == "not_dispatched" for row in generated)
    before = planner.get(request["workflow"]["planner_session_id"])
    selections = len(case["selector"].contexts)
    with pytest.raises(PermissionError, match="Trial admission blocked.*unqualified_adapters"):
        trial_api.execute_trial(request, planner=planner, workspace=tmp_path, actor="participant",
            authority=case["authority"](), now=NOW, trace_observer=observer, scientific_workflow=RecordingScientificRuntime())
    events = observer.read_events(identity)
    assert events[:len(generated)] == generated
    assert len({row["trace_id"] for row in events}) == 1
    root = next(row["span_id"] for row in events if row["parent_span_id"] is None)
    assert all(row["parent_span_id"] == root for row in events if row["phase"] != "workflow")
    denied = [row for row in events if row["phase"] == "permission_preflight"][-1]
    assert denied["source_status"] == "blocked" and denied["dispatch_status"] == "not_dispatched"
    run = [row for row in events if row["phase"] == "run"]
    assert run and run[-1]["kind"] == "failure" and run[-1]["source_status"] == "blocked"
    assert not any(row["kind"] in {"dispatch", "completion"} for row in run)
    assert events[-1]["span_id"] == root and events[-1]["source_status"] == "blocked"
    assert planner.methods.list_records("run") == []
    assert planner.get(request["workflow"]["planner_session_id"]) == before
    assert len(case["selector"].contexts) == selections
    assert observer.failure_count == 0


def test_returned_failed_tool_receipt_remains_failure_without_reexecution(tmp_path):
    observer = _observer(tmp_path)
    receipt = {"status": "failed", "error": "PRIVATE_TOOL_ERROR_CANARY"}
    calls = []
    assert _call(observer, "failed-tool-synthetic", "run", lambda: calls.append(1) or receipt,
                 role="numerical", finalize_root=True) is receipt
    events = observer.read_events("failed-tool-synthetic")
    assert calls == [1]
    assert events[-1]["kind"] == "failure" and events[-1]["source_status"] == "failed"
    assert events[-1]["dispatch_status"] == "unknown"
    assert not any(row["kind"] == "completion" for row in events)
    assert b"PRIVATE_" not in canonical_bytes(events)
