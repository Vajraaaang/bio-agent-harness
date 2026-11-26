"""Custody and graph dispatch for registered native Plugin operations."""

from __future__ import annotations

import json
import os
import platform
import re
import sys
import time
from importlib.util import find_spec
from pathlib import Path
from tempfile import NamedTemporaryFile

from devbench.core._files import hash_bounded_file, read_bounded_file
from devbench.core._hashing import decode_receipt_json
from devbench.core.procedure_profile import OperationCapability, ProcedureProfile
from devbench.engine.workflow_operations import OperationBlocked, OperationConflict

from .adaptive_workflow import AdaptiveWorkflow, request_digest
from .adaptive_workflow_state import (
    AdaptiveStep,
    ArtifactVersion,
    InputLink,
    StepReceipt,
)
from .plugin_jobs import MAX_SECONDS, JobStore, canonical, digest

_ID = re.compile(r"[a-f0-9]{32}")
_SHA = re.compile(r"[a-f0-9]{64}")
_REASON = re.compile(r"[A-Za-z0-9_.:-]{1,128}")
_PROFILE = "plugin-native-operation"
_FORMAT = "devbench.plugin-native-custody/1"
_RESULT = "plugin-native-result-json-v1"
_MAX_RECEIPT = 1024**2
_MAX_ARTIFACT = 256 * 1024**2
_BASE_MODULES = (
    "devbench.platform.adaptive_workflow",
    "devbench.platform.adaptive_workflow_state",
    "devbench.platform.plugin_graph",
    "devbench.platform.plugin_jobs",
    "devbench.platform.plugin_mcp",
)
_OP_MODULES = {
    "matrix_audit": (
        "devbench.platform.workbench_matrix_audit",
        "devbench.platform.workbench_matrix_audit_plot",
    ),
    "qc-salmon": (
        "devbench.analysis.fastq_execution",
        "devbench.analysis.fastq_contract",
    ),
    "shared_motor_umap": (
        "devbench.platform.plugin_motor_umap",
        "devbench.analysis.shared_motor_umap",
    ),
    "motor_program_comparison": (
        "devbench.platform.plugin_motor_programs",
        "devbench.analysis.workbench_motor_programs",
    ),
}
_MOTOR_PORTS = {
    "umap": "figure",
    "markers": "figure",
    "coordinates": "embedding",
    "composition": "table",
    "parameters": "table",
}
_SOLVER_MODULES = {
    "scrna_ccc": ("scrna_ccc",),
    "scrna_neighborhood_da": ("scrna_neighborhood_da", "family_fitting"),
    "bulk_deconvolution": ("bulk_deconvolution",),
    "bulk_dtu": ("bulk_dtu",),
    "spatial_pattern": ("spatial_analysis",),
    "perturbation_response": (
        "perturbation_analysis",
        "scrna_neighborhood_da",
        "family_fitting",
    ),
    "phenotype_prediction": ("phenotype_prediction",),
}
_PRERESERVE_RACES = frozenset(
    {
        "Controller evidence changed before dispatch",
        "Scientific revision changed before reservation",
    }
)
_ACTIVATION_RACES = frozenset(
    {
        "Scientific revision changed",
        "Scientific revision changed during authorization",
    }
)
_REVISION_ATTEMPTS = 5


def _operation_modules(operation):
    from .plugin_rna_solvers import LOCAL_OPERATIONS

    if operation in LOCAL_OPERATIONS:
        if operation not in _SOLVER_MODULES:
            raise OperationBlocked("plugin_graph_operation_unregistered")
        shared = (
            "_rna_solver_inputs",
            "design",
            "rna_answerability",
            "scientific_types",
        )
        return ("devbench.platform.plugin_rna_solvers",) + tuple(
            "devbench.core." + module
            for module in (*_SOLVER_MODULES[operation], *shared)
        )
    if operation not in _OP_MODULES:
        raise OperationBlocked("plugin_graph_operation_unregistered")
    return _OP_MODULES[operation]


