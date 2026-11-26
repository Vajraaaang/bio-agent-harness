"""Publication of verified Studio fits into existing, frozen Plugin runs."""

import json

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
    saved = ledger._read(lambda db: broker._source(db, source))
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
