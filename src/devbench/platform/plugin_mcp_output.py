"""Structured MCP response contracts for the local RNA tools."""

from functools import cache
from typing import Annotated, Any, Literal, NotRequired, TypedDict

from jsonschema import Draft202012Validator
from pydantic import ConfigDict, Field, TypeAdapter, with_config

from devbench.core.procedure_profile import EvidenceDigest
from devbench.core.rna_answerability import (
    AnswerabilityDecision,
    AttestedMeasurement,
    Measurement,
    RuntimeEvidence,
    Status,
)

from .adaptive_workflow_state import ArtifactVersion, Identity

_Id = Annotated[str, Field(pattern=r"^[0-9a-f]{32}$")]
_Count = Annotated[int, Field(ge=0)]
PRIVATE_LAUNCH_HINT = (
    'devbench workbench --offline-only --workspace "<configured workspace>" '
    '--input-root "<configured input root>" --enable-scientific'
)


@with_config(ConfigDict(extra="forbid"))
class _ResponseRecord(TypedDict):
    """Fixed wire records reject fields outside their declared projection."""


class ValidationIssue(_ResponseRecord):
    loc: str
    type: str


class Blocked(_ResponseRecord):
    error: Literal["OperationBlocked"]
    reason: str
    validation_errors: NotRequired[
        Annotated[list[ValidationIssue], Field(max_length=16)]
    ]
    studio_url: NotRequired[str | None]
    launch_hint: NotRequired[
        Annotated[str, Field(json_schema_extra={"const": PRIVATE_LAUNCH_HINT})]
    ]


class StudioHandoff(_ResponseRecord):
    studio_url: str | None
    launch_hint: str


class ObservedHeader(_ResponseRecord):
    file_type: str
    cells: NotRequired[_Count | None]
    genes: NotRequired[_Count | None]
    matrix_layout: NotRequired[str]
    matrix_chunks_inspected: NotRequired[bool]
    obs_columns: NotRequired[list[str]]
    obs_columns_truncated: NotRequired[bool]
    columns: NotRequired[list[str]]
    columns_truncated: NotRequired[bool]
    layers: NotRequired[list[str]]
    obsm_keys: NotRequired[list[str]]


class InspectedInput(_ResponseRecord):
    data_classification: Literal["public", "synthetic"]
    observed: ObservedHeader
    sha256: EvidenceDigest | None
    size_bytes: _Count | None
    header_sha256: EvidenceDigest | None
    inspection_receipt_sha256: EvidenceDigest
    observation_axis_sha256: NotRequired[EvidenceDigest]
    feature_axis_sha256: NotRequired[EvidenceDigest]
    axis_verification_reason: NotRequired[Literal["hdf5_axis_verification_failed"]]


class Inspection(_ResponseRecord):
    inputs: Annotated[list[InspectedInput], Field(min_length=1, max_length=16)]


class RefusedDesign(_ResponseRecord):
    status: Literal["refused"]
    reason: str


class EstimableDesign(_ResponseRecord):
    status: Literal["estimable"]
    rank: _Count
    columns: _Count
    residual_df: _Count
    declared_units: _Count
    metadata_sha256: EvidenceDigest
    observation_axis_sha256: EvidenceDigest
    independent_units_per_group: list[_Count]
    independence: Literal["sample_labels_checked_against_declared_biological_units"]


class MeasurementPreview(_ResponseRecord):
    kind: Measurement
    slot: NotRequired[str]
    coordinate_frame: NotRequired[str]
    coordinate_units: NotRequired[str]


class SolverAnswer(AnswerabilityDecision):
    """Local solvers return the full registry decision and its bound measurements."""

    evidence_provenance: Literal["server_attested_local_inputs"]
    design_evidence_status: str
    registered_family_count: _Count
    attested_measurements: list[AttestedMeasurement]


class Answer(_ResponseRecord):
    status: Status
    reason: str
    missing_measurements: list[list[str]]
    execution_route: Literal["tier_a", "tier_b", "intake"] | None
    claim_scope: str | None
    evidence_provenance: str
    design_evidence_status: str
    registered_family_count: _Count
    clinical_release_allowed: Literal[False]
    attested_measurements: NotRequired[list[MeasurementPreview]]
    runtime: NotRequired[RuntimeEvidence | None]
    local_design: NotRequired[RefusedDesign | EstimableDesign]
    measurement_basis: NotRequired[str]
    workflow_evidence_sha256: NotRequired[EvidenceDigest]
    count1_confirmation_sha256: NotRequired[EvidenceDigest]


