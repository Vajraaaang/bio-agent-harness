"""Trusted analysis episodes over existing scientific, resource and audit controls.

This is a coordinator API, not an OS sandbox or a training environment. A live
participant still needs the existing permission boundary and preflight. Hidden
evaluation references and scores are deliberately absent from the step API.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import hashlib
import math
from pathlib import Path
import threading
import time

from .checkpoints import CheckpointStore, atomic_json, digest, sha256


@dataclass(frozen=True)
class EpisodeLimits:
    max_steps: int = 32
    max_revisions: int = 2
    timeout_seconds: float = 600
    stage_timeout_seconds: float = 180
    memory_mb: int = 2048
    max_input_bytes: int = 100_000_000

    def __post_init__(self):
        for name, low, high in (("max_steps", 1, 100), ("max_revisions", 0, 2),
                                ("memory_mb", 128, 4096), ("max_input_bytes", 1, 100_000_000)):
            value = getattr(self, name)
            if type(value) is not int or not low <= value <= high:
                raise ValueError(f"{name} exceeds the episode policy")
        for name, maximum in (("timeout_seconds", 3600), ("stage_timeout_seconds", 300)):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 < value <= maximum:
                raise ValueError(f"{name} exceeds the episode policy")


def _json_copy(value):
    from devharness_core.provenance import canonical_json
    return json.loads(canonical_json(value))


def _public_only(value):
    """Reject structured evaluator fields before recording participant values."""
    from devbench.study.biology_input_limits import validate_public_structure, EPISODE_EVALUATOR_FIELDS
    scientific_planning = isinstance(value, dict) and value.get("decision_kind") in {
        "method_scientific_planning", "method_capability_resolution", "method_recommendation",
        "method_trial_design", "method_implementation_draft"}
    validate_public_structure(value, allow_algorithm_seed=scientific_planning)
    denied = EPISODE_EVALUATOR_FIELDS
    def check(item):
        if isinstance(item, dict):
            if denied & item.keys():
                raise ValueError("Evaluator fields are not episode inputs")
            for child in item.values():
                check(child)
        elif isinstance(item, (list, tuple)):
            for child in item:
                check(child)
    check(value)


def _sources():
    """Fingerprint used implementation paths, never reference answers/graders."""
    project = Path(__file__).resolve().parents[2]
    paths = {Path(__file__), *(project / "devharness_core").rglob("*.py"),
             *(project / "devharness_server").rglob("*.py"),
             *(project / "devharness_server").rglob("*.R")}
    paths.update(project / "devbench" / "analysis" / name for name in
                 ("checkpoints.py", "resources.py", "runtime.py", "episode_agent.py", "model_authority.py", "episode_codex.py", "episode_cli.py", "episode_handoff.py", "episode_synthesis.py", "episode_proposals.py", "episode_planning.py"))
    paths.update(project / name for name in ("devbench_engine/adapters.py", "devbench/staging.py",
        "devbench/adapters/code_action.py", "devbench/adapters/execution_capture.py", "devbench/study/execution.py",
        "devbench/study/adapter.py", "devbench/study/exec_preflight.py",
        "devbench/study/cli_identity.py", "devbench/study/model_telemetry.py"))
    paths.update(project / "devbench" / "study" / name for name in (
        "biology_broker.py", "broker.py", "biology_tools_bulk.py", "biology_tools_singlecell.py",
        "biology_tool_io.py", "biology_input_limits.py", "zarr_bounds.py", "biology_answers.py", "contracts.py",
        "permissions.py", "evidence.py", "evidence_keys.py", "execution_ledger.py"))
    return {str(path.relative_to(project)): sha256(path) for path in sorted(paths)}


def _support(assay, kind, task):
    measurement = task["measurement"]
    targets = {x["target_id"] for x in task["questions"]}
    if assay == "bulk" and targets == {"mean_contrast"}:
        return (kind == "count_matrix" and measurement == "raw_counts") or (
            kind == "normalized_expression" and measurement == "continuous")
    return (assay == "scrna" and kind == "count_matrix"
            and measurement in {"umi_counts", "non_umi_counts"} and targets == {"cell_state_effect"})


def normalize_episode_configuration(*, question, assay, public_task, limits=None,
                                    fixed_options=None, input_kind=None, execution_tier="local_supervised"):
    """Normalize an authored public specification without reading inputs or running tools.

    This preserves unsupported routes for explicit refusal; it does not establish
    observed design support or authorize data/model execution.
    """
    from devbench.study.biology_tool_io import validate_public_task
    from devbench.study.biology_broker import COMMON_CHECKS, biology_tool_menu, validate_biology_request
    from devbench.study.biology_input_limits import PUBLIC_MAX_BULK_FEATURES, PUBLIC_MAX_FEATURES

    if not isinstance(question, str) or not question.strip() or len(question) > 8000:
        raise ValueError("A bounded biological question is required")
    if assay not in {"bulk", "scrna", "snrna"} or execution_tier not in {"local_supervised", "colab"}:
        raise ValueError("Declare a supported assay vocabulary and execution tier")
    task = _json_copy(public_task)
    _public_only(task)
    projected = validate_public_task(task)
    if (any(isinstance(task.get(key), dict) for key in ("measurement_layer", "measurement", "measurement_type"))
            or isinstance(projected["analysis_context"].get("measurement"), dict)):
        # Preserve the selected assay declaration before canonicalizing its name.
        task["measurement_layer"] = _json_copy(projected["measurement_layer"])
    task["measurement"] = projected["measurement"]
    task["questions"] = projected["targets"]
    task["unit_hierarchy"] = projected["unit_hierarchy"]
    task["population_unit_label"] = projected["unit_hierarchy"]["population_unit_label"]
    task["context"] = projected["analysis_context"]
    # Public validation resolves consistent top/context declarations first.
    # Defaults must not manufacture a conflicting coordinate. Retain existing
    # top-level records (including extra provenance) without adding new keys.
    if "analysis_columns" not in task:
        task["analysis_columns"] = {"condition": "condition", "time": "numeric_time" if assay == "bulk" else None, "dose": None,
            "covariate": None, "population_unit": task["population_unit_label"], "batches": [],
            **projected.get("analysis_columns", {})}
    else:
        task["analysis_columns"].update(projected.get("analysis_columns", {}))
    genes = task.get("gene_ids")
    maximum_features = PUBLIC_MAX_BULK_FEATURES if assay == "bulk" else PUBLIC_MAX_FEATURES
    if not isinstance(genes, list) or not genes or len(genes) > maximum_features or len(set(genes)) != len(genes):
        raise ValueError("A declared bounded feature universe is required")
    limits = limits or EpisodeLimits()
    if not isinstance(limits, EpisodeLimits):
        raise TypeError("Use EpisodeLimits for coordinator-owned resource settings")
    kind = input_kind or ("normalized_expression" if task["measurement"] == "continuous" else "count_matrix")
    if kind not in {"count_matrix", "normalized_expression", "raw_reads"}:
        raise ValueError("Unknown input kind")
    options = _json_copy(fixed_options or {})
    _public_only(options)
    supported = _support(assay, kind, task)
    menu = biology_tool_menu(task) if supported else {}
    if not isinstance(options, dict) or set(options) - set(menu):
        raise ValueError("Fixed options must belong to registered actions")
    # Bind declared column roles into both diagnostics and estimation. An
    # absent caller option must not silently select a different column.
    for tool in set(menu) - set(COMMON_CHECKS):
        values = dict(options.get(tool, {}))
        roles = task["analysis_columns"]
        values.setdefault("condition_col", roles["condition"])
        if menu[tool]["driver"] == "bulk":
            values.setdefault("batch_cols", roles.get("batches", []))
            if "blocking" in roles:
                values.setdefault("block_cols", roles["blocking"])
            if (values["batch_cols"] != roles.get("batches", [])
                    or values.get("block_cols", []) != roles.get("blocking", [])):
                raise ValueError("Fixed options cannot change declared batch or fixed-block columns")
        elif roles.get("batches"):
            values.setdefault("batch_cols", roles["batches"])
        options[tool] = values
    for tool, values in options.items():
        validate_biology_request(tool, {"options": values}, task, enforced=True)
        driver = menu[tool]["driver"]
        if driver == "bulk":
            from devbench.study.biology_tools_bulk import validate_bulk_options as validate
        else:
            from devbench.study.biology_tools_singlecell import validate_singlecell_options as validate
        validate(values)
    return {"question": question, "assay": assay, "public_task": task, "limits": asdict(limits),
            "fixed_options": options, "input_kind": kind, "execution_tier": execution_tier}


class AnalysisEpisode:
    """Single-writer decision state; all calculations use the existing broker."""

    @classmethod
    def create(cls, output, *, question, assay, public_task, inputs, limits=None,
               fixed_options=None, input_kind=None, execution_tier="local_supervised",
               parent_root=None, allow_data_execution=False):
        if allow_data_execution is not True:
            raise PermissionError("Episode data execution is disabled")
        from .resources import configure_threads
        configure_threads()
        from devbench.study.biology_broker import BiologyBroker, biology_tool_menu
        from devbench.study.permissions import require_protected_storage
        from devharness_core.provenance import ExecutionDAG, copy_artifact, hash_artifact
        from .runtime import observe_runtime

        # Preserve the existing declaration/parent-receipt error order. The
        # standalone normalizer also validates declarations for its own callers.
        if not isinstance(question, str) or not question.strip() or len(question) > 8000:
            raise ValueError("A bounded biological question is required")
        if assay not in {"bulk", "scrna", "snrna"} or execution_tier not in {"local_supervised", "colab"}:
            raise ValueError("Declare a supported assay vocabulary and execution tier")
        if parent_root is not None and (not isinstance(parent_root, str) or len(parent_root) != 64
                                      or any(c not in "0123456789abcdef" for c in parent_root)):
            raise ValueError("Invalid parent receipt")
        configuration = normalize_episode_configuration(question=question, assay=assay, public_task=public_task,
            limits=limits, fixed_options=fixed_options, input_kind=input_kind, execution_tier=execution_tier)
        task, kind = configuration["public_task"], configuration["input_kind"]
        limits = limits or EpisodeLimits()
        supported = _support(assay, kind, task)
        menu = biology_tool_menu(task) if supported else {}
        if not isinstance(inputs, dict) or set(inputs) != {"expression", "metadata", "annotations"}:
            raise ValueError("Bind expression, metadata and public annotations explicitly")
        root = Path(output)
        if not root.is_absolute() or root.is_symlink():
            raise ValueError("Use a new absolute episode directory")
        require_protected_storage(root)
        root = root.resolve()
        root.mkdir(parents=True, mode=0o700, exist_ok=False)
        self = cls()
        self.root, self.limits = root, limits
        self.created = time.monotonic()
        self.finished = None
        self.measurements = []
        self.state, self.outcome, self.revisions = "ready", None, 0
        self.actions, self.results, self.last_result = [], [], None
        self.clarifications = []
        self.agent_selections, self.agent_policy = [], None
        self.receipt, self.broker = None, None
        self.handoff, self.handoff_count = None, 0
        self.controller_deadline, self.resume_driver, self.retained_broker_receipt = None, None, None
        self.retained_broker_directory = None
        self.review_binding_sha256 = None
        self.lock = threading.RLock()
        self.audit = ExecutionDAG(root / "audit", actor_id="analysis-episode-coordinator")
        self.instance_id = json.loads((root / "audit" / "ledger.json").read_text())["run_id"]
        self.evaluation_plan_sha256 = None
        self.last_node = None
        self.supported, self.menu = supported, menu
        self.started = None
        self.input_paths, snapshot = {}, {}
        try:
            (root / "inputs").mkdir(mode=0o700)
            remaining = limits.max_input_bytes
            for role, supplied in inputs.items():
                source = Path(supplied)
                if not source.is_absolute() or source.is_symlink():
                    raise ValueError("Episode inputs require regular absolute paths")
                suffix = source.suffix or (".zarr" if source.is_dir() else ".data")
                target = root / "inputs" / (role + suffix)
                reference = copy_artifact(source, target, max_bytes=remaining, max_entries=5000)
                remaining -= reference["size_bytes"]
                for path in ([target] if target.is_file() else target.rglob("*")):
                    if path.is_file():
                        path.chmod(0o400)
                self.input_paths[role] = target
                snapshot[role] = {"path": target.relative_to(root).as_posix(),
                    **{key: reference[key] for key in ("kind", "sha256", "size_bytes")}}
            self.snapshot_id = digest(snapshot)
            source_manifest = _sources()
            self.source_id = digest(source_manifest)
            config = {"format": "devbench.analysis.episode/1", **configuration, "snapshot_id": self.snapshot_id,
                "source_sha256": self.source_id, "parent_root": parent_root}
            self.config = _json_copy(config)
            self.config_id = digest(config)
            self.snapshot = snapshot
            atomic_json(root / "parameters.json", config)
            atomic_json(root / "snapshot.json", snapshot)
            atomic_json(root / "source-identity.json", source_manifest)
            self.runtime = observe_runtime(execution_tier, detect_gpu=False)
            atomic_json(root / "runtime.json", self.runtime)
            self.checkpoints = CheckpointStore(root / "checkpoints", {"config": self.config_id,
                "snapshot": self.snapshot_id, "source": self.source_id,
                "python": self.runtime["runtime"]["python"], "packages": self.runtime["runtime"]["all_package_versions"]})
            for name in ("parameters.json", "snapshot.json", "source-identity.json", "runtime.json"):
                self.audit.add_artifact(root / name)
            for role, path in self.input_paths.items():
                self.audit.add_artifact(path, max_bytes=limits.max_input_bytes, max_entries=5000)
            self._record("episode.started", {"config_sha256": self.config_id, "snapshot_id": self.snapshot_id,
                "supported": supported, "model_sessions_launched": False,
                "control_scope": "trusted_coordinator_and_supervised_workers_not_live_participant_isolation"})
            if supported:
                policy, bound, aliases = self._broker_spec()
                self.broker = BiologyBroker(policy, aliases,
                    root / "tool-evidence", public_task=bound, allow_data_execution=True, defer_start=True)
            return self
        except BaseException as error:
            atomic_json(root / "creation-failure.json", {"outcome": "infrastructure_failure",
                "error_type": type(error).__name__, "message": str(error)[:2000]})
            self.audit.add_artifact(root / "creation-failure.json")
            self._record("episode.creation_failed", {"error_type": type(error).__name__})
            atomic_json(root / "root-receipt.json", self.audit.finalize())
            raise

    def _record(self, kind, payload):
        self.last_node = self.audit.append(kind, _json_copy(payload), parents=(() if self.last_node is None else (self.last_node,)))

    def _broker_spec(self):
        """Rebuild only the broker contract derived from immutable episode data."""
        from devbench.study.broker import BrokerPolicy
        task = _json_copy(self.config["public_task"])
        task.update(input_sha256=self.snapshot_id,
            expression_path=str(self.input_paths["expression"]), metadata_path=str(self.input_paths["metadata"]),
            public_annotations_path=str(self.input_paths["annotations"]))
        policy = BrokerPolicy("enforced", self.snapshot_id,
            "continuous" if task["measurement"] == "continuous" else "raw_counts", tuple(task["gene_ids"]),
            population_inference_allowed=True, timeout_sec=self.limits.timeout_seconds,
            implementation_sha256=self.source_id, max_calls=self.limits.max_steps, max_memory_mb=self.limits.memory_mb)
        return policy, task, {str(path): path for path in self.input_paths.values()}

    def register_agent(self, policy):
        """Trusted coordinator registration, not an agent-selectable action."""
        with self.lock:
            if self.outcome is not None or self.actions or self.agent_policy is not None:
                raise ValueError("Register one agent policy before episode actions")
            self._check_inputs()
            self.agent_policy = _json_copy(policy)
            self._record("episode.agent_registered", {"policy": self.agent_policy,
                "scope": "structured_action_selection_not_scientific_correctness"})

    def record_agent_selection(self, evidence, artifact):
        """Bind coordinator-observed session evidence before a terminal action."""
        with self.lock:
            if self.outcome is not None or self.agent_policy is None:
                raise ValueError("Agent evidence requires an active registered controller")
            artifact = Path(artifact)
            if artifact.is_symlink() or not artifact.resolve().is_relative_to(self.root / "agent"):
                raise ValueError("Agent evidence must be in coordinator-owned episode storage")
            record = _json_copy(evidence)
            if not isinstance(record, dict) or len(json.dumps(record)) > 4_000_000:
                raise ValueError("Agent evidence must be a bounded object")
            for name, maximum in (("model_sessions_attempted", 2), ("tokens", 1_000_000_000)):
                value = record.get(name)
                if value is not None and (type(value) is not int or not 0 <= value <= maximum):
                    raise ValueError("Invalid agent usage evidence")
            model = record.get("actual_model")
            if model is not None and (not isinstance(model, str) or not 1 <= len(model) <= 300):
                raise ValueError("Invalid observed model evidence")
            self._record("episode.agent_selection", {"sequence": len(self.agent_selections)+1,
                "evidence": record, "artifact": self.audit.add_artifact(artifact)})
            self.agent_selections.append(record)

    def abort(self, reason, *, scientific=False):
        """Trusted control-plane termination; never available in the tool menu."""
        with self.lock:
            if self.outcome is not None:
                raise RuntimeError("Terminal episodes cannot be aborted again")
            if not isinstance(reason, str) or not reason.strip() or len(reason) > 2000 or type(scientific) is not bool:
                raise ValueError("A bounded coordinator failure reason is required")
            if self.handoff is not None:
                from .episode_handoff import resume_handoff
                resumed = resume_handoff(self.root, self.handoff["manifest_path"], expected_root=self.handoff["root_hash"],
                    abort_reason=reason, abort_scientific=scientific, allow_data_execution=True)
                self.__dict__.update(resumed.__dict__)
                return json.loads((self.root / "episode.json").read_text())
            self._record("episode.coordinator_termination", {"reason": reason, "scientific": scientific})
            return self._finish("scientific_failure" if scientific else "infrastructure_failure", reason)

    def register_evaluation(self, plan_sha256):
        """Trusted coordinator commitment; this is not a participant action."""
        with self.lock:
            if (self.state != "ready" or self.actions or self.outcome is not None
                    or (self.broker is not None and self.broker.events)):
                raise ValueError("Evaluation must be registered before episode actions")
            if self.evaluation_plan_sha256 is not None:
                raise ValueError("An evaluation plan is already registered")
            if not isinstance(plan_sha256, str) or len(plan_sha256) != 64 or any(c not in "0123456789abcdef" for c in plan_sha256):
                raise ValueError("Invalid evaluation plan identity")
            self._check_inputs()
            self._record("episode.evaluation_registered", {"plan_sha256": plan_sha256, "instance_id": self.instance_id})
            self.evaluation_plan_sha256 = plan_sha256

    def _check_inputs(self):
        from devharness_core.provenance import hash_artifact
        if digest(json.loads((self.root / "parameters.json").read_text())) != self.config_id:
            raise ValueError("episode_configuration_changed")
        if digest(self.config) != self.config_id or digest(self.snapshot) != self.snapshot_id:
            raise ValueError("episode_in_memory_configuration_changed")
        for role, path in self.input_paths.items():
            if hash_artifact(path, max_bytes=self.limits.max_input_bytes, max_entries=5000)["sha256"] != self.snapshot[role]["sha256"]:
                raise ValueError("episode_input_snapshot_changed")
        if digest(_sources()) != self.source_id:
            raise ValueError("episode_implementation_changed")
        if getattr(self, "review_binding_sha256", None) is not None:
            from .episode_proposals import verify_bound_review
            verify_bound_review(self.root, self.review_binding_sha256, self.config, self.snapshot)
        elif (self.root / "review-binding.json").exists():
            raise ValueError("unregistered_episode_review_record")

    def observation(self):
        from devbench.study.biology_broker import COMMON_CHECKS, PREREQUISITES, AUDIT_TOOLS
        coverage = ("unavailable" if not self.measurements else
                    "sampled" if all(m.get("memory_coverage") == "sampled" for m in self.measurements) else "sampled_partial")
        rss = max((m["sampled_peak_rss_bytes"] for m in self.measurements), default=0)/1024**2 if coverage == "sampled" else None
        catalog = {name: {"action": {"kind": "tool", "tool": name},
            "fixed_options": self.config["fixed_options"].get(name, {}), "capability": description,
            "prerequisites": [] if name in AUDIT_TOOLS else [*COMMON_CHECKS, *PREREQUISITES.get(name, ())]}
            for name, description in self.menu.items()}
        attempts = [item.get("model_sessions_attempted") for item in self.agent_selections]
        tokens = [item.get("tokens") for item in self.agent_selections]
        models = {item.get("actual_model") for item in self.agent_selections}
        model = next(iter(models)) if len(models) == 1 and all(
            item.get("model_identity_verified") is True for item in self.agent_selections) else None
        agent = {"selections": len(self.agent_selections), "model_sessions_attempted":
            sum(attempts) if all(type(value) is int and value >= 0 for value in attempts) else None,
            "evidence_scope": "coordinator_session_records_not_biological_validation"}
        return _json_copy({"format": "devbench.analysis.observation/1", "state": self.state,
            "outcome": self.outcome, "terminated": self.outcome is not None, "snapshot_id": self.snapshot_id,
            "question": self.config["question"], "assay": self.config["assay"],
            "support_status": "bounded_workflow" if self.supported else "unsupported_route",
            "allowed_tools": sorted(self.menu), "tool_catalog": catalog,
            "steps": len(self.actions), "revisions": self.revisions,
            "steps_remaining": max(0, self.limits.max_steps-len(self.actions)),
            "scientific_correctness": None, "harness_accepted": self.outcome in {"completed", "refusal"} if self.outcome else None,
            "harness_acceptance_scope": "software_execution_or_supported_refusal",
            "scientific_trial_passed": False,
            "agent_execution": agent,
            "usage": {"tokens": sum(tokens) if tokens and all(type(value) is int and value >= 0 for value in tokens) else None,
                      "cost": None, "model": model,
                      "elapsed_sec": (self.finished if self.finished is not None else time.monotonic())-self.created,
                      "RSS_MB": rss, "memory_coverage": coverage},
            "evaluation_feedback_available": False,
            "review_status": "recorded_operator_research_review" if getattr(self, "review_binding_sha256", None) else "not_recorded",
            "reporting": {"maximum_selected_findings": 20,
                "finish_reference_fields": ["action", "target_id", "hypothesis_id"],
                "scope": "optional_references_to_prior_computations_numbers_come_from_verified_receipts"}})

    def _refusal_supported(self):
        if not self.supported:
            return True
        for result in self.results:
            diagnostic = result.get("diagnostics", {})
            if diagnostic.get("status") == "support_unavailable":
                return True
            # This diagnostic has no target fragment: the design audit fails
            # before fitting. Recognize its specific scientific refusal, never
            # arbitrary worker, dependency, or numerical errors as success.
            method, error = result.get("method", {}), diagnostic.get("analysis_error", {})
            if (method.get("tool") == "validate_design_matrix"
                    and method.get("version") == "observed-bulk-1"
                    and method.get("uses_observed_inputs_only") is True
                    and error.get("type") == "ValueError"
                    and error.get("code") == "repeated_units_across_design_coordinates_require_cluster_model"):
                return True
            design = diagnostic.get("design", {})
            if design.get("condition_number_validated") is False:
                return True
            for target in result.get("target_results", []):
                if target.get("claim_type") == "refusal" and any(
                        check.get("status") in {"unknown", "violated"} for check in target.get("support_checks", [])):
                    return True
        return False

    def _finish(self, outcome, reason):
        if self.outcome is not None:
            raise RuntimeError("Terminal episodes cannot be changed")
        self.finished = time.monotonic()
        try:
            return self._seal_outcome(outcome, reason)
        except BaseException as error:
            self.outcome, self.state, self.receipt = "infrastructure_failure", "terminal", None
            # Preserve the failed publication where storage still permits it.
            # Without a retained verified receipt no result is accepted.
            try:
                atomic_json(self.root / "finalization-failure.json", {
                    "outcome": self.outcome, "harness_accepted": False,
                    "error_type": type(error).__name__, "message": str(error)[:2000]})
            except (OSError, ValueError):
                pass
            raise

    def _seal_outcome(self, outcome, reason):
        if self.outcome is not None:
            raise RuntimeError("Terminal episodes cannot be changed")
        termination_context = {"initial_outcome": outcome, "initial_reason": reason}
        self.outcome, self.state = outcome, "terminal"
        broker_receipt = self.broker.close() if self.broker is not None else self.retained_broker_receipt
        if broker_receipt is not None and broker_receipt.get("capture_complete") is not True:
            outcome, reason = "infrastructure_failure", "tool_evidence_capture_incomplete"
            self.outcome = outcome
        final = {**self.observation(), "reason": reason, "refusal_supported": self._refusal_supported(),
            "scientific_support": "registered_checks_only_conditional_on_declared_inputs",
            "broker_receipt": broker_receipt, "source_sha256": self.source_id,
            "broker_directory": (self.broker.output_dir.relative_to(self.root).as_posix() if self.broker is not None
                                 else getattr(self, "retained_broker_directory", None)),
            "finding_requests": getattr(self, "synthesis_requests", []),
            "audit_prefix_node_ids": list(self.audit._node_ids), "termination_context": termination_context,
            "terminal_scope": "workflow_outcome_not_reference_based_correctness"}
        from .episode_synthesis import safe_synthesis, render_synthesis
        synthesis = safe_synthesis(self.root, self.config, final, self.actions)
        if synthesis["evidence"]["status"] == "withheld":
            if final["outcome"] != "infrastructure_failure":
                final["reason"] = "synthesis_evidence_unverified"
            final.update(outcome="infrastructure_failure", harness_accepted=False)
        elif outcome == "completed" and not synthesis["outputs_complete"]:
            final.update(outcome="scientific_failure", harness_accepted=False, reason="verified_outputs_not_complete")
        self.outcome = final["outcome"]
        synthesis.update(outcome=final["outcome"], harness_accepted=final["harness_accepted"], reason=final["reason"])
        atomic_json(self.root / "episode.json", final)
        atomic_json(self.root / "actions.json", self.actions)
        atomic_json(self.root / "clarifications.json", self.clarifications)
        atomic_json(self.root / "synthesis.json", synthesis)
        (self.root / "summary.md").write_text(render_synthesis(synthesis))
        inventory = {}
        for path in sorted(self.root.rglob("*")):
            relative = path.relative_to(self.root)
            if relative.parts[0] == "audit" or path.is_dir() or relative.as_posix() == "root-receipt.json":
                continue
            if path.is_symlink():
                raise ValueError("Episode artifacts cannot be symlinks")
            inventory[relative.as_posix()] = sha256(path)
            self.audit.add_artifact(path, max_bytes=128_000_000)
        atomic_json(self.root / "artifact-index.json", inventory)
        self.audit.add_artifact(self.root / "artifact-index.json")
        self._record("episode.terminal", {"outcome": final["outcome"], "record_sha256": sha256(self.root / "episode.json"),
                                         "artifact_index_sha256": sha256(self.root / "artifact-index.json")})
        self.receipt = self.audit.finalize(analysis_fingerprint=digest({"config": self.config_id, "snapshot": self.snapshot_id}))
        atomic_json(self.root / "root-receipt.json", self.receipt)
        return final

    def _reject(self, code):
        self.state = "needs_revision"
        result = {**self.observation(), "status": "rejected", "diagnostic": code}
        self._record("episode.action_rejected", result)
        return result

    def _claim_attempts(self):
        # Derive from the retained journal so replay and clarification handoffs
        # cannot reset the shared schema/evidence repair budget.
        return sum(action.get("kind") == "finish" or action == {
            "kind": "invalid", "reason": "invalid_claim_values"} for action in self.actions)

    def _reject_claim(self, code):
        result = self._reject(code)
        if self._claim_attempts() >= 2:
            return self._finish("scientific_failure", "claim_rewrite_budget_exhausted")
        return result

    def step(self, action, *, deadline=None):
        with self.lock:
            if self.outcome is not None:
                raise RuntimeError("Terminal episodes cannot accept actions")
            if self.handoff is not None:
                raise RuntimeError("Information handoff is checkpointed; use verified continuation")
            if deadline is not None and (isinstance(deadline, bool) or not isinstance(deadline, (int, float)) or not math.isfinite(deadline)):
                raise ValueError("Coordinator deadline must be a finite monotonic timestamp")
            if self.controller_deadline is not None:
                deadline = min(deadline, self.controller_deadline) if deadline is not None else self.controller_deadline
            if deadline is not None:
                if time.monotonic() >= deadline:
                    return self._finish("infrastructure_failure", "coordinator_deadline_exhausted")
            try:
                self._check_inputs()
            except (OSError, ValueError) as error:
                return self._finish("infrastructure_failure", str(error))
            if len(self.actions) >= self.limits.max_steps:
                return self._finish("scientific_failure", "action_budget_exhausted")
            fields = {"tool": {"kind", "tool", "options"}, "finish": {"kind", "findings"},
                      "refuse": {"kind", "reason"}, "revise": {"kind", "reason"},
                      "request_information": {"kind", "message"}}
            claim_attempt = isinstance(action, dict) and (action.get("kind") == "finish" or action == {
                "kind": "invalid", "reason": "invalid_claim_values"})
            if (not isinstance(action, dict) or not isinstance(action.get("kind"), str) or action["kind"] not in fields
                    or set(action)-fields[action["kind"]]):
                self.actions.append({"kind": "invalid", "reason": "invalid_claim_values" if claim_attempt else "unknown_action_fields"})
                if claim_attempt:
                    return self._reject_claim("invalid_claim_values")
                return self._reject("unknown_action_fields")
            try:
                _public_only(action)
                accepted = _json_copy(action)
                if len(json.dumps(accepted)) > 32_000:
                    raise ValueError("action_too_large")
                for key in ("reason", "message"):
                    if key in action and (not isinstance(action[key], str) or not action[key].strip() or len(action[key]) > 4000):
                        raise ValueError("invalid_action_text")
                if action["kind"] == "tool":
                    if not isinstance(action.get("tool"), str) or not 1 <= len(action["tool"]) <= 160:
                        raise ValueError("invalid_tool_name")
                    if "options" in action and action["options"] != self.config["fixed_options"].get(action["tool"], {}):
                        raise ValueError("method_parameters_are_coordinator_frozen")
            except (TypeError, ValueError):
                self.actions.append({"kind": "invalid", "reason": "invalid_claim_values" if claim_attempt else "invalid_action_values"})
                if claim_attempt:
                    return self._reject_claim("invalid_claim_values")
                return self._reject("invalid_action_values")
            self.actions.append(accepted)
            self._record("episode.action", {"sequence": len(self.actions), "action": accepted,
                "state_before": self.state, "actor_scope": "coordinator_supplied_action_not_authenticated_identity"})
            kind = action["kind"]
            if kind == "request_information":
                self.state = "awaiting_information"
                result = {**self.observation(), "status": "information_requested", "message": action.get("message", "Clarify the experimental metadata")}
                self._record("episode.observation", result)
                return result
            if kind == "revise":
                if self.state not in {"needs_revision", "awaiting_information"}:
                    return self._reject("revision_requires_diagnostic_or_information_request")
                if self.revisions >= self.limits.max_revisions:
                    return self._finish("scientific_failure", "revision_budget_exhausted")
                self.revisions += 1
                self.state = "ready"
                result = {**self.observation(), "status": "revision_started", "scope": "strategy_only_inputs_and_settings_unchanged"}
                self._record("episode.observation", result)
                return result
            if kind == "refuse":
                return self._finish("refusal" if self._refusal_supported() else "scientific_failure",
                                    "diagnostic_or_policy_refusal" if self._refusal_supported() else "unsupported_refusal_claim")
            if kind == "finish":
                from .episode_synthesis import check_finding_requests, validate_finding_requests
                requests = action.get("findings", [])
                try:
                    validate_finding_requests(requests)
                except (TypeError, ValueError):
                    return self._reject_claim("invalid_claim_values")
                try:
                    if requests:
                        check_finding_requests(self, requests)
                except (OSError, ValueError, TypeError, KeyError):
                    return self._reject_claim("finding_reference_not_supported_by_current_results")
                self.synthesis_requests = _json_copy(requests)
                latest = {target["target_id"]: target for result in self.results for target in result.get("target_results", [])}
                expected = {target["target_id"] for target in self.config["public_task"]["questions"]}
                complete = set(latest) == expected and all(t.get("estimate_status") == "estimated" for t in latest.values())
                if complete:
                    complete = all(row.get("estimate_status", "estimated") == "estimated"
                        for target in latest.values() for row in (target.get("payload") or {}).get("gene_statistics", []))
                return self._finish("completed" if complete else "refusal" if self._refusal_supported() else "scientific_failure",
                                    "requested_outputs_checked" if complete else "requested_outputs_not_fully_supported")
            if self.state != "ready":
                return self._reject("resolve_current_decision_before_another_tool")
            if self._claim_attempts():
                return self._reject("claim_repair_cannot_reenter_execution")
            tool = action.get("tool")
            if tool not in self.menu or self.broker is None:
                return self._reject("unsupported_tool_or_workflow")
            options = self.config["fixed_options"].get(tool, {})
            if "options" in action and action["options"] != options:
                return self._reject("method_parameters_are_coordinator_frozen")
            from devbench.study.biology_broker import COMMON_CHECKS, PREREQUISITES, AUDIT_TOOLS, _audit_completed
            if tool not in AUDIT_TOOLS:
                for check in (*COMMON_CHECKS, *PREREQUISITES.get(tool, ())):
                    if not any(event["tool"] == check and _audit_completed(event, self.broker.results)
                               and (check in COMMON_CHECKS or event["arguments"].get("options", {}) == options)
                               for event in self.broker.events):
                        return self._reject("required_diagnostic_not_completed:" + check)
            if self.started is None:
                self.broker.start_analysis()
                self.started = time.monotonic()
            if time.monotonic() - self.started >= self.limits.timeout_seconds:
                return self._finish("infrastructure_failure", "episode_deadline_exhausted")
            stage = f"action-{len(self.actions):04d}"
            self.state = "running"
            def execute(folder):
                from devharness_core.watchdog import worker_limits
                with worker_limits(deadline=min(self.broker.started+self.limits.timeout_seconds,
                                                time.monotonic()+self.limits.stage_timeout_seconds,
                                                deadline if deadline is not None else math.inf),
                                   max_mem_mb=self.limits.memory_mb):
                    response = self.broker.execute(tool, {"options": options})
                atomic_json(folder / "observation.json", response)
                if response["status"] != "succeeded":
                    raise RuntimeError("supervised_tool_execution_failed:" + str(response.get("error")))
                return {"tool": tool, "receipt_id": response["receipt_id"]}
            try:
                folder = self.checkpoints.run(stage, execute)
                self.measurements.append(json.loads((folder / "receipt.json").read_text())["measurement"])
                response = json.loads((folder / "observation.json").read_text())
                self.last_result = _json_copy(response["result"])
                self.results.append(self.last_result)
                self.state = "ready"
                self._record("episode.observation", {"sequence": len(self.actions), "tool": tool,
                    "checkpoint_receipt": self.audit.add_artifact(folder / "receipt.json"),
                    "result_sha256": sha256(folder / "observation.json")})
                return {**self.observation(), **response}
            except BaseException as error:
                failures = sorted((self.root / "checkpoints" / "failures").glob(stage+"-*.json"))
                if failures:
                    self.measurements.append(json.loads(failures[-1].read_text())["measurement"])
                final = self._finish("infrastructure_failure", f"{type(error).__name__}: {error}"[:2000])
                if not isinstance(error, Exception):
                    raise
                return final

    def reset(self, output, *, allow_data_execution=False):
        if allow_data_execution is not True:
            raise PermissionError("Reset/re-execution requires coordinator authorization")
        from .episode_proposals import copy_review_for_replay, require_disjoint_output
        require_disjoint_output(output, [self.root])
        self._check_inputs()
        expected_review_binding = getattr(self, "review_binding_sha256", None)
        if self.outcome is None:
            self.abort("coordinator_reset_before_completion")
        config = self.config
        clone = type(self).create(output, question=config["question"], assay=config["assay"],
            public_task=config["public_task"], inputs=self.input_paths, limits=self.limits,
            fixed_options=config["fixed_options"], input_kind=config["input_kind"],
            execution_tier=config["execution_tier"], parent_root=self.receipt["root_hash"], allow_data_execution=True)
        try:
            copy_review_for_replay(self.root, clone, expected_binding_sha256=expected_review_binding)
            return clone
        except BaseException as error:
            clone.abort("review_reset_binding_failed:" + type(error).__name__)
            raise

    def replay(self, output, *, expected_root, allow_data_execution=False):
        if allow_data_execution is not True:
            raise PermissionError("Numerical replay requires coordinator authorization")
        return replay_episode(self.root, expected_root, output, allow_data_execution=True)


def verify_episode(root, expected_root):
    """Verify retained source artifacts against a caller-retained DAG receipt."""
    from devharness_core.provenance import verify_execution, canonical_json
    from devbench.study.evidence import _read, decode_json
    root = Path(root)
    if not root.is_absolute() or root.is_symlink():
        raise ValueError("Use an absolute episode directory")
    report = verify_execution(root / "audit" / "manifest.json", expected_root)
    if not report["valid"]:
        raise ValueError("episode_audit_failed:" + ";".join(report["errors"]))
    manifest = decode_json(_read(root / "audit/manifest.json", 16_000_000))
    manifest_content = {key: value for key, value in manifest.items() if key != "root_hash"}
    if hashlib.sha256(canonical_json(manifest_content)).hexdigest() != expected_root:
        raise ValueError("episode_manifest_changed_after_verification")
    index_content = _read(root / "artifact-index.json", 16_000_000)
    index_hash = hashlib.sha256(index_content).hexdigest()
    if not any(a.get("name") == "artifact-index.json" and a.get("sha256") == index_hash for a in manifest["artifacts"]):
        raise ValueError("episode_artifact_index_not_bound")
    expected = decode_json(index_content)
    if not isinstance(expected, dict):
        raise ValueError("episode_artifact_index_invalid")
    observed = set()
    for path in root.rglob("*"):
        name = path.relative_to(root).as_posix()
        if name.split("/")[0] == "audit" or name in {"artifact-index.json", "root-receipt.json"} or path.is_dir():
            continue
        if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
            raise ValueError("episode_artifact_path_invalid")
        observed.add(name)
        if expected.get(name) != sha256(path):
            raise ValueError("episode_artifact_changed:" + name)
    if observed != set(expected):
        raise ValueError("episode_artifact_inventory_changed")
    review_bindings = []
    for node_id in manifest["node_ids"]:
        node = decode_json(_read(root / "audit/nodes" / (node_id + ".json"), 4_000_000))
        node_content = {key: value for key, value in node.items() if key != "node_id"}
        if hashlib.sha256(canonical_json(node_content)).hexdigest() != node_id:
            raise ValueError("episode_node_changed_after_verification")
        if node["kind"] == "episode.review_bound":
            review_bindings.append(node["payload"]["binding_sha256"])
    if len(review_bindings) > 1:
        raise ValueError("multiple_episode_review_bindings")
    return {**report, "review_binding_sha256": review_bindings[0] if review_bindings else None,
            "scope": "artifact_integrity_not_scientific_correctness_or_numerical_replay"}


def replay_episode(root, expected_root, output, *, allow_data_execution=False):
    """Re-execute the verified journal without relying on a live Python object."""
    if allow_data_execution is not True:
        raise PermissionError("Numerical replay requires coordinator authorization")
    from .runtime import observe_runtime
    root = Path(root)
    from .episode_proposals import require_disjoint_output
    require_disjoint_output(output, [root])
    verified_original = verify_episode(root, expected_root)
    config = json.loads((root / "parameters.json").read_text())
    if digest(_sources()) != config["source_sha256"]:
        raise ValueError("episode_implementation_changed_use_new_analysis")
    old_runtime = json.loads((root / "runtime.json").read_text())["runtime"]
    new_runtime = observe_runtime(config["execution_tier"], detect_gpu=False)["runtime"]
    if any(old_runtime[key] != new_runtime[key] for key in ("python", "all_package_versions")):
        raise ValueError("episode_runtime_changed_use_new_analysis")
    snapshot = json.loads((root / "snapshot.json").read_text())
    actions = json.loads((root / "actions.json").read_text())
    clarification_path = root / "clarifications.json"
    clarifications = json.loads(clarification_path.read_text()) if clarification_path.exists() else []
    from .episode_handoff import apply_clarification, validate_clarification
    if not isinstance(clarifications, list) or len(clarifications) > config["limits"]["max_revisions"]:
        raise ValueError("Invalid saved clarification journal")
    after_actions = []
    for item in clarifications:
        if (not isinstance(item, dict) or set(item) != {"after_action", "response"}
                or type(item["after_action"]) is not int or not 1 <= item["after_action"] <= len(actions)
                or actions[item["after_action"]-1].get("kind") != "request_information"):
            raise ValueError("Invalid saved clarification position")
        validate_clarification(item["response"])
        after_actions.append(item["after_action"])
    if after_actions != sorted(set(after_actions)):
        raise ValueError("Clarifications must be unique and chronological")
    clone = AnalysisEpisode.create(output, question=config["question"], assay=config["assay"],
        public_task=config["public_task"], inputs={role: root / item["path"] for role, item in snapshot.items()},
        limits=EpisodeLimits(**config["limits"]), fixed_options=config["fixed_options"],
        input_kind=config["input_kind"], execution_tier=config["execution_tier"],
        parent_root=expected_root, allow_data_execution=True)
    from .episode_proposals import copy_review_for_replay
    try:
        copy_review_for_replay(root, clone, expected_binding_sha256=verified_original["review_binding_sha256"])
    except BaseException as error:
        clone.abort("review_replay_binding_failed:" + type(error).__name__)
        raise
    by_action = {item["after_action"]: item["response"] for item in clarifications}
    for number, action in enumerate(actions, 1):
        if clone.outcome is not None:
            break
        clone.step(action)
        if number in by_action and clone.outcome is None:
            apply_clarification(clone, by_action[number])
    if clone.outcome is None:
        prior = json.loads((root / "episode.json").read_text())
        clone._record("episode.replay_external_boundary", {"model_sessions_launched": False,
            "scope": "numerical_action_replay_does_not_repeat_external_selector"})
        clone.abort("replay_journal_ended_at_external_boundary", scientific=prior["outcome"] == "scientific_failure")
    return clone
