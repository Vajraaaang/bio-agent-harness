"""Optional monotonic deadline shared by a daemon request and its WAL operations."""

from __future__ import annotations

from contextvars import ContextVar
from time import monotonic

deadline = ContextVar("devbench_dispatch_deadline", default=float("inf"))


def remaining(maximum):
    left = deadline.get() - monotonic()
    if left <= 0:
        from .workflow_operations import OperationBlocked

        raise OperationBlocked("resource_quota_exceeded")
    return min(maximum, left)


def check_deadline():
    remaining(0)
