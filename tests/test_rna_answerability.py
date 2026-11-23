"""Scientific and execution admission for scoped RNA-seq questions."""

import pytest
from pydantic import ValidationError

from devbench.core.rna_answerability import (
    RNA_ENTRY_POINTS,
    RNA_FAMILIES,
    RNA_QUESTIONS,
    AnswerabilityRequest,
    AttestedMeasurement,
    DatasetAccessPin,
    DesignEvidence,
    ReferencePin,
    RuntimeEvidence,
    assess_answerability,
)

_COUNTS = "1" * 64
_AXIS = "2" * 64
_FEATURES = "3" * 64
_RECEIPT = "4" * 64
_RUNTIME = "5" * 64
_REFERENCE = ReferencePin(
    organism="Homo sapiens",
    genome_build="GRCh38",
    annotation_source="GENCODE",
    annotation_release="45",
)


def _measurement(kind, *, value=_COUNTS, **kwargs):
    return AttestedMeasurement(
        kind=kind,
        artifact_sha256=value,
        **{
            "receipt_sha256": _RECEIPT,
            "observation_axis_sha256": _AXIS,
            "feature_axis_sha256": _FEATURES,
            **kwargs,
        },
    )


def _request(family, question, *, entry_point="annotated_object", **kwargs):
    return AnswerabilityRequest(
        family_id=family,
        question=question,
        entry_point=entry_point,
        assay="single_nucleus_rna_seq",
        **kwargs,
    )


def _velocity_inputs():
    return (
        _measurement("spliced_counts", slot="layers/spliced"),
        _measurement("unspliced_counts", slot="layers/unspliced"),
    )


def _design(**kwargs):
    return DesignEvidence(
        **{
            "biological_unit": "donor",
            "observation_axis_sha256": _AXIS,
            "receipt_sha256": _RECEIPT,
            "estimability": "estimable",
            "independent_units_per_group": (4, 4),
            **kwargs,
        }
    )


def test_registry_covers_every_family_and_documents_entry_points():
    assert tuple(RNA_FAMILIES) == tuple(range(1, 21))
    assert {item.family_id for item in RNA_QUESTIONS.values()} == set(range(1, 21))
    assert set(RNA_ENTRY_POINTS) == {
        "reads",
        "alignments",
        "transcript_quantification",
        "count_matrix",
        "normalized_expression",
        "annotated_object",
        "prior_results",
    }
    assert RNA_FAMILIES[1].priorities["single_nucleus_rna_seq"] == 1
    assert RNA_FAMILIES[1].priorities["bulk_rna_seq"] == 2
    assert RNA_FAMILIES[15].priorities["single_nucleus_rna_seq"] == 3


def test_counts_only_cannot_answer_velocity_even_with_runtime():
    request = _request(15, "rna_velocity")
    runtime = RuntimeEvidence(
        question="rna_velocity",
        tier="tier_b",
        operation_id="velocity_extension",
        runtime_sha256=_RUNTIME,
        method_contract_sha256=_RECEIPT,
    )
    result = assess_answerability(
        request,
        measurements=(_measurement("gene_counts"),),
        runtime=runtime,
    )
    assert result.status == "awaiting_information"
    assert result.reason == "missing_measurements"
    assert result.missing_measurements == (("spliced_counts",), ("unspliced_counts",))
    assert result.execution_route is None


@pytest.mark.parametrize(
    "disagreement", ["observation_axis_sha256", "feature_axis_sha256"]
)
def test_velocity_requires_aligned_layers(disagreement):
    spliced, unspliced = _velocity_inputs()
    unspliced = unspliced.model_copy(update={disagreement: "a" * 64})
    result = assess_answerability(
        _request(15, "rna_velocity"),
        measurements=(spliced, unspliced),
    )
    assert result.status == "refused"
    assert result.reason == "measurement_axes_incompatible"


def test_one_matrix_cannot_claim_both_velocity_measurements():
    spliced, _ = _velocity_inputs()
    result = assess_answerability(
        _request(15, "rna_velocity"),
        measurements=(spliced, spliced.model_copy(update={"kind": "unspliced_counts"})),
    )
    assert result.status == "refused"
    assert result.reason == "duplicated_measurement_object"


def test_answerable_velocity_with_no_registered_backend_reports_unavailable():
    result = assess_answerability(
        _request(15, "rna_velocity"), measurements=_velocity_inputs()
    )
    assert result.status == "unavailable"
    assert result.reason == "runtime_unavailable"
    assert result.missing_measurements == ()
    assert result.evidence_sha256 == (_RECEIPT,)


def test_tier_b_velocity_is_exploratory_even_with_complete_measurements():
    runtime = RuntimeEvidence(
        question="rna_velocity",
        tier="tier_b",
        operation_id="velocity_extension",
        runtime_sha256=_RUNTIME,
        method_contract_sha256=_RECEIPT,
    )
    result = assess_answerability(
        _request(15, "rna_velocity"),
        measurements=_velocity_inputs(),
        runtime=runtime,
    )
    assert result.status == "ready"
    assert result.execution_route == "tier_b"
    assert result.claim_scope == "exploratory_only"
    assert result.clinical_release_allowed is False


