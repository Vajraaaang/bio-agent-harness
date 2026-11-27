"""Chen–Gao's single-feature CADET test conditional on a two-means Lloyd path.

The NB input regime uses independently specified marginals and randomized
quantile residuals. The selective reference is Gaussian on this modeled scale.
"""

from __future__ import annotations

from typing import Annotated, Literal

import numpy as np
from pydantic import Field
from scipy import stats
from scipy.special import log_ndtr, logsumexp, ndtri

from devbench.core._files import hash_bounded_file, read_bounded_file
from devbench.core.scientific_types import content_sha256
from devbench.core.scrna_marker_inference import MarkerRow, marker_rows_sha256
from rna_kernels.scrna_clusterde import marker_context, read_marker_counts, write_report
from rna_kernels.scrna_upstream_contract import Column, Digest, Seed, _FrozenParameters
from rna_kernels.units import UNKNOWN_UNIT_MARKERS


class Parameters(_FrozenParameters):
    target_feature: Column
    unit_key: Column
    seed: Seed = 0
    alpha: Annotated[float, Field(gt=0, lt=1)] = 0.05


Positive = Annotated[float, Field(gt=0)]


class GaussianReference(_FrozenParameters):
    format: Literal["devbench.cadet-reference/1"]
    origin: Literal["known"]
    null_regime: Literal["independent_nb_features_and_units"]
    source_sha256: Digest
    feature_ids: tuple[Column, ...] = Field(min_length=2, max_length=512)
    mean: tuple[Positive, ...] = Field(min_length=2, max_length=512)
    theta: tuple[Positive, ...] = Field(min_length=2, max_length=512)
    covariance: tuple[tuple[float, ...], ...] = Field(min_length=2, max_length=512)


def quantile_residuals(counts, mean, theta, rng):
    """Randomized PIT for discrete counts using exact NB interval probabilities."""
    counts = np.asarray(counts, dtype=np.int64)
    prob = theta / (theta + mean)
    u = stats.nbinom.cdf(counts - 1, theta, prob) + rng.uniform(
        size=counts.shape
    ) * stats.nbinom.pmf(counts, theta, prob)
    if np.any((u <= 0) | (u >= 1)):
        raise ValueError("Gaussian residual tail outside numeric precision")
    return ndtri(u)


def _lloyd(x, rng):
    indices = rng.choice(len(x), 2, replace=False)
    centers = x[indices]
    history = []
    for _ in range(100):
        group = (
            np.square(x - centers[1]).sum(1) < np.square(x - centers[0]).sum(1)
        ).astype(int)
        if np.bincount(group, minlength=2).min() < 1:
            raise ValueError("Two non-degenerate clusters required")
        if history and np.array_equal(group, history[-1]):
            # The stopping comparison is also part of the selection event.
            history.append(group)
            return indices, history
        history.append(group)
        centers = np.stack([x[group == j].mean(0) for j in range(2)])
    raise ValueError("Lloyd path did not converge within iteration bound")


def _forbidden_intervals(a, b, c):
    # Forbidden where a*d^2 + b*d + c > 0.  d=0 is the observed data.
    if c > 1e-8:
        raise ValueError("Observed Lloyd assignment outside its event")
    if a == 0:
        if b > 0:
            return [(-c / b, np.inf)]
        if b < 0:
            return [(-np.inf, -c / b)]
        return []
    disc = b * b - 4 * a * c
    if disc <= 0:
        return [(-np.inf, np.inf)] if a > 0 else []
    # Stable pair of quadratic roots including when the roots have unequal sizes.
    r = -0.5 * (b + np.copysign(np.sqrt(disc), b))
    lo, hi = sorted((r / a, c / r))
    return [(-np.inf, lo), (hi, np.inf)] if a > 0 else [(lo, hi)]


def _allowed_intervals(bans):
    left = -np.inf
    allowed = []
    for lo, hi in sorted(bans):
        if lo > left:
            allowed.append((left, lo))
        left = max(left, hi)
    if left < np.inf:
        allowed.append((left, np.inf))
    return allowed


def _log_mass(lo, hi):
    if lo >= hi:
        return -np.inf
    if lo >= 0:
        lo, hi = -hi, -lo
    high, low = log_ndtr(hi), log_ndtr(lo)
    if low >= high:
        return -np.inf
    return high + np.log(-np.expm1(low - high))


