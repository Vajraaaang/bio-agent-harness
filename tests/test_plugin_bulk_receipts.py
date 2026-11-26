"""Publication of verified Studio fits into existing, frozen Plugin runs."""

import json
import sqlite3
from contextlib import contextmanager, nullcontext
from types import SimpleNamespace

import pytest

from devbench.analysis.evidence_access import AccessPolicy, EvidenceBroker
from devbench.engine.workflow_operations import OperationBlocked
from devbench.platform import plugin_mcp as mcp
from devbench.platform.plugin_bulk_receipts import (
    publish_reviewed_bulk_receipts,
    reviewed_bulk_source_id,
)
from devbench.platform.plugin_jobs import digest
from tests.test_scientific_authority_migration import _binding

pytestmark = pytest.mark.wal


@pytest.fixture
def pending_bulk(workspace_tmp_path):
    workspace = workspace_tmp_path / "workspace"
    inputs = workspace_tmp_path / "inputs"
    inputs.mkdir(mode=0o700)
    plugin = mcp.Plugin(workspace, inputs, None)
    jobs = plugin.jobs
    (workspace / "methods").mkdir(mode=0o700, exist_ok=True)
    ledger = mcp._ledger(workspace)
    sid, rid, subject, root = "a" * 32, "c" * 32, "d" * 64, "e" * 64
    frozen = {
        "plan_id": "b" * 32,
        "session_id": sid,
        "data_classification": "synthetic",
        "operation": "bulk_de",
        "profiles": [{"sha256": root}],
        "session_intake": {"source_sha256": root, "design_sha256": "f" * 64},
    }
    plan_id, plan_sha = jobs.freeze(frozen)
    with jobs._write() as db:
        db.execute(
            """INSERT INTO plugin_runs VALUES
               (?, ?, ?, ?, 'awaiting_review', NULL, ?, NULL, NULL, 1, 2, 2)""",
            (rid, sid, plan_id, plan_sha, json.dumps({"bulk_step_sha256": subject})),
        )
    ledger.scientific.commit(sid, expected_revision=0, record=_binding())
    broker = EvidenceBroker(ledger.scientific)
    policy = broker.register_policy(
        AccessPolicy("synthetic-policy", 1, allowed_roots=(root,), allow_outcomes=True)
    )
    lineage = mcp._lineage(sid, frozen)
    broker.register_context(
        lineage["context_id"],
        principal_id="scientist",
        lineage_id=sid,
        role="scientist",
        phase="readback",
        policy_sha256=policy,
    )
    broker.register_source(
        lineage["source_id"],
        content_sha256=lineage["intake_sha256"],
        classification="structural",
        roots=(root,),
    )
    receipt = {
        "operation": "bulk_de",
        "session_id": sid,
        "run_id": rid,
        "probability_bounds_status": "verified",
        "clinical_release_allowed": False,
    }
    return workspace, plugin, frozen, subject, receipt, ledger, broker


@pytest.mark.parametrize("already_sealed", [False, True])
def test_publication_recovers_legacy_and_preserves_existing_seal(
    pending_bulk, monkeypatch, already_sealed
):
    workspace, plugin, plan, subject, receipt, ledger, broker = pending_bulk
    rid, sid = receipt["run_id"], plan["session_id"]

    def verified(_ledger, candidate, run, step):
        assert (candidate, run, step) == (plan, rid, subject)
        return receipt, lambda db: None

    monkeypatch.setattr(mcp, "_bulk_verified_fit", verified)
    archive = workspace / "runs" / rid / "replay_bundle" / digest(receipt)
    if already_sealed:
        mcp._seal_bulk_replay(workspace, rid, plan, digest(plan), receipt)
    publish_reviewed_bulk_receipts(workspace)
    stat = (archive / "manifest.json").stat()
    publish_reviewed_bulk_receipts(workspace, sid)
    assert (archive / "manifest.json").stat().st_mtime_ns == stat.st_mtime_ns
    sealed = {
        **receipt,
        "replay_bundle_sha256": digest(mcp._replay_manifest(plan, receipt)),
    }
    source = reviewed_bulk_source_id(rid, sealed)
    saved = ledger._read(lambda db, source=source: broker._source(db, source))
    assert saved["content_sha256"] == digest(sealed)
    assert saved["parents"] == [mcp._lineage(sid, plan)["source_id"]]
    assert saved["tags"] == ["inferential_statistics"]
    assert (
        ledger._read(
            lambda db: db.execute(
                "SELECT count(*) FROM scientific_disclosures"
            ).fetchone()[0]
        )
        == 0
    )
    mcp._verify_replay(archive, digest(plan), sealed)

    def forbid_production(*_args, **_kwargs):
        pytest.fail("Readback attempted to publish or change an access grant")

    monkeypatch.setattr(mcp, "_seal_bulk_replay", forbid_production)
    for method in ("register_policy", "register_context", "register_source"):
        monkeypatch.setattr(EvidenceBroker, method, forbid_production)
    args = mcp.Run(session_id=sid, run_id=rid)
    observed = plugin.receipt(args)
    assert {key: observed[key] for key in sealed} == sealed
    assert plugin.receipt(args) == observed


