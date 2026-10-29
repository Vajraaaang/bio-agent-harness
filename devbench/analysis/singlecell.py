"""Explicit single-cell methods with preserved counts and evidence limitations."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import sparse


def validate_counts(matrix):
    values = matrix.data if sparse.issparse(matrix) else np.asarray(matrix)
    if (values.dtype.kind not in "iuf" or not np.isfinite(values).all()
            or np.any(values < 0) or np.any(values != np.floor(values))
            or np.any(values > 2**53-1)):
        raise ValueError("raw_counts_required: finite nonnegative integers; normalized values are not counts")


def gene_symbols(data, column=None):
    if column is not None:
        if column not in data.var:
            raise ValueError("Requested gene-symbol column is unavailable")
        source = column
    else:
        source = next((name for name in ("gene_symbols", "gene_symbol", "feature_name", "gene_name")
                       if name in data.var), "var_names")
    values = data.var.index if source == "var_names" else data.var[source]
    symbols = pd.Index(values).fillna("").astype(str)
    return symbols, source


def quality_control(data, *, symbol_column=None, batch_key=None, min_genes=200,
                    mad_limit=5.0, max_mito_percent=None):
    import scanpy as sc

    validate_counts(data.X)
    if type(min_genes) is not int or min_genes < 1:
        raise ValueError("min_genes must be a positive integer")
    if mad_limit is not None and (not np.isfinite(mad_limit) or mad_limit <= 0):
        raise ValueError("mad_limit must be positive or None")
    if max_mito_percent is not None and not 0 <= max_mito_percent <= 100:
        raise ValueError("max_mito_percent must lie between zero and 100")
    if batch_key is not None and (batch_key not in data.obs or data.obs[batch_key].isna().any()):
        raise ValueError("QC batch identities are unavailable or missing")
    symbols, source = gene_symbols(data, symbol_column)
    mito = np.asarray(symbols.str.upper().str.startswith("MT-"), dtype=bool)
    if not mito.any():
        raise ValueError("mitochondrial_annotation_unavailable: zero symbol matches; provide a gene-symbol mapping")
    result = data.copy()
    result.var["mt"] = mito
    result.var["gene_symbols"] = symbols.to_numpy()
    sc.pp.calculate_qc_metrics(result, qc_vars=["mt"], percent_top=None, log1p=True, inplace=True)
    keep, report = filter_qc_metrics(result.obs, batch_key=batch_key, min_genes=min_genes,
        mad_limit=mad_limit, max_mito_percent=max_mito_percent,
        mitochondrial_features=int(mito.sum()), symbol_source=source)
    result.obs["qc_pass"] = keep
    return result[keep].copy(), report


def filter_qc_metrics(metrics, *, batch_key=None, min_genes=200, mad_limit=5.0,
                      max_mito_percent=None, mitochondrial_features=0, symbol_source=None):
    """Apply the same QC rule to complete per-cell metrics from bounded reads."""
    if type(min_genes) is not int or min_genes < 1:
        raise ValueError("min_genes must be a positive integer")
    if mad_limit is not None and (not np.isfinite(mad_limit) or mad_limit <= 0):
        raise ValueError("mad_limit must be positive or None")
    if max_mito_percent is not None and not 0 <= max_mito_percent <= 100:
        raise ValueError("max_mito_percent must lie between zero and 100")
    if batch_key is not None and (batch_key not in metrics or metrics[batch_key].isna().any()):
        raise ValueError("QC batch identities are unavailable or missing")
    reject = ((metrics["total_counts"].to_numpy() <= 0)
              | (metrics["n_genes_by_counts"].to_numpy() < min_genes))
    warnings, thresholds = [], []
    groups = metrics[batch_key].astype(str).to_numpy() if batch_key else np.repeat("all", len(metrics))
    if mad_limit is not None:
        for group in sorted(set(groups)):
            indices = np.flatnonzero(groups == group)
            for metric in ("log1p_total_counts", "log1p_n_genes_by_counts", "pct_counts_mt"):
                values = metrics[metric].to_numpy()[indices]
                median = float(np.median(values))
                mad = float(np.median(np.abs(values-median)))
                if not np.isfinite(median) or not np.isfinite(mad):
                    reject[indices] = True
                    warnings.append(f"nonfinite_qc_metrics:{group}:{metric}")
                    continue
                if mad == 0:
                    warnings.append(f"zero_MAD_no_adaptive_cutoff:{group}:{metric}")
                    continue
                low, high = median-mad_limit*mad, median+mad_limit*mad
                rejected = values > high
                if metric != "pct_counts_mt":
                    rejected |= values < low
                reject[indices[rejected]] = True
                thresholds.append({"batch": group, "metric": metric, "median": median,
                                   "MAD": mad, "lower": low if metric != "pct_counts_mt" else None,
                                   "upper": high})
    if max_mito_percent is not None:
        reject |= metrics["pct_counts_mt"].to_numpy() > max_mito_percent
    report = {"method": "scanpy_QC_log1p_MAD/1", "cells_input": len(metrics),
              "cells_retained": int((~reject).sum()), "cells_excluded": int(reject.sum()),
              "mitochondrial_features": mitochondrial_features, "gene_symbol_source": symbol_source,
              "min_genes": min_genes, "mad_limit": mad_limit, "max_mito_percent": max_mito_percent,
              "thresholds": thresholds, "warnings": warnings,
              "excluded_cell_ids": metrics.index[reject].tolist()}
    if report["cells_retained"] < 4:
        raise ValueError("insufficient_cells_after_QC: fewer than four cells remain")
    return ~reject, report


def detect_doublets(data, *, batch_key=None, expected_rate=0.05, seed=0, copy=True):
    """Predict doublets; copy=False updates metadata on caller-owned data."""
    import scanpy as sc

    validate_counts(data.X)
    if not 0 < expected_rate < 1:
        raise ValueError("expected_rate must lie strictly between zero and one")
    if type(copy) is not bool:
        raise ValueError("copy must be boolean")
    result = data.copy() if copy else data
    groups = result.obs[batch_key].astype(str) if batch_key else pd.Series("one_library", index=result.obs_names)
    result.obs["doublet_score"] = np.nan
    result.obs["predicted_doublet"] = pd.Series(pd.NA, index=result.obs_names, dtype="boolean")
    reports = []
    for group in sorted(groups.unique()):
        mask = np.asarray(groups == group)
        count = int(mask.sum())
        if count < 100:
            reports.append({"batch": group, "cells": count, "status": "unavailable",
                            "reason": "fewer_than_100_cells_for_configured_doublet_method"})
            continue
        piece = result if count == result.n_obs else result[mask].copy()
        sc.pp.scrublet(piece, expected_doublet_rate=expected_rate, random_state=seed,
                       n_prin_comps=min(30, piece.n_vars-1, count-1), use_approx_neighbors=False,
                       verbose=False)
        scores = piece.obs["doublet_score"].to_numpy()
        flags = piece.obs.get("predicted_doublet")
        if (not np.isfinite(scores).all() or flags is None or flags.isna().any()
                or any(not isinstance(value, (bool, np.bool_)) for value in flags)):
            raise ValueError("doublet_detection_failed: finite scores and a thresholded prediction are required")
        result.obs.loc[piece.obs_names, "doublet_score"] = scores
        result.obs.loc[piece.obs_names, "predicted_doublet"] = piece.obs["predicted_doublet"].astype(bool)
        reports.append({"batch": group, "cells": count, "status": "completed",
                        "predicted_doublets": int(piece.obs["predicted_doublet"].sum())})
    available = result.obs["doublet_score"].notna()
    return result, {"method": "scanpy_scrublet", "expected_rate_assumption": expected_rate,
                    "status": "completed" if available.all() else "partial" if available.any() else "unavailable",
                    "filtering": "flag_only", "batches": reports,
                    "scope": "computational_doublet_prediction_not_experimental_truth"}


def normalize_and_embed(data, *, n_top_genes=2000, n_pcs=30, seed=0, batch_key=None,
                        correct_batch=False, resolution=1.0):
    import scanpy as sc

    validate_counts(data.X)
    if correct_batch and (batch_key is None or batch_key not in data.obs or data.obs[batch_key].isna().any()):
        raise ValueError("batch_correction_requires_observed_batch_identities")
    result = data.copy()
    result.layers["counts"] = result.X.copy()
    sc.pp.normalize_total(result, target_sum=1e4)
    sc.pp.log1p(result)
    sc.pp.highly_variable_genes(result, n_top_genes=min(n_top_genes, result.n_vars), flavor="seurat",
                                batch_key=batch_key)
    selected = int(result.var["highly_variable"].sum())
    components = min(n_pcs, selected-1, result.n_obs-1)
    if components < 2:
        raise ValueError("insufficient_variable_genes_for_embedding")
    sc.pp.pca(result, n_comps=components, random_state=seed, mask_var="highly_variable", svd_solver="arpack")
    correction = {"status": "not_requested", "method": None}
    representation = "X_pca"
    if correct_batch:
        if result.obs[batch_key].nunique() == 1:
            correction = {"status": "not_applicable_single_observed_batch", "method": None}
        else:
            import harmonypy
            fitted = harmonypy.run_harmony(result.obsm["X_pca"], result.obs, batch_key,
                random_state=seed, nclust=max(2, min(100, result.n_obs//30)),
                max_iter_harmony=10, verbose=False, device="cpu")
            corrected = np.asarray(fitted.Z_corr)
            if corrected.shape == result.obsm["X_pca"].T.shape:
                corrected = corrected.T
            if corrected.shape != result.obsm["X_pca"].shape or not np.isfinite(corrected).all():
                raise ValueError("invalid_harmony_embedding")
            result.obsm["X_pca_harmony"] = corrected
            representation = "X_pca_harmony"
            correction = {"status": "completed", "method": "harmonypy", "batch_key": batch_key,
                          "scope": "embedding_only_not_gene_expression_or_causal_batch_adjustment"}
    sc.pp.neighbors(result, n_neighbors=min(15, result.n_obs-1), use_rep=representation, random_state=seed)
    sc.tl.leiden(result, flavor="igraph", directed=False, n_iterations=2,
                 resolution=resolution, random_state=seed)
    sc.tl.umap(result, random_state=seed)
    if not np.isfinite(result.obsm["X_umap"]).all():
        raise ValueError("nonfinite_umap_embedding")
    return result, {"normalization": "total_10000_then_log1p", "counts_preserved_in_layer": "counts",
                    "HVG_method": "seurat", "HVG_count": selected, "PCA_components": components,
                    "batch_correction": correction, "neighbor_representation": representation,
                    "clusters": result.obs["leiden"].nunique(), "seed": seed,
                    "scope": "exploratory_geometry_not_cell_identity_or_causality"}


def marker_annotation(data, panels, *, symbol_column=None, minimum_coverage=0.5, minimum_margin=0.35,
                      library_totals=None):
    counts = data.layers["counts"] if "counts" in data.layers else data.X
    validate_counts(counts)
    symbols, source = gene_symbols(data, symbol_column)
    upper = np.asarray(symbols.str.upper())
    totals = np.asarray(counts.sum(axis=1)).ravel() if library_totals is None else np.asarray(library_totals)
    if (totals.shape != (data.n_obs,) or not np.isfinite(totals).all() or np.any(totals <= 0)
            or np.any(totals < np.asarray(counts.sum(axis=1)).ravel())):
        raise ValueError("marker_annotation_requires_positive_library_totals")
    normalized = sparse.csr_matrix(counts, dtype=float).multiply((10000/totals)[:, None]).tocsr()
    normalized.data = np.log1p(normalized.data)
    names, scores, coverage = [], [], {}
    for name, markers in panels.items():
        requested = {str(x).upper() for x in markers}
        matched = requested & set(upper)
        fraction = len(matched)/len(requested) if requested else 0.0
        coverage[name] = {"matched_symbols": sorted(matched), "requested": len(requested), "fraction": fraction}
        if len(matched) < 2 or fraction < minimum_coverage:
            continue
        columns = np.flatnonzero(np.isin(upper, list(matched)))
        names.append(name)
        scores.append(np.asarray(normalized[:, columns].mean(axis=1)).ravel())
    labels = pd.Series("uncertain", index=data.obs_names, name="marker_cell_type")
    evidence = {"method": "mean_log1p_positive_marker_expression/1", "coverage": coverage,
                "reference_identity": "sha256:" + hashlib.sha256(json.dumps(panels, sort_keys=True,
                    separators=(",", ":")).encode()).hexdigest(),
                "reference_version": "content_addressed_marker_panel/1",
                "scores_are_calibrated_probabilities": False,
                "symbol_source": source, "minimum_margin": minimum_margin,
                "scope": "candidate_label_not_experimental_identity", "status": "insufficient_marker_coverage"}
    if not names:
        return labels, evidence
    matrix = np.column_stack(scores)
    order = np.argsort(matrix, axis=1)
    best = matrix[np.arange(len(labels)), order[:, -1]]
    second = matrix[np.arange(len(labels)), order[:, -2]] if len(names)>1 else np.zeros(len(labels))
    supported = (best >= 1.0) & (best > second) & (best-second >= minimum_margin)
    labels.loc[supported] = np.asarray(names)[order[supported, -1]]
    evidence.update(status="completed", uncertain_cells=int((~supported).sum()),
                    cell_score_preview=_score_preview(data.obs_names, names, matrix, labels), preview_limit=50)
    return labels, evidence


def _score_preview(cell_ids, names, values, labels):
    """At most fifty observations and two competing scores per observation."""
    result = []
    for index, cell in enumerate(cell_ids[:50]):
        order = np.argsort(-values[index], kind="stable")[:2]
        best = float(values[index, order[0]])
        second = float(values[index, order[1]]) if len(order) > 1 else 0.0
        result.append({"cell_id": str(cell), "label": str(labels.iloc[index]),
            "competing_scores": {str(names[number]): float(values[index, number]) for number in order},
            "top_score": best, "runner_up_score": second, "margin": best-second})
    return result


def symbol_count_view(data, symbol_column=None):
    """Aggregate duplicate symbols on the count scale before re-normalizing."""
    import anndata as ad
    import scanpy as sc

    counts = data.layers["counts"] if "counts" in data.layers else data.X
    validate_counts(counts)
    symbols, _ = gene_symbols(data, symbol_column)
    keep = np.asarray(symbols != "")
    if not keep.any():
        raise ValueError("gene_symbols_unavailable")
    codes, unique = pd.factorize(symbols[keep], sort=True)
    projection = sparse.csr_matrix((np.ones(len(codes)), (np.arange(len(codes)), codes)),
                                   shape=(len(codes), len(unique)))
    values = sparse.csr_matrix(counts)[:, keep] @ projection
    result = ad.AnnData(values, obs=data.obs.copy(), var=pd.DataFrame(index=unique))
    sc.pp.normalize_total(result, target_sum=1e4)
    sc.pp.log1p(result)
    return result


def annotate_celltypist(data, model_path, *, expected_sha256, minimum_score=0.5, minimum_margin=0.15,
                        symbol_column=None):
    from numbers import Real

    if any(not isinstance(value, Real) or isinstance(value, (bool, np.bool_))
           or not np.isfinite(value) or not 0 <= value <= 1
           for value in (minimum_score, minimum_margin)):
        raise ValueError("invalid_celltypist_thresholds: require finite numbers between 0 and 1")
    minimum_score, minimum_margin = float(minimum_score), float(minimum_margin)
    path = Path(model_path)
    with path.open("rb") as handle:
        digest = hashlib.file_digest(handle, "sha256").hexdigest()
    if digest != expected_sha256:
        raise ValueError("model_checksum_mismatch: do not deserialize an unverified model")
    import celltypist
    prediction = celltypist.annotate(symbol_count_view(data, symbol_column), model=str(path),
                                     mode="best match", majority_voting=False, use_GPU=False)
    probabilities = prediction.probability_matrix
    predicted = prediction.predicted_labels
    if (not isinstance(probabilities, pd.DataFrame) or not isinstance(predicted, pd.DataFrame)
            or not probabilities.index.is_unique or not predicted.index.is_unique
            or not probabilities.index.equals(data.obs_names) or not predicted.index.equals(data.obs_names)):
        raise ValueError("celltypist_cell_identity_mismatch")
    classes = probabilities.columns
    if (not len(classes) or not classes.is_unique
            or any(not isinstance(label, str) or not label.strip() for label in classes)):
        raise ValueError("invalid_celltypist_score_classes")
    try:
        values = probabilities.to_numpy(dtype=float)
    except (TypeError, ValueError) as error:
        raise ValueError("invalid_celltypist_scores") from error
    if not np.isfinite(values).all() or np.any((values < 0) | (values > 1)):
        raise ValueError("invalid_celltypist_scores")
    if (not predicted.columns.is_unique or "predicted_labels" not in predicted
            or predicted["predicted_labels"].isna().any()
            or not predicted["predicted_labels"].isin(classes).all()):
        raise ValueError("invalid_celltypist_labels")
    ordered = np.sort(values, axis=1)
    best, second = ordered[:, -1], ordered[:, -2] if values.shape[1]>1 else np.zeros(data.n_obs)
    labels = predicted["predicted_labels"].astype(str).copy()
    if np.any(values[np.arange(data.n_obs), classes.get_indexer(labels)] != best):
        raise ValueError("invalid_celltypist_labels: label does not match a maximum score")
    # CellTypist uses per-class sigmoid scores, not a row-normalized distribution.
    uncertain = (best < minimum_score) | (best <= second) | (best-second < minimum_margin)
    labels.iloc[np.flatnonzero(uncertain)] = "uncertain"
    return labels, pd.Series(best, index=data.obs_names, name="cell_type_score"), {
        "method": "celltypist", "status": "completed", "model_sha256": digest,
        "reference_identity": "sha256:" + digest, "reference_version": "content_addressed_celltypist_model/1",
        "scores_are_calibrated_probabilities": False,
        "cell_score_preview": _score_preview(data.obs_names, classes, values, labels), "preview_limit": 50,
        "uncertain_cells": int(uncertain.sum()),
        "minimum_score": minimum_score, "minimum_margin": minimum_margin,
        "scope": "classifier_scores_not_calibrated_probabilities_or_experimental_identity"}
