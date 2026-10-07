"""Independent workbench input tests; no benchmark execution or goldens."""
from pathlib import Path

import anndata as ad
import numpy as np
import pandas as pd
import pytest
from scipy import sparse


def dataset(tmp_path, layout="csr"):
    values = np.arange(60, dtype=np.float32).reshape(12, 5)
    values[values % 3 != 0] = 0
    matrix = {"csr": sparse.csr_matrix, "csc": sparse.csc_matrix,
              "dense": np.asarray}[layout](values)
    data = ad.AnnData(matrix, obs=pd.DataFrame(index=[f"c{i}" for i in range(12)]),
                      var=pd.DataFrame({"gene_symbols": ["mt-Co1", "Sox2", "Pax6", "Dcx", "Gad1"]},
                                       index=[f"ENSMUSG{i}" for i in range(5)]))
    data.layers["counts"] = matrix.copy()
    data.obsm["unused_embedding"] = np.ones((12, 2))
    path = tmp_path / "input.h5ad"
    data.write_h5ad(path)
    return path, values


@pytest.mark.parametrize("layout", ["csr", "csc", "dense"])
def test_selected_matrix_is_backed_and_subset_matches_source(tmp_path, layout):
    from devbench.analysis.io import open_h5ad
    path, values = dataset(tmp_path, layout)
    with open_h5ad(path, layer="counts") as source:
        assert source.shape == (12, 5)
        assert source.matrix_source == "layers/counts"
        selected = source.subset([8, 1, 5], [3, 0], max_bytes=100_000)
        result = selected.X.toarray() if sparse.issparse(selected.X) else selected.X
        np.testing.assert_array_equal(result, values[[8, 1, 5]][:, [3, 0]])
        assert selected.obs_names.tolist() == ["c8", "c1", "c5"]
        assert selected.var_names.tolist() == ["ENSMUSG3", "ENSMUSG0"]
        assert not selected.layers and not selected.obsm
    assert source.closed
    with pytest.raises(ValueError, match="closed"):
        source.subset([0])


def test_unused_matrices_are_never_deserialized(tmp_path, monkeypatch):
    from devbench.analysis.io import open_h5ad
    path, _ = dataset(tmp_path)
    read = ad.io.read_elem
    reads = []

    def guarded(element):
        reads.append(element.name)
        assert element.name in {"/obs", "/var"}
        return read(element)

    monkeypatch.setattr(ad.io, "read_elem", guarded)
    with open_h5ad(path) as source:
        source.subset([0, 1])
    assert reads == ["/obs", "/var"]


def test_refuses_materialization_over_budget_and_invalid_indices(tmp_path):
    from devbench.analysis.io import open_h5ad
    path, _ = dataset(tmp_path)
    with open_h5ad(path) as source:
        with pytest.raises(MemoryError, match="budget"):
            source.subset(range(12), max_bytes=1)
        for rows in ([0, 0], [-1], [12], [True], [0.5], []):
            with pytest.raises(ValueError):
                source.subset(rows)
        with pytest.raises(ValueError):
            source.subset([0], max_bytes=True)


def test_missing_layer_is_an_error_and_does_not_fall_back(tmp_path):
    from devbench.analysis.io import open_h5ad
    path, _ = dataset(tmp_path)
    with pytest.raises(ValueError, match="layer"):
        with open_h5ad(path, layer="invented"):
            pass


def test_input_is_unchanged(tmp_path):
    import hashlib
    from devbench.analysis.io import open_h5ad
    path, _ = dataset(tmp_path)
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    with open_h5ad(path) as source:
        source.subset([1, 2, 3])
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


def test_all_features_reuses_the_single_backed_CSR_materialization(tmp_path):
    """A full-feature read needs no second expression-sized allocation."""
    from devbench.analysis.io import open_h5ad
    path, values = dataset(tmp_path)
    with open_h5ad(path, layer="counts") as source:
        backend = source.matrix
        reads = []

        class ObservedReader:
            format = "csr"

            def __getitem__(self, selection):
                result = backend[selection]
                reads.append(result)
                return result

        source.matrix = ObservedReader()
        selected = source.subset([8, 1, 5])
        np.testing.assert_array_equal(selected.X.toarray(), values[[8, 1, 5]])
        assert np.shares_memory(selected.X.data, reads[0].data)
        assert np.shares_memory(selected.X.indices, reads[0].indices)


def test_large_CSR_read_bounds_backend_requests_and_preserves_row_order(tmp_path):
    from devbench.analysis.io import open_h5ad
    values = np.arange(300*9, dtype=np.float32).reshape(300, 9)
    values[values % 3 != 0] = 0
    path = tmp_path/'many-rows.h5ad'
    ad.AnnData(sparse.csr_matrix(values),
        obs=pd.DataFrame(index=[f'c{i}' for i in range(300)])).write_h5ad(path)
    rows = np.random.default_rng(931).permutation(300)[:250]
    with open_h5ad(path) as source:
        backend = source.matrix
        sizes = []

        class BoundedReader:
            format = 'csr'

            def __getitem__(self, selection):
                size = len(selection[0])
                sizes.append(size)
                if size > 128:
                    raise MemoryError('single backend request exceeds 128 rows')
                return backend[selection]

        source.matrix = BoundedReader()
        result = source.subset(rows)
        np.testing.assert_array_equal(result.X.toarray(), values[rows])
        assert result.obs_names.tolist() == [f'c{i}' for i in rows]
        assert max(sizes) <= 128
