"""Supervised three-condition execution over frozen public processed inputs.

Importing this module performs no data reads and starts no participant.  The
entry point requires literal authorization before it inspects any argument.
"""
from __future__ import annotations

from collections.abc import Mapping
import hashlib
import re
import shutil
from pathlib import Path
import sys
import tempfile
import time


_REPOSITORY = Path(__file__).resolve().parents[2]
_SOURCE_PACKAGES = ("devbench", "devbench_engine", "devharness", "devharness_core", "devharness_server",
                    "benchmarks", "devharness_controlled")

_COMMON_GUIDANCE = """Read TASK_README.md, task_inputs.json and answer_schema.json. Analyze only the supplied
public processed expression and metadata. Preserve the declared measurement layer:
developmental observations are continuous RPKM and SEQC inputs are raw counts.
Collection stage is a reference axis, not universal pseudotime truth. Repeated
specimens are not independent animals, and SEQC technical libraries are not patients.
Do not claim complete biological truth, population inference, causal regulation or
clinical validation. Do not download data, install packages, use external services,
read coordinator files or inspect another participant workspace.

Treat staged inputs, schema, guidance and the tool client as read-only. Use bounded
reads and compact summaries. Write a standalone solve.py with a module-level
if __name__ == '__main__': guard, execute it using the exact staged Python command,
and have it write finite final_discovery.json matching answer_schema.json. Bind the
answer to the exact task_id and input_sha256. The saved file, rather than chat text,
is evaluated against a limited private reference task after execution.
"""

_ARM_GUIDANCE = {
    "baseline": "No DevHarness endpoint is supplied. Use ordinary Python and leave tool_evidence_ids empty.",
    "optional": "DevHarness tools are optional; ordinary methods, mixed methods and no tool use are all allowed. Never invent a receipt.",
    "enforced": "Report acceptance requires host-verified DevHarness receipts whose successful results bind the submitted answer. Preserve exact receipt IDs.",
}


def public_participant_guidance(condition):
    if condition not in _ARM_GUIDANCE:
        raise ValueError("Unknown public study condition")
    return _COMMON_GUIDANCE + "\n" + _ARM_GUIDANCE[condition] + "\n"


def _write(path, value, *, replace=False):
    from .supervision import write_coordinator_json
    write_coordinator_json(path, value, replace=replace, allow_data_execution=True)


def _json(path, limit=64_000_000):
    from .evidence import _read, decode_json
    return decode_json(_read(Path(path), limit))


def _record(value):
    from .public_records import parse_public_attempt
    return parse_public_attempt(value)


def validate_public_execution_configuration(plan, prepared_bundles):
    """Validate authored contracts without opening public or private bundle files."""
    from .public_study import DATASETS, PreparedPublicTask, PublicStudyPlan, public_identity
    if not isinstance(plan, PublicStudyPlan):
        raise TypeError("A PublicStudyPlan is required")
    plan = PublicStudyPlan.model_validate_json(plan.model_dump_json())
    if (plan.requested_model is None
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_./:-]*", plan.requested_model) is None):
        raise ValueError("A pinned requested_model is required")
    if not isinstance(prepared_bundles, Mapping) or set(prepared_bundles) != set(plan.datasets):
        raise ValueError("Prepared public tasks must cover exactly the frozen datasets")
    bundles = {}
    for dataset_id in plan.datasets:
        bundle = prepared_bundles[dataset_id]
        if not isinstance(bundle, PreparedPublicTask):
            raise TypeError("Each public dataset requires a PreparedPublicTask")
        bundle = PreparedPublicTask.model_validate_json(bundle.model_dump_json())
        root = Path(bundle.root)
        if not root.is_absolute() or ".." in root.parts:
            raise ValueError("Prepared public roots must be absolute without traversal")
        if (bundle.dataset_id != dataset_id or bundle.plan_identity != plan.sha256()
                or bundle.task_id != public_identity(plan, "task", dataset_id)
                or bundle.source_id != DATASETS[dataset_id][0]):
            raise ValueError("Prepared public task differs from the frozen plan")
        bundles[dataset_id] = bundle
    if len({bundle.root for bundle in bundles.values()}) != len(bundles):
        raise ValueError("Prepared public task roots must be distinct")
    return plan, bundles


