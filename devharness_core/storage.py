"""Gene-by-sample storage with bounded reads from Zarr, HDF5, and CSV.

CSV scanning is streaming but repeated random reads are linear in file length;
Zarr or HDF5 is preferable for repeated analysis. DataFrame writers do not make
an additional whole-matrix NumPy copy. All filesystem inputs are resolved once.
"""

import csv
from pathlib import Path
from typing import Any, Iterator, List, Tuple, Union

import h5py
import numpy as np
import pandas as pd
import zarr
from numcodecs import Blosc

PathLike = Union[str, Path]


def _labels(values: Any, name: str) -> List[str]:
    labels = [str(value) for value in values]
    if not labels or any(not value for value in labels):
        raise ValueError(name + " must be nonempty")
    if len(set(labels)) != len(labels):
        raise ValueError(name + " must be unique")
    return labels


def _indices(selection: Any, length: int) -> np.ndarray:
    if isinstance(selection, slice):
        return np.arange(length, dtype=np.int64)[selection]
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


class ChunkedExpressionStore:
    """Open an expression store. Matrices always have genes on the first axis."""

    def __init__(self, path: PathLike) -> None:
        self.path = Path(path).expanduser().resolve()
        self._closed = False
        self._handle = None
        self._array = None
        suffix = self.path.suffix.lower()
        if suffix == ".zarr":
            self.format = "zarr"
            self._handle = zarr.open_group(str(self.path), mode="r")
            self._array = self._handle["expression"]
            self.gene_names = _labels(self._handle["gene_names"][:], "Gene labels")
            self.sample_names = _labels(self._handle["sample_names"][:], "Sample labels")
        elif suffix in (".h5", ".hdf5"):
            self.format = "hdf5"
            self._handle = h5py.File(str(self.path), mode="r")
            try:
                self._array = self._handle["expression"]
                self.gene_names = _labels(self._handle["gene_names"].asstr()[:], "Gene labels")
                self.sample_names = _labels(self._handle["sample_names"].asstr()[:], "Sample labels")
            except BaseException:
                self._handle.close()
                raise
        elif suffix == ".csv":
            self.format = "csv"
            with self.path.open("r", encoding="utf-8", newline="") as handle:
                header = next(csv.reader(handle), [])
            self.sample_names = _labels(header[1:], "Sample labels")
            names: List[str] = []
            for chunk in pd.read_csv(self.path, usecols=[0], dtype=str,
                                     keep_default_na=False, chunksize=5000):
                names.extend(chunk.iloc[:, 0].tolist())
            self.gene_names = _labels(names, "Gene labels")
        else:
            raise ValueError("Unsupported expression format; use .zarr, .h5, .hdf5, or .csv")
        self.shape = (len(self.gene_names), len(self.sample_names))
        if self._array is not None and tuple(self._array.shape) != self.shape:
            self.close()
            raise ValueError("Expression dimensions do not match gene and sample labels")
        self._gene_lookup = {name: index for index, name in enumerate(self.gene_names)}

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
        unique_columns, column_inverse = np.unique(columns, return_inverse=True)
        unique_rows, row_inverse = np.unique(rows, return_inverse=True)
        if self.format == "zarr":
            return np.asarray(self._array.oindex[rows, columns])
        if self.format == "hdf5":
            values = np.empty((len(unique_rows), len(unique_columns)), dtype=self._array.dtype)
            for position, row in enumerate(unique_rows):
                values[position] = self._array[int(row), unique_columns]
        else:
            values = np.empty((len(unique_rows), len(unique_columns)), dtype=float)
            offset = 0
            use_columns = [int(column) + 1 for column in unique_columns]
            for frame in pd.read_csv(self.path, usecols=use_columns, chunksize=500):
                positions = np.flatnonzero((unique_rows >= offset) & (unique_rows < offset + len(frame)))
                if len(positions):
                    values[positions] = frame.iloc[unique_rows[positions] - offset].to_numpy(dtype=float)
                offset += len(frame)
                if offset > unique_rows[-1]:
                    break
            if offset <= unique_rows[-1]:
                raise ValueError("CSV row count changed while the store was open")
        return values[row_inverse][:, column_inverse]

    def get_gene_vector(self, gene_name: str) -> np.ndarray:
        """Read one named gene, preserving sample order."""
        try:
            index = self._gene_lookup[str(gene_name)]
        except KeyError:
            raise KeyError("Unknown gene: " + str(gene_name)) from None
        return self.get_chunk([index], slice(None))[0]

    def iter_gene_chunks(self, chunk_size: int = 500) -> Iterator[Tuple[List[str], np.ndarray]]:
        """Yield bounded gene blocks; CSV is scanned once per complete iteration."""
        self._ensure_open()
        if not isinstance(chunk_size, int) or chunk_size < 1:
            raise ValueError("chunk_size must be a positive integer")
        if self.format == "csv":
            offset = 0
            for frame in pd.read_csv(self.path, usecols=list(range(1, self.shape[1] + 1)),
                                     chunksize=chunk_size):
                yield self.gene_names[offset:offset + len(frame)], frame.to_numpy(dtype=float)
                offset += len(frame)
        else:
            for start in range(0, self.shape[0], chunk_size):
                stop = min(start + chunk_size, self.shape[0])
                yield self.gene_names[start:stop], np.asarray(self._array[start:stop, :])

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
        if self.format == "hdf5" and self._handle is not None:
            self._handle.close()
        self._closed = True

    def __enter__(self) -> "ChunkedExpressionStore":
        self._ensure_open()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()
