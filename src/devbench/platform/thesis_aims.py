"""Prospective research aims and receipt-backed revisions in the scientific ledger."""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable, Collection, Mapping, Sequence
from contextlib import nullcontext
from dataclasses import dataclass
from sqlite3 import Connection
from typing import Annotated, Any, Literal

from pydantic import (
    Field,
    StringConstraints,
    TypeAdapter,
    model_serializer,
    model_validator,
)

from devbench.adaptive.models import AdaptiveRecord
from devbench.core._hashing import canonical_sha256
from devbench.core.thesis_blinded_review import (
    BlindedAimAssessment,
    BlindedAimSubject,
    BlindedReviewVerifier,
    panel_projection,
    review_metrics,
)
from devbench.engine.partition_authority import PartitionAuthority, embargoed
from devbench.engine.scientific_authority import (
    _check_lineage,
    append_scientific_projection_event,
    load_scientific_projection,
    require_same_ledger_transaction,
)
from devbench.engine.workflow_operations import (
    OperationBlocked,
    OperationConflict,
    WorkflowOperationLedger,
    _digest,
    _json,
)

from .adaptive_workflow import AdaptiveWorkflow
from .adaptive_workflow_state import Digest, Identity
from .adaptive_workflow_state import _State as AdaptiveState

_STREAM = "thesis_aims"
_FORMAT = "devbench.thesis-aim-lifecycle/1"
_MAX_AIMS = 32
_BASIS_PREFIX = "thesis-aim:"
_POSITIVE_OUTCOMES = frozenset({"supported", "no_significant_finding"})

Text = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=512)
]
AimKind = Literal["confirmatory", "exploratory"]
OutcomeStatus = Literal[
    "supported",
    "no_significant_finding",
    "unresolved",
    "refused",
    "failed",
]
_Document = dict[str, Any]


class AimRequirement(AdaptiveRecord):
    """Retain requirement identity and value without copying the source text."""

    name: Identity
    value_sha256: Digest
    origin: Literal[
        "user_prespecified", "assistant_proposed", "fixture_default", "derived"
    ]
    source_sha256: Digest


class CompetingExplanation(AdaptiveRecord):
    explanation_id: Identity
    explanation: Text
    discriminating_check: Text


class AimMultiplicity(AdaptiveRecord):
    """One declared BH testing scope, possibly shared by several aims."""

    family_id: Identity
    aim_ids: tuple[Identity, ...] = Field(min_length=1, max_length=_MAX_AIMS)
    hypotheses_sha256: Digest
    adjustment: Literal["BH"] = "BH"
    fdr: float = Field(gt=0, lt=1, strict=True)

    @model_validator(mode="after")
    def _order_members(self) -> AimMultiplicity:
        if len(set(self.aim_ids)) != len(self.aim_ids):
            raise ValueError("A multiplicity family requires distinct aim identities")
        object.__setattr__(self, "aim_ids", tuple(sorted(self.aim_ids)))
        return self


class AimDefinition(AdaptiveRecord):
    aim_id: Identity
    kind: AimKind
    question: Text
    estimand: Text
    input_sha256s: tuple[Digest, ...] = Field(default=(), max_length=16)
    depends_on: tuple[Identity, ...] = Field(default=(), max_length=16)
    evaluation_sha256s: tuple[Digest, ...] = Field(default=(), max_length=16)
    multiplicity: AimMultiplicity | None = None
    competing_explanations: tuple[CompetingExplanation, ...] = Field(
        default=(), max_length=8
    )
    requirement_sources: tuple[AimRequirement, ...] = Field(default=(), max_length=16)
    blinded_review_required: bool = Field(default=False, strict=True)

    @model_serializer(mode="wrap")
    def retain_legacy_aim_digest(self, handler):
        data = handler(self)
        if not self.blinded_review_required:
            data.pop("blinded_review_required", None)
        return data

    @model_validator(mode="after")
    def _scope(self) -> AimDefinition:
        groups = (
            self.input_sha256s,
            self.evaluation_sha256s,
            self.depends_on,
            tuple(row.explanation_id for row in self.competing_explanations),
            tuple(row.name for row in self.requirement_sources),
        )
        if any(len(set(group)) != len(group) for group in groups):
            raise ValueError(
                "Research aim identities must be distinct within each scope"
            )
        if self.kind == "exploratory":
            if self.multiplicity is not None or self.evaluation_sha256s:
                raise ValueError(
                    "Exploratory aims cannot claim confirmatory multiplicity"
                )
        elif (
            self.multiplicity is None
            or self.aim_id not in self.multiplicity.aim_ids
            or not self.evaluation_sha256s
            or not set(self.evaluation_sha256s) <= set(self.input_sha256s)
        ):
            raise ValueError(
                "Confirmatory aims require an exact evaluation and multiplicity scope"
            )
        return self


class AimApproval(AdaptiveRecord):
    actor: Identity
    rationale: Text
    approved_at: int = Field(gt=0)
    aim_sha256: Digest


