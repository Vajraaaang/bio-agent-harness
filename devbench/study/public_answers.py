"""Public-data answer contracts. They do not assert complete biological truth."""
from typing import Annotated, Literal

from pydantic import Field, model_validator

from .contracts import FrozenContract, Identifier, Sha256

Number = Annotated[float, Field(allow_inf_nan=False)]


class ObservedGeneCurve(FrozenContract):
    gene_id: Identifier
    means: tuple[Number, ...] = Field(min_length=1, max_length=100)


class DevelopmentalObservation(FrozenContract):
    kind: Literal["developmental_observation"] = "developmental_observation"
    scope: Literal["descriptive_observed_libraries"] = "descriptive_observed_libraries"
    stage_labels: tuple[Identifier, ...] = Field(min_length=3, max_length=100)
    curves: tuple[ObservedGeneCurve, ...] = Field(min_length=1, max_length=5000)
    independent_animal_count: None = None
    pseudotime_truth_claimed: Literal[False] = False
    population_inference_claimed: Literal[False] = False

    @model_validator(mode="after")
    def aligned_axes(self):
        if len(set(self.stage_labels)) != len(self.stage_labels):
            raise ValueError("Observed stages must be distinct")
        if len({row.gene_id for row in self.curves}) != len(self.curves):
            raise ValueError("Observed gene curves must be distinct")
        if any(len(row.means) != len(self.stage_labels) for row in self.curves):
            raise ValueError("Every observed curve must match the declared stage axis")
        return self


class LibraryFactor(FrozenContract):
    library_id: Identifier
    factor: Annotated[float, Field(gt=0, ge=1e-6, le=1e6, allow_inf_nan=False)]


class SEQCNormalization(FrozenContract):
    kind: Literal["seqc_normalization"] = "seqc_normalization"
    scope: Literal["normalization_and_technical_reproducibility"] = "normalization_and_technical_reproducibility"
    factors: tuple[LibraryFactor, ...] = Field(min_length=4, max_length=1000)
    method: Annotated[str, Field(min_length=1, max_length=1000)]
    independent_patient_count: Literal[0] = 0
    unit: Literal["technical_library"] = "technical_library"
    complete_gene_truth_claimed: Literal[False] = False

    @model_validator(mode="after")
    def distinct_libraries(self):
        if len({row.library_id for row in self.factors}) != len(self.factors):
            raise ValueError("Normalization factors must name distinct technical libraries")
        return self


class PublicAnswer(FrozenContract):
    record_format: Literal["devbench.public-answer/1"] = "devbench.public-answer/1"
    task_id: Sha256
    input_sha256: Sha256
    payload: Annotated[DevelopmentalObservation | SEQCNormalization, Field(discriminator="kind")]
    tool_evidence_ids: tuple[Sha256, ...] = Field(default=(), max_length=100)
    limitations: tuple[Annotated[str, Field(min_length=1, max_length=2000)], ...] = Field(min_length=1, max_length=50)

    @model_validator(mode="after")
    def distinct_evidence(self):
        if len(set(self.tool_evidence_ids)) != len(self.tool_evidence_ids):
            raise ValueError("Public answer evidence IDs must be distinct")
        return self
