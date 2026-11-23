"""Publication-aware response projections shared by Workbench routes."""

import math
import re
from datetime import datetime
from pathlib import Path

from devbench.analysis.scientific_publication import (
    public_metadata,
    safe_diagnostic,
    workspace_guard,
)
from devbench.engine.workflow_operations import OperationBlocked, OperationConflict

ACTIVE = {
    "queued",
    "inspecting",
    "planning",
    "recording_review",
    "preparing",
    "running",
    "cancel_requested",
    "finalizing",
}


def _publication_hashes(app):
    registry = getattr(getattr(app, "scientific", None), "publication_registry", None)
    roots = (
        getattr(app, "workspace", None),
        getattr(getattr(app, "scientific", None), "root", None),
        registry.ledger.path.parent if registry is not None else None,
    )
    blocked = set()
    for root in {root for root in roots if isinstance(root, Path)}:
        guard = workspace_guard(root)
        guard.require()
        blocked.update(guard.blocked_hashes)
    return frozenset(blocked)


def _publication_guard(app):
    scientific = getattr(app, "scientific", None)
    return (
        scientific.publication_guard()
        if hasattr(scientific, "publication_guard")
        else workspace_guard(app.workspace)
    )


def _job_source_hashes(record):
    proposal = record.get("proposal") or {}
    intake = record.get("intake_binding") or {}
    reviewed = record.get("reviewed_binding") or {}
    return (
        (proposal.get("external_input") or {}).get("sha256"),
        (intake.get("external_input") or {}).get("sha256"),
        *(reviewed.get("input_sha256") or {}).values(),
        *(value for key, value in intake.items() if key.endswith("_sha256")),
        *(
            item.get("sha256")
            for item in intake.get("sources", ())
            if isinstance(item, dict)
        ),
        (intake.get("annotation") or {}).get("sha256"),
        *(
            item.get("sha256")
            for item in (record.get("downloads") or {}).values()
            if isinstance(item, dict)
        ),
    )


def _planning_result_view(planning):
    if not isinstance(planning, dict):
        return None
    status = planning.get("status")
    if status not in (
        "proposal_ready_for_review",
        "awaiting_information",
        "refusal",
        "invalid_proposal",
        "infrastructure_failure",
    ):
        return None
    summary = {"status": status}
    decision = planning.get("decision")
    kind = decision.get("kind") if isinstance(decision, dict) else None
    if kind in ("propose", "request_information", "refuse"):
        summary["decision"] = {"kind": kind}
    receipt = planning.get("authoring_receipt")
    identity = receipt.get("root_hash") if isinstance(receipt, dict) else None
    if isinstance(identity, str) and re.fullmatch(r"[a-f0-9]{64}", identity):
        summary["authoring_receipt"] = {"root_hash": identity}
    return summary


def _download_publication_subjects(app, record, registry):
    from devbench.analysis.scientific_publication import (
        publication_descriptor_for_source,
    )

    subjects = {}
    for item in (record.get("downloads") or {}).values():
        if isinstance(item, dict) and isinstance(item.get("sha256"), str):
            subject = registry.subject_for_source(item["sha256"])
            if subject is not None:
                subjects[subject.subject_id] = subject
            elif (
                publication_descriptor_for_source(app, item["sha256"])
                is not None
            ):
                # Do not discard the download identity before checking
                # every supported owner ledger or a concurrent binding.
                raise PermissionError("publication_source_withheld")
    return subjects


def _job_publication_subject(app, record, registry):
    from devbench.analysis.scientific_publication import (
        publication_descriptor_for_source,
    )

    if registry is None:
        if any(
            publication_descriptor_for_source(app, identity) is not None
            for identity in _job_source_hashes(record)
            if isinstance(identity, str)
        ):
            raise PermissionError("publication_subject_required")
        return record, None
    subjects = _download_publication_subjects(app, record, registry)
    if subjects:
        current_id = record.get("publication_subject_id")
        if current_id is not None:
            with registry.ledger._database() as db:
                db.execute("BEGIN")
                current, _ = registry._subject(db, current_id)
                if current.source_sha256 not in {
                    item.source_sha256 for item in subjects.values()
                }:
                    raise PermissionError("publication_source_withheld")
        elif len(subjects) == 1:
            record = {**record, "publication_subject_id": next(iter(subjects))}
        else:
            from devbench.analysis.scientific_publication import (
                project_subject_metadata,
            )

            return record, {
                key: record[key]
                for key in ("id", "status", "operation_kind")
                if key in record
            } | {
                "publications": [
                    project_subject_metadata(app, {"subject_id": identity})
                    for identity in subjects
                ]
            }
    return record, None


