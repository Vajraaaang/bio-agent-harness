"""Action-scoped RNA entrypoints over fixed and adaptive LangGraph routes.

Each call authorizes only its requested model or numeric operation. Checkpoints
retain facts, never runtime permission; generic non-RNA planner uses stay local.
"""
import math
from contextlib import contextmanager, nullcontext
from copy import deepcopy
from functools import partial

from devbench.engine.workflow_operations import LEDGER_NAME as LEDGER_NAME
from devbench.engine.workflow_operations import WorkflowOperationLedger

from .workflow_actions import WorkflowActions, require_fixed_lineage
from .workflow_state import digest

CHECKPOINT_NAME = "rna-workflow-checkpoints.sqlite"


def _replay_input(delivery, expected_sha256):
    from devbench.analysis.evidence_access import EvidenceDelivery
    if type(delivery) is not EvidenceDelivery:
        raise PermissionError("Numerical replay requires a separate original-input entitlement")
    with delivery.broker.authority.ledger._database() as db:
        context, policy = delivery.broker._context_policy(db, delivery.context_id)
        source = delivery.broker._source(db, delivery.source_id)
        if (source["content_sha256"] != expected_sha256 or context["role"] != "scientist"
                or context["phase"] not in {"analysis", "readback"}
                or not {"original_source_data", "replay_input"} <= set(source["tags"])
                or set(source["tags"]) & {"audit_replay", "protected_evaluation"}):
            raise PermissionError("Numerical replay requires a separate original-input entitlement")
        delivery.broker._check(context, policy, source, None)


def validate_erql_replay(package, expected_root, *, entitlement, input_path, input_delivery,
                         grant, allow_data_execution):
    """Validate fresh replay scope and separate input custody without execution."""
    import math
    import re
    import time
    from pathlib import Path

    from devbench.analysis.bundles import _canonical, _document, _load_replay, _sha
    from devbench.study.evidence import _read
    from devharness_controlled.erql_contracts import parse_erql_plan, read_erql_request
    from devharness_controlled.trust import verify_plan_approvals

    if allow_data_execution is not True:
        raise PermissionError("Numerical replay requires explicit new execution permission")
    report, manifest, members = _load_replay(package, expected_root, entitlement)
    expected = {"format", "scope", "grant_id", "actor", "package_root_sha256", "plan_sha256", "request_sha256",
                "source_snapshot_sha256", "environment_sha256", "expires_at", "max_jobs"}
    pins = {"package_root_sha256": expected_root, "plan_sha256": manifest["plan_sha256"],
            "request_sha256": manifest["external_inputs"][0]["sha256"],
            "source_snapshot_sha256": manifest["source_binding"]["sha256"],
            "environment_sha256": _sha(_canonical(manifest["environment"]))}
    if (type(grant) is not dict or set(grant) != expected or grant["format"] != "devbench.erql-replay-grant/1"
            or grant["scope"] != "synthetic_numerical_replay" or type(grant["max_jobs"]) is not int or grant["max_jobs"] != 1
            or any(grant[key] != value for key, value in pins.items())
            or any(type(grant[key]) is not str or re.fullmatch(r"[A-Za-z0-9_.:-]{1,96}", grant[key]) is None for key in ("actor", "grant_id"))
            or type(grant["expires_at"]) not in (int, float) or not math.isfinite(grant["expires_at"])
            or grant["expires_at"] <= time.time()):
        raise PermissionError("Invalid or expired exact numerical replay grant")
    _replay_input(input_delivery, pins["request_sha256"])
    raw = _read(Path(input_path), manifest["external_inputs"][0]["size_bytes"])
    if len(raw) != manifest["external_inputs"][0]["size_bytes"] or _sha(raw) != pins["request_sha256"]:
        raise ValueError("replay_external_input_changed")
    _replay_input(input_delivery, pins["request_sha256"])
    plan = parse_erql_plan(_document(members[manifest["artifacts"]["plan.json"]["member"]]))
    read_erql_request(input_path, plan)
    approvals = _document(members[manifest["artifacts"]["approvals.json"]["member"]])
    signers = _document(members[manifest["artifacts"]["trust-registry.json"]["member"]])
    verify_plan_approvals(plan, approvals, signers)
    from devharness_controlled.erql_contracts import DRIVER, IMAGE, LOCK
    if (manifest["environment"]["image_id"] != IMAGE or _sha(_read(DRIVER, 1024**2)) != plan.driver_sha256
            or _sha(_read(LOCK, 1024**2)) != plan.package_lock_sha256):
        raise ValueError("replay_installed_source_or_environment_changed")
    return report, manifest, members, plan, approvals, signers, raw


class ERQLReplayAuthority:
    """A fresh reserved replay attempt; archived records cannot instantiate it."""

    def __init__(self, authority, token, grant, plan, *, entitlement, input_delivery):
        self.authority, self.token, self.grant, self.plan = authority, token, deepcopy(grant), plan
        self.entitlement, self.input_delivery = entitlement, input_delivery
        self.expires_at = None

    def validate(self, plan):
        if plan.sha256() != self.plan.sha256() or self.token["request_sha256"] != plan.request_sha256:
            raise PermissionError("Replay authority differs from its exact numerical plan")

    @contextmanager
    def guard(self):
        self.entitlement.require(self.entitlement.content_sha256)
        _replay_input(self.input_delivery, self.plan.request_sha256)
        with self.authority._execution_tx(self.token["lineage_id"]) as db:
            state = self.authority._load_state(db, self.token["lineage_id"])
            self.authority._active(db, self.token["lineage_id"], state, self.token)
            attempt = self.authority._checked_attempt(db, self.token)
            if attempt["execution_state"] != "submission_uncertain":
                raise PermissionError("Replay dispatch has no reserved submission intent")
            self.expires_at = min(state["expires_at"], self.grant["expires_at"])
            yield {"scope": "synthetic_numerical_replay", "token": self.token,
                   "grant_sha256": digest(self.grant), "publication_eligible": False}

    def authorize_dispatch(self, timestamp):
        if self.expires_at is None or timestamp.timestamp() >= self.expires_at:
            raise PermissionError("Numerical replay grant expired before observed daemon start")


def _compare_replay_document(left, right, path, mismatches):
    if type(left) is float and type(right) in (int, float):
        matches = math.isclose(left, right, abs_tol=1e-14, rel_tol=1e-10)
    elif type(left) is dict and type(right) is dict and left.keys() == right.keys():
        for key in left:
            _compare_replay_document(
                left[key], right[key], path + "/" + key, mismatches,
            )
        return
    elif type(left) is list and type(right) is list and len(left) == len(right):
        for index, (a, b) in enumerate(zip(left, right, strict=True)):
            _compare_replay_document(a, b, path + "/" + str(index), mismatches)
        return
    else:
        matches = type(left) is type(right) and left == right
    if not matches and len(mismatches) < 32:
        mismatches.append(path)


