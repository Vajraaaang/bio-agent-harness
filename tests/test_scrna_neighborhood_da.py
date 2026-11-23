"""Synthetic recovery and admission checks for donor-level neighborhood DA."""

from hashlib import sha256
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from devbench.core._hashing import canonical_sha256
from devbench.core._rna_solver_inputs import ConditionContrast
from devbench.core.scientific_types import content_sha256
from devbench.core.scrna_neighborhood_da import (
    NeighborhoodDASpec,
    _spatial_fdr,
    fit_neighborhood_da,
)


def _inputs(
    tmp_path: Path,
    *,
    expansion: bool = False,
    collapsed: bool = False,
    donors_per_group: int = 8,
    confounded: bool = False,
    exclusive_region: bool = False,
) -> tuple[Path, Path, NeighborhoodDASpec]:
    rng = np.random.default_rng(407)
    embedding, metadata = [], []
    n_regions = 24
    for arm in ("control", "treated"):
        for donor in range(donors_per_group):
            depth = rng.uniform(0.85, 1.15)
            for region in range(n_regions):
                multiplier = 4 if expansion and arm == "treated" and region < 3 else 1
                if exclusive_region and arm == "control" and region == 0:
                    multiplier = 0
                rate = 9 * depth * multiplier * rng.gamma(10, 0.1)
                for x, y in rng.normal((8 * region, 0), 0.15, (rng.poisson(rate), 2)):
                    cell_id = f"cell-{len(metadata):06d}"
                    embedding.append(
                        (cell_id, 0.0 if collapsed else x, y if not collapsed else 0.0)
                    )
                    metadata.append((cell_id, f"{arm}-{donor}", arm, arm))
    embedding_path = tmp_path / "embedding.csv"
    metadata_path = tmp_path / "metadata.csv"
    pd.DataFrame(embedding, columns=["cell_id", "x", "y"]).to_csv(
        embedding_path, index=False
    )
    metadata_frame = pd.DataFrame(
        metadata, columns=["cell_id", "donor_id", "condition", "batch"]
    )
    if not confounded:
        metadata_frame = metadata_frame.drop(columns="batch")
    metadata_frame.sample(frac=1, random_state=38).to_csv(metadata_path, index=False)
    spec = NeighborhoodDASpec(
        embedding_sha256=sha256(embedding_path.read_bytes()).hexdigest(),
        metadata_sha256=sha256(metadata_path.read_bytes()).hexdigest(),
        contrast=ConditionContrast(
            numerator="treated",
            denominator="control",
            technical_factors=("batch",) if confounded else (),
        ),
        k=60,
        prop=0.035,
        seed=249,
    )
    return embedding_path, metadata_path, spec


def test_recovers_planted_local_expansion_and_hashes_receipt(tmp_path: Path):
    embedding, metadata, spec = _inputs(tmp_path, expansion=True)
    receipt = fit_neighborhood_da(embedding, metadata, spec)
    assert receipt.clinical_release_allowed is False
    assert receipt.specification_sha256 == canonical_sha256(
        spec.model_dump(mode="json")
    )
    assert receipt.receipt_sha256 == content_sha256(
        receipt.model_dump(mode="json", exclude={"receipt_sha256"})
    )
    assert receipt.donors_per_condition == {"control": 8, "treated": 8}
    assert receipt.design_diagnostics["condition_number_validated"]
    assert len(receipt.neighborhoods) == int(np.ceil(receipt.n_cells * spec.prop))
    assert receipt.dispersion_calibration["method"] == "pydeseq2_empirical_bayes"
    significant = [
        row
        for row in receipt.neighborhoods
        if row.spatial_fdr is not None and row.spatial_fdr < 0.05
    ]
    assert significant
    assert any(
        row.center_coordinates[0] < 20
        and row.log2_fold_change is not None
        and row.log2_fold_change > 1
        for row in significant
    )
    for row in receipt.neighborhoods:
        assert row.kth_neighbor_distance > 0
        if row.p_value is None:
            assert row.q_value is row.spatial_fdr is row.log2_fold_change is None
        else:
            assert row.q_value is not None and row.spatial_fdr is not None
            assert 0 < row.p_value <= row.q_value <= 1
            assert row.p_value <= row.spatial_fdr <= 1
    assert receipt == fit_neighborhood_da(embedding, metadata, spec)


def test_null_probabilities_have_roughly_uniform_calibration(tmp_path: Path):
    receipt = fit_neighborhood_da(*_inputs(tmp_path))
    p_values = np.asarray(
        [row.p_value for row in receipt.neighborhoods if row.p_value is not None]
    )
    assert len(p_values) >= 60
    # Neighborhoods overlap, so treat this as a descriptive calibration
    # check. An independent-sample uniformity test is inappropriate here.
    assert 0.30 < np.median(p_values) < 0.70
    assert np.mean(p_values < 0.05) < 0.13
    assert np.mean(p_values > 0.90) > 0.025


def test_neighborhood_without_both_arms_has_null_statistics(tmp_path: Path):
    receipt = fit_neighborhood_da(
        *_inputs(tmp_path, expansion=True, exclusive_region=True)
    )
    exclusive = [row for row in receipt.neighborhoods if row.center_coordinates[0] < 1]
    assert exclusive
    for row in exclusive:
        assert row.missing_reason == "condition_without_count_support"
        assert row.log2_fold_change is row.log2_standard_error is None
        assert row.p_value is row.q_value is row.spatial_fdr is None


@pytest.mark.parametrize(
    ("inputs", "error"),
    [
        ({"collapsed": True}, "zero_kth_neighbor_distance"),
        ({"donors_per_group": 2}, "at_least_three_donors"),
        ({"confounded": True}, "rank_deficient_design"),
    ],
)
def test_rejects_invalid_geometry_and_donor_design(tmp_path: Path, inputs, error: str):
    with pytest.raises(ValueError, match=error):
        fit_neighborhood_da(*_inputs(tmp_path, **inputs))


def test_weighted_fdr_matches_suffix_minimum_and_retains_failed_rows():
    # Sorted p-values are .10, .20, .30, and 1 (padded). The weighted
    # ratios are .40, .20*8/3, .30*8/7, and 1.
    weighted = _spatial_fdr([0.30, 0.10, None, 0.20], np.array([4.0, 2.0, 1.0, 1.0]))
    assert weighted == pytest.approx([0.30 * 8 / 7, 0.30 * 8 / 7, None, 0.30 * 8 / 7])


def test_revalidates_copied_specification_before_graph_fit(tmp_path: Path):
    embedding, metadata, spec = _inputs(tmp_path)
    with pytest.raises(ValueError, match="greater than or equal to 1"):
        fit_neighborhood_da(embedding, metadata, spec.model_copy(update={"k": 0}))
