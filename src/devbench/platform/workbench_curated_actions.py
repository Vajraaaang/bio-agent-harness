"""Workbench declarations and QC readback for coordinator-bound single-cell steps."""

from __future__ import annotations

import hashlib
from collections import defaultdict
from dataclasses import replace
from itertools import islice

from pydantic import TypeAdapter

from devbench.analysis.rna_investigation_projection import _json
from devbench.core.contrast_design import TIER_A_BULK_OPERATION
from devbench.engine.partition_authority import embargoed
from devbench.engine.tier_a_executor import TierAExecutor
from devbench.engine.tier_a_pathways import PATHWAY_OPERATIONS
from devbench.engine.tier_a_scrna import (
    _REVIEWED_INPUTS,
    _require_image,
    load_single_cell_spec,
    validate_single_cell_step,
)
from devbench.engine.workflow_operations import OperationBlocked, OperationConflict
from rna_kernels.scrna_upstream_contract import (
    _PARAMETERS,
    DONOR_TRAJECTORY,
    SCRNA_OPERATIONS,
    DropletParameters,
    SingleCellStepSpec,
)

from .adaptive_workflow import request_digest
from .adaptive_workflow_state import (
    AdaptiveStep,
    ArtifactVersion,
    BasisUse,
    Identity,
    _State,
)
from .thesis_workflow import has_thesis_scope
from .workbench_adaptive import _adaptive_ledger, _flow, _graph_revision
from .workbench_interactive import ensure_adaptive_binding
from .workflow_adaptive_actions import register_adaptive_output_source

_QC = "scrna_batch_qc"
_ACTOR = "workbench-operator"
_IDENTITY = TypeAdapter(Identity)
_VIEW_LIMIT = 96
_REPORT_ROWS = 24
_MAX_REPORT_BYTES = 16_000_000
_DROPLET_SCHEMA = TypeAdapter(DropletParameters).json_schema()
_BASE_FIELDS = {
    "action",
    "expected_revision",
    "allow_data_execution",
    "step_id",
    "parameters",
}
_FIELDS = {
    "queue": _BASE_FIELDS | {"operation", "branch_id", "biological_unit", "sources"},
    "revise_qc": _BASE_FIELDS | {"generation"},
}
_INPUT_REPRESENTATIONS = {
    port.representation
    for operation in SCRNA_OPERATIONS.values()
    for port in operation.inputs.values()
}
_QC_FIELDS = (
    "method",
    "cells_input",
    "cells_retained",
    "cells_excluded",
    "mitochondrial_features",
    "gene_symbol_source",
    "min_genes",
    "mad_limit",
    "max_mito_percent",
    "qc_group_key",
    "mad_definition",
    "mad_scale",
)


def _source_choice(flow, db, state, source_id, port, artifact):
    if (
        artifact["representation"] not in _INPUT_REPRESENTATIONS
        or "checked_observation" not in artifact["permitted_uses"]
        or artifact["version_id"] in state.stale_versions
        or embargoed(db, artifact["sha256"])
    ):
        return None
    if any(
        flow._held_ancestry(state, step)
        for step in flow._artifact_producers(
            state,
            artifact["version_id"],
        )
    ):
        return None
    try:
        flow._check_derived_delivery(db, state, artifact["version_id"])
    except OperationBlocked:
        return None
    return {
        "id": source_id,
        "port": port,
        **{key: artifact[key] for key in ("kind", "representation", "biological_unit")},
    }


def _sources(flow, db, state):
    for version_id, (artifact, producer) in sorted(state.artifacts.items()):
        if producer is None:
            entry = _source_choice(flow, db, state, version_id, None, artifact)
            if entry is not None:
                yield entry
    for step_id, step in sorted(state.nodes.items()):
        outcome = state.outcome(step_id)
        if (
            not outcome
            or outcome["status"] != "succeeded"
            or step.branch_id in state.retired
        ):
            continue
        for port, artifact in outcome["outputs"].items():
            entry = _source_choice(flow, db, state, step_id, port, artifact)
            if entry is not None:
                yield entry


