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

from .checkpoints import atomic_json
from .io import open_expression
from .singlecell import filter_qc_metrics, gene_symbols, validate_counts


def read_table(path):
    # Do not coerce cell IDs such as '001' or labels such as 'NA'.
    result = pd.read_csv(path, dtype={0: str}, float_precision="round_trip",
                         keep_default_na=False, na_values=[""])
    # pandas 2.3 re-infers numeric index values even with dtype/converters if
    # index_col is supplied to read_csv; set the index only after parsing.
    index = result.pop(result.columns[0])
    result.index = pd.Index(index.astype(str).to_numpy(), name=None)
    return result


def stream_qc(source_path, store, *, layer, batch_key, block_rows=128,
              max_block_bytes=64*1024**2, min_genes=200, mad_limit=5.0,
              max_mito_percent=None, symbol_column=None):
    import scanpy as sc

    with open_expression(Path(source_path), layer=layer, batch_key=batch_key) as source:
        if batch_key not in source.obs or source.obs[batch_key].isna().any():
            raise ValueError("observed_batch_identities_required")
        symbols, symbol_source = gene_symbols(source, symbol_column)
        mito = np.asarray(symbols.str.upper().str.startswith("MT-"))
        if not mito.any():
            raise ValueError("mitochondrial_annotation_unavailable: zero symbol matches")
        chunks = []
        for start in range(0, source.shape[0], block_rows):
            stop = min(start+block_rows, source.shape[0])
            name = f"qc_{start:08d}"
            def operation(folder):
                data = source.subset(range(start, stop), max_bytes=max_block_bytes)
                validate_counts(data.X)
                data.obs = data.obs[[batch_key]].copy()
                data.var["mt"] = mito
                sc.pp.calculate_qc_metrics(data, qc_vars=["mt"], percent_top=[20] if data.n_vars>=20 else None,
                                           log1p=True, inplace=True)
                if data.n_vars<20:
                    data.obs["pct_counts_in_top_20_genes"]=np.nan
                data.obs.to_csv(folder / "metrics.csv")
                return {"start": start, "stop": stop, "features": source.shape[1]}
            store.run(name, operation)
            chunks.append(name)
        def finish(folder):
            metrics = pd.concat([read_table(store.path(name)/"metrics.csv") for name in chunks])
            if not metrics.index.equals(source.obs.index):
                raise ValueError("streamed_QC_row_identity_mismatch")
            keep, report = filter_qc_metrics(metrics, batch_key=batch_key, min_genes=min_genes,
                mad_limit=mad_limit, max_mito_percent=max_mito_percent,
                mitochondrial_features=int(mito.sum()), symbol_source=symbol_source)
            report["input_info"] = getattr(source, "input_info", {"format": "anndata_H5AD"})
            metrics["qc_pass"] = keep
            metrics.to_csv(folder/"metrics.csv")
            atomic_json(folder/"report.json", report)
            # External predictions are a separate assessment artifact, never fit input.
            source.obs.to_csv(folder/"external-reference.csv")
            from .qc_concordance import compare_qc
            atomic_json(folder/"concordance.json",compare_qc(source.obs,metrics))
            var = source.var.copy()
            var["gene_symbols"] = symbols.to_numpy()
            var.to_csv(folder/"features.csv")
            return {"cells": len(metrics), "retained": int(keep.sum())}
        return store.run("qc", finish, dependencies=chunks)


def _combine_moments(count, mean, m2, values):
    """Chan parallel moments; avoid subtracting large sum-of-squares totals."""
    n = values.shape[0]
    if not n:
        return count, mean, m2
    block_mean = np.asarray(values.mean(axis=0)).ravel()
    # Nonnegative normalized counts; clip small negative roundoff only.
    block_m2 = np.asarray(values.multiply(values).sum(axis=0)).ravel() - n*block_mean**2
    tolerance = 1e-10 * np.maximum(1, n*block_mean**2)
    if np.any(block_m2 < -tolerance):
        raise ValueError("unstable_gene_variance")
    block_m2 = np.maximum(block_m2, 0)
    delta = block_mean-mean
    total = count+n
    return total, mean+delta*(n/total), m2+block_m2+delta**2*(count*n/total)


