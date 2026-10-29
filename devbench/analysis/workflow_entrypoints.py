"""Action-scoped RNA entrypoints over one mandatory durable LangGraph route.

Each call authorizes only its requested model or numeric operation. Checkpoints
retain facts, never runtime permission; generic non-RNA planner uses stay local.
"""
from copy import deepcopy

from .workflow_actions import WorkflowActions
from .workflow_operations import WorkflowOperationLedger
from .workflow_state import digest


CHECKPOINT_NAME = "rna-workflow-checkpoints.sqlite"
LEDGER_NAME = "rna-workflow-operations.sqlite"


def audit_workflow_design(planner, session_id, *, actor):
    """Run or verify only the existing audit node, without model or fit authority.

    This is a durable graph entry point, not a second controller. Later planning
    uses the same checkpoint and still requires its scientific runtime.
    """
    from .langgraph_workflow import SerialRNAWorkflow

    plan = planner.get(session_id)
    if not is_rna_plan(plan) or plan.get("rna_design") is None:
        raise ValueError("An explicit rna_design on a registered RNA plan is required")
    ledger = WorkflowOperationLedger(planner.methods.root / LEDGER_NAME)
    actions = WorkflowActions(planner, ledger, actor=actor, selector=None)
    with SerialRNAWorkflow(actions, planner.methods.root / CHECKPOINT_NAME, stepwise=True) as workflow:
        state = workflow.graph.get_state(workflow._config(session_id)).values
        if not state:
            workflow.start(actions.create_state(session_id))
            state = workflow.graph.get_state(workflow._config(session_id)).values
        # Do not advance or reconstruct a later planning runtime to read an audit.
        if state["phase"] != "audit":
            raise ValueError("The workflow has advanced beyond audit; use retained workflow state and evidence")
        actions.check(state)
        if state["status"] in {"completed", "abstained"}:
            actions.verify_terminal(state)
        record = ledger.get(session_id, "audit", 0)
        return {"format": "devbench.local-rna-design-audit/1", "session_id": session_id,
            "status": state["status"], "phase": state["phase"], "failure": state.get("failure"),
            "audit": deepcopy(((record or {}).get("result") or {}).get("evidence")),
            "execution": "not_run", "numerical_replay": "not_run", "biological_agreement": "not_run",
            "scope": "design_audit_only_not_scientific_validation"}


