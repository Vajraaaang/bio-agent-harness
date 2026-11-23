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
    return (
        counts,
        [f"g{i}" for i in range(len(counts))],
        ["A"] * 6 + ["B"] * 6,
        [f"d{i}" for i in range(12)],
    )


def test_actual_wald_keeps_entire_bh_universe(cohort):
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
    assert codes[:2].tolist() == [2, 2]
    assert np.isnan(np.vstack((effect, p, q))[:, codes != 0]).all()
    assert (codes == 0).sum() > 145
    assert np.nanmedian(effect[2:15]) > 1.3
    valid = codes == 0
    padded = np.where(valid, p, 1.0)
    order = np.argsort(padded)
    expected = np.empty_like(p)
    expected[order] = np.minimum(
        1,
        np.minimum.accumulate(
            (padded[order] * len(p) / np.arange(1, len(p) + 1))[::-1]
        )[::-1],
    )
    np.testing.assert_allclose(q[valid], expected[valid])
    assert np.all((0 <= p[valid]) & (p[valid] <= q[valid]) & (q[valid] <= 1))
    assert evidence["solver"] == "pydeseq2_wald"
    assert evidence["solver_version"]
    assert evidence["normalization"]["scope"] == "complete_declared_gene_family"
    assert evidence["normalization"]["reference_gene_count"] == len(genes) - 2
    assert evidence["normalization"]["size_factors"]


def test_zero_condition_marginal_holds_every_gene_without_fitting(cohort, monkeypatch):
    _, _, labels, units = cohort
    counts = np.array([[5] * 6 + [0] * 6, [0] * 6 + [11] * 6])

    def unexpected_fit(*args, **kwargs):
        pytest.fail("The Wald estimator received a zero-marginal row")

    monkeypatch.setattr("rna_kernels.bulk_pydeseq2_wald._fit_wald", unexpected_fit)
    effect, p, q, codes, evidence = fit_pydeseq2_wald(
        counts,
        ["first", "second"],
        labels,
        units,
        numerator="B",
        denominator="A",
        measurement="raw_counts",
    )
    assert codes.tolist() == [2, 2]
    assert np.isnan(np.vstack((effect, p, q))).all()
    assert evidence["normalization"]["method"] == "median_of_ratios_poscounts"


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
