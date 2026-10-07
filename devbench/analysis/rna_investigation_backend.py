"""Packet-only investigators over the existing protected Codex selector.

Current native security and aggregate-token capabilities remain unqualified.
Offline protocol tests do not authorize native model/data execution.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import importlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import stat
import tempfile
from typing import Callable

from devharness_core.provenance import canonical_json
from .episode_codex import CodexActionSelector, _canonical, selection_contract, selection_instruction
from .methods import MethodService


def _digest(value):
    return hashlib.sha256(canonical_json(value)).hexdigest()


def _copy(value):
    return json.loads(canonical_json(value))


@dataclass(frozen=True)
class InvestigationExecutionAuthority:
    """Trusted runtime permission check; never saved in a packet or cursor."""
    check: Callable
    data_classification: str
    budget_policy: str

    def __post_init__(self):
        if not callable(self.check):
            raise TypeError("Current model authorization requires a callable runtime check")
        if self.data_classification not in {"synthetic", "public"}:
            raise PermissionError("Investigation data classification does not permit external models")
        if self.budget_policy not in {"hard_total_tokens", "bounded_observed_tokens"}:
            raise ValueError("Unknown investigation budget policy")

    def require(self, request):
        if self.check(_copy(request)) is not True:
            raise PermissionError("Investigation requires current model authorization")


class BoundedReadFacade:
    """Coordinator packet builder: exact immutable selectors, never child tools."""
    def __init__(self, resolver, *, allowed_selectors, workspace, private_roots=(), max_bytes=16_000):
        if not callable(resolver):
            raise TypeError("A trusted artifact resolver is required")
        self.resolver = resolver
        self.workspace = _canonical(workspace)
        self.private_roots = tuple(_canonical(root) for root in private_roots)
        if type(max_bytes) is not int or not 1 <= max_bytes <= 48_000:
            raise ValueError("Invalid bounded read byte limit")
        self.max_bytes = max_bytes
        self.allowed_selectors = _copy(allowed_selectors)

    def read(self, request):
        if not isinstance(request, dict) or canonical_json(request) not in [canonical_json(row) for row in self.allowed_selectors]:
            raise PermissionError("Unregistered bounded read selector")
        if set(request) not in ({"artifact_ref", "pointer", "max_bytes"},
                                 {"artifact_ref", "start_line", "end_line", "max_bytes"}):
            raise ValueError("Unsupported bounded read selector")
        maximum = request["max_bytes"]
        if type(maximum) is not int or not 1 <= maximum <= min(8000, self.max_bytes):
            raise ValueError("Read selector byte limit is invalid")
        ref = request["artifact_ref"]
        if (not isinstance(ref, dict) or set(ref) != {"artifact_id", "sha256", "artifact_type", "size_bytes"}
                or type(ref["size_bytes"]) is not int or not 0 <= ref["size_bytes"] <= 1_000_000):
            raise ValueError("Read artifact byte bound is invalid")
        path = Path(self.resolver(_copy(ref)))
        if (not path.is_absolute() or any(part.is_symlink() for part in (path, *path.parents))
                or path.resolve() != path or not path.is_relative_to(self.workspace)):
            raise PermissionError("Read artifact path is outside the nonsymlink workspace")
        if any(path.is_relative_to(root) for root in self.private_roots):
            raise PermissionError("Private artifact reads are denied")
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size != ref["size_bytes"]:
                raise ValueError("Read artifact byte size mismatch")
            content = stream.read(ref["size_bytes"] + 1)
        if len(content) != ref["size_bytes"] or hashlib.sha256(content).hexdigest() != ref["sha256"]:
            raise ValueError("Read artifact hash or byte size mismatch")
        if "pointer" in request:
            pointer = request["pointer"]
            if (not isinstance(pointer, str) or len(pointer) > 1000 or (pointer and not pointer.startswith("/"))
                    or any(part in {".", ".."} for part in pointer.split("/"))):
                raise ValueError("Invalid JSON read selector")
            from devbench.study.evidence import decode_json
            value = decode_json(content)
            for part in pointer[1:].split("/") if pointer else ():
                part = part.replace("~1", "/").replace("~0", "~")
                if isinstance(value, list) and part.isdecimal():
                    index = int(part)
                    if index >= len(value):
                        raise ValueError("Read selector is absent")
                    value = value[index]
                elif isinstance(value, dict) and part in value:
                    value = value[part]
                else:
                    raise ValueError("Read selector is absent")
            encoded = canonical_json(value)
        else:
            start, end = request["start_line"], request["end_line"]
            if type(start) is not int or type(end) is not int or not 1 <= start <= end or end - start >= 128:
                raise ValueError("Invalid bounded line selector")
            lines = content.decode("utf-8").splitlines(keepends=True)
            if end > len(lines):
                raise ValueError("Read line selector is absent")
            value = "".join(lines[start-1:end])
            encoded = value.encode("utf-8")
        if len(encoded) > maximum:
            raise ValueError("Read selector output exceeds its byte bound")
        return value


class AuditedInvestigationBackend:
    """Thin coordinator adapter; construction performs no dispatch."""
    def __init__(self, methods, selector, *, ledger=None):
        if not isinstance(methods, MethodService) or not isinstance(selector, CodexActionSelector):
            raise TypeError("Use MethodService and the protected CodexActionSelector")
        if selector.private_root is None or methods.root != selector.private_root:
            raise ValueError("Investigation capture and artifacts require the same protected root")
        self.methods, self.selector = methods, selector
        self.trace_observer = None
        self.ledger = None
        if ledger is not None:
            self.bind_ledger(ledger)

    def bind_ledger(self, ledger):
        from .workflow_operations import WorkflowOperationLedger
        if not isinstance(ledger, WorkflowOperationLedger) or not ledger.path.is_relative_to(self.methods.root):
            raise PermissionError("Use the existing same-workspace model ledger")
        if self.ledger is not None and self.ledger.path != ledger.path:
            raise PermissionError("Investigation model ledger binding cannot change")
        self.ledger = ledger
        return self

    def model_dispatch_gate(self, reservation):
        """Runtime-only reference to the current shared authority, never a grant."""
        from .workflow_operations import ModelCallReservation
        if self.ledger is None or not isinstance(reservation, ModelCallReservation):
            raise PermissionError("A bound shared ledger reservation is required")
        record = self.ledger.get_model_reservation(reservation)
        return {"format": "devbench.model-dispatch-gate/1", "ledger_path": str(self.ledger.path),
            "campaign_id": reservation.campaign_id, "node": reservation.node, "attempt": reservation.attempt,
            "reservation_ref": reservation.ref, "request_digest": reservation.request_digest,
            "policy_digest": reservation.policy_digest, "capability_digest": reservation.capability_digest,
            "context_digest": record["request"]["context_digest"]}

    def capabilities(self):
        modules = ("devbench.analysis.rna_trace_receipts", "devbench.analysis.rna_trace_contract", "devbench.analysis.rna_investigation_backend", "devbench.analysis.episode_codex",
            "devbench.analysis.rna_investigation_contract", "devbench.analysis.workflow_operations",
            "devbench.analysis.methods", "devbench.analysis.checkpoints",
            "devbench.analysis.langgraph_investigations", "devbench.analysis.rna_investigations",
            "devbench.analysis.workflow_actions", "devbench.analysis.workflow_state",
            "devbench.analysis.langgraph_workflow", "devbench.analysis.method_planner",
            "devbench.analysis.method_planner_contract", "devbench.analysis.method_authoring_contract",
            "devbench.analysis.rna_investigation_projection", "devbench.analysis.rna_input_contract",
            "devbench.analysis.method_validation", "devbench.analysis.scientific_judgment",
            "devbench.analysis.scientific_objects", "devbench_engine.adapters",
            "devbench.study.model_telemetry", "devbench.study.permissions", "devbench.study.adapter",
            "devbench.study.exec_preflight", "devbench.study.cli_identity",
            "devbench.adapters.code_action", "devbench.staging", "devharness_core.watchdog")
        origins = {name: str(_canonical(Path(importlib.import_module(name).__file__))) for name in modules}
        source_root = _canonical(Path(__file__).parents[2])
        if any(not Path(path).is_relative_to(source_root) for path in origins.values()):
            raise ValueError("Backend source module origin is outside the assigned workspace")
        paths = tuple(Path(path) for path in origins.values())
        sources = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
        executable = shutil.which(self.selector.executable)
        executable_path = Path(executable).resolve() if executable else None
        from devbench.study.cli_identity import codex_component_hashes
        component_hashes = codex_component_hashes(executable_path) if executable_path else {}
        source_digest = _digest({"module_origins": origins, "file_sha256": sources})
        runtime_digest = _digest({"python": sys.version, "executable": str(executable_path),
                                  "component_sha256": component_hashes})
        config = self.selector.configuration()
        value = {"format": "devbench.model-backend-capability/1", "backend": "codex-cli",
            "backend_digest": _digest({"configuration": config, "source_digest": source_digest,
                                       "runtime_digest": runtime_digest}),
            "runtime_digest": runtime_digest, "source_digest": source_digest,
            "scope": "native", "qualification": "unqualified",
            "capability_status": "source_inspected_not_multiagent_qualified",
            "enforced_total_token_cap": False, "usage_capture": "posthoc_optional",
            "verified_usage_capture": False, "context_bytes_enforced": True,
            "outer_timeout_enforced": True, "memory_enforced": True,
            "request_count_enforced": True, "all_attributable_requests_accounted": False,
            "parallel_boundary_qualified": False,
            "child_command_or_numerical_tools_allowed": False,
            "packet_only_reasoning": True, "direct_read_tools": False,
            "source_sha256": sources,
            "source_module_origins": origins,
            "runtime_component_sha256": component_hashes,
            "limitations": ["aggregate_token_ceiling_unavailable", "native_multi_child_security_unqualified",
                            "actor_workspace_writable", "runtime_read_grants_not_packet_only",
                            "tool_events_rejected_after_observation", "usage_posthoc_optional"]}
        value["capability_digest"] = _digest(value)
        return value

    def require_admission(self, policy):
        if policy not in {"hard_total_tokens", "bounded_observed_tokens"}:
            raise ValueError("Unknown investigation budget policy")
        record = self.capabilities()
        operational = ("context_bytes_enforced", "outer_timeout_enforced", "memory_enforced",
                       "request_count_enforced", "all_attributable_requests_accounted",
                       "parallel_boundary_qualified")
        if (record["capability_status"] not in {"offline_qualified", "native_qualified"}
                or record["usage_capture"] != "verified_complete"
                or any(record.get(key) is not True for key in operational)
                or record["child_command_or_numerical_tools_allowed"] is not False
                or (policy == "hard_total_tokens" and record["enforced_total_token_cap"] is not True)):
            raise PermissionError("blocked_backend_capability:" + policy)
        return record

    def _retain(self, value, artifact_type):
        """Use the existing immutable artifact authority, never a parallel store."""
        data = canonical_json(value)
        if len(data) > 64_000:
            raise ValueError("Investigation artifact exceeds its byte bound")
        with tempfile.NamedTemporaryFile(dir=self.methods.root, suffix=".json") as stream:
            stream.write(data)
            stream.flush()
            retained = self.methods.import_artifact(Path(stream.name), artifact_type=artifact_type,
                                                    actor="investigation_coordinator")
        return {key: retained[key] for key in ("artifact_id", "sha256", "artifact_type", "size_bytes")}

    def _artifact(self, ref, *, artifact_type=None, max_bytes=64_000):
        if (not isinstance(ref, dict) or set(ref) != {"artifact_id", "sha256", "artifact_type", "size_bytes"}
                or type(ref["size_bytes"]) is not int or not 0 <= ref["size_bytes"] <= max_bytes
                or (artifact_type is not None and ref["artifact_type"] != artifact_type)):
            raise ValueError("Invalid bounded investigation artifact reference")
        actual = self.methods.get("artifact", ref["artifact_id"])
        if any(actual.get(key) != ref[key] for key in ref):
            raise ValueError("Investigation artifact manifest binding mismatch")
        path = _canonical(self.methods.artifact_path(ref["artifact_id"]))
        if any(part.is_symlink() for part in (path, *path.parents)):
            raise ValueError("Investigation artifact symlink is forbidden")
        with path.open("rb") as stream:
            data = stream.read(max_bytes + 1)
        if len(data) != ref["size_bytes"] or hashlib.sha256(data).hexdigest() != ref["sha256"]:
            raise ValueError("Investigation artifact byte/hash binding mismatch")
        return data

    def _document(self, ref, *, artifact_type=None, max_bytes=64_000):
        from devbench.study.evidence import decode_json
        value = decode_json(self._artifact(ref, artifact_type=artifact_type, max_bytes=max_bytes))
        if not isinstance(value, dict):
            raise ValueError("Investigation artifacts must contain finite JSON objects")
        return value

    def retain_packet(self, packet):
        from .rna_investigation_contract import validate_investigation_packet
        validate_investigation_packet(packet)
        return self._retain(packet, "rna_investigation_packet")

    def read_packet(self, ref):
        from .rna_investigation_contract import validate_investigation_packet
        packet = self._document(ref, artifact_type="rna_investigation_packet", max_bytes=48_000)
        return validate_investigation_packet(packet)

    def read_result(self, ref):
        return self._document(ref, artifact_type="rna_investigation_result", max_bytes=24_000)

    def parent_selection_binding(self, selector, context, *, parent_session_id, session_id, invocation_id):
        configured = selector.configuration() if isinstance(selector, CodexActionSelector) else {}
        expected = self.selector.configuration()
        configured_purpose = configured.pop("purpose", getattr(selector, "purpose", None))
        expected.pop("purpose", None)
        if (not isinstance(selector, CodexActionSelector)
                or configured != expected
                or (configured_purpose is not None and configured_purpose != context.get("decision_kind"))
                or selector.private_root != self.selector.private_root):
            raise PermissionError("Investigation campaign backbone configuration changed")
        for identity in (parent_session_id, session_id, invocation_id):
            if not isinstance(identity, str) or re.fullmatch(r"[A-Za-z0-9_-]{1,128}", identity) is None:
                raise ValueError("Invalid bounded selection identity")
        schema, _ = selection_contract(context)
        prompt = selection_instruction(context)
        capability = self.capabilities()
        return {"parent_session_id": parent_session_id, "session_id": session_id,
                "invocation_id": invocation_id, "backend_digest": capability["backend_digest"],
                "runtime_digest": capability["runtime_digest"], "source_digest": capability["source_digest"],
                "prompt_digest": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
                "schema_digest": _digest(schema), "context_digest": _digest(context),
                "purpose": context.get("decision_kind", "episode_action")}

    def parent_call_limits(self, context, campaign, planner_limits):
        allocation = campaign.get("identity", {}).get("parent_call_limits", {})
        limits = allocation.get(context.get("decision_kind"), allocation.get("default"))
        if not isinstance(limits, dict) or set(limits) != {"tokens", "calls", "seconds", "context_bytes", "memory_mb"}:
            raise PermissionError("Parent call lacks frozen campaign allocation")
        result = _copy(limits)
        result["seconds"] = min(result["seconds"], planner_limits["session_timeout_seconds"])
        result["context_bytes"] = min(result["context_bytes"], planner_limits["max_context_bytes"])
        result["memory_mb"] = min(result["memory_mb"], planner_limits["memory_mb"], self.selector.memory_mb)
        if result["calls"] < 2:
            raise PermissionError("Parent call allocation must include permission preflight and action")
        schema_path = self.selector.workspace_parent / ("a-" + "0"*10) / "answer_schema.json"
        if len(selection_instruction(context, schema_path=schema_path).encode()) > result["context_bytes"]:
            raise PermissionError("Parent context/schema/wrapper exceeds frozen campaign byte bound")
        return result

    def prepare_requests(self, packet_refs, *, budget_policy, reservation_refs):
        from .rna_investigation_contract import investigation_context, validate_investigation_request
        if not 1 <= len(packet_refs) <= 2 or len(packet_refs) != len(reservation_refs):
            raise ValueError("Prepare one or two exact investigative requests")
        packets = [self.read_packet(ref) for ref in packet_refs]
        ids = [packet["investigation_id"] for packet in packets]
        if len(set(ids)) != len(ids) or len({packet["parent_session_id"] for packet in packets}) != 1:
            raise ValueError("Children require independent IDs and one parent")
        capability = self.capabilities()
        roots = [self._child_root(identity) for identity in ids]
        if len(set(roots)) != len(roots):
            raise ValueError("Compact child runtime identities collided")
        for root in roots:
            _canonical(root).mkdir(mode=0o700, parents=True, exist_ok=True)
        prepared = []
        for packet, packet_ref, reservation_ref, root in zip(packets, packet_refs, reservation_refs, roots):
            if (packet["bindings"]["runtime_digest"] != capability["runtime_digest"]
                    or packet["bindings"]["source_digest"] != capability["source_digest"]):
                raise ValueError("Investigation packet runtime/source binding changed")
            context = investigation_context(packet)
            invocation_id = _digest({"packet_ref": packet_ref, "reservation_ref": reservation_ref})[:32]
            binding = self.parent_selection_binding(self.selector, context,
                parent_session_id=packet["parent_session_id"], session_id=packet["investigation_id"],
                invocation_id=invocation_id)
            if packet["return_schema_digest"] != binding["schema_digest"]:
                raise ValueError("Investigation return schema changed")
            request = {"format": "devbench.rna-investigation-request/1",
                "investigation_id": packet["investigation_id"], "parent_session_id": packet["parent_session_id"],
                "stage": packet["stage"], "packet_ref": packet_ref,
                "backend_digest": capability["backend_digest"], "runtime_digest": capability["runtime_digest"],
                "budget_policy": budget_policy, "reservation_ref": reservation_ref}
            validate_investigation_request(request, packet=packet)
            binding["investigation_request_digest"] = _digest(request)
            binding["packet_ref"] = _copy(packet_ref)
            binding["request"] = _copy(request)
            descriptor = {"format": "devbench.rna-investigation-invocation/1", "request": request,
                "binding": binding, "child_roots": ids,
                "selector_configuration": self.selector.configuration()}
            invocation_ref = self._retain(descriptor, "json")
            identity = _digest(binding)
            with self.methods._db() as database:
                row = database.execute("SELECT 1 FROM records WHERE kind='rna_invocation_index' AND id=?", (identity,)).fetchone()
                if row is None:
                    self.methods._put(database, "rna_invocation_index", identity, {"invocation_ref": invocation_ref})
                elif self.methods._get(database, "rna_invocation_index", identity) != {"invocation_ref": invocation_ref}:
                    raise ValueError("Investigative invocation index changed")
            prepared.append({"request": request, "binding": binding, "invocation_ref": invocation_ref})
        return prepared

    def _invocation(self, invocation_ref):
        value = self._document(invocation_ref, artifact_type="json")
        if set(value) != {"format", "request", "binding", "child_roots", "selector_configuration"} or value["format"] != "devbench.rna-investigation-invocation/1":
            raise ValueError("Invalid retained investigative invocation")
        if value["selector_configuration"] != self.selector.configuration():
            raise ValueError("Retained investigative backbone changed")
        packet = self.read_packet(value["request"]["packet_ref"])
        from .rna_investigation_contract import investigation_context, validate_investigation_request
        validate_investigation_request(value["request"], packet=packet)
        context = investigation_context(packet)
        expected = self.parent_selection_binding(self.selector, context,
            parent_session_id=packet["parent_session_id"], session_id=packet["investigation_id"],
            invocation_id=value["binding"]["invocation_id"])
        expected.update(investigation_request_digest=_digest(value["request"]),
                        packet_ref=value["request"]["packet_ref"], request=value["request"])
        if expected != value["binding"]:
            raise ValueError("Retained investigative request/context binding changed")
        return value, packet, context

    def read_dispatch(self, invocation_ref):
        descriptor, _, _ = self._invocation(invocation_ref)
        return _copy(descriptor)

    def _output(self, invocation_id):
        if not isinstance(invocation_id, str) or re.fullmatch(r"[A-Za-z0-9_-]{1,128}", invocation_id) is None:
            raise ValueError("Invalid capture invocation identity")
        return _canonical(self.methods.root / "investigation-captures" / invocation_id)

    def _child_root(self, identity):
        if not isinstance(identity, str) or re.fullmatch(r"[a-f0-9]{32}", identity) is None:
            raise ValueError("Invalid child runtime identity")
        # Existing native negative controls need short Unix-socket paths. IDs
        # remain fully bound in artifacts; compact paths are not isolation proof.
        return _canonical(self.selector.workspace_parent / ("c" + hashlib.sha256(identity.encode()).hexdigest()[:4]))

    def investigate(self, request, *, execution_authority, reservation):
        """Exactly one reserved selector invocation; no repairs or tool loops."""
        from .workflow_operations import ModelCallReservation
        original_request = request
        request = _copy(request)
        if not isinstance(request, dict):
            raise ValueError("Use an exact JSON investigation request")
        if not isinstance(execution_authority, InvestigationExecutionAuthority):
            raise PermissionError("A trusted runtime investigation authority is required")
        self.require_admission(request.get("budget_policy"))  # Before all preflight/provider calls.
        if execution_authority.budget_policy != request.get("budget_policy"):
            raise PermissionError("Runtime authority budget policy changed")
        execution_authority.require(request)
        if _copy(original_request) != request:
            raise ValueError("Investigation request changed after admission")
        if not isinstance(reservation, ModelCallReservation):
            raise PermissionError("A retained model reservation is required")
        if self.ledger is None:
            raise PermissionError("Investigation requires the existing shared model ledger")
        retained = self.ledger.get_model_reservation(reservation)
        if retained["status"] != "active" or reservation.ref != request.get("reservation_ref"):
            raise PermissionError("Investigation requires an activated exact reservation")
        binding = reservation.request
        if binding.get("request") != request or binding.get("investigation_request_digest") != _digest(request):
            raise ValueError("Reserved investigation request bytes changed")
        packet = self.read_packet(request["packet_ref"])
        if packet["scope"]["data_classification"] != execution_authority.data_classification:
            raise PermissionError("Current authority and packet data classification differ")
        if packet["bindings"]["policy_digest"] != reservation.policy_digest:
            raise PermissionError("Investigation packet policy binding changed")
        from .rna_investigation_contract import investigation_context, validate_investigation_result
        context = investigation_context(packet)
        root = self._child_root(packet["investigation_id"])
        # All sibling roots were allocated before the first launch. The exact
        # bound invocation descriptor records them; none are conversational state.
        invocation_ref = self._find_invocation(binding)
        descriptor, _, _ = self._invocation(invocation_ref)
        denied = tuple(self._child_root(identity)
                       for identity in descriptor["child_roots"] if identity != packet["investigation_id"])
        child_selector = CodexActionSelector(requested_model=self.selector.requested_model,
            workspace_parent=root, private_root=self.selector.private_root,
            executable=self.selector.executable, reasoning_effort=self.selector.reasoning_effort,
            memory_mb=min(self.selector.memory_mb, reservation.limits["memory_mb"]),
            max_context_bytes=reservation.limits["context_bytes"],
            denied_roots=(*self.selector.denied_roots, *self.selector.workspaces, *denied))
        limits = reservation.limits
        if len(str(root / ("a-" + "0"*10) / "control" / ("u-" + "0"*8)).encode()) > 100:
            raise PermissionError("Investigation actor parent exceeds the existing protected preflight path bound")
        schema_path = root / ("a-" + "0"*10) / "answer_schema.json"
        prompt = selection_instruction(context, schema_path=schema_path)
        if len(prompt.encode("utf-8")) > limits["context_bytes"] or limits["calls"] < 2:
            raise PermissionError("Reserved investigation context/call budget is insufficient")
        output = self._output(binding["invocation_id"])
        if output.exists():
            return self.reconcile_capture(invocation_ref)
        output.mkdir(mode=0o700, parents=True)
        from .checkpoints import atomic_json
        atomic_json(output / "invocation.json", descriptor)
        execution_authority.require(request)
        if _copy(original_request) != request:
            raise ValueError("Investigation request changed after admission")
        remaining = self.ledger.model_call_remaining(reservation)
        if remaining <= 0:
            raise PermissionError("Investigation campaign absolute deadline exhausted")
        trace_transport = {}
        if self.trace_observer is not None:
            reference = self.trace_observer.prepare_invocation(packet["parent_session_id"], binding,
                reservation, role="investigator")
            if reference is not None:
                trace_transport["trace_sidecar"] = reference
        failure = None
        try:
            child_selector.select(context, output, timeout_seconds=min(300, limits["seconds"], remaining),
                memory_mb=limits["memory_mb"], protected_model_gate=self.model_dispatch_gate(reservation),
                allow_model_execution=True, **trace_transport)
        except BaseException as error:
            failure = {"type": type(error).__name__, "message": str(error)[:2000]}
        capture_ref, usage_ref = None, None
        try:
            capture_ref, receipt = self.verify_selection_capture(output, bindings=binding, reservation=reservation)
            usage_ref = self.retain_verified_usage(capture_ref, self.verify_capture(capture_ref, reservation))
            if receipt["status"] == "completed":
                action = self._capture_action(capture_ref)
                validate_investigation_result(action, packet=packet, transport_pending=True)
                if action["capture_ref"] is not None or action["usage_ref"] is not None:
                    raise ValueError("Child cannot grant itself transport authority")
                result = {**action, "capture_ref": capture_ref, "usage_ref": usage_ref}
                validate_investigation_result(result, packet=packet)
                result_ref = self._retain(result, "rna_investigation_result")
            else:
                result_ref = None
            outcome = {"status": "completed" if result_ref else "incomplete",
                "invocation_ref": invocation_ref, "result_ref": result_ref,
                "capture_ref": capture_ref, "usage_ref": usage_ref, "error": failure}
        except (OSError, ValueError, RuntimeError) as error:
            outcome = {"status": "incomplete" if capture_ref else "uncertain", "invocation_ref": invocation_ref,
                "result_ref": None, "capture_ref": capture_ref, "usage_ref": usage_ref,
                "error": failure or {"type": type(error).__name__, "message": str(error)[:2000]}}
        atomic_json(output / "completion.json", outcome)
        if self.trace_observer is not None:
            self.trace_observer.complete_invocation(trace_transport.get("trace_sidecar"), reservation=reservation,
                status=outcome["status"],capture_verified=capture_ref is not None)
        return outcome

    def _find_invocation(self, binding):
        # The small index lives in the existing service and grants no authority;
        # every use revalidates the actual immutable artifact and exact binding.
        record = self.methods.get("rna_invocation_index", _digest(binding))
        descriptor, _, _ = self._invocation(record["invocation_ref"])
        if descriptor["binding"] != binding:
            raise ValueError("Investigative invocation index binding changed")
        return record["invocation_ref"]

    def verify_selection_capture(self, output, *, bindings, reservation):
        """Retain real selector files unchanged, then independently verify them."""
        output = _canonical(output)
        if not output.is_relative_to(self.methods.root):
            raise PermissionError("Selection capture must remain coordinator-private")
        if reservation.request != bindings or reservation.request_digest != _digest(bindings):
            raise ValueError("Capture reservation/request binding changed")
        names = ("evidence.json", "phase-timing.json", "boundary-probe.json", "instruction.json",
            "selection-dispatch.json", "exec-preflight/reservation.json", "exec-preflight/dispatch_checkpoint.json",
            "exec-preflight/probe.py", "exec-preflight/probe_schema.json", "exec-preflight/model_identity.json",
            "exec-preflight/launch_configuration.json", "exec-preflight/instruction.txt",
            "exec-preflight/cli_records.jsonl", "exec-preflight/evidence.json", "exec-preflight/stderr.txt",
            "model/metadata.json", "model/answer.json", "model/cli-records.jsonl",
            "model/model-identity.json", "model/stream-timing.json")
        files = {}
        for name in names:
            path = _canonical(output / name)
            if path.is_file():
                if path.stat().st_size > 4_000_000 or any(part.is_symlink() for part in (path, *path.parents)):
                    raise ValueError("Capture file violates its nonsymlink byte bound")
                value = self.methods.import_artifact(path, artifact_type="file", actor="investigation_capture")
                files[name] = {key: value[key] for key in ("artifact_id", "sha256", "artifact_type", "size_bytes")}
        from devbench.study.evidence import decode_json
        if "evidence.json" not in files:
            raise ValueError("No actual selector evidence was retained")
        details = decode_json(self._artifact(files["evidence.json"], max_bytes=4_000_000))
        if details.get("workspace"):
            actor = _canonical(details["workspace"])
            if not actor.is_relative_to(self.selector.workspace_parent):
                raise ValueError("Captured actor workspace was not allocated by this backbone")
            for name, path in (("actor/context.json", actor / "control/context.json"),
                               ("actor/answer_schema.json", actor / "answer_schema.json")):
                _canonical(path)
                if path.is_file() and path.stat().st_size <= 1_000_000:
                    artifact = self.methods.import_artifact(path, artifact_type="file", actor="investigation_capture")
                    files[name] = {key: artifact[key] for key in ("artifact_id", "sha256", "artifact_type", "size_bytes")}
        response_digest = None
        if "model/answer.json" in files:
            try:
                response_digest = _digest(decode_json(self._artifact(files["model/answer.json"], max_bytes=4_000_000)))
            except (ValueError, TypeError):
                pass
        manifest = {"format": "devbench.rna-investigation-capture/1", "binding": _copy(bindings),
            "reservation_ref": reservation.ref, "reservation_request_digest": reservation.request_digest,
            "policy_digest": reservation.policy_digest, "capability_digest": reservation.capability_digest,
            "capability": self.capabilities(), "files": files, "response_digest": response_digest}
        capture_ref = self._retain(manifest, "rna_investigation_capture")
        receipt = self.verify_capture(capture_ref, reservation)
        return capture_ref, {**receipt, "response_digest": response_digest}

    def retain_verified_usage(self, capture_ref, receipt):
        actual = self.verify_capture(capture_ref)
        supplied = {key: value for key, value in receipt.items() if key != "response_digest"}
        if supplied != actual:
            raise ValueError("Usage receipt differs from exact retained capture")
        return self._retain({"format": "devbench.rna-investigation-usage/1",
                            "capture_ref": capture_ref, "receipt": actual}, "rna_investigation_usage")

    def _capture_action(self, capture_ref):
        manifest = self._document(capture_ref, artifact_type="rna_investigation_capture")
        from devbench.study.evidence import decode_json
        return decode_json(self._artifact(manifest["files"]["model/answer.json"], max_bytes=4_000_000))

    def verify_capture(self, capture_ref, reservation=None):
        """Exact retained CLI/runtime/usage verification; never launch or write."""
        from devbench.study.evidence import decode_json
        from devbench.study.model_telemetry import normalize_usage, correlate_usage, completion_model, COMPLETION_EVENTS
        from devbench.study.cli_identity import verify_codex_identity
        from devbench.study.exec_preflight import _verify_records, _BASE_CHECKS, _PROBE_SOURCE
        import ast
        import shlex

        manifest = self._document(capture_ref, artifact_type="rna_investigation_capture")
        if set(manifest) != {"format", "binding", "reservation_ref", "reservation_request_digest", "policy_digest", "capability_digest", "capability", "files", "response_digest"} or manifest["format"] != "devbench.rna-investigation-capture/1":
            raise ValueError("Invalid immutable native capture manifest")
        binding = manifest["binding"]
        current = self.capabilities()
        if manifest["capability"] != current or any(binding.get(key) != current[key] for key in ("backend_digest", "runtime_digest", "source_digest")):
            raise ValueError("Capture source/backend/runtime identity is stale")
        if manifest["reservation_request_digest"] != _digest(binding):
            raise ValueError("Capture request digest is forged")
        if reservation is not None and (reservation.ref != manifest["reservation_ref"]
                or reservation.request != binding or reservation.request_digest != manifest["reservation_request_digest"]):
            raise ValueError("Capture belongs to a different exact reservation")
        if self.ledger is None:
            raise PermissionError("Actual capture verification requires the bound shared ledger")
        retained = self.ledger.get_model_reservation(manifest["reservation_ref"])
        campaign = self.ledger.get_campaign(retained["campaign_id"])
        if (retained["request"] != binding or retained["request_digest"] != manifest["reservation_request_digest"]
                or campaign["policy_digest"] != manifest["policy_digest"]
                or campaign["capability_digest"] != manifest["capability_digest"]):
            raise ValueError("Capture model campaign/reservation binding changed")
        raw = {name: self._artifact(ref, artifact_type="file", max_bytes=4_000_000)
               for name, ref in manifest["files"].items()}
        documents = {name: decode_json(data) for name, data in raw.items() if name.endswith(".json") and name != "model/answer.json"}
        details = documents.get("evidence.json", {})
        preflight = documents.get("exec-preflight/evidence.json", {})
        selection = documents.get("model/metadata.json", {})
        if details.get("preflight") != (preflight or None) or (selection and details.get("selection") != selection):
            raise ValueError("Selector evidence does not match actual retained phase metadata")
        if (not selection and details.get("selection")
                and ("selection-dispatch.json" in raw or details["selection"].get("inference_dispatch_attempted") is True)):
            raise ValueError("Claimed selection launch lacks actual retained metadata")
        if details.get("codex_identity"):
            verify_codex_identity(details["codex_identity"])
        if "actor/context.json" in documents and _digest(documents["actor/context.json"]) != binding["context_digest"]:
            raise ValueError("Capture context bytes differ from the reserved request")
        if "actor/answer_schema.json" in documents and _digest(documents["actor/answer_schema.json"]) != binding["schema_digest"]:
            raise ValueError("Capture schema bytes differ from the reserved request")
        if "instruction.json" in documents:
            instruction = documents["instruction.json"].get("instruction")
            if not isinstance(instruction, str) or hashlib.sha256(instruction.encode()).hexdigest() != binding["prompt_digest"]:
                raise ValueError("Capture prompt bytes differ from the reserved request")
        else:
            instruction = None

        def records(name):
            return [decode_json(line) for line in raw.get(name, b"").splitlines() if line]

        action_records = records("model/cli-records.jsonl")
        action_sessions = [row.get("thread_id") for row in action_records if row.get("type") == "thread.started"]
        preflight_records = records("exec-preflight/cli_records.jsonl")
        preflight_sessions = [row.get("thread_id") for row in preflight_records if row.get("type") == "thread.started"]
        native_ids = preflight_sessions + action_sessions
        if len(set(native_ids)) != len(native_ids) or any(not isinstance(value, str) or not value for value in native_ids):
            raise ValueError("Capture reuses or omits a native session identity")
        calls = int("exec-preflight/dispatch_checkpoint.json" in raw) + int("selection-dispatch.json" in raw)
        if calls != len(native_ids):
            raise ValueError("Native launch/capture remains uncertain")

        usage_items = []
        for phase, metadata, native_name in (("preflight", preflight, "exec-preflight/model_identity.json"),
                                            ("action", selection, "model/model-identity.json")):
            attempted = "exec-preflight/dispatch_checkpoint.json" in raw if phase == "preflight" else "selection-dispatch.json" in raw
            if not attempted:
                continue
            usage = metadata.get("billing", {}).get("usage") if phase == "preflight" else (
                metadata.get("turns", [{}])[-1].get("usage") if metadata.get("turns") else None)
            native = documents.get(native_name)
            if native is not None and native != metadata.get("native_model_identity"):
                raise ValueError("Retained native telemetry differs from phase metadata")
            matching = [row for row in (native or {}).get("events", []) if row.get("completion") is True
                        and row.get("event") in COMPLETION_EVENTS]
            valid = (native is not None and native.get("capture_complete") is True
                and native.get("usage_matches_cli") is True and metadata.get("event_capture_complete") is True
                and correlate_usage(matching, usage)["matches"]
                and completion_model((native or {}).get("events", []), metadata.get("session_id")) == metadata.get("actual_model"))
            if valid:
                usage_items.append(normalize_usage(usage))
            else:
                # Preserve an observed primary-count floor only when the native
                # capture supports that CLI count; ambiguity never becomes an
                # exact total. Cached/reasoning subsets add nothing to the floor.
                observed = {}
                if (native is not None and native.get("capture_complete") is True
                        and metadata.get("event_capture_complete") is True
                        and native.get("conversation_id") == metadata.get("session_id")):
                    for key in ("input_tokens", "output_tokens"):
                        value = (usage or {}).get(key)
                        values = [row.get("usage", {}).get(key) for row in matching]
                        if (type(value) is int and value >= 0 and values
                                and all(type(item) is int and item >= 0 for item in values)
                                and sum(values) >= value):
                            observed[key] = value
                usage_items.append(normalize_usage({**observed, "exact_accounting_available": False}))
        # Unknown/invalid phase accounting can never become a known aggregate
        # by summing only its surviving component counts.
        usage = normalize_usage({"phase_usage": [item["native_usage"] for item in usage_items]}) if calls else normalize_usage({"input_tokens": 0, "output_tokens": 0})

        succeeded = (calls == 2 and preflight.get("verified") is True
            and selection.get("response_validation_status") == "validated"
            and selection.get("model_identity_verified") is True
            and selection.get("actual_model") == preflight.get("actual_model")
            and selection.get("returncode") == 0 and selection.get("event_capture_complete") is True
            and documents.get("boundary-probe.json", {}).get("verified") is True
            and len(action_sessions) == 1 and len(preflight_sessions) == 1
            and all(name in documents for name in ("actor/context.json", "actor/answer_schema.json", "instruction.json")))
        if succeeded:
            if (not action_records or action_records[0].get("type") != "thread.started"
                    or action_records[-1].get("type") != "turn.completed"
                    or sum(row.get("type") == "turn.completed" for row in action_records) != 1
                    or any(row.get("type") in {"error", "turn.failed"} for row in action_records)
                    or any(row.get("item", {}).get("type") not in {"reasoning", "agent_message"}
                           for row in action_records if row.get("type") in {"item.started", "item.updated", "item.completed"})):
                raise ValueError("Protected investigation observed commands, tools or invalid completion")
            command = selection.get("cli_command", [])
            if any(flag not in command for flag in ("--ephemeral", "--ignore-user-config", "--ignore-rules", "--strict-config")):
                raise ValueError("Protected capture omitted independent session controls")
            expected_prompt = instruction
            if "--output-schema" not in command:
                schema_path = str(Path(details["workspace"]) / "answer_schema.json")
                from devbench_engine.adapters import local_schema_prompt
                expected_prompt = local_schema_prompt(instruction, schema_path)
            if selection.get("provider_instruction_sha256") != hashlib.sha256(expected_prompt.encode()).hexdigest():
                raise ValueError("Actual native prompt hash differs from the bound wrapper")
            probe_source = raw.get("exec-preflight/probe.py", b"").decode("utf-8")
            config_line, _, body = probe_source.partition("\n")
            if not config_line.startswith("CONFIG = ") or body != _PROBE_SOURCE:
                raise ValueError("Retained permission probe source changed")
            config = ast.literal_eval(config_line[len("CONFIG = "):])
            invocation = shlex.split(preflight["expected_command"])
            if len(invocation) != 3 or invocation[1] != "-I":
                raise ValueError("Retained permission probe command changed")
            report = _verify_records(preflight_records, Path(invocation[0]), Path(invocation[2]),
                                     config["nonce"], _BASE_CHECKS | {"tool_endpoint_absent"})
            if report != preflight.get("probe_report"):
                raise ValueError("Retained permission negative control is forged")
            action = decode_json(raw["model/answer.json"])
            from devbench_engine.adapters import _validate_schema
            _validate_schema(action, documents["actor/answer_schema.json"])
            if _digest(action) != manifest["response_digest"]:
                raise ValueError("Retained actual response binding changed")
        elapsed = details.get("phase_timing", {}).get("elapsed_sec", details.get("duration_sec"))
        if type(elapsed) not in {int, float} or not 0 <= elapsed <= 3600:
            raise ValueError("Actual captured selection duration is unavailable")
        return {"format": "devbench.model-call-capture/1", "reservation_ref": manifest["reservation_ref"],
            "request_digest": manifest["reservation_request_digest"],
            **{key: binding[key] for key in ("session_id", "invocation_id", "backend_digest", "runtime_digest",
                                            "prompt_digest", "schema_digest", "context_digest")},
            "status": "completed" if succeeded else "failed", "capture_ref": _copy(capture_ref),
            "usage": usage, "actual_calls": calls, "elapsed_seconds": elapsed,
            "launch_confirmed": bool(calls), "native_session_ids": native_ids}

    def reconcile_capture(self, invocation_ref):
        """Read only; ambiguous launch is never automatically repeated."""
        descriptor, _, _ = self._invocation(invocation_ref)
        output = self._output(descriptor["binding"]["invocation_id"])
        marker = output / "completion.json"
        if not marker.is_file() or marker.is_symlink() or marker.stat().st_size > 64_000:
            if self.ledger is not None:
                retained = self.ledger.get_model_reservation(descriptor["request"]["reservation_ref"])
                if retained and retained.get("capture"):
                    capture_ref = retained["capture"]["capture_ref"]
                    receipt = self.verify_capture(capture_ref)
                    if receipt != retained["capture"] or receipt["invocation_id"] != descriptor["binding"]["invocation_id"]:
                        raise ValueError("Retained ledger capture differs from this invocation")
                    return {"status": "incomplete", "invocation_ref": _copy(invocation_ref),
                        "result_ref": None, "capture_ref": _copy(capture_ref), "usage_ref": None,
                        "error": {"type": "ResultCommitUnavailable", "message": "Verified capture retained; normalized result unavailable; no redispatch"}}
            return {"status": "uncertain", "invocation_ref": _copy(invocation_ref),
                    "result_ref": None, "capture_ref": None, "usage_ref": None,
                    "error": {"type": "UncertainInvocation", "message": "No exact retained completion; no redispatch"}}
        from devbench.study.evidence import decode_json
        outcome = decode_json(marker.read_bytes())
        if outcome.get("invocation_ref") != invocation_ref:
            raise ValueError("Retained completion invocation binding changed")
        if outcome.get("capture_ref"):
            receipt = self.verify_capture(outcome["capture_ref"])
            if receipt["invocation_id"] != descriptor["binding"]["invocation_id"]:
                raise ValueError("Retained completion capture binding changed")
        if outcome.get("result_ref"):
            self.verify_child_result(descriptor["request"]["packet_ref"], outcome["result_ref"])
        elif outcome.get("status") == "completed":
            raise ValueError("Retained completion lacks a result")
        return _copy(outcome)

    def verify_child_result(self, packet_ref, result_ref):
        from .rna_investigation_contract import validate_investigation_result
        packet, result = self.read_packet(packet_ref), self.read_result(result_ref)
        validate_investigation_result(result, packet=packet)
        receipt = self.verify_capture(result["capture_ref"])
        capture = self._document(result["capture_ref"], artifact_type="rna_investigation_capture")
        if (receipt["status"] != "completed" or capture["binding"].get("packet_ref") != packet_ref
                or capture["response_digest"] != _digest({**result, "capture_ref": None, "usage_ref": None})):
            raise ValueError("Child result does not match its actual packet/response capture")
        usage = self._document(result["usage_ref"], artifact_type="rna_investigation_usage", max_bytes=24_000)
        if usage != {"format": "devbench.rna-investigation-usage/1", "capture_ref": result["capture_ref"], "receipt": receipt}:
            raise ValueError("Child usage does not match actual capture")
        return result

    verify_child = verify_child_result

    def verify_incomplete_child(self, packet_ref, dispatch_ref):
        descriptor, packet, _ = self._invocation(dispatch_ref)
        if descriptor["request"]["packet_ref"] != packet_ref:
            raise ValueError("Incomplete child packet binding changed")
        outcome = self.reconcile_capture(dispatch_ref)
        if outcome["status"] == "completed":
            raise ValueError("Complete child cannot be labeled missing")
        return {"investigation_id": packet["investigation_id"], "status": outcome["status"],
                "capture_ref": outcome["capture_ref"], "error": outcome["error"]}
