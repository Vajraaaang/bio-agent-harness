"""Closed, local-only RNA tracing receipts. Correlation is never execution authority."""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import math
import re
import uuid

from .workflow_state import SERIAL_ROUTE

CONTEXT_FORMAT = "devbench.rna-trace-context/1"
EVENT_FORMAT = "devbench.rna-trace-event/1"
MAX_RECEIPT_BYTES = 4096
ROLES = frozenset({"lead", "investigator", "coordinator", "numerical", "review"})
PHASES = frozenset({"workflow", "invocation", "audit", "question", "generate", "resolve", "design", "declare", "run", "verify", "recommend", "investigate", "investigation_decision", "investigation_barrier", "attach", "reassess", "preliminary_explanation", "permission_preflight", "selection", "synthesis", "review", "execution_review", "method_scientific_planning", "method_capability_resolution", "method_trial_design", "method_recommendation", "method_candidates_reassessment", "method_preliminary_explanation"}) | frozenset(SERIAL_ROUTE)
KINDS = frozenset({"reservation", "dispatch", "capture", "completion", "failure", "cancel", "pause", "reconcile", "verification", "gap"})
SOURCE_STATUSES = frozenset({"reserved", "active", "completed", "failed", "cancelled", "uncertain", "incomplete", "abstained", "skipped", "blocked", "ready", "running", "review_required", "paused", "reconciliation_required", "open", "closed", "returned_unvalidated"})
DISPATCH_STATUSES = frozenset({"not_dispatched", "prepared", "attempted", "unknown"})
CAPTURE_STATUSES = frozenset({"unavailable", "partial", "verified", "invalid"})
REASONS = frozenset({"none", "not_dispatched", "not_allocated", "admission_blocked", "capture_missing", "capture_partial", "result_missing", "usage_unavailable", "timing_unavailable", "invalid_clock", "clock_disagreement", "observer_absent", "observer_failure", "event_cap", "byte_cap", "cancelled", "timeout", "tool_rejected", "failed", "uncertain", "incomplete", "paused", "diagnostic_refusal", "source_changed"})
BINDINGS = frozenset({"workflow", "reservation", "invocation", "capture", "result", "usage", "policy", "source", "runtime", "test_family", "shared_result"})
COST_BASES = frozenset({"verified_recorded_charge", "verified_recorded_estimate", "observed_partial_charge", "observed_partial_estimate", "unavailable"})
# ISO-4217 codes, not arbitrary user labels or account identifiers.
CURRENCIES = frozenset("AED AFN ALL AMD ANG AOA ARS AUD AWG AZN BAM BBD BDT BGN BHD BIF BMD BND BOB BOV BRL BSD BTN BWP BYN BZD CAD CDF CHE CHF CHW CLF CLP CNY COP COU CRC CUP CVE CZK DJF DKK DOP DZD EGP ERN ETB EUR FJD FKP GBP GEL GHS GIP GMD GNF GTQ GYD HKD HNL HTG HUF IDR ILS INR IQD IRR ISK JMD JOD JPY KES KGS KHR KMF KPW KRW KWD KYD KZT LAK LBP LKR LRD LSL LYD MAD MDL MGA MKD MMK MNT MOP MRU MUR MVR MWK MXN MXV MYR MZN NAD NGN NIO NOK NPR NZD OMR PAB PEN PGK PHP PKR PLN PYG QAR RON RSD RUB RWF SAR SBD SCR SDG SEK SGD SHP SLE SOS SRD SSP STN SVC SYP SZL THB TJS TMT TND TOP TRY TTD TWD TZS UAH UGX USD USN UYI UYU UYW UZS VED VES VND VUV WST XAF XAG XAU XBA XBB XBC XBD XCD XDR XOF XPD XPF XPT XSU XUA YER ZAR ZMW ZWG".split())


def canonical_bytes(value):
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode("utf-8")
    except (ValueError, TypeError, RecursionError):
        raise ValueError("Trace value is not finite canonical JSON") from None


def trace_digest(value):
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def _closed(value, keys, label):
    if type(value) is not dict or set(value) != set(keys):
        raise ValueError(f"Invalid closed {label} fields")


def _enum(value, allowed, label):
    if type(value) is not str or value not in allowed:
        raise ValueError(f"Invalid {label}")


def _uuid(value, label, nullable=False):
    if nullable and value is None:
        return
    try:
        valid = type(value) is str and str(uuid.UUID(value)) == value
    except (ValueError, AttributeError, TypeError):
        valid = False
    if not valid:
        raise ValueError(f"Invalid {label} UUID")


def _digest(value, label, nullable=False):
    if nullable and value is None:
        return
    if type(value) is not str or re.fullmatch(r"[a-f0-9]{64}", value) is None:
        raise ValueError(f"Invalid {label} digest")


def _integer(value, label, minimum=0, nullable=False):
    if nullable and value is None:
        return
    if type(value) is not int or not minimum <= value <= 2**63 - 1:
        raise ValueError(f"Invalid {label} integer")


def _number(value, label, nullable=False):
    if nullable and value is None:
        return
    try:
        valid = type(value) in (int, float) and math.isfinite(value) and value >= 0
    except (OverflowError, ValueError, TypeError):
        valid = False
    if not valid:
        raise ValueError(f"Invalid {label} measurement")


def _bounded(value):
    if len(canonical_bytes(value)) > MAX_RECEIPT_BYTES:
        raise ValueError("Trace receipt exceeds byte cap")
    return deepcopy(value)