def _operation_reason(executor, operation_id, operation, sources, execution_block):
    if operation_id == DONOR_TRAJECTORY:
        return "thesis_answerability_required"
    if execution_block is not None:
        return execution_block
    try:
        _require_image(executor, operation_id)
    except PermissionError:
        return "pinned_runtime_required"
    if any(
        not any(
            (port.representation, port.kind) == (item["representation"], item["kind"])
            for item in sources
        )
        for port in operation.inputs.values()
    ):
        return "current_input_required"
    return None


def _operations(executor, sources, execution_block):
    operations = []
    for operation_id, operation in SCRNA_OPERATIONS.items():
        reason = _operation_reason(
            executor, operation_id, operation, sources, execution_block
        )
        operations.append(
            {
                "id": operation_id,
                "inputs": [
                    {
                        "name": name,
                        "representation": port.representation,
                        "kind": port.kind,
                    }
                    for name, port in operation.inputs.items()
                ],
                "available": reason is None,
                "reason": reason,
            }
        )
    return operations


def _qc_report(flow, db, state, step_id, generation):
    step = state.nodes.get(step_id)
    if step is None or step.operation_id != _QC:
        raise OperationBlocked("single_cell_current_qc_required")
    artifact = flow._current_use(db, step_id, "qc_report", "compute", generation)
    outcome = state.outcome(step_id)
    dispatch = state.dispatches[outcome["attempt_id"]]
    if (
        dispatch["token"]["backend_id"] != TierAExecutor.backend_id
        or artifact.representation != "scrna_qc_report_json"
    ):
        raise OperationBlocked("single_cell_qc_receipt_unverified")
    return artifact, outcome["receipt_sha256"], dispatch["envelope"]


def _has_current_qc_report(flow, db, state, step_id, generation):
    try:
        _qc_report(flow, db, state, step_id, generation)
    except OperationBlocked:
        return False
    return True


def _related_stage(step: AdaptiveStep) -> bool:
    return step.operation_id in PATHWAY_OPERATIONS or (
        step.operation_id == TIER_A_BULK_OPERATION
        and step.regime in {"S-I", "S-P", "P-P"}
    )


def _view(app, binding, flow):
    executor = binding.tier_a_executor
    runtime_ready = type(executor) is TierAExecutor and app.allow_data_execution is True
    with flow._read_connection() as db:
        state = flow._state(db)
        snapshot = flow.projection_snapshot(connection=db)
        blocked = (
            snapshot["execution_block"]
            if runtime_ready
            else "bound_single_cell_runtime_required"
        )
        revision = _graph_revision(flow, connection=db)
        sources = list(islice(_sources(flow, db, state), _VIEW_LIMIT + 1))
        stale = defaultdict(list)
        for tombstone in snapshot["tombstones"]:
            stale[tombstone["step_id"]].append(
                {
                    "generation": tombstone["generation"],
                    "reason": tombstone["reason"],
                }
            )
        steps, stage_steps = [], []
        for row in snapshot["steps"]:
            step = state.nodes[row["step_id"]]
            if _related_stage(step):
                stage_steps.append(
                    {
                        "id": step.step_id,
                        "operation": step.operation_id,
                        "status": row["status"],
                    }
                )
            if step.operation_id not in SCRNA_OPERATIONS:
                continue
            has_report = (
                step.operation_id == _QC
                and type(executor) is TierAExecutor
                and (
                    _has_current_qc_report(
                        flow, db, state, step.step_id, row["generation"]
                    )
                )
            )
            steps.append(
                {
                    "id": step.step_id,
                    "operation": step.operation_id,
                    "branch_id": step.branch_id,
                    "biological_unit": step.profile.biological_units[0],
                    "generation": row["generation"],
                    "status": row["status"],
                    "reason": row["reason"],
                    "dependencies": row["dependencies"],
                    "stale_generations": stale[step.step_id][-_REPORT_ROWS:],
                    "qc_report_available": has_report,
                    "can_revise_qc": has_report
                    and blocked is None
                    and not has_thesis_scope(step),
                }
            )
    operations = _operations(executor, sources[:_VIEW_LIMIT], blocked)
    payload = {
        "session_id": flow.lineage_id,
        "status": "available",
        "revision": revision,
        "reason": blocked,
        "operations": operations,
        "steps": steps[:_VIEW_LIMIT],
        "stage_steps": stage_steps[:_VIEW_LIMIT],
        "sources": sources[:_VIEW_LIMIT],
        "inventory_truncated": max(len(steps), len(stage_steps), len(sources))
        > _VIEW_LIMIT,
        "action_reasons": {"queue": blocked},
        "actions": {
            "queue": any(operation["available"] for operation in operations),
            "revise_qc": any(step["can_revise_qc"] for step in steps[:_VIEW_LIMIT]),
        },
    }

    def guard(db):
        if _graph_revision(flow, connection=db) != revision:
            raise OperationConflict(
                "Adaptive graph changed during single-cell readback"
            )
        current = islice(_sources(flow, db, flow._state(db)), _VIEW_LIMIT + 1)
        if list(current) != sources:
            raise OperationConflict(
                "Single-cell source choices changed during readback"
            )

    _deliver(binding.delivery, binding.delivery.source_id, payload, guard)
    # The static schemas contain field names such as fdr, with no fitted values.
    for operation in operations:
        identity = operation["id"]
        operation["parameters_schema"] = (
            _DROPLET_SCHEMA
            if identity == "scrna_droplets"
            else _PARAMETERS[identity].model_json_schema()
        )
    return payload


