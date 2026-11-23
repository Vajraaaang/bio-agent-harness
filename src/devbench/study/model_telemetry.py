"""Private, bounded capture of Codex's native runtime identity telemetry.

Only identifiers and completion metadata survive parsing. This establishes a
CLI-reported model identity, not a provider-signed model/weights attestation.
"""
import gzip
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import re
import secrets
import threading

from .contracts import canonical_json_bytes
from .evidence import decode_json
from .permissions import _toml


TOKEN_ENV = "DEVBENCH_MODEL_CAPTURE_TOKEN"
COMPLETION_EVENTS = {"codex.sse_event", "codex.websocket_event", "codex.websocket.event"}
MAX_BYTES = 2_000_000


def normalize_usage(usage):
    """Keep cached input and reasoning output inside their reported totals.

    Missing/invalid accounting stays unknown. This is posthoc normalization,
    never a guaranteed ceiling or independent evidence that the input is real.
    """
    source = usage if isinstance(usage, dict) else {}
    names = {"input_tokens", "output_tokens", "cached_input_tokens", "reasoning_tokens",
             "reasoning_output_tokens"}
    native = {key: value for key, value in source.items() if key in names
              and (type(value) in {int, bool} and abs(value) <= 1_000_000_000
                   or isinstance(value, str) and len(value) <= 64)}
    if source.get("exact_accounting_available") is False:
        native["exact_accounting_available"] = False
    markers = []
    for item in source.get("invalid_source_fields", []) if isinstance(source.get("invalid_source_fields"), list) else ():
        if (isinstance(item, dict) and set(item) == {"field", "value_type", "sha256"}
                and item["field"] in names and isinstance(item["value_type"], str)
                and len(item["value_type"]) <= 64 and isinstance(item["sha256"], str)
                and re.fullmatch(r"[a-f0-9]{64}", item["sha256"])):
            markers.append(dict(item))
        if len(markers) >= 5:
            break
    if "phase_usage" in source:
        phases = source["phase_usage"]
        if (not isinstance(phases, list) or not 1 <= len(phases) <= 64
                or any(not isinstance(phase, dict) or "phase_usage" in phase for phase in phases)):
            raise ValueError("Native phase usage must be a bounded flat phase list")
        normalized = [normalize_usage(phase) for phase in phases]
        phase_native = [{key: value for key, value in phase["native_usage"].items()
                         if key != "observed_token_lower_bound"} for phase in normalized]
        lower_bound = sum(phase["native_usage"]["observed_token_lower_bound"] for phase in normalized)
        phases_known = all(phase["normalized_total_tokens"] is not None for phase in normalized)
        totals = {}
        if phases_known:
            for key in ("input_tokens", "output_tokens", "cached_input_tokens", "reasoning_tokens"):
                values = [phase[key] for phase in normalized]
                if all(value is not None for value in values):
                    totals[key] = sum(values)
        # Validate every outer reported field against the phase totals through
        # the same flat normalizer. An early aggregate return must not discard
        # invalid null/float/oversize values or a conflicting reasoning alias.
        invalid_outer_fields = {item["field"] for item in markers}
        outer = {**{key: value for key, value in totals.items() if key not in invalid_outer_fields},
                 **{key: source[key] for key in names if key in source}}
        if source.get("exact_accounting_available") is False:
            outer["exact_accounting_available"] = False
        if markers:
            outer["invalid_source_fields"] = markers
        outer_normalized = normalize_usage(outer)
        mismatch = any(key in source and source[key] != value for key, value in totals.items())
        known = phases_known and outer_normalized["normalized_total_tokens"] is not None and not mismatch
        canonical = {**outer_normalized["native_usage"], "phase_usage": phase_native,
                     "observed_token_lower_bound": lower_bound}
        if not known:
            canonical["exact_accounting_available"] = False
        return {"normalization": "input_output_subsets/1", "native_usage": canonical,
            "normalized_total_tokens": totals["input_tokens"] + totals["output_tokens"] if known else None,
            **{key: totals.get(key) if known else None for key in (
                "input_tokens", "output_tokens", "cached_input_tokens", "reasoning_tokens")}}

    def count(key):
        value = native.get(key)
        return value if type(value) is int and 0 <= value <= 1_000_000_000 else None

    input_tokens, output_tokens = count("input_tokens"), count("output_tokens")
    native["observed_token_lower_bound"] = (input_tokens or 0) + (output_tokens or 0)
    cached = count("cached_input_tokens")
    reasoning = count("reasoning_tokens") if "reasoning_tokens" in native else count("reasoning_output_tokens")
    invalid_source = any(key in source and (type(source[key]) is not int or not 0 <= source[key] <= 1_000_000_000)
                         for key in names)
    if invalid_source:
        by_field = {item["field"]: item for item in markers}
        for key in sorted(names):
            if key not in source or (type(source[key]) is int and 0 <= source[key] <= 1_000_000_000):
                continue
            value = source[key]
            value_type = type(value).__name__[:64]
            try:
                encoded = json.dumps(value, allow_nan=False, separators=(",", ":")).encode("utf-8")
            except (ValueError, TypeError):
                encoded = ("unserializable_type:" + value_type).encode("utf-8")
            by_field[key] = {"field": key, "value_type": value_type,
                             "sha256": hashlib.sha256(encoded).hexdigest()}
        markers = [by_field[key] for key in sorted(by_field)]
    invalid_subset = ((cached is not None and (input_tokens is None or cached > input_tokens))
                      or any(count(key) is not None and (output_tokens is None or count(key) > output_tokens)
                             for key in ("reasoning_tokens", "reasoning_output_tokens"))
                      or ("reasoning_tokens" in native and "reasoning_output_tokens" in native
                          and native["reasoning_tokens"] != native["reasoning_output_tokens"]))
    invalid = invalid_source or invalid_subset or source.get("exact_accounting_available") is False
    if invalid:
        native["exact_accounting_available"] = False
    if markers:
        native["invalid_source_fields"] = markers
    return {"normalization": "input_output_subsets/1", "native_usage": native,
            "normalized_total_tokens": input_tokens + output_tokens
                if input_tokens is not None and output_tokens is not None and not invalid else None,
            "input_tokens": input_tokens if not invalid else None, "output_tokens": output_tokens if not invalid else None,
            "cached_input_tokens": cached if not invalid else None,
            "reasoning_tokens": reasoning if not invalid else None}


