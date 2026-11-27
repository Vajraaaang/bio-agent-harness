"""Single-batch ClusterDE using NB marginals, a Gaussian copula and Clipper BC."""

from __future__ import annotations

from typing import Annotated

import numpy as np
from pydantic import Field
from scipy import sparse, stats
from scipy.special import ndtri
from sklearn.cluster import KMeans
from sklearn.covariance import LedoitWolf

from devbench.core._files import hash_bounded_file
from devbench.core.expression_io import open_h5ad
from devbench.core.scientific_types import content_sha256
from devbench.core.scrna_marker_inference import (
    MarkerContext,
    MarkerRow,
    marker_rows_sha256,
)
from rna_kernels.canonical import canonical_json
from rna_kernels.scrna_upstream_contract import Column, Seed, _FrozenParameters


class Parameters(_FrozenParameters):
    batch_key: Column
    seed: Seed = 0
    alpha: Annotated[float, Field(gt=0, lt=1)] = 0.05


def read_marker_counts(path):
    with open_h5ad(path) as source:
        n, g = source.shape
        if not (8 <= n <= 10000 and 2 <= g <= 512 and n * g <= 2000000):
            raise ValueError(
                "Selective marker analysis exceeds its fitted matrix regime"
            )
        frame = source.subset()
        counts = frame.X.toarray() if sparse.issparse(frame.X) else np.asarray(frame.X)
        if (
            not np.isfinite(counts).all()
            or counts.min() < 0
            or counts.max() > 2**31 - 1
            or not np.array_equal(counts, counts.astype(np.int64))
        ):
            raise ValueError("Verified integer raw counts required")
        if not frame.obs_names.is_unique or not frame.var_names.is_unique:
            raise ValueError("Selective marker axes must be unique")
        return counts, frame.obs, tuple(map(str, frame.var_names))


def _fit_copula(counts):
    mean = counts.mean(axis=0)
    variance = counts.var(axis=0, ddof=1)
    if np.any(mean <= 0) or np.any(variance <= 0):
        raise ValueError("Remove constant genes before freezing the feature universe")
    # The Poisson boundary is represented explicitly (inverse size zero).
    dispersion = np.maximum((variance - mean) / mean**2, 0)
    scores = ndtri((stats.rankdata(counts, axis=0) - 0.5) / len(counts))
    covariance = LedoitWolf().fit(scores).covariance_
    sd = np.sqrt(covariance.diagonal())
    return mean, dispersion, covariance / np.outer(sd, sd)


def synthetic_null(counts, rng):
    mean, dispersion, corr = _fit_copula(counts)
    try:
        factor = np.linalg.cholesky(corr)
    except np.linalg.LinAlgError:
        # Perfectly collinear features can have an exactly singular copula.
        eig, vectors = np.linalg.eigh(corr)
        if eig.min() < -np.finfo(float).eps * len(eig) * np.linalg.norm(corr, ord=2):
            raise ValueError("Fitted copula covariance is indefinite") from None
        factor = vectors * np.sqrt(np.maximum(eig, 0))
    normals = rng.standard_normal(counts.shape) @ factor.T
    quantiles = np.clip(
        stats.norm.cdf(normals), np.finfo(float).eps, 1 - np.finfo(float).eps
    )
    synthetic = np.empty(counts.shape, dtype=np.int64)
    nb = dispersion > 1e-8
    size = 1 / dispersion[nb]
    synthetic[:, nb] = stats.nbinom.ppf(
        quantiles[:, nb], size, size / (size + mean[nb])
    )
    synthetic[:, ~nb] = stats.poisson.ppf(quantiles[:, ~nb], mean[~nb])
    return synthetic, {
        "means": mean.tolist(),
        "inverse_sizes": dispersion.tolist(),
        "correlation": corr.tolist(),
    }


def _de_scores(counts, seed):
    log = np.log1p(counts.astype(float))
    labels = KMeans(n_clusters=2, n_init=1, random_state=seed).fit_predict(log)
    if np.bincount(labels, minlength=2).min() < 2:
        raise ValueError("Both fitted clusters require at least two cells")
    p = stats.mannwhitneyu(
        log[labels == 0], log[labels == 1], axis=0, method="asymptotic"
    ).pvalue
    return -np.log10(np.maximum(p, np.finfo(float).tiny)), labels


