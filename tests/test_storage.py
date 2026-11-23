"""Behavioral tests for expression storage across supported formats."""

import gzip

import h5py
import numpy as np
import pandas as pd
import pytest
import zarr
from numcodecs import Blosc

from devharness_core.storage import ChunkedExpressionStore


@pytest.fixture
def expression():
    return pd.DataFrame(np.arange(30, dtype=float).reshape(6, 5),
                        index=["Sox2", "Pax6", "Neurog2", "Tbr1", "g4", "g5"],
                        columns=["s0", "s1", "s2", "s3", "s4"])


@pytest.mark.parametrize("suffix,writer", [
    (".zarr", "from_dataframe_to_zarr"),
    (".h5", "from_dataframe_to_hdf5"),
    (".csv", "from_dataframe_to_csv"),
])
def test_roundtrip_selection_and_streaming(tmp_path, expression, suffix, writer):
    path = tmp_path / ("counts" + suffix)
    with getattr(ChunkedExpressionStore, writer)(expression, path) as store:
        assert store.shape == expression.shape
        assert store.gene_names == list(expression.index)
        assert store.sample_names == list(expression.columns)
        np.testing.assert_equal(store.get_chunk([4, 0, 4], [3, 1, 3]),
                                expression.to_numpy()[np.ix_([4, 0, 4], [3, 1, 3])])
        np.testing.assert_equal(store.get_chunk(slice(None, None, -1), slice(1, 4)),
                                expression.iloc[::-1, 1:4].to_numpy())
        np.testing.assert_equal(store.get_gene_vector("Pax6"), expression.loc["Pax6"])
        chunks = list(store.iter_gene_chunks(chunk_size=2))
        assert [gene for genes, _ in chunks for gene in genes] == list(expression.index)
        np.testing.assert_equal(np.concatenate([values for _, values in chunks]), expression)
        selected = list(store.iter_gene_chunks(chunk_size=2, sample_indices=[3, 1, 3]))
        assert [gene for genes, _ in selected for gene in genes] == list(expression.index)
        np.testing.assert_equal(np.concatenate([values for _, values in selected]), expression.iloc[:, [3, 1, 3]])
        empty = list(store.iter_gene_chunks(chunk_size=2, sample_indices=[]))
        assert np.concatenate([values for _, values in empty]).shape == (len(expression), 0)
        assert store.get_chunk([], [0]).shape == (0, 1)
        assert store.get_chunk([0], []).shape == (1, 0)
        with pytest.raises(KeyError):
            store.get_gene_vector("missing")
        with pytest.raises(IndexError):
            store.get_chunk([100], [0])
    with pytest.raises(RuntimeError, match="closed"):
        store.get_chunk([0], [0])
    with ChunkedExpressionStore(path) as reopened:
        np.testing.assert_equal(reopened.get_chunk([-1], [-1]), expression.iloc[[-1], [-1]])


def test_zarr_uses_requested_chunks_and_blosc(tmp_path, expression):
    path = tmp_path / "counts.zarr"
    ChunkedExpressionStore.from_dataframe_to_zarr(expression, path, (2, 3)).close()
    group = zarr.open_group(str(path), mode="r")
    array = group["expression"]
    assert array.chunks == (2, 3)
    assert isinstance(array.compressor, Blosc)
    assert array.compressor.cname == "zstd"
    assert array.compressor.shuffle == Blosc.BITSHUFFLE


def test_csv_keeps_numeric_gene_ids_and_quoted_labels(tmp_path):
    frame = pd.DataFrame([[1, 2], [3, 4]], index=["0001", "a,b"], columns=["s,1", "s2"])
    with ChunkedExpressionStore.from_dataframe_to_csv(frame, tmp_path / "data.csv") as store:
        assert store.gene_names == ["0001", "a,b"]
        np.testing.assert_equal(store.get_gene_vector("0001"), [1, 2])


