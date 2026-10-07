"""Conditional private investigator subgraphs over authoritative harness APIs."""
from copy import deepcopy
from importlib import metadata
import sqlite3
import itertools
import re
from typing import Annotated, TypedDict

from devharness_core.provenance import canonical_json
from .langgraph_workflow import SerialRNAWorkflow
from .rna_investigations import (decide_investigation, digest, planning_binding,
    read_investigation_json, retain_investigation_json, reconcile_investigations, reduce_investigation_refs,
    compose_investigation_task, verify_investigation_assignment, ASSIGNMENT_POLICY)
from .workflow_state import validate_workflow_state


class _IndependentGraphState(TypedDict):
    cursor: dict
    child_returns: Annotated[dict, reduce_investigation_refs]


class _ChildState(TypedDict):
    investigation_id: str
    workflow_id: str
    dispatch_ref: dict
    result_ref: dict | None
    status: str


_PARENT_MODEL_PHASES = {
    "generate": "method_scientific_planning", "resolve": "method_capability_resolution",
    "design": "method_trial_design", "recommend": "method_recommendation",
    "reassess": "method_candidates_reassessment", "preliminary_explanation": "method_preliminary_explanation",
}


def scientific_choice_identity(candidate):
    """Bind a planned scientific procedure, without names, ranking or catalog."""
    return "choice:" + digest({"method_family": candidate["method_family"],
        "parameters": sorted([{"name": row["name"], "value": row["value"]}
                              for row in candidate.get("parameter_candidates", [])], key=canonical_json),
        "diagnostic_profile": candidate.get("diagnostic_profile"), "action_scope": candidate.get("action_scope")})


def _scientific_variants(candidate):
    grouped = {}
    for row in candidate.get("parameter_candidates", []):
        group = grouped.setdefault(row["name"], {})
        group.setdefault(digest(row["value"]), row)
    varying = [name for name, entries in grouped.items() if len(entries) > 1]
    if not varying:
        return [deepcopy(candidate)]
    # One explicit parameter comparison is bounded and does not invent a
    # Cartesian tuning search or turn parameter choices into observations.
    if len(varying) != 1 or len(grouped[varying[0]]) != 2:
        return []
    variants = []
    for selected in grouped[varying[0]].values():
        variant = deepcopy(candidate)
        variant["parameter_candidates"] = [deepcopy(next(iter(grouped[name].values()))) for name in grouped if name != varying[0]] + [deepcopy(selected)]
        variants.append(variant)
    return variants


def _axis_choices(axis, left, right, plan, evidence):
    """Supported observable method differences; factual prerequisites do not count."""
    changes = set()
    left_params = {row["name"]: row["value"] for row in left.get("parameter_candidates", [])}
    right_params = {row["name"]: row["value"] for row in right.get("parameter_candidates", [])}
    for key in set(left_params) | set(right_params):
        if key not in left_params or key not in right_params or canonical_json(left_params[key]) != canonical_json(right_params[key]):
            changes.add(key)
    if any(key not in left_params or key not in right_params for key in changes):
        return False
    inspection = plan["context"]["dataset_summary"].get("observed", {}).get("bulk_rna_input", {})
    roles, catalog = inspection.get("metadata_roles", {}), inspection.get("metadata_catalog", {})
    observed = {row["field"]: row["value"] for row in evidence["facts"]["observed"]}
    if axis in {"temporal_shape_or_complexity", "valid_nuisance_model"}:
        representation = inspection.get("representation")
        for choice, params in ((left, left_params), (right, right_params)):
            family = str(params.get("model_family", choice["method_family"])).casefold()
            if representation == "raw_counts" and re.search(r"negative[ _-]?binomial", family) is None:
                return False
            if representation == "normalized_expression" and re.search(r"\bgaussian\b", family) is None:
                return False
    if axis == "temporal_shape_or_complexity":
        if not changes or not changes <= {"df", "spline_df", "basis_degree", "trend_model", "spline_degree", "spline_basis", "model_shape"}:
            return False
        column = roles.get("time")
        count = catalog.get(column, {}).get("distinct_count", 0)
        if not column or count < 3 or observed.get("chronology_valid") is not True:
            return False
        for params in (left_params, right_params):
            for key in ("df", "spline_df", "basis_degree", "spline_degree"):
                if key in params and (type(params[key]) is not int or not 1 <= params[key] < count):
                    return False
        return True
    if axis == "valid_nuisance_model":
        column = roles.get("batch")
        return (bool(changes) and changes <= {"nuisance_strategy", "batch_adjustment", "nuisance_model", "batch_model", "covariate_model"}
            and bool(column) and catalog.get(column, {}).get("distinct_count", 0) >= 2
            and observed.get("design_rank_valid") is True and observed.get("age_batch_confounded") is False)
    if axis == "objective_tradeoff":
        if not changes or not changes <= {"loss_function", "error_tradeoff", "sensitivity_weight", "specificity_weight", "robustness_weight", "stability_weight"}:
            return False
        objective = " ".join([plan["context"]["task"]["scientific_objective"],
                              *plan["context"]["task"].get("constraints", [])]).casefold()
        criteria = set()
        for key in changes:
            if key.endswith("_weight"):
                criteria.add(key.removesuffix("_weight"))
            else:
                for value in (left_params[key], right_params[key]):
                    if type(value) is str:
                        criteria.add(value.replace("_", " ").casefold())
        return len(criteria) >= 2 and all(criterion in objective for criterion in criteria)
    return False


def require_pinned_graph():
    from .langchain_tools import ensure_local_observability
    ensure_local_observability()
    for package, expected in (("langgraph", "1.2.12"), ("langchain-core", "1.6.6"),
                              ("langgraph-checkpoint-sqlite", "3.1.1")):
        try:
            actual = metadata.version(package)
        except metadata.PackageNotFoundError as error:
            raise ValueError("Independent investigations require the pinned optional LangGraph runtime") from error
        if actual != expected:
            raise ValueError("Independent investigations require the pinned optional LangGraph runtime")