@pytest.mark.parametrize(
    ("question", "inputs", "admitted", "unrelated"),
    [
        (
            "pathway_enrichment",
            ("ranked_gene_statistics", "gene_sets"),
            ("rna_pathway_tested", "rna_pathway_camera"),
            "rna_pathway_marker_ora",
        ),
        (
            "overrepresentation",
            ("foreground_gene_list", "filtered_tested_background", "gene_sets"),
            (
                "rna_pathway_tested",
                "rna_pathway_marker_ora",
                "rna_pathway_nb_marker_ora",
            ),
            "rna_pathway_camera",
        ),
    ],
)
def test_family_six_preserves_tier_b_and_requires_the_bound_curated_route(
    question, inputs, admitted, unrelated
):
    request = _request(6, question, entry_point="prior_results")
    measurements = tuple(
        _measurement(name, value=str(i + 1) * 64) for i, name in enumerate(inputs)
    )
    for operation in (*admitted, unrelated):
        result = assess_answerability(
            request,
            measurements=measurements,
            runtime=RuntimeEvidence(
                question=question,
                tier="tier_a",
                operation_id=operation,
                runtime_sha256=_RUNTIME,
            ),
        )
        if operation in admitted:
            assert result.execution_route == "tier_a"
            assert result.claim_scope == "requires_scientific_review"
        else:
            assert result.reason == "runtime_unavailable"
            assert result.execution_route is None

    extension = assess_answerability(
        request,
        measurements=measurements,
        runtime=RuntimeEvidence(
            question=question,
            tier="tier_b",
            operation_id="pathway_extension",
            runtime_sha256=_RUNTIME,
            method_contract_sha256=_RECEIPT,
        ),
    )
    assert extension.execution_route == "tier_b"
    assert extension.claim_scope == "exploratory_only"
    assert extension.clinical_release_allowed is False


def test_embedding_cannot_substitute_for_spatial_coordinates():
    request = AnswerabilityRequest(
        family_id=9,
        question="spatial_pattern",
        assay="spatial_transcriptomics",
        entry_point="annotated_object",
    )
    expression = _measurement("normalized_expression", slot="X")
    embedding = _measurement("latent_embedding", slot="obsm/X_umap")
    result = assess_answerability(request, measurements=(expression, embedding))
    assert result.status == "awaiting_information"
    assert result.missing_measurements == (("spatial_coordinates",),)
    with pytest.raises(ValidationError, match="Physical coordinates"):
        _measurement("spatial_coordinates", slot="obsm/spatial")


def test_ase_requires_exact_genotype_sample_and_variant_axes():
    request = _request(12, "allele_specific_expression")
    counts = _measurement("allele_counts", value="a" * 64, reference=_REFERENCE)
    absent = assess_answerability(request, measurements=(counts,))
    assert absent.missing_measurements == (("genotypes",),)
    genotypes = _measurement("genotypes", value="b" * 64, reference=_REFERENCE)
    mismatched = genotypes.model_copy(update={"observation_axis_sha256": "c" * 64})
    assert (
        assess_answerability(
            request,
            measurements=(counts, mismatched),
        ).reason
        == "measurement_axes_incompatible"
    )
    assert (
        assess_answerability(
            request,
            measurements=(counts, genotypes),
            design=_design(),
        ).reason
        == "runtime_unavailable"
    )


def test_reference_binding_and_access_are_retained():
    request = _request(15, "rna_velocity")
    spliced, unspliced = _velocity_inputs()
    access = DatasetAccessPin(
        archive="GEO",
        accession="GSE100000",
        accessed_at="2026-11-17T10:00:00Z",
        access_record_sha256=_RECEIPT,
    )
    spliced = spliced.model_copy(update={"reference": _REFERENCE, "access": access})
    incompatible = unspliced.model_copy(
        update={
            "reference": ReferencePin(
                organism="Homo sapiens",
                genome_build="GRCh37",
                annotation_source="GENCODE",
                annotation_release="19",
            )
        }
    )
    result = assess_answerability(request, measurements=(spliced, incompatible))
    assert result.status == "refused"
    assert result.reason == "reference_pins_conflict"
    assert result.access_pins == (access,)
    assert _REFERENCE in result.reference_pins
    with pytest.raises(ValidationError):
        DatasetAccessPin(
            archive="GEO",
            accession="GSE100000",
            accessed_at="2026-11-17T10:00:00",
            access_record_sha256=_RECEIPT,
        )


@pytest.mark.parametrize(
    ("design", "reason"),
    [
        (None, "design_evidence_required"),
        (_design(estimability="confounded"), "design_confounded"),
        (_design(independent_units_per_group=(1, 1000)), "insufficient_replication"),
        (_design(observation_axis_sha256="a" * 64), "design_axis_incompatible"),
    ],
)
def test_rank_and_replication_block_dispatch_independently_of_solver(design, reason):
    runtime = RuntimeEvidence(
        question="differential_expression",
        tier="tier_a",
        operation_id="bulk_condition_contrast",
        runtime_sha256=_RUNTIME,
    )
    request = AnswerabilityRequest(
        family_id=1,
        question="differential_expression",
        assay="bulk_rna_seq",
        entry_point="count_matrix",
    )
    result = assess_answerability(
        request,
        measurements=(_measurement("gene_counts"),),
        design=design,
        runtime=runtime,
    )
    assert result.reason == reason
    assert result.execution_route is None


