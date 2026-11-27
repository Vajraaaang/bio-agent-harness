"""Predeclared synthetic complete-null calibration, with the run as FDR unit."""

import json

import numpy as np
import pytest
from scipy import sparse, stats
from sklearn.cluster import KMeans

from rna_kernels.scrna_cadet import cadet, quantile_residuals
from rna_kernels.scrna_clusterde import clipper, clusterde
from rna_kernels.scrna_nb_countsplit import FoldDiagnostics, split_count_block


def test_complete_null_overdispersed_nb():
    # Independent NB observations; no cell types, donor effects or differential
    # features. Freeze all trials, solver parameters and discovery families here.
    n, genes, reps, alpha = 192, 160, 128, 0.05
    theta, mean = 4.0, 12.0
    children = np.random.SeedSequence(20261127).spawn(reps)
    discovered = dict.fromkeys(("clusterde", "cadet", "theta16", "theta4"), 0)
    rejected = 0
    for rep, child in enumerate(children):
        rng = np.random.default_rng(child)
        counts = rng.negative_binomial(theta, theta / (theta + mean), (n, genes))
        score, selected, _, _, _ = clusterde(counts, rep, alpha)
        assert np.isfinite(score).all()
        discovered["clusterde"] += int(selected.any())
        # CADET and splitting test one prespecified feature after clustering on
        # two features. Thus the exact BH family has one member, q equals p.
        x = counts[:, :2]
        z = quantile_residuals(x, np.full(2, mean), np.full(2, theta), rng)
        _, p, _, event = cadet(z, 0, np.eye(2), rep)
        assert 0 <= p <= 1
        json.dumps(event, allow_nan=False)
        discovered["cadet"] += int(p <= alpha)
        for size in (4, 16):
            a, b = split_count_block(sparse.csr_matrix(x), np.full(2, size), 0.5, rng)
            labels = KMeans(n_clusters=2, n_init=1, random_state=rep).fit_predict(
                a.toarray()
            )
            y = b.toarray()[:, 0]
            split_p = stats.mannwhitneyu(
                y[labels == 0], y[labels == 1], method="asymptotic"
            ).pvalue
            discovered[f"theta{size}"] += int(split_p <= alpha)
            if size == 16:
                diagnostic = FoldDiagnostics([size] * 2)
                diagnostic.update(a, b)
                try:
                    diagnostic.verify()
                except ValueError:
                    rejected += 1
    print(
        json.dumps(
            {
                "replicates": reps,
                "alpha": alpha,
                "any_discoveries": discovered,
                "wrong_theta_refusals": rejected,
            },
            allow_nan=False,
        )
    )
    # Compare excess rejections with the predeclared one-sided exact binomial
    # test. Report the point estimate even for a nonsignificant excess.
    for method in ("clusterde", "cadet", "theta4"):
        assert (
            stats.binomtest(
                discovered[method], reps, alpha, alternative="greater"
            ).pvalue
            >= 0.05
        )
    assert discovered["theta16"] / reps > 0.20 and rejected / reps >= 0.90


def test_native_clipper_can_make_discoveries_and_reject_symmetric_scores():
    strong = np.r_[np.linspace(2, 5, 30), np.zeros(10)]
    assert clipper(strong, 0.05)[0].sum() == 30
    assert not clipper(np.r_[strong, -strong], 0.05)[0].any()


def test_cadet_selective_event_contains_observed_statistic():
    # A stable separated two-cluster alternative supplies a power control.
    rng = np.random.default_rng(17)
    x = rng.normal(size=(80, 2))
    x[:40] += 4
    score, p, labels, event = cadet(x, 0, np.eye(2), 3)
    sd = np.sqrt(1 / (labels == 0).sum() + 1 / (labels == 1).sum())
    assert any(
        (lo is None or lo <= score / sd) and (hi is None or hi >= score / sd)
        for lo, hi in event["standardized_intervals"]
    )
    assert p < 0.05


def test_opposite_fold_correlations_cannot_cancel():
    rng = np.random.default_rng(15)
    x = rng.poisson(8, (500, 2))
    y = np.column_stack([x[:, 0], 24 - x[:, 1]])
    diagnostic = FoldDiagnostics([4, 4])
    diagnostic.update(sparse.csr_matrix(x), sparse.csr_matrix(y))
    with pytest.raises(ValueError, match="correlation"):
        diagnostic.verify()


def test_clusterde_planted_nb_signal_power():
    # Same panel size as the complete-null test. Change the mean of the first
    # 48 features in half the cells while retaining overdispersion throughout.
    rng = np.random.default_rng(20261127)
    mean = np.full((192, 160), 4.0)
    mean[:96, :48] = 32
    x = rng.negative_binomial(4, 4 / (4 + mean))
    contrast, selected, _, _, diagnostic = clusterde(x, 0, 0.05)
    print(
        json.dumps(
            {
                "planted": 48,
                "contrast_scores": contrast.tolist(),
                "selected": selected.tolist(),
                "true_discoveries": int(selected[:48].sum()),
                "false_discoveries": int(selected[48:].sum()),
                "threshold": diagnostic["threshold"],
                "signal_score_range": [
                    float(contrast[:48].min()),
                    float(contrast[:48].max()),
                ],
                "null_score_range": [
                    float(contrast[48:].min()),
                    float(contrast[48:].max()),
                ],
            }
        )
    )
    assert selected[:48].sum() >= 1


