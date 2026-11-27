"""Scientific admission of reference labels and foundation-model comparisons."""

from collections.abc import Callable
from typing import Literal

from pydantic import Field, model_validator

from .scientific_types import FrozenContract, Identifier, Probability, Sha256

LabelProvenance = Literal[
    "orthogonal_protein_or_spatial", "frozen_external_reference", "internal_clustering"
]
FoundationModel = Literal["scGPT", "Geneformer", "scBERT"]
FOUNDATION_MODELS = frozenset({"scgpt", "geneformer", "scbert"})


class FoundationComparison(FrozenContract):
    """Frozen E4 comparison, resolved by the coordinator for this exact execution."""

    stage: Literal["E4"]
    axis: Literal["B2", "B7"]
    model: FoundationModel
    baseline: Literal["logistic_regression", "PCA", "scVI"]
    input_sha256: Sha256
    frozen_plan_sha256: Sha256
    model_execution_sha256: Sha256
    baseline_execution_sha256: Sha256
    evaluation_sha256: Sha256
    training_donors: tuple[Identifier, ...] = Field(min_length=1)
    evaluation_donors: tuple[Identifier, ...] = Field(min_length=1)
    metric: Identifier
    higher_is_better: bool
    model_score: float
    baseline_score: float
    outcome: Literal["improved", "tied", "worse"]

    @model_validator(mode="after")
    def consistent(self):
        train, test = set(self.training_donors), set(self.evaluation_donors)
        if (
            train & test
            or len(train) != len(self.training_donors)
            or len(test) != len(self.evaluation_donors)
        ):
            raise ValueError("distinct donor-disjoint partitions required")
        if (
            len(
                {
                    self.model_execution_sha256,
                    self.baseline_execution_sha256,
                    self.evaluation_sha256,
                    self.frozen_plan_sha256,
                }
            )
            != 4
        ):
            raise ValueError("distinct execution and evaluation receipts required")
        difference = (self.model_score - self.baseline_score) * (
            1 if self.higher_is_better else -1
        )
        if self.outcome != (
            "improved" if difference > 0 else "worse" if difference < 0 else "tied"
        ):
            raise ValueError("comparison outcome conflicts with executed scores")
        return self


def require_foundation_benchmark(
    method: str,
    *,
    axis: Literal["B2", "B7"],
    input_sha256: str,
    as_default: bool,
    comparison: FoundationComparison | None = None,
    verify_execution: Callable[[FoundationComparison], bool] | None = None,
) -> None:
    """Re-resolve E4 custody (including the frozen split and both executions)."""
    if method.casefold() not in FOUNDATION_MODELS:
        return
    if as_default:
        raise ValueError("foundation models cannot be default solvers")
    if (
        comparison is None
        or comparison.model.casefold() != method.casefold()
        or comparison.axis != axis
        or comparison.input_sha256 != input_sha256
        or verify_execution is None
        or verify_execution(comparison) is not True
    ):
        raise ValueError("verified donor-disjoint E4 comparison required")


class AnnotationParameters(FrozenContract):
    method: Literal[
        "marker_panel", "celltypist", "popv", "scarches", "azimuth", "singler"
    ]
    label_provenance: LabelProvenance
    donor_key: Identifier = "donor"
    label_key: Identifier = "cell_type"
    truth_key: Identifier | None = None
    calibration_donors: list[Identifier] = Field(default_factory=list, max_length=1000)
    panels: dict[str, list[str]] = Field(default_factory=dict, max_length=512)
    min_confidence: Probability = 0.8
    min_stability: Probability = 0.8
    rare_prevalence: Probability = 0.05
    reference_epochs: int = Field(default=100, ge=1, le=500)
    label_epochs: int = Field(default=25, ge=1, le=500)
    query_epochs: int = Field(default=100, ge=1, le=500)
    seed: int = Field(default=0, ge=0, le=2**31 - 1)

    @model_validator(mode="after")
    def coherent(self):
        if bool(self.panels) != (self.method == "marker_panel"):
            raise ValueError("marker-panel annotation requires frozen marker panels")
        if any(
            not k.strip() or not v or len(v) > 1000 or any(not g.strip() for g in v)
            for k, v in self.panels.items()
        ):
            raise ValueError("empty or oversized marker panel")
        if self.truth_key == self.donor_key or self.label_key == self.donor_key:
            raise ValueError("donor column cannot serve as label")
        if self.calibration_donors and not self.truth_key:
            raise ValueError("calibration donors require explicit truth labels")
        if len(set(self.calibration_donors)) != len(self.calibration_donors):
            raise ValueError("duplicate calibration donor")
        return self


def require_verified_annotation(
    result: dict,
    *,
    expression_sha256: str,
    reference_sha256: str,
    verify_custody: Callable[[str], bool],
) -> None:
    """Admit an E5 label check only after independent review of the exact result.

    The coordinator's verifier must resolve the result's execution, source of
    truth, donor identities and frozen thresholds. It must never be populated
    from a request parameter or from the worker's own success status.
    """
    from .scientific_types import content_sha256

    inputs = result.get("input_sha256") or {}
    if (
        result.get("status") != "completed"
        or inputs.get("expression") != expression_sha256
        or inputs.get("reference") != reference_sha256
        or result.get("label_provenance")
        not in {"orthogonal_protein_or_spatial", "frozen_external_reference"}
        or result.get("held_out_stability_passed") is not True
        or result.get("e5_label_checks_passed") is not True
        or result.get("clinical_release_allowed") is not False
        or verify_custody(content_sha256(result)) is not True
    ):
        raise ValueError(
            "verified donor-held-out label provenance and stability required"
        )
