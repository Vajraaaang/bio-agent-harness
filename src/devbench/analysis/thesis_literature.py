"""Project scoped literature comparisons from retained query and review evidence."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime
from sqlite3 import Connection
from typing import TYPE_CHECKING, Annotated, Any, Literal

from pydantic import Field, model_validator

from devbench.adaptive.models import AdaptiveRecord
from devbench.engine.workflow_operations import OperationBlocked
from devbench.platform.adaptive_workflow_state import Digest, Identity

from .checkpoints import digest
from .evidence_access import AccessDenied, EvidenceBroker
from .literature_adapter import (
    _fence,
    _notice_updates,
    _require_snapshot_current,
    _retained_snapshot,
    _status_view,
    literature_use_current,
)
from .literature_adapter import _state as _literature_state
from .scientific_result_contract import ClaimSpec
from .scientific_review_holds import ReviewHoldLifecycle

if TYPE_CHECKING:
    from devbench.platform.adaptive_workflow import AdaptiveWorkflow

Note = Annotated[str, Field(min_length=1, max_length=2000)]
_Document = dict[str, Any]


class LiteratureQueryReceipt(AdaptiveRecord):
    """The exact envelope receipt returned to the query's entitled context."""

    format: Literal["devbench.literature-query-receipt/1"]
    disclosure_reservation_id: Identity
    query_sha256: Digest
    snapshot_id: Digest
    policy_sha256: Digest
    claim_binding_sha256: Digest
    content_identity_sha256: Digest


class LiteratureQueryReference(AdaptiveRecord):
    operation_id: Identity
    receipt: LiteratureQueryReceipt


class LiteratureComparisonPlan(AdaptiveRecord):
    """Comparison criteria and the complete selected corpus for one exact claim."""

    claim: ClaimSpec
    scope_description: Note
    selection_rule: Note
    comparison_dimensions: tuple[Note, ...] = Field(min_length=1, max_length=12)
    corpus: tuple[LiteratureQueryReference, ...] = Field(min_length=1, max_length=32)

    @model_validator(mode="after")
    def unique_references(self) -> LiteratureComparisonPlan:
        for identities in (
            self.comparison_dimensions,
            tuple(item.operation_id for item in self.corpus),
            tuple(item.receipt.snapshot_id for item in self.corpus),
        ):
            if len(identities) != len(set(identities)):
                raise ValueError(
                    "Literature comparison requires distinct corpus references"
                )
        return self

    def sha256(self) -> str:
        """Digest recorded by the coordinator as a review basis before synthesis."""
        return digest(self.model_dump(mode="json"))


class ThesisLiteratureComparison(AdaptiveRecord):
    plan: LiteratureComparisonPlan
    plan_basis_id: Identity
    review_binding_sha256: Digest
    assessment_id: Digest


def _retained_operation(
    state: _Document,
    reference: LiteratureQueryReference,
    binding: _Document,
) -> _Document:
    operation = state["operations"].get(reference.operation_id)
    if (
        operation is None
        or operation["status"] != "completed"
        or operation["spec"]["claim_binding"] != binding
    ):
        raise OperationBlocked("literature_query_operation_unavailable")
    result = operation["result"]
    receipt = reference.receipt
    snapshot = state["snapshots"].get(receipt.snapshot_id)
    if (
        result is None
        or result["status"] not in {"available", "truncated"}
        or result["snapshot_id"] != receipt.snapshot_id
        or snapshot is None
        or receipt.query_sha256 != digest(operation["spec"]["request"])
        or receipt.claim_binding_sha256 != digest(binding)
        or receipt.content_identity_sha256 != snapshot["content_identity_sha256"]
    ):
        raise OperationBlocked("literature_query_receipt_unverified")
    return operation


