"""Pure catalog and configuration checks; no observations, RNG or analysis."""

import ast
from dataclasses import FrozenInstanceError
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from devbench.study.biology import (
    BiologyCatalog, BiologyRecipe, CapabilityRegistration, EffectProgram,
    GenerationParameters, RuntimeSupportRegistry, check_biology_capabilities,
    load_biology_catalog, validate_role_separation,
)


def changed(model, **updates):
    return type(model)(**{**model.model_dump(), **updates})


def recipe(**updates):
    values = dict(recipe_id="recipe-training", template_id="T01", case_id="T01-dense",
                  role="training", generation_seed=None, mechanism_holdout_group="smooth-development",
                  parameters=GenerationParameters(num_genes=20, independent_units_per_group=3,
                                                  condition_labels=("A", "B"),
                                                  time_grid=tuple(float(i) for i in range(12)),
                                                  time_units="days"))
    return BiologyRecipe(**{**values, **updates})


def test_catalog_is_complete_static_and_round_trips():
    catalog = load_biology_catalog()
    assert len(catalog.templates) == 20
    assert len(catalog.effects) == 30
    assert sum(len(t.cases) for t in catalog.templates) == 97
    assert BiologyCatalog.model_validate_json(catalog.model_dump_json()) == catalog
    assert catalog.status == "configuration_only_no_data"
    assert all(t.generator_support == "proposed_not_registered" for t in catalog.templates)
    assert all(t.grading_support == "proposed_not_registered" for t in catalog.templates)
    assert all(t.qualification_status == "not_generated_not_calibrated_not_evaluated" for t in catalog.templates)
    assert all(c.status == "configured_not_generated" for t in catalog.templates for c in t.cases)
    assert all(s.status == "candidate_not_acquired_or_qualified" for s in catalog.sources)


def test_catalog_keeps_targets_separate_and_allows_zero_regulators():
    catalog = load_biology_catalog()
    aliased = next(c for c in catalog.template("T06").cases if c.case_id == "T06-aliased")
    assert {x.target_id: x.expected_scope for x in aliased.target_scopes} == {
        "adjusted_effect": "refusal", "observed_difference": "limited_reference"}
    assert all("master_regulators" not in t.required_answer_fields
               for template in catalog.templates for t in template.targets)
    regulatory = catalog.template("T19")
    assert "candidate_regulators" in regulatory.targets[0].required_answer_fields
    assert all(next(s for s in c.target_scopes if s.target_id == "binding").expected_scope == "refusal"
               for c in regulatory.cases)


def test_case_effects_are_coherent_and_annotations_never_alias_truth():
    catalog = load_biology_catalog()
    for template in catalog.templates:
        cases = {c.case_id: c for c in template.cases}
        for case in cases.values():
            assert not set(case.public_annotation_ids) & set(case.private_truth_objects)
            assert set(case.effect_ids) <= set(template.effect_ids)
            if case.paired_case_id:
                assert case.paired_case_id in cases
                assert case.paired_minimal_change
    sex_case = catalog.template("T08").cases[0]
    genotype_case = catalog.template("T08").cases[2]
    assert sex_case.effect_ids == ("E13",)
    assert genotype_case.effect_ids == ("E14",)
    unknown = next(c for c in catalog.template("T07").cases if c.case_id.endswith("unknown-identity"))
    assert unknown.biological_identity_status == "intentionally_unknown"


def test_every_target_declares_estimand_support_metrics_and_specific_tools():
    for template in load_biology_catalog().templates:
        for target in template.targets:
            assert target.estimand and target.contrast and target.measurement_units
            assert target.minimum_design_support.required_labels
            assert target.minimum_design_support.status == "declared_design_minimum_not_power_guarantee"
            assert target.tool_requirements and target.required_answer_fields
            assert all(m.calibration_status == "proposed_uncalibrated" for m in target.scientific_metrics)
            assert "unsupported_direct_causality" in target.forbidden_claims
            assert target.statistical_reporting.q_value_constraint == "finite_0_le_p_le_q_le_1"
    cellular = load_biology_catalog().template("T14")
    assert cellular.unit_hierarchy.cell_label == "cell_id"
    assert all("donor_batch_correction_establishes_replication" in t.forbidden_claims for t in cellular.targets)
    assert "no extra zero inflation by default" in load_biology_catalog().template("T18").observation_layers[0].observation_model


