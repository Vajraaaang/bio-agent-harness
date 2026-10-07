"""Memory ownership must not change the seeded doublet calculation."""
import anndata as ad
import numpy as np
import pandas as pd
import pytest
from scipy import sparse

pytest.importorskip('scanpy')


def test_owned_doublet_input_avoids_copies_without_changing_predictions():
    from devbench.analysis.singlecell import detect_doublets
    rng = np.random.default_rng(49102)
    rates = rng.uniform(.2, 2, 500)
    means = np.tile(rates, (320, 1))
    means[:160, :60] *= 12
    means[160:, 60:120] *= 12
    means *= rng.lognormal(0, .3, (320, 1))
    singlets = rng.poisson(rng.gamma(3, means/3)).astype(np.float64)
    doublets = singlets[:32]+singlets[160:192]
    counts = sparse.csr_matrix(np.vstack([singlets, doublets]))
    original_counts = counts.copy()
    data = ad.AnnData(counts,
        obs=pd.DataFrame({'batch': 'one'}, index=[f'cell_{i}' for i in range(352)]))
    expected, expected_report = detect_doublets(data, batch_key='batch', seed=49102)
    assert 'doublet_score' not in data.obs
    observed, report = detect_doublets(data, batch_key='batch', seed=49102, copy=False)
    assert observed is data
    np.testing.assert_array_equal(observed.obs.doublet_score, expected.obs.doublet_score)
    np.testing.assert_array_equal(observed.obs.predicted_doublet, expected.obs.predicted_doublet)
    assert report == expected_report
    assert (observed.X != original_counts).nnz == 0