def registered_plugin_runtime(plan):
    """Pin the registered adapter, its entry points, and the route's own runtime."""
    modules = (*_BASE_MODULES, *_operation_modules(plan["operation"]))
    try:
        sources = {}
        for module in modules:
            spec = find_spec(module)
            if spec is None or spec.origin is None:
                raise ValueError("Missing native Plugin module")
            sources[module] = hash_bounded_file(
                Path(spec.origin).resolve(), _MAX_RECEIPT
            )
    except (ImportError, OSError, ValueError) as error:
        raise OperationBlocked("plugin_graph_runtime_unavailable") from error
    code = digest(sources)
    return {
        "format": "devbench.plugin-native-runtime/1",
        "code_sha256": code,
        "environment_sha256": digest(
            {
                "code": code,
                "runtime": plan["runtime"],
                "python": sys.version,
                "platform": platform.system(),
                "architecture": platform.machine(),
            }
        ),
    }


def is_registered_plugin_step(step):
    return step["profile"]["profile_id"] == _PROFILE


def plugin_dispatch_identity(ledger, executor, envelope):
    if not is_registered_plugin_step(envelope["step"]):
        raise OperationBlocked("adaptive_plugin_requires_bound_step")
    if (
        type(executor) is not RegisteredPluginExecutor
        or executor.ledger.path != ledger.path
    ):
        raise PermissionError("Native Plugin execution requires its bound executor")
    executor.validate(envelope)
    return executor.code_sha256


def _dependency_refs(plan):
    refs = plan.get("dependencies", [])
    if not isinstance(refs, list) or len(refs) + len(plan["profiles"]) > 16:
        raise OperationBlocked("plugin_dependency_scope_invalid")
    ids = []
    for ref in refs:
        if (
            not isinstance(ref, dict)
            or set(ref) != {"plan_id", "plan_sha256"}
            or not _ID.fullmatch(str(ref["plan_id"]))
            or not _SHA.fullmatch(str(ref["plan_sha256"]))
            or ref["plan_id"] == plan["plan_id"]
        ):
            raise OperationBlocked("plugin_dependency_scope_invalid")
        ids.append(ref["plan_id"])
    if len(set(ids)) != len(ids):
        raise OperationBlocked("plugin_dependency_scope_invalid")
    return refs


def require_plugin_dependencies(jobs_or_workspace, plan):
    """Require completed, current, verified prerequisite outputs without writes."""
    from .plugin_mcp import studio_job_readback

    refs = _dependency_refs(plan)
    if not refs:
        return
    workspace = (
        jobs_or_workspace.workspace
        if isinstance(jobs_or_workspace, JobStore)
        else Path(jobs_or_workspace)
    )
    with studio_job_readback(workspace) as db:
        if db is None:
            raise OperationBlocked("plugin_dependency_not_completed")
        for ref in refs:
            key = (plan["session_id"], ref["plan_id"])
            frozen = JobStore._plan(
                db.execute(
                    "SELECT * FROM plugin_plans WHERE session_id=? AND plan_id=?",
                    key,
                ).fetchone(),
                ref["plan_id"],
                ref["plan_sha256"],
            )
            run = db.execute(
                "SELECT * FROM plugin_runs WHERE session_id=? AND plan_id=?",
                key,
            ).fetchone()
            if run is None or run["status"] != "completed" or not run["receipt_json"]:
                raise OperationBlocked("plugin_dependency_not_completed")
            if run["plan_sha256"] != ref["plan_sha256"]:
                raise OperationBlocked("plan_identity_mismatch")
            verify_registered_plugin_result(
                workspace, run["run_id"], frozen, json.loads(run["receipt_json"])
            )


def _port_kinds(operation):
    from .plugin_rna_solvers import LOCAL_OPERATIONS

    ports = {"result": "table"}
    if operation == "shared_motor_umap":
        ports.update(_MOTOR_PORTS)
    elif operation in LOCAL_OPERATIONS or operation == "motor_program_comparison":
        ports["analysis"] = "table"
    return ports


