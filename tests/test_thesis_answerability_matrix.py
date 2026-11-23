"""Frozen positive and missing-measurement probes for the 20 thesis families.

Fixtures represent coordinator attestations. Tier B readiness measures the
contract boundary with an attested extension, independent of package coverage.
"""

from dataclasses import dataclass
from hashlib import sha256

import pytest

from devbench.core.rna_answerability import (
    AnswerabilityRequest,
    Assay,
    AttestedMeasurement,
    DesignEvidence,
    EntryPoint,
    Measurement,
    ReferencePin,
    RequiredMeasurements,
    RuntimeEvidence,
    assess_answerability,
)


@dataclass(frozen=True)
class _Probe:
    family: int
    question: str
    assay: Assay
    entry: EntryPoint
    complete: tuple[Measurement, ...]
    remove: Measurement
    missing: RequiredMeasurements
    curated_operation: str | None = None
    intake_only: bool = False


_GENE_EXPRESSION = ("gene_counts", "normalized_expression", "estimated_gene_counts")
_PROBES = (
    _Probe(
        1,
        "differential_cell_state",
        "single_nucleus_rna_seq",
        "count_matrix",
        ("gene_counts", "donor_pseudobulk_counts"),
        "donor_pseudobulk_counts",
        (("donor_pseudobulk_counts",),),
        "bulk_condition_contrast",
    ),
    _Probe(
        2,
        "cell_identity",
        "single_nucleus_rna_seq",
        "annotated_object",
        ("latent_embedding", "normalized_expression"),
        "normalized_expression",
        (_GENE_EXPRESSION,),
    ),
    _Probe(
        3,
        "compositional_abundance",
        "single_nucleus_rna_seq",
        "annotated_object",
        ("gene_counts", "cell_type_counts"),
        "cell_type_counts",
        (("cell_type_counts",),),
        "scrna_sccoda",
    ),
    _Probe(
        4,
        "transcript_usage",
        "bulk_rna_seq",
        "count_matrix",
        ("gene_counts", "transcript_counts"),
        "transcript_counts",
        (("transcript_counts",),),
        "bulk_dtu",
    ),
    _Probe(
        5,
        "novel_transcripts",
        "long_read_rna_seq",
        "alignments",
        ("read_alignments", "transcript_structure_alignments"),
        "transcript_structure_alignments",
        (("transcript_structure_reads", "transcript_structure_alignments"),),
    ),
    _Probe(
        6,
        "overrepresentation",
        "bulk_rna_seq",
        "prior_results",
        ("foreground_gene_list", "filtered_tested_background", "gene_sets"),
        "filtered_tested_background",
        (("filtered_tested_background",),),
        "rna_pathway_tested",
    ),
    _Probe(
        7,
        "observed_time_course",
        "single_nucleus_rna_seq",
        "annotated_object",
        ("gene_counts", "inferred_pseudotime", "sampled_time"),
        "sampled_time",
        (("sampled_time",),),
        "fit_temporal_trajectory",
    ),
    _Probe(
        8,
        "atlas_integration",
        "single_nucleus_rna_seq",
        "annotated_object",
        ("normalized_expression", "latent_embedding", "batch_membership"),
        "batch_membership",
        (("batch_membership",),),
        "scrna_integrate_qualified",
    ),
    _Probe(
        9,
        "spatial_domains",
        "spatial_transcriptomics",
        "annotated_object",
        ("gene_counts", "spatial_coordinates", "spatial_segmentation"),
        "spatial_coordinates",
        (("spatial_coordinates",),),
    ),
    _Probe(
        10,
        "cell_communication",
        "single_nucleus_rna_seq",
        "normalized_expression",
        ("cell_type_expression", "ligand_receptor_reference"),
        "ligand_receptor_reference",
        (("ligand_receptor_reference",),),
    ),
    _Probe(
        11,
        "perturbation_response",
        "single_cell_rna_seq",
        "annotated_object",
        ("gene_counts", "perturbation_assignments", "control_assignments"),
        "control_assignments",
        (("control_assignments",),),
    ),
    _Probe(
        12,
        "allele_specific_expression",
        "bulk_rna_seq",
        "count_matrix",
        ("allele_counts", "genotypes"),
        "genotypes",
        (("genotypes",),),
    ),
    _Probe(
        13,
        "fusion_evidence",
        "bulk_rna_seq",
        "alignments",
        ("read_alignments", "fusion_junction_evidence"),
        "fusion_junction_evidence",
        (("fusion_junction_evidence",),),
    ),
    _Probe(
        14,
        "circular_rna_usage",
        "bulk_rna_seq",
        "count_matrix",
        ("gene_counts", "backsplice_counts"),
        "backsplice_counts",
        (("backsplice_counts",),),
    ),
    _Probe(
        15,
        "rna_velocity",
        "single_cell_rna_seq",
        "annotated_object",
        ("spliced_counts", "unspliced_counts"),
        "unspliced_counts",
        (("unspliced_counts",),),
    ),
    _Probe(
        16,
        "multi_organism_inventory",
        "bulk_rna_seq",
        "prior_results",
        ("prior_study_results", "organism_resolved_summary"),
        "organism_resolved_summary",
        (("organism_resolved_summary",),),
        intake_only=True,
    ),
    _Probe(
        17,
        "paired_modalities",
        "linked_modalities",
        "annotated_object",
        ("gene_counts", "protein_measurements", "paired_modality_map"),
        "paired_modality_map",
        (("paired_modality_map",),),
    ),
    _Probe(
        18,
        "phenotype_prediction",
        "bulk_rna_seq",
        "count_matrix",
        ("gene_counts", "phenotype_outcomes", "independent_evaluation_partition"),
        "independent_evaluation_partition",
        (("independent_evaluation_partition",),),
    ),
    _Probe(
        19,
        "published_reproduction",
        "bulk_rna_seq",
        "count_matrix",
        ("gene_counts", "published_analysis_protocol", "prior_study_results"),
        "published_analysis_protocol",
        (("published_analysis_protocol",),),
    ),
    _Probe(
        20,
        "method_benchmark",
        "other_expression",
        "prior_results",
        (
            "benchmark_data",
            "baseline_specification",
            "independent_evaluation_partition",
        ),
        "baseline_specification",
        (("baseline_specification",),),
    ),
)


