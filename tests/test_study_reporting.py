"""Empty file/configuration report plumbing; no measured records or analysis."""
from pathlib import Path
import hashlib
import json

import pytest

from devbench.study import default_study_plan
from devbench.study.reporting import load_report_records


def test_report_reader_is_disabled_before_paths_or_plan_access():
    with pytest.raises(PermissionError, match="disabled"):
        load_report_records(Path("/missing"), None)


def test_empty_external_record_list_preserves_unavailable_storage(tmp_path):
    path = tmp_path / "empty.json"
    path.write_text("[]")
    records, storage = load_report_records(path, default_study_plan(), allow_result_analysis=True)
    assert records == [] and storage == {}


def test_detail_requests_cannot_guess_files_without_manifest(tmp_path):
    path = tmp_path / "empty.json"
    path.write_text("[]")
    with pytest.raises(ValueError, match="requires the execution manifest"):
        load_report_records(path, default_study_plan(), gene_task_id="unavailable",
                            allow_result_analysis=True)


def test_malicious_identifier_cannot_be_used_as_a_detail_path(tmp_path):
    # Protocol-only record stub; no biological values or model output is used.
    plan = default_study_plan()
    path = tmp_path / "attempt_records.json"
    content = json.dumps([{"attempt_id": "../escape", "task_identity": "requested"}]).encode()
    path.write_bytes(content)
    (tmp_path / "execution.json").write_text(json.dumps({
        "record_format": "devbench.study.execution/1", "plan_identity": plan.sha256(),
        "attempt_records_sha256": hashlib.sha256(content).hexdigest(),
        "gene_detail_storage": "per_attempt", "gene_rows_omitted_from_aggregate": True,
        "attempt_artifacts": {"../escape": {"gene_row_count": 1}}, "retained_gene_row_count": 1}))
    with pytest.raises(ValueError, match="safe identifiers"):
        load_report_records(path, plan, gene_task_id="requested", allow_result_analysis=True)