def clipper(contrast, alpha):
    thresholds = np.unique(np.abs(contrast[contrast != 0]))
    for threshold in thresholds:
        positive = contrast >= threshold
        if (1 + np.count_nonzero(contrast <= -threshold)) <= alpha * positive.sum():
            return positive, float(threshold)
    return np.zeros(contrast.shape, dtype=bool), None


def clusterde(counts, seed, alpha):
    synthetic, model = synthetic_null(counts, np.random.default_rng(seed))
    target, labels = _de_scores(counts, seed)
    null, null_labels = _de_scores(synthetic, seed)
    contrast = target - null
    selected, threshold = clipper(contrast, alpha)
    diagnostic = {
        "synthetic_counts_sha256": content_sha256(synthetic.tolist()),
        "target_cluster_sizes": np.bincount(labels).tolist(),
        "null_cluster_sizes": np.bincount(null_labels).tolist(),
        "target_scores": target.tolist(),
        "null_scores": null.tolist(),
        "threshold": threshold,
        "synthetic_mean": synthetic.mean(axis=0).tolist(),
        "synthetic_variance": synthetic.var(axis=0, ddof=1).tolist(),
    }
    return contrast, selected, labels, model, diagnostic


def write_report(outputs, result, sidecar):
    result.update(
        status="completed",
        evidence_level="L1",
        calibration_status="uncalibrated_L1",
        clinical_release_allowed=False,
    )
    files = {
        "result": outputs / "result.json",
        "plot_sidecar": outputs / "plot_sidecar.json",
    }
    files["result"].write_bytes(canonical_json(result))
    files["plot_sidecar"].write_bytes(canonical_json(sidecar))
    return files


def marker_context(path, labels, obs, genes, parameters):
    return MarkerContext(
        counts_sha256=hash_bounded_file(path, 1 << 40),
        cluster_labels_sha256=content_sha256(labels.tolist()),
        cell_ids_sha256=content_sha256(tuple(obs.index)),
        feature_ids_sha256=content_sha256(genes),
        design_sha256=content_sha256(parameters.model_dump(mode="json")),
        contrast_id="two_clusters",
    )


def run_m2(op, paths, params, outputs):
    parameters = Parameters.model_validate(params)
    x, obs, genes = read_marker_counts(paths["expression"])
    batch = obs[parameters.batch_key]
    if batch.isna().any() or batch.nunique() != 1:
        raise ValueError("ClusterDE requires a single identified batch")
    score, selected, labels, model, diagnostic = clusterde(
        x, parameters.seed, parameters.alpha
    )
    rows = [
        MarkerRow(gene_id=g, score=float(s), selected=bool(k), test_status="tested")
        for g, s, k in zip(genes, score, selected, strict=True)
    ]
    context = marker_context(paths["expression"], labels, obs, genes, parameters)
    return write_report(
        outputs,
        {
            "method": "clusterde",
            "implementation": "standalone_nb_copula_clipper_bc",
            "null_fit": (
                "Moment-estimated NB margins and Ledoit-Wolf rank-Gaussian covariance."
            ),
            "native_scdesign3_equivalence_verified": False,
            "method_version": "clusterde.nb-copula-clipper-bc.1",
            "context": context.model_dump(mode="json"),
            "rows_sha256": marker_rows_sha256(rows),
            "fdr_threshold": parameters.alpha,
            "gene_statistics": [row.model_dump(mode="json") for row in rows],
            "null_model": model,
            "null_model_sha256": content_sha256(model),
            "null_diagnostic": diagnostic,
            "null_diagnostic_sha256": content_sha256(diagnostic),
            "input_population": "single_batch",
            "independent_review_required": True,
        },
        {
            "axis_labels": {
                "x": "Feature",
                "y": "Target minus synthetic null DE score",
            },
            "sample_count": len(obs),
            "donor_count": 0,
            "transformations": [
                "Cells are the observational units.",
                "Target minus null Wilcoxon scores; native Clipper BC selection.",
            ],
            "columns": ["gene", "score", "selected"],
            "rows": [[r.gene_id, r.score, r.selected] for r in rows],
        },
    )
