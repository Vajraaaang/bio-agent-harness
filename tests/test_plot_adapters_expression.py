"""Synthetic geometry checks; these fixtures do not establish scientific validity."""

from __future__ import annotations

from copy import deepcopy
import importlib
import math

import pytest


MODULE = "devbench.analysis.plot_adapters_expression"


def _adapter():
    return importlib.import_module(MODULE)


def fixture_packets():
    """Return explicitly labeled, small synthetic packets for export/browser QA."""
    qc = {
        "cells": [
            {"id": f"synthetic-cell-{i}", "sample": "S1" if i < 5 else "S2",
             "batch": "B1" if i < 5 else "B2", "total_counts": v,
             "n_genes_by_counts": v // 2, "pct_counts_mt": float(i + 1),
             "qc_pass": i not in (0, 7)}
            for i, v in enumerate([10, 20, 25, 30, 200, 15, 35, 40])
        ],
        "group_by": "sample",
        "units": {"total_counts": "count", "n_genes_by_counts": "gene", "pct_counts_mt": "percent"},
        "thresholds": {"min_counts": 15, "min_genes": 5, "max_pct_mt": 7},
        "threshold_source": "Synthetic saved filtering configuration",
        "bandwidth": 2.0,
    }
    observations = [
        {"id": f"synthetic-observation-{i}", "value": v,
         "batch": "B1" if i < 5 else "B2"}
        for i, v in enumerate([1, 2, 3, 4, 100, 2, 4, 8])
    ]
    depth = {"observations": observations, "unit": "count", "group_by": "batch",
             "entity_kind": "cell", "bins": [0, 10, 50, 101],
             "cutoffs": [{"value": 3, "label": "Synthetic saved minimum depth"}]}
    expression = {
        "observations": [
            {"id": f"synthetic-expression-{i}", "cell_id": f"synthetic-cell-{i}",
             "gene_id": "SyntheticGeneA", "group": "A" if i < 5 else "B", "value": v}
            for i, v in enumerate([0, 1, 2, 3, 30, 1, 4, 8])
        ],
        "group_by": "group", "source_scale": "counts", "unit": "count",
        "layer": "synthetic-counts", "bandwidth": 1.0,
    }
    doublets = {
        "cells": [{"id": f"synthetic-cell-{i}", "score": v, "predicted_doublet": v >= .5,
                   "x": i, "y": (i % 3) - 1} for i, v in enumerate([.1, .2, .3, .6, .8])],
        "bins": [0, .25, .5, .75, 1], "threshold": .5,
        "threshold_source": "Synthetic saved call threshold", "method": "Synthetic doublet score",
        "embedding_name": "Synthetic UMAP", "score_unit": "score", "bandwidth": .1,
    }
    hvg = {
        "genes": [{"id": "SyntheticGeneA", "mean": 2.0, "statistic": 3.0, "highly_variable": True},
                  {"id": "SyntheticGeneB", "mean": 4.0, "statistic": 1.5, "highly_variable": False}],
        "mean_unit": "count", "statistic": "standardized_variance", "statistic_unit": "dimensionless",
        "source_scale": "counts", "layer": "synthetic-counts", "method": "Synthetic saved HVG method",
    }
    integration = {
        "cells": [{"id": f"synthetic-cell-{i}", "batch": f"B{i % 2}", "cell_type": f"T{i % 2}",
                   "before": [float(i), float(i % 3)], "after": [float(i) / 2, float(i % 3)]}
                  for i in range(6)],
        "embedding_name": "Synthetic UMAP", "before_method": "Synthetic unintegrated",
        "after_method": "Synthetic saved integration",
        "metrics": [{"id": f"synthetic-metric-{i}", "cell_type": f"T{i}", "batch_asw": -.1 + i * .1,
                     "cell_type_asw": .5 + i * .1, "batch_lisi": 1.2 + i, "cell_type_lisi": 1.1 + i / 10}
                    for i in range(2)],
        "metric_definitions": {"batch_asw": "Saved raw batch silhouette [-1, 1]",
                               "cell_type_asw": "Saved raw cell-type silhouette [-1, 1]",
                               "batch_lisi": "Saved batch inverse Simpson index",
                               "cell_type_lisi": "Saved cell-type inverse Simpson index"},
    }
    feature = {
        "cells": [{"id": f"synthetic-cell-{i}", "x": i, "y": i % 3, "value": v}
                  for i, v in enumerate([0, 1, 2, 4, 8])],
        "feature_id": "SyntheticGeneA", "layer": "synthetic-counts", "source_scale": "counts",
        "unit": "count", "embedding_name": "Synthetic UMAP",
    }
    marker = {
        "group_gene_aggregates": [
            {"id": "synthetic-dot-A", "group": "A", "gene_id": "SyntheticGeneA", "mean_expression": 1.0,
             "numerator": 2, "denominator": 3, "member_ids": ["c1", "c2", "c3"], "values": [0, 1, 2]},
            {"id": "synthetic-dot-B", "group": "B", "gene_id": "SyntheticGeneA", "mean_expression": 2.0,
             "numerator": 2, "denominator": 2, "member_ids": ["c4", "c5"], "values": [1, 3]},
        ], "unit": "count", "source_scale": "counts", "layer": "synthetic-counts",
        "expression_threshold": 0.0, "denominator_kind": "measured_cells",
    }
    heatmap = {"values": [[0, 1, 3], [3, 2, 0], [1, 4, 2]],
               "row_ids": ["SyntheticGeneA", "SyntheticGeneB", "SyntheticGeneC"],
               "column_ids": ["SyntheticS1", "SyntheticS2", "SyntheticS3"],
               "unit": "count", "source_scale": "counts", "layer": "synthetic-counts", "cluster": True}
    module_heatmap = {**deepcopy(heatmap), "unit": "score", "source_scale": "module_score",
                      "layer": "synthetic-module-scores", "row_ids": ["SyntheticM1", "SyntheticM2", "SyntheticM3"],
                      "score_definition": "Synthetic saved module scores, not gene expression"}
    compositions = []
    for d, condition, numerator in [("d1", "A", 1), ("d2", "A", 2), ("d3", "B", 3), ("d4", "B", 2)]:
        members = [f"{d}-cell-{j}" for j in range(4)]
        compositions.append({"id": f"synthetic-composition-{d}", "donor_id": d, "sample_id": f"s-{d}",
                             "condition": condition, "cell_type": "SyntheticT", "numerator": numerator,
                             "denominator": 4, "member_ids": members, "numerator_ids": members[:numerator]})
    donor = {"compositions": compositions, "denominator_kind": "all_saved_cells",
             "biological_unit": "donor", "unit": "fraction",
             "summaries": [{"id": "synthetic-defined-interval", "type": "interval", "condition": "A", "cell_type": "SyntheticT",
                            "unit_ids": ["d1", "d2"], "estimate": .375, "low": .2, "high": .6,
                            "method": "Synthetic saved interval method; no inference performed by fixture",
                            "unit": "fraction", "interval_kind": "confidence", "level": .9,
                            "scope": "Pointwise interval for the condition A mean donor fraction",
                            "denominator_kind": "all_saved_cells"}]}
    batch_rows = []
    for batch in ("B1", "B2"):
        members = [f"{batch}-{i}" for i in range(4)]
        for celltype, subset in [("T1", members[:1]), ("T2", members[1:])]:
            batch_rows.append({"id": f"synthetic-{batch}-{celltype}", "batch": batch, "cell_type": celltype,
                               "numerator": len(subset), "denominator": 4,
                               "member_ids": members, "numerator_ids": subset})
    batch = {"compositions": batch_rows, "denominator_kind": "all_saved_cells", "unit": "fraction"}
    confidence = {"observations": [{"id": f"synthetic-cell-{i}", "value": v, "group": "Synthetic predicted A"}
                                    for i, v in enumerate([.1, .3, .8, .9])],
                  "bins": [0, .5, 1], "classifier": "Synthetic classifier",
                  "score_definition": "Saved uncalibrated top-class score", "score_unit": "confidence_score"}
    density = {"cells": [{"id": f"synthetic-cell-{i}", "x": i % 4, "y": i // 4,
                          "group": "A" if i < 4 else "B"} for i in range(8)],
               "embedding_name": "Synthetic UMAP", "bins": [2, 2]}
    saved_embedding = {
        "cells": [{"id": f"synthetic-embedding-cell-{i}", "x": float(x), "y": float(y),
                   "group": "Synthetic A" if x <= 0 else "Synthetic B"}
                  for i, (x, y) in enumerate(((-1, -1), (0, -1), (1, -1), (-1, 0), (0, 0),
                                              (1, 0), (-1, 1), (0, 1), (1, 1)))],
        "method": "t-SNE", "representation": "Synthetic saved log1p-expression PCA representation",
        "configuration": {"perplexity": 3, "learning_rate": 100, "random_state": 7},
    }
    hexbin = {**deepcopy(saved_embedding), "method": "UMAP",
              "configuration": {"n_neighbors": 3, "min_dist": .2, "random_state": 7},
              "hex_radius": 1.0, "origin": [0, 0]}
    hexbin["cells"] = [{"id": f"synthetic-hex-cell-{i}", "x": x, "y": y, "group": "Synthetic group"}
                      for i, (x, y) in enumerate(((0, 0), (.2, .1), (math.sqrt(3), 0),
                                                  (math.sqrt(3) + .1, .1), (math.sqrt(3) / 2, 1.5)))]
    contours = {**deepcopy(saved_embedding), "bandwidth": [.5, .5], "grid_size": [65, 65],
                "levels": [.02, .06, .10]}
    ambient = {
        "observations": [{"id": f"synthetic-ambient-cell-{i}", "sample_id": f"S{i // 3}", "batch": f"B{i // 3}",
                          "value": value, "status": "recorded" if value is not None else "failed",
                          "reason": None if value is not None else "Synthetic saved estimator failure",
                          "total_counts": 100 + 50 * i}
                         for i, value in enumerate((.05, .1, .2, .3, .7, None))],
        "entity_kind": "cell", "group_by": "sample_id", "method": "Synthetic saved contamination estimator",
        "measurement": "estimated_contamination_fraction", "unit": "fraction",
        "measurement_definition": "Synthetic saved estimate of ambient RNA fraction per cell",
        "bins": [0, .1, .25, .5, 1], "bandwidth": .06,
        "cutoffs": [{"value": .25, "label": "Synthetic saved operator cutoff", "source": "Synthetic saved configuration"}],
        "embedding": {"method": "UMAP", "representation": "Synthetic saved log1p PCA",
                      "configuration": {"n_neighbors": 3, "min_dist": .2, "random_state": 7},
                      "coordinates": [{"id": f"synthetic-ambient-cell-{i}", "x": i, "y": i % 2} for i in range(6)]},
        "ambient_gene_profile": {"genes": [{"id": "SyntheticAmbientA", "value": .4, "status": "recorded"},
                                             {"id": "SyntheticAmbientB", "value": .6, "status": "recorded"}],
                                 "unit": "fraction", "quantity_definition": "Saved gene fraction in the synthetic ambient profile",
                                 "source_population": "Synthetic explicitly saved empty-droplet profile"},
        "correction": {"values": [{"id": "synthetic-correction-0", "observation_id": "synthetic-ambient-cell-0",
                                    "gene_id": "SyntheticAmbientA", "before": 8, "after": 4.5, "status": "recorded"},
                                   {"id": "synthetic-correction-1", "observation_id": "synthetic-ambient-cell-1",
                                    "gene_id": "SyntheticAmbientA", "before": 15, "after": 10.5, "status": "recorded"},
                                   {"id": "synthetic-correction-2", "observation_id": "synthetic-ambient-cell-2",
                                    "gene_id": "SyntheticAmbientA", "before": 22, "after": None, "status": "failed",
                                    "reason": "Synthetic saved correction failure"}],
                       "before_layer": "synthetic-raw-counts", "after_layer": "synthetic-saved-corrected-count-estimates",
                       "before_scale": "counts", "after_scale": "estimated_counts", "before_unit": "count", "after_unit": "estimated_count",
                       "method": "Synthetic saved correction output; never run by the adapter"},
    }
    datasets = {
        "qc_violins": qc, "qc_ridges": qc, "qc_joint_scatter": {k: v for k, v in qc.items() if k != "bandwidth"},
        "depth_histogram": depth,
        "depth_ecdf": {k: v for k, v in depth.items() if k != "bins"},
        "batch_boxplot": {k: v for k, v in depth.items() if k not in ("bins", "cutoffs")},
        "expression_violins": expression, "expression_boxplots": expression, "expression_ridges": expression,
        "doublet_diagnostics": doublets, "hvg_scatter": hvg,
        "scrna_pca_scree": {"components": [{"pc": 1, "variance_explained": .5}, {"pc": 2, "variance_explained": .3},
                                              {"pc": 3, "variance_explained": .1}],
                            "variance_unit": "fraction", "layer": "synthetic-log1p", "source_scale": "log1p_expression"},
        "integration_diagnostics": integration, "feature_expression": feature, "marker_dotplot": marker,
        "expression_heatmap": heatmap, "module_heatmap": module_heatmap,
        "donor_composition": donor, "batch_composition": batch,
        "classifier_confidence_histogram": confidence, "embedding_density_centroids": density,
        "saved_embedding": saved_embedding, "embedding_hexbin": hexbin, "embedding_density_contours": contours,
        "ambient_rna_diagnostics": ambient,
    }
    return [{"id": f"synthetic-{capability}", "capability": capability, "data": deepcopy(data)}
            for capability, data in datasets.items()]


