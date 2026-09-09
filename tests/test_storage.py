"""Behavioral tests for expression storage across supported formats."""

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


def test_rejects_ambiguous_axes(tmp_path, expression):
    expression.index = ["same"] * len(expression)
    with pytest.raises(ValueError, match="unique"):
        ChunkedExpressionStore.from_dataframe_to_zarr(expression, tmp_path / "bad.zarr")


def test_unknown_format_is_explicit(tmp_path):
    with pytest.raises(ValueError, match="format"):
        ChunkedExpressionStore(tmp_path / "data.npz")