class AimEvidence(AdaptiveRecord):
    """A current accepted artifact or the retained receipt of a failed step."""

    step_id: Identity
    receipt_sha256: Digest
    artifact_sha256: Digest | None = None


class AimOutcome(AdaptiveRecord):
    status: OutcomeStatus
    rationale: Text
    evidence: tuple[AimEvidence, ...] = Field(default=(), max_length=16)
    uncertainty: Text

    @model_validator(mode="after")
    def _require_evidence(self) -> AimOutcome:
        if self.status in _POSITIVE_OUTCOMES and (
            not self.evidence
            or any(ref.artifact_sha256 is None for ref in self.evidence)
        ):
            raise ValueError("A finding or negative result requires accepted artifacts")
        return self


class ExplanationAssessment(AdaptiveRecord):
    explanation_id: Identity
    conclusion: Literal["supported", "weakened", "inconclusive"]
    evidence: AimEvidence
    rationale: Text


class AimBinding(AdaptiveRecord):
    aim_id: Identity
    version: int = Field(gt=0)
    aim_sha256: Digest
    basis_id: Identity
    basis_sha256: Digest
    kind: AimKind
    multiplicity: AimMultiplicity | None


class _BlindedAimPanel(AdaptiveRecord):
    subject: BlindedAimSubject
    evidence: tuple[AimEvidence, ...] = Field(min_length=1, max_length=16)
    assessments: tuple[BlindedAimAssessment, ...] = Field(default=(), max_length=8)

    def public_state(self) -> _Document:
        return panel_projection(self.subject, self.assessments)


class _Aim(AdaptiveRecord):
    definition: AimDefinition
    version: int = Field(default=1, gt=0)
    approval: AimApproval | None = None
    outcome: AimOutcome | None = None
    assessed_explanations: tuple[ExplanationAssessment, ...] = ()
    invalidated_by: Identity | None = None
    review_panel: _BlindedAimPanel | None = None

    @model_serializer(mode="wrap")
    def retain_legacy_event_encoding(self, handler):
        data = handler(self)
        if self.review_panel is None:
            data.pop("review_panel", None)
        return data


class _Projection(AdaptiveRecord):
    aims: dict[str, _Aim] = Field(default_factory=dict)
    family_locks: dict[str, Digest] = Field(default_factory=dict)
    history: list[_Document] = Field(default_factory=list)


def _apply_event(state: _Projection, row: _Document) -> None:
    record = row["record"]
    event = record["state"]
    if record["event"] != _STREAM or event["format"] != _FORMAT:
        raise OperationConflict("Unexpected research aim event")
    changed = _Aim.model_validate(event["aim"])
    identity = changed.definition.aim_id
    state.aims[identity] = changed
    state.history.append(
        {
            **{
                key: event[key]
                for key in (
                    "action",
                    "invalidated",
                    "actor",
                    "rationale",
                    "recorded_at",
                )
            },
            "revision": row["revision"],
            "aim_id": identity,
            "aim_version": changed.version,
            "aim_sha256": canonical_sha256(changed.definition.model_dump(mode="json")),
            **(
                {"outcome": event["aim"]["outcome"]}
                if event["action"] == "outcome"
                else {}
            ),
            **_review_history_transition(event["action"], changed.review_panel),
        }
    )
    for other in event["invalidated"]:
        state.aims[other] = state.aims[other].model_copy(
            update={"invalidated_by": identity}
        )
    if event["action"] == "approve" and (family := changed.definition.multiplicity):
        state.family_locks[family.family_id] = canonical_sha256(
            family.model_dump(mode="json")
        )


def _review_history_transition(action: str, panel: _BlindedAimPanel | None):
    if action == "review_opened" and panel is not None:
        return {"review_subject": panel.subject.model_dump(mode="json")}
    if action == "review_assessed" and panel is not None:
        return {"review_assessment": panel.assessments[-1].model_dump(mode="json")}
    return {}


def _basis_id(aim: _Aim) -> str:
    return _BASIS_PREFIX + canonical_sha256([aim.definition.aim_id, aim.version])


def _binding(aim: _Aim) -> AimBinding:
    if aim.approval is None or aim.invalidated_by is not None:
        raise OperationBlocked("thesis_aim_approval_or_revision_required")
    return AimBinding(
        aim_id=aim.definition.aim_id,
        version=aim.version,
        aim_sha256=aim.approval.aim_sha256,
        basis_id=_basis_id(aim),
        basis_sha256=canonical_sha256(aim.approval.model_dump(mode="json")),
        kind=aim.definition.kind,
        multiplicity=aim.definition.multiplicity,
    )


def _lookup_aim(state: _Projection, identity: str) -> _Aim:
    try:
        return state.aims[identity]
    except KeyError as error:
        raise OperationConflict("Research aim is unregistered") from error