def _packet(capability):
    return next(packet["data"] for packet in fixture_packets() if packet["capability"] == capability)


CAPABILITIES = [p["capability"] for p in fixture_packets()]


def test_expression_adapter_module_exists():
    assert importlib.util.find_spec(MODULE) is not None, "expression plot adapters have not been implemented"


@pytest.mark.parametrize("capability", CAPABILITIES)
def test_every_capability_has_real_geometry_and_valid_source_links(capability):
    from devbench.analysis.scientific_plot_contract import validate_panel
    adapter = _adapter()
    data = _packet(capability)
    original = deepcopy(data)
    assert set(adapter.CAPABILITIES) == set(CAPABILITIES)
    assert set(adapter.CAPABILITIES[capability]) == {"required", "optional", "description"}
    panels = adapter.build_panels(capability, data)
    assert data == original, "source objects must stay read-only"
    assert panels and any(p["marks"] for p in panels)
    for p in panels:
        validate_panel(p)
        assert p["capability"] == capability
        keys = {r["key"] for r in p["rows"]}
        assert len(keys) == len(p["rows"])
        for mark in p["marks"]:
            assert set(mark["keys"]) <= keys
        assert any(r["link"] for r in p["rows"])


@pytest.mark.parametrize("capability", CAPABILITIES)
def test_missing_required_field_is_a_refusal(capability):
    adapter = _adapter()
    data = _packet(capability)
    del data[adapter.CAPABILITIES[capability]["required"][0]]
    with pytest.raises(ValueError):
        adapter.build_panels(capability, data)