class IndependentRNAWorkflow(SerialRNAWorkflow):
    def __init__(self, actions, checkpoint_path, *, investigation_backend, readonly=False):
        if type(readonly) is not bool:
            raise ValueError("Read-only replay selection must be an exact boolean")
        if readonly and (not actions.planner.methods.readonly or not actions.ledger.readonly):
            raise PermissionError("Read-only replay requires existing read-only method and ledger stores")
        self.readonly = readonly
        require_pinned_graph()
        policy = getattr(actions, "investigation_policy", None)
        if type(policy) is not dict or policy.get("format") != "devbench.rna-investigation-policy/1":
            raise ValueError("Independent investigations require an explicit frozen policy")
        budget = policy.get("budget_policy")
        if type(budget) is not dict:
            raise ValueError("Independent investigations require an explicit admitted model budget policy")
        from .rna_investigation_backend import AuditedInvestigationBackend
        if not isinstance(investigation_backend, AuditedInvestigationBackend):
            raise TypeError("Independent investigations require the trusted audited protected backend")
        self.capability = investigation_backend.require_admission(budget.get("kind"))
        self.actions, self.backend = actions, investigation_backend
        self.policy = deepcopy(policy)
        from .method_executor import _path
        self.checkpoint_path = _path(checkpoint_path)
        before = self._database_identity() if self.checkpoint_path.exists() else None
        if readonly and not self.checkpoint_path.is_file():
            raise ValueError("Read-only replay checkpoint does not exist")
        database = self.checkpoint_path.as_uri() + "?mode=ro&immutable=1" if readonly else self.checkpoint_path
        self.connection = sqlite3.connect(database, check_same_thread=False, uri=readonly)
        self.database_identity = self._database_identity()
        if before is not None and before != self.database_identity:
            self.connection.close()
            raise ValueError("Checkpoint database identity changed while opening")
        self.configuration_digest = digest({"policy": self.policy,
            "evidence": getattr(actions, "investigation_evidence", None)})
        self.frozen_policy = None
        self._compile()

    @classmethod
    def open_replay(cls, actions, checkpoint_path, *, investigation_backend):
        """Open existing stores for verification; never initialize or resume them."""
        return cls(actions, checkpoint_path, investigation_backend=investigation_backend, readonly=True)

    def _require_writer(self):
        if self.readonly:
            raise PermissionError("Read-only replay cannot continue or reconcile a workflow")

    def _compile(self):
        from langgraph.graph import StateGraph, START, END
        from langgraph.checkpoint.sqlite import SqliteSaver
        from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
        from langgraph.types import interrupt, Send

        child = StateGraph(_ChildState)
        child.add_node("private_invocation", self._launch_child)
        child.add_edge(START, "private_invocation")
        child.add_edge("private_invocation", END)
        self.child_graph = child.compile()
        builder = StateGraph(_IndependentGraphState)
        for node in ("audit", "question", "generate", "resolve", "design", "declare", "run", "verify", "recommend"):
            builder.add_node(node, lambda state, selected=node: self._parent_node(selected, state))
        for node in ("preliminary_explanation", "reassess"):
            builder.add_node(node, lambda state, selected=node: self._special_parent(selected, state))
        builder.add_node("investigation_decision", self._decision)

        def child_wrapper(state):
            # Map only private immutable invocation references into the child.
            result = self.child_graph.invoke(deepcopy(state), config={"callbacks": [], "recursion_limit": 5})
            return {"child_returns": {result["investigation_id"]: {
                "result_ref": result["result_ref"], "status": result["status"]}}}

        builder.add_node("investigate", child_wrapper)
        builder.add_node("investigation_barrier", self._barrier)
        builder.add_node("attach", self._attach)

        def pause(state, phase):
            cursor = self._cursor(state)
            self._check(cursor)
            if self._cancelled(cursor["workflow_id"]):
                return {"cursor": self._failure(cursor, phase, "cancelled", "Campaign was cancelled", status="cancelled")}
            if phase == "review" or cursor["parent_cursor"]["trial_ids"]:
                interrupt({"phase": phase, "session_id": cursor["parent_session_id"],
                    "planner_hash": cursor["expected_plan_hash"],
                    "instruction": "Review frozen drafts or declarations and supply current authority separately"})
                self._check(cursor)
            return {"cursor": {**cursor, "phase": phase, "status": "ready"}}

        builder.add_node("review", lambda state: pause(state, "review"))
        builder.add_node("execution_review", lambda state: pause(state, "execution_review"))
        builder.add_edge(START, "audit")

        def onward(state, following):
            return following if state["cursor"]["status"] == "ready" else END

        for before, after in (("audit", "question"), ("question", "generate"), ("resolve", "design"),
                              ("design", "review"), ("review", "declare"), ("declare", "execution_review")):
            builder.add_conditional_edges(before, lambda state, following=after: onward(state, following))
        builder.add_conditional_edges("generate", lambda state: onward(state,
            "investigation_decision" if self.policy["stage"] == "pre_fit_methods" else "resolve"))
        builder.add_conditional_edges("execution_review", lambda state: onward(state,
            "run" if state["cursor"]["parent_cursor"]["trial_ids"] else "verify"))
        builder.add_conditional_edges("run", lambda state: onward(state, "run" if
            state["cursor"]["parent_cursor"]["trial_index"] < len(state["cursor"]["parent_cursor"]["trial_ids"]) else "verify"))
        def after_verify(state):
            if self.policy["stage"] != "post_fit_explanation":
                return onward(state, "recommend")
            plan = self.actions.planner.get(state["cursor"]["parent_session_id"])
            if not any(row["status"] == "success" for row in plan["results"]):
                return onward(state, "investigation_decision")
            evidence = self._verified_evidence(plan)
            return onward(state, "preliminary_explanation" if evidence["assessment_available"] else "investigation_decision")
        builder.add_conditional_edges("verify", after_verify)
        builder.add_conditional_edges("preliminary_explanation", lambda state: onward(state, "investigation_decision"))

        def fork(state):
            cursor = self._cursor(state)
            if cursor["status"] != "ready":
                return END
            if not cursor["child_ids"]:
                return "resolve" if cursor["stage"] == "pre_fit_methods" else "recommend"
            return [Send("investigate", {"investigation_id": identity, "workflow_id": cursor["workflow_id"],
                "dispatch_ref": cursor["dispatch_refs"][identity]}) for identity in cursor["child_ids"]]

        builder.add_conditional_edges("investigation_decision", fork)
        builder.add_edge("investigate", "investigation_barrier")
        builder.add_conditional_edges("investigation_barrier", lambda state: onward(state, "attach"))
        builder.add_conditional_edges("attach", lambda state: onward(state,
            "reassess" if state["cursor"]["stage"] == "pre_fit_methods" else "recommend"))
        builder.add_conditional_edges("reassess", lambda state: onward(state, "resolve"))
        builder.add_edge("recommend", END)
        saver = SqliteSaver(self.connection, serde=JsonPlusSerializer(pickle_fallback=False,
            allowed_json_modules=None, allowed_msgpack_modules=None))
        if self.readonly:
            tables = {row[0] for row in self.connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if not {"checkpoints", "writes"} <= tables:
                self.connection.close()
                raise ValueError("Read-only replay checkpoint schema is unavailable")
            # The pinned saver otherwise runs setup PRAGMAs/CREATE statements.
            # Existing tables are verified above; no setup is permitted here.
            saver.is_setup = True
        self.saver = saver
        self.graph = builder.compile(checkpointer=saver)

    def _cursor(self, state):
        from .rna_investigation_contract import validate_independent_cursor
        if len(canonical_json(state)) > 64_000:
            raise ValueError("Independent graph state exceeds its 64000-byte budget")
        return validate_independent_cursor(state["cursor"])

    def _refresh(self, cursor, parent, phase, *, status=None, failure=None):
        from .rna_investigation_contract import validate_independent_cursor
        parent = validate_workflow_state(parent)
        return validate_independent_cursor({**cursor, "parent_cursor": parent,
            "expected_plan_hash": parent["expected_planner_hash"], "binding_digest": parent["binding_digest"],
            "phase": phase, "status": status or parent["status"], "failure": failure if failure is not None else parent["failure"]})

    def _failure(self, cursor, phase, kind, message, *, status="reconciliation_required"):
        parent_status = status if status in {"cancelled", "failed", "reconciliation_required"} else "failed"
        parent = {**cursor["parent_cursor"], "status": parent_status,
                  "failure": {"kind": str(kind)[:120], "message": str(message)[:500]}}
        return self._refresh(cursor, parent, phase, status=status, failure=parent["failure"])

    def _cancelled(self, identity):
        campaign = self.actions.ledger.get_campaign(identity)
        return self.actions.ledger.is_cancelled(identity) or campaign["status"] == "cancelled"

    def _check_configuration(self):
        current = digest({"policy": self.actions.investigation_policy, "evidence": self.actions.investigation_evidence})
        if current != self.configuration_digest:
            raise ValueError("Investigation policy, evidence or request configuration changed")
        actual = self.backend.require_admission(self.policy["budget_policy"]["kind"])
        if actual != self.capability:
            raise ValueError("Investigation source, runtime or backend capability changed")

    def _check(self, cursor, *, expected_hash=True):
        self._check_configuration()
        self.actions.check(cursor["parent_cursor"], expected_hash=expected_hash)
        campaign = self.actions.ledger.get_campaign(cursor["workflow_id"])
        if (campaign is None or campaign["identity"].get("configuration_digest") != self.configuration_digest
                or campaign["identity"].get("investigation_policy_digest") != cursor["policy_digest"]
                or campaign["capability"] != self.capability):
            raise ValueError("Independent workflow campaign or frozen request binding is stale")
        self.frozen_policy = deepcopy(campaign["identity"]["investigation_policy"])
        if digest(self.frozen_policy) != cursor["policy_digest"]:
            raise ValueError("Independent workflow policy bytes changed")
        return campaign

    def _freeze_campaign(self, parent):
        self._check_configuration()
        self.backend.bind_ledger(self.actions.ledger)
        plan = self.actions.check(parent)
        campaign = self.actions.ledger.get_campaign(parent["workflow_id"])
        if campaign is None:
            if plan.get("selection_history") or plan.get("selection_in_progress"):
                raise ValueError("Independent mode must open its campaign before the first parent call")
            evidence = self.actions.investigation_evidence
            if type(evidence) is not dict or type(self.policy.get("parent_call_limits")) is not dict:
                raise ValueError("Explicit verified evidence and parent model allocations are required")
            if (type(evidence.get("question_record")) is not dict
                    or evidence["question_record"].get("objective") != plan["context"]["task"]["scientific_objective"]):
                raise ValueError("Investigation question requires the exact current scientific objective")
            budget_ref = retain_investigation_json(self.actions.planner.methods, self.policy["budget_policy"],
                                                   actor=self.actions.actor, max_bytes=8000)
            frozen = {**deepcopy(self.policy), "budget_policy_ref": budget_ref,
                "source_digest": self.capability["source_digest"], "runtime_digest": self.capability["runtime_digest"],
                "evidence_digest": digest(evidence), "question_record": deepcopy(evidence["question_record"]),
                "assignment_policy": ASSIGNMENT_POLICY}
            protected = self._required_parent_allocations()
            frozen["required_parent_stages"] = list(protected)
            frozen["required_parent_allocations"] = deepcopy(protected)
            identity = {"expected_plan_hash": digest(plan), "source_digest": self.capability["source_digest"],
                "runtime_digest": self.capability["runtime_digest"], "configuration_digest": self.configuration_digest,
                "investigation_policy_digest": digest(frozen), "investigation_policy": frozen,
                "parent_call_limits": deepcopy(self.policy["parent_call_limits"])}
            held = self.policy["budget_policy"]["reconciliation"]
            if self._allocation_fits(protected, held):
                identity["protected_parent_allocations"] = deepcopy(protected)
            campaign = self.actions.ledger.open_campaign(parent["workflow_id"], session_id=parent["session_id"],
                policy=self.policy["budget_policy"], capability=self.capability, identity=identity)
        else:
            if campaign["identity"].get("configuration_digest") != self.configuration_digest:
                raise ValueError("Existing campaign has different policy or evidence bytes")
            frozen = campaign["identity"]["investigation_policy"]
        attached = self.actions.planner.attach_model_campaign(parent["session_id"], self.actions.ledger,
            parent["workflow_id"], self.backend, expected_plan_hash=digest(plan), actor=self.actions.actor)
        self.frozen_policy = deepcopy(frozen)
        return {**parent, "expected_planner_hash": digest(attached)}

    def start(self, state):
        self._require_writer()
        require_pinned_graph()
        state = validate_workflow_state(state)
        if state["phase"] != "audit" or state["operation_refs"]:
            raise ValueError("Start requires an unused exact serial parent cursor")
        config = self._config(state["workflow_id"])
        with self.invocation_lease(state["workflow_id"]):
            if self.graph.get_state(config).values:
                raise ValueError("Workflow checkpoint already exists; resume instead of restarting")
            state = self._freeze_campaign(state)
            from .rna_investigation_contract import validate_independent_cursor
            cursor = validate_independent_cursor({"format": "devbench.rna-independent-workflow/1",
                "workflow_id": state["workflow_id"], "parent_session_id": state["session_id"], "parent_cursor": state,
                "expected_plan_hash": state["expected_planner_hash"], "binding_digest": state["binding_digest"],
                "policy_digest": digest(self.frozen_policy), "stage": self.policy["stage"], "decision": None,
                "allocated_children": 0, "child_ids": [], "dispatch_refs": {}, "result_refs": {},
                "reconciliation_ref": None, "phase": "audit", "status": "ready", "failure": None})
            result = self.graph.invoke({"cursor": cursor, "child_returns": {}}, config=config)
            return self._visible(result, config)

    def _visible(self, result, config):
        cursor = self._cursor({key: result[key] for key in ("cursor", "child_returns")})
        snapshot = self.graph.get_state(config)
        if snapshot.interrupts:
            phase = snapshot.interrupts[0].value["phase"]
            return {**cursor, "phase": phase, "status": "review_required"}
        return cursor

    def resume(self, workflow_id):
        self._require_writer()
        require_pinned_graph()
        from langgraph.types import Command
        config = self._config(workflow_id)
        with self.invocation_lease(workflow_id):
            snapshot = self.graph.get_state(config)
            if not snapshot.values:
                raise ValueError("Workflow checkpoint does not exist")
            cursor = self._cursor(snapshot.values)
            if cursor["workflow_id"] != workflow_id:
                raise ValueError("Checkpoint is bound to another workflow")
            self.backend.bind_ledger(self.actions.ledger)
            self._check(cursor)
            if cursor["status"] == "cancelled":
                return cursor
            plan = self.actions.planner.get(cursor["parent_session_id"])
            self.actions.planner.attach_model_campaign(cursor["parent_session_id"], self.actions.ledger,
                workflow_id, self.backend, expected_plan_hash=digest(plan), actor=self.actions.actor)
            if cursor["status"] == "reconciliation_required":
                return cursor
            if not snapshot.next:
                self.actions.verify_terminal(cursor["parent_cursor"])
                return cursor
            result = self.graph.invoke(Command(resume="continue") if snapshot.interrupts else None, config=config)
            return self._visible(result, config)

    def _parent_node(self, node, state):
        cursor = self._cursor(state)
        try:
            self._check(cursor)
            if self._cancelled(cursor["workflow_id"]):
                return {"cursor": self._failure(cursor, node, "cancelled", "Campaign was cancelled", status="cancelled")}
            parent = self.actions.perform(node, cursor["parent_cursor"])
            if parent["status"] == "reconciliation_required" and node in _PARENT_MODEL_PHASES:
                verified = self._failed_parent_snapshot(cursor, node)
                if verified is not None:
                    parent = {**parent, "expected_planner_hash": digest(verified)}
            if self._cancelled(cursor["workflow_id"]):
                actual = self.actions.planner.get(cursor["parent_session_id"])
                parent = {**parent, "expected_planner_hash": digest(actual), "status": "cancelled", "outcome_ref": None,
                    "failure": {"kind": "cancelled", "message": "Campaign cancelled; actual model tails remain captured and charged"}}
            return {"cursor": self._refresh(cursor, parent, node)}
        except Exception as error:
            return {"cursor": self._captured_parent_failure(cursor, node, error)}

    def _captured_parent_failure(self, cursor, node, error):
        failed = self._failure(cursor, node, type(error).__name__, str(error))
        try:
            verified = self._failed_parent_snapshot(cursor, node)
        except Exception as invalid:
            return self._failure(cursor, node, type(invalid).__name__, str(invalid))
        if verified is not None:
            parent = {**failed["parent_cursor"], "expected_planner_hash": digest(verified)}
            return self._refresh(failed, parent, node)
        return failed

    def _failed_parent_snapshot(self, cursor, node):
        """Derive a nonaccepted snapshot from exact retained dispatch evidence.

        This is verification only: no selection, inspection write, settlement,
        scientific acceptance, or operation completion is performed. A changed
        hash alone never authorizes rebinding a failed cursor.
        """
        if node not in _PARENT_MODEL_PHASES:
            return None
        record = self.actions.ledger.get(cursor["workflow_id"], node, 0)
        if record is None or record["status"] not in {"active", "uncertain", "failed"}:
            return None
        request = record["request"]
        special = node in {"reassess", "preliminary_explanation"}
        if (request.get("session_id") != cursor["parent_session_id"]
                or request.get("binding_digest") != cursor["binding_digest"]
                or (special and (set(request) != {"session_id", "expected_plan_hash", "binding_digest", "policy_digest"}
                                 or request["policy_digest"] != cursor["policy_digest"]))
                or (not special and request.get("artifact_refs") != cursor["parent_cursor"]["artifact_refs"])):
            raise ValueError("Failed parent operation differs from its exact workflow bindings")
        methods = self.actions.planner.methods
        before_hash = request["expected_plan_hash" if special else "expected_planner_hash"]
        before = methods.get("method_plan_snapshot", before_hash)
        actual = self.actions.check(cursor["parent_cursor"], expected_hash=False)
        if (digest(before) != before_hash or planning_binding(before) != cursor["binding_digest"]
                or cursor["expected_plan_hash"] not in {digest(before), digest(actual)}):
            raise ValueError("Failed parent cursor has a stale before/current snapshot binding")
        old, history = before["selection_history"], actual["selection_history"]
        if (len(history) != len(old) + 1 or history[:-1] != old or actual.get("selection_in_progress") is not None
                or history[-1]["status"] not in {"failed", "returned_unvalidated"} or history[-1]["phase"] != _PARENT_MODEL_PHASES[node]):
            return None
        selection = history[-1]
        selector_digest = digest(self.actions.selector.configuration()) if special else request.get("selector_digest")
        if (digest(selection["selector"]) != selector_digest
                or not selection.get("model_reservation_ref") or not selection.get("model_capture_ref")
                or not selection.get("model_usage_ref")):
            return None
        reservation = self.actions.ledger.get_model_reservation(selection["model_reservation_ref"])
        call = reservation["request"]
        if (reservation["campaign_id"] != cursor["workflow_id"] or reservation["capture"] is None
                or call.get("purpose") != _PARENT_MODEL_PHASES[node] or call.get("session_id") != selection["attempt"]
                or call.get("invocation_id") != selection["attempt"]
                or call.get("context_digest") != selection["context_sha256"]):
            raise ValueError("Failed selection differs from its actual reserved parent dispatch")
        campaign = self.actions.ledger.get_campaign(cursor["workflow_id"])
        handle = self.actions.ledger._reservation(reservation, campaign)
        receipt = self.backend.verify_capture(selection["model_capture_ref"], handle)
        usage = read_investigation_json(methods, selection["model_usage_ref"], max_bytes=24_000)
        if (receipt != reservation["capture"] or usage.get("capture_ref") != selection["model_capture_ref"]
                or usage.get("receipt") != receipt or selection.get("model_usage") != receipt["usage"]
                or selection.get("native_session_ids") != receipt["native_session_ids"]):
            raise ValueError("Failed selection capture or usage differs from its actual retained charge")
        manifest = read_investigation_json(methods, selection["model_capture_ref"])
        if "response" in selection:
            response_ref = manifest.get("response_ref", manifest.get("files", {}).get("model/answer.json"))
            if response_ref is None or manifest.get("response_digest") is None:
                raise ValueError("Failed selection response has no actual immutable capture")
            from devbench.study.evidence import decode_json
            captured_response = decode_json(self.backend._artifact(response_ref, max_bytes=4_000_000))
            if (digest(captured_response) != manifest["response_digest"]
                    or digest(selection["response"]) != manifest["response_digest"]):
                raise ValueError("Failed selection response differs from its actual captured bytes")
        elif selection["status"] == "returned_unvalidated":
            raise ValueError("Returned parent selection is missing its actual captured response")
        selecting = methods.get("method_plan_snapshot", call["plan_hash"])
        if digest(selecting) != call["plan_hash"] or selecting["selection_history"] != old:
            raise ValueError("Failed selection lacks its exact retained pre-dispatch snapshot")
        allowed = {"candidates", "recommendation_evidence_assessment"} if node == "recommend" else set()
        if any(before.get(key) != selecting.get(key) for key in (set(before) | set(selecting)) - allowed):
            raise ValueError("Planner changed outside the failed parent operation")
        if node == "recommend":
            self._verify_failed_recommendation_assessment(before, selecting)
        expected = {**deepcopy(selecting), "selection_history": deepcopy(history), "selection_in_progress": None}
        if actual != expected or methods.get("method_plan_snapshot", digest(actual)) != actual:
            raise ValueError("Planner changed outside its retained failed selection history")
        return actual

    def _verify_failed_recommendation_assessment(self, before, selecting):
        """Check the ordinary pre-selection assessment without writing inspections."""
        from .method_planner import _applicability, ACTION_PREREQUISITE_POLICIES
        summary = before["context"]["dataset_summary"]
        candidates = []
        for candidate in before["candidates"]:
            expected = _applicability(candidate, summary)
            if "action_scope" in candidate:
                numeric = set(ACTION_PREREQUISITE_POLICIES["retain"])
                computable = numeric | {"measurement_rank", "measurement_variance_positive"}
                requested = numeric | {row["field"] for row in candidate["assumptions"] if row["field"] in computable}
                requested |= {row["field"] for row in summary["unknowns"] if row["field"] in computable}
                bulk = summary["observed"].get("bulk_rna_input")
                if bulk is None:
                    raise ValueError("Failed recommendation lacks a supported retained bulk RNA inspection")
                inspection = {"origin": "devbench.bulk-rna-inspection/1", "inspection_kind": "bulk_rna_input",
                    "input_artifact_ids": sorted(set(summary.get("artifact_ids", []))), "observed": {},
                    "bulk_rna_inspection_artifact_ref": bulk.get("inspection_artifact_ref"),
                    "requested_fields": sorted(requested), "failures": []}
                identity = digest(inspection)
                if self.actions.planner.methods.get("method_prerequisite_inspection", identity) != inspection:
                    raise ValueError("Failed recommendation prerequisite inspection changed")
                expected["prerequisite_inspections"] = [identity]
            candidates.append(expected)
        if selecting["candidates"] != candidates:
            raise ValueError("Failed recommendation changed its scientific candidates outside the deterministic assessment")
        self.actions.planner._verified_results(selecting)
        if selecting.get("recommendation_evidence_assessment") != self.actions.planner._evidence_assessment(selecting):
            raise ValueError("Failed recommendation evidence assessment differs from actual retained results")

    def _special_parent(self, node, state):
        cursor = self._cursor(state)
        try:
            self._check(cursor)
            if self._cancelled(cursor["workflow_id"]):
                return {"cursor": self._failure(cursor, node, "cancelled", "Campaign was cancelled", status="cancelled")}
            method = self.actions.planner.reassess_candidates if node == "reassess" else self.actions.planner.nominate_explanation
            request = {"session_id": cursor["parent_session_id"], "expected_plan_hash": cursor["expected_plan_hash"],
                       "binding_digest": cursor["binding_digest"], "policy_digest": cursor["policy_digest"]}
            def callback():
                self._check(cursor)
                kwargs = {"actor": self.actions.actor, "allow_model_execution": self.actions.allow_model_execution}
                if node == "preliminary_explanation":
                    from .rna_investigation_contract import build_investigation_packet
                    from .rna_investigation_projection import verify_packet_projection
                    plan = self.actions.planner.get(cursor["parent_session_id"])
                    evidence = self._verified_evidence(plan)
                    if not evidence["assessment_available"]:
                        raise ValueError("Actual fitted statistical coverage is absent; no preliminary explanation is dispatched")
                    packet = build_investigation_packet(plan, evidence, stage=cursor["stage"],
                        task="parent_verified_output_view", policy={**self.frozen_policy, "policy_digest": cursor["policy_digest"]})
                    kwargs["verified_evidence"] = verify_packet_projection(self.actions.planner.methods, packet, plan)
                method(cursor["parent_session_id"], self.actions.selector, **kwargs)
                return {"before_plan_hash": cursor["expected_plan_hash"],
                        "after_plan_hash": digest(self.actions.planner.get(cursor["parent_session_id"]))}
            result = self.actions.ledger.run(cursor["workflow_id"], node, 0, request, callback)
            if digest(self.actions.planner.get(cursor["parent_session_id"])) != result["after_plan_hash"]:
                raise ValueError("Completed parent operation differs from the actual planner snapshot")
            parent = {**cursor["parent_cursor"], "expected_planner_hash": result["after_plan_hash"]}
            return {"cursor": self._refresh(cursor, parent, node)}
        except Exception as error:
            return {"cursor": self._captured_parent_failure(cursor, node, error)}

    def _verified_evidence(self, plan=None):
        value = deepcopy(self.actions.investigation_evidence)
        value["assessment_available"] = self.policy["stage"] == "pre_fit_methods"
        for entry in value.get("verified_artifacts", []):
            actual = read_investigation_json(self.actions.planner.methods, entry["ref"], max_bytes=128_000)
            if "value" in entry and actual != entry["value"]:
                raise ValueError("Coordinator scientific evidence bytes changed")
        for reference in value.get("diagnostic_refs", []) + value.get("evidence_refs", []):
            manifest = self.actions.planner.methods.get("artifact", reference["artifact_id"])
            self.actions.planner.methods.artifact_path(reference["artifact_id"])
            if {key: manifest[key] for key in reference} != reference:
                raise ValueError("Coordinator evidence manifest changed")
        if self.policy["stage"] == "post_fit_explanation" and plan is not None and plan.get("results"):
            from .rna_investigation_projection import project_verified_outputs
            overlay = project_verified_outputs(self.actions.planner.methods, plan,
                policy={**self.frozen_policy, "policy_digest": digest(self.frozen_policy)})
            for category, records in overlay["facts"].items():
                by_field = {record["field"]: record for record in value["facts"][category]}
                for record in records:
                    if record["field"] in by_field and by_field[record["field"]] != record:
                        raise ValueError("Frozen scientific input conflicts with actual verified output coverage")
                    by_field[record["field"]] = record
                value["facts"][category] = list(by_field.values())
            for key in ("evidence_refs", "diagnostic_refs"):
                refs = {reference["artifact_id"]: reference for reference in value[key]}
                for reference in overlay[key]:
                    if reference["artifact_id"] in refs and refs[reference["artifact_id"]] != reference:
                        raise ValueError("Verified output reference conflicts with a retained input")
                    refs[reference["artifact_id"]] = reference
                value[key] = list(refs.values())
            value["verified_artifacts"] += overlay["verified_artifacts"]
            value["assessment_available"] = overlay["assessment_available"]
        return value

    def _nomination(self, plan, evidence):
        if self.policy["stage"] == "pre_fit_methods":
            from .method_planner import _applicability
            candidates = [candidate for candidate in plan.get("scientific_candidates", [])
                if _applicability(candidate, plan["context"]["dataset_summary"])["applicability"] == "eligible"]
            choices = {}
            requirements = {row["name"]: row["value"] for row in plan.get("user_parameter_requirements", [])}
            for candidate in candidates:
                for variant in _scientific_variants(candidate):
                    if any(row["name"] in requirements and canonical_json(row["value"]) != canonical_json(requirements[row["name"]])
                           for row in variant.get("parameter_candidates", [])):
                        continue
                    choices.setdefault(scientific_choice_identity(variant), variant)
            references = {row["artifact_id"]: row for row in evidence["evidence_refs"]}
            items = []
            for item in deepcopy(self.frozen_policy.get("ambiguity_items", [])):
                ids = item["hypothesis_ids"]
                if (len(ids) == 2 and ids[0] != ids[1] and all(identity in choices for identity in ids)
                        and all(references.get(ref["artifact_id"]) == ref for ref in item["evidence_refs"])
                        and _axis_choices(item["axis"], choices[ids[0]], choices[ids[1]], plan, evidence)):
                    items.append(item)
            rules = self.frozen_policy.get("ambiguity_rules", [])
            if type(rules) is not list or len(rules) > 2:
                raise ValueError("Use at most two frozen observable ambiguity rules")
            for rule in rules:
                if (type(rule) is not dict or set(rule) != {"id", "axis", "evidence_refs", "discriminating_criterion"}
                        or not rule["evidence_refs"]
                        or any(references.get(ref["artifact_id"]) != ref for ref in rule["evidence_refs"])):
                    raise ValueError("Ambiguity rules require exact frozen observed evidence and comparison criteria")
                for left, right in itertools.combinations(sorted(choices), 2):
                    if _axis_choices(rule["axis"], choices[left], choices[right], plan, evidence):
                        items.append({**deepcopy(rule), "hypothesis_ids": [left, right], "missing_information_status": "sufficient"})
                        break
            if len(items) > 2:
                raise ValueError("At most two distinct admissible scientific investigations are supported")
            if len({(row["axis"], tuple(row["hypothesis_ids"]), row["discriminating_criterion"]) for row in items}) != len(items):
                raise ValueError("Duplicated scientific alternatives do not justify a second investigation")
            phase = "method_scientific_planning"
        else:
            explanation = plan.get("preliminary_explanation", {}).get("response", {})
            items = deepcopy(explanation.get("ambiguity_items", []))
            references = {row["artifact_id"]: row for row in evidence["evidence_refs"]}
            items = [item for item in items if item["evidence_refs"] and all(
                references.get(ref["artifact_id"]) == ref for ref in item["evidence_refs"])]
            phase = "method_preliminary_explanation"
        history = [row for row in plan["selection_history"] if row["phase"] == phase and row["status"] == "accepted"]
        if not history or not items:
            return [], None, None
        selection = history[-1]
        recorded = {"capture_ref": selection.get("model_capture_ref"), "usage_ref": selection.get("model_usage_ref")}
        if not recorded["capture_ref"] or not recorded["usage_ref"] or not selection.get("model_reservation_ref"):
            raise ValueError("A nomination requires actual captured and charged parent evidence")
        retained = self.actions.ledger.get_model_reservation(selection["model_reservation_ref"])
        campaign = self.actions.ledger.get_campaign(plan["session_id"])
        reservation = self.actions.ledger._reservation(retained, campaign)
        verified = self.backend.verify_capture(recorded["capture_ref"], reservation)
        if retained["status"] != "completed" or retained["capture"] != verified:
            raise ValueError("Parent nomination lacks an exact settled shared-campaign capture")
        usage = read_investigation_json(self.actions.planner.methods, recorded["usage_ref"], max_bytes=24_000)
        if usage.get("capture_ref") != recorded["capture_ref"] or usage.get("receipt") != verified:
            raise ValueError("Parent nomination usage differs from the actual captured call")
        nomination = {"parent_session_id": plan["session_id"], "selection_attempt": selection["attempt"],
            "context_sha256": selection["context_sha256"], "capture_ref": recorded["capture_ref"],
            "usage_ref": recorded["usage_ref"], "ambiguity_items_sha256": digest(items)}
        return items, retain_investigation_json(self.actions.planner.methods, nomination, actor=self.actions.actor,
                                                max_bytes=8000), nomination

    def _decision(self, state):
        cursor = self._cursor(state)
        try:
            campaign = self._check(cursor)
            if self._cancelled(cursor["workflow_id"]):
                return {"cursor": self._failure(cursor, "investigation_decision", "cancelled", "Campaign was cancelled", status="cancelled")}
            plan = self.actions.planner.get(cursor["parent_session_id"])
            request = {"parent_plan_hash": digest(plan), "binding_digest": cursor["binding_digest"],
                       "policy_digest": cursor["policy_digest"]}
            def callback():
                from .rna_investigation_contract import build_investigation_packet, investigation_prerequisites
                from .workflow_operations import model_reservation_ref
                evidence = self._verified_evidence(plan)
                task = "independent_method_investigation" if cursor["stage"] == "pre_fit_methods" else "independent_explanation_investigation"
                neutral = build_investigation_packet(plan, evidence, stage=cursor["stage"], task=task,
                    policy={**self.frozen_policy, "policy_digest": cursor["policy_digest"]})
                from .rna_investigation_projection import verify_packet_projection
                neutral = verify_packet_projection(self.actions.planner.methods, neutral, plan)
                prerequisites = investigation_prerequisites(neutral)
                items, nomination_ref, nomination = self._nomination(plan, evidence)
                scope = evidence["question_record"]["action"]
                observed = {row["field"]: row["value"] for row in neutral["facts"]["observed"]}
                declared = {row["field"]: row["value"] for row in neutral["facts"]["declared"]}
                blockers = set(prerequisites["blocking"])
                groups = {"input_integrity": {"input_integrity_valid", "gene_ids_unique", "sample_ids_unique", "measurements_finite"},
                    "join": {"alignment_valid"}, "representation": {"representation", "count_values_valid"},
                    "biological_units": {"biological_independence", "biological_unit_identity_known", "biological_units"},
                    "contrast": {"contrast_valid", "age_batch_confounded"}, "rank": {"design_rank_valid", "age_batch_confounded"},
                    "chronology": {"chronology_valid"}}
                checks = []
                for name in ("input_integrity", "representation", "join", "biological_units", "contrast", "rank", "chronology"):
                    fields = groups[name]
                    if scope in {"retain", "diagnostic_flagging"} and name in {"biological_units", "contrast", "rank", "chronology"}:
                        status = "not_applicable"
                    elif fields & blockers:
                        status = "failed" if any(field in observed or field in declared for field in fields & blockers) else "unknown"
                    else:
                        status = "satisfied"
                    checks.append({"check_id": name, "status": status,
                                   "evidence_ref": neutral["diagnostic_refs"][0], "action_scope": scope})
                question_ref = retain_investigation_json(self.actions.planner.methods, evidence["question_record"],
                                                         actor=self.actions.actor, max_bytes=8000)
                requirements = [{"requirement": record, "ref": retain_investigation_json(self.actions.planner.methods,
                    record, actor=self.actions.actor, max_bytes=8000)} for record in plan.get("user_parameter_requirements", [])]
                fixed = next((row["ref"] for row in requirements if row["requirement"]["name"] == "fixed_procedure"), None)
                admitted = min(self.frozen_policy["allocation"], campaign["policy"]["max_children"], len(items))
                child_limits = self.frozen_policy.get("child_call_limits")
                if admitted and type(child_limits) is not dict:
                    raise ValueError("Children require explicit frozen shared-campaign call allocations")
                if admitted:
                    held = campaign["reconciliation_held"]
                    policy = campaign["policy"]
                    token_cap = policy["max_total_tokens"] if policy["kind"] == "hard_total_tokens" else policy["soft_token_target"]
                    remaining = {"tokens": token_cap - campaign["verified_spent_tokens"] - campaign["outstanding_reserved_tokens"] - held["tokens"],
                        "calls": policy["max_calls"] - campaign["verified_calls"] - campaign["outstanding_reserved_calls"] - held["calls"],
                        "seconds": policy["max_total_seconds"] - campaign["verified_seconds"] - campaign["outstanding_reserved_seconds"] - held["seconds"]}
                    admitted = min(admitted, *(max(0, int(remaining[key] // child_limits[key])) for key in remaining))
                features = {"format": "devbench.rna-investigation-trigger/1", "stage": cursor["stage"],
                    "action_intent": scope, "scope_binding": planning_binding(plan), "question_ref": question_ref,
                    "requirement_refs": [row["ref"] for row in requirements], "prerequisite_checks": checks,
                    "fixed_procedure_requirement_ref": fixed, "nomination_ref": nomination_ref, "ambiguity_items": items,
                    "evidence_ready": bool(neutral["evidence_refs"]) and evidence["assessment_available"],
                    "prior_fork_count": 1 if campaign["fork"] else 0,
                    "budget_policy_ref": self.frozen_policy["budget_policy_ref"], "admissible_child_count": admitted}
                diagnostics = {"features": features, "nomination": nomination, "requirement_records": requirements,
                    "scope_supported": True, "capability_supported": True,
                    "parent_reconciliation_reserved": self._parent_capacity_reserved(campaign),
                    "unresolved_dispatch": bool(campaign["unresolved_count"])}
                decision = decide_investigation(plan, diagnostics, policy=self.frozen_policy)
                identities = [digest({"parent": cursor["workflow_id"], "stage": cursor["stage"],
                    "policy_digest": cursor["policy_digest"], "ordinal": index})[:32] for index in range(decision["allocated_children"])]
                packets = []
                for identity, selected in zip(identities, decision["selected_ambiguity_ids"]):
                    packet = build_investigation_packet(plan, {**evidence, "investigation_id": identity},
                        stage=cursor["stage"], task=task, policy={**self.frozen_policy, "policy_digest": cursor["policy_digest"]})
                    packet = compose_investigation_task(self.actions.planner.methods, packet, decision,
                        selected_item_id=selected, actor=self.actions.actor)
                    packet = verify_packet_projection(self.actions.planner.methods, packet, plan)
                    packets.append(self.backend.retain_packet(packet))
                self.actions.ledger.reserve_investigation_fork(cursor["workflow_id"],
                    fork_id=digest({"parent": cursor["workflow_id"], "policy": cursor["policy_digest"]})[:32],
                    stage=cursor["stage"], child_ids=identities,
                    identity={"expected_plan_hash": digest(plan), "decision_digest": digest(decision),
                              "packet_refs": packets, "binding_digest": cursor["binding_digest"]})
                refs = [model_reservation_ref(cursor["workflow_id"], "investigator_" + identity, 0) for identity in identities]
                prepared = self.backend.prepare_requests(packets, budget_policy=self.policy["budget_policy"]["kind"],
                    reservation_refs=refs) if identities else []
                for identity, row in zip(identities, prepared):
                    self.actions.ledger.reserve_model_call(cursor["workflow_id"], "investigator_" + identity, 0,
                        row["binding"], limits=self.frozen_policy["child_call_limits"],
                        purpose="independent_investigation", child_id=identity)
                return {"decision": decision, "child_ids": identities,
                    "dispatch_refs": {identity: row["invocation_ref"] for identity, row in zip(identities, prepared)}}
            result = self.actions.ledger.run(cursor["workflow_id"], "investigation_decision", 0, request, callback)
            updated = {**cursor, **result, "allocated_children": result["decision"]["allocated_children"],
                       "phase": "investigation_decision", "status": "blocked" if result["decision"]["status"] == "blocked" else "ready"}
            from .rna_investigation_contract import validate_independent_cursor
            return {"cursor": validate_independent_cursor(updated)}
        except Exception as error:
            return {"cursor": self._failure(cursor, "investigation_decision", type(error).__name__, str(error))}

    def _parent_capacity_reserved(self, campaign):
        allocations = self._required_parent_allocations()
        if campaign["identity"].get("protected_parent_allocations") != allocations:
            return False
        return self._allocation_fits(allocations, campaign["reconciliation_held"])

    def _required_parent_allocations(self):
        phases = (["method_candidates_reassessment", "method_capability_resolution", "method_trial_design", "method_recommendation"]
                  if self.policy["stage"] == "pre_fit_methods" else ["method_recommendation"])
        declared = self.policy["parent_call_limits"]
        allocations = {}
        for phase in phases:
            limits = declared.get(phase, declared.get("default"))
            if type(limits) is not dict:
                raise ValueError("Every future protected parent stage requires a frozen allocation")
            allocations[phase] = deepcopy(limits)
        return allocations

    @staticmethod
    def _allocation_fits(allocations, held):
        required = {key: 0 for key in ("tokens", "calls", "seconds")}
        context = 0
        for limits in allocations.values():
            for key in required:
                required[key] += limits[key]
            context = max(context, limits["context_bytes"])
        return all(held[key] >= number for key, number in required.items()) and held["context_bytes"] >= context

    def _launch_child(self, state):
        identity = state["investigation_id"]
        self._check_configuration()
        descriptor = self.backend.read_dispatch(state["dispatch_ref"])
        if descriptor["request"]["investigation_id"] != identity or descriptor["request"]["parent_session_id"] != state["workflow_id"]:
            raise ValueError("Child dispatch is bound to another parent or investigation")
        packet = self.backend.read_packet(descriptor["request"]["packet_ref"])
        before = self.actions.planner.methods.get("method_plan_snapshot", packet["bindings"]["plan_hash"])
        verify_investigation_assignment(self.actions.planner.methods, packet, before, ledger=self.actions.ledger)
        try:
            from .rna_investigation_backend import InvestigationExecutionAuthority
            authority = self.actions.investigation_authority
            if not isinstance(authority, InvestigationExecutionAuthority) or self.actions.allow_model_execution is not True:
                raise PermissionError("Current child model authorization is required")
            reservation = self.actions.ledger.reserve_model_call(state["workflow_id"], "investigator_" + identity, 0,
                descriptor["binding"], limits=self.frozen_policy["child_call_limits"],
                purpose="independent_investigation", child_id=identity)
            if self._cancelled(state["workflow_id"]) or reservation.status != "reserved":
                outcome = self.backend.reconcile_capture(state["dispatch_ref"])
            else:
                reservation = self.actions.ledger.activate_model_call(reservation)
                outcome = self.backend.investigate(descriptor["request"], execution_authority=authority, reservation=reservation)
                if outcome.get("capture_ref"):
                    self.actions.ledger.settle_model_call(reservation, capture=outcome["capture_ref"],
                                                         verify_capture=self.backend.verify_capture)
                else:
                    self.actions.ledger.mark_model_call_uncertain(reservation, error_type="capture_unavailable")
            status = outcome["status"] if outcome["status"] in {"completed", "abstained", "failed", "incomplete"} else "incomplete"
            return {**state, "result_ref": outcome.get("result_ref"), "status": status}
        except Exception:
            # Preserve actual backend state; a provider may have already launched.
            outcome = self.backend.reconcile_capture(state["dispatch_ref"])
            if not outcome.get("capture_ref"):
                retained = self.actions.ledger.get_model_reservation(state["workflow_id"], "investigator_" + identity, 0)
                if retained["status"] in {"reserved", "active"}:
                    handle = self.actions.ledger._reservation(retained, self.actions.ledger.get_campaign(state["workflow_id"]))
                    self.actions.ledger.mark_model_call_uncertain(handle, error_type="capture_unavailable")
            return {**state, "result_ref": outcome.get("result_ref"), "status": "incomplete"}

    def _barrier(self, state):
        cursor = self._cursor(state)
        barrier_binding = digest({"children": cursor["child_ids"], "returns": state["child_returns"]})
        self.actions.ledger._observe(cursor["workflow_id"], "investigation_barrier", 0,
            barrier_binding, "reservation", "active")
        collected = reduce_investigation_refs({}, state["child_returns"])
        self.actions.ledger._observe(cursor["workflow_id"], "investigation_barrier", 0,
            barrier_binding, "verification",
            "completed" if set(collected)==set(cursor["child_ids"]) and all(
                row["status"] != "incomplete" for row in collected.values()) else "incomplete")
        if set(collected) != set(cursor["child_ids"]):
            return {"cursor": self._failure(cursor, "investigation_barrier", "incomplete", "Terminal child barrier is incomplete")}
        updated = {**cursor, "result_refs": {identity: row["result_ref"] for identity, row in collected.items()
                                             if row["result_ref"] is not None}, "phase": "investigation_barrier"}
        if self._cancelled(cursor["workflow_id"]):
            return {"cursor": self._failure(updated, "investigation_barrier", "cancelled",
                                             "Campaign was cancelled; actual child tails remain retained and charged", status="cancelled")}
        if any(row["status"] == "incomplete" for row in collected.values()):
            updated = self._failure(updated, "investigation_barrier", "incomplete", "Incomplete child calls require explicit reconciliation")
        return {"cursor": updated}

    def _attach(self, state):
        cursor = self._cursor(state)
        try:
            self._check(cursor)
            request = {"expected_plan_hash": cursor["expected_plan_hash"], "dispatch_refs": cursor["dispatch_refs"],
                       "result_refs": cursor["result_refs"], "policy_digest": cursor["policy_digest"]}
            def callback():
                self._check(cursor)
                plan = self.actions.planner.get(cursor["parent_session_id"])
                packets, results = [], []
                for identity in cursor["child_ids"]:
                    dispatch = self.backend.read_dispatch(cursor["dispatch_refs"][identity])
                    packet_ref = dispatch["request"]["packet_ref"]
                    packets.append({"ref": packet_ref, "value": self.backend.read_packet(packet_ref),
                                    "dispatch_ref": cursor["dispatch_refs"][identity], "error": None})
                    if identity in cursor["result_refs"]:
                        result_ref = cursor["result_refs"][identity]
                        results.append({"ref": result_ref, "value": self.backend.verify_child_result(packet_ref, result_ref)})
                bundle = reconcile_investigations(plan, results, packets=packets,
                    verified_evidence=self._verified_evidence(plan)["evidence_refs"])
                bundle_ref = retain_investigation_json(self.actions.planner.methods, bundle, actor=self.actions.actor,
                                                       max_bytes=24_000)
                actual = self.actions.planner.attach_investigation_evidence(cursor["parent_session_id"], bundle_ref,
                    expected_plan_hash=cursor["expected_plan_hash"], actor=self.actions.actor)
                return {"bundle_ref": bundle_ref, "before_plan_hash": cursor["expected_plan_hash"], "after_plan_hash": digest(actual)}
            record = self.actions.ledger.run(cursor["workflow_id"], "attach", 0, request, callback)
            if digest(self.actions.planner.get(cursor["parent_session_id"])) != record["after_plan_hash"]:
                raise ValueError("Parent evidence attachment differs from its authoritative planner snapshot")
            parent = {**cursor["parent_cursor"], "expected_planner_hash": record["after_plan_hash"]}
            return {"cursor": {**self._refresh(cursor, parent, "attach"), "reconciliation_ref": record["bundle_ref"]}}
        except Exception as error:
            return {"cursor": self._failure(cursor, "attach", type(error).__name__, str(error))}

    def reconcile(self, workflow_id):
        """Recover verified effects or retain uncertainty; this method never dispatches."""
        self._require_writer()
        require_pinned_graph()
        config = self._config(workflow_id)
        with self.invocation_lease(workflow_id):
            snapshot = self.graph.get_state(config)
            if not snapshot.values:
                self._restore_checkpoint(workflow_id, config)
                snapshot = self.graph.get_state(config)
            cursor = self._cursor(snapshot.values)
            if cursor["workflow_id"] != workflow_id:
                raise ValueError("Only an exact uncertain independent checkpoint can be reconciled")
            self.backend.bind_ledger(self.actions.ledger)
            self._check(cursor, expected_hash=False)
            if cursor["status"] == "reconciliation_required" and cursor["phase"] in _PARENT_MODEL_PHASES:
                verified = self._failed_parent_snapshot(cursor, cursor["phase"])
                if verified is not None:
                    parent = {**cursor["parent_cursor"], "expected_planner_hash": digest(verified)}
                    updated = self._refresh(cursor, parent, cursor["phase"])
                    if updated != cursor:
                        self.graph.update_state(config, {"cursor": updated}, as_node=cursor["phase"])
                    return updated  # A captured failure is never a completed operation.
            if cursor["status"] != "reconciliation_required":
                self._check(cursor)
                return cursor
            if cursor["phase"] in {"investigation_barrier", "investigate"}:
                collected = {}
                for identity in cursor["child_ids"]:
                    outcome = self.backend.reconcile_capture(cursor["dispatch_refs"][identity])
                    if outcome["status"] not in {"completed", "abstained", "failed"}:
                        retained = self.actions.ledger.get_model_reservation(workflow_id, "investigator_" + identity, 0)
                        if retained["status"] in {"reserved", "active"} and not outcome.get("capture_ref"):
                            handle = self.actions.ledger._reservation(retained, self.actions.ledger.get_campaign(workflow_id))
                            self.actions.ledger.mark_model_call_uncertain(handle, error_type="capture_unavailable")
                        return cursor
                    descriptor = self.backend.read_dispatch(cursor["dispatch_refs"][identity])
                    if not outcome.get("capture_ref") or not outcome.get("result_ref"):
                        return cursor
                    retained = self.actions.ledger.get_model_reservation(workflow_id, "investigator_" + identity, 0)
                    handle = self.actions.ledger._reservation(retained, self.actions.ledger.get_campaign(workflow_id))
                    if retained["status"] in {"completed", "failed"}:
                        if self.backend.verify_capture(outcome["capture_ref"], handle) != retained["capture"]:
                            raise ValueError("Retained child settlement differs from its actual capture")
                    else:
                        self.actions.ledger.reconcile_model_call(handle, capture=outcome["capture_ref"],
                                                                verify_capture=self.backend.verify_capture)
                    self.backend.verify_child_result(descriptor["request"]["packet_ref"], outcome["result_ref"])
                    collected[identity] = {"result_ref": outcome.get("result_ref"), "status": outcome["status"]}
                updated = {**cursor, "status": "ready", "failure": None,
                    "parent_cursor": {**cursor["parent_cursor"], "status": "ready", "failure": None},
                    "result_refs": {key: row["result_ref"] for key, row in collected.items() if row["result_ref"] is not None}}
                from langgraph.types import Overwrite
                # Only explicit verified recovery may replace an incomplete
                # channel; ordinary concurrent returns still reject conflicts.
                self.graph.update_state(config, {"cursor": updated, "child_returns": Overwrite(collected)}, as_node="investigation_barrier")
                return updated
            if cursor["phase"] == "attach":
                plan = self.actions.planner.get(cursor["parent_session_id"])
                links = plan.get("investigation_evidence", [])
                if len(links) != 1 or links[0]["attachment_before_hash"] != cursor["expected_plan_hash"]:
                    return cursor
                from .rna_investigations import verify_investigation_bundle
                original = self.actions.planner.methods.get("method_plan_snapshot", cursor["expected_plan_hash"])
                def verify(packet_ref, result_ref, *, dispatch_ref=None):
                    return (self.backend.verify_child_result(packet_ref, result_ref) if result_ref is not None
                            else self.backend.verify_incomplete_child(packet_ref, dispatch_ref))
                verify_investigation_bundle(self.actions.planner.methods, links[0]["bundle_ref"], plan=original,
                    verify_child=verify)
                if any(original.get(key) != plan.get(key) for key in (set(original) | set(plan)) - {"investigation_evidence"}):
                    raise ValueError("Parent changed outside the uncertain attachment")
                result = {"bundle_ref": links[0]["bundle_ref"], "before_plan_hash": cursor["expected_plan_hash"],
                          "after_plan_hash": digest(plan)}
                self.actions.ledger.reconcile(workflow_id, "attach", 0, result)
                parent = {**cursor["parent_cursor"], "status": "ready", "failure": None, "expected_planner_hash": digest(plan)}
                updated = {**self._refresh(cursor, parent, "attach"), "reconciliation_ref": links[0]["bundle_ref"]}
                self.graph.update_state(config, {"cursor": updated}, as_node="attach")
                return updated
            if cursor["phase"] in {"reassess", "preliminary_explanation"}:
                updated = self._reconcile_parent(cursor)
                if updated["status"] == "ready":
                    self.graph.update_state(config, {"cursor": updated}, as_node=cursor["phase"])
                return updated
            if cursor["phase"] not in {"reassess", "preliminary_explanation", "investigation_decision"}:
                parent = self.actions.reconcile(cursor["parent_cursor"])
                updated = self._refresh(cursor, parent, cursor["phase"])
                self.graph.update_state(config, {"cursor": updated}, as_node=cursor["phase"])
                return updated
            # No caller-authored outcome or speculative resynthesis can resolve it.
            return cursor

    def _reconcile_parent(self, cursor):
        node = cursor["phase"]
        record = self.actions.ledger.get(cursor["workflow_id"], node, 0)
        if record is None or record["status"] not in {"active", "uncertain"}:
            raise ValueError("Parent recovery requires its exact retained uncertain operation")
        before = self.actions.planner.methods.get("method_plan_snapshot", cursor["expected_plan_hash"])
        current = self.actions.planner.get(cursor["parent_session_id"])
        allowed = {"selection_history", "selection_in_progress"} | ({"scientific_candidates", "candidate_reassessment"}
                   if node == "reassess" else {"preliminary_explanation"})
        if (record["request"]["expected_plan_hash"] != cursor["expected_plan_hash"]
                or any(before.get(key) != current.get(key) for key in (set(before) | set(current)) - allowed)
                or current.get("selection_in_progress") is not None):
            raise ValueError("Planner changed outside the exact uncertain parent operation")
        old, history = before["selection_history"], current["selection_history"]
        phase = "method_candidates_reassessment" if node == "reassess" else "method_preliminary_explanation"
        if len(history) != len(old) + 1 or history[:-1] != old or history[-1]["status"] != "accepted" or history[-1]["phase"] != phase:
            return cursor
        selection = history[-1]
        reservation = self.actions.ledger.get_model_reservation(selection["model_reservation_ref"])
        handle = self.actions.ledger._reservation(reservation, self.actions.ledger.get_campaign(cursor["workflow_id"]))
        receipt = self.backend.verify_capture(selection["model_capture_ref"], handle)
        manifest = read_investigation_json(self.actions.planner.methods, selection["model_capture_ref"])
        if (reservation["status"] != "completed" or receipt != reservation["capture"]
                or manifest["response_digest"] != digest(selection["response"])):
            raise ValueError("Parent recovery requires an exact accepted and settled captured response")
        if node == "reassess":
            from .method_planner_contract import validate_scientific_candidates, validate_parameter_origins
            candidates = validate_scientific_candidates(selection["response"])
            validate_parameter_origins(candidates, current.get("user_parameter_requirements", []))
            reassessed = current.get("candidate_reassessment", {})
            if (current["scientific_candidates"] != candidates or reassessed.get("previous_scientific_candidates") != before["scientific_candidates"]
                    or reassessed.get("selection_attempt") != selection["attempt"]
                    or reassessed.get("bundle_ref") != before["investigation_evidence"][0]["bundle_ref"]):
                raise ValueError("Reassessed scientific candidates differ from their captured proposal")
        else:
            from .method_planner_contract import validate_preliminary_explanation
            explanation = validate_preliminary_explanation(selection["response"])
            if (current.get("preliminary_explanation", {}).get("response") != explanation
                    or current["preliminary_explanation"]["selection_attempt"] != selection["attempt"]):
                raise ValueError("Preliminary explanation differs from its captured proposal")
        result = {"before_plan_hash": cursor["expected_plan_hash"], "after_plan_hash": digest(current)}
        self.actions.ledger.reconcile(cursor["workflow_id"], node, 0, result)
        parent = {**cursor["parent_cursor"], "status": "ready", "failure": None, "expected_planner_hash": digest(current)}
        return self._refresh(cursor, parent, node)

    def _restore_checkpoint(self, workflow_id, config):
        """Reconstruct an exact cursor from retained operations, never rerun APIs."""
        from .rna_investigation_contract import validate_independent_cursor
        from .workflow_state import initial_state
        self.backend.bind_ledger(self.actions.ledger)
        campaign = self.actions.ledger.get_campaign(workflow_id)
        if campaign is None or campaign["identity"].get("configuration_digest") != self.configuration_digest:
            raise ValueError("Lost checkpoint has no exact retained independent campaign")
        self.frozen_policy = deepcopy(campaign["identity"]["investigation_policy"])
        first = self.actions.ledger.get(workflow_id, "audit", 0)
        if first is None:
            raise ValueError("Lost checkpoint has no verified initial operation")
        origin = self.actions.planner.methods.get("method_plan_snapshot", first["request"]["expected_planner_hash"])
        refs = [{key: self.actions.planner.methods.get("artifact", identity)[key]
                 for key in ("artifact_id", "sha256", "artifact_type", "size_bytes")}
                for identity in origin["context"]["dataset_summary"].get("artifact_ids", [])]
        parent = initial_state(origin, refs)
        cursor = {"format": "devbench.rna-independent-workflow/1", "workflow_id": workflow_id,
            "parent_session_id": workflow_id, "parent_cursor": parent, "expected_plan_hash": digest(origin),
            "binding_digest": planning_binding(origin), "policy_digest": digest(self.frozen_policy), "stage": self.policy["stage"],
            "decision": None, "allocated_children": 0, "child_ids": [], "dispatch_refs": {}, "result_refs": {},
            "reconciliation_ref": None, "phase": "audit", "status": "ready", "failure": None}
        nodes = ["audit", "question", "generate"]
        if cursor["stage"] == "pre_fit_methods":
            nodes += ["investigation_decision", "attach", "reassess"]
        nodes += ["resolve", "design", "declare", "run", "verify"]
        if cursor["stage"] == "post_fit_explanation":
            if self.actions.ledger.get(workflow_id, "preliminary_explanation", 0) is not None:
                nodes += ["preliminary_explanation"]
            nodes += ["investigation_decision", "attach"]
        nodes += ["recommend"]
        for node in nodes:
            if node in {"attach", "reassess"} and not cursor["child_ids"]:
                continue
            attempts = range(len(cursor["parent_cursor"]["trial_ids"])) if node == "run" else [0]
            if node == "run" and not cursor["parent_cursor"]["trial_ids"]:
                continue
            for attempt in attempts:
                record = self.actions.ledger.get(workflow_id, node, attempt)
                if record is None:
                    break
                if record["status"] != "completed":
                    cursor = self._failure(cursor, node, record["status"], "Retained operation requires explicit verified reconciliation")
                    break
                result = record["result"]
                if node == "investigation_decision":
                    cursor.update(result)
                    cursor["allocated_children"] = result["decision"]["allocated_children"]
                    cursor["phase"] = node
                    cursor["status"] = "blocked" if result["decision"]["status"] == "blocked" else "ready"
                    for identity, dispatch in cursor["dispatch_refs"].items():
                        outcome = self.backend.reconcile_capture(dispatch)
                        if outcome.get("result_ref") is not None:
                            cursor["result_refs"][identity] = outcome["result_ref"]
                elif node in {"attach", "reassess", "preliminary_explanation"}:
                    parent = {**cursor["parent_cursor"], "expected_planner_hash": result["after_plan_hash"]}
                    cursor = self._refresh(cursor, parent, node)
                    if node == "attach":
                        cursor["reconciliation_ref"] = result["bundle_ref"]
                else:
                    parent = {**cursor["parent_cursor"], **result["update"], "expected_planner_hash": result["after_planner_hash"]}
                    ref = f"{node}:{attempt}"
                    parent["operation_refs"] += [] if ref in parent["operation_refs"] else [ref]
                    cursor = self._refresh(cursor, parent, node)
            if record is None or cursor["status"] != "ready":
                break
        collected = {}
        for identity, dispatch in cursor["dispatch_refs"].items():
            outcome = self.backend.reconcile_capture(dispatch)
            status = outcome["status"] if outcome["status"] in {"completed", "abstained", "failed"} else "incomplete"
            collected[identity] = {"status": status, "result_ref": outcome.get("result_ref")}
        cursor = validate_independent_cursor(cursor)
        # This explicit recovery writes only a reconstructed checkpoint. It
        # never grants provider/numerical authority or changes scientific data.
        self.graph.update_state(config, {"cursor": cursor, "child_returns": collected}, as_node=cursor["phase"])
        return cursor

    def replay(self, workflow_id):
        """Read and verify evidence and usage, with zero continuation or writes."""
        self._verify_database()
        snapshot = self.graph.get_state(self._config(workflow_id))
        if not snapshot.values:
            raise ValueError("Workflow checkpoint does not exist")
        cursor = self._cursor(snapshot.values)
        if cursor["workflow_id"] != workflow_id:
            raise ValueError("Checkpoint is bound to another workflow")
        self.backend.bind_ledger(self.actions.ledger)
        if cursor["status"] == "reconciliation_required" and cursor["phase"] in _PARENT_MODEL_PHASES:
            verified = self._failed_parent_snapshot(cursor, cursor["phase"])
            if verified is not None:
                parent = {**cursor["parent_cursor"], "expected_planner_hash": digest(verified)}
                cursor = self._refresh(cursor, parent, cursor["phase"])
        self._check(cursor)
        from .rna_investigations import verify_investigation_bundle
        for identity, dispatch_ref in cursor["dispatch_refs"].items():
            descriptor = self.backend.read_dispatch(dispatch_ref)
            if descriptor["request"]["investigation_id"] != identity:
                raise ValueError("Replay child identity differs from its retained dispatch")
            outcome = self.backend.reconcile_capture(dispatch_ref)
            if identity in cursor["result_refs"] and outcome.get("result_ref") != cursor["result_refs"][identity]:
                raise ValueError("Replay child result differs from its retained capture")
        if cursor["reconciliation_ref"] is not None:
            bundle = read_investigation_json(self.actions.planner.methods, cursor["reconciliation_ref"], max_bytes=24_000)
            before = self.actions.planner.methods.get("method_plan_snapshot", bundle["plan_hash"])
            def verify(packet_ref, result_ref, *, dispatch_ref=None):
                return (self.backend.verify_child_result(packet_ref, result_ref) if result_ref is not None
                        else self.backend.verify_incomplete_child(packet_ref, dispatch_ref))
            verify_investigation_bundle(self.actions.planner.methods, cursor["reconciliation_ref"], plan=before,
                verify_child=verify)
        for reservation in self.actions.ledger.campaign_reservations(workflow_id):
            if reservation["capture"] is not None:
                campaign = self.actions.ledger.get_campaign(workflow_id)
                handle = self.actions.ledger._reservation(reservation, campaign)
                verified = self.backend.verify_capture(reservation["capture"]["capture_ref"], handle)
                if verified != reservation["capture"]:
                    raise ValueError("Replay model usage receipt failed actual capture verification")
        if not snapshot.next and cursor["status"] in {"completed", "abstained"}:
            self.actions.verify_terminal(cursor["parent_cursor"])
        return cursor
