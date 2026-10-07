"""Participant answer contracts for biology targets; passive, bounded imports.

An answer binds to one task, input manifest and feature universe. Every requested
target carries its own scientific scope. Free text is retained for review; it is
never used as a substitute for a structured scientific claim or an LLM judge.
"""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, model_validator

from .biology import SharedProcessProjectionContract
from .contracts import FrozenContract, Identifier, Sha256, content_sha256

Finite = Annotated[float, Field(allow_inf_nan=False)]
Probability = Annotated[float, Field(ge=0.0, le=1.0, allow_inf_nan=False)]
Text = Annotated[str, Field(min_length=1, max_length=3000)]
EstimateStatus = Literal["estimated", "unavailable", "failed", "not_tested"]
EvidenceScope = Literal[
    "population_mean", "observed_difference", "within_cell_type", "relative_composition",
    "anchored_absolute", "association", "curated_membership", "motif_occurrence",
    "state_geometry", "within_branch_ordering", "clone_equivalence", "randomized_assignment",
    "achieved_intervention", "factorial_nonadditivity", "measurement_diagnostic", "not_identified",
]
ClaimCode = Literal[
    "arbitrary_cross_branch_total_order", "automatic_cycle_regression", "cells_are_independent_donors",
    "clinical_safe_dose", "contamination_marker_is_new_cell_type", "correlation_is_direct_regulation",
    "descriptive_difference_is_adjusted_effect", "donor_batch_correction_establishes_replication",
    "endpoint_only_phase_classification", "founder_barcode_alone_orients_state_path",
    "knockout_response_is_direct_binding", "motif_occurrence_is_binding", "pseudotime_is_collection_age",
    "raw_count_claim_from_transformed_layer", "relative_abundance_is_absolute_cell_loss",
    "snapshot_abundance_is_transition_rate", "snapshot_geometry_is_ancestry",
    "standardized_input_scale_is_per_cell_scale", "tf_expression_is_tf_activity",
    "transcript_response_is_binding", "unique_variant_causality", "universal_sex_dimorphism",
    "unsampled_event_discovery", "unsupported_clinical_validation", "unsupported_direct_causality",
    "unsupported_rna_velocity", "structural_zeros_identified_from_observed_zeros",
    "absence_or_equivalence_from_nonsignificance",
    "shared_process_quotient_is_full_expression_geometry",
    "private_query_labels_define_projection", "projection_deletes_input_cells_or_counts",
    "shared_process_graph_is_full_all_gene_geometry",
]
TargetId = Literal[
    "sampled_mean", "event_timing", "rhythm", "condition_main", "time_interaction",
    "dose_curve", "effective_concentration", "mean_contrast", "variability",
    "adjusted_effect", "observed_difference", "unit_accounting", "covariate_effect",
    "covariate_interaction", "relative_expression", "absolute_rna", "residual_modules",
    "direct_regulation", "tissue_effect", "cell_fractions", "clean_population_effect",
    "measurement_route", "cell_state_effect", "captured_abundance", "state_topology",
    "ancestry", "sampled_state_geometry", "population_abundance", "transition_dynamics",
    "maturation_order", "cycle_state", "measurement_diagnostics", "clean_cell_state",
    "association_activity", "regulon_membership", "motif_occurrence", "binding",
    "assignment_effect", "successful_intervention", "epistasis", "direct_edge",
]

CriterionId = Literal[
    "independent_biological_units", "biological_identity_recorded", "contrast_levels_observed",
    "design_rank_identifiable", "condition_batch_overlap", "covariate_condition_overlap", "covariate_level_support",
    "time_sampling_resolution", "rhythm_candidate_domain_identifiable", "dose_half_response_bracketed",
    "nonzero_response_information", "hypothesis_observed_information", "absolute_scale_anchor_verified",
    "complete_mixture_reference", "stable_marker_reference", "capture_sampling_calibrated", "sampling_window_support",
    "shared_process_projection", "component_bridge_observed", "independent_lineage_history", "direction_anchor_observed",
    "transition_dynamics_measured", "full_cycle_support", "protocol_and_analysis_layer_recorded",
    "cell_identity_reference_supported", "individual_artifact_identity_supported", "regulon_reference_supplied",
    "motif_reference_supplied", "binding_assay_supplied", "direct_regulatory_edge_identified",
    "randomized_assignment_supported", "achieved_intervention_evidence", "achieved_intervention_positivity",
    "factorial_design_supported", "exclusion_assumptions_supported", "missingness_assumptions_supported",
    "observed_quality_overlap", "fraction_denominator_defined", "candidate_tf_universe_recorded",
]