def test_curated_route_keeps_scientific_review_gate():
    request = AnswerabilityRequest(
        family_id=1,
        question="differential_expression",
        assay="bulk_rna_seq",
        entry_point="count_matrix",
    )
    runtime = RuntimeEvidence(
        question="differential_expression",
        tier="tier_a",
        operation_id="bulk_condition_contrast",
        runtime_sha256=_RUNTIME,
    )
    result = assess_answerability(
        request,
        measurements=(_measurement("gene_counts"),),
        design=_design(),
        runtime=runtime,
    )
    assert (result.status, result.execution_route) == ("ready", "tier_a")
    assert result.claim_scope == "requires_scientific_review"
    assert result.design_receipt_sha256 == _RECEIPT


def test_raw_read_archive_download_is_refused_even_with_local_metadata():
    request = _request(
        5,
        "novel_transcripts",
        entry_point="reads",
        requires_read_archive_download=True,
    )
    assert (
        assess_answerability(request, measurements=()).reason == "read_archive_download"
    )


def test_local_reads_do_not_count_as_ready_expression():
    request = _request(1, "differential_expression", entry_point="reads")
    result = assess_answerability(
        request, measurements=(_measurement("sequencing_reads"),)
    )
    assert result.status == "awaiting_information"
    assert ("gene_counts", "normalized_expression", "estimated_gene_counts") in (
        result.missing_measurements
    )


def test_declared_entry_point_requires_its_own_evidence():
    result = assess_answerability(
        _request(15, "rna_velocity", entry_point="prior_results"),
        measurements=_velocity_inputs(),
    )
    assert result.status == "awaiting_information"
    assert result.reason == "entry_point_evidence_required"
    assert "ranked_gene_statistics" in result.missing_measurements[0]


def test_estimated_gene_counts_require_matched_effective_lengths():
    request = _request(1, "differential_expression", entry_point="count_matrix")
    measured_counts = _measurement("estimated_gene_counts")
    assert assess_answerability(
        request,
        measurements=(measured_counts,),
        design=_design(),
    ).missing_measurements == (("effective_lengths",),)
    lengths = _measurement("effective_lengths", value="a" * 64)
    result = assess_answerability(
        request,
        measurements=(measured_counts, lengths),
        design=_design(),
    )
    assert result.status == "unavailable"
    unaligned_lengths = lengths.model_copy(update={"feature_axis_sha256": "b" * 64})
    result = assess_answerability(
        request,
        measurements=(measured_counts, unaligned_lengths),
        design=_design(),
    )
    assert result.reason == "measurement_axes_incompatible"


def test_mixed_expression_inputs_require_selection_before_effective_lengths():
    decision = assess_answerability(
        _request(1, "differential_expression", entry_point="count_matrix"),
        measurements=(
            _measurement("gene_counts"),
            _measurement("estimated_gene_counts", value="6" * 64),
        ),
        design=_design(),
    )
    assert decision.status == "awaiting_information"
    assert decision.reason == "ambiguous_measurement_selection"
    assert decision.missing_measurements == ()


def test_late_measurements_are_considered():
    inputs = tuple(
        _measurement("latent_embedding", value=f"{number:064x}") for number in range(20)
    )
    result = assess_answerability(
        _request(15, "rna_velocity"),
        measurements=(*inputs, *_velocity_inputs()),
    )
    assert result.status == "unavailable"
    assert result.missing_measurements == ()


def test_controlled_access_requires_a_separate_local_authorization_receipt():
    access = DatasetAccessPin(
        archive="dbGaP",
        accession="phs001000.v1.p1",
        accessed_at="2026-11-17T10:00:00Z",
        access_record_sha256=_RECEIPT,
    )
    result = assess_answerability(
        _request(2, "cell_identity"),
        measurements=(_measurement("gene_counts", access=access),),
    )
    assert result.reason == "authorized_local_access_required"


def test_family_16_only_has_descriptive_inventory_route():
    request = _request(16, "multi_organism_inventory")
    runtime = RuntimeEvidence(
        question=request.question,
        tier="tier_b",
        operation_id="external",
        runtime_sha256=_RUNTIME,
        method_contract_sha256=_RECEIPT,
    )
    result = assess_answerability(
        request,
        measurements=(_measurement("organism_resolved_summary"),),
        runtime=runtime,
    )
    assert result.execution_route == "intake"
    assert result.claim_scope == "checked_observation"
    assert (
        assess_answerability(
            _request(16, "other_action"),
            measurements=(),
            runtime=runtime,
        ).status
        == "refused"
    )


def test_unknown_question_and_cross_family_substitution_fail_closed():
    for request in (_request(120, "rna_velocity"), _request(4, "rna_velocity")):
        result = assess_answerability(request, measurements=_velocity_inputs())
        assert result.status == "refused"
        assert result.reason == "unregistered_question"
