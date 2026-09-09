"""Temporary-workspace evaluations with deterministic answer and evidence grading."""
import json
import hashlib
import importlib.metadata
import os
import platform
import re
import shutil
import threading
import time
import uuid
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Callable, Dict, List, Optional, TypedDict

import psutil
from jsonschema import Draft202012Validator, validate

from devbench_engine.composite import AllOfGrader, AverageOfGrader
from devbench_engine.graders_bio import grade_label_set_jaccard, grade_longest_subsequence
from devbench_engine.graders_math import grade_numeric_tolerance, grade_temporal_monotonicity
from devharness_core.watchdog import HarnessResourceInterrupt, configure_worker_environment, _recursive_children


class EvalResult(TypedDict):
    task_id: str
    passed: bool
    score: float
    grader_result: Optional[dict]
    metadata: dict


def _result(passed: bool, score: float, reasoning: str, maximum: float = 1) -> dict:
    return {"passed": passed, "score": score * maximum, "score_max": maximum, "reasoning": reasoning}


def _lookup(answer: dict, field):
    """Resolve legacy dotted paths or explicit keys containing literal dots."""
    if isinstance(field, str):
        keys = field.split(".")
    elif isinstance(field, list) and field and all(isinstance(key, str) and key for key in field):
        keys = field
    else:
        raise ValueError("Grader field path must be a dotted string or nonempty list of string keys")
    value = answer
    for key in keys:
        value = value[key]
    return value


def requires_domain_tool_history(grader: dict) -> bool:
    if grader.get("type") == "tool_sequence":
        return True
    return any(requires_domain_tool_history(child) for child in grader.get("config", grader).get("children", []))


def _tool_evidence(events: List[dict], work_dir: Optional[str], answer: dict) -> bool:
    """Require real, ordered calls on this task's staged data, including fit success."""
    if work_dir is None:
        return False
    work = Path(work_dir).resolve()
    deficient = False
    for event in events:
        args, result = event.get("arguments", {}), event.get("result", {})
        if not event.get("succeeded"):
            continue
        if event["tool_name"] == "validate_design_matrix":
            formula = re.sub(r"\s+", "", args.get("formula_str", ""))
            if (Path(args.get("metadata_csv_path", "")).resolve() == work / "metadata.csv"
                    and formula in ("1+numeric_time+C(batch)", "numeric_time+C(batch)")):
                deficient |= result.get("is_full_rank") is False
        if deficient and event["tool_name"] == "fit_temporal_trajectory":
            if (Path(args.get("metadata_path", "")).resolve() == work / "metadata.csv"
                    and Path(args.get("counts_path", "")).resolve() == work / "counts.zarr"
                    and "batch" not in result.get("formula", "").lower()
                    and result.get("design", {}).get("is_full_rank") is True
                    and {"Sox2", "Tbr1"} <= set(result.get("genes", {}))
                    and args.get("time_col") == "embryonic_age"
                    and answer.get("corrected_formula") in (args.get("formula_str"), result.get("formula"))):
                return True
    return False


