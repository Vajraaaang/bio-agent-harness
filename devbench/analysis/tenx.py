"""Read Cell Ranger v3 feature-barcode HDF5 by bounded barcode slices.

10x stores features x barcodes as CSC. The same sparse arrays represent its
transpose as CSR; no full matrix transpose, conversion or input copy is needed.
Only Gene Expression features enter RNA library-size/QC calculations.
"""
from __future__ import annotations

import re

import anndata as ad
import numpy as np
import pandas as pd
from scipy import sparse

from .io import _indices


class TenXExpression:
    def __init__(self, handle, layer, *, batch_key="batch", max_metadata_bytes=128*1024**2):
        if layer not in (None, "counts"):
            raise ValueError("10x HDF5 contains raw counts only; requested layer is unavailable")
        self._handle = handle
        self._group = handle["matrix"]
        group = self._group
        required = {"shape", "data", "indices", "indptr", "barcodes", "features"}
        if not required.issubset(group):
            raise ValueError("Unsupported or incomplete 10x feature-barcode matrix")
        feature = group["features"]
        if not {"id", "name", "feature_type"}.issubset(feature):
            raise ValueError("10x v3 feature IDs, symbols and feature types are required")
        if group["shape"].shape != (2,):
            raise ValueError("Invalid 10x shape")
        self._raw_features, n_cells = map(int, group["shape"][:])
        if min(self._raw_features, n_cells) < 1:
            raise ValueError("Nonempty 10x expression is required")
        if (group["indptr"].shape != (n_cells+1,) or group["barcodes"].shape != (n_cells,)
                or any(feature[name].shape != (self._raw_features,) for name in ("id", "name", "feature_type"))
                or group["data"].ndim != 1 or group["indices"].shape != group["data"].shape):
            raise ValueError("10x matrix shape and metadata are inconsistent")
        if (n_cells*256+self._raw_features*3*256+(n_cells+1)*8) > max_metadata_bytes:
            raise MemoryError("10x annotation allocation estimate exceeds the metadata budget")
        if group["indptr"].dtype.kind not in "iu" or group["indices"].dtype.kind not in "iu":
            raise ValueError("10x sparse indices and pointers must be integers")
        self._pointer = group["indptr"][:].astype(np.int64)
        if (self._pointer[0] != 0 or self._pointer[-1] != len(group["data"])
                or np.any(np.diff(self._pointer) < 0)):
            raise ValueError("Invalid 10x sparse pointer array")
        ids = feature["id"].asstr()[:]
        symbols = feature["name"].asstr()[:]
        types = feature["feature_type"].asstr()[:]
        self._RNA = np.flatnonzero(types == "Gene Expression")
        if not len(self._RNA):
            raise ValueError("10x file has no Gene Expression features")
        self.var = pd.DataFrame({"gene_symbols": symbols[self._RNA], "feature_type": types[self._RNA]},
                                index=pd.Index(ids[self._RNA], dtype=str))
        if "genome" in feature:
            if feature["genome"].shape != (self._raw_features,):
                raise ValueError("10x genome annotation shape mismatch")
            self.var["genome"] = feature["genome"].asstr()[:][self._RNA]
        barcodes = pd.Index(group["barcodes"].asstr()[:], dtype=str)
        if not barcodes.is_unique or not self.var.index.is_unique:
            raise ValueError("10x barcode and RNA feature IDs must be unique")
        groups = [re.search(r"-([1-9][0-9]*)$", barcode) for barcode in barcodes]
        if all(groups):
            batches = ["GEM_well_"+match.group(1) for match in groups]
            basis = "observed_barcode_GEM_well_suffix; not_donor_or_independent_biological_identity"
        else:
            batches = ["input_file"]*len(barcodes)
            basis = "single_input_file_assumption; library_batch_and_biological_units_not_supplied"
        self.obs = pd.DataFrame({batch_key: batches}, index=barcodes)
        self.shape = (n_cells, len(self._RNA))
        self.matrix_source = "10x/matrix:Gene Expression"
        self.input_info = {"format": "10x_feature_barcode_HDF5_v3", "features_in_file": self._raw_features,
            "RNA_features": len(self._RNA), "excluded_non_RNA_features": self._raw_features-len(self._RNA),
            "batch_label_basis": basis, "batch_key": batch_key,
            "count_scope": "Gene Expression only; antibody and other assays excluded from RNA totals"}

    @property
    def closed(self):
        return not bool(self._handle.id.valid)

    def subset(self, observations=None, features=None, *, max_bytes=256*1024**2):
        if self.closed:
            raise ValueError("Backed input is closed")
        if type(max_bytes) is not int or max_bytes < 1:
            raise ValueError("max_bytes must be a positive integer")
        rows, columns = _indices(observations, self.shape[0]), _indices(features, self.shape[1])
        lengths = self._pointer[rows+1]-self._pointer[rows]
        nnz = int(lengths.sum())
        group = self._group
        estimate = 4*(nnz*(group["data"].dtype.itemsize+8)+(len(rows)+1)*8)+self._pointer.nbytes
        if estimate > max_bytes:
            raise MemoryError(f"10x slice allocation estimate {estimate} exceeds budget {max_bytes}")
        values, indices = [], []
        for row in rows:
            lo, hi = self._pointer[row:row+2]
            ix = group["indices"][lo:hi].astype(np.int64)
            if np.any(ix < 0) or np.any(ix >= self._raw_features):
                raise ValueError("10x sparse index exceeds feature dimensions")
            indices.append(ix)
            values.append(group["data"][lo:hi])
        pointer = np.r_[0, np.cumsum(lengths)]
        matrix = sparse.csr_matrix((np.concatenate(values), np.concatenate(indices), pointer),
                                   shape=(len(rows), self._raw_features))
        matrix.sum_duplicates()
        matrix = matrix[:, self._RNA[columns]].tocsr()
        return ad.AnnData(matrix, obs=self.obs.iloc[rows].copy(), var=self.var.iloc[columns].copy(),
            uns={"source_matrix": self.matrix_source, "allocation_estimate_bytes": estimate})

    def iter_rows(self, *, block_rows=256, features=None, max_bytes=64*1024**2, start=0):
        if type(block_rows) is not int or block_rows < 1 or type(start) is not int or not 0 <= start <= self.shape[0]:
            raise ValueError("Invalid row-block size or resume offset")
        while start < self.shape[0]:
            size = min(block_rows, self.shape[0]-start)
            while True:
                try:
                    block = self.subset(range(start, start+size), features, max_bytes=max_bytes)
                    break
                except MemoryError:
                    if size == 1:
                        raise
                    size = max(1,size//2)
            yield block
            start += size
