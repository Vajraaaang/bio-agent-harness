"""Explicitly authorized execution of a frozen study on prepared input bundles.

Nothing here generates data or supplies reference answers to participants.
Import, contract validation and initial records are configuration-only. The
single execution entry point rejects before inspecting any argument unless its
literal authorization flag is True. Live integration remains a separate check.
"""
from __future__ import annotations

from dataclasses import dataclass, fields
import hashlib
import math
import os
from pathlib import Path
import re
import shutil
import sys
import sysconfig
import tempfile
import time
from collections.abc import Mapping

from .contracts import StudyPlan, canonical_json_bytes, content_sha256
from .permissions import PermissionBoundary, require_protected_storage
from .planning import prepare_plan, schedule_attempts, schedule_controls
from .records import AccountingCompleteness, AttemptRecord


_REPOSITORY = Path(__file__).resolve().parents[2]
_SOURCE_PACKAGES = ("devbench", "devbench_engine", "devharness", "devharness_core", "devharness_server",
                    "benchmarks", "devharness_controlled")
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,159}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_PUBLIC_CONTEXT_KEYS = frozenset({
    "record_format", "measurement_type", "time_unit", "expression_path", "metadata_path", "path_base",
    "gene_ids", "hypothesis_family_id", "target", "time_grid", "time_col", "biological_unit_column",
    "reference_genes", "reference_batch", "library_factor", "batch_reference_policy",
    "biological_unit_reference", "required_scientific_question", "source_accession", "source_sha256",
    "measurement_layer", "available_truth_scopes", "unknown_truth_scopes",
    "population_inference_permitted", "permitted_claim_scopes", "unit_provenance_verified",
    "expression_units", "biological_unit_scope", "reference_scope", "source_kind",
})


def _safe_path(value):
    path = Path(value)
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("Study paths must be absolute without traversal")
    if any(part.is_symlink() for part in (path, *path.parents)):
        raise ValueError("Study paths must not contain symlinks")
    return path.resolve()


@dataclass(frozen=True)
class PreparedCohortBundle:
    """Coordinator-retained provenance, never inferred from participant files.

    root contains input_manifest.json, inputs/ and private/truth.json. Hashes
    must come from the independent preparation receipt, not from trusting a
    newly read manifest. An independence ID denotes an actual source cohort;
    it must not be manufactured from an arbitrary participant/task label.
    """
    root: Path
    input_sha256: str
    truth_sha256: str
    recipe_id: str
    generation_seed: int | None
    independence_id: str
    unit_provenance_verified: bool

    def __post_init__(self):
        object.__setattr__(self, "root", require_protected_storage(_safe_path(self.root)))
        if any(not isinstance(value, str) or _SHA256.fullmatch(value) is None
               for value in (self.input_sha256, self.truth_sha256)):
            raise ValueError("Externally retained input and private truth SHA256 are required")
        if any(not isinstance(value, str) or _IDENTIFIER.fullmatch(value) is None
               for value in (self.recipe_id, self.independence_id)):
            raise ValueError("Recipe and independence identifiers are required")
        if self.generation_seed is not None and (type(self.generation_seed) is not int
                                                or not 0 <= self.generation_seed <= 2**63 - 1):
            raise ValueError("Generation seed must be a bounded integer or None")
        if type(self.unit_provenance_verified) is not bool:
            raise TypeError("Unit provenance must be explicitly supplied as a boolean")

    @classmethod
    def from_mapping(cls, value):
        """Parse one coordinator JSON record without reading the bundle."""
        if not isinstance(value, dict) or set(value) != {field.name for field in fields(cls)}:
            raise ValueError("Prepared cohort fields must match the explicit coordinator contract")
        if not isinstance(value["root"], str):
            raise TypeError("Prepared cohort root must be an absolute JSON path string")
        return cls(**{**value, "root": Path(value["root"])})


def validate_execution_configuration(plan, bundle_paths):
    """Validate the authored plan and explicit provenance without opening data."""
    if not isinstance(plan, StudyPlan):
        raise TypeError("A frozen StudyPlan is required")
    plan = StudyPlan.model_validate_json(plan.model_dump_json())
    if plan.requested_model is None:
        raise ValueError("A pinned requested_model is required before execution")
    if not isinstance(bundle_paths, Mapping) or set(bundle_paths) != {row.cohort_id for row in plan.cohorts}:
        raise ValueError("Prepared bundles must cover exactly every planned cohort")
    recipes = {row.recipe_id: row for row in plan.dataset_recipes}
    prepared = dict(bundle_paths)
    for cohort in plan.cohorts:
        bundle = prepared[cohort.cohort_id]
        if not isinstance(bundle, PreparedCohortBundle):
            raise TypeError("Each cohort requires an explicit PreparedCohortBundle")
        if bundle.recipe_id != cohort.recipe_id or bundle.generation_seed != cohort.generation_seed:
            raise ValueError("Prepared cohort recipe/seed differs from the immutable plan")
        if recipes[cohort.recipe_id].source_kind == "synthetic" and cohort.generation_seed is None:
            raise ValueError("Synthetic execution requires seeds assigned in the immutable plan")
    for name in ("root", "input_sha256", "independence_id"):
        if len({getattr(bundle, name) for bundle in prepared.values()}) != len(prepared):
            raise ValueError("Independent cohort bundles must have distinct " + name)
    return plan, prepared


def initial_attempt_records(plan, bundle_paths):
    """Retain every planned model slot with explicitly unknown observed evidence."""
    plan, bundles = validate_execution_configuration(plan, bundle_paths)
    recipes = {row.recipe_id: row for row in plan.dataset_recipes}
    return [AttemptRecord(
        attempt_id=row.attempt_id, study_id=plan.study_id, plan_identity=row.plan_identity,
        task_identity=row.task_identity, cohort_id=row.cohort_id,
        independence_id=bundles[row.cohort_id].independence_id, scenario=row.scenario,
        condition=row.condition, actor_type="model_participant", repetition=row.repetition,
        source_kind=recipes[row.recipe_id].source_kind, input_sha256=None,
        shared_environment_sha256=None, harness_sha256=None, actual_model=None,
        requested_model=plan.requested_model, model_identity_verified=False, attempted=False,
        completion_status="not_started", accepted_final=False, scientific_success=None,
        report_accepted=None, workflow_passed=None, integrity_status="unavailable",
        biological_metrics={}, metric_missingness={}, diagnostic_outcomes={}, refusal_outcomes={},
        elapsed_sec=None, RSS_MB=None, tokens=None, cost=None,
        accounting_completeness=AccountingCompleteness(**dict.fromkeys(
            ("elapsed_sec", "RSS_MB", "tokens", "cost"), "unavailable")),
        trusted_tool_capture_complete=False, trusted_tool_calls=None,
    ) for row in schedule_attempts(plan)]


