"""Evidence gate for post-clustering markers and their downstream ORA use."""

from collections.abc import Callable, Sequence
from math import isclose
from typing import Annotated, Literal

from pydantic import Field, model_validator

from devbench.core.publication_families import budgeted_bh

from .scientific_types import (
    FrozenContract,
    Identifier,
    Probability,
    Sha256,
    content_sha256,
)

MarkerMethod = Literal[
    "unsplit", "poisson_thinning", "nb_countsplit", "clusterde", "cadet"
]
MarkerScope = Literal["exploratory_marker_ranking", "confirmatory_marker_eligible"]
_MAX_FEATURES = 100_000
_EXPLORATORY: MarkerScope = "exploratory_marker_ranking"
_CONFIRMATORY: MarkerScope = "confirmatory_marker_eligible"


class MarkerContext(FrozenContract):
    """Ordered cell/feature axes and the exact inputs to the tested contrast."""

    counts_sha256: Sha256
    cluster_labels_sha256: Sha256
    cell_ids_sha256: Sha256
    feature_ids_sha256: Sha256
    design_sha256: Sha256
    contrast_id: Identifier


class MarkerRow(FrozenContract):
    gene_id: Identifier
    score: float | None
    p_value: Probability | None = None
    q_value: Probability | None = None
    test_status: Literal[
        "ranked",
        "tested",
        "not_estimable",
        "independently_filtered",
    ] = "ranked"
    selected: bool = False


class DispersionProvenance(FrozenContract):
    origin: Literal["independent_cohort", "known", "in_sample"]
    prior_free: bool
    values_sha256: Sha256
    source_sha256: Sha256
    feature_ids_sha256: Sha256


class _MarkerProof(FrozenContract):
    context: MarkerContext
    rows_sha256: Sha256
    execution_receipt_sha256: Sha256
    independent_check_sha256: Sha256
    runtime_sha256: Sha256
    method_version: Identifier
    fdr_threshold: Annotated[float, Field(gt=0, lt=1)]


class NegativeBinomialSplitProof(_MarkerProof):
    method: Literal["nb_countsplit"] = "nb_countsplit"
    split_distribution: Literal["dirichlet_multinomial", "poisson_binomial"]
    splitting_receipt_sha256: Sha256
    clustering_receipt_sha256: Sha256
    clustering_counts_sha256: Sha256
    inference_counts_sha256: Sha256
    dispersion: DispersionProvenance


class ClusterDEProof(_MarkerProof):
    method: Literal["clusterde"] = "clusterde"
    unimodal: bool
    input_population: Literal["single_batch", "harmonized", "mixed_unassessed"]
    null_model_sha256: Sha256
    null_diagnostic_sha256: Sha256


class CADETProof(_MarkerProof):
    method: Literal["cadet"] = "cadet"
    selection_event_sha256: Sha256
    gaussian_reference_sha256: Sha256
    target_feature: Identifier
    clustering: Literal["two_cluster_lloyd"] = "two_cluster_lloyd"


MarkerProof = Annotated[
    NegativeBinomialSplitProof | ClusterDEProof | CADETProof,
    Field(discriminator="method"),
]
MarkerCustodyVerifier = Callable[[MarkerProof], bool]


class MarkerResult(FrozenContract):
    format: Literal["devbench.post-clustering-markers/1"] = (
        "devbench.post-clustering-markers/1"
    )
    context: MarkerContext
    method: MarkerMethod
    marker_scope: MarkerScope
    reason: Identifier
    gene_statistics: Annotated[
        tuple[MarkerRow, ...],
        Field(min_length=1, max_length=_MAX_FEATURES),
    ]
    proof: MarkerProof | None = None

    @model_validator(mode="after")
    def respect_scope(self):
        if self.marker_scope == _CONFIRMATORY and self.proof is None:
            raise ValueError("Confirmatory eligibility requires a marker proof")
        if self.marker_scope == _EXPLORATORY and (
            self.proof is not None
            or any(
                row.p_value is not None
                or row.q_value is not None
                or row.selected
                or row.test_status == "tested"
                for row in self.gene_statistics
            )
        ):
            raise ValueError("Exploratory ranks cannot expose inferential statistics")
        return self


