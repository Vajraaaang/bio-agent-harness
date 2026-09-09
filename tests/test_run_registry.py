"""Durable local scheduling evidence; no clinical or identity qualification implied."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from devharness_controlled import run_registry


@pytest.fixture
def registry(tmp_path):
    return run_registry.RunRegistry(tmp_path / "registry")


def register(registry, tmp_path, request="request-1"):
    return registry.register(request, "a" * 64, tmp_path / request, "operator", submission_sha256="d" * 64)


def start_through_dispatch(registry, identifier):
    for kind in ("started", "runtime_intent", "container_observed", "launch_authorized", "start_requested"):
        registry.append_event(identifier, kind, {"stage": kind}, "operator")


def test_registration_is_idempotent_with_bound_plan_and_workspace(registry, tmp_path):
    first = register(registry, tmp_path)
    assert first["created"] is True
    assert first["status"] == "registered"
    second = register(registry, tmp_path)
    assert second["created"] is False
    assert second["attempt_id"] == first["attempt_id"]
    assert registry.get(first["attempt_id"])["status"] == "registered"
    with pytest.raises(ValueError, match="conflict"):
        registry.register("request-1", "b" * 64, tmp_path / "request-1", "operator", submission_sha256="d" * 64)
    with pytest.raises(ValueError, match="conflict"):
        registry.register("request-1", "a" * 64, tmp_path / "another", "operator", submission_sha256="d" * 64)
    with pytest.raises(ValueError, match="workspace"):
        registry.register("request-2", "a" * 64, tmp_path / "request-1", "operator", submission_sha256="d" * 64)
    with pytest.raises(ValueError, match="conflict"):
        registry.register("request-1", "a" * 64, tmp_path / "request-1", "operator", submission_sha256="e" * 64)
    assert len(first["attempt_id"]) == 32


def test_parallel_registration_creates_one_attempt(tmp_path):
    def create(_):
        registry = run_registry.RunRegistry(tmp_path / "registry")
        return register(registry, tmp_path)
    with ThreadPoolExecutor(max_workers=8) as pool:
        attempts = list(pool.map(create, range(24)))
    assert sum(attempt["created"] for attempt in attempts) == 1
    assert len({attempt["attempt_id"] for attempt in attempts}) == 1


def test_lifecycle_and_exact_terminal_commit(registry, tmp_path):
    attempt = register(registry, tmp_path)
    identifier = attempt["attempt_id"]
    with pytest.raises(ValueError, match="transition"):
        registry.append_event(identifier, "completed", {"execution_root_hash": "b" * 64}, "operator")
    start_through_dispatch(registry, identifier)
    with pytest.raises(ValueError, match="root"):
        registry.append_event(identifier, "completed", {}, "operator")
    payload = {"execution_root_hash": "b" * 64}
    completed = registry.append_event(identifier, "completed", payload, "operator")
    replay = registry.append_event(identifier, "completed", payload, "operator")
    assert completed == replay
    assert registry.get(identifier)["status"] == "completed"
    assert len(registry.get(identifier)["events"]) == 7
    for kind, body, actor in (("failed", {}, "operator"), ("completed", {"execution_root_hash": "c" * 64}, "operator"), ("completed", payload, "another")):
        with pytest.raises(ValueError, match="terminal"):
            registry.append_event(identifier, kind, body, actor)


@pytest.mark.parametrize("terminal", ["failed", "interrupted"])
def test_registered_can_terminate_without_start(registry, tmp_path, terminal):
    attempt = register(registry, tmp_path)
    registry.append_event(attempt["attempt_id"], terminal, {"reason": "not launched"}, "operator")
    assert registry.get(attempt["attempt_id"])["status"] == terminal
    assert registry.verify_integrity()["valid"]


def test_checkpoint_accepts_preserved_prefix_and_is_read_only(registry, tmp_path):
    attempt = register(registry, tmp_path)
    checkpoint = registry.export_checkpoint()
    before = registry.database_path.read_bytes()
    assert registry.verify_integrity(expected_checkpoint=checkpoint)["valid"]
    assert registry.database_path.read_bytes() == before
    registry.append_event(attempt["attempt_id"], "interrupted", {"reason": "lost worker"}, "operator")
    assert registry.verify_integrity()["valid"]
    anchored = registry.verify_integrity(expected_checkpoint=checkpoint)
    assert anchored["externally_anchored"] is True
    assert anchored["later_event_count"] == 1
    register(registry, tmp_path, "new-attempt")
    assert registry.verify_integrity(expected_checkpoint=checkpoint)["later_attempt_count"] == 1
    assert registry.registry_id == checkpoint["registry_id"]
    assert run_registry.RunRegistry(registry.directory).registry_id == registry.registry_id


def test_changed_checkpoint_prefix_is_rejected(registry, tmp_path):
    register(registry, tmp_path)
    checkpoint = registry.export_checkpoint()
    checkpoint["attempts"][0]["event_root_hash"] = "f" * 64
    body = {key: value for key, value in checkpoint.items() if key != "checkpoint_sha256"}
    checkpoint["checkpoint_sha256"] = hashlib.sha256(run_registry.canonical_json_bytes(body)).hexdigest()
    with pytest.raises(ValueError, match="checkpoint"):
        registry.verify_integrity(expected_checkpoint=checkpoint)


def test_corrupt_checkpoint_and_cross_registry_anchor_are_rejected(registry, tmp_path):
    register(registry, tmp_path)
    checkpoint = registry.export_checkpoint()
    checkpoint["attempt_count"] = True
    with pytest.raises(ValueError, match="checkpoint"):
        registry.verify_integrity(expected_checkpoint=checkpoint)
    other = run_registry.RunRegistry(tmp_path / "other")
    with pytest.raises(ValueError, match="checkpoint"):
        other.verify_integrity(expected_checkpoint=registry.export_checkpoint())


def test_sql_updates_and_deletes_are_blocked(registry, tmp_path):
    register(registry, tmp_path)
    with sqlite3.connect(registry.database_path) as connection:
        for sql in ("DELETE FROM events", "UPDATE events SET kind='failed'", "DELETE FROM attempts", "UPDATE attempts SET actor_id='someone'", "DELETE FROM registry_metadata"):
            with pytest.raises(sqlite3.DatabaseError, match="append-only"):
                connection.execute(sql)


def test_chain_tampering_is_rejected_even_after_restoring_trigger(registry, tmp_path):
    register(registry, tmp_path)
    with sqlite3.connect(registry.database_path) as connection:
        trigger = connection.execute("SELECT sql FROM sqlite_master WHERE name='events_no_update'").fetchone()[0]
        connection.execute("DROP TRIGGER events_no_update")
        connection.execute("UPDATE events SET payload_json='{}'")
        connection.execute(trigger)
    with pytest.raises(ValueError, match="hash|registered"):
        registry.verify_integrity()
    with pytest.raises(ValueError):
        registry.get(registry.list_attempts()[0]["attempt_id"])


def test_committed_attempt_survives_abrupt_process_exit(tmp_path):
    code = "from devharness_controlled.run_registry import RunRegistry; import os; from pathlib import Path; r=RunRegistry(Path(__import__('sys').argv[1])); r.register('child', 'a'*64, Path(__import__('sys').argv[2]), 'child', submission_sha256='d'*64); os._exit(7)"
    result = subprocess.run([sys.executable, "-c", code, str(tmp_path / "registry"), str(tmp_path / "workspace")], capture_output=True)
    assert result.returncode == 7, result.stderr.decode()
    registry = run_registry.RunRegistry(tmp_path / "registry")
    assert registry.list_attempts()[0]["status"] == "registered"
    assert registry.verify_integrity()["valid"]


def test_uncommitted_sql_transaction_is_rolled_back_after_process_death(registry, tmp_path):
    attempt = register(registry, tmp_path)
    code = "import sqlite3,os,sys; c=sqlite3.connect(sys.argv[1]); c.execute('BEGIN IMMEDIATE'); c.execute('DROP TRIGGER events_no_update'); c.execute(\"UPDATE events SET kind='failed'\"); os._exit(9)"
    result = subprocess.run([sys.executable, "-c", code, str(registry.database_path)], capture_output=True)
    assert result.returncode == 9, result.stderr.decode()
    # A write-capable reopen performs SQLite's hot-journal recovery.
    reopened = run_registry.RunRegistry(registry.directory)
    assert reopened.get(attempt["attempt_id"])["status"] == "registered"
    assert reopened.verify_integrity()["valid"]


def test_lease_is_exclusive_and_reacquired_after_subprocess_death(registry, tmp_path):
    attempt = register(registry, tmp_path)
    identifier = attempt["attempt_id"]
    with registry.lease(identifier):
        with pytest.raises(run_registry.LeaseBusyError):
            with registry.lease(identifier):
                pass
    code = "from devharness_controlled.run_registry import RunRegistry; import sys,time; r=RunRegistry(sys.argv[1]);\nwith r.lease(sys.argv[2]):\n print('locked',flush=True)\n time.sleep(60)"
    process = subprocess.Popen([sys.executable, "-u", "-c", code, str(registry.directory), identifier], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        assert process.stdout.readline().strip() == "locked"
        with pytest.raises(run_registry.LeaseBusyError):
            with registry.lease(identifier):
                pass
    finally:
        process.kill()
        process.wait(timeout=5)
    with registry.lease(identifier):
        pass


def test_registry_path_and_sidecar_rejections(tmp_path):
    real = tmp_path / "real"
    real.mkdir(mode=0o700)
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)
    with pytest.raises(ValueError, match="canonical|symlink"):
        run_registry.RunRegistry(link)
    with pytest.raises(ValueError, match="absolute"):
        run_registry.RunRegistry("relative")
    registry = run_registry.RunRegistry(real)
    target = tmp_path / "target"
    target.write_text("not a database")
    (real / "runs.sqlite3-journal").symlink_to(target)
    with pytest.raises(ValueError, match="regular|symlink"):
        registry.list_attempts()
    assert target.read_text() == "not a database"


def test_symlink_workspace_and_lease_files_are_rejected(registry, tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "link"
    link.symlink_to(target, target_is_directory=True)
    with pytest.raises(ValueError, match="canonical|symlink"):
        registry.register("link", "a" * 64, link / "new", "operator", submission_sha256="d" * 64)
    attempt = register(registry, tmp_path)
    lease_path = registry.directory / "leases" / (attempt["attempt_id"] + ".lock")
    lease_path.symlink_to(target / "other")
    with pytest.raises(ValueError, match="regular|symlink"):
        with registry.lease(attempt["attempt_id"]):
            pass


def test_payload_and_pagination_bounds(registry, tmp_path):
    for number in range(3):
        register(registry, tmp_path, f"request-{number}")
    first = registry.list_attempts(limit=2)
    second = registry.list_attempts(limit=2, after_attempt_id=first[-1]["attempt_id"])
    assert len(first) == 2 and len(second) == 1
    assert len({row["attempt_id"] for row in first + second}) == 3
    for limit in (0, 1001, True, 1.5):
        with pytest.raises(ValueError):
            registry.list_attempts(limit=limit)
    for payload in ({"x": float("nan")}, {"x": "a" * 65537}, [], {1: "not a string"}):
        with pytest.raises((ValueError, TypeError)):
            registry.append_event(first[0]["attempt_id"], "started", payload, "operator")


@pytest.mark.parametrize("stage", ["registered", "started", "runtime_intent", "container_observed", "launch_authorized", "start_requested"])
@pytest.mark.parametrize("terminal", ["failed", "interrupted"])
def test_every_nonterminal_stage_can_be_interrupted_or_fail(registry, tmp_path, stage, terminal):
    identifier = register(registry, tmp_path)["attempt_id"]
    stages = ["registered", "started", "runtime_intent", "container_observed", "launch_authorized", "start_requested"]
    for kind in stages[1:stages.index(stage) + 1]:
        registry.append_event(identifier, kind, {}, "operator")
    registry.append_event(identifier, terminal, {"reason": "test fault"}, "operator")
    assert registry.get(identifier)["status"] == terminal
    assert registry.verify_integrity()["valid"]


@pytest.mark.parametrize("stage", ["registered", "started", "runtime_intent", "container_observed", "launch_authorized"])
def test_completion_cannot_skip_dispatch_evidence(registry, tmp_path, stage):
    identifier = register(registry, tmp_path)["attempt_id"]
    stages = ["registered", "started", "runtime_intent", "container_observed", "launch_authorized"]
    for kind in stages[1:stages.index(stage) + 1]:
        registry.append_event(identifier, kind, {}, "operator")
    with pytest.raises(ValueError, match="transition"):
        registry.append_event(identifier, "completed", {"execution_root_hash": "a" * 64}, "operator")


def test_exact_terminal_idempotence_compares_canonical_json_types(registry, tmp_path):
    identifier = register(registry, tmp_path)["attempt_id"]
    registry.append_event(identifier, "interrupted", {"value": True}, "operator")
    with pytest.raises(ValueError, match="terminal"):
        registry.append_event(identifier, "interrupted", {"value": 1}, "operator")


@pytest.mark.parametrize("permissions", [0o777, 0o755, 0o750])
def test_existing_registry_directory_must_be_private(tmp_path, permissions):
    path = tmp_path / "registry"
    path.mkdir(mode=permissions)
    with pytest.raises(ValueError, match="0700"):
        run_registry.RunRegistry(path)


@pytest.mark.parametrize("kind", ["fifo", "hardlink", "public"])
def test_database_unsafe_file_types_are_rejected(tmp_path, kind):
    directory = tmp_path / "registry"
    directory.mkdir(mode=0o700)
    path = directory / "runs.sqlite3"
    if kind == "fifo":
        os.mkfifo(path, mode=0o600)
    elif kind == "hardlink":
        source = tmp_path / "source"
        source.touch(mode=0o600)
        os.link(source, path)
    else:
        path.touch(mode=0o644)
    with pytest.raises(ValueError, match="regular"):
        run_registry.RunRegistry(directory)


def test_exec_child_does_not_inherit_lease(registry, tmp_path):
    identifier = register(registry, tmp_path)["attempt_id"]
    child = "from devharness_controlled.run_registry import RunRegistry; import sys; r=RunRegistry(sys.argv[1]);\nwith r.lease(sys.argv[2]): print('reacquired', flush=True)"
    parent = "from devharness_controlled.run_registry import RunRegistry; import os,sys; r=RunRegistry(sys.argv[1]);\nwith r.lease(sys.argv[2]): os.execv(sys.executable, [sys.executable, '-c', sys.argv[3], sys.argv[1], sys.argv[2]])"
    result = subprocess.run([sys.executable, "-c", parent, str(registry.directory), identifier, child], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "reacquired"


def test_clock_regression_cannot_fabricate_a_transition(registry, tmp_path, monkeypatch):
    identifier = register(registry, tmp_path)["attempt_id"]
    monkeypatch.setattr(run_registry, "_now", lambda: "2000-01-01T00:00:00.000000Z")
    with pytest.raises(ValueError, match="Clock"):
        registry.append_event(identifier, "started", {}, "operator")
    assert registry.get(identifier)["status"] == "registered"


def test_registry_capacity_refuses_without_purging(registry, tmp_path, monkeypatch):
    monkeypatch.setattr(run_registry, "MAX_ATTEMPTS", 1)
    first = register(registry, tmp_path)
    with pytest.raises(ValueError, match="capacity"):
        register(registry, tmp_path, "request-2")
    assert registry.get(first["attempt_id"])["status"] == "registered"


def test_missing_trigger_is_detected_before_any_mutation(registry, tmp_path):
    register(registry, tmp_path)
    with sqlite3.connect(registry.database_path) as connection:
        connection.execute("DROP TRIGGER events_no_delete")
    with pytest.raises(ValueError, match="schema|triggers"):
        register(registry, tmp_path, "request-2")


def test_new_registry_clock_cannot_precede_registry_creation(registry, tmp_path, monkeypatch):
    monkeypatch.setattr(run_registry, "_now", lambda: "2000-01-01T00:00:00.000000Z")
    with pytest.raises(ValueError, match="chronology|Clock"):
        register(registry, tmp_path)


def test_checkpoint_detects_rehashed_history_replacement(registry, tmp_path):
    identifier = register(registry, tmp_path)["attempt_id"]
    registry.append_event(identifier, "interrupted", {"reason": "original"}, "operator")
    checkpoint = registry.export_checkpoint()
    event = registry.get(identifier)["events"][-1]
    with sqlite3.connect(registry.database_path) as connection:
        trigger = connection.execute("SELECT sql FROM sqlite_master WHERE name='events_no_update'").fetchone()[0]
        connection.execute("DROP TRIGGER events_no_update")
        event["payload"] = {"reason": "rewritten"}
        event.pop("event_hash")
        changed_hash = hashlib.sha256(run_registry.canonical_json_bytes(event)).hexdigest()
        connection.execute("UPDATE events SET payload_json=?,event_hash=? WHERE attempt_id=? AND sequence=2", ('{"reason":"rewritten"}', changed_hash, identifier))
        connection.execute(trigger)
    # A database owner can rewrite history and recompute hashes. Only the saved
    # external checkpoint detects this internally consistent replacement.
    assert registry.verify_integrity()["valid"]
    with pytest.raises(ValueError, match="checkpoint"):
        registry.verify_integrity(expected_checkpoint=checkpoint)


def test_portable_workspace_aliases_cannot_register_twice(registry, tmp_path):
    registry.register("first", "a" * 64, tmp_path / "future-workspace", "operator", submission_sha256="d" * 64)
    with pytest.raises(ValueError, match="workspace"):
        registry.register("second", "a" * 64, tmp_path / "FUTURE-WORKSPACE", "operator", submission_sha256="d" * 64)


@pytest.mark.skipif(not hasattr(__import__("fcntl"), "F_GETPATH"), reason="macOS actual-path spelling check")
def test_existing_mac_case_alias_is_not_canonical(registry, tmp_path):
    target = tmp_path / "real-workspace"
    target.mkdir()
    alias = target.with_name("REAL-WORKSPACE")
    if not alias.exists() or not alias.samefile(target):
        pytest.skip("Filesystem is case-sensitive")
    with pytest.raises(ValueError, match="canonical"):
        registry.register("case", "a" * 64, alias, "operator", submission_sha256="d" * 64)


def test_checkpoint_detects_database_rollback(registry, tmp_path):
    identifier = register(registry, tmp_path)["attempt_id"]
    older = registry.database_path.read_bytes()
    registry.append_event(identifier, "interrupted", {"reason": "observed stop"}, "operator")
    retained = registry.export_checkpoint()
    # Replacing the database bytes is within the owner's power and bypasses SQL
    # triggers; it must not pass against a later independently retained receipt.
    registry.database_path.write_bytes(older)
    assert registry.verify_integrity()["valid"]
    with pytest.raises(ValueError, match="checkpoint"):
        registry.verify_integrity(expected_checkpoint=retained)


def test_database_and_lease_permissions_are_private(registry, tmp_path):
    identifier = register(registry, tmp_path)["attempt_id"]
    assert registry.directory.stat().st_mode & 0o777 == 0o700
    assert registry.database_path.stat().st_mode & 0o777 == 0o600
    with registry.lease(identifier) as lease:
        assert Path(lease["lease_path"]).stat().st_mode & 0o777 == 0o600
    registry.database_path.chmod(0o644)
    with pytest.raises(ValueError, match="regular"):
        registry.get(identifier)


def test_missing_middle_event_is_not_a_valid_history(registry, tmp_path):
    identifier = register(registry, tmp_path)["attempt_id"]
    start_through_dispatch(registry, identifier)
    with sqlite3.connect(registry.database_path) as connection:
        trigger = connection.execute("SELECT sql FROM sqlite_master WHERE name='events_no_delete'").fetchone()[0]
        connection.execute("DROP TRIGGER events_no_delete")
        connection.execute("DELETE FROM events WHERE attempt_id=? AND sequence=3", (identifier,))
        connection.execute(trigger)
    with pytest.raises(ValueError, match="sequence"):
        registry.verify_integrity()


def test_duplicate_dispatch_events_do_not_append(registry, tmp_path):
    identifier = register(registry, tmp_path)["attempt_id"]
    registry.append_event(identifier, "started", {}, "operator")
    with pytest.raises(ValueError, match="transition"):
        registry.append_event(identifier, "started", {}, "operator")
    assert len(registry.get(identifier)["events"]) == 2
