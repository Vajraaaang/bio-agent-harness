"""Content-addressed records for one adaptive RNA-seq investigation."""

from __future__ import annotations

import hashlib
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, model_validator

from devbench.engine.provenance import canonical_json

SCHEMA_VERSION = "adaptive-rna/1"
DIGEST = r"^[0-9a-f]{64}$"
ConsumerPurpose = Literal[
    "exploratory",
    "checked_observation",
    "qualified_inference",
    "protected_evaluation",
    "admitted_research_inference",
]
EvidenceScope = Literal[
    "structural_only",
    "approved_adaptive_diagnostic",
    "target_outcome_exposed",
    "unknown",
]


class AdaptiveRecord(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, validate_default=True, allow_inf_nan=False
    )


def content_digest(kind: str, model: BaseModel) -> str:
    body = model.model_dump(mode="json", exclude={"content_digest"})
    payload = {"kind": kind, "schema": SCHEMA_VERSION, "body": body}
    return hashlib.sha256(canonical_json(payload)).hexdigest()


class SealedRecord(AdaptiveRecord):
    content_digest: Annotated[str, Field(pattern=DIGEST)]

    @model_validator(mode="after")
    def _check_digest(self, info: ValidationInfo):
        expected = content_digest(type(self).__name__, self)
        if self.content_digest != expected:
            if info.context and info.context.get("seal") == type(self).__name__:
                object.__setattr__(self, "content_digest", expected)
                return self
            raise ValueError("DIGEST_MISMATCH")
        return self


def seal(cls, **data):
    data["content_digest"] = "0" * 64
    return cls.model_validate(data, context={"seal": cls.__name__})


class ResourceGrant(AdaptiveRecord):
    cpu_seconds: Annotated[int, Field(ge=0, le=86_400)]
    memory_mb: Annotated[int, Field(ge=1, le=1_048_576)]
    numerical_jobs: Annotated[int, Field(ge=0, le=1_000)]


class GraphSemantics(AdaptiveRecord):
    node_identity: str
    node_order_digest: Annotated[str, Field(pattern=DIGEST)]
    directed: bool
    edge_weight_semantics: str
    symmetry_policy: str
    self_loop_policy: str
    construction_lineage: tuple[str, ...]


class FactRecord(SealedRecord):
    fact_class: Literal[
        "observed", "supplied", "derived", "inferred", "unknown", "conflicting"
    ]
    statement: str
    source: str
    actor: str
    conflicts_with: tuple[str, ...] = ()


class QuestionContract(SealedRecord):
    question_id: str
    objective: str
    authorized_data: tuple[str, ...]
    population: str
    biological_unit_context: str
    intended_claims: tuple[str, ...]
    exploratory_envelope: str
    constraint_sources: tuple[str, ...]
    assumptions: tuple[str, ...] = ()
    conflicts: tuple[str, ...] = ()
    inference_mode: Literal["evidence_bound_research", "confirmatory"] = (
        "evidence_bound_research"
    )


class InspectionSpec(AdaptiveRecord):
    payload_type: Literal["inspection"]
    selectors: tuple[str, ...]
    dimensions: tuple[str, ...]
    known_provenance: tuple[str, ...]


class TransformSpec(AdaptiveRecord):
    payload_type: Literal["transform"]
    algorithm: str
    axis_mappings: tuple[str, ...]
    invariants: tuple[str, ...]
    learned_state_dependencies: tuple[str, ...] = ()
    output_representation: str


class PredictiveModelSpec(AdaptiveRecord):
    payload_type: Literal["predictive_model"]
    training_objective: str
    label_provenance: str
    grouping: str
    pipeline: str
    tuning_rule: str
    partition_policy: str
    generalization_scope: str


class AnalysisSpec(AdaptiveRecord):
    payload_type: Literal["analysis"]
    method_id: str
    method_version: str
    representation: str
    cohort: str
    design: str
    contrast: str
    normalization: str
    testing_family: str
    qualification_regime: str
    exposure_policy: str


class ComparisonSpec(AdaptiveRecord):
    payload_type: Literal["comparison"]
    quantities: tuple[str, ...]
    competing_explanations: tuple[str, ...]
    criteria: tuple[str, ...]
    dependence_limits: tuple[str, ...]
    claim_obligations: tuple[str, ...]


class ValidationSpec(AdaptiveRecord):
    payload_type: Literal["validation"]
    checks: tuple[str, ...]
    controls: tuple[str, ...]
    criteria: tuple[str, ...]
    dependence_limits: tuple[str, ...]
    claim_obligations: tuple[str, ...]