def test_sparse_nb_cadet():
    theta, mean, n, reps = 0.4, 1.0, 192, 128
    discoveries, zeros = 0, 0
    for rep, child in enumerate(np.random.SeedSequence(20261127).spawn(reps)):
        rng = np.random.default_rng(child)
        x = rng.negative_binomial(theta, theta / (theta + mean), (n, 2))
        zeros += int((x == 0).sum())
        z = quantile_residuals(x, np.full(2, mean), np.full(2, theta), rng)
        _, p, _, _ = cadet(z, 0, np.eye(2), rep)
        assert 0 <= p <= 1
        discoveries += p <= 0.05
    print(
        json.dumps(
            {
                "sparse_cadet_discoveries": discoveries,
                "trials": reps,
                "zero_fraction": zeros / (reps * n * 2),
            }
        )
    )
    assert (
        stats.binomtest(discoveries, reps, 0.05, alternative="greater").pvalue >= 0.05
    )


def test_mounted_cadet_proof_and_ora_custody(tmp_path):
    import anndata
    import pandas as pd

    from devbench.core._files import hash_bounded_file
    from devbench.core.scientific_types import content_sha256
    from devbench.core.scrna_marker_inference import (
        CADETProof,
        MarkerRow,
        marker_rows_sha256,
        project_post_clustering_markers,
        require_confirmatory_marker_ora,
    )
    from devbench.engine.nb_marker_confirmation import verify_m2_marker_execution
    from devbench.mcp.plot_sidecars import verify_plot_sidecar
    from rna_kernels.scrna_cadet import run_m2

    rng = np.random.default_rng(20261127)
    # Planted alternative on the modeled scale, observed through discrete NB
    # marginals. The external population reference and model parameters are fixed.
    z = rng.normal(size=(96, 2))
    z[:48] += 3
    counts = stats.nbinom.ppf(stats.norm.cdf(z), 4, 0.25).astype(int)
    expression = tmp_path / "expression.h5ad"
    reference = tmp_path / "reference.json"
    genes = ["g0", "g1"]
    adata = anndata.AnnData(
        X=counts,
        obs=pd.DataFrame(
            {
                "unit": [f"u{i}" for i in range(96)],
                "donor_id": [f"d{i}" for i in range(96)],
            },
            index=[f"c{i}" for i in range(96)],
        ),
        var=pd.DataFrame(index=genes),
    )
    adata.write_h5ad(expression)
    reference.write_text(
        json.dumps(
            {
                "format": "devbench.cadet-reference/1",
                "origin": "known",
                "null_regime": "independent_nb_features_and_units",
                "source_sha256": "1" * 64,
                "feature_ids": genes,
                "mean": [12.0, 12.0],
                "theta": [4.0, 4.0],
                "covariance": np.eye(2).tolist(),
            }
        )
    )
    params = {"unit_key": "unit", "target_feature": "g0", "seed": 3}
    independent = json.loads(reference.read_bytes())
    correlated = dict(independent, covariance=[[1.0, 0.1], [0.1, 1.0]])
    reference.write_text(json.dumps(correlated))
    with pytest.raises(ValueError, match="identity covariance"):
        run_m2(
            "scrna_cadet",
            {"expression": expression, "reference": reference},
            params,
            tmp_path,
        )
    undeclared = dict(independent)
    del undeclared["null_regime"]
    reference.write_text(json.dumps(undeclared))
    with pytest.raises(ValueError, match="null_regime"):
        run_m2(
            "scrna_cadet",
            {"expression": expression, "reference": reference},
            params,
            tmp_path,
        )
    reference.write_text(json.dumps(independent))
    files = run_m2(
        "scrna_cadet",
        {"expression": expression, "reference": reference},
        params,
        tmp_path,
    )
    result = json.loads(files["result"].read_bytes())
    verify_plot_sidecar(files["plot_sidecar"], "json")
    kwargs = {
        "expression_sha256": hash_bounded_file(expression, 1 << 30),
        "parameters": params,
        "request_sha256": "2" * 64,
        "runtime_sha256": "3" * 64,
        "reference_sha256": hash_bounded_file(reference, 1 << 20),
    }
    receipt = verify_m2_marker_execution(result, **kwargs)
    assert not receipt["scientific_custody_verified"]
    proof = CADETProof.model_validate(receipt["proof"])
    rows = [MarkerRow.model_validate(row) for row in result["gene_statistics"]]
    assert rows[0].selected and rows[0].p_value == rows[0].q_value
    unverified = project_post_clustering_markers(
        rows, context=proof.context, proof=proof, method="cadet"
    )
    with pytest.raises(PermissionError):
        require_confirmatory_marker_ora(
            unverified, foreground=["g0"], tested_background=["g0"]
        )
    # This test authority stands in for an independent, exact-subject review.
    reviewed = content_sha256(proof.model_dump(mode="json"))

    def admitted(candidate):
        return content_sha256(candidate.model_dump(mode="json")) == reviewed

    checked = project_post_clustering_markers(
        rows,
        context=proof.context,
        proof=proof,
        method="cadet",
        verify_custody=admitted,
    )
    require_confirmatory_marker_ora(
        checked, foreground=["g0"], tested_background=["g0"], verify_custody=admitted
    )
    with pytest.raises(PermissionError):
        require_confirmatory_marker_ora(
            checked, foreground=["g0"], tested_background=genes, verify_custody=admitted
        )
    for bad_rows in (
        [rows[0].model_copy(update={"selected": False}), rows[1]],
        [rows[0], rows[1].model_copy(update={"score": 1.0})],
    ):
        rebound = proof.model_copy(update={"rows_sha256": marker_rows_sha256(bad_rows)})
        with pytest.raises(ValueError, match="CADET"):
            project_post_clustering_markers(
                bad_rows,
                context=proof.context,
                proof=rebound,
                method="cadet",
                verify_custody=lambda candidate, bound=rebound: candidate == bound,
            )
    result["gene_statistics"][0].update(p_value=1.0, q_value=1.0, selected=False)
    result["rows_sha256"] = marker_rows_sha256(
        [MarkerRow.model_validate(r) for r in result["gene_statistics"]]
    )
    with pytest.raises(ValueError, match="conditional probability"):
        verify_m2_marker_execution(result, **kwargs)


