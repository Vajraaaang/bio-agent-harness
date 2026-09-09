"""Failure telemetry only; these fixtures are not benchmark or numerical results."""
import json

from scripts.native_effect_coverage import collect_execution_status


def save(root,name,value):
    path=root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(value))


def test_collector_preserves_cli_failure_after_candidate_numeric_completion(tmp_path):
    save(tmp_path,"execution/outcome.json",{"status":"analysis_complete_pending_review"})
    save(tmp_path,"execution/controlled-verification.json",{"valid":False,"analysis_completed":False,"errors":["Native process ran outside its launch authorization/return interval"]})
    save(tmp_path,"stdout.log",{"status":"operation_failed","error":"native_controlled_evidence_verification_failed: timing"})
    result=collect_execution_status(tmp_path,1)
    assert result["status"]=="failed" and result["usable"] is False
    assert result["candidate_status"]=="analysis_complete_pending_review"
    assert "timing" in result["error"] and result["verification_errors"]


def test_exit_zero_without_verified_semantics_is_not_usable(tmp_path):
    save(tmp_path,"execution/outcome.json",{"status":"analysis_complete_pending_review"})
    save(tmp_path,"stdout.log",{"status":"analysis_complete_pending_review"})
    result=collect_execution_status(tmp_path,0)
    assert result["usable"] is False and result["error"]


def test_verified_candidate_status_is_usable(tmp_path):
    save(tmp_path,"execution/outcome.json",{"status":"analysis_complete_pending_review"})
    save(tmp_path,"execution/controlled-verification.json",{"valid":True,"analysis_completed":True,"errors":[]})
    save(tmp_path,"stdout.log",{"status":"analysis_complete_pending_review"})
    result=collect_execution_status(tmp_path,0)
    assert result["usable"] is True and result["error"] is None


def test_malformed_cli_output_has_explicit_failure_telemetry(tmp_path):
    (tmp_path/"stdout.log").write_text("infrastructure interrupted JSON output")
    result=collect_execution_status(tmp_path,1)
    assert result["usable"] is False and result["evidence_read_errors"] and result["error"]