def _verify_disclosure(
    connection: Connection,
    reference: LiteratureQueryReference,
    operation: _Document,
    binding: _Document,
) -> None:
    receipt = reference.receipt
    reservation = EvidenceBroker._get(
        connection,
        "reservation",
        receipt.disclosure_reservation_id,
    )
    source_id = "literature-source-" + digest([receipt.snapshot_id, binding])
    query_page = reservation.get("query_page") or {}
    exact_page = {
        **operation["result"],
        "query_receipt": receipt.model_dump(mode="json"),
    }
    if (
        reservation["state"] not in {"delivery_attempted", "receipt_confirmed"}
        or reservation["source_id"] != source_id
        or reservation["context_id"] != operation["spec"]["context_id"]
        or reservation["policy_sha256"] != receipt.policy_sha256
        or query_page.get("query_sha256") != receipt.query_sha256
        or reservation["content_sha256"] != digest(exact_page)
    ):
        raise OperationBlocked("literature_query_receipt_unverified")


def _publication_view(
    state: _Document,
    ref: _Document,
    snapshot: _Document,
    assessment: _Document,
) -> _Document | None:
    status = _status_view(snapshot["extraction"], snapshot["metadata"])
    if status is None:
        return None
    if status["retraction"] == "retracted" or any(
        state["retracted"].get(identity) for identity in ref["identifiers"]
    ):
        raise OperationBlocked("literature_corpus_source_retracted")
    notices = {key: state["notices"].get(key, []) for key in ref["identifiers"]}
    if ref["sha256"] != assessment["assessment"]["snapshot_id"]:
        _, visible = _notice_updates({"notices": {}}, status)
        if any(
            set(names) - set(visible.get(key, [])) for key, names in notices.items()
        ):
            raise OperationBlocked("literature_corpus_correction_requires_review")
    return {
        **{
            key: status.get(key)
            for key in (
                "identifiers",
                "title",
                "version",
                "retraction",
                "correction",
                "coverage",
            )
        },
        "observed_notices": status.get("notices", []),
        "notice_receipt_basis": notices,
    }


def _retrievals(snapshot: _Document) -> list[_Document]:
    responses = [
        {key: row[key] for key in ("content_sha256", "size_bytes", "retrieved_at")}
        for row in snapshot["responses"]
    ]
    if not responses or any(
        datetime.fromisoformat(row["retrieved_at"]).utcoffset() is None
        for row in responses
    ):
        raise OperationBlocked("literature_retrieval_timestamp_unverified")
    return responses


def _source_record(
    connection: Connection,
    state: _Document,
    reference: LiteratureQueryReference,
    assessment: _Document,
) -> _Document:
    binding = assessment["binding"]
    operation = _retained_operation(state, reference, binding)
    _verify_disclosure(connection, reference, operation, binding)
    ref = state["snapshots"][reference.receipt.snapshot_id]
    _require_snapshot_current(state, ref)
    grant = state["grant"]
    snapshot = _retained_snapshot(ref, grant["root"], grant["limits"]["response_bytes"])
    if snapshot["fence"] != assessment["fence"]:
        raise OperationBlocked("literature_corpus_execution_fence_stale")
    return {
        "operation_id": reference.operation_id,
        "query_receipt": reference.receipt.model_dump(mode="json"),
        "provider": snapshot["request"]["provider"],
        "operation": snapshot["request"]["operation"],
        "retrieval_status": snapshot["status"],
        "access": snapshot["access"],
        "publication": _publication_view(state, ref, snapshot, assessment),
        "search": snapshot["search"],
        "responses": _retrievals(snapshot),
    }


def _current_assessment(
    workflow: AdaptiveWorkflow,
    connection: Connection,
    state: _Document,
    request: ThesisLiteratureComparison,
) -> _Document:
    lifecycle = ReviewHoldLifecycle(workflow.authority, workflow.lineage_id)
    claim = request.plan.claim
    assessment = state["assessments"].get(request.assessment_id)
    if assessment is None or not literature_use_current(
        lifecycle,
        claim.claim_id,
        request.review_binding_sha256,
        connection=connection,
        assessment_id=request.assessment_id,
    ):
        raise OperationBlocked("literature_claim_assessment_unavailable_or_stale")
    binding = assessment["binding"]
    if (
        binding["claim_sha256"] != digest(claim.model_dump(mode="json"))
        or assessment["assessment"]["relation"] != "supports"
    ):
        raise OperationBlocked("literature_assessment_differs_from_claim")
    fence = _fence(workflow.authority, connection, workflow.lineage_id)
    if assessment["fence"] != fence or fence["cancelled"]:
        raise OperationBlocked("literature_assessment_execution_fence_stale")
    return assessment