def replay_fixed_erql(package, expected_root, output, *, entitlement, input_path, input_delivery,
                      grant, execution_ledger, allow_data_execution=False):
    """Reexecute one fixed numerical request under fresh, single-job authority."""
    from pathlib import Path

    from devbench.analysis.bundles import _canonical, _document, _sha
    from devbench.engine.execution_snapshot import current_binding
    from devbench.engine.workspace import protected_path
    from devbench.study.evidence import _read
    from devharness_controlled.native_executor import execute_erql_plan
    from devharness_core.provenance import verify_execution

    report, manifest, members, plan, approvals, signers, request = validate_erql_replay(
        package, expected_root, entitlement=entitlement, input_path=input_path, input_delivery=input_delivery,
        grant=grant, allow_data_execution=allow_data_execution)
    current = current_binding()
    if current is None or current["sha256"] != manifest["source_binding"]["sha256"]:
        raise PermissionError("Numerical replay requires the exact installed source snapshot")
    output, ledger_path = protected_path(output), protected_path(execution_ledger)
    if (output.exists() or not output.parent.is_dir() or ledger_path.is_relative_to(output)
            or Path(input_path).is_relative_to(output)):
        raise ValueError("Numerical replay requires fresh output disjoint from authority and inputs")
    authority = WorkflowOperationLedger(ledger_path, scientific=True).scientific
    lineage = "replay-" + grant["grant_id"]
    binding = authority.commit(lineage, expected_revision=0, record={"kind": "binding", "record_id": "exact-replay",
        "cohort_sha256": plan.source_sha256, "design_sha256": plan.design_sha256,
        "procedure_sha256": plan.sha256(), "family_allocation_sha256": plan.allocation.sha256(),
        "exposure_basis_sha256": expected_root, "policy_sha256": _sha(_canonical(grant))})
    authority.commit(lineage, expected_revision=1, record={"kind": "grant", "record_id": grant["grant_id"],
        "binding_id": "exact-replay", "grant_sha256": _sha(_canonical(grant)), "dependencies": [
            {"record_id": "exact-replay", "kind": "binding", "sha256": binding["sha256"]}]})
    def authorize(_):
        validate_erql_replay(package, expected_root, entitlement=entitlement, input_path=input_path,
            input_delivery=input_delivery, grant=grant, allow_data_execution=True)
        return {"authorized": True, "authorization_sha256": _sha(_canonical(grant)), "expires_at": grant["expires_at"]}
    state = authority.activate_execution(lineage, expected_revision=2, binding_id="exact-replay",
        grant_id=grant["grant_id"], max_jobs=1, authorize=authorize)
    token = authority.reserve_attempt(lineage, publication_unit="numerical-replay", request_sha256=plan.request_sha256,
        environment_sha256=grant["environment_sha256"], backend_id="fixed_erql_replay_1",
        expected_epoch=state["epoch"], binding_id="exact-replay", expected_revision=state["revision"])
    scope = ERQLReplayAuthority(authority, token, grant, plan, entitlement=entitlement, input_delivery=input_delivery)
    _replay_input(input_delivery, plan.request_sha256)
    reservation = input_delivery.broker.reserve(input_delivery.context_id, input_delivery.source_id, request, opaque=True)
    input_delivery.broker.mark_attempted(reservation["reservation_id"])
    output.mkdir(mode=0o700)
    (output / "replay-grant.json").write_bytes(_canonical(grant))
    (output / "request.json").write_bytes(request)
    outcome = None
    def submit(_):
        nonlocal outcome
        outcome = execute_erql_plan(plan, approvals, signers, request_path=output / "request.json",
                                   output_dir=output / "execution", replay_authority=scope)
        return token["attempt_id"]
    authority.dispatch_attempt(token, submit)
    audit_root = outcome["receipt"]["root_hash"]
    transport_path = output / "execution/transport.json"
    transport = _document(_read(transport_path, 16 * 1024**2)) if transport_path.exists() else None
    def integrity():
        return verify_execution(
            output / "execution/audit/manifest.json", audit_root,
        )["valid"]

    succeeded = outcome["status"] == "analysis_complete_pending_review"
    cleanup = "confirmed" if transport and transport["cleanup"]["removed"] else "unknown"
    authority.reconcile_attempt(token, job_id=token["attempt_id"], execution_state="succeeded" if succeeded else "failed",
        cleanup_state=cleanup, usage={}, receipt_sha256=audit_root, verify_observation=lambda _: integrity())
    mismatches = []
    if succeeded:
        for name, path in (("result.json", "native-output/result.json"), ("family-evidence.json", "family-evidence.json")):
            _compare_replay_document(
                _document(members[manifest["artifacts"][name]["member"]]),
                _document(_read(output / "execution" / path, 16 * 1024**2)),
                name, mismatches,
            )
    match = succeeded and not mismatches and cleanup == "confirmed" and integrity()
    comparison = {"operation_kind": "authorized_numerical_replay", "package_root_sha256": expected_root,
        "original_root_sha256": manifest["original_root_sha256"], "replay_root_sha256": audit_root,
        "scope": "synthetic_numerical_replay", "numerical_match": match, "mismatches": mismatches,
        "comparison": manifest["numerical_comparison"], "original_status": manifest["original_status"],
        "replay_status": outcome["status"], "scientific_acceptance": "not_granted", "publication_eligible": False,
        "numerical_stage_launches": 1, "model_sessions": 0, "qualification": "blocked", "token": token}
    verification_sha = _sha(_canonical(comparison))
    if match:
        authority.stage_output(token, artifact_sha256=audit_root, verification_sha256=verification_sha,
                               verify_output=lambda _: integrity())
        accepted = authority.accept_output(token)
        comparison["execution_acceptance"] = accepted["acceptance_state"]
    else:
        comparison["execution_acceptance"] = "not_accepted"
    (output / "replay-comparison.json").write_bytes(_canonical(comparison))
    return comparison


def _require_fixed_entrypoint(planner, session_id):
    path = planner.methods.root / LEDGER_NAME
    if path.exists():
        require_fixed_lineage(WorkflowOperationLedger(path, readonly=True), session_id)


