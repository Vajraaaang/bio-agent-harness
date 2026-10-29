"""Demo blocks without real science services; injected fixtures prove engineering only."""
import json

import pytest


@pytest.mark.parametrize("representation", ["raw_counts", "normalized_expression"])
def test_offline_example_retains_explicit_block_without_scientific_runtime(tmp_path, representation):
    from scripts.run_serial_rna_workflow_demo import run_demo
    result = run_demo(tmp_path / "demo", representation=representation)
    assert result["status"] == "blocked"
    assert result["representation"] == representation
    assert "scientific" in result["blocker"]["message"].lower()
    assert result["model_provider_calls"] == result["method_runs"] == result["recorded_selections"] == 0
    assert result["scientific_qualification"] == "not_established"
    assert json.loads((tmp_path / "demo" / "result.json").read_bytes()) == result
    assert result["metadata_reordered"] is True and result["biological_independence"] is None
    assert result["approvals"] == {} and result["input_bytes_unchanged"] is True
    with pytest.raises(FileExistsError):
        run_demo(tmp_path / "demo", representation=representation)


@pytest.mark.parametrize("representation", ["raw_counts", "normalized_expression"])
def test_explicit_engineering_runtime_demonstrates_route_without_scientific_endorsement(tmp_path, representation):
    from scripts.run_serial_rna_workflow_demo import run_demo
    from tests.support.workflow_science_fixtures import RecordingScientificRuntime
    result = run_demo(tmp_path / "injected", representation=representation, scientific_workflow=RecordingScientificRuntime())
    assert result["status"] == "abstained"
    assert result["method_runs"] == 0 and result["recorded_selections"] == 3
    assert result["scientific_qualification"] == "not_established"
    assert result["model_provider_calls"] is None  # The demo does not audit an injected runtime's external calls.
    assert result["input_bytes_unchanged"] is True and result["approvals"] == {}
