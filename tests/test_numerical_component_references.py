"""Small independent component references, not pipeline or clinical qualification.

All observations are literal synthetic fixtures. Dispersion is fixed in the NB
check; annotations are fixed in the aggregation check. Neither is independently
estimated here. The paired OLS check qualifies the design arithmetic only.
"""
from copy import deepcopy
from dataclasses import replace
import math
from statistics import NormalDist

import numpy as np
import pandas as pd
import pytest

from devbench.study.biology_tool_io import ObservedData


def test_ordinary_normalization_matches_hand_derived_log_medians():
    from devharness_server.normalization import median_ratio_factors

    # Every eligible row has geometric mean 4. The sample log2-ratio
    # medians are (-1, 1, 1/2), including an even-size middle pair.
    counts = np.array([[2, 8, 4], [4, 16, 1], [1, 8, 8], [2, 4, 8],
                       [256, 0, 1], [0, 0, 0]])
    genes = ("r1", "r2", "r3", "r4", "has_zero", "all_zero")
    expected = 2.0 ** np.array([-7 / 6, 5 / 6, 1 / 3])
    actual, details = median_ratio_factors(counts, genes)
    np.testing.assert_allclose(actual, expected, rtol=1e-12, atol=1e-12)
    assert math.prod(actual) == pytest.approx(1.0)
    assert details["method"] == "median_of_ratios"
    assert details["reference_gene_ids"] == list(genes[:4])
    assert details["reference_gene_count"] == 4
    assert details["factor_centering"] == "geometric_mean_one"
    # Whole-family arithmetic is invariant to gene order; this does not test
    # the separate, order-sensitive bounded calibration-panel selector.
    reordered, _ = median_ratio_factors(counts[::-1], genes[::-1])
    np.testing.assert_allclose(reordered, expected, rtol=1e-12, atol=1e-12)


def test_positive_count_normalization_matches_full_sample_root_reference():
    from devharness_server.normalization import median_ratio_factors

    # No row is all-positive. Positive products are (64, 8, 8), and the
    # disclosed full-n=3 roots are (4, 2, 2), not positive-entry-count roots.
    # Per-sample geometric middle ratios are (2, 2, 1), before centering.
    counts = np.array([[4, 16, 0], [0, 2, 4], [8, 0, 1], [0, 0, 0]])
    actual, details = median_ratio_factors(counts, ("a", "b", "c", "zero"))
    expected = 2.0 ** np.array([1 / 3, 1 / 3, -2 / 3])
    np.testing.assert_allclose(actual, expected, rtol=1e-12, atol=1e-12)
    assert details["method"] == "median_of_ratios_poscounts"
    assert details["reference_gene_ids"] == ["a", "b", "c"]
    assert details["reference_gene_count"] == 3


@pytest.mark.parametrize("counts,reason", [
    ([[0, 0], [0, 0]], "no_normalization_reference"),
    ([[4, 0], [0, 0]], "no_sample_normalization_reference"),
])
def test_normalization_without_usable_references_fails(counts, reason):
    from devharness_server.normalization import median_ratio_factors

    with pytest.raises(ValueError, match=reason):
        median_ratio_factors(np.asarray(counts), ("a", "b"))