def initial_public_attempt_records(plan, prepared_bundles):
    from .public_records import PublicAttemptRecord
    from .public_study import schedule_public_attempts
    from .records import AccountingCompleteness
    plan, bundles = validate_public_execution_configuration(plan, prepared_bundles)
    rows = []
    for attempt in schedule_public_attempts(plan):
        bundle = bundles[attempt["dataset_id"]]
        rows.append(PublicAttemptRecord(
            **attempt, study_id=plan.study_id, source_id=bundle.source_id,
            independence_id=bundle.independence_id, input_sha256=bundle.input_sha256,
            reference_sha256=bundle.reference_sha256, preparation_sha256=bundle.preparation_sha256,
            shared_runtime_sha256=None, harness_sha256=None, requested_model=plan.requested_model,
            actual_model=None, model_identity_sha256=None, model_identity_verified=False,
            attempted=False, completion_status="not_started", accepted_final=False,
            report_accepted=None, workflow_passed=None, integrity_status="unavailable",
            reference_task_eligible=None, reference_task_success=None, complete_biological_correctness=None,
            metrics={}, errors=(), metric_missingness={}, trusted_tool_capture_complete=False,
            trusted_tool_calls=None, trusted_receipt_count=None, elapsed_sec=None, RSS_MB=None,
            tokens=None, cost=None, accounting_completeness=AccountingCompleteness(
                elapsed_sec="unavailable", RSS_MB="unavailable", tokens="unavailable", cost="unavailable")))
    return rows


def apply_public_grade(values, grade):
    """Project the producer's limited public grade without touching acceptance."""
    from .records import validate_finite_json
    if not isinstance(values, dict) or not isinstance(grade, dict):
        raise TypeError("Public grade projection requires dictionaries")
    if grade.get("task_id", values.get("task_id")) != values.get("task_id"):
        raise ValueError("Public grade task identity differs from the scheduled task")
    if grade.get("input_sha256", values.get("input_sha256")) != values.get("input_sha256"):
        raise ValueError("Public grade input identity differs from the scheduled input")
    if grade.get("complete_biological_correctness") is not None:
        raise ValueError("Public references cannot establish complete biological correctness")
    success = grade.get("reference_task_success")
    metrics = grade.get("metrics", {})
    errors = grade.get("errors", [])
    missingness = grade.get("metric_missingness", {})
    if (success is not None and type(success) is not bool) or not isinstance(metrics, dict):
        raise ValueError("Malformed public grade")
    if (not isinstance(errors, (list, tuple)) or len(errors) > 1000
            or any(not isinstance(item, str) or not 1 <= len(item) <= 2000 for item in errors)):
        raise ValueError("Malformed public grade errors")
    if not isinstance(missingness, dict):
        raise ValueError("Malformed public metric missingness")
    missing = set(validate_finite_json(metrics, path=""))
    allowed_missing = missing | ({"reference_task_success"} if success is None else set())
    if not missing <= set(missingness) <= allowed_missing:
        raise ValueError("Public grade missingness does not match unavailable metrics")
    values.update(reference_task_success=success, complete_biological_correctness=None,
                  metrics=metrics, errors=list(errors), metric_missingness=missingness)


def _report_acceptance(condition, answer, workflow_passed=None):
    """Adjudicate evidence claims separately from the public task grade."""
    claimed_receipts = bool(answer.tool_evidence_ids)
    if condition == "baseline":
        return not claimed_receipts
    if condition == "optional":
        return not claimed_receipts or workflow_passed is True
    if condition == "enforced":
        return workflow_passed is True
    raise ValueError("Unknown public study condition")


def _runtime_identity(roots, plan, executable):
    from .contracts import content_sha256
    from .cli_identity import codex_component_hashes
    from .execution import _file_digest, _tree_digest
    return content_sha256({
        "runtime_files": {str(path): _tree_digest(path, allowed_symlink_targets=roots) for path in roots},
        "python": sys.version,
        "budget": plan.budget.model_dump(mode="json"),
        "codex_executable_sha256": _file_digest(executable),
        "codex_component_sha256": codex_component_hashes(executable),
        "scope": "shared_public_runtime_source_and_native_bytes_excluding_python_caches",
    })


