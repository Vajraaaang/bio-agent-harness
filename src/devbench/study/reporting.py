"""Bounded report inputs and optional per-task gene detail loading.

The execution manifest binds a compact aggregate to retained detail records.
Its origin is only verified when the caller supplies an independently retained
hash; internal hash agreement is not operator authentication.
"""
import hashlib
from pathlib import Path
import re

from .evidence import _read, decode_json


def load_report_records(records_path, plan, *, gene_task_id=None,
                        execution_receipt_sha256=None, allow_result_analysis=False):
    if allow_result_analysis is not True:
        raise PermissionError("Recorded result reading is disabled")
    records_path = Path(records_path)
    if ".." in records_path.parts:
        raise ValueError("Record paths must not contain parent traversal")
    content = _read(records_path, 128_000_000)
    records = decode_json(content)
    if not isinstance(records, list) or len(records) > 100_000:
        raise ValueError("Attempt records must be a bounded JSON array")
    if any(not isinstance(row, dict) for row in records):
        raise ValueError("Attempt records must contain JSON objects")
    manifest_path = records_path.parent / "execution.json"
    if records_path.name != "attempt_records.json" or not manifest_path.exists():
        if gene_task_id is not None or execution_receipt_sha256 is not None:
            raise ValueError("Gene detail or receipt verification requires the execution manifest")
        return records, {}
    manifest_bytes = _read(manifest_path, 8_000_000)
    if execution_receipt_sha256 is not None:
        if (not isinstance(execution_receipt_sha256, str)
                or not re.fullmatch(r"[0-9a-f]{64}", execution_receipt_sha256)
                or hashlib.sha256(manifest_bytes).hexdigest() != execution_receipt_sha256):
            raise ValueError("Execution manifest differs from the independently retained receipt")
    manifest = decode_json(manifest_bytes)
    if (not isinstance(manifest, dict) or manifest.get("record_format") != "devbench.study.execution/1"
            or manifest.get("plan_identity") != plan.sha256()
            or manifest.get("attempt_records_sha256") != hashlib.sha256(content).hexdigest()):
        raise ValueError("Execution manifest does not bind this plan and aggregate record file")
    if manifest.get("gene_detail_storage") != "per_attempt" or manifest.get("gene_rows_omitted_from_aggregate") is not True:
        raise ValueError("Unsupported execution gene-detail storage contract")
    artifacts = manifest.get("attempt_artifacts")
    if not isinstance(artifacts, dict) or len(artifacts) > len(records):
        raise ValueError("Malformed per-attempt artifact index")
    by_id = {row.get("attempt_id"): row for row in records}
    if len(by_id) != len(records) or any(not isinstance(key, str) or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9_.-]{0,159}", key) for key in by_id):
        raise ValueError("Aggregate attempt IDs must be distinct safe identifiers")
    from .planning import schedule_attempts
    scheduled = {attempt.attempt_id: attempt for attempt in schedule_attempts(plan)}
    retained, loaded, bytes_loaded = 0, 0, 0
    selected_found = gene_task_id is None
    for attempt_id, artifact in artifacts.items():
        if attempt_id not in by_id or not isinstance(artifact, dict):
            raise ValueError("Detail artifact does not belong to the aggregate")
        count = artifact.get("gene_row_count")
        if type(count) is not int or not 0 <= count <= 100_000:
            raise ValueError("Invalid retained gene row count")
        retained += count
        row = by_id[attempt_id]
        if row.get("gene_rows"):
            raise ValueError("Compact aggregate unexpectedly contains gene details")
        if gene_task_id is not None and row.get("task_identity") == gene_task_id:
            expected_attempt = scheduled.get(attempt_id)
            if (expected_attempt is None or row.get("study_id") != plan.study_id
                    or row.get("plan_identity") != plan.sha256()
                    or row.get("task_identity") != expected_attempt.task_identity
                    or row.get("condition") != expected_attempt.condition
                    or row.get("cohort_id") != expected_attempt.cohort_id
                    or row.get("actor_type") != "model_participant"):
                raise ValueError("Detail record does not belong to the frozen model-attempt schedule")
            selected_found = True
            path = Path(artifact.get("record_path", ""))
            # Follow only the coordinator's deterministic per-attempt location.
            expected = records_path.parent / "attempts" / attempt_id / "record.json"
            if path != expected:
                raise ValueError("Detail record path is outside the expected attempt location")
            payload = _read(path, min(64_000_000, 128_000_000 - bytes_loaded))
            bytes_loaded += len(payload)
            if hashlib.sha256(payload).hexdigest() != artifact.get("record_sha256"):
                raise ValueError("Per-attempt detail hash mismatch")
            full = decode_json(payload)
            if (not isinstance(full, dict) or not isinstance(full.get("gene_rows"), list)
                    or len(full["gene_rows"]) != count or {**full, "gene_rows": []} != row):
                raise ValueError("Per-attempt record disagrees with the aggregate or receipt count")
            by_id[attempt_id] = full
            loaded += count
    if retained != manifest.get("retained_gene_row_count"):
        raise ValueError("Retained gene count does not match the execution manifest")
    if not selected_found:
        raise ValueError("Requested task has no retained detail artifact")
    storage = {"gene_detail_storage": "per_attempt", "gene_rows_omitted_from_aggregate": True,
               "retained_gene_row_count": retained, "loaded_gene_row_count": loaded,
               "execution_receipt_origin_verified": execution_receipt_sha256 is not None,
               "gene_detail_count_scope": "coordinator_receipt_declared",
               "gene_detail_task_filter": gene_task_id}
    return [by_id[row["attempt_id"]] for row in records], storage
