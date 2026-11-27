"""Frozen-nucleus QC, capture-level predictions and differential retention."""

import json

import anndata as ad
import numpy as np
import pandas as pd
import pytest
from scipy import sparse

from devbench.mcp.plot_sidecars import verify_plot_sidecar
from rna_kernels import scrna_capture_doublets as doublets
from rna_kernels.mounted_scrna import run_m2
from rna_kernels.scrna_qc_audit import SolverUnavailable


@pytest.fixture
def expression(tmp_path):
    path = tmp_path.resolve() / "expression.h5ad"
    counts = np.array([[3, 4], [0, 0], [1, 3], [2, 1]] * 2)
    obs = pd.DataFrame(
        dict(
            donor=list("aabbccdd"),
            capture=list("aaaabbbb"),
            condition=list("abbbbbbb"),
            cluster=list("abababab"),
        ),
        index=list("ABCDEFGH"),
    )
    data = ad.AnnData(sparse.csr_matrix(counts), obs=obs)
    data.layers["unspliced"] = sparse.csr_matrix(counts)
    data.layers["spliced"] = sparse.csr_matrix(2 * counts)
    data.write_h5ad(path)
    return path


def test_nuclear_metrics_audit(expression, tmp_path):
    paths = run_m2(
        "snrna_droplet_qc",
        {"expression": expression},
        {"classify_droplets": False},
        tmp_path,
    )
    metrics = pd.read_parquet(paths["metrics"])
    assert metrics.pct_counts_mt.isna().all()
    np.testing.assert_allclose(metrics.nuclear_fraction.dropna(), 1 / 3)
    assert metrics.nuclear_fraction.isna().sum() == 2
    assert metrics.qc_pass.sum() == 6
    result = json.loads(paths["result"].read_bytes())
    assert "mitochondrial_annotation_unavailable" in result["warnings"]
    assert "disproportionate_retention_rate_across_cluster" in result["warnings"]
    assert {r["axis"] for r in result["differential_audit"]} == {
        "donor",
        "capture",
        "condition",
        "cluster",
    }
    verify_plot_sidecar(paths["plot_sidecar"], "json")


def test_bam_summary_alignment(expression, tmp_path):
    summary = tmp_path / "counts.csv"
    pd.DataFrame(
        dict(barcode=list("HGFEDCBA"), intronic=[9] * 8, exonic=[1] * 8)
    ).to_csv(summary, index=False)
    ports = {"expression": expression, "bam_summary": summary}
    paths = run_m2("snrna_droplet_qc", ports, {"classify_droplets": False}, tmp_path)
    np.testing.assert_allclose(pd.read_parquet(paths["metrics"]).nuclear_fraction, 0.9)
    summary.write_text("barcode,intronic,exonic\nA,9,1\n")
    with pytest.raises(ValueError, match="barcode_mismatch"):
        run_m2("snrna_droplet_qc", ports, {}, tmp_path)


def test_scdblfinder_stratification(expression, tmp_path, monkeypatch):
    calls = []

    def classify(source, rows, root, rate, seed):
        calls.append((list(rows), rate))
        return pd.DataFrame(
            dict(
                doublet_score=[0.8] * len(rows),
                predicted_doublet=[rate == 0.1] * len(rows),
            ),
            index=source.obs.index[rows],
        )

    monkeypatch.setattr(doublets, "_scdblfinder", classify)
    paths = run_m2(
        "scrna_doublets",
        {"expression": expression},
        {"expected_rates": {"a": 0.1, "b": 0.2}},
        tmp_path,
    )
    assert calls == [([0, 2, 3], 0.1), ([4, 6, 7], 0.2)]
    result = json.loads(paths["result"].read_bytes())
    assert result["status"] == "completed"
    assert "disproportionate_doublet_rate_across_capture" in result["warnings"]
    assert pd.read_parquet(paths["metrics"]).predicted_doublet.isna().sum() == 2
    verify_plot_sidecar(paths["plot_sidecar"], "json")


def test_scdblfinder_unavailable_fallback_audited(expression, tmp_path, monkeypatch):
    def unavailable(*args):
        raise SolverUnavailable("package_unavailable")

    monkeypatch.setattr(doublets, "_scdblfinder", unavailable)
    paths = run_m2("scrna_doublets", {"expression": expression}, {}, tmp_path)
    result = json.loads(paths["result"].read_bytes())
    assert result["requested_method"] == "scdblfinder"
    assert all(
        lane["method"] == "scrublet"
        and lane["fallback_reason"] == "scdblfinder_package_unavailable"
        for lane in result["batches"]
    )
    assert result["status"] == "runtime_unavailable"
    assert all(r["doublet_rate"] is None for r in result["differential_audit"])
