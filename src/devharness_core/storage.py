"""Gene-by-sample storage for Zarr, HDF5, AnnData, tables, and 10x MTX.

CSV and TSV scanning is streaming. AnnData reads only axis labels and the
selected dense or sparse matrix. 10x MTX is loaded into bounded sparse storage.
DataFrame writers do not make an additional whole-matrix NumPy copy.
"""

import csv
import gzip
import math
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, List, Tuple, Union

import h5py
import numpy as np
import pandas as pd
import zarr
from numcodecs import Blosc

from .provenance import _open_regular

PathLike = Union[str, Path]
_MAX_GENE_BLOCK_BYTES = 16 * 1024 * 1024
_MAX_READ_BYTES = 100_000_000
_MAX_AXIS_BYTES = 64 * 1024 * 1024
_MAX_MTX_LINE_BYTES = 65_536
_MTX_SUFFIXES = (".mtx", ".mtx.gz")


def _chunk_rows(requested_rows: int, selected_width: int, *, itemsize: int = 8) -> int:
    """Bound numeric storage before reading a gene block."""
    row_bytes = selected_width * itemsize
    bounded_rows = _MAX_GENE_BLOCK_BYTES // row_bytes
    if bounded_rows < 1:
        raise ValueError("Selected expression row exceeds the numeric read budget")
    return min(requested_rows, bounded_rows)


def _labels(values: Any, name: str) -> List[str]:
    labels = [str(value) for value in values]
    if not labels or any(not value for value in labels):
        raise ValueError(name + " must be nonempty")
    if len(set(labels)) != len(labels):
        raise ValueError(name + " must be unique")
    return labels


def _indices(selection: Any, length: int) -> np.ndarray:
    if isinstance(selection, slice):
        return np.arange(*selection.indices(length), dtype=np.int64)
    values = np.asarray(selection)
    if values.ndim == 0:
        values = values.reshape(1)
    if values.ndim != 1:
        raise IndexError("Selection must be one-dimensional")
    if values.size == 0:
        return np.empty(0, dtype=np.int64)
    if not np.issubdtype(values.dtype, np.integer):
        raise IndexError("Selection must contain integer indices")
    values = values.astype(np.int64, copy=True)
    values[values < 0] += length
    if np.any(values < 0) or np.any(values >= length):
        raise IndexError("Expression index out of bounds")
    return values


def _h5_member(group, name, *, field):
    if (
        not isinstance(group, h5py.Group)
        or not isinstance(name, str)
        or not name
        or name in {".", ".."}
        or "/" in name
        or "\x00" in name
    ):
        raise ValueError(f"Invalid AnnData {field} name")
    link = group.get(name, getlink=True)
    if link is None:
        raise ValueError(f"Requested AnnData {field} is unavailable")
    if not isinstance(link, h5py.HardLink):
        raise ValueError(f"AnnData {field} must use internal storage")
    node = group[name]
    if isinstance(node, h5py.Dataset):
        if node.external or node.is_virtual:
            raise ValueError(f"AnnData {field} must use internal storage")
        if (
            node.chunks
            and math.prod(node.chunks) * node.dtype.itemsize > _MAX_GENE_BLOCK_BYTES
        ):
            raise ValueError(f"AnnData {field} has a chunk exceeding the read budget")
    return node


def _h5ad_axis(handle, name, size):
    frame = _h5_member(handle, name, field=f"{name} axis")
    if not isinstance(frame, h5py.Group):
        raise ValueError("AnnData axes must be dataframe groups")
    if "_index" in frame.attrs and frame.attrs.get_id("_index").shape != ():
        raise ValueError("AnnData index name must be scalar")
    index = frame.attrs.get("_index", "_index")
    if isinstance(index, bytes):
        index = index.decode("utf-8")
    axis = _h5_member(frame, index, field=f"{name} index")
    if (
        not isinstance(axis, h5py.Dataset)
        or axis.shape != (size,)
        or not h5py.check_string_dtype(axis.dtype)
    ):
        raise ValueError("AnnData text indices must match the expression axes")
    if size * max(axis.dtype.itemsize, 32) > _MAX_AXIS_BYTES:
        raise ValueError("AnnData index exceeds the metadata budget")
    labels = []
    label_bytes = 0
    for start in range(0, size, 1024):
        block = axis.asstr()[start : start + 1024].tolist()
        label_bytes += sum(len(value.encode("utf-8")) for value in block)
        if label_bytes > _MAX_AXIS_BYTES:
            raise ValueError("AnnData index exceeds the decoded metadata budget")
        labels.extend(block)
    return _labels(labels, f"AnnData {name} labels")


