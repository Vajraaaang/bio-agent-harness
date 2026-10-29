"""Runtime adapters to existing planner APIs. Saved state never grants authority."""
from copy import deepcopy
import threading

from .workflow_state import SERIAL_ROUTE, digest, initial_state, planning_binding, validate_workflow_state


_COLAB_FACTORY_ADMISSION = object()


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


def create_rna_workflow(*, mode="single_agent", actions, checkpoint_path, investigation_backend=None, trace_observer=None):
    """Construct the explicitly selected controller; construction never dispatches."""
    qualification = False
    if type(actions).__module__ == "devbench.analysis.colab_workflow":
        from .colab_workflow import ColabQualificationActions
        qualification = type(actions) is ColabQualificationActions
        if qualification and mode != "single_agent":
            raise ValueError("Synthetic Colab qualification requires the single-agent workflow")
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
    if qualification:
        workflow._qualification_factory_token = _COLAB_FACTORY_ADMISSION
    if trace_observer is not None:
        from .rna_trace_receipts import ObservedWorkflow
        return ObservedWorkflow(workflow, trace_observer, actions, investigation_backend)
    return workflow


class _LedgerCancellationEvent(threading.Event):
    """Fresh runtime observer of the existing durable cancellation flag."""
    def __init__(self, ledger, workflow_id):
        super().__init__()
        self.ledger, self.workflow_id = ledger, workflow_id

    def is_set(self):
        return super().is_set() or self.ledger.is_cancelled(self.workflow_id)


