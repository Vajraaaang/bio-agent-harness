"""Coordinator biology attempt evidence; no references, data, or execution on import.

Reference availability is supplied before the participant answers. It is not
inferred from an answer, outcome, case name, fitted gene, or cell count.
"""
from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import Field, model_validator

from .biology import Role, TemplateId
from .biology_answers import TargetId
from .biology_scoring import BiologyGrade, PUBLIC_METRIC_IDS, PUBLIC_SAFE_ROW_FIELDS, SafeEstimateRow, ScorableComponent, TargetGrade
from .contracts import Condition, FrozenContract, Identifier, Sha256, canonical_json_bytes, content_sha256
from .records import AccountingCompleteness, CompletionStatus, Nonnegative, NonnegativeInt, validate_finite_json

ReferenceAvailability = Literal["scorable", "unavailable"]
ReferenceScope = Literal["supported", "refusal", "limited_reference"]


def reference_availability_identity(record: Any) -> str:
    """Hash coordinator availability metadata, never private reference values."""
    get = record.get if isinstance(record, dict) else lambda key: getattr(record, key)
    return content_sha256({name: get(name) for name in (
        "input_sha256", "thresholds_sha256", "target_reference_availability",
        "target_reference_scopes", "target_reference_components", "target_reference_missingness")})


