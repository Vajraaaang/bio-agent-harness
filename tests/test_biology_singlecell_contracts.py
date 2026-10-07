"""Only pure configuration checks run while biological data execution is held.

Small local fixtures require DEVBENCH_RUN_LOCAL_FIXTURES=1.
Live benchmark participants remain separately authorized.
"""

from __future__ import annotations

import builtins
import importlib

import pytest

from devbench.study.biology import BiologyRecipe, GenerationParameters


def recipe(case_id="T14-state-only", **parameters):
    template = case_id.split("-", 1)[0]
    conditions = ("control", "treated") if template in {"T14", "T20"} else ("observed",)
    if case_id == "T20-double-perturbation":
        conditions = ("control", "a", "b", "a_b")
    return BiologyRecipe(
        recipe_id="contract-only", template_id=template, case_id=case_id,
        role="calibration", generation_seed=17, mechanism_holdout_group="contract-only",
        parameters=GenerationParameters(**{
            "num_genes": 120, "independent_units_per_group": 3,
            "condition_labels": conditions, "cells_per_specimen": 40,
            "cell_type_count": 3, **parameters,
        }),
    )


def singlecell():
    return importlib.import_module("devbench.study.biology_singlecell")


def prohibit_numerical_import(monkeypatch):
    original = builtins.__import__

    def guarded(name, *args, **kwargs):
        assert name.split(".")[0] not in {"numpy", "pandas", "scipy"}, "numerical runtime imported during a configuration-only check"
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded)


def test_import_is_configuration_only(monkeypatch):
    prohibit_numerical_import(monkeypatch)
    importlib.reload(singlecell())


@pytest.mark.parametrize("permission", [None, False, 0, 1, "true", [], {}])
def test_default_denial_precedes_recipe_inspection(permission, monkeypatch):
    module = singlecell()
    prohibit_numerical_import(monkeypatch)

    class UnreadableRecipe:
        def __getattribute__(self, name):
            raise AssertionError("recipe inspected without authorization")

    with pytest.raises(PermissionError, match="allow_data_execution=True"):
        module.generate_biology_singlecell(UnreadableRecipe(), allow_data_execution=permission)


def test_supported_case_registry_is_exact_and_not_a_generic_fallback():
    module = singlecell()
    assert len(module.supported_biology_singlecell_cases()) == 37
    assert "T15-direction-reversed" in module.supported_biology_singlecell_cases()
    assert "T18-homotypic-doublet" in module.supported_biology_singlecell_cases()
    assert "T20-off-target" in module.supported_biology_singlecell_cases()
    with pytest.raises(ValueError, match="not implemented"):
        module.validate_biology_singlecell_recipe(recipe("T14-unimplemented"))


def test_admission_has_no_numerical_runtime(monkeypatch):
    module = singlecell()
    configuration = recipe()
    prohibit_numerical_import(monkeypatch)
    plan = module.validate_biology_singlecell_recipe(configuration)
    assert plan.observation_upper_bound == 240
    assert plan.expression_bytes == 120 * 240 * 8
    assert plan.stored_numeric_bytes_upper_bound > plan.expression_bytes


def test_all_declared_cases_have_pure_admission_and_target_scopes(monkeypatch):
    module = singlecell()
    prohibit_numerical_import(monkeypatch)
    for case_id in module.supported_biology_singlecell_cases():
        changes = {}
        if case_id == "T14-one-donor":
            changes["independent_units_per_group"] = 1
        if case_id == "T16-age-mismatch":
            changes.update(time_grid=(0.0, 1.0, 2.0), time_units="days")
        plan = module.validate_biology_singlecell_recipe(recipe(case_id, **changes))
        assert plan.target_scopes
        assert len(dict(plan.target_scopes)) == len(plan.target_scopes)
        expected_measurement = "non_umi_counts" if case_id == "T18-non-umi-censoring" else "umi_counts"
        assert plan.measurement == expected_measurement


