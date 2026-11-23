"""Typed result evidence for claim review, without execution or scientific authority.

The full bounded artifact is checked before a smaller reviewer projection is
made. Supplied method/cohort declarations are bindings, not independently
established biological facts. No estimator, threshold selection or repair runs.
"""

import hashlib
import json
import math
import re
from decimal import ROUND_HALF_EVEN, ROUND_HALF_UP, Decimal, localcontext
from typing import Annotated, Literal

from pydantic import Field, model_validator

from devbench.study.contracts import FrozenContract, Identifier, Sha256, content_sha256

from .scientific_judgment import ClaimJudgment, ClaimPacket, Text

# Only declared /2 typed results and strictly referenced companions get this budget.
# Generic diagnostics/sources/plots retain the legacy 128,000-byte bound.
MAX_RESULT_BYTES = 16_000_000
MAX_PACKET_EVIDENCE_BYTES = 32_000_000
MAX_RESULT_FEATURES = 100_000
MAX_REVIEW_CONTEXT_BYTES = 128_000
PREVIEW_ROWS = 32
Finite = Annotated[float, Field(allow_inf_nan=False)]
Probability = Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]
Ids = Annotated[tuple[Identifier, ...], Field(max_length=MAX_RESULT_FEATURES)]
METRICS = ("effect", "p_value", "adjusted_p_value", "standard_error")


class ClaimSpec(FrozenContract):
    "A substantive claim's exact scientific use; declarations are not authentication."

    claim_id: Identifier
    revision: Annotated[int, Field(strict=True, ge=1)]
    question: Text
    population: Text
    biological_unit: Text
    estimand: Text
    scale: Text
    contrast: Text
    direction: Literal["positive", "negative", "two_sided", "not_applicable"]
    claim_class: Literal[
        "checked_observation",
        "qualified_procedure_result",
        "exploratory_finding",
        "biological_interpretation",
    ]
    effect_fields: Annotated[tuple[Text, ...], Field(max_length=32)]
    uncertainty_fields: Annotated[tuple[Text, ...], Field(max_length=32)]
    family_id: Identifier | None
    exposure: Literal["structural", "diagnostic", "target_outcome", "unknown"]
    evidence_ids: Annotated[tuple[Identifier, ...], Field(min_length=1, max_length=128)]
    required_checks: Annotated[
        tuple[Identifier, ...], Field(min_length=1, max_length=64)
    ]
    review_status: Literal["pending", "current", "held", "withdrawn"] = "pending"


class ClaimValidationPlan(FrozenContract):
    """Scientific obligations, not proof that a check or replication occurred."""

    claim_ids: Annotated[tuple[Identifier, ...], Field(min_length=1, max_length=128)]
    competing_explanations: Annotated[
        tuple[Text, ...], Field(min_length=1, max_length=32)
    ]
    checks: Annotated[tuple[Identifier, ...], Field(min_length=1, max_length=64)]
    independence_dimension: Text
    required_evidence: Annotated[
        tuple[Identifier, ...], Field(min_length=1, max_length=128)
    ]
    stopping_conditions: Annotated[tuple[Text, ...], Field(min_length=1, max_length=32)]
    limitations: Annotated[tuple[Text, ...], Field(min_length=1, max_length=32)]

    @model_validator(mode="after")
    def nonredundant_ids(self):
        for name in ("claim_ids", "checks", "required_evidence"):
            values = getattr(self, name)
            if len(set(values)) != len(values):
                raise ValueError("Validation plan requires distinct " + name)
        return self


class ReviewQueryHandle(FrozenContract):
    """Public identity only; the coordinator retains the actual read capability."""

    handle_id: Identifier
    source_id: Identifier
    context_id: Identifier
    source_sha256: Sha256
    bindings_sha256: Sha256
    policy_sha256: Sha256
    family_ids: Annotated[tuple[Identifier, ...], Field(max_length=128)]


class BiologicalContext(FrozenContract):
    organism: Text | None
    tissue: Text | None
    intervention: Text | None
    direction: Text | None
    uncertainty: Text | None


class LiteratureClaimBinding(FrozenContract):
    claim_id: Identifier
    claim_sha256: Sha256
    evidence_sha256: Sha256
    review_binding_sha256: Sha256
    obligation_id: Identifier
    context: BiologicalContext
    requires_verified_status: bool = False


class LiteratureClaimAssessment(FrozenContract):
    claim_id: Identifier
    claim_sha256: Sha256
    evidence_sha256: Sha256
    snapshot_id: Sha256
    passage_ids: Annotated[tuple[Identifier, ...], Field(min_length=1, max_length=32)]
    relation: Literal["supports", "refutes", "context", "inapplicable", "insufficient"]
    source_context: BiologicalContext
    rationale: Text
    limitations: Annotated[tuple[Text, ...], Field(min_length=1, max_length=32)]
    reviewer_id: Identifier


class LiteratureQueryHandle(FrozenContract):
    handle_id: Identifier
    source_id: Identifier
    context_id: Identifier
    policy_sha256: Sha256
    capability_sha256: Sha256
    grant_sha256: Sha256
    claims: Annotated[
        tuple[LiteratureClaimBinding, ...], Field(min_length=1, max_length=64)
    ]


def reviewed_publication_descriptor(
    lifecycle,
    binding,
    entity_id,
    profile,
    *,
    design_binding=None,
    connection=None,
    **kwargs,
):
    """Feed current scoped T17 state into T16's existing publication intersection."""
    from .scientific_publication import build_publication_descriptor
    from .scientific_review_holds import ReviewBinding, ReviewHoldLifecycle

    if (
        type(lifecycle) is not ReviewHoldLifecycle
        or "review" in kwargs
        or kwargs.get("scope") == "adaptive"
    ):
        raise TypeError(
            "Publication review eligibility must come from its coordinator lifecycle"
        )
    if connection is None:
        with lifecycle.ledger._database() as db:
            db.execute("BEGIN")
            return reviewed_publication_descriptor(
                lifecycle,
                binding,
                entity_id,
                profile,
                design_binding=design_binding,
                connection=db,
                **kwargs,
            )
    lifecycle.state(
        connection=connection
    )  # Validate the caller's same-ledger snapshot.
    binding = ReviewBinding.model_validate(binding)
    from .literature_adapter import literature_use_current

    if not literature_use_current(
        lifecycle, entity_id, binding.sha256(), connection=connection
    ):
        kwargs["claim_current"] = False
    dependencies = tuple(kwargs.pop("dependency_sha256", ())) + (binding.sha256(),)
    if kwargs.get("allocation") is not None:
        from devbench.core.publication_families import FamilyAllocation

        allocation = FamilyAllocation.model_validate(kwargs["allocation"])
        state = lifecycle.state(connection=connection)
        tables = tuple(
            identity
            for identity, item in state["entities"].items()
            if item["kind"] == "table"
        )
        blocked = lifecycle.blocking_entities(
            binding, touchpoint="publication", entity_ids=tables, connection=connection
        )
        family_ids = {family.family_id for family in allocation.families}
        kwargs["family_review_holds"] = tuple(
            sorted(
                {
                    state["entities"][identity]["family_id"]
                    for identity in blocked
                    if state["entities"][identity]["family_id"] in family_ids
                }
            )
        )
    draft = build_publication_descriptor(
        profile, dependency_sha256=dependencies, **kwargs
    )
    review = lifecycle.review_eligibility(
        binding,
        publication_basis_sha256=draft.basis_sha256,
        entity_id=entity_id,
        design_binding=design_binding,
        connection=connection,
    )
    return build_publication_descriptor(
        profile, dependency_sha256=dependencies, review=review, **kwargs
    )


