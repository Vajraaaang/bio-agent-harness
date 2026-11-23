"""Donor-balanced, MuSiC-style bulk deconvolution on a shared linear gene axis.

The expression files are genes-by-samples (bulk) and genes-by-cells (reference)
CSV tables with a unique `gene_id` column and an optional `chromosome` column.
The reference metadata CSV has exactly `cell_id,donor_id,cell_type`. All axes
use exact identifiers. Declarations specify the expression scales because an
arbitrarily transformed table can masquerade as a linear one. Reference library
sizes and relative abundances use all non-outlier reference genes. The fit uses
matched genes. Each cell type must have at least two independent donors.

Method inspiration: Wang et al., Nature Communications (2019),
https://doi.org/10.1038/s41467-018-08023-x.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, NamedTuple, TypedDict

import numpy as np
import pandas as pd
from numpy.typing import NDArray
from pydantic import Field
from scipy.optimize import nnls
from scipy.stats import rankdata

from ._hashing import canonical_sha256
from ._rna_solver_inputs import (
    indexed_rows,
    numeric_matrix,
    raw_count_matrix,
    read_solver_csv,
    require_labels,
)
from .design import DESIGN_CONDITION_NUMBER_LIMIT
from .scientific_types import FrozenContract, Sha256, content_sha256

_Floats = NDArray[np.float64]


class BulkDeconvolutionSpec(FrozenContract):
    """Bind expression and metadata bytes, their scales, and the fit policy."""

    format: Literal["devbench.bulk-deconvolution/1"] = "devbench.bulk-deconvolution/1"
    bulk_sha256: Sha256
    reference_sha256: Sha256
    metadata_sha256: Sha256
    bulk_measurement: Literal["counts", "cpm"]
    reference_measurement: Literal["raw_counts"]
    min_shared_genes: int = Field(default=10, ge=3)
    exclude_ribosomal: bool = False
    variance_floor: float = Field(default=1e-10, ge=1e-16, le=1)
    tolerance: float = Field(default=1e-7, gt=0, le=0.01)
    max_iterations: int = Field(default=200, ge=1, le=1000)


class _Expression(NamedTuple):
    genes: tuple[str, ...]
    observations: tuple[str, ...]
    chromosomes: dict[str, str]
    values: _Floats


class _MassAudit(TypedDict):
    original_mass: int | float
    removed_mass: int | float
    removed_fraction: float | None


class BulkDeconvolutionSample(TypedDict):
    sample_id: str
    status: Literal["estimated", "not_estimable"]
    proportions: dict[str, float] | None
    rmse: float | None
    pearson_r: float | None
    spearman_r: float | None
    iterations: int
    missing_reason: str | None


class BulkDeconvolutionReceipt(TypedDict):
    format: Literal["devbench.bulk-deconvolution-result/1"]
    specification_sha256: str
    specification: dict[str, Any]
    input_sha256s: dict[str, str]
    receipt_sha256: str
    method: Literal["music_style_donor_reweighted_nnls"]
    quantity: Literal["relative_cell_type_proportions"]
    claim_scope: Literal["reference_conditioned_relative_composition"]
    clinical_release_allowed: Literal[False]
    status: Literal["completed", "partially_estimable", "no_estimable_samples"]
    reference_donors_by_cell_type: dict[str, list[str]]
    reference_average_library_size: dict[str, float]
    reference_library_size_scope: Literal["all_non_outlier_reference_genes"]
    reference_condition_number: float
    input_gene_counts: dict[str, int]
    retained_shared_gene_ids: list[str]
    outlier_audit: dict[str, Any]
    zero_cross_donor_variance_genes: int
    variance_floor: float
    gene_weight_policy: Literal["inverse_floored_cross_donor_variance"]
    reconstruction_scale: str
    limitations: list[str]
    samples: list[BulkDeconvolutionSample]


@dataclass(frozen=True)
class _Reference:
    cell_types: tuple[str, ...]
    donors: dict[str, list[str]]
    library_sizes: _Floats
    relative_sizes: _Floats
    signature: _Floats
    variance: _Floats
    condition_number: float


def _read_expression(
    path: Path, digest: str, *, measurement: Literal["counts", "raw_counts", "cpm"]
) -> _Expression:
    frame = read_solver_csv(path, digest)
    if "gene_id" not in frame:
        raise ValueError("deconvolution_gene_id_column_required")
    frame = indexed_rows(frame, "gene_id")
    genes = tuple(frame.index)
    chromosomes = (
        dict(zip(genes, frame.pop("chromosome"), strict=True))
        if "chromosome" in frame
        else {}
    )
    observations = tuple(frame.columns)
    if not observations:
        raise ValueError("deconvolution_requires_nonempty_sample_or_cell_ids")
    if measurement == "cpm":
        values = numeric_matrix(frame)
        if np.any(values < 0):
            raise ValueError("deconvolution_requires_nonnegative_linear_expression")
        if np.any(values.sum(axis=0) > 1_001_000):
            raise ValueError("deconvolution_cpm_mass_exceeds_one_million")
    else:
        try:
            values = raw_count_matrix(frame.T).T
        except ValueError as error:
            raise ValueError(
                "deconvolution_requires_exact_nonnegative_integer_counts"
            ) from error
    return _Expression(genes, observations, chromosomes, values)


def _reference_groups(
    labels: pd.DataFrame, cells: tuple[str, ...]
) -> dict[str, dict[str, list[int]]]:
    if set(labels.columns) != {"cell_id", "donor_id", "cell_type"}:
        raise ValueError("deconvolution_reference_metadata_columns_mismatch")
    require_labels(labels, ("cell_id", "donor_id", "cell_type"))
    cell_labels = tuple(labels["cell_id"])
    if len(set(cell_labels)) != len(cell_labels) or set(cell_labels) != set(cells):
        raise ValueError("deconvolution_metadata_cell_axis_mismatch")
    groups: dict[str, dict[str, list[int]]] = {}
    ordered = labels.set_index("cell_id").loc[list(cells)]
    for index, (donor, cell_type) in enumerate(
        zip(ordered["donor_id"], ordered["cell_type"], strict=True)
    ):
        groups.setdefault(cell_type, {}).setdefault(donor, []).append(index)
    if len(set(labels["donor_id"])) < 2:
        raise ValueError("deconvolution_requires_at_least_two_reference_donors")
    if any(len(donors) < 2 for donors in groups.values()):
        raise ValueError("deconvolution_requires_two_reference_donors_per_cell_type")
    return groups


def _outlier_mask(
    bulk: _Expression, reference: _Expression, *, exclude_ribosomal: bool
) -> set[str]:
    excluded = set()
    mitochondrial_chromosomes = {"M", "MT", "CHRM", "CHRMT"}
    for gene in set(bulk.genes) | set(reference.genes):
        symbol = gene.upper()
        chromosomes = {
            table.chromosomes.get(gene, "").strip().upper()
            for table in (bulk, reference)
        }
        if (
            chromosomes & mitochondrial_chromosomes
            or symbol.startswith(("MT-", "CHRM:", "CHRMT:", "CHRM_", "CHRMT_"))
            or symbol in {"MALAT1", "CHRM", "CHRMT"}
            or (exclude_ribosomal and symbol.startswith(("RPS", "RPL")))
        ):
            excluded.add(gene)
    return excluded


def _mass(values: _Floats, *, counts: bool) -> int | float:
    if not counts:
        return float(values.sum())
    # Chunk/convert only when integer accumulation could overflow uint64.
    if int(values.max(initial=0)) * values.size <= np.iinfo(np.uint64).max:
        return int(values.sum(dtype=np.uint64))
    return sum(int(value) for value in values.flat)


def _mass_audit(
    values: _Floats, excluded: NDArray[np.bool_], *, counts: bool
) -> _MassAudit:
    total = _mass(values, counts=counts)
    removed = _mass(values[excluded], counts=counts)
    return {
        "original_mass": total,
        "removed_mass": removed,
        "removed_fraction": removed / total if total else None,
    }


def _outlier_audit(
    bulk: _Expression,
    reference: _Expression,
    excluded: set[str],
    groups: dict[str, dict[str, list[int]]],
    spec: BulkDeconvolutionSpec,
) -> dict[str, Any]:
    bulk_mask = np.asarray([gene in excluded for gene in bulk.genes], dtype=bool)
    ref_mask = np.asarray([gene in excluded for gene in reference.genes], dtype=bool)
    bulk_counts = spec.bulk_measurement == "counts"
    bulk_mass = _mass_audit(bulk.values, bulk_mask, counts=bulk_counts)
    reference_mass = _mass_audit(reference.values, ref_mask, counts=True)
    return {
        "rules": ["mitochondrial_chromosome_or_identifier", "MALAT1"]
        + (["RPS*_and_RPL*"] if spec.exclude_ribosomal else []),
        "removed_bulk_gene_ids": sorted(excluded.intersection(bulk.genes)),
        "removed_reference_gene_ids": sorted(excluded.intersection(reference.genes)),
        "bulk_removed_fraction": bulk_mass["removed_fraction"],
        "reference_removed_fraction": reference_mass["removed_fraction"],
        "bulk_mass": bulk_mass,
        "reference_mass": reference_mass,
        "bulk_mass_by_sample": {
            sample: _mass_audit(bulk.values[:, index], bulk_mask, counts=bulk_counts)
            for index, sample in enumerate(bulk.observations)
        },
        "reference_mass_by_cell_type_and_donor": {
            cell_type: {
                donor: _mass_audit(reference.values[:, indexes], ref_mask, counts=True)
                for donor, indexes in donors.items()
            }
            for cell_type, donors in groups.items()
        },
    }


def _shared_axis(
    bulk: _Expression, reference: _Expression, excluded: set[str], minimum: int
) -> tuple[list[str], _Floats, list[int]]:
    reference_index = {gene: index for index, gene in enumerate(reference.genes)}
    pairs = [
        (index, reference_index[gene])
        for index, gene in enumerate(bulk.genes)
        if gene not in excluded and gene in reference_index
    ]
    if len(pairs) < minimum:
        raise ValueError("deconvolution_insufficient_shared_genes")
    bulk_rows, reference_rows = zip(*pairs, strict=True)
    shared_reference_rows = list(reference_rows)
    reference_values = reference.values[shared_reference_rows]
    # All-zero reference rows do not supply identifiable reference information.
    if np.count_nonzero(reference_values.any(axis=1)) < minimum:
        raise ValueError("deconvolution_insufficient_shared_genes_with_signal")
    return (
        [bulk.genes[index] for index in bulk_rows],
        bulk.values[list(bulk_rows)],
        shared_reference_rows,
    )


def _reference_profiles(
    reference: _Expression,
    groups: dict[str, dict[str, list[int]]],
    excluded: set[str],
    shared_rows: list[int],
) -> _Reference:
    cell_types = tuple(sorted(groups))
    retained = np.asarray([gene not in excluded for gene in reference.genes])
    mean_abundance, variance, library_sizes = [], [], []
    for cell_type in cell_types:
        donor_groups = groups[cell_type]
        totals = np.column_stack(
            [
                reference.values[:, donor_groups[donor]].sum(axis=1)
                for donor in sorted(donor_groups)
            ]
        )
        libraries = totals[retained].sum(axis=0)
        if np.any(libraries <= 0):
            raise ValueError("deconvolution_donor_cell_type_has_no_retained_expression")
        abundance = totals[shared_rows] / libraries
        mean = abundance.mean(axis=1)
        mean_abundance.append(mean)
        variance.append(abundance.var(axis=1, ddof=1))
        cells_per_donor = [len(donor_groups[donor]) for donor in sorted(donor_groups)]
        library_sizes.append(np.mean(libraries / cells_per_donor))
    sizes = np.asarray(library_sizes)
    relative_sizes = sizes / sizes.max()
    signature = np.column_stack(mean_abundance) * relative_sizes
    condition_number = float(np.linalg.cond(signature))
    if (
        np.linalg.matrix_rank(signature) != len(cell_types)
        or not np.isfinite(condition_number)
        or condition_number > DESIGN_CONDITION_NUMBER_LIMIT
    ):
        raise ValueError("deconvolution_reference_signatures_unidentifiable")
    return _Reference(
        cell_types,
        {cell_type: sorted(groups[cell_type]) for cell_type in cell_types},
        sizes,
        relative_sizes,
        signature,
        np.column_stack(variance),
        condition_number,
    )


def _correlation(observed: _Floats, reconstructed: _Floats) -> float | None:
    if np.ptp(observed) == 0 or np.ptp(reconstructed) == 0:
        return None
    value = float(np.corrcoef(observed, reconstructed)[0, 1])
    return float(np.clip(value, -1, 1)) if np.isfinite(value) else None


def _failure(
    sample_id: str, reason: str, iterations: int = 0
) -> BulkDeconvolutionSample:
    return {
        "sample_id": sample_id,
        "status": "not_estimable",
        "proportions": None,
        "rmse": None,
        "pearson_r": None,
        "spearman_r": None,
        "iterations": iterations,
        "missing_reason": reason,
    }


def _fit_sample(
    sample_id: str,
    observed: _Floats,
    reference: _Reference,
    spec: BulkDeconvolutionSpec,
) -> BulkDeconvolutionSample:
    if observed.sum() <= 0:
        return _failure(sample_id, "empty_bulk_profile_on_retained_genes")
    observed = observed / observed.sum()
    iteration = 0
    try:
        coefficients, _ = nnls(reference.signature, observed)
        for iteration in range(1, spec.max_iterations + 1):
            # Raw NNLS coefficients retain the common bulk scale during reweighting.
            # The shared numerator confines square-root weights to (0, 1].
            scaled_coefficients = coefficients * reference.relative_sizes
            variance = reference.variance @ scaled_coefficients**2
            weights = np.sqrt(
                spec.variance_floor / np.maximum(spec.variance_floor, variance)
            )
            weighted_signature = reference.signature * weights[:, None]
            if np.linalg.cond(weighted_signature) > DESIGN_CONDITION_NUMBER_LIMIT:
                return _failure(sample_id, "unstable_weighted_signature", iteration)
            updated, _ = nnls(weighted_signature, observed * weights)
            if np.linalg.norm(updated - coefficients, ord=1) <= spec.tolerance:
                coefficients = updated
                break
            coefficients = updated
        else:
            return _failure(sample_id, "reweighted_nnls_nonconverged", iteration)
    except (ValueError, RuntimeError, np.linalg.LinAlgError):
        return _failure(sample_id, "reweighted_nnls_numerical_failure", iteration)
    if not np.isfinite(coefficients).all() or coefficients.sum() <= 0:
        return _failure(sample_id, "empty_fitted_bulk_profile", iteration)
    proportions = coefficients / coefficients.sum()
    reconstructed = reference.signature @ proportions
    reconstructed /= reconstructed.sum()
    return {
        "sample_id": sample_id,
        "status": "estimated",
        "proportions": dict(
            zip(reference.cell_types, proportions.tolist(), strict=True)
        ),
        "rmse": float(np.sqrt(np.mean((observed - reconstructed) ** 2))),
        "pearson_r": _correlation(observed, reconstructed),
        "spearman_r": _correlation(rankdata(observed), rankdata(reconstructed)),
        "iterations": iteration,
        "missing_reason": None,
    }


def fit_bulk_deconvolution(
    bulk_path: Path,
    reference_path: Path,
    metadata_path: Path,
    spec: BulkDeconvolutionSpec,
) -> BulkDeconvolutionReceipt:
    """Estimate reference-relative cell fractions for independent bulk samples."""
    spec = BulkDeconvolutionSpec.model_validate(spec.model_dump())
    bulk = _read_expression(
        bulk_path, spec.bulk_sha256, measurement=spec.bulk_measurement
    )
    reference = _read_expression(
        reference_path, spec.reference_sha256, measurement=spec.reference_measurement
    )
    metadata = read_solver_csv(metadata_path, spec.metadata_sha256)
    groups = _reference_groups(metadata, reference.observations)
    excluded = _outlier_mask(bulk, reference, exclude_ribosomal=spec.exclude_ribosomal)
    audit = _outlier_audit(bulk, reference, excluded, groups, spec)
    shared_genes, target, shared_reference_rows = _shared_axis(
        bulk, reference, excluded, max(spec.min_shared_genes, len(groups) + 1)
    )
    profiles = _reference_profiles(reference, groups, excluded, shared_reference_rows)
    samples = [
        _fit_sample(sample, target[:, index], profiles, spec)
        for index, sample in enumerate(bulk.observations)
    ]
    estimated = sum(row["status"] == "estimated" for row in samples)
    status: Literal["completed", "partially_estimable", "no_estimable_samples"]
    if estimated == len(samples):
        status = "completed"
    elif estimated:
        status = "partially_estimable"
    else:
        status = "no_estimable_samples"
    declaration = spec.model_dump(mode="json")
    receipt: BulkDeconvolutionReceipt = {
        "format": "devbench.bulk-deconvolution-result/1",
        "specification_sha256": content_sha256(declaration),
        "specification": declaration,
        "input_sha256s": {
            "bulk": spec.bulk_sha256,
            "reference": spec.reference_sha256,
            "reference_metadata": spec.metadata_sha256,
        },
        "receipt_sha256": "",
        "method": "music_style_donor_reweighted_nnls",
        "quantity": "relative_cell_type_proportions",
        "claim_scope": "reference_conditioned_relative_composition",
        "clinical_release_allowed": False,
        "status": status,
        "reference_donors_by_cell_type": profiles.donors,
        "reference_average_library_size": dict(
            zip(profiles.cell_types, profiles.library_sizes.tolist(), strict=True)
        ),
        "reference_library_size_scope": "all_non_outlier_reference_genes",
        "reference_condition_number": profiles.condition_number,
        "input_gene_counts": {
            "bulk": len(bulk.genes),
            "reference": len(reference.genes),
        },
        "retained_shared_gene_ids": shared_genes,
        "outlier_audit": audit,
        "zero_cross_donor_variance_genes": int(
            np.count_nonzero((profiles.variance == 0).all(axis=1))
        ),
        "variance_floor": spec.variance_floor,
        "gene_weight_policy": "inverse_floored_cross_donor_variance",
        "reconstruction_scale": "unit_sum_relative_expression_on_retained_shared_genes",
        "limitations": [
            "Proportions are relative to the supplied reference cell types.",
            "Reference count depth is assumed to preserve relative RNA content.",
            "Gene matching and measurement scales rely on supplied declarations.",
            "Reconstruction metrics use the same retained genes as the fit.",
            "The model yields point estimates conditional on the retained reference.",
        ],
        "samples": samples,
    }
    receipt["receipt_sha256"] = canonical_sha256(
        {key: value for key, value in receipt.items() if key != "receipt_sha256"}
    )
    return receipt