def _prepare_adaptive_advance(
    adaptive, *, expected_revision, manual_local_oci, resume_controller, controller,
):
    from devbench.engine.workflow_operations import OperationConflict

    if expected_revision is not None:
        if type(expected_revision) is not int or expected_revision < 0:
            raise ValueError("Expected revision must be a nonnegative integer")
        if adaptive.status()["revision"] != expected_revision:
            raise OperationConflict("Adaptive workflow revision changed before advancement")
    if manual_local_oci:
        if adaptive.controller_state()["binding"] is not None:
            raise PermissionError("The bound controller requires its own runtime capabilities")
        adaptive.manual_local_oci = True
    if resume_controller:
        if controller is None:
            raise ValueError("Controller resumption requires its fresh runtime capabilities")
        controller.bind(adaptive)
        controller.resume(adaptive, expected_revision=expected_revision)
        if expected_revision is not None:
            expected_revision = adaptive.status()["revision"]
    return expected_revision


def _prepare_research_admissions(adaptive, actions):
    from devbench.engine.workflow_operations import OperationBlocked

    from .adaptive_workflow import RESEARCH_INFERENCE

    subjects = adaptive._read_projection(lambda db: [
        step.step_id for step in adaptive._state(db).nodes.values()
        if step.purpose == RESEARCH_INFERENCE
    ])
    for subject in subjects:
        try:
            actions.prepare_research_admission(adaptive, subject)
        except OperationBlocked:
            continue


def advance_adaptive_workflow(
    planner, session_id, *, actor, executor, ledger=None, partition_check=None,
    max_steps=1, allow_data_execution=False, review_lifecycle=None,
    review_binding=None, controller=None, diagnostic_qualification=None,
    resume_controller=False, manual_local_oci=False, expected_revision=None,
    diagnostic_actions=None, tier_a_executor=None,
):
    """Advance admitted work from the authoritative lineage under a fresh grant."""
    from .adaptive_workflow import ADAPTIVE_MODE
    from .workflow_actions import create_rna_workflow

    if (allow_data_execution is not True
            or (executor is None and tier_a_executor is None)):
        raise PermissionError(
            "Adaptive dispatch requires fresh data execution authority and an executor"
        )
    if type(manual_local_oci) is not bool or manual_local_oci and (
        controller is not None or tier_a_executor is not None
    ):
        raise ValueError(
            "Manual local OCI dispatch cannot supply controller capabilities"
        )
    if ledger is None:
        ledger = WorkflowOperationLedger(
            planner.methods.root / LEDGER_NAME, scientific=True,
        )
    adaptive = planner.adaptive_workflow(
        session_id, ledger, partition_check=partition_check,
    )
    expected_revision = _prepare_adaptive_advance(
        adaptive, expected_revision=expected_revision,
        manual_local_oci=manual_local_oci, resume_controller=resume_controller,
        controller=controller,
    )
    actions = WorkflowActions(
        planner, ledger, actor=actor, selector=None, executor=executor,
        allow_data_execution=True, review_lifecycle=review_lifecycle,
        review_binding=review_binding,
        diagnostic_qualification=diagnostic_qualification,
        controller=controller, diagnostic_actions=diagnostic_actions,
        tier_a_executor=tier_a_executor,
    )
    if review_lifecycle is not None and review_lifecycle.lineage_id != session_id:
        raise ValueError("Primary review belongs to another scientific lineage")
    adaptive.review_admission = actions.adaptive_review_admission
    adaptive.diagnostic_admission = actions.adaptive_diagnostic_admission
    if tier_a_executor is not None:
        _prepare_research_admissions(adaptive, actions)
    graph = create_rna_workflow(mode=ADAPTIVE_MODE, actions=actions, adaptive=adaptive)
    if expected_revision is None:
        return graph.advance(max_steps=max_steps)
    return graph.advance(max_steps=max_steps, expected_revision=expected_revision)


def audit_workflow_design(planner, session_id, *, actor, allow_data_execution=False):
    """Run or verify only the existing audit node, without model or fit authority.

    This is a durable graph entry point, not a second controller. Later planning
    uses the same checkpoint and still requires its scientific runtime.
    """
    from .langgraph_workflow import SerialRNAWorkflow

    plan = planner.get(session_id)
    _require_fixed_entrypoint(planner, session_id)
    population = plan.get("scrna_population_input") is not None
    if not is_rna_plan(plan) or (not population and plan.get("rna_design") is None):
        raise ValueError("An explicit RNA design or supplied-population binding is required")
    if population and allow_data_execution is not True:
        raise PermissionError("The supplied-population preflight requires fresh data execution authority")
    ledger = WorkflowOperationLedger(planner.methods.root / LEDGER_NAME)
    actions = WorkflowActions(planner, ledger, actor=actor, selector=None, allow_data_execution=allow_data_execution)
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


def _workflow_advance_stop(
    session_id, phase, state=None, following=(), *, status, gate=None,
    required_action=None, reason=None, ledger=None, actions=None,
):
    state = state or {}
    refs = list(state.get("operation_refs", []))
    steps = []
    if ledger is not None:
        tail = state.get("phase")
        tail_ref = f"{tail}:{actions.operation_attempt(state, tail)}"
        inspect = refs + ([tail_ref] if tail and tail_ref not in refs else [])
        for reference in inspect:
            node, attempt = reference.rsplit(":", 1)
            record = ledger.get(session_id, node, int(attempt))
            if record is None:
                continue
            result = record.get("result") or {}
            row = {
                "node": node, "attempt": int(attempt), "status": record["status"],
                "controller_status": result.get("update", {}).get("status"),
                "request_sha256": record["request_digest"],
                "result_sha256": record["result_digest"],
            }
            evidence = result.get("evidence")
            if evidence is not None:
                row["evidence"] = deepcopy(evidence)
            steps.append(row)
    return {
        "format": "devbench.scientific-advance/1", "session_id": session_id,
        "status": status, "gate": gate,
        "next_node": following[0] if len(following) == 1 else None,
        "phase": state.get("phase"), "requested_phase": phase,
        "controller_status": state.get("status"),
        "operation_refs": refs, "steps": steps,
        "required_action": required_action, "reason": reason,
        "scientific_qualification": "not_established",
    }


def _workflow_advance_preflight(
    planner, plan, session_id, *, actor, phase, allow_data_execution,
    scientific_workflow, stopped,
):
    # Declared local analyses first reach the same durable audit barrier even
    # when no model/discovery/reviewer runtime has been configured yet.
    if (plan.get("scrna_population_input") is not None and phase == "planning"
            and not allow_data_execution):
        return stopped(
            status="awaiting_information", required_action="authorize_data_execution",
            reason="The supplied-population audit/design/pseudobulk preflight "
                   "requires fresh runtime data authority.",
        )
    if plan.get("rna_design") is not None and phase == "planning":
        audit_path = planner.methods.root / LEDGER_NAME
        audit_record = None
        if audit_path.exists():
            ledger = WorkflowOperationLedger(audit_path, readonly=True)
            audit_record = ledger.get(session_id, "audit", 0)
        if (audit_record is None
                or (audit_record.get("result") or {}).get("update", {}).get("status")
                != "ready"):
            audited = audit_workflow_design(planner, session_id, actor=actor)
            if audited["status"] != "ready":
                return audited
    if scientific_workflow is None:
        return stopped(
            status="awaiting_information",
            required_action="configure_scientific_workflow",
            reason="A trusted discovery, implementation and independent-review "
                   "runtime is required.",
        )
    return None