def advance_workflow_to_gate(planner, session_id, *, actor, selector=None, version_ids=(),
                             allow_model_execution=False, scientific_workflow=None, phase="planning", before_advance=None):
    """Advance an explicitly requested campaign without acknowledging its gates.

    Runtime permissions are supplied afresh and are never reconstructed from the
    checkpoint. Every real node still runs through WorkflowActions and its ledger.
    This entrypoint cannot declare a trial, grant data execution, run a worker or
    reconcile an interrupted effect.
    """
    from .workflow_state import SERIAL_ROUTE

    if type(phase) is not str or phase not in {"planning", "synthesis"}:
        raise ValueError("Use planning or synthesis advancement")
    if type(allow_model_execution) is not bool:
        raise ValueError("Use an explicit boolean model-execution permission")
    if before_advance is not None and not callable(before_advance):
        raise ValueError("The current-runtime verifier must be a trusted callable")
    plan = planner.get(session_id)
    if not is_rna_plan(plan):
        raise ValueError("Automatic graph advancement requires an RNA scientific plan")
    ledger = None

    def stopped(state=None, following=(), *, status, gate=None, required_action=None, reason=None):
        state = state or {}
        refs = list(state.get("operation_refs", []))
        steps = []
        if ledger is not None:
            tail = state.get("phase")
            tail_ref = f"{tail}:{state.get('trial_index', 0) if tail == 'run' else 0}"
            inspect = refs + ([tail_ref] if tail and tail_ref not in refs else [])
            for reference in inspect:
                node, attempt = reference.rsplit(":", 1)
                record = ledger.get(session_id, node, int(attempt))
                if record is None:
                    continue
                row = {"node": node, "attempt": int(attempt), "status": record["status"],
                    "controller_status": (record.get("result") or {}).get("update", {}).get("status"),
                    "request_sha256": record["request_digest"], "result_sha256": record["result_digest"]}
                evidence = (record.get("result") or {}).get("evidence")
                if evidence is not None:
                    row["evidence"] = deepcopy(evidence)
                steps.append(row)
        return {"format": "devbench.scientific-advance/1", "session_id": session_id,
            "status": status, "gate": gate, "next_node": following[0] if len(following) == 1 else None,
            "phase": state.get("phase"), "requested_phase": phase,
            "controller_status": state.get("status"), "operation_refs": refs, "steps": steps,
            "required_action": required_action, "reason": reason,
            "scientific_qualification": "not_established"}

    # Declared local analyses first reach the same durable audit barrier even
    # when no model/discovery/reviewer runtime has been configured yet.
    if plan.get("rna_design") is not None and phase == "planning":
        audit_path = planner.methods.root / LEDGER_NAME
        audit_record = (WorkflowOperationLedger(audit_path, readonly=True).get(session_id, "audit", 0)
                        if audit_path.exists() else None)
        if audit_record is None or ((audit_record.get("result") or {}).get("update", {}).get("status") != "ready"):
            audited = audit_workflow_design(planner, session_id, actor=actor)
            if audited["status"] != "ready":
                return audited
    if scientific_workflow is None:
        return stopped(status="awaiting_information", required_action="configure_scientific_workflow",
            reason="A trusted discovery, implementation and independent-review runtime is required.")
    checkpoint = planner.methods.root / CHECKPOINT_NAME
    if phase == "synthesis" and not checkpoint.exists():
        return stopped(status="awaiting_information", required_action="complete_controlled_trials",
            reason="Synthesis requires the retained reviewed planning and controlled-trial route.")
    ledger = WorkflowOperationLedger(planner.methods.root / LEDGER_NAME)
    declared = ledger.get(session_id, "declare", 0)
    declarations = declared["request"]["declarations"] if declared is not None else []
    actions = WorkflowActions(planner, ledger, actor=actor, selector=selector, version_ids=version_ids,
        declarations=declarations, allow_model_execution=allow_model_execution,
        allow_data_execution=False, scientific_workflow=scientific_workflow)
    allowed = (set(SERIAL_ROUTE[:SERIAL_ROUTE.index("review")]) if phase == "planning"
               else {"verify", "recommend", "conclusion_review"})

    def permission(node):
        if node in {"generate", "resolve", "recommend"}:
            if selector is None:
                return "configure_planning_selector", "A planning selector must be explicitly configured."
            configuration = selector.configuration() if hasattr(selector, "configuration") else {}
            if configuration.get("backend") != "recorded_fixture" and not allow_model_execution:
                return "authorize_model_execution", "This campaign requires current explicit model-execution permission."
        if node in {"candidate_review", "design_review", "conclusion_review"}:
            configuration = scientific_workflow.configuration()
            if configuration.get("format") == "devbench.workflow-scientific-configuration/1" and configuration.get("reviewer") is None:
                return "configure_scientific_reviewer", "A substantive independent scientific-review adapter is required."
            if getattr(scientific_workflow, "allow_review_model_execution", False) and not allow_model_execution:
                return "authorize_model_execution", "Scientific review requires current explicit model-execution permission."
        if node == "implementation" and getattr(scientific_workflow, "allow_implementation_model_execution", False) and not allow_model_execution:
            return "authorize_model_execution", "Implementation generation requires current explicit model-execution permission."
        return None

    from .langgraph_workflow import SerialRNAWorkflow
    with SerialRNAWorkflow(actions, checkpoint, stepwise=True) as workflow:
        config = workflow._config(session_id)
        snapshot = workflow.graph.get_state(config)
        if not snapshot.values:
            if phase != "planning":
                return stopped(status="awaiting_information", required_action="complete_controlled_trials",
                    reason="No retained planning/execution cursor exists for this session.")
            if before_advance is not None:
                before_advance()
            workflow.start(actions.create_state(session_id))
        for _ in range(len(SERIAL_ROUTE) + 1):
            if before_advance is not None:
                before_advance()
            snapshot = workflow.graph.get_state(config)
            state, following = snapshot.values, snapshot.next
            if state["status"] in {"completed", "abstained"}:
                workflow.actions.verify_terminal(state)
                return stopped(state, following, status=state["status"])
            if state["status"] not in {"ready", "review_required"}:
                return stopped(state, following, status="recovery_required", required_action="inspect_and_reconcile",
                    reason=(state.get("failure") or {}).get("message", "The retained controller requires explicit recovery."))
            actions.check(state)
            if ledger.is_cancelled(session_id):
                return stopped(state, following, status="cancelled", required_action="inspect_cancelled_workflow",
                    reason="The retained workflow is cancelled; nothing was resumed.")
            if state["status"] == "review_required":
                return stopped(state, following, status="awaiting_information", required_action="inspect_scientific_review",
                    reason=(state.get("failure") or {}).get("message", "A scientific review requires attention."))
            if len(following) != 1:
                return stopped(state, following, status="recovery_required", required_action="inspect_workflow_checkpoint",
                    reason="The retained workflow has no unique next node.")
            node = following[0]
            if node in {"review", "execution_review"}:
                # Enter an unvisited interrupt once, but never send its resume
                # value. Empty-trial execution review is still an explicit gate.
                if not snapshot.interrupts and (node == "review" or state["trial_ids"]):
                    workflow.resume(session_id, expected_nodes={node})
                    snapshot = workflow.graph.get_state(config)
                    state, following = snapshot.values, snapshot.next
                return stopped(state, following, status="awaiting_operator_review", gate=node,
                    required_action="declare_trial" if node == "review" else "run_trial",
                    reason="Review the frozen trial selection." if node == "review" else
                        "Explicit runtime authority and the exact declared trial are required before execution.")
            if node not in allowed:
                return stopped(state, following, status="awaiting_information",
                    required_action="advance_synthesis" if node in {"verify", "recommend", "conclusion_review"} else "complete_controlled_trials",
                    reason="The next node is outside this explicitly requested campaign phase.")
            blocked = permission(node)
            if blocked:
                return stopped(state, following, status="awaiting_information", required_action=blocked[0], reason=blocked[1])
            workflow.resume(session_id, expected_nodes={node})
        raise ValueError("Scientific campaign exceeded its bounded node count")