# Public criterion meanings, never expected diagnoses or hidden case mappings.
IDENTIFICATION_CRITERIA: dict[str, str] = {
    "independent_biological_units": "Independent donor/organism/culture replication, not cells or technical libraries.",
    "biological_identity_recorded": "Records identify which observations share a biological unit.",
    "contrast_levels_observed": "Every level in the requested contrast occurs in the observed design.",
    "design_rank_identifiable": "The requested contrast is estimable under the observed design and nuisance adjustment.",
    "condition_batch_overlap": "Observed condition and batch allocation permits the requested adjusted contrast.",
    "covariate_condition_overlap": "The requested covariate contrast has within-condition support.",
    "covariate_level_support": "Observed covariate levels/dosages support the declared contrast.",
    "time_sampling_resolution": "Observed time sampling resolves the requested event, without inventing unsampled events.",
    "rhythm_candidate_domain_identifiable": "The observed grid distinguishes candidate periods/phases in the declared domain.",
    "dose_half_response_bracketed": "Observed dose support brackets the declared half-response target.",
    "nonzero_response_information": "An informative response supports a timing, rhythm or concentration parameter.",
    "hypothesis_observed_information": "Observed measurements contain information for the requested gene effect.",
    "absolute_scale_anchor_verified": "Independent verified input controls identify the claimed absolute scale.",
    "complete_mixture_reference": "Supplied reference profiles span the claimed mixture components.",
    "stable_marker_reference": "An independently justified stable feature subset supports mixture attribution.",
    "capture_sampling_calibrated": "Capture evidence identifies relative population fractions in the specified windows.",
    "sampling_window_support": "Observed sampling windows support the claimed population or trajectory region.",
    "shared_process_projection": "The public feature projection is followed, retaining all observations and original counts.",
    "component_bridge_observed": "Observed intermediate states support a bridge between separated sampled components.",
    "independent_lineage_history": "An independent retained history assay supports origin equivalence.",
    "direction_anchor_observed": "An independently anchored state history supports direction, beyond founder precedence.",
    "transition_dynamics_measured": "Dynamic measurements identify transition rates/fate probabilities beyond snapshot occupancy.",
    "full_cycle_support": "Observed states cover a full cycle rather than only a local arc.",
    "protocol_and_analysis_layer_recorded": "The declared assay and input layer are documented and compatible with the analysis.",
    "cell_identity_reference_supported": "Evidence supports the requested cell-state interpretation despite unresolved mixtures.",
    "individual_artifact_identity_supported": "An observation-specific reference identifies individual artifacts rather than only aggregate QC.",
    "regulon_reference_supplied": "The requested curated membership reference and feature background are supplied.",
    "motif_reference_supplied": "The requested sequence motif occurrence reference and background are supplied.",
    "binding_assay_supplied": "An independent binding assay supports a binding claim.",
    "direct_regulatory_edge_identified": "Intervention/mediation evidence separates direct regulation from association or downstream response.",
    "randomized_assignment_supported": "A documented assignment record and biological unit support the intervention contrast.",
    "achieved_intervention_evidence": "An independent efficacy assay measures the intended achieved intervention.",
    "achieved_intervention_positivity": "Observed assignments actually achieve the intended intervention with adequate support.",
    "factorial_design_supported": "Observed independent assignments include control, A, B and AB for the stated interaction.",
    "exclusion_assumptions_supported": "Supplied evidence supports required exclusion assumptions, not just a total assignment response.",
    "missingness_assumptions_supported": "Observed missingness evidence and explicit assumptions identify the requested population target.",
    "observed_quality_overlap": "Observed assay-quality support distinguishes quality effects from the biological contrast.",
    "fraction_denominator_defined": "The requested fraction has an explicit observable reference population and denominator.",
    "candidate_tf_universe_recorded": "The candidate regulator universe is declared independently of activity conclusions.",
}


class IdentificationFinding(FrozenContract):
    """Coordinator support finding; never copied into participant questions."""
    code: CriterionId
    status: Literal["violated", "unknown"]
    evidence_ids: tuple[Identifier, ...] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def evidence_is_distinct(self) -> IdentificationFinding:
        _distinct(self.evidence_ids, "finding evidence ids")
        return self


def public_hypothesis_id(gene_id: str, component_id: str | None = None) -> str:
    """Public deterministic identifier; no values, private labels or randomness."""
    return gene_id if component_id is None else "h-" + content_sha256((gene_id, component_id))


POINTWISE_CONFIDENCE_LEVEL = 0.95
EFFECT_INTERVAL_METRICS = frozenset({"interval_coverage", "effect_interval_coverage"})


