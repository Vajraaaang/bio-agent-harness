"""Out-of-band plan grants and the independent, outcome-blind review boundary."""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
import stat
import time
from pathlib import Path

from devbench.core._hashing import decode_receipt_json, utf8_sha256, workflow_json
from devbench.daemon import auth
from devbench.engine.partition_authority import embargoed
from devbench.engine.scientific_authority import _row, verify_frozen_plan
from devbench.engine.workflow_operations import OperationBlocked
from devbench.platform.plugin_jobs import canonical, digest

PUBLIC = {"public", "synthetic"}
PURPOSES = {"qc_diagnostic", "exploratory", "sensitivity_probe", "method_benchmark"}
AUTO_METHOD = "workspace_policy_auto_tier2_public"
FORMAT = "devbench.control-plan/1"
SHA = re.compile(r"[0-9a-f]{64}\Z")


def load_policy(workspace, *, cli_enabled=False):
    """Read only from the protected daemon directory at process startup."""
    try:
        with auth.state_directory(workspace) as directory:
            try:
                raw = auth.read_file(directory, "policy.json", 4096)
            except FileNotFoundError:
                raw = b"{}"
            flags = decode_receipt_json(raw)
            if not isinstance(flags, dict) or set(flags) - {
                "auto_approve_tier2_public"
            }:
                return None
            if type(flags.get("auto_approve_tier2_public", False)) is not bool:
                return None
            if cli_enabled is True:
                raw = canonical(
                    {"auto_approve_tier2_public": True, "origin": "daemon_cli"}
                )
                auth.write_file(directory, "launch-policy.json", raw)
            elif flags.get("auto_approve_tier2_public") is not True:
                return None
            return hashlib.sha256(raw).hexdigest()
    except (OSError, ValueError, TypeError):
        return None


def active_policy_digest(workspace, expected):
    """Revocation or permissions drift disables a previously loaded opt-in."""
    if expected is None:
        return None
    try:
        with auth.state_directory(workspace) as directory:
            for name in ("policy.json", "launch-policy.json"):
                try:
                    raw = auth.read_file(directory, name, 4096)
                except FileNotFoundError:
                    continue
                if hmac.compare_digest(expected, hashlib.sha256(raw).hexdigest()):
                    return expected
    except (OSError, ValueError):
        pass
    return None


def _identities(plan):
    bindings = list(plan.get("input_bindings", {}).values())
    for step in plan.get("graph_steps") or ():
        bindings.extend(step.get("input_bindings", {}).values())
    profiles = [*plan.get("profiles", ()), *plan.get("fastq", {}).get("inventory", ())]
    return (
        {item["sha256"] for item in bindings}
        | {item["sha256"] for item in profiles if item.get("sha256")}
        | set(plan.get("session_roots", ()))
    )


def _legacy_gate1(plan):
    return plan.get("epistemic_purpose") == "confirmatory" or plan.get(
        "governance_action"
    ) in {
        "record_waiver",
        "promote_kernel",
    }


def protected_scope(db, plan):
    """Read the latest privacy floor while the scientific WAL transition is fenced."""
    from devbench.mcp.context import guarded_digest, roots_query

    sid = plan["session_id"]
    hashes = _identities(plan)
    if db.execute(
        "SELECT 1 FROM sqlite_master WHERE name='mcp_artifact_roots'"
    ).fetchone():
        ancestors = roots_query(db, None, hashes, "SELECT sha256 FROM ancestry")
        hashes.update(row[0] for row in ancestors)
    if any(embargoed(db, sha) for sha in hashes):
        raise OperationBlocked("input_scope_violation")
    if plan.get("format") != FORMAT:
        if not db.execute(
            "SELECT 1 FROM sqlite_master WHERE name='mcp_sessions'"
        ).fetchone():
            return plan.get("data_classification") not in PUBLIC
        return plan.get("data_classification") not in PUBLIC or guarded_digest(
            db, sid, hashes
        )
    row = db.execute(
        "SELECT classification FROM mcp_sessions WHERE session_id=?", (sid,)
    ).fetchone()
    return (
        row is None
        or row[0] not in PUBLIC
        or plan.get("data_classification") not in PUBLIC
        or guarded_digest(db, sid, hashes)
    )