def _publication_status(app, record):
    try:
        blocked = _publication_hashes(app)
        if any(
            isinstance(value, str) and value in blocked
            for value in _job_source_hashes(record)
        ):
            raise PermissionError("publication_source_withheld")
        registry = getattr(
            getattr(app, "scientific", None), "publication_registry", None
        )
        record, published = _job_publication_subject(app, record, registry)
        if published is not None:
            return published
    except PermissionError:
        return {
            key: record[key]
            for key in (
                "id",
                "operation_kind",
                "status",
                "updated_at",
                "investigation_id",
                "thread_id",
                "request_identity",
            )
            if key in record
        } | {
            "publication_status": "withheld",
            "publication_reason": "publication_source_withheld",
        }
    planning = _planning_result_view(record.get("planning_result"))
    record = {
        key: value
        for key, value in record.items()
        if key
        not in {
            "downloads",
            "raw_result",
            "result",
            "attachments",
            "planning_result",
            "validation",
            "review_evidence",
            "method_source",
        }
    }
    if planning is not None:
        record["planning_result"] = planning
    error = record.get("error")
    if isinstance(error, dict):
        record["error"] = {
            "type": error.get("type"),
            "message": safe_diagnostic(error.get("message")),
        }
    execution = record.get("execution")
    if isinstance(execution, dict):
        record["execution"] = {
            key: execution[key]
            for key in (
                "bundle_root",
                "manifest_sha256",
                "archive_sha256",
                "status",
                "completed_analysis",
            )
            if key in execution
        }
        if execution.get("reason") is not None:
            record["execution"]["reason"] = safe_diagnostic(record.get("status"))
    review = record.get("review")
    if isinstance(review, dict):
        record["review"] = {
            key: review[key]
            for key in ("decision", "reviewer", "status", "confirmations")
            if key in review
        }
    from devbench.analysis.scientific_publication import project_subject_metadata

    return project_subject_metadata(
        app, public_metadata(record, blocked_hashes=blocked)
    )


def _require_job_publication(app, record, *digests):
    blocked = _publication_hashes(app)
    if any(
        isinstance(value, str) and value in blocked
        for value in (*_job_source_hashes(record), *digests)
    ):
        raise PermissionError("publication_source_withheld")


_EVENT_TOOLS = {
    "analysis",
    "audit_expression_quality",
    "validate_design_matrix",
    "analyze_batch_effects",
    "mean_condition_analysis",
    "query_evidence",
    "scrna_population_audit",
    "scrna_population_design",
    "scrna_population_pseudobulk",
    "scrna_population_contrast",
}
_EVENT_PHASES = {"Exploring", "Planning", "Running pipeline", "Synthesizing"}
_EVENT_STATUSES = ACTIVE | {
    "completed",
    "completed_review_passed",
    "completed_review_failed",
    "failed",
    "cancelled",
    "operation_failed",
    "awaiting_information",
    "awaiting_operator_review",
    "approved",
    "recovery_required",
    "unsupported",
    "succeeded",
}


def _public_event(event):
    from .workflow_state import SERIAL_ROUTE

    kind, phase, status = event.get("type"), event.get("phase"), event.get("status")
    value = {
        "type": kind
        if type(kind) is str and kind in {"tool", "canceled", "task_action"}
        else "task_action",
        "phase": phase
        if type(phase) is str and phase in _EVENT_PHASES
        else "Exploring",
        "status": status
        if type(status) is str and status in _EVENT_STATUSES
        else "unknown",
    }
    sequence, tool, duration, at = (
        event.get(key) for key in ("sequence", "tool", "duration_s", "at")
    )
    if type(sequence) is int and sequence >= 0:
        value["sequence"] = sequence
    if type(tool) is str and tool in _EVENT_TOOLS.union(SERIAL_ROUTE):
        value["tool"] = tool
    if (
        type(duration) in (int, float)
        and math.isfinite(duration)
        and 0 <= duration <= 86400
    ):
        value["duration_s"] = duration
    if type(at) is str and len(at) <= 40:
        try:
            value["at"] = datetime.fromisoformat(at).isoformat()
        except ValueError:
            pass
    return value


def _public_events(payload):
    phase, status = payload.get("phase"), payload.get("status")
    value = {
        "format": "devbench.investigation-events/1",
        "events": [
            _public_event(event)
            for event in payload.get("events", ())
            if isinstance(event, dict)
        ],
        "phase": phase
        if type(phase) is str and phase in _EVENT_PHASES
        else "Exploring",
        "status": status
        if type(status) is str and status in _EVENT_STATUSES
        else "unknown",
        "terminal": payload.get("terminal") is True,
    }
    if type(payload.get("total_events")) is int and payload["total_events"] >= 0:
        value["total_events"] = payload["total_events"]
    return value


