"""Synthetic custody and numerical checks for the mounted single-cell worker."""

import json
from dataclasses import asdict
from hashlib import sha256
from pathlib import Path
from shutil import copyfile

import anndata as ad
import numpy as np
import pandas as pd
import pytest
from scipy import sparse

from rna_kernels.canonical import canonical_json
from rna_kernels.mounted_scrna import run_mounted_scrna
from rna_kernels.scrna_upstream_contract import (
    REQUEST_FORMAT,
    SCRNA_OPERATIONS,
    SingleCellStepSpec,
)


def _sha(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def expression(workspace_tmp_path: Path) -> Path:
    rng = np.random.default_rng(219)
    matrix = rng.poisson(4, (36, 48)).astype(np.int32)
    matrix[0] = 0
    obs = pd.DataFrame(
        {"batch": ["001"] * 18 + ["002"] * 18,
         "lane": ["first"] * 18 + ["second"] * 18},
        index=[f"{i:04d}" for i in range(len(matrix))],
    )
    var = pd.DataFrame(index=["MT-C01", *(f"gene-{i}" for i in range(47))])
    data = ad.AnnData(sparse.csr_matrix(np.log1p(matrix)), obs=obs, var=var)
    data.layers["counts"] = sparse.csr_matrix(matrix)
    path = workspace_tmp_path / "fixture.h5ad"
    data.write_h5ad(path)
    return path


def _mounts(root, operation, sources, *, runtime_image_id=None, **parameters):
    inputs = root / "inputs"
    outputs = root / "outputs"
    inputs.mkdir(parents=True)
    outputs.mkdir()
    hashes = {}
    for port, source in sources.items():
        mounted = inputs / SCRNA_OPERATIONS[operation].inputs[port].filename
        copyfile(source, mounted)
        hashes[port] = _sha(mounted)
    without_batch = {
        "scrna_droplets", "scrna_nb_countsplit", "scrna_nb_fold_cluster",
        "scrna_nb_fold_pseudobulk",
        "scrna_nb_marker_fit", "fit_donor_trajectory",
    }
    request = {
        "format": REQUEST_FORMAT,
        "operation": operation,
        "parameters": parameters if operation in without_batch
        else {"batch_key": "batch", **parameters},
        "inputs": hashes,
    }
    if runtime_image_id is not None:
        request["runtime_image_id"] = runtime_image_id
    (inputs / "request.json").write_bytes(canonical_json(request))
    return inputs, outputs, _sha(inputs / "request.json")


def _checked_run(inputs, outputs, digest):
    summary = run_mounted_scrna(inputs, outputs, digest)
    assert summary["request_sha256"] == digest
    assert summary["claim_scope"] == "checked_observation"
    assert json.loads((outputs / "summary.json").read_bytes()) == summary
    artifacts = {path.name: _sha(path) for path in outputs.iterdir()
                 if path.name != "summary.json"}
    assert summary["output_sha256"] == artifacts
    return summary


def _qc(root, expression):
    claim = _mounts(
        root, "scrna_batch_qc", {"expression": expression},
        min_genes=1, mad_limit=None, qc_group_key="lane",
        retention_group_key="batch", mad_definition="scaled_mad",
    )
    assert _checked_run(*claim)["status"] == "completed"
    return claim[1]


def _split_dispersion(expression, path, *, units=()):
    source = ad.read_h5ad(expression)
    document = {
        "format": "devbench.nb-split-dispersion/1",
        "analysis_counts_sha256": _sha(expression),
        "source_sha256": "a" * 64,
        "source_receipt_sha256": "b" * 64,
        "origin": "independent_cohort" if units else "known",
        "prior_free": True,
        "feature_ids": source.var_names.tolist(),
        "theta": [4.0] * source.n_vars,
        "biological_unit_key": "batch" if units else None,
        "calibration_unit_ids": list(units),
    }
    path.write_bytes(canonical_json(document))
    return path


def test_mounted_nb_split_conserves_counts_and_isolates_axes(
    expression, workspace_tmp_path,
):
    raw_sha = _sha(expression)
    manifest = _split_dispersion(
        expression, workspace_tmp_path / "theta.json", units=("external",),
    )
    original = ad.read_h5ad(expression)
    mounts = _mounts(
        workspace_tmp_path / "split", "scrna_nb_countsplit",
        {"expression": expression, "split_dispersion": manifest},
        seed=93, clustering_fraction=0.4,
    )
    assert _checked_run(*mounts)["status"] == "completed"
    clustering = ad.read_h5ad(mounts[1] / "clustering.h5ad")
    inference = ad.read_h5ad(mounts[1] / "inference.h5ad")
    for fold in (clustering, inference):
        assert fold.obs_names.equals(original.obs_names)
        assert fold.var_names.equals(original.var_names)
    assert not clustering.obs.columns.size and not inference.obs.columns.size
    assert ((clustering.X + inference.X) != original.layers["counts"]).nnz == 0
    assert _sha(expression) == raw_sha
    report = json.loads((mounts[1] / "split_report.json").read_bytes())
    assert report["split_distribution"] == "dirichlet_multinomial"
    assert report["counts_sha256"] == raw_sha
    assert report["clustering_counts_sha256"] == _sha(mounts[1] / "clustering.h5ad")
    assert report["inference_counts_sha256"] == _sha(mounts[1] / "inference.h5ad")
    assert report["marker_inference_performed"] is False


def test_nb_split_has_expected_margins_and_no_empirical_fold_correlation():
    from rna_kernels.scrna_nb_countsplit import split_count_block

    rng = np.random.default_rng(72)
    original = rng.negative_binomial(4.0, 0.25, size=(60_000, 1))
    left, right = split_count_block(
        sparse.csr_matrix(original), np.array([4.0]), 0.4, rng,
    )
    folded = [part.toarray().ravel() for part in (left, right)]
    np.testing.assert_array_equal(folded[0] + folded[1], original.ravel())
    np.testing.assert_allclose([part.mean() for part in folded], [4.8, 7.2], atol=0.06)
    np.testing.assert_allclose([part.var() for part in folded], [19.2, 28.8], atol=0.8)
    assert abs(np.corrcoef(folded)[0, 1]) < 0.025


def test_fold_clustering_rejects_an_unbound_fold_or_report(
    expression, workspace_tmp_path,
):
    manifest = _split_dispersion(expression, workspace_tmp_path / "theta.json")
    split = _mounts(
        workspace_tmp_path / "split", "scrna_nb_countsplit",
        {"expression": expression, "split_dispersion": manifest},
        seed=41, clustering_fraction=0.4,
    )
    assert _checked_run(*split)["status"] == "completed"
    cluster = _mounts(
        workspace_tmp_path / "cluster", "scrna_nb_fold_cluster",
        {"clustering_counts": split[1] / "inference.h5ad",
         "split_report": split[1] / "split_report.json"},
        seed=59, n_top_genes=20, n_pcs=8, resolution=1.2,
    )
    with pytest.raises(
        ValueError, match="split report does not bind the clustering fold",
    ):
        run_mounted_scrna(*cluster)
    assert not list(cluster[1].iterdir())


@pytest.mark.parametrize("incomplete", [False, True])
def test_fold_pseudobulk_sums_only_observed_raw_counts(
    expression, workspace_tmp_path, incomplete,
):
    import h5py

    original = ad.read_h5ad(expression)
    original.obs["donor"] = [f"donor-{i // 6}" for i in range(original.n_obs)]
    original.obs["specimen"] = [f"specimen-{i // 6}" for i in range(original.n_obs)]
    original.obs["condition"] = "fixed"
    original.write_h5ad(expression)
    manifest = _split_dispersion(expression, workspace_tmp_path / "theta.json")
    split = _mounts(
        workspace_tmp_path / "split", "scrna_nb_countsplit",
        {"expression": expression, "split_dispersion": manifest}, seed=27,
        clustering_fraction=0.4, marker_cohort_columns={
            "donor_key": "donor", "specimen_key": "specimen",
            "condition_key": "condition",
        },
    )
    _checked_run(*split)
    report = split[1] / "split_report.json"
    bindings = json.loads(report.read_bytes())
    # These synthetic labels exercise arithmetic; calibrated labels require Tier A.
    clusters = [
        "0" if incomplete and i < 6 else str(i % 2) for i in range(original.n_obs)
    ]
    labels = workspace_tmp_path / "labels.json"
    labels.write_bytes(canonical_json({
        "format": "devbench.nb-fold-cluster-labels/1",
        "split_report_sha256": _sha(report),
        "clustering_counts_sha256": bindings["clustering_counts_sha256"],
        "cell_ids_sha256": bindings["cell_ids_sha256"],
        "labels": [{"cell_id": cell, "cluster_id": cluster}
                   for cell, cluster in zip(original.obs_names, clusters, strict=True)],
        "inference_performed": False,
    }))
    fold = split[1] / "inference.h5ad"
    stage = _mounts(
        workspace_tmp_path / "aggregate", "scrna_nb_fold_pseudobulk",
        {"inference_counts": fold, "split_report": report, "cluster_labels": labels,
         "donor_roster": split[1] / "donor_roster.json"},
        condition="fixed", numerator="1", denominator="0",
    )
    _checked_run(*stage)
    result = json.loads((stage[1] / "aggregation_report.json").read_bytes())
    assert result["missing_profiles"] == int(incomplete)
    if incomplete:
        assert result["refusal_codes"] == ["incomplete_donor_pair"]
        assert result["design_status"] == "held" and result["design"] is None
    else:
        assert result["design_status"] == "ready"
        assert result["design"]["residual_df"] == 5
    observed = pd.read_csv(stage[1] / "metadata.csv", dtype=str)
    assert len(observed) == 12 - int(incomplete)
    assert set(observed.loc[observed["donor"] == "donor-0", "condition"]) == (
        {"0"} if incomplete else {"0", "1"}
    )
    counts = ad.read_h5ad(fold).X
    with h5py.File(stage[1] / "pseudobulk.h5", "r") as handle:
        for position, row in enumerate(observed.itertuples()):
            included = (original.obs["donor"] == row.donor).to_numpy()
            included &= np.asarray(clusters) == row.condition
            expected = np.asarray(counts[included].sum(axis=0)).ravel()
            np.testing.assert_array_equal(handle["expression"][:, position], expected)
    if incomplete:
        fit = _mounts(
            workspace_tmp_path / "fit", "scrna_nb_marker_fit",
            {
                "pseudobulk_counts": stage[1] / "pseudobulk.h5",
                "pseudobulk_metadata": stage[1] / "metadata.csv",
                "aggregation_report": stage[1] / "aggregation_report.json",
            },
            condition="fixed", numerator="1", denominator="0", fdr_threshold=0.05,
        )
        with pytest.raises(ValueError, match="complete paired donor design"):
            run_mounted_scrna(*fit)
        assert not list(fit[1].iterdir())


def test_nb_pseudobulk_refuses_repeated_specimens_as_replicates():
    from rna_kernels.scrna_nb_pseudobulk import _profile_inventory
    from rna_kernels.scrna_upstream_contract import CountSplitPseudobulkParameters

    parameters = CountSplitPseudobulkParameters(
        condition="fixed", numerator="1", denominator="0",
    )
    rows = [
        ({"cluster_id": str(i)},
         {"donor": "donor-1", "specimen": f"sample-{i}", "condition": "fixed"})
        for i in range(2)
    ]
    with pytest.raises(ValueError, match="one specimen per donor"):
        _profile_inventory(rows, parameters)


@pytest.mark.parametrize("defect", ["overlap", "in_sample", "reordered", "rounded"])
def test_mounted_nb_split_checks_calibration_and_measurements(
    expression, workspace_tmp_path, defect,
):
    if defect == "rounded":
        altered = ad.read_h5ad(expression)
        altered.layers["counts"] = altered.layers["counts"].astype(float) + altered.X
        altered.write_h5ad(expression)
    units = ("001",) if defect == "overlap" else ("external",)
    path = _split_dispersion(expression, workspace_tmp_path / "theta.json", units=units)
    document = json.loads(path.read_bytes())
    if defect == "in_sample":
        document["source_sha256"] = document["analysis_counts_sha256"]
    if defect == "reordered":
        document["feature_ids"].reverse()
    path.write_bytes(canonical_json(document))
    mounts = _mounts(
        workspace_tmp_path / "split", "scrna_nb_countsplit",
        {"expression": expression, "split_dispersion": path},
        seed=93, clustering_fraction=0.4,
    )
    with pytest.raises(
        ValueError, match="disjoint|independent|feature axis|raw integer",
    ):
        run_mounted_scrna(*mounts)
    assert not list(mounts[1].iterdir())


def test_mounted_scoped_chain_preserves_exact_counts(
    expression, workspace_tmp_path, monkeypatch,
):
    pytest.importorskip("scanpy")
    qc = _qc(workspace_tmp_path / "qc", expression)
    report = json.loads((qc / "qc_report.json").read_bytes())
    assert report["cells_retained"] == 35
    assert report["excluded_cell_ids"] == ["0000"]
    assert report["qc_group_key"] == "lane"

    lane_claim = _mounts(
        workspace_tmp_path / "doublets", "scrna_lane_doublets",
        {"expression": expression, "qc_metrics": qc / "qc_metrics.csv"},
        capture_key="lane",
    )
    assert _checked_run(*lane_claim)["status"] == "unavailable"
    doublets = json.loads((lane_claim[1] / "doublet_report.json").read_bytes())
    assert [lane["cells"] for lane in doublets["batches"]] == [17, 18]
    predictions = pd.read_csv(lane_claim[1] / "predictions.csv")
    assert predictions["predicted_doublet"].isna().all()

    prepare = _mounts(
        workspace_tmp_path / "prepare", "scrna_prepare",
        {"expression": expression, "qc_metrics": qc / "qc_metrics.csv",
         "qc_features": qc / "qc_features.csv"}, n_top_genes=14,
    )
    assert _checked_run(*prepare)["status"] == "completed"
    source = ad.read_h5ad(expression)
    selected = ad.read_h5ad(prepare[1] / "selected.h5ad")
    assert selected.shape == (35, 14)
    assert selected.obs_names.tolist() == source.obs_names[1:].tolist()
    counts = source[selected.obs_names, selected.var_names].layers["counts"]
    assert (counts != selected.layers["counts"]).nnz == 0
    totals = np.asarray(source.layers["counts"][1:].sum(axis=1)).ravel()
    expected = np.log1p(counts.toarray() * (1e4 / totals[:, None]))
    np.testing.assert_allclose(selected.X.toarray(), expected, rtol=1e-6)
    hvg = pd.read_csv(prepare[1] / "hvg.csv")
    assert hvg["highly_variable"].sum() == 14

    pytest.importorskip("igraph")
    integration = _mounts(
        workspace_tmp_path / "integration", "scrna_integrate",
        {"selected": prepare[1] / "selected.h5ad"}, correction="none", n_pcs=6,
    )
    assert _checked_run(*integration)["status"] == "completed"
    out = integration[1]
    report = json.loads((out / "integration_report.json").read_bytes())
    assert report["published_condition"] == "uncorrected"
    embedding = pd.read_csv(out / "embedding.csv", dtype={0: str})
    assert embedding.iloc[:, 0].tolist() == selected.obs_names.tolist()
    assert np.isfinite(embedding[["UMAP_1", "UMAP_2"]]).all().all()
    assert sparse.load_npz(out / "graph.npz").shape == (35, 35)
    assert (out / "embedding.png").read_bytes().startswith(b"\x89PNG\r\n\x1a\n")

    from devbench.analysis import embedding as embedding_module

    def collapsed_correction(store, folder, **_parameters):
        baseline = np.load(store.path("pca") / "latent.npy", allow_pickle=False)
        np.save(folder / "latent.npy", np.zeros_like(baseline), allow_pickle=False)

    monkeypatch.setattr(embedding_module, "_save_harmony", collapsed_correction)
    correction = _mounts(
        workspace_tmp_path / "correction", "scrna_integrate",
        {"selected": prepare[1] / "selected.h5ad"}, correction="harmony", n_pcs=6,
    )
    partial = _checked_run(*correction)
    assert partial["status"] == "partial"
    assert partial["reason"] == "requested_correction_rejected"
    out = correction[1]
    report = json.loads((out / "integration_report.json").read_bytes())
    assert report["published_condition"] == "uncorrected"
    assert not report["conditions"]["harmony"]["embedding_valid"]
    assert report["conditions"]["uncorrected"]["provenance"]["graph_sha256"] == (
        partial["output_sha256"]["graph.npz"]
    )


def test_prepare_rejects_hash_bound_totals_that_do_not_match_counts(
    expression, workspace_tmp_path,
):
    pytest.importorskip("scanpy")
    from devbench.analysis.streaming import read_table

    qc = _qc(workspace_tmp_path / "qc", expression)
    metrics = read_table(qc / "qc_metrics.csv", label_columns=("batch",))
    metrics.loc["0001", "total_counts"] += 1
    altered = workspace_tmp_path / "different_totals.csv"
    metrics.to_csv(altered)
    claim = _mounts(
        workspace_tmp_path / "prepare", "scrna_prepare",
        {"expression": expression, "qc_metrics": altered,
         "qc_features": qc / "qc_features.csv"}, n_top_genes=14,
    )
    with pytest.raises(ValueError, match="QC totals do not match the raw counts layer"):
        run_mounted_scrna(*claim)
    assert not list(claim[1].iterdir())


def test_scran_unavailable_keeps_report_and_withholds_preparation(
    expression, workspace_tmp_path, monkeypatch,
):
    pytest.importorskip("scanpy")
    from rna_kernels import scrna_scran

    monkeypatch.setattr(scrna_scran, "_pinned_runtime", lambda _folder: False)
    qc = _qc(workspace_tmp_path / "qc", expression)
    claim = _mounts(
        workspace_tmp_path / "scran", "scrna_prepare",
        {"expression": expression, "qc_metrics": qc / "qc_metrics.csv",
         "qc_features": qc / "qc_features.csv"}, normalization="scran",
    )
    summary = _checked_run(*claim)
    assert summary["status"] == "unavailable"
    assert summary["reason"] == "scran_runtime_unavailable"
    assert set(summary["output_sha256"]) == {"normalization_report.json"}
    report = json.loads((claim[1] / "normalization_report.json").read_bytes())
    assert report["qc_passing_cells"] == 35
    assert report["requested_package_version"] == "1.40.0"


def test_mounted_integration_requires_bound_external_label_qualification(
    expression, workspace_tmp_path,
):
    pytest.importorskip("scanpy")
    pytest.importorskip("igraph")
    from devbench.analysis.checkpoints import digest

    root = workspace_tmp_path
    data = ad.read_h5ad(expression)[1:, :].copy()
    selected = root / "prepared.h5ad"
    data.write_h5ad(selected)
    labels = root / "labels.json"
    labels.write_bytes(canonical_json({
        cell: f"fixture-{index % 2}"
        for index, cell in enumerate(data.obs_names)
    }))
    qualification = root / "qualification.json"
    review = {
        "format": "devbench.integration-label-verification/1",
        "actor": "synthetic-fixture-reviewer",
        "rationale": "Declared independent fixture labels exercise byte binding.",
        "label_origin": "independent_assignment",
        "source_sha256": sha256(b"synthetic-orthogonal-source").hexdigest(),
        "labels_sha256": _sha(labels),
        "selected_sha256": _sha(selected),
        "cell_axis_sha256": digest(data.obs_names.tolist()),
    }
    qualification.write_bytes(canonical_json(review))
    sources = {
        "selected": selected, "labels": labels, "label_qualification": qualification,
    }
    claim = _mounts(
        root / "qualified", "scrna_integrate_qualified", sources,
        correction="none", n_pcs=6,
    )
    assert _checked_run(*claim)["status"] == "completed"
    report = json.loads((claim[1] / "integration_report.json").read_bytes())
    assert report["label_qualification"]["qualification_sha256"] == _sha(qualification)
    assert report["scib"]["status"] in {"measured", "unavailable"}
    assert report["scib"]["provenance"]["labels_sha256"] == _sha(labels)

    review["selected_sha256"] = sha256(b"different-cohort").hexdigest()
    qualification.write_bytes(canonical_json(review))
    mismatched = _mounts(
        root / "mismatched", "scrna_integrate_qualified", sources,
        correction="none", n_pcs=6,
    )
    with pytest.raises(ValueError, match="integration_label_cohort_mismatch"):
        run_mounted_scrna(*mismatched)
    assert not list(mismatched[1].iterdir())


def test_mounted_droplet_status_retains_original_provenance(workspace_tmp_path):
    from tests.test_p3_scrna_droplets import _provenance, _write_tenx

    source = workspace_tmp_path / "raw_droplets.h5"
    _write_tenx(source)
    provenance = _provenance(source)
    proof = workspace_tmp_path / "raw_droplet_provenance.json"
    proof.write_bytes(canonical_json({
        "format": "devbench.raw-droplet-provenance/1", **asdict(provenance),
    }))
    claim = _mounts(
        workspace_tmp_path / "droplets", "scrna_droplets",
        {"droplets": source, "droplet_provenance": proof}, method="emptydrops",
        lower=10,
    )
    summary = _checked_run(*claim)
    assert summary["status"] == "unavailable"
    assert summary["reason"] == "pinned_droplet_runtime_required"
    assert set(summary["output_sha256"]) == {"droplet_report.json"}
    report = json.loads((claim[1] / "droplet_report.json").read_bytes())
    assert report["source_sha256"] == _sha(source)
    assert report["source_provenance_sha256"] == provenance.sha256()


@pytest.mark.parametrize("parameters", [
    {"batch_key": "batch", "min_genes": True},
    {"batch_key": "batch", "mad_limit": float("inf")},
    {"batch_key": "  "},
    {"batch_key": "batch", "expression": "/unbound/path"},
    {"batch_key": "batch", "correction": "none"},
])
def test_step_contract_rejects_invalid_or_other_stage_parameters(parameters):
    with pytest.raises(ValueError):
        SingleCellStepSpec.model_validate({
            "operation": "scrna_batch_qc", "parameters": parameters,
        })


@pytest.mark.parametrize("failure", ["wrong_sha", "noncanonical_request"])
def test_worker_blocks_unbound_request_before_output(
    expression, workspace_tmp_path, failure,
):
    inputs, outputs, digest = _mounts(
        workspace_tmp_path / "qc", "scrna_batch_qc", {"expression": expression},
    )
    if failure == "wrong_sha":
        with (inputs / "expression.h5ad").open("ab") as stream:
            stream.write(b"unexpected")
    else:
        with (inputs / "request.json").open("ab") as stream:
            stream.write(b"\n")
        digest = _sha(inputs / "request.json")
    with pytest.raises(ValueError):
        run_mounted_scrna(inputs, outputs, digest)
    assert not list(outputs.iterdir())