def _validate_dependencies(state: _Projection, proposed: AimDefinition) -> None:
    edges = {
        identity: aim.definition.depends_on for identity, aim in state.aims.items()
    }
    edges[proposed.aim_id] = proposed.depends_on
    if set(proposed.depends_on) - edges.keys():
        raise ValueError("A research aim requires registered dependencies")
    remaining = set(edges)
    while remaining:
        ready = {key for key in remaining if remaining.isdisjoint(edges[key])}
        if not ready:
            raise ValueError("Research aims require acyclic dependencies")
        remaining -= ready


def _require_frozen_family(state: _Projection, definition: AimDefinition) -> None:
    family = definition.multiplicity
    if family is None:
        return
    frozen = state.family_locks.get(family.family_id)
    if frozen is not None and frozen != canonical_sha256(
        family.model_dump(mode="json")
    ):
        raise OperationConflict("A frozen family cannot change its scope")


def _check_family_members(state: _Projection, aim: _Aim, *, all_approved: bool) -> None:
    if (family := aim.definition.multiplicity) is None:
        return
    for identity in family.aim_ids:
        member = state.aims.get(identity)
        if (
            member is None
            or member.definition.multiplicity != family
            or member.invalidated_by is not None
        ):
            raise OperationBlocked("thesis_confirmatory_family_scope_incomplete")
        if all_approved and member.approval is None:
            raise OperationBlocked("thesis_confirmatory_family_approval_incomplete")


def _invalidated(state: _Projection, origin: str) -> list[str]:
    affected = {origin}
    while True:
        dependent = {
            key
            for key, item in state.aims.items()
            if affected.intersection(item.definition.depends_on)
            or (
                item.definition.multiplicity is not None
                and affected.intersection(item.definition.multiplicity.aim_ids)
            )
        }
        if dependent <= affected:
            # An unapproved proposal has no acquired evidence to invalidate.
            return sorted(
                key
                for key in affected - {origin}
                if state.aims[key].approval is not None
            )
        affected |= dependent


@dataclass(frozen=True, slots=True)
class _EvidenceContext:
    workflow: AdaptiveWorkflow
    db: Connection
    adaptive: AdaptiveState


def _verify_evidence(ref: AimEvidence, aim: _Aim, context: _EvidenceContext) -> None:
    adaptive = context.adaptive
    if ref.step_id not in adaptive.nodes:
        raise OperationBlocked("thesis_aim_evidence_step_unregistered")
    current = adaptive.outcome(ref.step_id)
    if current is None:
        raise OperationBlocked("thesis_aim_evidence_receipt_stale")
    envelope = adaptive.dispatches[current["attempt_id"]]["envelope"]
    binding = _binding(aim)
    provenance = envelope["basis"].get(binding.basis_id)
    direct = {ref["basis_id"] for ref in envelope["step"]["basis"]}
    if (
        binding.basis_id not in direct
        or provenance is None
        or provenance["sha256"] != binding.basis_sha256
    ):
        raise OperationBlocked("thesis_aim_evidence_wrong_approval_basis")
    current = _bind_evidence_receipt(ref, context, current, envelope)
    if ref.artifact_sha256 is None and current["status"] == "failed":
        return
    _verify_artifact(ref, aim, adaptive, current, envelope)


def _bind_evidence_receipt(
    ref: AimEvidence,
    context: _EvidenceContext,
    current: _Document,
    envelope: _Document,
) -> _Document:
    if current["receipt_sha256"] == ref.receipt_sha256:
        return current
    if "joint_bh" not in envelope.get("thesis_scope", {}):
        raise OperationBlocked("thesis_aim_evidence_receipt_stale")
    from .thesis_joint_bh_release import current_joint_bh_release

    release, aim_id = current_joint_bh_release(
        context.workflow, context.db, context.adaptive, current, claim=False
    )
    if release.receipt_sha256() != ref.receipt_sha256:
        raise OperationBlocked("thesis_aim_evidence_receipt_stale")
    return {
        **current,
        "outputs": {"result": release.outputs[aim_id].model_dump(mode="json")},
    }


def _verify_artifact(ref, aim, adaptive, current, envelope) -> None:
    admitted = {"qualified_inference", "admitted_research_inference"}
    node = adaptive.nodes[ref.step_id]
    if (
        current["status"] != "succeeded"
        or node.branch_id in adaptive.retired
        or ref.step_id in adaptive.holds
        or (ref.step_id, adaptive.generations[ref.step_id]) in adaptive.claim_stale
    ):
        raise OperationBlocked("thesis_aim_evidence_not_current")
    for item in current["outputs"].values():
        if item["sha256"] == ref.artifact_sha256 and (
            item["version_id"] not in adaptive.stale_versions
        ):
            if aim.definition.kind != "confirmatory":
                return
            pins = {pin["sha256"] for pin in envelope["pins"]}
            if (
                set(item["permitted_uses"]) & admitted
                and envelope["step"]["purpose"] in admitted
                and set(aim.definition.evaluation_sha256s) <= pins
            ):
                return
    raise OperationBlocked("thesis_aim_evidence_artifact_unadmitted")


