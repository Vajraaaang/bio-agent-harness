"""Synthetic probes of the generalized mounted request contract."""

import json

import numpy as np
import pytest

from rna_kernels.mounted_bulk import run_mounted_bulk
from tests.test_mounted_bulk import _build, _digest


def test_mounted_multilevel_gaussian_omnibus_has_no_signed_effect(workspace_tmp_path):
    import pyarrow.parquet as pq

    inputs = workspace_tmp_path / "inputs"
    outputs = workspace_tmp_path / "outputs"
    inputs.mkdir()
    outputs.mkdir()
    request = _build(inputs, "h5", 24)
    metadata = inputs / "metadata.csv"
    metadata.write_text(
        metadata.read_text()
        .replace("s200,d200,case", "s200,d200,other")
        .replace("s201,d201,case", "s201,d201,other")
        .replace("s202,d202,case", "s202,d202,other")
    )
    config = json.loads(request.read_text())
    config["metadata_sha256"] = _digest(metadata)
    config["model"] = {
        "comparison": "nested",
        "units": "independent",
        "reduced_terms": ["batch"],
    }
    request.write_text(json.dumps(config))
    summary = run_mounted_bulk(inputs, outputs, _digest(request))
    results = pq.read_table(outputs / "genes.parquet").to_pylist()
    assert summary["comparison"] == "nested"
    assert summary["test_df"] == 2
    assert all(row["effect"] is None and row["values"] == [] for row in results)
    assert all(0 <= row["p_value"] <= row["q_value"] <= 1 for row in results)
    _assert_rank_not_applicable(summary, outputs, 256)


def test_mounted_explicit_condition_contrast_has_frozen_direction(workspace_tmp_path):
    from rna_kernels.mounted_model import compile_mounted_model

    request = {
        "numerator": "case",
        "denominator": "control",
        "unit_column": "donor",
        "condition_column": "condition",
        "technical_factors": [],
        "continuous_covariates": [],
        "model": {
            "comparison": "contrast",
            "units": "independent",
            "condition_weights": {"case": 1, "control": -0.5, "other": -0.5},
        },
    }
    rows = [
        dict(condition=group, donor=str(i))
        for i, group in enumerate(["other", "control", "case"] * 4)
    ]
    cohort = compile_mounted_model(rows, request)
    assert cohort.design.shape == (12, 3)
    np.testing.assert_array_equal(cohort.contrast, [0, 1, -0.5])
    request["model"]["condition_weights"] = {"case": 1, "control": -1}
    with pytest.raises(ValueError, match="observed condition levels"):
        compile_mounted_model(rows, request)


