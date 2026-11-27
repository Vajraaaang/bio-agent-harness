"""ADR-27 two-axis admission, retry boundaries and layer custody."""

import json

import anndata as ad
import h5py
import numpy as np
import pandas as pd
import pytest
from scipy import sparse

from devbench.core._files import hash_bounded_file
from rna_kernels import scrna_ambient as ambient
from rna_kernels.mounted_scrna import run_m2
from rna_kernels.scrna_droplets import DropletRuntime, route_ambient_decontamination
from rna_kernels.scrna_qc_audit import SolverUnavailable


def route(**updates):
    args = dict(
        input_matrix_tier="R0",
        hardware_backend="cpu",
        modality="scrna_seq",
        raw_droplets=25000,
        captures=4,
        cellbender_version="0.4.0",
    )
    return route_ambient_decontamination(**(args | updates))


@pytest.mark.parametrize(
    "updates,method,reason",
    [
        ({}, "soupx", "cellbender_cpu_not_opted_in"),
        ({"modality": "snrna_seq"}, "decontx", "cellbender_cpu_not_opted_in"),
        ({"hardware_backend": "cuda"}, "cellbender", None),
        ({"allow_cellbender_cpu": True}, "cellbender", None),
        (
            {"allow_cellbender_cpu": True, "raw_droplets": 25001},
            "soupx",
            "cellbender_cpu_budget_exceeded",
        ),
        (
            {"allow_cellbender_cpu": True, "captures": 5},
            "soupx",
            "cellbender_cpu_budget_exceeded",
        ),
        (
            {"hardware_backend": "cuda", "cellbender_version": "0.3.1"},
            "soupx",
            "cellbender_yanked_0.3.1",
        ),
        (
            {"input_matrix_tier": "R1", "hardware_backend": "cuda"},
            "decontx",
            "missing_raw_droplet_matrix",
        ),
    ],
)
def test_route_boundaries(updates, method, reason):
    answer = route(**updates)
    assert answer.ambient_method == method
    assert answer.cellbender_deferred_reason == reason


@pytest.mark.parametrize("method", ["soupx", "cellbender"])
def test_filtered_only_refuses_raw_method(method):
    with pytest.raises(ValueError, match="missing_raw_droplet_matrix"):
        route(input_matrix_tier="R1", requested_method=method)


def test_old_cellbender_pin_rejected():
    with pytest.raises(ValueError, match="at_least_0.3.2"):
        DropletRuntime("cellbender", "sha256:" + "1" * 64, "0.3.1")