class QueryReceipt(FrozenContract):
    """Authority-bound receipt for one bounded view of immutable evidence."""

    format: Literal["devbench.query-receipt/1"] = "devbench.query-receipt/1"
    source_sha256: Sha256
    source_schema: str
    registered_source_sha256: Sha256
    query_sha256: Sha256
    predicate: dict
    aggregation: str
    sort: dict
    joins: dict
    eligibility: dict
    total_matched: Annotated[int, Field(strict=True, ge=0)]
    total_returned: Annotated[int, Field(strict=True, ge=0)]
    missing_count: Annotated[int, Field(strict=True, ge=0)]
    failed_count: Annotated[int, Field(strict=True, ge=0)]
    excluded_count: Annotated[int, Field(strict=True, ge=0)]
    offset: Annotated[int, Field(strict=True, ge=0)]
    status: Literal["available", "unavailable"]
    next_cursor: Annotated[str, Field(pattern=r"^[a-f0-9]{32}$")] | None
    complete: bool
    disclosure_reservation_id: Annotated[str, Field(pattern=r"^[a-f0-9]{32}$")]
    policy_sha256: Sha256
    policy_version: Annotated[int, Field(strict=True, ge=1)]
    statistics_included: bool

    @model_validator(mode="after")
    def page_is_consistent(self):
        if (
            self.offset + self.total_returned > self.total_matched
            or self.missing_count > self.total_matched
            or self.failed_count > self.total_matched
            or self.complete
            != (self.status == "available" and self.next_cursor is None)
            or (
                self.next_cursor is not None
                and (
                    self.next_cursor != self.disclosure_reservation_id
                    or not self.total_returned
                    or self.status != "available"
                    or self.offset + self.total_returned >= self.total_matched
                )
            )
        ):
            raise ValueError("Inconsistent query receipt counts or continuation")
        return self


def erql_family_evidence(plan, request, result):
    """Project an already validated T06 result into the T07 family contract."""
    from devbench.core.publication_families import FamilyEvidence, FeatureEvidence

    rows = []
    for index, gene in enumerate(result["genes"]):
        gene_id = gene["gene_id"]
        if gene["status"] == "tested":
            values = gene["statistics"]
            rows.append(
                FeatureEvidence(
                    gene_id=gene_id,
                    status="tested",
                    p=values["p"]["value"],
                    effect=values["logFC"]["value"],
                )
            )
        else:
            status = (
                "filtered"
                if any(row[index] for row in request["counts"])
                else "filtered_all_zero"
            )
            reason = (
                gene["filter_reason"]
                if status == "filtered"
                else "all_zero_in_bound_counts"
            )
            rows.append(FeatureEvidence(gene_id=gene_id, status=status, reason=reason))
    return FamilyEvidence(
        family_id=plan.family_id,
        allocation_sha256=plan.allocation.sha256(),
        request_sha256=plan.request_sha256,
        status="observed",
        rows=tuple(rows),
        eligible_features=tuple(
            gene
            for gene, keep in zip(
                request["feature_ids"], result["filter"]["keep"], strict=True
            )
            if keep
        ),
    )


def publication_table(result, descriptor, *, retained_sha256):
    """Project the retained row inventory through a current typed publication basis."""
    from .scientific_publication import PublicationDescriptor, public_metadata

    result = ScientificResult.model_validate(result)
    descriptor = PublicationDescriptor.model_validate(descriptor)
    if descriptor.source_sha256 != retained_sha256 or descriptor.allow_statistics:
        # General retained results lack T07 allocation weights. Only the fixed
        # family exporter can publish inferential rows under this gate.
        raise PermissionError(
            "Retained diagnostic does not bind the admitted family table"
        )
    return public_metadata(
        {
            "result_id": result.result_id,
            "family_id": result.family.family_id,
            "rows": [row.model_dump(mode="json") for row in result.rows],
        },
        descriptor=descriptor,
    )


def _unique(values, name):
    if len(values) != len(set(values)):
        raise ValueError("Duplicate " + name)


class ResultCohort(FrozenContract):
    cohort_id: Identifier
    sample_ids: Annotated[tuple[Identifier, ...], Field(min_length=1, max_length=4096)]
    biological_unit_ids: Annotated[
        tuple[Identifier, ...], Field(min_length=1, max_length=4096)
    ]

    @model_validator(mode="after")
    def relationships(self):
        _unique(self.sample_ids, "sample identity")
        if len(self.sample_ids) != len(self.biological_unit_ids):
            raise ValueError(
                "Each observed sample requires its declared biological unit"
            )
        return self


class ResultContrast(FrozenContract):
    contrast_id: Identifier
    reference: Text
    comparison: Text
    operation: Literal["comparison_minus_reference"]

    @model_validator(mode="after")
    def levels(self):
        if self.reference == self.comparison:
            raise ValueError("Contrast levels must differ")
        return self


class ResultMethod(FrozenContract):
    method_id: Identifier
    version: Text
    normalization: Text
    test: Text
    effect_scale: Text
    effect_kind: Literal["unshrunken", "shrunken"]
    test_effect_source: Text | None = None

    @model_validator(mode="after")
    def shrinkage(self):
        if self.effect_kind == "shrunken" and self.test_effect_source is None:
            raise ValueError(
                "Shrunken effects must declare which estimate supplies the test"
            )
        return self


class ResultFamily(FrozenContract):
    family_id: Identifier
    eligible_ids: Ids
    complete: bool
    adjustment: Literal[
        "benjamini_hochberg",
        "benjamini_yekutieli",
        "storey_q",
        "unknown",
        "not_applicable",
    ]
    failed_hypothesis_policy: Literal[
        "retain_null_use_one_for_bh_denominator", "exclude_failed_hypotheses", "unknown"
    ]

    @model_validator(mode="after")
    def identities(self):
        _unique(self.eligible_ids, "family feature")
        return self


class ResultRow(FrozenContract):
    feature_id: Identifier
    status: Literal[
        "ok",
        "independent_filtered",
        "fit_failed",
        "outlier",
        "not_estimable",
        "not_tested",
        "partially_available",
    ]
    effect: Finite | None
    p_value: Probability | None
    adjusted_p_value: Probability | None
    standard_error: Annotated[float, Field(ge=0, allow_inf_nan=False)] | None
    missing_reasons: dict[
        Literal["effect", "p_value", "adjusted_p_value", "standard_error"], Text
    ]

    @model_validator(mode="after")
    def missing_metrics(self):
        for metric in METRICS:
            if (getattr(self, metric) is None) != (metric in self.missing_reasons):
                raise ValueError(
                    "Every missing statistic requires its own reason; numbers "
                    "cannot carry missing reasons"
                )
        if self.status in {"fit_failed", "not_estimable", "not_tested"} and any(
            getattr(self, name) is not None for name in METRICS
        ):
            raise ValueError(
                "Unestimated rows retain null statistics rather than substituted zeroes"
            )
        if self.status == "independent_filtered" and (
            self.adjusted_p_value is not None
            or self.missing_reasons.get("adjusted_p_value") != "independent_filtering"
        ):
            raise ValueError(
                "Independent filtering retains a missing adjusted probability and "
                "its reason"
            )
        # Here "outlier" denotes a suppressed test, not merely detection of an
        # unusual observation. Successful refits/unfiltered tests use an
        # available status and retain their method/diagnostic provenance.
        if self.status == "outlier" and (
            self.p_value is not None or self.adjusted_p_value is not None
        ):
            raise ValueError(
                "Outlier-suppressed tests retain null raw and adjusted probabilities"
            )
        if self.status == "partially_available" and (
            all(getattr(self, metric) is None for metric in METRICS)
            or all(getattr(self, metric) is not None for metric in METRICS)
        ):
            raise ValueError(
                "A partial row must preserve both available and unavailable metrics"
            )
        if self.status == "ok" and (self.effect is None or self.p_value is None):
            raise ValueError(
                "An ok fitted row requires its effect and raw test probability"
            )
        return self


class EvidenceReference(FrozenContract):
    evidence_id: Identifier
    sha256: Sha256


class GeneSetDatabaseReference(EvidenceReference):
    database_id: Identifier
    version: Text