def cadet(x, feature, covariance, seed):
    """Compute P(|Z| >= |z_obs| given all assignments and the nuisance projection)."""
    idx, history = _lloyd(x, np.random.default_rng(seed))
    labels = history[-1]
    nu = np.where(labels == 1, 1 / np.sum(labels == 1), -1 / np.sum(labels == 0))
    stat = float(nu @ x[:, feature])
    variance = covariance[feature, feature]
    sd = np.sqrt(variance * (nu @ nu))
    slope = np.outer(nu / (nu @ nu), covariance[:, feature] / variance)
    centers, derivatives = x[idx], slope[idx]
    bans = []
    for group in history:
        sign = 1 - 2 * group
        r0, r1 = x - centers[0], x - centers[1]
        s0, s1 = slope - derivatives[0], slope - derivatives[1]
        a = sign * np.sum((s0 - s1) * (s0 + s1), axis=1)
        b = 2 * sign * (np.sum(r0 * s0, axis=1) - np.sum(r1 * s1, axis=1))
        c = sign * np.sum((r0 - r1) * (r0 + r1), axis=1)
        for coefs in zip(a, b, c, strict=True):
            bans.extend(_forbidden_intervals(*coefs))
        centers = np.stack([x[group == j].mean(0) for j in range(2)])
        derivatives = np.stack([slope[group == j].mean(0) for j in range(2)])
    allowed = _allowed_intervals(bans)
    intervals = [((lo + stat) / sd, (hi + stat) / sd) for lo, hi in allowed]
    cutoff = abs(stat) / sd
    denominator = logsumexp([_log_mass(lo, hi) for lo, hi in intervals])
    numerator = logsumexp(
        [
            m
            for lo, hi in intervals
            for m in (_log_mass(lo, min(hi, -cutoff)), _log_mass(max(lo, cutoff), hi))
        ]
    )
    if not np.isfinite(denominator) or not np.isfinite(numerator):
        raise ValueError("Selective probability unresolved at numeric precision")
    event = {
        "initial_rows": idx.tolist(),
        "assignment_path_sha256": content_sha256([v.tolist() for v in history]),
        "iterations": len(history),
        "contrast_standard_error": float(sd),
        "cluster_sizes": np.bincount(labels).tolist(),
        "standardized_intervals": [
            [None if np.isinf(v) else float(v) for v in pair] for pair in intervals
        ],
    }
    return stat, float(np.exp(min(0, numerator - denominator))), labels, event


def _check_independent_rows(obs, unit_key):
    # A barcode, sample or specimen alone cannot establish organism identity.
    identities = {
        "donor",
        "organism",
        "animal",
        "patient",
        "subject",
        "participant",
        "individual",
        "biologicalunit",
    }
    names = {
        key: "".join(c for c in key.casefold() if c.isalnum()).removesuffix("id")
        for key in obs.columns
        if isinstance(key, str) and (key.casefold() != "organism" or key == unit_key)
    }
    if unit_key not in obs or not identities.intersection(names.values()):
        raise ValueError(
            "CADET requires declared higher-level biological unit identity"
        )
    checked = {key for key, role in names.items() if role in identities | {"specimen"}}
    checked.add(unit_key)
    for key in sorted(checked):
        values = obs[key].tolist()
        if any(
            not isinstance(v, str) or v.strip().casefold() in UNKNOWN_UNIT_MARKERS
            for v in values
        ) or len({v.strip() for v in values}) != len(values):
            raise ValueError("Repeated or missing biological unit in CADET: " + key)
    return {
        "checked_unit_keys": sorted(checked),
        "observations": len(obs),
        "status": "one_row_per_declared_identity",
        "independence_verified": False,
    }


def run_m2(op, paths, params, outputs):
    parameters = Parameters.model_validate(params)
    counts, obs, genes = read_marker_counts(paths["expression"])
    unit_audit = _check_independent_rows(obs, parameters.unit_key)
    ref = GaussianReference.model_validate_json(
        read_bounded_file(paths["reference"], 4 * 1024**2)
    )
    if (
        ref.source_sha256 == hash_bounded_file(paths["expression"], 1 << 40)
        or ref.feature_ids != genes
        or not len(genes) == len(ref.mean) == len(ref.theta)
    ):
        raise ValueError("Independent Gaussian reference must match feature axes")
    covariance = np.asarray(ref.covariance)
    # Independent discrete jitter restores each known marginal to N(0,1).
    # It cannot reconstruct a correlated Gaussian copula from tied counts.
    if not np.array_equal(covariance, np.eye(len(genes))):
        raise ValueError("NB quantile residuals require identity covariance")
    rng = np.random.default_rng(np.random.SeedSequence([parameters.seed, 1]))
    z = quantile_residuals(counts, np.array(ref.mean), np.array(ref.theta), rng)
    score, p, labels, event = cadet(
        z, genes.index(parameters.target_feature), covariance, parameters.seed
    )
    rows = [
        MarkerRow(
            gene_id=g,
            score=score,
            p_value=p,
            q_value=p,
            selected=p <= parameters.alpha,
            test_status="tested",
        )
        if g == parameters.target_feature
        else MarkerRow(gene_id=g, score=None, test_status="independently_filtered")
        for g in genes
    ]
    context = marker_context(paths["expression"], labels, obs, genes, parameters)
    return write_report(
        outputs,
        {
            "method": "cadet",
            "method_version": "cadet.two-means.1",
            "context": context.model_dump(mode="json"),
            "gene_statistics": [r.model_dump(mode="json") for r in rows],
            "rows_sha256": marker_rows_sha256(rows),
            "fdr_threshold": parameters.alpha,
            "target_feature": parameters.target_feature,
            "gaussian_reference_sha256": hash_bounded_file(
                paths["reference"], 4 * 1024**2
            ),
            "selection_event": event,
            "selection_event_sha256": content_sha256(event),
            "independent_review_required": True,
            "modeled_scale": "randomized_nb_quantile_residual",
            "null_regime": ref.null_regime,
            "unit_audit": unit_audit,
            "assumptions": "Independent rows; known Gaussian residual covariance.",
        },
        {
            "axis_labels": {
                "x": "Target feature",
                "y": "Difference in mean Gaussian residual",
            },
            "sample_count": len(obs),
            "donor_count": 0,
            "transformations": [
                "One observation per declared independent unit.",
                "Randomized NB quantile residual; conditional Lloyd-path test.",
            ],
            "columns": ["gene", "effect", "p_value"],
            "rows": [[parameters.target_feature, score, p]],
        },
    )