def _outcome_current(aim: _Aim, context: _EvidenceContext) -> bool:
    if aim.outcome is None or aim.invalidated_by is not None:
        return False
    try:
        if aim.outcome.status in _POSITIVE_OUTCOMES:
            _require_blinded_outcome(aim, aim.outcome)
        _check_outcome_evidence(aim, aim.outcome, context)
    except OperationBlocked:
        return False
    return True


def _require_blinded_outcome(aim: _Aim, outcome: AimOutcome) -> None:
    if not aim.definition.blinded_review_required:
        return
    panel = aim.review_panel
    if panel is None:
        raise OperationBlocked("thesis_blinded_review_required")
    outcome_digest = canonical_sha256(outcome.model_dump(mode="json"))
    if panel.subject.outcome_sha256 != outcome_digest:
        raise OperationBlocked("thesis_reviewed_outcome_changed")
    status = panel.public_state()["status"]
    if status != "passed":
        raise OperationBlocked("thesis_blinded_review_" + status)


def _verify_explanation_evidence(
    assessment: ExplanationAssessment,
    aim: _Aim,
    context: _EvidenceContext,
) -> None:
    node = context.adaptive.nodes.get(assessment.evidence.step_id)
    if node is not None and node.branch_id in context.adaptive.retired:
        raise OperationBlocked("thesis_aim_evidence_not_current")
    _verify_evidence(assessment.evidence, aim, context)


def _stale_explanation_evidence(aim: _Aim, context: _EvidenceContext) -> dict[str, str]:
    stale = {}
    for assessment in aim.assessed_explanations:
        try:
            _verify_explanation_evidence(assessment, aim, context)
        except OperationBlocked as error:
            stale[assessment.explanation_id] = error.state
    return stale


def _check_competing_explanations(aim: _Aim, context: _EvidenceContext) -> None:
    declared = {item.explanation_id for item in aim.definition.competing_explanations}
    assessed = {item.explanation_id for item in aim.assessed_explanations}
    if declared - assessed:
        raise OperationBlocked("thesis_aim_competing_explanations_unassessed")
    if _stale_explanation_evidence(aim, context):
        raise OperationBlocked("thesis_aim_competing_explanation_evidence_stale")


def _check_positive_evidence(
    aim: _Aim,
    evidence: Sequence[AimEvidence],
    context: _EvidenceContext,
) -> None:
    for reference in evidence:
        _verify_evidence(reference, aim, context)
        if aim.definition.blinded_review_required and (
            reference.artifact_sha256 is None
            or embargoed(context.db, reference.artifact_sha256)
        ):
            raise OperationBlocked("thesis_blinded_review_evidence_unavailable")
    if aim.definition.kind == "confirmatory" and not any(
        context.adaptive.nodes[reference.step_id].primary for reference in evidence
    ):
        raise OperationBlocked("thesis_aim_primary_evidence_required")
    _check_competing_explanations(aim, context)


def _check_outcome_evidence(aim: _Aim, outcome: AimOutcome, context) -> None:
    if outcome.status in _POSITIVE_OUTCOMES:
        _check_positive_evidence(aim, outcome.evidence, context)
    else:
        for reference in outcome.evidence:
            _verify_evidence(reference, aim, context)


def _review_contributors(state, aim, context, evidence, opener) -> set[str]:
    contributors = {opener, aim.approval.actor}
    contributors.update(
        row["actor"]
        for row in state.history
        if row["aim_id"] == aim.definition.aim_id
        and row["aim_version"] == aim.version
        and row["actor"] is not None
    )
    for ref in (*evidence, *(item.evidence for item in aim.assessed_explanations)):
        result = context.adaptive.outcome(ref.step_id)
        dispatch = context.adaptive.dispatches[result["attempt_id"]]
        contributors.add(dispatch["envelope"]["actor"])
    return {actor.casefold() for actor in contributors}


def _check_dependencies(
    state: _Projection, aim: _Aim, context: _EvidenceContext
) -> None:
    pending = list(aim.definition.depends_on)
    visited = set()
    while pending:
        identity = pending.pop()
        if identity in visited:
            continue
        parent = _lookup_aim(state, identity)
        if (
            parent.outcome is None
            or parent.outcome.status not in _POSITIVE_OUTCOMES
            or not _outcome_current(parent, context)
        ):
            raise OperationBlocked("thesis_aim_dependency_not_current")
        visited.add(identity)
        pending.extend(parent.definition.depends_on)


