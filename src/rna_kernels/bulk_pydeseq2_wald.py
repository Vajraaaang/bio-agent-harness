"""Python-only PyDESeq2 fallback for a frozen independent two-level contrast."""

from importlib.metadata import version

import numpy as np
import pandas as pd

from .streaming_rle import streaming_median_ratio_factors

_MAX_PANDAS_MATRIX_BYTES = 100_000_000
_MAX_SAMPLES = 4096
_MAX_EXACT_COUNT = 2**53 - 1
_MIN_MEAN = 1.0


def _axis(labels, size, name):
    values = tuple(labels)
    if len(values) != size or any(
        type(value) is not str or not value.strip() for value in values
    ):
        raise ValueError(f"Require complete nonblank {name} labels")
    return values


def _frozen_design(conditions, units, samples, numerator, denominator, design):
    conditions = _axis(conditions, samples, "condition")
    units = _axis(units, samples, "biological unit")
    if len(set(units)) != samples:
        raise ValueError(
            "Python-only Wald requires unique independent biological units"
        )
    if numerator == denominator or set(conditions) != {numerator, denominator}:
        raise ValueError("Require exactly the frozen numerator and denominator")
    target = np.asarray(conditions) == numerator
    if min(np.count_nonzero(target), np.count_nonzero(~target)) < 3:
        raise ValueError(
            "Python-only Wald requires at least three biological units per condition"
        )
    compiled = np.column_stack((np.ones(samples), target))
    if design is not None and not np.array_equal(design, compiled):
        raise ValueError(
            "Python-only Wald design must be the intercept and directed "
            "condition coefficient"
        )
    return units, target, compiled


def _validate_counts(counts):
    if not isinstance(counts, np.ndarray) or counts.ndim != 2 or not all(counts.shape):
        raise ValueError("Require a nonempty gene-by-sample NumPy count matrix")
    if counts.shape[1] > _MAX_SAMPLES or counts.size * 8 > _MAX_PANDAS_MATRIX_BYTES:
        raise ValueError(
            "Counts exceed the bounded Python-only pandas fit; use a mounted kernel"
        )
    if (
        counts.dtype.kind not in "iuf"
        or not np.isfinite(counts).all()
        or (counts < 0).any()
        or (counts > _MAX_EXACT_COUNT).any()
        or counts.dtype.kind == "f"
        and not np.equal(counts, np.floor(counts)).all()
    ):
        raise ValueError(
            "Python-only Wald requires finite nonnegative exactly representable "
            "raw integers"
        )
    if not np.any(counts > 0, axis=0).all():
        raise ValueError(
            "Python-only Wald requires a nonempty library for each biological unit"
        )


def _fit_wald(counts, genes, units, design, factors):
    from pydeseq2.dds import DeseqDataSet
    from pydeseq2.ds import DeseqStats

    matrix = pd.DataFrame(
        counts.T.astype(np.int64), index=list(units), columns=list(genes)
    )
    model = pd.DataFrame(
        design, index=list(units), columns=["Intercept", "condition_coef"]
    )
    dds = DeseqDataSet(
        counts=matrix,
        metadata=pd.DataFrame(index=list(units)),
        design=model,
        fit_type="mean",
        refit_cooks=False,
        quiet=True,
        low_memory=True,
        n_cpus=1,
    )
    # Set a common, audited full-family RLE reference before the dispersion fit.
    dds.obs["size_factors"] = factors
    dds.layers["normed_counts"] = dds.X / factors[:, None]
    dds.var["_normed_means"] = dds.layers["normed_counts"].mean(axis=0)
    dds.fit_genewise_dispersions()
    dds.fit_dispersion_trend()
    dds.fit_dispersion_prior()
    dds.fit_MAP_dispersions()
    dds.fit_LFC()
    dds.calculate_cooks()
    outlier = np.asarray(dds.cooks_outlier(), dtype=bool)
    contrast = np.array([0.0, 1.0])
    stats = DeseqStats(
        dds,
        contrast=contrast,
        independent_filter=False,
        cooks_filter=False,
        quiet=True,
        n_cpus=1,
    )
    stats.run_wald_test()
    effects = np.asarray(stats.LFC @ contrast / np.log(2))
    probabilities = stats.p_values.to_numpy()
    valid = (
        np.isfinite(effects)
        & np.isfinite(probabilities)
        & (probabilities >= 0)
        & (probabilities <= 1)
        & np.isfinite(stats.SE.to_numpy())
        & (stats.SE.to_numpy() > 0)
        & ~outlier
    )
    for field in ("_genewise_converged", "_MAP_converged", "_LFC_converged"):
        valid &= dds.var[field].to_numpy(dtype=bool, na_value=False)
    return effects, probabilities, valid


