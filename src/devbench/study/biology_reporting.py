"""Bounded report reads bound to retained coordinator artifact identities."""
from pathlib import Path
import hashlib
import re

from .contracts import canonical_json_bytes


MAX_RECORD_BYTES = 64_000_000
MAX_DETAIL_BYTES = 64_000_000
MAX_TOTAL_DETAIL_BYTES = 128_000_000
MAX_PREDICTED_SCALARS = 1_000_000
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _path(value):
    path = Path(value)
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("Report inputs require absolute paths without parent traversal")
    if any(part.is_symlink() for part in (path, *path.parents)):
        raise ValueError("Report input paths must not contain symlinks")
    return path


def _digest(value):
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise ValueError("Expected artifact identity must be a SHA-256 digest")
    return value


def load_biology_report_records(records_path, plan, *, task_id=None, execution_receipt_sha256=None,
                                 allow_result_analysis=False):
    """Load compact records, optionally restoring one task's predicted details.

    Internal hash consistency is distinguished from verification against an
    independently retained execution receipt. Neither is a signature. Details
    must match their scheduled slot before any manifest-controlled path is read.
    """
    if allow_result_analysis is not True:
        raise PermissionError("Biological result reading is disabled")
    from .biology_planning import validate_biology_plan, schedule_biology_attempts
    from .biology_records import parse_biological_attempt
    from .evidence import _read, decode_json
    plan = validate_biology_plan(plan)
    source = _path(records_path)
    if execution_receipt_sha256 is not None:
        _digest(execution_receipt_sha256)
    if task_id is not None:
        _digest(task_id)
    raw = _read(source, MAX_RECORD_BYTES)
    envelope = decode_json(raw)
    if (not isinstance(envelope, dict) or set(envelope) != {"record_format", "plan_identity", "records"}
            or envelope["record_format"] != "devbench.biology.attempt-records/1"
            or envelope["plan_identity"] != plan.sha256() or not isinstance(envelope["records"], list)
            or len(envelope["records"]) > 100_000):
        raise ValueError("Biological record envelope does not match the frozen plan/format")
    records = [parse_biological_attempt(row) for row in envelope["records"]]
    if len({record.attempt_id for record in records}) != len(records):
        raise ValueError("Duplicate attempt records prevent trustworthy aggregation")
    manifest_path = _path(source.parent / "execution.json")
    execution_raw = _read(manifest_path, 32_000_000)
    manifest_sha = hashlib.sha256(execution_raw).hexdigest()
    if execution_receipt_sha256 is not None and execution_receipt_sha256 != manifest_sha:
        raise ValueError("Execution manifest differs from the separately retained receipt")
    manifest = decode_json(execution_raw)
    if (not isinstance(manifest, dict) or manifest.get("plan_identity") != plan.sha256()
            or manifest.get("aggregate_path") != str(source)
            or manifest.get("aggregate_sha256") != hashlib.sha256(raw).hexdigest()):
        raise ValueError("Recorded aggregate differs from the execution manifest")
    artifacts = manifest.get("attempt_artifacts")
    if not isinstance(artifacts, dict) or len(artifacts) > 100_000:
        raise ValueError("Execution requires a bounded per-attempt artifact inventory")
    scheduled = {row.attempt_id: row for row in schedule_biology_attempts(plan)}
    if (set(artifacts) != {row.attempt_id for row in records} or set(artifacts) - set(scheduled)
            or any(row.safe_rows for row in records)
            or manifest.get("safe_detail_storage") != "per_attempt"
            or manifest.get("safe_rows_omitted_from_aggregate") is not True):
        raise ValueError("Execution artifact inventory disagrees with compact scheduled records")
    inventory_rows = inventory_scalars = 0
    for attempt_id, artifact in artifacts.items():
        if not isinstance(artifact, dict):
            raise ValueError("Execution artifact inventory entries must be objects")
        counts = [artifact.get(key) for key in ("safe_row_count", "safe_scalar_count")]
        if any(type(value) is not int or not 0 <= value <= MAX_PREDICTED_SCALARS for value in counts):
            raise ValueError("Execution artifact detail counts exceed their contract")
        _digest(artifact.get("record_sha256"))
        detail_path = _path(artifact.get("record_path"))
        if not detail_path.is_relative_to(source.parent / "attempts" / attempt_id):
            raise ValueError("Detail path escapes its assigned attempt directory")
        inventory_rows += counts[0]
        inventory_scalars += counts[1]
    retained = {}
    for key in ("retained_safe_row_count", "retained_safe_scalar_count"):
        value = manifest.get(key)
        if type(value) is not int or value < 0:
            raise ValueError("Execution must declare nonnegative retained detail counts")
        retained[key] = value
    if (retained["retained_safe_row_count"] != inventory_rows
            or retained["retained_safe_scalar_count"] != inventory_scalars):
        raise ValueError("Retained detail counts disagree with the artifact inventory")
    storage = {"safe_detail_storage": "per_attempt", "safe_rows_omitted_from_aggregate": True,
        **retained, "loaded_safe_row_count": 0, "loaded_safe_scalar_count": 0,
        "detail_task_id": task_id, "execution_manifest_sha256": manifest_sha,
        "retained_root_verified": execution_receipt_sha256 is not None,
        "receipt_scope": "separately_retained_root" if execution_receipt_sha256 is not None else "internal_consistency_only",
        "clinical_qualification": False}
    if task_id is not None and task_id not in {row.task_identity for row in scheduled.values()}:
        raise ValueError("Requested detail task does not belong to the frozen schedule")
    if task_id is None:
        return records, storage
    total_bytes = total_rows = total_scalars = 0
    restored = []
    bindings = ("study_id", "plan_identity", "task_identity", "cohort_id", "recipe_id",
                "template_id", "case_id", "condition", "repetition")
    for record in records:
        if record.task_identity != task_id:
            restored.append(record)
            continue
        slot = scheduled.get(record.attempt_id)
        if slot is None or any(getattr(record, field) != getattr(slot, field) for field in bindings):
            raise ValueError("Selected detail record has invalid scheduled bindings")
        artifact = artifacts.get(record.attempt_id)
        if not isinstance(artifact, dict):
            raise ValueError("Selected attempt has no retained detail receipt")
        digest = _digest(artifact.get("record_sha256"))
        detail = _path(artifact.get("record_path"))
        expected_dir = source.parent / "attempts" / record.attempt_id
        if not detail.is_relative_to(expected_dir):
            raise ValueError("Detail path escapes its assigned attempt directory")
        limit = min(MAX_DETAIL_BYTES, MAX_TOTAL_DETAIL_BYTES-total_bytes)
        if limit <= 0:
            raise ValueError("Selected task detail exceeds the aggregate byte budget")
        detail_raw = _read(detail, limit)
        total_bytes += len(detail_raw)
        if hashlib.sha256(detail_raw).hexdigest() != digest:
            raise ValueError("Retained attempt details differ from their manifest identity")
        full = parse_biological_attempt(decode_json(detail_raw))
        if (full.model_dump(mode="json", exclude={"safe_rows"})
                != record.model_dump(mode="json", exclude={"safe_rows"})):
            raise ValueError("Detailed and aggregate attempt fields disagree")
        row_count = len(full.safe_rows)
        scalar_count = sum(len(row.values) for row in full.safe_rows)
        if artifact.get("safe_row_count") != row_count or artifact.get("safe_scalar_count") != scalar_count:
            raise ValueError("Retained detail counts disagree with their saved receipt")
        total_rows += row_count
        total_scalars += scalar_count
        if total_rows > MAX_PREDICTED_SCALARS or total_scalars > MAX_PREDICTED_SCALARS:
            raise ValueError("Selected task prediction detail exceeds the scalar budget")
        restored.append(full)
    storage.update(loaded_safe_row_count=total_rows, loaded_safe_scalar_count=total_scalars)
    return restored, storage
