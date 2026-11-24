"""Bounded whole-transcriptome passes followed by a selected-feature matrix.

No old labels, cluster assignments or confidence scores enter preprocessing.
Full-library totals are retained when normalizing the selected-feature matrix.
"""

from __future__ import annotations

import json
from pathlib import Path

import anndata as ad
import numpy as np
import pandas as pd
from scipy import sparse

from devbench.core.expression_io import open_expression

from .checkpoints import CheckpointStore, atomic_json
from .scrna_qc import MadDefinition, declared_groups, pin_configuration
from .singlecell import (
    calculate_cell_qc,
    filter_qc_metrics,
    gene_symbols,
    qc_feature_masks,
    validate_counts,
)


def read_table(path, *, label_columns: tuple[str, ...] = ()):
    # Do not coerce cell IDs such as '001' or labels such as 'NA'.
    label_dtypes: dict[int | str, type] = {0: str, **dict.fromkeys(label_columns, str)}
    result = pd.read_csv(
        path,
        dtype=label_dtypes,
        float_precision="round_trip",
        keep_default_na=False,
        na_values=[""],
    )
    # pandas 2.3 re-infers numeric index values even with dtype/converters if
    # index_col is supplied to read_csv; set the index only after parsing.
    index = result.pop(result.columns[0])
    result.index = pd.Index(index.astype(str).to_numpy(), name=None)
    return result


def stream_qc(
    source_path: str | Path,
    store: CheckpointStore,
    *,
    layer: str | None,
    batch_key: str,
    block_rows=128,
    max_block_bytes=64 * 1024**2,
    min_genes=200,
    mad_limit=5.0,
    max_mito_percent=None,
    symbol_column=None,
    qc_group_key: str | None = None,
    retention_group_key: str | None = None,
    mad_definition: MadDefinition = "absolute_mad",
) -> Path:
    governed = (qc_group_key, retention_group_key, mad_definition) != (
        None,
        None,
        "absolute_mad",
    )
    with open_expression(Path(source_path), layer=layer, batch_key=batch_key) as source:
        if batch_key not in source.obs or source.obs[batch_key].isna().any():
            raise ValueError("observed_batch_identities_required")
        symbols, symbol_source = gene_symbols(source, symbol_column)
        masks = qc_feature_masks(symbols)
        labels, dependencies = _stream_qc_roles(
            source,
            store,
            governed,
            batch_key,
            qc_group_key,
            retention_group_key,
            {
                "counts_layer": layer,
                "block_rows": block_rows,
                "max_block_bytes": max_block_bytes,
                "min_genes": min_genes,
                "mad_limit": mad_limit,
                "max_mito_percent": max_mito_percent,
                "symbol_column": symbol_column,
                "mad_definition": mad_definition,
            },
        )
        chunks = []
        for start in range(0, source.shape[0], block_rows):
            stop = min(start + block_rows, source.shape[0])
            name = f"qc_{start:08d}"

            def operation(folder, *, start=start, stop=stop):
                data = source.subset(range(start, stop), max_bytes=max_block_bytes)
                validate_counts(data.X)
                data.obs = data.obs[list(labels)].copy()
                calculate_cell_qc(
                    data,
                    masks,
                    percent_top=[20] if data.n_vars >= 20 else None,
                )
                if data.n_vars < 20:
                    data.obs["pct_counts_in_top_20_genes"] = np.nan
                data.obs.to_csv(folder / "metrics.csv")
                return {"start": start, "stop": stop, "features": source.shape[1]}

            store.run(name, operation, dependencies=dependencies)
            chunks.append(name)

        def finish(folder):
            metrics = pd.concat(
                [
                    read_table(
                        store.path(name) / "metrics.csv",
                        label_columns=labels,
                    )
                    for name in chunks
                ]
            )
            if not metrics.index.equals(source.obs.index):
                raise ValueError("streamed_QC_row_identity_mismatch")
            keep, report = filter_qc_metrics(
                metrics,
                batch_key=batch_key,
                min_genes=min_genes,
                mad_limit=mad_limit,
                max_mito_percent=max_mito_percent,
                mitochondrial_features=int(masks["mt"].sum()),
                symbol_source=symbol_source,
                qc_group_key=qc_group_key,
                retention_group_key=retention_group_key,
                mad_definition=mad_definition,
            )
            report["input_info"] = getattr(
                source,
                "input_info",
                {"format": "anndata_H5AD"},
            )
            metrics["qc_pass"] = keep
            metrics.to_csv(folder / "metrics.csv")
            atomic_json(folder / "report.json", report)
            # External predictions are a separate assessment artifact, never fit input.
            source.obs.to_csv(folder / "external-reference.csv")
            from .qc_concordance import compare_qc

            atomic_json(folder / "concordance.json", compare_qc(source.obs, metrics))
            var = source.var.copy()
            var["gene_symbols"] = symbols.to_numpy()
            var.to_csv(folder / "features.csv")
            return {"cells": len(metrics), "retained": int(keep.sum())}

        return store.run("qc", finish, dependencies=chunks)