@pytest.mark.parametrize("case_id,changes,reason", [
    ("T14-one-donor", {}, "one independent donor"),
    ("T14-state-only", {"independent_units_per_group": 1}, "three independent donors"),
    ("T14-state-only", {"cells_per_specimen": 0}, "cells per specimen"),
    ("T14-state-only", {"cell_type_count": 1}, "cell types"),
    ("T14-state-only", {"num_genes": 30}, "64 to 5000"),
    ("T14-state-only", {"technical_libraries_per_specimen": 2}, "technical resequencing"),
    ("T14-state-only", {"stored_expression_layers": 2}, "one expression layer"),
    ("T14-state-only", {"condition_labels": ("only",)}, "exactly 2"),
    ("T17-cycle-target", {"condition_labels": ("a", "b")}, "exactly 1"),
    ("T16-age-mismatch", {}, "three declared collection ages"),
    ("T20-double-perturbation", {"condition_labels": ("a", "b")}, "exactly 4"),
    ("T18-umi-sampling", {"dose_grid": (0., 1.), "dose_units": "arbitrary"}, "dose-dependent"),
])
def test_incoherent_designs_are_rejected_without_execution(case_id, changes, reason):
    with pytest.raises(ValueError, match=reason):
        singlecell().validate_biology_singlecell_recipe(recipe(case_id, **changes))


def test_private_and_observation_layer_budget_is_checked_before_execution():
    with pytest.raises(ValueError, match="stored numeric layers"):
        singlecell().validate_biology_singlecell_recipe(recipe(max_matrix_bytes=240000))


def test_per_target_refusal_preserves_answerable_parts():
    module = singlecell()
    plan = module.validate_biology_singlecell_recipe(recipe("T15-convergence-unmarked"))
    assert dict(plan.target_scopes) == {"state_topology": "supported", "ancestry": "refusal"}
    plan = module.validate_biology_singlecell_recipe(recipe("T16-missing-intermediate"))
    assert dict(plan.target_scopes)["population_abundance"] == "refusal"
    plan = module.validate_biology_singlecell_recipe(recipe("T19-motif-supported"))
    assert dict(plan.target_scopes)["motif_occurrence"] == "limited_reference"
    assert dict(plan.target_scopes)["binding"] == "refusal"


@pytest.mark.parametrize("case_id", ["T15-linear", "T15-bifurcation", "T15-convergence-history"])
def test_unoriented_history_is_only_a_limited_origin_reference(case_id):
    scopes = dict(singlecell().validate_biology_singlecell_recipe(recipe(case_id)).target_scopes)
    assert scopes["state_topology"] == "supported"
    assert scopes["ancestry"] == "limited_reference"


def test_negative_controls_do_not_support_successful_intervention_effect():
    scopes = dict(singlecell().validate_biology_singlecell_recipe(recipe("T20-control")).target_scopes)
    assert scopes["assignment_effect"] == "supported"
    assert scopes["successful_intervention"] == "refusal"


@pytest.mark.parametrize("num_genes,expected", [(64, 16), (120, 16), (1000, 100), (5000, 200)])
def test_enrichment_query_size_is_fixed_bounded_configuration(num_genes, expected, monkeypatch):
    module = singlecell()
    prohibit_numerical_import(monkeypatch)
    assert module._enrichment_query_size(num_genes) == expected
    assert 0 < expected < num_genes


@pytest.mark.parametrize("num_genes", [0, 63, 5001, True])
def test_enrichment_query_size_rejects_an_incomplete_or_unbounded_universe(num_genes):
    with pytest.raises(ValueError, match="complete 64 to 5000"):
        singlecell()._enrichment_query_size(num_genes)