def _stage_public_inputs(bundle, workspace, condition, public_task, *, allow_data_execution=False):
    if allow_data_execution is not True:
        raise PermissionError("Public input staging is disabled")
    from .contracts import content_sha256
    from .execution import _file_digest
    from .public_answers import PublicAnswer
    from .public_io import input_inventory
    from .public_tools import public_tool_menu
    inventory = input_inventory(Path(bundle.root) / "inputs")
    hashes = {}
    for relative, receipt in inventory.items():
        source, target = Path(bundle.root) / "inputs" / relative, workspace / "inputs" / relative
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with source.open("rb") as reader, target.open("xb") as writer:
            shutil.copyfileobj(reader, writer, length=1_048_576)
        target.chmod(0o444)
        hashes[str(target)] = receipt["sha256"]
    task = dict(public_task)
    task.update(input_sha256=bundle.input_sha256, expression_path=str(workspace / "inputs/counts.zarr"),
                metadata_path=str(workspace / "inputs/metadata.csv"),
                calibration_path=str(workspace / "inputs/calibration.json"))
    _write(workspace / "task_inputs.json", task)
    _write(workspace / "input_manifest.json", {"record_format": "devbench.public-input-manifest/1",
                                                "input_sha256": bundle.input_sha256, "files": inventory})
    _write(workspace / "answer_schema.json", PublicAnswer.model_json_schema())
    from .submission_validation import stage_submission_validator
    validator = stage_submission_validator(workspace, "public", max_answer_bytes=8_000_000)
    texts = {
        "TASK_README.md": public_participant_guidance(condition)
            + f"\nExecute: {workspace / '.venv/bin/python'} {workspace / 'solve.py'}\n"
            + f"\nBefore submitting, run this public contract check in a separate command:\n{validator.command}\n"
            + "Fix reported contract errors, rerun solve.py, then validate again. This check supplies no grading references or scientific score.\n",
        "sdk_cheatsheet.py": '"""Public-task examples only; execute analysis from solve.py main."""\n',
    }
    if condition != "baseline":
        texts["devharness_client.py"] = (Path(__file__).parent / "client.py").read_text()
        texts["sdk_cheatsheet.py"] += (
            "from devharness_client import call\n"
            "# Read tool_menu.json. Calls accept only options; all input paths are coordinator-bound.\n"
            "# Copy only actual successful receipt IDs into tool_evidence_ids.\n")
        _write(workspace / "tool_menu.json", public_tool_menu(task))
        texts["TASK_README.md"] += "\nAvailable host tools and options are listed in tool_menu.json.\n"
    for name, text in texts.items():
        with (workspace / name).open("x") as handle:
            handle.write(text)
    names = ("task_inputs.json", "input_manifest.json", "answer_schema.json", *texts,
             *(("tool_menu.json",) if condition != "baseline" else ()))
    for name in names:
        path = workspace / name
        path.chmod(0o444)
        hashes[str(path)] = _file_digest(path)
    protected = (workspace / "inputs", workspace / ".venv", workspace / "control",
                 *(workspace / name for name in names), *validator.protected_paths)
    hashes.update(validator.hashes)
    harness_hash = content_sha256({name: hashes[str(workspace / name)] for name in names
                                   if name in texts or name == "tool_menu.json"})
    return task, protected, hashes, harness_hash


def _verify_integrity(plan, bundle, source_hash, executable, roots, runtime_hash, staged_hashes):
    from .execution import _file_digest, _source_identity
    from .public_study import verify_public_task
    verify_public_task(bundle, plan, allow_data_execution=True)
    if (_source_identity() != source_hash or _runtime_identity(roots, plan, executable) != runtime_hash
            or any(_file_digest(Path(path)) != digest for path, digest in staged_hashes.items())):
        raise ValueError("Public source, runtime or protected input integrity changed")


def _load_reference(bundle):
    from .evidence import _read, decode_json
    raw = _read(Path(bundle.root) / "private/reference.json", 16_000_000)
    if hashlib.sha256(raw).hexdigest() != bundle.reference_sha256:
        raise ValueError("Retained public reference bytes changed")
    return decode_json(raw)


def _reference_task_eligibility(bundle, reference):
    """Expose only answer-independent eligibility, never private expected values."""
    if (not isinstance(reference, dict) or reference.get("record_format") != "devbench.public-reference/1"
            or reference.get("task_id") != bundle.task_id
            or reference.get("input_sha256") != bundle.input_sha256
            or reference.get("source_id") != bundle.source_id
            or reference.get("complete_biological_truth") is not False):
        raise ValueError("Public reference does not bind the prepared task")
    if reference.get("kind") == "developmental_observation":
        genes = reference.get("gene_ids")
        if not isinstance(genes, list) or not genes or len(set(genes)) != len(genes):
            raise ValueError("Developmental reference eligibility is malformed")
        return {"reference_task_eligible": True,
                "scope": "observed_library_means_only", "reference_sha256": bundle.reference_sha256}
    if reference.get("kind") == "seqc_normalization":
        eligible = reference.get("eligible_evaluation_controls")
        ratios = reference.get("evaluation_ratios_A_over_B")
        if (not isinstance(eligible, list) or len(set(eligible)) != len(eligible)
                or not isinstance(ratios, dict) or not set(eligible) <= set(ratios)):
            raise ValueError("SEQC reference eligibility is malformed")
        return {"reference_task_eligible": bool(eligible),
                "scope": "held_out_nominal_ERCC_ratios", "eligible_control_count": len(eligible),
                "reference_sha256": bundle.reference_sha256}
    raise ValueError("Unknown public reference kind")