def _registration(plan):
    pinned = plan.get("graph_runtime") or {}
    operation = plan["operation"]
    _operation_modules(operation)
    plan_id = plan["plan_id"]
    if not _ID.fullmatch(plan_id):
        raise OperationBlocked("plan_identity_mismatch")
    sources = tuple(
        ArtifactVersion(
            version_id=f"plugin-{plan_id}-input-{index}",
            sha256=profile["sha256"],
            kind="table",
            representation="plugin-source-" + profile["observed"]["file_type"],
            biological_unit="not_declared",
            unit_membership_sha256=profile["sha256"],
            measurement_history=("plugin_verified_source",),
        )
        for index, profile in enumerate(plan["profiles"])
    )
    ports = _port_kinds(operation)
    profile = ProcedureProfile(
        profile_id=_PROFILE,
        version=1,
        operations=(
            OperationCapability(
                operation_id=operation,
                output_kinds=tuple(sorted(set(ports.values()))),
                max_memory_mb=8192,
                timeout_seconds=MAX_SECONDS,
            ),
        ),
        input_representations=tuple(
            sorted({item.representation for item in sources} | {_RESULT})
        ),
        biological_units=("not_declared",),
        permitted_claims=("checked_observation",),
    )
    step_id = "plugin-" + plan_id
    step = AdaptiveStep(
        step_id=step_id,
        branch_id=step_id,
        operation_id=operation,
        profile=profile,
        environment_sha256=pinned["environment_sha256"],
        parameters_sha256=digest(plan),
        inputs=tuple(
            InputLink(
                port={"name": f"source_{index}", "artifact_kind": "table"},
                source_id=item.version_id,
            )
            for index, item in enumerate(sources)
        )
        + tuple(
            InputLink(
                port={"name": f"prerequisite_{index}", "artifact_kind": "table"},
                source_id="plugin-" + ref["plan_id"],
                source_port="result",
            )
            for index, ref in enumerate(_dependency_refs(plan))
        ),
        outputs=tuple(
            {"name": name, "artifact_kind": kind} for name, kind in ports.items()
        ),
        basis=({"basis_id": "workbench-intake"}, {"basis_id": "workbench-exposure"}),
        purpose="checked_observation",
        shared_units=False,
    )
    return step, sources


def _register(flow, step, sources):
    # Atomic registration allows a retry after a crash before graph dispatch.
    with flow.authority._execution_tx(flow.lineage_id) as db:
        state = flow._state(db)
        for artifact in sources:
            previous = state.artifacts.get(artifact.version_id)
            if previous is None:
                flow.register_artifact(
                    artifact, basis=("workbench-intake",), connection=db
                )
            elif previous != (artifact.model_dump(mode="json"), None):
                raise OperationConflict("Plugin graph input registration changed")
        existing = state.nodes.get(step.step_id)
        if existing is None:
            flow.insert(step, connection=db)
        elif existing != step:
            raise OperationConflict("Plugin graph step registration changed")


def _custody_path(workspace, attempt_id):
    if not _ID.fullmatch(attempt_id):
        raise ValueError("Invalid Plugin graph attempt identity")
    path = workspace / "plugin-graph" / attempt_id
    if path.resolve() != path:
        raise ValueError("Plugin graph custody path contains a symlink")
    return path


def _sealed_json(path, value):
    raw = canonical(value)
    if len(raw) > _MAX_RECEIPT:
        raise OperationBlocked("plugin_graph_receipt_too_large")
    with NamedTemporaryFile(dir=path.parent) as candidate:
        candidate.write(raw)
        candidate.flush()
        os.fchmod(candidate.fileno(), 0o400)
        os.fsync(candidate.fileno())
        os.link(candidate.name, path)
    descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return digest(value)


def _read_sealed_json(path):
    raw = read_bounded_file(path, _MAX_RECEIPT)
    content = decode_receipt_json(raw)
    if not isinstance(content, dict) or raw != canonical(content):
        raise ValueError("Native custody requires the exact canonical output bytes")
    return content


