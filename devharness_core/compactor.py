"""Compact temporal transcriptomics without exposing raw expression matrices.

All variance selection, normalization, SVD, and design diagnostics run in a
spawned worker. For large inputs, pass a store or path so spawn receives only a
filename. Passing a DataFrame supports small notebook inputs but necessarily
serializes the caller's already-resident DataFrame into the worker.
"""

import copy
import json
from pathlib import Path
from typing import Any, Dict, List, Tuple, Union

import numpy as np
import pandas as pd
from sklearn.decomposition import TruncatedSVD

from devharness_core.stages import parse_stages
from devharness_core.storage import ChunkedExpressionStore
from devharness_core.watchdog import HarnessResourceInterrupt, run_sandboxed


def _aligned_metadata(metadata_df: pd.DataFrame, sample_names: List[str], time_col: str) -> pd.DataFrame:
    metadata = metadata_df.copy()
    if "sample_id" in metadata.columns:
        metadata = metadata.set_index("sample_id", drop=False)
    metadata.index = metadata.index.map(str)
    if not metadata.index.is_unique:
        raise ValueError("Metadata sample identifiers must be unique")
    missing = set(sample_names) - set(metadata.index)
    if missing:
        raise ValueError("Metadata is missing expression samples: " + ", ".join(sorted(missing)[:5]))
    if time_col not in metadata:
        raise ValueError("Metadata is missing time column: " + time_col)
    metadata = metadata.loc[sample_names].copy()
    metadata["numeric_time"] = parse_stages(metadata[time_col])
    if "batch" in metadata and metadata["batch"].isna().any():
        raise ValueError("Batch values must not be missing")
    return metadata


def _compute_manifold(source: Union[str, pd.DataFrame], metadata: pd.DataFrame,
                      top_n: int, n_components: int) -> Dict[str, Any]:
    store = ChunkedExpressionStore(source) if isinstance(source, str) else None
    try:
        gene_names = store.gene_names if store else list(source.index.map(str))
        gene_count, sample_count = store.shape if store else source.shape
        selected_count = min(top_n, gene_count)
        estimated_bytes = sample_count * selected_count * 8 * 8
        if estimated_bytes > 1536 * 1024 * 1024:
            raise HarnessResourceInterrupt("Selected matrix would exceed the 1.5 GB active-memory allowance; reduce top_n")
        chunk_size = min(500, max(1, int(64 * 1024 * 1024 / (8 * sample_count))))
        if store:
            chunks = store.iter_gene_chunks(chunk_size)
        else:
            chunks = ((gene_names[start:start + chunk_size], source.iloc[start:start + chunk_size].to_numpy(dtype=float))
                      for start in range(0, gene_count, chunk_size))
        scores = np.empty(gene_count, dtype=float)
        library_sizes = np.zeros(sample_count, dtype=float)
        all_nonnegative = True
        offset = 0
        for _, values in chunks:
            values = np.asarray(values, dtype=float)
            if not np.isfinite(values).all():
                raise ValueError("Expression values must be finite")
            means = values.mean(axis=1)
            scores[offset:offset + len(values)] = values.var(axis=1) / np.maximum(np.abs(means), 1e-8)
            library_sizes += np.maximum(values, 0).sum(axis=0)
            all_nonnegative = all_nonnegative and bool(np.all(values >= 0))
            offset += len(values)
        selected_indices = np.argsort(-scores, kind="stable")[:selected_count]
        selected_genes = [gene_names[index] for index in selected_indices]
        values = (store.get_chunk(selected_indices, slice(None)).astype(float)
                  if store else source.iloc[selected_indices].to_numpy(dtype=float))
        if all_nonnegative:
            values /= np.maximum(library_sizes, 1.0)[None, :]
            values *= 10000.0
            np.log1p(values, out=values)
        values -= values.mean(axis=1, keepdims=True)
        scales = values.std(axis=1, keepdims=True)
        values /= np.where(scales > 1e-12, scales, 1.0)
        matrix = values.T
        components = min(n_components, sample_count, selected_count)
        if not np.any(matrix):
            embedding = np.zeros((sample_count, components), dtype=float)
            variance_ratio = np.zeros(components, dtype=float)
            loadings = np.zeros((components, selected_count), dtype=float)
        elif selected_count == 1:
            # sklearn requires two feature columns; a one-column SVD is exact.
            u, singular, vt = np.linalg.svd(matrix, full_matrices=False)
            embedding = u[:, :components] * singular[:components]
            loadings = vt[:components]
            variance_ratio = np.asarray([1.0])
        else:
            svd = TruncatedSVD(n_components=components, random_state=0)
            embedding = svd.fit_transform(matrix)
            variance_ratio = np.nan_to_num(svd.explained_variance_ratio_, nan=0.0)
            loadings = svd.components_
        numeric_time = metadata["numeric_time"].to_numpy(dtype=float)
        # SVD signs are arbitrary. Orient PC1 with increasing numeric time for
        # reproducibility; this summary metric is not a chronology truth judge.
        covariance = float(np.dot(embedding[:, 0], numeric_time - numeric_time.mean()))
        if covariance < 0:
            embedding[:, 0] *= -1
            loadings[0] *= -1
        centroids: Dict[str, List[float]] = {}
        variances: Dict[str, List[float]] = {}
        stage_positions = []
        for stage in np.unique(numeric_time):
            stage_embedding = embedding[numeric_time == stage]
            key = format(stage, ".12g")
            if float(key) != stage:
                key = repr(float(stage))
            centroid = stage_embedding.mean(axis=0)
            centroids[key] = centroid.tolist()
            variances[key] = stage_embedding.var(axis=0).tolist()
            stage_positions.append(float(centroid[0]))
        monotonicity = (float(np.sign(np.diff(stage_positions)).mean()) if len(stage_positions) >= 2 else None)
        design_parts = [np.ones((sample_count, 1)), numeric_time[:, None]]
        if "batch" in metadata:
            batch = pd.get_dummies(metadata["batch"].astype(str), drop_first=True, dtype=float)
            if batch.shape[1]:
                design_parts.append(batch.to_numpy())
        design = np.column_stack(design_parts)
        singular_values = np.linalg.svd(design, compute_uv=False)
        full_rank = np.linalg.matrix_rank(design) == design.shape[1]
        condition_number = float(singular_values[0] / singular_values[-1]) if full_rank else None
        if condition_number is not None and not np.isfinite(condition_number):
            condition_number = None
        driver_indices = np.argsort(-np.abs(loadings[0]), kind="stable")[:8]
        state = {
            "num_genes": gene_count,
            "num_samples": sample_count,
            "selected_gene_count": selected_count,
            "svd_variance_explained": variance_ratio.tolist(),
            "stage_centroids": centroids,
            "stage_variances": variances,
            "condition_number": condition_number,
            "condition_number_status": "finite" if condition_number is not None else "singular",
            "top_trajectory_driver_genes": [selected_genes[index] for index in driver_indices],
            "temporal_monotonicity": monotonicity,
        }
        if monotonicity is None:
            state["temporal_monotonicity_status"] = "insufficient_timepoints"
        return {"state": state, "embedding": embedding,
                "explained_variance_ratio": variance_ratio, "selected_genes": selected_genes}
    finally:
        if store is not None:
            store.close()


