"""Synthetic checks for staged, file-backed supplied-population pseudobulk."""

import hashlib

import h5py
import numpy as np
import pandas as pd
import pytest
from anndata import AnnData
from scipy import sparse

from devbench.core.population_contract import canonical_bytes
from devbench.platform import scrna_population_analysis as analysis
from devharness_core.storage import ChunkedExpressionStore
from tests.test_scrna_population_analysis import fixture


def test_binary_pseudobulk_covers_large_observed_profile_family(workspace_tmp_path):
    _, spec, _, _ = fixture(workspace_tmp_path)
    genes, donors = 20_000, 128
    records = [
        {
            "embryo": f"d{donor:03}",
            "type": population,
            "stage": "E1" if donor < 64 else "E2",
            "batch": f"batch{donor:03}",
            "capture": f"capture{donor:03}",
        }
        for donor in range(donors)
        for population in ("P", "N")
        if (donor, population) != (127, "N")
        for _ in range(2)
    ]
    cells = len(records)
    obs = pd.DataFrame(records, index=[f"cell{i}" for i in range(cells)])
    var = pd.DataFrame(index=[f"gene{i}" for i in range(genes)])
    data = AnnData(X=sparse.csr_matrix((cells, genes)), obs=obs, var=var)
    matrix = sparse.csr_matrix(
        (np.full(cells, 3), (np.arange(cells), np.arange(cells) // 2)),
        shape=(cells, genes),
    )
    data.layers["counts"] = matrix
    source = workspace_tmp_path / "large.h5ad"
    data.write_h5ad(source)
    with source.open("rb") as stream:
        spec["source_sha256"] = hashlib.file_digest(stream, "sha256").hexdigest()
    output = workspace_tmp_path / "pseudobulk.h5"

    summary = analysis.stage_population_pseudobulk_hdf5(source, spec, output)

    assert summary["matrix"]["shape"] == [genes, 255]
    assert summary["matrix"]["decoded_bytes"] == genes * 255 * 8 > 16 * 1024**2
    assert "counts" not in summary and "feature_ids" not in summary
    assert len(canonical_bytes(summary)) <= 1024**2
    with output.open("rb") as stream:
        assert (
            hashlib.file_digest(stream, "sha256").hexdigest()
            == summary["matrix"]["sha256"]
        )
    assert summary["missing_profile_count"] == 1
    assert summary["profile_library_sizes"] == [6] * 255
    with ChunkedExpressionStore(output) as store:
        assert store.shape == (genes, 255)
        np.testing.assert_array_equal(store.get_gene_vector("gene254"), [0] * 254 + [6])
        np.testing.assert_array_equal(store.get_gene_vector("gene19999"), np.zeros(255))
    with h5py.File(output, "r") as handle:
        assert handle["profiles"]["biological_unit"].asstr()[-1] == "d127"
        assert handle["profiles"]["population"].asstr()[-1] == "Progenitor"
    with pytest.raises(FileExistsError):
        analysis.stage_population_pseudobulk_hdf5(source, spec, output)
    population_output = workspace_tmp_path / "population.h5"
    population_summary = analysis.stage_population_pseudobulk_hdf5(
        source,
        spec,
        population_output,
        population="Progenitor",
    )
    assert population_summary["matrix"]["shape"] == [genes, 128]
    assert population_summary["missing_profile_count"] == 0
    with h5py.File(population_output, "r") as handle:
        assert len(set(handle["profiles/biological_unit"].asstr()[:])) == 128
        assert set(handle["profiles/population"].asstr()[:]) == {"Progenitor"}


def test_binary_pseudobulk_rejects_invalid_counts_without_publishing(
    workspace_tmp_path,
):
    source, spec, _, _ = fixture(workspace_tmp_path, bad=-1)
    output = workspace_tmp_path / "pseudobulk.h5"
    with pytest.raises(ValueError, match="nonnegative integers"):
        analysis.stage_population_pseudobulk_hdf5(source, spec, output)
    assert not output.exists()
