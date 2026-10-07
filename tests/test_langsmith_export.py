"""Post-run tracing contracts. No provider, LangSmith or biological analysis calls."""
import hashlib
import json
from pathlib import Path
from unittest.mock import Mock

import pytest


def dump(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, allow_nan=False))
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def recorded_run(tmp_path):
    def make(track="classic"):
        root = tmp_path / track
        rows = []
        artifacts = {}
        for condition in ("baseline", "optional", "enforced"):
            attempt_id = "attempt_" + condition
            source_kind = "public_processed" if track == "public" else "synthetic"
            row = dict(attempt_id=attempt_id, condition=condition, source_kind=source_kind,
                       input_sha256="1"*64, task_identity="2"*64, actual_model=None,
                       requested_model="gpt-5.5", model_identity_verified=False,
                       attempted=True, completion_status="completed", accepted_final=True,
                       scientific_success=condition != "baseline", reference_task_success=None,
                       report_accepted=True, workflow_passed=condition == "enforced",
                       trusted_tool_capture_complete=True, trusted_tool_calls=int(condition != "baseline"),
                       elapsed_sec=4.5, RSS_MB=None, tokens=42, cost=None,
                       biological_metrics={"f1": 0.7}, errors=["SECRET_patient"],
                       gene_rows=[{"subject": "SECRET_patient"}])
            rows.append(row)
            attempt = root / "attempts" / attempt_id
            dump(attempt / "execution.json", {"adapter": {
                "duration_sec": 3.0, "tokens": 42,
                "commands": [{"command": "python -c 'SECRET_API_KEY'", "exit_code": 0,
                              "status": "completed", "output_excerpt": "SECRET_patient"}],
                "observable_events": [{"type": "self_correction", "tool_name": "audit_quality",
                                       "failed_call_id": "SECRET_patient", "basis": "SECRET_patient"}]},
                "errors": ["SECRET_patient"], "workspace": str(tmp_path / "workspaces" / attempt_id)})
            dump(attempt / "dispatch_checkpoint.json", {"timestamp": "2026-09-12T12:00:00+00:00"})
            answer_hash = dump(tmp_path / "workspaces" / attempt_id / "final_discovery.json", {
                "claim_scope": "descriptive", "condition_number_validated": True,
                "stage_kinetics": {"SECRET_sample": 1.5}, "biological_rationale": "SECRET_patient"})
            detail_path = attempt / "execution.json"
            details = json.loads(detail_path.read_text())
            details["adapter"]["commands"][0].update(direct_script_invocation=True, succeeded=True,
                answer_sha256_at_completion=answer_hash)
            artifacts[attempt_id] = {"execution_detail_sha256": dump(detail_path, details)}
            if condition != "baseline":
                event = dict(sequence=1, tool="audit_quality", arguments={"secret":"SECRET_API_KEY"},
                             started_at="2026-09-12T12:00:01+00:00", elapsed_sec=0.25,
                             status="failed", error="SECRET_patient", receipt_id="3"*64)
                path = attempt / "receipts" / "events.jsonl"
                path.parent.mkdir(parents=True)
                path.write_text(json.dumps(event)+"\n")
            dump(attempt / "exec-preflight" / "evidence.json", {
                "verified": True, "scored": False, "duration_sec": 1.0,
                "inference_dispatch_attempted": True,
                "billing": {"tokens": 7, "cost_usd": None}, "checks": {"private_read_denied": True}})
        aggregate = root / ("attempt_records.json" if track == "classic" else "records.json")
        aggregate_hash = dump(aggregate, rows if track == "classic" else {"records": rows})
        manifest = {"record_format": f"devbench.{'study' if track == 'classic' else track}.execution/1",
                    "status": "finished", "plan_identity": "4"*64,
                    "attempt_artifacts": artifacts,
                    "attempt_records_sha256" if track == "classic" else "aggregate_sha256": aggregate_hash}
        dump(root / "execution.json", manifest)
        return root, tmp_path / "workspaces"
    return make