class RankingReference(EvidenceReference):
    statistic: Literal["effect", "p_value", "adjusted_p_value"]
    direction: Literal["ascending", "descending"]


class GeneSetMetadata(FrozenContract):
    database: GeneSetDatabaseReference
    source_result_evidence_id: Identifier
    source_result_sha256: Sha256
    contrast_id: Identifier


class RankedEnrichmentMetadata(GeneSetMetadata):
    kind: Literal["ranked_enrichment"]
    ranking: RankingReference


class OverrepresentationMetadata(GeneSetMetadata):
    kind: Literal["overrepresentation"]
    universe: EvidenceReference
    selection: EvidenceReference


class GeneSetMembership(FrozenContract):
    set_id: Identifier
    feature_ids: Ids

    @model_validator(mode="after")
    def identities(self):
        _unique(self.feature_ids, "gene-set member")
        return self


class GeneSetLibrary(FrozenContract):
    record_format: Literal["devbench.scientific-gene-set-library/1"]
    database_id: Identifier
    version: Text
    sets: Annotated[tuple[GeneSetMembership, ...], Field(max_length=4096)]

    @model_validator(mode="after")
    def identities(self):
        _unique([item.set_id for item in self.sets], "gene-set database entry")
        return self


class GeneRanking(FrozenContract):
    record_format: Literal["devbench.scientific-ranking/1"]
    source_result_sha256: Sha256
    contrast_id: Identifier
    statistic: Literal["effect", "p_value", "adjusted_p_value"]
    direction: Literal["ascending", "descending"]
    feature_ids: Ids
    values: Annotated[tuple[Finite, ...], Field(max_length=MAX_RESULT_FEATURES)]

    @model_validator(mode="after")
    def ordering(self):
        _unique(self.feature_ids, "ranked feature")
        if (
            len(self.feature_ids) != len(self.values)
            or tuple(sorted(self.values, reverse=self.direction == "descending"))
            != self.values
        ):
            raise ValueError(
                "Ranking values must follow their declared ordering and feature "
                "identities"
            )
        return self


class GeneMappingRow(FrozenContract):
    source_id: Identifier
    target_id: Identifier | None
    status: Literal["mapped", "unmapped", "ambiguous"]

    @model_validator(mode="after")
    def status_target(self):
        if (self.status == "mapped") != (self.target_id is not None):
            raise ValueError(
                "Mapping status must preserve unresolved target identities"
            )
        return self


class GeneMapping(FrozenContract):
    record_format: Literal["devbench.scientific-gene-mapping/1"]
    mapping_id: Identifier
    version: Text
    source_namespace: Text
    target_namespace: Text
    provenance: Text
    records: Annotated[
        tuple[GeneMappingRow, ...], Field(max_length=MAX_RESULT_FEATURES)
    ]

    @model_validator(mode="after")
    def identities(self):
        _unique([row.source_id for row in self.records], "mapping source")
        _unique(
            [row.target_id for row in self.records if row.target_id is not None],
            "unambiguous mapping target",
        )
        return self


class GeneSetUniverse(FrozenContract):
    record_format: Literal["devbench.scientific-gene-set-universe/1"]
    source_result_sha256: Sha256
    contrast_id: Identifier
    eligible_ids: Ids
    mapped_ids: Ids
    mapping: EvidenceReference

    @model_validator(mode="after")
    def identities(self):
        _unique(self.eligible_ids, "universe eligible feature")
        _unique(self.mapped_ids, "universe mapped feature")
        return self


class GeneSetSelection(FrozenContract):
    record_format: Literal["devbench.scientific-gene-set-selection/1"]
    source_result_sha256: Sha256
    contrast_id: Identifier
    feature_ids: Ids
    provenance: Text

    @model_validator(mode="after")
    def identities(self):
        _unique(self.feature_ids, "selected mapped feature")
        return self


class ScientificResult(FrozenContract):
    record_format: Literal["devbench.scientific-result-diagnostic/1"]
    result_id: Identifier
    input_root: Sha256
    cohort: ResultCohort
    biological_unit_count: Annotated[int, Field(ge=1)]
    contrast: ResultContrast
    method: ResultMethod
    family: ResultFamily
    rows: Annotated[tuple[ResultRow, ...], Field(max_length=MAX_RESULT_FEATURES)]
    result_kind: Literal["gene_effect", "ranked_enrichment", "overrepresentation"] = (
        "gene_effect"
    )
    gene_set: RankedEnrichmentMetadata | OverrepresentationMetadata | None = None

    def _check_result_kind(self):
        if self.result_kind == "gene_effect":
            if (
                self.gene_set is not None
                or self.method.effect_scale == "normalized_enrichment_score"
            ):
                raise ValueError(
                    "A gene-set score cannot silently become a gene-effect result"
                )
        elif self.gene_set is None or self.result_kind != self.gene_set.kind:
            raise ValueError(
                "Gene-set results require the matching structured result-kind evidence"
            )
        if (
            self.result_kind == "ranked_enrichment"
            and self.method.effect_scale != "normalized_enrichment_score"
        ):
            raise ValueError(
                "Ranked enrichment must identify the supplied NES effect scale"
            )
        if self.result_kind == "overrepresentation" and (
            self.method.effect_scale not in {"odds_ratio", "overlap_count"}
            or any(row.effect is not None and row.effect < 0 for row in self.rows)
        ):
            raise ValueError(
                "ORA requires an explicit nonnegative ratio or overlap metric"
            )

    @model_validator(mode="after")
    def population(self):
        self._check_result_kind()
        if self.biological_unit_count != len(set(self.cohort.biological_unit_ids)):
            raise ValueError(
                "Biological unit count must count distinct declared units, not "
                "sample labels"
            )
        _unique([row.feature_id for row in self.rows], "result feature")
        eligible = set(self.family.eligible_ids)
        retained = {
            row.feature_id for row in self.rows if row.status != "independent_filtered"
        }
        if not retained <= eligible or (self.family.complete and retained != eligible):
            raise ValueError(
                "Complete families must retain all eligible hypotheses, including "
                "failed fits"
            )
        if any(
            row.feature_id in eligible
            for row in self.rows
            if row.status == "independent_filtered"
        ):
            raise ValueError(
                "Independent-filtered rows are outside the adjustment family"
            )
        if self.family.adjustment == "benjamini_hochberg":
            for row in self.rows:
                if row.adjusted_p_value is not None and (
                    row.p_value is None or row.adjusted_p_value < row.p_value
                ):
                    raise ValueError("BH adjusted probabilities require q >= p")
        return self


class ResultBinding(FrozenContract):
    evidence_id: Identifier
    result_id: Identifier
    cohort_sha256: Sha256
    contrast_id: Identifier
    method_id: Identifier
    method_version: Text
    effect_scale: Text
    family_id: Identifier | None = None
    family_sha256: Sha256 | None = None
    result_kind: Literal["gene_effect", "ranked_enrichment", "overrepresentation"] = (
        "gene_effect"
    )


