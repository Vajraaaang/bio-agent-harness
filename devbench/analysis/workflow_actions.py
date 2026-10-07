"""Runtime adapters to existing planner APIs. Saved state never grants authority."""
from copy import deepcopy

from .workflow_state import digest, initial_state, planning_binding, validate_workflow_state


def create_rna_workflow(*, mode="single_agent", actions, checkpoint_path, investigation_backend=None, trace_observer=None):
    """Construct the explicitly selected controller; construction never dispatches."""
    if mode == "single_agent":
        from .langgraph_workflow import SerialRNAWorkflow
        workflow = SerialRNAWorkflow(actions, checkpoint_path, enabled=True)
    elif mode == "independent_investigations":
        if investigation_backend is None:
            raise ValueError("Independent investigations require an audited backend")
        from .langgraph_investigations import IndependentRNAWorkflow
        workflow = IndependentRNAWorkflow(actions, checkpoint_path, investigation_backend=investigation_backend)
    else:
        raise ValueError("Unknown RNA workflow mode")
    if trace_observer is not None:
        from .rna_trace_receipts import ObservedWorkflow
        return ObservedWorkflow(workflow, trace_observer, actions, investigation_backend)
    return workflow


class WorkflowActions:
    def __init__(self, planner, ledger, *, actor, selector, version_ids=(), declarations=(),
                 allow_model_execution=False, allow_data_execution=False, executor=None, execution_bindings=None,
                 investigation_policy=None, investigation_evidence=None, investigation_authority=None):
        self.planner, self.ledger = planner, ledger
        self.actor, self.selector = actor, selector
        self.version_ids, self.declarations = list(version_ids), deepcopy(list(declarations))
        if len(self.version_ids) > 64 or len(set(self.version_ids)) != len(self.version_ids):
            raise ValueError("Use at most 64 distinct method versions")
        if len(self.declarations) > 64 or any(type(item) is not dict or set(item) != {"draft_id", "branch_id"}
                or any(type(value) is not str or not value or len(value) > 300 for value in item.values())
                for item in self.declarations):
            raise ValueError("Declare bounded exact draft and exploratory branch IDs")
        self.allow_model_execution = allow_model_execution is True
        self.allow_data_execution = allow_data_execution is True
        self.executor, self.execution_bindings = executor, execution_bindings
        self.investigation_policy = deepcopy(investigation_policy)
        self.investigation_evidence = deepcopy(investigation_evidence)
        self.investigation_authority = investigation_authority

    def create_state(self, session_id):
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

    def _audit(self, plan):
        from .rna_input_contract import inspect_registered_bulk_rna_input
        inspection = plan["context"]["dataset_summary"].get("observed", {}).get("bulk_rna_input")
        if not inspection:
            raise ValueError("RNA workflow requires an explicit registered bulk RNA binding")
        refs = inspection["input_artifact_refs"]
        declaration = {key: inspection[key] for key in ("format", "representation", "axes", "provenance", "biological_independence")}
        if "metadata_roles" in inspection:
            declaration["metadata_roles"] = inspection["metadata_roles"]
        actual = inspect_registered_bulk_rna_input(self.planner.methods,
            {"format": "devbench.bulk-rna-binding/1", "matrix_artifact_id": refs["matrix"]["artifact_id"],
             "metadata_artifact_id": refs["metadata"]["artifact_id"], "declarations": declaration})
        for key in ("representation", "shape", "gene_ids_sha256", "sample_ids_sha256", "metadata_sha256",
                    "aligned_metadata_sha256", "measurement_values_sha256", "input_artifact_refs"):
            if actual[key] != inspection[key]:
                raise ValueError("RNA axis, metadata, measurement or representation binding changed")
        # Numeric alignment is verified. Unknown provenance/replication remains unknown.

    def _drafts(self, plan):
        designs = plan.get("trial_designs", [])
        return designs[-1]["proposals"] if designs else []

    def _selected(self, plan):
        drafts = {item["id"]: item for item in self._drafts(plan)}
        if len({item["draft_id"] for item in self.declarations}) != len(self.declarations):
            raise ValueError("Select each frozen trial draft at most once")
        if any(item["draft_id"] not in drafts for item in self.declarations):
            raise ValueError("Caller declaration must select an exact frozen trial draft")
        if drafts and not self.declarations:
            raise ValueError("Explicit caller trial selection is required")
        return [(item["branch_id"], deepcopy(drafts[item["draft_id"]])) for item in self.declarations]

    def perform(self, node, cursor):
        from .workflow_operations import OperationBlocked
        state = validate_workflow_state(cursor)
        if self.ledger.is_cancelled(state["workflow_id"]):
            return {**state, "phase": node, "status": "cancelled"}
        if node == "run" and not self.allow_data_execution:
            return {**state, "phase": node, "status": "failed",
                    "failure": {"kind": "PermissionError", "message": "Runtime data execution authority is required"}}
        if node == "run":
            self._check_trial_cursor(state)
        attempt = state["trial_index"] if node == "run" else 0
        request = {"session_id": state["session_id"], "expected_planner_hash": state["expected_planner_hash"],
            "binding_digest": state["binding_digest"], "artifact_refs": state["artifact_refs"],
            "version_ids": self.version_ids if node == "resolve" else [],
            "declarations": self.declarations if node == "declare" else [],
            "trial_ids": state["trial_ids"] if node == "run" else [],
            "execution_binding_digest": digest(self.execution_bindings) if node == "run" else None,
            "selector_digest": digest(self.selector.configuration()) if node in {"generate", "resolve", "design", "recommend"}
                and hasattr(self.selector, "configuration") else None}

        def callback():
            plan = self.check(state)
            update = {"phase": node, "status": "ready", "failure": None}
            kwargs = {"actor": self.actor, "allow_model_execution": self.allow_model_execution}
            if node == "audit":
                self._audit(plan)
            elif node == "question":
                # Existing planner owns objective and requirement attribution validation.
                if not plan["context"]["task"].get("scientific_objective"):
                    raise ValueError("An explicit scientific objective is required")
            elif node == "generate":
                self.planner.generate_candidates(state["session_id"], self.selector, **kwargs)
            elif node == "resolve":
                self.planner.resolve_capabilities(state["session_id"], self.selector, self.version_ids, **kwargs)
            elif node == "design":
                # No eligible implementation is a scientifically meaningful planning outcome.
                candidates = plan.get("candidates", [])
                if candidates and any(item.get("method_version_id") is not None and item.get("applicability") == "eligible" for item in candidates):
                    self.planner.design_trials(state["session_id"], self.selector, **kwargs)
            elif node == "declare":
                selected = self._selected(plan)
                for branch, draft in selected:
                    self.planner.declare_trial(state["session_id"], branch, draft, actor=self.actor)
                update["trial_ids"] = [draft["id"] for _, draft in selected]
            elif node == "run":
                experiment = state["trial_ids"][state["trial_index"]]
                results = self.planner.run_trial(state["session_id"], experiment, actor=self.actor,
                    allow_data_execution=self.allow_data_execution, executor=self.executor,
                    execution_bindings=self.execution_bindings)
                update["trial_index"] = state["trial_index"] + 1
                if any(row["status"] in {"failed", "resource_limit", "interrupted"} for row in results):
                    update.update(status="failed", failure={"kind": "trial_failed", "message": "Trial failure retained in authoritative planner results"})
            elif node == "verify":
                self.planner.evidence_assessment(state["session_id"])
                update["outcome_ref"] = {"session_id": state["session_id"], "planner_hash": digest(plan), "kind": "evidence"}
            elif node == "recommend":
                recommendation = self.planner.recommend(state["session_id"], self.selector, **kwargs)
                update["status"] = "completed" if recommendation["choice"] is not None else "abstained"
                update["outcome_ref"] = {"session_id": state["session_id"], "planner_hash": digest(self.planner.get(state["session_id"])),
                                         "kind": "recommendation" if recommendation["choice"] is not None else "abstention"}
            else:
                raise ValueError("Unknown workflow operation")
            return {"before_planner_hash": state["expected_planner_hash"],
                    "after_planner_hash": digest(self.planner.get(state["session_id"])), "update": update}

        try:
            result = self.ledger.run(state["workflow_id"], node, attempt, request, callback)
        except OperationBlocked as error:
            return validate_workflow_state({**state, "phase": node,
                "status": "cancelled" if error.state == "cancelled" else "reconciliation_required",
                "failure": {"kind": error.state, "message": "Operation retained; explicit reconciliation required before further work"}})
        except Exception:
            retained = self.ledger.get(state["workflow_id"], node, attempt)
            if retained is not None and retained["status"] in {"uncertain", "failed"}:
                return validate_workflow_state({**state, "phase": node, "status": "reconciliation_required",
                    "failure": {"kind": retained["status"], "message": "Operation effects are uncertain; no automatic redispatch"}})
            raise
        self.check(state, expected_hash=False)
        if digest(self.planner.get(state["session_id"])) != result["after_planner_hash"]:
            raise ValueError("Completed operation replay rejected: current planner is stale")
        ref = f"{node}:{attempt}"
        return validate_workflow_state({**state, **result["update"], "expected_planner_hash": result["after_planner_hash"],
            "operation_refs": state["operation_refs"] + ([] if ref in state["operation_refs"] else [ref])})

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
            raise ValueError("Trial cursor planner hash differs from its retained serial scope")

    def reconcile(self, cursor):
        """Reconstruct a completed API effect from harness records, never caller results.

        Partial dispatch and unvalidated model returns cannot be reconciled into
        success. This function performs no selector call or numerical execution.
        """
        state = validate_workflow_state(cursor)
        node = state["phase"]
        attempt = state["trial_index"] if node == "run" else 0
        record = self.ledger.get(state["workflow_id"], node, attempt)
        if record is None or record["status"] not in {"uncertain", "active"}:
            raise ValueError("Reconciliation requires a retained uncertain operation")
        request = record["request"]
        if request["session_id"] != state["session_id"] or request["expected_planner_hash"] != state["expected_planner_hash"]:
            raise ValueError("Uncertain operation is bound to another cursor")
        before = self.planner.methods.get("method_plan_snapshot", request["expected_planner_hash"])
        current = self.check(state, expected_hash=False)
        if digest(before) != request["expected_planner_hash"] or planning_binding(before) != state["binding_digest"]:
            raise ValueError("Retained before snapshot is invalid")
        retained_current = self.planner.methods.get("method_plan_snapshot", digest(current))
        if retained_current != current:
            raise ValueError("Current planner lacks a retained authoritative snapshot")
        allowed = {
            "generate": {"scientific_candidates", "selection_history", "selection_in_progress"},
            "resolve": {"candidates", "registry", "selection_history", "selection_in_progress"},
            "design": {"trial_designs", "selection_history", "selection_in_progress"},
            "declare": {"experiments"}, "run": {"experiments", "results", "execution_correlations"},
            "recommend": {"recommendation", "candidates", "recommendation_evidence_assessment", "selection_history", "selection_in_progress"},
            "audit": set(), "question": set(), "verify": set(),
        }
        if node not in allowed or any(before.get(key) != current.get(key) for key in (set(before) | set(current)) - allowed[node]):
            raise ValueError("Planner changed outside the uncertain operation; reconciliation rejected")
        update = {"phase": node, "status": "ready", "failure": None}
        noop_design = (node == "design" and current == before and not any(
            item.get("method_version_id") is not None and item.get("applicability") == "eligible"
            for item in before.get("candidates", [])))
        if node in {"generate", "resolve", "design", "recommend"} and not noop_design:
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
                from .method_authoring_contract import validate_trial_design
                proposed = validate_trial_design(history[-1]["response"])
                ids = set(current["context"]["dataset_summary"].get("artifact_ids", []))
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
                update.update(status="completed" if selected else "abstained", outcome_ref={"session_id": state["session_id"],
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
                self._audit(current)
            if node == "verify":
                self.planner.evidence_assessment(state["session_id"])
                update["outcome_ref"] = {"session_id": state["session_id"], "planner_hash": digest(current), "kind": "evidence"}
        result = {"before_planner_hash": digest(before), "after_planner_hash": digest(current), "update": update}
        self.ledger.reconcile(state["workflow_id"], node, attempt, result)
        ref = f"{node}:{attempt}"
        return validate_workflow_state({**state, **update, "expected_planner_hash": digest(current),
            "operation_refs": state["operation_refs"] + ([] if ref in state["operation_refs"] else [ref])})

    def recover_interrupted_plan(self, cursor):
        """Explicit existing harness cleanup, without granting resume or redispatch."""
        from .method_planner_recovery import recover_plan
        state = validate_workflow_state(cursor)
        if state["status"] != "reconciliation_required":
            raise ValueError("Planner recovery requires an uncertain workflow")
        self.check(state, expected_hash=False)
        return recover_plan(self.planner, state["session_id"])

    def verify_terminal(self, state):
        plan = self.check(state)
        if state["status"] in {"completed", "abstained"}:
            record = self.ledger.get(state["workflow_id"], "recommend", 0)
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
