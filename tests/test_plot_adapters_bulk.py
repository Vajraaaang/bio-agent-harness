"""Synthetic scientific-contract tests and public browser-gallery packets.

All quantities are deliberately synthetic. The fixtures demonstrate rendering
and rejection semantics; they are not evidence of scientific calibration.
"""
from __future__ import annotations

from copy import deepcopy
import importlib
import importlib.util
import math

import pytest


def fixture_packets():
    """Return one small labeled synthetic packet for every bulk capability."""
    binding = {"cohort_id": "synthetic-cohort", "contrast_id": "treated-control",
               "gene_id_namespace": "synthetic", "effect_unit": "log2_fold_change"}
    genes = [
        {"gene_id": "G1", "base_mean": 99.0, "raw_lfc": 2.0, "shrunken_lfc": 1.5,
         "raw_uncertainty": .4, "shrunken_uncertainty": .2, "p_value": .001,
         "q_value": .01, "boundary_warning": False, "filter_status": "retained"},
        {"gene_id": "G2", "base_mean": 3.0, "raw_lfc": -30.0, "shrunken_lfc": -.2,
         "raw_uncertainty": 15.0, "shrunken_uncertainty": .5, "p_value": 0.0,
         "q_value": 0.0, "boundary_warning": True, "filter_status": "retained"},
        {"gene_id": "G3", "base_mean": 0.0, "raw_lfc": None, "shrunken_lfc": None,
         "raw_uncertainty": None, "shrunken_uncertainty": None, "p_value": None,
         "q_value": None, "boundary_warning": False, "filter_status": "filtered_low_count",
         "reason": "saved_fit_unavailable"},
    ]
    effects = {"binding": binding, "population_unit": "gene", "genes": genes,
               "estimators": {"raw": {"id": "synthetic-mle", "uncertainty_type": "standard_error",
                                      "uncertainty_unit": "log2_fold_change"},
                              "shrunken": {"id": "synthetic-posterior", "uncertainty_type": "posterior_sd",
                                           "uncertainty_unit": "log2_fold_change"}}}
    samples = [
        {"sample_id": "S1", "biological_unit_id": "D1", "condition": "control"},
        {"sample_id": "S2", "biological_unit_id": "D1", "condition": "treated"},
        {"sample_id": "S3", "biological_unit_id": "D2", "condition": "control"},
        {"sample_id": "S4", "biological_unit_id": "D2", "condition": "treated"},
    ]
    pairs = [
        {"pair_id": "D1", "biological_unit_id": "D1", "from_sample": "S1", "to_sample": "S2"},
        {"pair_id": "D2", "biological_unit_id": "D2", "from_sample": "S3", "to_sample": "S4"},
    ]
    pairing = {"biological_unit": "donor", "from_condition": "control", "to_condition": "treated", "pairs": pairs}
    sample_common = {"population_unit": "sample", "biological_unit": "donor", "samples": samples}
    scores = [{"sample_id": s["sample_id"], "scores": v} for s, v in zip(samples,
              [[-2., -1., -.5, .1], [0., -1., .5, .1], [0., 1., -.5, -.1], [2., 1., .5, -.1]])]
    pca = {**sample_common, "scores": scores, "explained_variance_ratio": [.6, .25, .1, .05],
           "score_unit": "VST_expression_score", "transformation": "saved VST; gene centered",
           "pairing": pairing, "loadings": [{"gene_id": "G1", "loadings": [.8, .2, .1, 0.]},
                                              {"gene_id": "G2", "loadings": [-.2, .7, 0., .2]}],
           "loading_scale": 2., "loading_unit": "dimensionless", "loading_definition": "unit eigenvectors"}
    distances = [[0., 2., math.sqrt(8), math.sqrt(20)], [2., 0., 2., math.sqrt(8)],
                 [math.sqrt(8), 2., 0., 2.], [math.sqrt(20), math.sqrt(8), 2., 0.]]
    tracks = {"condition": {s["sample_id"]: s["condition"] for s in samples},
              "donor": {s["sample_id"]: s["biological_unit_id"] for s in samples},
              "size_factor": {"S1": .8, "S2": 1., "S3": 1.1, "S4": 1.2}}
    heat = {**sample_common, "sample_ids": [s["sample_id"] for s in samples],
            "matrix": distances, "distance_definition": "Euclidean distance on saved VST",
            "unit": "VST_expression_distance", "covariate_tracks": tracks,
            "cluster": {"method": "average", "metric": "precomputed"}}
    correlation = deepcopy(heat)
    correlation.pop("distance_definition")
    correlation.update(matrix=[[1., .7, .2, .1], [.7, 1., .1, .2], [.2, .1, 1., .8], [.1, .2, .8, 1.]],
                       correlation_definition="Pearson correlation on saved VST", unit="correlation",
                       cluster={"method": "average", "metric": "one_minus_correlation"})
    hist_genes = [{"gene_id": "G1", "p_value": 0., "filter_status": "retained", "boundary_warning": True},
                  {"gene_id": "G2", "p_value": .1, "filter_status": "retained", "boundary_warning": False},
                  {"gene_id": "G3", "p_value": 1., "filter_status": "filtered_low_count", "boundary_warning": True},
                  {"gene_id": "G4", "p_value": None, "filter_status": "filtered_low_count", "reason": "not_tested"}]
    paired = {"population_unit": "sample", "biological_unit": "donor", "samples": samples,
              "pairing": pairing, "gene_ids": ["G1", "G2"], "normalized_counts": [[1., 3., 2., 4.], [0., 0., 0., None]],
              "normalization": "saved RLE size factors", "expression_unit": "normalized_count",
              "contribution_definition": "absolute_log2_difference_share", "pseudocount": .5}
    qc = {**sample_common, "measurement": "raw_integer_count", "gene_ids": ["G1", "G2", "G3"],
          "counts": [[0, 10, 4, 20], [10, 0, 2, 11], [1, 2, 0, 3]],
          "size_factors": [{"sample_id": x["sample_id"], "size_factor": f} for x, f in zip(samples, [.8, 1., 1.1, 1.2])],
          "size_factor_method": "saved RLE"}
    cooks = {**sample_common, "gene_ids": ["G1", "G2", "G3", "G4"],
             "cooks_distances": [[0., .01, .1, .2], [.1, .1, .2, .4], [.2, .3, .3, .6], [1., 2., 4., None]],
             "model_id": "synthetic-nb-fit", "distance_unit": "dimensionless"}
    influence = {"population_unit": "pair", "biological_unit": "donor", "binding": binding,
                 "full_fit_id": "synthetic-full-fit", "definition": "leave_one_pair_out_minus_full_log2_effect",
                 "pairs": [{"pair_id": "D1", "biological_unit_id": "D1"}, {"pair_id": "D2", "biological_unit_id": "D2"}],
                 "genes": [{"gene_id": "G1", "full_effect": 2., "omissions": [
                     {"pair_id": "D1", "fit_id": "minus-D1", "effect": 1.5},
                     {"pair_id": "D2", "fit_id": "minus-D2", "effect": None, "reason": "refit_failed"}]}]}
    method_a = {"method_id": "synthetic-A", "test_id": "Wald", "binding": binding,
                "genes": [{"gene_id": "G1", "effect": 2., "p_value": .01},
                          {"gene_id": "G2", "effect": -.5, "p_value": 0.},
                          {"gene_id": "G3", "effect": None, "p_value": None, "reason": "not_fit"}]}
    method_b = {"method_id": "synthetic-B", "test_id": "LRT", "binding": deepcopy(binding),
                "genes": [{"gene_id": "G3", "effect": 0., "p_value": .8},
                          {"gene_id": "G1", "effect": 1., "p_value": .1},
                          {"gene_id": "G2", "effect": -1., "p_value": .2}]}
    decisions = {"population_unit": "gene", "binding": binding, "gene_ids": ["G1", "G2", "G3"],
                 "methods": [
                    {"method_id": "A", "binding": deepcopy(binding), "selection_definition": "saved q < 0.05 decision",
                     "genes": [{"gene_id": "G1", "selected": True, "disposition": "selected"},
                               {"gene_id": "G2", "selected": False, "disposition": "boundary_warning"},
                               {"gene_id": "G3", "selected": False, "disposition": "filtered_low_count"}]},
                    {"method_id": "B", "binding": deepcopy(binding), "selection_definition": "saved q < 0.05 decision",
                     "genes": [{"gene_id": "G1", "selected": True, "disposition": "selected"},
                               {"gene_id": "G2", "selected": True, "disposition": "selected"},
                               {"gene_id": "G3", "selected": False, "disposition": "filtered_low_count"}]}]}
    return [
        {"capability": "bulk_ma_comparison", "data": deepcopy(effects)},
        {"capability": "bulk_volcano", "data": {**deepcopy(effects), "probability_mode": "p", "alpha": .05, "effect_threshold": 1., "label_gene_ids": ["G1", "G2"]}},
        {"capability": "bulk_dispersion", "data": {"population_unit": "gene", "dispersion_unit": "dimensionless",
            "mean_unit": "normalized_count", "model_id": "synthetic-nb-fit",
            "genes": [{"gene_id": "G1", "mean": 10., "gene_wise": .2, "final": .15, "boundary_warning": False},
                      {"gene_id": "G2", "mean": 0., "gene_wise": 0., "final": None, "boundary_warning": True, "reason": "boundary"}],
            "trend": [{"mean": 1., "dispersion": .4}, {"mean": 10., "dispersion": .1}], "trend_method": "saved parametric"}},
        {"capability": "bulk_pca", "data": pca},
        {"capability": "bulk_mds", "data": {**sample_common, "mode": "distance_matrix", "sample_ids": [s["sample_id"] for s in samples],
            "distance_matrix": distances, "distance_definition": "Euclidean distance on saved VST", "distance_unit": "VST_expression_distance", "pairing": pairing}},
        {"capability": "bulk_sample_distance_heatmap", "data": heat},
        {"capability": "bulk_sample_correlation_heatmap", "data": correlation},
        {"capability": "bulk_pvalue_histogram", "data": {"population_unit": "gene", "genes": hist_genes, "bin_count": 20,
            "test_id": "synthetic-Wald", "uniform_reference": {"definition": "hypothetical_uniform_over_observed_finite_p", "scope": "all_finite"},
            "boundary_evidence": {"evidence_id": "synthetic-saved-boundary-diagnostics", "source_field": "saved_fit.boundary_warning",
                                  "definition": "saved model boundary diagnostic, not inferred from p or effect magnitude"}}},
        {"capability": "bulk_independent_filtering", "data": {"population_unit": "gene", "alpha": .05, "selection_id": "saved-filter-1",
            "filter_statistic": "mean_normalized_count", "quantile_unit": "fraction", "selected_quantile": .2,
            "selection_rule": "saved selection", "tested_gene_count": 100,
            "curve": [{"quantile": 0., "rejections": 10}, {"quantile": .2, "rejections": 15}, {"quantile": .4, "rejections": None, "reason": "not_evaluated"}]}},
        {"capability": "bulk_paired_expression", "data": paired},
        {"capability": "bulk_library_qc", "data": qc},
        {"capability": "bulk_cooks_distance", "data": cooks},
        {"capability": "bulk_leave_one_pair_out", "data": influence},
        {"capability": "bulk_concordance", "data": {"population_unit": "gene", "left": method_a, "right": method_b,
            "comparison_kind": "cross_method_and_test", "effect_comparability": "same fitted coefficient, different tests"}},
        {"capability": "bulk_upset_dispositions", "data": decisions},
        {"capability": "bulk_residual_diagnostics", "data": {
            "population_unit": "gene_sample", "biological_unit": "donor", "samples": deepcopy(samples),
            "model_id": "synthetic-nb-fit", "residual_type": "pearson", "residual_unit": "dimensionless",
            "residual_definition": "saved (observed - fitted mean) / saved model standard deviation",
            "fitted_unit": "normalized_count", "group_by": "condition",
            "records": [
                {"gene_id": "G1", "sample_id": "S1", "fitted": 2., "residual": -.5},
                {"gene_id": "G1", "sample_id": "S2", "fitted": 4., "residual": .2},
                {"gene_id": "G1", "sample_id": "S3", "fitted": 3., "residual": .3},
                {"gene_id": "G1", "sample_id": "S4", "fitted": 5., "residual": -.2},
                {"gene_id": "G2", "sample_id": "S1", "fitted": 1., "residual": -1.},
                {"gene_id": "G2", "sample_id": "S2", "fitted": 2., "residual": .8},
                {"gene_id": "G2", "sample_id": "S3", "fitted": 1.5, "residual": .1},
                {"gene_id": "G2", "sample_id": "S4", "fitted": None, "residual": None, "reason": "saved_fit_failed"},
            ]}},
        {"capability": "bulk_design_diagnostics", "data": {
            **deepcopy(sample_common), "model_id": "synthetic-confounded-design", "sample_ids": [s["sample_id"] for s in samples],
            "columns": [{"column_id": "intercept", "unit": "dimensionless", "role": "intercept"},
                        {"column_id": "treatment", "unit": "indicator", "role": "condition"},
                        {"column_id": "confounded_batch", "unit": "indicator", "role": "batch"}],
            "matrix": [[1., 0., 0.], [1., 1., 1.], [1., 0., 0.], [1., 1., 1.]],
            "diagnostics": {"mode": "saved", "evidence_id": "synthetic-saved-design-diagnostics", "method": "saved SVD",
                            "rank": 2, "condition_number": None, "condition_number_definition": "full_column_rank_2norm",
                            "singular_values": [math.sqrt(4+math.sqrt(8)), math.sqrt(4-math.sqrt(8)), 0.],
                            "rank_tolerance": 1e-10, "status": "rank_deficient", "reason": "singular_design"},
            "contrasts": [{"contrast_id": "isolated-treatment", "weights": [0., 1., 0.], "status": "unestimable",
                           "evidence_id": "saved-contrast-check", "reason": "treatment_and_batch_columns_identical"}]}},
        {"capability": "bulk_mean_variance", "data": {
            "population_unit": "gene", "biological_unit": "donor", "samples": deepcopy(samples),
            "measurement_scale": "normalized_count", "mean_unit": "normalized_count", "variance_unit": "normalized_count_squared",
            "mean_definition": "saved arithmetic mean across the listed sample normalized counts",
            "variance_definition": "saved empirical sample variance across the listed sample normalized counts",
            "variance_ddof": 1, "display_transform": "log10",
            "genes": [{"gene_id": "G1", "empirical_mean": 10., "empirical_variance": 100., "sample_ids": [s["sample_id"] for s in samples]},
                      {"gene_id": "G2", "empirical_mean": 0., "empirical_variance": 0., "sample_ids": [s["sample_id"] for s in samples]},
                      {"gene_id": "G3", "empirical_mean": 4., "empirical_variance": None, "sample_ids": [s["sample_id"] for s in samples], "reason": "saved_variance_missing"}]}},
    ]


