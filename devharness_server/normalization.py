"""Bounded count preprocessing; called only from supervised statistical workers.

Median-of-ratios assumes most reference genes are stable or changes are balanced
(Anders & Huber, 2010, doi:10.1186/gb-2010-11-10-r106). PyDESeq2 implements
the DESeq2 Cox-Reid gene-wise likelihood, empirical mean-dispersion trend and
log-normal MAP shrinkage (Love et al., 2014, doi:10.1186/s13059-014-0550-8).
This module uses a disclosed, deterministic calibration panel, not a purported
whole-transcriptome fit. It never normalizes continuous expression automatically.
"""

import importlib.metadata
import warnings

import numpy as np
import pandas as pd


MAX_PANEL_BYTES = 16 * 1024 * 1024
MAX_BLOCK_BYTES = 4 * 1024 * 1024


def expression_summary(store, data_type="auto"):
    """Validate all entries using bounded blocks and inspect the source dtype.

CSV dtype inspection is a second streaming pass because the storage abstraction
returns floats even for integer CSVs. Integral *floating* source values are
continuous in auto mode. Explicit ``counts`` is an assertion of raw count
provenance, still requiring finite, nonnegative integer-valued observations.
    """
    if data_type not in {"auto", "counts", "continuous"}:
        raise ValueError("data_type must be auto, counts, or continuous")
    block_size = max(1, min(500, MAX_BLOCK_BYTES // (8 * store.shape[1])))
    integer_values, nonnegative = True, True
    zeros, total = 0, 0
    maximum = 0.0
    for _, values in store.iter_gene_chunks(block_size):
        if not np.isfinite(values).all():
            raise ValueError("nonfinite_expression: Expression values must be finite")
        nonnegative = nonnegative and not np.any(values < 0)
        integer_values = integer_values and not np.any(values != np.floor(values))
        zeros += int(np.count_nonzero(values == 0))
        total += values.size
        maximum = max(maximum, float(np.max(values)))
    if store.format == "csv":
        integer_source = True
        for frame in pd.read_csv(store.path, usecols=list(range(1, store.shape[1] + 1)),
                                 chunksize=block_size):
            integer_source = integer_source and all(
                pd.api.types.is_integer_dtype(dtype) for dtype in frame.dtypes)
    else:
        integer_source = np.issubdtype(store._array.dtype, np.integer)
    discrete = data_type == "counts" or (
        data_type == "auto" and integer_source and integer_values and nonnegative)
    if discrete and (not integer_values or not nonnegative or maximum > 2 ** 53 - 1):
        raise ValueError("invalid_counts: Raw counts must be nonnegative integers <= 2**53-1")
    return {"data_type": "counts" if discrete else "continuous", "source_integer_dtype": bool(integer_source),
            "integer_valued": bool(integer_values), "nonnegative": bool(nonnegative),
            "finite": True, "zero_fraction": zeros / total, "gene_count": store.shape[0],
            "sample_count": store.shape[1], "scan_block_genes": block_size}


def calibration_panel(store, requested_genes, maximum_genes=1000):
    """Select evenly spaced gene indices plus all requested genes, within 16 MiB.

The selection does not use expression abundance or outcomes. Uniform spacing is
deterministic, but can be biased if file order systematically groups genes;
the exact IDs are returned for audit and sensitivity analysis.
    """
    if isinstance(maximum_genes, bool) or not isinstance(maximum_genes, int) or not 1 <= maximum_genes <= 5000:
        raise ValueError("calibration_max_genes must be an integer between 1 and 5000")
    lookup = {gene: i for i, gene in enumerate(store.gene_names)}
    absent = [gene for gene in requested_genes if gene not in lookup]
    if absent:
        raise ValueError("Unknown genes: " + ", ".join(absent))
    count = min(maximum_genes, store.shape[0], MAX_PANEL_BYTES // (8 * store.shape[1]))
    if count < len(requested_genes):
        raise ValueError("Requested genes exceed the bounded calibration panel; reduce genes or sample count")
    selected = {lookup[gene] for gene in requested_genes}
    for index in np.linspace(0, store.shape[0] - 1, count, dtype=int):
        if len(selected) == count:
            break
        selected.add(int(index))
    indices = sorted(selected)
    values = store.get_chunk(indices, slice(None))
    details = {"gene_ids": [store.gene_names[i] for i in indices],
               "panel_selection": "evenly_spaced_plus_requested", "panel_gene_count": len(indices),
               "total_gene_count": store.shape[0], "max_dense_bytes": MAX_PANEL_BYTES,
               "panel_bytes": int(values.nbytes), "requested_max_genes": maximum_genes}
    return values, details


def median_ratio_factors(counts, gene_ids):
    """Return geometric-mean-one RLE factors; disclose the positive-count fallback.

The usual estimator excludes genes with any zero. If none qualify, the DESeq2
positive-count variant uses the n-th root of the product of positive counts and
per-sample positive ratios. Samples without a usable reference are rejected.
    """
    positive = counts > 0
    reference = positive.all(axis=1)
    method = "median_of_ratios"
    if not reference.any():
        reference = positive.any(axis=1)
        method = "median_of_ratios_poscounts"
    if not reference.any():
        raise ValueError("no_normalization_reference: Calibration panel contains only zeros")
    selected = counts[reference]
    log_counts = np.zeros_like(selected, dtype=float)
    np.log(selected, out=log_counts, where=selected > 0)
    log_means = log_counts.mean(axis=1)
    factors = []
    for sample in range(counts.shape[1]):
        usable = selected[:, sample] > 0
        if not usable.any():
            raise ValueError("no_sample_normalization_reference: A sample has no positive reference counts")
        factors.append(float(np.median(log_counts[usable, sample] - log_means[usable])))
    log_factors = np.asarray(factors)
    factors = np.exp(log_factors - log_factors.mean())
    if not np.isfinite(factors).all() or np.any(factors <= 0):
        raise ValueError("invalid_size_factors: Normalization did not produce finite positive factors")
    return factors, {"method": method, "reference_gene_count": int(reference.sum()),
                     "reference_gene_ids": [gene for gene, use in zip(gene_ids, reference) if use],
                     "factor_centering": "geometric_mean_one", "scope": "calibration_panel",
                     "assumption": "Most reference genes are stable or changes are balanced"}


def _retry_dispersion(counts, design, mu, alpha_hat, min_disp, max_disp,
                       prior_disp_var=None, cr_reg=True, prior_reg=False):
    """Verify a bounded retry of PyDESeq2's identical scalar alpha objective.

PyDESeq2 0.5.x marks its grid fallback as L-BFGS-B nonconvergence. In 0.5.4,
``fit_alpha_mle`` also omits prior arguments in that fallback call. This retry
explicitly retains the Cox-Reid determinant and MAP log-normal penalty. A
129-point log-alpha scan brackets the best region; bounded scalar minimization
must converge and improve upon the finite grid minimum. This is numerical
recovery, not an alternate biological dispersion estimator.
    """
    from scipy.optimize import minimize_scalar
    from pydeseq2.utils import nb_nll

    def objective(log_alpha):
        alpha = np.exp(log_alpha)
        loss = float(nb_nll(counts, mu, alpha))
        if cr_reg:
            weights = mu / (1 + mu * alpha)
            sign, log_determinant = np.linalg.slogdet((design.T * weights) @ design)
            if sign <= 0 or not np.isfinite(log_determinant):
                return float("inf")
            loss += 0.5 * log_determinant
        if prior_reg:
            loss += (log_alpha - np.log(alpha_hat)) ** 2 / (2 * prior_disp_var)
        return loss

    grid = np.linspace(np.log(min_disp), np.log(max_disp), 129)
    scores = np.array([objective(value) for value in grid])
    if not np.isfinite(scores).all():
        return None
    best = int(np.argmin(scores))
    fit = minimize_scalar(objective, bounds=(grid[max(0, best - 1)], grid[min(128, best + 1)]),
                          method="bounded", options={"xatol": 1e-9, "maxiter": 500})
    if not fit.success or not np.isfinite(fit.fun):
        return None
    if 0 < best < len(grid) - 1 and fit.fun > scores[best] + 1e-8:
        return None
    candidates = [(float(fit.fun), float(fit.x)), (float(scores[best]), float(grid[best]))]
    score, log_alpha = min(candidates)
    if score > float(scores[best]) + 1e-8:
        return None
    return float(np.exp(log_alpha)), {"method": "bounded_scalar_with_grid_validation", "grid_points": 129,
                                      "objective": score, "grid_minimum_objective": float(scores[best]),
                                      "prior_penalty_included": bool(prior_reg)}


def calibrate_counts(counts, panel, metadata, design, strict=True):
    """Fit PyDESeq2 gene-wise, trend, prior and MAP dispersions on a bounded panel.

Raw integer data enter PyDESeq2; supplied RLE factors enter its offset-equivalent
size-factor fields and the subsequent statsmodels GLMs use their logarithms.
PyDESeq2 is pinned by the project; the runtime version and fitted prior/trend are
also returned. Numerical optimization failures are never replaced by alpha=1.
    """
    from pydeseq2.dds import DeseqDataSet
    from pydeseq2.default_inference import DefaultInference

    counts = np.asarray(counts, dtype=np.int64)
    factors, normalization = median_ratio_factors(counts, panel["gene_ids"])
    diagnostics = []
    retry_records = []
    nonzero_names = [gene for gene, nonzero in zip(panel["gene_ids"], np.any(counts != 0, axis=1)) if nonzero]
    mean_converged = {gene: True for gene in panel["gene_ids"]}

    class AuditedInference(DefaultInference):
        def irls(self, *args, **kwargs):
            beta, mu, leverage, converged = super().irls(*args, **kwargs)
            if not np.isfinite(mu).all() or np.any(mu <= 0) or not np.isfinite(beta).all():
                raise ValueError("dispersion_mean_nonfinite: Invalid mean fit during dispersion calibration")
            failed = [nonzero_names[i] for i in np.flatnonzero(~converged)]
            if failed:
                for gene in failed:
                    mean_converged[gene] = False
                diagnostics.append({"code": "dispersion_mean_nonconvergence", "severity": "error", "gene_ids": failed})
                if strict:
                    raise ValueError("dispersion_mean_nonconvergence: " + ", ".join(failed))
            return beta, mu, leverage, converged

        def alpha_mle(self, counts, design_matrix, mu, alpha_hat, min_disp, max_disp,
                      prior_disp_var=None, cr_reg=True, prior_reg=False):
            estimates, converged = super().alpha_mle(counts, design_matrix, mu, alpha_hat, min_disp, max_disp,
                                                     prior_disp_var, cr_reg, prior_reg)
            for i in np.flatnonzero(~converged):
                recovered = _retry_dispersion(counts[:, i], design_matrix, mu[:, i], alpha_hat[i],
                                                min_disp, max_disp, prior_disp_var, cr_reg, prior_reg)
                if recovered is not None:
                    estimates[i], record = recovered
                    converged[i] = True
                    retry_records.append(dict(record, gene_id=nonzero_names[i], phase="map" if prior_reg else "genewise"))
            return estimates, converged

    with warnings.catch_warnings(record=True) as recorded:
        warnings.simplefilter("always")
        dds = DeseqDataSet(counts=pd.DataFrame(counts.T, index=metadata.index, columns=panel["gene_ids"]),
                           metadata=metadata, design=design, n_cpus=1, low_memory=True,
                           inference=AuditedInference(n_cpus=1),
                           refit_cooks=False, quiet=True)
        # Explicit factors avoid PyDESeq2 silently choosing iterative normalization.
        dds.obs["size_factors"] = factors
        dds.layers["normed_counts"] = dds.X / factors[:, None]
        dds.var["_normed_means"] = dds.layers["normed_counts"].mean(axis=0)
        dds.fit_genewise_dispersions()
        nonzero = dds.var["non_zero"].to_numpy(dtype=bool)
        raw = dds.var["genewise_dispersions"].to_numpy(dtype=float)
        gene_converged = dds.var["_genewise_converged"].fillna(True).to_numpy(dtype=bool)
        if not np.isfinite(raw[nonzero]).all():
            raise ValueError("dispersion_nonfinite: PyDESeq2 gene-wise estimates are nonfinite")
        if np.any(~gene_converged & nonzero):
            failed = [gene for gene, ok in zip(panel["gene_ids"], gene_converged) if not ok]
            diagnostics.append({"code": "dispersion_genewise_nonconvergence", "severity": "error", "gene_ids": failed})
            if strict:
                raise ValueError("dispersion_genewise_nonconvergence: " + ", ".join(failed))
        if not np.any(raw[nonzero] >= 100 * dds.min_disp):
            # DESeq2 cannot estimate a log-dispersion prior when all genes are at
            # the Poisson boundary. Retain the actual likelihood estimates and
            # explicitly report that no empirical-Bayes calibration was possible.
            final, trend = raw.copy(), raw.copy()
            map_converged = gene_converged
            prior_variance, trend_type, trend_coefficients = None, "unavailable_poisson_boundary", None
            outliers = np.zeros(len(raw), dtype=bool)
            method = "pydeseq2_genewise_poisson_boundary"
            diagnostics.append({"code": "dispersion_shrinkage_unavailable", "severity": "warning",
                                "message": "All calibration estimates are near the Poisson boundary; no prior was fitted"})
        else:
            dds.fit_dispersion_trend()
            dds.fit_dispersion_prior()
            if not np.isfinite(dds.uns["prior_disp_var"]):
                raise ValueError("dispersion_prior_nonfinite: PyDESeq2 could not estimate the dispersion prior")
            dds.fit_MAP_dispersions()
            final = dds.var["dispersions"].to_numpy(dtype=float)
            trend = dds.var["fitted_dispersions"].to_numpy(dtype=float)
            outliers = dds.var["_outlier_genes"].to_numpy(dtype=bool)
            map_converged = dds.var["_MAP_converged"].fillna(True).to_numpy(dtype=bool)
            if not np.isfinite(final[nonzero]).all() or not np.isfinite(trend[nonzero]).all():
                raise ValueError("dispersion_nonfinite: PyDESeq2 MAP/trend estimates are nonfinite")
            if np.any(~map_converged & nonzero):
                failed = [gene for gene, ok in zip(panel["gene_ids"], map_converged) if not ok]
                diagnostics.append({"code": "dispersion_map_nonconvergence", "severity": "error", "gene_ids": failed})
                if strict:
                    raise ValueError("dispersion_map_nonconvergence: " + ", ".join(failed))
            prior_variance = float(dds.uns["prior_disp_var"])
            trend_type = dds.uns["disp_function_type"]
            trend_coefficients = ({str(k): float(v) for k, v in dds.uns["trend_coeffs"].items()}
                                  if "trend_coeffs" in dds.uns else {"mean": float(dds.uns["mean_disp"])})
            method = "pydeseq2_empirical_bayes"
    if retry_records:
        diagnostics.append({"code": "dispersion_optimizer_retry_recovered", "severity": "warning",
                            "message": "PyDESeq2 L-BFGS-B failures were recovered with validated scalar optimization, retaining MAP penalties",
                            "gene_ids": list(dict.fromkeys(record["gene_id"] for record in retry_records))})
    output = {}
    for i, gene in enumerate(panel["gene_ids"]):
        output[gene] = {"genewise_dispersion": float(raw[i]) if nonzero[i] else None,
                        "dispersion_alpha": float(final[i]) if nonzero[i] else None,
                        "trend_dispersion": float(trend[i]) if nonzero[i] else None,
                        "dispersion_outlier": bool(outliers[i]),
                        "dispersion_converged": bool(mean_converged[gene] and gene_converged[i] and map_converged[i])}
    details = dict(panel, method=method, package="pydeseq2", version=importlib.metadata.version("pydeseq2"),
                   n_cpus=1, low_memory=True, refit_cooks=False, design_columns=list(design.columns),
                   prior_log_dispersion_variance=prior_variance, trend_type=trend_type,
                   trend_coefficients=trend_coefficients, min_disp=float(dds.min_disp), max_disp=float(dds.max_disp),
                   min_mu=float(dds.min_mu), beta_tol=float(dds.beta_tol), fit_type="parametric_with_disclosed_mean_fallback",
                   optimizer_retries=retry_records, gene_estimates=output)
    return factors, normalization, details, diagnostics, list(dict.fromkeys(str(w.message) for w in recorded))
