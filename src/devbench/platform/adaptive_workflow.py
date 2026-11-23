"""Versioned adaptive scheduling backed by the scientific authority ledger."""

import hashlib
import json
import math
import time
import uuid
from contextlib import contextmanager, nullcontext
from copy import deepcopy

from pydantic import TypeAdapter

from devbench.adaptive.models import AdaptiveRecord as AdaptiveRecord
from devbench.adaptive.models import ConsumerPurpose as ConsumerPurpose
from devbench.adaptive.models import Port as Port
from devbench.core.procedure_profile import ProcedureProfile as ProcedureProfile
from devbench.engine.partition_authority import PartitionAuthority, embargoed
from devbench.engine.scientific_authority import (
    _ID,
    _check_lineage,
    _matches,
    _row,
    append_scientific_projection_event,
    load_scientific_projection,
    require_same_ledger_transaction,
)
from devbench.engine.workflow_operations import (
    OperationBlocked,
    OperationConflict,
    _digest,
    _json,
)

from .adaptive_workflow_state import (
    AdaptiveStep as AdaptiveStep,
)
from .adaptive_workflow_state import (
    ArtifactPin as ArtifactPin,
)
from .adaptive_workflow_state import (
    ArtifactVersion as ArtifactVersion,
)
from .adaptive_workflow_state import (
    BasisKind as BasisKind,
)
from .adaptive_workflow_state import (
    BasisUse as BasisUse,
)
from .adaptive_workflow_state import (
    Digest as Digest,
)
from .adaptive_workflow_state import (
    Identity as Identity,
)
from .adaptive_workflow_state import (
    InputLink as InputLink,
)
from .adaptive_workflow_state import (
    RetirementReason as RetirementReason,
)
from .adaptive_workflow_state import (
    RevisionReason as RevisionReason,
)
from .adaptive_workflow_state import (
    StepReceipt as StepReceipt,
)
from .adaptive_workflow_state import (
    Use as Use,
)
from .adaptive_workflow_state import (
    _State as _State,
)
from .adaptive_workflow_state import (
    validate_dag as _validate_dag,
)

ADAPTIVE_MODE = "adaptive/1"
_MAX_STEPS = 512
RESEARCH_INFERENCE = "admitted_research_inference"
_RECOVERABLE_BACKENDS = {"local_registered_tier_a_1", "local_exploratory_oci_1"}


def request_digest(envelope):
    return _digest(_json(envelope))


def validate_dag(nodes):
    return _validate_dag(nodes, maximum_steps=_MAX_STEPS)


def _sealed_nb_input(step, link, artifact):
    from devbench.engine.tier_a_pathways import is_curated_sealed_input

    return (
        step.purpose == "checked_observation"
        and link.source_port == link.port.name
        and is_curated_sealed_input(
            step.operation_id, link.port.name, artifact.representation
        )
        and not artifact.permitted_uses
    )


def _requires_tier_a(step):
    from devbench.engine.tier_a_scrna import is_single_cell_operation

    return step["purpose"] == RESEARCH_INFERENCE or is_single_cell_operation(
        step["operation_id"]
    )


