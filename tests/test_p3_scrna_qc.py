"""Synthetic tests for explicit single-cell QC and lane-wise doublets."""

import hashlib
import json

import numpy as np
import pandas as pd
import pytest
from scipy import sparse

from devbench.analysis.annotation import run_doublets
from devbench.analysis.checkpoints import CheckpointStore
from devbench.analysis.singlecell import detect_doublets, filter_qc_metrics
from devbench.analysis.streaming import stream_qc


@pytest.mark.parametrize(
    ("definition", "scale", "retained"),
    [("absolute_mad", 1.0, 9), ("scaled_mad", 1.4826, 10)],
)
def test_mad_is_local_and_its_convention_changes_the_cutoff(
    definition, scale, retained,
):
    metrics = pd.DataFrame(
        {
            "total_counts": [100] * 10,
            "n_genes_by_counts": [10] * 10,
            "log1p_total_counts": [2, 3, 3, 4, 5.5, 7, 8, 8, 9, 10],
            "log1p_n_genes_by_counts": [2.0] * 10,
            "pct_counts_mt": [10.0] * 10,
            "capture": ["001"] * 5 + ["1"] * 5,
            "condition": ["control"] * 4 + ["case"] + ["case"] * 2 + ["control"] * 3,
        }
    )
    keep, report = filter_qc_metrics(
        metrics,
        qc_group_key="capture",
        retention_group_key="condition",
        mad_definition=definition,
        mad_limit=2,
        min_genes=2,
    )
    assert int(keep.sum()) == retained
    assert keep[5:].all()
    assert report["mad_definition"] == definition
    assert report["mad_scale"] == scale
    depth = [
        row for row in report["thresholds"]
        if row["metric"] == "log1p_total_counts"
    ]
    assert [row["batch"] for row in depth] == ["001", "1"]
    assert [row["upper"] for row in depth] == pytest.approx(
        [3 + 2 * scale, 8 + 2 * scale]
    )
    groups = report["group_retention"]
    assert groups["group_key"] == "condition"
    assert groups["groups"] == [
        {
            "group": "case",
            "cells_input": 3,
            "cells_retained": retained - 7,
            "retention_rate": (retained - 7) / 3,
        },
        {
            "group": "control",
            "cells_input": 7,
            "cells_retained": 7,
            "retention_rate": 1.0,
        },
    ]
    assert groups["max_rate_gap"] == pytest.approx((10 - retained) / 3)


def _source(workspace, obs, counts, genes=None):
    ad = pytest.importorskip("anndata")
    matrix = sparse.csr_matrix(counts)
    if genes is None:
        genes = ["MT-gene", *(f"g{i}" for i in range(1, matrix.shape[1]))]
    data = ad.AnnData(
        X=matrix.astype(np.float64) * 0.125,
        obs=obs,
        var=pd.DataFrame(index=genes),
    )
    data.layers["counts"] = matrix
    path = workspace / "source.h5ad"
    data.write_h5ad(path)
    return path


