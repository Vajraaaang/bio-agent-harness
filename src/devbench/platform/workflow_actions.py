"""Runtime adapters to existing planner APIs. Saved state never grants authority."""

import sqlite3
from collections.abc import Callable
from copy import deepcopy
from typing import TYPE_CHECKING

from devbench.engine.workflow_operations import OperationBlocked, OperationConflict

from .workflow_adaptive_actions import _AdaptiveControllerActions
from .workflow_diagnostic_actions import _DiagnosticActions
from .workflow_serial_evidence import _LedgerCancellationEvent, _SerialWorkflowEvidence
from .workflow_state import (
    _PLANNER_EFFECTS,
    _SCIENCE_NODES,
    _SerialWorkflowCursorActions,
    digest,
    validate_workflow_state,
)
from .workflow_state import (
    require_fixed_lineage as require_fixed_lineage,
)

if TYPE_CHECKING:
    from .adaptive_workflow import AdaptiveWorkflow
    from .thesis_joint_bh_release import JointBHRelease

def create_rna_workflow(
    *,
    mode="single_agent",
    actions,
    checkpoint_path=None,
    investigation_backend=None,
    trace_observer=None,
    adaptive=None,
):
    """Construct the explicitly selected controller; construction never dispatches."""
    if mode == "adaptive/1":
        from .langgraph_workflow import AdaptiveRNAWorkflow

        if adaptive is None or any(
            value is not None
            for value in (checkpoint_path, investigation_backend, trace_observer)
        ):
            raise ValueError("Adaptive routing requires its scientific ledger binding")
        return AdaptiveRNAWorkflow(actions, adaptive)
    if adaptive is not None:
        raise ValueError("An adaptive binding requires the versioned adaptive route")
    workflow = _create_fixed_workflow(
        mode, actions, checkpoint_path, investigation_backend
    )
    if trace_observer is not None:
        from devbench.analysis.rna_trace_receipts import ObservedWorkflow

        return ObservedWorkflow(
            workflow, trace_observer, actions, investigation_backend
        )
    return workflow


def _create_fixed_workflow(mode, actions, checkpoint_path, investigation_backend):
    if mode == "single_agent":
        from .langgraph_workflow import SerialRNAWorkflow

        return SerialRNAWorkflow(actions, checkpoint_path, enabled=True)
    if mode == "independent_investigations":
        if investigation_backend is None:
            raise ValueError("Independent investigations require an audited backend")
        from devbench.analysis.langgraph_investigations import IndependentRNAWorkflow

        return IndependentRNAWorkflow(
            actions,
            checkpoint_path,
            investigation_backend=investigation_backend,
        )
    raise ValueError("Unknown RNA workflow mode")


