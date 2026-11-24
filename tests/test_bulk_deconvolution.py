from hashlib import sha256
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from pydantic import ValidationError

from devbench.core._hashing import canonical_sha256
from devbench.core.bulk_deconvolution import (
    BulkDeconvolutionSpec,
    fit_bulk_deconvolution,
)


def _write(frame: pd.DataFrame, path: Path) -> str:
    frame.to_csv(path, index=False)
    return sha256(path.read_bytes()).hexdigest()


def _mixture_files(
    root: Path,
    *,
    one_donor: bool = False,
    zero_variance: bool = False,
    add_outliers: bool = False,
    exclude_ribosomal: bool = False,
    cpm: bool = False,
):
    left = np.array([60, 10, 20, 20, 9, 11])
    right = np.array([10, 140, 40, 20, 50, 40])
    shift = np.array([5, -5, 0, 0, 0, 0])
    genes = ["g1", "g2", "g3", "g4", "g5", "g6"]
    profiles = {
        "cell2": left - shift,
        "cell1": left + shift,
        "cell3": right - shift,
        "cell4": right + shift,
    }
    if zero_variance:
        profiles.update(cell1=left, cell2=left, cell3=right, cell4=right)
    reference = pd.DataFrame({"gene_id": genes, **profiles})
    labels = pd.DataFrame(
        {
            "cell_id": ["cell4", "cell2", "cell1", "cell3"],
            "donor_id": ["D2", "D2", "D1", "D1"] if not one_donor else ["D1"] * 4,
            "cell_type": ["right", "left", "left", "right"],
        }
    )
    bulk = pd.DataFrame(
        {"gene_id": genes, "sample": left * 3 + right, "null": right * 4}
    ).iloc[::-1]
    if add_outliers:
        bulk = pd.concat(
            [
                bulk.assign(chromosome="chr1"),
                pd.DataFrame(
                    [
                        ["MT-CO1", 900, 111, "chrM"],
                        ["MALAT1", 700, 222, "chr11"],
                        ["rRNA_mito_annotation", 500, 333, "MT"],
                    ],
                    columns=bulk.columns.to_list() + ["chromosome"],
                ),
            ],
            ignore_index=True,
        )
        reference = pd.concat(
            [
                reference.assign(chromosome="chr1"),
                pd.DataFrame(
                    [
                        ["MT-CO1", 2000, 10, 20, 40, "chrM"],
                        ["MALAT1", 1000, 20, 40, 80, "chr11"],
                        ["rRNA_mito_annotation", 3000, 30, 60, 100, "MT"],
                    ],
                    columns=reference.columns.to_list() + ["chromosome"],
                ),
            ],
            ignore_index=True,
        )
    if exclude_ribosomal:
        bulk = pd.concat(
            [bulk, pd.DataFrame({"gene_id": ["RPS3"], "sample": [1], "null": [2]})],
            ignore_index=True,
        ).fillna("")
        reference = pd.concat(
            [
                reference,
                pd.DataFrame(
                    {
                        "gene_id": ["RPS3"],
                        "cell1": [11],
                        "cell2": [13],
                        "cell3": [17],
                        "cell4": [19],
                    }
                ),
            ],
            ignore_index=True,
        ).fillna("")
    if cpm:
        for sample in ("sample", "null"):
            bulk[sample] = bulk[sample] / bulk[sample].sum() * 1_000_000
    paths = tuple(root / name for name in ("bulk.csv", "reference.csv", "cells.csv"))
    hashes = [
        _write(table, path)
        for table, path in zip(
            (bulk, reference, labels),
            paths,
            strict=True,
        )
    ]
    spec = BulkDeconvolutionSpec(
        bulk_sha256=hashes[0],
        reference_sha256=hashes[1],
        metadata_sha256=hashes[2],
        bulk_measurement="cpm" if cpm else "counts",
        reference_measurement="raw_counts",
        min_shared_genes=4,
        exclude_ribosomal=exclude_ribosomal,
    )
    return paths, spec, bulk, reference


@pytest.mark.parametrize("cpm", [False, True])
def test_planted_cell_proportions_and_zero_component(tmp_path, cpm):
    paths, spec, _, _ = _mixture_files(tmp_path, cpm=cpm)
    result = fit_bulk_deconvolution(*paths, spec)
    samples = {sample["sample_id"]: sample for sample in result["samples"]}
    mixed = samples["sample"]
    assert mixed["proportions"] == pytest.approx({"left": 0.75, "right": 0.25})
    assert samples["null"]["proportions"] == pytest.approx({"left": 0.0, "right": 1.0})
    assert mixed["rmse"] < 1e-12
    assert mixed["pearson_r"] == pytest.approx(1.0)
    assert mixed["spearman_r"] == pytest.approx(1.0)
    assert result["quantity"] == "relative_cell_type_proportions"
    assert result["clinical_release_allowed"] is False
    assert result["receipt_sha256"] == canonical_sha256(
        {key: value for key, value in result.items() if key != "receipt_sha256"}
    )


def test_outlier_audit_before_reference_library_size_fit(tmp_path):
    paths, spec, bulk, reference = _mixture_files(
        tmp_path, add_outliers=True, exclude_ribosomal=True
    )
    result = fit_bulk_deconvolution(*paths, spec)
    assert result["reference_average_library_size"] == {
        "left": 130.0,
        "right": 300.0,
    }
    audit = result["outlier_audit"]
    removed = ["MALAT1", "MT-CO1", "RPS3", "rRNA_mito_annotation"]
    assert audit["removed_bulk_gene_ids"] == removed
    assert audit["removed_reference_gene_ids"] == removed
    for expected, table, columns in (
        (audit["bulk_removed_fraction"], bulk, ["sample", "null"]),
        (
            audit["reference_removed_fraction"],
            reference,
            ["cell1", "cell2", "cell3", "cell4"],
        ),
    ):
        total = float(table[columns].to_numpy().sum())
        assert (
            expected
            == float(table.loc[table.gene_id.isin(removed), columns].to_numpy().sum())
            / total
        )
    assert result["samples"][0]["proportions"] == pytest.approx(
        {"left": 0.75, "right": 0.25}
    )