@pytest.mark.parametrize(
    ("measurement", "comparison", "units", "primary", "solver", "latent", "arm_size"),
    [
        ("raw_counts", "lrt", "independent", True, "edger_v4", None, 52),
        (
            "tximport_estimated_counts",
            "coefficient",
            "independent",
            True,
            "edger_v4",
            None,
            52,
        ),
        ("raw_counts", "contrast", "independent", True, "edger_v4", None, 52),
        ("raw_counts", "contrast", "independent", False, "deseq2", None, 6),
        ("raw_counts", "lrt", "independent", False, "deseq2", None, 6),
        ("raw_counts", "coefficient", "paired_fixed", True, "edger_v4", None, 100),
        ("continuous", "coefficient", "mixed", True, "dream", None, 100),
        ("raw_counts", "coefficient", "mixed", True, "dream", None, 100),
        ("raw_counts", "coefficient", "independent", True, "edger_v4", "ruvg", 6),
        ("raw_counts", "coefficient", "independent", True, "edger_v4", "svaseq", 6),
    ],
)
def test_generalized_mounted_bioconductor_dispatch(
    workspace_tmp_path,
    measurement,
    comparison,
    units,
    primary,
    solver,
    latent,
    arm_size,
):
    import os

    import pyarrow.parquet as pq

    from tests.test_mounted_kernel_oci import _run_worker

    if not os.environ.get("DEVBENCH_RNA_KERNEL_TEST_IMAGE"):
        pytest.skip("pinned local RNA kernel image required")
    inputs = workspace_tmp_path / "inputs"
    outputs = workspace_tmp_path / "outputs"
    inputs.mkdir()
    outputs.mkdir()
    levels = (
        ("reference", "case", "other")
        if comparison in {"lrt", "contrast"}
        else ("reference", "case")
    )
    clusters = [(level, j) for level in levels for j in range(arm_size)]
    if units == "mixed":
        clusters.extend([("case", 0), ("reference", 1)])
    ids = [
        str(i) if units == "independent" else str(j)
        for i, (_, j) in enumerate(clusters)
    ]
    samples = [f"s{i}" for i in range(len(ids))]
    genes = [f"g{i}" for i in range(64)]
    rng = np.random.default_rng(437)
    counts = rng.negative_binomial(20, 0.25, size=(len(genes), len(samples))).astype(
        float
    )
    target = np.array([level == "case" for level, _ in clusters])
    counts[:6, target] *= 4
    if measurement == "continuous":
        counts = np.log2(counts + 1)
    if measurement == "tximport_estimated_counts":
        counts += 0.1
    config = {
        "format": "devbench.mounted-bulk/1",
        "expression": "expression.h5",
        "measurement": measurement,
        "biological_unit": "donor",
        "unit_column": "donor",
        "condition_column": "condition",
        "technical_factors": [],
        "layer": None,
        "numerator": "case",
        "denominator": "reference",
    }
    expression = inputs / config["expression"]
    _write_h5(expression, counts, genes, samples)
    metadata = inputs / "metadata.csv"
    metadata.write_text(
        "sample_id,donor,condition\n"
        + "".join(
            f"{sample},{unit},{level}\n"
            for sample, unit, (level, _) in zip(samples, ids, clusters, strict=True)
        )
    )
    family = inputs / "family.json"
    family.write_text(
        json.dumps(
            {
                "family_id": "synthetic-kernel",
                "feature_ids": genes,
                "primary": primary,
                "numerator": "case",
                "denominator": "reference",
            }
        )
    )
    config.update(
        expression_sha256=_digest(expression),
        metadata_sha256=_digest(metadata),
        family_sha256=_digest(family),
    )
    model = {"comparison": comparison, "units": units}
    if comparison == "contrast":
        model["condition_weights"] = {"case": 1, "other": -0.5, "reference": -0.5}
    if measurement == "tximport_estimated_counts":
        lengths = inputs / "lengths.h5"
        _write_h5(lengths, rng.uniform(200, 1000, counts.shape), genes, samples)
        model["lengths_sha256"] = _digest(lengths)
    if latent == "ruvg":
        model["latent"] = {
            "method": "ruvg",
            "n_factors": 1,
            "controls": {
                "origin": "spike_ins",
                "scope": "independent",
                "source": "synthetic fixture",
                "gene_ids": genes[20:40],
            },
        }
    elif latent == "svaseq":
        model["latent"] = {"method": latent, "sv_method": "leek"}
    config["model"] = model
    request = inputs / "request.json"
    request.write_text(json.dumps(config))
    _, summary = _run_worker(inputs, outputs, request)
    rows = pq.read_table(outputs / "genes.parquet").to_pylist()
    _assert_kernel_result(summary, rows, model, solver, measurement)
    n_units = len(set(ids))
    if n_units >= 100:
        _assert_rank_not_applicable(summary, outputs, n_units)
    else:
        assert "rank_sensitivity" not in summary


def _assert_rank_not_applicable(summary, outputs, n_units):
    rank = summary["rank_sensitivity"]
    assert rank["status"] == "not_applicable" and rank["n_units"] == n_units
    assert rank["reason"] and not rank.get("hold") and "artifact_sha256" not in rank
    assert not (outputs / "sensitivity.parquet").exists()


def _assert_kernel_result(summary, rows, model, solver, measurement):
    comparison = model["comparison"]
    assert summary["solver"] == solver
    assert summary["comparison"] == comparison
    assert summary["tested_count"] > 0
    observed = [row for row in rows if row["estimate_status"] == "estimated"]
    assert all(0 <= row["p_value"] <= row["q_value"] <= 1 for row in observed)
    if comparison == "lrt":
        assert summary["test_df"] == 2
        assert all(row["effect"] is None for row in observed)
    else:
        assert any(row["effect"] > 0 for row in observed[:6])
    if "latent" in model:
        assert summary["latent_diagnostics"]["method"] == model["latent"]["method"]
        assert summary["latent_diagnostics"]["diagnostic_only"] is True
    if measurement == "tximport_estimated_counts":
        assert (
            summary["normalization"]["method"]
            == "tximport_effective_length_and_TMM_2D_offset"
        )


def _write_h5(path, data, genes, samples):
    import h5py

    string = h5py.string_dtype()
    with h5py.File(path, "w") as handle:
        handle.create_dataset("gene_names", data=genes, dtype=string)
        handle.create_dataset("sample_names", data=samples, dtype=string)
        handle.create_dataset("expression", data=data)
