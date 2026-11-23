"""Perturbation response in PCA space and biological-unit pseudobulk DE."""

import math
import warnings
from pathlib import Path
from typing import Any, Literal

import numpy as np
import pandas as pd
from numpy.typing import NDArray
from pydantic import Field, model_validator
from scipy.spatial import cKDTree
from scipy.spatial.distance import cdist
from scipy.stats import t
from sklearn.decomposition import PCA
from sklearn.mixture import GaussianMixture

from rna_kernels.nb_irls import DenseDesign, NBIRLSGene, fit_statsmodels_nb
from rna_kernels.normalization import log1p_counts_per_10k

from ._rna_solver_inputs import (
    Label,
    adjust_probabilities,
    read_count_and_metadata,
    require_labels,
    solver_receipt,
)
from .design import require_design_condition
from .family_fitting import _calibrate_family_counts, conditional_nb_contrasts
from .observed_data import ObservedData
from .scientific_types import FrozenContract, Sha256

_Floats = NDArray[np.float64]
_Ints = NDArray[np.intp]
_Mask = NDArray[np.bool_]


class PerturbationSpec(FrozenContract):
    format: Literal["devbench.perturbation-response/1"] = (
        "devbench.perturbation-response/1"
    )
    counts_sha256: Sha256
    metadata_sha256: Sha256
    non_targeting_control: Label
    target_genes: tuple[tuple[Label, Label], ...] = ()
    n_neighbors: int = Field(default=10, ge=1, le=128)
    pca_components: int = Field(default=20, ge=1, le=100)
    n_perm: int = Field(default=999, ge=1, le=10_000)
    seed: int = Field(default=0, ge=0, le=2**32 - 1)

    @model_validator(mode="after")
    def target_gene_mapping(self) -> "PerturbationSpec":
        labels = [label for label, _ in self.target_genes]
        if len(labels) != len(set(labels)) or self.non_targeting_control in labels:
            raise ValueError("target_genes_require_distinct_perturbation_labels")
        return self


def _energy_scores(embedding: _Floats, assignments: _Floats) -> _Floats:
    """Evaluate U-statistics jointly without an observation-square matrix."""
    size, draws = assignments.shape
    treated = assignments.sum(axis=0)
    controls = size - treated
    within_treated = np.zeros(draws)
    to_treated = np.zeros(draws)
    total = 0.0
    block = min(256, size - 1)
    for i in range(0, size, block):
        x, selected_i = embedding[i : i + block], assignments[i : i + block]
        for j in range(0, size, block):
            y, selected_j = embedding[j : j + block], assignments[j : j + block]
            distances = cdist(x, y)
            within_treated += np.sum(selected_i * (distances @ selected_j), axis=0)
            to_treated += distances.sum(axis=0) @ selected_j
            total += float(distances.sum())
    cross = to_treated - within_treated
    within_controls = total - within_treated - 2 * cross
    return (
        2 * cross / (treated * controls)
        - within_treated / (treated * (treated - 1))
        - within_controls / (controls * (controls - 1))
    )


