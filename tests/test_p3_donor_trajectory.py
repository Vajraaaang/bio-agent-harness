"""Synthetic checks with independent donor curves and bounded H5AD input."""

import csv
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from anndata import AnnData
from scipy.sparse import csr_matrix
from scipy.stats import f

from devbench.core._files import hash_bounded_file


def _cohort(root: Path, *, paired: bool = False, missing_bin: bool = False):
    from devbench.core.scrna_donor_trajectory import DonorTrajectorySpec

    root.mkdir(exist_ok=True)
    rng = np.random.default_rng(12)
    times = (np.arange(8) + 0.5) / 8
    shape = np.column_stack((times, times**2, times**3))
    null_curves = rng.normal(0, 0.05, (8, 3))
    cells: list[tuple[str, str, str, float, str]] = []
    values: list[tuple[float, float, float]] = []
    for arm, condition in enumerate(("control", "treated")):
        for index in range(8):
            donor = f"d{index}" if paired else f"d{arm}-{index}"
            slopes = rng.normal(0, 0.15, 3)
            for j, time in enumerate(times):
                if missing_bin and index == 0 and j == 0:
                    continue
                for _ in range(5):
                    signal = 4 + shape[j] @ slopes + arm * (2 * time + time**2)
                    null = 2 + shape[j] @ null_curves[index]
                    signal += rng.normal(0, 0.02)
                    null += rng.normal(0, 0.02)
                    cells.append(
                        (
                            f"cell-{len(cells)}",
                            donor,
                            condition,
                            time,
                            f"batch-{index % 2}",
                        )
                    )
                    values.append((signal, null, 4.2 * (index + 1) + arm))
    expression = root / "expression.h5ad"
    coordinates = root / "coordinates.csv"
    AnnData(
        X=csr_matrix(values),
        obs=pd.DataFrame(index=[row[0] for row in cells]),
        var=pd.DataFrame(index=["g_signal", "g_null", "g_constant"]),
    ).write_h5ad(expression)
    _write_coordinates(coordinates, cells)
    spec = DonorTrajectorySpec(
        expression_sha256=hash_bounded_file(expression, 10_000_000),
        coordinates_sha256=hash_bounded_file(coordinates, 10_000_000),
        original_counts_sha256="1" * 64,
        normalization_receipt_sha256="2" * 64,
        axis_receipt_sha256="3" * 64,
        axis_kind="inferred_pseudotime",
        axis_selection="independent",
        intent="confirmatory",
        measurement="normalized_expression",
        knot_count=2,
        bin_count=8,
        min_cells_per_bin=5,
        design="paired" if paired else "independent",
        numerator="treated",
        denominator="control",
        technical_factors=("batch",),
    )
    return expression, coordinates, spec, cells, np.asarray(values)


def _write_coordinates(path, cells):
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(("cell_id", "donor_id", "condition", "pseudotime", "batch"))
        writer.writerows(cells)


def _fit(expression, coordinates, spec):
    from devbench.core.scrna_donor_trajectory import fit_donor_trajectory

    return fit_donor_trajectory(
        expression,
        coordinates,
        spec,
        verify_inference=lambda supplied: supplied == spec,
    )


def test_independent_donor_spline_matches_multivariate_oracle(workspace_tmp_path):
    expression, coordinates, spec, _, values = _cohort(workspace_tmp_path)
    result = _fit(expression, coordinates, spec)
    json.dumps(result, allow_nan=False)
    rows = {row["gene_id"]: row for row in result["gene_statistics"]}
    n_donors = 16
    n_basis = 3
    time = (np.arange(8) + 0.5) / 8
    basis = np.vander(time, 4, increasing=True)
    signal = values[:, 0].reshape(n_donors, 8, 5).mean(axis=-1)
    donor_shapes = np.linalg.lstsq(basis, signal.T, rcond=None)[0][1:].T
    design = np.column_stack(
        (np.ones(n_donors), np.repeat([0, 1], 8), np.tile([0, 1], 8))
    )
    coefficients = np.linalg.lstsq(design, donor_shapes, rcond=None)[0]
    residuals = donor_shapes - design @ coefficients
    residual_df = n_donors - design.shape[1]
    cxx = np.linalg.inv(design.T @ design)[1, 1]
    t2 = float(
        coefficients[1]
        @ np.linalg.solve(residuals.T @ residuals * cxx / residual_df, coefficients[1])
    )
    denominator_df = residual_df - n_basis + 1
    probability = f.sf(
        t2 * denominator_df / (residual_df * n_basis), n_basis, denominator_df
    )
    assert result["n_donors"] == n_donors
    assert result["design_diagnostics"]["residual_df"] == residual_df
    assert result["bh_family_size"] == 3
    assert rows["g_signal"]["p"] == pytest.approx(probability, rel=1e-8)
    assert rows["g_signal"]["p"] < 0.01
    assert rows["g_signal"]["q"] == pytest.approx(3 * probability, rel=1e-8)
    assert rows["g_signal"]["effect"] > 2
    assert rows["g_null"]["q"] > 0.05
    assert rows["g_constant"]["p"] is rows["g_constant"]["q"] is None