def is_rna_plan(plan):
    observed = plan["context"]["dataset_summary"].get("observed", {})
    scrna = observed.get("scrna_input")
    input_set = observed.get("rna_input_set")
    return ("bulk_rna_input" in observed
            or type(observed.get("fastq_input")) is dict and observed["fastq_input"].get("format") == "devbench.fastq-input/1"
            or type(scrna) is dict and scrna.get("format") == "devbench.scrna-input/1"
            or type(input_set) is dict and input_set.get("format") == "devbench.rna-input-set/1"
            or "rna_execution_inputs" in plan)


def synthesis_ready(planner, session_id, *, scientific_workflow=None, selector=None):
    """Observe an existing completed-trial cursor without resuming any graph node."""
    from .workflow_state import validate_workflow_state
    from .method_executor import _path
    plan = planner.get(session_id)
    if not is_rna_plan(plan):
        return False
    checkpoint, ledger_path = (planner.methods.root / name for name in (CHECKPOINT_NAME, LEDGER_NAME))
    for path in (checkpoint, ledger_path):
        if path.is_symlink():
            raise ValueError("Scientific workflow state cannot be a symlink")
        if not path.exists():
            return False
        _path(path)
    ledger = WorkflowOperationLedger(ledger_path, readonly=True)
    if ledger.is_cancelled(session_id):
        return False
    actions = WorkflowActions(planner, ledger, actor="synthesis-readiness", selector=selector,
        scientific_workflow=scientific_workflow)
    from .langgraph_workflow import SerialRNAWorkflow
    with SerialRNAWorkflow(actions, checkpoint, stepwise=True) as workflow:
        try:
            with workflow.invocation_lease(session_id):
                snapshot = workflow.graph.get_state(workflow._config(session_id))
                if not snapshot.values or len(snapshot.next) != 1:
                    return False
                state = validate_workflow_state(snapshot.values)
                if (state["status"] != "ready" or snapshot.next[0] not in {"verify", "recommend", "conclusion_review"}
                        or not state["trial_ids"] or state["trial_index"] != len(state["trial_ids"])):
                    return False
                actions.check(state)
                actions.check_node_entry(snapshot.next[0], state)
                return True
        except ValueError as error:
            if "invocation lease is active" in str(error):
                return False
            raise


