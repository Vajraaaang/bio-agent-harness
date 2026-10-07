"""Static biological coverage contracts; importing this module performs no I/O.

The catalog describes future experiments, not generated observations, calibrated
methods or execution authority. It deliberately does not extend the six legacy
temporal scenarios. Numerical generators and graders must register independently.
Recipes and case-level expected scopes are coordinator records: participant
projections must omit case labels, expected diagnostics, seeds and private truth.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Annotated, Callable, Literal

from pydantic import Field, model_validator

from .contracts import FrozenContract, Identifier, Seed


TemplateId = Annotated[str, Field(pattern=r"^T(0[1-9]|1[0-9]|20)$")]
EffectId = Annotated[str, Field(pattern=r"^E(0[1-9]|[12][0-9]|30)$")]
Text = Annotated[str, Field(min_length=1, max_length=3000)]
Measurement = Literal["raw_counts", "umi_counts", "non_umi_counts", "continuous"]
Family = Literal["bulk_temporal", "bulk_condition", "bulk_dose", "bulk_mixture",
                 "single_cell_state", "single_cell_topology", "regulatory", "perturbation"]
TargetScope = Literal["supported", "refusal", "limited_reference"]
Role = Literal["training", "calibration", "evaluation"]
Nonnegative = Annotated[float, Field(ge=0.0, allow_inf_nan=False)]
Fraction = Annotated[float, Field(ge=0.0, le=1.0, allow_inf_nan=False)]


def _unique(values: tuple, label: str) -> None:
    if len(values) != len(set(values)):
        raise ValueError(f"{label} must be distinct")


class SourceReference(FrozenContract):
    source_id: Identifier
    title: Text
    url: Annotated[str, Field(pattern=r"^https://")]
    use_scope: Text
    status: Literal["candidate_not_acquired_or_qualified"] = "candidate_not_acquired_or_qualified"


class EffectDefinition(FrozenContract):
    effect_id: EffectId
    name: Text
    biological_mechanism: Text
    observation_boundary: Text
    source_refs: tuple[Identifier, ...] = Field(min_length=1)


class MeasurementLayer(FrozenContract):
    measurement: Measurement
    units: Text
    observation_model: Text
    scale_scope: Literal["relative_abundance", "anchored_abundance", "normalized_expression"]
    analysis_route: Literal["count_likelihood", "continuous_likelihood"]

    @model_validator(mode="after")
    def route_matches_layer(self) -> MeasurementLayer:
        if (self.measurement == "continuous") != (self.analysis_route == "continuous_likelihood"):
            raise ValueError("continuous expression must not use a count likelihood")
        return self


class BiologicalUnitHierarchy(FrozenContract):
    population_unit: Literal["donor", "organism", "experimental_culture"]
    population_unit_label: Identifier
    specimen_label: Identifier = "specimen_id"
    library_label: Identifier = "library_id"
    cell_label: Identifier | None = None
    nesting: Text
    repeated_measure_policy: Text
    identity_status: Literal["declared_synthetic", "intentionally_unknown"] = "declared_synthetic"

    @model_validator(mode="after")
    def label_levels_are_distinct(self) -> BiologicalUnitHierarchy:
        _unique(tuple(x for x in (self.population_unit_label, self.specimen_label,
                                  self.library_label, self.cell_label) if x), "unit labels")
        return self


class DesignSupport(FrozenContract):
    """Proposed design minima, never claims of sufficient power or calibration."""

    minimum_independent_units_per_group: Annotated[int, Field(ge=1, le=1000)]
    minimum_timepoints: Annotated[int, Field(ge=0, le=100)] = 0
    minimum_dose_levels: Annotated[int, Field(ge=0, le=100)] = 0
    minimum_conditions: Annotated[int, Field(ge=1, le=20)] = 1
    required_labels: tuple[Identifier, ...] = Field(min_length=1)
    critical_confounders: tuple[Identifier, ...]
    additional_requirements: tuple[Text, ...] = Field(min_length=1)
    status: Literal["declared_design_minimum_not_power_guarantee"] = "declared_design_minimum_not_power_guarantee"


class ScientificMetric(FrozenContract):
    metric_id: Identifier
    reference_scope: Literal["private_simulation_truth", "public_annotation", "observable_design"]
    applicability: Text
    calibration_status: Literal["proposed_uncalibrated"] = "proposed_uncalibrated"


class StatisticalReportingContract(FrozenContract):
    applicability: Literal["when_a_gene_test_is_performed"] = "when_a_gene_test_is_performed"
    required_gene_fields: tuple[str, ...] = ("gene_id", "estimate_status", "coordinate_ids", "values", "p_value", "q_value", "missing_reason")
    multiplicity: Literal["declare_tested_family_and_BH_scope"] = "declare_tested_family_and_BH_scope"
    q_value_constraint: Literal["finite_0_le_p_le_q_le_1"] = "finite_0_le_p_le_q_le_1"
    failed_estimate_policy: Literal["unavailable_never_substituted_with_zero"] = "unavailable_never_substituted_with_zero"
    selection_policy: Literal["disclose_selection_conditional_inference"] = "disclose_selection_conditional_inference"


class SharedProcessProjectionContract(FrozenContract):
    """Target-specific feature view; observed feature IDs are bound at execution."""

    scope: Literal["shared_process_quotient"] = "shared_process_quotient"
    rule: Literal["exclude_union_of_declared_broad_type_identity_markers"] = "exclude_union_of_declared_broad_type_identity_markers"
    marker_reference_id: Literal["cell_annotation_reference"] = "cell_annotation_reference"
    analysis_layer: Literal["counts"] = "counts"
    full_geometry_scope: Literal["not_claimed"] = "not_claimed"
    all_observations_retained: Literal[True] = True
    full_counts_preserved: Literal[True] = True
    marker_genes_are_nuisance_for_other_questions: Literal[False] = False


class TargetContract(FrozenContract):
    target_id: Identifier
    estimand: Text
    measurement_units: Text
    reference_population: Text
    contrast: Text
    prediction_reference: Text
    required_observation_layers: tuple[Measurement, ...] = Field(
        min_length=1, description="Compatible layer alternatives; each case selects exactly one.")
    minimum_design_support: DesignSupport
    identification_assumptions: tuple[Text, ...] = Field(min_length=1)
    forbidden_claims: tuple[Identifier, ...] = Field(min_length=1)
    required_answer_fields: tuple[Identifier, ...] = Field(min_length=1,
        description="Canonical fields in TargetAnswer or its typed payload. Payload fields apply to estimates; refusals use target fields and grounded support checks.")
    coordinator_evaluation_fields: tuple[Identifier, ...] = ()
    scientific_metrics: tuple[ScientificMetric, ...] = Field(min_length=1)
    tool_requirements: tuple[Identifier, ...] = Field(min_length=1)
    scorer_capability_id: Identifier
    shared_process_projection: SharedProcessProjectionContract | None = None
    statistical_reporting: StatisticalReportingContract = Field(default_factory=StatisticalReportingContract)
    realization_requirements: tuple[Text, ...] = (
        "Check actual observed support and hypothesis-specific eligibility before assigning numerical truth; declared design support does not identify every realized hypothesis.",
    )

    @model_validator(mode="after")
    def distinct_fields(self) -> TargetContract:
        for field in ("required_answer_fields", "coordinator_evaluation_fields", "tool_requirements", "required_observation_layers"):
            _unique(getattr(self, field), field)
        if "master_regulators" in self.required_answer_fields:
            raise ValueError("fixed master_regulators output is not a universal biological contract")
        return self


class TargetScopeContract(FrozenContract):
    target_id: Identifier
    expected_scope: TargetScope
    available_public_evidence: tuple[Identifier, ...] = Field(min_length=1)
    required_limitation: Text
    numerical_scoring: Literal["eligible_if_estimated", "restricted_reference", "not_identifiable"]
    tool_requirements: tuple[Identifier, ...] = ()
    tool_requirement_purpose: Literal["target_analysis", "observable_support_diagnostic"] | None = None
    support_interpretation: Literal[
        "declared_potential_requires_runtime_hypothesis_checks"
    ] = "declared_potential_requires_runtime_hypothesis_checks"

    @model_validator(mode="after")
    def scope_matches_scoring(self) -> TargetScopeContract:
        expected = {"supported": "eligible_if_estimated", "refusal": "not_identifiable",
                    "limited_reference": "restricted_reference"}[self.expected_scope]
        if self.numerical_scoring != expected:
            raise ValueError("numerical scoring must respect target identifiability")
        purpose = "observable_support_diagnostic" if self.expected_scope == "refusal" else "target_analysis"
        if self.tool_requirement_purpose is None:
            object.__setattr__(self, "tool_requirement_purpose", purpose)
        elif self.expected_scope == "refusal" and self.tool_requirement_purpose != purpose:
            raise ValueError("refusal targets require observable support diagnostics, not estimation claims")
        return self


class BiologyCase(FrozenContract):
    case_id: Identifier
    description: Text
    mechanism_variant: Identifier
    observation_variant: Identifier
    design_variant: Identifier
    measurement: Measurement
    effect_ids: tuple[EffectId, ...] = Field(min_length=1)
    target_scopes: tuple[TargetScopeContract, ...] = Field(min_length=1)
    paired_case_id: Identifier | None = None
    paired_minimal_change: Text | None = None
    public_annotation_ids: tuple[Identifier, ...] = ()
    private_truth_objects: tuple[Identifier, ...] = Field(min_length=1)
    biological_identity_status: Literal["declared_synthetic", "intentionally_unknown"] = "declared_synthetic"
    intentionally_violated_support: tuple[Text, ...] = ()
    status: Literal["configured_not_generated"] = "configured_not_generated"

    @model_validator(mode="after")
    def validate_case(self) -> BiologyCase:
        _unique(self.effect_ids, "effect_ids")
        _unique(tuple(x.target_id for x in self.target_scopes), "case target ids")
        if (self.paired_case_id is None) != (self.paired_minimal_change is None):
            raise ValueError("paired cases require a minimal change description")
        if set(self.public_annotation_ids) & set(self.private_truth_objects):
            raise ValueError("public annotations and private simulation truth must be disjoint")
        return self


class BiologyTemplate(FrozenContract):
    template_id: TemplateId
    name: Text
    family: Family
    priority: Literal["P0", "P1"]
    effect_ids: tuple[EffectId, ...] = Field(min_length=1)
    source_refs: tuple[Identifier, ...] = Field(min_length=1)
    observation_layers: tuple[MeasurementLayer, ...] = Field(min_length=1)
    unit_hierarchy: BiologicalUnitHierarchy
    targets: tuple[TargetContract, ...] = Field(min_length=1)
    cases: tuple[BiologyCase, ...] = Field(min_length=2)
    generator_capability_id: Identifier
    generator_support: Literal["proposed_not_registered"] = "proposed_not_registered"
    tool_support: Literal["target_compatibility_not_verified"] = "target_compatibility_not_verified"
    grading_support: Literal["proposed_not_registered"] = "proposed_not_registered"
    qualification_status: Literal["not_generated_not_calibrated_not_evaluated"] = "not_generated_not_calibrated_not_evaluated"

    @model_validator(mode="after")
    def validate_template(self) -> BiologyTemplate:
        ids = tuple(x.target_id for x in self.targets)
        _unique(ids, "template target ids")
        _unique(tuple(x.case_id for x in self.cases), "case ids")
        _unique(tuple(x.measurement for x in self.observation_layers), "observation layers")
        cases = {c.case_id: c for c in self.cases}
        layers = {x.measurement for x in self.observation_layers}
        for target in self.targets:
            if not set(target.required_observation_layers) <= layers:
                raise ValueError("target observation layers must be declared by template")
        for case in self.cases:
            if not case.case_id.startswith(self.template_id + "-"):
                raise ValueError("case id must be prefixed by template id")
            if {x.target_id for x in case.target_scopes} != set(ids):
                raise ValueError("every case must declare scope for every template target")
            if not set(case.effect_ids) <= set(self.effect_ids) or case.measurement not in layers:
                raise ValueError("case effects and measurement must belong to template")
            if any(case.measurement not in t.required_observation_layers for t in self.targets):
                raise ValueError("case measurement is incompatible with a target")
            for target in self.targets:
                projection = target.shared_process_projection
                if projection is not None:
                    scope = next(s for s in case.target_scopes if s.target_id == target.target_id)
                    if (projection.marker_reference_id not in case.public_annotation_ids
                            or "shared_process_projection" not in scope.available_public_evidence):
                        raise ValueError("shared-process targets require public marker and projection evidence")
            if case.paired_case_id and (case.paired_case_id not in cases or case.paired_case_id == case.case_id):
                raise ValueError("paired case must be a different case in the same template")
        return self


class BiologyCatalog(FrozenContract):
    record_format: Literal["devbench.biology-catalog/1"] = "devbench.biology-catalog/1"
    status: Literal["configuration_only_no_data"] = "configuration_only_no_data"
    source_proposal: Text
    sources: tuple[SourceReference, ...] = Field(min_length=1)
    effects: tuple[EffectDefinition, ...] = Field(min_length=30, max_length=30)
    templates: tuple[BiologyTemplate, ...] = Field(min_length=20, max_length=20)

    @model_validator(mode="after")
    def complete_catalog(self) -> BiologyCatalog:
        if {e.effect_id for e in self.effects} != {f"E{i:02}" for i in range(1, 31)}:
            raise ValueError("catalog must contain effects E01 through E30 exactly once")
        if {t.template_id for t in self.templates} != {f"T{i:02}" for i in range(1, 21)}:
            raise ValueError("catalog must contain templates T01 through T20 exactly once")
        source_ids = tuple(s.source_id for s in self.sources)
        _unique(source_ids, "source ids")
        for entry in (*self.effects, *self.templates):
            if not set(entry.source_refs) <= set(source_ids):
                raise ValueError("unknown primary source reference")
        if {e for t in self.templates for e in t.effect_ids} != {e.effect_id for e in self.effects}:
            raise ValueError("templates must cover every declared effect")
        return self

    def template(self, template_id: str) -> BiologyTemplate:
        for template in self.templates:
            if template.template_id == template_id:
                return template
        raise ValueError(f"unknown biology template: {template_id}")


class EffectProgram(FrozenContract):
    effect_id: EffectId
    gene_fraction: Fraction
    amplitude: Annotated[float, Field(ge=-8.0, le=8.0, allow_inf_nan=False)]
    amplitude_units: Literal["log2_fold_change", "standard_deviations", "fraction_change", "log_variance_ratio"]
    onset_fraction: Fraction | None = None
    width_fraction: Annotated[float, Field(gt=0.0, le=1.0)] | None = None

    @model_validator(mode="after")
    def structural_program_scope(self) -> EffectProgram:
        if self.effect_id == "E10" and self.gene_fraction != 1.0:
            raise ValueError("E10 composition acts on the whole mixture; gene_fraction must be one")
        return self


class GenerationParameters(FrozenContract):
    """Explicit future generation bounds. No dimensions are selected by a seed."""

    num_genes: Annotated[int, Field(ge=1, le=5000)]
    independent_units_per_group: Annotated[int, Field(ge=1, le=1000)]
    condition_labels: tuple[Identifier, ...] = Field(min_length=1, max_length=20)
    time_grid: tuple[Nonnegative, ...] = Field(default=(), max_length=100)
    time_units: Text | None = None
    dose_grid: tuple[Nonnegative, ...] = Field(default=(), max_length=100)
    dose_units: Text | None = None
    num_batches: Annotated[int, Field(ge=1, le=100)] = 1
    technical_libraries_per_specimen: Annotated[int, Field(ge=1, le=20)] = 1
    repeated_specimens_per_unit: Annotated[int, Field(ge=1, le=100)] = 1
    cells_per_specimen: Annotated[int, Field(ge=0, le=10000)] = 0
    cell_type_count: Annotated[int, Field(ge=0, le=10)] = 0
    reference_mean: Annotated[float, Field(gt=0.0, le=100000.0)] = 20.0
    dispersion: Annotated[float, Field(gt=0.0, le=100.0)] = 0.2
    nuisance_intensity: Annotated[float, Field(ge=0.0, le=4.0)] = 0.0
    effect_programs: tuple[EffectProgram, ...] = ()
    max_matrix_bytes: Annotated[int, Field(ge=1, le=100_000_000)] = 100_000_000
    stored_expression_layers: Annotated[int, Field(ge=1, le=4)] = 1

    @property
    def observation_count_upper_bound(self) -> int:
        # A conservative crossed allocation bound; batches allocate existing
        # observations and never multiply the number of biological units.
        positions = max(1, len(self.time_grid), len(self.dose_grid), self.repeated_specimens_per_unit)
        specimens = self.independent_units_per_group * len(self.condition_labels) * positions
        return specimens * max(1, self.cells_per_specimen) * self.technical_libraries_per_specimen

    @model_validator(mode="after")
    def bounded_configuration(self) -> GenerationParameters:
        _unique(self.condition_labels, "condition labels")
        _unique(tuple(e.effect_id for e in self.effect_programs), "effect programs")
        for grid, units, label in ((self.time_grid, self.time_units, "time"),
                                   (self.dose_grid, self.dose_units, "dose")):
            if bool(grid) != bool(units):
                raise ValueError(f"{label} grid and units must be supplied together")
            if any(b <= a for a, b in zip(grid, grid[1:])):
                raise ValueError(f"{label} grid must be strictly increasing")
        if self.time_grid and self.dose_grid:
            raise ValueError("time-by-dose combinations need a separate coherent template")
        # Composition weights operate on the mixture, not a disjoint gene
        # subset, and can coexist with a within-type expression program.
        if sum(e.gene_fraction for e in self.effect_programs if e.effect_id != "E10") > 1.0 + 1e-12:
            raise ValueError("effect program gene fractions cannot exceed one")
        bound = self.observation_count_upper_bound * self.num_genes * 8 * self.stored_expression_layers
        if bound > self.max_matrix_bytes:
            raise ValueError("declared expression layers exceed the bounded matrix budget")
        return self


class BiologyRecipe(FrozenContract):
    recipe_id: Identifier
    template_id: TemplateId
    case_id: Identifier
    role: Role
    generation_seed: Seed | None = None
    parameters: GenerationParameters
    mechanism_holdout_group: Identifier
    status: Literal["planned_not_generated"] = "planned_not_generated"
    private_truth_scope: Literal["future_simulation_truth_only"] = "future_simulation_truth_only"

    def validate_against(self, catalog: BiologyCatalog) -> BiologyCase:
        template = catalog.template(self.template_id)
        case = next((c for c in template.cases if c.case_id == self.case_id), None)
        if case is None:
            raise ValueError("recipe case must belong to its template")
        if not {p.effect_id for p in self.parameters.effect_programs} <= set(case.effect_ids):
            raise ValueError("effect programs must belong to the selected coherent case")
        single_cell = template.unit_hierarchy.cell_label is not None
        if single_cell != (self.parameters.cells_per_specimen > 0):
            raise ValueError("cell counts must agree with the template biological hierarchy")
        if template.family == "bulk_dose" and not self.parameters.dose_grid:
            raise ValueError("dose templates require an explicit dose grid")
        if template.family == "bulk_mixture" and not 3 <= self.parameters.cell_type_count <= 5:
            raise ValueError("bulk mixture templates require three to five cell types")
        for scope in case.target_scopes:
            if scope.expected_scope != "supported":
                continue
            target = next(t for t in template.targets if t.target_id == scope.target_id)
            support = target.minimum_design_support
            p = self.parameters
            if (p.independent_units_per_group < support.minimum_independent_units_per_group
                    or len(p.time_grid) < support.minimum_timepoints
                    or len(p.dose_grid) < support.minimum_dose_levels
                    or len(p.condition_labels) < support.minimum_conditions):
                raise ValueError(f"recipe violates declared design support for {scope.target_id}")
        return case


def validate_role_separation(recipes: tuple[BiologyRecipe, ...]) -> None:
    """Check assigned streams without inventing seeds or claiming independence."""
    _unique(tuple(r.recipe_id for r in recipes), "recipe ids")
    assigned = tuple(r.generation_seed for r in recipes if r.generation_seed is not None)
    _unique(assigned, "assigned generation seeds across roles and cohorts")


@dataclass(frozen=True)
class CapabilityRegistration:
    """Explicit integration hook. Callable presence is not validation evidence."""

    kind: Literal["generator", "tool", "scorer"]
    capability_id: str
    template_id: str
    case_ids: tuple[str, ...]
    target_ids: tuple[str, ...]
    measurement: Measurement
    handler: Callable

    def __post_init__(self) -> None:
        if self.kind not in {"generator", "tool", "scorer"} or not callable(self.handler):
            raise ValueError("capability registration requires a supported kind and a callable")
        if (not isinstance(self.case_ids, tuple) or not isinstance(self.target_ids, tuple)
                or not self.case_ids or not self.target_ids):
            raise ValueError("capability support must declare exact cases and targets")
        if (not isinstance(self.template_id, str)
                or not re.fullmatch(r"T(0[1-9]|1[0-9]|20)", self.template_id)):
            raise ValueError("capability registration requires a known template identifier")
        if self.measurement not in {"raw_counts", "umi_counts", "non_umi_counts", "continuous"}:
            raise ValueError("capability registration requires an explicit measurement")
        for identifier in (self.capability_id, *self.case_ids, *self.target_ids):
            if not isinstance(identifier, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,159}", identifier):
                raise ValueError("capability identifiers must be bounded exact identifiers")
        if any(not c.startswith(self.template_id + "-") for c in self.case_ids):
            raise ValueError("capability cases must belong to the declared template")
        _unique(self.case_ids, "capability cases")
        _unique(self.target_ids, "capability targets")


@dataclass(frozen=True)
class RuntimeSupportRegistry:
    registrations: tuple[CapabilityRegistration, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.registrations, tuple) or any(
                not isinstance(r, CapabilityRegistration) for r in self.registrations):
            raise ValueError("runtime registrations must be an immutable tuple of capabilities")

    def contains(self, kind: str, capability_id: str, template_id: str,
                 case_id: str, target_id: str, measurement: Measurement) -> bool:
        return any(r.kind == kind and r.capability_id == capability_id
                   and r.template_id == template_id and case_id in r.case_ids
                   and target_id in r.target_ids and r.measurement == measurement
                   and callable(r.handler) for r in self.registrations)


class TargetCapabilityStatus(FrozenContract):
    target_id: Identifier
    expected_scope: TargetScope
    missing_capabilities: tuple[str, ...]
    executable_handlers_registered: bool
    tool_requirement_purpose: Literal["target_analysis", "observable_support_diagnostic"]
    estimation_support: Literal["not_assessed_by_callable_inventory"] = "not_assessed_by_callable_inventory"
    qualification_status: Literal["not_verified_by_configuration"] = "not_verified_by_configuration"


def check_biology_capabilities(recipe: BiologyRecipe, catalog: BiologyCatalog,
                               registry: RuntimeSupportRegistry | None = None) -> tuple[TargetCapabilityStatus, ...]:
    """Inspect registrations only; never import, invoke or qualify a handler."""
    case = recipe.validate_against(catalog)
    template = catalog.template(recipe.template_id)
    registry = registry or RuntimeSupportRegistry()
    result = []
    for target in template.targets:
        scope_contract = next(x for x in case.target_scopes if x.target_id == target.target_id)
        required_tools = scope_contract.tool_requirements or target.tool_requirements
        requirements = (("generator", template.generator_capability_id),
                        ("scorer", target.scorer_capability_id),
                        *(("tool", tool) for tool in required_tools))
        missing = tuple(f"{kind}:{name}" for kind, name in requirements
                        if not registry.contains(kind, name, template.template_id,
                                                 case.case_id, target.target_id, case.measurement))
        scope = scope_contract.expected_scope
        result.append(TargetCapabilityStatus(target_id=target.target_id, expected_scope=scope,
                                             missing_capabilities=missing,
                                             tool_requirement_purpose=scope_contract.tool_requirement_purpose,
                                             executable_handlers_registered=not missing))
    return tuple(result)


def load_biology_catalog(path: Path | None = None) -> BiologyCatalog:
    """Read a bounded catalog when explicitly requested, never at import time."""
    source = path or Path(__file__).with_name("biology_catalog.json")
    if not source.is_absolute():
        raise ValueError("catalog paths must be absolute")
    with source.open("rb") as stream:
        raw = stream.read(2_000_001)
    if len(raw) > 2_000_000:
        raise ValueError("biology catalog exceeds the configuration size limit")
    return BiologyCatalog.model_validate_json(raw)
