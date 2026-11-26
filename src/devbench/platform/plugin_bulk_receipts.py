"""Publish a reviewed Studio bulk fit before a Plugin receipt is requested."""

import json
import re
import sqlite3
from contextlib import closing
from pathlib import Path

from devbench.analysis.evidence_access import EvidenceBroker
from devbench.engine.scientific_authority import verify_frozen_plan
from devbench.engine.workflow_operations import LEDGER_NAME, OperationBlocked

from .plugin_jobs import MAX_DOCUMENT_BYTES, JobStore, digest

_CANDIDATES = f"""
    SELECT plan.*, run.run_id, run.stage_json
    FROM plugin_runs AS run JOIN plugin_plans AS plan
      ON (run.plan_id, run.session_id, run.plan_sha256)
       = (plan.plan_id, plan.session_id, plan.plan_sha256)
    WHERE run.status = 'awaiting_review'
      AND octet_length(run.stage_json) <= {MAX_DOCUMENT_BYTES}
      AND octet_length(plan.plan_json) <= {MAX_DOCUMENT_BYTES}
      AND CASE WHEN json_valid(plan.plan_json)
               THEN json_extract(plan.plan_json, '$.operation') END = 'bulk_de'
"""
_UNAVAILABLE = (
    OSError,
    sqlite3.Error,
    RuntimeError,
    ValueError,
    TypeError,
    AttributeError,
    LookupError,
    ImportError,
)


def reviewed_bulk_source_id(run_id: str, sealed_receipt: dict) -> str:
    return f"plugin-receipt-{run_id}-{digest(sealed_receipt)[:24]}"


def _publish(workspace, jobs, row):
    from . import plugin_mcp as mcp

    plan = JobStore._plan(row)
    sid, rid, plan_sha = plan["session_id"], row["run_id"], row["plan_sha256"]
    verify_frozen_plan(plan, sid, row["plan_id"], plan_sha)
    subject = json.loads(row["stage_json"]).get("bulk_step_sha256")
    if (
        not isinstance(subject, str)
        or not re.fullmatch(r"[0-9a-f]{64}", subject)
        or not re.fullmatch(r"[0-9a-f]{32}", rid)
        or JobStore.protected_inputs(jobs, plan)
    ):
        return
    ledger = mcp._ledger(workspace)
    broker = EvidenceBroker.open_existing(ledger.scientific)
    lineage, roots = mcp._lineage(sid, plan), mcp._roots(plan)
    broker.check_delivery(
        lineage["context_id"], lineage["source_id"], lineage_id=sid, role="scientist"
    )
    ledger._read(lambda db: broker._query_dependencies(db, roots))
    receipt, current = mcp._bulk_verified_fit(ledger, plan, rid, subject)
    tags = mcp._receipt_tags(receipt)
    sealed = {
        **receipt,
        "replay_bundle_sha256": digest(mcp._replay_manifest(plan, receipt)),
    }
    source = reviewed_bulk_source_id(rid, sealed)
    dependencies = tuple(dict.fromkeys((*roots, *mcp.studio_evidence_digests(sealed))))

    # Lock the job's public custody at final registration, in the same order
    # as JobStore.frozen_policy. Keep potentially slow fit verification outside.
    uri = (workspace / "plugin-jobs.sqlite3").as_uri() + "?mode=rw"
    with closing(sqlite3.connect(uri, uri=True, timeout=1)) as pinned, pinned:
        pinned.row_factory = sqlite3.Row
        pinned.execute("BEGIN IMMEDIATE")
        active = pinned.execute(_CANDIDATES + " AND run.run_id=?", (rid,)).fetchone()
        if (
            active is None
            or active["plan_sha256"] != plan_sha
            or json.loads(active["stage_json"]).get("bulk_step_sha256") != subject
            or JobStore.protected_inputs(pinned, JobStore._plan(active))
        ):
            return
        context = mcp._receipt_context(broker, plan, lineage, tags)

        def finish(db):
            current(db)
            broker._query_dependencies(db, dependencies)
            parent = broker._source(db, lineage["source_id"])
            if parent["content_sha256"] != lineage["intake_sha256"] or set(
                parent["roots"]
            ) != set(roots):
                raise OperationBlocked("receipt_identity_mismatch")
            broker._check_delivery(
                db, lineage["context_id"], lineage["source_id"], sid, "scientist"
            )
            broker._check_delivery(db, context, source, sid, "scientist")
            if mcp._seal_bulk_replay(workspace, rid, plan, plan_sha, receipt) != sealed:
                raise OperationBlocked("replay_bundle_identity_mismatch")

        broker.register_source(
            source,
            content_sha256=digest(sealed),
            roots=roots,
            parents=(lineage["source_id"],),
            classification="target_outcome",
            tags=tags,
            _guard=finish,
        )


def publish_reviewed_bulk_receipts(workspace: Path, session_id: str | None = None):
    """Recover eligible old runs, or publish fits for the operator's session."""
    from .plugin_mcp import studio_job_readback

    if not (workspace / "methods" / LEDGER_NAME).is_file():
        return
    sql, params = _CANDIDATES, ()
    if session_id is not None:
        sql += " AND run.session_id=?"
        params = (session_id,)
    try:
        with studio_job_readback(workspace) as jobs:
            if jobs is None:
                return
            # The cursor streams one bounded frozen plan at a time. A pending
            # or superseded review cannot mask a later session that is ready.
            for row in jobs.execute(sql, params):
                try:
                    _publish(workspace, jobs, row)
                except _UNAVAILABLE:
                    continue
    except _UNAVAILABLE:
        return  # Studio also serves workspaces with no Plugin or receipt schema.
