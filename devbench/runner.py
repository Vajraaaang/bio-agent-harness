"""Primary code-as-action execution with retained coordinator audit evidence."""
import hashlib
import json
from pathlib import Path
import shutil
import time
from tempfile import TemporaryDirectory
from contextlib import contextmanager

from jsonschema import validate
from devbench_engine.runner import EvalRunner, _MemorySampler, _environment, _classify_inputs, evaluate_grader
from devbench_engine.invariants import statistical_failure_veto
from devharness_core.provenance import canonical_json, hash_artifact
from devharness_core.watchdog import HarnessResourceInterrupt
from devharness_server.dispatcher import json_safe
from devbench.staging import stage_python, write_task_guidance
from devbench.graders import grade_design_matrix_gate, grade_fdr_invariant
from devbench.evidence import CodeActionEvidence


def source_fingerprint():
    root = Path(__file__).resolve().parent.parent
    digest = hashlib.sha256()
    for package in ("devharness", "devbench", "devharness_core", "devharness_server", "devbench_engine", "benchmarks", "experiments", "scripts"):
        for path in sorted((root / package).rglob("*.py")):
            digest.update(str(path.relative_to(root)).encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()


def _bounded_copy(source, destination, limit=8_000_000):
    try:
        if not source.exists():
            return None
        if source.is_symlink() or not source.is_file() or source.stat().st_size > limit:
            return {"preserved": False, "reason": "not a bounded regular file"}
        with source.open("rb") as handle:
            content = handle.read(limit + 1)
        if len(content) > limit:
            return {"preserved": False, "reason": "file grew beyond bound"}
        destination.write_bytes(content)
        return {"preserved": True, "sha256": hashlib.sha256(content).hexdigest(), "bytes": len(content), "path": str(destination)}
    except OSError as error:
        return {"preserved": False, "reason": str(error)[:500]}


def _input_integrity(workspace, protected, runtime_hashes):
    """Retain each observed integrity outcome without masking execution errors."""
    result = {"checked": protected is not None or bool(runtime_hashes),
              "protected_inputs": {}, "runtime_sources": {}}
    for group, expected in (("protected_inputs", protected or {}), ("runtime_sources", runtime_hashes)):
        for name, digest in expected.items():
            entry = {"expected_sha256": digest, "observed_sha256": None}
            result[group][name] = entry
            path = workspace / name
            try:
                if path.is_symlink() or not path.resolve().is_relative_to(workspace):
                    entry.update(status="invalid", error="Input is a symlink or escapes the workspace")
                elif not path.exists():
                    entry.update(status="missing", error="Protected input is missing")
                elif group == "runtime_sources" and not path.is_file():
                    entry.update(status="invalid", error="Runtime source is not a regular file")
                else:
                    observed = hash_artifact(path)["sha256"]
                    entry.update(observed_sha256=observed, status="unchanged" if observed == digest else "changed")
            except Exception as error:
                entry.update(status="error", error=f"{type(error).__name__}: {error}"[:1000])
    result["passed"] = (all(entry["status"] == "unchanged" for group in ("protected_inputs", "runtime_sources")
                            for entry in result[group].values()) if result["checked"] else None)
    return result


def _preserve_changed_inputs(workspace, output, integrity):
    """Preserve small changed files under one aggregate bound, never whole stores."""
    remaining = 8_000_000
    integrity["preservation_byte_limit"] = remaining
    for group in ("protected_inputs", "runtime_sources"):
        for name, entry in integrity[group].items():
            if entry["status"] == "unchanged":
                continue
            path = workspace / name
            try:
                if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(workspace):
                    receipt = {"preserved": False, "reason": "Not a workspace regular file; directories are not copied"}
                else:
                    destination = output / "changed_inputs" / name
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    receipt = _bounded_copy(path, destination, limit=remaining)
                    if receipt and receipt.get("preserved"):
                        remaining -= receipt["bytes"]
                        receipt["matches_observed_sha256"] = receipt["sha256"] == entry["observed_sha256"]
                entry["preserved_artifact"] = receipt or {"preserved": False, "reason": "Input disappeared during preservation"}
            except Exception as error:
                entry["preserved_artifact"] = {"preserved": False, "reason": f"{type(error).__name__}: {error}"[:1000]}
    integrity["preserved_bytes"] = integrity["preservation_byte_limit"] - remaining


@contextmanager
def _workspace_directory(workspace_dir=None):
    if workspace_dir is None:
        with TemporaryDirectory(prefix="devbench-codeaction-") as temporary:
            yield Path(temporary).resolve()
    else:
        path = Path(workspace_dir).resolve()
        path.mkdir(parents=True, exist_ok=False)
        yield path


class CodeActionRunner:
    """Stage, execute, check inputs and grade; retain failures without editing them."""
    def __init__(self, task_spec_path, condition="assisted", track=None):
        self.legacy = EvalRunner(str(Path(task_spec_path).resolve()), condition=condition)
        self.path, self.spec = self.legacy.path, self.legacy.spec
        self.condition, self.track = condition, track

    def run(self, adapter, output_dir, workspace_dir=None):
        output = Path(output_dir).resolve()
        output.mkdir(parents=True, mode=0o700, exist_ok=False)
        started = time.monotonic()
        task_budget = adapter.timeout_sec
        # Freeze the normalized configuration actually used for staging and grading.
        # Its private grader configuration belongs only in coordinator evidence.
        spec_snapshot = canonical_json(self.spec)
        spec = json.loads(spec_snapshot)
        condition = self.condition
        metadata = {"condition": condition, "backend": "code-action", "record_format": "devbench.code-action/2",
                    "task_spec_hash": hashlib.sha256(spec_snapshot).hexdigest(), "task_spec_size_bytes": len(spec_snapshot),
                    "task_spec_hash_scope": "canonical_json_normalized_task_snapshot",
                    "track": self.track or spec.get("metadata", {}).get("track", "unknown"),
                    "source_kind": spec.get("metadata", {}).get("source_kind"),
                    "evidence_directory": str(output), "audit_mode": "coordinator_execution_DAG",
                    "clinical_release_allowed": False, "compliance_certified": False,
                    "accepted_final": False, "tool_calls": [], "tool_capture_complete": False}
        metadata["task_budget_sec"] = task_budget
        grade = None
        artifacts = {}
        protected, runtime = None, {}
        phase = "audit_error"
        evidence = None
        with _MemorySampler() as memory:
            try:
                evidence = CodeActionEvidence(output)
                phase = "runtime_identity_error"
                metadata.update(code_hash=source_fingerprint(), environment=_environment())
                phase = "staging_error"
                with _workspace_directory(workspace_dir) as workspace:
                    try:
                        snapshot_path = output / "task_spec.snapshot.json"
                        snapshot_path.write_bytes(spec_snapshot)
                        artifacts[snapshot_path.name] = {"preserved": True, "sha256": metadata["task_spec_hash"],
                            "bytes": len(spec_snapshot), "path": str(snapshot_path), "scope": "coordinator_only"}
                        phase = "audit_error"
                        evidence.begin(metadata)
                        phase = "staging_error"
                        staged = self.legacy._stage(workspace, spec=spec, condition=condition)
                        classification = _classify_inputs(metadata["source_kind"], staged.get("metadata", {}).get("source_kind"), spec.get("files", []))
                        metadata["data_classification"] = classification
                        metadata["source_kind"] = classification["effective"]
                        if metadata["source_kind"] not in {"synthetic", "public", "public_processed"}:
                            raise PermissionError("Only declared synthetic/public inputs may enter external Code-as-Action execution")
                        runtime = stage_python(workspace, assisted=condition == "assisted")
                        metadata["runtime"] = runtime
                        write_task_guidance(workspace, spec["instruction"], runtime["python"], assisted=condition == "assisted", timeout_sec=task_budget)
                        protected = {path.name: hash_artifact(path)["sha256"] for path in workspace.iterdir() if path.name != ".venv"}
                        metadata["input_hashes"] = protected
                        metadata["dataset_hash"] = hashlib.sha256(canonical_json(protected)).hexdigest()
                        phase = "audit_error"
                        evidence.capture_inputs(workspace, protected, runtime["runtime_source_hashes"], metadata["dataset_hash"])
                        phase = "staging_error"
                        schema = json.loads((workspace / "answer_schema.json").read_text())
                        metadata["staging_elapsed_sec"] = time.monotonic() - started
                        phase = "agent_error"
                        adapter.condition = condition
                        remaining = task_budget - (time.monotonic() - started)
                        if remaining <= 0:
                            raise HarnessResourceInterrupt("Task budget exhausted during staging")
                        adapter.timeout_sec = remaining
                        answer = adapter.execute(spec["instruction"], workspace)
                        phase = "input_integrity_error"
                        metadata["input_integrity"] = _input_integrity(workspace, protected, runtime["runtime_source_hashes"])
                        if not metadata["input_integrity"]["passed"]:
                            raise ValueError("Protected task input or runtime source failed integrity verification")
                        phase = "answer_error"
                        validate(answer, schema)
                        phase = "grader_error"
                        primary = evaluate_grader(answer, spec["grader"], {"tool_events": [], "work_dir": str(workspace),
                            "ground_truth": staged.get("ground_truth", {}), "condition": condition})
                        veto = statistical_failure_veto(answer, [])
                        gates = [grade_design_matrix_gate(answer), grade_fdr_invariant(answer)]
                        passed = primary["passed"] and not veto["passed"] and all(g["passed"] for g in gates)
                        grade = {"passed": bool(passed), "score": primary["score"] if passed else 0.0,
                                 "score_max": primary.get("score_max", 1), "children": [primary, *gates, veto],
                                 "reasoning": "Task and invariant gates passed" if passed else "Task or invariant gate failed"}
                        metadata.update(answer=answer, primary_grader_result=primary, statistical_invariant_veto=veto,
                                        failure_type=None if passed else "grader_failure", accepted_final=True)
                        if time.monotonic() - started > task_budget:
                            grade = None
                            raise HarnessResourceInterrupt("Task completed after its whole-task deadline")
                    except Exception as error:
                        metadata.update(failure_type="resource_interrupt" if isinstance(error, HarnessResourceInterrupt) else phase,
                                        error=f"{type(error).__name__}: {error}"[:3000], accepted_final=False)
                    finally:
                        phase = "audit_error"
                        adapter.timeout_sec = task_budget
                        if "input_integrity" not in metadata:
                            metadata["input_integrity"] = _input_integrity(workspace, protected, runtime.get("runtime_source_hashes", {}))
                        _preserve_changed_inputs(workspace, output, metadata["input_integrity"])
                        observation = json_safe(adapter.metadata)
                        metadata["adapter_observations"] = observation
                        for key in ("actual_model", "model_ids", "requested_model", "billing_mode", "session_id", "usage", "usage_complete", "tokens", "cost_usd",
                                    "turns", "script_execution_observed", "mcp_attempts", "mcp_cancellations", "cli_command",
                                    "inference_dispatch_attempted", "inference_session_observed"):
                            if key in observation:
                                metadata[key] = observation[key]
                        # Participant SDK traces are retained, never promoted to trusted
                        # tool events or used to contradict a host-owned grade.
                        metadata["tool_calls"] = []
                        metadata["tool_capture_complete"] = False
                        for name in ("solve.py", "final_discovery.json", ".devharness_trace.jsonl", "agent_prompt.txt", "agent_final_message.txt",
                                     "TASK_README.md", "sdk_cheatsheet.py", "answer_schema.json"):
                            artifact = _bounded_copy(workspace / name, output / name)
                            if artifact:
                                artifacts[name] = artifact
                        (output / "cli-observations.json").write_text(json.dumps(observation, indent=2, allow_nan=False))
            except Exception as error:
                detail = f"{type(error).__name__}: {error}"[:3000]
                metadata.setdefault("execution_failure_type", metadata.get("failure_type", phase))
                metadata.setdefault("error", detail)
                if phase == "audit_error":
                    metadata["audit_error"] = detail
                    metadata.setdefault("persistence_errors", []).append(detail)
                metadata.update(failure_type=phase, accepted_final=False)
                grade = None
        metadata.update(elapsed_sec=time.monotonic() - started,
                        elapsed_scope="staging, execution and flat preservation; excludes DAG finalization and run.json publication",
                        peak_memory_mb=memory.peak_mb if not memory.error else None,
                        memory_samples=memory.samples, artifacts=artifacts,
                        memory_sampling={"scope": "runner_and_recursive_children_before_DAG_finalization", "unit": "bytes", "interval_sec": .1,
                                         "complete": memory.error is None, "retained_stride": memory.stride,
                                         "successful_sample_count": memory.sample_count})
        if memory.error:
            metadata["memory_measurement_error"] = memory.error
        result = {"task_id": spec["task_id"], "model": adapter.model or "codex-cli:default", "backend": "code-action",
                  "condition": condition, "passed": bool(grade and grade["passed"]), "score": grade["score"] if grade else 0.0,
                  "grader_result": grade, "metadata": json_safe(metadata)}
        audit_started = time.monotonic()
        try:
            if metadata.get("failure_type") == "audit_error":
                result["metadata"].setdefault("audit_error", metadata.get("error", "Audit capture failed"))
            if evidence is None:
                raise RuntimeError(metadata.get("error", "Audit initialization failed"))
            result["metadata"]["audit"] = evidence.finish(result, artifacts)
        except Exception as error:
            result["metadata"].setdefault("persistence_errors", []).append(f"{type(error).__name__}: {error}"[:3000])
            result["metadata"].setdefault("execution_failure_type", metadata.get("failure_type"))
            result["metadata"].update(audit_error=f"{type(error).__name__}: {error}"[:3000],
                failure_type="audit_error", accepted_final=False)
            result.update(passed=False, score=0.0, grader_result=None)
        result["metadata"]["audit_finalization_elapsed_sec"] = time.monotonic() - audit_started
        (output / "run.json").write_text(json.dumps(result, indent=2, allow_nan=False))
        return result
