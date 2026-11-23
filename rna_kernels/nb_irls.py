"""Batched weighted systems for negative-binomial IRLS on the host."""

from functools import cached_property

import numpy as np


def _solve_system(matrix, rhs):
    """Solve independent systems without losing a batch to one singular gene."""
    if matrix.shape[-1] == 1:
        with np.errstate(divide="ignore", invalid="ignore"):
            return rhs / matrix[..., 0]
    try:
        return np.linalg.solve(matrix, rhs[..., None])[..., 0]
    except np.linalg.LinAlgError:
        result = np.full_like(rhs, np.nan)
        for index in range(len(rhs)):
            try:
                result[index] = np.linalg.solve(matrix[index], rhs[index])
            except np.linalg.LinAlgError:
                continue
        return result


def _invert_systems(systems):
    try:
        return np.linalg.inv(systems)
    except np.linalg.LinAlgError:
        # Retry separately so a singular gene cannot discard its neighbors.
        covariance = np.full_like(systems, np.nan)
        for index, system in enumerate(systems):
            try:
                covariance[index] = np.linalg.inv(system)
            except np.linalg.LinAlgError:
                continue
        return covariance


class FixedEffectDesign:
    """Fit weighted systems using disjoint unit columns and a small Schur system."""

    def __init__(self, matrix, indicators, reference, unit_ids):
        self.matrix = matrix
        self.size = matrix.shape[1]
        self.unit_ids = unit_ids
        self.indicators = indicators
        self.reference = reference
        self.target_indices = np.asarray(
            [i for i in range(self.size) if i != reference and i not in indicators],
            dtype=np.intp,
        )
        self.targets = matrix[:, self.target_indices]
        self.unit_count = int(np.max(unit_ids)) + 1
        order = np.argsort(unit_ids, kind="stable")
        self.order = None if np.array_equal(order, np.arange(len(order))) else order
        group_sizes = np.bincount(unit_ids)
        self.starts = np.r_[0, np.cumsum(group_sizes[:-1])]
        self.group_width = (
            int(group_sizes[0]) if np.all(group_sizes == group_sizes[0]) else None
        )
        self.sorted_targets = self._ordered(self.targets.T).T
        self.target_products = self._cross_terms(self.sorted_targets)

    @classmethod
    def from_design(cls, design):
        matrix = np.asarray(design, dtype=float)
        columns = getattr(design, "columns", ())
        indices = np.asarray(
            [
                i
                for i, name in enumerate(columns)
                if str(name).startswith(("biological_unit_", "fixed_block_"))
            ],
            dtype=np.intp,
        )
        if matrix.ndim != 2 or not len(indices) or not np.isfinite(matrix).all():
            return None
        candidates = matrix[:, indices]
        if (
            not np.all((candidates == 0) | (candidates == 1))
            or np.any(candidates.sum(axis=0) == 0)
            or np.any(candidates.sum(axis=1) > 1)
        ):
            return None
        complement = candidates.sum(axis=1) == 0
        reference = None
        if np.any(complement):
            intercepts = np.flatnonzero(np.all(matrix == 1, axis=0))
            if len(intercepts) != 1:
                return None
            reference = int(intercepts[0])
        # Keep the reference unit at index zero for the reference-coded case.
        mapping = (candidates @ np.arange(1, len(indices) + 1)).astype(np.intp)
        unit_ids = mapping if reference is not None else mapping - 1
        if matrix.shape[1] - (len(indices) + int(reference is not None)) > 16:
            return None
        return cls(matrix, indices, reference, unit_ids)

    @staticmethod
    def _cross_terms(targets):
        return (targets[:, :, None] * targets[:, None, :]).reshape(len(targets), -1)

    def _ordered(self, values):
        return values if self.order is None else values[:, self.order, ...]

    def _group_sum(self, values):
        if self.group_width is not None:
            return values.reshape(
                len(values),
                self.unit_count,
                self.group_width,
                *values.shape[2:],
            ).sum(axis=2)
        return np.add.reduceat(values, self.starts, axis=1)

    def _system(self, original_weights, ridge):
        weights = self._ordered(original_weights)
        diagonal = self._group_sum(weights)
        cross = self._group_sum(weights[:, :, None] * self.sorted_targets)
        num_targets = len(self.target_indices)
        hessian = (weights @ self.target_products).reshape(
            len(weights), num_targets, num_targets
        )
        if ridge and self.reference is not None:
            # With original beta = (u0, uj-u0), ridge couples each donor to the
            # reference only. Keep u0 in the small system and eliminate uj.
            regularized = np.zeros((len(weights), num_targets + 1, num_targets + 1))
            regularized[:, 0, 0] = diagonal[:, 0] + ridge * self.unit_count
            regularized[:, 0, 1:] = regularized[:, 1:, 0] = cross[:, 0]
            regularized[:, 1:, 1:] = hessian + ridge * np.eye(num_targets)
            ref_cross = np.full((*cross[:, 1:].shape[:2], 1), -ridge)
            cross = np.concatenate((ref_cross, cross[:, 1:]), axis=2)
            diagonal = diagonal[:, 1:] + ridge
            hessian = regularized
        elif ridge:
            diagonal = diagonal + ridge
            hessian = hessian + ridge * np.eye(num_targets)
        with np.errstate(divide="ignore", invalid="ignore"):
            ratios = cross / diagonal[:, :, None]
        schur = hessian - ratios.swapaxes(1, 2) @ cross
        return diagonal, ratios, schur

    def solve(self, weights, response, *, ridge=0):
        weighted = self._ordered(weights * response)
        unit_rhs = self._group_sum(weighted)
        target_rhs = weighted @ self.sorted_targets
        retained_reference = ridge and self.reference is not None
        if retained_reference:
            target_rhs = np.column_stack((unit_rhs[:, 0], target_rhs))
            unit_rhs = unit_rhs[:, 1:]
        diagonal, ratios, schur = self._system(weights, ridge)
        target_rhs -= (ratios.swapaxes(1, 2) @ unit_rhs[..., None])[..., 0]
        tail = _solve_system(schur, target_rhs)
        unit = unit_rhs / diagonal - (ratios @ tail[..., None])[..., 0]
        if retained_reference:
            unit = np.column_stack((tail[:, 0], unit))
            tail = tail[:, 1:]
        beta = np.empty((len(weights), self.size))
        beta[:, self.target_indices] = tail
        if self.reference is None:
            beta[:, self.indicators] = unit
        else:
            beta[:, self.reference] = unit[:, 0]
            beta[:, self.indicators] = unit[:, 1:] - unit[:, :1]
        return beta

    def predict(self, beta):
        if self.reference is None:
            unit = beta[:, self.indicators]
        else:
            reference = beta[:, self.reference]
            unit = np.column_stack(
                (reference, beta[:, self.indicators] + reference[:, None])
            )
        return unit[:, self.unit_ids] + beta[:, self.target_indices] @ self.targets.T

    def logdet(self, weights):
        diagonal, _, schur = self._system(weights, 0)
        sign, value = np.linalg.slogdet(schur)
        with np.errstate(divide="ignore", invalid="ignore"):
            return np.where(sign == 1, np.log(diagonal).sum(axis=1) + value, np.nan)

    def contrast_covariance(self, weights, contrasts):
        diagonal, ratios, schur = self._system(weights, 0)
        unit = contrasts[:, self.indicators]
        if self.reference is not None:
            unit = np.column_stack(
                (contrasts[:, self.reference] - unit.sum(axis=1), unit)
            )
        schur_covariance = _invert_systems(schur)
        target = contrasts[:, self.target_indices]
        if not np.any(unit):
            return target @ schur_covariance @ target.T
        reduced = target - unit @ ratios
        with np.errstate(divide="ignore", invalid="ignore"):
            unit_covariance = (unit / diagonal[:, None, :]) @ unit.T
        return unit_covariance + reduced @ schur_covariance @ reduced.swapaxes(1, 2)

    def empty_unit(self, counts):
        return np.any(self._group_sum(self._ordered(counts)) == 0, axis=1)


