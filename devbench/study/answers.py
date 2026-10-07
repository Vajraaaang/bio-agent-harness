"""Participant claims, not self-certified evidence or scientific acceptance."""
from typing import Annotated, Literal

from pydantic import Field, model_validator

from .contracts import FrozenContract, Probability, Sha256

Label = Annotated[str, Field(min_length=1, max_length=200)]
Finite = Annotated[float, Field(allow_inf_nan=False)]
EffectUnits = Literal["log2_normalized_mean_change", "expression_difference"]
InferenceScope = Literal["fixed_design", "selection_conditional", "selection_adjusted_omnibus", "descriptive"]


class CurveReference(FrozenContract):
    reference_batch: Label | None
    library_factor: Annotated[float, Field(gt=0.0, allow_inf_nan=False)] | None
    batch_reference_policy: Literal["first_observation", "observed_batch_mix"] = "first_observation"
    biological_unit_reference: Literal["population_mean", "observed_subset"] = "population_mean"

    @model_validator(mode="after")
    def observed_mixture_scope(self):
        if self.batch_reference_policy == "observed_batch_mix" and (
                self.reference_batch is not None or self.biological_unit_reference != "observed_subset"):
            raise ValueError("Observed batch mixtures require an observed-subset reference without a fixed batch")
        return self


class AnalysisDescription(FrozenContract):
    method: Label
    formula: Annotated[str, Field(min_length=1, max_length=4096)]
    measurement_type: Literal["raw_counts", "continuous"]
    normalization: Label
    selection_criterion: Literal["fixed", "aic", "bic", "gcv", "other"]
    candidate_dfs: tuple[Annotated[int, Field(ge=1, le=100)], ...] = ()
    inference_scope: InferenceScope
    hypothesis_family_id: Label
    biological_unit_column: Label
    reference: CurveReference
    reference_description: Annotated[str, Field(min_length=1, max_length=1000)]
    tool_analysis_id: Sha256 | None = None


class GeneEstimate(FrozenContract):
    gene_id: Label
    status: Literal["tested", "described", "filtered", "no_information", "failed", "not_tested"]
    missing_reason: Annotated[str, Field(min_length=1, max_length=1000)] | None = None
    p_value: Probability | None = None
    q_value: Probability | None = None
    claimed_discovery: bool = False
    effect: Finite | None = None
    effect_units: EffectUnits | None = None
    shape: Literal["flat", "increasing", "decreasing", "transient", "delayed", "unresolved"] = "unresolved"
    time_grid: tuple[Finite, ...] = ()
    predicted_curve: tuple[Finite, ...] = ()
    confidence_interval: tuple[Finite, Finite] | None = None
    confidence_level: Annotated[float, Field(gt=0.0, lt=1.0)] | None = None
    interval_scope: Literal["fixed_design", "selection_conditional", "selection_adjusted", "unavailable"] = "unavailable"

    @model_validator(mode="after")
    def coherent_estimate(self):
        if self.status == "tested" and self.p_value is None:
            raise ValueError("A tested hypothesis requires its p-value")
        if self.status == "described" and self.p_value is not None:
            raise ValueError("A descriptive summary cannot claim a hypothesis-test p-value")
        if self.status != "tested":
            if not self.missing_reason:
                raise ValueError("An untested/failed/filtered hypothesis requires a reason")
            if self.claimed_discovery or self.q_value is not None:
                raise ValueError("Unavailable tests cannot claim significance or a successful q-value")
        if self.q_value is not None and (self.p_value is None or self.q_value < self.p_value):
            raise ValueError("Adjusted probabilities must refer to a p-value and satisfy q >= p")
        if self.claimed_discovery and self.q_value is None:
            raise ValueError("A discovery claim requires multiplicity-adjusted evidence")
        if (self.effect is None) != (self.effect_units is None):
            raise ValueError("Effect estimates and their measurement units must be supplied together")
        if len(self.time_grid) != len(self.predicted_curve) or len(self.time_grid) > 100:
            raise ValueError("A bounded prediction curve must match its time grid")
        if self.time_grid and (len(self.time_grid) < 3 or any(b <= a for a, b in zip(self.time_grid, self.time_grid[1:]))):
            raise ValueError("Curve times must be strictly increasing with at least three observations")
        if self.confidence_interval is None:
            if self.confidence_level is not None or self.interval_scope != "unavailable":
                raise ValueError("Missing intervals cannot claim coverage or an inference scope")
        elif (self.effect is None or self.confidence_level is None or self.interval_scope == "unavailable"
              or self.confidence_interval[1] < self.confidence_interval[0]):
            raise ValueError("An interval requires an effect, level, valid bounds and declared scope")
        return self