class PlanIdentity(StudioHandoff):
    session_id: _Id
    plan_id: _Id
    plan_sha256: EvidenceDigest


class WorkerRuntime(_ResponseRecord):
    target: Literal["local_worker"]
    oci_digest: str | None
    architecture: str | None
    oci_runtime_verification: Literal["required_at_launch"] | None
    lockfile_sha256: EvidenceDigest
    network_policy: str


_Package = Literal[
    "devharness", "numpy", "pandas", "scipy", "statsmodels", "scikit-learn", "pydeseq2"
]


class PythonRuntime(_ResponseRecord):
    target: Literal["local_worker"]
    engine: Literal["python"]
    implementation: str
    python_version: str
    platform: str
    architecture: str
    package_versions: Annotated[dict[_Package, str], Field(min_length=7, max_length=7)]
    lockfile_sha256: EvidenceDigest | None
    oci_digest: None
    network_policy: Literal["offline"]
    runtime_sha256: EvidenceDigest


class Citation(_ResponseRecord):
    citation: str
    provenance: Literal["unverified_caller_citation"]


class PlannedStep(_ResponseRecord):
    name: str
    state: Literal["planned"]


class Plan(PlanIdentity):
    status: Literal["planned"]
    execution_route: Literal["mcp", "mcp_then_offline_studio"]
    runtime: WorkerRuntime | PythonRuntime
    answerability: Answer | SolverAnswer | None
    references: list[Citation]
    inputs: list[InspectedInput]
    steps: list[PlannedStep]
    clinical_release_allowed: Literal[False]


class RegisteredPlan(PlanIdentity):
    status: Literal["planned"]
    execution_route: Literal["mcp"]


class PrivatePlan(PlanIdentity):
    status: Literal["local_studio_only"]
    execution_route: Literal["offline_studio"] | None
    execution_blocker: NotRequired[str]


class Progress(_ResponseRecord):
    completed: _Count
    total: _Count


class RunTask(_ResponseRecord):
    step: str
    target: str
    status: str
    duration_seconds: NotRequired[float]
    duration: NotRequired[str | None]
    resumed_from_checkpoint: NotRequired[bool]


class RunStatus(StudioHandoff):
    session_id: _Id
    run_id: _Id
    status: str
    compute_target: Literal["local_worker"]
    progress: Progress
    tasks: list[RunTask]
    warnings: list[str]
    generation: _Count
    stale: bool
    continuation: NotRequired[str]


class MatrixAuditMetrics(_ResponseRecord):
    genes: _Count
    profiles: _Count
    quality_status: str


class BulkFitMetrics(_ResponseRecord):
    feature_rows: _Count
    tested_feature_rows: _Count


class SalmonMetrics(_ResponseRecord):
    engine_launch_count: _Count
    engine_execution: str


class CellMetrics(_ResponseRecord):
    steps_completed: _Count


class PseudobulkMetrics(_ResponseRecord):
    review_required: Literal[True]
    step_id: Identity


class PathwayMetrics(_ResponseRecord):
    method: str
    result_rows: _Count


class RegisteredMethodMetrics(_ResponseRecord):
    outputs: _Count


class SolverMetrics(_ResponseRecord):
    solver_status: str
    n_cells: NotRequired[_Count]
    n_donors: NotRequired[_Count]
    n_spots: NotRequired[_Count]
    n_donor_sections: NotRequired[_Count]
    n_undirected_edges: NotRequired[_Count]
    tested_genes: NotRequired[_Count]
    screened_genes: NotRequired[_Count]
    target_ofdr: NotRequired[float]
    pca_components_used: NotRequired[_Count]
    reference_condition_number: NotRequired[float]
    feature_selection_stability: NotRequired[float]
    samples: NotRequired[_Count]
    estimated_samples: NotRequired[_Count]
    out_of_fold_n_donors: NotRequired[_Count]
    out_of_fold_roc_auc: NotRequired[float | None]
    out_of_fold_pr_auc: NotRequired[float | None]
    out_of_fold_brier_score: NotRequired[float]
    out_of_fold_calibration_error: NotRequired[float]
    out_of_fold_calibration_slope: NotRequired[float | None]


class SummaryTable(_ResponseRecord):
    caption: str
    columns: list[str]
    rows: list[list[str | int | float | bool | None]]