@pytest.mark.parametrize("field,value", [
    ("role", "test"), ("generation_seed", True), ("generation_seed", -1),
    ("generation_seed", "42"), ("status", "generated"),
    ("template_id", "T21"), ("recipe_id", "../private"),
])
def test_recipe_rejects_invalid_or_execution_claim_fields(field, value):
    with pytest.raises(ValidationError):
        changed(recipe(), **{field: value})


def test_recipes_and_nested_parameters_are_frozen():
    value = recipe()
    with pytest.raises(ValidationError):
        value.role = "evaluation"
    with pytest.raises(ValidationError):
        value.parameters.num_genes = 42
    with pytest.raises(FrozenInstanceError):
        RuntimeSupportRegistry().registrations = ()


def test_recipe_and_role_validation_never_assign_generation_seeds():
    first = recipe()
    first.validate_against(load_biology_catalog())
    second = changed(first, recipe_id="calibration", role="calibration")
    third = changed(first, recipe_id="evaluation", role="evaluation",
                    mechanism_holdout_group="biphasic-holdout")
    validate_role_separation((first, second, third))
    assert all(r.generation_seed is None for r in (first, second, third))
    seeded = tuple(changed(r, generation_seed=i) for i, r in enumerate((first, second, third)))
    validate_role_separation(seeded)
    assert seeded[0].parameters == seeded[1].parameters == seeded[2].parameters
    with pytest.raises(ValueError, match="generation seeds"):
        validate_role_separation((seeded[0], changed(seeded[1], generation_seed=0)))


@pytest.mark.parametrize("updates", [
    {"num_genes": 5001}, {"num_genes": True}, {"dispersion": float("nan")},
    {"nuisance_intensity": float("inf")}, {"independent_units_per_group": 0},
    {"time_grid": (0.0, 0.0)}, {"time_grid": (1.0, 0.0)}, {"time_units": None},
    {"dose_grid": (0.0, 1.0), "dose_units": "nM"},
    {"condition_labels": ("A", "A")},
])
def test_generation_parameters_are_bounded_without_constructing_observations(updates):
    with pytest.raises(ValidationError):
        changed(recipe().parameters, **updates)


def test_matrix_bound_counts_all_declared_expression_layers():
    parameters = GenerationParameters(num_genes=2000, independent_units_per_group=3,
                                      condition_labels=("A", "B"), cells_per_specimen=500)
    assert parameters.observation_count_upper_bound == 3000
    with pytest.raises(ValidationError, match="matrix budget"):
        changed(parameters, stored_expression_layers=3)


def test_mixture_structure_does_not_consume_a_disjoint_gene_partition():
    composition = EffectProgram(effect_id="E10", gene_fraction=1.0, amplitude=0.15,
                                amplitude_units="fraction_change")
    state = EffectProgram(effect_id="E11", gene_fraction=0.15, amplitude=1.0,
                          amplitude_units="log2_fold_change")
    parameters = changed(recipe().parameters, cell_type_count=3,
                         effect_programs=(composition, state))
    configured = recipe(template_id="T11", case_id="T11-composition-and-state", parameters=parameters)
    assert configured.validate_against(load_biology_catalog()).case_id == "T11-composition-and-state"
    with pytest.raises(ValidationError, match="whole mixture"):
        changed(composition, gene_fraction=0.5)
    extra = EffectProgram(effect_id="E01", gene_fraction=0.9, amplitude=1.0,
                          amplitude_units="log2_fold_change")
    with pytest.raises(ValidationError, match="fractions cannot exceed"):
        changed(parameters, effect_programs=(composition, state, extra))


def test_catalog_support_is_potential_not_realized_numerical_truth():
    catalog = load_biology_catalog()
    for template in catalog.templates:
        for target in template.targets:
            assert target.realization_requirements
        for case in template.cases:
            for scope in case.target_scopes:
                assert scope.support_interpretation == "declared_potential_requires_runtime_hypothesis_checks"
                assert "latent_value" not in type(scope).model_fields
    zero_amplitude = EffectProgram(effect_id="E01", gene_fraction=0.1, amplitude=0.0,
                                   amplitude_units="log2_fold_change")
    configured = recipe(parameters=changed(recipe().parameters, effect_programs=(zero_amplitude,)))
    assert configured.validate_against(catalog).case_id == "T01-dense"
    assert configured.generation_seed is None


