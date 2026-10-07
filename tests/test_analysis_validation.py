"""Scorecard checks independent of the preserved benchmark graders."""
import copy

import pytest


def example():
    truth = {"gene_ids": ["null", "positive", "negative"], "targets": {"sampled_mean": {
        "expected_refusal": False, "effect": [[0, 0], [0, 1], [0, -1]],
        "nonnull": [False, True, True]}}}
    rows = [{"gene_id": gene, "estimate_status": "estimated", "coordinate_ids": ["grid_0", "grid_1"],
             "values": values, "p_value": p, "q_value": q} for gene, values, p, q in [
                 ("null", [0, 0], .8, .8), ("positive", [0, 1], .001, .003),
                 ("negative", [0, -1], .002, .003)]]
    result = {"target_results": [{"target_id": "sampled_mean", "claim_type": "restricted_estimate",
                                  "payload": {"gene_statistics": rows}}]}
    return result, truth


def test_scorecard_measures_recovery_and_false_positives():
    from devbench.analysis.validation import score_known_truth
    result, truth = example()
    score = score_known_truth(result, truth, "sampled_mean")
    assert score["passed"] is True
    assert score["metrics"]["direction_accuracy"] == 1
    assert score["metrics"]["effect_rmse_log2"] == 0
    assert score["metrics"]["null_false_positive_rate"] == 0
    broken = copy.deepcopy(result)
    broken["target_results"][0]["payload"]["gene_statistics"][2]["values"][1] = 1
    assert score_known_truth(broken, truth, "sampled_mean")["passed"] is False


def test_missing_genes_and_misaligned_coordinates_cannot_be_silently_scored():
    from devbench.analysis.validation import score_known_truth
    result, truth = example()
    result["target_results"][0]["payload"]["gene_statistics"].pop()
    score = score_known_truth(result, truth, "sampled_mean")
    assert score["passed"] is False
    assert score["metrics"]["coverage"] == pytest.approx(2/3)
    result, truth = example()
    result["target_results"][0]["payload"]["gene_statistics"][1]["coordinate_ids"].reverse()
    with pytest.raises(ValueError, match="coordinate"):
        score_known_truth(result, truth, "sampled_mean")


def test_nonfinite_estimates_and_invalid_q_values_fail():
    from devbench.analysis.validation import score_known_truth
    result, truth = example()
    result["target_results"][0]["payload"]["gene_statistics"][0]["values"][1] = float("nan")
    with pytest.raises(ValueError, match="finite"):
        score_known_truth(result, truth, "sampled_mean")
    result, truth = example()
    result["target_results"][0]["payload"]["gene_statistics"][1]["q_value"] = 0
    with pytest.raises(ValueError, match="q"):
        score_known_truth(result, truth, "sampled_mean")


def test_refusal_requires_recorded_rank_evidence_and_no_estimate():
    from devbench.analysis.validation import score_known_truth
    truth = {"targets": {"adjusted_effect": {"expected_refusal": True}}}
    fragment = {"target_id": "adjusted_effect", "claim_type": "refusal",
                "limitation_codes": ["rank_deficient_design"], "payload": None}
    result = {"target_results": [fragment], "diagnostics": {
        "analysis_error": {"code": "rank_deficient_design"}},
        "independent_design_check": {"rank": 2, "columns": 3}}
    assert score_known_truth(result, truth, "adjusted_effect")["passed"] is True
    result["diagnostics"] = {}
    assert score_known_truth(result, truth, "adjusted_effect")["passed"] is False
    result["diagnostics"] = {"analysis_error": {"code": "rank_deficient_design"}}
    fragment["payload"] = {"gene_statistics": [{"values": [1]}]}
    assert score_known_truth(result, truth, "adjusted_effect")["passed"] is False


def test_measurement_start_failure_is_preserved_without_masking(tmp_path, monkeypatch):
    import json
    from devbench.analysis import resources
    from devbench.analysis.validation import run_validation

    class DeniedMeasurement:
        def __init__(self, name):
            pass

        def __enter__(self):
            raise PermissionError("measurement denied")

        def __exit__(self, *args):
            pass

    monkeypatch.setattr(resources, "StageMeasurement", DeniedMeasurement)
    output = tmp_path / "failed-run"
    with pytest.raises(PermissionError, match="measurement denied"):
        run_validation(tmp_path / "input", output)
    assert json.loads((output / "failure.json").read_text())["type"] == "PermissionError"
