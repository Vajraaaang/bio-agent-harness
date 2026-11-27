"""Revision-fenced research lifecycle, backed by the thesis and scientific ledgers."""

from __future__ import annotations

import hashlib
import json
from uuid import uuid4

from devbench.engine.workflow_operations import OperationBlocked
from devbench.platform.plugin_jobs import canonical, digest
from devbench.platform.thesis_aims import ThesisAimLifecycle
from devbench.platform.workbench_thesis_actions import _aim_action

from .context import PRIVATE
from .state import aims

_ACTOR = "mcp-scientist"
_AIM_ACTIONS = {
    "propose_aim",
    "revise_aim",
    "approve_aim",
    "assess_explanation",
    "record_outcome",
}


def _invalidate(db, sid, field, value):
    if not db.execute(
        "SELECT 1 FROM sqlite_master WHERE name='mcp_frozen_plans'"
    ).fetchone():
        return []
    # Extend the invalidated set through frozen predecessor identities.
    rows = db.execute(
        """WITH RECURSIVE stale(plan_id) AS (
           SELECT plan_id FROM mcp_frozen_plans WHERE session_id=?
           AND json_extract(plan_json,?)=? UNION
           SELECT p.plan_id FROM mcp_frozen_plans p,
                  json_each(p.plan_json,'$.depends_on') d
           JOIN stale s ON s.plan_id=json_extract(d.value,'$.plan_id')
           WHERE p.session_id=?)
           SELECT r.run_id FROM scientific_daemon_runs r JOIN stale USING(plan_id)
           WHERE r.session_id=?""",
        (sid, "$." + field, value, sid, sid),
    )
    runs = [r[0] for r in rows]
    db.executemany(
        "UPDATE mcp_artifacts SET status='stale' WHERE session_id=? "
        "AND producer_step_id=?",
        [(sid, rid) for rid in runs],
    )
    return runs


def _mutate_aim(ctx, args, db, lifecycle):
    sid, action = args["session_id"], args["action"]
    request = dict(args)
    identity = args.get("aim_id") or args.get("definition", {}).get("aim_id")
    if not identity or (
        "definition" in request and request["definition"].get("aim_id") != identity
    ):
        raise OperationBlocked("invalid_request")
    request.setdefault("rationale", "Recorded by the research MCP client.")
    prior = db.execute(
        "SELECT * FROM mcp_aims WHERE session_id=? AND aim_id=?", (sid, identity)
    ).fetchone()
    if prior:
        request["version"] = prior["version"]
        if args.get("aim_version_sha256") and args["aim_version_sha256"] != digest(
            json.loads(prior["body"])
        ):
            raise OperationBlocked("plan_identity_mismatch")
    _aim_action(
        lifecycle,
        action,
        request,
        _ACTOR,
        {"expected_revision": ctx.authority._revision(db, sid), "connection": db},
    )
    projection, _ = lifecycle._projection(db)
    invalid = []
    for key, aim in projection.aims.items():
        status = (
            "invalidated"
            if aim.invalidated_by
            else "outcome_recorded"
            if aim.outcome
            else "approved"
            if aim.approval
            else "proposed"
        )
        db.execute(
            """INSERT INTO mcp_aims VALUES (?,?,?,?,?) ON CONFLICT(session_id,aim_id)
            DO UPDATE SET version=excluded.version,
            status=excluded.status,body=excluded.body""",
            (
                sid,
                key,
                aim.version,
                status,
                canonical(aim.definition.model_dump(mode="json")).decode(),
            ),
        )
        if action == "revise_aim" and (key == identity or aim.invalidated_by):
            invalid.extend(_invalidate(db, sid, "aim_id", key))
    return {"invalidated_steps": sorted(set(invalid))}


def _branch(ctx, args, db):
    sid, key = args["session_id"], args["branch_id"]
    if args["action"] == "retire_branch":
        changed = db.execute(
            "UPDATE mcp_branches SET status='retired' WHERE session_id=? "
            "AND branch_id=? AND status='active'",
            (sid, key),
        ).rowcount
        if not changed:
            raise OperationBlocked("stale_revision_conflict")
        return {"invalidated_steps": _invalidate(db, sid, "branch_id", key)}
    parent = db.execute(
        "SELECT * FROM mcp_branches WHERE session_id=? AND branch_id=? "
        "AND status='active'",
        (sid, args["parent_branch_id"]),
    ).fetchone()
    if (
        parent is None
        or db.execute(
            "SELECT 1 FROM mcp_branches WHERE session_id=? AND branch_id=?", (sid, key)
        ).fetchone()
    ):
        raise OperationBlocked("stale_revision_conflict")
    aim = args.get("aim_id", parent["aim_id"])
    if (
        aim
        and not db.execute(
            "SELECT 1 FROM mcp_aims WHERE session_id=? AND aim_id=?", (sid, aim)
        ).fetchone()
    ):
        raise OperationBlocked("input_scope_violation")
    db.execute(
        "INSERT INTO mcp_branches VALUES (?,?,?,'active',?,?)",
        (sid, key, args["branch_kind"], parent["branch_id"], aim),
    )
    return {}


