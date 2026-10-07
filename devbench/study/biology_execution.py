"""Explicitly authorized biological-study coordination over prepared bundles.

Scientific references and availability are frozen in a separately supervised
preparation job before any participant is dispatched. Participants receive only
verified public bytes, public questions, ordinary dependencies and their arm's
method policy. Importing this module performs no data or model work.
"""
from __future__ import annotations

from collections.abc import Mapping
import csv
import hashlib
import io
import math
import os
from pathlib import Path
import shutil
import sys
import tempfile
import time


_REPOSITORY = Path(__file__).resolve().parents[2]
_REFERENCE_FIELDS = ("target_reference_availability", "target_reference_missingness", "target_reference_scopes",
                     "target_reference_components", "reference_availability_sha256", "thresholds_sha256")
_COMMON_GUIDANCE = """Read task_inputs.json and answer_schema.json. Analyze the requested biological questions
using the actual supplied measurement layer, biological units and target-specific
estimands. Check alignment, replication, design support, normalization, contrasts
and numerical failure before making claims. Collection age is a reference, not
universal pseudotime truth. Cells and technical libraries are not independent
donors. Association, curated membership, motif enrichment and interventions have
different evidence scopes; do not invent missing observations or direct causality.
Use the entire declared hypothesis universe, preserve unavailable estimates and
missing reasons, and distinguish method failure from a justified scientific refusal.
Ground a refusal in a violated or unknown public identification_criteria entry
and cite the observed input supporting that diagnostic in support_checks.
The engineering thresholds are uncalibrated; code execution is not biological or
clinical qualification. Never read coordinator files or other participants.

Treat inputs, schema and guidance as read-only. Use only the supplied ordinary
Python dependencies and absolute paths, with bounded reads and compact output.
Do not install packages, download data, use external services or bypass permissions.
Write standalone solve.py with a module-level if __name__ == '__main__': guard.
Execute it directly with the exact staged .venv/bin/python command in TASK_README.md.
That execution must write finite final_discovery.json matching answer_schema.json
(devbench.biology-answer/1), including task_id, input_sha256 and gene_universe_sha256.
Each requested target needs a target_result with the exact public estimand, units,
contrast and reference definitions. Unsupported analyses stay failed or unavailable;
never turn a software failure into a scientific refusal. The saved JSON, rather
than the final chat message, is graded. Narrative remains separate from checked claims.
In narrative_for_human_review and scientific explanations, discuss biological
evidence, scientific methods, assumptions, uncertainty and alternative explanations.
Keep benchmark-arm names, assistant identity, harness/client branding and receipt
identifiers out of this prose. Put operational provenance in the structured evidence
fields instead. Do not omit scientific caveats or alter results to conceal tool use.
"""
_ARM_GUIDANCE = {
    "baseline": "No DevHarness endpoint or client is supplied. Use ordinary methods. Tool evidence IDs may be empty; never invent receipts.",
    "optional": "DevHarness is optional. Ordinary methods, hybrids and no tool use are equally permitted. Tool adoption has no direct correctness reward. If used, preserve actual receipt_id values and exact result fields.",
    "enforced": "Exploration may use any supplied method, but report acceptance requires host-verified tool receipts. Call the documented common checks and matching method prerequisites. Every target result must exactly match a successful recorded target_result, with its receipt_id in tool_evidence_ids. A failed method does not justify a refusal or fabricated estimate.",
}


def biology_participant_guidance(condition):
    """Pure method policy, without scenarios, references, or generated answers."""
    if condition not in _ARM_GUIDANCE:
        raise ValueError("Unknown biological study condition")
    return _COMMON_GUIDANCE + "\n" + _ARM_GUIDANCE[condition] + "\n"


def _write(path, value, *, replace=False):
    from .supervision import write_coordinator_json
    write_coordinator_json(path, value, replace=replace, allow_data_execution=True)


def _json(path, limit=64_000_000):
    from .evidence import _read, decode_json
    return decode_json(_read(Path(path), limit))


def _record(value):
    from .biology_records import parse_biological_attempt
    return parse_biological_attempt(value)


def validate_biology_execution_configuration(plan, prepared_bundles):
    """Validate only authored contracts; never inspect prepared dataset bytes."""
    from .biology_planning import BiologyStudyPlan, validate_biology_plan
    from .biology_datasets import PreparedBiologyBundle
    from .contracts import content_sha256
    if not isinstance(plan, BiologyStudyPlan):
        raise TypeError("A BiologyStudyPlan is required")
    if plan.requested_model is None:
        raise ValueError("A pinned requested_model is required")
    plan = validate_biology_plan(plan)
    recipes = {recipe.recipe_id: recipe for recipe in plan.recipes}
    evaluation = {key for key, recipe in recipes.items() if recipe.role == "evaluation"}
    if any(recipes[key].generation_seed is None for key in evaluation):
        raise ValueError("Evaluation generation seeds must be frozen before execution")
    if (not isinstance(prepared_bundles, Mapping) or not evaluation <= set(prepared_bundles)
            or set(prepared_bundles) - set(recipes)):
        raise ValueError("Prepared bundles must cover evaluation cohorts and no unknown recipes")
    bundles = {}
    for key, value in prepared_bundles.items():
        if not isinstance(value, PreparedBiologyBundle):
            raise TypeError("Each cohort requires a PreparedBiologyBundle")
        bundle = PreparedBiologyBundle.model_validate_json(value.model_dump_json())
        if (bundle.cohort_id != key or bundle.recipe_id != key or bundle.plan_identity != plan.sha256()
                or bundle.recipe_sha256 != content_sha256(recipes[key].model_dump(mode="json"))
                or bundle.catalog_sha256 != plan.catalog_sha256):
            raise ValueError("Prepared biology receipt differs from the exact plan/cohort")
        bundles[key] = bundle
    return plan, bundles