@pytest.mark.parametrize("capability", ["qc_violins", "qc_ridges"])
def test_qc_pre_post_keep_excluded_cells_and_thresholds(capability):
    panels = _adapter().build_panels(capability, _packet(capability))
    assert len(panels) == 6
    assert all(len(p["rows"]) == 8 for p in panels)
    assert all(p["reference_lines"] for p in panels)
    pre = [p for p in panels if "pre-filter" in p["title"].lower()]
    post = [p for p in panels if "post-filter" in p["title"].lower()]
    assert len(pre) == len(post) == 3
    assert all(sum(r["reason"] is not None for r in p["rows"]) == 2 for p in post)
    assert all(any(m["type"] == "polygon" for m in p["marks"]) for p in panels)
    assert all(any("excluded=2" in a for a in p["annotations"]) for p in panels)


def test_qc_joint_scatter_uses_all_source_values_and_correct_mito_scale():
    panels = _adapter().build_panels("qc_joint_scatter", _packet("qc_joint_scatter"))
    assert len(panels) == 3
    assert panels[0]["rows"][0]["x"] == 10
    assert panels[0]["rows"][0]["y"] == 5
    assert panels[0]["color_scale"]["unit"] == "percent"
    assert len(panels[0]["marks"]) == 8
    assert any(m.get("shape") == "triangle" for m in panels[0]["marks"])


def test_histogram_exact_bins_median_and_explicit_log_coordinates():
    data = _packet("depth_histogram")
    p = _adapter().build_panels("depth_histogram", data)[0]
    bars = [m for m in p["marks"] if m["type"] == "rect"]
    assert sorted(m["height"] for m in bars) == [0, 0, 0, 1, 3, 4]
    assert sum(m["height"] for m in bars) == 8
    assert any(r["label"].startswith("Median") for r in p["reference_lines"])
    data["bins"] = [1, 10, 101]
    data["transform"] = "log10"
    p = _adapter().build_panels("depth_histogram", data)[0]
    assert p["axes"]["x"]["transform"] == "log10"
    assert p["rows"][4]["x"] == pytest.approx(2.0)
    assert p["rows"][4]["details"]["source_value"] == 100


def test_ecdf_ties_are_right_continuous_and_end_at_one():
    data = _packet("depth_ecdf")
    data["observations"][2]["value"] = 2
    p = _adapter().build_panels("depth_ecdf", data)[0]
    tied = [r for r in p["rows"] if r["group"] == "B1" and r["x"] == 2]
    assert len(tied) == 2 and all(r["y"] == .6 for r in tied)
    assert all(m["points"][-1][1] == 1 for m in p["marks"] if m["type"] == "polyline")


@pytest.mark.parametrize("capability", ["batch_boxplot", "expression_boxplots"])
def test_tukey_boxes_show_real_outlier_and_stable_observations(capability):
    data = _packet(capability)
    first = _adapter().build_panels(capability, data)[0]
    second = _adapter().build_panels(capability, data)[0]
    assert first == second
    assert sum(m["type"] == "point" for m in first["marks"]) == 8
    box = next(m for m in first["marks"] if m["type"] == "rect")
    assert box["y"] == (2 if capability == "batch_boxplot" else 1)
    assert box["height"] == 2
    group_rows = [r for r in first["rows"] if r["group"] in ("B1", "A")]
    assert max(r["details"]["source_value"] for r in group_rows) in (30, 100)
    assert any("n=5" in a for a in first["annotations"])


@pytest.mark.parametrize("capability", ["expression_violins", "expression_ridges"])
def test_expression_density_is_actual_kde_with_bandwidth_and_cell_links(capability):
    p = _adapter().build_panels(capability, _packet(capability))[0]
    assert any(m["type"] == "polygon" and len(m["points"]) > 10 for m in p["marks"])
    assert all(r["link"]["kind"] == "cell" for r in p["rows"])
    assert all(r["details"]["layer"] == "synthetic-counts" for r in p["rows"])
    assert any("bandwidth=1" in a for a in p["annotations"])


def test_constant_distribution_has_no_invented_density_spread():
    data = _packet("expression_violins")
    for r in data["observations"]:
        r["value"] = 2
    p = _adapter().build_panels("expression_violins", data)[0]
    assert not any(m["type"] == "polygon" for m in p["marks"])
    assert sum(m["type"] == "point" for m in p["marks"]) == 8
    assert any("density unavailable" in a.lower() for a in p["annotations"])