def _workflow_node_permission(
    node, *, selector, scientific_workflow, allow_model_execution,
):
    if node in {"generate", "resolve", "recommend"}:
        if selector is None:
            return (
                "configure_planning_selector",
                "A planning selector must be explicitly configured.",
            )
        configuration = (
            selector.configuration() if hasattr(selector, "configuration") else {}
        )
        if (configuration.get("backend") != "recorded_fixture"
                and not allow_model_execution):
            return (
                "authorize_model_execution",
                "This campaign requires current explicit model-execution permission.",
            )
    if node in {"candidate_review", "design_review", "conclusion_review"}:
        configuration = scientific_workflow.configuration()
        if (configuration.get("format")
                == "devbench.workflow-scientific-configuration/1"
                and configuration.get("reviewer") is None):
            return (
                "configure_scientific_reviewer",
                "A substantive independent scientific-review adapter is required.",
            )
        if (getattr(scientific_workflow, "allow_review_model_execution", False)
                and not allow_model_execution):
            return (
                "authorize_model_execution",
                "Scientific review requires current explicit "
                "model-execution permission.",
            )
    if (node == "implementation"
            and getattr(
                scientific_workflow, "allow_implementation_model_execution", False,
            )
            and not allow_model_execution):
        return (
            "authorize_model_execution",
            "Implementation generation requires current explicit "
            "model-execution permission.",
        )
    return None


def _campaign_snapshot_stop(workflow, actions, ledger, session_id, snapshot, stopped):
    state, following = snapshot.values, snapshot.next
    if state["status"] in {"completed", "abstained"}:
        workflow.actions.verify_terminal(state)
        return stopped(state, following, status=state["status"])
    if state["status"] not in {"ready", "review_required"}:
        return stopped(
            state, following, status="recovery_required",
            required_action="inspect_and_reconcile",
            reason=(state.get("failure") or {}).get(
                "message", "The retained controller requires explicit recovery.",
            ),
        )
    actions.check(state)
    if ledger.is_cancelled(session_id):
        return stopped(
            state, following, status="cancelled",
            required_action="inspect_cancelled_workflow",
            reason="The retained workflow is cancelled; nothing was resumed.",
        )
    if state["status"] == "review_required":
        return stopped(
            state, following, status="awaiting_information",
            required_action="inspect_scientific_review",
            reason=(state.get("failure") or {}).get(
                "message", "A scientific review requires attention.",
            ),
        )
    if len(following) != 1:
        return stopped(
            state, following, status="recovery_required",
            required_action="inspect_workflow_checkpoint",
            reason="The retained workflow has no unique next node.",
        )
    return None


def _campaign_node_stop(
    workflow, config, session_id, snapshot, *, phase, allowed, verification_only,
    permission, stopped,
):
    state, following = snapshot.values, snapshot.next
    node = following[0]
    if phase == "planning" and node in {"review", "execution_review"}:
        # Enter an unvisited interrupt once, but never send its resume value.
        if not snapshot.interrupts and (node == "review" or state["trial_ids"]):
            workflow.resume(session_id, expected_nodes={node})
            snapshot = workflow.graph.get_state(config)
            state, following = snapshot.values, snapshot.next
        return stopped(
            state, following, status="awaiting_operator_review", gate=node,
            required_action="declare_trial" if node == "review" else "run_trial",
            reason="Review the frozen trial selection." if node == "review" else
                   "Explicit runtime authority and the exact declared trial are "
                   "required before execution.",
        )
    if node not in allowed:
        return stopped(
            state, following, status="awaiting_information",
            required_action=("advance_synthesis"
                             if node in {"verify", "recommend", "conclusion_review"}
                             else "complete_controlled_trials"),
            reason="The next node is outside this explicitly requested campaign phase.",
        )
    if verification_only and node != "verify":
        return stopped(
            state, following, status="awaiting_information",
            required_action="authorize_model_execution",
            reason="Local verification completed. Further synthesis "
                   "requires matching authority.",
        )
    blocked = permission(node)
    if blocked:
        return stopped(
            state, following, status="awaiting_information",
            required_action=blocked[0], reason=blocked[1],
        )
    return None


def _resume_campaign_node(
    workflow, session_id, node, *, actions, plan, ledger, validate_synthesis,
):
    def validate(snapshot):
        binding = retained_synthesis_evidence(actions, snapshot)
        validate_synthesis(binding)

    evidence_scope = nullcontext()
    if (validate_synthesis is not None
            and plan.get("scrna_population_input") is not None):
        from .scrna_population_admission import retained_synthesis_audit

        audit = ledger.get(session_id, "audit", 0)
        evidence_scope = retained_synthesis_audit(
            session_id, audit["result"]["evidence"]["record_ref"],
        )
    with evidence_scope:
        workflow.resume(
            session_id, expected_nodes={node},
            **({"before_invoke": validate} if validate_synthesis is not None else {}),
        )


def _run_workflow_campaign(
    workflow, actions, ledger, session_id, *, phase, allowed, before_advance,
    verification_only, permission, stopped, resume_node,
):
    from .workflow_state import SERIAL_ROUTE

    config = workflow._config(session_id)
    snapshot = workflow.graph.get_state(config)
    if not snapshot.values:
        if phase != "planning":
            return stopped(
                status="awaiting_information",
                required_action="complete_controlled_trials",
                reason="No retained planning/execution cursor exists for this session.",
            )
        if before_advance is not None:
            before_advance()
        workflow.start(actions.create_state(session_id))
    for _ in range(len(SERIAL_ROUTE) + 1):
        if before_advance is not None:
            before_advance()
        snapshot = workflow.graph.get_state(config)
        stop = _campaign_snapshot_stop(
            workflow, actions, ledger, session_id, snapshot, stopped,
        )
        if stop is not None:
            return stop
        stop = _campaign_node_stop(
            workflow, config, session_id, snapshot, phase=phase, allowed=allowed,
            verification_only=verification_only, permission=permission, stopped=stopped,
        )
        if stop is not None:
            return stop
        resume_node(workflow, session_id, snapshot.next[0])
    raise ValueError("Scientific campaign exceeded its bounded node count")