@pytest.mark.parametrize("track", ["classic", "biology", "public"])
def test_preview_real_formats_redacts_content_and_keeps_conditions(recorded_run, tmp_path, track):
    from devbench.study.langsmith_export import prepare_export
    root, workspaces = recorded_run(track)
    before = {str(p): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    report = prepare_export(root, tmp_path / "preview", data_scope="public_processed" if track == "public" else "synthetic",
                            workspace_root=workspaces, allow_evidence_read=True)
    payload = json.loads(Path(report["payload_path"]).read_text())
    serialized = json.dumps(payload)
    assert "SECRET" not in serialized and str(tmp_path) not in serialized
    parents = [r for r in payload["runs"] if "parent_run_id" not in r]
    assert {r["extra"]["metadata"]["condition"] for r in parents} == {"baseline", "optional", "enforced"}
    assert all(r["outputs"]["cost"] is None and r["outputs"]["RSS_MB"] is None for r in parents)
    assert all(r["extra"]["metadata"]["actual_model"] is None for r in parents)
    assert any(r["name"] == "command:python" for r in payload["runs"])
    assert any(r["name"] == "self_correction" for r in payload["runs"])
    assert len([r for r in payload["runs"] if r["name"] == "tool:audit_quality"]) == 2
    assert len([r for r in payload["runs"] if r["name"] == "permission_preflight"]) == 3
    assert all(r["outputs"]["final_answer"]["condition_number_validated"] for r in parents)
    assert before == {str(p): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def test_preview_requires_authorization_before_reading(tmp_path):
    from devbench.study.langsmith_export import prepare_export
    with pytest.raises(PermissionError):
        prepare_export(tmp_path / "absent", tmp_path / "out", data_scope="synthetic")
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize("fault", ["active", "hash_mismatch", "clinical", "symlink", "truncated_events"])
def test_invalid_or_unsafe_source_is_rejected(recorded_run, tmp_path, fault):
    from devbench.study.langsmith_export import prepare_export
    root, _ = recorded_run()
    path = root / "execution.json"
    manifest = json.loads(path.read_text())
    if fault == "active":
        manifest["status"] = "started"
    elif fault == "hash_mismatch":
        manifest["attempt_records_sha256"] = "0"*64
    elif fault == "clinical":
        records = json.loads((root / "attempt_records.json").read_text())
        records[0]["source_kind"] = "clinical"
        manifest["attempt_records_sha256"] = dump(root / "attempt_records.json", records)
    elif fault == "symlink":
        victim = root / "attempts/attempt_baseline/execution.json"
        external = tmp_path / "external.json"
        external.write_text(victim.read_text())
        victim.unlink()
        victim.symlink_to(external)
    else:
        (root / "attempts/attempt_optional/receipts/events.jsonl").write_text('{"tool":')
    dump(path, manifest)
    with pytest.raises((ValueError, OSError)):
        prepare_export(root, tmp_path / "out", data_scope="synthetic", allow_evidence_read=True)
    assert not (tmp_path / "out" / "payload.json").exists()


@pytest.mark.parametrize("conditions", [["baseline", "optional", "enforced"], ["optional", "enforced"]])
def test_standalone_probe_does_not_become_scored_result(tmp_path, conditions):
    from devbench.study.langsmith_export import prepare_export
    root = tmp_path / "probes"
    dump(root / "preflight.json", {"record_format": "devbench.preflight-only/1", "track": "classic",
        "status": "interrupted", "plan_identity": "4"*64,
        "requested_conditions": conditions,
        "probes": {c:{"condition":c,"status":"not_run","verified":False} for c in conditions}})
    result = prepare_export(root, tmp_path / "out", data_scope="synthetic", allow_evidence_read=True)
    runs = json.loads(Path(result["payload_path"]).read_text())["runs"]
    assert len(runs) == len(conditions)
    assert all(r["extra"]["metadata"]["scored"] is False for r in runs)
    assert all(r["outputs"].get("scientific_success") is None for r in runs)


def test_export_ids_are_stable_and_span_budget_is_enforced(recorded_run, tmp_path):
    from devbench.study.langsmith_export import prepare_export
    root, _ = recorded_run()
    first = prepare_export(root, tmp_path / "one", data_scope="synthetic", allow_evidence_read=True)
    second = prepare_export(root, tmp_path / "two", data_scope="synthetic", allow_evidence_read=True)
    assert first["payload_sha256"] == second["payload_sha256"]
    with pytest.raises(ValueError, match="span"):
        prepare_export(root, tmp_path / "small", data_scope="synthetic", allow_evidence_read=True, max_spans=2)


def test_upload_authorization_and_hash_precede_credentials_and_transport(tmp_path, monkeypatch):
    from devbench.study.langsmith_export import upload_export
    monkeypatch.delenv("LANGSMITH_API_KEY", raising=False)
    with pytest.raises(PermissionError):
        upload_export(tmp_path / "absent", "0"*64, tmp_path / "journal")


def test_upload_uses_only_reviewed_payload_and_records_partial_failure(recorded_run, tmp_path, monkeypatch):
    import devbench.study.langsmith_export as tracing
    root, _ = recorded_run()
    preview = tracing.prepare_export(root, tmp_path / "preview", data_scope="synthetic", allow_evidence_read=True)
    payload = Path(preview["payload_path"])
    send = Mock(side_effect=[None, OSError("SECRET_API_KEY network failure")])
    monkeypatch.setattr(tracing, "_post_run", send)
    monkeypatch.setenv("LANGSMITH_API_KEY", "SECRET_API_KEY")
    result = tracing.upload_export(payload, preview["payload_sha256"], tmp_path / "upload", allow_upload=True)
    assert result["status"] == "incomplete" and result["confirmed_run_uploads"] == 1
    assert result["uncertain_run_uploads"] == 1 and send.call_count == 2
    assert "SECRET" not in json.dumps(result)
    assert Path(result["receipt_path"]).exists()
    assert hashlib.sha256(payload.read_bytes()).hexdigest() == preview["payload_sha256"]


@pytest.mark.parametrize("fault", ["hash", "endpoint", "credentials"])
def test_upload_admission_never_sends_on_error(recorded_run, tmp_path, monkeypatch, fault):
    import devbench.study.langsmith_export as tracing
    root, _ = recorded_run()
    preview = tracing.prepare_export(root, tmp_path / "preview", data_scope="synthetic", allow_evidence_read=True)
    send = Mock()
    monkeypatch.setattr(tracing, "_post_run", send)
    monkeypatch.setenv("LANGSMITH_API_KEY", "SECRET_API_KEY")
    if fault == "credentials":
        monkeypatch.delenv("LANGSMITH_API_KEY")
    with pytest.raises((ValueError, RuntimeError)):
        tracing.upload_export(Path(preview["payload_path"]), "0"*64 if fault == "hash" else preview["payload_sha256"],
            tmp_path / "upload", endpoint="https://example.invalid" if fault == "endpoint" else "https://api.smith.langchain.com",
            allow_upload=True)
    send.assert_not_called()


def test_cli_registers_preview_and_upload_without_affecting_runs():
    from devbench.study.cli import build_parser
    commands = build_parser()._subparsers._group_actions[0].choices
    assert "langsmith-export" in commands and "langsmith-upload" in commands


@pytest.mark.parametrize("target", ["detail", "answer"])
def test_changed_bound_evidence_is_rejected(recorded_run, tmp_path, target):
    from devbench.study.langsmith_export import prepare_export
    root, workspaces = recorded_run()
    path = root / "attempts/attempt_baseline/execution.json" if target == "detail" else workspaces / "attempt_baseline/final_discovery.json"
    value = json.loads(path.read_text())
    value["tampered"] = True
    dump(path, value)
    with pytest.raises(ValueError, match="hash|binding"):
        prepare_export(root, tmp_path / "out", data_scope="synthetic", workspace_root=workspaces,
                       allow_evidence_read=True)


def test_upload_rejects_unredacted_field_even_with_matching_hash(recorded_run, tmp_path, monkeypatch):
    import devbench.study.langsmith_export as tracing
    root, _ = recorded_run()
    preview = tracing.prepare_export(root, tmp_path / "preview", data_scope="synthetic", allow_evidence_read=True)
    path = Path(preview["payload_path"])
    payload = json.loads(path.read_text())
    payload["runs"][0]["outputs"]["free_text"] = "SECRET_patient"
    digest = dump(path, payload)
    monkeypatch.setenv("LANGSMITH_API_KEY", "SECRET_API_KEY")
    send = Mock()
    monkeypatch.setattr(tracing, "_post_run", send)
    with pytest.raises(ValueError, match="redaction"):
        tracing.upload_export(path, digest, tmp_path / "upload", allow_upload=True)
    send.assert_not_called()


def test_observed_broker_recovery_is_explicit_and_not_an_llm_judgment(recorded_run, tmp_path):
    from devbench.study.langsmith_export import prepare_export
    root, _ = recorded_run()
    path = root / "attempts/attempt_optional/receipts/events.jsonl"
    first = json.loads(path.read_text())
    second = {**first, "sequence":2, "status":"succeeded", "arguments":{"changed":True}}
    path.write_text(json.dumps(first)+"\n"+json.dumps(second)+"\n")
    result = prepare_export(root, tmp_path / "out", data_scope="synthetic", allow_evidence_read=True)
    runs = json.loads(Path(result["payload_path"]).read_text())["runs"]
    recovered = [run for run in runs if run["name"] == "tool_recovery"]
    assert len(recovered) == 1 and recovered[0]["outputs"]["arguments_changed"] is True


def test_native_transport_posts_closed_span_and_blocks_redirects(monkeypatch):
    from devbench.study.langsmith_export import _post_run, _NoRedirect
    import devbench.study.langsmith_export as tracing
    class Response:
        status = 202
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self, size): return b"{}"
    opener = Mock()
    opener.open.return_value = Response()
    build = Mock(return_value=opener)
    monkeypatch.setattr(tracing, "build_opener", build)
    span = {"id":"00000000-0000-0000-0000-000000000001", "end_time":"2026-09-13T00:00:00+00:00"}
    _post_run("https://api.smith.langchain.com", "test-secret", None, span, 5)
    request = opener.open.call_args.args[0]
    assert request.full_url == "https://api.smith.langchain.com/runs" and request.method == "POST"
    assert json.loads(request.data) == span
    assert any(isinstance(handler, _NoRedirect) for handler in build.call_args.args)
    assert _NoRedirect().redirect_request(request, None, 302, "", {}, "https://evil.invalid") is None


def test_successful_upload_sends_all_spans_once_without_llm_calls(recorded_run, tmp_path, monkeypatch):
    import devbench.study.langsmith_export as tracing
    root, workspaces = recorded_run()
    preview = tracing.prepare_export(root, tmp_path / "preview", data_scope="synthetic",
                                     workspace_root=workspaces, allow_evidence_read=True)
    send = Mock(return_value=None)
    monkeypatch.setattr(tracing, "_post_run", send)
    monkeypatch.setenv("LANGSMITH_API_KEY", "test-only")
    receipt = tracing.upload_export(Path(preview["payload_path"]), preview["payload_sha256"],
                                    tmp_path / "upload", allow_upload=True)
    assert receipt["status"] == "uploaded" and receipt["confirmed_run_uploads"] == preview["spans"]
    assert receipt["uncertain_run_uploads"] == receipt["llm_evaluations"] == 0
    sent = [call.args[3] for call in send.call_args_list]
    assert len({run["id"] for run in sent}) == len(sent)
    assert all(run["run_type"] != "llm" for run in sent)


def test_actual_cli_preview_and_denied_upload(recorded_run, tmp_path, capsys, monkeypatch):
    from devbench.study.cli import main
    root, _ = recorded_run()
    assert main(["langsmith-export", "--run-dir", str(root), "--output", str(tmp_path / "preview"),
                 "--data-scope", "synthetic", "--authorize-evidence-read"]) == 0
    result = json.loads(capsys.readouterr().out)
    monkeypatch.setenv("LANGSMITH_API_KEY", "SECRET_KEY")
    assert main(["langsmith-upload", "--payload", result["payload_path"], "--expected-sha256", result["payload_sha256"],
                 "--output", str(tmp_path / "upload")]) == 2
    assert "SECRET_KEY" not in capsys.readouterr().err
    assert not (tmp_path / "upload").exists()


def test_tool_name_allowlist_covers_current_biology_tools():
    from devbench.study.langsmith_export import TOOLS
    from devbench.study.biology_tools_bulk import BULK_TOOL_NAMES
    from devbench.study.biology_tools_singlecell import SINGLECELL_TOOL_NAMES
    assert set(BULK_TOOL_NAMES) | set(SINGLECELL_TOOL_NAMES) <= TOOLS


def test_tool_name_allowlist_covers_current_public_tools():
    from devbench.study.langsmith_export import TOOLS
    from devbench.study.public_tools import AUDIT, SUMMARY, NORMALIZE
    assert {AUDIT, SUMMARY, NORMALIZE} <= TOOLS


@pytest.mark.parametrize("cap", [True, 0, 10001])
def test_invalid_export_caps_reject_before_file_reads(tmp_path, cap):
    from devbench.study.langsmith_export import prepare_export
    with pytest.raises(ValueError, match="span"):
        prepare_export(tmp_path / "absent", tmp_path / "out", data_scope="synthetic", max_spans=cap,
                       allow_evidence_read=True)


def test_missing_event_times_are_labeled_and_broker_durations_are_preserved(recorded_run, tmp_path):
    from devbench.study.langsmith_export import prepare_export
    root, _ = recorded_run()
    result = prepare_export(root, tmp_path / "out", data_scope="synthetic", allow_evidence_read=True)
    runs = json.loads(Path(result["payload_path"]).read_text())["runs"]
    command = next(run for run in runs if run["name"] == "command:python")
    assert command["extra"]["metadata"]["event_timing_available"] is False
    tool = next(run for run in runs if run["name"] == "tool:audit_quality")
    assert tool["extra"]["metadata"]["event_timing_available"] is True
    assert tool["outputs"]["elapsed_sec"] == 0.25


@pytest.mark.parametrize("field,value", [("tokens", True), ("cost", False), ("scientific_success", 1)])
def test_wrong_measurement_types_are_not_exported_as_valid(recorded_run, tmp_path, field, value):
    from devbench.study.langsmith_export import prepare_export
    root, _ = recorded_run()
    rows = json.loads((root / "attempt_records.json").read_text())
    rows[0][field] = value
    manifest = json.loads((root / "execution.json").read_text())
    manifest["attempt_records_sha256"] = dump(root / "attempt_records.json", rows)
    dump(root / "execution.json", manifest)
    with pytest.raises(ValueError, match="outcome|measurement"):
        prepare_export(root, tmp_path / "out", data_scope="synthetic", allow_evidence_read=True)
