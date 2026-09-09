"""Distribution metadata changes must invalidate numerical replay admission."""
import importlib.metadata
from types import SimpleNamespace

import pytest

from devbench_engine import runner, audit_cli
from devharness_core.provenance import ExecutionDAG
from devharness_server.dispatcher import ToolDispatcher, capture_tool_events


def _metadata(monkeypatch, versions):
    monkeypatch.setattr(importlib.metadata, "version", lambda name: versions.get(name, "1.0"))
    monkeypatch.setattr(importlib.metadata, "distributions", lambda: [SimpleNamespace(metadata={"Name":name}, version=version, entry_points=()) for name,version in versions.items()])


def _history(tmp_path):
    work = tmp_path / "original"
    work.mkdir()
    source = work / "metadata.csv"
    source.write_text("sample_id,time\ns1,0\ns2,1\n")
    dag = ExecutionDAG(tmp_path / "audit")
    parent = dag.append("run_started", {"code_hash":runner._code_fingerprint(), "environment":runner._environment()})
    parent = dag.append("inputs_staged", {"workspace":str(work), "artifacts":{"metadata.csv":dag.add_artifact(source)}}, parents=[parent])
    with capture_tool_events() as events:
        ToolDispatcher().execute("calculate_temporal_monotonicity", {"stage_values":{"0":1.,"1":2.}})
    parent = dag.append("tool_completed", events[0], parents=[parent])
    return dag.finalize()


@pytest.mark.parametrize("package", ["patsy", "jsonschema", "pydantic", "h5py", "numcodecs", "psutil", "mcp", "cryptography"])
def test_previously_omitted_package_drift_refuses_replay_before_restore(tmp_path, monkeypatch, package):
    versions = {name:"1.0" for name in ("numpy", "scipy", "pandas", "statsmodels", "pydeseq2", "scikit-learn", "zarr", "networkx", package)}
    _metadata(monkeypatch, versions)
    receipt = _history(tmp_path)
    versions[package] = "1.0.post1"
    output = tmp_path / "replayed"
    result = audit_cli.replay_execution(receipt["manifest_path"], receipt["root_hash"], output)
    assert result["passed"] is False
    assert result["runtime_match"] is False
    assert result["replayed_tool_count"] == 0
    assert not output.exists()


def test_inventory_records_nonlegacy_dependencies_and_normalizes_names(monkeypatch):
    _metadata(monkeypatch, {"Some_Transitive.Dependency":"2.0", "NumPy":"1.0", "numpy":"1.0"})
    environment = runner._environment()
    assert environment["distribution_versions"] == {"numpy":"1.0", "some-transitive-dependency":"2.0"}
    assert environment["packages"]["numpy"] == "1.0"
    assert environment["distribution_version_scope"] == "metadata_visible_on_python_search_path"
    assert environment["native_library_identity_recorded"] is False


@pytest.mark.parametrize("versions", [{"foo_bar":"1.0", "foo-bar":"2.0"}, {"":"1.0"}, {"package":""}, {}, {"package":None}])
def test_ambiguous_or_missing_distribution_identity_fails_explicitly(monkeypatch, versions):
    _metadata(monkeypatch, versions)
    with pytest.raises(ValueError, match="[Dd]istribution"):
        runner._environment()


def test_distribution_discovery_is_bounded_without_materializing_the_iterator(monkeypatch):
    visited = []
    def distributions():
        while True:
            visited.append(1)
            yield SimpleNamespace(metadata={"Name":"duplicate"}, version="1.0")
    monkeypatch.setattr(importlib.metadata, "distributions", distributions)
    with pytest.raises(ValueError, match="bound"):
        runner._environment()
    assert len(visited) == 4097


def test_unchanged_visible_metadata_descriptor_replays_numerical_call(tmp_path, monkeypatch):
    _metadata(monkeypatch, {"numpy":"1.0", "patsy":"1.0", "jsonschema":"1.0"})
    receipt = _history(tmp_path)
    result = audit_cli.replay_execution(receipt["manifest_path"], receipt["root_hash"], tmp_path / "replay")
    assert result["passed"] and result["runtime_match"] and result["replayed_tool_count"] == 1
