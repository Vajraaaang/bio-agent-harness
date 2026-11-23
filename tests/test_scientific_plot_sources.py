"""Saved-output projections: software/custody checks, not biological trials."""

import json
from copy import deepcopy
from pathlib import Path

import pytest

from devbench.analysis.checkpoints import digest
from devbench.analysis.scientific_plot_sources import (
    SCRNA_FORMAT,
    TOOL_FORMAT,
    MissingSavedPlotInputs,
    UnsupportedSavedPlotSource,
    prepare_plot_input_from_saved,
    prepare_report_plot_input,
)
from devbench.analysis.scientific_plot_suite import prepare_scientific_plot_workspace

SOURCE = {"sha256": "a" * 64, "run_id": "saved-synthetic-test", "kind": "synthetic"}


def workspace(document):
    return prepare_scientific_plot_workspace(
        prepare_plot_input_from_saved(document), source=SOURCE
    )


def temporal_result():
    # Exact names/semantics emitted by tools_stat._fit_temporal_worker.
    return {
        "family": "gaussian",
        "formula": "~ numeric_time",
        "selection_criterion": "fixed",
        "genes": {
            "g1": {
                "status": "ok",
                "time_grid": [0.0, 1.0, 2.0],
                "predicted_curve": [-1.0, 2.0, 5.0],
                "selected_df": 2,
                "formula": "~ numeric_time",
                "effect_scale": "expression_units",
                "contrast_note": (
                    "First sample nuisance covariates held fixed at both endpoints; "
                    "count library factor=1"
                ),
                "candidate_scores": [
                    {"df": 2, "aic": 3.0, "bic": 4.0, "gcv": 0.2, "status": "ok"},
                    {"df": 3, "status": "failed", "reason": "rank_deficient"},
                ],
            }
        },
        "provenance": {
            "parameters": {
                "statistical_method_version": (
                    "temporal_glm/4_exact_terms_and_effect_scale"
                ),
                "time_col": "time",
                "gene_list": ["g1"],
            }
        },
        "warnings": [],
    }


def enrichment_result(kind="regulon"):
    # Frozen synthetic result in the exact _enrichment_worker result schema.
    return {
        "results": [
            {
                ("tf" if kind == "regulon" else "motif"): "TF1",
                "overlap": 1,
                "reference_size": 2,
                "selected_size": 1,
                "background_size": 3,
                "fold_enrichment": 1.5,
                "p_value": 2 / 3,
                "q_value": 2 / 3,
            }
        ],
        "provenance": {
            "reference_sha256": "b" * 64,
            "evidence_type": "supplied curated TF-target overlap"
            if kind == "regulon"
            else "supplied sequence motif-hit calls",
            "test": "one-sided hypergeometric overrepresentation",
            "fdr_method": "Benjamini-Hochberg",
            "fdr_scope": "all supplied tested sets before output truncation",
            "tested_sets": 1,
            "returned_sets": 1,
            "truncated": False,
            "background_size": 3,
            "selected_size": 1,
        },
        "limitations": [],
    }


def lineage_result():
    return {
        "pseudotime": {"s1": 0.0, "s2": 1.0},
        "branch_assignments": {"s1": "branch_0", "s2": "branch_0"},
        "lineage_graph": {
            "nodes": [
                {"id": "C0", "label": "C0", "sample_count": 1, "mean_pseudotime": 0.0},
                {"id": "C1", "label": "C1", "sample_count": 1, "mean_pseudotime": 1.0},
            ],
            "edges": [{"source": "C0", "target": "C1"}],
            "root": "C0",
        },
        "provenance": {
            "algorithm": (
                "variable genes / standardization / PCA / kNN diffusion / "
                "rooted geodesic / cluster MST"
            ),
            "sampling_time_used": False,
        },
        "limitations": ["Exploratory geometry is not lineage tracing."],
    }


def network_result():
    return {
        "edges": [
            {"tf": "g1", "target": "g2", "coefficient": -1.7, "score": 1.7, "sign": -1}
        ],
        "candidate_universe": [
            {"tf": "g1", "target": "g2"},
            {"tf": "g1", "target": "g3"},
        ],
        "provenance": {
            "algorithm": "lagged time-mean multivariable ridge association",
            "alpha": 1.0,
            "lag_observed_intervals": 1,
            "transform": "continuous expression z-score",
            "candidate_edge_count": 2,
            "returned_edge_count": 1,
            "truncated": True,
        },
        "limitations": [],
    }


def bound_table(rows):
    return {
        "rows": rows,
        "source": {
            "artifact_id": "saved-table",
            "sha256": "a" * 64,
            "run_id": "saved-run",
            "rows_sha256": digest(rows),
        },
    }