def test_zero_variance_is_finite(tmp_path):
    paths, spec, _, _ = _mixture_files(tmp_path, zero_variance=True)
    result = fit_bulk_deconvolution(*paths, spec)
    assert result["zero_cross_donor_variance_genes"] == 6
    assert result["variance_floor"] > 0
    for row in result["samples"]:
        assert sum(row["proportions"].values()) == pytest.approx(1.0)
        assert all(0.0 <= value <= 1.0 for value in row["proportions"].values())
        assert np.isfinite([row["rmse"], row["pearson_r"], row["spearman_r"]]).all()


def test_refuses_log_input(tmp_path):
    paths, spec, bulk, _ = _mixture_files(tmp_path)
    for field in ("bulk_measurement", "reference_measurement"):
        with pytest.raises(ValidationError, match=field):
            BulkDeconvolutionSpec.model_validate(
                {**spec.model_dump(), field: "log1p_normalized_expression"}
            )
    bulk["sample"] = np.log1p(bulk["sample"])
    changed = spec.model_copy(update={"bulk_sha256": _write(bulk, paths[0])})
    with pytest.raises(ValueError, match="integer_counts"):
        fit_bulk_deconvolution(*paths, changed)


def test_refuses_one_reference_donor_even_with_multiple_cells(tmp_path):
    paths, spec, _, _ = _mixture_files(tmp_path, one_donor=True)
    with pytest.raises(ValueError, match="at_least_two_reference_donors"):
        fit_bulk_deconvolution(*paths, spec)


def test_refuses_insufficient_shared_genes(tmp_path):
    paths, spec, bulk, _ = _mixture_files(tmp_path)
    bulk = bulk.head(3)
    changed = spec.model_copy(update={"bulk_sha256": _write(bulk, paths[0])})
    with pytest.raises(ValueError, match="insufficient_shared_genes"):
        fit_bulk_deconvolution(*paths, changed)


def test_refuses_changed_input_bytes(tmp_path):
    paths, spec, bulk, _ = _mixture_files(tmp_path)
    _write(bulk.iloc[:3], paths[0])
    with pytest.raises(ValueError, match="digest"):
        fit_bulk_deconvolution(*paths, spec)


def test_zero_bulk_sample_is_unestimable(tmp_path):
    paths, spec, bulk, _ = _mixture_files(tmp_path)
    bulk["null"] = 0
    changed = spec.model_copy(update={"bulk_sha256": _write(bulk, paths[0])})
    result = fit_bulk_deconvolution(*paths, changed)
    sample = next(s for s in result["samples"] if s["sample_id"] == "null")
    assert sample["status"] == "not_estimable"
    assert sample["proportions"] is None
    assert sample["rmse"] is None
    assert sample["pearson_r"] is None
    assert sample["spearman_r"] is None


def test_cross_donor_variance_downweights_noisy_genes(tmp_path):
    paths, spec, bulk, _ = _mixture_files(tmp_path)
    bulk = bulk.drop(columns="null")
    bulk.loc[bulk.gene_id == "g1", "sample"] += 160
    bulk.loc[bulk.gene_id == "g2", "sample"] -= 160
    changed = spec.model_copy(update={"bulk_sha256": _write(bulk, paths[0])})
    (sample,) = fit_bulk_deconvolution(*paths, changed)["samples"]
    assert sample["iterations"] > 1
    assert sample["proportions"] == pytest.approx(
        {"left": 0.75, "right": 0.25}, abs=1e-4
    )
    limited = changed.model_copy(update={"max_iterations": 1})
    (failed,) = fit_bulk_deconvolution(*paths, limited)["samples"]
    assert failed["missing_reason"] == "reweighted_nnls_nonconverged"
    assert failed["proportions"] is None


@pytest.mark.parametrize("outlier_gene", ["g1", "g2"])
def test_single_bulk_outlier_is_damped_and_reported(tmp_path, outlier_gene):
    paths, spec, bulk, _ = _mixture_files(tmp_path, zero_variance=True)
    bulk.loc[bulk.gene_id == outlier_gene, "sample"] += 264
    changed = spec.model_copy(update={"bulk_sha256": _write(bulk, paths[0])})
    sample = fit_bulk_deconvolution(*paths, changed)["samples"][0]
    assert sample["status"] == "estimated"
    assert abs(sample["proportions"]["left"] - 0.75) < 0.03
    assert (
        sample["residual_diagnostics"]["largest_absolute_residual_gene"] == outlier_gene
    )
    assert sample["residual_diagnostics"]["weighted_rmse"] < sample["rmse"]


def test_reference_library_size_includes_unmatched_retained_genes(tmp_path):
    paths, spec, _, reference = _mixture_files(tmp_path)
    reference.loc[len(reference)] = ["reference_only", 80, 80, 20, 20]
    changed = spec.model_copy(update={"reference_sha256": _write(reference, paths[1])})
    result = fit_bulk_deconvolution(*paths, changed)
    assert result["reference_average_library_size"] == {"left": 210.0, "right": 320.0}
    assert "reference_only" not in result["retained_shared_gene_ids"]
    assert result["reconstruction_scale"] == (
        "unit_sum_relative_expression_on_retained_shared_genes"
    )
    assert result["samples"][0]["proportions"] == pytest.approx(
        {"left": 0.75, "right": 0.25}
    )