def _direct(planner, session, node, *, actor, selector, version_ids, branch_id, spec,
            experiment_id, allow_model_execution, allow_data_execution, executor, execution_bindings, before_dispatch):
    options = {"actor": actor, "allow_model_execution": allow_model_execution}
    if node == "generate":
        return planner.generate_candidates(session, selector, **options)
    if node == "resolve":
        return planner.resolve_capabilities(session, selector, list(version_ids), **options)
    if node == "design":
        return planner.design_trials(session, selector, **options)
    if node == "recommend":
        return planner.recommend(session, selector, **options)
    if node == "declare":
        return planner.declare_trial(session, branch_id, spec, actor=actor)
    if node == "run":
        return planner.run_trial(session, experiment_id, actor=actor, allow_data_execution=allow_data_execution,
                                 executor=executor, execution_bindings=execution_bindings,
                                 **({"before_dispatch": before_dispatch} if before_dispatch is not None else {}))
    raise ValueError("Unknown RNA workflow action")


def workflow_action(planner, session_id, node, *, actor, selector=None, version_ids=(), branch_id=None,
                    spec=None, experiment_id=None, allow_model_execution=False, allow_data_execution=False,
                    executor=None, execution_bindings=None, before_dispatch=None, scientific_workflow=None,
                    execution_inputs_ref=None, execution_workspace=None):
    """Start/resume exactly one requested stage, preserving public planner values."""
    options = dict(actor=actor, selector=selector, version_ids=version_ids, branch_id=branch_id, spec=spec,
        experiment_id=experiment_id, allow_model_execution=allow_model_execution,
        allow_data_execution=allow_data_execution, executor=executor, execution_bindings=execution_bindings,
        before_dispatch=before_dispatch)
    plan = planner.get(session_id)
    attachment = None
    if execution_inputs_ref is not None or execution_workspace is not None:
        if node != "design" or not is_rna_plan(plan) or execution_inputs_ref is None or execution_workspace is None:
            raise ValueError("Late execution inputs require an RNA design action, sealed reference and workspace")
        from .method_executor import _path
        attachment = {"record_ref": deepcopy(execution_inputs_ref), "workspace": str(_path(execution_workspace))}
    if not is_rna_plan(plan):
        return _direct(planner, session_id, node, **options)
    if scientific_workflow is None:
        raise PermissionError("RNA actions require a trusted scientific workflow runtime for discovery, implementation and independent reviews")
    if node not in {"discovery", "generate", "candidate_review", "resolve", "implementation", "design", "design_review",
                    "declare", "run", "recommend", "conclusion_review"}:
        raise ValueError("Unknown RNA workflow action")
    if node == "run" and allow_data_execution is not True:
        raise PermissionError("Runtime data execution authority is required for this request")
    checkpoint = planner.methods.root / CHECKPOINT_NAME
    ledger = WorkflowOperationLedger(planner.methods.root / LEDGER_NAME)
    declared = ledger.get(session_id, "declare", 0)
    declarations = declared["request"]["declarations"] if declared is not None else []
    if node == "declare":
        drafts = plan.get("trial_designs", [])
        if (type(spec) is not dict or not drafts
                or spec not in drafts[-1]["proposals"]):
            raise ValueError("Declaration must select an exact frozen graph trial draft")
        declarations = [{"draft_id": spec["id"], "branch_id": branch_id}]
    actions = WorkflowActions(planner, ledger, actor=actor, selector=selector, version_ids=version_ids,
        declarations=declarations, allow_model_execution=allow_model_execution,
        allow_data_execution=allow_data_execution, executor=executor, execution_bindings=execution_bindings,
        before_dispatch=before_dispatch, requested_experiment_id=experiment_id, scientific_workflow=scientific_workflow,
        execution_input_request=attachment)
    from .langgraph_workflow import SerialRNAWorkflow
    with SerialRNAWorkflow(actions, checkpoint, stepwise=True) as workflow:
        config = workflow._config(session_id)
        snapshot = workflow.graph.get_state(config)
        if not snapshot.values:
            if node != "discovery":
                raise ValueError("RNA workflow must start with discovery; prior discovery and scientific reviews cannot be bypassed")
            workflow.start(actions.create_state(session_id))
        allowed = {"discovery": {"question", "discovery"}, "generate": {"generate"},
            "candidate_review": {"candidate_review"}, "resolve": {"resolve"}, "implementation": {"implementation"},
            "design": {"design"}, "design_review": {"design_review"}, "declare": {"review", "declare"},
            "run": {"execution_review", "run"}, "recommend": {"verify", "recommend"},
            "conclusion_review": {"conclusion_review"}}[node]
        for _ in range(8):
            snapshot = workflow.graph.get_state(config)
            state = snapshot.values
            if state["status"] not in {"ready", "review_required"}:
                raise ValueError("RNA workflow requires explicit recovery: " + state["status"])
            actions.check(state)
            following = snapshot.next
            if len(following) != 1 or following[0] not in allowed:
                raise ValueError("RNA workflow action requires prior stage " + (following[0] if following else "new planning cycle"))
            if node == "run" and following[0] == "run":
                if state["trial_index"] >= len(state["trial_ids"]) or state["trial_ids"][state["trial_index"]] != experiment_id:
                    raise ValueError("Execution must match the next exact declared trial")
            result = workflow.resume(session_id, expected_nodes=allowed)
            actual = workflow.graph.get_state(config).values
            if (node == "run" and actual["phase"] == "run" and actual["status"] == "failed"
                    and (actual.get("failure") or {}).get("kind") == "trial_failed"):
                break  # Retain numeric failures in the public receipt; continuation remains blocked.
            if actual["status"] not in {"ready", "review_required", "completed", "abstained"}:
                error = actual.get("failure") or {}
                raise ValueError("RNA workflow stopped: " + error.get("message", actual["status"]))
            if actual["phase"] == node and not result.get("__interrupt__"):
                break
        else:
            raise ValueError("RNA workflow action exceeded bounded node advances")
    result_cursor = actual
    actual = planner.get(session_id)
    if node == "conclusion_review":
        return {"status": result_cursor["status"], "final": result_cursor["status"] in {"completed", "abstained"},
            "recommendation": deepcopy(actual["recommendation"]),
            "review": deepcopy(ledger.get(session_id, node, 0)["result"]["evidence"]),
            "scientific_qualification": "not_established"}
    if node in {"discovery", "candidate_review", "implementation", "design_review"}:
        return deepcopy(ledger.get(session_id, node, 0)["result"]["evidence"])
    if node == "generate":
        return deepcopy(actual["scientific_candidates"])
    if node == "resolve":
        return deepcopy(actual["candidates"])
    if node == "design":
        return deepcopy(actions._drafts(actual))
    if node == "declare":
        return deepcopy(next(row for row in actual["experiments"] if row["id"] == spec["id"]))
    if node == "run":
        return deepcopy([row for row in actual["results"] if row["experiment_id"] == experiment_id])
    return {"status": "provisional", "final": False, "recommendation": deepcopy(actual["recommendation"]),
            "conclusion_review_status": "required", "scientific_qualification": "not_established"}