def evaluate_grader(answer: dict, spec: dict, context: dict) -> dict:
    kind = spec["type"]
    config = dict(spec.get("config", spec))
    if "expected_from_truth" in config:
        try:
            config["expected"] = _lookup(context.get("ground_truth", {}), config["expected_from_truth"])
        except (KeyError, TypeError):
            return _result(False, 0, "Required reference truth is unavailable")
    if kind in ("all_of", "average_of"):
        children = []
        for child in config["children"]:
            child_result = evaluate_grader(answer, child, context)
            child_result["role"] = child.get("role", "gate")
            children.append(child_result)
        grader = AllOfGrader() if kind == "all_of" else AverageOfGrader(
            config.get("pass_rule", "all"), config.get("min_passing_children", 1), config.get("score_threshold", .75))
        result = grader.evaluate(children)
        maximum = float(spec.get("score_max", 1))
        result.update(score=result["score"] * maximum, score_max=maximum)
        return result
    maximum = float(spec.get("score_max", 1))
    if kind == "tool_sequence":
        passed = _tool_evidence(context.get("tool_events", []), context.get("work_dir"), answer)
        return _result(passed, float(passed), "Captured rank check and corrected full-rank fit" if passed else "Required tool evidence missing", maximum)
    try:
        actual = _lookup(answer, config["field"])
    except (KeyError, TypeError):
        return _result(False, 0, f"Missing answer field: {config.get('field')}", maximum)
    if kind == "numeric_tolerance":
        grade = grade_numeric_tolerance(actual, config["expected"], config["tolerances"])
    elif kind in ("label_set_jaccard", "jaccard_label_set"):
        grade = grade_label_set_jaccard(actual, config["expected"], config.get("threshold", .6), config.get("expected_count"))
    elif kind == "longest_subsequence":
        grade = grade_longest_subsequence(actual, config["expected"], config.get("pass_threshold", .75))
    elif kind == "temporal_monotonicity":
        grade = grade_temporal_monotonicity(actual, config.get("expected", 1))
    elif kind == "pseudotime":
        from devbench_engine.graders_bio import grade_pseudotime
        branches = _lookup(answer, config["branch_field"]) if config.get("branch_field") else None
        expected_branches = _lookup(context["ground_truth"], config["branches_from_truth"]) if config.get("branches_from_truth") else None
        grade = grade_pseudotime(actual, config["expected"], config.get("threshold", .8), branches,
                                expected_branches, config.get("min_coverage", 1.0))
    elif kind == "lineage_graph":
        from devbench_engine.graders_bio import grade_lineage_graph
        grade = grade_lineage_graph(actual, config["expected"], threshold=config.get("threshold", .8),
            method=config.get("method", "label_edit"), timeout_sec=config.get("timeout_sec", 5),
            max_nodes=config.get("max_nodes", 8), require_tree=config.get("require_tree", False))
    elif kind == "network_precision_recall":
        from devbench_engine.graders_bio import grade_network_precision_recall
        universe = (_lookup(context["ground_truth"], config["universe_from_truth"])
                    if config.get("universe_from_truth") else config["candidate_universe"])
        grade = grade_network_precision_recall(actual, config["expected"], universe,
                                               threshold=config.get("threshold", .5), signed=config.get("signed", False))
    elif kind == "effect_calibration":
        from devbench_engine.graders_bio import grade_effect_calibration
        grade = grade_effect_calibration(actual, config["expected"],
            absolute_tolerance=config.get("absolute_tolerance", .25),
            relative_tolerance=config.get("relative_tolerance", .1), threshold=config.get("threshold", .8))
    elif kind == "equals":
        passed = type(actual) is type(config["expected"]) and actual == config["expected"]
        grade = passed, float(passed), "Exact value agreement" if passed else "Value does not match"
    else:
        raise ValueError(f"Unknown grader type: {kind}")
    return _result(*grade, maximum)


class _MemorySampler:
    """Sample process-tree RSS, keeping a bounded, progressively thinned trace.

    RSS sums can count shared pages more than once and miss peaks between polls.
    These observations are not an OS-enforced allocation limit.
    """
    MAX_SAMPLES = 1200

    def __init__(self) -> None:
        self.peak_mb = 0.0
        self.error: Optional[str] = None
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.samples: List[dict] = []
        self.sample_count = 0
        self.stride = 1
        self.started = time.monotonic()

    def _sample(self) -> None:
        try:
            parent = psutil.Process(os.getpid())
            processes = [parent] + list(_recursive_children(parent))
        except (psutil.Error, OSError) as error:
            self.error = str(error)
            return
        total = 0
        complete = True
        for process in processes:
            try:
                total += process.memory_info().rss
            except psutil.NoSuchProcess:
                continue
            except (psutil.Error, OSError) as error:
                self.error = str(error)
                complete = False
                continue
        if not complete:
            return
        self.peak_mb = max(self.peak_mb, total / 1_000_000)
        self.sample_count += 1
        if (self.sample_count - 1) % self.stride == 0:
            self.samples.append({"elapsed_sec": time.monotonic() - self.started, "rss_bytes": total})
        if len(self.samples) > self.MAX_SAMPLES:
            self.samples = self.samples[::2]
            self.stride *= 2

    def _run(self) -> None:
        while not self.stop.wait(.1):
            self._sample()

    def __enter__(self):
        self._sample()
        self.thread.start()
        return self

    def __exit__(self, *_):
        self.stop.set()
        self.thread.join()
        self._sample()


def _code_fingerprint() -> str:
    root = Path(__file__).resolve().parent.parent
    digest = hashlib.sha256()
    for package in ("devharness_core", "devharness_server", "devbench_engine", "benchmarks", "experiments"):
        for path in sorted((root / package).rglob("*.py")):
            digest.update(str(path.relative_to(root)).encode())
            digest.update(path.read_bytes())
    guidance = root / "devbench_engine" / "AGENTS.md"
    digest.update(b"devbench_engine/AGENTS.md")
    digest.update(guidance.read_bytes())
    return digest.hexdigest()


