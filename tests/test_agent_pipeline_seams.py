"""Full offline fixture-agent pipeline, not a model-performance benchmark.

The CLI/auth handshake and permission preflight are explicit local doubles.
Outer coordinator workers run inline, so this does not test their process-tree
resource isolation. All staged files, command events, broker transport, numerical
workers, answer validation, grading, receipt sealing and report logic are real.
No provider is contacted; the reported identity is always offline-fixture.
"""
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import shlex
import sys
import tempfile
import textwrap
import time

import pytest


MODEL = "offline-fixture"
TOOLS = ("audit_expression_quality", "validate_biological_units",
         "validate_design_matrix", "condition_time_analysis")
pytestmark = pytest.mark.skipif(
    os.environ.get("DEVBENCH_RUN_LOCAL_FIXTURES") != "1",
    reason="The full offline fixture-agent pipeline requires explicit local-fixture opt-in",
)

_SOLVER = '''import importlib.util
import json
from pathlib import Path

REVISION = 1
TOOLS = ("audit_expression_quality", "validate_biological_units",
         "validate_design_matrix", "condition_time_analysis")


def main():
    work = Path(__file__).resolve().parent
    for module in ("devbench", "devbench_engine", "benchmarks", "experiments"):
        assert importlib.util.find_spec(module) is None, module
    assert not (work / "private").exists()
    task = json.loads((work / "task_inputs.json").read_text())
    assert not {"case_id", "generation_seed", "private_truth"} & task.keys()
    calls = 0
    if (work / "devharness_client.py").is_file():
        from devharness_client import call
        for tool in TOOLS:
            response = call(tool, options={})
            if response["status"] != "succeeded":
                raise RuntimeError("Offline fixture tool failed: " + tool + ": " + str(response.get("error")))
            calls += 1
        targets = response["result"]["target_results"]
        for target in targets:
            target["tool_evidence_ids"] = [response["receipt_id"]]
    else:
        targets = []
        for question in task["questions"]:
            targets.append({
                "target_id": question["target_id"], "estimand": question["estimand"],
                "units": question.get("measurement_units", question.get("units")),
                "contrast": question["contrast"],
                "reference_population": question["reference_population"],
                "prediction_reference": question["prediction_reference"],
                "claim_type": "failed", "estimate_status": "failed",
                "evidence_scope": "not_identified",
                "limitation_codes": ["offline_fixture_method_unavailable"],
            })
    answer = {
        "task_id": task["task_id"], "input_sha256": task["input_sha256"],
        "gene_universe_sha256": task["gene_universe_sha256"], "target_results": targets,
        "narrative_for_human_review": "Offline fixture only; revision " + str(REVISION),
    }
    if REVISION == 1:
        answer["fixture_intermediate_error"] = True
    (work / "final_discovery.json").write_text(json.dumps(answer, allow_nan=False))
    print(json.dumps({"fixture_revision": REVISION, "actual_tool_calls": calls}))


if __name__ == "__main__":
    main()
'''