def _secure_stage(workspace, value):
    stage = Path(value)
    if not stage.is_relative_to(workspace) or stage == workspace:
        return False
    from devbench.engine.method_executor import _container_ids

    for path in (stage, stage / "inputs", stage / "outputs"):
        expected_owner = (
            _container_ids()[0] if path == stage / "outputs" else os.getuid()
        )
        if path != path.resolve(strict=True):
            return False
        info = path.stat()
        if (
            not stat.S_ISDIR(info.st_mode)
            or info.st_uid != expected_owner
            or info.st_mode & 0o022
        ):
            return False
    return True


def verified_isolation(workspace, plan, observation):
    """Check the runtime's observed container configuration before permitting start."""
    if not observation:
        return False
    try:
        stage = Path(observation["stage"])
        document = observation["document"]
        host = document["HostConfig"]
        opts = set(host["SecurityOpt"])
        image = plan["exploratory_code"]["base_image_id"]
        mounts = document["Mounts"]
        return (
            _secure_stage(workspace, stage)
            and re.fullmatch(r"sha256:[0-9a-f]{64}", image) is not None
            and document["Image"] == image
            and host["NetworkMode"] == "none"
            and host["ReadonlyRootfs"] is True
            and host["Privileged"] is False
            and set(host["CapDrop"]) == {"ALL"}
            and not host.get("CapAdd")
            and bool(opts & {"no-new-privileges:true", "no-new-privileges=true"})
            and not any("unconfined" in opt for opt in opts)
            and len(mounts) == 2
            and {(m["Type"], m["Source"], m["Destination"], m["RW"]) for m in mounts}
            == {
                ("bind", str(stage / "inputs"), "/inputs", False),
                ("bind", str(stage / "outputs"), "/outputs", True),
            }
        )
    except (OSError, ValueError, KeyError, TypeError):
        return False


def tier2(plan):
    return plan.get("execution_lane") == "tier2_exploratory"


def _auto_candidate(plan):
    return (
        plan.get("format") == FORMAT
        and plan.get("data_classification") in PUBLIC
        and plan.get("plan_kind") == "tier2_exploratory_code"
        and plan.get("epistemic_purpose") in PURPOSES
        and plan.get("claim_tier") == "L1_execution_verified"
        and plan.get("exploratory_only") is True
        and plan.get("clinical_release_allowed") is False
        and not plan.get("graph_steps")
        and isinstance(plan.get("script_sha256"), str)
        and bool(SHA.fullmatch(plan["script_sha256"]))
    )


def auto_grant(jobs, plan, sha, policy_sha, inspect):
    """Persist a grant only after independently observing all five ADR-19 gates."""
    if not policy_sha or not _auto_candidate(plan):
        return False
    args = (plan["session_id"], plan["plan_id"], sha)
    with jobs.frozen_policy(*args) as (_, restricted):
        if restricted:
            return False
    if jobs.authority.daemon_read(lambda db: protected_scope(db, plan)):
        return False
    if not verified_isolation(jobs.workspace, plan, inspect(jobs, plan)):
        return False

    def check(db):
        from devbench.engine.dispatch_deadline import check_deadline

        check_deadline()
        if protected_scope(db, plan):
            raise OperationBlocked("private_data_local_studio_only")

    jobs.authority.record_plan_approval(
        session_id=args[0],
        plan_id=args[1],
        plan_sha256=sha,
        frozen_plan=plan,
        public_plan_policy=jobs.frozen_policy(*args),
        check_grant=check,
        actor_id="workspace-policy",
        surface="daemon_policy",
        auth_method=AUTO_METHOD,
        rationale=f"Verified public Tier 2 sandbox under policy {policy_sha}",
        policy_sha256=policy_sha,
    )
    return True


def prepare_auto_grant(jobs, plan, sha, policy_sha):
    """Probe a new exact grant once; publication still checks the live isolation."""
    if not policy_sha or not _auto_candidate(plan):
        return False
    if active_policy_digest(jobs.workspace, policy_sha) is None:
        return False
    if jobs.authority.daemon_read(lambda db: protected_scope(db, plan)):
        return False
    if jobs.authority.daemon_read(
        lambda db: bool(_manual_grants(db, plan, sha, private=False))
    ):
        return False
    grant = jobs.authority.plan_approval(plan["session_id"], plan["plan_id"], sha)
    if (
        grant
        and grant["auth_method"] == AUTO_METHOD
        and grant["policy_sha256"] == policy_sha
        and grant["script_sha256"] == plan["script_sha256"]
    ):
        return True
    from devbench.mcp.execution import inspect_isolation

    return auto_grant(jobs, plan, sha, policy_sha, inspect_isolation)