class WorkflowActions(
    _AdaptiveControllerActions,
    _DiagnosticActions,
    _SerialWorkflowEvidence,
    _SerialWorkflowCursorActions,
):
    def __init__(
        self,
        planner,
        ledger,
        *,
        actor,
        selector,
        version_ids=(),
        declarations=(),
        allow_model_execution=False,
        allow_data_execution=False,
        executor=None,
        execution_bindings=None,
        investigation_policy=None,
        investigation_evidence=None,
        investigation_authority=None,
        before_dispatch=None,
        requested_experiment_id=None,
        scientific_workflow=None,
        execution_input_request=None,
        retained_synthesis=False,
        review_lifecycle=None,
        review_binding=None,
        diagnostic_qualification=None,
        controller=None,
        diagnostic_actions=None,
        tier_a_executor=None,
        research_delivery=None,
    ):
        self.planner, self.ledger = planner, ledger
        self.actor, self.selector = actor, selector
        self.version_ids, self.declarations = (
            list(version_ids),
            deepcopy(list(declarations)),
        )
        if len(self.version_ids) > 64 or len(set(self.version_ids)) != len(
            self.version_ids
        ):
            raise ValueError("Use at most 64 distinct method versions")
        if len(self.declarations) > 64 or any(
            type(item) is not dict
            or set(item) != {"draft_id", "branch_id"}
            or any(
                type(value) is not str or not value or len(value) > 300
                for value in item.values()
            )
            for item in self.declarations
        ):
            raise ValueError("Declare bounded exact draft and exploratory branch IDs")
        self.allow_model_execution = allow_model_execution is True
        self.allow_data_execution = allow_data_execution is True
        self.executor, self.execution_bindings = executor, execution_bindings
        if tier_a_executor is not None:
            from devbench.engine.tier_a_executor import TierAExecutor

            if (
                type(tier_a_executor) is not TierAExecutor
                or tier_a_executor.ledger.path != ledger.path
            ):
                raise PermissionError("Tier A must use the same workflow ledger")
        self.tier_a_executor = tier_a_executor
        self.before_dispatch = before_dispatch
        self.requested_experiment_id = requested_experiment_id
        self.scientific_workflow = scientific_workflow
        self.retained_synthesis = retained_synthesis
        self._validate_review_binding(ledger, review_lifecycle, review_binding)
        self.review_lifecycle, self.review_binding = review_lifecycle, review_binding
        from devbench.analysis.rna_diagnostic_policy import (
            DiagnosticQualificationAuthority,
        )

        if diagnostic_qualification is not None and (
            type(diagnostic_qualification) is not DiagnosticQualificationAuthority
            or review_lifecycle is None
        ):
            raise TypeError(
                "Diagnostic qualification requires a trusted authority "
                "and the bound review lifecycle"
            )
        self.diagnostic_qualification = (
            diagnostic_qualification or DiagnosticQualificationAuthority(lambda _: None)
        )
        if execution_input_request is not None and (
            type(execution_input_request) is not dict
            or set(execution_input_request) != {"record_ref", "workspace"}
            or execution_input_request["record_ref"] is None
            or execution_input_request["workspace"] is None
        ):
            raise ValueError(
                "Use an exact late execution-input reference and workspace request"
            )
        self.execution_input_request = deepcopy(execution_input_request)
        self.investigation_policy = deepcopy(investigation_policy)
        self.investigation_evidence = deepcopy(investigation_evidence)
        self.investigation_authority = investigation_authority
        if controller is not None:
            from devbench.analysis.episode_agent import AdaptiveController

            if type(controller) is not AdaptiveController:
                raise TypeError("Use the trusted adaptive controller runtime")
        self.controller = controller
        self._validate_research_delivery(
            research_delivery, ledger, review_lifecycle, controller,
        )
        self.research_delivery = research_delivery
        self._diagnostic_actions = self._bind_controller_diagnostics(diagnostic_actions)

    @staticmethod
    def _validate_research_delivery(delivery, ledger, lifecycle, controller):
        from devbench.analysis.evidence_access import EvidenceDelivery

        if delivery is not None and (
            type(delivery) is not EvidenceDelivery
            or delivery.broker.authority.ledger is not ledger
            or lifecycle is None
            or controller is not None
        ):
            raise PermissionError("Use the exact local research disclosure capability")

    @staticmethod
    def _validate_review_binding(ledger, lifecycle, binding):
        if lifecycle is None:
            if binding is not None:
                raise ValueError("Review binding requires its lifecycle authority")
            return

        from devbench.analysis.scientific_review_holds import ReviewHoldLifecycle

        if type(lifecycle) is not ReviewHoldLifecycle or lifecycle.ledger is not ledger:
            raise ValueError(
                "Review admission requires this workflow's scientific ledger"
            )
        if not callable(binding):
            raise TypeError(
                "Current review binding must be supplied by the coordinator"
            )

    def _bind_controller_diagnostics(self, actions):
        if actions is None or type(actions) is dict and not actions:
            return {}
        from pydantic import TypeAdapter

        from devbench.analysis.rna_diagnostic_policy import (
            DiagnosticContext,
            DiagnosticRouteState,
            RnaDiagnosticPolicy,
        )

        from .adaptive_workflow import Identity

        if (
            self.controller is None
            or self.review_lifecycle is None
            or type(actions) is not dict
            or len(actions) > 64
        ):
            raise ValueError(
                "Diagnostic action catalog requires the bound adaptive controller "
                "and review lifecycle"
            )
        identity = TypeAdapter(Identity)
        catalog = {}
        required = {"policy", "inputs", "context", "state", "input_evidence_id"}
        for action_id, entry in actions.items():
            identity.validate_python(action_id)
            if (
                type(entry) is not dict
                or not required <= entry.keys()
                or entry.keys() - required - {"hold_id", "step_action_id"}
                or type(entry["policy"]) is not RnaDiagnosticPolicy
            ):
                raise ValueError(
                    "Diagnostic action requires exact coordinator-owned policy "
                    "and evidence inputs"
                )
            selected = entry.get("step_action_id")
            step = self.controller.action_catalog.get(selected)
            if selected is not None and step is None:
                raise ValueError("Diagnostic action refers to an unregistered step")
            hold_id = entry.get("hold_id")
            if hold_id is not None:
                identity.validate_python(hold_id)
            context = DiagnosticContext.model_validate(entry["context"]).model_dump(
                mode="json"
            )
            route_state = DiagnosticRouteState.model_validate(
                entry["state"]
            ).model_dump(mode="json")
            evidence_id = identity.validate_python(entry["input_evidence_id"])
            inputs = deepcopy(entry["inputs"])
            descriptor = {
                "action_id": action_id,
                "policy_id": entry["policy"].to_dict()["policy_id"],
                "policy_sha256": entry["policy"].sha256,
                "input_evidence_id": evidence_id,
                "affected_use": route_state["affected_use"],
                "hold_id": hold_id,
                "action_sha256": digest(
                    [
                        context,
                        route_state,
                        inputs,
                        evidence_id,
                        hold_id,
                        step.model_dump(mode="json") if step is not None else None,
                        entry["policy"].sha256,
                    ]
                ),
            }
            catalog[action_id] = {
                "descriptor": descriptor,
                "policy": entry["policy"],
                "inputs": inputs,
                "context": context,
                "state": route_state,
                "step": step,
            }
        return catalog

    def apply_adaptive_decision(self, workflow, pending):
        if pending is None:
            return None
        sequence = pending["sequence"]
        with workflow.ledger._operation_lock(
            (workflow.lineage_id, "adaptive-decision", sequence)
        ):
            return self._apply_adaptive_decision(workflow, pending)

    def _apply_adaptive_decision(self, workflow, pending):
        from .adaptive_workflow import request_digest

        decision, sequence = pending["decision"], pending["sequence"]
        state = workflow.controller_state()
        if 0 <= sequence < len(state["decisions"]):
            accepted = state["decisions"][sequence]
            if all(
                accepted[key] == pending[key]
                for key in ("context_sha256", "decision", "evidence")
            ):
                return {**accepted["result"], "reconciled": True}
        if state["pending"] != pending:
            raise OperationConflict(
                "Adaptive decision differs from its retained selection"
            )
        kind = decision["kind"]
        evidence_hash = request_digest(
            {
                key: pending["context"]["evidence"][key]
                for key in decision["evidence_ids"]
            }
        )
        try:
            self.controller.invoker._runtime()
            self._refresh_after_research_brief(workflow, pending)
            current = self.adaptive_controller_context(workflow)
            self._require_current_adaptive_selection(workflow, pending, current)
            if kind in {"dispatch", "review", "query_literature", "query_evidence"}:
                result = self._observe_adaptive_selection(workflow, pending, current)
            else:
                return self._complete_adaptive_selection(
                    workflow, pending, current, evidence_hash
                )
        except (
            ValueError,
            PermissionError,
            OperationBlocked,
            OperationConflict,
        ) as error:
            if isinstance(error, OperationBlocked) and error.state in {
                "active",
                "ledger_busy",
                "active_review_requires_reconciliation",
            }:
                raise
            if isinstance(error, OperationConflict) and isinstance(
                error.__cause__, sqlite3.DatabaseError
            ):
                raise
            return self._reject_adaptive_selection(workflow, sequence, kind, error)
        return workflow.complete_controller_decision(sequence, result)

    def _require_current_adaptive_selection(self, workflow, pending, current):
        # Selection's own disclosure changes the audit summary. Scope, graph,
        # holds and the selected evidence remain compared exactly.
        def comparable(context):
            return {
                key: value
                for key, value in context.items()
                if key not in {"revision", "scientific_state"}
            }

        decision, selected = pending["decision"], pending["context"]
        kind = decision["kind"]
        if comparable(current) != comparable(selected):
            # A dispatch for this generation or an authenticated review can
            # have already changed the graph or holds before this retry.
            before = {
                row["step_id"]: row["generation"] for row in selected["graph"]["steps"]
            }
            after = {
                row["step_id"]: row["generation"] for row in current["graph"]["steps"]
            }
            dispatched = (
                kind == "dispatch"
                and decision["step_id"] in selected["ready"]
                and before.get(decision["step_id"]) == after.get(decision["step_id"])
                and decision["step_id"] in workflow.status()["outcomes"]
            )
            reviewed = (
                kind == "review"
                and (
                    self.controller._review_root(workflow, pending["sequence"])
                    / "result.json"
                ).is_file()
            )
            if not dispatched and not reviewed:
                raise OperationBlocked("controller_evidence_changed")
        if selected["termination_required"] and kind != "stop":
            raise OperationBlocked("reserved_termination_requires_stop")

    def _reject_adaptive_selection(self, workflow, sequence, kind, error):
        reason = getattr(error, "state", str(error))[:1000]
        result = {"status": "rejected", "kind": kind, "reason": reason}
        if reason == "current_active_review_capability_required":
            result["resumption_condition"] = (
                "Supply the current coordinator review capability or perform a fresh "
                "current review under the same cumulative campaign."
            )
        elif reason == "current_adaptive_review_material_required":
            result["resumption_condition"] = (
                "Review the changed scientific material at both required touchpoints "
                "under the same cumulative campaign."
            )
        workflow.complete_controller_decision(sequence, result)
        if reason == "cancelled" and kind == "stop":
            return self.stop_adaptive(workflow, "cancelled")
        return result

    def _complete_adaptive_selection(self, workflow, pending, current, evidence_hash):
        decision, sequence = pending["decision"], pending["sequence"]
        kind, revision = decision["kind"], current["revision"]
        if kind == "route_diagnostic":
            return self._route_adaptive_diagnostic(workflow, pending, revision)
        if kind == "page_context":
            return self._page_adaptive_context(workflow, pending, revision)
        if kind == "stop":
            return self._finish_adaptive_stop(workflow, pending)
        if kind == "propose_method_benchmark":
            return self._retain_benchmark_proposal(workflow, pending, current)
        apply = self._prepare_adaptive_mutation(
            workflow, pending, current, evidence_hash
        )
        return workflow.complete_controller_decision(
            sequence,
            None,
            apply=apply,
            expected_revision=revision,
        )

    def _prepare_adaptive_mutation(self, workflow, pending, current, evidence_hash):
        decision = pending["decision"]
        kind = decision["kind"]
        if kind == "publish_joint_bh":
            return self._prepare_controller_joint_publication(
                workflow, pending, current
            )
        if kind == "propose_pipeline_step":
            return self._prepare_pipeline_change(
                workflow, pending, current, evidence_hash
            )
        if kind in {"insert", "revise", "schedule_check"}:
            return self._prepare_registered_step_change(
                workflow, decision, evidence_hash
            )
        if kind == "declare_sweep":
            return self._prepare_sweep(workflow, decision)
        if kind == "retire_branch":
            return self._prepare_branch_retirement(workflow, decision, evidence_hash)
        if kind == "respond":
            return self._prepare_review_response(decision)
        raise ValueError("Unsupported adaptive controller action")

    def _prepare_controller_joint_publication(self, workflow, pending, current):
        decision = pending["decision"]
        target = {
            "family_id": decision["family_id"],
            "publication_sha256": decision["publication_sha256"],
        }
        if any(
            target not in context.get("joint_publication_choices", ())
            for context in (pending["context"], current)
        ):
            raise OperationBlocked("joint_publication_choice_not_currently_offered")
        publish = self._prepare_joint_bh(workflow, **target)

        def apply(db):
            release = publish(db)
            return {
                "status": "applied",
                "kind": "publish_joint_bh",
                "family_id": release.family_id,
                "publication_sha256": release.publication_sha256,
                "receipt_sha256": release.receipt_sha256(),
            }

        return apply

    def _retain_benchmark_proposal(self, workflow, pending, current):
        from .thesis_benchmark_actions import propose_benchmark

        result = propose_benchmark(
            getattr(self.controller, "benchmark_plans", {}),
            pending["decision"],
            pending["context"].get("benchmark_choices", ()),
            current.get("benchmark_choices", ()),
        )
        return workflow.complete_controller_decision(
            pending["sequence"],
            result,
            expected_revision=current["revision"],
        )

    def _prepare_pipeline_change(self, workflow, pending, current, evidence_hash):
        from devbench.analysis.method_planner_contract import pipeline_artifact_versions

        decision = pending["decision"]
        if self.tier_a_executor is None or not decision["evidence_ids"]:
            raise OperationBlocked("tier_a_pipeline_requires_current_evidence")
        offered = {
            item["version_id"]: item["sha256"]
            for item in pending["context"].get("pipeline_inputs", [])
        }
        current_offers = {
            item["version_id"]: item["sha256"]
            for item in current.get("pipeline_inputs", [])
        }
        versions = pipeline_artifact_versions(decision).values()
        if any(
            key not in offered or offered[key] != current_offers.get(key)
            for key in versions
        ):
            raise OperationBlocked("tier_a_pipeline_inputs_not_currently_offered")
        step = self.controller.compile_pipeline_step(
            workflow,
            decision,
            executor=self.tier_a_executor,
            expected_revision=current["revision"],
        )
        diagnostic = (
            self.tier_a_executor.mounted_design_preflight(step.parameters_sha256)
            if "model" in decision["design"]
            else None
        )

        def apply(db):
            workflow.insert(step, evidence_sha256=evidence_hash, connection=db)
            result = {
                "status": "applied",
                "kind": "propose_pipeline_step",
                "step_id": step.step_id,
            }
            if diagnostic is not None:
                result["design_preflight"] = diagnostic
            return result

        return apply

    def _prepare_registered_step_change(self, workflow, decision, evidence_hash):
        kind = decision["kind"]
        action = self.controller.action_catalog.get(decision["action_id"])
        if action is None:
            raise ValueError("Controller selected an unregistered action")
        if kind != "insert" and not decision["evidence_ids"]:
            raise ValueError(
                "Revision and resolving checks require observed triggering evidence"
            )

        def apply(db):
            if kind == "insert":
                workflow.insert(action, evidence_sha256=evidence_hash, connection=db)
            elif kind == "revise":
                workflow.revise(
                    action,
                    reason=decision["reason"],
                    evidence_sha256=evidence_hash,
                    connection=db,
                )
            else:
                self.schedule_review_check(
                    decision["hold_id"],
                    workflow,
                    action.model_dump(mode="json"),
                    connection=db,
                )
            return {"status": "applied", "kind": kind, "step_id": action.step_id}

        return apply

    def _prepare_sweep(self, workflow, decision):
        members = {
            item["step_id"]: item["omission_reason"] for item in decision["members"]
        }
        if len(members) != len(decision["members"]):
            raise ValueError("Sweep members must be distinct")

        def apply(db):
            prior_members = sum(
                len(value) for value in workflow._state(db).sweeps.values()
            )
            if prior_members + len(members) > 24:
                raise OperationBlocked("sweep_member_budget_exhausted")
            workflow.declare_sweep(decision["sweep_id"], members, connection=db)
            return {"status": "applied", "kind": "declare_sweep", "members": members}

        return apply

    def _prepare_branch_retirement(self, workflow, decision, evidence_hash):
        if not decision["evidence_ids"]:
            raise ValueError("Branch retirement requires current evidence")

        def apply(db):
            workflow.retire_branch(
                decision["branch_id"],
                reason=decision["reason"],
                evidence_sha256=evidence_hash,
                connection=db,
            )
            return {"status": "applied", "kind": "retire_branch"}

        return apply

    def _prepare_review_response(self, decision):
        from .adaptive_workflow import request_digest

        if self.review_lifecycle is None:
            raise OperationBlocked("current_review_lifecycle_required")

        def respond(db):
            return self.review_lifecycle.controller_response(
                decision["hold_id"],
                connection=db,
                response_sha256=request_digest(decision),
                relevant_evidence_ids=decision["evidence_ids"],
            )

        return respond

    def _route_adaptive_diagnostic(self, workflow, pending, revision):
        decision = pending["decision"]
        action = self._diagnostic_actions.get(decision["action_id"])
        if action is None or action["descriptor"] not in pending["context"].get(
            "diagnostic_actions", ()
        ):
            raise PermissionError(
                "Diagnostic routing requires a visible registered action"
            )
        descriptor = action["descriptor"]
        if descriptor["input_evidence_id"] not in decision["evidence_ids"]:
            raise PermissionError(
                "Diagnostic routing requires a citation to its admitted input evidence"
            )
        return self.apply_rna_diagnostic_policy(
            action["policy"],
            action["inputs"],
            action["context"],
            action["state"],
            input_evidence_id=descriptor["input_evidence_id"],
            hold_id=descriptor["hold_id"],
            workflow=workflow,
            step=action["step"],
            controller_sequence=pending["sequence"],
            controller_expected_revision=revision,
        )

    def _page_adaptive_context(self, workflow, pending, revision):
        decision = pending["decision"]
        section, cursor = decision["section"], decision["cursor"]
        page = pending["context"].get("context_pages", {}).get(section)
        if page is None or cursor not in (page["next_cursor"], page["previous_cursor"]):
            raise OperationBlocked("current_controller_context_cursor_required")
        offset, cumulative = cursor.split(":", 1)
        return workflow.complete_controller_decision(
            pending["sequence"],
            {
                "status": "paged",
                "kind": "page_context",
                "section": section,
                "offset": int(offset),
                "digest": cumulative,
            },
            expected_revision=revision,
        )

    def _finish_adaptive_stop(self, workflow, pending):
        decision = pending["decision"]
        current = self.adaptive_controller_context(workflow)
        termination_required = pending["context"]["termination_required"]
        joint_publications = current.get("joint_publication_choices", ())
        work_remains = current["ready"] or joint_publications
        if current["available_resolving_checks"]:
            raise OperationBlocked("necessary_resolving_computation_available")
        if work_remains and not termination_required:
            raise OperationBlocked("useful_ready_computation_remaining")
        if work_remains:
            completed = "succeeded" in workflow.status()["outcomes"].values()
            decision = {
                **decision,
                "disposition": "partial" if completed else "unresolved",
                "unresolved": [
                    *decision["unresolved"],
                    *(
                        "Joint BH publication remains pending for " + item["family_id"]
                        for item in joint_publications
                    ),
                ],
            }
        if decision["disposition"] == "completed_exploratory":
            if current["blocked"] or current["open_holds"]:
                raise OperationBlocked("current_reviews_and_resolved_scope_required")
            self.controller.require_current_completion_reviews(
                workflow,
                review_binding=self.review_binding,
            )
        reason = (
            "controller_decision_budget_exhausted"
            if work_remains and termination_required
            else decision["reason"]
        )

        def prepare(db):
            outcome = self._adaptive_outcome(
                workflow, reason, decision=decision, connection=db
            )
            if outcome["status"] == "completed_exploratory":
                if outcome["remaining_ready_steps"] or outcome["open_holds"]:
                    raise OperationBlocked(
                        "current_reviews_and_resolved_scope_required"
                    )
                self.controller.require_current_completion_reviews(
                    workflow,
                    review_binding=self.review_binding,
                    connection=db,
                )
            return outcome

        from devbench.analysis.method_planner_recovery import _plan_lease

        with _plan_lease(self.planner, workflow.lineage_id):
            return workflow.finish_controller_stop(
                pending["sequence"],
                expected_revision=current["revision"],
                prepare=prepare,
            )

    def _observe_adaptive_selection(self, workflow, pending, current):
        from .adaptive_workflow import request_digest

        decision, sequence = pending["decision"], pending["sequence"]
        kind = decision["kind"]
        if kind == "dispatch":
            return self._dispatch_adaptive_selection(
                workflow, pending, current["revision"]
            )
        if kind == "review":
            return self.controller.review(workflow, decision["touchpoint"], sequence)
        if kind == "query_literature":
            handle = next(
                (
                    item
                    for item in self.controller.literature_handles
                    if item.handle_id == decision["handle_id"]
                ),
                None,
            )
            if handle is None:
                raise PermissionError(
                    "Targeted Literature requires its current entitled handle"
                )
            return handle.query(
                decision["request"],
                claim_id=decision["claim_id"],
                context_id=self.controller.context_id,
                request_id="controller-"
                + request_digest([workflow.lineage_id, sequence])[:32],
            )
        handle = next(
            (
                row
                for row in pending["context"].get("query_handles", [])
                if row.get("handle_id") == decision["handle_id"]
                and row["content_status"] == "available"
            ),
            None,
        )
        if handle is None:
            raise OperationBlocked("current_controller_query_handle_required")
        reservation_id = request_digest(
            [
                "controller-query",
                workflow.lineage_id,
                sequence,
                pending["context_sha256"],
            ]
        )[:32]
        page = self._query_controller_evidence(
            workflow,
            handle,
            decision["query"],
            reservation_id=reservation_id,
        )
        return {"status": page["status"], "kind": kind, "page": page}

    def _dispatch_adaptive_selection(self, workflow, pending, revision):
        step_id = pending["decision"]["step_id"]
        if step_id not in pending["context"]["ready"]:
            raise OperationBlocked("selected_step_not_ready")
        selected_generation = next(
            (
                row["generation"]
                for row in pending["context"]["graph"]["steps"]
                if row["step_id"] == step_id
            ),
            None,
        )
        retained = workflow.current_generation_outcome(
            step_id,
            selected_generation,
            expected_revision=revision,
        )
        if retained is not None:
            result = {**retained, "reconciled": True}
        else:
            result = self.perform_adaptive(
                workflow, step_id, expected_revision=revision
            )
        self._record_adaptive_check_outputs(workflow, step_id)
        return result

    def _record_adaptive_check_outputs(self, workflow, step_id):
        if self.review_lifecycle is None:
            return
        from .adaptive_workflow import request_digest

        for hold in self.review_lifecycle.state()["holds"].values():
            if not any(
                item["action_id"] == step_id and item["status"] == "scheduled"
                for item in hold["checks"]
            ):
                continue
            with workflow.ledger._database() as db:
                outcome = workflow._state(db).outcome(step_id)
            if outcome is None or outcome["status"] != "succeeded":
                continue
            artifact = next(iter(outcome["outputs"].values()))
            identity = (
                "check-output-"
                + request_digest([hold["hold_id"], artifact["version_id"]])[:32]
            )
            self.review_lifecycle.register_evidence(
                identity, artifact["sha256"], relevant_entity_ids=hold["scope"]
            )
            self.review_lifecycle.record_check_result(
                hold["hold_id"],
                action_id=step_id,
                result_sha256=artifact["sha256"],
                evidence_ids=(identity,),
            )

    def reconcile_adaptive(self, workflow):
        """Recover ledger-owned registered work before choosing the next action."""
        from .adaptive_workflow import RESEARCH_INFERENCE, AdaptiveWorkflow

        if self.allow_data_execution is not True:
            return []
        if type(workflow) is not AdaptiveWorkflow or workflow.ledger is not self.ledger:
            raise ValueError("Adaptive recovery requires the bound workflow ledger")
        candidates = [
            executor
            for executor in (self.executor, self.tier_a_executor)
            if workflow._registered_backend(executor) is not None
        ]
        if not candidates:
            return []
        workflow.review_admission = self.adaptive_review_admission
        workflow.diagnostic_admission = self.adaptive_diagnostic_admission
        recovered = []
        for executor in candidates:
            pending = workflow._unresolved_dispatches(executor)
            subjects = {
                envelope["step"]["step_id"]
                for _, envelope, _ in pending
                if envelope["step"]["purpose"] == RESEARCH_INFERENCE
            }
            for step_id in sorted(subjects):
                try:
                    self.prepare_research_admission(workflow, step_id)
                except OperationBlocked:
                    # Recovery may retain an audit-only outcome under a new hold.
                    continue
            recovered.extend(workflow.reconcile_pending(executor))
        return recovered

    def _adaptive_executor_for_step(self, workflow, step):
        from devbench.core.contrast_design import TIER_A_BULK_OPERATION
        from devbench.engine.exploratory_oci import ExploratoryOCI
        from devbench.engine.tier_a_executor import TierAExecutor
        from devbench.engine.tier_a_scrna import is_single_cell_operation

        from .adaptive_workflow import RESEARCH_INFERENCE

        if step.purpose == RESEARCH_INFERENCE:
            if (
                step.oci_method_version_id is not None
                or step.operation_id != TIER_A_BULK_OPERATION
            ):
                raise OperationBlocked("unsupported_registered_research_operation")
            executor = self.tier_a_executor
            if executor is None:
                raise OperationBlocked("registered_tier_a_research_executor_required")
            self.prepare_research_admission(workflow, step.step_id)
        elif is_single_cell_operation(step.operation_id):
            executor = self.tier_a_executor
            if executor is None:
                raise OperationBlocked(
                    "registered_tier_a_single_cell_executor_required"
                )
        else:
            executor = self.executor
            if executor is None or isinstance(executor, TierAExecutor):
                raise PermissionError(
                    "Adaptive dispatch requires a registered step executor"
                )
            if step.oci_method_version_id is None and isinstance(
                executor, ExploratoryOCI
            ):
                raise PermissionError(
                    "The OCI executor requires a registered OCI method"
                )
            if step.primary or step.purpose == "qualified_inference":
                self.require_primary_review(step.step_id)
        return executor

    def perform_adaptive(self, workflow, step_id, *, expected_revision=None):
        """Dispatch with fresh runtime authority through the scientific ledger."""
        from .adaptive_workflow import AdaptiveWorkflow

        if self.allow_data_execution is not True:
            raise PermissionError(
                "Adaptive dispatch requires fresh data execution authority "
                "and an executor"
            )
        if type(workflow) is not AdaptiveWorkflow or workflow.ledger is not self.ledger:
            raise ValueError(
                "Adaptive dispatch requires the bound scientific operation ledger"
            )
        if (
            self.review_lifecycle is not None
            and self.review_lifecycle.lineage_id != workflow.lineage_id
        ):
            raise ValueError("Review admission belongs to another scientific lineage")
        workflow.review_admission = self.adaptive_review_admission
        workflow.diagnostic_admission = self.adaptive_diagnostic_admission
        step = workflow._read_projection(lambda db: workflow._state(db).nodes[step_id])
        executor = self._adaptive_executor_for_step(workflow, step)
        return workflow.dispatch(
            step_id, executor, actor=self.actor, expected_revision=expected_revision
        )

    def inspect_exploratory_figure(
        self, workflow, *, step_id, figure_port, numeric_port
    ):
        """Verify figure and source data from this workflow's checked receipt."""
        from devbench.engine.exploratory_oci import ExploratoryOCI

        if (
            type(self.executor) is not ExploratoryOCI
            or workflow.ledger is not self.ledger
        ):
            raise PermissionError(
                "Figure checks require the bound exploratory workflow"
            )
        return self.executor.inspect_figure(
            workflow,
            step_id=step_id,
            figure_port=figure_port,
            numeric_port=numeric_port,
        )

    def publish_joint_bh(
        self, workflow: "AdaptiveWorkflow", family_id: str
    ) -> "JointBHRelease":
        """Release an approved cross-aim family after every verified fit completes."""
        publish = self._prepare_joint_bh(workflow, family_id)
        with workflow.authority._execution_tx(workflow.lineage_id) as db:
            return publish(db)

    def _prepare_joint_bh(
        self,
        workflow: "AdaptiveWorkflow",
        family_id: str,
        publication_sha256: str | None = None,
    ) -> Callable[[sqlite3.Connection], "JointBHRelease"]:
        from .adaptive_workflow import AdaptiveWorkflow
        from .thesis_aims import ThesisAimLifecycle
        from .thesis_joint_bh import ThesisJointBHRegistry
        from .thesis_joint_bh_release import prepare_joint_bh_release

        if (
            self.allow_data_execution is not True
            or type(workflow) is not AdaptiveWorkflow
            or workflow.ledger is not self.ledger
            or self.tier_a_executor is None
            or self.review_lifecycle is None
            or self.review_lifecycle.lineage_id != workflow.lineage_id
        ):
            raise PermissionError("Joint BH requires authorized Tier A execution")
        workflow.review_admission = self.adaptive_review_admission
        workflow.diagnostic_admission = self.adaptive_diagnostic_admission
        aims = ThesisAimLifecycle(self.ledger, workflow.lineage_id)
        retained = ThesisJointBHRegistry(aims).read(family_id)
        registered = workflow._read_projection(
            lambda db: set(workflow._state(db).nodes)
        )
        for member in retained.plan.members:
            if member.step_id is not None and member.step_id in registered:
                self.prepare_research_admission(workflow, member.step_id)
        return prepare_joint_bh_release(
            workflow,
            self.tier_a_executor,
            family_id,
            expected_publication_sha256=publication_sha256,
        )

    def perform(self, node, cursor):
        state = validate_workflow_state(cursor)
        if self.retained_synthesis and node not in {
            "verify",
            "recommend",
            "conclusion_review",
        }:
            raise ValueError(
                "Historical evidence validation only permits retained synthesis."
            )
        self.check_node_entry(node, state)
        stopped = self._serial_preflight(node, state)
        if stopped is not None:
            return stopped
        attempt = self.operation_attempt(state, node)
        request = self._serial_operation_request(node, state)

        def callback():
            return self._perform_serial_operation(node, state, attempt, request)

        try:
            result = self.ledger.run(
                state["workflow_id"], node, attempt, request, callback
            )
        except OperationBlocked as error:
            return validate_workflow_state(
                {
                    **state,
                    "phase": node,
                    "status": "cancelled"
                    if error.state == "cancelled"
                    else "reconciliation_required",
                    "failure": {
                        "kind": error.state,
                        "message": "Operation retained; explicit reconciliation "
                        "required before further work",
                    },
                }
            )
        except Exception as error:
            retained = self.ledger.get(state["workflow_id"], node, attempt)
            if retained is not None and retained["status"] in {"uncertain", "failed"}:
                message = (
                    "Operation effects are uncertain; no automatic redispatch. "
                    f"{type(error).__name__}: {error}"
                )
                return validate_workflow_state(
                    {
                        **state,
                        "phase": node,
                        "status": "reconciliation_required",
                        "failure": {
                            "kind": retained["status"],
                            "message": message[:500],
                        },
                    }
                )
            raise
        self.check_node_output(node, state, result)
        if node in _SCIENCE_NODES:
            self._verify_science_evidence(node, state["session_id"], result, request)
        self.check(state, expected_hash=False)
        if (
            digest(self.planner.get(state["session_id"]))
            != result["after_planner_hash"]
        ):
            raise ValueError(
                "Completed operation replay rejected: current planner is stale"
            )
        ref = f"{node}:{attempt}"
        return validate_workflow_state(
            {
                **state,
                **result["update"],
                "expected_planner_hash": result["after_planner_hash"],
                "operation_refs": state["operation_refs"]
                + ([] if ref in state["operation_refs"] else [ref]),
            }
        )

    def _serial_preflight(self, node, state):
        if self.ledger.is_cancelled(state["workflow_id"]):
            return {**state, "phase": node, "status": "cancelled"}
        if (
            node == "audit"
            and not self.allow_data_execution
            and self.planner.get(state["session_id"]).get("scrna_population_input")
            is not None
        ):
            return validate_workflow_state(
                {
                    **state,
                    "phase": node,
                    "status": "review_required",
                    "failure": {
                        "kind": "data_execution_authority_required",
                        "message": "Supplied-population preflight requires fresh "
                        "explicit data execution authority",
                    },
                }
            )
        if node == "run":
            if not self.allow_data_execution:
                return {
                    **state,
                    "phase": node,
                    "status": "failed",
                    "failure": {
                        "kind": "PermissionError",
                        "message": "Runtime data execution authority is required",
                    },
                }
            self._check_trial_cursor(state)
            if self.review_lifecycle is not None:
                self.require_primary_review(state["trial_ids"][state["trial_index"]])
            if (
                self.requested_experiment_id is not None
                and state["trial_ids"][state["trial_index"]]
                != self.requested_experiment_id
            ):
                raise ValueError(
                    "Runtime execution request differs from the next exact "
                    "declared trial"
                )
        if node in _SCIENCE_NODES and self.scientific_workflow is None:
            return validate_workflow_state(
                {
                    **state,
                    "phase": node,
                    "status": "review_required",
                    "failure": {
                        "kind": "scientific_workflow_unconfigured",
                        "message": "A configured bounded scientific discovery "
                        "and review runtime is required",
                    },
                }
            )
        return None

    def _serial_operation_request(self, node, state):
        request = {
            "session_id": state["session_id"],
            "expected_planner_hash": state["expected_planner_hash"],
            "actor": self.actor,
            "binding_digest": state["binding_digest"],
            "artifact_refs": state["artifact_refs"],
            "version_ids": self.version_ids if node == "resolve" else [],
            "declarations": self.declarations if node == "declare" else [],
            "trial_ids": state["trial_ids"] if node == "run" else [],
            "execution_binding_digest": digest(self.execution_bindings)
            if node == "run"
            else None,
            "selector_digest": (
                digest(self.selector.configuration())
                if hasattr(self.selector, "configuration")
                else None
            ),
            "science_configuration_digest": self._science_configuration_digest(),
        }
        if node == "design" and self.execution_input_request is not None:
            request["execution_inputs"] = deepcopy(self.execution_input_request)
        return request

    def _perform_serial_operation(self, node, state, attempt, request):
        plan = self.check(state)
        update = {"phase": node, "status": "ready", "failure": None}
        kwargs = {
            "actor": self.actor,
            "allow_model_execution": self.allow_model_execution,
        }
        evidence = None
        session_id = state["session_id"]
        if node in _SCIENCE_NODES:
            evidence = self.scientific_workflow.perform(
                node,
                self.planner,
                session_id,
                actor=self.actor,
                selector=self.selector,
                **(
                    {"attempt": attempt}
                    if node == "conclusion_review" and attempt
                    else {}
                ),
            )
            update.update(self._science_update(node, state, evidence))
        elif node == "audit":
            evidence = self._audit(plan)
            if evidence is not None:
                update.update(self._audit_update(plan, evidence))
        elif node == "run":
            update.update(self._run_serial_trial(state, plan))
        elif node == "verify":
            self.planner.evidence_assessment(session_id)
            update["outcome_ref"] = {
                "session_id": session_id,
                "planner_hash": digest(plan),
                "kind": "evidence",
            }
        elif node == "recommend":
            recommendation = self.planner.recommend(session_id, self.selector, **kwargs)
            update["status"] = "ready"
            update["outcome_ref"] = {
                "session_id": session_id,
                "planner_hash": digest(self.planner.get(session_id)),
                "kind": "recommendation"
                if recommendation["choice"] is not None
                else "abstention",
            }
        else:
            self._apply_serial_planning(node, session_id, plan, update, kwargs)
        after = self.planner.get(session_id)
        if any(
            (key in plan) != (key in after) or plan.get(key) != after.get(key)
            for key in (set(plan) | set(after)) - _PLANNER_EFFECTS[node]
        ):
            raise ValueError(
                "Workflow node changed planner fields outside its execution contract"
            )
        result = {
            "before_planner_hash": state["expected_planner_hash"],
            "after_planner_hash": digest(after),
            "update": update,
        }
        if evidence is not None:
            result["evidence"] = evidence
            if node in _SCIENCE_NODES:
                self._verify_science_evidence(node, session_id, result, request)
        self.check_node_output(node, state, result)
        return result

    def _apply_serial_planning(self, node, session_id, plan, update, kwargs):
        if node == "question":
            # The planner validates objective and requirement attribution.
            if not plan["context"]["task"].get("scientific_objective"):
                raise ValueError("An explicit scientific objective is required")
        elif node in {"review", "execution_review"}:
            # The graph invokes these after its orchestration pause is resumed.
            # This receipt grants no scientific or execution approval.
            pass
        elif node in {"generate", "resolve"}:
            workflow = self.scientific_workflow
            if workflow is not None and hasattr(workflow, "generation_context"):
                kwargs["discovery_context"] = workflow.generation_context(
                    self.planner, session_id
                )
            if node == "generate":
                self.planner.generate_candidates(session_id, self.selector, **kwargs)
            else:
                self.planner.resolve_capabilities(
                    session_id,
                    self.selector,
                    self.version_ids,
                    **kwargs,
                )
        elif node == "design":
            self._design_serial_trials(session_id, plan, kwargs)
        elif node == "declare":
            selected = self._selected(plan)
            for branch, draft in selected:
                self.planner.declare_trial(session_id, branch, draft, actor=self.actor)
            update["trial_ids"] = [draft["id"] for _, draft in selected]
        else:
            raise ValueError("Unknown workflow operation")

    def _design_serial_trials(self, session_id, plan, kwargs):
        # The absence of an eligible implementation is a planning outcome.
        candidates = plan.get("candidates", [])
        if candidates and any(
            item.get("method_version_id") is not None
            and item.get("applicability") == "eligible"
            for item in candidates
        ):
            if self.execution_input_request is not None:
                kwargs.update(
                    execution_inputs_ref=self.execution_input_request["record_ref"],
                    execution_workspace=self.execution_input_request["workspace"],
                )
            self.planner.design_trials(session_id, self.selector, **kwargs)
        elif self.execution_input_request is not None:
            raise ValueError("Late execution inputs require a resolved eligible method")

    def _run_serial_trial(self, state, plan):
        experiment = state["trial_ids"][state["trial_index"]]
        options = {}
        if (
            "fastq_input" in plan["context"]["dataset_summary"].get("observed", {})
            or plan.get("scrna_population_input") is not None
        ):
            options["cancellation_event"] = _LedgerCancellationEvent(
                self.ledger,
                state["workflow_id"],
            )
        if self.before_dispatch is not None:
            options["before_dispatch"] = self.before_dispatch
        results = self.planner.run_trial(
            state["session_id"],
            experiment,
            actor=self.actor,
            allow_data_execution=self.allow_data_execution,
            executor=self.executor,
            execution_bindings=self.execution_bindings,
            **options,
        )
        update = {"trial_index": state["trial_index"] + 1}
        if any(
            row["status"] in {"failed", "resource_limit", "interrupted"}
            for row in results
        ):
            update.update(
                status="failed",
                failure={
                    "kind": "trial_failed",
                    "message": "Trial failure retained in authoritative planner "
                    "results",
                },
            )
        return update
