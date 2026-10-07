"""Bounded assembly must retain count identities and the existing normalization."""
import weakref

import anndata as ad
import numpy as np
import pytest
from scipy import sparse

pytest.importorskip('scanpy')

from devbench.analysis.checkpoints import CheckpointStore, sha256
from devbench.analysis.streaming import read_table, stream_hvg, stream_qc, stream_selected
from tests.test_analysis_streaming import fixture


def prepare(tmp_path):
    path = tmp_path/'input.h5ad'
    original = fixture(path)
    store = CheckpointStore(tmp_path/'checkpoints', {'input': sha256(path)})
    stream_qc(path, store, layer=None, batch_key='batch', block_rows=7,
              min_genes=3, mad_limit=None)
    stream_hvg(path, store, layer=None, batch_key='batch', block_rows=7, n_top_genes=40)
    return path, original, store


def test_selected_assembly_does_not_retain_all_loaded_chunks(tmp_path, monkeypatch):
    path, _, store = prepare(tmp_path)
    references = []
    peak_live = 0
    load = sparse.load_npz

    def observed_load(filename):
        nonlocal peak_live
        block = load(filename)
        references.append(weakref.ref(block))
        peak_live = max(peak_live, sum(ref() is not None for ref in references))
        return block

    monkeypatch.setattr(sparse, 'load_npz', observed_load)
    stream_selected(path, store, layer=None, block_rows=7)
    assert peak_live <= 2, f'Assembly retained {peak_live} input chunks simultaneously'


def test_selected_assembly_preserves_existing_float32_normalization_exactly(tmp_path):
    path, original, store = prepare(tmp_path)
    stream_selected(path, store, layer=None, block_rows=7)
    result = ad.read_h5ad(store.path('selected')/'selected.h5ad')
    metrics = read_table(store.path('qc')/'metrics.csv')
    hvg = read_table(store.path('hvg')/'features.csv')
    rows = metrics.qc_pass.to_numpy(bool)
    columns = hvg.highly_variable.to_numpy(bool)
    counts = original.X[rows][:, columns].astype(np.float32)
    expected = counts.multiply((1e4/metrics.loc[rows, 'total_counts'].to_numpy())[:, None]).tocsr().astype(np.float32)
    expected.data = np.log1p(expected.data)
    assert (result.layers['counts'] != counts).nnz == 0
    np.testing.assert_array_equal(result.obs_names, original.obs_names[rows])
    np.testing.assert_array_equal(result.var_names, original.var_names[columns])
    np.testing.assert_array_equal(result.X.data, expected.data)
    np.testing.assert_array_equal(result.X.indices, expected.indices)
    np.testing.assert_array_equal(result.X.indptr, expected.indptr)


@pytest.mark.parametrize('block_rows', [1, 2, 3, 8])
def test_block_normalization_keeps_empty_rows_duplicates_and_large_exact_counts(block_rows):
    from devbench.analysis.streaming import _normalize_selected_counts
    counts = sparse.csr_matrix((np.array([2**24, 1, 3, 5, 0, 2], dtype=np.float32),
        np.array([3, 1, 2, 2, 4, 0]), np.array([0, 2, 2, 5, 6])), shape=(4, 5))
    before = counts.copy()
    totals = np.array([2**24+5, 9, 15, 4], dtype=np.float64)
    expected = counts.multiply((1e4/totals)[:, None]).tocsr().astype(np.float32)
    expected.data = np.log1p(expected.data)
    result = _normalize_selected_counts(counts, totals, block_rows)
    np.testing.assert_array_equal(result.data, expected.data)
    np.testing.assert_array_equal(result.indices, expected.indices)
    np.testing.assert_array_equal(result.indptr, expected.indptr)
    np.testing.assert_array_equal(counts.data, before.data)
    np.testing.assert_array_equal(counts.indices, before.indices)
