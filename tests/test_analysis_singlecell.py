import numpy as np
import pandas as pd
import pytest
from scipy import sparse

ad = pytest.importorskip("anndata")
pytest.importorskip("scanpy")


def counts_fixture():
    rng = np.random.default_rng(128)
    values = rng.poisson(2, (80, 60)).astype(np.int32)
    values[:40, 1:11] += 20
    values[40:, 11:21] += 20
    symbols = ["mt-Co1", "Cd3d", "Cd3e", "Trac", "Il7r"] + [f"Gene{i}" for i in range(5, 60)]
    symbols[11:15] = ["Ms4a1", "Cd79a", "Cd79b", "Cd19"]
    return ad.AnnData(sparse.csr_matrix(values),
        obs=pd.DataFrame({"batch": ["one", "two"]*40}, index=[f"c{i}" for i in range(80)]),
        var=pd.DataFrame({"gene_symbols": symbols}, index=[f"ENSMUSG{i}" for i in range(60)]))


def test_qc_uses_symbols_and_retains_correct_raw_metrics():
    from devbench.analysis.singlecell import quality_control
    original = counts_fixture()
    result, report = quality_control(original, min_genes=3, mad_limit=None)
    np.testing.assert_allclose(result.obs["total_counts"], np.asarray(original.X.sum(axis=1)).ravel())
    expected = 100 * original.X[:, 0].toarray().ravel() / np.asarray(original.X.sum(axis=1)).ravel()
    np.testing.assert_allclose(result.obs["pct_counts_mt"], expected)
    np.testing.assert_allclose(result.obs["log1p_total_counts"], np.log1p(result.obs["total_counts"]))
    assert report["mitochondrial_features"] == 1
    assert report["gene_symbol_source"] == "gene_symbols"


def test_zero_mito_matches_cannot_be_reported_as_zero_percent():
    from devbench.analysis.singlecell import quality_control
    data = counts_fixture()
    data.var["gene_symbols"] = [f"Other{i}" for i in range(60)]
    with pytest.raises(ValueError, match="mitochondrial_annotation_unavailable"):
        quality_control(data, min_genes=3)


def test_normalized_noninteger_input_is_refused_as_counts():
    from devbench.analysis.singlecell import quality_control
    data = counts_fixture()
    data.X = data.X.astype(float) / 3
    with pytest.raises(ValueError, match="raw_counts"):
        quality_control(data, min_genes=3)


def test_normalization_preserves_counts_and_clustering_has_finite_geometry():
    from devbench.analysis.singlecell import quality_control, normalize_and_embed
    data, _ = quality_control(counts_fixture(), min_genes=3, mad_limit=None)
    counts = data.X.copy()
    result, report = normalize_and_embed(data, n_top_genes=30, n_pcs=8, seed=128)
    assert (result.layers["counts"] != counts).nnz == 0
    linear = result.X.copy()
    linear.data = np.expm1(linear.data)
    np.testing.assert_allclose(np.asarray(linear.sum(axis=1)).ravel(), 10_000, rtol=2e-6)
    assert result.obsm["X_umap"].shape == (80, 2)
    assert np.isfinite(result.obsm["X_umap"]).all()
    assert result.obs["leiden"].nunique() >= 2
    assert report["batch_correction"]["status"] == "not_requested"


def test_marker_annotation_is_scored_and_missing_reference_is_uncertain():
    from devbench.analysis.singlecell import marker_annotation
    data = counts_fixture()
    panels = {"T cell": ["Cd3d", "Cd3e", "Trac", "Il7r"],
              "B cell": ["Ms4a1", "Cd79a", "Cd79b", "Cd19"]}
    labels, evidence = marker_annotation(data, panels)
    assert (labels.iloc[:40] == "T cell").mean() > .9
    assert (labels.iloc[40:] == "B cell").mean() > .9
    labels, evidence = marker_annotation(data, {"Unknown type": ["missing1", "missing2"]})
    assert set(labels) == {"uncertain"}
    assert evidence["status"] == "insufficient_marker_coverage"


def test_small_doublet_batch_stays_unavailable_instead_of_zero():
    from devbench.analysis.singlecell import detect_doublets
    data, report = detect_doublets(counts_fixture(), batch_key="batch")
    assert report["status"] == "unavailable"
    assert data.obs["doublet_score"].isna().all()
    assert data.obs["predicted_doublet"].isna().all()


def test_celltypist_requires_model_identity_before_loading(tmp_path):
    from devbench.analysis.singlecell import annotate_celltypist
    fake_model = tmp_path / "untrusted.pkl"
    fake_model.write_bytes(b"not a trusted model")
    with pytest.raises(ValueError, match="model_checksum_mismatch"):
        annotate_celltypist(counts_fixture(), fake_model, expected_sha256="0"*64)


def test_doublet_scores_without_thresholded_flags_are_a_failure(monkeypatch):
    import scanpy as sc
    from devbench.analysis.singlecell import detect_doublets
    data = ad.concat([counts_fixture(), counts_fixture()], index_unique="-")
    def no_threshold(piece, **kwargs):
        piece.obs["doublet_score"] = .1
        piece.obs["predicted_doublet"] = "unknown"
    monkeypatch.setattr(sc.pp, "scrublet", no_threshold)
    with pytest.raises(ValueError, match="doublet_detection_failed"):
        detect_doublets(data)
