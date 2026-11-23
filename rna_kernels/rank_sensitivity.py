"""Frozen two-level rank sensitivity over independent, declared biological units."""

from dataclasses import dataclass, field

import numpy as np
from scipy import stats

_MIN_UNITS = 100
_MAX_UNITS = 4096
_MIN_ARM = 3
_MAX_PERMUTATIONS = 4095
_MAX_BLOCK_ENTRIES = 4 * 1024**2


@dataclass(frozen=True)
class RankSensitivityPlan:
    """One design and permutation schedule for the entire measured family."""

    method: str
    n_units: int
    numerator: str
    denominator: str
    seed: int
    permutations: int
    strata_count: int
    numeric_covariate_count: int
    _target: np.ndarray = field(repr=False, compare=False)
    _groups: tuple[np.ndarray, ...] = field(repr=False, compare=False)
    _q_nuisance: np.ndarray | None = field(default=None, repr=False, compare=False)
    _contrast: np.ndarray | None = field(default=None, repr=False, compare=False)
    _weights: np.ndarray | None = field(default=None, repr=False, compare=False)

    def evidence(self):
        permutation = self.method == "freedman_lane"
        effect = (
            "adjusted_mean_rank_percentile_difference"
            if permutation
            else "weighted_probability_of_superiority_minus_one_half"
        )
        return {
            "method": self.method,
            "n_units": self.n_units,
            "direction": {"numerator": self.numerator, "denominator": self.denominator},
            "strata_count": self.strata_count,
            "numeric_covariate_count": self.numeric_covariate_count,
            "permutations": self.permutations,
            "seed": self.seed if permutation else None,
            "minimum_permutation_p": 1 / (1 + self.permutations)
            if permutation
            else None,
            "exchangeability": (
                "within_frozen_strata" if self.strata_count else "across_declared_units"
            )
            if permutation
            else None,
            "reference": (
                "approximate_nuisance_residual_permutation_on_average_ranks"
                if permutation
                else "tie_adjusted_asymptotic_normal"
            ),
            "statistic": "two_sided_absolute_condition_score"
            if permutation
            else "two_sided_rank_sum",
            "effect_scale": effect,
            "independence_verified": False,
        }


def _is_label(value):
    return isinstance(value, str) and bool(value.strip())


def _target_axis(conditions, unit_ids, numerator, denominator):
    labels = tuple(conditions)
    units = tuple(unit_ids)
    if not _MIN_UNITS <= len(labels) <= _MAX_UNITS or len(units) != len(labels):
        raise ValueError(
            "Rank sensitivity requires 100 to 4096 aligned biological units"
        )
    if not all(_is_label(unit) for unit in units) or len(set(units)) != len(units):
        raise ValueError(
            "Rank sensitivity requires unique declared biological-unit labels"
        )
    if (
        not _is_label(numerator)
        or not _is_label(denominator)
        or numerator == denominator
        or not all(_is_label(label) for label in labels)
        or set(labels) != {numerator, denominator}
    ):
        raise ValueError(
            "Rank sensitivity requires exactly the two directed condition levels"
        )
    target = np.asarray(labels) == numerator
    if min(int(target.sum()), int((~target).sum())) < _MIN_ARM:
        raise ValueError("Rank sensitivity requires three biological units per arm")
    return target


def _stratum_key(value):
    values = value if isinstance(value, tuple) else (value,)
    if not values or not all(
        _is_label(label)
        or isinstance(label, (int, np.integer))
        and not isinstance(label, bool)
        for label in values
    ):
        raise ValueError("Rank sensitivity requires observed discrete stratum labels")
    return values


def _strata_groups(strata, size):
    if strata is None:
        return (np.arange(size),)
    values = tuple(strata)
    if len(values) != size:
        raise ValueError("Rank sensitivity strata must align with all biological units")
    groups: dict[tuple, list[int]] = {}
    for index, value in enumerate(values):
        groups.setdefault(_stratum_key(value), []).append(index)
    return tuple(np.asarray(indices) for indices in groups.values())