def _deliver(delivery, source_id, payload, guard):
    broker = delivery.broker
    reserved = broker.reserve(delivery.context_id, source_id, payload, _guard=guard)
    broker.mark_attempted(reserved["reservation_id"], _guard=guard)


def _bounded_report(raw):
    report = _json(raw)
    preview = {key: report[key] for key in _QC_FIELDS if key in report}
    for field in ("thresholds", "warnings"):
        entries = report.get(field, [])
        preview[field] = entries[:_REPORT_ROWS]
        preview[field + "_total"] = len(entries)
    if "group_retention" in report:
        retention = report["group_retention"]
        preview["group_retention"] = {
            "group_key": retention["group_key"],
            "max_rate_gap": retention["max_rate_gap"],
            "groups": retention["groups"][:_REPORT_ROWS],
            "groups_total": len(retention["groups"]),
        }
    return preview


def _decision_sources(state):
    sources = {
        row["evidence"].get("controller_disclosure_source_id")
        for row in state.controller["decisions"]
    }
    if None in sources or "" in sources:
        raise OperationBlocked("current_controller_disclosure_binding_required")
    return tuple(sorted(sources))


def _qc_revision_impact(state: _State, step_id: str) -> dict[str, object]:
    # Invalidation only mutates these collections. Keep the authoritative
    # projection untouched while applying its exact revisit semantics.
    preview = replace(
        state,
        generations=state.generations.copy(),
        tombstones=state.tombstones.copy(),
        claim_stale=state.claim_stale.copy(),
        stale_versions=state.stale_versions.copy(),
        comparison_versions=state.comparison_versions.copy(),
    )
    preview.invalidate(
        [(step_id, "compute")], "diagnostic_revisit", retain_comparisons=True
    )
    impact = []
    for key, generation in sorted(state.generations.items()):
        if key == step_id:
            continue
        effect = None
        if preview.generations[key] != generation:
            effect = "superseded"
        elif (key, generation) in preview.claim_stale and (
            key, generation
        ) not in state.claim_stale:
            effect = "claim_stale"
        if effect:
            impact.append({"step_id": key, "generation": generation, "effect": effect})
    return {"steps": impact[:_REPORT_ROWS], "total": len(impact)}


