"""Scientific authority migration and outbox fault controls."""
import sqlite3
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

import pytest

from devbench.engine.workflow_operations import (
    OperationBlocked,
    OperationConflict,
    WorkflowOperationLedger,
)


def _binding():
    return {"kind": "binding", "record_id": "binding-a", **dict.fromkeys(
        ("cohort_sha256", "design_sha256", "procedure_sha256", "family_allocation_sha256",
         "exposure_basis_sha256", "policy_sha256"), "a" * 64)}


def test_unpatched_sqlite_blocks_scientific_wal_before_creation(tmp_path, monkeypatch):
    monkeypatch.setattr(sqlite3, "sqlite_version_info", (3, 43, 1))
    path = tmp_path / "operations.sqlite"
    with pytest.raises(OperationConflict, match="patched SQLite"):
        WorkflowOperationLedger(path, scientific=True)
    assert not path.exists()


@pytest.mark.wal
@pytest.mark.parametrize("replacement", ["absent", "regular", "hardlink", "symlink"])
def test_wal_sidecar_rechecks_unlinked_inode(tmp_path, monkeypatch, replacement):
    database = tmp_path / "ledger.sqlite"
    ledger = WorkflowOperationLedger(database, scientific=True)
    wal = database.with_name(database.name + "-wal")
    other = tmp_path / "other"
    other.touch()
    original = Path.lstat
    observations = 0

    def lstat(path):
        nonlocal observations
        if path == wal:
            observations += 1
            if observations == 1:
                return SimpleNamespace(st_mode=original(other).st_mode, st_nlink=0)
        return original(path)

    if replacement == "regular":
        wal.touch()
    elif replacement == "hardlink":
        wal.hardlink_to(other)
    elif replacement == "symlink":
        wal.symlink_to(other)
    monkeypatch.setattr(Path, "lstat", lstat)

    if replacement in {"hardlink", "symlink"}:
        with pytest.raises(OperationConflict, match="Unsafe scientific WAL sidecar"):
            ledger._validate_wal_sidecars()
    else:
        ledger._validate_wal_sidecars()
    assert observations == 2


@pytest.mark.wal
def test_migration_preserves_legacy_operations_and_sets_durability(tmp_path):
    path = tmp_path / "operations.sqlite"
    legacy = WorkflowOperationLedger(path)
    legacy.run("old", "fit", 0, {}, lambda: {"artifact": "old"})
    ledger = WorkflowOperationLedger(path, scientific=True)
    assert ledger.get("old", "fit", 0)["result"] == {"artifact": "old"}
    reopened = WorkflowOperationLedger(path)
    with reopened._database() as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert connection.execute("PRAGMA busy_timeout").fetchone()[0] == 10000
        assert connection.execute("PRAGMA synchronous").fetchone()[0] == 2
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 1
    row = reopened.scientific.commit("lineage", expected_revision=0, record=_binding())
    assert row["revision"] == 1
    assert reopened.scientific.pending("lineage") == [row]


@pytest.mark.wal
def test_authority_binds_dependencies_holds_and_unique_outputs(tmp_path):
    ledger = WorkflowOperationLedger(tmp_path / "operations.sqlite", scientific=True)
    authority = ledger.scientific
    start = authority.commit("lineage", expected_revision=0, record=_binding())
    assert authority.commit("lineage", expected_revision=0, record=_binding()) == start
    with pytest.raises(OperationBlocked, match="scientific_lineage"):
        ledger.run("lineage", "fit", 0, {}, lambda: pytest.fail("legacy path dispatched"))
    grant = {"record_id": "grant-a", "kind": "grant", "binding_id": "binding-a",
             "grant_sha256": "a" * 64, "dependencies": [
                 {"record_id": "binding-a", "kind": "binding", "sha256": "0" * 64}]}
    with pytest.raises(OperationConflict, match="dependency"):
        authority.commit("lineage", expected_revision=1, record=grant)
    grant["dependencies"][0]["sha256"] = start["sha256"]
    authority.commit("lineage", expected_revision=1, record=grant)
    hold = {"record_id": "hold-a", "kind": "hold", "binding_id": "binding-a", "reason_code": "review"}
    authority.commit("lineage", expected_revision=2, record=hold)
    result = {"record_id": "out-a", "kind": "output", "binding_id": "binding-a", "publication_unit": "family-a",
              "artifact_sha256": "b" * 64, "verification_sha256": "c" * 64}
    with pytest.raises(OperationBlocked, match="scientific_attempt_required"):
        authority.commit("lineage", expected_revision=3, record=result)
    with pytest.raises(OperationBlocked, match="scientific_attempt_required"):
        authority.commit("lineage", expected_revision=3, record=result, verify_output=lambda _: True)
    resolution = {"record_id": "resolved-a", "kind": "hold_resolution", "binding_id": "binding-a",
                  "hold_id": "hold-a", "verification_sha256": "d" * 64}
    authority.commit("lineage", expected_revision=3, record=resolution)
    with pytest.raises(OperationBlocked, match="scientific_attempt_required"):
        authority.commit("lineage", expected_revision=5, record={**result, "record_id": "out-b"},
                         verify_output=lambda _: True)
    assert [row["revision"] for row in authority.pending("lineage")] == [1, 2, 3, 4]
    authority.commit("lineage", expected_revision=4, record={**_binding(), "record_id": "binding-b"})
    with pytest.raises(OperationBlocked, match="scientific_attempt_required"):
        authority.commit("lineage", expected_revision=6, record={**result, "record_id": "out-c",
                         "publication_unit": "another"}, verify_output=lambda _: True)


