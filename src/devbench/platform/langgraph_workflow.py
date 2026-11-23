"""LangGraph control flow for fixed and adaptive RNA workflows."""
import fcntl
import os
import sqlite3
import stat
from contextlib import contextmanager
from typing import TypedDict

from .workflow_state import SERIAL_ROUTE, WorkflowState, validate_workflow_state

class _AdaptiveInvocation(TypedDict):
    remaining: int
    expected_revision: int | None
    dispatches: list[dict]
    ready: bool
    pending: dict | None
    decisions: list[dict]


class AdaptiveRNAWorkflow:
    """Schedule current work from the scientific ledger on every graph turn."""

    def __init__(self, actions, adaptive):
        from langgraph.graph import END, START, StateGraph

        self.actions = actions
        self.adaptive = adaptive
        builder = StateGraph(_AdaptiveInvocation)
        if getattr(actions, "controller", None) is None:
            builder.add_node("dispatch", self._dispatch)
            builder.add_edge(START, "dispatch")
            builder.add_conditional_edges("dispatch",
                lambda state: "dispatch" if state["remaining"] and state["ready"] else END,
                {"dispatch": "dispatch", END: END})
        else:
            from devbench.engine.workflow_operations import OperationBlocked
            try:
                actions.controller.bind(adaptive)
            except OperationBlocked as error:
                if adaptive.controller_state()["binding"] is None:
                    raise
                actions.stop_adaptive(adaptive, error.state)
            builder.add_node("select", self._select)
            builder.add_node("apply", self._apply)
            builder.add_edge(START, "select")
            builder.add_conditional_edges("select", lambda state: "apply" if state["pending"] else END,
                                          {"apply": "apply", END: END})
            builder.add_conditional_edges("apply", lambda state: "select" if state["remaining"] and state["ready"] else END,
                                          {"select": "select", END: END})
        self.graph = builder.compile()

    def _select(self, state):
        from devbench.analysis.episode_agent import InvalidSelection
        from devbench.engine.workflow_operations import OperationBlocked
        try:
            return {"pending": self.actions.select_adaptive(self.adaptive, expected_revision=state["expected_revision"])}
        except (OperationBlocked, InvalidSelection, ValueError, PermissionError) as error:
            reason = getattr(error, "state", "controller_selection_requires_reconciliation")
            if reason not in {"active", "ledger_busy"}:
                self.actions.stop_adaptive(self.adaptive, reason)
            return {"pending": None, "ready": False}

    def _apply(self, state):
        try:
            result = self.actions.apply_adaptive_decision(self.adaptive, state["pending"])
        except Exception as error:
            # An effect can have a retained receipt even when its controller
            # journal write failed. Keep the exact selected action for resume.
            result = {"status": "reconciliation_required", "reason": "controller_apply_requires_reconciliation",
                      "error_type": type(error).__name__}
            return {"remaining": state["remaining"] - 1, "ready": False,
                    "decisions": [*state["decisions"], result]}
        dispatches = state["dispatches"]
        if (state["pending"]["decision"]["kind"] == "dispatch" and result.get("status") != "rejected"
                and not result.get("reconciled")):
            dispatches = [*dispatches, result]
        return {"remaining": state["remaining"] - 1,
            "expected_revision": None,
            "ready": self.adaptive.controller_state()["outcome"] is None,
            "dispatches": dispatches, "decisions": [*state["decisions"], result]}

    def _dispatch(self, state):
        ready = self.adaptive.ready()
        if not ready:
            return {"ready": False}
        receipt = self.actions.perform_adaptive(self.adaptive, ready[0], expected_revision=state["expected_revision"])
        return {"remaining": state["remaining"] - 1, "ready": True, "expected_revision": None,
                "dispatches": [*state["dispatches"], receipt]}

    def advance(self, *, max_steps=1, expected_revision=None):
        from devbench.analysis.langchain_tools import ensure_local_observability
        from devbench.engine.workflow_operations import OperationConflict

        if type(max_steps) is not int or not 1 <= max_steps <= 64:
            raise ValueError("Adaptive graph invocation requires a budget of 1 to 64 steps")
        if expected_revision is not None:
            if type(expected_revision) is not int or expected_revision < 0:
                raise ValueError("Expected revision must be a nonnegative integer")
            if self.adaptive.status()["revision"] != expected_revision:
                raise OperationConflict("Adaptive workflow revision changed before dispatch")
        recover = getattr(self.actions, "reconcile_adaptive", None)
        reconciled = recover(self.adaptive) if callable(recover) else []
        if reconciled and expected_revision is not None:
            # Recovery is safe against the retained receipt. A new dispatch
            # needs a fresh revision because another writer may have acted.
            state = {"dispatches": [], "decisions": []}
        else:
            config = {"recursion_limit": 2 * max_steps + 3, "callbacks": []}
            ensure_local_observability(config)
            state = self.graph.invoke(
                {
                    "remaining": max_steps, "dispatches": [], "ready": True,
                    "pending": None, "decisions": [],
                    "expected_revision": expected_revision,
                },
                config=config,
            )
        result = {**self.adaptive.status(), "steps_dispatched": len(state["dispatches"]),
                  "dispatches": state["dispatches"]}
        if reconciled:
            result["reconciled_attempts"] = len(reconciled)
        if getattr(self.actions, "controller", None) is not None:
            result.update(decisions=state["decisions"], controller_outcome=self.adaptive.controller_state()["outcome"])
        return result