class DenseDesign:
    """Small full-rank general design with bounded batched Cholesky solves."""

    def __init__(self, matrix):
        self.matrix = np.asarray(matrix, dtype=float)
        self.size = self.matrix.shape[1]
        self.products = FixedEffectDesign._cross_terms(self.matrix)

    def system(self, weights):
        return (weights @ self.products).reshape(len(weights), self.size, self.size)

    def solve(self, weights, response):
        systems = self.system(weights)
        rhs = (weights * response) @ self.matrix
        try:
            factor = np.linalg.cholesky(systems)
        except np.linalg.LinAlgError:
            return _solve_system(systems, rhs)
        beta = _solve_system(factor, rhs)
        return _solve_system(factor.swapaxes(1, 2), beta)

    def predict(self, beta):
        return beta @ self.matrix.T

    def contrast_covariance(self, weights, contrasts):
        covariance = _invert_systems(self.system(weights))
        return contrasts @ covariance @ contrasts.T

    def empty_unit(self, counts):
        return np.zeros(len(counts), dtype=bool)


def _deviance(counts, mu, alpha):
    shifted = counts + 1 / alpha
    return 2 * np.sum(
        counts * np.log(np.maximum(counts / mu, np.finfo(float).eps))
        - shifted * np.log(shifted / (mu + 1 / alpha)),
        axis=1,
    )


class NBIRLSBatch:
    """Fitted coefficients and final *working* weights in the original design."""

    def __init__(self, design, counts, alpha, offset, params, weights, converged):
        self.design = design
        self.counts = counts
        self.alpha = alpha
        self.offset = offset
        self.params = params
        self.weights = weights
        self.converged = converged
        self.df_resid = design.matrix.shape[0] - design.size
        self._cached_projection = None

    def contrast(self, contrasts):
        """Return contrasts and covariance diagonals with one batched projection."""
        contrasts = np.asarray(contrasts)
        cached = self._cached_projection
        if cached is None or not np.array_equal(cached[0], contrasts):
            active = self.converged
            effects = self.params @ contrasts.T
            variance = np.full_like(effects, np.nan)
            covariance = self.design.contrast_covariance(
                self.weights[active], contrasts
            )
            variance[active] = np.diagonal(covariance, axis1=1, axis2=2)
            cached = (contrasts.copy(), effects, variance)
            self._cached_projection = cached
        return cached[1:]