class PresentationRule(FrozenContract):
    rule_id: Identifier
    evidence_id: Identifier
    pointer: Annotated[str, Field(min_length=1, max_length=1000, pattern=r"^/")]
    metric_kind: Literal[
        "exact",
        "rounded",
        "significant_figures",
        "probability",
        "log_probability",
        "relative",
        "direction",
        "rank_direction",
        "count",
        "missing",
    ]
    decimal_places: Annotated[int, Field(ge=0, le=15)] | None = None
    significant_figures: Annotated[int, Field(ge=1, le=15)] | None = None
    rounding: Literal["half_even", "half_up"] = "half_even"
    relative_tolerance: (
        Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)] | None
    ) = None
    log10_tolerance: Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)] | None = (
        None
    )
    # Frozen decisions use value < threshold; equality is non-rejecting.
    decision_threshold: (
        Annotated[float, Field(gt=0, lt=1, allow_inf_nan=False)] | None
    ) = None
    feature_id: Identifier | None = None
    unit: Text | None = None
    missing_reason: Text | None = None

    @model_validator(mode="after")
    def comparison_policy(self):
        required = {
            "rounded": "decimal_places",
            "significant_figures": "significant_figures",
            "probability": "relative_tolerance",
            "relative": "relative_tolerance",
            "log_probability": "log10_tolerance",
            "missing": "missing_reason",
        }
        if (
            self.metric_kind in required
            and getattr(self, required[self.metric_kind]) is None
        ):
            raise ValueError(
                "Presentation rule must freeze its comparison precision or missing "
                "reason"
            )
        for field, kinds in {
            "decimal_places": {"rounded"},
            "significant_figures": {"significant_figures"},
            "relative_tolerance": {"relative", "probability"},
            "log10_tolerance": {"log_probability"},
            "decision_threshold": {"probability", "log_probability"},
            "missing_reason": {"missing"},
        }.items():
            if getattr(self, field) is not None and self.metric_kind not in kinds:
                raise ValueError(
                    "Comparison parameters must match the declared metric kind"
                )
        return self


class QCRequirement(FrozenContract):
    requirement_id: Identifier
    origin: Literal["user"]
    source_text: Text
    record_sha256: Sha256
    metric: Identifier
    unit: Text
    denominator: Text
    operator: Literal["less_than", "greater_than"]
    threshold: Finite


class QCProvenance(FrozenContract):
    origin: Literal[
        "user", "protocol", "literature", "operational", "fixture", "assistant"
    ]
    record_id: Identifier
    record_sha256: Sha256
    rationale: Text
    requirement_record: QCRequirement | None = None


class QCPolicy(FrozenContract):
    policy_id: Identifier
    version: Text
    metric: Identifier
    unit: Text
    denominator: Text
    applicability: Text
    provenance: QCProvenance
    operator: Literal["less_than", "greater_than"]
    threshold: Finite
    observed_value: Finite | None
    consequence: Literal["warning", "fatal_eligibility"]
    limitations: Text
    observed_evidence_id: Identifier | None = None
    observed_pointer: Annotated[str, Field(pattern=r"^/")] | None = None

    @model_validator(mode="after")
    def source(self):
        source = self.provenance
        if source.origin == "user":
            record = source.requirement_record
            if (
                record is None
                or source.record_sha256 != record.record_sha256
                or any(
                    getattr(self, key) != getattr(record, key)
                    for key in (
                        "metric",
                        "unit",
                        "denominator",
                        "operator",
                        "threshold",
                    )
                )
            ):
                raise ValueError(
                    "User-origin QC policy requires an explicit matching "
                    "requirement record"
                )
        elif source.requirement_record is not None:
            raise ValueError(
                "A user requirement record cannot relabel a different policy origin"
            )
        if (self.observed_evidence_id is None) != (self.observed_pointer is None):
            raise ValueError("Observed QC evidence requires both artifact and pointer")
        return self


class PlotSpecification(FrozenContract):
    record_format: Literal["devbench.scientific-plot-specification/1"]
    result_sha256: Sha256
    cohort_sha256: Sha256
    contrast_id: Identifier
    feature_ids: Ids
    plot_type: Text
    x_metric: Text
    y_metric: Text
    transformations: Annotated[tuple[Text, ...], Field(max_length=32)]


class PlotBinding(FrozenContract):
    plot_evidence_id: Identifier
    result_evidence_id: Identifier
    result_sha256: Sha256
    cohort_sha256: Sha256
    contrast_id: Identifier
    feature_ids: Ids
    specification_evidence_id: Identifier
    specification_sha256: Sha256


class ClaimPacketV2(ClaimPacket):
    record_format: Literal["devbench.scientific-claim-packet/2"] = (
        "devbench.scientific-claim-packet/2"
    )
    result_bindings: Annotated[tuple[ResultBinding, ...], Field(max_length=64)]
    presentation_rules: Annotated[tuple[PresentationRule, ...], Field(max_length=128)]
    qc_policies: Annotated[tuple[QCPolicy, ...], Field(max_length=64)]
    plot_bindings: Annotated[tuple[PlotBinding, ...], Field(max_length=64)]

    @model_validator(mode="after")
    def result_references(self):
        ids = {item.evidence_id for item in self.evidence}
        for values, name in (
            (self.result_bindings, "evidence_id"),
            (self.presentation_rules, "rule_id"),
            (self.qc_policies, "policy_id"),
            (self.plot_bindings, "plot_evidence_id"),
        ):
            _unique([getattr(item, name) for item in values], name)
        bound = {item.evidence_id for item in self.result_bindings}
        if not bound <= ids or any(
            rule.evidence_id not in bound for rule in self.presentation_rules
        ):
            raise ValueError("Result rules must reference a known typed result binding")
        for policy in self.qc_policies:
            if (
                policy.observed_evidence_id is not None
                and policy.observed_evidence_id not in ids
            ):
                raise ValueError("Unknown QC evidence")
        for binding in self.plot_bindings:
            if (
                not {
                    binding.plot_evidence_id,
                    binding.result_evidence_id,
                    binding.specification_evidence_id,
                }
                <= ids
            ):
                raise ValueError("Unknown plot evidence")
        return self


class PresentationAssertion(FrozenContract):
    rule_id: Identifier
    value: float | int | str | None
    missing_reason: Text | None = None


class ClaimJudgmentV2(ClaimJudgment):
    record_format: Literal["devbench.scientific-judgment/2"] = (
        "devbench.scientific-judgment/2"
    )
    presentation_assertions: Annotated[
        tuple[PresentationAssertion, ...], Field(max_length=128)
    ] = ()

    @model_validator(mode="after")
    def rules(self):
        _unique(
            [item.rule_id for item in self.presentation_assertions],
            "presentation assertion",
        )
        return self


def resolve_pointer(value, pointer):
    try:
        for part in pointer[1:].split("/"):
            if re.search(r"~(?![01])", part):
                raise ValueError()
            key = part.replace("~1", "/").replace("~0", "~")
            if isinstance(value, list):
                if re.fullmatch(r"0|[1-9][0-9]*", key) is None:
                    raise ValueError()
                value = value[int(key)]
            else:
                value = value[key]
        return value
    except (KeyError, IndexError, TypeError, ValueError) as error:
        raise ValueError("Unsupported presentation/QC JSON pointer") from error


def _family_presentation_thresholds(result, presentation_rules):
    thresholds = {}
    for rule in presentation_rules:
        match = re.fullmatch(r"/rows/(0|[1-9][0-9]*)/adjusted_p_value", rule.pointer)
        if (
            match is not None
            and rule.decision_threshold is not None
            and int(match.group(1)) < len(result.rows)
        ):
            identity = result.rows[int(match.group(1))].feature_id
            thresholds.setdefault(identity, []).append(rule.decision_threshold)
    return thresholds


def multiplicity_check(result, *, presentation_rules=()):
    family = result.family
    base = {
        "family_id": family.family_id,
        "family_size": len(family.eligible_ids),
        "status": "not_assessed",
        "reason": None,
        "audited_count": 0,
    }
    if not family.complete:
        return {**base, "reason": "incomplete_tested_family"}
    if (
        family.adjustment != "benjamini_hochberg"
        or family.failed_hypothesis_policy == "unknown"
    ):
        return {**base, "reason": "unsupported_or_unknown_adjustment_policy"}
    rows = {row.feature_id: row for row in result.rows}
    hypotheses = [rows[key] for key in family.eligible_ids]
    if family.failed_hypothesis_policy == "exclude_failed_hypotheses":
        hypotheses = [row for row in hypotheses if row.p_value is not None]
    if not hypotheses:
        return {**base, "reason": "empty_tested_family"}
    ordered = sorted(
        hypotheses, key=lambda row: 1.0 if row.p_value is None else row.p_value
    )
    thresholds = _family_presentation_thresholds(result, presentation_rules)
    running = 1.0
    count = 0
    for rank in range(len(ordered), 0, -1):
        row = ordered[rank - 1]
        probability = 1.0 if row.p_value is None else row.p_value
        running = min(running, probability * (len(ordered) / rank))
        if row.p_value is not None:
            if row.adjusted_p_value is None or not math.isclose(
                row.adjusted_p_value, running, rel_tol=1e-10, abs_tol=0.0
            ):
                raise ValueError(
                    "BH probabilities do not match the complete declared testing family"
                )
            if any(
                (row.adjusted_p_value < threshold) != (running < threshold)
                for threshold in thresholds.get(row.feature_id, ())
            ):
                raise ValueError(
                    "Captured and independently recomputed BH cross a frozen "
                    "presentation decision boundary"
                )
            count += 1
    return {**base, "status": "verified", "reason": None, "audited_count": count}