@pytest.mark.parametrize("fit_path", ["bulk_family", "fixed_candidate", "temporal"])
def test_fixed_dispersion_nb_matches_closed_form_likelihood_and_contrast(fit_path, monkeypatch):
    from devbench.study.biology_tools_bulk import _fit_family, _fit_given_calibration, _linear_estimate
    from devharness_server.tools_stat import _fit_candidate

    # Within each equal-exposure pair, deviations cancel. The NB2 score is
    # therefore zero at baseline means 20 and 40 for any fixed alpha > 0.
    # This gives an analytic reference without a second fitted estimator.
    values = np.array([8, 12, 36, 44, 16, 24, 76, 84])
    factors = np.array([0.5, 0.5, 2, 2] * 2)
    design = pd.DataFrame({"Intercept": np.ones(8), "condition_B": [0] * 4 + [1] * 4})
    alpha = 0.2
    expected_mu = np.array([10, 10, 40, 40, 20, 20, 80, 80], dtype=float)
    expected_beta = np.log([20, 2])
    if fit_path == "bulk_family":
        from devharness_server import normalization
        # Isolate the conditional GLM component; this deliberately does not
        # claim to test an empirical dispersion or normalization estimate.
        monkeypatch.setattr(normalization, "calibrate_counts", lambda *args, **kwargs:
            (factors, {}, {"gene_estimates": {"g0": {"dispersion_alpha": alpha,
                "dispersion_converged": True}}}, {}, []))
        data = _observed([values], [{"condition": "A" if i < 4 else "B"} for i in range(8)])
        fits, failures = _fit_family(data, design, {})
        assert not failures
        fitted = fits["g0"]
    elif fit_path == "fixed_candidate":
        fitted = _fit_given_calibration(values, design.to_numpy(), alpha=alpha, offset=np.log(factors))
    else:
        fitted, _, _ = _fit_candidate(values, design, alpha, np.log(factors))
    assert fitted.converged
    assert fitted.scale == pytest.approx(1.0)
    np.testing.assert_allclose(fitted.params, expected_beta, rtol=1e-7, atol=1e-9)
    np.testing.assert_allclose(fitted.fittedvalues, expected_mu, rtol=1e-7, atol=1e-9)

    # NB2 log PMF from gamma functions, independently of statsmodels/PyDESeq2.
    size = 1 / alpha
    expected_ll = sum(math.lgamma(y + size) - math.lgamma(size) - math.lgamma(y + 1)
                      + size * math.log(size / (size + mu))
                      + y * math.log(mu / (size + mu))
                      for y, mu in zip(values, expected_mu, strict=True))
    assert fitted.llf == pytest.approx(expected_ll, rel=1e-10, abs=1e-9)
    score = design.to_numpy().T @ ((values - fitted.fittedvalues) / (1 + alpha * fitted.fittedvalues))
    np.testing.assert_allclose(score, np.zeros(2), rtol=0, atol=1e-6)

    # Group information is sum(mu/(1+alpha*mu)): 140/9 and 296/17.
    # Transforming independent log group means to (intercept, B-A) gives:
    variance_a, variance_b = 9 / 140, 17 / 296
    expected_cov = np.array([[variance_a, -variance_a],
                             [-variance_a, variance_a + variance_b]])
    np.testing.assert_allclose(fitted.cov_params(), expected_cov, rtol=1e-7, atol=1e-9)
    effect, intervals, p_value = _linear_estimate(fitted, [0, 1], discrete=True)
    se_log2 = math.sqrt(variance_a + variance_b) / math.log(2)
    half_width = NormalDist().inv_cdf(0.975) * se_log2
    assert effect == pytest.approx([1.0], rel=1e-7, abs=1e-9)
    assert (intervals[0]["lower"], intervals[0]["upper"]) == pytest.approx(
        (1 - half_width, 1 + half_width), rel=1e-7, abs=1e-9)
    assert p_value == pytest.approx(math.erfc(1 / (se_log2 * math.sqrt(2))), rel=1e-6)
    reverse, reverse_intervals, reverse_p = _linear_estimate(fitted, [0, -1], discrete=True)
    assert reverse == pytest.approx([-1.0], rel=1e-7, abs=1e-9)
    assert (reverse_intervals[0]["lower"], reverse_intervals[0]["upper"]) == pytest.approx(
        (-1 - half_width, -1 + half_width), rel=1e-7, abs=1e-9)
    assert reverse_p == pytest.approx(p_value)


def _observed(values, records, *, measurement="umi_counts"):
    """Only literal in-memory inputs; no loader, annotations or data acquisition."""
    metadata = pd.DataFrame(records)
    metadata["sample_id"] = [f"c{i}" for i in range(len(records))]
    metadata["cell_id"] = metadata["sample_id"]
    if "capture_batch" not in metadata:
        metadata["capture_batch"] = "capture0"
    metadata = metadata.set_index("sample_id", drop=False)
    expression = np.asarray(values)
    expression.flags.writeable = False
    return ObservedData(expression=expression,
        gene_ids=tuple(f"g{i}" for i in range(len(expression))),
        sample_ids=tuple(metadata.index), metadata=metadata, annotations={},
        measurement=measurement, targets=(), provenance={},
        unit_hierarchy={"population_unit": "donor", "population_unit_label": "donor_id",
                        "specimen_label": "specimen_id", "library_label": "library_id",
                        "identity_status": "declared_synthetic"})