class NBIRLSGene:
    """Conditional model result with lazily materialized dense covariance."""

    def __init__(self, batch, index):
        self.batch = batch
        self.index = index
        self.df_resid = batch.df_resid
        self.params = batch.params[index]

    @cached_property
    def covariance(self):
        design = self.batch.design
        return design.contrast_covariance(
            self.batch.weights[self.index : self.index + 1],
            np.eye(design.size),
        )[0]

    def cov_params(self):
        return self.covariance

    @cached_property
    def llf(self):
        from scipy.special import gammaln

        batch = self.batch
        gene = self.index
        mu = np.exp(batch.design.predict(self.params[None, :])[0] + batch.offset)
        counts, alpha = batch.counts[gene], batch.alpha[gene]
        inverse = 1 / alpha
        return float(
            np.sum(
                counts * np.log(alpha * mu)
                - (counts + inverse) * np.log1p(alpha * mu)
                + gammaln(counts + inverse)
                - gammaln(inverse)
                - gammaln(counts + 1),
            )
        )

    def _contrast_test(self, contrast, *, f_reference):
        from types import SimpleNamespace

        from scipy.stats import chi2, f

        matrix = np.atleast_2d(contrast)
        values = matrix @ self.params
        covariance = matrix @ self.covariance @ matrix.T
        df_num = np.linalg.matrix_rank(covariance)
        score = float(values @ np.linalg.pinv(covariance) @ values)
        if f_reference:
            return SimpleNamespace(
                statistic=score / df_num,
                df_num=df_num,
                df_denom=self.df_resid,
                pvalue=f.sf(score / df_num, df_num, self.df_resid),
            )
        return SimpleNamespace(
            statistic=score, df_denom=df_num, pvalue=chi2.sf(score, df_num)
        )

    def wald_test(self, contrast, *, scalar=True):
        return self._contrast_test(contrast, f_reference=False)

    def f_test(self, contrast):
        return self._contrast_test(contrast, f_reference=True)


def _irl_step(design, counts, mu, predictor, alpha, offset):
    weights = mu / (1 + alpha * mu)
    endog = predictor + (counts - mu) / mu - offset
    beta = design.solve(weights, endog)
    next_predictor = design.predict(beta) + offset
    valid = (
        np.isfinite(beta).all(axis=1)
        & np.isfinite(next_predictor).all(axis=1)
        & (np.abs(next_predictor).max(axis=1) < 36)
    )
    next_mu = np.exp(np.where(valid[:, None], next_predictor, 0))
    deviance = _deviance(counts, next_mu, alpha)
    valid &= np.isfinite(deviance) & np.any(np.abs(next_mu - counts) > 1e-8, axis=1)
    return beta, weights, valid, next_predictor, next_mu, deviance


def fit_statsmodels_nb(design, counts, alpha, offset, *, maxiter=200):
    """Match Statsmodels' NB/log IRLS start, deviance exit and covariance weights."""
    counts = np.asarray(counts, dtype=float)
    alpha = np.asarray(alpha, dtype=float)
    offset = np.asarray(offset, dtype=float)
    start_mu = (counts + counts.mean(axis=1, keepdims=True)) / 2
    good = (
        np.isfinite(counts).all(axis=1)
        & (counts >= 0).all(axis=1)
        & (start_mu > 0).all(axis=1)
        & np.isfinite(alpha)
        & (alpha >= 1e-7)
        & ~design.empty_unit(counts)
    )
    rows = np.flatnonzero(good)
    params = np.full((len(counts), design.size), np.nan)
    covariance_weights = np.zeros_like(counts)
    converged = np.zeros(len(counts), dtype=bool)
    mu = start_mu[rows]
    predictor = np.log(mu)
    with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
        deviance = _deviance(counts[rows], mu, alpha[rows, None])
        for _ in range(maxiter):
            if not rows.size:
                break
            beta, weights, valid, predictor, mu, next_dev = _irl_step(
                design,
                counts[rows],
                mu,
                predictor,
                alpha[rows, None],
                offset,
            )
            done = valid & (np.abs(next_dev - deviance) <= 1e-8)
            accepted = rows[done]
            params[accepted] = beta[done]
            covariance_weights[accepted] = weights[done]
            converged[accepted] = True
            keep = valid & ~done
            rows, mu, predictor, deviance = (
                rows[keep],
                mu[keep],
                predictor[keep],
                next_dev[keep],
            )
    return NBIRLSBatch(
        design, counts, alpha, offset, params, covariance_weights, converged
    )