def initial_biology_attempt_records(plan, prepared_bundles):
    from .biology_planning import schedule_biology_attempts
    from .contracts import content_sha256
    plan, bundles = validate_biology_execution_configuration(plan, prepared_bundles)
    recipes = {row.recipe_id: row for row in plan.recipes}
    identities = {key: "bio-cohort-" + content_sha256({"generator": bundle.generator_sha256,
                  "generation_seed": recipes[key].generation_seed}) for key, bundle in bundles.items()}
    # Reusing exact public bytes cannot manufacture another independent cohort.
    by_input = {}
    for key, bundle in bundles.items():
        by_input[bundle.input_sha256] = min(by_input.get(bundle.input_sha256, identities[key]), identities[key])
    rows = []
    for attempt in schedule_biology_attempts(plan):
        values = {name: getattr(attempt, name) for name in ("attempt_id", "study_id", "plan_identity", "task_identity",
                  "cohort_id", "recipe_id", "template_id", "case_id", "condition", "repetition")}
        values.update(role="evaluation", actor_type="model_participant", source_kind="synthetic",
            independence_id=by_input[bundles[attempt.cohort_id].input_sha256], source_sha256=None, input_sha256=None,
            shared_runtime_sha256=None, harness_sha256=None, actual_model=None, requested_model=plan.requested_model,
            model_identity_sha256=None, model_identity_verified=False, attempted=False, completion_status="not_started",
            accepted_final=False, scientific_success=None, available_target_scientific_success=None,
            report_accepted=None, workflow_passed=None, integrity_status="unavailable",
            target_reference_availability={}, target_reference_missingness={}, target_reference_scopes={},
            target_reference_components={}, reference_availability_sha256=None, thresholds_sha256=None,
            per_target_results=[], safe_rows=[], trusted_tool_capture_complete=False, trusted_tool_calls=None,
            trusted_receipt_count=None, elapsed_sec=None, RSS_MB=None, tokens=None, cost=None,
            accounting_completeness=dict.fromkeys(("elapsed_sec", "RSS_MB", "tokens", "cost"), "unavailable"))
        rows.append(_record(values))
    return rows


def project_biology_task(bundle, recipe, task_id, workspace, *, allow_data_execution=False):
    """Derive public axes and role bindings from a fully verified input bundle."""
    if allow_data_execution is not True:
        raise PermissionError("Biological public-data projection is disabled")
    from .biology_datasets import verify_prepared_biology_bundle
    from .biology_tool_io import validate_public_task
    from .biology_answers import IDENTIFICATION_CRITERIA, public_output_layout
    from .biology_scoring import association_pair_universe
    from .contracts import content_sha256
    from .evidence import _read
    from .execution import _safe_path
    from devharness_core.storage import ChunkedExpressionStore
    bundle = verify_prepared_biology_bundle(bundle, recipe, bundle.plan_identity, allow_data_execution=True)
    workspace = _safe_path(workspace)
    root = Path(bundle.root)
    public = _json(root / "inputs/task_inputs.json", 50_000_000)
    with ChunkedExpressionStore(root / "inputs/counts.zarr") as store:
        genes, observations = tuple(map(str, store.gene_names)), tuple(map(str, store.sample_names))
    reader = csv.DictReader(io.StringIO(_read(root / "inputs/metadata.csv", 50_000_000).decode("utf-8")))
    headers = reader.fieldnames or []
    rows = list(reader)
    if tuple(row.get("sample_id") for row in rows) != observations:
        raise ValueError("Observed metadata and expression axes do not align")
    context = public["context"]
    def role(candidates):
        available = [name for name in candidates if name in headers]
        if len(available) > 1:
            raise ValueError("Ambiguous public analysis column role")
        return available[0] if available else None
    time_column = role(("time", "numeric_time", "collection_age"))
    aliases = context.get("metadata_aliases", {})
    batches = [name for name in ("batch", "capture_batch", "sequencing_batch", "processing_batch")
               if name in headers and not (name in aliases and aliases[name] in headers)]
    columns = {"condition": role(("condition",)), "time": time_column, "dose": role(("dose", "concentration")),
               "covariate": role(("sex", "genotype_dosage", "covariate")),
               "population_unit": public["population_unit_label"], "batches": batches}
    if columns["population_unit"] not in headers:
        raise ValueError("Declared population-unit column is absent")
    times = [float(row[time_column]) for row in rows] if time_column else []
    if any(not math.isfinite(value) for value in times):
        raise ValueError("Observed time labels are nonfinite")
    public.update(task_id=task_id, input_sha256=bundle.input_sha256, gene_ids=list(genes),
                  gene_universe_sha256=content_sha256(genes), observation_ids=list(observations),
                  analysis_columns=columns, analysis_reference_time=min(times) if times else None)
    public["identification_criteria"] = dict(IDENTIFICATION_CRITERIA)
    public["answer_layout"] = public_output_layout(public, _json(root / "inputs/public_annotations.json", 5_000_000))
    for key, filename in (("expression_path", "counts.zarr"), ("metadata_path", "metadata.csv"),
                          ("public_annotations_path", "public_annotations.json")):
        public[key] = str(workspace / "inputs" / filename)
    if any(question["target_id"] == "residual_modules" for question in public["questions"]):
        public["association_pair_universe"] = [pair.model_dump(mode="json") for pair in association_pair_universe(genes)]
        public["association_pair_universe_rule"] = "Sort gene IDs; form unique undirected circular-offset pairs at offsets 1, 2, 4, 8 when gene count exceeds the offset; sort resulting pairs. This fixed public comparison family is independent of expression and truth."
    validate_public_task(public)
    return public


def _biology_reference_worker(arguments, *, allow_data_execution=False):
    if allow_data_execution is not True:
        raise PermissionError("Biological reference preparation is disabled")
    from .biology import load_biology_catalog
    from .biology_scoring import build_biology_reference, grade_biology_answer
    from .biology_records import biology_grade_record_fields
    from .execution import _file_digest, _source_identity
    plan, recipe, bundle, task_id, output, source_hash = arguments
    if _source_identity() != source_hash:
        raise RuntimeError("Implementation changed before reference preparation")
    public = project_biology_task(bundle, recipe, task_id, Path(bundle.root), allow_data_execution=True)
    reference = build_biology_reference(template=load_biology_catalog().template(recipe.template_id),
        private_truth=_json(Path(bundle.root) / "private/truth.json", 50_000_000), public_context=public["context"],
        public_annotations=_json(Path(bundle.root) / "inputs/public_annotations.json", 50_000_000),
        task_id=task_id, input_sha256=bundle.input_sha256, gene_ids=tuple(public["gene_ids"]),
        cell_ids=tuple(public["observation_ids"]) if recipe.parameters.cells_per_specimen else (),
        thresholds=plan.thresholds, allow_result_analysis=True)
    unavailable = grade_biology_answer(None, reference=reference, thresholds=plan.thresholds, allow_result_analysis=True)
    fields = biology_grade_record_fields(unavailable, input_sha256=bundle.input_sha256,
                                         expected_task_identity=task_id, allow_result_analysis=True)
    _write(output / "reference.json", reference.model_dump(mode="json"))
    _write(output / "public_task.json", public)
    _write(output / "missing_answer_grade.json", unavailable.model_dump(mode="json"))
    if _source_identity() != source_hash:
        raise RuntimeError("Implementation changed during reference preparation")
    _write(output / "attempt_result.json", {"task_id": task_id, "input_sha256": bundle.input_sha256,
        "source_sha256": source_hash, "reference_availability": {name: fields[name] for name in _REFERENCE_FIELDS},
        "artifacts": {name: {"path": str(output / name), "sha256": _file_digest(output / name)}
                      for name in ("reference.json", "public_task.json", "missing_answer_grade.json")}})