def _energy_test(
    embedding: _Floats, treated: _Mask, strata: list[_Ints], *, n_perm: int, seed: int
) -> dict[str, Any]:
    # Drop strata where labels cannot be exchanged before defining the test.
    mixed = [i for i in strata if treated[i].any() and not treated[i].all()]
    rows = np.concatenate(mixed) if mixed else np.array([], dtype=np.intp)
    size = len(rows)
    observed = treated[rows]
    eligible_embedding = embedding[rows]
    output: dict[str, Any] = {
        "distance": None,
        "p_value": None,
        "q_value": None,
        "n_treated_cells": int(observed.sum()),
        "n_control_cells": int(size - observed.sum()),
        "n_mixed_strata": len(mixed),
    }
    if min(output["n_treated_cells"], output["n_control_cells"]) < 2:
        return {**output, "status": "insufficient_exchangeable_cells"}
    boundaries = np.cumsum([0, *(len(block) for block in mixed)])
    rng = np.random.default_rng(seed)
    exceed = 0
    distance = 0.0
    batch_size = min(64, max(1, 100_000 // size))
    for start in range(0, n_perm + 1, batch_size):
        draws = min(batch_size, n_perm + 1 - start)
        labels = np.empty((size, draws), dtype=float)
        for column in range(draws):
            for lo, hi in zip(boundaries[:-1], boundaries[1:], strict=True):
                group = observed[lo:hi]
                labels[lo:hi, column] = (
                    group if start == column == 0 else rng.permutation(group)
                )
        statistics = _energy_scores(eligible_embedding, labels)
        if start == 0:
            distance = float(statistics[0])
            statistics = statistics[1:]
        exceed += int(np.count_nonzero(statistics >= distance - 1e-12))
    return {
        **output,
        "distance": distance,
        "p_value": (1 + exceed) / (1 + n_perm),
        "status": "tested",
    }


def _local_signatures(
    embedding: _Floats,
    treated: _Mask,
    strata: list[_Ints],
    k: int,
) -> tuple[_Floats, _Mask, list[tuple[_Ints, _Ints]]]:
    signatures = np.zeros_like(embedding)
    valid = np.zeros(len(embedding), dtype=bool)
    matched = []
    for group in strata:
        controls, targets = group[~treated[group]], group[treated[group]]
        neighbors = min(k, len(controls) - 1)
        if not len(targets) or neighbors < 1:
            continue
        tree = cKDTree(embedding[controls])
        chunk = max(1, 262_144 // (neighbors * embedding.shape[1]))
        for start in range(0, len(group), chunk):
            rows = group[start : start + chunk]
            _, nearest = tree.query(embedding[rows], k=neighbors + 1)
            self_matches = controls[nearest] == rows[:, None]
            chosen = np.take_along_axis(
                nearest, np.argsort(self_matches, kind="stable", axis=1), axis=1
            )[:, :neighbors]
            signatures[rows] = embedding[rows] - embedding[controls[chosen]].mean(
                axis=1
            )
        valid[group] = True
        matched.append((targets, controls))
    return signatures, valid, matched


def _mixture(
    signatures: _Floats, treated: _Mask, seed: int
) -> tuple[_Floats | None, float | None]:
    if min(np.count_nonzero(treated), np.count_nonzero(~treated)) < 2:
        return None, None
    direction = signatures[treated].mean(axis=0) - signatures[~treated].mean(axis=0)
    norm = np.linalg.norm(direction)
    if norm <= np.finfo(float).eps:
        return None, None
    scores = (signatures @ (direction / norm))[:, None]
    one = GaussianMixture(n_components=1, random_state=seed).fit(scores)
    two = GaussianMixture(n_components=2, n_init=5, random_state=seed).fit(scores)
    gain = float(one.bic(scores) - two.bic(scores))
    if not two.converged_ or gain <= 0:
        return None, gain
    posterior = two.predict_proba(scores)[:, int(two.means_.argmax())]
    if posterior[treated].mean() <= posterior[~treated].mean():
        return None, gain
    return posterior, gain


def _efficacy(
    embedding: _Floats,
    treated: _Mask,
    cell_ids: list[str],
    strata: list[_Ints],
    target: str | None,
    feature: _Floats | None,
    spec: PerturbationSpec,
) -> dict[str, Any]:
    signatures, eligible, matched = _local_signatures(
        embedding, treated, strata, spec.n_neighbors
    )
    posterior, gain = _mixture(signatures[eligible], treated[eligible], spec.seed)
    probabilities = (
        dict(zip(np.flatnonzero(eligible), posterior, strict=True))
        if (posterior is not None)
        else {}
    )
    knockdown = None
    if target is not None:
        difference = None
        if feature is not None and matched:
            difference = float(
                np.mean(
                    [
                        feature[targets].mean() - feature[controls].mean()
                        for targets, controls in matched
                    ]
                )
            )
        knockdown = {
            "gene_id": target,
            "measured": feature is not None,
            "log1p_cpm10k_difference": difference,
            "matched_strata": len(matched),
        }
    return {
        "status": "mixture_supported"
        if posterior is not None
        else "mixture_unresolved",
        "bic_improvement_over_one_component": gain,
        "target_gene_check": knockdown,
        "cells": [
            {
                "cell_id": cell_ids[i],
                "probability": float(probabilities[i]) if i in probabilities else None,
            }
            for i in np.flatnonzero(treated)
        ],
    }


def _pool_counts(
    counts: _Floats,
    metadata: pd.DataFrame,
) -> tuple[_Floats, NDArray, _Floats, dict]:
    groups = metadata.groupby(["perturbation", "donor_id"], sort=True).indices
    rows = []
    batch_levels = []
    for indices in groups.values():
        batches = metadata["batch"].iloc[indices].unique()
        if len(batches) != 1:
            raise ValueError("pseudobulk_unit_arm_has_mixed_batches")
        batch_levels.append(batches[0])
        rows.append(counts[indices].sum(axis=0))
    pooled = np.asarray(rows)
    totals = pooled.sum(axis=1)
    if np.any(totals > 2**53) or np.any(totals == 0):
        raise ValueError("pseudobulk_requires_positive_bounded_raw_libraries")

    return pooled, np.asarray(batch_levels), totals, groups


def _pseudobulk_input(
    counts: _Floats,
    metadata: pd.DataFrame,
    control: str,
    perturbation: str,
) -> tuple[_Floats, pd.DataFrame, dict[str, Any]]:
    pooled, batch, totals, groups = _pool_counts(counts, metadata)
    index = pd.MultiIndex.from_tuples(groups, names=["perturbation", "donor_id"])
    treated = (index.get_level_values("perturbation") == perturbation).astype(float)
    donors = index.get_level_values("donor_id")
    control_units = set(donors[treated == 0])
    treated_units = set(donors[treated == 1])
    support = {control: len(control_units), perturbation: len(treated_units)}
    if min(support.values()) < 2:
        raise ValueError("pseudobulk_requires_two_biological_units_per_arm")
    paired = bool(control_units & treated_units)
    if paired and control_units != treated_units:
        raise ValueError("pseudobulk_requires_complete_pairs_or_disjoint_units")
    nested = paired and all(len(set(batch[donors == donor])) == 1 for donor in donors)
    donor_levels = sorted(control_units)[1:] if paired else []
    batch_levels = [] if nested else sorted(set(batch))[1:]
    terms = 2 + len(donor_levels) + len(batch_levels)
    if len(pooled) * terms**2 > 4_000_000:
        raise ValueError("pseudobulk_design_over_budget")
    design_cols = {
        "intercept": np.ones(len(pooled)),
        "perturbation": treated,
        **{f"donor={donor}": (donors == donor).astype(float) for donor in donor_levels},
        **{f"batch={label}": (batch == label).astype(float) for label in batch_levels},
    }
    design = pd.DataFrame(
        design_cols, index=[f"pseudobulk-{i}" for i in range(len(pooled))]
    )
    diagnostics = require_design_condition(design)
    if diagnostics["residual_df"] < 1:
        raise ValueError("pseudobulk_requires_residual_degrees_of_freedom")
    diagnostics.update(
        paired=paired,
        batch_nested_in_donor=nested,
        biological_units_per_group=support,
        model_terms=list(design.columns),
        pseudobulk_rows=[
            {
                "sample_id": sample,
                "perturbation": arm,
                "donor_id": donor,
                "n_cells": len(groups[arm, donor]),
                "total_counts": int(total),
            }
            for sample, (arm, donor), total in zip(
                design.index, index, totals, strict=True
            )
        ],
    )
    return pooled.T, design, diagnostics


def _de_estimate(fit: Any, contrast: _Floats, alpha: float) -> dict[str, Any]:
    try:
        projection = conditional_nb_contrasts(fit, contrast)
    except (ValueError, ArithmeticError, np.linalg.LinAlgError):
        projection = None
    if projection is None:
        return {"missing_reason": "nb_contrast_covariance_unavailable"}
    ((effect, variance, _),) = projection
    if not math.isfinite(effect) or not math.isfinite(variance) or variance <= 0:
        return {"missing_reason": "nb_contrast_covariance_unavailable"}
    statistic = abs(effect) / math.sqrt(variance)
    if not math.isfinite(statistic):
        return {"missing_reason": "nb_wald_statistic_nonfinite"}
    return {
        "log2_fold_change": float(effect / math.log(2)),
        "dispersion_alpha": alpha,
        "p_value": max(math.ulp(0.0), float(2 * t.sf(statistic, fit.df_resid))),
    }


def _fit_de(
    counts: _Floats,
    genes: list[str],
    design: pd.DataFrame,
    offset: _Floats,
    dispersions: dict,
    failed: dict,
) -> tuple[list[dict[str, Any]], list[str]]:
    rows: list[dict[str, Any]] = [
        {
            "gene_id": gene,
            "log2_fold_change": None,
            "dispersion_alpha": None,
            "p_value": None,
            "q_value": None,
        }
        for gene in genes
    ]
    numeric = DenseDesign(design.to_numpy())
    contrast = np.zeros((1, len(design.columns)))
    contrast[0, 1] = 1
    eligible_indices, alphas = [], []
    for i, gene in enumerate(genes):
        info = dispersions.get(gene, {})
        alpha = info.get("dispersion_alpha")
        if gene in failed:
            rows[i]["missing_reason"] = failed[gene]
        elif (
            not info.get("dispersion_converged")
            or alpha is None
            or not math.isfinite(alpha)
            or alpha < 1e-7
        ):
            rows[i]["missing_reason"] = "dispersion_unavailable_or_nonconverged"
        elif not (
            counts[i, design["perturbation"] == 1].any()
            and counts[i, design["perturbation"] == 0].any()
        ):
            rows[i]["missing_reason"] = "arm_without_count_support"
        else:
            eligible_indices.append(i)
            alphas.append(alpha)

    with warnings.catch_warnings(record=True) as observed:
        warnings.simplefilter("always")
        block = min(512, max(1, 131_072 // len(design)))
        for start in range(0, len(eligible_indices), block):
            indices = eligible_indices[start : start + block]
            values = alphas[start : start + block]
            models = fit_statsmodels_nb(numeric, counts[indices], values, offset)
            for j, i in enumerate(indices):
                if models.converged[j]:
                    rows[i].update(
                        _de_estimate(NBIRLSGene(models, j), contrast, values[j])
                    )
                else:
                    rows[i]["missing_reason"] = "nb_irls_nonconvergence"
    for row, q_value in zip(
        rows, adjust_probabilities([row["p_value"] for row in rows]), strict=True
    ):
        row["q_value"] = q_value
    return rows, list(dict.fromkeys(str(entry.message) for entry in observed))


def _pseudobulk_de(
    pooled: _Floats,
    design: pd.DataFrame,
    diagnostics: dict[str, Any],
    genes: list[str],
) -> dict[str, Any]:
    metadata = pd.DataFrame(index=design.index)
    data = ObservedData(
        expression=pooled,
        gene_ids=tuple(genes),
        sample_ids=tuple(design.index),
        metadata=metadata,
        annotations={},
        measurement="counts",
        unit_hierarchy={},
        targets=(),
        provenance={},
    )
    calibration: dict[str, Any] = {}
    _, dispersions, failed, offset = _calibrate_family_counts(
        data, design, calibration, False
    )
    rows, model_warnings = _fit_de(pooled, genes, design, offset, dispersions, failed)
    return {
        "design_diagnostics": diagnostics,
        "normalization": calibration["normalization"],
        "calibration": {
            key: calibration["calibration"].get(key)
            for key in (
                "method",
                "panel_selection",
                "panel_gene_count",
                "calibration_scope",
            )
        },
        "warnings": calibration["calibration_warnings"] + model_warnings,
        "inference": (
            "Residual-df t inference conditional on fitted dispersion and offsets."
        ),
        "gene_statistics": rows,
    }


def fit_perturbation_response(
    counts_path: Path, metadata_path: Path, spec: PerturbationSpec
) -> dict[str, Any]:
    """Use cell-by-gene raw counts and aligned cell_id, donor_id, perturbation
    and optional batch metadata. Guide and control names are treated literally.
    """
    spec = PerturbationSpec.model_validate(spec.model_dump())
    counts, metadata, genes = read_count_and_metadata(
        counts_path, metadata_path, spec.counts_sha256, spec.metadata_sha256, "cell_id"
    )
    metadata = metadata.copy()
    if "batch" not in metadata:
        metadata["batch"] = "unspecified"
    require_labels(metadata, ("donor_id", "perturbation", "batch"))
    control = spec.non_targeting_control
    perturbations = set(metadata["perturbation"])
    if control not in perturbations:
        raise ValueError("declared_non_targeting_control_is_absent")
    perturbations.remove(control)
    if not perturbations or set(dict(spec.target_genes)) - perturbations:
        raise ValueError("perturbation_and_declared_target_labels_must_be_observed")
    totals = counts.sum(axis=1)
    if np.any(totals == 0):
        raise ValueError("perturbation_pca_requires_positive_cell_library_sizes")
    expression = log1p_counts_per_10k(counts, totals[:, None])
    components = min(spec.pca_components, *expression.shape)
    embedding = PCA(
        n_components=components,
        svd_solver="randomized",
        random_state=spec.seed,
    ).fit_transform(expression)
    results = []
    target_genes = dict(spec.target_genes)
    for perturbation in sorted(perturbations):
        active = metadata["perturbation"].isin([control, perturbation]).to_numpy()
        cells = metadata.loc[active]
        pooled, design, diagnostics = _pseudobulk_input(
            counts[active], cells, control, perturbation
        )
        strata = list(cells.groupby(["donor_id", "batch"], sort=True).indices.values())
        treated = (cells["perturbation"] == perturbation).to_numpy()
        target = target_genes.get(perturbation)
        feature = expression[active, genes.index(target)] if target in genes else None
        results.append(
            {
                "perturbation": perturbation,
                "energy_test": _energy_test(
                    embedding[active],
                    treated,
                    strata,
                    n_perm=spec.n_perm,
                    seed=spec.seed,
                ),
                "efficacy": _efficacy(
                    embedding[active],
                    treated,
                    cells.index.tolist(),
                    strata,
                    target,
                    feature,
                    spec,
                ),
                "pseudobulk_de": _pseudobulk_de(pooled, design, diagnostics, genes),
            }
        )
    for row, q_value in zip(
        results,
        adjust_probabilities([r["energy_test"]["p_value"] for r in results]),
        strict=True,
    ):
        row["energy_test"]["q_value"] = q_value
    return solver_receipt(
        "devbench.perturbation-response-result/1",
        spec,
        {"counts": spec.counts_sha256, "metadata": spec.metadata_sha256},
        {
            "status": "completed",
            "claim_scope": "replicate_aware_perturbation_association",
            "count1_unchanged": True,
            "pca_components_used": components,
            "perturbations": results,
            "method": (
                "Energy U-test on log1p library-normalized PCA, conditional on cell "
                "label exchangeability in mixed donor-batch strata. The unbiased "
                "estimate can be negative. Exploratory efficacy uses matched local "
                "control signatures and a projected two-component GMM. "
                "DE uses raw donor-by-perturbation sums, RLE offsets, empirical-Bayes "
                "NB dispersions and conditional NB IRLS. BH spans the perturbations "
                "for the energy test and the gene family for each DE contrast."
            ),
        },
    )
