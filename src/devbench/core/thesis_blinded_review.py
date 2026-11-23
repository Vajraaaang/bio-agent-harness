"""Typed evidence subjects and assessments for blinded thesis review."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable
from typing import Annotated, Literal, Self

from pydantic import Field, StringConstraints, model_validator

from devbench.adaptive.models import AdaptiveRecord

from ._hashing import canonical_sha256
from .scientific_types import Sha256

_Principal = Annotated[str, Field(pattern=r"^[A-Za-z0-9_.:-]{1,128}$")]
_Rationale = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=512)
]
_Dimension = Literal[
    "units", "design", "provenance", "claim_scope", "alternatives", "follow_up"
]
_Rating = Literal["supports", "needs_evidence", "veto"]
_DIMS = {"units", "design", "provenance", "claim_scope", "alternatives", "follow_up"}
PanelStatus = Literal[
    "awaiting_independent_reviews", "passed", "needs_evidence",
    "unresolved_disagreement", "vetoed",
]


class BlindedAimSubject(AdaptiveRecord):
    """Bind a masked packet to an exact approved aim and proposed conclusion."""

    format: Literal["devbench.blinded-aim-subject/1"] = "devbench.blinded-aim-subject/1"
    aim_id: _Principal
    aim_version: int = Field(gt=0, strict=True)
    approved_aim_sha256: Sha256
    outcome_sha256: Sha256
    masked_packet_sha256: Sha256
    reviewers: tuple[_Principal, ...] = Field(min_length=2, max_length=8)

    @model_validator(mode="after")
    def distinct_reviewers(self) -> Self:
        if len({actor.casefold() for actor in self.reviewers}) != len(self.reviewers):
            raise ValueError("Blinded review requires two or more distinct reviewers")
        return self

    def sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json"))


class BlindedAimAssessment(AdaptiveRecord):
    """An exact assessment delivered through the host review verifier."""

    format: Literal["devbench.blinded-aim-assessment/1"] = (
        "devbench.blinded-aim-assessment/1"
    )
    reviewer: _Principal
    subject_sha256: Sha256
    receipt_sha256: Sha256
    ratings: dict[_Dimension, _Rating]
    rationale: _Rationale
    veto_codes: tuple[_Principal, ...] = Field(default=(), max_length=8)

    @model_validator(mode="after")
    def complete_assessment(self) -> Self:
        if self.ratings.keys() != _DIMS:
            raise ValueError("Blinded review requires a rating for every dimension")
        if bool(self.veto_codes) != (self.disposition() == "veto"):
            raise ValueError("Blinded veto ratings require explicit veto codes")
        if len(set(self.veto_codes)) != len(self.veto_codes):
            raise ValueError("Blinded review veto codes must be distinct")
        return self

    def disposition(self) -> _Rating:
        values = set(self.ratings.values())
        if "veto" in values:
            return "veto"
        return "needs_evidence" if "needs_evidence" in values else "supports"


BlindedReviewVerifier = Callable[[BlindedAimSubject, BlindedAimAssessment], bool]


def panel_projection(
    subject: BlindedAimSubject, assessments: tuple[BlindedAimAssessment, ...],
) -> dict:
    dispositions = {assessment.disposition() for assessment in assessments}
    has_quorum = len(assessments) == len(subject.reviewers)
    status: PanelStatus
    if "veto" in dispositions:
        status = "vetoed"
    elif not has_quorum:
        status = "awaiting_independent_reviews"
    elif len(dispositions) > 1:
        status = "unresolved_disagreement"
    else:
        status = "passed" if dispositions == {"supports"} else "needs_evidence"
    codes = Counter(code for item in assessments for code in item.veto_codes)
    return {
        "subject_sha256": subject.sha256(),
        "masked_packet_sha256": subject.masked_packet_sha256,
        "assigned_reviewers": len(subject.reviewers),
        "submitted_reviews": len(assessments),
        "receipt_sha256s": [assessment.receipt_sha256 for assessment in assessments],
        "status": status,
        "unresolved_disagreement": len(dispositions) > 1,
        "adjudicated": has_quorum and status in {"passed", "vetoed"},
        "veto_codes": dict(codes),
    }


def review_metrics(panels: Iterable[dict]) -> dict:
    records = list(panels)
    adjudicated = sum(item["adjudicated"] for item in records)
    passed = sum(item["status"] == "passed" for item in records)
    codes: Counter[str] = Counter()
    for item in records:
        codes.update(item["veto_codes"])
    return {
        "panels": len(records), "adjudicated": adjudicated, "passed": passed,
        "adjudicated_pass_rate": passed / adjudicated if adjudicated else None,
        "stale_panels": sum(item["status"] == "stale_evidence" for item in records),
        "needs_evidence_panels": sum(
            item["status"] == "needs_evidence" for item in records
        ),
        "unresolved_disagreements": sum(
            item["unresolved_disagreement"] and item["status"] != "stale_evidence"
            for item in records
        ),
        "veto_codes": dict(codes),
    }
