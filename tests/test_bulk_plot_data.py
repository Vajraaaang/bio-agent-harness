"""Saved bulk plotting evidence: no model refits, family filtering, or identity inference."""
from copy import deepcopy
from dataclasses import replace
import json

import numpy as np
import pandas as pd
import pytest


SOURCE = {"sha256": "a" * 64, "run_id": "run-1", "kind": "synthetic"}
CONTRAST = {"operation": "comparison_minus_reference", "reference_condition": "A",
            "comparison_condition": "B", "effect_scale": "log2_relative_expression",
            "normalization": "RLE_library_size_offsets"}


def observed(*, paired=False, count_values=None):
    from devbench.study.biology_tool_io import ObservedData
    samples = ("sB2", "sA1", "sB1", "sA2")
    metadata = pd.DataFrame({"condition": ["B", "A", "B", "A"],
        "donor_id": ["d2", "d1", "d1", "d2"] if paired else ["d4", "d1", "d3", "d2"],
        "specimen_id": ["p4", "p1", "p3", "p2"], "library_id": samples}, index=samples)
    values = np.array(count_values if count_values is not None else
                      [[16, 1, 8, 2], [0, 0, 0, 0], [2, 0, 0, 0]], dtype=float)
    return ObservedData(expression=values, gene_ids=tuple("g" + str(i) for i in range(len(values))),
        sample_ids=samples, metadata=metadata, annotations={}, measurement="raw_counts",
        unit_hierarchy={"population_unit": "donor", "population_unit_label": "donor_id",
                        "specimen_label": "specimen_id", "library_label": "library_id",
                        "identity_status": "declared"}, targets=(), provenance={},
        context={"analysis_columns": {"condition": "condition", "blocking": ["donor_id"] if paired else []}})


def calibration(genes):
    return {"min_disp": 1e-8, "max_disp": 10.0, "gene_estimates": {
        gene: {"genewise_dispersion": 0.4, "trend_dispersion": 0.2, "dispersion_alpha": 1e-8,
               "dispersion_outlier": False, "dispersion_converged": True} for gene in genes}}


def result(*, evidence=True, paired=False):
    data = observed(paired=paired)
    diagnostics = {"calibration": calibration(data.gene_ids), "model_warnings": {"g0": ["saved_warning"]}}
    if evidence:
        from devbench.analysis.bulk_plot_data import capture_bulk_plot_evidence
        diagnostics["plot_evidence"] = capture_bulk_plot_evidence(data, np.array([2., 1., 2., 1.]),
                                                                  diagnostics["calibration"])
    rows = [
        {"gene_id": "g0", "hypothesis_id": "h0", "estimate_status": "estimated", "values": [2.],
         "intervals": [{"lower": 1., "upper": 3., "confidence_level": 0.95}], "p_value": 0., "q_value": 0.},
        {"gene_id": "g1", "hypothesis_id": "h1", "estimate_status": "failed", "values": [],
         "intervals": [], "p_value": None, "q_value": None, "missing_reason": "all_zero"},
        {"gene_id": "g2", "hypothesis_id": "h2", "estimate_status": "estimated", "values": [0.],
         "intervals": [{"lower": -1., "upper": 1., "confidence_level": 0.95}], "p_value": 0.2, "q_value": 0.3},
    ]
    payload = {"kind": "effects", "gene_statistics": rows, "computed_contrast": CONTRAST,
               "biological_unit": "donor", "analysis_layer": "raw_counts"}
    return {"target_results": [{"target_id": "mean_contrast", "payload": payload}], "diagnostics": diagnostics}


def test_capture_retains_exact_unsorted_alignment_factors_and_means():
    from devbench.analysis.bulk_plot_data import capture_bulk_plot_evidence
    data = observed()
    evidence = capture_bulk_plot_evidence(data, [2., 1., 2., 1.], calibration(data.gene_ids))
    assert evidence["status"] == "available"
    assert evidence["sample_ids"] == list(data.sample_ids)
    assert evidence["raw_counts"] == data.expression.tolist()
    assert evidence["size_factors"] == [2., 1., 2., 1.]
    assert evidence["mean_normalized_counts"] == [3.75, 0., 0.25]
    assert [sample["condition"] for sample in evidence["samples"]] == ["B", "A", "B", "A"]
    assert [sample["unit_id"] for sample in evidence["samples"]] == ["d4", "d1", "d3", "d2"]
    data = replace(data, metadata=data.metadata.iloc[::-1])
    unavailable = capture_bulk_plot_evidence(data, [2., 1., 2., 1.], calibration(data.gene_ids))
    assert unavailable["status"] == "unavailable"
    assert unavailable["reason"] == "sample_metadata_alignment_mismatch"


