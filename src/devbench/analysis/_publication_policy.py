"""Publication descriptors, evidence admission and public metadata policy."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from hashlib import sha256
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from devbench.core._hashing import canonical_sha256 as _digest
from devbench.core.procedure_profile import (
    ADMITTED_RESEARCH,
    ClaimKind,
    EvidenceDigest,
    ProcedureProfile,
)
from devbench.core.publication_families import FamilyAllocation

from .scientific_review_holds import Identity, ReviewBinding


class _Frozen(BaseModel):
    model_config = ConfigDict(
        frozen=True, extra="forbid", strict=True, revalidate_instances="always"
    )


class PublicationAccess(_Frozen):
    source: bool = True
    checked: bool = True
    outcomes: bool = False
    statistics: bool = False
    exposure: Literal["structural", "diagnostic", "target_outcome", "unknown"] = (
        "structural"
    )
    policy_sha256: EvidenceDigest | None = None
    exposure_sha256: EvidenceDigest | None = None


ReviewState = Literal["unavailable", "pending", "current", "held", "stale"]


class ReviewEligibility(_Frozen):
    """T17 supplies current decisions from its evidence-first and narrative stages."""

    basis_sha256: EvidenceDigest | None = None
    design: ReviewState = "unavailable"
    numerical: ReviewState = "unavailable"
    evidence_first: ReviewState = "unavailable"
    narrative: ReviewState = "unavailable"


class PublicationDescriptor(_Frozen):
    format: Literal["devbench.scientific-publication/1"] = (
        "devbench.scientific-publication/1"
    )
    scope: Literal["fixed_capsule", "adaptive"]
    source_sha256: EvidenceDigest
    allocation_sha256: EvidenceDigest | None
    profile_sha256: EvidenceDigest
    basis_sha256: EvidenceDigest
    allowed_claims: tuple[ClaimKind, ...]
    review: ReviewEligibility
    dependencies_current: bool
    claim_current: bool
    reasons: tuple[str, ...]
    family_review_holds: tuple[str, ...] = ()
    subject_id: EvidenceDigest | None = Field(default=None, exclude=True)

    @property
    def allow_statistics(self):
        return "qualified_procedure_result" in self.allowed_claims

    @property
    def allow_descriptive(self):
        return self.allow_statistics or "exploratory_finding" in self.allowed_claims

    @property
    def allow_checked(self):
        return "checked_observation" in self.allowed_claims

    @property
    def allow_interpretation(self):
        return (
            self.allow_descriptive
            and self.claim_current
            and self.review.basis_sha256 == self.basis_sha256
            and self.review.evidence_first == self.review.narrative == "current"
        )


def build_publication_descriptor(
    profile,
    *,
    source_sha256,
    allocation=None,
    access=None,
    review=None,
    scope="fixed_capsule",
    dependencies_current=True,
    claim_current=True,
    dependency_sha256=(),
    exploratory=False,
    selected=False,
    embargo=False,
    family_review_holds=(),
):
    """Intersect admitted procedure claims with current, coordinator-owned evidence."""
    profile = ProcedureProfile.model_validate(profile)
    access = (
        PublicationAccess.model_validate(access)
        if access is not None
        else PublicationAccess(source=False)
    )
    review = (
        ReviewEligibility.model_validate(review)
        if review is not None
        else ReviewEligibility()
    )
    allocation_sha256 = (
        FamilyAllocation.model_validate(allocation).sha256()
        if allocation is not None
        else None
    )
    family_review_holds = tuple(sorted(set(family_review_holds)))
    if family_review_holds and (
        allocation is None
        or set(family_review_holds)
        - {
            item.family_id
            for item in FamilyAllocation.model_validate(allocation).families
        }
    ):
        raise ValueError("Review-held families must belong to the exact allocation")
    profile_sha256 = _digest(profile.model_dump(mode="json"))
    basis = _digest(
        {
            "source_sha256": source_sha256,
            "allocation_sha256": allocation_sha256,
            "profile_sha256": profile_sha256,
            "access": access.model_dump(mode="json"),
            "dependencies": list(dependency_sha256),
            "scope": scope,
            "exploratory": exploratory,
            "selected": selected,
            **(
                {"family_review_holds": list(family_review_holds)}
                if family_review_holds
                else {}
            ),
        }
    )
    available = access.source and dependencies_current and not embargo
    permit = set(profile.permitted_claims) if available else set()
    reasons = []
    if ADMITTED_RESEARCH in permit:
        permit.remove(ADMITTED_RESEARCH)
        reasons.append("research_inference_requires_current_scoped_delivery")
    if not available:
        reasons.append("publication_source_withheld")
    if not access.checked:
        permit.discard("checked_observation")
    if not access.outcomes:
        permit.discard("exploratory_finding")
    if (
        "qualified_procedure_result" not in permit
        or allocation is None
        or exploratory
        or selected
        or not access.outcomes
        or not access.statistics
        or access.exposure not in {"structural", "diagnostic"}
        or review.basis_sha256 != basis
        or review.design != "current"
        or review.numerical != "current"
    ):
        permit.discard("qualified_procedure_result")
        reasons.append("qualified_result_unavailable")
    if (
        not claim_current
        or review.basis_sha256 != basis
        or review.evidence_first != "current"
        or review.narrative != "current"
    ):
        reasons.append("interpretation_review_unavailable")
    return PublicationDescriptor(
        scope=scope,
        source_sha256=source_sha256,
        allocation_sha256=allocation_sha256,
        profile_sha256=profile_sha256,
        basis_sha256=basis,
        allowed_claims=tuple(sorted(permit)),
        review=review,
        dependencies_current=dependencies_current,
        claim_current=claim_current,
        reasons=tuple(reasons),
        family_review_holds=family_review_holds,
    )


def access_from_delivery(delivery, source_sha256, *, connection=None):
    """Use the same principal lineage, scope and possible exposure as the T12 broker."""
    from .evidence_access import AccessDenied, EvidenceDelivery, _max_class

    if type(delivery) is not EvidenceDelivery:
        return PublicationAccess(source=False)
    broker = delivery.broker
    if connection is None:
        with broker.authority.ledger._database() as db:
            db.execute("BEGIN")
            return access_from_delivery(delivery, source_sha256, connection=db)
    _same_transaction(connection, broker.authority.ledger)
    try:
        if connection is not None:
            db = connection
            context, policy = broker._context_policy(db, delivery.context_id)
            source = broker._source(db, delivery.source_id)
            if source["content_sha256"] != source_sha256:
                raise AccessDenied("Publication source differs from registered content")
            prior = broker._prior(db, context, source)
            tags = set(source["tags"]).union(*(item["scope"]["tags"] for item in prior))
            classification = _max_class(
                source["classification"], *(item["classification"] for item in prior)
            )
            broker._check(
                context,
                policy,
                {**source, "tags": sorted(tags), "classification": classification},
                set(),
            )
            exposure = _max_class(
                "structural", *(item["classification"] for item in prior)
            )
            return PublicationAccess(
                source=True,
                checked=not policy.allowed_fields,
                outcomes=policy.allow_outcomes and not policy.allowed_fields,
                statistics=policy.allow_statistics and not policy.allowed_fields,
                exposure=exposure,
                policy_sha256=policy.sha256(),
                exposure_sha256=_digest(
                    [[item["reservation_id"], item["state"]] for item in prior]
                ),
            )
    except AccessDenied:
        return PublicationAccess(source=False)


def _same_transaction(connection, ledger):
    from devbench.engine.scientific_authority import require_same_ledger_transaction

    require_same_ledger_transaction(connection, ledger)


class PublicationSubject(_Frozen):
    """Immutable coordinator join; a subject is never an admission decision."""

    format: Literal["devbench.publication-subject/1"] = "devbench.publication-subject/1"
    subject_id: EvidenceDigest
    lineage_id: Identity
    entity_id: Identity
    entity_kind: Literal["table", "claim"]
    logical_key: str
    revision: int = Field(ge=1)
    prior_subject_id: EvidenceDigest | None
    step_id: Identity
    generation: int = Field(ge=1)
    port: Identity
    artifact_version_id: Identity
    source_sha256: EvidenceDigest
    source_path: str
    source_schema: Literal[
        "scientific_result", "saved_bulk_result", "checked_observation"
    ]
    attempt_id: Identity
    accepted_record_id: Identity
    accepted_record_sha256: EvidenceDigest
    binding_id: Identity
    scientific_binding_sha256: EvidenceDigest
    envelope_sha256: EvidenceDigest
    profile: ProcedureProfile
    allocation: FamilyAllocation | None
    frozen_exposure: PublicationAccess
    decision_exposure: dict
    publication_binding: ReviewBinding
    design_binding: ReviewBinding
    packet_path: str | None
    packet_sha256: EvidenceDigest | None
    claim: dict | None
    check_id: Literal["exact_sum", "coverage", "mathematical_graph_topology"] | None


def adaptive_publication_descriptor(
    workflow,
    step_id,
    port,
    *,
    generation,
    expected_sha256,
    delivery,
    allocation=None,
    review=None,
):
    """Compatibility lookup; only the authoritative subject can join reviews."""

    from devbench.platform.adaptive_workflow import AdaptiveWorkflow

    from ._publication_registry import PublicationRegistry

    if type(workflow) is not AdaptiveWorkflow or review is not None:
        raise TypeError(
            "Adaptive publication requires its authoritative publication subject"
        )
    registry = PublicationRegistry(workflow)
    with workflow.ledger._database() as db:
        db.execute("BEGIN")
        state = workflow._state(db)
        node = state.nodes[step_id]
        try:
            subject = registry.subject_for_source(expected_sha256, connection=db)
            if subject is not None and (
                subject.step_id,
                subject.port,
                subject.generation,
            ) == (step_id, port, generation):
                if allocation is not None and allocation != subject.allocation:
                    raise TypeError(
                        "Adaptive publication cannot replace its registered allocation"
                    )
                return registry.descriptor(
                    subject.subject_id, delivery=delivery, connection=db
                )
        except PermissionError:
            pass
    return build_publication_descriptor(
        node.profile,
        source_sha256=expected_sha256,
        access=PublicationAccess(source=False),
        scope="adaptive",
        dependencies_current=False,
        claim_current=False,
        exploratory=True,
    )


class CheckedObservation(_Frozen):
    check_id: Literal["exact_sum", "coverage", "mathematical_graph_topology"]
    source_sha256: EvidenceDigest
    value: int | dict[str, int]

    @classmethod
    def compute(cls, check_id, source):
        if type(source) is not dict:
            raise ValueError("A registered observation needs closed source fields")
        if check_id == "exact_sum" and source.keys() == {"values"}:
            values = source["values"]
            if (
                type(values) is not list
                or len(values) > 100_000
                or any(type(v) is not int or abs(v) >= 2**53 for v in values)
            ):
                raise ValueError("Exact sum requires bounded integers")
            result = sum(values)
        elif check_id == "coverage" and source.keys() == {"retained"}:
            flags = source["retained"]
            if (
                type(flags) is not list
                or len(flags) > 100_000
                or any(type(flag) is not bool for flag in flags)
            ):
                raise ValueError("Coverage requires a bounded boolean mask")
            result = {"retained": sum(flags), "total": len(flags)}
        elif check_id == "mathematical_graph_topology" and source.keys() == {
            "node_count",
            "edges",
        }:
            size, edges = source["node_count"], source["edges"]
            if (
                type(size) is not int
                or not 0 <= size <= 100_000
                or type(edges) is not list
                or len(edges) > 100_000
                or any(
                    type(edge) is not list
                    or len(edge) != 2
                    or any(type(i) is not int or not 0 <= i < size for i in edge)
                    for edge in edges
                )
            ):
                raise ValueError(
                    "Graph observation requires a bounded indexed edge list"
                )
            pairs = {tuple(edge) for edge in edges}
            result = {
                "nodes": size,
                "directed_edges": len(pairs),
                "self_loops": sum(a == b for a, b in pairs),
                "isolated_nodes": size - len({i for edge in pairs for i in edge}),
            }
        else:
            raise ValueError("Unregistered observation check or source fields")
        return cls(check_id=check_id, source_sha256=_digest(source), value=result)

    def public(self, descriptor, *, source):
        if (
            not descriptor.allow_checked
            or self.source_sha256 != descriptor.source_sha256
            or self != self.compute(self.check_id, source)
        ):
            raise PermissionError("checked_observation_not_admitted")
        return {"claim_kind": "checked_observation", **self.model_dump(mode="json")}


def _label(value):
    # Free prose and formula-like spreadsheet cells are outside the public label schema.
    return (
        value
        if (
            type(value) is str
            and re.fullmatch(r"[\w][\w.:-]{0,199}", value)
            and not is_statistic_field(value)
            and not re.match(r"(?i)^(?:p|q|fdr|padj)[.:-]\d", value)
        )
        else None
    )


_DIAGNOSTIC_CODES = frozenset(
    {
        "failed_eligible_test",
        "filter_retained_no_features",
        "result_unavailable",
        "execution_failed",
        "publication_source_withheld",
        "checked_observation_not_admitted",
        "bundle_root_mismatch",
        "artifact_digest_mismatch",
        "unavailable",
        "cancelled",
        "request_cancelled",
        "synthesis_requires_tool_receipts",
        "synthesis_computed_contrast_unavailable",
        "invalid_request",
        "publication_access_denied",
        "operation_unavailable",
        "sqlite_wal_upgrade_required",
        "model_selector_not_configured",
        "scientific_workspace_not_enabled",
        "data_execution_disabled",
        "scientific_workflow_not_configured",
        "adaptive_runtime_not_bound",
        "adaptive_input_version_invalidated",
        "adaptive_held_out_partition_check_required",
        "adaptive_use_invalidated",
        "adaptive_use_held_or_retired",
        "adaptive_claim_use_invalidated",
        "adaptive_protected_partition_delivery_denied",
        "scientific_epoch_revoked",
        "scientific_hold",
        "scientific_cancelled",
        "scientific_grant_expired",
        "scientific_budget_exhausted",
        "restricted_audit_entitlement_required",
        "workspace_operation_failed",
        "scientific_version_unavailable",
        "software_verification_required",
        "project_identity_changed",
        "execution_timeout",
        "evidence_query_rejected",
        "evidence_query_rejected:evidence_query_sample_axis_mismatch",
        "publication_statistics_withheld",
        "AccessDenied",
        "KeyError",
        "ValueError",
        "PermissionError",
        "RuntimeError",
        "OSError",
        "InterruptedError",
        "infrastructure_failure",
        "execution_failure_unclassified",
        "validation_evidence_withheld",
        "publication_subject_required",
        "publication_subject_superseded",
        "publication_changed_before_release",
        "publication_accepted_output_mismatch",
        "publication_renderer_binding_mismatch",
        "publication_requires_bound_renderer",
        "publication_format_unavailable",
        "publication_source_schema_unavailable",
        "publication_plot_source_unavailable",
        "publication_authority_locator_unavailable",
    }
)
_EFFECTS = {
    "effect",
    "effectestimate",
    "logfc",
    "log2foldchange",
    "foldchange",
    "estimate",
    "descriptiveeffect",
}
_OPAQUE = {
    "payload",
    "stdout",
    "stderr",
    "sourcecode",
    "sourcepreview",
    "raw",
    "rawresult",
    "rawbytes",
    "rawaudit",
    "sourcetext",
    "rawcounts",
    "attachments",
    "messages",
    "prompt",
    "completion",
    "reasoning",
    "traceback",
    "authorization",
    "credential",
    "accesstoken",
    "secret",
    "apikey",
    "bindings",
    "queryreceipts",
    "queryreceipt",
    "validationplan",
    "controllernarrative",
    "publicationauthorities",
}
_CLAIMS = {
    "finding",
    "findings",
    "analystfindings",
    "interpretation",
    "biologicalrationale",
    "narrative",
    "rationale",
    "claimtext",
    "claim",
    "claims",
    "conclusion",
    "conclusions",
    "caption",
    "tooltip",
    "presentationassertions",
    "judgment",
    "verdict",
    "narrativereview",
}
_ERROR_FIELDS = {"error", "exception", "failure", "errormessage", "details", "reason"}
_STAT_EXTRA = {
    "adjustedpvalue",
    "adjustedpvalues",
    "adjustedqvalue",
    "rawpvalue",
    "rawqvalue",
    "passesbudgetthreshold",
    "isde",
    "isdeg",
    "neglog10p",
    "neglog10q",
    "neglog10fdr",
    "neglog10padj",
    "zscore",
    "tstatistic",
    "standarderror",
    "confidenceinterval",
    "criticalvalue",
    "localalpha",
    "discovery",
    "discoveries",
    "nrejected",
    "reject",
    "tophits",
    "bhadjusted",
    "effectrank",
    "sortby",
    "rankby",
}
_SHA = re.compile(r"[0-9a-f]{64}")


def safe_diagnostic(error):
    """Preserve registered codes without relaying exception arguments."""
    from devbench.engine.workflow_operations import OperationBlocked

    code = error.state if isinstance(error, OperationBlocked) else error
    if (
        isinstance(error, (PermissionError, ValueError))
        and len(error.args) == 1
        and type(error.args[0]) is str
    ):
        if error.args[0] in _DIAGNOSTIC_CODES:
            return error.args[0]
    if type(code) is str and code in _DIAGNOSTIC_CODES:
        return code
    if isinstance(error, TimeoutError):
        return "execution_timeout"
    if isinstance(error, PermissionError):
        return "publication_access_denied"
    if isinstance(error, (ValueError, TypeError)):
        return "invalid_request"
    if isinstance(error, KeyError):
        return "unavailable"
    return "operation_unavailable"


def _key(key):
    return re.sub(r"[^a-z0-9]", "", key.casefold())


def is_statistic_field(key):
    from .evidence_access import _NORMAL_STATS

    name = _key(key)
    return (
        name in _NORMAL_STATS
        or name in _STAT_EXTRA
        or any(
            part in name
            for part in (
                "pvalue",
                "qvalue",
                "qlocal",
                "qbudget",
                "padj",
                "significan",
                "fdr",
            )
        )
    )


def _project_metadata_mapping(item, visit, depth, *, statistics, claims, outcomes):
    projected = {}
    for key, child in item.items():
        if type(key) is not str:
            continue
        name = _key(key)
        if (
            name in _OPAQUE
            or any(token in name for token in ("credential", "accesstoken", "apikey"))
            or not statistics
            and is_statistic_field(key)
            or not claims
            and name in _CLAIMS
            or not outcomes
            and name in _EFFECTS
            or (
                name in {"allowstatistics", "qualifiedprocedureresult"}
                and not statistics
            )
            or name.endswith(("base64", "encodedbytes"))
        ):
            continue
        if name in _ERROR_FIELDS and child is not None:
            if name == "error" and type(child) is dict:
                projected[key] = {
                    field: safe_diagnostic(child[field])
                    for field in ("type", "message", "code")
                    if field in child
                }
            else:
                projected[key] = safe_diagnostic(child)
        elif name in {"claimkind", "claimtype"} and child == ADMITTED_RESEARCH:
            projected[key] = "withheld"
        elif (
            name in {"claimkind", "claimtype"}
            and child == "qualified_procedure_result"
            and not statistics
        ):
            projected[key] = "exploratory_finding" if outcomes else "withheld"
        else:
            projected[key] = visit(child, depth + 1)
    return projected


def public_metadata(value, *, descriptor=None, blocked_hashes=()):
    """Project legacy metadata only. Numeric and figure outputs have typed exporters."""
    statistics = descriptor is not None and descriptor.allow_statistics
    # Reviewed exploratory narratives have a separate exact packet projection.
    # A permissive descriptor must not make arbitrary legacy prose reviewable.
    claims = (
        descriptor is not None
        and descriptor.allow_statistics
        and descriptor.allow_interpretation
    )
    outcomes = descriptor is None or descriptor.allow_descriptive
    blocked = frozenset(blocked_hashes)

    def visit(item, depth):
        if depth > 32:
            return None
        if isinstance(item, dict):
            if blocked and any(
                type(child) is str and child in blocked for child in item.values()
            ):
                return {"status": "withheld", "reason": "publication_source_withheld"}
            return _project_metadata_mapping(
                item,
                visit,
                depth,
                statistics=statistics,
                claims=claims,
                outcomes=outcomes,
            )
        if isinstance(item, (list, tuple)):
            return [visit(child, depth + 1) for child in item]
        if type(item) is str:
            return (
                None if item in blocked or item.startswith(("data:", "file:")) else item
            )
        return (
            item
            if type(item) in (int, bool, type(None))
            or type(item) is float
            and math.isfinite(item)
            else None
        )

    return visit(value, 0)


@dataclass(frozen=True)
class AuditReplayEntitlement:
    """Exact retained audit source under a current T12 auditor grant."""

    delivery: object
    content_sha256: str

    def _guard(self, db):
        from .evidence_access import AccessDenied, EvidenceDelivery

        delivery = self.delivery
        if type(delivery) is not EvidenceDelivery:
            raise AccessDenied(
                "Restricted replay requires an authority-issued delivery"
            )
        broker = delivery.broker
        context, policy = broker._context_policy(db, delivery.context_id)
        source = broker._source(db, delivery.source_id)
        tags = set(source["tags"])
        if (
            context["role"] != "auditor"
            or context["phase"] != "readback"
            or source["content_sha256"] != self.content_sha256
            or "audit_replay" not in tags
            or "original_source_data" in tags
        ):
            raise AccessDenied("Exact restricted audit/replay entitlement required")
        broker._check(context, policy, source, None)

    def require(self, content_sha256):
        if content_sha256 != self.content_sha256 or not _SHA.fullmatch(content_sha256):
            raise PermissionError(
                "Restricted replay source differs from its exact entitlement"
            )
        from .evidence_access import EvidenceDelivery

        if type(self.delivery) is not EvidenceDelivery:
            raise PermissionError(
                "Restricted replay requires an authority-issued delivery"
            )
        with self.delivery.broker.authority.ledger._database() as db:
            db.execute("BEGIN")
            self._guard(db)

    def release(self, raw):
        if type(raw) is not bytes:
            raise TypeError("Restricted replay requires exact bytes")
        self.require(sha256(raw).hexdigest())
        broker = self.delivery.broker
        reservation = broker.reserve(
            self.delivery.context_id,
            self.delivery.source_id,
            raw,
            opaque=True,
            _guard=self._guard,
        )
        broker.mark_attempted(reservation["reservation_id"], _guard=self._guard)
        return raw


def audit_replay_entitlement(delivery, content_sha256):
    result = AuditReplayEntitlement(delivery, content_sha256)
    result.require(content_sha256)
    return result
