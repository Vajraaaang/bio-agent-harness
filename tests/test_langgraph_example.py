"""Executable offline RNA entrypoint, not a native discovery evaluation."""
import importlib.util

import pytest


@pytest.mark.parametrize("representation", ["raw_counts", "normalized_expression"])
def test_offline_example_uses_real_rna_planner_and_abstains(tmp_path, representation):
    pytest.importorskip("langgraph")
    assert importlib.util.find_spec("scripts.run_serial_rna_workflow_demo") is not None
    from scripts.run_serial_rna_workflow_demo import run_demo
    result = run_demo(tmp_path / "demo", representation=representation)
    assert result["status"] == "abstained"
    assert result["representation"] == representation
    assert result["model_provider_calls"] == result["method_runs"] == 0
    assert result["recorded_selections"] == 3
    assert result["metadata_reordered"] is True
    assert result["biological_independence"] is None
    assert result["approvals"] == {}
    assert result["input_bytes_unchanged"] is True
    assert result["evidence_replay_calls"] == 0
    with pytest.raises(FileExistsError):
        run_demo(tmp_path / "demo", representation=representation)
