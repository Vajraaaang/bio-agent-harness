"""Review admission and diagnostic routing under the scientific ledger."""
from copy import deepcopy
import math
import time

from .workflow_state import digest


class _DiagnosticActions:
    def _require_diagnostic_action_binding(self, connection, workflow, step, binding, *, actor):
        """Recheck the admitted local computation without calling the acceptance verifier."""
        from devbench.engine.exploratory_oci import ExploratoryOCI
        from devbench.engine.partition_authority import embargoed
        from devbench.engine.provenance import canonical_json
        from devbench.engine.workflow_operations import OperationBlocked

        executor = self.executor
        if (type(executor) is not ExploratoryOCI or executor.ledger.path != workflow.ledger.path
                or executor.backend_id != binding["backend_id"]
                or step.oci_method_version_id != binding["method_version_id"]
                or workflow.candidate_envelope(step, actor=actor, connection=connection) != binding["envelope"]
                or executor.parameters.get(step.parameters_sha256) != canonical_json(binding["parameters"])):
            raise OperationBlocked("diagnostic_action_computation_changed")
        method = executor.methods.method(step.oci_method_version_id)
        if method["contract"] != binding["contract"] or method["version_id"] != binding["method_version_id"]:
            raise OperationBlocked("diagnostic_action_computation_changed")
        for field in ("source_artifact", "environment_artifact"):
            identity = method[field]
            artifact = executor.methods.get("artifact", identity)
            if (any(artifact[key] != value for key, value in binding[field].items())
                    or embargoed(connection, artifact["sha256"])):
                raise OperationBlocked("diagnostic_action_source_or_binding_changed_during_verification")
            executor.methods.artifact_path(identity)

    def adaptive_diagnostic_admission(self, connection, envelope, record):
        """Prepare fresh exact-action qualification before dispatch or acceptance takes a write lock."""
        from devbench.analysis.rna_diagnostic_policy import (RnaDiagnosticPolicy, bind_rna_diagnostic_action,
            DiagnosticQualificationAuthority)
        from devbench.analysis.scientific_review_holds import ReviewBinding
        from devbench.engine.workflow_operations import OperationBlocked
        from .adaptive_workflow import AdaptiveStep, AdaptiveWorkflow, request_digest

        if (connection is not None or self.review_lifecycle is None
                or type(self.diagnostic_qualification) is not DiagnosticQualificationAuthority):
            raise OperationBlocked("current_diagnostic_action_authority_required")
        flow = AdaptiveWorkflow(self.ledger, self.review_lifecycle.lineage_id)
        step = AdaptiveStep.model_validate(envelope["step"])

        def require_use(db):
            if ReviewBinding.model_validate(self.review_binding(record["affected_use"])).sha256() != record["binding_sha256"]:
                raise OperationBlocked("diagnostic_action_review_binding_changed")
            current = self.review_lifecycle._state(db)
            if (record["affected_use"] in current["withdrawn"]
                    or current["holds"].get(record["hold_id"], {}).get("status") != "open"
                    or current["evidence"].get(record["input_evidence_id"], {}).get("sha256") != record["input_sha256"]):
                raise OperationBlocked("diagnostic_action_current_use_or_evidence_changed")

        with self.ledger._database() as db:
            db.execute("BEGIN")
            require_use(db)
        actual = bind_rna_diagnostic_action(flow, step, self.executor, actor=envelope["actor"])
        if actual is None or request_digest(actual) != record["action_acceptance"]["binding_sha256"]:
            raise OperationBlocked("diagnostic_action_computation_changed")
        policy = RnaDiagnosticPolicy.from_dict(record["policy"])
        accepted = self.diagnostic_qualification.verify_action(policy, record["context"],
            {"input_sha256": record["input_sha256"]}, record["route_state"], record["diagnostic_acceptance"], actual)
        if accepted is None or accepted != record["action_acceptance"]:
            raise OperationBlocked("diagnostic_action_acceptance_changed")

        def require_current(db):
            require_use(db)
            self._require_diagnostic_action_binding(db, flow, step, actual, actor=envelope["actor"])

        with self.ledger._database() as db:
            db.execute("BEGIN")
            require_current(db)
        return accepted, require_current

    def schedule_review_check(self, hold_id, workflow, step, *, connection=None):
        """Controller proposes a real resolving DAG branch; execution stays separate."""
        if self.review_lifecycle is None or workflow.ledger is not self.ledger:
            raise PermissionError("Resolving checks require the bound controller workflow")
        return self.review_lifecycle.schedule_check(hold_id, workflow, step, actor=self.actor,
            **({"connection": connection} if connection is not None else {}))

    def apply_rna_diagnostic_policy(self, policy, inputs, context, state, *, input_evidence_id,
                                    hold_id=None, workflow=None, step=None, controller_sequence=None,
                                    controller_expected_revision=None):
        """Bind a finite diagnostic route to real review state and optional DAG work.

        This is a trusted controller entrypoint. It never executes the check,
        excludes a donor, alters normalization or changes a scientific target.
        Current obligations, review admission and prior checks come from the
        scientific ledger rather than a model's proposed completion flags.
        """
        from devbench.analysis.rna_diagnostic_policy import (DiagnosticContext, DiagnosticRouteState, route_rna_diagnostic,
            bind_rna_diagnostic_action)
        from devbench.analysis.scientific_review_holds import ReviewBinding, CheckRequest
        from devbench.engine.workflow_operations import OperationBlocked, OperationConflict
        from .adaptive_workflow import AdaptiveStep, AdaptiveWorkflow, request_digest

        if self.review_lifecycle is None:
            raise PermissionError("Diagnostic routing requires the bound scientific review lifecycle")
        state = DiagnosticRouteState.model_validate(state).model_dump(mode="json")
        supplied_state = deepcopy(state) if controller_sequence is not None else None
        context = DiagnosticContext.model_validate(context).model_dump(mode="json")
        inputs = deepcopy(inputs)
        entity = state["affected_use"]
        binding = ReviewBinding.model_validate(self.review_binding(entity))
        binding_sha256 = binding.sha256()
        snapshot = self.review_lifecycle.state()
        authority = self.review_lifecycle.authority
        lineage = self.review_lifecycle.lineage_id

        def execution_fence(db):
            current = authority._load_state(db, lineage)
            if current is None:
                return None
            authority._active(db, lineage, current)
            return {key: current[key] for key in ("epoch", "binding_id", "grant_id", "authorization_sha256", "expires_at")}

        with self.ledger._database() as db:
            db.execute("BEGIN")
            original_fence = execution_fence(db)
            original_revision = authority._revision(db, lineage)
        if entity not in snapshot["entities"]:
            raise OperationConflict("Diagnostic affected use is absent from the review graph")
        evidence = snapshot["evidence"].get(input_evidence_id)
        if evidence is None:
            raise OperationBlocked("registered_diagnostic_input_evidence_required")
        obligations = snapshot["obligations"].get(binding_sha256, {})
        for item in state["obligations"]:
            registered = obligations.get(item["obligation_id"])
            if registered is None or registered["entity_id"] != entity or input_evidence_id not in registered["evidence_ids"]:
                raise OperationConflict("Diagnostic route must retain its exact registered scoped obligation and input")
        if not state["obligations"]:
            raise OperationBlocked("registered_scoped_diagnostic_obligation_required")
        # The budget follows exact policy/input/use evidence across hold IDs and
        # callers. Omitting the old hold cannot manufacture an unused check.
        prior_actions, reservations = set(), []
        charged_elapsed = state["elapsed_seconds"]
        for event in snapshot["history"]:
            prior = event["detail"].get("route", {}) if event["action"] == "diagnostic_route" else {}
            if (prior.get("policy_sha256") == policy.sha256 and prior.get("input_sha256") == evidence["sha256"]
                    and prior.get("affected_use") == entity):
                # Legacy routes with no observed checks have no elapsed time
                # derived from a pending check's deadline.
                recorded_charge = event["detail"].get("charged_elapsed_seconds")
                if recorded_charge is None and not prior.get("registered_check_states"):
                    recorded_charge = prior.get("budget_usage", {}).get("elapsed_seconds", 0)
                if recorded_charge is not None:
                    charged_elapsed = max(charged_elapsed, recorded_charge)
                if prior.get("scheduled_check"):
                    prior_actions.add(prior["scheduled_check"]["action_id"])
            if event["action"] == "diagnostic_check_start":
                start = event["detail"]
                if (start["policy_sha256"], start["input_sha256"], start["affected_use"]) == (policy.sha256, evidence["sha256"], entity):
                    prior_actions.add(start["action_id"])
                    reservations.append(start)
        state["elapsed_seconds"] = charged_elapsed
        reserved_keys = {(row["action_id"], row.get("generation", 1)) for row in reservations}
        legacy_actions = prior_actions - {action for action, _ in reserved_keys}
        state["checks_used"] = max(state["checks_used"], len(reserved_keys) + len(legacy_actions))
        prior_checks = [check for recorded in snapshot["holds"].values() for check in recorded["checks"]
            if check["action_id"] in prior_actions]
        hold = None if hold_id is None else snapshot["holds"].get(hold_id)
        if hold_id is not None and (hold is None or entity not in hold["scope"]):
            raise OperationConflict("Diagnostic hold does not affect this use")
        if hold is not None:
            checks = hold["checks"]
            state["checks_used"] = max(state["checks_used"], len(checks))
            prior_checks.extend(checks)
            state["dependent_use_disproved"] = bool(hold["objection"]["target_invalidity_evidence_ids"])
        elif state["dependent_use_disproved"]:
            raise OperationConflict("Disproved use requires its retained scoped invalidity evidence")

        budget = policy.to_dict()["budget"]

        def active_deadlines(adaptive, review, records):
            deadlines = [item.get("deadline_epoch", item["started_at_epoch"] + budget["max_seconds"])
                for item in records if AdaptiveWorkflow._diagnostic_reservation_in_scope(
                    adaptive, review, item, hold_id=hold_id, binding_sha256=binding_sha256)
                and AdaptiveWorkflow._diagnostic_check_pending(adaptive, item["action_id"])]
            if any(type(value) not in (int, float) or not math.isfinite(value) for value in deadlines):
                raise OperationBlocked("diagnostic_check_deadline_unavailable")
            return deadlines

        check_states = []
        generations = {}
        check_ids = sorted(prior_actions | {check["action_id"] for check in prior_checks})
        if check_ids:
            # The lifecycle's scheduled/completed marker is not a provider
            # outcome. Reconcile the authoritative current DAG and retained
            # attempt receipts, including failed, omitted and audit-only work.
            with self.ledger._database() as db:
                db.execute("BEGIN")
                adaptive = AdaptiveWorkflow(self.ledger, self.review_lifecycle.lineage_id)._state(db)
                generations = adaptive.generations
                scoped = {row["action_id"] for row in reservations if AdaptiveWorkflow._diagnostic_reservation_in_scope(
                    adaptive, snapshot, row, hold_id=hold_id, binding_sha256=binding_sha256)}
                reserved_ids = {row["action_id"] for row in reservations}
                if hold is not None:
                    scoped.update(check["action_id"] for check in hold["checks"] if check["action_id"] not in reserved_ids)
                wall_millis = 0
                for identity in check_ids:
                    node = adaptive.nodes.get(identity)
                    outcome = adaptive.outcome(identity) if node is not None else None
                    omission = next((members[identity] for members in adaptive.sweeps.values()
                        if members.get(identity) is not None), None)
                    status, reason = "pending", None
                    if outcome is not None:
                        status, reason = outcome["status"], outcome["reason"]
                    elif node is None and omission is None and identity in reserved_ids:
                        status, reason = "reserved", "registered_check_reserved_not_inserted"
                    elif node is None or node.branch_id in adaptive.retired or omission is not None:
                        status = "omitted"
                        reason = omission or (adaptive.retired[node.branch_id]["reason"] if node is not None
                            and node.branch_id in adaptive.retired else "registered_check_step_unavailable")
                    attempts = []
                    for attempt_id, dispatch in adaptive.dispatches.items():
                        if dispatch["envelope"]["step"]["step_id"] != identity:
                            continue
                        attempt = self.review_lifecycle.authority._load_state(db, self.review_lifecycle.lineage_id, attempt_id)
                        if attempt is None:
                            raise OperationConflict("Registered diagnostic dispatch has no retained scientific attempt")
                        wall_millis += attempt["usage"].get("wall_millis", 0)
                        attempts.append({"attempt_id": attempt_id, "execution_state": attempt["execution_state"],
                            "generation": dispatch["envelope"]["generation"]})
                        if outcome is None and node is not None and dispatch["envelope"]["generation"] == adaptive.generations[identity]:
                            if attempt["execution_state"] in {"failed", "cancelled", "noncompletion_confirmed"}:
                                status = "omitted" if attempt["execution_state"] == "noncompletion_confirmed" else "failed"
                                reason = attempt["execution_state"]
                    check_states.append({"action_id": identity, "status": status, "reason": reason,
                        "outcome": deepcopy(outcome), "attempts": attempts})
                charged_elapsed = max(charged_elapsed, wall_millis / 1000)
                state["elapsed_seconds"] = charged_elapsed
                deadlines = active_deadlines(adaptive, snapshot, reservations)
                if deadlines:
                    state["elapsed_seconds"] = max(state["elapsed_seconds"], budget["max_seconds"] - min(deadlines) + time.time())
            scoped_statuses = {item["status"] for item in check_states if item["action_id"] in scoped}
            if scoped_statuses & {"failed", "audit_only"}:
                state["check_status"] = "failed"
            elif "omitted" in scoped_statuses:
                state["check_status"] = "infeasible"
            elif scoped_statuses & {"pending", "reserved"}:
                state["check_status"] = "pending"
            elif "succeeded" in scoped_statuses:
                state["check_status"] = "completed"
        if entity in snapshot["withdrawn"]:
            state["check_status"] = "infeasible"
        state["review_binding_sha256"] = binding_sha256
        state["current_design_review"] = False
        if state["intent"] == "prefit":
            try:
                self.require_primary_review(entity)
            except OperationBlocked:
                pass
            else:
                state["current_design_review"] = True
        route = route_rna_diagnostic(policy, inputs, context, state, authority=self.diagnostic_qualification)
        route["registered_check_states"] = check_states
        if evidence["sha256"] != route["input_sha256"]:
            raise OperationConflict("Diagnostic calculation differs from its registered immutable input evidence")
        if ReviewBinding.model_validate(self.review_binding(entity)) != binding:
            raise OperationConflict("Current review binding changed during diagnostic routing")

        def require_current(current, db):
            if ReviewBinding.model_validate(self.review_binding(entity)) != binding:
                raise OperationConflict("Current review binding changed before diagnostic action")
            if current["evidence"].get(input_evidence_id) != evidence:
                raise OperationConflict("Diagnostic source identity changed before diagnostic action")
            live = current["obligations"].get(binding_sha256, {})
            if any(live.get(item["obligation_id"]) != obligations[item["obligation_id"]] for item in state["obligations"]):
                raise OperationConflict("Diagnostic scoped obligation changed before diagnostic action")
            if execution_fence(db) != original_fence:
                raise OperationConflict("Diagnostic execution fence changed before diagnostic action")
            if hold is not None:
                actual = current["holds"].get(hold_id)
                if (actual is None or actual["status"] != hold["status"] or actual["objection"] != hold["objection"]
                        or entity not in actual["scope"]):
                    raise OperationConflict("Diagnostic hold changed before diagnostic action")

        if step is not None:
            step = AdaptiveStep.model_validate(step)
            step_sha256 = request_digest(step.model_dump(mode="json"))
            reserved = next((row for row in reservations if row.get("hold_id") == hold_id
                and row["action_id"] == step.step_id and row.get("step_sha256") == step_sha256
                and row.get("generation", 1) == generations.get(step.step_id, 1)
                and row.get("binding_sha256") == binding_sha256 and row.get("context_sha256") == route["context_sha256"]), None)
            if (route["route"] == "check_pending" and reserved is not None
                    and any(row["action_id"] == step.step_id and row["status"] == "reserved" for row in check_states)):
                if reserved["inferential_status"] == "policy_conditional" and route["qualification_status"] != "qualified":
                    raise PermissionError("Resuming a qualified diagnostic action requires current qualification")
                route.update(route="registered_check", reason="resume_reserved_diagnostic_check",
                    action=deepcopy(reserved["action"]), inferential_status=reserved["inferential_status"])

        route["action_qualification"] = {"status": "unavailable", "reason": "action_specific_qualification_not_requested"}
        if route["route"] == "registered_check" and route["inferential_status"] == "policy_conditional":
            action_binding = (bind_rna_diagnostic_action(workflow, step, self.executor, actor=self.actor)
                if workflow is not None and step is not None else None)
            if action_binding is None:
                route.update(inferential_status="exploratory", action_qualification={"status": "unavailable",
                    "reason": "registered_implementation_binding_unavailable"})
            else:
                accepted = self.diagnostic_qualification.verify_action(policy, context, route["execution"], state,
                    route["qualification_evidence"], action_binding)
                if accepted is None:
                    route.update(inferential_status="exploratory", action_qualification={"status": "unavailable",
                        "reason": "independent_action_acceptance_unavailable"})
                else:
                    route["action_qualification"] = {"status": "qualified", "reason": None,
                        "binding": action_binding, "acceptance": accepted}

        satisfy = None
        if route["route"] == "matched_quantitative_alternative":
            satisfy = self.review_lifecycle._prepare_diagnostic_satisfactions(route["satisfied_obligation_ids"], binding,
                policy=policy, inputs=inputs, context=context, route_state=state,
                qualification_authority=self.diagnostic_qualification, evidence_ids=(input_evidence_id,),
                current_binding=lambda: self.review_binding(entity), expected_acceptance=route["qualification_evidence"])

        def register(current):
            value = policy.to_dict()
            for event in current["history"]:
                if event["action"] == "register_diagnostic_policy":
                    old = event["detail"]
                    if (old["policy"]["policy_id"], old["policy"]["version"]) == (value["policy_id"], value["version"]) \
                            and old["policy_sha256"] != policy.sha256:
                        raise OperationConflict("Registered diagnostic policy identity/version is immutable")
            registration = {"policy": value, "policy_sha256": policy.sha256}
            return {}, registration, registration

        def retain(current, db):
            # Withdrawal and its currentness-checked route share one commit;
            # a rejected stale route cannot leave a damaged current use behind.
            require_current(current, db)
            updates = {}
            if route["terminal"] or route["invalidate_dependent_use"]:
                scope = self.review_lifecycle._scope(current, (entity,))
                updates["withdrawn"] = {key: {"reason": route["reason"] + ": " + route["resumption_condition"],
                    "requested_entity_id": entity} for key in scope}
            return updates, {"binding_sha256": binding.sha256(), "input_evidence_id": input_evidence_id,
                "hold_id": hold_id, "charged_elapsed_seconds": charged_elapsed, "route": route}, route

        should_schedule = route["route"] == "registered_check" and hold is not None and workflow is not None and step is not None
        if route["route"] == "registered_check" and not should_schedule:
            route.update(route="unavailable", reason="registered_check_branch_resources_unavailable",
                resumption_condition="Provide the bound open hold, exact registered DAG check and its resources.")
        action_proof = route["action_qualification"]
        conditional = route["inferential_status"] == "policy_conditional"
        if should_schedule:
            capability = next(item for item in step.profile.operations if item.operation_id == step.operation_id)
            if (step.operation_id != route["action"]["operation_id"] or capability.timeout_seconds > budget["max_seconds"]
                    or step.primary or not conditional and step.purpose != "exploratory"):
                raise PermissionError("Diagnostic check exceeds its registered operation, resources or inferential scope")
            self.review_lifecycle._check_workflow(workflow)
            step_sha256 = request_digest(step.model_dump(mode="json"))
        reservation_key = {"policy_sha256": policy.sha256, "input_sha256": evidence["sha256"], "affected_use": entity}

        def current_reservations(current):
            return {(event["detail"]["action_id"], event["detail"].get("generation", 1)): event["detail"]
                for event in current["history"]
                if event["action"] == "diagnostic_check_start" and all(event["detail"].get(key) == value
                    for key, value in reservation_key.items())}

        def check_authority(current, db, reservation=None):
            require_current(current, db)
            if entity in current["withdrawn"]:
                raise OperationBlocked("diagnostic_affected_use_withdrawn")
            live = current["holds"].get(hold_id)
            if live is None or live["status"] != "open" or live["objection"]["check_request"] is None:
                raise OperationConflict("Diagnostic scheduling requires the current open check hold")
            if any(row["action_id"] == step.step_id and row["status"] == "completed" for row in live["checks"]):
                raise OperationBlocked("diagnostic_check_result_already_recorded")
            request = CheckRequest.model_validate(live["objection"]["check_request"])
            if (request.registered_check_id != step.operation_id
                    or self.review_lifecycle.registered_checks.get(step.operation_id) != request.action_kind):
                raise PermissionError("Diagnostic policy action differs from the registered check authority")
            effects = set(capability.effects)
            if route["action"]["kind"] == "read_only":
                if (request.action_kind != "read_only" or effects - {"read_only", "read_frozen_artifacts"}
                        or any(link.source_port is not None for link in step.inputs)):
                    raise PermissionError("Read-only diagnostic policy cannot borrow fit, preprocessing or mutable-input authority")
            elif (request.action_kind not in {"new_fit", "preprocessing"}
                    or request.action_kind not in effects or effects - {request.action_kind, "read_only", "read_frozen_artifacts"}):
                raise PermissionError("Diagnostic branch policy and actual declared effects must match the CheckRequest")
            if conditional:
                self._require_diagnostic_action_binding(db, workflow, step, action_proof["binding"], actor=self.actor)
            if reservation is not None:
                if current_reservations(current).get((step.step_id, reservation.get("generation", 1))) != reservation:
                    raise OperationConflict("Diagnostic check reservation changed before scheduling")
                if time.time() >= reservation["deadline_epoch"]:
                    raise OperationBlocked("diagnostic_budget_exhausted")
                if reservation["execution_fence"] != execution_fence(db):
                    raise OperationConflict("Diagnostic reserved execution fence changed before scheduling")

        def reserve(current, db):
            check_authority(current, db)
            prior = current_reservations(current)
            adaptive = workflow._state(db)
            generation = adaptive.generations.get(step.step_id, 1)
            now = time.time()
            deadline = min([now + budget["max_seconds"] - state["elapsed_seconds"],
                *active_deadlines(adaptive, current, prior.values())])
            if deadline <= now:
                route.update(route="terminate_unresolved", reason="diagnostic_budget_exhausted", terminal=True,
                    action=None, inferential_status="unresolved",
                    resumption_condition="A new authorized evidence/resource basis and bound use are required; the existing budget does not reset.")
                return None
            identity = {"step_sha256": step_sha256, "hold_id": hold_id, "binding_sha256": binding_sha256,
                "context_sha256": route["context_sha256"], "action": deepcopy(route["action"]),
                "inferential_status": route["inferential_status"],
                "action_binding_sha256": action_proof.get("acceptance", {}).get("binding_sha256")}
            if (step.step_id, generation) in prior:
                reservation = prior[step.step_id, generation]
                if any(reservation.get(key) != value for key, value in identity.items()):
                    raise OperationConflict("Diagnostic reserved action identity is immutable")
                return reservation
            checks_used = max(len(prior), state["checks_used"], len(current["holds"][hold_id]["checks"]))
            if checks_used >= min(budget["max_checks"], budget["max_branch_steps"]):
                route.update(route="check_pending", reason="diagnostic_check_capacity_reserved", action=None,
                    inferential_status="unresolved", resumption_condition="Wait for or resume the exact reserved check; a different hold or step cannot reset its capacity.",
                    budget_usage={"checks_used": checks_used,
                        "elapsed_seconds": max(state["elapsed_seconds"], budget["max_seconds"] - (deadline - now))})
                return None
            reservation = {**reservation_key, **identity, "action_id": step.step_id, "generation": generation,
                "status": "reserved", "started_at_epoch": now, "deadline_epoch": deadline, "execution_fence": original_fence}
            return self.review_lifecycle._write("diagnostic_check_start", lambda _: ({}, reservation, reservation), connection=db)

        def schedule(db, reservation):
            check_authority(self.review_lifecycle._state(db), db, reservation)
            scheduled = self.schedule_review_check(hold_id, workflow, step, connection=db)
            check_authority(self.review_lifecycle._state(db), db, reservation)
            if conditional:
                from devbench.analysis.rna_diagnostic_policy import verify_retained_diagnostic_action

                admitted = {**reservation_key, "policy": policy.to_dict(), "action_id": step.step_id,
                    "hold_id": hold_id, "binding_sha256": binding_sha256, "generation": reservation.get("generation", 1),
                    "input_evidence_id": input_evidence_id, "context": deepcopy(context), "route_state": deepcopy(state),
                    "diagnostic_acceptance": deepcopy(route["qualification_evidence"]),
                    "action_binding": deepcopy(action_proof["binding"]), "action_acceptance": deepcopy(action_proof["acceptance"])}
                verify_retained_diagnostic_action(admitted, action_proof["binding"]["envelope"])
                self.review_lifecycle._write("diagnostic_action_accepted", lambda _: ({}, admitted, admitted), connection=db)
            route["scheduled_check"] = scheduled

        failure = None
        with authority._execution_tx(lineage) as db:
            if controller_sequence is not None:
                if type(workflow) is not AdaptiveWorkflow or workflow.ledger is not self.ledger or workflow.lineage_id != lineage:
                    raise PermissionError("Diagnostic journaling requires its exact adaptive workflow")
                if authority._revision(db, lineage) != controller_expected_revision:
                    raise OperationConflict("Controller diagnostic evidence changed before routing")
                journal = workflow._state(db).controller
                pending = journal["pending"]
                decision = (pending or {}).get("decision") or {}
                selected = self._diagnostic_actions.get(decision.get("action_id"), {})
                if (pending is None or pending["sequence"] != controller_sequence or decision.get("kind") != "route_diagnostic"
                        or selected.get("policy") != policy or selected.get("inputs") != inputs
                        or selected.get("context") != context or selected.get("state") != supplied_state
                        or selected["descriptor"] not in pending["context"].get("diagnostic_actions", ())
                        or input_evidence_id not in decision["evidence_ids"]
                        or selected["descriptor"]["input_evidence_id"] != input_evidence_id
                        or selected["descriptor"]["hold_id"] != hold_id or selected.get("step") != step):
                    raise OperationConflict("Diagnostic routing differs from the selected controller action")
                workflow._controller_admission(db, journal)
            if satisfy is not None:
                if authority._revision(db, lineage) != original_revision:
                    raise OperationConflict("Diagnostic scientific revision changed during routing")
                obligations.update(satisfy(db))
            self.review_lifecycle._write("register_diagnostic_policy", register, connection=db)
            current = self.review_lifecycle._state(db)
            require_current(current, db)
            reservation = reserve(current, db) if should_schedule else None
            if reservation is None:
                result = self.review_lifecycle._write("diagnostic_route", retain, with_database=True, connection=db)
            else:
                # Keep the exact consumed reservation on failure. Scheduling,
                # admission and the successful route share the same commit.
                db.execute("SAVEPOINT diagnostic_check_schedule")
                try:
                    schedule(db, reservation)
                    result = self.review_lifecycle._write("diagnostic_route", retain, with_database=True, connection=db)
                except Exception as error:
                    db.execute("ROLLBACK TO diagnostic_check_schedule")
                    failure = error
                finally:
                    db.execute("RELEASE diagnostic_check_schedule")
            if controller_sequence is not None and failure is None:
                applied = {"status": "applied", "kind": "route_diagnostic", "route": result["route"],
                           "reason": result["reason"], "diagnostic_route_sha256": digest(result)}
                workflow.complete_controller_decision(controller_sequence, applied, connection=db)
        if failure is not None:
            raise failure
        if conditional and "scheduled_check" in result:
            workflow.diagnostic_admission = self.adaptive_diagnostic_admission
        return result if controller_sequence is None else applied
