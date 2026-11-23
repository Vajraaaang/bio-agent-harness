"""Deterministic target-specific scoring; no numerical or data work on import.

This is coordinator-only code. References are normalized from explicitly named
target fields, never a case-name oracle or a global latent mechanism. Missing
references stay unavailable. Configuration coverage is not qualification.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from itertools import permutations
import math
from typing import Annotated, Literal, cast, get_args

from pydantic import Field, ValidationError, model_validator

from .biology import BiologyCatalog, BiologyTemplate, CapabilityRegistration
from .biology_answers import (
    TARGET_PAYLOAD_KINDS, EFFECT_INTERVAL_METRICS, POINTWISE_CONFIDENCE_LEVEL,
    effect_uncertainty_contract, AncestryPayload, AssociationPayload, BiologyAnswer,
    CellCoordinate, CellStatePayload, ConcentrationEstimate, ConcentrationPayload,
    ConditionPhase, DiagnosticValue, DiagnosticsPayload, EffectsPayload,
    EnrichmentPayload, EventEstimate, EventsPayload, EvidenceScope, Finite,
    FractionEstimate, FractionsPayload, GraphEdge, GraphPayload, HypothesisEstimate,
    Interval, OrderingPayload, Probability, RhythmPayload, SharedProcessProjection, TargetAnswer, TargetId, Text,
)
from .contracts import FrozenContract, Identifier, Sha256, canonical_json_bytes, content_sha256


class BiologyScoringThresholds(FrozenContract):
    """Frozen before measurement; these engineering cutoffs are uncalibrated."""
    status: Literal["provisional_uncalibrated_locked"] = "provisional_uncalibrated_locked"
    alpha: Annotated[float, Field(gt=0, lt=1)] = 0.05
    minimum_signed_recall: Probability = 0.7
    maximum_fdp: Probability = 0.1
    minimum_estimate_availability: Probability = 0.9
    maximum_effect_rmse: Annotated[float, Field(gt=0)] = 0.5
    maximum_relative_curve_rmse: Annotated[float, Field(gt=0)] = 0.25
    maximum_fraction_mae: Probability = 0.1
    maximum_relative_period_error: Probability = 0.2
    maximum_circular_phase_error: Annotated[float, Field(gt=0)] = 0.5
    maximum_relative_concentration_error: Probability = 0.2
    maximum_damping_error: Annotated[float, Field(gt=0)] = 0.2
    maximum_covariance_rmse: Annotated[float, Field(gt=0)] = 0.25
    minimum_association_magnitude: Annotated[float, Field(gt=0)] = 0.01
    minimum_topology_score: Probability = 0.8
    minimum_topology_recall: Probability = 1.0
    minimum_branch_macro_f1: Probability = 0.8
    minimum_partition_f1: Probability = 0.8
    minimum_rank_agreement: Probability = 0.8
    minimum_event_f1: Probability = 0.7
    maximum_interval_expansion: Annotated[float, Field(ge=1)] = 2.0
    minimum_interval_coverage: Probability = 0.8
    maximum_interval_width_ratio: Annotated[float, Field(gt=0)] = 4.0
    numeric_tolerance: Annotated[float, Field(gt=0)] = 1e-9


class HypothesisReference(FrozenContract):
    hypothesis_id: Identifier
    gene_id: Identifier | None = None
    component_id: Identifier | None = None
    coordinate_ids: tuple[Identifier, ...] = Field(default=(), max_length=1000)
    values: tuple[Finite | None, ...] = Field(default=(), max_length=1000)
    testable: bool = True
    nonnull: bool | None = None
    unavailable_reason: Identifier | None = None

    @model_validator(mode="after")
    def unavailable_is_not_null(self) -> HypothesisReference:
        if self.coordinate_ids and len(self.coordinate_ids) != len(self.values):
            raise ValueError("reference coordinates must match values")
        if any(x is None for x in self.values) and self.nonnull is not None:
            raise ValueError("unavailable reference cannot be assigned a null classification")
        if not self.testable and any(x is not None for x in self.values):
            raise ValueError("unavailable numerical reference must remain None")
        return self


class RhythmReference(FrozenContract):
    hypothesis_id: Identifier
    nonnull: bool | None
    period: Finite | None = None
    phases: tuple[ConditionPhase, ...] = ()
    damping: Finite | None = None


class GeneLoading(FrozenContract):
    gene_id: Identifier
    values: tuple[Finite, ...] = Field(max_length=20)


class MembershipReference(FrozenContract):
    regulator_id: Identifier
    gene_ids: tuple[Identifier, ...] = Field(max_length=100000)


class TargetReference(FrozenContract):
    target_id: TargetId
    estimand: Text
    units: Text
    contrast: Text
    reference_population: Text
    prediction_reference: Text
    scope: Literal["supported", "refusal", "limited_reference"]
    evidence_scope: EvidenceScope
    numerical_reference_available: bool = False
    forbidden_claims: tuple[Identifier, ...] = ()
    required_refusal_criteria: tuple[Identifier, ...] = ()
    metric_ids: tuple[Identifier, ...] = ()
    unsupported_reference_reason: Identifier | None = None
    measurement: Literal["raw_counts", "umi_counts", "non_umi_counts", "continuous"] | None = None
    biological_unit: Literal["donor", "organism", "experimental_culture", "observed_library"] | None = None
    reference_condition: Identifier | None = None
    reference_time: Finite | None = None
    fixed_effect_coordinate_ids: tuple[Identifier, ...] = Field(default=(), max_length=1000)
    efficacy_evidence_sha256: Sha256 | None = None
    covariate_contrast: Text | None = None
    hypotheses: tuple[HypothesisReference, ...] = Field(default=(), max_length=100000)
    curves: tuple[HypothesisReference, ...] = Field(default=(), max_length=100000)
    events: tuple[EventEstimate, ...] = Field(default=(), max_length=100000)
    event_hypothesis_ids: tuple[Identifier, ...] = Field(default=(), max_length=100000)
    rhythms: tuple[RhythmReference, ...] = Field(default=(), max_length=100000)
    phase_reference_time: Finite | None = None
    phase_convention: Literal["cosine_plus_phase"] | None = None
    frequency_domain: Interval | None = None
    concentrations: tuple[ConcentrationEstimate, ...] = Field(default=(), max_length=100000)
    cell_fractions: tuple[FractionEstimate, ...] = Field(default=(), max_length=100000)
    rna_fractions: tuple[FractionEstimate, ...] = Field(default=(), max_length=100000)
    fraction_label_invariant: bool = False
    denominator: Text | None = None
    graph: tuple[GraphEdge, ...] = Field(default=(), max_length=28)
    coordinates: tuple[CellCoordinate, ...] = Field(default=(), max_length=100000)
    direction_identifiable: bool = False
    rotation_invariant: bool = False
    reflection_invariant: bool = False
    full_cycle_covered: bool | None = None
    loadings: tuple[GeneLoading, ...] = Field(default=(), max_length=100000)
    association_pairs: tuple[GraphEdge, ...] = Field(default=(), max_length=100000)
    covariance_multiplier_by_condition: tuple[tuple[Identifier, Finite], ...] = ()
    candidates: tuple[Identifier, ...] = Field(default=(), max_length=100000)
    memberships: tuple[MembershipReference, ...] = Field(default=(), max_length=10000)
    enrichment_query_gene_ids: tuple[Identifier, ...] | None = Field(default=None, max_length=100000)
    annotation_id: Identifier | None = None
    diagnostic_values: tuple[DiagnosticValue, ...] = Field(default=(), max_length=100)
    shared_process_projection: SharedProcessProjection | None = None

    @model_validator(mode="after")
    def refusal_has_no_numerical_reference(self) -> TargetReference:
        if self.scope == "refusal" and (self.numerical_reference_available or self.hypotheses or self.curves
                                       or self.events or self.rhythms or self.concentrations
                                       or self.cell_fractions or self.rna_fractions or self.graph
                                       or self.coordinates or self.loadings or self.memberships):
            raise ValueError("refusal reference must omit private unidentifiable numerical truth")
        for rows in (self.hypotheses, self.curves):
            ids = [x.hypothesis_id for x in rows]
            if len(ids) != len(set(ids)):
                raise ValueError("reference hypotheses must be distinct")
        if len(set(self.fixed_effect_coordinate_ids)) != len(self.fixed_effect_coordinate_ids):
            raise ValueError("fixed effect coordinates must be distinct")
        for row in self.hypotheses:
            for coordinate, value in zip(row.coordinate_ids, row.values):
                if coordinate in self.fixed_effect_coordinate_ids and value is not None and abs(value) > 1e-12:
                    raise ValueError("declared algebraic reference contrast is not zero")
        if self.numerical_reference_available and self.target_id in {"state_topology", "sampled_state_geometry", "maturation_order", "cycle_state"} and self.shared_process_projection is None:
            raise ValueError("graph and order references require the declared public shared-process projection")
        return self


class BiologyReference(FrozenContract):
    record_format: Literal["devbench.biology-reference/1"] = "devbench.biology-reference/1"
    task_id: Identifier
    input_sha256: Sha256
    gene_universe_sha256: Sha256
    gene_ids: tuple[Identifier, ...] = Field(default=(), max_length=100000)
    cell_ids: tuple[Identifier, ...] = Field(default=(), max_length=100000)
    targets: tuple[TargetReference, ...] = Field(max_length=50)
    qualification: Literal["unqualified_simulation_reference"] = "unqualified_simulation_reference"
    thresholds_sha256: Sha256 = Field(default_factory=lambda: content_sha256(BiologyScoringThresholds().model_dump(mode="json")))

    @model_validator(mode="after")
    def references_are_bound(self) -> BiologyReference:
        ids = [x.target_id for x in self.targets]
        if len(ids) != len(set(ids)):
            raise ValueError("reference targets must be distinct")
        if len(self.gene_ids) != len(set(self.gene_ids)) or len(self.cell_ids) != len(set(self.cell_ids)):
            raise ValueError("reference identifiers must be distinct")
        if self.gene_ids and content_sha256(self.gene_ids) != self.gene_universe_sha256:
            raise ValueError("reference gene universe digest mismatch")
        return self


class SafeEstimateRow(FrozenContract):
    """Only participant estimates and accounting; never private gene truth."""
    target_id: TargetId
    hypothesis_id: Identifier
    gene_id: Identifier | None = None
    component_id: Identifier | None = None
    estimate_status: Literal["estimated", "unavailable", "failed", "not_tested", "missing"]
    values: tuple[Finite, ...] = ()
    p_value: Probability | None = None
    q_value: Probability | None = None
    missing_reason: Identifier | None = None


MetricId = Literal[
    "absolute_scale_error", "activity_correlation", "aliasing_diagnostic_accuracy", "ancestry_assignment_error",
    "annotation_overlap_accuracy", "assignment_effect_error", "association_pair_count", "association_support_fdp",
    "association_support_precision", "association_support_recall", "bh_arithmetic_valid", "branch_macro_f1",
    "cell_fraction_error", "cell_state_partition_error", "circular_phase_error", "concentration_error",
    "contamination_precision_recall", "correct_refusal_rate", "correction_bias", "curve_availability", "curve_error",
    "cycle_leakage", "cycle_signal_retention", "damping_error", "descriptive_effect_error", "diagnostic_accuracy",
    "difference_in_differences_error", "directional_error_rate", "discovery_count", "distribution_conditioned_coverage",
    "dose_curve_error", "effect_bias", "effect_error", "effect_interval_coverage", "effect_mae", "effect_rmse",
    "effective_unit_accounting", "efficacy_scope_error", "enrichment_arithmetic_valid", "enrichment_calibration",
    "epistasis_error", "estimate_availability", "event_f1", "event_interval_expansion", "event_precision", "event_recall",
    "evidence_scope_error", "false_cell_identity_rate", "false_cell_intrinsic_attribution", "false_equivalence_rate",
    "false_interaction_rate", "false_rhythm_rate", "false_scale_claim_rate", "hypothesis_accounting", "interaction_fdp",
    "interaction_recall", "interval_coverage", "interval_mean_width", "interval_width_ratio", "intervention_effect_error",
    "interval_availability", "interval_expected_coordinate_count", "interval_reported_coordinate_count",
    "interval_fixed_coordinates_excluded", "interval_required_confidence_level",
    "label_macro_f1", "maturation_order_agreement", "maturation_order_error", "mean_false_positive_rate", "mean_null_test_coverage",
    "module_covariance_support_f1", "module_recovery", "motif_enrichment_calibration", "null_gene_fdp", "null_type_i_error",
    "observed_arc_order_agreement", "partition_f1", "partition_precision", "partition_recall", "period_error",
    "population_fraction_error", "precision", "prior_scope_error", "rare_branch_recall", "rare_response_sensitivity",
    "realized_fdp", "recall", "recall_by_abundance", "regulator_rank_precision", "regulator_rank_recall",
    "relative_abundance_error", "relative_effect_error", "replication_diagnostic_accuracy", "residual_correlation_error",
    "rhythm_recall", "rna_fraction_error", "shape_macro_f1", "signed_recall", "state_abundance_scope_error",
    "subgroup_false_positive_rate", "subtype_fdp", "subtype_recall", "support_diagnostics", "target_retention",
    "tested_hypothesis_count", "timing_interval_coverage", "tissue_effect_error", "tissue_effect_recall",
    "topology_precision", "topology_recall", "topology_score", "unnecessary_refusal_rate", "unsupported_binding_rate",
    "unsupported_claim_count", "unsupported_direct_edge_rate", "unsupported_imputation_rate", "unsupported_transition_rate",
    "variability_recall", "viable_rare_cell_retention", "within_branch_order", "wrong_family_rate", "zero_frequency_calibration",
]
PUBLIC_METRIC_IDS = frozenset(get_args(MetricId))
PUBLIC_SAFE_ROW_FIELDS = frozenset({"target_id", "hypothesis_id", "gene_id", "component_id", "estimate_status",
                                     "values", "p_value", "q_value", "missing_reason"})


ReferenceAvailability = Literal["scorable", "unavailable"]
ScorableComponent = Literal[
    "structural_refusal", "effect_estimation", "gene_hypothesis_tests", "curve_estimation",
    "observed_event_intervals", "rhythm_tests", "period_phase_damping", "concentration_solutions_and_eligibility",
    "cell_fractions", "rna_fractions", "state_topology", "state_partition", "within_branch_order",
    "clone_equivalence", "maturation_order", "cycle_phase_equivalence", "observed_cycle_arc_order", "signal_covariance",
    "candidate_signal_ranking", "annotation_overlap_arithmetic", "unit_accounting", "measurement_route",
    "protocol_diagnostics", "clean_cell_state_partition", "target_retention",
]


class TargetReferenceStatus(FrozenContract):
    target_id: TargetId
    availability: ReferenceAvailability
    scope: Literal["supported", "refusal", "limited_reference"]
    scorable_components: tuple[ScorableComponent, ...] = ()
    unavailable_reason: Identifier | None = None


class TargetGrade(FrozenContract):
    target_id: TargetId
    outcome: Literal["scored", "correct_refusal", "missing_answer", "invalid_answer", "analysis_failed", "unsupported_reference"]
    scientific_success: bool | None
    metrics: dict[MetricId, Finite | bool | None] = Field(default_factory=dict)
    metric_missingness: dict[MetricId | Literal["scientific_success"], str] = Field(default_factory=dict)
    unsupported_claims: tuple[Identifier, ...] = ()
    errors: tuple[str, ...] = ()
    reference_availability: ReferenceAvailability = "unavailable"
    reference_scope: Literal["supported", "refusal", "limited_reference"] | None = None
    scorable_components: tuple[ScorableComponent, ...] = ()


class BiologyGrade(FrozenContract):
    record_format: Literal["devbench.biology-grade/1"] = "devbench.biology-grade/1"
    answer_sha256: Sha256 | None = None
    task_id: Identifier
    input_sha256: Sha256
    gene_universe_sha256: Sha256
    per_target_results: tuple[TargetGrade, ...]
    scientific_success: bool | None
    available_target_scientific_success: bool | None = None
    target_reference_availability: dict[TargetId, ReferenceAvailability] = Field(default_factory=dict)
    target_reference_missingness: dict[TargetId, Identifier] = Field(default_factory=dict)
    metric_missingness: dict[str, str] = Field(default_factory=dict)
    safe_rows: tuple[SafeEstimateRow, ...] = ()
    errors: tuple[str, ...] = ()
    thresholds_sha256: Sha256
    calibration_status: Literal["provisional_uncalibrated_locked"] = "provisional_uncalibrated_locked"
    narrative_status: Literal["unscored_requires_human_review"] = "unscored_requires_human_review"


def _authorized(permission: bool) -> None:
    if permission is not True:
        raise PermissionError("Biological result analysis requires allow_result_analysis=True")


_IDENTIFIED_RESTRAINT_REASONS = frozenset({
    "declared_numerical_reference_unavailable", "cell_identity_not_point_identifiable",
    "biological_identity_unavailable", "single_donor_population_fraction_not_identifiable",
})


def _requires_target_restraint(target: TargetReference) -> bool:
    if target.scope == "refusal" or target.unsupported_reference_reason in _IDENTIFIED_RESTRAINT_REASONS:
        return True
    return (TARGET_PAYLOAD_KINDS[target.target_id] == "effects" and bool(target.hypotheses)
            and not any(row.testable and row.values and all(value is not None for value in row.values)
                        for row in target.hypotheses))


def _reference_status(target: TargetReference) -> TargetReferenceStatus:
    """Answer-independent availability of this implementation's decision rule.

    Hypothesis masks remain private; this exposes only target-level scientific
    scope and component names. Descriptive protocol checks alone do not make a
    contamination-calibration target adjudicable.
    """
    components: list[ScorableComponent] = []
    reason = None
    if _requires_target_restraint(target):
        components.append("structural_refusal")
    elif not target.numerical_reference_available:
        reason = target.unsupported_reference_reason or "no_identified_target_reference"
    else:
        kind = TARGET_PAYLOAD_KINDS[target.target_id]
        if kind == "effects":
            eligible = tuple(x for x in target.hypotheses if x.testable and x.values and all(v is not None for v in x.values))
            if eligible:
                components.append("effect_estimation")
                if any(x.nonnull is not None for x in eligible):
                    components.append("gene_hypothesis_tests")
                if any(x.testable and x.values and all(v is not None for v in x.values) for x in target.curves):
                    components.append("curve_estimation")
            else:
                reason = "no_identifiable_effect_hypotheses"
        elif kind == "events":
            if target.event_hypothesis_ids:
                components.append("observed_event_intervals")
            else:
                reason = "event_hypothesis_universe_missing"
        elif kind == "rhythm":
            if target.rhythms and any(x.nonnull is not None for x in target.rhythms) and target.frequency_domain and target.phase_reference_time is not None and target.phase_convention is not None:
                components.append("rhythm_tests")
                if any(x.nonnull is True and x.period is not None and x.phases and x.damping is not None for x in target.rhythms):
                    components.append("period_phase_damping")
            else:
                reason = "rhythm_reference_or_analysis_domain_missing"
        elif kind == "concentration":
            if target.concentrations:
                components.append("concentration_solutions_and_eligibility")
            else:
                reason = "concentration_hypothesis_universe_missing"
        elif kind == "fractions":
            if target.cell_fractions and target.denominator:
                components.append("cell_fractions")
                if target.rna_fractions:
                    components.append("rna_fractions")
            else:
                reason = "fraction_reference_or_denominator_missing"
        elif kind == "graph":
            if target.graph and target.coordinates and target.shared_process_projection:
                components.extend(("state_topology", "state_partition", "within_branch_order"))
            else:
                reason = "projected_geometry_reference_missing"
        elif kind == "ancestry":
            if target.coordinates and any(x.group is not None for x in target.coordinates):
                components.append("clone_equivalence")
            else:
                reason = "independent_clone_equivalence_reference_missing"
        elif kind == "ordering":
            if not target.shared_process_projection:
                reason = "shared_process_projection_reference_missing"
            elif len(target.coordinates) < 3:
                reason = "insufficient_order_reference_support"
            else:
                components.append("maturation_order" if target.target_id == "maturation_order" else
                                  ("observed_cycle_arc_order" if target.full_cycle_covered is False else "cycle_phase_equivalence"))
        elif kind == "association":
            if target.target_id == "residual_modules" and target.loadings and target.association_pairs:
                components.append("signal_covariance")
            elif target.target_id == "association_activity" and target.loadings and target.candidates:
                components.append("candidate_signal_ranking")
            else:
                reason = "association_reference_universe_missing"
        elif kind == "enrichment":
            if target.memberships and target.annotation_id and target.enrichment_query_gene_ids is not None:
                components.append("annotation_overlap_arithmetic")
            else:
                reason = "bound_annotation_query_reference_missing"
        elif kind == "diagnostics":
            if target.target_id == "measurement_diagnostics":
                if target.diagnostic_values:
                    components.extend(("protocol_diagnostics", "structural_refusal"))
                else:
                    reason = "observable_protocol_reference_missing"
            elif target.diagnostic_values:
                components.append("unit_accounting" if target.target_id == "unit_accounting" else "measurement_route")
            else:
                reason = "observable_diagnostic_reference_missing"
        elif kind == "cell_state":
            if any(x.group is not None for x in target.coordinates):
                components.extend(("clean_cell_state_partition", "target_retention"))
            else:
                reason = "no_identifiable_cell_identity_subset"
        else:
            reason = "target_decision_reference_unavailable"
    return TargetReferenceStatus(target_id=target.target_id, availability="unavailable" if reason else "scorable",
                                 scope=target.scope, scorable_components=tuple(components), unavailable_reason=reason)


def _sequence(value: object) -> tuple:
    if isinstance(value, (tuple, list)):
        return tuple(value)
    if type(value).__module__.split(".")[0] == "numpy" and hasattr(value, "tolist"):
        return _sequence(value.tolist())
    raise ValueError("expected a bounded reference sequence")


def _mapping(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError("expected a reference object")
    return cast(Mapping[str, object], value)


def _number(value: object) -> float:
    if type(value).__module__.split(".")[0] == "numpy" and hasattr(value, "item"):
        value = value.item()
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError("reference number must be finite")
    return float(value)


def _flat(value: object) -> tuple[float | None, ...]:
    if value is None:
        return (None,)
    if isinstance(value, (tuple, list)) or hasattr(value, "shape") and getattr(value, "ndim", 0) > 0:
        return tuple(x for row in _sequence(value) for x in _flat(row))
    return (_number(value),)


def _flag(value: object) -> bool | None:
    if value is None:
        return None
    if type(value).__module__.split(".")[0] == "numpy" and hasattr(value, "item"):
        value = value.item()
    if type(value) is not bool:
        raise ValueError("reference null/support flag must be bool or None")
    return value


def _nonnull_flag(value: object) -> bool | None:
    if isinstance(value, (tuple, list)) or getattr(value, "ndim", 0) > 0:
        flags = tuple(_flag(v) for v in _sequence(value))
        return None if any(v is None for v in flags) else any(flags)
    return _flag(value)


def _refusal_criteria(name, raw, context, annotations):
    mapping = {"event_timing": "time_sampling_resolution", "rhythm": "rhythm_candidate_domain_identifiable",
        "effective_concentration": "dose_half_response_bracketed", "absolute_rna": "absolute_scale_anchor_verified",
        "direct_regulation": "direct_regulatory_edge_identified", "direct_edge": "direct_regulatory_edge_identified",
        "binding": "binding_assay_supplied", "transition_dynamics": "transition_dynamics_measured",
        "epistasis": "factorial_design_supported", "cell_state_effect": "independent_biological_units",
        "cell_fractions": "complete_mixture_reference", "clean_population_effect": "missingness_assumptions_supported",
        "population_abundance": "capture_sampling_calibrated", "regulon_membership": "regulon_reference_supplied",
        "motif_occurrence": "motif_reference_supplied"}
    if name == "successful_intervention":
        efficacy = tuple(_flat(raw.get("known_simulation_efficacy_a", ())))
        return ("achieved_intervention_positivity" if efficacy and all(value == 0 for value in efficacy)
                else "achieved_intervention_evidence",)
    if name == "adjusted_effect":
        return ("contrast_levels_observed" if "invalid_contrast" in raw else "design_rank_identifiable",)
    if name == "ancestry":
        return ("direction_anchor_observed" if annotations.get("history_markers") else "independent_lineage_history",)
    if name == "population_abundance":
        return (("direction_anchor_observed",) if annotations.get("capture_calibration")
                else ("direction_anchor_observed", "capture_sampling_calibrated"))
    if name == "sampled_mean":
        identity = _mapping(context.get("biological_units", {})).get("identity_status")
        return ("biological_identity_recorded" if identity == "intentionally_unknown" else "independent_biological_units",)
    return (mapping.get(name, "hypothesis_observed_information"),)


def _hypotheses(raw: Mapping[str, object], gene_ids: tuple[str, ...], *, field: str = "effect",
                component_ids: tuple[str, ...] = (), coordinate_name: str | None = None,
                skip_reference_coordinate: bool = False, component_coordinates: bool = False) -> tuple[HypothesisReference, ...]:
    from .biology_answers import public_hypothesis_id
    if field not in raw:
        return ()
    rows = _sequence(raw[field])
    if len(rows) != len(gene_ids):
        raise ValueError("target reference does not match the bound gene universe")
    nulls = _sequence(raw["nonnull"]) if "nonnull" in raw and field == "effect" else (None,) * len(rows)
    masks = _sequence(raw["hypothesis_testable"]) if "hypothesis_testable" in raw else (True,) * len(rows)
    if len(nulls) != len(rows) or len(masks) != len(rows):
        raise ValueError("reference hypothesis masks have wrong dimensions")
    type_mask = _sequence(raw["observed_type_support_mask"]) if "observed_type_support_mask" in raw else ()
    result = []
    for index, (gene, row) in enumerate(zip(gene_ids, rows)):
        pieces = _sequence(row) if component_ids else (row,)
        labels = component_ids or (None,)
        if len(pieces) != len(labels):
            raise ValueError("component labels must match the target's declared columns")
        gene_nulls = (_sequence(nulls[index]) if nulls[index] is not None else (None,) * len(labels)) if component_ids else (nulls[index],)
        for column, (label, piece) in enumerate(zip(labels, pieces)):
            values = _flat(piece)
            offset = 1 if skip_reference_coordinate else 0
            values = values[offset:]
            testable = _flag(masks[index]) is True and not any(v is None for v in values)
            if type_mask:
                testable = testable and _flag(type_mask[column]) is True
            nonnull = _nonnull_flag(gene_nulls[column]) if testable else None
            if field == "effect" and "nonnull" not in raw and testable:
                # Exact simulation contrast references define their explicit
                # omnibus null as all declared effect coordinates equal zero.
                # This never consults latent unavailable/counterfactual fields.
                nonnull = any(abs(v) > 1e-10 for v in values)
            result.append(HypothesisReference(
                hypothesis_id=public_hypothesis_id(gene, label), gene_id=gene, component_id=label,
                coordinate_ids=((label,) if component_coordinates and label is not None else
                    (coordinate_name or "contrast",) if len(values) == 1 else
                    tuple(f"grid_{i+offset}" for i in range(len(values)))),
                values=values if testable else tuple(None for _ in values), testable=testable,
                nonnull=nonnull, unavailable_reason=None if testable else "hypothesis_not_identifiable"))
    return tuple(result)


_EVIDENCE: dict[str, EvidenceScope] = {
    **{name: "population_mean" for name, kind in TARGET_PAYLOAD_KINDS.items() if kind == "effects"},
    "event_timing": "population_mean", "rhythm": "population_mean", "effective_concentration": "population_mean",
    "observed_difference": "observed_difference", "relative_expression": "relative_composition",
    "tissue_effect": "relative_composition", "absolute_rna": "anchored_absolute",
    "cell_state_effect": "within_cell_type", "cell_fractions": "relative_composition",
    "captured_abundance": "relative_composition", "population_abundance": "relative_composition",
    "state_topology": "state_geometry", "sampled_state_geometry": "state_geometry",
    "ancestry": "clone_equivalence", "maturation_order": "within_branch_ordering", "cycle_state": "state_geometry",
    "residual_modules": "association", "association_activity": "association",
    "regulon_membership": "curated_membership", "motif_occurrence": "motif_occurrence",
    "unit_accounting": "measurement_diagnostic", "measurement_route": "measurement_diagnostic",
    "measurement_diagnostics": "measurement_diagnostic", "clean_cell_state": "within_cell_type",
    "assignment_effect": "randomized_assignment", "successful_intervention": "achieved_intervention",
    "epistasis": "factorial_nonadditivity", "direct_regulation": "not_identified",
    "transition_dynamics": "not_identified", "binding": "not_identified", "direct_edge": "not_identified",
}


def _fractions(raw: object, population_ids: tuple[str, ...], *, transpose: bool = False,
               coordinate_ids: tuple[str, ...] = ()) -> tuple[FractionEstimate, ...]:
    matrix = tuple(_flat(row) for row in _sequence(raw))
    if transpose:
        matrix = tuple(tuple(row[i] for row in matrix) for i in range(len(matrix[0])))
    if len(matrix) != len(population_ids):
        raise ValueError("fraction population identifiers must match reference")
    if coordinate_ids and any(len(row) != len(coordinate_ids) for row in matrix):
        raise ValueError("Fraction coordinates must match the declared public condition axis")
    return tuple(FractionEstimate(population_id=label, coordinate_id=coordinate_ids[i] if coordinate_ids else f"coordinate_{i}", fraction=value)
                 for label, row in zip(population_ids, matrix) for i, value in enumerate(row))


def _declared_projection(public_context: Mapping[str, object], public_annotations: Mapping[str, object],
                         target: Mapping[str, object], gene_ids: tuple[str, ...]) -> SharedProcessProjection | None:
    """Bind public feature selection; never infer types from private cell labels."""
    public_record = public_context.get("shared_process_projection")
    if public_record is None:
        return None
    projection = SharedProcessProjection.model_validate_json(canonical_json_bytes(public_record))
    marker_reference = _mapping(public_annotations.get(projection.marker_reference_id, {}))
    markers = _sequence(marker_reference.get("markers", ()))
    marker_union = {str(gene) for item in markers for gene in _sequence(_mapping(item).get("gene_ids", ()))}
    excluded = tuple(sorted(marker_union))
    if not marker_union or not marker_union <= set(gene_ids):
        raise ValueError("public projection requires a valid broad-type marker dictionary")
    retained = tuple(gene for gene in gene_ids if gene not in marker_union)
    if projection.excluded_feature_ids != excluded or projection.retained_feature_universe != retained:
        raise ValueError("shared-process projection must exclude exactly the public marker union and retain input order")
    local_record = target.get("shared_process_projection")
    if local_record is None:
        return None
    local = SharedProcessProjection.model_validate_json(canonical_json_bytes(local_record))
    if local != projection:
        raise ValueError("target reference and public shared-process projection differ")
    return projection


def build_biology_reference(*, template: BiologyTemplate, private_truth: Mapping[str, object],
                            public_context: Mapping[str, object], public_annotations: Mapping[str, object],
                            task_id: str, input_sha256: str, gene_ids: tuple[str, ...],
                            cell_ids: tuple[str, ...] = (), allow_result_analysis: bool = False,
                            thresholds: BiologyScoringThresholds | None = None) -> BiologyReference:
    """Normalize only target-scoped truth; must run inside the coordinator boundary.

    Missing target-local identifiers/reference support produce explicit gaps.
    Neither case IDs, seeds, biological program membership nor global latent
    mechanism dictionaries are consulted. A refusal drops all numerical fields.
    """
    _authorized(allow_result_analysis)
    if "gene_ids" in private_truth and tuple(str(x) for x in _sequence(private_truth["gene_ids"])) != gene_ids:
        raise ValueError("private reference gene axis differs from the bound input universe")
    targets = _mapping(private_truth.get("targets"))
    measurement_value = public_context.get("measurement")
    measurement = (_mapping(measurement_value).get("measurement")
                   if isinstance(measurement_value, Mapping) else measurement_value)
    design = _mapping(public_context.get("design", {}))
    condition_ids = tuple(str(x) for x in _sequence(design.get("condition_levels", public_context.get("condition_labels", ()))))
    biological_unit = _mapping(public_context.get("biological_units", {})).get("population_unit", public_context.get("biological_unit"))
    result = []
    for contract in template.targets:
        name = contract.target_id
        if name not in TARGET_PAYLOAD_KINDS:
            raise ValueError(f"unregistered biology target: {name}")
        raw = _mapping(targets.get(name, {}))
        if "gene_ids" in raw and tuple(str(x) for x in _sequence(raw["gene_ids"])) != gene_ids:
            raise ValueError("target gene axis differs from the bound input universe")
        if "observation_ids" in raw and tuple(str(x) for x in _sequence(raw["observation_ids"])) != cell_ids:
            raise ValueError("target observation axis differs from the bound input cells")
        common = dict(target_id=name, estimand=contract.estimand, units=contract.measurement_units,
                      contrast=contract.contrast, reference_population=contract.reference_population,
                      prediction_reference=contract.prediction_reference, scope=raw.get("scope", "limited_reference"),
                      evidence_scope=_EVIDENCE[name], forbidden_claims=contract.forbidden_claims,
                      metric_ids=tuple(m.metric_id for m in contract.scientific_metrics), measurement=measurement,
                      biological_unit=biological_unit)
        target_populations = _mapping(public_context.get("target_populations", {}))
        if name in target_populations:
            common["reference_population"] = target_populations[name]
        public_references = _mapping(public_context.get("target_references", {}))
        if name in public_references:
            override = _mapping(public_references[name])
            for field in ("estimand", "units", "contrast", "reference_population", "prediction_reference"):
                if field in override:
                    common[field] = override[field]
        if not raw:
            result.append(TargetReference(**common, unsupported_reference_reason="target_reference_missing"))
            continue
        if raw.get("scope") == "refusal":
            result.append(TargetReference(**common, required_refusal_criteria=_refusal_criteria(name, raw, public_context, public_annotations)))
            continue
        fields: dict[str, object] = {}
        if name == "condition_main":
            times = _sequence(design.get("time_grid", ()))
            fields["reference_time"] = _number(times[0]) if times else None
        if name == "successful_intervention" and "efficacy_measurement" in public_annotations:
            fields["efficacy_evidence_sha256"] = content_sha256(public_annotations["efficacy_measurement"])
        if name in {"covariate_effect", "covariate_interaction"}:
            fields["reference_condition"] = condition_ids[0] if condition_ids else None
            fields["covariate_contrast"] = contract.contrast
        kind = TARGET_PAYLOAD_KINDS[name]
        if kind in {"graph", "ordering"}:
            if contract.shared_process_projection is None:
                result.append(TargetReference(**common, unsupported_reference_reason="shared_process_projection_contract_missing"))
                continue
            projection = _declared_projection(public_context, public_annotations, raw, gene_ids)
            if projection is None:
                result.append(TargetReference(**common, unsupported_reference_reason="shared_process_projection_reference_missing"))
                continue
            fields["shared_process_projection"] = projection
        if raw.get("numerical_reference_available") is False and not (name in {"rhythm", "effective_concentration"} and "nonnull" in raw):
            fields["unsupported_reference_reason"] = "declared_numerical_reference_unavailable"
        elif kind == "effects":
            uncertainty = effect_uncertainty_contract(name, public_context,
                public_context.get("analysis_reference_time"))
            if uncertainty is not None:
                fields["fixed_effect_coordinate_ids"] = tuple(uncertainty["fixed_effect_coordinate_ids"])
            components = tuple(str(x) for x in _sequence(raw.get("cell_type_labels", ()))) if name == "cell_state_effect" else ()
            if name in {"assignment_effect", "successful_intervention"} and "effect" in raw:
                components = condition_ids[1:]
                raw = {**raw, "effect": tuple(_sequence(row)[1:] for row in _sequence(raw["effect"]))}
                if "nonnull" in raw:
                    raw["nonnull"] = tuple(_sequence(row)[1:] for row in _sequence(raw["nonnull"]))
            if name == "cell_state_effect" and not components:
                fields["unsupported_reference_reason"] = "target_component_identifiers_missing"
            else:
                fields["hypotheses"] = _hypotheses(raw, gene_ids, component_ids=components,
                    coordinate_name="condition_contrast" if name == "cell_state_effect" else
                                    "factorial_nonadditivity" if name == "epistasis" else None,
                    skip_reference_coordinate=name == "time_interaction",
                    component_coordinates=name in {"assignment_effect", "successful_intervention"})
                fields["curves"] = _hypotheses(raw, gene_ids, field="curve")
        elif name == "event_timing":
            rows = _sequence(raw.get("event_intervals", ()))
            if len(rows) != len(gene_ids):
                raise ValueError("event reference must match feature universe")
            fields["events"] = tuple(EventEstimate(hypothesis_id=gene, kind=item["kind"],
                                                    interval=Interval(lower=_number(_sequence(item["interval"])[0]), upper=_number(_sequence(item["interval"])[1])))
                                     for gene, row in zip(gene_ids, rows) for value in _sequence(row) for item in (_mapping(value),))
            # Flat genes remain in the event universe: spurious events count.
            fields["event_hypothesis_ids"] = gene_ids
        elif name == "rhythm":
            flags = _sequence(raw.get("nonnull", ()))
            if len(flags) != len(gene_ids):
                raise ValueError("rhythm null universe must match genes")
            indices = tuple(int(x) for x in _sequence(raw.get("informative_gene_indices", ())))
            native_convention = raw.get("phase_convention")
            if indices and native_convention not in {"sine_plus_phase", "cosine_plus_phase"}:
                result.append(TargetReference(**common, unsupported_reference_reason="rhythm_phase_convention_missing"))
                continue
            if indices and raw.get("amplitude_convention") != "nonnegative":
                result.append(TargetReference(**common, unsupported_reference_reason="rhythm_amplitude_convention_missing"))
                continue
            phase_offset = -math.pi / 2 if native_convention == "sine_plus_phase" else 0.0
            phases = _mapping(raw.get("phase_radians_by_condition", {}))
            phase_rows = {str(k): _sequence(v) for k, v in phases.items()}
            lookup = {index: i for i, index in enumerate(indices)}
            fields["rhythms"] = tuple(RhythmReference(hypothesis_id=gene, nonnull=_flag(flags[i]),
                period=_number(raw["period"]) if i in lookup else None,
                phases=tuple(ConditionPhase(condition=c, phase_radians=(_number(values[lookup[i]]) % math.tau + phase_offset) % math.tau) for c, values in phase_rows.items()) if i in lookup else (),
                damping=_number(raw["damping_per_time_unit"]) if i in lookup else None) for i, gene in enumerate(gene_ids))
            time_grid = _sequence(design.get("time_grid", ()))
            reference_time = raw.get("phase_reference_time", time_grid[0] if time_grid else None)
            fields["phase_reference_time"] = _number(reference_time) if reference_time is not None else None
            fields["phase_convention"] = "cosine_plus_phase"
            domain = _sequence(_mapping(public_context.get("rhythm_analysis_domain", {})).get("frequency_cycles_per_time_unit", ()))
            if len(domain) == 2:
                fields["frequency_domain"] = Interval(lower=_number(domain[0]), upper=_number(domain[1]))
        elif name == "effective_concentration":
            indices = {int(x) for x in _sequence(raw.get("informative_gene_indices", ()))}
            unique = raw.get("unique_monotone_target") is True
            roots = ((_number(raw["effective_concentration"]),) if unique else tuple(_number(x) for x in _sequence(raw.get("half_response_root_set", ())))) if indices else ()
            fields["concentrations"] = tuple(ConcentrationEstimate(hypothesis_id=gene,
                solution_kind=("unique" if unique else "root_set") if i in indices else "unavailable",
                roots=roots if i in indices else (), missing_reason=None if i in indices else "flat_response_no_concentration")
                for i, gene in enumerate(gene_ids))
        elif kind == "fractions":
            if name == "cell_fractions":
                annotation = _mapping(public_annotations.get("cell_type_reference", {}))
                labels = tuple(str(x) for x in _sequence(annotation.get("cell_type_ids", ())))
                if "cell_fractions" in raw and labels:
                    fields["cell_fractions"] = _fractions(raw["cell_fractions"], labels)
                    fields["rna_fractions"] = _fractions(raw["rna_fractions"], labels)
                fields["denominator"] = "all_reference_cell_types_within_each_condition_and_grid_position"
            elif name == "captured_abundance":
                labels = tuple(str(x) for x in _sequence(raw.get("cell_type_labels", ())))
                if raw.get("scope") == "limited_reference" and "realized_captured_fractions" not in raw:
                    fields["unsupported_reference_reason"] = "single_donor_population_fraction_not_identifiable"
                elif raw.get("scope") == "limited_reference" and labels and "realized_captured_fractions" in raw:
                    fields["cell_fractions"] = _fractions(raw["realized_captured_fractions"], labels, transpose=True, coordinate_ids=condition_ids)
                elif labels and "population_fractions" in raw:
                    fields["cell_fractions"] = _fractions(raw["population_fractions"], labels, transpose=True, coordinate_ids=condition_ids)
                else:
                    fields["unsupported_reference_reason"] = "target_component_identifiers_missing"
                fields["denominator"] = str(raw.get("denominator", "all_captured_viable_cells"))
            else:
                values = _sequence(raw.get("fractions", ()))
                fields["cell_fractions"] = _fractions(tuple((x,) for x in values), tuple(f"branch_{i}" for i in range(len(values))))
                fields["fraction_label_invariant"] = True
                fields["denominator"] = str(raw["denominator"])
        elif kind == "graph":
            fields["graph"] = tuple(GraphEdge(source=str(edge[0]), target=str(edge[1])) for row in _sequence(raw.get("graph", ())) for edge in (_sequence(row),))
            order = _sequence(raw.get("within_component_order", raw.get("latent_time", ())))
            branch = _sequence(raw.get("observable_state_index", ()))
            if len(order) != len(cell_ids) or len(branch) != len(cell_ids):
                raise ValueError("state geometry reference must match bound cells")
            fields["coordinates"] = tuple(CellCoordinate(cell_id=cell, group=f"state_{int(group)}",
                order=_number(time) if time is not None else None) for cell, group, time in zip(cell_ids, branch, order))
        elif name == "ancestry":
            annotation = _mapping(public_annotations.get("history_markers", {}))
            records = _sequence(annotation.get("cell_records", ()))
            fields["coordinates"] = tuple(CellCoordinate(cell_id=str(row["cell_id"]), group=str(row["founder_record"])) for item in records for row in (_mapping(item),))
            fields["direction_identifiable"] = False
            if not records:
                fields["unsupported_reference_reason"] = "independent_history_records_missing"
        elif kind == "ordering":
            key = "latent_time" if name == "maturation_order" else "phase_radians"
            coordinates = _sequence(raw.get(key, ()))
            if len(coordinates) != len(cell_ids):
                raise ValueError("ordering reference must match bound cells")
            fields["coordinates"] = tuple(CellCoordinate(cell_id=cell, **({"order": _number(x)} if name == "maturation_order" else {"phase_radians": _number(x)})) for cell, x in zip(cell_ids, coordinates))
            fields["rotation_invariant"] = raw.get("rotation_invariant", False)
            fields["reflection_invariant"] = raw.get("reflection_invariant", False)
            fields["full_cycle_covered"] = raw.get("full_cycle_covered")
        elif kind == "association":
            loadings = _sequence(raw.get("loadings", ()))
            if len(loadings) != len(gene_ids):
                raise ValueError("association reference must match genes")
            fields["loadings"] = tuple(GeneLoading(gene_id=gene, values=_flat(values)) for gene, values in zip(gene_ids, loadings))
            if name == "residual_modules":
                fields["association_pairs"] = association_pair_universe(gene_ids)
            fields["candidates"] = tuple(str(x) for x in _sequence(public_context.get("transcription_factor_annotation", ())))
            multipliers = _sequence(raw.get("factor_variance_by_condition", ()))
            fields["covariance_multiplier_by_condition"] = tuple((label, _number(value)) for label, value in zip(condition_ids, multipliers))
            # T19 activity axes are arbitrarily rotated/scaled latent factors.
            # Only signal covariance and candidate ranking are scored.
        elif kind == "enrichment":
            annotation_id = "regulon_annotation" if name == "regulon_membership" else "motif_hit_annotation"
            annotation = _mapping(public_annotations.get(annotation_id, {}))
            rows = _sequence(annotation.get("memberships" if name == "regulon_membership" else "hits", ()))
            grouped: dict[str, set[str]] = {}
            measured = set(gene_ids)
            scanned: dict[str, set[str]] = {}
            occurrence_calls = {}
            for value in rows:
                row = _mapping(value)
                regulator, gene = str(row["tf_id"]), str(row["target_id"])
                grouped.setdefault(regulator, set())
                is_member = True
                if name == "motif_occurrence":
                    count = row.get("hit_count")
                    if type(count) is not int or count < 0:
                        raise ValueError("motif reference requires explicit nonnegative integer hit counts")
                    is_member = count > 0
                    pair = (regulator, gene)
                    if pair in occurrence_calls and occurrence_calls[pair] != is_member:
                        raise ValueError("conflicting motif reference occurrence calls")
                    occurrence_calls[pair] = is_member
                    scanned.setdefault(regulator, set()).add(gene)
                if is_member and gene in measured:
                    grouped[regulator].add(gene)
            fields["memberships"] = tuple(MembershipReference(regulator_id=k, gene_ids=tuple(sorted(v))) for k, v in sorted(grouped.items()))
            fields["annotation_id"] = annotation_id
            background = tuple(str(x) for x in _sequence(public_context.get("enrichment_background_gene_ids", gene_ids)))
            if len(background) != len(set(background)) or set(background) != set(gene_ids):
                raise ValueError("declared enrichment background differs from the bound feature universe")
            if "enrichment_query_gene_ids" in public_context:
                query = tuple(str(x) for x in _sequence(public_context["enrichment_query_gene_ids"]))
                if len(query) != len(set(query)) or not set(query) <= set(gene_ids):
                    raise ValueError("public enrichment query must be a distinct feature subset")
                fields["enrichment_query_gene_ids"] = query
            if not annotation:
                fields["unsupported_reference_reason"] = "required_public_annotation_missing"
            elif name == "motif_occurrence" and (not scanned or any(not measured <= observed for observed in scanned.values())):
                common["scope"] = "limited_reference"
                fields.update(numerical_reference_available=False,
                              unsupported_reference_reason="incomplete_motif_background_scan")
        elif kind == "diagnostics":
            diagnostics = []
            if name == "unit_accounting":
                if raw.get("identity_available") is True:
                    diagnostics.append(DiagnosticValue(code="biological_unit_count", value=int(raw["population_units"])))
                else:
                    fields["unsupported_reference_reason"] = "biological_identity_unavailable"
            elif name == "measurement_route":
                diagnostics.extend((DiagnosticValue(code="measurement_type", value=raw["measurement"]),
                                    DiagnosticValue(code="analysis_route", value=raw["analysis_route"])))
            else:
                diagnostics.extend((DiagnosticValue(code="measurement_type", value=measurement),
                                    DiagnosticValue(code="protocol", value=public_context["protocol"]),
                                    DiagnosticValue(code="analysis_layer", value=public_context["expression_layer"]),
                                    DiagnosticValue(code="biological_unit", value=biological_unit),
                                    DiagnosticValue(code="individual_artifact_identification", value="not_identified")))
            fields["diagnostic_values"] = tuple(diagnostics)
        elif kind == "cell_state":
            # Clustering is permutation invariant. A public marker dictionary
            # would be needed to score named identities; homotypic artifacts
            # and unresolved ambient mixtures are not exact identity targets.
            if "clean_identity_reference_mask" not in raw:
                fields["unsupported_reference_reason"] = "cell_identity_eligibility_not_declared"
            elif raw.get("clean_identity_numerical_reference_available") is not True:
                fields["unsupported_reference_reason"] = "cell_identity_not_point_identifiable"
            else:
                groups = _sequence(raw.get("cell_type", ()))
                mask = _sequence(raw["clean_identity_reference_mask"])
                if len(groups) != len(cell_ids) or len(mask) != len(cell_ids):
                    raise ValueError("cell-state reference must match bound cells")
                fields["coordinates"] = tuple(CellCoordinate(cell_id=cell, group=f"type_{int(group)}" if _flag(eligible) else None) for cell, group, eligible in zip(cell_ids, groups, mask))
        else:
            fields["unsupported_reference_reason"] = "no_identified_numeric_reference"
        has_reference = any(fields.get(key) for key in ("hypotheses", "curves", "event_hypothesis_ids", "rhythms", "concentrations",
            "cell_fractions", "graph", "coordinates", "loadings", "memberships", "diagnostic_values"))
        fields["numerical_reference_available"] = bool(has_reference) and "unsupported_reference_reason" not in fields
        if not has_reference and "unsupported_reference_reason" not in fields:
            fields["unsupported_reference_reason"] = "declared_reference_has_no_scorable_values"
        result.append(TargetReference(**common, **fields))
    locked = thresholds or BiologyScoringThresholds()
    return BiologyReference(task_id=task_id, input_sha256=input_sha256, gene_universe_sha256=content_sha256(gene_ids),
                            gene_ids=gene_ids, cell_ids=cell_ids, targets=tuple(result),
                            thresholds_sha256=content_sha256(locked.model_dump(mode="json")))


def biology_scoring_registrations(catalog: BiologyCatalog) -> tuple[CapabilityRegistration, ...]:
    """Exact configuration coverage; never invokes or qualifies a scorer."""
    result = []
    for template in catalog.templates:
        for target in template.targets:
            if target.target_id not in TARGET_PAYLOAD_KINDS:
                continue
            for layer in template.observation_layers:
                cases = tuple(c.case_id for c in template.cases if c.measurement == layer.measurement)
                if cases:
                    result.append(CapabilityRegistration(kind="scorer", capability_id=target.scorer_capability_id,
                        template_id=template.template_id, case_ids=cases, target_ids=(target.target_id,),
                        measurement=layer.measurement, handler=grade_biology_answer))
    return tuple(result)


def association_pair_universe(gene_ids: tuple[str, ...]) -> tuple[GraphEdge, ...]:
    """Public deterministic comparison pairs; independent of any observed value.

    Sorted-ID circular offsets 1, 2, 4 and 8 provide at most 4G pairs. Staging
    must supply this exact rule/pair list; the grader never picks favorable
    edges after seeing truth or participant predictions.
    """
    genes = tuple(sorted(gene_ids))
    if len(genes) > 25000 or len(genes) != len(set(genes)):
        raise ValueError("association universe exceeds bounds or has duplicate genes")
    pairs = {tuple(sorted((gene, genes[(i + offset) % len(genes)])))
             for i, gene in enumerate(genes) for offset in (1, 2, 4, 8)
             if len(genes) > offset}
    return tuple(GraphEdge(source=a, target=b) for a, b in sorted(pairs))


def _mean(values: tuple | list) -> float | None:
    if not values:
        return None
    scale = max(abs(x) for x in values)
    value = scale * (math.fsum(x / scale for x in values) / len(values)) if scale else 0.0
    return value if math.isfinite(value) else None


def _difference(left: float, right: float) -> float | None:
    value = left - right
    return value if math.isfinite(value) else None


def _rmse(errors: tuple | list) -> float | None:
    if not errors or any(x is None for x in errors):
        return None
    scale = max(abs(x) for x in errors)
    return scale * math.sqrt(math.fsum((x / scale) ** 2 for x in errors) / len(errors)) if scale else 0.0


def _ratio(a: int | float, b: int | float) -> float | None:
    return a / b if b else None


def circular_distance(left: float, right: float, *, allow_result_analysis: bool = False) -> float:
    _authorized(allow_result_analysis)
    # Reduce separately: subtraction of two admitted finite floats may overflow.
    return abs((left % math.tau - right % math.tau + math.pi) % math.tau - math.pi)


def _phase_at_reference(phase: float, period: float, reported_reference_time: float, comparison_reference_time: float) -> float | None:
    """Transform A*cos(ω*(t-t0)+φ) to the common reference time, safely."""
    displacement = _difference(comparison_reference_time, reported_reference_time)
    if displacement is None:
        return None
    # Modulo before multiplication avoids overflow from large equivalent
    # time origins; a reference-time change is not evidence of a phase error.
    return (phase % math.tau + math.tau * ((displacement % period) / period)) % math.tau


def root_set_error(left: tuple[float, ...], right: tuple[float, ...], *, allow_result_analysis: bool = False) -> float | None:
    _authorized(allow_result_analysis)
    if not left or len(left) != len(right):
        return None
    return _rmse(tuple(_difference(a, b) for a, b in zip(sorted(left), sorted(right))))


def _ranks(values: tuple[float, ...]) -> tuple[float, ...]:
    order = sorted(range(len(values)), key=values.__getitem__)
    ranks = [0.0] * len(values)
    start = 0
    while start < len(order):
        end = start + 1
        while end < len(order) and values[order[end]] == values[order[start]]:
            end += 1
        for i in order[start:end]:
            ranks[i] = (start + end - 1) / 2
        start = end
    return tuple(ranks)


def rank_agreement(left: tuple[float, ...], right: tuple[float, ...], *, orientation_invariant: bool,
                   allow_result_analysis: bool = False) -> float | None:
    _authorized(allow_result_analysis)
    if len(left) != len(right) or len(left) < 3:
        return None
    x, y = _ranks(left), _ranks(right)
    mx, my = _mean(x), _mean(y)
    dx, dy = tuple(v - mx for v in x), tuple(v - my for v in y)
    denominator = math.sqrt(math.fsum(v * v for v in dx) * math.fsum(v * v for v in dy))
    if denominator == 0:
        return None
    rho = max(-1.0, min(1.0, math.fsum(a * b for a, b in zip(dx, dy)) / denominator))
    return abs(rho) if orientation_invariant else (1 + rho) / 2


def partition_scores(reference: tuple[str, ...], predicted: tuple[str, ...], *, allow_result_analysis: bool = False) -> dict[str, float | None]:
    """Pair co-membership, computed from contingency counts without n² arrays."""
    _authorized(allow_result_analysis)
    if len(reference) != len(predicted) or not reference:
        return {"partition_precision": None, "partition_recall": None, "partition_f1": None}
    choose2 = lambda n: n * (n - 1) // 2
    matched = sum(choose2(n) for n in Counter(zip(reference, predicted)).values())
    truth_pairs = sum(choose2(n) for n in Counter(reference).values())
    prediction_pairs = sum(choose2(n) for n in Counter(predicted).values())
    result = {"partition_precision": _ratio(matched, prediction_pairs),
            "partition_recall": _ratio(matched, truth_pairs),
            "partition_f1": 2 * matched / (truth_pairs + prediction_pairs) if truth_pairs + prediction_pairs else 1.0}
    truth_groups, pred_groups = tuple(sorted(set(reference))), tuple(sorted(set(predicted)))
    n = max(len(truth_groups), len(pred_groups))
    result["label_macro_f1"] = None
    if n <= 8:
        truth_count, pred_count, together = Counter(reference), Counter(predicted), Counter(zip(reference, predicted))
        best = 0.0
        for mapping in permutations(range(n), len(pred_groups)):
            score = 0.0
            for column, row in enumerate(mapping):
                if row < len(truth_groups):
                    a, b = truth_groups[row], pred_groups[column]
                    score += 2 * together[a, b] / (truth_count[a] + pred_count[b])
            best = max(best, score / n)
        result["label_macro_f1"] = best
    return result


def graph_scores(reference: tuple[tuple[str, str], ...], predicted: tuple[tuple[str, str], ...], *, directed: bool,
                 allow_result_analysis: bool = False) -> dict[str, float | None]:
    """Maximum edge overlap under node permutation; at most eight state nodes."""
    _authorized(allow_result_analysis)
    ref_nodes = tuple(sorted({x for edge in reference for x in edge}))
    pred_nodes = tuple(sorted({x for edge in predicted for x in edge}))
    if len(ref_nodes) > 8 or len(pred_nodes) > 8:
        raise ValueError("state graph exceeds the eight-node exact-matching limit")
    if any(a == b for a, b in (*reference, *predicted)):
        raise ValueError("self-loops are not state-transition edges")
    canonical = (lambda a, b: (a, b)) if directed else (lambda a, b: tuple(sorted((a, b))))
    ref_edges = {canonical(a, b) for a, b in reference}
    pred_edges = {canonical(a, b) for a, b in predicted}
    if len(ref_edges) != len(reference) or len(pred_edges) != len(predicted):
        raise ValueError("duplicate state edges are invalid")
    if not ref_edges:
        return {"topology_score": None, "topology_precision": None, "topology_recall": None}
    count = max(len(ref_nodes), len(pred_nodes))
    ref_labels = tuple(range(len(ref_nodes)))
    lookup = dict(zip(ref_nodes, ref_labels))
    expected = {canonical(lookup[a], lookup[b]) for a, b in ref_edges}
    best = 0
    for labels in permutations(range(count), len(pred_nodes)):
        mapping = dict(zip(pred_nodes, labels))
        candidate = {canonical(mapping[a], mapping[b]) for a, b in pred_edges}
        best = max(best, len(expected & candidate))
        if best == min(len(ref_edges), len(pred_edges)):
            break
    return {"topology_score": 2 * best / (len(ref_edges) + len(pred_edges)),
            "topology_precision": _ratio(best, len(pred_edges)), "topology_recall": _ratio(best, len(ref_edges))}


def _bh_minimum(pvalues: Mapping[str, float]) -> dict[str, float]:
    ordered = sorted(pvalues, key=lambda key: (pvalues[key], key))
    result = {}
    previous = 1.0
    n = len(ordered)
    for index in range(n - 1, -1, -1):
        key = ordered[index]
        previous = min(previous, pvalues[key] * n / (index + 1))
        result[key] = previous
    return result


def _safe_rows(target: TargetReference, payload: EffectsPayload) -> tuple[SafeEstimateRow, ...]:
    submitted = {x.hypothesis_id: x for x in payload.gene_statistics}
    rows = []
    for ref in target.hypotheses:
        estimate = submitted.get(ref.hypothesis_id)
        if estimate is None:
            rows.append(SafeEstimateRow(target_id=target.target_id, hypothesis_id=ref.hypothesis_id,
                gene_id=ref.gene_id, component_id=ref.component_id, estimate_status="missing", missing_reason="answer_omitted_hypothesis"))
        else:
            rows.append(SafeEstimateRow(target_id=target.target_id, **estimate.model_dump(exclude={"coordinate_ids", "intervals"})))
    return tuple(rows)


def _effect_metrics(target: TargetReference, payload: EffectsPayload, thresholds: BiologyScoringThresholds) -> tuple[dict, list[str], list[str], bool | None]:
    submitted = {x.hypothesis_id: x for x in payload.gene_statistics}
    expected = {x.hypothesis_id: x for x in target.hypotheses}
    errors: list[str] = []
    unsupported: list[str] = []
    if target.target_id == "cell_state_effect" and not payload.aggregation_definition:
        errors.append("pseudobulk_aggregation_definition_missing")
    if target.target_id == "condition_main" and payload.reference_time != target.reference_time:
        errors.append("condition_main_reference_time_mismatch")
    if target.target_id == "successful_intervention" and (
            target.efficacy_evidence_sha256 is None or payload.efficacy_evidence_sha256 != target.efficacy_evidence_sha256):
        errors.append("achieved_intervention_evidence_missing_or_mismatched")
    if set(submitted) - set(expected):
        errors.append("foreign_hypothesis")
    if payload.analysis_layer != target.measurement:
        errors.append("measurement_layer_mismatch")
    if payload.effect_definition != target.contrast:
        errors.append("effect_definition_mismatch")
    if payload.biological_unit == "observed_library" and target.evidence_scope != "observed_difference":
        unsupported.append("libraries_not_independent_biological_units")
    if target.biological_unit and target.evidence_scope != "observed_difference" and payload.biological_unit != target.biological_unit:
        errors.append("biological_unit_definition_mismatch")
    if target.target_id == "adjusted_effect":
        if payload.condition_number_validated is not True or payload.condition_number is None:
            errors.append("adjusted_design_condition_validation_missing")
        if payload.design_rank is None or payload.design_rank != payload.design_column_count:
            errors.append("adjusted_design_rank_not_validated")
    if target.target_id == "absolute_rna" and not payload.scale_anchor:
        unsupported.append("absolute_scale_anchor_missing")
    if target.target_id in {"covariate_effect", "covariate_interaction"} and (payload.reference_condition != target.reference_condition or payload.covariate_contrast != target.covariate_contrast):
        errors.append("within_condition_covariate_contrast_mismatch")
    deltas, interval_hits, widths, scales, directions = [], [], [], [], []
    discoveries, true_discoveries, signed_discoveries, false_discoveries = 0, 0, 0, 0
    eligible = tuple(x for x in target.hypotheses if x.testable and x.values and all(v is not None for v in x.values))
    fixed_coordinates = set(target.fixed_effect_coordinate_ids)
    intervals_required = bool(EFFECT_INTERVAL_METRICS.intersection(target.metric_ids))
    fixed_interval_count = sum(coordinate in fixed_coordinates for row in eligible for coordinate in row.coordinate_ids)
    expected_interval_count = sum(len(row.values) for row in eligible) - fixed_interval_count
    tested, usable, null_pvalues = {}, 0, []
    nonnull_count = sum(ref.nonnull is True for ref in eligible)
    known_hypotheses = sum(ref.nonnull is not None for ref in eligible)
    for ref in target.hypotheses:
        estimate = submitted.get(ref.hypothesis_id)
        if not ref.testable or any(x is None for x in ref.values):
            if estimate and estimate.estimate_status == "estimated":
                unsupported.append("numerical_claim_for_unavailable_hypothesis")
            continue
        if estimate is None or estimate.estimate_status != "estimated":
            continue
        if estimate.gene_id != ref.gene_id or estimate.component_id != ref.component_id:
            errors.append("hypothesis_entity_binding_mismatch")
            continue
        if len(estimate.values) != len(ref.values) or estimate.coordinate_ids != ref.coordinate_ids:
            errors.append("hypothesis_coordinate_mismatch")
            continue
        usable += 1
        local = tuple(_difference(v, r) for v, r in zip(estimate.values, ref.values))
        if any(v is None for v in local):
            errors.append("arithmetic_overflow")
            continue
        deltas.extend(local)
        sign_ok = all((v > 0) == (r > 0) and abs(v) > thresholds.numeric_tolerance
                      for v, r in zip(estimate.values, ref.values) if abs(r) > thresholds.numeric_tolerance)
        for i, interval in enumerate(estimate.intervals):
            if ref.coordinate_ids and ref.coordinate_ids[i] in fixed_coordinates:
                continue
            if interval is not None:
                if intervals_required and interval.confidence_level != POINTWISE_CONFIDENCE_LEVEL:
                    continue
                width = _difference(interval.upper, interval.lower)
                if width is None:
                    errors.append("arithmetic_overflow")
                    continue
                interval_hits.append(float(interval.lower <= ref.values[i] <= interval.upper))
                widths.append(width)
                scales.append(max(abs(ref.values[i]), thresholds.maximum_effect_rmse))
        if estimate.p_value is not None:
            tested[ref.hypothesis_id] = estimate.p_value
            if ref.nonnull is False:
                null_pvalues.append(estimate.p_value)
            if estimate.q_value <= thresholds.alpha:
                if ref.nonnull is None:
                    unsupported.append("significance_without_valid_null_reference")
                else:
                    discoveries += 1
                    if ref.nonnull:
                        true_discoveries += 1
                        signed_discoveries += int(sign_ok)
                        directions.append(float(not sign_ok))
                    else:
                        false_discoveries += 1
    # The complete declared family includes failed/unavailable hypotheses. Their
    # internal p=1 positions reserve multiplicity without publishing fake tests.
    minimum_q = _bh_minimum({x.hypothesis_id: x.p_value if x.p_value is not None else 1.0
                             for x in payload.gene_statistics})
    bh_valid = all(abs(x.q_value - minimum_q[x.hypothesis_id]) <= thresholds.numeric_tolerance
                   for x in payload.gene_statistics if x.p_value is not None)
    if not bh_valid:
        errors.append("bh_adjustment_inconsistent")
    if intervals_required and len(interval_hits) != expected_interval_count:
        errors.append("required_pointwise_intervals_missing_or_wrong_level")
    complete_testing = all(ref.hypothesis_id in tested for ref in eligible if ref.nonnull is not None)
    effect_rmse = _rmse(deltas)
    availability = _ratio(usable, len(eligible))
    metrics = {"effect_rmse": effect_rmse, "effect_mae": _mean(tuple(abs(x) for x in deltas)),
               "effect_bias": _mean(deltas),
               "estimate_availability": availability, "hypothesis_accounting": _ratio(len(set(submitted) & set(expected)), len(expected)),
               "signed_recall": _ratio(signed_discoveries, nonnull_count), "recall": _ratio(true_discoveries, nonnull_count),
               "precision": _ratio(true_discoveries, discoveries),
               # FDP of an empty rejection set is conventionally zero. Precision
               # of that empty set and recall in an all-null family are undefined.
               "realized_fdp": false_discoveries / max(1, discoveries) if known_hypotheses else None,
               "directional_error_rate": _mean(directions),
               "bh_arithmetic_valid": bh_valid if any(row.p_value is not None for row in payload.gene_statistics) else None,
               "null_type_i_error": _mean(tuple(float(p <= thresholds.alpha) for p in null_pvalues)),
               "interval_coverage": _mean(interval_hits), "interval_mean_width": _mean(widths),
               "interval_availability": _ratio(len(interval_hits), expected_interval_count),
               "interval_expected_coordinate_count": float(expected_interval_count),
               "interval_reported_coordinate_count": float(len(interval_hits)),
               "interval_fixed_coordinates_excluded": float(fixed_interval_count),
               "interval_required_confidence_level": POINTWISE_CONFIDENCE_LEVEL if intervals_required else None,
               "interval_width_ratio": _mean(tuple(w / s for w, s in zip(widths, scales))),
               "discovery_count": float(discoveries), "tested_hypothesis_count": float(len(tested))}
    curve_deltas = []
    curve_rows = {x.hypothesis_id: x for x in payload.curve_estimates}
    if set(curve_rows) - {x.hypothesis_id for x in target.curves}:
        errors.append("foreign_curve_hypothesis")
    usable_curves = 0
    for ref in target.curves:
        curve = curve_rows.get(ref.hypothesis_id)
        if not ref.testable or any(x is None for x in ref.values):
            if curve and curve.estimate_status == "estimated":
                unsupported.append("numerical_claim_for_unavailable_hypothesis")
            continue
        if curve is None or curve.estimate_status != "estimated":
            continue
        if curve.coordinate_ids != ref.coordinate_ids or len(curve.values) != len(ref.values):
            errors.append("curve_coordinate_mismatch")
            continue
        usable_curves += 1
        scale = max(1.0, max(abs(v) for v in ref.values))
        curve_deltas.extend((v / scale - r / scale) for v, r in zip(curve.values, ref.values))
    metrics["curve_error"] = _rmse(curve_deltas)
    eligible_curves = sum(x.testable and all(v is not None for v in x.values) for x in target.curves)
    metrics["curve_availability"] = _ratio(usable_curves, eligible_curves)
    if not eligible:
        return metrics, errors, unsupported, None
    success = bool(payload.analysis_performed and not errors and not unsupported and availability is not None
                   and availability >= thresholds.minimum_estimate_availability and set(submitted) == set(expected)
                   and effect_rmse is not None and effect_rmse <= thresholds.maximum_effect_rmse)
    if known_hypotheses:
        success = success and complete_testing and metrics["realized_fdp"] <= thresholds.maximum_fdp
        if nonnull_count:
            success = success and metrics["signed_recall"] >= thresholds.minimum_signed_recall
    if target.curves:
        success = success and set(curve_rows) == {x.hypothesis_id for x in target.curves}
        success = success and metrics["curve_error"] is not None and metrics["curve_error"] <= thresholds.maximum_relative_curve_rmse
        success = success and metrics["curve_availability"] is not None and metrics["curve_availability"] >= thresholds.minimum_estimate_availability
    if interval_hits:
        success = success and metrics["interval_coverage"] >= thresholds.minimum_interval_coverage
        success = success and metrics["interval_width_ratio"] <= thresholds.maximum_interval_width_ratio
    return metrics, errors, unsupported, bool(success)


def _event_metrics(target: TargetReference, payload: EventsPayload, thresholds: BiologyScoringThresholds) -> tuple[dict, list[str], bool]:
    errors = []
    if not target.event_hypothesis_ids:
        errors.append("event_hypothesis_universe_missing")
    if set(payload.eligible_hypotheses) != set(target.event_hypothesis_ids):
        errors.append("event_hypothesis_accounting_mismatch")
    if target.event_hypothesis_ids and (len(target.event_hypothesis_ids)-len(payload.unavailable_hypotheses))/len(target.event_hypothesis_ids) < thresholds.minimum_estimate_availability:
        errors.append("event_estimate_availability_below_threshold")
    remaining = set(range(len(target.events)))
    hits, expansions = 0, []
    # Match same-kind events by minimum interval discrepancy, independently
    # of record order; over-wide intervals do not establish timing precision.
    for prediction in sorted(payload.event_intervals, key=lambda x: (x.hypothesis_id, x.kind, x.interval.lower)):
        candidates = [i for i in remaining if target.events[i].hypothesis_id == prediction.hypothesis_id
                      and target.events[i].kind == prediction.kind]
        matches = []
        for index in candidates:
            ref = target.events[index].interval
            width = _difference(prediction.interval.upper, prediction.interval.lower)
            ref_width = _difference(ref.upper, ref.lower)
            if width is None or ref_width is None:
                errors.append("arithmetic_overflow")
                continue
            expansion = width / max(ref_width, thresholds.numeric_tolerance)
            overlap = min(ref.upper, prediction.interval.upper) - max(ref.lower, prediction.interval.lower)
            # An exact point inside a multi-observation bracket fabricates
            # continuous-time precision. Require the observed bracket itself.
            brackets_reference = prediction.interval.lower <= ref.lower and prediction.interval.upper >= ref.upper
            if overlap >= 0 and brackets_reference and expansion <= thresholds.maximum_interval_expansion:
                matches.append((expansion, index))
        if matches:
            expansion, index = min(matches)
            remaining.remove(index)
            hits += 1
            expansions.append(expansion)
    nref, npred = len(target.events), len(payload.event_intervals)
    precision, recall = _ratio(hits, npred), _ratio(hits, nref)
    f1 = 2 * hits / (nref + npred) if nref + npred else 1.0
    metrics = {"event_precision": precision, "event_recall": recall, "event_f1": f1,
               "timing_interval_coverage": recall, "event_interval_expansion": _mean(expansions)}
    return metrics, errors, not errors and f1 >= thresholds.minimum_event_f1


def _rhythm_metrics(target: TargetReference, payload: RhythmPayload, thresholds: BiologyScoringThresholds) -> tuple[dict, list[str], list[str], bool]:
    submitted = {x.hypothesis_id: x for x in payload.rhythms}
    refs = {x.hypothesis_id: x for x in target.rhythms}
    errors, unsupported = [], []
    if len(submitted) != len(payload.rhythms) or set(submitted) != set(refs):
        errors.append("rhythm_hypothesis_accounting_mismatch")
    if target.phase_reference_time is None or target.phase_convention != payload.phase_convention:
        errors.append("phase_reference_convention_unavailable_or_mismatched")
    if target.frequency_domain is None or payload.candidate_frequency_domain != target.frequency_domain:
        errors.append("candidate_frequency_domain_mismatch")
    if payload.sampling_ambiguity:
        unsupported.append("period_not_identified_under_declared_sampling")
    period_errors, phase_errors, damping_errors = [], [], []
    discoveries, false_discoveries, detected, available, nonnull = 0, 0, 0, 0, 0
    pvalues = {x.hypothesis_id: x.p_value for x in payload.rhythms if x.p_value is not None}
    minimum_q = _bh_minimum(pvalues)
    if any(abs(x.q_value - minimum_q[x.hypothesis_id]) > thresholds.numeric_tolerance for x in payload.rhythms if x.q_value is not None):
        errors.append("bh_adjustment_inconsistent")
    for key, ref in refs.items():
        row = submitted.get(key)
        nonnull += ref.nonnull is True
        if row is None or row.estimate_status != "estimated":
            continue
        available += 1
        if row.rhythmic != (row.q_value <= thresholds.alpha):
            errors.append("rhythm_discovery_not_consistent_with_q")
        if row.rhythmic:
            if ref.nonnull is None:
                unsupported.append("rhythm_claim_without_identified_reference")
                continue
            discoveries += 1
            false_discoveries += ref.nonnull is False
        if ref.nonnull is not True or not row.rhythmic:
            continue
        detected += 1
        period_errors.append(abs(row.period / ref.period - 1.0))
        truth_phases = {p.condition: p.phase_radians for p in ref.phases}
        predicted_phases = {p.condition: p.phase_radians for p in row.phase_by_condition}
        if set(truth_phases) != set(predicted_phases):
            errors.append("rhythm_condition_phase_mismatch")
        else:
            for condition in truth_phases:
                aligned = (_phase_at_reference(predicted_phases[condition], row.period, payload.phase_reference_time,
                                                target.phase_reference_time) if target.phase_reference_time is not None else None)
                if aligned is None:
                    errors.append("phase_reference_transform_unavailable")
                else:
                    phase_errors.append(circular_distance(aligned, truth_phases[condition], allow_result_analysis=True))
        difference = _difference(row.damping, ref.damping)
        if difference is None:
            errors.append("arithmetic_overflow")
        else:
            damping_errors.append(abs(difference))
    metrics = {"period_error": _mean(period_errors), "circular_phase_error": _mean(phase_errors),
               "damping_error": _mean(damping_errors), "rhythm_recall": _ratio(detected, nonnull),
               "false_rhythm_rate": false_discoveries / max(1, sum(r.nonnull is False for r in refs.values())),
               "realized_fdp": false_discoveries / max(1, discoveries),
               "estimate_availability": _ratio(available, len(refs))}
    success = not errors and not unsupported and available == len(refs)
    if nonnull:
        success = success and metrics["rhythm_recall"] >= thresholds.minimum_signed_recall
        for name, cutoff in (("period_error", thresholds.maximum_relative_period_error),
                             ("circular_phase_error", thresholds.maximum_circular_phase_error),
                             ("damping_error", thresholds.maximum_damping_error)):
            success = success and metrics[name] is not None and metrics[name] <= cutoff
    success = success and metrics["realized_fdp"] <= thresholds.maximum_fdp
    return metrics, errors, unsupported, bool(success)


def _concentration_metrics(target: TargetReference, payload: ConcentrationPayload, thresholds: BiologyScoringThresholds) -> tuple[dict, list[str], list[str], bool]:
    submitted = {x.hypothesis_id: x for x in payload.concentration_result}
    refs = {x.hypothesis_id: x for x in target.concentrations}
    errors, unsupported, differences = [], [], []
    if not refs:
        errors.append("concentration_hypothesis_universe_missing")
    if len(submitted) != len(payload.concentration_result) or set(submitted) != set(refs):
        errors.append("concentration_hypothesis_accounting_mismatch")
    eligible, estimated, unnecessary_refusals = 0, 0, 0
    for key, ref in refs.items():
        row = submitted.get(key)
        if ref.solution_kind == "unavailable":
            if row and row.solution_kind != "unavailable":
                unsupported.append("concentration_for_flat_or_unresolved_response")
            continue
        eligible += 1
        if row is None or row.solution_kind == "unavailable":
            unnecessary_refusals += 1
            continue
        if row.solution_kind != ref.solution_kind or len(row.roots) != len(ref.roots):
            errors.append("concentration_solution_kind_mismatch")
            continue
        error = root_set_error(row.roots, ref.roots, allow_result_analysis=True)
        if error is None:
            errors.append("arithmetic_overflow")
        else:
            estimated += 1
            differences.append(error / max(max(ref.roots), thresholds.numeric_tolerance))
    metrics = {"concentration_error": _mean(differences), "estimate_availability": _ratio(estimated, eligible),
               "unnecessary_refusal_rate": _ratio(unnecessary_refusals, eligible)}
    success = not errors and not unsupported and estimated == eligible
    if eligible:
        success = success and metrics["concentration_error"] is not None and metrics["concentration_error"] <= thresholds.maximum_relative_concentration_error
    return metrics, errors, unsupported, bool(success)


def _fraction_metrics(target: TargetReference, payload: FractionsPayload, thresholds: BiologyScoringThresholds) -> tuple[dict, list[str], list[str], bool]:
    errors, unsupported = [], []
    if payload.reference_denominator != target.denominator:
        errors.append("fraction_denominator_mismatch")
    if payload.absolute_population_claimed:
        unsupported.append("relative_capture_does_not_establish_absolute_population")
    if target.scope == "limited_reference" and payload.reference_coverage != "restricted":
        unsupported.append("reference_coverage_overstated")
    metrics = {}
    for label, references, submitted in (("cell_fraction_error", target.cell_fractions, payload.cell_fractions),
                                         ("rna_fraction_error", target.rna_fractions, payload.rna_fractions)):
        refs = {(x.population_id, x.coordinate_id): x.fraction for x in references}
        values = {(x.population_id, x.coordinate_id): x.fraction for x in submitted}
        if len(values) != len(submitted):
            errors.append("duplicate_fraction")
        if not references:
            metrics[label] = None
            if submitted:
                unsupported.append("fraction_without_reference")
            continue
        if target.fraction_label_invariant:
            if len({x.coordinate_id for x in references}) != 1 or len({x.coordinate_id for x in submitted}) > 1:
                errors.append("fraction_coordinate_mismatch")
            if {x.coordinate_id for x in references} != {x.coordinate_id for x in submitted}:
                errors.append("fraction_coordinate_mismatch")
            if len(values) != len(refs):
                errors.append("fraction_population_count_mismatch")
            diffs = tuple(abs(a - b) for a, b in zip(sorted(refs.values()), sorted(values.values()))) if len(values) == len(refs) else ()
        else:
            if set(values) != set(refs):
                errors.append("fraction_population_or_coordinate_mismatch")
            diffs = tuple(abs(values[k] - refs[k]) for k in refs if k in values)
        totals: dict[str, float] = {}
        for row in submitted:
            totals[row.coordinate_id] = totals.get(row.coordinate_id, 0.0) + row.fraction
        if any(abs(value - 1.0) > thresholds.numeric_tolerance for value in totals.values()):
            errors.append("fractions_must_sum_to_one_within_coordinate")
        metrics[label] = _mean(diffs)
    success = not errors and not unsupported
    for key in ("cell_fraction_error", "rna_fraction_error"):
        if key == "rna_fraction_error" and not target.rna_fractions:
            continue
        success = success and metrics[key] is not None and metrics[key] <= thresholds.maximum_fraction_mae
    return metrics, errors, unsupported, bool(success)


def _coordinates(reference: tuple[CellCoordinate, ...], predictions: tuple[CellCoordinate, ...]) -> tuple[dict, dict, list[str]]:
    expected = {x.cell_id: x for x in reference}
    submitted = {x.cell_id: x for x in predictions}
    errors = []
    if len(submitted) != len(predictions) or set(submitted) != set(expected):
        errors.append("cell_coordinate_accounting_mismatch")
    return expected, submitted, errors


def _projection_errors(target: TargetReference, payload: GraphPayload | OrderingPayload) -> list[str]:
    if target.shared_process_projection is None:
        return ["shared_process_projection_reference_missing"]
    if payload.shared_process_projection != target.shared_process_projection:
        return ["shared_process_projection_mismatch"]
    if payload.retained_feature_universe != target.shared_process_projection.retained_feature_universe:
        return ["retained_feature_universe_mismatch"]
    if payload.excluded_feature_ids != target.shared_process_projection.excluded_feature_ids:
        return ["excluded_feature_universe_mismatch"]
    return []


def _graph_metrics(target: TargetReference, payload: GraphPayload, thresholds: BiologyScoringThresholds) -> tuple[dict, list[str], list[str], bool]:
    projection_errors = _projection_errors(target, payload)
    if projection_errors:
        return {}, projection_errors, [], False
    refs, values, errors = _coordinates(target.coordinates, payload.within_branch_order)
    unsupported = []
    if payload.orientation_uncertainty == "anchored" and not target.direction_identifiable:
        unsupported.append("expression_path_direction_unanchored")
    metrics = graph_scores(tuple((x.source, x.target) for x in target.graph),
                           tuple((x.source, x.target) for x in payload.state_graph), directed=target.direction_identifiable, allow_result_analysis=True)
    keys = tuple(k for k in refs if k in values)
    if any(values[k].group is None or (refs[k].order is not None and values[k].order is None) for k in keys):
        errors.append("state_group_or_order_missing")
    else:
        metrics.update(partition_scores(tuple(refs[k].group for k in keys), tuple(values[k].group for k in keys), allow_result_analysis=True))
        order_scores = []
        for branch in sorted({refs[k].group for k in keys}):
            branch_keys = tuple(k for k in keys if refs[k].group == branch and refs[k].order is not None)
            if len(branch_keys) < 2:
                continue
            agreement = rank_agreement(tuple(refs[k].order for k in branch_keys),
                                       tuple(values[k].order for k in branch_keys), orientation_invariant=not target.direction_identifiable, allow_result_analysis=True)
            if agreement is not None:
                order_scores.append(agreement)
        metrics["within_branch_order"] = _mean(order_scores)
    success = not errors and not unsupported and metrics["topology_score"] is not None and metrics["topology_score"] >= thresholds.minimum_topology_score
    success = success and metrics.get("partition_f1") is not None and metrics["partition_f1"] >= thresholds.minimum_partition_f1
    success = success and metrics.get("topology_recall") is not None and metrics["topology_recall"] >= thresholds.minimum_topology_recall
    success = success and metrics.get("label_macro_f1") is not None and metrics["label_macro_f1"] >= thresholds.minimum_branch_macro_f1
    success = success and metrics.get("within_branch_order") is not None and metrics["within_branch_order"] >= thresholds.minimum_rank_agreement
    return metrics, errors, unsupported, bool(success)


def _ordering_metrics(target: TargetReference, payload: OrderingPayload, thresholds: BiologyScoringThresholds) -> tuple[dict, list[str], list[str], bool | None]:
    projection_errors = _projection_errors(target, payload)
    if projection_errors:
        return {}, projection_errors, [], False
    refs, submitted, errors = _coordinates(target.coordinates, payload.coordinates)
    unsupported = []
    if payload.orientation_uncertainty == "anchored" and not target.direction_identifiable:
        unsupported.append("unanchored_order_claimed_as_directed_history")
    keys = tuple(k for k in refs if k in submitted)
    if target.target_id == "maturation_order":
        if any(submitted[k].order is None for k in keys):
            errors.append("maturation_coordinate_missing")
            agreement = None
        else:
            agreement = rank_agreement(tuple(refs[k].order for k in keys), tuple(submitted[k].order for k in keys), orientation_invariant=True, allow_result_analysis=True)
        return {"maturation_order_agreement": agreement, "maturation_order_error": 1 - agreement if agreement is not None else None}, errors, unsupported, (not errors and not unsupported and agreement >= thresholds.minimum_rank_agreement) if agreement is not None else None
    if target.full_cycle_covered is False:
        if payload.cycle_target_scope != "observed_arc":
            unsupported.append("full_cycle_not_covered")
        # A narrow arc supports only a local ordering, invariant to rotation,
        # reflection and monotone reparameterization. Full phase/period error
        # remains unavailable; arbitrary angular magnitudes are not rewarded.
        if any(submitted[k].phase_radians is None for k in keys):
            errors.append("observed_arc_coordinate_missing")
            agreement = None
        else:
            def unwrap(phases: tuple[float, ...]) -> tuple[float, ...]:
                normalized = tuple(value % math.tau for value in phases)
                center = math.atan2(math.fsum(math.sin(x) for x in normalized), math.fsum(math.cos(x) for x in normalized))
                return tuple((x - center + math.pi) % math.tau - math.pi for x in normalized)
            agreement = rank_agreement(unwrap(tuple(refs[k].phase_radians for k in keys)),
                                       unwrap(tuple(submitted[k].phase_radians for k in keys)),
                                       orientation_invariant=True, allow_result_analysis=True)
        success = not errors and not unsupported and agreement is not None and agreement >= thresholds.minimum_rank_agreement
        return {"circular_phase_error": None, "observed_arc_order_agreement": agreement}, errors, unsupported, bool(success)
    if any(submitted[k].phase_radians is None for k in keys):
        return {"circular_phase_error": None}, errors + ["cycle_phase_missing"], unsupported, False
    best = None
    for sign in ((-1, 1) if target.reflection_invariant else (1,)):
        deltas = tuple((refs[k].phase_radians % math.tau - sign * (submitted[k].phase_radians % math.tau)) % math.tau for k in keys)
        rotation = math.atan2(math.fsum(math.sin(x) for x in deltas), math.fsum(math.cos(x) for x in deltas)) if target.rotation_invariant else 0.0
        value = _mean(tuple(circular_distance(refs[k].phase_radians, sign * (submitted[k].phase_radians % math.tau) + rotation, allow_result_analysis=True) for k in keys))
        if value is not None:
            best = min(best, value) if best is not None else value
    success = not errors and not unsupported and best is not None and best <= thresholds.maximum_circular_phase_error
    return {"circular_phase_error": best}, errors, unsupported, bool(success)


def _association_metrics(target: TargetReference, payload: AssociationPayload, thresholds: BiologyScoringThresholds) -> tuple[dict, list[str], list[str], bool | None]:
    loadings = {row.gene_id: row.values for row in target.loadings}
    errors, unsupported, differences = [], [], []
    if payload.claims_direct_causality:
        unsupported.append("association_not_direct_causality")
    candidate_set = set(target.candidates)
    if target.target_id == "association_activity" and set(payload.candidate_universe) != candidate_set:
        errors.append("candidate_universe_mismatch")
    if len(payload.candidate_regulators) != len(set(payload.candidate_regulators)) or set(payload.candidate_regulators) - candidate_set:
        errors.append("candidate_ranking_invalid")
    if target.target_id == "association_activity" and payload.covariance_scale != "log_expression_signal_correlation":
        errors.append("latent_activity_scale_not_identified")
    multiplier = 1.0
    if target.covariance_multiplier_by_condition:
        multipliers = dict(target.covariance_multiplier_by_condition)
        if payload.condition not in multipliers:
            errors.append("covariance_condition_reference_missing")
        else:
            multiplier = multipliers[payload.condition]
    seen = set()
    expected_pairs = {(x.source, x.target) for x in target.association_pairs}
    pair_true = pair_detected = pair_false = pair_positive = 0
    true_pair_keys = set()
    for edge in payload.association_edges:
        key = tuple(sorted((edge.source, edge.target)))
        if edge.source == edge.target or key in seen or edge.source not in loadings or edge.target not in loadings:
            errors.append("association_edge_invalid")
            continue
        seen.add(key)
        if expected_pairs and key not in expected_pairs:
            errors.append("association_pair_outside_declared_universe")
        a, b = loadings[edge.source], loadings[edge.target]
        reference = math.fsum(x * y for x, y in zip(a, b))
        if payload.covariance_scale == "log_expression_signal_correlation":
            denominator = math.sqrt(math.fsum(x * x for x in a) * math.fsum(y * y for y in b))
            if not denominator:
                # No latent signal variance: signal correlation is undefined,
                # not a verified zero association or negative causal edge.
                unsupported.append("correlation_with_zero_signal_variance")
                continue
            reference /= denominator
        else:
            reference *= multiplier
        difference = _difference(edge.covariance, reference)
        if difference is None:
            errors.append("arithmetic_overflow")
        else:
            differences.append(difference)
        if expected_pairs:
            positive = abs(reference) > thresholds.numeric_tolerance
            if positive:
                true_pair_keys.add(key)
            selected = abs(edge.covariance) >= thresholds.minimum_association_magnitude
            pair_true += positive
            pair_positive += selected
            pair_detected += positive and selected
            pair_false += not positive and selected
    active = {gene for gene in target.candidates if gene in loadings and any(abs(v) > thresholds.numeric_tolerance for v in loadings[gene])}
    ranked = payload.candidate_regulators
    hits = len(set(ranked[:len(active)]) & active)
    # No precision/recall on unknown causal-edge negatives. This ranking has a
    # finite declared candidate universe and a simulation signal-association role.
    metrics = {"residual_correlation_error": _rmse(differences), "regulator_rank_precision": _ratio(hits, len(ranked[:len(active)])),
               "regulator_rank_recall": _ratio(hits, len(active)), "activity_correlation": None,
               "module_recovery": None, "association_pair_count": float(len(seen))}
    if target.target_id == "residual_modules":
        if seen != expected_pairs or not expected_pairs:
            errors.append("association_pair_universe_incomplete")
        metrics.update(association_support_precision=_ratio(pair_detected, pair_positive),
                       association_support_recall=_ratio(pair_detected, pair_true),
                       association_support_fdp=pair_false / max(1, pair_positive),
                       module_recovery=2 * pair_detected / (pair_true + pair_positive) if pair_true + pair_positive else 1.0)
        success = not errors and not unsupported and bool(expected_pairs) and bool(differences)
        success = success and metrics["residual_correlation_error"] <= thresholds.maximum_covariance_rmse
        success = success and metrics["module_recovery"] >= thresholds.minimum_partition_f1
        if payload.module_membership:
            modules: dict[str, set[str]] = {}
            assigned = set()
            for row in payload.module_membership:
                if row.gene_id not in loadings or (row.gene_id, row.module_id) in assigned:
                    errors.append("module_membership_invalid")
                    continue
                assigned.add((row.gene_id, row.module_id))
                modules.setdefault(row.gene_id, set())
                if row.module_id is not None:
                    modules[row.gene_id].add(row.module_id)
            if set(modules) != set(loadings):
                errors.append("module_feature_accounting_incomplete")
            module_pairs = {pair for pair in expected_pairs if modules.get(pair[0], set()) & modules.get(pair[1], set())}
            overlap = len(module_pairs & true_pair_keys)
            module_f1 = 2 * overlap / (len(module_pairs) + len(true_pair_keys)) if module_pairs or true_pair_keys else 1.0
            # This compares the association support induced by modules, not
            # arbitrary names or an unidentifiable latent loading-column basis.
            metrics["module_covariance_support_f1"] = module_f1
            success = success and module_f1 >= thresholds.minimum_partition_f1 and not errors
    else:
        if active and not payload.association_edges:
            errors.append("association_evidence_missing")
        success = not errors and not unsupported and bool(active) and set(ranked) == candidate_set
        success = success and metrics["regulator_rank_recall"] >= thresholds.minimum_signed_recall
        if differences:
            success = success and metrics["residual_correlation_error"] <= thresholds.maximum_covariance_rmse
        if payload.module_membership:
            unsupported.append("absolute_latent_module_basis_not_identified")
            success = False
    return metrics, errors, unsupported, success


def _hypergeometric_tail(population: int, members: int, selected: int, overlap: int) -> float:
    denominator = math.comb(population, selected)
    if denominator == 0:
        raise ValueError("invalid enrichment universe")
    return min(1.0, math.fsum(math.comb(members, k) * math.comb(population - members, selected - k) / denominator
                             for k in range(overlap, min(members, selected) + 1) if 0 <= selected - k <= population - members))


def _enrichment_metrics(target: TargetReference, payload: EnrichmentPayload, gene_ids: tuple[str, ...], thresholds: BiologyScoringThresholds) -> tuple[dict, list[str], list[str], bool | None]:
    errors, unsupported = [], []
    universe = set(gene_ids)
    if len(payload.feature_universe) != len(set(payload.feature_universe)) or set(payload.feature_universe) != universe:
        errors.append("enrichment_feature_universe_mismatch")
    if payload.annotation_provenance != target.annotation_id:
        errors.append("annotation_provenance_mismatch")
    if payload.unknown_edges_are_negatives:
        unsupported.append("unknown_edges_are_not_verified_negatives")
    if payload.binding_inferred:
        unsupported.append("motif_or_membership_does_not_establish_binding")
    refs = {x.regulator_id: set(x.gene_ids) & universe for x in target.memberships}
    estimates = {x.regulator_id: x for x in payload.enrichment_results}
    if len(estimates) != len(payload.enrichment_results) or set(estimates) != set(refs):
        errors.append("enrichment_test_family_incomplete")
    expected_p = {}
    correct_overlaps = 0
    for key, row in estimates.items():
        if key not in refs or set(row.selected_gene_ids) - universe:
            errors.append("enrichment_foreign_feature_or_regulator")
            continue
        if target.enrichment_query_gene_ids is not None and set(row.selected_gene_ids) != set(target.enrichment_query_gene_ids):
            errors.append("enrichment_query_set_mismatch")
        overlap = refs[key] & set(row.selected_gene_ids)
        if overlap != set(row.overlap_gene_ids):
            errors.append("membership_overlap_mismatch")
        else:
            correct_overlaps += 1
        expected_p[key] = _hypergeometric_tail(len(universe), len(refs[key]), len(row.selected_gene_ids), len(overlap))
        a = len(overlap)
        b, c = len(row.selected_gene_ids) - a, len(refs[key]) - a
        d = len(universe) - a - b - c
        status = "finite" if b*c else "infinite" if a*d else "undefined"
        expected_odds = a*d/(b*c) if b*c else None
        if row.odds_ratio_status != status or (expected_odds is not None and (
                row.odds_ratio is None or abs(row.odds_ratio-expected_odds) > thresholds.numeric_tolerance * max(1., abs(expected_odds)))):
            errors.append("enrichment_odds_arithmetic_mismatch")
    expected_q = _bh_minimum(expected_p)
    arithmetic = bool(expected_p) and all(abs(estimates[k].p_value - p) <= thresholds.numeric_tolerance
        and abs(estimates[k].q_value - expected_q[k]) <= thresholds.numeric_tolerance for k, p in expected_p.items())
    if not arithmetic:
        errors.append("enrichment_probability_arithmetic_mismatch")
    # Arithmetic agreement is not empirical null calibration or FDR control.
    metrics = {"enrichment_arithmetic_valid": arithmetic, "enrichment_calibration": None,
               "motif_enrichment_calibration": None, "annotation_overlap_accuracy": _ratio(correct_overlaps, len(refs))}
    success = not errors and not unsupported
    return metrics, errors, unsupported, (None if success and target.enrichment_query_gene_ids is None else success)


def _diagnostic_metrics(target: TargetReference, payload: DiagnosticsPayload) -> tuple[dict, list[str], bool | None]:
    submitted = {x.code: x.value for x in payload.diagnostics}
    errors = []
    if len(submitted) != len(payload.diagnostics):
        errors.append("duplicate_diagnostic")
    for code in ("biological_unit_count", "biological_unit", "individual_artifact_identification",
                 "measurement_type", "analysis_route", "protocol", "analysis_layer"):
        value = getattr(payload, code)
        if value is not None:
            if code in submitted and submitted[code] != value:
                errors.append("contradictory_diagnostic")
            submitted[code] = value
    checks = [float(row.code in submitted and submitted[row.code] == row.value) for row in target.diagnostic_values]
    correct = _mean(checks)
    metrics = {"diagnostic_accuracy": correct}
    if target.target_id == "unit_accounting":
        metrics["effective_unit_accounting"] = correct
        metrics["replication_diagnostic_accuracy"] = correct
    elif target.target_id == "measurement_route":
        metrics["wrong_family_rate"] = 1 - correct if correct is not None else None
        if payload.model_family is None:
            errors.append("model_family_missing")
        count_families = {"negative_binomial", "poisson", "poisson_lognormal", "quasi_likelihood_count"}
        continuous_families = {"gaussian", "gaussian_spline", "robust_gaussian", "continuous_spline"}
        families = continuous_families if target.measurement == "continuous" else count_families
        if payload.model_family not in families:
            errors.append("model_family_incompatible_with_measurement_route")
    else:
        # The existing generator supplies known simulated artifacts, but no
        # target-local support mask justifying individual artifact labels.
        # Assay/unit correctness and conservative restraint are adjudicable;
        # individual artifact calibration is a separate unavailable component.
        if submitted.get("individual_artifact_identification") != "not_identified":
            errors.append("individual_artifact_identity_claim_not_supported")
        if any(row.decision == "exclude" for row in payload.qc_decisions):
            errors.append("individual_qc_exclusion_requires_adjudicable_reference")
        metrics.update(zero_frequency_calibration=None, contamination_precision_recall=None, viable_rare_cell_retention=None)
        return metrics, errors, not errors and correct == 1.0
    return metrics, errors, (not errors and correct == 1.0) if correct is not None else None


_ALIASES: dict[str, dict[str, str]] = {
    "condition_main": {"effect_error": "effect_rmse"},
    "time_interaction": {"interaction_recall": "recall", "interaction_fdp": "realized_fdp", "difference_in_differences_error": "effect_rmse"},
    "dose_curve": {"dose_curve_error": "curve_error", "effect_interval_coverage": "interval_coverage"},
    "variability": {"variability_recall": "recall"},
    "adjusted_effect": {"effect_error": "effect_rmse"},
    "observed_difference": {"descriptive_effect_error": "effect_rmse"},
    "covariate_effect": {"effect_error": "effect_rmse"},
    "covariate_interaction": {"interaction_recall": "recall", "false_interaction_rate": "null_type_i_error"},
    "relative_expression": {"relative_effect_error": "effect_rmse", "null_gene_fdp": "realized_fdp"},
    "absolute_rna": {"absolute_scale_error": "effect_rmse"},
    "tissue_effect": {"tissue_effect_recall": "recall", "tissue_effect_error": "effect_rmse"},
    "clean_population_effect": {"correction_bias": "effect_bias", "target_retention": "recall"},
    "cell_state_effect": {"subtype_recall": "recall", "subtype_fdp": "realized_fdp"},
    "captured_abundance": {"relative_abundance_error": "cell_fraction_error"},
    "population_abundance": {"population_fraction_error": "cell_fraction_error"},
    "state_topology": {"branch_macro_f1": "label_macro_f1"},
    "assignment_effect": {"assignment_effect_error": "effect_rmse"},
    "successful_intervention": {"intervention_effect_error": "effect_rmse"},
    "epistasis": {"epistasis_error": "effect_rmse", "interaction_fdp": "realized_fdp"},
}


def _target_grade(target: TargetReference, answer: TargetAnswer | None, reference: BiologyReference,
                  thresholds: BiologyScoringThresholds) -> tuple[TargetGrade, tuple[SafeEstimateRow, ...]]:
    missing = {name: "no_compatible_reference_or_estimates" for name in target.metric_ids}
    metrics: dict[str, float | bool | None] = {name: None for name in target.metric_ids}
    if answer is None:
        return TargetGrade(target_id=target.target_id, outcome="missing_answer", scientific_success=False,
            metrics=metrics, metric_missingness={name: "target_answer_missing" for name in metrics}), ()
    errors = []
    forbidden = set(target.forbidden_claims) | {"unsupported_direct_causality", "unsupported_clinical_validation",
        "unsupported_rna_velocity", "structural_zeros_identified_from_observed_zeros", "absence_or_equivalence_from_nonsignificance",
        "shared_process_quotient_is_full_expression_geometry"}
    unsupported = list(set(answer.asserted_claims) & forbidden)
    for field in ("estimand", "units", "contrast", "reference_population", "prediction_reference"):
        if getattr(answer, field) != getattr(target, field):
            errors.append(f"target_{field}_mismatch")
    if answer.claim_type == "failed":
        return TargetGrade(target_id=target.target_id, outcome="analysis_failed", scientific_success=False,
            metrics=metrics, metric_missingness={name: "analysis_failed" for name in metrics},
            unsupported_claims=tuple(sorted(set(unsupported))), errors=tuple(errors)), ()
    if _requires_target_restraint(target):
        if answer.claim_type != "refusal" or answer.evidence_scope != "not_identified":
            unsupported.append("target_not_identifiable")
        required = set(target.required_refusal_criteria or _refusal_criteria(target.target_id, {}, {}, {}))
        if not any(check.code in required and check.status in {"violated", "unknown"}
                   for check in answer.support_checks):
            errors.append("refusal_diagnostic_evidence_missing_or_wrong_reason")
        correct = not errors and not unsupported and bool(answer.limitation_codes)
        metrics["correct_refusal_rate"] = float(correct)
        metrics["unsupported_claim_count"] = float(len(set(unsupported)))
        for name in target.metric_ids:
            if name.startswith("unsupported_") or name == "false_scale_claim_rate":
                metrics[name] = float(not correct)
        missing = {key: "not_identifiable_no_numerical_score" for key, value in metrics.items() if value is None}
        return TargetGrade(target_id=target.target_id, outcome="correct_refusal" if correct else "invalid_answer",
            scientific_success=correct, metrics=metrics, metric_missingness=missing,
            unsupported_claims=tuple(sorted(set(unsupported))), errors=tuple(errors)), ()
    if answer.claim_type == "refusal":
        # Some restricted targets support diagnostics alone. Refusal is honest
        # but not automatically success when no adjudicated diagnostic exists.
        unavailable = not target.numerical_reference_available
        metrics["unnecessary_refusal_rate"] = None if unavailable else 1.0
        return TargetGrade(target_id=target.target_id, outcome="unsupported_reference" if unavailable else "scored",
            scientific_success=None if unavailable and not errors and not unsupported else False,
            metrics=metrics, metric_missingness={key: target.unsupported_reference_reason or "answer_refused_supported_target" for key, value in metrics.items() if value is None},
            unsupported_claims=tuple(sorted(set(unsupported))), errors=tuple(errors)), ()
    if answer.evidence_scope != target.evidence_scope:
        unsupported.append("target_evidence_scope_mismatch")
    if target.scope == "limited_reference" and (answer.claim_type != "restricted_estimate" or not answer.limitation_codes):
        unsupported.append("restricted_reference_claimed_as_full_support")
    if not target.numerical_reference_available:
        unsupported.append("numerical_claim_without_identified_reference")
        return TargetGrade(target_id=target.target_id, outcome="unsupported_reference", scientific_success=False,
            metrics=metrics, metric_missingness={key: target.unsupported_reference_reason or "no_identified_numerical_reference" for key in metrics},
            unsupported_claims=tuple(sorted(set(unsupported))), errors=tuple(errors)), ()
    payload = answer.payload
    safe_rows: tuple[SafeEstimateRow, ...] = ()
    values: dict = {}
    success: bool | None = False
    try:
        if isinstance(payload, EffectsPayload):
            values, found_errors, found_unsupported, success = _effect_metrics(target, payload, thresholds)
            errors.extend(found_errors)
            unsupported.extend(found_unsupported)
            safe_rows = _safe_rows(target, payload)
        elif isinstance(payload, EventsPayload):
            values, found_errors, success = _event_metrics(target, payload, thresholds)
            errors.extend(found_errors)
        elif isinstance(payload, RhythmPayload):
            values, found_errors, found_unsupported, success = _rhythm_metrics(target, payload, thresholds)
            errors.extend(found_errors)
            unsupported.extend(found_unsupported)
        elif isinstance(payload, ConcentrationPayload):
            values, found_errors, found_unsupported, success = _concentration_metrics(target, payload, thresholds)
            errors.extend(found_errors)
            unsupported.extend(found_unsupported)
        elif isinstance(payload, FractionsPayload):
            values, found_errors, found_unsupported, success = _fraction_metrics(target, payload, thresholds)
            errors.extend(found_errors)
            unsupported.extend(found_unsupported)
        elif isinstance(payload, GraphPayload):
            values, found_errors, found_unsupported, success = _graph_metrics(target, payload, thresholds)
            errors.extend(found_errors)
            unsupported.extend(found_unsupported)
        elif isinstance(payload, AncestryPayload):
            refs, submitted, found_errors = _coordinates(target.coordinates, payload.clone_equivalence)
            errors.extend(found_errors)
            if payload.direction_identified:
                unsupported.append("clone_equivalence_does_not_orient_expression_history")
            if payload.ancestry_status != "equivalence_only" or not payload.history_evidence:
                errors.append("independent_history_equivalence_required")
            keys = tuple(k for k in refs if k in submitted)
            if any(submitted[k].group is None for k in keys):
                errors.append("clone_equivalence_label_missing")
            else:
                values.update(partition_scores(tuple(refs[k].group for k in keys), tuple(submitted[k].group for k in keys), allow_result_analysis=True))
            score = values.get("partition_f1")
            values["ancestry_assignment_error"] = 1 - score if score is not None else None
            success = score is not None and score >= thresholds.minimum_partition_f1
        elif isinstance(payload, OrderingPayload):
            values, found_errors, found_unsupported, success = _ordering_metrics(target, payload, thresholds)
            errors.extend(found_errors)
            unsupported.extend(found_unsupported)
        elif isinstance(payload, AssociationPayload):
            values, found_errors, found_unsupported, success = _association_metrics(target, payload, thresholds)
            errors.extend(found_errors)
            unsupported.extend(found_unsupported)
        elif isinstance(payload, EnrichmentPayload):
            values, found_errors, found_unsupported, success = _enrichment_metrics(target, payload, reference.gene_ids, thresholds)
            errors.extend(found_errors)
            unsupported.extend(found_unsupported)
        elif isinstance(payload, DiagnosticsPayload):
            if target.target_id == "measurement_route" and not payload.residual_diagnostics:
                errors.append("residual_diagnostics_status_missing")
            values, found_errors, success = _diagnostic_metrics(target, payload)
            errors.extend(found_errors)
        elif isinstance(payload, CellStatePayload):
            refs, submitted, found_errors = _coordinates(target.coordinates, payload.cell_type_labels)
            errors.extend(found_errors)
            keys = tuple(k for k in refs if k in submitted and refs[k].group is not None)
            # Eligibility selects the adjudicable subset. It must not become a
            # hidden demand to identify exactly which cells are multiplets.
            if any(submitted[k].group is None for k in keys):
                errors.append("cell_state_label_missing")
            else:
                values.update(partition_scores(tuple(refs[k].group for k in keys), tuple(submitted[k].group for k in keys), allow_result_analysis=True))
            score = values.get("partition_f1")
            values["cell_state_partition_error"] = 1 - score if score is not None else None
            # Label-invariant state recovery does not identify individual
            # technical artifacts or establish named biological cell identities.
            success = score is not None and score >= thresholds.minimum_partition_f1
            decisions = {row.cell_id: row.decision for row in payload.qc_decisions}
            if len(decisions) != len(payload.qc_decisions) or set(decisions) - set(refs):
                errors.append("qc_cell_accounting_mismatch")
            retained = _mean(tuple(float(decisions.get(k, "retain") != "exclude") for k in keys))
            values["target_retention"] = retained
            success = success and retained is not None and retained >= thresholds.minimum_estimate_availability
        else:
            errors.append("target_payload_not_implemented")
    except (ValueError, TypeError, KeyError, IndexError, OverflowError, ZeroDivisionError):
        # Invalid estimates are failures, never numerical zeros. Do not echo
        # reference values or private per-gene support via exception messages.
        errors.append("target_scoring_input_or_arithmetic_invalid")
        success = False
    metrics.update(values)
    for destination, source in _ALIASES.get(target.target_id, {}).items():
        metrics[destination] = metrics.get(source)
    for key, value in tuple(metrics.items()):
        if value is not None and not math.isfinite(value):
            metrics[key] = None
            errors.append("arithmetic_overflow")
    metrics["unsupported_claim_count"] = float(len(set(unsupported)))
    if errors or unsupported:
        success = False
    missing = {key: ("not_defined_for_reference_or_submitted_estimates" if key in values else "metric_reference_or_implementation_unavailable")
               for key, value in metrics.items() if value is None}
    if success is None:
        missing["scientific_success"] = "target_qualification_reference_incomplete"
    return TargetGrade(target_id=target.target_id, outcome="invalid_answer" if errors or unsupported else ("unsupported_reference" if success is None else "scored"),
        scientific_success=success, metrics=metrics, metric_missingness=missing,
        unsupported_claims=tuple(sorted(set(unsupported))), errors=tuple(sorted(set(errors)))), safe_rows


def grade_biology_answer(answer: BiologyAnswer | Mapping[str, object] | None, *, reference: BiologyReference,
                         allow_result_analysis: bool = False,
                         thresholds: BiologyScoringThresholds | None = None) -> BiologyGrade:
    """Score one attempted answer identically in every agent condition.

    Result analysis must be explicitly authorized before even inspecting inputs.
    A missing or invalid answer fails all scorable requested targets. Reference
    availability is fixed before parsing the answer. ``scientific_success``
    retains the complete-contract rule; ``available_target_scientific_success``
    requires correctness on every independently scorable target and is None
    when there are none. Neither field means empirical calibration.
    """
    _authorized(allow_result_analysis)
    thresholds = thresholds or BiologyScoringThresholds()
    digest = content_sha256(thresholds.model_dump(mode="json"))
    if not isinstance(reference, BiologyReference):
        raise ValueError("a validated BiologyReference is required")
    reference_status = {target.target_id: _reference_status(target) for target in reference.targets}
    errors = []
    if reference.thresholds_sha256 != digest:
        errors.append("threshold_configuration_mismatch")
    parsed = None
    if answer is not None:
        try:
            parsed = answer if isinstance(answer, BiologyAnswer) else BiologyAnswer.model_validate_json(canonical_json_bytes(answer))
        except (ValidationError, ValueError, TypeError):
            errors.append("answer_schema_invalid")
    if parsed is not None:
        for field in ("task_id", "input_sha256", "gene_universe_sha256"):
            if getattr(parsed, field) != getattr(reference, field):
                errors.append("task_binding_mismatch" if field == "task_id" else f"{field}_mismatch")
        expected = {x.target_id for x in reference.targets}
        if {x.target_id for x in parsed.target_results} - expected:
            errors.append("foreign_target")
    submissions = {x.target_id: x for x in parsed.target_results} if parsed is not None and not errors else {}
    grades, rows = [], []
    for target in reference.targets:
        grade, safe = _target_grade(target, submissions.get(target.target_id), reference, thresholds)
        status = reference_status[target.target_id]
        grade = grade.model_copy(update={"reference_availability": status.availability, "reference_scope": status.scope,
                                         "scorable_components": status.scorable_components})
        grades.append(grade)
        rows.extend(safe)
    # T05 asks two distinct questions. A variance discovery is never counted as
    # a mean false positive: this metric uses only the paired mean-test family.
    by_target = {x.target_id: x for x in reference.targets}
    if "variability" in by_target and "mean_contrast" in by_target and "mean_contrast" in submissions:
        mean_payload = submissions["mean_contrast"].payload
        mean_grade = next(g for g in grades if g.target_id == "mean_contrast")
        if isinstance(mean_payload, EffectsPayload) and not mean_grade.errors:
            nulls = {x.hypothesis_id for x in by_target["mean_contrast"].hypotheses if x.testable and x.nonnull is False}
            tests = {x.hypothesis_id: x.q_value for x in mean_payload.gene_statistics if x.q_value is not None and x.hypothesis_id in nulls}
            for i, grade in enumerate(grades):
                if grade.target_id == "variability":
                    values = dict(grade.metrics)
                    values["mean_false_positive_rate"] = _mean(tuple(float(q <= thresholds.alpha) for q in tests.values()))
                    values["mean_null_test_coverage"] = _ratio(len(tests), len(nulls))
                    missing = dict(grade.metric_missingness)
                    for key in ("mean_false_positive_rate", "mean_null_test_coverage"):
                        if values[key] is None:
                            missing[key] = "no_completed_test_with_known_mean_null"
                        else:
                            missing.pop(key, None)
                    grades[i] = grade.model_copy(update={"metrics": values, "metric_missingness": missing})
    all_references_scorable = bool(grades) and all(status.availability == "scorable" for status in reference_status.values())
    if not all_references_scorable:
        success = None
    elif errors or any(g.scientific_success is not True for g in grades):
        success = False
    else:
        success = True
    scorable_grades = tuple(g for g in grades if reference_status[g.target_id].availability == "scorable")
    available_success = (all(g.scientific_success is True for g in scorable_grades) if scorable_grades else None)
    if errors and scorable_grades:
        available_success = False
    missing = {f"{g.target_id}.{name}": reason for g in grades for name, reason in g.metric_missingness.items()}
    if success is None:
        missing["scientific_success"] = "no_targets" if not grades else "one_or_more_target_references_unavailable"
    if available_success is None:
        missing["available_target_scientific_success"] = "no_scorable_target_references"
    return BiologyGrade(task_id=reference.task_id, input_sha256=reference.input_sha256,
                        answer_sha256=content_sha256(parsed.model_dump(mode="json")) if parsed is not None else None,
                        gene_universe_sha256=reference.gene_universe_sha256,
                        per_target_results=tuple(grades), scientific_success=success, available_target_scientific_success=available_success,
                        target_reference_availability={key: status.availability for key, status in reference_status.items()},
                        target_reference_missingness={key: status.unavailable_reason for key, status in reference_status.items() if status.unavailable_reason is not None},
                        metric_missingness=missing, safe_rows=tuple(rows), errors=tuple(errors), thresholds_sha256=digest)