def effect_uncertainty_contract(target_id: str, context: dict, reference_time=None) -> dict | None:
    """Public pointwise interval policy; fixed contrasts come from design, not truth."""
    if target_id not in {"sampled_mean", "dose_curve"}:
        return None
    design = context.get("design", {})
    grid = list(design.get("dose_grid") or design.get("time_grid") or [0.0])
    conditions = list(design.get("condition_levels", context.get("condition_labels", [])))
    reference = grid[0] if target_id == "dose_curve" or reference_time is None else reference_time
    fixed = [f"grid_{condition * len(grid) + index}" for condition in range(max(1, len(conditions)))
             for index, value in enumerate(grid) if value == reference]
    return {"confidence_level": POINTWISE_CONFIDENCE_LEVEL, "interval_scope": "pointwise",
            "complete_intervals_required_when_estimable": True, "fixed_effect_coordinate_ids": fixed,
            "coverage_scope": "estimated_nonfixed_coordinates; report_missing_intervals_separately",
            "nominal_coverage_validated": False}


def public_output_layout(public_task: dict, public_annotations: dict | None = None) -> dict:
    """Publish serialization coordinates using public design metadata only.

    No private reference, testability mask or effect is accepted. A required
    coordinate is not evidence that its numerical value is identifiable.
    """
    context = public_task.get("context", {})
    design = context.get("design", {})
    conditions = list(design.get("condition_levels", context.get("condition_labels", [])))
    grid = list(design.get("dose_grid") or design.get("time_grid") or [0.0])
    reference = (public_annotations or {}).get("cell_annotation_reference", {})
    labels = sorted(row["label"] for row in reference.get("markers", ()))
    result = {}
    for question in public_task["questions"]:
        target = question["target_id"]
        kind = TARGET_PAYLOAD_KINDS[target]
        layout = {"payload_kind": kind, "availability": "Determine from observed support; never infer availability from this layout"}
        if kind == "effects":
            layout["hypothesis_id_rule"] = "gene_id"
            layout["multiplicity_contract"] = {
                "method": "BH", "family": "all_declared_hypothesis_rows",
                "missing_test_positions": "reserve_with_internal_p_one; public_p_and_q_remain_unavailable",
                "multiplicity_scope_field": "names_only_hypotheses_with_actual_p_values",
                "independent_filtering": "not_inferred_from_missing_or_failed_fits"}
            uncertainty = effect_uncertainty_contract(target, context, public_task.get("analysis_reference_time"))
            if uncertainty is not None:
                layout["uncertainty_contract"] = uncertainty
            if target in {"assignment_effect", "successful_intervention", "cell_state_effect"}:
                components = labels if target == "cell_state_effect" else conditions[1:]
                layout.update(component_ids=components,
                    hypothesis_id_rule='h- followed by SHA256 of UTF-8 json.dumps([gene_id, component_id], separators=(",", ":"), ensure_ascii=False)',
                    coordinate_ids_by_component={label: ["condition_contrast" if target == "cell_state_effect" else label]
                                                 for label in components})
            else:
                full = target in {"sampled_mean", "clean_population_effect", "dose_curve"}
                n = len(grid) * max(1, len(conditions)) if full else len(grid)
                if target in {"condition_main", "observed_difference", "variability"}:
                    coordinates = ["contrast"]
                elif target == "epistasis":
                    coordinates = ["factorial_nonadditivity"]
                elif target == "time_interaction":
                    coordinates = [f"grid_{i}" for i in range(1, len(grid))]
                else:
                    coordinates = [f"grid_{i}" for i in range(n)] if n>1 else ["contrast"]
                layout["effect_coordinate_ids"] = coordinates
                if full:
                    layout["curve_coordinate_ids"] = [f"grid_{i}" for i in range(n)]
                    layout["coordinate_order"] = "Declared condition order, then increasing declared grid position"
                else:
                    layout["coordinate_order"] = "Increasing declared grid position, or the explicitly named scalar contrast"
                if target == "condition_main":
                    layout["reference_time"] = public_task.get("analysis_reference_time")
            layout["curve_scale"] = "unchanged continuous expression" if public_task["measurement"] == "continuous" else "expected counts at the declared unit library-size reference"
        elif target == "captured_abundance":
            layout.update(population_ids=labels, coordinate_ids=conditions,
                reference_denominator="observed_captured_subset_within_condition" if context.get("population_reference") == "observed captured subset" else "all_captured_viable_cells")
        elif target == "cell_fractions":
            layout.update(coordinate_ids=[f"coordinate_{i}" for i in range(len(grid)*len(conditions))],
                          coordinate_order="Declared condition order, then increasing declared grid position")
        elif kind in {"events", "rhythm", "concentration"}:
            layout["hypothesis_id_rule"] = "gene_id; retain the entire supplied gene family and explicit unavailable statuses"
        result[target] = layout
    return result

