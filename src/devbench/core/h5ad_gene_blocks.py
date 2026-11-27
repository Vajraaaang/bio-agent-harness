"""Bounded gene blocks from a selected, already-open AnnData matrix."""

import math
from collections.abc import Iterator

import h5py
import numpy as np

from .hdf5_custody import validate_hdf5_tree
from .population_data import _node, _sparse_components
from .sparse_expression_blocks import iter_minor_blocks

_MAX_BLOCK_BYTES = 16 * 1024**2
_MAX_AXIS_BYTES = 64 * 1024**2


def _bounded_node(
    group: h5py.Group | h5py.Dataset,
    name: str,
) -> h5py.Group | h5py.Dataset:
    if not isinstance(group, h5py.Group):
        raise ValueError("AnnData groups must use internal HDF5 groups")
    node = _node(group, name)
    _check_chunk(node)
    return node


def _check_chunk(node: h5py.Group | h5py.Dataset) -> None:
    if (
        isinstance(node, h5py.Dataset)
        and node.chunks
        and math.prod(node.chunks) * node.dtype.itemsize > _MAX_BLOCK_BYTES
    ):
        raise ValueError("AnnData dataset has a chunk exceeding the read budget")


def _axis(handle: h5py.File, name: str, size: int) -> list[str]:
    frame = _bounded_node(handle, name)
    if not isinstance(frame, h5py.Group):
        raise ValueError("AnnData axes must be dataframe groups")
    validate_hdf5_tree(frame)
    if "_index" in frame.attrs and frame.attrs.get_id("_index").shape != ():
        raise ValueError("AnnData index name must be scalar")
    index = frame.attrs.get("_index", "_index")
    if isinstance(index, bytes):
        index = index.decode("utf-8")
    axis = _bounded_node(frame, index)
    validate_hdf5_tree(axis, max_materialized_bytes=_MAX_AXIS_BYTES)
    if (
        not isinstance(axis, h5py.Dataset)
        or axis.shape != (size,)
        or h5py.check_string_dtype(axis.dtype) is None
    ):
        raise ValueError("AnnData text indices must match the expression axes")
    if size * max(32, axis.dtype.itemsize) > _MAX_AXIS_BYTES:
        raise ValueError("AnnData index exceeds the metadata budget")
    labels: list[str] = []
    label_bytes = 0
    for start in range(0, size, 1024):
        block = axis.asstr()[start : start + 1024].tolist()
        label_bytes += sum(len(value.encode("utf-8")) for value in block)
        if label_bytes > _MAX_AXIS_BYTES:
            raise ValueError("AnnData index exceeds the decoded metadata budget")
        labels.extend(block)
    if len(set(labels)) != size or any(not value for value in labels):
        raise ValueError("AnnData indices must contain distinct nonempty labels")
    return labels


def _header(node: h5py.Group | h5py.Dataset) -> tuple[tuple[int, int], str | None]:
    format_name = None
    if isinstance(node, h5py.Dataset):
        shape = node.shape
    else:
        attrs = node.attrs
        if (
            "shape" not in attrs
            or attrs.get_id("shape").shape != (2,)
            or "encoding-type" not in attrs
            or attrs.get_id("encoding-type").shape != ()
        ):
            raise ValueError("Invalid AnnData sparse matrix header")
        shape = tuple(attrs["shape"])
        encoding = attrs["encoding-type"]
        if isinstance(encoding, bytes):
            encoding = encoding.decode("utf-8")
        if encoding not in {"csr_matrix", "csc_matrix"}:
            raise ValueError("AnnData supports dense, CSR, or CSC expression storage")
        format_name = encoding[:3]
    if (
        shape is None
        or len(shape) != 2
        or any(not isinstance(size, (int, np.integer)) or size < 1 for size in shape)
    ):
        raise ValueError("AnnData expression requires nonempty two-dimensional axes")
    return (int(shape[0]), int(shape[1])), format_name


