"""Synthetic mounted-kernel contracts, with no external source data."""

import hashlib
import json

import h5py
import numpy as np
import pyarrow.parquet as pq
import pytest
import zarr

from devharness_core.provenance import hash_artifact


def _digest(path):
    return hash_artifact(path)["sha256"]


def _build(root, suffix, genes):
    samples = [f"s{i}" for i in range(256)]
    labels = [f"g{i:05}" for i in range(genes)]
    source = root / f"expression.{suffix}"
    rng = np.random.default_rng(9)
    if suffix == "h5ad":
        import pandas as pd
        from anndata import AnnData

        matrix = rng.normal(size=(len(samples), genes))
        matrix[128:, :] += 1
        AnnData(
            matrix, obs=pd.DataFrame(index=samples), var=pd.DataFrame(index=labels)
        ).write_h5ad(source)
    else:
        owner = (
            h5py.File(source, "w")
            if suffix == "h5"
            else zarr.open_group(str(source), mode="w")
        )
        try:
            dtype = h5py.string_dtype() if suffix == "h5" else "<U12"
            owner.create_dataset("gene_names", data=np.asarray(labels, dtype=dtype))
            owner.create_dataset("sample_names", data=np.asarray(samples, dtype=dtype))
            array = owner.create_dataset(
                "expression",
                shape=(genes, len(samples)),
                dtype="f8",
                chunks=(min(genes, 256), len(samples)),
            )
            for start in range(0, genes, 256):
                block = rng.normal(size=(min(256, genes - start), len(samples)))
                block[:, 128:] += 1
                array[start : start + len(block)] = block
        finally:
            if suffix == "h5":
                owner.close()
    metadata = root / "metadata.csv"
    metadata.write_text(
        "sample_id,donor,condition,batch\n"
        + "".join(
            f"{sample},d{i},{'control' if i < 128 else 'case'},b{i % 2}\n"
            for i, sample in reversed(list(enumerate(samples)))
        )
    )
    family = root / "family.json"
    family.write_text(
        json.dumps(
            {
                "family_id": "case-control",
                "feature_ids": labels,
                "numerator": "case",
                "denominator": "control",
                "primary": True,
            }
        )
    )
    request = root / "request.json"
    request.write_text(
        json.dumps(
            {
                "format": "devbench.mounted-bulk/1",
                "expression": source.name,
                "expression_sha256": _digest(source),
                "metadata_sha256": _digest(metadata),
                "family_sha256": _digest(family),
                "measurement": "continuous",
                "layer": None,
                "biological_unit": "donor",
                "unit_column": "donor",
                "condition_column": "condition",
                "technical_factors": ["batch"],
                "numerator": "case",
                "denominator": "control",
            }
        )
    )
    return request


@pytest.mark.parametrize(
    ("suffix", "genes"), [("h5", 20_000), ("h5ad", 32), ("zarr", 32)]
)
def test_mounted_bulk_externalizes_complete_family(workspace_tmp_path, suffix, genes):
    from rna_kernels.mounted_bulk import run_mounted_bulk

    inputs = workspace_tmp_path / "inputs"
    outputs = workspace_tmp_path / "outputs"
    inputs.mkdir()
    outputs.mkdir()
    request = _build(inputs, suffix, genes)
    digest = hashlib.sha256(request.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match="request hash"):
        run_mounted_bulk(inputs, outputs, "0" * 64)
    assert list(outputs.iterdir()) == []
    run_mounted_bulk(inputs, outputs, digest)
    summary = json.loads((outputs / "summary.json").read_bytes())
    assert summary["row_count"] == genes
    assert summary["request_sha256"] == digest
    assert summary["genes_sha256"] == _digest(outputs / "genes.parquet")
    assert (outputs / "summary.json").stat().st_size < 2_048
    table = pq.read_table(outputs / "genes.parquet")
    assert table.num_rows == genes
    assert np.mean(table["effect"].to_numpy()) == pytest.approx(1.0, abs=0.06)
    assert np.all(table["q_value"].to_numpy() >= table["p_value"].to_numpy())