@pytest.mark.parametrize("context", [{}, {"analysis_columns": None},
    {"analysis_columns": {"condition": None, "batches": [], "blocking": []}}])
def test_capture_uses_actual_resolved_condition_column_instead_of_literal_condition(context):
    from devbench.analysis.bulk_plot_data import capture_bulk_plot_evidence, build_bulk_plot_workspace, numeric_plot_data
    from devbench.study.biology_tools_bulk import _design
    data = observed()
    data.metadata["actual_group"] = ["A", "B", "A", "B"]
    data = replace(data, context=context)
    design, details = _design(data, "condition", {"condition_col": "actual_group",
        "reference_condition": "A", "comparison_condition": "B"})
    assert details["condition_column"] == "actual_group"
    evidence = capture_bulk_plot_evidence(data, [2., 1., 2., 1.], calibration(data.gene_ids), design=design)
    assert evidence["status"] == "available"
    assert evidence["pairing"]["condition_column"] == "actual_group"
    assert [sample["condition"] for sample in evidence["samples"]] == ["A", "B", "A", "B"]
    saved = result(evidence=False)
    saved["diagnostics"]["plot_evidence"] = evidence
    workspace = build_bulk_plot_workspace(saved, source=SOURCE)
    assert [row["x"]["value"] for row in numeric_plot_data(workspace, "expression", "g0")["rows"]] == [0, 1, 0, 1]


def test_design_without_resolved_plot_metadata_cannot_guess_condition_or_pairing():
    from devbench.analysis.bulk_plot_data import capture_bulk_plot_evidence
    data = observed(paired=True)
    design = pd.DataFrame({"intercept": [1.] * 4, "fixed_block_donor_id_d2": [1., 0., 0., 1.]},
                          index=data.sample_ids)
    evidence = capture_bulk_plot_evidence(data, [1.] * 4, calibration(data.gene_ids), design=design)
    assert evidence["status"] == "available"
    assert all(sample["condition"] is None and sample["pair_id"] is None for sample in evidence["samples"])
    assert evidence["pairing"]["reason"] == "resolved_design_plot_metadata_unavailable"


def test_full_workspace_preserves_saved_estimates_source_and_sparse_warnings():
    from devbench.analysis.bulk_plot_data import build_bulk_plot_workspace, validate_bulk_plot_workspace
    saved = result()
    before = deepcopy(saved)
    workspace = build_bulk_plot_workspace(saved, source=SOURCE)
    assert saved == before
    assert workspace["format"] == "devbench.bulk-plot-workspace/1"
    assert workspace["source"] == SOURCE
    assert workspace["effect_definition"] == CONTRAST
    assert workspace["effect_scale"] == "log2_relative_expression"
    assert workspace["biological_unit"] == "donor"
    assert [gene["gene_id"] for gene in workspace["genes"]] == ["g0", "g1", "g2"]
    first, failed, sparse = workspace["genes"]
    assert first["effect"] == 2. and first["p_value"] == first["q_value"] == 0.
    assert first["interval"] == {"lower": 1., "upper": 3., "level": 0.95, "kind": "confidence"}
    assert first["dispersion"] == {"raw": 0.4, "trend": 0.2, "final": 1e-8,
                                     "converged": True, "boundary": "lower", "outlier": False}
    assert "dispersion_at_lower_boundary" in first["warnings"]
    assert "saved_warning" in first["warnings"]
    assert failed["effect"] is None and failed["p_value"] is None
    assert failed["estimate_status"] == "failed" and failed["missing_reason"] == "all_zero"
    assert "all_zero_counts" in failed["warnings"]
    assert "counts_in_one_sample_only" in sparse["warnings"]
    assert not any("discover" in str(value) for value in workspace.values())
    assert validate_bulk_plot_workspace(workspace) == workspace
    json.dumps(workspace, allow_nan=False)


def test_saved_gene_calibration_warnings_and_global_limitations_are_retained():
    from devbench.analysis.bulk_plot_data import build_bulk_plot_workspace
    saved = result()
    saved["diagnostics"]["calibration_diagnostics"] = [
        {"code": "dispersion_optimizer_retry_recovered", "severity": "warning", "gene_ids": ["g0"]},
        {"code": "dispersion_shrinkage_unavailable", "severity": "warning", "message": "No prior was fitted"}]
    saved["diagnostics"]["calibration_warnings"] = ["saved calibration warning"]
    workspace = build_bulk_plot_workspace(saved, source=SOURCE)
    assert "dispersion_optimizer_retry_recovered" in workspace["genes"][0]["warnings"]
    assert "dispersion_optimizer_retry_recovered" not in workspace["genes"][1]["warnings"]
    assert any("dispersion_shrinkage_unavailable" in limitation for limitation in workspace["limitations"])
    assert any("saved calibration warning" in limitation for limitation in workspace["limitations"])