def advance_workflow_to_gate(planner, session_id, *, actor, selector=None, version_ids=(),
                             allow_model_execution=False, scientific_workflow=None, phase="planning", before_advance=None,
                             allow_data_execution=False, validate_synthesis=None, verification_only=False):
    """Advance an explicitly requested campaign without acknowledging its gates.

    Runtime permissions are supplied afresh and are never reconstructed from the
    checkpoint. Every real node still runs through WorkflowActions and its ledger.
    This entrypoint cannot declare or fit a trial or reconcile an interrupted
    effect. Only the separately scoped population preflight may run registered
    non-inferential workers when fresh data authority is explicitly supplied.
    """
    from .workflow_state import SERIAL_ROUTE

    if type(phase) is not str or phase not in {"planning", "synthesis"}:
        raise ValueError("Use planning or synthesis advancement")
    if type(allow_model_execution) is not bool:
        raise ValueError("Use an explicit boolean model-execution permission")
    if type(allow_data_execution) is not bool:
        raise ValueError("Use an explicit boolean data-execution permission")
    if before_advance is not None and not callable(before_advance):
        raise ValueError("The current-runtime verifier must be a trusted callable")
    plan = planner.get(session_id)
    _require_fixed_entrypoint(planner, session_id)
    if not is_rna_plan(plan):
        raise ValueError("Automatic graph advancement requires an RNA scientific plan")
    stopped = partial(_workflow_advance_stop, session_id, phase)
    stop = _workflow_advance_preflight(
        planner, plan, session_id, actor=actor, phase=phase,
        allow_data_execution=allow_data_execution,
        scientific_workflow=scientific_workflow, stopped=stopped,
    )
    if stop is not None:
        return stop
    checkpoint = planner.methods.root / CHECKPOINT_NAME
    if phase == "synthesis" and not checkpoint.exists():
        return stopped(status="awaiting_information", required_action="complete_controlled_trials",
            reason="Synthesis requires the retained reviewed planning and controlled-trial route.")
    ledger = WorkflowOperationLedger(planner.methods.root / LEDGER_NAME)
    declared = ledger.get(session_id, "declare", 0)
    declarations = declared["request"]["declarations"] if declared is not None else []
    actions = WorkflowActions(planner, ledger, actor=actor, selector=selector, version_ids=version_ids,
        declarations=declarations, allow_model_execution=allow_model_execution,
        allow_data_execution=allow_data_execution, scientific_workflow=scientific_workflow,
        retained_synthesis=validate_synthesis is not None)
    if validate_synthesis is not None and phase != "synthesis":
        raise ValueError("Retained evidence binding is scoped to synthesis")
    allowed = (set(SERIAL_ROUTE[:SERIAL_ROUTE.index("review")]) if phase == "planning"
               else {"verify", "recommend", "conclusion_review"})

    permission = partial(
        _workflow_node_permission, selector=selector,
        scientific_workflow=scientific_workflow,
        allow_model_execution=allow_model_execution,
    )
    resume_node = partial(
        _resume_campaign_node, actions=actions, plan=plan, ledger=ledger,
        validate_synthesis=validate_synthesis,
    )

    from .langgraph_workflow import SerialRNAWorkflow

    with SerialRNAWorkflow(actions, checkpoint, stepwise=True) as workflow:
        return _run_workflow_campaign(
            workflow, actions, ledger, session_id, phase=phase, allowed=allowed,
            before_advance=before_advance, verification_only=verification_only,
            permission=permission,
            stopped=partial(stopped, ledger=ledger, actions=actions),
            resume_node=resume_node,
        )


def is_rna_plan(plan):
    observed = plan["context"]["dataset_summary"].get("observed", {})
    scrna = observed.get("scrna_input")
    input_set = observed.get("rna_input_set")
    return ("bulk_rna_input" in observed
            or type(observed.get("fastq_input")) is dict and observed["fastq_input"].get("format") == "devbench.fastq-input/1"
            or type(scrna) is dict and scrna.get("format") == "devbench.scrna-input/1"
            or type(input_set) is dict and input_set.get("format") == "devbench.rna-input-set/1"
            or "rna_execution_inputs" in plan)


def synthesis_evidence_key(investigation_id, session_id, frozen_revision, trials, results):
    return digest({"investigation_id": investigation_id, "session_id": session_id,
        "frozen_revision": frozen_revision, "trials": trials, "results": results})


def retained_synthesis_evidence(actions, snapshot):
    from .workflow_state import validate_workflow_state

    state = validate_workflow_state(snapshot.values)
    session = state["session_id"]
    if actions.ledger.is_cancelled(session):
        raise ValueError("The retained workflow is cancelled.")
    if state["status"] != "ready":
        raise ValueError((state.get("failure") or {}).get("message") or "The retained controller requires reconciliation.")
    if len(snapshot.next) != 1 or snapshot.next[0] not in {"verify", "recommend", "conclusion_review"}:
        raise ValueError("The retained controller is outside the synthesis phase.")
    node = snapshot.next[0]
    plan = actions.check(state)
    actions.check_node_entry(node, state)
    if actions.ledger.get(session, node, actions.operation_attempt(state, node)) is not None:
        raise ValueError("The next synthesis node has an existing effect that requires reconciliation.")
    trials = plan["experiments"]
    if (not trials or state["trial_index"] != len(state["trial_ids"])
            or [trial["id"] for trial in trials] != state["trial_ids"]
            or any(trial["status"] != "completed" for trial in trials)):
        raise ValueError("Synthesis requires every declared trial to be completed.")
    expected = [(trial["id"], candidate) for trial in trials for candidate in trial["candidates"]]
    observed = [(row["experiment_id"], row["candidate"]) for row in plan["results"]]
    if not expected or sorted(expected) != sorted(observed) or any(not row["provenance"].get("run_id") for row in plan["results"]):
        raise ValueError("The retained results do not cover every declared trial candidate.")
    actions.planner.evidence_assessment(session)
    receipts = [actions.ledger.get(session, "run", index) for index in range(len(trials))]
    return {"next_node": node, "session_id": session,
        "frozen_revision": receipts[-1]["result"]["after_planner_hash"],
        "data_classification": plan.get("data_classification", "unclassified"), "trials": [
            {"experiment": trial, "request_digest": receipt["request_digest"], "result_digest": receipt["result_digest"]}
            for trial, receipt in zip(trials, receipts)], "results": [
            {**row, "artifacts": [{**ref, "sha256": actions.planner.methods.get("artifact", ref["artifact_id"])["sha256"]}
                for ref in row["artifacts"]]} for row in plan["results"]]}