def _public_investigation_turns(turns):
    return [
        {key: turn[key] for key in ("id", "kind", "step", "at", "role") if key in turn}
        if turn.get("kind") in {"synthesis", "error", "analyst_query"}
        else turn
        for turn in turns
        if isinstance(turn, dict)
    ]


def _is_read_only_description(record):
    return (
        (record.get("request") or {}).get("category") == "descriptive_retrieval"
        and (record.get("descriptive_answer") or {}).get("completion") == "completed"
        and not record.get("scientific_session_id")
        and not record.get("job_id")
    )


def _adaptive_step_title(operation):
    if operation == "scrna_batch_qc":
        return "Per-capture quality control"
    return operation.replace("_", " ").replace("-", " ").capitalize()


def _adaptive_step_rows(entries):
    receipts = {
        (row["step_id"], row["generation"]): row["receipt_sha256"]
        for row in entries
        if row["entry_kind"] == "attempt" and row.get("receipt_sha256")
    }
    steps = []
    for row in entries:
        if row["entry_kind"] != "step":
            continue
        identity, generation = row["step_id"], row["generation"]
        step = {
            "id": identity,
            "title": _adaptive_step_title(row["operation_id"]),
            "operation": row["operation_id"],
            "status": row["status"],
            "generation": generation,
        }
        if row.get("reason"):
            step["detail"] = safe_diagnostic(row["reason"]).replace("_", " ")
        if receipt := receipts.get((identity, generation)):
            step["receipt_sha256"] = receipt
        steps.append(step)
    return steps


def _adaptive_campaign_on_hold(app, binding, session_id):
    if binding.campaign_mode != "local_interactive_codex":
        return False
    association = app.scientific._plan(session_id).get("model_campaign") or {}
    identity = association.get("campaign_id")
    if identity is None:
        return False
    ledger = binding.delivery.broker.authority.ledger
    campaign = ledger.get_campaign(identity)
    return bool(
        campaign
        and campaign["session_id"] == session_id
        and (campaign["unresolved_count"] or campaign["status"] != "open")
    )


def _adaptive_next_action(view, *, campaign_hold):
    actions = view.get("actions") or {}
    graph = view.get("graph") or {}
    if actions.get("run_checked_step"):
        return (
            "Approve the proposed QC step",
            "The retained plan is ready. Review its scope and grant data execution.",
            "needs_approval",
        )
    if campaign_hold:
        return (
            "Review the stopped Codex campaign",
            "The current model campaign is held. Review its recorded state "
            "before sending another request.",
            "blocked",
        )
    model_required = actions.get("model_required")
    if model_required and (actions.get("advance") or actions.get("resume")):
        return (
            "Review the next Codex step",
            "Model selection and data execution each require fresh consent.",
            "needs_approval",
        )
    if actions.get("advance"):
        return (
            "Review the queued work",
            "The recorded step requires fresh execution consent to run.",
            "needs_approval",
        )
    if actions.get("model_available"):
        return (
            "Review the next Codex step",
            "Model selection and data execution each require fresh consent.",
            "needs_approval",
        )
    if graph.get("execution_block"):
        return (
            "Review the execution hold",
            safe_diagnostic(graph["execution_block"]).replace("_", " "),
            "blocked",
        )
    if any(
        row["entry_kind"] == "step" and row["status"] == "succeeded"
        for row in graph.get("entries", ())
    ):
        return (
            "Review the completed work",
            "Inspect the current findings and their execution record.",
            "ready",
        )
    return (
        "Review the plan and available steps",
        "The workflow is idle. Inspect the current plan or queue a supported step.",
        "ready",
    )


def _adaptive_plan(steps):
    if not steps:
        return {
            "title": "Workflow plan",
            "summary": "The investigation is bound. A workflow step is still needed.",
            "steps": [],
        }
    return {
        "title": "Recorded workflow plan",
        "summary": "These steps are recorded for this investigation.",
        "steps": [
            {"title": step["title"], "description": step["status"].replace("_", " ")}
            for step in steps
        ],
    }