def _material_hashes(workspace, rid, plan, result):
    from devbench.core.population_data import bound_query_file

    run_root = workspace / "runs" / rid
    operation = plan["operation"]
    if operation == "shared_motor_umap":
        from .plugin_motor_umap import _ARTIFACTS

        claims = result["artifacts"]
        if set(claims) != set(_ARTIFACTS):
            raise ValueError("Unexpected motor-neuron graph artifacts")
        actual = {
            name: hash_bounded_file(run_root / "shared-motor-umap" / filename, maximum)
            for name, (filename, _, maximum) in _ARTIFACTS.items()
        }
        if actual != claims:
            raise ValueError("Motor-neuron graph output changed")
        return actual
    if "analysis" not in _port_kinds(operation):
        return {}
    artifact = result["result_artifact"]
    path = workspace / artifact["path"]
    size = artifact["size_bytes"]
    if (
        not path.is_relative_to(run_root)
        or type(size) is not int
        or not 1 <= size <= _MAX_ARTIFACT
    ):
        raise ValueError("Unbounded or unrelated native result artifact")
    with bound_query_file(path, artifact["sha256"], size) as source:
        if os.fstat(source.fileno()).st_size != size:
            raise ValueError("Native result artifact size changed")
    return {"analysis": artifact["sha256"]}


def _outputs(attempt_id, plan, content):
    return {
        name: ArtifactVersion(
            version_id=f"plugin-{attempt_id}-{name}",
            sha256=identity,
            kind=_port_kinds(plan["operation"])[name],
            representation=_RESULT
            if name == "result"
            else f"plugin-{plan['operation']}-{name}-v1",
            biological_unit="not_declared",
            unit_membership_sha256=digest(plan["profiles"]),
            measurement_history=("plugin_verified_output",),
        )
        for name, identity in content.items()
    }


def _read_custody(workspace, rid, plan, envelope, receipt):
    path = _custody_path(workspace, receipt.job_id)
    record = _read_sealed_json(path / "receipt.json")
    manifest = record["manifest"]
    if (
        set(record) != {"manifest", "receipt"}
        or record["receipt"] != receipt.model_dump(mode="json")
        or manifest
        != {
            "format": _FORMAT,
            "run_id": rid,
            "plan_sha256": digest(plan),
            "request_sha256": request_digest(envelope),
            "status": receipt.status,
            "reason": receipt.reason,
            "output_sha256s": {
                key: value.sha256 for key, value in receipt.outputs.items()
            },
        }
        or digest(manifest) != receipt.io_sha256
        or tuple(pin.model_dump(mode="json") for pin in receipt.consumed)
        != tuple(envelope["pins"])
    ):
        raise ValueError("Native graph receipt differs from the retained submission")
    if receipt.status == "failed":
        return None
    result = _read_sealed_json(path / "result.json")
    if "graph_execution" in result:
        raise ValueError("Native result has invalid graph custody")
    actual = {
        "result": digest(result),
        **_material_hashes(workspace, rid, plan, result),
    }
    if _outputs(receipt.job_id, plan, actual) != receipt.outputs:
        raise ValueError("Native graph output digest changed")
    return result


