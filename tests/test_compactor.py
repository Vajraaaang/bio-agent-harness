"""Temporal compression is deterministic, bounded, and handles degenerate inputs."""

import json

import numpy as np
import pandas as pd
import pytest

from devharness_core.compactor import TemporalStateCompactor
from devharness_core.stages import parse_stage
from devharness_core.storage import ChunkedExpressionStore


def dataset():
    rng = np.random.default_rng(41)
    samples = ["s" + str(i) for i in range(12)]
    values = rng.poisson(15, size=(30, 12)).astype(float)
    values[0] = np.repeat([2, 10, 40, 90], 3)
    counts = pd.DataFrame(values, index=["gene_" + str(i) for i in range(30)], columns=samples)
    metadata = pd.DataFrame({"sample_id": samples,
                             "embryonic_age": np.repeat(["E8.5", "E10.5", "E12.5", "E14.5"], 3),
                             "batch": ["A", "B"] * 6})
    return counts, metadata


def test_compactor_svd_and_strict_json_budget():
    counts, metadata = dataset()
    compactor = TemporalStateCompactor(counts, metadata, "embryonic_age", top_n=15)
    assert compactor.embedding.shape == (12, 5)
    assert len(compactor.selected_genes) == 15
    assert np.isfinite(compactor.explained_variance_ratio).all()
    text = compactor.to_json_manifold()
    assert len(text.encode("utf-8")) < 1500
    state = json.loads(text)
    assert len(state["stage_centroids"]) == 4
    assert 0 <= sum(state["svd_variance_explained"]) <= 1.000001
    assert np.isfinite(state["condition_number"])
    assert -1 <= state["temporal_monotonicity"] <= 1
    assert state["top_trajectory_driver_genes"]


def test_out_of_core_matches_dataframe_and_aligns_samples(tmp_path):
    counts, metadata = dataset()
    path = tmp_path / "counts.zarr"
    ChunkedExpressionStore.from_dataframe_to_zarr(counts, path).close()
    first = TemporalStateCompactor(counts, metadata, "embryonic_age", top_n=10)
    second = TemporalStateCompactor(path, metadata.iloc[::-1], "embryonic_age", top_n=10)
    np.testing.assert_allclose(first.embedding, second.embedding)
    assert first.selected_genes == second.selected_genes


def test_single_stage_zero_matrix_has_finite_summary():
    counts = pd.DataFrame([[0, 0]], index=["zero"], columns=["a", "b"])
    metadata = pd.DataFrame({"embryonic_age": ["E8.5", "E8.5"]}, index=["a", "b"])
    compactor = TemporalStateCompactor(counts, metadata, "embryonic_age")
    state = json.loads(compactor.to_json_manifold())
    assert state["temporal_monotonicity"] is None
    assert state["temporal_monotonicity_status"] == "insufficient_timepoints"
    assert state["condition_number"] is None
    assert state["condition_number_status"] == "singular"
    assert np.isfinite(compactor.embedding).all()


def test_distinct_close_timepoints_keep_distinct_published_centroids():
    counts = pd.DataFrame([[0, 0]], index=["zero"], columns=["a", "b"])
    times = [1.0, 1.0 + 1e-13]
    metadata = pd.DataFrame({"time": times}, index=["a", "b"])
    compactor = TemporalStateCompactor(counts, metadata, "time")
    state = json.loads(compactor.to_json_manifold())
    assert len(state["stage_centroids"]) == len(state["stage_variances"]) == 2
    assert list(map(float, state["stage_centroids"])) == times
    assert state["temporal_monotonicity"] == 0.0


def test_utf8_names_and_many_stages_still_fit():
    counts, metadata = dataset()
    counts.index = ["基因" * 150 + str(i) for i in range(len(counts))]
    compactor = TemporalStateCompactor(counts, metadata, "embryonic_age")
    text = compactor.to_json_manifold(max_tokens=750)
    assert len(text.encode("utf-8")) < 750
    assert "top_trajectory_driver_genes" in json.loads(text)
    with pytest.raises(ValueError, match="budget"):
        compactor.to_json_manifold(max_tokens=5)


def test_metadata_mismatch_rejected_before_spawn():
    counts, metadata = dataset()
    with pytest.raises(ValueError, match="sample"):
        TemporalStateCompactor(counts, metadata.iloc[:-1], "embryonic_age")


@pytest.mark.parametrize("value,expected", [("E8.5", 8.5), ("day 10.5", 10.5), (3, 3.0)])
def test_stage_cleaning(value, expected):
    assert parse_stage(value) == expected


@pytest.mark.parametrize("value", ["E8.5 to E10.5", "unknown", float("nan")])
def test_invalid_stages_rejected(value):
    with pytest.raises(ValueError):
        parse_stage(value)