_RESULT_MANIFEST = "devbench.scientific-result-manifest/1"
_RESULT_ROWS = "devbench.scientific-result-parquet-rows/1"
_RESULT_MANIFEST_MAX_BYTES = 1024**2
_RESULT_BINARY_PACKET_BYTES = 512 * 1024**2
_RESULT_DECODED_PACKET_BYTES = 256 * 1024**2
_RESULT_BINARY_PACKET_ROWS = 200_000


class ParquetResultRows(EvidenceReference):
    size_bytes: Annotated[int, Field(strict=True, ge=1, le=256 * 1024**2)]
    row_count: Annotated[int, Field(strict=True, ge=1, le=MAX_RESULT_FEATURES)]


def _parquet_result_rows(path, reference, *, max_decoded_bytes):
    import pyarrow as pa

    from devbench.core.binary_artifacts import bound_parquet, parquet_batches

    reasons = pa.struct([pa.field(name, pa.string()) for name in METRICS])
    expected = pa.schema(
        [
            ("feature_id", pa.string()),
            ("status", pa.string()),
            *((name, pa.float64()) for name in METRICS),
            ("missing_reasons", reasons),
        ]
    )
    rows, decoded = [], 0
    with bound_parquet(
        path, reference.sha256, reference.size_bytes, expected_size=reference.size_bytes
    ) as reader:
        if (
            not reader.schema_arrow.equals(expected, check_metadata=False)
            or reader.metadata.num_rows != reference.row_count
        ):
            raise ValueError("Unsupported typed result Parquet schema or row count")
        for batch in parquet_batches(reader, max_decoded_bytes=max_decoded_bytes):
            decoded += batch.nbytes
            for row in batch.to_pylist():
                if row["missing_reasons"] is None:
                    raise ValueError(
                        "Result Parquet requires explicit missing-reason fields"
                    )
                row["missing_reasons"] = {
                    key: value
                    for key, value in row["missing_reasons"].items()
                    if value is not None
                }
                rows.append(row)
    return rows, decoded


def _result_manifest_document(value, rows):
    document = {key: entry for key, entry in value.items() if key != "rows_ref"}
    document.update(record_format="devbench.scientific-result-diagnostic/1", rows=rows)
    family = document.get("family")
    if (
        isinstance(family, dict)
        and family.get("complete") is True
        and "eligible_ids" not in family
    ):
        document["family"] = {
            **family,
            "eligible_ids": [
                row["feature_id"]
                for row in rows
                if row["status"] != "independent_filtered"
            ],
        }
    return document


def load_retained_result_manifest(raw, source_path, *, guard_companion):
    """Open the same-workspace, digest-addressed companion of an accepted manifest."""
    from pathlib import Path

    from devbench.study.evidence import decode_json

    if len(raw) > _RESULT_MANIFEST_MAX_BYTES:
        raise ValueError("Typed result manifest exceeds its byte bound")
    value = decode_json(raw)
    if (
        type(value) is not dict
        or value.get("record_format") != _RESULT_MANIFEST
        or "rows" in value
    ):
        raise ValueError(
            "An exact typed result manifest without inline rows is required"
        )
    reference = ParquetResultRows.model_validate(value.get("rows_ref"))
    guard_companion(reference.sha256)
    bound = Path(source_path).with_name(reference.sha256 + ".parquet")
    rows, _ = _parquet_result_rows(
        bound, reference, max_decoded_bytes=_RESULT_DECODED_PACKET_BYTES
    )
    result = _scientific_result(_result_manifest_document(value, rows))
    multiplicity_check(result)
    return result


def _decode_claim_artifact(item, raw):
    from devbench.study.evidence import decode_json

    if item.kind == "diagnostic":
        value = decode_json(raw)
        if isinstance(value, dict):
            kind = value.get("record_format")
            if kind == _RESULT_ROWS:
                raise ValueError(
                    "Parquet evidence cannot be supplied as a JSON descriptor"
                )
            if kind == _RESULT_MANIFEST and len(raw) > _RESULT_MANIFEST_MAX_BYTES:
                raise ValueError("Typed result manifest exceeds its byte bound")
    elif item.kind == "source":
        if item.source.actual_text not in raw.decode("utf-8"):
            raise ValueError(
                "Retrieved source text is absent from the verified artifact"
            )
        value = item.source.actual_text
    else:
        value = None
    return value


def _bounded_result_capture(packet, read, absolute):
    evidence = {item.evidence_id: item for item in packet.evidence}
    captured = {}
    total = 0

    def capture(evidence_id, limit):
        nonlocal total
        if evidence_id in captured:
            return captured[evidence_id]
        item = evidence.get(evidence_id)
        if item is None:
            raise ValueError("Unknown typed companion evidence reference")
        remaining = MAX_PACKET_EVIDENCE_BYTES - total
        if remaining <= 0:
            raise ValueError("Claim packet cumulative evidence byte bound exceeded")
        raw = read(absolute(item.path), min(limit, remaining))
        total += len(raw)
        if hashlib.sha256(raw).hexdigest() != item.sha256:
            raise ValueError("Evidence artifact changed: " + evidence_id)
        value = _decode_claim_artifact(item, raw)
        captured[evidence_id] = value
        return value

    def companion(reference, model):
        item = evidence.get(reference.evidence_id)
        if item is None or item.kind != "diagnostic" or item.sha256 != reference.sha256:
            raise ValueError(
                "Typed companion must bind its declared diagnostic artifact"
            )
        return model.model_validate_json(
            json.dumps(
                capture(reference.evidence_id, MAX_RESULT_BYTES), allow_nan=False
            )
        )

    return evidence, captured, capture, companion


def _capture_bound_results(packet, evidence, captured, capture, absolute):
    results = []
    binary_total = decoded_total = binary_rows = 0
    bound_ids = {binding.evidence_id for binding in packet.result_bindings}
    for binding in packet.result_bindings:
        item = evidence.get(binding.evidence_id)
        if item is None or item.kind != "diagnostic":
            raise ValueError("Typed result must bind diagnostic JSON evidence")
        value = capture(binding.evidence_id, MAX_RESULT_BYTES)
        if isinstance(value, dict) and value.get("record_format") == _RESULT_MANIFEST:
            if "rows" in value:
                raise ValueError("A typed result manifest cannot include inline rows")
            reference = ParquetResultRows.model_validate(value.get("rows_ref"))
            table = evidence.get(reference.evidence_id)
            if (
                table is None
                or table.kind != "diagnostic"
                or table.sha256 != reference.sha256
                or reference.evidence_id in bound_ids
                or reference.evidence_id in captured
            ):
                raise ValueError(
                    "Typed result manifest requires a distinct bound Parquet companion"
                )
            binary_total += reference.size_bytes
            binary_rows += reference.row_count
            if (
                binary_total > _RESULT_BINARY_PACKET_BYTES
                or binary_rows > _RESULT_BINARY_PACKET_ROWS
            ):
                raise ValueError(
                    "Claim packet cumulative binary evidence bound exceeded"
                )
            rows, decoded = _parquet_result_rows(
                absolute(table.path),
                reference,
                max_decoded_bytes=_RESULT_DECODED_PACKET_BYTES - decoded_total,
            )
            decoded_total += decoded
            value = _result_manifest_document(value, rows)
            captured[binding.evidence_id] = value
            captured[reference.evidence_id] = {
                "record_format": _RESULT_ROWS,
                "row_count": reference.row_count,
                "size_bytes": reference.size_bytes,
                "sha256": reference.sha256,
            }
        results.append(_scientific_result(value))
    return results


