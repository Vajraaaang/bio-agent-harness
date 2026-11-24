"""Synthetic checks for the Python-only one-coefficient Wald fallback."""

import numpy as np
import pytest

from rna_kernels.bulk_pydeseq2_wald import fit_pydeseq2_wald


@pytest.fixture
def cohort():
    rng = np.random.default_rng(412)
    counts = rng.negative_binomial(30, 0.5, size=(180, 12))
    counts[:15, 6:] *= 3
    counts[0, :6] = 0
    counts[1, :] = 0
    counts[2, :] = [1] + [0] * 11
    return (
        counts,
        [f"g{i}" for i in range(len(counts))],
        ["A"] * 6 + ["B"] * 6,
        [f"d{i}" for i in range(12)],
    )


def test_actual_wald_tests_on_off_gene_and_adjusts_eligible_family(cohort):
    counts, genes, labels, units = cohort
    effect, p, q, codes, evidence = fit_pydeseq2_wald(
        counts,
        genes,
        labels,
        units,
        numerator="B",
        denominator="A",
        measurement="raw_counts",
    )
    assert len(effect) == len(p) == len(q) == len(codes) == len(genes)
    assert codes[:3].tolist() == [0, 1, 1]
    assert effect[0] > 1 and p[0] < 0.01
    assert np.isnan(np.vstack((effect, p, q))[:, codes != 0]).all()
    assert (codes == 0).sum() > 145
    assert np.nanmedian(effect[3:15]) > 1.3
    valid = codes == 0
    eligible = codes != 1
    padded = np.where(valid[eligible], p[eligible], 1.0)
    order = np.argsort(padded)
    expected = np.empty_like(padded)
    expected[order] = np.minimum(
        1,
        np.minimum.accumulate(
            (padded[order] * len(padded) / np.arange(1, len(padded) + 1))[::-1]
        )[::-1],
    )
    np.testing.assert_allclose(q[valid], expected[valid[eligible]])
    assert np.all((0 <= p[valid]) & (p[valid] <= q[valid]) & (q[valid] <= 1))
    assert evidence["solver"] == "pydeseq2_wald"
    assert evidence["solver_version"]
    assert evidence["normalization"]["scope"] == "complete_declared_gene_family"
    assert evidence["normalization"]["reference_gene_count"] == len(genes) - 3
    assert evidence["normalization"]["size_factors"]


def test_wald_pads_only_failed_eligible_hypotheses(cohort, monkeypatch):
    counts, genes, labels, units = cohort
    counts = counts[:5]

    def synthetic_fit(fitted_counts, *args):
        np.testing.assert_array_equal(fitted_counts, counts[[0, 3, 4]])
        return np.ones(3), np.array([0.01, 0.04, 0.001]), np.array([True, True, False])

    monkeypatch.setattr("rna_kernels.bulk_pydeseq2_wald._fit_wald", synthetic_fit)
    _, p, q, codes, _ = fit_pydeseq2_wald(
        counts,
        genes[:5],
        labels,
        units,
        numerator="B",
        denominator="A",
        measurement="raw_counts",
    )
    assert codes.tolist() == [0, 1, 1, 0, 3]
    np.testing.assert_allclose(q, [0.03, np.nan, np.nan, 0.06, np.nan])
    assert np.isnan(p[codes != 0]).all()


@pytest.mark.parametrize(
    "case", ["lrt", "repeated", "scale", "direction", "multi", "integer", "label"]
)
def test_rejects_scope_before_fit(cohort, case):
    counts, genes, labels, units = cohort
    kwargs = dict(numerator="B", denominator="A", measurement="raw_counts")
    passed = np.column_stack((np.ones(12), [0] * 6 + [1] * 6))
    if case == "lrt":
        kwargs["test"] = "lrt"
    elif case == "repeated":
        units[-1] = units[0]
    elif case == "scale":
        kwargs["measurement"] = "continuous"
    elif case == "direction":
        kwargs["design"] = passed[::-1]
    elif case == "multi":
        kwargs["design"] = np.column_stack((passed, np.arange(12)))
    elif case == "integer":
        counts = counts.astype(float)
        counts[0, 0] = 0.5
    else:
        labels[-1] = "C"
    with pytest.raises(ValueError):
        fit_pydeseq2_wald(counts, genes, labels, units, **kwargs)