def refresh_workspace_context(planner, before, after, *, scientific_workflow=None):
    """Bind controller-owned scientific metadata updates without accepting drift."""
    if not is_rna_plan(before):
        return
    allowed = {"scientific_base_revision", "scientific_trial_definitions"}
    if any((key in before) != (key in after) or before.get(key) != after.get(key)
           for key in (set(before) | set(after)) - allowed):
        raise ValueError("Only scientific workspace execution references may refresh a graph checkpoint")
    changed = sorted(key for key in allowed if (key in before) != (key in after) or before.get(key) != after.get(key))
    if not changed:
        return
    checkpoint = planner.methods.root / CHECKPOINT_NAME
    if not checkpoint.exists():
        return
    ledger = WorkflowOperationLedger(planner.methods.root / LEDGER_NAME)
    actions = WorkflowActions(planner, ledger, actor="workspace-controller", selector=None, scientific_workflow=scientific_workflow)
    from .langgraph_workflow import SerialRNAWorkflow
    with SerialRNAWorkflow(actions, checkpoint, stepwise=True) as workflow:
        with workflow.invocation_lease(before["session_id"]):
            config = workflow._config(before["session_id"])
            snapshot = workflow.graph.get_state(config)
            if not snapshot.values:
                return
            if snapshot.values["expected_planner_hash"] != digest(before):
                raise ValueError("Scientific workspace graph checkpoint changed")
            before_hash, after_hash = digest(before), digest(after)
            request = {"session_id": before["session_id"], "before_planner_hash": before_hash,
                "after_planner_hash": after_hash, "binding_digest": snapshot.values["binding_digest"]}
            def retain_bridge():
                original = planner.methods.get("method_plan_snapshot", before_hash)
                revised = planner.methods.get("method_plan_snapshot", after_hash)
                if (digest(original) != before_hash or digest(revised) != after_hash
                        or original != before or revised != after or digest(planner.get(before["session_id"])) != after_hash
                        or any((key in original) != (key in revised) or original.get(key) != revised.get(key)
                               for key in (set(original) | set(revised)) - allowed)):
                    raise ValueError("Scientific metadata bridge differs from its immutable planner snapshots")
                return {"before_planner_hash": before_hash, "after_planner_hash": after_hash,
                    "changed_fields": changed}
            attempt = len([row for row in ledger.replay(before["session_id"]) if row["node"] == "workspace_context"])
            ledger.run(before["session_id"], "workspace_context", attempt, request, retain_bridge)
            updated = {**snapshot.values, "expected_planner_hash": after_hash}
            actions.check(updated)
            workflow.graph.update_state(config, updated, as_node=snapshot.values["phase"])