def _public_attempt_worker(arguments, preflight_only=False, *, allow_data_execution=False):
    if allow_data_execution is not True:
        raise PermissionError("Public participant execution is disabled")
    from devbench.staging import stage_python
    from .adapter import StudyCodeActionAdapter
    from .broker import BrokerPolicy
    from .contracts import canonical_json_bytes, content_sha256
    from .evidence import verify_receipts
    from .execution import _failure_outcome, _observed_accounting, _runtime_roots, _source_identity
    from .permissions import PermissionBoundary, probe_boundary
    from .public_answers import PublicAnswer
    from .public_grading import grade_public_answer
    from .public_study import verify_public_task
    from .public_tools import PublicToolBroker, verify_public_workflow
    from .transport import BrokerTransport

    plan, attempt, initial, bundle, workspace, output, private_roots, source_hash, expected_runtime, executable = arguments
    values = {} if preflight_only else initial.model_dump(mode="json")
    details = {"attempt_id": attempt["attempt_id"], "errors": [],
               "reference_sha256": bundle.reference_sha256,
               "resource_scope": "one_supervised_owned_process_tree"}
    adapter = broker = transport = None
    answer = parsed = evidence = None
    roots, staged = (), {}
    stage, interrupted = "preparation", False
    started = time.monotonic()
    try:
        if _source_identity() != source_hash:
            raise RuntimeError("Implementation changed before public participant preparation")
        verify_public_task(bundle, plan, allow_data_execution=True)
        public_task = _json(Path(bundle.root) / "inputs/task_inputs.json", 2_000_000)
        environment = stage_python(workspace, assisted=False)
        roots = _runtime_roots(workspace, environment)
        runtime_hash = _runtime_identity(roots, plan, executable)
        if expected_runtime is not None and runtime_hash != expected_runtime:
            raise ValueError("Shared runtime differs across public study conditions")
        values["shared_runtime_sha256"] = runtime_hash
        public_task, protected, staged, values["harness_sha256"] = _stage_public_inputs(
            bundle, workspace, attempt["condition"], public_task, allow_data_execution=True)
        _write(output / "staging_checkpoint.json", {"attempt_id": attempt["attempt_id"],
            "shared_runtime_sha256": runtime_hash, "harness_sha256": values["harness_sha256"],
            "runtime_roots": [str(root) for root in roots], "staged_hashes": staged})
        (workspace / "control").mkdir(mode=0o700)
        assisted = attempt["condition"] != "baseline"
        boundary = PermissionBoundary(workspace=workspace, private_roots=tuple(private_roots), runtime_roots=roots,
            protected_paths=protected, socket_path=workspace / "control/tools.sock" if assisted else None)
        probe = probe_boundary(boundary, executable, Path(environment["python"]), allow_configuration_probe=True)
        details["boundary_probe"] = probe
        if probe.get("verified") is not True or probe.get("configuration") != boundary.config_overrides():
            raise RuntimeError("Exact public participant boundary could not be verified")
        _verify_integrity(plan, bundle, source_hash, executable, roots, runtime_hash, staged)
        if assisted:
            policy = BrokerPolicy(condition=attempt["condition"], input_sha256=bundle.input_sha256,
                measurement_type=public_task["measurement"], gene_ids=tuple(public_task["gene_ids"]),
                population_inference_allowed=False, unit_provenance_verified=False,
                time_col="stage" if public_task["kind"] == "developmental_observation" else "unused_time_axis",
                biological_unit_column=("specimen_label" if public_task["kind"] == "developmental_observation"
                                        else "library_id"), timeout_sec=plan.budget.timeout_sec,
                max_memory_mb=plan.budget.max_memory_mb, implementation_sha256=source_hash)
            aliases = {str(workspace / "inputs/counts.zarr"): Path(bundle.root) / "inputs/counts.zarr",
                       str(workspace / "inputs/metadata.csv"): Path(bundle.root) / "inputs/metadata.csv",
                       str(workspace / "inputs/calibration.json"): Path(bundle.root) / "inputs/calibration.json"}
            broker = PublicToolBroker(policy, aliases, output / "receipts", public_task=public_task,
                public_input_root=Path(bundle.root) / "inputs", allow_data_execution=True, defer_start=True)
            transport = BrokerTransport(broker, boundary.socket_path, allow_data_execution=True)
        def checkpoint(observed):
            _write(output / "dispatch_checkpoint.json", {"attempt_id": attempt["attempt_id"],
                                                          "attempted": True, **observed})
        adapter = StudyCodeActionAdapter(boundary=boundary, requested_model=plan.requested_model,
            condition=attempt["condition"], executable=str(executable), timeout_sec=plan.budget.timeout_sec,
            max_mem_mb=plan.budget.max_memory_mb, thread_limit=plan.budget.cpu_threads,
            tool_capability_file=transport.capability_file if transport else None,
            coordinator_dispatch_callback=checkpoint, model_identity_output=output / "model_identity.json")
        from .exec_preflight import run_exec_preflight
        if broker is not None:
            broker.enable_execution_scopes()
            adapter.execution_observer = broker.observe_execution
        stage = "exec_preflight"
        details["exec_preflight"] = run_exec_preflight(adapter, output / "exec-preflight", allow_model_execution=True)
        if details["exec_preflight"].get("verified") is not True:
            raise RuntimeError("Exec-path permission preflight failed; scored participant was not launched")
        if not preflight_only:
            from .exec_preflight import require_scored_model_identity
            require_scored_model_identity(details["exec_preflight"])
            if broker is not None:
                details["tool_analysis_window"] = broker.start_analysis()
            stage = "participant_execution"
            answer = adapter.execute("Read the public processed-data task instructions.", workspace,
                                     allow_data_execution=True)
            stage = "execution_capture_validation"
            if adapter.metadata.get("event_capture_complete") is not True:
                raise RuntimeError("Coordinator execution-event capture is incomplete")
            stage = "script_provenance_validation"
            if adapter.metadata.get("script_execution_observed") is not True:
                raise RuntimeError("Direct solve.py execution proof is missing")
            values["completion_status"] = "completed"
    except KeyboardInterrupt:
        interrupted = True
        values["completion_status"] = "tool_error" if adapter and adapter.metadata.get("inference_dispatch_attempted") else "blocked"
        details["errors"].append("Coordinator interrupted")
    except Exception as error:
        metadata = adapter.metadata if adapter else {}
        status, code = _failure_outcome(error, stage=stage,
            attempted=metadata.get("inference_dispatch_attempted") is True, metadata=metadata)
        values["completion_status"] = status
        details["errors"].append(f"{stage}:{code}:{type(error).__name__}: {error}"[:2000])
    finally:
        metadata = adapter.metadata if adapter else {}
        values["attempted"] = metadata.get("inference_dispatch_attempted") is True
        details["adapter"] = metadata
        transport_ok, retained = False, None
        if transport is not None:
            try:
                details["transport"] = transport.close()
                transport_ok = details["transport"].get("transport_stopped") is True
            except Exception as error:
                details["errors"].append(f"transport:{type(error).__name__}: {error}"[:2000])
        if broker is not None:
            try:
                retained = broker.close()
                _write(output / "retained_tool_root.json", retained)
                details["retained_tool_root"] = retained
            except Exception as error:
                details["errors"].append(f"broker_seal:{type(error).__name__}: {error}"[:2000])
        values.update(_observed_accounting(metadata, time.monotonic() - started))
        values["accounting_completeness"] = values["accounting_completeness"].model_dump(mode="json")
        if values["attempted"] and not preflight_only:
            values["actual_model"] = metadata.get("actual_model")
            values["model_identity_verified"] = metadata.get("model_identity_verified") is True
            if values["actual_model"] is not None:
                values["model_identity_sha256"] = content_sha256({"actual_model": values["actual_model"],
                    "origin": metadata.get("model_identity_origin"), "verified": values["model_identity_verified"]})
            try:
                _verify_integrity(plan, bundle, source_hash, executable, roots,
                                  values["shared_runtime_sha256"], staged)
                from .cli_identity import verify_codex_identity
                verify_codex_identity(details["exec_preflight"]["codex_identity"])
                values["integrity_status"] = "verified"
            except Exception as error:
                values["integrity_status"] = "failed"
                details["errors"].append(f"integrity:{type(error).__name__}: {error}"[:2000])
        if not preflight_only and values["attempted"] and values["integrity_status"] == "verified":
            assisted = attempt["condition"] != "baseline"
            if not assisted:
                values.update(trusted_tool_capture_complete=True, trusted_tool_calls=0, trusted_receipt_count=0)
                values["final_execution_tool_calls"] = 0 if values.get("final_execution_id") else None
            elif retained is not None and transport_ok:
                try:
                    evidence = verify_receipts(output / "receipts", expected_receipt=retained,
                        expected_input_sha256=bundle.input_sha256, expected_implementation_sha256=source_hash,
                        allow_evidence_read=True)
                    if evidence.condition != attempt["condition"]:
                        raise ValueError("Recorded tool condition differs from scheduled arm")
                    values.update(trusted_tool_capture_complete=True, trusted_tool_calls=len(evidence.events),
                                  trusted_receipt_count=len(evidence.events))
                    from .execution_usage import selected_tool_calls
                    values["final_execution_tool_calls"] = selected_tool_calls(evidence, values.get("final_execution_id"))
                except Exception as error:
                    details["errors"].append(f"receipts:{type(error).__name__}: {error}"[:2000])
            if answer is not None and values["completion_status"] == "completed":
                try:
                    parsed = PublicAnswer.model_validate_json(canonical_json_bytes(answer))
                    if parsed.task_id != bundle.task_id or parsed.input_sha256 != bundle.input_sha256:
                        raise ValueError("Answer identity differs from the fixed public task")
                    values["accepted_final"] = True
                    values["report_accepted"] = _report_acceptance(attempt["condition"], parsed)
                except Exception as error:
                    values["completion_status"] = "invalid_output"
                    details["errors"].append(f"answer:{type(error).__name__}: {error}"[:2000])
            if parsed is not None and evidence is not None:
                try:
                    workflow = verify_public_workflow(parsed, evidence, public_task, allow_evidence_read=True)
                    details["workflow"] = workflow
                    values["workflow_passed"] = workflow["workflow_passed"]
                    values["report_accepted"] = _report_acceptance(
                        attempt["condition"], parsed, workflow["workflow_passed"])
                except Exception as error:
                    details["errors"].append(f"workflow:{type(error).__name__}: {error}"[:2000])
            try:
                reference = _load_reference(bundle)
                grade = grade_public_answer(parsed if values["accepted_final"] else None,
                    input_root=Path(bundle.root) / "inputs", reference=reference, plan=plan,
                    allow_data_execution=True)
                details["grade"] = grade
                apply_public_grade(values, grade)
            except Exception as error:
                details["errors"].append(f"grading:{type(error).__name__}: {error}"[:2000])
        if preflight_only:
            from .preflight_only import finish_probe_worker
            finish_probe_worker(output, attempt["condition"], details, interrupted=interrupted, broker=broker,
                                adapter=adapter, runtime_sha256=values.get("shared_runtime_sha256"))
        else:
            _write(output / "execution.json", details)
            _write(output / "attempt_result.json", {"record": _record(values).model_dump(mode="json"),
                                                     "interrupted": interrupted})