def marker_rows_sha256(rows: Sequence[MarkerRow]) -> str:
    """Bind complete, ordered marker output including unavailable hypotheses."""
    return content_sha256([row.model_dump(mode="json") for row in rows])


def _validate_axis(rows: Sequence[MarkerRow], context: MarkerContext) -> None:
    if not 1 <= len(rows) <= _MAX_FEATURES:
        raise ValueError("Marker family exceeds the feature bound or is empty")
    genes = tuple(row.gene_id for row in rows)
    if (
        len(set(genes)) != len(genes)
        or content_sha256(genes) != context.feature_ids_sha256
    ):
        raise ValueError("Marker rows must cover the exact ordered feature axis")


def _countsplit_basis_error(proof: NegativeBinomialSplitProof) -> str | None:
    if proof.split_distribution != "dirichlet_multinomial":
        return "nb_dirichlet_multinomial_split_required"
    matrix_ids = {
        proof.context.counts_sha256,
        proof.clustering_counts_sha256,
        proof.inference_counts_sha256,
    }
    if (
        len(matrix_ids) != 3
        or len(
            {
                proof.splitting_receipt_sha256,
                proof.clustering_receipt_sha256,
                proof.execution_receipt_sha256,
                proof.independent_check_sha256,
            }
        )
        != 4
    ):
        return "separate_countsplit_folds_and_receipts_required"
    dispersion = proof.dispersion
    if (
        dispersion.origin == "in_sample"
        or not dispersion.prior_free
        or dispersion.source_sha256 in matrix_ids
        or dispersion.feature_ids_sha256 != proof.context.feature_ids_sha256
    ):
        return "independent_prior_free_dispersion_required"
    return None


def _clusterde_basis_error(proof: ClusterDEProof) -> str | None:
    if (
        not proof.unimodal
        or proof.input_population == "mixed_unassessed"
        or proof.null_model_sha256 == proof.context.counts_sha256
        or proof.null_diagnostic_sha256 == proof.execution_receipt_sha256
    ):
        return "verified_unimodal_clusterde_null_required"
    return None


def _control_error(
    method: MarkerMethod,
    context: MarkerContext,
    rows: Sequence[MarkerRow],
    proof: MarkerProof | None,
    verify_custody: MarkerCustodyVerifier | None,
) -> str | None:
    if method == "unsplit":
        return "unsplit_post_clustering_expression"
    if method == "poisson_thinning":
        return "poisson_split_does_not_address_nb_overdispersion"
    if proof is None or proof.method != method:
        return "marker_control_evidence_unavailable"
    if (
        proof.context != context
        or proof.rows_sha256 != marker_rows_sha256(rows)
        or proof.execution_receipt_sha256 == proof.independent_check_sha256
    ):
        return "marker_execution_or_axes_not_bound"
    basis_error = (
        _countsplit_basis_error(proof)
        if isinstance(proof, NegativeBinomialSplitProof)
        else _clusterde_basis_error(proof)
        if isinstance(proof, ClusterDEProof)
        else None
    )
    if basis_error is not None:
        return basis_error
    if not callable(verify_custody) or verify_custody(proof) is not True:
        return "marker_control_custody_unverified"
    if not any(row.test_status == "tested" for row in rows):
        return "no_tested_marker_hypotheses"
    return None


def _validate_test_rows(rows: Sequence[MarkerRow], proof: MarkerProof) -> None:
    for row in rows:
        tested = row.test_status == "tested"
        if row.test_status == "ranked" or tested and row.score is None:
            raise ValueError("Verified marker tests require exact numerical row status")
        if row.selected and not tested:
            raise ValueError("Unavailable marker hypotheses cannot be selected")
    if isinstance(proof, ClusterDEProof):
        if any(row.p_value is not None or row.q_value is not None for row in rows):
            raise ValueError("ClusterDE selection has no calibrated BH p/q pair")
        return
    if isinstance(proof, CADETProof):
        _validate_cadet_target(rows, proof)
    _validate_bh(rows, proof.fdr_threshold)