def _digest(label: str) -> str:
    return sha256(label.encode("utf-8")).hexdigest()


def _attestation(kind: Measurement) -> AttestedMeasurement:
    coordinate_metadata = (
        {"coordinate_frame": "fixture-slide", "coordinate_units": "micrometres"}
        if kind == "spatial_coordinates"
        else {}
    )
    return AttestedMeasurement(
        kind=kind,
        artifact_sha256=_digest(kind + " artifact"),
        receipt_sha256=_digest(kind + " receipt"),
        observation_axis_sha256=_digest("fixture observations"),
        feature_axis_sha256=_digest("fixture features"),
        reference=ReferencePin(
            organism="fixture organism",
            genome_build="fixture-1",
            annotation_source="fixture reference",
            annotation_release="1",
        ),
        **coordinate_metadata,
    )


def test_answerability_probe_inventory_is_exact():
    assert tuple(probe.family for probe in _PROBES) == tuple(range(1, 21))


@pytest.mark.parametrize("probe", _PROBES, ids=lambda case: case.question)
def test_family_probe_enforces_decisive_measurements(probe):
    request = AnswerabilityRequest(
        family_id=probe.family,
        question=probe.question,
        assay=probe.assay,
        entry_point=probe.entry,
    )
    design = DesignEvidence(
        biological_unit="donor",
        receipt_sha256=_digest("fixture design"),
        observation_axis_sha256=_digest("fixture observations"),
        estimability="estimable",
        independent_units_per_group=(4, 4),
    )
    tier = "tier_a" if probe.curated_operation else "tier_b"
    runtime = RuntimeEvidence(
        question=probe.question,
        tier=tier,
        operation_id=probe.curated_operation or "fixture_extension",
        runtime_sha256=_digest("fixture runtime"),
        method_contract_sha256=_digest("fixture extension contract")
        if tier == "tier_b"
        else None,
    )
    complete = tuple(map(_attestation, probe.complete))
    ready = assess_answerability(
        request, measurements=complete, design=design, runtime=runtime
    )
    assert ready.status == "ready"
    assert ready.execution_route == ("intake" if probe.intake_only else tier)
    assert ready.claim_scope == (
        "checked_observation"
        if probe.intake_only
        else "requires_scientific_review"
        if tier == "tier_a"
        else "exploratory_only"
    )

    partial = tuple(item for item in complete if item.kind != probe.remove)
    blocked = assess_answerability(
        request, measurements=partial, design=design, runtime=runtime
    )
    assert blocked.status == "awaiting_information"
    assert blocked.reason == "missing_measurements"
    assert blocked.missing_measurements == probe.missing
    assert blocked.execution_route is None

    if not probe.intake_only:
        unavailable = assess_answerability(
            request, measurements=complete, design=design
        )
        assert unavailable.status == "unavailable"
        assert unavailable.reason == "runtime_unavailable"
    if probe.curated_operation:
        unrelated = runtime.model_copy(update={"operation_id": "scrna_prepare"})
        refused = assess_answerability(
            request,
            measurements=complete,
            design=design,
            runtime=unrelated,
        )
        assert refused.status == "unavailable"
        assert refused.reason == "runtime_unavailable"