@pytest.mark.parametrize("change", ["stale_fit", "reclassified"])
def test_eligibility_change_before_commit_cannot_publish(
    pending_bulk, monkeypatch, change
):
    workspace, plugin, plan, _, receipt, ledger, _ = pending_bulk

    def verified(*_):
        if change == "reclassified":
            plugin.jobs.freeze(
                {**plan, "plan_id": "9" * 32, "data_classification": "private"}
            )

        def current(db):
            assert db.in_transaction
            if change == "stale_fit":
                raise OperationBlocked("bulk_fit_generation_changed")

        return receipt, current

    monkeypatch.setattr(mcp, "_bulk_verified_fit", verified)
    publish_reviewed_bulk_receipts(workspace)
    assert not (workspace / "runs" / receipt["run_id"] / "replay_bundle").exists()
    assert not ledger._read(
        lambda db: db.execute(
            "SELECT 1 FROM scientific_access_sources "
            "WHERE identity LIKE 'plugin-receipt-%'"
        ).fetchone()
    )


def test_unrelated_workspace_stays_empty(workspace_tmp_path):
    publish_reviewed_bulk_receipts(workspace_tmp_path)
    assert list(workspace_tmp_path.iterdir()) == []


def test_strict_publication_retries_after_closed_read_and_recovers(
    pending_bulk, monkeypatch
):
    from devbench.platform import plugin_bulk_receipts as publication

    workspace, _, plan, *_ = pending_bulk
    readback = mcp.studio_job_readback
    reading = False
    attempts = 0

    @contextmanager
    def tracked(path):
        nonlocal reading
        with readback(path) as db:
            reading = True
            try:
                yield db
            finally:
                reading = False

    def publish(_workspace, _row):
        nonlocal attempts
        assert not reading
        attempts += 1
        if attempts <= 3:
            error = sqlite3.OperationalError("private path in lock error")
            error.sqlite_errorcode = sqlite3.SQLITE_BUSY
            raise error

    monkeypatch.setattr(mcp, "studio_job_readback", tracked)
    monkeypatch.setattr(publication, "_publish", publish)
    monkeypatch.setattr(publication, "_PUBLISH_BACKOFF", (0, 0))
    with pytest.raises(
        OperationBlocked, match="bulk_receipt_publication_retry_required"
    ) as error:
        publish_reviewed_bulk_receipts(workspace, plan["session_id"], strict=True)
    assert error.value.__cause__ is None
    assert attempts == 3
    publish_reviewed_bulk_receipts(workspace, plan["session_id"], strict=True)
    assert attempts == 4


