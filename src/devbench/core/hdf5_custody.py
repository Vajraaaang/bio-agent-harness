"""Check HDF5 object graphs and storage dependencies before decoding AnnData."""

from __future__ import annotations

import math
import os
from contextlib import closing
from pathlib import Path

import h5py
import numpy as np

from .input_files import _hash_fd, _open_file, _signature

_MAX_OBJECTS = 16_384
_MAX_DEPTH = 32
_MAX_CHUNK = 64 << 20
_MAX_ATTRIBUTES = 16 << 20
_MAX_LABEL = 4_096
_FILTERS = {
    h5py.h5z.FILTER_DEFLATE,
    h5py.h5z.FILTER_FLETCHER32,
    h5py.h5z.FILTER_SHUFFLE,
    h5py.h5z.FILTER_LZF,
}


def _type(type_id):
    if type_id.detect_class(h5py.h5t.REFERENCE):
        raise ValueError("HDF5 object and region references are outside matrix custody")
    if type_id.detect_class(h5py.h5t.VLEN):
        raise ValueError("HDF5 variable-length arrays are outside matrix custody")
    if type_id.get_size() > 65_536:
        raise ValueError("HDF5 element type exceeds the metadata budget")


def _child_name(value):
    if isinstance(value, bytes):
        value = value.decode("utf-8")
    if (
        not isinstance(value, str)
        or not value
        or "/" in value
        or "\0" in value
        or value in {".", ".."}
    ):
        raise ValueError(
            "AnnData metadata keys must identify a direct child of their HDF5 group"
        )
    return value


def validate_frame_names(group):
    """A dataframe's attribute-supplied keys must stay within its checked tree."""
    for field in ("_index", "column-order"):
        if field not in group.attrs:
            continue
        values = np.asarray(group.attrs[field])
        expected_dimension = 0 if field == "_index" else 1
        if values.ndim != expected_dimension or values.size > _MAX_OBJECTS:
            raise ValueError("AnnData dataframe has invalid HDF5 column metadata")
        names = [_child_name(value) for value in values.reshape(-1).tolist()]
        if len(set(names)) != len(names) or any(name not in group for name in names):
            raise ValueError(
                "AnnData dataframe column metadata differs from its HDF5 children"
            )


def _series_categories(node):
    if "categories" not in node.attrs:
        return
    target = node.attrs["categories"]
    if isinstance(target, bytes):
        target = target.decode("utf-8")
    if isinstance(target, str):
        # Legacy AnnData categorical encodings point inside this dataframe.
        for part in target.split("/"):
            _child_name(part)


def _attributes(node):
    total = 0
    if len(node.attrs) > 1024:
        raise ValueError("HDF5 attributes exceed the metadata budget")
    for name in node.attrs:
        with closing(node.attrs.get_id(name)) as attribute:
            dtype = attribute.get_type()
            _type(dtype)
            count = int(attribute.get_space().get_simple_extent_npoints())
            if not count:
                continue
            total += max(attribute.get_storage_size(), count * dtype.get_size())
            if total > _MAX_ATTRIBUTES:
                raise ValueError("HDF5 attributes exceed the metadata budget")
            if attribute.dtype.hasobject:
                total += _text_attribute_bytes(attribute, count)
    return total


def _text_attribute_bytes(attribute, count):
    kind = h5py.check_string_dtype(attribute.dtype)
    # Only plain strings may use a variable-size heap in AnnData attributes.
    if kind is None or count * (_MAX_LABEL + 1) > _MAX_ATTRIBUTES:
        raise ValueError("HDF5 heap attribute exceeds the metadata budget")
    if not count:
        return 0
    dtype = h5py.string_dtype(kind.encoding, length=_MAX_LABEL + 1)
    buffer = np.empty(attribute.shape, dtype=dtype)
    target = h5py.h5t.py_create(dtype, logical=True)
    attribute.read(buffer, mtype=target)
    sizes = np.char.str_len(buffer)
    if np.any(sizes > _MAX_LABEL):
        raise ValueError("HDF5 attribute text exceeds the metadata budget")
    return int(sizes.sum())


def _check_storage(dataset):
    if dataset.is_virtual or dataset.external:
        raise ValueError("HDF5 virtual and external dataset storage is prohibited")
    _type(dataset.id.get_type())
    props = dataset.id.get_create_plist()
    if any(props.get_filter(i)[0] not in _FILTERS for i in range(props.get_nfilters())):
        raise ValueError("HDF5 dataset uses an unreviewed filter")
    if (
        dataset.chunks
        and math.prod(dataset.chunks) * dataset.dtype.itemsize > _MAX_CHUNK
    ):
        raise ValueError("HDF5 physical chunk exceeds the read budget")


