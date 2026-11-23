"""Read the current durable permission before entering a trusted selector.

These local records bind an operator permission to one selection attempt. They
are not authenticated approval, scientific acceptance, or an OS sandbox. The
offline names identify trusted coordinator fixtures, never participant plugins.
"""
from pathlib import Path

from devbench.study.evidence import _read, decode_json
from .checkpoints import digest


OFFLINE_SELECTOR_BACKENDS = frozenset({
    "offline_scripted_fixture", "offline_planning_fixture", "recorded_fixture",
    "recorded_planning_answer", "recorded_scientific_review_fixture",
    "recorded_scientific_review_no_model", "recorded_v2_synthetic_control",
})


def is_offline_selector(configuration):
    return isinstance(configuration, dict) and configuration.get("backend") in OFFLINE_SELECTOR_BACKENDS


def reservation_binding(configuration, attempt):
    """Bind authority to the exact frozen selector and public selection input."""
    return {"selector_sha256": digest(configuration), "attempt_sha256": digest(attempt)}


def read_model_authority(path, *, configuration, attempt, maximum_authority=False):
    """Return read-back authority, bounded by the current explicit permission.

    Missing, corrupt, non-regular, or mismatched records deny entry, including
    for offline fixtures. A recorded numerical result cannot create authority.
    Unknown selector configurations are potentially live and fail closed.
    """
    try:
        record = decode_json(_read(Path(path), 65_536))
        if not isinstance(record, dict) or type(record.get("model_execution_authorized")) is not bool:
            raise ValueError("Model authority must be an exact boolean")
        if any(record.get(key) != value for key, value in reservation_binding(configuration, attempt).items()):
            raise ValueError("Model authority selection binding changed")
    except (OSError, TypeError, ValueError) as error:
        raise PermissionError("Invalid model authority reservation") from error
    authorized = record["model_execution_authorized"]
    if authorized and maximum_authority is not True:
        raise PermissionError("Persisted model authorization cannot exceed current explicit permission")
    if is_offline_selector(configuration):
        return False
    if authorized is not True:
        raise PermissionError("Selector requires current persisted model authorization")
    return authorized