@pytest.mark.parametrize("recovery", ["advance", "studio"])
def test_advance_and_retry_publish_the_retained_fit(
    pending_bulk, monkeypatch, recovery
):
    from devbench.platform import (
        plugin_bulk_receipts as publication,
    )
    from devbench.platform import (
        workbench_adaptive,
        workflow_actions,
        workflow_entrypoints,
    )

    workspace, plugin, plan, subject, receipt, ledger, _ = pending_bulk
    sid, rid = plan["session_id"], receipt["run_id"]
    step = "operator-bulk-de"
    step_binding = {"step_id": step}
    superseded_id, superseded_sha = plugin.jobs.freeze({**plan, "plan_id": "9" * 32})
    with plugin.jobs._write() as db:
        db.execute(
            "UPDATE plugin_runs SET stage_json=? WHERE run_id=?",
            (json.dumps({"bulk_step_sha256": digest(step_binding)}), rid),
        )
        db.execute(
            """INSERT INTO plugin_runs VALUES
               (?, ?, ?, ?, 'awaiting_review', NULL, ?, NULL, NULL, 1, 2, 2)""",
            (
                "0" * 32,
                sid,
                superseded_id,
                superseded_sha,
                json.dumps({"bulk_step_sha256": subject}),
            ),
        )
    state = SimpleNamespace(
        nodes={step: SimpleNamespace(model_dump=lambda **_: step_binding)},
        generations={step: 1},
        outcome=lambda _: None,
    )
    flow = SimpleNamespace(
        lineage_id=sid,
        _state=lambda _: state,
        _read_connection=nullcontext,
    )
    planner = SimpleNamespace(
        methods=SimpleNamespace(root=workspace / "methods"),
        adaptive_workflow=lambda *_args, **_kwargs: flow,
    )
    completed = {"status": "succeeded"}
    dispatches = []

    def advance(**_kwargs):
        if state.outcome(step) is not None:
            return {"steps_dispatched": 0}
        dispatches.append(step)
        state.outcome = lambda _: completed
        return {"steps_dispatched": 1}

    monkeypatch.setattr(
        workflow_entrypoints,
        "WorkflowActions",
        lambda *_a, **_kw: SimpleNamespace(
            adaptive_review_admission=None,
            adaptive_diagnostic_admission=None,
        ),
    )
    monkeypatch.setattr(
        workflow_actions,
        "create_rna_workflow",
        lambda **_kw: SimpleNamespace(advance=advance),
    )

    def verified(_ledger, _plan, run_id, step_subject):
        assert (run_id, step_subject) == (rid, digest(step_binding))
        return receipt, lambda db: None

    monkeypatch.setattr(mcp, "_bulk_verified_fit", verified)
    monkeypatch.setattr(publication, "_PUBLISH_BACKOFF", ())
    publish = publication._publish

    def locked(*_):
        error = sqlite3.OperationalError("internal SQLite diagnostic")
        error.sqlite_errorcode = sqlite3.SQLITE_BUSY
        raise error

    monkeypatch.setattr(publication, "_publish", locked)
    with pytest.raises(
        OperationBlocked, match="bulk_receipt_publication_retry_required"
    ):
        workflow_entrypoints.advance_adaptive_workflow(
            planner,
            sid,
            actor="test",
            executor=object(),
            ledger=ledger,
            allow_data_execution=True,
        )
    assert dispatches == [step]
    assert state.outcome(step) == completed
    monkeypatch.setattr(publication, "_publish", publish)
    if recovery == "advance":
        recovered = workflow_entrypoints.advance_adaptive_workflow(
            planner,
            sid,
            actor="test",
            executor=object(),
            ledger=ledger,
            allow_data_execution=True,
        )
    else:
        monkeypatch.setattr(workbench_adaptive, "_graph_revision", lambda _: 2)
        recovered = workbench_adaptive._run_operator_step(
            SimpleNamespace(workspace=workspace),
            None,
            flow,
            {"step_id": step, "generation": 1},
            2,
        )
    assert recovered["steps_dispatched"] == 0
    assert dispatches == [step]
    assert plugin.receipt(mcp.Run(session_id=sid, run_id=rid))["run_id"] == rid


def test_startup_retains_earlier_seal_when_verified_fit_receipt_changes(
    pending_bulk, monkeypatch
):
    workspace, plugin, plan, _, receipt, ledger, broker = pending_bulk
    sid, rid = plan["session_id"], receipt["run_id"]
    updated = {**receipt, "review_confirmation_sha256": "f" * 64}
    for verified in (receipt, updated):
        monkeypatch.setattr(
            mcp,
            "_bulk_verified_fit",
            lambda *_, result=verified: (result, lambda db: None),
        )
        publish_reviewed_bulk_receipts(workspace)
    for verified in (receipt, updated):
        manifest = mcp._replay_manifest(plan, verified)
        sealed = {**verified, "replay_bundle_sha256": digest(manifest)}
        archive = workspace / "runs" / rid / "replay_bundle" / digest(verified)
        mcp._verify_replay(archive, digest(plan), sealed)
        source = reviewed_bulk_source_id(rid, sealed)
        assert ledger._read(lambda db, source=source: broker._source(db, source))[
            "content_sha256"
        ] == digest(sealed)
    observed = plugin.receipt(mcp.Run(session_id=sid, run_id=rid))
    assert (
        observed["review_confirmation_sha256"] == updated["review_confirmation_sha256"]
    )