def _critic(args, db):
    sid = args["session_id"]
    if not db.execute(
        "SELECT 1 FROM mcp_aims WHERE session_id=? AND aim_id=?", (sid, args["aim_id"])
    ).fetchone():
        raise OperationBlocked("input_scope_violation")
    if pid := args.get("plan_id"):
        plan = db.execute(
            "SELECT plan_json FROM mcp_frozen_plans WHERE session_id=? AND plan_id=?",
            (sid, pid),
        ).fetchone()
        if plan is None or json.loads(plan[0]).get("aim_id") != args["aim_id"]:
            raise OperationBlocked("plan_identity_mismatch")
    review = uuid4().hex
    db.execute(
        "INSERT INTO mcp_holds VALUES (?,?,'pending',?)",
        (
            sid,
            review,
            canonical(
                {
                    "kind": "critic_review",
                    "gate": args.get("gate", 1),
                    "aim_id": args["aim_id"],
                    "plan_id": args.get("plan_id"),
                }
            ).decode(),
        ),
    )
    return {"critic_review_id": review, "status": "requested"}


def _waiver(ctx, args, db):
    from .approval import require_explicit_gate1

    sid, hold_id = args["session_id"], args["hold_id"]
    plan = ctx.jobs.plan(sid, args["plan_id"])
    if digest(plan) != args["plan_sha256"]:
        raise OperationBlocked("plan_identity_mismatch")
    subject = {
        "kind": "waiver",
        "plan_id": plan["plan_id"],
        "plan_sha256": args["plan_sha256"],
        "rationale_sha256": digest(args["rationale"]),
    }
    row = db.execute(
        "SELECT * FROM mcp_holds WHERE session_id=? AND hold_id=?", (sid, hold_id)
    ).fetchone()
    if row is None:
        db.execute(
            "INSERT INTO mcp_holds VALUES (?,?,'pending',?)",
            (sid, hold_id, canonical(subject).decode()),
        )
        return {"status": "awaiting_review"}
    if json.loads(row["body"]) != subject or row["status"] != "pending":
        raise OperationBlocked("plan_identity_mismatch")
    require_explicit_gate1(db, plan, args["plan_sha256"])
    # The operator signs the specific waiver rationale in addition to the
    # blinded review of its plan. Ordinary plan approval cannot waive a hold.
    if not db.execute(
        "SELECT 1 FROM scientific_plan_approvals WHERE session_id=? AND plan_id=? "
        "AND plan_sha256=? AND rationale=? "
        "AND surface IN ('cli','studio','codex_native')",
        (sid, plan["plan_id"], args["plan_sha256"], args["rationale"]),
    ).fetchone():
        raise OperationBlocked("awaiting_review")
    db.execute(
        "UPDATE mcp_holds SET status='waived' WHERE session_id=? AND hold_id=?",
        (sid, hold_id),
    )
    return {"status": "waived"}


def _package(ctx, args, db):
    from .receipts import verified_receipt

    sid = args["session_id"]
    # Dataset bytes remain content-addressed external inputs; frozen scripts,
    # parameter choices, retention paths and verified evidence make replay possible.
    records = db.execute(
        "SELECT * FROM mcp_artifacts WHERE session_id=? ORDER BY artifact_id", (sid,)
    )
    artifacts = [
        {
            "artifact": json.loads(r["ref_json"]),
            "path": r["path"],
            "provenance": json.loads(r["detail_json"]),
            "status": r["status"],
        }
        for r in records
    ]
    plans = []
    if db.execute(
        "SELECT 1 FROM sqlite_master WHERE name='mcp_frozen_plans'"
    ).fetchone():
        for row in db.execute(
            "SELECT * FROM mcp_frozen_plans WHERE session_id=? ORDER BY plan_id", (sid,)
        ):
            plan = json.loads(row["plan_json"])
            if digest(plan) != row["plan_sha256"]:
                raise OperationBlocked("plan_identity_mismatch")
            plans.append({"plan": plan, "sha256": row["plan_sha256"]})
    runs = db.execute(
        "SELECT run_id FROM scientific_daemon_runs WHERE session_id=? AND "
        "status IN ('completed','failed','cancelled') ORDER BY run_id",
        (sid,),
    )
    receipts = [verified_receipt(ctx, sid, row[0], db) for row in runs]
    if not receipts:
        raise OperationBlocked("awaiting_information")
    manifest = {
        "format": "devbench.research-package/1",
        "session_id": sid,
        "revision": ctx.session(sid, db)["revision"],
        "spec": args.get("spec", {}),
        "aims": aims(db, sid),
        "inputs_and_outputs": artifacts,
        "plans": plans,
        "receipts": receipts,
        "clinical_release_allowed": False,
    }
    from devbench.engine.provenance import canonical_json

    raw = canonical_json(manifest)
    sha = hashlib.sha256(raw).hexdigest()
    path = ctx.workspace / "research-packages" / sid / (sha + ".json")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.resolve() != path:
        raise OperationBlocked("input_scope_violation")
    if path.exists():
        from devbench.core._files import hash_bounded_file

        if hash_bounded_file(path, len(raw)) != sha:
            raise OperationBlocked("plan_identity_mismatch")
    else:
        # Publish complete bytes atomically before recording their root.
        from devbench.engine.tier_a_executor import _write_record

        _write_record(path, manifest)
        if hashlib.sha256(path.read_bytes()).hexdigest() != sha:
            raise OperationBlocked("plan_identity_mismatch")
    return {"package_root_sha256": sha, "status": "built"}


