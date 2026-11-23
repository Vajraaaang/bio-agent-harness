"""Selected-matrix AnnData access without eager loading of unrelated layers."""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

import anndata as ad
import h5py
import numpy as np
from scipy import sparse


def _indices(selection, size):
    if selection is None:
        return np.arange(size, dtype=np.int64)
    if isinstance(selection, slice):
        start, stop, step = selection.indices(size)
        values = np.arange(start, stop, step, dtype=np.int64)
    else:
        values = np.asarray(list(selection))
    if (values.ndim != 1 or values.size == 0 or values.dtype.kind not in "iu"
            or np.any(values < 0) or np.any(values >= size)
            or len(np.unique(values)) != len(values)):
        raise ValueError("Select distinct, in-range integer indices")
    return values.astype(np.int64, copy=False)


class BackedExpression:
    """Read-only context-owned matrix; materialization is explicit and budgeted.

    Only obs and var annotations are loaded at open. X or one named layer stays
    on disk through AnnData's public CSR/CSC interface (or an HDF5 dense array).
    Other layers, raw, uns and embeddings are deliberately not deserialized.
    Allocation estimates are conservative guards, not operating-system caps.
    """

    def __init__(self, handle, layer):
        self._handle = handle
        if layer is not None and (not isinstance(layer, str) or not layer or "/" in layer):
            raise ValueError("layer must be a single nonempty name")
        self.matrix_source = "X" if layer is None else f"layers/{layer}"
        if self.matrix_source not in handle:
            raise ValueError(f"Requested matrix/layer is unavailable: {self.matrix_source}")
        self._element = handle[self.matrix_source]
        self.matrix = (ad.io.sparse_dataset(self._element)
                       if isinstance(self._element, h5py.Group) else self._element)
        self.shape = tuple(self.matrix.shape)
        if len(self.shape) != 2 or min(self.shape) < 1:
            raise ValueError("Expression must have a nonempty two-dimensional shape")
        self.obs = ad.io.read_elem(handle["obs"])
        self.var = ad.io.read_elem(handle["var"])
        if (len(self.obs), len(self.var)) != self.shape:
            raise ValueError("Expression shape does not match observation/feature metadata")
        if not self.obs.index.is_unique or not self.var.index.is_unique:
            raise ValueError("Observation and feature identifiers must be unique")

    @property
    def closed(self):
        return not bool(self._handle.id.valid)

    def _allocation_estimate(self, rows, columns):
        if isinstance(self._element, h5py.Dataset):
            # The HDF5 row read precedes column filtering; count the full rows.
            return 3 * len(rows) * self.shape[1] * self._element.dtype.itemsize
        major = rows if self.matrix.format == "csr" else columns
        pointer = self._element["indptr"]
        ordered = np.sort(major)
        nnz = int(np.sum(pointer[ordered + 1] - pointer[ordered], dtype=np.int64))
        values = nnz * (self._element["data"].dtype.itemsize + self._element["indices"].dtype.itemsize)
        # Include AnnData's potentially cached full pointer array and copies.
        return 3 * (values + (len(major) + 1) * pointer.dtype.itemsize) + pointer.size * pointer.dtype.itemsize

    def _read_csr_rows(self, rows, max_bytes):
        if len(rows) <= 128:
            return self.matrix[rows, :]
        # AnnData's fancy row read can concatenate a complete library in one
        # allocation. Read bounded pieces into one final CSR instead.
        pointer = self._element["indptr"]
        order = np.argsort(rows)
        widths = np.empty(len(rows), dtype=np.int64)
        widths[order] = pointer[rows[order]+1]-pointer[rows[order]]
        entries = int(widths.sum())
        index_dtype = np.int32 if max(entries, len(rows), self.shape[1]) < 2**31 else np.int64
        data = np.empty(entries, dtype=self._element["data"].dtype)
        indices = np.empty(entries, dtype=index_dtype)
        indptr = np.empty(len(rows)+1, dtype=index_dtype)
        indptr[0] = 0
        np.cumsum(widths, out=indptr[1:])
        remaining = max_bytes-data.nbytes-indices.nbytes-indptr.nbytes
        block_budget = min(64*1024**2, remaining)
        start = 0
        while start < len(rows):
            count = min(128, len(rows)-start)
            while self._allocation_estimate(rows[start:start+count], None) > block_budget:
                if count == 1:
                    raise MemoryError("CSR row buffer exceeds remaining allocation budget")
                count = max(1, count//2)
            stop = start+count
            block = self.matrix[rows[start:stop], :]
            left, right = int(indptr[start]), int(indptr[stop])
            if block.shape != (count, self.shape[1]) or block.nnz != right-left:
                raise ValueError("CSR row materialization changed shape or entry count")
            data[left:right] = block.data
            indices[left:right] = block.indices
            del block
            start = stop
        return sparse.csr_matrix((data, indices, indptr), shape=(len(rows), self.shape[1]), copy=False)

    def subset(self, observations=None, features=None, *, max_bytes=256 * 1024**2):
        if self.closed:
            raise ValueError("Backed input is closed")
        if type(max_bytes) is not int or max_bytes < 1:
            raise ValueError("max_bytes must be a positive integer")
        rows, columns = _indices(observations, self.shape[0]), _indices(features, self.shape[1])
        estimate = self._allocation_estimate(rows, columns)
        if estimate > max_bytes:
            raise MemoryError(f"Subset allocation estimate {estimate} exceeds budget {max_bytes} bytes")
        if isinstance(self._element, h5py.Dataset):
            order = np.argsort(rows)
            block = self.matrix[rows[order], :][np.argsort(order)][:, columns]
        elif self.matrix.format == "csr":
            block = self._read_csr_rows(rows, max_bytes)
            if features is not None:
                block = block[:, columns]
            block = block.tocsr()
        else:
            block = self.matrix[:, columns][rows, :].tocsr()
        return ad.AnnData(X=block, obs=self.obs.iloc[rows].copy(), var=self.var.iloc[columns].copy(),
                          uns={"source_matrix": self.matrix_source,
                               "allocation_estimate_bytes": estimate})

    def iter_rows(self, *, block_rows=256, features=None, max_bytes=64*1024**2, start=0):
        """Stream bounded CSR/dense row blocks; a resume offset is explicit."""
        if type(block_rows) is not int or block_rows < 1 or type(start) is not int or not 0 <= start <= self.shape[0]:
            raise ValueError("Invalid row-block size or resume offset")
        if not isinstance(self._element, h5py.Dataset) and self.matrix.format != "csr":
            raise ValueError("Streaming all features by row requires CSR or dense storage; CSC needs an explicit conversion plan")
        position = start
        while position < self.shape[0]:
            count = min(block_rows, self.shape[0]-position)
            while True:
                try:
                    block = self.subset(range(position, position+count), features, max_bytes=max_bytes)
                    break
                except MemoryError:
                    if count == 1:
                        raise
                    count = max(1, count//2)
            yield block
            position += count



@contextmanager
def open_h5ad(path: str | Path, *, layer: str | None = None):
    """Open a selected expression matrix; always close its HDF5 handle."""
    path = Path(path)
    if not path.is_absolute():
        raise ValueError("Input path must be absolute")
    with h5py.File(path, "r") as handle:
        yield BackedExpression(handle, layer)


@contextmanager
def open_expression(path: str | Path, *, layer: str | None = None, batch_key="batch"):
    """Dispatch by on-disk schema, without converting or copying the input."""
    path = Path(path)
    if not path.is_absolute():
        raise ValueError("Input path must be absolute")
    with h5py.File(path, "r") as handle:
        if "obs" in handle and "var" in handle:
            yield BackedExpression(handle, layer)
        elif "matrix" in handle and isinstance(handle["matrix"], h5py.Group):
            from .tenx import TenXExpression
            yield TenXExpression(handle, layer, batch_key=batch_key)
        else:
            raise ValueError("Unsupported input: use AnnData H5AD or Cell Ranger v3 feature-barcode HDF5")