class RegisteredPluginExecutor:
    """Bound coordinator executor. The public Plugin never supplies callbacks."""

    backend_id = "local_registered_plugin_1"

    def __init__(self, ledger, jobs, rid, plan, execute, verify_sources):
        self.ledger, self.workspace, self.rid = ledger, jobs.workspace, rid
        self.plan, self.execute, self.verify_sources = plan, execute, verify_sources
        self.code_sha256 = plan["graph_runtime"]["code_sha256"]

    def validate(self, envelope):
        if (
            envelope["lineage_id"] != self.plan["session_id"]
            or envelope["environment_sha256"]
            != self.plan["graph_runtime"]["environment_sha256"]
            or envelope["step"]
            != _registration(self.plan)[0].model_dump(
                mode="json", exclude={"oci_method_version_id"}
            )
            or self.plan["graph_runtime"] != registered_plugin_runtime(self.plan)
        ):
            raise OperationBlocked("plugin_graph_execution_identity_changed")

    def run(self, envelope, *, token):
        self.validate(envelope)
        attempt = token["attempt_id"]
        path = _custody_path(self.workspace, attempt)
        path.mkdir(mode=0o700, parents=True, exist_ok=False)
        started = time.monotonic()
        reason, outputs = None, {}
        try:
            self.verify_sources()
            result = self.execute()
            self.verify_sources()
            if not isinstance(result, dict) or set(result) & {
                "graph_execution",
                "_terminal_status",
            }:
                raise OperationBlocked("plugin_graph_result_unavailable")
            members = _material_hashes(self.workspace, self.rid, self.plan, result)
            checksum = _sealed_json(path / "result.json", result)
            outputs = _outputs(attempt, self.plan, {"result": checksum, **members})
        except OperationBlocked as error:
            reason = (
                error.state
                if _REASON.fullmatch(error.state)
                else "plugin_graph_execution_blocked"
            )
        except TimeoutError:
            reason = "plugin_worker_deadline_exceeded"
        except Exception as error:
            reason = f"worker_failed_{type(error).__name__}"
        status = "failed" if reason else "succeeded"
        manifest = {
            "format": _FORMAT,
            "run_id": self.rid,
            "plan_sha256": digest(self.plan),
            "request_sha256": token["request_sha256"],
            "status": status,
            "reason": reason,
            "output_sha256s": {key: value.sha256 for key, value in outputs.items()},
        }
        receipt = StepReceipt(
            job_id=attempt,
            request_sha256=token["request_sha256"],
            consumed=tuple(envelope["pins"]),
            status=status,
            outputs=outputs,
            reason=reason,
            cleanup_state="unknown" if reason else "confirmed",
            wall_millis=round((time.monotonic() - started) * 1000),
            io_sha256=digest(manifest),
        )
        _sealed_json(
            path / "receipt.json",
            {"manifest": manifest, "receipt": receipt.model_dump(mode="json")},
        )
        return receipt

    def verify(self, envelope, receipt):
        try:
            self.validate(envelope)
            _read_custody(self.workspace, self.rid, self.plan, envelope, receipt)
            return True
        except (KeyError, OSError, ValueError, OperationBlocked):
            return False

    def recover(self, flow, token):
        path = _custody_path(self.workspace, token["attempt_id"]) / "receipt.json"
        if not path.exists():
            raise OperationBlocked("plugin_graph_attempt_requires_reconciliation")
        receipt = StepReceipt.model_validate(_read_sealed_json(path)["receipt"])
        return flow.complete(token, receipt, verify=self.verify)


def _flow(workspace, plan, *, readonly=False):
    from .plugin_mcp import _ledger

    return AdaptiveWorkflow(_ledger(workspace, readonly=readonly), plan["session_id"])


def _accepted_result(flow, workspace, rid, plan):
    if not _ID.fullmatch(rid):
        raise OperationBlocked("unknown_run")
    step_id = "plugin-" + plan["plan_id"]

    def current(db):
        state = flow._state(db)
        result = state.outcome(step_id)
        if (
            result is None
            or result["status"] != "succeeded"
            or not result["accepted_record_id"]
        ):
            raise OperationBlocked("plugin_graph_receipt_unavailable")
        if set(result["outputs"]) != set(_port_kinds(plan["operation"])):
            raise OperationBlocked("plugin_graph_result_changed")
        outputs = {
            name: flow._current_use(db, step_id, name, "compute", None)
            for name in result["outputs"]
        }
        dispatch = state.dispatches[result["attempt_id"]]
        return result, outputs, dispatch

    result, outputs, dispatch = flow._read_projection(current)
    attempt_id = result["attempt_id"]
    envelope = dispatch["envelope"]
    if (
        envelope["step"]["parameters_sha256"] != digest(plan)
        or dispatch["token"]["backend_id"] != RegisteredPluginExecutor.backend_id
    ):
        raise OperationBlocked("plugin_graph_execution_identity_changed")
    try:
        path = _custody_path(workspace, attempt_id) / "receipt.json"
        receipt = StepReceipt.model_validate(_read_sealed_json(path)["receipt"])
        if (
            receipt.job_id != attempt_id
            or receipt.outputs != outputs
            or request_digest(receipt.model_dump(mode="json"))
            != result["receipt_sha256"]
        ):
            raise ValueError("Graph outcome differs from the reported attempt")
        retained = _read_custody(workspace, rid, plan, envelope, receipt)
    except (KeyError, OSError, TypeError, ValueError) as error:
        raise OperationBlocked("plugin_graph_result_changed") from error
    return {
        **retained,
        "graph_execution": {
            "step_id": step_id,
            "attempt_id": attempt_id,
            "accepted_record_id": result["accepted_record_id"],
            "receipt_sha256": result["receipt_sha256"],
            "result_sha256": outputs["result"].sha256,
        },
    }