def hvg_from_moments(counts, means, m2, feature_names, n_top_genes):
    """Scanpy Seurat bin/rank policy over streamed, batch-specific moments.

    The two bin helpers come from the uv-locked Scanpy implementation. Regression
    tests compare the entire table/selection to its public batched HVG method.
    Upgrading Scanpy requires rerunning that parity check.
    """
    from scanpy.preprocessing._highly_variable_genes import _get_mean_bins, _get_disp_stats

    tables = []
    for number, mean, squared in zip(counts, means, m2, strict=True):
        if number < 2:
            raise ValueError("HVG_requires_two_QC_passing_cells_per_observed_batch")
        expressed = mean > 0
        if not expressed.any():
            raise ValueError("batch_has_no_expressed_genes")
        variance = squared[expressed]/(number-1)
        dispersion = variance/mean[expressed]
        dispersion[dispersion == 0] = np.nan
        frame = pd.DataFrame({"means": np.log1p(mean[expressed]),
                              "dispersions": np.log(dispersion)})
        frame["mean_bin"] = _get_mean_bins(frame["means"], "seurat", 20)
        stats = _get_disp_stats(frame, "seurat")
        frame["dispersions_norm"] = (frame["dispersions"]-stats["avg"])/stats["dev"]
        finite = frame["dispersions_norm"].dropna().sort_values(ascending=False)
        if not len(finite):
            raise ValueError("no_estimable_normalized_gene_dispersions")
        cutoff = finite.iloc[min(n_top_genes, len(finite))-1]
        frame["highly_variable"] = frame["dispersions_norm"].fillna(-np.inf) >= cutoff
        frame.index = feature_names[expressed]
        frame = frame.drop(columns="mean_bin").reindex(feature_names, fill_value=0)
        frame["highly_variable"] = frame["highly_variable"].astype(int)
        tables.append(frame)
    result = pd.concat(tables).groupby(level=0, observed=True).agg(
        {"means": "mean", "dispersions": "mean", "dispersions_norm": "mean", "highly_variable": "sum"})
    result["highly_variable_nbatches"] = result["highly_variable"]
    result["highly_variable_intersection"] = result["highly_variable_nbatches"] == len(counts)
    result.sort_values(["highly_variable_nbatches", "dispersions_norm"],
                       ascending=False, na_position="last", inplace=True)
    result["highly_variable"] = np.arange(len(result)) < n_top_genes
    return result.loc[feature_names]


def stream_hvg(source_path, store, *, layer, batch_key, n_top_genes=2000,
               block_rows=128, max_block_bytes=64*1024**2, checkpoint_rows=1024):
    metrics = read_table(store.path("qc")/"metrics.csv")
    keep = metrics["qc_pass"].to_numpy(dtype=bool)
    levels = sorted(metrics[batch_key].astype(str).unique())
    groups = metrics[batch_key].astype(str).to_numpy()
    with open_expression(Path(source_path), layer=layer, batch_key=batch_key) as source:
        if not metrics.index.equals(source.obs.index):
            raise ValueError("HVG_input_cell_identity_mismatch")
        previous = "qc"
        for start in range(0, source.shape[0], checkpoint_rows):
            stop = min(start+checkpoint_rows, source.shape[0])
            name = f"moments_{start:08d}"
            def operation(folder):
                if previous == "qc":
                    count = np.zeros(len(levels), dtype=np.int64)
                    means = np.zeros((len(levels), source.shape[1]))
                    m2 = np.zeros_like(means)
                else:
                    with np.load(store.path(previous)/"moments.npz", allow_pickle=False) as state:
                        count, means, m2 = (state[k].copy() for k in ("count", "means", "m2"))
                for block_start in range(start, stop, block_rows):
                    block_stop = min(block_start+block_rows, stop)
                    rows = np.arange(block_start, block_stop)[keep[block_start:block_stop]]
                    if len(rows):
                        block = source.subset(rows, max_bytes=max_block_bytes)
                        validate_counts(block.X)
                        values = sparse.csr_matrix(block.X, dtype=np.float64)
                        totals = metrics["total_counts"].to_numpy()[rows]
                        values = values.multiply((1e4/totals)[:, None]).tocsr()
                        for i, group in enumerate(levels):
                            mask = groups[rows] == group
                            count[i], means[i], m2[i] = _combine_moments(
                                count[i], means[i], m2[i], values[mask])
                np.savez_compressed(folder/"moments.npz", count=count, means=means, m2=m2)
                return {"processed_through_row": stop, "batches": levels}
            store.run(name, operation, dependencies=(previous,))
            previous = name
        def finish(folder):
            with np.load(store.path(previous)/"moments.npz", allow_pickle=False) as state:
                table = hvg_from_moments(state["count"], state["means"], state["m2"],
                                         source.var.index, min(n_top_genes, source.shape[1]))
            table.to_csv(folder/"features.csv")
            return {"method": "seurat_batch_aware_streamed_moments", "HVG_count": int(table.highly_variable.sum()),
                    "normalization": "full_library_total_10000", "batch_key": batch_key}
        return store.run("hvg", finish, dependencies=(previous,))


