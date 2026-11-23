"""Single-operator Codex campaign over the protected CLI capture transport."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import TYPE_CHECKING

from devbench.engine.workflow_operations import LOCAL_INTERACTIVE_CODEX

from .episode_codex import CodexActionSelector, _canonical
from .rna_investigation_backend import AuditedInvestigationBackend, _digest

if TYPE_CHECKING:
    from devbench.engine.workflow_operations import WorkflowOperationLedger
    from devbench.platform.method_planner import ScientificMethodPlanner

    from .episode_agent import CampaignSelectionInvoker


def _select_fields(record, keys):
    return {key: record[key] for key in keys if key in record}


def local_controller_evidence(context):
    fields = (
        "version_id",
        "sha256",
        "kind",
        "representation",
        "biological_unit",
        "unit_membership_sha256",
    )
    return {
        identity: {
            "artifact": _select_fields(entry["artifact"], fields),
            "content_status": "admitted_pipeline_input",
        }
        for identity, entry in context["evidence"].items()
        if entry.get("content_status") == "admitted_pipeline_input"
    }


def local_controller_payload(context):
    """Project only the question, inspected metadata and structural frontier."""
    summary = context["dataset_summary"]
    graph = context["graph"]
    inputs = local_controller_evidence(context)
    inspection = summary["observed"].get("scrna_input") or {}
    declarations = inspection.get("declarations") or {}
    unit = declarations.get("biological_independence") or {}
    existing_steps = {step["step_id"] for step in graph["steps"]}
    return {
        "decision_kind": "adaptive_controller",
        "task": _select_fields(
            context["task"], ("scientific_objective", "computational_task")
        ),
        "dataset_summary": {
            "observed": _select_fields(
                summary["observed"],
                (
                    "file_type",
                    "header_only",
                    "header_status",
                    "column_names_unique",
                    "expression_values_read",
                    "columns",
                    "obs_columns",
                    "var_columns",
                    "layers",
                    "obsm_keys",
                    "cells",
                    "genes",
                    "rows",
                ),
            ),
            "unknowns": summary["unknowns"],
        },
        "declared_context": {
            **_select_fields(
                declarations,
                ("assay", "measurement", "counts_layer", "capture_unit_key",
                 "capture_units_confirmed"),
            ),
            "biological_unit": _select_fields(unit, ("unit_column", "independent")),
        },
        "graph": {
            "mode": graph["mode"],
            "steps": [
                _select_fields(
                    step,
                    (
                        "step_id",
                        "branch_id",
                        "generation",
                        "operation_id",
                        "purpose",
                        "status",
                    ),
                )
                for step in graph["steps"]
            ],
            "sweep_members": [
                _select_fields(member, ("sweep_id", "step_id", "status"))
                for member in graph["sweep_members"]
            ],
        },
        "ready": context["ready"],
        "action_catalog": {
            identity: _select_fields(
                action, ("step_id", "operation_id", "purpose", "parameters_sha256")
            )
            for identity, action in context.get("action_catalog", {}).items()
            if action.get("step_id") not in existing_steps
        },
        "pipeline_inputs": [entry["artifact"] for entry in inputs.values()],
        "evidence": inputs,
        **_select_fields(
            context,
            ("decisions_remaining", "termination_required", "selection_feedback"),
        ),
        "selection_rules": (
            "Single-operator session. Evaluate the question before choosing an "
            "action. For a useful unqueued, registered diagnostic, use insert with "
            "its catalog action_id and explain why it addresses the question. "
            "For a matching ready step, use dispatch with that exact step_id on a "
            "later separately authorized action. "
            "If the question needs missing data or review, use stop with precise "
            "unresolved items. Independent review is unavailable in this session."
        ),
    }


class LocalInteractiveCodexBackend(AuditedInvestigationBackend):
    """One coordinator-controlled CLI role with post-dispatch usage accounting."""

    def __init__(self, methods, selector, *, data_scope, allow_model_execution, actor):
        from .episode_agent import AgentLimits

        if allow_model_execution is not True:
            raise PermissionError(
                "Local interactive Codex requires current model authorization"
            )
        if data_scope not in {"synthetic", "public"}:
            raise PermissionError(
                "Local interactive Codex requires an exact public or synthetic scope"
            )
        if type(actor) is not str or not actor.strip() or len(actor) > 128:
            raise ValueError(
                "Local interactive Codex requires a bounded operator label"
            )
        super().__init__(methods, selector)
        self.data_scope = data_scope
        self.actor = actor
        self._require_selector()
        self.limits = AgentLimits(
            max_selections=24,
            timeout_seconds=3600,
            memory_mb=selector.memory_mb,
            max_context_bytes=min(128_000, selector.max_context_bytes or 128_000),
        )

    def _require_selector(self):
        selector = self.selector
        if (
            type(selector) is not CodexActionSelector
            or selector.private_root != self.methods.root
            or selector.workspace_parent.is_relative_to(self.methods.root)
            or self.methods.root.is_relative_to(selector.workspace_parent)
            or selector.protected_model_gate is not None
            or selector.trace_sidecar is not None
            or self.trace_observer is not None
        ):
            raise PermissionError(
                "Use the current protected single-operator Codex selector"
            )

    @property
    def _allocation(self):
        return {
            "tokens": 32_768,
            "calls": 1,
            "seconds": self.limits.session_timeout_seconds,
            "context_bytes": self.limits.max_context_bytes,
            "memory_mb": self.limits.memory_mb,
        }

    @property
    def policy(self):
        allocation = self._allocation
        return {
            "format": "devbench.model-budget-policy/1",
            "kind": LOCAL_INTERACTIVE_CODEX,
            "max_total_tokens": None,
            "soft_token_target": self.limits.max_selections * allocation["tokens"],
            "max_calls": self.limits.max_selections,
            "max_total_seconds": self.limits.timeout_seconds,
            "max_context_bytes": self.limits.max_context_bytes,
            "memory_mb": self.limits.memory_mb,
            "max_children": 0,
            "reconciliation": {
                key: allocation[key]
                for key in ("tokens", "calls", "seconds", "context_bytes")
            },
        }

    def capabilities(self):
        value = super().capabilities()
        source = _canonical(Path(__file__))
        source_hashes = {
            **value["source_sha256"],
            str(source): hashlib.sha256(source.read_bytes()).hexdigest(),
        }
        origins = {**value["source_module_origins"], __name__: str(source)}
        source_digest = _digest(
            {
                "module_origins": origins,
                "file_sha256": source_hashes,
            }
        )
        value.update(
            backend=LOCAL_INTERACTIVE_CODEX,
            backend_digest=_digest(
                {
                    "adapter": LOCAL_INTERACTIVE_CODEX,
                    "transport_digest": value["backend_digest"],
                    "source_digest": source_digest,
                    "actor_workspace_parent": str(self.selector.workspace_parent),
                    "selector_context_bound": self.selector.max_context_bytes,
                    "policy": self.policy,
                    "data_scope": self.data_scope,
                }
            ),
            scope=LOCAL_INTERACTIVE_CODEX,
            identity="single_operator_interactive",
            capability_status="single_operator_interactive_not_multiagent_qualified",
            max_concurrent_calls=1,
            source_digest=source_digest,
            source_sha256=source_hashes,
            source_module_origins=origins,
            private_model_evidence_allowed=False,
        )
        value.pop("capability_digest")
        value["capability_digest"] = _digest(value)
        return value

    def require_admission(self, policy):
        if policy != LOCAL_INTERACTIVE_CODEX:
            raise PermissionError("blocked_backend_capability:" + policy)
        self._require_selector()
        return self.capabilities()

    def campaign_identity(self, plan_hash):
        capability = self.capabilities()
        return {
            "expected_plan_hash": plan_hash,
            "source_digest": capability["source_digest"],
            "runtime_digest": capability["runtime_digest"],
            "data_scope": self.data_scope,
            "operator": self.actor,
            "parent_call_limits": {"default": self._allocation},
            "protected_parent_allocations": {"adaptive_termination": self._allocation},
        }

    def parent_selection_binding(self, selector, context, **identities):
        if (
            selector is not self.selector
            or context.get("decision_kind") != "adaptive_controller"
            or context.get("analyst_profile") is not None
        ):
            raise PermissionError(
                "Local interactive Codex admits only the single-operator controller"
            )
        return super().parent_selection_binding(selector, context, **identities)


def attach_local_interactive_codex(
    planner: ScientificMethodPlanner,
    ledger: WorkflowOperationLedger,
    plan: dict,
    selector: CodexActionSelector,
    *,
    data_scope: str,
    allow_model_execution: bool,
    actor: str,
) -> CampaignSelectionInvoker:
    """Open or reattach the exact single-operator campaign at the coordinator."""
    from devbench.engine.workflow_operations import WorkflowOperationLedger
    from devbench.platform.method_planner import ScientificMethodPlanner

    from .episode_agent import CampaignSelectionInvoker

    if (
        type(planner) is not ScientificMethodPlanner
        or type(ledger) is not WorkflowOperationLedger
        or ledger.readonly
    ):
        raise TypeError("Use the current planner and writable workflow ledger")
    session_id = plan["session_id"]
    campaign_id = "local-codex-" + session_id
    with ledger._operation_lock((campaign_id, "attach", 0)):
        current = planner.get(session_id)
        if current != plan:
            raise ValueError("Local interactive Codex requires the exact current plan")
        if current["data_classification"] != data_scope:
            raise PermissionError(
                "Model scope must match the exact plan classification"
            )
        backend = LocalInteractiveCodexBackend(
            planner.methods,
            selector,
            data_scope=data_scope,
            allow_model_execution=allow_model_execution,
            actor=actor,
        )
        capability = backend.require_admission(LOCAL_INTERACTIVE_CODEX)
        plan_hash = _digest(current)
        retained = current.get("model_campaign")
        if retained is None and (
            current["selection_history"] or current["selection_in_progress"]
        ):
            raise ValueError(
                "Attach the interactive campaign before the first selection"
            )
        original_hash = plan_hash
        if retained is not None:
            if retained["campaign_id"] != campaign_id or retained["ledger_path"] != str(
                ledger.path
            ):
                raise PermissionError(
                    "A different retained model campaign cannot be replaced"
                )
            campaign = ledger.get_campaign(campaign_id)
            if campaign is None:
                raise PermissionError(
                    "The retained local interactive campaign is unavailable"
                )
            original_hash = campaign["identity"]["expected_plan_hash"]
        ledger.open_campaign(
            campaign_id,
            session_id=session_id,
            policy=backend.policy,
            capability=capability,
            identity=backend.campaign_identity(original_hash),
            backend=backend,
        )
        planner.attach_model_campaign(
            session_id,
            ledger,
            campaign_id,
            backend,
            expected_plan_hash=plan_hash,
            actor=actor,
        )
    return CampaignSelectionInvoker(
        planner,
        session_id,
        limits=backend.limits,
        allow_model_execution=allow_model_execution,
    )
