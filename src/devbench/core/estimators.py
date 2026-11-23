"""Estimator interface for the existing conditional per-gene model families."""

from typing import Any, Protocol


class FittedGene(Protocol):
    @property
    def params(self) -> Any: ...

    def cov_params(self) -> Any: ...

    @property
    def df_resid(self) -> float: ...

    @property
    def llf(self) -> float: ...


class GaussianFit(FittedGene, Protocol):
    @property
    def resid(self) -> Any: ...

    def f_test(self, contrast: Any) -> Any: ...


class NegativeBinomialFit(FittedGene, Protocol):
    def wald_test(self, contrast: Any, *, scalar: bool) -> Any: ...


class StatisticalEstimator(Protocol):
    def fit_gaussian(self, values: Any, design: Any) -> GaussianFit:
        """Fit continuous expression on its original measurement scale."""
        ...

    def fit_negative_binomial(self, values: Any, design: Any, *, alpha: float, offset: Any) -> NegativeBinomialFit:
        """Fit raw counts using supplied dispersion and log size factors."""
        ...


class StatsmodelsEstimator:
    def fit_gaussian(self, values: Any, design: Any) -> GaussianFit:
        import numpy as np
        import statsmodels.api as sm

        fitted = sm.OLS(values, design).fit()
        if fitted.scale <= np.finfo(float).tiny:
            raise ValueError("zero_residual_variance")
        return fitted

    def fit_negative_binomial(self, values: Any, design: Any, *, alpha: float, offset: Any) -> NegativeBinomialFit:
        import statsmodels.api as sm

        fitted = sm.GLM(values, design, family=sm.families.NegativeBinomial(alpha=alpha),
                        offset=offset).fit(maxiter=200, tol=1e-8)
        if not fitted.converged:
            raise ValueError("negative_binomial_nonconvergence")
        return fitted
