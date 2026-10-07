"""Admission checks only run while biological data execution remains on hold.

Small local regression fixtures are enabled by DEVBENCH_RUN_LOCAL_FIXTURES=1.
This flag never authorizes live benchmark participants.
"""

from __future__ import annotations

import builtins
import importlib

import pytest

from devbench.study.biology import BiologyRecipe, EffectProgram, GenerationParameters, load_biology_catalog


def recipe(case_id="T01-dense", **parameters):
    return BiologyRecipe(
        recipe_id="contract-only", template_id=case_id.split("-")[0],
        case_id=case_id, role="calibration", generation_seed=17,
        mechanism_holdout_group="contract-only",
        parameters=GenerationParameters(**{
            "num_genes": 120, "independent_units_per_group": 4,
            "condition_labels": ("control", "treated"),
            "time_grid": tuple(float(i) for i in range(13)), "time_units": "hours",
            **parameters,
        }),
    )


def bulk():
    return importlib.import_module("devbench.study.biology_bulk")


def test_bulk_module_keeps_numerical_imports_lazy(monkeypatch):
    original = builtins.__import__

    def guarded(name, *args, **kwargs):
        assert name.split(".")[0] not in {"numpy", "pandas", "scipy"}
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded)
    importlib.reload(bulk())


@pytest.mark.parametrize("permission", [None, False, 0, 1, "true", [], {}])
def test_admission_requires_literal_true_before_even_reading_recipe(permission):
    class UnreadableRecipe:
        def __getattribute__(self, name):
            raise AssertionError("recipe inspected before execution authorization")

    with pytest.raises(PermissionError, match="allow_data_execution=True"):
        bulk().generate_biology_bulk(UnreadableRecipe(), allow_data_execution=permission)


def test_supported_case_list_is_exact_and_rejects_unknown_case():
    case_ids = bulk().supported_biology_bulk_cases()
    assert "T03-baseline-only" in case_ids
    assert "T11-rna-content-shift" in case_ids
    assert not any(case.startswith("T14-") for case in case_ids)
    with pytest.raises(ValueError, match="not implemented"):
        bulk().validate_biology_bulk_recipe(recipe("T01-made-up"))


def test_configuration_validation_does_not_import_numerical_runtime(monkeypatch):
    module = bulk()
    planned = recipe()
    original = builtins.__import__

    def guarded(name, *args, **kwargs):
        assert name.split(".")[0] not in {"numpy", "pandas", "scipy"}
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded)
    plan = module.validate_biology_bulk_recipe(planned)
    assert plan.observation_upper_bound == 104
    assert plan.expression_bytes == 120 * 104 * 8


def test_bulk_dimension_cap_rejects_before_runtime():
    with pytest.raises(ValueError, match="5000"):
        bulk().validate_biology_bulk_recipe(recipe(num_genes=5001))


def test_peak_dense_working_set_budget_is_checked_before_runtime():
    with pytest.raises(ValueError, match="working"):
        bulk().validate_biology_bulk_recipe(recipe(max_matrix_bytes=150000))


@pytest.mark.parametrize("case_id,changes,reason", [
    ("T02-dense", {"time_grid": (0., 1., 2., 3.)}, "dense"),
    ("T02-aliased", {}, "aliased"),
    ("T07-single-unit", {}, "one independent"),
    ("T07-technical", {}, "technical"),
    ("T07-repeated-donors", {}, "repeated"),
    ("T06-aliased", {"num_batches": 1}, "batch"),
    ("T04-bracketed", {}, "dose"),
])
def test_case_design_is_coherent_before_runtime(case_id, changes, reason):
    with pytest.raises(ValueError, match=reason):
        bulk().validate_biology_bulk_recipe(recipe(case_id, **changes))


def test_registry_covers_only_exact_bulk_catalog_cases_without_claiming_tool_support():
    catalog = load_biology_catalog()
    registrations = bulk().biology_bulk_registrations(catalog)
    registered = {case for registration in registrations for case in registration.case_ids}
    assert registered == set(bulk().supported_biology_bulk_cases())
    assert all(registration.kind == "generator" for registration in registrations)
    assert all(registration.handler is bulk().generate_biology_bulk for registration in registrations)