def _stream_qc_roles(
    source,
    store,
    governed,
    batch_key,
    qc_group_key,
    retention_group_key,
    settings,
):
    if not governed:
        return (batch_key,), ()
    if getattr(source, "input_info", {}).get("batch_label_basis") is not None:
        raise ValueError("Governed QC requires verified batch or capture annotations")
    labels = tuple(
        dict.fromkeys(
            key
            for key in (batch_key, qc_group_key, retention_group_key)
            if key is not None
        )
    )
    for key in labels:
        declared_groups(source.obs, key, f"Invalid QC grouping column: {key}")
        if key in {"total_counts", "n_genes_by_counts", "qc_pass"} or key.startswith(
            ("log1p_", "pct_counts_", "total_counts_")
        ):
            raise ValueError(f"QC grouping column conflicts with a metric: {key}")
    configuration = {
        **settings,
        "batch_key": batch_key,
        "qc_group_key": qc_group_key or batch_key,
        "retention_group_key": retention_group_key,
    }
    return labels, (pin_configuration(store, "qc_configuration", configuration),)


def _combine_moments(count, mean, m2, values):
    """Chan parallel moments; avoid subtracting large sum-of-squares totals."""
    n = values.shape[0]
    if not n:
        return count, mean, m2
    block_mean = np.asarray(values.mean(axis=0)).ravel()
    # Nonnegative normalized counts; clip small negative roundoff only.
    block_m2 = (
        np.asarray(values.multiply(values).sum(axis=0)).ravel() - n * block_mean**2
    )
    tolerance = 1e-10 * np.maximum(1, n * block_mean**2)
    if np.any(block_m2 < -tolerance):
        raise ValueError("unstable_gene_variance")
    block_m2 = np.maximum(block_m2, 0)
    delta = block_mean - mean
    total = count + n
    return (
        total,
        mean + delta * (n / total),
        m2 + block_m2 + delta**2 * (count * n / total),
    )


