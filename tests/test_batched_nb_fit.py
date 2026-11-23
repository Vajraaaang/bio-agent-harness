"""Conditional negative-binomial numerics on synthetic observations."""

import numpy as np
import pandas as pd
import pytest
from scipy.stats import t
from statsmodels.stats.multitest import multipletests


def paired_design():
    donors = np.repeat(np.arange(128), 2)
    condition = np.tile([0.0, 1.0], 128)
    donor_indicators = (donors[:, None] == np.arange(1, 128)).astype(float)
    return pd.DataFrame(
        np.column_stack(
            (
                np.ones(256),
                donor_indicators,
                condition * (donors < 64),
                condition * (donors >= 64),
            )
        ),
        columns=[
            "intercept",
            *(f"biological_unit_{d}" for d in range(1, 128)),
            "population_at_stage_0",
            "population_at_stage_1",
        ],
    )


@pytest.mark.parametrize("ridge", [0, 1e-6])
def test_paired_schur_solve_and_logdet_match_original_design(ridge):
    from rna_kernels.nb_irls import FixedEffectDesign

    design = paired_design()
    matrix = design.to_numpy()
    reducer = FixedEffectDesign.from_design(design)
    assert reducer is not None
    rng = np.random.default_rng(5)
    weights, response = rng.uniform(0.1, 2, (5, 256)), rng.normal(size=(5, 256))
    systems = np.einsum("ni,gn,nj->gij", matrix, weights, matrix)
    rhs = (weights * response) @ matrix
    expected = np.linalg.solve(
        systems + ridge * np.eye(matrix.shape[1]), rhs[..., None]
    )[..., 0]
    np.testing.assert_allclose(
        reducer.solve(weights, response, ridge=ridge), expected, atol=1e-10
    )
    np.testing.assert_allclose(
        reducer.logdet(weights), np.linalg.slogdet(systems)[1], atol=1e-10
    )


def test_statsmodels_reference_parity_for_200_paired_genes():
    from devbench.core.estimators import StatsmodelsEstimator
    from rna_kernels.nb_irls import FixedEffectDesign, fit_statsmodels_nb

    rng = np.random.default_rng(26)
    design = paired_design()
    reducer = FixedEffectDesign.from_design(design)
    assert reducer is not None
    offset = rng.uniform(-0.3, 0.3, 256)
    alpha = rng.uniform(0.04, 0.4, 200)
    condition = design.to_numpy()[:, -2:].sum(axis=1)
    means = np.exp(
        rng.uniform(2.6, 4.4, (200, 128)).repeat(2, axis=1) + offset + 0.22 * condition
    )
    counts = rng.negative_binomial(1 / alpha[:, None], 1 / (1 + means * alpha[:, None]))
    fit = fit_statsmodels_nb(reducer, counts, alpha, offset)
    assert fit.converged.all()
    contrasts = np.eye(design.shape[1])[-2:]
    got_effect, got_variance = fit.contrast(contrasts)
    reference_effect, reference_variance = [], []
    estimator = StatsmodelsEstimator()
    for row, dispersion in zip(counts, alpha, strict=True):
        expected = estimator.fit_negative_binomial(
            row, design, alpha=dispersion, offset=offset
        )
        reference_effect.append(contrasts @ np.asarray(expected.params))
        reference_variance.append(
            np.einsum("ki,ij,kj->k", contrasts, expected.cov_params(), contrasts)
        )
    reference_effect = np.asarray(reference_effect)
    reference_variance = np.asarray(reference_variance)
    np.testing.assert_allclose(
        got_effect / np.log(2), reference_effect / np.log(2), atol=1e-6, rtol=0
    )
    np.testing.assert_allclose(
        np.sqrt(got_variance) / np.log(2),
        np.sqrt(reference_variance) / np.log(2),
        atol=1e-6,
        rtol=0,
    )
    observed_p = 2 * t.sf(np.abs(got_effect / np.sqrt(got_variance)), fit.df_resid)
    expected_p = 2 * t.sf(
        np.abs(reference_effect / np.sqrt(reference_variance)), fit.df_resid
    )
    np.testing.assert_allclose(observed_p, expected_p, atol=1e-6, rtol=0)
    np.testing.assert_allclose(
        multipletests(observed_p.ravel(), method="fdr_bh")[1],
        multipletests(expected_p.ravel(), method="fdr_bh")[1],
        atol=1e-6,
        rtol=0,
    )


