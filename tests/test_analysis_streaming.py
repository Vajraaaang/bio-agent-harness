import json

import anndata as ad
import numpy as np
import pandas as pd
import pytest
from scipy import sparse

pytest.importorskip("scanpy")

from devbench.analysis.checkpoints import CheckpointStore, sha256
from devbench.analysis.streaming import read_table, stream_qc, stream_hvg, stream_selected


def fixture(path, *, dense=False):
    rng = np.random.default_rng(61821)
    values = rng.negative_binomial(2, .6, (96, 130)).astype(float)
    values[:48, 20:30] += 10
    values[:, -1] = 0
    values[:48, -2] = 0
    data = ad.AnnData(values if dense else sparse.csr_matrix(values),
        obs=pd.DataFrame({"batch": ["a"]*48+["b"]*48,
                          "celltypes": ["do_not_use"]*96, "conf_score": .8},
                         index=[f"{i:04d}" for i in range(96)]),
        var=pd.DataFrame(index=["mt-Co1"]+[f"Gene{i:04d}" for i in range(129)]))
    data.write_h5ad(path)
    return data


@pytest.mark.parametrize("dense", [False, True])
def test_streaming_qc_hvg_and_normalization_match_public_scanpy(tmp_path, dense):
    import scanpy as sc
    from devbench.analysis.singlecell import quality_control
    path = tmp_path/"input.h5ad"
    original = fixture(path, dense=dense)
    store = CheckpointStore(tmp_path/"checkpoints", {"input": sha256(path)})
    stream_qc(path, store, layer=None, batch_key="batch", block_rows=17, min_genes=3, mad_limit=None)
    metrics = read_table(store.path("qc")/"metrics.csv")
    expected, _ = quality_control(original, batch_key="batch", min_genes=3, mad_limit=None)
    assert metrics.index.tolist() == original.obs_names.tolist()
    assert "celltypes" not in metrics and "conf_score" not in metrics
    np.testing.assert_allclose(metrics["pct_counts_mt"], expected.obs["pct_counts_mt"])
    stream_hvg(path, store, layer=None, batch_key="batch", block_rows=17, n_top_genes=40)
    sc.pp.normalize_total(expected, target_sum=1e4)
    sc.pp.log1p(expected)
    sc.pp.highly_variable_genes(expected, flavor="seurat", batch_key="batch", n_top_genes=40)
    actual = read_table(store.path("hvg")/"features.csv")
    for name in ("means", "dispersions", "dispersions_norm"):
        np.testing.assert_allclose(actual[name], expected.var[name], rtol=2e-5, atol=2e-7, equal_nan=True)
    np.testing.assert_array_equal(actual["highly_variable"], expected.var["highly_variable"])
    np.testing.assert_array_equal(actual["highly_variable_nbatches"], expected.var["highly_variable_nbatches"])
    stream_selected(path, store, layer=None, block_rows=17)
    selected = ad.read_h5ad(store.path("selected")/"selected.h5ad")
    columns = np.flatnonzero(expected.var.highly_variable.to_numpy())
    counts = original.X if sparse.issparse(original.X) else sparse.csr_matrix(original.X)
    assert (selected.layers["counts"] != counts[:, columns]).nnz == 0
    expected_x = expected.X[:, columns]
    expected_x = expected_x.toarray() if sparse.issparse(expected_x) else expected_x
    np.testing.assert_allclose(selected.X.toarray(), expected_x, rtol=2e-6, atol=2e-6)
    # Selected genes retain their FULL-library denominator, not a new HVG total.
    linear = selected.X.copy()
    linear.data = np.expm1(linear.data)
    assert np.all(np.asarray(linear.sum(axis=1)).ravel() < 9999)


def test_qc_resume_reuses_finished_chunks_after_failure(tmp_path, monkeypatch):
    from devbench.analysis.io import BackedExpression
    path = tmp_path/"input.h5ad"
    fixture(path)
    store = CheckpointStore(tmp_path/"checkpoints", {"input": sha256(path)})
    real = BackedExpression.subset
    seen = []
    def interrupted(self, rows=None, *args, **kwargs):
        seen.append(list(rows)[0])
        if list(rows)[0] == 34:
            raise RuntimeError("simulate disconnect")
        return real(self, rows, *args, **kwargs)
    monkeypatch.setattr(BackedExpression, "subset", interrupted)
    with pytest.raises(RuntimeError, match="disconnect"):
        stream_qc(path, store, layer=None, batch_key="batch", block_rows=17, min_genes=3, mad_limit=None)
    assert seen == [0, 17, 34]
    seen.clear()
    def resumed(self, rows=None, *args, **kwargs):
        seen.append(list(rows)[0])
        return real(self, rows, *args, **kwargs)
    monkeypatch.setattr(BackedExpression, "subset", resumed)
    stream_qc(path, store, layer=None, batch_key="batch", block_rows=17, min_genes=3, mad_limit=None)
    assert seen == [34, 51, 68, 85]
    assert len(list((store.root/"failures").glob("*.json"))) == 1


def test_zero_mitochondrial_matches_fail_before_any_checkpoint(tmp_path):
    path = tmp_path/"input.h5ad"
    data = fixture(path)
    data.var_names = [f"ENSMUSG{i}" for i in range(data.n_vars)]
    data.write_h5ad(path)
    store = CheckpointStore(tmp_path/"checkpoints", {"input": sha256(path)})
    with pytest.raises(ValueError, match="mitochondrial_annotation_unavailable"):
        stream_qc(path, store, layer=None, batch_key="batch")
    assert not list(store.root.glob("qc_*"))
