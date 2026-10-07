"""Closed correlation receipts reject hostile and invented evidence."""
from copy import deepcopy
import uuid
import pytest
from devbench.analysis.rna_trace_contract import validate_trace_context, validate_trace_event


def event(**changes):
    root = str(uuid.UUID(int=1))
    value = {"format": "devbench.rna-trace-event/1", "event_id": str(uuid.UUID(int=2)), "trace_id": root, "span_id": root, "parent_span_id": None, "sequence": 0, "attempt": 1, "role": "coordinator", "phase": "workflow", "kind": "completion", "source_status": "completed", "dispatch_status": "not_dispatched", "capture_status": "unavailable", "clock": {"utc": None, "monotonic_ns": None, "process_alias": str(uuid.UUID(int=3)), "timing_basis": "unavailable"}, "bindings": {"workflow": "a" * 64}, "metrics": {"input_tokens": None, "output_tokens": None, "total_tokens": None, "elapsed_seconds": None, "peak_rss_mb": None, "provider_cost": {"value": None, "currency": None, "basis": "unavailable", "source_alias": None}, "usage_basis": "unavailable"}, "reason_code": "none", "origin": "retained_authoritative_evidence"}
    value.update(changes)
    return value


def context():
    return {"format": "devbench.rna-trace-context/1", "trace_id": str(uuid.UUID(int=1)), "span_id": str(uuid.UUID(int=2)), "parent_span_id": str(uuid.UUID(int=1)), "workflow_alias": str(uuid.UUID(int=3)), "attempt": 1, "role": "investigator", "phase": "selection", "invocation_binding_digest": "a" * 64, "source_digest": "b" * 64, "runtime_digest": "c" * 64, "policy_digest": "d" * 64}


@pytest.mark.parametrize("field", ["prompt", "response", "thoughts", "question", "dataset", "path", "selector", "grant", "credentials", "baggage", "future_field"])
def test_unknown_fields_are_never_masked(field):
    for validator, value in ((validate_trace_event, event()), (validate_trace_context, context())):
        value[field] = "PRIVACY_CANARY"
        with pytest.raises(ValueError) as caught:
            validator(value)
        assert "PRIVACY_CANARY" not in str(caught.value)


@pytest.mark.parametrize("key,bad", [("attempt", 0), ("attempt", True), ("sequence", -1), ("trace_id", "bad"), ("role", "admin"), ("phase", "unknown"), ("kind", "success"), ("source_status", "unknown"), ("dispatch_status", "success"), ("capture_status", "complete"), ("reason_code", "free text"), ("origin", "model_claim")])
def test_invalid_event_fields(key, bad):
    with pytest.raises(ValueError):
        validate_trace_event(event(**{key: bad}))


@pytest.mark.parametrize("field", ["input_tokens", "output_tokens", "total_tokens", "elapsed_seconds", "peak_rss_mb"])
@pytest.mark.parametrize("bad", [-1, True, float("nan"), float("inf"), 10**1000])
def test_invalid_numbers(field, bad):
    value = event()
    value["metrics"][field] = bad
    with pytest.raises(ValueError):
        validate_trace_event(value)


@pytest.mark.parametrize("clock", [{"utc": "1969-01-01T00:00:00Z"}, {"utc": "2026-01-01T00:00:00"}, {"monotonic_ns": -1}, {"timing_basis": "observed"}, {"headers": "canary"}])
def test_bad_clock(clock):
    value = event()
    value["clock"].update(clock)
    with pytest.raises(ValueError):
        validate_trace_event(value)


def test_copies_and_scientific_bindings_remain_local():
    value = event()
    returned = validate_trace_event(value)
    returned["bindings"]["workflow"] = "b" * 64
    assert value["bindings"]["workflow"] == "a" * 64
    assert validate_trace_context(context()) == context()


def test_metrics_and_cost_require_actual_evidence():
    value = event()
    value["metrics"].update(input_tokens=10, output_tokens=2, total_tokens=12, usage_basis="verified_complete")
    assert validate_trace_event(value) == value
    value["metrics"]["total_tokens"] = 11
    with pytest.raises(ValueError):
        validate_trace_event(value)
    value = event()
    value["metrics"]["provider_cost"].update(value=0, currency="USD", source_alias=str(uuid.UUID(int=5)), basis="verified_recorded_charge")
    assert validate_trace_event(value) == value
    value["metrics"]["provider_cost"]["source_alias"] = None
    with pytest.raises(ValueError):
        validate_trace_event(value)


def test_reconstructed_display_time_is_not_duration():
    value = event()
    value["clock"].update(utc="2000-01-01T00:00:00Z", timing_basis="display_anchor")
    value["metrics"]["elapsed_seconds"] = 0
    with pytest.raises(ValueError):
        validate_trace_event(value)


def test_bad_digests_and_oversize_rejected():
    for bad in ("A" * 64, "x" * 64, "a" * 63, None):
        value = context()
        value["source_digest"] = bad
        with pytest.raises(ValueError):
            validate_trace_context(value)
    value = event()
    value["bindings"]["workflow"] = "a" * 5000
    with pytest.raises(ValueError):
        validate_trace_event(value)
