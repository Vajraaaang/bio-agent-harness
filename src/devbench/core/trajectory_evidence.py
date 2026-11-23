"""Prospective evidence for a candidate donor-aware pseudotime analysis."""

from typing import Annotated, Literal, Self

from pydantic import StringConstraints, model_validator

from ._hashing import canonical_sha256
from .scientific_types import FrozenContract, Identifier, Sha256


class DonorTrajectoryEvidence(FrozenContract):
    """Exact-subject evidence that the host must verify before dispatch."""

    format: Literal["devbench.donor-trajectory-evidence/1"] = (
        "devbench.donor-trajectory-evidence/1"
    )
    specification_sha256: Sha256
    normalized_expression_sha256: Sha256
    coordinates_sha256: Sha256
    original_counts_sha256: Sha256
    normalization_receipt_sha256: Sha256
    axis_receipt_sha256: Sha256
    design_receipt_sha256: Sha256
    feature_axis_sha256: Sha256
    independence_basis: Literal["orthogonal_assay", "independent_holdout"]
    independence_receipt_sha256: Sha256
    review_receipt_sha256: Sha256
    reviewer: Identifier
    rationale: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1024)
    ]

    @model_validator(mode="after")
    def distinct_evidence(self) -> Self:
        receipts = {
            self.normalization_receipt_sha256,
            self.axis_receipt_sha256,
            self.design_receipt_sha256,
            self.independence_receipt_sha256,
            self.review_receipt_sha256,
        }
        if (
            len(receipts) != 5
            or self.normalized_expression_sha256 == self.original_counts_sha256
        ):
            raise ValueError("Trajectory scientific receipts must be distinct")
        return self

    def sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json"))

    def hypotheses_sha256(self) -> str:
        """Identify the single frozen BH family, including its exact feature axis."""
        return canonical_sha256({
            "format": "devbench.donor-trajectory-hypotheses/1",
            "adjustment": "BH",
            "specification_sha256": self.specification_sha256,
            "feature_axis_sha256": self.feature_axis_sha256,
        })