# Exact registration is deliberate. A novel target has no generic success path.
TARGET_PAYLOAD_KINDS: dict[str, str] = {
    "sampled_mean": "effects", "condition_main": "effects", "time_interaction": "effects",
    "dose_curve": "effects", "mean_contrast": "effects", "variability": "effects",
    "adjusted_effect": "effects", "observed_difference": "effects", "covariate_effect": "effects",
    "covariate_interaction": "effects", "relative_expression": "effects", "absolute_rna": "effects",
    "tissue_effect": "effects", "clean_population_effect": "effects", "cell_state_effect": "effects",
    "assignment_effect": "effects", "successful_intervention": "effects", "epistasis": "effects",
    "event_timing": "events", "rhythm": "rhythm", "effective_concentration": "concentration",
    "cell_fractions": "fractions", "captured_abundance": "fractions", "population_abundance": "fractions",
    "state_topology": "graph", "sampled_state_geometry": "graph", "ancestry": "ancestry",
    "maturation_order": "ordering", "cycle_state": "ordering", "residual_modules": "association",
    "association_activity": "association", "regulon_membership": "enrichment", "motif_occurrence": "enrichment",
    "unit_accounting": "diagnostics", "measurement_route": "diagnostics",
    "measurement_diagnostics": "diagnostics", "clean_cell_state": "cell_state",
    "direct_regulation": "restraint", "transition_dynamics": "restraint",
    "binding": "restraint", "direct_edge": "restraint",
}


def _distinct(values: tuple, name: str) -> None:
    if len(values) != len(set(values)):
        raise ValueError(f"{name} must be distinct")


class Interval(FrozenContract):
    lower: Finite
    upper: Finite
    confidence_level: Annotated[float, Field(gt=0, lt=1)] | None = None

    @model_validator(mode="after")
    def ordered(self) -> Interval:
        if self.lower > self.upper:
            raise ValueError("interval endpoints must be ordered")
        return self


class HypothesisEstimate(FrozenContract):
    hypothesis_id: Identifier
    gene_id: Identifier | None = None
    component_id: Identifier | None = None
    estimate_status: EstimateStatus
    coordinate_ids: tuple[Identifier, ...] = Field(default=(), max_length=1000)
    values: tuple[Finite, ...] = Field(default=(), max_length=1000)
    intervals: tuple[Interval | None, ...] = Field(default=(), max_length=1000)
    p_value: Probability | None = None
    q_value: Probability | None = None
    missing_reason: Identifier | None = None

    @model_validator(mode="after")
    def unavailable_never_becomes_zero(self) -> HypothesisEstimate:
        _distinct(self.coordinate_ids, "coordinate ids")
        if self.estimate_status != "estimated":
            if self.values or self.intervals or self.p_value is not None or self.q_value is not None:
                raise ValueError("unavailable or failed estimate must not carry numbers or significance")
            if self.missing_reason is None:
                raise ValueError("unavailable estimates require a missing reason")
        elif not self.values or self.missing_reason is not None:
            raise ValueError("estimated hypotheses require values and no missing reason")
        if self.coordinate_ids and len(self.coordinate_ids) != len(self.values):
            raise ValueError("coordinate ids must match values")
        if self.intervals and len(self.intervals) != len(self.values):
            raise ValueError("intervals must match values")
        if (self.p_value is None) != (self.q_value is None):
            raise ValueError("tested hypotheses require both p and BH q")
        if self.p_value is not None and self.q_value < self.p_value:
            raise ValueError("BH q must be at least p")
        return self


class SupportCheck(FrozenContract):
    code: Identifier
    status: Literal["satisfied", "violated", "unknown"]
    evidence_ids: tuple[Identifier, ...] = Field(min_length=1, max_length=100)
    explanation: Text


class ComputedConditionContrast(FrozenContract):
    """Pairwise computation metadata, distinct from the requested free text.

    This schema checks internal consistency. A caller still needs a verified
    tool receipt to establish that the stated computation actually ran.
    """

    operation: Literal["comparison_minus_reference"] = "comparison_minus_reference"
    reference_condition: Identifier
    comparison_condition: Identifier
    effect_scale: Literal["log2_relative_expression", "unchanged_input_expression"]
    normalization: Literal["RLE_library_size_offsets", "unchanged_input"]

    @model_validator(mode="after")
    def consistent_definition(self):
        if self.reference_condition == self.comparison_condition:
            raise ValueError("Computed contrast requires distinct reference and comparison conditions")
        counts = self.effect_scale == "log2_relative_expression"
        if counts != (self.normalization == "RLE_library_size_offsets"):
            raise ValueError("Computed contrast scale and normalization disagree")
        return self

    @classmethod
    def from_measurement(cls, measurement, reference, comparison):
        if measurement not in {"raw_counts", "umi_counts", "non_umi_counts", "continuous"}:
            raise ValueError("Computed contrast requires an explicit supported measurement")
        counts = measurement != "continuous"
        return cls(reference_condition=reference, comparison_condition=comparison,
                   effect_scale="log2_relative_expression" if counts else "unchanged_input_expression",
                   normalization="RLE_library_size_offsets" if counts else "unchanged_input")

    def describe(self):
        scale = ("log2 relative-expression scale with RLE library-size offsets"
                 if self.effect_scale == "log2_relative_expression" else "unchanged input-expression scale")
        return f"{self.comparison_condition} minus {self.reference_condition} on the {scale}."


