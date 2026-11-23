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