class _H5adMatrix:
    """Read an observation-by-feature matrix in gene-by-sample order."""

    def __init__(self, matrix):
        self.matrix = matrix
        self.components = None
        if isinstance(matrix, h5py.Dataset):
            shape = matrix.shape
        else:
            if (
                "shape" not in matrix.attrs
                or matrix.attrs.get_id("shape").shape != (2,)
                or "encoding-type" not in matrix.attrs
                or matrix.attrs.get_id("encoding-type").shape != ()
            ):
                raise ValueError("Invalid AnnData sparse matrix header")
            shape = tuple(matrix.attrs["shape"])
        if (
            shape is None
            or len(shape) != 2
            or any(
                not isinstance(size, (int, np.integer)) or size < 1 for size in shape
            )
        ):
            raise ValueError(
                "AnnData expression must have a nonempty two-dimensional shape"
            )
        self.shape = (int(shape[1]), int(shape[0]))
        if isinstance(matrix, h5py.Dataset):
            self.dtype = matrix.dtype
        else:
            encoding = matrix.attrs["encoding-type"]
            if isinstance(encoding, bytes):
                encoding = encoding.decode("utf-8")
            if encoding not in {"csr_matrix", "csc_matrix"}:
                raise ValueError(
                    "AnnData expression supports dense, CSR, or CSC storage"
                )
            self.csr = encoding == "csr_matrix"
            self.components = self._sparse_components(matrix, shape)
            self.dtype = self.components[0].dtype
        if self.dtype.kind not in "biuf":
            raise ValueError("AnnData expression must be real numeric")

    def _sparse_components(self, matrix, shape):
        data, indices, pointers = (
            _h5_member(matrix, name, field=f"sparse {name}")
            for name in ("data", "indices", "indptr")
        )
        major = shape[0] if self.csr else shape[1]
        if (
            not all(
                isinstance(item, h5py.Dataset) for item in (data, indices, pointers)
            )
            or data.ndim != 1
            or indices.shape != data.shape
            or pointers.shape != (major + 1,)
            or indices.dtype.kind not in "iu"
            or pointers.dtype.kind not in "iu"
            or pointers[0] != 0
            or pointers[-1] != data.size
        ):
            raise ValueError("Invalid AnnData sparse component lengths or indices")
        return data, indices, pointers

    def read(self, rows, columns):
        if self.components is not None:
            return self._read_sparse(rows, columns)

        def selection(indices):
            start, stop = int(indices[0]), int(indices[-1]) + 1
            return slice(start, stop) if stop - start == len(indices) else indices

        sample_selection, gene_selection = selection(columns), selection(rows)
        if isinstance(sample_selection, slice) or isinstance(gene_selection, slice):
            return self.matrix[sample_selection, gene_selection].T
        # h5py allows only one fancy axis.
        return np.stack([self.matrix[int(sample), rows] for sample in columns], axis=1)

    def _read_sparse(self, rows, columns):
        data, indices, pointers = self.components
        outer, inner = (columns, rows) if self.csr else (rows, columns)
        minor = self.shape[0] if self.csr else self.shape[1]
        accumulator = (
            np.uint64
            if self.dtype.kind == "u"
            else (np.int64 if self.dtype.kind in "bi" else np.float64)
        )
        decoded = np.zeros(
            (len(outer), len(inner)), dtype=np.result_type(self.dtype, accumulator)
        )
        for index, major in enumerate(outer):
            start, stop = map(int, pointers[int(major) : int(major) + 2])
            if not 0 <= start <= stop <= data.size:
                raise ValueError("Invalid AnnData sparse pointers")
            for offset in range(start, stop, 65_536):
                end = min(offset + 65_536, stop)
                coordinates = indices[offset:end]
                if np.any(coordinates < 0) or np.any(coordinates >= minor):
                    raise ValueError("AnnData sparse index exceeds its declared axis")
                positions = np.searchsorted(inner, coordinates)
                selected = np.flatnonzero(positions < len(inner))
                selected = selected[inner[positions[selected]] == coordinates[selected]]
                np.add.at(
                    decoded[index], positions[selected], data[offset:end][selected]
                )
        return decoded.T if self.csr else decoded