def _distribution_versions() -> dict:
    """Record visible name/version metadata, not installed-file attestation."""
    versions = {}
    for index, distribution in enumerate(importlib.metadata.distributions()):
        if index >= 4096:
            raise ValueError("Distribution metadata exceeds the 4096-entry bound")
        name, version = distribution.metadata.get("Name"), distribution.version
        if (not isinstance(name, str) or len(name) > 200
                or not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?", name)
                or not isinstance(version, str) or not version or len(version) > 200
                or version.strip() != version or any(ord(char) < 32 for char in version)):
            raise ValueError("Distribution metadata must contain valid nonempty name/version strings")
        name = re.sub(r"[-_.]+", "-", name).lower()
        if name in versions and versions[name] != version:
            raise ValueError("Distribution metadata has conflicting versions for " + name)
        versions[name] = version
    if not versions:
        raise ValueError("Distribution metadata is unavailable")
    return dict(sorted(versions.items()))


def _environment() -> dict:
    thread_settings = configure_worker_environment()
    distributions = _distribution_versions()
    # Retain the original subset for downstream readers. Replay compares the
    # complete descriptor, including the distribution version census below.
    versions = {name: distributions.get(name) for name in (
        "numpy", "scipy", "pandas", "statsmodels", "pydeseq2", "scikit-learn", "zarr", "networkx")}
    return {"python": platform.python_version(), "platform": platform.system(), "machine": platform.machine(),
            "packages": versions, "thread_settings": thread_settings,
            "environment_schema_version": "devbench.python-environment/2",
            "distribution_versions": distributions,
            "distribution_version_scope": "metadata_visible_on_python_search_path",
            "native_library_identity_recorded": False}


def _classify_inputs(declared, staged, assets: List[dict]) -> dict:
    """Never downgrade a restriction or infer the classification of an overlay."""
    allowed = {"synthetic", "public", "public_processed"}
    labels = [value for value in (declared, staged) if value not in (None, "unspecified")]
    labels.extend(asset.get("source_kind", "unspecified") for asset in assets)
    restricted = next((label for label in labels if label in {"clinical", "private", "restricted"}), None)
    effective = restricted or ("unspecified" if not labels or any(label not in allowed for label in labels)
                               else "public_processed" if any(label.startswith("public") for label in labels) else "synthetic")
    return {"declared": declared, "builder": staged, "overlays": [asset.get("source_kind", "unspecified") for asset in assets],
            "effective": effective, "basis": "most_restrictive_declared_or_builder_classification", "phi_detection_performed": False}


