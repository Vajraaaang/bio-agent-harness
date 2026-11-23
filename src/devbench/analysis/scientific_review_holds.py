"""Coordinator-owned, revision-bound review cycles and dependency-scoped holds.

Model replies are proposals to ``admit_objection``.  The remaining methods are
trusted coordinator entrypoints; none is a JSON command or a worker dispatcher.
Records share the scientific authority's durable transaction and replay stream.
"""
from __future__ import annotations

import json
import uuid
from contextlib import nullcontext
from copy import deepcopy
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    ValidationError,
    model_validator,
)

from devbench.engine.scientific_authority import (
    _check_lineage,
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

Identity = Annotated[str, Field(pattern=r"^[A-Za-z0-9_.:-]{1,128}$")]
Digest = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
Text = Annotated[str, Field(min_length=1, max_length=4096)]
Touchpoint = Literal["design", "publication"]
Exposure = Literal["structural", "diagnostic", "target_outcome", "unknown"]
_MECHANICAL = {"AliasProved", "DeclaredDesignRoleEncoded", "ArtifactVerified", "ClaimRemoved"}
_SECTIONS = ("entities", "evidence", "obligations", "cycles", "holds", "withdrawn", "reservations")
_STABLE_BINDING_FIELDS = (
    "facts_sha256", "inputs_sha256", "design_sha256", "procedure_sha256",
    "policy_sha256", "obligations_sha256",
)


class _Record(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, validate_default=True,
        revalidate_instances="always", allow_inf_nan=False)


class ReviewBinding(_Record):
    facts_sha256: Digest
    inputs_sha256: Digest
    exposure_sha256: Digest
    design_sha256: Digest
    procedure_sha256: Digest
    policy_sha256: Digest
    evidence_sha256: Digest
    obligations_sha256: Digest
    claim_sha256: Digest | None = None

    def sha256(self):
        return _digest(_json(self.model_dump(mode="json")))


class ReviewEntity(_Record):
    entity_id: Identity
    kind: Literal["fit", "table", "claim"]
    dependencies: tuple[Identity, ...] = Field(default=(), max_length=128)
    family_id: Identity | None = None
    workflow_step_id: Identity | None = None

    @model_validator(mode="after")
    def family(self):
        if self.kind == "table" and self.family_id is None:
            raise ValueError("Inferential tables must identify the complete family")
        if len(set(self.dependencies)) != len(self.dependencies):
            raise ValueError("Duplicate review dependency")
        return self


class ResolutionPredicate(_Record):
    kind: Literal["AliasProved", "DeclaredDesignRoleEncoded", "ArtifactVerified", "ClaimRemoved",
                  "AssumptionAdequate", "CounterevidenceResolved"]
    owner_id: Identity
    evidence_required: tuple[Identity, ...] = Field(min_length=1, max_length=128)
    resumption_condition: Text

    @property
    def mechanical(self):
        return self.kind in _MECHANICAL

    def sha256(self):
        return _digest(_json(self.model_dump(mode="json")))


class DiagnosticResolutionPredicate(_Record):
    """A distinct exact numerical threat; legacy predicate bytes are unchanged."""
    kind: Literal["DiagnosticThreatAdequate"] = "DiagnosticThreatAdequate"
    owner_id: Identity
    policy_sha256: Digest
    threat: Identity
    evidence_required: tuple[Identity, ...] = Field(min_length=1, max_length=128)
    resumption_condition: Text

    @property
    def mechanical(self):
        return False

    def sha256(self):
        return _digest(_json(self.model_dump(mode="json")))


class ResolutionAssessment(_Record):
    """An explicit challenger decision, never inferred from generic completion."""

    hold_id: Identity | None = None
    obligation_id: Identity | None = None
    binding_sha256: Digest
    predicate_sha256: Digest
    satisfied: bool = Field(strict=True)
    rationale: Text
    evidence_ids: tuple[Identity, ...] = Field(min_length=1, max_length=128)

    @model_validator(mode="after")
    def exact_subject(self):
        if (self.hold_id is None) == (self.obligation_id is None) or not self.rationale.strip():
            raise ValueError("Scientific resolution requires exactly one subject and an explicit rationale")
        return self


class ReviewObligation(_Record):
    obligation_id: Identity
    entity_id: Identity
    stage: Literal["pre_fit", "post_fit", "claim"]
    evidence_ids: tuple[Identity, ...] = Field(default=(), max_length=128)
    predicate: ResolutionPredicate | DiagnosticResolutionPredicate | None = None


def obligations_digest(obligations):
    values = [ReviewObligation.model_validate(item).model_dump(mode="json") for item in obligations]
    return _digest(_json(sorted(values, key=lambda item: item["obligation_id"])))


class CheckRequest(_Record):
    threat: Text
    affected_claim_ids: tuple[Identity, ...] = Field(min_length=1, max_length=128)
    required_evidence: tuple[Identity, ...] = Field(min_length=1, max_length=128)
    registered_check_id: Identity
    predicate: ResolutionPredicate
    action_kind: Literal["read_only", "new_fit", "preprocessing"] = "read_only"
    input_evidence_ids: tuple[Identity, ...] = Field(default=(), max_length=128)


class ReviewObjection(_Record):
    category: Literal["UNIT_FACT", "MEASUREMENT", "DESIGN_ESTIMABILITY", "ESTIMAND_MISMATCH", "SELECTION",
                      "FAMILY_ACCOUNTING", "MISSING_CHECK", "COUNTEREVIDENCE", "OVERCLAIM"]
    affected_entity_ids: tuple[Identity, ...] = Field(min_length=1, max_length=128)
    evidence_ids: tuple[Identity, ...] = Field(default=(), max_length=128)
    missing_obligation_ids: tuple[Identity, ...] = Field(default=(), max_length=128)
    requested_resolution: Text
    severity: Literal["clarification", "material", "critical"]
    necessity: Text
    predicate: ResolutionPredicate
    check_request: CheckRequest | None = None
    fit_prerequisite_id: Identity | None = None
    target_invalidity_evidence_ids: tuple[Identity, ...] = Field(default=(), max_length=128)


class ReviewBudget(_Record):
    invocations: int = Field(default=24, ge=1, le=1024, strict=True)
    turns: int = Field(default=48, ge=1, le=4096, strict=True)
    queries: int = Field(default=64, ge=0, le=4096, strict=True)
    tokens: int = Field(default=262144, ge=1, le=10000000, strict=True)
    resolution_invocations: int = Field(default=4, ge=0, strict=True)
    resolution_queries: int = Field(default=8, ge=0, strict=True)
    resolution_tokens: int = Field(default=32768, ge=0, strict=True)

    @model_validator(mode="after")
    def reserves(self):
        if any(getattr(self, "resolution_" + key) > getattr(self, key)
               for key in ("invocations", "queries", "tokens")):
            raise ValueError("Resolution reserve exceeds its total review budget")
        return self


class ReviewHoldLifecycle:
    """Trusted coordinator facade.  Reopening cannot reset holds or usage."""

    def __init__(self, authority, lineage_id, *, registered_verifiers=None, registered_checks=(), budget=None,
                 adaptive_workflow=None):
        _check_lineage(lineage_id)
        self.authority, self.ledger, self.lineage_id = authority, authority.ledger, lineage_id
        self.registered_verifiers = dict(registered_verifiers or {})
        check_kinds = dict(registered_checks) if isinstance(registered_checks, dict) else {
            key: "read_only" for key in registered_checks}
        self.registered_checks = TypeAdapter(dict[Identity, Literal["read_only", "new_fit", "preprocessing"]]).validate_python(check_kinds)
        self.budget = ReviewBudget.model_validate(budget) if budget is not None else ReviewBudget()
        self.adaptive_workflow = adaptive_workflow
        if adaptive_workflow is not None:
            self._check_workflow(adaptive_workflow)

    def _check_workflow(self, workflow):
        from devbench.platform.adaptive_workflow import AdaptiveWorkflow

        if (type(workflow) is not AdaptiveWorkflow or workflow.ledger.path != self.ledger.path
                or workflow.lineage_id != self.lineage_id):
            raise PermissionError("Review actions require the same authoritative adaptive workflow")

    def _sync_fit_holds(self, db, state, updates):
        """Dispatch and hold mutations serialize under the existing ledger lock."""
        if "holds" not in updates:
            return
        from devbench.platform.adaptive_workflow import AdaptiveWorkflow

        workflow = self.adaptive_workflow or AdaptiveWorkflow(self.ledger, self.lineage_id)
        adaptive = workflow._state(db)
        holds = {**state["holds"], **updates["holds"]}
        entities = {**state["entities"], **updates.get("entities", {})}
        changed_scope = {key for hold in updates["holds"].values() for key in hold["scope"]}
        for key in sorted(changed_scope):
            entity = entities[key]
            step_id = entity["workflow_step_id"] or key
            if entity["kind"] != "fit" or step_id not in adaptive.nodes:
                continue
            held = any(hold["status"] == "open" and key in hold["scope"] for hold in holds.values())
            existing = adaptive.holds.get(step_id)
            if existing is not None and existing["reason"] != "t17_review":
                continue
            if not held and existing is None:
                continue
            proof = _digest(_json(sorted(hold["hold_id"] for hold in holds.values()
                                        if hold["status"] == "open" and key in hold["scope"])))
            workflow._event(db, "hold", {"step_id": step_id, "reason": "t17_review" if held else None,
                                        "evidence_sha256": proof})

    def _state(self, db):
        return self._projection(db)[0]

    def _projection(self, db):
        return load_scientific_projection(db, self.lineage_id, "review",
            initial=self._initial_state, apply=self._apply_record)

    @staticmethod
    def _initial_state():
        state = {key: {} for key in _SECTIONS}
        state.update(budget=None, accounting=dict.fromkeys(("invocations", "turns", "queries", "tokens"), 0),
                     observed_tokens=0, history=[])
        return state

    @staticmethod
    def _apply_record(state, row):
        event = row["record"]
        if event["event"] != "review_lifecycle":
            raise OperationConflict("Invalid review event namespace")
        change = event["state"]
        if change["format"] != "devbench.review-lifecycle/1":
            raise OperationConflict("Unsupported review lifecycle version")
        for section, values in change["updates"].items():
            if section not in (*_SECTIONS, "accounting"):
                raise OperationConflict("Invalid review state section")
            state[section].update(values)
        state["budget"] = change["budget"]
        state["observed_tokens"] += change["observed_tokens"]
        state["history"].append({"revision": row["revision"], "action": change["action"],
                                 "detail": change["detail"]})

    @classmethod
    def replay_records(cls, records):
        """Fold original validated review records without creating authority state."""
        state = cls._initial_state()
        for row in records:
            cls._apply_record(state, row)
        return state

    def state(self, *, connection=None):
        """Return a detached current projection; editing it grants no authority."""
        if connection is not None:
            require_same_ledger_transaction(connection, self.ledger)
            return self._state(connection)
        with self.ledger._database() as db:
            db.execute("BEGIN")
            return self._state(db)

    def hold_origins(self, broker, hold_id, *, connection):
        """Resolve actual review custody; citation labels never grant access."""
        from .evidence_access import AccessDenied

        if broker.authority.ledger.path != self.ledger.path:
            raise AccessDenied("Hold evidence requires the same authoritative ledger")
        state = self.state(connection=connection)
        hold = state["holds"].get(hold_id)
        cycle = state["cycles"].get(hold["cycle_id"]) if hold is not None else None
        if cycle is None:
            raise AccessDenied("Hold narrative lacks its authoritative originating record")
        evidence_ids = self._available_evidence(cycle) | set(hold["objection"]["evidence_ids"])
        request = hold["objection"].get("check_request")
        if request is not None:
            evidence_ids.update(request["input_evidence_ids"])
        if not evidence_ids:
            raise AccessDenied("Hold narrative evidence origin is unknown")
        receipts = self._hold_query_origins(broker, connection, cycle)
        return self._hold_evidence_origins(
            broker, connection, state, cycle, evidence_ids, receipts,
        )

    def _hold_query_origins(self, broker, connection, cycle):
        from .evidence_access import AccessDenied

        inventory = {identity: broker._get(connection, "context", identity)
                     for (identity,) in broker._inventory(connection, "context")}
        context_id = cycle["context_id"]
        contexts, pending = set(), [context_id]
        while pending:
            identity = pending.pop()
            if identity in contexts:
                continue
            contexts.add(identity)
            if len(contexts) > 64:
                raise AccessDenied("Hold context ancestry exceeds its bound")
            context = inventory.get(identity)
            if context is not None:
                if context["lineage_id"] != self.lineage_id:
                    raise AccessDenied(
                        "Hold context ancestry belongs to another workflow"
                    )
                pending.extend(context["parents"])
        # The reveal is a child of the evidence-first context. Include all
        # actual queries in that review, even when a reply omits a citation.
        descendants = {context_id}
        while True:
            children = {
                identity for identity, context in inventory.items()
                if context["lineage_id"] == self.lineage_id
                and context["role"] == "challenger"
                and context["phase"] == "narrative_reveal"
                and descendants.intersection(context["parents"])
            }
            if children <= descendants:
                break
            descendants.update(children)
            if len(descendants | contexts) > 64:
                raise AccessDenied("Hold context ancestry exceeds its bound")
        contexts.update(descendants)
        slots = ",".join("?" for _ in contexts)
        rows = connection.execute(
            "SELECT payload FROM scientific_disclosures WHERE "
            f"json_extract(payload,'$.context_id') IN ({slots}) "
            "AND json_type(payload,'$.query_page')='object' "
            "AND json_extract(payload,'$.possible')=1 AND "
            "json_extract(payload,'$.state') "
            "IN ('delivery_attempted','receipt_confirmed') LIMIT 65",
            tuple(sorted(contexts)),
        ).fetchall()
        if len(rows) > 64:
            raise AccessDenied("Hold query-origin inventory exceeds its bound")
        return [json.loads(row[0]) for row in rows]

    @staticmethod
    def _hold_evidence_origins(broker, connection, state, cycle, evidence_ids,
                               receipts):
        from .evidence_access import AccessDenied

        groups, dependencies = set(), set()
        for row in receipts:
            broker._context_policy(connection, row["context_id"])
            groups.add(frozenset((row["source_id"],)))
            dependencies.add(row["content_sha256"])
            dependencies.update(row.get("query_page", {}).get("dependencies", ()))
        for identity in evidence_ids:
            evidence = state["evidence"].get(identity)
            if evidence is None:
                raise AccessDenied("Hold narrative evidence origin is unknown")
            dependencies.add(evidence["sha256"])
            sources = {
                row["source_id"]
                for row in broker._source_peers(connection, evidence["sha256"])
            }
            matched = [row for row in receipts if row["content_sha256"] == evidence["sha256"]]
            if identity in cycle.get("query_sources", {}) and not any(
                row.get("query_page") for row in matched
            ):
                raise AccessDenied(
                    "Hold query evidence lacks its originating review receipt"
                )
            if sources:
                groups.add(frozenset(sources))
            if not sources and not matched:
                raise AccessDenied("Hold narrative evidence origin is unknown")
        broker._query_dependencies(connection, tuple(sorted(dependencies)))
        return {"groups": groups, "dependencies": dependencies, "evidence_ids": evidence_ids}

    def _write(self, action, build, *, expected_revision=None, with_database=False, connection=None):
        _check_lineage(self.lineage_id, expected_revision)
        if connection is not None:
            require_same_ledger_transaction(connection, self.ledger)
        with (self.authority._execution_tx(self.lineage_id) if connection is None else nullcontext(connection)) as db:
            revision = self.authority._revision(db, self.lineage_id)
            if revision == 0 or expected_revision is not None and revision != expected_revision:
                raise OperationConflict("Review mutation requires the current scientific revision")
            state, head = self._projection(db)
            if state["budget"] is not None and state["budget"] != self.budget.model_dump(mode="json"):
                raise OperationConflict("Review budget cannot reset on a new session")
            snapshot = head.snapshot(lambda: state)
            updates, detail, result = build(state, db) if with_database else build(state)
            self._sync_fit_holds(db, state, updates)
            change = {"format": "devbench.review-lifecycle/1", "action": action, "updates": updates,
                "detail": detail, "budget": self.budget.model_dump(mode="json"), "observed_tokens": 0}
            if action == "settle_invocation":
                change["observed_tokens"] = detail["observed_tokens"] or 0
            append_scientific_projection_event(self.authority, db, self.lineage_id, "review", {"kind": "execution_event",
                "record_id": "review-" + uuid.uuid4().hex, "event": "review_lifecycle",
                "state": change, "state_sha256": _digest(_json(change))}, snapshot)
            return deepcopy(result)

    @staticmethod
    def _cycle(state, cycle_id):
        if cycle_id not in state["cycles"]:
            raise OperationConflict("Review cycle is unregistered")
        return deepcopy(state["cycles"][cycle_id])

    @staticmethod
    def _hold(state, hold_id):
        if hold_id not in state["holds"]:
            raise OperationConflict("Review hold is unregistered")
        return deepcopy(state["holds"][hold_id])

    @staticmethod
    def _scope(state, entities):
        result = set(entities)
        if result - state["entities"].keys():
            raise ValueError("Affected review entity is unregistered")
        affects_fit = any(state["entities"][key]["kind"] == "fit" for key in result)
        while True:
            families = {state["entities"][key]["family_id"] for key in result
                        if state["entities"][key]["kind"] == "table"}
            enlarged = result | {key for key, item in state["entities"].items()
                if (item["kind"] == "table" and item["family_id"] in families)
                or result.intersection(item["dependencies"])}
            if enlarged == result:
                return sorted(key for key in result if affects_fit or state["entities"][key]["kind"] != "fit")
            result = enlarged

    def register_entities(self, entities, *, expected_revision=None):
        typed = tuple(ReviewEntity.model_validate(item) for item in entities)
        if not typed or len(typed) > 512 or len({item.entity_id for item in typed}) != len(typed):
            raise ValueError("Review entities need bounded unique identities")
        values = {item.entity_id: item.model_dump(mode="json") for item in typed}

        def build(state):
            for key, item in values.items():
                if key in state["entities"] and state["entities"][key] != item:
                    raise OperationConflict("Review entity versions are immutable")
            graph = {**state["entities"], **values}
            self._validate_entity_graph(graph)
            updates = {"entities": values}
            propagated = {}
            for hold_id, existing in state["holds"].items():
                if existing["status"] != "open":
                    continue
                scope = self._scope({**state, "entities": graph}, existing["objection"]["affected_entity_ids"])
                if scope != existing["scope"]:
                    propagated[hold_id] = {**existing, "scope": scope}
            if propagated:
                updates["holds"] = propagated
            return updates, {"entity_ids": sorted(values)}, values

        return self._write("register_entities", build, expected_revision=expected_revision)

    @staticmethod
    def _validate_entity_graph(graph):
        if len(graph) > 512 or any(
            set(item["dependencies"]) - graph.keys() for item in graph.values()
        ):
            raise ValueError(
                "Review graph exceeds its bound or has missing dependencies"
            )
        remaining, seen = set(graph), set()
        while remaining:
            ready = {
                key for key in remaining if set(graph[key]["dependencies"]) <= seen
            }
            if not ready:
                raise ValueError("Review dependency graph contains a cycle")
            remaining -= ready
            seen |= ready

    def register_evidence(self, evidence_id, sha256, *, relevant_entity_ids=(), content_identity_sha256=None,
                          expected_revision=None):
        TypeAdapter(tuple[Identity, Digest, tuple[Identity, ...]]).validate_python(
            (evidence_id, sha256, tuple(relevant_entity_ids)))
        record = {"evidence_id": evidence_id, "sha256": sha256, "relevant_entity_ids": list(relevant_entity_ids)}
        if content_identity_sha256 is not None:
            record["content_identity_sha256"] = TypeAdapter(Digest).validate_python(content_identity_sha256)

        def build(state):
            if set(relevant_entity_ids) - state["entities"].keys():
                raise ValueError("Evidence relevance names an unknown review entity")
            if evidence_id in state["evidence"] and state["evidence"][evidence_id] != record:
                raise OperationConflict("Evidence identities are immutable")
            if len(state["evidence"]) >= 2048 and evidence_id not in state["evidence"]:
                raise OperationBlocked("review_evidence_budget_exhausted")
            return {"evidence": {evidence_id: record}}, {"evidence_id": evidence_id}, record

        return self._write("register_evidence", build, expected_revision=expected_revision)

    @staticmethod
    def _available_evidence(cycle):
        return set(cycle["evidence_ids"]) | set(cycle.get("observed_evidence_ids", []))

    @staticmethod
    def _evidence_identity(evidence):
        return evidence.get("content_identity_sha256", evidence["sha256"])

    def record_query_evidence(self, cycle_id, evidence_id, sha256, *, relevant_entity_ids=(), source_handle_id=None,
                              content_identity_sha256=None):
        """Record a broker-verified query receipt over an already entitled source.

        The host query adapter owns source, receipt, and transport verification;
        a challenger reply cannot invoke this method or invent a query receipt.
        """
        TypeAdapter(tuple[Identity, Digest, tuple[Identity, ...]]).validate_python(
            (evidence_id, sha256, tuple(relevant_entity_ids)))
        if content_identity_sha256 is not None:
            TypeAdapter(Digest).validate_python(content_identity_sha256)

        def build(state):
            cycle = self._cycle(state, cycle_id)
            if cycle["phase"] in {"completed", "failed", "unavailable"}:
                raise OperationConflict("Query evidence cannot change a completed review cycle")
            handles = cycle["entitled_handle_ids"]
            handle = source_handle_id or (handles[0] if len(handles) == 1 else None)
            if handle is None or handle not in handles:
                raise PermissionError("Query evidence requires its exact entitled source handle")
            if set(relevant_entity_ids) - state["entities"].keys():
                raise OperationConflict("Query relevance refers to unregistered entities")
            record = {"evidence_id": evidence_id, "sha256": sha256, "relevant_entity_ids": list(relevant_entity_ids)}
            if content_identity_sha256 is not None:
                record["content_identity_sha256"] = content_identity_sha256
            if evidence_id in state["evidence"] and state["evidence"][evidence_id] != record:
                raise OperationConflict("Query receipt evidence identity is immutable")
            observed = set(cycle.get("observed_evidence_ids", [])) | {evidence_id}
            if len(observed) > self.budget.queries or len(state["evidence"]) >= 2048 and evidence_id not in state["evidence"]:
                raise OperationBlocked("query_evidence_inventory_budget_exhausted")
            cycle["observed_evidence_ids"] = sorted(observed)
            cycle.setdefault("query_sources", {})[evidence_id] = handle
            return {"evidence": {evidence_id: record}, "cycles": {cycle_id: cycle}}, {
                "cycle_id": cycle_id, "evidence_id": evidence_id, "source_handle_id": handle}, record

        return self._write("record_query_evidence", build)

    def register_obligations(self, obligations, *, binding, expected_revision=None):
        binding = ReviewBinding.model_validate(binding)
        typed = tuple(ReviewObligation.model_validate(item) for item in obligations)
        if obligations_digest(typed) != binding.obligations_sha256:
            raise OperationConflict("Review obligation digest differs from current binding")
        if len(typed) > 128 or len({item.obligation_id for item in typed}) != len(typed):
            raise ValueError("Review obligations require bounded unique identities")
        values = {item.obligation_id: {**item.model_dump(mode="json"), "satisfied": False, "proof": None}
                  for item in typed}

        def build(state):
            if any(item.entity_id not in state["entities"] for item in typed):
                raise ValueError("Review obligation names an unknown entity")
            existing = state["obligations"].get(binding.sha256())
            if existing is not None:
                return {}, {"binding_sha256": binding.sha256()}, existing
            return {"obligations": {binding.sha256(): values}}, {"binding_sha256": binding.sha256()}, values

        return self._write("register_obligations", build, expected_revision=expected_revision)

    def open_cycle(self, cycle_id, touchpoint, binding, *, reviewer_id, context_id, narrative_sha256,
                   validation_plan_sha256, exposure="unknown", exposure_receipt_ids=(), entitled_handle_ids=(),
                   evidence_ids=(), entity_ids=(), expected_revision=None):
        binding = ReviewBinding.model_validate(binding)
        TypeAdapter(tuple[Identity, Touchpoint, Identity, Identity, Digest, Digest, Exposure,
                          tuple[Identity, ...], tuple[Identity, ...]]).validate_python((cycle_id, touchpoint,
            reviewer_id, context_id, narrative_sha256, validation_plan_sha256, exposure,
            tuple(exposure_receipt_ids), tuple(entitled_handle_ids)))

        def build(state):
            if cycle_id in state["cycles"]:
                raise OperationConflict("Review cycle identity is immutable")
            if any(item["context_id"] == context_id for item in state["cycles"].values()):
                raise OperationConflict("Each touchpoint requires a fresh challenger context")
            if binding.obligations_sha256 != obligations_digest(()) and binding.sha256() not in state["obligations"]:
                raise OperationConflict("Current review obligation inventory is unregistered")
            selected = list(evidence_ids) if evidence_ids else sorted(state["evidence"])
            subjects = sorted(set(entity_ids)) if entity_ids else sorted(state["entities"])
            if not subjects or set(subjects) - state["entities"].keys():
                raise OperationConflict("Review subjects must identify registered entities")
            if set(selected) - state["evidence"].keys():
                raise OperationConflict("Review evidence is unregistered")
            rereviewed = self._rereview_holds(
                state, binding, touchpoint, subjects, cycle_id,
            )
            cycle = {"cycle_id": cycle_id, "touchpoint": touchpoint, "binding": binding.model_dump(mode="json"),
                "binding_sha256": binding.sha256(), "reviewer_id": reviewer_id, "context_id": context_id,
                "exposure": exposure, "exposure_receipt_ids": list(exposure_receipt_ids),
                "entitled_handle_ids": list(entitled_handle_ids), "evidence_ids": selected,
                "subject_entity_ids": subjects,
                "observed_evidence_ids": [], "query_sources": {},
                "narrative_sha256": narrative_sha256, "validation_plan_sha256": validation_plan_sha256,
                "phase": "evidence_first", "evidence_first_assessment_sha256": None, "assessment_sha256": None,
                "covered_obligation_ids": [], "usage": dict.fromkeys(("invocations", "turns", "queries", "tokens"), 0)}
            updates = {"cycles": {cycle_id: cycle}}
            if rereviewed:
                updates["holds"] = rereviewed
            return updates, {"cycle_id": cycle_id}, cycle

        return self._write("open_cycle", build, expected_revision=expected_revision)

    @staticmethod
    def _rereview_holds(state, binding, touchpoint, subjects, cycle_id):
        rereviewed = {}
        for hold_id, prior in state["holds"].items():
            original = state["cycles"][prior["cycle_id"]]
            if (
                prior["status"] != "open" or original["touchpoint"] != touchpoint
                or not set(subjects).intersection(prior["scope"])
                or any(original["binding"][key] != getattr(binding, key)
                       for key in _STABLE_BINDING_FIELDS)
            ):
                continue
            hold = deepcopy(prior)
            response = next(
                (item for item in hold["responses"] if not item["reviewed"]), None,
            )
            if response is None:
                raise OperationBlocked("unchanged_objection_rereview_cycle_exhausted")
            response.update(reviewed=True, rereview_cycle_id=cycle_id)
            rereviewed[hold_id] = hold
        return rereviewed

    def record_initial_assessment(self, cycle_id, *, assessment_sha256, covered_obligation_ids=()):
        TypeAdapter(Digest).validate_python(assessment_sha256)

        def build(state):
            cycle = self._cycle(state, cycle_id)
            if cycle["phase"] != "evidence_first":
                raise OperationConflict("Initial assessment is immutable and precedes narrative reveal")
            known = state["obligations"].get(cycle["binding_sha256"], {})
            if set(covered_obligation_ids) - known.keys():
                raise OperationConflict("Assessment names an unregistered obligation")
            cycle.update(phase="initial_assessed", evidence_first_assessment_sha256=assessment_sha256,
                         covered_obligation_ids=sorted(set(covered_obligation_ids)))
            return {"cycles": {cycle_id: cycle}}, {"cycle_id": cycle_id}, cycle

        return self._write("initial_assessment", build)

    def reveal_narrative(self, cycle_id, *, narrative_sha256, validation_plan_sha256):
        def build(state):
            cycle = self._cycle(state, cycle_id)
            if cycle["phase"] != "initial_assessed":
                raise OperationConflict("Narrative reveal requires the retained initial assessment")
            if (narrative_sha256 != cycle["narrative_sha256"]
                    or validation_plan_sha256 != cycle["validation_plan_sha256"]):
                raise OperationConflict("Actual controller narrative or validation plan changed")
            cycle["phase"] = "narrative_revealed"
            return {"cycles": {cycle_id: cycle}}, {"cycle_id": cycle_id}, cycle

        return self._write("reveal_narrative", build)

    def complete_cycle(self, cycle_id, *, assessment_sha256, covered_obligation_ids=(), outcome="completed",
                       assessment_content=None, connection=None):
        TypeAdapter(tuple[Digest, Literal["completed", "failed", "unavailable"]]).validate_python((assessment_sha256, outcome))
        decisions = []
        if assessment_content is not None:
            raw = _json(assessment_content)
            if type(assessment_content) is not dict or len(raw) > 262144 or _digest(raw) != assessment_sha256:
                raise OperationConflict("Review content does not match its retained assessment digest")
            decisions = TypeAdapter(list[ResolutionAssessment]).validate_python(assessment_content.get("resolution_assessments", []))
            if len(decisions) > 128 or len({(item.hold_id, item.obligation_id) for item in decisions}) != len(decisions):
                raise OperationConflict("Review content has duplicate or unbounded resolution decisions")

        def build(state, db):
            cycle = self._cycle(state, cycle_id)
            if cycle["phase"] in {"completed", "failed", "unavailable"}:
                raise OperationConflict("Completed review cycle is immutable")
            updates = {}
            if outcome == "completed":
                updates = self._completed_review_updates(
                    state, db, cycle, covered_obligation_ids, decisions,
                )
            cycle.update(phase=outcome, assessment_sha256=assessment_sha256)
            cycle["resolution_assessments"] = [item.model_dump(mode="json") for item in decisions]
            cycle["assessment_content"] = deepcopy(assessment_content)
            updates["cycles"] = {cycle_id: cycle}
            return updates, {"cycle_id": cycle_id, "outcome": outcome}, cycle

        return self._write("complete_cycle", build, with_database=True, connection=connection)

    def _completed_review_updates(self, state, db, cycle, covered_ids, decisions):
        if cycle["phase"] != "narrative_revealed":
            raise OperationConflict("Completed review requires actual narrative review")
        obligations = state["obligations"].get(cycle["binding_sha256"], {})
        covered = set(cycle["covered_obligation_ids"]) | set(covered_ids)
        if covered != set(obligations):
            raise OperationBlocked("review_obligation_coverage_incomplete")
        cycle["covered_obligation_ids"] = sorted(covered)
        updates = self._review_resolution_updates(state, cycle, obligations, decisions)

        from .literature_adapter import _state as literature_state

        external = {
            use["binding"]["obligation_id"]
            for use in literature_state(db, self.lineage_id)["claims"].values()
            if use["binding"]["review_binding_sha256"] == cycle["binding_sha256"]
        }
        rejected = {
            obligations[item.obligation_id]["entity_id"] for item in decisions
            if item.obligation_id in external and not item.satisfied
        }
        closing = {
            item.hold_id for item in decisions
            if item.hold_id is not None and item.satisfied
        }
        current_holds = {**state["holds"], **updates.get("holds", {})}
        held = {
            identity for hold in current_holds.values()
            if hold["status"] == "open" and hold["hold_id"] not in closing
            for identity in hold["scope"]
        }
        self._require_current_literature(
            db, cycle, set(cycle["subject_entity_ids"]) - rejected - held
            - state["withdrawn"].keys(),
        )
        rejected_obligations = {
            item.obligation_id for item in decisions
            if item.obligation_id is not None and not item.satisfied
        }
        if rejected_obligations:
            obligations = deepcopy(obligations)
            for identity in rejected_obligations:
                obligations[identity].update(satisfied=False, proof=None)
            updates["obligations"] = {cycle["binding_sha256"]: obligations}
        return updates

    def _review_resolution_updates(self, state, cycle, obligations, decisions):
        updates = {}
        for decision in decisions:
            if (
                decision.binding_sha256 != cycle["binding_sha256"]
                or not set(decision.evidence_ids) <= self._available_evidence(cycle)
            ):
                raise OperationConflict(
                    "Explicit resolution decision has a stale binding "
                    "or unreviewed evidence"
                )
            if decision.hold_id is not None:
                target = self._hold(state, decision.hold_id)
                predicate = ResolutionPredicate.model_validate(
                    target["objection"]["predicate"],
                )
            else:
                item = obligations.get(decision.obligation_id)
                if item is None or item["predicate"] is None:
                    raise OperationConflict(
                        "Explicit resolution decision lacks a registered "
                        "scientific predicate"
                    )
                if item["predicate"]["kind"] == "DiagnosticThreatAdequate":
                    raise PermissionError(
                        "Numerical diagnostic predicates require independently "
                        "qualified policy evidence"
                    )
                predicate = ResolutionPredicate.model_validate(item["predicate"])
            if (predicate.mechanical or predicate.owner_id != cycle["reviewer_id"]
                    or predicate.sha256() != decision.predicate_sha256):
                raise OperationConflict(
                    "Explicit resolution decision does not match its assigned "
                    "scientific predicate"
                )
            if (decision.hold_id is not None and not decision.satisfied
                    and target["status"] == "resolved"):
                target = self._negative_hold(state, cycle, decision)
                target.update(status="open", resolution=None)
                updates.setdefault("holds", {})[decision.hold_id] = target
        return updates

    def _require_current_literature(self, db, cycle, entity_ids):
        from .literature_adapter import literature_use_current
        if any(not literature_use_current(self, identity, cycle["binding_sha256"], connection=db)
               for identity in entity_ids):
            raise OperationBlocked("current_literature_claim_assessment_required")

    def _negative_hold(self, state, cycle, decision):
        target = self._hold(state, decision.hold_id)
        predicate = ResolutionPredicate.model_validate(target["objection"]["predicate"])
        target_binding = target.get("revalidated_binding", state["cycles"][target["cycle_id"]]["binding"])
        if (decision.satisfied or decision.binding_sha256 != cycle["binding_sha256"]
                or ReviewBinding.model_validate(target_binding).sha256() != cycle["binding_sha256"]
                or not set(target["objection"]["affected_entity_ids"]) <= set(cycle["subject_entity_ids"])
                or predicate.mechanical or predicate.owner_id != cycle["reviewer_id"]
                or predicate.sha256() != decision.predicate_sha256
                or not set(decision.evidence_ids) <= self._available_evidence(cycle)):
            raise OperationConflict("Negative hold assessment requires its current reviewed binding, scope and predicate")
        return target

    def admit_objection(self, cycle_id, objection, *, expected_revision=None):
        """Validate a challenger proposal without treating its prose as fact."""
        try:
            objection = ReviewObjection.model_validate(objection)
        except (ValidationError, TypeError, ValueError):
            objection = None

        def build(state):
            cycle = self._cycle(state, cycle_id)
            reasons = (
                ["invalid_objection_schema"] if objection is None
                else self._objection_reasons(state, cycle, objection)
            )
            if reasons:
                result = {"status": "clarification_required", "hold_id": None, "reasons": tuple(reasons)}
                return {}, {"cycle_id": cycle_id, **result, "reasons": reasons}, result
            # Wording, response hashes, sessions and evidence packaging cannot create
            # a second allowance for an unchanged substantive objection.
            stable = {key: cycle["binding"][key] for key in _STABLE_BINDING_FIELDS}

            def evidence_identity(names):
                # Existing byte-equivalent aliases are the same evidence.  A
                # required future artifact has no content identity yet, so its
                # declared obligation name must remain distinct.
                return sorted({"content:" + self._evidence_identity(state["evidence"][key])
                    if key in state["evidence"] else "required:" + key for key in names})

            predicate_identity = {"kind": objection.predicate.kind, "owner_id": objection.predicate.owner_id,
                "evidence_required": evidence_identity(objection.predicate.evidence_required)}
            request = objection.check_request
            check_identity = None if request is None else {
                "registered_check_id": request.registered_check_id, "action_kind": request.action_kind,
                "affected_claim_ids": sorted(set(request.affected_claim_ids)),
                "required_evidence": evidence_identity(request.required_evidence),
                "input_evidence": evidence_identity(request.input_evidence_ids or objection.evidence_ids)}
            fingerprint = _digest(_json({"binding": stable, "category": objection.category,
                "affected": sorted(objection.affected_entity_ids),
                "evidence": sorted({self._evidence_identity(state["evidence"][key]) for key in objection.evidence_ids}),
                "missing": sorted(objection.missing_obligation_ids), "predicate": predicate_identity,
                "check": check_identity, "target_invalidity": evidence_identity(objection.target_invalidity_evidence_ids),
                "prerequisite": objection.fit_prerequisite_id}))
            existing = next((hold for hold in state["holds"].values() if hold["fingerprint"] == fingerprint), None)
            if existing is not None:
                result = {"status": "held" if existing["status"] == "open" else "resolved",
                          "hold_id": existing["hold_id"], "reasons": ()}
                return {}, {"cycle_id": cycle_id, "repeated_hold_id": existing["hold_id"]}, result
            hold_id = "hold-" + fingerprint[:32]
            hold = {"hold_id": hold_id, "fingerprint": fingerprint, "cycle_id": cycle_id,
                "binding_sha256": cycle["binding_sha256"], "objection": objection.model_dump(mode="json"),
                "scope": self._scope(state, objection.affected_entity_ids), "status": "open",
                "responses": [], "checks": [], "resolution": None}
            result = {"status": "held", "hold_id": hold_id, "reasons": ()}
            return {"holds": {hold_id: hold}}, {"cycle_id": cycle_id, "hold_id": hold_id}, result

        return self._write("admit_objection", build, expected_revision=expected_revision)

    def _objection_reasons(self, state, cycle, objection):
        reasons = []
        obligations = state["obligations"].get(cycle["binding_sha256"], {})
        affected = set(objection.affected_entity_ids)
        if affected - state["entities"].keys():
            reasons.append("unknown_affected_entity")
        if affected - set(cycle["subject_entity_ids"]):
            reasons.append("affected_entity_outside_review_scope")
        if (not objection.necessity.strip()
                or not objection.requested_resolution.strip()):
            reasons.append("unjustified_necessity")
        if not objection.evidence_ids and not objection.missing_obligation_ids:
            reasons.append("ungrounded_objection")
        if set(objection.evidence_ids) - self._available_evidence(cycle):
            reasons.append("evidence_outside_current_packet")
        if set(objection.missing_obligation_ids) - obligations.keys():
            reasons.append("unregistered_missing_obligation")
        if objection.severity == "clarification":
            reasons.append("clarification_not_material_hold")
        mechanical = objection.predicate.mechanical
        if mechanical and not callable(
            self.registered_verifiers.get(objection.predicate.owner_id)
        ):
            reasons.append("unregistered_predicate_verifier")
        elif not mechanical and objection.predicate.owner_id != cycle["reviewer_id"]:
            reasons.append("scientific_owner_differs_from_challenger")
        self._check_objection_scope(state, objection, obligations, affected, reasons)
        return reasons

    def _check_objection_scope(self, state, objection, obligations, affected, reasons):
        fits = [key for key in affected if key in state["entities"]
                and state["entities"][key]["kind"] == "fit"]
        prerequisite = obligations.get(objection.fit_prerequisite_id)
        if fits:
            valid_prerequisite = (
                prerequisite is not None and prerequisite["stage"] == "pre_fit"
                and prerequisite["entity_id"] in fits and not prerequisite["satisfied"]
            )
            demonstrated = set(objection.target_invalidity_evidence_ids)
            if not valid_prerequisite and (
                not demonstrated or not demonstrated <= set(objection.evidence_ids)
            ):
                reasons.append(
                    "fit_hold_requires_prerequisite_or_demonstrated_target_invalidity"
                )
        request = objection.check_request
        if request is None:
            return
        registered_kind = self.registered_checks.get(request.registered_check_id)
        if registered_kind != request.action_kind:
            reasons.append("unregistered_resolving_check_kind")
        if any(key not in state["entities"] or state["entities"][key]["kind"] != "claim"
               for key in request.affected_claim_ids):
            reasons.append("check_request_requires_registered_claims")
        if request.predicate != objection.predicate:
            reasons.append("check_predicate_differs_from_objection")
        if not reasons and not set(request.affected_claim_ids) <= set(
            self._scope(state, affected)
        ):
            reasons.append("check_claims_outside_hold_dependencies")

    def controller_response(self, hold_id, *, response_sha256, relevant_evidence_ids=(), connection=None):
        TypeAdapter(Digest).validate_python(response_sha256)

        def build(state):
            hold = self._hold(state, hold_id)
            if hold["status"] != "open":
                raise OperationConflict("Response requires an open review hold")
            new = set(relevant_evidence_ids)
            if new - state["evidence"].keys() or any(not set(self._scope(state,
                    state["evidence"][key]["relevant_entity_ids"])) & set(hold["scope"]) for key in new):
                raise OperationBlocked("response_requires_registered_relevant_evidence")
            used = set(hold["objection"]["evidence_ids"])
            used.update(key for response in hold["responses"] for key in response["evidence_ids"])
            used_hashes = {self._evidence_identity(state["evidence"][key]) for key in used}
            new_hashes = {self._evidence_identity(state["evidence"][key]) for key in new}
            if hold["responses"] and not new_hashes - used_hashes:
                raise OperationBlocked("unchanged_objection_response_cycle_exhausted")
            response = {"response_sha256": response_sha256, "evidence_ids": sorted(new),
                        "reviewed": False}
            hold["responses"].append(response)
            return {"holds": {hold_id: hold}}, {"hold_id": hold_id, "response": response}, hold

        return self._write("controller_response", build, connection=connection)

    def _check_candidate(self, state, db, hold_id, workflow, step, step_sha256, actor):
        hold = self._hold(state, hold_id)
        request = CheckRequest.model_validate(hold["objection"]["check_request"]) \
            if hold["objection"]["check_request"] is not None else None
        if hold["status"] != "open" or request is None:
            raise OperationConflict("Resolving check requires an open hold and typed CheckRequest")
        if self.registered_checks.get(request.registered_check_id) != request.action_kind or step.operation_id != request.registered_check_id:
            raise PermissionError("Resolving action differs from the registered CheckRequest")
        capability = next(item for item in step.profile.operations if item.operation_id == step.operation_id)
        if request.action_kind == "read_only" and (
            step.primary
            or step.purpose in {"qualified_inference", "admitted_research_inference"}
            or set(capability.effects) - {"read_only", "read_frozen_artifacts"}
            or any(link.source_port is not None for link in step.inputs)
        ):
            raise PermissionError(
                "Read-only CheckRequest cannot schedule a fit, preprocessing, "
                "or mutable input"
            )
        required_inputs = request.input_evidence_ids or tuple(hold["objection"]["evidence_ids"])
        if not required_inputs or set(required_inputs) - state["evidence"].keys():
            raise OperationBlocked("resolving_check_input_evidence_required")
        hashes = self._check_input_hashes(workflow._state(db), step)
        if not {state["evidence"][key]["sha256"] for key in required_inputs} <= hashes:
            raise OperationBlocked("resolving_check_input_evidence_not_pinned")
        existing = next(
            (item for item in hold["checks"] if item["action_id"] == step.step_id),
            None,
        )
        if existing is not None and existing["step_sha256"] != step_sha256:
            raise OperationConflict(
                "Resolving action identity cannot change its step specification"
            )
        workflow.candidate_envelope(step, actor=actor, connection=db)
        return hold, request, existing

    @staticmethod
    def _check_input_hashes(adaptive, step):
        hashes = set()
        for link in step.inputs:
            if link.source_port is None and link.source_id in adaptive.artifacts:
                hashes.add(adaptive.artifacts[link.source_id][0]["sha256"])
            elif link.source_port is not None and link.source_id in adaptive.nodes:
                outcome = adaptive.outcome(link.source_id)
                if outcome and link.source_port in outcome["outputs"]:
                    hashes.add(outcome["outputs"][link.source_port]["sha256"])
        return hashes

    def check_candidate(self, hold_id, workflow, step, *, actor="review-controller", connection=None):
        """Validate the same exact resolving action as scheduling, without writes."""
        from devbench.platform.adaptive_workflow import AdaptiveStep, request_digest

        self._check_workflow(workflow)
        step = AdaptiveStep.model_validate(step.model_dump() if isinstance(step, AdaptiveStep) else step)
        with (self.ledger._database() if connection is None else nullcontext(connection)) as db:
            if connection is None:
                db.execute("BEGIN")
            state = self.state(connection=db)
            self._check_candidate(state, db, hold_id, workflow, step,
                                  request_digest(step.model_dump(mode="json")), actor)
        return True

    def schedule_check(self, hold_id, workflow, step, *, actor="review-controller", connection=None):
        """Insert a host-authorized resolving step; never invoke an executor."""
        from devbench.platform.adaptive_workflow import AdaptiveStep, request_digest

        self._check_workflow(workflow)
        step = AdaptiveStep.model_validate(step)
        step_sha256 = request_digest(step.model_dump(mode="json"))

        def reserve(state, db):
            hold, request, existing = self._check_candidate(
                state, db, hold_id, workflow, step, step_sha256, actor)
            if existing is not None:
                return {}, {"hold_id": hold_id, "action_id": step.step_id}, existing
            trace = {"action_id": step.step_id, "step_sha256": step_sha256,
                "request": request.model_dump(mode="json"), "status": "reserved", "result": None}
            hold["checks"].append(trace)
            return {"holds": {hold_id: hold}}, {"hold_id": hold_id, "action": trace}, trace

        reserved = self._write("reserve_check", reserve, with_database=True, connection=connection)
        if reserved["status"] == "reserved":
            with (self.ledger._database() if connection is None else nullcontext(connection)) as db:
                existing = workflow._state(db).nodes.get(step.step_id)
            if existing is None:
                workflow.insert(step, evidence_sha256=step_sha256, **({"connection": connection} if connection is not None else {}))
            elif request_digest(existing.model_dump(mode="json")) != step_sha256:
                raise OperationConflict("Resolving action conflicts with its current DAG step")

        def scheduled(state):
            hold = self._hold(state, hold_id)
            trace = next(item for item in hold["checks"] if item["action_id"] == step.step_id)
            if trace["status"] == "reserved":
                trace["status"] = "scheduled"
            return {"holds": {hold_id: hold}}, {"hold_id": hold_id, "action_id": step.step_id}, trace

        return self._write("schedule_check", scheduled, connection=connection)

    def record_check_result(self, hold_id, *, action_id, result_sha256, evidence_ids):
        """Retain an actual accepted DAG output; completion is not resolution."""
        from devbench.platform.adaptive_workflow import AdaptiveWorkflow

        TypeAdapter(tuple[Identity, Digest]).validate_python((action_id, result_sha256))
        workflow = AdaptiveWorkflow(self.ledger, self.lineage_id)

        def build(state, db):
            adaptive = workflow._state(db)
            outcome = adaptive.outcome(action_id) if action_id in adaptive.nodes else None
            if outcome is None or result_sha256 not in {item["sha256"] for item in outcome["outputs"].values()}:
                raise OperationConflict("Resolving result is absent from the current accepted adaptive DAG outputs")
            for port, artifact in outcome["outputs"].items():
                if artifact["sha256"] == result_sha256:
                    workflow._current_use(db, action_id, port, "compute", None)
            hold = self._hold(state, hold_id)
            trace = next((item for item in hold["checks"] if item["action_id"] == action_id), None)
            if trace is None or trace["status"] != "scheduled":
                raise OperationConflict("Resolving result requires its recorded scheduled action")
            evidence = self._proof_evidence(state, (), evidence_ids)
            if result_sha256 not in {item["sha256"] for item in evidence.values()}:
                raise OperationConflict("Resolving result must name its registered output evidence")
            trace.update(status="completed", result={"result_sha256": result_sha256,
                "evidence_ids": sorted(evidence), "attempt_id": outcome["attempt_id"]})
            return {"holds": {hold_id: hold}}, {"hold_id": hold_id, "action": trace}, trace

        return self._write("record_check_result", build, with_database=True)

    @staticmethod
    def _proof_evidence(state, required, evidence_ids):
        names = set(evidence_ids)
        if names - state["evidence"].keys() or not set(required) <= names:
            raise OperationBlocked("resolution_predicate_evidence_incomplete")
        return {key: state["evidence"][key] for key in sorted(names)}

    def resolve_mechanical(self, hold_id, *, verifier_id, proof_sha256, evidence_ids):
        TypeAdapter(Digest).validate_python(proof_sha256)

        def build(state):
            hold = self._hold(state, hold_id)
            predicate = ResolutionPredicate.model_validate(hold["objection"]["predicate"])
            verifier = self.registered_verifiers.get(verifier_id)
            if not predicate.mechanical or verifier_id != predicate.owner_id or not callable(verifier):
                raise PermissionError("Only the registered mechanical predicate verifier may close this hold")
            if hold["status"] != "open":
                raise OperationConflict("Mechanical proof requires an open hold")
            evidence = self._proof_evidence(state, predicate.evidence_required, evidence_ids)
            proof = {"hold_id": hold_id, "binding_sha256": hold["binding_sha256"],
                     "predicate": predicate.model_dump(mode="json"), "proof_sha256": proof_sha256,
                     "evidence": evidence, "objection": hold["objection"]}
            if verifier(deepcopy(proof)) is not True:
                raise OperationBlocked("mechanical_resolution_predicate_not_satisfied")
            hold.update(status="resolved", resolution={"kind": "mechanical_proof", "verifier_id": verifier_id,
                "proof_sha256": proof_sha256, "evidence_ids": sorted(evidence)})
            return {"holds": {hold_id: hold}}, {"hold_id": hold_id, "resolution": hold["resolution"]}, hold

        return self._write("resolve_mechanical", build)

    def resolve_scientific(self, hold_id, *, cycle_id, evidence_ids, assessment_sha256, connection=None):
        """Record a bound challenger withdrawal, never a controller verdict."""
        def build(state, db):
            hold, cycle = self._hold(state, hold_id), self._cycle(state, cycle_id)
            predicate = ResolutionPredicate.model_validate(hold["objection"]["predicate"])
            if predicate.mechanical or predicate.owner_id != cycle["reviewer_id"]:
                raise PermissionError("Scientific predicate requires its assigned challenger")
            if (hold["status"] != "open" or cycle["phase"] != "completed"
                    or cycle["assessment_sha256"] != assessment_sha256):
                raise OperationConflict("Scientific resolution requires the current completed assessment")
            self._require_explicit_decision(cycle, predicate, evidence_ids, hold_id=hold_id)
            original = state["cycles"][hold["cycle_id"]]["binding"]
            current_binding = hold.get("revalidated_binding", original)
            if any(cycle["binding"][key] != current_binding[key] for key in (
                    "facts_sha256", "inputs_sha256", "design_sha256", "procedure_sha256", "policy_sha256",
                    "exposure_sha256", "obligations_sha256")):
                raise OperationConflict("Scientific closure requires revalidation of changed hold dependencies")
            evidence = self._proof_evidence(state, predicate.evidence_required, evidence_ids)
            if not set(evidence) <= self._available_evidence(cycle):
                raise OperationConflict("Scientific closure evidence was absent from the review packet")
            original_hashes = {self._evidence_identity(state["evidence"][key]) for key in hold["objection"]["evidence_ids"]}
            novel = {key for key in evidence if self._evidence_identity(evidence[key]) not in original_hashes}
            if not novel:
                raise OperationBlocked("scientific_withdrawal_requires_specific_new_evidence")
            if not any(set(self._scope(state, evidence[key]["relevant_entity_ids"])) & set(hold["scope"])
                       for key in novel):
                raise OperationBlocked("scientific_withdrawal_requires_relevant_new_evidence")
            required = hold["objection"]["missing_obligation_ids"]
            obligations = state["obligations"].get(cycle["binding_sha256"], {})
            if any(key not in obligations or not obligations[key]["satisfied"] for key in required):
                raise OperationBlocked("scientific_resolution_obligations_unmet")
            self._require_current_literature(db, cycle, hold["scope"])
            hold.update(status="resolved", resolution={"kind": "challenger_withdrawal", "cycle_id": cycle_id,
                "assessment_sha256": assessment_sha256, "evidence_ids": sorted(evidence)})
            return {"holds": {hold_id: hold}}, {"hold_id": hold_id, "resolution": hold["resolution"]}, hold

        return self._write("resolve_scientific", build, with_database=True, connection=connection)

    def revalidate_hold(self, hold_id, binding, *, verifier_id, proof_sha256, evidence_ids):
        """Bind corrected dependencies after host-registered verification.

        This preserves the original objection and leaves its scientific status
        open until its assigned challenger makes an explicit current decision.
        """
        binding = ReviewBinding.model_validate(binding)
        TypeAdapter(Digest).validate_python(proof_sha256)

        def build(state):
            hold = self._hold(state, hold_id)
            verifier = self.registered_verifiers.get(verifier_id)
            if not callable(verifier):
                raise PermissionError("Dependency revalidation requires a registered verifier")
            if hold["status"] != "open" or not evidence_ids:
                raise OperationConflict("Dependency revalidation requires an open hold and corrective evidence")
            evidence = self._proof_evidence(state, (), evidence_ids)
            if not any(set(self._scope(state, item["relevant_entity_ids"])) & set(hold["scope"])
                       for item in evidence.values()):
                raise OperationBlocked("dependency_revalidation_requires_relevant_evidence")
            if binding.obligations_sha256 != obligations_digest(()) and binding.sha256() not in state["obligations"]:
                raise OperationConflict("Corrected binding requires its current obligation inventory")
            proof = {"hold_id": hold_id, "scope": hold["scope"], "original_binding_sha256": hold["binding_sha256"],
                "binding": binding.model_dump(mode="json"), "proof_sha256": proof_sha256, "evidence": evidence}
            if verifier(deepcopy(proof)) is not True:
                raise OperationBlocked("changed_review_dependencies_not_verified")
            hold["revalidated_binding"] = binding.model_dump(mode="json")
            hold.setdefault("dependency_revalidations", []).append({**proof, "verifier_id": verifier_id})
            return {"holds": {hold_id: hold}}, {"hold_id": hold_id, "proof": proof}, hold

        return self._write("revalidate_hold", build)

    @staticmethod
    def _require_explicit_decision(cycle, predicate, evidence_ids, *, hold_id=None, obligation_id=None):
        decision = next((item for item in cycle.get("resolution_assessments", [])
            if item["hold_id"] == hold_id and item["obligation_id"] == obligation_id), None)
        if (decision is None or not decision["satisfied"] or decision["binding_sha256"] != cycle["binding_sha256"]
                or decision["predicate_sha256"] != predicate.sha256()
                or set(decision["evidence_ids"]) != set(evidence_ids)):
            raise OperationBlocked("explicit_satisfied_scientific_resolution_required")

    def satisfy_obligation(self, obligation_id, binding, *, verifier_id, proof_sha256, evidence_ids):
        binding = ReviewBinding.model_validate(binding)
        TypeAdapter(Digest).validate_python(proof_sha256)

        def build(state):
            obligations = deepcopy(state["obligations"].get(binding.sha256(), {}))
            if obligation_id not in obligations:
                raise OperationConflict("Proof does not match a current registered obligation")
            item = obligations[obligation_id]
            if item["predicate"] and item["predicate"]["kind"] == "DiagnosticThreatAdequate":
                raise PermissionError("Numerical diagnostic predicates require their qualified policy authority")
            predicate = ResolutionPredicate.model_validate(item["predicate"]) if item["predicate"] else None
            if predicate is not None and not predicate.mechanical:
                raise PermissionError("A scientific obligation requires an explicit challenger assessment")
            verifier = self.registered_verifiers.get(verifier_id)
            if not callable(verifier) or predicate is not None and verifier_id != predicate.owner_id:
                raise PermissionError("Obligation requires its registered verifier")
            evidence = self._proof_evidence(state,
                (*item["evidence_ids"], *(predicate.evidence_required if predicate else ())), evidence_ids)
            proof = {"obligation_id": obligation_id, "binding_sha256": binding.sha256(),
                     "proof_sha256": proof_sha256, "evidence": evidence, "predicate": item["predicate"]}
            if verifier(deepcopy(proof)) is not True:
                raise OperationBlocked("obligation_resolution_predicate_not_satisfied")
            item.update(satisfied=True, proof={**proof, "verifier_id": verifier_id})
            return {"obligations": {binding.sha256(): obligations}}, proof, item

        return self._write("satisfy_obligation", build)

    def assess_obligation(self, obligation_id, binding, *, cycle_id, evidence_ids, assessment_sha256, connection=None):
        binding = ReviewBinding.model_validate(binding)

        def build(state, db):
            cycle = self._cycle(state, cycle_id)
            obligations = deepcopy(state["obligations"].get(binding.sha256(), {}))
            item = obligations.get(obligation_id)
            if item is None or cycle["binding_sha256"] != binding.sha256():
                raise OperationConflict("Scientific obligation assessment requires its current binding")
            if item["predicate"] and item["predicate"]["kind"] == "DiagnosticThreatAdequate":
                raise PermissionError("Numerical diagnostic predicates require their qualified policy authority")
            predicate = ResolutionPredicate.model_validate(item["predicate"]) if item["predicate"] else None
            if predicate is None or predicate.mechanical or predicate.owner_id != cycle["reviewer_id"]:
                raise PermissionError("Scientific obligation requires its assigned challenger")
            if cycle["phase"] != "completed" or cycle["assessment_sha256"] != assessment_sha256:
                raise OperationConflict("Scientific obligation requires a completed assessment")
            self._require_explicit_decision(cycle, predicate, evidence_ids, obligation_id=obligation_id)
            evidence = self._proof_evidence(state, (*item["evidence_ids"], *predicate.evidence_required), evidence_ids)
            if not set(evidence) <= self._available_evidence(cycle):
                raise OperationConflict("Scientific evidence was absent from the review packet")
            from .literature_adapter import _state as literature_state
            if any(use["binding"]["obligation_id"] == obligation_id and use["binding"]["claim_id"] == item["entity_id"]
                   for use in literature_state(db, self.lineage_id)["claims"].values()):
                self._require_current_literature(db, cycle, (item["entity_id"],))
            proof = {"cycle_id": cycle_id, "binding_sha256": binding.sha256(),
                     "assessment_sha256": assessment_sha256, "evidence_ids": sorted(evidence)}
            item.update(satisfied=True, proof=proof)
            return {"obligations": {binding.sha256(): obligations}}, proof, item

        return self._write("assess_obligation", build, with_database=True, connection=connection)

    def satisfy_diagnostic_obligation(self, obligation_id, binding, *, policy, inputs, context,
                                     route_state, qualification_authority, evidence_ids, current_binding):
        """Accept only an exact qualified numerical alternative, atomically."""
        apply = self._prepare_diagnostic_satisfactions((obligation_id,), binding, policy=policy, inputs=inputs,
            context=context, route_state=route_state, qualification_authority=qualification_authority,
            evidence_ids=evidence_ids, current_binding=current_binding)
        return apply()[obligation_id]

    def _prepare_diagnostic_satisfactions(self, obligation_ids, binding, *, policy, inputs, context,
                                          route_state, qualification_authority, evidence_ids, current_binding,
                                          expected_acceptance=None):
        """Read acceptance without a write lock and return its fenced ledger mutation."""
        from .rna_diagnostic_policy import (
            DiagnosticContext,
            DiagnosticQualificationAuthority,
            DiagnosticRouteState,
            implementation_sha256,
            route_rna_diagnostic,
        )

        binding = ReviewBinding.model_validate(binding)
        binding_sha256 = binding.sha256()
        if type(qualification_authority) is not DiagnosticQualificationAuthority or not callable(current_binding):
            raise PermissionError("A trusted diagnostic authority and current binding resolver are required")
        route_state = DiagnosticRouteState.model_validate(route_state)
        if route_state.review_binding_sha256 != binding_sha256:
            raise OperationConflict("Diagnostic route does not bind the current review subject")
        inputs = deepcopy(inputs)
        context = DiagnosticContext.model_validate(context).model_dump(mode="json")
        state_document = route_state.model_dump(mode="json")
        evidence_ids = tuple(evidence_ids)
        with self.ledger._database() as db:
            db.execute("BEGIN")
            revision = self.authority._revision(db, self.lineage_id)
            original = self._state(db)
            execution_fence = self.authority._load_state(db, self.lineage_id)
            if execution_fence is not None:
                self.authority._active(db, self.lineage_id, execution_fence)
        subjects = self._diagnostic_obligation_subjects(
            original, binding_sha256, obligation_ids, policy, route_state, evidence_ids,
        )

        implementation = implementation_sha256()
        route = route_rna_diagnostic(policy, inputs, context, route_state, authority=qualification_authority)
        if (route["route"] != "matched_quantitative_alternative" or not subjects
                or subjects.keys() - set(route["satisfied_obligation_ids"])
                or any(route["input_sha256"] not in {row["sha256"] for row in evidence.values()}
                    for _, _, _, evidence in subjects.values())):
            raise OperationBlocked("qualified_matched_diagnostic_evidence_required")
        if expected_acceptance is not None and route["qualification_evidence"] != expected_acceptance:
            raise PermissionError("Diagnostic qualification acceptance changed before satisfaction")

        def require_current(db, fresh):
            self._require_diagnostic_satisfaction_current(
                db, fresh, binding_sha256=binding_sha256,
                current_binding=current_binding, revision=revision,
                execution_fence=execution_fence, implementation=implementation,
                current_implementation=implementation_sha256, subjects=subjects,
                evidence_ids=evidence_ids,
            )

        with self.ledger._database() as db:
            db.execute("BEGIN")
            require_current(db, self._state(db))
        accepted = qualification_authority.verify(policy, context, route["execution"], state_document)
        if accepted is None or accepted != route["qualification_evidence"]:
            raise PermissionError("Diagnostic qualification acceptance changed or was revoked before satisfaction")

        def build(state, db):
            require_current(db, state)
            return self._diagnostic_satisfaction_updates(
                state, binding_sha256, policy, route, accepted, execution_fence,
                subjects,
            )

        def apply(connection=None):
            return self._write("satisfy_diagnostic_obligation", build, expected_revision=revision,
                with_database=True, connection=connection)

        return apply

    def _diagnostic_obligation_subjects(
        self, state, binding_sha256, obligation_ids, policy, route_state, evidence_ids,
    ):
        value = policy.to_dict()
        scope = {entry.obligation_id: entry.threat for entry in route_state.obligations}
        subjects = {}
        for identity in obligation_ids:
            item = state["obligations"].get(binding_sha256, {}).get(identity)
            if (
                item is None or not item["predicate"]
                or item["predicate"]["kind"] != "DiagnosticThreatAdequate"
            ):
                raise PermissionError(
                    "Only an explicit numerical diagnostic obligation admits this "
                    "alternative"
                )
            predicate = DiagnosticResolutionPredicate.model_validate(item["predicate"])
            if (
                predicate.policy_sha256 != policy.sha256
                or predicate.owner_id != value["policy_id"]
                or predicate.threat != route_state.threat
                or item["entity_id"] != route_state.affected_use
                or scope.get(identity) != predicate.threat
            ):
                raise OperationConflict(
                    "Diagnostic policy, threat, obligation or affected use differs "
                    "from its predicate"
                )
            required = (*item["evidence_ids"], *predicate.evidence_required)
            evidence = self._proof_evidence(state, required, evidence_ids)
            subjects[identity] = (item, predicate, required, evidence)
        return subjects

    def _require_diagnostic_satisfaction_current(
        self, db, state, *, binding_sha256, current_binding, revision,
        execution_fence, implementation, current_implementation, subjects, evidence_ids,
    ):
        if ReviewBinding.model_validate(current_binding()).sha256() != binding_sha256:
            raise OperationConflict(
                "Current review binding changed during diagnostic qualification"
            )
        execution = self.authority._load_state(db, self.lineage_id)
        if execution_fence is not None:
            self.authority._active(
                db, self.lineage_id, execution,
                token={"epoch": execution_fence["epoch"]},
            )
        if (execution != execution_fence
                or self.authority._revision(db, self.lineage_id) != revision):
            raise OperationConflict(
                "Diagnostic scientific revision or execution fence changed during "
                "qualification"
            )
        if current_implementation() != implementation:
            raise PermissionError(
                "Diagnostic implementation source changed before satisfaction"
            )
        current = state["obligations"].get(binding_sha256, {})
        for identity, (item, _, required, evidence) in subjects.items():
            if (current.get(identity) != item or item["entity_id"] in state["withdrawn"]
                    or self._proof_evidence(state, required, evidence_ids) != evidence):
                raise OperationConflict(
                    "Diagnostic obligation, evidence or current use changed during "
                    "qualification"
                )
        from .literature_adapter import _state as literature_state

        if any(use["binding"]["obligation_id"] in subjects
               for use in literature_state(db, self.lineage_id)["claims"].values()):
            raise PermissionError(
                "A diagnostic alternative cannot discharge a Literature obligation"
            )

    @staticmethod
    def _diagnostic_satisfaction_updates(
        state, binding_sha256, policy, route, accepted, execution_fence, subjects,
    ):
        # The acceptance SHA commits its independently verified exact subject.
        if (accepted["acceptance_sha256"]
                != route["qualification_evidence"]["acceptance_sha256"]):
            raise PermissionError(
                "Diagnostic qualification acceptance changed or was revoked before "
                "satisfaction"
            )
        obligations = deepcopy(state["obligations"][binding_sha256])
        route_sha256 = _digest(_json(route))
        proofs = {}
        for identity, (_, predicate, _, evidence) in subjects.items():
            proof = {
                "policy_sha256": policy.sha256, "predicate_sha256": predicate.sha256(),
                "binding_sha256": binding_sha256, "threat": predicate.threat,
                "route_sha256": route_sha256, "qualification": accepted,
                "evidence": evidence, "execution_fence": deepcopy(execution_fence),
            }
            obligations[identity].update(satisfied=True, proof=proof)
            proofs[identity] = proof
        detail = {"proofs": proofs}
        if len(subjects) == 1:
            identity, proof = next(iter(proofs.items()))
            detail = {"obligation_id": identity, **proof}
        return (
            {"obligations": {binding_sha256: obligations}}, detail,
            {key: obligations[key] for key in subjects},
        )

    def withdraw_use(self, entity_id, *, reason):
        TypeAdapter(Text).validate_python(reason)

        def build(state):
            scope = self._scope(state, (entity_id,))
            values = {key: {"reason": reason, "requested_entity_id": entity_id} for key in scope}
            return {"withdrawn": values}, {"entity_id": entity_id, "reason": reason}, values

        return self._write("withdraw_use", build)

    @classmethod
    def _latest_cycle(cls, state, binding, touchpoint, entity_id=None):
        for cycle in reversed(tuple(state["cycles"].values())):
            if cycle["touchpoint"] != touchpoint or cycle["binding_sha256"] != binding.sha256():
                continue
            subjects = cycle["subject_entity_ids"]
            coverage = cls._scope(state, [key for key in subjects if state["entities"][key]["kind"] == "fit"]) \
                if touchpoint == "design" else subjects
            if entity_id is None or entity_id in coverage:
                return cycle
        return None

    def require_primary_fit(self, entity_id, binding, *, connection=None):
        binding = ReviewBinding.model_validate(binding)
        state = self.state(connection=connection)
        self._primary_fit_cycle(state, entity_id, binding)

    def _primary_fit_cycle(self, state, entity_id, binding):
        if entity_id not in state["entities"] or state["entities"][entity_id]["kind"] != "fit":
            raise OperationConflict("Primary fit gate requires a registered fit entity")
        cycle = self._latest_cycle(state, binding, "design", entity_id)
        if cycle is None or cycle["phase"] != "completed":
            raise OperationBlocked("completed_current_design_review_required")
        if entity_id in state["withdrawn"]:
            raise OperationBlocked("affected_use_withdrawn")
        if any(hold["status"] == "open" and entity_id in hold["scope"] for hold in state["holds"].values()):
            raise OperationBlocked("unresolved_fit_review_hold")
        for item in state["obligations"].get(binding.sha256(), {}).values():
            if item["stage"] == "pre_fit" and not item["satisfied"] \
                    and entity_id in self._scope(state, (item["entity_id"],)):
                raise OperationBlocked("unmet_pre_fit_obligation")
        return cycle

    def require_admitted_research_fit(self, entity_id, binding, *, connection=None):
        """Require current blinded reviews of this exact prospective fit scope."""
        binding = ReviewBinding.model_validate(binding)
        state = self.state(connection=connection)
        design = self._primary_fit_cycle(state, entity_id, binding)
        publication = self._latest_cycle(state, binding, "publication", entity_id)
        if publication is None or publication["phase"] != "completed":
            raise OperationBlocked("completed_current_publication_review_required")
        if any(cycle["exposure"] not in {"structural", "diagnostic"}
               or not cycle["evidence_first_assessment_sha256"]
               or not cycle["assessment_sha256"] for cycle in (design, publication)):
            raise OperationBlocked("blinded_current_research_reviews_required")
        scope = set(self._scope(state, (entity_id,)))
        if scope.intersection(state["withdrawn"]) or any(
            row["status"] == "open" and scope.intersection(row["scope"])
            for row in state["holds"].values()
        ):
            raise OperationBlocked("unresolved_research_review_hold")
        bound = state["obligations"].get(binding.sha256(), {})
        obligations = [row for row in bound.values() if row["entity_id"] in scope]
        if any(not row["satisfied"] for row in obligations):
            raise OperationBlocked("unmet_research_review_obligation")
        shared_evidence = (
            self._available_evidence(design) & self._available_evidence(publication)
        )
        evidence = [
            state["evidence"][identity]["sha256"] for identity in shared_evidence
            if identity in state["evidence"] and scope.intersection(
                state["evidence"][identity]["relevant_entity_ids"]
            )
        ]
        if not evidence:
            raise OperationBlocked(
                "shared_scoped_research_diagnostic_evidence_required"
            )
        # Additional post-fit diagnostics apply when a review actually binds
        # their obligation. Unknown or unregistered Q labels fail qualification.
        checks = {"Q-DESIGN"}
        for row in obligations:
            predicate = row.get("predicate") or {}
            if predicate.get("kind") == "DiagnosticThreatAdequate":
                checks.add(predicate["owner_id"])
        return {
            "binding_sha256": binding.sha256(),
            "design": design,
            "publication": publication,
            "required_checks": sorted(checks),
            "scoped_evidence_sha256": sorted(set(evidence)),
        }

    def blocking_entities(self, binding, *, touchpoint, entity_ids, connection=None):
        """Return affected uses, independently of a cycle's pending/completed phase."""
        binding = ReviewBinding.model_validate(binding)
        TypeAdapter(Touchpoint).validate_python(touchpoint)
        state = self.state(connection=connection)
        subjects = set(entity_ids)
        if subjects - state["entities"].keys():
            raise OperationConflict("Blocking projection requires registered subject entities")
        affected = set(state["withdrawn"])
        for hold in state["holds"].values():
            if hold["status"] == "open":
                affected.update(hold["scope"])
        for item in state["obligations"].get(binding.sha256(), {}).values():
            if not item["satisfied"] and (touchpoint == "publication" or item["stage"] == "pre_fit"):
                affected.update(self._scope(state, (item["entity_id"],)))
        if touchpoint == "design":
            affected = {key for key in affected if state["entities"][key]["kind"] == "fit"}
        return tuple(sorted(subjects & affected))

    def review_eligibility(self, binding, publication_basis_sha256, *, entity_id=None, design_binding=None,
                           connection=None):
        """Project coordinator state into T16; this never overrides numerical failures."""
        from .scientific_publication import ReviewEligibility

        binding, state = ReviewBinding.model_validate(binding), self.state(connection=connection)
        design_binding = ReviewBinding.model_validate(design_binding) if design_binding is not None else binding
        TypeAdapter(Digest).validate_python(publication_basis_sha256)
        if entity_id is not None and entity_id not in state["entities"]:
            raise OperationConflict("Publication use is unregistered")
        design = self._latest_cycle(state, design_binding, "design", entity_id)
        publication = self._latest_cycle(state, binding, "publication", entity_id)

        design_status = self._review_stage(design)
        if any(getattr(binding, key) != getattr(design_binding, key) for key in (
                "facts_sha256", "inputs_sha256", "exposure_sha256", "design_sha256", "procedure_sha256", "policy_sha256")):
            design_status = "stale"
        numerical = "current" if design_status == "current" else design_status
        evidence_first = self._review_stage(
            publication, "evidence_first_assessment_sha256",
        )
        narrative = self._review_stage(publication)
        affected = [hold for hold in state["holds"].values() if hold["status"] == "open"
                    and (entity_id is None or entity_id in hold["scope"])]
        pending = [item for item in state["obligations"].get(binding.sha256(), {}).values()
                   if not item["satisfied"] and (entity_id is None or entity_id in self._scope(state, (item["entity_id"],)))]
        if design_binding != binding:
            pending.extend(item for item in state["obligations"].get(design_binding.sha256(), {}).values()
                if not item["satisfied"] and (entity_id is None or entity_id in self._scope(state, (item["entity_id"],))))
        if any(any(state["entities"][key]["kind"] == "fit" for key in hold["objection"]["affected_entity_ids"])
               for hold in affected) or any(item["stage"] == "pre_fit" for item in pending):
            design_status = numerical = "held"
        if any(any(state["entities"][key]["kind"] == "table" for key in hold["objection"]["affected_entity_ids"])
               for hold in affected) or any(item["stage"] == "post_fit" and state["entities"][item["entity_id"]]["kind"] != "claim"
                                          for item in pending):
            numerical = "held"
        if affected or pending or entity_id in state["withdrawn"]:
            evidence_first = narrative = "held"
        if entity_id in state["withdrawn"] and state["entities"][entity_id]["kind"] != "claim":
            numerical = "held"
        return ReviewEligibility(basis_sha256=publication_basis_sha256, design=design_status, numerical=numerical,
                                 evidence_first=evidence_first, narrative=narrative)

    @staticmethod
    def _review_stage(cycle, field="phase"):
        if cycle is None or cycle["phase"] in {"failed", "unavailable"}:
            return "unavailable"
        current = cycle["phase"] == "completed" if field == "phase" else cycle[field]
        return "current" if current else "pending"

    def _usage_updates(self, state, cycle_id, amounts, purpose):
        if purpose not in {"review", "resolution"} or any(type(value) is not int or value < 0 for value in amounts.values()):
            raise ValueError("Review usage requires nonnegative integer counters and a registered purpose")
        cycle = self._cycle(state, cycle_id)
        if any(item["status"] == "overrun" for item in state["reservations"].values()):
            raise OperationBlocked("review_token_reservation_overrun")
        totals = {key: state["accounting"][key] + value for key, value in amounts.items()}
        for key, value in totals.items():
            reserve = getattr(self.budget, "resolution_" + key, 0) if purpose == "review" else 0
            if value > getattr(self.budget, key) - reserve:
                raise OperationBlocked("review_" + key + "_budget_exhausted")
        cycle["usage"] = {key: cycle["usage"][key] + value for key, value in amounts.items()}
        return {"cycles": {cycle_id: cycle}, "accounting": totals}, totals

    def record_usage(self, cycle_id, *, invocations=0, turns=0, queries=0, tokens=0, purpose="review"):
        amounts = dict(invocations=invocations, turns=turns, queries=queries, tokens=tokens)

        def build(state):
            updates, totals = self._usage_updates(state, cycle_id, amounts, purpose)
            return updates, {"cycle_id": cycle_id, "purpose": purpose, "amounts": amounts}, totals

        return self._write("record_usage", build)

    def reserve_invocation(self, cycle_id, *, maximum_tokens, purpose="review"):
        if type(maximum_tokens) is not int or maximum_tokens < 1:
            raise ValueError("Invocation reservation requires a positive token bound")
        reservation_id = "review-reservation-" + uuid.uuid4().hex

        def build(state):
            updates, _ = self._usage_updates(state, cycle_id,
                dict(invocations=0, turns=0, queries=0, tokens=maximum_tokens), purpose)
            record = {"reservation_id": reservation_id, "cycle_id": cycle_id, "maximum_tokens": maximum_tokens,
                      "observed_tokens": None, "status": "reserved"}
            updates["reservations"] = {reservation_id: record}
            return updates, record, reservation_id

        return self._write("reserve_invocation", build)

    def settle_invocation(self, cycle_id, reservation_id, *, tokens=None):
        if tokens is not None and (type(tokens) is not int or tokens < 0):
            raise ValueError("Observed tokens must be a nonnegative integer or unknown")

        def build(state):
            record = deepcopy(state["reservations"].get(reservation_id))
            if record is None or record["cycle_id"] != cycle_id:
                raise OperationConflict("Token reservation belongs to another cycle or is absent")
            if record["status"] != "reserved":
                raise OperationConflict("Token reservation is already settled")
            excess = max(0, (tokens or 0) - record["maximum_tokens"])
            record.update(observed_tokens=tokens, status="overrun" if excess else "unknown" if tokens is None else "settled")
            updates = {"reservations": {reservation_id: record}}
            if excess:
                cycle = self._cycle(state, cycle_id)
                cycle["usage"]["tokens"] += excess
                updates.update(cycles={cycle_id: cycle}, accounting={"tokens": state["accounting"]["tokens"] + excess})
            return updates, record, record

        return self._write("settle_invocation", build)
