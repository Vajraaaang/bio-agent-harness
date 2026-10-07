"""Offline runtime qualification, never substitute participant performance."""
import importlib.metadata
import json
import os
from pathlib import Path
import subprocess

import pytest


class Distribution:
    def __init__(self, root, name, requires=(), version="1.0", entries=None, extras=()):
        self.metadata = {"Name": name, "Provides-Extra": extras}
        self.version, self.requires, self.root = version, requires, root
        self.files = [Path(name.replace("-", "_") + "/__init__.py"),
                      Path(name + "-1.0.dist-info/METADATA")] if entries is None else entries
        for item in self.files:
            target = root / item
            target.parent.mkdir(parents=True, exist_ok=True)
            target.touch()

    def locate_file(self, item):
        return self.root / item


def resolve(monkeypatch, tmp_path, distributions, roots):
    from devbench.runtime_dependencies import resolve_dependencies
    monkeypatch.setattr(importlib.metadata, "distributions", lambda **kw: iter(distributions))
    return resolve_dependencies(tmp_path, roots)


def test_dependency_closure_includes_transitive_extras_and_native_metadata(monkeypatch, tmp_path):
    distributions = [
        Distribution(tmp_path, "analysis", ["base>=1", "late[extra]", "ignored; python_version<'2'"]),
        Distribution(tmp_path, "base", ["native; extra=='speed'"]),
        Distribution(tmp_path, "late", ["base[speed]; extra=='extra'"]),
        Distribution(tmp_path, "native", entries=[Path("native.libs/libexample.so"), Path("native-1.0.dist-info/METADATA")]),
        Distribution(tmp_path, "unrelated"),
    ]
    entries, versions = resolve(monkeypatch, tmp_path, distributions, ("analysis",))
    assert set(versions) == {"analysis", "base", "late", "native"}
    assert {path.name for path in entries} >= {"native.libs", "analysis-1.0.dist-info", "base"}
    assert "unrelated" not in {path.name for path in entries}


@pytest.mark.parametrize("requirement", ["absent", "base>=2", "base @ https://invalid.test/wheel"])
def test_incomplete_or_unpinned_runtime_fails_closed(monkeypatch, tmp_path, requirement):
    distributions = [Distribution(tmp_path, "analysis", [requirement]), Distribution(tmp_path, "base")]
    with pytest.raises(ValueError):
        resolve(monkeypatch, tmp_path, distributions, ("analysis",))


def test_shared_namespace_does_not_admit_unselected_package(monkeypatch, tmp_path):
    distributions = [Distribution(tmp_path, "safe", entries=[Path("shared/__init__.py"), Path("shared/safe.py")]),
                     Distribution(tmp_path, "unsafe", entries=[Path("shared/unsafe.py")])]
    with pytest.raises(ValueError, match="shared"):
        resolve(monkeypatch, tmp_path, distributions, ("safe",))


def test_coordinator_and_editable_hooks_are_rejected(monkeypatch, tmp_path):
    for member in ("devbench/fake.py", "__editable___analysis.py", "sitecustomize.py"):
        distributions = [Distribution(tmp_path, "analysis", entries=[Path(member)])]
        with pytest.raises(ValueError, match="Forbidden"):
            resolve(monkeypatch, tmp_path, distributions, ("analysis",))


def test_shared_namespace_helpers_are_partitioned_without_admitting_optional_stack(monkeypatch, tmp_path):
    distributions = [Distribution(tmp_path, "safe", entries=[Path("safe/__init__.py"), Path("testing/safe/helper.py")]),
                     Distribution(tmp_path, "optional", entries=[Path("testing/optional/helper.py")])]
    entries, versions = resolve(monkeypatch, tmp_path, distributions, ("safe",))
    assert [path.relative_to(tmp_path).as_posix() for path in entries] == ["safe", "testing/safe"]
    assert versions == {"safe": "1.0"}


def test_missing_wheel_record_fails_closed(monkeypatch, tmp_path):
    distribution = Distribution(tmp_path, "analysis")
    distribution.files = None
    with pytest.raises(ValueError, match="record"):
        resolve(monkeypatch, tmp_path, [distribution], ("analysis",))


