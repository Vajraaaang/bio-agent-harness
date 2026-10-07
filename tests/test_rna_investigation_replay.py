"""Read-only evidence replay is distinct from workflow continuation."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess

import pytest

from devbench.analysis.langgraph_investigations import IndependentRNAWorkflow
from devbench.analysis.method_planner import ScientificMethodPlanner
from devbench.analysis.methods import MethodService
from devbench.analysis.workflow_actions import WorkflowActions
from devbench.analysis.workflow_operations import WorkflowOperationLedger
from tests.test_rna_investigation_recovery import RecoveryFixtureBackend, recovery_setup


def fingerprint(root):
    """Include tree membership, identities, permissions, timestamps and bytes."""
    records = {}
    for path in sorted(Path(root).rglob("*")):
        stat = path.stat()
        records[str(path.relative_to(root))] = (stat.st_ino, stat.st_mode, stat.st_mtime_ns,
            hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None)
    return records


def completed_fixture(tmp_path, allocation=1):
    planner, plan, backend, ledger, actions, workflow = recovery_setup(tmp_path, allocation)
    identity = plan["session_id"]
    assert workflow.start(actions.create_state(identity))["status"] == "review_required"
    terminal = workflow.resume(identity)
    assert terminal["status"] == "abstained"
    checkpoint = workflow.checkpoint_path
    workflow.close()
    return planner, identity, backend, ledger, actions, checkpoint, terminal


def replay_actions(planner, ledger, actions):
    methods = MethodService(planner.methods.root, readonly=True)
    reader = WorkflowOperationLedger(ledger.path, readonly=True)
    reader_planner = ScientificMethodPlanner(methods)
    backend = RecoveryFixtureBackend(methods, actions.selector)
    backend.bind_ledger(reader)
    # Authority is deliberately absent when reading historical evidence.
    readonly_actions = WorkflowActions(reader_planner, reader, actor="offline-replay",
        selector=actions.selector, investigation_policy=deepcopy(actions.investigation_policy),
        investigation_evidence=deepcopy(actions.investigation_evidence))
    return readonly_actions, backend


def deny_effects(monkeypatch, actions, backend):
    effects = []

    def forbidden(*args, **kwargs):
        effects.append((args, kwargs))
        raise AssertionError("Evidence replay attempted execution or a storage mutation")

    from devharness_server.dispatcher import ToolDispatcher
    from devbench.analysis.episode_codex import CodexActionSelector
    from devbench.analysis.method_executor import LocalDockerExecutor
    from langgraph.checkpoint.sqlite import SqliteSaver
    for target, names in (
        (actions.selector, ("select",)),
        (actions, ("perform", "reconcile")),
        (backend, ("investigate", "prepare_requests", "_retain")),
        (actions.planner, ("retrieve_evidence", "run_trial", "attach_model_campaign",
                           "attach_investigation_evidence", "_save")),
        (actions.planner.methods, ("import_artifact", "_put", "reconcile", "run_step", "reproduce")),
        (actions.ledger, ("run", "reconcile", "reserve_model_call", "activate_model_call",
                          "settle_model_call", "reconcile_model_call", "open_campaign", "cancel_campaign")),
        (ToolDispatcher, ("execute",)),
        (LocalDockerExecutor, ("execute",)),
        (CodexActionSelector, ("select",)),
        (subprocess, ("run", "Popen")),
        (SqliteSaver, ("put", "put_writes", "delete_thread")),
    ):
        for name in names:
            if hasattr(target, name):
                monkeypatch.setattr(target, name, forbidden)
    return effects


@pytest.mark.parametrize("allocation", [0, 1, 2])
def test_readonly_replay_opens_existing_stores_and_has_zero_effects(tmp_path, monkeypatch, allocation):
    planner, identity, original, ledger, actions, checkpoint, terminal = completed_fixture(tmp_path, allocation)
    before = fingerprint(tmp_path)
    read_actions, backend = replay_actions(planner, ledger, actions)
    assert read_actions.planner.methods.readonly
    assert read_actions.ledger.readonly
    assert not read_actions.allow_model_execution
    assert read_actions.investigation_authority is None
    effects = deny_effects(monkeypatch, read_actions, backend)
    connections = []
    connect = sqlite3.connect

    def observe_connect(path, *args, **kwargs):
        connections.append(str(path))
        return connect(path, *args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", observe_connect)
    assert hasattr(IndependentRNAWorkflow, "open_replay"), "A read-only independent replay constructor is required"
    with IndependentRNAWorkflow.open_replay(read_actions, checkpoint, investigation_backend=backend) as replay:
        assert replay.replay(identity) == terminal
        assert replay.replay(identity) == terminal
        assert not read_actions.planner._model_campaigns
    assert effects == []
    assert connections and all("mode=ro" in path for path in connections)
    assert fingerprint(tmp_path) == before
    assert len(original.launches) == allocation


def test_readonly_replay_cannot_create_a_missing_checkpoint(tmp_path):
    planner, identity, _, ledger, actions, _, _ = completed_fixture(tmp_path)
    read_actions, backend = replay_actions(planner, ledger, actions)
    absent = tmp_path / "absent-checkpoint.sqlite"
    before = fingerprint(tmp_path)
    assert hasattr(IndependentRNAWorkflow, "open_replay"), "A read-only independent replay constructor is required"
    with pytest.raises((ValueError, OSError, sqlite3.Error), match="exist|available|open|read-only"):
        IndependentRNAWorkflow.open_replay(read_actions, absent, investigation_backend=backend)
    assert not absent.exists()
    assert fingerprint(tmp_path) == before


def test_readonly_replay_rejects_writer_stores_before_continuation(tmp_path):
    planner, identity, backend, ledger, actions, checkpoint, _ = completed_fixture(tmp_path)
    before = fingerprint(tmp_path)
    assert hasattr(IndependentRNAWorkflow, "open_replay"), "A read-only independent replay constructor is required"
    with pytest.raises((ValueError, PermissionError), match="read.only"):
        IndependentRNAWorkflow.open_replay(actions, checkpoint, investigation_backend=backend)
    assert fingerprint(tmp_path) == before


def test_readonly_replay_preserves_uncertainty_and_outstanding_charge(tmp_path, monkeypatch):
    planner, plan, backend, ledger, actions, workflow = recovery_setup(tmp_path)
    identity = plan["session_id"]
    backend.crash_after_launch = True
    interrupted = workflow.start(actions.create_state(identity))
    assert interrupted["status"] == "reconciliation_required"
    charges = deepcopy(ledger.campaign_reservations(identity))
    campaign = deepcopy(ledger.get_campaign(identity))
    assert campaign["unresolved_count"] == 1 and campaign["outstanding_reserved_tokens"] == 40
    checkpoint = workflow.checkpoint_path
    workflow.close()
    before = fingerprint(tmp_path)
    read_actions, reader_backend = replay_actions(planner, ledger, actions)
    effects = deny_effects(monkeypatch, read_actions, reader_backend)
    with IndependentRNAWorkflow.open_replay(read_actions, checkpoint, investigation_backend=reader_backend) as replay:
        assert replay.replay(identity) == interrupted
    assert read_actions.ledger.get_campaign(identity) == campaign
    assert read_actions.ledger.campaign_reservations(identity) == charges
    assert effects == []
    assert fingerprint(tmp_path) == before


def test_replay_is_inert_and_restored_references_grant_no_execution_authority(tmp_path, monkeypatch):
    planner, identity, _, ledger, actions, checkpoint, terminal = completed_fixture(tmp_path)
    read_actions, backend = replay_actions(planner, ledger, actions)
    effects = deny_effects(monkeypatch, read_actions, backend)
    assert hasattr(IndependentRNAWorkflow, "open_replay"), "A read-only independent replay constructor is required"
    with IndependentRNAWorkflow.open_replay(read_actions, checkpoint, investigation_backend=backend) as replay:
        assert replay.replay(identity)["result_refs"] == terminal["result_refs"]
        for operation in (replay.resume, replay.reconcile):
            with pytest.raises((ValueError, PermissionError), match="read.only|replay"):
                operation(identity)
        with pytest.raises((ValueError, PermissionError), match="read.only|replay"):
            replay.start(terminal["parent_cursor"])
    assert effects == []


@pytest.mark.parametrize("artifact", ["dataset", "packet", "capture", "usage", "result", "bundle"])
def test_readonly_replay_rejects_damaged_retained_evidence_without_effects(tmp_path, monkeypatch, artifact):
    planner, identity, original, ledger, actions, checkpoint, terminal = completed_fixture(tmp_path)
    child = terminal["child_ids"][0]
    descriptor = original.read_dispatch(terminal["dispatch_refs"][child])
    outcome = original.reconcile_capture(terminal["dispatch_refs"][child])
    references = {"dataset": terminal["parent_cursor"]["artifact_refs"][0],
        "packet": descriptor["request"]["packet_ref"], "capture": outcome["capture_ref"],
        "usage": outcome["usage_ref"], "result": outcome["result_ref"],
        "bundle": terminal["reconciliation_ref"]}
    damaged = planner.methods.artifact_path(references[artifact]["artifact_id"])
    # This generated test artifact is deliberately damaged; historical sealed
    # evidence must fail verification rather than be silently regenerated.
    damaged.chmod(0o600)
    damaged.write_bytes(damaged.read_bytes() + b"\n")
    damaged.chmod(0o444)
    before = fingerprint(tmp_path)
    read_actions, backend = replay_actions(planner, ledger, actions)
    effects = deny_effects(monkeypatch, read_actions, backend)
    with IndependentRNAWorkflow.open_replay(read_actions, checkpoint, investigation_backend=backend) as replay:
        with pytest.raises((ValueError, RuntimeError, OSError), match="hash|digest|integrity|changed|mismatch|artifact"):
            replay.replay(identity)
    assert effects == []
    assert fingerprint(tmp_path) == before


def test_readonly_replay_rejects_forged_ledger_usage_receipt(tmp_path, monkeypatch):
    planner, identity, _, ledger, actions, checkpoint, _ = completed_fixture(tmp_path)
    with sqlite3.connect(ledger.path) as db:
        reference, encoded = db.execute("SELECT ref,capture_json FROM model_reservations WHERE purpose='independent_investigation'").fetchone()
        receipt = json.loads(encoded)
        receipt["usage"]["normalized_total_tokens"] = 0
        db.execute("UPDATE model_reservations SET capture_json=? WHERE ref=?",
            (json.dumps(receipt), reference))
    before = fingerprint(tmp_path)
    read_actions, backend = replay_actions(planner, ledger, actions)
    effects = deny_effects(monkeypatch, read_actions, backend)
    with IndependentRNAWorkflow.open_replay(read_actions, checkpoint, investigation_backend=backend) as replay:
        with pytest.raises((ValueError, RuntimeError), match="digest|receipt|capture|corrupt|integrity|JSON"):
            replay.replay(identity)
    assert effects == []
    assert fingerprint(tmp_path) == before


def test_readonly_replay_succeeds_with_physical_readonly_databases(tmp_path, monkeypatch):
    planner, identity, _, ledger, actions, checkpoint, terminal = completed_fixture(tmp_path)
    databases = [planner.methods.database, ledger.path, checkpoint]
    previous_modes = [path.stat().st_mode & 0o777 for path in databases]
    for path in databases:
        path.chmod(0o444)
    try:
        before = fingerprint(tmp_path)
        read_actions, backend = replay_actions(planner, ledger, actions)
        effects = deny_effects(monkeypatch, read_actions, backend)
        with IndependentRNAWorkflow.open_replay(read_actions, checkpoint, investigation_backend=backend) as replay:
            assert replay.replay(identity) == terminal
        assert effects == []
        assert fingerprint(tmp_path) == before
    finally:
        for path, mode in zip(databases, previous_modes):
            path.chmod(mode)


@pytest.mark.parametrize("alias", ["symlink", "hardlink"])
def test_readonly_replay_rejects_checkpoint_aliases_without_storage_writes(tmp_path, alias):
    import os
    planner, identity, _, ledger, actions, checkpoint, _ = completed_fixture(tmp_path)
    path = tmp_path / (alias + "-checkpoint.sqlite")
    if alias == "symlink":
        path.symlink_to(checkpoint)
    else:
        os.link(checkpoint, path)
    before = fingerprint(tmp_path)
    read_actions, backend = replay_actions(planner, ledger, actions)
    with pytest.raises((ValueError, OSError), match="symlink|hardlink|link|regular|alias"):
        IndependentRNAWorkflow.open_replay(read_actions, path, investigation_backend=backend)
    assert fingerprint(tmp_path) == before