class SerialRNAWorkflow:
    def __init__(self, actions, checkpoint_path, *, enabled=True, stepwise=False):
        if enabled is not True:
            raise ValueError("LangGraph is required; workflow graph bypass is not supported")
        if any(os.environ.get(key, "").lower() not in {"", "false", "0"} for key in ("LANGCHAIN_TRACING", "LANGCHAIN_TRACING_V2", "LANGSMITH_TRACING")):
            raise ValueError("External tracing is not supported by this local workflow")
        from devbench.analysis.method_executor import _path
        checkpoint_path = _path(checkpoint_path)
        self.checkpoint_path = checkpoint_path
        previous_identity = self._database_identity() if checkpoint_path.exists() else None
        from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
        from langgraph.checkpoint.sqlite import SqliteSaver
        from langgraph.graph import END, START, StateGraph
        from langgraph.types import interrupt
        self.actions = actions
        self.connection = sqlite3.connect(checkpoint_path, check_same_thread=False)
        self.database_identity = self._database_identity()
        if previous_identity is not None and self.database_identity != previous_identity:
            self.connection.close()
            raise ValueError("Checkpoint database identity changed while opening")
        saver = SqliteSaver(self.connection, serde=JsonPlusSerializer(pickle_fallback=False,
            allowed_json_modules=None, allowed_msgpack_modules=None))
        builder = StateGraph(WorkflowState)
        for node in SERIAL_ROUTE:
            if node in {"review", "execution_review"}:
                continue
            builder.add_node(node, lambda state, selected=node: self._node(selected, state))

        self._add_review_nodes(builder, interrupt)
        builder.add_edge(START, "audit")
        sequence = SERIAL_ROUTE
        for before, after in zip(sequence, sequence[1:], strict=False):
            if before == "run":
                builder.add_conditional_edges(
                    before,
                    lambda state: END if state["status"] != "ready" else (
                        "run" if state["trial_index"] < len(state["trial_ids"])
                        else "verify"
                    ),
                )
            elif before == "execution_review":
                builder.add_conditional_edges(
                    before,
                    lambda state: END if state["status"] != "ready" else (
                        "run" if state["trial_ids"] else "verify"
                    ),
                )
            else:
                builder.add_conditional_edges(
                    before,
                    lambda state, following=after: (
                        following if state["status"] == "ready" else END
                    ),
                )
        builder.add_edge("conclusion_review", END)
        self.graph = builder.compile(checkpointer=saver, interrupt_after=(
            [node for node in SERIAL_ROUTE
             if node not in {"review", "execution_review"}]
            if stepwise else None))

    def _add_review_nodes(self, builder, interrupt):
        def review(state):
            self.actions.check_node_entry("review", state)
            self.actions.check(state)
            if self.actions.ledger.is_cancelled(state["workflow_id"]):
                return {**state, "phase": "review", "status": "cancelled"}
            interrupt({"phase": "review", "session_id": state["session_id"], "planner_hash": state["expected_planner_hash"],
                       "instruction": "Review frozen trial drafts and inject exact caller-selected draft/branch references"})
            self.actions.check(state)
            return self.actions.perform("review", state)

        def execution_review(state):
            self.actions.check_node_entry("execution_review", state)
            self.actions.check(state)
            if self.actions.ledger.is_cancelled(state["workflow_id"]):
                return {**state, "phase": "execution_review", "status": "cancelled"}
            if state["trial_ids"]:
                interrupt({"phase": "execution_review", "session_id": state["session_id"],
                           "planner_hash": state["expected_planner_hash"], "trial_ids": state["trial_ids"],
                           "instruction": "Review declarations; runtime execution authority must be supplied separately"})
                self.actions.check(state)
            return self.actions.perform("execution_review", state)

        builder.add_node("review", review)
        builder.add_node("execution_review", execution_review)

    def _node(self, node, state):
        try:
            return self.actions.perform(node, state)
        except Exception as error:
            return validate_workflow_state({**state, "phase": node, "status": "failed",
                "failure": {"kind": type(error).__name__, "message": str(error)[:500]}})

    @staticmethod
    def _config(workflow_id):
        import re
        if type(workflow_id) is not str or re.fullmatch(r"[0-9a-f]{32}", workflow_id) is None:
            raise ValueError("Use an exact workflow UUID")
        return {"configurable": {"thread_id": workflow_id}, "recursion_limit": 160, "callbacks": []}

    def start(self, state):
        from devbench.analysis.langchain_tools import ensure_local_observability
        ensure_local_observability()
        state = validate_workflow_state(state)
        if (state["phase"] != "audit" or state["status"] != "ready" or state["operation_refs"]
                or state["trial_ids"] or state["trial_index"] or state["outcome_ref"] is not None or state["failure"] is not None):
            raise ValueError("Start requires an unused initial audit cursor; resume existing workflows")
        if self.actions.ledger.replay(state["workflow_id"]):
            raise ValueError("Start requires an unused workflow; resume retained operations")
        config = self._config(state["workflow_id"])
        with self.invocation_lease(state["workflow_id"]):
            if self.graph.get_state(config).values:
                raise ValueError("Workflow checkpoint already exists; resume instead of restarting")
            return self.graph.invoke(state, config=config)

    def resume(self, workflow_id, *, expected_nodes=None, before_invoke=None):
        from langgraph.types import Command

        from devbench.analysis.langchain_tools import ensure_local_observability
        ensure_local_observability()
        config = self._config(workflow_id)
        with self.invocation_lease(workflow_id):
            snapshot = self.graph.get_state(config)
            if not snapshot.values:
                raise ValueError("Workflow checkpoint does not exist")
            state = validate_workflow_state(snapshot.values)
            if state["workflow_id"] != workflow_id:
                raise ValueError("Checkpoint is bound to another workflow")
            if expected_nodes is not None and (len(snapshot.next) != 1 or snapshot.next[0] not in expected_nodes):
                raise ValueError("Requested workflow action differs from the next retained graph node")
            if before_invoke is not None:
                before_invoke(snapshot)
            if not snapshot.next:
                return self.actions.verify_terminal(state)
            return self.graph.invoke(Command(resume="continue") if snapshot.interrupts else None, config=config)

    def reconcile(self, workflow_id):
        """Continue only after deriving completed effects from actual harness evidence."""
        from devbench.analysis.langchain_tools import ensure_local_observability
        ensure_local_observability()
        config = self._config(workflow_id)
        with self.invocation_lease(workflow_id):
            snapshot = self.graph.get_state(config)
            state = validate_workflow_state(snapshot.values)
            if state["workflow_id"] != workflow_id or state["status"] != "reconciliation_required":
                raise ValueError("Only an uncertain checkpoint can be reconciled")
            reconciled = self.actions.reconcile(state)
            self.graph.update_state(config, reconciled, as_node=state["phase"])
            return reconciled

    def reconcile_recommendation_header_failure(self, workflow_id, *, actor):
        """Reopen a retained recommendation with no selector or recommendation effects."""
        config = self._config(workflow_id)
        with self.invocation_lease(workflow_id):
            snapshot = self.graph.get_state(config)
            state = validate_workflow_state(snapshot.values)
            original = self.actions.ledger.get(workflow_id, "recommend", 0)
            if (not self.actions.retained_synthesis or state["workflow_id"] != workflow_id
                    or state["status"] != "reconciliation_required" or state["phase"] != "recommend"
                    or self.actions.ledger.is_cancelled(workflow_id)
                    or original is None or original["status"] != "uncertain"
                    or not self._matches_operation_request(original, state)
                    or self.actions.ledger.get(workflow_id, "recommend", 1) is not None):
                raise ValueError("No matching pre-selection recommendation failure can be reconciled")
            plan = self.actions.check(state)
            if (plan.get("selection_in_progress") is not None or (plan.get("recommendation") or {}).get("choice") is not None
                    or any(row.get("phase") == "method_recommendation" for row in plan["selection_history"])):
                raise ValueError("Recommendation effects are present")
            campaign = plan.get("model_campaign")
            if campaign and any(row["request"].get("purpose") == "method_recommendation"
                    and row["charge_basis"] != "verified_unlaunched"
                    for row in self.actions.ledger.campaign_reservations(campaign["campaign_id"])):
                raise ValueError("Recommendation model effects require reconciliation")
            self.actions.planner.evidence_assessment(workflow_id)
            ready = validate_workflow_state({**state, "phase": "verify", "status": "ready", "failure": None})
            self.actions.check_node_entry("recommend", ready)
            receipt = {"original_request_digest": original["request_digest"],
                "planner_hash": state["expected_planner_hash"]}
            self.actions.ledger.run(workflow_id, "recommend_no_effect", 0,
                {**receipt, "actor": actor, "failure": state["failure"]}, lambda: receipt)
            self.graph.update_state(config, ready, as_node="verify")
            return ready

    @staticmethod
    def _matches_operation_request(operation, state):
        from devbench.engine.workflow_operations import _digest, _json
        request = operation["request"]
        return (operation["request_digest"] == _digest(_json(request))
                and all(request.get(name) == state[name] for name in
                        ("session_id", "expected_planner_hash", "binding_digest", "artifact_refs")))

    def reconcile_review_projection_failure(self, workflow_id, *, actor):
        """Reopen a conclusion review whose sealed receipt proves zero dispatch."""
        from devbench.study.evidence import _read, decode_json

        config = self._config(workflow_id)
        with self.invocation_lease(workflow_id):
            snapshot = self.graph.get_state(config)
            state = validate_workflow_state(snapshot.values)
            ledger = self.actions.ledger
            original = ledger.get(workflow_id, "conclusion_review", 0)
            if (not self.actions.retained_synthesis or state["workflow_id"] != workflow_id
                    or state["status"] != "review_required" or state["phase"] != "conclusion_review"
                    or state["operation_refs"][-1:] != ["conclusion_review:0"] or ledger.is_cancelled(workflow_id)
                    or original is None or original["status"] != "completed"
                    or not self._matches_operation_request(original, state)
                    or ledger.get(workflow_id, "conclusion_review", 1) is not None):
                raise ValueError("No matching conclusion review can be reconciled")
            self.actions.check(state)
            methods = self.actions.planner.methods
            envelope = original["result"]["evidence"]
            runtime = decode_json(_read(methods.artifact_path(envelope["record_ref"]["artifact_id"]), 1_000_000))
            ref = runtime["service_evidence"]["record_ref"]
            review = decode_json(_read(methods.artifact_path(ref["artifact_id"]), 1_000_000))["record"]
            request = review["request_result"]
            if (review["status"] != "blocked" or request["status"] != "infrastructure_failure"
                    or request.get("selector_entered") is not False
                    or type(request.get("model_sessions_attempted")) is not int or request["model_sessions_attempted"] != 0):
                raise ValueError("Review receipt does not prove a pre-dispatch failure")
            recommend = ledger.get(workflow_id, "recommend", self.actions.operation_attempt(state, "recommend"))
            ready = validate_workflow_state({**state, "phase": "recommend", "status": "ready", "failure": None,
                "operation_refs": state["operation_refs"][:-1], "outcome_ref": recommend["result"]["update"]["outcome_ref"]})
            self.actions.check_node_entry("conclusion_review", ready)
            receipt = {"original_request_digest": original["request_digest"], "planner_hash": state["expected_planner_hash"]}
            ledger.run(workflow_id, "conclusion_review_no_effect", 0,
                {**receipt, "actor": actor, "blocked_review": ref}, lambda: receipt)
            self.graph.update_state(config, ready, as_node="recommend")
            return ready

    @contextmanager
    def invocation_lease(self, workflow_id):
        from devbench.analysis.method_executor import _path
        self._config(workflow_id)
        self._verify_database()
        directory = self.checkpoint_path.with_name(self.checkpoint_path.name + ".locks")
        _path(directory)
        directory.mkdir(exist_ok=True)
        path = _path(directory / (workflow_id + ".lock"))
        descriptor = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise ValueError("Workflow invocation lease is active") from error
            self._verify_database()
            yield
            self._verify_database()
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    def _database_identity(self):
        from devbench.analysis.method_executor import _path
        metadata = _path(self.checkpoint_path).stat()
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise ValueError("Checkpoint database must be a regular single-link file; hardlink aliases are forbidden")
        return metadata.st_dev, metadata.st_ino

    def _verify_database(self):
        if self._database_identity() != self.database_identity:
            raise ValueError("Checkpoint database identity was replaced")

    def close(self):
        self.connection.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
