"""Bounded count preprocessing for supervised expression-analysis workers.

Median-of-ratios assumes most reference genes are stable or changes are balanced
(Anders & Huber, 2010, doi:10.1186/gb-2010-11-10-r106). PyDESeq2 implements
the DESeq2 Cox-Reid gene-wise likelihood, empirical mean-dispersion trend and
log-normal MAP shrinkage (Love et al., 2014, doi:10.1186/s13059-014-0550-8).
The statistical workers disclose the sampling design and exact calibration panel.
Only count inputs enter the count normalization functions.
"""

import importlib.metadata
import warnings

import numpy as np
import pandas as pd

MAX_PANEL_BYTES = 16 * 1024 * 1024
MAX_BLOCK_BYTES = 4 * 1024 * 1024
MIN_NORMALIZED_MEAN_COUNT = 1.0


def count_like(values):
    """Check count compatibility independently of measurement provenance."""
    return bool(np.all(values >= 0) and np.all(values == np.floor(values)))


def log1p_counts_per_10k(values, library_sizes):
    """Transform measured genes using totals over all available input genes."""
    if not np.isfinite(library_sizes).all() or np.any(library_sizes < 0):
        raise ValueError("Count library sizes must be finite and nonnegative")
    # Zero libraries stay zero so QC can report them. Tools that require a
    # usable expression profile reject a zero library before fitting.
    return np.log1p((values / np.where(library_sizes > 0, library_sizes, 1)) * 10_000)