def _qc_detail(app, binding, flow, step_id, generation):
    executor = binding.tier_a_executor
    if type(executor) is not TierAExecutor:
        raise PermissionError("QC readback requires the current bound Tier A store")
    with flow._read_connection() as db:
        state = flow._state(db)
        artifact, receipt, envelope = _qc_report(flow, db, state, step_id, generation)
        decision_sources = _decision_sources(state)
        graph_revision = _graph_revision(flow, connection=db)
        revision_impact = _qc_revision_impact(state, step_id)
    delivery = binding.delivery
    broker = delivery.broker
    source_id, dependencies = register_adaptive_output_source(
        flow, step_id, "qc_report", decision_sources,
        broker=broker, context_id=delivery.context_id, role="scientist",
    )
    with executor.open_output(artifact) as stream:
        raw = stream.read(_MAX_REPORT_BYTES + 1)

    if (
        len(raw) > _MAX_REPORT_BYTES
        or hashlib.sha256(raw).hexdigest() != artifact.sha256
    ):
        raise ValueError("QC report exceeds its byte limit or differs from its receipt")
    payload = {
        "session_id": flow.lineage_id,
        "step_id": step_id,
        "generation": generation,
        "receipt_sha256": receipt,
        "artifact_sha256": artifact.sha256,
        "parameters": load_single_cell_spec(executor, envelope).parameters.model_dump(
            mode="json"
        ),
        "report": _bounded_report(raw),
        "revision_impact": revision_impact,
    }
    witness = flow._read_projection(
        lambda db: request_digest(broker._source(db, source_id))
    )

    def guard(db):
        if _graph_revision(flow, connection=db) != graph_revision:
            raise OperationConflict("Adaptive graph changed during QC readback")
        state = flow._state(db)
        current = _qc_report(flow, db, state, step_id, generation)
        if current[:2] != (artifact, receipt):
            raise OperationConflict("The QC receipt changed during readback")
        if (
            _decision_sources(state) != decision_sources
            or request_digest(broker._source(db, source_id)) != witness
        ):
            raise OperationConflict("The QC evidence scope changed during readback")
        broker._query_dependencies(db, tuple(dependencies))

    _deliver(delivery, source_id, payload, guard)
    return payload


def curated_view(app, session_id, *, qc_step=None, generation=None):
    """Read current governed single-cell options or one reviewed QC subject."""
    if (qc_step is None) != (generation is None):
        raise ValueError("QC readback requires the exact step and generation")
    if qc_step is not None:
        _IDENTITY.validate_python(qc_step)
        if type(generation) is not int or generation < 1:
            raise ValueError("Use a positive QC generation")
    scientific = app.scientific
    if scientific is None:
        raise PermissionError(
            "Enable the scientific workspace for single-cell workflows"
        )
    scientific._plan(session_id)
    if _adaptive_ledger(scientific, session_id) is None:
        if qc_step is not None:
            raise PermissionError("QC readback requires an admitted adaptive lineage")
        return {"session_id": session_id, "status": "not_adaptive"}
    binding = ensure_adaptive_binding(app, session_id)
    if binding is None:
        if qc_step is not None:
            raise PermissionError("QC readback requires current evidence delivery")
        return {"session_id": session_id, "status": "current_delivery_required"}
    _, ledger = binding.require_owner(scientific)
    flow = _flow(binding, ledger)
    if qc_step is None:
        return _view(app, binding, flow)
    return _qc_detail(app, binding, flow, qc_step, generation)


def _current_source(flow, db, state, source):
    if type(source) is not dict or source.keys() != {"id", "port"}:
        raise ValueError("Each input requires an exact registered source")
    source_id, port = source["id"], source["port"]
    _IDENTITY.validate_python(source_id)
    if port is None:
        stored = state.artifacts.get(source_id)
        if stored is None or stored[1] is not None:
            raise OperationBlocked("single_cell_current_input_required")
        artifact = ArtifactVersion.model_validate(stored[0])
    else:
        _IDENTITY.validate_python(port)
        artifact = flow._current_use(db, source_id, port, "compute", None)
    if not _source_choice(
        flow,
        db,
        state,
        source_id,
        port,
        artifact.model_dump(mode="json"),
    ):
        raise OperationBlocked("single_cell_current_input_required")
    return (source_id, port), artifact