def _adaptive_findings(app, binding, question, steps):
    if not getattr(app, "interactive_adaptive", False) or not any(
        step["operation"] == "scrna_batch_qc" and step["status"] == "succeeded"
        for step in steps
    ):
        return {"status": "pending"}
    from .workbench_qc_findings import current_qc_findings

    try:
        return current_qc_findings(app, binding, question) or {"status": "pending"}
    except (OperationBlocked, OperationConflict, PermissionError, ValueError, OSError):
        return {
            "status": "blocked",
            "summary": "QC completed. Its findings are temporarily unavailable.",
        }


def _adaptive_activity(view, action):
    attempts = [
        row
        for row in (view.get("graph") or {}).get("entries", [])
        if row["entry_kind"] == "attempt"
    ]
    last = attempts[-1] if attempts else None
    activity = {"events": list(view.get("events", []))}
    if action["status"] == "blocked":
        activity["current"] = {
            "title": action["label"],
            "detail": action["detail"],
            "status": action["status"],
        }
    if last:
        activity["last"] = {
            "title": last["step_id"],
            "detail": f"Recorded attempt for generation {last['generation']}.",
            "status": last["status"],
        }
    return activity


def _adaptive_running_view(binding, events):
    title = (
        "Codex workflow advance running"
        if binding.controller is not None
        else "Workflow execution running"
    )
    detail = "Waiting for a retained decision or execution receipt."
    current = {"title": title, "detail": detail, "status": "running"}
    return {
        "session_id": binding.session_id,
        "status": "running",
        "next_action": {"label": title, "detail": detail, "status": "running"},
        "activity": {"current": current, "events": [*events, current][-12:]},
    }


_STEP_OUTCOME_ACTIVITY = {
    "succeeded": ("Execution receipt recorded.", "succeeded"),
    "failed": ("Execution failed. Review the retained attempt.", "failed"),
    "audit_only": ("Attempt retained for audit.", "blocked"),
}


def _adaptive_timeline(flow, db):
    """Read a closed event vocabulary. Exclude serialized inputs and model prose."""
    from devbench.engine.scientific_authority import _row

    state = flow._state(db)
    rows = db.execute(
        "SELECT * FROM scientific_records WHERE lineage_id=? "
        "AND kind='adaptive_event' AND "
        "(json_extract(payload,'$.event') IN ('step','dispatch','outcome') "
        "OR (json_extract(payload,'$.event')='controller' AND "
        "(json_type(payload,'$.state.decision')='object' OR "
        "json_type(payload,'$.state.outcome')='object'))) "
        "ORDER BY revision DESC LIMIT 12",
        (flow.lineage_id,),
    ).fetchall()
    events = [
        _adaptive_activity_event(_row(row)["record"], state)
        for row in reversed(rows)
    ]
    return [event for event in events if event is not None]


def _adaptive_activity_event(event, state):
    kind, change = event["event"], event["state"]
    if kind == "controller":
        return {
            "title": "Codex workflow decision",
            "detail": "A workflow decision has been recorded.",
            "status": "recorded",
        }
    if kind == "step":
        step_id = change["step"]["step_id"]
        detail = (
            "Step revision queued for execution."
            if change.get("reason")
            else "Step queued for execution."
        )
        status = "queued"
    elif kind == "dispatch":
        step_id = change["envelope"]["step"]["step_id"]
        detail, status = "Execution dispatched.", "recorded"
    else:
        attempt = change["attempt_id"]
        step_id = state.dispatches[attempt]["envelope"]["step"]["step_id"]
        detail, status = _STEP_OUTCOME_ACTIVITY.get(
            change["status"], ("Execution attempt recorded.", "recorded")
        )
    if step_id not in state.nodes:
        return None
    title = _adaptive_step_title(state.nodes[step_id].operation_id)
    return {"title": title, "detail": detail, "status": status}