def _write_json(path, value, *, replace=False):
    from .supervision import write_coordinator_json
    write_coordinator_json(path, value, replace=replace, allow_data_execution=True)


def _file_digest(path, *, limit=250_000_000):
    from .evidence import _file_sha256
    return _file_sha256(path, limit)


def _tree_digest(root, *, sources_only=False, allowed_symlink_targets=()):
    """Bounded source/runtime fingerprint; compiled Python caches are excluded."""
    files, size = {}, 0
    paths = [root] if root.is_file() else sorted(root.rglob("*"))
    for path in paths:
        if "__pycache__" in path.parts or path.suffix in {".pyc", ".pyo"}:
            continue
        link = None
        target = path
        if path.is_symlink():
            if sources_only or not allowed_symlink_targets:
                raise ValueError("Fingerprint roots may not contain nested symlinks")
            target = path.resolve(strict=True)
            if not target.is_file() or not any(target == allowed or target.is_relative_to(allowed)
                                              for allowed in allowed_symlink_targets):
                raise ValueError("Runtime symlink must target a file inside already-admitted runtime roots")
            link = {"link_text": os.readlink(path), "resolved_target": str(target)}
        if not path.is_file() or (sources_only and path.suffix != ".py"):
            continue
        size += path.stat().st_size
        if size > 2_000_000_000 or len(files) >= 200_000:
            raise ValueError("Runtime/source fingerprint exceeds its bounded budget")
        name = path.name if root.is_file() else path.relative_to(root).as_posix()
        digest = _file_digest(target, limit=250_000_000)
        files[name] = digest if link is None else {**link, "target_sha256": digest}
    return content_sha256(files)


def _source_identity():
    return content_sha256({name: _tree_digest(_REPOSITORY / name, sources_only=True)
                           for name in _SOURCE_PACKAGES})


def _verify_bundle(bundle, recipe, plan_identity, cohort_id):
    from .evidence import _read, decode_json
    root = _safe_path(bundle.root)
    preparation = decode_json(_read(root / "private" / "preparation_receipt.json", 100_000))
    expected_bundle = {**vars(bundle), "root": str(bundle.root)}
    if (not isinstance(preparation, dict)
            or preparation.get("record_format") != "devbench.study.cohort-preparation/1"
            or preparation.get("plan_identity") != plan_identity
            or preparation.get("cohort_id") != cohort_id
            or preparation.get("bundle") != expected_bundle):
        raise ValueError("Prepared bytes are not bound to this exact plan/cohort receipt")
    manifest = decode_json(_read(root / "input_manifest.json", 32_000_000))
    files = manifest.get("files")
    if (manifest.get("record_format") != "devbench.study.input_manifest/1"
            or not isinstance(files, dict) or not 1 <= len(files) <= 20_000
            or content_sha256(files) != bundle.input_sha256
            or manifest.get("input_sha256") != bundle.input_sha256
            or manifest.get("measurement_type") != recipe.measurement_type):
        raise ValueError("Prepared input manifest does not match retained provenance")
    actual, total = {}, 0
    for path in sorted((root / "inputs").rglob("*")):
        _safe_path(path)
        if path.is_dir():
            continue
        size = path.stat().st_size
        total += size
        if total > 250_000_000 or len(actual) >= 20_000:
            raise ValueError("Prepared input bundle exceeds its bounded budget")
        actual[path.relative_to(root).as_posix()] = {"bytes": size, "sha256": _file_digest(path)}
    if actual != files:
        raise ValueError("Prepared input bytes differ from the retained manifest")
    if _file_digest(root / "private" / "truth.json", limit=50_000_000) != bundle.truth_sha256:
        raise ValueError("Private truth bytes differ from the separately retained hash")
    context = decode_json(_read(root / "inputs" / "task_inputs.json", 32_000_000))
    if (not isinstance(context, dict) or set(context) - _PUBLIC_CONTEXT_KEYS
            or context.get("record_format") != "devbench.study.task_inputs/1"
            or context.get("measurement_type") != recipe.measurement_type
            or context.get("path_base") != "bundle_root"):
        raise ValueError("Public task context has unsupported or private fields")
    if context.get("source_kind", recipe.source_kind) != recipe.source_kind:
        raise ValueError("Public task source scope differs from the locked recipe")
    if ("unit_provenance_verified" in context
            and context["unit_provenance_verified"] is not bundle.unit_provenance_verified):
        raise ValueError("Public biological-unit provenance differs from its coordinator receipt")
    if ("population_inference_permitted" in context
            and type(context["population_inference_permitted"]) is not bool):
        raise ValueError("Population-inference scope must be explicitly boolean")
    scopes = context.get("permitted_claim_scopes")
    if scopes is not None and (not isinstance(scopes, list) or not scopes or len(set(scopes)) != len(scopes)
                              or any(scope not in {"association", "descriptive", "inferred_ordering",
                                                   "curated_membership", "perturbation_response"} for scope in scopes)):
        raise ValueError("Permitted claim scopes must be a distinct supported list")
    if recipe.source_kind == "public_processed" and not bundle.unit_provenance_verified:
        if context.get("population_inference_permitted") is not False or scopes != ["descriptive"]:
            raise ValueError("Unverified public biological units permit descriptive scope only")
    genes = context.get("gene_ids")
    if (not isinstance(genes, list) or not 1 <= len(genes) <= 5000
            or any(not isinstance(gene, str) or not 1 <= len(gene) <= 200 for gene in genes)
            or len(set(genes)) != len(genes)
            or context.get("hypothesis_family_id") != content_sha256({
                "gene_ids": genes, "target": context.get("target", "temporal_omnibus")})):
        raise ValueError("A bounded fixed hypothesis family is required")
    for key in ("expression_path", "metadata_path"):
        relative = Path(context.get(key, ""))
        if relative.is_absolute() or ".." in relative.parts or not relative.parts or relative.parts[0] != "inputs":
            raise ValueError("Public data paths must be relative to the bundle inputs directory")
        if not (root / relative).exists():
            raise ValueError("Prepared input path does not exist")
    return manifest, context