def require_completed_parent(planner, session_id, *, scientific_workflow=None):
    """Verify a finished scientific route before an explicitly reviewed follow-up."""
    from .workflow_state import validate_workflow_state, planning_binding
    checkpoint = planner.methods.root / CHECKPOINT_NAME
    ledger_path = planner.methods.root / LEDGER_NAME
    if not checkpoint.is_file() or not ledger_path.is_file():
        raise ValueError("RNA follow-up requires a completed parent graph recommendation")
    ledger = WorkflowOperationLedger(ledger_path, readonly=True)
    actions = WorkflowActions(planner, ledger, actor="followup-verifier", selector=None, scientific_workflow=scientific_workflow)
    from .langgraph_workflow import SerialRNAWorkflow
    with SerialRNAWorkflow(actions, checkpoint) as workflow:
        with workflow.invocation_lease(session_id):
            snapshot = workflow.graph.get_state(workflow._config(session_id))
            if not snapshot.values:
                raise ValueError("RNA follow-up requires a completed parent graph recommendation")
            state = validate_workflow_state(snapshot.values)
            if (snapshot.next or state["phase"] != "conclusion_review" or state["status"] != "completed"
                    or not state["operation_refs"] or state["operation_refs"][-1] != "conclusion_review:0"):
                raise ValueError("RNA follow-up requires a completed parent graph recommendation")
            current = actions.check(state, expected_hash=False)
            actions.validate_evidence(state)
            completed = ledger.get(session_id, "conclusion_review", 0)
            if (completed is None or completed["status"] != "completed"
                    or completed["result"]["after_planner_hash"] != state["expected_planner_hash"]
                    or any(state[key] != value for key, value in completed["result"]["update"].items())):
                raise ValueError("Parent graph recommendation differs from its retained completion")
            frozen = planner.methods.get("method_plan_snapshot", state["expected_planner_hash"])
            # Existing approval/full-execution APIs mutate only these follow-up
            # records. Scientific choices, evidence and requirements stay fixed.
            allowed = {"approvals", "full_execution", "analysis_proposal_id"}
            if (digest(frozen) != state["expected_planner_hash"] or planning_binding(frozen) != planning_binding(current)
                    or any((key in frozen) != (key in current) or frozen.get(key) != current.get(key)
                           for key in (set(frozen) | set(current)) - allowed)):
                raise ValueError("Parent graph scientific evidence or recommendation changed before follow-up")
            return {"session_id": session_id, "planner_hash": state["expected_planner_hash"],
                    "binding_digest": state["binding_digest"]}


