"""Local Studio approval and receipt readback for private Plugin graph steps."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from devbench.analysis.evidence_access import EvidenceBroker
from devbench.engine.workflow_operations import OperationBlocked

from .plugin_jobs import ACTIVE, JobStore, _orphaned_worker, _worker_alive
from .plugin_rna_solvers import LOCAL_OPERATIONS

_ID = re.compile(r"[0-9a-f]{32}")
_SHA = re.compile(r"[0-9a-f]{64}")
_ERROR_CODE = re.compile(r"[A-Za-z0-9_.:-]{1,128}")
_OPERATIONS = tuple(
    sorted({"motor_program_comparison", "shared_motor_umap", *LOCAL_OPERATIONS})
)
_PLAN_LIMIT = 20
_RESULT_LIMIT = 64 * 1024


def _supports(plan: dict[str, Any]) -> bool:
    return (
        plan.get("format") == "devbench.plugin-plan/1"
        and plan.get("data_classification") == "private"
        and plan.get("operation") in _OPERATIONS
        and type(plan.get("graph_runtime")) is dict
    )


def _check_scope(workspace: Path, plan: dict[str, Any]) -> None:
    from .plugin_mcp import _ledger, _lineage, _roots

    ledger = _ledger(workspace)
    broker = EvidenceBroker.open_existing(ledger.scientific)
    lineage = _lineage(plan["session_id"], plan)
    roots = _roots(plan)

    def check(db):
        context, policy = broker._context_policy(db, lineage["context_id"])
        source = broker._source(db, lineage["source_id"])
        if (
            context["lineage_id"] != plan["session_id"]
            or context["role"] != "scientist"
            or source["classification"] != "structural"
            or source["content_sha256"] != lineage["intake_sha256"]
            or set(roots) != set(source["roots"])
            or policy.allow_outcomes
        ):
            raise PermissionError(
                "The Plugin plan is outside its private session scope"
            )
        broker._check(context, policy, source, set())
        broker._query_dependencies(db, roots)

    ledger._read(check)


def _require_ready(workspace: Path, plan: dict[str, Any]) -> None:
    from .plugin_graph import registered_plugin_runtime, require_plugin_dependencies

    if registered_plugin_runtime(plan) != plan["graph_runtime"]:
        raise OperationBlocked("plugin_graph_runtime_changed")
    require_plugin_dependencies(workspace, plan)


def _error_code(value: Any, default: str) -> str:
    return value if isinstance(value, str) and _ERROR_CODE.fullmatch(value) else default


def _verified_result(workspace, plan, run):
    from .plugin_graph import verify_registered_plugin_result
    from .plugin_jobs import canonical

    receipt = verify_registered_plugin_result(
        workspace,
        run["run_id"],
        plan,
        json.loads(run["receipt_json"]),
    )
    result = {"graph_execution": receipt["graph_execution"]}
    for key in ("metrics", "summary_tables", "limitations"):
        if key in receipt:
            result[key] = receipt[key]
    if len(canonical(result)) > _RESULT_LIMIT:
        raise OperationBlocked("private_plugin_receipt_byte_budget_exceeded")
    return result


def _interrupted(workspace, run):
    stage = json.loads(run["stage_json"])
    created = stage.get("process_created_at")
    if stage.get("daemon_managed"):
        # Queued work has no PID. A live supervisor owns the exit/cleanup window.
        return run["status"] == "running" and _orphaned_worker(
            workspace, run["pid"], created
        )
    return not _worker_alive(run["pid"], created)


def _run_view(workspace, plan, plan_sha, run):
    if (
        run["session_id"] != plan["session_id"]
        or run["plan_id"] != plan["plan_id"]
        or run["plan_sha256"] != plan_sha
        or _ID.fullmatch(run["run_id"]) is None
    ):
        raise OperationBlocked("plan_identity_mismatch")
    view = {"run_id": run["run_id"], "status": run["status"]}
    if run["error_text"]:
        view["error"] = _error_code(run["error_text"], "plugin_worker_failed")
    if run["status"] in ACTIVE and _interrupted(workspace, run):
        view.update(status="interrupted", error="worker_not_running")
    if run["status"] == "completed":
        try:
            result = _verified_result(workspace, plan, run)
        except (OperationBlocked, OSError, ValueError, KeyError, TypeError):
            view.update(status="unavailable", error="verified_receipt_unavailable")
        else:
            view.update(result)
    return view


def _plan_metadata(plan):
    from .workbench_adaptive import _plan_parameters

    parameters = plan.get("design", {}).get("parameters", {})
    return {
        "inputs": [
            {"name": Path(profile["source"]).name, "role": profile["role"]}
            for profile in plan.get("profiles", ())
        ],
        "parameters": _plan_parameters(parameters),
    }


def _plan_view(app, plan, plan_sha, run):
    view = {
        "plan_id": plan["plan_id"],
        "plan_sha256": plan_sha,
        "operation": plan["operation"],
        "status": "awaiting_review",
        "can_run": False,
        **_plan_metadata(plan),
    }
    if isinstance(plan.get("objective"), str):
        view["objective"] = plan["objective"]
    if plan.get("dependencies"):
        view["dependencies"] = plan["dependencies"]
    if run is not None:
        return {**view, **_run_view(app.workspace, plan, plan_sha, run)}
    try:
        _require_ready(app.workspace, plan)
    except OperationBlocked as error:
        code = _error_code(error.state, "plugin_graph_blocked")
        view.update(status="blocked", error=code)
    else:
        view["can_run"] = app.allow_data_execution is True
    return view


def studio_view(app, session_id: str) -> dict[str, Any] | None:
    """Read only committed, current private plan results through offline Studio."""
    from .plugin_mcp import studio_job_readback

    if app.offline_only is not True:
        return None
    views = []
    with studio_job_readback(app.workspace) as db:
        if db is None:
            return None
        plans = db.execute(
            "SELECT * FROM plugin_plans WHERE session_id=? "
            "AND json_extract(plan_json, '$.data_classification')='private' "
            "AND json_type(plan_json, '$.graph_runtime')='object' "
            "AND json_extract(plan_json, '$.operation') "
            "IN (SELECT value FROM json_each(?)) "
            "ORDER BY created_at DESC, rowid DESC LIMIT ?",
            (session_id, json.dumps(_OPERATIONS), _PLAN_LIMIT),
        ).fetchall()
        for row in reversed(plans):
            plan = JobStore._plan(row)
            if not _supports(plan):
                continue
            _check_scope(app.workspace, plan)
            run = db.execute(
                "SELECT * FROM plugin_runs WHERE session_id=? AND plan_id=?",
                (session_id, row["plan_id"]),
            ).fetchone()
            views.append(_plan_view(app, plan, row["plan_sha256"], run))
    return {"plans": views} if views else None


def studio_launch(app, session_id: str, body: dict[str, Any]) -> dict[str, Any]:
    """Approve exactly one frozen private graph plan in the same workspace."""
    from .plugin_mcp import studio_job_readback

    if app.offline_only is not True or app.allow_data_execution is not True:
        raise PermissionError("Private graph steps require offline Studio execution")
    if (
        type(body) is not dict
        or body.keys() != {"plan_id", "plan_sha256", "allow_data_execution"}
        or body["allow_data_execution"] is not True
        or not isinstance(body["plan_id"], str)
        or re.fullmatch(r"(?:cp-)?[0-9a-f]{32}", body["plan_id"]) is None
        or not isinstance(body["plan_sha256"], str)
        or _SHA.fullmatch(body["plan_sha256"]) is None
    ):
        raise PermissionError("Review and authorize the exact private graph plan")
    plan_id, plan_sha = body["plan_id"], body["plan_sha256"]
    with studio_job_readback(app.workspace) as db:
        row = (
            None
            if db is None
            else db.execute(
                "SELECT * FROM plugin_plans WHERE session_id=? AND plan_id=?",
                (session_id, plan_id),
            ).fetchone()
        )
        plan = JobStore._plan(row, plan_id, plan_sha)
    if plan.get("format") == "devbench.control-plan/1":
        run = JobStore(app.workspace).launch(session_id, plan_id, plan_sha)
        return {k: run[k] for k in ("session_id", "plan_id", "run_id", "status")}
    if not _supports(plan):
        raise OperationBlocked("private_graph_plan_unavailable")
    _check_scope(app.workspace, plan)
    _require_ready(app.workspace, plan)
    run = JobStore(app.workspace).launch(session_id, plan_id, plan_sha)
    return _plan_view(app, plan, plan_sha, run)