class BiologicalAttemptRecord(FrozenContract):
    """One coordinator slot. Runtime hashes cover common libraries/configuration.

    model_identity_sha256 covers stable observed model identity metadata, not
    per-run token usage or timestamps. harness_sha256 is condition-specific and
    never substitutes for the shared runtime when pairing conditions.
    """
    record_format: Literal["devbench.biology.attempt/1"] = "devbench.biology.attempt/1"
    attempt_id: Identifier
    study_id: Identifier
    plan_identity: Sha256
    task_identity: Sha256
    cohort_id: Identifier
    recipe_id: Identifier
    template_id: TemplateId
    case_id: Identifier
    role: Role
    condition: Condition
    repetition: Annotated[int, Field(ge=1, le=100)]
    actor_type: Literal["model_participant", "scripted_control"]
    independence_id: Identifier
    source_kind: Literal["synthetic", "public_processed"]
    source_sha256: Sha256 | None
    input_sha256: Sha256 | None
    shared_runtime_sha256: Sha256 | None
    harness_sha256: Sha256 | None
    actual_model: Annotated[str, Field(min_length=1, max_length=300)] | None
    requested_model: Annotated[str, Field(min_length=1, max_length=300)] | None
    model_identity_sha256: Sha256 | None
    model_identity_verified: bool
    attempted: bool
    completion_status: CompletionStatus
    accepted_final: bool
    answer_sha256: Sha256 | None = None
    scientific_success: bool | None
    available_target_scientific_success: bool | None
    report_accepted: bool | None
    workflow_passed: bool | None
    integrity_status: Literal["verified", "failed", "unavailable"]
    target_reference_availability: dict[TargetId, ReferenceAvailability]
    target_reference_scopes: dict[TargetId, ReferenceScope]
    target_reference_components: dict[TargetId, tuple[ScorableComponent, ...]]
    target_reference_missingness: dict[TargetId, Annotated[str, Field(min_length=1, max_length=1000)]]
    reference_availability_sha256: Sha256 | None
    per_target_results: Annotated[tuple[TargetGrade, ...], Field(max_length=50)]
    safe_rows: Annotated[tuple[SafeEstimateRow, ...], Field(max_length=100_000)]
    thresholds_sha256: Sha256 | None
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
        mask = self.target_reference_availability
        if set(mask) != set(self.target_reference_scopes) or set(mask) != set(self.target_reference_components):
            raise ValueError("Every target requires an answer-independent reference scope")
        if any((state == "scorable") != bool(self.target_reference_components[target]) for target, state in mask.items()):
            raise ValueError("Scorable targets require declared adjudicable components; unknown targets have none")
        if any(len(components) > 50 or len(set(components)) != len(components) for components in self.target_reference_components.values()):
            raise ValueError("Adjudicable target components must be bounded and distinct")
        unavailable = {target for target, state in mask.items() if state == "unavailable"}
        if unavailable != set(self.target_reference_missingness):
            raise ValueError("Every unavailable target requires exactly one reference missingness reason")
        if mask and (self.input_sha256 is None or self.thresholds_sha256 is None
                     or self.reference_availability_sha256 != reference_availability_identity(self)):
            raise ValueError("Reference availability must bind the input and fixed thresholds")
        if not mask and self.reference_availability_sha256 is not None:
            raise ValueError("An absent reference mask cannot claim a reference-availability identity")
        if self.attempted and (not mask or self.completion_status == "not_started"):
            raise ValueError("Attempted tasks require the pre-answer reference mask and execution status")
        if not self.attempted and (
            self.completion_status not in {"not_started", "blocked"} or self.accepted_final or self.answer_sha256 is not None
            or self.scientific_success is not None or self.available_target_scientific_success is not None
            or self.report_accepted is not None or self.workflow_passed is not None
            or self.per_target_results or self.safe_rows or self.model_identity_verified
            or self.trusted_tool_capture_complete or self.trusted_tool_calls is not None or self.trusted_receipt_count is not None
        ):
            raise ValueError("Unattempted metadata cannot assert scientific or execution evidence")
        if self.integrity_status == "verified" and (not self.attempted or any(value is None for value in (
            self.input_sha256, self.source_sha256, self.shared_runtime_sha256))):
            raise ValueError("Verified execution integrity requires observed source/input/shared-runtime hashes")
        if self.accepted_final and (self.completion_status != "completed" or self.integrity_status != "verified"):
            raise ValueError("Structural final acceptance requires completed, integrity-verified execution")
        if (self.scientific_success is not None or self.available_target_scientific_success is not None) and not self.accepted_final:
            raise ValueError("Raw correctness requires a structurally accepted answer; attempted failures are counted by metrics")
        if not any(state == "scorable" for state in mask.values()) and (
            self.scientific_success is not None or self.available_target_scientific_success is not None):
            raise ValueError("No adjudicable target reference means correctness remains unavailable")
        if self.scientific_success is not None and unavailable:
            raise ValueError("Any unavailable requested target leaves complete-contract correctness unknown")
        if self.report_accepted is True and (not self.accepted_final or
                (self.condition == "enforced" and self.workflow_passed is not True)):
            raise ValueError("Enforced report acceptance requires successful required workflow checks")
        if self.workflow_passed is not None and (not self.trusted_tool_capture_complete or self.integrity_status != "verified"):
            raise ValueError("Workflow adjudication requires complete trusted capture and verified integrity")
        if self.trusted_tool_capture_complete and (self.trusted_tool_calls is None or self.trusted_receipt_count is None):
            raise ValueError("Complete trusted capture requires actual tool-call and receipt counts")
        if self.model_identity_verified and (self.actual_model is None or self.model_identity_sha256 is None
                                            or self.actor_type != "model_participant"):
            raise ValueError("Verified model matching requires observed model identity and its capture hash")
        for name in ("elapsed_sec", "RSS_MB", "tokens", "cost"):
            if (getattr(self, name) is None) != (getattr(self.accounting_completeness, name) == "unavailable"):
                raise ValueError(f"{name} must agree with its accounting completeness")
        targets = [row.target_id for row in self.per_target_results]
        if len(targets) != len(set(targets)) or set(targets)-set(mask):
            raise ValueError("Target grades must be distinct members of the registered reference mask")
        by_target = {row.target_id: row for row in self.per_target_results}
        scorable = {target for target, state in mask.items() if state == "scorable"}
        if self.scientific_success is True and any(target not in by_target or by_target[target].scientific_success is not True for target in mask):
            raise ValueError("Full-contract success requires explicit successful grades for every requested target")
        if self.available_target_scientific_success is True and any(target not in by_target or by_target[target].scientific_success is not True for target in scorable):
            raise ValueError("Available-target success requires explicit successful grades for every scorable target")
        if (self.per_target_results or self.safe_rows) and self.integrity_status != "verified":
            raise ValueError("Invalid-integrity execution cannot provide scientific metrics or estimates")
        for row in self.per_target_results:
            validate_finite_json(row.model_dump(mode="json"))
            missing_metric_keys = set(row.metric_missingness)-{"scientific_success"}
            if missing_metric_keys != {name for name, value in row.metrics.items() if value is None}:
                raise ValueError("Target metric missingness must exactly identify unavailable metrics")
            if "scientific_success" in row.metric_missingness and row.scientific_success is not None:
                raise ValueError("An available target result cannot claim missing scientific correctness")
            if row.reference_availability != mask[row.target_id] or row.reference_scope != self.target_reference_scopes[row.target_id]:
                raise ValueError("Target grades cannot change pre-answer reference availability or scope")
            if tuple(row.scorable_components) != tuple(self.target_reference_components[row.target_id]):
                raise ValueError("Target grades cannot change the pre-answer adjudicable components")
            if mask[row.target_id] == "unavailable" and row.scientific_success is not None:
                raise ValueError("Unknown target truth cannot become a successful or failed biological result")
            if row.scientific_success is True and not self.accepted_final:
                raise ValueError("A missing/invalid final cannot have a successful target grade")
            if len(row.metrics) > 200 or len(row.errors) > 1000 or any(len(error) > 2000 for error in row.errors):
                raise ValueError("Public target grade metadata exceeds its bounded contract")
            if set(row.metrics)-PUBLIC_METRIC_IDS:
                raise ValueError("Only producer-allowlisted public metric names may be exported")
        if self.safe_rows and not self.accepted_final:
            raise ValueError("Predicted-row export requires a structurally accepted answer")
        if sum(len(row.values) for row in self.safe_rows) > 1_000_000:
            raise ValueError("Predicted scalar count exceeds the bounded record export budget")
        seen = set()
        for row in self.safe_rows:
            key = (row.target_id, row.hypothesis_id, row.gene_id, row.component_id)
            if (row.target_id not in mask or key in seen or len(row.values) > 1000
                    or set(row.model_dump(mode="json"))-PUBLIC_SAFE_ROW_FIELDS):
                raise ValueError("Safe estimate rows must be bounded, distinct, registered target predictions")
            seen.add(key)
        return self