def expression_summary(store, data_type="auto"):
    """Validate all entries using bounded blocks and inspect the source dtype.

CSV and TSV dtype inspection is a second streaming pass because the storage
abstraction returns floats even for integer tables. Integral *floating* source
values are continuous in auto mode. Explicit ``counts`` asserts raw count
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
    if store.format in {"csv", "tsv"}:
        integer_source = True
        separator = "\t" if store.format == "tsv" else ","
        for frame in pd.read_csv(store.path, sep=separator,
                                 usecols=list(range(1, store.shape[1] + 1)),
                                 chunksize=block_size):
            integer_source = integer_source and all(
                pd.api.types.is_integer_dtype(dtype) for dtype in frame.dtypes)
    else:
        integer_source = np.issubdtype(store._array.dtype, np.integer)
    discrete = data_type == "counts" or (
        data_type == "auto" and integer_source and integer_values and nonnegative)
    if discrete and (not integer_values or not nonnegative or maximum > 2 ** 53 - 1):
        raise ValueError(
            "invalid_counts: Raw counts must be nonnegative integers <= 2**53-1"
        )
    return {
        "data_type": "counts" if discrete else "continuous",
        "source_integer_dtype": bool(integer_source),
        "integer_valued": bool(integer_values),
        "nonnegative": bool(nonnegative),
        "finite": True,
        "zero_fraction": zeros / total,
        "gene_count": store.shape[0],
        "sample_count": store.shape[1],
        "scan_block_genes": block_size,
    }


def select_calibration_panel_indices(
    gene_names, sample_count, requested_genes, maximum_genes=1000, *, seed=0,
):
    """Retain requested genes and sample the rest uniformly without replacement."""
    if (
        isinstance(maximum_genes, bool)
        or not isinstance(maximum_genes, int)
        or not 1 <= maximum_genes <= 5000
    ):
        raise ValueError("calibration_max_genes must be an integer between 1 and 5000")
    if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed < 2 ** 32:
        raise ValueError("Calibration panel seed must be an integer in [0, 2**32 - 1]")
    lookup = {gene: i for i, gene in enumerate(gene_names)}
    absent = [gene for gene in requested_genes if gene not in lookup]
    if absent:
        raise ValueError("Unknown genes: " + ", ".join(absent))
    count = min(maximum_genes, len(gene_names), MAX_PANEL_BYTES // (8 * sample_count))
    selected = {lookup[gene] for gene in requested_genes}
    if count < max(len(selected), 1):
        raise ValueError(
            "Requested genes exceed the bounded calibration panel; "
            "reduce genes or sample count"
        )
    if count == len(gene_names):
        indices = list(range(count))
        method = "all_genes"
    else:
        n_sampled = count - len(selected)
        method = "requested_genes_only"
        if n_sampled:
            available = np.fromiter(
                (i for i in range(len(gene_names)) if i not in selected),
                dtype=np.intp, count=len(gene_names) - len(selected),
            )
            rng = np.random.Generator(np.random.PCG64(seed))
            selected.update(
                rng.choice(available, size=n_sampled, replace=False).tolist()
            )
            method = "seeded_uniform_without_replacement_plus_requested"
        indices = sorted(selected)
    return indices, {
        "gene_ids": [gene_names[i] for i in indices],
        "panel_selection": method,
        "panel_seed": seed,
        "panel_gene_indices": indices,
        "panel_gene_count": len(indices),
        "total_gene_count": len(gene_names),
        "max_dense_bytes": MAX_PANEL_BYTES,
        "requested_max_genes": maximum_genes,
    }


def calibration_panel(store, requested_genes, maximum_genes=1000, *, seed=0):
    """Read the chosen rows in source order within the 16 MiB float64 bound."""
    indices, details = select_calibration_panel_indices(
        store.gene_names, store.shape[1], requested_genes, maximum_genes, seed=seed,
    )
    values = store.get_chunk(indices, slice(None))
    details["panel_bytes"] = int(values.nbytes)
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
        raise ValueError(
            "no_normalization_reference: Calibration panel contains only zeros"
        )
    selected = counts[reference]
    log_counts = np.zeros_like(selected, dtype=float)
    np.log(selected, out=log_counts, where=selected > 0)
    log_means = log_counts.mean(axis=1)
    factors = []
    for sample in range(counts.shape[1]):
        usable = selected[:, sample] > 0
        if not usable.any():
            raise ValueError(
                "no_sample_normalization_reference: A sample has no positive "
                "reference counts"
            )
        factors.append(float(np.median(log_counts[usable, sample] - log_means[usable])))
    log_factors = np.asarray(factors)
    factors = np.exp(log_factors - log_factors.mean())
    if not np.isfinite(factors).all() or np.any(factors <= 0):
        raise ValueError(
            "invalid_size_factors: Normalization did not produce finite "
            "positive factors"
        )
    return factors, {
        "method": method,
        "reference_gene_count": int(reference.sum()),
        "reference_gene_ids": [
            gene for gene, use in zip(gene_ids, reference, strict=False) if use
        ],
        "factor_centering": "geometric_mean_one",
        "scope": "calibration_panel",
        "assumption": "Most reference genes are stable or changes are balanced",
    }


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
    from pydeseq2.utils import nb_nll
    from scipy.optimize import minimize_scalar

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
    fit = minimize_scalar(
        objective,
        bounds=(grid[max(0, best - 1)], grid[min(128, best + 1)]),
        method="bounded",
        options={"xatol": 1e-9, "maxiter": 500},
    )
    if not fit.success or not np.isfinite(fit.fun):
        return None
    if 0 < best < len(grid) - 1 and fit.fun > scores[best] + 1e-8:
        return None
    candidates = [
        (float(fit.fun), float(fit.x)),
        (float(scores[best]), float(grid[best])),
    ]
    score, log_alpha = min(candidates)
    if score > float(scores[best]) + 1e-8:
        return None
    return float(np.exp(log_alpha)), {
        "method": "bounded_scalar_with_grid_validation",
        "grid_points": 129,
        "objective": score,
        "grid_minimum_objective": float(scores[best]),
        "prior_penalty_included": bool(prior_reg),
    }


def _note_nonconvergence(code, failed, strict, diagnostics):
    diagnostics.append({"code": code, "severity": "error", "gene_ids": failed})
    if strict:
        raise ValueError(code + ": " + ", ".join(failed))


def _audited_inference(
    nonzero_names, mean_converged, strict, diagnostics, retry_records
):
    from pydeseq2.default_inference import DefaultInference

    class AuditedInference(DefaultInference):
        def irls(self, *args, **kwargs):
            beta, mu, leverage, converged = super().irls(*args, **kwargs)
            if (
                not np.isfinite(mu).all()
                or np.any(mu <= 0)
                or not np.isfinite(beta).all()
            ):
                raise ValueError(
                    "dispersion_mean_nonfinite: Invalid mean fit during dispersion "
                    "calibration"
                )
            failed = [nonzero_names[i] for i in np.flatnonzero(~converged)]
            if failed:
                for gene in failed:
                    mean_converged[gene] = False
                _note_nonconvergence(
                    "dispersion_mean_nonconvergence", failed, strict, diagnostics
                )
            return beta, mu, leverage, converged

        def alpha_mle(
            self,
            counts,
            design_matrix,
            mu,
            alpha_hat,
            min_disp,
            max_disp,
            prior_disp_var=None,
            cr_reg=True,
            prior_reg=False,
        ):
            estimates, converged = super().alpha_mle(
                counts,
                design_matrix,
                mu,
                alpha_hat,
                min_disp,
                max_disp,
                prior_disp_var,
                cr_reg,
                prior_reg,
            )
            for i in np.flatnonzero(~converged):
                recovered = _retry_dispersion(
                    counts[:, i],
                    design_matrix,
                    mu[:, i],
                    alpha_hat[i],
                    min_disp,
                    max_disp,
                    prior_disp_var,
                    cr_reg,
                    prior_reg,
                )
                if recovered is not None:
                    estimates[i], record = recovered
                    converged[i] = True
                    retry_records.append(
                        dict(
                            record,
                            gene_id=nonzero_names[i],
                            phase="map" if prior_reg else "genewise",
                        )
                    )
            return estimates, converged

    return AuditedInference(n_cpus=1)


def _set_shared_dispersion_trend(dds, dispersion_reference, nonzero):
    from pydeseq2.utils import dispersion_trend

    # Reuse the fitted hyperparameters for every block. Refitting a
    # prior per block would make estimates depend on row order.
    dds.uns["disp_function_type"] = dispersion_reference["trend_type"]
    dds.uns["prior_disp_var"] = np.float64(
        dispersion_reference["prior_log_dispersion_variance"]
    )
    dds.uns["_squared_logres"] = np.float64(
        dispersion_reference["prior_squared_log_residuals"]
    )
    coefficients = dispersion_reference["trend_coefficients"]
    if dds.uns["disp_function_type"] == "parametric":
        fitted = dispersion_trend(
            dds.var["_normed_means"].to_numpy()[nonzero],
            np.array([coefficients["a0"], coefficients["a1"]]),
        )
    elif dds.uns["disp_function_type"] == "mean":
        fitted = np.full(int(nonzero.sum()), coefficients["mean"])
    else:
        raise ValueError("unsupported_shared_dispersion_trend")
    if not np.isfinite(fitted).all() or np.any(fitted <= 0):
        raise ValueError("shared_dispersion_trend_invalid")
    dds.var["fitted_dispersions"] = np.nan
    dds.var.loc[nonzero, "fitted_dispersions"] = fitted


def _fit_map_dispersions(
    dds, dispersion_reference, nonzero, genes, strict, diagnostics
):
    if dispersion_reference is None:
        dds.fit_dispersion_trend()
        dds.fit_dispersion_prior()
    else:
        _set_shared_dispersion_trend(dds, dispersion_reference, nonzero)
    if not np.isfinite(dds.uns["prior_disp_var"]):
        raise ValueError(
            "dispersion_prior_nonfinite: PyDESeq2 could not estimate the dispersion "
            "prior"
        )
    dds.fit_MAP_dispersions()
    final = dds.var["dispersions"].to_numpy(dtype=float)
    trend = dds.var["fitted_dispersions"].to_numpy(dtype=float)
    outliers = dds.var["_outlier_genes"].to_numpy(dtype=bool)
    converged = dds.var["_MAP_converged"].fillna(True).to_numpy(dtype=bool)
    if not np.isfinite(final[nonzero]).all() or not np.isfinite(trend[nonzero]).all():
        raise ValueError(
            "dispersion_nonfinite: PyDESeq2 MAP/trend estimates are nonfinite"
        )
    if np.any(~converged & nonzero):
        failed = [gene for gene, ok in zip(genes, converged, strict=False) if not ok]
        _note_nonconvergence(
            "dispersion_map_nonconvergence", failed, strict, diagnostics
        )
    return final, trend, outliers, converged


def calibrate_counts(
    counts,
    panel,
    metadata,
    design,
    strict=True,
    *,
    size_factors=None,
    dispersion_reference=None,
):
    """Fit PyDESeq2 gene-wise, trend, prior and MAP dispersions on a bounded panel.

    Raw integer data enter PyDESeq2. The panel estimates the dispersion reference
    and estimates RLE factors when whole-family factors are unavailable. A
    subsequent gene block uses the same factors, prior and trend while estimating
    its own gene-wise and MAP dispersions. The subsequent GLMs use the logarithms
    of the RLE factors as offsets. The output records the PyDESeq2 version,
    calibration estimates and numerical failures.
    """
    from pydeseq2.dds import DeseqDataSet

    counts = np.asarray(counts, dtype=np.int64)
    if size_factors is None and dispersion_reference is None:
        factors, normalization = median_ratio_factors(counts, panel["gene_ids"])
    else:
        factors = np.asarray(size_factors, dtype=float)
        if (
            (dispersion_reference is not None
             and dispersion_reference["design_columns"] != list(design.columns))
            or factors.shape != (counts.shape[1],)
            or not np.isfinite(factors).all()
            or np.any(factors <= 0)
        ):
            raise ValueError("inconsistent_shared_count_calibration_reference")
        normalization = {
            "method": "supplied_rle_factors",
            "scope": (
                "whole_family" if dispersion_reference is None
                else "shared_reference_panel"
            ),
        }
    diagnostics = []
    retry_records = []
    nonzero_names = [
        gene
        for gene, nonzero in zip(
            panel["gene_ids"], np.any(counts != 0, axis=1), strict=False,
        )
        if nonzero
    ]
    mean_converged = {gene: True for gene in panel["gene_ids"]}

    with warnings.catch_warnings(record=True) as recorded:
        warnings.simplefilter("always")
        dds = DeseqDataSet(
            counts=pd.DataFrame(
                counts.T, index=metadata.index, columns=panel["gene_ids"]
            ),
            metadata=metadata,
            design=design,
            n_cpus=1,
            low_memory=True,
            inference=_audited_inference(
                nonzero_names,
                mean_converged,
                strict,
                diagnostics,
                retry_records,
            ),
            refit_cooks=False,
            quiet=True,
        )
        # Explicit factors avoid PyDESeq2 silently choosing iterative normalization.
        dds.obs["size_factors"] = factors
        dds.layers["normed_counts"] = dds.X / factors[:, None]
        dds.var["_normed_means"] = dds.layers["normed_counts"].mean(axis=0)
        dds.fit_genewise_dispersions()
        nonzero = dds.var["non_zero"].to_numpy(dtype=bool)
        raw = dds.var["genewise_dispersions"].to_numpy(dtype=float)
        gene_converged = (
            dds.var["_genewise_converged"].fillna(True).to_numpy(dtype=bool)
        )
        if not np.isfinite(raw[nonzero]).all():
            raise ValueError(
                "dispersion_nonfinite: PyDESeq2 gene-wise estimates are nonfinite"
            )
        if np.any(~gene_converged & nonzero):
            failed = [
                gene
                for gene, ok in zip(panel["gene_ids"], gene_converged, strict=False)
                if not ok
            ]
            _note_nonconvergence(
                "dispersion_genewise_nonconvergence", failed, strict, diagnostics
            )
        above_boundary = nonzero & (raw >= 100 * dds.min_disp)
        boundary_only = (
            not above_boundary.any()
            if dispersion_reference is None
            else dispersion_reference["prior_log_dispersion_variance"] is None
        )
        if boundary_only:
            # DESeq2 cannot estimate a log-dispersion prior when all genes are at
            # the Poisson boundary. Retain the actual likelihood estimates and
            # explicitly report that no empirical-Bayes calibration was possible.
            final, trend = raw.copy(), raw.copy()
            map_converged = gene_converged
            prior_variance, squared_logres = None, None
            trend_type, trend_coefficients = "unavailable_poisson_boundary", None
            outliers = np.zeros(len(raw), dtype=bool)
            method = "pydeseq2_genewise_poisson_boundary"
            diagnostics.append(
                {
                    "code": "dispersion_shrinkage_unavailable",
                    "severity": "warning",
                    "message": (
                        "The reference panel was near the Poisson boundary and has no "
                        "fitted prior"
                    ),
                }
            )
            if dispersion_reference is not None and above_boundary.any():
                failed = [
                    gene
                    for gene, outside in zip(
                        panel["gene_ids"], above_boundary, strict=False,
                    )
                    if outside
                ]
                map_converged = gene_converged & ~above_boundary
                _note_nonconvergence(
                    "shared_dispersion_prior_unavailable", failed, strict, diagnostics
                )
        else:
            final, trend, outliers, map_converged = _fit_map_dispersions(
                dds,
                dispersion_reference,
                nonzero,
                panel["gene_ids"],
                strict,
                diagnostics,
            )
            prior_variance = float(dds.uns["prior_disp_var"])
            squared_logres = float(dds.uns["_squared_logres"])
            trend_type = dds.uns["disp_function_type"]
            if dispersion_reference is None:
                trend_coefficients = (
                    {str(k): float(v) for k, v in dds.uns["trend_coeffs"].items()}
                    if trend_type == "parametric"
                    else {"mean": float(dds.uns["mean_disp"])}
                )
            else:
                trend_coefficients = dispersion_reference["trend_coefficients"]
            method = "pydeseq2_empirical_bayes"
    if retry_records:
        diagnostics.append(
            {
                "code": "dispersion_optimizer_retry_recovered",
                "severity": "warning",
                "message": (
                    "PyDESeq2 L-BFGS-B failures were recovered with validated scalar "
                    "optimization, retaining MAP penalties"
                ),
                "gene_ids": list(
                    dict.fromkeys(record["gene_id"] for record in retry_records)
                ),
            }
        )
    output = {}
    for i, gene in enumerate(panel["gene_ids"]):
        output[gene] = {
            "genewise_dispersion": float(raw[i]) if nonzero[i] else None,
            "dispersion_alpha": float(final[i]) if nonzero[i] else None,
            "trend_dispersion": float(trend[i]) if nonzero[i] else None,
            "dispersion_outlier": bool(outliers[i]),
            "dispersion_converged": bool(
                mean_converged[gene] and gene_converged[i] and map_converged[i]
            ),
        }
    details = dict(
        panel,
        method=method,
        package="pydeseq2",
        version=importlib.metadata.version("pydeseq2"),
        n_cpus=1,
        low_memory=True,
        refit_cooks=False,
        design_columns=list(design.columns),
        prior_log_dispersion_variance=prior_variance,
        prior_squared_log_residuals=squared_logres,
        trend_type=trend_type,
        trend_coefficients=trend_coefficients,
        min_disp=float(dds.min_disp),
        max_disp=float(dds.max_disp),
        min_mu=float(dds.min_mu),
        beta_tol=float(dds.beta_tol),
        fit_type="parametric_with_disclosed_mean_fallback",
        optimizer_retries=retry_records,
        gene_estimates=output,
    )
    return (
        factors,
        normalization,
        details,
        diagnostics,
        list(dict.fromkeys(str(w.message) for w in recorded)),
    )


def low_count_mask(counts, factors):
    """Fixed abundance filter based on all observations, without labels or tests."""
    return np.mean(counts / factors, axis=1) < MIN_NORMALIZED_MEAN_COUNT


def _batch_shared_dispersion(
    calibrator, counts, pending_indices, pending, factors, pilot,
):
    try:
        optimized = calibrator.estimate(
            counts[pending_indices], pending, factors, pilot,
        )
    except (ValueError, ArithmeticError, np.linalg.LinAlgError):
        optimized = {}
    residual = [
        (index, gene)
        for index, gene in zip(pending_indices, pending, strict=True)
        if gene not in optimized
    ]
    return optimized, [index for index, _ in residual], [gene for _, gene in residual]


def calibrate_count_family(blocks, reference, metadata, design, *, strict):
    """Apply one fitted RLE/dispersion reference to the remaining count blocks.

    The reference tuple is returned by ``calibrate_counts``. This routine
    calibrates one dense block at a time and retains scalar gene estimates.
    Genes below the fixed label-blind threshold have no test.
    """
    from .paired_dispersion import PairedDispersion

    factors, _, pilot, pilot_diagnostics, pilot_warnings = reference
    batched = PairedDispersion.from_design(design, pilot)
    estimates, filtered, seen = {}, {}, set()
    diagnostics, warnings_list = list(pilot_diagnostics), list(pilot_warnings)
    retries = list(pilot["optimizer_retries"])
    batched_count = fallback_count = 0
    for genes, counts in blocks:
        if (
            counts.shape != (len(genes), len(metadata))
            or counts.size * 8 > MAX_PANEL_BYTES
            or len(genes) != len(set(genes))
            or seen.intersection(genes)
        ):
            raise ValueError("invalid_bounded_count_calibration_block")
        seen.update(genes)
        pending_indices = []
        for index, (gene, low) in enumerate(
            zip(genes, low_count_mask(counts, factors), strict=True)
        ):
            if low:
                filtered[gene] = "independent_low_count_filter"
            elif gene in pilot["gene_estimates"]:
                estimates[gene] = pilot["gene_estimates"][gene]
            else:
                pending_indices.append(index)
        pending = [genes[index] for index in pending_indices]
        if batched is not None and pending_indices:
            optimized, pending_indices, pending = _batch_shared_dispersion(
                batched, counts, pending_indices, pending, factors, pilot,
            )
            estimates.update(optimized)
            batched_count += len(optimized)
        if not pending_indices:
            continue
        fallback_count += len(pending)
        _, _, fit, block_diagnostics, block_warnings = calibrate_counts(
            counts[pending_indices], {"gene_ids": pending}, metadata, design, strict,
            size_factors=factors, dispersion_reference=pilot)
        estimates.update(fit["gene_estimates"])
        retries.extend(fit["optimizer_retries"])
        diagnostics.extend(block_diagnostics)
        warnings_list.extend(block_warnings)
    if (
        len(seen) != pilot["total_gene_count"]
        or not set(pilot["gene_ids"]).issubset(seen)
    ):
        raise ValueError("incomplete_bounded_count_calibration_family")
    calibration = dict(pilot, gene_estimates=estimates, optimizer_retries=retries,
                       calibration_scope="one_reference_for_all_gene_blocks")
    if batched is not None:
        calibration["block_optimizer"] = {
            "method": "paired_schur_cox_reid_map",
            "batched_gene_count": batched_count, "scalar_gene_count": fallback_count,
        }
    return calibration, diagnostics, list(dict.fromkeys(warnings_list)), filtered
