"""Required serial LangGraph controller; the harness retains execution authority."""
import os
import sqlite3
import stat
from contextlib import contextmanager
from contextvars import ContextVar
import fcntl

from .workflow_state import SERIAL_ROUTE, WorkflowState, digest, validate_workflow_state


_active_node_dispatch = ContextVar("devbench_active_serial_node_dispatch", default=None)
_active_graph_invocation = ContextVar("devbench_active_serial_graph_invocation", default=None)


def _require_active_node_dispatch(controller, node, state):
    active = _active_node_dispatch.get()
    if (_active_graph_invocation.get() is not controller
            or active is None or active[0] is not controller or active[1] != node
            or active[2] != digest(state)):
        raise PermissionError("Qualification dispatch requires the current serial graph node invocation")
    return active[3]


class SerialRNAWorkflow:
    def __init__(self, actions, checkpoint_path, *, enabled=True, stepwise=False):
        if enabled is not True:
            raise ValueError("LangGraph is required; workflow graph bypass is not supported")
        if any(os.environ.get(key, "").lower() not in {"", "false", "0"} for key in ("LANGCHAIN_TRACING", "LANGCHAIN_TRACING_V2", "LANGSMITH_TRACING")):
            raise ValueError("External tracing is not supported by this local workflow")
        from .method_executor import _path
        checkpoint_path = _path(checkpoint_path)
        self.checkpoint_path = checkpoint_path
        previous_identity = self._database_identity() if checkpoint_path.exists() else None
        from langgraph.graph import StateGraph, START, END
        from langgraph.checkpoint.sqlite import SqliteSaver
        from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
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
        builder.add_edge(START, "audit")
        sequence = SERIAL_ROUTE
        for before, after in zip(sequence, sequence[1:]):
            if before == "run":
                builder.add_conditional_edges(before, lambda state: END if state["status"] != "ready" else
                    ("run" if state["trial_index"] < len(state["trial_ids"]) else "verify"))
            elif before == "execution_review":
                builder.add_conditional_edges(before, lambda state: END if state["status"] != "ready" else
                    ("run" if state["trial_ids"] else "verify"))
            else:
                builder.add_conditional_edges(before, lambda state, following=after: following if state["status"] == "ready" else END)
        builder.add_edge("conclusion_review", END)
        self.graph = builder.compile(checkpointer=saver, interrupt_after=(
            [node for node in SERIAL_ROUTE if node not in {"review", "execution_review"}]
            if stepwise else None))

    def _node(self, node, state):
        active = _active_node_dispatch.set((self, node, digest(state), object()))
        try:
            if node == "run":
                from .workflow_actions import _COLAB_FACTORY_ADMISSION
                if getattr(self, "_qualification_factory_token", None) is _COLAB_FACTORY_ADMISSION:
                    from .colab_workflow import ColabQualificationActions
                    if type(self.actions) is ColabQualificationActions:
                        with self.actions.graph_run_scope(self, state):
                            return self.actions.perform(node, state)
            return self.actions.perform(node, state)
        except Exception as error:
            return validate_workflow_state({**state, "phase": node, "status": "failed",
                "failure": {"kind": type(error).__name__, "message": str(error)[:500]}})
        finally:
            _active_node_dispatch.reset(active)

    @staticmethod
    def _config(workflow_id):
        import re
        if type(workflow_id) is not str or re.fullmatch(r"[0-9a-f]{32}", workflow_id) is None:
            raise ValueError("Use an exact workflow UUID")
        return {"configurable": {"thread_id": workflow_id}, "recursion_limit": 160, "callbacks": []}

    def start(self, state):
        from .langchain_tools import ensure_local_observability
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
            active = _active_graph_invocation.set(self)
            try:
                return self.graph.invoke(state, config=config)
            finally:
                _active_graph_invocation.reset(active)

    def resume(self, workflow_id, *, expected_nodes=None):
        from langgraph.types import Command
        from .langchain_tools import ensure_local_observability
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
            if not snapshot.next:
                return self.actions.verify_terminal(state)
            active = _active_graph_invocation.set(self)
            try:
                return self.graph.invoke(Command(resume="continue") if snapshot.interrupts else None, config=config)
            finally:
                _active_graph_invocation.reset(active)

    def reconcile(self, workflow_id):
        """Continue only after deriving completed effects from actual harness evidence."""
        from .langchain_tools import ensure_local_observability
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

    @contextmanager
    def invocation_lease(self, workflow_id):
        from .method_executor import _path
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
        from .method_executor import _path
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