def test_pseudobulk_matches_exact_unequal_cell_and_specimen_sums():
    from devbench.study.biology_tools_singlecell import _pseudobulk

    records = [dict(donor_id=donor, condition=condition, specimen_id=specimen, library_id=library)
               for donor, condition, specimen, library in [
                   ("d0", "A", "s00", "l00"), ("d0", "A", "s00", "l01"),
                   ("d0", "A", "s01", "l02"), ("d0", "A", "s01", "l02"),
                   ("d1", "B", "s10", "l10"), ("d1", "B", "s10", "l10"),
                   ("d1", "B", "s11", "l11"), ("d1", "B", "s11", "l11"),
                   ("d1", "B", "s11", "l11")]]
    data = _observed([[2, 3, 7, 4, 10, 20, 30, 40, 1000],
                      [10, 0, 1, 8, 1, 2, 3, 4, 1000]], records)
    before = data.metadata.copy(deep=True)
    labels = ["T", "T", "T", "B", "T", "T", "T", "T", None]
    actual, details = _pseudobulk(data, labels, {}, population_ids=["T", "B"])
    expected = {("d0", "A", "B"): [4, 8], ("d0", "A", "T"): [12, 11],
                ("d1", "B", "T"): [100, 10]}
    assert actual.expression.shape == (2, 3)
    for index, row in enumerate(actual.metadata.itertuples()):
        np.testing.assert_array_equal(actual.expression[:, index],
                                      expected[(row.donor_id, row.condition, row.cell_type)])
    membership = {(item["donor_id"], item["cell_type"]): item for item in details["membership"]}
    assert membership[("d0", "T")]["cell_ids"] == ["c0", "c1", "c2"]
    assert membership[("d0", "T")]["specimen_ids"] == ["s00", "s01"]
    assert membership[("d0", "T")]["cell_count"] == 3
    assert membership[("d1", "T")]["cell_count"] == 4
    assert details["unavailable_donor_type_combinations"] == [
        {"donor_id": "d1", "condition": "B", "cell_type": "B"}]
    assert details["missing_groups_filled_with_zeros"] is False
    assert details["uncertain_cells_not_used_for_type_specific_model"] == 1
    assert details["unit_audit"]["biological_unit_count"] == 2
    assert actual.expression.flags.writeable is False
    assert data.metadata.equals(before)


def _paired_observed():
    records = [dict(donor_id=f"d{i}", condition=condition,
                    specimen_id=f"s{i}{condition}", library_id=f"l{i}{condition}",
                    capture_batch="x" if i < 2 else "y")
               for i in range(4) for condition in ("A", "B")]
    return _observed([[10., 12., 30., 31., 5., 9., 50., 55.]], records, measurement="continuous")


def test_complete_paired_design_matches_within_donor_ols_reference():
    from scipy.stats import t, ttest_rel
    from devbench.study.biology_tools_bulk import _fit_family, _linear_estimate
    from devbench.study.biology_tools_singlecell import _design

    # An isolated linear-model check of the donor-blocked design, not an
    # assertion that the registered cellular count route admits OLS inputs.
    data = _paired_observed()
    design, prediction, details = _design(data, {}, ["A", "B"])
    expected_design = [[1, 0, 0, 0, 0], [1, 1, 0, 0, 0],
                       [1, 0, 1, 0, 0], [1, 1, 1, 0, 0],
                       [1, 0, 0, 1, 0], [1, 1, 0, 1, 0],
                       [1, 0, 0, 0, 1], [1, 1, 0, 0, 1]]
    np.testing.assert_array_equal(design, expected_design)
    np.testing.assert_array_equal(prediction[1] - prediction[0], [0, 1, 0, 0, 0])
    assert details["paired_donor_blocking"] is True
    assert details["independent_donors_per_condition"] == {"A": 4, "B": 4}
    assert details["residual_df"] == 3
    assert len(details["nuisance_levels_represented_by_donor_block"]) == 1
    fits, failures = _fit_family(data, design, {})
    assert not failures
    fitted = fits["g0"]
    np.testing.assert_allclose(fitted.params, [9.5, 3, 19.5, -4, 41.5], rtol=1e-12, atol=1e-12)
    # Paired differences (2,1,4,5) have mean 3 and sample variance 10/3.
    assert fitted.scale == pytest.approx(5 / 3)
    assert np.asarray(fitted.cov_params())[1, 1] == pytest.approx(5 / 6)
    values, intervals, p_value = _linear_estimate(fitted, prediction[1] - prediction[0], discrete=False)
    independent = ttest_rel([12, 31, 9, 55], [10, 30, 5, 50])
    half_width = t.ppf(0.975, 3) * math.sqrt(5 / 6)
    assert values == pytest.approx([3.0])
    assert (intervals[0]["lower"], intervals[0]["upper"]) == pytest.approx((3 - half_width, 3 + half_width))
    assert p_value == pytest.approx(independent.pvalue)