def _numeric_covariates(covariates, size):
    if covariates is None:
        return np.empty((size, 0))
    raw = np.asarray(covariates)
    if raw.ndim == 1:
        raw = raw[:, None]
    if (
        raw.ndim != 2
        or raw.shape[0] != size
        or not 0 < raw.shape[1] < size
        or raw.dtype.kind not in "iuf"
        or not np.isfinite(raw).all()
    ):
        raise ValueError(
            "Rank sensitivity requires finite, aligned continuous covariates"
        )
    clean = raw.astype(float)
    magnitude = np.max(np.abs(clean), axis=0)
    clean /= np.where(magnitude > 0, magnitude, 1)
    clean -= clean.mean(axis=0)
    norm = np.linalg.norm(clean, axis=0)
    return clean / np.where(norm > 0, norm, 1)


def _permutation_plan(target, groups, numeric, permutations, seed):
    samples = len(target)
    nuisance_columns = len(groups) + numeric.shape[1]
    if samples <= nuisance_columns + 1:
        raise ValueError("Rank sensitivity design has no residual degrees of freedom")
    nuisance = np.zeros((samples, nuisance_columns))
    for column, group in enumerate(groups):
        nuisance[group, column] = 1 / np.sqrt(len(group))
    nuisance[:, len(groups) :] = numeric
    full = np.column_stack((nuisance, target / np.linalg.norm(target)))
    if np.linalg.matrix_rank(full) != full.shape[1]:
        raise ValueError(
            "Rank sensitivity condition and nuisance design is rank deficient"
        )
    if np.linalg.cond(full) > 1000:
        raise ValueError(
            "Rank sensitivity condition and nuisance design exceeds conditioning bound"
        )
    if not any(target[group].any() and not target[group].all() for group in groups):
        raise ValueError(
            "Rank sensitivity permutation requires an exchangeable mixed stratum"
        )
    nuisance_q = np.linalg.qr(nuisance, mode="reduced")[0]
    contrast = target - nuisance_q @ (nuisance_q.T @ target)
    rng = np.random.Generator(np.random.PCG64(seed))
    weights = np.empty((permutations, samples))
    for row in weights:
        for group in groups:
            row[rng.permutation(group)] = contrast[group]
    return nuisance_q, contrast, weights


def prepare_rank_sensitivity(
    conditions,
    unit_ids,
    *,
    numerator,
    denominator,
    strata=None,
    covariates=None,
    permutations=999,
    seed=0,
):
    """Freeze routing, nuisance projections and the common permutation schedule.

    Biological-unit IDs, strata, and numeric covariates follow the exact column
    order of every caller-supplied expression block. Multiple discrete factors
    can be encoded as a tuple per unit. Permutations stay inside these strata.
    """
    if type(permutations) is not int or not 19 <= permutations <= _MAX_PERMUTATIONS:
        raise ValueError("Rank sensitivity requires 19 to 4095 requested permutations")
    if type(seed) is not int or not 0 <= seed < 2**64:
        raise ValueError("Rank sensitivity seed must be a nonnegative 64-bit integer")
    target = _target_axis(conditions, unit_ids, numerator, denominator)
    size = len(target)
    groups = _strata_groups(strata, size)
    numeric = _numeric_covariates(covariates, size)
    covariate_count = numeric.shape[1]
    method = "freedman_lane"
    nuisance = contrast = weights = None
    if strata is None and covariates is None and target.sum() * 2 == size:
        method = "wilcoxon"
    elif (
        strata is not None
        and covariates is None
        and all(
            _MIN_ARM <= target[group].sum() <= len(group) - _MIN_ARM for group in groups
        )
    ):
        method = "van_elteren"
    else:
        nuisance, contrast, weights = _permutation_plan(
            target, groups, numeric, permutations, seed
        )
    for array in (target, *groups, nuisance, contrast, weights):
        if array is not None:
            array.setflags(write=False)
    return RankSensitivityPlan(
        method,
        size,
        numerator,
        denominator,
        seed,
        permutations if weights is not None else 0,
        len(groups) if strata is not None else 0,
        covariate_count,
        target,
        groups,
        nuisance,
        contrast,
        weights,
    )


