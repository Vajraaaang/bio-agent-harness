"""Bounded workflow cursors and reconciliation of retained serial operations."""
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


_SCIENCE_NODES = {"discovery", "candidate_review", "implementation", "design_review", "conclusion_review"}


# These are the only planner fields each operation may change. Numerical and
# scientific result validation remains owned by the existing planner/method APIs.
_PLANNER_EFFECTS = {
    "generate": {"scientific_candidates", "selection_history", "selection_in_progress"},
    "resolve": {"candidates", "registry", "selection_history", "selection_in_progress"},
    "design": {"trial_designs", "selection_history", "selection_in_progress", "rna_execution_inputs"},
    "declare": {"experiments"}, "run": {"experiments", "results", "execution_correlations"},
    "recommend": {"recommendation", "candidates", "recommendation_evidence_assessment", "selection_history", "selection_in_progress"},
    "discovery": {"sources"}, "implementation": {"candidates", "registry", "implementation_drafts", "selection_history", "selection_in_progress"},
    "candidate_review": set(), "design_review": set(), "conclusion_review": set(),
    "audit": set(), "question": set(), "review": set(), "execution_review": set(), "verify": set(),
}


def require_fixed_lineage(ledger, lineage_id):
    """Reject current fixed-route use once the lineage has scientific authority."""
    with ledger._database() as connection:
        ledger._require_legacy_lineage(connection, lineage_id)