def _rounded(value: Any) -> Any:
    if isinstance(value, float):
        return float(format(value, ".5g"))
    if isinstance(value, dict):
        return {key: _rounded(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_rounded(item) for item in value]
    return value


class TemporalStateCompactor:
    """Compute temporal summaries using a memory-monitored spawned worker."""

    def __init__(self, counts_df: Union[pd.DataFrame, ChunkedExpressionStore, str, Path],
                 metadata_df: pd.DataFrame, time_col: str, top_n: int = 1000,
                 n_components: int = 5, max_mem_mb: float = 8000,
                 timeout_sec: float = 300) -> None:
        if not isinstance(top_n, int) or top_n < 1 or not isinstance(n_components, int) or n_components < 1:
            raise ValueError("top_n and n_components must be positive integers")
        if isinstance(counts_df, pd.DataFrame):
            ChunkedExpressionStore._validate_dataframe(counts_df)
            sample_names = list(counts_df.columns.map(str))
            source: Union[str, pd.DataFrame] = counts_df
        elif isinstance(counts_df, ChunkedExpressionStore):
            counts_df._ensure_open()
            sample_names = counts_df.sample_names
            source = str(counts_df.path)
        else:
            with ChunkedExpressionStore(Path(counts_df).expanduser().resolve()) as store:
                sample_names = store.sample_names
                source = str(store.path)
        metadata = _aligned_metadata(metadata_df, sample_names, time_col)
        result = run_sandboxed(_compute_manifold, args=(source, metadata, top_n, n_components),
                               max_mem_mb=max_mem_mb, timeout_sec=timeout_sec)
        self.state = result["state"]
        self.embedding = result["embedding"]
        self.explained_variance_ratio = result["explained_variance_ratio"]
        self.selected_genes = result["selected_genes"]

    def to_json_manifold(self, max_tokens: int = 1500) -> str:
        """Return strict JSON shorter in UTF-8 bytes than the requested budget.

        UTF-8 bytes conservatively bound ordinary byte-based tokenizers, without
        guessing a chars-per-token ratio or selecting a model-specific encoder.
        Over-budget summaries reduce drivers, component precision/detail, then
        stage coverage; truncation is explicit and first/last stages are kept
        whenever possible. A budget too small for required fields is rejected.
        """
        if not isinstance(max_tokens, int) or max_tokens < 1:
            raise ValueError("Token budget must be a positive integer")
        state = _rounded(copy.deepcopy(self.state))

        def encode() -> str:
            return json.dumps(state, separators=(",", ":"), ensure_ascii=False, allow_nan=False)

        def fits() -> bool:
            return len(encode().encode("utf-8")) < max_tokens

        if fits():
            return encode()
        state["summary_truncated"] = True
        while state["top_trajectory_driver_genes"] and not fits():
            state["top_trajectory_driver_genes"].pop()
        while len(state["svd_variance_explained"]) > 1 and not fits():
            state["svd_variance_explained"].pop()
            for key in ("stage_centroids", "stage_variances"):
                for vector in state[key].values():
                    vector.pop()
        while len(state["stage_centroids"]) > 2 and not fits():
            keys = list(state["stage_centroids"])
            removed = keys[len(keys) // 2]
            del state["stage_centroids"][removed]
            del state["stage_variances"][removed]
        if not fits():
            raise ValueError("Token budget too small to preserve required manifold fields")
        return encode()