def _tenx_component(directory, names):
    paths = [directory / (name + suffix) for name in names for suffix in ("", ".gz")]
    present = [path for path in paths if path.exists() or path.is_symlink()]
    if len(present) != 1 or present[0].is_symlink() or not present[0].is_file():
        names_text = "/".join(names)
        raise ValueError(f"10x MTX requires one regular {names_text} companion")
    return present[0]


def tenx_mtx_paths(path: PathLike) -> tuple[Path, Path, Path]:
    """Resolve the matrix, feature and barcode paths for a standard 10x set."""
    source = Path(path).expanduser()
    if source.is_symlink():
        raise ValueError("10x MTX inputs must be separate regular files without links")
    source = source.resolve()
    directory = source if source.is_dir() else source.parent
    matrix = _tenx_component(directory, ("matrix.mtx",)) if source.is_dir() else source
    if not matrix.name.lower().endswith(_MTX_SUFFIXES) or not matrix.is_file():
        raise ValueError("10x MTX requires a regular .mtx or .mtx.gz matrix file")
    return (
        matrix,
        _tenx_component(directory, ("features.tsv", "genes.tsv")),
        _tenx_component(directory, ("barcodes.tsv",)),
    )


@contextmanager
def _tenx_lines(path, *, limit):
    def lines(stream):
        read_bytes = 0
        while raw := stream.readline(_MAX_MTX_LINE_BYTES + 1):
            read_bytes += len(raw)
            if len(raw) > _MAX_MTX_LINE_BYTES or read_bytes > limit:
                raise ValueError("10x MTX text exceeds the decoded read budget")
            yield raw.decode("utf-8").rstrip("\r\n")

    with _open_regular(path) as source:
        if path.suffix.lower() == ".gz":
            with gzip.GzipFile(fileobj=source) as stream:
                yield lines(stream)
        else:
            yield lines(source)


def _exact_lines(lines, expected, name):
    seen = 0
    for seen, line in enumerate(lines, 1):
        if seen > expected:
            break
        yield line
    if seen != expected:
        raise ValueError(f"10x {name} count does not match the matrix header")


