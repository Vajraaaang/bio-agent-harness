"""Bounded host batches for projections of conditional NB fits."""

import math

import numpy as np

from rna_kernels.nb_irls import (
    DenseDesign,
    FixedEffectDesign,
    NBIRLSGene,
    fit_statsmodels_nb,
)


def conditional_nb_contrasts(fitted, vectors):
    """Project the conditional log2 effects without expanding unit covariance."""
    if not isinstance(fitted, NBIRLSGene):
        return None
    effects, variances = fitted.batch.contrast(np.asarray(vectors))
    index = fitted.index
    if not np.isfinite(variances[index]).all():
        raise ValueError("nonfinite_model_estimate")
    scale = math.log(2)
    return tuple(
        (float(effect / scale), float(variance / scale**2), fitted.df_resid)
        for effect, variance in zip(effects[index], variances[index], strict=True)
    )


def _design_for_batch(design):
    paired = FixedEffectDesign.from_design(design)
    if paired is not None:
        return paired
    matrix = np.asarray(design)
    if matrix.shape[1] <= 32:
        return DenseDesign(matrix)
    return None


def _eligible_dispersion(estimate):
    return (
        estimate.get("dispersion_alpha")
        if estimate.get("dispersion_converged")
        else None
    )


def iter_batched_nb_genes(data, design, offset, dispersions, failures):
    """Yield each original row with a fast fit when its working state verifies."""
    system = _design_for_batch(design) if len(data.gene_ids) >= 32 else None
    if system is None:
        yield from (
            (gene, values, None)
            for gene, values in zip(data.gene_ids, data.expression, strict=True)
        )
        return
    rows = min(512, max(1, 131_072 // system.matrix.shape[0]))
    for start in range(0, len(data.gene_ids), rows):
        genes = data.gene_ids[start : start + rows]
        block = data.expression[start : start + rows]
        alpha = [
            _eligible_dispersion(dispersions.get(gene, {}))
            if gene not in failures
            else None
            for gene in genes
        ]
        selected = [i for i, value in enumerate(alpha) if value is not None]
        accepted = {}
        if selected:
            fit = fit_statsmodels_nb(
                system,
                block[selected],
                np.array([alpha[i] for i in selected]),
                offset,
            )
            accepted = {
                selected[i]: NBIRLSGene(fit, i) for i in np.flatnonzero(fit.converged)
            }
        yield from (
            (gene, values, accepted.get(i))
            for i, (gene, values) in enumerate(zip(genes, block, strict=True))
        )