def _adaptive_structural_view(app, binding):
    """Build an operational summary under the existing scope without deliveries."""
    from devbench.analysis.evidence_access import _fields

    from .workbench_adaptive import (
        _approved_qc_candidate,
        _can_open_local_model,
        _flow,
        _graph_revision,
        _model_allowed,
    )

    plan, ledger = binding.require_owner(app.scientific)
    flow = _flow(binding, ledger)
    delivery = binding.delivery
    broker = delivery.broker
    with flow._read_connection() as db:
        recipient, policy = broker._context_policy(db, delivery.context_id)
        if (
            recipient["lineage_id"] != flow.lineage_id
            or recipient["role"] != "scientist"
        ):
            raise PermissionError("Workflow status requires its authorized scientist")
        source = broker._source(db, delivery.source_id)
        snapshot = flow.projection_snapshot(connection=db)
        controller = flow.controller_state(connection=db)
        entries = [
            {"entry_kind": kind, **{
                key: row[key] for key in (
                    "step_id", "operation_id", "status", "generation",
                    "receipt_sha256", "reason",
                ) if key in row
            }}
            for name, kind in (("steps", "step"), ("attempt_history", "attempt"))
            for row in snapshot[name][-32:]
        ]
        execution_block = snapshot["execution_block"]
        graph = {"entries": entries, "execution_block": execution_block}
        events = _adaptive_timeline(flow, db)
        fields = _fields({"graph": graph, "events": events})
        broker._check(recipient, policy, source, fields)
        revision = _graph_revision(flow, connection=db)
    model_required = (
        binding.controller is not None or controller["binding"] is not None
    )
    runnable = (
        (binding.executor is not None or binding.tier_a_executor is not None)
        and app.allow_data_execution is True
        and execution_block is None
        and (
            binding.controller is not None and _model_allowed(app, plan)
            if model_required
            else any(row["status"] == "ready" for row in snapshot["steps"])
        )
    )
    return {
        "graph_status": "available",
        "revision": revision,
        "graph": graph,
        "events": events,
        "actions": {
            "run_checked_step": _approved_qc_candidate(app, binding, flow),
            "advance": runnable and controller["outcome"] is None,
            "resume": runnable and controller["outcome"] is not None and model_required,
            "model_required": model_required,
            "model_available": (
                execution_block is None and _can_open_local_model(app, plan, binding)
            ),
        },
    }


def _adaptive_research_view(app, record):
    session_id = record.get("scientific_session_id")
    if not session_id or record.get("workflow_mode") != "adaptive/1":
        return None
    binding = getattr(app, "_adaptive_bindings", {}).get(session_id)
    try:
        view = _adaptive_structural_view(app, binding) if binding is not None else {}
        if binding is not None and session_id in getattr(
            app, "_adaptive_operations", ()
        ):
            return _adaptive_running_view(binding, view["events"])
        campaign_hold = bool(
            binding and _adaptive_campaign_on_hold(app, binding, session_id)
        )
    except (OperationBlocked, OperationConflict, PermissionError, ValueError, OSError):
        view, campaign_hold = {}, False
    if view.get("graph_status") != "available":
        return {
            "session_id": session_id,
            "status": "unavailable",
            "next_action": {
                "label": "Workflow readback unavailable",
                "detail": "Refresh to check the current workflow again.",
                "status": "blocked",
            },
        }
    title, detail, status = _adaptive_next_action(view, campaign_hold=campaign_hold)
    next_action = {"label": title, "detail": detail, "status": status}
    steps = _adaptive_step_rows(view["graph"]["entries"])
    # Findings require a delivery. Do not initiate one during a dispatch.
    with app._lock:
        if session_id in app._adaptive_operations:
            return _adaptive_running_view(binding, view["events"])
        findings = _adaptive_findings(app, binding, record.get("question", ""), steps)
    return {
        "session_id": session_id,
        "revision": view["revision"],
        "status": status,
        "plan": _adaptive_plan(steps),
        "steps": steps,
        "next_action": next_action,
        "activity": _adaptive_activity(view, next_action),
        "limitations": findings.get("limitations", []),
        "checked_observation": findings,
    }


def _public_investigation(app, value):
    job = value.get("job_state") or {}
    blocked = _publication_hashes(app)
    intake = [
        (value.get("profile") or {}).get("sha256"),
        *(
            item.get("sha256")
            for item in value.get("source_profiles", ())
            if isinstance(item, dict)
        ),
    ]
    if job.get("publication_status") == "withheld" or any(
        type(identity) is str and identity in blocked for identity in intake
    ):
        return {
            key: value[key]
            for key in ("id", "status", "revision", "job_id")
            if key in value
        } | {
            "publication_status": "withheld",
            "publication_reason": "publication_source_withheld",
        }
    result = dict(value)
    synthesis = result.get("synthesis")
    if isinstance(synthesis, dict):
        if synthesis.get("format") == "devbench.episode-publication/1":
            result["synthesis"] = {
                key: synthesis[key]
                for key in (
                    "format",
                    "outcome",
                    "reason",
                    "targets",
                    "outputs_complete",
                    "scientific_correctness",
                )
                if key in synthesis
            }
        elif _is_read_only_description(value):
            result["synthesis"] = {
                "source_sha256": value["descriptive_answer"]["source_sha256"],
                **{
                    key: synthesis[key]
                    for key in ("supported_findings", "uncertainties")
                    if key in synthesis
                },
            }
        else:
            result["synthesis"] = {"publication_status": "withheld"}
    if result.get("error"):
        result["error"] = {"message": "operation_unavailable"}
    for key in ("turns", "thread_turns"):
        if isinstance(result.get(key), list):
            result[key] = _public_investigation_turns(result[key])
    if research := _adaptive_research_view(app, value):
        result["adaptive_research"] = research
    return result