def hvg_from_moments(counts, means, m2, feature_names, n_top_genes):
    """Scanpy Seurat bin/rank policy over streamed, batch-specific moments.

    The two bin helpers come from the uv-locked Scanpy implementation. Regression
    tests compare the entire table/selection to its public batched HVG method.
    Upgrading Scanpy requires rerunning that parity check.
    """
    from scanpy.preprocessing._highly_variable_genes import (
        _get_disp_stats,
        _get_mean_bins,
    )

    tables = []
    for number, mean, squared in zip(counts, means, m2, strict=True):
        if number < 2:
            raise ValueError("HVG_requires_two_QC_passing_cells_per_observed_batch")
        expressed = mean > 0
        if not expressed.any():
            raise ValueError("batch_has_no_expressed_genes")
        variance = squared[expressed] / (number - 1)
        dispersion = variance / mean[expressed]
        dispersion[dispersion == 0] = np.nan
        frame = pd.DataFrame(
            {"means": np.log1p(mean[expressed]), "dispersions": np.log(dispersion)}
        )
        frame["mean_bin"] = _get_mean_bins(frame["means"], "seurat", 20)
        stats = _get_disp_stats(frame, "seurat")
        frame["dispersions_norm"] = (frame["dispersions"] - stats["avg"]) / stats["dev"]
        finite = frame["dispersions_norm"].dropna().sort_values(ascending=False)
        if not len(finite):
            raise ValueError("no_estimable_normalized_gene_dispersions")
        cutoff = finite.iloc[min(n_top_genes, len(finite)) - 1]
        frame["highly_variable"] = frame["dispersions_norm"].fillna(-np.inf) >= cutoff
        frame.index = feature_names[expressed]
        frame = frame.drop(columns="mean_bin").reindex(feature_names, fill_value=0)
        frame["highly_variable"] = frame["highly_variable"].astype(int)
        tables.append(frame)
    result = (
        pd.concat(tables)
        .groupby(level=0, observed=True)
        .agg(
            {
                "means": "mean",
                "dispersions": "mean",
                "dispersions_norm": "mean",
                "highly_variable": "sum",
            }
        )
    )
    result["highly_variable_nbatches"] = result["highly_variable"]
    result["highly_variable_intersection"] = result["highly_variable_nbatches"] == len(
        counts
    )
    result.sort_values(
        ["highly_variable_nbatches", "dispersions_norm"],
        ascending=False,
        na_position="last",
        inplace=True,
    )
    result["highly_variable"] = np.arange(len(result)) < n_top_genes
    return result.loc[feature_names]


def _scran_factors(store, metrics):
    if store.verify("scran_factors", ("qc",)) is None:
        raise ValueError("missing_dependency:scran_factors")
    retained = metrics.index[metrics["qc_pass"].to_numpy(dtype=bool)]
    table = read_table(store.path("scran_factors") / "factors.csv")
    if not table.index.equals(retained) or list(table.columns) != ["scran_size_factor"]:
        raise ValueError("scran_size_factor_identity_mismatch")
    values = table["scran_size_factor"].to_numpy(dtype=np.float64)
    if not np.isfinite(values).all() or np.any(values <= 0):
        raise ValueError("invalid_scran_size_factors")
    factors = np.ones(len(metrics), dtype=np.float64)
    factors[metrics["qc_pass"].to_numpy(dtype=bool)] = values
    return factors


def stream_hvg(
    source_path,
    store,
    *,
    layer,
    batch_key,
    n_top_genes=2000,
    block_rows=128,
    max_block_bytes=64 * 1024**2,
    checkpoint_rows=1024,
    use_scran_factors=False,
):
    metrics = read_table(store.path("qc") / "metrics.csv", label_columns=(batch_key,))
    keep = metrics["qc_pass"].to_numpy(dtype=bool)
    levels = sorted(metrics[batch_key].astype(str).unique())
    groups = metrics[batch_key].astype(str).to_numpy()
    factors = _scran_factors(store, metrics) if use_scran_factors else None
    normalization = (
        "full_transcriptome_scran_size_factors"
        if use_scran_factors
        else "full_library_total_10000"
    )
    with open_expression(Path(source_path), layer=layer, batch_key=batch_key) as source:
        if not metrics.index.equals(source.obs.index):
            raise ValueError("HVG_input_cell_identity_mismatch")
        previous = "qc"
        for start in range(0, source.shape[0], checkpoint_rows):
            stop = min(start + checkpoint_rows, source.shape[0])
            name = f"moments_{start:08d}"

            def operation(folder, *, previous=previous, start=start, stop=stop):
                if previous == "qc":
                    count = np.zeros(len(levels), dtype=np.int64)
                    means = np.zeros((len(levels), source.shape[1]))
                    m2 = np.zeros_like(means)
                else:
                    with np.load(
                        store.path(previous) / "moments.npz", allow_pickle=False
                    ) as state:
                        count, means, m2 = (
                            state[k].copy() for k in ("count", "means", "m2")
                        )
                for block_start in range(start, stop, block_rows):
                    block_stop = min(block_start + block_rows, stop)
                    rows = np.arange(block_start, block_stop)[
                        keep[block_start:block_stop]
                    ]
                    if len(rows):
                        block = source.subset(rows, max_bytes=max_block_bytes)
                        validate_counts(block.X)
                        values = sparse.csr_matrix(block.X, dtype=np.float64)
                        totals = metrics["total_counts"].to_numpy()[rows]
                        scale = 1e4 / totals if factors is None else 1 / factors[rows]
                        values = values.multiply(scale[:, None]).tocsr()
                        for i, group in enumerate(levels):
                            mask = groups[rows] == group
                            count[i], means[i], m2[i] = _combine_moments(
                                count[i], means[i], m2[i], values[mask]
                            )
                np.savez_compressed(
                    folder / "moments.npz", count=count, means=means, m2=m2
                )
                return {"processed_through_row": stop, "batches": levels}

            dependencies = (previous,)
            if previous == "qc" and use_scran_factors:
                dependencies += ("scran_factors",)
            store.run(name, operation, dependencies=dependencies)
            previous = name

        def finish(folder):
            with np.load(
                store.path(previous) / "moments.npz", allow_pickle=False
            ) as state:
                table = hvg_from_moments(
                    state["count"],
                    state["means"],
                    state["m2"],
                    source.var.index,
                    min(n_top_genes, source.shape[1]),
                )
            table.to_csv(folder / "features.csv")
            return {
                "method": "seurat_batch_aware_streamed_moments",
                "HVG_count": int(table.highly_variable.sum()),
                "normalization": normalization,
                "batch_key": batch_key,
            }

        return store.run("hvg", finish, dependencies=(previous,))