def test_paired_donor_statistics_are_invariant_to_duplicating_cells(
    workspace_tmp_path,
):
    root = workspace_tmp_path
    expression, coordinates, spec, cells, values = _cohort(root, paired=True)
    baseline = _fit(expression, coordinates, spec)
    duplicate = root / "duplicate.h5ad"
    selection = [index for index, row in enumerate(cells) if row[1] == "d0"]
    duplicated_rows = [(f"additional-{i}", *cells[i][1:]) for i in selection]
    AnnData(
        X=np.vstack((values, values[selection])),
        obs=pd.DataFrame(index=[row[0] for row in [*cells, *duplicated_rows]]),
        var=pd.DataFrame(index=["g_signal", "g_null", "g_constant"]),
    ).write_h5ad(duplicate)
    _write_coordinates(coordinates, [*duplicated_rows, *cells])
    duplicate_spec = spec.model_copy(
        update={
            "expression_sha256": hash_bounded_file(duplicate, 10_000_000),
            "coordinates_sha256": hash_bounded_file(coordinates, 10_000_000),
        }
    )
    replicated = _fit(duplicate, coordinates, duplicate_spec)
    assert baseline["n_donors"] == replicated["n_donors"] == 8
    assert baseline["design_diagnostics"]["residual_df"] == 7
    for original, expanded in zip(
        baseline["gene_statistics"],
        replicated["gene_statistics"],
        strict=True,
    ):
        assert expanded["p"] == pytest.approx(original["p"], rel=1e-8)
        assert expanded["effect"] == pytest.approx(original["effect"], rel=1e-8)


def test_exploratory_selected_pseudotime_never_emits_probabilities(workspace_tmp_path):
    expression, coordinates, spec, _, _ = _cohort(workspace_tmp_path)
    selected = spec.model_copy(
        update={
            "axis_selection": "same_expression",
            "intent": "exploratory",
        }
    )
    result = _fit(expression, coordinates, selected)
    assert result["claim_scope"] == "exploratory_only"
    assert result["gene_statistics"][0]["effect"] > 2
    assert all(row["p"] is row["q"] is None for row in result["gene_statistics"])


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"method": "tradeSeq"}, "cell_level_tradeSeq_refused"),
        ({"axis_kind": "collection_age"}, "inferred_pseudotime_required"),
        ({"measurement": "raw_counts"}, "normalized_expression_required"),
        ({"measurement": "rounded_counts"}, "normalized_expression_required"),
        ({"axis_selection": "same_expression"}, "independent_axis_evidence_required"),
        ({"coordinates_sha256": "9" * 64}, "trajectory_coordinates_digest_mismatch"),
    ],
)
def test_trajectory_scope_and_provenance_refuse_unsupported_inputs(
    workspace_tmp_path,
    changes,
    reason,
):
    expression, coordinates, spec, _, _ = _cohort(workspace_tmp_path)
    with pytest.raises(ValueError, match=reason):
        _fit(expression, coordinates, spec.model_copy(update=changes))


def test_confirmatory_needs_trusted_precommit_and_axis_evidence(workspace_tmp_path):
    from devbench.core.scrna_donor_trajectory import fit_donor_trajectory

    expression, coordinates, spec, _, _ = _cohort(workspace_tmp_path)
    with pytest.raises(ValueError, match="trajectory_inference_authorization_required"):
        fit_donor_trajectory(expression, coordinates, spec)


def test_missing_pseudotime_bin_refuses_instead_of_excluding_donors(
    workspace_tmp_path,
):
    expression, coordinates, spec, _, _ = _cohort(
        workspace_tmp_path,
        missing_bin=True,
    )
    with pytest.raises(
        ValueError, match="insufficient_within_donor_pseudotime_support"
    ):
        _fit(expression, coordinates, spec)


def test_trajectory_rejects_condition_completely_confounded_with_batch(
    workspace_tmp_path,
):
    expression, coordinates, spec, cells, _ = _cohort(workspace_tmp_path)
    _write_coordinates(
        coordinates,
        [
            (barcode, donor, group, time, group)
            for barcode, donor, group, time, _ in cells
        ],
    )
    amended = spec.model_copy(
        update={
            "coordinates_sha256": hash_bounded_file(coordinates, 10_000_000),
        }
    )
    with pytest.raises(ValueError, match="rank_deficient_design"):
        _fit(expression, coordinates, amended)


def test_one_donor_cannot_be_counted_as_independent_in_both_arms(workspace_tmp_path):
    expression, coordinates, spec, _, _ = _cohort(workspace_tmp_path, paired=True)
    with pytest.raises(ValueError, match="repeated_donors_require_paired_design"):
        _fit(expression, coordinates, spec.model_copy(update={"design": "independent"}))


def test_trajectory_does_not_extrapolate_end_bin_centers(workspace_tmp_path):
    expression, coordinates, spec, cells, _ = _cohort(workspace_tmp_path)
    _write_coordinates(
        coordinates,
        [
            (cell, donor, group, time + 0.01, batch)
            for cell, donor, group, time, batch in cells
        ],
    )
    amended = spec.model_copy(
        update={
            "coordinates_sha256": hash_bounded_file(coordinates, 10_000_000),
        }
    )
    with pytest.raises(
        ValueError, match="trajectory_contrast_requires_observed_support"
    ):
        _fit(expression, coordinates, amended)


def test_integer_storage_cannot_be_fit_as_continuous_measurement(workspace_tmp_path):
    from anndata import read_h5ad

    expression, coordinates, spec, _, _ = _cohort(workspace_tmp_path)
    replacement = read_h5ad(expression)
    replacement.X = replacement.X.astype(np.int64)
    replacement.write_h5ad(expression)
    amended = spec.model_copy(
        update={
            "expression_sha256": hash_bounded_file(expression, 10_000_000),
        }
    )
    with pytest.raises(
        ValueError, match="trajectory_requires_continuous_float_storage"
    ):
        _fit(expression, coordinates, amended)