class WorkbenchInvestigationProjection:
    """Read retained records and project their scientific gate options."""

    @staticmethod
    def _scientific_uncertainties(plan, assessment):
        from .workbench_investigations import _copy

        rows = _copy(plan["context"]["dataset_summary"].get("unknowns", []))
        seen = {
            (row.get("field"), row.get("reason", row.get("impact"))) for row in rows
        }

        def add(field, reason):
            if (
                not isinstance(reason, str)
                or not reason.strip()
                or (field, reason) in seen
            ):
                return
            seen.add((field, reason))
            rows.append(
                {
                    "field": field,
                    "reason": reason,
                    "source": "verified_comparison_assessment",
                }
            )

        for reason in assessment.get("limitations", []):
            add("comparison_evidence", reason)
        for candidate in assessment.get("candidates", []):
            for reason in candidate.get("reasons", []):
                add(
                    "comparison_evidence",
                    str(candidate.get("candidate", "Recorded candidate"))
                    + ": "
                    + reason,
                )
        if assessment.get("independent_scientific_validation") is False:
            add(
                "independent_scientific_validation",
                "Independent scientific validation is not established by these "
                "execution and comparison checks.",
            )
        return rows

    def _response(self, record):
        from .workbench_investigations import MAX_ANCESTORS, MAX_TURNS, _copy

        ancestors, seen, current = [], {record["id"]}, record
        while current.get("parent_investigation"):
            parent = current["parent_investigation"]
            if parent in seen or len(ancestors) >= MAX_ANCESTORS:
                raise ValueError(
                    "Investigation ancestry is cyclic or exceeds the thread budget"
                )
            current = self._load(parent)
            if current["thread_id"] != record["thread_id"]:
                raise ValueError(
                    "Investigation ancestry has a mismatched thread identity"
                )
            seen.add(parent)
            ancestors.append(current)
        turns = [
            turn for item in reversed(ancestors) for turn in item["turns"]
        ] + record["turns"]
        if len(turns) > MAX_TURNS:
            raise ValueError("Combined thread exceeds its retained turn budget")
        return _copy(
            {
                **record,
                "thread_turns": turns,
                "synthesis_continuation": self._synthesis_descriptor(record),
            }
        )

    def _scientific_cursor(self, scientific, session_id):
        """Observe and validate an existing graph; never initialize or resume it."""
        from devbench.engine.workflow_operations import WorkflowOperationLedger

        from .workbench_adaptive import adaptive_cursor
        from .workflow_actions import WorkflowActions
        from .workflow_entrypoints import CHECKPOINT_NAME, LEDGER_NAME
        from .workflow_state import validate_workflow_state

        adaptive = adaptive_cursor(scientific, session_id)
        if adaptive is not None:
            return adaptive
        checkpoint, ledger_path = (
            scientific.root / name for name in (CHECKPOINT_NAME, LEDGER_NAME)
        )
        if not checkpoint.exists() or not ledger_path.exists():
            return None
        if checkpoint.is_symlink() or ledger_path.is_symlink():
            raise ValueError("Scientific gate state cannot be a symlink")
        from .langgraph_workflow import SerialRNAWorkflow

        planner = scientific.planner()
        ledger = WorkflowOperationLedger(ledger_path, readonly=True)
        actions = WorkflowActions(
            planner,
            ledger,
            actor="investigation-gate-projection",
            selector=scientific.selector,
            scientific_workflow=scientific.scientific_workflow,
        )
        with SerialRNAWorkflow(actions, checkpoint, stepwise=True) as workflow:
            snapshot = workflow.graph.get_state(workflow._config(session_id))
            if not snapshot.values:
                return None
            state = validate_workflow_state(snapshot.values)
            actions.check(state)
            following = list(snapshot.next)
            if len(following) == 1 and following[0] in {"review", "execution_review"}:
                actions.check_node_entry(following[0], state)
            return {
                "next": following,
                "phase": state["phase"],
                "status": state["status"],
                "trial_ids": state["trial_ids"],
                "trial_index": state["trial_index"],
                "failure": state.get("failure"),
            }

    def _project_scientific_plan(self, record, catalog=None):
        from .workbench_investigations import _copy

        scientific = getattr(self.app, "scientific", None)
        session_id = record.get("scientific_session_id")
        if scientific is None or session_id is None:
            return
        catalog = catalog or scientific.catalog()
        plan = scientific._plan(session_id)
        keys = (
            "session_id",
            "context",
            "scientific_candidates",
            "candidates",
            "trial_designs",
            "experiments",
            "recommendation",
            "scientific_reviews",
            "scientific_trial_definitions",
            "user_parameter_requirements",
            "evaluation_purpose",
            "limits",
            "preliminary_explanation",
        )
        record["scientific_plan"] = {
            key: _copy(plan[key]) for key in keys if key in plan
        }
        pending = any(
            row.get("operation_id") in record["scientific_operations"]
            and row.get("status") in {"queued", "running"}
            for row in catalog.get("operations", [])
        )
        cursor, errors = None, []
        if pending:
            errors.append("A recorded scientific operation is still active.")
        else:
            try:
                cursor = self._scientific_cursor(scientific, session_id)
            except (ValueError, OSError, RuntimeError) as error:
                errors.append(str(error)[:2000])
        if cursor and cursor.get("mode") == "adaptive/1":
            record.pop("gate", None)
            record.pop("required_action", None)
            record["status"] = "adaptive_workflow"
            record["workflow_mode"] = cursor["mode"]
            record["clarification_fields"] = []
            record["gate_options"] = {
                "session_id": session_id,
                "controller": cursor,
                "can_submit": False,
                "gate": None,
                "proposals": [],
                "declared_trials": [],
            }
            return
        self._project_review_gates(record, plan, catalog, cursor, errors, pending)

    def _declared_trial_options(self, declared, next_trial, plan, options, valid_gate):
        from .workbench_investigations import _copy

        gate, errors = options["gate"], options["blocked_reasons"]
        rows = []
        for trial in declared:
            reasons = list(errors)
            if gate != "execution_review" or trial["id"] != next_trial:
                reasons.append(
                    "Execution requires the next exact declared trial at its "
                    "execution-review gate."
                )
            if trial.get("status") != "declared":
                reasons.append("This trial is not a fresh declared execution.")
            if not self.app.allow_data_execution:
                reasons.append("Data execution is disabled for this service.")
            rows.append(
                {
                    "experiment_id": trial["id"],
                    "status": trial.get("status"),
                    "spec": _copy(trial),
                    "analysis_ref": plan.get("scientific_trial_definitions", {}).get(
                        trial["id"]
                    ),
                    "eligible": valid_gate and not reasons,
                    "blocked_reasons": reasons,
                }
            )
        return rows

    def _project_review_gates(self, record, plan, catalog, cursor, errors, pending):
        from .workbench_investigations import SCIENTIFIC_CONFIRMATIONS, _copy

        session_id = record["scientific_session_id"]
        gate = (
            cursor["next"][0]
            if cursor
            and len(cursor["next"]) == 1
            and cursor["next"][0] in {"review", "execution_review"}
            else None
        )
        valid_gate = bool(
            gate and cursor["status"] in {"ready", "review_required"} and not errors
        )
        if gate:
            record["gate"] = gate
        if not valid_gate and not errors:
            errors.append(
                "The verified scientific controller is not at an operator review gate."
            )
        designs = plan.get("trial_designs", [])
        proposals = designs[-1].get("proposals", []) if designs else []
        declared = plan.get("experiments", [])
        options = {
            "gate": gate,
            "session_id": session_id,
            "workspace_revision": catalog["revision"],
            "controller": cursor,
            "data_execution_allowed": self.app.allow_data_execution,
            "startup_data_execution_allowed": self.app.allow_data_execution,
            "required_confirmations": [
                {"key": key, "label": value}
                for key, value in SCIENTIFIC_CONFIRMATIONS.items()
            ]
            if gate == "review"
            else [],
            "review_evidence": {
                "workflow_steps": [
                    step
                    for step in catalog.get("workflow_steps", [])
                    if step.get("session_id") == session_id
                ],
                "scientific_reviews": plan.get("scientific_reviews", []),
            },
            "proposals": [],
            "declared_trials": [],
            "blocked_reasons": errors,
            "can_submit": False,
        }
        for spec in proposals:
            reasons = list(errors)
            if gate != "review":
                reasons.append(
                    "Trial declaration requires the verified planning review gate."
                )
            if any(row["id"] == spec["id"] for row in declared):
                reasons.append("This exact trial has already been declared.")
            options["proposals"].append(
                {
                    "draft_id": spec["id"],
                    "spec": _copy(spec),
                    "eligible": valid_gate and not reasons,
                    "blocked_reasons": reasons,
                    "diagnostics": {
                        "review_scope": "Existing graph binding and predecessor "
                        "scientific-review receipts",
                        "resource_limits": spec.get("resource_limits"),
                        "criteria": spec.get("metrics", []),
                        "metric_sources": spec.get("metric_sources", {}),
                        "subset_validity": spec.get("subset_validity"),
                    },
                }
            )
        next_trial = (
            cursor["trial_ids"][cursor["trial_index"]]
            if cursor and cursor["trial_index"] < len(cursor["trial_ids"])
            else None
        )
        options["declared_trials"] = self._declared_trial_options(
            declared, next_trial, plan, options, valid_gate
        )
        rows = (
            options["proposals"]
            if gate == "review"
            else options["declared_trials"]
            if gate == "execution_review"
            else []
        )
        options["can_submit"] = any(row["eligible"] for row in rows)
        if valid_gate and not options["can_submit"]:
            options["blocked_reasons"].append(
                "No eligible frozen trial is available at this gate."
            )
        record["gate_options"] = _copy(options)
        if valid_gate and not pending:
            record["status"] = (
                "awaiting_operator_review" if gate == "review" else "approved"
            )


