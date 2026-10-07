"""Projection remains deterministic, finite, bounded and causally valid."""
from copy import deepcopy
import uuid
import pytest
from devbench.analysis.rna_trace_contract import canonical_bytes
from devbench.analysis.rna_trace_projection import project_rna_trace, validate_projected_span, read_rna_trace_snapshot
from tests.test_rna_trace_contract import event

NAMESPACE = str(uuid.UUID(int=100))


def snapshot(children=0):
    root = event()
    rows = [root]
    for number in range(children):
        child = event(event_id=str(uuid.UUID(int=10 + number)), span_id=str(uuid.UUID(int=20 + number)), parent_span_id=root["span_id"], role="investigator", phase="selection", dispatch_status="attempted")
        rows.append(child)
    return {"format": "devbench.rna-trace-snapshot/1", "workflow_binding": "a" * 64, "events": rows, "capability": {"backend_kind": "offline_replay", "qualification_status": "offline_qualified", "admitted": False}, "allocation": children, "actual_children": children, "complete": True, "gaps": []}


@pytest.mark.parametrize("children", [0, 1, 2])
def test_deterministic_private_topology(children):
    source = snapshot(children)
    before = canonical_bytes(source)
    one = project_rna_trace(source, export_namespace=NAMESPACE, limits={})
    two = project_rna_trace(source, export_namespace=NAMESPACE, limits={})
    assert canonical_bytes(one) == canonical_bytes(two)
    assert canonical_bytes(source) == before
    assert len(one["spans"]) == children + 1
    root = one["spans"][0]
    assert root["parent_run_id"] is None
    for child in one["spans"][1:]:
        assert child["parent_run_id"] == root["id"]
    assert b"a" * 64 not in canonical_bytes(one["spans"])


@pytest.mark.parametrize("case", ["orphan", "cycle", "duplicate", "wrong_workflow", "unknown_metadata"])
def test_invalid_topology(case):
    source = snapshot(2)
    if case == "orphan":
        source["events"][1]["parent_span_id"] = str(uuid.UUID(int=999))
    elif case == "cycle":
        source["events"][1]["parent_span_id"] = source["events"][2]["span_id"]
        source["events"][2]["parent_span_id"] = source["events"][1]["span_id"]
    elif case == "duplicate":
        source["events"][1]["event_id"] = source["events"][0]["event_id"]
    elif case == "wrong_workflow":
        source["events"][1]["bindings"]["workflow"] = "b" * 64
    else:
        source["events"][1]["secret"] = "canary"
    with pytest.raises(ValueError):
        project_rna_trace(source, export_namespace=NAMESPACE, limits={})


def test_identical_duplicate_idempotent_conflicting_sequence_rejected():
    source = snapshot(1)
    source["events"].append(deepcopy(source["events"][1]))
    result = project_rna_trace(source, export_namespace=NAMESPACE, limits={})
    assert len(result["spans"]) == 2
    revision = deepcopy(source["events"][1])
    revision["event_id"] = str(uuid.UUID(int=999))
    source["events"].append(revision)
    with pytest.raises(ValueError):
        project_rna_trace(source, export_namespace=NAMESPACE, limits={})


def test_caps_retain_partial_tree_and_gap_count():
    result = project_rna_trace(snapshot(2), export_namespace=NAMESPACE, limits={"max_spans": 2})
    assert not result["complete"]
    assert result["local_evidence"]["omitted_spans"] == 1
    assert result["local_evidence"]["gaps"] == ["event_cap"]
    assert all(not row["extra"]["metadata"]["complete"] for row in result["spans"])
    with pytest.raises(ValueError):
        project_rna_trace(snapshot(), export_namespace=NAMESPACE, limits={"max_spans": 2001})


def test_uncertain_and_missing_accounting_never_become_success_or_zero():
    source = snapshot(1)
    source["events"][1]["source_status"] = "uncertain"
    result = project_rna_trace(source, export_namespace=NAMESPACE, limits={})
    assert result["spans"][1]["extra"]["metadata"]["source_status"] == "uncertain"
    assert result["accounting"]["total_tokens"] is None
    assert result["accounting"]["provider_cost_total"] is None


def test_remote_validator_rejects_new_metadata_and_content():
    value = project_rna_trace(snapshot(), export_namespace=NAMESPACE, limits={})["spans"][0]
    validate_projected_span(value)
    value["extra"]["metadata"]["question"] = "canary"
    with pytest.raises(ValueError):
        validate_projected_span(value)


def test_evidence_authorization_precedes_all_source_access():
    with pytest.raises(PermissionError):
        read_rna_trace_snapshot(actions=None, checkpoint_path="/forbidden", investigation_backend=None, workflow_id="unused", allow_evidence_read=False)


def test_missing_observations_are_null_and_partial_counts_are_a_floor():
    source = snapshot(1)
    result = project_rna_trace(source, export_namespace=NAMESPACE, limits={})
    assert result["accounting"]["observed_token_subtotal"] is None
    source["events"][1]["metrics"].update(input_tokens=8, usage_basis="observed_partial")
    result = project_rna_trace(source, export_namespace=NAMESPACE, limits={})
    assert result["accounting"]["observed_token_subtotal"] is None
    assert result["spans"][1]["extra"]["metadata"]["metrics"]["input_tokens"] == 8
    assert result["accounting"]["total_tokens"] is None


