"""Versioned capability and scientific-claim declarations."""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

Label = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)]
EvidenceDigest = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
ADMITTED_RESEARCH = "admitted_research_inference"
ClaimKind = Literal[
    "checked_observation", "exploratory_finding", "qualified_procedure_result",
    "admitted_research_inference",
]


class OperationCapability(BaseModel):
    """What an operation can execute within a bounded runtime."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    operation_id: Label
    output_kinds: tuple[Label, ...] = Field(min_length=1)
    effects: tuple[Label, ...] = ()
    max_memory_mb: int = Field(gt=0, strict=True)
    timeout_seconds: int = Field(gt=0, strict=True)


class ProcedureProfile(BaseModel):
    """Separate executable operations from the evidence for scientific claims."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    format: Literal["devbench.procedure-profile/1"] = "devbench.procedure-profile/1"
    profile_id: Label
    version: int = Field(gt=0, strict=True)
    operations: tuple[OperationCapability, ...] = Field(min_length=1)
    input_representations: tuple[Label, ...] = Field(min_length=1)
    biological_units: tuple[Label, ...] = Field(min_length=1)
    design_scope: tuple[Label, ...] = ()
    label_scope: tuple[Label, ...] = ()
    assumptions: tuple[Label, ...] = ()
    controls: tuple[Label, ...] = ()
    permitted_claims: tuple[ClaimKind, ...] = Field(min_length=1)
    qualification_evidence: tuple[EvidenceDigest, ...] = ()

    @model_validator(mode="after")
    def validate_claim_scope(self):
        ids = [operation.operation_id for operation in self.operations]
        if len(set(ids)) != len(ids):
            raise ValueError("Duplicate operation capability")
        if "qualified_procedure_result" in self.permitted_claims and not self.qualification_evidence:
            raise ValueError("Qualified procedure results require qualification evidence")
        return self