class EffectsPayload(FrozenContract):
    kind: Literal["effects"] = "effects"
    analysis_performed: bool
    gene_statistics: tuple[HypothesisEstimate, ...] = Field(default=(), max_length=100000)
    curve_estimates: tuple[HypothesisEstimate, ...] = Field(default=(), max_length=100000)
    multiplicity_scope: tuple[Identifier, ...] = Field(default=(), max_length=100000,
        description="IDs with actual p-values. BH uses all declared hypothesis rows; failed/unavailable rows reserve internal p=1 positions without publishing fabricated p/q values.")
    selection_conditional_inference: bool
    biological_unit: Literal["donor", "organism", "experimental_culture", "observed_library"]
    analysis_layer: Literal["raw_counts", "umi_counts", "non_umi_counts", "continuous"]
    effect_definition: Text
    computed_contrast: ComputedConditionContrast | None = Field(default=None,
        description="Present only when the producer binds a pairwise fitted contrast and its actual scale; absent in historical or other-estimand outputs.")
    prediction_structure: Literal["coordinate_specific", "shared_across_grid", "shared_across_conditions"] = "coordinate_specific"
    reference_condition: Identifier | None = None
    reference_time: Finite | None = None
    aggregation_definition: Text | None = None
    efficacy_evidence_sha256: Sha256 | None = None
    covariate_contrast: Text | None = None
    scale_anchor: Identifier | None = None
    condition_number_validated: bool | None = None
    condition_number: Annotated[float, Field(ge=1, allow_inf_nan=False)] | None = None
    design_rank: Annotated[int, Field(ge=0)] | None = None
    design_column_count: Annotated[int, Field(ge=1)] | None = None

    @model_validator(mode="after")
    def family_is_explicit(self) -> EffectsPayload:
        _distinct(tuple(x.hypothesis_id for x in self.gene_statistics), "hypotheses")
        _distinct(tuple(x.hypothesis_id for x in self.curve_estimates), "curve hypotheses")
        _distinct(self.multiplicity_scope, "multiplicity scope")
        tested = {x.hypothesis_id for x in self.gene_statistics if x.p_value is not None}
        if tested != set(self.multiplicity_scope):
            raise ValueError("multiplicity scope must exactly name the submitted tested hypotheses")
        if not self.analysis_performed and (self.gene_statistics or self.curve_estimates):
            raise ValueError("unperformed analysis cannot contain estimates")
        if any(row.p_value is not None for row in self.curve_estimates):
            raise ValueError("hypothesis tests belong in the explicit gene test family, not curve rows")
        if self.computed_contrast is not None:
            contrast = self.computed_contrast
            if not self.analysis_performed or not any(row.estimate_status == "estimated" for row in self.gene_statistics):
                raise ValueError("Computed contrast requires a performed gene estimate")
            if contrast.reference_condition != self.reference_condition:
                raise ValueError("Computed contrast reference disagrees with the payload reference")
            if (self.analysis_layer == "continuous") != (contrast.effect_scale == "unchanged_input_expression"):
                raise ValueError("Computed contrast scale disagrees with the analysis layer")
        return self


class EventEstimate(FrozenContract):
    hypothesis_id: Identifier
    kind: Literal["peak", "trough", "half_observed_response", "baseline_crossing_reversal"]
    interval: Interval


class EventsPayload(FrozenContract):
    kind: Literal["events"] = "events"
    event_intervals: tuple[EventEstimate, ...] = Field(default=(), max_length=100000)
    eligible_hypotheses: tuple[Identifier, ...] = Field(default=(), max_length=100000)
    unavailable_hypotheses: dict[Identifier, Identifier] = Field(default_factory=dict, max_length=100000)
    timing_definition: Literal["observed_grid_brackets"]

    @model_validator(mode="after")
    def event_family_is_explicit(self):
        _distinct(self.eligible_hypotheses, "event hypothesis family")
        if set(self.unavailable_hypotheses) - set(self.eligible_hypotheses):
            raise ValueError("Unavailable event hypotheses must belong to the declared family")
        if any(event.hypothesis_id in self.unavailable_hypotheses for event in self.event_intervals):
            raise ValueError("Unavailable hypotheses cannot carry detected events")
        return self


class ConditionPhase(FrozenContract):
    condition: Identifier
    phase_radians: Finite