def scrna_tables():
    rows = [
        {
            "cell_id": "c" + str(i),
            "sample_id": "s" + str(i // 3),
            "batch": "batch1",
            "total_counts": 10 + i,
            "n_genes_by_counts": 2 + i,
            "pct_counts_mt": float(i),
            "qc_pass": i != 0,
        }
        for i in range(6)
    ]
    return {
        "format": SCRNA_FORMAT,
        "cohort": {
            "ids": [r["cell_id"] for r in rows],
            "sha256": digest([r["cell_id"] for r in rows]),
        },
        "qc_metrics": {
            **bound_table(rows),
            "sample_column": "sample_id",
            "batch_column": "batch",
            "group_by": "batch",
            "report": {
                "method": "scanpy_QC_log1p_MAD/1",
                "cells_input": 6,
                "cells_retained": 5,
                "cells_excluded": 1,
                "excluded_cell_ids": ["c0"],
                "min_genes": 3,
                "max_mito_percent": 20.0,
                "thresholds": [],
            },
        },
        "group_gene_aggregates": {
            **bound_table(
                [
                    {
                        "id": "group-g1",
                        "group": "group",
                        "gene_id": "g1",
                        "mean_expression": 1.5,
                        "numerator": 1,
                        "denominator": 2,
                        "member_ids": ["c1", "c2"],
                        "values": [0.0, 3.0],
                    }
                ]
            ),
            "unit": "normalized_expression",
            "source_scale": "normalized_expression",
            "layer": "saved_normalized",
            "expression_threshold": 0.0,
            "denominator_kind": "measured_cells",
        },
    }


def test_bulk_capture_maps_three_dispersion_fields_and_keeps_saved_factors():
    from tests.test_bulk_plot_data import result

    raw = result()
    raw["diagnostics"]["normalization"] = {"method": "median_of_ratios"}
    raw["diagnostics"]["calibration"].update(
        method="pydeseq2_empirical_bayes", trend_type="parametric"
    )
    original = deepcopy(raw)
    prepared = workspace(raw)
    assert raw == original
    assert {p["capability"] for p in prepared["panels"]} == {
        "bulk_library_qc",
        "bulk_dispersion",
        "scatter_marginals",
    }
    dispersion = next(
        p for p in prepared["panels"] if p["capability"] == "bulk_dispersion"
    )
    assert {r["group"] for r in dispersion["rows"]} == {"gene_wise", "final", "trend"}
    layers = {r["group"]: r["details"] for r in dispersion["rows"] if r["id"] == "g0"}
    assert (
        layers["gene_wise"]["dispersion"] == 0.4
        and layers["final"]["dispersion"] == 1e-8
    )
    depth = next(p for p in prepared["panels"] if p["title"] == "Raw library depth")
    assert [(r["id"], r["y"]) for r in depth["rows"]] == [
        ("sB2", 18.0),
        ("sA1", 1.0),
        ("sB1", 8.0),
        ("sA2", 2.0),
    ]


def test_absent_saved_bulk_definitions_do_not_fabricate_diagnostic_plots():
    from tests.test_bulk_plot_data import result

    raw = result()
    raw["target_results"][0]["payload"].pop("computed_contrast")
    with pytest.raises(MissingSavedPlotInputs):
        prepare_plot_input_from_saved(raw)


def test_saved_bulk_ma_preserves_failed_effects_and_independent_x_members():
    import math

    from tests.test_bulk_plot_data import result

    raw = result()
    raw["target_results"][0]["payload"]["gene_statistics"][1]["values"] = [1234.0]
    original = deepcopy(raw)
    packet = prepare_plot_input_from_saved(raw)
    assert raw == original
    data = next(
        p["data"] for p in packet["plots"] if p["capability"] == "scatter_marginals"
    )
    source = {r["id"]: r for r in data["rows"]}
    assert source["g0"]["x"] == math.log10(4.75) and source["g0"]["y"] == 2.0
    assert source["g1"]["x"] == 0.0 and source["g1"]["y"] is None
    assert source["g1"]["details"]["estimate_status"] == "failed"
    assert source["g1"]["details"]["gene_statistics"]["values"] == [1234.0]
    view = workspace(raw)
    joint, xmargin, ymargin = [
        p for p in view["panels"] if p["capability"] == "scatter_marginals"
    ]
    assert len(joint["marks"]) == 2
    assert (
        sum(r["details"]["count"] for r in xmargin["rows"] if r["entity_kind"] == "bin")
        == 3
    )
    assert (
        sum(r["details"]["count"] for r in ymargin["rows"] if r["entity_kind"] == "bin")
        == 2
    )
    for panel in (joint, xmargin, ymargin):
        assert {r["id"] for r in panel["rows"] if r["entity_kind"] == "gene"} == {
            "g0",
            "g1",
            "g2",
        }
    assert any(
        {"kind": "gene", "id": "g1"} in r["details"].get("member_links", [])
        for r in xmargin["rows"]
    )
    assert not any(
        {"kind": "gene", "id": "g1"} in r["details"].get("member_links", [])
        for r in ymargin["rows"]
    )


def test_all_failed_native_bulk_effects_keep_means_without_imputed_Y_values():
    from tests.test_bulk_plot_data import result

    raw = result()
    for record in raw["target_results"][0]["payload"]["gene_statistics"]:
        record.update(
            estimate_status="failed", values=[], missing_reason="convergence_failure"
        )
    packet = prepare_plot_input_from_saved(raw)
    data = next(
        p["data"] for p in packet["plots"] if p["capability"] == "scatter_marginals"
    )
    assert all(r["y"] is None for r in data["rows"])
    assert any("empty axis scaffold" in note for note in packet["limitations"])
    panels = [
        p for p in workspace(raw)["panels"] if p["capability"] == "scatter_marginals"
    ]
    assert not panels[0]["marks"]
    assert (
        sum(
            r["details"]["count"]
            for r in panels[1]["rows"]
            if r["entity_kind"] == "bin"
        )
        == 3
    )
    assert (
        sum(
            r["details"]["count"]
            for r in panels[2]["rows"]
            if r["entity_kind"] == "bin"
        )
        == 0
    )


def test_native_bulk_MA_rejects_an_effect_family_from_different_genes():
    from tests.test_bulk_plot_data import result

    raw = result()
    raw["target_results"][0]["payload"]["gene_statistics"][0]["gene_id"] = (
        "another_cohort_gene"
    )
    with pytest.raises(ValueError, match="family_mismatch"):
        prepare_plot_input_from_saved(raw)


def test_registered_existing_bulk_output_reaches_saved_MA_marginals(tmp_path):
    """Existing registered synthetic method verification, not a new analysis."""
    from devharness_server.dispatcher import ToolDispatcher
    from tests.test_registered_bulk_contrast import bulk_inputs

    task, paths, options = bulk_inputs(tmp_path, continuous=False)
    raw = json.loads(
        ToolDispatcher(allowed_root=tmp_path).execute(
            "bulk_condition_contrast",
            {
                "counts_path": str(paths["expression"]),
                "metadata_path": str(paths["metadata"]),
                "public_task": task,
                "options": options,
            },
        )
    )
    before = deepcopy(raw)
    packet = prepare_plot_input_from_saved(raw)
    assert raw == before
    data = next(
        p["data"] for p in packet["plots"] if p["capability"] == "scatter_marginals"
    )
    actual = {
        r["gene_id"]: r for r in raw["target_results"][0]["payload"]["gene_statistics"]
    }
    assert [r["id"] for r in data["rows"]] == task["gene_ids"]
    for row in data["rows"]:
        assert row["details"]["gene_statistics"] == actual[row["id"]]
        assert row["y"] == (
            actual[row["id"]]["values"][0]
            if actual[row["id"]]["estimate_status"] == "estimated"
            else None
        )


def test_registered_temporal_shape_keeps_failed_candidates_and_no_invented_ci():
    raw = temporal_result()
    packet = prepare_plot_input_from_saved(raw)
    assert {p["capability"] for p in packet["plots"]} == {
        "temporal_model_comparison",
        "saved_temporal_curve",
    }
    curve = next(
        p for p in packet["plots"] if p["capability"] == "saved_temporal_curve"
    )["data"]
    assert curve["points"][0]["value"] == -1.0
    assert "unit not retained" in curve["time_unit"]
    assert "knots" not in curve["model"] and "interval" not in curve
    view = workspace(raw)
    assert not any(m["type"] == "polygon" for p in view["panels"] for m in p["marks"])
    model = next(
        p for p in view["panels"] if p["capability"] == "temporal_model_comparison"
    )
    assert any(r["reason"] == "rank_deficient" for r in model["rows"])
    raw["genes"]["g1"]["time_grid"].pop()
    with pytest.raises(ValueError, match="alignment"):
        prepare_plot_input_from_saved(raw)


@pytest.mark.parametrize("kind", ["regulon", "motif"])
def test_registered_enrichment_preserves_denominators_and_exact_parameter_membership(
    kind,
):
    key = "selected_genes" if kind == "regulon" else "foreground_genes"
    document = {
        "format": TOOL_FORMAT,
        "tool_name": kind + "_enrichment",
        "result": enrichment_result(kind),
        "parameters": {key: ["g1"], "background_genes": ["g1", "g2", "g3"]},
    }
    view = workspace(document)
    assert view["panels"][0]["rows"][0]["details"]["universe_size"] == 3
    if kind == "regulon":
        assert view["panels"][0]["rows"][0]["x"] == 1.0
    else:
        assert view["panels"][0]["rows"][0]["x"] == 1.5
    document["parameters"][key] = ["outside"]
    with pytest.raises(ValueError, match="subset"):
        prepare_plot_input_from_saved(document)


def test_saved_ridge_is_not_relabelled_as_correlation_or_curated_membership():
    view = workspace(network_result())
    edge = next(r for r in view["panels"][0]["rows"] if r["entity_kind"] == "edge")
    assert edge["details"]["coefficient"] == -1.7
    assert edge["details"].get("correlation") is None
    assert edge["details"].get("curated_membership") is None
    raw = network_result()
    raw["edges"][0]["target"] = "foreign"
    with pytest.raises(ValueError, match="universe"):
        workspace(raw)


@pytest.mark.parametrize(
    "aggregation,algorithm",
    [
        ("sample", "lagged sample-order multivariable ridge association"),
        ("replicate", "lagged within-replicate multivariable ridge association"),
    ],
)
def test_explicit_network_aggregation_retains_declared_ridge_method(
    aggregation, algorithm
):
    raw = network_result()
    raw["provenance"].update(algorithm=algorithm, aggregation=aggregation)
    result = prepare_plot_input_from_saved(raw)
    edge = result["plots"][0]["data"]["edges"][0]
    assert edge["association_method"] == algorithm
    assert edge["coefficient"] == raw["edges"][0]["coefficient"]
    raw["provenance"]["aggregation"] = "time_mean"
    with pytest.raises(ValueError, match="aggregation_mismatch"):
        prepare_plot_input_from_saved(raw)


@pytest.mark.parametrize(
    "field,value", [("fdr_scope", "only displayed terms"), ("truncated", True)]
)
def test_enrichment_scope_and_saved_output_truncation_cannot_be_relabelled(
    field, value
):
    raw = enrichment_result()
    raw["provenance"][field] = value
    with pytest.raises(ValueError, match="testing_family"):
        workspace(raw)


def enrichment_with_saved_membership(kind="regulon"):
    raw = enrichment_result(kind)
    raw["membership"] = {
        "format": "devbench.saved-enrichment-membership/1",
        "selected_ids": ["g1"],
        "background_ids": ["g1", "g2", "g3"],
        "term_member_ids": {"TF1": ["g1", "g2"]},
        "source_id": "saved-synthetic-reference-membership",
    }
    raw["provenance"].update(
        species="synthetic_species",
        database={"name": "Synthetic reference", "version": "fixture-v1"},
        background_definition="Three saved measured synthetic genes",
        tested_sets=3,
        returned_sets=1,
        truncated=True,
        promoter_definition="Saved synthetic promoter intervals",
        promoter_source_id="synthetic-promoters",
    )
    if kind == "motif":
        raw["promoters"] = [
            {
                "id": "promoter-g1",
                "gene_id": "g1",
                "chromosome": "chrFixture",
                "start": 10,
                "end": 40,
                "strand": "+",
                "genome_build": "synthetic-build",
                "coordinate_system": "0-based-half-open",
                "source_id": "synthetic-promoters",
            }
        ]
    return raw


@pytest.mark.parametrize("kind", ["regulon", "motif"])
def test_enrichment_keeps_full_membership_provenance_and_upstream_omission(
    kind,
):
    raw = enrichment_with_saved_membership(kind)
    original = deepcopy(raw)
    packet = prepare_plot_input_from_saved(raw)
    assert raw == original
    data = packet["plots"][0]["data"]
    assert data["universe"]["member_ids"] == ["g1", "g2", "g3"]
    assert data["universe"]["membership_complete"] is True
    assert data["provenance"]["species"] == "synthetic_species"
    assert data["provenance"]["database"]["version"] == "fixture-v1"
    assert data["coverage"] == {
        "tested_sets": 3,
        "retained_sets": 1,
        "omitted_sets": 2,
        "truncated": True,
    }
    term = data["terms"][0]
    assert term[
        "query_member_ids" if kind == "regulon" else "foreground_member_ids"
    ] == ["g1"]
    assert term["set_member_ids" if kind == "regulon" else "background_hit_ids"] == [
        "g1",
        "g2",
    ]
    assert term[
        "overlap_member_ids" if kind == "regulon" else "foreground_hit_ids"
    ] == ["g1"]
    if kind == "motif":
        assert data["promoters"] == raw["promoters"]
    view = workspace(raw)
    serialized = json.dumps(view)
    assert "fixture-v1" in serialized and "omitted" in serialized.lower()


def test_missing_native_membership_and_species_remain_explicitly_unknown():
    packet = prepare_plot_input_from_saved(enrichment_result())
    data = packet["plots"][0]["data"]
    assert "member_ids" not in data["universe"]
    assert (
        data["provenance"]["species"] is None and data["provenance"]["database"] is None
    )
    assert "set_member_ids" not in data["terms"][0]
    assert any(
        "Complete per-term member IDs were not retained" in value
        for value in packet["limitations"]
    )


@pytest.mark.parametrize(
    "attack",
    ["wrong_term_count", "outside_background", "wrong_overlap", "dispatch_mismatch"],
)
def test_saved_enrichment_memberships_are_checked_not_only_displayed(attack):
    raw = enrichment_with_saved_membership()
    if attack == "wrong_term_count":
        raw["membership"]["term_member_ids"]["TF1"] = ["g1"]
    elif attack == "outside_background":
        raw["membership"]["term_member_ids"]["TF1"] = ["g1", "foreign"]
    elif attack == "wrong_overlap":
        raw["membership"]["term_member_ids"]["TF1"] = ["g2", "g3"]
    else:
        raw = {
            "format": TOOL_FORMAT,
            "tool_name": "regulon_enrichment",
            "result": raw,
            "parameters": {
                "selected_genes": ["g2"],
                "background_genes": ["g1", "g2", "g3"],
            },
        }
    with pytest.raises(ValueError, match="membership"):
        prepare_plot_input_from_saved(raw)


def test_partial_saved_term_membership_is_retained_without_claiming_complete_ids():
    raw = enrichment_result()
    raw["results"][0].update(
        set_member_ids=["g1"],
        membership_complete=False,
        membership_source_id="saved-partial-reference",
        member_unit="gene",
    )
    packet = prepare_plot_input_from_saved(raw)
    term = packet["plots"][0]["data"]["terms"][0]
    assert term["set_member_ids"] == ["g1"] and term["membership_complete"] is False


def test_saved_member_ids_keep_unknown_completeness_without_metadata():
    raw = enrichment_result()
    raw["results"][0].update(
        set_member_ids=["g1", "g2"],
        membership_source_id="saved-reference",
        member_unit="gene",
    )
    term = prepare_plot_input_from_saved(raw)["plots"][0]["data"]["terms"][0]
    assert term["set_member_ids"] == ["g1", "g2"] and "membership_complete" not in term


def test_retained_enrichment_term_ids_cannot_come_from_another_gene_cohort(
    workspace_tmp_path,
):
    raw = enrichment_result()
    raw["results"][0].update(
        set_member_ids=["g1", "foreign"],
        membership_complete=True,
        membership_source_id="saved-reference",
        member_unit="gene",
    )
    with pytest.raises(ValueError, match="gene universe|gene lineage"):
        retained_plot_bundle(workspace_tmp_path, raw)


def test_actual_lineage_schema_projects_topology_and_rejects_mixed_sample_cohorts():
    raw = lineage_result()
    view = workspace(raw)
    edge = next(r for r in view["panels"][0]["rows"] if r["entity_kind"] == "edge")
    assert edge["details"]["evidence"] == "inferred"
    raw["branch_assignments"]["foreign"] = "branch_0"
    with pytest.raises(ValueError, match="membership"):
        workspace(raw)


def test_saved_scrna_qc_and_aggregates_retain_all_cells_and_denominator_ids():
    document = scrna_tables()
    before = deepcopy(document)
    view = workspace(document)
    assert document == before
    qc = next(p for p in view["panels"] if p["capability"] == "qc_joint_scatter")
    assert len(qc["rows"]) == 6 and qc["rows"][0]["details"]["qc_status"] == "excluded"
    marker = next(p for p in view["panels"] if p["capability"] == "marker_dotplot")
    assert marker["rows"][0]["details"]["member_ids"] == ["c1", "c2"]


def test_saved_scrna_QC_correlation_uses_actual_identity_columns_and_all_source_cells():
    document = scrna_tables()
    original = deepcopy(document)
    packet = prepare_plot_input_from_saved(document)
    data = next(
        p["data"]
        for p in packet["plots"]
        if p["capability"] == "measurement_correlation"
    )
    assert document == original
    assert (
        data["observation_kind"] == "cell"
        and data["missingness_policy"] == "pairwise_complete"
    )
    assert [m["id"] for m in data["measurements"]] == [
        "total_counts",
        "n_genes_by_counts",
        "pct_counts_mt",
    ]
    assert all(
        m["declared_transform"].startswith("identity;") for m in data["measurements"]
    )
    assert [r["id"] for r in data["observations"]] == document["cohort"]["ids"]
    assert data["observations"][0]["details"]["qc_pass"] is False
    panel = next(
        p
        for p in workspace(document)["panels"]
        if p["capability"] == "measurement_correlation"
    )
    pair = next(
        r
        for r in panel["rows"]
        if r["entity_kind"] == "measurement_pair"
        and r["details"]["left_measurement"]["id"] == "total_counts"
        and r["details"]["right_measurement"]["id"] == "n_genes_by_counts"
    )
    assert pair["details"]["n_pairs"] == 6 and pair["details"][
        "pearson_r"
    ] == pytest.approx(1.0)
    assert pair["details"]["pair_member_ids"] == document["cohort"]["ids"]


def test_saved_QC_correlation_preserves_pairwise_missingness_and_null_constant_pairs():
    document = scrna_tables()
    table = document["qc_metrics"]
    table["rows"][2]["total_counts"] = None
    for record in table["rows"]:
        record["pct_counts_mt"] = 3.0
    table["source"]["rows_sha256"] = digest(table["rows"])
    panel = next(
        p
        for p in workspace(document)["panels"]
        if p["capability"] == "measurement_correlation"
    )
    pairs = {
        (
            r["details"]["left_measurement"]["id"],
            r["details"]["right_measurement"]["id"],
        ): r
        for r in panel["rows"]
        if r["entity_kind"] == "measurement_pair"
    }
    depth_genes = pairs[("total_counts", "n_genes_by_counts")]
    assert depth_genes["details"]["pair_member_ids"] == ["c0", "c1", "c3", "c4", "c5"]
    assert pairs[("n_genes_by_counts", "n_genes_by_counts")]["details"]["n_pairs"] == 6
    mito = pairs[("pct_counts_mt", "pct_counts_mt")]
    assert (
        mito["details"]["pearson_r"] is None
        and mito["reason"] == "constant_measurement"
    )


@pytest.mark.parametrize(
    "attack", ["row_hash", "cohort", "membership", "denominator", "qc_report", "path"]
)
def test_scrna_bound_tables_reject_tampering_and_cross_cohort_members(attack):
    document = scrna_tables()
    if attack == "row_hash":
        document["qc_metrics"]["rows"][0]["total_counts"] += 1
    elif attack == "cohort":
        document["cohort"]["ids"][0] = "foreign"
    elif attack in {"membership", "denominator"}:
        value = document["group_gene_aggregates"]
        if attack == "membership":
            value["rows"][0]["member_ids"][0] = "foreign"
        else:
            value["rows"][0]["denominator"] = 4
        value["source"]["rows_sha256"] = digest(value["rows"])
    elif attack == "qc_report":
        document["qc_metrics"]["report"]["excluded_cell_ids"] = []
    else:
        document["qc_metrics"]["source"]["path"] = "/arbitrary/never/read.csv"
    with pytest.raises(ValueError):
        workspace(document)


def test_unknown_arrays_are_not_assigned_scientific_meaning():
    with pytest.raises(UnsupportedSavedPlotSource):
        prepare_plot_input_from_saved({"x": [1.0, 2.0], "y": [3.0, 4.0]})


def test_engine_named_saved_curves_and_exact_rss_bytes_use_common_packets():
    metadata = {
        "memory_samples": [
            {"elapsed_sec": 0.0, "rss_bytes": 1048576},
            {"elapsed_sec": 1.0, "rss_bytes": 2097152},
        ],
        "report_artifacts": {
            "expression_curves": [
                {
                    "gene": "g1",
                    "x_label": "age",
                    "y_label": "expression",
                    "points": [
                        {"x": 0.0, "prediction": 2.0},
                        {"x": 1.0, "prediction": 3.0},
                    ],
                }
            ]
        },
    }
    packet = prepare_report_plot_input(metadata)
    memory = next(p for p in packet["plots"] if p["capability"] == "resource_curve")
    assert (
        memory["data"]["value_unit"] == "bytes"
        and memory["data"]["measurements"][1]["value"] == 2097152
    )
    from devbench_engine.visualizer import render_report

    html = render_report({"task_id": "synthetic-plot-test", "metadata": metadata})
    assert "data-engine-workspace=" in html and "BulkScientificFigure.mount" in html
    assert "scientificWorkspace:JSON.parse" in html
    # Shared packaged JavaScript legitimately includes optional image support.
    # Check this actual saved payload, not string literals in unrelated code.
    import re

    encoded = re.search(
        r'<script type="application/json" data-engine-workspace="\d+">(.*?)</script>',
        html,
        re.S,
    ).group(1)
    retained = json.loads(encoded)
    assert all(
        mark["type"] != "image"
        for panel in retained["panels"]
        for mark in panel["marks"]
    )


def test_plot_inputs_remain_strict_and_cannot_carry_unvalidated_extra_fields():
    packet = prepare_plot_input_from_saved(lineage_result())
    assert prepare_plot_input_from_saved(packet) == packet
    packet["plots"][0]["data"]["curve"] = [42.0]
    with pytest.raises(ValueError):
        prepare_plot_input_from_saved(packet)


def test_static_curve_preview_reports_omissions_with_complete_typed_source():
    from devbench_engine.visualizer import render_report

    points = [{"x": float(i), "prediction": float(i % 7)} for i in range(2001)]
    metadata = {
        "report_artifacts": {"expression_curves": [{"gene": "g1", "points": points}]}
    }
    packet = prepare_report_plot_input(metadata)
    assert len(packet["plots"][0]["data"]["points"]) == 2001
    report = render_report({"task_id": "synthetic-preview-limit", "metadata": metadata})
    assert "Legacy static preview: 2000 of 2001 supplied points" in report


def retained_plot_bundle(tmp_path, raw=None):
    from devbench.analysis.rna_comparison_outputs import (
        extract_comparison_outputs,
        load_comparison_outputs,
    )
    from tests.test_rna_comparison_outputs import (
        RetainedTableExecutor,
        bound_run,
        prepared,
    )

    service, step, plan = prepared(tmp_path)
    # An actual retained output artifact from the existing synthetic executor.
    # Its raw recognized lineage schema is converted, not an invented bundle.
    run = bound_run(
        service,
        step,
        plan,
        RetainedTableExecutor(lineage_result() if raw is None else raw),
    )
    reference = extract_comparison_outputs([run], plan, artifact_service=service)
    return (
        service,
        run,
        plan,
        load_comparison_outputs(reference, artifact_service=service),
    )


def test_comparison_output_extracts_and_rederives_actual_saved_lineage(
    workspace_tmp_path,
):
    service, run, plan, bundle = retained_plot_bundle(workspace_tmp_path)
    assert len(bundle["graphs"]) == 1 and not bundle["gene_tables"]
    graph = bundle["graphs"][0]
    assert graph["artifact_ref"]["artifact_id"] in run["outputs"].values()
    assert graph["evidence_root"] == run["evidence"]["root_hash"]
    projection = json.loads(Path(graph["projection_ref"]["path"]).read_bytes())
    assert projection["input_refs"] == bundle["runs"][0]["input_refs"]
    assert projection["packet"]["plots"][0]["capability"] == "lineage_schematic"
    assert (
        projection["scope"]
        == "saved_output_projection_not_comparative_scientific_qualification"
    )


@pytest.mark.parametrize(
    "raw,field",
    [
        (temporal_result(), "curves"),
        (enrichment_result(), "sets"),
        (network_result(), "graphs"),
    ],
)
def test_comparison_output_populates_sets_and_curves_from_actual_retained_named_results(
    workspace_tmp_path, raw, field
):
    service, run, plan, bundle = retained_plot_bundle(workspace_tmp_path, raw)
    assert len(bundle[field]) == 1
    projection = json.loads(
        Path(bundle[field][0]["projection_ref"]["path"]).read_bytes()
    )
    assert projection["run_id"] == run["run_id"]
    assert (
        projection["artifact_ref"]["sha256"]
        == bundle[field][0]["artifact_ref"]["sha256"]
    )


def test_output_declared_enrichment_membership_cannot_cross_actual_cohort(
    workspace_tmp_path,
):
    raw = {
        "format": TOOL_FORMAT,
        "tool_name": "regulon_enrichment",
        "result": enrichment_result(),
        "parameters": {
            "selected_genes": ["foreign"],
            "background_genes": ["foreign", "g2", "g3"],
        },
    }
    with pytest.raises(ValueError, match="gene universe"):
        retained_plot_bundle(workspace_tmp_path, raw)


def test_temporal_no_information_predictions_remain_unavailable():
    raw = temporal_result()
    raw["genes"]["g1"]["status"] = "no_information"
    view = workspace(raw)
    panel = next(p for p in view["panels"] if p["capability"] == "saved_temporal_curve")
    assert all(r["status"] == "excluded" for r in panel["rows"])
    assert not panel["marks"]


def test_temporal_failed_and_missing_genes_have_no_invented_coordinates():
    raw = temporal_result()
    raw["provenance"]["parameters"]["gene_list"] += ["g_failed", "g_absent"]
    raw["genes"]["g_failed"] = {
        "status": "failed",
        "failure_code": "dispersion_fit_failed",
        "reason": "nonconvergence",
        "candidate_scores": [],
        "p_value": None,
        "padj": None,
        "selected_df": None,
    }
    before = deepcopy(raw)
    view = workspace(raw)
    assert raw == before
    rows = {r["id"]: r for p in view["panels"] for r in p["rows"]}
    for gene in ("g_failed", "g_absent"):
        assert rows[gene]["x"] is None and rows[gene]["y"] is None
        assert rows[gene]["link"] == {"kind": "gene", "id": gene}
    assert "nonconvergence" in rows["g_failed"]["reason"]
    assert "absent" in rows["g_absent"]["reason"].lower()


def test_all_failed_temporal_family_retains_source_records_and_no_numeric_marks():
    raw = temporal_result()
    raw["genes"]["g1"] = {
        "status": "failed",
        "failure_code": "all_candidate_fits_failed",
        "candidate_scores": [
            {"df": 2, "status": "failed", "reason": "glm_nonconvergence"}
        ],
        "selected_df": None,
    }
    view = workspace(raw)
    assert len(view["panels"]) == 1
    assert any(
        r["id"] == "g1" and "all_candidate_fits_failed" in r["reason"]
        for r in view["panels"][0]["rows"]
    )
    assert all(r["x"] is None and r["y"] is None for r in view["panels"][0]["rows"])
    assert not view["panels"][0]["marks"]


def test_temporal_selected_candidate_identity_is_preserved():
    packet = prepare_plot_input_from_saved(temporal_result())
    data = next(
        p["data"]
        for p in packet["plots"]
        if p["capability"] == "temporal_model_comparison"
    )
    assert data["selected_candidate_id"] == "df-2"
    assert data["selected_df"] == 2 and data["selection_criterion"] == "fixed"
    assert data["selection_provenance"] == {
        "version": "temporal_glm/4_exact_terms_and_effect_scale"
    }


@pytest.mark.parametrize(
    "mode", ["foreign", "false_complete", "wrong_input_count", "foreign_root"]
)
def test_lineage_outputs_must_match_actual_bound_input_sample_cohort(
    workspace_tmp_path, mode
):
    raw = lineage_result()
    if mode == "foreign":
        raw["pseudotime"] = {"alien_sample_1": 0.0, "alien_sample_2": 1.0}
        raw["branch_assignments"] = {"alien_sample_1": "root", "alien_sample_2": "root"}
    elif mode == "false_complete":
        raw["provenance"].update(
            input_sample_count=4, selected_sample_count=2, subsampled=False
        )
    elif mode == "wrong_input_count":
        raw["provenance"].update(
            input_sample_count=2, selected_sample_count=2, subsampled=False
        )
    else:
        raw["provenance"]["root_sample_id"] = "alien_sample_1"
    with pytest.raises(ValueError, match="sample|cohort|root"):
        retained_plot_bundle(workspace_tmp_path, raw)


def test_lineage_projection_retains_checked_sample_ids_root_and_branches(
    workspace_tmp_path,
):
    raw = lineage_result()
    raw["provenance"].update(
        input_sample_count=4,
        selected_sample_count=2,
        subsampled=True,
        root_sample_id="s1",
    )
    service, run, plan, bundle = retained_plot_bundle(workspace_tmp_path, raw)
    projection = json.loads(
        Path(bundle["graphs"][0]["projection_ref"]["path"]).read_bytes()
    )
    membership = projection["sample_membership"]
    assert membership["input_sample_ids"] == ["s1", "s2", "s3", "s4"]
    assert membership["sample_ids"] == ["s1", "s2"]
    assert membership["pseudotime"] == raw["pseudotime"]
    assert membership["branch_assignments"] == raw["branch_assignments"]
    assert membership["root_id"] == "C0" and membership["root_sample_id"] == "s1"
    assert membership["input_metadata_ref"]["artifact_id"] in run["inputs"].values()


def test_lineage_membership_replay_rejects_rehashed_branch_or_root_substitution(
    workspace_tmp_path,
):
    from devbench.analysis.rna_comparison_outputs import validate_comparison_outputs
    from devbench.analysis.rna_execution_inputs import _source

    service, run, plan, bundle = retained_plot_bundle(workspace_tmp_path)
    graph = bundle["graphs"][0]
    projection = json.loads(Path(graph["projection_ref"]["path"]).read_bytes())
    projection["sample_membership"]["branch_assignments"]["s1"] = "invented_branch"
    projection["sample_membership"]["root_id"] = "C1"
    changed = workspace_tmp_path / "altered-membership.json"
    changed.write_text(json.dumps(projection))
    graph["projection_ref"] = _source(changed)
    with pytest.raises(ValueError, match="Derived scientific plot"):
        validate_comparison_outputs(
            bundle, artifact_service=service, assessment_plan=plan
        )


def test_bulk_saved_factor_alignment_is_not_inferred_from_array_order():
    from tests.test_bulk_plot_data import result

    raw = result()
    raw["diagnostics"]["plot_evidence"]["sample_ids"].reverse()
    with pytest.raises(ValueError, match="alignment"):
        prepare_plot_input_from_saved(raw)


@pytest.mark.parametrize(
    "attack", ["cohort", "run", "artifact", "projection_bytes", "rehashed_projection"]
)
def test_comparison_plot_custody_rejects_tamper_and_cohort_substitution(
    workspace_tmp_path, attack
):
    from devbench.analysis.rna_comparison_outputs import validate_comparison_outputs
    from devbench.analysis.rna_execution_inputs import _source

    service, run, plan, bundle = retained_plot_bundle(workspace_tmp_path)
    graph = bundle["graphs"][0]
    if attack == "cohort":
        graph["cohort_sha256"] = "f" * 64
    elif attack == "run":
        graph["run_id"] = "f" * 32
    elif attack == "artifact":
        graph["artifact_ref"]["artifact_id"] = next(iter(run["inputs"].values()))
    else:
        original = Path(graph["projection_ref"]["path"])
        changed = json.loads(original.read_bytes())
        changed["packet"]["plots"][0]["data"]["nodes"][0]["label"] = "altered node"
        # Keep actual immutable artifacts untouched; substitute a different
        # protected derived file, then attempt both stale and updated hashes.
        path = workspace_tmp_path / "tampered-projection.json"
        path.write_text(json.dumps(changed))
        if attack == "rehashed_projection":
            graph["projection_ref"] = _source(path)
        else:
            graph["projection_ref"]["path"] = str(path)
    with pytest.raises(ValueError):
        validate_comparison_outputs(
            bundle, artifact_service=service, assessment_plan=plan
        )
