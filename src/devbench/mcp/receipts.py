"""Hash-bound terminal evidence and read-only public receipt projection."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from devbench.core._files import hash_bounded_file, read_bounded_file
from devbench.engine.scientific_authority import _row
from devbench.engine.workflow_operations import OperationBlocked
from devbench.platform.plugin_jobs import canonical, digest

from .context import PRIVATE


def _record_id(rid, status):
    return "mcp-receipt-" + rid + "-" + status


def _base(row, lane):
    return {
        "session_id": row["session_id"],
        "run_id": row["run_id"],
        "plan_id": row["plan_id"],
        "plan_sha256": row["plan_sha256"],
        "status": row["status"],
        "execution_lane": lane,
        "claim_tier": "none",
        "clinical_release_allowed": False,
        "ai_critic_verified": False,
        "probability_bounds_status": "not_evaluated",
        "critic_audit": {
            "status": "not_run",
            "critic_origin": "deterministic_verifier",
            "findings": [],
        },
        "metrics": {},
        "summary_tables": [],
        "outputs": {},
        "figure_artifacts": [],
        "limitations": [],
        "studio_url": None,
    }


def _seal(authority, db, receipt):
    state = {
        "run_id": receipt["run_id"],
        "status": receipt["status"],
        "receipt_sha256": digest(receipt),
        "replay_bundle_sha256": receipt["replay_bundle_sha256"],
    }
    record = {
        "record_id": _record_id(receipt["run_id"], receipt["status"]),
        "kind": "execution_event",
        "event": "mcp_terminal_receipt",
        "state": state,
        "state_sha256": digest(state),
    }
    authority._append_execution_record(db, receipt["session_id"], record)


def _terminal_receipt(row, lane):
    receipt = _base(row, lane)
    receipt["limitations"] = ["The run produced no accepted scientific output."]
    receipt["replay_bundle_sha256"] = digest(
        {
            k: row[k]
            for k in (
                "session_id",
                "run_id",
                "plan_id",
                "plan_sha256",
                "status",
                "cleanup_state",
            )
        }
    )
    return receipt


def seal_terminal(authority, db, row):
    if row["status"] not in {"failed", "cancelled"}:
        return
    plan = db.execute(
        "SELECT execution_lane FROM mcp_frozen_plans WHERE session_id=? "
        "AND plan_id=? AND plan_sha256=?",
        (row["session_id"], row["plan_id"], row["plan_sha256"]),
    ).fetchone()
    if plan is None:
        raise OperationBlocked("plan_identity_mismatch")
    receipt = _terminal_receipt(row, plan[0])
    existing = db.execute(
        "SELECT * FROM scientific_records WHERE lineage_id=? AND record_id=?",
        (row["session_id"], _record_id(row["run_id"], row["status"])),
    ).fetchone()
    if existing is None:
        _seal(authority, db, receipt)
    elif _row(existing)["record"]["state"]["receipt_sha256"] != digest(receipt):
        raise OperationBlocked("plan_identity_mismatch")


def _probability_bounds(files):
    """Verify the full numerical family of each declared Parquet result."""
    import numpy as np
    import pyarrow.parquet as pq

    observed = 0
    for ref, path, _ in files.values():
        if ref["kind"] != "table" or ref["format"] != "parquet":
            continue
        table = pq.ParquetFile(path)
        names = set(table.schema_arrow.names)
        p = next((key for key in ("p_value", "pvalue", "p") if key in names), None)
        q = next(
            (
                key
                for key in ("q_value", "padj", "adjusted_p_value", "q")
                if key in names
            ),
            None,
        )
        if p is None or q is None:
            continue
        for batch in table.iter_batches(batch_size=2048, columns=[p, q]):
            for raw, adjusted in zip(
                *(c.to_pylist() for c in batch.columns), strict=True
            ):
                if raw is None and adjusted is None:
                    continue
                if not (
                    type(raw) in {int, float}
                    and type(adjusted) in {int, float}
                    and np.isfinite(raw)
                    and np.isfinite(adjusted)
                    and 0 <= raw <= adjusted <= 1
                ):
                    return "failed"
                observed += 1
    return "verified" if observed else "not_evaluated"


def publish(ctx, db, row, files, result, directory):
    from .context import store_artifact

    plan = row["plan"]
    sid = row["session_id"]
    receipt = _base(row, plan["execution_lane"])
    receipt.update(
        claim_tier="L1_execution_verified",
        metrics=result["metrics"],
        summary_tables=result["tables"],
        limitations=result["limitations"],
    )
    files = {
        port: (
            {
                **ref,
                "artifact_id": "out-" + digest([row["run_id"], port, ref["sha256"]]),
            },
            path,
            spec,
        )
        for port, (ref, path, spec) in files.items()
    }
    bounds = _probability_bounds(files)
    receipt["probability_bounds_status"] = bounds
    if bounds != "not_evaluated":
        receipt["critic_audit"] = {
            "status": "passed" if bounds == "verified" else "flagged",
            "critic_origin": "deterministic_verifier",
            "findings": ["Checked the retained probability and adjustment columns."],
        }
    classification = (
        "private"
        if ctx.protected(sid, plan["session_roots"], db)
        else plan["data_classification"]
    )
    for port, (ref, path, spec) in files.items():
        ctx._enroll_source(db, sid, ref, classification, parents=plan["session_roots"])
        store_artifact(
            db,
            sid,
            ref,
            path,
            classification,
            {
                "source_sha256": ref["sha256"],
                "parent_sha256s": plan["session_roots"],
                "execution_lane": plan["execution_lane"],
                "exploratory_only": plan["exploratory_only"],
                "clinical_release_allowed": False,
                "contains_expression_matrix": ref["kind"] == "expression_matrix",
            },
            row["run_id"],
        )
        receipt["outputs"][port] = ref
        if ref["kind"] == "figure":
            sidecar = files[spec["sidecar_port"]][0]
            if sidecar["kind"] != "plot_sidecar":
                raise OperationBlocked("scientific_refusal")
            receipt["figure_artifacts"].append(
                {
                    "artifact": ref,
                    "sidecar": sidecar,
                    "verification_state": "produced_unverified",
                }
            )
    manifest = {
        "plan_sha256": row["plan_sha256"],
        "session_id": sid,
        "run_id": row["run_id"],
        "input_bindings": plan["input_bindings"],
        "outputs": receipt["outputs"],
        "execution_lane": plan["execution_lane"],
        "oci_isolated": True,
        "claim_tier": "L1_execution_verified",
        "exploratory_only": plan["exploratory_only"],
        "clinical_release_allowed": False,
    }
    receipt["replay_bundle_sha256"] = digest(manifest)
    with (directory / "manifest.json").open("xb") as handle:
        handle.write(canonical(manifest))
    (directory / "manifest.json").chmod(0o400)
    _seal(ctx.authority, db, receipt)
    ctx.event(
        db,
        sid,
        "mcp_outputs_accepted",
        {
            "run_id": row["run_id"],
            "receipt_sha256": digest(receipt),
            "clinical_release_allowed": False,
        },
    )
    db.execute(
        "UPDATE mcp_sessions SET epistemic_state=? WHERE session_id=?",
        ("E5" if plan["epistemic_purpose"] == "confirmatory" else "E2", sid),
    )
    return receipt


def _terminal_body(ctx, row, plan, record):
    if row["status"] == "completed":
        receipt = json.loads(row["receipt_json"])
        manifest = read_bounded_file(
            ctx.workspace / "runs" / row["run_id"] / "control-plane" / "manifest.json",
            2**20,
        )

        if hashlib.sha256(manifest).hexdigest() != record["replay_bundle_sha256"]:
            raise OperationBlocked("plan_identity_mismatch")
    else:
        receipt = _terminal_receipt(row, plan["execution_lane"])
    return receipt


def verified_receipt(ctx, sid, rid, db, *, local_offline=False, allow_stale=False):
    if ctx.protected(sid, db=db) and not (local_offline and ctx.offline_only):
        raise OperationBlocked(PRIVATE)
    row = db.execute(
        "SELECT * FROM scientific_daemon_runs WHERE run_id=? AND session_id=?",
        (rid, sid),
    ).fetchone()
    if row is None:
        raise OperationBlocked("input_scope_violation")
    status = row["status"]
    if status not in {"completed", "failed", "cancelled"}:
        raise OperationBlocked("awaiting_information")
    retained = db.execute(
        "SELECT * FROM scientific_records WHERE lineage_id=? AND record_id=?",
        (sid, _record_id(rid, status)),
    ).fetchone()
    if retained is None:
        raise OperationBlocked("plan_identity_mismatch")
    record = _row(retained)["record"]["state"]
    plan = ctx.jobs.plan(sid, row["plan_id"])
    if digest(plan) != row["plan_sha256"]:
        raise OperationBlocked("plan_identity_mismatch")
    ctx.check_embargo(plan.get("session_roots", ()), db)
    receipt = _terminal_body(ctx, row, plan, record)
    if (
        digest(receipt) != record["receipt_sha256"]
        or receipt["clinical_release_allowed"] is not False
        or any(
            receipt[key] != row[key]
            for key in ("session_id", "run_id", "plan_id", "plan_sha256", "status")
        )
    ):
        raise OperationBlocked("plan_identity_mismatch")
    for ref in receipt["outputs"].values():
        artifact = ctx.artifact(
            sid, ref["artifact_id"], ref["sha256"], db, allow_stale=allow_stale
        )
        ctx.check_source(db, sid, ref["sha256"])
        if (
            hash_bounded_file(Path(artifact["path"]), ref["size_bytes"])
            != ref["sha256"]
        ):
            raise OperationBlocked("plan_identity_mismatch")
    return receipt


def get_run_receipt(ctx, args):
    sid = args["session_id"]
    # A client can reuse the digest of this exact document in post-evidence
    # literature bindings, even after the daemon's loopback port has changed.
    return ctx.authority.daemon_read(
        lambda db: verified_receipt(ctx, sid, args["run_id"], db)
    )