class RhythmEstimate(FrozenContract):
    hypothesis_id: Identifier
    estimate_status: EstimateStatus
    rhythmic: bool | None = None
    period: Annotated[float, Field(gt=0, allow_inf_nan=False)] | None = None
    phase_by_condition: tuple[ConditionPhase, ...] = Field(default=(), max_length=20)
    damping: Annotated[float, Field(ge=0, allow_inf_nan=False)] | None = None
    p_value: Probability | None = None
    q_value: Probability | None = None
    missing_reason: Identifier | None = None

    @model_validator(mode="after")
    def rhythm_is_a_tested_claim(self) -> RhythmEstimate:
        _distinct(tuple(p.condition for p in self.phase_by_condition), "phase conditions")
        if self.estimate_status != "estimated":
            if any(x is not None for x in (self.rhythmic, self.period, self.damping, self.p_value, self.q_value)) or self.phase_by_condition:
                raise ValueError("unavailable rhythm cannot have fitted results")
            if self.missing_reason is None:
                raise ValueError("unavailable rhythm requires missing reason")
        else:
            if self.rhythmic is None or self.p_value is None or self.q_value is None:
                raise ValueError("rhythm decision requires an explicit test and p/q")
            if self.q_value < self.p_value:
                raise ValueError("BH q must be at least p")
            if self.rhythmic and (self.period is None or not self.phase_by_condition or self.damping is None):
                raise ValueError("rhythmic hypothesis requires period, phase and damping")
            if not self.rhythmic and (self.period is not None or self.phase_by_condition or self.damping is not None):
                raise ValueError("non-rhythmic hypothesis has no identified period or phase")
        return self


class RhythmPayload(FrozenContract):
    kind: Literal["rhythm"] = "rhythm"
    rhythms: tuple[RhythmEstimate, ...] = Field(default=(), max_length=100000)
    phase_reference_time: Finite
    phase_convention: Literal["cosine_plus_phase"]
    amplitude_convention: Literal["nonnegative"]
    candidate_frequency_domain: Interval
    sampling_ambiguity: bool
    selection_conditional_inference: bool


class ConcentrationEstimate(FrozenContract):
    hypothesis_id: Identifier
    solution_kind: Literal["unique", "root_set", "unavailable"]
    roots: tuple[Annotated[float, Field(ge=0, allow_inf_nan=False)], ...] = Field(default=(), max_length=8)
    intervals: tuple[Interval, ...] = Field(default=(), max_length=8)
    missing_reason: Identifier | None = None

    @model_validator(mode="after")
    def solution_count(self) -> ConcentrationEstimate:
        if self.solution_kind == "unique" and len(self.roots) != 1:
            raise ValueError("unique concentration requires exactly one root")
        if self.solution_kind == "root_set" and len(self.roots) < 2:
            raise ValueError("nonmonotone root set requires distinct roots")
        _distinct(self.roots, "concentration roots")
        if self.solution_kind == "unavailable" and (self.roots or self.intervals or not self.missing_reason):
            raise ValueError("unavailable concentration cannot contain numerical roots")
        if self.intervals and len(self.intervals) != len(self.roots):
            raise ValueError("concentration intervals must match roots")
        return self


class ConcentrationPayload(FrozenContract):
    kind: Literal["concentration"] = "concentration"
    concentration_definition: Literal["half_maximal_log2_response"]
    concentration_result: tuple[ConcentrationEstimate, ...] = Field(default=(), max_length=100000)


class FractionEstimate(FrozenContract):
    population_id: Identifier
    coordinate_id: Identifier
    fraction: Probability
    interval: Interval | None = None


class FractionsPayload(FrozenContract):
    kind: Literal["fractions"] = "fractions"
    cell_fractions: tuple[FractionEstimate, ...] = Field(default=(), max_length=100000)
    rna_fractions: tuple[FractionEstimate, ...] = Field(default=(), max_length=100000)
    reference_denominator: Text
    reference_coverage: Literal["complete", "restricted", "unavailable"]
    absolute_population_claimed: bool = False


class GraphEdge(FrozenContract):
    source: Identifier
    target: Identifier


class CellCoordinate(FrozenContract):
    cell_id: Identifier
    group: Identifier | None = None
    order: Finite | None = None
    phase_radians: Finite | None = None


class SharedProcessProjection(SharedProcessProjectionContract):
    """Exact public feature projection; does not identify query-cell types."""
    excluded_feature_ids: tuple[Identifier, ...] = Field(min_length=1, max_length=100000)
    retained_feature_universe: tuple[Identifier, ...] = Field(min_length=1, max_length=100000)

    @model_validator(mode="after")
    def disjoint_declared_features(self) -> SharedProcessProjection:
        _distinct(self.excluded_feature_ids, "projection excluded features")
        _distinct(self.retained_feature_universe, "projection retained features")
        if set(self.excluded_feature_ids) & set(self.retained_feature_universe):
            raise ValueError("projection retained and excluded features must be disjoint")
        return self