def _runtime_identity(roots, plan, executable):
    from .contracts import content_sha256
    from .cli_identity import codex_component_hashes
    from .execution import _tree_digest, _file_digest
    return content_sha256({"runtime_files": {str(path): _tree_digest(path, allowed_symlink_targets=roots) for path in roots},
        "python": sys.version, "budget": plan.budget.model_dump(mode="json"),
        "reasoning_effort": plan.reasoning_effort, "codex_executable_sha256": _file_digest(executable),
        "codex_component_sha256": codex_component_hashes(executable),
        "scope": "shared_runtime_source_native_bytes_excluding_python_caches_and_arm_guidance"})


def _stage_biology_inputs(bundle, public, workspace, condition, *, allow_data_execution=False):
    if allow_data_execution is not True:
        raise PermissionError("Biological input materialization is disabled")
    from .biology_answers import biology_answer_schema
    from .biology_broker import biology_tool_menu
    from .biology_datasets import is_biology_public_member
    from .contracts import content_sha256
    from .execution import _file_digest
    manifest = _json(Path(bundle.root) / "input_manifest.json", 10_000_000)
    hashes = {}
    for name, receipt in manifest["files"].items():
        if not is_biology_public_member(name):
            raise ValueError("Only registered public biological input members may be staged")
        source, target = Path(bundle.root) / name, workspace / name
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with source.open("rb") as reader, target.open("xb") as writer:
            shutil.copyfileobj(reader, writer, length=1_048_576)
        target.chmod(0o444)
        hashes[str(target)] = receipt["sha256"]
    public = dict(public)
    for key, filename in (("expression_path", "counts.zarr"), ("metadata_path", "metadata.csv"),
                          ("public_annotations_path", "public_annotations.json")):
        public[key] = str(workspace / "inputs" / filename)
    _write(workspace / "task_inputs.json", public)
    _write(workspace / "input_manifest.json", manifest)
    _write(workspace / "answer_schema.json", biology_answer_schema())
    from .submission_validation import stage_submission_validator
    validator = stage_submission_validator(workspace, "biology", max_answer_bytes=8_000_000)
    texts = {"TASK_README.md": biology_participant_guidance(condition) +
             f"\nExecute: {workspace / '.venv/bin/python'} {workspace / 'solve.py'}\n"
             + f"\nBefore submitting, run this public contract check in a separate command:\n{validator.command}\n"
             + "Fix reported contract errors, rerun solve.py, then validate again. This check supplies no grading references or scientific score.\n",
             "sdk_cheatsheet.py": '"""Use the exact staged Python. Importing this guide runs no analysis."""\n'}
    if condition != "baseline":
        texts["devharness_client.py"] = (Path(__file__).parent / "client.py").read_text()
        texts["sdk_cheatsheet.py"] += ('from devharness_client import call\n'
            '# Read tool_menu.json. Calls accept only options, with input paths bound by the coordinator.\n'
            '# response = call("method_name", options={})  # inside solve.py main guard only\n'
            '# Check response["status"] and every target result. Copy the actual target_result,\n'
            '# adding response["receipt_id"] to its tool_evidence_ids; preserve all other fields.\n'
            '# Run common checks and matching prerequisites before enforced methods.\n')
        _write(workspace / "tool_menu.json", biology_tool_menu(public))
    for name, text in texts.items():
        with (workspace / name).open("x") as handle:
            handle.write(text)
    names = ["task_inputs.json", "input_manifest.json", "answer_schema.json", *texts]
    if condition != "baseline":
        names.append("tool_menu.json")
    for name in names:
        (workspace / name).chmod(0o444)
        hashes[str(workspace / name)] = _file_digest(workspace / name)
    protected = (workspace / "inputs", workspace / ".venv", workspace / "control", *(workspace / name for name in names),
                 *validator.protected_paths)
    hashes.update(validator.hashes)
    arm_hash = content_sha256({name: hashes[str(workspace / name)] for name in names
                              if name in {*texts, "tool_menu.json"}})
    return public, protected, hashes, arm_hash


def _read_preparation(preparation):
    from .biology_scoring import BiologyReference
    from .evidence import _read
    from .execution import _file_digest
    for item in preparation["artifacts"].values():
        if _file_digest(Path(item["path"])) != item["sha256"]:
            raise ValueError("Retained reference preparation artifact changed")
    reference = BiologyReference.model_validate_json(_read(
        Path(preparation["artifacts"]["reference.json"]["path"]), 64_000_000))
    public = _json(preparation["artifacts"]["public_task.json"]["path"])
    return reference, public


def _verify_attempt_integrity(plan, recipe, bundle, source_hash, executable, runtime_roots, runtime_hash,
                              staged_hashes, preparation):
    from .biology_datasets import verify_prepared_biology_bundle
    from .execution import _file_digest, _source_identity
    verify_prepared_biology_bundle(bundle, recipe, plan.sha256(), allow_data_execution=True)
    if preparation is not None:
        _read_preparation(preparation)
    if (_source_identity() != source_hash or _runtime_identity(runtime_roots, plan, executable) != runtime_hash
            or any(_file_digest(Path(path)) != digest for path, digest in staged_hashes.items())):
        raise ValueError("Biological source, runtime or protected input integrity changed")


