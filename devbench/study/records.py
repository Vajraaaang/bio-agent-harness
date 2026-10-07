"""Coordinator attempt evidence. Constructing a record never runs an analysis.

Unknown observations are required nullable fields, not successful-looking defaults.
Scientific correctness, structural artifact acceptance, and report acceptance
are separate observations. Report rejection never rewrites scientific truth.
"""
from __future__ import annotations

import math
from typing import Annotated, Any, Literal

from pydantic import Field, model_validator

from .contracts import ActorType, Condition, FrozenContract, Identifier, Scenario, Sha256

Nonnegative = Annotated[float, Field(ge=0, allow_inf_nan=False)]
NonnegativeInt = Annotated[int, Field(ge=0, strict=True)]
AccountingStatus = Literal["complete", "partial", "unavailable"]
CompletionStatus = Literal[
    "not_started", "completed", "timeout", "provider_error", "tool_error", "invalid_output", "blocked",
]
GENE_EXPORT_FIELDS = frozenset({
    "gene_id", "status", "missing_reason", "p_value", "q_value", "claimed_discovery",
    "effect", "effect_units", "shape", "time_grid", "predicted_curve", "confidence_interval",
    "confidence_level", "interval_scope", "probability_support", "effect_comparable", "curve_comparable",
})


def validate_finite_json(value: Any, *, path: str = "value", depth: int = 0) -> list[str]:
    """Reject non-JSON/nonfinite metadata and return paths of explicitly missing values."""
    if depth > 16:
        raise ValueError(f"{path} exceeds the metadata nesting limit")
    if value is None:
        return [path]
    if type(value) in (bool, str, int):
        if isinstance(value, str) and len(value) > 10_000:
            raise ValueError(f"{path} exceeds the text bound")
        return []
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError(f"{path} must be finite")
        return []
    if isinstance(value, dict):
        if len(value) > 100_000 or any(not isinstance(key, str) for key in value):
            raise ValueError(f"{path} requires bounded string keys")
        return [missing for key, item in value.items()
                for missing in validate_finite_json(item, path=f"{path}.{key}" if path else key, depth=depth+1)]
    if isinstance(value, (tuple, list)):
        if len(value) > 1_000_000:
            raise ValueError(f"{path} exceeds the array bound")
        return [missing for index, item in enumerate(value)
                for missing in validate_finite_json(item, path=f"{path}[{index}]", depth=depth+1)]
    raise ValueError(f"{path} is not finite JSON metadata")


class AccountingCompleteness(FrozenContract):
    elapsed_sec: AccountingStatus
    RSS_MB: AccountingStatus
    tokens: AccountingStatus
    cost: AccountingStatus