def test_historical_result_has_honest_availability_and_keeps_missing_rows():
    from devbench.analysis.bulk_plot_data import build_bulk_plot_workspace, numeric_plot_data, numeric_plot_coverage
    workspace = build_bulk_plot_workspace(result(evidence=False), source=SOURCE)
    assert workspace["availability"]["volcano"]["status"] == "available"
    assert workspace["availability"]["pvalue_histogram"]["status"] == "available"
    for kind in ("ma", "pca", "dispersion", "paired_expression", "expression"):
        assert workspace["availability"][kind]["status"] == "unavailable"
        assert workspace["availability"][kind]["reason"]
    volcano = numeric_plot_data(workspace, "volcano")
    assert volcano["membership"] == ["g0", "g1", "g2"]
    assert volcano["axes"]["y"]["field"] == "q_value"
    assert volcano["axes"]["y"]["transform"] == "negative_log10"
    assert volcano["rows"][0]["y"] == {"status": "missing", "value": None, "censoring": None}
    assert volcano["rows"][2]["y"]["value"] == 0.3
    coverage = numeric_plot_coverage(workspace, "volcano")
    assert coverage["total_entities"] == 3 and coverage["drawn_entities"] == 1
    assert coverage["undrawn_reason_counts"]["q_value_zero_has_no_finite_negative_log10"] == 1
    assert coverage["undrawn_reason_counts"]["estimate_failed"] == 1


def test_pairing_uses_declared_units_and_contrast_order_without_sample_id_inference():
    from devbench.analysis.bulk_plot_data import build_bulk_plot_workspace, numeric_plot_data
    paired = build_bulk_plot_workspace(result(paired=True), source=SOURCE)
    assert paired["pairing"]["status"] == "paired"
    plot = numeric_plot_data(paired, "paired_expression", "g0")
    assert plot["plot_kind"] == "paired_expression" and plot["entity_kind"] == "sample"
    assert [row["x"]["value"] for row in plot["rows"]] == [1, 0, 1, 0]
    assert [row["group_id"] for row in plot["rows"]] == ["d2", "d1", "d1", "d2"]
    assert [row["y"]["value"] for row in plot["rows"]] == [8., 1., 4., 2.]
    assert plot["axes"]["y"]["transform"] == "natural_log1p"
    unpaired = build_bulk_plot_workspace(result(), source=SOURCE)
    assert unpaired["pairing"]["status"] == "unpaired"
    assert unpaired["pairing"]["reason"] == "pairing_not_explicitly_declared"
    assert all(sample["pair_id"] is None for sample in unpaired["samples"])
    assert unpaired["availability"]["paired_expression"]["status"] == "unavailable"
    unpaired_plot = numeric_plot_data(unpaired, "expression", "g0")
    assert [row["group_id"] for row in unpaired_plot["rows"]] == list(observed().sample_ids)


def test_pca_known_geometry_is_descriptive_deterministic_and_reports_variance():
    from devbench.analysis.bulk_plot_data import build_bulk_plot_workspace, capture_bulk_plot_evidence
    saved = result()
    data = observed(count_values=[[0, 1, 3, 7], [7, 3, 1, 0], [0, 0, 0, 0]])
    saved["diagnostics"]["plot_evidence"] = capture_bulk_plot_evidence(data, [1.] * 4,
                                                                          calibration(data.gene_ids))
    workspace = build_bulk_plot_workspace(saved, source=SOURCE)
    pca = workspace["pca"]
    assert pca["producer"] == "devbench.log2-normalized-counts-pca/1"
    assert "log2" in pca["transform"] and "VST" not in pca["transform"]
    assert pca["explained_variance_ratio"] == pytest.approx([1., 0.], abs=1e-12)
    assert pca["explained_variance"] == pytest.approx([10 / 3, 0.], abs=1e-12)
    scores = np.array([[row["pc1"], row["pc2"]] for row in pca["scores"]])
    assert scores.mean(axis=0) == pytest.approx([0., 0.], abs=1e-12)
    assert np.linalg.norm(scores[0] - scores[3]) == pytest.approx(3 * np.sqrt(2))
    assert pca == build_bulk_plot_workspace(saved, source=SOURCE)["pca"]