@pytest.mark.skipif(__import__("os").environ.get("DEVBENCH_RUN_LOCAL_FIXTURES") != "1", reason="User hold: biological data generation and numerical qualification are not authorized")
class TestSingleCellNumericalQualification:
    """No arrays, metadata fixtures, RNGs or models are created on collection."""

    def test_abundance_only_has_no_within_type_expression_effect(self):
        import numpy as np
        result = singlecell().generate_biology_singlecell(recipe("T14-abundance-only"), allow_data_execution=True)
        target = result.private_truth["targets"]["cell_state_effect"]
        assert not np.any(target["nonnull"])
        assert np.any(result.private_truth["targets"]["captured_abundance"]["effect"])

    def test_cancellation_is_on_abundance_not_opposite_log_fold_changes(self):
        import numpy as np
        result = singlecell().generate_biology_singlecell(recipe("T14-cancellation"), allow_data_execution=True)
        baseline = result.private_truth["baseline_type_profiles"]
        changes = baseline * (np.exp2(result.private_truth["targets"]["cell_state_effect"]["effect"]) - 1)
        assert np.allclose(changes.mean(axis=1), 0)
        assert np.any(changes > 0) and np.any(changes < 0)

    def test_reversed_history_changes_truth_without_changing_snapshots(self):
        import numpy as np
        observed = singlecell().generate_biology_singlecell(recipe("T15-convergence-unmarked"), allow_data_execution=True)
        reversed_case = singlecell().generate_biology_singlecell(recipe("T15-direction-reversed"), allow_data_execution=True)
        assert np.array_equal(observed.expression, reversed_case.expression)
        assert np.allclose(observed.private_truth["trajectory"]["latent_time"], 1 - reversed_case.private_truth["trajectory"]["latent_time"])
        assert observed.private_truth["trajectory"]["graph"] != reversed_case.private_truth["trajectory"]["graph"]
        assert "history_markers" not in observed.public_annotations
        assert "history_markers" not in reversed_case.public_annotations
        assert observed.public_annotations["cell_annotation_reference"] == reversed_case.public_annotations["cell_annotation_reference"]

    def test_competing_target_switch_keeps_the_same_observations(self):
        import numpy as np
        maturation = singlecell().generate_biology_singlecell(recipe("T17-independent-axes"), allow_data_execution=True)
        cycle = singlecell().generate_biology_singlecell(recipe("T17-cycle-target"), allow_data_execution=True)
        assert np.array_equal(maturation.expression, cycle.expression)
        assert maturation.public_context["primary_process"] == "maturation"
        assert cycle.public_context["primary_process"] == "cell_cycle"

    def test_missing_intermediates_are_not_complete_population_reference(self):
        import numpy as np
        result = singlecell().generate_biology_singlecell(recipe("T16-missing-intermediate"), allow_data_execution=True)
        tau = result.private_truth["trajectory"]["latent_time"]
        assert not np.any((tau > .35) & (tau < .7))
        assert result.private_truth["targets"]["population_abundance"]["scope"] == "refusal"

    def test_unmeasured_variable_efficacy_does_not_identify_successful_intervention(self):
        result = singlecell().generate_biology_singlecell(recipe("T20-variable-efficacy"), allow_data_execution=True)
        assert "efficacy_measurement" not in result.public_annotations
        target = result.private_truth["targets"]["successful_intervention"]
        assert target["scope"] == "refusal" and "effect" not in target
        assert result.private_truth["targets"]["assignment_effect"]["scope"] == "supported"

    def test_indirect_cascade_does_not_certify_direct_regulation(self):
        result = singlecell().generate_biology_singlecell(recipe("T20-indirect-cascade"), allow_data_execution=True)
        truth = result.private_truth["perturbation"]
        assert any(source == truth["nodes"][2] for source, _, _ in truth["structural_edges"])
        assert result.private_truth["targets"]["direct_edge"]["scope"] == "refusal"

    def test_double_perturbation_has_nonadditive_production_truth(self):
        import numpy as np
        result = singlecell().generate_biology_singlecell(recipe("T20-double-perturbation"), allow_data_execution=True)
        assert np.any(result.private_truth["targets"]["epistasis"]["effect"] > 0)
        factors = result.private_truth["perturbation"]["mean_production_factor"]
        target = result.private_truth["targets"]["epistasis"]
        assert np.allclose(target["effect"], (factors[:, 3] - factors[:, 1] - factors[:, 2] + factors[:, 0]) / factors[:, 0])
        assert target["effect_units"] == "dimensionless_control_normalized_mean_nonadditivity"
        assert result.public_context["target_contrasts"]["epistasis"]["formula"] == target["contrast"]

    def test_control_assignments_have_null_population_response(self):
        import numpy as np
        result = singlecell().generate_biology_singlecell(recipe("T20-control"), allow_data_execution=True)
        assert not np.any(result.private_truth["targets"]["assignment_effect"]["effect"])
        successful = result.private_truth["targets"]["successful_intervention"]
        assert successful["scope"] == "refusal" and "effect" not in successful

    def test_annotations_do_not_supply_true_cell_labels_or_hidden_seed(self):
        result = singlecell().generate_biology_singlecell(recipe(), allow_data_execution=True)
        public = repr((result.metadata, result.public_context, result.public_annotations))
        for forbidden in ("generation_seed", "latent_time", "T14-state-only"):
            assert forbidden not in public
        def public_keys(value):
            if isinstance(value, dict):
                return set(value) | set().union(*(public_keys(item) for item in value.values()))
            if isinstance(value, (tuple, list)):
                return set().union(*(public_keys(item) for item in value))
            return set()
        # Naming the requested cell_state_effect target does not disclose the
        # private state_effect array; reject the private key, not substrings.
        assert "state_effect" not in public_keys((result.metadata, result.public_context, result.public_annotations))
        assert all("cell_type" not in row for row in result.metadata)
        assert result.public_context["biological_unit"] == "donor"

    def test_donors_are_preserved_with_repeated_specimens(self):
        result = singlecell().generate_biology_singlecell(recipe(repeated_specimens_per_unit=2), allow_data_execution=True)
        assert len({row["donor_id"] for row in result.metadata}) == 6
        assert len({row["specimen_id"] for row in result.metadata}) == 12
        assert all(row["capture_batch"] == row["batch"] for row in result.metadata)
        assert result.public_context["metadata_aliases"] == {"batch": "capture_batch"}
        assert result.expression.shape == (120, 480)

    def test_protocol_layers_are_integer_and_do_not_fabricate_velocity(self):
        import numpy as np
        for case in ("T18-umi-sampling", "T18-non-umi-censoring"):
            result = singlecell().generate_biology_singlecell(recipe(case), allow_data_execution=True)
            assert np.issubdtype(result.expression.dtype, np.integer)
            assert np.all(result.expression >= 0)
            assert result.public_context["spliced_unspliced_layers_available"] is False
        assert result.measurement == "non_umi_counts"

    def test_decoy_priors_are_not_private_edges_and_binding_is_unavailable(self):
        result = singlecell().generate_biology_singlecell(recipe("T19-decoy-priors"), allow_data_execution=True)
        assert "regulon_annotation" in result.public_annotations
        assert "motif_hit_annotation" in result.public_annotations
        assert result.private_truth["targets"]["binding"]["scope"] == "refusal"
        assert result.private_truth["regulatory"]["gene_causal_orientation_identifiable"] is False

    @pytest.mark.parametrize("case_id,same_type", [("T18-homotypic-doublet", True), ("T18-heterotypic-doublet", False)])
    def test_doublets_have_two_valid_components_of_the_prescribed_type(self, case_id, same_type):
        import numpy as np
        result = singlecell().generate_biology_singlecell(recipe(case_id), allow_data_execution=True)
        truth = result.private_truth
        mask = truth["measurement"]["doublet_mask"]
        partner = truth["measurement"]["second_component_profile_index"][mask]
        assert np.any(mask) and np.all(partner >= 0)
        assert np.all((truth["cell_type"][mask] == truth["cell_type"][partner]) == same_type)
        assert np.all(truth["specimen_index"][mask] == truth["specimen_index"][partner])

    def test_ambient_reference_is_an_independent_count_assay(self):
        import numpy as np
        result = singlecell().generate_biology_singlecell(recipe("T18-ambient-reference"), allow_data_execution=True)
        reference = result.public_annotations["ambient_reference"]["counts"]
        assert reference.shape == (len(result.gene_ids),)
        assert np.issubdtype(reference.dtype, np.integer)
        assert np.all(result.private_truth["measurement"]["ambient_fraction"] > 0)
        assert result.private_truth["targets"]["clean_cell_state"]["scope"] == "supported"

    def test_role_streams_are_reproducible_and_separated(self):
        import numpy as np
        configuration = recipe()
        first = singlecell().generate_biology_singlecell(configuration, allow_data_execution=True)
        repeated = singlecell().generate_biology_singlecell(configuration, allow_data_execution=True)
        other_role = singlecell().generate_biology_singlecell(configuration.model_copy(update={"role": "evaluation"}), allow_data_execution=True)
        assert np.array_equal(first.expression, repeated.expression)
        assert not np.array_equal(first.expression, other_role.expression)
        assert first.gene_ids != other_role.gene_ids

    @pytest.mark.parametrize("case_id", ["T15-convergence-history", "T15-convergence-unmarked"])
    def test_convergent_source_program_is_exhausted_at_the_declared_merge(self, case_id):
        import numpy as np
        result = singlecell().generate_biology_singlecell(recipe(case_id), allow_data_execution=True)
        trajectory = result.private_truth["trajectory"]
        merged = trajectory["latent_time"] >= trajectory["merge_time"]
        assert np.any(merged)
        assert np.all(trajectory["lineage_program_concentration"][merged] == 0)
        assert np.all(trajectory["observable_state_index"][merged] == 0)
        assert ("merged", "terminal") in trajectory["graph"]
        if case_id.endswith("history"):
            ancestry = result.private_truth["targets"]["ancestry"]
            assert ancestry["scope"] == "limited_reference" and not ancestry["direction_identifiable"]
            assert "simulation_direction" not in ancestry
            assert result.public_annotations["history_markers"]["direction_anchor_available"] is False

    def test_cycle_reference_is_invariant_under_reflection_without_direction_evidence(self):
        result = singlecell().generate_biology_singlecell(recipe("T17-independent-axes"), allow_data_execution=True)
        target = result.private_truth["targets"]["cycle_state"]
        assert target["rotation_invariant"] and target["reflection_invariant"]
        assert result.public_context["cycle_phase_invariances"] == ("rotation", "reflection")

    def test_inactive_tf_candidates_have_no_planted_activity_coefficients(self):
        import numpy as np
        result = singlecell().generate_biology_singlecell(recipe("T19-expression-only"), allow_data_execution=True)
        regulatory = result.private_truth["regulatory"]
        assert np.all(regulatory["loadings"][regulatory["annotated_tf_indices"][4:], :] == 0)

    def test_cell_type_target_axes_preserve_the_internal_effect_order(self):
        result = singlecell().generate_biology_singlecell(recipe(), allow_data_execution=True)
        state = result.private_truth["targets"]["cell_state_effect"]
        abundance = result.private_truth["targets"]["captured_abundance"]
        assert state["gene_ids"] == result.gene_ids
        assert state["cell_type_labels"] == abundance["cell_type_labels"] == result.private_truth["type_labels"]
        assert state["effect"].shape == (len(state["gene_ids"]), len(state["cell_type_labels"]))
        assert abundance["population_fractions"].shape == (len(abundance["condition_labels"]), len(abundance["cell_type_labels"]))
        public_labels = {row["label"] for row in result.public_annotations["cell_annotation_reference"]["markers"]}
        assert set(state["cell_type_labels"]) == public_labels

    def test_activity_target_axes_do_not_make_latent_activity_measured_or_causal(self):
        result = singlecell().generate_biology_singlecell(recipe("T19-expression-only"), allow_data_execution=True)
        target = result.private_truth["targets"]["association_activity"]
        assert target["gene_ids"] == result.gene_ids
        assert target["observation_ids"] == result.observation_ids
        assert len(target["candidate_tf_ids"]) == 8
        assert target["activity_factor_tf_ids"] == target["loadings_column_ids"]
        assert target["loadings"].shape == (len(target["gene_ids"]), len(target["loadings_column_ids"]))
        assert target["activity"].shape == (len(target["activity_factor_tf_ids"]), len(target["observation_ids"]))
        assert set(target["activity_reference_equivalence"]) == {"factor_permutation", "factor_sign", "factor_scale", "orthogonal_rotation"}
        assert not target["activity_is_measured"] and not target["causal_activity_identifiable"]
        assert not target["factor_ids_are_causal_tf_identity"]
        public = result.public_context
        assert set(public["transcription_factor_annotation"]) == set(target["candidate_tf_ids"])
        assert "activity_factor_tf_ids" not in public and "loadings_column_ids" not in public

    def test_one_donor_abundance_uses_realized_subset_instead_of_population_truth(self):
        import numpy as np
        result = singlecell().generate_biology_singlecell(
            recipe("T14-one-donor", independent_units_per_group=1), allow_data_execution=True)
        target = result.private_truth["targets"]["captured_abundance"]
        types = result.private_truth["cell_type"]
        expected_counts = np.stack([
            np.bincount(types[np.asarray([row["condition"] == condition for row in result.metadata])],
                        minlength=len(target["cell_type_labels"]))
            for condition in target["condition_labels"]
        ])
        expected_fractions = expected_counts / expected_counts.sum(axis=1, keepdims=True)
        assert np.allclose(target["realized_captured_fractions"], expected_fractions)
        assert np.allclose(target["effect"], expected_fractions[1] - expected_fractions[0])
        assert target["scope"] == "limited_reference" and target["reference_scope"] == "observed_captured_subset"
        assert "population_fractions" not in target
        assert "unsupported_simulation_population_fractions" in target
        assert result.public_context["population_reference"] == "observed captured subset"
        public_reference = result.public_context["target_references"]["captured_abundance"]
        assert public_reference == {field: target[field] for field in
                                    ("estimand", "contrast", "units", "reference_population", "prediction_reference")}

    @pytest.mark.parametrize("case_id", ["T18-umi-sampling", "T18-homotypic-doublet", "T18-heterotypic-doublet", "T18-ambient-unresolved"])
    def test_cell_identity_reference_excludes_unordered_mixed_multiplets(self, case_id):
        import numpy as np
        result = singlecell().generate_biology_singlecell(recipe(case_id), allow_data_execution=True)
        target = result.private_truth["targets"]["clean_cell_state"]
        doublet = result.private_truth["measurement"]["doublet_mask"]
        eligible = target["clean_identity_reference_mask"]
        assert np.all(eligible[~doublet])
        if case_id == "T18-heterotypic-doublet":
            assert not np.any(eligible[doublet])
        else:
            assert np.all(eligible)
        assert target["reference_unit"] == "captured_observation"
        assert target["class_label_invariances"] == ("label_permutation",)
        assert target["clean_identity_numerical_reference_available"] == (case_id != "T18-ambient-unresolved")
        assert not target["mask_establishes_artifact_identifiability"]
        assert target["artifact_detection_calibration"] == "unavailable"
        assert "clean_identity_reference_mask" not in result.public_context
        assert all("clean_identity_reference_mask" not in row for row in result.metadata)

    def test_enrichment_query_uses_the_same_observations_independently_of_prior_choice(self):
        import numpy as np
        annotated = singlecell().generate_biology_singlecell(recipe("T19-motif-supported"), allow_data_execution=True)
        decoys = singlecell().generate_biology_singlecell(recipe("T19-decoy-priors"), allow_data_execution=True)
        assert np.array_equal(annotated.expression, decoys.expression)
        assert annotated.public_context["enrichment_query_gene_ids"] == decoys.public_context["enrichment_query_gene_ids"]
        query = annotated.public_context["enrichment_query_gene_ids"]
        assert len(query) == 16 and set(query) < set(annotated.gene_ids)
        assert annotated.public_context["enrichment_background_gene_ids"] == annotated.gene_ids
        for target_id in ("regulon_membership", "motif_occurrence"):
            target = annotated.private_truth["targets"][target_id]
            assert target["enrichment_query_gene_ids"] == query
            assert target["same_expression_selection"] and not target["calibrated_enrichment_p_values_or_fdr"]
            assert not target["independent_selection_assay"] and not target["causal_evidence"]

    def test_observed_query_selection_keeps_raw_counts_and_breaks_ties_by_gene_id(self):
        import numpy as np
        counts = np.ones((64, 3), dtype=np.int64)
        original = counts.copy()
        gene_ids = tuple(f"g_{index:03d}" for index in reversed(range(64)))
        selected = singlecell()._observed_enrichment_query(np, counts, gene_ids, max_working_bytes=100000)
        assert selected["enrichment_query_gene_ids"] == tuple(sorted(gene_ids)[:16])
        assert np.array_equal(counts, original)
        assert selected["enrichment_query_selection"]["positive_variance_gene_count"] == 0
        with pytest.raises(ValueError, match="working-buffer"):
            singlecell()._observed_enrichment_query(np, counts, gene_ids, max_working_bytes=1)

    def test_expression_only_case_has_no_unsupported_enrichment_query(self):
        result = singlecell().generate_biology_singlecell(recipe("T19-expression-only"), allow_data_execution=True)
        assert "enrichment_query_gene_ids" not in result.public_context
        assert result.private_truth["targets"]["regulon_membership"]["scope"] == "refusal"
        assert result.private_truth["targets"]["motif_occurrence"]["scope"] == "refusal"

    @pytest.mark.parametrize("cell_type_count", [3, 4, 5])
    @pytest.mark.parametrize("case_id,target_ids", [
        ("T15-bifurcation", ("state_topology",)),
        ("T16-balanced-capture", ("sampled_state_geometry",)),
        ("T17-independent-axes", ("maturation_order", "cycle_state")),
    ])
    def test_shared_process_projection_preserves_full_type_biology(self, cell_type_count, case_id, target_ids):
        import numpy as np
        result = singlecell().generate_biology_singlecell(recipe(case_id, cell_type_count=cell_type_count), allow_data_execution=True)
        projection = result.public_context["shared_process_projection"]
        markers = result.public_annotations["cell_annotation_reference"]
        excluded = {gene for entry in markers["markers"] for gene in entry["gene_ids"]}
        assert set(projection["excluded_feature_ids"]) == excluded
        retained = tuple(gene for gene in result.gene_ids if gene not in excluded)
        assert projection["retained_feature_universe"] == retained
        profiles = result.private_truth["baseline_type_profiles"]
        retained_rows = [index for index, gene in enumerate(result.gene_ids) if gene in set(retained)]
        excluded_rows = [index for index, gene in enumerate(result.gene_ids) if gene in excluded]
        assert np.all(profiles[retained_rows] == profiles[retained_rows, :1])
        assert np.any(profiles[excluded_rows] != profiles[excluded_rows, :1])
        assert result.expression.shape == (len(result.gene_ids), len(result.observation_ids))
        assert np.issubdtype(result.expression.dtype, np.integer)
        assert not markers["query_cell_labels_supplied"]
        assert all("cell_type" not in row for row in result.metadata)
        assert projection["full_counts_preserved"] and projection["all_observations_retained"]
        assert projection["full_geometry_scope"] == "not_claimed"
        assert not projection["marker_genes_are_nuisance_for_other_questions"]
        for target_id in target_ids:
            target = result.private_truth["targets"][target_id]
            assert target["shared_process_projection"] == projection
            assert target["projected_baseline_type_invariant"]
            assert not target["full_type_by_state_geometry_scored"]
        full = result.private_truth["unscored_full_type_by_state"]
        assert np.array_equal(full["type_baseline_profiles"], profiles)
        assert not full["scored_by_shared_process_quotient"]

    @pytest.mark.parametrize("case_id", ["T20-randomized", "T20-indirect-cascade", "T20-double-perturbation", "T20-control"])
    def test_achieved_dose_assay_is_bound_to_public_target_ids_and_prescribed_scale(self, case_id):
        result = singlecell().generate_biology_singlecell(recipe(case_id), allow_data_execution=True)
        assignment = result.public_annotations["guide_assignments"]
        efficacy = result.public_annotations["efficacy_measurement"]
        assigned_targets = {target for row in assignment["records"] for target in row["assigned_targets"]}
        assert set(efficacy["target_to_column"]) == assigned_targets
        assert efficacy["units"] == "achieved_intervention_dose_reduction"
        assert efficacy["reference_value"] == 0.0 and efficacy["full_intervention_value"] == 1.0
        assert "not 100 percent transcript knockdown" in efficacy["scale_definition"]
        assert efficacy["assay_qualification"] == "not_empirically_qualified"
        assumptions = efficacy["identification_assumptions"]
        assert assumptions["randomized_assignment"] and assumptions["randomization_unit"] == "donor"
        assert assumptions["target_specificity_assumed"] and assumptions["exclusion_restriction_assumed"]
        assert assumptions["consistency_assumed"] and assumptions["no_between_donor_interference_assumed"]
        assert not assumptions["measurement_is_expression_estimated"]
        doses = {row["cell_id"]: row for row in efficacy["records"]}
        for record in assignment["records"]:
            for target, column in efficacy["target_to_column"].items():
                expected_dose = efficacy["full_intervention_value"] if target in record["assigned_targets"] else efficacy["reference_value"]
                assert doses[record["cell_id"]][column] == expected_dose
        if case_id == "T20-control":
            assert not efficacy["target_to_column"]
            assert result.private_truth["targets"]["successful_intervention"]["scope"] == "refusal"
        public = repr(efficacy)
        assert "structural_edges" not in public and "mean_production_factor" not in public
        assert case_id not in public

    @pytest.mark.parametrize("case_id", ["T20-variable-efficacy", "T20-off-target"])
    def test_unobserved_or_nonspecific_achievement_has_no_public_identification_certificate(self, case_id):
        result = singlecell().generate_biology_singlecell(recipe(case_id), allow_data_execution=True)
        assert "efficacy_measurement" not in result.public_annotations
        assert result.private_truth["targets"]["successful_intervention"]["scope"] == "refusal"

    def test_missing_intermediate_scores_disconnected_observed_support_without_a_hidden_bridge(self):
        import numpy as np
        result = singlecell().generate_biology_singlecell(recipe("T16-missing-intermediate"), allow_data_execution=True)
        target = result.private_truth["targets"]["sampled_state_geometry"]
        assert "latent_time" not in target
        assert not target["full_latent_graph_scored"]
        assert not target["between_component_order_identifiable"]
        assert not target["unobserved_connectivity_identifiable"]
        assert np.array_equal(target["branch"], target["observed_component_ids"])
        incident = [node for edge in target["graph"] for node in edge]
        assert len(incident) == len(set(incident)), "observed segments must not share a hidden fork/bridge"
        assert set(incident) <= set(target["observed_support_nodes"])
        for component in np.unique(target["observed_component_ids"]):
            members = np.flatnonzero(target["observed_component_ids"] == component)
            values = [target["within_component_order"][int(index)] for index in members]
            if len(members) >= 2:
                assert min(values) == 0.0 and max(values) == 1.0
            else:
                assert values == [None]
        full = result.private_truth["unscored_full_process_geometry"]
        assert not full["scored"] and full["graph"] != target["graph"]
        assert target["support_gap_contract"]["scope"] == "observed_support_components_only"
        assert not target["support_gap_contract"]["latent_gap_coordinates_disclosed"]
        # Keep the inferred answer private: all sampling variants receive the
        # same observed-support question, without a disconnected-graph hint.
        assert "support_gap_contract" not in result.public_context
        assert "disconnected represented support" not in repr(result.public_context)