def test_each_registered_bulk_case_has_an_admissible_configuration_only():
    catalog = load_biology_catalog()
    for case_id in bulk().supported_biology_bulk_cases():
        template_id = case_id.split("-")[0]
        changes = {}
        if case_id in {"T01-sparse", "T01-unsampled-pulse", "T02-aliased"}:
            changes["time_grid"] = (0., 1., 2., 3., 4., 5.)
        if template_id == "T04":
            changes.update(time_grid=(), time_units=None, dose_grid=(0., 1., 3., 10., 30., 100.), dose_units="nM")
        if template_id == "T06":
            changes["num_batches"] = 2
        if case_id in {"T07-repeated-donors", "T07-single-unit"}:
            changes["repeated_specimens_per_unit"] = 13
        if case_id == "T07-single-unit":
            changes["independent_units_per_group"] = 1
        if case_id == "T07-technical":
            changes["technical_libraries_per_specimen"] = 2
        if case_id == "T08-genotype-balanced":
            changes["independent_units_per_group"] = 6
        if template_id == "T11":
            changes["cell_type_count"] = 3
        planned = bulk().validate_biology_bulk_recipe(recipe(case_id, **changes), catalog)
        assert planned.dense_working_bytes_upper_bound <= 100_000_000


def test_clustered_dense_rhythm_grid_cannot_claim_sampling_identification():
    with pytest.raises(ValueError, match="frequency domain"):
        bulk().validate_biology_bulk_recipe(recipe("T02-dense", time_grid=tuple(i / 100 for i in range(11)) + (1.,)))


def test_variance_target_below_poisson_bound_is_rejected_without_sampling():
    program = EffectProgram(effect_id="E09", gene_fraction=.5, amplitude=-8,
                            amplitude_units="log_variance_ratio")
    with pytest.raises(ValueError, match="Poisson variance bound"):
        bulk().validate_biology_bulk_recipe(recipe("T05-variance-only", effect_programs=(program,)))


def test_state_effect_cannot_consume_all_required_reference_markers():
    program = EffectProgram(effect_id="E11", gene_fraction=1., amplitude=1.,
                            amplitude_units="log2_fold_change")
    with pytest.raises(ValueError, match="unchanged reference markers"):
        bulk().validate_biology_bulk_recipe(recipe("T11-state-only", cell_type_count=3, effect_programs=(program,)))


def test_structural_zero_amplitude_cannot_be_given_a_fake_log_change():
    program = EffectProgram(effect_id="E08", gene_fraction=.15, amplitude=1.,
                            amplitude_units="log2_fold_change")
    with pytest.raises(ValueError, match="log effects are undefined"):
        bulk().validate_biology_bulk_recipe(recipe("T05-structural-zero", effect_programs=(program,)))


def test_invalid_contrast_sentinel_must_be_absent_from_observed_condition_configuration():
    configured = recipe("T06-invalid-contrast", num_batches=2,
                        condition_labels=("control", "unobserved_level"))
    bulk().validate_biology_bulk_recipe(configured)
    absent = bulk()._withheld_condition(configured)
    assert absent not in configured.parameters.condition_labels
    assert not any(hint in absent for hint in ("unobserved", "invalid", "T06", "refusal"))
    collision = recipe("T06-invalid-contrast", num_batches=2, condition_labels=("control", absent))
    assert bulk()._withheld_condition(collision) not in collision.parameters.condition_labels


@pytest.mark.parametrize("case_id,changes", [
    ("T08-sex-balanced", {"num_batches": 3}),
    ("T08-genotype-balanced", {"num_batches": 3, "independent_units_per_group": 6}),
    ("T08-sex-imbalanced", {"num_batches": 2}),
])
def test_covariate_support_rejects_unintended_batch_aliases(case_id, changes):
    with pytest.raises(ValueError, match="batch"):
        bulk().validate_biology_bulk_recipe(recipe(case_id, **changes))