class ProjectedPayload(FrozenContract):
    shared_process_projection: SharedProcessProjection
    analysis_layer: Literal["counts"]
    retained_feature_universe: tuple[Identifier, ...] = Field(min_length=1, max_length=100000)
    excluded_feature_ids: tuple[Identifier, ...] = Field(min_length=1, max_length=100000)

    @model_validator(mode="after")
    def projection_declarations_agree(self) -> ProjectedPayload:
        if self.retained_feature_universe != self.shared_process_projection.retained_feature_universe:
            raise ValueError("retained feature universe must match the declared projection")
        if self.excluded_feature_ids != self.shared_process_projection.excluded_feature_ids:
            raise ValueError("excluded feature IDs must match the declared projection")
        return self


class GraphPayload(ProjectedPayload):
    kind: Literal["graph"] = "graph"
    state_graph: tuple[GraphEdge, ...] = Field(default=(), max_length=28)
    within_branch_order: tuple[CellCoordinate, ...] = Field(default=(), max_length=100000)
    orientation_uncertainty: Literal["unanchored", "anchored"]
    support_gaps: tuple[Identifier, ...] = Field(default=(), max_length=100)


class AncestryPayload(FrozenContract):
    kind: Literal["ancestry"] = "ancestry"
    ancestry_status: Literal["equivalence_only", "unavailable"]
    clone_equivalence: tuple[CellCoordinate, ...] = Field(default=(), max_length=100000)
    history_evidence: tuple[Identifier, ...] = Field(default=(), max_length=100)
    direction_identified: bool = False


class OrderingPayload(ProjectedPayload):
    kind: Literal["ordering"] = "ordering"
    coordinates: tuple[CellCoordinate, ...] = Field(default=(), max_length=100000)
    orientation_uncertainty: Literal["unanchored", "anchored"]
    cycle_target_scope: Literal["full_cycle", "observed_arc", "not_applicable"]


class AssociationEdge(FrozenContract):
    source: Identifier
    target: Identifier
    covariance: Finite


class ModuleMembership(FrozenContract):
    gene_id: Identifier
    module_id: Identifier | None = None


class AssociationPayload(FrozenContract):
    kind: Literal["association"] = "association"
    association_edges: tuple[AssociationEdge, ...] = Field(default=(), max_length=100000)
    module_membership: tuple[ModuleMembership, ...] = Field(default=(), max_length=100000)
    candidate_regulators: tuple[Identifier, ...] = Field(default=(), max_length=100000)
    candidate_universe: tuple[Identifier, ...] = Field(default=(), max_length=100000)
    covariance_scale: Literal["log_expression_signal_covariance", "log_expression_signal_correlation"]
    condition: Identifier | None = None
    claims_direct_causality: bool = False


class EnrichmentEstimate(FrozenContract):
    regulator_id: Identifier
    selected_gene_ids: tuple[Identifier, ...] = Field(max_length=100000)
    overlap_gene_ids: tuple[Identifier, ...] = Field(max_length=100000)
    p_value: Probability
    q_value: Probability
    odds_ratio: Annotated[float, Field(ge=0, allow_inf_nan=False)] | None = None
    odds_ratio_status: Literal["finite", "infinite", "undefined", "unavailable"] = "unavailable"

    @model_validator(mode="after")
    def enrichment_bounds(self) -> EnrichmentEstimate:
        _distinct(self.selected_gene_ids, "selected genes")
        _distinct(self.overlap_gene_ids, "overlap genes")
        if self.q_value < self.p_value:
            raise ValueError("BH q must be at least p")
        if (self.odds_ratio_status == "finite") != (self.odds_ratio is not None):
            raise ValueError("Only a finite odds ratio status may carry a numerical ratio")
        return self


class EnrichmentPayload(FrozenContract):
    kind: Literal["enrichment"] = "enrichment"
    enrichment_results: tuple[EnrichmentEstimate, ...] = Field(default=(), max_length=10000)
    feature_universe: tuple[Identifier, ...] = Field(default=(), max_length=100000)
    annotation_provenance: Identifier
    test_definition: Literal["one_sided_hypergeometric_membership"]
    unknown_edges_are_negatives: bool = False
    binding_inferred: bool = False
    selection_conditional_inference: Literal[True]
    probability_scope: Literal["conditional_combinatorial_annotation_overlap"] = "conditional_combinatorial_annotation_overlap"


class DiagnosticValue(FrozenContract):
    code: Identifier
    value: str | bool | int | Finite | None
    missing_reason: Identifier | None = None


class CellDecision(FrozenContract):
    cell_id: Identifier
    decision: Literal["retain", "exclude", "uncertain"]
    reason: Identifier


