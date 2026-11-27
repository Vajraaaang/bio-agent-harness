"""Out-of-band acceptance of explicitly reviewed, workspace-scoped Tier 1 methods."""

from __future__ import annotations

import hashlib
import re
from uuid import uuid4

from jsonschema import Draft202012Validator

from devbench.engine.scientific_authority import _row, verify_frozen_plan
from devbench.engine.workflow_operations import OperationBlocked
from devbench.platform.plugin_jobs import canonical, digest

from .approval import require_explicit_gate1
from .schemas import AXIS, REPRESENTATIONS

_GATE_EVENTS = (
    "pinned_environment",
    "typed_io",
    "null_and_planted_calibration",
    "adversarial_boundary_tests",
    "independent_review",
)


def _evidence(db, sid, version, binding, event):
    row = db.execute(
        "SELECT * FROM scientific_records WHERE lineage_id=? AND record_id=? "
        "AND sha256=?",
        (sid, binding["record_id"], binding["sha256"]),
    ).fetchone()
    if row is None:
        raise OperationBlocked("awaiting_review")
    record = _row(row)["record"]
    state = record["state"]
    if (
        record["event"] != event
        or record["state_sha256"] != digest(state)
        or state["method_version_id"] != version
        or state["status"] != "passed"
    ):
        raise OperationBlocked("awaiting_review")
    return state


def _validate_descriptor(source, proposal, evidence):
    from .registry import catalog

    if (
        not re.fullmatch("[a-z][a-z0-9._-]{1,95}", proposal["kernel_id"])
        or proposal["kernel_id"] in catalog()
        or proposal["scope"] != "workspace"
        or proposal["method_version_id"] != source["script_sha256"]
        or not proposal["capability_axes"]
        or set(proposal["capability_axes"]) - set(AXIS["enum"])
        or not proposal["input_ports"]
        or set(proposal["input_ports"].values()) - set(REPRESENTATIONS["enum"])
    ):
        raise OperationBlocked("invalid_request")
    schema = proposal["parameter_schema"]
    Draft202012Validator.check_schema(schema)
    if (
        schema.get("type") != "object"
        or schema.get("additionalProperties") is not False
        or "seed" not in schema.get("required", ())
        or "seed" not in schema.get("properties", {})
    ):
        raise OperationBlocked("invalid_request")
    pinned = evidence["pinned_environment"]
    if pinned["image_id"] != source["exploratory_code"][
        "base_image_id"
    ] or not re.fullmatch("[0-9a-f]{64}", pinned["lock_sha256"]):
        raise OperationBlocked("plan_identity_mismatch")
    io = evidence["typed_io"]
    if (
        io["input_ports"] != proposal["input_ports"]
        or io["parameter_schema"] != schema
        or io["output_ports"] != source["exploratory_code"]["expected_outputs"]
    ):
        raise OperationBlocked("plan_identity_mismatch")
    calibration = evidence["null_and_planted_calibration"]
    if (
        not proposal["calibrated_regimes"]
        or calibration["calibrated_regimes"] != proposal["calibrated_regimes"]
        or not all(
            isinstance(r, str) and 0 < len(r) <= 128
            for r in proposal["calibrated_regimes"]
        )
    ):
        raise OperationBlocked("awaiting_review")
    critic = evidence["independent_review"]
    if (
        critic["critic_origin"]
        not in ("ai_critic_review", "human_expert_panel", "hybrid")
        or critic["deterministic_verification_passed"] is not True
        or critic["reviewer_id"].casefold() == source["author_id"].casefold()
    ):
        raise OperationBlocked("awaiting_review")


def _check(ctx, db, source, proposal):
    if (
        source["plan_kind"] != "tier2_exploratory_code"
        or source.get("graph_steps")
        or source.get("sweep_spec")
        or source.get("governance_action")
        or source["script_sha256"]
        != hashlib.sha256(
            source["exploratory_code"]["source_code"].encode()
        ).hexdigest()
        or set(proposal["evidence"]) != set(_GATE_EVENTS)
    ):
        raise OperationBlocked("invalid_request")
    if ctx.protected(source["session_id"], db=db) and not ctx.offline_only:
        raise OperationBlocked("private_data_local_studio_only")
    evidence = {
        event: _evidence(
            db,
            source["session_id"],
            source["script_sha256"],
            proposal["evidence"][event],
            event,
        )
        for event in _GATE_EVENTS
    }
    _validate_descriptor(source, proposal, evidence)


