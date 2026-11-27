"""Prospective joint-family declarations attached to the scientific ledger."""

import time
from contextlib import nullcontext
from sqlite3 import Connection, Row

from pydantic import Field, TypeAdapter

from devbench.adaptive.models import AdaptiveRecord
from devbench.core._hashing import canonical_sha256
from devbench.core.joint_bh import JointBHPlan, PlanID
from devbench.engine.scientific_authority import _projection_event
from devbench.engine.workflow_operations import (
    OperationBlocked,
    OperationConflict,
    _digest,
    _json,
)

from .adaptive_workflow_state import Digest, Identity
from .adaptive_workflow_state import _State as AdaptiveState
from .thesis_aims import AimMultiplicity, Text, ThesisAimLifecycle, _Aim, _Projection

_EVENT = "thesis_joint_bh_plan"


class RetainedJointBHPlan(AdaptiveRecord):
    plan: JointBHPlan
    actor: Identity
    rationale: Text
    registered_at: int = Field(gt=0)
    aim_versions: dict[Identity, int] = Field(max_length=32)
    aim_definitions_sha256: Digest


def read_joint_event(db: Connection, lineage_id: str | None, key: str) -> Row | None:
    rows = db.execute(
        """SELECT r.lineage_id,r.revision,r.record_id,r.kind,r.binding_id,
          r.publication_unit,
          CASE WHEN length(CAST(r.payload AS BLOB)) <= 65536 THEN r.payload END
            AS payload,
          r.sha256,o.sha256 AS outbox_digest FROM scientific_records r
          LEFT JOIN scientific_outbox o ON r.lineage_id=o.lineage_id
            AND r.revision=o.revision
          WHERE r.record_id=? AND r.kind='execution_event'
            AND (? IS NULL OR r.lineage_id=?) LIMIT 2""",
        (key, lineage_id, lineage_id),
    ).fetchall()
    if len(rows) > 1:
        raise OperationConflict("Joint BH record identity is ambiguous")
    return rows[0] if rows else None


def _read_plan(
    db: Connection, lineage_id: str, family_id: str
) -> tuple[str, RetainedJointBHPlan | None, int | None]:
    key = "thesis-joint-bh-" + canonical_sha256(family_id)
    row = read_joint_event(db, lineage_id, key)
    if row is None:
        return key, None, None
    record = _projection_event(row)["record"]
    if record["event"] != _EVENT:
        raise OperationConflict("Invalid joint BH record namespace")
    retained = RetainedJointBHPlan.model_validate_json(_json(record["state"]))
    if retained.plan.family_id != family_id:
        raise OperationConflict("Joint BH record differs from its family identity")
    return key, retained, row["revision"]


def _inventory(
    state: _Projection, plan: JointBHPlan
) -> tuple[dict[str, _Aim], str, dict[str, int]]:
    scope = AimMultiplicity(
        family_id=plan.family_id,
        aim_ids=plan.aim_ids,
        hypotheses_sha256=plan.sha256(),
        fdr=plan.fdr,
    )
    members = {}
    for aim_id in plan.aim_ids:
        aim = state.aims.get(aim_id)
        if (
            aim is None
            or aim.invalidated_by is not None
            or aim.definition.kind != "confirmatory"
            or aim.definition.multiplicity != scope
        ):
            raise OperationBlocked("thesis_joint_bh_aim_scope_changed")
        members[aim_id] = aim
    definitions = canonical_sha256(
        {key: aim.definition.model_dump(mode="json") for key, aim in members.items()}
    )
    versions = {key: aim.version for key, aim in members.items()}
    return members, definitions, versions


def _check_current(state: _Projection, retained: RetainedJointBHPlan) -> None:
    _, definitions, versions = _inventory(state, retained.plan)
    if (
        retained.aim_definitions_sha256 != definitions
        or retained.aim_versions != versions
    ):
        raise OperationBlocked("thesis_joint_bh_aim_versions_changed")


def _require_prospective(adaptive: AdaptiveState, plan: JointBHPlan) -> None:
    steps = {member.step_id for member in plan.members}
    parameters = {member.parameters_sha256 for member in plan.members}
    for dispatch in adaptive.dispatches.values():
        step = dispatch["envelope"]["step"]
        if step["step_id"] in steps or step["parameters_sha256"] in parameters:
            raise OperationConflict("Freeze the plan before a member fit is dispatched")


class ThesisJointBHRegistry:
    """Retain an operator-attributed declaration before any member approval."""

    def __init__(self, aims: ThesisAimLifecycle):
        self.aims = aims

    def freeze(
        self,
        plan: JointBHPlan,
        *,
        actor: str,
        rationale: str,
        connection: Connection | None = None,
    ) -> int:
        actor, rationale = self.aims._actor(actor, rationale)
        plan = JointBHPlan.model_validate(plan)
        authority = self.aims.authority
        lineage = self.aims.lineage_id
        if connection is not None:
            from devbench.engine.scientific_authority import (
                require_same_ledger_transaction,
            )

            require_same_ledger_transaction(connection, self.aims.ledger)
        transaction = (
            authority._execution_tx(lineage)
            if connection is None
            else nullcontext(connection)
        )
        with transaction as db:
            key, retained, revision = _read_plan(db, lineage, plan.family_id)
            state, _ = self.aims._projection(db)
            if retained is not None:
                if retained.plan != plan:
                    raise OperationConflict("A retained joint BH plan is immutable")
                _check_current(state, retained)
                assert revision is not None
                return revision
            members, definitions, versions = _inventory(state, plan)
            if any(aim.approval is not None for aim in members.values()):
                raise OperationConflict("Freeze the plan before the first aim approval")
            _require_prospective(self.aims.workflow._state(db), plan)
            declared = RetainedJointBHPlan(
                plan=plan,
                actor=actor,
                rationale=rationale,
                registered_at=time.time_ns(),
                aim_versions=versions,
                aim_definitions_sha256=definitions,
            )
            document = declared.model_dump(mode="json")
            return authority._append_execution_record(
                db,
                lineage,
                {
                    "kind": "execution_event",
                    "event": _EVENT,
                    "record_id": key,
                    "state": document,
                    "state_sha256": _digest(_json(document)),
                },
            )

    def read(
        self, family_id: str, *, connection: Connection | None = None
    ) -> RetainedJointBHPlan:
        identity = TypeAdapter(PlanID).validate_python(family_id)

        def read_current(db: Connection) -> RetainedJointBHPlan:
            _, retained, _ = _read_plan(db, self.aims.lineage_id, identity)
            if retained is None:
                raise OperationBlocked("thesis_joint_bh_plan_missing")
            state, _ = self.aims._projection(db)
            _check_current(state, retained)
            return retained

        return self.aims._read(read_current, connection)
