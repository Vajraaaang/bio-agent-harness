import pandas as pd
import pytest


def test_cluster_concordance_is_label_permutation_invariant_and_not_accuracy():
    from devbench.analysis.concordance import compare_predictions
    old = pd.DataFrame({"leiden": ["a", "a", "b", "b"],
                        "predicted_labels": ["T", "T", "B", "B"]}, index=list("wxyz"))
    new = pd.DataFrame({"leiden": ["7", "7", "2", "2"],
                        "cell_type": ["T", "uncertain", "B", "B"]}, index=list("wxyz"))
    report = compare_predictions(old, new)
    assert report["clustering"]["ARI"] == 1
    assert report["clustering"]["NMI"] == 1
    assert report["annotation"]["predicted_labels"]["label_agreement"] == .75
    assert report["scope"] == "concordance_with_prior_predictions_not_experimental_accuracy"


def test_missing_model_does_not_produce_fabricated_annotation_agreement():
    from devbench.analysis.concordance import compare_predictions
    old = pd.DataFrame({"leiden": ["a", "a", "b", "b"],
                        "celltypes": ["T", "T", "B", "B"]}, index=list("wxyz"))
    new = pd.DataFrame({"leiden": ["7", "7", "2", "2"]}, index=list("wxyz"))
    report = compare_predictions(old, new)
    assert report["annotation"]["status"] == "unavailable_model_not_rerun"
    assert report["annotation"]["label_agreement"] is None


def test_concordance_rejects_misaligned_or_duplicate_cells():
    from devbench.analysis.concordance import compare_predictions
    old = pd.DataFrame({"leiden": ["a", "b"]}, index=["one", "two"])
    for indices in (["one", "one"], ["one", "absent"]):
        with pytest.raises(ValueError, match="identit"):
            compare_predictions(old, pd.DataFrame({"leiden": ["a", "b"]}, index=indices))


def test_missing_reference_clusters_are_unknown_not_perfect_agreement():
    from devbench.analysis.concordance import compare_predictions
    old = pd.DataFrame({"leiden": [None, None]}, index=["one", "two"])
    new = pd.DataFrame({"leiden": ["a", "b"]}, index=old.index)
    assert compare_predictions(old, new)["clustering"]["ARI"] is None


def test_stored_confidence_distribution_is_reported_without_a_new_annotation():
    from devbench.analysis.concordance import compare_predictions
    old = pd.DataFrame({"leiden": ["a", "a", "b", "b"], "conf_score": [.2, .55, .85, .95]}, index=list("wxyz"))
    new = pd.DataFrame({"leiden": ["x", "x", "y", "y"]}, index=old.index)
    report = compare_predictions(old, new)
    assert [row["cells"] for row in report["reference_confidence"]["bins"]] == [1,1,1,1]
    assert report["reference_confidence"]["quantiles"]["0.5"] == pytest.approx(.7)
    assert report["reference_status"] == "fixed_external_reference_with_unknown_annotation_provenance"


def test_real_small_concordance_runs_both_embeddings_without_fitting_to_old_labels(tmp_path):
    pytest.importorskip("scanpy")
    from tests.test_analysis_singlecell import counts_fixture
    from devbench.analysis.concordance import run_concordance
    data = counts_fixture()
    data.layers["counts"] = data.X.copy()
    data.obs["leiden"] = ["old-A"]*40 + ["old-B"]*40
    data.obs["celltypes"] = ["prior-T"]*40 + ["prior-B"]*40
    source = tmp_path / "labelled.h5ad"
    data.write_h5ad(source)
    report = run_concordance(source, tmp_path / "comparison", min_genes=3, mad_limit=None,
                             n_top_genes=30, n_pcs=8, seed=128)
    assert report["input_unchanged"]
    assert report["batch_count"] == 2
    assert set(report["conditions"]) == {"uncorrected", "harmony"}
    for result in report["conditions"].values():
        assert result["clustering"]["ARI"] is not None
        assert result["annotation"]["label_agreement"] is None
    assert report["conditions"]["harmony"]["method"]["batch_correction"]["status"] == "completed"
