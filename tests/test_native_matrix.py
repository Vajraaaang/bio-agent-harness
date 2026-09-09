"""Matrix generator and summary checks; no Docker, model or native fitting calls."""
import json
from pathlib import Path

import numpy as np
import pytest

from scripts import native_nb_matrix as matrix


@pytest.fixture
def protocol():
    return json.loads((Path(__file__).resolve().parents[1] / "validation/native_nb_matrix_v1.json").read_text())


@pytest.mark.parametrize("cell_index", range(6))
def test_generator_repeats_and_preserves_fixed_null_space(protocol, cell_index):
    cell = protocol["cells"][cell_index]
    first = matrix.cohort_arrays(protocol, cell, np.random.SeedSequence(protocol["seed"], spawn_key=(cell_index, 0)))
    second = matrix.cohort_arrays(protocol, cell, np.random.SeedSequence(protocol["seed"], spawn_key=(cell_index, 0)))
    assert np.array_equal(first["counts"], second["counts"])
    assert first["counts"].shape == (80, cell["samples"])
    assert first["counts"].dtype.kind in "iu"
    assert np.array_equal(first["full"][:, :2], first["reduced"])
    assert np.linalg.matrix_rank(first["full"]) == 5
    assert np.linalg.matrix_rank(first["reduced"]) == 2
    assert first["truth"]["max_absolute_null_temporal_coefficient"] < 1e-10
    assert sum(first["truth"]["null_hypotheses"]) == (32 if cell["regime"] == "complete_null" else 16)
    assert np.all(np.bincount(first["times"].astype(int)) == cell["samples"] // 6)


def test_mixed_gene_roles_and_effect_curves_are_prespecified(protocol):
    data = matrix.cohort_arrays(protocol, protocol["cells"][4], np.random.SeedSequence(2026091001, spawn_key=(4, 0)))
    assert data["truth"]["hypothesis_shapes"][:16] == ["null"] * 16
    for start, shape in ((16, "positive_linear"), (20, "negative_linear"), (24, "transient"), (28, "delayed")):
        assert data["truth"]["hypothesis_shapes"][start:start+4] == [shape] * 4
    assert np.all(data["effects"][:16] == 0) and np.all(data["effects"][32:] == 0)
    assert np.allclose(np.ptp(data["effects"][16:32], axis=1), np.log(4))


def test_mixed_fdp_power_and_null_rejection_have_distinct_denominators():
    null = [True] * 16 + [False] * 16
    p = [0.8] * 32
    q = [0.8] * 32
    for index in (0, 1, 16, 17, 20, 24):
        p[index] = q[index] = 0.001
    scores = matrix.cohort_scores(p, q, null, ["null"] * 16 + ["alternative"] * 16)
    assert scores["false_discoveries"] == 2 and scores["discoveries"] == 6
    assert scores["null_rejection_fraction"] == 2 / 16
    assert scores["fdp"] == 2 / 6
    assert scores["power"] == 4 / 16


def test_zero_discoveries_does_not_create_nan_or_fake_power():
    scores = matrix.cohort_scores([0.8] * 32, [0.8] * 32, [True] * 32, ["null"] * 32)
    assert scores["fdp"] == 0 and scores["power"] is None
    assert scores["null_rejection_fraction"] == 0


@pytest.mark.parametrize("value", [None, float("nan"), float("inf"), -0.1, 1.1])
def test_missing_or_invalid_probability_cannot_be_a_completed_cohort(value):
    p = [0.5] * 32
    p[0] = value
    with pytest.raises(ValueError):
        matrix.cohort_scores(p, [0.8] * 32, [True] * 32, ["null"] * 32)


def test_zero_observations_have_nonzero_uncertainty():
    summary = matrix.bounded_summary([0.0] * 20)
    assert summary["mean"] == 0.0
    assert summary["ci95"][0] == 0.0 and summary["ci95"][1] > 0.1
    binary = matrix.bounded_summary([0.0] * 20, binary=True)
    assert binary["ci95"][1] > 0.1


def test_simultaneous_warning_requires_complete_cell_and_has_no_pass_label():
    warning = matrix.primary_warning([1.0] * 20, binary=True)
    assert warning["assessable"] is True and warning["warning"] is True
    assert warning["family_slots"] == 24 and warning["one_sided_alpha"] == 0.05 / 24
    incomplete = matrix.primary_warning([1.0] * 19, binary=True)
    assert incomplete["assessable"] is False and incomplete["warning"] is None
    no_signal = matrix.primary_warning([0.0] * 20, binary=True)
    assert no_signal["warning"] is False and "passed" not in no_signal


def test_paired_difference_interval_uses_the_signed_range():
    summary = matrix.bounded_summary([-0.5, 0.5] * 10, low=-1, high=1)
    assert summary["mean"] == 0
    assert summary["ci95"][0] < 0 < summary["ci95"][1]
    assert summary["ci95"][1] > matrix.bounded_summary([0.0] * 20)["ci95"][1]