def _runtime_roots(workspace, environment):
    """Grant individual third-party entries and Python runtime components only."""
    if environment.get("runtime_source_hashes"):
        raise ValueError("Study participants must not receive harness implementation source")
    site = workspace / ".venv" / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages"
    roots = {Path(sys.executable).resolve()}
    for name in environment["linked_dependency_entries"]:
        path = site / name
        if not path.is_symlink():
            raise ValueError("Expected individually linked ordinary dependency")
        roots.add(path.resolve())
    stdlib = Path(sysconfig.get_path("stdlib")).resolve()
    # Grant stdlib entries individually: its site-packages sibling may contain
    # editable coordinator hooks and must never receive a broad read grant.
    roots.update(path.resolve() for path in stdlib.iterdir()
                 if path.name not in {"site-packages", "dist-packages", "__pycache__"})
    library = sysconfig.get_config_var("LDLIBRARY")
    library_dir = sysconfig.get_config_var("LIBDIR")
    if library and library_dir:
        path = (Path(library_dir) / library).resolve()
        if path.is_file():
            roots.add(path)
    if sys.platform == "darwin":
        framework = Path(sys.base_prefix) / "Python"
        if framework.is_file():
            roots.add(framework.resolve())
    return tuple(sorted(roots))


def _environment_identity(runtime_roots, plan, executable):
    from .cli_identity import codex_component_hashes
    return content_sha256({"runtime_files": {str(path): _tree_digest(path, allowed_symlink_targets=runtime_roots) for path in runtime_roots},
                           "python": sys.version, "budgets": plan.budgets.model_dump(mode="json"),
                           "codex_executable_sha256": _file_digest(executable),
                           "codex_component_sha256": codex_component_hashes(executable),
                           "reasoning_effort": plan.reasoning_effort,
                           "fingerprint_scope": "runtime_source_and_native_bytes_excluding_python_caches"})


def _stage_inputs(workspace, bundle, manifest, context, attempt, plan, *, allow_data_execution=False):
    if allow_data_execution is not True:
        raise PermissionError("Study input materialization is disabled without explicit authorization")
    from .answers import StudyAnswer
    from .guidance import COMMON_GUIDANCE, participant_guidance, sdk_cheatsheet
    for relative in manifest["files"]:
        source, destination = bundle.root / relative, workspace / relative
        destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with source.open("rb") as reader, destination.open("xb") as writer:
            shutil.copyfileobj(reader, writer, length=1_048_576)
        destination.chmod(0o444)
    public = {**context, **attempt.participant_projection(plan), "input_sha256": bundle.input_sha256,
              "path_base": "absolute", "time_col": context.get("time_col", "numeric_time")}
    for key in ("expression_path", "metadata_path"):
        public[key] = str(workspace / context[key])
    _write_json(workspace / "input_manifest.json", manifest)
    _write_json(workspace / "task_inputs.json", public)
    _write_json(workspace / "answer_schema.json", StudyAnswer.model_json_schema())
    from .submission_validation import stage_submission_validator
    validator = stage_submission_validator(workspace, "study", max_answer_bytes=8_000_000)
    readme = (COMMON_GUIDANCE + "\nRead condition_guidance.md and task_inputs.json.\n"
              + "Scientific question: " + context["required_scientific_question"] + "\n"
              + f"Staged Python: {workspace / '.venv/bin/python'}\n"
              + f"Execute: {workspace / '.venv/bin/python'} {workspace / 'solve.py'}\n"
              + f"\nBefore submitting, run this public contract check in a separate command:\n{validator.command}\n"
              + "Fix reported contract errors, rerun solve.py, then validate again. This check supplies no grading references or scientific score.\n")
    texts = {"TASK_README.md": readme, "sdk_cheatsheet.py": sdk_cheatsheet(attempt.condition),
             "condition_guidance.md": participant_guidance(attempt.condition)[len(COMMON_GUIDANCE):]}
    if attempt.condition != "baseline":
        texts["devharness_client.py"] = (Path(__file__).parent / "client.py").read_text()
    for name, value in texts.items():
        with (workspace / name).open("x") as handle:
            handle.write(value)
    protected = (workspace / "inputs", workspace / ".venv", workspace / "control",
                 *(workspace / name for name in (*texts, "input_manifest.json", "task_inputs.json", "answer_schema.json")),
                 *validator.protected_paths)
    for path in protected:
        if path.is_file():
            path.chmod(0o444)
    # Condition-specific bytes are deliberately excluded from common runtime identity.
    harness_hash = content_sha256({name: hashlib.sha256(value.encode()).hexdigest()
                                  for name, value in texts.items()
                                  if name in {"condition_guidance.md", "sdk_cheatsheet.py", "devharness_client.py"}})
    staged_hashes = {str(workspace / relative): item["sha256"] for relative, item in manifest["files"].items()}
    staged_hashes.update({str(path): _file_digest(path) for path in protected if path.is_file()})
    return public, protected, staged_hashes, harness_hash


def _observed_accounting(metadata, elapsed):
    def number(value):
        try:
            return value if type(value) in (int, float) and math.isfinite(value) and value >= 0 else None
        except OverflowError:
            return None
    rss = number(metadata.get("peak_rss_mb"))
    tokens = metadata.get("tokens")
    tokens = tokens if type(tokens) is int and tokens >= 0 else None
    cost = number(metadata.get("reported_usage_cost_usd", metadata.get("cost_usd")))
    executions = metadata.get("script_executions") if metadata.get("inference_dispatch_attempted") is True else None
    return {"elapsed_sec": elapsed, "RSS_MB": rss, "tokens": tokens, "cost": cost,
            "observed_script_execution_count": len(executions) if isinstance(executions, list) else None,
            "final_execution_id": metadata.get("final_execution_id") if executions else None,
            "accounting_completeness": AccountingCompleteness(
                elapsed_sec="complete", RSS_MB="partial" if rss is not None else "unavailable",
                tokens=("complete" if metadata.get("usage_complete") is True else "partial")
                       if tokens is not None else "unavailable",
                # Subscription and broker compute cost are not fully observed.
                cost="partial" if cost is not None else "unavailable")}


def _failure_outcome(error, *, stage, attempted, metadata):
    """Classify control-plane evidence without guessing from exception text."""
    if not attempted:
        return ("blocked", "dispatch_checkpoint_failed" if metadata.get("dispatch_stage") ==
                "checkpoint_failed_before_launch" else "preparation_failure")
    if stage == "execution_capture_validation":
        return "tool_error", "execution_capture_incomplete"
    if isinstance(error, TimeoutError):
        return "timeout", "participant_deadline"
    if metadata.get("event_capture_complete") is False:
        return "tool_error", "execution_capture_incomplete"
    if stage == "script_provenance_validation":
        return "invalid_output", "script_execution_unverified"
    # The inherited adapter validates the executed answer only after a
    # successful, completely captured process. Without those observations an
    # arbitrary ValueError/RuntimeError is not evidence of participant failure.
    if (stage == "participant_execution" and metadata.get("event_capture_complete") is True
            and metadata.get("returncode") == 0):
        if metadata.get("script_execution_observed") is False:
            return "invalid_output", "script_execution_unverified"
        if isinstance(error, ValueError):
            return "invalid_output", "answer_validation_failed"
    return "provider_error", "participant_execution_failure"


