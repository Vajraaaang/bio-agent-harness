"""Historical in-memory investigation prototype and deterministic replay.

Migrated production lineages use the scientific authority ledger.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from pydantic import ValidationError

from devbench.adaptive.legacy import LegacyRejected, import_legacy_source
from devbench.adaptive.models import (
    BlockedStep,
    DerivedArtifact,
    FactRecord,
    FigureRecord,
    LegacyImport,
    OperationAttempt,
    PlanVersion,
    QuestionContract,
    StepOutcome,
    StepSpec,
    UseInvalidation,
    VerificationRecord,
)
from devbench.engine.provenance import canonical_json


class ChainError(ValueError):
    pass


class PlanRejected(ValueError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class AttemptView:
    attempt: OperationAttempt
    disposition: str
    observation: str
    reason: str | None = None


@dataclass(frozen=True)
class RejectionView:
    reason: str
    subject_id: str


@dataclass(frozen=True)
class InvestigationView:
    plans: tuple[PlanVersion, ...]
    active_revision: int | None
    question: QuestionContract | None
    facts: tuple[FactRecord, ...]
    attempts: tuple[AttemptView, ...]
    artifacts: tuple[DerivedArtifact, ...]
    outcomes: tuple[StepOutcome, ...]
    blocked: tuple[BlockedStep, ...]
    figures: tuple[FigureRecord, ...]
    verifications: tuple[VerificationRecord, ...]
    invalidations: tuple[UseInvalidation, ...]
    legacy_imports: tuple[LegacyImport, ...]
    rejections: tuple[RejectionView, ...]
    root: str | None

    def active_plan(self) -> PlanVersion | None:
        for plan in self.plans:
            if plan.revision == self.active_revision:
                return plan
        return None

    def ready_frontier(self) -> tuple[str, ...]:
        plan = self.active_plan()
        if plan is None:
            return ()
        outcomes = {outcome.step_id: outcome for outcome in self.outcomes}
        blocked = {item.step_id for item in self.blocked}
        unreachable = set()
        for step_id in blocked:
            unreachable.update(compute_descendants(plan, step_id))
        ready = []
        for node in plan.nodes:
            if node.step_id in outcomes or node.step_id in blocked or node.step_id in plan.retired:
                continue
            if node.step_id in unreachable:
                continue
            if _inference_blocker(node, self.question) is not None:
                continue
            edges = [
                edge for edge in plan.edges
                if edge.kind == "compute" and edge.target_id == node.step_id
            ]
            if all(_predecessor_satisfied(edge, outcomes) for edge in edges):
                ready.append(node.step_id)
        return tuple(ready)

    def inspect_history(self) -> dict:
        """Bounded projection. Artifact bytes and expression values are omitted."""
        return {
            "root": self.root,
            "active_revision": self.active_revision,
            "ready_frontier": list(self.ready_frontier()),
            "facts": [fact.fact_class for fact in self.facts],
            "attempts": [
                {
                    "attempt_id": item.attempt.attempt_id,
                    "disposition": item.disposition,
                    "observation": item.observation,
                    "idempotency_key": item.attempt.idempotency_key,
                }
                for item in self.attempts
            ],
            "artifacts": [
                {"digest": artifact.content_digest, "kind": artifact.kind}
                for artifact in self.artifacts
            ],
            "outcomes": [
                {"step_id": outcome.step_id, "execution_status": outcome.execution_status}
                for outcome in self.outcomes
            ],
            "blocked": [item.step_id for item in self.blocked],
            "figures": [{"figure_id": figure.figure_id, "state": figure.state} for figure in self.figures],
            "rejections": [item.reason for item in self.rejections],
            "legacy_imports": [item.source_sha256 for item in self.legacy_imports],
        }


def compute_descendants(plan: PlanVersion, step_id: str) -> tuple[str, ...]:
    outgoing: dict[str, list[str]] = {}
    for edge in plan.edges:
        if edge.kind == "compute":
            outgoing.setdefault(edge.source_id, []).append(edge.target_id)
    seen = set()
    stack = list(outgoing.get(step_id, ()))
    while stack:
        node = stack.pop()
        if node in seen:
            continue
        seen.add(node)
        stack.extend(outgoing.get(node, ()))
    return tuple(sorted(seen))


def _inference_blocker(node: StepSpec, question: QuestionContract | None) -> str | None:
    if node.operation.payload_type != "analysis":
        return None
    if question is None or question.question_id != node.question_id:
        return "CONTRACT_REQUIRED"
    if question.conflicts:
        return "CONFLICTING_FACTS"
    return None


def _predecessor_satisfied(edge, outcomes: dict[str, StepOutcome]) -> bool:
    outcome = outcomes.get(edge.source_id)
    if outcome is None:
        return False
    if edge.source_port is None:
        return outcome.execution_status == "completed"
    return any(
        port.port == edge.source_port and port.status == "fulfilled" for port in outcome.ports
    )


def _proposal_digest(raw) -> str:
    try:
        body = canonical_json(raw)
    except (TypeError, ValueError):
        body = repr(type(raw).__name__).encode("utf-8")
    return hashlib.sha256(body).hexdigest()


def _assert_plan_shape(plan: PlanVersion) -> None:
    node_ids = [node.step_id for node in plan.nodes]
    if len(node_ids) != len(set(node_ids)) or not node_ids:
        raise PlanRejected("NODE_IDENTITY")
    edge_ids = [edge.edge_id for edge in plan.edges]
    if len(edge_ids) != len(set(edge_ids)):
        raise PlanRejected("EDGE_IDENTITY")
    known = set(node_ids)
    for edge in plan.edges:
        if edge.source_id not in known or edge.target_id not in known:
            raise PlanRejected("EDGE_ENDPOINT")
    for node in plan.nodes:
        if node.question_id != plan.question_id or node.plan_id != plan.plan_id:
            raise PlanRejected("PLAN_BINDING")
        compute_sources = tuple(
            edge.source_id for edge in plan.edges
            if edge.kind == "compute" and edge.target_id == node.step_id
        )
        if tuple(node.dependencies) != compute_sources:
            raise PlanRejected("DEPENDENCY_EDGE_MISMATCH")
    color: dict[str, int] = {}

    def visit(step_id: str) -> None:
        state = color.get(step_id, 0)
        if state == 1:
            raise PlanRejected("CYCLE")
        if state == 2:
            return
        color[step_id] = 1
        for edge in plan.edges:
            if edge.source_id == step_id:
                visit(edge.target_id)
        color[step_id] = 2

    for step_id in node_ids:
        visit(step_id)


def _assert_revision(plan: PlanVersion, active: int | None, expected: int | None) -> None:
    if expected != active:
        raise PlanRejected("STALE_REVISION")
    if active is None:
        if plan.revision != 0 or plan.parent_revision is not None:
            raise PlanRejected("INITIAL_REVISION")
        return
    if plan.revision != active + 1 or plan.parent_revision != active:
        raise PlanRejected("REVISION_SEQUENCE")


class _Build:
    def __init__(self):
        self.plans = []
        self.active = None
        self.question = None
        self.facts = []
        self.attempts = []
        self.artifacts = []
        self.outcomes = []
        self.blocked = []
        self.figures = []
        self.verifications = []
        self.invalidations = []
        self.legacy = []
        self.rejections = []
        self.root = None


def _audit(state: _Build) -> None:
    accepted_keys = []
    artifact_ids = {artifact.content_digest for artifact in state.artifacts}
    verification_ids = {
        verification.attempt_id
        for verification in state.verifications
        if verification.result == "passed"
    }
    for item in state.attempts:
        if item.disposition != "accepted":
            continue
        accepted_keys.append(item.attempt.idempotency_key)
        if item.attempt.attempt_id not in verification_ids:
            raise ChainError("ACCEPTED_WITHOUT_VERIFICATION")
    if len(accepted_keys) != len(set(accepted_keys)):
        raise ChainError("DUPLICATE_ACCEPTANCE")
    for invalidation in state.invalidations:
        if invalidation.artifact_digest not in artifact_ids:
            raise ChainError("INVALIDATION_DROPPED_BYTES")
    revisions = [plan.revision for plan in state.plans]
    if revisions != list(range(len(revisions))):
        raise ChainError("PLAN_REVISION_GAP")


def _apply(state: _Build, payload: dict) -> None:
    kind = payload["event_type"]
    if kind == "plan_committed":
        plan = PlanVersion.model_validate(payload["plan"])
        _assert_plan_shape(plan)
        if state.active is None and (plan.revision != 0 or plan.parent_revision is not None):
            raise ChainError("ILLEGAL_PLAN")
        if state.active is not None and (
            plan.revision != state.active + 1 or plan.parent_revision != state.active
        ):
            raise ChainError("ILLEGAL_PLAN")
        state.plans.append(plan)
        state.active = plan.revision
    elif kind == "invalid_proposal":
        state.rejections.append(RejectionView(payload["error_code"], payload["proposal_digest"]))
    elif kind == "question_recorded":
        state.question = QuestionContract.model_validate(payload["question"])
    elif kind == "fact_recorded":
        state.facts.append(FactRecord.model_validate(payload["fact"]))
    elif kind == "attempt_reserved":
        attempt = OperationAttempt.model_validate(payload["attempt"])
        state.attempts.append(AttemptView(attempt, "reserved", "reserved", None))
    elif kind == "attempt_rejected":
        attempt = OperationAttempt.model_validate(payload["attempt"])
        state.attempts.append(AttemptView(attempt, "rejected", "rejected", payload["reason"]))
        state.rejections.append(RejectionView(payload["reason"], attempt.attempt_id))
    elif kind == "attempt_observed":
        _replace_attempt(state, payload["attempt_id"], observation=payload["observation"])
    elif kind == "attempt_accepted":
        _replace_attempt(state, payload["attempt_id"], disposition="accepted", observation="accepted", reason=None)
    elif kind == "acceptance_rejected":
        disposition = "rejected" if payload["reason"] == "DUPLICATE_ACCEPTANCE" else None
        observation = "rejected" if disposition == "rejected" else None
        _replace_attempt(
            state,
            payload["attempt_id"],
            disposition=disposition,
            observation=observation,
            reason=payload["reason"],
        )
        state.rejections.append(RejectionView(payload["reason"], payload["attempt_id"]))
    elif kind == "artifact_published":
        state.artifacts.append(DerivedArtifact.model_validate(payload["artifact"]))
    elif kind == "outcome_recorded":
        state.outcomes.append(StepOutcome.model_validate(payload["outcome"]))
    elif kind == "step_blocked":
        state.blocked.append(BlockedStep.model_validate(payload["blocked"]))
    elif kind == "figure_recorded":
        state.figures.append(FigureRecord.model_validate(payload["figure"]))
    elif kind == "verification_recorded":
        state.verifications.append(VerificationRecord.model_validate(payload["verification"]))
    elif kind == "use_invalidated":
        state.invalidations.append(UseInvalidation.model_validate(payload["invalidation"]))
    elif kind == "legacy_imported":
        state.legacy.append(LegacyImport.model_validate(payload["legacy"]))
    elif kind == "legacy_rejected":
        state.rejections.append(RejectionView(payload["reason"], payload.get("source_sha256", "")))
    else:
        raise ChainError("UNKNOWN_EVENT")


def replay(events) -> InvestigationView:
    """Rebuild the investigation from stored events without executing a method."""
    state = _Build()
    previous = None
    for index, event in enumerate(events):
        payload = event["payload"]
        digest = hashlib.sha256(canonical_json({"payload": payload, "previous": previous})).hexdigest()
        if (
            event["sequence"] != index
            or event["previous_digest"] != previous
            or event["event_digest"] != digest
        ):
            raise ChainError("CHAIN_MISMATCH")
        _apply(state, payload)
        previous = digest
    state.root = previous
    _audit(state)
    return InvestigationView(
        plans=tuple(state.plans),
        active_revision=state.active,
        question=state.question,
        facts=tuple(state.facts),
        attempts=tuple(sorted(state.attempts, key=lambda item: (item.attempt.attempt_id, item.disposition))),
        artifacts=tuple(state.artifacts),
        outcomes=tuple(state.outcomes),
        blocked=tuple(state.blocked),
        figures=tuple(state.figures),
        verifications=tuple(state.verifications),
        invalidations=tuple(state.invalidations),
        legacy_imports=tuple(state.legacy),
        rejections=tuple(state.rejections),
        root=state.root,
    )


class InvestigationLedger:
    """Coordinator-side record. Callers propose; this object decides what is stored."""

    def __init__(self):
        self._events: list[dict] = []

    def export(self) -> tuple[dict, ...]:
        return tuple(self._events)

    def view(self) -> InvestigationView:
        return replay(self._events)

    def propose_plan(self, raw: dict, *, expected_revision: int | None) -> str:
        view = self.view()
        try:
            plan = PlanVersion.model_validate(raw)
            _assert_plan_shape(plan)
            _assert_revision(plan, view.active_revision, expected_revision)
        except (ValidationError, PlanRejected, ValueError) as error:
            code = error.code if isinstance(error, PlanRejected) else "INVALID_PLAN"
            self._append({
                "event_type": "invalid_proposal",
                "error_code": code,
                "proposal_digest": _proposal_digest(raw),
            })
            return "invalid_proposal"
        self._append({"event_type": "plan_committed", "plan": plan.model_dump(mode="json")})
        return "plan_committed"

    def record_question(self, contract: QuestionContract) -> None:
        self._append({"event_type": "question_recorded", "question": contract.model_dump(mode="json")})

    def record_fact(self, fact: FactRecord) -> None:
        self._append({"event_type": "fact_recorded", "fact": fact.model_dump(mode="json")})

    def reserve_attempt(self, raw: dict) -> str:
        view = self.view()
        try:
            attempt = OperationAttempt.model_validate(raw)
        except ValidationError:
            self._append({
                "event_type": "invalid_proposal",
                "error_code": "INVALID_ATTEMPT",
                "proposal_digest": _proposal_digest(raw),
            })
            return "invalid_proposal"
        if attempt.state != "reserved":
            return self._reject_attempt(attempt, "INVALID_RESERVATION")
        if any(item.attempt.attempt_id == attempt.attempt_id for item in view.attempts):
            return self._reject_attempt(attempt, "DUPLICATE_ATTEMPT_ID")
        plan = view.active_plan()
        known = set() if plan is None else {node.content_digest for node in plan.nodes}
        if attempt.spec_digest not in known or attempt.plan_revision != view.active_revision:
            return self._reject_attempt(attempt, "STALE_OR_UNKNOWN_SPEC")
        for item in view.attempts:
            if item.attempt.idempotency_key != attempt.idempotency_key or item.disposition == "rejected":
                continue
            if item.disposition == "accepted":
                return self._reject_attempt(attempt, "DUPLICATE_OPERATION")
            if item.observation == "recovery_required":
                return self._reject_attempt(attempt, "UNCERTAIN_OPERATION")
            if item.observation == "reserved":
                return self._reject_attempt(attempt, "RESERVATION_OPEN")
        self._append({"event_type": "attempt_reserved", "attempt": attempt.model_dump(mode="json")})
        return "attempt_reserved"

    def observe_attempt(self, attempt_id: str, observation: str) -> str:
        if observation not in {"unknown", "noncompletion", "cancelled"}:
            raise ValueError("UNKNOWN_OBSERVATION")
        item = self._reserved(attempt_id)
        if item is None:
            self._append({
                "event_type": "invalid_proposal",
                "error_code": "UNKNOWN_ATTEMPT",
                "proposal_digest": _proposal_digest({"attempt_id": attempt_id}),
            })
            return "invalid_proposal"
        mapped = {"unknown": "recovery_required", "noncompletion": "failed", "cancelled": "cancelled"}
        self._append({
            "event_type": "attempt_observed",
            "attempt_id": attempt_id,
            "observation": mapped[observation],
        })
        return "attempt_observed"

    def publish_artifact(self, artifact: DerivedArtifact) -> str:
        view = self.view()
        known = {
            item.attempt.attempt_id for item in view.attempts
            if item.disposition == "reserved"
        }
        if artifact.producer_attempt_id not in known:
            self._append({
                "event_type": "invalid_proposal",
                "error_code": "UNKNOWN_PRODUCER",
                "proposal_digest": artifact.content_digest,
            })
            return "invalid_proposal"
        self._append({"event_type": "artifact_published", "artifact": artifact.model_dump(mode="json")})
        return "artifact_published"

    def record_outcome(self, outcome: StepOutcome) -> str:
        view = self.view()
        plan = view.active_plan()
        nodes = {} if plan is None else {node.step_id: node for node in plan.nodes}
        node = nodes.get(outcome.step_id)
        reserved = {
            item.attempt.attempt_id for item in view.attempts if item.disposition == "reserved"
        }
        published = {
            artifact.content_digest: artifact for artifact in view.artifacts
            if artifact.producer_attempt_id == outcome.attempt_id
        }
        expected_ports = () if node is None else tuple(port.name for port in node.output_ports)
        actual_ports = tuple(port.port for port in outcome.ports)
        bound = all(
            port.artifact_id in published for port in outcome.ports if port.status == "fulfilled"
        )
        if node is None or outcome.attempt_id not in reserved or actual_ports != expected_ports or not bound:
            self._append({
                "event_type": "invalid_proposal",
                "error_code": "UNBOUND_OUTCOME",
                "proposal_digest": _proposal_digest(outcome.model_dump(mode="json")),
            })
            return "invalid_proposal"
        self._append({"event_type": "outcome_recorded", "outcome": outcome.model_dump(mode="json")})
        return "outcome_recorded"

    def block_step(self, blocked: BlockedStep) -> str:
        self._append({"event_type": "step_blocked", "blocked": blocked.model_dump(mode="json")})
        return "step_blocked"

    def record_figure(self, figure: FigureRecord) -> str:
        self._append({"event_type": "figure_recorded", "figure": figure.model_dump(mode="json")})
        return "figure_recorded"

    def record_verification(self, verification: VerificationRecord) -> str:
        self._append({
            "event_type": "verification_recorded",
            "verification": verification.model_dump(mode="json"),
        })
        return "verification_recorded"

    def accept_attempt(self, attempt_id: str) -> str:
        view = self.view()
        matches = [item for item in view.attempts if item.attempt.attempt_id == attempt_id]
        if len(matches) != 1 or matches[0].disposition != "reserved":
            self._append({
                "event_type": "invalid_proposal",
                "error_code": "UNKNOWN_ATTEMPT",
                "proposal_digest": _proposal_digest({"attempt_id": attempt_id}),
            })
            return "invalid_proposal"
        item = matches[0]
        if any(
            other.disposition == "accepted" and other.attempt.idempotency_key == item.attempt.idempotency_key
            for other in view.attempts
        ):
            self._append({
                "event_type": "acceptance_rejected",
                "attempt_id": attempt_id,
                "reason": "DUPLICATE_ACCEPTANCE",
            })
            return "acceptance_rejected"
        passed = [
            verification for verification in view.verifications
            if verification.attempt_id == attempt_id and verification.result == "passed"
        ]
        if item.observation == "recovery_required" and not passed:
            self._append({
                "event_type": "acceptance_rejected",
                "attempt_id": attempt_id,
                "reason": "COMPLETION_UNCERTAIN",
            })
            return "acceptance_rejected"
        outcomes = [outcome for outcome in view.outcomes if outcome.attempt_id == attempt_id]
        fulfilled = [
            port.artifact_id
            for outcome in outcomes
            for port in outcome.ports
            if port.status == "fulfilled"
        ]
        published = {artifact.content_digest for artifact in view.artifacts}
        evidence = set() if not passed else set(passed[-1].evidence_ids)
        if (
            item.attempt.plan_revision != view.active_revision
            or not passed
            or not outcomes
            or set(fulfilled) != evidence
            or not set(fulfilled).issubset(published)
        ):
            self._append({
                "event_type": "acceptance_rejected",
                "attempt_id": attempt_id,
                "reason": "STALE_OR_UNVERIFIED",
            })
            return "acceptance_rejected"
        self._append({"event_type": "attempt_accepted", "attempt_id": attempt_id})
        return "attempt_accepted"

    def invalidate_use(self, invalidation: UseInvalidation) -> str:
        view = self.view()
        if invalidation.artifact_digest not in {artifact.content_digest for artifact in view.artifacts}:
            self._append({
                "event_type": "invalid_proposal",
                "error_code": "UNKNOWN_ARTIFACT",
                "proposal_digest": invalidation.artifact_digest,
            })
            return "invalid_proposal"
        self._append({
            "event_type": "use_invalidated",
            "invalidation": invalidation.model_dump(mode="json"),
        })
        return "use_invalidated"

    def import_legacy(self, raw: dict) -> str:
        try:
            imported = import_legacy_source(raw)
        except LegacyRejected as error:
            self._append({
                "event_type": "legacy_rejected",
                "reason": error.code,
                "source_sha256": raw.get("source_sha256") if isinstance(raw.get("source_sha256"), str) else "",
            })
            return "legacy_rejected"
        self._append({"event_type": "legacy_imported", "legacy": imported.model_dump(mode="json")})
        return "legacy_imported"

    def _reserved(self, attempt_id: str) -> AttemptView | None:
        for item in self.view().attempts:
            if item.attempt.attempt_id == attempt_id and item.disposition == "reserved":
                return item
        return None

    def _reject_attempt(self, attempt: OperationAttempt, reason: str) -> str:
        self._append({
            "event_type": "attempt_rejected",
            "attempt": attempt.model_dump(mode="json"),
            "reason": reason,
        })
        return "attempt_rejected"

    def _append(self, payload: dict) -> None:
        payload = json_payload(payload)
        previous = None if not self._events else self._events[-1]["event_digest"]
        digest = hashlib.sha256(canonical_json({"payload": payload, "previous": previous})).hexdigest()
        self._events.append({
            "sequence": len(self._events),
            "previous_digest": previous,
            "event_digest": digest,
            "payload": payload,
        })


def json_payload(payload: dict) -> dict:
    return json.loads(canonical_json(payload))


def _replace_attempt(state: _Build, attempt_id: str, *, disposition=None, observation=None, reason=None) -> None:
    for index, item in enumerate(state.attempts):
        if item.attempt.attempt_id == attempt_id and item.disposition == "reserved":
            state.attempts[index] = AttemptView(
                item.attempt,
                item.disposition if disposition is None else disposition,
                item.observation if observation is None else observation,
                item.reason if reason is None else reason,
            )
            return
    raise ChainError("UNKNOWN_ATTEMPT")
