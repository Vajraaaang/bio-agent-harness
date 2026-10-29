"""Packaging and negative custody checks only; no scientific execution."""
import hashlib
import base64
import csv
import io
import json
import subprocess
import zipfile
from pathlib import Path

import pytest

from devbench.analysis.workflow_candidate_freeze import (
    EXECUTION_POLICY, FORMAT, LOCK_PATHS, SOURCE_FORMAT, _json_ref, _source_map,
    _verified_wheel_members, _verify_dependency_versions, canonical_bytes, freeze_file_ref,
    validate_candidate_manifest, verify_freeze_ref,
)
from scripts.build_colab_notebook import allowed_bundle_path
from scripts.freeze_required_workflow_candidate import _branch, source_paths


def test_retained_reference_detects_same_size_tampering(tmp_path):
    asset = tmp_path / "asset.json"
    asset.write_bytes(b'{"a":1}')
    reference = freeze_file_ref(asset)
    assert verify_freeze_ref(reference) == asset.read_bytes()
    asset.write_bytes(b'{"a":2}')
    with pytest.raises(ValueError, match="bytes changed"):
        verify_freeze_ref(reference)


def test_reference_rejects_boolean_sizes_and_symlinks(tmp_path):
    path = tmp_path / "asset"
    path.write_bytes(b"a")
    reference = freeze_file_ref(path)
    with pytest.raises(ValueError, match="size"):
        verify_freeze_ref(dict(reference, size_bytes=True))
    link = tmp_path / "alias"
    link.symlink_to(path)
    with pytest.raises(ValueError, match="symlinks"):
        freeze_file_ref(link)
    with pytest.raises(ValueError, match="absolute"):
        freeze_file_ref(Path("asset"))


@pytest.mark.parametrize("content", [b'{"a":1,"a":2}', b'{"a":NaN}'])
def test_candidate_json_rejects_ambiguous_or_nonfinite_values(tmp_path, content):
    path = tmp_path / "ambiguous.json"
    path.write_bytes(content)
    with pytest.raises(ValueError):
        _json_ref(freeze_file_ref(path))


def test_bound_candidate_cannot_substitute_payload_for_retained_reference():
    with pytest.raises(ValueError, match="retained file"):
        validate_candidate_manifest({"format": FORMAT}, expected_sha256="a" * 64)


def test_candidate_policy_and_actual_runtime_are_mandatory(tmp_path):
    candidate = {name: None for name in (
        "source_manifest_ref", "dependency_lock_ref", "wheel_ref", "generated_assets", "installed_records",
        "module_origins", "closure", "test_results")}
    candidate.update(format=FORMAT, source_root=str(tmp_path / "source"), runtime_root=str(tmp_path / "runtime"),
                     branch="unit-control-fixture", execution_policy=dict(EXECUTION_POLICY, max_runs=True))
    with pytest.raises(ValueError, match="policy"):
        validate_candidate_manifest(candidate)
    candidate["execution_policy"] = dict(EXECUTION_POLICY)
    with pytest.raises(ValueError, match="actual isolated installed runtime"):
        validate_candidate_manifest(candidate)


def test_source_manifest_hashes_all_locks_and_rejects_stale_source(tmp_path):
    rows = []
    for name in LOCK_PATHS:
        path = tmp_path / name
        path.write_bytes(name.encode())
        rows.append({"relative_path": name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                     "size_bytes": path.stat().st_size})
    value = {"format": SOURCE_FORMAT, "source_root": str(tmp_path), "branch": "software-fixture", "files": rows,
             "qualification_assets": {}, "audit_refs": []}
    with pytest.raises(ValueError, match="five authorized qualification assets"):
        _source_map(value, tmp_path)
    (tmp_path / LOCK_PATHS[0]).write_bytes(b"changed")
    with pytest.raises(ValueError, match="changed"):
        _source_map(value, tmp_path)


def test_omitting_real_source_from_manifest_cannot_hide_it(tmp_path):
    rows = []
    for name in LOCK_PATHS:
        path = tmp_path / name
        path.write_bytes(name.encode())
        rows.append({"relative_path": name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                     "size_bytes": path.stat().st_size})
    module = tmp_path / "devbench" / "omitted.py"
    module.parent.mkdir()
    module.write_text("# Inert metadata fixture; never imported.\n")
    manifest = {"format": SOURCE_FORMAT, "source_root": str(tmp_path), "branch": "software-fixture",
                "files": rows, "qualification_assets": {}, "audit_refs": []}
    with pytest.raises(ValueError, match="Python inventory changed"):
        _source_map(manifest, tmp_path)