def test_complete_family_above_old_5000_limit_is_not_truncated():
    from devbench.analysis.bulk_plot_data import build_bulk_plot_workspace, numeric_plot_data
    saved = result(evidence=False)
    rows = saved["target_results"][0]["payload"]["gene_statistics"]
    rows[:] = [dict(rows[2], gene_id=f"g{i}", hypothesis_id=f"h{i}") for i in range(6001)]
    workspace = build_bulk_plot_workspace(saved, source=SOURCE)
    assert len(workspace["genes"]) == 6001
    assert len(numeric_plot_data(workspace, "volcano")["membership"]) == 6001
    assert workspace["coverage"]["retained_gene_count"] == 6001
    assert workspace["coverage"]["truncated"] is False


def test_all_failed_saved_family_still_yields_inspectable_unavailable_workspace():
    from devbench.analysis.bulk_plot_data import build_bulk_plot_workspace
    saved = result(evidence=False)
    failed = saved["target_results"][0]["payload"]["gene_statistics"][1]
    saved["target_results"][0]["payload"] = None
    saved["diagnostics"]["failed_gene_statistics"] = {"mean_contrast": [failed]}
    workspace = build_bulk_plot_workspace(saved, source=SOURCE)
    assert workspace["genes"][0]["gene_id"] == "g1"
    assert workspace["genes"][0]["estimate_status"] == "failed"
    assert all(value["status"] == "unavailable" for value in workspace["availability"].values())


def test_partial_attempted_family_is_rejected_instead_of_silently_filtered():
    from devbench.analysis.bulk_plot_data import build_bulk_plot_workspace
    saved = result(evidence=False)
    saved["diagnostics"]["gene_test_adjustment_family"] = {
        "attempted_hypothesis_ids": ["h0", "h1", "h2", "h3"], "attempted_count": 4}
    with pytest.raises(ValueError, match="family"):
        build_bulk_plot_workspace(saved, source=SOURCE)


def test_sparse_condition_warnings_describe_counts_without_effect_cutoff():
    from devbench.analysis.bulk_plot_data import build_bulk_plot_workspace
    workspace = build_bulk_plot_workspace(result(), source=SOURCE)
    assert "zero_counts_in_reference_condition" in workspace["genes"][2]["warnings"]


def test_expression_availability_requires_recorded_computed_conditions():
    from devbench.analysis.bulk_plot_data import build_bulk_plot_workspace
    saved = result()
    for sample in saved["diagnostics"]["plot_evidence"]["samples"]:
        sample["condition"] = None
    workspace = build_bulk_plot_workspace(saved, source=SOURCE)
    assert workspace["availability"]["expression"]["status"] == "unavailable"


def test_result_gene_cap_explicitly_rejects_whole_family_without_slice():
    from devbench.analysis.bulk_plot_data import build_bulk_plot_workspace
    saved = result(evidence=False)
    rows = saved["target_results"][0]["payload"]["gene_statistics"]
    rows[:] = [rows[0]] * 100001
    with pytest.raises(ValueError, match="100000"):
        build_bulk_plot_workspace(saved, source=SOURCE)


def test_saved_capture_bounds_are_checked_before_descriptive_svd(monkeypatch):
    from devbench.analysis.bulk_plot_data import build_bulk_plot_workspace
    saved = result()
    evidence = saved["diagnostics"]["plot_evidence"]
    evidence["samples"] = [dict(evidence["samples"][0], sample_id=f"s{i}") for i in range(1001)]
    evidence["sample_ids"] = [sample["sample_id"] for sample in evidence["samples"]]
    evidence["size_factors"] = [sample["size_factor"] for sample in evidence["samples"]]
    evidence["raw_counts"] = [[1] * 1001 for _ in range(3)]
    def forbidden(*args, **kwargs):
        pytest.fail("invalid or over-budget saved evidence must not reach PCA")
    monkeypatch.setattr(np.linalg, "svd", forbidden)
    with pytest.raises(ValueError):
        build_bulk_plot_workspace(saved, source=SOURCE)


def test_unavailable_capture_preserves_known_extent_separately_from_retained_arrays():
    from devbench.analysis.bulk_plot_data import build_bulk_plot_workspace, capture_bulk_plot_evidence
    saved = result(evidence=False)
    data = replace(observed(), sample_ids=tuple(f"s{i}" for i in range(1001)))
    saved["diagnostics"]["plot_evidence"] = capture_bulk_plot_evidence(data, [], {})
    workspace = build_bulk_plot_workspace(saved, source=SOURCE)
    assert workspace["coverage"]["sample_count"] == 0
    assert workspace["coverage"]["evidence_sample_count"] == 1001
    assert workspace["coverage"]["evidence_matrix_value_count"] == 3003
    assert workspace["coverage"]["truncated"] is False


