"""Structured MCP response contracts for the local RNA tools."""

from functools import cache
from typing import Annotated, Any, Literal, NotRequired, TypedDict

from pydantic import ConfigDict, Field, TypeAdapter, with_config

from devbench.core.procedure_profile import EvidenceDigest
from devbench.core.rna_answerability import RuntimeEvidence, Status

_Id = Annotated[str, Field(pattern=r"^[0-9a-f]{32}$")]
_Count = Annotated[int, Field(ge=0)]


class ValidationIssue(TypedDict):
    loc: str
    type: str


@with_config(ConfigDict(extra="forbid"))
class Blocked(TypedDict):
    error: Literal["OperationBlocked"]
    reason: str
    validation_errors: NotRequired[list[ValidationIssue]]
    studio_url: NotRequired[str | None]
    launch_hint: NotRequired[str]


class StudioHandoff(TypedDict):
    studio_url: str | None
    launch_hint: str


class ObservedHeader(TypedDict):
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


class InspectedInput(TypedDict):
    data_classification: Literal["public", "synthetic"]
    observed: ObservedHeader
    sha256: EvidenceDigest | None
    size_bytes: _Count | None
    header_sha256: EvidenceDigest | None
    inspection_receipt_sha256: EvidenceDigest
    observation_axis_sha256: NotRequired[EvidenceDigest]
    feature_axis_sha256: NotRequired[EvidenceDigest]
    axis_verification_reason: NotRequired[Literal["hdf5_axis_verification_failed"]]


class Inspection(TypedDict):
    inputs: list[InspectedInput]


class Answer(TypedDict):
    status: Status
    reason: str
    missing_measurements: list[list[str]]
    execution_route: Literal["tier_a", "tier_b", "intake"] | None
    claim_scope: str | None
    evidence_provenance: str
    design_evidence_status: str
    registered_family_count: _Count
    clinical_release_allowed: Literal[False]
    attested_measurements: NotRequired[list[dict[str, Any]]]
    runtime: NotRequired[RuntimeEvidence | None]
    local_design: NotRequired[dict[str, Any]]
    count1_confirmation_sha256: NotRequired[EvidenceDigest]


class PlanIdentity(StudioHandoff):
    session_id: _Id
    plan_id: _Id
    plan_sha256: EvidenceDigest


class Plan(PlanIdentity):
    status: Literal["planned"]
    execution_route: Literal["mcp", "mcp_then_offline_studio"]
    runtime: NotRequired[dict[str, Any]]
    answerability: NotRequired[Answer | None]
    references: NotRequired[list[dict[str, str]]]
    inputs: NotRequired[list[InspectedInput]]
    steps: NotRequired[list[dict[str, str]]]
    clinical_release_allowed: NotRequired[Literal[False]]


@with_config(ConfigDict(extra="forbid"))
class PrivatePlan(PlanIdentity):
    status: Literal["local_studio_only"]
    execution_route: Literal["offline_studio"] | None
    execution_blocker: NotRequired[str]


class Progress(TypedDict):
    completed: _Count
    total: _Count


class RunTask(TypedDict):
    step: str
    target: str
    status: str
    duration_seconds: NotRequired[float]
    duration: NotRequired[str | None]


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


class Receipt(StudioHandoff):
    """Verified identity plus the evidence supplied by the selected operation."""

    session_id: _Id
    run_id: _Id
    operation: str
    probability_bounds_status: str
    clinical_release_allowed: Literal[False]
    replay_bundle_sha256: EvidenceDigest
    metrics: NotRequired[dict[str, Any]]
    summary_tables: NotRequired[list[dict[str, Any]]]
    tool_receipts: NotRequired[list[dict[str, Any]]]
    limitations: NotRequired[list[str]]


class Aim(TypedDict):
    aim_id: str
    version: _Count
    status: str


class AimList(TypedDict):
    session_id: _Id
    revision: _Count
    aims: list[Aim]


class AimRecorded(TypedDict):
    session_id: _Id
    revision: _Count
    status: Literal["recorded"]


class LiteratureSnapshot(TypedDict):
    status: Literal["available", "unavailable", "truncated"]
    snapshot_id: EvidenceDigest
    query_receipt: dict[str, Any]
    scope: str
    failure: str | None


class VerifiedPackage(StudioHandoff):
    status: Literal["verified"]
    package_root_sha256: EvidenceDigest
    clinical_release_allowed: Literal[False]


_RESULTS = {
    "inspect_rna_inputs": Inspection,
    "check_answerability": Answer,
    "plan_rna_workflow": Plan | PrivatePlan,
    "execute_reviewed_plan": RunStatus,
    "get_run_status": RunStatus,
    "get_execution_receipt": Receipt,
    "manage_research_aim": AimList | AimRecorded | LiteratureSnapshot | VerifiedPackage,
}


@cache
def output_schema(name: str) -> dict[str, Any]:
    """MCP requires an object at the root, including for result unions."""
    return {"type": "object", **TypeAdapter(_RESULTS[name] | Blocked).json_schema()}