@pytest.fixture(scope="module")
def bulk_runtime(tmp_path_factory):
    from devbench.staging import stage_python
    workspace = tmp_path_factory.mktemp("bulk-runtime")
    return workspace, stage_python(workspace, assisted=False, profile="bulk")


def test_real_bulk_runtime_imports_science_and_excludes_optional_stacks(bulk_runtime):
    workspace, environment = bulk_runtime
    code = '''import importlib.util,importlib.metadata,json
import pydeseq2.dds,pydeseq2.ds,numpy,pandas,scipy,statsmodels.api,patsy
import sklearn,zarr,numcodecs,h5py,psutil,jsonschema,networkx,pydantic,matplotlib
for name in ('torch','scanpy','scvi','devbench','devbench_engine','devharness','benchmarks'):
    assert importlib.util.find_spec(name) is None,name
print(json.dumps({name:importlib.metadata.version(name) for name in ('pydeseq2','pydantic','numpy')}))
'''
    result = subprocess.run([environment["python"], "-I", "-B", "-c", code], cwd=workspace,
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["numpy"] == importlib.metadata.version("numpy")
    assert environment["runtime_profile"] == "bulk"
    assert "torch" not in environment["dependency_distributions"]
    assert environment["runtime_source_hashes"] == {}


def test_public_validator_runs_in_real_bulk_runtime(bulk_runtime):
    from devbench.study.submission_validation import stage_submission_validator
    from tests.test_submission_validation import biology_answer
    workspace, _ = bulk_runtime
    staged = stage_submission_validator(workspace, "biology", max_answer_bytes=8_000_000)
    (workspace / "final_discovery.json").write_text(json.dumps(biology_answer()))
    result = subprocess.run(staged.argv, cwd=workspace, capture_output=True, text=True,
                            timeout=20, env={"PATH": os.defpath, "HOME": str(workspace)})
    assert result.returncode == 0, result.stderr + result.stdout
    assert json.loads(result.stdout)["valid"] is True


def test_entire_bulk_runtime_fingerprints_under_unchanged_limits(bulk_runtime):
    from devbench.study.execution import _runtime_roots, _tree_digest
    workspace, environment = bulk_runtime
    roots = _runtime_roots(workspace, environment)
    assert not any("torch" in str(root) for root in roots)
    for root in roots:
        assert len(_tree_digest(root, allowed_symlink_targets=roots)) == 64


def test_existing_per_file_guard_still_rejects_oversize_artifact(tmp_path):
    from devbench.study.execution import _file_digest
    path = tmp_path / "oversize.fixture"
    with path.open("wb") as stream:
        stream.truncate(250_000_001)
    with pytest.raises(ValueError, match="bounded regular file"):
        _file_digest(path)


def test_bulk_profile_does_not_silently_change_legacy_assisted_staging(tmp_path):
    from devbench.staging import stage_python
    with pytest.raises(ValueError, match="broker"):
        stage_python(tmp_path, assisted=True, profile="bulk")
    assert not (tmp_path / ".venv").exists()


def test_bulk_profile_is_not_silently_ignored_in_preflight_only():
    from devbench.study.biology_execution import execute_biology_study
    with pytest.raises(ValueError, match="scored"):
        execute_biology_study(None, None, None, workspace_root=None,
                              participant_runtime_profile="bulk", preflight_only=True)


def test_bulk_worker_is_registered_and_denied_without_authorization():
    from devbench.study import biology_execution as module
    worker = module._biology_bulk_attempt_worker
    assert getattr(module, worker.__name__) is worker
    assert worker.__qualname__ == worker.__name__ and worker.__closure__ is None
    with pytest.raises(PermissionError, match="disabled"):
        worker(None)


def test_bulk_profile_rejects_cellular_recipes_before_dispatch(monkeypatch):
    from types import SimpleNamespace
    from devbench.study import biology_execution as module
    study = SimpleNamespace(recipes=(SimpleNamespace(parameters=SimpleNamespace(cells_per_specimen=4)),))
    monkeypatch.setattr(module, "validate_biology_execution_configuration", lambda *args: (study, {}))
    with pytest.raises(ValueError, match="cellular"):
        module.execute_biology_study(study, {}, None, workspace_root=None,
                                     participant_runtime_profile="bulk", allow_data_execution=True)
