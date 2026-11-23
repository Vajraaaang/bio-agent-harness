"""Closed accounting contracts for coordinator-managed generation jobs.

A job is a durable launch intent. It is neither an internal provider request nor
a token, subscription-credit or monetary allocation. Native usage is optional
evidence and never supplies authority to launch another job.
"""

from __future__ import annotations

import json
import math


JOB_POLICY_FORMAT = "devbench.model-job-budget-policy/1"
JOB_CAPTURE_FORMAT = "devbench.codex-job-capture/1"
_IDENTITY_FIELDS = ("session_id", "invocation_id", "backend_digest", "runtime_digest",
                    "prompt_digest", "schema_digest", "context_digest")


def _integer(value, name, *, minimum=0, maximum=1_000_000_000):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"Invalid generation job {name}")
    return value


def _seconds(value, name):
    if type(value) not in (int, float) or not math.isfinite(value) or not 0 < value <= 86400:
        raise ValueError(f"Invalid generation job {name}")
    return value


def _hash(value):
    if type(value) is not str or len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise ValueError("Generation job identity must be a SHA-256 digest")


def validate_job_policy(value):
    fields = {"format", "kind", "max_jobs", "max_concurrent_jobs", "max_total_seconds",
              "max_context_bytes", "max_output_bytes", "max_children", "reconciliation"}
    if type(value) is not dict or set(value) != fields or value["format"] != JOB_POLICY_FORMAT:
        raise ValueError("Invalid generation job policy schema; internal request, token, credit and spend caps are unsupported")
    if value["kind"] != "generation_jobs":
        raise ValueError("Unknown generation job policy")
    _integer(value["max_jobs"], "max_jobs", minimum=1, maximum=1024)
    if type(value["max_concurrent_jobs"]) is not int or value["max_concurrent_jobs"] != 1:
        raise ValueError("Generation job concurrency must be one")
    if type(value["max_children"]) is not int or value["max_children"] != 0:
        raise ValueError("Generation jobs do not admit children")
    _seconds(value["max_total_seconds"], "max_total_seconds")
    _integer(value["max_context_bytes"], "max_context_bytes", minimum=1, maximum=1_000_000)
    _integer(value["max_output_bytes"], "max_output_bytes", minimum=1, maximum=16_000_000)
    reserve = value["reconciliation"]
    if type(reserve) is not dict or set(reserve) != {"jobs", "seconds", "context_bytes"}:
        raise ValueError("Invalid generation job parent reservation")
    _integer(reserve["jobs"], "reconciliation jobs", minimum=1)
    _seconds(reserve["seconds"], "reconciliation seconds")
    _integer(reserve["context_bytes"], "reconciliation context_bytes", minimum=1)
    if any(reserve[key] > value[limit] for key, limit in (
            ("jobs", "max_jobs"), ("seconds", "max_total_seconds"), ("context_bytes", "max_context_bytes"))):
        raise ValueError("Generation job parent reservation exceeds campaign")
    return json.loads(json.dumps(value, allow_nan=False))


def validate_job_limits(value):
    if type(value) is not dict or set(value) != {"jobs", "seconds", "context_bytes", "output_bytes"}:
        raise ValueError("Invalid generation job limits schema")
    if type(value["jobs"]) is not int or value["jobs"] != 1:
        raise ValueError("Each reservation admits exactly one generation job")
    _seconds(value["seconds"], "seconds")
    _integer(value["context_bytes"], "context_bytes", minimum=1, maximum=1_000_000)
    _integer(value["output_bytes"], "output_bytes", minimum=1, maximum=16_000_000)
    return dict(value)


def validate_job_capture(value, capture, reservation):
    required = {"format", "reservation_ref", "request_digest", "capture_ref", "launch_intent",
                "launch_confirmed", "status", "process_reaped", "job_count", "elapsed_seconds",
                "native_session_ids", "provenance"}
    if (type(value) is not dict or not required <= set(value)
            or set(value) - required - {"usage", "response_sha256"}
            or value["format"] != JOB_CAPTURE_FORMAT):
        raise ValueError("Unsupported generation job capture schema")
    if type(capture) is not dict or set(capture) != {"artifact_id", "sha256", "artifact_type", "size_bytes"}:
        raise ValueError("An immutable generation job capture reference is required")
    for key in ("artifact_id", "artifact_type"):
        item = capture[key]
        if type(item) is not str or not item.strip() or len(item.encode("utf-8")) > 256:
            raise ValueError("Invalid generation job capture reference")
    _hash(capture["sha256"])
    _integer(capture["size_bytes"], "capture size", minimum=1, maximum=16_000_000)
    if (value["reservation_ref"] != reservation.ref or value["request_digest"] != reservation.request_digest
            or value["capture_ref"] != capture):
        raise ValueError("Generation job capture does not match its reservation")
    provenance = value["provenance"]
    if (type(provenance) is not dict or any(provenance.get(key) != reservation.request[key] for key in _IDENTITY_FIELDS)):
        raise ValueError("Generation job provenance differs from its exact reserved invocation")
    if value["launch_intent"] is not True or type(value["launch_confirmed"]) is not bool or type(value["process_reaped"]) is not bool:
        raise ValueError("Invalid generation job launch lifecycle")
    if value["status"] not in ("completed", "failed", "not_started", "uncertain"):
        raise ValueError("Invalid generation job terminal status")
    if type(value["job_count"]) is not int or value["job_count"] != 1:
        raise ValueError("A launch intent always consumes one generation job")
    elapsed = value["elapsed_seconds"]
    if type(elapsed) not in (int, float) or not math.isfinite(elapsed) or not 0 <= elapsed <= 86400:
        raise ValueError("Invalid generation job elapsed time")
    sessions = value["native_session_ids"]
    if (type(sessions) is not list or len(sessions) > 1024
            or any(type(item) is not str or not item or len(item.encode("utf-8")) > 256 for item in sessions)
            or len(sessions) != len(set(sessions)) or (not value["launch_confirmed"] and sessions)):
        raise ValueError("Invalid generation job native session identities")
    if value["status"] == "completed" and (not value["launch_confirmed"] or not sessions):
        raise ValueError("A completed generation job requires a confirmed native session")
    if value["status"] == "not_started" and (value["launch_confirmed"] or not value["process_reaped"]):
        raise ValueError("A not-started generation job cannot retain a live process")
    if "usage" in value and type(value["usage"]) is not dict:
        raise ValueError("Optional generation job usage must retain an observed object")
    if "response_sha256" in value:
        _hash(value["response_sha256"])
    for key in ("context_bytes", "output_bytes"):
        if key in provenance:
            _integer(provenance[key], key)
    return json.loads(json.dumps(value, allow_nan=False))
