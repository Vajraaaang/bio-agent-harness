import json
from hashlib import sha256

import numpy as np
import pytest
from scipy import sparse

from devbench.analysis.checkpoints import CheckpointStore, digest
from devharness_core.provenance import canonical_json


def _geometry():
    rng = np.random.default_rng(829)
    values = rng.normal(size=(36, 4))
    ids = [f"cell-{i:03}" for i in range(len(values))]
    batches = ["run-a", "run-b"] * (len(values) // 2)
    graph = sparse.diags([np.ones(len(values) - 1)] * 2, [-1, 1], format="csr")
    return values, ids, batches, graph


@pytest.mark.parametrize(
    ("error", "code"),
    [
        ("nan", "nonfinite_embedding"),
        ("collapse", "collapsed_embedding"),
        ("no_edges", "graph_no_edges"),
        ("isolates", "graph_excessive_isolates"),
        ("misordered", "graph_cell_alignment"),
    ],
)
def test_governed_embedding_refuses_invalid_geometry_and_graph(error, code):
    from devbench.analysis.integration_metrics import assess_integration

    reference, cell_ids, batches, graph = _geometry()
    candidate = reference.copy()
    graph_cell_ids = cell_ids.copy()
    if error == "nan":
        candidate[-1, 0] = np.nan
    elif error == "collapse":
        candidate[:] = 12
    elif error == "no_edges":
        graph = sparse.eye(len(cell_ids), format="csr")
    elif error == "isolates":
        graph = sparse.block_diag([graph[:18, :18], graph[18:, 18:] * 0], format="csr")
    elif error == "misordered":
        graph_cell_ids.reverse()

    report = assess_integration(
        reference,
        candidate,
        graph,
        cell_ids=cell_ids,
        graph_cell_ids=graph_cell_ids,
        batches=batches,
    )
    assert report["status"] == "fail"
    assert report["embedding_valid"] is False
    assert code in report["diagnostics"]
    assert report["biological_conservation"]["status"] == "unavailable"


@pytest.mark.parametrize("n_batches", [2, 4])
def test_perfect_batch_alignment_preserves_within_batch_geometry(n_batches):
    from sklearn.neighbors import kneighbors_graph

    from devbench.analysis.integration_metrics import assess_integration
    from rna_kernels.embedding_qc import neighborhood_metrics

    rng = np.random.default_rng(862)
    shared = rng.normal(size=(36, 4))
    candidate = np.tile(shared, (n_batches, 1))
    batch_codes = np.repeat(np.arange(n_batches), len(shared))
    reference = candidate + 100 * batch_codes[:, None]
    global_geometry = neighborhood_metrics(reference, candidate, 10)
    assert global_geometry["neighborhood_retention"] < 0.6
    graph = kneighbors_graph(candidate, 10, include_self=False)
    cell_ids = [f"cell-{i}" for i in range(len(candidate))]
    report = assess_integration(
        reference, candidate, graph.maximum(graph.T),
        cell_ids=cell_ids, graph_cell_ids=cell_ids,
        batches=[f"run-{code}" for code in batch_codes],
    )
    assert report["status"] == "pass"
    assert report["geometry"]["neighborhood_retention"] == 1
    assert report["geometry"]["trustworthiness"] == 1
    assert report["batch_mixing"]["cross_batch_edge_fraction"] > 0.5


def test_within_batch_geometry_weights_eligible_batches_by_cell_count():
    from devbench.analysis.integration_metrics import assess_integration
    from rna_kernels.embedding_qc import neighborhood_metrics

    reference, cell_ids, _, graph = _geometry()
    batches = ["big"] * 24 + ["small"] * 9 + ["too-small"] * 3
    candidate = reference.copy()
    candidate[24:33] = candidate[24:33][::-1]
    small = neighborhood_metrics(reference[24:33], candidate[24:33], 10)
    report = assess_integration(
        reference, candidate, graph,
        cell_ids=cell_ids, graph_cell_ids=cell_ids, batches=batches,
    )
    assert report["geometry"]["batches_compared"] == 2
    assert report["geometry"]["cells_compared"] == 33
    assert "within_batch_geometry_incomplete" in report["warnings"]
    for metric in ("neighborhood_retention", "trustworthiness"):
        expected = (24 + 9 * small[metric]) / 33
        assert report["geometry"][metric] == pytest.approx(expected)


def test_two_lineages_with_disjoint_graph_are_accepted():
    from devbench.analysis.integration_metrics import assess_integration

    values, cell_ids, batches, graph = _geometry()
    values[18:] += 20
    graph = sparse.block_diag([graph[:18, :18], graph[18:, 18:]], format="csr")
    report = assess_integration(
        values, values, graph,
        cell_ids=cell_ids, graph_cell_ids=cell_ids, batches=batches,
    )
    assert report["status"] == "pass"
    assert report["graph"]["connected_components"] == 2
    assert report["graph"]["largest_component_fraction"] == 0.5
    assert report["graph"]["isolated_observations"] == 0
    assert "graph_disconnected_components" in report["warnings"]


def test_governed_embedding_keeps_mixing_and_unqualified_label_metrics_distinct():
    from devbench.analysis.integration_metrics import assess_integration

    values, cell_ids, batches, graph = _geometry()
    report = assess_integration(
        values,
        values,
        graph,
        cell_ids=cell_ids,
        graph_cell_ids=cell_ids,
        batches=batches,
    )
    assert report["status"] == "pass"
    assert report["embedding_valid"] is True
    assert report["geometry"]["neighborhood_retention"] == 1
    assert report["geometry"]["trustworthiness"] == 1
    assert report["graph"]["connected_components"] == 1
    assert report["batch_mixing"]["cross_batch_edge_fraction"] == 1
    assert report["biological_conservation"] == {
        "status": "unavailable",
        "reason": "verified_external_labels_required",
    }


def test_external_label_receipt_binds_bytes_cohort_and_independent_provenance(
    workspace_tmp_path,
):
    from devbench.analysis.integration_metrics import (
        ExternalLabelEvidence,
        assess_integration,
        verify_external_labels,
    )

    values, cell_ids, batches, graph = _geometry()
    labels = ["label-a", "label-b"] * (len(cell_ids) // 2)
    label_bytes = canonical_json(dict(zip(cell_ids, labels, strict=True)))
    label_file = workspace_tmp_path / "labels.json"
    label_file.write_bytes(label_bytes)
    selected_sha256 = sha256(b"synthetic selected cells").hexdigest()
    receipt = workspace_tmp_path / "verification.json"
    payload = {
        "format": "devbench.integration-label-verification/1",
        "source_sha256": sha256(b"synthetic orthogonal experiment").hexdigest(),
        "labels_sha256": sha256(label_bytes).hexdigest(),
        "selected_sha256": selected_sha256,
        "cell_axis_sha256": digest(cell_ids),
        "label_origin": "orthogonal_measurement",
        "actor": "synthetic-reviewer",
        "rationale": "Cell identities from an independent synthetic measurement.",
    }

    def bind():
        raw = canonical_json(payload)
        receipt.write_bytes(raw)
        return ExternalLabelEvidence(label_file, receipt, sha256(raw).hexdigest())

    evidence = bind()
    observed, provenance = verify_external_labels(
        evidence, cell_ids=cell_ids, selected_sha256=selected_sha256
    )
    assert observed.tolist() == labels
    assert provenance["qualification_sha256"] == evidence.qualification_sha256
    assert provenance["label_origin"] == "orthogonal_measurement"
    report = assess_integration(
        values,
        values,
        graph,
        cell_ids=cell_ids,
        graph_cell_ids=cell_ids,
        batches=batches,
        selected_sha256=selected_sha256,
        verified_labels=evidence,
    )
    conservation = report["biological_conservation"]
    assert conservation["provenance"] == provenance
    assert conservation["status"] == "measured"
    assert -1 <= conservation["cell_type_ASW"] <= 1
    assert conservation["silhouette_label"] == pytest.approx(
        (1 + conservation["cell_type_ASW"]) / 2
    )
    assert report["batch_mixing"]["batch_ASW"] is None  # Batch and label coincide.
    crossed = assess_integration(
        values, values, graph,
        cell_ids=cell_ids, graph_cell_ids=cell_ids,
        batches=["run-a", "run-a", "run-b", "run-b"] * 9,
        selected_sha256=selected_sha256, verified_labels=evidence,
    )
    assert 0 <= crossed["batch_mixing"]["batch_ASW"] <= 1
    with pytest.raises(ValueError, match="integration_label_cohort_mismatch"):
        verify_external_labels(
            evidence, cell_ids=cell_ids[::-1], selected_sha256=selected_sha256
        )

    payload["label_origin"] = "rna_derived"
    with pytest.raises(ValueError, match="integration_label_origin_unqualified"):
        verify_external_labels(
            bind(), cell_ids=cell_ids, selected_sha256=selected_sha256
        )
    label_file.write_bytes(b"{}")
    payload["label_origin"] = "orthogonal_measurement"
    with pytest.raises(ValueError, match="integration_labels_hash_mismatch"):
        verify_external_labels(
            bind(), cell_ids=cell_ids, selected_sha256=selected_sha256
        )


@pytest.mark.parametrize("correction", ["none", "harmony"])
def test_nb_fold_clustering_accepts_two_disjoint_lineages(
    workspace_tmp_path, correction,
):
    pytest.importorskip("scanpy")
    pytest.importorskip("igraph")
    if correction == "harmony":
        pytest.importorskip("harmonypy")
    import h5py
    import pandas as pd

    from devbench.core.scientific_types import content_sha256
    from rna_kernels.scrna_nb_clustering import cluster_mounted_fold
    from rna_kernels.scrna_nb_countsplit import _FoldWriter
    from rna_kernels.scrna_upstream_contract import (
        REQUEST_FORMAT,
        MountedSingleCellRequest,
    )

    rng = np.random.default_rng(216)
    matrix = rng.poisson(1, (96, 36)).astype(np.int32)
    matrix[:48, :12] += 80
    matrix[48:, 12:24] += 80
    cells = pd.Index([f"cell-{row:03}" for row in range(len(matrix))])
    genes = pd.Index([f"gene-{col}" for col in range(matrix.shape[1])])
    fold = workspace_tmp_path / "clustering.h5ad"
    with h5py.File(fold, "x") as handle:
        writer = _FoldWriter(handle, cells, genes, "clustering")
        writer.append(sparse.csr_matrix(matrix))
    fold_sha256 = sha256(fold.read_bytes()).hexdigest()
    split = workspace_tmp_path / "split_report.json"
    split.write_bytes(canonical_json({
        "format": "devbench.nb-countsplit/1",
        "split_distribution": "dirichlet_multinomial",
        "marker_inference_performed": False,
        "clustering_counts_sha256": fold_sha256,
        "cell_ids_sha256": content_sha256(tuple(cells)),
        "cells": len(cells),
        "genes": len(genes),
        "dispersion": {"feature_ids_sha256": content_sha256(tuple(genes))},
    }))
    request = MountedSingleCellRequest.model_validate({
        "format": REQUEST_FORMAT,
        "operation": "scrna_nb_fold_cluster",
        "inputs": {"clustering_counts": fold_sha256,
                   "split_report": sha256(split.read_bytes()).hexdigest()},
        "parameters": {"seed": 5, "n_top_genes": 36, "n_pcs": 8, "resolution": 0.2},
    })
    store = CheckpointStore(workspace_tmp_path / "cluster", {"input": request.inputs})
    paths = {"clustering_counts": fold, "split_report": split}
    options = {}
    if correction == "harmony":
        batches = pd.Series(["001", "1"] * 48, index=cells)
        options = {"batch_labels": batches, "correction": correction}
        with pytest.raises(ValueError, match="ordered clustering fold axis"):
            cluster_mounted_fold(
                request, paths, store, request.parameters,
                batch_labels=batches.iloc[::-1], correction=correction,
            )
    outputs = cluster_mounted_fold(request, paths, store, request.parameters, **options)
    assessment = json.loads(outputs["graph_assessment"].read_bytes())
    report = json.loads(outputs["clustering_report"].read_bytes())
    condition = correction if correction != "none" else "uncorrected"
    result = assessment["conditions"][condition]
    assert result["status"] == "pass"
    assert result["graph"]["connected_components"] == 2
    assert report["clusters"] == 2
    assert report["assigned_cells"] == 96
    assert report["graph_sha256"] == result["provenance"]["graph_sha256"]
    if correction == "harmony":
        assert report["batch_correction"]["observed_batches"] == 2
        assert report["batch_correction"]["condition_used"] == "harmony"


@pytest.fixture
def selected_checkpoint(workspace_tmp_path):
    ad = pytest.importorskip("anndata")
    sc = pytest.importorskip("scanpy")
    import pandas as pd

    rng = np.random.default_rng(29)
    counts = sparse.csr_matrix(rng.poisson(4, (72, 40)).astype(np.int32))
    data = ad.AnnData(
        counts.copy(),
        obs=pd.DataFrame(
            {
                "batch": ["library-first", "library-second"] * 36,
                "celltypes": ["RNA-derived labels are not qualifications"] * 72,
            },
            index=[f"cell-{i:03}" for i in range(72)],
        ),
    )
    data.layers["counts"] = counts
    sc.pp.normalize_total(data, target_sum=1e4)
    sc.pp.log1p(data)
    store = CheckpointStore(workspace_tmp_path / "checkpoints", {"fixture": "harmony"})
    store.run("selected", lambda folder: data.write_h5ad(folder / "selected.h5ad"))
    return store, counts


def test_real_harmony_assesses_exact_graph_and_sidecar(
    workspace_tmp_path,
    selected_checkpoint,
):
    pytest.importorskip("harmonypy")
    import anndata as ad

    from devbench.analysis.embedding import (
        render_integration_embedding,
        run_embedding_stages,
    )

    store, counts = selected_checkpoint
    arguments = {
        "batch_key": "batch",
        "seed": 17,
        "correction": "harmony",
        "n_pcs": 8,
        "governed": True,
    }
    assert run_embedding_stages(store, **arguments) == {
        "uncorrected": "pca",
        "harmony": "harmony",
    }
    assessment = store.path("integration_assessment") / "assessment.json"
    report = json.loads(assessment.read_bytes())
    assert report["status"] == "pass"
    for condition in ("uncorrected", "harmony"):
        result = report["conditions"][condition]
        graph_dir = store.path(condition + "_graph")
        assert result["embedding_valid"]
        assert (
            result["provenance"]["graph_sha256"]
            == sha256((graph_dir / "neighbors.npz").read_bytes()).hexdigest()
        )
        assert (
            result["provenance"]["embedding_csv_sha256"]
            == sha256((graph_dir / "embedding.csv").read_bytes()).hexdigest()
        )
        assert result["biological_conservation"]["reason"] == (
            "verified_external_labels_required"
        )
    retained = ad.read_h5ad(store.path("selected") / "selected.h5ad")
    assert (retained.layers["counts"] != counts).nnz == 0

    csv = store.path("harmony_graph") / "embedding.csv"
    rendered = workspace_tmp_path / "admitted.png"
    figure = render_integration_embedding(
        csv,
        rendered,
        batch_key="batch",
        condition="harmony",
    )
    assert (
        figure["data_sha256"]
        == report["conditions"]["harmony"]["provenance"]["embedding_csv_sha256"]
    )
    assert figure["image_sha256"] == sha256(rendered.read_bytes()).hexdigest()
    assert figure["plotted_cells"] == 72
    assert figure["plot_state"]["scientific_assessment"] == "pending"
    csv.write_text("modified\n")
    with pytest.raises(ValueError, match="checkpoint_artifact_mismatch"):
        run_embedding_stages(store, **arguments)


def test_governed_collapse_withholds_candidate_and_retains_baseline(
    selected_checkpoint,
    monkeypatch,
):
    from types import SimpleNamespace

    harmonypy = pytest.importorskip("harmonypy")
    from devbench.analysis.embedding import run_embedding_stages

    store, _ = selected_checkpoint
    monkeypatch.setattr(
        harmonypy,
        "run_harmony",
        lambda values, *args, **kwargs: SimpleNamespace(Z_corr=np.zeros_like(values)),
    )
    assert run_embedding_stages(
        store,
        batch_key="batch",
        seed=13,
        correction="harmony",
        governed=True,
    ) == {"uncorrected": "pca"}
    report = json.loads(
        (store.path("integration_assessment") / "assessment.json").read_bytes()
    )
    assert report["status"] == "partial"
    assert report["conditions"]["harmony"]["diagnostics"] == ["collapsed_embedding"]
    assert not (store.path("harmony_graph") / "embedding.csv").exists()


def test_real_scvi_candidate_is_assessed_before_acceptance(selected_checkpoint):
    pytest.importorskip("scvi")
    from devbench.analysis.embedding import run_embedding_stages

    store, _ = selected_checkpoint
    accepted = run_embedding_stages(
        store,
        batch_key="batch",
        seed=17,
        correction="scvi",
        scvi_epochs=2,
        n_pcs=8,
        governed=True,
    )
    report = json.loads(
        (store.path("integration_assessment") / "assessment.json").read_bytes()
    )
    candidate = report["conditions"]["scvi"]
    method = json.loads((store.path("scvi") / "method.json").read_bytes())
    assert method["method"] == "scvi_SCVI_NB"
    assert method["epochs_completed"] == 2
    assert np.isfinite(np.load(store.path("scvi") / "latent.npy")).all()
    assert candidate["graph"]["connected_components"] >= 1
    assert ("scvi" in accepted) == candidate["embedding_valid"]
    assert set(accepted) == set(report["accepted_conditions"])