class AttemptRecord(FrozenContract):
    """One scheduled attempt, including failures. Monetary cost is in USD.

    shared_environment_sha256 hashes common runtime libraries/configuration;
    condition-specific harness/client bytes belong in harness_sha256. Neither
    model requests nor an unverified observed model establish a matched model.
    """
    record_format: Literal["devbench.study.attempt/1"] = "devbench.study.attempt/1"
    attempt_id: Identifier
    study_id: Identifier
    plan_identity: Sha256
    task_identity: Sha256
    cohort_id: Identifier
    independence_id: Identifier
    scenario: Scenario
    condition: Condition
    actor_type: ActorType
    repetition: Annotated[int, Field(ge=1, le=100)]
    source_kind: Literal["synthetic", "public_processed"]
    input_sha256: Sha256 | None
    shared_environment_sha256: Sha256 | None
    harness_sha256: Sha256 | None
    actual_model: Annotated[str, Field(min_length=1, max_length=300)] | None
    requested_model: Annotated[str, Field(min_length=1, max_length=300)] | None
    model_identity_verified: bool
    attempted: bool
    completion_status: CompletionStatus
    # Schema-valid, executed, integrity-checked artifact, independent of whether
    # the product's required-check gate accepts its report.
    accepted_final: bool
    # Raw hidden-reference correctness, even if required workflow checks fail.
    scientific_success: bool | None
    report_accepted: bool | None
    workflow_passed: bool | None
    integrity_status: Literal["verified", "failed", "unavailable"]
    biological_metrics: dict[str, Any]
    metric_missingness: dict[str, Annotated[str, Field(min_length=1, max_length=1000)]]
    diagnostic_outcomes: dict[str, bool | None]
    refusal_outcomes: dict[str, bool | None]
    elapsed_sec: Nonnegative | None
    RSS_MB: Nonnegative | None
    tokens: NonnegativeInt | None
    cost: Nonnegative | None
    accounting_completeness: AccountingCompleteness
    trusted_tool_capture_complete: bool
    trusted_tool_calls: NonnegativeInt | None
    observed_script_execution_count: NonnegativeInt | None = None
    final_execution_id: Identifier | None = None
    final_execution_tool_calls: NonnegativeInt | None = None
    gene_rows: Annotated[tuple[dict[str, Any], ...], Field(max_length=100_000)] = ()

    @model_validator(mode="after")
    def coherent_evidence(self):
        from .execution_usage import validate_execution_usage
        validate_execution_usage(self)
        missing = validate_finite_json(self.biological_metrics, path="")
        if set(missing) != set(self.metric_missingness):
            raise ValueError("Every missing biological metric needs exactly one declared path and reason")
        for key in ("elapsed_sec", "RSS_MB", "tokens", "cost"):
            observed = getattr(self, key)
            state = getattr(self.accounting_completeness, key)
            if (observed is None) != (state == "unavailable"):
                raise ValueError(f"{key} must agree with accounting completeness")
        if not self.attempted:
            if self.completion_status not in {"not_started", "blocked"}:
                raise ValueError("Unattempted records cannot claim physical execution")
            if (self.accepted_final or self.scientific_success is not None or self.report_accepted is not None
                    or self.workflow_passed is not None
                    or self.model_identity_verified or self.trusted_tool_capture_complete
                    or self.trusted_tool_calls is not None or self.biological_metrics or self.gene_rows):
                raise ValueError("Unattempted records cannot claim scientific, model, or execution evidence")
        elif self.completion_status == "not_started":
            raise ValueError("Attempted records cannot be not_started")
        if self.model_identity_verified and (self.actual_model is None or self.actor_type != "model_participant"):
            raise ValueError("Verified model identity requires an observed model participant")
        if self.accepted_final and (self.completion_status != "completed" or self.integrity_status != "verified"):
            raise ValueError("An accepted final requires completed execution and verified integrity")
        if self.integrity_status == "verified" and (not self.attempted or self.input_sha256 is None
                                                    or self.shared_environment_sha256 is None):
            raise ValueError("Verified execution integrity requires observed input and shared-environment hashes")
        if self.scientific_success is not None and (not self.accepted_final or self.integrity_status != "verified"):
            raise ValueError("Scientific results require an accepted final and verified integrity")
        if self.report_accepted is True and (not self.accepted_final or
                                            (self.condition == "enforced" and self.workflow_passed is not True)):
            raise ValueError("Report acceptance requires a structural final and enforced workflow checks to pass")
        if self.workflow_passed is not None and (self.integrity_status != "verified" or not self.trusted_tool_capture_complete):
            raise ValueError("Workflow evaluation requires complete trusted capture and verified integrity")
        if self.trusted_tool_capture_complete and self.trusted_tool_calls is None:
            raise ValueError("Complete trusted capture requires an observed tool-call count")
        if self.integrity_status != "verified" and (self.biological_metrics or self.gene_rows):
            raise ValueError("Unverified execution cannot supply scientific metric evidence")
        seen: set[str] = set()
        for row in self.gene_rows:
            validate_finite_json(row)
            if set(row) - GENE_EXPORT_FIELDS:
                raise ValueError("Gene export fields must not expose private reference/truth values")
            gene_id = row.get("gene_id")
            if not isinstance(gene_id, str) or not gene_id or gene_id in seen:
                raise ValueError("Gene export IDs must be nonempty and distinct")
            seen.add(gene_id)
        return self


def parse_attempt_record(value: AttemptRecord | dict[str, Any]) -> AttemptRecord:
    """Validate finite JSON dictionaries without coercing scalar types."""
    if isinstance(value, AttemptRecord):
        value = value.model_dump(mode="json")
    # JSON input permits lists for JSON arrays while preserving strict scalar validation.
    from .contracts import canonical_json_bytes
    return AttemptRecord.model_validate_json(canonical_json_bytes(value))