def test_doublet_histogram_density_and_embedding_share_all_source_cells():
    panels = _adapter().build_panels("doublet_diagnostics", _packet("doublet_diagnostics"))
    assert len(panels) == 3
    assert all(len(p["rows"]) == 5 for p in panels)
    assert any(m["type"] == "polyline" for m in panels[1]["marks"])
    assert sum(m.get("shape") == "triangle" for m in panels[2]["marks"]) == 2
    assert all(p["reference_lines"][0]["value"] == .5 for p in panels[:2])


def test_hvg_does_not_recompute_saved_selection_or_scores():
    p = _adapter().build_panels("hvg_scatter", _packet("hvg_scatter"))[0]
    assert [(r["x"], r["y"]) for r in p["rows"]] == [(2, 3), (4, 1.5)]
    assert [r["group"] for r in p["rows"]] == ["Selected HVG", "Other gene"]


def test_pca_percent_is_explicit_conversion_of_saved_variance():
    p = _adapter().build_panels("scrna_pca_scree", _packet("scrna_pca_scree"))[0]
    assert [r["y"] for r in p["rows"]] == [50, 30, 10]
    assert p["axes"]["y"]["unit"] == "percent"
    assert any(m["type"] == "polyline" for m in p["marks"])


def test_integration_plots_saved_embeddings_and_raw_asw_lisi():
    panels = _adapter().build_panels("integration_diagnostics", _packet("integration_diagnostics"))
    assert len(panels) == 6
    assert all(len(p["rows"]) == 6 for p in panels[:4])
    assert panels[0]["rows"][2]["x"] == 2
    assert panels[2]["rows"][2]["x"] == 1
    assert panels[4]["rows"][0]["x"] == -.1
    assert panels[5]["rows"][0]["y"] == 1.1


def test_feature_plot_retains_layer_value_and_explicit_display_transform():
    data = _packet("feature_expression")
    data["display_transform"] = "log1p"
    p = _adapter().build_panels("feature_expression", data)[0]
    assert p["rows"][4]["x"] == 4
    assert p["rows"][4]["details"]["source_value"] == 8
    assert p["marks"][4]["value"] == pytest.approx(math.log1p(8))
    assert "log1p" in p["color_scale"]["label"]


def test_marker_dot_area_is_fraction_above_threshold_and_denominator_retained():
    p = _adapter().build_panels("marker_dotplot", _packet("marker_dotplot"))[0]
    dots = [m for m in p["marks"] if m["type"] == "point"]
    assert (dots[0]["radius"] / dots[1]["radius"]) ** 2 == pytest.approx(2 / 3)
    assert p["size_scale"]["unit"] == "fraction"
    assert p["rows"][0]["details"]["member_ids"] == ["c1", "c2", "c3"]


@pytest.mark.parametrize("capability", ["expression_heatmap", "module_heatmap"])
def test_heatmaps_have_nine_actual_cells_and_clustering_dendrograms(capability):
    p = _adapter().build_panels(capability, _packet(capability))[0]
    assert sum(m["type"] == "rect" for m in p["marks"]) >= 9
    assert any(m["type"] in ("line", "polyline") for m in p["marks"])
    assert p["color_scale"] and len(p["color_scale"]["domain"]) == 2
    assert any("cluster" in a.lower() or "linkage" in a.lower() for a in p["annotations"])


def test_constant_row_zscore_is_unavailable_not_zero():
    data = _packet("expression_heatmap")
    data["values"][0] = [2, 2, 2]
    data["transform"] = "row_zscore"
    p = _adapter().build_panels("expression_heatmap", data)[0]
    missing = [r for r in p["rows"] if r["reason"] is not None]
    assert len(missing) >= 3
    assert any("constant" in str(r).lower() or "variance" in str(r).lower() for r in missing)


def test_donor_composition_uses_per_donor_points_no_invented_intervals():
    data = _packet("donor_composition")
    data.pop("summaries")
    p = _adapter().build_panels("donor_composition", data)[0]
    assert [r["y"] for r in p["rows"]] == [.25, .5, .75, .5]
    assert all(r["link"]["kind"] == "donor" for r in p["rows"])
    assert [m["type"] for m in p["marks"]] == ["point"] * 4
    assert any("biological unit: donor" in a.lower() for a in p["annotations"])


def test_saved_donor_interval_requires_and_retains_exact_support():
    data = _packet("donor_composition")
    data["summaries"] = [{"id": "synthetic-saved-interval", "type": "interval", "condition": "A",
                          "cell_type": "SyntheticT", "unit_ids": ["d1", "d2"], "estimate": .375,
                          "low": .2, "high": .6, "method": "Synthetic saved interval", "unit": "fraction"}]
    p = _adapter().build_panels("donor_composition", data)[0]
    assert any(m["type"] == "line" and m["y1"] == .2 and m["y2"] == .6 for m in p["marks"])
    assert len(p["rows"]) == 5


def test_batch_composition_is_stacked_fraction_with_exact_membership():
    p = _adapter().build_panels("batch_composition", _packet("batch_composition"))[0]
    bars = [m for m in p["marks"] if m["type"] == "rect"]
    assert [b["height"] for b in bars] == [.25, .75, .25, .75]
    assert [b["y"] for b in bars] == [0, .25, 0, .25]
    assert all(len(r["details"]["member_ids"]) == 4 for r in p["rows"])


def test_classifier_confidence_is_not_claimed_calibrated():
    p = _adapter().build_panels("classifier_confidence_histogram", _packet("classifier_confidence_histogram"))[0]
    assert sum(m["height"] for m in p["marks"] if m["type"] == "rect") == 4
    assert any("calibration" in a.lower() for a in p["limitations"])


def test_density_bins_cover_every_point_once_and_centroids_are_exact():
    p = _adapter().build_panels("embedding_density_centroids", _packet("embedding_density_centroids"))[0]
    bins = [m for m in p["marks"] if m["type"] == "rect"]
    keys = [k for m in bins for k in m["keys"]]
    assert len(keys) == len(set(keys)) == len(p["rows"]) == 8
    labels = [m for m in p["marks"] if m["type"] == "text"]
    assert [(m["x"], m["y"]) for m in labels] == [(1.5, 0), (1.5, 1)]
    assert all(len(m["keys"]) == 4 for m in labels)


