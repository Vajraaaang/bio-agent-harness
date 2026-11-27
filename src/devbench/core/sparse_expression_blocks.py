"""Bounded minor-axis traversal of a disk-backed CSR or CSC matrix."""

import tempfile
from pathlib import Path

import numpy as np
from scipy import sparse


def _row_boundaries(counts, start, block_rows, max_entries):
    stops = []
    while start < len(counts):
        limit = min(start + block_rows, len(counts))
        cumulative = np.cumsum(counts[start:limit], dtype=np.int64)
        size = int(np.searchsorted(cumulative, max_entries, side="right"))
        if not size:
            raise MemoryError("One expression row exceeds the read budget")
        start += size
        stops.append(start)
    return np.asarray(stops, dtype=np.int64)


def _spool(records, buckets, directory):
    order = np.argsort(buckets, kind="stable")
    groups = buckets[order]
    offsets = np.flatnonzero(groups[1:] != groups[:-1]) + 1
    for indices in np.split(order, offsets):
        if len(indices):
            with (directory / str(buckets[indices[0]])).open("ab") as stream:
                records[indices].tofile(stream)


def iter_minor_blocks(group, shape, *, block_rows, max_bytes, start=0, features=None):
    """Traverse the minor axis in order using a private, temporary disk transpose.

    Each compressed input range is scanned once for row counts and once for
    values. The emitted CSR holds just one row interval. This also works for
    unsorted, duplicate or empty sparse coordinates.
    """
    data, indices, indptr = (group[key] for key in ("data", "indices", "indptr"))
    rows, cols = shape
    dtype = np.dtype([("row", "<i8"), ("col", "<i8"), ("value", data.dtype)])
    batch = max_bytes // (dtype.itemsize * 10)
    if batch < 1 or (rows + cols + 1) * 16 > max_bytes:
        raise MemoryError("Expression axis pointers exceed the read budget")
    pointer = np.asarray(indptr[:], dtype=np.int64)
    if (
        pointer.shape != (cols + 1,)
        or pointer[0] != 0
        or pointer[-1] != len(data)
        or indices.shape != data.shape
        or np.any(pointer[1:] < pointer[:-1])
    ):
        raise ValueError("Invalid sparse expression pointers")
    counts = np.zeros(rows, dtype=np.int64)
    for left in range(0, len(indices), batch):
        members = indices[left : left + batch]
        if np.any(members < 0) or np.any(members >= rows):
            raise ValueError("Invalid sparse expression coordinates")
        np.add.at(counts, members, 1)
    stops = _row_boundaries(
        counts, start, block_rows, max_bytes // (dtype.itemsize * 5)
    )
    del counts
    with tempfile.TemporaryDirectory(prefix="devbench-transpose-") as root:
        directory = Path(root)
        for left in range(0, len(data), batch):
            right = min(len(data), left + batch)
            record = np.empty(right - left, dtype=dtype)
            record["row"] = indices[left:right]
            record["col"] = (
                np.searchsorted(pointer, np.arange(left, right), side="right") - 1
            )
            record["value"] = data[left:right]
            record = record[record["row"] >= start]
            _spool(
                record, np.searchsorted(stops, record["row"], side="right"), directory
            )
        for group_index, stop in enumerate(stops):
            path = directory / str(group_index)
            records = (
                np.fromfile(path, dtype=dtype)
                if path.exists()
                else np.empty(0, dtype=dtype)
            )
            # Keep duplicate entries in the source dtype. COO.tocsr() would
            # first sum duplicates, potentially wrapping an integer count.
            order = np.argsort(records["row"], kind="stable")
            nrows = int(stop - start)
            indptr = np.empty(nrows + 1, dtype=np.int64)
            indptr[0] = 0
            np.cumsum(
                np.bincount(records["row"] - start, minlength=nrows), out=indptr[1:]
            )
            block = sparse.csr_matrix(
                (records["value"][order], records["col"][order], indptr),
                shape=(nrows, cols),
            )
            if features is not None:
                block = block[:, features]
            yield start, int(stop), block
            path.unlink(missing_ok=True)
            start = int(stop)