def test_dense_irls_matches_statsmodels_and_exits_boundary_rows():
    from devbench.core.estimators import StatsmodelsEstimator
    from rna_kernels.nb_irls import DenseDesign, fit_statsmodels_nb

    rng = np.random.default_rng(51)
    design = pd.DataFrame(
        {
            "intercept": np.ones(64),
            "treated": np.tile([0, 1], 32),
            "age": rng.normal(size=64),
        }
    )
    values = design.to_numpy()
    offset = rng.normal(0, 0.2, size=64)
    rng_counts = rng.negative_binomial(
        5, 5 / (5 + np.exp(values @ [3, 0.5, 0.1] + offset)), size=(16, 64)
    )
    rng_counts[0, :] = 0
    fit = fit_statsmodels_nb(
        DenseDesign(values), rng_counts, np.repeat(0.1, 16), offset
    )
    assert fit.converged.tolist() == [False, *([True] * 15)]
    effects, covariance_diagonal = fit.contrast(np.eye(values.shape[1]))
    estimator = StatsmodelsEstimator()
    for i in range(1, len(rng_counts)):
        expected = estimator.fit_negative_binomial(
            rng_counts[i], design, alpha=0.1, offset=offset
        )
        np.testing.assert_allclose(effects[i], expected.params, atol=1e-6, rtol=0)
        np.testing.assert_allclose(
            covariance_diagonal[i], np.diag(expected.cov_params()), atol=1e-6, rtol=0
        )


def test_dense_projection_isolates_a_singular_converged_gene() -> None:
    from devbench.core.batched_nb_fit import conditional_nb_contrasts
    from devbench.core.estimators import StatsmodelsEstimator
    from devbench.core.family_fitting import _fit_models
    from devbench.core.population_statistics import _contrast_estimates
    from rna_kernels.nb_irls import DenseDesign, NBIRLSGene, fit_statsmodels_nb

    design = np.array([[1.0, 0.0], [1.0, 0.0], [1.0, 1.0], [1.0, 1.0]])
    counts = np.array([[8, 10, 16, 18], [7, 9, 18, 20], [10, 12, 23, 25], [0] * 4])
    alpha, offset = np.full(len(counts), 0.1), np.zeros(design.shape[0])
    fit = fit_statsmodels_nb(DenseDesign(design), counts, alpha, offset)
    assert fit.converged.tolist() == [True, True, True, False]
    # Losing both treated weights makes just this gene's working system singular.
    fit.weights[1, 2:] = 0
    vector = np.array([0.0, 1.0])
    vectors = vector[None, :]

    _, variances = fit.contrast(vectors)
    assert np.isfinite(variances[:, 0]).tolist() == [True, False, True, False]
    assert np.isnan(variances[1]).all()

    def project(fitted):
        projected = conditional_nb_contrasts(fitted, vectors)
        if projected is not None:
            return projected
        return (_contrast_estimates(fitted, vector),)

    genes = ("valid_before", "singular", "valid_after")
    candidates = [(gene, counts[i], NBIRLSGene(fit, i)) for i, gene in enumerate(genes)]
    dispersions = {
        gene: {"dispersion_alpha": alpha[i], "dispersion_converged": True}
        for i, gene in enumerate(genes)
    }
    estimator = StatsmodelsEstimator()
    failures: dict[str, str] = {}
    fits, batched_count = _fit_models(
        candidates,
        estimator,
        design,
        offset,
        {},
        dispersions,
        failures,
        project,
    )
    assert batched_count == 2
    assert not failures
    for i, gene in enumerate(genes):
        expected = estimator.fit_negative_binomial(
            counts[i],
            design,
            alpha=alpha[i],
            offset=offset,
        )
        np.testing.assert_allclose(fits[gene], project(expected), atol=1e-6, rtol=0)


@pytest.mark.parametrize("empty_unit", [False, True], ids=["schur", "empty-unit"])
def test_paired_projection_isolates_a_singular_converged_gene(empty_unit):
    from rna_kernels.nb_irls import FixedEffectDesign, fit_statsmodels_nb

    donors = np.repeat(np.arange(4), 2)
    design = pd.DataFrame(
        {
            "intercept": np.ones(len(donors)),
            **{f"biological_unit_{i}": donors == i for i in range(1, 4)},
            "condition": np.tile([0, 1], 4),
        }
    )
    reducer = FixedEffectDesign.from_design(design)
    assert reducer is not None
    counts = np.array(
        [
            [8, 16, 10, 18, 12, 23, 15, 25],
            [7, 18, 9, 20, 12, 21, 10, 19],
            [10, 23, 12, 25, 13, 21, 16, 27],
        ]
    )
    fit = fit_statsmodels_nb(reducer, counts, np.full(3, 0.1), np.zeros(len(donors)))
    assert fit.converged.all()
    contrasts = np.eye(design.shape[1])[[0, -1]]
    neighbors = [0, 2]
    kept_covariances = reducer.contrast_covariance(fit.weights[neighbors], contrasts)
    kept_variances = np.diagonal(kept_covariances, axis1=1, axis2=2)
    # Remove either one complete unit or all treated weights in just one row.
    if empty_unit:
        fit.weights[1, :2] = 0
    else:
        fit.weights[1, 1::2] = 0
    with np.errstate(divide="raise", invalid="raise"):
        _, variances = fit.contrast(contrasts)
    assert np.isnan(variances[1]).all()
    assert np.isfinite(variances[neighbors]).all()
    np.testing.assert_array_equal(variances[neighbors], kept_variances)