@pytest.mark.wal
def test_outbox_insert_failure_rolls_back_record_and_retries(tmp_path):
    ledger = WorkflowOperationLedger(tmp_path / "operations.sqlite", scientific=True)
    with ledger._database() as connection:
        connection.execute("""CREATE TRIGGER inject_failure BEFORE INSERT ON scientific_outbox
                              BEGIN SELECT RAISE(ABORT, 'test outbox fault'); END""")
    with pytest.raises(OperationConflict):
        ledger.scientific.commit("lineage", expected_revision=0, record=_binding())
    assert ledger.scientific.get("lineage", "binding-a") is None
    assert ledger.scientific.pending("lineage") == []
    with ledger._database() as connection:
        connection.execute("DROP TRIGGER inject_failure")
    assert ledger.scientific.commit("lineage", expected_revision=0, record=_binding())["revision"] == 1


@pytest.mark.wal
def test_projection_retries_after_acknowledgment_loss(tmp_path):
    from devbench.engine.methods import MethodService

    path = tmp_path / "operations.sqlite"
    ledger = WorkflowOperationLedger(path, scientific=True)
    methods = MethodService(tmp_path / "methods")
    record = ledger.scientific.commit("lineage", expected_revision=0, record=_binding())

    def applied_then_interrupted(event):
        methods.project_scientific_record(event)
        raise RuntimeError("injected projection acknowledgment loss")

    with pytest.raises(RuntimeError, match="acknowledgment"):
        ledger.scientific.drain("lineage", applied_then_interrupted)
    reopened = WorkflowOperationLedger(path)
    assert reopened.scientific.pending("lineage") == [record]
    assert reopened.scientific.drain("lineage", methods.project_scientific_record) == 1
    assert reopened.scientific.pending("lineage") == []
    assert methods.list_records("scientific_projection_event") == [record]
    assert methods.get("scientific_projection_head", "lineage") == record


@pytest.mark.wal
def test_failed_schema_migration_rolls_back_and_partial_schema_fails_closed(tmp_path, monkeypatch):
    from devbench.engine import scientific_authority

    path = tmp_path / "operations.sqlite"
    WorkflowOperationLedger(path)
    with monkeypatch.context() as patch:
        def fail(db):
            db.execute(scientific_authority.SCHEMA["scientific_schema"])
            raise RuntimeError("migration fault")
        patch.setattr(scientific_authority, "install_schema", fail)
        with pytest.raises(RuntimeError, match="migration fault"):
            WorkflowOperationLedger(path, scientific=True)
    with WorkflowOperationLedger(path)._database() as db:
        assert not scientific_authority.check_schema(db)
    WorkflowOperationLedger(path, scientific=True)
    with WorkflowOperationLedger(path)._database() as db:
        db.execute("DROP TABLE scientific_outbox")
    with pytest.raises(OperationConflict, match="Incomplete"):
        WorkflowOperationLedger(path, scientific=True)