def _adapter():
    name = "devbench.analysis.plot_adapters_bulk"
    assert importlib.util.find_spec(name) is not None, "bulk scientific adapters must be implemented"
    return importlib.import_module(name)


def _packet(name):
    return deepcopy(next(p["data"] for p in fixture_packets() if p["capability"] == name))


@pytest.mark.parametrize("packet", fixture_packets(), ids=lambda p: p["capability"])
def test_every_capability_has_real_valid_geometry_and_preserved_membership(packet):
    mod = _adapter()
    panels = mod.build_panels(packet["capability"], packet["data"])
    assert panels
    assert any(p["marks"] for p in panels)
    for p in panels:
        assert p["capability"] == packet["capability"]
        assert p["rows"]
        assert p["marks"] or all(r["x"] is None or r["y"] is None for r in p["rows"]), p["title"]
        assert len({r["key"] for r in p["rows"]}) == len(p["rows"])
        assert all(r["reason"] for r in p["rows"] if r["x"] is None or r["y"] is None)
        assert all(math.isfinite(r[k]) for r in p["rows"] for k in ["x", "y"] if r[k] is not None)
        assert {key for mark in p["marks"] for key in mark["keys"]} <= {r["key"] for r in p["rows"]}


def test_ma_keeps_raw_and_posterior_uncertainty_separate_without_magnitude_badge():
    d = _packet("bulk_ma_comparison")
    d["genes"][1]["boundary_warning"] = False
    panels = _adapter().build_panels("bulk_ma_comparison", d)
    assert len(panels) == 2
    assert panels[0]["rows"][0]["x"] == pytest.approx(2.)
    assert panels[0]["rows"][1]["y"] == -30
    assert panels[1]["rows"][1]["y"] == -.2
    assert "standard_error" in " ".join(panels[0]["annotations"])
    assert "posterior_sd" in " ".join(panels[1]["annotations"])
    assert "boundary" not in panels[0]["rows"][1]["group"]
    assert any(m["type"] == "line" and m["y1"] == pytest.approx(1.6) and m["y2"] == pytest.approx(2.4)
               for m in panels[0]["marks"])