def _cohort(path, n):
    x = sparse.csr_matrix(1 + np.arange(n * 8, dtype=np.int32).reshape(n, 8) % 11)
    obs = pd.DataFrame(
        {
            "capture": ["lane" + str(i % 2) for i in range(n)],
            "donor": ["d" + str(i % 4) for i in range(n)],
            "condition": ["ab"[i % 2] for i in range(n)],
            "cluster": ["k" + str((i // 2) % 2) for i in range(n)],
        },
        index=["cell" + str(i) for i in range(n)],
    )
    ad.AnnData(x, obs=obs).write_h5ad(path)


def test_linked_adjustment_and_retry(monkeypatch, tmp_path):
    root = tmp_path.resolve()
    source = root / "expr.h5ad"
    raw = root / "raw.h5ad"
    out = root / "out"
    out.mkdir()
    _cohort(source, 12)
    _cohort(raw, 18)
    digest = hash_bounded_file(source, 2**20)
    monkeypatch.setattr(ambient, "_version", lambda: "0.4.0")
    monkeypatch.setattr(ambient, "observed_hardware", lambda: "cuda")
    trials = []

    def failed(raw, barcodes, genes, positions, scratch, rate, cuda):
        trials.append((rate, cuda))
        raise SolverUnavailable("cellbender_fit_failed")

    monkeypatch.setattr(ambient, "_run_cellbender", failed)
    called = []

    def fake_r(driver, scratch, method, seed, tier):
        called.append((method, tier))
        if method == "soupx":
            raise SolverUnavailable("soupx_autoestcont_warning_or_failure")
        with (
            h5py.File(scratch / "counts.h5") as inp,
            h5py.File(scratch / "adjusted.h5", "w") as dest,
        ):
            assert "raw" in inp
            for key in ["p", "i", "x", "dim"]:
                arr = inp["filtered"][key][:]
                dest["Dim" if key == "dim" else key] = arr * 0.8 if key == "x" else arr

    monkeypatch.setattr(ambient, "run_r", fake_r)
    paths = run_m2(
        "scrna_ambient_decontamination",
        dict(expression=source, raw_droplets=raw),
        {},
        out,
    )
    result = json.loads(paths["result"].read_bytes())
    assert trials == [(0.0001, True), (0.00005, True)]
    assert called == [("soupx", "R0"), ("decontx", "R0"), ("decontx", "R0")]
    assert result["ambient_method"] == "decontx"
    assert result["decontamination_regime"] == "r0_cpu_soupx_decontx"
    assert result["cellbender_deferred_reason"] == "cellbender_retry_exhausted"
    assert result["r1_sha256"] == digest == hash_bounded_file(source, 2**20)
    assert result["sensitivity"]["purpose"] == "E3"
    assert result["primary_de_representation"] == "count_1_raw_counts"
    assert result["clinical_release_allowed"] is False
    assert set(pd.read_parquet(paths["metrics"]).columns) == {
        "feature",
        "raw_log2_cpm_difference",
        "adjusted_log2_cpm_difference",
    }
    with h5py.File(paths["adjusted"]) as f:
        assert f["X"].attrs["transformation"] == "corrected_expression"
        required = {
            "ambient_method",
            "decontamination_regime",
            "input_matrix_tier",
            "hardware_backend",
            "cellbender_version",
            "cellbender_deferred_reason",
        }
        assert required <= set(f.attrs) and required <= set(result)
    np.testing.assert_allclose(
        ad.read_h5ad(paths["adjusted"]).X.toarray(),
        ad.read_h5ad(source).X.toarray() * 0.8,
    )


def test_missing_background_does_not_execute(tmp_path, monkeypatch):
    source = tmp_path.resolve() / "e.h5ad"
    _cohort(source, 12)
    monkeypatch.setattr(
        ambient, "_adjust", lambda *args: pytest.fail("solver launched")
    )
    outputs = run_m2(
        "scrna_ambient_decontamination",
        {"expression": source},
        {"requested_method": "cellbender"},
        tmp_path,
    )
    assert (
        json.loads(outputs["result"].read_bytes())["reason"]
        == "missing_raw_droplet_matrix"
    )


def test_raw_primary_blocks_unverified_inputs_and_incomplete_pairs(
    tmp_path, monkeypatch
):
    from rna_kernels import scrna_primary_pseudobulk as primary

    monkeypatch.setattr(primary, "run_r", lambda *args: pytest.fail("solver launched"))
    units = [(str(i), c) for i, c in enumerate(["a"] * 3 + ["b"] * 3)]

    def fit(records, admitted=True, confounded=False):
        result, receipt = primary.raw_primary(
            np.ones((len(records), 3)),
            records,
            list("xyz"),
            "a",
            "b",
            tmp_path,
            admitted,
            confounded,
        )
        assert not result
        return receipt["reason"]

    assert fit(units, False) == "primary_count_or_forensic_gate_held"
    assert fit(units, True, True) == "capture_confounded_with_condition"
    assert fit(units[:-1]) == "fewer_than_three_donors_per_condition"
    assert fit([*units[:4], ("0", "b"), units[5]]) == "incomplete_donor_pairs"


def test_raw_primary_reviewed_driver_transport_for_complete_pairs(
    tmp_path, monkeypatch
):
    from rna_kernels import scrna_primary_pseudobulk as primary
    from rna_kernels.canonical import canonical_json

    units = [(donor, group) for donor in "ABC" for group in "ab"]
    counts = np.arange(6 * 3).reshape(6, 3) + 1

    def solver(driver, scratch, root):
        assert driver == "mounted_general.R"
        config = json.loads((root / "model.json").read_bytes())
        assert config["units"] == "paired_fixed" and config["solver"] == "edger_v4"
        np.testing.assert_array_equal(
            np.fromfile(root / "counts.bin", "<f8").reshape(3, 6).T, counts
        )
        effect = [1.0, np.nan, np.nan]
        p = [0.07, np.nan, np.nan]
        q = [0.14, np.nan, np.nan]
        np.array([effect, p, q, [0, 3, 1]], dtype="<f8").tofile(root / "result.bin")
        evidence = dict(
            solver="edger_v4",
            test="qlf",
            solver_version="4.10.7",
            solver_details={
                "normalization": "TMM",
                "adjustment": "BH",
                "filter": "filterByExpr",
            },
        )
        (root / "method.json").write_bytes(canonical_json(evidence))

    monkeypatch.setattr(primary, "run_r", solver)
    estimates, receipt = primary.raw_primary(
        counts, units, list("xyz"), "a", "b", tmp_path, True, False
    )
    assert receipt["status"] == "completed" and receipt["biological_units"] == 3
    assert receipt["design"] == "paired_fixed" and receipt["failed_genes"] == 1
    assert estimates["raw_estimate_status"].tolist() == [
        "estimated",
        "failed",
        "filtered",
    ]
    assert np.isnan(estimates["raw_p_value"][1:]).all()