def test_unverified_scale_and_no_achieved_intervention_cannot_claim_support():
    catalog = load_biology_catalog()
    controls = {c.case_id: c for c in catalog.template("T09").cases}
    assert controls["T09-valid-controls"].public_annotation_ids == ("declared_external_controls",)
    assert controls["T09-invalid-controls"].public_annotation_ids == ("declared_external_controls",)
    valid = {s.target_id: s.expected_scope for s in controls["T09-valid-controls"].target_scopes}
    invalid = {s.target_id: s.expected_scope for s in controls["T09-invalid-controls"].target_scopes}
    assert valid["absolute_rna"] == "supported" and invalid["absolute_rna"] == "refusal"
    control = next(c for c in catalog.template("T20").cases if c.case_id == "T20-control")
    scope = {s.target_id: s.expected_scope for s in control.target_scopes}
    assert scope["assignment_effect"] == "supported"
    assert scope["successful_intervention"] == "refusal"
    quality = {c.case_id: c for c in catalog.template("T12").cases}
    assert "gene_susceptibility" in quality["T12-quality-overlap"].public_annotation_ids
    assert "gene_susceptibility" not in quality["T12-quality-aliased"].public_annotation_ids


def test_shared_process_projection_is_explicit_and_target_specific():
    catalog = load_biology_catalog()
    selected = {"T15": {"state_topology"}, "T16": {"sampled_state_geometry"},
                "T17": {"maturation_order", "cycle_state"}}
    for template in catalog.templates:
        for target in template.targets:
            expected = target.target_id in selected.get(template.template_id, set())
            assert (target.shared_process_projection is not None) == expected
            if not expected:
                continue
            projection = target.shared_process_projection
            assert projection.analysis_layer == "counts"
            assert projection.all_observations_retained and projection.full_counts_preserved
            assert not projection.marker_genes_are_nuisance_for_other_questions
            assert {"shared_process_projection", "analysis_layer", "excluded_feature_ids",
                    "retained_feature_universe"} <= set(target.required_answer_fields)
            with pytest.raises(ValidationError):
                changed(projection, all_observations_retained=False)
            with pytest.raises(ValidationError):
                changed(projection, full_geometry_scope="full_all_gene_geometry")


def test_shared_process_template_rejects_missing_public_projection_evidence():
    template = load_biology_catalog().template("T15")
    case = template.cases[0]
    without_markers = changed(case, public_annotation_ids=tuple(
        key for key in case.public_annotation_ids if key != "cell_annotation_reference"))
    with pytest.raises(ValidationError, match="public marker and projection evidence"):
        changed(template, cases=(without_markers, *template.cases[1:]))
    scopes = tuple(changed(scope, available_public_evidence=tuple(
        key for key in scope.available_public_evidence if key != "shared_process_projection"))
        if scope.target_id == "state_topology" else scope for scope in case.target_scopes)
    without_projection = changed(case, target_scopes=scopes)
    with pytest.raises(ValidationError, match="public marker and projection evidence"):
        changed(template, cases=(without_projection, *template.cases[1:]))


def test_unsupported_case_combinations_and_insufficient_design_are_rejected():
    catalog = load_biology_catalog()
    with pytest.raises(ValueError, match="case must belong"):
        changed(recipe(), case_id="T02-dense").validate_against(catalog)
    with pytest.raises(ValueError, match="design support"):
        changed(recipe(), parameters=changed(recipe().parameters, independent_units_per_group=1)).validate_against(catalog)
    with pytest.raises(ValueError, match="cell counts"):
        changed(recipe(), template_id="T15", case_id="T15-linear").validate_against(catalog)
    with pytest.raises(ValueError, match="cell types"):
        changed(recipe(), template_id="T11", case_id="T11-complete-reference").validate_against(catalog)
    unrelated = EffectProgram(effect_id="E21", gene_fraction=0.1, amplitude=1.0,
                              amplitude_units="log2_fold_change")
    with pytest.raises(ValueError, match="coherent case"):
        changed(recipe(), parameters=changed(recipe().parameters, effect_programs=(unrelated,))).validate_against(catalog)


def test_unknown_catalog_fields_and_false_qualification_claims_are_rejected():
    catalog = load_biology_catalog()
    template = catalog.templates[0]
    with pytest.raises(ValidationError):
        changed(template, generator_support="implemented")
    with pytest.raises(ValidationError):
        changed(template, qualified=True)
    with pytest.raises(ValidationError, match="exactly once"):
        changed(catalog, templates=(catalog.templates[0], *catalog.templates[:-1]))
    with pytest.raises(ValidationError, match="disjoint"):
        changed(template.cases[0], public_annotation_ids=("latent_expression_means",))