class AdaptiveWorkflow:
    """Plan current uses and reserve actual work through the T11 execution epoch."""

    def __init__(
        self,
        ledger,
        lineage_id,
        *,
        partition_check=None,
        review_admission=None,
        diagnostic_admission=None,
    ):
        _check_lineage(lineage_id)
        self.ledger = ledger
        self.lineage_id = lineage_id
        self.authority = ledger.scientific
        self.partition_check = partition_check
        self.manual_local_oci = False
        if review_admission is not None and not callable(review_admission):
            raise TypeError(
                "Primary review admission must be a trusted coordinator capability"
            )
        self.review_admission = review_admission
        if diagnostic_admission is not None and not callable(diagnostic_admission):
            raise TypeError(
                "Diagnostic admission must be a trusted coordinator capability"
            )
        self.diagnostic_admission = diagnostic_admission

    def _state(self, db):
        return self._projection(db)[0]

    def _projection(self, db):
        return load_scientific_projection(
            db,
            self.lineage_id,
            "adaptive",
            initial=_State,
            apply=_State.apply_verified,
            restore=_State.from_checkpoint,
        )

    def _event(self, db, event, state, *, projection=None):
        prior, head = self._projection(db) if projection is None else projection
        if event == "controller":
            previous = prior.controller
            count = len(previous["decisions"])
            if state["decisions"][:count] != previous["decisions"] or len(
                state["decisions"]
            ) - count not in {0, 1}:
                raise OperationConflict("Controller history is append-only")
            change = {
                "format": "devbench.controller-change/1",
                **{
                    key: state[key]
                    for key in ("binding", "pending", "outcome")
                    if state[key] != previous[key]
                },
            }
            if len(state["decisions"]) > count:
                change["decision"] = state["decisions"][-1]
            state = change
        return append_scientific_projection_event(
            self.authority,
            db,
            self.lineage_id,
            "adaptive",
            {
                "kind": "adaptive_event",
                "record_id": "adaptive-" + uuid.uuid4().hex,
                "event": event,
                "state": state,
                "state_sha256": request_digest(state),
            },
            head.snapshot(prior.checkpoint_state),
        )

    def _mutate(self, event, record, validate, expected_revision, *, connection=None):
        self.ledger._writable()
        _check_lineage(self.lineage_id, expected_revision)
        if connection is not None:
            require_same_ledger_transaction(connection, self.ledger)
        with (
            self.authority._execution_tx(self.lineage_id)
            if connection is None
            else nullcontext(connection)
        ) as db:
            revision = self.authority._revision(db, self.lineage_id)
            if (
                revision == 0
                or expected_revision is not None
                and revision != expected_revision
            ):
                raise OperationConflict(
                    "Adaptive mutation requires its current scientific lineage revision"
                )
            projection = self._projection(db)
            validate(projection[0])
            if event == "artifact" and record.get("parents"):
                self._validate_artifact_parents(db, projection[0], record["parents"])
            if event == "step" and record["reason"] is not None:
                self._check_revision_budget(db, projection[0], record)
            return self._event(db, event, record, projection=projection)

    def _check_revision_budget(self, db, state, record):
        step_id = record["step"]["step_id"]
        reasons = [
            row[0]
            for row in db.execute(
                "SELECT json_extract(payload,'$.state.reason') FROM scientific_records "
                "WHERE lineage_id=? AND kind='adaptive_event' "
                "AND json_extract(payload,'$.event')='step' "
                "AND json_extract(payload,'$.state.step.step_id')=? "
                "AND json_extract(payload,'$.state.reason') IS NOT NULL",
                (self.lineage_id, step_id),
            )
        ]
        if record["reason"] == "retry":
            if record["step"] != state.nodes[step_id].model_dump(mode="json"):
                raise OperationBlocked("infrastructure_retry_requires_unchanged_step")
            if reasons.count("retry") >= 2:
                raise OperationBlocked(
                    "unchanged_infrastructure_retry_budget_exhausted",
                )
        elif sum(reason != "retry" for reason in reasons) >= 3:
            raise OperationBlocked("scientific_revision_budget_exhausted")

    def record_basis(
        self,
        basis_id: Identity,
        kind: BasisKind,
        sha256: Digest,
        *,
        expected_revision=None,
        connection=None,
    ):
        TypeAdapter(tuple[Identity, BasisKind, Digest]).validate_python(
            (basis_id, kind, sha256)
        )

        def validate(state):
            if basis_id in state.basis and state.basis[basis_id]["kind"] != kind:
                raise OperationConflict("Attributed basis cannot change kind")

        return self._mutate(
            "basis",
            {"basis_id": basis_id, "kind": kind, "sha256": sha256},
            validate,
            expected_revision,
            connection=connection,
        )

    @contextmanager
    def _read_connection(self, connection=None):
        with (
            self.ledger._database() if connection is None else nullcontext(connection)
        ) as db:
            if connection is None:
                db.execute("BEGIN")
            else:
                require_same_ledger_transaction(db, self.ledger)
            yield db

    def _read_projection(self, query, *, connection=None):
        if connection is None:
            return self.ledger._read(query)
        with self._read_connection(connection) as db:
            return query(db)

    def controller_state(self, *, connection=None):
        return self._read_projection(
            lambda db: deepcopy(self._state(db).controller),
            connection=connection,
        )

    def _replay_material(self, planner, connection):
        if planner is None:
            return None
        from devbench.analysis.method_planner import ScientificMethodPlanner
        from devbench.analysis.workflow_scientific_reviews import (
            WorkflowScientificReviewAdapter,
        )
        from devbench.engine.workflow_operations import LEDGER_NAME

        if (
            type(planner) is not ScientificMethodPlanner
            or planner.methods.root / LEDGER_NAME != self.ledger.path
        ):
            raise PermissionError(
                "Replay scientific specification requires the actual owning planner"
            )
        return WorkflowScientificReviewAdapter.adaptive_material(
            planner, self, connection=connection
        )

    def replay_identity(self, *, connection=None, planner=None):
        """Structural capture witness only; it grants no access or execution."""
        if connection is None:
            from devbench.engine.workflow_operations import WorkflowOperationLedger

            reader = WorkflowOperationLedger(
                self.ledger.path, readonly=True, inert=True
            )
            flow = AdaptiveWorkflow(reader, self.lineage_id)
            with flow._read_connection() as db:
                return flow.replay_identity(connection=db, planner=planner)
        with self._read_connection(connection) as db:
            rows = db.execute(
                "SELECT revision,sha256 FROM scientific_records WHERE lineage_id=? "
                "ORDER BY revision LIMIT 4097",
                (self.lineage_id,),
            ).fetchall()
            if len(rows) > 4096:
                raise OperationBlocked("replay_scientific_record_bound_exceeded")
            document = {
                "format": "devbench.investigation-capture/1",
                "lineage_id": self.lineage_id,
                "records": [{"revision": row[0], "sha256": row[1]} for row in rows],
            }
            campaigns, reservations = [], []
            if db.execute(
                "SELECT 1 FROM sqlite_master WHERE name='model_campaigns'"
            ).fetchone():
                campaigns = [
                    dict(row)
                    for row in db.execute(
                        "SELECT campaign_id,status,reason,policy_digest,"
                        "capability_digest,identity_digest FROM model_campaigns WHERE "
                        "session_id=? ORDER BY campaign_id LIMIT 65",
                        (self.lineage_id,),
                    )
                ]
                if len(campaigns) > 64:
                    raise OperationBlocked("replay_model_campaign_bound_exceeded")
                for campaign in campaigns:
                    rows = db.execute(
                        "SELECT campaign_id,node,attempt,status,request_digest,"
                        "limits_digest,capture_digest,"
                        "error_type,charge_basis FROM model_reservations WHERE "
                        "campaign_id=? ORDER BY node,attempt LIMIT 4097",
                        (campaign["campaign_id"],),
                    ).fetchall()
                    if len(rows) + len(reservations) > 4096:
                        raise OperationBlocked(
                            "replay_model_reservation_bound_exceeded"
                        )
                    reservations.extend(dict(row) for row in rows)
            document.update(model_campaigns=campaigns, model_reservations=reservations)
            access = {}
            if db.execute(
                "SELECT 1 FROM sqlite_master WHERE name='scientific_disclosure_schema'"
            ).fetchone():
                from devbench.analysis.evidence_access import EvidenceBroker

                dependencies = EvidenceBroker.open_readonly(
                    self.authority
                ).replay_dependencies(self.lineage_id, connection=db)
                access = {
                    kind: {key: record["sha256"] for key, record in values.items()}
                    for kind, values in dependencies["original_records"].items()
                }
            document["access_records"] = access
            material = self._replay_material(planner, db)
            document["scientific_material_binding"] = (
                None
                if material is None
                else {
                    "format": "devbench.scientific-material-capture/1",
                    "lineage_id": self.lineage_id,
                    "plan_sha256": request_digest(material["plan"]),
                    "objective_sha256": request_digest(
                        material["plan"]["context"]["task"]
                    ),
                    "material_sha256": request_digest(material),
                }
            )
            return {**document, "sha256": request_digest(document)}

    def replay_snapshot(self, *, entitlement, planner=None):
        """Capture actual owner-resolved history, never infer current permission."""
        from devbench.analysis.bundles import _inert_entitlement
        from devbench.analysis.evidence_access import EvidenceBroker
        from devbench.analysis.literature_adapter import _state as literature_state
        from devbench.analysis.scientific_publication import PublicationRegistry
        from devbench.analysis.scientific_review_holds import ReviewHoldLifecycle
        from devbench.engine.workflow_operations import WorkflowOperationLedger

        entitlement = _inert_entitlement(entitlement)
        entitlement.require(entitlement.content_sha256)
        reader = WorkflowOperationLedger(self.ledger.path, readonly=True, inert=True)
        flow = AdaptiveWorkflow(reader, self.lineage_id)
        with flow._read_connection() as db:
            identity = flow.replay_identity(connection=db, planner=planner)
            entitlement.require(identity["sha256"])
            rows = db.execute(
                "SELECT * FROM scientific_records WHERE lineage_id=? ORDER BY "
                "revision LIMIT 4097",
                (self.lineage_id,),
            ).fetchall()
            originals = []
            for row in rows:
                verified = _row(row)
                originals.append(
                    {
                        "revision": row["revision"],
                        "record_id": row["record_id"],
                        "kind": row["kind"],
                        "sha256": verified["sha256"],
                        "payload": row["payload"],
                    }
                )
            state = flow._state(db)
            material = flow._replay_material(planner, db)
            if (
                material is not None
                and request_digest(material)
                != identity["scientific_material_binding"]["material_sha256"]
            ):
                raise ValueError(
                    "Replay scientific material changed during owner capture"
                )
            if any(
                node.held_out or node.purpose == "protected_evaluation"
                for node in state.nodes.values()
            ):
                raise PermissionError(
                    "restricted_replay_excludes_protected_evaluation_lineages"
                )
            lifecycle = ReviewHoldLifecycle(reader.scientific, self.lineage_id)
            subjects, heads = PublicationRegistry(flow, lifecycle)._state(db)
            attempts = []
            if db.execute(
                "SELECT 1 FROM sqlite_master WHERE name='scientific_attempts'"
            ).fetchone():
                ids = db.execute(
                    "SELECT attempt_id FROM scientific_attempts WHERE lineage_id=? "
                    "ORDER BY attempt_id LIMIT 129",
                    (self.lineage_id,),
                ).fetchall()
                if len(ids) > 128:
                    raise OperationBlocked("replay_attempt_bound_exceeded")
                for (attempt_id,) in ids:
                    token = reader.scientific._load_state(
                        db, self.lineage_id, attempt_id
                    )["token"]
                    attempts.append(reader.scientific._checked_attempt(db, token))
            exposure = {
                "contexts": {},
                "policies": {},
                "sources": {},
                "disclosures": {},
                "original_records": {},
            }
            if db.execute(
                "SELECT 1 FROM sqlite_master WHERE name='scientific_disclosure_schema'"
            ).fetchone():
                exposure = EvidenceBroker.open_readonly(
                    reader.scientific
                ).replay_dependencies(self.lineage_id, connection=db)
            campaigns = []
            reservations = []
            for record in identity["model_campaigns"]:
                campaign_id = record["campaign_id"]
                campaigns.append(reader.get_campaign(campaign_id))
                reservations.extend(reader.campaign_reservations(campaign_id))
            return {
                "format": "devbench.investigation-replay-snapshot/1",
                "capture_identity": identity,
                "original_records": originals,
                "lineage_id": self.lineage_id,
                "steps": {
                    key: {
                        "specification": node.model_dump(mode="json"),
                        "generation": state.generations[key],
                    }
                    for key, node in sorted(state.nodes.items())
                },
                "basis": deepcopy(state.basis),
                "artifacts": {
                    key: {"artifact": value, "producer": producer}
                    for key, (value, producer) in state.artifacts.items()
                },
                "dispatches": deepcopy(state.dispatches),
                "outcomes": deepcopy(state.outcomes),
                "attempts": attempts,
                "retired": deepcopy(state.retired),
                "sweeps": deepcopy(state.sweeps),
                "tombstones": [
                    {"step_id": key[0], "generation": key[1], "reason": value}
                    for key, value in sorted(state.tombstones.items())
                ],
                "claim_stale": [
                    {"step_id": key[0], "generation": key[1], "reason": value}
                    for key, value in sorted(state.claim_stale.items())
                ],
                "source_basis": deepcopy(state.sources),
                "holds": deepcopy(state.holds),
                "stale_versions": sorted(state.stale_versions),
                "comparison_versions": sorted(state.comparison_versions),
                "controller": deepcopy(state.controller),
                "model_campaign": campaigns[0] if len(campaigns) == 1 else None,
                "model_campaigns": campaigns,
                "model_reservations": reservations,
                "reviews": lifecycle.state(connection=db),
                "exposures": exposure,
                "publication_subjects": {
                    key: value.model_dump(mode="json")
                    for key, value in subjects.items()
                },
                "publication_heads": heads,
                "literature": literature_state(db, self.lineage_id),
                "scientific_material": material,
                "scientific_acceptance": "not_granted",
                "current_use": "unavailable",
            }

    def bind_controller(self, binding):
        """An engineering sublimit and catalog cannot reset the external grant."""
        _json(binding)
        if (
            type(binding.get("max_decisions")) is not int
            or not 1 <= binding["max_decisions"] <= 24
        ):
            raise ValueError("Controller decision ceiling is 24")
        with self.authority._execution_tx(self.lineage_id) as db:
            state = self._state(db).controller
            if state["binding"] is not None:
                if state["binding"] != binding:
                    raise OperationConflict(
                        "Controller campaign, catalog or limits cannot reset on resume"
                    )
                return deepcopy(state)
            state["binding"] = deepcopy(binding)
            self._event(db, "controller", state)
            return deepcopy(state)

    def begin_controller_decision(self, context, *, expected_revision):
        with self.authority._execution_tx(self.lineage_id) as db:
            if self.authority._revision(db, self.lineage_id) != expected_revision:
                raise OperationConflict("Controller context revision changed")
            state = self._state(db).controller
            if state["pending"] is not None:
                raise OperationBlocked("controller_decision_pending")
            if state["binding"] is None or state["outcome"] is not None:
                raise OperationBlocked("controller_not_active")
            sequence = len(state["decisions"])
            if sequence >= state["binding"]["max_decisions"]:
                raise OperationBlocked("controller_decision_budget_exhausted")
            state["pending"] = {
                "sequence": sequence,
                "context": deepcopy(context),
                "context_sha256": request_digest(context),
                "decision": None,
                "evidence": None,
            }
            self._event(db, "controller", state)
            return deepcopy(state["pending"])

    def select_controller_decision(self, sequence, decision, evidence):
        with self.authority._execution_tx(self.lineage_id) as db:
            state = self._state(db).controller
            pending = state["pending"]
            if pending is None or pending["sequence"] != sequence:
                raise OperationConflict("Controller decision has no current intent")
            if pending["decision"] is not None:
                if pending["decision"] != decision or pending["evidence"] != evidence:
                    raise OperationConflict("Controller selected decision is immutable")
                return deepcopy(pending)
            pending.update(decision=deepcopy(decision), evidence=deepcopy(evidence))
            self._event(db, "controller", state)
            return deepcopy(pending)

    def reject_controller_selection(self, sequence, selection_index, rejection):
        """Retain bounded feedback without replacing the selected scientific context."""
        if (
            set(rejection) != {"reason", "response_sha256"}
            or not isinstance(rejection["reason"], str)
            or len(rejection["reason"]) > 128
        ):
            raise ValueError("Use bounded controller selection feedback")
        TypeAdapter(Digest).validate_python(rejection["response_sha256"])
        with self.authority._execution_tx(self.lineage_id) as db:
            state = self._state(db).controller
            pending = state["pending"]
            if (
                pending is None
                or pending["sequence"] != sequence
                or pending["decision"] is not None
            ):
                raise OperationConflict(
                    "Feedback requires its exact unselected controller intent"
                )
            rejected = pending.setdefault("rejected_selections", [])
            if len(rejected) != selection_index or len(rejected) >= 2:
                raise OperationConflict(
                    "Controller feedback cannot reset the selection budget"
                )
            rejected.append(deepcopy(rejection))
            self._event(db, "controller", state)
            return deepcopy(rejected)

    def complete_controller_decision(
        self, sequence, result, *, apply=None, expected_revision=None, connection=None
    ):
        """Structural mutations and their decision receipt share one transaction."""
        if connection is not None:
            self.ledger._writable()
            require_same_ledger_transaction(connection, self.ledger)
        with (
            self.authority._execution_tx(self.lineage_id)
            if connection is None
            else nullcontext(connection)
        ) as db:
            if (
                expected_revision is not None
                and self.authority._revision(db, self.lineage_id) != expected_revision
            ):
                raise OperationConflict("Controller evidence changed before mutation")
            state = self._state(db).controller
            pending = state["pending"]
            if (
                pending is None
                or pending["sequence"] != sequence
                or pending["decision"] is None
            ):
                raise OperationConflict(
                    "Controller completion has no selected decision"
                )
            if apply is not None:
                self._controller_admission(db, state)
                result = apply(db)
            result = json.loads(json.dumps(result, allow_nan=False))
            # The immutable intent event retains the full context. Repeating
            # all earlier contexts in every event would exhaust the ledger's
            # bounded record size during an otherwise small investigation.
            state["decisions"].append(
                {
                    **{
                        key: value for key, value in pending.items() if key != "context"
                    },
                    "result": deepcopy(result),
                }
            )
            state["pending"] = None
            self._event(db, "controller", state)
            return deepcopy(result)

    def _controller_campaign(self, db, controller):
        binding = controller["binding"]
        if binding is not None:
            campaign = self.ledger._campaign(db, binding["campaign_id"])
            if campaign["identity_digest"] != binding["campaign_identity_sha256"]:
                raise OperationConflict("Controller campaign identity changed")
            return campaign

    def _controller_admission(self, db, controller):
        campaign = self._controller_campaign(db, controller)
        if campaign is not None:
            self.ledger._dispatchable(db, campaign)

    def _controller_stop_outcome(self, db, controller, outcome):
        """A truthful stop retains cancellation even after the last plan read."""
        campaign = self._controller_campaign(db, controller)
        if campaign is not None and campaign["status"] == "cancelled":
            outcome = {
                **outcome,
                "reason": "cancelled",
                "resumption_conditions": [
                    "Start a new explicitly authorized campaign as a separate run, "
                    "linked to the retained cancelled campaign, evidence and spend; "
                    "this is not same-investigation resume."
                ],
            }
            if outcome["status"] == "completed_exploratory":
                outcome["status"] = (
                    "partial" if outcome["completed_steps"] else "unresolved"
                )
        return outcome

    def stop_controller(self, outcome):
        with self.authority._execution_tx(self.lineage_id) as db:
            state = self._state(db).controller
            if state["outcome"] is not None:
                return deepcopy(state["outcome"])
            outcome = outcome(db) if callable(outcome) else outcome
            if outcome["status"] not in {"partial", "unresolved"}:
                raise PermissionError(
                    "Completed outcomes require the atomic selected-stop gate"
                )
            state["outcome"] = deepcopy(outcome)
            self._event(db, "controller", state)
            return deepcopy(outcome)

    def finish_controller_stop(self, sequence, *, expected_revision, prepare):
        """Validate and commit the selected stop and terminal outcome together."""
        if not callable(prepare):
            raise TypeError("Stop acceptance requires a trusted current-state builder")
        with self.authority._execution_tx(self.lineage_id) as db:
            if self.authority._revision(db, self.lineage_id) != expected_revision:
                raise OperationConflict(
                    "Controller evidence changed before stop acceptance"
                )
            state = self._state(db).controller
            pending = state["pending"]
            if (
                state["outcome"] is not None
                or pending is None
                or pending["sequence"] != sequence
                or pending["decision"] is None
                or pending["decision"]["kind"] != "stop"
            ):
                raise OperationConflict(
                    "Terminal acceptance requires the exact selected stop"
                )
            outcome = self._controller_stop_outcome(db, state, prepare(db))
            if outcome["status"] == "completed_exploratory":
                self._controller_admission(db, state)
                status = self._status(db, self._state(db))
                if status["ready"] or status["blocked"] or status["execution_block"]:
                    raise OperationBlocked("current_completed_scope_required")
                from devbench.analysis.scientific_review_holds import (
                    ReviewHoldLifecycle,
                )

                review = ReviewHoldLifecycle(self.authority, self.lineage_id).state(
                    connection=db
                )
                if any(hold["status"] == "open" for hold in review["holds"].values()):
                    raise OperationBlocked(
                        "current_reviews_and_resolved_scope_required"
                    )
            elif outcome["status"] not in {"partial", "unresolved"}:
                raise ValueError("Unknown controller terminal disposition")
            result = {"status": "stopped", "reason": outcome["reason"]}
            state["decisions"].append(
                {
                    **{
                        key: value for key, value in pending.items() if key != "context"
                    },
                    "result": result,
                }
            )
            state["pending"] = None
            outcome["decisions"] = len(state["decisions"])
            state["outcome"] = deepcopy(outcome)
            self._event(db, "controller", state)
            return deepcopy(outcome)

    def resume_controller(self, *, expected_outcome_sha256, expected_revision=None):
        with self.authority._execution_tx(self.lineage_id) as db:
            if (
                expected_revision is not None
                and self.authority._revision(db, self.lineage_id) != expected_revision
            ):
                raise OperationConflict(
                    "Adaptive workflow revision changed before resumption"
                )
            state = self._state(db).controller
            if (
                state["outcome"] is None
                or request_digest(state["outcome"]) != expected_outcome_sha256
            ):
                raise OperationConflict(
                    "Controller resumption requires the exact retained outcome"
                )
            self.authority._active(
                db, self.lineage_id, self.authority._load_state(db, self.lineage_id)
            )
            state["outcome"] = None
            self._event(db, "controller", state)
            return deepcopy(state)

    def register_artifact(
        self, artifact, *, basis=(), parents=(),
        expected_revision=None, connection=None,
    ):
        from devbench.core.joint_bh import JOINT_BH_INPUT, JOINT_BH_OUTPUT_PREFIX

        artifact = ArtifactVersion.model_validate(artifact)
        parents = TypeAdapter(tuple[Identity, ...]).validate_python(parents)
        if len(parents) > 16 or len(set(parents)) != len(parents):
            raise ValueError("Derived artifacts require at most 16 distinct parents")
        if (
            artifact.representation == JOINT_BH_INPUT
            or artifact.version_id.startswith(JOINT_BH_OUTPUT_PREFIX)
        ):
            raise PermissionError("Joint BH artifacts require their retained receipts")

        def validate(state):
            if artifact.version_id in state.artifacts:
                raise OperationConflict("Immutable artifact version already exists")
            if set(basis) - set(state.basis):
                raise ValueError("Artifact basis is unregistered")

        record = {"artifact": artifact.model_dump(mode="json"), "basis": list(basis)}
        if parents:
            record["parents"] = list(parents)
        return self._mutate(
            "artifact", record,
            validate,
            expected_revision,
            connection=connection,
        )

    def _validate_artifact_parents(self, db, state, parents):
        if set(parents) - state.artifacts.keys():
            raise ValueError("Derived artifact parents must already be registered")
        for version in state.artifact_ancestors(parents, include_execution=True):
            item, producer = state.artifacts[version]
            if version in state.stale_versions or (
                producer is not None
                and (
                    state.generations[producer[0]] != producer[1]
                    or state.nodes[producer[0]].branch_id in state.retired
                    or self._held_ancestry(state, producer[0])
                )
            ):
                raise OperationBlocked("adaptive_derived_parent_not_current")
            if embargoed(db, item["sha256"]):
                raise PermissionError("Derived artifacts cannot use protected parents")

    @staticmethod
    def _validate_step_edit(state, step, reason):
        from .thesis_workflow import has_thesis_scope

        if (step.step_id in state.nodes) != (reason is not None):
            raise OperationConflict(
                "Use insertion or revision for the matching step state"
            )
        if step.branch_id in state.retired:
            raise OperationBlocked("adaptive_branch_retired")
        if reason is not None and state.nodes[step.step_id].branch_id != step.branch_id:
            raise ValueError("Step revision cannot move branch identity")
        if reason is not None and has_thesis_scope(state.nodes[step.step_id]):
            if not has_thesis_scope(step):
                raise OperationBlocked("thesis_scope_revision_cannot_unbind")
        if any(ref.basis_id not in state.basis for ref in step.basis):
            raise ValueError("Step basis is unregistered")
        if any(
            link.source_port is None and link.source_id not in state.artifacts
            for link in step.inputs
        ):
            raise ValueError("Immutable input version is unregistered")
        validate_dag({**state.nodes, step.step_id: step})

    def _edit_step(
        self, step, reason, evidence_sha256, expected_revision, *, connection=None
    ):
        step = AdaptiveStep.model_validate(
            step.model_dump() if isinstance(step, AdaptiveStep) else step
        )
        if reason is not None:
            TypeAdapter(RevisionReason).validate_python(reason)
            if evidence_sha256 is None:
                raise ValueError("Revision requires its triggering evidence digest")
        if evidence_sha256 is not None:
            TypeAdapter(Digest).validate_python(evidence_sha256)

        def validate(state):
            self._validate_step_edit(state, step, reason)

        return self._mutate(
            "step",
            {
                "step": step.model_dump(mode="json"),
                "reason": reason,
                "evidence_sha256": evidence_sha256,
            },
            validate,
            expected_revision,
            connection=connection,
        )

    def insert(
        self, step, *, evidence_sha256=None, expected_revision=None, connection=None
    ):
        return self._edit_step(
            step, None, evidence_sha256, expected_revision, connection=connection
        )

    def candidate_envelope(self, step, *, actor, connection=None):
        """Resolve an exact proposed/current step without insertion or authority.

        The candidate exists only in a detached projection. This runs the same
        insertion and envelope checks, including effective environment, inherited
        basis, pins, representation and scope, without reserving a job or grant.
        """
        step = AdaptiveStep.model_validate(
            step.model_dump() if isinstance(step, AdaptiveStep) else step
        )
        if not _matches(_ID, actor):
            raise ValueError("Candidate envelopes require an attributed actor")
        if connection is not None:
            require_same_ledger_transaction(connection, self.ledger)
        with (
            self.ledger._database() if connection is None else nullcontext(connection)
        ) as db:
            if connection is None:
                db.execute("BEGIN")
            state = self._state(db)
            existing = state.nodes.get(step.step_id)
            if existing is None:
                self._validate_step_edit(state, step, None)
                state.apply(
                    "step",
                    {
                        "step": step.model_dump(mode="json"),
                        "reason": None,
                        "evidence_sha256": None,
                    },
                )
            elif existing.model_dump(mode="json") != step.model_dump(mode="json"):
                raise OperationConflict(
                    "Candidate step differs from the current registered action"
                )
            return self._envelope(state, step.step_id, actor, db=db)

    def revise(
        self, step, *, reason, evidence_sha256, expected_revision=None, connection=None
    ):
        return self._edit_step(
            step, reason, evidence_sha256, expected_revision, connection=connection
        )

    def retire_branch(
        self,
        branch_id,
        *,
        reason,
        evidence_sha256,
        expected_revision=None,
        connection=None,
    ):
        TypeAdapter(tuple[Identity, RetirementReason, Digest]).validate_python(
            (branch_id, reason, evidence_sha256)
        )

        def validate(state):
            if branch_id in state.retired or not any(
                node.branch_id == branch_id for node in state.nodes.values()
            ):
                raise OperationConflict("Branch is absent or already retired")

        return self._mutate(
            "retire",
            {
                "branch_id": branch_id,
                "reason": reason,
                "evidence_sha256": evidence_sha256,
            },
            validate,
            expected_revision,
            connection=connection,
        )

    def hold(self, step_id, reason, *, evidence_sha256, expected_revision=None):
        TypeAdapter(tuple[Identity, Identity | None, Digest]).validate_python(
            (step_id, reason, evidence_sha256)
        )

        def validate(state):
            if (
                step_id not in state.nodes
                or reason is None
                and step_id not in state.holds
            ):
                raise OperationConflict(
                    "Hold or resolution does not identify a current step"
                )

        return self._mutate(
            "hold",
            {"step_id": step_id, "reason": reason, "evidence_sha256": evidence_sha256},
            validate,
            expected_revision,
        )

    def declare_sweep(
        self, sweep_id, members, *, expected_revision=None, connection=None
    ):
        TypeAdapter(tuple[Identity, dict[Identity, Identity | None]]).validate_python(
            (sweep_id, members)
        )
        if not members or len(members) > _MAX_STEPS:
            raise ValueError("Sweep needs a bounded, complete membership")

        def validate(state):
            if sweep_id in state.sweeps:
                raise OperationConflict("Sweep membership is immutable")
            if any(
                reason is None and key not in state.nodes
                for key, reason in members.items()
            ):
                raise ValueError("Sweep members without a step need an omission reason")

        return self._mutate(
            "sweep",
            {"sweep_id": sweep_id, "members": members},
            validate,
            expected_revision,
            connection=connection,
        )

    @staticmethod
    def _held_ancestry(state, step_id):
        pending, visited = [step_id], set()
        while pending:
            current = pending.pop()
            if current in visited:
                continue
            visited.add(current)
            if current in state.holds:
                return True
            for link in state.nodes[current].inputs:
                if link.source_port is not None:
                    pending.append(link.source_id)
                else:
                    pending.extend(
                        AdaptiveWorkflow._artifact_producers(state, link.source_id)
                    )
        return False

    @staticmethod
    def _artifact_producers(state, version_id):
        for version in state.artifact_ancestors((version_id,)):
            if producer := state.artifacts[version][1]:
                yield producer[0]

    def _pinned_input(self, state, step, link):
        from devbench.core.joint_bh import JOINT_BH_OUTPUT_PREFIX

        if link.source_id.startswith(JOINT_BH_OUTPUT_PREFIX) and (
            step.operation_id != "rna_pathway_tested"
            or link.port.name != "pathway_statistics"
        ):
            raise OperationBlocked("adaptive_joint_bh_pathway_input_only")
        raw, producer = state.artifacts[link.source_id]
        if raw["version_id"] in state.stale_versions:
            raise OperationBlocked("adaptive_input_version_invalidated")
        if link.source_id in state.derived_parents and any(
            self._held_ancestry(state, name)
            for name in self._artifact_producers(state, link.source_id)
        ):
            raise OperationBlocked("adaptive_dependency_held")
        if producer is not None:
            name, generation = producer
            if self._held_ancestry(state, name):
                raise OperationBlocked("adaptive_dependency_held")
            current = (
                generation == state.generations[name]
                and state.nodes[name].branch_id not in state.retired
            )
            comparison = (
                step.comparison and raw["version_id"] in state.comparison_versions
            )
            if not current and not comparison:
                raise OperationBlocked(
                    "adaptive_historical_input_requires_declared_comparison",
                )
        return raw

    def _upstream_input(self, state, link):
        producer = state.nodes[link.source_id]
        result = state.outcome(link.source_id)
        if producer.branch_id in state.retired or self._held_ancestry(
            state, link.source_id
        ):
            raise OperationBlocked("adaptive_dependency_held_or_retired")
        if result is None:
            raise OperationBlocked("adaptive_dependency_pending")
        if result["status"] != "succeeded":
            raise OperationBlocked("adaptive_dependency_failed")
        raw = result["outputs"].get(link.source_port)
        if raw is None:
            if not link.port.required:
                return None
            raise OperationBlocked("adaptive_dependency_output_unavailable")
        if raw["version_id"] in state.stale_versions:
            raise OperationBlocked("adaptive_input_version_invalidated")
        return raw

    def _input(self, state, step, link):
        raw = (
            self._pinned_input(state, step, link)
            if link.source_port is None
            else self._upstream_input(state, link)
        )
        if raw is None:
            return None
        artifact = ArtifactVersion.model_validate(raw)
        if (
            artifact.kind != link.port.artifact_kind
            or artifact.representation not in step.profile.input_representations
        ):
            raise OperationBlocked("adaptive_input_representation_mismatch")
        if artifact.biological_unit not in step.profile.biological_units:
            raise OperationBlocked("adaptive_biological_unit_mismatch")
        from devbench.core.contrast_design import (
            TIER_A_COUNT_REPRESENTATIONS,
            TIER_A_MOUNTED_COUNT_REPRESENTATIONS,
        )

        if (
            artifact.kind == "counts"
            and artifact.representation == "tximport_counts_h5"
        ):
            if (
                artifact.measurement_history != ("tximport_estimated_counts",)
                or step.operation_id != "bulk_condition_contrast"
                or step.purpose != RESEARCH_INFERENCE
            ):
                raise OperationBlocked("adaptive_tximport_measurement_history")
        elif artifact.kind == "counts" and (
            not artifact.measurement_history
            or artifact.representation
            not in (
                *TIER_A_COUNT_REPRESENTATIONS,
                *TIER_A_MOUNTED_COUNT_REPRESENTATIONS,
            )
            or any(
                value.casefold()
                not in {
                    "raw",
                    "raw_counts",
                    "counted",
                    "umi_counts",
                    "non_umi_counts",
                    "integer_counts",
                    "pseudobulk",
                    "lane_sum",
                    "count-1",
                }
                for value in artifact.measurement_history
            )
        ):
            raise OperationBlocked("adaptive_COUNT-1_measurement_history")
        if (
            step.purpose not in artifact.permitted_uses
            and not _sealed_nb_input(step, link, artifact)
        ):
            raise OperationBlocked("adaptive_input_purpose_not_permitted")
        if (
            artifact.label_origin != "none"
            and step.profile.label_scope
            and artifact.label_origin not in step.profile.label_scope
        ):
            raise OperationBlocked("adaptive_label_scope_mismatch")
        if (
            step.purpose in {"qualified_inference", RESEARCH_INFERENCE}
            and artifact.selection_status != "none"
        ):
            raise OperationBlocked("adaptive_selection_restricts_inference")
        return artifact

    def _review_basis(self, db, step):
        if self.review_admission is None:
            raise OperationBlocked("completed_current_design_review_required")
        research = step.purpose == RESEARCH_INFERENCE
        if research:
            from .workflow_actions import WorkflowActions

            owner = getattr(self.review_admission, "__self__", None)
            if (
                type(owner) is not WorkflowActions
                or owner.ledger.path != self.ledger.path
                or getattr(self.review_admission, "__func__", None)
                is not WorkflowActions.adaptive_review_admission
            ):
                raise OperationBlocked("adaptive_research_requires_bound_admission")
        basis = self.review_admission(db, step)
        if research and (
            not isinstance(basis, dict)
            or basis.get("research_admission", {}).get("format")
            != "devbench.rna-research-admission/1"
        ):
            raise OperationBlocked("adaptive_research_admission_required")
        return basis

    def _step_inputs(self, state, step):
        inputs = {}
        for link in step.inputs:
            artifact = self._input(state, step, link)
            if artifact is not None:
                inputs[link.port.name] = artifact
        if (
            step.shared_units
            and len(
                {
                    (item.biological_unit, item.unit_membership_sha256)
                    for item in inputs.values()
                }
            )
            > 1
        ):
            raise OperationBlocked("adaptive_input_unit_membership_mismatch")
        return inputs

    def _inference_review_basis(self, db, step, inputs):
        research = step.purpose == RESEARCH_INFERENCE
        if research and (step.oci_method_version_id is not None or step.held_out):
            raise OperationBlocked("adaptive_research_requires_tier_a")
        if not (
            step.primary
            or step.purpose
            in {
                "qualified_inference",
                RESEARCH_INFERENCE,
            }
        ):
            return None
        if step.regime == "P-P":
            labels = [item for item in inputs.values() if item.label_origin != "none"]
            if not labels or any(
                item.label_origin != "independent"
                and (
                    item.label_origin != "rna_derived"
                    or item.label_qualification_sha256 is None
                    or item.label_qualification_sha256
                    not in step.profile.qualification_evidence
                )
                or item.selection_status != "none"
                for item in labels
            ):
                raise OperationBlocked(
                    "adaptive_primary_P-P_requires_independent_or_qualified_RNA_labels"
                )
        claim_kind = RESEARCH_INFERENCE if research else "qualified_procedure_result"
        if claim_kind not in step.profile.permitted_claims:
            raise OperationBlocked(
                "adaptive_research_requires_admitted_profile"
                if research
                else "adaptive_primary_requires_qualified_procedure",
            )
        if step.profile.design_scope and step.regime not in step.profile.design_scope:
            raise OperationBlocked("adaptive_regime_outside_procedure_scope")
        return self._review_basis(db, step)

    def _computation_basis(self, db, state, step, inputs):
        basis = {
            ref.basis_id: {**state.basis[ref.basis_id], "use": ref.use}
            for ref in step.basis
        }
        protected = any(embargoed(db, item.sha256) for item in inputs.values())
        if protected and (
            type(self.partition_check) is not PartitionAuthority
            or step.purpose != "protected_evaluation"
            or not step.held_out
            or step.oci_method_version_id is None
        ):
            raise OperationBlocked("adaptive_protected_partition_mount_denied")
        if step.oci_method_version_id is not None:
            # Unknown output lineage depends on every input and the attributed
            # evidence and decisions that produced those inputs.
            for item in inputs.values():
                for key in state.sources.get(item.version_id, ()):
                    basis.setdefault(key, {**state.basis[key], "use": "compute"})
                producer = state.artifacts[item.version_id][1]
                if producer is not None:
                    outcome = state.latest[producer]
                    ancestors = state.dispatches[outcome["attempt_id"]]["envelope"][
                        "basis"
                    ]
                    for key, entry in ancestors.items():
                        if entry["kind"] != "environment":
                            existing = basis.setdefault(key, entry)
                            if existing["sha256"] != entry["sha256"]:
                                raise OperationBlocked(
                                    "adaptive_input_inherited_basis_mismatch"
                                )
        return basis

    @staticmethod
    def _derived_source_digests(state, versions):
        if not state.derived_parents:
            return []
        ancestry = state.artifact_ancestors(versions, include_execution=True)
        derivations = [
            version for version in ancestry if version in state.derived_parents
        ]
        parents = {parent for v in derivations for parent in state.derived_parents[v]}
        digests = {
            state.artifacts[parent][0]["sha256"]
            for parent in state.artifact_ancestors(parents, include_execution=True)
        }
        digests.update(
            state.basis[key]["sha256"]
            for version in derivations
            for key in state.sources.get(version, ())
            if state.basis[key]["kind"] == "source"
        )
        return sorted(digests)

    def _derived_dispatch_sources(self, db, state, inputs):
        sources = self._derived_source_digests(
            state, (item.version_id for item in inputs.values())
        )
        if any(embargoed(db, digest) for digest in sources):
            raise OperationBlocked("adaptive_protected_partition_mount_denied")
        return sources

    def _check_derived_delivery(self, db, state, version_id):
        sources = self._derived_source_digests(state, (version_id,))
        if any(embargoed(db, digest) for digest in sources):
            raise OperationBlocked("adaptive_protected_partition_delivery_denied")

    def _check_partition_inputs(
        self,
        db,
        state,
        step,
        inputs,
        basis,
        *,
        token,
        consume,
        draft,
    ):
        if step.held_out or step.purpose == "protected_evaluation":
            if any(
                item.learned_scope in {"full_cohort", "unknown"}
                for item in inputs.values()
            ):
                raise OperationBlocked(
                    "adaptive_held_out_full_cohort_or_unknown_learned_state"
                )
            if type(self.partition_check) is PartitionAuthority:
                if self.partition_check.authority.ledger.path != self.ledger.path:
                    raise OperationBlocked("adaptive_partition_authority_mismatch")
                self.partition_check.check_training_ancestry(db, state, inputs)
                if not draft:
                    self.partition_check.check(
                        db,
                        self.lineage_id,
                        step,
                        inputs,
                        basis,
                        token=token,
                        consume=consume,
                    )
            elif (
                self.partition_check is None
                or self.partition_check(step, deepcopy(inputs), deepcopy(basis))
                is not True
            ):
                raise OperationBlocked("adaptive_held_out_partition_check_required")

    def _envelope(
        self,
        state,
        step_id,
        actor,
        *,
        db,
        token=None,
        consume=False,
        authorization_draft=False,
        manual_local_oci=False,
    ):
        from .thesis_workflow import ThesisWorkflow, has_thesis_scope

        manual_local_oci = self.manual_local_oci or manual_local_oci
        if manual_local_oci and state.controller["binding"] is not None:
            raise OperationBlocked("manual_local_oci_requires_unbound_controller")
        self._controller_admission(db, state.controller)
        step = state.nodes[step_id]
        if manual_local_oci and step.oci_method_version_id is None:
            raise OperationBlocked("manual_local_oci_step_required")
        if step.branch_id in state.retired:
            raise OperationBlocked("adaptive_branch_retired")
        if step_id in state.holds:
            raise OperationBlocked(state.holds[step_id]["reason"])
        inputs = self._step_inputs(state, step)
        derived_sources = self._derived_dispatch_sources(db, state, inputs)
        review_basis = self._inference_review_basis(db, step, inputs)
        basis = self._computation_basis(db, state, step, inputs)
        self._check_partition_inputs(
            db,
            state,
            step,
            inputs,
            basis,
            token=token,
            consume=consume,
            draft=authorization_draft,
        )
        environment = {
            value["sha256"]
            for value in basis.values()
            if value["kind"] == "environment"
        }
        if len(environment) > 1:
            raise OperationBlocked("adaptive_conflicting_environment_basis")
        environment_sha = next(iter(environment), step.environment_sha256)
        thesis_scope = {}
        if has_thesis_scope(step):
            lifecycle = ThesisWorkflow(self.ledger, self.lineage_id)
            thesis_scope["thesis_scope"] = lifecycle.require_step(
                db,
                step,
                inputs=inputs,
                basis=basis,
                environment=environment_sha,
            )
        return {
            "mode": ADAPTIVE_MODE,
            "lineage_id": self.lineage_id,
            "actor": actor,
            **({"manual_local_oci": True} if manual_local_oci else {}),
            **({"design_review": review_basis} if review_basis is not None else {}),
            **({"derived_source_sha256s": derived_sources} if derived_sources else {}),
            **thesis_scope,
            "environment_sha256": environment_sha,
            "step": step.model_dump(
                mode="json",
                exclude={"oci_method_version_id"}
                if step.oci_method_version_id is None
                else None,
            ),
            "generation": state.generations[step_id],
            "basis": basis,
            "inputs": {
                port: item.model_dump(mode="json") for port, item in inputs.items()
            },
            "pins": [
                ArtifactPin(
                    port=port,
                    version_id=item.version_id,
                    sha256=item.sha256,
                    graph_sha256=item.graph_sha256,
                ).model_dump(mode="json")
                for port, item in sorted(inputs.items())
            ],
        }

    def authorize_protected_step(self, step_id):
        if type(self.partition_check) is not PartitionAuthority:
            raise PermissionError(
                "A held-out evaluator requires protected partition custody"
            )
        with self.ledger._database() as db:
            db.execute("BEGIN")
            state = self._state(db)
            envelope = self._envelope(
                state, step_id, "authorization", db=db, authorization_draft=True
            )
            step = state.nodes[step_id]
            inputs = {
                name: ArtifactVersion.model_validate(item)
                for name, item in envelope["inputs"].items()
            }
        return self.partition_check.authorize(
            self.lineage_id, step, inputs, envelope["basis"]
        )

    def _pending(self, db, state):
        pending = set()
        for attempt, record in state.dispatches.items():
            step = record["envelope"]["step"]["step_id"]
            known_backend = record["token"]["backend_id"] in _RECOVERABLE_BACKENDS
            if not known_backend and (
                attempt in state.outcomes
                or record["envelope"]["generation"] != state.generations[step]
            ):
                continue
            current = self.authority._load_state(db, self.lineage_id, attempt)
            if current is None:
                raise OperationConflict("Adaptive dispatch has no scientific attempt")
            settled = (
                attempt in state.outcomes
                or current["execution_state"] == "noncompletion_confirmed"
            )
            if not settled or known_backend and current["cleanup_state"] != "confirmed":
                pending.add(step)
        return pending

    def _execution_block(self, db):
        execution = self.authority._load_state(db, self.lineage_id)
        try:
            self.authority._active(db, self.lineage_id, execution)
        except (OperationBlocked, OperationConflict) as error:
            return getattr(error, "state", str(error))
        if execution["reserved_jobs"] >= execution["max_jobs"]:
            return "scientific_budget_exhausted"
        return None

    def _status(self, db, state):
        execution_block = self._execution_block(db)
        ready, blocked, outcomes = [], {}, {}
        pending = self._pending(db, state)
        for identity in validate_dag(state.nodes):
            step = state.nodes[identity]
            outcome = state.outcome(identity)
            if step.branch_id in state.retired:
                continue
            if identity in state.holds:
                blocked[identity] = state.holds[identity]["reason"]
            elif outcome is not None:
                outcomes[identity] = outcome["status"]
            elif identity in pending:
                blocked[identity] = "adaptive_attempt_requires_reconciliation"
            elif execution_block:
                blocked[identity] = execution_block
            else:
                try:
                    self._envelope(state, identity, "readiness", db=db)
                except OperationBlocked as error:
                    blocked[identity] = error.state
                else:
                    ready.append(identity)
        return {
            "mode": ADAPTIVE_MODE,
            "revision": self.authority._revision(db, self.lineage_id),
            "ready": tuple(ready),
            "blocked": blocked,
            "outcomes": outcomes,
            "execution_block": execution_block,
        }

    def status(self, *, connection=None):
        return self._read_projection(
            lambda db: self._status(db, self._state(db)),
            connection=connection,
        )

    def ready(self):
        return self.status()["ready"]

    @staticmethod
    def _diagnostic_reservation_in_scope(
        state, review, record, *, hold_id=None, binding_sha256=None
    ):
        owner = record.get("hold_id")
        hold = review["holds"].get(owner)
        return (
            hold is not None
            and hold["status"] == "open"
            and record["affected_use"] in hold["scope"]
            and record["affected_use"] not in review["withdrawn"]
            and (hold_id is None or owner == hold_id)
            and (binding_sha256 is None or record["binding_sha256"] == binding_sha256)
            and record.get("generation", 1)
            == state.generations.get(record["action_id"], 1)
        )

    @staticmethod
    def _diagnostic_check_pending(state, step_id):
        step = state.nodes.get(step_id)
        return not any(
            members.get(step_id) is not None for members in state.sweeps.values()
        ) and (
            step is None
            or (step.branch_id not in state.retired and state.outcome(step_id) is None)
        )

    @staticmethod
    def _require_diagnostic_deadline(deadlines):
        if any(
            type(value) not in (int, float) or not math.isfinite(value)
            for value in deadlines
        ):
            raise OperationBlocked("diagnostic_check_deadline_unavailable")
        if deadlines and time.time() >= min(deadlines):
            raise OperationBlocked("diagnostic_check_budget_exhausted")

    def _diagnostic_requirements(
        self, db, state, envelope, *, token=None, admission_boundary
    ):
        from devbench.analysis.rna_diagnostic_policy import (
            verify_retained_diagnostic_action,
        )
        from devbench.analysis.scientific_review_holds import ReviewHoldLifecycle

        step_id, generation = envelope["step"]["step_id"], envelope["generation"]
        review = ReviewHoldLifecycle(self.authority, self.lineage_id)._state(db)
        starts, proofs = [], []
        for event in review["history"]:
            action, detail = event["action"], event["detail"]
            if (
                action not in {"diagnostic_check_start", "diagnostic_action_accepted"}
                or detail["action_id"] != step_id
            ):
                continue
            (starts if action == "diagnostic_check_start" else proofs).append(detail)
        current = [item for item in starts if item.get("generation", 1) == generation]
        if admission_boundary:
            current = [
                item
                for item in current
                if self._diagnostic_reservation_in_scope(state, review, item)
                and self._diagnostic_check_pending(state, step_id)
            ]
            if starts and not current:
                raise OperationBlocked("diagnostic_check_deadline_unavailable")
        deadlines = (
            tuple(item.get("deadline_epoch") for item in current)
            if admission_boundary
            else ()
        )
        self._require_diagnostic_deadline(deadlines)
        conditional = [
            item
            for item in current
            if item.get("inferential_status") == "policy_conditional"
        ]
        admitted = [
            proof
            for proof in proofs
            if proof.get(
                "generation", proof["action_binding"]["envelope"]["generation"]
            )
            == generation
            and any(
                all(
                    proof[key] == item[key]
                    for key in (
                        "hold_id",
                        "binding_sha256",
                        "policy_sha256",
                        "input_sha256",
                    )
                )
                for item in conditional
            )
        ]
        if conditional and not admitted or proofs and not starts:
            raise OperationBlocked("diagnostic_action_binding_required")
        if not admitted:
            return current, None, deadlines
        accepted = admitted[-1]
        if any(proof != accepted for proof in admitted):
            raise OperationConflict(
                "Diagnostic action has conflicting current acceptance evidence"
            )
        try:
            verify_retained_diagnostic_action(accepted, envelope, token=token)
        except Exception as error:
            raise OperationBlocked(
                "diagnostic_action_qualification_invalidated"
            ) from error
        return current, accepted, deadlines

    def _prepare_diagnostic_admission(self, envelope, *, token=None):
        with self._read_connection() as db:
            requirements = self._diagnostic_requirements(
                db, self._state(db), envelope, token=token, admission_boundary=True
            )
        _, record, deadlines = requirements
        if record is None:
            return requirements, None
        if self.diagnostic_admission is None:
            raise OperationBlocked("current_diagnostic_action_authority_required")
        try:
            accepted, require_current = self.diagnostic_admission(
                None, envelope, deepcopy(record)
            )
        except (OperationBlocked, OperationConflict):
            raise
        except Exception as error:
            raise OperationBlocked(
                "diagnostic_action_qualification_invalidated"
            ) from error
        if accepted != record["action_acceptance"] or not callable(require_current):
            raise OperationBlocked("diagnostic_action_acceptance_changed")
        self._require_diagnostic_deadline(deadlines)
        return requirements, require_current

    def _guard(
        self,
        db,
        envelope,
        outputs=(),
        *,
        token=None,
        consume=False,
        acceptance=False,
        diagnostic_preflight=None,
    ):
        from .thesis_workflow import check_thesis_outputs

        outputs = tuple(outputs)
        admission_boundary = consume or acceptance or bool(outputs)
        state = self._state(db)
        try:
            current = self._envelope(
                state,
                envelope["step"]["step_id"],
                envelope["actor"],
                db=db,
                token=token,
                consume=consume,
                manual_local_oci=envelope.get("manual_local_oci") is True,
            )
        except OperationBlocked as error:
            raise OperationBlocked("adaptive_dispatch_basis_invalidated") from error
        if current != envelope:
            raise OperationBlocked("adaptive_dispatch_basis_invalidated")
        check_thesis_outputs(envelope, outputs)
        if any(item.version_id in state.artifacts for item in outputs):
            raise OperationConflict(
                "Cannot replace an immutable adaptive artifact version"
            )
        requirements = self._diagnostic_requirements(
            db, state, envelope, token=token, admission_boundary=admission_boundary
        )
        if admission_boundary:
            if diagnostic_preflight is None or diagnostic_preflight[0] != requirements:
                raise OperationBlocked("diagnostic_action_admission_changed")
            require_current = diagnostic_preflight[1]
            if require_current is not None:
                try:
                    require_current(db)
                except (OperationBlocked, OperationConflict):
                    raise
                except Exception as error:
                    raise OperationBlocked(
                        "diagnostic_action_qualification_invalidated"
                    ) from error
            self._require_diagnostic_deadline(requirements[2])

    def _registered_backend(self, executor):
        from devbench.engine.exploratory_oci import ExploratoryOCI
        from devbench.engine.tier_a_executor import TierAExecutor

        kind = {ExploratoryOCI: "oci", TierAExecutor: "tier_a"}.get(type(executor))
        if kind is not None and executor.ledger.path != self.ledger.path:
            raise PermissionError(
                "Recovery requires the workflow's exact executor ledger",
            )
        return kind

    def _unresolved_dispatches(self, executor, step_id=None):
        def read(db):
            state = self._state(db)
            result = []
            for attempt_id, dispatch in state.dispatches.items():
                token, envelope = dispatch["token"], dispatch["envelope"]
                current_step = envelope["step"]["step_id"]
                other_step = step_id is not None and current_step != step_id
                if token["backend_id"] != executor.backend_id or other_step:
                    continue
                attempt = self.authority._checked_attempt(db, token)
                settled = (
                    attempt_id in state.outcomes
                    or attempt["execution_state"] == "noncompletion_confirmed"
                )
                if not settled or attempt["cleanup_state"] != "confirmed":
                    result.append((token, envelope, attempt))
            return result

        return self._read_projection(read)

    def _reconcile_step(self, step_id, executor, kind):
        recovered = []
        for token, _, attempt in self._unresolved_dispatches(executor, step_id):
            previous = self._load_dispatch(token)[1]
            if kind == "tier_a" and attempt["execution_state"] == "reserved":
                # Tier A cannot start without the committed submission intent.
                proof = request_digest({"reserved_without_submission": attempt})
                result = self.authority.reconcile_attempt(
                    token,
                    job_id=None,
                    execution_state="noncompletion_confirmed",
                    cleanup_state="confirmed",
                    usage=attempt["usage"],
                    receipt_sha256=proof,
                    _require_reserved=True,
                    verify_observation=lambda _proof,
                    token=token,
                    attempt=attempt: self.authority.attempt(token) == attempt,
                )
            else:
                result = executor.recover(self, token)
            if result is not None and (
                result != previous or self.authority.attempt(token) != attempt
            ):
                recovered.append(result)
        return recovered

    def reconcile_pending(self, executor):
        """Attach verified receipts before the scheduler considers another launch."""
        kind = self._registered_backend(executor)
        if kind is None:
            return []
        steps = {
            envelope["step"]["step_id"]
            for _, envelope, _ in self._unresolved_dispatches(executor)
        }
        recovered = []
        for step_id in sorted(steps):
            try:
                lock_key = (self.lineage_id, "adaptive-step", step_id)
                with self.ledger._operation_lock(lock_key):
                    recovered.extend(self._reconcile_step(step_id, executor, kind))
            except OperationBlocked:
                # An active or unverified attempt stays pending and cannot launch.
                continue
        return recovered

    @staticmethod
    def _dispatch_key(envelope, code_sha256):
        return request_digest(
            {
                "step_id": envelope["step"]["step_id"],
                "code_digest": code_sha256,
                "inputs_sha256": request_digest(envelope["pins"]),
                "params_sha256": envelope["step"]["parameters_sha256"],
            }
        )

    def _check_dispatch_key(self, db, identity):
        from devbench.engine.tier_a_executor import TIER_A_INTERRUPTED

        state = self._state(db)
        matches = [
            row
            for row in state.dispatches.values()
            if row.get("idempotency_key") == identity
        ]
        if len(matches) >= 3:
            raise OperationBlocked("unchanged_infrastructure_retry_budget_exhausted")
        for row in matches:
            token = row["token"]
            attempt = self.authority._checked_attempt(db, token)
            if attempt["execution_state"] == "noncompletion_confirmed":
                continue
            outcome = state.outcomes.get(token["attempt_id"])
            if (
                outcome is not None
                and outcome["status"] == "failed"
                and outcome["reason"]
                in {
                    "exploratory_coordinator_interrupted",
                    TIER_A_INTERRUPTED,
                }
                and attempt["cleanup_state"] == "confirmed"
            ):
                continue
            raise OperationBlocked("adaptive_identical_step_already_dispatched")

    def dispatch(self, step_id, executor, *, actor, expected_revision=None):
        """Reconcile exact registered work, then reserve and fence a new submission."""
        if not _matches(_ID, actor):
            raise ValueError("Adaptive dispatch requires an exact actor identity")
        kind = self._registered_backend(executor)
        if kind is None:
            return self._dispatch_once(step_id, executor, actor, expected_revision)
        with self.ledger._operation_lock((self.lineage_id, "adaptive-step", step_id)):
            revision = self._read_projection(
                lambda db: self.authority._revision(db, self.lineage_id),
            )
            if expected_revision is not None and revision != expected_revision:
                raise OperationConflict("Controller evidence changed before dispatch")
            recovered = self._reconcile_step(step_id, executor, kind)
            if recovered:

                def current(db):
                    state = self._state(db)
                    return state.outcome(step_id) if step_id in state.nodes else None

                outcome = self._read_projection(current)
                if outcome is not None and outcome in recovered:
                    return {**outcome, "reconciled": True}
                if expected_revision is not None:
                    raise OperationConflict(
                        "Recovery changed the revision; refresh before a new dispatch",
                    )
                revision = self._read_projection(
                    lambda db: self.authority._revision(db, self.lineage_id),
                )
            return self._dispatch_once(step_id, executor, actor, revision)

    def _dispatch_snapshot(self, step_id, actor, expected_revision):
        with self.ledger._database() as db:
            db.execute("BEGIN")
            state = self._state(db)
            if step_id not in state.nodes:
                raise ValueError("Unknown adaptive step")
            status = self._status(db, state)
            if (
                expected_revision is not None
                and status["revision"] != expected_revision
            ):
                raise OperationConflict("Controller evidence changed before dispatch")
            if step_id not in status["ready"]:
                raise OperationBlocked(
                    status["blocked"].get(step_id, "adaptive_step_not_ready")
                )
            envelope = self._envelope(state, step_id, actor, db=db)
            execution = self.authority._load_state(db, self.lineage_id)
            return envelope, execution, status["revision"]

    def _dispatch_code(self, executor, envelope):
        from devbench.engine.exploratory_oci import ExploratoryOCI
        from devbench.engine.tier_a_executor import TierAExecutor

        from .thesis_workflow import check_thesis_backend

        step = envelope["step"]
        oci_version = step.get("oci_method_version_id")
        if oci_version is not None:
            if _requires_tier_a(step):
                raise OperationBlocked("adaptive_tier_a_requires_registered_operation")
            if (
                type(executor) is not ExploratoryOCI
                or executor.ledger.path != self.ledger.path
            ):
                raise PermissionError(
                    "An exploratory OCI step requires the bound local OCI adapter",
                )
            if step["held_out"] and (
                type(self.partition_check) is not PartitionAuthority
                or executor.partition is not self.partition_check
            ):
                raise PermissionError(
                    "Evaluator and scheduler partition authorities differ"
                )
            validated = executor.validate(envelope)
            contract = None if validated is None else validated[1]["contract"]
            check_thesis_backend(
                envelope,
                executor.backend_id,
                validated_contract=contract,
            )
            return (
                oci_version
                if validated is None
                else contract["implementation"]["source_sha256"]
            )
        if isinstance(executor, ExploratoryOCI):
            raise OperationBlocked("adaptive_oci_requires_bound_method")
        if _requires_tier_a(step):
            if (
                type(executor) is not TierAExecutor
                or executor.ledger.path != self.ledger.path
            ):
                raise PermissionError(
                    "A registered research step requires its bound Tier A executor",
                )
            validated = executor.validate(envelope)
            if "thesis_scope" in envelope:
                check_thesis_backend(
                    envelope,
                    executor.backend_id,
                    validated_specification=None if validated is None else validated[1],
                )
            return executor.code_sha256
        if type(executor) is TierAExecutor:
            raise OperationBlocked("adaptive_tier_a_requires_admitted_research")
        check_thesis_backend(envelope, executor.backend_id)
        return None

    def _dispatch_once(self, step_id, executor, actor, expected_revision):
        backend_id = getattr(executor, "backend_id", None)
        if not _matches(_ID, actor) or not _matches(_ID, backend_id):
            raise ValueError(
                "Adaptive dispatch requires actor and registered backend identities",
            )
        if not all(
            callable(getattr(executor, method, None)) for method in ("run", "verify")
        ):
            raise ValueError(
                "Adaptive backend requires execution and receipt verification",
            )
        envelope, execution, revision = self._dispatch_snapshot(
            step_id,
            actor,
            expected_revision,
        )
        oci = envelope["step"].get("oci_method_version_id") is not None
        code_sha256 = self._dispatch_code(executor, envelope)
        diagnostic_preflight = self._prepare_diagnostic_admission(envelope)
        tier_a = _requires_tier_a(envelope["step"])
        generation = envelope["generation"]
        unit = "adaptive-" + request_digest([step_id, generation])
        identity = (
            self._dispatch_key(envelope, code_sha256)
            if code_sha256 is not None
            else None
        )

        def retained(db, token):
            record = {"token": token, "envelope": envelope}
            if tier_a:
                record["worker_custody_format"] = executor.worker_custody_format
            if identity is not None:
                self._check_dispatch_key(db, identity)
                record["idempotency_key"] = identity
            self._event(db, "dispatch", record)

        token = self.authority.reserve_attempt(
            self.lineage_id,
            publication_unit=unit,
            request_sha256=request_digest(envelope),
            environment_sha256=envelope["environment_sha256"],
            backend_id=executor.backend_id,
            expected_epoch=execution["epoch"],
            binding_id=execution["binding_id"],
            expected_revision=revision,
            _adaptive_reservation=retained,
        )
        receipt = None

        def submit(_token):
            nonlocal receipt
            receipt = StepReceipt.model_validate(
                executor.run(deepcopy(envelope), token=_token)
                if oci
                else executor.run(deepcopy(envelope))
            )
            return receipt.job_id

        def guard(db):
            self._guard(
                db,
                envelope,
                token=token,
                consume=True,
                diagnostic_preflight=diagnostic_preflight,
            )
            if tier_a:
                executor._record_custody(db, token, "prepared")

        self.authority.dispatch_attempt(token, submit, _adaptive_guard=guard)
        return self.complete(token, receipt, verify=executor.verify)

    def _load_dispatch(self, token):
        if token.get("lineage_id") != self.lineage_id:
            raise OperationConflict("Adaptive attempt belongs to another lineage")
        with self.ledger._database() as db:
            db.execute("BEGIN")
            state = self._state(db)
            retained = state.dispatches.get(token.get("attempt_id"))
            if retained is None or retained["token"] != token:
                raise OperationConflict("Unknown adaptive dispatch token")
            return retained["envelope"], state.outcomes.get(token["attempt_id"])

    def _require_receipt_verifier(self, token, envelope, receipt, verify):
        if envelope["step"].get("oci_method_version_id") is not None:
            from devbench.engine.exploratory_oci import ExploratoryOCI

            expected = ExploratoryOCI
            reason = "adaptive_oci_receipt_requires_bound_verifier"
        elif _requires_tier_a(envelope["step"]):
            from devbench.engine.tier_a_executor import TierAExecutor

            expected = TierAExecutor
            reason = "adaptive_tier_a_receipt_requires_bound_verifier"
        else:
            return
        backend = getattr(verify, "__self__", None)
        if (
            type(backend) is not expected
            or backend.ledger.path != self.ledger.path
            or getattr(verify, "__func__", None) is not expected.verify
            or receipt.job_id != token["attempt_id"]
            or receipt.io_sha256 is None
        ):
            raise OperationBlocked(reason)

    def complete(self, token, receipt, *, verify):
        """Reconcile a verified receipt against the exact persisted dispatch."""
        receipt = StepReceipt.model_validate(receipt)
        envelope, previous = self._load_dispatch(token)
        self._require_receipt_verifier(token, envelope, receipt, verify)
        receipt_document = receipt.model_dump(
            mode="json",
            exclude={"cleanup_state", "io_sha256"}
            if receipt.cleanup_state == "unknown" and receipt.io_sha256 is None
            else None,
        )
        receipt_sha = request_digest(receipt_document)
        if previous is not None:
            if previous["receipt_sha256"] != receipt_sha:
                raise OperationConflict(
                    "Adaptive outcome already has different receipt evidence"
                )
            return previous
        if not callable(verify) or verify(deepcopy(envelope), receipt) is not True:
            raise OperationBlocked("adaptive_consumer_receipt_unverified")
        actual = sorted(
            (pin.model_dump(mode="json") for pin in receipt.consumed),
            key=lambda pin: pin["port"],
        )
        pin_mismatch = (
            receipt.request_sha256 != token["request_sha256"]
            or actual != envelope["pins"]
        )
        step = AdaptiveStep.model_validate(envelope["step"])
        expected = {port.name: port for port in step.outputs}
        bad_output = receipt.status == "succeeded" and (
            set(receipt.outputs) - set(expected)
            or any(
                port.required and name not in receipt.outputs
                for name, port in expected.items()
            )
            or any(
                item.kind != expected[name].artifact_kind
                for name, item in receipt.outputs.items()
                if name in expected
            )
        )
        self.authority.reconcile_attempt(
            token,
            job_id=receipt.job_id,
            execution_state=receipt.status,
            cleanup_state=receipt.cleanup_state,
            usage={"wall_millis": receipt.wall_millis},
            receipt_sha256=receipt_sha,
            verify_observation=lambda _: True,
        )
        outcome = {
            "attempt_id": token["attempt_id"],
            "status": receipt.status,
            "reason": receipt.reason,
            "receipt_sha256": receipt_sha,
            "outputs": receipt_document["outputs"],
            "consumed": receipt_document["consumed"],
            "accepted_record_id": None,
        }
        if pin_mismatch or bad_output or receipt.status == "failed":
            outcome.update(
                status="failed",
                staged_outputs=outcome["outputs"],
                outputs={},
                reason=(
                    "pinned_input_mismatch"
                    if pin_mismatch
                    else "output_port_mismatch"
                    if bad_output
                    else receipt.reason
                ),
            )
            self._record_failed(token, outcome)
            if pin_mismatch:
                raise ValueError(
                    "Consumer receipt does not match pinned artifact versions or graph "
                    "digests"
                )
            if bad_output:
                raise ValueError(
                    "Consumer receipt does not fulfill declared output ports"
                )
            return outcome
        return self._accept_completed_output(token, envelope, receipt, outcome)

    def _accept_completed_output(self, token, envelope, receipt, outcome):
        staged = self.authority.stage_output(
            token,
            artifact_sha256=request_digest(outcome["outputs"]),
            verification_sha256=outcome["receipt_sha256"],
            verify_output=lambda _: True,
        )

        preflight_error, diagnostic_preflight = None, None
        if staged["acceptance_state"] == "staged":
            try:
                diagnostic_preflight = self._prepare_diagnostic_admission(
                    envelope, token=token
                )
            except (OperationBlocked, OperationConflict) as error:
                preflight_error = error

        def guard(db):
            if preflight_error is not None:
                raise preflight_error
            self._guard(
                db,
                envelope,
                receipt.outputs.values(),
                token=token,
                acceptance=True,
                diagnostic_preflight=diagnostic_preflight,
            )

        def accepted(db, record_id):
            outcome["accepted_record_id"] = record_id
            self._event(db, "outcome", outcome)

        result = self.authority.accept_output(
            token, _adaptive_guard=guard, _adaptive_accept=accepted
        )
        if result["acceptance_state"] == "accepted":
            return self._load_dispatch(token)[1]
        outcome.update(
            status="audit_only",
            staged_outputs=outcome["outputs"],
            outputs={},
            reason=result["rejection_reason"],
        )
        self._record_failed(token, outcome)
        return outcome

    def _record_failed(self, token, outcome):
        with self.authority._execution_tx(self.lineage_id) as db:
            state = self._state(db)
            existing = state.outcomes.get(token["attempt_id"])
            if existing is None:
                self._event(db, "outcome", outcome)
                self._retain_rank_sensitivity_hold(db, state, token, outcome)
            elif existing != outcome:
                raise OperationConflict(
                    "Adaptive attempt already has a different outcome"
                )

    def _retain_rank_sensitivity_hold(self, db, state, token, outcome):
        from devbench.analysis.rna_diagnostic_policy import RANK_SENSITIVITY_HOLD
        from devbench.engine.tier_a_executor import TierAExecutor

        if (
            outcome.get("reason") != RANK_SENSITIVITY_HOLD
            or token["backend_id"] != TierAExecutor.backend_id
        ):
            return
        envelope = state.dispatches[token["attempt_id"]]["envelope"]
        step = envelope["step"]
        identity = step["step_id"]
        if (
            step["purpose"] == RESEARCH_INFERENCE
            and step["primary"]
            and state.generations.get(identity) == envelope["generation"]
            and step["branch_id"] not in state.retired
            and identity not in state.holds
        ):
            self._event(
                db,
                "hold",
                {
                    "step_id": identity,
                    "reason": RANK_SENSITIVITY_HOLD,
                    "evidence_sha256": outcome["receipt_sha256"],
                },
            )

    def _current_use(self, db, step_id, port, use, generation):
        from devbench.core.joint_bh import JOINT_BH_INPUT
        from rna_kernels.scrna_upstream_contract import NB_SEALED_REPRESENTATIONS

        state = self._state(db)
        if step_id not in state.nodes:
            raise ValueError("Unknown adaptive step")
        current = state.generations[step_id]
        if generation is not None and generation != current:
            raise OperationBlocked("adaptive_use_invalidated")
        if state.nodes[step_id].branch_id in state.retired or self._held_ancestry(
            state, step_id
        ):
            raise OperationBlocked("adaptive_use_held_or_retired")
        if use == "claim" and (step_id, current) in state.claim_stale:
            raise OperationBlocked("adaptive_claim_use_invalidated")
        result = state.outcome(step_id)
        artifact = (
            result["outputs"].get(port)
            if result and result["status"] == "succeeded"
            else None
        )
        if (
            artifact is None
            or artifact["version_id"] in state.stale_versions
            or artifact["representation"] in NB_SEALED_REPRESENTATIONS
        ):
            raise OperationBlocked("adaptive_current_use_unavailable")
        self._check_derived_delivery(db, state, artifact["version_id"])
        if artifact["representation"] == JOINT_BH_INPUT:
            from .thesis_joint_bh_release import current_joint_bh_release

            release, aim_id = current_joint_bh_release(
                self, db, state, result, claim=use == "claim"
            )
            return release.outputs[aim_id]
        step = state.nodes[step_id]
        if use == "claim" and step.purpose == RESEARCH_INFERENCE:
            envelope = state.dispatches[result["attempt_id"]]["envelope"]
            retained = envelope.get("design_review")
            if self._review_basis(db, step) != retained:
                raise OperationBlocked("adaptive_research_claim_admission_changed")
        if embargoed(db, artifact["sha256"]):
            raise OperationBlocked("adaptive_protected_partition_delivery_denied")
        return ArtifactVersion.model_validate(artifact)

    def current_use(self, step_id, port, *, purpose, use="compute", generation=None):
        if purpose not in {
            "cache",
            "query",
            "export",
            "replay-current-use",
        } or use not in {"compute", "claim"}:
            raise ValueError("Unknown adaptive artifact use")
        with self.ledger._database() as db:
            db.execute("BEGIN")
            return self._current_use(db, step_id, port, use, generation)

    def disclosure_provenance(self, step_id, port, *, connection=None):
        """Exact accepted artifact ancestry; these identities grant no disclosure."""
        from devbench.core.joint_bh import JOINT_BH_OUTPUT_PREFIX

        with self._read_connection(connection) as db:
            artifact = self._current_use(db, step_id, port, "compute", None)
            state = self._state(db)
            pending, records, dependencies = [artifact.version_id], {}, set()
            if artifact.version_id.startswith(JOINT_BH_OUTPUT_PREFIX):
                from .thesis_joint_bh_release import current_joint_bh_release

                release, _ = current_joint_bh_release(
                    self, db, state, state.outcome(step_id), claim=False
                )
                pending = [
                    state.outcomes[item.attempt_id]["outputs"][port]["version_id"]
                    for item in release.sources
                ]
                basis = [release.plan_sha256, release.receipt_sha256()]
                records[artifact.version_id] = {
                    "artifact": artifact.model_dump(mode="json"),
                    "parents": pending.copy(),
                    "basis": basis,
                }
                dependencies.update((artifact.sha256, *basis))
            while pending:
                version = pending.pop()
                if version in records:
                    continue
                if len(records) >= 512:
                    raise OperationBlocked("adaptive_disclosure_ancestry_exceeds_bound")
                value, producer = state.artifacts[version]
                parents, basis = [], []
                dependencies.add(value["sha256"])
                if producer is not None:
                    outcome = state.latest[producer]
                    envelope = state.dispatches[outcome["attempt_id"]]["envelope"]
                    parents = [
                        item["version_id"] for item in envelope["inputs"].values()
                    ]
                    basis = [
                        item["sha256"]
                        for item in envelope["basis"].values()
                        if item["kind"] not in {"code", "environment"}
                    ]
                    dependencies.update(
                        item["sha256"] for item in envelope["basis"].values()
                    )
                    dependencies.update(
                        (
                            envelope["environment_sha256"],
                            envelope["step"]["parameters_sha256"],
                        )
                    )
                else:
                    parents = list(state.derived_parents.get(version, ()))
                    basis = [
                        state.basis[key]["sha256"]
                        for key in state.sources.get(version, ())
                    ]
                dependencies.update(basis)
                records[version] = {
                    "artifact": deepcopy(value),
                    "parents": parents,
                    "basis": basis,
                }
                pending.extend(parents)
            return {
                "version_id": artifact.version_id,
                "artifacts": records,
                "dependencies": sorted(dependencies),
            }

    def history(self, step_id):
        """Trusted audit read of historical outcomes, including invalidated versions."""
        with self.ledger._database() as db:
            db.execute("BEGIN")
            state = self._state(db)
            return tuple(
                outcome
                for attempt, outcome in state.outcomes.items()
                if state.dispatches[attempt]["envelope"]["step"]["step_id"] == step_id
            )

    def current_generation_outcome(self, step_id, generation, *, expected_revision):
        """Read the selected generation at the exact controller snapshot."""
        with self._read_connection(None) as db:
            state = self._state(db)
            status = self._status(db, state)
            if (
                status["revision"] != expected_revision
                or generation is None
                or state.generations.get(step_id) != generation
            ):
                raise OperationConflict("Controller evidence changed before dispatch")
            return state.outcome(step_id) if step_id in status["outcomes"] else None

    def _projected_dependencies(self, state, node):
        dependencies = set()
        for link in node.inputs:
            if link.source_port is not None:
                dependencies.add(link.source_id)
            elif link.source_id in state.derived_parents:
                dependencies.update(self._artifact_producers(state, link.source_id))
        return sorted(dependencies)

    @staticmethod
    def _projected_input_ports(state, node):
        """Identify structural bindings without opening inputs or parameter bytes."""
        ports = []
        for link in node.inputs:
            if link.source_port is None:
                artifact, _ = state.artifacts.get(link.source_id, (None, None))
            else:
                current = state.outcome(link.source_id) or {}
                artifact = current.get("outputs", {}).get(link.source_port)
            row = {
                "port": link.port.name[:96],
                "kind": link.port.artifact_kind,
                "source_id": link.source_id,
                "source_port": link.source_port,
            }
            if artifact and artifact["version_id"] not in state.stale_versions:
                row["representation"] = artifact["representation"]
            ports.append(row)
        return ports

    def projection_snapshot(self, *, connection=None):
        """Collect display metadata and tombstones without returning artifact bytes."""

        def read(db):
            state = self._state(db)
            status = self._status(db, state)
            rows = []
            for step_id, node in sorted(state.nodes.items()):
                current = state.outcome(step_id)
                retired = state.retired.get(node.branch_id)
                rows.append(
                    {
                        "step_id": step_id,
                        "branch_id": node.branch_id,
                        "generation": state.generations[step_id],
                        "operation_id": node.operation_id,
                        "parameters_sha256": node.parameters_sha256,
                        "input_ports": self._projected_input_ports(state, node),
                        "purpose": node.purpose,
                        "role": node.role,
                        "status": "retired"
                        if retired
                        else "blocked"
                        if step_id in status["blocked"]
                        else status["outcomes"].get(step_id, "ready"),
                        "reason": (
                            retired["reason"]
                            if retired
                            else status["blocked"].get(step_id)
                            or (current or {}).get("reason")
                        ),
                        "claim_hold": state.claim_stale.get(
                            (step_id, state.generations[step_id])
                        ),
                        "dependencies": self._projected_dependencies(state, node),
                    }
                )
            by_id = {row["step_id"]: row for row in rows}
            sweeps = [
                {
                    "sweep_id": sweep,
                    "step_id": step,
                    "status": "omitted"
                    if reason is not None
                    else by_id[step]["status"],
                    "reason": reason,
                }
                for sweep, members in sorted(state.sweeps.items())
                for step, reason in sorted(members.items())
            ]
            tombstones = [
                {"step_id": step, "generation": generation, "reason": reason}
                for (step, generation), reason in sorted(state.tombstones.items())
            ]
            historical = [
                {
                    "step_id": state.dispatches[attempt]["envelope"]["step"]["step_id"],
                    "branch_id": state.dispatches[attempt]["envelope"]["step"][
                        "branch_id"
                    ],
                    "generation": state.dispatches[attempt]["envelope"]["generation"],
                    "status": outcome["status"],
                    "reason": outcome["reason"],
                    "receipt_sha256": outcome["receipt_sha256"],
                }
                for attempt, outcome in state.outcomes.items()
            ]
            snapshot = {
                "mode": ADAPTIVE_MODE,
                "steps": rows,
                "sweep_members": sweeps,
                "tombstones": tombstones,
                "attempt_history": historical,
                "execution_block": status["execution_block"],
            }
            if state.controller["binding"] is not None:
                snapshot["controller_outcome"] = deepcopy(state.controller["outcome"])
            return snapshot

        return self._read_projection(read, connection=connection)

    def _authorized_current(self, step_id, port, delivery, use):
        from devbench.analysis.evidence_access import EvidenceDelivery

        if (
            type(delivery) is not EvidenceDelivery
            or delivery.broker.authority.ledger.path != self.ledger.path
        ):
            raise PermissionError(
                "Adaptive evidence delivery requires the same scientific authority"
            )
        artifact = self.current_use(step_id, port, purpose="query", use=use)

        def guard(db):
            if self._current_use(db, step_id, port, use, None) != artifact:
                raise OperationBlocked("adaptive_use_invalidated_during_delivery")

        return artifact, guard

    @staticmethod
    def _load_current_bytes(artifact, load):
        raw = load(artifact)
        if (
            type(raw) is not bytes
            or len(raw) > 16_000_000
            or hashlib.sha256(raw).hexdigest() != artifact.sha256
        ):
            raise ValueError(
                "Adaptive artifact bytes do not match their accepted version",
            )
        return raw

    def _read_current(self, step_id, port, load, delivery, use):
        artifact, guard = self._authorized_current(step_id, port, delivery, use)
        return self._load_current_bytes(artifact, load), guard

    def query_current(
        self,
        step_id,
        port,
        query,
        *,
        load,
        evidence_delivery,
        bindings=None,
        use="compute",
        open_artifact=None,
    ):
        from devbench.analysis.episode_evidence_queries import query_result
        from devbench.study.evidence import decode_json

        artifact, guard = self._authorized_current(
            step_id,
            port,
            evidence_delivery,
            use,
        )
        if artifact.representation == "parquet":
            from devbench.analysis.episode_evidence_queries import query_parquet_result

            if not callable(open_artifact):
                raise PermissionError(
                    "Tier A readback requires the bound Parquet artifact reader",
                )
            return query_parquet_result(
                artifact,
                query,
                open_artifact=open_artifact,
                evidence_delivery=evidence_delivery,
                bindings=bindings,
                _use_guard=guard,
            )
        raw = self._load_current_bytes(artifact, load)
        return query_result(
            decode_json(raw),
            query,
            bindings=bindings,
            evidence_delivery=evidence_delivery,
            _use_guard=guard,
        )

    def export_current(self, step_id, port, *, load, evidence_delivery, use="compute"):
        raw, guard = self._read_current(step_id, port, load, evidence_delivery, use)
        broker = evidence_delivery.broker
        reservation = broker.reserve(
            evidence_delivery.context_id, evidence_delivery.source_id, raw, _guard=guard
        )
        broker.mark_attempted(reservation["reservation_id"], _guard=guard)
        return raw
