"""Bounded current investigation projection from one scientific WAL snapshot."""

from __future__ import annotations

import json

from devbench.engine.partition_authority import embargoed
from devbench.platform.plugin_jobs import digest

from .context import PUBLIC


def opaque_id(value):
    return "id-" + digest(value)[:32] if value is not None else None


def aims(db, sid, private=False):
    result = []
    for row in db.execute(
        "SELECT aim_id,version,status,body FROM mcp_aims WHERE session_id=? "
        "ORDER BY rowid DESC LIMIT 40",
        (sid,),
    ):
        if private:
            result.append({"aim_id": opaque_id(row["aim_id"]), "status": row["status"]})
        else:
            result.append(
                {
                    "aim_id": row["aim_id"],
                    "version": row["version"],
                    "status": row["status"],
                    "definition": json.loads(row["body"]),
                }
            )
    return result


def _artifacts(ctx, db, sid, private, limit):
    result = []
    for row in db.execute(
        "SELECT * FROM mcp_artifacts WHERE session_id=? ORDER BY rowid DESC LIMIT ?",
        (sid, limit),
    ):
        ref = json.loads(row["ref_json"])
        blocked = embargoed(db, ref["sha256"])
        visible = not private and not blocked
        if visible:
            ctx.check_source(db, sid, ref["sha256"])
        result.append(
            {
                "artifact": ref if visible else None,
                "opaque_artifact": None
                if visible
                else {k: ref[k] for k in ("artifact_id", "sha256")},
                "status": "embargoed" if blocked else row["status"],
                "producer_step_id": row["producer_step_id"],
            }
        )
    return result


def _plan_holds(ctx, db, sid, private, limit):
    from devbench.engine.workflow_operations import OperationBlocked

    from .approval import active_policy_digest, require_governance

    if (
        not limit
        or not db.execute(
            "SELECT 1 FROM sqlite_master WHERE name='mcp_frozen_plans'"
        ).fetchone()
    ):
        return []
    held = []
    rows = db.execute(
        "SELECT plan_json,plan_sha256 FROM mcp_frozen_plans p WHERE session_id=? "
        "AND NOT EXISTS (SELECT 1 FROM scientific_daemon_runs r WHERE "
        "r.session_id=p.session_id AND r.plan_id=p.plan_id) "
        "ORDER BY p.rowid DESC LIMIT ?",
        (sid, limit),
    )
    for raw, sha in rows:
        plan = json.loads(raw)
        try:
            require_governance(
                ctx.authority,
                plan,
                sha,
                private,
                offline_only=ctx.offline_only,
                policy_sha=active_policy_digest(
                    ctx.workspace, getattr(ctx.supervisor, "policy_sha256", None)
                ),
                gate1=plan["epistemic_purpose"] == "confirmatory",
                db=db,
            )
        except OperationBlocked as error:
            item = {"hold_id": opaque_id(plan["plan_id"]), "status": "pending"}
            if not private:
                item.update(plan_id=plan["plan_id"], reason=error.state)
            held.append(item)
    return held


def list_investigation_state(ctx, args):
    sid = args["session_id"]

    def snapshot(db):
        state = ctx.session(sid, db)
        private = ctx.protected(sid, db=db)
        branches = []
        for row in db.execute(
            "SELECT branch_id,branch_kind,status,parent_branch_id,aim_id "
            "FROM mcp_branches "
            "WHERE session_id=? AND (status!='retired' OR ?) "
            "ORDER BY rowid DESC LIMIT 64",
            (sid, args.get("include_retired_branches", False)),
        ):
            item = dict(row)
            if private:
                item = {
                    k: opaque_id(v) if k.endswith("_id") else v for k, v in item.items()
                }
            branches.append(item)
        runs = []
        for row in db.execute(
            "SELECT run_id,plan_id,status FROM scientific_daemon_runs "
            "WHERE session_id=? "
            "ORDER BY started_at DESC LIMIT ?",
            (sid, args.get("run_limit", 20)),
        ):
            item = dict(row)
            if not private:
                plan = ctx.jobs.plan(sid, item["plan_id"])
                item["lane"] = plan.get("execution_lane", "tier1_curated")
                item["claim_tier"] = (
                    "L1_execution_verified" if item["status"] == "completed" else "none"
                )
            runs.append(item)
        holds = [
            dict(row)
            for row in db.execute(
                "SELECT hold_id,status FROM mcp_holds WHERE session_id=? "
                "AND status='pending' "
                "ORDER BY rowid DESC LIMIT 32",
                (sid,),
            )
        ]
        if private:
            holds = [{**row, "hold_id": opaque_id(row["hold_id"])} for row in holds]
        holds.extend(_plan_holds(ctx, db, sid, private, 32 - len(holds)))
        classification = state["classification"]
        return {
            "session_id": sid,
            "revision": state["revision"],
            "epistemic_state": state["epistemic_state"],
            "data_classification": "private"
            if private and classification in PUBLIC
            else classification,
            "redacted": private,
            "aims": aims(db, sid, private),
            "branches": branches,
            "artifacts": _artifacts(
                ctx, db, sid, private, args.get("artifact_limit", 50)
            ),
            "runs": runs,
            "pending_holds": holds,
            **ctx.handoff(sid, private),
        }

    return ctx.authority.daemon_read(snapshot)