def _assemble_selected_chunks(store, chunks):
    """Allocate the final CSR once, keeping at most one input chunk in memory."""
    sizes = []
    for name in chunks:
        block = sparse.load_npz(store.path(name) / "counts.npz")
        sizes.append((block.shape[0], block.shape[1], block.nnz))
        del block
    cells = sum(size[0] for size in sizes)
    features = sizes[0][1]
    nonzeros = sum(size[2] for size in sizes)
    index_dtype = np.int32 if max(cells, features, nonzeros) < 2**31 else np.int64
    values = np.empty(nonzeros, dtype=np.float32)
    indices = np.empty(nonzeros, dtype=index_dtype)
    indptr = np.empty(cells + 1, dtype=index_dtype)
    indptr[0] = 0
    rows = np.empty(cells, dtype=np.int64)
    row_offset = value_offset = 0
    for name, (count, width, entries) in zip(chunks, sizes, strict=False):
        block = sparse.load_npz(store.path(name) / "counts.npz")
        selected_rows = np.load(store.path(name) / "rows.npy", allow_pickle=False)
        if (
            width != features
            or block.shape != (count, width)
            or block.nnz != entries
            or len(selected_rows) != count
        ):
            raise ValueError("selected_chunk_shape_or_row_identity_mismatch")
        values[value_offset : value_offset + entries] = block.data
        indices[value_offset : value_offset + entries] = block.indices
        indptr[row_offset + 1 : row_offset + count + 1] = (
            block.indptr[1:] + value_offset
        )
        rows[row_offset : row_offset + count] = selected_rows
        row_offset += count
        value_offset += entries
        del block, selected_rows
    return sparse.csr_matrix(
        (values, indices, indptr), shape=(cells, features), copy=False
    ), rows