def _claimed_comparison(
    workflow: AdaptiveWorkflow,
    connection: Connection,
    request: ThesisLiteratureComparison,
) -> _Document:
    state = _literature_state(connection, workflow.lineage_id)
    review = _current_assessment(workflow, connection, state, request)
    selected = {reference.receipt.snapshot_id for reference in request.plan.corpus}
    if selected != set(review["dependencies"]):
        raise OperationBlocked("literature_corpus_differs_from_assessed_dependencies")
    results = [
        _source_record(connection, state, reference, review)
        for reference in request.plan.corpus
    ]
    assessed = review["assessment"]
    return {
        "status": "supported_in_retrieved_corpus",
        "scope": "retrieved_material_at_sealing_revision",
        "plan_sha256": request.plan.sha256(),
        "plan": request.plan.model_dump(mode="json"),
        "assessment": {
            "assessment_id": request.assessment_id,
            "review_binding_sha256": request.review_binding_sha256,
            "claim_sha256": assessed["claim_sha256"],
            "evidence_sha256": assessed["evidence_sha256"],
            "snapshot_id": assessed["snapshot_id"],
            "passage_ids": assessed["passage_ids"],
            "reviewer_id": assessed["reviewer_id"],
            "relation": assessed["relation"],
            "alignment": review["alignment"],
            "limitations": review["limitations"],
            "semantic_entailment_verified": False,
        },
        "sources": results,
    }


def project_literature_comparison(
    workflow: AdaptiveWorkflow,
    connection: Connection,
    request: ThesisLiteratureComparison,
    *,
    basis: _Document | None,
) -> _Document:
    """Return a current corpus comparison or its fail-closed withholding reason."""
    identity = request.plan.sha256()
    try:
        if basis != {
            "basis_id": request.plan_basis_id,
            "kind": "review_basis",
            "sha256": identity,
        }:
            raise OperationBlocked("literature_comparison_plan_not_frozen")
        return _claimed_comparison(workflow, connection, request)
    except OperationBlocked as error:
        reason = error.state
    except AccessDenied:
        reason = "literature_query_receipt_unverified"
    except (ValueError, OSError):
        reason = "literature_source_custody_or_status_unverified"
    return {
        "status": "withheld",
        "reason": reason,
        "plan_sha256": identity,
        "claim_id": request.plan.claim.claim_id,
    }


def render_literature_comparison(record: _Document | None) -> Iterator[str]:
    """Render source facts and the attributed relation in its recorded scope."""
    if record is None:
        return
    from .episode_synthesis import _text

    yield ""
    yield "### Literature comparison"
    yield ""
    if record["status"] == "withheld":
        yield f"The literature comparison was withheld. {_text(record['reason'])}."
        return
    plan = record["plan"]
    yield "The supporting assessment applies to the retrieved corpus."
    yield f"Assessed question. {_text(plan['claim']['question'])}"
    yield f"Scope. {_text(plan['scope_description'])}"
    yield f"Selection rule. {_text(plan['selection_rule'])}"
    dimensions = ", ".join(_text(value) for value in plan["comparison_dimensions"])
    yield f"The frozen plan specified comparison along {dimensions}."
    assessment = record["assessment"]
    if "status_coverage_unknown" in assessment["limitations"]:
        yield "The provider reported limited publication notice coverage."
    for source in record["sources"]:
        publication = source["publication"]
        if publication is None:
            continue
        title = _text(publication["title"] or source["provider"])
        retrieved = _text(source["responses"][-1]["retrieved_at"])
        yield f"- {title} was retrieved at {retrieved}."
    yield "Novelty interpretation is limited to this assessed material."