def synthesis_descriptor(planner, session_id, *, scientific_workflow=None, selector=None):
    from devbench.analysis.method_executor import _path

    from .langgraph_workflow import SerialRNAWorkflow

    if not is_rna_plan(planner.get(session_id)):
        raise ValueError("Synthesis requires a retained RNA scientific plan.")
    checkpoint, ledger_path = (planner.methods.root / name for name in (CHECKPOINT_NAME, LEDGER_NAME))
    for path in (checkpoint, ledger_path):
        if path.is_symlink() or not path.exists():
            raise ValueError("Retained synthesis checkpoint or operation ledger is unavailable.")
        _path(path)
    ledger = WorkflowOperationLedger(ledger_path, readonly=True)
    actions = WorkflowActions(planner, ledger, actor="synthesis-readiness", selector=selector,
        scientific_workflow=scientific_workflow, retained_synthesis=True)
    with SerialRNAWorkflow(actions, checkpoint, stepwise=True) as workflow:
        with workflow.invocation_lease(session_id):
            return retained_synthesis_evidence(actions, workflow.graph.get_state(workflow._config(session_id)))


def synthesis_ready(planner, session_id, *, scientific_workflow=None, selector=None):
    """Observe an existing completed-trial cursor without resuming any graph node."""
    try:
        synthesis_descriptor(planner, session_id, scientific_workflow=scientific_workflow, selector=selector)
    except (ValueError, KeyError):
        return False
    return True


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


def _check_workflow_action_cursor(
    actions, state, following, allowed, node, experiment_id,
):
    if state["status"] not in {"ready", "review_required"}:
        raise ValueError("RNA workflow requires explicit recovery: " + state["status"])
    actions.check(state)
    if len(following) != 1 or following[0] not in allowed:
        prior = following[0] if following else "new planning cycle"
        raise ValueError("RNA workflow action requires prior stage " + prior)
    if node == "run" and following[0] == "run":
        index = state["trial_index"]
        trials = state["trial_ids"]
        if index >= len(trials) or trials[index] != experiment_id:
            raise ValueError("Execution must match the next exact declared trial")


def _run_workflow_action(workflow, actions, session_id, node, allowed, experiment_id):
    config = workflow._config(session_id)
    snapshot = workflow.graph.get_state(config)
    if not snapshot.values:
        if node != "discovery":
            raise ValueError(
                "RNA workflow must start with discovery; prior discovery and "
                "scientific reviews cannot be bypassed"
            )
        workflow.start(actions.create_state(session_id))
    for _ in range(8):
        snapshot = workflow.graph.get_state(config)
        _check_workflow_action_cursor(
            actions, snapshot.values, snapshot.next, allowed, node, experiment_id,
        )
        result = workflow.resume(session_id, expected_nodes=allowed)
        actual = workflow.graph.get_state(config).values
        if (node == "run" and actual["phase"] == "run" and actual["status"] == "failed"
                and (actual.get("failure") or {}).get("kind") == "trial_failed"):
            # Retain numeric failures in the public receipt.
            return actual
        if actual["status"] not in {
            "ready", "review_required", "completed", "abstained",
        }:
            error = actual.get("failure") or {}
            raise ValueError(
                "RNA workflow stopped: " + error.get("message", actual["status"])
            )
        if actual["phase"] == node and not result.get("__interrupt__"):
            return actual
    raise ValueError("RNA workflow action exceeded bounded node advances")


def _workflow_action_result(
    actions, ledger, session_id, node, result_cursor, actual, spec, experiment_id,
):
    if node == "conclusion_review":
        return {
            "status": result_cursor["status"],
            "final": result_cursor["status"] in {"completed", "abstained"},
            "recommendation": deepcopy(actual["recommendation"]),
            "review": deepcopy(ledger.get(session_id, node, 0)["result"]["evidence"]),
            "scientific_qualification": "not_established",
        }
    if node in {"discovery", "candidate_review", "implementation", "design_review"}:
        return deepcopy(ledger.get(session_id, node, 0)["result"]["evidence"])
    if node == "generate":
        return deepcopy(actual["scientific_candidates"])
    if node == "resolve":
        return deepcopy(actual["candidates"])
    if node == "design":
        return deepcopy(actions._drafts(actual))
    if node == "declare":
        matching = next(row for row in actual["experiments"] if row["id"] == spec["id"])
        return deepcopy(matching)
    if node == "run":
        return deepcopy([
            row for row in actual["results"] if row["experiment_id"] == experiment_id
        ])
    return {
        "status": "provisional", "final": False,
        "recommendation": deepcopy(actual["recommendation"]),
        "conclusion_review_status": "required",
        "scientific_qualification": "not_established",
    }


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
    _require_fixed_entrypoint(planner, session_id)
    attachment = None
    if execution_inputs_ref is not None or execution_workspace is not None:
        if node != "design" or not is_rna_plan(plan) or execution_inputs_ref is None or execution_workspace is None:
            raise ValueError("Late execution inputs require an RNA design action, sealed reference and workspace")
        from devbench.analysis.method_executor import _path
        attachment = {"record_ref": deepcopy(execution_inputs_ref), "workspace": str(_path(execution_workspace))}
    if not is_rna_plan(plan):
        return _direct(planner, session_id, node, **options)
    if scientific_workflow is None:
        raise PermissionError("RNA actions require a trusted scientific workflow runtime for discovery, implementation and independent reviews")
    allowed = {
        "discovery": {"question", "discovery"}, "generate": {"generate"},
        "candidate_review": {"candidate_review"}, "resolve": {"resolve"},
        "implementation": {"implementation"}, "design": {"design"},
        "design_review": {"design_review"}, "declare": {"review", "declare"},
        "run": {"execution_review", "run"}, "recommend": {"verify", "recommend"},
        "conclusion_review": {"conclusion_review"},
    }
    if node not in allowed:
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
        result_cursor = _run_workflow_action(
            workflow, actions, session_id, node, allowed[node], experiment_id,
        )
    return _workflow_action_result(
        actions, ledger, session_id, node, result_cursor, planner.get(session_id), spec,
        experiment_id,
    )


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
    from .workflow_state import planning_binding, validate_workflow_state
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


def _followup_execute(
    state, *, parent, current_parent, planner, ledger, identity, request, actor,
    operation,
):
    from devharness_core.provenance import canonical_json

    prior = ledger.get(identity, "check", 0)
    if (state["phase"] != "execute" or state["parent"] != parent
            or state["output_ref"] is not None
            or prior is None or prior["status"] != "completed"):
        raise ValueError("Follow-up execution requires its completed check node")

    def dispatch():
        if current_parent() != parent:
            raise ValueError("Follow-up parent changed before dispatch")
        value = operation()
        directory = planner.methods.root / "workflow-followups" / identity
        from devbench.analysis.method_executor import _path

        directory = _path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        path = _path(directory / "output.json")
        with path.open("xb") as handle:
            handle.write(canonical_json(value))
        record = planner.methods.import_artifact(
            path, artifact_type="json", actor=actor,
        )
        return {
            key: record[key]
            for key in ("artifact_id", "sha256", "artifact_type", "size_bytes")
        }

    ref = ledger.run(identity, "execute", 0, request, dispatch)
    return {"phase": "verify", "output_ref": ref}