@pytest.mark.parametrize("mt_present", [True, False])
def test_nucleus_qc_agrees_on_zero_mad_fallback_and_optional_metrics(
    workspace_tmp_path, mt_present,
):
    ad = pytest.importorskip("anndata")
    pytest.importorskip("scanpy")
    from devbench.analysis.singlecell import quality_control
    from devbench.analysis.streaming import read_table

    genes = ["mt-Co1" if mt_present else "g0", "Rps12", "Malat1", "g3"]
    obs = pd.DataFrame(
        {"capture": ["a"] * 6 + ["b"] * 6},
        index=[f"nucleus-{i}" for i in range(12)],
    )
    counts = np.tile([5, 3, 5, 87], (12, 1))
    counts[5] = [85, 3, 5, 7]  # Only the mitochondrial percentage changes.
    counts[6:, [0, 3]] = [20, 72]
    source = _source(workspace_tmp_path, obs, counts, genes)
    store = CheckpointStore(
        workspace_tmp_path / "stages", {"input": "synthetic nuclei"}
    )
    destination = stream_qc(
        source, store, layer="counts", batch_key="capture", qc_group_key="capture",
        min_genes=2, block_rows=3,
    )
    table = read_table(destination / "metrics.csv")
    raw = ad.read_h5ad(source)
    raw.X = raw.layers["counts"]
    kept, in_memory = quality_control(raw, batch_key="capture", min_genes=2)
    streamed = json.loads((destination / "report.json").read_text())
    assert kept.obs_names.tolist() == table.index[table["qc_pass"]].tolist()
    np.testing.assert_allclose(table["pct_counts_ribo"], 3)
    np.testing.assert_allclose(table["pct_counts_malat1"], 5)
    np.testing.assert_allclose(table["total_counts_malat1"], 5)
    if mt_present:
        assert table["pct_counts_mt"].loc["nucleus-5"] == 85
        assert not table["qc_pass"].loc["nucleus-5"]
        for report in (in_memory, streamed):
            assert report["excluded_cell_ids"] == ["nucleus-5"]
            assert "zero_MAD_mitochondrial_fallback:a" in report["warnings"]
            assert [row["upper"] for row in report["thresholds"]
                    if row["metric"] == "pct_counts_mt"] == [10, 25]
    else:
        assert table["qc_pass"].all()
        assert table["pct_counts_mt"].isna().all()
        for report in (in_memory, streamed):
            assert report["mitochondrial_features"] == 0
            assert "mitochondrial_annotation_unavailable" in report["warnings"]


def test_streamed_qc_reads_original_layer_and_records_group_retention(
    workspace_tmp_path,
):
    pytest.importorskip("scanpy")
    obs = pd.DataFrame(
        {
            "batch": ["same"] * 8,
            "capture": ["001"] * 4 + ["1"] * 4,
            "condition": ["control", "case"] * 4,
        },
        index=[f"c{i}" for i in range(8)],
    )
    counts = np.tile([2, 4, 6, 8], (8, 1))
    counts[1] = [0, 0, 0, 0]
    source = _source(workspace_tmp_path, obs, counts)
    identity = hashlib.sha256(source.read_bytes()).hexdigest()
    store = CheckpointStore(workspace_tmp_path / "stages", {"input": identity})
    destination = stream_qc(
        source,
        store,
        layer="counts",
        batch_key="batch",
        qc_group_key="capture",
        retention_group_key="condition",
        mad_definition="scaled_mad",
        mad_limit=5.0,
        min_genes=2,
        block_rows=3,
    )
    result = json.loads((destination / "report.json").read_text())
    assert result["cells_excluded"] == 1
    assert result["excluded_cell_ids"] == ["c1"]
    assert result["qc_group_key"] == "capture"
    assert result["group_retention"]["max_rate_gap"] == 0.25
    table = pd.read_csv(destination / "metrics.csv", dtype={"capture": str})
    assert table["capture"].tolist() == ["001"] * 4 + ["1"] * 4
    assert hashlib.sha256(source.read_bytes()).hexdigest() == identity
    with pytest.raises(ValueError, match="qc_configuration_changed"):
        stream_qc(
            source, store, layer="counts", batch_key="batch",
            qc_group_key="capture", mad_definition="absolute_mad",
        )


@pytest.mark.parametrize(
    ("capture", "labels", "reason"),
    [
        (None, ["a", "b", "a", "b"], "capture_library_role_not_declared"),
        ("capture", [" ", "b", "b", "b"], "declared_capture_library_identities"),
        ("capture", ["a", "a", "a", "a"], "ambiguous_capture_library_identities"),
    ],
)
def test_strict_scrublet_refuses_absent_or_ambiguous_lane_identity(
    workspace_tmp_path, capture, labels, reason,
):
    obs = pd.DataFrame(
        {"batch": ["x", "x", "y", "y"], "capture": labels},
        index=["a1", "a2", "b1", "b2"],
    )
    source = _source(workspace_tmp_path, obs, np.ones((4, 3), dtype=np.int32))
    store = _qc_fixture(workspace_tmp_path, obs, np.ones(4, dtype=bool))
    with pytest.raises(ValueError, match=reason):
        run_doublets(
            source, store, layer="counts", batch_key="batch", seed=13,
            capture_key=capture, require_declared_capture=True,
        )
    assert not store.path("doublets").exists()