def _apply(ctx, args, db, lifecycle, release):
    from devbench.platform.thesis_joint_bh import ThesisJointBHRegistry

    from .literature import query_literature

    action = args["action"]
    if action in _AIM_ACTIONS:
        return _mutate_aim(ctx, args, db, lifecycle)
    if action in {"fork_branch", "retire_branch"}:
        return _branch(ctx, args, db)
    if action == "freeze_joint_bh":
        registry = ThesisJointBHRegistry(lifecycle)
        registry.freeze(
            args["plan"],
            actor=_ACTOR,
            rationale=args.get("rationale", "Freeze family"),
            connection=db,
        )
        return {
            "joint_bh_state": {
                "status": "frozen",
                "family_id": args["plan"]["family_id"],
            }
        }
    if action == "release_joint_bh":
        item = release(db)
        return {
            "joint_bh_state": {
                "status": "released",
                "receipt_sha256": item.receipt_sha256(),
            }
        }
    if action == "query_literature":
        return {"literature_records": query_literature(ctx, args, db)}
    if action == "request_critic_review":
        return _critic(args, db)
    if action == "record_waiver":
        return _waiver(ctx, args, db)
    return _package(ctx, args, db)


def manage_research_aim(ctx, args):
    sid, action = args["session_id"], args["action"]
    base = {
        "session_id": sid,
        "action": action,
        "status": "recorded",
        "invalidated_steps": [],
        "joint_bh_state": None,
        "literature_records": None,
        "critic_review_id": None,
        "package_root_sha256": None,
        "clinical_release_allowed": False,
    }
    if action == "list":
        return ctx.authority.daemon_read(
            lambda db: {
                **base,
                "status": "listed",
                "revision": ctx.session(sid, db)["revision"],
                "aims": aims(db, sid, ctx.protected(sid, db=db)),
                "studio_url": ctx.handoff(sid, ctx.protected(sid, db=db))["studio_url"],
            }
        )
    private_snapshot = action == "query_literature" and args.get(
        "offline_snapshot_sha256"
    )
    if ctx.protected(sid) and not private_snapshot:
        raise OperationBlocked(PRIVATE)
    lifecycle = ThesisAimLifecycle(
        ctx.authority.ledger, sid, authorize_operator=lambda actor: actor == _ACTOR
    )
    release = None
    if action == "release_joint_bh":
        from devbench.engine.tier_a_executor import TierAExecutor
        from devbench.platform.thesis_joint_bh_release import prepare_joint_bh_release

        executor = TierAExecutor(
            ctx.authority.ledger,
            allow_data_execution=True,
            mounted_image_id=ctx.plugin.mounted_image_id,
        )
        release = prepare_joint_bh_release(
            lifecycle.workflow, executor, args["family_id"]
        )
    with ctx.authority.daemon_transaction() as db:
        if ctx.session(sid, db)["revision"] != args["expected_revision"]:
            raise OperationBlocked("stale_revision_conflict")
        private = ctx.protected(sid, db=db)
        if private and not private_snapshot:
            raise OperationBlocked(PRIVATE)
        updates = _apply(ctx, args, db, lifecycle, release)
        from .server import check_deadline

        check_deadline()
        revision = ctx.event(
            db,
            sid,
            "mcp_aim_action",
            {
                "action": action,
                "request_sha256": digest(args),
                "result_sha256": digest(updates),
                "actor_id": _ACTOR,
            },
        )
        return {
            **base,
            **updates,
            "revision": revision,
            "aims": aims(db, sid, private),
            "studio_url": ctx.handoff(sid, private)["studio_url"],
        }
