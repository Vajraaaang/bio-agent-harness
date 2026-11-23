"""Synthetic geometry and refusal checks; these are not scientific validation."""

from copy import deepcopy
import importlib
import importlib.util

import pytest


CAPABILITIES = (
    "gsea_running", "enrichment_dotplot", "temporal_spline",
    "temporal_model_comparison", "saved_temporal_curve", "pseudotime_monotonicity",
    "lineage_schematic", "regulatory_network", "motif_enrichment",
    "resource_curve", "execution_dag", "repertoire_abundance",
    "repertoire_overlap", "repertoire_diversity", "multimodal_features",
    "multimodal_heatmap", "rna_velocity", "spatial_expression",
    "splice_tracks", "perturbation_control", "orthology_comparison",
    "genomic_effect", "sva_association",
)


def api():
    name = "devbench.analysis.plot_adapters_advanced"
    assert importlib.util.find_spec(name) is not None, "advanced adapters are not implemented"
    return importlib.import_module(name)


def packet(capability):
    return deepcopy(next(p["data"] for p in fixture_packets() if p["capability"] == capability))


def panels(capability, data=None):
    return api().build_panels(capability, packet(capability) if data is None else data)


def marks(panel, kind):
    return [m for m in panel["marks"] if m["type"] == kind]


def test_complete_named_inventory_has_genuine_adapters():
    assert set(api().CAPABILITIES) == set(CAPABILITIES)
    assert {p["capability"] for p in fixture_packets()} == set(CAPABILITIES)


@pytest.mark.parametrize("capability", CAPABILITIES)
def test_synthetic_source_builds_valid_source_linked_geometry(capability):
    from devbench.analysis.scientific_plot_contract import validate_panel

    data = packet(capability)
    original = deepcopy(data)
    result = panels(capability, data)
    assert data == original, "adapter mutated the supplied saved source"
    assert result
    for p in result:
        validate_panel(p)
        assert p["capability"] == capability
        assert p["marks"], "a generic unavailable panel is not an adapter"
        keys = {r["key"] for r in p["rows"]}
        assert keys
        assert all(set(m["keys"]) <= keys for m in p["marks"])
        assert p["axes"]["x"]["unit"] and p["axes"]["y"]["unit"]
        assert any("synthetic" in s.lower() for s in p["limitations"])


@pytest.mark.parametrize("capability", CAPABILITIES)
def test_required_evidence_cannot_be_omitted(capability):
    spec = api().CAPABILITIES[capability]
    assert spec["required"] and spec["description"]
    assert isinstance(spec["optional"], list)
    for field in spec["required"]:
        data = packet(capability)
        del data[field]
        with pytest.raises(ValueError):
            panels(capability, data)


def test_unknown_capability_or_fields_are_rejected():
    with pytest.raises(ValueError):
        api().build_panels("invented", {})
    data = packet("resource_curve")
    data["invented_parameter"] = "not used"
    with pytest.raises(ValueError):
        panels("resource_curve", data)


def test_gsea_curve_and_barcode_use_saved_ranks_scores():
    curve, rug = panels("gsea_running")
    assert marks(curve, "polyline")[0]["points"] == [[1.0, 0.4], [2.0, 0.1], [3.0, 0.6], [4.0, 0.0]]
    assert [m["x1"] for m in marks(rug, "line")] == [1.0, 3.0]
    assert all(m["x1"] == m["x2"] for m in marks(rug, "line"))
    assert "4" in " ".join(curve["annotations"])


def test_gsea_incomplete_ranking_and_unmatched_hit_are_rejected():
    data = packet("gsea_running")
    data["running_scores"].pop()
    with pytest.raises(ValueError, match="rank"):
        panels("gsea_running", data)
    data = packet("gsea_running")
    data["hits"][0]["rank"] = 5
    with pytest.raises(ValueError, match="rank"):
        panels("gsea_running", data)


def test_enrichment_ratio_denominator_q_area_and_direction():
    p = panels("enrichment_dotplot")[0]
    assert [r["x"] for r in p["rows"] if r["entity_kind"] == "gene_set"] == [0.2, 0.4]
    pts = marks(p, "point")
    assert pts[1]["radius"] ** 2 / pts[0]["radius"] ** 2 == pytest.approx(2)
    assert {m["group"] for m in pts} == {"up", "down"}
    assert all(r["details"]["query_size"] == 10 for r in p["rows"] if r["entity_kind"] == "gene_set")
    assert "100" in " ".join(p["annotations"])
    data = packet("enrichment_dotplot")
    data.update(method="GSEA", x_metric="NES")
    data["terms"][0]["nes"] = 1.6
    data["terms"][1]["nes"] = -2.1
    assert [r["x"] for r in panels("enrichment_dotplot", data)[0]["rows"] if r["entity_kind"] == "gene_set"] == [1.6, -2.1]
    data["method"] = "regulon"
    assert panels("enrichment_dotplot", data)


def test_enrichment_invalid_universe_and_zero_q_are_not_fabricated():
    data = packet("enrichment_dotplot")
    data["terms"][0]["query_size"] = 1000
    with pytest.raises(ValueError, match="universe"):
        panels("enrichment_dotplot", data)
    data = packet("enrichment_dotplot")
    data["terms"][0]["q"] = 0
    p = panels("enrichment_dotplot", data)[0]
    assert p["rows"][0]["x"] is None
    assert "underflow" in p["rows"][0]["reason"]
    assert len(marks(p, "point")) == 1


def test_temporal_saved_ribbon_observations_and_df():
    p = panels("temporal_spline")[0]
    assert marks(p, "polygon")[0]["points"] == [[0.0, 0.5], [1.0, 1.5], [2.0, 3.0], [2.0, 1.0], [1.0, 0.5], [0.0, 0.1]]
    assert {(m["x"], m["y"]) for m in marks(p, "point") if m["keys"][0].startswith("observation:")} == {(0.0, 0.2), (1.0, 1.2), (2.0, 2.2)}
    text = " ".join(p["annotations"])
    assert "0.9" in text and "df=3" in text and "biological" in text
    assert "selection-conditional" in " ".join(p["limitations"])


def test_temporal_refuses_fake_interval_nb_scale_or_offsets():
    data = packet("temporal_spline")
    del data["interval"]["level"]
    with pytest.raises(ValueError):
        panels("temporal_spline", data)
    data = packet("temporal_spline")
    data["family"] = "negative_binomial"
    with pytest.raises(ValueError, match="raw_counts"):
        panels("temporal_spline", data)
    data["input_scale"] = "raw_counts"
    with pytest.raises(ValueError, match="RLE"):
        panels("temporal_spline", data)
    data["model"].update(offset_method="RLE", dispersion_method="empirical_bayes")
    data["observations"] = [dict(r, value=i + 1) for i, r in enumerate(data["observations"])]
    assert panels("temporal_spline", data)


def test_failed_fit_is_retained_without_a_success_looking_curve():
    data = packet("temporal_spline")
    data["fitted"][1].update(value=None, lower=None, upper=None, status="failed", reason="singular fit")
    p = panels("temporal_spline", data)[0]
    failed = next(r for r in p["rows"] if r["id"] == "fit1")
    assert failed["y"] is None and failed["reason"] == "singular fit"
    assert not marks(p, "polygon")
    assert not marks(p, "polyline"), "must not bridge a failed fit point"


def test_candidate_comparison_uses_actual_saved_criteria_and_failures():
    result = panels("temporal_model_comparison")
    assert len(result) == 3
    assert [r["y"] for r in result[0]["rows"]] == [12.0, 10.0, None]
    assert result[0]["rows"][2]["reason"] == "nonconvergence"
    data = packet("temporal_model_comparison")
    data["candidates"][0]["comparison_set_id"] = "different response"
    with pytest.raises(ValueError, match="comparison"):
        panels("temporal_model_comparison", data)


def test_pseudotime_age_is_reference_and_not_ground_truth():
    p = panels("pseudotime_monotonicity")[0]
    assert [(r["x"], r["y"]) for r in p["rows"]] == [(1.0, 0.2), (2.0, 0.8)]
    assert p["axes"]["x"]["unit"] == "days"
    assert "not ground truth" in " ".join(p["limitations"])
    data = packet("pseudotime_monotonicity")
    del data["observations"][0]["biological_unit_id"]
    with pytest.raises(ValueError):
        panels("pseudotime_monotonicity", data)