class DiagnosticsPayload(FrozenContract):
    kind: Literal["diagnostics"] = "diagnostics"
    diagnostics: tuple[DiagnosticValue, ...] = Field(default=(), max_length=100)
    qc_decisions: tuple[CellDecision, ...] = Field(default=(), max_length=100000)
    biological_unit_count: Annotated[int, Field(ge=0)] | None = None
    biological_unit: Literal["donor", "organism", "experimental_culture", "observed_library", "cell"] | None = None
    individual_artifact_identification: Literal["not_identified", "claimed"] | None = None
    measurement_type: Literal["raw_counts", "umi_counts", "non_umi_counts", "continuous"] | None = None
    analysis_route: Literal["count_likelihood", "continuous_likelihood"] | None = None
    model_family: Identifier | None = None
    residual_diagnostics: tuple[DiagnosticValue, ...] = Field(default=(), max_length=100)
    protocol: Text | None = None
    analysis_layer: Literal["counts", "continuous", "normalized", "integrated", "residuals"] | None = None
    transformation_history: tuple[Text, ...] = Field(default=(), max_length=100)


class CellStatePayload(FrozenContract):
    kind: Literal["cell_state"] = "cell_state"
    cell_type_labels: tuple[CellCoordinate, ...] = Field(default=(), max_length=100000)
    qc_decisions: tuple[CellDecision, ...] = Field(default=(), max_length=100000)
    reference_coverage: Literal["complete", "restricted", "unavailable"]
    uncertain_labels: tuple[Identifier, ...] = Field(default=(), max_length=100000)


class RestraintPayload(FrozenContract):
    kind: Literal["restraint"] = "restraint"
    status: Literal["not_identified"]
    missing_evidence: tuple[Identifier, ...] = Field(min_length=1, max_length=100)


Payload = Annotated[
    EffectsPayload | EventsPayload | RhythmPayload | ConcentrationPayload | FractionsPayload |
    GraphPayload | AncestryPayload | OrderingPayload | AssociationPayload | EnrichmentPayload |
    DiagnosticsPayload | CellStatePayload | RestraintPayload,
    Field(discriminator="kind"),
]


class TargetAnswer(FrozenContract):
    target_id: TargetId
    estimand: Text
    units: Text
    contrast: Text
    reference_population: Text
    prediction_reference: Text
    claim_type: Literal["supported_estimate", "restricted_estimate", "refusal", "failed"]
    evidence_scope: EvidenceScope
    estimate_status: Literal["estimated", "unavailable", "failed"]
    limitation_codes: tuple[Identifier, ...] = Field(default=(), max_length=100)
    asserted_claims: tuple[ClaimCode, ...] = Field(default=(), max_length=100)
    support_checks: tuple[SupportCheck, ...] = Field(default=(), max_length=100)
    tool_evidence_ids: tuple[Sha256, ...] = Field(default=(), max_length=100, json_schema_extra={"uniqueItems": True})
    payload: Payload | None = None

    @model_validator(mode="after")
    def target_owns_payload(self) -> TargetAnswer:
        _distinct(self.limitation_codes, "limitation codes")
        _distinct(self.asserted_claims, "asserted claims")
        _distinct(self.tool_evidence_ids, "tool evidence ids")
        if self.payload is not None and self.payload.kind != TARGET_PAYLOAD_KINDS[self.target_id]:
            raise ValueError("payload kind does not match the target-specific contract")
        if self.claim_type in {"supported_estimate", "restricted_estimate"}:
            if self.estimate_status != "estimated" or self.payload is None:
                raise ValueError("a supported or restricted estimate requires its target payload")
        else:
            if self.estimate_status == "estimated":
                raise ValueError("a refusal or failure cannot report an estimated target")
            if self.claim_type == "refusal" and self.estimate_status != "unavailable":
                raise ValueError("a scientific refusal must remain distinct from analysis failure")
            if self.claim_type == "failed" and self.estimate_status != "failed":
                raise ValueError("a failed target must retain failed status")
            if self.payload is not None and not isinstance(self.payload, RestraintPayload):
                raise ValueError("refusals and failures cannot smuggle numerical estimates")
            if not self.limitation_codes:
                raise ValueError("a refusal or failure requires explicit missingness")
        return self


class BiologyAnswer(FrozenContract):
    record_format: Literal["devbench.biology-answer/1"] = "devbench.biology-answer/1"
    task_id: Identifier
    input_sha256: Sha256
    gene_universe_sha256: Sha256
    target_results: tuple[TargetAnswer, ...] = Field(max_length=50)
    narrative_for_human_review: Annotated[str, Field(max_length=12000)] = ""

    @model_validator(mode="after")
    def unique_targets(self) -> BiologyAnswer:
        _distinct(tuple(x.target_id for x in self.target_results), "target results")
        return self


def biology_answer_schema() -> dict[str, object]:
    """Return a static schema; does not inspect a task or execute analysis."""
    return BiologyAnswer.model_json_schema()