def validate_trace_context(value: dict) -> dict:
    _closed(value, {"format", "trace_id", "span_id", "parent_span_id", "workflow_alias", "attempt", "role", "phase", "invocation_binding_digest", "source_digest", "runtime_digest", "policy_digest"}, "context")
    if value["format"] != CONTEXT_FORMAT:
        raise ValueError("Unsupported trace context format")
    for key in ("trace_id", "span_id", "workflow_alias"):
        _uuid(value[key], key)
    _uuid(value["parent_span_id"], "parent_span_id", True)
    if value["parent_span_id"] == value["span_id"]:
        raise ValueError("Trace context cannot parent itself")
    _integer(value["attempt"], "attempt", 1)
    _enum(value["role"], ROLES, "role")
    _enum(value["phase"], PHASES, "phase")
    for key in ("invocation_binding_digest", "source_digest", "runtime_digest", "policy_digest"):
        _digest(value[key], key)
    return _bounded(value)


def validate_cost_record(value):
    _closed(value, {"value", "currency", "basis", "source_alias"}, "cost")
    _enum(value["basis"], COST_BASES, "cost basis")
    if value["basis"] == "unavailable":
        if any(value[key] is not None for key in ("value", "currency", "source_alias")):
            raise ValueError("Unavailable cost must remain null")
    else:
        _number(value["value"], "cost")
        _enum(value["currency"], CURRENCIES, "currency")
        _uuid(value["source_alias"], "cost source")
    return deepcopy(value)


def validate_trace_event(value: dict) -> dict:
    _closed(value, {"format", "event_id", "trace_id", "span_id", "parent_span_id", "sequence", "attempt", "role", "phase", "kind", "source_status", "dispatch_status", "capture_status", "clock", "bindings", "metrics", "reason_code", "origin"}, "event")
    if value["format"] != EVENT_FORMAT:
        raise ValueError("Unsupported trace event format")
    for key in ("event_id", "trace_id", "span_id"):
        _uuid(value[key], key)
    _uuid(value["parent_span_id"], "parent_span_id", True)
    if value["parent_span_id"] == value["span_id"]:
        raise ValueError("Trace event cannot parent itself")
    _integer(value["sequence"], "sequence")
    _integer(value["attempt"], "attempt", 1)
    for key, allowed in (("role", ROLES), ("phase", PHASES), ("kind", KINDS), ("source_status", SOURCE_STATUSES), ("dispatch_status", DISPATCH_STATUSES), ("capture_status", CAPTURE_STATUSES), ("reason_code", REASONS), ("origin", {"retained_authoritative_evidence", "optional_observer_receipt"})):
        _enum(value[key], allowed, key)
    clock = value["clock"]
    _closed(clock, {"utc", "monotonic_ns", "process_alias", "timing_basis"}, "clock")
    _uuid(clock["process_alias"], "process alias")
    _integer(clock["monotonic_ns"], "monotonic clock", nullable=True)
    _enum(clock["timing_basis"], {"observed", "reconstructed", "display_anchor", "unavailable"}, "timing basis")
    if clock["utc"] is not None:
        try:
            if type(clock["utc"]) is not str or not clock["utc"].endswith("Z"):
                raise ValueError()
            stamp = datetime.fromisoformat(clock["utc"][:-1] + "+00:00")
            if stamp.timestamp() < 0 or stamp.tzinfo != timezone.utc:
                raise ValueError()
        except (ValueError, TypeError, OverflowError):
            raise ValueError("Invalid trace UTC clock") from None
    if clock["timing_basis"] == "unavailable" and (clock["utc"] is not None or clock["monotonic_ns"] is not None):
        raise ValueError("Unavailable clock must remain null")
    if clock["timing_basis"] == "observed" and clock["utc"] is None and clock["monotonic_ns"] is None:
        raise ValueError("Observed clock requires actual measurement")
    bindings = value["bindings"]
    if type(bindings) is not dict or not set(bindings) <= BINDINGS or "workflow" not in bindings:
        raise ValueError("Invalid local binding fields")
    for key, binding in bindings.items():
        _digest(binding, key)
    metrics = value["metrics"]
    _closed(metrics, {"input_tokens", "output_tokens", "total_tokens", "elapsed_seconds", "peak_rss_mb", "provider_cost", "usage_basis"}, "metrics")
    for key in ("input_tokens", "output_tokens", "total_tokens"):
        _integer(metrics[key], key, nullable=True)
    for key in ("elapsed_seconds", "peak_rss_mb"):
        _number(metrics[key], key, nullable=True)
    validate_cost_record(metrics["provider_cost"])
    _enum(metrics["usage_basis"], {"verified_complete", "observed_partial", "unavailable"}, "usage basis")
    if metrics["usage_basis"] == "unavailable" and any(metrics[key] is not None for key in ("input_tokens", "output_tokens", "total_tokens")):
        raise ValueError("Unavailable token usage must remain null")
    if metrics["usage_basis"] == "observed_partial" and metrics["total_tokens"] is not None:
        raise ValueError("Partial usage cannot claim an exact total")
    if metrics["usage_basis"] == "verified_complete" and any(metrics[key] is None for key in ("input_tokens", "output_tokens", "total_tokens")):
        raise ValueError("Complete usage requires all token counts")
    if metrics["total_tokens"] is not None and metrics["total_tokens"] != metrics["input_tokens"] + metrics["output_tokens"]:
        raise ValueError("Token counts disagree")
    if metrics["elapsed_seconds"] is not None and clock["timing_basis"] != "observed":
        raise ValueError("Display/reconstructed clocks cannot establish duration")
    return _bounded(value)
