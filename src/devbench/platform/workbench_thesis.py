"""Admit thesis work and publish packages from the Workbench's bound lineage."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import TYPE_CHECKING, Any

from devbench.analysis.thesis_exploratory_replay import (
    is_exploratory_thesis_artifact,
    open_exploratory_thesis_artifact,
)
from devbench.analysis.thesis_synthesis import ThesisSynthesisSpec, write_thesis_package
from devbench.core._files import hash_bounded_file, read_bounded_file
from devbench.core._hashing import decode_receipt_json
from devbench.core.contrast_design import TIER_A_BULK_OPERATION, TIER_A_BULK_OUTPUT
from devbench.core.procedure_profile import ADMITTED_RESEARCH
from devbench.core.rna_answerability import RuntimeEvidence
from devbench.core.thesis_blinded_review import BlindedAimAssessment
from devbench.engine.exploratory_oci import ExploratoryOCI
from devbench.engine.partition_authority import embargoed
from devbench.engine.tier_a_executor import TierAExecutor
from devbench.engine.workflow_operations import OperationBlocked, OperationConflict
from rna_kernels.scrna_upstream_contract import DONOR_TRAJECTORY

from .adaptive_workflow_state import AdaptiveStep
from .thesis_workflow import ThesisWorkflow, check_thesis_backend
from .workbench_adaptive import (
    WorkbenchAdaptiveBinding,
    _flow,
    _graph_revision,
    _readback_flow,
)

if TYPE_CHECKING:
    from devbench.analysis.thesis_synthesis_evidence import OpenArtifact
    from devbench.core.procedure_profile import ProcedureProfile
    from devbench.core.rna_answerability import (
        AnswerabilityRequest,
        AttestedMeasurement,
        DesignEvidence,
    )
    from devbench.core.trajectory_evidence import DonorTrajectoryEvidence

    from .scrna_general_pseudobulk import StagedPseudobulk
    from .thesis_aims import AimOutcome, ThesisAimLifecycle

_MAX_REVIEW_PACKET_BYTES = 16 * 1024**2
_MAX_REVIEW_ASSESSMENT_BYTES = 64 * 1024
_EMPTY_DIGEST = hashlib.sha256(b"").hexdigest()


def _workspace_path(path: Path, roots: tuple[Path, ...]) -> Path:
    if (
        not path.is_absolute()
        or path.resolve() != path
        or not any(path.is_relative_to(root) for root in roots)
    ):
        raise PermissionError("Use thesis package paths in the admitted workspace")
    return path


def _checked_thesis_lineage(app, session_id):
    binding = app._adaptive_bindings.get(session_id)
    if type(binding) is not WorkbenchAdaptiveBinding or app.scientific is None:
        raise PermissionError("Thesis binding requires a Workbench lineage")
    _, ledger = binding.require_owner(app.scientific)
    lifecycle = binding.thesis_lifecycle or getattr(
        binding.controller, "thesis_lifecycle", None
    )
    if (
        type(lifecycle) is not ThesisWorkflow
        or lifecycle.ledger.path != ledger.path
        or lifecycle.lineage_id != session_id
    ):
        raise PermissionError("Use the current Workbench thesis owner")
    if session_id in app._adaptive_operations:
        raise OperationConflict("An adaptive Workbench action is active")
    app._require_current_runtime()
    return _flow(binding, ledger), lifecycle, binding


def _checked_thesis_owner(app, session_id):
    flow, lifecycle, binding = _checked_thesis_lineage(app, session_id)
    if type(binding.tier_a_executor) is not TierAExecutor:
        raise PermissionError("Use the current Workbench thesis owner")
    return flow, lifecycle, binding.tier_a_executor


def _validate_expected_revision(expected_revision):
    if type(expected_revision) is not int or expected_revision < 0:
        raise ValueError("Use the current nonnegative Workbench graph revision")


def _check_graph_revision(flow, expected_revision, *, connection=None):
    if _graph_revision(flow, connection=connection) != expected_revision:
        raise OperationConflict("Adaptive graph revision changed")


def _review_readback(flow, aims, aim_id, db) -> dict[str, Any]:
    context = aims.scientific_context(connection=db)
    panel = next(
        row["blinded_review"] for row in context["research_aims"]
        if row["aim_id"] == aim_id
    )
    return {"revision": _graph_revision(flow, connection=db), "panel": panel}


def open_workbench_blinded_review(
    app,
    session_id: str,
    *,
    aim_id: str,
    version: int,
    outcome: AimOutcome,
    masked_packet: Path,
    reviewers: tuple[str, ...],
    actor: str,
    rationale: str,
    expected_revision: int,
) -> dict[str, Any]:
    """Freeze an operator-prepared local packet for an approved aim version."""
    _validate_expected_revision(expected_revision)
    with app._lock:
        flow, lifecycle, _ = _checked_thesis_lineage(app, session_id)
        _check_graph_revision(flow, expected_revision)
        path = _workspace_path(masked_packet, (app.workspace, app.scientific.root))
        digest = hash_bounded_file(path, _MAX_REVIEW_PACKET_BYTES)
        if digest == _EMPTY_DIGEST:
            raise ValueError("The masked review packet must contain evidence")
        with flow.authority._execution_tx(session_id) as db:
            _check_graph_revision(flow, expected_revision, connection=db)
            if embargoed(db, digest):
                raise PermissionError("Protected evaluation data cannot enter review")
            lifecycle.aims.open_blinded_review(
                aim_id, version=version, outcome=outcome, masked_packet_sha256=digest,
                reviewers=reviewers, actor=actor, rationale=rationale, connection=db,
            )
            return _review_readback(flow, lifecycle.aims, aim_id, db)


def submit_workbench_blinded_review(
    app,
    session_id: str,
    *,
    aim_id: str,
    version: int,
    assessment_file: Path,
    expected_revision: int,
) -> dict[str, Any]:
    """Admit an independently verified local assessment through the bound verifier."""
    _validate_expected_revision(expected_revision)
    with app._lock:
        flow, lifecycle, _ = _checked_thesis_lineage(app, session_id)
        _check_graph_revision(flow, expected_revision)
        path = _workspace_path(assessment_file, (app.workspace, app.scientific.root))
        raw = read_bounded_file(path, _MAX_REVIEW_ASSESSMENT_BYTES)
        digest = hashlib.sha256(raw).hexdigest()
        assessment = BlindedAimAssessment.model_validate(decode_receipt_json(raw))
        with flow.authority._execution_tx(session_id) as db:
            _check_graph_revision(flow, expected_revision, connection=db)
            if embargoed(db, digest) or embargoed(db, assessment.receipt_sha256):
                raise PermissionError("Protected evaluation data cannot enter review")
            lifecycle.aims.submit_blinded_review(
                aim_id, version=version, assessment=assessment, connection=db,
            )
            return {
                **_review_readback(flow, lifecycle.aims, aim_id, db),
                "assessment_file_sha256": digest,
            }


def bind_workbench_donor_trajectory(
    app,
    session_id: str,
    *,
    step: AdaptiveStep,
    aim_id: str,
    request: AnswerabilityRequest,
    measurements: tuple[AttestedMeasurement, ...],
    design: DesignEvidence | None,
    expected_revision: int,
    trajectory_evidence: DonorTrajectoryEvidence | None = None,
) -> dict[str, Any]:
    """Prepare an existing curated candidate using the current host verifier.

    This is an in-process coordinator action. Workbench advance owns dispatch.
    """
    if step.operation_id != DONOR_TRAJECTORY or request.question != "donor_trajectory":
        raise ValueError("Use the registered donor trajectory action and question")
    _validate_expected_revision(expected_revision)
    with app._lock:
        flow, lifecycle, executor = _checked_thesis_owner(app, session_id)
        runtime = RuntimeEvidence(
            question=request.question,
            tier="tier_a",
            operation_id=DONOR_TRAJECTORY,
            runtime_sha256=executor.environment_sha256,
        )
        with flow.authority._execution_tx(session_id) as db:
            _check_graph_revision(flow, expected_revision, connection=db)
            scoped, decision = lifecycle.bind_step(
                step,
                aim_id=aim_id,
                request=request,
                measurements=measurements,
                backend_id=executor.backend_id,
                design=design,
                runtime=runtime,
                trajectory_evidence=trajectory_evidence,
                connection=db,
            )
            step_id = None
            if decision.status == "ready":
                envelope = flow.candidate_envelope(
                    scoped, actor="workbench-operator", connection=db
                )
                executor.validate(envelope)
                check_thesis_backend(envelope, executor.backend_id)
                flow.insert(scoped, connection=db)
                step_id = scoped.step_id
            return {
                "step_id": step_id,
                "decision": decision.model_dump(mode="json"),
                "revision": _graph_revision(flow, connection=db),
            }


def _pseudobulk_scope(staged, request, measurements, design):
    plan = staged.pipeline
    if plan is None:
        raise OperationBlocked("pseudobulk_capsule_held")
    assay = {"scrna": "single_cell_rna_seq", "snrna": "single_nucleus_rna_seq"}
    if (
        request.question != "differential_cell_state"
        or request.assay != assay.get(staged.summary["specification"]["assay"])
    ):
        raise OperationBlocked("pseudobulk_question_scope_changed")
    if any(
        item.kind == "donor_pseudobulk_counts" and (
            item.artifact_sha256 != plan.expression_sha256
            or item.feature_axis_sha256 != staged.summary["feature_ids_sha256"]
        )
        for item in measurements
    ) or (design is not None and design.biological_unit != plan.biological_unit):
        raise OperationBlocked("pseudobulk_measurement_scope_changed")
    return plan


def _pseudobulk_step(plan, profile, step_id, branch_id, executor, parameters, inputs):
    return AdaptiveStep.model_validate({
        "step_id": step_id,
        "branch_id": branch_id,
        "operation_id": TIER_A_BULK_OPERATION,
        "profile": profile,
        "environment_sha256": executor.environment_sha256,
        "parameters_sha256": parameters,
        "inputs": [
            {"port": {"name": port, "artifact_kind": artifact.kind},
             "source_id": artifact.version_id}
            for port, artifact in inputs.items()
        ],
        "outputs": [{"name": TIER_A_BULK_OUTPUT, "artifact_kind": "table"}],
        "purpose": ADMITTED_RESEARCH,
        "regime": plan.design.regime,
        "primary": plan.family.primary,
        "comparison": not plan.family.primary,
    })


def _pseudobulk_qc_sources(flow, db, staged):
    from devbench.core.scrna_qc_selection import QCSelectionBinding

    from .scrna_general_pseudobulk import retained_qc_label_files
    from .scrna_qc_handoff import (
        QC_LABEL_BASIS_PREFIX,
        current_qc_parents,
        retain_qc_source_basis,
    )

    selection = staged.summary.get("qc_selection")
    if selection is None:
        return (), ()
    parents = current_qc_parents(
        flow, QCSelectionBinding.model_validate(selection), connection=db,
    )
    proof = staged.summary["label_qualification"]
    files = retained_qc_label_files(staged.summary)
    if proof is not None and not files:
        # Legacy pair capsules retain their original input-based replay.
        return parents, ()
    labels = sorted({*files.values(), proof["source_sha256"]}) if files else []
    if any(embargoed(db, digest) for digest in labels):
        raise PermissionError("Pseudobulk label evidence contains protected inputs")
    return parents, (
        retain_qc_source_basis(flow, db, staged.provenance_sha256),
        *(retain_qc_source_basis(flow, db, digest, prefix=QC_LABEL_BASIS_PREFIX)
          for digest in labels),
    )


def _retain_pseudobulk_inputs(flow, db, staged, inputs):
    from .scrna_qc_handoff import QC_LABEL_BASIS_PREFIX

    identities = (staged.provenance_sha256, staged.summary["source_sha256"],
                  *staged.summary["artifact_sha256"].values())
    if any(embargoed(db, digest) for digest in identities):
        raise PermissionError("Pseudobulk capsule contains protected inputs")
    parents, basis = _pseudobulk_qc_sources(flow, db, staged)
    required_basis = (
        set(basis) if any(key.startswith(QC_LABEL_BASIS_PREFIX) for key in basis)
        else set()
    )
    state = flow._state(db)
    for artifact in inputs.values():
        recorded = state.artifacts.get(artifact.version_id)
        if recorded is None:
            flow.register_artifact(
                artifact, parents=parents, basis=basis, connection=db,
            )
        elif (
            recorded != (artifact.model_dump(mode="json"), None)
            or tuple(state.derived_parents.get(artifact.version_id, ())) != parents
            or not required_basis.issubset(state.sources.get(artifact.version_id, ()))
        ):
            raise OperationConflict("Pseudobulk input identity differs from history")


def bind_workbench_pseudobulk(
    app,
    session_id: str,
    *,
    staged: StagedPseudobulk,
    step_id: str,
    branch_id: str,
    profile: ProcedureProfile,
    aim_id: str,
    request: AnswerabilityRequest,
    measurements: tuple[AttestedMeasurement, ...],
    design: DesignEvidence | None,
    expected_revision: int,
) -> dict[str, Any]:
    """Bind a verified local pseudobulk capsule before separate research review."""
    from devbench.engine.mounted_kernel_oci import require_mounted_pipeline_runtime

    from .scrna_general_pseudobulk import register_general_pseudobulk

    _validate_expected_revision(expected_revision)
    with app._lock:
        flow, lifecycle, executor = _checked_thesis_owner(app, session_id)
        _check_graph_revision(flow, expected_revision)
        _workspace_path(staged.directory, (app.workspace, app.scientific.root))
        plan = _pseudobulk_scope(staged, request, measurements, design)
        require_mounted_pipeline_runtime(
            plan, executor.mounted_image_id, representation="raw_counts_h5",
        )
        parameters, inputs = register_general_pseudobulk(
            executor, staged, workflow=flow,
        )
        preflight = executor.mounted_design_preflight(parameters)
        step = _pseudobulk_step(
            plan, profile, step_id, branch_id, executor, parameters, inputs,
        )
        runtime = RuntimeEvidence(
            question=request.question, tier="tier_a",
            operation_id=TIER_A_BULK_OPERATION,
            runtime_sha256=executor.environment_sha256,
        )
        with flow.authority._execution_tx(session_id) as db:
            _check_graph_revision(flow, expected_revision, connection=db)
            _retain_pseudobulk_inputs(flow, db, staged, inputs)
            candidate, decision = lifecycle.bind_step(
                step, aim_id=aim_id, request=request, measurements=measurements,
                backend_id=executor.backend_id, design=design, runtime=runtime,
                connection=db,
            )
            ready = decision.status == "ready"
            if ready:
                scope = lifecycle.require_step(
                    db, candidate, inputs=inputs, basis=flow._state(db).basis,
                    environment=executor.environment_sha256,
                )
                check_thesis_backend(
                    {"step": candidate.model_dump(mode="json"), "thesis_scope": scope},
                    executor.backend_id, validated_specification=plan,
                )
                flow.insert(
                    candidate, connection=db, evidence_sha256=staged.provenance_sha256,
                )
            return {
                "step_id": candidate.step_id if ready else None,
                "decision": decision.model_dump(mode="json"),
                "revision": _graph_revision(flow, connection=db),
                "capsule_sha256": staged.provenance_sha256,
                "structural_design_sha256": preflight["sha256"],
            }


def write_workbench_thesis_package(
    app,
    session_id: str,
    spec: ThesisSynthesisSpec,
    *,
    root: Path,
    answer_schema: Path,
    aim_lifecycle: ThesisAimLifecycle | None = None,
    open_artifact: OpenArtifact | None = None,
) -> dict[str, Any]:
    """Use the live Workbench authorization and current, receipt-bound results."""
    binding = app._adaptive_bindings.get(session_id)
    if binding is None or app.scientific is None:
        raise PermissionError("Thesis synthesis requires an admitted Workbench lineage")
    scientific = app.scientific
    _, ledger = binding.require_owner(scientific)
    roots = (app.workspace, scientific.root)
    destination = _workspace_path(root, roots)
    schema = _workspace_path(answer_schema, roots)
    tier_a = binding.tier_a_executor
    exploratory = (
        binding.executor if type(binding.executor) is ExploratoryOCI else None
    )
    if aim_lifecycle is None:
        thesis = binding.thesis_lifecycle or getattr(
            binding.controller, "thesis_lifecycle", None
        )
        aim_lifecycle = thesis.aims if thesis is not None else None
    flow = _readback_flow(scientific, binding, ledger)
    if open_artifact is None:
        open_artifact = _bound_artifact_reader(flow, tier_a, exploratory)
    return write_thesis_package(
        destination,
        flow,
        spec,
        answer_schema=schema,
        open_artifact=open_artifact,
        aim_lifecycle=aim_lifecycle,
        tier_a_executor=tier_a,
        exploratory_executor=exploratory,
    )


def _bound_artifact_reader(flow, tier_a, exploratory) -> OpenArtifact:
    if tier_a is None and exploratory is None:
        raise PermissionError("Thesis synthesis requires a bound artifact reader")

    def open_artifact(artifact):
        if exploratory is not None and is_exploratory_thesis_artifact(artifact):
            return open_exploratory_thesis_artifact(flow, exploratory, artifact)
        if tier_a is None:
            raise PermissionError("Artifact is outside the bound thesis executor")
        return tier_a.open_output(artifact)

    return open_artifact