def _queue(flow, executor, db, state, request):
    operation_id = request["operation"]
    if type(operation_id) is not str or operation_id not in SCRNA_OPERATIONS:
        raise ValueError("Select a registered single-cell operation")
    if operation_id == DONOR_TRAJECTORY:
        raise OperationBlocked("thesis_answerability_required")
    declared = request["sources"]
    operation = SCRNA_OPERATIONS[operation_id]
    if type(declared) is not dict or declared.keys() != operation.inputs.keys():
        raise ValueError("Select exactly the registered input ports")
    if request["step_id"] in state.nodes:
        raise OperationConflict("This workflow step ID is already used")
    sources = {}
    basis = []
    for name, source in declared.items():
        sources[name], artifact = _current_source(flow, db, state, source)
        if name in _REVIEWED_INPUTS:
            current = next(
                (
                    identity
                    for identity, item in sorted(state.basis.items())
                    if item["kind"] == "review_basis"
                    and item["sha256"] == artifact.sha256
                ),
                None,
            )
            if current is None:
                raise OperationBlocked("single_cell_current_review_basis_required")
            basis.append(BasisUse(basis_id=current))
    step = executor.register_single_cell_step(
        {"operation": operation_id, "parameters": request["parameters"]},
        step_id=request["step_id"],
        branch_id=request["branch_id"],
        biological_unit=request["biological_unit"],
        sources=sources,
        basis=tuple(basis),
    )
    envelope = flow.candidate_envelope(step, actor=_ACTOR, connection=db)
    validate_single_cell_step(executor, step, envelope)
    flow.insert(step, connection=db)


def _revise_qc(flow, executor, db, state, request):
    step_id, generation = request["step_id"], request["generation"]
    if type(generation) is not int or generation < 1:
        raise ValueError("Use the exact positive QC generation")
    _, receipt, envelope = _qc_report(flow, db, state, step_id, generation)
    step = state.nodes[step_id]
    if has_thesis_scope(step):
        raise OperationBlocked("thesis_scope_revision_requires_existing_review")
    previous = load_single_cell_spec(executor, envelope)
    current = SingleCellStepSpec.model_validate(
        {
            "operation": _QC,
            "parameters": {
                **previous.parameters.model_dump(mode="json"),
                **request["parameters"],
            },
        }
    )
    if current.parameters == previous.parameters:
        raise ValueError("Change at least one QC setting before recording a revision")
    declaration = executor.register_single_cell_step(
        current,
        step_id=step_id,
        branch_id=step.branch_id,
        biological_unit=step.profile.biological_units[0],
        sources={
            link.port.name: (link.source_id, link.source_port) for link in step.inputs
        },
        basis=step.basis,
    )
    revised = step.model_copy(
        update={"parameters_sha256": declaration.parameters_sha256},
    )
    flow.revise(
        revised, reason="diagnostic_revisit", evidence_sha256=receipt, connection=db
    )


def curated_action(app, session_id, request):
    """Declare one typed checked observation or revise its exact QC thresholds."""
    name = request.get("action") if type(request) is dict else None
    if type(name) is not str or name not in _FIELDS or request.keys() != _FIELDS[name]:
        raise ValueError("Use the declared single-cell action fields")
    revision = request["expected_revision"]
    if (
        type(revision) is not int
        or revision < 0
        or type(request["parameters"]) is not dict
    ):
        raise ValueError("Use the current graph revision and a parameter object")
    _IDENTITY.validate_python(request["step_id"])
    if (
        request["allow_data_execution"] is not True
        or app.allow_data_execution is not True
    ):
        raise PermissionError(
            "Single-cell changes require a fresh data grant and startup access"
        )
    with app._lock:
        binding = ensure_adaptive_binding(app, session_id)
        if binding is None or type(binding.tier_a_executor) is not TierAExecutor:
            raise PermissionError(
                "Single-cell changes require the current bound Tier A runtime"
            )
        _, ledger = binding.require_owner(app.scientific)
        app._require_current_runtime()
        if session_id in app._adaptive_operations:
            raise OperationConflict("An adaptive Workbench action is active")
        executor = binding.tier_a_executor
        flow = _flow(binding, ledger)
        with flow.authority._execution_tx(session_id) as db:
            if _graph_revision(flow, connection=db) != revision:
                raise OperationConflict(
                    "Adaptive graph revision changed; refresh before retrying"
                )
            execution_block = flow._execution_block(db)
            if execution_block is not None:
                raise OperationBlocked(execution_block)
            state = flow._state(db)
            if name == "queue":
                _queue(flow, executor, db, state, request)
            else:
                _revise_qc(flow, executor, db, state, request)
            return {
                "session_id": session_id,
                "step_id": request["step_id"],
                "revision": _graph_revision(flow, connection=db),
            }