def governed_followup(planner, session_id, *, kind, request_id, actor, operation, verify, scientific_workflow=None):
    """Separate durable check→execute→verify route; no implicit approval or retry."""
    from typing import TypedDict
    from langgraph.graph import StateGraph, START, END
    from .method_validation import finite_json
    from devharness_core.provenance import canonical_json
    if kind not in {"full", "rerun"} or type(request_id) is not str or not request_id or len(request_id) > 300:
        raise ValueError("Use an exact bounded governed follow-up identity")
    parent = require_completed_parent(planner, session_id, scientific_workflow=scientific_workflow)
    identity = digest({"parent": parent, "kind": kind, "request_id": request_id})[:32]
    ledger = WorkflowOperationLedger(planner.methods.root / LEDGER_NAME)
    request = {"parent": parent, "kind": kind, "request_id": request_id, "actor": actor}

    class FollowupState(TypedDict):
        phase: str
        parent: dict
        output_ref: dict | None

    def check(state):
        if state != {"phase": "check", "parent": parent, "output_ref": None}:
            raise ValueError("Follow-up check requires its exact initial node input")
        actual = ledger.run(identity, "check", 0, request,
                            lambda: require_completed_parent(planner, session_id, scientific_workflow=scientific_workflow))
        if actual != parent:
            raise ValueError("Follow-up parent binding changed")
        return {"phase": "execute"}

    def execute(state):
        prior = ledger.get(identity, "check", 0)
        if (state["phase"] != "execute" or state["parent"] != parent or state["output_ref"] is not None
                or prior is None or prior["status"] != "completed"):
            raise ValueError("Follow-up execution requires its completed check node")
        def dispatch():
            if require_completed_parent(planner, session_id, scientific_workflow=scientific_workflow) != parent:
                raise ValueError("Follow-up parent changed before dispatch")
            value = operation()
            directory = planner.methods.root / "workflow-followups" / identity
            from .method_executor import _path
            directory = _path(directory)
            directory.mkdir(parents=True, exist_ok=True)
            path = _path(directory / "output.json")
            with path.open("xb") as handle:
                handle.write(canonical_json(value))
            record = planner.methods.import_artifact(path, artifact_type="json", actor=actor)
            return {key: record[key] for key in ("artifact_id", "sha256", "artifact_type", "size_bytes")}
        ref = ledger.run(identity, "execute", 0, request, dispatch)
        return {"phase": "verify", "output_ref": ref}

    def verified(state):
        prior = ledger.get(identity, "execute", 0)
        if (state["phase"] != "verify" or state["parent"] != parent or prior is None
                or prior["status"] != "completed" or state["output_ref"] != prior["result"]):
            raise ValueError("Follow-up verification requires its retained exact execution output")
        value = read_output(state["output_ref"])
        def validate():
            verify(value)
            return {"output_sha256": digest(value), "parent": parent}
        receipt = ledger.run(identity, "verify", 0, {**request, "output_ref": state["output_ref"]}, validate)
        # Recheck actual local execution evidence even for a completed replay.
        verify(value)
        if receipt != {"output_sha256": digest(value), "parent": parent}:
            raise ValueError("Follow-up verification receipt differs from actual output")
        return {"phase": "completed"}

    def read_output(reference):
        path = planner.methods.artifact_path(reference["artifact_id"])
        record = planner.methods.get("artifact", reference["artifact_id"])
        if {key: record[key] for key in reference} != reference:
            raise ValueError("Follow-up execution artifact reference changed")
        return finite_json(path)

    from .langgraph_workflow import SerialRNAWorkflow
    actions = WorkflowActions(planner, ledger, actor=actor, selector=None, scientific_workflow=scientific_workflow)
    with SerialRNAWorkflow(actions, planner.methods.root / CHECKPOINT_NAME) as controller:
        builder = StateGraph(FollowupState)
        for name, callback in (("check", check), ("execute", execute), ("verify", verified)):
            builder.add_node(name, callback)
        builder.add_edge(START, "check")
        builder.add_edge("check", "execute")
        builder.add_edge("execute", "verify")
        builder.add_edge("verify", END)
        graph = builder.compile(checkpointer=controller.graph.checkpointer)
        config = controller._config(identity)
        with controller.invocation_lease(identity):
            snapshot = graph.get_state(config)
            if not snapshot.values:
                result = graph.invoke({"phase": "check", "parent": parent, "output_ref": None}, config=config)
            elif snapshot.next:
                result = graph.invoke(None, config=config)
            else:
                result = snapshot.values
            if result["phase"] != "completed":
                raise ValueError("Governed follow-up did not complete verification")
            # Completed returns are read-only replays, never renewed execution.
            verified({**result, "phase": "verify"})
            return read_output(result["output_ref"])


