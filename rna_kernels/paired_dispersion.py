"""Vectorized PyDESeq2 shared-reference calibration for complete donor pairs."""

from dataclasses import dataclass

import numpy as np
from scipy.special import digamma, gammaln


@dataclass(frozen=True)
class PairedDispersion:
    """Folded, exact pair information matrices with a shared EB pilot."""

    reducer: object
    pairs: np.ndarray
    contrast_products: np.ndarray
    inverse: np.ndarray
    linear_mean: bool
    root_solver: object

    @classmethod
    def from_design(cls, design, pilot):
        # This path mirrors the installed, tested PyDESeq2 algorithm.
        if pilot["version"] != "0.5.4" or pilot["trend_type"] not in {
            "parametric",
            "mean",
        }:
            return None
        from .nb_irls import FixedEffectDesign

        try:
            from scipy.optimize.elementwise import find_root
        except ImportError:
            return None
        reducer = FixedEffectDesign.from_design(design)
        if reducer is None or not np.all(np.bincount(reducer.unit_ids) == 2):
            return None
        pairs = np.argsort(reducer.unit_ids, kind="stable").reshape(-1, 2)
        differences = reducer.targets[pairs[:, 1]] - reducer.targets[pairs[:, 0]]
        products = (differences[:, :, None] * differences[:, None, :]).reshape(
            len(pairs), -1
        )
        inverse = np.linalg.pinv(reducer.matrix)
        linear = len(np.unique(reducer.matrix, axis=0)) == reducer.size
        return cls(reducer, pairs, products, inverse, linear, find_root)

    def _initial_statistics(self, counts, factors, lower, upper):
        normalized = counts / factors
        fitted = self.reducer.predict(normalized @ self.inverse.T)
        fitted = np.maximum(fitted, 1)
        rough = np.maximum(
            np.sum(((normalized - fitted) ** 2 - fitted) / fitted**2, axis=1)
            / (fitted.shape[1] - self.reducer.size),
            0,
        )
        count_mean = normalized.mean(axis=1)
        moments = (
            normalized.var(axis=1, ddof=1) - np.mean(1 / factors) * count_mean
        ) / count_mean**2
        return count_mean, np.clip(np.minimum(rough, moments), lower, upper)

    def _means(self, counts, factors, rough_dispersion, *, min_mu, beta_tol):
        if self.linear_mean:
            return np.maximum(
                factors * self.reducer.predict((counts / factors) @ self.inverse.T),
                min_mu,
            ), np.ones(len(counts), dtype=bool)
        beta = np.log(counts / factors + 0.1) @ self.inverse.T
        means = factors * np.exp(self.reducer.predict(beta))
        active = np.arange(len(counts))
        converged = np.zeros(len(counts), dtype=bool)
        previous = np.full(len(counts), 1000.0)
        from pydeseq2.utils import nb_nll

        for _ in range(35):
            alpha = rough_dispersion[active, None]
            active_counts = counts[active]
            mu = np.maximum(means[active], min_mu)
            weights = mu / (1.0 + mu * alpha)
            response = np.log(mu / factors) + (active_counts - mu) / mu
            beta = self.reducer.solve(weights, response, ridge=1e-6)
            regular = (np.isfinite(beta) & (np.abs(beta) <= 30)).all(axis=1)
            new_means = factors * np.exp(self.reducer.predict(beta))
            # PyDESeq2 uses clamped means in iterations but returns the original means.
            mu = np.maximum(new_means, min_mu)
            deviance = -2 * np.atleast_1d(nb_nll(active_counts.T, mu.T, alpha[:, 0]))
            regular &= np.isfinite(deviance) & (
                np.isfinite(new_means) & (new_means > 0)
            ).all(axis=1)
            finished = regular & (
                np.abs(deviance - previous[active])
                <= beta_tol * (np.abs(deviance) + 0.1)
            )
            means[active] = new_means
            previous[active] = deviance
            converged[active[finished]] = True
            active = active[regular & ~finished]
            if active.size == 0:
                break
        return means, converged

    def _pair_information(self, mu, alpha):
        inverse_mu = 1 / mu
        first = inverse_mu[:, self.pairs[:, 0]]
        second = inverse_mu[:, self.pairs[:, 1]]
        harmonic = 1 / (first + second + 2 * alpha)
        return 1 / (first + alpha) + 1 / (second + alpha), harmonic

    def _schur(self, harmonic):
        width = self.reducer.targets.shape[1]
        return (harmonic @ self.contrast_products).reshape(len(harmonic), width, width)

    def _cr_gradient(self, mu, alpha):
        information, harmonic = self._pair_information(mu, alpha)
        # d log Δ = -(w_a + w_b) + 2h. For S, only the within-pair difference
        # remains and dS/dα follows by differentiating h.
        diagonal = (2 * harmonic - information).sum(axis=1)
        width = self.reducer.targets.shape[1]
        if width == 0:
            return diagonal
        derivative = -2 * self._schur(harmonic**2)
        return diagonal + np.trace(
            np.linalg.solve(self._schur(harmonic), derivative), axis1=1, axis2=2
        )

    def _loss(self, log_alpha, counts, mu, prior_center, prior_variance):
        alpha = np.exp(log_alpha[:, None])
        inverse = 1 / alpha
        scaled_means = mu * alpha
        # Drop only the observation-dependent log-factorial constant.
        likelihood_loss = (
            gammaln(inverse)
            - gammaln(counts + inverse)
            + (counts + inverse) * np.log1p(scaled_means)
            - counts * np.log(scaled_means)
        ).sum(axis=1)
        diagonal, harmonic = self._pair_information(mu, alpha)
        sign, schur = np.linalg.slogdet(self._schur(harmonic))
        loss = likelihood_loss + 0.5 * (np.log(diagonal).sum(axis=1) + schur)
        if prior_variance is not None:
            loss += (log_alpha - prior_center) ** 2 / (2 * prior_variance)
        return np.where(sign == 1, loss, np.nan)

    def _dispersion(self, counts, mu, lower, upper, start, *, trend=None, prior=None):
        prior_center = None if trend is None else np.log(trend)

        def derivative(log_alpha, positions):
            alpha = np.exp(log_alpha[:, None])
            means = mu[positions]
            observed = counts[positions]
            inverse = 1 / alpha
            scaled_means = means * alpha
            nb_score = (
                digamma(observed + inverse)
                - digamma(inverse)
                - np.log1p(scaled_means)
                - (observed - means) * alpha / (1 + scaled_means)
            ).sum(axis=1) / alpha[:, 0]
            score = nb_score + 0.5 * alpha[:, 0] * self._cr_gradient(means, alpha)
            if prior is not None:
                score += (log_alpha - prior_center[positions]) / prior
            return score

        output = np.full(len(counts), np.nan)
        # Near the Poisson boundary, the upstream floating point likelihood
        # has a flat numerical score. Keep its existing recovery path there.
        lo = np.full(len(counts), np.log(max(lower, 1e-6)))
        hi = np.full(len(counts), np.log(upper))
        indices = np.arange(len(counts))
        bracketed = (derivative(lo, indices) < 0) & (derivative(hi, indices) > 0)
        if not bracketed.any():
            return output
        indices = indices[bracketed]
        result = self.root_solver(
            derivative,
            (lo[indices], hi[indices]),
            args=(indices,),
            tolerances={"xatol": 1e-9, "xrtol": 1e-10},
            maxiter=50,
        )
        selected = result.success & np.isfinite(result.x) & (np.abs(result.f_x) <= 1e-5)
        indices, roots = indices[selected], result.x[selected]
        if not indices.size:
            return output
        center = None if prior_center is None else prior_center[indices]
        args = counts[indices], mu[indices], center, prior
        loss = self._loss(roots, *args)
        initial = self._loss(np.log(np.clip(start[indices], lower, upper)), *args)
        good = np.isfinite(loss) & np.isfinite(initial) & (loss <= initial + 1e-4)
        output[indices[good]] = np.exp(roots[good])
        return output

    def estimate(self, counts, genes, factors, pilot):
        lower, upper = pilot["min_disp"], pilot["max_disp"]
        with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
            means, initial = self._initial_statistics(counts, factors, lower, upper)
            terms = pilot["trend_coefficients"]
            trend = (
                terms["a0"] + terms["a1"] / means
                if pilot["trend_type"] == "parametric"
                else np.full(len(counts), terms["mean"])
            )
            mu, regular = self._means(
                counts,
                factors,
                initial,
                min_mu=pilot["min_mu"],
                beta_tol=pilot["beta_tol"],
            )
            regular &= np.isfinite(trend) & (trend > 0) & (mu > 0).all(axis=1)
            selected = np.flatnonzero(regular)
            if selected.size == 0:
                return {}
            counts, mu, trend = counts[selected], mu[selected], trend[selected]
            raw = self._dispersion(counts, mu, lower, upper, initial[selected])
            posterior = self._dispersion(
                counts,
                mu,
                lower,
                upper,
                trend,
                trend=trend,
                prior=pilot["prior_log_dispersion_variance"],
            )
            outlier_distance = (
                np.log(raw)
                - np.log(trend)
                - 2 * np.sqrt(pilot["prior_squared_log_residuals"])
            )
            valid = (
                np.isfinite(raw)
                & np.isfinite(posterior)
                & (np.abs(outlier_distance) > 1e-4)
            )
        return {
            genes[selected[i]]: {
                "genewise_dispersion": float(raw[i]),
                "dispersion_alpha": float(raw[i] if outlier else posterior[i]),
                "trend_dispersion": float(trend[i]),
                "dispersion_outlier": outlier,
                "dispersion_converged": True,
            }
            for i in np.flatnonzero(valid)
            for outlier in (bool(outlier_distance[i] > 0),)
        }