@pytest.mark.parametrize("capability", ["lineage_schematic", "execution_dag"])
def test_graph_layout_preserves_edges_and_rejects_cycles(capability):
    p = panels(capability)[0]
    assert len(marks(p, "arrow")) == 2
    assert "schematic" in " ".join(p["annotations"]).lower()
    data = packet(capability)
    data["edges"].append({"id": "cycle", "source": "c", "target": "a", "evidence": "inferred"})
    with pytest.raises(ValueError, match="cycle"):
        panels(capability, data)


def test_regulatory_evidence_channels_are_separate_and_not_causal():
    p = panels("regulatory_network")[0]
    assert len(marks(p, "line")) == 3
    assert any(m.get("shape") == "diamond" for m in marks(p, "point"))
    assert not marks(p, "arrow")
    assert "causal" in " ".join(p["limitations"])
    edge = next(r for r in p["rows"] if r["id"] == "edge1")
    assert edge["details"]["correlation"] == -0.8
    data = packet("regulatory_network")
    del data["edges"][0]["motif_source"]
    with pytest.raises(ValueError, match="motif"):
        panels("regulatory_network", data)


def test_motif_requires_background_and_uses_saved_enrichment():
    p = panels("motif_enrichment")[0]
    assert p["rows"][0]["x"] == 2.0
    assert p["rows"][0]["details"]["background_size"] == 100
    data = packet("motif_enrichment")
    del data["terms"][0]["background_hits"]
    with pytest.raises(ValueError, match="background"):
        panels("motif_enrichment", data)


def test_resource_curve_preserves_time_and_memory_units():
    p = panels("resource_curve")[0]
    assert marks(p, "polyline")[0]["points"] == [[0.0, 100.0], [2.0, 130.0]]
    assert p["axes"]["y"]["unit"] == "MiB"
    data = packet("resource_curve")
    data["measurements"][0]["value"] = -1
    with pytest.raises(ValueError):
        panels("resource_curve", data)


def test_repertoire_counts_and_overlap_have_explicit_denominators():
    p = panels("repertoire_abundance")[0]
    assert [r["y"] for r in p["rows"]] == [0.6, 0.4, 0.5, 0.5]
    assert p["rows"][0]["details"]["sample_total"] == 10
    q = panels("repertoire_overlap")[0]
    cross = next(r for r in q["rows"] if r["details"]["sample_a"] == "s1" and r["details"]["sample_b"] == "s2")
    assert cross["details"]["overlap"] == pytest.approx(1 / 3)
    assert cross["details"]["shared_clone_ids"] == ["C1"]
    assert cross["details"]["union_count"] == 3
    data = packet("repertoire_abundance")
    data["sample_totals"]["s1"] = 2
    with pytest.raises(ValueError, match="total"):
        panels("repertoire_abundance", data)


def test_saved_diversity_is_not_recomputed_or_given_invented_intervals():
    p = panels("repertoire_diversity")[0]
    assert [r["y"] for r in p["rows"]] == [0.67, 0.69]
    assert not marks(p, "line")
    data = packet("repertoire_diversity")
    del data["metrics"][0]["estimator"]
    with pytest.raises(ValueError, match="estimator"):
        panels("repertoire_diversity", data)


def test_multimodal_paired_features_and_separate_assay_heatmaps():
    p = panels("multimodal_features")[0]
    assert [(r["x"], r["y"]) for r in p["rows"]] == [(2.0, 10.0), (3.0, 20.0)]
    assert p["axes"]["x"]["unit"] == "log1p RNA counts"
    assert p["axes"]["y"]["unit"] == "protein intensity"
    heatmaps = panels("multimodal_heatmap")
    assert {p["color_scale"]["unit"] for p in heatmaps} == {"log1p RNA counts", "protein intensity"}
    data = packet("multimodal_features")
    data["pairs"][0]["y_cell_id"] = "unpaired"
    with pytest.raises(ValueError, match="pair"):
        panels("multimodal_features", data)
    data = packet("multimodal_heatmap")
    data["assays"][1]["cell_ids"] = ["c2", "c1"]
    with pytest.raises(ValueError, match="pair"):
        panels("multimodal_heatmap", data)


def test_velocity_uses_actual_vectors_and_spliced_unspliced_evidence():
    p = panels("rna_velocity")[0]
    arrow = marks(p, "arrow")[0]
    assert (arrow["x1"], arrow["y1"], arrow["x2"], arrow["y2"]) == (1.0, 2.0, 2.0, 1.5)
    data = packet("rna_velocity")
    del data["cells"][0]["unspliced"]
    with pytest.raises(ValueError, match="unspliced"):
        panels("rna_velocity", data)


def test_spatial_coordinates_units_and_registration_are_preserved():
    p = panels("spatial_expression")[0]
    assert [(r["x"], r["y"]) for r in p["rows"]] == [(10.0, 20.0), (30.0, 40.0)]
    assert p["axes"]["x"]["unit"] == "um"
    assert p["rows"][0]["details"]["registration"]["reference"] == "section-A"
    data = packet("spatial_expression")
    data["coordinate_unit"] = "unknown pixels"
    with pytest.raises(ValueError, match="physical"):
        panels("spatial_expression", data)


def test_splice_tracks_use_saved_intervals_coverage_and_junction_counts():
    cov, junctions, exons = panels("splice_tracks")
    assert marks(cov, "rect")[0]["width"] == 10
    assert marks(cov, "rect")[0]["height"] == 5
    arc = marks(junctions, "arc")[0]
    assert arc["points"][0] == [110.0, 0.0]
    assert arc["points"][-1] == [140.0, 0.0]
    # A cubic with equal interior heights reaches 3/4 of that height at t=.5.
    assert arc["points"][1][1] * 0.75 == pytest.approx(8)
    assert len(marks(exons, "rect")) == 2
    data = packet("splice_tracks")
    data["junctions"][0]["end"] = 100
    with pytest.raises(ValueError, match="interval"):
        panels("splice_tracks", data)


def test_perturbation_pairing_follows_biological_unit_not_observation_order():
    p = panels("perturbation_control")[0]
    assert len(marks(p, "line")) == 2
    lines = marks(p, "line")
    assert {(m["y1"], m["y2"]) for m in lines} == {(1.0, 3.0), (2.0, 2.5)}
    data = packet("perturbation_control")
    data["observations"].append(dict(data["observations"][0], id="technical-replicate"))
    with pytest.raises(ValueError, match="biological"):
        panels("perturbation_control", data)


def test_orthology_keeps_one_to_many_pairs_without_averaging():
    p = panels("orthology_comparison")[0]
    assert [(r["x"], r["y"]) for r in p["rows"][:2]] == [(2.0, 1.0), (2.0, 3.0)]
    assert len(marks(p, "point")) == 2
    assert "not averaged" in " ".join(p["annotations"])
    data = packet("orthology_comparison")
    data["mapping_policy"] = "average"
    with pytest.raises(ValueError, match="retain_all_pairs"):
        panels("orthology_comparison", data)


def test_genomic_and_sva_views_preserve_source_positions_and_associations():
    p = panels("genomic_effect")[0]
    assert [(r["x"], r["y"]) for r in p["rows"]] == [(100.0, -1.5), (200.0, 2.0)]
    assert p["axes"]["x"]["unit"] == "bp"
    q = panels("sva_association")[0]
    assert [m["value"] for m in marks(q, "rect")] == [-0.8, 0.4]
    data = packet("sva_association")
    data["associations"][0]["value"] = 1.1
    with pytest.raises(ValueError, match="correlation"):
        panels("sva_association", data)


@pytest.mark.parametrize("capability,field", [
    ("gsea_running", "running_scores"), ("enrichment_dotplot", "terms"),
    ("temporal_spline", "observations"), ("temporal_model_comparison", "candidates"),
    ("saved_temporal_curve", "points"),
    ("pseudotime_monotonicity", "observations"), ("lineage_schematic", "nodes"),
    ("regulatory_network", "edges"), ("motif_enrichment", "terms"),
    ("resource_curve", "measurements"), ("execution_dag", "nodes"),
    ("repertoire_abundance", "clones"), ("repertoire_overlap", "clones"),
    ("repertoire_diversity", "metrics"), ("multimodal_features", "pairs"),
    ("multimodal_heatmap", "assays"), ("rna_velocity", "cells"),
    ("spatial_expression", "spots"), ("splice_tracks", "coverage"),
    ("perturbation_control", "observations"), ("orthology_comparison", "mappings"),
    ("genomic_effect", "genes"), ("sva_association", "associations"),
])
def test_duplicate_source_entities_are_rejected(capability, field):
    data = packet(capability)
    data[field].append(deepcopy(data[field][0]))
    with pytest.raises(ValueError, match="unique|duplicate"):
        panels(capability, data)