def _family_bh(probabilities, eligible):
    padded = np.where(eligible, probabilities, 1.0)
    order = np.argsort(padded, kind="stable")
    ranked = padded[order] * len(padded) / np.arange(1, len(padded) + 1)
    adjusted = np.full_like(probabilities, np.nan)
    adjusted[order] = np.minimum(1, np.minimum.accumulate(ranked[::-1])[::-1])
    return np.where(eligible, np.maximum(probabilities, adjusted), np.nan)


def fit_pydeseq2_wald(
    counts,
    gene_ids,
    conditions,
    unit_ids,
    *,
    numerator,
    denominator,
    measurement,
    test="wald",
    design=None,
    scratch_dir=None,
):
    """Fit one coefficient and return effects, p-values, BH q-values and evidence.

    The original measured feature order is preserved. Codes are 0 (tested),
    1 (label-blind low count), 2 (zero marginal in a condition), and 3
    (fit/statistic unavailable). Untested rows have NaN internally and should
    be serialized as null. Non-tested rows occupy conservative BH positions.
    """
    if test != "wald" or measurement not in {
        "raw_counts",
        "umi_counts",
        "non_umi_counts",
    }:
        raise ValueError(
            "Python-only fallback supports a single Wald coefficient on declared "
            "raw counts"
        )
    _validate_counts(counts)
    genes = _axis(gene_ids, counts.shape[0], "feature")
    if len(set(genes)) != len(genes):
        raise ValueError("Require unique measured feature labels")
    units, target, compiled = _frozen_design(
        conditions,
        unit_ids,
        counts.shape[1],
        numerator,
        denominator,
        design,
    )
    factors, normalization = streaming_median_ratio_factors(
        lambda: (counts[start : start + 128] for start in range(0, len(genes), 128)),
        counts.shape[1],
        scratch_dir=scratch_dir,
    )
    separated = ~np.any(counts[:, target], axis=1) | ~np.any(counts[:, ~target], axis=1)
    high_count = np.mean(counts / factors, axis=1) >= _MIN_MEAN
    keep = ~separated & high_count
    codes = np.where(separated, 2, np.where(keep, 3, 1)).astype(np.int8)
    effects = np.full(len(genes), np.nan)
    probabilities = np.full(len(genes), np.nan)
    if keep.any():
        selected = np.flatnonzero(keep)
        fitted, p_values, valid = _fit_wald(
            counts[selected],
            [genes[index] for index in selected],
            units,
            compiled,
            factors,
        )
        codes[selected[valid]] = 0
        effects[selected[valid]] = fitted[valid]
        probabilities[selected[valid]] = p_values[valid]
    adjusted = _family_bh(probabilities, codes == 0)
    return (
        effects,
        probabilities,
        adjusted,
        codes,
        {
            "solver": "pydeseq2_wald",
            "solver_version": version("pydeseq2"),
            "biological_unit_identity": "declared",
            "independence_verified": False,
            "dispersion_trend": "mean",
            "direction": {"numerator": numerator, "denominator": denominator},
            "normalization": {**normalization, "size_factors": factors.tolist()},
            "low_count_filter": "mean_RLE_normalized_counts_at_least_one",
            "cooks_outlier_policy": "withhold_without_count_replacement",
            "multiplicity": "BH_complete_family_internal_p_one_for_untested",
        },
    )