def _read_tenx_mtx(path):
    from scipy import sparse

    matrix, features, barcodes = tenx_mtx_paths(path)
    with _tenx_lines(matrix, limit=256 * 1024 * 1024) as source:
        header = next(source, "").lower().split()
        if (
            len(header) != 5
            or header[:3] != ["%%matrixmarket", "matrix", "coordinate"]
            or header[3] not in {"integer", "real"}
            or header[4] != "general"
        ):
            raise ValueError("Expected a general coordinate Matrix Market count matrix")
        entries = (
            line
            for line in source
            if line.strip() and not line.lstrip().startswith("%")
        )
        genes_in_file, samples_in_file, count = map(int, next(entries, "").split())
        shape = (genes_in_file, samples_in_file)
        if not all(1 <= size <= _MAX_AXIS_BYTES // 64 for size in shape) or count < 0:
            raise ValueError("10x dimensions exceed the axis or sparse read budget")
        if 64 * count + 8 * (genes_in_file + samples_in_file) > _MAX_READ_BYTES:
            raise ValueError("10x MTX sparse allocation exceeds the read budget")
        genes, gene_positions = [], []
        with _tenx_lines(features, limit=_MAX_AXIS_BYTES) as feature_source:
            for index, line in enumerate(
                _exact_lines(feature_source, genes_in_file, "feature")
            ):
                field = line.split("\t", 3)
                if len(field) < 3 or field[2] == "Gene Expression":
                    genes.append(field[0])
                    gene_positions.append(index)
        genes = _labels(genes, "10x RNA feature IDs")
        with _tenx_lines(barcodes, limit=_MAX_AXIS_BYTES) as barcode_source:
            samples = _labels(
                _exact_lines(barcode_source, samples_in_file, "barcode"), "10x barcodes"
            )
        coordinates = np.empty((2, count), dtype=np.int64)
        values = np.empty(count, dtype=float)
        for index, line in enumerate(_exact_lines(entries, count, "matrix entry")):
            gene, sample, value = line.split()
            row, column = int(gene) - 1, int(sample) - 1
            count_value = int(value) if header[3] == "integer" else float(value)
            if (
                not 0 <= row < genes_in_file
                or not 0 <= column < samples_in_file
                or not 0 <= count_value <= 2**53 - 1
                or count_value != int(count_value)
            ):
                raise ValueError("Invalid 10x count entry")
            coordinates[:, index] = (row, column)
            values[index] = count_value
    expression = sparse.coo_matrix((values, coordinates), shape=shape).tocsr()[
        gene_positions
    ]
    if expression.data.size and expression.data.max() > 2**53 - 1:
        raise ValueError("Summed 10x counts exceed the exact integer range")
    if header[3] == "integer":
        expression = expression.astype(np.int64)
    return expression, genes, samples


class ChunkedExpressionStore:
    """Open an expression store. Matrices always have genes on the first axis."""

    def __init__(self, path: PathLike, *, layer: str | None = None) -> None:
        self.path = Path(path).expanduser().resolve()
        self._closed = False
        self._handle = None
        self._array = None
        self._stream = None
        suffix = self.path.suffix.lower()
        if layer is not None and suffix != ".h5ad":
            raise ValueError("Named expression layers require an AnnData source")
        if suffix == ".zarr":
            self.format = "zarr"
            self._handle = zarr.open_group(str(self.path), mode="r")
            self._array = self._handle["expression"]
            self.gene_names = _labels(self._handle["gene_names"][:], "Gene labels")
            self.sample_names = _labels(self._handle["sample_names"][:], "Sample labels")
        elif suffix in (".h5", ".hdf5"):
            self.format = "hdf5"
            self._handle = h5py.File(str(self.path), mode="r")
            loaded = False
            try:
                self._array = self._handle["expression"]
                self.gene_names = _labels(self._handle["gene_names"].asstr()[:], "Gene labels")
                self.sample_names = _labels(self._handle["sample_names"].asstr()[:], "Sample labels")
                loaded = True
            finally:
                if not loaded:
                    self._handle.close()
        elif suffix == ".h5ad":
            self.format = "h5ad"
            self._open_h5ad(layer)
        elif suffix in {".csv", ".tsv"}:
            self.format = suffix[1:]
            self._separator = "\t" if suffix == ".tsv" else ","
            with self.path.open("r", encoding="utf-8", newline="") as handle:
                header = next(csv.reader(handle, delimiter=self._separator), [])
            self.sample_names = _labels(header[1:], "Sample labels")
            names: List[str] = []
            for chunk in pd.read_csv(
                self.path, sep=self._separator, usecols=[0], dtype=str,
                keep_default_na=False, chunksize=5000,
            ):
                names.extend(chunk.iloc[:, 0].tolist())
            self.gene_names = _labels(names, "Gene labels")
        elif self.path.name.lower().endswith(_MTX_SUFFIXES) or self.path.is_dir():
            self.format = "10x_mtx"
            self._array, self.gene_names, self.sample_names = _read_tenx_mtx(path)
        else:
            formats = "Zarr, HDF5, H5AD, CSV, TSV, or 10x MTX"
            raise ValueError("Unsupported expression format. Use " + formats)
        self.shape = (len(self.gene_names), len(self.sample_names))
        if self._array is not None and tuple(self._array.shape) != self.shape:
            self.close()
            raise ValueError("Expression dimensions do not match gene and sample labels")
        self._gene_lookup = {name: index for index, name in enumerate(self.gene_names)}

    def _open_h5ad(self, layer: str | None) -> None:
        self._stream = _open_regular(self.path)
        try:
            self._handle = h5py.File(self._stream, mode="r")
            if layer is None:
                element = _h5_member(self._handle, "X", field="matrix")
            else:
                layers = _h5_member(self._handle, "layers", field="layers")
                element = _h5_member(layers, layer, field="layer")
            self._array = _H5adMatrix(element)
            genes, samples = self._array.shape
            self.gene_names = _h5ad_axis(self._handle, "var", genes)
            self.sample_names = _h5ad_axis(self._handle, "obs", samples)
        except BaseException:
            self.close()
            raise

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("Expression store is closed")

    def get_chunk(self, gene_indices: Any, sample_indices: Any) -> np.ndarray:
        """Read a Cartesian selection while retaining order and duplicate indices."""
        self._ensure_open()
        rows = _indices(gene_indices, self.shape[0])
        columns = _indices(sample_indices, self.shape[1])
        if rows.size == 0 or columns.size == 0:
            return np.empty((len(rows), len(columns)), dtype=float)
        if self.format in {"h5ad", "10x_mtx", "tsv"}:
            itemsize = self._array.dtype.itemsize if self._array is not None else 8
            dtype_bytes = max(8, itemsize)
            if len(rows) * len(columns) * dtype_bytes > _MAX_READ_BYTES:
                raise ValueError(
                    "Expression selection exceeds the read budget. Use gene blocks."
                )
        unique_columns, column_inverse = np.unique(columns, return_inverse=True)
        unique_rows, row_inverse = np.unique(rows, return_inverse=True)
        if self.format == "zarr":
            assert self._array is not None
            return np.asarray(self._array.oindex[rows, columns])
        if self.format == "hdf5":
            assert self._array is not None
            values = np.empty((len(unique_rows), len(unique_columns)), dtype=self._array.dtype)
            for position, row in enumerate(unique_rows):
                values[position] = self._array[int(row), unique_columns]
        elif self.format == "h5ad":
            assert self._array is not None
            values = self._array.read(unique_rows, unique_columns)
        elif self.format == "10x_mtx":
            assert self._array is not None
            values = self._array[unique_rows][:, unique_columns].toarray()
        else:
            values = self._read_table_chunk(unique_rows, unique_columns)
        return values[np.ix_(row_inverse, column_inverse)]

    def _read_table_chunk(self, rows: np.ndarray, columns: np.ndarray) -> np.ndarray:
        use_columns = (columns + 1).tolist()
        chunk_size = _chunk_rows(500, len(columns))
        values = np.empty((len(rows), len(columns)), dtype=float)
        offset = 0
        for frame in pd.read_csv(
            self.path, sep=self._separator, usecols=use_columns, chunksize=chunk_size,
        ):
            next_offset = offset + len(frame)
            positions = np.flatnonzero((rows >= offset) & (rows < next_offset))
            if len(positions):
                values[positions] = frame.iloc[rows[positions] - offset].to_numpy(
                    dtype=float,
                )
            offset = next_offset
            if offset > rows[-1]:
                return values
        raise ValueError(
            f"{self.format.upper()} row count changed while the store was open"
        )

    def get_gene_vector(self, gene_name: str) -> np.ndarray:
        """Read one named gene, preserving sample order."""
        try:
            index = self._gene_lookup[str(gene_name)]
        except KeyError:
            raise KeyError("Unknown gene: " + str(gene_name)) from None
        return self.get_chunk([index], slice(None))[0]

    def iter_gene_chunks(self, chunk_size: int = 500, *, sample_indices: Any = None
                         ) -> Iterator[Tuple[List[str], np.ndarray]]:
        """Yield bounded gene blocks, optionally reading only the selected sample columns."""
        self._ensure_open()
        if not isinstance(chunk_size, int) or chunk_size < 1:
            raise ValueError("chunk_size must be a positive integer")
        columns = _indices(sample_indices, self.shape[1]) if sample_indices is not None else None
        if columns is not None and not columns.size:
            for start in range(0, self.shape[0], chunk_size):
                names = self.gene_names[start:start + chunk_size]
                yield names, np.empty((len(names), 0), dtype=float)
            return
        if self.format in {"csv", "tsv"}:
            yield from self._iter_table_chunks(chunk_size, columns)
        elif self.format in {"h5ad", "10x_mtx"}:
            width = self.shape[1] if columns is None else len(columns)
            selected_samples = slice(None) if columns is None else columns
            assert self._array is not None
            itemsize = max(8, self._array.dtype.itemsize)
            chunk_size = _chunk_rows(chunk_size, width, itemsize=itemsize)
            for start in range(0, self.shape[0], chunk_size):
                stop = min(start + chunk_size, self.shape[0])
                block = self.get_chunk(slice(start, stop), selected_samples)
                yield self.gene_names[start:stop], block
        else:
            assert self._array is not None
            for start in range(0, self.shape[0], chunk_size):
                stop = min(start + chunk_size, self.shape[0])
                block = (np.asarray(self._array[start:stop, :]) if columns is None
                         else self.get_chunk(slice(start, stop), columns))
                yield self.gene_names[start:stop], block

    def _iter_table_chunks(
        self, chunk_size: int, columns: np.ndarray | None,
    ) -> Iterator[tuple[list[str], np.ndarray]]:
        offset = 0
        if columns is None:
            use_columns, inverse = list(range(1, self.shape[1] + 1)), None
        else:
            unique_columns, inverse = np.unique(columns, return_inverse=True)
            use_columns = (unique_columns + 1).tolist()
        width = len(use_columns) if columns is None else len(columns)
        for frame in pd.read_csv(
            self.path, sep=self._separator, usecols=use_columns,
            chunksize=_chunk_rows(chunk_size, width),
        ):
            block = frame.to_numpy(dtype=float)
            stop = offset + len(frame)
            block = block if inverse is None else block[:, inverse]
            yield self.gene_names[offset:stop], block
            offset = stop
        if offset != self.shape[0]:
            raise ValueError(
                f"{self.format.upper()} row count changed while the store was open"
            )

    @staticmethod
    def _validate_dataframe(df: pd.DataFrame) -> Tuple[List[str], List[str]]:
        genes = _labels(df.index, "Gene labels")
        samples = _labels(df.columns, "Sample labels")
        if not all(pd.api.types.is_numeric_dtype(dtype) for dtype in df.dtypes):
            raise ValueError("Expression values must be numeric")
        return genes, samples

    @classmethod
    def from_dataframe_to_zarr(cls, df: pd.DataFrame, zarr_path: PathLike,
                               chunk_shape: Tuple[int, int] = (500, 50)) -> "ChunkedExpressionStore":
        """Write compressed Zarr v2 in blocks using an explicit numcodecs codec."""
        genes, samples = cls._validate_dataframe(df)
        if len(chunk_shape) != 2 or any(not isinstance(size, int) or size < 1 for size in chunk_shape):
            raise ValueError("chunk_shape must contain two positive integers")
        path = Path(zarr_path).expanduser().resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        compressor = Blosc(cname="zstd", clevel=3, shuffle=Blosc.BITSHUFFLE)
        group = zarr.open_group(str(path), mode="w", zarr_version=2)
        array = group.create_dataset("expression", shape=df.shape, chunks=chunk_shape,
                                     dtype=np.result_type(*df.dtypes), compressor=compressor)
        group.create_dataset("gene_names", data=np.asarray(genes, dtype=str), compressor=compressor)
        group.create_dataset("sample_names", data=np.asarray(samples, dtype=str), compressor=compressor)
        for start in range(0, len(df), chunk_shape[0]):
            array[start:start + chunk_shape[0], :] = df.iloc[start:start + chunk_shape[0]].to_numpy()
        return cls(path)

    @classmethod
    def from_dataframe_to_hdf5(cls, df: pd.DataFrame, hdf5_path: PathLike,
                               chunk_shape: Tuple[int, int] = (500, 50)) -> "ChunkedExpressionStore":
        """Write portable gzip-compressed HDF5 in bounded gene blocks."""
        genes, samples = cls._validate_dataframe(df)
        if len(chunk_shape) != 2 or any(not isinstance(size, int) or size < 1 for size in chunk_shape):
            raise ValueError("chunk_shape must contain two positive integers")
        path = Path(hdf5_path).expanduser().resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        chunks = tuple(min(size, axis) for size, axis in zip(chunk_shape, df.shape))
        with h5py.File(str(path), "w") as handle:
            array = handle.create_dataset("expression", shape=df.shape, chunks=chunks,
                                           dtype=np.result_type(*df.dtypes), compression="gzip", compression_opts=3)
            handle.create_dataset("gene_names", data=genes, dtype=h5py.string_dtype("utf-8"))
            handle.create_dataset("sample_names", data=samples, dtype=h5py.string_dtype("utf-8"))
            for start in range(0, len(df), chunks[0]):
                array[start:start + chunks[0], :] = df.iloc[start:start + chunks[0]].to_numpy()
        return cls(path)

    @classmethod
    def from_dataframe_to_csv(cls, df: pd.DataFrame, csv_path: PathLike) -> "ChunkedExpressionStore":
        """Write an indexed gene-by-sample CSV with bounded formatter chunks."""
        cls._validate_dataframe(df)
        path = Path(csv_path).expanduser().resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(path, index=True, index_label="gene_id", chunksize=500)
        return cls(path)

    def close(self) -> None:
        try:
            if self.format in {"hdf5", "h5ad"} and self._handle is not None:
                self._handle.close()
        finally:
            if self._stream is not None:
                self._stream.close()
            self._closed = True

    def __enter__(self) -> "ChunkedExpressionStore":
        self._ensure_open()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()
