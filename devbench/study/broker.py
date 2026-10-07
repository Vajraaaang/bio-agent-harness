"""Coordinator-owned tool execution and receipts for the three-arm study.

This module never starts workers or reads expression files on import. An
executing coordinator must explicitly authorize data operations and establish
the participant's OS read boundary before creating a session.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import hmac
import json
import re
from pathlib import Path
import threading
import time

from .contracts import canonical_json_bytes, content_sha256

TOOLS = {
    "audit_quality": "audit_expression_quality",
    "validate_design": "validate_design_matrix",
    "analyze_batch_effects": "analyze_batch_effects",
    "fit_trajectory": "fit_temporal_trajectory",
    "infer_lineage": "infer_lineage",
    "infer_regulatory_network": "infer_regulatory_network",
    "regulon_enrichment": "regulon_enrichment",
    "motif_enrichment": "motif_enrichment",
    "compact_manifold": "compact_temporal_state",
}
MAX_REQUEST_BYTES = 256_000
MAX_RESULT_BYTES = 8_000_000
LOCAL_TOOLS = {"fit_family", "inspect_study_design", "summarize_observed_expression"}


def parse_request(content, *, expected_capability=None, allowed_tools=None):
    """Validate bounded JSON without invoking a tool or inspecting inputs."""
    if not isinstance(content, bytes) or len(content) > MAX_REQUEST_BYTES:
        raise ValueError("Tool request exceeds its byte contract")

    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("Duplicate JSON keys are forbidden")
            result[key] = value
        return result

    def invalid_constant(value):
        raise ValueError("Nonfinite JSON numbers are forbidden")

    request = json.loads(content, object_pairs_hook=pairs, parse_constant=invalid_constant)
    names = {*TOOLS, *LOCAL_TOOLS} if allowed_tools is None else frozenset(allowed_tools)
    if (not isinstance(request, dict) or set(request) != {"tool", "arguments", "capability"}
            or not isinstance(request["tool"], str) or request["tool"] not in names
            or not isinstance(request["arguments"], dict)):
        raise ValueError("Unknown tool or malformed request")
    capability = request["capability"]
    if (not isinstance(expected_capability, str) or not isinstance(capability, str)
            or not 32 <= len(capability) <= 128 or not hmac.compare_digest(capability, expected_capability)):
        raise PermissionError("This tool capability does not authorize the request")
    return {"tool": request["tool"], "arguments": request["arguments"]}


@dataclass(frozen=True)
class BrokerPolicy:
    condition: str
    input_sha256: str
    measurement_type: str
    gene_ids: tuple[str, ...]
    population_inference_allowed: bool
    timeout_sec: float
    implementation_sha256: str
    max_calls: int = 100
    max_evidence_bytes: int = 128_000_000
    max_memory_mb: int = 4096
    unit_provenance_verified: bool = False
    time_col: str = "numeric_time"
    biological_unit_column: str = "biological_unit_id"

    def __post_init__(self):
        if self.condition not in {"optional", "enforced"}:
            raise ValueError("Only assisted conditions receive a tool broker")
        if self.measurement_type not in {"raw_counts", "continuous"}:
            raise ValueError("A declared supported measurement type is required")
        if (any(len(value) != 64 or any(c not in "0123456789abcdef" for c in value)
                for value in (self.input_sha256, self.implementation_sha256))
                or not self.gene_ids or len(set(self.gene_ids)) != len(self.gene_ids)
                or len(self.gene_ids) > 5000):
            raise ValueError("Broker requires a bounded fixed hypothesis family and input hash")
        if any(not isinstance(gene, str) or not 1 <= len(gene) <= 200 for gene in self.gene_ids):
            raise ValueError("Invalid hypothesis identifiers")
        if type(self.population_inference_allowed) is not bool:
            raise ValueError("Population scope must be coordinator-declared")
        if type(self.unit_provenance_verified) is not bool:
            raise ValueError("Unit provenance status must be coordinator-declared")
        if any(not isinstance(value, str) or not 1 <= len(value) <= 200
               for value in (self.time_col, self.biological_unit_column)):
            raise ValueError("Task time and biological-unit columns must be explicitly named")
        if type(self.max_calls) is not int or self.max_calls < 1 or self.max_calls > 1000:
            raise ValueError("Invalid broker request budget")
        if isinstance(self.timeout_sec, bool) or not 0 < self.timeout_sec <= 86_400:
            raise ValueError("Invalid broker deadline")
        if type(self.max_evidence_bytes) is not int or not 1_000_000 <= self.max_evidence_bytes <= 512_000_000:
            raise ValueError("Invalid broker evidence byte budget")
        if type(self.max_memory_mb) is not int or not 64 <= self.max_memory_mb <= 1_048_576:
            raise ValueError("Invalid broker worker memory budget")


class ToolBroker:
    """Execute real tools and keep receipts outside the participant workspace."""
    def __init__(self, policy, input_aliases, output_dir, *, allow_data_execution=False, defer_start=False):
        if allow_data_execution is not True:
            raise PermissionError("Data execution is disabled; planning cannot create a tool broker")
        if type(defer_start) is not bool:
            raise ValueError("defer_start must be an explicit boolean")
        from devharness_server.dispatcher import ToolDispatcher
        from devharness_core.provenance import hash_artifact
        self.policy = policy
        self.output_dir = Path(output_dir)
        if (not self.output_dir.is_absolute()
                or any(path.is_symlink() for path in (self.output_dir, *self.output_dir.parents))):
            raise ValueError("Broker output must be a new absolute coordinator directory")
        from .permissions import require_protected_storage
        require_protected_storage(self.output_dir)
        self.output_dir = self.output_dir.resolve()
        self.output_dir.mkdir(parents=True, mode=0o700, exist_ok=False)
        self.scratch_dir = self.output_dir / "scratch"
        self.scratch_dir.mkdir(mode=0o700)
        self.aliases = {}
        for public, private in input_aliases.items():
            public, private = Path(public), Path(private)
            if not public.is_absolute() or not private.is_absolute() or private.is_symlink():
                raise ValueError("Input aliases must refer to protected absolute paths")
            self.aliases[str(public.resolve())] = private.resolve()
        if not self.aliases:
            raise ValueError("Protected input aliases are required")
        self.input_hashes = {str(path): hash_artifact(path)["sha256"] for path in self.aliases.values()}
        self.dispatcher = ToolDispatcher()
        self.started = None if defer_start else time.monotonic()
        self.events = []
        self.results = {}
        self.lock = threading.RLock()
        self.closed = False
        self.evidence_bytes = 0
        self.capture_error = None
        self.final_receipt = None
        self.execution_ledger = None

    def enable_execution_scopes(self):
        from .execution_ledger import ExecutionLedger
        if self.events or self.execution_ledger is not None:
            raise ValueError("Execution scoping must be enabled once before tool calls")
        self.execution_ledger = ExecutionLedger()

    def observe_execution(self, phase, context):
        if self.execution_ledger is None or self.started is None:
            return  # Permission-probe commands are not analysis executions.
        if not context.get("script_invocation_candidate", True):
            return
        self.execution_ledger.observe(phase, context)

    def start_analysis(self):
        """Start a deferred tool window once, from trusted coordinator code.

        The outer attempt supervisor still bounds every condition. This does
        not reset or extend an already active window and is not a public tool.
        """
        with self.lock:
            if self.closed:
                raise RuntimeError("Tool session is closed")
            if self.started is not None:
                raise RuntimeError("Tool analysis window already started")
            self.started = time.monotonic()
            return {"started_monotonic": self.started, "timeout_sec": self.policy.timeout_sec,
                    "origin": "trusted_coordinator_activation", "outer_attempt_deadline_unchanged": True}

    def _store(self, name, content, *, append=False):
        if self.capture_error is not None:
            raise RuntimeError("Tool evidence capture has already failed")
        if self.evidence_bytes + len(content) > self.policy.max_evidence_bytes:
            self.capture_error = "Coordinator evidence byte budget exhausted"
            raise RuntimeError(self.capture_error)
        try:
            with (self.output_dir / name).open("ab" if append else "xb") as handle:
                handle.write(content)
            self.evidence_bytes += len(content)
        except OSError as error:
            self.capture_error = f"Evidence persistence failed: {type(error).__name__}"
            raise

    @property
    def allowed_tools(self):
        return frozenset({*TOOLS, *LOCAL_TOOLS})

    def _invoke_tool(self, tool, arguments):
        """Trusted subclasses may supply another allowlisted observed-data driver.

        This hook runs inside the same input-integrity, resource and receipt
        boundary. Participants never provide callables or import names.
        """
        if tool == "fit_family":
            return self._fit_family(arguments)
        if tool == "inspect_study_design":
            return self._observed_admission(arguments)
        if tool == "summarize_observed_expression":
            return self._observed_summary(arguments)
        return json.loads(self.dispatcher.execute(TOOLS[tool], self._backend_arguments(tool, arguments)))

    def _check_inputs(self):
        from devharness_core.provenance import hash_artifact
        for path, expected in self.input_hashes.items():
            if hash_artifact(Path(path))["sha256"] != expected:
                raise RuntimeError("Protected coordinator input changed")

    def _translate_paths(self, arguments):
        translated = dict(arguments)
        for key, value in arguments.items():
            if key.endswith("_path") and value is not None:
                if not isinstance(value, str) or not Path(value).is_absolute():
                    raise ValueError("Tool paths must be absolute staged input paths")
                path = str(Path(value).resolve())
                if path not in self.aliases:
                    raise PermissionError("Tool path is not an approved input")
                translated[key] = str(self.aliases[path])
        return translated

    def _backend_arguments(self, tool, arguments):
        translated = self._translate_paths(arguments)
        if tool == "validate_design":
            if "metadata_csv_path" in translated:
                raise ValueError("Use the shared participant argument metadata_path")
            translated["metadata_csv_path"] = translated.pop("metadata_path")
        return translated

    def _observed_admission(self, arguments):
        from .admission import inspect_admission
        allowed = {"metadata_path", "time_col", "biological_unit_column"}
        if set(arguments) - allowed:
            raise ValueError("Unknown observable-admission argument")
        if (arguments.get("time_col", self.policy.time_col) != self.policy.time_col
                or arguments.get("biological_unit_column", self.policy.biological_unit_column) != self.policy.biological_unit_column):
            raise ValueError("Admission columns must match the protected task contract")
        translated = self._translate_paths(arguments)
        return inspect_admission(translated["metadata_path"],
            time_col=self.policy.time_col, biological_unit_column=self.policy.biological_unit_column,
            unit_provenance_verified=self.policy.unit_provenance_verified, allow_data_execution=True)

    def _observed_summary(self, arguments):
        from .descriptive import summarize_observed_expression
        if set(arguments) - {"counts_path", "metadata_path", "time_col"}:
            raise ValueError("Observed summaries use the complete protected family and measurement contract")
        if self.policy.condition == "enforced":
            matching = [event for event in self.events if event["tool"] == "audit_quality"
                        and event["status"] == "succeeded"
                        and event.get("quality_key") == self._quality_key(arguments)]
            if not matching or self.results[matching[-1]["receipt_id"]].get("status") not in {"pass", "warning"}:
                raise ValueError("A matching quality audit is required before an observed summary")
        translated = self._translate_paths(arguments)
        return summarize_observed_expression(translated["counts_path"], translated["metadata_path"],
            gene_ids=self.policy.gene_ids, measurement_type=self.policy.measurement_type,
            time_col=self.policy.time_col, allow_data_execution=True)

    def _design_key(self, arguments):
        from .evidence_keys import evidence_key
        return evidence_key("design", self.policy.input_sha256,
                            {"time_col": self.policy.time_col, **arguments})

    def _quality_key(self, arguments):
        from .evidence_keys import evidence_key
        return evidence_key("quality", self.policy.input_sha256,
                            {"time_col": self.policy.time_col, **arguments})

    def _public_result(self, value):
        """Keep coordinator directory names out of participant-visible metadata."""
        if isinstance(value, dict):
            return {key: self._public_result(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self._public_result(item) for item in value]
        if isinstance(value, str):
            for public, private in self.aliases.items():
                value = value.replace(str(private), public)
                value = value.replace(str(private.parent), str(Path(public).parent))
        return value

    def _require_fit_prerequisites(self, arguments):
        if not self.policy.population_inference_allowed:
            raise ValueError("Population inference is unavailable for the declared biological units")
        admission = self._observed_admission({"metadata_path": arguments.get("metadata_path"),
            "time_col": arguments.get("time_col", self.policy.time_col)})
        if not admission["population_inference_allowed"]:
            raise ValueError("Observable sampling metadata blocks this fit: " + ", ".join(admission["blocking_diagnostics"]))
        criterion = arguments.get("selection_criterion")
        if criterion not in {"fixed", "aic", "bic", "gcv"} or not isinstance(arguments.get("spline_dfs"), list):
            raise ValueError("Fits require an explicit selection criterion and candidate dfs")
        expected_type = "counts" if self.policy.measurement_type == "raw_counts" else "continuous"
        if arguments.get("data_type") != expected_type:
            raise ValueError("Fit measurement type contradicts the protected input contract")
        if self.policy.condition == "enforced":
            # The study's adjusted temporal estimand uses a full categorical
            # batch term. Limit this acceptance path to an auditable model form;
            # ordinary-code exploration remains available in every condition.
            formula = re.sub(r"\s+", "", arguments.get("formula_str", ""))
            if not re.fullmatch(r"1\+(?:numeric_time|cr\(numeric_time,df=[1-9][0-9]?,constraints=(?:'center'|\"center\")\))\+C\(batch\)", formula):
                raise ValueError("Enforced temporal fitting requires 1 + numeric_time + C(batch), or a centered cr(numeric_time, df=N, constraints='center') term")
            if arguments.get("strict", True) is not True:
                raise ValueError("Required fitting cannot disable strict statistical failure handling")
            quality_key = self._quality_key(arguments)
            quality = [row for row in self.events if row["tool"] == "audit_quality"
                       and row["status"] == "succeeded" and row.get("quality_key") == quality_key]
            if not quality or self.results[quality[-1]["receipt_id"]].get("status") == "fail":
                raise ValueError("A successful applicable quality audit is required before fitting")
            key = self._design_key(arguments)
            design = [row for row in self.events if row["tool"] == "validate_design"
                      and row["status"] == "succeeded" and row.get("design_key") == key]
            if not design or not self.results[design[-1]["receipt_id"]].get("is_full_rank"):
                raise ValueError("A matching full-rank design check is required before fitting")

    def _fit_family(self, arguments):
        """Bounded chunks, common calibration panel and one declared BH family."""
        from devharness_server.normalization import bh_adjust
        self._require_fit_prerequisites(arguments)
        if "gene_list" in arguments:
            raise ValueError("fit_family uses the complete declared hypothesis family")
        estimates, chunks = {}, []
        for start in range(0, len(self.policy.gene_ids), 100):
            if time.monotonic() - self.started >= self.policy.timeout_sec:
                raise TimeoutError("Study deadline reached during family fitting")
            current = dict(arguments, gene_list=list(self.policy.gene_ids[start:start + 100]),
                           calibration_max_genes=len(self.policy.gene_ids))
            result = json.loads(self.dispatcher.execute("fit_temporal_trajectory", self._translate_paths(current)))
            if not isinstance(result.get("genes"), dict):
                raise ValueError("A fit chunk returned no gene estimates")
            if self.policy.measurement_type == "raw_counts":
                calibration = result.get("dispersion_calibration") or {}
                if set(calibration.get("gene_ids", [])) != set(self.policy.gene_ids):
                    raise ValueError("The bounded calibration panel cannot cover the complete study family")
                if chunks and result.get("size_factors") != chunks[0].get("size_factors"):
                    raise ValueError("Library normalization changed across family chunks")
            estimates.update(result["genes"])
            chunks.append({key: value for key, value in result.items() if key != "genes"})
        if set(estimates) != set(self.policy.gene_ids):
            raise ValueError("Fit chunks did not account for the declared family")
        raw_p = [estimates[gene].get("p_value") if estimates[gene].get("status") == "ok" else None
                 for gene in self.policy.gene_ids]
        adjusted = bh_adjust([p if p is not None else 1.0 for p in raw_p])
        for gene, p, q in zip(self.policy.gene_ids, raw_p, adjusted):
            estimates[gene]["family_q_value"] = float(q) if p is not None else None
        return {"genes": estimates, "chunks": chunks, "family_size": len(self.policy.gene_ids),
                "hypothesis_family": list(self.policy.gene_ids), "adjustment": "BH_full_declared_family",
                "calibration_panel_policy": "entire_bounded_input_family_per_chunk",
                "inference_warning": "Full-family adjustment does not establish calibration of the underlying tests"}

    def execute(self, tool, arguments):
        if self.closed:
            raise RuntimeError("Tool session is closed")
        if self.execution_ledger is None:
            return self._execute(tool, arguments)
        with self.execution_ledger.request_scope() as execution_id:
            return self._execute(tool, arguments, execution_id=execution_id)

    def _execute(self, tool, arguments, *, execution_id=None):
        from devharness_server.dispatcher import capture_tool_events
        from devharness_core.watchdog import worker_limits
        with self.lock:
            if self.closed or len(self.events) >= self.policy.max_calls:
                raise RuntimeError("Tool session is closed or its request budget is exhausted")
            if self.started is None:
                raise RuntimeError("Tool analysis window not started")
            if tool not in self.allowed_tools or not isinstance(arguments, dict):
                raise ValueError("Unknown tool or invalid arguments")
            arguments = json.loads(canonical_json_bytes(arguments))
            started = time.monotonic()
            event = {"sequence": len(self.events) + 1, "tool": tool, "arguments": arguments,
                     "input_sha256": self.policy.input_sha256,
                     "implementation_sha256": self.policy.implementation_sha256,
                     "started_at": datetime.now(timezone.utc).isoformat(),
                     "evidence_scope": "coordinator_tool_execution"}
            if self.execution_ledger is not None:
                event["execution_id"] = execution_id
            result, backend_events = None, []
            try:
                if tool in {"audit_quality", "validate_design", "fit_family", "fit_trajectory",
                            "inspect_study_design", "summarize_observed_expression", "analyze_batch_effects", "compact_manifold"}:
                    if arguments.get("time_col", self.policy.time_col) != self.policy.time_col:
                        raise ValueError("Time column must match the protected task contract")
                    arguments["time_col"] = self.policy.time_col
                if tool == "audit_quality" and self.policy.condition == "enforced" and arguments.get("thresholds") is not None:
                    raise ValueError("Required quality-audit thresholds cannot be changed by the participant")
                if tool == "audit_quality" and self.policy.condition == "enforced":
                    defaults = {"max_cells": 500, "top_genes": 200, "n_neighbors": 10, "seed": 0}
                    if any(arguments.get(key, value) != value for key, value in defaults.items()):
                        raise ValueError("Required quality-audit sampling settings are fixed")
                if started - self.started >= self.policy.timeout_sec:
                    raise TimeoutError("Study deadline exhausted")
                self._check_inputs()
                if tool in {"fit_family", "fit_trajectory"}:
                    self._require_fit_prerequisites(arguments)
                with worker_limits(deadline=self.started + self.policy.timeout_sec,
                                   max_mem_mb=self.policy.max_memory_mb,
                                   scratch_dir=self.scratch_dir), capture_tool_events() as backend_events:
                    result = self._invoke_tool(tool, arguments)
                self._check_inputs()
                if time.monotonic() - self.started >= self.policy.timeout_sec:
                    raise TimeoutError("Tool returned after the study deadline")
                result_bytes = canonical_json_bytes(result)
                if len(result_bytes) > MAX_RESULT_BYTES:
                    raise ValueError("Tool result exceeds its bounded evidence contract")
                event.update(status="succeeded", result_sha256=hashlib.sha256(result_bytes).hexdigest())
                if tool == "validate_design":
                    event["design_key"] = self._design_key(arguments)
                if tool == "audit_quality":
                    event["quality_key"] = self._quality_key(arguments)
            except Exception as error:
                event.update(status="failed", error=f"{type(error).__name__}: {error}"[:2000])
            event["elapsed_sec"] = time.monotonic() - started
            if backend_events:
                backend_bytes = canonical_json_bytes(backend_events)
                if len(backend_bytes) > 32_000_000:
                    event.update(status="failed", error="Backend evidence exceeded its aggregate bound")
                else:
                    backend_name = f"backend-{event['sequence']:04d}.json"
                    self._store(backend_name, backend_bytes)
                    event["backend_evidence"] = {"file": backend_name,
                        "sha256": hashlib.sha256(backend_bytes).hexdigest(), "calls": len(backend_events)}
            event["previous_receipt"] = self.events[-1]["receipt_id"] if self.events else None
            receipt_id = content_sha256(event)
            event["receipt_id"] = receipt_id
            self.events.append(event)
            self._store("events.jsonl", canonical_json_bytes(event) + b"\n", append=True)
            if event["status"] == "succeeded":
                self._store(f"{receipt_id}.json", result_bytes)
                self.results[receipt_id] = result
            return {"receipt_id": receipt_id, "status": event["status"], "result": self._public_result(result) if event["status"] == "succeeded" else None,
                    "error": self._public_result(event.get("error")), "input_sha256": self.policy.input_sha256}

    def close(self):
        self.closed = True
        if not self.lock.acquire(timeout=5):
            self.capture_error = "Tool handler did not stop within the coordinator cleanup deadline"
            return {"record_format": "devbench.study.tool-receipts/1",
                    "input_sha256": self.policy.input_sha256,
                    "implementation_sha256": self.policy.implementation_sha256,
                    "condition": self.policy.condition, "event_count": len(self.events),
                    "root_receipt": None, "capture_complete": False, "capture_error": self.capture_error,
                    "scope": "coordinator_tool_execution", "clinical_qualification": False}
        try:
            if self.final_receipt is not None:
                return dict(self.final_receipt)
            self.closed = True
            receipt = {"record_format": "devbench.study.tool-receipts/1",
                       "input_sha256": self.policy.input_sha256, "event_count": len(self.events),
                       "implementation_sha256": self.policy.implementation_sha256,
                       "condition": self.policy.condition,
                       "root_receipt": self.events[-1]["receipt_id"] if self.events else None,
                       "capture_complete": self.capture_error is None,
                       "capture_error": self.capture_error,
                       "scope": "coordinator_tool_execution",
                       "clinical_qualification": False}
            if self.execution_ledger is not None:
                executions, execution_error = self.execution_ledger.seal()
                encoded = canonical_json_bytes(executions)
                receipt.update(record_format="devbench.study.tool-receipts/2",
                    execution_manifest={"file": "executions.json", "count": len(executions),
                                        "sha256": hashlib.sha256(encoded).hexdigest()},
                    execution_scope="coordinator_observed_sequential_script_command_windows",
                    numerical_replay="out_of_scope")
                if execution_error:
                    receipt.update(capture_complete=False, capture_error=execution_error)
                if self.capture_error is None:
                    self._store("executions.json", encoded)
            if self.capture_error is None:
                self._store("root.json", canonical_json_bytes(receipt))
            self.final_receipt = receipt
            return receipt
        finally:
            self.lock.release()