def _capture_gene_set_companions(results, companion):
    for result in results:
        metadata = result.gene_set
        if metadata is None:
            continue
        companion(metadata.database, GeneSetLibrary)
        if metadata.kind == "ranked_enrichment":
            companion(metadata.ranking, GeneRanking)
        else:
            universe = companion(metadata.universe, GeneSetUniverse)
            companion(universe.mapping, GeneMapping)
            companion(metadata.selection, GeneSetSelection)


def capture_v2_artifacts(packet, *, read, absolute):
    """Admit a closed typed-reference graph before generic evidence, in any list order.

    A record-format string alone never enlarges a read budget. Each companion
    must be referenced by an admitted typed result (or its typed ORA universe),
    match its hash, and validate against its specific closed companion schema.
    Each artifact is captured once under the same cumulative budget.
    """
    evidence, captured, capture, companion = _bounded_result_capture(
        packet, read, absolute
    )
    results = _capture_bound_results(packet, evidence, captured, capture, absolute)
    _capture_gene_set_companions(results, companion)
    for item in packet.evidence:
        capture(item.evidence_id, 128_000)
    return {item.evidence_id: captured[item.evidence_id] for item in packet.evidence}


def _scientific_result(value):
    # Converting through JSON preserves strict tuple and scalar validation at
    # the public boundary even when the rows originated in Arrow.
    return ScientificResult.model_validate_json(json.dumps(value, allow_nan=False))


def _validate_bound_results(packet, artifacts, evidence, results):
    for binding in packet.result_bindings:
        if evidence[binding.evidence_id].kind != "diagnostic":
            raise ValueError("Typed results require diagnostic JSON evidence")
        result = _scientific_result(artifacts[binding.evidence_id])
        if (
            result.input_root != packet.input_root
            or result.result_id != binding.result_id
            or content_sha256(result.cohort.model_dump(mode="json"))
            != binding.cohort_sha256
            or result.contrast.contrast_id != binding.contrast_id
            or result.method.method_id != binding.method_id
            or result.method.version != binding.method_version
            or result.method.effect_scale != binding.effect_scale
            or result.result_kind != binding.result_kind
            or binding.family_id is not None
            and result.family.family_id != binding.family_id
            or binding.family_sha256 is not None
            and content_sha256(result.family.model_dump(mode="json"))
            != binding.family_sha256
        ):
            raise ValueError(
                "Typed result input/cohort/contrast/method/family identity does "
                "not match its binding"
            )
        multiplicity_check(
            result,
            presentation_rules=[
                rule
                for rule in packet.presentation_rules
                if rule.evidence_id == binding.evidence_id
            ],
        )
        results[binding.evidence_id] = result


def _verify_result_and_plot_coverage(packet, artifacts, results):
    # A typed result cannot evade checks by dropping its binding.
    for item in packet.evidence:
        content = artifacts[item.evidence_id]
        if isinstance(content, dict) and (
            content.get("record_format") == _RESULT_MANIFEST
            or str(content.get("record_format", "")).startswith(
                "devbench.scientific-result-diagnostic/"
            )
        ):
            if (
                content["record_format"] != "devbench.scientific-result-diagnostic/1"
                or item.evidence_id not in results
            ):
                raise ValueError(
                    "Every supported typed result artifact requires a result binding"
                )
    if {item.evidence_id for item in packet.evidence if item.kind == "plot"} != {
        binding.plot_evidence_id for binding in packet.plot_bindings
    }:
        raise ValueError(
            "Every /2 plot requires its source/population/contrast/specification "
            "binding"
        )


def _validate_bound_plots(packet, artifacts, evidence, results):
    for binding in packet.plot_bindings:
        if (
            evidence[binding.plot_evidence_id].kind != "plot"
            or binding.result_evidence_id not in results
            or evidence[binding.specification_evidence_id].kind != "diagnostic"
            or evidence[binding.specification_evidence_id].sha256
            != binding.specification_sha256
            or evidence[binding.result_evidence_id].sha256 != binding.result_sha256
        ):
            raise ValueError("Plot requires current result and specification evidence")
        result = results[binding.result_evidence_id]
        spec = PlotSpecification.model_validate_json(
            json.dumps(artifacts[binding.specification_evidence_id], allow_nan=False)
        )
        for key in ("result_sha256", "cohort_sha256", "contrast_id", "feature_ids"):
            if getattr(spec, key) != getattr(binding, key):
                raise ValueError(
                    "Plot specification differs from bound source/population/contrast"
                )
        if (
            binding.cohort_sha256
            != content_sha256(result.cohort.model_dump(mode="json"))
            or binding.contrast_id != result.contrast.contrast_id
            or not set(binding.feature_ids) <= {row.feature_id for row in result.rows}
        ):
            raise ValueError("Plot uses stale population, contrast or features")
        _unique(binding.feature_ids, "plotted feature")


def validate_result_evidence(packet, artifacts):
    """Check captured content only: caller hashes the same bytes before parsing."""
    from .method_planner_contract import _reject_capabilities
    from .scientific_judgment import _blinded_diagnostics

    results = {}
    evidence = {item.evidence_id: item for item in packet.evidence}
    for item in packet.evidence:
        if item.kind == "diagnostic":
            _blinded_diagnostics(artifacts[item.evidence_id])
            _reject_capabilities(artifacts[item.evidence_id])
            _reject_review_metadata(artifacts[item.evidence_id])
    _validate_bound_results(packet, artifacts, evidence, results)
    validate_gene_set_evidence(packet, artifacts, results)
    _verify_result_and_plot_coverage(packet, artifacts, results)
    for rule in packet.presentation_rules:
        _rule_value(rule, artifacts, results)
    for policy in packet.qc_policies:
        if policy.observed_evidence_id is not None:
            actual = resolve_pointer(
                artifacts[policy.observed_evidence_id], policy.observed_pointer
            )
            if type(actual) not in {int, float} or actual != policy.observed_value:
                raise ValueError(
                    "QC observation does not match captured diagnostic evidence"
                )
    _validate_bound_plots(packet, artifacts, evidence, results)
    return results


def _rule_value(rule, artifacts, results):
    value = resolve_pointer(artifacts[rule.evidence_id], rule.pointer)
    result = results[rule.evidence_id]
    match = re.fullmatch(
        r"/rows/(0|[1-9][0-9]*)/(effect|p_value|adjusted_p_value|standard_error)",
        rule.pointer,
    )
    metric = (
        match.group(2)
        if match
        else "biological_unit_count"
        if rule.pointer == "/biological_unit_count"
        else None
    )
    if metric is None:
        raise ValueError("Presentation rule must name an actual typed statistic")
    row = result.rows[int(match.group(1))] if match else None
    if rule.feature_id is not None and (
        row is None or rule.feature_id != row.feature_id
    ):
        raise ValueError("Presentation feature identity does not match its result row")
    unit = (
        "probability"
        if metric in {"p_value", "adjusted_p_value"}
        else "biological_units"
        if metric == "biological_unit_count"
        else result.method.effect_scale
    )
    if rule.unit is not None and rule.unit != unit:
        raise ValueError("Presentation unit differs from its typed metric")
    if (
        rule.metric_kind in {"probability", "log_probability"}
        and metric not in {"p_value", "adjusted_p_value"}
        or rule.metric_kind == "direction"
        and metric != "effect"
        or rule.metric_kind == "direction"
        and result.result_kind != "gene_effect"
        or rule.metric_kind == "rank_direction"
        and (metric != "effect" or result.result_kind != "ranked_enrichment")
        or rule.metric_kind == "count"
        and not (
            metric == "biological_unit_count"
            or metric == "effect"
            and result.result_kind == "overrepresentation"
            and result.method.effect_scale == "overlap_count"
        )
        or metric == "biological_unit_count"
        and rule.metric_kind not in {"count", "exact"}
        or metric in {"p_value", "adjusted_p_value"}
        and rule.metric_kind
        not in {"probability", "log_probability", "exact", "missing"}
    ):
        raise ValueError("Presentation comparison kind does not match metric identity")
    if rule.metric_kind == "missing":
        if (
            row is None
            or value is not None
            or row.missing_reasons.get(metric) != rule.missing_reason
        ):
            raise ValueError(
                "Missing presentation rule must preserve the statistic missing reason"
            )
    elif value is None:
        raise ValueError("A missing statistic cannot be presented as a number")
    return value