def parse_biological_attempt(value: BiologicalAttemptRecord | dict) -> BiologicalAttemptRecord:
    payload = value.model_dump(mode="json") if isinstance(value, BiologicalAttemptRecord) else value
    return BiologicalAttemptRecord.model_validate_json(canonical_json_bytes(payload))


def biology_grade_record_fields(grade: BiologyGrade, *, input_sha256: str,
                                expected_task_identity: str | None = None,
                                allow_result_analysis: bool = False) -> dict:
    """Project producer-typed public grades only; never accept BiologyReference."""
    if allow_result_analysis is not True:
        raise PermissionError("Grade projection requires allow_result_analysis=True")
    if not isinstance(grade, BiologyGrade):
        raise TypeError("Only a BiologyGrade may be projected; private reference objects are forbidden")
    if expected_task_identity is None:
        raise ValueError("The scheduled expected_task_identity is required before attaching a grade")
    grade = BiologyGrade.model_validate_json(grade.model_dump_json())
    if grade.input_sha256 != input_sha256 or grade.task_id != expected_task_identity:
        raise ValueError("Grade task/input identity does not match this scheduled attempt")
    result = {
        "input_sha256": input_sha256, "task_identity": grade.task_id, "scientific_success": grade.scientific_success,
        "available_target_scientific_success": grade.available_target_scientific_success,
        "answer_sha256": grade.answer_sha256,
        "target_reference_availability": dict(grade.target_reference_availability),
        "target_reference_missingness": dict(grade.target_reference_missingness),
        "target_reference_scopes": {row.target_id: row.reference_scope for row in grade.per_target_results},
        "target_reference_components": {row.target_id: list(row.scorable_components) for row in grade.per_target_results},
        "per_target_results": [row.model_dump(mode="json") for row in grade.per_target_results],
        "safe_rows": [row.model_dump(mode="json") for row in grade.safe_rows],
        "thresholds_sha256": grade.thresholds_sha256,
    }
    result["reference_availability_sha256"] = reference_availability_identity(result)
    return result