def _validated_result(supervision, output):
    from .evidence import _read, decode_json
    receipt = next((item for item in supervision.artifacts if item.kind == "result"), None)
    if receipt is None or receipt.path != output / "attempt_result.json":
        raise ValueError("No bounded public worker result was captured")
    raw = _read(receipt.path, 64_000_000)
    if hashlib.sha256(raw).hexdigest() != receipt.sha256:
        raise ValueError("Public worker result changed after supervision")
    return decode_json(raw)


def _collect_attempt(supervision, arguments):
    from .execution import _file_digest
    plan, attempt, initial, bundle, workspace, output, private_roots, source_hash, expected_runtime, executable = arguments
    record, interrupted = initial, supervision.interrupted
    detail = supervision.as_dict()
    try:
        result = _validated_result(supervision, output)
        candidate = _record(result["record"])
        for name in ("attempt_id", "study_id", "plan_identity", "task_id", "dataset_id", "condition",
                     "repetition", "execution_order", "source_id", "independence_id", "input_sha256",
                     "reference_sha256", "preparation_sha256", "requested_model"):
            if getattr(candidate, name) != getattr(initial, name):
                raise ValueError("Public worker result differs from the scheduled identity")
        record = candidate
        interrupted = interrupted or result.get("interrupted") is True
    except Exception as error:
        detail["record_recovery_error"] = f"{type(error).__name__}: {error}"[:2000]
        values = record.model_dump(mode="json")
        attempted = False
        checkpoint = output / "dispatch_checkpoint.json"
        if checkpoint.is_file():
            observed = _json(checkpoint, 100_000)
            attempted = observed.get("attempted") is True and observed.get("attempt_id") == attempt["attempt_id"]
        values.update(attempted=attempted, completion_status=("timeout" if supervision.failure_kind == "shared_deadline"
                      else "tool_error") if attempted else "blocked", accepted_final=False,
                      report_accepted=None, workflow_passed=None, integrity_status="unavailable",
                      reference_task_success=None, metrics={}, errors=[], metric_missingness={},
                      trusted_tool_capture_complete=False, trusted_tool_calls=None, trusted_receipt_count=None,
                      final_execution_tool_calls=None)
        record = _record(values)
    values = record.model_dump(mode="json")
    rss = [value for value in (record.RSS_MB, supervision.RSS_MB) if value is not None]
    values.update(elapsed_sec=max(record.elapsed_sec or 0, supervision.elapsed_sec), RSS_MB=max(rss) if rss else None)
    values["accounting_completeness"].update(elapsed_sec="complete", RSS_MB="partial" if rss else "unavailable")
    record = _record(values)
    detail["record_path"] = str(output / "record.json")
    _write(output / "record.json", record.model_dump(mode="json"), replace=(output / "record.json").exists())
    detail["record_sha256"] = _file_digest(output / "record.json")
    execution = output / "execution.json"
    if not execution.is_file():
        _write(execution, {"attempt_id": attempt["attempt_id"], "attempted": record.attempted,
            "completion_status": record.completion_status,
            "errors": [detail.get("error") or detail.get("record_recovery_error")
                       or "No worker execution detail was retained"],
            "supervision": detail})
    return record, interrupted, {"record_path": detail["record_path"], "record_sha256": detail["record_sha256"],
        "execution_detail_path": str(execution), "execution_detail_sha256": _file_digest(execution),
        "retained_tool_root_path": str(output / "retained_tool_root.json") if (output / "retained_tool_root.json").is_file() else None,
        "retained_tool_root_sha256": _file_digest(output / "retained_tool_root.json") if (output / "retained_tool_root.json").is_file() else None,
        "supervision": detail}