def _manual_grants(db, plan, sha, *, private):
    rows = db.execute(
        "SELECT actor_id,surface,offline_approval FROM scientific_plan_approvals "
        "WHERE session_id=? AND plan_id=? AND plan_sha256=? AND script_sha256 IS ? "
        "AND surface IN ('cli','studio','codex_native')",
        (plan["session_id"], plan["plan_id"], sha, plan.get("script_sha256")),
    )
    return {
        row[0] for row in rows if not private or row[1] in {"cli", "studio"} and row[2]
    }


def gate1_subject(plan):
    """Export the preregistered design packet for independent review."""
    keys = (
        "session_id",
        "plan_id",
        "aim_id",
        "aim_version",
        "branch_id",
        "objective",
        "epistemic_purpose",
        "governance_action",
        "input_bindings",
        "parameter_origins",
        "kernel",
        "script_sha256",
        "depends_on",
    )
    return {
        "format": "devbench.outcome-blind-design/1",
        "plan_sha256": digest(plan),
        **{key: plan[key] for key in keys if key in plan},
    }


def _review_holds(db, plan):
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='mcp_holds'").fetchone():
        return []
    return db.execute(
        "SELECT hold_id,status FROM mcp_holds WHERE session_id=? "
        "AND json_extract(body,'$.kind')='critic_review' "
        "AND json_extract(body,'$.gate')=1 AND (json_extract(body,'$.plan_id')=? "
        "OR (json_extract(body,'$.plan_id') IS NULL "
        "AND json_extract(body,'$.aim_id')=?))",
        (plan["session_id"], plan["plan_id"], plan.get("aim_id")),
    ).fetchall()


def record_gate1(
    jobs, identity, *, reviewer, subject_sha256, status, rationale, offline
):
    """A local reviewer supplies an assessment of the exact outcome-blind packet."""
    plan = jobs.plan(identity["session_id"], identity["plan_id"])
    verify_frozen_plan(plan, **identity)
    subject_sha = digest(gate1_subject(plan))
    if (
        (plan.get("format") != FORMAT and not _legacy_gate1(plan))
        or status not in {"passed", "flagged"}
        or not reviewer.strip()
        or not rationale.strip()
        or len(rationale) > 1600
        or reviewer.casefold() == plan.get("author_id", "mcp-scientist").casefold()
        or not hmac.compare_digest(subject_sha, subject_sha256)
    ):
        raise OperationBlocked("critic_review_subject_mismatch")
    sid, pid = identity["session_id"], identity["plan_id"]
    with jobs.authority.daemon_transaction() as db:
        with jobs.frozen_policy(sid, pid, identity["plan_sha256"]) as (_, protected):
            if not offline and (protected or protected_scope(db, plan)):
                raise OperationBlocked("private_data_local_studio_only")
            before_fit = db.execute(
                "SELECT 1 FROM scientific_daemon_runs WHERE session_id=? "
                "AND plan_id=? AND dispatched_at IS NOT NULL",
                (sid, pid),
            ).fetchone()
            if before_fit:
                raise OperationBlocked("critic_review_requires_outcome_blind_design")
            state = {
                **identity,
                "subject_sha256": subject_sha,
                "actor_id": reviewer,
                "status": status,
                "rationale": rationale,
                "outcome_blind": True,
            }
            record_id = "gate1-" + digest(state)
            exists = db.execute(
                "SELECT 1 FROM scientific_records WHERE lineage_id=? AND record_id=?",
                (sid, record_id),
            ).fetchone()
            if exists is None:
                jobs.authority._append_execution_record(
                    db,
                    sid,
                    {
                        "kind": "execution_event",
                        "event": "control_plan_gate1",
                        "record_id": record_id,
                        "state": state,
                        "state_sha256": utf8_sha256(workflow_json(state)),
                    },
                )
            for hold_id, prior_status in _review_holds(db, plan):
                if prior_status == "pending":
                    db.execute(
                        "UPDATE mcp_holds SET status=? WHERE session_id=? "
                        "AND hold_id=?",
                        (status, sid, hold_id),
                    )
    return {"status": status, "subject_sha256": subject_sha, "actor_id": reviewer}


