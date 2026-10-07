"""Offline software invariance; recorded selectors are not scientific validation.

Pre-fit controls declare raw counts and abstain without fitting. Post-fit controls
execute the public bounded normalized-expression fixture's approved fit once.
Independent runs have fresh identities: comparisons explicitly select scientific
and accounting fields, never claim byte equality of independently minted IDs.
"""
import json
import statistics
import time
import uuid

import pytest

from devbench.analysis.rna_trace_contract import canonical_bytes, trace_digest
from devbench.analysis.rna_trace_receipts import LocalTraceObserver
from devbench.analysis.rna_trace_projection import project_rna_trace, read_rna_trace_snapshot
from devbench.analysis.workflow_actions import create_rna_workflow
from devbench.analysis.workflow_operations import _policy, _json, _digest
from tests.test_langgraph_investigations import independent_setup
from tests.test_rna_independent_postfit_workflow import _case
from tests.test_rna_investigation_replay import fingerprint, replay_actions, deny_effects


def _observer(root, backend, actions):
    capability = backend.capabilities()
    return LocalTraceObserver(root.resolve(), source_digest=capability["source_digest"],
        runtime_digest=capability["runtime_digest"], policy_digest=_digest(_json(_policy(actions.investigation_policy["budget_policy"]))))


def _run(root, stage, allocation, observer_path=None):
    root = root.resolve()
    root.mkdir(parents=True)
    if stage == "post_fit_explanation":
        case = _case(root, allocation=allocation)
    else:
        planner, plan, backend, ledger, actions = independent_setup(root, allocation)
        case = dict(root=root, planner=planner, plan=plan, backend=backend, ledger=ledger, actions=actions,
                    methods=planner.methods, selector=actions.selector)
    observer = _observer(case["methods"].root / "observer.sqlite", case["backend"], case["actions"]) if observer_path else None
    checkpoint = root / "checkpoint.sqlite"
    with create_rna_workflow(mode="independent_investigations", actions=case["actions"],
            checkpoint_path=checkpoint, investigation_backend=case["backend"], trace_observer=observer) as workflow:
        identity = case["plan"]["session_id"]
        terminal = workflow.start(case["actions"].create_state(identity))
        while terminal["status"] == "review_required":
            terminal = workflow.resume(identity)
    case.update(identity=identity, terminal=terminal, checkpoint=checkpoint, observer=observer)
    return case


def _scientific_accounting(case):
    campaign = case["ledger"].get_campaign(case["identity"])
    saved = case["planner"].get(case["identity"])
    result = {"status": case["terminal"]["status"], "allocated_children": case["terminal"]["allocated_children"],
        "phases": [context["decision_kind"] for context in case["selector"].contexts],
        "scientific_candidates": saved["scientific_candidates"],
        "verified_calls": campaign["verified_calls"], "verified_spent_tokens": campaign["verified_spent_tokens"],
        "unresolved_count": campaign["unresolved_count"],
        "actual_child_count": len(case["backend"].child_contexts)}
    if "executor" in case:
        result.update(input_bytes=case["input_bytes"], fit_calls=case["executor"].calls,
                      executions=case["executor"].executions)
        # Elapsed time is a runtime observation; compare the actual fitted gene
        # statistics directly, not a projection's assertion of equality.
        raw = json.loads(case["executor"].raw_output_bytes[0])
        result["gene_statistics"] = raw["genes"]
    return result


def _projection(case, actions, backend, namespace):
    snapshot = read_rna_trace_snapshot(actions=actions, checkpoint_path=case["checkpoint"],
        investigation_backend=backend, workflow_id=case["identity"], allow_evidence_read=True)
    return project_rna_trace(snapshot, export_namespace=namespace,
                             limits={"max_spans": 2000, "max_bytes": 8 * 1024 * 1024})


@pytest.mark.parametrize("stage", ["pre_fit_methods", "post_fit_explanation"])
@pytest.mark.parametrize("allocation", [0, 1, 2])
def test_off_on_scientific_accounting_and_ten_readonly_projections(tmp_path, monkeypatch, stage, allocation):
    off = _run(tmp_path / "off", stage, allocation)
    on = _run(tmp_path / "on", stage, allocation, tmp_path / "observer.sqlite")
    assert _scientific_accounting(off) == _scientific_accounting(on)
    assert on["terminal"]["allocated_children"] == allocation
    if stage == "post_fit_explanation":
        assert on["executor"].executions == off["executor"].executions == 1
        assert on["executor"].calls == ["fit_temporal_trajectory"]
    assert on["observer"].read_events(on["identity"])
    read_actions, backend = replay_actions(on["planner"], on["ledger"], on["actions"])
    read_actions.trace_observer_path = on["observer"].path
    events_before = on["observer"].read_events(on["identity"])
    roots = [row for row in on["observer"].read_contexts(on["identity"]) if row["parent_span_id"] is None]
    assert len(roots) == 1
    before = fingerprint(on["root"])
    effects = deny_effects(monkeypatch, read_actions, backend)
    namespace = roots[0]["trace_id"]
    outputs = [_projection(on, read_actions, backend, namespace) for _ in range(10)]
    live_ids = {row["span_id"] for row in events_before}
    assert live_ids <= {row["id"] for row in outputs[0]["spans"]}
    assert outputs[0]["local_evidence"]["observer_events"] == events_before
    with pytest.raises(ValueError, match="namespace"):
        _projection(on, read_actions, backend, str(uuid.uuid4()))
    assert all(canonical_bytes(output) == canonical_bytes(outputs[0]) for output in outputs)
    assert effects == []
    assert fingerprint(on["root"]) == before
    assert on["observer"].read_events(on["identity"]) == events_before
    assert not (on["checkpoint"].parent / (on["checkpoint"].name + "-wal")).exists()
    assert not (on["checkpoint"].parent / (on["checkpoint"].name + "-shm")).exists()