def execute_public_study(plan, prepared_bundles, output_dir, *, workspace_root,
                         codex_executable="codex", max_live_attempts=6, allow_data_execution=False,
                         allow_exec_preflight=False, preflight_only=False, max_provider_sessions=6, preflight_conditions=None):
    """Execute frozen public attempts under the common supervised participant path."""
    if type(preflight_only) is not bool:
        raise ValueError("preflight_only must be boolean")
    if preflight_conditions is not None and not preflight_only:
        raise ValueError("Condition selection requires preflight-only mode")
    if preflight_only:
        from .preflight_only import execute_preflight_only
        return execute_preflight_only("public", plan, prepared_bundles, output_dir, workspace_root,
            codex_executable=codex_executable, max_provider_sessions=max_provider_sessions,
            allow_exec_preflight=allow_exec_preflight, preflight_conditions=preflight_conditions)
    if allow_data_execution is not True:
        raise PermissionError("Public data/model execution is disabled")
    from .public_study import schedule_public_attempts
    plan, bundles = validate_public_execution_configuration(plan, prepared_bundles)
    attempts = schedule_public_attempts(plan)
    if type(max_live_attempts) is not int or max_live_attempts < 1:
        raise ValueError("The explicit live-attempt budget must be a positive integer")
    if len(attempts) > max_live_attempts:
        raise ValueError("Frozen public schedule exceeds max_live_attempts; no participant was launched")
    from .preflight_only import require_provider_budget
    require_provider_budget(2 * len(attempts), max_provider_sessions)
    if allow_exec_preflight is not True:
        raise PermissionError("Scored execution requires explicit authorization for its additional exec preflight session")
    from .contracts import content_sha256
    from .execution import _file_digest, _safe_path, _source_identity
    from .permissions import require_protected_storage
    from .public_study import verify_public_task
    from .supervision import supervise_coordinator
    destination = require_protected_storage(_safe_path(output_dir))
    workspace_root = require_protected_storage(_safe_path(workspace_root))
    if not workspace_root.is_relative_to(_REPOSITORY / "tmp"):
        raise ValueError("Public participant workspaces must remain below repository tmp")
    bundle_roots = [require_protected_storage(_safe_path(bundle.root)) for bundle in bundles.values()]
    private = [destination, *bundle_roots]
    if any(workspace_root.is_relative_to(root) or root.is_relative_to(workspace_root) for root in private):
        raise ValueError("Public workspaces and coordinator-private storage must be disjoint")
    if any(destination.is_relative_to(root) or root.is_relative_to(destination) for root in private[1:]):
        raise ValueError("Public outputs and prepared bundles must be disjoint")
    destination.mkdir(mode=0o700, parents=True, exist_ok=False)
    _write(destination / "plan.json", plan.model_dump(mode="json"))
    _write(destination / "schedule.json", {"record_format": "devbench.public.schedule/1",
                                            "plan_identity": plan.sha256(), "attempts": attempts})
    frozen = {name: _file_digest(destination / name) for name in ("plan.json", "schedule.json")}
    records = initial_public_attempt_records(plan, bundles)
    run = {"record_format": "devbench.public.execution/1", "study_id": plan.study_id,
        "planned_exec_preflight_sessions": len(attempts), "maximum_planned_provider_sessions": 2 * len(attempts),
        "max_provider_sessions": max_provider_sessions,
        "preflight_accounting_scope": "separate per-attempt exec-preflight evidence; not scored participant tokens",
        "plan_identity": plan.sha256(), "status": "started", "planned_attempt_count": len(attempts),
        "attempted_count": 0, "processed_attempt_count": 0, "all_model_slots_processed": False,
        "comprehensive_execution": False, "errors": [], "aggregate_path": str(destination / "records.json"),
        "aggregate_sha256": None, "attempt_artifacts": {}, "shared_runtime_sha256": None,
        "source_kind": "public_processed", "complete_biological_correctness": None,
        "reference_eligibility": {},
        "calibration_status": plan.calibration_status, "output_dir": str(destination)}
    def save():
        from .preflight_only import preflight_accounting
        run["preflight_accounting"] = preflight_accounting(
            destination / "attempts" / item["attempt_id"] / "exec-preflight" for item in attempts)
        _write(destination / "records.json", {"record_format": "devbench.public.attempt-records/1",
            "plan_identity": plan.sha256(), "records": [row.model_dump(mode="json") for row in records]},
            replace=(destination / "records.json").exists())
        run["aggregate_sha256"] = _file_digest(destination / "records.json")
        run["attempted_count"] = sum(row.attempted for row in records)
        run["processed_attempt_count"] = sum(row.completion_status != "not_started" for row in records)
        _write(destination / "execution.json", run, replace=(destination / "execution.json").exists())
    save()
    try:
        for dataset_id, bundle in bundles.items():
            verify_public_task(bundle, plan, allow_data_execution=True)
            eligibility = _reference_task_eligibility(bundle, _load_reference(bundle))
            run["reference_eligibility"][dataset_id] = eligibility
            for index, record in enumerate(records):
                if record.dataset_id == dataset_id:
                    records[index] = record.model_copy(update={
                        "reference_task_eligible": eligibility["reference_task_eligible"]})
        save()
        if plan.budget.token_budget is not None:
            raise ValueError("The registered participant cannot enforce a token ceiling")
        executable_name = shutil.which(str(codex_executable))
        if executable_name is None:
            raise RuntimeError("Codex executable is unavailable; no public participant was launched")
        executable = Path(executable_name).resolve()
        from .cli_identity import read_codex_identity
        identity = read_codex_identity(executable)
        source_hash, executable_hash = _source_identity(), identity["executable_sha256"]
        run.update(source_sha256=source_hash, codex_executable_sha256=executable_hash,
                   codex_version=identity["version"], codex_identity=identity,
                   frozen_plan_files=frozen, frozen_plan_sha256=content_sha256(frozen))
        workspace_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        workspaces = [Path(tempfile.mkdtemp(prefix="u-", dir=workspace_root)).resolve() for _ in attempts]
        if any(len(str(workspace / "control/tools.sock").encode()) > 100 for workspace in workspaces):
            raise ValueError("Protected public participant paths exceed the Unix socket length budget")
        runtime_hash = None
        for index, (attempt, workspace) in enumerate(zip(attempts, workspaces)):
            from .cli_identity import verify_codex_identity
            verify_codex_identity(identity)
            if (_source_identity() != source_hash or _file_digest(executable) != executable_hash
                    or any(_file_digest(destination / name) != digest for name, digest in frozen.items())):
                raise RuntimeError("Frozen public source, executable, plan or schedule changed")
            bundle = bundles[attempt["dataset_id"]]
            output = destination / "attempts" / attempt["attempt_id"]
            output.mkdir(mode=0o700, parents=True)
            private_roots = (*private, *(_REPOSITORY / name for name in _SOURCE_PACKAGES),
                             *(path for path in workspaces if path != workspace))
            arguments = (plan, attempt, records[index], bundle, workspace, output, private_roots,
                         source_hash, runtime_hash, executable)
            supervision = supervise_coordinator(_public_attempt_worker, (arguments,), output, plan.budget,
                                                 allow_data_execution=True)
            records[index], interrupted, artifact = _collect_attempt(supervision, arguments)
            if records[index].shared_runtime_sha256 is not None:
                if runtime_hash is None:
                    runtime_hash = records[index].shared_runtime_sha256
                    run["shared_runtime_sha256"] = runtime_hash
                elif records[index].shared_runtime_sha256 != runtime_hash:
                    raise RuntimeError("Public attempt runtime differs from the shared runtime")
            run["attempt_artifacts"][attempt["attempt_id"]] = artifact
            save()
            if interrupted:
                raise KeyboardInterrupt
        run["all_model_slots_processed"] = True
        run["status"] = "finished" if all(row.attempted for row in records) else "finished_with_blocked_attempts"
        run["comprehensive_execution"] = all(row.completion_status == "completed"
            and row.integrity_status == "verified" and row.trusted_tool_capture_complete for row in records)
    except KeyboardInterrupt:
        run["status"] = "interrupted"
        run["errors"].append("Remaining scheduled public model slots retained without fabricated execution")
    except Exception as error:
        run["status"] = "blocked"
        run["errors"].append(f"{type(error).__name__}: {error}"[:2000])
        if not any(row.attempted for row in records):
            records = [row.model_copy(update={"completion_status": "blocked"}) for row in records]
    finally:
        for row in records:
            if row.attempt_id in run["attempt_artifacts"]:
                continue
            output = destination / "attempts" / row.attempt_id
            output.mkdir(mode=0o700, parents=True, exist_ok=True)
            record_path, detail_path = output / "record.json", output / "execution.json"
            _write(record_path, row.model_dump(mode="json"), replace=record_path.exists())
            _write(detail_path, {"attempt_id": row.attempt_id, "attempted": row.attempted,
                "completion_status": row.completion_status, "errors": ["No supervised participant result retained"]},
                replace=detail_path.exists())
            run["attempt_artifacts"][row.attempt_id] = {
                "record_path": str(record_path), "record_sha256": _file_digest(record_path),
                "execution_detail_path": str(detail_path), "execution_detail_sha256": _file_digest(detail_path),
                "retained_tool_root_path": None, "retained_tool_root_sha256": None, "supervision": None}
        save()
    return {**run, "execution_manifest_sha256": _file_digest(destination / "execution.json")}