def _followup_read_output(planner, reference):
    from devbench.analysis.method_validation import finite_json

    path = planner.methods.artifact_path(reference["artifact_id"])
    record = planner.methods.get("artifact", reference["artifact_id"])
    if {key: record[key] for key in reference} != reference:
        raise ValueError("Follow-up execution artifact reference changed")
    return finite_json(path)


def _run_followup_graph(
    controller, identity, parent, check, execute, verified, read_output,
):
    from typing import TypedDict

    from langgraph.graph import END, START, StateGraph

    class FollowupState(TypedDict):
        phase: str
        parent: dict
        output_ref: dict | None

    builder = StateGraph(FollowupState)
    builder.add_node("check", check)
    builder.add_node("execute", execute)
    builder.add_node("verify", verified)
    builder.add_edge(START, "check")
    builder.add_edge("check", "execute")
    builder.add_edge("execute", "verify")
    builder.add_edge("verify", END)
    graph = builder.compile(checkpointer=controller.graph.checkpointer)
    config = controller._config(identity)
    with controller.invocation_lease(identity):
        snapshot = graph.get_state(config)
        if not snapshot.values:
            result = graph.invoke(
                {"phase": "check", "parent": parent, "output_ref": None}, config=config,
            )
        elif snapshot.next:
            result = graph.invoke(None, config=config)
        else:
            result = snapshot.values
        if result["phase"] != "completed":
            raise ValueError("Governed follow-up did not complete verification")
        # Completed returns are read-only replays, never renewed execution.
        verified({**result, "phase": "verify"})
        return read_output(result["output_ref"])


def governed_followup(planner, session_id, *, kind, request_id, actor, operation, verify, scientific_workflow=None):
    """Separate durable check→execute→verify route; no implicit approval or retry."""
    if kind not in {"full", "rerun"} or type(request_id) is not str or not request_id or len(request_id) > 300:
        raise ValueError("Use an exact bounded governed follow-up identity")
    current_parent = partial(
        require_completed_parent, planner, session_id,
        scientific_workflow=scientific_workflow,
    )
    parent = current_parent()
    identity = digest({"parent": parent, "kind": kind, "request_id": request_id})[:32]
    ledger = WorkflowOperationLedger(planner.methods.root / LEDGER_NAME)
    request = {"parent": parent, "kind": kind, "request_id": request_id, "actor": actor}
    read_output = partial(_followup_read_output, planner)
    execute = partial(
        _followup_execute, parent=parent, current_parent=current_parent,
        planner=planner, ledger=ledger, identity=identity, request=request,
        actor=actor, operation=operation,
    )

    def check(state):
        if state != {"phase": "check", "parent": parent, "output_ref": None}:
            raise ValueError("Follow-up check requires its exact initial node input")
        actual = ledger.run(identity, "check", 0, request, current_parent)
        if actual != parent:
            raise ValueError("Follow-up parent binding changed")
        return {"phase": "execute"}

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

    from .langgraph_workflow import SerialRNAWorkflow

    actions = WorkflowActions(planner, ledger, actor=actor, selector=None, scientific_workflow=scientific_workflow)
    with SerialRNAWorkflow(actions, planner.methods.root / CHECKPOINT_NAME) as controller:
        return _run_followup_graph(
            controller, identity, parent, check, execute, verified, read_output,
        )


def workflow_full_execution(planner, session_id, *, actor, allow_data_execution=False, executor=None, scientific_workflow=None):
    _require_fixed_entrypoint(planner, session_id)
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
    _require_fixed_entrypoint(planner, session_id)
    if is_rna_plan(planner.get(session_id)):
        path = planner.methods.root / LEDGER_NAME
        if path.exists() and WorkflowOperationLedger(path, readonly=True).replay(session_id):
            raise ValueError("RNA workflow sources are frozen after the workflow starts; create a new planning cycle before adding sources")
    return planner.retrieve_evidence(session_id, url, allowed_hosts=allowed_hosts, actor=actor, allow_network=allow_network)