def _p95(values):
    return sorted(values)[max(0, int(len(values) * .95 + .999999) - 1)]


def test_thirty_paired_local_observer_samples(tmp_path):
    """Absolute short-workload deltas; no provider/science/quality measurement."""
    from devbench.analysis.rna_trace_contract import validate_trace_event
    off = _run(tmp_path / "pilot", "pre_fit_methods", 0)
    observer = _observer(tmp_path / "pilot-observer.sqlite", off["backend"], off["actions"])
    identity = off["identity"]
    namespace = str(uuid.uuid4())
    actions, backend = replay_actions(off["planner"], off["ledger"], off["actions"])
    source_before = fingerprint(off["root"])
    samples = []
    for index in range(30):
        start = time.perf_counter_ns()
        request_digest = trace_digest({"iteration": index, "synthetic": True})
        baseline = time.perf_counter_ns() - start
        start = time.perf_counter_ns()
        same_digest = trace_digest({"iteration": index, "synthetic": True})
        observer.observe_operation(identity, "generate", index + 1, same_digest, "completion", "completed")
        observed = time.perf_counter_ns() - start
        assert request_digest == same_digest
        start = time.perf_counter_ns()
        projection = _projection(off, actions, backend, namespace)
        projection_ns = time.perf_counter_ns() - start
        samples.append({"baseline_seconds": baseline / 1e9, "observed_seconds": observed / 1e9,
            "paired_delta_seconds": (observed - baseline) / 1e9, "projection_seconds": projection_ns / 1e9,
            "projection_bytes": len(canonical_bytes(projection))})
    events = observer.read_events(identity)
    for event in events:
        validate_trace_event(event)
    assert len(events) == 31
    assert sum(event["kind"] == "completion" and event["phase"] == "generate" for event in events) == 30
    assert fingerprint(off["root"]) == source_before
    deltas = [row["paired_delta_seconds"] for row in samples]
    report = {"format": "devbench.rna-trace-software-overhead/1", "paired_samples": samples,
        "median_added_seconds": statistics.median(deltas), "p95_added_seconds": _p95(deltas),
        "median_projection_seconds": statistics.median(row["projection_seconds"] for row in samples),
        "p95_projection_seconds": _p95([row["projection_seconds"] for row in samples]),
        "event_count": len(events), "event_bytes": sum(len(canonical_bytes(event)) for event in events),
        "peak_rss_mb": None, "peak_rss_reason": "not_captured", "measurement_failures": [],
        "percentage_overhead": None, "percentage_reason": "baseline_near_zero",
        "scope": "short local observer serialization and read-only projection; no scientific quality evidence"}
    (tmp_path / "rna-trace-overhead.json").write_bytes(canonical_bytes(report))
    assert report["event_bytes"] <= 31 * 4096


@pytest.mark.parametrize("changes,blocking", [
    ({"design_rank_valid": False}, "design_rank_valid"),
    ({"contrast_valid": False}, "contrast_valid"),
    ({"biological_units": ["a", "a", "b", "b"]}, "biological_units"),
    ({"age_batch_confounded": True}, "age_batch_confounded"),
])
def test_public_synthetic_refusal_is_not_promoted_by_projection(changes, blocking):
    """Project a labeled fixture receipt from actual prerequisite diagnostics.

    This is a contract/projection control, not a claim that the synthetic packet
    was dispatched or executed through a workflow's numerical fitter.
    """
    from tests.test_rna_investigation_contract import contract, packet
    original = packet(changes=changes)
    before = canonical_bytes(original)
    decision = contract().investigation_prerequisites(original)
    assert decision["inference_allowed"] is False and blocking in decision["blocking"]
    identity = str(uuid.uuid4())
    binding = trace_digest(decision)
    event = {"format": "devbench.rna-trace-event/1", "event_id": str(uuid.uuid4()),
        "trace_id": identity, "span_id": identity, "parent_span_id": None,
        "sequence": 0, "attempt": 1, "role": "coordinator", "phase": "workflow",
        "kind": "verification", "source_status": "blocked", "dispatch_status": "not_dispatched",
        "capture_status": "unavailable", "clock": {"utc": None, "monotonic_ns": None,
            "process_alias": str(uuid.uuid4()), "timing_basis": "unavailable"},
        "bindings": {"workflow": binding, "result": binding},
        "metrics": {"input_tokens": None, "output_tokens": None, "total_tokens": None,
            "elapsed_seconds": None, "peak_rss_mb": None, "provider_cost": {"value": None,
                "currency": None, "basis": "unavailable", "source_alias": None}, "usage_basis": "unavailable"},
        "reason_code": "diagnostic_refusal", "origin": "optional_observer_receipt"}
    snapshot = {"investigation_stage": "pre_fit_methods", "format": "devbench.rna-trace-snapshot/1", "workflow_binding": binding, "events": [event],
        "capability": {"backend_kind": "unknown", "qualification_status": "unknown", "admitted": False},
        "allocation": 0, "actual_children": 0, "complete": False, "gaps": ["observer_absent"]}
    projected = project_rna_trace(snapshot, export_namespace=str(uuid.uuid4()), limits={})
    metadata = projected["spans"][0]["extra"]["metadata"]
    assert metadata["source_status"] == "blocked" and metadata["reason_code"] == "diagnostic_refusal"
    assert metadata["actual_children"] == 0 and not metadata["complete"]
    assert canonical_bytes(original) == before
    assert contract().investigation_prerequisites(original) == decision