def _normalize_selected_counts(counts, totals, block_rows, *, size_factors=None):
    """Preserve float64 scaling then float32 log1p, without whole-matrix temporaries."""
    values = np.empty(counts.nnz, dtype=np.float32)
    indices = np.empty(counts.nnz, dtype=counts.indices.dtype)
    indptr = np.empty(len(totals) + 1, dtype=counts.indptr.dtype)
    indptr[0] = 0
    scale = 1e4 / totals if size_factors is None else 1 / size_factors
    offset = 0
    for start in range(0, len(totals), block_rows):
        stop = min(start + block_rows, len(totals))
        if len(totals) == 1:
            block = counts.multiply(scale[:, None]).tocsr().astype(np.float32)
        else:
            # SciPy treats a one-row factor as a scalar and can keep float32.
            # Preserve the original multi-row calculation's float64 products,
            # including summing duplicate coordinates before the float32 cast.
            scaled = counts[start:stop].tocoo()
            scaled.data = scaled.data * scale[start:stop][scaled.row]
            block = scaled.tocsr().astype(np.float32)
            del scaled
        end = offset + block.nnz
        values[offset:end] = block.data
        indices[offset:end] = block.indices
        indptr[start + 1 : stop + 1] = block.indptr[1:] + offset
        offset = end
        del block
    result = sparse.csr_matrix(
        (values[:offset], indices[:offset], indptr), shape=counts.shape, copy=False
    )
    np.log1p(result.data, out=result.data)
    return result


def stream_selected(
    source_path,
    store,
    *,
    layer,
    batch_key=None,
    block_rows=128,
    max_block_bytes=64 * 1024**2,
    max_selected_bytes=512 * 1024**2,
    use_scran_factors=False,
):
    metrics = read_table(
        store.path("qc") / "metrics.csv",
        label_columns=(batch_key,) if batch_key is not None else (),
    )
    factors = _scran_factors(store, metrics) if use_scran_factors else None
    hvg = read_table(store.path("hvg") / "features.csv")
    features = np.flatnonzero(hvg["highly_variable"].to_numpy(dtype=bool))
    chunks = []
    with open_expression(Path(source_path), layer=layer) as source:
        for start in range(0, source.shape[0], block_rows):
            stop = min(start + block_rows, source.shape[0])
            rows = np.arange(start, stop)[
                metrics["qc_pass"].to_numpy(dtype=bool)[start:stop]
            ]
            if not len(rows):
                continue
            name = f"selected_{start:08d}"

            def operation(folder, rows=rows):
                block = source.subset(rows, features, max_bytes=max_block_bytes)
                validate_counts(block.X)
                values = sparse.csr_matrix(block.X)
                if np.max(values.data, initial=0) > 2**24:
                    raise ValueError(
                        "selected_counts_exceed_exact_float32_integer_range"
                    )
                sparse.save_npz(folder / "counts.npz", values.astype(np.float32))
                np.save(folder / "rows.npy", rows, allow_pickle=False)
                return {
                    "cells": len(rows),
                    "features": len(features),
                    "sparse_bytes": values.nnz * 8 + (len(rows) + 1) * 4,
                }

            store.run(name, operation, dependencies=("qc", "hvg"))
            chunks.append(name)

        def finish(folder):
            needed = sum(
                json.loads((store.path(n) / "receipt.json").read_text())["metadata"][
                    "sparse_bytes"
                ]
                for n in chunks
            )
            if needed * 3 > max_selected_bytes:
                raise MemoryError("selected_feature_assembly_exceeds_budget")
            values, rows = _assemble_selected_chunks(store, chunks)
            obs = metrics.iloc[rows].copy()
            var = read_table(store.path("qc") / "features.csv").iloc[features].copy()
            for col in hvg:
                var[col] = hvg.iloc[features][col]
            selected_factors = None if factors is None else factors[rows]
            normalized = _normalize_selected_counts(
                values,
                obs["total_counts"].to_numpy(),
                block_rows,
                size_factors=selected_factors,
            )
            normalization = "full_transcriptome_library_total_10000_then_log1p"
            if selected_factors is not None:
                obs["scran_size_factor"] = selected_factors
                normalization = "full_transcriptome_scran_size_factors_then_log1p"
            result = ad.AnnData(normalized, obs=obs, var=var)
            result.layers["counts"] = values
            result.uns["normalization"] = normalization
            result.uns["source_matrix"] = source.matrix_source
            result.uns["original_feature_count"] = source.shape[1]
            result.write_h5ad(folder / "selected.h5ad")
            return {
                "cells": result.n_obs,
                "features": result.n_vars,
                "counts_scope": (
                    "selected_features_exact_counts_full_input_retained_externally"
                ),
            }

        return store.run("selected", finish, dependencies=chunks)