class AuditToolReceipt(_ResponseRecord):
    tool: str
    event_sha256: EvidenceDigest
    succeeded: bool


class ArtifactRef(_ResponseRecord):
    artifact_id: Identity
    sha256: EvidenceDigest


class SourceBindings(_ResponseRecord):
    input: ArtifactRef


_CellPort = Literal[
    "counts",
    "filtered_counts",
    "ambient_corrected_counts",
    "qc_metrics",
    "qc_features",
    "predictions",
    "selected",
    "embedding",
]


class StepToolReceipt(_ResponseRecord):
    step: str
    receipt_sha256: EvidenceDigest
    generation: NotRequired[_Count]
    outputs: NotRequired[dict[_CellPort, ArtifactRef]]


class GraphExecution(_ResponseRecord):
    step_id: Identity
    attempt_id: Identity
    accepted_record_id: str
    receipt_sha256: EvidenceDigest
    result_sha256: EvidenceDigest


class ResultArtifact(_ResponseRecord):
    path: str
    sha256: EvidenceDigest
    size_bytes: _Count


class Receipt(StudioHandoff):
    """Verified identity plus the evidence supplied by the selected operation."""

    session_id: _Id
    run_id: _Id
    operation: str
    probability_bounds_status: str
    clinical_release_allowed: Literal[False]
    replay_bundle_sha256: EvidenceDigest
    metrics: NotRequired[
        MatrixAuditMetrics
        | BulkFitMetrics
        | SalmonMetrics
        | CellMetrics
        | PseudobulkMetrics
        | PathwayMetrics
        | RegisteredMethodMetrics
        | SolverMetrics
    ]
    summary_tables: NotRequired[list[SummaryTable]]
    tool_receipts: NotRequired[list[AuditToolReceipt | StepToolReceipt]]
    limitations: NotRequired[list[str]]
    warnings: NotRequired[list[str]]
    figure_preview: NotRequired[str]
    nextflow_trace: NotRequired[str]
    graph_execution: NotRequired[GraphExecution]
    claim_scope: NotRequired[str]
    runtime_sha256: NotRequired[EvidenceDigest]
    result_artifact: NotRequired[ResultArtifact]
    source_bindings: NotRequired[SourceBindings]
    registered_method_outputs: NotRequired[
        Annotated[
            dict[Identity, ArtifactVersion],
            Field(max_length=16, json_schema_extra={"additionalProperties": False}),
        ]
    ]
    count1_confirmation_sha256: NotRequired[EvidenceDigest]


class Aim(_ResponseRecord):
    aim_id: str
    version: _Count
    status: str


class AimList(_ResponseRecord):
    session_id: _Id
    revision: _Count
    aims: list[Aim]


class AimRecorded(_ResponseRecord):
    session_id: _Id
    revision: _Count
    status: Literal["recorded"]


class LiteratureQueryReceipt(_ResponseRecord):
    format: Literal["devbench.literature-query-receipt/1"]
    disclosure_reservation_id: _Id
    query_sha256: EvidenceDigest
    snapshot_id: EvidenceDigest
    policy_sha256: EvidenceDigest
    claim_binding_sha256: EvidenceDigest
    content_identity_sha256: EvidenceDigest


class LiteratureSnapshot(_ResponseRecord):
    status: Literal["available", "unavailable", "truncated"]
    snapshot_id: EvidenceDigest
    query_receipt: LiteratureQueryReceipt
    scope: str
    failure: str | None


class VerifiedPackage(StudioHandoff):
    status: Literal["verified"]
    package_root_sha256: EvidenceDigest
    clinical_release_allowed: Literal[False]


_RESULTS = {
    "inspect_rna_inputs": Inspection,
    "check_answerability": Answer | SolverAnswer,
    "plan_rna_workflow": Plan | RegisteredPlan | PrivatePlan,
    "execute_reviewed_plan": RunStatus,
    "get_run_status": RunStatus,
    "get_execution_receipt": Receipt,
    "manage_research_aim": AimList | AimRecorded | LiteratureSnapshot | VerifiedPackage,
}


@cache
def output_schema(name: str) -> dict[str, Any]:
    """MCP requires an object at the root, including for result unions."""
    return {"type": "object", **TypeAdapter(_RESULTS[name] | Blocked).json_schema()}


@cache
def output_validator(name: str) -> Draft202012Validator:
    """Use the advertised contract before handing any payload to the SDK."""
    return Draft202012Validator(output_schema(name))