def _fixture_cli(path):
    """Execute real shell commands and report their actual statuses as CLI events."""
    driver = r'''
import json
from pathlib import Path
import shlex
import subprocess
import sys
import time
import tomllib


def emit(value):
    print(json.dumps(value), flush=True)


def main():
    if sys.argv[1:] == ["--version"]:
        print("codex-cli 0.0.0-offline-fixture")
        return 0
    if sys.argv[1:] == ["login", "status"]:
        print("Logged in using ChatGPT (OFFLINE FIXTURE; no real authentication)")
        return 0
    assert sys.argv[1] == "exec", "Fixture accepts only its explicit offline commands"
    sys.stdin.read()
    work = Path.cwd().resolve()
    child_environment = None
    for index, argument in enumerate(sys.argv[:-1]):
        if argument == "-c" and sys.argv[index + 1].startswith("shell_environment_policy.set="):
            child_environment = tomllib.loads(sys.argv[index + 1])["shell_environment_policy"]["set"]
    assert child_environment and child_environment["DEVHARNESS_PYTHON"] == str(work / ".venv/bin/python")
    python = child_environment["DEVHARNESS_PYTHON"]
    run = shlex.join([python, str(work / "solve.py")])
    validate = shlex.join([python, "-I", "-B", str(work / "validate_submission.py"),
                          str(work / "final_discovery.json")])
    relative = "cat > solve.py <<'FIXTURE_SOLVER'\n" + SOLVER + "FIXTURE_SOLVER\n" + run
    absolute = ("cat > " + shlex.quote(str(work / "solve.py")) + " <<'FIXTURE_SOLVER'\n"
                + SOLVER.replace("REVISION = 1", "REVISION = 2") + "FIXTURE_SOLVER\n" + run)
    edit = (shlex.quote(python) + " - <<'FIXTURE_EDIT'\nfrom pathlib import Path\n"
            + "path = Path(" + repr(str(work / "solve.py")) + ")\n"
            + "path.write_text(path.read_text().replace('REVISION = 2', 'REVISION = 3'))\n"
            + "FIXTURE_EDIT\n" + run)
    steps = (("relative-heredoc", relative, 0), ("check-invalid", validate, 1),
             ("absolute-heredoc", absolute, 0), ("python-edit-rerun", edit, 0),
             ("check-final", validate, 0))
    emit({"type": "thread.started", "thread_id": "offline-fixture-" + work.name})
    for name, command, expected in steps:
        item = {"id": name, "type": "command_execution", "command": command, "status": "in_progress"}
        emit({"type": "item.started", "item": item})
        # Real agents have time between commands. These small gaps let the
        # coordinator consume starts/completions before the next file mutation.
        time.sleep(0.1)
        result = subprocess.run(command, shell=True, executable="/bin/sh", cwd=work,
                                env=child_environment, capture_output=True, text=True, timeout=90)
        item.update(status="completed" if result.returncode == 0 else "failed",
                    exit_code=result.returncode, aggregated_output=result.stdout + result.stderr)
        emit({"type": "item.completed", "item": item})
        time.sleep(0.1)
        if result.returncode != expected:
            emit({"type": "turn.failed", "error": {"message": "Unexpected offline fixture command status"}})
            return 1
    emit({"type": "turn.completed", "model": "offline-fixture",
          "usage": {"input_tokens": 0, "output_tokens": 0}})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''
    path.write_text("#!" + sys.executable + "\nSOLVER = " + repr(_SOLVER) + "\n" + textwrap.dedent(driver))
    path.chmod(0o700)
    return path


def _offline_controls(monkeypatch, executable):
    """Double only external preflight and outer coordinator process supervision."""
    from devbench.study import exec_preflight, permissions, supervision
    from devbench.study.cli_identity import read_codex_identity

    workers = []

    def inline_supervisor(worker, args, output, budget, *, allow_data_execution=False, **options):
        assert allow_data_execution is True
        budget, output = supervision.validate_supervision_configuration(worker, args, output, budget, **options)
        workers.append(worker.__name__)
        started = time.monotonic()
        worker(*args, allow_data_execution=True)
        result_path = output / "attempt_result.json"
        raw = result_path.read_bytes()
        receipt = supervision.ArtifactReceipt(result_path.name, "result", result_path,
                                               hashlib.sha256(raw).hexdigest(), len(raw))
        return supervision.SupervisionResult(
            exitcode=0, failure_kind=None, error=None, interrupted=False, resource_failure=False,
            elapsed_sec=time.monotonic() - started, RSS_MB=None, rss_coverage="unavailable",
            result_present=True, artifacts=(receipt,), artifact_errors=(),
        )

    def permission_fixture(boundary, *args, **kwargs):
        return {"verified": True, "configuration": boundary.config_overrides(),
                "scope": "offline_fixture_configuration_only_not_os_isolation"}

    def preflight_fixture(adapter, output, *, allow_model_execution=False):
        assert allow_model_execution is True
        # The fixture CLI supplies its own explicit identity; there is no external
        # provider telemetry to capture and no reason to create an HTTP listener.
        adapter.model_identity_output = None
        evidence = {
            "verified": True, "model_identity_verified": True, "actual_model": MODEL,
            "codex_identity": read_codex_identity(executable),
            "scope": "offline_fixture_only_not_live_permission_or_provider_evidence",
            "inference_dispatch_attempted": False, "duration_sec": 0.0,
            "billing": {"tokens": 0, "cost_usd": 0.0},
        }
        output.mkdir(mode=0o700, parents=True)
        supervision.write_coordinator_json(output / "evidence.json", evidence, allow_data_execution=True)
        return evidence

    monkeypatch.setattr(supervision, "supervise_coordinator", inline_supervisor)
    monkeypatch.setattr(permissions, "probe_boundary", permission_fixture)
    monkeypatch.setattr(exec_preflight, "run_exec_preflight", preflight_fixture)
    return workers


def test_full_offline_fixture_agent_pipeline_preserves_execution_and_receipt_seams(monkeypatch):
    from devbench.study.biology_dashboard import build_biology_dashboard
    from devbench.study.biology_datasets import prepare_biology_dataset
    from devbench.study.biology_execution import execute_biology_study
    from devbench.study.biology_metrics import analyze_biology_attempts
    from devbench.study.biology_planning import default_biology_recipe, default_biology_study, validate_biology_plan
    from devbench.study.biology_reporting import load_biology_report_records
    from devbench.study.contracts import RunBudget
    from devbench.study.evidence import verify_receipts

    parent = Path(__file__).resolve().parents[1] / "tmp"
    parent.mkdir(exist_ok=True)
    # The short prefix leaves room for the real AF_UNIX broker path.
    with tempfile.TemporaryDirectory(prefix="ap-", dir=parent) as temporary:
        root = Path(temporary).resolve()
        executable = _fixture_cli(root / "fixture-codex")
        workers = _offline_controls(monkeypatch, executable)
        recipe = default_biology_recipe("T03-baseline-only", num_genes=32).model_copy(
            update={"generation_seed": 317})
        proposed = default_biology_study(study_id="offline-agent-pipeline-fixture", num_genes=120,
                                         requested_model=MODEL)
        plan = validate_biology_plan(proposed.model_copy(update={
            "recipes": (recipe,), "budget": RunBudget(timeout_sec=180, max_memory_mb=2048, cpu_threads=1),
        }))
        bundle = prepare_biology_dataset(recipe, root / "data", plan_identity=plan.sha256(),
                                         cohort_id=recipe.recipe_id, allow_data_execution=True)
        result = execute_biology_study(
            plan, {recipe.recipe_id: bundle}, root / "out", workspace_root=root / "w",
            codex_executable=str(executable), allow_data_execution=True, allow_exec_preflight=True,
            max_live_attempts=3, max_provider_sessions=6,
        )
        attempt_errors = []
        for artifact in result["attempt_artifacts"].values():
            detail_path = artifact.get("execution_detail_path")
            if detail_path is not None:
                attempt_errors.extend(json.loads(Path(detail_path).read_text()).get("errors", []))
        assert result["status"] == "finished", [*result["errors"], *attempt_errors]
        assert workers == ["_biology_reference_worker", *["_biology_attempt_worker"] * 3]
        assert result["attempted_count"] == result["processed_attempt_count"] == 3
        assert result["comprehensive_execution"] is True
        assert result["codex_version"] == "codex-cli 0.0.0-offline-fixture"
        assert result["preflight_accounting"]["confirmed_provider_session_attempts"] == 0
        records, storage = load_biology_report_records(
            root / "out/records.json", plan, execution_receipt_sha256=result["execution_manifest_sha256"],
            allow_result_analysis=True,
        )
        assert storage["retained_root_verified"] is True
        assert {row.condition for row in records} == {"baseline", "optional", "enforced"}
        validator_hashes = set()
        for record in records:
            attempt = root / "out/attempts" / record.attempt_id
            details = json.loads((attempt / "execution.json").read_text())
            metadata = details["adapter"]
            assert record.accepted_final is True, details["errors"]
            assert record.integrity_status == "verified" and record.completion_status == "completed"
            assert record.actual_model == MODEL and record.model_identity_verified is True
            assert isinstance(record.scientific_success, bool)
            assert isinstance(details.get("grade"), dict)
            assert record.observed_script_execution_count == 3
            assert metadata["event_capture_complete"] is True and metadata["script_execution_observed"] is True
            scripts = metadata["script_executions"]
            assert record.final_execution_id == metadata["final_execution_id"] == scripts[-1]["execution_id"]
            workspace = Path(metadata["permission_boundary"]["workspace"])
            assert scripts[-1]["script_sha256_at_completion"] == hashlib.sha256((workspace / "solve.py").read_bytes()).hexdigest()
            assert scripts[-1]["answer_sha256_at_completion"] == hashlib.sha256((workspace / "final_discovery.json").read_bytes()).hexdigest()
            assert len({entry["script_sha256_at_completion"] for entry in scripts}) == 3
            commands = {entry["cli_item_id"]: entry for entry in metadata["commands"]}
            assert commands["check-invalid"]["exit_code"] == 1
            invalid = json.loads(commands["check-invalid"]["output_excerpt"])
            assert invalid["valid"] is False
            assert any(error["path"] == "$.fixture_intermediate_error" for error in invalid["errors"])
            assert json.loads(commands["check-final"]["output_excerpt"])["valid"] is True
            assert commands["check-final"]["direct_script_invocation"] is False
            validator = workspace / "validate_submission.py"
            digest = hashlib.sha256(validator.read_bytes()).hexdigest()
            validator_hashes.add(digest)
            checkpoint = json.loads((attempt / "staging_checkpoint.json").read_text())
            assert checkpoint["staged_hashes"][str(validator)] == digest
            assert shlex.join((str(workspace / ".venv/bin/python"), "-I", "-B", str(validator),
                               str(workspace / "final_discovery.json"))) in (workspace / "TASK_README.md").read_text()
            if record.condition == "baseline":
                assert record.trusted_tool_calls == record.final_execution_tool_calls == 0
                assert not (workspace / "devharness_client.py").exists()
                continue
            assert record.trusted_tool_calls == record.trusted_receipt_count == 3 * len(TOOLS)
            assert record.final_execution_tool_calls == len(TOOLS)
            assert isinstance(record.workflow_passed, bool) and "workflow" in details
            retained = json.loads((attempt / "retained_tool_root.json").read_text())
            evidence = verify_receipts(
                attempt / "receipts", expected_receipt=retained, expected_input_sha256=bundle.input_sha256,
                expected_implementation_sha256=result["source_sha256"], allow_evidence_read=True,
            )
            assert evidence.execution_scoping is True and len(evidence.executions) == 3
            counts = Counter(event["execution_id"] for event in evidence.events)
            assert counts == {entry["execution_id"]: len(TOOLS) for entry in scripts}
            selected = {event["receipt_id"] for event in evidence.events
                        if event["execution_id"] == record.final_execution_id}
            answer = json.loads((workspace / "final_discovery.json").read_text())
            assert all(set(target["tool_evidence_ids"]) <= selected and target["tool_evidence_ids"]
                       for target in answer["target_results"])
        assert len(validator_hashes) == 1

        analysis = analyze_biology_attempts(records, plan, allow_result_analysis=True, bootstrap_replicates=10)
        assert analysis["attempted_count"] == 3 and analysis["quarantined_record_count"] == 0
        groups = {row["condition"]: row for row in analysis["group_summaries"]}
        assert {condition: row["tool_adoption_count"] for condition, row in groups.items()} == {
            "baseline": 0, "optional": 1, "enforced": 1}
        assert sum(row["tool_calls_all_executions_known_subtotal"] for row in groups.values()) == 24
        assert sum(row["final_execution_tool_calls_known_subtotal"] for row in groups.values()) == 8
        report = build_biology_dashboard(plan, analysis, root / "report", allow_result_reporting=True)
        summary = json.loads(Path(report["paths"]["summary"]).read_text())
        assert summary["attempted_count"] == 3
        assert all(row["actual_model"] == MODEL for row in summary["run_rows"])
        assert summary["bootstrap"]["performed"] is False
        for name, path in report["paths"].items():
            assert hashlib.sha256(Path(path).read_bytes()).hexdigest() == report["hashes"][name]
