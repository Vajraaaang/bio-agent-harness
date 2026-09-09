"""Quality diagnostics use measured data, explicit backgrounds and real workers."""
import json

import numpy as np
import pandas as pd
import pytest


def cohort(tmp_path, values=None, specimens=None):
    rng = np.random.default_rng(13)
    values = rng.poisson(15, size=(12, 24)).astype(float) if values is None else np.asarray(values)
    samples = [f"s{i:03}" for i in range(values.shape[1])]
    counts, metadata = tmp_path / "counts.csv", tmp_path / "metadata.csv"
    pd.DataFrame(values, index=[f"g{i}" for i in range(values.shape[0])], columns=samples).to_csv(counts)
    pd.DataFrame({"sample_id": samples, "embryonic_age": [f"E{i}" for i in range(len(samples))],
                  "specimen_id": specimens or [f"specimen{i}" for i in range(len(samples))],
                  "condition": ["control"] * (len(samples) // 2) + ["treated"] * (len(samples) - len(samples) // 2),
                  "batch": ["one"] * len(samples)}).to_csv(metadata, index=False)
    return str(counts), str(metadata), samples


def codes(result):
    return {row["code"] for row in result["diagnostics"]}


def test_qc_counts_dropout_and_repeated_specimens(tmp_path):
    from devharness_server.tools_qc import audit_expression_quality
    rng = np.random.default_rng(1)
    values = rng.poisson(20, size=(20, 24)) * (rng.random((20, 24)) > .7)
    counts, metadata, _ = cohort(tmp_path, values, [f"p{i // 4}" for i in range(24)])
    result = audit_expression_quality(counts, metadata)
    assert result["status"] == "warning"
    assert {"high_dropout", "repeated_specimens"}.issubset(codes(result))
    assert result["metrics"]["effective_sample_count"] == 6
    assert result["metrics"]["sample_count"] == 24
    assert result["metrics"]["nonnegative_integer_expression"]
    assert result["metrics"]["zero_fraction"] > .6
    json.dumps(result, allow_nan=False)


def test_qc_all_zero_and_nonfinite_data_fail_explicitly(tmp_path):
    from devharness_server.tools_qc import audit_expression_quality
    counts, metadata, _ = cohort(tmp_path, np.zeros((5, 8)))
    result = audit_expression_quality(counts, metadata)
    assert result["status"] == "fail" and "all_zero_expression" in codes(result)
    frame = pd.read_csv(counts, index_col=0)
    frame.iloc[0, 0] = float("nan")
    frame.iloc[1, 1] = float("inf")
    frame.to_csv(counts)
    result = audit_expression_quality(counts, metadata)
    assert result["status"] == "fail" and "nonfinite_expression" in codes(result)
    assert result["metrics"]["missing_value_count"] == 1
    assert result["metrics"]["nonfinite_value_count"] == 2
    assert result["metrics"]["library_size_summary"] is None
    json.dumps(result, allow_nan=False)


def test_qc_invalid_contrast_and_batch_alias_are_diagnosed(tmp_path):
    from devharness_server.tools_qc import audit_expression_quality
    counts, metadata, _ = cohort(tmp_path)
    absent = {"group_col": "condition", "numerator": "absent", "denominator": "control"}
    result = audit_expression_quality(counts, metadata, contrast=absent)
    assert result["status"] == "fail" and "invalid_contrast" in codes(result)
    assert result["metrics"]["contrast_valid"] is False
    frame = pd.read_csv(metadata)
    frame["batch"] = frame["condition"]
    frame.to_csv(metadata, index=False)
    result = audit_expression_quality(counts, metadata,
        contrast={"group_col": "condition", "numerator": "treated", "denominator": "control"})
    assert result["status"] == "fail" and "confounded_contrast" in codes(result)
    assert result["metrics"]["contrast_valid"] is False


def test_qc_sample_alignment_and_embedding_collapse(tmp_path):
    from devharness_server.tools_qc import audit_expression_quality
    counts, metadata, samples = cohort(tmp_path)
    embedding = tmp_path / "embedding.csv"
    pd.DataFrame({"sample_id": samples, "x": np.zeros(len(samples)), "y": np.zeros(len(samples))}).to_csv(embedding, index=False)
    result = audit_expression_quality(counts, metadata, candidate_embedding_path=str(embedding))
    assert result["status"] == "fail" and "collapsed_embedding" in codes(result)
    assert result["metrics"]["embedding_valid"] is False
    frame = pd.read_csv(embedding)
    frame.loc[0, "sample_id"] = "unmeasured"
    frame.to_csv(embedding, index=False)
    result = audit_expression_quality(counts, metadata, candidate_embedding_path=str(embedding))
    assert result["status"] == "fail" and "embedding_sample_alignment" in codes(result)
    frame = pd.read_csv(metadata)
    frame.loc[0, "sample_id"] = "unmeasured"
    frame.to_csv(metadata, index=False)
    result = audit_expression_quality(counts, metadata)
    assert result["status"] == "fail" and "sample_alignment" in codes(result)


def test_qc_preserved_branches_and_oversmoothing_use_neighborhoods(tmp_path):
    from devharness_server.tools_qc import audit_expression_quality
    time = np.tile(np.linspace(0, 1, 30), 2)
    branch = np.repeat([-1, 1], 30)
    xy = np.column_stack([time, branch * (.2 + time)])
    values = np.vstack([xy[:, 0], xy[:, 1], xy[:, 0] + xy[:, 1], xy[:, 0] - xy[:, 1]])
    counts, metadata, samples = cohort(tmp_path, values)
    preserved, smoothed = tmp_path / "preserved.csv", tmp_path / "smoothed.csv"
    pd.DataFrame({"sample_id": samples, "x": xy[:, 0], "y": xy[:, 1]}).to_csv(preserved, index=False)
    pd.DataFrame({"sample_id": samples, "x": time, "y": np.zeros(len(time))}).to_csv(smoothed, index=False)
    good = audit_expression_quality(counts, metadata, candidate_embedding_path=str(preserved), n_neighbors=5)
    bad = audit_expression_quality(counts, metadata, candidate_embedding_path=str(smoothed), n_neighbors=5)
    assert good["metrics"]["embedding"]["trustworthiness"] > .9
    assert good["metrics"]["embedding"]["neighborhood_retention"] > .7
    assert "embedding_neighborhood_loss" not in codes(good)
    assert "embedding_neighborhood_loss" in codes(bad)
    assert bad["metrics"]["embedding"]["neighborhood_retention"] < .65
    assert not bad["metrics"]["embedding_valid"]
    assert "causal" in " ".join(bad["limitations"])


def test_qc_reference_variance_preserved_or_lost_on_aligned_ids(tmp_path):
    from devharness_server.tools_qc import audit_expression_quality
    counts, metadata, _ = cohort(tmp_path)
    original = pd.read_csv(counts, index_col=0)
    reference = tmp_path / "reference.csv"
    original.iloc[::-1, ::-1].to_csv(reference)
    kept = audit_expression_quality(counts, metadata, reference_counts_path=str(reference))
    assert kept["metrics"]["reference_comparison"]["variance_retention"] == pytest.approx(1)
    assert kept["metrics"]["reference_comparison"]["neighborhood_retention"] == pytest.approx(1)
    assert "reference_variance_loss" not in codes(kept)
    smoothed = np.tile(original.mean(axis=1).to_numpy()[:, None], (1, original.shape[1]))
    pd.DataFrame(smoothed, index=original.index, columns=original.columns).to_csv(counts)
    lost = audit_expression_quality(counts, metadata, reference_counts_path=str(reference))
    assert lost["status"] == "fail"
    assert {"reference_variance_loss", "reference_geometry_collapse"}.issubset(codes(lost))
    assert lost["metrics"]["reference_comparison"]["variance_retention"] == pytest.approx(0)


def test_qc_thresholds_are_explicit_and_configurable(tmp_path):
    from devharness_server.tools_qc import audit_expression_quality
    rng = np.random.default_rng(5)
    values = rng.poisson(20, size=(40, 24)) * (rng.random((40, 24)) > .6)
    counts, metadata, _ = cohort(tmp_path, values)
    relaxed = audit_expression_quality(counts, metadata, thresholds={"dropout_warning": .99})
    assert "high_dropout" not in codes(relaxed)
    assert relaxed["thresholds"]["dropout_warning"] == .99
    with pytest.raises(ValueError, match="threshold"):
        audit_expression_quality(counts, metadata, thresholds={"dropout_warning": float("nan")})
    with pytest.raises(ValueError, match="Unknown"):
        audit_expression_quality(counts, metadata, thresholds={"clinical_certainty": .9})


def test_qc_nonfinite_embedding_outside_geometry_subset_is_not_hidden(tmp_path):
    from devharness_server.tools_qc import audit_expression_quality
    counts, metadata, samples = cohort(tmp_path)
    selected = set(np.random.default_rng(0).choice(len(samples), 4, replace=False))
    excluded = next(index for index in range(len(samples)) if index not in selected)
    embedding = tmp_path / "embedding.csv"
    frame = pd.DataFrame({"sample_id": samples, "x": np.arange(len(samples)), "y": np.arange(len(samples), dtype=float)})
    frame.loc[excluded, "y"] = float("inf")
    frame.to_csv(embedding, index=False)
    result = audit_expression_quality(counts, metadata, candidate_embedding_path=str(embedding), max_cells=4)
    assert result["status"] == "fail" and "nonfinite_embedding" in codes(result)


def test_qc_numerical_overflow_fails_with_finite_json(tmp_path):
    from devharness_server.tools_qc import audit_expression_quality
    values = np.tile([1e250, -1e250], (4, 6))
    counts, metadata, _ = cohort(tmp_path, values)
    result = audit_expression_quality(counts, metadata)
    assert result["status"] == "fail" and "expression_overflow" in codes(result)
    json.dumps(result, allow_nan=False)


def test_qc_missing_specimen_provenance_does_not_count_cells_as_replicates(tmp_path):
    from devharness_server.tools_qc import audit_expression_quality
    counts, metadata, samples = cohort(tmp_path)
    frame = pd.read_csv(metadata).drop(columns="specimen_id")
    frame.to_csv(metadata, index=False)
    result = audit_expression_quality(counts, metadata)
    assert result["status"] == "warning" and "independence_unverified" in codes(result)
    assert result["metrics"]["sample_count"] == len(samples)
    assert result["metrics"]["effective_sample_count"] is None


def test_qc_reference_ratio_overflow_is_not_serialized_as_infinity(tmp_path):
    from devharness_server.tools_qc import audit_expression_quality
    values = np.random.default_rng(11).normal(size=(4, 12))
    counts, metadata, samples = cohort(tmp_path, values * 1e150)
    reference = tmp_path / "reference.csv"
    pd.DataFrame(values * 1e-150, index=[f"g{i}" for i in range(4)], columns=samples).to_csv(reference)
    result = audit_expression_quality(counts, metadata, reference_counts_path=str(reference))
    assert result["status"] == "fail" and "reference_scale_overflow" in codes(result)
    assert result["metrics"]["reference_comparison"]["variance_retention"] is None
    json.dumps(result, allow_nan=False)