@pytest.mark.parametrize("capability,field,value_field", [
    ("gsea_running", "running_scores", "score"), ("enrichment_dotplot", "terms", "q"),
    ("temporal_spline", "observations", "value"), ("temporal_model_comparison", "candidates", "aic"),
    ("saved_temporal_curve", "points", "value"),
    ("pseudotime_monotonicity", "observations", "pseudotime"),
    ("regulatory_network", "edges", "correlation"), ("motif_enrichment", "terms", "fold_enrichment"),
    ("resource_curve", "measurements", "value"), ("repertoire_abundance", "clones", "count"),
    ("repertoire_diversity", "metrics", "value"), ("multimodal_features", "pairs", "x_value"),
    ("rna_velocity", "cells", "spliced"), ("spatial_expression", "spots", "expression"),
    ("splice_tracks", "coverage", "value"), ("perturbation_control", "observations", "value"),
    ("orthology_comparison", "mappings", None), ("genomic_effect", "genes", "effect"),
    ("sva_association", "associations", "value"),
])
def test_failed_saved_record_retained_without_fabricated_geometry(capability, field, value_field):
    data = packet(capability)
    failed = data[field][0]
    failed.update(status="failed", reason="synthetic recorded failure")
    if value_field:
        failed[value_field] = None
    result = panels(capability, data)
    matches = [(p, r) for p in result for r in p["rows"] if r["id"] == failed["id"]]
    assert matches
    for p, r in matches:
        assert r["x"] is None and r["y"] is None
        assert r["reason"] == "synthetic recorded failure"
        assert not any(r["key"] in m["keys"] for m in p["marks"])


@pytest.mark.parametrize("capability", ["execution_dag", "lineage_schematic"])
def test_graph_failed_node_is_a_failed_inventory_item(capability):
    data = packet(capability)
    data["nodes"][1].update(status="failed", reason="saved node operation failed")
    p = panels(capability, data)[0]
    node = next(r for r in p["rows"] if r["id"] == "b")
    assert node["group"] == "failed"
    assert node["details"]["status"] == "failed"
    assert node["details"]["reason"] == "saved node operation failed"
    assert any("inventory" in s or "topology" in s for s in p["limitations"])


def test_failed_clone_membership_does_not_silently_change_overlap():
    data = packet("repertoire_overlap")
    data["clones"][0].update(count=None, status="failed", reason="clone counting failed")
    p = panels("repertoire_overlap", data)[0]
    affected = [r for r in p["rows"] if r["entity_kind"] == "sample_pair" and "s1" in (r["details"]["sample_a"], r["details"]["sample_b"])]
    assert len(affected) == 3
    assert all(r["x"] is None and "failed" in r["reason"] for r in affected)
    assert len(marks(p, "rect")) == 1


def test_overlap_export_retains_counts_and_empty_union_is_not_zero():
    p = panels("repertoire_overlap")[0]
    source = next(r for r in p["rows"] if r["id"] == "cl1")
    assert source["details"]["count"] == 6
    assert "cl1" in p["rows"][0]["details"]["source_record_ids"]
    data = packet("repertoire_overlap")
    data["clones"] = []
    p = panels("repertoire_overlap", data)[0]
    assert not p["marks"]
    assert all(r["details"]["overlap"] is None and "undefined" in r["reason"] for r in p["rows"])
    data["metric"] = "invented similarity"
    with pytest.raises(ValueError):
        panels("repertoire_overlap", data)


def test_multimodal_missing_values_are_retained_without_imputation():
    data = packet("multimodal_heatmap")
    data["assays"][0]["values"][0][0] = None
    p = panels("multimodal_heatmap", data)[0]
    assert p["rows"][0]["x"] is None and "no imputation" in p["rows"][0]["reason"]
    assert len(marks(p, "rect")) == 1


def test_arbitrary_valid_assay_and_section_labels_do_not_become_invalid_panel_ids():
    data = packet("multimodal_heatmap")
    data["assays"][0]["id"] = "RNA assay / log"
    assert panels("multimodal_heatmap", data)
    data = packet("spatial_expression")
    data["spots"][0]["section_id"] = "Section A / 1"
    assert len(panels("spatial_expression", data)) == 2


def test_negative_nb_interval_and_inconsistent_orthology_are_refused():
    data = packet("temporal_spline")
    data.update(family="negative_binomial", input_scale="raw_counts")
    data["model"].update(offset_method="RLE", dispersion_method="empirical_bayes")
    data["observations"] = [dict(r, value=i + 1) for i, r in enumerate(data["observations"])]
    data["fitted"][0]["lower"] = -1
    with pytest.raises(ValueError, match="interval"):
        panels("temporal_spline", data)
    data = packet("orthology_comparison")
    data["mappings"][0]["relationship"] = "one_to_one"
    with pytest.raises(ValueError, match="multiplicity"):
        panels("orthology_comparison", data)


def test_genomic_position_and_sva_q_constraints():
    data = packet("genomic_effect")
    data["genes"][0]["position"] = 0
    with pytest.raises(ValueError):
        panels("genomic_effect", data)
    data = packet("sva_association")
    data["associations"][0].update(p=0.2, q=0.1)
    with pytest.raises(ValueError, match="q"):
        panels("sva_association", data)


def test_quadratic_expansions_are_bounded_before_allocating_geometry():
    data = packet("repertoire_overlap")
    data["samples"] = [f"s{i}" for i in range(450)]
    data["biological_units"] = {sample: sample for sample in data["samples"]}
    with pytest.raises(ValueError, match="budget"):
        panels("repertoire_overlap", data)
    data = packet("multimodal_heatmap")
    data["paired_ids"] = [f"c{i}" for i in range(450)]
    for assay in data["assays"]:
        assay.update(cell_ids=data["paired_ids"], features=[f"g{i}" for i in range(450)], values=[[0] * 450] * 450)
    with pytest.raises(ValueError, match="budget"):
        panels("multimodal_heatmap", data)


def test_failed_regulatory_evidence_is_retained_even_without_a_successful_channel():
    data = packet("regulatory_network")
    data["edges"][1].update(correlation=None, status="failed", reason="association estimation failed")
    p = panels("regulatory_network", data)[0]
    failed = next(r for r in p["rows"] if r["id"] == "edge2")
    assert failed["reason"] == "association estimation failed"
    assert failed["x"] is None
    assert len(marks(p, "line")) == 2


def test_failed_assay_block_preserves_values_without_successful_marks():
    data = packet("multimodal_heatmap")
    data["assays"][0].update(status="failed", reason="assay QC rejected")
    p = panels("multimodal_heatmap", data)[0]
    assert not p["marks"]
    assert all(r["reason"] == "assay QC rejected" for r in p["rows"])
    assert p["rows"][0]["details"]["value"] == 2


@pytest.mark.parametrize("capability", ["lineage_schematic", "execution_dag"])
def test_failed_graph_edge_is_not_drawn_as_verified_topology(capability):
    data = packet(capability)
    data["edges"][0].update(status="failed", reason="edge inference failed")
    p = panels(capability, data)[0]
    assert len(marks(p, "arrow")) == 1
    failed = next(r for r in p["rows"] if r["id"] == "ab")
    assert failed["reason"] == "edge inference failed" and failed["x"] is None


def test_unrecognized_status_and_out_of_domain_saved_p_are_rejected():
    data = packet("temporal_spline")
    data["fitted"][0]["status"] = "nonconverged_but_plausible"
    with pytest.raises(ValueError, match="status"):
        panels("temporal_spline", data)
    data = packet("sva_association")
    data["associations"][0]["p"] = -0.2
    with pytest.raises(ValueError):
        panels("sva_association", data)


def test_saved_temporal_curve_uses_actual_predictions_and_names_absent_evidence():
    p = panels("saved_temporal_curve")[0]
    assert marks(p, "polyline")[0]["points"] == [[0.0, 0.3], [1.0, 1.0], [2.0, 2.0]]
    assert not marks(p, "polygon")
    assert p["axes"]["x"]["unit"] == "numeric_time:age (unit not retained)"
    assert p["rows"][0]["details"]["model"]["selected_df"] == 3
    text = " ".join([*p["annotations"], *p["limitations"]]).lower()
    assert "observations" in text and "unavailable" in text and "knots" in text and "uncertainty" in text
    assert "no new smoothing" in text


