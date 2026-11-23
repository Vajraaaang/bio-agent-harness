"""Measured expression, replication, contrast and representation diagnostics.

All numerical analysis runs in a supervised worker. Diagnostic thresholds are
explicit exploratory settings, not validated clinical or GxP acceptance limits.
"""

import hashlib
import json
import math
from numbers import Real
from pathlib import Path
from typing import Any, Literal, TypedDict

import numpy as np

from devharness_core.normalization import log1p_counts_per_10k
from devharness_core.stages import parse_stages
from devharness_core.storage import ChunkedExpressionStore
from devharness_core.units import declared_unit_counts
from devharness_core.watchdog import run_sandboxed
from devharness_server.tools_lineage import (
    _bounded_int,
    _bounded_metadata,
    _matrix_sha256,
)
from rna_kernels.embedding_qc import (
    EMBEDDING_THRESHOLDS,
    embedding_rank,
)
from rna_kernels.embedding_qc import loses_neighbors as _loses_neighbors
from rna_kernels.embedding_qc import neighborhood_metrics as _neighborhood_metrics

_DEFAULT_THRESHOLDS = {
    "dropout_warning": 0.5,
    "variance_retention_warning": 0.2,
    **EMBEDDING_THRESHOLDS,
}


class _AuditReport(TypedDict):
    status: str
    diagnostics: list[dict[str, str]]
    metrics: dict[str, Any]
    thresholds: dict[str, float]
    provenance: dict[str, Any]
    limitations: list[str]