@pytest.mark.wal
def test_wal_snapshot_and_bounded_writer_contention(tmp_path):
    import time

    path = tmp_path / "operations.sqlite"
    ledger = WorkflowOperationLedger(path, scientific=True)
    other = WorkflowOperationLedger(path)
    with ledger._database() as reader:
        reader.execute("BEGIN")
        assert reader.execute("SELECT count(*) FROM scientific_records").fetchone()[0] == 0
        other.scientific.commit("lineage", expected_revision=0, record=_binding())
        assert reader.execute("SELECT count(*) FROM scientific_records").fetchone()[0] == 0
    with ledger._database() as writer:
        writer.execute("BEGIN IMMEDIATE")
        assert other.scientific.get("lineage", "binding-a")["revision"] == 1
        start = time.monotonic()
        with pytest.raises(OperationBlocked, match="ledger_busy"):
            other.scientific.commit("another", expected_revision=0, record=_binding())
        assert 9.5 <= time.monotonic() - start < 30
    with other._database() as db:
        assert tuple(db.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()) == (0, 0, 0)


def _exclusive_wal_writer(path):
    writer = sqlite3.connect(path, timeout=0)
    try:
        writer.execute("PRAGMA locking_mode=EXCLUSIVE")
        writer.execute("BEGIN EXCLUSIVE")
    except BaseException:
        writer.close()
        raise
    return writer


@pytest.mark.wal
@pytest.mark.parametrize("read_kind", ["ledger", "authority", "adaptive"])
def test_scientific_reads_retry_transient_wal_busy(tmp_path, monkeypatch, read_kind):
    from devbench.engine import workflow_operations
    from devbench.platform.adaptive_workflow import AdaptiveWorkflow

    ledger = WorkflowOperationLedger(tmp_path / "operations.sqlite", scientific=True)
    ledger.run("legacy", "fit", 0, {}, lambda: {"artifact": "legacy"})
    authority = ledger.scientific
    authority.commit("lineage", expected_revision=0, record=_binding())
    reads = {
        "ledger": lambda: ledger.get("legacy", "fit", 0),
        "authority": lambda: authority.pending("lineage"),
        "adaptive": AdaptiveWorkflow(ledger, "lineage").projection_snapshot,
    }
    read = reads[read_kind]
    expected = read()
    waits = []
    with closing(_exclusive_wal_writer(ledger.path)) as writer:
        def release_on_retry(delay):
            waits.append(delay)
            writer.close()

        monkeypatch.setattr(workflow_operations.time, "sleep", release_on_retry)
        assert read() == expected
    assert len(waits) == 1
    assert 0 < waits[0] < 0.1


@pytest.mark.wal
def test_scientific_read_busy_exhaustion_is_blocked(tmp_path, monkeypatch):
    from devbench.engine import workflow_operations

    ledger = WorkflowOperationLedger(tmp_path / "operations.sqlite", scientific=True)
    authority = ledger.scientific
    authority.commit("lineage", expected_revision=0, record=_binding())
    waits = []
    monkeypatch.setattr(workflow_operations.time, "sleep", waits.append)
    with (
        closing(_exclusive_wal_writer(ledger.path)),
        pytest.raises(OperationBlocked) as blocked,
    ):
        authority.get("lineage", "binding-a")
    assert blocked.value.state == "ledger_busy"
    assert blocked.value.__cause__.sqlite_errorcode & 0xFF == sqlite3.SQLITE_BUSY
    assert len(waits) == 2
    assert all(0 < wait < 0.1 for wait in waits)


@pytest.mark.wal
def test_scientific_read_retry_rechecks_ledger_identity(tmp_path, monkeypatch):
    from devbench.engine import workflow_operations

    ledger = WorkflowOperationLedger(tmp_path / "operations.sqlite", scientific=True)
    authority = ledger.scientific
    authority.commit("lineage", expected_revision=0, record=_binding())
    replacement = WorkflowOperationLedger(
        tmp_path / "replacement.sqlite", scientific=True
    )
    waits = []
    with closing(_exclusive_wal_writer(ledger.path)) as writer:
        def substitute_ledger(delay):
            waits.append(delay)
            writer.close()
            replacement.path.replace(ledger.path)

        monkeypatch.setattr(workflow_operations.time, "sleep", substitute_ledger)
        with pytest.raises(OperationConflict, match="resource identity changed"):
            authority.get("lineage", "binding-a")
    assert len(waits) == 1