def _apply_grade(values, grade, *, accepted):
    from .biology_records import biology_grade_record_fields
    fields = biology_grade_record_fields(grade, input_sha256=values["input_sha256"],
        expected_task_identity=values["task_identity"], allow_result_analysis=True)
    for key in _REFERENCE_FIELDS:
        if fields[key] != values[key]:
            raise ValueError("Grading changed the pre-dispatch reference availability")
    values.update(fields)
    if not accepted:
        values.update(scientific_success=None, available_target_scientific_success=None, safe_rows=[])


def _biology_bulk_attempt_worker(arguments, *, allow_data_execution=False):
    """Registered supervised entry point; keep legacy/default workers unchanged."""
    return _biology_attempt_worker(arguments, allow_data_execution=allow_data_execution,
                                   participant_runtime_profile="bulk")


def _biology_attempt_worker(arguments, preflight_only=False, *, allow_data_execution=False,
                            participant_runtime_profile="host"):
    if allow_data_execution is not True:
        raise PermissionError("Biological participant execution is disabled")
    from devbench.staging import stage_python
    from .adapter import StudyCodeActionAdapter
    from .biology_answers import BiologyAnswer
    from .biology_datasets import verify_prepared_biology_bundle
    from .biology_broker import BiologyBroker, verify_biology_workflow
    from .biology_scoring import grade_biology_answer
    from .broker import BrokerPolicy
    from .contracts import canonical_json_bytes, content_sha256
    from .evidence import verify_receipts
    from .execution import _runtime_roots, _source_identity, _observed_accounting, _failure_outcome
    from .permissions import PermissionBoundary, probe_boundary
    from .transport import BrokerTransport
    plan, attempt, initial, recipe, bundle, preparation, workspace, output, private_roots, source_hash, expected_runtime, executable = arguments
    values = {} if preflight_only else initial.model_dump(mode="json")
    details = {"attempt_id": attempt.attempt_id, "errors": []}
    if not preflight_only:
        details["reference_sha256"] = preparation["artifacts"]["reference.json"]["sha256"]
    adapter = broker = transport = None
    answer = reference = None
    roots, staged = (), {}
    stage, interrupted = "preparation", False
    started = time.monotonic()
    try:
        if _source_identity() != source_hash:
            raise RuntimeError("Implementation changed before participant preparation")
        verify_prepared_biology_bundle(bundle, recipe, plan.sha256(), allow_data_execution=True)
        if preflight_only:
            public = project_biology_task(bundle, recipe, attempt.task_identity, workspace, allow_data_execution=True)
        else:
            reference, public = _read_preparation(preparation)
        environment = (stage_python(workspace, assisted=False, profile="bulk")
                       if participant_runtime_profile == "bulk" else stage_python(workspace, assisted=False))
        details["participant_runtime"] = environment
        roots = _runtime_roots(workspace, environment)
        runtime_hash = _runtime_identity(roots, plan, executable)
        if expected_runtime is not None and runtime_hash != expected_runtime:
            raise ValueError("Shared runtime differs across study conditions")
        values["shared_runtime_sha256"] = runtime_hash
        public, protected, staged, values["harness_sha256"] = _stage_biology_inputs(
            bundle, public, workspace, attempt.condition, allow_data_execution=True)
        (workspace / "control").mkdir(mode=0o700)
        _write(output / "staging_checkpoint.json", {"record": values, "runtime_roots": [str(root) for root in roots],
                                                    "staged_hashes": staged})
        assisted = attempt.condition != "baseline"
        boundary = PermissionBoundary(workspace=workspace, private_roots=tuple(private_roots), runtime_roots=roots,
            protected_paths=protected, socket_path=workspace / "control/tools.sock" if assisted else None)
        probe = probe_boundary(boundary, executable, Path(environment["python"]), allow_configuration_probe=True)
        details["boundary_probe"] = probe
        if probe.get("verified") is not True or probe.get("configuration") != boundary.config_overrides():
            raise RuntimeError("Exact biological participant boundary could not be verified")
        _verify_attempt_integrity(plan, recipe, bundle, source_hash, executable, roots, runtime_hash, staged, preparation)
        if assisted:
            # Legacy policy fields are unused by biological admission, which
            # receives only public inputs and observed-data tool diagnostics.
            policy = BrokerPolicy(condition=attempt.condition, input_sha256=bundle.input_sha256,
                measurement_type="continuous" if bundle.measurement == "continuous" else "raw_counts",
                gene_ids=tuple(public["gene_ids"]), population_inference_allowed=False, unit_provenance_verified=False,
                time_col=public["analysis_columns"]["time"] or "unused_time_axis",
                biological_unit_column=public["analysis_columns"]["population_unit"],
                timeout_sec=plan.budget.timeout_sec, max_memory_mb=plan.budget.max_memory_mb,
                implementation_sha256=source_hash)
            aliases = {public[key]: Path(bundle.root) / "inputs" / name for key, name in (
                ("expression_path", "counts.zarr"), ("metadata_path", "metadata.csv"),
                ("public_annotations_path", "public_annotations.json"))}
            broker = BiologyBroker(policy, aliases, output / "receipts", public_task=public,
                                   allow_data_execution=True, defer_start=True)
            transport = BrokerTransport(broker, boundary.socket_path, allow_data_execution=True)
        def checkpoint(observed):
            _write(output / "dispatch_checkpoint.json", {"attempt_id": attempt.attempt_id, "attempted": True, **observed})
        adapter = StudyCodeActionAdapter(boundary=boundary, requested_model=plan.requested_model,
            condition=attempt.condition, reasoning_effort=plan.reasoning_effort, executable=str(executable),
            timeout_sec=plan.budget.timeout_sec, max_mem_mb=plan.budget.max_memory_mb,
            thread_limit=plan.budget.cpu_threads, tool_capability_file=transport.capability_file if transport else None,
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
            answer = adapter.execute("Read the biological task instructions.", workspace, allow_data_execution=True)
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
        status, code = _failure_outcome(error, stage=stage, attempted=metadata.get("inference_dispatch_attempted") is True, metadata=metadata)
        values["completion_status"] = status
        details["errors"].append(f"{stage}:{code}:{type(error).__name__}: {error}"[:2000])
    finally:
        metadata = adapter.metadata if adapter else {}
        values["attempted"] = metadata.get("inference_dispatch_attempted") is True
        details["adapter"] = metadata
        transport_ok = False
        retained = None
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
                _verify_attempt_integrity(plan, recipe, bundle, source_hash, executable, roots,
                    values["shared_runtime_sha256"], staged, preparation)
                from .cli_identity import verify_codex_identity
                verify_codex_identity(details["exec_preflight"]["codex_identity"])
                values["integrity_status"] = "verified"
            except Exception as error:
                values["integrity_status"] = "failed"
                details["errors"].append(f"integrity:{type(error).__name__}: {error}"[:2000])
        if not preflight_only and values["attempted"] and values["integrity_status"] == "verified":
            evidence = None
            if not assisted:
                values.update(trusted_tool_capture_complete=True, trusted_tool_calls=0, trusted_receipt_count=0)
                values["final_execution_tool_calls"] = 0 if values.get("final_execution_id") else None
            elif retained is not None and transport_ok:
                try:
                    evidence = verify_receipts(output / "receipts", expected_receipt=retained,
                        expected_input_sha256=bundle.input_sha256, expected_implementation_sha256=source_hash,
                        allow_evidence_read=True)
                    if evidence.condition != attempt.condition:
                        raise ValueError("Recorded tool condition differs from scheduled arm")
                    values.update(trusted_tool_capture_complete=True, trusted_tool_calls=len(evidence.events),
                                  trusted_receipt_count=len(evidence.events))
                    from .execution_usage import selected_tool_calls
                    values["final_execution_tool_calls"] = selected_tool_calls(evidence, values.get("final_execution_id"))
                except Exception as error:
                    details["errors"].append(f"receipts:{type(error).__name__}: {error}"[:2000])
            parsed = None
            if answer is not None and values["completion_status"] == "completed":
                try:
                    parsed = BiologyAnswer.model_validate_json(canonical_json_bytes(answer))
                    if (parsed.task_id != attempt.task_identity or parsed.input_sha256 != bundle.input_sha256
                            or parsed.gene_universe_sha256 != public["gene_universe_sha256"]):
                        raise ValueError("Answer identity differs from the fixed task")
                    values["accepted_final"] = True
                    values["report_accepted"] = attempt.condition != "enforced"
                except Exception as error:
                    values["completion_status"] = "invalid_output"
                    details["errors"].append(f"answer:{type(error).__name__}: {error}"[:2000])
            if parsed is not None and evidence is not None:
                try:
                    workflow = verify_biology_workflow(parsed, evidence, public, allow_evidence_read=True)
                    details["workflow"] = workflow
                    values["workflow_passed"] = workflow["workflow_passed"]
                    if attempt.condition == "enforced":
                        values["report_accepted"] = workflow["workflow_passed"]
                except Exception as error:
                    details["errors"].append(f"workflow:{type(error).__name__}: {error}"[:2000])
            # Raw biological grading is independent of the workflow result.
            try:
                grade = grade_biology_answer(parsed if values["accepted_final"] else None,
                    reference=reference, thresholds=plan.thresholds, allow_result_analysis=True)
                details["grade"] = grade.model_dump(mode="json")
                _apply_grade(values, grade, accepted=values["accepted_final"])
            except Exception as error:
                details["errors"].append(f"grading:{type(error).__name__}: {error}"[:2000])
        if preflight_only:
            from .preflight_only import finish_probe_worker
            finish_probe_worker(output, attempt.condition, details, interrupted=interrupted, broker=broker,
                                adapter=adapter, runtime_sha256=values.get("shared_runtime_sha256"))
        else:
            _write(output / "execution.json", details)
            _write(output / "attempt_result.json", {"record": _record(values).model_dump(mode="json"), "interrupted": interrupted})


def _validated_worker_result(supervision, output):
    from .evidence import _read, decode_json
    receipt = next((item for item in supervision.artifacts if item.kind == "result"), None)
    if receipt is None or receipt.path != output / "attempt_result.json":
        raise ValueError("No bounded raw worker result was captured")
    payload = _read(receipt.path, 64_000_000)
    if hashlib.sha256(payload).hexdigest() != receipt.sha256:
        raise ValueError("Raw worker result changed after supervision")
    return decode_json(payload)


def _collect_attempt(supervision, arguments):
    """Retain valid completed evidence even when supervisor teardown failed."""
    from .biology_scoring import BiologyGrade
    from .contracts import canonical_json_bytes
    from .execution import _file_digest
    plan, attempt, initial, recipe, bundle, preparation, workspace, output, private_roots, source_hash, expected_runtime, executable = arguments
    started = time.monotonic()
    record, interrupted = initial, supervision.interrupted
    details = supervision.as_dict()
    retained = False
    try:
        result = _validated_worker_result(supervision, output)
        candidate = _record(result["record"])
        for name in ("attempt_id", "study_id", "plan_identity", "task_identity", "cohort_id", "recipe_id", "template_id", "case_id",
                     "condition", "repetition", "role", "actor_type", "independence_id", "source_kind", "source_sha256",
                     "input_sha256", "requested_model", *_REFERENCE_FIELDS):
            if getattr(candidate, name) != getattr(initial, name):
                raise ValueError("Worker result differs from scheduled identity or pre-answer availability")
        record, retained = candidate, True
        interrupted = interrupted or result.get("interrupted") is True
    except Exception as error:
        details["record_recovery_error"] = f"{type(error).__name__}: {error}"[:2000]
    failed = supervision.failure_kind is not None or supervision.exitcode != 0 or not retained
    if failed:
        values = record.model_dump(mode="json")
        if not retained and (output / "dispatch_checkpoint.json").is_file():
            observed = _json(output / "dispatch_checkpoint.json", 100_000)
            values["attempted"] = observed.get("attempted") is True and observed.get("attempt_id") == attempt.attempt_id
        staged = None
        if (output / "staging_checkpoint.json").is_file():
            staged = _json(output / "staging_checkpoint.json", 32_000_000)
            snapshot = _record(staged["record"])
            if snapshot.attempt_id != attempt.attempt_id or snapshot.reference_availability_sha256 != initial.reference_availability_sha256:
                raise ValueError("Staging checkpoint differs from pre-dispatch identity")
            values.update(shared_runtime_sha256=snapshot.shared_runtime_sha256, harness_sha256=snapshot.harness_sha256)
        values.update(completion_status=("timeout" if supervision.failure_kind == "shared_deadline" else "tool_error")
                      if values["attempted"] else "blocked", accepted_final=False, scientific_success=None,
                      available_target_scientific_success=None, report_accepted=False if record.report_accepted is not None else None,
                      safe_rows=[])
        if values["attempted"] and staged is not None:
            try:
                _verify_attempt_integrity(plan, recipe, bundle, source_hash, executable,
                    tuple(Path(root) for root in staged["runtime_roots"]), values["shared_runtime_sha256"],
                    staged["staged_hashes"], preparation)
                if record.integrity_status != "failed":
                    values["integrity_status"] = "verified"
                missing = BiologyGrade.model_validate_json(canonical_json_bytes(_json(preparation["artifacts"]["missing_answer_grade.json"]["path"])))
                _apply_grade(values, missing, accepted=False)
            except Exception as error:
                values["integrity_status"] = "failed"
                details["integrity_recovery_error"] = f"{type(error).__name__}: {error}"[:2000]
        elif values["attempted"]:
            values["integrity_status"] = "unavailable"
        if values["integrity_status"] != "verified":
            values.update(per_target_results=[], safe_rows=[], workflow_passed=None)
        record = _record(values)
    values = record.model_dump(mode="json")
    rss = [item for item in (record.RSS_MB, supervision.RSS_MB) if item is not None]
    values.update(elapsed_sec=max(record.elapsed_sec or 0, supervision.elapsed_sec + time.monotonic() - started),
                  RSS_MB=max(rss) if rss else None)
    values["accounting_completeness"].update(elapsed_sec="complete", RSS_MB="partial" if rss else "unavailable")
    record = _record(values)
    details.update(completed_result_retained=retained, recovery_parent_RSS_MB=None,
                   recovery_scope="parent_binding_and_integrity_after_owned_process_termination")
    _write(output / "supervision.json", details)
    _write(output / "record.json", record.model_dump(mode="json"))
    detail = output / "execution.json"
    artifact = {"record_path": str(output / "record.json"), "record_sha256": _file_digest(output / "record.json"),
        "safe_row_count": len(record.safe_rows), "safe_scalar_count": sum(len(row.values) for row in record.safe_rows),
        "execution_detail_path": str(detail) if detail.is_file() else None,
        "execution_detail_sha256": _file_digest(detail) if detail.is_file() else None}
    return record.model_copy(update={"safe_rows": ()}), interrupted, artifact


def _pilot_infrastructure_failure(record, supervision, details):
    """Stop reasons use execution evidence, never scientific scores or acceptance."""
    if supervision.failure_kind is not None or supervision.exitcode != 0:
        return "supervision_failure:" + (supervision.failure_kind or "worker_exit")
    if not record.attempted:
        return "scored_dispatch_not_reached"
    if not record.model_identity_verified:
        return "model_identity_unverified"
    if record.integrity_status != "verified":
        return "attempt_integrity_unverified"
    if not record.trusted_tool_capture_complete:
        return "tool_capture_incomplete"
    adapter = details.get("adapter", {})
    if adapter.get("event_capture_complete") is not True:
        return "execution_capture_incomplete"
    if adapter.get("script_execution_observed") is not True:
        return "script_execution_unverified"
    if record.completion_status in {"timeout", "tool_error", "provider_error", "blocked"}:
        return "participant_infrastructure_failure"
    return None


def execute_biology_study(plan, prepared_bundles, output_dir, *, workspace_root,
                          allow_data_execution=False, codex_executable="codex", max_live_attempts=6,
                          allow_exec_preflight=False, preflight_only=False, max_provider_sessions=6, preflight_conditions=None,
                          pause_after_attempts=None, checkpoint_timeout_sec=300, expected_codex_sha256=None,
                          stop_on_infrastructure_failure=False, participant_runtime_profile="host"):
    """Run authored biology attempts without generating data or coercing targets.

    Both reference preparation and each participant coordinator have their own
    shared-tree supervision. Reference preparation is reported separately from
    model performance. Standalone callers need a module-level __main__ guard.
    """
    if type(preflight_only) is not bool:
        raise ValueError("preflight_only must be boolean")
    if type(stop_on_infrastructure_failure) is not bool:
        raise ValueError("stop_on_infrastructure_failure must be boolean")
    if participant_runtime_profile not in {"host", "bulk"}:
        raise ValueError("Unknown participant runtime profile")
    if preflight_only and participant_runtime_profile != "host":
        raise ValueError("The bulk runtime profile currently applies only to scored execution with its mandatory preflight")
    if preflight_conditions is not None and not preflight_only:
        raise ValueError("Condition selection requires preflight-only mode")
    if pause_after_attempts is not None and (type(pause_after_attempts) is not int or pause_after_attempts<1):
        raise ValueError("pause_after_attempts must be a positive integer")
    if type(checkpoint_timeout_sec) is not int or not 1<=checkpoint_timeout_sec<=3600:
        raise ValueError("checkpoint timeout must be 1..3600 seconds")
    if expected_codex_sha256 is not None:
        import re
        if not isinstance(expected_codex_sha256,str) or not re.fullmatch('[0-9a-f]{64}',expected_codex_sha256):
            raise ValueError("Expected Codex identity must be a SHA-256 digest")
        if not Path(codex_executable).is_absolute():
            raise ValueError("A pinned Codex binary requires an explicit absolute executable path")
    if preflight_only and (pause_after_attempts is not None or expected_codex_sha256 is not None or stop_on_infrastructure_failure):
        raise ValueError("Checkpoint and expected-binary options apply only to scored execution")
    if preflight_only:
        from .preflight_only import execute_preflight_only
        return execute_preflight_only("biology", plan, prepared_bundles, output_dir, workspace_root,
            codex_executable=codex_executable, max_provider_sessions=max_provider_sessions,
            allow_exec_preflight=allow_exec_preflight, preflight_conditions=preflight_conditions)
    if allow_data_execution is not True:
        raise PermissionError("Biological data/model execution is disabled")
    from .biology_planning import prepare_biology_plan, schedule_biology_attempts
    from .biology_scoring import BiologyReference
    from .contracts import content_sha256
    from .execution import _safe_path, _file_digest, _source_identity, _SOURCE_PACKAGES
    from .permissions import require_protected_storage
    plan, bundles = validate_biology_execution_configuration(plan, prepared_bundles)
    if participant_runtime_profile == "bulk" and any(recipe.parameters.cells_per_specimen for recipe in plan.recipes):
        raise ValueError("The bulk participant runtime is not qualified for cellular recipes")
    if type(max_live_attempts) is not int or max_live_attempts < 1:
        raise ValueError("The explicit live-attempt budget must be a positive integer")
    if len(schedule_biology_attempts(plan)) > max_live_attempts:
        raise ValueError("Frozen schedule exceeds max_live_attempts; no participants launched. Explicitly approve a larger attempt budget or prepare a smaller study.")
    if pause_after_attempts is not None and pause_after_attempts>len(schedule_biology_attempts(plan)):
        raise ValueError("Checkpoint is outside the frozen schedule")
    from .preflight_only import require_provider_budget
    require_provider_budget(2 * len(schedule_biology_attempts(plan)), max_provider_sessions)
    if allow_exec_preflight is not True:
        raise PermissionError("Scored execution requires explicit authorization for its additional exec preflight session")
    from .supervision import supervise_coordinator
    destination = require_protected_storage(_safe_path(output_dir))
    workspace_root = require_protected_storage(_safe_path(workspace_root))
    if not workspace_root.is_relative_to(_REPOSITORY / "tmp"):
        raise ValueError("Biology workspaces must remain below repository tmp")
    private = [destination, *(Path(bundle.root) for bundle in bundles.values())]
    if any(workspace_root.is_relative_to(root) or root.is_relative_to(workspace_root) for root in private):
        raise ValueError("Workspace parent and private coordinator storage must be disjoint")
    if any(destination.is_relative_to(Path(bundle.root)) or Path(bundle.root).is_relative_to(destination) for bundle in bundles.values()):
        raise ValueError("Execution output and prepared data must be disjoint")
    plan_receipt = prepare_biology_plan(plan, destination)
    attempts = schedule_biology_attempts(plan)
    records = initial_biology_attempt_records(plan, bundles)
    run = {"record_format": "devbench.biology.execution/1", "study_id": plan.study_id, "plan_identity": plan.sha256(),
        "pause_after_attempts":pause_after_attempts,"checkpoint_timeout_sec":checkpoint_timeout_sec,
        "stop_on_infrastructure_failure": stop_on_infrastructure_failure, "stop_reason": None,
        "participant_runtime_profile": participant_runtime_profile,
        "expected_codex_sha256":expected_codex_sha256,
        "planned_exec_preflight_sessions": len(attempts), "maximum_planned_provider_sessions": 2 * len(attempts),
        "max_provider_sessions": max_provider_sessions,
        "preflight_accounting_scope": "separate per-attempt exec-preflight evidence; not scored participant tokens",
        "status": "started", "planned_attempt_count": len(attempts), "attempted_count": 0, "processed_attempt_count": 0,
        "all_model_slots_processed": False, "comprehensive_execution": False, "errors": [], "reference_preparation": {},
        "aggregate_path": str(destination / "records.json"), "aggregate_sha256": None, "attempt_artifacts": {},
        "safe_detail_storage": "per_attempt", "safe_rows_omitted_from_aggregate": True,
        "retained_safe_row_count": 0, "retained_safe_scalar_count": 0,
        "reference_preparation_resource_scope": "separately_supervised; excluded_from_model_performance",
        "calibration_status": "uncalibrated", "output_dir": str(destination)}
    def save():
        from .preflight_only import preflight_accounting
        run["preflight_accounting"] = preflight_accounting(
            destination / "attempts" / item.attempt_id / "exec-preflight" for item in attempts)
        _write(destination / "records.json", {"record_format": "devbench.biology.attempt-records/1",
            "plan_identity": plan.sha256(), "records": [row.model_dump(mode="json") for row in records]},
            replace=(destination / "records.json").exists())
        run["aggregate_sha256"] = _file_digest(destination / "records.json")
        run["attempted_count"] = sum(row.attempted for row in records)
        run["processed_attempt_count"] = sum(row.completion_status != "not_started" for row in records)
        _write(destination / "execution.json", run, replace=(destination / "execution.json").exists())
    save()
    preparations = {}
    try:
        if plan.budget.token_budget is not None:
            raise ValueError("The registered participant cannot enforce a token ceiling")
        executable_name = shutil.which(str(codex_executable))
        if executable_name is None:
            raise RuntimeError("Codex executable is unavailable; no participant was launched")
        executable = Path(executable_name).resolve()
        from .cli_identity import read_codex_identity
        identity = read_codex_identity(executable)
        if expected_codex_sha256 is not None and identity["executable_sha256"]!=expected_codex_sha256:
            raise RuntimeError("Codex binary differs from the explicitly approved build; no model dispatched")
        source_hash, executable_hash = _source_identity(), identity["executable_sha256"]
        run.update(source_sha256=source_hash, codex_executable_sha256=executable_hash,
                   codex_version=identity["version"], codex_identity=identity)
        workspace_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        workspaces = [Path(tempfile.mkdtemp(prefix="b-", dir=workspace_root)).resolve() for _ in attempts]
        if any(len(str(workspace / "control/tools.sock").encode()) > 100 for workspace in workspaces):
            raise ValueError("Protected participant paths exceed the Unix socket length budget")
        recipes = {recipe.recipe_id: recipe for recipe in plan.recipes}
        for cohort_id in dict.fromkeys(attempt.cohort_id for attempt in attempts):
            attempt = next(row for row in attempts if row.cohort_id == cohort_id)
            output = destination / "references" / ("r-" + attempt.task_identity[:24])
            output.mkdir(mode=0o700, parents=True)
            supervision = supervise_coordinator(_biology_reference_worker,
                ((plan, recipes[cohort_id], bundles[cohort_id], attempt.task_identity, output, source_hash),),
                output, plan.budget, allow_data_execution=True)
            entry = {"supervision": supervision.as_dict(), "status": "blocked"}
            run["reference_preparation"][cohort_id] = entry
            try:
                if supervision.failure_kind is not None or supervision.exitcode != 0:
                    raise RuntimeError("Reference-preparation infrastructure failure")
                prepared = _validated_worker_result(supervision, output)
                reference, public = _read_preparation(prepared)
                if (prepared["task_id"] != attempt.task_identity or prepared["input_sha256"] != bundles[cohort_id].input_sha256
                        or prepared["source_sha256"] != source_hash
                        or reference.task_id != attempt.task_identity or reference.input_sha256 != bundles[cohort_id].input_sha256
                        or public["task_id"] != attempt.task_identity or public["input_sha256"] != reference.input_sha256
                        or public["gene_universe_sha256"] != reference.gene_universe_sha256
                        or reference.thresholds_sha256 != prepared["reference_availability"]["thresholds_sha256"]
                        or reference.thresholds_sha256 != content_sha256(plan.thresholds.model_dump(mode="json"))):
                    raise ValueError("Prepared reference binding differs from the frozen cohort")
                preparations[cohort_id] = prepared
                entry.update(status="ready", artifacts=prepared["artifacts"],
                             reference_availability_sha256=prepared["reference_availability"]["reference_availability_sha256"])
                for index, row in enumerate(records):
                    if row.cohort_id == cohort_id:
                        records[index] = _record({**row.model_dump(mode="json"), **prepared["reference_availability"],
                            "input_sha256": bundles[cohort_id].input_sha256, "source_sha256": source_hash})
            except Exception as error:
                entry["error"] = f"{type(error).__name__}: {error}"[:2000]
                for index, row in enumerate(records):
                    if row.cohort_id == cohort_id:
                        records[index] = row.model_copy(update={"completion_status": "blocked"})
            save()
            if supervision.interrupted:
                raise KeyboardInterrupt
            if stop_on_infrastructure_failure and entry["status"] != "ready":
                run["stop_reason"] = "reference_preparation_failed"
                raise RuntimeError("Reference preparation failed; remaining model slots must not launch")
        runtime_hash = None
        processed_workers = 0
        checkpoint_stopped = False
        for index, (attempt, workspace) in enumerate(zip(attempts, workspaces)):
            from .cli_identity import verify_codex_identity
            verify_codex_identity(identity)
            if attempt.cohort_id not in preparations:
                continue
            if (_source_identity() != source_hash or _file_digest(executable) != executable_hash
                    or any(_file_digest(destination / name) != digest for name, digest in plan_receipt["files"].items())):
                raise RuntimeError("Frozen source, executable or study plan changed")
            output = destination / "attempts" / attempt.attempt_id
            output.mkdir(mode=0o700, parents=True)
            private_roots = (*private, *(_REPOSITORY / name for name in _SOURCE_PACKAGES),
                             *(path for path in workspaces if path != workspace))
            arguments = (plan, attempt, records[index], recipes[attempt.recipe_id], bundles[attempt.cohort_id],
                preparations[attempt.cohort_id], workspace, output, private_roots, source_hash, runtime_hash, executable)
            worker = _biology_bulk_attempt_worker if participant_runtime_profile == "bulk" else _biology_attempt_worker
            supervision = supervise_coordinator(worker, (arguments,), output, plan.budget,
                                                 allow_data_execution=True)
            records[index], interrupted, artifact = _collect_attempt(supervision, arguments)
            if records[index].shared_runtime_sha256 is not None and runtime_hash is None:
                runtime_hash = records[index].shared_runtime_sha256
            run["attempt_artifacts"][attempt.attempt_id] = artifact
            run["retained_safe_row_count"] += artifact["safe_row_count"]
            run["retained_safe_scalar_count"] += artifact["safe_scalar_count"]
            run["processed_attempt_count"] += 1
            save()
            if interrupted:
                raise KeyboardInterrupt
            processed_workers += 1
            if stop_on_infrastructure_failure:
                detail_path = artifact.get("execution_detail_path")
                details = _json(Path(detail_path), 32_000_000) if detail_path else {}
                reason = _pilot_infrastructure_failure(records[index], supervision, details)
                if reason is not None:
                    checkpoint_stopped = True
                    run.update(status="stopped_infrastructure_failure", stop_reason=reason,
                               stopped_after_attempt_id=attempt.attempt_id)
                    save()
                    break
            if pause_after_attempts == processed_workers:
                from .checkpoint import wait_for_checkpoint
                run["status"] = "awaiting_checkpoint_decision"
                save()
                decision = wait_for_checkpoint(destination, attempt.attempt_id, timeout_sec=checkpoint_timeout_sec)
                run["checkpoint_decision"] = decision
                if decision["decision"] != "continue":
                    checkpoint_stopped = True
                    run["status"] = "checkpoint_expired" if decision["decision"] == "expired" else "stopped_at_checkpoint"
                    break
                run["status"] = "started"
        if not checkpoint_stopped:
            run["all_model_slots_processed"] = True
            run["status"] = "finished" if all(row.attempted for row in records) else "finished_with_blocked_attempts"
            run["comprehensive_execution"] = all(row.completion_status == "completed" and row.integrity_status == "verified"
                                                 and row.trusted_tool_capture_complete for row in records)
    except KeyboardInterrupt:
        run["status"] = "interrupted"
        run["errors"].append("Remaining scheduled model slots retained without fabricated execution")
    except Exception as error:
        run["status"] = "blocked"
        run["errors"].append(f"{type(error).__name__}: {error}"[:2000])
        if not any(row.attempted for row in records):
            records = [row.model_copy(update={"completion_status": "blocked"}) for row in records]
    finally:
        # Unlaunched slots need real record receipts too. Otherwise a blocked
        # study cannot be reported without inventing an absent artifact entry.
        for row in records:
            if row.attempt_id in run["attempt_artifacts"]:
                continue
            output = destination / "attempts" / row.attempt_id
            output.mkdir(mode=0o700, parents=True, exist_ok=True)
            record_path = output / "record.json"
            _write(record_path, row.model_dump(mode="json"), replace=record_path.exists())
            run["attempt_artifacts"][row.attempt_id] = {
                "record_path": str(record_path), "record_sha256": _file_digest(record_path),
                "safe_row_count": 0, "safe_scalar_count": 0,
                "execution_detail_path": None, "execution_detail_sha256": None}
        save()
    return {**run, "execution_manifest_sha256": _file_digest(destination / "execution.json")}