def _rounded(value, places, rounding):
    with localcontext() as context:
        context.prec = 400
        return Decimal(str(value)).quantize(
            Decimal(1).scaleb(-places),
            rounding=ROUND_HALF_EVEN if rounding == "half_even" else ROUND_HALF_UP,
        )


def _probability_presentation_matches(rule, kind, actual, claimed):
    # No absolute epsilon: finite tiny p values cannot become zero.
    if actual == 0 or claimed == 0:
        valid = actual == claimed
    elif kind == "probability":
        valid = abs(claimed / actual - 1) <= rule.relative_tolerance
    else:
        valid = abs(math.log10(claimed) - math.log10(actual)) <= rule.log10_tolerance
    if rule.decision_threshold is not None:
        valid = valid and (actual < rule.decision_threshold) == (
            claimed < rule.decision_threshold
        )
    return valid


def _numeric_presentation_matches(rule, claimed, actual):
    valid = False
    kind = rule.metric_kind
    if kind == "count":
        valid = type(claimed) is int and claimed == actual
    elif kind == "exact":
        valid = claimed == actual
    elif kind in {"rounded", "significant_figures"}:
        places = (
            rule.decimal_places
            if kind == "rounded"
            else (
                rule.significant_figures - 1 - Decimal(str(abs(actual))).adjusted()
                if actual
                else 0
            )
        )
        valid = Decimal(str(claimed)) == _rounded(actual, places, rule.rounding)
    elif kind == "relative":
        valid = (actual > 0) - (actual < 0) == (claimed > 0) - (
            claimed < 0
        ) and math.isclose(
            actual, claimed, rel_tol=rule.relative_tolerance, abs_tol=0.0
        )
    elif (
        kind in {"probability", "log_probability"}
        and 0 <= claimed <= 1
        and 0 <= actual <= 1
    ):
        valid = _probability_presentation_matches(rule, kind, actual, claimed)
    return valid


def _presentation_matches(rule, assertion, actual):
    claimed = assertion.value
    valid = False
    if rule.metric_kind == "missing":
        valid = claimed is None and assertion.missing_reason == rule.missing_reason
    elif assertion.missing_reason is not None:
        valid = False
    elif rule.metric_kind == "direction":
        valid = claimed == (
            "positive" if actual > 0 else "negative" if actual < 0 else "zero"
        )
    elif rule.metric_kind == "rank_direction":
        valid = claimed == (
            "enriched_toward_top_of_rank"
            if actual > 0
            else "enriched_toward_bottom_of_rank"
            if actual < 0
            else "no_signed_rank_enrichment"
        )
    elif type(claimed) in {int, float} and math.isfinite(claimed):
        valid = _numeric_presentation_matches(rule, claimed, actual)
    return valid


def validate_presentation_assertions(packet, judgment, artifacts):
    # Result validation already occurred on these captured bytes.
    import json

    results = {
        binding.evidence_id: ScientificResult.model_validate_json(
            json.dumps(artifacts[binding.evidence_id], allow_nan=False)
        )
        for binding in packet.result_bindings
    }
    rules = {rule.rule_id: rule for rule in packet.presentation_rules}
    for assertion in judgment.presentation_assertions:
        if assertion.rule_id not in rules:
            raise ValueError("Unknown coordinator presentation rule")
        rule = rules[assertion.rule_id]
        if rule.evidence_id not in judgment.evidence_ids:
            raise ValueError("Presentation assertion must cite its result evidence")
        actual = _rule_value(rule, artifacts, results)
        valid = _presentation_matches(rule, assertion, actual)
        if not valid:
            raise ValueError(
                "Unsupported presentation assertion for coordinator metric rule"
            )


def qc_checks(packet):
    checks = []
    for policy in packet.qc_policies:
        triggered = (
            None
            if policy.observed_value is None
            else (
                policy.observed_value < policy.threshold
                if policy.operator == "less_than"
                else policy.observed_value > policy.threshold
            )
        )
        checks.append(
            {
                "policy_id": policy.policy_id,
                "version": policy.version,
                "metric": policy.metric,
                "unit": policy.unit,
                "denominator": policy.denominator,
                "applicability": policy.applicability,
                "provenance": policy.provenance.model_dump(mode="json"),
                "status": "not_assessed"
                if triggered is None
                else policy.consequence
                if triggered
                else "passed",
                "observation_basis": "verified_artifact"
                if policy.observed_evidence_id
                else "coordinator_declaration",
                "limitations": policy.limitations,
            }
        )
    return checks


def result_context(packet, artifacts):
    checks = []
    results = {}
    for binding in packet.result_bindings:
        result = _scientific_result(artifacts[binding.evidence_id])
        results[binding.evidence_id] = result
        checks.append(
            {
                "evidence_id": binding.evidence_id,
                "result_id": result.result_id,
                "cohort_sha256": binding.cohort_sha256,
                "contrast_id": binding.contrast_id,
                "method_id": binding.method_id,
                "method_version": binding.method_version,
                "multiplicity": multiplicity_check(result),
            }
        )
    return {
        "result_bindings": [
            binding.model_dump(mode="json") for binding in packet.result_bindings
        ],
        "result_checks": checks,
        "presentation_rules": [
            rule.model_dump(mode="json") for rule in packet.presentation_rules
        ],
        "qc_checks": qc_checks(packet),
        "plot_bindings": [
            binding.model_dump(mode="json") for binding in packet.plot_bindings
        ],
        "gene_set_checks": validate_gene_set_evidence(packet, artifacts, results),
    }


def _check_ranked_enrichment(metadata, source, source_evidence, captured, check):
    ranking = captured(metadata.ranking, GeneRanking)
    if (
        ranking.source_result_sha256 != source_evidence.sha256
        or ranking.contrast_id != metadata.contrast_id
        or ranking.statistic != metadata.ranking.statistic
        or ranking.direction != metadata.ranking.direction
    ):
        raise ValueError(
            "Ranking must bind the exact source statistic, ordering and contrast"
        )
    available = {
        row.feature_id: getattr(row, ranking.statistic)
        for row in source.rows
        if getattr(row, ranking.statistic) is not None
    }
    if set(ranking.feature_ids) != set(available) or any(
        value != available[key]
        for key, value in zip(ranking.feature_ids, ranking.values, strict=False)
    ):
        raise ValueError(
            "Ranking feature/value population differs from the captured source result"
        )
    check.update(
        ranking_statistic=ranking.statistic,
        ranking_direction=ranking.direction,
        ranked_count=len(ranking.feature_ids),
        interpretation=(
            "NES sign locates enrichment toward the top or bottom of this "
            "declared rank."
        ),
    )


