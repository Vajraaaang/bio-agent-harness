"""Bounded coordinator records for public-track attempts.

The public reference can adjudicate only the registered observation or
normalization task.  It never establishes complete biological correctness.
"""
from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import Field, model_validator

from .contracts import Condition, FrozenContract, Identifier, Sha256, canonical_json_bytes
from .public_study import DatasetId
from .records import AccountingCompleteness, CompletionStatus, Nonnegative, NonnegativeInt, validate_finite_json


class PublicAttemptRecord(FrozenContract):
    record_format: Literal["devbench.public.attempt/1"] = "devbench.public.attempt/1"
    attempt_id: Identifier
    study_id: Identifier
    plan_identity: Sha256
    task_id: Sha256
    dataset_id: DatasetId
    condition: Condition
    repetition: Annotated[int, Field(ge=1, le=100)]
    execution_order: Annotated[int, Field(ge=1, le=1000)]
    actor_type: Literal["model_participant"] = "model_participant"
    source_kind: Literal["public_processed"] = "public_processed"
    source_id: Identifier
    independence_id: Identifier
    input_sha256: Sha256
    reference_sha256: Sha256
    preparation_sha256: Sha256
    shared_runtime_sha256: Sha256 | None
    harness_sha256: Sha256 | None
    requested_model: Annotated[str, Field(min_length=1, max_length=300)]
    actual_model: Annotated[str, Field(min_length=1, max_length=300)] | None
    model_identity_sha256: Sha256 | None
    model_identity_verified: bool
    attempted: bool
    completion_status: CompletionStatus
    accepted_final: bool
    report_accepted: bool | None
    workflow_passed: bool | None
    integrity_status: Literal["verified", "failed", "unavailable"]
    reference_task_eligible: bool | None
    reference_task_success: bool | None
    complete_biological_correctness: None = None
    metrics: dict[str, Any]
    errors: Annotated[tuple[Annotated[str, Field(min_length=1, max_length=2000)], ...], Field(max_length=1000)]
    metric_missingness: dict[str, Annotated[str, Field(min_length=1, max_length=1000)]]
    trusted_tool_capture_complete: bool
    trusted_tool_calls: NonnegativeInt | None
    trusted_receipt_count: NonnegativeInt | None
    observed_script_execution_count: NonnegativeInt | None = None
    final_execution_id: Identifier | None = None
    final_execution_tool_calls: NonnegativeInt | None = None
    elapsed_sec: Nonnegative | None
    RSS_MB: Nonnegative | None
    tokens: NonnegativeInt | None
    cost: Nonnegative | None
    accounting_completeness: AccountingCompleteness

    @model_validator(mode="after")
    def coherent_evidence(self):
        from .execution_usage import validate_execution_usage
        validate_execution_usage(self)
        missing = validate_finite_json(self.metrics, path="")
        permitted_missing = set(missing)
        if self.reference_task_success is None:
            permitted_missing.add("reference_task_success")
        if not set(missing) <= set(self.metric_missingness) <= permitted_missing:
            raise ValueError("Every missing public metric needs exactly one missingness reason")
        if not self.attempted:
            if (self.completion_status not in {"not_started", "blocked"} or self.accepted_final
                    or self.report_accepted is not None or self.workflow_passed is not None
                    or self.reference_task_success is not None or self.metrics or self.errors
                    or self.model_identity_verified or self.trusted_tool_capture_complete
                    or self.trusted_tool_calls is not None or self.trusted_receipt_count is not None):
                raise ValueError("Unattempted public slots cannot claim execution or result evidence")
        elif self.completion_status == "not_started":
            raise ValueError("Attempted public slots cannot remain not_started")
        if self.model_identity_verified and (self.actual_model is None or self.model_identity_sha256 is None):
            raise ValueError("Verified model identity requires an observed model and identity hash")
        if self.integrity_status == "verified" and (
                not self.attempted or self.shared_runtime_sha256 is None or self.harness_sha256 is None):
            raise ValueError("Verified public execution requires observed input, runtime and harness integrity")
        if self.accepted_final and (self.completion_status != "completed" or self.integrity_status != "verified"):
            raise ValueError("Accepted public output requires completed integrity-verified execution")
        if self.reference_task_success is not None and (
                self.reference_task_eligible is not True or not self.attempted
                or self.integrity_status != "verified"):
            raise ValueError("Reference-task correctness requires an eligible integrity-verified attempt")
        if self.reference_task_success is True and not self.accepted_final:
            raise ValueError("A successful public task requires a structurally accepted final")
        if self.reference_task_eligible is False and self.reference_task_success is not None:
            raise ValueError("An ineligible public reference task has unknown correctness")
        if self.metrics and (not self.attempted or self.integrity_status != "verified"):
            raise ValueError("Public metrics require an integrity-verified attempt")
        if self.report_accepted is True and (
                not self.accepted_final or (self.condition == "enforced" and self.workflow_passed is not True)):
            raise ValueError("Enforced report acceptance requires a verified required workflow")
        if self.workflow_passed is not None and (
                self.integrity_status != "verified" or not self.trusted_tool_capture_complete):
            raise ValueError("Workflow adjudication requires verified receipts and execution integrity")
        if self.trusted_tool_capture_complete and (
                self.trusted_tool_calls is None or self.trusted_receipt_count is None):
            raise ValueError("Complete tool capture requires observed call and receipt counts")
        for name in ("elapsed_sec", "RSS_MB", "tokens", "cost"):
            if (getattr(self, name) is None) != (getattr(self.accounting_completeness, name) == "unavailable"):
                raise ValueError(f"{name} must agree with accounting completeness")
        return self


def parse_public_attempt(value: PublicAttemptRecord | dict[str, Any]) -> PublicAttemptRecord:
    if isinstance(value, PublicAttemptRecord):
        value = value.model_dump(mode="json")
    return PublicAttemptRecord.model_validate_json(canonical_json_bytes(value))