@pytest.mark.parametrize(("theta", "mean"), [(4.0, 12.0), (0.4, 1.0)])
def test_cadet_independent_precision_panel(theta, mean):
    # One fixed confirmation panel per regime after the original 128 trials.
    # No optional stopping; the admission criterion is an upper bound, not
    # failure to detect inflation. This probes a single frozen target only.
    n, trials, alpha = 192, 1024, 0.05
    panel_id = 1 if theta == 4 else 2
    rejected = 0
    for rep, seed in enumerate(
        np.random.SeedSequence([20261127, 28, panel_id]).spawn(trials)
    ):
        rng = np.random.default_rng(seed)
        counts = rng.negative_binomial(theta, theta / (theta + mean), (n, 2))
        z = quantile_residuals(counts, np.full(2, mean), np.full(2, theta), rng)
        _, p, _, _ = cadet(z, 0, np.eye(2), rep)
        rejected += int(p <= alpha)
    upper = (
        float(stats.beta.ppf(0.95, rejected + 1, trials - rejected))
        if rejected < trials
        else 1.0
    )
    print(
        json.dumps(
            {
                "confirmation_trials": trials,
                "theta": theta,
                "mean": mean,
                "any_discoveries": rejected,
                "upper_95": upper,
                "upper_limit": 0.075,
            }
        )
    )
    assert upper <= 0.075


def test_synthetic_counts_preserve_values_beyond_input_dtype():
    from rna_kernels.scrna_clusterde import synthetic_null

    # The fitted synthesis distribution has upper support beyond signed byte.
    x = np.tile(np.array([[10, 2], [126, 118]], dtype=np.int8), (128, 1))
    synthetic, _ = synthetic_null(x, np.random.default_rng(1))
    wider, _ = synthetic_null(x.astype(np.int64), np.random.default_rng(1))
    assert synthetic.dtype == np.int64 and synthetic.max() > 127
    np.testing.assert_array_equal(synthetic, wider)


def test_discrete_quantile_residual_handles_unsigned_zeros():
    x = np.array([[0, 2], [1, 0]], dtype=np.uint8)
    args = (np.ones(2), np.full(2, 4))
    np.testing.assert_array_equal(
        quantile_residuals(x, *args, np.random.default_rng(0)),
        quantile_residuals(x.astype(np.int64), *args, np.random.default_rng(0)),
    )


@pytest.mark.parametrize("column", ["donor", "Subject_ID", "organism_id", "specimen"])
def test_cadet_refuses_unique_barcodes_from_repeated_units(column):
    import pandas as pd

    from rna_kernels.scrna_cadet import _check_independent_rows

    frame = pd.DataFrame(
        {"barcode": ["c0", "c1"], "donor_id": ["d0", "d1"], column: ["same", "same"]}
    )
    with pytest.raises(ValueError, match="Repeated or missing"):
        _check_independent_rows(frame, "barcode")
    frame[column] = ["s1", None]
    with pytest.raises(ValueError, match="Repeated or missing"):
        _check_independent_rows(frame, "barcode")


def test_cadet_requires_declared_higher_identity():
    import pandas as pd

    from rna_kernels.scrna_cadet import _check_independent_rows

    frame = pd.DataFrame({"cell_id": ["c0", "c1"], "specimen": ["s0", "s1"]})
    with pytest.raises(ValueError, match="higher-level biological unit"):
        _check_independent_rows(frame, "cell_id")
