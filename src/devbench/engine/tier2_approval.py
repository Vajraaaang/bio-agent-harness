"""Bind script-specific local operator approval to the exact scientific inputs."""

from __future__ import annotations

from devbench.engine.exploratory_images import exploratory_image
from devbench.engine.workflow_operations import OperationBlocked
from devbench.platform.plugin_jobs import digest


def script_scope(envelope, contract):
    step = envelope["step"]
    return {
        "session_id": envelope["lineage_id"],
        "step_id": step["step_id"],
        "branch_id": step["branch_id"],
        "purpose": step["purpose"],
        "method_version_id": step["oci_method_version_id"],
        "script_sha256": contract["implementation"]["source_sha256"],
        "image_id": exploratory_image(contract["environment"]["image"]).image_id,
        "environment_sha256": step["environment_sha256"],
        "parameters_sha256": step["parameters_sha256"],
        "limits": contract["execution"],
        "outputs": contract["outputs"],
        "inputs": envelope["pins"],
    }


def _document(scope, classification):
    fields = {
        **scope,
        "data_classification": classification,
        "clinical_release_allowed": False,
        "format": "devbench.governed-script-approval/1",
    }
    return {**fields, "plan_id": "tier2-" + digest(fields)}


def approve(
    backend, flow, step_id, *, actor_id, data_classification, surface, auth_method
):
    if flow.ledger.path != backend.ledger.path or data_classification not in {
        "public",
        "synthetic",
    }:
        raise PermissionError(
            "Direct exploratory approval requires a declared public or synthetic graph"
        )
    if backend.private:
        raise PermissionError("Private script approval requires offline Studio")
    from devbench.platform.adaptive_workflow import AdaptiveStep

    step = (
        step_id
        if isinstance(step_id, AdaptiveStep)
        else flow._read_projection(lambda db: flow._state(db).nodes[step_id])
    )
    envelope = flow.candidate_envelope(step, actor=actor_id)
    _, method, _ = backend.validate(envelope)
    if step.held_out:
        raise PermissionError("Protected evaluation has its own sealed authority")
    scope = script_scope(envelope, method["contract"])
    plan = _document(scope, data_classification)
    return backend.ledger.scientific.record_plan_approval(
        session_id=plan["session_id"],
        plan_id=plan["plan_id"],
        plan_sha256=digest(plan),
        frozen_plan=plan,
        actor_id=actor_id,
        surface=surface,
        auth_method=auth_method,
        rationale="Approved script SHA-256 " + scope["script_sha256"],
    )


def require_approval(backend, envelope, contract):
    if envelope["step"].get("held_out"):
        # validate() already admitted the exact sealed evaluator and the
        # partition authority verifies its one-time entitlement at dispatch.
        return
    scope = script_scope(envelope, contract)
    authority = backend.ledger.scientific
    if backend.plugin_plan is not None:
        plan = backend.plugin_plan
        if (
            plan.get("operation") != "registered_exploratory"
            or scope["step_id"] != "plugin-" + plan["plan_id"]
            or plan["registered_method"].get("script_scope") != scope
        ):
            raise OperationBlocked("exploratory_script_approval_changed")
        if not authority.plan_approval(
            scope["session_id"], plan["plan_id"], digest(plan)
        ):
            raise OperationBlocked("explicit_script_plan_approval_required")
        return
    if not backend.private:
        for classification in ("synthetic", "public"):
            plan = _document(scope, classification)
            if authority.plan_approval(
                scope["session_id"], plan["plan_id"], digest(plan)
            ):
                return
    raise OperationBlocked("explicit_script_plan_approval_required")
