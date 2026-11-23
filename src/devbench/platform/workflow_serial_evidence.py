"""Evidence and review checks for retained workflow operations."""
from copy import deepcopy
import threading

from .workflow_state import (
    SERIAL_ROUTE, _PLANNER_EFFECTS, _SCIENCE_NODES, digest, require_fixed_lineage, validate_workflow_state,
)


class _LedgerCancellationEvent(threading.Event):
    """Fresh runtime observer of the existing durable cancellation flag."""
    def __init__(self, ledger, workflow_id):
        super().__init__()
        self.ledger, self.workflow_id = ledger, workflow_id

    def is_set(self):
        return super().is_set() or self.ledger.is_cancelled(self.workflow_id)


class _SerialWorkflowEvidence:
    def validate_evidence(self, cursor):
        """A phase name or reference string is never evidence of completed work."""
        state = validate_workflow_state(cursor)
        prefix = [f"{name}:{self.operation_attempt(state, name)}" for name in SERIAL_ROUTE[:SERIAL_ROUTE.index("run")]]
        prefix.extend(f"run:{attempt}" for attempt in range(state["trial_index"]))
        if state["trial_index"] == len(state["trial_ids"]):
            prefix.extend(f"{node}:{self.operation_attempt(state, node)}" for node in SERIAL_ROUTE[SERIAL_ROUTE.index("verify"):])
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
                    and not self._science_configuration_matches(request.get("science_configuration_digest"))):
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

    def _science_configuration_matches(self, expected):
        if self.scientific_workflow is None:
            return expected is None
        configuration = self.scientific_workflow.configuration()
        if digest(configuration) == expected:
            return True
        if not self.retained_synthesis:
            return False
        previous = deepcopy(configuration)
        claim_packets = ((previous.get("reviewer") or {}).get("claim_packets") or {})
        if claim_packets.get("builder") != "exact-scoped-stage-material/1":
            return False
        # The previous Workbench builder used inference intent for abstentions.
        # Match the full original configuration with only that exact source digest.
        claim_packets["builder_source_sha256"] = "b5740fc90a12f4ded9a532515e6b7b4dc48343cc3f5d2545ff26b625db9a3800"
        return digest(previous) == expected

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
        expected_configuration = request.get("science_configuration_digest")
        if runtime is not None and not self._science_configuration_matches(expected_configuration):
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
        if runtime is None or expected_configuration != self._science_configuration_digest():
            from devbench.analysis.workflow_science import verify_scientific_evidence
            verified = verify_scientific_evidence(node, self.planner, session_id, evidence, plan_snapshot=snapshot)
            if runtime is not None and verified["configuration_sha256"] != expected_configuration:
                raise ValueError("Historical scientific runtime does not match the retained operation")
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
            from devbench.analysis.langgraph_investigations import verify_parent_bridge
            if verify_parent_bridge(self.planner, self.ledger, workflow_id, bridged, after_hash):
                return
        raise ValueError("Workflow planner hash chain lacks a verified retained bridge")

    def _audit(self, plan):
        if plan.get("scrna_population_input") is not None:
            from .scrna_population_admission import run_population_preflight
            return run_population_preflight(plan, self.planner.methods, actor=self.actor,
                allow_data_execution=self.allow_data_execution,
                cancellation_event=_LedgerCancellationEvent(self.ledger, plan["session_id"]))
        if "fastq_input" in plan["context"]["dataset_summary"].get("observed", {}):
            from devbench.analysis.rna_design_audit import audit_rna_design
            return audit_rna_design(plan, self.planner.methods)
        input_set = plan["context"]["dataset_summary"].get("observed", {}).get("rna_input_set")
        if input_set is not None:
            from devbench.analysis.rna_input_set_contract import verify_input_set_inspection
            actual = verify_input_set_inspection(self.planner.methods, input_set)
            if {ref["artifact_id"] for ref in actual["input_artifact_refs"].values()} != set(plan["context"]["dataset_summary"].get("artifact_ids", [])):
                raise ValueError("RNA graph input set differs from its exact dataset artifacts")
            return
        scrna = plan["context"]["dataset_summary"].get("observed", {}).get("scrna_input")
        if scrna is not None:
            from devbench.analysis.rna_scrna_input_contract import verify_scrna_inspection, match_scrna_scientific_contract
            verify_scrna_inspection(self.planner.methods, scrna)
            if plan.get("scientific_contract") is not None:
                match_scrna_scientific_contract(plan["scientific_contract"], scrna)
            return
        from devbench.analysis.rna_input_contract import inspect_registered_bulk_rna_input
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
            from devbench.analysis.rna_design_audit import audit_rna_design
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
        if plan.get("scrna_population_input") is not None:
            from .scrna_population_admission import verify_population_preflight
            historical_ref = (result.get("evidence") or {}).get("record_ref") if self.retained_synthesis else None
            expected = verify_population_preflight(plan, self.planner.methods, historical_ref=historical_ref)
        else:
            from devbench.analysis.rna_design_audit import audit_rna_design
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

    def check_node_entry(self, node, cursor):
        """Validate the route before reserving or dispatching any node effect.

        An original before-cursor may replay a completed operation; a cursor
        claiming the operation is already complete cannot dispatch it again.
        """
        state = validate_workflow_state(cursor)
        require_fixed_lineage(self.ledger, state["workflow_id"])
        if node not in SERIAL_ROUTE or state["status"] != "ready":
            raise ValueError("Workflow node requires a known route and ready status")
        index = SERIAL_ROUTE.index(node)
        preceding = list(SERIAL_ROUTE[:index])
        if "run" in preceding:
            preceding.remove("run")
        expected = [f"{name}:{self.operation_attempt(state, name)}" for name in preceding]
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
        plan = self.planner.get(before["session_id"])
        audited = node == "audit" and ("rna_design" in plan or "scrna_population_input" in plan)
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
