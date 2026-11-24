"""Synthetic controls for the frozen covariate-aware rank sensitivity."""

import numpy as np
import pytest
from scipy.stats import mannwhitneyu

from rna_kernels.rank_sensitivity import (
    prepare_rank_sensitivity,
    rank_family_bh,
)
from rna_kernels.rank_sensitivity import (
    test_rank_block as run_block,
)


def _cohort(n=120, group=None, **kwargs):
    if group is None:
        group = ["A", "B"] * (n // 2)
    return prepare_rank_sensitivity(
        group,
        [f"d{i}" for i in range(n)],
        numerator="B",
        denominator="A",
        **kwargs,
    )


def test_routes_balance_strata_covariates_and_sparse_strata():
    assert _cohort().method == "wilcoxon"
    assert (
        _cohort(strata=np.repeat(["batch1", "batch2", "batch3"], 40)).method
        == "van_elteren"
    )
    assert _cohort(strata=np.repeat(np.arange(40), 3)).method == "freedman_lane"
    assert _cohort(covariates=np.arange(120)[:, None]).method == "freedman_lane"


def test_wilcoxon_signed_effect_asymptotic_reference_and_incomplete_rows():
    rng = np.random.default_rng(4)
    values = rng.integers(0, 15, size=(6, 120)).astype(float)
    values[0, 1::2] += 5
    values[3, :] = 2
    values[4, 17] = np.nan
    values[5, :] = 0
    effect, p, codes = run_block(_cohort(), values)
    reference = mannwhitneyu(
        values[:3, 1::2], values[:3, ::2], axis=1, method="asymptotic"
    )
    np.testing.assert_allclose(p[:3], reference.pvalue)
    np.testing.assert_allclose(effect[:3], reference.statistic / (60 * 60) - 0.5)
    assert effect[0] > 0
    assert codes.tolist() == [0, 0, 0, 3, 3, 1]
    assert np.isnan(p[3:]).all() and np.isnan(effect[3:]).all()
    np.testing.assert_allclose(
        rank_family_bh(np.array([0.01, 0.02, np.nan, np.nan]), np.array([0, 0, 3, 1])),
        np.array([0.03, 0.03, np.nan, np.nan]),
        equal_nan=True,
    )


def test_unbalanced_wilcoxon_matches_asymptotic_reference():
    plan = _cohort(group=["A"] * 70 + ["B"] * 50)
    values = np.random.default_rng(51).integers(0, 15, size=(3, 120))
    values[0, 70:] += 6
    effect, p, codes = run_block(plan, values)
    reference = mannwhitneyu(
        values[:, 70:], values[:, :70], axis=1, method="asymptotic"
    )
    assert plan.method == "wilcoxon" and plan.permutations == 0
    assert plan.evidence()["minimum_permutation_p"] is None
    np.testing.assert_array_equal(codes, [0, 0, 0])
    np.testing.assert_allclose(effect, reference.statistic / (70 * 50) - 0.5)
    np.testing.assert_allclose(p, reference.pvalue)


def test_van_elteren_cancels_pooled_batch_signal():
    rng = np.random.default_rng(713)
    labels = ["A"] * 50 + ["B"] * 10 + ["A"] * 10 + ["B"] * 50
    strata = ["first"] * 60 + ["second"] * 60
    values = rng.normal(size=(2, 120))
    values[:, 60:] += 10
    values[1, np.asarray(labels) == "B"] += 3
    effect, p, codes = run_block(_cohort(group=labels, strata=strata), values)
    assert list(codes) == [0, 0]
    assert (
        mannwhitneyu(
            values[0, np.asarray(labels) == "A"], values[0, np.asarray(labels) == "B"]
        ).pvalue
        < 0.01
    )
    assert p[0] > 0.1
    assert p[1] < 0.001 and effect[1] > 0.3


def test_freedman_lane_reproducible_chunking_and_covariate_adjustment():
    rng = np.random.default_rng(179)
    labels = ["A"] * 60 + ["B"] * 60
    covariate = rng.normal(size=120) + np.repeat([0, 1.4], 60)
    values = covariate + rng.normal(scale=0.35, size=(7, 120))
    values[1, 60:] += 1.1
    plan = _cohort(
        group=labels, covariates=covariate[:, None], seed=31, permutations=199
    )
    effects, p, codes = run_block(plan, values)
    chunks = [run_block(plan, values[:3]), run_block(plan, values[3:])]
    for expected, left, right in zip((effects, p, codes), *chunks, strict=True):
        np.testing.assert_allclose(np.concatenate((left, right)), expected)
    assert np.all(codes == 0)
    assert p[0] > 0.1
    assert 1 / 200 <= p[1] < 0.025
    assert effects[1] > 0
    np.testing.assert_allclose(p * 200, np.round(p * 200))
    alt = _cohort(
        group=labels, covariates=covariate[:, None], seed=32, permutations=199
    )
    assert np.any(run_block(alt, values)[1] != p)


def test_sparse_permutations_preserve_strata_and_conservative_resolution():
    strata = np.repeat(np.arange(40), 3)
    plan = _cohort(strata=strata, permutations=199, seed=2)
    data = strata[None, :] * 50.0 + np.tile([0, 1], 60)
    effects, p, codes = run_block(plan, data)
    assert codes.tolist() == [0] and effects[0] > 0
    assert p.tolist() == [1 / 200]


def test_refuses_independence_and_nonidentifiable_adjustment():
    with pytest.raises(ValueError, match="100"):
        _cohort(n=98)
    with pytest.raises(ValueError, match="unique"):
        prepare_rank_sensitivity(
            ["A", "B"] * 60, ["same"] * 120, numerator="B", denominator="A"
        )
    with pytest.raises(ValueError, match="rank"):
        _cohort(strata=["A", "B"] * 60)
    with pytest.raises(ValueError, match="rank"):
        _cohort(covariates=np.ones((120, 1)))
    with pytest.raises(ValueError, match="finite"):
        _cohort(covariates=np.full((120, 1), np.nan))


def test_stratified_rows_with_only_between_strata_differences_are_unavailable():
    strata = np.repeat(np.arange(3), 40)
    plan = _cohort(strata=strata)
    effects, p, codes = run_block(plan, strata[None, :])
    assert codes.tolist() == [3]
    assert np.isnan(effects).all() and np.isnan(p).all()
    with pytest.raises(ValueError, match="unavailable row"):
        rank_family_bh(np.array([0.2, 0.4]), np.array([0, 3]))
