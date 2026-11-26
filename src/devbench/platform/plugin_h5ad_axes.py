"""Stream AnnData labels and hash their exact ordered, canonical JSON array."""

import hashlib
import sqlite3
from contextlib import ExitStack, closing, contextmanager
from functools import lru_cache
from itertools import batched
from pathlib import Path
from tempfile import TemporaryDirectory

import h5py
import numpy as np

from devbench.analysis.workbench_inputs import _h5ad_encoding, _h5ad_index_name
from devbench.core.population_data import _node
from rna_kernels.canonical import canonical_json

_BLOCK_ROWS = 256
_READ_BYTES = 1024 * 1024
_MEMORY_LABELS = 8192
_MAX_LABEL_CHARS = 200


@contextmanager
def _label_database(size):
    # Supervised workers point their temporary directory at their own result spool.
    with ExitStack() as stack:
        path = ":memory:"
        if size > _MEMORY_LABELS:
            folder = stack.enter_context(TemporaryDirectory(prefix="h5ad-labels-"))
            path = str(Path(folder) / "labels.sqlite3")
        db = stack.enter_context(closing(sqlite3.connect(path)))
        db.execute("PRAGMA cache_size=-2048")
        yield db


def _reader(node, field, expected, validate_dataset):
    if not isinstance(node, h5py.Dataset) or node.shape != (expected,):
        raise ValueError("HDF5 axis does not match the expected one-dimensional shape")
    validate_dataset(node)
    dtype = node.dtype if field is None else node.dtype[field]
    text = h5py.check_string_dtype(dtype)
    if text is not None and text.length is None:
        # A vlen pointer's itemsize does not bound its payload. Read into fixed
        # slots, with one extra byte so an overlong UTF-8 label fails validation.
        dtype = h5py.string_dtype(text.encoding, length=4 * _MAX_LABEL_CHARS + 1)
        view = node.astype(dtype if field is None else np.dtype([(field, dtype)]))

        def read(selection):
            values = view[selection]
            return values if field is None else values[field]

        return read, dtype
    reader = node if field is None else node.fields(field)
    return reader.__getitem__, dtype


def _chunks(reader, dtype, size):
    stride = min(_BLOCK_ROWS, _READ_BYTES // max(dtype.itemsize, 1))
    if stride < 1:
        raise ValueError("HDF5 label width exceeds the inspection budget")
    for start in range(0, size, stride):
        yield reader(slice(start, min(start + stride, size)))


def _text_values(reader, dtype, size):
    encoding = h5py.check_string_dtype(dtype)
    if encoding is None:
        raise ValueError("HDF5 axis requires text labels")
    for values in _chunks(reader, dtype, size):
        for raw in values:
            if len(raw) > 4 * _MAX_LABEL_CHARS:
                raise ValueError("HDF5 label exceeds the supported size")
            value = raw.decode(encoding.encoding)
            if not value.strip() or len(value) > _MAX_LABEL_CHARS or "\x00" in value:
                raise ValueError("HDF5 label exceeds the supported size or is empty")
            yield value


def _categorical_values(reader, dtype, expected, categories, validate_dataset):
    if (
        dtype.kind not in "iu"
        or not isinstance(categories, h5py.Dataset)
        or categories.ndim != 1
        or not categories.size
    ):
        raise ValueError(
            "HDF5 categorical annotation requires integer codes and text levels"
        )
    levels, level_dtype = _reader(categories, None, categories.size, validate_dataset)
    with _label_database(categories.size) as db:
        db.execute("CREATE TABLE levels(label TEXT NOT NULL)")
        db.executemany(
            "INSERT INTO levels VALUES (?)",
            ((v,) for v in _text_values(levels, level_dtype, categories.size)),
        )

        @lru_cache(maxsize=_BLOCK_ROWS)
        def lookup(code):
            return db.execute(
                "SELECT label FROM levels WHERE rowid=?", (code + 1,)
            ).fetchone()[0]

        for codes in _chunks(reader, dtype, expected):
            if np.any(codes < 0) or np.any(codes >= categories.size):
                raise ValueError(
                    "HDF5 categorical annotation has missing or invalid labels"
                )
            yield from (lookup(int(code)) for code in codes)


def _labels(axis, field, expected, validate_dataset, annotation):
    if expected < 1:
        raise ValueError("HDF5 label axis must be nonempty")
    legacy = isinstance(axis, h5py.Dataset)
    node = axis if legacy else _node(axis, field)
    if isinstance(node, h5py.Group):
        if _h5ad_encoding(node) != "categorical":
            raise ValueError("Unsupported HDF5 label encoding")
        reader, dtype = _reader(_node(node, "codes"), None, expected, validate_dataset)
        yield from _categorical_values(
            reader, dtype, expected, _node(node, "categories"), validate_dataset
        )
        return
    reader, dtype = _reader(node, field if legacy else None, expected, validate_dataset)
    if annotation and dtype.kind in "iub":
        if legacy and dtype.kind in "iu" and "uns" in axis.file:
            uns = _node(axis.file, "uns")
            key = field + "_categories"
            if isinstance(uns, h5py.Group) and key in uns:
                yield from _categorical_values(
                    reader, dtype, expected, _node(uns, key), validate_dataset
                )
                return
        if field == "label":
            for codes in _chunks(reader, dtype, expected):
                if not np.isin(codes, (0, 1)).all():
                    raise ValueError("Phenotype labels require binary values")
                yield from (str(int(code)) for code in codes)
            return
    yield from _text_values(reader, dtype, expected)


def h5ad_label_blocks(axis, field, expected, validate_dataset, *, annotation=False):
    """Yield aligned blocks of 256 decoded labels, with a shorter final block."""
    return batched(
        _labels(axis, field, expected, validate_dataset, annotation), _BLOCK_ROWS
    )


def h5ad_index_digest(axis, expected, validate_dataset) -> str:
    checksum = hashlib.sha256(b"[")
    name = _h5ad_index_name(axis)
    with _label_database(expected) as db:
        db.execute("CREATE TABLE seen(label TEXT PRIMARY KEY) WITHOUT ROWID")
        for i, block in enumerate(
            h5ad_label_blocks(axis, name, expected, validate_dataset)
        ):
            try:
                db.executemany(
                    "INSERT INTO seen VALUES (?)", ((label,) for label in block)
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError("AnnData axis identifiers are not unique") from exc
            if i:
                checksum.update(b",")
            checksum.update(canonical_json(list(block))[1:-1])
    checksum.update(b"]")
    return checksum.hexdigest()