@pytest.mark.parametrize("suffix", ["h5", "zarr"])
def test_mounted_worker_refuses_external_storage_and_executable_codecs(
    workspace_tmp_path, suffix
):
    from rna_kernels.mounted_bulk import run_mounted_bulk

    inputs = workspace_tmp_path / "inputs"
    outputs = workspace_tmp_path / "outputs"
    inputs.mkdir()
    outputs.mkdir()
    request = _build(inputs, suffix, 8)
    document = json.loads(request.read_bytes())
    expression = inputs / document["expression"]
    if suffix == "h5":
        external = workspace_tmp_path / "external.h5"
        with h5py.File(external, "w") as handle:
            handle.create_dataset("matrix", data=np.zeros((8, 256)))
        with h5py.File(expression, "a") as handle:
            del handle["expression"]
            handle["expression"] = h5py.ExternalLink(str(external), "matrix")
        problem = "internal storage"
    else:
        header = expression / "expression/.zarray"
        array = json.loads(header.read_bytes())
        array["compressor"] = {"id": "pickle"}
        header.write_text(json.dumps(array))
        problem = "Zarr codec"
    document["expression_sha256"] = _digest(expression)
    request.write_text(json.dumps(document))
    digest = hashlib.sha256(request.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match=problem):
        run_mounted_bulk(inputs, outputs, digest)
    assert list(outputs.iterdir()) == []


def test_mounted_python_only_wald_is_fixed_before_fit(workspace_tmp_path):
    from rna_kernels.mounted_bulk import run_mounted_bulk

    inputs, outputs = workspace_tmp_path / "inputs", workspace_tmp_path / "outputs"
    inputs.mkdir()
    outputs.mkdir()
    request = _build(inputs, "h5", 200)
    document = json.loads(request.read_bytes())
    expression = inputs / document["expression"]
    counts = np.random.default_rng(61).negative_binomial(20, 0.3, (200, 256))
    counts[:8, 128:] *= 3
    with h5py.File(expression, "a") as handle:
        handle["expression"][:] = counts
    document["expression_sha256"] = _digest(expression)
    document["measurement"] = "raw_counts"
    request.write_text(json.dumps(document))
    with pytest.raises(ValueError, match="Python-only counts support"):
        run_mounted_bulk(inputs, outputs, _digest(request), runtime="python_only")
    document["technical_factors"] = []
    request.write_text(json.dumps(document))
    summary = run_mounted_bulk(inputs, outputs, _digest(request), runtime="python_only")
    result = pq.read_table(outputs / "genes.parquet")
    assert summary["solver"] == "pydeseq2_wald"
    assert summary["row_count"] == 200
    assert np.nanmedian(result["effect"].slice(0, 8).to_numpy()) > 1.3


def test_mounted_continuous_covariate_runs_freedman_lane(workspace_tmp_path):
    from rna_kernels.mounted_bulk import run_mounted_bulk

    inputs, outputs = workspace_tmp_path / "inputs", workspace_tmp_path / "outputs"
    inputs.mkdir()
    outputs.mkdir()
    request = _build(inputs, "h5", 64)
    document = json.loads(request.read_bytes())
    expression = inputs / document["expression"]
    rng = np.random.default_rng(42)
    age = rng.normal(size=256)
    age[128:] += 1.2
    values = 5 * age[None, :] + rng.normal(size=(64, 256))
    values[:10, 128:] += 2
    with h5py.File(expression, "a") as handle:
        handle["expression"][:] = values
    metadata = inputs / "metadata.csv"
    header, *rows = metadata.read_text().splitlines()
    metadata.write_text(
        header
        + ",age\n"
        + "\n".join(f"{row},{age[int(row.split(',')[0][1:])]}" for row in rows)
        + "\n"
    )
    document.update(
        expression_sha256=_digest(expression),
        metadata_sha256=_digest(metadata),
        continuous_covariates=["age"],
    )
    request.write_text(json.dumps(document))
    summary = run_mounted_bulk(inputs, outputs, _digest(request))
    rank = summary["rank_sensitivity"]
    assert rank["method"] == "freedman_lane"
    assert rank["tested_count"] == 64 and rank["permutations"] == 999
    assert rank["artifact_sha256"] == _digest(outputs / "sensitivity.parquet")
    table = pq.read_table(outputs / "genes.parquet")
    assert np.median(table["effect"].slice(10).to_numpy()) == pytest.approx(
        0.0, abs=0.07
    )
    assert (
        np.median(values[10:, 128:].mean(axis=1) - values[10:, :128].mean(axis=1)) > 3
    )