def test_saved_curve_refuses_fabricated_ci_fields_and_breaks_at_failed_predictions():
    data = packet("saved_temporal_curve")
    data["points"][1].update(value=None, status="failed", reason="prediction failed")
    p = panels("saved_temporal_curve", data)[0]
    assert not marks(p, "polyline")
    assert p["rows"][1]["reason"] == "prediction failed"
    data = packet("saved_temporal_curve")
    data["interval"] = {"level": 0.95, "kind": "confidence"}
    with pytest.raises(ValueError):
        panels("saved_temporal_curve", data)
    data = packet("saved_temporal_curve")
    data["points"][1]["time"] = 0
    with pytest.raises(ValueError, match="unique"):
        panels("saved_temporal_curve", data)


def ridge_network():
    return {"tf_ids": ["TF1"], "target_ids": ["G1", "G2"],
            "edges": [{"id": "ridge1", "tf": "TF1", "target": "G1", "association_type": "ridge_association",
                       "coefficient": 2.5, "coefficient_definition": "standardized_ridge_coefficient",
                       "association_method": "lagged time-mean multivariable ridge association", "score": 2.5, "sign": 1,
                       "ridge_parameters": {"alpha": 1.0, "lag_observed_intervals": 1, "transform": "log1p"}},
                      {"id": "ridge2", "tf": "TF1", "target": "G2", "association_type": "ridge_association",
                       "coefficient": -0.4, "coefficient_definition": "standardized_ridge_coefficient",
                       "association_method": "lagged time-mean multivariable ridge association", "score": 0.4, "sign": -1}],
            "source_label": "synthetic standardized ridge geometry fixture"}


def test_regulatory_ridge_coefficients_are_not_mislabeled_as_correlations():
    p = panels("regulatory_network", ridge_network())[0]
    edge = next(r for r in p["rows"] if r["id"] == "ridge1")
    assert edge["details"]["coefficient"] == 2.5  # ridge coefficients are not bounded by 1
    assert edge["details"]["association_type"] == "ridge_association"
    assert edge["details"]["curated_membership"] is None
    assert edge["details"]["motif_hit"] is None
    assert "correlation" not in edge["details"]
    assert len(marks(p, "line")) == 2 and not marks(p, "arrow")
    assert all("ridge" in m["group"] for m in marks(p, "line"))
    text = " ".join([*p["annotations"], *p["limitations"]])
    assert "standardized ridge" in text and "not a probability" in text
    assert "unassessed" in text


@pytest.mark.parametrize("mutation", ["correlation", "definition", "method", "score", "alpha", "sign"])
def test_regulatory_ridge_refuses_ambiguous_or_inconsistent_coefficient_evidence(mutation):
    data = ridge_network()
    edge = data["edges"][0]
    if mutation == "correlation":
        edge["correlation"] = 0.2
    elif mutation == "definition":
        edge["coefficient_definition"] = "probability"
    elif mutation == "method":
        del edge["association_method"]
    elif mutation == "score":
        edge["score"] = 0.9
    elif mutation == "alpha":
        edge["ridge_parameters"]["alpha"] = -1
    else:
        edge["sign"] = -1
    with pytest.raises(ValueError):
        panels("regulatory_network", data)


def test_empty_regulon_reference_is_retained_as_explicitly_unavailable():
    data = packet("enrichment_dotplot")
    data["method"] = "regulon"
    data["terms"][0].update(set_size=0, overlap_count=0, q=1, status="unavailable", reason="no reference genes in measured background")
    data["terms"][0].update(set_member_ids=[], overlap_member_ids=[])
    p = panels("enrichment_dotplot", data)[0]
    assert p["rows"][0]["x"] is None
    assert p["rows"][0]["details"]["set_size"] == 0
    assert p["rows"][0]["reason"] == "no reference genes in measured background"
    assert len(marks(p, "point")) == 1


def test_temporal_missing_interval_preserves_saved_mean_line_and_points():
    data = packet("temporal_spline")
    data["fitted"][0].update(lower=None, upper=None, reason="interval unavailable; prediction retained")
    p = panels("temporal_spline", data)[0]
    fit = next(r for r in p["rows"] if r["id"] == "fit0")
    assert (fit["x"], fit["y"], fit["status"]) == (0.0, 0.3, "recorded")
    assert fit["details"]["interval_evidence"]["status"] == "unavailable"
    assert "interval unavailable" in fit["details"]["interval_evidence"]["reason"]
    assert marks(p, "polyline")[0]["points"] == [[0.0, 0.3], [1.0, 1.0], [2.0, 2.0]]
    assert any(m["keys"] == [fit["key"]] for m in marks(p, "point"))
    assert all(fit["key"] not in m["keys"] for m in marks(p, "polygon"))


def test_temporal_interval_method_scope_and_conditional_metadata_are_saved_not_inferred():
    data = packet("temporal_spline")
    data["interval"].update(method="saved parametric bootstrap", scope="pointwise conditional on the selected df", selection_conditional=True)
    p = panels("temporal_spline", data)[0]
    fit = next(r for r in p["rows"] if r["id"] == "fit0")
    evidence = fit["details"]["interval_evidence"]
    assert evidence["method"] == "saved parametric bootstrap"
    assert evidence["scope"] == "pointwise conditional on the selected df"
    assert evidence["selection_conditional"] is True
    labels = " ".join(p["annotations"])
    assert evidence["method"] in labels and evidence["scope"] in labels and "selection_conditional=true" in labels
    data["interval"]["selection_conditional"] = False
    p = panels("temporal_spline", data)[0]
    assert next(r for r in p["rows"] if r["id"] == "fit0")["details"]["interval_evidence"]["selection_conditional"] is False
    assert "selection_conditional=false" in " ".join(p["annotations"])
    for key in ("method", "scope", "selection_conditional"):
        data["interval"].pop(key)
    p = panels("temporal_spline", data)[0]
    evidence = next(r for r in p["rows"] if r["id"] == "fit0")["details"]["interval_evidence"]
    assert evidence["method"] is None and evidence["scope"] is None and evidence["selection_conditional"] is None
    assert "method=unrecorded" in " ".join(p["annotations"])
    assert "scope=unrecorded" in " ".join(p["annotations"])
    assert "selection_conditional=unrecorded" in " ".join(p["annotations"])
    data["interval"]["selection_conditional"] = "true"
    with pytest.raises(ValueError):
        panels("temporal_spline", data)


def test_temporal_interval_absence_and_failed_mean_have_independent_geometry():
    data = packet("temporal_spline")
    del data["interval"]
    del data["fitted"][0]["lower"]
    del data["fitted"][0]["upper"]
    p = panels("temporal_spline", data)[0]
    assert len(marks(p, "polyline")) == 1 and not marks(p, "polygon")
    assert "interval" in " ".join(p["annotations"]).lower()
    data = packet("temporal_spline")
    data["fitted"][1].update(value=None, status="failed", reason="fit failed")
    p = panels("temporal_spline", data)[0]
    fit_points = [m for m in marks(p, "point") if m["keys"][0].startswith("fit:")]
    assert {(m["x"], m["y"]) for m in fit_points} == {(0.0, 0.3), (2.0, 2.0)}
    assert not marks(p, "polyline")


@pytest.mark.parametrize("metric,domain", [("pearson_r", [-1, 1]), ("spearman_r", [-1, 1]), ("r_squared", [0, 1])])
def test_sva_natural_metric_domain_is_not_rescaled_to_observed_extrema(metric, domain):
    data = packet("sva_association")
    data["association_metric"] = metric
    for record in data["associations"]:
        record["value"] = 0.2
    assert panels("sva_association", data)[0]["color_scale"]["domain"] == domain


def test_sva_saved_effect_accepts_explicit_symmetric_display_domain():
    data = packet("sva_association")
    data.update(association_metric="saved_effect", unit="standardized coefficient", color_domain=[-2, 2])
    assert panels("sva_association", data)[0]["color_scale"]["domain"] == [-2, 2]
    data["color_domain"] = [-0.1, 0.1]
    with pytest.raises(ValueError):
        panels("sva_association", data)


def test_saved_curve_preserves_failed_gene_without_inventing_a_prediction():
    data = packet("saved_temporal_curve")
    data["points"] = []
    data["source_records"] = [{"id": "G1", "status": "failed", "reason": "no finite prediction was saved"}]
    p = panels("saved_temporal_curve", data)[0]
    assert not p["marks"]
    assert p["rows"][0]["x"] is None and p["rows"][0]["y"] is None
    assert p["rows"][0]["reason"] == "no finite prediction was saved"
    data["source_records"] = []
    with pytest.raises(ValueError):
        panels("saved_temporal_curve", data)


