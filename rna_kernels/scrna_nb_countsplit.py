"""Two-fold Dirichlet-multinomial thinning of immutable raw RNA counts."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Annotated, Literal

import anndata as ad
import h5py
import numpy as np
import pandas as pd
from pydantic import Field, model_validator
from scipy import sparse

from devbench.core._files import hash_bounded_file, read_bounded_file
from devbench.core._hashing import decode_receipt_json
from devbench.core.expression_io import open_h5ad
from devbench.core.scientific_types import content_sha256

from .canonical import canonical_json
from .scrna_upstream_contract import (
    COUNT_LAYER,
    MAX_MATRIX_BYTES,
    SCRNA_OPERATIONS,
    Column,
    CountSplitParameters,
    Digest,
    MountedSingleCellRequest,
    _FrozenParameters,
)

_MAX_AXIS = 100_000
_MAX_COUNT = 2**31 - 1
_CHUNK_ENTRIES = 131_072


class IndependentDispersion(_FrozenParameters):
    """Reviewed NB size theta, with variance equal to mean plus mean²/theta."""

    format: Literal["devbench.nb-split-dispersion/1"]
    analysis_counts_sha256: Digest
    source_sha256: Digest
    source_receipt_sha256: Digest
    origin: Literal["known", "independent_cohort"]
    prior_free: Literal[True]
    feature_ids: tuple[Column, ...] = Field(min_length=1, max_length=_MAX_AXIS)
    theta: tuple[Annotated[float, Field(gt=0)], ...] = Field(
        min_length=1,
        max_length=_MAX_AXIS,
    )
    biological_unit_key: Column | None = None
    calibration_unit_ids: tuple[Column, ...] = Field(default=(), max_length=_MAX_AXIS)

    @model_validator(mode="after")
    def aligned(self) -> IndependentDispersion:
        if self.analysis_counts_sha256 == self.source_sha256:
            raise ValueError("Split dispersion requires independent provenance")
        if len(set(self.feature_ids)) != len(self.feature_ids) or len(
            self.feature_ids
        ) != len(self.theta):
            raise ValueError("Split dispersion must cover the exact feature axis")
        if self.origin == "independent_cohort" and (
            self.biological_unit_key is None
            or not self.calibration_unit_ids
            or len(set(self.calibration_unit_ids)) != len(self.calibration_unit_ids)
        ):
            raise ValueError("Independent dispersion requires a declared cohort axis")
        return self

    def verify_axes(self, obs: pd.DataFrame, features: tuple[str, ...]) -> None:
        if self.feature_ids != features:
            raise ValueError("Split dispersion differs from the ordered feature axis")
        if self.origin == "independent_cohort":
            key = self.biological_unit_key
            if key is None or key not in obs:
                raise ValueError(
                    "Independent dispersion requires the analysis unit axis"
                )
            units = obs[key].tolist()
            if any(
                not isinstance(unit, str) or not unit or unit.strip() != unit
                for unit in units
            ):
                raise ValueError("Independent dispersion requires identified units")
            if not set(units).isdisjoint(self.calibration_unit_ids):
                raise ValueError(
                    "Analysis and calibration biological units must be disjoint"
                )


def split_count_block(
    matrix: sparse.csr_matrix,
    theta: np.ndarray,
    fraction: float,
    rng: np.random.Generator,
) -> tuple[sparse.csr_matrix, sparse.csr_matrix]:
    """Sample Beta-Binomial conditionals, the two-fold DM specialization."""
    source = matrix.data
    if (
        not matrix.has_canonical_format
        or np.any(matrix.indices < 0)
        or np.any(matrix.indices >= matrix.shape[1])
        or source.dtype.kind not in "iuf"
        or not np.all(np.isfinite(source))
        or np.any(source < 0)
        or np.any(source > _MAX_COUNT)
        or np.any(source != np.floor(source))
    ):
        raise ValueError("NB split requires canonical nonnegative raw integer counts")
    if theta.shape != (matrix.shape[1],) or not 0 < fraction < 1:
        raise ValueError("NB split parameters do not match the feature axis")
    size = theta[matrix.indices]
    a, b = fraction * size, (1 - fraction) * size
    if not np.isfinite(size).all() or np.any(a <= 0) or np.any(b <= 0):
        raise ValueError("NB split size must give two finite positive shapes")
    probabilities = rng.beta(a, b)
    counts = source.astype(np.int64, copy=False)
    clustering = rng.binomial(counts, probabilities)
    inference = counts - clustering
    return (
        _fold_block(matrix, clustering),
        _fold_block(matrix, inference),
    )


def _fold_block(matrix: sparse.csr_matrix, data: np.ndarray) -> sparse.csr_matrix:
    fold = sparse.csr_matrix(
        (data, matrix.indices.copy(), matrix.indptr.copy()),
        shape=matrix.shape,
    )
    fold.eliminate_zeros()
    return fold


class _FoldWriter:
    """Append canonical CSR rows to H5AD without loading the full fold."""

    def __init__(self, handle: h5py.File, obs, var, role: str):
        handle.attrs.update(
            {
                "encoding-type": "anndata",
                "encoding-version": "0.1.0",
                "devbench_fold_role": role,
            }
        )
        ad.io.write_elem(handle, "obs", pd.DataFrame(index=obs))
        ad.io.write_elem(handle, "var", pd.DataFrame(index=var))
        group = handle.create_group("X")
        group.attrs.update(
            {
                "encoding-type": "csr_matrix",
                "encoding-version": "0.1.0",
                "shape": (len(obs), len(var)),
            }
        )
        options = {
            "maxshape": (None,),
            "chunks": (_CHUNK_ENTRIES,),
            "compression": "gzip",
        }
        self._data = group.create_dataset("data", (0,), dtype="i4", **options)
        self._indices = group.create_dataset("indices", (0,), dtype="i4", **options)
        self._offsets = group.create_dataset("indptr", (len(obs) + 1,), dtype="i8")
        self._position = 0
        self.total = 0

    def append(self, matrix: sparse.csr_matrix) -> None:
        start = len(self._data)
        stop = start + matrix.nnz
        self._data.resize((stop,))
        self._indices.resize((stop,))
        self._data[start:stop] = matrix.data
        self._indices[start:stop] = matrix.indices
        end = self._position + matrix.shape[0]
        self._offsets[self._position + 1 : end + 1] = matrix.indptr[1:] + start
        self._position = end
        self.total += int(matrix.data.sum(dtype=np.int64))


def _write_folds(expression, dispersion, parameters, clustering, inference):
    theta = np.asarray(dispersion.theta)
    rng = np.random.Generator(np.random.PCG64(parameters.seed))
    with (
        h5py.File(clustering, "x") as left,
        h5py.File(inference, "x") as right,
    ):
        cl = _FoldWriter(left, expression.obs.index, expression.var.index, "clustering")
        inf = _FoldWriter(
            right, expression.obs.index, expression.var.index, "inference"
        )
        for block in expression.iter_rows(block_rows=128, max_bytes=16 * 1024**2):
            first, second = split_count_block(
                sparse.csr_matrix(block.X),
                theta,
                parameters.clustering_fraction,
                rng,
            )
            cl.append(first)
            inf.append(second)
        return cl.total, inf.total


def split_mounted_counts(
    request: MountedSingleCellRequest,
    paths: Mapping[str, Path],
    root: Path,
    parameters: CountSplitParameters,
) -> dict[str, Path]:
    port = SCRNA_OPERATIONS["scrna_nb_countsplit"].inputs["split_dispersion"]
    raw = read_bounded_file(paths["split_dispersion"], port.max_bytes)
    if canonical_json(decode_receipt_json(raw)) != raw:
        raise ValueError("Split dispersion requires a canonical reviewed document")
    dispersion = IndependentDispersion.model_validate_json(raw)
    if dispersion.analysis_counts_sha256 != request.inputs["expression"]:
        raise ValueError("Split dispersion is bound to a different analysis matrix")
    clustering, inference = root / "clustering.h5ad", root / "inference.h5ad"
    roster = None
    with open_h5ad(paths["expression"], layer=COUNT_LAYER) as expression:
        if expression.shape[1] > _MAX_AXIS:
            raise ValueError("NB split exceeds the supported feature axis")
        dispersion.verify_axes(expression.obs, tuple(expression.var.index))
        if parameters.marker_cohort_columns is not None:
            from .scrna_nb_pseudobulk import write_donor_roster

            roster = root / "donor_roster.json"
            write_donor_roster(
                expression,
                roster,
                parameters.marker_cohort_columns,
                request.inputs["expression"],
            )
        totals = _write_folds(expression, dispersion, parameters, clustering, inference)
        cells, genes = expression.shape
        axis = content_sha256(tuple(expression.obs.index))
    if min(totals) < 1:
        raise ValueError("NB split produced an empty count fold")
    report = root / "split_report.json"
    report.write_bytes(
        canonical_json(
            {
                "format": "devbench.nb-countsplit/1",
                "split_distribution": "dirichlet_multinomial",
                "theta_parameterization": "negative_binomial_size",
                "dependence_assumption": "requires_correct_theta_and_nb_model",
                "counts_sha256": request.inputs["expression"],
                "dispersion_manifest_sha256": request.inputs["split_dispersion"],
                "dispersion": {
                    "origin": dispersion.origin,
                    "prior_free": dispersion.prior_free,
                    "source_sha256": dispersion.source_sha256,
                    "source_receipt_sha256": dispersion.source_receipt_sha256,
                    "values_sha256": content_sha256(dispersion.theta),
                    "feature_ids_sha256": content_sha256(dispersion.feature_ids),
                },
                "cell_ids_sha256": axis,
                "clustering_counts_sha256": hash_bounded_file(
                    clustering, MAX_MATRIX_BYTES
                ),
                "inference_counts_sha256": hash_bounded_file(
                    inference, MAX_MATRIX_BYTES
                ),
                "parameters": parameters.model_dump(mode="json"),
                "cells": cells,
                "genes": genes,
                "fold_totals": {"clustering": totals[0], "inference": totals[1]},
                "marker_inference_performed": False,
                **(
                    {"donor_roster_sha256": hash_bounded_file(roster, 64 * 1024**2)}
                    if roster is not None
                    else {}
                ),
            }
        )
    )
    return {
        "clustering_counts": clustering,
        "inference_counts": inference,
        "split_report": report,
        **({"donor_roster": roster} if roster is not None else {}),
    }