@pytest.mark.parametrize("capability", CAPABILITIES)
def test_missing_numeric_observation_remains_explicit(capability):
    data = _packet(capability)
    if capability.startswith("qc_"):
        data["cells"][0]["total_counts"] = None
    elif capability in ("depth_histogram", "depth_ecdf", "batch_boxplot", "expression_violins", "expression_boxplots", "expression_ridges", "classifier_confidence_histogram", "ambient_rna_diagnostics"):
        data["observations"][0]["value"] = None
    elif capability == "doublet_diagnostics":
        data["cells"][0]["score"] = None
    elif capability == "hvg_scatter":
        data["genes"][0]["statistic"] = None
    elif capability == "scrna_pca_scree":
        data["components"][0]["variance_explained"] = None
    elif capability == "integration_diagnostics":
        data["cells"][0]["before"] = [None, None]
    elif capability == "feature_expression":
        data["cells"][0]["value"] = None
    elif capability == "marker_dotplot":
        data["group_gene_aggregates"][0].pop("values")
        data["group_gene_aggregates"][0]["mean_expression"] = None
    elif capability.endswith("heatmap"):
        data["values"][0][0] = None
    elif capability in ("donor_composition", "batch_composition"):
        data.pop("summaries", None)
        data["compositions"][0]["numerator"] = None
        data["compositions"][0]["numerator_ids"] = None
    else:
        data["cells"][0]["x"] = None
    panels = _adapter().build_panels(capability, data)
    assert any(r["reason"] is not None for p in panels for r in p["rows"])
    assert all(r["reason"] for p in panels for r in p["rows"] if r["x"] is None or r["y"] is None)


@pytest.mark.parametrize("capability", CAPABILITIES)
def test_every_capability_refuses_incompatible_scientific_input(capability):
    data = _packet(capability)
    if capability.startswith("qc_"):
        data["units"]["pct_counts_mt"] = "fraction"
    elif capability in ("depth_histogram", "depth_ecdf", "batch_boxplot"):
        data["unit"] = "log_normalized_expression"
    elif capability.startswith("expression_") or capability == "feature_expression":
        data["source_scale"] = "module_score"
    elif capability == "doublet_diagnostics":
        data["score_unit"] = "count"
    elif capability == "hvg_scatter":
        data["statistic_unit"] = "count"
    elif capability == "scrna_pca_scree":
        data["components"][0]["variance_explained"] = 1.5
    elif capability == "integration_diagnostics":
        data["metrics"][0]["batch_asw"] = 1.5
    elif capability == "marker_dotplot":
        data["group_gene_aggregates"][0]["denominator"] = 2
    elif capability == "module_heatmap":
        data["source_scale"] = "counts"
    elif capability in ("donor_composition", "batch_composition"):
        data["compositions"][0]["numerator_ids"] = ["not-in-denominator"]
    elif capability == "classifier_confidence_histogram":
        data["observations"][0]["value"] = 1.5
    elif capability == "ambient_rna_diagnostics":
        data["unit"] = "count"
    else:
        data["cells"][1]["id"] = data["cells"][0]["id"]
    with pytest.raises(ValueError):
        _adapter().build_panels(capability, data)


def test_pairing_distinct_donors_is_refused():
    data = _packet("donor_composition")
    data["pairs"] = [{"from_id": "synthetic-composition-d1", "to_id": "synthetic-composition-d3"}]
    with pytest.raises(ValueError, match="same.*unit|same.*donor"):
        _adapter().build_panels("donor_composition", data)


def test_histogram_refuses_silent_out_of_bin_clipping():
    data = _packet("depth_histogram")
    data["bins"] = [0, 10]
    with pytest.raises(ValueError, match="bin|range"):
        _adapter().build_panels("depth_histogram", data)


def test_duplicate_sample_per_donor_is_not_an_extra_biological_replicate():
    data = _packet("donor_composition")
    duplicate = deepcopy(data["compositions"][0])
    duplicate["id"] = "another-sample-same-donor"
    duplicate["sample_id"] = "technical-second-sample"
    data["compositions"].append(duplicate)
    with pytest.raises(ValueError, match="biological|donor|unit"):
        _adapter().build_panels("donor_composition", data)


def test_marker_refuses_inconsistent_saved_raw_threshold_fraction():
    data = _packet("marker_dotplot")
    data["group_gene_aggregates"][0]["numerator"] = 3
    with pytest.raises(ValueError, match="threshold|numerator"):
        _adapter().build_panels("marker_dotplot", data)


def test_expression_does_not_silently_double_log_a_saved_log_layer():
    data = _packet("feature_expression")
    data["source_scale"] = "log1p_expression"
    data["unit"] = "log1p_expression"
    data["display_transform"] = "log1p"
    with pytest.raises(ValueError, match="already|log"):
        _adapter().build_panels("feature_expression", data)


@pytest.mark.parametrize("capability", CAPABILITIES)
def test_each_builder_returns_distinct_panel_identifiers(capability):
    panels = _adapter().build_panels(capability, _packet(capability))
    assert len({p["id"] for p in panels}) == len(panels)


def test_saved_names_with_spaces_and_slashes_remain_valid_labels():
    expression = _packet("expression_violins")
    for observation in expression["observations"]:
        observation["gene_id"] = "Synthetic gene / α"
    assert "Synthetic gene / α" in _adapter().build_panels("expression_violins", expression)[0]["title"]
    composition = _packet("donor_composition")
    for observation in composition["compositions"]:
        observation["cell_type"] = "Synthetic T / memory cells"
    for summary in composition.get("summaries", []):
        summary["cell_type"] = "Synthetic T / memory cells"
    assert "Synthetic T / memory cells" in _adapter().build_panels("donor_composition", composition)[0]["title"]


def test_depth_median_is_transformed_after_source_scale_summary():
    data = _packet("depth_histogram")
    data["observations"] = [{"id": "a", "value": 1, "batch": "A"}, {"id": "b", "value": 9, "batch": "A"}]
    data["bins"] = [1, 10]
    data["transform"] = "log10"
    p = _adapter().build_panels("depth_histogram", data)[0]
    median_line = next(line for line in p["reference_lines"] if line["label"].startswith("Median"))
    assert median_line["value"] == pytest.approx(math.log10(5))


def test_two_saved_intervals_preserve_only_actual_donors_as_support():
    data = _packet("donor_composition")
    first = {"id": "interval-one", "type": "interval", "condition": "A", "cell_type": "SyntheticT",
             "unit_ids": ["d1", "d2"], "estimate": .375, "low": .2, "high": .6,
             "method": "Synthetic saved interval one", "unit": "fraction"}
    second = {**first, "id": "interval-two", "method": "Synthetic saved interval two", "low": .3, "high": .5}
    data["summaries"] = [first, second]
    p = _adapter().build_panels("donor_composition", data)[0]
    assert len(p["rows"]) == 6