def test_volcano_keeps_zero_and_missing_probabilities_without_floor():
    d = _packet("bulk_volcano")
    for mode, y in [("p", 3.), ("q", 2.)]:
        d["probability_mode"] = mode
        panels = _adapter().build_panels("bulk_volcano", d)
        assert len(panels) == 2
        assert panels[0]["rows"][0]["y"] == pytest.approx(y)
        assert panels[0]["rows"][1]["y"] is None
        assert "zero" in panels[0]["rows"][1]["reason"]
        assert len(panels[0]["rows"]) == 3


def test_dispersion_has_saved_trend_and_both_estimators_with_zero_missing():
    p = _adapter().build_panels("bulk_dispersion", _packet("bulk_dispersion"))[0]
    assert {r["group"] for r in p["rows"]} >= {"gene_wise", "final", "trend"}
    assert any(m["type"] == "polyline" and m["points"] == [[0., math.log10(.4)], [1., -1.]] for m in p["marks"])
    assert len([r for r in p["rows"] if r["id"] == "G2" and r["x"] is None]) == 2


def test_pca_has_34_scree_actual_loadings_and_explicit_pair_direction():
    panels = _adapter().build_panels("bulk_pca", _packet("bulk_pca"))
    assert len(panels) == 4
    p12, p34, scree, biplot = panels
    assert "60.0%" in p12["axes"]["x"]["label"]
    assert p34["rows"][0]["x"] == -.5
    assert [r["y"] for r in scree["rows"]] == pytest.approx([60., 25., 10., 5.])
    arrows = [m for m in p12["marks"] if m["type"] == "arrow"]
    assert len(arrows) == 2
    assert arrows[0]["x1"] == -2. and arrows[0]["x2"] == 0.
    assert any(m["type"] == "arrow" and m["x2"] == 1.6 and m["y2"] == .4 for m in biplot["marks"])