def _attempt(plan, attempt, initial, bundle, workspace, output, other_workspaces,
             private_roots, source_hash, common_environment, executable, *, allow_data_execution=False, preflight_only=False):
    if allow_data_execution is not True:
        raise PermissionError("Study attempt execution is disabled without explicit authorization")
    from devbench.staging import stage_python
    from .adapter import StudyCodeActionAdapter
    from .admission import inspect_admission
    from .broker import BrokerPolicy, ToolBroker
    from .evidence import _read, decode_json, evaluate_workflow, verify_receipts
    from .permissions import probe_boundary
    from .scoring import score_answer
    from .transport import BrokerTransport
    from .answers import StudyAnswer

    values = {} if preflight_only else initial.model_dump()
    details = {"attempt_id": attempt.attempt_id, "workspace": str(workspace), "errors": [],
               "resource_scope": {"RSS_MB": "outer_owned_attempt_process_watchdog_covers_CLI_and_broker_workers",
                                  "memory_limits": "one_aggregate_sampled_process_tree_ceiling; local_worker_limits_additional",
                                  "cost": "subscription_and_broker_compute_cost_not_fully_observed"}}
    adapter = broker = transport = None
    staged_hashes, runtime_roots, retained_root = {}, (), None
    started = time.monotonic()
    attempted = False
    answer = None
    interrupted = False
    failure_stage = "preparation"
    recipe = next(row for row in plan.dataset_recipes if row.recipe_id == attempt.recipe_id)
    try:
        if _source_identity() != source_hash:
            raise RuntimeError("Coordinator implementation changed since study sealing")
        manifest, context = _verify_bundle(bundle, recipe, attempt.plan_identity, attempt.cohort_id)
        values["input_sha256"] = bundle.input_sha256
        environment = stage_python(workspace, assisted=False)
        runtime_roots = _runtime_roots(workspace, environment)
        environment_hash = _environment_identity(runtime_roots, plan, executable)
        if common_environment and common_environment[0] != environment_hash:
            raise RuntimeError("Shared runtime bytes differ from earlier conditions")
        if not common_environment:
            common_environment.append(environment_hash)
        values["shared_environment_sha256"] = environment_hash
        public, protected, staged_hashes, harness_hash = _stage_inputs(
            workspace, bundle, manifest, context, attempt, plan, allow_data_execution=True)
        values["harness_sha256"] = harness_hash
        _write_json(output / "staging_checkpoint.json", {
            "input_sha256": bundle.input_sha256, "shared_environment_sha256": environment_hash,
            "harness_sha256": harness_hash, "runtime_roots": [str(path) for path in runtime_roots],
            "staged_file_hashes": staged_hashes})
        (workspace / "control").mkdir(mode=0o700)
        assisted = attempt.condition != "baseline"
        socket_path = workspace / "control" / "tools.sock" if assisted else None
        boundary = PermissionBoundary(workspace=workspace, runtime_roots=runtime_roots,
            private_roots=tuple(private_roots) + tuple(other_workspaces),
            protected_paths=protected, socket_path=socket_path)
        details["boundary"] = boundary.describe()
        probe = probe_boundary(boundary, executable, Path(environment["python"]), allow_configuration_probe=True)
        details["boundary_probe"] = probe
        if probe.get("verified") is not True or probe.get("configuration") != boundary.config_overrides():
            raise RuntimeError("Exact participant boundary failed standalone verification")
        if assisted:
            admission = inspect_admission(bundle.root / context["metadata_path"],
                unit_provenance_verified=bundle.unit_provenance_verified,
                time_col=public["time_col"], biological_unit_column=public["biological_unit_column"],
                allow_data_execution=True)
            policy = BrokerPolicy(condition=attempt.condition, input_sha256=bundle.input_sha256,
                measurement_type=recipe.measurement_type, gene_ids=tuple(public["gene_ids"]),
                population_inference_allowed=admission["population_inference_allowed"],
                timeout_sec=plan.budgets.timeout_sec, implementation_sha256=source_hash,
                max_memory_mb=plan.budgets.max_memory_mb,
                unit_provenance_verified=bundle.unit_provenance_verified,
                time_col=public["time_col"], biological_unit_column=public["biological_unit_column"])
            broker = ToolBroker(policy, {public[key]: bundle.root / context[key]
                                         for key in ("expression_path", "metadata_path")},
                                output / "receipts", allow_data_execution=True, defer_start=True)
            transport = BrokerTransport(broker, socket_path, allow_data_execution=True)
        def dispatch_observer(observation):
            # Executed by trusted coordinator code just before the CLI model
            # subprocess invocation. Never a participant-writable declaration.
            _write_json(output / "dispatch_checkpoint.json", {
                "attempt_id": attempt.attempt_id, "attempted": True, **observation})

        adapter = StudyCodeActionAdapter(boundary=boundary, requested_model=plan.requested_model,
            condition=attempt.condition, reasoning_effort=plan.reasoning_effort,
            executable=str(executable), timeout_sec=plan.budgets.timeout_sec,
            max_mem_mb=plan.budgets.max_memory_mb, thread_limit=plan.budgets.cpu_threads,
            tool_capability_file=transport.capability_file if transport else None,
            coordinator_dispatch_callback=dispatch_observer, model_identity_output=output / "model_identity.json")
        from .exec_preflight import run_exec_preflight
        if broker is not None:
            broker.enable_execution_scopes()
            adapter.execution_observer = broker.observe_execution
        failure_stage = "exec_preflight"
        details["exec_preflight"] = run_exec_preflight(adapter, output / "exec-preflight", allow_model_execution=True)
        if details["exec_preflight"].get("verified") is not True:
            raise RuntimeError("Exec-path permission preflight failed; scored participant was not launched")
        if not preflight_only:
            from .exec_preflight import require_scored_model_identity
            require_scored_model_identity(details["exec_preflight"])
            if broker is not None:
                details["tool_analysis_window"] = broker.start_analysis()
            failure_stage = "participant_execution"
            answer = adapter.execute("Read the protected task instructions.", workspace, allow_data_execution=True)
            attempted = adapter.metadata.get("inference_dispatch_attempted") is True
            failure_stage = "execution_capture_validation"
            if adapter.metadata.get("event_capture_complete") is not True:
                raise RuntimeError("Coordinator execution-event capture is incomplete")
            failure_stage = "script_provenance_validation"
            if adapter.metadata.get("script_execution_observed") is not True:
                raise RuntimeError("Direct script execution provenance is unverified")
            values["completion_status"] = "completed"
    except KeyboardInterrupt:
        interrupted = True
        details["errors"].append("coordinator_interrupted")
        values["completion_status"] = "blocked"
    except Exception as error:
        details["errors"].append(f"{type(error).__name__}: {error}"[:2000])
        metadata = adapter.metadata if adapter is not None else {}
        attempted = metadata.get("inference_dispatch_attempted") is True
        status, code = _failure_outcome(error, stage=failure_stage, attempted=attempted, metadata=metadata)
        values["completion_status"] = status
        details["failure"] = {"stage": failure_stage, "code": code, "completion_status": status}
    finally:
        if adapter is not None:
            attempted = adapter.metadata.get("inference_dispatch_attempted") is True
            details["adapter"] = adapter.metadata
        transport_ok = False
        if transport is not None:
            try:
                details["transport"] = transport.close()
                transport_ok = details["transport"].get("transport_stopped") is True
            except Exception as error:
                details["errors"].append(f"transport_shutdown:{type(error).__name__}: {error}"[:2000])
        if broker is not None:
            try:
                retained_root = broker.close()
                details["retained_tool_root"] = retained_root
                _write_json(output / "retained_tool_root.json", retained_root)
            except Exception as error:
                details["errors"].append(f"receipt_seal:{type(error).__name__}: {error}"[:2000])
        values["attempted"] = attempted
        values.update(_observed_accounting(adapter.metadata if adapter else {}, time.monotonic() - started))
        if attempted and not preflight_only:
            observed = adapter.metadata.get("actual_model") if adapter else None
            # Only the adapter's consistent provider completion-event identity
            # can verify the runtime model; requests never become observations.
            values["actual_model"] = observed if isinstance(observed, str) and 0 < len(observed) <= 300 else None
            values["model_identity_verified"] = (
                values["actual_model"] is not None and adapter.metadata.get("model_identity_verified") is True)
            try:
                _verify_bundle(bundle, recipe, attempt.plan_identity, attempt.cohort_id)
                if (_source_identity() != source_hash
                        or _environment_identity(runtime_roots, plan, executable) != values["shared_environment_sha256"]
                        or any(_file_digest(Path(path)) != digest for path, digest in staged_hashes.items())):
                    raise RuntimeError("Protected input, runtime or source integrity changed")
                from .cli_identity import verify_codex_identity
                verify_codex_identity(details["exec_preflight"]["codex_identity"])
                values["integrity_status"] = "verified"
            except Exception as error:
                values["integrity_status"] = "failed"
                details["errors"].append(f"integrity:{type(error).__name__}: {error}"[:2000])
        if not preflight_only and attempted and values["integrity_status"] == "verified":
            evidence = None
            if broker is not None and transport_ok and retained_root is not None:
                try:
                    evidence = verify_receipts(output / "receipts", expected_receipt=retained_root,
                        expected_input_sha256=bundle.input_sha256, expected_implementation_sha256=source_hash,
                        allow_evidence_read=True)
                    if evidence.condition != attempt.condition:
                        raise ValueError("Receipt condition differs from scheduled condition")
                    values.update(trusted_tool_capture_complete=True, trusted_tool_calls=len(evidence.events))
                    from .execution_usage import selected_tool_calls
                    values["final_execution_tool_calls"] = selected_tool_calls(evidence, values.get("final_execution_id"))
                except Exception as error:
                    details["errors"].append(f"receipt_verification:{type(error).__name__}: {error}"[:2000])
            elif attempt.condition == "baseline":
                # The baseline has no registered broker endpoint. This count
                # concerns broker adoption only, not all participant computation.
                values.update(trusted_tool_capture_complete=True, trusted_tool_calls=0)
                values["final_execution_tool_calls"] = 0 if values.get("final_execution_id") else None
            if answer is not None and values["completion_status"] == "completed":
                try:
                    parsed = StudyAnswer.model_validate_json(canonical_json_bytes(answer))
                    if parsed.task_id != attempt.task_identity or parsed.input_sha256 != bundle.input_sha256:
                        raise ValueError("Final answer differs from the scheduled input identity")
                    values["accepted_final"] = True
                    values["report_accepted"] = attempt.condition != "enforced"
                    policy_errors = []
                    if public.get("population_inference_permitted") is False:
                        if (parsed.claim_scope != "descriptive"
                                or (parsed.analysis is not None and (
                                    parsed.analysis.inference_scope != "descriptive"
                                    or parsed.analysis.reference.biological_unit_reference != "observed_subset"))
                                or any(row.p_value is not None or row.q_value is not None or row.claimed_discovery
                                       or row.confidence_interval is not None for row in parsed.gene_statistics)
                                or parsed.lineage is not None or parsed.regulatory_edges or parsed.tf_activation_order):
                            policy_errors.append("public_observations_do_not_support_population_or_latent_claims")
                    permitted_scopes = public.get("permitted_claim_scopes")
                    if permitted_scopes is not None and parsed.claim_scope not in permitted_scopes:
                        policy_errors.append("claim_scope_not_permitted_by_shared_task")
                    details["task_scope_policy_errors"] = policy_errors
                    if evidence is not None:
                        workflow = evaluate_workflow(parsed, evidence, task_context=public, allow_evidence_read=True)
                        details["workflow"] = workflow
                        values["workflow_passed"] = workflow["workflow_passed"]
                        if attempt.condition == "enforced":
                            values["report_accepted"] = workflow["workflow_passed"]
                    if policy_errors:
                        values["report_accepted"] = False
                    # Truth is decoded only after participant completion, with
                    # the private bundle outside its verified OS read boundary.
                    truth = decode_json(_read(bundle.root / "private" / "truth.json", 50_000_000))
                    score = score_answer(parsed, truth, thresholds=plan.thresholds,
                                         expected_task_id=attempt.task_identity,
                                         expected_input_sha256=bundle.input_sha256)
                    if score["source_kind"] != recipe.source_kind:
                        raise ValueError("Reference source scope differs from the locked recipe")
                    details["score"] = score
                    values.update(scientific_success=score["scientific_success"], biological_metrics=score["metrics"],
                        metric_missingness=score["missingness"], gene_rows=tuple(score["gene_rows"]),
                        diagnostic_outcomes={key: value for key, value in score["diagnostics"].items()
                                             if type(value) is bool or value is None})
                except Exception as error:
                    details["errors"].append(f"answer_evaluation:{type(error).__name__}: {error}"[:2000])
                    if not values["accepted_final"]:
                        values["completion_status"] = "invalid_output"
        try:
            _write_json(output / "execution.json", details)
        except Exception as error:
            # Losing the retained execution record must not leave accepted
            # scientific evidence. The artifact itself remains in its workspace.
            values.update(accepted_final=False, scientific_success=None, report_accepted=None,
                          workflow_passed=None, biological_metrics={}, metric_missingness={},
                          diagnostic_outcomes={}, gene_rows=(), trusted_tool_capture_complete=False,
                          trusted_tool_calls=None, final_execution_tool_calls=None)
            if attempted:
                values["completion_status"] = "tool_error"
            details = {"attempt_id": attempt.attempt_id, "workspace": str(workspace),
                       "errors": [f"execution_evidence_persistence_failed:{type(error).__name__}: {error}"[:2000]],
                       "attempted": attempted, "complete_execution_capture": False}
            _write_json(output / "execution_error.json", details)
    if preflight_only:
        from .preflight_only import finish_probe_worker
        return finish_probe_worker(output, attempt.condition, details, interrupted=interrupted, broker=broker,
            adapter=adapter, runtime_sha256=values.get("shared_environment_sha256")), interrupted
    return AttemptRecord(**values), interrupted