def verify_registered_plugin_result(workspace, rid, plan, receipt):
    """Check readback and return only results bound to accepted graph evidence."""
    actual = _accepted_result(
        _flow(workspace, plan, readonly=True), workspace, rid, plan
    )
    for key, value in actual.items():
        if key == "receipt_sha256" or (key == "figure_preview" and key not in receipt):
            continue
        if key not in receipt or receipt[key] != value:
            raise OperationBlocked("plugin_graph_result_changed")
    return actual


def run_registered_plugin_step(jobs, rid, plan, *, execute, verify_sources):
    """Run one exact JobStore-approved native node and return accepted evidence."""
    from .workbench_interactive import _activate_session

    sid, plan_id = plan["session_id"], plan["plan_id"]
    row = jobs.run(sid, rid)
    if (
        row["pid"] != os.getpid()
        or row["status"] != "running"
        or row["plan_id"] != plan_id
        or row["plan_sha256"] != digest(plan)
        or jobs.plan(sid, plan_id) != plan
    ):
        raise OperationBlocked("worker_ownership_unavailable")
    if plan.get("graph_runtime") != registered_plugin_runtime(plan):
        raise OperationBlocked("plugin_graph_runtime_changed")
    require_plugin_dependencies(jobs, plan)
    verify_sources()
    flow = _flow(jobs.workspace, plan)
    for attempt in range(_REVISION_ATTEMPTS):
        try:
            _activate_session(
                flow,
                _APPROVED_WORKER,
                "workbench-session-input",
                {"question": plan["objective"]},
            )
            break
        except OperationConflict as error:
            if str(error) not in _ACTIVATION_RACES or attempt == _REVISION_ATTEMPTS - 1:
                raise
            time.sleep(0.015 * (attempt + 1))
    step, sources = _registration(plan)
    _register(flow, step, sources)
    executor = RegisteredPluginExecutor(
        flow.ledger, jobs, rid, plan, execute, verify_sources
    )
    outcome = flow._read_projection(lambda db: flow._state(db).outcome(step.step_id))
    if outcome is None:
        outcome = _dispatch_native(flow, step.step_id, executor)
    if outcome["status"] != "succeeded":
        raise OperationBlocked(outcome["reason"] or "plugin_graph_execution_failed")
    return _accepted_result(flow, jobs.workspace, rid, plan)


def _dispatch_native(flow, step_id, executor):
    for attempt in range(_REVISION_ATTEMPTS):
        try:
            return flow.dispatch(step_id, executor, actor="plugin-operator")
        except OperationConflict as error:
            if str(error) not in _PRERESERVE_RACES or attempt == _REVISION_ATTEMPTS - 1:
                raise
            # Studio readback can append disclosure records. Recompute the exact
            # envelope only if this step has not reserved or started work.
            if flow._read_projection(
                lambda db: any(
                    item["envelope"]["step"]["step_id"] == step_id
                    for item in flow._state(db).dispatches.values()
                )
            ):
                raise
            time.sleep(0.015 * (attempt + 1))


class _ApprovedWorker:
    allow_data_execution = True


_APPROVED_WORKER = _ApprovedWorker()
