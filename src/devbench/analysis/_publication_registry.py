"""Coordinator-owned publication registration, projection and release."""

from __future__ import annotations

import json
import re
from hashlib import sha256
from pathlib import Path

from devbench.core.procedure_profile import ProcedureProfile
from devbench.core.publication_families import FamilyAllocation
from devbench.engine.provenance import canonical_json

from ._publication_locations import _known_adaptive_identity
from ._publication_policy import (
    CheckedObservation,
    PublicationAccess,
    PublicationSubject,
    _digest,
    _label,
    _same_transaction,
    access_from_delivery,
    build_publication_descriptor,
    is_statistic_field,
)
from .scientific_review_holds import ReviewBinding


class PublicationRegistry:
    """Read and append publication subjects in the existing scientific ledger.

    This coordinator API is deliberately separate from HTTP mutation arguments.
    Independent adaptive qualification remains unavailable; admitted adaptive
    projections carry descriptive or checked evidence only.
    """

    def __init__(self, workflow, lifecycle=None):
        from devbench.platform.adaptive_workflow import AdaptiveWorkflow

        from .scientific_review_holds import ReviewHoldLifecycle

        if type(workflow) is not AdaptiveWorkflow:
            raise TypeError("Publication requires the authoritative adaptive workflow")
        lifecycle = lifecycle or ReviewHoldLifecycle(
            workflow.authority, workflow.lineage_id
        )
        if (
            type(lifecycle) is not ReviewHoldLifecycle
            or lifecycle.ledger.path != workflow.ledger.path
            or lifecycle.lineage_id != workflow.lineage_id
        ):
            raise PermissionError(
                "Publication and review require the same scientific lineage"
            )
        self.workflow, self.lifecycle = workflow, lifecycle
        self.ledger, self.authority = workflow.ledger, workflow.authority

    def _state(self, db):
        from devbench.engine.scientific_authority import _row

        rows = db.execute(
            "SELECT * FROM scientific_records WHERE lineage_id=? "
            "AND kind='execution_event' "
            "AND record_id GLOB 'publication-subject-*' ORDER BY revision LIMIT 4097",
            (self.workflow.lineage_id,),
        ).fetchall()
        if len(rows) > 4096:
            raise PermissionError("publication_subject_inventory_exhausted")
        return self.replay_subjects(
            [_row(row)["record"] for row in rows], self.workflow.lineage_id
        )

    @staticmethod
    def replay_subjects(records, lineage_id):
        """Resolve captured subject history without granting current publication."""
        if len(records) > 4096:
            raise PermissionError("publication_subject_inventory_exhausted")
        subjects, heads = {}, {}
        for record in records:
            if record["event"] != "publication_subject":
                raise PermissionError("publication_subject_integrity_mismatch")
            subject = PublicationSubject.model_validate_json(
                canonical_json(record["state"])
            )
            body = subject.model_dump(mode="json", exclude={"subject_id"})
            previous = heads.get(subject.logical_key)
            if (
                subject.subject_id != _digest(body)
                or subject.lineage_id != lineage_id
                or subject.prior_subject_id != previous
                or subject.revision
                != (subjects[previous].revision + 1 if previous else 1)
            ):
                raise PermissionError("publication_subject_integrity_mismatch")
            subjects[subject.subject_id] = subject
            heads[subject.logical_key] = subject.subject_id
        return subjects, heads

    def _accepted(self, db, step_id, port, generation):
        from devbench.engine.scientific_authority import _lookup
        from devbench.engine.workflow_operations import OperationBlocked

        try:
            artifact = self.workflow._current_use(
                db, step_id, port, "compute", generation
            )
        except (OperationBlocked, KeyError) as error:
            raise PermissionError("publication_source_withheld") from error
        state = self.workflow._state(db)
        outcome = state.outcome(step_id)
        dispatch = state.dispatches[outcome["attempt_id"]]
        envelope, token = dispatch["envelope"], dispatch["token"]
        accepted = _lookup(db, self.workflow.lineage_id, outcome["accepted_record_id"])
        binding = _lookup(db, self.workflow.lineage_id, token["binding_id"])
        latest_binding = db.execute(
            "SELECT record_id FROM scientific_records WHERE lineage_id=? "
            "AND kind='binding' "
            "ORDER BY revision DESC LIMIT 1",
            (self.workflow.lineage_id,),
        ).fetchone()
        if (
            accepted is None
            or binding is None
            or accepted["record"]["kind"] != "output"
            or binding["record"]["kind"] != "binding"
            or accepted["record"].get("attempt_id") != outcome["attempt_id"]
            or accepted["record"]["binding_id"] != token["binding_id"]
            or accepted["record"]["publication_unit"] != token["publication_unit"]
            or accepted["record"]["artifact_sha256"] != _digest(outcome["outputs"])
            or accepted["record"]["verification_sha256"] != outcome["receipt_sha256"]
            or envelope["generation"] != generation
            or latest_binding is None
            or latest_binding[0] != token["binding_id"]
        ):
            raise PermissionError("publication_accepted_output_mismatch")
        return artifact, outcome, envelope, accepted, binding

    def _read(self, path, expected=None):
        from devbench.study.evidence import _read

        path = Path(path)
        if (
            not path.is_absolute()
            or path.resolve() != path
            or path.is_symlink()
            or not path.is_relative_to(self.ledger.path.parent.resolve())
        ):
            raise PermissionError(
                "Publication source requires protected workspace retention"
            )
        raw = _read(path, 16_000_000)
        if expected is not None and sha256(raw).hexdigest() != expected:
            raise PermissionError("publication_source_withheld")
        return raw

    @staticmethod
    def _guard_companion(db, identity, delivery=None):
        from devbench.engine.partition_authority import embargoed

        if embargoed(db, identity):
            raise PermissionError("publication_source_withheld")
        if delivery is not None:
            broker = delivery.broker
            context, policy = broker._context_policy(db, delivery.context_id)
            for peer in broker._source_peers(db, identity):
                broker._check(
                    context, policy, broker._source(db, peer["source_id"]), set()
                )

    @staticmethod
    def _check_registration_bindings(
        binding,
        envelope,
        profile,
        allocation,
        exposure,
        publication_binding,
        design_binding,
    ):
        scientific = binding["record"]
        if (
            scientific["procedure_sha256"] != _digest(profile.model_dump(mode="json"))
            or scientific["family_allocation_sha256"]
            != (allocation.sha256() if allocation else _digest(None))
            or scientific["exposure_basis_sha256"] != exposure.exposure_sha256
            or design_binding.exposure_sha256 != scientific["exposure_basis_sha256"]
            or allocation is not None
            and allocation.sha256()
            not in {item["sha256"] for item in envelope["basis"].values()}
        ):
            raise PermissionError(
                "Publication procedure, allocation or frozen exposure binding differs"
            )
        for review_binding in (publication_binding, design_binding):
            if any(
                getattr(review_binding, key) != scientific[other]
                for key, other in (
                    ("facts_sha256", "cohort_sha256"),
                    ("design_sha256", "design_sha256"),
                    ("procedure_sha256", "procedure_sha256"),
                    ("policy_sha256", "policy_sha256"),
                )
            ):
                raise PermissionError(
                    "Publication review differs from its accepted scientific binding"
                )
            if review_binding.inputs_sha256 not in {
                _digest(envelope["pins"]),
                *(item["sha256"] for item in envelope["basis"].values()),
            }:
                raise PermissionError(
                    "Publication review inputs differ from the accepted "
                    "execution basis"
                )

    @staticmethod
    def _registration_families(review, entity_id, step_id, result, allocation):
        entity = review["entities"].get(entity_id)
        if entity is None or entity["kind"] not in {"table", "claim"}:
            raise ValueError("Publication requires a registered table or claim entity")
        ancestors, pending = set(), [entity_id]
        while pending:
            key = pending.pop()
            if key in ancestors:
                continue
            ancestors.add(key)
            pending.extend(review["entities"][key]["dependencies"])
        if not any(
            item["kind"] == "fit" and (item["workflow_step_id"] or key) == step_id
            for key in ancestors
            for item in (review["entities"][key],)
        ):
            raise PermissionError(
                "Publication review entity does not depend on the producer"
            )
        families = {
            review["entities"][key]["family_id"]
            for key in ancestors
            if review["entities"][key]["kind"] == "table"
        }
        if result is not None and result["family_id"] not in families:
            raise PermissionError("Publication result differs from the review family")
        if allocation is not None and not families <= {
            item.family_id for item in allocation.families
        }:
            raise PermissionError(
                "Publication allocation differs from the review family"
            )
        if (
            result is not None
            and allocation is not None
            and set(allocation.family(result["family_id"]).feature_ids)
            != {row["gene_id"] for row in result["rows"]}
        ):
            raise PermissionError(
                "Publication source differs from its complete allocated "
                "feature inventory"
            )
        return entity, families

    @staticmethod
    def _registration_claim(
        entity, entity_id, packet, publication_binding, families, review, source_sha
    ):
        claim = None
        if entity["kind"] == "claim":
            if (
                packet is None
                or packet.binding != publication_binding
                or packet.touchpoint != "publication"
            ):
                raise PermissionError(
                    "Claim publication requires its exact retained review packet"
                )
            claims = [item for item in packet.claims if item.claim_id == entity_id]
            if len(claims) != 1 or claims[0].family_id not in families:
                raise PermissionError(
                    "Claim publication requires its exact family and claim revision"
                )
            claim = claims[0].model_dump(mode="json")
            if not any(
                identity in review["evidence"]
                and review["evidence"][identity]["sha256"] == source_sha
                for identity in claim["evidence_ids"]
            ):
                raise PermissionError(
                    "Claim evidence does not include the accepted output"
                )
        elif packet is not None:
            raise ValueError(
                "Table publication does not acquire claim authority from a packet"
            )
        return claim

    def _registration_predecessor(
        self,
        db,
        entity,
        entity_id,
        prior_subject_id,
        publication_binding,
        source_sha,
        claim,
    ):
        subjects, heads = self._state(db)
        logical = entity["kind"] + ":" + entity_id
        previous = heads.get(logical)
        if previous != prior_subject_id:
            raise PermissionError(
                "Publication supersession requires its current prior subject"
            )
        history = [item for item in subjects.values() if item.logical_key == logical]
        if any(
            item.publication_binding == publication_binding
            and item.source_sha256 == source_sha
            for item in history
        ):
            raise PermissionError("A superseded publication binding cannot be revived")
        if claim is not None and any(
            item.claim is not None and item.claim["revision"] >= claim["revision"]
            for item in history
        ):
            raise PermissionError(
                "Claim supersession requires a new exact claim revision"
            )
        return logical, previous, history

    def _require_publication_recipient(self, db, delivery):
        from .evidence_access import EvidenceDelivery

        if (
            type(delivery) is not EvidenceDelivery
            or delivery.broker.authority.ledger.path != self.ledger.path
        ):
            raise PermissionError("Publication requires same-ledger recipient delivery")
        context, _ = delivery.broker._context_policy(db, delivery.context_id)
        if context["lineage_id"] != self.workflow.lineage_id:
            raise PermissionError("Publication recipient belongs to another lineage")

    def register(
        self,
        entity_id,
        *,
        step_id,
        port,
        generation,
        source_path,
        publication_binding,
        design_binding,
        frozen_exposure,
        allocation=None,
        packet_path=None,
        prior_subject_id=None,
        check_id=None,
    ):
        "Validate an exact pending join before appending; never accept current flags."

        from .scientific_publication import _retained_table
        from .scientific_review import ActiveReviewPacket

        publication_binding = ReviewBinding.model_validate(publication_binding)
        design_binding = ReviewBinding.model_validate(design_binding)
        allocation = (
            FamilyAllocation.model_validate(allocation)
            if allocation is not None
            else None
        )
        if (
            type(frozen_exposure) is not dict
            or "classification" not in frozen_exposure
            or len(canonical_json(frozen_exposure)) > 128_000
        ):
            raise ValueError(
                "Frozen decision exposure requires its bounded exact "
                "classification document"
            )
        exposure = PublicationAccess(
            exposure=frozen_exposure["classification"],
            exposure_sha256=_digest(frozen_exposure),
        )
        raw = self._read(source_path)
        source_sha = sha256(raw).hexdigest()
        value = json.loads(raw)
        if check_id is not None:
            CheckedObservation.compute(check_id, value)
        packet = (
            ActiveReviewPacket.model_validate_json(self._read(packet_path))
            if packet_path is not None
            else None
        )
        with self.authority._execution_tx(self.workflow.lineage_id) as db:
            artifact, outcome, envelope, accepted, binding = self._accepted(
                db, step_id, port, generation
            )
            profile = ProcedureProfile.model_validate_json(
                canonical_json(envelope["step"]["profile"])
            )
            if artifact.sha256 != source_sha:
                raise PermissionError(
                    "Publication source differs from the accepted output"
                )
            result = (
                _retained_table(
                    raw,
                    source_path=source_path,
                    guard_companion=lambda identity: self._guard_companion(
                        db, identity
                    ),
                )
                if check_id is None
                else None
            )
            self._check_registration_bindings(
                binding,
                envelope,
                profile,
                allocation,
                exposure,
                publication_binding,
                design_binding,
            )
            review = self.lifecycle.state(connection=db)
            entity, families = self._registration_families(
                review,
                entity_id,
                step_id,
                result,
                allocation,
            )
            claim = self._registration_claim(
                entity,
                entity_id,
                packet,
                publication_binding,
                families,
                review,
                source_sha,
            )
            logical, previous, history = self._registration_predecessor(
                db,
                entity,
                entity_id,
                prior_subject_id,
                publication_binding,
                source_sha,
                claim,
            )
            body = dict(
                format="devbench.publication-subject/1",
                lineage_id=self.workflow.lineage_id,
                entity_id=entity_id,
                entity_kind=entity["kind"],
                logical_key=logical,
                revision=len(history) + 1,
                prior_subject_id=previous,
                step_id=step_id,
                generation=generation,
                port=port,
                artifact_version_id=artifact.version_id,
                source_sha256=source_sha,
                source_path=str(source_path),
                source_schema=result["schema"] if result else "checked_observation",
                attempt_id=outcome["attempt_id"],
                accepted_record_id=accepted["record"]["record_id"],
                accepted_record_sha256=accepted["sha256"],
                binding_id=binding["record"]["record_id"],
                scientific_binding_sha256=binding["sha256"],
                envelope_sha256=_digest(envelope),
                profile=profile.model_dump(mode="json"),
                allocation=allocation.model_dump(mode="json") if allocation else None,
                frozen_exposure=exposure.model_dump(mode="json"),
                decision_exposure=frozen_exposure,
                publication_binding=publication_binding.model_dump(mode="json"),
                design_binding=design_binding.model_dump(mode="json"),
                packet_path=str(packet_path) if packet_path is not None else None,
                packet_sha256=sha256(self._read(packet_path)).hexdigest()
                if packet_path is not None
                else None,
                claim=claim,
                check_id=check_id,
            )
            subject = PublicationSubject.model_validate_json(
                canonical_json({**body, "subject_id": _digest(body)})
            )
            state = subject.model_dump(mode="json")
            self.authority._append_execution_record(
                db,
                self.workflow.lineage_id,
                {
                    "kind": "execution_event",
                    "record_id": "publication-subject-" + subject.subject_id,
                    "event": "publication_subject",
                    "state": state,
                    "state_sha256": _digest(state),
                },
            )
            return subject

    def _subject(self, db, subject_id):
        subjects, heads = self._state(db)
        subject = subjects.get(subject_id)
        if subject is None or heads.get(subject.logical_key) != subject_id:
            raise PermissionError("publication_subject_superseded")
        artifact, outcome, envelope, accepted, binding = self._accepted(
            db, subject.step_id, subject.port, subject.generation
        )
        if (
            artifact.sha256 != subject.source_sha256
            or artifact.version_id != subject.artifact_version_id
            or outcome["attempt_id"] != subject.attempt_id
            or accepted["sha256"] != subject.accepted_record_sha256
            or binding["sha256"] != subject.scientific_binding_sha256
            or _digest(envelope) != subject.envelope_sha256
        ):
            raise PermissionError("publication_source_withheld")
        return subject, artifact

    def _current_blocking_entities(self, db, subject, entity_ids, *, state=None):
        (
            "Join scoped review heads without letting an old subject "
            "select old obligations."
        )
        state = self.lifecycle.state(connection=db) if state is None else state
        bindings = {
            binding.sha256(): binding
            for binding in (subject.publication_binding, subject.design_binding)
        }
        for touchpoint in ("design", "publication"):
            remaining = set(entity_ids)
            for cycle in reversed(tuple(state["cycles"].values())):
                if cycle["touchpoint"] != touchpoint:
                    continue
                covered = set(self.lifecycle._scope(state, cycle["subject_entity_ids"]))
                if remaining.intersection(covered):
                    bindings[cycle["binding_sha256"]] = ReviewBinding.model_validate(
                        cycle["binding"]
                    )
                    remaining.difference_update(covered)
                if not remaining:
                    break
        affected = set()
        for binding in bindings.values():
            affected.update(
                self.lifecycle.blocking_entities(
                    binding,
                    touchpoint="publication",
                    entity_ids=entity_ids,
                    connection=db,
                )
            )
        return tuple(sorted(affected)), tuple(sorted(bindings))

    def descriptor(self, subject_id, *, delivery, connection=None):
        from devbench.engine.workflow_operations import OperationBlocked

        from .literature_adapter import literature_use_current

        if connection is None:
            with self.ledger._database() as db:
                db.execute("BEGIN")
                return self.descriptor(subject_id, delivery=delivery, connection=db)
        _same_transaction(connection, self.ledger)
        db = connection
        self._require_publication_recipient(db, delivery)
        subject, artifact = self._subject(db, subject_id)
        access = access_from_delivery(delivery, subject.source_sha256, connection=db)
        if not access.source:
            raise PermissionError("publication_source_withheld")
        # The fit used the frozen exposure document. Readback disclosure remains
        # cumulative in the broker, but never rewrites that decision-time basis.
        access = access.model_copy(
            update={
                "exposure": subject.frozen_exposure.exposure,
                "exposure_sha256": subject.frozen_exposure.exposure_sha256,
                "statistics": False,
            }
        )
        review_state = self.lifecycle.state(connection=db)
        families = {
            review_state["entities"][key]["family_id"]
            for key in review_state["entities"]
            if subject.entity_id in self.lifecycle._scope(review_state, (key,))
            and review_state["entities"][key]["kind"] == "table"
        }
        tables = tuple(
            key
            for key, item in review_state["entities"].items()
            if item["kind"] == "table" and item["family_id"] in families
        )
        blocked, current_review_bindings = self._current_blocking_entities(
            db, subject, tables, state=review_state
        )
        if blocked:
            access = access.model_copy(update={"outcomes": False})
        claim_current = literature_use_current(
            self.lifecycle,
            subject.entity_id,
            subject.publication_binding.sha256(),
            connection=db,
        )
        try:
            self.workflow._current_use(
                db, subject.step_id, subject.port, "claim", subject.generation
            )
        except OperationBlocked:
            claim_current = False
        dependencies = (
            subject.subject_id,
            subject.scientific_binding_sha256,
            subject.envelope_sha256,
            subject.publication_binding.sha256(),
            subject.design_binding.sha256(),
            _digest(list(blocked)),
            _digest(list(current_review_bindings)),
        )
        kwargs = dict(
            source_sha256=subject.source_sha256,
            allocation=subject.allocation,
            access=access,
            scope="adaptive",
            claim_current=claim_current,
            dependency_sha256=dependencies,
            exploratory=True,
            selected=artifact.selection_status != "none",
        )
        draft = build_publication_descriptor(subject.profile, **kwargs)
        review = self.lifecycle.review_eligibility(
            subject.publication_binding,
            draft.basis_sha256,
            entity_id=subject.entity_id,
            design_binding=subject.design_binding,
            connection=db,
        )
        # A publication reviewer may have seen the result. That exact review
        # exposure is separate from the producer's frozen decision exposure.
        if (
            subject.publication_binding.exposure_sha256
            != subject.design_binding.exposure_sha256
            and all(
                getattr(subject.publication_binding, key)
                == getattr(subject.design_binding, key)
                for key in (
                    "facts_sha256",
                    "inputs_sha256",
                    "design_sha256",
                    "procedure_sha256",
                    "policy_sha256",
                )
            )
        ):
            decision = self.lifecycle.review_eligibility(
                subject.design_binding,
                draft.basis_sha256,
                entity_id=subject.entity_id,
                design_binding=subject.design_binding,
                connection=db,
            )
            review = review.model_copy(
                update={
                    key: getattr(decision, key)
                    for key in ("design", "numerical")
                    if getattr(review, key) == "stale"
                }
            )
        # Looking up the latest cycle for a supplied old binding alone is not
        # currentness. A newer review of this use invalidates that old cycle.
        for touchpoint, bound in (
            ("design", subject.design_binding),
            ("publication", subject.publication_binding),
        ):
            latest = next(
                (
                    cycle
                    for cycle in reversed(tuple(review_state["cycles"].values()))
                    if cycle["touchpoint"] == touchpoint
                    and subject.entity_id
                    in (
                        self.lifecycle._scope(review_state, cycle["subject_entity_ids"])
                        if touchpoint == "design"
                        else cycle["subject_entity_ids"]
                    )
                ),
                None,
            )
            if latest is not None and latest["binding_sha256"] != bound.sha256():
                fields = (
                    {"design": "stale", "numerical": "stale"}
                    if touchpoint == "design"
                    else {"evidence_first": "stale", "narrative": "stale"}
                )
                review = review.model_copy(update=fields)
        if blocked:
            review = review.model_copy(update={"numerical": "held"})
        return build_publication_descriptor(
            subject.profile, review=review, **kwargs
        ).model_copy(update={"subject_id": subject.subject_id})

    def blocked_sources(self, connection):
        """Current-use vetoes for cached geometry; independent audit stays readable."""
        from devbench.engine.workflow_operations import OperationBlocked

        _same_transaction(connection, self.ledger)
        subjects, heads = self._state(connection)
        blocked = set(subjects) - set(heads.values())
        review = self.lifecycle.state(connection=connection)
        state = self.workflow._state(connection)
        registered = {
            subjects[key].source_sha256
            for key in heads.values()
            if subjects[key].entity_kind == "table"
        }
        for outcome in state.outcomes.values():
            for artifact in {
                **outcome.get("staged_outputs", {}),
                **outcome["outputs"],
            }.values():
                if artifact["sha256"] not in registered:
                    blocked.add(artifact["sha256"])
        for identity in heads.values():
            subject = subjects[identity]
            try:
                self._subject(connection, identity)
                self._read(subject.source_path, subject.source_sha256)
                tables = tuple(
                    key
                    for key, item in review["entities"].items()
                    if item["kind"] == "table"
                    and subject.entity_id in self.lifecycle._scope(review, (key,))
                )
                affected, _ = self._current_blocking_entities(
                    connection,
                    subject,
                    (*tables, subject.entity_id)
                    if subject.entity_kind == "claim"
                    else tables,
                    state=review,
                )
                if affected:
                    raise PermissionError("publication_source_withheld")
                if subject.entity_kind == "claim":
                    self.workflow._current_use(
                        connection,
                        subject.step_id,
                        subject.port,
                        "claim",
                        subject.generation,
                    )
                    from .literature_adapter import literature_use_current

                    if not literature_use_current(
                        self.lifecycle,
                        subject.entity_id,
                        subject.publication_binding.sha256(),
                        connection=connection,
                    ):
                        raise PermissionError("publication_source_withheld")
            except (OperationBlocked, PermissionError, ValueError, KeyError, OSError):
                blocked.add(identity)
                if subject.entity_kind == "table":
                    blocked.add(subject.source_sha256)
        return blocked

    def subject_for_source(
        self, source_sha256, *, connection=None, _lineage_only=False
    ):
        if connection is None:
            with self.ledger._database() as db:
                db.execute("BEGIN")
                return self.subject_for_source(
                    source_sha256, connection=db, _lineage_only=_lineage_only
                )
        _same_transaction(connection, self.ledger)
        subjects, heads = self._state(connection)
        if source_sha256 in subjects:
            # A subject reference must be resolved by its owning coordinator;
            # it cannot become an unrecognized legacy content hash.
            raise PermissionError("publication_source_withheld")
        candidates = [
            subjects[key]
            for key in heads.values()
            if subjects[key].source_sha256 == source_sha256
            and subjects[key].entity_kind == "table"
        ]
        if len(candidates) == 1:
            self._subject(connection, candidates[0].subject_id)
            return candidates[0]
        state = self.workflow._state(connection)
        recognized = any(
            item["sha256"] == source_sha256
            for outcome in state.outcomes.values()
            for item in {
                **outcome.get("staged_outputs", {}),
                **outcome["outputs"],
            }.values()
        )
        if recognized or candidates:
            raise PermissionError("publication_subject_required")
        if not _lineage_only and _known_adaptive_identity(
            self.ledger,
            connection,
            source_sha256,
            exclude_lineage=self.workflow.lineage_id,
        ):
            raise PermissionError("publication_source_withheld")
        return None

    def projection(
        self, subject_id, *, delivery, connection=None, presentation_request=None
    ):
        from .scientific_publication import _effect_figure, _retained_table

        if connection is None:
            with self.ledger._database() as db:
                db.execute("BEGIN")
                return self.projection(
                    subject_id,
                    delivery=delivery,
                    connection=db,
                    presentation_request=presentation_request,
                )
        descriptor = self.descriptor(
            subject_id, delivery=delivery, connection=connection
        )
        subject, _ = self._subject(connection, subject_id)
        raw = self._read(subject.source_path, subject.source_sha256)
        if subject.check_id is not None:
            source = json.loads(raw)
            checked = CheckedObservation.compute(subject.check_id, source)
            # Checked observations bind canonical source bytes, without a test.
            if checked.source_sha256 != subject.source_sha256:
                raise PermissionError("checked_observation_not_admitted")
            return {
                "format": "devbench.adaptive-publication/1",
                "subject_id": subject_id,
                "source_sha256": subject.source_sha256,
                "observation": checked.public(descriptor, source=source),
            }
        result = _retained_table(
            raw,
            source_path=subject.source_path,
            guard_companion=lambda identity: self._guard_companion(
                connection, identity, delivery
            ),
        )
        if result["schema"] != subject.source_schema:
            raise PermissionError("publication_source_withheld")
        rows = [
            {
                "family_id": _label(result["family_id"]),
                "gene_id": _label(row["gene_id"]),
                "status": row["status"],
                **({"effect": row["effect"]} if descriptor.allow_descriptive else {}),
            }
            for row in result["rows"]
        ]
        rows.sort(key=lambda row: (row["family_id"] or "", row["gene_id"] or ""))
        interpretation = self._interpretation(connection, subject, descriptor)
        inventory = [
            {
                "family_id": _label(result["family_id"]),
                "status": "available" if descriptor.allow_descriptive else "withheld",
                "complete": result["complete"],
                "measured": len(rows),
                "eligible": result["eligible"],
                "failed": result["failed"],
            }
        ]
        if subject.allocation is not None:
            inventory.extend(
                {
                    "family_id": _label(family.family_id),
                    "status": "withheld",
                    "complete": False,
                    "measured": len(family.feature_ids),
                    "eligible": None,
                    "failed": None,
                }
                for family in subject.allocation.families
                if family.family_id != result["family_id"]
            )
        inventory.sort(key=lambda row: row["family_id"] or "")
        scale = result["effect_scale"]
        effect_scale = (
            scale
            if (
                type(scale) is str
                and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_ ./()+-]{0,119}", scale)
                and not is_statistic_field(scale)
            )
            else "unspecified"
        )
        projection = {
            "format": "devbench.adaptive-publication/1",
            "scope": "adaptive_descriptive",
            "subject_id": subject_id,
            "source_sha256": subject.source_sha256,
            "basis_sha256": descriptor.basis_sha256,
            "effect_scale": effect_scale,
            "family_inventory": inventory,
            "table": rows,
            "interpretation": interpretation,
            "figure": _effect_figure(
                rows, subject.source_sha256, adaptive_scale=effect_scale
            )
            if descriptor.allow_descriptive
            else None,
            "exports": {
                kind: "/api/scientific/publications/" + subject_id + "?format=" + kind
                for kind in ("json", "csv", "svg", "png", "pdf", "zip")
            },
        }
        self._apply_binary_preview(projection, result, rows)
        if presentation_request is not None:
            from .bulk_plot_service import _workspace, audit_public_scientific_workspace

            if (
                subject.source_schema != "saved_bulk_result"
                or not descriptor.allow_descriptive
            ):
                raise PermissionError("publication_plot_source_unavailable")
            workspace = _workspace(
                Path(subject.source_path),
                subject.source_sha256,
                run_id=subject.attempt_id,
                mode="scientific",
                public=True,
                descriptor=descriptor,
            )
            audit_public_scientific_workspace(
                workspace, presentation_request, current_use=False
            )
            projection["presentation"] = {
                "workspace": workspace,
                "request": presentation_request,
            }
        return projection

    @staticmethod
    def _apply_binary_preview(projection, result, rows):
        if result.get("binary_transport"):
            shown = min(128, len(rows))
            status = "complete" if shown == len(rows) else "preview"
            projection["table_view"] = {
                "shown_rows": shown,
                "total_rows": len(rows),
                "status": status,
            }
            projection["table"] = rows[:shown]
            if status == "preview":
                for kind in ("csv", "zip"):
                    projection["exports"].pop(kind)

    def _interpretation(self, db, subject, descriptor):
        from .scientific_review import ActiveReviewPacket

        if subject.claim is None or not descriptor.allow_interpretation:
            return None
        packet = ActiveReviewPacket.model_validate_json(
            self._read(subject.packet_path, subject.packet_sha256)
        )
        cycle = self.lifecycle._latest_cycle(
            self.lifecycle.state(connection=db),
            subject.publication_binding,
            "publication",
            subject.entity_id,
        )
        if (
            packet.binding != subject.publication_binding
            or cycle is None
            or cycle["narrative_sha256"] != _digest(packet.controller_narrative)
            or cycle["validation_plan_sha256"]
            != _digest(packet.validation_plan.model_dump(mode="json"))
            or subject.claim
            not in [claim.model_dump(mode="json") for claim in packet.claims]
            or subject.claim["claim_class"] != "exploratory_finding"
        ):
            return None
        text = packet.controller_narrative
        if re.search(
            r"(?i)\b(?:significan\w*|p[-_ ]?value|q[-_ ]?value|fdr|causal\w*|"
            r"rank(?:ed|ing)?|top[- ]?hit\w*|"
            r"probabilit\w*|null[- ]hypothes\w*|confidence[- ]interval\w*|"
            r"standard[- ]error\w*|"
            r"statistic\w*|reject\w*|differentially[- ]expressed)\b|\b[pq]\s*[=<>]",
            text,
        ):
            return None
        if any(
            is_statistic_field(field)
            for field in (
                *subject.claim["effect_fields"],
                *subject.claim["uncertainty_fields"],
            )
        ):
            return None
        return {
            "claim_kind": "exploratory_finding",
            "claim_id": subject.claim["claim_id"],
            "revision": subject.claim["revision"],
            "text": text,
        }

    def render(self, subject_id, *, delivery, format, presentation_request=None):
        from .scientific_publication import (
            RenderedPublication,
            _publication_renderer_sha256,
            _render_publication,
        )

        request_bytes = (
            canonical_json(presentation_request)
            if presentation_request is not None
            else None
        )
        if request_bytes is not None and len(request_bytes) > 128_000:
            raise ValueError("publication_request_exceeds_bound")
        with self.ledger._database() as db:
            db.execute("BEGIN")
            projection = self.projection(
                subject_id,
                delivery=delivery,
                connection=db,
                presentation_request=json.loads(request_bytes)
                if request_bytes is not None
                else None,
            )
            descriptor = self.descriptor(subject_id, delivery=delivery, connection=db)
            if projection.get("table_view", {}).get(
                "status"
            ) == "preview" and format in {"csv", "zip"}:
                raise PermissionError("publication_format_unavailable")
            return RenderedPublication(
                self,
                delivery,
                subject_id,
                descriptor.model_dump_json(),
                canonical_json(projection),
                format,
                request_bytes,
                _publication_renderer_sha256(),
                _render_publication(projection, format),
            )

    def export(self, subject_id, *, delivery, format):
        return self.render(subject_id, delivery=delivery, format=format).release()

    def describe_replay(
        self,
        *,
        capture_entitlement,
        artifact_entitlements=None,
        planner=None,
        runtime_owner=None,
    ):
        from .bundles import investigation_replay_package

        return investigation_replay_package(
            self,
            capture_entitlement=capture_entitlement,
            artifact_entitlements=artifact_entitlements,
            planner=planner,
            runtime_owner=runtime_owner,
        )[1]

    def export_replay(
        self,
        subject_id=None,
        *,
        delivery=None,
        audience="public",
        entitlement=None,
        capture_entitlement=None,
        artifact_entitlements=None,
        planner=None,
        runtime_owner=None,
    ):
        from .bundles import (
            _audit_entitlement,
            investigation_replay_package,
            public_replay_package,
        )

        if audience == "public":
            if subject_id is None or delivery is None:
                raise PermissionError(
                    "Public replay requires the current registered publication delivery"
                )
            return public_replay_package(
                self.render(subject_id, delivery=delivery, format="json")
            )
        if audience != "restricted_audit":
            raise PermissionError("Unknown replay audience")
        entitlement = _audit_entitlement(entitlement)
        entitlement.require(entitlement.content_sha256)
        raw, receipt = investigation_replay_package(
            self,
            capture_entitlement=capture_entitlement,
            artifact_entitlements=artifact_entitlements,
            planner=planner,
            runtime_owner=runtime_owner,
        )
        return {**receipt, "bytes": entitlement.release(raw)}
