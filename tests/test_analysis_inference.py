import copy

import pytest


def result():
    return {"diagnostics": {"model_selection": {"requested_spline_candidates": [3, 5, 8],
             "model_selection_performed": True}}, "target_results": [{"target_id": "sampled_mean",
             "limitation_codes": [], "payload": {"kind": "effects", "gene_statistics": [
                 {"gene_id": "a", "estimate_status": "estimated", "p_value": .01, "q_value": .02},
                 {"gene_id": "b", "estimate_status": "estimated", "p_value": .04, "q_value": .04}]}}]}


def test_selected_model_p_is_adjusted_before_gene_bh_without_mutating_raw_output():
    from devbench.analysis.inference import adjust_selected_model_tests
    raw = result()
    before = copy.deepcopy(raw)
    corrected = adjust_selected_model_tests(raw)
    assert raw == before
    rows = corrected["target_results"][0]["payload"]["gene_statistics"]
    assert [x["p_value"] for x in rows] == pytest.approx([.03, .12])
    assert [x["q_value"] for x in rows] == pytest.approx([.06, .12])
    assert corrected["inference_review"]["count_dispersion_calibration_established"] is False


def test_single_model_does_not_receive_unnecessary_model_selection_penalty():
    from devbench.analysis.inference import adjust_selected_model_tests
    raw = result()
    raw["diagnostics"]["model_selection"].update(requested_spline_candidates=[3], model_selection_performed=False)
    rows = adjust_selected_model_tests(raw)["target_results"][0]["payload"]["gene_statistics"]
    assert [x["p_value"] for x in rows] == pytest.approx([.01, .04])


def test_unknown_search_scope_is_not_silently_treated_as_one_model():
    from devbench.analysis.inference import adjust_selected_model_tests
    raw = result()
    del raw["diagnostics"]["model_selection"]["requested_spline_candidates"]
    with pytest.raises(ValueError, match="candidate"):
        adjust_selected_model_tests(raw)


def test_failed_estimates_keep_missing_p_and_q():
    from devbench.analysis.inference import adjust_selected_model_tests
    raw = result()
    raw["target_results"][0]["payload"]["gene_statistics"].append(
        {"gene_id": "failed", "estimate_status": "failed", "p_value": None, "q_value": None})
    corrected = adjust_selected_model_tests(raw)
    rows = corrected["target_results"][0]["payload"]["gene_statistics"]
    assert rows[-1]["p_value"] is None and rows[-1]["q_value"] is None
    assert rows[0]["q_value"] == pytest.approx(.09)
