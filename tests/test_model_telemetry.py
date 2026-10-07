"""Native CLI identity metadata, not model self-reports or requested flags."""
import json
import os
from pathlib import Path
import tempfile
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest


def packet(session="session-1", model="gpt-6-astra", kind="response.completed", event="codex.sse_event"):
    values={"conversation.id":session,"model":model,"event.kind":kind,"event.name":event,
            "input_token_count":"20","output_token_count":"10",
            "user.email":"DO_NOT_RETAIN@example.invalid","prompt":"DO_NOT_RETAIN"}
    return {"resourceLogs":[{"scopeLogs":[{"logRecords":[{"attributes":[
        {"key":k,"value":{"stringValue":v}} for k,v in values.items()]}]}]}]}


def test_native_completion_metadata_is_filtered_and_session_bound():
    from devbench.study.model_telemetry import extract_events, completion_model
    rows=extract_events(packet())
    assert "DO_NOT_RETAIN" not in json.dumps(rows)
    assert completion_model(rows,"session-1")=="gpt-6-astra"
    assert completion_model(rows,"another-session") is None
    assert completion_model(extract_events(packet(kind="response.created")),"session-1") is None
    assert completion_model(extract_events(packet(event="codex.tool_result")),"session-1") is None
    native=packet()
    native["resourceLogs"][0]["scopeLogs"][0]["logRecords"][0]["body"]=None
    assert completion_model(extract_events(native),"session-1")=="gpt-6-astra"


def test_conflicting_runtime_models_do_not_verify():
    from devbench.study.model_telemetry import extract_events, completion_model
    rows=extract_events(packet())+extract_events(packet(model="gpt-5.5"))
    assert completion_model(rows,"session-1") is None


def test_native_request_metadata_and_string_failure_are_retained_safely():
    from devbench.study.model_telemetry import extract_events, completion_model
    payload = packet()
    attributes = payload["resourceLogs"][0]["scopeLogs"][0]["logRecords"][0]["attributes"]
    attributes.extend([
        {"key": "model_reasoning_effort", "value": {"stringValue": "low"}},
        {"key": "service_tier", "value": {"stringValue": "default"}},
        {"key": "success", "value": {"stringValue": "false"}},
    ])
    rows = extract_events(payload)
    assert rows[0]["request_metadata"] == {"model_reasoning_effort": "low", "service_tier": "default"}
    assert completion_model(rows, "session-1") is None


def native_completion(input_tokens, output_tokens, *, inference=True):
    return {"event": "codex.sse_event", "completion": True, "conversation_id": "session-1",
            "model": "gpt-6-astra", "success": None,
            "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
            "request_metadata": {"model_reasoning_effort": "low"} if inference else {}}


def test_native_accounting_preserves_explicit_warmup_prefix():
    from devbench.study.model_telemetry import correlate_usage
    events = [native_completion(15706, 0, inference=False), native_completion(36021, 122),
              native_completion(36417, 13)]
    result = correlate_usage(events, {"input_tokens": 72438, "output_tokens": 135})
    assert result == {"matches": True, "method": "inference_request_metadata_after_zero_generation_prewarm",
                      "excluded_prewarm_events": 1, "inference_events": 2}
    assert len(events) == 3


@pytest.mark.parametrize("case", ["unmarked", "inference_zero_output", "late_unmarked", "wrong_input", "missing_usage"])
def test_ambiguous_or_mismatched_accounting_fails(case):
    from devbench.study.model_telemetry import correlate_usage
    events = [native_completion(5, 0, inference=False), native_completion(20, 10)]
    if case == "unmarked":
        events[1]["request_metadata"] = {}
    elif case == "inference_zero_output":
        events[0]["request_metadata"] = {"model_reasoning_effort": "low"}
    elif case == "late_unmarked":
        events.reverse()
    elif case == "wrong_input":
        events[1]["usage"]["input_tokens"] += 1
    else:
        events[0]["usage"] = {}
    assert correlate_usage(events, {"input_tokens": 20, "output_tokens": 10})["matches"] is False


def test_exact_accounting_includes_zero_output_inference_and_requires_usage():
    from devbench.study.model_telemetry import correlate_usage
    events = [native_completion(5, 0), native_completion(20, 10)]
    assert correlate_usage(events, {"input_tokens": 25, "output_tokens": 10})["matches"] is True
    assert correlate_usage(events, None)["matches"] is False


def test_normalized_usage_counts_cached_and_reasoning_tokens_as_subsets():
    from devbench.study import model_telemetry
    assert hasattr(model_telemetry, "normalize_usage"), "Bound capture normalization is missing"
    usage = model_telemetry.normalize_usage({"input_tokens": 100, "output_tokens": 30,
        "cached_input_tokens": 80, "reasoning_tokens": 20})
    assert usage["normalized_total_tokens"] == 130
    assert usage["cached_input_tokens"] == 80
    assert usage["reasoning_tokens"] == 20
    assert usage["normalization"] == "input_output_subsets/1"


@pytest.mark.parametrize("usage", [None, {}, {"input_tokens": 12},
    {"input_tokens": True, "output_tokens": 12}, {"input_tokens": 12, "output_tokens": -1}])
def test_normalized_usage_preserves_unknown_usage(usage):
    from devbench.study import model_telemetry
    assert hasattr(model_telemetry, "normalize_usage"), "Bound capture normalization is missing"
    assert model_telemetry.normalize_usage(usage)["normalized_total_tokens"] is None


def test_normalized_usage_rejects_impossible_subset_without_fabricating_zero():
    from devbench.study import model_telemetry
    assert hasattr(model_telemetry, "normalize_usage"), "Bound capture normalization is missing"
    normalized = model_telemetry.normalize_usage({"input_tokens": 10, "output_tokens": 5,
        "cached_input_tokens": 11, "reasoning_output_tokens": 6})
    assert normalized["cached_input_tokens"] is None
    assert normalized["reasoning_tokens"] is None
    assert normalized["normalized_total_tokens"] is None


