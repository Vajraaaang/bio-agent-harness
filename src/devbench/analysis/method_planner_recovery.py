"""Per-plan operation leases and conservative recovery of orphaned planning.

Lease files remain in place: unlinking a released lease could let two processes
lock different inodes for the same plan. Recovery records interruption only; it
never retries a model, starts computation, or changes approval scope.
"""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
import errno
import fcntl
from functools import wraps
import os
import re
import stat

from .method_executor import _path


_INTERRUPTED = "Coordinator exited before this planner operation completed; no computation was restarted"


class _ActivePlan(ValueError):
    """A different operation still holds this plan's lease."""


@contextmanager
def _plan_lease(planner, session_id):
    # A supplied identifier must resolve to an existing plan before becoming a
    # filesystem component. UUID validation also rejects traversal and aliases.
    state = planner.get(session_id)
    if (not isinstance(session_id, str) or re.fullmatch(r"[0-9a-f]{32}", session_id) is None
            or state.get("session_id") != session_id):
        raise ValueError("Invalid registered planner session identity")
    directory = _path(planner.methods.root / "planner-leases")
    directory.mkdir(mode=0o700, exist_ok=True)
    path = _path(directory / session_id)
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(descriptor, "r+b") as lease:
        if not stat.S_ISREG(os.fstat(lease.fileno()).st_mode):
            raise ValueError("Planner lease must be a regular file")
        try:
            fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            if error.errno in {errno.EACCES, errno.EAGAIN, errno.EWOULDBLOCK}:
                raise _ActivePlan("Another planner operation is in progress") from error
            raise
        try:
            yield
        finally:
            fcntl.flock(lease, fcntl.LOCK_UN)


def _recover_locked(planner, session_id):
    method_runs = planner.methods.reconcile()
    original = planner.get(session_id)
    recovered = deepcopy(original)
    if recovered.get("selection_in_progress") is not None:
        recovered["selection_in_progress"] = None
    history = recovered.get("selection_history", [])
    if history and history[-1].get("status") == "running":
        history[-1].update(status="interrupted", error=_INTERRUPTED)
    results = recovered["results"]
    recorded = {(row["experiment_id"], row["candidate"]) for row in results}
    for experiment in recovered["experiments"]:
        if experiment.get("status") != "running":
            continue
        # Preserve every frozen declaration field and its original identity.
        experiment["status"] = "interrupted"
        for candidate in experiment["candidates"]:
            key = experiment["id"], candidate
            if key in recorded:
                continue
            results.append({"experiment_id": experiment["id"], "candidate": candidate, "status": "failed",
                            "metric_values": {}, "warnings": [], "failures": [_INTERRUPTED], "artifacts": [],
                            "provenance": {"declaration_sha256": experiment["declaration_sha256"]}})
            recorded.add(key)
    full = recovered.get("full_execution")
    if full and full.get("status") == "running":
        full.update(status="interrupted", error=_INTERRUPTED)
    changed = recovered != original
    if changed:
        planner._save(original, recovered, actor="restart-reconciliation", action="planner_interrupted_operations_recovered")
    return {"session_id": session_id, "recovered": changed or bool(method_runs),
            "active_operation": False, "recovered_method_runs": method_runs}


def recover_plan(planner, session_id):
    """Recover an orphaned plan, or report an active lease without mutation."""
    try:
        with _plan_lease(planner, session_id):
            return _recover_locked(planner, session_id)
    except _ActivePlan:
        return {"session_id": session_id, "recovered": False,
                "active_operation": True, "recovered_method_runs": []}


def plan_operation(function):
    """Serialize a plan operation and recover only after acquiring its lease."""
    @wraps(function)
    def protected(planner, session_id, *args, **kwargs):
        with _plan_lease(planner, session_id):
            _recover_locked(planner, session_id)
            return function(planner, session_id, *args, **kwargs)
    return protected
