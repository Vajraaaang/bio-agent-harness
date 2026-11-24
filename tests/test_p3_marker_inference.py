"""Synthetic receipt and selection checks for post-clustering markers."""

import json

import pytest

from devbench.core.protected_partitions import PartitionVault
from devbench.core.scrna_marker_inference import (
    ClusterDEProof,
    DispersionProvenance,
    MarkerContext,
    MarkerResult,
    MarkerRow,
    NegativeBinomialSplitProof,
    marker_rows_sha256,
    project_post_clustering_markers,
    require_confirmatory_marker_ora,
)
from devbench.study.contracts import content_sha256


def _rows():
    return (
        MarkerRow(gene_id="g1", score=2, p_value=0.01, q_value=0.03,
                  test_status="tested", selected=True),
        MarkerRow(gene_id="g2", score=1, p_value=0.02, q_value=0.03,
                  test_status="tested"),
        MarkerRow(gene_id="g3", score=None, test_status="not_estimable"),
        MarkerRow(gene_id="g4", score=None, test_status="independently_filtered"),
    )


def _context(rows):
    return MarkerContext(
        counts_sha256=content_sha256("original-counts"),
        cluster_labels_sha256=content_sha256("cluster-labels"),
        cell_ids_sha256=content_sha256(["c01", "c02"]),
        feature_ids_sha256=content_sha256(tuple(row.gene_id for row in rows)),
        design_sha256=content_sha256("fixed-donor-design"),
        contrast_id="cluster-a-vs-b",
    )


def _binding(rows):
    return {
        "context": _context(rows),
        "rows_sha256": marker_rows_sha256(rows),
        "execution_receipt_sha256": content_sha256("method-run"),
        "independent_check_sha256": content_sha256("independent-check"),
        "runtime_sha256": content_sha256("synthetic-runtime"),
        "method_version": "synthetic-custody-fixture",
        "fdr_threshold": 0.05,
    }


def _nb_proof(rows):
    return NegativeBinomialSplitProof(
        **_binding(rows),
        split_distribution="dirichlet_multinomial",
        splitting_receipt_sha256=content_sha256("split-run"),
        clustering_receipt_sha256=content_sha256("cluster-run"),
        clustering_counts_sha256=content_sha256("split-cluster"),
        inference_counts_sha256=content_sha256("split-test"),
        dispersion=DispersionProvenance(
            origin="independent_cohort",
            prior_free=True,
            values_sha256=content_sha256("dispersion-values"),
            source_sha256=content_sha256("independent-cohort"),
            feature_ids_sha256=_context(rows).feature_ids_sha256,
        ),
    )


def _fixture_verifier(workspace, *proofs):
    """Inject synthetic trusted custody, without claiming a biological verification."""
    vault = PartitionVault(workspace / "marker-custody")
    admitted = {
        (proof.execution_receipt_sha256, proof.independent_check_sha256):
        vault.put(proof.model_dump_json().encode())
        for proof in proofs
    }

    def verify(candidate):
        sha256 = admitted.get(
            (candidate.execution_receipt_sha256, candidate.independent_check_sha256)
        )
        if sha256 is None:
            return False
        with vault.open(sha256) as file:
            return file.read() == candidate.model_dump_json().encode()

    return verify


@pytest.mark.parametrize("method", ["unsplit", "poisson_thinning"])
def test_unsplit_and_poisson_marker_scores_have_no_inferential_probabilities(method):
    rows = _rows()
    result = project_post_clustering_markers(
        rows, context=_context(rows), method=method,
    )
    assert result.marker_scope == "exploratory_marker_ranking"
    assert result.proof is None
    assert [row.score for row in result.gene_statistics] == [2, 1, None, None]
    assert all(row.p_value is None and row.q_value is None
               for row in result.gene_statistics)
    assert all(not row.selected for row in result.gene_statistics)
    with pytest.raises(PermissionError, match="exploratory_marker_ranking"):
        require_confirmatory_marker_ora(
            result, foreground=("g1",), tested_background=("g1", "g2"),
        )


@pytest.mark.parametrize("origin,prior_free", [("in_sample", True), ("known", False)])
def test_nb_theta_snooping_is_exploratory(workspace_tmp_path, origin, prior_free):
    rows = _rows()
    proof = _nb_proof(rows)
    proof = proof.model_copy(update={"dispersion": proof.dispersion.model_copy(
        update={"origin": origin, "prior_free": prior_free},
    )})
    result = project_post_clustering_markers(
        rows, context=proof.context, method="nb_countsplit", proof=proof,
        verify_custody=_fixture_verifier(workspace_tmp_path, proof),
    )
    assert result.marker_scope == "exploratory_marker_ranking"
    assert result.reason == "independent_prior_free_dispersion_required"
    assert all(row.p_value is None and row.q_value is None
               for row in result.gene_statistics)


