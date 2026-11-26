"""Publish retained reviewed bulk fits through mutation paths."""

import json
import re
import sqlite3
import time
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


def _publish(workspace, row):
    from . import plugin_mcp as mcp

    plan = JobStore._plan(row)
    sid, rid, plan_sha = plan["session_id"], row["run_id"], row["plan_sha256"]
    verify_frozen_plan(plan, sid, row["plan_id"], plan_sha)
    subject = json.loads(row["stage_json"]).get("bulk_step_sha256")
    if (
        not isinstance(subject, str)
        or not re.fullmatch(r"[0-9a-f]{64}", subject)
        or not re.fullmatch(r"[0-9a-f]{32}", rid)
    ):
        return
    with mcp.studio_job_readback(workspace) as jobs:
        if jobs is None or JobStore.protected_inputs(jobs, plan):
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
    with closing(sqlite3.connect(uri, uri=True, timeout=10)) as pinned, pinned:
        pinned.row_factory = sqlite3.Row
        pinned.execute("BEGIN IMMEDIATE")
        active = pinned.execute(_CANDIDATES + " AND run.run_id=?", (rid,)).fetchone()
        if (
            active is None
            or active["plan_sha256"] != plan_sha
            or json.loads(active["stage_json"]).get("bulk_step_sha256") != subject
        ):
            raise OperationBlocked("bulk_receipt_candidate_changed")
        if JobStore.protected_inputs(pinned, JobStore._plan(active)):
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


_PUBLISH_BACKOFF = (0.05, 0.2)


def _retry_busy(action, *args):
    for delay in (*_PUBLISH_BACKOFF, None):
        try:
            return action(*args)
        except sqlite3.OperationalError as error:
            code = getattr(error, "sqlite_errorcode", 0) & 0xFF
            if delay is None or code not in {
                sqlite3.SQLITE_BUSY,
                sqlite3.SQLITE_LOCKED,
            }:
                raise
            time.sleep(delay)


def _candidate_batch(workspace, session_id, step_subject, after):
    from .plugin_mcp import studio_job_readback

    sql, params = _CANDIDATES + " AND run.run_id > ?", (after,)
    if session_id is not None:
        sql += " AND run.session_id=?"
        params += (session_id,)
    if step_subject is not None:
        sql += (
            " AND CASE WHEN json_valid(run.stage_json) THEN "
            "json_extract(run.stage_json, '$.bulk_step_sha256') END = ?"
        )
        params += (step_subject,)
    with studio_job_readback(workspace) as jobs:
        if jobs is None:
            return []
        return jobs.execute(sql + " ORDER BY run.run_id LIMIT 8", params).fetchall()


def _publication_failure(error):
    if isinstance(error, sqlite3.OperationalError):
        code = getattr(error, "sqlite_errorcode", 0) & 0xFF
        if code in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}:
            return "bulk_receipt_publication_retry_required"
    return "bulk_receipt_publication_unavailable"


def publish_reviewed_bulk_receipts(
    workspace: Path,
    session_id: str | None = None,
    *,
    strict: bool = False,
    step_subject: str | None = None,
) -> None:
    """Publish bounded candidate batches, with strict errors for completed fits."""
    if not (workspace / "methods" / LEDGER_NAME).is_file():
        return
    after = ""
    failure = None
    try:
        while rows := _retry_busy(
            _candidate_batch, workspace, session_id, step_subject, after
        ):
            after = rows[-1]["run_id"]
            # The enumeration transaction has closed before publication can write.
            for row in rows:
                try:
                    _retry_busy(_publish, workspace, row)
                except _UNAVAILABLE as error:
                    if failure is None:
                        failure = _publication_failure(error)
    except _UNAVAILABLE as error:
        if failure is None:
            failure = _publication_failure(error)
    if strict and failure is not None:
        raise OperationBlocked(failure)


def completed_operator_bulk_subject(flow, *, generation=None) -> str | None:
    """Read the current durable outcome so a mutation can resume publication."""
    from .workbench_interactive import _OPERATOR_BULK_STEP

    step = _OPERATOR_BULK_STEP
    with flow._read_connection() as db:
        state = flow._state(db)
        if step not in state.nodes or (
            generation is not None and state.generations[step] != generation
        ):
            return None
        outcome = state.outcome(step)
        if outcome is None or outcome["status"] != "succeeded":
            return None
        return digest(state.nodes[step].model_dump(mode="json"))