def _validate_cadet_target(rows: Sequence[MarkerRow], proof: CADETProof) -> None:
    targets = [row for row in rows if row.test_status == "tested"]
    if len(targets) != 1 or targets[0].gene_id != proof.target_feature:
        raise ValueError("CADET requires one prespecified target feature")
    target = targets[0]
    if (
        target.p_value is None
        or target.q_value != target.p_value
        or target.selected != (target.p_value <= proof.fdr_threshold)
    ):
        raise ValueError(
            "CADET target selection differs from its single-test BH family"
        )
    if any(
        row.test_status != "independently_filtered" or row.score is not None
        for row in rows
        if row.gene_id != proof.target_feature
    ):
        raise ValueError("CADET non-target features must remain untested")


def _validate_bh(rows: Sequence[MarkerRow], fdr_threshold: float) -> None:
    for row in rows:
        if row.test_status != "tested":
            if row.p_value is not None or row.q_value is not None:
                raise ValueError("Unavailable marker probabilities must remain null")
            continue
        if row.p_value is None or row.q_value is None or row.q_value < row.p_value:
            raise ValueError("Tested markers require 0 <= p <= q and q >= p")
        if row.selected and row.q_value > fdr_threshold:
            raise ValueError("Selected marker exceeds its frozen FDR threshold")
    family = [row for row in rows if row.test_status != "independently_filtered"]
    expected = budgeted_bh([row.p_value for row in family], "1", pad_failed=True)
    for row, item in zip(family, expected, strict=True):
        if row.q_value is not None and not isclose(
            row.q_value,
            item["q_local"],
            rel_tol=1e-10,
            abs_tol=0.0,
        ):
            raise ValueError("Marker q-values must match the complete BH family")


def project_post_clustering_markers(
    rows: Sequence[MarkerRow],
    *,
    context: MarkerContext,
    method: MarkerMethod,
    proof: MarkerProof | None = None,
    verify_custody: MarkerCustodyVerifier | None = None,
) -> MarkerResult:
    """Shape actual results under a coordinator-owned verification capability.

    The trusted verifier must resolve the exact versioned execution and separate
    scientific check, including artifact bytes, axes, unit design and exposure.
    For count splitting it must also verify split/label ancestry and independent
    dispersion. For ClusterDE it must verify the unimodal null and selection
    procedure. Agent-supplied JSON cannot serve as that capability.
    """
    retained = tuple(rows)
    _validate_axis(retained, context)
    error = _control_error(method, context, retained, proof, verify_custody)
    if error is not None:
        return MarkerResult(
            context=context,
            method=method,
            marker_scope=_EXPLORATORY,
            reason=error,
            gene_statistics=tuple(
                MarkerRow(gene_id=row.gene_id, score=row.score) for row in retained
            ),
        )
    assert proof is not None
    _validate_test_rows(retained, proof)
    return MarkerResult(
        context=context,
        method=method,
        marker_scope=_CONFIRMATORY,
        reason="verified_marker_control",
        gene_statistics=retained,
        proof=proof,
    )


def require_confirmatory_marker_ora(
    result: MarkerResult,
    *,
    foreground: Sequence[str],
    tested_background: Sequence[str],
    verify_custody: MarkerCustodyVerifier | None = None,
    direction: Literal["all", "up_only", "down_only"] = "all",
) -> None:
    """Revalidate custody and exact tested universe at the ORA dispatch boundary."""
    from devbench.core.rna_pathway_analysis import matches_foreground_direction

    if type(result) is not MarkerResult or result.marker_scope != _CONFIRMATORY:
        raise PermissionError(
            "exploratory_marker_ranking cannot supply confirmatory ORA"
        )
    checked = project_post_clustering_markers(
        result.gene_statistics,
        context=result.context,
        method=result.method,
        proof=result.proof,
        verify_custody=verify_custody,
    )
    if checked.marker_scope != _CONFIRMATORY:
        raise PermissionError(checked.reason)
    tested = {
        row.gene_id for row in checked.gene_statistics if row.test_status == "tested"
    }
    selected = {
        row.gene_id
        for row in checked.gene_statistics
        if row.selected and matches_foreground_direction(row.score, direction)
    }
    background = set(tested_background)
    if isinstance(tested_background, str) or (
        background != tested or len(background) != len(tested_background)
    ):
        raise PermissionError("ORA requires the exact filtered_tested_background")
    genes = set(foreground)
    if isinstance(foreground, str) or (
        not genes or genes - selected or len(genes) != len(foreground)
    ):
        raise PermissionError("ORA foreground requires a verified_marker_selection")
