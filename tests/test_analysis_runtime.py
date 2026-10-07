import hashlib

import pytest


def test_runtime_records_declared_tier_and_observed_environment():
    from devbench.analysis.runtime import observe_runtime
    result = observe_runtime("colab", detect_gpu=False)
    assert result["execution_tier"] == "colab"
    assert result["local_permission_preflight_inherited"] is False
    assert result["runtime"]["RAM_total_bytes"] > 0
    assert result["runtime"]["python"]
    assert result["runtime"]["packages"]["anndata"]
    assert result["runtime"]["GPU"]["status"] == "not_inspected"
    assert result["tier_scope"] == "runner_declared_not_platform_attestation"


def test_hash_mismatch_is_a_failure_not_a_warning(tmp_path):
    from devbench.analysis.runtime import verify_data_identity
    path = tmp_path / "data.h5ad"
    path.write_bytes(b"input bytes")
    expected = hashlib.sha256(path.read_bytes()).hexdigest()
    assert verify_data_identity(path, expected)["matched"]
    path.write_bytes(b"changed input")
    with pytest.raises(ValueError, match="data_sha256_mismatch"):
        verify_data_identity(path, expected)


def test_invalid_tier_is_rejected():
    from devbench.analysis.runtime import observe_runtime
    with pytest.raises(ValueError, match="tier"):
        observe_runtime("trusted_colab")


def test_colab_bootstrap_reuses_source_allowlist_and_locked_project_environment(tmp_path):
    from pathlib import Path
    from devbench.analysis.notebooks import colab_bootstrap_cells
    project = Path(__file__).resolve().parents[1]
    cells, identity = colab_bootstrap_cells(project, runtime_root=tmp_path)
    assert "uv.lock" in identity["files"]
    assert all(not name.startswith(("results/", "workbench_runs/")) for name in identity["files"])
    assert "--locked" in cells[1] and "analysis" in cells[1]
    assert "drive.mount" in cells[2]
    assert "INSTALL_CODEX" not in "\n".join(cells)
    for cell in cells:
        compile(cell, "colab-bootstrap", "exec")
    namespace = {}
    exec(compile(cells[0], "colab-bootstrap", "exec"), namespace)
    assert (namespace["PROJECT"] / "uv.lock").read_bytes() == (project / "uv.lock").read_bytes()