def _investigation_turn_events(turns, phase):
    raw_events = []
    for turn in turns:
        kind = turn.get("kind")
        content = turn.get("content") or {}
        if kind == "lifecycle":
            raw_events.append(
                {
                    "type": "tool",
                    "phase": "Exploring",
                    "label": "Inspecting dataset metadata",
                    "status": content.get("state", "completed"),
                    "at": turn.get("at"),
                }
            )
        elif kind == "analyst_action":
            action = content.get("action") or {}
            tool_name = action.get("tool") or action.get("kind") or "action"
            dur = (content.get("evidence") or {}).get("duration_seconds")
            raw_events.append(
                {
                    "type": "tool",
                    "phase": phase,
                    "label": f"Ran {tool_name}",
                    "tool": tool_name,
                    "duration_s": dur,
                    "status": content.get("status", "completed"),
                    "inputs": action.get("options") or action.get("decision"),
                    "at": turn.get("at"),
                }
            )
        elif kind == "singlecell_evidence":
            tool_name = content.get("tool_name", "analysis")
            raw_events.append(
                {
                    "type": "tool",
                    "phase": phase,
                    "label": f"Ran {tool_name}",
                    "tool": tool_name,
                    "status": content.get("status", "completed"),
                    "at": turn.get("at"),
                }
            )
        elif kind == "scientific_step":
            step = content.get("step") or {}
            node = step.get("node") or "step"
            raw_events.append(
                {
                    "type": "tool",
                    "phase": phase,
                    "label": f"Ran {node}",
                    "tool": node,
                    "status": step.get("status", "completed"),
                    "at": turn.get("at"),
                }
            )
        elif kind == "job_event":
            to_state = content.get("to") or content.get("status")
            if to_state in {"cancelled", "cancel_requested"}:
                raw_events.append(
                    {
                        "type": "canceled",
                        "phase": phase,
                        "label": f"Canceled {turn.get('title', 'analysis')}",
                        "status": "cancelled",
                        "reason": content.get("reason", "Canceled by operator"),
                        "at": turn.get("at"),
                    }
                )
            else:
                raw_events.append(
                    {
                        "type": "task_action",
                        "phase": phase,
                        "label": f"Native analysis: {to_state}",
                        "status": to_state,
                        "at": turn.get("at"),
                    }
                )

    return raw_events


def _investigation_event_page(record, since):
    phase = "Exploring"
    status = record.get("status", "unknown")
    if status in {"planning", "awaiting_operator_review"}:
        phase = "Planning"
    elif status in {"preparing", "running"}:
        phase = "Running pipeline"
    elif status.startswith("completed") or record.get("synthesis"):
        phase = "Synthesizing"

    raw_events = _investigation_turn_events(record.get("turns", []), phase)
    if status == "cancelled":
        raw_events.append(
            {
                "type": "canceled",
                "phase": phase,
                "label": "Canceled analysis operation",
                "status": "cancelled",
                "at": record.get("updated_at"),
            }
        )

    numbered = []
    for i, ev in enumerate(raw_events, 1):
        ev["sequence"] = i
        if i > since:
            numbered.append(ev)

    terminal = status in {
        "completed",
        "completed_review_passed",
        "completed_review_failed",
        "failed",
        "cancelled",
        "operation_failed",
    }
    return {
        "format": "devbench.investigation-events/1",
        "events": numbered,
        "phase": phase,
        "status": status,
        "terminal": terminal,
        "total_events": len(raw_events),
    }
