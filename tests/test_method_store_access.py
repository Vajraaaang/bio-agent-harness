"""Read-only metadata access and scientific-controller integration seams."""
import sqlite3
import uuid

import pytest

from devbench.analysis.methods import MethodService
from tests.test_extensible_methods import FixtureExecutor, prepared


def test_readonly_missing_store_does_not_initialize(tmp_path):
    root = tmp_path / "absent"
    with pytest.raises((ValueError, FileNotFoundError)):
        MethodService(root, readonly=True)
    assert not root.exists()


def test_readonly_access_neither_reconciles_nor_writes(tmp_path, monkeypatch):
    service, analysis, *_ = prepared(tmp_path)
    before = service.database.read_bytes()
    monkeypatch.setattr(MethodService, "reconcile", lambda _: pytest.fail("A read opened worker recovery"))
    reader = MethodService(service.root, readonly=True)
    assert reader.analysis(analysis["analysis_id"]) == analysis
    with pytest.raises((PermissionError, sqlite3.OperationalError)):
        reader.create_analysis("Forbidden", actor="reader")
    assert service.database.read_bytes() == before


def test_schema_two_method_records_remain_readable(tmp_path):
    service, analysis, *_ = prepared(tmp_path)
    with service._db() as db:
        db.execute("UPDATE schema_version SET version=2")
    before = service.database.read_bytes()
    reader = MethodService(service.root, readonly=True)
    assert reader.analysis(analysis["analysis_id"]) == analysis
    assert service.database.read_bytes() == before


def test_unknown_schema_fails_without_modifying_database(tmp_path):
    service = MethodService(tmp_path / "workspace")
    with service._db() as db:
        db.execute("UPDATE schema_version SET version=999")
    before = service.database.read_bytes()
    with pytest.raises(ValueError, match="schema"):
        MethodService(service.root)
    assert service.database.read_bytes() == before


def test_controller_identity_is_reserved_and_sealed_before_execution(tmp_path):
    service, _, _, step, *_ = prepared(tmp_path)
    run_id = uuid.uuid4().hex
    correlation = {"project_id": "a" * 32, "scientific_run_id": "b" * 32,
                   "attempt_id": "c" * 32, "analysis_version_id": "d" * 64,
                   "dataset_version_ids": ["e" * 64]}
    run = service.run_step(step["step_id"], actor="researcher", executor=FixtureExecutor(),
                           run_id=run_id, correlation=correlation)
    assert run["run_id"] == run_id
    assert run["correlation"] == correlation
    assert service.provenance(run_id)["valid"]
    with pytest.raises((ValueError, FileExistsError)):
        service.run_step(step["step_id"], actor="researcher", executor=FixtureExecutor(),
                         run_id=run_id, correlation=correlation)


def test_atomic_approval_hook_rolls_back_head_and_review(tmp_path):
    service, analysis, _, step, *_ = prepared(tmp_path)
    run = service.run_step(step["step_id"], actor="researcher", executor=FixtureExecutor())
    proposal = service.propose(run["run_id"], actor="researcher", rationale="Review synthetic output")
    audit = service.audit()

    def failed_scientific_acceptance(db, revision, selected_proposal, selected_run):
        assert selected_proposal["proposal_id"] == proposal["proposal_id"]
        assert selected_run["run_id"] == run["run_id"]
        service._put(db, "scientific_test", "sentinel", {"review": revision["revision_id"]})
        raise ValueError("Scientific target became stale")

    with pytest.raises(ValueError, match="stale"):
        service.approve(proposal["proposal_id"], actor="reviewer", rationale="Reviewed", acknowledge_warnings=True,
                        _on_approval=failed_scientific_acceptance)
    assert service.analysis(analysis["analysis_id"]) == analysis
    assert service.get("proposal", proposal["proposal_id"])["status"] == "pending"
    assert service.audit() == audit
    with pytest.raises(ValueError, match="Unknown"):
        service.get("scientific_test", "sentinel")


def test_readonly_cli_commands_never_reconcile_or_initialize(tmp_path, monkeypatch, capsys):
    from devbench.analysis.method_cli import main
    service, analysis, *_ = prepared(tmp_path)
    before = service.database.read_bytes()
    monkeypatch.setattr(MethodService, "reconcile", lambda _: pytest.fail("Read-only CLI reconciled workers"))
    assert main(["--workspace", str(service.root), "analysis", "--id", analysis["analysis_id"]]) == 0
    assert main(["--workspace", str(service.root), "audit"]) == 0
    assert service.database.read_bytes() == before
    missing = tmp_path / "missing-reader"
    with pytest.raises(ValueError, match="initialization|unavailable"):
        main(["--workspace", str(missing), "audit"])
    assert not missing.exists()
    capsys.readouterr()