def _gate1(db, plan, sha, grants):
    if any(status in {"pending", "flagged"} for _, status in _review_holds(db, plan)):
        return False
    rows = db.execute(
        "SELECT * FROM scientific_records WHERE lineage_id=? AND record_id LIKE "
        "'gate1-%' AND json_extract(payload,'$.state.plan_sha256')=? "
        "ORDER BY revision DESC",
        (plan["session_id"], sha),
    )
    row = rows.fetchone()
    if row is None:
        return False
    record = _row(row)["record"]
    state = record["state"]
    return (
        record["event"] == "control_plan_gate1"
        and state["status"] == "passed"
        and state["outcome_blind"] is True
        and state["plan_id"] == plan["plan_id"]
        and state["subject_sha256"] == digest(gate1_subject(plan))
        and state["actor_id"].casefold()
        != plan.get("author_id", "mcp-scientist").casefold()
        and any(actor.casefold() != state["actor_id"].casefold() for actor in grants)
    )


def require_legacy_grant(authority, plan, sha, protected, *, offline_only, db=None):
    def check(connection):
        private = protected_scope(connection, plan) or protected
        if private and not offline_only:
            raise OperationBlocked("private_data_local_studio_only")
        if plan.get("operation") == "registered_exploratory" and not SHA.fullmatch(
            plan.get("script_sha256") or ""
        ):
            raise OperationBlocked("registered_method_replan_required")
        grants = _manual_grants(connection, plan, sha, private=private)
        if not grants:
            raise OperationBlocked("explicit_plan_approval_required")
        if _legacy_gate1(plan) and not _gate1(connection, plan, sha, grants):
            raise OperationBlocked("outcome_blind_critic_required")

    return check(db) if db is not None else authority.daemon_read(check)


def require_explicit_gate1(db, plan, sha):
    """Waivers and promotions always require a distinct explicit grant and review."""
    verify_frozen_plan(plan, plan["session_id"], plan["plan_id"], sha)
    grants = _manual_grants(db, plan, sha, private=protected_scope(db, plan))
    if not grants:
        raise OperationBlocked("explicit_plan_approval_required")
    if not _gate1(db, plan, sha, grants):
        raise OperationBlocked("outcome_blind_critic_required")


def _admit(db, plan, sha, protected, *, offline_only, policy_sha, gate1):
    if plan.get("governance_action") in {"record_waiver", "promote_kernel"}:
        raise OperationBlocked("input_scope_violation")
    private = protected_scope(db, plan) or protected
    if private and not offline_only:
        raise OperationBlocked("private_data_local_studio_only")
    _current_basis(db, plan)
    if not (private or tier2(plan) or gate1):
        return
    grants = _manual_grants(db, plan, sha, private=private)
    if grants:
        if gate1 and not _gate1(db, plan, sha, grants):
            raise OperationBlocked("outcome_blind_critic_required")
        return
    if (
        not private
        and not gate1
        and policy_sha
        and _auto_candidate(plan)
        and db.execute(
            "SELECT 1 FROM scientific_plan_approvals WHERE session_id=? AND "
            "plan_id=? AND plan_sha256=? AND auth_method=? AND surface='daemon_policy' "
            "AND policy_sha256=? AND script_sha256=?",
            (
                plan["session_id"],
                plan["plan_id"],
                sha,
                AUTO_METHOD,
                policy_sha,
                plan["script_sha256"],
            ),
        ).fetchone()
    ):
        return
    raise OperationBlocked("explicit_plan_approval_required")


