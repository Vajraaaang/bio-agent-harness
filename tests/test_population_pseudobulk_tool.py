"""Opt-in registered pseudobulk capsule staging with runner-owned evidence."""

import hashlib
import json
from pathlib import Path

import pytest

from devharness_server.dispatcher import ToolDispatcher, capture_tool_events
from tests.test_scrna_population_analysis import fixture

_TOOL = "scrna_population_pseudobulk_hdf5"


def test_binary_pseudobulk_is_opt_in_and_records_verified_artifact(workspace_tmp_path):
    source, spec, _, _ = fixture(workspace_tmp_path)
    output = workspace_tmp_path / "counts.h5"
    arguments = {
        "anndata_path": str(source),
        "specification": spec,
        "output_path": str(output),
    }

    default = ToolDispatcher(allowed_root=workspace_tmp_path)
    assert _TOOL not in {schema["name"] for schema in default.get_schemas()}
    with pytest.raises(ValueError, match="Unknown tool"):
        default.execute(_TOOL, arguments)
    with pytest.raises(ValueError, match="workspace"):
        ToolDispatcher(allow_artifact_writes=True)
    assert not output.exists()

    dispatcher = ToolDispatcher(
        allowed_root=workspace_tmp_path, allow_artifact_writes=True
    )
    legacy = next(
        schema
        for schema in dispatcher.get_schemas()
        if schema["name"] == "scrna_population_pseudobulk"
    )
    assert set(legacy["inputSchema"]["properties"]) == {"anndata_path", "specification"}

    with capture_tool_events() as events:
        result = json.loads(dispatcher.execute(_TOOL, arguments))
    assert result["supervision"]["cleanup"]["verified_stopped"] is True
    assert result["matrix"]["name"] == output.name
    assert result["matrix"]["shape"] == [24, 12]
    with output.open("rb") as stream:
        assert (
            hashlib.file_digest(stream, "sha256").hexdigest()
            == result["matrix"]["sha256"]
        )
    assert "counts" not in result
    assert events[0]["tool_name"] == _TOOL and events[0]["succeeded"] is True
    assert events[0]["result"] == result


def test_binary_pseudobulk_stays_scoped_and_withholds_after_unverified_cleanup(
    workspace_tmp_path,
    monkeypatch,
):
    from devharness_core import watchdog

    root = workspace_tmp_path / "root"
    root.mkdir()
    source, spec, _, _ = fixture(root)
    dispatcher = ToolDispatcher(allowed_root=root, allow_artifact_writes=True)
    arguments = {"anndata_path": str(source), "specification": spec}

    outside = workspace_tmp_path / "outside.h5"
    with pytest.raises(PermissionError, match="outside the assigned workspace"):
        dispatcher.execute(_TOOL, {**arguments, "output_path": str(outside)})
    assert not outside.exists()

    def unsafe_worker(function, args, *, diagnostics, **kwargs):
        Path(args[2]).write_bytes(b"worker output")
        diagnostics["cleanup"] = {"verified_stopped": False}
        return {}

    monkeypatch.setattr(watchdog, "run_sandboxed", unsafe_worker)
    output = root / "counts.h5"
    with pytest.raises(RuntimeError, match="cleanup was not verified"):
        dispatcher.execute(_TOOL, {**arguments, "output_path": str(output)})
    assert not output.exists()