class ThesisAimLifecycle:
    """Bind research aims to the adaptive workflow's ledger and outbox."""

    def __init__(
        self,
        ledger: WorkflowOperationLedger,
        lineage_id: str,
        *,
        authorize_operator: Callable[[str], bool] | None = None,
        user_requirements: Sequence[_Document] = (),
        verify_blinded_review: BlindedReviewVerifier | None = None,
    ) -> None:
        _check_lineage(lineage_id)
        self.ledger = ledger
        self.authority = ledger.scientific
        self.lineage_id = lineage_id
        self.workflow = AdaptiveWorkflow(ledger, lineage_id)
        self._authorize_operator = authorize_operator
        self._verify_blinded_review = verify_blinded_review
        self._user_requirements = self._retained_requirements(user_requirements)

    @staticmethod
    def _retained_requirements(requirements: Sequence[_Document]) -> dict[str, tuple]:
        if not requirements:
            return {}
        from devbench.analysis.method_planner_contract import validate_planning_metadata

        checked = validate_planning_metadata(
            user_parameter_requirements=list(requirements),
        )["user_parameter_requirements"]
        return {
            row["name"]: (canonical_sha256(row["value"]), canonical_sha256(row))
            for row in checked
        }

    def _actor(self, actor: str, rationale: str) -> tuple[str, str]:
        actor = TypeAdapter(Identity).validate_python(actor)
        rationale = TypeAdapter(Text).validate_python(rationale)
        if (
            self._authorize_operator is None
            or self._authorize_operator(actor) is not True
        ):
            raise PermissionError("Research aim changes require an authorized operator")
        return actor, rationale

    def _projection(self, db: Connection):
        return load_scientific_projection(
            db,
            self.lineage_id,
            _STREAM,
            initial=_Projection,
            apply=_apply_event,
            restore=_Projection.model_validate,
        )

    def _change(
        self,
        action: str,
        build: Callable,
        *,
        expected_revision: int | None,
        connection: Connection | None,
        actor: str | None = None,
        rationale: str | None = None,
    ) -> int:
        self.ledger._writable()
        _check_lineage(self.lineage_id, expected_revision)
        if connection is not None:
            require_same_ledger_transaction(connection, self.ledger)
        transaction = (
            self.authority._execution_tx(self.lineage_id)
            if connection is None
            else nullcontext(connection)
        )
        with transaction as db:
            current = self.authority._revision(db, self.lineage_id)
            if (
                current == 0
                or expected_revision is not None
                and current != expected_revision
            ):
                raise OperationConflict(
                    "Research aim mutation requires a current revision"
                )
            state, head = self._projection(db)
            changed, invalidated = build(state, db)
            event = {
                "format": _FORMAT,
                "action": action,
                "aim": changed.model_dump(mode="json"),
                "invalidated": invalidated,
                "actor": actor,
                "rationale": rationale,
                "recorded_at": time.time_ns(),
            }
            record = {
                "kind": "execution_event",
                "event": _STREAM,
                "record_id": "thesis-aim-" + uuid.uuid4().hex,
                "state": event,
                "state_sha256": _digest(_json(event)),
            }
            db.execute("SAVEPOINT thesis_aim_change")
            try:
                self._sync_basis(db, action, state, changed, invalidated)
                return append_scientific_projection_event(
                    self.authority,
                    db,
                    self.lineage_id,
                    _STREAM,
                    record,
                    head.snapshot(lambda: state.model_dump(mode="json")),
                )
            except BaseException:
                db.execute("ROLLBACK TO thesis_aim_change")
                raise
            finally:
                db.execute("RELEASE thesis_aim_change")

    def _sync_basis(self, db, action, state, changed, invalidated) -> None:
        if action == "approve":
            binding = _binding(changed)
            self.workflow._event(
                db,
                "basis",
                {
                    "basis_id": binding.basis_id,
                    "kind": "fact",
                    "sha256": binding.basis_sha256,
                },
            )
        elif action == "revise":
            for identity in (changed.definition.aim_id, *invalidated):
                previous = state.aims[identity]
                if previous.approval is not None:
                    self.workflow._event(
                        db,
                        "basis",
                        {
                            "basis_id": _basis_id(previous),
                            "kind": "fact",
                            "sha256": canonical_sha256(
                                [
                                    "invalidated",
                                    changed.definition.aim_id,
                                    changed.version,
                                    _basis_id(previous),
                                ]
                            ),
                        },
                    )

    def _validate_definition(
        self, state: _Projection, definition: AimDefinition
    ) -> None:
        _validate_dependencies(state, definition)
        _require_frozen_family(state, definition)
        for record in definition.requirement_sources:
            if record.origin == "user_prespecified" and self._user_requirements.get(
                record.name
            ) != (record.value_sha256, record.source_sha256):
                raise ValueError(
                    "A user attribution requires an exact retained user requirement"
                )

    def propose(
        self,
        definition: AimDefinition,
        *,
        expected_revision: int | None = None,
        connection: Connection | None = None,
    ) -> int:
        definition = AimDefinition.model_validate(definition)

        def build(state, _):
            if definition.aim_id in state.aims:
                raise OperationConflict(
                    "An existing aim requires an attributed revision"
                )
            if len(state.aims) >= _MAX_AIMS:
                raise OperationBlocked("thesis_aim_inventory_full")
            self._validate_definition(state, definition)
            return _Aim(definition=definition), []

        return self._change(
            "propose",
            build,
            expected_revision=expected_revision,
            connection=connection,
        )

    def revise(
        self,
        definition: AimDefinition,
        *,
        actor: str,
        rationale: str,
        expected_revision: int | None = None,
        connection: Connection | None = None,
    ) -> int:
        actor, rationale = self._actor(actor, rationale)
        definition = AimDefinition.model_validate(definition)

        def build(state, _):
            old = _lookup_aim(state, definition.aim_id)
            if old.definition.blinded_review_required and not (
                definition.blinded_review_required
            ):
                raise OperationBlocked("thesis_blinded_review_requirement_locked")
            self._validate_definition(state, definition)
            return _Aim(definition=definition, version=old.version + 1), _invalidated(
                state, definition.aim_id
            )

        return self._change(
            "revise",
            build,
            actor=actor,
            rationale=rationale,
            expected_revision=expected_revision,
            connection=connection,
        )

    def approve(
        self,
        aim_id: str,
        *,
        actor: str,
        rationale: str,
        expected_revision: int | None = None,
        connection: Connection | None = None,
    ) -> int:
        actor, rationale = self._actor(actor, rationale)

        def build(state, db):
            aim = _lookup_aim(state, aim_id)
            if aim.approval is not None or aim.invalidated_by is not None:
                raise OperationConflict(
                    "An approved or invalidated aim requires revision"
                )
            _check_family_members(state, aim, all_approved=False)
            if any(
                PartitionAuthority._prior_exposure(db, (identity, identity))
                for identity in aim.definition.evaluation_sha256s
            ):
                raise OperationBlocked("thesis_aim_evaluation_previously_exposed")
            approval = AimApproval(
                actor=actor,
                rationale=rationale,
                approved_at=int(time.time()),
                aim_sha256=canonical_sha256(aim.definition.model_dump(mode="json")),
            )
            return aim.model_copy(update={"approval": approval}), []

        return self._change(
            "approve",
            build,
            actor=actor,
            rationale=rationale,
            expected_revision=expected_revision,
            connection=connection,
        )

    def _read(self, query: Callable, connection: Connection | None):
        if connection is None:
            return self.ledger._read(query)
        require_same_ledger_transaction(connection, self.ledger)
        return query(connection)

    def require_ready(
        self,
        aim_id: str,
        *,
        connection: Connection | None = None,
    ) -> AimBinding:
        def read(db):
            state, _ = self._projection(db)
            aim = _lookup_aim(state, aim_id)
            return _check_ready(state, aim, self._evidence_context(db))

        return self._read(read, connection)

    def _evidence_context(self, db: Connection) -> _EvidenceContext:
        return _EvidenceContext(self.workflow, db, self.workflow._state(db))

    def require_step_basis(
        self,
        basis: Mapping[str, _Document],
        *,
        input_sha256s: Collection[str],
        confirmatory_fit: bool,
        connection: Connection,
        hypotheses_sha256: str | None = None,
    ) -> tuple[AimBinding, ...]:
        """Check direct aim bindings at dispatch and acceptance."""
        scoped = {key for key in basis if key.startswith(_BASIS_PREFIX)}
        if not scoped:
            return ()
        require_same_ledger_transaction(connection, self.ledger)
        state, _ = self._projection(connection)
        candidates = {_basis_id(aim): aim for aim in state.aims.values()}
        if scoped - candidates.keys():
            raise OperationBlocked("thesis_aim_basis_has_been_superseded")
        context = self._evidence_context(connection)
        bindings = []
        for key in sorted(scoped):
            aim = candidates[key]
            binding = _check_ready(state, aim, context)
            if basis[key]["sha256"] != binding.basis_sha256:
                raise OperationBlocked("thesis_aim_approval_basis_changed")
            if confirmatory_fit and (
                aim.definition.kind != "confirmatory"
                or not set(aim.definition.evaluation_sha256s) <= set(input_sha256s)
            ):
                raise OperationBlocked("thesis_aim_confirmatory_inputs_unbound")
            if (
                confirmatory_fit
                and hypotheses_sha256 is not None
                and (
                    binding.multiplicity is None
                    or binding.multiplicity.hypotheses_sha256 != hypotheses_sha256
                )
            ):
                raise OperationBlocked("thesis_aim_confirmatory_family_unbound")
            bindings.append(binding)
        return tuple(bindings)

    def open_blinded_review(
        self,
        aim_id: str,
        *,
        version: int,
        outcome: AimOutcome,
        masked_packet_sha256: Digest,
        reviewers: Sequence[Identity],
        actor: str,
        rationale: str,
        expected_revision: int | None = None,
        connection: Connection | None = None,
    ) -> int:
        """Freeze the masked packet before any independently verified responses."""
        actor, rationale = self._actor(actor, rationale)
        proposal = AimOutcome.model_validate(outcome.model_dump(mode="json"))
        if proposal.status not in _POSITIVE_OUTCOMES:
            raise ValueError("A blinded panel requires an evidence-backed finding")
        outcome_digest = canonical_sha256(proposal.model_dump(mode="json"))

        def build(state, db):
            aim = _lookup_aim(state, aim_id)
            if (
                version != aim.version
                or aim.outcome is not None
                or aim.review_panel is not None
            ):
                raise OperationConflict("Blinded review requires an unused current aim")
            if not aim.definition.blinded_review_required:
                raise OperationBlocked("thesis_blinded_review_not_declared")
            context = self._evidence_context(db)
            binding = _check_ready(state, aim, context)
            _check_positive_evidence(aim, proposal.evidence, context)
            original = {outcome_digest}
            for reference in proposal.evidence:
                original.update((reference.receipt_sha256, reference.artifact_sha256))
            if masked_packet_sha256 in original:
                raise ValueError("Bind a distinct masked review packet")
            subject = BlindedAimSubject(
                aim_id=aim_id,
                aim_version=version,
                approved_aim_sha256=binding.aim_sha256,
                outcome_sha256=outcome_digest,
                masked_packet_sha256=masked_packet_sha256,
                reviewers=tuple(reviewers),
            )
            contributors = _review_contributors(
                state, aim, context, proposal.evidence, actor
            )
            if contributors.intersection(item.casefold() for item in subject.reviewers):
                raise PermissionError("Blinded reviewers must be independent")
            panel = _BlindedAimPanel(subject=subject, evidence=proposal.evidence)
            return aim.model_copy(update={"review_panel": panel}), []

        return self._change(
            "review_opened",
            build,
            expected_revision=expected_revision,
            connection=connection,
            actor=actor,
            rationale=rationale,
        )

    def submit_blinded_review(
        self,
        aim_id: str,
        *,
        version: int,
        assessment: BlindedAimAssessment,
        expected_revision: int | None = None,
        connection: Connection | None = None,
    ) -> int:
        """Admit one exact subject-bound response from the installed verifier."""
        assessment = BlindedAimAssessment.model_validate(
            assessment.model_dump(mode="json")
        )

        def build(state, db):
            aim = _lookup_aim(state, aim_id)
            panel = aim.review_panel
            if version != aim.version or aim.outcome is not None or panel is None:
                raise OperationConflict("Blinded assessment requires the current panel")
            context = self._evidence_context(db)
            binding = _check_ready(state, aim, context)
            subject = panel.subject
            if (
                subject.sha256() != assessment.subject_sha256
                or subject.approved_aim_sha256 != binding.aim_sha256
            ):
                raise OperationBlocked("thesis_blinded_review_subject_changed")
            if assessment.reviewer not in subject.reviewers:
                raise PermissionError("Use an assigned independent reviewer")
            if any(
                old.reviewer == assessment.reviewer
                or old.receipt_sha256 == assessment.receipt_sha256
                for old in panel.assessments
            ):
                raise OperationConflict("Blinded review or receipt already recorded")
            _check_positive_evidence(aim, panel.evidence, context)
            verifier = self._verify_blinded_review
            if verifier is None or verifier(subject, assessment) is not True:
                raise PermissionError("Use an authorized independently verified review")
            updated = panel.model_copy(
                update={"assessments": (*panel.assessments, assessment)}
            )
            return aim.model_copy(update={"review_panel": updated}), []

        return self._change(
            "review_assessed",
            build,
            expected_revision=expected_revision,
            connection=connection,
            actor=assessment.reviewer,
            rationale=assessment.rationale,
        )

    def blinded_review_history(
        self,
        aim_id: str,
        *,
        version: int,
        connection: Connection | None = None,
    ) -> _Document:
        """Read the exact attributed local audit, including a superseded panel."""

        def read(db):
            state, _ = self._projection(db)
            records = [
                item
                for item in state.history
                if item["aim_id"] == aim_id and item["aim_version"] == version
            ]
            subjects = [r["review_subject"] for r in records if "review_subject" in r]
            if len(subjects) != 1:
                raise OperationBlocked("thesis_blinded_review_subject_unavailable")
            return {
                "subject": subjects[0],
                "assessments": [
                    r["review_assessment"] for r in records if "review_assessment" in r
                ],
            }

        return self._read(read, connection)

    def record_outcome(
        self,
        aim_id: str,
        *,
        version: int,
        status: OutcomeStatus,
        rationale: str,
        uncertainty: str,
        evidence: Sequence[AimEvidence | _Document] = (),
        expected_revision: int | None = None,
        connection: Connection | None = None,
    ) -> int:
        outcome = AimOutcome.model_validate(
            {
                "status": status,
                "rationale": rationale,
                "uncertainty": uncertainty,
                "evidence": evidence,
            }
        )

        def build(state, db):
            aim = _lookup_aim(state, aim_id)
            if version != aim.version or aim.outcome is not None:
                raise OperationConflict(
                    "Aim outcome requires an unused current version"
                )
            _binding(aim)
            context = self._evidence_context(db)
            if status in _POSITIVE_OUTCOMES:
                _check_family_members(state, aim, all_approved=True)
                _check_dependencies(state, aim, context)
                _require_blinded_outcome(aim, outcome)
            _check_outcome_evidence(aim, outcome, context)
            return aim.model_copy(update={"outcome": outcome}), []

        return self._change(
            "outcome",
            build,
            expected_revision=expected_revision,
            connection=connection,
        )

    def assess_explanation(
        self,
        aim_id: str,
        assessment: ExplanationAssessment,
        *,
        version: int,
        expected_revision: int | None = None,
        connection: Connection | None = None,
    ) -> int:
        assessment = ExplanationAssessment.model_validate(assessment)

        def build(state, db):
            aim = _lookup_aim(state, aim_id)
            if version != aim.version or aim.outcome is not None:
                raise OperationConflict("Explanation assessment requires an open aim")
            if aim.review_panel is not None:
                raise OperationBlocked("thesis_blinded_review_explanations_locked")
            declared = {
                item.explanation_id for item in aim.definition.competing_explanations
            }
            prior = {item.explanation_id for item in aim.assessed_explanations}
            if assessment.explanation_id not in declared - prior:
                raise OperationConflict(
                    "Explanation is unregistered or already assessed"
                )
            if (
                assessment.conclusion != "inconclusive"
                and assessment.evidence.artifact_sha256 is None
            ):
                raise ValueError(
                    "A discriminating conclusion requires an accepted artifact"
                )
            _verify_explanation_evidence(assessment, aim, self._evidence_context(db))
            return aim.model_copy(
                update={
                    "assessed_explanations": (*aim.assessed_explanations, assessment)
                }
            ), []

        return self._change(
            "assess_explanation",
            build,
            expected_revision=expected_revision,
            connection=connection,
        )

    def scientific_context(self, *, connection: Connection | None = None) -> _Document:
        """Read current coordinator context for subsequent mediated disclosure."""

        def read(db):
            state, _ = self._projection(db)
            context = self._evidence_context(db)
            aims = [_context_row(state, aim, context) for aim in state.aims.values()]
            panels = [row["blinded_review"] for row in aims if "blinded_review" in row]
            reviews = (
                {"blinded_review_metrics": review_metrics(panels)} if panels else {}
            )
            return {
                "format": _FORMAT,
                "research_aims": aims,
                "raw_matrix_rows": 0,
                "clinical_release_allowed": False,
                **reviews,
            }

        return self._read(read, connection)

    def history(
        self,
        *,
        connection: Connection | None = None,
        include_blinded_audit: bool = True,
    ) -> list[_Document]:
        """Return detached, ordered attribution and preserved terminal outcomes."""

        def read(db):
            state, _ = self._projection(db)
            if include_blinded_audit:
                return state.history
            return [_redact_review_history(row) for row in state.history]

        return self._read(read, connection)