def test_classical_mds_reconstructs_declared_euclidean_distance_and_labels_eigenvalues():
    d = _packet("bulk_mds")
    panels = _adapter().build_panels("bulk_mds", d)
    rows = panels[0]["rows"]
    for i, a in enumerate(rows):
        for j, b in enumerate(rows):
            assert math.hypot(a["x"] - b["x"], a["y"] - b["y"]) == pytest.approx(d["distance_matrix"][i][j])
    assert "positive eigenvalue" in panels[0]["axes"]["x"]["label"]
    assert "PCA" not in panels[0]["axes"]["x"]["label"]
    assert panels == _adapter().build_panels("bulk_mds", d)


@pytest.mark.parametrize("name", ["bulk_sample_distance_heatmap", "bulk_sample_correlation_heatmap"])
def test_sample_heatmaps_have_full_matrix_dendrogram_and_covariate_tracks(name):
    panels = _adapter().build_panels(name, _packet(name))
    heat = panels[0]
    assert len([m for m in heat["marks"] if m["type"] == "rect"]) >= 16
    assert any(m["type"] in {"line", "polyline"} for p in panels for m in p["marks"])
    assert any("condition" in p["title"].lower() or "covariate" in p["title"].lower() for p in panels)
    if "correlation" in name:
        assert heat["color_scale"]["domain"] == [-1., 1.]


def test_histogram_counts_endpoints_and_saved_filtered_status_without_calibration_claim():
    panels = _adapter().build_panels("bulk_pvalue_histogram", _packet("bulk_pvalue_histogram"))
    assert len(panels) == 3
    all_p = panels[0]
    assert len(all_p["rows"]) == 4
    assert sum(m["height"] for m in all_p["marks"] if m["type"] == "rect") == 3
    assert all_p["reference_lines"][0]["value"] == pytest.approx(3 / 20)
    assert any("p=0: 1" in x and "p=1: 1" in x for x in all_p["annotations"])
    assert "calibration" in " ".join(all_p["limitations"]).lower()


def test_filter_curve_uses_saved_counts_and_selected_quantile_not_a_refit():
    p = _adapter().build_panels("bulk_independent_filtering", _packet("bulk_independent_filtering"))[0]
    assert [r["y"] for r in p["rows"]] == [10., 15., None]
    assert any(ref["axis"] == "x" and ref["value"] == .2 for ref in p["reference_lines"])
    assert any("0.05" in x for x in p["annotations"])


def test_paired_facets_have_actual_all_pair_shares_and_no_influence_claim():
    panels = _adapter().build_panels("bulk_paired_expression", _packet("bulk_paired_expression"))
    assert len(panels) == 4
    expr, shares = panels[:2]
    assert expr["rows"][0]["y"] == pytest.approx(math.log2(1.5))
    assert len([m for m in expr["marks"] if m["type"] == "line"]) == 2
    d1, d2 = math.log2(3.5 / 1.5), math.log2(4.5 / 2.5)
    assert [r["y"] for r in shares["rows"]] == pytest.approx([d1 / (d1 + d2), d2 / (d1 + d2)])
    assert all(r["y"] is None for r in panels[3]["rows"])
    assert "influence" in " ".join(shares["limitations"]).lower()


def test_library_qc_computes_exact_detected_counts_and_uses_saved_factors():
    panels = _adapter().build_panels("bulk_library_qc", _packet("bulk_library_qc"))
    assert len(panels) == 4
    assert [r["y"] for r in panels[0]["rows"]] == [11, 12, 6, 34]
    assert [r["y"] for r in panels[1]["rows"]] == [2, 2, 2, 3]
    assert [r["y"] for r in panels[2]["rows"]] == [1, 1, 0, 2]
    assert panels[3]["rows"][0]["x"] == 11 and panels[3]["rows"][0]["y"] == .8


def test_cooks_keeps_zero_unplotted_and_has_tukey_boxes_and_density():
    panels = _adapter().build_panels("bulk_cooks_distance", _packet("bulk_cooks_distance"))
    assert len(panels) == 2
    assert len(panels[0]["rows"]) == 16
    assert panels[0]["rows"][0]["y"] is None
    assert any(m["type"] == "rect" for m in panels[0]["marks"])
    assert any(m["type"] == "polygon" for m in panels[1]["marks"])


def test_saved_influence_uses_actual_refit_difference_and_preserves_failure():
    p = _adapter().build_panels("bulk_leave_one_pair_out", _packet("bulk_leave_one_pair_out"))[0]
    assert [r["y"] for r in p["rows"]] == [-.5, None]
    assert p["rows"][1]["details"]["fit_id"] == "minus-D2"


def test_concordance_binds_gene_ids_and_reports_bland_altman_in_original_test_identities():
    panels = _adapter().build_panels("bulk_concordance", _packet("bulk_concordance"))
    assert len(panels) == 4
    effect, effect_ba, prob, prob_ba = panels
    assert (effect["rows"][0]["x"], effect["rows"][0]["y"]) == (2., 1.)
    assert (effect_ba["rows"][0]["x"], effect_ba["rows"][0]["y"]) == (1.5, -1.)
    assert (prob["rows"][0]["x"], prob["rows"][0]["y"]) == (2., 1.)
    assert prob["rows"][1]["x"] is None and prob_ba["rows"][1]["y"] is None
    assert "Wald" in effect["axes"]["x"]["label"] and "LRT" in effect["axes"]["y"]["label"]


def test_upset_intersections_count_exact_membership_and_preserve_dispositions():
    panels = _adapter().build_panels("bulk_upset_dispositions", _packet("bulk_upset_dispositions"))
    assert len(panels) == 3
    intersections, membership, dispositions = panels
    assert sum(m["height"] for m in intersections["marks"] if m["type"] == "rect") == 3
    assert len(intersections["rows"]) == 3
    assert any(m["type"] == "line" for m in membership["marks"])
    assert sum(m["height"] for m in dispositions["marks"] if m["type"] == "rect") == 6
    assert {r["group"] for r in dispositions["rows"]} >= {"selected", "boundary_warning", "filtered_low_count"}