class _InvalidInput(ValueError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


def _add(report: _AuditReport, code: str, severity: str, message: str) -> None:
    report["diagnostics"].append(
        {"code": code, "severity": severity, "message": message}
    )


def _finish(report: _AuditReport) -> _AuditReport:
    severities = {row["severity"] for row in report["diagnostics"]}
    report["status"] = (
        "fail"
        if "error" in severities
        else "warning"
        if "warning" in severities
        else "pass"
    )
    return report


def _profile(
    store: ChunkedExpressionStore, selected: np.ndarray, data_type: str
) -> dict:
    """Full quality scan in <=8 MB gene chunks; geometry variance uses <=500 cells."""
    genes, samples = store.gene_names, store.sample_names
    if len(samples) > 100000 or len(genes) > 1000000:
        raise _InvalidInput(
            "audit_size_limit",
            "QC supports at most 100000 samples and one million gene labels per "
            "supervised audit",
        )
    sums = np.zeros(len(samples))
    nonzero_per_sample = np.zeros(len(samples), dtype=np.int64)
    fingerprints = [hashlib.sha256() for _ in samples]
    digest = hashlib.sha256(
        json.dumps(
            {"genes": genes, "samples": samples},
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode()
    )
    zeros = missing = nonfinite = negative = noninteger = 0
    variance_values: list[float] = []
    chunk_size = max(1, min(128, (8 * 1024 * 1024) // (8 * len(samples))))
    for _, block in store.iter_gene_chunks(chunk_size=chunk_size):
        values = np.asarray(block, dtype=float)
        finite = np.isfinite(values)
        missing += int(np.isnan(values).sum())
        nonfinite += int((~finite).sum())
        negative += int((values < 0).sum())
        zeros += int((values == 0).sum())
        noninteger += int((finite & (values != np.floor(values))).sum())
        # Summaries are marked unavailable on any nonfinite input below; this
        # temporary finite-only sum is never presented as imputed expression.
        with np.errstate(over="ignore", invalid="ignore"):
            sums += np.where(finite, values, 0).sum(axis=0)
            variance = np.var(values[:, selected], axis=1)
        variance_values.extend(variance.tolist())
        nonzero_per_sample += (finite & (values != 0)).sum(axis=0)
        canonical = np.asarray(values, dtype="<f8", order="C").copy()
        canonical[canonical == 0] = (
            0  # Canonicalize signed zero for exact-profile equality.
        )
        digest.update(canonical.tobytes())
        for sample_index, fingerprint in enumerate(fingerprints):
            fingerprint.update(canonical[:, sample_index].tobytes())
    total = len(genes) * len(samples)
    valid_sums = not nonfinite and bool(np.isfinite(sums).all())
    sum_summary = (
        {
            "minimum": float(sums.min()),
            "median": float(np.median(sums)),
            "maximum": float(sums.max()),
        }
        if valid_sums
        else None
    )
    integer = not nonfinite and not negative and not noninteger
    is_counts = data_type != "continuous" and integer
    selected_library_sizes = sums[selected] if is_counts and valid_sums else None
    variances = np.asarray(variance_values)
    if selected_library_sizes is not None and np.isfinite(variances).all():
        offset = 0
        for _, block in store.iter_gene_chunks(128, sample_indices=selected):
            transformed = log1p_counts_per_10k(block, selected_library_sizes)
            variances[offset : offset + len(block)] = np.var(transformed, axis=1)
            offset += len(block)
    return {
        "gene_count": len(genes),
        "sample_count": len(samples),
        "zero_fraction": zeros / total,
        "missing_value_count": missing,
        "nonfinite_value_count": nonfinite,
        "negative_value_count": negative,
        "nonnegative_integer_expression": bool(integer),
        "library_size_summary": sum_summary if is_counts else None,
        "nonzero_per_sample": dict(
            zip(samples, nonzero_per_sample.tolist(), strict=True)
        ),
        "column_sum_summary": sum_summary,
        "zero_expression_samples": int((nonzero_per_sample == 0).sum()),
        "all_zero_expression": zeros == total,
        "unique_expression_profiles": len({fp.digest() for fp in fingerprints}),
        "expression_sha256": digest.hexdigest(),
        "variances": variances,
        "geometry_library_sizes": selected_library_sizes,
        "summary_overflow": not nonfinite and not valid_sums,
    }


def _selected_genes(
    store: ChunkedExpressionStore, profile: dict, top_genes: int
) -> list:
    variances = profile["variances"]
    threshold = 1e-12 if profile["geometry_library_sizes"] is not None else 0
    eligible = [
        i
        for i, variance in enumerate(variances)
        if math.isfinite(variance) and variance > threshold
    ]
    eligible.sort(key=lambda i: (-variances[i], store.gene_names[i]))
    return [store.gene_names[i] for i in eligible[:top_genes]]


def _read_genes(
    store: ChunkedExpressionStore, genes: list, selected: np.ndarray
) -> np.ndarray:
    lookup = {gene: index for index, gene in enumerate(store.gene_names)}
    return np.asarray(
        store.get_chunk([lookup[gene] for gene in genes], selected), dtype=float
    )


def _geometry(
    expression: np.ndarray, library_sizes: np.ndarray | None, tolerance: float
) -> tuple:
    from sklearn.decomposition import PCA
    from sklearn.preprocessing import StandardScaler

    if not np.isfinite(expression).all():
        raise _InvalidInput(
            "nonfinite_representation", "Representation contains nonfinite values"
        )
    if not expression.size or expression.shape[1] < 2:
        return np.empty((expression.shape[1], 0)), 0
    values = (
        log1p_counts_per_10k(expression, library_sizes)
        if library_sizes is not None
        else expression
    ).T
    standardized = StandardScaler().fit_transform(values)
    if not np.isfinite(standardized).all():
        raise _InvalidInput(
            "representation_overflow",
            "Expression standardization overflowed; rescale input",
        )
    if np.max(np.ptp(standardized, axis=0)) == 0:
        return np.zeros((len(standardized), 1)), 0
    pca = PCA(
        n_components=min(30, len(standardized) - 1, standardized.shape[1]),
        svd_solver="full",
    )
    coordinates = pca.fit_transform(standardized)
    rank = int(np.sum(pca.singular_values_ > tolerance * pca.singular_values_[0]))
    return coordinates[:, : max(rank, 1)], rank


def _contrast(
    report: _AuditReport, metadata, contrast: dict, time_col: str | None
) -> None:
    metrics = report["metrics"]
    metrics["contrast_valid"] = False
    required = ("group_col", "numerator", "denominator")
    if not isinstance(contrast, dict) or any(
        not isinstance(contrast.get(key), str) or not contrast[key] for key in required
    ):
        _add(
            report,
            "invalid_contrast",
            "error",
            "Contrast requires nonempty group_col, numerator and denominator strings",
        )
        return
    column, numerator, denominator = (contrast[key] for key in required)
    if column not in metadata or numerator == denominator:
        _add(
            report,
            "invalid_contrast",
            "error",
            "Contrast group column must exist and requested levels must be distinct",
        )
        return
    groups = metadata[column].astype(str)
    subset = metadata.loc[groups.isin([numerator, denominator])].copy()
    group_counts = {
        level: int((groups == level).sum()) for level in (numerator, denominator)
    }
    metrics["contrast"] = {
        "group_col": column,
        "numerator": numerator,
        "denominator": denominator,
        "sample_counts": group_counts,
        "fitted": False,
    }
    if not all(group_counts.values()):
        _add(
            report,
            "invalid_contrast",
            "error",
            "At least one requested contrast level has no measured samples; "
            "no model was fitted",
        )
        return
    independent = {}
    for level in (numerator, denominator):
        counts, incomplete = declared_unit_counts(
            subset.loc[subset[column].astype(str) == level]
        )
        independent[level] = min(counts.values()) if counts and not incomplete else None
    metrics["contrast"]["specimen_counts"] = {
        level: int(
            subset.loc[subset[column].astype(str) == level, "specimen_id"].nunique()
        )
        if "specimen_id" in subset
        else None
        for level in (numerator, denominator)
    }
    metrics["contrast"]["declared_biological_unit_counts"] = independent
    if any(count is not None and count < 2 for count in independent.values()):
        _add(
            report,
            "insufficient_independent_replicates",
            "error",
            "Each requested group needs at least two declared biological units; unique "
            "specimens cannot erase a shared donor or biological unit",
        )
        return
    y = (subset[column].astype(str) == numerator).astype(float)
    # Residualization checks estimability against batch intercepts and numeric
    # time without constructing an unbounded specimen-by-specimen design.
    batch = (
        subset["batch"].astype(str)
        if "batch" in subset
        else np.zeros(len(subset), dtype=int)
    )
    residual_y = y - y.groupby(batch).transform("mean")
    alias = np.linalg.norm(residual_y.to_numpy()) < 1e-10
    covariates = ["batch"] if "batch" in subset else ["Intercept"]
    if time_col is not None and time_col in subset:
        try:
            numeric = subset[time_col].copy()
            numeric.loc[:] = parse_stages(numeric)
            numeric = numeric.astype(float)
            residual_time = numeric - numeric.groupby(batch).transform("mean")
            t = residual_time.to_numpy()
            yr = residual_y.to_numpy()
            if np.linalg.norm(t) > 1e-10:
                alias = alias or _time_alias(t, yr)
            covariates.append(time_col)
        except (ValueError, TypeError):
            _add(
                report,
                "invalid_contrast_time",
                "error",
                "Numeric time could not be checked for contrast aliasing",
            )
            return
    metrics["contrast"]["checked_covariates"] = covariates
    metrics["contrast"]["aliased_with_covariates"] = bool(alias)
    if alias:
        _add(
            report,
            "confounded_contrast",
            "error",
            "Requested group effect is aliased with batch/time; a causal adjusted "
            "contrast is not identifiable and was not fitted",
        )
        return
    metrics["contrast_valid"] = True


def _time_alias(time_residual: np.ndarray, contrast_residual: np.ndarray) -> bool:
    projected = (
        time_residual
        * (time_residual @ contrast_residual)
        / (time_residual @ time_residual)
    )
    remainder = float(np.linalg.norm(contrast_residual - projected))
    scale = max(1.0, float(np.linalg.norm(contrast_residual)))
    return remainder <= 1e-10 * scale


def _record_profile(
    report: _AuditReport,
    profile: dict,
    sample_count: int,
    data_type: str,
    layer: str | None,
) -> bool:
    metrics, provenance = report["metrics"], report["provenance"]
    metrics.update(
        {
            key: value
            for key, value in profile.items()
            if key
            not in (
                "variances",
                "expression_sha256",
                "geometry_library_sizes",
                "summary_overflow",
            )
        }
    )
    provenance["expression_sha256"] = profile["expression_sha256"]
    if layer is not None:
        provenance["layer"] = layer
    provenance["requested_data_type"] = data_type
    invalid_counts = (
        data_type == "counts" and not profile["nonnegative_integer_expression"]
    )
    if invalid_counts:
        _add(
            report,
            "invalid_declared_counts",
            "error",
            "Declared counts require finite, nonnegative integer-valued expression",
        )
    provenance["expression_hash_scope"] = (
        "complete scanned numeric matrix plus ordered gene and sample IDs"
    )
    nonfinite = profile["nonfinite_value_count"]
    if nonfinite:
        _add(
            report,
            "nonfinite_expression",
            "error",
            "Expression includes missing or infinite values; "
            "no imputation or representation comparison was performed",
        )
    overflow = profile["summary_overflow"] or (
        not nonfinite and not np.isfinite(profile["variances"]).all()
    )
    if overflow:
        _add(
            report,
            "expression_overflow",
            "error",
            "Finite input overflowed numerical summaries; rescale expression",
        )
    if profile["all_zero_expression"]:
        _add(
            report,
            "all_zero_expression",
            "error",
            "All measured expression values are zero; no biological geometry can be "
            "inferred",
        )
    elif profile["zero_expression_samples"]:
        _add(
            report,
            "zero_expression_samples",
            "error",
            "Some samples have no nonzero finite expression; "
            "investigate failed libraries before modeling",
        )
    if profile["zero_fraction"] >= report["thresholds"]["dropout_warning"]:
        _add(
            report,
            "high_dropout",
            "warning",
            "Observed zero fraction exceeds the configured threshold; "
            "zeros alone do not establish a dropout mechanism",
        )
    if profile["unique_expression_profiles"] < sample_count:
        _add(
            report,
            "duplicate_expression_profiles",
            "warning",
            "Exact repeated expression profiles were measured; "
            "inspect technical duplication before treating samples as independent",
        )
    return not (invalid_counts or nonfinite or overflow)


def _record_sample_diagnostics(
    report: _AuditReport,
    metadata,
    sample_count: int,
    time_col: str | None,
    contrast: dict | None,
) -> None:
    metrics = report["metrics"]
    unit_counts, incomplete_units = declared_unit_counts(metadata)
    metrics["declared_unit_counts"] = unit_counts
    metrics["incomplete_unit_columns"] = incomplete_units
    metrics["independence_established"] = False
    if unit_counts and not incomplete_units:
        effective = min(unit_counts.values())
        metrics["effective_sample_count"] = effective
        metrics["declared_specimen_count"] = unit_counts.get("specimen_id")
        if effective < sample_count:
            _add(
                report,
                "repeated_specimens",
                "warning",
                "Multiple observations share a declared biological identity; "
                "sample count is not independent replicate count",
            )
    else:
        metrics["effective_sample_count"] = None
        _add(
            report,
            "independence_unverified",
            "warning",
            "Complete specimen IDs are unavailable; "
            "biological replicate count cannot be inferred from cell IDs",
        )
    if time_col in metadata:
        try:
            numeric_time = parse_stages(metadata[time_col])
            metrics["distinct_observed_times"] = len(np.unique(numeric_time))
        except ValueError:
            _add(
                report,
                "invalid_time_labels",
                "error",
                "Sampling-time labels could not be converted to finite numeric ages",
            )
    elif time_col is not None:
        _add(
            report,
            "missing_time_metadata",
            "warning",
            "Requested sampling-time column is unavailable",
        )
    if contrast is not None:
        _contrast(report, metadata, contrast, time_col)


def _compare_reference(
    report: _AuditReport,
    store: ChunkedExpressionStore,
    reference_counts_path: str,
    reference_data_type: str,
    selected: np.ndarray,
    samples: list[str],
    library_sizes: np.ndarray | None,
    top_genes: int,
    n_neighbors: int,
) -> None:
    thresholds, provenance = report["thresholds"], report["provenance"]
    tolerance = thresholds["rank_relative_tolerance"]
    with ChunkedExpressionStore(reference_counts_path) as reference:
        if set(reference.sample_names) != set(store.sample_names) or set(
            reference.gene_names
        ) != set(store.gene_names):
            raise _InvalidInput(
                "reference_alignment",
                "Reference and candidate must contain exactly the same gene and "
                "sample IDs",
            )
        lookup = {sample: index for index, sample in enumerate(reference.sample_names)}
        reference_selected = np.asarray([lookup[sample] for sample in samples])
        profile = _profile(reference, reference_selected, reference_data_type)
        if (
            profile["nonfinite_value_count"]
            or profile["summary_overflow"]
            or not np.isfinite(profile["variances"]).all()
        ):
            raise _InvalidInput(
                "nonfinite_reference",
                "Reference expression must be finite with valid numerical summaries",
            )
        if (
            reference_data_type == "counts"
            and not profile["nonnegative_integer_expression"]
        ):
            raise _InvalidInput(
                "invalid_reference_counts",
                "Declared reference counts require nonnegative integer-valued "
                "expression",
            )
        genes = _selected_genes(reference, profile, top_genes)
        ref_values = _read_genes(reference, genes, reference_selected)
        candidate_values = _read_genes(store, genes, selected)
        reference_libraries = profile["geometry_library_sizes"]
        ref_geometry, ref_rank = _geometry(ref_values, reference_libraries, tolerance)
        candidate_geometry, candidate_rank = _geometry(
            candidate_values, library_sizes, tolerance
        )
        reference_variance = float(np.var(ref_values, axis=1).sum()) if genes else 0.0
        candidate_variance = (
            float(np.var(candidate_values, axis=1).sum()) if genes else 0.0
        )
        retention = (
            candidate_variance / reference_variance if reference_variance > 0 else None
        )
        comparison = {
            "variance_retention": retention,
            "reference_rank": ref_rank,
            "candidate_rank": candidate_rank,
            "selected_gene_count": len(genes),
            "variance_scale": "as supplied, on reference-selected variable genes",
        }
        if retention is not None and not math.isfinite(retention):
            retention = comparison["variance_retention"] = None
            _add(
                report,
                "reference_scale_overflow",
                "error",
                "Reference/candidate variance ratio overflowed; verify comparable "
                "units and rescale inputs",
            )
        provenance["reference_expression_sha256"] = profile["expression_sha256"]
        provenance["reference_geometry_transform"] = (
            "log1p_cp10k" if reference_libraries is not None else "continuous"
        )
        report["metrics"]["reference_comparison"] = comparison
        unavailable = {
            "trustworthiness": None,
            "neighborhood_retention": None,
            "comparison_available": False,
        }
        if not ref_rank or reference_variance <= 0:
            _add(
                report,
                "unusable_reference",
                "error",
                "Reference has no variable expression; variance and neighborhood "
                "retention are undefined",
            )
            comparison.update(unavailable)
        elif not candidate_rank:
            comparison.update(unavailable)
            _add(
                report,
                "reference_geometry_collapse",
                "error",
                "Candidate expression is constant despite a variable aligned reference",
            )
        else:
            comparison.update(
                _neighborhood_metrics(ref_geometry, candidate_geometry, n_neighbors)
            )
            if _loses_neighbors(comparison, thresholds):
                _add(
                    report,
                    "reference_neighborhood_loss",
                    "warning",
                    "Candidate loses neighborhoods relative to the aligned reference "
                    "under the configured thresholds",
                )
        if (
            retention is not None
            and retention < thresholds["variance_retention_warning"]
        ):
            _add(
                report,
                "reference_variance_loss",
                "warning",
                "Candidate retains little reference-gene variance; "
                "investigate overcorrection or measurement rescaling",
            )


def _embedding_coordinates(
    embedding, samples: list[str], columns: list[str]
) -> np.ndarray:
    try:
        # Finiteness is a full-input QC property, even though
        # expensive geometry is restricted to the selected cells.
        for start in range(0, len(embedding), 512):
            block = embedding.iloc[start : start + 512][columns].to_numpy(dtype=float)
            if not np.isfinite(block).all():
                raise _InvalidInput(
                    "nonfinite_embedding",
                    "Embedding coordinates must be finite, "
                    "including samples outside the geometry subset",
                )
        values = (
            embedding.set_index("sample_id").loc[samples, columns].to_numpy(dtype=float)
        )
    except _InvalidInput:
        raise
    except (TypeError, ValueError):
        raise _InvalidInput(
            "invalid_embedding_values", "Embedding coordinates must be numeric"
        ) from None
    if not np.isfinite(values).all():
        raise _InvalidInput(
            "nonfinite_embedding", "Embedding coordinates must be finite"
        )
    return values


def _compare_embedding(
    report: _AuditReport,
    candidate_embedding_path: str,
    sample_names: list[str],
    samples: list[str],
    raw_geometry: np.ndarray,
    raw_rank: int,
    n_neighbors: int,
) -> None:
    metrics, thresholds = report["metrics"], report["thresholds"]
    embedding = _bounded_metadata(candidate_embedding_path)
    report["provenance"]["candidate_embedding_sha256"] = embedding.attrs[
        "source_sha256"
    ]
    metrics["embedding_valid"] = False
    if set(embedding["sample_id"]) != set(sample_names):
        raise _InvalidInput(
            "embedding_sample_alignment",
            "Embedding must contain exactly the measured sample IDs",
        )
    columns = [column for column in embedding if column != "sample_id"]
    if not columns or len(columns) > 100:
        raise _InvalidInput(
            "invalid_embedding_columns",
            "Embedding requires 1 to 100 numeric coordinate columns",
        )
    values = _embedding_coordinates(embedding, samples, columns)
    rank = embedding_rank(values, thresholds["rank_relative_tolerance"])
    comparison: dict[str, Any] = {
        "rank": rank,
        "raw_rank": raw_rank,
        "dimensions": len(columns),
    }
    metrics["embedding"] = comparison
    if not rank or not raw_rank:
        comparison.update(
            {
                "trustworthiness": None,
                "neighborhood_retention": None,
                "comparison_available": False,
            }
        )
        if not rank:
            _add(
                report,
                "collapsed_embedding",
                "error",
                "Candidate embedding is constant; neighborhood and lineage ordering "
                "are undefined",
            )
        else:
            _add(
                report,
                "embedding_reference_unavailable",
                "error",
                "Raw-expression geometry is degenerate; embedding fidelity cannot be "
                "evaluated",
            )
        return
    comparison.update(_neighborhood_metrics(raw_geometry, values, n_neighbors))
    if _loses_neighbors(comparison, thresholds):
        _add(
            report,
            "embedding_neighborhood_loss",
            "warning",
            "Embedding fails the configured raw-expression neighborhood fidelity "
            "check; "
            "investigate oversmoothing or alternate preprocessing",
        )
    elif comparison["comparison_available"]:
        metrics["embedding_valid"] = True
    else:
        _add(
            report,
            "insufficient_geometry_samples",
            "warning",
            "At least four samples are required for neighborhood fidelity diagnostics",
        )
    if rank < raw_rank:
        _add(
            report,
            "representation_rank_reduction",
            "info",
            "Embedding rank is lower than raw-expression rank; "
            "rank reduction alone is not evidence of oversmoothing",
        )


def _audit_worker(
    counts_path: str,
    metadata_path: str,
    time_col: str | None,
    reference_counts_path: str | None,
    candidate_embedding_path: str | None,
    contrast: dict | None,
    thresholds: dict,
    max_cells: int,
    top_genes: int,
    n_neighbors: int,
    seed: int,
    data_type: str,
    reference_data_type: str,
    layer: str | None = None,
) -> _AuditReport:
    report: _AuditReport = {
        "status": "fail",
        "diagnostics": [],
        "metrics": {},
        "thresholds": thresholds,
        "provenance": {
            "counts_path": counts_path,
            "metadata_path": metadata_path,
            "reference_counts_path": reference_counts_path,
            "candidate_embedding_path": candidate_embedding_path,
            "algorithm": (
                "chunked expression QC / declared-specimen checks / "
                "bounded PCA and neighborhood diagnostics"
            ),
            "max_cells": max_cells,
            "top_genes": top_genes,
            "seed": seed,
        },
        "limitations": [
            "Thresholds are configurable exploratory diagnostics, not clinical "
            "validation or GxP release criteria.",
            "Specimen IDs identify declared units; metadata alone cannot establish "
            "biological independence or causal identification.",
            "Neighborhood loss can reflect oversmoothing, correction of nuisance "
            "variation, or a different representation; it is not proof of biological "
            "information loss.",
            "Reference variance ratios assume matching measurement units and "
            "preprocessing; global rescaling alone changes variance ratios.",
            "Sampling labels are checked as metadata and are not treated as true "
            "pseudotime. No contrast model is fitted by this audit.",
        ],
    }
    metrics, provenance = report["metrics"], report["provenance"]
    try:
        metadata = _bounded_metadata(metadata_path)
        provenance["metadata_sha256"] = metadata.attrs["source_sha256"]
        with ChunkedExpressionStore(counts_path, layer=layer) as store:
            if set(store.sample_names) != set(metadata["sample_id"]):
                raise _InvalidInput(
                    "sample_alignment",
                    "Expression and metadata sample IDs must match exactly",
                )
            metadata = metadata.set_index("sample_id", drop=False).loc[
                store.sample_names
            ]
            n = len(store.sample_names)
            selected = (
                np.arange(n)
                if n <= max_cells
                else np.sort(
                    np.random.default_rng(seed).choice(n, max_cells, replace=False)
                )
            )
            samples = [store.sample_names[int(index)] for index in selected]
            provenance.update(
                {
                    "geometry_sample_count": len(samples),
                    "geometry_subsampled": n > len(samples),
                    "geometry_sample_ids_sha256": hashlib.sha256(
                        json.dumps(samples).encode()
                    ).hexdigest(),
                }
            )
            profile = _profile(store, selected, data_type)
            valid_geometry_input = _record_profile(report, profile, n, data_type, layer)
            _record_sample_diagnostics(report, metadata, n, time_col, contrast)
            if not valid_geometry_input:
                return _finish(report)

            genes = _selected_genes(store, profile, top_genes)
            raw = _read_genes(store, genes, selected)
            library_sizes = profile["geometry_library_sizes"]
            raw_geometry, raw_rank = _geometry(
                raw, library_sizes, thresholds["rank_relative_tolerance"]
            )
            provenance["geometry_transform"] = (
                "log1p_cp10k" if library_sizes is not None else "continuous"
            )
            provenance["geometry_selected_genes"] = genes
            provenance["selected_expression_sha256"] = _matrix_sha256(
                raw, genes, samples
            )
            metrics["raw_representation_rank"] = raw_rank
            if not raw_rank:
                _add(
                    report,
                    "expression_geometry_collapse",
                    "error",
                    "Selected expression has no varying geometry; ordering or "
                    "neighborhood preservation cannot be evaluated",
                )

            if reference_counts_path:
                _compare_reference(
                    report,
                    store,
                    reference_counts_path,
                    reference_data_type,
                    selected,
                    samples,
                    library_sizes,
                    top_genes,
                    n_neighbors,
                )
            if candidate_embedding_path:
                _compare_embedding(
                    report,
                    candidate_embedding_path,
                    store.sample_names,
                    samples,
                    raw_geometry,
                    raw_rank,
                    n_neighbors,
                )
    except _InvalidInput as exc:
        _add(report, exc.code, "error", str(exc))
    except (ValueError, OSError, KeyError, np.linalg.LinAlgError) as exc:
        _add(report, "invalid_input", "error", str(exc)[:300])
    return _finish(report)


def _audit_thresholds(thresholds: dict[str, float] | None) -> dict[str, float]:
    selected_thresholds = dict(_DEFAULT_THRESHOLDS)
    if thresholds is not None:
        if not isinstance(thresholds, dict):
            raise ValueError("thresholds must be a dictionary")
        if set(thresholds) - selected_thresholds.keys():
            unknown = sorted(set(thresholds) - selected_thresholds.keys())
            raise ValueError("Unknown diagnostic threshold: " + ", ".join(unknown))
        for key, threshold in thresholds.items():
            try:
                valid = (
                    not isinstance(threshold, bool)
                    and isinstance(threshold, Real)
                    and math.isfinite(threshold)
                    and 0 <= threshold <= 1
                )
            except (ValueError, OverflowError):
                valid = False
            if not valid or (key == "rank_relative_tolerance" and threshold == 0):
                raise ValueError(
                    "Diagnostic thresholds must be finite in [0,1], with positive rank "
                    "tolerance"
                )
            selected_thresholds[key] = float(threshold)
    return selected_thresholds


def audit_expression_quality(
    counts_path: str,
    metadata_path: str,
    time_col: str | None = "embryonic_age",
    reference_counts_path: str | None = None,
    candidate_embedding_path: str | None = None,
    contrast: dict | None = None,
    thresholds: dict[str, float] | None = None,
    max_cells: int = 500,
    top_genes: int = 200,
    n_neighbors: int = 10,
    seed: int = 0,
    data_type: Literal["counts", "continuous", "auto"] = "auto",
    reference_data_type: Literal["counts", "continuous", "auto"] = "auto",
    *,
    layer: str | None = None,
) -> dict:
    """Audit observed QC, declared replication, contrasts and representation loss.

    With reference_counts_path, counts_path is the candidate matrix and the
    reference is the original, in comparable units with the same IDs. Embedding
    CSVs require sample_id and numeric coordinates. Geometry is capped at 500
    cells/200 genes; full matrix quality checks stream bounded chunks. Invalid
    data returns status=fail with explicit diagnostics; no contrast is fitted.
    Count geometry uses counts per 10,000 across all genes before log1p.
    Declare reviewed normalized inputs as continuous even if values are integral.
    Auto infers scale from numeric values and does not establish provenance.
    Nonzero counts include measured finite nonzero entries by sample ID.
    Pass time_col=None for non-temporal samples. A named layer selects AnnData
    layers/<name> from the candidate; the default selects X.
    """
    for value, name, low, high in (
        (max_cells, "max_cells", 4, 500),
        (top_genes, "top_genes", 2, 200),
        (n_neighbors, "n_neighbors", 1, 100),
        (seed, "seed", 0, 2**32 - 1),
    ):
        _bounded_int(value, name, low, high)
    selected_thresholds = _audit_thresholds(thresholds)
    if time_col is not None and (not isinstance(time_col, str) or not time_col):
        raise ValueError("time_col must be a nonempty column name or None")
    for name, declared_type in (
        ("data_type", data_type),
        ("reference_data_type", reference_data_type),
    ):
        if not isinstance(declared_type, str) or declared_type not in {
            "counts",
            "continuous",
            "auto",
        }:
            raise ValueError(f"{name} must be counts, continuous or auto")
    resolved = [
        str(Path(path).expanduser().resolve()) if path is not None else None
        for path in (
            counts_path,
            metadata_path,
            reference_counts_path,
            candidate_embedding_path,
        )
    ]
    return run_sandboxed(
        _audit_worker,
        (
            resolved[0],
            resolved[1],
            time_col,
            resolved[2],
            resolved[3],
            contrast,
            selected_thresholds,
            max_cells,
            top_genes,
            n_neighbors,
            seed,
            data_type,
            reference_data_type,
            layer,
        ),
        max_mem_mb=2000,
        timeout_sec=120,
    )