def test_one_source_cell_cannot_be_two_independent_donor_memberships():
    data = _packet("donor_composition")
    first, second = data["compositions"][:2]
    second["member_ids"][0] = first["member_ids"][0]
    second["numerator_ids"][0] = first["numerator_ids"][0]
    with pytest.raises(ValueError, match="multiple|unit|donor|membership"):
        _adapter().build_panels("donor_composition", data)


@pytest.mark.parametrize("capability", ["qc_violins", "depth_histogram", "expression_violins", "feature_expression", "expression_heatmap", "marker_dotplot"])
def test_fractional_values_cannot_be_mislabeled_raw_counts(capability):
    data = _packet(capability)
    if capability == "qc_violins":
        data["cells"][0]["total_counts"] = 1.5
    elif capability in ("depth_histogram", "expression_violins"):
        data["observations"][0]["value"] = 1.5
    elif capability == "feature_expression":
        data["cells"][0]["value"] = 1.5
    elif capability == "expression_heatmap":
        data["values"][0][0] = 1.5
    else:
        data["group_gene_aggregates"][0]["values"] = [0, .5, 2.5]
    with pytest.raises(ValueError, match="integer|count"):
        _adapter().build_panels(capability, data)


def test_fractional_means_of_integer_raw_counts_remain_valid():
    data = _packet("marker_dotplot")
    a = data["group_gene_aggregates"][0]
    a["values"] = [0, 1, 3]
    a["mean_expression"] = 4 / 3
    assert _adapter().build_panels("marker_dotplot", data)[0]["marks"][0]["value"] == pytest.approx(4 / 3)


def test_repeated_donor_samples_across_conditions_require_donor_units():
    data = _packet("donor_composition")
    data["biological_unit"] = "sample"
    data["compositions"][2]["donor_id"] = data["compositions"][0]["donor_id"]
    with pytest.raises(ValueError, match="Repeated|donor"):
        _adapter().build_panels("donor_composition", data)


def test_saved_tukey_composition_box_matches_actual_donor_support():
    data = _packet("donor_composition")
    data["summaries"] = [{"id": "saved-box", "type": "box", "condition": "A", "cell_type": "SyntheticT",
                          "unit_ids": ["d1", "d2"], "q1": .3125, "median": .375, "q3": .4375,
                          "whisker_low": .25, "whisker_high": .5, "method": "Saved Tukey 1.5 IQR", "unit": "fraction"}]
    p = _adapter().build_panels("donor_composition", data)[0]
    assert any(m["type"] == "rect" and m["height"] == .125 for m in p["marks"])
    data["summaries"][0]["whisker_high"] = .9
    with pytest.raises(ValueError, match="Tukey|disagrees"):
        _adapter().build_panels("donor_composition", data)


def test_valid_explicit_donor_pair_preserves_both_source_keys():
    data = _packet("donor_composition")
    data["compositions"][2]["donor_id"] = "d1"
    data["pairs"] = [{"from_id": data["compositions"][0]["id"], "to_id": data["compositions"][2]["id"]}]
    p = _adapter().build_panels("donor_composition", data)[0]
    line = next(m for m in p["marks"] if m["type"] == "line")
    assert line["keys"] == [p["rows"][0]["key"], p["rows"][2]["key"]]


def test_module_heatmap_does_not_link_scores_as_gene_expression():
    p = _adapter().build_panels("module_heatmap", _packet("module_heatmap"))[0]
    assert all(r["link"]["kind"] == "module" for r in p["rows"] if r["details"].get("row_id"))


@pytest.mark.parametrize("value", [None, {}, "not-edges", [0, None, 1]])
def test_malformed_confidence_bins_produce_eligibility_value_error(value):
    data = _packet("classifier_confidence_histogram")
    data["bins"] = value
    with pytest.raises(ValueError):
        _adapter().build_panels("classifier_confidence_histogram", data)


def test_saved_tsne_retains_exact_coordinates_and_source_configuration():
    data = _packet("saved_embedding")
    p = _adapter().build_panels("saved_embedding", data)[0]
    assert p["axes"]["x"]["label"] == "t-SNE 1"
    assert [(r["x"], r["y"]) for r in p["rows"]] == [(c["x"], c["y"]) for c in data["cells"]]
    assert p["rows"][0]["details"]["source_configuration"] == data["configuration"]
    assert all(r["details"]["representation"] == data["representation"] for r in p["rows"])
    assert all(m["type"] == "point" for m in p["marks"])


@pytest.mark.parametrize("method", ["PCA", "UMAP/tSNE", "unknown", None])
def test_saved_embedding_requires_an_unambiguous_declared_method(method):
    data = _packet("saved_embedding")
    data["method"] = method
    with pytest.raises(ValueError, match="method|UMAP|t-SNE"):
        _adapter().build_panels("saved_embedding", data)


def test_hexbin_is_true_equal_area_axial_hexagons_with_exact_membership():
    data = _packet("embedding_hexbin")
    p = _adapter().build_panels("embedding_hexbin", data)[0]
    bins = [m for m in p["marks"] if m["type"] == "polygon"]
    assert len(bins) == 3
    assert sorted(len(m["keys"]) for m in bins) == [1, 2, 2]
    assert sorted(m["value"] for m in bins) == [1, 2, 2]
    assert sorted(k for m in bins for k in m["keys"]) == sorted(r["key"] for r in p["rows"])
    origin_bin = next(m for m in bins if p["rows"][0]["key"] in m["keys"])
    assert len(origin_bin["points"]) == 6
    assert all(math.hypot(x, y) == pytest.approx(1) for x, y in origin_bin["points"])
    for mark in bins:
        vertices = mark["points"]
        area = abs(sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(vertices, vertices[1:] + vertices[:1]))) / 2
        assert area == pytest.approx(3 * math.sqrt(3) / 2)
    assert p["rows"][2]["details"]["hex_axial_bin"] == [1, 0]
    assert p["rows"][4]["details"]["hex_axial_bin"] == [0, 1]


def test_hexbin_translation_moves_geometry_and_preserves_bin_membership():
    data = _packet("embedding_hexbin")
    original = _adapter().build_panels("embedding_hexbin", data)[0]
    data["origin"] = [10, -20]
    for cell in data["cells"]:
        cell["x"] += 10
        cell["y"] -= 20
    moved = _adapter().build_panels("embedding_hexbin", data)[0]
    assert [m["keys"] for m in moved["marks"]] == [m["keys"] for m in original["marks"]]
    for a, b in zip(original["marks"], moved["marks"]):
        for (x, y), moved_point in zip(a["points"], b["points"]):
            assert moved_point == pytest.approx([x + 10, y - 20])