@pytest.mark.wal
def test_committed_outbox_survives_abrupt_process_exit_with_sidecars(tmp_path):
    import json
    import subprocess
    import sys

    path = tmp_path / "operations.sqlite"
    code = """
import json, os, sqlite3, sys
from devbench.engine.workflow_operations import WorkflowOperationLedger
ledger = WorkflowOperationLedger(sys.argv[1], scientific=True)
retained = sqlite3.connect(sys.argv[1])
retained.execute('BEGIN')
retained.execute('SELECT count(*) FROM scientific_records').fetchone()
ledger.scientific.commit('lineage', expected_revision=0, record=json.loads(sys.argv[2]))
os._exit(0)
"""
    subprocess.run([sys.executable, "-c", code, str(path), json.dumps(_binding())], check=True, timeout=30)
    assert path.with_name(path.name + "-wal").stat().st_size > 0
    reopened = WorkflowOperationLedger(path, readonly=True)
    assert reopened.scientific.get("lineage", "binding-a")["revision"] == 1
    assert len(reopened.scientific.pending("lineage")) == 1
    with pytest.raises(OperationBlocked, match="readonly"):
        reopened.scientific.drain("lineage", lambda _: pytest.fail("readonly projected"))


@pytest.mark.wal
@pytest.mark.parametrize("change", ["prefix", "snapshot", "missing_snapshot"])
def test_adaptive_checkpoint_replays_tail_and_authenticates_retained_prefix(tmp_path, monkeypatch, change):
    import json

    from devbench.engine import scientific_authority
    from devbench.platform.adaptive_workflow import AdaptiveWorkflow, _State

    monkeypatch.setattr(scientific_authority, "_CHECKPOINT_INTERVAL", 2, raising=False)
    ledger = WorkflowOperationLedger(tmp_path / "operations.sqlite", scientific=True)
    ledger.scientific.commit("lineage", expected_revision=0, record=_binding())
    flow = AdaptiveWorkflow(ledger, "lineage")
    for index in range(5):
        revision = flow.record_basis(f"basis-{index}", "fact", "b" * 64)
        assert revision == index + 2

    reopened = AdaptiveWorkflow(WorkflowOperationLedger(ledger.path, readonly=True), "lineage")
    applied = []
    original_apply = _State.apply

    def observe_tail(state, event, payload):
        applied.append(payload["basis_id"])
        return original_apply(state, event, payload)

    monkeypatch.setattr(_State, "apply", observe_tail)
    with reopened._read_connection() as connection:
        assert list(reopened._state(connection).basis) == [f"basis-{index}" for index in range(5)]
    assert applied == ["basis-4"]

    with ledger._database() as connection:
        if change == "prefix":
            raw = connection.execute("SELECT payload FROM scientific_records WHERE revision=2").fetchone()[0]
            record = json.loads(raw)
            record["state"]["sha256"] = "c" * 64
            record["state_sha256"] = scientific_authority._digest(scientific_authority._json(record["state"]))
            replaced = scientific_authority._record_payload(record)
            checksum = scientific_authority._digest(replaced)
            connection.execute("UPDATE scientific_records SET payload=?,sha256=? WHERE revision=2", (replaced, checksum))
            connection.execute("UPDATE scientific_outbox SET sha256=? WHERE revision=2", (checksum,))
        elif change == "snapshot":
            connection.execute("UPDATE scientific_projection_checkpoints SET state_json='{}'")
        else:
            connection.execute("DELETE FROM scientific_projection_checkpoints")
    with reopened._read_connection() as connection, pytest.raises(OperationConflict, match="checkpoint"):
        reopened._state(connection)


@pytest.mark.wal
def test_legacy_adaptive_history_past_old_ceiling_can_write_checkpoint(tmp_path):
    from devbench.engine.workflow_operations import _digest, _json
    from devbench.platform.adaptive_workflow import AdaptiveWorkflow

    ledger = WorkflowOperationLedger(tmp_path / "operations.sqlite", scientific=True)
    authority = ledger.scientific
    authority.commit("lineage", expected_revision=0, record=_binding())
    state = {"basis_id": "legacy", "kind": "fact", "sha256": "b" * 64}
    state_sha256 = _digest(_json(state))
    with authority._execution_tx("lineage") as connection:
        for index in range(8193):
            authority._append_execution_record(connection, "lineage", {
                "kind": "adaptive_event", "record_id": f"adaptive-legacy-{index}",
                "event": "basis", "state": state, "state_sha256": state_sha256,
            })
    flow = AdaptiveWorkflow(ledger, "lineage")
    assert flow.record_basis("new", "fact", "c" * 64) == 8195
    with flow._read_connection() as connection:
        checkpoint = connection.execute("SELECT checkpoint_revision FROM scientific_projection_checkpoints").fetchone()
        assert checkpoint[0] == 8194
        assert list(flow._state(connection).basis) == ["legacy", "new"]