class SyntheticERQLCapsule:
    """Local operator-bound evaluation capsule with one signed family inventory."""

    def __init__(self, directory):
        from pathlib import Path

        from devharness_controlled.erql_contracts import (
            CANONICAL,
            ERQLFamilyPlan,
            parse_erql_plan,
        )
        from devharness_controlled.verification import _json, _read

        self.directory = Path(directory)
        if (not self.directory.is_absolute() or self.directory.resolve() != self.directory
                or not self.directory.is_dir()):
            raise ValueError("ERQL capsule requires a canonical absolute directory")
        self.plan = parse_erql_plan(_json(_read(self.directory / "plan.json", limit=8 * 1024**2)))
        if not isinstance(self.plan, ERQLFamilyPlan) or self.plan.profile != CANONICAL:
            raise ValueError("The evaluation route requires a signed canonical ERQL family plan")
        self.approvals = _json(_read(self.directory / "approvals.json", limit=1024**2))
        self.signers = _json(_read(self.directory / "trust-registry.json", limit=1024**2))
        if type(self.approvals) is not list or type(self.signers) is not list:
            raise ValueError("ERQL approvals and trust registry must be arrays")

    def authorize(self, *, allow_data_execution):
        from devbench.engine.execution_snapshot import current_binding
        from devharness_controlled.erql_contracts import read_erql_request
        from devharness_controlled.trust import verify_plan_approvals

        if allow_data_execution is not True:
            raise PermissionError("Synthetic ERQL requires explicit data execution permission")
        if current_binding() is None:
            raise PermissionError("Synthetic ERQL requires a retained execution snapshot")
        verify_plan_approvals(self.plan, self.approvals, self.signers)
        read_erql_request(self.directory / "request.json", self.plan)

    def run(self, output, *, allow_data_execution):
        from pathlib import Path

        from devbench.study.permissions import require_protected_storage
        from devharness_controlled.native_executor import execute_erql_plan

        output = Path(output)
        if (not output.is_absolute() or output.resolve() != output
                or output.is_relative_to(self.directory) or self.directory.is_relative_to(output)):
            raise ValueError("Use a canonical ERQL output path disjoint from the capsule")
        require_protected_storage(output)
        self.authorize(allow_data_execution=allow_data_execution)
        return execute_erql_plan(self.plan, self.approvals, self.signers,
                                 request_path=self.directory / "request.json", output_dir=output)

    def _retained_artifacts(self, output):
        from devharness_controlled.verification import _json, _read
        from devharness_core.provenance import _digest, verify_execution

        receipt = _json(_read(output / "receipt.json"))
        root_hash = receipt["root_hash"]
        path = output / "audit/manifest.json"
        integrity = verify_execution(path, root_hash)
        manifest = _json(_read(path, limit=16 * 1024**2))
        manifest_hash = _digest({
            key: value for key, value in manifest.items() if key != "root_hash"
        })
        if (not integrity["valid"] or manifest["root_hash"] != root_hash
                or manifest_hash != root_hash
                or manifest["analysis_fingerprint"] != self.plan.sha256()):
            raise ValueError("ERQL retained evidence failed integrity or plan binding")

        def retained(name):
            refs = [row for row in manifest["artifacts"] if row["name"] == name]
            if len(refs) != 1:
                raise ValueError("ERQL retained artifact is missing or ambiguous")
            return _json(_read(output / name, refs[0]["sha256"], limit=16 * 1024**2))

        outcome = retained("outcome.json")
        if outcome["plan_sha256"] != self.plan.sha256() or outcome["profile"] != self.plan.profile:
            raise ValueError("ERQL outcome differs from its configured plan")
        return root_hash, outcome, retained

    def status(self, output, *, guard=None, delivery=None):
        """Project retained synthetic evidence without starting another attempt."""
        from pathlib import Path

        from devbench.engine.provenance import canonical_json

        output = Path(output)
        if output.resolve() != output:
            raise ValueError("ERQL output path changed")
        base = {"scope": "synthetic_verification", "profile": self.plan.profile,
                "plan_sha256": self.plan.sha256(), "publication_eligible": False}
        if not output.exists():
            return {**base, "status": "ready"}
        if not (output / "receipt.json").is_file():
            return {**base, "status": "recovery_required"}
        root_hash, outcome, retained = self._retained_artifacts(output)
        from devbench.analysis.scientific_publication import (
            PublicationAccess,
            access_from_delivery,
            build_publication_descriptor,
            fixed_capsule,
            workspace_guard,
        )
        from devbench.core.procedure_profile import ProcedureProfile
        from devbench.core.publication_families import FamilyEvidence

        retained_families = retained("family-synthesis.json") if "family_synthesis" in outcome else None
        if retained_families is not None and (retained_families["publication_eligible"] is not False
                or retained_families["allocation_sha256"] != self.plan.allocation.sha256()):
            raise ValueError("ERQL family synthesis differs from its evaluation scope")
        source = outcome.get("family_evidence", {}).get("sha256", root_hash)
        guards = [workspace_guard(output), workspace_guard(self.directory)]
        if guard is not None:
            guards.append(guard)
        dependencies = (self.plan.source_sha256, self.plan.request_sha256, self.plan.design_sha256,
                        root_hash, source, outcome.get("raw_result", {}).get("sha256"))
        allowed = all(item.available and not set(dependencies) & item.blocked_hashes for item in guards)
        access = (access_from_delivery(delivery, source) if allowed and delivery is not None else
                  PublicationAccess(source=allowed, outcomes=allowed))
        allowed = allowed and access.source
        evidence = ()
        if allowed and "family_evidence" in outcome:
            evidence = (FamilyEvidence.model_validate_json(
                canonical_json(retained("family-evidence.json"))),)
        profile = ProcedureProfile(profile_id=self.plan.profile, version=1, operations=({
            "operation_id": "fixed_capsule", "output_kinds": ["table", "figure"],
            "max_memory_mb": self.plan.max_memory_mb, "timeout_seconds": self.plan.timeout_sec},
        ), input_representations=("COUNT-1",), biological_units=("declared_biological_unit",),
            permitted_claims=("exploratory_finding",))
        descriptor = build_publication_descriptor(profile, source_sha256=source, allocation=self.plan.allocation,
            access=access, dependency_sha256=tuple(
                identity for identity in dependencies if identity is not None), exploratory=True)
        publication = fixed_capsule(descriptor, self.plan.allocation, evidence)
        families = None
        if allowed and retained_families is not None:
            rows = {}
            for row in publication["table"]:
                rows.setdefault(row["family_id"], []).append({key: value for key, value in row.items()
                                                            if key != "family_id"})
            families = {"format": "devbench.erql-public-family-inventory/1",
                        "allocation_sha256": self.plan.allocation.sha256(), "publication_eligible": False,
                        "families": [{**family, "genes": rows.get(family["family_id"], [])}
                                     for family in publication["family_inventory"]]}
        diagnostic = outcome.get("family")
        diagnostics = ({key: diagnostic[key] for key in ("tested", "filtered")
                        if type(diagnostic.get(key)) is int and diagnostic[key] >= 0}
                       if allowed and type(diagnostic) is dict else None)
        return {**base, "status": outcome["status"], "audit_root": root_hash,
                "execution_code": guards[0].project(outcome["execution_code"]), "families": families,
                "diagnostics": diagnostics, "publication": publication,
                "failure": outcome.get("error_type") if outcome.get("error_type") in
                    {"ValueError", "PermissionError", "NativeRuntimeError", "TimeoutError"} else None}

    def publication_export(self, output, format, *, guard=None, delivery=None):
        """Read-only fixed presentation export with no source or replay attachments."""
        from devbench.analysis.scientific_publication import fixed_csv
        from devbench.engine.provenance import canonical_json

        result = self.status(output, guard=guard, delivery=delivery).get("publication")
        if result is None:
            raise KeyError("Fixed capsule publication unavailable")
        if format == "json":
            return canonical_json(result), "application/json", "fixed-publication.json"
        if format == "csv":
            return fixed_csv(result), "text/csv; charset=utf-8", "fixed-result.csv"
        if format == "svg" and result["figure"] is not None:
            return result["figure"]["svg"].encode(), "image/svg+xml", "fixed-effects.svg"
        if format == "svg":
            raise KeyError("Fixed capsule figure unavailable")
        raise ValueError("Unknown fixed publication format")

    def describe_replay(self, output, *, entitlement=None, snapshot_root=None):
        from devbench.analysis.bundles import describe_erql_replay
        return describe_erql_replay(self, output, entitlement=entitlement, snapshot_root=snapshot_root)

    def export_replay(self, output, destination, *, entitlement=None, capture_entitlement=None, snapshot_root=None, audience="restricted_audit"):
        from devbench.analysis.bundles import export_erql_replay
        return export_erql_replay(self, output, destination, entitlement=entitlement, capture_entitlement=capture_entitlement,
                                  snapshot_root=snapshot_root, audience=audience)
