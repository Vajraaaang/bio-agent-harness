"""Synthetic parity and bounds for whole-family RLE normalization."""

import numpy as np
import pytest

from rna_kernels.normalization import median_ratio_factors


@pytest.mark.parametrize("zero_mode", ["regular", "poscounts"])
@pytest.mark.parametrize("gene_count", [15, 16, 66_001])
def test_streaming_rle_matches_dense_reference(
    workspace_tmp_path, zero_mode, gene_count
):
    from rna_kernels.streaming_rle import streaming_median_ratio_factors

    rng = np.random.default_rng(118)
    counts = rng.integers(1, 10_000, size=(gene_count, 5))
    if zero_mode == "poscounts":
        counts[np.arange(gene_count), np.arange(gene_count) % 5] = 0
        counts[0] = 0
    elif zero_mode == "regular":
        counts[0, 1] = 0

    def blocks():
        for start in range(0, gene_count, 9_001):
            yield counts[start : start + 9_001]

    expected, ref = median_ratio_factors(counts, list(range(gene_count)))
    observed, evidence = streaming_median_ratio_factors(
        blocks,
        counts.shape[1],
        scratch_dir=workspace_tmp_path,
    )

    np.testing.assert_allclose(observed, expected, rtol=1e-14)
    assert evidence["reference_gene_count"] == ref["reference_gene_count"]
    assert evidence["method"] == ref["method"]
    assert evidence["scope"] == "complete_declared_gene_family"
    assert not list(workspace_tmp_path.iterdir())


@pytest.mark.parametrize(
    ("counts", "limit", "message"),
    [
        ([[0, 0], [0, 0]], 1024, "no_normalization_reference"),
        ([[0, 1], [0, 2]], 1024, "no_sample_normalization_reference"),
        ([[2, 1], [2, 3]], 31, "scratch byte budget"),
        ([[0.5, 1], [1, 2]], 1024, "nonnegative integer"),
        ([[1, 1], [np.nan, 1]], 1024, "nonnegative integer"),
    ],
)
def test_streaming_rle_rejects_invalid_inputs(
    workspace_tmp_path, counts, limit, message
):
    from rna_kernels.streaming_rle import streaming_median_ratio_factors

    with pytest.raises(ValueError, match=message):
        streaming_median_ratio_factors(
            lambda: iter([np.asarray(counts)]),
            2,
            scratch_dir=workspace_tmp_path,
            max_scratch_bytes=limit,
        )
    assert not list(workspace_tmp_path.iterdir())


def test_float32_count_storage_keeps_float64_log_precision(workspace_tmp_path):
    from rna_kernels.streaming_rle import streaming_median_ratio_factors

    counts = np.asarray(
        [[3, 15, 8], [40, 1, 15], [20, 6, 8], [30, 6, 6]], dtype=np.float32
    )
    expected, _ = median_ratio_factors(
        counts.astype(np.int64), list(range(len(counts)))
    )
    factors, _ = streaming_median_ratio_factors(
        lambda: iter([counts]),
        3,
        scratch_dir=workspace_tmp_path,
    )

    np.testing.assert_allclose(factors, expected, rtol=1e-14)


def test_over_panel_calibration_uses_full_family_factors(
    monkeypatch, workspace_tmp_path
):
    import tempfile
    from types import SimpleNamespace

    import pandas as pd

    from devbench.core import family_fitting
    from rna_kernels import normalization

    monkeypatch.setattr(tempfile, "tempdir", str(workspace_tmp_path))
    monkeypatch.setattr(normalization, "MAX_PANEL_BYTES", 64)
    monkeypatch.setattr(
        family_fitting, "capture_bulk_plot_evidence", lambda *_a, **_kw: {}
    )
    counts = np.array([[3, 15, 8], [40, 1, 15], [20, 6, 8], [30, 6, 6]])
    metadata = pd.DataFrame(index=["s1", "s2", "s3"])
    data = SimpleNamespace(
        expression=counts,
        gene_ids=("g1", "g2", "g3", "g4"),
        sample_ids=list(metadata.index),
        metadata=metadata,
        measurement="raw_counts",
        context={},
        unit_hierarchy={},
    )
    design = pd.DataFrame({"intercept": [1.0] * len(metadata)}, index=metadata.index)
    expected, _ = median_ratio_factors(counts, data.gene_ids)

    def calibrate_panel(panel_counts, panel, *_args, strict, size_factors=None):
        assert not strict
        assert len(panel_counts) < len(counts)
        np.testing.assert_allclose(size_factors, expected, rtol=1e-14)
        return size_factors, {}, {"gene_estimates": {}}, [], []

    def calibrate_family(blocks, reference, *_args, strict):
        assert not strict
        np.testing.assert_allclose(reference[0], expected, rtol=1e-14)
        assert sum(len(genes) for genes, _counts in blocks) == len(counts)
        return {"gene_estimates": {}}, [], [], dict.fromkeys(data.gene_ids, "filtered")

    monkeypatch.setattr(normalization, "calibrate_counts", calibrate_panel)
    monkeypatch.setattr(normalization, "calibrate_count_family", calibrate_family)
    diagnostics = {}

    fits, failures = family_fitting._fit_family(data, design, diagnostics)

    assert not fits
    assert failures == dict.fromkeys(data.gene_ids, "filtered")
    assert diagnostics["normalization"]["scope"] == "complete_declared_gene_family"
    assert diagnostics["normalization"]["per_sample_size_factors"] == pytest.approx(
        dict(zip(data.sample_ids, expected, strict=True)),
    )
