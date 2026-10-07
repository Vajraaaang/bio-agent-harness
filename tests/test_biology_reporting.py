"""Byte-only empty ledgers and no-execution boundary checks."""
import hashlib
import json

import pytest

from devbench.study.biology_planning import default_biology_study
from devbench.study.biology_reporting import load_biology_report_records
from devbench.study.contracts import canonical_json_bytes


def empty_ledger(directory):
    plan = default_biology_study()
    raw = canonical_json_bytes({"record_format": "devbench.biology.attempt-records/1",
                                "plan_identity": plan.sha256(), "records": []})
    path = directory / "records.json"
    path.write_bytes(raw)
    manifest = {"plan_identity": plan.sha256(), "aggregate_path": str(path),
        "aggregate_sha256": hashlib.sha256(raw).hexdigest(), "attempt_artifacts": {},
        "retained_safe_row_count": 0, "retained_safe_scalar_count": 0,
        "safe_detail_storage": "per_attempt", "safe_rows_omitted_from_aggregate": True}
    receipt = canonical_json_bytes(manifest)
    (directory / "execution.json").write_bytes(receipt)
    return plan, path, hashlib.sha256(receipt).hexdigest()


def test_empty_ledger_distinguishes_internal_consistency_and_retained_root(tmp_path):
    plan, path, digest = empty_ledger(tmp_path)
    records, storage = load_biology_report_records(path, plan, allow_result_analysis=True)
    assert records == []
    assert storage["receipt_scope"] == "internal_consistency_only"
    assert storage["retained_root_verified"] is False
    records, storage = load_biology_report_records(path, plan,
        execution_receipt_sha256=digest, allow_result_analysis=True)
    assert records == [] and storage["retained_root_verified"] is True
    assert storage["clinical_qualification"] is False


def test_empty_receipt_mismatch_fails_before_any_detail_reads(tmp_path):
    plan, path, _ = empty_ledger(tmp_path)
    with pytest.raises(ValueError, match="separately retained"):
        load_biology_report_records(path, plan, execution_receipt_sha256="a" * 64, allow_result_analysis=True)


def test_empty_ledger_digest_and_symlink_inputs_fail_closed(tmp_path):
    plan, path, _ = empty_ledger(tmp_path)
    alias = tmp_path / "alias.json"
    alias.symlink_to(path)
    with pytest.raises(ValueError, match="symlink"):
        load_biology_report_records(alias, plan, allow_result_analysis=True)
    path.write_text(path.read_text() + "\n")
    with pytest.raises(ValueError, match="aggregate differs"):
        load_biology_report_records(path, plan, allow_result_analysis=True)


def test_empty_inventory_cannot_claim_retained_predictions(tmp_path):
    plan, path, _ = empty_ledger(tmp_path)
    manifest_path = path.parent / "execution.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["retained_safe_scalar_count"] = 1
    manifest_path.write_bytes(canonical_json_bytes(manifest))
    with pytest.raises(ValueError, match="artifact inventory"):
        load_biology_report_records(path, plan, allow_result_analysis=True)