class EvalRunner:
    """Audited execution of trusted callbacks and resource-limited CLI agents.

    The local execution graph provides tamper evidence. Qualified deployment,
    authenticated identities, retention and signature controls are separate.
    """
    def __init__(self, task_spec_path: str, audit_dir: Optional[Path] = None,
                 actor_id: str = "local-unverified", condition: str = "assisted",
                 validation_mode: str = "research", control_evidence: Optional[dict] = None) -> None:
        self.path = Path(task_spec_path).expanduser().resolve()
        self.spec = json.loads(self.path.read_text())
        if "id" in self.spec:
            self.spec.setdefault("task_id", self.spec["id"])
        if "task" in self.spec:
            self.spec.setdefault("instruction", self.spec["task"])
        for key in ("task_id", "instruction", "grader"):
            if key not in self.spec:
                raise ValueError(f"Task specification missing {key}")
        if "answer_schema" in self.spec:
            Draft202012Validator.check_schema(self.spec["answer_schema"])
        if condition not in ("assisted", "baseline") or validation_mode not in ("research", "clinical"):
            raise ValueError("Unknown execution condition or validation mode")
        self.audit_dir = Path(audit_dir).expanduser().resolve() if audit_dir is not None else Path.cwd().resolve() / "results" / "audit"
        self.actor_id, self.condition, self.validation_mode = actor_id, condition, validation_mode
        self.control_evidence = control_evidence or {}

    def _stage(self, work: Path, *, spec: Optional[dict] = None, condition: Optional[str] = None) -> dict:
        spec = self.spec if spec is None else spec
        condition = self.condition if condition is None else condition
        guidance = Path(__file__).resolve().parent / "AGENTS.md"
        if condition == "assisted":
            text = guidance.read_text()
        else:
            text = ("# Benchmark baseline\nAnalyze the supplied data using standard Python and shell tools. "
                    "Domain-specific devharness tools and packages are unavailable in this condition; do not import them. "
                    "Treat data, metadata and answer_schema.json as read-only. Work only in this workspace. "
                    "Write finite JSON to final_discovery.json matching answer_schema.json. "
                    "Raise explicit diagnostic warnings for invalid or unidentifiable analyses.\n")
        (work / "AGENTS.md").write_text(text)
        (work / "CLAUDE.md").write_text(text)
        (work / "answer_schema.json").write_text(json.dumps(spec.get("answer_schema", {"type": "object"})))
        staged = {"metadata": {}, "ground_truth": {}}
        dataset = spec.get("dataset", {})
        if dataset.get("builder") == "matrix":
            from benchmarks.matrix import stage_matrix_task
            staged = stage_matrix_task(work, dataset)
        elif dataset.get("builder") == "public_subset":
            from benchmarks.datasets import stage_public_task
            staged = stage_public_task(work, dataset)
        elif dataset.get("builder") == "observed_temporal":
            from benchmarks.temporal import stage_temporal_task
            staged = stage_temporal_task(work, dataset)
        elif dataset.get("builder") is not None:
            raise ValueError(f"Unsupported dataset builder: {dataset['builder']}")
        elif dataset:
            from benchmarks.generate_synthetic import stage_benchmark
            stage_benchmark(work, dataset.get("variant", "standard"), dataset.get("seed", 42))
            staged["metadata"] = {"source_kind": "synthetic", "track": "legacy"}
        for asset in spec.get("files", []):
            source = (self.path.parent / asset["source"]).resolve()
            destination = (work / asset["destination"]).resolve()
            if not destination.is_relative_to(work) or destination == work:
                raise ValueError("Asset destination escapes workspace")
            destination.parent.mkdir(parents=True, exist_ok=True)
            if source.is_dir():
                shutil.copytree(source, destination)
            else:
                shutil.copy2(source, destination)
        return staged

    @staticmethod
    def _parse_answer(raw, work: Path) -> tuple:
        if raw is None:
            artifact = work / "final_discovery.json"
            if not artifact.exists():
                artifact = work / "eval_answer.json"
            if artifact.is_symlink() or artifact.resolve().parent != work:
                raise ValueError("Answer artifact must reside in the task workspace")
            if artifact.stat().st_size > 1_000_000:
                raise ValueError("Answer artifact exceeds 1 MB")
            raw = json.loads(artifact.read_text())
        elif isinstance(raw, str):
            if len(raw.encode()) > 1_000_000:
                raise ValueError("Answer exceeds 1 MB")
            raw = json.loads(raw)
        if not isinstance(raw, dict):
            raise ValueError("Agent must return an answer object or write final_discovery.json")
        if len(json.dumps(raw, allow_nan=False).encode()) > 1_000_000:
            raise ValueError("Answer and metadata exceed 1 MB")
        answer, reported = raw.get("answer", raw), raw.get("metadata", {}) if "answer" in raw else {}
        if not isinstance(answer, dict) or not isinstance(reported, dict):
            raise ValueError("Answer and reported metadata must be objects")
        return answer, reported

    def run(self, adapter) -> EvalResult:
        from devharness_core.provenance import ExecutionDAG, analysis_fingerprint, canonical_json, hash_artifact, evaluate_controls
        from devharness_server.dispatcher import capture_tool_events, json_safe
        from devbench_engine.adapters import AgentAdapter, CodexCLIAdapter, ClaudeCLIAdapter, APIAdapter
        start = time.monotonic()
        # Freeze the configuration used by this execution, including its private
        # grader. A later source-file or caller mutation cannot change this run.
        spec_snapshot = canonical_json(self.spec)
        spec = json.loads(spec_snapshot)
        condition, validation_mode = self.condition, self.validation_mode
        control_evidence = json.loads(canonical_json(self.control_evidence))
        metadata: Dict = {"condition": condition, "validation_mode": validation_mode,
                          "comparison_eligible": not requires_domain_tool_history(spec["grader"]),
                          "release_status": "held" if validation_mode == "clinical" else "research_only",
                          "task_spec_hash": hashlib.sha256(spec_snapshot).hexdigest(),
                          "task_spec_hash_scope": "canonical_json_normalized_task_snapshot",
                          "code_hash": _code_fingerprint(), "environment": _environment(),
                          "track": spec.get("metadata", {}).get("track", "legacy"),
                          "source_kind": spec.get("metadata", {}).get("source_kind", "unspecified")}
        grade, answer, events, parents = None, None, [], []
        phase = "audit_error"
        try:
            dag = ExecutionDAG(self.audit_dir / uuid.uuid4().hex, actor_id=self.actor_id)
            # This directory is coordinator-only and is removed before the
            # participant workspace exists. Never stage grader snapshots there.
            with TemporaryDirectory(prefix="devbench-private-spec-") as private_directory:
                snapshot_path = Path(private_directory).resolve() / "task_spec.snapshot.json"
                snapshot_path.write_bytes(spec_snapshot)
                task_reference = dag.add_artifact(snapshot_path)
            parents = [dag.append("run_started", {"task_id": spec["task_id"], "condition": condition,
                "validation_mode": validation_mode,
                "code_hash": metadata["code_hash"], "environment": metadata["environment"],
                "task_spec": task_reference, "task_spec_hash": metadata["task_spec_hash"],
                "task_spec_hash_scope": metadata["task_spec_hash_scope"],
                "task_spec_source_path": str(self.path), "actor_identity_verified": False})]
        except Exception as error:
            metadata.update(failure_type="audit_error", error=f"Audit initialization failed: {error}")
            return {"task_id": str(spec["task_id"]), "passed": False, "score": 0.0,
                    "grader_result": None, "metadata": metadata}
        with _MemorySampler() as memory, TemporaryDirectory(prefix="devbench-") as directory:
            work = Path(directory).resolve()
            try:
                phase = "staging_error"
                staged = self._stage(work, spec=spec, condition=condition)
                metadata["data_classification"] = _classify_inputs(metadata["source_kind"],
                    staged.get("metadata", {}).get("source_kind"), spec.get("files", []))
                metadata["source_kind"] = metadata["data_classification"]["effective"]
                metadata.update({key: staged.get("metadata", {})[key] for key in ("track",)
                                 if key in staged.get("metadata", {})})
                protected = {path.name: dag.add_artifact(path) for path in sorted(work.iterdir())}
                data_digests = {name: ref["sha256"] for name, ref in protected.items()
                                if name not in ("AGENTS.md", "CLAUDE.md", "answer_schema.json")}
                metadata["dataset_hash"] = hashlib.sha256(canonical_json(data_digests)).hexdigest()
                fingerprint = analysis_fingerprint(inputs=data_digests, code=metadata["code_hash"],
                    environment=metadata["environment"], parameters=spec.get("dataset", {}),
                    seeds={"dataset": spec.get("dataset", {}).get("seed")}, method="devbench-evaluation-v2")
                metadata["analysis_fingerprint"] = fingerprint
                parents = [dag.append("inputs_staged", {"artifacts": protected, "workspace": str(work), "dataset_hash": metadata["dataset_hash"],
                    "truth_digest": staged.get("truth_digest"), "source": staged.get("metadata", {}),
                    "data_classification": metadata["data_classification"]}, parents=parents)]
                phase = "data_policy_error"
                if isinstance(adapter, (CodexCLIAdapter, ClaudeCLIAdapter, APIAdapter)):
                    if validation_mode == "clinical" or metadata["source_kind"] not in {"synthetic", "public_processed", "public"}:
                        raise PermissionError("External model execution requires declared synthetic/public data in research mode; clinical and unclassified data are blocked")
                from experiments.matrix_oracle import MatrixOracleAdapter
                if isinstance(adapter, MatrixOracleAdapter):
                    adapter.set_truth(staged.get("ground_truth", {}))
                    metadata["oracle"] = True
                if hasattr(adapter, "condition"):
                    adapter.condition = condition
                phase = "agent_error"
                with capture_tool_events() as events:
                    raw = (adapter.execute(spec["instruction"], work) if hasattr(adapter, "execute")
                           else adapter(spec["instruction"], str(work)))
                events = list(events)
                if isinstance(adapter, AgentAdapter):
                    events.extend(adapter.tool_events)
                phase = "input_integrity_error"
                for name, ref in protected.items():
                    if hash_artifact(work / name)["sha256"] != ref["sha256"]:
                        raise ValueError(f"Protected task input changed: {name}")
                phase = "answer_error"
                answer, reported = self._parse_answer(raw, work)
                metadata["agent_reported"] = reported
                # Legacy usage fields are informational only; provenance,
                # identity and execution state are always runner-owned.
                for key in ("tokens", "cost_usd"):
                    if key in reported:
                        metadata[key] = reported[key]
                validate(answer, spec.get("answer_schema", {"type": "object"}))
                output = work / "final_discovery.json"
                if output.is_symlink():
                    raise ValueError("Final answer must not be a symlink")
                output.write_text(json.dumps(answer, allow_nan=False, ensure_ascii=False))
                phase = "grader_error"
                grade = evaluate_grader(answer, spec["grader"], {"tool_events": events, "work_dir": str(work),
                    "ground_truth": staged.get("ground_truth", {}), "condition": condition})
                from devbench_engine.invariants import statistical_failure_veto
                veto = statistical_failure_veto(answer, events)
                metadata["statistical_invariant_veto"] = veto
                if veto["passed"]:
                    metadata["primary_grader_result"] = grade
                    grade = AllOfGrader().evaluate([grade, veto])
                metadata["failure_type"] = None if grade["passed"] else "grader_failure"
                metadata["answer"] = answer
                if validation_mode == "clinical":
                    readiness = evaluate_controls(control_evidence)
                    metadata["control_readiness"] = readiness
                    metadata["release_status"] = "held"
                    metadata["scientific_grader_result"] = grade
                    metadata["failure_type"] = "regulatory_release_hold"
                    grade = _result(False, 0, "Clinical release requires qualified validation, authenticated approval and signature controls")
                else:
                    metadata["release_status"] = "research_only"
            except Exception as error:
                metadata.update(failure_type="resource_interrupt" if isinstance(error, HarnessResourceInterrupt) else phase,
                                error=f"{type(error).__name__}: {error}"[:2000])
                grade = None
            finally:
                if isinstance(adapter, AgentAdapter):
                    try:
                        json.dumps(adapter.metadata, allow_nan=False)
                        metadata["adapter_observations"] = json.loads(json.dumps(adapter.metadata))
                        observed_fields = {"actual_model", "model_ids", "model_usage", "turns", "tokens", "cost_usd",
                            "billing_mode", "usage", "usage_complete", "session_id", "stdout_tail", "stderr_tail",
                            "duration_sec", "returncode", "peak_rss_mb", "preflight_failure", "login_status",
                            "continuity", "tool_errors", "self_corrections", "auth_method", "subscription_type",
                            "reported_usage_cost_usd"}
                        for key in observed_fields & adapter.metadata.keys():
                            metadata[key] = adapter.metadata[key]
                        events = list(adapter.tool_events)
                    except (TypeError, ValueError):
                        metadata["adapter_metadata_error"] = "Invalid adapter diagnostics"
                metadata["tool_calls"] = events
                metadata["tool_capture_complete"] = condition == "assisted"
                metadata["elapsed_sec"] = time.monotonic() - start
                try:
                    for event in events:
                        parents = [dag.append("tool_completed" if event.get("succeeded") else "tool_failed", json_safe(event), parents=parents)]
                    for turn in metadata.get("turns", []):
                        parents = [dag.append("agent_turn", json_safe(turn), parents=parents)]
                    artifacts = {}
                    for name in ("final_discovery.json", "eval_answer.json"):
                        path = work / name
                        if path.exists() and not path.is_symlink() and path.is_file() and path.stat().st_size <= 1_000_000:
                            artifacts[name] = dag.add_artifact(path)
                    status = "success" if grade and grade["passed"] else "failed"
                    parents = [dag.append("run_finished", {"status": status, "failure_type": metadata.get("failure_type"),
                        "error": metadata.get("error"), "grader_result": grade, "artifacts": artifacts}, parents=parents)]
                    metadata["audit"] = dag.finalize(analysis_fingerprint=metadata.get("analysis_fingerprint"))
                except Exception as error:
                    metadata.update(failure_type="audit_error", error=f"Audit persistence failed: {error}"[:2000])
                    grade = None
        metadata["peak_memory_mb"] = memory.peak_mb if not memory.error else None
        metadata["memory_samples"] = memory.samples
        metadata["memory_sampling"] = {"scope": "runner_and_recursive_children", "unit": "bytes",
            "interval_sec": 0.1, "successful_sample_count": memory.sample_count,
            "retained_stride": memory.stride, "complete": memory.error is None,
            "peak_memory_mb_unit": "decimal_MB", "limit_enforced_by_sampler": False}
        if memory.error:
            metadata["memory_measurement_error"] = memory.error
        return {"task_id": str(spec["task_id"]), "passed": bool(grade and grade["passed"]),
                "score": grade["score"] if grade else 0.0, "grader_result": grade, "metadata": json_safe(metadata)}