@pytest.mark.parametrize("name,mutate", [
    ("bulk_ma_comparison", lambda d: d["estimators"]["shrunken"].update(uncertainty_unit="count")),
    ("bulk_volcano", lambda d: d["genes"][0].update(q_value=.0001)),
    ("bulk_dispersion", lambda d: d["genes"][0].update(final=-.1)),
    ("bulk_pca", lambda d: d["pairing"]["pairs"][0].update(to_sample="S4")),
    ("bulk_mds", lambda d: d["distance_matrix"][0].__setitem__(1, -2.)),
    ("bulk_sample_distance_heatmap", lambda d: d["matrix"][0].__setitem__(1, 4.)),
    ("bulk_sample_correlation_heatmap", lambda d: d["matrix"][0].__setitem__(0, .9)),
    ("bulk_pvalue_histogram", lambda d: d["genes"][0].update(p_value=1.1)),
    ("bulk_independent_filtering", lambda d: d.update(selected_quantile=.3)),
    ("bulk_paired_expression", lambda d: d.update(contribution_definition="causal_influence")),
    ("bulk_library_qc", lambda d: d["counts"][0].__setitem__(0, .5)),
    ("bulk_cooks_distance", lambda d: d["cooks_distances"][0].__setitem__(0, -.1)),
    ("bulk_leave_one_pair_out", lambda d: d["genes"][0]["omissions"][0].update(pair_id="undeclared")),
    ("bulk_concordance", lambda d: d["right"]["binding"].update(contrast_id="other-contrast")),
    ("bulk_upset_dispositions", lambda d: d["methods"][0]["genes"][0].update(disposition="calibrated")),
    ("bulk_residual_diagnostics", lambda d: d.update(residual_unit="raw_count")),
    ("bulk_design_diagnostics", lambda d: d["diagnostics"].update(rank=3)),
    ("bulk_mean_variance", lambda d: d.update(variance_unit="dispersion_alpha")),
])
def test_each_capability_refuses_scientifically_incompatible_evidence(name, mutate):
    d = _packet(name)
    mutate(d)
    with pytest.raises(ValueError):
        _adapter().build_panels(name, d)


@pytest.mark.parametrize("packet", fixture_packets(), ids=lambda p: p["capability"])
def test_population_units_are_explicit_and_unknown_fields_are_rejected(packet):
    d = deepcopy(packet["data"])
    d.pop("population_unit")
    with pytest.raises(ValueError):
        _adapter().build_panels(packet["capability"], d)
    d = deepcopy(packet["data"])
    d["automatic_inference"] = True
    with pytest.raises(ValueError):
        _adapter().build_panels(packet["capability"], d)


def test_heatmap_clustering_requires_the_explicit_scientific_distance_declaration():
    d = _packet("bulk_sample_correlation_heatmap")
    d["cluster"] = True
    with pytest.raises(ValueError):
        _adapter().build_panels("bulk_sample_correlation_heatmap", d)
    d["cluster"] = {"metric": "precomputed", "method": "average"}
    with pytest.raises(ValueError):
        _adapter().build_panels("bulk_sample_correlation_heatmap", d)


def test_matrix_identity_and_missing_covariates_preserve_sample_membership():
    d = _packet("bulk_sample_distance_heatmap")
    d["covariate_tracks"]["condition"]["S2"] = None
    panels = _adapter().build_panels("bulk_sample_distance_heatmap", d)
    assert all(r["link"]["kind"] == "sample" for r in panels[0]["rows"])
    track = next(p for p in panels if p["title"] == "Covariate: condition")
    missing = next(r for r in track["rows"] if r["id"] == "S2")
    assert missing["y"] is None and missing["reason"]


def test_saved_mds_does_not_recompute_scores_or_claim_pca_variance():
    original = _packet("bulk_mds")
    d = {k: original[k] for k in ["population_unit", "biological_unit", "samples"]}
    d.update(mode="saved_scores", score_unit="saved_nonmetric_score", method="saved nonmetric MDS",
             scores=[{"sample_id": s["sample_id"], "scores": [float(i), None if i == 3 else i/2]} for i, s in enumerate(d["samples"])])
    panels = _adapter().build_panels("bulk_mds", d)
    assert len(panels) == 1
    assert [(r["x"], r["y"]) for r in panels[0]["rows"]] == [(0., 0.), (1., .5), (2., 1.), (3., None)]
    assert "%" not in panels[0]["axes"]["x"]["label"]
    d["eigenvalues"] = [4., 1., -.5]
    d["eigenvalue_definition"] = "PCA_variance"
    with pytest.raises(ValueError):
        _adapter().build_panels("bulk_mds", d)


def test_classical_mds_retains_negative_eigenvalues_as_geometry_limitations():
    d = _packet("bulk_mds")
    d["distance_matrix"] = [[0., 1., 1., 4.], [1., 0., 1., 1.], [1., 1., 0., 1.], [4., 1., 1., 0.]]
    panels = _adapter().build_panels("bulk_mds", d)
    assert any(r["y"] < 0 for r in panels[1]["rows"])
    assert sum(r["details"]["positive_eigenvalue_fraction"] for r in panels[1]["rows"]) == pytest.approx(1.)


def test_dispersion_missing_saved_trend_does_not_create_a_bridging_curve():
    d = _packet("bulk_dispersion")
    d["trend"] = [{"mean": 1., "dispersion": .4}, {"mean": 5., "dispersion": None, "reason": "saved_trend_failed"},
                  {"mean": 10., "dispersion": .1}]
    p = _adapter().build_panels("bulk_dispersion", d)[0]
    assert not any(m["type"] == "polyline" for m in p["marks"])
    assert len([r for r in p["rows"] if r["group"] == "trend"]) == 3


def test_calibrated_status_requires_method_cohort_contrast_and_gene_bound_evidence():
    d = _packet("bulk_upset_dispositions")
    d["methods"][0]["genes"][0]["disposition"] = "calibrated_discovery"
    d["methods"][0]["calibration"] = {"evidence_id": "saved-calibration", "method_id": "A",
        "cohort_id": d["binding"]["cohort_id"], "contrast_id": d["binding"]["contrast_id"],
        "gene_ids": ["G1"], "criterion": "synthetic saved independent calibration check", "result": "passed"}
    panels = _adapter().build_panels("bulk_upset_dispositions", d)
    assert any(r["group"] == "calibrated_discovery" for r in panels[2]["rows"])
    d["methods"][0]["calibration"]["method_id"] = "different-method"
    with pytest.raises(ValueError):
        _adapter().build_panels("bulk_upset_dispositions", d)


def test_saved_boundary_reason_is_metadata_when_coordinates_exist():
    d = _packet("bulk_ma_comparison")
    d["genes"][0]["reason"] = "saved_boundary_diagnostic"
    d["genes"][0]["boundary_warning"] = True
    p = _adapter().build_panels("bulk_ma_comparison", d)[0]
    assert p["rows"][0]["status"] == "recorded"
    assert p["rows"][0]["details"]["reason"] == "saved_boundary_diagnostic"


