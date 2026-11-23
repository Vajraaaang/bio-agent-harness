"""Offline run lifecycle and watchdog contracts with no runtime transport."""

import json
import re
from dataclasses import dataclass, replace
from datetime import datetime
from hashlib import sha256
from threading import Lock

from devbench.engine.provenance import canonical_json


_JOB_ID = re.compile(r"[0-9a-f]{32}")
_UTC_SECONDS = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z")
_FORWARD = {
    "allocating": "ready",
    "ready": "transferring",
    "transferring": "running",
    "running": "exited",
    "exited": "staged",
}
_ACTIVE = {"reserved", "allocating", "ready", "transferring", "running"}
_CLEANUP = {"cancel_requested", "failed", "exited", "staged"}


def watchdog_command(job_id, deadline_at_utc, *, now, limit_seconds):
    """Build fixed argv from the verified manifest's job and UTC deadline."""
    if not isinstance(job_id, str) or not _JOB_ID.fullmatch(job_id):
        raise ValueError("INVALID_WATCHDOG_JOB_ID")
    if (
        not isinstance(deadline_at_utc, str)
        or not _UTC_SECONDS.fullmatch(deadline_at_utc)
        or not isinstance(now, datetime)
        or now.utcoffset() is None
        or type(limit_seconds) is not int
        or not 1 <= limit_seconds <= 86_400
    ):
        raise ValueError("INVALID_WATCHDOG_DEADLINE")
    try:
        deadline = datetime.fromisoformat(deadline_at_utc)
    except ValueError:
        raise ValueError("INVALID_WATCHDOG_DEADLINE") from None
    if not 0 < (deadline - now).total_seconds() <= limit_seconds:
        raise ValueError("INVALID_WATCHDOG_DEADLINE")
    return (
        "/opt/devbench/venv/bin/python", "-I", "-m", "devbench_colab_watchdog",
        "--job-id", job_id, "--deadline-utc", deadline_at_utc,
    )


def run_watchdog(*, remaining_seconds, monotonic, sleep, terminate_group, unassign):
    """Model guest deadline behavior with supplied clock and stop capabilities.

    The caller binds termination to the owned group and VM in its simulation.
    The watchdog attempts unassignment even when group termination fails.
    """
    if (
        type(remaining_seconds) not in {int, float}
        or not 0 <= remaining_seconds <= 86_400
    ):
        raise ValueError("INVALID_WATCHDOG_DEADLINE")
    deadline = monotonic() + remaining_seconds
    delay = remaining_seconds
    while delay > 0:
        sleep(delay)
        delay = deadline - monotonic()
    try:
        terminate_group()
    finally:
        unassign()


@dataclass(frozen=True, slots=True)
class RunAttempt:
    job_id: str
    manifest_sha256: str
    backend: str
    phase: str
    connection_generation: int | None
    outcome: str | None = None
    allocation_started: bool = False


class OfflineRunLedger:
    """In-memory model of the reservation and connection serialization point.

    The caller supplies a verified manifest and a synthetic lease assertion.
    This object can only record simulated transitions. It has no allocator,
    provider client, durable store, or worker invocation.
    """

    def __init__(self, account_id=None, connection_generation=0):
        if type(connection_generation) is not int or connection_generation < 0:
            raise ValueError("INVALID_CONNECTION_GENERATION")
        self._account_id = account_id
        self._generation = connection_generation
        self._runs = {}
        self._lock = Lock()

    @property
    def connection_generation(self):
        with self._lock:
            return self._generation

    def get(self, job_id):
        with self._lock:
            return self._runs[job_id]

    def reserve(self, verified_manifest, *, simulated_provider_lease=False):
        """Consume a verified job ID only after backend admission succeeds."""
        manifest_bytes = canonical_json(verified_manifest)
        snapshot = json.loads(manifest_bytes)
        job_id = snapshot["job_id"]
        backend = snapshot["backend"]
        binding = snapshot["account_binding"]
        if not isinstance(job_id, str) or not _JOB_ID.fullmatch(job_id):
            raise ValueError("INVALID_JOB_ID")
        with self._lock:
            if job_id in self._runs:
                raise PermissionError("JOB_ALREADY_RESERVED")
            generation = None
            if backend == "colab":
                if self._account_id is None or binding != {
                    "account_id": self._account_id,
                    "connection_generation": self._generation,
                }:
                    raise PermissionError("STALE_OR_WRONG_ACCOUNT")
                if simulated_provider_lease is not True:
                    raise PermissionError("HARD_RUNTIME_DEADLINE_UNAVAILABLE")
                generation = self._generation
            elif backend != "local_supervised" or binding is not None:
                raise PermissionError("INVALID_BACKEND_BINDING")
            record = RunAttempt(job_id, sha256(manifest_bytes).hexdigest(), backend, "reserved", generation)
            self._runs[job_id] = record
            return record

    def reset_connection(self, account_id):
        """Invalidate old Colab runs in the same critical section as dispatch."""
        with self._lock:
            self._generation += 1
            self._account_id = account_id
            for job_id, record in self._runs.items():
                if record.backend != "colab":
                    continue
                if record.phase in _ACTIVE:
                    self._runs[job_id] = replace(
                        record, phase="cancel_requested", outcome="cancelled",
                    )
                elif record.phase in {"exited", "staged"}:
                    self._runs[job_id] = replace(record, phase="cleanup_pending")
            return self._generation

    def advance(
        self, job_id, phase, *, runtime_verified=False, watchdog_armed=False,
        exit_succeeded=None, absence_confirmed=False,
    ):
        """Record an admissible transition and return its immutable snapshot."""
        with self._lock:
            record = self._runs[job_id]
            outcome = record.outcome
            if phase == "cancel_requested" and record.phase in _ACTIVE:
                outcome = "cancelled"
            elif phase == "failed" and record.phase in _ACTIVE:
                outcome = "failed"
            elif phase == "cleanup_pending" and record.phase in _CLEANUP:
                pass
            elif phase == "closed" and record.phase in {"staged", "cleanup_pending"}:
                if record.allocation_started and absence_confirmed is not True:
                    raise PermissionError("CLEANUP_UNCONFIRMED")
            else:
                outcome = self._validate_forward(
                    record, phase, runtime_verified, watchdog_armed, exit_succeeded,
                )
            result = replace(
                record, phase=phase, outcome=outcome,
                allocation_started=record.allocation_started or phase == "allocating",
            )
            self._runs[job_id] = result
            return result

    def _validate_forward(self, record, phase, runtime_verified, watchdog_armed, exit_succeeded):
        if record.backend == "colab" and record.connection_generation != self._generation:
            raise PermissionError("STALE_GENERATION")
        if record.phase == "reserved":
            expected = "allocating" if record.backend == "colab" else "running"
        else:
            expected = _FORWARD.get(record.phase)
        if expected is None or phase != expected:
            raise ValueError("INVALID_RUN_TRANSITION")
        if phase == "ready" and (runtime_verified is not True or watchdog_armed is not True):
            raise PermissionError("RUNTIME_OR_WATCHDOG_UNVERIFIED")
        if phase == "exited":
            if type(exit_succeeded) is not bool:
                raise ValueError("MISSING_EXIT_OUTCOME")
            return "succeeded" if exit_succeeded else "failed"
        if phase == "staged" and record.outcome != "succeeded":
            raise PermissionError("RESULT_NOT_STAGEABLE")
        return record.outcome