class _SerialWorkflowCursorActions:
    def create_state(self, session_id):
        require_fixed_lineage(self.ledger, session_id)
        plan = self.planner.get(session_id)
        if self.ledger.replay(session_id):
            raise ValueError("Existing workflow operations require checkpoint resume; create a new planner cycle to revise")
        refs = []
        for identity in plan["context"]["dataset_summary"].get("artifact_ids", []):
            self.planner.methods.artifact_path(identity)
            artifact = self.planner.methods.get("artifact", identity)
            refs.append({key: artifact[key] for key in ("artifact_id", "sha256", "artifact_type", "size_bytes")})
        return initial_state(plan, refs)

    def check(self, state, *, expected_hash=True):
        state = validate_workflow_state(state)
        require_fixed_lineage(self.ledger, state["workflow_id"])
        self.validate_evidence(state)
        plan = self.planner.get(state["session_id"])
        if planning_binding(plan) != state["binding_digest"] or digest(plan.get("scientific_anchors")) != state["anchors_digest"]:
            raise ValueError("Workflow dataset, task, requirement or anchor binding is stale")
        if expected_hash and digest(plan) != state["expected_planner_hash"]:
            raise ValueError("Workflow planner checkpoint is stale")
        ids = plan["context"]["dataset_summary"].get("artifact_ids", [])
        if ids != [ref["artifact_id"] for ref in state["artifact_refs"]]:
            raise ValueError("Workflow artifact bindings are stale")
        for ref in state["artifact_refs"]:
            self.planner.methods.artifact_path(ref["artifact_id"])
            artifact = self.planner.methods.get("artifact", ref["artifact_id"])
            if {key: artifact[key] for key in ref} != ref:
                raise ValueError("Workflow artifact reference is stale")
        return plan

    def operation_attempt(self, state, node):
        if node == "run":
            return state["trial_index"]
        if node not in {"audit", "recommend", "conclusion_review"}:
            return 0
        recovered = self.ledger.get(state["workflow_id"], node + "_no_effect", 0)
        if recovered is None:
            return 0
        original = self.ledger.get(state["workflow_id"], node, 0)
        status = "completed" if node == "conclusion_review" else "uncertain"
        if (recovered["status"] != "completed" or original is None or original["status"] != status
                or recovered["result"] != {"original_request_digest": original["request_digest"],
                    "planner_hash": original["request"]["expected_planner_hash"]}):
            raise ValueError(f"{node} recovery receipt does not match the original effect")
        return 1

    def _check_trial_cursor(self, state):
        declared = self.ledger.get(state["workflow_id"], "declare", 0)
        if declared is None or declared["status"] != "completed":
            raise ValueError("Trial cursor requires a retained completed declaration")
        if state["trial_ids"] != declared["result"]["update"]["trial_ids"]:
            raise ValueError("Trial cursor differs from the caller-selected frozen declaration")
        if state["trial_index"] >= len(state["trial_ids"]):
            raise ValueError("Trial cursor has no pending execution")
        if self.declarations != declared["request"]["declarations"]:
            raise ValueError("Runtime trial declaration scope differs from the retained selection")
        expected = declared["result"]["after_planner_hash"]
        if state["trial_index"]:
            previous = self.ledger.get(state["workflow_id"], "run", state["trial_index"] - 1)
            if previous is None or previous["status"] != "completed":
                raise ValueError("Trial cursor requires the preceding retained serial execution")
            expected = previous["result"]["after_planner_hash"]
        if state["expected_planner_hash"] != expected:
            expected = self._workspace_context_hash(state["workflow_id"], expected, state["expected_planner_hash"])
            if state["expected_planner_hash"] != expected:
                raise ValueError("Trial cursor planner hash differs from its retained serial scope")

    def _workspace_context_hash(self, workflow_id, before_hash, target_hash):
        """Follow only retained metadata-only bridges; never rebind numerical work."""
        allowed = {"scientific_base_revision", "scientific_trial_definitions"}
        for record in self.ledger.replay(workflow_id):
            if record["node"] != "workspace_context" or record["status"] != "completed":
                continue
            request, result = record["request"], record["result"]
            if request.get("before_planner_hash") != before_hash:
                continue
            next_hash = request.get("after_planner_hash")
            if (result.get("before_planner_hash") != before_hash or result.get("after_planner_hash") != next_hash
                    or request.get("session_id") != workflow_id):
                raise ValueError("Workspace context bridge differs from retained evidence")
            before = self.planner.methods.get("method_plan_snapshot", before_hash)
            after = self.planner.methods.get("method_plan_snapshot", next_hash)
            changed = {key for key in set(before) | set(after)
                       if (key in before) != (key in after) or before.get(key) != after.get(key)}
            if (digest(before) != before_hash or digest(after) != next_hash or not changed or not changed <= allowed
                    or planning_binding(before) != planning_binding(after)):
                raise ValueError("Workspace context bridge changed outside its metadata contract")
            before_hash = next_hash
            if before_hash == target_hash:
                break
        return before_hash

    def reconcile(self, cursor):
        """Reconstruct a completed API effect from harness records, never caller results.

        Partial dispatch and unvalidated model returns cannot be reconciled into
        success. This function performs no selector call or numerical execution.
        """
        state = validate_workflow_state(cursor)
        node = state["phase"]
        attempt = self.operation_attempt(state, node)
        record = self.ledger.get(state["workflow_id"], node, attempt)
        if record is None or record["status"] not in {"uncertain", "active", "completed"}:
            raise ValueError("Reconciliation requires a retained uncertain operation")
        request = record["request"]
        if (request["session_id"] != state["session_id"]
                or request["expected_planner_hash"] != state["expected_planner_hash"]
                or request.get("binding_digest") != state["binding_digest"]
                or request.get("artifact_refs") != state["artifact_refs"]):
            raise ValueError("Uncertain operation is bound to another cursor")
        if (hasattr(self.selector, "configuration") and request.get("selector_digest") is not None
                and digest(self.selector.configuration()) != request["selector_digest"]):
            raise ValueError("Reconciliation selector identity differs from its retained route")
        if record["status"] == "completed":
            self.check_node_output(node, state, record["result"])
        before = self.planner.methods.get("method_plan_snapshot", request["expected_planner_hash"])
        current = self.check(state, expected_hash=False)
        if digest(before) != request["expected_planner_hash"] or planning_binding(before) != state["binding_digest"]:
            raise ValueError("Retained before snapshot is invalid")
        retained_current = self.planner.methods.get("method_plan_snapshot", digest(current))
        if retained_current != current:
            raise ValueError("Current planner lacks a retained authoritative snapshot")
        allowed = _PLANNER_EFFECTS
        if node not in allowed or any(before.get(key) != current.get(key) for key in (set(before) | set(current)) - allowed[node]):
            raise ValueError("Planner changed outside the uncertain operation; reconciliation rejected")
        update = {"phase": node, "status": "ready", "failure": None}
        science_evidence = None
        if node in _SCIENCE_NODES:
            if self.scientific_workflow is None:
                raise ValueError("Scientific reconciliation requires the configured evidence verifier")
            if record["status"] == "completed":
                science_evidence = record["result"]["evidence"]
            else:
                science_evidence = self.scientific_workflow.reconcile(node, self.planner, state["session_id"],
                    before_planner_hash=request["expected_planner_hash"],
                    **({"attempt": attempt} if node == "conclusion_review" and attempt else {}))
            update.update(self._science_update(node, state, science_evidence))
        noop_design = (node == "design" and current == before and not any(
            item.get("method_version_id") is not None and item.get("applicability") == "eligible"
            for item in before.get("candidates", [])))
        if node in _SCIENCE_NODES:
            pass
        elif node in {"generate", "resolve", "design", "recommend"} and not noop_design:
            history = current["selection_history"]
            old = before["selection_history"]
            phases = {"generate": "method_scientific_planning", "resolve": "method_capability_resolution",
                      "design": "method_trial_design", "recommend": "method_recommendation"}
            if (len(history) != len(old) + 1 or history[:-1] != old or current.get("selection_in_progress") is not None
                    or history[-1]["status"] != "accepted" or history[-1]["phase"] != phases[node]
                    or digest(history[-1]["selector"]) != request["selector_digest"]):
                raise ValueError("No exact accepted selector completion exists; dispatch remains uncertain")
            if node == "generate" and current["scientific_candidates"] != history[-1]["response"]["candidates"]:
                raise ValueError("Frozen scientific candidates differ from accepted response")
            if node == "resolve" and set(current["registry"]) != set(request["version_ids"]):
                raise ValueError("Resolved registry differs from reserved inventory")
            if node == "design":
                from devbench.analysis.method_authoring_contract import validate_trial_design
                proposed = validate_trial_design(history[-1]["response"])
                self._verify_design_attachment({"before_planner_hash": digest(before),
                    "after_planner_hash": digest(current)}, request)
                execution = (self.planner._governed_execution_inputs(current)
                             if current.get("rna_execution_inputs") is not None else None)
                ids = ({ref["artifact_id"] for ref in execution["execution_refs"].values()} if execution is not None
                       else set(current["context"]["dataset_summary"].get("artifact_ids", [])))
                for spec in proposed:
                    self.planner._bind_trial_inputs(spec, allowed_ids=ids)
                if (current.get("trial_designs", [])[:-1] != before.get("trial_designs", [])
                        or current["trial_designs"][-1]["proposals"] != proposed
                        or current["trial_designs"][-1]["selection_attempt"] != history[-1]["attempt"]):
                    raise ValueError("Trial drafts differ from accepted design response")
            if node == "recommend":
                if current["recommendation"] != history[-1]["response"]:
                    raise ValueError("Recommendation differs from accepted response")
                self.planner.evidence_assessment(state["session_id"])
                selected = current["recommendation"]["choice"] is not None
                update.update(status="ready", outcome_ref={"session_id": state["session_id"],
                    "planner_hash": digest(current), "kind": "recommendation" if selected else "abstention"})
        elif node == "declare":
            if self.declarations != request["declarations"]:
                raise ValueError("Runtime declaration scope differs from reserved caller choice")
            chosen = self._selected(before)
            old = before["experiments"]
            new = current["experiments"][len(old):]
            if current["experiments"][:len(old)] != old or len(new) != len(chosen):
                raise ValueError("Partial trial declaration remains uncertain")
            for declaration, (branch, draft) in zip(new, chosen):
                projected = {key: value for key, value in declaration.items() if key not in {"commands", "step_ids", "branch_id", "status", "declaration_sha256"}}
                expected = {key: value for key, value in draft.items() if key != "commands"}
                if projected != expected or declaration["branch_id"] != branch or declaration["status"] != "declared":
                    raise ValueError("Retained declaration differs from selected frozen draft")
            self.planner.evidence_assessment(state["session_id"])
            update["trial_ids"] = [draft["id"] for _, draft in chosen]
        elif node == "run":
            self._check_trial_cursor(state)
            experiment_id = state["trial_ids"][attempt]
            if digest(self.execution_bindings) != request["execution_binding_digest"]:
                raise ValueError("Runtime execution bindings differ from reserved dispatch")
            expected_correlations = deepcopy(before.get("execution_correlations", {}))
            if self.execution_bindings is not None:
                expected_correlations[experiment_id] = deepcopy(self.execution_bindings)
            if current.get("execution_correlations", {}) != expected_correlations:
                raise ValueError("Retained execution correlation differs from reserved dispatch")
            old_exp = {item["id"]: item for item in before["experiments"]}
            new_exp = {item["id"]: item for item in current["experiments"]}
            expected_exp = deepcopy(old_exp)
            expected_exp[experiment_id]["status"] = "completed"
            rows = current["results"][len(before["results"]):]
            if (new_exp != expected_exp or current["results"][:len(before["results"])] != before["results"]
                    or len(rows) != len(old_exp[experiment_id]["candidates"])
                    or {row["candidate"] for row in rows} != set(old_exp[experiment_id]["candidates"])
                    or any(row["experiment_id"] != experiment_id or not row["provenance"].get("run_id") for row in rows)):
                raise ValueError("No complete receipt-bound trial exists; dispatch remains uncertain")
            self.planner.evidence_assessment(state["session_id"])
            update["trial_index"] = attempt + 1
            if any(row["status"] in {"failed", "resource_limit", "interrupted"} for row in rows):
                update.update(status="failed", failure={"kind": "trial_failed", "message": "Trial failure retained in authoritative planner results"})
        else:
            if current != before:
                raise ValueError("Read-only operation unexpectedly changed planner")
            if node == "audit":
                science_evidence = self._audit(current)
                if science_evidence is not None:
                    update.update(self._audit_update(current, science_evidence))
            if node == "verify":
                self.planner.evidence_assessment(state["session_id"])
                update["outcome_ref"] = {"session_id": state["session_id"], "planner_hash": digest(current), "kind": "evidence"}
        result = {"before_planner_hash": digest(before), "after_planner_hash": digest(current), "update": update}
        if science_evidence is not None:
            result["evidence"] = science_evidence
            if node in _SCIENCE_NODES:
                self._verify_science_evidence(node, state["session_id"], result, request)
        self.check_node_output(node, state, result)
        # A previous reconciliation may have committed its receipt immediately
        # before the graph checkpoint was lost. Reconstruct from the same
        # authoritative evidence and require its exact completed result.
        if record["status"] == "completed" and record["result"] != result:
            raise ValueError("Completed reconciliation differs from retained completion evidence")
        self.ledger.reconcile(state["workflow_id"], node, attempt, result)
        ref = f"{node}:{attempt}"
        return validate_workflow_state({**state, **update, "expected_planner_hash": digest(current),
            "operation_refs": state["operation_refs"] + ([] if ref in state["operation_refs"] else [ref])})

    def recover_interrupted_plan(self, cursor):
        """Explicit existing harness cleanup, without granting resume or redispatch."""
        from devbench.analysis.method_planner_recovery import recover_plan
        state = validate_workflow_state(cursor)
        if state["status"] != "reconciliation_required":
            raise ValueError("Planner recovery requires an uncertain workflow")
        self.check(state, expected_hash=False)
        return recover_plan(self.planner, state["session_id"])

    def verify_terminal(self, state):
        plan = self.check(state)
        if state["status"] == "abstained" and state["phase"] == "audit":
            record = self.ledger.get(state["workflow_id"], "audit", self.operation_attempt(state, "audit"))
            if (record is None or record["status"] != "completed"
                    or record["result"]["after_planner_hash"] != state["expected_planner_hash"]
                    or any(state[key] != value for key, value in record["result"]["update"].items())):
                raise ValueError("Terminal design refusal differs from its retained audit")
            self._verify_audit_evidence(plan, record["result"])
        elif state["status"] in {"completed", "abstained"}:
            record = self.ledger.get(state["workflow_id"], "conclusion_review", self.operation_attempt(state, "conclusion_review"))
            if (record is None or record["status"] != "completed"
                    or record["result"]["after_planner_hash"] != state["expected_planner_hash"]
                    or any(state[key] != value for key, value in record["result"]["update"].items())):
                raise ValueError("Terminal outcome differs from retained completion")
            kind = "recommendation" if plan["recommendation"]["choice"] is not None else "abstention"
            if state["outcome_ref"]["kind"] != kind:
                raise ValueError("Terminal outcome differs from actual recommendation")
        if state["outcome_ref"] is not None and state["outcome_ref"]["planner_hash"] != state["expected_planner_hash"]:
            raise ValueError("Terminal outcome reference is stale")
        return state