class PlotSpec(AdaptiveRecord):
    payload_type: Literal["plot"]
    source_artifact_ids: tuple[str, ...]
    transformations: tuple[str, ...]
    encodings: tuple[str, ...]
    diagnostic_question: str
    panel_binding: str
    verification_policy: str


class ExploratoryCodeSpec(AdaptiveRecord):
    payload_type: Literal["exploratory_code"]
    code_digest: Annotated[str, Field(pattern=DIGEST)]
    interpreter: str
    declared_inputs: tuple[str, ...]
    output_types: tuple[str, ...]
    effects: tuple[str, ...]
    checks: tuple[str, ...]
    random_state: str


class DiagnosticCodeSpec(AdaptiveRecord):
    payload_type: Literal["diagnostic_code"]
    diagnostic_question: str
    code: ExploratoryCodeSpec


Operation = Annotated[
    InspectionSpec
    | TransformSpec
    | PredictiveModelSpec
    | AnalysisSpec
    | ComparisonSpec
    | ValidationSpec
    | PlotSpec
    | ExploratoryCodeSpec
    | DiagnosticCodeSpec,
    Field(discriminator="payload_type"),
]


class Port(AdaptiveRecord):
    name: str
    artifact_kind: Literal[
        "counts",
        "continuous_matrix",
        "embedding",
        "graph",
        "model",
        "predictions",
        "scores",
        "membership_view",
        "table",
        "figure",
        "droplet_matrix",
    ]
    required: bool = True


class DependencyEdge(AdaptiveRecord):
    edge_id: str
    source_id: str
    target_id: str
    kind: Literal["compute", "claim"]
    reason: str
    source_port: str | None = None
    target_port: str | None = None


class StepSpec(SealedRecord):
    step_id: str
    question_id: str
    plan_id: str
    branch_id: str
    purpose: str
    expected_information: str
    dependencies: tuple[str, ...]
    input_ports: tuple[Port, ...]
    output_ports: tuple[Port, ...]
    operation: Operation
    evidence_use_scope: EvidenceScope
    source_environment: str
    resources: ResourceGrant
    completion_obligations: tuple[str, ...]


class PlanVersion(SealedRecord):
    plan_id: str
    revision: Annotated[int, Field(ge=0)]
    parent_revision: int | None
    question_id: str
    nodes: tuple[StepSpec, ...]
    edges: tuple[DependencyEdge, ...]
    inserted: tuple[str, ...] = ()
    replaced: tuple[str, ...] = ()
    retired: tuple[str, ...] = ()
    triggering_evidence: tuple[str, ...] = ()
    rationale: str
    expected_observation: str
    claim_consequences: tuple[str, ...] = ()


class DerivedArtifact(SealedRecord):
    kind: Literal[
        "counts",
        "continuous_matrix",
        "embedding",
        "graph",
        "model",
        "predictions",
        "scores",
        "membership_view",
        "table",
        "figure",
    ]
    producer_attempt_id: str
    producer_spec_digest: Annotated[str, Field(pattern=DIGEST)]
    parent_digests: tuple[str, ...]
    axes: tuple[str, ...]
    stable_id_digest: Annotated[str, Field(pattern=DIGEST)]
    dimensions: tuple[Annotated[int, Field(ge=0)], ...]
    storage_layout: str
    units: str
    measurement_meaning: str
    original_population: str
    derived_population: str
    algorithm: str
    code_digest: Annotated[str, Field(pattern=DIGEST)] | None = None
    environment: str
    parameters_digest: Annotated[str, Field(pattern=DIGEST)]
    randomness: str
    learned_fit_dependencies: tuple[str, ...] = ()
    selection_history: tuple[str, ...] = ()
    permitted_uses: tuple[ConsumerPurpose, ...]
    byte_integrity: Literal["verified", "unverified", "failed"]
    schema_compatible: bool
    computational_checks: tuple[str, ...] = ()
    scientific_applicability: Literal[
        "applicable", "experimental", "inapplicable", "unknown"
    ]
    selection_status: Literal["none", "selected", "exposed", "unknown"]
    qualified_regime: str | None = None
    data_use_entitlement: str | None = None
    data_use_state: Literal[
        "none", "sealed", "authorized", "consumed", "compromised"
    ] = "none"
    graph_semantics: GraphSemantics | None = None
    parent_membership: str | None = None

    @model_validator(mode="after")
    def _kind_contract(self):
        if self.kind == "graph" and self.graph_semantics is None:
            raise ValueError("GRAPH_SEMANTICS_REQUIRED")
        if self.kind != "graph" and self.graph_semantics is not None:
            raise ValueError("GRAPH_SEMANTICS_FORBIDDEN")
        if self.kind == "membership_view" and not self.parent_membership:
            raise ValueError("MEMBERSHIP_PARENT_REQUIRED")
        if self.kind != "membership_view" and self.parent_membership is not None:
            raise ValueError("MEMBERSHIP_PARENT_FORBIDDEN")
        return self