def test_kde_contours_follow_actual_two_dimensional_density_levels():
    data = _packet("embedding_density_contours")
    p = _adapter().build_panels("embedding_density_contours", data)[0]
    contours = [m for m in p["marks"] if m["type"] == "polyline"]
    assert {m["value"] for m in contours} == set(data["levels"])
    assert all(len(m["points"]) > 20 for m in contours)
    assert sum(m["type"] == "point" for m in p["marks"]) == len(data["cells"])
    all_keys = {r["key"] for r in p["rows"]}
    hx, hy = data["bandwidth"]
    for mark in contours:
        assert set(mark["keys"]) == all_keys, "Every Gaussian kernel contributes to every contour"
        for x, y in mark["points"][::10]:
            exact = sum(math.exp(-.5 * (((x - c["x"]) / hx) ** 2 + ((y - c["y"]) / hy) ** 2)) for c in data["cells"]) / (len(data["cells"]) * 2 * math.pi * hx * hy)
            assert exact == pytest.approx(mark["value"], abs=.0006)
    assert p["rows"][0]["details"]["density_grid_size"] == [65, 65]
    assert p["rows"][0]["details"]["density_bandwidth"] == [.5, .5]


def test_kde_degenerate_coordinates_keep_points_with_explicit_failure_reason():
    data = _packet("embedding_density_contours")
    for cell in data["cells"]:
        cell["y"] = 2 * cell["x"]
    p = _adapter().build_panels("embedding_density_contours", data)[0]
    assert not any(m["type"] == "polyline" for m in p["marks"])
    assert sum(m["type"] == "point" for m in p["marks"]) == len(data["cells"])
    assert all("rank" in r["details"]["density_unavailable_reason"] for r in p["rows"])
    assert any("unavailable" in note.lower() for note in p["annotations"])


@pytest.mark.parametrize("field,value", [("bandwidth", [0, 1]), ("bandwidth", [1]),
                                        ("grid_size", [1, 65]), ("levels", [0]), ("levels", [.1, .05]),
                                        ("bounds", [-.5, .5, -.5, .5])])
def test_density_contours_refuse_undefined_or_clipping_display_parameters(field, value):
    data = _packet("embedding_density_contours")
    data[field] = value
    with pytest.raises(ValueError):
        _adapter().build_panels("embedding_density_contours", data)


def test_contours_do_not_draw_an_invented_requested_density_level():
    data = _packet("embedding_density_contours")
    data["levels"] = [1e6]
    with pytest.raises(ValueError, match="level|intersect"):
        _adapter().build_panels("embedding_density_contours", data)


@pytest.mark.parametrize("capability", ["saved_embedding", "embedding_hexbin", "embedding_density_contours"])
def test_embedding_configuration_is_saved_finite_metadata(capability):
    data = _packet(capability)
    data["configuration"] = {"perplexity": float("nan")}
    with pytest.raises(ValueError, match="configuration|finite"):
        _adapter().build_panels(capability, data)


def test_ambient_diagnostics_render_only_saved_quantities_and_preserve_failures():
    data = _packet("ambient_rna_diagnostics")
    panels = _adapter().build_panels("ambient_rna_diagnostics", data)
    assert len(panels) == 6
    histogram, violin, depth, embedding, profile, correction = panels
    assert sum(m["height"] for m in histogram["marks"] if m["type"] == "rect") == 5
    assert any(m["type"] == "polygon" for m in violin["marks"])
    assert len(embedding["rows"]) == len(data["observations"]) == 6
    assert embedding["rows"][1]["x"] == 1
    assert embedding["marks"][1]["value"] == .1
    assert depth["rows"][0]["x"] == 100 and depth["rows"][0]["y"] == .05
    assert profile["rows"][0]["y"] == .4
    assert correction["rows"][0]["x"] == 8 and correction["rows"][0]["y"] == 4.5
    assert len(correction["rows"]) == 3 and correction["rows"][2]["reason"] == "Synthetic saved correction failure"
    for p in panels[:4]:
        assert {r["id"] for r in p["rows"]} == {r["id"] for r in data["observations"]}
        assert p["rows"][-1]["reason"] == "Synthetic saved estimator failure"
        assert all(p["rows"][-1]["key"] not in m["keys"] for m in p["marks"])
    assert any("calibration" in text.lower() for text in histogram["limitations"])


def test_ambient_minimal_saved_input_never_invents_embedding_profile_or_correction():
    data = _packet("ambient_rna_diagnostics")
    for field in ("embedding", "ambient_gene_profile", "correction", "cutoffs"):
        del data[field]
    for observation in data["observations"]:
        observation.pop("total_counts")
    panels = _adapter().build_panels("ambient_rna_diagnostics", data)
    assert len(panels) == 2
    assert all(not p["reference_lines"] for p in panels)


def test_ambient_failed_numeric_estimate_is_retained_but_not_shown_as_success():
    data = _packet("ambient_rna_diagnostics")
    data["observations"][0].update(status="failed", reason="Saved nonconvergence", value=.99)
    panels = _adapter().build_panels("ambient_rna_diagnostics", data)
    for p in panels[:4]:
        r = p["rows"][0]
        assert r["x"] is None and r["y"] is None
        assert r["reason"] == "Saved nonconvergence"
        assert r["details"]["saved_estimator_value"] == .99
        assert all(r["key"] not in mark["keys"] for mark in p["marks"])


def test_ambient_scores_are_not_reinterpreted_as_fractions():
    data = _packet("ambient_rna_diagnostics")
    data.update(measurement="ambient_rna_score", unit="score", bins=[-4, 0, 4], cutoffs=[])
    data["observations"][0]["value"] = -2.5
    p = _adapter().build_panels("ambient_rna_diagnostics", data)[0]
    assert p["rows"][0]["x"] == -2.5
    assert p["axes"]["x"]["unit"] == "score"
    data["unit"] = "fraction"
    with pytest.raises(ValueError, match="unit|fraction|score"):
        _adapter().build_panels("ambient_rna_diagnostics", data)


def test_ambient_percent_stays_percent_in_rows_legends_and_saved_cutoffs():
    data = _packet("ambient_rna_diagnostics")
    data.update(unit="percent", bins=[0, 10, 25, 50, 100])
    data["cutoffs"][0]["value"] = 25
    for r in data["observations"]:
        if r["value"] is not None:
            r["value"] *= 100
    panels = _adapter().build_panels("ambient_rna_diagnostics", data)
    assert panels[0]["rows"][0]["x"] == 5
    assert panels[0]["reference_lines"][0]["value"] == 25
    assert panels[3]["color_scale"]["unit"] == "percent"