def _current_basis(db, plan):
    branch = db.execute(
        "SELECT status FROM mcp_branches WHERE session_id=? AND branch_id=?",
        (plan["session_id"], plan["branch_id"]),
    ).fetchone()
    if branch is None or branch[0] == "retired":
        raise OperationBlocked("stale_revision_conflict")
    if plan.get("aim_id"):
        aim = db.execute(
            "SELECT version,status FROM mcp_aims WHERE session_id=? AND aim_id=?",
            (plan["session_id"], plan["aim_id"]),
        ).fetchone()
        if aim is None or aim[0] != plan["aim_version"] or aim[1] == "retired":
            raise OperationBlocked("stale_revision_conflict")
    for binding in plan.get("depends_on") or ():
        row = db.execute(
            "SELECT status FROM scientific_daemon_runs WHERE session_id=? "
            "AND plan_id=? AND plan_sha256=? ORDER BY rowid DESC LIMIT 1",
            (plan["session_id"], binding["plan_id"], binding["plan_sha256"]),
        ).fetchone()
        if row is None or row[0] != "completed":
            raise OperationBlocked("awaiting_information")


def require_governance(
    authority,
    plan,
    sha,
    protected,
    *,
    offline_only,
    policy_sha=None,
    gate1=True,
    db=None,
):
    def check(connection):
        _admit(
            connection,
            plan,
            sha,
            protected,
            offline_only=offline_only,
            policy_sha=policy_sha,
            gate1=gate1,
        )

    return check(db) if db is not None else authority.daemon_read(check)


def issue_native_confirmation(jobs, identity, actor):
    """Only a local IPC operator can mint this one-time opaque confirmation."""
    plan = jobs.plan(identity["session_id"], identity["plan_id"])
    verify_frozen_plan(plan, **identity)
    triple = tuple(identity[key] for key in ("session_id", "plan_id", "plan_sha256"))
    token = secrets.token_urlsafe(32)
    token_sha = hashlib.sha256(token.encode()).hexdigest()
    with (
        jobs.authority.daemon_transaction() as db,
        jobs.frozen_policy(*triple) as (_, private),
    ):
        if private or protected_scope(db, plan):
            raise OperationBlocked("private_data_local_studio_only")
        db.execute("""CREATE TABLE IF NOT EXISTS mcp_native_confirmations (
            token_sha256 TEXT PRIMARY KEY, session_id TEXT NOT NULL,
            plan_id TEXT NOT NULL, plan_sha256 TEXT NOT NULL,
            actor_id TEXT NOT NULL, expires REAL NOT NULL)""")
        now = time.time()
        db.execute("DELETE FROM mcp_native_confirmations WHERE expires<=?", (now,))
        db.execute(
            "INSERT INTO mcp_native_confirmations VALUES(?,?,?,?,?,?)",
            (token_sha, *triple, actor, now + 300),
        )
    return token


def accept_native_confirmation(ctx, args, token):
    """Atomically consume an operator nonce with its exact, still-public grant."""
    if not isinstance(token, str) or re.fullmatch(r"[A-Za-z0-9_-]{43}", token) is None:
        raise OperationBlocked("explicit_plan_approval_required")
    token_sha = hashlib.sha256(token.encode()).hexdigest()
    triple = tuple(args[key] for key in ("session_id", "plan_id", "plan_sha256"))
    where = (
        "token_sha256=? AND session_id=? AND plan_id=? AND plan_sha256=? AND expires>?"
    )

    def lookup(db):
        exists = db.execute(
            "SELECT 1 FROM sqlite_master WHERE name='mcp_native_confirmations'"
        ).fetchone()
        if not exists:
            return None
        return db.execute(
            "SELECT actor_id FROM mcp_native_confirmations WHERE " + where,
            (token_sha, *triple, time.time()),
        ).fetchone()

    row = ctx.authority.daemon_read(lookup)
    if row is None:
        raise OperationBlocked("explicit_plan_approval_required")
    actor = row[0]

    def consume(db):
        from devbench.engine.dispatch_deadline import check_deadline

        check_deadline()
        count = db.execute(
            "DELETE FROM mcp_native_confirmations WHERE actor_id=? AND " + where,
            (actor, token_sha, *triple, time.time()),
        ).rowcount
        if count != 1:
            raise OperationBlocked("explicit_plan_approval_required")

    ctx.jobs.approve(
        *triple,
        actor_id=actor,
        surface="codex_native",
        auth_method="local_operator_confirmation",
        require_public=True,
        grant_guard=consume,
    )
