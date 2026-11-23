"""Paired calibration uses the same conditional NB dispersion objective."""

import numpy as np
import pandas as pd


def _paired_counts():
    rng = np.random.default_rng(37)
    unit_count = 24
    units = np.repeat(np.arange(unit_count), 2)
    population = np.tile([0, 1], unit_count)
    stage = np.repeat(np.arange(unit_count) >= unit_count // 2, 2)
    indicators = (np.arange(1, unit_count) == units[:, None]).astype(float)
    metadata = pd.DataFrame(index=[f"s{i}" for i in range(len(units))])
    design = pd.DataFrame(
        np.column_stack(
            [np.ones(len(units)), indicators, population * ~stage, population * stage]
        ),
        columns=[
            "intercept",
            *[f"biological_unit_{i}" for i in range(1, unit_count)],
            "population_at_stage_0",
            "population_at_stage_1",
        ],
        index=metadata.index,
    )
    factors = np.exp(np.linspace(-0.14, 0.14, len(units)))
    means = rng.uniform(50, 100, (200, 1)) * factors
    shape = 8
    counts = rng.negative_binomial(shape, shape / (shape + means))
    genes = tuple(f"g{i}" for i in range(len(counts)))
    return counts, genes, factors, metadata, design


def test_paired_shared_calibration_matches_pydeseq2_without_rebuilding_datasets(
    monkeypatch,
):
    from rna_kernels import normalization

    counts, genes, factors, metadata, design = _paired_counts()
    reference = normalization.calibrate_counts(
        counts[:64],
        {"gene_ids": list(genes[:64]), "total_gene_count": len(genes)},
        metadata,
        design,
        strict=False,
        size_factors=factors,
    )
    pilot = reference[2]
    assert pilot["prior_log_dispersion_variance"] is not None
    expected = normalization.calibrate_counts(
        counts[64:],
        {"gene_ids": list(genes[64:])},
        metadata,
        design,
        strict=False,
        size_factors=factors,
        dispersion_reference=pilot,
    )[2]["gene_estimates"]
    original = normalization.calibrate_counts
    fallback = []

    def observe_fallback(values, panel, *args, **kwargs):
        fallback.extend(panel["gene_ids"])
        return original(values, panel, *args, **kwargs)

    monkeypatch.setattr(normalization, "calibrate_counts", observe_fallback)
    result, _, _, filtered = normalization.calibrate_count_family(
        [(genes, counts)],
        reference,
        metadata,
        design,
        strict=False,
    )
    assert not filtered
    assert len(fallback) < (len(genes) - 64) * 0.1
    actual = result["gene_estimates"]
    assert set(actual) == set(genes)
    for gene, reference_row in expected.items():
        row = actual[gene]
        for key in ("dispersion_outlier", "dispersion_converged"):
            assert row[key] == reference_row[key]
        for key in ("genewise_dispersion", "dispersion_alpha", "trend_dispersion"):
            np.testing.assert_allclose(
                row[key],
                reference_row[key],
                rtol=2e-5,
                atol=1e-6,
                err_msg=f"{gene} {key}",
            )


def test_cox_reid_pair_information_matches_dense_reference_after_reordering():
    from pydeseq2.utils import nb_nll
    from scipy.special import gammaln

    from rna_kernels.paired_dispersion import PairedDispersion

    counts, _, _, _, design = _paired_counts()
    rng = np.random.default_rng(1331)
    order = rng.permutation(len(design))
    design = design.iloc[order]
    counts = counts[:6, order]
    context = PairedDispersion.from_design(
        design,
        {"trend_type": "mean", "version": "0.5.4"},
    )
    mu = rng.uniform(0.5, 200, counts.shape)
    alpha = rng.uniform(0.02, 0.5, (len(counts), 1))
    log_alpha = np.log(alpha[:, 0])
    centers = rng.uniform(-4, -1, len(counts))
    prior = 0.7
    expected_loss, expected_gradient = [], []
    matrix = design.to_numpy()
    for observed, means, dispersion, log_dispersion, center in zip(
        counts,
        mu,
        alpha[:, 0],
        log_alpha,
        centers,
        strict=True,
    ):
        weights = means / (1 + means * dispersion)
        information = (matrix.T * weights) @ matrix
        derivative = -(matrix.T * weights**2) @ matrix
        expected_gradient.append(np.trace(np.linalg.solve(information, derivative)))
        expected_loss.append(
            nb_nll(observed, means, dispersion)
            - gammaln(observed + 1).sum()
            + 0.5 * np.linalg.slogdet(information)[1]
            + (log_dispersion - center) ** 2 / (2 * prior)
        )
    np.testing.assert_allclose(
        context._cr_gradient(mu, alpha), expected_gradient, atol=1e-8
    )
    np.testing.assert_allclose(
        context._loss(log_alpha, counts, mu, centers, prior), expected_loss, atol=1e-8
    )
