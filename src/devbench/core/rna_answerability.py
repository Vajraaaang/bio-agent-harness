"""Measurement admission for scoped transcriptomics questions.

The coordinator supplies summaries from verified artifacts and design receipts.
This module examines their scientific compatibility without opening data,
discovering software, or granting execution or publication authority.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from types import MappingProxyType
from typing import Annotated, Literal

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from ._hashing import canonical_sha256
from .procedure_profile import EvidenceDigest

Assay = Literal[
    "bulk_rna_seq",
    "single_cell_rna_seq",
    "single_nucleus_rna_seq",
    "spatial_transcriptomics",
    "long_read_rna_seq",
    "direct_rna_seq",
    "small_rna_seq",
    "linked_modalities",
    "other_expression",
]
EntryPoint = Literal[
    "reads",
    "alignments",
    "transcript_quantification",
    "count_matrix",
    "normalized_expression",
    "annotated_object",
    "prior_results",
]
Measurement = Literal[
    "sequencing_reads",
    "read_alignments",
    "transcript_counts",
    "gene_counts",
    "estimated_gene_counts",
    "effective_lengths",
    "normalized_expression",
    "donor_pseudobulk_counts",
    "cell_type_counts",
    "cell_type_expression",
    "cell_type_reference",
    "splice_junction_counts",
    "transcript_structure_reads",
    "transcript_structure_alignments",
    "ranked_gene_statistics",
    "gene_sets",
    "filtered_tested_background",
    "foreground_gene_list",
    "regulon_edges",
    "sampled_time",
    "inferred_pseudotime",
    "batch_membership",
    "ortholog_map",
    "spatial_coordinates",
    "spatial_segmentation",
    "latent_embedding",
    "ligand_receptor_reference",
    "perturbation_assignments",
    "control_assignments",
    "genotypes",
    "allele_counts",
    "fusion_junction_evidence",
    "editing_site_counts",
    "small_rna_counts",
    "backsplice_counts",
    "repeat_counts",
    "spliced_counts",
    "unspliced_counts",
    "nascent_rna_measurements",
    "turnover_time_points",
    "modification_signal",
    "ribosome_footprint_counts",
    "organism_resolved_summary",
    "chromatin_accessibility",
    "protein_measurements",
    "imaging_measurements",
    "paired_modality_map",
    "clonotype_assignments",
    "lineage_assignments",
    "phenotype_outcomes",
    "independent_evaluation_partition",
    "published_analysis_protocol",
    "prior_study_results",
    "benchmark_data",
    "baseline_specification",
]
Priority = Literal[1, 2, 3]
AxisBinding = Literal["none", "observations", "features", "both"]
Status = Literal["ready", "awaiting_information", "refused", "unavailable"]
Text = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)
]
Identifier = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_.:-]{1,128}$")]
RequiredMeasurements = tuple[tuple[Measurement, ...], ...]

_CELLS: tuple[Assay, ...] = ("single_cell_rna_seq", "single_nucleus_rna_seq")
_SPATIAL: tuple[Assay, ...] = ("spatial_transcriptomics",)
_EXPRESSION: tuple[Measurement, ...] = (
    "gene_counts",
    "normalized_expression",
    "estimated_gene_counts",
)
_TYPE_COUNTS: tuple[Measurement, ...] = ("cell_type_counts",)
_READS: tuple[Measurement, ...] = ("sequencing_reads", "transcript_structure_reads")
_ALIGNMENTS: tuple[Measurement, ...] = (
    "read_alignments",
    "transcript_structure_alignments",
)


@dataclass(frozen=True)
class FamilyCapability:
    title: str
    priorities: Mapping[Assay, Priority]


_FAMILY_TITLES = (
    "Expression differences and differential cell states",
    "Cell identities, heterogeneity, and rare populations",
    "Cell abundance, composition, and deconvolution",
    "Alternative splicing and transcript usage",
    "Transcript discovery and annotation",
    "Pathways, gene programs, and networks",
    "Development, temporal responses, and state transitions",
    "Integration, atlas construction, and comparative transcriptomics",
    "Spatial organization and tissue context",
    "Cell–cell communication",
    "Perturbation responses and functional genomics",
    "Genetic regulation and allelic expression",
    "Transcript abnormalities and cancer-related questions",
    "Small, noncoding, circular, and repetitive RNAs",
    "RNA production, processing, turnover, modification, and translation",
    "Host–pathogen and community transcriptomics",
    "Multimodal, repertoire, and clonal analyses",
    "Prediction, signatures, and phenotype associations",
    "Reproduction, replication, and evidence synthesis",
    "Experimental design and method development",
)


def _priorities(family_id: int) -> Mapping[Assay, Priority]:
    priorities: dict[Assay, Priority] = {
        "bulk_rna_seq": 3,
        "single_cell_rna_seq": 3,
        "single_nucleus_rna_seq": 3,
        "spatial_transcriptomics": 3,
        "long_read_rna_seq": 3,
        "direct_rna_seq": 3,
        "small_rna_seq": 3,
        "linked_modalities": 3,
        "other_expression": 3,
    }
    if family_id in {1, 2, 3, 6, 7, 8}:
        priorities.update({assay: 1 for assay in _CELLS})
    if family_id in {1, 3, 6, 7, 8}:
        priorities["bulk_rna_seq"] = 2
    return MappingProxyType(priorities)


RNA_FAMILIES: Mapping[int, FamilyCapability] = MappingProxyType(
    {
        family_id: FamilyCapability(title, _priorities(family_id))
        for family_id, title in enumerate(_FAMILY_TITLES, 1)
    }
)


@dataclass(frozen=True)
class EntryPointContract:
    present_any_of: tuple[Measurement, ...]
    description: str


_COUNT_MATRIX: tuple[Measurement, ...] = (
    "gene_counts",
    "estimated_gene_counts",
    "transcript_counts",
    "donor_pseudobulk_counts",
    "cell_type_counts",
    "splice_junction_counts",
    "allele_counts",
    "editing_site_counts",
    "small_rna_counts",
    "backsplice_counts",
    "repeat_counts",
    "spliced_counts",
    "unspliced_counts",
    "ribosome_footprint_counts",
)
RNA_ENTRY_POINTS: Mapping[EntryPoint, EntryPointContract] = MappingProxyType(
    {
        "reads": EntryPointContract(
            _READS,
            "Local pre-staged reads with assay and reference evidence.",
        ),
        "alignments": EntryPointContract(
            _ALIGNMENTS,
            "Local alignments with their original reference and read scope.",
        ),
        "transcript_quantification": EntryPointContract(
            ("transcript_counts", "estimated_gene_counts"),
            "Transcript-level counts or estimates with retained length provenance.",
        ),
        "count_matrix": EntryPointContract(
            _COUNT_MATRIX,
            "Observed or estimated counts with distinct feature scopes.",
        ),
        "normalized_expression": EntryPointContract(
            ("normalized_expression", "cell_type_expression"),
            "Declared continuous expression and transformation history.",
        ),
        "annotated_object": EntryPointContract(
            (
                *_COUNT_MATRIX,
                "normalized_expression",
                "cell_type_expression",
                "latent_embedding",
                "spatial_coordinates",
                "organism_resolved_summary",
            ),
            "Individually attested measurements, layers, axes, coordinates and labels.",
        ),
        "prior_results": EntryPointContract(
            (
                "ranked_gene_statistics",
                "foreground_gene_list",
                "prior_study_results",
                "organism_resolved_summary",
                "benchmark_data",
            ),
            "Bound results and tested-family identity with separate data provenance.",
        ),
    }
)


@dataclass(frozen=True)
class QuestionContract:
    family_id: int
    measurements: RequiredMeasurements
    assays: tuple[Assay, ...] = ()
    align: AxisBinding = "none"
    min_units_per_group: int = 0
    reference_required: bool = False
    curated_operation: str | None = None
    intake_only: bool = False
    curated_alternatives: tuple[str, ...] = ()


# Each tuple inside measurements is a group of alternatives. Each group needs
# one explicit, receipt-backed selection so assay presence cannot imply a method.
RNA_QUESTIONS: Mapping[str, QuestionContract] = MappingProxyType(
    {
        "differential_expression": QuestionContract(
            1,
            (_EXPRESSION,),
            min_units_per_group=2,
            curated_operation="bulk_condition_contrast",
        ),
        "differential_cell_state": QuestionContract(
            1,
            (("donor_pseudobulk_counts",),),
            assays=_CELLS,
            min_units_per_group=2,
            curated_operation="bulk_condition_contrast",
        ),
        "cell_identity": QuestionContract(2, (_EXPRESSION,), assays=_CELLS),
        "compositional_abundance": QuestionContract(
            3,
            (_TYPE_COUNTS,),
            assays=_CELLS,
            min_units_per_group=3,
            curated_operation="scrna_sccoda",
            curated_alternatives=("scrna_neighborhood_da",),
        ),
        "bulk_deconvolution": QuestionContract(
            3,
            (_EXPRESSION, ("cell_type_reference",)),
            assays=("bulk_rna_seq",),
            align="features",
            curated_operation="bulk_deconvolution",
        ),
        "transcript_usage": QuestionContract(
            4,
            (("transcript_counts",),),
            min_units_per_group=2,
            reference_required=True,
        ),
        "splice_junction_usage": QuestionContract(
            4,
            (("splice_junction_counts",),),
            min_units_per_group=2,
            reference_required=True,
        ),
        "novel_transcripts": QuestionContract(
            5,
            (("transcript_structure_reads", "transcript_structure_alignments"),),
            reference_required=True,
        ),
        "pathway_enrichment": QuestionContract(
            6,
            (("ranked_gene_statistics",), ("gene_sets",)),
            curated_operation="rna_pathway_tested",
            curated_alternatives=("rna_pathway_camera",),
        ),
        "overrepresentation": QuestionContract(
            6,
            (
                ("foreground_gene_list",),
                ("filtered_tested_background",),
                ("gene_sets",),
            ),
            curated_operation="rna_pathway_tested",
            curated_alternatives=(
                "rna_pathway_marker_ora",
                "rna_pathway_nb_marker_ora",
            ),
        ),
        "regulon_activity": QuestionContract(
            6,
            (("normalized_expression",), ("regulon_edges",)),
        ),
        "observed_time_course": QuestionContract(
            7,
            (_EXPRESSION, ("sampled_time",)),
            align="observations",
            min_units_per_group=2,
            curated_operation="fit_temporal_trajectory",
        ),
        "donor_trajectory": QuestionContract(
            7,
            (("normalized_expression",), ("inferred_pseudotime",)),
            assays=_CELLS,
            align="observations",
            min_units_per_group=3,
            curated_operation="fit_donor_trajectory",
        ),
        "atlas_integration": QuestionContract(
            8,
            (("normalized_expression",), ("batch_membership",)),
            assays=_CELLS,
            align="observations",
            curated_operation="scrna_integrate",
            curated_alternatives=("scrna_integrate_qualified",),
        ),
        "cross_species_comparison": QuestionContract(
            8, (_EXPRESSION, ("ortholog_map",))
        ),
        "spatial_pattern": QuestionContract(
            9,
            (_EXPRESSION, ("spatial_coordinates",)),
            assays=_SPATIAL,
            align="observations",
        ),
        "spatial_domains": QuestionContract(
            9,
            (_EXPRESSION, ("spatial_coordinates",), ("spatial_segmentation",)),
            assays=_SPATIAL,
            align="observations",
        ),
        "cell_communication": QuestionContract(
            10,
            (("cell_type_expression",), ("ligand_receptor_reference",)),
            assays=(*_CELLS, *_SPATIAL),
            curated_operation="scrna_ccc",
        ),
        "perturbation_response": QuestionContract(
            11,
            (_EXPRESSION, ("perturbation_assignments",), ("control_assignments",)),
            align="observations",
            min_units_per_group=2,
        ),
        "allele_specific_expression": QuestionContract(
            12,
            (("allele_counts",), ("genotypes",)),
            align="both",
            min_units_per_group=2,
            reference_required=True,
        ),
        "expression_qtl": QuestionContract(
            12,
            (("normalized_expression",), ("genotypes",)),
            align="observations",
            min_units_per_group=3,
            reference_required=True,
        ),
        "fusion_evidence": QuestionContract(
            13,
            (("fusion_junction_evidence",),),
            reference_required=True,
        ),
        "rna_editing_evidence": QuestionContract(
            13,
            (("editing_site_counts",), ("genotypes",)),
            align="both",
            reference_required=True,
        ),
        "small_rna_expression": QuestionContract(
            14,
            (("small_rna_counts",),),
            assays=("small_rna_seq",),
            reference_required=True,
            min_units_per_group=2,
        ),
        "circular_rna_usage": QuestionContract(
            14,
            (("backsplice_counts",),),
            reference_required=True,
            min_units_per_group=2,
        ),
        "repeat_rna_expression": QuestionContract(
            14,
            (("repeat_counts",),),
            reference_required=True,
            min_units_per_group=2,
        ),
        "rna_velocity": QuestionContract(
            15,
            (("spliced_counts",), ("unspliced_counts",)),
            assays=(*_CELLS, *_SPATIAL),
            align="both",
        ),
        "rna_turnover": QuestionContract(
            15,
            (_EXPRESSION, ("nascent_rna_measurements",), ("turnover_time_points",)),
            align="observations",
            min_units_per_group=2,
        ),
        "rna_modification": QuestionContract(
            15,
            (("modification_signal",),),
            reference_required=True,
        ),
        "translation_efficiency": QuestionContract(
            15,
            (_EXPRESSION, ("ribosome_footprint_counts",)),
            align="both",
            min_units_per_group=2,
            reference_required=True,
        ),
        "multi_organism_inventory": QuestionContract(
            16,
            (("organism_resolved_summary",),),
            intake_only=True,
        ),
        "paired_modalities": QuestionContract(
            17,
            (
                _EXPRESSION,
                (
                    "chromatin_accessibility",
                    "protein_measurements",
                    "imaging_measurements",
                ),
                ("paired_modality_map",),
            ),
            align="observations",
        ),
        "clonotype_association": QuestionContract(
            17,
            (_EXPRESSION, ("clonotype_assignments",)),
            align="observations",
            min_units_per_group=2,
        ),
        "lineage_concordance": QuestionContract(
            17,
            (("inferred_pseudotime",), ("lineage_assignments",)),
            align="observations",
        ),
        "phenotype_prediction": QuestionContract(
            18,
            (
                _EXPRESSION,
                ("phenotype_outcomes",),
                ("independent_evaluation_partition",),
            ),
            align="observations",
            min_units_per_group=2,
        ),
        "published_reproduction": QuestionContract(
            19,
            (_EXPRESSION, ("published_analysis_protocol",), ("prior_study_results",)),
        ),
        "study_replication": QuestionContract(
            19,
            (("ranked_gene_statistics",), ("prior_study_results",)),
        ),
        "method_benchmark": QuestionContract(
            20,
            (
                ("benchmark_data",),
                ("baseline_specification",),
                ("independent_evaluation_partition",),
            ),
        ),
    }
)


class _Record(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        allow_inf_nan=False,
    )


class ReferencePin(_Record):
    organism: Text
    genome_build: Text
    annotation_source: Text
    annotation_release: Text
    reference_sha256: EvidenceDigest | None = None

    @field_validator("genome_build", "annotation_release")
    @classmethod
    def require_fixed_version(cls, value: str) -> str:
        if value.casefold() in {"latest", "current", "unknown", "unspecified"}:
            raise ValueError("Reference genome and annotation require fixed versions")
        return value


class DatasetAccessPin(_Record):
    """Retain provenance for a supplied dataset record without network lookup."""

    archive: Literal["GEO", "SRA", "ArrayExpress", "CELLxGENE", "dbGaP", "EGA"]
    accession: Identifier
    accessed_at: Annotated[AwareDatetime, Field(strict=False)]
    access_record_sha256: EvidenceDigest
    authorization_sha256: EvidenceDigest | None = None

    @field_validator("accessed_at", mode="before")
    @classmethod
    def require_timestamp(cls, value: object) -> object:
        if isinstance(value, int | float):
            raise ValueError("An explicit timezone-aware access timestamp is required")
        return value


class AttestedMeasurement(_Record):
    """Evidence about one specific measured object, such as an AnnData layer."""

    kind: Measurement
    artifact_sha256: EvidenceDigest
    receipt_sha256: EvidenceDigest
    slot: Text | None = None
    observation_axis_sha256: EvidenceDigest | None = None
    feature_axis_sha256: EvidenceDigest | None = None
    reference: ReferencePin | None = None
    access: DatasetAccessPin | None = None
    coordinate_frame: Text | None = None
    coordinate_units: Text | None = None

    @model_validator(mode="after")
    def physical_coordinates(self) -> AttestedMeasurement:
        if self.kind == "spatial_coordinates" and (
            self.coordinate_frame is None or self.coordinate_units is None
        ):
            raise ValueError("Physical coordinates require a named frame and units")
        return self


class DesignEvidence(_Record):
    """Bound results of an independent unit and design preflight."""

    biological_unit: Text
    receipt_sha256: EvidenceDigest
    observation_axis_sha256: EvidenceDigest
    estimability: Literal["estimable", "confounded", "rank_deficient", "unknown"]
    independent_units_per_group: tuple[
        Annotated[int, Field(ge=0, le=2_000_000)], ...
    ] = Field(max_length=128)


class RuntimeEvidence(_Record):
    """Coordinator-owned proof of an executable profile for one question."""

    question: Identifier
    tier: Literal["tier_a", "tier_b"]
    operation_id: Identifier
    runtime_sha256: EvidenceDigest
    method_contract_sha256: EvidenceDigest | None = None

    @model_validator(mode="after")
    def bind_method(self) -> RuntimeEvidence:
        if self.tier == "tier_b" and self.method_contract_sha256 is None:
            raise ValueError("A Tier B runtime must bind its MethodContract")
        return self


class AnswerabilityRequest(_Record):
    family_id: int = Field(ge=1)
    question: Identifier
    assay: Assay
    entry_point: EntryPoint
    requires_read_archive_download: bool = False


class AnswerabilityDecision(_Record):
    """Prerequisite assessment with a ceiling on any resulting claim."""

    request: AnswerabilityRequest
    status: Status
    reason: str
    inventory_sha256: EvidenceDigest
    missing_measurements: RequiredMeasurements = ()
    evidence_sha256: tuple[EvidenceDigest, ...] = ()
    design_receipt_sha256: EvidenceDigest | None = None
    reference_pins: tuple[ReferencePin, ...] = ()
    access_pins: tuple[DatasetAccessPin, ...] = ()
    execution_route: Literal["tier_a", "tier_b", "intake"] | None = None
    runtime: RuntimeEvidence | None = None
    claim_scope: (
        Literal[
            "requires_scientific_review",
            "exploratory_only",
            "checked_observation",
        ]
        | None
    ) = None
    clinical_release_allowed: Literal[False] = False


def assess_answerability(
    request: AnswerabilityRequest,
    *,
    measurements: tuple[AttestedMeasurement, ...],
    design: DesignEvidence | None = None,
    runtime: RuntimeEvidence | None = None,
) -> AnswerabilityDecision:
    """Check scientific inputs before considering an available runtime."""
    if len(measurements) > 128:
        raise ValueError("Answerability accepts at most 128 attested measurements")
    result = AnswerabilityDecision(
        request=request,
        status="refused",
        reason="unregistered_question",
        inventory_sha256=canonical_sha256(
            [item.model_dump(mode="json") for item in measurements]
        ),
        evidence_sha256=tuple(sorted({item.receipt_sha256 for item in measurements})),
        design_receipt_sha256=design.receipt_sha256 if design else None,
        reference_pins=_references(measurements),
        access_pins=tuple(
            dict.fromkeys(item.access for item in measurements if item.access)
        ),
    )
    if request.requires_read_archive_download:
        return result.model_copy(update={"reason": "read_archive_download"})
    contract = RNA_QUESTIONS.get(request.question)
    if contract is None or contract.family_id != request.family_id:
        return result
    if contract.assays and request.assay not in contract.assays:
        return result.model_copy(update={"reason": "assay_incompatible"})
    if (
        request.question == "compositional_abundance"
        and runtime is not None
        and runtime.question == request.question
        and runtime.tier == "tier_a"
        and runtime.operation_id == "scrna_neighborhood_da"
    ):
        # The solver counts donor membership in KNN neighborhoods from this axis.
        contract = replace(contract, measurements=(("latent_embedding",),))
    selected, issue = _select_measurements(request, contract, measurements)
    if issue is not None:
        reason, missing = issue
        return result.model_copy(
            update={
                "status": "awaiting_information",
                "reason": reason,
                "missing_measurements": missing,
            }
        )
    scientific = _measurement_issue(contract, selected)
    if scientific is None:
        scientific = _design_issue(contract, selected, design)
    if scientific is not None:
        status, reason = scientific
        return result.model_copy(update={"status": status, "reason": reason})
    return _select_route(result, contract, runtime)


def _select_measurements(
    request: AnswerabilityRequest,
    contract: QuestionContract,
    measurements: tuple[AttestedMeasurement, ...],
) -> tuple[tuple[AttestedMeasurement, ...], tuple[str, RequiredMeasurements] | None]:
    requirements = contract.measurements
    # A tximport offset belongs to a selected estimated-count representation.
    if request.question == "differential_expression" and [
        item.kind for item in measurements if item.kind in _EXPRESSION
    ] == ["estimated_gene_counts"]:
        requirements += (("effective_lengths",),)
    candidates = tuple(
        tuple(item for item in measurements if item.kind in alternatives)
        for alternatives in requirements
    )
    missing = tuple(
        requirement
        for requirement, options in zip(requirements, candidates, strict=True)
        if not options
    )
    if missing:
        return (), ("missing_measurements", missing)
    primary_measurements = RNA_ENTRY_POINTS[request.entry_point].present_any_of
    if not any(item.kind in primary_measurements for item in measurements):
        return (), ("entry_point_evidence_required", (primary_measurements,))
    if any(len(options) != 1 for options in candidates):
        return (), ("ambiguous_measurement_selection", ())
    return tuple(options[0] for options in candidates), None


def _references(items: tuple[AttestedMeasurement, ...]) -> tuple[ReferencePin, ...]:
    return tuple(dict.fromkeys(item.reference for item in items if item.reference))


def _reference_conflict(references: tuple[ReferencePin, ...]) -> bool:
    versions = {
        (pin.organism, pin.genome_build, pin.annotation_source, pin.annotation_release)
        for pin in references
    }
    fingerprints = {pin.reference_sha256 for pin in references if pin.reference_sha256}
    return len(versions) > 1 or len(fingerprints) > 1


def _measurement_issue(
    contract: QuestionContract,
    selected: tuple[AttestedMeasurement, ...],
) -> tuple[Status, str] | None:
    if len({(item.artifact_sha256, item.slot) for item in selected}) != len(selected):
        return "refused", "duplicated_measurement_object"
    if any(
        item.access is not None
        and item.access.archive in {"dbGaP", "EGA"}
        and item.access.authorization_sha256 is None
        for item in selected
    ):
        return "refused", "authorized_local_access_required"
    align = "both" if selected[0].kind == "estimated_gene_counts" else contract.align
    axis_problem = _axis_issue(align, selected)
    if axis_problem is not None:
        return axis_problem
    references = _references(selected)
    if contract.reference_required and any(item.reference is None for item in selected):
        return "awaiting_information", "reference_pin_required"
    if (align != "none" or contract.reference_required) and _reference_conflict(
        references
    ):
        return "refused", "reference_pins_conflict"
    return None


def _axis_issue(
    align: AxisBinding,
    selected: tuple[AttestedMeasurement, ...],
) -> tuple[Status, str] | None:
    axes = []
    if align in {"both", "observations"}:
        axes.append({item.observation_axis_sha256 for item in selected})
    if align in {"both", "features"}:
        axes.append({item.feature_axis_sha256 for item in selected})
    if any(None in axis for axis in axes):
        return "awaiting_information", "measurement_axes_unverified"
    if any(len(axis) != 1 for axis in axes):
        return "refused", "measurement_axes_incompatible"
    return None


def _design_issue(
    contract: QuestionContract,
    selected: tuple[AttestedMeasurement, ...],
    design: DesignEvidence | None,
) -> tuple[Status, str] | None:
    floor = contract.min_units_per_group
    if not floor:
        return None
    if design is None or design.estimability == "unknown":
        return "awaiting_information", "design_evidence_required"
    if design.estimability != "estimable":
        return "refused", f"design_{design.estimability}"
    if (
        len(design.independent_units_per_group) < 2
        or min(design.independent_units_per_group) < floor
    ):
        return "refused", "insufficient_replication"
    expression_axis = selected[0].observation_axis_sha256
    if expression_axis is None:
        return "awaiting_information", "measurement_axes_unverified"
    if design.observation_axis_sha256 != expression_axis:
        return "refused", "design_axis_incompatible"
    return None


def _select_route(
    result: AnswerabilityDecision,
    contract: QuestionContract,
    runtime: RuntimeEvidence | None,
) -> AnswerabilityDecision:
    if contract.intake_only:
        return result.model_copy(
            update={
                "status": "ready",
                "reason": "intake_scope",
                "execution_route": "intake",
                "claim_scope": "checked_observation",
            }
        )
    if runtime and runtime.question == result.request.question:
        if runtime.tier == "tier_b":
            return result.model_copy(
                update={
                    "status": "ready",
                    "reason": "prerequisites_satisfied",
                    "execution_route": "tier_b",
                    "claim_scope": "exploratory_only",
                    "runtime": runtime,
                }
            )
        if runtime.operation_id in (
            contract.curated_operation, *contract.curated_alternatives
        ):
            return result.model_copy(
                update={
                    "status": "ready",
                    "reason": "prerequisites_satisfied",
                    "execution_route": "tier_a",
                    "claim_scope": "requires_scientific_review",
                    "runtime": runtime,
                }
            )
    return result.model_copy(
        update={
            "status": "unavailable",
            "reason": "runtime_unavailable",
        }
    )