def _attempt_worker(arguments, completion=None, release=None, preflight_only=False, *, allow_data_execution=False):
    """Trusted scientific worker; generic supervision owns the spawn handshake.

    The optional legacy handshake arguments remain accepted for guard-only
    callers. They no longer control process supervision or result persistence.
    """
    if allow_data_execution is not True:
        raise PermissionError("Study worker execution is disabled without explicit authorization")
    output = arguments[5]
    record, interrupted = _attempt(*arguments, allow_data_execution=True, preflight_only=preflight_only)
    if preflight_only:
        return
    _write_json(output / "attempt_result.json", {
        "record": record.model_dump(mode="json"), "interrupted": interrupted})


def _run_owned_attempt(plan, attempt, initial, bundle, workspace, output, other_workspaces,
                       private_roots, source_hash, common_environment, executable, *, allow_data_execution=False):
    if allow_data_execution is not True:
        raise PermissionError("Study process execution is disabled without explicit authorization")
    from .evidence import _read, decode_json
    from .records import parse_attempt_record
    from .supervision import supervise_coordinator

    arguments = (plan, attempt, initial, bundle, workspace, output, other_workspaces,
                 private_roots, source_hash, list(common_environment), executable)
    started = time.monotonic()
    observed_process = supervise_coordinator(_attempt_worker, (arguments,), output, plan.budgets,
                                             allow_data_execution=True)
    elapsed, rss = observed_process.elapsed_sec, observed_process.RSS_MB
    interrupted, resource_failure = observed_process.interrupted, observed_process.resource_failure
    failure_kind, error, exitcode = observed_process.failure_kind, observed_process.error, observed_process.exitcode
    failure_completion = "timeout" if failure_kind == "shared_deadline" else "tool_error"
    supervision = {"scope": "isolated_coordinator_Codex_and_broker_worker_process_tree",
                   "measurement": "sampled_process_tree_RSS", "elapsed_sec": elapsed, "RSS_MB": rss,
                   "memory_limit_mb": plan.budgets.max_memory_mb, "timeout_sec": plan.budgets.timeout_sec,
                   "resource_failure": resource_failure, "error": error, "failure_kind": failure_kind,
                   "worker_exitcode": exitcode, "process_supervision": observed_process.as_dict(),
                   "recovery_integrity_execution": "supervising_parent_after_participant_tree_termination",
                   "recovery_parent_RSS_MB": None,
                   "memory_ceiling_scope": "owned_attempt_tree; parent_recovery_integrity_is_outside_this_ceiling",
                   "limitations": ["Sampling may miss short lived allocations and process descendants",
                                   "Supervising-parent recovery integrity memory is unmeasured",
                                   "Broker monetary cost and total subscription cost are unavailable"]}
    _write_json(output / "supervision.json", supervision)
    result_path = output / "attempt_result.json"
    record = initial
    retained_result = False
    if result_path.is_file():
        # Persistence precedes the worker's final release handshake. A later
        # interrupt, monitor failure or abnormal exit must not erase already
        # captured model, usage or tool evidence in this coordinator-owned file.
        try:
            payload = _read(result_path, 64_000_000)
            retained_receipt = next((item for item in observed_process.artifacts if item.kind == "result"), None)
            if retained_receipt is None or hashlib.sha256(payload).hexdigest() != retained_receipt.sha256:
                raise ValueError("Raw child result differs from its bounded supervision capture")
            result = decode_json(payload)
            candidate = parse_attempt_record(result["record"])
            if type(result.get("interrupted")) is not bool:
                raise ValueError("Owned worker interruption state is malformed")
            bindings = ("attempt_id", "study_id", "plan_identity", "task_identity", "cohort_id",
                        "independence_id", "scenario", "condition", "actor_type", "repetition",
                        "source_kind", "requested_model")
            if any(getattr(candidate, field) != getattr(initial, field) for field in bindings):
                raise ValueError("Owned worker returned a different scheduled attempt")
            if candidate.input_sha256 not in {None, bundle.input_sha256}:
                raise ValueError("Owned worker returned a different input identity")
            record = candidate
            interrupted = interrupted or result["interrupted"]
            retained_result = True
            supervision.update(raw_attempt_result_path=str(result_path),
                               raw_attempt_result_sha256=hashlib.sha256(payload).hexdigest())
        except Exception as caught:
            supervision["result_validation_error"] = f"{type(caught).__name__}: {caught}"[:2000]
    if failure_kind is None and (exitcode != 0 or not retained_result):
        failure_kind = ("worker_result_invalid" if "result_validation_error" in supervision
                        else "worker_exit_failure" if exitcode != 0 else "worker_result_missing")
        supervision["failure_kind"] = failure_kind
        supervision["error"] = f"Owned attempt ended without an orderly finalization ({failure_kind})"
    worker_error_path = output / "worker_error.json"
    if worker_error_path.is_file():
        try:
            worker_error_bytes = _read(worker_error_path, 10_000)
            worker_error = decode_json(worker_error_bytes)
            supervision.update(worker_error_path=str(worker_error_path),
                               worker_error_sha256=hashlib.sha256(worker_error_bytes).hexdigest(),
                               worker_error=worker_error.get("error"))
        except Exception as caught:
            supervision["worker_error_validation_error"] = f"{type(caught).__name__}: {caught}"[:2000]
    supervisor_failed = failure_kind is not None or exitcode != 0
    supervision["completed_result_retained"] = retained_result
    if supervisor_failed or not retained_result:
        values = record.model_dump()
        checkpoint = output / "dispatch_checkpoint.json"
        attempted = record.attempted
        # A validated final worker record supersedes its earlier checkpoint;
        # in particular it can report a callback failure before actual launch.
        if not retained_result and checkpoint.is_file():
            try:
                observed = decode_json(_read(checkpoint, 100_000))
                attempted = observed.get("attempted") is True and observed.get("attempt_id") == attempt.attempt_id
            except Exception as caught:
                supervision["checkpoint_validation_error"] = f"{type(caught).__name__}: {caught}"[:2000]
        stage_path = output / "staging_checkpoint.json"
        staged = None
        if stage_path.is_file():
            try:
                staged = decode_json(_read(stage_path, 32_000_000))
                hashes = ("input_sha256", "shared_environment_sha256", "harness_sha256")
                if (not isinstance(staged, dict) or any(not isinstance(staged.get(name), str)
                        or _SHA256.fullmatch(staged[name]) is None for name in hashes)
                        or staged["input_sha256"] != bundle.input_sha256):
                    raise ValueError("Staging checkpoint lacks valid matching input/runtime hashes")
                for name in hashes:
                    if retained_result and values[name] not in {None, staged.get(name)}:
                        raise ValueError("Staging checkpoint differs from the retained final record")
                for name in hashes:
                    values[name] = staged.get(name)
            except Exception as caught:
                staged = None
                supervision["staging_validation_error"] = f"{type(caught).__name__}: {caught}"[:2000]
        values["attempted"] = attempted
        values["completion_status"] = failure_completion if attempted else "blocked"
        values.update(accepted_final=False, scientific_success=None,
                      report_accepted=False if record.report_accepted is not None else None)
        if attempted and staged is not None:
            try:
                recipe = next(row for row in plan.dataset_recipes if row.recipe_id == attempt.recipe_id)
                _verify_bundle(bundle, recipe, attempt.plan_identity, attempt.cohort_id)
                roots = tuple(Path(path) for path in staged["runtime_roots"])
                if (_source_identity() != source_hash
                        or _environment_identity(roots, plan, executable) != values["shared_environment_sha256"]
                        or any(_file_digest(Path(path)) != digest for path, digest in staged["staged_file_hashes"].items())):
                    raise ValueError("Post-interruption integrity differs from the staged state")
                # A successful later read cannot erase a previously observed
                # integrity failure, including a mutation that was restored.
                if record.integrity_status != "failed":
                    values["integrity_status"] = "verified"
            except Exception as caught:
                values["integrity_status"] = "failed"
                supervision["integrity_error"] = f"{type(caught).__name__}: {caught}"[:2000]
        elif attempted and record.integrity_status != "failed":
            values["integrity_status"] = "unavailable"
        if values["integrity_status"] != "verified":
            values.update(workflow_passed=None, biological_metrics={}, metric_missingness={},
                          diagnostic_outcomes={}, refusal_outcomes={}, gene_rows=())
        record = AttemptRecord(**values)
    # Include parent-side recovery validation in elapsed cost, even though its
    # memory remains explicitly outside the ended participant-tree observer.
    elapsed = max(time.monotonic() - started, record.elapsed_sec or 0)
    supervision["elapsed_sec"] = elapsed
    values = record.model_dump()
    available_rss = [value for value in (rss, record.RSS_MB) if value is not None]
    retained_rss = max(available_rss) if available_rss else None
    values.update(elapsed_sec=elapsed, RSS_MB=retained_rss)
    values["accounting_completeness"].update(elapsed_sec="complete", RSS_MB="partial" if retained_rss is not None else "unavailable")
    record = AttemptRecord(**values)
    supervision["integrity_status"] = record.integrity_status
    _write_json(output / "supervision.json", supervision, replace=True)
    if record.shared_environment_sha256 is not None and not common_environment:
        common_environment.append(record.shared_environment_sha256)
    record_path = output / "record.json"
    _write_json(record_path, record.model_dump(mode="json"))
    detail_path = output / "execution.json"
    artifact = {"record_path": str(record_path), "record_sha256": _file_digest(record_path),
                "gene_row_count": len(record.gene_rows), "gene_rows_omitted_from_aggregate": True,
                "execution_detail_path": str(detail_path) if detail_path.is_file() else None,
                "execution_detail_sha256": _file_digest(detail_path) if detail_path.is_file() else None}
    # Full gene evidence stays in the retained per-attempt record. The aggregate
    # carries every scalar/biological metric without hundreds of repeated arrays.
    compact = record.model_copy(update={"gene_rows": ()})
    return compact, interrupted, artifact


