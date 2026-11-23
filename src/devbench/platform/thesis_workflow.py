"""Verify prospective aim and measurement custody for adaptive execution."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from contextlib import nullcontext
from sqlite3 import Connection, Cursor, Row
from typing import Any, Literal

from pydantic import Field

from devbench.adaptive.models import AdaptiveRecord
from devbench.core._hashing import canonical_sha256
from devbench.core.contrast_design import PipelineSpec
from devbench.core.joint_bh import JOINT_BH_INPUT, JointBHFitScope, JointBHMember
from devbench.core.rna_answerability import (
    AnswerabilityDecision,
    AnswerabilityRequest,
    AttestedMeasurement,
    DesignEvidence,
    RuntimeEvidence,
    assess_answerability,
)
from devbench.core.thesis_blinded_review import BlindedReviewVerifier
from devbench.core.trajectory_evidence import DonorTrajectoryEvidence
from devbench.engine.scientific_authority import (
    _check_lineage,
    _projection_event,
    require_same_ledger_transaction,
)
from devbench.engine.workflow_operations import (
    OperationBlocked,
    OperationConflict,
    WorkflowOperationLedger,
    _digest,
    _json,
)
from rna_kernels.scrna_upstream_contract import DONOR_TRAJECTORY

from .adaptive_workflow_state import (
    AdaptiveStep,
    ArtifactVersion,
    BasisUse,
    Digest,
    Identity,
)
from .thesis_aims import AimBinding, ThesisAimLifecycle
from .thesis_joint_bh import ThesisJointBHRegistry

_AIM_PREFIX = "thesis-aim:"
_SCOPE_PREFIX = "thesis-answerability:"
_RECORD_PREFIX = "thesis-answerability-"
_STREAM = "thesis_answerability"
_MAX_EVIDENCE_RECORDS = 1024
_CONFIRMATORY = {"admitted_research_inference", "qualified_inference"}
_DOCUMENT = dict[str, Any]
Attestation = (
    AttestedMeasurement | DesignEvidence | RuntimeEvidence | DonorTrajectoryEvidence
)
AttestationVerifier = Callable[[Connection, Attestation], bool]


class _AnswerabilityBinding(AdaptiveRecord):
    format: Literal["devbench.thesis-answerability-binding/1"] = (
        "devbench.thesis-answerability-binding/1"
    )
    aim: AimBinding
    step_id: Identity
    step_sha256: Digest
    inputs_sha256: Digest
    backend_id: Identity
    decision: AnswerabilityDecision
    trajectory_evidence: DonorTrajectoryEvidence | None = Field(
        default=None, exclude_if=lambda value: value is None
    )


def _direct_refs(step: AdaptiveStep, prefix: str) -> tuple[str, ...]:
    return tuple(ref.basis_id for ref in step.basis if ref.basis_id.startswith(prefix))


def has_thesis_scope(step: AdaptiveStep) -> bool:
    return bool(_direct_refs(step, _AIM_PREFIX) or _direct_refs(step, _SCOPE_PREFIX))


def _step_identity(step: AdaptiveStep) -> str:
    """Bind everything except the scope pointer itself."""
    step_body = step.model_dump(mode="json")
    step_body["basis"] = [
        ref
        for ref in step_body["basis"]
        if not ref["basis_id"].startswith(_SCOPE_PREFIX)
    ]
    return canonical_sha256(step_body)


def _inputs_identity(inputs: Mapping[str, ArtifactVersion]) -> str:
    return canonical_sha256(
        {name: item.model_dump(mode="json") for name, item in inputs.items()}
    )


def _records(
    db: Connection,
    lineage: str,
    record_id: str | None = None,
) -> Cursor:
    return db.execute(
        """SELECT r.lineage_id,r.revision,r.record_id,r.kind,r.binding_id,
          r.publication_unit,
          CASE WHEN length(CAST(r.payload AS BLOB)) <= 65536 THEN r.payload END
            AS payload,
          r.sha256,o.sha256 AS outbox_digest FROM scientific_records r
          LEFT JOIN scientific_outbox o ON r.lineage_id=o.lineage_id
            AND r.revision=o.revision
          WHERE r.lineage_id=? AND r.kind='execution_event' AND r.record_id LIKE ?
          ORDER BY r.revision LIMIT ?""",
        (lineage, record_id or _RECORD_PREFIX + "%", _MAX_EVIDENCE_RECORDS + 1),
    )


def _verified_proof(row: Row) -> tuple[_AnswerabilityBinding, str]:
    verified = _projection_event(row)["record"]
    if verified["event"] != _STREAM:
        raise OperationConflict("Invalid thesis answerability event namespace")
    proof = _AnswerabilityBinding.model_validate_json(_json(verified["state"]))
    identity = canonical_sha256(proof.model_dump(mode="json"))
    if verified["record_id"] != _RECORD_PREFIX + identity:
        raise OperationConflict(
            "Thesis answerability proof does not match its identity"
        )
    return proof, identity


def _route_issue(
    decision: AnswerabilityDecision,
    step: AdaptiveStep,
    environment: str,
) -> str | None:
    if decision.status != "ready":
        return decision.reason
    if decision.execution_route == "intake":
        if (
            step.operation_id != "multi_organism_inventory"
            or step.purpose != "checked_observation"
            or step.oci_method_version_id is not None
        ):
            return "intake_scope_required"
        return None
    runtime = decision.runtime
    if (
        runtime is None
        or runtime.runtime_sha256 != environment
        or runtime.operation_id != step.operation_id
    ):
        return "runtime_binding_changed"
    if (decision.execution_route == "tier_b") != (
        step.oci_method_version_id is not None
    ):
        return "tier_binding_changed"
    return None


def _check_confirmatory(
    binding: AimBinding,
    step: AdaptiveStep,
    joint_plans: ThesisJointBHRegistry,
    db: Connection,
) -> JointBHFitScope | None:
    if step.purpose not in _CONFIRMATORY:
        return None
    family = binding.multiplicity
    if family is None or step.operation_id != "bulk_condition_contrast":
        raise OperationBlocked("thesis_confirmatory_family_binding_unavailable")
    if family.aim_ids != (binding.aim_id,):
        retained = joint_plans.read(family.family_id, connection=db)
        member = next(
            item for item in retained.plan.members if item.aim_id == binding.aim_id
        )
        if step.comparison and not step.primary:
            if member.step_id is None:
                raise OperationBlocked("thesis_joint_bh_step_unbound")
            if any(
                step.step_id == item.step_id
                or step.parameters_sha256 == item.parameters_sha256
                for item in retained.plan.members
            ):
                raise OperationBlocked("thesis_joint_bh_comparison_reuses_primary")
        else:
            if member.parameters_sha256 != step.parameters_sha256:
                raise OperationBlocked("thesis_joint_bh_parameters_unbound")
            if member.step_id != step.step_id:
                raise OperationBlocked("thesis_joint_bh_step_unbound")
        return JointBHFitScope(
            family_id=family.family_id,
            plan_sha256=family.hypotheses_sha256,
            member=member,
        )
    return None


class ThesisWorkflow:
    """Trusted intake of assay attestations for one adaptive lineage."""

    def __init__(
        self,
        ledger: WorkflowOperationLedger,
        lineage_id: str,
        *,
        verify_attestation: AttestationVerifier | None = None,
        authorize_operator: Callable[[str], bool] | None = None,
        verify_blinded_review: BlindedReviewVerifier | None = None,
    ) -> None:
        from .adaptive_workflow import AdaptiveWorkflow

        _check_lineage(lineage_id)
        self.ledger = ledger
        self.lineage_id = lineage_id
        self.flow = AdaptiveWorkflow(ledger, lineage_id)
        self.aims = ThesisAimLifecycle(
            ledger, lineage_id, authorize_operator=authorize_operator,
            verify_blinded_review=verify_blinded_review,
        )
        self._verify_attestation = verify_attestation

    def bind_step(
        self,
        step: AdaptiveStep,
        *,
        aim_id: str,
        request: AnswerabilityRequest,
        measurements: tuple[AttestedMeasurement, ...],
        backend_id: str,
        design: DesignEvidence | None = None,
        runtime: RuntimeEvidence | None = None,
        trajectory_evidence: DonorTrajectoryEvidence | None = None,
        expected_revision: int | None = None,
        connection: Connection | None = None,
    ) -> tuple[AdaptiveStep, AnswerabilityDecision]:
        """Record a ready or refused question and return its scoped candidate."""
        self.ledger._writable()
        _check_lineage(self.lineage_id, expected_revision)
        if has_thesis_scope(step):
            raise ValueError("Bind a candidate without reserved thesis references")
        if connection is not None:
            require_same_ledger_transaction(connection, self.ledger)
        transaction = (
            self.flow.authority._execution_tx(self.lineage_id)
            if connection is None
            else nullcontext(connection)
        )
        with transaction as db:
            if (
                expected_revision is not None
                and self.flow.authority._revision(db, self.lineage_id)
                != expected_revision
            ):
                raise OperationConflict(
                    "Thesis workflow revision changed before binding"
                )
            binding = self.aims.require_ready(aim_id, connection=db)
            scoped = _with_basis(step, binding.basis_id)
            state = self.flow._state(db)
            revision = "design_change" if step.step_id in state.nodes else None
            self.flow._validate_step_edit(state, scoped, revision)
            inputs = self.flow._step_inputs(state, scoped)
            self._verify_inputs(db, inputs, measurements, design, runtime)
            decision = assess_answerability(
                request,
                measurements=measurements,
                design=design,
                runtime=runtime,
            )
            if trajectory_evidence is not None:
                self._approve_trajectory(
                    db, binding, scoped, inputs, decision, measurements,
                    trajectory_evidence,
                )
            proof = _AnswerabilityBinding(
                aim=binding,
                step_id=scoped.step_id,
                step_sha256=_step_identity(scoped),
                inputs_sha256=_inputs_identity(inputs),
                backend_id=backend_id,
                decision=decision,
                trajectory_evidence=trajectory_evidence,
            )
            identity = canonical_sha256(proof.model_dump(mode="json"))
            bound = _with_basis(scoped, _SCOPE_PREFIX + identity)
            self._retain(db, proof, identity)
            return bound, decision

    def _approve_trajectory(
        self,
        db: Connection,
        aim: AimBinding,
        step: AdaptiveStep,
        inputs: Mapping[str, ArtifactVersion],
        decision: AnswerabilityDecision,
        measurements: tuple[AttestedMeasurement, ...],
        evidence: DonorTrajectoryEvidence,
    ) -> None:
        _check_trajectory_subject(step, decision, inputs, measurements, evidence)
        self._require_trajectory_aim(db, aim, inputs, evidence)
        if self._verify_attestation is None or (
            self._verify_attestation(db, evidence) is not True
        ):
            raise OperationBlocked("trajectory_evidence_unverified")

    def _require_trajectory_aim(
        self,
        db: Connection,
        aim: AimBinding,
        inputs: Mapping[str, ArtifactVersion],
        evidence: DonorTrajectoryEvidence,
    ) -> None:
        family = aim.multiplicity
        if family is None or family.aim_ids != (aim.aim_id,):
            raise OperationBlocked("trajectory_requires_single_aim_bh_family")
        self.aims.require_step_basis(
            {aim.basis_id: {"sha256": aim.basis_sha256}},
            input_sha256s={item.sha256 for item in inputs.values()},
            confirmatory_fit=True,
            hypotheses_sha256=evidence.hypotheses_sha256(),
            connection=db,
        )

    def _verify_inputs(
        self,
        db: Connection,
        inputs: Mapping[str, ArtifactVersion],
        measurements: tuple[AttestedMeasurement, ...],
        design: DesignEvidence | None,
        runtime: RuntimeEvidence | None,
    ) -> None:
        if len(measurements) > 128:
            raise ValueError("Thesis answerability measurement inventory exceeds 128")
        input_digests = {item.sha256 for item in inputs.values()}
        if any(item.artifact_sha256 not in input_digests for item in measurements):
            raise OperationBlocked("thesis_measurement_not_a_pinned_input")
        if self._verify_attestation is None:
            raise PermissionError(
                "Thesis inputs require trusted attestation verification"
            )
        for record in (*measurements, design, runtime):
            if record is not None and self._verify_attestation(db, record) is not True:
                raise OperationBlocked("thesis_input_attestation_unverified")

    def _retain(
        self, db: Connection, proof: _AnswerabilityBinding, identity: str
    ) -> None:
        state = proof.model_dump(mode="json")
        record_id = _RECORD_PREFIX + identity
        prior = _records(db, self.lineage_id, record_id).fetchone()
        if prior is not None:
            retained, _ = _verified_proof(prior)
            if retained != proof:
                raise OperationConflict(
                    "Thesis answerability proof differs from history"
                )
            return
        count = db.execute(
            """SELECT count(*) FROM scientific_records WHERE lineage_id=?
               AND kind='execution_event' AND record_id LIKE ?""",
            (self.lineage_id, _RECORD_PREFIX + "%"),
        ).fetchone()[0]
        if count >= _MAX_EVIDENCE_RECORDS:
            raise OperationBlocked("thesis_answerability_record_bound")
        record = {
            "kind": "execution_event",
            "record_id": record_id,
            "event": _STREAM,
            "state": state,
            "state_sha256": _digest(_json(state)),
        }
        db.execute("SAVEPOINT thesis_answerability")
        try:
            self.flow.authority._append_execution_record(db, self.lineage_id, record)
            self.flow._event(
                db,
                "basis",
                {
                    "basis_id": _SCOPE_PREFIX + identity,
                    "kind": "fact",
                    "sha256": identity,
                },
            )
        except BaseException:
            db.execute("ROLLBACK TO thesis_answerability")
            raise
        finally:
            db.execute("RELEASE thesis_answerability")

    def require_step(
        self,
        db: Connection,
        step: AdaptiveStep,
        *,
        inputs: Mapping[str, ArtifactVersion],
        basis: Mapping[str, _DOCUMENT],
        environment: str,
    ) -> _DOCUMENT:
        """Recheck the current aim and the entire pinned dispatch envelope."""
        require_same_ledger_transaction(db, self.ledger)
        aim_refs = _direct_refs(step, _AIM_PREFIX)
        scope_refs = _direct_refs(step, _SCOPE_PREFIX)
        if len(aim_refs) != 1 or len(scope_refs) != 1:
            raise OperationBlocked("thesis_answerability_required")
        (aim,) = self.aims.require_step_basis(
            {key: basis[key] for key in aim_refs},
            input_sha256s={item.sha256 for item in inputs.values()},
            confirmatory_fit=step.purpose in _CONFIRMATORY,
            connection=db,
        )
        scope_id = scope_refs[0]
        identity = scope_id.removeprefix(_SCOPE_PREFIX)
        if basis[scope_id]["sha256"] != identity:
            raise OperationBlocked("thesis_answerability_basis_changed")
        row = _records(db, self.lineage_id, _RECORD_PREFIX + identity).fetchone()
        if row is None:
            raise OperationBlocked("thesis_answerability_proof_missing")
        proof, _ = _verified_proof(row)
        if (
            proof.aim != aim
            or proof.step_sha256 != _step_identity(step)
            or proof.inputs_sha256 != _inputs_identity(inputs)
        ):
            raise OperationBlocked("thesis_answerability_scope_changed")
        if (issue := _route_issue(proof.decision, step, environment)) is not None:
            raise OperationBlocked("thesis_answerability_" + issue)
        if proof.trajectory_evidence is not None:
            self._require_trajectory_aim(db, aim, inputs, proof.trajectory_evidence)
        joint = _check_confirmatory(aim, step, ThesisJointBHRegistry(self.aims), db)
        envelope_scope = _execution_binding(proof, identity)
        if joint is not None:
            role = (
                "joint_bh_reference"
                if step.comparison and not step.primary
                else "joint_bh"
            )
            envelope_scope[role] = joint.model_dump(mode="json")
        return envelope_scope

    def scientific_context(self, *, connection: Connection | None = None) -> _DOCUMENT:
        """Coordinator-only state with verified decision summaries."""
        if connection is not None:
            require_same_ledger_transaction(connection, self.ledger)
            return self._context(connection)
        return self.ledger._read(self._context)

    def _context(self, db: Connection) -> _DOCUMENT:
        if not db.in_transaction:
            db.execute("BEGIN")
        context = self.aims.scientific_context(connection=db)
        summaries = []
        for row in _records(db, self.lineage_id):
            proof, identity = _verified_proof(row)
            verdict = proof.decision.model_dump(mode="json")
            evidence = proof.trajectory_evidence
            summaries.append(
                {
                    "aim_id": proof.aim.aim_id,
                    "aim_version": proof.aim.version,
                    "step_id": proof.step_id,
                    "proof_sha256": identity,
                    "ledger_revision": row["revision"],
                    **(
                        {
                            "trajectory_review": {
                                "evidence_sha256": evidence.sha256(),
                                "review_receipt_sha256": evidence.review_receipt_sha256,
                                "reviewer": evidence.reviewer,
                            }
                        }
                        if evidence is not None else {}
                    ),
                    **{
                        key: verdict[key]
                        for key in (
                            "request",
                            "status",
                            "reason",
                            "missing_measurements",
                            "execution_route",
                        )
                    },
                }
            )
        if len(summaries) > _MAX_EVIDENCE_RECORDS:
            raise OperationConflict("Thesis answerability inventory changed")
        return {**context, "answerability": summaries}


def _with_basis(step: AdaptiveStep, identity: str) -> AdaptiveStep:
    return AdaptiveStep.model_validate(
        {
            **step.model_dump(),
            "basis": (*step.basis, BasisUse(basis_id=identity)),
        }
    )


def _check_trajectory_subject(
    step: AdaptiveStep,
    decision: AnswerabilityDecision,
    inputs: Mapping[str, ArtifactVersion],
    measurements: tuple[AttestedMeasurement, ...],
    evidence: DonorTrajectoryEvidence,
) -> None:
    if (
        step.operation_id != DONOR_TRAJECTORY
        or step.purpose != "checked_observation"
        or decision.status != "ready"
        or decision.request.question != "donor_trajectory"
        or decision.execution_route != "tier_a"
    ):
        raise OperationBlocked("trajectory_evidence_requires_donor_candidate_step")
    if {port: item.sha256 for port, item in inputs.items()} != {
        "trajectory_expression": evidence.normalized_expression_sha256,
        "trajectory_coordinates": evidence.coordinates_sha256,
        "trajectory_spec": evidence.specification_sha256,
    }:
        raise OperationBlocked("trajectory_evidence_subject_changed")
    selected = {item.kind: item for item in measurements}
    normalized = selected["normalized_expression"]
    axis = selected["inferred_pseudotime"]
    if (
        normalized.artifact_sha256 != evidence.normalized_expression_sha256
        or normalized.receipt_sha256 != evidence.normalization_receipt_sha256
        or normalized.feature_axis_sha256 != evidence.feature_axis_sha256
        or axis.artifact_sha256 != evidence.coordinates_sha256
        or axis.receipt_sha256 != evidence.axis_receipt_sha256
        or decision.design_receipt_sha256 != evidence.design_receipt_sha256
    ):
        raise OperationBlocked("trajectory_evidence_receipts_changed")


def _execution_binding(proof: _AnswerabilityBinding, identity: str) -> _DOCUMENT:
    family = proof.aim.multiplicity
    runtime = proof.decision.runtime
    return {
        "aim_id": proof.aim.aim_id,
        "aim_version": proof.aim.version,
        "proof_sha256": identity,
        "backend_id": proof.backend_id,
        "route": proof.decision.execution_route,
        "hypotheses_sha256": family.hypotheses_sha256 if family is not None else None,
        "method_contract_sha256": runtime.method_contract_sha256 if runtime else None,
        **(
            {"trajectory_evidence": proof.trajectory_evidence.model_dump(mode="json")}
            if proof.trajectory_evidence is not None
            else {}
        ),
    }


def compiled_hypotheses_sha256(specification: PipelineSpec) -> str:
    """Hash the ordered attempted test universe of the verified compiled family."""
    return canonical_sha256(specification.family.model_dump(mode="json"))


def check_thesis_backend(
    envelope: _DOCUMENT,
    backend_id: str,
    *,
    validated_specification: Any = None,
    validated_contract: _DOCUMENT | None = None,
) -> None:
    scope = envelope.get("thesis_scope")
    if scope is None:
        return
    if backend_id != scope["backend_id"]:
        raise OperationBlocked("thesis_answerability_backend_changed")
    if scope["route"] == "tier_b" and (
        validated_contract is None
        or canonical_sha256(validated_contract) != scope["method_contract_sha256"]
    ):
        raise OperationBlocked("thesis_answerability_method_contract_changed")
    if envelope["step"]["purpose"] in _CONFIRMATORY:
        _check_compiled_family(envelope, scope, validated_specification)


def _check_compiled_family(
    envelope: _DOCUMENT, scope: _DOCUMENT, specification: Any
) -> None:
    if type(specification) is not PipelineSpec:
        raise OperationBlocked("thesis_aim_confirmatory_family_unbound")
    hypotheses = scope["hypotheses_sha256"]
    if "joint_bh" in scope:
        joint = JointBHFitScope.model_validate(scope["joint_bh"])
        expected = JointBHMember.from_pipeline(
            scope["aim_id"], specification, step_id=envelope["step"]["step_id"]
        )
        valid = (
            "joint_bh_reference" not in scope
            and envelope["step"].get("primary") is True
            and specification.family.primary
            and joint.plan_sha256 == hypotheses
            and joint.member == expected
        )
    elif "joint_bh_reference" in scope:
        valid = _matches_joint_comparison(envelope["step"], scope, specification)
    else:
        companion = _comparison_primary_family_sha256(envelope["step"], specification)
        valid = compiled_hypotheses_sha256(specification) == hypotheses or (
            companion is not None and companion == hypotheses
        )
    if not valid:
        raise OperationBlocked("thesis_aim_confirmatory_family_unbound")


def _comparison_primary_family_sha256(step, specification: PipelineSpec) -> str | None:
    family = specification.family
    if (
        family.primary
        or step.get("primary") is not False
        or step.get("comparison") is not True
    ):
        return None
    # Role and its allocated weight can differ. The exact tested family cannot.
    primary = family.model_copy(
        update={"primary": True, "weight": None if family.weight is None else "1"}
    )
    return canonical_sha256(primary.model_dump(mode="json"))


def _matches_joint_comparison(step, scope, specification: PipelineSpec) -> bool:
    reference = JointBHFitScope.model_validate(scope["joint_bh_reference"])
    member = reference.member
    return (
        reference.plan_sha256 == scope["hypotheses_sha256"]
        and member.aim_id == scope["aim_id"]
        and member.step_id is not None
        and member.step_id != step["step_id"]
        and member.parameters_sha256 != step["parameters_sha256"]
        and _comparison_primary_family_sha256(step, specification)
        == member.family_sha256
    )


def check_thesis_outputs(
    envelope: _DOCUMENT, outputs: Iterable[ArtifactVersion]
) -> None:
    scope = envelope.get("thesis_scope")
    if scope is not None and "joint_bh" in scope:
        if any(
            item.representation != JOINT_BH_INPUT or item.permitted_uses
            for item in outputs
        ):
            raise OperationBlocked("thesis_joint_bh_unsealed_member_output")
        return
    if (
        scope is None
        or scope["route"] == "tier_a"
        and (envelope["step"]["purpose"] in _CONFIRMATORY)
    ):
        return
    if any(set(item.permitted_uses) & _CONFIRMATORY for item in outputs):
        raise OperationBlocked("thesis_claim_scope_promotion")
