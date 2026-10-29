"""Small, finite, authority-free cursors for the required RNA workflow."""
from copy import deepcopy
import hashlib
import re
from typing import TypedDict

from devharness_core.provenance import canonical_json

MAX_STATE_BYTES = 64_000
SERIAL_ROUTE = ("audit", "question", "discovery", "generate", "candidate_review", "resolve",
                "implementation", "design", "design_review", "review", "declare", "execution_review",
                "run", "verify", "recommend", "conclusion_review")
PHASES = set(SERIAL_ROUTE)
STATUSES = {"ready", "review_required", "completed", "abstained", "failed", "reconciliation_required", "cancelled"}


class WorkflowState(TypedDict):
    format: str
    workflow_id: str
    session_id: str
    expected_planner_hash: str
    binding_digest: str
    artifact_refs: list
    anchors_digest: str
    phase: str
    status: str
    operation_refs: list
    trial_ids: list
    trial_index: int
    outcome_ref: dict | None
    failure: dict | None


def digest(value):
    return hashlib.sha256(canonical_json(value)).hexdigest()


def planning_binding(plan):
    material = {"context": plan["context"], "user_parameter_requirements": plan.get("user_parameter_requirements", []),
                   "data_classification": plan.get("data_classification"), "limits": plan.get("limits"),
                   "scientific_anchors": plan.get("scientific_anchors")}
    if "scientific_contract" in plan:
        material.update(scientific_contract=plan["scientific_contract"],
                        scientific_contract_sha256=plan.get("scientific_contract_sha256"))
    if "rna_design" in plan:
        material.update(rna_design=plan["rna_design"], rna_design_sha256=plan.get("rna_design_sha256"))
    return digest(material)


def validate_workflow_state(state):
    if type(state) is not dict or set(state) != set(WorkflowState.__annotations__):
        raise ValueError("Use the exact bounded workflow cursor; data and authorities are forbidden")
    if len(canonical_json(state)) > MAX_STATE_BYTES:
        raise ValueError("Workflow cursor exceeds its byte budget")
    if state["format"] != "devbench.rna-workflow/1":
        raise ValueError("Unsupported workflow cursor version")
    for key in ("workflow_id", "session_id"):
        if type(state[key]) is not str or re.fullmatch(r"[0-9a-f]{32}", state[key]) is None:
            raise ValueError("Workflow and planner IDs must be exact UUID hex strings")
    if state["workflow_id"] != state["session_id"]:
        raise ValueError("One workflow identity is bound to one planner session")
    for key in ("expected_planner_hash", "binding_digest", "anchors_digest"):
        if type(state[key]) is not str or re.fullmatch(r"[0-9a-f]{64}", state[key]) is None:
            raise ValueError("Workflow bindings require exact SHA-256 digests")
    if state["phase"] not in PHASES or state["status"] not in STATUSES:
        raise ValueError("Unknown workflow phase or outcome")
    refs = state["artifact_refs"]
    if type(refs) is not list or len(refs) > 128:
        raise ValueError("Use bounded artifact references")
    for ref in refs:
        if (type(ref) is not dict or set(ref) != {"artifact_id", "sha256", "artifact_type", "size_bytes"}
                or type(ref["artifact_id"]) is not str or re.fullmatch(r"[0-9a-f]{64}", ref["sha256"]) is None
                or type(ref["artifact_type"]) is not str or len(ref["artifact_type"]) > 80
                or type(ref["size_bytes"]) is not int or ref["size_bytes"] < 0):
            raise ValueError("Invalid immutable artifact reference")
    for key, limit in (("trial_ids", 64), ("operation_refs", 160)):
        if (type(state[key]) is not list or len(state[key]) > limit
                or any(type(item) is not str or not item or len(item) > 300 for item in state[key])):
            raise ValueError("Workflow reference list exceeds its bound")
    if (type(state["trial_index"]) is not int or not 0 <= state["trial_index"] <= len(state["trial_ids"])
            or len(state["trial_ids"]) != len(set(state["trial_ids"]))):
        raise ValueError("Invalid serial trial cursor")
    if state["outcome_ref"] is not None and (type(state["outcome_ref"]) is not dict
            or set(state["outcome_ref"]) != {"session_id", "planner_hash", "kind"}
            or state["outcome_ref"]["session_id"] != state["session_id"]
            or state["outcome_ref"]["kind"] not in {"recommendation", "abstention", "evidence"}
            or re.fullmatch(r"[0-9a-f]{64}", state["outcome_ref"]["planner_hash"]) is None):
        raise ValueError("Invalid retained outcome reference")
    if state["failure"] is not None and (type(state["failure"]) is not dict
            or set(state["failure"]) != {"kind", "message"}
            or any(type(v) is not str or len(v) > 500 for v in state["failure"].values())):
        raise ValueError("Use bounded failure details")
    return deepcopy(state)


def initial_state(plan, artifact_refs):
    return validate_workflow_state({"format": "devbench.rna-workflow/1", "workflow_id": plan["session_id"],
        "session_id": plan["session_id"], "expected_planner_hash": digest(plan), "binding_digest": planning_binding(plan),
        "artifact_refs": artifact_refs, "anchors_digest": digest(plan.get("scientific_anchors")),
        "phase": "audit", "status": "ready", "operation_refs": [], "trial_ids": [], "trial_index": 0,
        "outcome_ref": None, "failure": None})