def test_lineage_retains_node_statistics_and_sample_branch_membership():
    data = packet("lineage_schematic")
    data["nodes"][0].update(sample_count=2, mean_pseudotime=0.2)
    data["membership"] = {"root_id": "a", "root_sample_id": "s1", "pseudotime_unit": "inferred ordering units",
                          "samples": [{"id": "s1", "pseudotime": 0.1, "branch_id": "root"},
                                      {"id": "s2", "pseudotime": 0.3, "branch_id": "branch_1"}]}
    p = panels("lineage_schematic", data)[0]
    node = next(r for r in p["rows"] if r["id"] == "a")
    assert node["details"]["sample_count"] == 2 and node["details"]["mean_pseudotime"] == 0.2
    sample = next(r for r in p["rows"] if r["id"] == "s2")
    assert sample["details"]["branch_id"] == "branch_1"
    assert sample["details"]["pseudotime"] == 0.3
    assert sample["x"] is None and sample["link"] == {"kind": "sample", "id": "s2"}
    data["membership"]["root_sample_id"] = "foreign sample"
    with pytest.raises(ValueError):
        panels("lineage_schematic", data)


def test_candidate_selection_is_saved_identity_not_minimum_criterion():
    data = packet("temporal_model_comparison")
    data["selected_candidate_id"] = "df2"  # AIC(df3) is smaller; do not infer selection.
    data["selected_df"] = 2
    for p in panels("temporal_model_comparison", data):
        selected = next(r for r in p["rows"] if r["id"] == "df2")
        assert selected["details"]["selected"] is True
        assert any(m.get("shape") == "diamond" and m["keys"] == [selected["key"]] for m in p["marks"])
        assert "df2" in " ".join(p["annotations"])


def test_enrichment_overlap_lower_bound_is_enforced():
    data = packet("enrichment_dotplot")
    data["universe"] = {"id": "small", "size": 10}
    data["terms"] = [{"id": "impossible", "label": "Impossible", "query_size": 8, "set_size": 8,
                      "overlap_count": 1, "q": 0.5, "direction": "none"}]
    data.pop("coverage", None)
    with pytest.raises(ValueError, match="overlap"):
        panels("enrichment_dotplot", data)


def enrichment_membership_packet():
    data = packet("enrichment_dotplot")
    data["universe"] = {"id": "synthetic-background", "size": 6, "unit": "gene", "member_ids": list("abcdef"),
                        "membership_complete": True, "membership_source_id": "synthetic-background-v1"}
    data["provenance"] = {"species": "synthetic organism", "database": {"name": "Synthetic gene sets", "version": "v1"},
                          "background_definition": "six synthetic measured genes", "reference_sha256": "a" * 64,
                          "evidence_type": "curated gene-set membership", "fdr_method": "BH",
                          "fdr_scope": "all supplied tested sets before output truncation"}
    data["coverage"] = {"tested_sets": 5, "retained_sets": 2, "omitted_sets": 3, "truncated": True}
    for record, members, overlap in zip(data["terms"], [list("acd"), list("bef")], [list("ac"), ["b"]]):
        record.update(query_size=3, set_size=3, overlap_count=len(overlap), query_member_ids=list("abc"),
                      set_member_ids=members, overlap_member_ids=overlap, membership_complete=True,
                      membership_source_id="synthetic-set-reference-v1", member_unit="gene")
    return data


def motif_membership_packet():
    data = packet("motif_enrichment")
    data["universe"] = {"id": "synthetic-promoters", "size": 4, "unit": "promoter", "member_ids": ["p1", "p2", "p3", "p4"],
                        "membership_complete": True, "membership_source_id": "synthetic-promoter-universe"}
    data["provenance"] = {"species": "synthetic organism", "database": {"name": "Synthetic motifs", "version": "v2"},
                          "background_definition": "four saved synthetic promoters", "promoter_definition": "saved synthetic genomic intervals",
                          "promoter_source_id": "synthetic-promoter-intervals", "evidence_type": "sequence motif hits"}
    data["coverage"] = {"tested_sets": 3, "retained_sets": 1, "omitted_sets": 2, "truncated": True}
    data["terms"][0].update(foreground_size=2, background_size=4, foreground_hits=1, background_hits=2, fold_enrichment=1,
                            foreground_member_ids=["p1", "p2"], background_member_ids=["p1", "p2", "p3", "p4"],
                            foreground_hit_ids=["p1"], background_hit_ids=["p1", "p3"], membership_complete=True,
                            membership_source_id="synthetic-motif-hit-table", member_unit="promoter")
    data["promoters"] = [{"id": "p1", "gene_id": "G1", "chromosome": "chr1", "start": 100, "end": 200,
                          "strand": "+", "genome_build": "synthetic-build", "coordinate_system": "0-based-half-open",
                          "source_id": "synthetic-promoter-intervals"}]
    return data


def test_enrichment_preserves_species_database_membership_and_upstream_omissions():
    p = panels("enrichment_dotplot", enrichment_membership_packet())[0]
    details = p["rows"][0]["details"]
    assert details["provenance"]["species"] == "synthetic organism"
    assert details["provenance"]["database"]["version"] == "v1"
    background = next(r for r in p["rows"] if r["key"] == details["universe"]["member_ids_source_key"])
    assert background["details"]["member_ids"] == list("abcdef")
    assert details["overlap_member_ids"] == list("ac")
    assert details["membership_evidence"]["set_member_ids"] == "complete"
    assert details["source_coverage"] == {"tested_sets": 5, "retained_sets": 2, "omitted_sets": 3, "truncated": True}
    text = " ".join([*p["annotations"], *p["limitations"], *p["y_categories"]])
    assert "tested=5" in text and "retained=2" in text and "omitted=3" in text
    assert "omitted sets cannot be shown" in text
    assert "v1" in text and "synthetic organism" in text and "2/3" in text


@pytest.mark.parametrize("mutation", ["wrong_overlap", "wrong_length", "foreign_member", "wrong_unit", "coverage", "false_complete"])
def test_enrichment_memberships_and_source_coverage_cannot_contradict_counts(mutation):
    data = enrichment_membership_packet()
    if mutation == "wrong_overlap":
        data["terms"][0]["overlap_member_ids"] = ["a", "b"]
    elif mutation == "wrong_length":
        data["universe"]["member_ids"] = ["a", "b"]
    elif mutation == "foreign_member":
        data["terms"][0]["set_member_ids"] = ["a", "c", "foreign"]
    elif mutation == "wrong_unit":
        data["terms"][0]["member_unit"] = "promoter"
    elif mutation == "coverage":
        data["coverage"]["retained_sets"] = 1
    else:
        data["coverage"]["truncated"] = False
    with pytest.raises(ValueError):
        panels("enrichment_dotplot", data)


def test_unprovided_enrichment_metadata_is_explicitly_unassessed():
    data = packet("enrichment_dotplot")
    data.pop("provenance", None)
    data.pop("coverage", None)
    data["universe"] = {"id": data["universe"]["id"], "size": data["universe"]["size"]}
    for record in data["terms"]:
        for key in ("query_member_ids", "set_member_ids", "overlap_member_ids", "membership_complete", "membership_source_id", "member_unit"):
            record.pop(key, None)
    p = panels("enrichment_dotplot", data)[0]
    details = p["rows"][0]["details"]
    assert details["provenance"]["species"] is None
    assert details["provenance"]["database"]["version"] is None
    background = next(r for r in p["rows"] if r["key"] == details["universe"]["member_ids_source_key"])
    assert background["details"]["member_ids"] is None
    assert details["source_coverage"]["tested_sets"] is None
    assert "unassessed" in " ".join(p["annotations"])


def test_motif_preserves_exact_hits_and_promoter_coordinates_with_units():
    p = panels("motif_enrichment", motif_membership_packet())[0]
    motif = next(r for r in p["rows"] if r["id"] == "motif1")
    assert motif["details"]["foreground_hit_ids"] == ["p1"]
    assert motif["details"]["background_hit_ids"] == ["p1", "p3"]
    assert motif["details"]["source_coverage"]["omitted_sets"] == 2
    promoter = next(r for r in p["rows"] if r["entity_kind"] == "promoter")
    assert promoter["details"]["start"] == 100 and promoter["details"]["coordinate_unit"] == "bp"
    assert promoter["details"]["strand"] == "+" and promoter["details"]["genome_build"] == "synthetic-build"
    text = " ".join([*p["annotations"], *p["limitations"], *p["y_categories"]])
    assert "foreground 1/2" in text and "background 2/4" in text and "v2" in text
    assert "binding" in text and "omitted sets cannot be shown" in text


