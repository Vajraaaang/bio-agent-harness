"""Plan realization records verify creation, never task qualification."""

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
VALIDATOR = (ROOT / "docs/superpowers/plans/"
             "2026-11-09-agentic-rna-seq-v2.4-implementation/validate_plan.py")


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _git(root, *args):
    return subprocess.run(
        ["git", "-C", str(root), "-c", "core.hooksPath=/dev/null",
         "-c", "user.name=Plan Test", "-c", "user.email=plan-test@example.invalid",
         "-c", "commit.gpgsign=false", *args],
        check=True, text=True, capture_output=True,
    ).stdout.strip()


@pytest.fixture
def plan(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("plan_realizations_validator", VALIDATOR)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    root = tmp_path / "repo"
    root.mkdir()
    output = root / "authoring"
    output.mkdir()
    (root / "docs").mkdir()
    (root / "docs/source.md").write_text("Existing source.\n")
    _git(root, "init", "--quiet")
    _git(root, "add", "docs/source.md")
    _git(root, "commit", "--quiet", "-m", "Synthetic source baseline")
    baseline = _git(root, "rev-parse", "HEAD")
    parent = root / "parent.md"
    parent.write_text("\n".join(f"**REQ-{n:02}**" for n in range(1, 62)))
    coverage = {
        "parent_sha256": _sha(parent.read_bytes()),
        "requirements": [{"id": f"REQ-{n:02}", "task_ids": ["T01"]}
                         for n in range(1, 62)],
    }
    (output / "requirement-coverage.json").write_text(json.dumps(coverage))
    lines = ["# Synthetic implementation plan", "**Existing:** `docs/source.md`"]
    lines += [f"| REQ-{n:02} | T01 |" for n in range(1, 62)]
    lines += [f"| RET-{n:02} | retained |" for n in range(1, 14)]
    for n in range(1, 27):
        lines += [f"### T{n:02} — Synthetic task", "**Owner:** test",
                  "**Depends on:** none", "**Requirements:** REQ-01",
                  "**Acceptance:** inspect output", "- [ ] Complete this task."]
        if n == 1:
            lines += ["**Create:** `docs/created.md`."]
        elif n == 2:
            lines += ["**Create:** `docs/pending.md`."]
    plan_path = root / "plan.md"
    plan_path.write_text("\n".join(lines) + "\n")
    created = root / "docs/created.md"
    created.write_text("Created artifact; not qualified.\n")
    manifest_path = root / "reports/plan-realizations.json"
    manifest_path.parent.mkdir()
    manifest = {
        "format": "devbench.implementation-plan-realizations/1",
        "plan_sha256": _sha(plan_path.read_bytes()),
        "baseline_commit": baseline,
        "files": [{"path": "docs/created.md", "task_id": "T01",
                   "sha256": _sha(created.read_bytes()), "state": "created_not_qualified"}],
    }
    for name, value in {"ROOT": root, "PLAN": plan_path, "OUTPUT": output,
                        "PARENT": parent, "EXPECTED_PARENT": coverage["parent_sha256"],
                        "REALIZATIONS": manifest_path, "EXPECTED_BASELINE": baseline}.items():
        monkeypatch.setattr(module, name, value, raising=False)
    return module, manifest


def _run(plan, capsys, manifest=None):
    module, valid_manifest = plan
    if manifest is not None:
        module.REALIZATIONS.write_text(json.dumps(manifest))
    result = module.main()
    summary = json.loads(capsys.readouterr().out)
    return result, summary


def _assert_rejected(plan, capsys, manifest, message):
    result, summary = _run(plan, capsys, manifest)
    assert result == 1
    assert message in json.dumps(summary["errors"])
    assert summary["realized_files"] == 0


def test_valid_manifest_reconciles_only_created_files(plan, capsys):
    module, manifest = plan
    result, summary = _run(plan, capsys, manifest)
    assert result == 0, summary["errors"]
    assert summary["status"] == "passed"
    assert summary["proposed_files"] == 2
    assert summary["realized_files"] == 1
    assert summary["realized_create_paths"] == ["docs/created.md"]
    assert summary["realization_manifest"]["baseline_commit"] == module.EXPECTED_BASELINE
    assert summary["realization_manifest"]["sha256"] == _sha(module.REALIZATIONS.read_bytes())
    assert summary["realization_manifest"]["state"] == "created_not_qualified"
    assert summary["requirements_covered"] == 61
    assert summary["tasks"] == 26
    assert summary["retirement_records"] == 13
    assert summary["dependency_acyclic"] is True
    assert summary["application_tests_run"] is False
    assert summary["scientific_workloads_run"] is False
    inventory = json.loads((module.OUTPUT / "file-inventory.json").read_text())
    assert inventory["proposed_create_paths"] == ["docs/created.md", "docs/pending.md"]
    assert inventory["realized_create_paths"] == ["docs/created.md"]


def test_absent_manifest_preserves_create_collision_failure(plan, capsys):
    result, summary = _run(plan, capsys)
    assert result == 1
    assert {"create_paths_already_exist": ["docs/created.md"]} in summary["errors"]
    assert summary["realized_files"] == 0
    assert summary["realization_manifest"] is None


def test_unrecorded_create_collision_is_not_inferred_as_realized(plan, capsys):
    module, manifest = plan
    (module.ROOT / "docs/pending.md").write_text("Unrecorded new file.\n")
    result, summary = _run(plan, capsys, manifest)
    assert result == 1
    assert {"create_paths_already_exist": ["docs/pending.md"]} in summary["errors"]
    assert summary["realized_create_paths"] == ["docs/created.md"]


@pytest.mark.parametrize("task", ["T02", "T99", "T01.fixed", None])
def test_unknown_or_foreign_task_is_rejected(plan, capsys, task):
    manifest = copy.deepcopy(plan[1])
    manifest["files"][0]["task_id"] = task
    _assert_rejected(plan, capsys, manifest, "Create task ownership mismatch")


@pytest.mark.parametrize("path", ["../created.md", "/tmp/created.md", "docs/../docs/created.md",
                                  "docs//created.md", "./docs/created.md", "docs\\created.md",
                                  "docs/created.md\x00", "", None])
def test_unsafe_or_noncanonical_path_is_rejected(plan, capsys, path):
    manifest = copy.deepcopy(plan[1])
    manifest["files"][0]["path"] = path
    _assert_rejected(plan, capsys, manifest, "Unsafe realization path")


def test_path_not_declared_create_is_rejected(plan, capsys):
    module, manifest = plan
    manifest["files"][0].update(path="docs/source.md", sha256=_sha((module.ROOT / "docs/source.md").read_bytes()))
    _assert_rejected(plan, capsys, manifest, "Path is not declared Create")


def test_duplicate_paths_reject_entire_manifest(plan, capsys):
    manifest = plan[1]
    manifest["files"].append(copy.deepcopy(manifest["files"][0]))
    _assert_rejected(plan, capsys, manifest, "Duplicate realization path")


@pytest.mark.parametrize("field,value,message", [
    ("format", "devbench.implementation-plan-realizations/2", "Unsupported realization format"),
    ("plan_sha256", "0" * 64, "Realization plan identity mismatch"),
    ("baseline_commit", "0" * 40, "Realization baseline identity mismatch"),
    ("files", {}, "Realization files must be a list"),
])
def test_manifest_binding_is_checked(plan, capsys, field, value, message):
    manifest = plan[1]
    manifest[field] = value
    _assert_rejected(plan, capsys, manifest, message)


@pytest.mark.parametrize("state", ["qualified", "accepted", "created", None])
def test_qualification_claim_cannot_be_recorded_as_realization(plan, capsys, state):
    manifest = plan[1]
    manifest["files"][0]["state"] = state
    _assert_rejected(plan, capsys, manifest, "Unsupported realization state")


@pytest.mark.parametrize("scope", ["manifest", "row"])
def test_unknown_fields_fail_closed(plan, capsys, scope):
    manifest = plan[1]
    target = manifest if scope == "manifest" else manifest["files"][0]
    target["accepted"] = True
    _assert_rejected(plan, capsys, manifest, "Unexpected realization fields")


@pytest.mark.parametrize("payload", [None, [], 1, "manifest"])
def test_invalid_top_level_json_is_rejected(plan, capsys, payload):
    plan[0].REALIZATIONS.write_text(json.dumps(payload))
    _assert_rejected(plan, capsys, None, "Realization manifest must be an object")


def test_malformed_json_is_rejected(plan, capsys):
    plan[0].REALIZATIONS.write_text("{\"broken\":")
    _assert_rejected(plan, capsys, None, "Cannot read realization manifest")


def test_duplicate_json_keys_are_rejected(plan, capsys):
    plan[0].REALIZATIONS.write_text('{"format": "wrong", "format": "other"}')
    _assert_rejected(plan, capsys, None, "Duplicate realization JSON key")


def test_source_hash_mismatch_is_rejected(plan, capsys):
    manifest = plan[1]
    manifest["files"][0]["sha256"] = "0" * 64
    _assert_rejected(plan, capsys, manifest, "Realization source hash mismatch")


def test_missing_realized_file_is_rejected(plan, capsys):
    (plan[0].ROOT / "docs/created.md").unlink()
    _assert_rejected(plan, capsys, plan[1], "Realization source is not a regular file")


def test_source_directory_is_rejected(plan, capsys):
    path = plan[0].ROOT / "docs/created.md"
    path.unlink()
    path.mkdir()
    _assert_rejected(plan, capsys, plan[1], "Realization source is not a regular file")


def test_unknown_baseline_commit_is_rejected(plan, capsys, monkeypatch):
    module, manifest = plan
    monkeypatch.setattr(module, "EXPECTED_BASELINE", "0" * 40)
    manifest["baseline_commit"] = "0" * 40
    _assert_rejected(plan, capsys, manifest, "Realization baseline commit does not exist")


def test_path_present_in_baseline_is_rejected(plan, capsys, monkeypatch):
    module, manifest = plan
    _git(module.ROOT, "add", "docs/created.md")
    _git(module.ROOT, "commit", "--quiet", "-m", "Artifact already present")
    baseline = _git(module.ROOT, "rev-parse", "HEAD")
    monkeypatch.setattr(module, "EXPECTED_BASELINE", baseline)
    manifest["baseline_commit"] = baseline
    _assert_rejected(plan, capsys, manifest, "Realization path already exists at baseline")


@pytest.mark.parametrize("target", ["source", "source_parent", "manifest", "manifest_parent"])
def test_symlinks_are_rejected_without_following_them(plan, capsys, target):
    module, manifest = plan
    module.REALIZATIONS.write_text(json.dumps(manifest))
    if target.startswith("source"):
        path = module.ROOT / ("docs" if target == "source_parent" else "docs/created.md")
    else:
        path = module.REALIZATIONS.parent if target == "manifest_parent" else module.REALIZATIONS
    retained = module.ROOT / ("retained-" + path.name)
    path.rename(retained)
    path.symlink_to(retained, target_is_directory=retained.is_dir())
    _assert_rejected(plan, capsys, None, "Symlink in realization path")


@pytest.mark.parametrize("target", ["source", "manifest"])
def test_bounded_reads_are_preserved(plan, capsys, target):
    module, manifest = plan
    if target == "source":
        path = module.ROOT / "docs/created.md"
        path.write_bytes(b"a" * 2_000_001)
        manifest["files"][0]["sha256"] = _sha(path.read_bytes())
        module.REALIZATIONS.write_text(json.dumps(manifest))
    else:
        module.REALIZATIONS.write_text(json.dumps(manifest) + " " * 2_000_001)
    _assert_rejected(plan, capsys, None, "exceeds bounded")


def test_valid_manifest_does_not_bypass_parent_hash_check(plan, capsys):
    module, manifest = plan
    module.PARENT.write_text(module.PARENT.read_text() + "\nChanged parent.\n")
    result, summary = _run(plan, capsys, manifest)
    assert result == 1
    assert "Parent content hash changed" in summary["errors"]
    assert summary["parent_unchanged"] is False
