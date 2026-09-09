"""Flat reports preserve missing outcomes, escape content and bind exact bytes."""
import hashlib
import json

from scripts.make_leaderboard import html_report, markdown_report, write_reports


def test_ungraded_results_are_not_completed_grades_and_html_is_escaped():
    attack = '<script>alert("x")</script>'
    runs = [{"task_id":attack,"passed":False,"score":0,"grader_result":None,
             "metadata":{"track":"batch","condition":"assisted","error":attack,"failure_type":"resource_interrupt"}}]
    markdown = markdown_report(runs)
    report = html_report(runs, "a" * 64)
    assert "NOT GRADED" in report and "Completed grades: 0" in markdown
    assert "Pass rate among completed grades: 0/0 (unknown)" in markdown
    assert attack not in report and "&lt;script&gt;" in report
    assert "CAS" not in report and "Clinical release" not in report
    assert "default-src 'none'" in report


def test_export_receipts_hash_the_actual_saved_files(tmp_path):
    result = write_reports([], tmp_path / "runs.json", tmp_path / "report.html", tmp_path / "report.md")
    receipts = json.loads((tmp_path / "runs.receipts.json").read_text())
    assert len(receipts["files"]) == 3
    for item in receipts["files"]:
        from pathlib import Path
        assert hashlib.sha256(Path(item["path"]).read_bytes()).hexdigest() == item["sha256"]
    assert result["receipts"].endswith("runs.receipts.json")