def test_motif_hit_membership_must_agree_with_foreground_and_background():
    data = motif_membership_packet()
    data["terms"][0]["foreground_hit_ids"] = ["p3"]
    with pytest.raises(ValueError):
        panels("motif_enrichment", data)
    data = motif_membership_packet()
    data["promoters"][0]["end"] = 50
    with pytest.raises(ValueError):
        panels("motif_enrichment", data)


def test_partial_membership_is_labeled_and_never_promoted_to_complete():
    data = enrichment_membership_packet()
    data["universe"].update(member_ids=["a", "b"], membership_complete=False)
    data["terms"][0].update(query_member_ids=["a"], set_member_ids=["a"], overlap_member_ids=["a"], membership_complete=False)
    p = panels("enrichment_dotplot", data)[0]
    assert p["rows"][0]["details"]["membership_evidence"]["set_member_ids"] == "partial"
    assert p["rows"][0]["details"]["universe"]["membership_complete"] is False
    assert "partial" in " ".join(p["annotations"])


def test_saved_candidate_df_criterion_and_provenance_are_retained_without_reselection():
    data = packet("temporal_model_comparison")
    data.pop("selected_candidate_id", None)
    data.update(selected_df=2, selection_criterion="BIC", selection_provenance={"source_id": "saved-selection-1",
                "method": "upstream selection", "version": "v1", "description": "saved chosen degree"})
    p = panels("temporal_model_comparison", data)[0]
    chosen = next(r for r in p["rows"] if r["id"] == "df2")
    assert chosen["details"]["selected"] is True
    assert chosen["details"]["selection_evidence"]["selected_df"] == 2
    assert chosen["details"]["selection_evidence"]["criterion"] == "BIC"
    assert chosen["details"]["selection_evidence"]["provenance"]["source_id"] == "saved-selection-1"
    assert "BIC" in " ".join(p["annotations"])
    data["selected_candidate_id"] = "df3"
    with pytest.raises(ValueError):
        panels("temporal_model_comparison", data)


def test_selected_df_does_not_invent_candidate_identity_when_ambiguous():
    data = packet("temporal_model_comparison")
    data.pop("selected_candidate_id", None)
    data["candidates"].append(dict(data["candidates"][0], id="different-fit-with-df2"))
    data["selected_df"] = 2
    p = panels("temporal_model_comparison", data)[0]
    matches = [r for r in p["rows"] if r["details"]["selected_df_match"]]
    assert len(matches) == 2 and all(r["details"]["selected"] is None for r in matches)
    assert "ambiguous" in " ".join(p["annotations"]).lower()


def test_orthology_mapping_version_namespaces_and_policy_are_inspectable():
    data = packet("orthology_comparison")
    data["mapping_provenance"] = {"source_id": "synthetic-orthology-table", "mapping_version": "release-7",
                                  "source_namespace": "species-A stable gene IDs", "target_namespace": "species-B stable gene IDs",
                                  "method": "saved curated orthology", "source_genome_build": "build-A", "target_genome_build": "build-B"}
    p = panels("orthology_comparison", data)[0]
    details = p["rows"][0]["details"]
    assert details["mapping_provenance"] == data["mapping_provenance"]
    assert details["mapping_policy"] == "retain_all_pairs"
    assert "release-7" in " ".join(p["annotations"])
    assert [(r["x"], r["y"]) for r in p["rows"][:2]] == [(2.0, 1.0), (2.0, 3.0)]


def test_orthology_missing_versions_and_namespaces_remain_unassessed():
    data = packet("orthology_comparison")
    data.pop("mapping_provenance", None)
    p = panels("orthology_comparison", data)[0]
    meta = p["rows"][0]["details"]["mapping_provenance"]
    assert meta["mapping_version"] is None and meta["source_namespace"] is None and meta["target_namespace"] is None
    assert "unassessed" in " ".join(p["annotations"])


def test_splice_strand_and_sample_biological_replicate_metadata_are_preserved():
    data = packet("splice_tracks")
    data["track_metadata"] = {"sample1": {"sample_id": "s1", "biological_unit_id": "donor1", "replicate_id": "rep1",
                                           "replicate_kind": "biological", "strand": "+", "source_id": "saved-library-metadata"}}
    for exon in data["exons"]:
        exon.update(strand="-", gene_id="G1")
    cov, junction, exons = panels("splice_tracks", data)
    for p in (cov, junction):
        assert p["rows"][0]["details"]["track_metadata"] == data["track_metadata"]["sample1"]
        assert p["rows"][0]["link"] == {"kind": "sample", "id": "s1"}
        assert "donor1" in " ".join(p["annotations"]) and "strand=+" in " ".join(p["annotations"])
    assert all(r["details"]["strand"] == "-" for r in exons["rows"])
    arrow = marks(exons, "arrow")[0]
    assert arrow["x1"] > arrow["x2"]
    data["coverage"][0]["sample_id"] = "conflicting-sample"
    with pytest.raises(ValueError):
        panels("splice_tracks", data)


def test_splice_missing_track_identity_is_not_inferred_from_track_label():
    data = packet("splice_tracks")
    data.pop("track_metadata", None)
    cov, junction, _ = panels("splice_tracks", data)
    for p in (cov, junction):
        metadata = p["rows"][0]["details"]["track_metadata"]
        assert metadata["sample_id"] is None and metadata["biological_unit_id"] is None and metadata["strand"] is None
        assert "unassessed" in " ".join(p["annotations"])


def test_unknown_completeness_retains_ids_but_still_rejects_impossible_membership():
    data = enrichment_membership_packet()
    data["universe"].pop("membership_complete")
    for record in data["terms"]:
        record.pop("membership_complete")
    p = panels("enrichment_dotplot", data)[0]
    assert p["rows"][0]["details"]["membership_evidence"]["set_member_ids"] == "unassessed"
    assert p["rows"][0]["details"]["set_member_ids"] == list("acd")
    data["terms"][0]["overlap_member_ids"] = ["a", "b"]
    with pytest.raises(ValueError):
        panels("enrichment_dotplot", data)


def test_large_tested_background_is_retained_once_without_per_term_duplication():
    data = packet("enrichment_dotplot")
    data["universe"].update(size=30000, member_ids=[f"G{i}" for i in range(1, 30001)])
    data["provenance"]["background_definition"] = "30000 synthetic measured genes"
    p = panels("enrichment_dotplot", data)[0]
    background = [r for r in p["rows"] if r["entity_kind"] == "universe"]
    assert len(background) == 1 and len(background[0]["details"]["member_ids"]) == 30000
    for record in p["rows"]:
        if record["entity_kind"] == "gene_set":
            assert "member_ids" not in record["details"]["universe"]
            assert record["details"]["universe"]["member_ids_source_key"] == background[0]["key"]


def test_gsea_preserves_reference_version_and_tested_gene_membership():
    data = packet("gsea_running")
    data["universe"].update(unit="gene", member_ids=["G1", "G2", "G3", "G4"], membership_complete=True,
                            membership_source_id="synthetic-ranked-gene-source")
    data["provenance"] = {"species": "synthetic organism", "database": {"name": "Synthetic pathways", "version": "v3"},
                          "evidence_type": "ranked gene-set enrichment", "background_definition": "four saved ranked genes"}
    curve, barcode = panels("gsea_running", data)
    for p in (curve, barcode):
        background = next(r for r in p["rows"] if r["entity_kind"] == "universe")
        assert background["details"]["member_ids"] == ["G1", "G2", "G3", "G4"]
        assert background["details"]["provenance"]["database"]["version"] == "v3"
        assert "v3" in " ".join(p["annotations"])
    data["universe"]["member_ids"][-1] = "different-gene"
    with pytest.raises(ValueError):
        panels("gsea_running", data)