def test_caller_proof_and_forged_scope_do_not_establish_custody(workspace_tmp_path):
    rows = _rows()
    proof = _nb_proof(rows)
    verify = _fixture_verifier(workspace_tmp_path)
    result = project_post_clustering_markers(
        rows, context=proof.context, method="nb_countsplit", proof=proof,
    )
    assert result.marker_scope == "exploratory_marker_ranking"
    document = result.model_dump(mode="json")
    document.update({
        "marker_scope": "confirmatory_marker_eligible",
        "proof": proof.model_dump(mode="json"),
        "gene_statistics": [row.model_dump(mode="json") for row in rows],
    })
    forged = MarkerResult.model_validate_json(json.dumps(document))
    with pytest.raises(PermissionError, match="custody_unverified"):
        require_confirmatory_marker_ora(
            forged, foreground=("g1",), tested_background=("g1", "g2"),
            verify_custody=verify,
        )


def test_nb_receipt_binds_rows_axes_and_filtered_ora_universe(workspace_tmp_path):
    rows = _rows()
    proof = _nb_proof(rows)
    verify = _fixture_verifier(workspace_tmp_path, proof)
    kwargs = {"method": "nb_countsplit", "proof": proof, "verify_custody": verify}
    result = project_post_clustering_markers(rows, context=proof.context, **kwargs)
    assert result.marker_scope == "confirmatory_marker_eligible"
    assert result.gene_statistics == rows
    assert result.proof == proof
    require_confirmatory_marker_ora(
        result, foreground=("g1",), tested_background=("g1", "g2"),
        verify_custody=verify,
    )
    with pytest.raises(PermissionError, match="filtered_tested_background"):
        require_confirmatory_marker_ora(
            result, foreground=("g1",), tested_background=("g1", "g2", "g3", "g4"),
            verify_custody=verify,
        )
    with pytest.raises(PermissionError, match="verified_marker_selection"):
        require_confirmatory_marker_ora(
            result, foreground=("g2",), tested_background=("g1", "g2"),
            verify_custody=verify,
        )
    new_context = proof.context.model_copy(
        update={"cluster_labels_sha256": content_sha256("different-labels")},
    )
    changed = project_post_clustering_markers(rows, context=new_context, **kwargs)
    assert changed.marker_scope == "exploratory_marker_ranking"
    assert changed.reason == "marker_execution_or_axes_not_bound"


@pytest.mark.parametrize("bad_q", [0.001, 0.02])
def test_nb_custody_cannot_approve_q_below_p_or_the_frozen_bh_value(
    workspace_tmp_path, bad_q,
):
    first, *rest = _rows()
    rows = (first.model_copy(update={"q_value": bad_q}), *rest)
    proof = _nb_proof(rows)
    with pytest.raises(ValueError, match="q >= p|complete BH family"):
        project_post_clustering_markers(
            rows, context=proof.context, method="nb_countsplit", proof=proof,
            verify_custody=_fixture_verifier(workspace_tmp_path, proof),
        )


@pytest.mark.parametrize("unimodal", [False, True])
def test_clusterde_keeps_native_selection_and_never_fabricates_bh(
    workspace_tmp_path, unimodal,
):
    rows = tuple(
        row.model_copy(update={"p_value": None, "q_value": None}) for row in _rows()
    )
    proof = ClusterDEProof(
        **_binding(rows),
        unimodal=unimodal,
        input_population="single_batch",
        null_model_sha256=content_sha256("unimodal-null"),
        null_diagnostic_sha256=content_sha256("null-diagnostic"),
    )
    verify = _fixture_verifier(workspace_tmp_path, proof)
    result = project_post_clustering_markers(
        rows, context=proof.context, method="clusterde", proof=proof,
        verify_custody=verify,
    )
    expected = (
        "confirmatory_marker_eligible" if unimodal else "exploratory_marker_ranking"
    )
    assert result.marker_scope == expected
    assert all(row.p_value is None and row.q_value is None
               for row in result.gene_statistics)
    if unimodal:
        require_confirmatory_marker_ora(
            result, foreground=("g1",), tested_background=("g1", "g2"),
            verify_custody=verify,
        )


@pytest.mark.parametrize(
    ("denominator", "missing", "rest_cells_per_donor"),
    [("0", 0, 1), ("__rest__", 1, 2)],
)
def test_nb_pair_completeness_uses_only_donors_in_the_contrast(
    denominator, missing, rest_cells_per_donor,
):
    from rna_kernels.scrna_nb_pseudobulk import _paired_design, _profile_inventory
    from rna_kernels.scrna_upstream_contract import CountSplitPseudobulkParameters

    parameters = CountSplitPseudobulkParameters(
        condition="fixed", numerator="1", denominator=denominator,
    )
    donors = [(f"donor-{i}", ["0", "1", "2"]) for i in range(3)]
    donors += [("only-other", ["2"]), ("unassigned", [None])]
    rows = [
        ({"cluster_id": cluster}, {
            "donor": donor, "specimen": f"specimen-{donor}", "condition": "fixed",
        })
        for donor, clusters in donors
        for cluster in clusters
    ]
    profiles, indices, pairs, absent = _profile_inventory(rows, parameters)
    assert pairs == 3
    assert absent == missing
    assert indices[-1] == -1
    assert (indices[-2] == -1) == (denominator == "0")
    assert [
        row["cell_count"] for row in profiles
        if row["condition"] == denominator and row["donor"].startswith("donor-")
    ] == [rest_cells_per_donor] * 3
    paired = [row for row in profiles if row["donor"].startswith("donor-")]
    assert _paired_design(paired, parameters)["residual_df"] == 2