def test_source_gene_names_need_not_be_panel_safe_and_do_not_collide():
    d = _packet("bulk_paired_expression")
    d["gene_ids"] = ["Gene/A", "Gene A"]
    panels = _adapter().build_panels("bulk_paired_expression", d)
    assert len(set(p["id"] for p in panels)) == 4
    assert {r["details"]["gene_id"] for p in panels for r in p["rows"]} == {"Gene/A", "Gene A"}


def test_constant_cooks_has_no_fabricated_density_and_missing_count_columns_are_not_summed():
    d = _packet("bulk_cooks_distance")
    d["cooks_distances"] = [[.1] * 4 for _ in range(4)]
    panels = _adapter().build_panels("bulk_cooks_distance", d)
    assert not any(m["type"] == "polygon" for m in panels[1]["marks"])
    q = _packet("bulk_library_qc")
    q["counts"][0][0] = None
    panels = _adapter().build_panels("bulk_library_qc", q)
    assert all(p["rows"][0]["y"] is None for p in panels[:3])
    assert panels[3]["rows"][0]["x"] is None


@pytest.mark.parametrize("mutation", [
    lambda d: d["right"]["genes"].pop(),
    lambda d: d["right"]["binding"].update(effect_unit="natural_log_fold_change"),
    lambda d: d["right"].update(test_id="Wald"),
    lambda d: d["left"]["genes"][0].update(p_value=float("nan")),
])
def test_concordance_refuses_mismatched_populations_scales_tests_and_nonfinite_p(mutation):
    d = _packet("bulk_concordance")
    mutation(d)
    with pytest.raises(ValueError):
        _adapter().build_panels("bulk_concordance", d)


def test_donor_shapes_and_labels_are_bound_to_explicit_biological_identity():
    p = _adapter().build_panels("bulk_pca", _packet("bulk_pca"))[0]
    points = {m["keys"][0]: m for m in p["marks"] if m["type"] == "point"}
    assert points["S1"]["shape"] == points["S2"]["shape"]
    assert points["S1"]["shape"] != points["S3"]["shape"]
    assert {m["text"] for m in p["marks"] if m["type"] == "text"} >= {"D1 / S1", "D2 / S4"}


def test_requested_volcano_labels_keep_gene_identity_and_do_not_label_zero_p_at_an_invented_height():
    d = _packet("bulk_volcano")
    d["label_gene_ids"] = ["G1", "G2"]
    panels = _adapter().build_panels("bulk_volcano", d)
    for p in panels:
        texts = [m for m in p["marks"] if m["type"] == "text"]
        assert len(texts) == 1 and texts[0]["text"] == "G1"
        assert texts[0]["keys"] == ["G1"]
    d["label_gene_ids"] = ["not-in-source"]
    with pytest.raises(ValueError):
        _adapter().build_panels("bulk_volcano", d)


def test_uncalibrated_disposition_is_not_relabelled_or_required_to_have_passed_calibration():
    d = _packet("bulk_upset_dispositions")
    d["methods"][0]["genes"][0]["disposition"] = "uncalibrated"
    panels = _adapter().build_panels("bulk_upset_dispositions", d)
    assert any(r["group"] == "uncalibrated" for r in panels[2]["rows"])


def test_accepted_calibration_evidence_remains_bound_in_exported_rows():
    d = _packet("bulk_upset_dispositions")
    d["methods"][0]["genes"][0]["disposition"] = "calibrated_discovery"
    evidence = {"evidence_id": "saved-calibration", "method_id": "A", "cohort_id": d["binding"]["cohort_id"],
        "contrast_id": d["binding"]["contrast_id"], "gene_ids": ["G1"], "criterion": "synthetic check", "result": "passed"}
    d["methods"][0]["calibration"] = evidence
    panels = _adapter().build_panels("bulk_upset_dispositions", d)
    r = next(r for r in panels[2]["rows"] if r["id"] == "G1" and r["details"]["method_id"] == "A")
    assert r["details"]["calibration_evidence"]["evidence_id"] == "saved-calibration"
    assert r["details"]["calibration_evidence"]["gene_id"] == "G1"


def test_pairing_rejects_unknown_biological_identity_encoded_as_a_string():
    d = _packet("bulk_paired_expression")
    d["samples"][0]["biological_unit_id"] = "unknown"
    d["samples"][1]["biological_unit_id"] = "unknown"
    d["pairing"]["pairs"][0]["biological_unit_id"] = "unknown"
    with pytest.raises(ValueError):
        _adapter().build_panels("bulk_paired_expression", d)
    d = _packet("bulk_leave_one_pair_out")
    d["pairs"][0]["biological_unit_id"] = "NA"
    with pytest.raises(ValueError):
        _adapter().build_panels("bulk_leave_one_pair_out", d)


def test_missing_pca_score_keeps_sample_and_suppresses_only_incomplete_pair_arrow():
    d = _packet("bulk_pca")
    d["scores"][1]["scores"][1] = None
    d["scores"][1]["reason"] = "saved_score_missing"
    p = _adapter().build_panels("bulk_pca", d)[0]
    assert len(p["rows"]) == 4
    assert next(r for r in p["rows"] if r["id"] == "S2")["y"] is None
    assert len([m for m in p["marks"] if m["type"] == "arrow"]) == 1


@pytest.mark.parametrize("packet", fixture_packets(), ids=lambda p: p["capability"])
def test_saved_inputs_remain_read_only(packet):
    original = deepcopy(packet["data"])
    _adapter().build_panels(packet["capability"], packet["data"])
    assert packet["data"] == original


def test_saved_residual_geometry_retains_model_units_biological_identity_and_failed_rows():
    panels = _adapter().build_panels("bulk_residual_diagnostics", _packet("bulk_residual_diagnostics"))
    assert len(panels) == 3
    scatter, boxes, violins = panels
    assert [(r["x"], r["y"]) for r in scatter["rows"]][:2] == [(2., -.5), (4., .2)]
    assert scatter["rows"][-1]["reason"] == "saved_fit_failed"
    assert scatter["rows"][0]["details"]["biological_unit_id"] == "D1"
    assert all(p["axes"]["y"]["unit"] == "dimensionless" for p in panels)
    assert all(any(ref["axis"] == "y" and ref["value"] == 0 for ref in p["reference_lines"]) for p in panels)
    assert any(m["type"] == "rect" for m in boxes["marks"])
    assert any(m["type"] == "polygon" for m in violins["marks"])
    assert all(len(p["rows"]) == 8 for p in panels)
    assert any("independent" in note for note in boxes["limitations"])