class Diagnostic(FrozenContract):
    code: Label
    message: Annotated[str, Field(min_length=1, max_length=2000)]
    scope: Literal["data", "design", "replication", "normalization", "model", "inference", "execution"]
    evidence_ids: tuple[Sha256, ...] = ()


class NetworkEdge(FrozenContract):
    source: Label
    target: Label
    score: Finite
    sign: Literal["positive", "negative", "unknown"] = "unknown"


class LineageResult(FrozenContract):
    nodes: tuple[Label, ...]
    edges: tuple[tuple[Label, Label], ...]
    root: Label | None = None
    pseudotime: dict[str, Finite] = Field(default_factory=dict)
    branch_assignments: dict[str, Label] = Field(default_factory=dict)
    interpretation: Literal["inferred_ordering"] = "inferred_ordering"

    @model_validator(mode="after")
    def graph_contract(self):
        if not self.nodes or len(self.nodes) > 100 or len(set(self.nodes)) != len(self.nodes):
            raise ValueError("Lineage nodes must be distinct and bounded")
        if len(set(self.edges)) != len(self.edges):
            raise ValueError("Duplicate lineage edges are invalid")
        if any(a == b or a not in self.nodes or b not in self.nodes for a, b in self.edges):
            raise ValueError("Lineage edges must join distinct declared nodes")
        if self.root is not None and self.root not in self.nodes:
            raise ValueError("The lineage root must be a declared node")
        if len(self.pseudotime) > 100_000 or len(self.branch_assignments) > 100_000:
            raise ValueError("Cell-level lineage output exceeds the bounded contract")
        return self


class StudyAnswer(FrozenContract):
    record_format: Literal["devbench.study.answer/1"] = "devbench.study.answer/1"
    task_id: Label
    input_sha256: Sha256
    decision: Literal["findings", "no_supported_discovery", "insufficient_evidence", "execution_failure"]
    claim_scope: Literal["association", "descriptive", "inferred_ordering", "curated_membership", "perturbation_response"]
    analysis: AnalysisDescription | None = None
    gene_statistics: Annotated[tuple[GeneEstimate, ...], Field(max_length=100_000)] = ()
    diagnostics: Annotated[tuple[Diagnostic, ...], Field(max_length=100)] = ()
    evidence_ids: Annotated[tuple[Sha256, ...], Field(max_length=1000)] = ()
    lineage: LineageResult | None = None
    regulatory_edges: Annotated[tuple[NetworkEdge, ...], Field(max_length=100_000)] = ()
    tf_activation_order: Annotated[tuple[Label, ...], Field(max_length=1000)] = ()
    unverified_narrative: Annotated[str, Field(max_length=4000)] = ""

    @model_validator(mode="after")
    def coherent_answer(self):
        genes = [row.gene_id for row in self.gene_statistics]
        if len(genes) != len(set(genes)):
            raise ValueError("Each hypothesis may be reported only once")
        if self.gene_statistics and self.analysis is None:
            raise ValueError("Gene statistics require a declared analysis")
        if self.decision in {"insufficient_evidence", "execution_failure"} and not self.diagnostics:
            raise ValueError("Refusal/failure requires an explicit diagnostic")
        if self.decision != "findings" and any(row.claimed_discovery for row in self.gene_statistics):
            raise ValueError("Discovery claims contradict the stated decision")
        if self.analysis is not None and self.analysis.measurement_type == "continuous":
            if any(row.effect_units == "log2_normalized_mean_change" for row in self.gene_statistics):
                raise ValueError("Generic continuous measurements do not establish abundance fold changes")
        if len(set(self.evidence_ids)) != len(self.evidence_ids):
            raise ValueError("Evidence references must be distinct")
        return self