def timed_snapshot(*, cross_process=False, reverse_monotonic=False, skew=False):
    source = snapshot()
    start = source["events"][0]
    start["kind"] = "reservation"
    start["source_status"] = "active"
    start["clock"].update(utc="2026-10-06T00:00:00Z", monotonic_ns=2_000_000_000, timing_basis="observed")
    end = deepcopy(start)
    end.update(sequence=1, kind="completion", source_status="completed", event_id=str(uuid.UUID(int=999)))
    end["clock"].update(utc="2026-10-06T00:00:10Z" if skew else "2026-10-06T00:00:01Z", monotonic_ns=1_000_000_000 if reverse_monotonic else 3_000_000_000)
    if cross_process:
        end["clock"]["process_alias"] = str(uuid.UUID(int=999))
    source["events"].append(end)
    return source


def test_only_same_process_monotonic_intervals_establish_duration():
    result = project_rna_trace(timed_snapshot(), export_namespace=NAMESPACE, limits={})
    assert result["spans"][0]["extra"]["metadata"]["metrics"]["elapsed_seconds"] == 1
    assert result["accounting"]["duration_seconds"] is None  # Envelope is not paid model time.
    result = project_rna_trace(timed_snapshot(cross_process=True), export_namespace=NAMESPACE, limits={})
    assert result["spans"][0]["extra"]["metadata"]["metrics"]["elapsed_seconds"] is None


@pytest.mark.parametrize("change,reason", [("reverse_monotonic", "invalid_clock"), ("skew", "clock_disagreement")])
def test_clock_anomalies_remain_explicit(change, reason):
    result = project_rna_trace(timed_snapshot(**{change: True}), export_namespace=NAMESPACE, limits={})
    assert reason in result["local_evidence"]["gaps"]
    assert result["complete"] is False


def test_cost_sum_overflow_rejected_and_mixed_currency_not_total():
    source = snapshot(2)
    for row in source["events"][1:]:
        row["metrics"].update(input_tokens=1, output_tokens=1, total_tokens=2, usage_basis="verified_complete")
        row["metrics"]["provider_cost"].update(value=1e308, currency="USD", basis="verified_recorded_charge", source_alias=str(uuid.UUID(int=101)))
    with pytest.raises(ValueError):
        project_rna_trace(source, export_namespace=NAMESPACE, limits={})
    source["events"][1]["metrics"]["provider_cost"].update(value=1, currency="USD")
    source["events"][2]["metrics"]["provider_cost"].update(value=2, currency="EUR")
    result = project_rna_trace(source, export_namespace=NAMESPACE, limits={})
    assert result["accounting"]["provider_cost_total"] is None
    assert len(result["accounting"]["provider_cost_subtotals"]) == 2


def test_unresolved_reservation_prevents_complete_usage_total():
    source = snapshot(1)
    source["events"][1]["metrics"].update(input_tokens=8, output_tokens=4, total_tokens=12, usage_basis="verified_complete")
    pending = event(event_id=str(uuid.UUID(int=500)), span_id=str(uuid.UUID(int=501)), parent_span_id=source["events"][0]["span_id"], role="lead", phase="method_scientific_planning", kind="reservation", source_status="uncertain")
    source["events"].append(pending)
    result = project_rna_trace(source, export_namespace=NAMESPACE, limits={})
    assert result["accounting"]["total_tokens"] is None
    assert result["accounting"]["observed_token_subtotal"] is None
    assert result["accounting"]["unresolved_usage_calls"] == 1


def test_top_accounting_uses_retained_ledger_without_resumming_phase_spans():
    source = snapshot(1)
    source["ledger_accounting"] = {"verified_spent_tokens": 123, "verified_calls": 2, "unresolved_count": 0, "unknown_usage_count": 0, "qualification": "complete", "policy_kind": "hard_total_tokens"}
    result = project_rna_trace(source, export_namespace=NAMESPACE, limits={})
    assert result["accounting"]["total_tokens"] == 123
    assert result["accounting"]["verified_calls"] == 2
    assert result["accounting"]["unresolved_cost_calls"] == 2
    assert result["accounting"]["provider_cost_total"] is None
    source["ledger_accounting"]["unknown_usage_count"] = 1
    result = project_rna_trace(source, export_namespace=NAMESPACE, limits={})
    assert result["accounting"]["total_tokens"] is None
    assert result["accounting"]["observed_token_subtotal"] == 123


def test_unresolved_tail_cost_blocks_scalar_even_when_known_calls_have_charges():
    source = snapshot(1)
    source["events"][1]["metrics"]["provider_cost"].update(value=2, currency="USD", basis="verified_recorded_charge", source_alias=str(uuid.UUID(int=101)))
    source["ledger_accounting"] = {"verified_spent_tokens": 12, "verified_calls": 1, "unresolved_count": 1, "unknown_usage_count": 1, "qualification": "incomplete", "policy_kind": "bounded_observed_tokens"}
    result = project_rna_trace(source, export_namespace=NAMESPACE, limits={})
    assert result["accounting"]["provider_cost_total"] is None
    assert result["accounting"]["provider_cost_subtotals"] == [{"currency": "USD", "basis": "verified_recorded_charge", "value": 2}]
    assert result["accounting"]["total_tokens"] is None
    del source["ledger_accounting"]
    source["events"].append(event(event_id=str(uuid.UUID(int=503)), span_id=str(uuid.UUID(int=504)), parent_span_id=source["events"][0]["span_id"], role="lead", phase="method_scientific_planning", kind="reservation", source_status="active"))
    result = project_rna_trace(source, export_namespace=NAMESPACE, limits={})
    assert result["accounting"]["provider_cost_total"] is None