def _assemble_selected_chunks(store, chunks):
    """Allocate the final CSR once, keeping at most one input chunk in memory."""
    sizes = []
    for name in chunks:
        block = sparse.load_npz(store.path(name)/"counts.npz")
        sizes.append((block.shape[0], block.shape[1], block.nnz))
        del block
    cells = sum(size[0] for size in sizes)
    features = sizes[0][1]
    nonzeros = sum(size[2] for size in sizes)
    index_dtype = np.int32 if max(cells, features, nonzeros) < 2**31 else np.int64
    values = np.empty(nonzeros, dtype=np.float32)
    indices = np.empty(nonzeros, dtype=index_dtype)
    indptr = np.empty(cells+1, dtype=index_dtype)
    indptr[0] = 0
    rows = np.empty(cells, dtype=np.int64)
    row_offset = value_offset = 0
    for name, (count, width, entries) in zip(chunks, sizes):
        block = sparse.load_npz(store.path(name)/"counts.npz")
        selected_rows = np.load(store.path(name)/"rows.npy", allow_pickle=False)
        if width != features or block.shape != (count, width) or block.nnz != entries or len(selected_rows) != count:
            raise ValueError("selected_chunk_shape_or_row_identity_mismatch")
        values[value_offset:value_offset+entries] = block.data
        indices[value_offset:value_offset+entries] = block.indices
        indptr[row_offset+1:row_offset+count+1] = block.indptr[1:]+value_offset
        rows[row_offset:row_offset+count] = selected_rows
        row_offset += count
        value_offset += entries
        del block, selected_rows
    return sparse.csr_matrix((values, indices, indptr), shape=(cells, features), copy=False), rows


def _normalize_selected_counts(counts, totals, block_rows):
    """Preserve float64 scaling then float32 log1p, without whole-matrix temporaries."""
    values = np.empty(counts.nnz, dtype=np.float32)
    indices = np.empty(counts.nnz, dtype=counts.indices.dtype)
    indptr = np.empty(len(totals)+1, dtype=counts.indptr.dtype)
    indptr[0] = 0
    offset = 0
    for start in range(0, len(totals), block_rows):
        stop = min(start+block_rows, len(totals))
        if len(totals) == 1:
            block = counts.multiply((1e4/totals)[:, None]).tocsr().astype(np.float32)
        else:
            # SciPy treats a one-row factor as a scalar and can keep float32.
            # Preserve the original multi-row calculation's float64 products,
            # including summing duplicate coordinates before the float32 cast.
            scaled = counts[start:stop].tocoo()
            scaled.data = scaled.data*(1e4/totals[start:stop])[scaled.row]
            block = scaled.tocsr().astype(np.float32)
            del scaled
        end = offset+block.nnz
        values[offset:end] = block.data
        indices[offset:end] = block.indices
        indptr[start+1:stop+1] = block.indptr[1:]+offset
        offset = end
        del block
    result = sparse.csr_matrix((values[:offset], indices[:offset], indptr), shape=counts.shape, copy=False)
    np.log1p(result.data, out=result.data)
    return result


def stream_selected(source_path, store, *, layer, block_rows=128,
                    max_block_bytes=64*1024**2, max_selected_bytes=512*1024**2):
    metrics = read_table(store.path("qc")/"metrics.csv")
    hvg = read_table(store.path("hvg")/"features.csv")
    features = np.flatnonzero(hvg["highly_variable"].to_numpy(dtype=bool))
    chunks = []
    with open_expression(Path(source_path), layer=layer) as source:
        for start in range(0, source.shape[0], block_rows):
            stop = min(start+block_rows, source.shape[0])
            rows = np.arange(start, stop)[metrics["qc_pass"].to_numpy(dtype=bool)[start:stop]]
            if not len(rows):
                continue
            name = f"selected_{start:08d}"
            def operation(folder):
                block = source.subset(rows, features, max_bytes=max_block_bytes)
                validate_counts(block.X)
                values = sparse.csr_matrix(block.X)
                if np.max(values.data, initial=0) > 2**24:
                    raise ValueError("selected_counts_exceed_exact_float32_integer_range")
                sparse.save_npz(folder/"counts.npz", values.astype(np.float32))
                np.save(folder/"rows.npy", rows, allow_pickle=False)
                return {"cells": len(rows), "features": len(features),
                        "sparse_bytes": values.nnz*8+(len(rows)+1)*4}
            store.run(name, operation, dependencies=("qc", "hvg"))
            chunks.append(name)
        def finish(folder):
            needed = sum(json.loads((store.path(n)/"receipt.json").read_text())["metadata"]["sparse_bytes"] for n in chunks)
            if needed*3 > max_selected_bytes:
                raise MemoryError("selected_feature_assembly_exceeds_budget")
            values, rows = _assemble_selected_chunks(store, chunks)
            obs = metrics.iloc[rows].copy()
            var = read_table(store.path("qc")/"features.csv").iloc[features].copy()
            for col in hvg:
                var[col] = hvg.iloc[features][col]
            normalized = _normalize_selected_counts(values, obs["total_counts"].to_numpy(), block_rows)
            result = ad.AnnData(normalized, obs=obs, var=var)
            result.layers["counts"] = values
            result.uns["normalization"] = "full_transcriptome_library_total_10000_then_log1p"
            result.uns["source_matrix"] = source.matrix_source
            result.uns["original_feature_count"] = source.shape[1]
            result.write_h5ad(folder/"selected.h5ad")
            return {"cells": result.n_obs, "features": result.n_vars,
                    "counts_scope": "selected_features_exact_counts_full_input_retained_externally"}
        return store.run("selected", finish, dependencies=chunks)