def test_residuals_cannot_be_generated_from_effect_rows_or_unknown_sample_ids():
    d = _packet("bulk_residual_diagnostics")
    d["records"][0].pop("residual")
    d["records"][0]["effect"] = -.5
    with pytest.raises(ValueError):
        _adapter().build_panels("bulk_residual_diagnostics", d)
    d = _packet("bulk_residual_diagnostics")
    d["records"][0]["sample_id"] = "unknown-sample"
    with pytest.raises(ValueError):
        _adapter().build_panels("bulk_residual_diagnostics", d)


def test_saved_design_geometry_keeps_actual_matrix_rank_singular_values_and_unestimable_contrast():
    d = _packet("bulk_design_diagnostics")
    panels = _adapter().build_panels("bulk_design_diagnostics", d)
    assert len(panels) == 5
    heat, rank, singular, condition, contrasts = panels
    assert len(heat["rows"]) == 12
    assert sum(m["value"] for m in heat["marks"] if m["type"] == "rect") == 8.
    assert next(r for r in rank["rows"] if r["id"] == "rank")["y"] == 2
    assert [r["y"] for r in singular["rows"]] == pytest.approx(d["diagnostics"]["singular_values"])
    assert condition["rows"][0]["y"] is None
    assert "rank_deficient_design" in heat["rows"][0]["warnings"]
    assert any("unestimable" in warning for r in contrasts["rows"] for warning in r["warnings"])
    assert any("causal" in note for p in panels for note in p["limitations"])


def test_explicit_descriptive_design_svd_matches_actual_matrix_without_model_fitting():
    d = _packet("bulk_design_diagnostics")
    d["diagnostics"] = {"mode": "descriptive_svd", "rank_tolerance": 1e-10, "scaling": "as_supplied_no_rescaling"}
    panels = _adapter().build_panels("bulk_design_diagnostics", d)
    assert next(r for r in panels[1]["rows"] if r["id"] == "rank")["y"] == 2
    assert [r["y"] for r in panels[2]["rows"]] == pytest.approx([math.sqrt(4+math.sqrt(8)), math.sqrt(4-math.sqrt(8)), 0.], abs=1e-12)
    assert panels[3]["rows"][0]["y"] is None
    assert any("explicit" in note.lower() and "SVD" in note for note in panels[2]["annotations"])


def test_incomplete_design_keeps_cells_and_missing_diagnostics_without_imputation():
    d = _packet("bulk_design_diagnostics")
    d["matrix"][0][1] = None
    d["diagnostics"] = {"mode": "descriptive_svd", "rank_tolerance": 1e-10, "scaling": "as_supplied_no_rescaling"}
    panels = _adapter().build_panels("bulk_design_diagnostics", d)
    assert len(panels[0]["rows"]) == 12
    assert any(r["reason"] for r in panels[0]["rows"])
    assert next(r for r in panels[1]["rows"] if r["id"] == "rank")["y"] is None
    assert all(r["y"] is None and "missing" in r["reason"] for r in panels[2]["rows"])


def test_design_diagnostics_require_actual_matrix_and_exact_column_bound_contrasts():
    d = _packet("bulk_design_diagnostics")
    d.pop("matrix")
    with pytest.raises(ValueError):
        _adapter().build_panels("bulk_design_diagnostics", d)
    d = _packet("bulk_design_diagnostics")
    d["contrasts"][0]["weights"] = [0., 1.]
    with pytest.raises(ValueError):
        _adapter().build_panels("bulk_design_diagnostics", d)


def test_full_rank_descriptive_design_has_actual_condition_number_without_causal_claim():
    d = _packet("bulk_design_diagnostics")
    d["columns"] = d["columns"][:2]
    d["matrix"] = [r[:2] for r in d["matrix"]]
    d.pop("contrasts")
    d["diagnostics"] = {"mode": "descriptive_svd", "rank_tolerance": 1e-10, "scaling": "as_supplied_no_rescaling"}
    panels = _adapter().build_panels("bulk_design_diagnostics", d)
    assert len(panels) == 4
    assert panels[3]["rows"][0]["y"] == pytest.approx(math.sqrt((3+math.sqrt(5))/(3-math.sqrt(5))))
    assert panels[1]["rows"][0]["y"] == 2
    assert not panels[0]["rows"][0]["warnings"]
    assert any("does not establish causal" in line for line in panels[3]["limitations"])


def test_saved_design_failure_is_retained_without_fabricated_singular_values():
    d = _packet("bulk_design_diagnostics")
    d["diagnostics"].update(status="failed", rank=None, condition_number=None,
                            singular_values=[None, None, None], reason="saved_decomposition_failed")
    panels = _adapter().build_panels("bulk_design_diagnostics", d)
    assert len(panels[2]["rows"]) == 3
    assert not panels[2]["marks"]
    assert all(r["reason"] == "saved_decomposition_failed" for r in panels[2]["rows"])
    assert panels[0]["rows"][0]["warnings"] == ["design_diagnostics_unavailable"]


@pytest.mark.parametrize("change", [
    lambda d: d["diagnostics"].update(condition_number=2.),
    lambda d: d["diagnostics"].update(singular_values=[2., 3., 0.]),
    lambda d: d["diagnostics"].update(status="full_rank"),
    lambda d: d["columns"][1].update(column_id="intercept"),
    lambda d: d["matrix"][0].__setitem__(0, float("nan")),
    lambda d: d["contrasts"][0].update(status="causally_identified"),
])
def test_design_refuses_incompatible_diagnostics_and_unsupported_contrast_claims(change):
    d = _packet("bulk_design_diagnostics")
    change(d)
    with pytest.raises(ValueError):
        _adapter().build_panels("bulk_design_diagnostics", d)


def test_response_residual_units_match_fitted_units_and_constant_groups_have_no_fake_density():
    d = _packet("bulk_residual_diagnostics")
    d["residual_type"] = "response"
    with pytest.raises(ValueError):
        _adapter().build_panels("bulk_residual_diagnostics", d)
    d["residual_unit"] = d["fitted_unit"]
    for record in d["records"]:
        record["residual"] = 0.
    panels = _adapter().build_panels("bulk_residual_diagnostics", d)
    assert not any(mark["type"] == "polygon" for mark in panels[2]["marks"])
    assert all(row["y"] == 0. for row in panels[1]["rows"])