@pytest.mark.skipif(__import__("os").environ.get("DEVBENCH_RUN_LOCAL_FIXTURES") != "1", reason="User hold: numerical generation and biological regression execution not authorized")
class TestNumericalBiologyRegression:
    """Future behavioral checks; no arrays or generator calls during collection."""

    def test_baseline_difference_is_not_condition_time_interaction(self):
        import numpy as np
        result = bulk().generate_biology_bulk(recipe("T03-baseline-only"), allow_data_execution=True)
        targets = result.private_truth["targets"]
        interaction = targets["time_interaction"]
        assert np.allclose(interaction["effect"], 0.)
        assert not np.any(interaction["nonnull"])
        assert np.max(result.private_truth["population_mean"]) > 0

    def test_all_null_has_no_planted_mean_effect(self):
        import numpy as np
        result = bulk().generate_biology_bulk(recipe("T05-whole-null"), allow_data_execution=True)
        mean = result.private_truth["population_mean"]
        assert np.allclose(mean, mean[:, :1])

    def test_structural_zero_genes_have_no_fabricated_log_effects(self):
        import numpy as np
        result = bulk().generate_biology_bulk(recipe("T05-structural-zero"), allow_data_execution=True)
        zeros = result.private_truth["mechanism"]["structural_zero_gene_mask"]
        assert np.any(zeros) and np.any(~zeros)
        assert np.all(result.expression[zeros] == 0)
        for target in result.private_truth["targets"].values():
            assert not np.any(target["hypothesis_testable"][zeros])
            assert all(value is None for value in target["effect"][zeros].reshape(-1))
            assert all(value is None for value in target["nonnull"][zeros])

    def test_continuous_assay_is_not_rounded_into_counts(self):
        import numpy as np
        result = bulk().generate_biology_bulk(recipe("T13-continuous"), allow_data_execution=True)
        assert result.measurement == "continuous"
        assert np.issubdtype(result.expression.dtype, np.floating)
        assert np.any(result.expression != np.round(result.expression))

    def test_mixture_cell_fractions_and_rna_fractions_are_distinct(self):
        import numpy as np
        result = bulk().generate_biology_bulk(recipe("T11-rna-content-shift", cell_type_count=3), allow_data_execution=True)
        mixture = result.private_truth["mixture"]
        assert np.allclose(mixture["cell_fractions"].sum(axis=0), 1)
        assert np.allclose(mixture["rna_fractions"].sum(axis=0), 1)
        assert not np.allclose(mixture["cell_fractions"], mixture["rna_fractions"])

    def test_opaque_ids_do_not_encode_case_or_truth(self):
        result = bulk().generate_biology_bulk(recipe(), allow_data_execution=True)
        assert all(value.startswith("g_") and "T01" not in value for value in result.gene_ids)
        public = repr(result.public_context)
        assert "generation_seed" not in public
        assert "private_truth" not in public
        assert "T01-dense" not in public

    def test_biological_donor_count_does_not_increase_with_technical_libraries(self):
        result = bulk().generate_biology_bulk(
            recipe("T07-technical", independent_units_per_group=3,
                   technical_libraries_per_specimen=2), allow_data_execution=True,
        )
        assert len({row["donor_id"] for row in result.metadata}) == 78
        assert len({row["sample_id"] for row in result.metadata}) == 156

    def test_nb_and_pln_share_marginal_targets_and_keep_integer_counts(self):
        import numpy as np
        left = bulk().generate_biology_bulk(recipe("T13-negative-binomial"), allow_data_execution=True)
        right = bulk().generate_biology_bulk(recipe("T13-poisson-lognormal"), allow_data_execution=True)
        assert np.array_equal(left.private_truth["population_mean"], right.private_truth["population_mean"])
        assert np.issubdtype(left.expression.dtype, np.integer)
        assert np.issubdtype(right.expression.dtype, np.integer)

    def test_variance_only_changes_variance_with_no_mean_de(self):
        import numpy as np
        result = bulk().generate_biology_bulk(recipe("T05-variance-only"), allow_data_execution=True)
        truth = result.private_truth["targets"]
        assert not np.any(truth["mean_contrast"]["nonnull"])
        assert np.any(truth["variability"]["nonnull"])
        affected = truth["variability"]["effect"][truth["variability"]["nonnull"]]
        assert np.allclose(affected, np.log(4))

    def test_zero_amplitude_has_no_phase_or_effective_concentration_reference(self):
        for case_id, effect_id in (("T02-dense", "E05"), ("T04-bracketed", "E06")):
            program = EffectProgram(effect_id=effect_id, gene_fraction=.5, amplitude=0,
                                    amplitude_units="log2_fold_change")
            changes = {"effect_programs": (program,)}
            if case_id.startswith("T04"):
                changes.update(time_grid=(), time_units=None,
                               dose_grid=(0., 1., 3., 10., 30., 100.), dose_units="nM")
            result = bulk().generate_biology_bulk(recipe(case_id, **changes), allow_data_execution=True)
            target = result.private_truth["targets"]["rhythm" if effect_id == "E05" else "effective_concentration"]
            assert not target["numerical_reference_available"]
            assert "period" not in target and "effective_concentration" not in target
            assert "phase_radians_by_condition" not in target

    def test_negative_rhythm_amplitude_is_canonical_at_a_shifted_time_origin(self):
        import numpy as np
        grid = tuple(float(40 + index) for index in range(13))
        results = []
        for amplitude in (1.3, -1.3):
            program = EffectProgram(effect_id="E05", gene_fraction=.5, amplitude=amplitude,
                                    amplitude_units="log2_fold_change")
            results.append(bulk().generate_biology_bulk(
                recipe("T02-phase-shift", time_grid=grid, effect_programs=(program,)),
                allow_data_execution=True,
            ))
        positive, negative = results
        pos_truth = positive.private_truth["targets"]["rhythm"]
        truth = negative.private_truth["targets"]["rhythm"]
        assert truth["phase_convention"] == "sine_plus_phase"
        assert truth["amplitude_convention"] == "nonnegative"
        assert truth["phase_reference_time"] == grid[0]
        assert truth["amplitude_log2_nonnegative"] == pytest.approx(1.3)
        domain = negative.public_context["rhythm_analysis_domain"]
        assert domain["phase_convention"] == "cosine_plus_phase"
        assert domain["phase_reference_time"] == grid[0]
        idx = truth["informative_gene_indices"]
        times = np.asarray(grid) - truth["phase_reference_time"]
        mean = negative.private_truth["population_mean"]
        baseline = negative.private_truth["mechanism"]["baseline"]
        for condition_index, condition in enumerate(("control", "treated")):
            phase = truth["phase_radians_by_condition"][condition]
            positive_phase = pos_truth["phase_radians_by_condition"][condition]
            difference = np.angle(np.exp(1j * (phase - positive_phase)))
            assert np.allclose(np.abs(difference), np.pi)
            reconstructed = truth["amplitude_log2_nonnegative"] * np.sin(
                2 * np.pi * times[None, :] / truth["period"] + phase[:, None])
            reconstructed *= np.exp(-truth["damping_per_time_unit"] * times[None, :])
            observed_curve = np.log2(mean[idx, condition_index * len(grid):(condition_index + 1) * len(grid)]
                                    / baseline[idx, None])
            assert np.allclose(reconstructed, observed_curve)

    def test_refusal_parameters_are_not_eligible_recovery_truth(self):
        result = bulk().generate_biology_bulk(
            recipe("T02-aliased", time_grid=(0., 1., 2., 3., 4., 5.)), allow_data_execution=True,
        )
        rhythm = result.private_truth["targets"]["rhythm"]
        assert rhythm["expected_refusal"]
        assert not rhythm["numerical_reference_available"]
        assert "period" not in rhythm
        assert rhythm["latent_simulation_truth"]["period"] > 0

    def test_u_shaped_response_has_two_half_response_roots(self):
        result = bulk().generate_biology_bulk(
            recipe("T04-u-shaped", time_grid=(), time_units=None,
                   dose_grid=(0., 1., 3., 10., 30., 100.), dose_units="nM"), allow_data_execution=True,
        )
        target = result.private_truth["targets"]["effective_concentration"]
        roots = target["half_response_root_set"]
        assert len(roots) == 2 and 0 < roots[0] < roots[1] < 100
        assert "effective_concentration" not in target

    def test_biphasic_timing_keeps_multiple_observed_events(self):
        result = bulk().generate_biology_bulk(recipe(), allow_data_execution=True)
        target = result.private_truth["targets"]["event_timing"]
        assert any(len(events) >= 2 for events in target["event_intervals"])

    def test_missingness_never_returns_unsupported_condition_time_cells(self):
        # A support failure is an explicit generation error, never a hidden retry.
        try:
            result = bulk().generate_biology_bulk(recipe("T12-mar"), allow_data_execution=True)
        except ValueError as error:
            assert "observed" in str(error)
            return
        groups = {}
        for row in result.metadata:
            groups.setdefault((row["condition"], row["time"]), set()).add(row["donor_id"])
        assert len(groups) == 26
        assert min(map(len, groups.values())) >= 3