def _decoded_size(node, limit):
    if node.shape is None:
        return 0
    count = math.prod(node.shape)
    size = count * node.dtype.itemsize
    if size > limit:
        raise ValueError("AnnData annotation exceeds the materialization budget")
    if not node.dtype.hasobject:
        return size
    kind = h5py.check_string_dtype(node.dtype)
    if kind is None or node.ndim != 1:
        raise ValueError("Unsupported variable-length AnnData annotation")
    size += count * 64  # index pointers and Python string objects
    if size > limit:
        raise ValueError("AnnData annotation exceeds the materialization budget")
    # HDF5's stored byte count for a variable string excludes heap bytes. A
    # fixed-width destination detects oversized strings without materializing
    # the complete string vector. Conversion uses the source character set.
    text = node.astype(h5py.string_dtype(kind.encoding, length=_MAX_LABEL + 1))
    for start in range(0, count, 256):
        lengths = np.char.str_len(text[start : start + 256])
        if np.any(lengths > _MAX_LABEL):
            raise ValueError("AnnData annotation text exceeds the read budget")
        size += int(lengths.sum()) * 4  # worst-case Python Unicode storage
        if size > limit:
            raise ValueError("AnnData annotation exceeds the materialization budget")
    return size


def validate_hdf5_tree(node, *, max_materialized_bytes=None):
    """Check only hard links and reviewed internal storage. Return decoded size
    when an eager-read budget is supplied; otherwise inspect structural metadata.
    """
    visited = {}
    active = set()
    edges = attribute_bytes = 0

    def visit(current, depth):
        nonlocal edges, attribute_bytes
        address = h5py.h5o.get_info(current.id).addr
        if address in active or depth > _MAX_DEPTH:
            raise ValueError("Cyclic or excessively deep HDF5 object graph")
        if address in visited:
            return visited[address]
        attribute_bytes += _attributes(current)
        if attribute_bytes > _MAX_ATTRIBUTES:
            raise ValueError("HDF5 attributes exceed the metadata budget")
        if isinstance(current, h5py.Dataset):
            _check_storage(current)
            _series_categories(current)
            size = (
                0
                if max_materialized_bytes is None
                else _decoded_size(current, max_materialized_bytes)
            )
        elif isinstance(current, h5py.Group):
            edges += len(current)
            if edges > _MAX_OBJECTS:
                raise ValueError("HDF5 object graph exceeds the metadata budget")
            validate_frame_names(current)
            active.add(address)
            size = sum(visit(_child(current, key), depth + 1) for key in current)
            active.remove(address)
        else:
            raise ValueError("Unsupported object in HDF5 expression storage")
        if max_materialized_bytes is not None and size > max_materialized_bytes:
            raise ValueError("AnnData annotation exceeds the materialization budget")
        visited[address] = size
        return size

    return visit(node, 0)


def _child(group, name):
    if not isinstance(group.get(name, getlink=True), h5py.HardLink):
        raise ValueError("HDF5 external and symbolic links are prohibited")
    return group[name]


def _validate_slots(handle):
    if not all(name in handle for name in ("obs", "var", "X")) or not all(
        isinstance(handle[name], h5py.Group) for name in ("obs", "var")
    ):
        raise ValueError(
            "H5AD requires local observation, feature and expression slots"
        )
    matrix = handle["X"]
    if isinstance(matrix, h5py.Dataset):
        if matrix.ndim != 2 or matrix.dtype.kind not in "biuf":
            raise ValueError(
                "H5AD requires a two-dimensional numeric expression matrix"
            )
        return
    layout = matrix.attrs.get("encoding-type")
    shape = np.asarray(matrix.attrs.get("shape"))
    if (
        layout not in {"csr_matrix", "csc_matrix"}
        or shape.shape != (2,)
        or shape.dtype.kind not in "iu"
        or np.any(shape < 0)
    ):
        raise ValueError("H5AD sparse expression header is invalid")
    if not all(
        name in matrix
        and isinstance(matrix[name], h5py.Dataset)
        and matrix[name].ndim == 1
        for name in ("data", "indices", "indptr")
    ):
        raise ValueError("H5AD sparse expression requires three internal arrays")
    major = int(shape[0 if layout == "csr_matrix" else 1])
    data, indices, indptr = (matrix[k] for k in ("data", "indices", "indptr"))
    if (
        data.shape != indices.shape
        or indptr.shape != (major + 1,)
        or data.dtype.kind not in "biuf"
        or indices.dtype.kind not in "iu"
        or indptr.dtype.kind not in "iu"
    ):
        raise ValueError("H5AD sparse components differ from their declared shape")


def validate_h5ad_custody(path, *, expected_sha256=None):
    """Verify the entire H5AD derivative on its original no-follow descriptor."""
    path = Path(path)
    if not path.is_absolute() or path.resolve(strict=True) != path:
        raise ValueError("H5AD custody requires a canonical absolute path")
    root = path.parent.stat()
    with _open_file(path.parent, (path.name,), (root.st_dev, root.st_ino)) as fd:
        info = os.fstat(fd)
        before = _signature(info)
        if expected_sha256 is not None:
            if _hash_fd(fd, info.st_size) != expected_sha256:
                raise ValueError("H5AD schema validation digest mismatch")
            os.lseek(fd, 0, os.SEEK_SET)
        with os.fdopen(os.dup(fd), "rb") as stream, h5py.File(stream, "r") as handle:
            validate_hdf5_tree(handle)
            _validate_slots(handle)
        if _signature(os.fstat(fd)) != before or _signature(path.lstat()) != before:
            raise ValueError("H5AD derivative changed during custody validation")