def fixture_packets() -> list[dict]:
    """Bounded synthetic geometry packets for renderer/export verification only."""
    clones = [
        {"id": "cl1", "sample_id": "s1", "clone_id": "C1", "count": 6},
        {"id": "cl2", "sample_id": "s1", "clone_id": "C2", "count": 4},
        {"id": "cl3", "sample_id": "s2", "clone_id": "C1", "count": 5},
        {"id": "cl4", "sample_id": "s2", "clone_id": "C3", "count": 5},
    ]
    graph_nodes = [{"id": "a", "label": "Input", "status": "complete"},
                   {"id": "b", "label": "Process", "status": "complete"},
                   {"id": "c", "label": "Output", "status": "complete"}]
    graph_edges = [{"id": "ab", "source": "a", "target": "b", "evidence": "inferred"},
                   {"id": "bc", "source": "b", "target": "c", "evidence": "inferred"}]
    x_assay = {"id": "RNA", "feature": "G1", "unit": "log1p RNA counts", "transform": "log1p"}
    y_assay = {"id": "protein", "feature": "P1", "unit": "protein intensity", "transform": "identity"}
    data = {
        "gsea_running": {
            "pathway_id": "synthetic pathway", "ranked_count": 4,
            "universe": {"id": "synthetic-tested-genes", "size": 4}, "ranking_metric": "saved signed statistic",
            "running_scores": [{"id": f"r{i}", "rank": i, "score": value, "gene_id": f"G{i}"}
                               for i, value in enumerate([0.4, 0.1, 0.6, 0.0], 1)],
            "hits": [{"id": "h1", "rank": 1, "gene_id": "G1"}, {"id": "h3", "rank": 3, "gene_id": "G3"}],
        },
        "enrichment_dotplot": {
            "method": "ORA", "x_metric": "GeneRatio", "universe": {"id": "synthetic-tested-genes", "size": 100},
            "terms": [{"id": "set1", "label": "Response", "overlap_count": 2, "query_size": 10, "set_size": 20, "q": 0.1, "direction": "up"},
                      {"id": "set2", "label": "Metabolism", "overlap_count": 4, "query_size": 10, "set_size": 30, "q": 0.01, "direction": "down"}],
        },
        "temporal_spline": {
            "gene_id": "G1", "family": "gaussian", "input_scale": "normalized", "expression_unit": "normalized expression", "time_unit": "days",
            "observations": [{"id": f"obs{i}", "biological_unit_id": f"donor{i}", "time": i, "value": value}
                             for i, value in enumerate([0.2, 1.2, 2.2])],
            "fitted": [{"id": "fit0", "time": 0, "value": 0.3, "lower": 0.1, "upper": 0.5},
                       {"id": "fit1", "time": 1, "value": 1, "lower": 0.5, "upper": 1.5},
                       {"id": "fit2", "time": 2, "value": 2, "lower": 1, "upper": 3}],
            "model": {"df": 3, "knots": [1], "selection": "candidate_selected"}, "interval": {"kind": "confidence", "level": 0.9},
        },
        "temporal_model_comparison": {
            "criteria": ["AIC", "BIC", "GCV"], "comparison_set_id": "synthetic-fixed-response",
            "comparison_basis": {"data_id": "synthetic-3-observations", "response": "normalized G1", "likelihood": "Gaussian with common scale"},
            "candidates": [{"id": "df2", "df": 2, "comparison_set_id": "synthetic-fixed-response", "aic": 12, "bic": 14, "gcv": 0.4},
                           {"id": "df3", "df": 3, "comparison_set_id": "synthetic-fixed-response", "aic": 10, "bic": 13, "gcv": 0.3},
                           {"id": "df4", "df": 4, "comparison_set_id": "synthetic-fixed-response", "status": "failed", "reason": "nonconvergence"}],
        },
        "saved_temporal_curve": {
            "gene_id": "G1", "time_unit": "numeric_time:age (unit not retained)", "value_unit": "expression units",
            "prediction_definition": "saved Gaussian conditional mean predictions",
            "points": [{"id": "saved0", "time": 0, "value": 0.3},
                       {"id": "saved1", "time": 1, "value": 1}, {"id": "saved2", "time": 2, "value": 2}],
            "model": {"family": "gaussian", "selected_df": 3, "formula": "expression ~ bs(age, df=3)",
                      "statistical_method_version": "synthetic", "effect_scale": "expression_units"},
        },
        "pseudotime_monotonicity": {
            "age_unit": "days", "pseudotime_unit": "dimensionless", "ordering_method": "saved synthetic ordering",
            "observations": [{"id": "c1", "collection_age": 1, "pseudotime": 0.2, "biological_unit_id": "d1"},
                             {"id": "c2", "collection_age": 2, "pseudotime": 0.8, "biological_unit_id": "d2"}],
        },
        "lineage_schematic": {"nodes": graph_nodes, "edges": graph_edges, "method": "saved synthetic topology"},
        "regulatory_network": {
            "tf_ids": ["TF1"], "target_ids": ["G1", "G2", "G3"],
            "edges": [{"id": "edge1", "tf": "TF1", "target": "G1", "correlation": -0.8, "correlation_method": "Pearson",
                       "curated_membership": True, "curated_source": "synthetic curated record", "motif_hit": True, "motif_source": "synthetic sequence hit"},
                      {"id": "edge2", "tf": "TF1", "target": "G2", "correlation": 0.3, "correlation_method": "Pearson",
                       "curated_membership": False, "motif_hit": False},
                      {"id": "edge3", "tf": "TF1", "target": "G3", "association_type": "ridge_association",
                       "coefficient": 2.5, "coefficient_definition": "standardized_ridge_coefficient", "score": 2.5, "sign": 1,
                       "association_method": "lagged time-mean multivariable ridge association",
                       "ridge_parameters": {"alpha": 1.0, "lag_observed_intervals": 1, "transform": "log1p"}}],
        },
        "motif_enrichment": {
            "universe": {"id": "synthetic-tested-promoters", "size": 100},
            "terms": [{"id": "motif1", "label": "Motif A", "foreground_hits": 4, "foreground_size": 10,
                       "background_hits": 20, "background_size": 100, "fold_enrichment": 2, "q": 0.02}],
        },
        "resource_curve": {
            "metric": "Resident memory", "time_unit": "seconds", "value_unit": "MiB",
            "measurements": [{"id": "m1", "time": 0, "value": 100, "series": "worker"},
                             {"id": "m2", "time": 2, "value": 130, "series": "worker"}],
        },
        "execution_dag": {"nodes": graph_nodes, "edges": graph_edges, "method": "saved execution dependencies"},
        "repertoire_abundance": {"clones": clones, "sample_totals": {"s1": 10, "s2": 10},
                                 "biological_units": {"s1": "donor1", "s2": "donor2"}, "clone_definition": "paired CDR3 nucleotide identity", "metric": "fraction"},
        "repertoire_overlap": {"clones": clones, "samples": ["s1", "s2"],
                               "biological_units": {"s1": "donor1", "s2": "donor2"}, "clone_definition": "paired CDR3 nucleotide identity", "metric": "jaccard"},
        "repertoire_diversity": {
            "metric_name": "Shannon entropy", "unit": "nats",
            "metrics": [{"id": "div1", "sample_id": "s1", "biological_unit_id": "d1", "value": 0.67, "estimator": "saved plug-in Shannon"},
                        {"id": "div2", "sample_id": "s2", "biological_unit_id": "d2", "value": 0.69, "estimator": "saved plug-in Shannon"}],
        },
        "multimodal_features": {
            "x_assay": x_assay, "y_assay": y_assay, "paired_by": "cell_id",
            "pairs": [{"id": "p1", "cell_id": "c1", "x_cell_id": "c1", "y_cell_id": "c1", "x_value": 2, "y_value": 10, "biological_unit_id": "d1"},
                      {"id": "p2", "cell_id": "c2", "x_cell_id": "c2", "y_cell_id": "c2", "x_value": 3, "y_value": 20, "biological_unit_id": "d1"}],
        },
        "multimodal_heatmap": {
            "paired_ids": ["c1", "c2"], "paired_by": "cell_id",
            "assays": [{"id": "RNA", "unit": "log1p RNA counts", "transform": "log1p", "features": ["G1"], "values": [[2, 3]], "cell_ids": ["c1", "c2"]},
                       {"id": "protein", "unit": "protein intensity", "transform": "identity", "features": ["P1"], "values": [[10, 20]], "cell_ids": ["c1", "c2"]}],
        },
        "rna_velocity": {
            "embedding_unit": "embedding units", "vector_unit": "embedding units/latent time", "time_unit": "latent time", "time_step": 2,
            "projection_method": "saved fitted velocity projection", "count_unit": "counts",
            "cells": [{"id": "c1", "x": 1, "y": 2, "spliced": 10, "unspliced": 3, "velocity_x": 0.5, "velocity_y": -0.25},
                      {"id": "c2", "x": 3, "y": 4, "spliced": 20, "unspliced": 4, "velocity_x": 0.2, "velocity_y": 0.3}],
        },
        "spatial_expression": {
            "feature_id": "G1", "coordinate_unit": "um", "expression_unit": "log1p counts",
            "registration": {"coordinate_system": "physical section", "method": "saved calibrated coordinates", "reference": "section-A", "registered": False},
            "spots": [{"id": "spot1", "x": 10, "y": 20, "expression": 2, "section_id": "A"},
                      {"id": "spot2", "x": 30, "y": 40, "expression": 4, "section_id": "A"}],
        },
        "splice_tracks": {
            "chromosome": "chr1", "genome_build": "synthetic-reference", "coordinate_system": "0-based-half-open", "coverage_unit": "read depth",
            "coverage": [{"id": "cov1", "start": 100, "end": 110, "value": 5, "track": "sample1"},
                         {"id": "cov2", "start": 140, "end": 150, "value": 7, "track": "sample1"}],
            "junctions": [{"id": "j1", "start": 110, "end": 140, "count": 8, "track": "sample1"}],
            "exons": [{"id": "e1", "start": 100, "end": 110, "transcript_id": "tx1"},
                      {"id": "e2", "start": 140, "end": 150, "transcript_id": "tx1"}],
        },
        "perturbation_control": {
            "conditions": ["control", "perturbation"], "expression_unit": "normalized count", "feature_id": "G1", "design": "paired",
            "observations": [{"id": "p1c", "biological_unit_id": "d1", "condition": "control", "value": 1},
                             {"id": "p2p", "biological_unit_id": "d2", "condition": "perturbation", "value": 2.5},
                             {"id": "p1p", "biological_unit_id": "d1", "condition": "perturbation", "value": 3},
                             {"id": "p2c", "biological_unit_id": "d2", "condition": "control", "value": 2}],
        },
        "orthology_comparison": {
            "source_species": "species-A", "target_species": "species-B", "effect_unit": "log2 fold change", "mapping_policy": "retain_all_pairs",
            "mappings": [{"id": "o1", "source_gene": "A1", "target_gene": "B1", "relationship": "one_to_many", "evidence": "synthetic orthology table"},
                         {"id": "o2", "source_gene": "A1", "target_gene": "B2", "relationship": "one_to_many", "evidence": "synthetic orthology table"}],
            "values": [{"id": "vA1", "species": "species-A", "gene_id": "A1", "value": 2},
                       {"id": "vB1", "species": "species-B", "gene_id": "B1", "value": 1},
                       {"id": "vB2", "species": "species-B", "gene_id": "B2", "value": 3}],
        },
        "genomic_effect": {
            "genome_build": "synthetic-reference", "coordinate_system": "1-based-inclusive", "effect_unit": "log2 fold change",
            "genes": [{"id": "ge1", "gene_id": "G1", "chromosome": "chr1", "position": 100, "effect": -1.5},
                      {"id": "ge2", "gene_id": "G2", "chromosome": "chr1", "position": 200, "effect": 2}],
        },
        "sva_association": {
            "association_metric": "pearson_r", "unit": "Pearson r",
            "associations": [{"id": "sv1a", "surrogate": "SV1", "covariate": "batch", "value": -0.8, "method": "saved Pearson association"},
                             {"id": "sv1b", "surrogate": "SV1", "covariate": "condition", "value": 0.4, "method": "saved Pearson association"}],
        },
    }
    data["gsea_running"]["universe"].update(unit="gene", member_ids=["G1", "G2", "G3", "G4"], membership_complete=True,
                                            membership_source_id="synthetic-ranked-gene-source")
    data["gsea_running"]["provenance"] = {"species": "synthetic organism", "database": {"name": "Synthetic pathways", "version": "v3"},
                                          "evidence_type": "ranked gene-set enrichment", "background_definition": "four saved ranked genes"}
    enrichment = data["enrichment_dotplot"]
    enrichment["universe"].update(unit="gene", member_ids=[f"G{i}" for i in range(1, 101)], membership_complete=True,
                                  membership_source_id="synthetic-tested-background")
    enrichment["provenance"] = {"species": "synthetic organism", "database": {"name": "Synthetic gene sets", "version": "v1"},
                                "background_definition": "100 synthetic measured genes", "evidence_type": "curated gene-set membership",
                                "fdr_method": "BH", "fdr_scope": "all supplied tested sets before output truncation"}
    enrichment["coverage"] = {"tested_sets": 5, "retained_sets": 2, "omitted_sets": 3, "truncated": True}
    for record, members, overlap in zip(enrichment["terms"],
                                       [["G1", "G2", *[f"G{i}" for i in range(11, 29)]],
                                        ["G3", "G4", "G5", "G6", *[f"G{i}" for i in range(31, 57)]]],
                                       [["G1", "G2"], ["G3", "G4", "G5", "G6"]]):
        record.update(query_member_ids=[f"G{i}" for i in range(1, 11)], set_member_ids=members,
                      overlap_member_ids=overlap, membership_complete=True, membership_source_id="synthetic-set-reference", member_unit="gene")
    motif = data["motif_enrichment"]
    motif["universe"].update(unit="promoter", member_ids=[f"p{i}" for i in range(1, 101)], membership_complete=True,
                            membership_source_id="synthetic-promoter-universe")
    motif["provenance"] = {"species": "synthetic organism", "database": {"name": "Synthetic motif set", "version": "v2"},
                           "background_definition": "100 synthetic promoters", "promoter_definition": "saved synthetic intervals",
                           "promoter_source_id": "synthetic-promoter-reference", "evidence_type": "sequence motif hits"}
    motif["coverage"] = {"tested_sets": 3, "retained_sets": 1, "omitted_sets": 2, "truncated": True}
    motif["terms"][0].update(foreground_member_ids=[f"p{i}" for i in range(1, 11)], background_member_ids=[f"p{i}" for i in range(1, 101)],
                             foreground_hit_ids=[f"p{i}" for i in range(1, 5)], background_hit_ids=[*[f"p{i}" for i in range(1, 5)], *[f"p{i}" for i in range(11, 27)]],
                             membership_complete=True, membership_source_id="synthetic-motif-hit-table", member_unit="promoter")
    motif["promoters"] = [{"id": "p1", "gene_id": "G1", "chromosome": "chr1", "start": 100, "end": 200,
                            "strand": "+", "genome_build": "synthetic-build", "coordinate_system": "0-based-half-open", "source_id": "synthetic-promoter-reference"}]
    data["temporal_model_comparison"].update(selected_candidate_id="df3", selected_df=3, selection_criterion="AIC",
                                            selection_provenance={"source_id": "synthetic-selection", "method": "saved candidate choice", "version": "v1"})
    data["saved_temporal_curve"]["source_records"] = [{"id": "G2", "status": "failed", "reason": "synthetic failed gene; no saved curve"}]
    data["lineage_schematic"]["nodes"] = deepcopy(graph_nodes)
    data["lineage_schematic"]["nodes"][0].update(sample_count=2, mean_pseudotime=0.2)
    data["lineage_schematic"]["membership"] = {"root_id": "a", "root_sample_id": "s1", "pseudotime_unit": "inferred ordering units",
                                               "samples": [{"id": "s1", "pseudotime": 0.1, "branch_id": "root"},
                                                           {"id": "s2", "pseudotime": 0.3, "branch_id": "branch_1"}]}
    data["orthology_comparison"]["mapping_provenance"] = {"source_id": "synthetic-orthology-table", "mapping_version": "release-7",
                                                           "source_namespace": "species-A stable gene IDs", "target_namespace": "species-B stable gene IDs",
                                                           "method": "saved curated orthology", "source_genome_build": "build-A", "target_genome_build": "build-B"}
    data["splice_tracks"]["track_metadata"] = {"sample1": {"sample_id": "s1", "biological_unit_id": "donor1", "replicate_id": "rep1",
                                                            "replicate_kind": "biological", "strand": "+", "source_id": "synthetic-library-metadata"}}
    for exon in data["splice_tracks"]["exons"]:
        exon.update(strand="+", gene_id="G1")
    return deepcopy([{"capability": capability, "data": {**values, "source_label": "synthetic geometry verification fixture"}}
                     for capability, values in data.items()])
