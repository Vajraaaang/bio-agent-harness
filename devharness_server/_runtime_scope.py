"""Deployment capabilities for the isolated participant SDK."""

from pathlib import Path

PARTICIPANT_MARKER = ".participant-runtime"
PARTICIPANT_RUNTIME = Path(__file__).with_name(PARTICIPANT_MARKER).is_file()


def require_coordinator_runtime():
    """Reject coordinator-owned methods in a staged SDK before reading inputs."""
    if PARTICIPANT_RUNTIME:
        raise PermissionError("This tool requires the governed coordinator runtime")