def test_wide_csv_parser_bounds_numeric_block_by_selected_width(tmp_path, monkeypatch):
    width = 30_000
    values = np.arange(3 * width).reshape(3, width)
    frame = pd.DataFrame(values, columns=[f"s{i}" for i in range(width)])
    path = tmp_path / "wide.csv"
    frame.to_csv(path)
    read_csv = pd.read_csv
    parser_bytes = []

    def observed_read(*args, **kwargs):
        columns = kwargs["usecols"]
        if len(columns) > 1:
            parser_bytes.append(len(columns) * kwargs["chunksize"] * np.dtype(float).itemsize)
        return read_csv(*args, **kwargs)

    monkeypatch.setattr(pd, "read_csv", observed_read)
    with ChunkedExpressionStore(path) as store:
        selected = np.arange(width - 1, 0, -1)
        blocks = list(store.iter_gene_chunks(sample_indices=selected))
        np.testing.assert_array_equal(np.concatenate([block for _, block in blocks]), values[:, selected])
        np.testing.assert_array_equal(store.get_chunk([2], selected), values[[2]][:, selected])
    assert len(parser_bytes) == 2
    assert max(parser_bytes) <= 16 * 1024 * 1024


def test_rejects_ambiguous_axes(tmp_path, expression):
    expression.index = ["same"] * len(expression)
    with pytest.raises(ValueError, match="unique"):
        ChunkedExpressionStore.from_dataframe_to_zarr(expression, tmp_path / "bad.zarr")


def test_unknown_format_is_explicit(tmp_path):
    with pytest.raises(ValueError, match="format"):
        ChunkedExpressionStore(tmp_path / "data.npz")


def test_tsv_keeps_exact_labels_and_source_dtype(tmp_path):
    from rna_kernels.normalization import expression_summary

    path = tmp_path / "expression.tsv"
    path.write_text('gene_id\t"s\t1"\ts2\n0001\t1\t2\n"g\t2"\t0\t3\n')
    with ChunkedExpressionStore(path) as store:
        assert store.gene_names == ["0001", "g\t2"]
        assert store.sample_names == ["s\t1", "s2"]
        np.testing.assert_array_equal(
            store.get_chunk([1, 0, 1], [1, 0]), [[3, 0], [2, 1], [3, 0]]
        )
        np.testing.assert_array_equal(
            np.concatenate([block for _, block in store.iter_gene_chunks(1)]),
            [[1, 2], [0, 3]],
        )
        assert expression_summary(store)["data_type"] == "counts"


def _h5ad_axes(handle, genes, samples):
    for axis, names in (("obs", samples), ("var", genes)):
        frame = handle.create_group(axis)
        frame.attrs["_index"] = "identifiers"
        frame.create_dataset(
            "identifiers", data=names, dtype=h5py.string_dtype("utf-8")
        )


@pytest.mark.parametrize("encoding", ["dense", "csr_matrix", "csc_matrix"])
def test_h5ad_preserves_gene_first_axes_and_requested_layer(
    tmp_path, expression, encoding
):
    from scipy import sparse

    path = tmp_path / "expression.h5ad"
    matrix = expression.to_numpy().astype(np.int32)
    matrix[matrix % 3 == 0] = 0
    with h5py.File(path, "w") as handle:
        _h5ad_axes(handle, list(expression.index), list(expression.columns))
        handle["X"] = np.zeros(matrix.T.shape)
        layers = handle.create_group("layers")
        if encoding == "dense":
            layers["counts"] = matrix.T
        else:
            packed = getattr(sparse, encoding)(matrix.T)
            counts = layers.create_group("counts")
            counts.attrs["encoding-type"] = encoding
            counts.attrs["shape"] = packed.shape
            for name in ("data", "indices", "indptr"):
                counts[name] = getattr(packed, name)

    with ChunkedExpressionStore(path) as root:
        np.testing.assert_array_equal(root.get_chunk([1], [1]), [[0]])
    with ChunkedExpressionStore(path, layer="counts") as store:
        genes, samples = [4, 0, 4], [3, 1, 3]
        assert store.gene_names == list(expression.index)
        assert store.sample_names == list(expression.columns)
        np.testing.assert_array_equal(
            store.get_chunk(genes, samples), matrix[np.ix_(genes, samples)]
        )
        np.testing.assert_array_equal(
            np.concatenate([block for _, block in store.iter_gene_chunks(2)]),
            matrix,
        )
        with pytest.raises(ValueError, match="layer"):
            ChunkedExpressionStore(path, layer="missing")
    with pytest.raises(RuntimeError, match="closed"):
        store.get_chunk([0], [0])