def test_ambient_partial_saved_embedding_does_not_drop_source_observations():
    data = _packet("ambient_rna_diagnostics")
    data["embedding"]["coordinates"] = data["embedding"]["coordinates"][:2]
    p = _adapter().build_panels("ambient_rna_diagnostics", data)[3]
    assert len(p["rows"]) == 6
    assert sum(r["reason"] is None for r in p["rows"]) == 2
    data["embedding"]["coordinates"].append({"id": "unbound-cell", "x": 0, "y": 0})
    with pytest.raises(ValueError, match="identity|source|observation"):
        _adapter().build_panels("ambient_rna_diagnostics", data)


def test_ambient_refuses_incomparable_saved_correction_scales():
    data = _packet("ambient_rna_diagnostics")
    data["correction"].update(after_scale="normalized_expression", after_unit="normalized_expression")
    with pytest.raises(ValueError, match="compar|scale|unit"):
        _adapter().build_panels("ambient_rna_diagnostics", data)


def test_ambient_sample_observations_keep_explicit_sample_links():
    data = _packet("ambient_rna_diagnostics")
    data.pop("embedding")
    data.pop("correction")
    data["entity_kind"] = "sample"
    data["observations"] = data["observations"][::3]
    p = _adapter().build_panels("ambient_rna_diagnostics", data)[0]
    assert [r["link"] for r in p["rows"]] == [{"kind": "sample", "id": "S0"}, {"kind": "sample", "id": "S1"}]
    data["observations"][1]["sample_id"] = "S0"
    with pytest.raises(ValueError, match="sample|unique|Duplicate"):
        _adapter().build_panels("ambient_rna_diagnostics", data)


@pytest.mark.parametrize("failure", ["fraction_domain", "failure_reason", "profile_unit", "correction_identity", "cutoff_source"])
def test_ambient_semantic_refusals(failure):
    data = _packet("ambient_rna_diagnostics")
    if failure == "fraction_domain":
        data["observations"][0]["value"] = 1.1
    elif failure == "failure_reason":
        data["observations"][-1].pop("reason")
    elif failure == "profile_unit":
        data["ambient_gene_profile"]["unit"] = "unknown"
    elif failure == "correction_identity":
        data["correction"]["values"][0]["observation_id"] = "unknown-cell"
    else:
        data["cutoffs"][0].pop("source")
    with pytest.raises(ValueError):
        _adapter().build_panels("ambient_rna_diagnostics", data)


def test_saved_composition_interval_carries_explicit_kind_level_scope_and_membership():
    data = _packet("donor_composition")
    p = _adapter().build_panels("donor_composition", data)[0]
    summary = next(r for r in p["rows"] if r["id"] == "synthetic-defined-interval")
    assert summary["details"]["interval_kind"] == "confidence"
    assert summary["details"]["level"] == .9
    assert summary["details"]["scope"] == data["summaries"][0]["scope"]
    assert summary["details"]["method"] == data["summaries"][0]["method"]
    assert summary["details"]["denominator_kind"] == "all_saved_cells"
    assert summary["details"]["unit_ids"] == ["d1", "d2"]
    assert summary["details"]["uncertainty_definition_status"] == "explicit_saved_definition"
    label = next(m for m in p["marks"] if m["type"] == "text")
    assert label["text"] == "90% confidence"
    assert set(label["keys"]) == {p["rows"][0]["key"], p["rows"][1]["key"], summary["key"]}
    assert any(data["summaries"][0]["scope"] in annotation for annotation in p["annotations"])


def test_legacy_composition_bounds_are_explicitly_unassessed_not_assumed_confidence():
    data = _packet("donor_composition")
    for name in ("interval_kind", "level", "scope", "denominator_kind"):
        data["summaries"][0].pop(name)
    p = _adapter().build_panels("donor_composition", data)[0]
    summary = next(r for r in p["rows"] if r["entity_kind"] == "group")
    assert summary["details"]["interval_kind"] == "unassessed"
    assert summary["details"]["level"] is None
    assert summary["details"]["scope"] is None
    assert summary["details"]["uncertainty_definition_status"] == "unassessed"
    assert next(m["text"] for m in p["marks"] if m["type"] == "text") == "Unassessed bounds"
    assert not any("95%" in str(mark) or "confidence" in str(mark) for mark in p["marks"])
    assert any("no confidence" in annotation.lower() for annotation in p["annotations"])


@pytest.mark.parametrize("kind,level,label", [("credible", .8, "80% credible"), ("range", None, "Saved range")])
def test_saved_composition_interval_types_remain_distinct(kind, level, label):
    data = _packet("donor_composition")
    data["summaries"][0].update(interval_kind=kind, level=level, scope="Explicit synthetic saved scope")
    p = _adapter().build_panels("donor_composition", data)[0]
    assert next(m["text"] for m in p["marks"] if m["type"] == "text") == label
    assert next(r for r in p["rows"] if r["entity_kind"] == "group")["details"]["interval_kind"] == kind


@pytest.mark.parametrize("field,value", [("interval_kind", "prediction"), ("level", None), ("level", 0),
                                         ("level", 1), ("level", 95), ("level", True), ("scope", ""),
                                         ("denominator_kind", "classified_cells"), ("unit", "percent"),
                                         ("low", .7), ("high", -.1), ("unit_ids", ["d1", "d3"])])
def test_composition_interval_refuses_inconsistent_definition_bounds_or_denominators(field, value):
    data = _packet("donor_composition")
    data["summaries"][0][field] = value
    with pytest.raises(ValueError):
        _adapter().build_panels("donor_composition", data)


@pytest.mark.parametrize("field", ["level", "scope", "denominator_kind"])
def test_defined_composition_intervals_require_complete_saved_meaning(field):
    data = _packet("donor_composition")
    data["summaries"][0].pop(field)
    with pytest.raises(ValueError, match="interval|level|scope|denominator"):
        _adapter().build_panels("donor_composition", data)


def test_saved_range_cannot_claim_a_confidence_level():
    data = _packet("donor_composition")
    data["summaries"][0]["interval_kind"] = "range"
    with pytest.raises(ValueError, match="level|range"):
        _adapter().build_panels("donor_composition", data)


def test_level_without_saved_interval_kind_cannot_be_interpreted_as_confidence():
    data = _packet("donor_composition")
    data["summaries"][0].pop("interval_kind")
    with pytest.raises(ValueError, match="kind|level"):
        _adapter().build_panels("donor_composition", data)
