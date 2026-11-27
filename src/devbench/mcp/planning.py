"""Freeze governed plan identities and expose bounded daemon job handles."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import shlex
import stat
import time
from contextlib import ExitStack
from pathlib import Path
from uuid import uuid4

from devbench.core._files import hash_bounded_file
from devbench.daemon import auth
from devbench.engine.dispatch_deadline import check_deadline, remaining
from devbench.engine.scientific_authority import _row, verify_frozen_plan
from devbench.engine.workflow_operations import OperationBlocked
from devbench.mcp import approval, approval_preflight
from devbench.mcp.schemas import SCRIPT_BYTES, StrictValidator
from devbench.platform.plugin_jobs import canonical, digest

_FINAL = {"completed", "failed", "cancelled", "interrupted"}
_STATUS = {
    "starting": "running",
    "finishing": "running",
    "cancelling": "running",
    "awaiting_approval": "awaiting_review",
    "awaiting_offline": "awaiting_review",
}
_INTERNAL = {".devbench-daemon", ".git", "methods", "runs", "plugin-jobs.sqlite3"}
_PLAN_TABLE = """CREATE TABLE IF NOT EXISTS mcp_frozen_plans (
    session_id TEXT NOT NULL, plan_id TEXT PRIMARY KEY, plan_sha256 TEXT NOT NULL,
    plan_json TEXT NOT NULL, execution_lane TEXT NOT NULL)"""


def _script_file(workspace, name):
    path = Path(name)
    if (
        path.is_absolute()
        or not path.parts
        or ".." in path.parts
        or path.parts[0] in _INTERNAL
    ):
        raise OperationBlocked("input_scope_violation")
    with ExitStack() as files:
        fd = os.open(workspace, auth.DIR_FLAGS)
        files.callback(os.close, fd)
        for part in path.parts[:-1]:
            fd = os.open(part, auth.DIR_FLAGS, dir_fd=fd)
            files.callback(os.close, fd)
        leaf = os.open(path.name, os.O_RDONLY | auth.FILE_FLAGS, dir_fd=fd)
        stream = files.enter_context(os.fdopen(leaf, "rb"))
        before = os.fstat(leaf)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise OperationBlocked("input_scope_violation")
        data = stream.read(SCRIPT_BYTES + 1)
        after = os.fstat(leaf)
        if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ):
            raise OperationBlocked("plan_identity_mismatch")
    return data


def _snapshot_code(workspace, code):
    if code.get("script_path") is not None:
        try:
            raw = _script_file(workspace, code.pop("script_path"))
        except OSError:
            raise OperationBlocked("input_scope_violation") from None
    else:
        raw = code["source_code"].encode("utf-8")
    if not raw or len(raw) > SCRIPT_BYTES:
        raise OperationBlocked("resource_quota_exceeded")
    code["source_code"] = raw.decode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _steps(request):
    seen = {"root"}
    for step in request.get("graph_steps") or ():
        key = step["step_key"]
        if (
            not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}", key)
            or key in seen
            or not set(step.get("depends_on", ())) <= seen
        ):
            raise OperationBlocked("invalid_request")
        seen.add(key)
        if (step["plan_kind"] == "tier1_kernel") != bool(step.get("kernel")) or (
            step["plan_kind"] == "tier2_exploratory_code"
        ) != bool(step.get("exploratory_code")):
            raise OperationBlocked("invalid_request")
        yield step


def _script_pins(ctx, plan):
    from devbench.mcp.registry import tier2_base_images

    scripts = [
        step["exploratory_code"]
        for step in (plan, *_steps(plan))
        if step.get("exploratory_code") is not None
    ]
    if not scripts:
        return None, []
    if plan["epistemic_purpose"] == "confirmatory":
        raise OperationBlocked("scientific_refusal")
    images = tier2_base_images()
    allowed = {(image["image_id"], image["language"]) for image in images}
    hashes = []
    warnings = []
    gpu = (plan.get("resource_override") or {}).get("gpu")
    for script in scripts:
        if gpu and not any(
            row["image_id"] == script["base_image_id"] and row["gpu_supported"]
            for row in images
        ):
            raise OperationBlocked("runtime_prerequisite_missing")
        if (script["base_image_id"], script["language"]) not in allowed:
            raise OperationBlocked("runtime_prerequisite_missing")
        hashes.append(_snapshot_code(ctx.workspace, script))
        warnings.extend(approval_preflight.check_source(script))
    return (hashes[0] if len(hashes) == 1 else digest(hashes)), warnings


def _kernel_overrides(plan, kernel):
    overrides = plan.get("resource_override") or {}
    profile = kernel.get("resource_profile") or {}
    for name in ("cpus", "memory_mb", "timeout_sec"):
        limit = profile.get("max_" + name)
        if limit is not None and overrides.get(name, 0) > limit:
            raise OperationBlocked("resource_quota_exceeded")
    if overrides.get("gpu") and not profile.get("gpu_supported"):
        raise OperationBlocked("runtime_prerequisite_missing")


def _validate_parameters(schema, base, sweep):
    check = StrictValidator(schema)
    if not check.is_valid(base) or any(
        not check.is_valid({**base, **params})
        for params in (sweep or {}).get("parameter_sets", ())
    ):
        raise OperationBlocked("invalid_request")


def _settings(ctx, plan):
    from devbench.mcp.registry import resolve_kernel, select_image

    pins = {}
    warnings = []
    for step in (plan, *_steps(plan)):
        if step["plan_kind"] != "tier1_kernel":
            continue
        request = step["kernel"]
        kernel = resolve_kernel(
            ctx, request["kernel_id"], request["version"], session_id=plan["session_id"]
        )
        if (
            kernel["promotion_scope"] == "workspace"
            and plan["epistemic_purpose"] == "confirmatory"
            and kernel["calibration_status"] != "calibrated_L2"
        ):
            raise OperationBlocked("scientific_refusal")
        _kernel_overrides(plan, kernel)
        params = kernel.get("parameter_schema")
        if params:
            _validate_parameters(params, request["parameters"], plan.get("sweep_spec"))
        if not kernel["runtime_ready"]:
            warnings.append("kernel_runtime_prerequisite_missing")
        key = step.get("step_key", "root")
        pins[key] = {
            "kernel_id": kernel["kernel_id"],
            "version": kernel["version"],
            "oci_image_digest": select_image(
                ctx,
                kernel["kernel_id"],
                session_id=plan["session_id"],
                version=kernel["version"],
            ),
            "required_representations": kernel["required_representations"],
        }
        if (
            kernel["kernel_id"] == "matrix_audit"
            and kernel["promotion_scope"] == "repository"
        ):
            pins[key]["script_sha256"] = hash_bounded_file(
                Path(__file__).with_name("matrix_audit.py"), SCRIPT_BYTES
            )
    root = pins.get("root", {})
    return {**root, "steps": pins}, sorted(set(warnings))


def require_runtime_pins(supervisor, plan):
    """Fail before enqueue when a frozen adapter has no locally available image."""
    from devbench.mcp.runtime import available_images

    live = {row["image_id"] for row in available_images(supervisor, backend="docker")}
    for step in (plan, *_steps(plan)):
        if step["plan_kind"] == "tier2_exploratory_code":
            image = step["exploratory_code"]["base_image_id"]
        else:
            pin = plan["runtime_pin"]["steps"].get(step.get("step_key", "root"), {})
            image = pin.get("oci_image_digest")
        if not image or image not in live:
            raise OperationBlocked("runtime_prerequisite_missing")


def _basis(ctx, db, plan):
    sid = plan["session_id"]
    branch = db.execute(
        "SELECT * FROM mcp_branches WHERE session_id=? AND branch_id=?",
        (sid, plan["branch_id"]),
    ).fetchone()
    if branch is None or branch["status"] == "retired":
        raise OperationBlocked("stale_revision_conflict")
    if plan.get("branch_kind", branch["branch_kind"]) != branch["branch_kind"]:
        raise OperationBlocked("input_scope_violation")
    plan["branch_kind"] = branch["branch_kind"]
    aim_id = plan.get("aim_id") or branch["aim_id"]
    if not aim_id:
        return
    aim = db.execute(
        "SELECT * FROM mcp_aims WHERE session_id=? AND aim_id=?", (sid, aim_id)
    ).fetchone()
    if aim is None or (branch["aim_id"] is not None and branch["aim_id"] != aim_id):
        raise OperationBlocked("input_scope_violation")
    if plan["epistemic_purpose"] == "confirmatory" and aim["status"] != "approved":
        raise OperationBlocked("awaiting_review")
    plan.update(aim_id=aim_id, aim_version=aim["version"])


def _origins(ctx, db, plan):
    parameters = (plan.get("kernel") or plan["exploratory_code"]).get("parameters", {})
    origins = plan.get("parameter_origins", {})
    for name, origin in origins.items():
        if name not in parameters:
            raise OperationBlocked("invalid_request")
        if origin not in {"user_prespecified", "operator_approved"}:
            continue
        row = db.execute(
            "SELECT * FROM scientific_records WHERE lineage_id=? "
            "AND kind='execution_event' AND json_extract(payload,'$.event')="
            "'parameter_requirement_retained' AND "
            "json_extract(payload,'$.state.name')=? "
            "AND json_extract(payload,'$.state.origin')=? "
            "AND json_extract(payload,'$.state.value_sha256')=? "
            "ORDER BY revision DESC LIMIT 1",
            (plan["session_id"], name, origin, digest(parameters[name])),
        ).fetchone()
        if row is None:
            raise OperationBlocked("awaiting_information")
        state = _row(row)["record"]["state"]
        fact_sha = state.get("fact_sha256", "")
        if not approval.SHA.fullmatch(fact_sha):
            raise OperationBlocked("awaiting_information")
        from devbench.platform.adaptive_workflow import AdaptiveWorkflow

        basis = (
            AdaptiveWorkflow(ctx.authority.ledger, plan["session_id"])._state(db).basis
        )
        if not any(
            item["kind"] == "fact" and item["sha256"] == fact_sha
            for item in basis.values()
        ):
            raise OperationBlocked("awaiting_information")
        for variant in (plan.get("sweep_spec") or {}).get("parameter_sets", ()):
            if name in variant and canonical(variant[name]) != canonical(
                parameters[name]
            ):
                raise OperationBlocked("scientific_refusal")


def _bind_inputs(ctx, db, plan, pins):
    roots = set()
    warnings = []
    for step in (plan, *_steps(plan)):
        representations = (
            pins["steps"]
            .get(step.get("step_key", "root"), {})
            .get("required_representations", ())
        )
        records = [
            ctx.artifact(
                plan["session_id"], binding["artifact_id"], binding["sha256"], db
            )
            for binding in step.get("input_bindings", plan["input_bindings"]).values()
        ]
        if code := step.get("exploratory_code"):
            warnings.extend(approval_preflight.input_warnings(code, records))
        roots.update(record["ref"]["sha256"] for record in records)
        roots.update(
            record["detail"]["source_sha256"]
            for record in records
            if record["detail"].get("source_sha256")
        )
        if not records and representations:
            raise OperationBlocked("awaiting_information")
        for record in records:
            ref = record["ref"]
            if (
                ref["kind"] == "expression_matrix"
                and representations
                and ref["representation_class"] not in representations
            ):
                raise OperationBlocked("scientific_refusal")
            count_model = representations and set(representations) <= {
                "count_1_raw_counts",
                "aggregated_unit_counts",
                "metadata_only",
                "reference_only",
            }
            if (
                count_model
                and ref["kind"] == "expression_matrix"
                and not record["detail"].get("count_1_verified")
            ):
                raise OperationBlocked("scientific_refusal")
    return sorted(roots), warnings


def _freeze(ctx, db, plan):
    sid = plan["session_id"]
    state = ctx.session(sid, db)
    db.execute(_PLAN_TABLE)
    _basis(ctx, db, plan)
    _origins(ctx, db, plan)
    roots, warnings = _bind_inputs(ctx, db, plan, plan["runtime_pin"])
    private = ctx.protected(sid, roots, db)
    for dep in plan.get("depends_on") or ():
        row = db.execute(
            "SELECT plan_sha256 FROM mcp_frozen_plans WHERE session_id=? AND plan_id=?",
            (sid, dep["plan_id"]),
        ).fetchone()
        if row is None or row[0] != dep["plan_sha256"]:
            raise OperationBlocked("plan_identity_mismatch")
    plan.update(
        session_roots=roots,
        data_classification=(
            "private"
            if private and state["classification"] in approval.PUBLIC
            else state["classification"]
        ),
    )
    encoded = canonical(plan)
    sha = hashlib.sha256(encoded).hexdigest()
    verify_frozen_plan(plan, sid, plan["plan_id"], sha)
    check_deadline()
    db.execute(
        "INSERT INTO mcp_frozen_plans VALUES(?,?,?,?,?)",
        (sid, plan["plan_id"], sha, encoded.decode(), plan["execution_lane"]),
    )
    # Both projections are written under the authority transaction. Readers
    # authenticate the Studio copy against the authoritative frozen identity.
    timeout = db.execute("PRAGMA busy_timeout").fetchone()[0] / 1000
    db.execute(f"PRAGMA busy_timeout={max(1, int(remaining(timeout) * 1000))}")
    db.execute(
        "ATTACH DATABASE ? AS studio_jobs", (ctx.jobs.path.as_uri() + "?mode=rw",)
    )
    db.execute(
        "INSERT INTO studio_jobs.plugin_plans VALUES(?,?,?,?,?)",
        (plan["plan_id"], sid, sha, encoded.decode(), time.time()),
    )
    ctx.event(
        db,
        sid,
        "mcp_plan_frozen",
        {
            "plan_id": plan["plan_id"],
            "plan_sha256": sha,
            "branch_id": plan["branch_id"],
            "execution_lane": plan["execution_lane"],
        },
    )
    check_deadline()
    return sha, private, warnings


def freeze_analysis_plan(ctx, args):
    plan = copy.deepcopy(args)
    script, code_warnings = _script_pins(ctx, plan)
    pin, warnings = _settings(ctx, plan)
    warnings.extend(code_warnings)
    if plan.get("exploratory_code"):
        pin["oci_image_digest"] = plan["exploratory_code"]["base_image_id"]
    plan.update(
        format=approval.FORMAT,
        operation="control_plane",
        author_id="mcp-scientist",
        input_root=str(ctx.input_root),
        allow_external_input_files=ctx.config.get("allow_external_input_files") is True,
        plan_id=uuid4().hex,
        execution_lane="tier2_exploratory" if script else "tier1_curated",
        claim_tier="L1_execution_verified",
        exploratory_only=script is not None,
        clinical_release_allowed=False,
        script_sha256=script,
        runtime_pin=pin,
    )
    with ctx.authority.daemon_transaction() as db:
        sha, private, evidence_warnings = _freeze(ctx, db, plan)
    warnings.extend(evidence_warnings)
    required = (
        "codex_native"
        if script or plan["epistemic_purpose"] == "confirmatory"
        else "none"
    )
    if script and not private and getattr(ctx.supervisor, "policy_sha256", None):
        warnings.append(
            "Automatic approval requires verified OCI isolation at submission."
        )
    handoff = ctx.handoff(plan["session_id"], private=private)
    if not private and required != "none":
        handoff["launch_hint"] = shlex.join(
            [
                "devbench",
                "approve",
                "--workspace",
                "<workspace>",
                "--session-id",
                plan["session_id"],
                "--plan-id",
                plan["plan_id"],
                "--plan-sha256",
                sha,
            ]
        )
    return {
        "session_id": plan["session_id"],
        "plan_id": plan["plan_id"],
        "plan_sha256": sha,
        "status": "local_studio_only" if private else "planned",
        "execution_lane": plan["execution_lane"],
        "claim_tier_ceiling": plan["claim_tier"],
        "approval_required": "local_studio_or_cli" if private else required,
        "runtime_pin": {} if private else pin,
        "static_preflight_warnings": [] if private else sorted(set(warnings)),
        **handoff,
    }


def _run(ctx, args):
    def lookup(db):
        if not db.execute(
            "SELECT 1 FROM sqlite_master WHERE name='mcp_frozen_plans'"
        ).fetchone():
            return None
        return db.execute(
            "SELECT r.run_id,r.plan_id,r.session_id,r.status,r.pool,r.stage_json,"
            "r.started_at,r.updated_at,r.finished_at,r.dispatched_at,"
            "r.cleanup_state,r.checkpoint_sha256,r.pid,r.generation,r.create_time,"
            "p.execution_lane FROM scientific_daemon_runs r JOIN mcp_frozen_plans p "
            "ON r.session_id=p.session_id AND r.plan_id=p.plan_id "
            "WHERE r.session_id=? AND r.run_id=?",
            (args["session_id"], args["run_id"]),
        ).fetchone()

    row = ctx.authority.daemon_read(lookup)
    if row is None:
        raise OperationBlocked("input_scope_violation")
    return dict(row)


def get_run_status(ctx, args):
    from devbench.daemon.supervisor import _worker_pulse

    row = _run(ctx, args)
    sid = row["session_id"]
    private = ctx.protected(sid)
    status = _STATUS.get(row["status"], row["status"])
    stage = json.loads(row["stage_json"])
    percent = stage.get("percent", 0)
    if not isinstance(percent, (int, float)) or not 0 <= percent <= 100 or private:
        percent = 0
    progress = {
        "completed": 100 if status == "completed" else percent,
        "total": 100,
        "unit": "percent",
        "stage": status,
    }
    total = stage.get("total")
    completed = stage.get("completed")
    if (
        not private
        and type(total) is int
        and type(completed) is int
        and 0 <= completed <= total
        and total > 0
    ):
        progress = {
            "completed": total if status == "completed" else completed,
            "total": total,
            "unit": "steps",
            "stage": status,
        }
    pulse = _worker_pulse(
        ctx.workspace / auth.STATE_DIR / "runs" / row["run_id"], row, stage
    )
    from datetime import datetime

    heartbeat = pulse["worker_heartbeat_utc"]
    age = (
        max(0, time.time() - datetime.fromisoformat(heartbeat).timestamp())
        if heartbeat
        else None
    )
    return {
        **args,
        "plan_id": row["plan_id"],
        "status": status,
        "pool": row["pool"],
        "execution_lane": row["execution_lane"],
        "progress": progress,
        "heartbeat_age_sec": age,
        "elapsed_sec": max(0, (row["finished_at"] or time.time()) - row["started_at"]),
        "checkpoint_available": bool(row["checkpoint_sha256"]),
        "tasks": [],
        "warnings": ["worker_heartbeat_stale"]
        if pulse["worker_heartbeat_stale"]
        else [],
        **ctx.handoff(sid, private=private),
    }


def submit_reviewed_run(ctx, args):
    if ctx.protected(args["session_id"]):
        # The actor reviews and submits protected work on the offline local surface.
        raise OperationBlocked("private_data_local_studio_only")
    plan = ctx.jobs.plan(args["session_id"], args["plan_id"])
    if plan.get("format") != approval.FORMAT:
        raise OperationBlocked("plan_identity_mismatch")
    verify_frozen_plan(plan, args["session_id"], args["plan_id"], args["plan_sha256"])
    if ctx.supervisor is None:
        raise OperationBlocked("runtime_prerequisite_missing")
    check_deadline()
    row = ctx.supervisor.submit(**args)
    return get_run_status(
        ctx, {"session_id": args["session_id"], "run_id": row["run_id"]}
    )


def cancel_run(ctx, args):
    row = _run(ctx, args)
    if ctx.supervisor is None:
        raise OperationBlocked("runtime_prerequisite_missing")
    terminal = row["status"] in _FINAL and row["cleanup_state"] == "confirmed"
    if not terminal:
        check_deadline()
        row = ctx.supervisor.cancel_run(args["run_id"], wait=False)
    cleanup = row["cleanup_state"]
    return {
        "session_id": args["session_id"],
        "run_id": args["run_id"],
        "status": "already_terminal" if terminal else "cancelled",
        "cleanup_state": cleanup if cleanup in {"confirmed", "failed"} else "pending",
        "checkpoint_preserved": bool(row["checkpoint_sha256"]),
    }