def _attributes(values):
    result = {}
    for item in values or []:
        key, value = item.get("key"), item.get("value") or {}
        if key == "error.message" and value:
            result["native_error"] = True
            continue
        if key not in {"model", "model_slug", "conversation.id", "conversation_id", "session.id", "session_id",
                       "thread_id", "event.name", "event_name", "event.kind", "event_kind", "kind", "event_type", "success",
                       "input_token_count", "output_token_count", "input_tokens", "output_tokens", "response_id",
                       "model_reasoning_effort", "service_tier"}:
            continue
        for name in ("stringValue", "string_value", "boolValue", "bool_value", "intValue", "int_value"):
            if name in value:
                result[key] = value[name]
    return result


def extract_events(payload):
    if not isinstance(payload, dict):
        raise ValueError("OTLP payload must be an object")
    events = []
    for resource in payload.get("resourceLogs", payload.get("resource_logs", [])):
        common = _attributes((resource.get("resource") or {}).get("attributes"))
        for scope in resource.get("scopeLogs", resource.get("scope_logs", [])):
            for record in scope.get("logRecords", scope.get("log_records", [])):
                fields = {**common, **_attributes(record.get("attributes"))}
                name = fields.get("event.name", fields.get("event_name", (record.get("body") or {}).get("stringValue")))
                if name not in COMPLETION_EVENTS | {"codex.conversation_starts"}:
                    continue
                model = fields.get("model", fields.get("model_slug"))
                session = next((fields[key] for key in ("conversation.id", "conversation_id", "session.id", "session_id", "thread_id") if key in fields), None)
                if (not isinstance(model, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_./:-]{0,299}", model)
                        or not isinstance(session, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", session)):
                    continue
                completed = any(fields.get(key) == "response.completed" for key in ("event.kind", "event_kind", "kind", "event_type"))
                usage = {}
                for token_key in ("input", "output"):
                    value = fields.get(token_key + "_token_count", fields.get(token_key + "_tokens"))
                    if type(value) is int or (isinstance(value, str) and value.isdecimal()):
                        value = int(value)
                        if 0 <= value <= 1_000_000_000:
                            usage[token_key + "_tokens"] = value
                success = fields.get("success")
                if isinstance(success, str) and success in {"true", "false"}:
                    success = success == "true"
                if fields.get("native_error") is True:
                    success = False
                request_metadata = {}
                for key in ("model_reasoning_effort", "service_tier"):
                    value = fields.get(key)
                    if isinstance(value, str) and re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", value):
                        request_metadata[key] = value
                events.append({"event": name, "conversation_id": session, "model": model,
                               "completion": completed, "success": success if type(success) is bool else None,
                               "usage": usage, "request_metadata": request_metadata})
    return events


def completion_model(events, session_id):
    matching = [event for event in events if event.get("conversation_id") == session_id
                and event.get("event") in COMPLETION_EVENTS and event.get("completion") is True]
    models = {event["model"] for event in matching}
    return next(iter(models)) if len(models) == 1 and all(e.get("success") is not False for e in matching) else None


def correlate_usage(completions, expected_usage):
    """Correlate full inference usage without guessing an arbitrary subset.

    Native Codex emits a generate=false websocket prewarm completion before
    inference. core/src/client.rs leaves request metadata unset for that path;
    with_inference_request attaches effort/service tier only to inference.
    All events remain in the receipt, and all completion models must agree.
    Unknown metadata, nonzero prewarm output, or a late unmarked event cannot
    be removed to make accounting match.
    """
    keys = ("input_tokens", "output_tokens")
    failure = {"matches": False, "method": "unmatched", "excluded_prewarm_events": 0,
               "inference_events": 0}
    if (not completions or not isinstance(expected_usage, dict)
            or any(type(expected_usage.get(key)) is not int or expected_usage[key] < 0 for key in keys)
            or any(type(event.get("usage", {}).get(key)) is not int or event["usage"][key] < 0
                   for event in completions for key in keys)):
        return failure

    def matches(events):
        return all(sum(event["usage"][key] for event in events) == expected_usage[key] for key in keys)

    if matches(completions):
        return {"matches": True, "method": "all_native_completion_events",
                "excluded_prewarm_events": 0, "inference_events": len(completions)}
    if (len(completions) > 1 and not completions[0].get("request_metadata")
            and completions[0]["usage"]["output_tokens"] == 0
            and all(event.get("request_metadata") for event in completions[1:])
            and matches(completions[1:])):
        return {"matches": True, "method": "inference_request_metadata_after_zero_generation_prewarm",
                "excluded_prewarm_events": 1, "inference_events": len(completions) - 1}
    return failure


class NativeModelCapture:
    def __init__(self, output_path):
        self.output_path = Path(output_path)
        if (not self.output_path.is_absolute() or self.output_path.exists()
                or any(path.is_symlink() for path in (self.output_path, *self.output_path.parents))):
            raise ValueError("Native identity output must be fresh, absolute and nonsymlinked")
        self.capability = secrets.token_hex(24)
        self.events, self.request_hashes, self.errors = [], [], []
        self.rejected_requests, self.total_bytes = 0, 0
        self.rejection_reasons = {"path": 0, "missing_capability": 0, "literal_environment_template": 0, "capability_mismatch": 0}
        self.lock = threading.Lock()
        self.receipt = None
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                self.connection.settimeout(2)
                received = self.headers.get("x-devbench-capture", "")
                if self.path != "/v1/logs" or not hmac.compare_digest(received, owner.capability):
                    with owner.lock:
                        owner.rejected_requests += 1
                        reason = ("path" if self.path != "/v1/logs" else "missing_capability" if not received
                                  else "literal_environment_template" if received == "${" + TOKEN_ENV + "}"
                                  else "capability_mismatch")
                        owner.rejection_reasons[reason] += 1
                    self.send_error(403)
                    return
                try:
                    size = int(self.headers.get("Content-Length", "0"))
                    if not 0 < size <= MAX_BYTES:
                        raise ValueError("Native telemetry request exceeded its bound")
                    raw = self.rfile.read(size)
                    if len(raw) != size:
                        raise ValueError("Incomplete native telemetry request")
                    encoding = self.headers.get("Content-Encoding", "identity").lower()
                    if encoding == "gzip":
                        with gzip.GzipFile(fileobj=io.BytesIO(raw)) as stream:
                            decoded = stream.read(MAX_BYTES + 1)
                    elif encoding == "identity":
                        decoded = raw
                    else:
                        raise ValueError("Unsupported telemetry encoding")
                    if len(decoded) > MAX_BYTES:
                        raise ValueError("Native telemetry decoded byte limit exceeded")
                    events = extract_events(decode_json(decoded))
                    with owner.lock:
                        owner.total_bytes += len(decoded)
                        if owner.total_bytes > 16_000_000 or len(owner.events) + len(events) > 4096 or len(owner.request_hashes) >= 128:
                            raise ValueError("Native telemetry aggregate limit exceeded")
                        owner.events.extend(events)
                        owner.request_hashes.append(hashlib.sha256(raw).hexdigest())
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(b"{}")
                except Exception as error:
                    with owner.lock:
                        owner.errors.append(type(error).__name__)
                    self.send_error(400)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.endpoint = f"http://127.0.0.1:{self.server.server_port}/v1/logs"
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": .05}, daemon=True)
        self.thread.start()

    def arguments(self):
        settings = {"environment": "devbench-private-identity", "log_user_prompt": False,
                    "exporter": {"otlp-http": {"endpoint": self.endpoint, "protocol": "json",
                        "headers": {"x-devbench-capture": self.capability}}},
                    "trace_exporter": "none", "metrics_exporter": "none"}
        return ["-c", "otel=" + _toml(settings)]

    def close(self, session_id, expected_usage=None):
        if self.receipt is not None:
            return self.receipt
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        with self.lock:
            matching = [e for e in self.events if e["conversation_id"] == session_id]
            completions = [e for e in matching if e["event"] in COMPLETION_EVENTS and e["completion"]]
            usage_correlation = correlate_usage(completions, expected_usage)
            complete = not self.errors and not self.thread.is_alive()
            self.receipt = {"record_format": "devbench.native-model-identity/2", "conversation_id": session_id,
                "capture_complete": complete, "completion_model": completion_model(matching, session_id) if complete else None,
                "usage_matches_cli": usage_correlation["matches"], "usage_correlation": usage_correlation,
                "events": matching, "request_sha256": self.request_hashes, "capture_errors": self.errors,
                "rejected_requests": self.rejected_requests, "decoded_bytes": self.total_bytes,
                "rejection_reasons": self.rejection_reasons,
                "privacy": "identifiers_and_completion_metadata_only; prompts_user_email_and_other_fields_discarded",
                "scope": "native_CLI_reported_completion_model; not_provider_signed_weights_attestation"}
        descriptor = os.open(self.output_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(canonical_json_bytes(self.receipt) + b"\n")
        return self.receipt
