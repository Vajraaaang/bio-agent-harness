"""Reporting contracts with explicitly unexecuted local records."""
import hashlib
from pathlib import Path
import tempfile

import pytest

from devbench.study.contracts import canonical_json_bytes
from devbench.study.public_study import PublicStudyPlan, PreparedPublicTask, public_identity
from devbench.study.public_execution import initial_public_attempt_records
from devbench.study.public_reporting import load_public_records, summarize_public


def _records(repetitions=1):
    plan = PublicStudyPlan(study_id="fixture", datasets=("developmental_general",), blinding_nonce="1"*64,
                           requested_model="test-only", repetitions=repetitions)
    bundle = PreparedPublicTask(root="/opt/fixture", dataset_id="developmental_general", source_id="E-MTAB-6798",
        task_id=public_identity(plan, "task", "developmental_general"), plan_identity=plan.sha256(),
        input_sha256="2"*64, reference_sha256="3"*64, preparation_sha256="4"*64, independence_id="shared-specimens")
    records = initial_public_attempt_records(plan, {"developmental_general": bundle})
    return plan, [row.model_copy(update={"reference_task_eligible": True}) for row in records]


def test_missing_eligible_slots_count_as_failure_and_never_clinical_truth():
    plan, records = _records()
    summary = summarize_public(plan, records, allow_result_analysis=True)
    assert len(summary["cells"]) == 3
    assert all(cell["reference_task_success_rate"] == 0 for cell in summary["cells"])
    assert all(cell["complete_biological_correctness"] is None and cell["tokens"] is None for cell in summary["cells"])
    assert summary["pool_with_synthetic_results"] is False
    with pytest.raises(ValueError, match="complete"):
        summarize_public(plan, records[:-1], allow_result_analysis=True)
    with pytest.raises(PermissionError):
        summarize_public(plan, records)


def test_public_report_verifies_receipts_and_rejects_mutation_and_escape():
    plan, records = _records()
    root = Path(__file__).resolve().parents[1] / "tmp"
    root.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=root, prefix="public-report-") as directory:
        directory = Path(directory)
        def write(path, data):
            path.parent.mkdir(exist_ok=True, parents=True)
            raw = canonical_json_bytes(data)
            path.write_bytes(raw)
            return hashlib.sha256(raw).hexdigest()
        artifacts = {}
        for row in records:
            record_path = directory / "attempts" / row.attempt_id / "record.json"
            detail_path = record_path.parent / "execution.json"
            artifacts[row.attempt_id] = {"record_path": str(record_path), "record_sha256": write(record_path, row.model_dump(mode="json")),
                "execution_detail_path": str(detail_path), "execution_detail_sha256": write(detail_path, {"attempt_id": row.attempt_id}),
                "retained_tool_root_path": None, "retained_tool_root_sha256": None}
        aggregate = directory / "records.json"
        digest = write(aggregate, {"record_format": "devbench.public.attempt-records/1", "plan_identity": plan.sha256(),
                                    "records": [row.model_dump(mode="json") for row in records]})
        manifest = {"record_format": "devbench.public.execution/1", "plan_identity": plan.sha256(),
                    "aggregate_path": str(aggregate), "aggregate_sha256": digest, "attempt_artifacts": artifacts,
                    "planned_attempt_count": 3, "attempted_count": 0,
                    "reference_eligibility": {"developmental_general": {"reference_task_eligible": True, "reference_sha256": "3"*64}}}
        receipt = write(directory / "execution.json", manifest)
        loaded, evidence = load_public_records(aggregate, plan, execution_receipt_sha256=receipt, allow_result_analysis=True)
        assert loaded == records
        assert evidence["receipt_scope"] == "separately_retained_root"
        with pytest.raises(ValueError, match="retained receipt"):
            load_public_records(aggregate, plan, execution_receipt_sha256="f"*64, allow_result_analysis=True)
        artifacts[records[0].attempt_id]["record_path"] = str(directory / "outside.json")
        write(directory / "execution.json", manifest)
        with pytest.raises(ValueError, match="escapes"):
            load_public_records(aggregate, plan, allow_result_analysis=True)


def test_partial_memory_capture_does_not_claim_a_complete_condition_peak():
    plan, records = _records(repetitions=2)
    row = records[0]
    accounting = row.accounting_completeness.model_copy(update={"RSS_MB": "complete"})
    records[0] = row.model_copy(update={"RSS_MB": 42.0, "accounting_completeness": accounting})
    summary = summarize_public(plan, records, allow_result_analysis=True)
    assert all(cell["peak_memory_mb"] is None for cell in summary["cells"])


def test_public_cli_plan_only_and_authorization_before_path_reads():
    from devbench.study.cli import build_parser, _run_command
    parser = build_parser()
    for command, arguments in (("public-prepare", ["--cache-root", "/missing"]),
                               ("public-run", ["--bundles", "/missing", "--workspace-root", "/missing"]),
                               ("public-report", ["--records", "/missing"])):
        args = parser.parse_args([command, "--plan", "/missing", "--output", "/missing", *arguments])
        with pytest.raises(PermissionError):
            _run_command(args)