def execute_study(plan, bundle_paths, output_dir, *, workspace_parent,
                  allow_data_execution=False, codex_executable="codex", allow_exec_preflight=False,
                  preflight_only=False, max_provider_sessions=6, preflight_conditions=None):
    """Run each model slot once using already prepared coordinator bundles.

    All output/workspace paths must be fresh and protected from platform scratch.
    workspace_parent must be under this repository's tmp/ directory. A passed
    flag authorizes future execution; it does not waive missing provenance,
    unsupported token ceilings or failed sandbox preflight. Scripted controls
    retain separate planned records and are never substituted for model slots.
    Standalone Python callers must use a module-level __main__ guard because
    each trusted coordinator attempt uses the multiprocessing spawn method.
    """
    if type(preflight_only) is not bool:
        raise ValueError("preflight_only must be boolean")
    if preflight_conditions is not None and not preflight_only:
        raise ValueError("Condition selection requires preflight-only mode")
    if preflight_only:
        from .preflight_only import execute_preflight_only
        return execute_preflight_only("classic", plan, bundle_paths, output_dir, workspace_parent,
            codex_executable=codex_executable, max_provider_sessions=max_provider_sessions,
            allow_exec_preflight=allow_exec_preflight, preflight_conditions=preflight_conditions)
    if allow_data_execution is not True:
        raise PermissionError("Study data/model execution is disabled without explicit authorization")
    if allow_exec_preflight is not True:
        raise PermissionError("Scored execution requires explicit authorization for its additional exec preflight session")
    plan, bundles = validate_execution_configuration(plan, bundle_paths)
    from .preflight_only import require_provider_budget
    require_provider_budget(2 * len(schedule_attempts(plan)), max_provider_sessions)
    destination = require_protected_storage(_safe_path(output_dir))
    workspace_parent = require_protected_storage(_safe_path(workspace_parent))
    if not workspace_parent.is_relative_to(_REPOSITORY / "tmp"):
        raise ValueError("Participant workspaces must be under the repository tmp directory")
    roots = (destination, *(bundle.root for bundle in bundles.values()))
    if any(workspace_parent.is_relative_to(root) or root.is_relative_to(workspace_parent) for root in roots):
        raise ValueError("Participant parent and private coordinator storage must be disjoint")
    if any(destination.is_relative_to(bundle.root) or bundle.root.is_relative_to(destination) for bundle in bundles.values()):
        raise ValueError("Execution output must be disjoint from prepared input bundles")
    preparation = prepare_plan(plan, destination)
    immutable_hashes = {destination / name: digest for name, digest in preparation["file_sha256"].items()}
    for path in immutable_hashes:
        path.chmod(0o444)
    attempts = schedule_attempts(plan)
    records = initial_attempt_records(plan, bundles)
    _write_json(destination / "attempt_records.json", [row.model_dump(mode="json") for row in records])
    _write_json(destination / "prepared_bundle_receipts.json", {
        key: {**vars(bundle), "root": str(bundle.root)} for key, bundle in bundles.items()})
    controls = schedule_controls(plan)
    # This entry point has no scripted execution registry. Preserve these
    # separately as unrun, even when every model participant is processed.
    if controls:
        _write_json(destination / "control_outcomes.json", controls)
    run = {"record_format": "devbench.study.execution/1", "study_id": plan.study_id,
           "planned_exec_preflight_sessions": len(attempts), "maximum_planned_provider_sessions": 2 * len(attempts),
           "max_provider_sessions": max_provider_sessions,
           "preflight_accounting_scope": "separate per-attempt exec-preflight evidence; not scored participant tokens",
           "plan_identity": plan.sha256(), "status": "started", "execution_authorized": True,
           "planned_attempt_count": len(attempts), "attempted_count": 0, "processed_attempt_count": 0,
           "all_model_slots_processed": False, "planned_control_count": len(controls),
           "controls_executed": False, "comprehensive_study_execution": False,
           "gene_detail_storage": "per_attempt", "gene_rows_omitted_from_aggregate": True,
           "retained_gene_row_count": 0, "attempt_artifacts": {},
           "attempt_records_sha256": _file_digest(destination / "attempt_records.json"),
           "calibration_status": "uncalibrated", "errors": [], "output_dir": str(destination)}
    def save_run():
        from .preflight_only import preflight_accounting
        run["preflight_accounting"] = preflight_accounting(
            destination / "attempts" / item.attempt_id / "exec-preflight" for item in attempts)
        _write_json(destination / "execution.json", run, replace=(destination / "execution.json").exists())
    save_run()
    thread_keys = ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS",
                   "VECLIB_MAXIMUM_THREADS")
    original_thread_environment = {key: os.environ.get(key) for key in thread_keys}
    try:
        if plan.budgets.token_budget is not None:
            raise RuntimeError("Token ceilings are not enforceable by the registered study adapter")
        executable = shutil.which(str(codex_executable))
        if executable is None:
            raise RuntimeError("Pinned participant executable is unavailable")
        executable = Path(executable).resolve()
        from .cli_identity import read_codex_identity
        run["codex_identity"] = read_codex_identity(executable)
        run["codex_executable_sha256"] = run["codex_identity"]["executable_sha256"]
        run["codex_version"] = run["codex_identity"]["version"]
        os.environ.update({key: str(plan.budgets.cpu_threads) for key in thread_keys})
        workspace_parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        workspaces = [Path(tempfile.mkdtemp(prefix="p-", dir=workspace_parent)).resolve() for _ in attempts]
        if any(len(str(workspace / "control/tools.sock").encode()) > 100 for workspace in workspaces):
            raise ValueError("Participant parent is too long for a protected Unix tool socket")
        source_hash = _source_identity()
        run["implementation_sha256"] = source_hash
        private_roots = [destination, *(bundle.root for bundle in bundles.values()),
                         *(_REPOSITORY / name for name in _SOURCE_PACKAGES)]
        common_environment = []
        for index, (attempt, workspace) in enumerate(zip(attempts, workspaces)):
            from .cli_identity import verify_codex_identity
            verify_codex_identity(run["codex_identity"])
            if any(_file_digest(path) != digest for path, digest in immutable_hashes.items()):
                raise RuntimeError("Immutable plan or schedule changed")
            if _file_digest(executable) != run["codex_executable_sha256"]:
                raise RuntimeError("Pinned Codex executable bytes changed")
            output = destination / "attempts" / attempt.attempt_id
            output.mkdir(mode=0o700, parents=True)
            siblings = [path for path in workspace_parent.iterdir() if path.resolve() != workspace]
            records[index], interrupted, artifact = _run_owned_attempt(plan, attempt, records[index], bundles[attempt.cohort_id],
                workspace, output, siblings, private_roots, source_hash, common_environment, executable,
                allow_data_execution=True)
            run["attempt_artifacts"][attempt.attempt_id] = artifact
            run["retained_gene_row_count"] += artifact["gene_row_count"]
            run["processed_attempt_count"] = index + 1
            run["attempted_count"] = sum(row.attempted for row in records)
            _write_json(destination / "attempt_records.json", [row.model_dump(mode="json") for row in records], replace=True)
            run["attempt_records_sha256"] = _file_digest(destination / "attempt_records.json")
            save_run()
            if interrupted:
                raise KeyboardInterrupt
        run["all_model_slots_processed"] = True
        run["status"] = "finished" if all(row.attempted for row in records) else "finished_with_blocked_attempts"
        run["comprehensive_study_execution"] = all(
            row.attempted and row.completion_status == "completed" and row.integrity_status == "verified"
            and row.trusted_tool_capture_complete for row in records) and not controls
    except KeyboardInterrupt:
        run["status"] = "interrupted"
        run["errors"].append("Remaining planned attempts retained as not_started")
    except Exception as error:
        run["status"] = "blocked"
        run["errors"].append(f"{type(error).__name__}: {error}"[:2000])
        if run["processed_attempt_count"] == 0:
            records = [row.model_copy(update={"completion_status": "blocked"}) for row in records]
    finally:
        for key, value in original_thread_environment.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        _write_json(destination / "attempt_records.json", [row.model_dump(mode="json") for row in records], replace=True)
        run["attempt_records_sha256"] = _file_digest(destination / "attempt_records.json")
        save_run()
    return {**run, "execution_manifest_sha256": _file_digest(destination / "execution.json")}