@pytest.mark.wal
def test_literature_and_review_checkpoints_keep_distinct_histories_and_retry(
    workspace_tmp_path, monkeypatch
):
    from devbench.analysis import literature_adapter as literature
    from devbench.analysis.evidence_access import EvidenceBroker
    from devbench.analysis.scientific_review_holds import ReviewHoldLifecycle
    from devbench.engine import scientific_authority

    monkeypatch.setattr(scientific_authority, "_CHECKPOINT_INTERVAL", 2)
    ledger = WorkflowOperationLedger(
        workspace_tmp_path / "operations.sqlite", scientific=True
    )
    authority = ledger.scientific
    authority.commit("lineage", expected_revision=0, record=_binding())
    review = ReviewHoldLifecycle(authority, "lineage")
    service = literature.LiteratureService(
        authority, "lineage", workspace_tmp_path / "literature",
        broker=EvidenceBroker(authority),
        capability={"plugin_root": str(workspace_tmp_path / "offline-client")},
    )
    for index in range(4):
        claim_id = f"claim-{index}"
        review.register_entities([{"entity_id": claim_id, "kind": "claim"}])
        service.bind_claim({"claim_id": claim_id, "claim_sha256": "a" * 64,
            "evidence_sha256": "b" * 64, "review_binding_sha256": "c" * 64, "obligation_id": "literature",
            "context": dict.fromkeys(("organism", "tissue", "intervention", "direction", "uncertainty"))})

    with ledger._database() as connection:
        checkpoints = connection.execute(
            "SELECT stream, checkpoint_revision FROM scientific_projection_checkpoints ORDER BY stream").fetchall()
        assert [tuple(row) for row in checkpoints] == [("literature", 5), ("review", 4)]
        originals = [scientific_authority._row(row) for row in connection.execute(
            "SELECT * FROM scientific_records WHERE kind='execution_event' ORDER BY revision")]
        assert ReviewHoldLifecycle.replay_records(
            row for row in originals if row["record"]["event"] == "review_lifecycle") == review.state()
        assert literature.replay_literature_records(
            row["record"] for row in originals if row["record"]["event"] == "literature") == service.state()
        connection.execute("""CREATE TRIGGER test_checkpoint_failure
            BEFORE UPDATE ON scientific_projection_checkpoints
            BEGIN SELECT RAISE(ABORT, 'checkpoint storage failed'); END""")

    # A failed snapshot write cannot leave an unbacked checkpoint anchor, even
    # if the enclosing transaction explicitly handles the local error.
    entity = [{"entity_id": "claim-retry", "kind": "claim"}]
    with authority._execution_tx("lineage") as connection, monkeypatch.context() as patch:
        original_write = review._write
        patch.setattr(review, "_write", lambda *args, **kwargs: original_write(*args, **kwargs, connection=connection))
        with pytest.raises(sqlite3.IntegrityError, match="checkpoint storage failed"):
            review.register_entities(entity)
        assert "claim-retry" not in review.state(connection=connection)["entities"]
        connection.execute("DROP TRIGGER test_checkpoint_failure")
    review.register_entities(entity)
    assert "claim-retry" in review.state()["entities"]


@pytest.mark.wal
def test_same_ledger_transaction_rejects_inactive_or_attached_database(tmp_path):
    from devbench.engine.scientific_authority import require_same_ledger_transaction

    ledger = WorkflowOperationLedger(tmp_path / "operations.sqlite", scientific=True)
    with ledger._database() as connection:
        with pytest.raises(PermissionError, match="same ledger transaction"):
            require_same_ledger_transaction(connection, ledger)
        connection.execute("BEGIN")
        require_same_ledger_transaction(connection, ledger)
    other = WorkflowOperationLedger(tmp_path / "other.sqlite")
    with other._database() as connection:
        connection.execute("ATTACH DATABASE ? AS attached", (str(ledger.path),))
        connection.execute("BEGIN")
        with pytest.raises(PermissionError, match="same ledger transaction"):
            require_same_ledger_transaction(connection, ledger)