@pytest.mark.parametrize("layout,reason", [
    ("missing_pair", "incomplete_repeated_donor_covariance_model_not_implemented"),
    ("duplicate", "multiple_rows_per_donor_group_covariance_model_not_implemented"),
])
def test_paired_design_does_not_promote_incomplete_or_duplicate_rows(layout, reason):
    from devbench.study.biology_tools_singlecell import _design, _MethodUnavailable

    data = _paired_observed()
    indices = list(range(7)) if layout == "missing_pair" else [*range(8), 0]
    changed = replace(data, expression=data.expression[:, indices],
                      metadata=data.metadata.iloc[indices].copy(),
                      sample_ids=tuple(data.sample_ids[index] for index in indices))
    with pytest.raises(_MethodUnavailable, match=reason):
        _design(changed, {}, ["A", "B"])


@pytest.mark.parametrize("reverse_family", [False, True])
def test_bh_ties_and_missing_estimates_keep_the_complete_attempted_family(reverse_family):
    from devbench.study.biology_tools_bulk import _bh

    rows = [{"hypothesis_id": name, "p_value": p, "q_value": p, "estimate_status": "estimated"}
            for name, p in [("mid", .04), ("tie_a", .01), ("high", .8), ("zero", 0.), ("tie_b", .01)]]
    failed = {"hypothesis_id": "failed", "p_value": None, "q_value": None,
              "estimate_status": "failed", "missing_reason": "fixture_fit_unavailable"}
    rows.insert(1, deepcopy(failed))
    family = ["missing_row", "tie_b", "high", "failed", "zero", "mid", "tie_a"]
    tested = _bh(rows, hypothesis_ids=family[::-1] if reverse_family else family)
    # Sorted internal p=(0,.01,.01,.04,.8,1,1). Reverse cumulative minima
    # of 7*p/rank give (0,7/300,7/300,.07,1,1,1).
    expected = {"zero": 0., "tie_a": 7 / 300, "tie_b": 7 / 300, "mid": .07, "high": 1.}
    assert tested == ["mid", "tie_a", "high", "zero", "tie_b"]
    assert len(rows) == 6  # The omitted attempt is bookkeeping, not an invented estimate.
    assert rows[1] == failed
    for row in rows:
        if row["hypothesis_id"] in expected:
            assert row["q_value"] == pytest.approx(expected[row["hypothesis_id"]])
            assert row["p_value"] <= row["q_value"] <= 1


@pytest.mark.parametrize("use_prior", [False, True])
def test_dispersion_retry_retains_independent_likelihood_and_penalties(use_prior):
    from devharness_server.normalization import _retry_dispersion

    counts = np.array([1, 2, 4, 8, 16, 32, 4, 8])
    mu = np.full(8, counts.mean())
    alpha_hat, prior_variance = .2, .5
    recovered = _retry_dispersion(counts, np.ones((8, 1)), mu, alpha_hat,
        1e-4, 5., prior_disp_var=prior_variance, cr_reg=True, prior_reg=use_prior)
    assert recovered is not None
    alpha, details = recovered

    def independent(log_alpha):
        a = math.exp(log_alpha)
        size = 1 / a
        loss = -sum(math.lgamma(y + size) - math.lgamma(size) - math.lgamma(y + 1)
            + size * math.log(size / (size + m)) + y * math.log(m / (size + m))
            for y, m in zip(counts, mu, strict=True))
        # For an intercept-only design, the Cox-Reid determinant is a scalar.
        loss += .5 * math.log(sum(m / (1 + a * m) for m in mu))
        if use_prior:
            loss += (log_alpha - math.log(alpha_hat)) ** 2 / (2 * prior_variance)
        return loss

    log_alpha = math.log(alpha)
    objective = independent(log_alpha)
    assert details["objective"] == pytest.approx(objective, abs=1e-8)
    assert details["prior_penalty_included"] is use_prior
    assert 1e-4 < alpha < 5.
    # Independent dense-grid and local-stationarity checks, not the retry's
    # own reported 129-point grid or PyDESeq2's nb_nll implementation.
    candidates = [independent(x) for x in np.linspace(math.log(1e-4), math.log(5.), 2049)]
    assert objective <= min(candidates) + 1e-8
    h = 1e-4
    derivative = (independent(log_alpha + h) - independent(log_alpha - h)) / (2 * h)
    assert abs(derivative) < 1e-4