def _qc_fixture(workspace, obs, keep):
    store = CheckpointStore(workspace / "stages", {"fixture": "qc-pass"})

    def write_metrics(folder):
        obs.assign(qc_pass=keep).to_csv(folder / "metrics.csv")

    store.run("qc", write_metrics)
    return store


def test_scrublet_matches_a_single_lane_and_retains_nulls_for_small_lanes(
    workspace_tmp_path,
):
    ad = pytest.importorskip("anndata")
    pytest.importorskip("scanpy")
    rng = np.random.default_rng(73)
    cells = 130
    rates = rng.uniform(0.5, 4.0, size=(cells + 9, 400))
    rates[:60, :80] *= 5
    rates[60:cells, 80:160] *= 5
    counts = np.vstack([rng.poisson(rates), np.ones((100, 400), dtype=int)])
    lanes = ["physical-A"] * cells + ["physical-B"] * 9 + ["physical-C"] * 100
    obs = pd.DataFrame(
        {"batch": "shared", "capture": lanes},
        index=[f"c{i}" for i in range(len(lanes))],
    )
    source = _source(workspace_tmp_path, obs, counts)
    input_identity = hashlib.sha256(source.read_bytes()).hexdigest()
    store = _qc_fixture(workspace_tmp_path, obs, np.ones(len(obs), dtype=bool))
    result = run_doublets(
        source, store, layer="counts", batch_key="batch", seed=13,
        capture_key="capture", require_declared_capture=True, expected_rate=0.08,
    )
    report = json.loads((result / "report.json").read_text())
    assert report["status"] == "partial"
    assert report["expected_rate_assumption"] == 0.08
    assert {row["batch"]: row["cells"] for row in report["batches"]} == {
        "physical-A": cells,
        "physical-B": 9,
        "physical-C": 100,
    }
    assert report["batches"][1]["reason"] == (
        "fewer_than_100_cells_for_configured_doublet_method"
    )
    assert report["batches"][2]["reason"] == "scrublet_lane_fit_failed"
    table = pd.read_csv(result / "predictions.csv", index_col=0)
    assert table.index.equals(obs.index)
    assert table.iloc[cells:].isna().all().all()
    assert hashlib.sha256(source.read_bytes()).hexdigest() == input_identity
    single = ad.AnnData(sparse.csr_matrix(counts[:cells]), obs=obs.iloc[:cells])
    independent, standalone = detect_doublets(
        single, batch_key="capture", expected_rate=0.08, seed=13,
    )
    assert standalone["status"] == "completed"
    np.testing.assert_allclose(
        table["doublet_score"].iloc[:cells], independent.obs["doublet_score"],
    )
    with pytest.raises(ValueError, match="doublet_configuration_changed"):
        run_doublets(
            source, store, layer="counts", batch_key="batch", seed=13,
            capture_key="capture", require_declared_capture=True, expected_rate=0.04,
        )


def test_qc_handoff_retains_exact_cell_ids_and_rejects_reordered_axes():
    from devbench.core.scrna_qc_selection import AcceptedQCSelection, QCSelectionBinding
    from devbench.platform.scrna_qc_handoff import _parse_mask

    cells, passed = _parse_mask(b"cell,qc_pass\n001,True\nNA,False\n")
    assert cells == ("001", "NA") and passed == (True, False)
    binding = QCSelectionBinding(
        lineage_id="fixture",
        step_id="qc",
        generation=1,
        receipt_sha256="a" * 64,
        source_sha256="b" * 64,
        metrics_sha256="c" * 64,
    )
    selection = AcceptedQCSelection(binding, cells, passed)
    assert selection.align(binding.source_sha256, cells) == passed
    with pytest.raises(ValueError, match="complete ordered cell axis"):
        selection.align(binding.source_sha256, tuple(reversed(cells)))


@pytest.mark.parametrize(
    "raw",
    [
        b"cell,qc_pass\nA,1\n",
        b"cell,qc_pass\nA,true\n",
        b"cell,qc_pass\nA,True\nA,False\n",
        b"cell,qc_pass,qc_pass\nA,True,False\n",
    ],
)
def test_qc_handoff_refuses_ambiguous_observation_ids_and_flags(raw):
    from devbench.platform.scrna_qc_handoff import _parse_mask

    with pytest.raises(ValueError):
        _parse_mask(raw)