def test_partial_pca_preserves_pc12_and_scree_without_requiring_missing_companions():
    d = _packet("bulk_pca")
    d["explained_variance_ratio"] = d["explained_variance_ratio"][:2]
    for scores in d["scores"]:
        scores["scores"] = scores["scores"][:2]
    for loading in d["loadings"]:
        loading["loadings"] = loading["loadings"][:2]
    panels = _adapter().build_panels("bulk_pca", d)
    assert len(panels) == 3
    assert "PC1/PC2" in panels[0]["title"]
    assert len(panels[1]["rows"]) == 2
    assert any("PC3/PC4 unavailable" in note for p in panels for note in p["annotations"])
    d["loadings"] = []
    for key in ["loading_scale", "loading_unit", "loading_definition"]:
        d.pop(key)
    panels = _adapter().build_panels("bulk_pca", d)
    assert len(panels) == 2
    assert any("Loading biplot unavailable" in note for p in panels for note in p["annotations"])
    d.pop("loadings")
    assert len(_adapter().build_panels("bulk_pca", d)) == 2


def test_finite_nonconverged_dispersion_retains_saved_coordinates_reason_and_warning_glyph():
    d = _packet("bulk_dispersion")
    d["genes"][0].update(reason="Saved dispersion unavailable or nonconverged.", convergence_status="nonconverged")
    p = _adapter().build_panels("bulk_dispersion", d)[0]
    rows = [r for r in p["rows"] if r["id"] == "G1"]
    assert all(r["status"] == "excluded" and r["reason"] == "Saved dispersion unavailable or nonconverged." for r in rows)
    assert all(r["warnings"] and r["x"] == 1. for r in rows)
    assert {r["y"] for r in rows} == {math.log10(.2), math.log10(.15)}
    keys = {r["key"] for r in rows}
    assert all(m["shape"] == "triangle" for m in p["marks"] if m["type"] == "point" and m["keys"][0] in keys)
    d["genes"][0].pop("convergence_status")
    legacy = _adapter().build_panels("bulk_dispersion", d)[0]
    assert all(r["warnings"] for r in legacy["rows"] if r["id"] == "G1")


def test_informational_saved_reason_is_retained_without_becoming_a_failed_fit():
    d = _packet("bulk_dispersion")
    d["genes"][0].update(reason="Saved at the requested reporting precision.", convergence_status="converged")
    p = _adapter().build_panels("bulk_dispersion", d)[0]
    rows = [r for r in p["rows"] if r["id"] == "G1"]
    assert all(r["status"] == "recorded" for r in rows)
    assert all(r["details"]["source_reason"] == "Saved at the requested reporting precision." for r in rows)


def test_empirical_mean_variance_uses_actual_saved_quantities_and_keeps_missing_or_zero_geometry():
    d = _packet("bulk_mean_variance")
    p = _adapter().build_panels("bulk_mean_variance", d)[0]
    assert (p["rows"][0]["x"], p["rows"][0]["y"]) == (1., 2.)
    assert p["rows"][1]["x"] is None and p["rows"][1]["y"] is None
    assert p["rows"][2]["y"] is None and p["rows"][2]["reason"] == "saved_variance_missing"
    assert any("dispersion" in note for note in p["limitations"])
    d["display_transform"] = "identity"
    p = _adapter().build_panels("bulk_mean_variance", d)[0]
    assert (p["rows"][0]["x"], p["rows"][0]["y"]) == (10., 100.)
    assert (p["rows"][1]["x"], p["rows"][1]["y"]) == (0., 0.)
    d["genes"][0]["sample_ids"] = ["not-a-declared-sample"]
    with pytest.raises(ValueError):
        _adapter().build_panels("bulk_mean_variance", d)


def test_boundary_associated_histogram_spikes_use_saved_provenance_and_actual_counts():
    d = _packet("bulk_pvalue_histogram")
    p = _adapter().build_panels("bulk_pvalue_histogram", d)[0]
    flagged = [m for m in p["marks"] if m["group"] == "saved_boundary_warning"]
    assert sum(m["height"] for m in flagged) == 2.
    assert sum(m["height"] for m in p["marks"] if m["type"] == "rect") == 3.
    assert any("boundary-associated p=0: 1" in note and "p=1: 1" in note for note in p["annotations"])
    assert any("boundary-associated p>=0.95: 1" in note for note in p["annotations"])
    assert p["rows"][0]["details"]["boundary_evidence"]["evidence_id"] == "synthetic-saved-boundary-diagnostics"
    d.pop("boundary_evidence")
    with pytest.raises(ValueError):
        _adapter().build_panels("bulk_pvalue_histogram", d)


def test_histogram_missing_boundary_assessment_is_unknown_and_not_inferred_from_endpoints():
    d = _packet("bulk_pvalue_histogram")
    d.pop("boundary_evidence")
    for gene in d["genes"]:
        gene.pop("boundary_warning", None)
    p = _adapter().build_panels("bulk_pvalue_histogram", d)[0]
    assert not any(m["group"] == "saved_boundary_warning" for m in p["marks"])
    assert any("boundary assessment unavailable" in note.lower() for note in p["annotations"])
    assert all(r["details"]["boundary_warning"] is None for r in p["rows"])


@pytest.mark.parametrize("name", ["bulk_sample_distance_heatmap", "bulk_sample_correlation_heatmap"])
def test_missing_sample_matrix_cells_are_retained_without_imputation_or_fake_clustering(name):
    d = _packet(name)
    d["matrix"][0][1] = d["matrix"][1][0] = None
    panels = _adapter().build_panels(name, d)
    assert len(panels[0]["rows"]) == 16
    assert sum(r["reason"] is not None for r in panels[0]["rows"]) == 2
    cells = [m for m in panels[0]["marks"] if m["type"] == "rect"]
    assert len([m for m in cells if m["value"] is not None]) == 14
    assert len([m for m in cells if m["value"] is None and m["style"]["fill"] == "#edf1f4"]) == 2
    assert not any(p["title"] == "Sample dendrogram" for p in panels)
    assert any("Clustering unavailable" in note and "missing" in note for note in panels[0]["annotations"])
    assert panels[0]["x_categories"] == d["sample_ids"]


def test_requested_pca_loadings_without_required_units_do_not_hide_sample_pca():
    d = _packet("bulk_pca")
    d.pop("loading_unit")
    panels = _adapter().build_panels("bulk_pca", d)
    assert len(panels) == 3
    assert any("Loading biplot unavailable" in note and "loading_unit" in note for p in panels for note in p["annotations"])
