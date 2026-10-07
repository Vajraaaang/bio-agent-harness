"""Versioned local UI metadata cannot authorize or rewrite scientific work."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from tests.test_workbench_ui import root, service
from tests.test_workbench_ui_organization import add_record


def test_legacy_view_state_migrates_without_changing_identity_draft_or_job(root):
    from devbench.analysis.checkpoints import atomic_json
    app = service(root)
    job = add_record(app)
    original = app.workspace_state()
    legacy = {key: value for key, value in original.items() if key != "schema_version"}
    legacy["format"] = "devbench.workspace/1"
    legacy["ui"].update(selected_job=job, question_draft="Retained legacy question", zoom="native")
    state_before = app._path("jobs", job, "state.json").read_bytes()
    app.close()
    atomic_json(root / "workspace/workspace.json", legacy)
    reopened = service(root)
    try:
        migrated = reopened.workspace_state()
        assert migrated["format"] == "devbench.workspace/2"
        assert migrated["schema_version"] == 2
        for key in ("project", "ui", "revision"):
            assert migrated[key] == legacy[key]
        assert json.loads((root / "workspace/workspace.previous.json").read_text()) == legacy
        assert reopened._path("jobs", job, "state.json").read_bytes() == state_before
        assert reopened.configuration()["execution_allowed"] is False
    finally:
        reopened.close()


@pytest.mark.parametrize("missing", [False, True])
def test_missing_or_invalid_metadata_requires_explicit_view_only_recovery(root, missing):
    app = service(root)
    job = add_record(app, status="completed_review_failed")
    original = app.workspace_state()
    saved = app.workspace_state({"expected_revision": original["revision"], "ui": {"question_draft": "Recover this draft", "selected_job": job}})
    app.workspace_state({"expected_revision": saved["revision"], "ui": {"question_draft": "Newest draft"}})
    state_before = app._path("jobs", job, "state.json").read_bytes()
    app.close()
    primary = root / "workspace/workspace.json"
    if missing:
        primary.unlink()
    else:
        primary.write_bytes(b'{"interrupted":')
    with pytest.raises(ValueError, match="restore-view-state-backup"):
        service(root)
    if not missing:
        assert primary.read_bytes() == b'{"interrupted":'
    recovered = service(root, restore_view_state_backup=True)
    try:
        restored = recovered.workspace_state()
        assert restored["project"] == saved["project"]
        assert restored["ui"] == saved["ui"]
        assert "Recovered" in restored["recovery_notice"]
        assert "revision " + str(saved["revision"]) in restored["recovery_notice"]
        assert recovered._path("jobs", job, "state.json").read_bytes() == state_before
        assert recovered.configuration()["execution_allowed"] is False
        assert recovered.configuration()["model_access"] is False
        if not missing:
            quarantines = list((root / "workspace").glob("workspace.invalid-*.json"))
            assert len(quarantines) == 1 and quarantines[0].read_bytes() == b'{"interrupted":'
    finally:
        recovered.close()


@pytest.mark.parametrize("replacement", ["future_schema", "invalid_backup", "backup_symlink"])
def test_recovery_does_not_downgrade_or_follow_invalid_backups(root, replacement):
    app = service(root)
    original = app.workspace_state()
    app.close()
    primary = root / "workspace/workspace.json"
    backup = root / "workspace/workspace.previous.json"
    if replacement == "future_schema":
        primary.write_text(json.dumps({**original, "format": "devbench.workspace/99", "schema_version": 99}))
    else:
        primary.write_bytes(b'broken')
        if replacement == "invalid_backup":
            backup.write_text(json.dumps({**original, "token": "must-not-be-inherited"}))
        else:
            outside = root / "outside.json"
            outside.write_text(json.dumps(original))
            backup.unlink(missing_ok=True)
            backup.symlink_to(outside)
    before = primary.read_bytes()
    with pytest.raises(ValueError):
        service(root, restore_view_state_backup=True)
    assert primary.read_bytes() == before
    assert not list((root / "workspace").glob("workspace.invalid-*.json"))


@pytest.mark.parametrize("phase", ["before_replace", "after_replace"])
def test_process_exit_during_metadata_save_retains_a_valid_old_or_new_record(root, phase):
    app = service(root)
    job = add_record(app)
    before = app.workspace_state()
    state_before = app._path("jobs", job, "state.json").read_bytes()
    app.close()
    program = '''
from pathlib import Path
import os, sys
from devbench.analysis.workbench_ui import WorkbenchService
if __name__ == "__main__":
    workspace, inputs, phase = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
    app = WorkbenchService(workspace, [inputs])
    replace = Path.replace
    def interrupted(path, target):
        if Path(target) == workspace / "workspace.json":
            if phase == "after_replace":
                replace(path, target)
            os._exit(73)
        return replace(path, target)
    Path.replace = interrupted
    current = app.workspace_state()
    app.workspace_state({"expected_revision": current["revision"], "ui": {"question_draft": "Saved around process exit"}})
'''
    repo = Path(__file__).resolve().parents[1]
    result = subprocess.run([sys.executable, "-c", program, str(root / "workspace"), str(root / "inputs"), phase],
        cwd=repo, env={**os.environ, "PYTHONPATH": str(repo)}, capture_output=True, text=True, timeout=30)
    assert result.returncode == 73, result.stderr
    reopened = service(root)
    try:
        after = reopened.workspace_state()
        assert after["project"] == before["project"]
        assert after["schema_version"] == 2
        assert json.loads((root / "workspace/workspace.previous.json").read_text()) == before
        if phase == "before_replace":
            assert after == before
        else:
            assert after["ui"]["question_draft"] == "Saved around process exit"
            assert after["revision"] == before["revision"] + 1
        assert reopened._path("jobs", job, "state.json").read_bytes() == state_before
    finally:
        reopened.close()


def test_directory_sync_failure_reconciles_revision_before_accepting_another_save(root, monkeypatch):
    app = service(root)
    original = app.workspace_state()
    real_sync = app._sync_workspace_directory
    calls = []
    def fail_after_primary_replace():
        calls.append(True)
        if len(calls) == 2:
            raise OSError("Injected directory-sync failure")
        real_sync()
    monkeypatch.setattr(app, "_sync_workspace_directory", fail_after_primary_replace)
    try:
        with pytest.raises(RuntimeError, match="interrupted"):
            app.workspace_state({"expected_revision": original["revision"], "ui": {"question_draft": "May already be stored"}})
        observed = app.workspace_state()
        assert observed["revision"] == original["revision"] + 1
        assert observed["ui"]["question_draft"] == "May already be stored"
        with pytest.raises(RuntimeError, match="another window"):
            app.workspace_state({"expected_revision": original["revision"], "ui": {"question_draft": "Stale overwrite"}})
    finally:
        app.close()


def test_schema_version_has_a_strict_integer_type(root):
    app = service(root)
    invalid = app.workspace_state()
    app.close()
    invalid["schema_version"] = 2.0
    (root / "workspace/workspace.json").write_text(json.dumps(invalid))
    with pytest.raises(ValueError):
        service(root)


def test_explicit_recovery_accepts_a_malformed_known_file_but_never_a_valid_rollback(root):
    app = service(root)
    original = app.workspace_state()
    app.close()
    with pytest.raises(ValueError, match="valid"):
        service(root, restore_view_state_backup=True)
    primary = root / "workspace/workspace.json"
    primary.write_text('{"ui": {"question_draft": "Truncated envelope"}}')
    recovered = service(root, restore_view_state_backup=True)
    try:
        assert recovered.workspace_state()["project"] == original["project"]
        assert recovered.workspace_state()["ui"] == original["ui"]
    finally:
        recovered.close()


def test_missing_both_metadata_files_never_resets_an_existing_project(root):
    app = service(root)
    job = add_record(app)
    state_before = app._path("jobs", job, "state.json").read_bytes()
    app.close()
    primary = root / "workspace/workspace.json"
    backup = root / "workspace/workspace.previous.json"
    primary.unlink()
    backup.unlink()
    with pytest.raises(ValueError, match="missing"):
        service(root)
    assert not primary.exists() and not backup.exists()
    assert (root / "workspace/jobs" / job / "state.json").read_bytes() == state_before


def test_unhashable_corrupt_format_has_an_explicit_recovery_path(root):
    app = service(root)
    original = app.workspace_state()
    app.close()
    (root / "workspace/workspace.json").write_text('{"format": []}')
    recovered = service(root, restore_view_state_backup=True)
    try:
        assert recovered.workspace_state()["project"] == original["project"]
    finally:
        recovered.close()