def _asymptotic_rank_test(plan, values):
    score = np.zeros(len(values))
    variance = np.zeros(len(values))
    weighted_pairs = 0.0
    for indices in plan._groups:
        target = plan._target[indices]
        total = len(indices)
        pairs = target.sum() * (~target).sum()
        weight = 1 / (total + 1) if plan.method == "van_elteren" else 1
        centered_ranks = stats.rankdata(values[:, indices], axis=1) - (total + 1) / 2
        score += weight * centered_ranks[:, target].sum(axis=1)
        variance += (
            weight**2
            * pairs
            / (total * (total - 1))
            * np.sum(centered_ranks**2, axis=1)
        )
        weighted_pairs += weight * pairs
    valid = variance > 0
    p = np.full(len(values), np.nan)
    continuity = 0.5 if plan.method == "wilcoxon" else 0.0
    absolute_score = np.maximum(np.abs(score[valid]) - continuity, 0)
    p[valid] = 2 * stats.norm.sf(absolute_score / np.sqrt(variance[valid]))
    effect = np.where(valid, score / weighted_pairs, np.nan)
    return effect, p


def _freedman_lane_test(plan, values):
    nuisance, contrast, weights = plan._q_nuisance, plan._contrast, plan._weights
    assert nuisance is not None and contrast is not None and weights is not None
    ranks = stats.rankdata(values, axis=1) / plan.n_units
    residual = ranks - (ranks @ nuisance) @ nuisance.T
    # The projected condition is orthogonal to the fitted nuisance term.
    # Its dot product with each reconstructed outcome thus reduces to the
    # dot product with permuted residuals alone.
    observed = np.einsum("ij,j->i", residual, contrast)
    exceedances = np.zeros(len(values), dtype=int)
    tolerance = (
        64
        * np.finfo(float).eps
        * np.linalg.norm(residual, axis=1)
        * np.linalg.norm(contrast)
    )
    threshold = np.abs(observed) - tolerance
    for start in range(0, len(weights), 128):
        scores = residual @ weights[start : start + 128].T
        exceedances += (np.abs(scores) >= threshold[:, None]).sum(axis=1)
    valid = np.linalg.norm(residual, axis=1) > (
        64 * np.finfo(float).eps * np.linalg.norm(ranks, axis=1)
    )
    effect = np.where(valid, observed / (contrast @ contrast), np.nan)
    probabilities = np.where(valid, (1 + exceedances) / (1 + plan.permutations), np.nan)
    return effect, probabilities


def test_rank_block(plan: RankSensitivityPlan, values: np.ndarray):
    """Return signed effects, nominal p-values and codes in original row order.

    The caller owns the expression scale. For declared counts it should supply
    log1p-RLE values. Missing data or zero residual rank information withhold
    the entire affected hypothesis (code 3) without changing the cohort.
    """
    if (
        not isinstance(values, np.ndarray)
        or values.ndim != 2
        or values.shape[1] != plan.n_units
        or values.size > _MAX_BLOCK_ENTRIES
        or values.dtype.kind not in "iuf"
    ):
        raise ValueError(
            "Rank sensitivity requires a bounded numeric gene-by-unit block"
        )
    effects = np.full(len(values), np.nan)
    probabilities = np.full(len(values), np.nan)
    selected = np.isfinite(values).all(axis=1) & (
        values.max(axis=1) > values.min(axis=1)
    )
    if selected.any():
        method = (
            _freedman_lane_test
            if plan.method == "freedman_lane"
            else _asymptotic_rank_test
        )
        effects[selected], probabilities[selected] = method(plan, values[selected])
    codes = np.where(np.isfinite(probabilities), 0, 3).astype(np.int8)
    return effects, probabilities, codes


def rank_family_bh(probabilities, codes):
    """Adjust the complete measured family, padding unavailable rows as internal p=1."""
    p = np.asarray(probabilities, dtype=float)
    flags = np.asarray(codes)
    if p.ndim != 1 or flags.shape != p.shape or not np.isin(flags, (0, 3)).all():
        raise ValueError(
            "Rank sensitivity BH requires aligned complete-family probabilities "
            "and codes"
        )
    eligible = flags == 0
    if (
        not np.isfinite(p[eligible]).all()
        or (p[eligible] < 0).any()
        or (p[eligible] > 1).any()
        or not np.isnan(p[~eligible]).all()
    ):
        raise ValueError(
            "Rank sensitivity BH received invalid probability or unavailable row"
        )
    padded = np.where(eligible, p, 1.0)
    order = np.argsort(padded, kind="stable")
    ranks = padded[order] * len(padded) / np.arange(1, len(padded) + 1)
    result = np.empty_like(p)
    result[order] = np.minimum(1, np.minimum.accumulate(ranks[::-1])[::-1])
    return np.where(eligible, np.maximum(p, result), np.nan)