def test_h5ad_streams_large_dense_shape_in_bounded_blocks(tmp_path):
    path = tmp_path / "wide.h5ad"
    genes, samples = 16384, 1024
    with h5py.File(path, "w") as handle:
        _h5ad_axes(
            handle, [f"g{i}" for i in range(genes)], [f"s{i}" for i in range(samples)]
        )
        matrix = handle.create_dataset("X", (samples, genes), dtype="f8", fillvalue=0)
        matrix[-1, -1] = 4

    with ChunkedExpressionStore(path) as store:
        assert store.shape == (genes, samples)
        np.testing.assert_array_equal(store.get_chunk([-1], [-1]), [[4]])
        names, block = next(store.iter_gene_chunks(genes))
        assert names[0] == "g0" and block.shape[1] == samples
        assert block.nbytes <= 16 * 1024 * 1024
        with pytest.raises(ValueError, match="read budget"):
            store.get_chunk(slice(None), slice(None))


def test_h5ad_requires_internal_matrix_storage(tmp_path):
    matrix_path = tmp_path / "other.h5"
    source_path = tmp_path / "expression.h5ad"
    with h5py.File(matrix_path, "w") as handle:
        handle["matrix"] = np.ones((1, 1))
    with h5py.File(source_path, "w") as handle:
        _h5ad_axes(handle, ["feature"], ["sample"])
        handle["X"] = h5py.ExternalLink(str(matrix_path), "/matrix")
    with pytest.raises(ValueError, match="internal"):
        ChunkedExpressionStore(source_path)


@pytest.mark.parametrize("compressed", [False, True])
def test_10x_mtx_retains_feature_ids_and_barcode_order(tmp_path, compressed):
    from devharness_core.storage import tenx_mtx_paths

    def write(name, body):
        filename = tmp_path / (name + (".gz" if compressed else ""))
        filename.write_bytes(
            gzip.compress(body.encode()) if compressed else body.encode()
        )
        return filename

    matrix = write(
        "matrix.mtx",
        "%%MatrixMarket matrix coordinate integer general\n"
        "% synthetic example\n4 3 5\n1 3 2\n2 2 4\n3 1 8\n4 1 3\n1 3 1\n",
    )
    features = write(
        "features.tsv",
        "id3\tRepeated\tGene Expression\n"
        "id1\tRepeated\tGene Expression\nid2\tOther\tAntibody Capture\n"
        "id4\tFourth\tGene Expression\n",
    )
    barcodes = write("barcodes.tsv", "sample3\nsample1\nsample2\n")
    assert tenx_mtx_paths(tmp_path) == (matrix, features, barcodes)
    assert tenx_mtx_paths(matrix) == (matrix, features, barcodes)
    with ChunkedExpressionStore(tmp_path if compressed else matrix) as store:
        assert store.gene_names == ["id3", "id1", "id4"]
        assert store.sample_names == ["sample3", "sample1", "sample2"]
        np.testing.assert_array_equal(
            store.get_chunk([2, 0, 2], [2, 0]), [[0, 3], [3, 0], [0, 3]]
        )
        np.testing.assert_array_equal(
            np.concatenate([block for _, block in store.iter_gene_chunks(2)]),
            [[0, 0, 3], [0, 4, 0], [3, 0, 0]],
        )
