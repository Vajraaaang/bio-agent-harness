"""Closed, finite C4 bindings. These records do not authenticate approvals.

Local preparation is independent of live admission. A trusted coordinator must
verify engineering evidence and approval authority again immediately before any
model or data dispatch; JSON declarations never grant either permission.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path

from jsonschema import Draft202012Validator, ValidationError

from .checkpoints import digest
from .method_planner_contract import _copy_json, validate_planning_metadata

FORMAT = "devbench.rna-scientific-trial/1"
FORMAT_V2 = "devbench.rna-scientific-trial/2"
FORMAT_V3 = "devbench.rna-scientific-trial/3"
ASSESSMENT_FORMATS = {FORMAT_V2, FORMAT_V3}
TEXT = {"type": "string", "minLength": 1, "maxLength": 4096, "pattern": r"\S"}
NAME = {**TEXT, "maxLength": 200}
HASH = {"type": "string", "pattern": "^[0-9a-f]{64}$"}
PATH = {**TEXT, "pattern": "^/"}


def obj(properties):
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}


def array(item, maximum=32, minimum=0):
    return {"type": "array", "items": item, "minItems": minimum, "maxItems": maximum, "uniqueItems": True}


def optional(schema):
    return {"anyOf": [schema, {"type": "null"}]}


REFERENCE = obj({"manifest_path": PATH, "root_hash": HASH})
SOURCE = obj({"path": PATH, "sha256": HASH, "size_bytes": {"type": "integer", "minimum": 1, "maximum": 1_000_000}})
APPROVAL = obj({"purpose": {"enum": ["workflow_generation", "trial_execution"]},
                "binding_sha256": HASH, "approver_id": NAME, "evidence": SOURCE,
                "expires_at": NAME})
DECLARATION = obj({"format": {"const": "devbench.bulk-rna-input/1"},
                   "representation": {"enum": ["raw_counts", "normalized_expression", None]},
                   "axes": {"const": ["gene", "sample"]},
                   "provenance": optional({"type": "object"}),
                   "biological_independence": optional({"type": "object"}),
                   "metadata_roles": {"type": "object", "additionalProperties": False,
                       "properties": {key: NAME for key in ("condition", "sex", "batch", "biological_unit", "time")}}})
BINDING = obj({"format": {"const": "devbench.bulk-rna-binding/1"}, "matrix_artifact_id": NAME,
               "metadata_artifact_id": NAME, "declarations": DECLARATION})
SCHEMA = obj({
    "format": {"const": FORMAT}, "trial_id": NAME,
    "question": obj({"text": TEXT, "source": SOURCE, "source_excerpt": TEXT}),
    "task": obj({"scientific_objective": TEXT, "computational_task": NAME}),
    "requirements": array(obj({"name": NAME, "value": {}, "evidence": TEXT}), 64),
    "dataset": obj({"binding": BINDING, "classification": {"enum": ["synthetic", "public", "private", "private_clinical", "unclassified"]},
                    "access_record": SOURCE, "inspection_sha256": HASH}),
    "objective": obj({"mode": {"enum": ["exact_reproduction", "independent_method_selection", "external_validation"]},
                      "estimand": TEXT, "claim_scope": TEXT, "intent": {"enum": ["descriptive", "infer"]}}),
    "design": obj({"contrasts": array(obj({"field": NAME, "reference": NAME, "target": NAME}), 16),
                   "covariates": array(NAME, 16), "chronological_field": optional(NAME),
                   "chronological_order": array(NAME, 128)}),
    "publication": REFERENCE, "comparison": optional(REFERENCE),
    "workflow": optional(obj({"planner_session_id": NAME, "experiment_ids": array(NAME, 16, 1), "sha256": HASH})),
    "engineering": obj({"source_digest": HASH, "runtime_digest": HASH, "backend_digest": HASH,
                        "model": NAME, "route": {"enum": ["native", "mediated", "offline_fixture"]}, "platform": NAME,
                        "qualification_refs": array(SOURCE, 16), "hosted_trace_required": {"type": "boolean"},
                        "baseline_decisions": array(obj({"identity": {"enum": ["T14-effect-definition-mismatch", "Harmony-missing-dependency"]},
                            "status": {"enum": ["not_dependent", "blocked", "independently_verified_correction"]}, "evidence": SOURCE}), 2, 2)}),
    "privacy": obj({"full_data_local": {"const": True}, "participant_fields": array(NAME, 32), "trace_fields": array(NAME, 32)}),
    "allocation": obj({"total_tokens": {"type": "integer", "minimum": 1, "maximum": 10_000_000},
                       "requests": {"type": "integer", "minimum": 1, "maximum": 64},
                       "memory_mb": {"type": "integer", "minimum": 128, "maximum": 65536},
                       "seconds": {"type": "number", "exclusiveMinimum": 0, "maximum": 3600},
                       "storage_bytes": {"type": "integer", "minimum": 1, "maximum": 10_000_000_000},
                       "max_runs": {"type": "integer", "minimum": 1, "maximum": 16},
                       "cost_basis": TEXT, "cost_ceiling": optional({"type": "number", "minimum": 0})}),
    "roles": obj({key: NAME for key in ("implementation_owner", "operator", "security_owner", "reviewer", "evaluator", "participant")}),
    "approvals": obj({"workflow_generation": optional(APPROVAL), "trial_execution": optional(APPROVAL)}),
})

# Version 1 is retained verbatim. Version 2 binds assessment custody explicitly,
# without accepting new fields through the historical parser.
SCHEMA_V2 = deepcopy(SCHEMA)
SCHEMA_V2["properties"]["format"] = {"const": FORMAT_V2}
CORRELATION_ID = {"type": "string", "pattern": "^[0-9a-f]{32,64}$"}
SCI_CONTEXT = obj({"project_id": CORRELATION_ID, "scientific_run_id": CORRELATION_ID,
    "attempt_id": CORRELATION_ID, "dataset_version_ids": array(HASH, 32, 1)})
SCHEMA_V2["properties"].update(assessment_intent=REFERENCE, assessment_plan=optional(REFERENCE), scientific_context=optional(SCI_CONTEXT))
# An explicitly supplied bridge is part of both approval subjects. Omission
# preserves the historical original-JSON generation path without backfilling it.
SCHEMA_V2["properties"]["execution_inputs_ref"] = SOURCE
SCHEMA_V2["required"].extend(["assessment_intent", "assessment_plan", "scientific_context"])
SCHEMA_V2["properties"]["workflow"] = optional(obj({
    "planner_session_id": NAME, "experiment_ids": array(NAME, 16, 1), "sha256": HASH,
    "workflow_ref": SOURCE,
}))

# Signed-in jobs have different accountable units from provider API requests.
# Historical schemas remain closed and retain their original approval subjects.
GENERATION_ALLOCATION = obj({
    "jobs": {"type": "integer", "minimum": 3, "maximum": 64},
    "seconds": {"type": "number", "exclusiveMinimum": 0, "maximum": 3600},
    "context_bytes": {"type": "integer", "minimum": 1024, "maximum": 1_000_000},
    "output_bytes": {"type": "integer", "minimum": 1, "maximum": 16_000_000},
})
NUMERIC_ALLOCATION = obj({key: deepcopy(SCHEMA["properties"]["allocation"]["properties"][key])
    for key in ("memory_mb", "seconds", "storage_bytes", "max_runs")})
SCHEMA_V3 = deepcopy(SCHEMA_V2)
SCHEMA_V3["properties"]["format"] = {"const": FORMAT_V3}
SCHEMA_V3["properties"]["engineering"]["properties"]["route"] = {"const": "codex_chatgpt"}
SCHEMA_V3["properties"]["engineering"]["properties"].update(
    client=obj({"kind": {"const": "official_codex_cli"}, "identity_sha256": HASH}),
    authentication=obj({"method": {"const": "chatgpt_signin"}, "observation_sha256": HASH}),
    budget_semantics={"const": "generation_jobs"}, qualified_boundary=optional(SOURCE))
SCHEMA_V3["properties"]["engineering"]["required"].extend(
    ["client", "authentication", "budget_semantics", "qualified_boundary"])
SCHEMA_V3["properties"]["allocation"] = obj({"kind": {"const": "generation_jobs"},
    "generation": GENERATION_ALLOCATION, "numeric": optional(NUMERIC_ALLOCATION)})


def validate_trial_request(value):
    value = _copy_json(value)
    if len(json.dumps(value, allow_nan=False).encode()) > 1_000_000:
        raise ValueError("Trial request exceeds its byte bound")
    try:
        schema = {FORMAT_V2: SCHEMA_V2, FORMAT_V3: SCHEMA_V3}.get(value.get("format"), SCHEMA)
        Draft202012Validator(schema).validate(value)
    except ValidationError as error:
        raise ValueError("Invalid closed scientific trial record: " + error.message[:300]) from error
    validate_planning_metadata(user_parameter_requirements=value["requirements"])
    if value["question"]["text"] != value["task"]["scientific_objective"]:
        raise ValueError("Question and scientific objective must retain the same actual question")
    decisions = value["engineering"]["baseline_decisions"]
    if len({item["identity"] for item in decisions}) != 2:
        raise ValueError("Map both retained baseline dependency decisions explicitly")
    roles = value["roles"]
    if roles["implementation_owner"] in {roles["operator"], roles["reviewer"], roles["evaluator"]}:
        raise ValueError("Implementation owner cannot self-approve or independently evaluate the gate")
    if roles["participant"] == roles["evaluator"]:
        raise ValueError("Participant and independent evaluator must be distinct")
    for purpose, approval in value["approvals"].items():
        if approval is not None:
            if approval["purpose"] != purpose or approval["approver_id"] != roles["operator"]:
                raise ValueError("Approval purpose or operator identity differs")
            _instant(approval["expires_at"])
    return deepcopy(value)


def trial_purpose_allocation(value, purpose):
    """A generation grant never activates or supplies a numeric-run allocation."""
    if purpose not in {"workflow_generation", "trial_execution"}:
        raise ValueError("Unsupported trial authority purpose")
    allocation = deepcopy(value["allocation"])
    if value["format"] == FORMAT_V3 and purpose == "workflow_generation":
        allocation["numeric"] = None
    return allocation


def _instant(value):
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, TypeError) as error:
        raise ValueError("Approval expiry must be a timezone-aware ISO timestamp") from error
    if result.tzinfo is None:
        raise ValueError("Approval expiry must be timezone-aware")
    return result


def trial_binding(value, purpose):
    """Separate generation and execution subjects; approvals are never self-hashed."""
    value = validate_trial_request(value)
    if purpose not in {"workflow_generation", "trial_execution"}:
        raise ValueError("Unsupported trial authority purpose")
    value.pop("approvals")
    if purpose == "workflow_generation":
        value.pop("workflow")
        value.pop("comparison")
        if value["format"] in ASSESSMENT_FORMATS:
            value.pop("assessment_plan")
            value.pop("scientific_context")
        if value["format"] == FORMAT_V3:
            value["allocation"] = trial_purpose_allocation(value, purpose)
    return digest({"purpose": purpose, "request": value})


def validate_approval(value, purpose, *, now=None):
    value = validate_trial_request(value)
    if value["format"] in ASSESSMENT_FORMATS and purpose == "trial_execution" and value["scientific_context"] is None:
        raise PermissionError("Scientific context IDs are required before version 2 execution approval")
    if value["format"] == FORMAT_V3 and purpose == "trial_execution" and value["allocation"]["numeric"] is None:
        raise PermissionError("Separate explicit numeric allocation is required before execution approval")
    approval = value["approvals"][purpose]
    if approval is None:
        raise PermissionError("Missing separate " + purpose + " approval")
    if approval["binding_sha256"] != trial_binding(value, purpose):
        raise PermissionError("Approval exact-trial binding mismatch")
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None or _instant(approval["expires_at"]) <= now:
        raise PermissionError("Approval expired or validation time lacks timezone")
    return deepcopy(approval)


def bounded_source(source, workspace):
    """Read exact local ordinary files only; caller labels grant no access authority."""
    root = Path(workspace)
    path = Path(source["path"])
    if not root.is_absolute() or not path.is_absolute() or not path.resolve().is_relative_to(root.resolve()) or path.is_symlink():
        raise ValueError("Trial source must be an absolute regular file inside the assigned workspace")
    from devbench.study.evidence import _read
    from hashlib import sha256
    content = _read(path, 1_000_000)
    if len(content) != source["size_bytes"]:
        raise ValueError("Trial source byte count changed or exceeds its bound")
    if sha256(content).hexdigest() != source["sha256"]:
        raise ValueError("Trial source digest changed")
    return content