class H5adGeneBlocks:
    """Read gene by observation blocks without unfolding unrelated metadata."""

    def __init__(
        self,
        handle: h5py.File,
        *,
        layer: str | None,
    ) -> None:
        matrix = (
            _bounded_node(handle, "X")
            if layer is None
            else _bounded_node(
                _bounded_node(handle, "layers"),
                layer,
            )
        )
        validate_hdf5_tree(matrix)
        (cells, genes), layout = _header(matrix)
        self.shape = (genes, cells)
        self._matrix = matrix
        self._layout = layout
        self._sparse = None
        self._offsets: np.ndarray | None = None
        if layout is None:
            self._dtype = matrix.dtype
        else:
            for component in ("data", "indices", "indptr"):
                _bounded_node(matrix, component)
            components = _sparse_components(
                matrix,
                (cells, genes),
                layout,
                _MAX_BLOCK_BYTES,
            )
            data, _, indptr = components
            self._sparse = components
            self._offsets = indptr[:] if layout == "csc" else None
            self._dtype = data.dtype
        if self._dtype.kind not in "biuf":
            raise ValueError("AnnData expression must be real numeric")
        self.sample_names = _axis(handle, "obs", cells)
        self.gene_names = _axis(handle, "var", genes)

    def _read_sparse(self, start: int, stop: int) -> np.ndarray:
        assert self._sparse is not None and self._offsets is not None
        data, indices, _ = self._sparse
        accumulator = {
            "u": np.uint64,
            "i": np.int64,
            "b": np.int64,
            "f": np.float64,
        }[self._dtype.kind]
        result = np.zeros(
            (stop - start, self.shape[1]),
            dtype=np.result_type(self._dtype, accumulator),
        )
        for major in range(start, stop):
            left = int(self._offsets[major])
            right = int(self._offsets[major + 1])
            for offset in range(left, right, 65_536):
                end = min(offset + 65_536, right)
                np.add.at(result[major - start], indices[offset:end], data[offset:end])
        return result

    def iter_gene_chunks(
        self,
        chunk_size: int,
    ) -> Iterator[tuple[list[str], np.ndarray]]:
        width = min(
            chunk_size,
            _MAX_BLOCK_BYTES // (self.shape[1] * max(8, self._dtype.itemsize)),
        )
        if width < 1:
            raise ValueError("AnnData expression row exceeds the numeric read budget")
        if self._layout == "csr":
            for start, stop, block in iter_minor_blocks(
                self._matrix, self.shape, block_rows=width, max_bytes=64 * 1024**2
            ):
                yield self.gene_names[start:stop], block.toarray()
            return
        for start in range(0, self.shape[0], width):
            stop = min(start + width, self.shape[0])
            if self._sparse is None:
                block = self._matrix[:, start:stop].T
            else:
                block = self._read_sparse(start, stop)
            yield self.gene_names[start:stop], block


class ZarrGeneBlocks:
    """Gene-axis traversal using the same sparse transposition as H5AD."""

    def __init__(self, source):
        self.source = source
        self.shape = source.shape[::-1]
        self.gene_names = source.var.index.tolist()
        self.sample_names = source.obs.index.tolist()
        source.validate_sparse_storage()

    def iter_gene_chunks(self, chunk_size):
        width = min(chunk_size, _MAX_BLOCK_BYTES // (self.shape[1] * 8))
        if width < 1:
            raise ValueError("AnnData expression row exceeds the numeric read budget")
        node = self.source._element
        if getattr(self.source.matrix, "format", None) == "csr":
            for start, stop, block in iter_minor_blocks(
                node, self.shape, block_rows=width, max_bytes=64 * 1024**2
            ):
                yield self.gene_names[start:stop], block.toarray()
            return
        for start in range(0, self.shape[0], width):
            stop = min(self.shape[0], start + width)
            block = self.source.matrix[:, start:stop].T
            if hasattr(block, "toarray"):
                block = block.toarray()
            yield self.gene_names[start:stop], block