def test_packaging_selection_is_explicit_and_excludes_reports(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[tool.setuptools.package-data]\ndevbench=["assets/*.json"]\n')
    for name in ("uv.lock", "requirements-langgraph.lock", "devbench/a.py", "devbench/assets/a.json",
                 "scripts/freeze_required_workflow_candidate.py", "tests/test_control.py", "reports/private.py"):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}")
    selected = {path.relative_to(tmp_path).as_posix() for path in source_paths(tmp_path)}
    assert {"uv.lock", "requirements-langgraph.lock", "devbench/a.py", "devbench/assets/a.json",
            "scripts/freeze_required_workflow_candidate.py", "tests/test_control.py"} <= selected
    assert "reports/private.py" not in selected
    assert allowed_bundle_path("requirements-langgraph.lock")
    assert allowed_bundle_path("scripts/freeze_required_workflow_candidate.py")
    for path in ("reports/candidate.json", "devbench/analysis/assets/source-bundle.zip", ".venv/secret.py"):
        assert not allowed_bundle_path(path)


def test_canonical_json_is_deterministic_and_finite():
    assert canonical_bytes({"b": 2, "a": 1}) == b'{"a":1,"b":2}\n'
    with pytest.raises(ValueError):
        canonical_bytes({"a": float("nan")})


def _inert_metadata_wheel(*, tamper=False, extra=False):
    members = {"devbench/a.py": b"# Inert engineering fixture; never imported.\n",
               "devharness-0.5.0.dist-info/METADATA": b"Name: devharness\nVersion: 0.5.0\n"}
    rows = []
    for name, body in members.items():
        rows.append([name, "sha256=" + base64.urlsafe_b64encode(hashlib.sha256(body).digest()).decode().rstrip("="), str(len(body))])
    record = "devharness-0.5.0.dist-info/RECORD"
    rows.append([record, "", ""])
    output = io.StringIO()
    csv.writer(output, lineterminator="\n").writerows(rows)
    members[record] = output.getvalue().encode()
    if tamper:
        members["devbench/a.py"] += b"# changed\n"
    if extra:
        members["devbench/unrecorded.py"] = b"# Unrecorded inert fixture.\n"
    result = io.BytesIO()
    with zipfile.ZipFile(result, "w") as archive:
        for name, body in members.items():
            archive.writestr(name, body)
    return io.BytesIO(result.getvalue())


def test_wheel_own_record_covers_all_bytes_and_members():
    with zipfile.ZipFile(_inert_metadata_wheel()) as archive:
        assert len(_verified_wheel_members(archive)) == 3
    with zipfile.ZipFile(_inert_metadata_wheel(tamper=True)) as archive:
        with pytest.raises(ValueError, match="differs from its RECORD"):
            _verified_wheel_members(archive)
    with zipfile.ZipFile(_inert_metadata_wheel(extra=True)) as archive:
        with pytest.raises(ValueError, match="cover every member"):
            _verified_wheel_members(archive)


def test_dependency_custody_also_requires_versions_to_satisfy_active_requirements(tmp_path):
    (tmp_path / "uv.lock").write_text('[[package]]\nname="devharness"\nversion="1"\n[[package]]\nname="foo"\nversion="1"\n')
    (tmp_path / "requirements-langgraph.lock").write_text("foo==1\n")
    verified = {"devharness": {"version": "1", "requires": ["foo>=2"]}, "foo": {"version": "1", "requires": []}}
    with pytest.raises(ValueError, match="violates Requires-Dist"):
        _verify_dependency_versions(verified, tmp_path)
    verified["devharness"]["requires"] = ["foo>=1"]
    _verify_dependency_versions(verified, tmp_path)
    verified["foo"]["version"] = "2"
    assert _verify_dependency_versions(verified, tmp_path) == [
        {"name": "foo", "installed_version": "2", "uv_versions": ["1"], "workflow_version": "1"}]


def test_freeze_retains_unborn_git_branch_without_inventing_a_commit(tmp_path, monkeypatch):
    def unborn_git(arguments, **kwargs):
        if arguments[3:] == ["symbolic-ref", "--short", "HEAD"]:
            return subprocess.CompletedProcess(arguments, 0, "fixture/no-commit\n", "")
        raise subprocess.CalledProcessError(128, arguments, stderr="HEAD has no commit")
    monkeypatch.setattr("scripts.freeze_required_workflow_candidate.subprocess.run", unborn_git)
    assert _branch(tmp_path) == "fixture/no-commit"