class OperationAttempt(AdaptiveRecord):
    attempt_id: str
    idempotency_key: str
    spec_digest: Annotated[str, Field(pattern=DIGEST)]
    plan_revision: Annotated[int, Field(ge=0)]
    fence: str
    state: Literal[
        "reserved", "running", "completed", "failed", "cancelled", "recovery_required"
    ]
    backend: str


class PortFulfillment(AdaptiveRecord):
    port: str
    status: Literal["fulfilled", "failed", "absent"]
    artifact_id: Annotated[str, Field(pattern=DIGEST)] | None = None
    reason: str = ""

    @model_validator(mode="after")
    def _no_fabricated_output(self):
        if self.status == "fulfilled" and self.artifact_id is None:
            raise ValueError("FULFILLED_PORT_REQUIRES_ARTIFACT")
        if self.status != "fulfilled" and self.artifact_id is not None:
            raise ValueError("MISSING_OUTPUT_FABRICATION")
        return self


class StepOutcome(AdaptiveRecord):
    step_id: str
    attempt_id: str
    execution_status: Literal["completed", "partial", "failed", "cancelled"]
    ports: tuple[PortFulfillment, ...]

    @model_validator(mode="after")
    def _status_matches_ports(self):
        fulfilled = [port for port in self.ports if port.status == "fulfilled"]
        missing = [port for port in self.ports if port.status != "fulfilled"]
        if self.execution_status == "completed" and (not self.ports or missing):
            raise ValueError("COMPLETED_REQUIRES_ALL_PORTS")
        if self.execution_status == "partial" and (not fulfilled or not missing):
            raise ValueError("PARTIAL_REQUIRES_MIXED_PORTS")
        if self.execution_status in ("failed", "cancelled") and fulfilled:
            raise ValueError("TERMINAL_FAILURE_CANNOT_PUBLISH_OUTPUTS")
        return self


class BlockedStep(AdaptiveRecord):
    step_id: str
    spec_digest: Annotated[str, Field(pattern=DIGEST)]
    purpose: str
    missing_condition: str
    inspected_evidence: tuple[str, ...]
    affected_output_ports: tuple[str, ...]
    affected_descendants: tuple[str, ...]
    affected_claims: tuple[str, ...]
    completed_independent_work: tuple[str, ...]
    resumption_predicate: str
    human_input_needed: bool


class FigureRecord(AdaptiveRecord):
    figure_id: str
    step_id: str
    state: Literal["produced_verified", "produced_unverified", "absent", "not_required"]
    source_artifact_id: Annotated[str, Field(pattern=DIGEST)] | None = None
    diagnostic_purpose: str
    reason: str = ""

    @model_validator(mode="after")
    def _source_binding(self):
        produced = self.state in ("produced_verified", "produced_unverified")
        if produced and self.source_artifact_id is None:
            raise ValueError("FIGURE_SOURCE_REQUIRED")
        if not produced and self.source_artifact_id is not None:
            raise ValueError("ABSENT_FIGURE_HAS_SOURCE")
        return self


class VerificationRecord(AdaptiveRecord):
    verification_id: str
    attempt_id: str
    verifier: str
    evidence_ids: tuple[Annotated[str, Field(pattern=DIGEST)], ...]
    tolerances: tuple[str, ...]
    applicability: str
    result: Literal["passed", "failed", "limited"]
    limits: tuple[str, ...] = ()


class UseInvalidation(AdaptiveRecord):
    artifact_digest: Annotated[str, Field(pattern=DIGEST)]
    use: ConsumerPurpose
    reason: str
    change_class: Literal[
        "wording",
        "contradictory_evidence",
        "design",
        "representation",
        "graph_or_score",
        "membership",
        "method_or_policy",
        "question_or_data_use",
    ]


class LegacyImport(AdaptiveRecord):
    source_kind: Literal[
        "episode", "serial_workflow", "investigation_fork", "staged_code_as_action"
    ]
    source_path: str
    source_sha256: Annotated[str, Field(pattern=DIGEST)]
    original_claim_status: str
    observed_fields: tuple[str, ...]
    missing_semantics: tuple[str, ...]
    qualified_in_regime: Literal[False] = False


class AdmissionDecision(AdaptiveRecord):
    execution_admitted: bool
    claim_eligible: bool
    reasons: tuple[str, ...]