@pytest.mark.parametrize("first", [
    {"input_tokens":8,"output_tokens":4,"cached_input_tokens":20},
    {"input_tokens":8,"output_tokens":4,"reasoning_tokens":7},
    {"input_tokens":8}, {},
])
def test_aggregate_accounting_never_launders_an_unknown_phase(first):
    from devbench.study.model_telemetry import normalize_usage
    raw={"phase_usage":[first,{"input_tokens":2,"output_tokens":1}]}
    usage=normalize_usage(raw)
    assert usage["normalized_total_tokens"] is None
    assert usage["input_tokens"] is None and usage["output_tokens"] is None
    assert all(all(retained.get(key)==value for key,value in original.items())
               for retained,original in zip(usage["native_usage"]["phase_usage"],raw["phase_usage"]))
    assert normalize_usage(usage["native_usage"])==usage


def test_known_phase_usage_is_canonically_normalized_without_double_counting():
    from devbench.study.model_telemetry import normalize_usage
    raw={"phase_usage":[{"input_tokens":8,"output_tokens":4,"cached_input_tokens":2,"reasoning_tokens":1},
                        {"input_tokens":2,"output_tokens":1,"cached_input_tokens":1,"reasoning_tokens":0}]}
    usage=normalize_usage(raw)
    assert usage["normalized_total_tokens"]==15
    assert usage["cached_input_tokens"]==3 and usage["reasoning_tokens"]==1
    assert normalize_usage(usage["native_usage"])==usage


def test_partial_primary_usage_preserves_a_lower_bound_without_claiming_exact_total():
    from devbench.study.model_telemetry import normalize_usage
    usage=normalize_usage({"input_tokens":101,"cached_input_tokens":80})
    assert usage["normalized_total_tokens"] is None
    assert usage["native_usage"]["observed_token_lower_bound"]==101
    assert normalize_usage(usage["native_usage"])==usage
    combined=normalize_usage({"phase_usage":[{"input_tokens":101,"cached_input_tokens":80},
                                             {"input_tokens":2,"output_tokens":1,"reasoning_tokens":1}]})
    assert combined["normalized_total_tokens"] is None
    assert combined["native_usage"]["observed_token_lower_bound"]==104


@pytest.mark.parametrize("invalid",[None,1.5,"x"*128])
def test_invalid_native_field_remains_unknown_after_roundtrip_and_aggregation(invalid):
    from devbench.study.model_telemetry import normalize_usage
    first=normalize_usage({"input_tokens":80,"output_tokens":4,"cached_input_tokens":invalid})
    assert first["normalized_total_tokens"] is None
    assert normalize_usage(first["native_usage"])==first
    combined=normalize_usage({"phase_usage":[first["native_usage"],{"input_tokens":2,"output_tokens":1}]})
    assert combined["normalized_total_tokens"] is None
    assert combined["native_usage"]["observed_token_lower_bound"]==87
    assert first["native_usage"]["exact_accounting_available"] is False
    marker=first["native_usage"]["invalid_source_fields"][0]
    assert marker["field"]=="cached_input_tokens" and len(marker["sha256"])==64


@pytest.mark.parametrize("alternate",[2,100])
def test_conflicting_reasoning_aliases_cannot_hide_invalid_subset(alternate):
    from devbench.study.model_telemetry import normalize_usage
    usage=normalize_usage({"input_tokens":80,"output_tokens":4,"reasoning_tokens":1,"reasoning_output_tokens":alternate})
    assert usage["normalized_total_tokens"] is None
    assert usage["native_usage"]["observed_token_lower_bound"]==84
    assert normalize_usage(usage["native_usage"])==usage


@pytest.mark.parametrize("key,invalid",[("cached_input_tokens",None),("cached_input_tokens",1.5),
    ("cached_input_tokens",1000000001),("reasoning_output_tokens",99)])
def test_outer_phase_envelope_cannot_launder_invalid_reported_fields(key,invalid):
    from devbench.study.model_telemetry import normalize_usage
    raw={"phase_usage":[{"input_tokens":8,"output_tokens":4,"cached_input_tokens":2,"reasoning_tokens":1}],key:invalid}
    value=normalize_usage(raw)
    assert value["normalized_total_tokens"] is None
    assert value["native_usage"]["observed_token_lower_bound"]==12
    assert value["native_usage"]["exact_accounting_available"] is False
    assert normalize_usage(value["native_usage"])==value


@pytest.mark.skipif(os.environ.get("DEVBENCH_RUN_LOCAL_FIXTURES")!="1",reason="Local HTTP fixture requires opt-in")
def test_private_receiver_requires_capability_and_retains_no_prompt(tmp_path):
    from devbench.study.model_telemetry import NativeModelCapture
    capture=NativeModelCapture(tmp_path/"identity.json")
    body=json.dumps(packet()).encode()
    try:
        with pytest.raises(HTTPError) as error:
            urlopen(Request(capture.endpoint,data=body,headers={"Content-Type":"application/json"}),timeout=2)
        assert error.value.code==403
        urlopen(Request(capture.endpoint,data=body,headers={"Content-Type":"application/json",
            "x-devbench-capture":capture.capability}),timeout=2).close()
    finally:
        receipt=capture.close("session-1",{"input_tokens":20,"output_tokens":10})
    assert receipt["completion_model"]=="gpt-6-astra"
    assert receipt["usage_matches_cli"] is True
    assert "DO_NOT_RETAIN" not in (tmp_path/"identity.json").read_text()
    assert capture.capability not in (tmp_path/"identity.json").read_text()
