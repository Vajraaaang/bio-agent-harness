"""Offline coordinator stop controls; these fixtures are never model evidence."""
from pathlib import Path
from types import SimpleNamespace
import tempfile

import pytest


def record(**changes):
    fields = dict(attempted=True, model_identity_verified=True, integrity_status="verified",
        trusted_tool_capture_complete=True, completion_status="completed", scientific_success=False,
        report_accepted=False)
    return SimpleNamespace(**(fields | changes))


def supervision(**changes):
    return SimpleNamespace(**(dict(failure_kind=None, exitcode=0, interrupted=False) | changes))


def detail(**changes):
    return {"adapter": dict(event_capture_complete=True, script_execution_observed=True, returncode=0) | changes}


@pytest.mark.parametrize("case,expected", [
    ("preflight", "scored_dispatch_not_reached"), ("identity", "model_identity_unverified"),
    ("integrity", "attempt_integrity_unverified"), ("tools", "tool_capture_incomplete"),
    ("events", "execution_capture_incomplete"), ("script", "script_execution_unverified"),
    ("provider", "participant_infrastructure_failure"), ("timeout", "participant_infrastructure_failure"),
    ("memory", "supervision_failure:memory_monitor"), ("exit", "supervision_failure:worker_exit"),
])
def test_stop_classifies_infrastructure_without_using_scientific_score(case, expected):
    from devbench.study.biology_execution import _pilot_infrastructure_failure
    r, s, d = record(), supervision(), detail()
    if case == "preflight": r.attempted = False
    elif case == "identity": r.model_identity_verified = False
    elif case == "integrity": r.integrity_status = "failed"
    elif case == "tools": r.trusted_tool_capture_complete = False
    elif case == "events": d = detail(event_capture_complete=False)
    elif case == "script": d = detail(script_execution_observed=False)
    elif case == "provider": r.completion_status = "provider_error"
    elif case == "timeout": r.completion_status = "timeout"
    elif case == "memory": s.failure_kind = "memory_monitor"
    elif case == "exit": s.exitcode = 1
    assert _pilot_infrastructure_failure(r, s, d) == expected


def test_bad_science_or_invalid_answer_with_complete_capture_does_not_stop():
    from devbench.study.biology_execution import _pilot_infrastructure_failure
    for status in ("completed", "invalid_output"):
        assert _pilot_infrastructure_failure(record(completion_status=status), supervision(), detail()) is None


def test_cli_admits_stop_flag_but_still_requires_execution_authorization(capsys):
    from devbench.study.cli import main
    assert main(["biology-run", "--plan", "/missing", "--bundles", "/missing", "--output", "/missing",
                 "--workspace-root", "/missing", "--stop-on-infrastructure-failure"]) == 2
    assert "authorization" in capsys.readouterr().err


@pytest.mark.parametrize("runtime_profile", ["host", "bulk"])
def test_second_worker_failure_stops_third_dispatch_and_retains_all_slots(monkeypatch, runtime_profile):
    from tests.test_biology_execution import plan, bundle_for
    from devbench.study import biology_execution as module, execution, cli_identity
    from devbench.study import supervision as supervisor_module
    from devbench.study.contracts import content_sha256
    study = plan(); bundle = bundle_for(study); calls = []
    parent = Path(__file__).resolve().parents[1] / "tmp"; parent.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="p", dir=parent) as temporary:
        root = Path(temporary); executable = root / "codex"; executable.write_text("offline fixture; never executed")
        executable_hash = execution._file_digest(executable)
        monkeypatch.setattr(module.shutil, "which", lambda value: str(executable))
        monkeypatch.setattr(execution, "_source_identity", lambda: "a" * 64)
        monkeypatch.setattr(cli_identity, "read_codex_identity", lambda value: {
            "version": "codex-cli 0.0.0-fixture", "executable_sha256": executable_hash})
        monkeypatch.setattr(cli_identity, "verify_codex_identity", lambda value: None)
        prepared = {}
        def supervise(worker, args, output, budget, **kwargs):
            if worker is module._biology_reference_worker:
                task = args[0][3]; thresholds = content_sha256(study.thresholds.model_dump(mode="json"))
                prepared.update(task_id=task, input_sha256=bundle.input_sha256, source_sha256="a" * 64,
                    reference_availability={"thresholds_sha256": thresholds,
                        "reference_availability_sha256": "e" * 64}, artifacts={})
            else:
                assert worker is (module._biology_bulk_attempt_worker if runtime_profile == "bulk"
                                  else module._biology_attempt_worker)
                calls.append(args[0][1].attempt_id)
            value = supervision()
            value.as_dict = lambda: {"failure_kind": None, "exitcode": 0}
            return value
        monkeypatch.setattr(supervisor_module, "supervise_coordinator", supervise)
        monkeypatch.setattr(module, "_validated_worker_result", lambda *args: prepared)
        def reference(value):
            common = dict(task_id=value["task_id"], input_sha256=bundle.input_sha256,
                          gene_universe_sha256="c" * 64)
            return SimpleNamespace(**common, thresholds_sha256=value["reference_availability"]["thresholds_sha256"]), common
        monkeypatch.setattr(module, "_read_preparation", reference)
        def collect(supervised, arguments):
            initial, output = arguments[2], arguments[7]
            changed = initial.model_copy(update={"attempted": True, "completion_status": "completed",
                "integrity_status": "verified", "model_identity_verified": True, "trusted_tool_capture_complete": True})
            # The second worker has an actual control-plane failure in this
            # simulated scheduler, irrespective of its scientific score.
            if len(calls) == 2: supervised.failure_kind = "memory_monitor"
            module._write(output / "execution.json", detail())
            return changed, False, {"safe_row_count": 0, "safe_scalar_count": 0,
                "execution_detail_path": str(output / "execution.json")}
        monkeypatch.setattr(module, "_collect_attempt", collect)
        result = module.execute_biology_study(study, {bundle.cohort_id: bundle}, root / "out",
            workspace_root=root / "w", allow_data_execution=True, allow_exec_preflight=True,
            stop_on_infrastructure_failure=True, participant_runtime_profile=runtime_profile)
        assert len(calls) == 2, result
        assert result["status"] == "stopped_infrastructure_failure"
        assert result["stop_reason"] == "supervision_failure:memory_monitor"
        assert result["all_model_slots_processed"] is False
        assert result["participant_runtime_profile"] == runtime_profile
        import json
        rows = json.loads((root / "out/records.json").read_text())["records"]
        assert len(rows) == 3 and rows[2]["attempted"] is False
