"""Opt-in scale checks against an explicitly pinned local mounted-kernel image."""

import csv
import json
import os
import re
from uuid import uuid4

import h5py
import numpy as np
import pyarrow.parquet as pq
import pytest

from devharness_core.provenance import canonical_json, hash_artifact

_IMAGE = os.environ.get("DEVBENCH_RNA_KERNEL_TEST_IMAGE", "")
pytestmark = pytest.mark.skipif(
    not _IMAGE, reason="Set the exact local RNA kernel image ID"
)


def _bulk_matrix(path, counts, genes, samples):
    string = h5py.string_dtype()
    with h5py.File(path, "w") as handle:
        handle.create_dataset("gene_names", data=genes, dtype=string)
        handle.create_dataset("sample_names", data=samples, dtype=string)
        handle.create_dataset("expression", data=counts, chunks=(256, len(samples)))
    return [
        (sample, sample, "E1" if index < len(samples) // 2 else "E2")
        for index, sample in enumerate(samples)
    ]


def _pseudobulk_matrix(path, counts, genes, samples):
    import pandas as pd
    from anndata import AnnData
    from scipy.sparse import csr_matrix

    from devbench.platform.scrna_population_analysis import (
        stage_population_pseudobulk_hdf5,
    )
    from tests.test_scrna_population_analysis import fixture

    cohort = path.parent.parent
    _, specification, _, _ = fixture(cohort)
    rows = [
        {
            "embryo": sample,
            "type": label,
            "stage": "E1" if index < 128 else "E2",
            "batch": "batch-" + sample,
            "capture": "capture-" + sample,
        }
        for index, sample in enumerate(samples)
        for label in ("P", "N")
    ]
    values = np.empty((len(rows), len(genes)), dtype=np.int64)
    values[::2] = counts.T
    values[1::2] = 2
    source = cohort / "scale.h5ad"
    dataset = AnnData(
        X=csr_matrix(values.shape),
        obs=pd.DataFrame(rows, index=[f"cell{index}" for index in range(len(rows))]),
        var=pd.DataFrame(index=genes),
    )
    dataset.layers["counts"] = values
    dataset.write_h5ad(source, compression="gzip")
    specification["source_sha256"] = hash_artifact(source)["sha256"]
    staged = stage_population_pseudobulk_hdf5(
        source, specification, path, population="Progenitor"
    )
    assert staged["matrix"]["shape"] == [len(genes), len(samples)]
    with h5py.File(path, "r") as handle:
        return list(
            zip(
                handle["sample_names"].asstr()[:],
                handle["profiles/biological_unit"].asstr()[:],
                handle["profiles/stage"].asstr()[:],
                strict=True,
            )
        )


def _run_worker(inputs, outputs, request):
    from devbench.core.execution_contracts import ResourceProfile
    from devbench.engine.mounted_kernel_oci import run_mounted_container

    digest = hash_artifact(request)["sha256"]
    profile = ResourceProfile(
        cpus=4, memory_mb=4096, pids_limit=128, scratch_mb=256, timeout_sec=450
    )
    proof = run_mounted_container(
        inputs.parent,
        image_id=_IMAGE,
        request_sha=digest,
        attempt_id=uuid4().hex,
        resource_profile=profile,
        data_classification="synthetic",
    )
    assert (
        proof["cleanup_confirmed"] and proof["oci_isolated"] and not proof["error_type"]
    ), proof
    return digest, json.loads((outputs / "summary.json").read_bytes())


@pytest.mark.parametrize(
    ("kind", "features"),
    [
        ("bulk", 20_000),
        ("pseudobulk", 20_000),
        ("bulk", 60_000),
        ("pseudobulk", 60_000),
    ],
)
def test_mounted_count_scale_in_pinned_container(workspace_tmp_path, kind, features):
    assert re.fullmatch(r"sha256:[a-f0-9]{64}", _IMAGE), "Use a local image content ID"
    inputs = workspace_tmp_path / "stage" / "inputs"
    inputs.mkdir(parents=True)
    outputs = inputs.parent / "outputs"
    outputs.mkdir()
    counts = np.random.default_rng(913).negative_binomial(14, 0.4, (features, 256))
    counts[:40, 128:] *= 2
    counts[0, :128] = 0
    genes = [f"g{i:05}" for i in range(len(counts))]
    samples = [f"d{i:03}" for i in range(counts.shape[1])]
    expression = inputs / "expression.h5"
    build = _bulk_matrix if kind == "bulk" else _pseudobulk_matrix
    rows = build(expression, counts, genes, samples)
    metadata = inputs / "metadata.csv"
    with metadata.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(("sample_id", "unit", "condition"))
        writer.writerows(rows)
    family = inputs / "family.json"
    family.write_bytes(
        canonical_json(
            {
                "family_id": "synthetic-scale",
                "feature_ids": genes,
                "primary": True,
                "numerator": "E2",
                "denominator": "E1",
            }
        )
    )
    request = inputs / "request.json"
    request.write_bytes(
        canonical_json(
            {
                "format": "devbench.mounted-bulk/1",
                "expression": expression.name,
                "expression_sha256": hash_artifact(expression)["sha256"],
                "metadata_sha256": hash_artifact(metadata)["sha256"],
                "family_sha256": hash_artifact(family)["sha256"],
                "measurement": "raw_counts",
                "layer": None,
                "biological_unit": "organism",
                "unit_column": "unit",
                "condition_column": "condition",
                "technical_factors": [],
                "numerator": "E2",
                "denominator": "E1",
            }
        )
    )
    digest, summary = _run_worker(inputs, outputs, request)
    assert summary["request_sha256"] == digest
    assert summary["row_count"] == len(genes)
    assert summary["genes_sha256"] == hash_artifact(outputs / "genes.parquet")["sha256"]
    rank = summary["rank_sensitivity"]
    assert rank["method"] == "wilcoxon" and rank["hold"] is None
    assert (
        rank["artifact_sha256"]
        == hash_artifact(outputs / "sensitivity.parquet")["sha256"]
    )
    table = pq.read_table(outputs / "genes.parquet")
    assert table["gene_id"].to_pylist() == genes
    if summary.get("regime_route"):
        assert summary["solver"] == "limma_voom_qw"
        assert summary["regime_route"] == "large_or_heterogeneous_independent"
        assert summary["sensitivity_solver"] == "edger_v4"
        assert summary["tested_count"] == len(genes)
        assert table["estimate_status"][0].as_py() == "estimated"
        assert table["effect"][0].as_py() > 0.85
    else:
        assert summary["solver"] == "edger_v4_qlf"
        assert summary["tested_count"] == len(genes) - 1
        assert table["missing_reason"][0].as_py() == "zero_condition_marginal"
    assert np.median(table["effect"].slice(1, 39).to_numpy()) > 0.85
    assert np.all(
        table["q_value"].slice(1).to_numpy() >= table["p_value"].slice(1).to_numpy()
    )


def test_mounted_count_and_rank_adjust_for_continuous_confounding(workspace_tmp_path):
    from scipy.stats import mannwhitneyu

    rng = np.random.default_rng(217)
    inputs = workspace_tmp_path / "stage" / "inputs"
    inputs.mkdir(parents=True)
    outputs = inputs.parent / "outputs"
    outputs.mkdir()
    samples = [f"s{i:03}" for i in range(128)]
    genes = [f"g{i:05}" for i in range(400)]
    age = rng.normal(size=len(samples)) + np.repeat([0, 1.4], 64)
    mean = np.full((len(genes), len(samples)), 45.0)
    mean[:20, 64:] *= 2
    mean[20:80] *= np.exp(0.7 * age)
    counts = rng.negative_binomial(60, 60 / (60 + mean))
    expression = inputs / "expression.h5"
    rows = _bulk_matrix(expression, counts, genes, samples)
    metadata = inputs / "metadata.csv"
    with metadata.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(("sample_id", "unit", "condition", "age"))
        writer.writerows([(*row, value) for row, value in zip(rows, age, strict=True)])
    family = inputs / "family.json"
    family.write_bytes(
        canonical_json(
            {
                "family_id": "synthetic-age-adjustment",
                "feature_ids": genes,
                "primary": True,
                "numerator": "E2",
                "denominator": "E1",
            }
        )
    )
    request = inputs / "request.json"
    request.write_bytes(
        canonical_json(
            {
                "format": "devbench.mounted-bulk/1",
                "expression": expression.name,
                "expression_sha256": hash_artifact(expression)["sha256"],
                "metadata_sha256": hash_artifact(metadata)["sha256"],
                "family_sha256": hash_artifact(family)["sha256"],
                "measurement": "raw_counts",
                "layer": None,
                "biological_unit": "organism",
                "unit_column": "unit",
                "condition_column": "condition",
                "technical_factors": [],
                "continuous_covariates": ["age"],
                "numerator": "E2",
                "denominator": "E1",
            }
        )
    )
    _, summary = _run_worker(inputs, outputs, request)
    if summary.get("regime_route"):
        assert summary["solver"] == "limma_voom_qw"
        assert summary["regime_route"] == "large_or_heterogeneous_independent"
    else:
        assert summary["solver"] == "edger_v4_qlf"
    rank = summary["rank_sensitivity"]
    assert rank["method"] == "freedman_lane" and rank["hold"] is None
    adjusted = pq.read_table(outputs / "sensitivity.parquet")
    primary = pq.read_table(outputs / "genes.parquet")
    pooled = mannwhitneyu(counts[:, 64:], counts[:, :64], axis=1).pvalue
    assert np.median(pooled[20:80]) < 1e-5
    assert np.median(adjusted["p_value"].slice(20, 60).to_numpy()) > 0.15
    assert np.median(primary["p_value"].slice(20, 60).to_numpy()) > 0.15
    assert np.median(adjusted["p_value"].slice(0, 20).to_numpy()) <= 0.01
    assert np.median(primary["p_value"].slice(0, 20).to_numpy()) < 0.001