class WorkflowActions:
    def __init__(self, planner, ledger, *, actor, selector, version_ids=(), declarations=(),
                 allow_model_execution=False, allow_data_execution=False, executor=None, execution_bindings=None,
                 investigation_policy=None, investigation_evidence=None, investigation_authority=None, before_dispatch=None,
                 requested_experiment_id=None, scientific_workflow=None, execution_input_request=None):
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
        self.before_dispatch = before_dispatch
        self.requested_experiment_id = requested_experiment_id
        self.scientific_workflow = scientific_workflow
        if execution_input_request is not None and (type(execution_input_request) is not dict
                or set(execution_input_request) != {"record_ref", "workspace"}
                or execution_input_request["record_ref"] is None or execution_input_request["workspace"] is None):
            raise ValueError("Use an exact late execution-input reference and workspace request")
        self.execution_input_request = deepcopy(execution_input_request)
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

    def validate_evidence(self, cursor):
        """A phase name or reference string is never evidence of completed work."""
        state = validate_workflow_state(cursor)
        prefix = [f"{name}:0" for name in SERIAL_ROUTE[:SERIAL_ROUTE.index("run")]]
        prefix.extend(f"run:{attempt}" for attempt in range(state["trial_index"]))
        if state["trial_index"] == len(state["trial_ids"]):
            prefix.extend(f"{node}:0" for node in SERIAL_ROUTE[SERIAL_ROUTE.index("verify"):])
        if state["operation_refs"] != prefix[:len(state["operation_refs"])]:
            raise ValueError("Workflow prerequisite operation route is invalid")
        selector_digest = digest(self.selector.configuration()) if hasattr(self.selector, "configuration") else None
        trial_ids, trial_index, outcome_ref, previous_hash = [], 0, None, None
        for reference in state["operation_refs"]:
            node, attempt = reference.split(":")
            record = self.ledger.get(state["workflow_id"], node, int(attempt))
            if (record is None or record["status"] != "completed"
                    or record["request"].get("session_id") != state["session_id"]
                    or record["request"].get("binding_digest") != state["binding_digest"]
                    or record["request"].get("artifact_refs") != state["artifact_refs"]):
                raise ValueError("Workflow node requires retained completed prerequisite evidence")
            result, request = record["result"], record["request"]
            if (self.scientific_workflow is not None
                    and not (node == "audit" and request.get("science_configuration_digest") is None)
                    and request.get("science_configuration_digest") != self._science_configuration_digest()):
                raise ValueError("Scientific runtime identity changed from the retained workflow route")
            if previous_hash is not None:
                self._check_hash_bridge(state["workflow_id"], previous_hash, request["expected_planner_hash"])
            update = result.get("update", {})
            statuses = {"completed", "abstained"} if node == "conclusion_review" else {"ready"}
            if (node == "audit" and reference == state["operation_refs"][-1]
                    and state["status"] in {"abstained", "review_required"}):
                statuses.add(state["status"])
            if node in _SCIENCE_NODES and reference == state["operation_refs"][-1] and state["status"] == "review_required":
                statuses.add("review_required")
            if (node == "run" and reference == state["operation_refs"][-1]
                    and state["status"] in {"failed", "cancelled", "reconciliation_required"}):
                statuses.add("failed")
            if (result.get("before_planner_hash") != request.get("expected_planner_hash")
                    or update.get("phase") != node or update.get("status") not in statuses):
                raise ValueError("Workflow prerequisite output differs from its completed evidence")
            self.check_node_output(node, {**state, "expected_planner_hash": request["expected_planner_hash"],
                "trial_index": int(attempt) if node == "run" else state["trial_index"]}, result)
            if (selector_digest is not None and request.get("selector_digest") is not None
                    and selector_digest != request["selector_digest"]):
                raise ValueError("Workflow model selector identity changed from its retained route")
            if node in _SCIENCE_NODES:
                self._verify_science_evidence(node, state["session_id"], result, request)
            trial_ids = update.get("trial_ids", trial_ids)
            trial_index = update.get("trial_index", trial_index)
            outcome_ref = update.get("outcome_ref", outcome_ref)
            previous_hash = result["after_planner_hash"]
        if (state["trial_ids"] != trial_ids or state["trial_index"] != trial_index
                or state["outcome_ref"] != outcome_ref):
            raise ValueError("Workflow cursor differs from its retained prerequisite output")
        # Failed/uncertain tails may have actual uncommitted effects. They cannot
        # advance: reconciliation independently derives those effects from receipts.
        if previous_hash is not None and state["status"] in {"ready", "review_required", "completed", "abstained"}:
            self._check_hash_bridge(state["workflow_id"], previous_hash, state["expected_planner_hash"])
        return state

    def _science_configuration_digest(self):
        return (digest(self.scientific_workflow.configuration())
                if self.scientific_workflow is not None else None)

    def _science_update(self, node, state, evidence, *, plan_snapshot=None):
        if type(evidence) is not dict:
            raise ValueError("Scientific node requires a verified evidence envelope")
        status = evidence.get("status")
        if status not in {"accepted", "completed", "descriptive", "blocked", "needs_validation"}:
            raise ValueError("Scientific evidence has an unsupported outcome")
        blocked = status in {"blocked", "needs_validation"}
        update = {"status": "review_required" if blocked else "ready", "failure": None}
        if blocked:
            update["failure"] = {"kind": "scientific_evidence_blocked",
                "message": f"{node} requires unresolved scientific evidence or validation; retained status: {status}"}
        if node == "conclusion_review":
            plan = self.planner.get(state["session_id"]) if plan_snapshot is None else plan_snapshot
            recommendation = plan.get("recommendation")
            if recommendation is None:
                raise ValueError("Conclusion review requires an actual provisional recommendation")
            selected = recommendation["choice"] is not None
            if not blocked:
                update["status"] = "completed" if selected else "abstained"
            update["outcome_ref"] = {"session_id": state["session_id"], "planner_hash": digest(plan),
                "kind": "recommendation" if selected else "abstention"}
        return update

    def _verify_science_evidence(self, node, session_id, result, request):
        runtime = self.scientific_workflow
        if runtime is not None and request.get("science_configuration_digest") != self._science_configuration_digest():
            raise ValueError("Scientific runtime identity differs from its retained route")
        evidence = result.get("evidence")
        required = {"record_ref", "status", "stage", "material_sha256", "before_planner_hash", "after_planner_hash"}
        if (type(evidence) is not dict or set(evidence) != required or evidence["stage"] != node
                or evidence["before_planner_hash"] != result["before_planner_hash"]
                or evidence["after_planner_hash"] != result["after_planner_hash"]):
            raise ValueError("Scientific evidence differs from the exact node execution")
        # Each operation owns its exact saved material; later allowed operations
        # never justify broadening the hash bridge or rewriting this snapshot.
        snapshot = self.planner.methods.get("method_plan_snapshot", result["after_planner_hash"])
        if digest(snapshot) != result["after_planner_hash"]:
            raise ValueError("Scientific operation lacks its exact retained after snapshot")
        if runtime is None:
            from .workflow_science import verify_scientific_evidence
            verify_scientific_evidence(node, self.planner, session_id, evidence, plan_snapshot=snapshot)
        else:
            runtime.verify(node, self.planner, session_id, evidence, plan_snapshot=snapshot)
        update = self._science_update(node, {"session_id": session_id}, evidence, plan_snapshot=snapshot)
        if any(result["update"].get(key) != value for key, value in update.items()):
            raise ValueError("Scientific node output differs from independently verified evidence")

    def _check_hash_bridge(self, workflow_id, before_hash, after_hash):
        if before_hash == after_hash:
            return
        bridged = self._workspace_context_hash(workflow_id, before_hash, after_hash)
        if bridged == after_hash:
            return
        if self.ledger.get_campaign(workflow_id) is not None:
            from .langgraph_investigations import verify_parent_bridge
            if verify_parent_bridge(self.planner, self.ledger, workflow_id, bridged, after_hash):
                return
        raise ValueError("Workflow planner hash chain lacks a verified retained bridge")

    def _audit(self, plan):
        if "fastq_input" in plan["context"]["dataset_summary"].get("observed", {}):
            from .rna_design_audit import audit_rna_design
            return audit_rna_design(plan, self.planner.methods)
        input_set = plan["context"]["dataset_summary"].get("observed", {}).get("rna_input_set")
        if input_set is not None:
            from .rna_input_set_contract import verify_input_set_inspection
            actual = verify_input_set_inspection(self.planner.methods, input_set)
            if {ref["artifact_id"] for ref in actual["input_artifact_refs"].values()} != set(plan["context"]["dataset_summary"].get("artifact_ids", [])):
                raise ValueError("RNA graph input set differs from its exact dataset artifacts")
            return
        scrna = plan["context"]["dataset_summary"].get("observed", {}).get("scrna_input")
        if scrna is not None:
            from .rna_scrna_input_contract import verify_scrna_inspection, match_scrna_scientific_contract
            verify_scrna_inspection(self.planner.methods, scrna)
            if plan.get("scientific_contract") is not None:
                match_scrna_scientific_contract(plan["scientific_contract"], scrna)
            return
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
        if "rna_design" in plan:
            from .rna_design_audit import audit_rna_design
            return audit_rna_design(plan, self.planner.methods)

    @staticmethod
    def _audit_update(plan, evidence):
        status = evidence["status"]
        if status in {"refused", "unsupported", "input_error"}:
            return {"status": "abstained", "failure": None,
                "outcome_ref": {"session_id": plan["session_id"], "planner_hash": digest(plan), "kind": "abstention"}}
        if status == "needs_information":
            return {"status": "review_required", "outcome_ref": None,
                "failure": {"kind": "rna_design_information_required",
                    "message": ("Explicit design information required: " + ", ".join(evidence["information_required"]))[:500]}}
        if status != "passed":
            raise ValueError("Unknown retained RNA design audit outcome")
        return {"status": "ready", "failure": None, "outcome_ref": None}

    def _verify_audit_evidence(self, plan, result):
        from .rna_design_audit import audit_rna_design
        expected = audit_rna_design(plan, self.planner.methods)
        if expected != result.get("evidence"):
            raise ValueError("Retained RNA design audit differs from the declared source bytes")
        update = {"phase": "audit", **self._audit_update(plan, expected)}
        if result["update"] != update:
            raise ValueError("Retained RNA design outcome differs from its verified evidence")

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
        self.check_node_entry(node, state)
        if self.ledger.is_cancelled(state["workflow_id"]):
            return {**state, "phase": node, "status": "cancelled"}
        if node == "run" and not self.allow_data_execution:
            return {**state, "phase": node, "status": "failed",
                    "failure": {"kind": "PermissionError", "message": "Runtime data execution authority is required"}}
        if node == "run":
            self._check_trial_cursor(state)
            if (self.requested_experiment_id is not None
                    and state["trial_ids"][state["trial_index"]] != self.requested_experiment_id):
                raise ValueError("Runtime execution request differs from the next exact declared trial")
        if node in _SCIENCE_NODES and self.scientific_workflow is None:
            return validate_workflow_state({**state, "phase": node, "status": "review_required",
                "failure": {"kind": "scientific_workflow_unconfigured",
                    "message": "A configured bounded scientific discovery and review runtime is required"}})
        attempt = state["trial_index"] if node == "run" else 0
        request = {"session_id": state["session_id"], "expected_planner_hash": state["expected_planner_hash"],
            "actor": self.actor,
            "binding_digest": state["binding_digest"], "artifact_refs": state["artifact_refs"],
            "version_ids": self.version_ids if node == "resolve" else [],
            "declarations": self.declarations if node == "declare" else [],
            "trial_ids": state["trial_ids"] if node == "run" else [],
            "execution_binding_digest": digest(self.execution_bindings) if node == "run" else None,
            "selector_digest": digest(self.selector.configuration()) if hasattr(self.selector, "configuration") else None,
            "science_configuration_digest": self._science_configuration_digest()}
        if node == "design" and self.execution_input_request is not None:
            request["execution_inputs"] = deepcopy(self.execution_input_request)

        def callback():
            plan = self.check(state)
            update = {"phase": node, "status": "ready", "failure": None}
            kwargs = {"actor": self.actor, "allow_model_execution": self.allow_model_execution}
            evidence = None
            if node in _SCIENCE_NODES:
                evidence = self.scientific_workflow.perform(node, self.planner, state["session_id"],
                    actor=self.actor, selector=self.selector)
                update.update(self._science_update(node, state, evidence))
            elif node == "audit":
                evidence = self._audit(plan)
                if evidence is not None:
                    update.update(self._audit_update(plan, evidence))
            elif node == "question":
                # Existing planner owns objective and requirement attribution validation.
                if not plan["context"]["task"].get("scientific_objective"):
                    raise ValueError("An explicit scientific objective is required")
            elif node in {"review", "execution_review"}:
                # The graph invokes these only after its orchestration pause is
                # resumed. This receipt grants no scientific/execution approval.
                pass
            elif node == "generate":
                if self.scientific_workflow is not None and hasattr(self.scientific_workflow, "generation_context"):
                    kwargs["discovery_context"] = self.scientific_workflow.generation_context(self.planner, state["session_id"])
                self.planner.generate_candidates(state["session_id"], self.selector, **kwargs)
            elif node == "resolve":
                if self.scientific_workflow is not None and hasattr(self.scientific_workflow, "generation_context"):
                    kwargs["discovery_context"] = self.scientific_workflow.generation_context(self.planner, state["session_id"])
                self.planner.resolve_capabilities(state["session_id"], self.selector, self.version_ids, **kwargs)
            elif node == "design":
                # No eligible implementation is a scientifically meaningful planning outcome.
                candidates = plan.get("candidates", [])
                if candidates and any(item.get("method_version_id") is not None and item.get("applicability") == "eligible" for item in candidates):
                    if self.execution_input_request is not None:
                        kwargs.update(execution_inputs_ref=self.execution_input_request["record_ref"],
                                      execution_workspace=self.execution_input_request["workspace"])
                    self.planner.design_trials(state["session_id"], self.selector, **kwargs)
                elif self.execution_input_request is not None:
                    raise ValueError("Late execution inputs require a resolved eligible method")
            elif node == "declare":
                selected = self._selected(plan)
                for branch, draft in selected:
                    self.planner.declare_trial(state["session_id"], branch, draft, actor=self.actor)
                update["trial_ids"] = [draft["id"] for _, draft in selected]
            elif node == "run":
                experiment = state["trial_ids"][state["trial_index"]]
                cancellation = ({"cancellation_event": _LedgerCancellationEvent(self.ledger, state["workflow_id"])}
                    if "fastq_input" in plan["context"]["dataset_summary"].get("observed", {}) else {})
                results = self.planner.run_trial(state["session_id"], experiment, actor=self.actor, **cancellation,
                    allow_data_execution=self.allow_data_execution, executor=self.executor,
                    execution_bindings=self.execution_bindings,
                    **({"before_dispatch": self.before_dispatch} if self.before_dispatch is not None else {}))
                update["trial_index"] = state["trial_index"] + 1
                if any(row["status"] in {"failed", "resource_limit", "interrupted"} for row in results):
                    update.update(status="failed", failure={"kind": "trial_failed", "message": "Trial failure retained in authoritative planner results"})
            elif node == "verify":
                self.planner.evidence_assessment(state["session_id"])
                update["outcome_ref"] = {"session_id": state["session_id"], "planner_hash": digest(plan), "kind": "evidence"}
            elif node == "recommend":
                recommendation = self.planner.recommend(state["session_id"], self.selector, **kwargs)
                update["status"] = "ready"
                update["outcome_ref"] = {"session_id": state["session_id"], "planner_hash": digest(self.planner.get(state["session_id"])),
                                         "kind": "recommendation" if recommendation["choice"] is not None else "abstention"}
            else:
                raise ValueError("Unknown workflow operation")
            after = self.planner.get(state["session_id"])
            if any((key in plan) != (key in after) or plan.get(key) != after.get(key)
                   for key in (set(plan) | set(after)) - _PLANNER_EFFECTS[node]):
                raise ValueError("Workflow node changed planner fields outside its execution contract")
            result = {"before_planner_hash": state["expected_planner_hash"],
                      "after_planner_hash": digest(after), "update": update}
            if evidence is not None:
                result["evidence"] = evidence
                if node in _SCIENCE_NODES:
                    self._verify_science_evidence(node, state["session_id"], result, request)
            self.check_node_output(node, state, result)
            return result

        try:
            result = self.ledger.run(state["workflow_id"], node, attempt, request, callback)
        except OperationBlocked as error:
            return validate_workflow_state({**state, "phase": node,
                "status": "cancelled" if error.state == "cancelled" else "reconciliation_required",
                "failure": {"kind": error.state, "message": "Operation retained; explicit reconciliation required before further work"}})
        except Exception as error:
            retained = self.ledger.get(state["workflow_id"], node, attempt)
            if retained is not None and retained["status"] in {"uncertain", "failed"}:
                return validate_workflow_state({**state, "phase": node, "status": "reconciliation_required",
                    "failure": {"kind": retained["status"],
                        "message": (f"Operation effects are uncertain; no automatic redispatch. {type(error).__name__}: {error}")[:500]}})
            raise
        self.check_node_output(node, state, result)
        if node in _SCIENCE_NODES:
            self._verify_science_evidence(node, state["session_id"], result, request)
        self.check(state, expected_hash=False)
        if digest(self.planner.get(state["session_id"])) != result["after_planner_hash"]:
            raise ValueError("Completed operation replay rejected: current planner is stale")
        ref = f"{node}:{attempt}"
        return validate_workflow_state({**state, **result["update"], "expected_planner_hash": result["after_planner_hash"],
            "operation_refs": state["operation_refs"] + ([] if ref in state["operation_refs"] else [ref])})

    def check_node_entry(self, node, cursor):
        """Validate the route before reserving or dispatching any node effect.

        An original before-cursor may replay a completed operation; a cursor
        claiming the operation is already complete cannot dispatch it again.
        """
        state = validate_workflow_state(cursor)
        if node not in SERIAL_ROUTE or state["status"] != "ready":
            raise ValueError("Workflow node requires a known route and ready status")
        index = SERIAL_ROUTE.index(node)
        preceding = list(SERIAL_ROUTE[:index])
        if "run" in preceding:
            preceding.remove("run")
        expected = [f"{name}:0" for name in preceding]
        if node in {"run", "verify", "recommend", "conclusion_review"}:
            if node != "run" and state["trial_index"] != len(state["trial_ids"]):
                raise ValueError("Workflow node requires all declared trial cursor executions")
            run_refs = [f"run:{attempt}" for attempt in range(state["trial_index"])]
            position = expected.index("verify:0") if "verify:0" in expected else len(expected)
            expected[position:position] = run_refs
        predecessor = SERIAL_ROUTE[index - 1] if index else "audit"
        if node == "run":
            predecessor = "run" if state["trial_index"] else "execution_review"
        elif node == "verify":
            predecessor = "run" if state["trial_ids"] else "execution_review"
        if state["phase"] != predecessor or state["operation_refs"] != expected:
            raise ValueError("Workflow node predecessor or operation route is invalid for this cursor")
        if index <= SERIAL_ROUTE.index("declare") and (state["trial_ids"] or state["trial_index"]):
            raise ValueError("Workflow trial cursor cannot precede its declaration output")
        self.validate_evidence(state)
        if node == "run":
            self._check_trial_cursor(state)
        return state

    def check_node_output(self, node, before, result):
        """Validate finite output and allowed updates before ledger completion."""
        audited = node == "audit" and "rna_design" in self.planner.get(before["session_id"])
        fields = {"before_planner_hash", "after_planner_hash", "update"} | ({"evidence"} if node in _SCIENCE_NODES or audited else set())
        if (type(result) is not dict or set(result) != fields
                or result["before_planner_hash"] != before["expected_planner_hash"]):
            raise ValueError("Workflow node output has an invalid execution binding")
        update = result["update"]
        extra = {"declare": {"trial_ids"}, "run": {"trial_index"},
                 "verify": {"outcome_ref"}, "recommend": {"outcome_ref"},
                 "conclusion_review": {"outcome_ref"}}.get(node, set())
        if audited:
            extra = {"outcome_ref"}
        if type(update) is not dict or set(update) != {"phase", "status", "failure"} | extra or update["phase"] != node:
            raise ValueError("Workflow node output violates its declared update contract")
        allowed_statuses = ({"completed", "abstained", "review_required"} if node == "conclusion_review" else
            ({"ready", "review_required"} if node in _SCIENCE_NODES else ({"ready", "failed"} if node == "run" else {"ready"})))
        if audited:
            allowed_statuses = {"ready", "review_required", "abstained"}
        if update["status"] not in allowed_statuses or ((update["failure"] is None) != (update["status"] not in {"failed", "review_required"})):
            raise ValueError("Workflow node output has an invalid outcome")
        if audited:
            self._verify_audit_evidence(self.planner.get(before["session_id"]), result)
        if node == "design":
            record = self.ledger.get(before["workflow_id"], node, 0)
            self._verify_design_attachment(result, (record or {}).get("request", {}))
        after = validate_workflow_state({**before, **update, "expected_planner_hash": result["after_planner_hash"]})
        if not _PLANNER_EFFECTS[node] and result["after_planner_hash"] != result["before_planner_hash"]:
            raise ValueError("Read-only workflow node changed its planner binding")
        if node == "run" and after["trial_index"] != before["trial_index"] + 1:
            raise ValueError("Execution output must advance exactly one declared trial")
        if node in {"verify", "recommend", "conclusion_review"}:
            recommendation = self.planner.get(before["session_id"]).get("recommendation")
            kind = "evidence" if node == "verify" else ("recommendation" if recommendation and recommendation["choice"] is not None else "abstention")
            if after["outcome_ref"] != {"session_id": after["session_id"], "planner_hash": after["expected_planner_hash"], "kind": kind}:
                raise ValueError("Workflow node output lacks its exact retained outcome reference")
        return after

    def _verify_design_attachment(self, result, request):
        """A new bridge must match this design operation's retained request."""
        before = self.planner.methods.get("method_plan_snapshot", result["before_planner_hash"])
        after = self.planner.methods.get("method_plan_snapshot", result["after_planner_hash"])
        attachment = request.get("execution_inputs")
        if attachment is None:
            if before.get("rna_execution_inputs") != after.get("rna_execution_inputs"):
                raise ValueError("Trial design changed execution inputs without a retained attachment request")
            return
        if type(attachment) is not dict or set(attachment) != {"record_ref", "workspace"}:
            raise ValueError("Malformed retained execution-input attachment")
        expected = self.planner._late_execution_binding(before, attachment["record_ref"], attachment["workspace"])
        if digest(after.get("rna_execution_inputs")) != digest(expected):
            raise ValueError("Trial design execution inputs differ from the retained attachment request")
        self.planner._governed_execution_inputs(after)

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
        attempt = state["trial_index"] if node == "run" else 0
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
                    before_planner_hash=request["expected_planner_hash"])
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
                from .method_authoring_contract import validate_trial_design
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
        from .method_planner_recovery import recover_plan
        state = validate_workflow_state(cursor)
        if state["status"] != "reconciliation_required":
            raise ValueError("Planner recovery requires an uncertain workflow")
        self.check(state, expected_hash=False)
        return recover_plan(self.planner, state["session_id"])

    def verify_terminal(self, state):
        plan = self.check(state)
        if state["status"] == "abstained" and state["phase"] == "audit":
            record = self.ledger.get(state["workflow_id"], "audit", 0)
            if (record is None or record["status"] != "completed"
                    or record["result"]["after_planner_hash"] != state["expected_planner_hash"]
                    or any(state[key] != value for key, value in record["result"]["update"].items())):
                raise ValueError("Terminal design refusal differs from its retained audit")
            self._verify_audit_evidence(plan, record["result"])
        elif state["status"] in {"completed", "abstained"}:
            record = self.ledger.get(state["workflow_id"], "conclusion_review", 0)
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
