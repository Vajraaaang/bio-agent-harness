"""Bind a locally registered, isolated method to an approved Plugin graph step."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from types import SimpleNamespace

from devbench.analysis.evidence_access import EvidenceBroker
from devbench.core._files import hash_bounded_file
from devbench.core.binary_artifacts import check_flat_parquet
from devbench.core.procedure_profile import ProcedureProfile
from devbench.engine.exploratory_images import exploratory_image
from devbench.engine.methods import MethodService, exploratory_method
from devbench.engine.tier2_artifacts import LANE_TAGS, port_kind
from devbench.engine.workflow_operations import OperationBlocked, OperationConflict

from .adaptive_workflow import AdaptiveStep, ArtifactVersion, BasisUse, InputLink, Port
from .method_planner import ScientificMethodPlanner
from .plugin_jobs import canonical, digest

OPERATION = "registered_exploratory"
_FORMAT = "devbench.plugin-registered-method/1"
_EXTERNAL_SCOPE = {"synthetic", "public"}
_PLAN_ID = re.compile(r"[0-9a-f]{32}")
_INTAKE = ("workbench-intake", "workbench-exposure")


def _context(workspace, session_id, parameters, *, readonly=False):
    from .plugin_mcp import _ledger

    ledger = _ledger(workspace, readonly=readonly)
    broker = EvidenceBroker.open_existing(ledger.scientific)
    broker.check_delivery(
        f"plugin-scientist-{session_id}",
        f"plugin-session-{session_id}",
        lineage_id=session_id,
        role="scientist",
    )
    methods = MethodService(workspace / "methods", reconcile=False, readonly=readonly)
    planner = ScientificMethodPlanner(methods)
    flow = planner.adaptive_workflow(session_id, ledger)
    flow.manual_local_oci = True
    executor = planner.exploratory_executor(session_id, ledger, (parameters,))
    executor.session_id = session_id
    return flow, executor


def _live_method(executor, method_version_id):
    method = executor.methods.method(method_version_id)
    contract = method["contract"]
    checked = exploratory_method(contract)
    for key, expected in (
        ("source_artifact", contract["implementation"]["source_sha256"]),
        ("environment_artifact", contract["environment"]["lock_sha256"]),
    ):
        path = executor.methods.artifact_path(method[key])
        if hash_bounded_file(path, 1_000_000) != expected:
            raise OperationBlocked("registered_method_artifact_changed")
    return checked, contract


def _native_json_source(flow, executor, artifact):
    from .plugin_graph import _custody_path, verify_registered_plugin_result
    from .plugin_jobs import JobStore
    from .plugin_mcp import _verify_replay, studio_job_readback, studio_plan_scope

    workspace = executor.methods.root.parent
    sid = flow.lineage_id
    with flow._read_connection() as db:
        state = flow._state(db)
        version = state.artifacts.get(artifact["version_id"])
        if version is None or version[1] is None:
            raise OperationBlocked("registered_method_native_source_unavailable")
        step_id, generation = version[1]
        if not step_id.startswith("plugin-") or not _PLAN_ID.fullmatch(step_id[7:]):
            raise OperationBlocked("registered_method_native_source_unavailable")
        accepted = flow._current_use(db, step_id, "result", "compute", generation)
        if accepted.model_dump(mode="json") != artifact:
            raise OperationBlocked("registered_method_native_source_changed")
        outcome = state.outcome(step_id)
    plan_id = step_id[7:]
    with studio_job_readback(workspace) as db:
        if db is None:
            raise OperationBlocked("registered_method_native_source_unavailable")
        plan_row = db.execute(
            "SELECT * FROM plugin_plans WHERE session_id=? AND plan_id=?",
            (sid, plan_id),
        ).fetchone()
        plan = JobStore._plan(plan_row, plan_id)
        run = db.execute(
            "SELECT * FROM plugin_runs WHERE session_id=? AND plan_id=? "
            "AND plan_sha256=? AND status='completed'",
            (sid, plan_id, plan_row["plan_sha256"]),
        ).fetchone()
        if (
            run is None
            or not studio_plan_scope(db, plan)
            or not plan.get("graph_runtime")
        ):
            raise OperationBlocked("registered_method_native_source_unavailable")
    rid = run["run_id"]
    receipt = json.loads(run["receipt_json"])
    _verify_replay(
        workspace / "runs" / rid / "replay_bundle", run["plan_sha256"], receipt
    )
    verified = verify_registered_plugin_result(workspace, rid, plan, receipt)
    proof = verified["graph_execution"]
    if (
        proof["attempt_id"] != outcome["attempt_id"]
        or proof["receipt_sha256"] != outcome["receipt_sha256"]
        or proof["result_sha256"] != accepted.sha256
    ):
        raise OperationBlocked("registered_method_native_source_changed")
    return _custody_path(workspace, proof["attempt_id"]) / "result.json"


def _check_json_input(flow, executor, artifact):
    from .plugin_graph import _RESULT

    checksum = artifact["sha256"]
    vault = executor.artifacts
    if artifact["representation"] == _RESULT:
        source = _native_json_source(flow, executor, artifact)
        if vault.put_file(source) != checksum:
            raise OperationBlocked("registered_method_native_source_changed")
    try:
        # JSON is eagerly decoded. Files, tables and matrix ports are streamed.
        path = vault.root / checksum
        from devbench.analysis.method_validation import _bounded_finite_json

        _bounded_finite_json(path, 64 * 1024**2)
        if hash_bounded_file(path, 64 * 1024**2) != checksum:
            raise OperationBlocked("registered_method_input_bytes_unavailable")
    except FileNotFoundError as error:
        raise OperationBlocked("registered_method_input_bytes_unavailable") from error


def _check_port_contents(flow, executor, contract, inputs):
    from .plugin_graph import _RESULT

    for name, artifact in inputs.items():
        requested = contract["inputs"][name]["artifact_type"]
        checksum = artifact["sha256"]
        if executor._matrix_object(artifact):
            if requested not in {"anndata", "file"}:
                raise OperationBlocked("registered_method_input_transport_unsupported")
            executor.zarr_vault.verify(checksum)
        elif artifact["representation"] == "exploratory_parquet":
            if requested != "file":
                raise OperationBlocked("registered_method_input_transport_unsupported")
            vault = executor.binary_training
            check_flat_parquet(vault.root / checksum, checksum, vault.max_bytes)
        elif requested in {"file", "anndata", "table"}:
            if artifact["representation"] == _RESULT:
                _check_json_input(flow, executor, artifact)
            with executor.artifacts.open(checksum):
                pass
        elif requested == "json":
            _check_json_input(flow, executor, artifact)
        else:
            raise OperationBlocked("registered_method_input_transport_unsupported")


def freeze_registered_method(
    workspace: Path,
    session_id: str,
    *,
    plan_id: str,
    method_version_id: str,
    step_name: str,
    parameters: dict,
    input_versions: dict[str, str],
    offline_studio: bool = False,
) -> dict:
    """Validate immutable source and graph pins without issuing authority."""
    from devbench.engine.tier2_approval import script_scope

    from .plugin_mcp import _ACTOR

    if (
        not _PLAN_ID.fullmatch(plan_id)
        or not 1 <= len(step_name.strip()) <= 200
        or type(parameters) is not dict
        or len(canonical(parameters)) > 32_768
        or type(input_versions) is not dict
        or not 1 <= len(input_versions) <= 16
    ):
        raise ValueError("Supply bounded registered method parameters and input ports")
    flow, executor = _context(workspace, session_id, parameters)
    executor.private = offline_studio
    method, contract = _live_method(executor, method_version_id)
    method.validate_parameters(parameters)
    required = {name for name, spec in contract["inputs"].items() if spec["required"]}
    if not required <= input_versions.keys() <= contract["inputs"].keys():
        raise OperationBlocked("registered_method_input_ports_mismatch")
    artifacts = flow._read_projection(lambda db: flow._state(db).artifacts)
    versions = {
        name: artifacts.get(version) for name, version in input_versions.items()
    }
    if any(item is None for item in versions.values()):
        raise OperationBlocked("registered_method_input_unavailable")
    sources = {
        name: ArtifactVersion.model_validate(item[0]) for name, item in versions.items()
    }
    if offline_studio:
        broker = EvidenceBroker.open_existing(flow.authority)
        for item in sources.values():
            broker.register_source(
                "offline-input-" + digest([session_id, item.sha256]),
                content_sha256=item.sha256,
                roots=(item.sha256,),
                tags=("private",),
                classification="target_outcome",
                parents=("plugin-session-" + session_id,),
            )
    elif _protected_sources(workspace, sources):
        raise OperationBlocked("private_data_local_studio_only")
    step = AdaptiveStep(
        step_id="plugin-" + plan_id,
        branch_id="plugin-" + plan_id,
        operation_id=OPERATION,
        profile=ProcedureProfile.model_validate(
            {
                "profile_id": "plugin-oci-" + method_version_id,
                "version": 1,
                "operations": [
                    {
                        "operation_id": OPERATION,
                        "output_kinds": sorted(
                            {port_kind(v) for v in contract["outputs"].values()}
                        ),
                        "max_memory_mb": contract["execution"]["memory_mb"],
                        "timeout_seconds": contract["execution"]["timeout_seconds"],
                    }
                ],
                "input_representations": sorted(
                    {item.representation for item in sources.values()}
                ),
                "biological_units": sorted(
                    {item.biological_unit for item in sources.values()}
                ),
                "permitted_claims": ["exploratory_finding"],
            }
        ),
        environment_sha256=digest(
            {
                **contract["environment"],
                "image_id": exploratory_image(
                    contract["environment"]["image"]
                ).image_id,
            }
        ),
        parameters_sha256=digest(parameters),
        inputs=tuple(
            InputLink(
                port=Port.model_validate(
                    {
                        "name": name,
                        "artifact_kind": item.kind,
                        "required": contract["inputs"][name]["required"],
                    }
                ),
                source_id=item.version_id,
            )
            for name, item in sorted(sources.items())
        ),
        outputs=tuple(
            Port(name=name, artifact_kind=port_kind(spec), required=spec["required"])
            for name, spec in sorted(contract["outputs"].items())
        ),
        basis=tuple(BasisUse(basis_id=name) for name in _INTAKE),
        purpose="exploratory",
        oci_method_version_id=method_version_id,
    )
    candidate = flow.candidate_envelope(step, actor=_ACTOR)
    executor.validate(candidate)
    _check_port_contents(flow, executor, contract, candidate["inputs"])
    return {
        "format": _FORMAT,
        "session_id": session_id,
        "plan_id": plan_id,
        "step_name": step_name.strip(),
        "method_contract_sha256": digest(contract),
        "script_sha256": contract["implementation"]["source_sha256"],
        "script_scope": script_scope(candidate, contract),
        "matrix_attestations": {
            port: executor.zarr_vault.attestation(item["sha256"])
            for port, item in candidate["inputs"].items()
            if executor._matrix_object(item)
        },
        "parameters": parameters,
        "step": step.model_dump(mode="json"),
        "pins": candidate["pins"],
    }


def _protected_sources(workspace, sources):
    from .plugin_jobs import JobStore

    if any(item.data_classification == "private" for item in sources.values()):
        return True
    return JobStore(workspace).has_private_input({v.sha256 for v in sources.values()})


def _bound_plan(workspace, plan, *, readonly=False, offline_studio=False):
    if plan["operation"] != OPERATION:
        raise OperationBlocked("registered_method_scope_unavailable")
    if plan["data_classification"] not in _EXTERNAL_SCOPE and not offline_studio:
        raise OperationBlocked("private_data_local_studio_only")
    sid, plan_id = plan["session_id"], plan["plan_id"]
    binding = plan["registered_method"]
    step = AdaptiveStep.model_validate(binding["step"])
    if (
        binding["format"] != _FORMAT
        or (binding["session_id"], binding["plan_id"]) != (sid, plan_id)
        or step.step_id != "plugin-" + plan_id
        or step.operation_id != OPERATION
        or step.purpose != "exploratory"
        or step.parameters_sha256 != digest(binding["parameters"])
    ):
        raise OperationBlocked("registered_method_plan_changed")
    flow, executor = _context(workspace, sid, binding["parameters"], readonly=readonly)
    _, contract = _live_method(executor, step.oci_method_version_id)
    if (
        digest(contract) != binding["method_contract_sha256"]
        or binding.get("script_sha256", contract["implementation"]["source_sha256"])
        != contract["implementation"]["source_sha256"]
    ):
        raise OperationBlocked("registered_method_contract_changed")
    if not offline_studio:
        checksums = [pin["sha256"] for pin in binding["pins"]]
        if flow.ledger._read(
            lambda db: EvidenceBroker.has_private_content(db, checksums)
        ):
            raise OperationBlocked("private_data_local_studio_only")
    executor.plugin_plan = plan
    executor.private = (
        plan["data_classification"] not in _EXTERNAL_SCOPE or offline_studio
    )
    basis = BasisUse(basis_id="plugin-approval-" + plan_id)
    return binding, flow, executor, contract, step, basis


def _verified_result(flow, executor, plan, binding, step, basis):
    from .plugin_graph import _RESULT

    with flow._read_connection() as db:
        state = flow._state(db)
        outcome = state.outcome(step.step_id)
        if (
            outcome is None
            or outcome["status"] != "succeeded"
            or outcome["accepted_record_id"] is None
            or state.nodes.get(step.step_id) != step
        ):
            raise OperationBlocked("registered_method_current_result_unavailable")
        retained = executor._retained_receipt(state, step.step_id)
        if retained is None:
            raise OperationBlocked("registered_method_receipt_unavailable")
        envelope, receipt = retained
        if envelope["pins"] != binding["pins"] or envelope["basis"].get(
            basis.basis_id, {}
        ).get("sha256") != digest(plan):
            raise OperationBlocked("registered_method_result_scope_changed")
        if not executor.private and EvidenceBroker.has_private_content(
            db, [artifact.sha256 for artifact in receipt.outputs.values()]
        ):
            raise OperationBlocked("private_data_local_studio_only")
        for port, output in receipt.outputs.items():
            current = flow._current_use(db, step.step_id, port, "compute", None)
            if current != output:
                raise OperationBlocked("registered_method_output_changed")
    for artifact in envelope["inputs"].values():
        if artifact["representation"] == _RESULT:
            _native_json_source(flow, executor, artifact)
    outputs = {
        port: item.model_dump(mode="json")
        for port, item in sorted(receipt.outputs.items())
    }
    return {
        "metrics": {"outputs": len(outputs)},
        "summary_tables": [
            {
                "caption": "Verified registered method outputs",
                "columns": ["Port", "Representation", "Verification"],
                "rows": [
                    [name, item["representation"], "verified"]
                    for name, item in outputs.items()
                ],
            }
        ],
        "registered_method_outputs": outputs,
        "graph_execution": {
            "step_id": step.step_id,
            "attempt_id": outcome["attempt_id"],
            "accepted_record_id": outcome["accepted_record_id"],
            "receipt_sha256": outcome["receipt_sha256"],
            "result_sha256": digest(outputs),
        },
        "claim_scope": "exploratory_only",
        # Public legacy MCP has a closed envelope. Its digest-bound StepReceipt
        # and ArtifactVersion children retain the full lane/claim metadata.
        **(LANE_TAGS if executor.private and receipt.execution_lane else {}),
        **({"data_classification": "private"} if executor.private else {}),
        "limitations": [
            "Method execution is verified. Scientific interpretation requires "
            "separate review.",
        ],
    }


def run_registered_method(jobs, rid, plan, *, verify_sources) -> dict:
    """Execute only the exact step launched by an approved Plugin job."""
    from .plugin_mcp import _ACTOR
    from .workbench_interactive import _activate_session

    try:
        run = jobs.run(plan["session_id"], rid)
    except OperationBlocked as error:
        raise OperationBlocked("registered_method_approval_required") from error
    if (
        run["status"] != "running"
        or run["pid"] != os.getpid()
        or run["plan_id"] != plan["plan_id"]
        or run["plan_sha256"] != digest(plan)
        or digest(jobs.plan(plan["session_id"], plan["plan_id"])) != run["plan_sha256"]
    ):
        raise OperationBlocked("registered_method_approval_required")
    from .plugin_jobs import require_execution_policy

    managed = jobs.managed_run(rid)
    offline = bool(
        managed and json.loads(managed["stage_json"]).get("_offline_only") is True
    )
    with jobs.frozen_policy(
        plan["session_id"], plan["plan_id"], run["plan_sha256"]
    ) as (_, protected):
        require_execution_policy(
            jobs.authority, plan, run["plan_sha256"], protected, offline_only=offline
        )
        if protected and plan["data_classification"] in _EXTERNAL_SCOPE:
            raise OperationBlocked("private_data_local_studio_only")

    binding, flow, executor, contract, draft, basis = _bound_plan(
        jobs.workspace, plan, offline_studio=protected and offline
    )

    verify_sources()
    step = draft.model_copy(update={"basis": (*draft.basis, basis)})
    existing = flow._read_projection(lambda db: flow._state(db).nodes.get(step.step_id))
    if existing is None:
        candidate = flow.candidate_envelope(draft, actor=_ACTOR)
        if candidate["pins"] != binding["pins"]:
            raise OperationBlocked("registered_method_input_pins_changed")
        executor.validate(candidate)
        _check_port_contents(flow, executor, contract, candidate["inputs"])
        _activate_session(
            flow,
            SimpleNamespace(allow_data_execution=True),
            "workbench-session-input",
            {"question": plan["objective"]},
        )
        with flow.authority._execution_tx(flow.lineage_id) as db:
            flow.record_basis(basis.basis_id, "fact", digest(plan), connection=db)
            flow.insert(step, connection=db)
            if (
                flow.candidate_envelope(step, actor=_ACTOR, connection=db)["pins"]
                != binding["pins"]
            ):
                raise OperationBlocked("registered_method_input_pins_changed")
    elif existing != step:
        raise OperationConflict("Registered method step differs from the approved plan")
    if flow._read_projection(lambda db: flow._state(db).outcome(step.step_id)) is None:
        jobs.stage(rid, step=OPERATION)
        result = flow.dispatch(step.step_id, executor, actor=_ACTOR)
        if result["status"] != "succeeded":
            raise OperationBlocked(
                result.get("reason") or "registered_method_execution_failed"
            )
    verify_sources()
    result = _verified_result(flow, executor, plan, binding, step, basis)
    return result


def verify_registered_method_result(
    workspace, rid, plan, receipt, *, offline_studio=False
) -> dict:
    """Recheck the retained graph receipt and current output bytes for readback."""
    from .plugin_jobs import JobStore
    from .plugin_mcp import studio_job_readback

    with studio_job_readback(workspace) as db:
        if db is None or (JobStore.protected_inputs(db, plan) and not offline_studio):
            raise OperationBlocked("private_data_local_studio_only")
        row = (
            None
            if db is None
            else db.execute(
                "SELECT plan_id,plan_sha256,status FROM plugin_runs "
                "WHERE session_id=? AND run_id=?",
                (plan["session_id"], rid),
            ).fetchone()
        )
    if (
        row is None
        or row["status"] != "completed"
        or row["plan_id"] != plan["plan_id"]
        or row["plan_sha256"] != digest(plan)
        or receipt.get("session_id") != plan["session_id"]
        or receipt.get("operation") != OPERATION
        or receipt.get("run_id") != rid
    ):
        raise OperationBlocked("registered_method_result_scope_changed")
    binding, flow, executor, _, draft, basis = _bound_plan(
        workspace, plan, readonly=True, offline_studio=offline_studio
    )
    step = draft.model_copy(update={"basis": (*draft.basis, basis)})
    result = _verified_result(flow, executor, plan, binding, step, basis)
    if any(receipt.get(key) != value for key, value in result.items()):
        raise OperationBlocked("registered_method_receipt_changed")
    return result["graph_execution"]
