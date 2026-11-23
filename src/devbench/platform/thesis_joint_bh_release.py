"""Atomically release a cross-aim BH family from current Tier A receipts."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from sqlite3 import Connection, Row
from typing import Any, BinaryIO, Literal

from pydantic import Field

from devbench.adaptive.models import AdaptiveRecord
from devbench.core._hashing import canonical_sha256
from devbench.core.contrast_design import TIER_A_BULK_OUTPUT, PipelineSpec
from devbench.core.joint_bh import (
    JOINT_BH_INPUT,
    JOINT_BH_OUTPUT_PREFIX,
    JointBHFitScope,
    JointBHMember,
    JointBHPlan,
    adjust_joint_bh,
)
from devbench.engine.joint_bh_parquet import encode_pooled_table, read_probabilities
from devbench.engine.partition_authority import embargoed
from devbench.engine.scientific_authority import (
    _projection_event,
    require_same_ledger_transaction,
)
from devbench.engine.tier_a_executor import TierAExecutor
from devbench.engine.workflow_operations import (
    OperationBlocked,
    OperationConflict,
    _digest,
    _json,
)

from .adaptive_workflow import AdaptiveWorkflow, request_digest
from .adaptive_workflow_state import ArtifactVersion, Digest, Identity
from .adaptive_workflow_state import _State as AdaptiveState
from .thesis_aims import ThesisAimLifecycle
from .thesis_joint_bh import (
    RetainedJointBHPlan,
    ThesisJointBHRegistry,
    read_joint_event,
)

_EVENT = "thesis_joint_bh_result"
_RECORD = "thesis-joint-bh-result-"
_VERSION = re.compile(r"joint-bh-pooled-([a-f0-9]{64})-([0-9]{1,2})")


class JointFitSource(AdaptiveRecord):
    aim_id: Identity
    step_id: Identity
    generation: int = Field(gt=0)
    attempt_id: Identity
    receipt_sha256: Digest
    artifact_sha256: Digest
    accepted_record_id: Identity


class JointBHRelease(AdaptiveRecord):
    format: Literal["devbench.thesis-joint-bh-release/1"] = (
        "devbench.thesis-joint-bh-release/1"
    )
    lineage_id: Identity
    family_id: Identity
    plan_sha256: Digest
    plan: JointBHPlan
    publication_sha256: Digest
    fdr: float = Field(gt=0, lt=1)
    attempted_hypotheses: int = Field(ge=2)
    sources: tuple[JointFitSource, ...] = Field(min_length=2, max_length=32)
    outputs: dict[Identity, ArtifactVersion] = Field(min_length=2, max_length=32)

    def receipt_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json"))


@dataclass(frozen=True, slots=True)
class _MemberFit:
    source: JointFitSource
    artifact: ArtifactVersion
    envelope: dict[str, Any]


def _require_accepted(fit: _MemberFit, attempt: dict[str, Any]) -> None:
    if (
        attempt["acceptance_state"] != "accepted"
        or attempt["output_record_id"] != fit.source.accepted_record_id
        or attempt["verification_sha256"] != fit.source.receipt_sha256
        or attempt["artifact_sha256"]
        != request_digest(
            {
                TIER_A_BULK_OUTPUT: fit.artifact.model_dump(mode="json"),
            }
        )
    ):
        raise OperationConflict("Joint BH member acceptance identity changed")


def _member_fit(
    flow: AdaptiveWorkflow,
    db: Connection,
    state: AdaptiveState,
    retained: RetainedJointBHPlan,
    member: JointBHMember,
    *,
    claim: bool,
) -> _MemberFit:
    step_id = member.step_id
    if step_id is None or step_id not in state.nodes:
        raise OperationBlocked("thesis_joint_bh_member_pending")
    step = state.nodes[step_id]
    generation = state.generations[step_id]
    if (
        step.branch_id in state.retired
        or flow._held_ancestry(state, step_id)
        or (step_id, generation) in state.claim_stale
    ):
        raise OperationBlocked("thesis_joint_bh_member_invalidated")
    outcome = state.outcome(step_id)
    if outcome is None:
        raise OperationBlocked("thesis_joint_bh_member_pending")
    if outcome["status"] != "succeeded":
        raise OperationBlocked("thesis_joint_bh_member_failed")
    dispatch = state.dispatches[outcome["attempt_id"]]
    envelope = dispatch["envelope"]
    artifact = ArtifactVersion.model_validate(outcome["outputs"][TIER_A_BULK_OUTPUT])
    if artifact.version_id in state.stale_versions:
        raise OperationBlocked("thesis_joint_bh_member_invalidated")
    _require_member_scope(envelope, retained, member, artifact)
    if claim and flow._review_basis(db, step) != envelope.get("design_review"):
        raise OperationBlocked("thesis_joint_bh_member_review_changed")
    digests = (
        artifact.sha256,
        member.parameters_sha256,
        member.family_sha256,
        *(pin["sha256"] for pin in envelope["pins"]),
    )
    if any(embargoed(db, digest) for digest in digests):
        raise OperationBlocked("thesis_joint_bh_member_protected")
    evidence = JointFitSource(
        aim_id=member.aim_id,
        step_id=step_id,
        generation=generation,
        attempt_id=outcome["attempt_id"],
        receipt_sha256=outcome["receipt_sha256"],
        artifact_sha256=artifact.sha256,
        accepted_record_id=outcome["accepted_record_id"],
    )
    fit = _MemberFit(evidence, artifact, envelope)
    _require_accepted(fit, flow.authority._checked_attempt(db, dispatch["token"]))
    return fit


def _require_member_scope(
    envelope: dict[str, Any],
    retained: RetainedJointBHPlan,
    member: JointBHMember,
    artifact: ArtifactVersion,
) -> None:
    scope = envelope.get("thesis_scope", {})
    expected = JointBHFitScope(
        family_id=retained.plan.family_id,
        plan_sha256=retained.plan.sha256(),
        member=member,
    )
    if (
        artifact.representation != JOINT_BH_INPUT
        or artifact.permitted_uses
        or scope.get("aim_version") != retained.aim_versions[member.aim_id]
        or scope.get("joint_bh") != expected.model_dump(mode="json")
    ):
        raise OperationBlocked("thesis_joint_bh_member_scope_changed")


@dataclass(frozen=True, slots=True)
class _FamilySnapshot:
    retained: RetainedJointBHPlan
    fits: tuple[_MemberFit, ...]
    lineage_id: str

    @property
    def sources(self) -> tuple[JointFitSource, ...]:
        return tuple(item.source for item in self.fits)

    @property
    def publication_sha256(self) -> str:
        return canonical_sha256(
            {
                "format": "devbench.thesis-joint-bh-release/1",
                "lineage_id": self.lineage_id,
                "plan_sha256": self.retained.plan.sha256(),
                "sources": [item.model_dump(mode="json") for item in self.sources],
            }
        )


def _snapshot(
    flow: AdaptiveWorkflow,
    db: Connection,
    state: AdaptiveState,
    family_id: str,
    *,
    claim: bool,
) -> _FamilySnapshot:
    aims = ThesisAimLifecycle(flow.ledger, flow.lineage_id)
    retained = ThesisJointBHRegistry(aims).read(family_id, connection=db)
    fits = tuple(
        _member_fit(flow, db, state, retained, member, claim=claim)
        for member in retained.plan.members
    )
    return _FamilySnapshot(retained, fits, flow.lineage_id)


def _decode_release(row: Row) -> JointBHRelease:
    record = _projection_event(row)["record"]
    release = JointBHRelease.model_validate_json(_json(record["state"]))
    if (
        record["event"] != _EVENT
        or record["record_id"] != _RECORD + release.publication_sha256
        or row["lineage_id"] != release.lineage_id
    ):
        raise OperationConflict("Joint BH release record identity changed")
    return release


def _output_version(identity: str, index: int) -> str:
    return f"{JOINT_BH_OUTPUT_PREFIX}{identity}-{index}"


def _require_current_release(
    db: Connection,
    release: JointBHRelease,
    snapshot: _FamilySnapshot,
) -> None:
    plan = snapshot.retained.plan
    if (
        release.plan_sha256 != plan.sha256()
        or release.plan != plan
        or release.family_id != plan.family_id
        or release.fdr != plan.fdr
        or release.publication_sha256 != snapshot.publication_sha256
        or release.sources != snapshot.sources
        or release.outputs.keys() != set(plan.aim_ids)
        or release.attempted_hypotheses != sum(m.feature_count for m in plan.members)
    ):
        raise OperationBlocked("thesis_joint_bh_release_invalidated")
    for index, fit in enumerate(snapshot.fits):
        artifact = release.outputs[fit.source.aim_id]
        if (
            artifact.version_id != _output_version(release.publication_sha256, index)
            or artifact
            != _published_artifact(fit, artifact.version_id, artifact.sha256)
            or embargoed(db, artifact.sha256)
        ):
            raise OperationBlocked("thesis_joint_bh_release_output_changed")


def _retained_release(
    db: Connection, snapshot: _FamilySnapshot
) -> JointBHRelease | None:
    row = read_joint_event(
        db, snapshot.lineage_id, _RECORD + snapshot.publication_sha256
    )
    if row is None:
        return None
    release = _decode_release(row)
    _require_current_release(db, release, snapshot)
    return release


def _published_artifact(fit: _MemberFit, version: str, digest: str) -> ArtifactVersion:
    return fit.artifact.model_copy(
        update={
            "version_id": version,
            "sha256": digest,
            "representation": "parquet",
            "permitted_uses": ("checked_observation", "admitted_research_inference"),
        }
    )


def _adjust(executor: TierAExecutor, snapshot: _FamilySnapshot) -> JointBHRelease:
    plan = snapshot.retained.plan
    specifications: dict[str, PipelineSpec] = {}
    measures = {}
    for fit in snapshot.fits:
        source = fit.source
        receipt = executor.load_receipt(fit.envelope, attempt_id=source.attempt_id)
        if (
            receipt is None
            or request_digest(receipt.model_dump(mode="json")) != source.receipt_sha256
            or receipt.outputs.get(TIER_A_BULK_OUTPUT) != fit.artifact
        ):
            raise OperationBlocked("thesis_joint_bh_member_receipt_changed")
        specification = executor._pipeline(fit.envelope)
        specifications[source.aim_id] = specification
        measures[source.aim_id] = read_probabilities(
            fit.artifact,
            executor._open_bulk_output,
            family_sha256=specification.family_sha256,
        )
    adjusted = adjust_joint_bh(
        plan, specifications=specifications, measurements=measures
    )
    publication = snapshot.publication_sha256
    outputs = {}
    for index, fit in enumerate(snapshot.fits):
        aim_id = fit.source.aim_id
        raw = encode_pooled_table(
            fit.artifact,
            executor._open_bulk_output,
            adjusted[aim_id],
            publication_sha256=publication,
        )
        sha256 = executor._outputs.put(raw)
        outputs[aim_id] = _published_artifact(
            fit, _output_version(publication, index), sha256
        )
    return JointBHRelease(
        lineage_id=snapshot.lineage_id,
        family_id=plan.family_id,
        plan_sha256=plan.sha256(),
        plan=plan,
        publication_sha256=publication,
        fdr=plan.fdr,
        attempted_hypotheses=sum(member.feature_count for member in plan.members),
        sources=snapshot.sources,
        outputs=outputs,
    )


def prepare_joint_bh_release(
    flow: AdaptiveWorkflow,
    executor: TierAExecutor,
    family_id: str,
    *,
    expected_publication_sha256: str | None = None,
) -> Callable[[Connection], JointBHRelease]:
    """Stage checked bytes and return a guarded publication for a ledger transaction."""
    if (
        type(executor) is not TierAExecutor
        or executor.ledger.path != flow.ledger.path
        or not executor.allow_data_execution
    ):
        raise PermissionError("Joint BH requires the bound Tier A executor")
    flow.ledger._writable()
    with flow._read_connection() as db:
        snapshot = _snapshot(flow, db, flow._state(db), family_id, claim=True)
        if (
            expected_publication_sha256 is not None
            and snapshot.publication_sha256 != expected_publication_sha256
        ):
            raise OperationBlocked("thesis_joint_bh_selected_sources_changed")
        prior = _retained_release(db, snapshot)
    release = prior if prior is not None else _adjust(executor, snapshot)

    def apply(db: Connection) -> JointBHRelease:
        require_same_ledger_transaction(db, flow.ledger)
        current = _snapshot(flow, db, flow._state(db), family_id, claim=True)
        if snapshot != current:
            raise OperationConflict("Joint BH member receipts changed before release")
        prior = _retained_release(db, current)
        if prior is not None:
            if prior != release:
                raise OperationConflict(
                    "Joint BH replay produced different output bytes"
                )
            return prior
        execution = flow.authority._load_state(db, flow.lineage_id)
        flow.authority._active(db, flow.lineage_id, execution)
        document = release.model_dump(mode="json")
        flow.authority._append_execution_record(
            db,
            flow.lineage_id,
            {
                "kind": "execution_event",
                "event": _EVENT,
                "record_id": _RECORD + release.publication_sha256,
                "state": document,
                "state_sha256": _digest(_json(document)),
            },
        )
        return release

    return apply


def joint_publication_choices(
    flow: AdaptiveWorkflow,
    db: Connection,
    state: AdaptiveState,
) -> list[dict[str, str]]:
    families = set()
    for step_id in state.nodes:
        outcome = state.outcome(step_id)
        if outcome is not None and outcome["status"] == "succeeded":
            envelope = state.dispatches[outcome["attempt_id"]]["envelope"]
            joint = envelope.get("thesis_scope", {}).get("joint_bh")
            if joint is not None:
                families.add(JointBHFitScope.model_validate(joint).family_id)
    choices = []
    for family_id in sorted(families):
        try:
            snapshot = _snapshot(flow, db, state, family_id, claim=False)
            current = _retained_release(db, snapshot)
        except OperationBlocked:
            continue
        if current is None:
            choices.append(
                {
                    "family_id": family_id,
                    "publication_sha256": snapshot.publication_sha256,
                }
            )
    return choices


def current_joint_bh_release(
    flow: AdaptiveWorkflow,
    db: Connection,
    state: AdaptiveState,
    outcome: dict[str, Any],
    *,
    claim: bool,
) -> tuple[JointBHRelease, str]:
    envelope = state.dispatches[outcome["attempt_id"]]["envelope"]
    scope = JointBHFitScope.model_validate(envelope["thesis_scope"]["joint_bh"])
    try:
        snapshot = _snapshot(flow, db, state, scope.family_id, claim=claim)
    except OperationBlocked as error:
        if error.state == "thesis_joint_bh_member_pending":
            raise OperationBlocked("thesis_joint_bh_member_unpublished") from error
        raise
    release = _retained_release(db, snapshot)
    if release is None:
        raise OperationBlocked("thesis_joint_bh_member_unpublished")
    return release, scope.member.aim_id


@contextmanager
def open_pooled_output(
    executor: TierAExecutor,
    artifact: ArtifactVersion,
) -> Iterator[BinaryIO]:
    match = _VERSION.fullmatch(artifact.version_id)
    if match is None:
        raise PermissionError("Invalid joint BH output identity")
    with executor.ledger._database() as db:
        db.execute("BEGIN")
        row = read_joint_event(db, None, _RECORD + match[1])
        if row is None:
            raise OperationBlocked("thesis_joint_bh_release_unavailable")
        release = _decode_release(row)
        flow = AdaptiveWorkflow(executor.ledger, release.lineage_id)
        snapshot = _snapshot(flow, db, flow._state(db), release.family_id, claim=False)
        _require_current_release(db, release, snapshot)
        if artifact not in release.outputs.values():
            raise PermissionError("Joint BH output differs from its retained release")
        with executor._outputs.open(artifact.sha256) as source:
            yield source
