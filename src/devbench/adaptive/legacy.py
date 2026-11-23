"""Read-only mapping of legacy analysis records into the new state model."""

from __future__ import annotations

import hashlib
from pathlib import Path

from devbench.adaptive.models import LegacyImport

NEW_SEMANTICS = (
    "evidence_use_scope",
    "biological_unit_context",
    "qualification_regime",
    "selection_history",
    "claim_eligibility",
    "data_use_plan",
    "operation_idempotency",
)


class LegacyRejected(ValueError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def import_legacy_source(raw: dict) -> LegacyImport:
    """Map one identified legacy object without granting qualification.

    ``source_path`` must be an absolute path to the bytes named by
    ``source_sha256``. Fields the legacy object does not supply stay missing.
    """
    if raw.get("qualified_in_regime") is True:
        raise LegacyRejected("LEGACY_QUALIFICATION_FABRICATION")
    kind = raw.get("source_kind")
    claim = raw.get("original_claim_status")
    digest = raw.get("source_sha256")
    if not isinstance(kind, str) or not isinstance(claim, str) or not claim:
        raise LegacyRejected("LEGACY_IDENTITY_INCOMPLETE")
    if not isinstance(digest, str):
        raise LegacyRejected("LEGACY_HASH_MISSING")
    path = raw.get("source_path")
    if not isinstance(path, str):
        raise LegacyRejected("LEGACY_PATH_MISSING")
    source = Path(path)
    if not source.is_absolute():
        raise LegacyRejected("LEGACY_PATH_NOT_ABSOLUTE")
    if source.is_symlink():
        raise LegacyRejected("LEGACY_SYMLINK")
    if not source.is_file():
        raise LegacyRejected("LEGACY_SOURCE_ABSENT")
    from devbench.study.evidence import _read
    try:
        retained = _read(source, 16 * 1024**2)
    except (ValueError, OSError) as error:
        raise LegacyRejected("LEGACY_SOURCE_BOUND_OR_UNSAFE") from error
    actual = hashlib.sha256(retained).hexdigest()
    if actual != digest:
        raise LegacyRejected("LEGACY_HASH_MISMATCH")
    observed = tuple(sorted(
        key for key, value in raw.items()
        if key not in {"qualified_in_regime"} and value not in (None, "")
    ))
    missing = tuple(name for name in NEW_SEMANTICS if name not in observed)
    return LegacyImport(
        source_kind=kind,
        source_path=str(source),
        source_sha256=digest,
        original_claim_status=claim,
        observed_fields=observed,
        missing_semantics=missing,
        qualified_in_regime=False,
    )