def prepare_workspace_promotion(ctx, source_identity, proposal):
    """Freeze a review packet for CLI/Studio sign-off and independent Gate 1.

    Only the local operator's promotion workflow calls this function. The MCP
    freeze schema cannot accept a promotion record or choose these gate events.
    """
    source = ctx.jobs.plan(source_identity["session_id"], source_identity["plan_id"])
    verify_frozen_plan(source, **source_identity)
    ctx.authority.daemon_read(lambda db: _check(ctx, db, source, proposal))
    review = {
        **source,
        "plan_id": "cp-" + uuid4().hex,
        "governance_action": "promote_kernel",
        "promotion": {"source_identity": source_identity, "descriptor": proposal},
    }
    sid, pid, sha = source["session_id"], review["plan_id"], digest(review)
    with ctx.authority.daemon_transaction() as db:
        _check(ctx, db, source, proposal)
        db.execute(
            "INSERT INTO mcp_frozen_plans VALUES(?,?,?,?,?)",
            (sid, pid, sha, canonical(review).decode(), review["execution_lane"]),
        )
        ctx.event(
            db,
            sid,
            "workspace_promotion_review_requested",
            {
                "plan_id": pid,
                "plan_sha256": sha,
                "method_version_id": source["script_sha256"],
            },
        )
    ctx.jobs.freeze(review)
    return {"session_id": sid, "plan_id": pid, "plan_sha256": sha}


def _checked(ctx, db, identity):
    review = ctx.jobs.plan(identity["session_id"], identity["plan_id"])
    verify_frozen_plan(review, **identity)
    if review.get("governance_action") != "promote_kernel":
        raise OperationBlocked("awaiting_review")
    require_explicit_gate1(db, review, identity["plan_sha256"])
    custody = review["promotion"]
    original = custody["source_identity"]
    source = ctx.jobs.plan(original["session_id"], original["plan_id"])
    verify_frozen_plan(source, **original)
    if (
        source["session_id"] != review["session_id"]
        or source["script_sha256"] != review["script_sha256"]
    ):
        raise OperationBlocked("plan_identity_mismatch")
    _check(ctx, db, source, custody["descriptor"])
    return custody["descriptor"], source


def seal_workspace_promotion(ctx, identity):
    """Publish the method, scope and calibration evidence bound by the grant."""
    with ctx.authority.daemon_transaction() as db:
        desc, _ = _checked(ctx, db, identity)
        event = {
            "kind": "execution_event",
            "record_id": "kernel-promotion-" + identity["plan_sha256"],
            "event": "workspace_kernel_promoted",
            "state": identity,
            "state_sha256": digest(identity),
        }
        existing = db.execute(
            "SELECT * FROM scientific_records WHERE lineage_id=? AND record_id=?",
            (identity["session_id"], event["record_id"]),
        ).fetchone()
        if existing is None:
            revision = ctx.authority._append_execution_record(
                db, identity["session_id"], event
            )
            db.execute(
                "UPDATE mcp_sessions SET revision=? WHERE session_id=?",
                (revision, identity["session_id"]),
            )
        elif _row(existing)["record"].get("state") != identity:
            raise OperationBlocked("plan_identity_mismatch")
    return desc["kernel_id"], desc["method_version_id"]


def workspace_kernels(ctx, sid):
    def read(db):
        rows = db.execute(
            "SELECT * FROM scientific_records WHERE "
            "kind='execution_event' AND record_id LIKE 'kernel-promotion-%' "
            "ORDER BY rowid DESC LIMIT 100",
        )
        result = {}
        for row in rows:
            if row["lineage_id"] != sid and ctx.protected(row["lineage_id"], db=db):
                continue
            record = _row(row)["record"]
            if (
                record["event"] != "workspace_kernel_promoted"
                or digest(record["state"]) != record["state_sha256"]
            ):
                raise OperationBlocked("plan_identity_mismatch")
            desc, source = _checked(ctx, db, record["state"])
            key = (desc["kernel_id"], desc["method_version_id"])
            if key in result and result[key][0] != desc:
                raise OperationBlocked("plan_identity_mismatch")
            result[key] = desc, source
        return result

    return ctx.authority.daemon_read(read)