def _redact_review_history(row: _Document) -> _Document:
    if row["action"] not in {"review_opened", "review_assessed"}:
        return row
    excluded = {"actor", "rationale", "review_subject", "review_assessment"}
    return {key: value for key, value in row.items() if key not in excluded}


def _check_ready(
    state: _Projection,
    aim: _Aim,
    context: _EvidenceContext,
) -> AimBinding:
    binding = _binding(aim)
    if aim.outcome is not None:
        raise OperationBlocked("thesis_aim_outcome_requires_revision")
    _check_family_members(state, aim, all_approved=True)
    _check_dependencies(state, aim, context)
    return binding


def _context_row(state: _Projection, aim: _Aim, context: _EvidenceContext) -> _Document:
    row = aim.model_dump(mode="json", exclude={"definition", "review_panel"})
    row.update(aim.definition.model_dump(mode="json"))
    if stale := _stale_explanation_evidence(aim, context):
        row["stale_explanation_evidence"] = stale
    if aim.review_panel is not None:
        row["blinded_review"] = _current_panel_state(state, aim, context)
    if aim.invalidated_by is not None:
        row["status"] = "invalidated"
        return row
    elif aim.approval is None:
        row["status"] = "operator_review"
        return row
    if aim.outcome is not None and aim.outcome.status not in _POSITIVE_OUTCOMES:
        row["status"] = _outcome_status(aim, context)
        return row
    try:
        _check_family_members(state, aim, all_approved=True)
        _check_dependencies(state, aim, context)
    except OperationBlocked as error:
        row.update(
            status="invalidated" if aim.outcome else "blocked", reason=error.state
        )
    else:
        row["status"] = _outcome_status(aim, context)
    return row


def _current_panel_state(
    state: _Projection,
    aim: _Aim,
    context: _EvidenceContext,
) -> _Document:
    panel = aim.review_panel
    if panel is None:
        raise OperationConflict("Blinded panel is unavailable")
    recorded = panel.public_state()
    try:
        _binding(aim)
        _check_family_members(state, aim, all_approved=True)
        _check_dependencies(state, aim, context)
        _check_positive_evidence(aim, panel.evidence, context)
    except OperationBlocked as error:
        recorded.update(status="stale_evidence", adjudicated=False, reason=error.state)
    return recorded


def _outcome_status(aim: _Aim, context: _EvidenceContext) -> str:
    if aim.outcome is None:
        return "ready"
    return aim.outcome.status if _outcome_current(aim, context) else "invalidated"
