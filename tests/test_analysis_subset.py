import numpy as np
import pytest


def test_block_reader_preserves_rows_and_subset_command_is_reproducible(tmp_path):
    from tests.test_analysis_io import dataset
    from devbench.analysis.io import open_h5ad
    from devbench.analysis.subset import create_subset
    import anndata as ad
    path, values = dataset(tmp_path)
    with open_h5ad(path, layer="counts") as source:
        chunks = list(source.iter_rows(block_rows=3, max_bytes=100_000))
    observed = np.vstack([chunk.X.toarray() for chunk in chunks])
    np.testing.assert_array_equal(observed, values)
    first, second = tmp_path / "one.h5ad", tmp_path / "two.h5ad"
    receipt = create_subset(path, first, cells=5, seed=7, layer="counts", max_bytes=100_000)
    create_subset(path, second, cells=5, seed=7, layer="counts", max_bytes=100_000)
    assert receipt["selected_cells"] == 5
    assert ad.read_h5ad(first).obs_names.equals(ad.read_h5ad(second).obs_names)
    assert receipt["source_unchanged"]
    with pytest.raises(FileExistsError):
        create_subset(path, first, cells=5, seed=7, layer="counts")