@pytest.mark.parametrize("defect", ["sample_limit", "matrix_limit", "nonpositive_factor", "fractional_count"])
def test_capture_limit_or_invalid_input_is_explicit_unavailable_without_truncation(defect):
    from devbench.analysis.bulk_plot_data import capture_bulk_plot_evidence
    data = observed()
    factors = np.ones(4)
    if defect == "sample_limit":
        data = replace(data, sample_ids=tuple(f"s{i}" for i in range(1001)))
    elif defect == "matrix_limit":
        data = replace(data, gene_ids=tuple(f"g{i}" for i in range(100000)),
                       sample_ids=tuple(f"s{i}" for i in range(11)))
    elif defect == "nonpositive_factor":
        factors[0] = 0.
    else:
        data.expression[0, 0] = 0.5
    evidence = capture_bulk_plot_evidence(data, factors, {})
    assert evidence["status"] == "unavailable" and evidence["reason"]
    assert evidence["raw_counts"] is None
    assert evidence["truncated"] is False


@pytest.mark.parametrize("defect", ["duplicate_gene", "nonfinite", "q_less_p", "bad_counts", "bad_source", "misaligned_evidence"])
def test_strict_validator_rejects_invalid_or_changed_workspace(defect):
    from devbench.analysis.bulk_plot_data import build_bulk_plot_workspace, validate_bulk_plot_workspace
    workspace = build_bulk_plot_workspace(result(), source=SOURCE)
    if defect == "duplicate_gene":
        workspace["genes"][1]["gene_id"] = "g0"
    elif defect == "nonfinite":
        workspace["genes"][0]["effect"] = float("nan")
    elif defect == "q_less_p":
        workspace["genes"][2]["q_value"] = 0.01
    elif defect == "bad_counts":
        workspace["genes"][0]["raw_counts"].pop()
    elif defect == "bad_source":
        workspace["source"]["sha256"] = "bad"
    else:
        workspace["pca"]["scores"][0]["sample_id"] = "alien"
    with pytest.raises(ValueError):
        validate_bulk_plot_workspace(workspace)


def test_histogram_retains_zero_one_and_missing_accounting():
    from devbench.analysis.bulk_plot_data import build_bulk_plot_workspace, numeric_plot_data, numeric_plot_coverage
    saved = result(evidence=False)
    saved["target_results"][0]["payload"]["gene_statistics"][2].update(p_value=1., q_value=1.)
    workspace = build_bulk_plot_workspace(saved, source=SOURCE)
    plot = numeric_plot_data(workspace, "pvalue_histogram")
    assert plot["plot_kind"] == "histogram" and plot["entity_kind"] == "bin"
    assert len(plot["rows"]) == 20
    assert plot["rows"][0]["y"]["value"] == 1
    assert plot["rows"][-1]["y"]["value"] == 1
    assert sum(row["y"]["value"] for row in plot["rows"]) == 2
    coverage = numeric_plot_coverage(workspace, "pvalue_histogram")
    assert coverage["valid_p_values"] == 2 and coverage["missing_p_values"] == 1


def test_existing_synthetic_bulk_fit_emits_actual_alignment_and_calibration(tmp_path):
    from devharness_server.dispatcher import ToolDispatcher
    from tests.test_registered_bulk_contrast import bulk_inputs
    task, paths, options = bulk_inputs(tmp_path, continuous=False)
    actual = json.loads(ToolDispatcher(allowed_root=tmp_path).execute("bulk_condition_contrast",
        {"counts_path": str(paths["expression"]), "metadata_path": str(paths["metadata"]),
         "public_task": task, "options": options}))
    evidence = actual["diagnostics"].get("plot_evidence")
    assert evidence is not None, "actual _fit_family must retain its plotting evidence"
    assert evidence["status"] == "available"
    assert len(evidence["sample_ids"]) == len(evidence["size_factors"]) == 8
    assert evidence["gene_ids"] == task["gene_ids"]
    assert len(evidence["raw_counts"]) == len(task["gene_ids"])
    assert all(value > 0 for value in evidence["size_factors"])
    from devbench.analysis.bulk_plot_data import build_bulk_plot_workspace
    workspace = build_bulk_plot_workspace(actual, source=SOURCE)
    assert workspace["availability"]["ma"]["status"] == "available"
    for gene in workspace["genes"]:
        assert gene["dispersion"]["final"] == actual["diagnostics"]["calibration"]["gene_estimates"][gene["gene_id"]]["dispersion_alpha"]