def workflow_full_execution(planner, session_id, *, actor, allow_data_execution=False, executor=None, scientific_workflow=None):
    if not is_rna_plan(planner.get(session_id)):
        return planner.run_full(session_id, actor=actor, allow_data_execution=allow_data_execution, executor=executor)
    if allow_data_execution is not True:
        raise PermissionError("Full data execution requires explicit authorization")
    full = planner.get(session_id).get("full_execution")
    if not full or not full.get("step_id"):
        raise ValueError("Full execution requires its existing exact approved step")
    def verify(value):
        if (planner.methods.get("run", value["run_id"]) != value
                or value["step_id"] != full["step_id"] or not planner.methods.provenance(value["run_id"])["valid"]):
            raise ValueError("Full execution output differs from its retained method evidence")
    return governed_followup(planner, session_id, kind="full", request_id=full["step_id"], actor=actor,
        operation=lambda: planner.run_full(session_id, actor=actor, allow_data_execution=allow_data_execution, executor=executor),
        verify=verify, scientific_workflow=scientific_workflow)


def workflow_source(planner, session_id, url, *, allowed_hosts, actor, allow_network=False):
    """Source changes require a fresh cycle after any graph operation begins."""
    if is_rna_plan(planner.get(session_id)):
        path = planner.methods.root / LEDGER_NAME
        if path.exists() and WorkflowOperationLedger(path, readonly=True).replay(session_id):
            raise ValueError("RNA workflow sources are frozen after the workflow starts; create a new planning cycle before adding sources")
    return planner.retrieve_evidence(session_id, url, allowed_hosts=allowed_hosts, actor=actor, allow_network=allow_network)