def _check_overrepresentation(
    metadata, source, source_evidence, captured, check, library, result
):
    universe = captured(metadata.universe, GeneSetUniverse)
    mapping = captured(universe.mapping, GeneMapping)
    selection = captured(metadata.selection, GeneSetSelection)
    if (
        not source.family.complete
        or set(universe.eligible_ids) != set(source.family.eligible_ids)
        or {row.source_id for row in mapping.records} != set(universe.eligible_ids)
        or set(universe.mapped_ids)
        != {row.target_id for row in mapping.records if row.status == "mapped"}
    ):
        raise ValueError(
            "ORA eligible/mapped universe differs from the complete source "
            "family and mapping evidence"
        )
    for item in (universe, selection):
        if (
            item.source_result_sha256 != source_evidence.sha256
            or item.contrast_id != metadata.contrast_id
        ):
            raise ValueError(
                "ORA universe/selection must bind the current source "
                "result and contrast"
            )
    if not set(selection.feature_ids) <= set(universe.mapped_ids):
        raise ValueError(
            "ORA selected genes must belong to the declared mapped eligible universe"
        )
    if result.method.effect_scale == "overlap_count":
        members = {item.set_id: set(item.feature_ids) for item in library.sets}
        selected = set(selection.feature_ids)
        mapped = set(universe.mapped_ids)
        for row in result.rows:
            if row.effect is not None and (
                not row.effect.is_integer()
                or row.effect != len(selected & mapped & members[row.feature_id])
            ):
                raise ValueError(
                    "ORA overlap count must equal the exact retained "
                    "selected/mapped set intersection"
                )
    check.update(
        eligible_count=len(universe.eligible_ids),
        mapped_count=len(universe.mapped_ids),
        selected_count=len(selection.feature_ids),
        mapping_provenance=mapping.provenance,
        selection_provenance=selection.provenance,
        interpretation=(
            "ORA is conditional on the supplied selected genes and mapped "
            "eligible universe."
        ),
    )


def validate_gene_set_evidence(packet, artifacts, results):
    (
        "Check declared enrichment inputs, not enrichment estimation "
        "or biological activation."
    )
    import json

    evidence = {item.evidence_id: item for item in packet.evidence}

    def captured(reference, model):
        item = evidence.get(reference.evidence_id)
        if item is None or item.kind != "diagnostic" or item.sha256 != reference.sha256:
            raise ValueError(
                "Gene-set evidence must bind a current diagnostic artifact"
            )
        return model.model_validate_json(
            json.dumps(artifacts[reference.evidence_id], allow_nan=False)
        )

    checks = []
    for evidence_id, result in results.items():
        if result.result_kind == "gene_effect":
            continue
        metadata = result.gene_set
        source = results.get(metadata.source_result_evidence_id)
        source_evidence = evidence.get(metadata.source_result_evidence_id)
        if (
            source is None
            or source.result_kind != "gene_effect"
            or source_evidence.sha256 != metadata.source_result_sha256
            or source.contrast != result.contrast
            or metadata.contrast_id != source.contrast.contrast_id
            or source.cohort != result.cohort
            or source.family.family_id == result.family.family_id
        ):
            raise ValueError(
                "Gene-set results require distinct hypothesis families and exact "
                "source cohort/contrast identities"
            )
        library = captured(metadata.database, GeneSetLibrary)
        if (
            library.database_id != metadata.database.database_id
            or library.version != metadata.database.version
        ):
            raise ValueError(
                "Gene-set database identity/version differs from captured evidence"
            )
        known_sets = {item.set_id for item in library.sets}
        if (
            not (
                {row.feature_id for row in result.rows}
                | set(result.family.eligible_ids)
            )
            <= known_sets
        ):
            raise ValueError("Gene-set hypotheses must identify retained database sets")
        check = {
            "evidence_id": evidence_id,
            "kind": result.result_kind,
            "database_id": library.database_id,
            "database_version": library.version,
            "contrast_id": metadata.contrast_id,
            "source_result_evidence_id": metadata.source_result_evidence_id,
            "mechanistic_interpretation": "not_assessed",
            "scope": (
                "Declared input consistency; biological appropriateness and pathway "
                "activation/inhibition remain unassessed."
            ),
        }
        if result.result_kind == "ranked_enrichment":
            _check_ranked_enrichment(metadata, source, source_evidence, captured, check)
        else:
            _check_overrepresentation(
                metadata,
                source,
                source_evidence,
                captured,
                check,
                library,
                result,
            )
        checks.append(check)
    return checks


def _reject_review_metadata(value, *, allow_pointers=False):
    """Reject structured protected data in full artifacts before truncation."""
    forbidden = {
        "answer",
        "answers",
        "expectedanswer",
        "answerkey",
        "priorverdict",
        "priorverdicts",
        "path",
        "paths",
        "artifactpath",
        "filepath",
        "manifestpath",
        "credentials",
        "credential",
        "authorization",
        "approval",
        "executionauthority",
        "modelidentity",
        "modelid",
        "privategolden",
        "golden",
        "grader",
    }
    if isinstance(value, dict):
        for key, child in value.items():
            name = re.sub(r"[^a-z0-9]", "", str(key).casefold())
            if (
                name in forbidden
                or name in {"allowmodelexecution", "allowdataexecution"}
                or name.endswith(("executiongranted", "releaseallowed"))
                or any(
                    part in name
                    for part in (
                        "credential",
                        "secret",
                        "authorization",
                        "approvaltoken",
                        "accesstoken",
                        "apikey",
                    )
                )
            ):
                raise ValueError(
                    "Protected paths, answers or authority cannot enter /2 "
                    "scientific evidence"
                )
            if allow_pointers and key in {"pointer", "observed_pointer"}:
                continue
            _reject_review_metadata(child, allow_pointers=allow_pointers)
    elif isinstance(value, list):
        for child in value:
            _reject_review_metadata(child, allow_pointers=allow_pointers)
    elif isinstance(value, str) and (
        value.startswith(("/", "\\\\"))
        or re.match(r"^[A-Za-z]:[\\/]", value)
        or value.casefold().startswith("file:")
    ):
        raise ValueError(
            "Local filesystem paths cannot enter /2 scientific evidence projections"
        )


def _preview(value, *, depth=0):
    if depth > 12:
        return {"projection_omitted": "nested_value_exceeds_preview_depth"}
    if isinstance(value, dict):
        result = {
            key: _preview(child, depth=depth + 1)
            for key, child in list(value.items())[:32]
        }
        if len(value) > 32:
            result["projection_omitted_fields"] = len(value) - 32
        return result
    if isinstance(value, list):
        return [_preview(child, depth=depth + 1) for child in value[:PREVIEW_ROWS]]
    if isinstance(value, str) and len(value) > 4000:
        return value[:4000] + " [preview truncated]"
    return value


def project_diagnostics(packet, artifacts):
    "Project captured, fully validated bytes; never recompute a truncated BH family."
    bound = {binding.evidence_id for binding in packet.result_bindings}
    projected = []
    for evidence in packet.evidence:
        if evidence.kind != "diagnostic":
            continue
        content = artifacts[evidence.evidence_id]
        item = {
            "evidence_id": evidence.evidence_id,
            "sha256": evidence.sha256,
            "content": _preview(content),
            "projection": {"scope": "bounded_preview_not_full_artifact"},
        }
        if evidence.evidence_id in bound:
            rows = content["rows"]
            eligible = content["family"]["eligible_ids"]
            item["projection"].update(
                retained_rows=len(rows),
                projected_rows=min(PREVIEW_ROWS, len(rows)),
                omitted_rows=max(0, len(rows) - PREVIEW_ROWS),
                retained_family_ids=len(eligible),
                omitted_family_ids=max(0, len(eligible) - PREVIEW_ROWS),
            )
        projected.append(item)
    return projected


def presentation_evidence(packet, artifacts):
    """Preserve every frozen rule's exact target, including rows outside preview."""
    return [
        {
            "rule_id": rule.rule_id,
            "evidence_id": rule.evidence_id,
            "pointer": rule.pointer,
            "value": resolve_pointer(artifacts[rule.evidence_id], rule.pointer),
            "missing_reason": rule.missing_reason,
        }
        for rule in packet.presentation_rules
    ]