def test_missing_support_is_reported_per_target_without_invoking_any_handler():
    status = check_biology_capabilities(recipe(), load_biology_catalog())
    assert {s.target_id for s in status} == {"sampled_mean", "event_timing"}
    assert all(not s.executable_handlers_registered for s in status)
    assert all("generator:generate.T01.v1" in s.missing_capabilities for s in status)
    assert all(s.qualification_status == "not_verified_by_configuration" for s in status)
    assert all(s.estimation_support == "not_assessed_by_callable_inventory" for s in status)
    aliased = changed(recipe(), template_id="T06", case_id="T06-aliased")
    adjusted = check_biology_capabilities(aliased, load_biology_catalog())[0]
    assert adjusted.expected_scope == "refusal"
    assert adjusted.tool_requirement_purpose == "observable_support_diagnostic"
    assert "tool:validate_design_matrix" in adjusted.missing_capabilities
    assert "tool:mean_condition_analysis" not in adjusted.missing_capabilities


def test_refusal_requirements_match_registered_observable_target_diagnostics():
    # These functions return passive declarations only; no handler is invoked.
    from devbench.study.biology_tools_bulk import bulk_tool_capabilities
    from devbench.study.biology_tools_singlecell import singlecell_tool_capabilities

    bulk, cellular = bulk_tool_capabilities(), singlecell_tool_capabilities()
    for template in load_biology_catalog().templates:
        methods = cellular if template.unit_hierarchy.cell_label else bulk
        for case in template.cases:
            for scope in case.target_scopes:
                assert "target_evidence_scope_audit" not in scope.tool_requirements
                if scope.expected_scope != "refusal":
                    continue
                target = next(t for t in template.targets if t.target_id == scope.target_id)
                assert scope.tool_requirement_purpose == "observable_support_diagnostic"
                for name in scope.tool_requirements or target.tool_requirements:
                    metadata = methods.get(name) or bulk.get(name)
                    assert metadata is not None, (case.case_id, scope.target_id, name)
                    # Empty target lists denote the existing common input/design
                    # prerequisites, not an estimator or a generic truth oracle.
                    assert not metadata["target_ids"] or scope.target_id in metadata["target_ids"]
                    assert metadata["qualification"] == "unvalidated_numerical_implementation"
                with pytest.raises(ValidationError, match="support diagnostics"):
                    changed(scope, tool_requirement_purpose="target_analysis")


def test_missing_state_intermediates_do_not_request_hidden_cross_gap_connectivity():
    template = load_biology_catalog().template("T16")
    case = next(c for c in template.cases if c.case_id == "T16-missing-intermediate")
    scope = next(s for s in case.target_scopes if s.target_id == "sampled_state_geometry")
    assert scope.expected_scope == "limited_reference"
    assert "support_gap_contract" in scope.available_public_evidence
    assert "disconnected observed-support components" in scope.required_limitation
    assert "private and unscored" in scope.required_limitation


def test_registry_rejects_noncallable_and_wildcard_support():
    with pytest.raises(ValueError, match="callable"):
        CapabilityRegistration("generator", "generate.T01.v1", "T01", ("T01-dense",),
                               ("sampled_mean",), "raw_counts", None)
    with pytest.raises(ValueError, match="exact identifiers"):
        CapabilityRegistration("tool", "validate_design_matrix", "T01", ("*",),
                               ("sampled_mean",), "raw_counts", len)


def test_new_module_imports_no_scientific_runtime_and_catalog_is_finite_json():
    module_path = Path(__file__).resolve().parents[1] / "devbench" / "study" / "biology.py"
    tree = ast.parse(module_path.read_text())
    imported = {n.module.split('.')[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
    imported |= {alias.name.split('.')[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for alias in n.names}
    assert not imported & {"random", "numpy", "pandas", "scipy", "devharness_server"}
    content = module_path.with_name("biology_catalog.json").read_text()
    raw = json.loads(content, parse_constant=lambda x: (_ for _ in ()).throw(ValueError(x)))
    assert raw["status"] == "configuration_only_no_data"
    with pytest.raises(ValueError, match="absolute"):
        load_biology_catalog(Path("biology_catalog.json"))
