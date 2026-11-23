import json
from hashlib import sha256
from importlib.util import find_spec

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
        ("fragmentation", "fragmented_graph"),
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
    elif error == "fragmentation":
        graph = sparse.block_diag([graph[:18, :18], graph[18:, 18:]], format="csr")
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
    if find_spec("scib_metrics") is None:
        assert conservation["status"] == "unavailable"
        assert conservation["reason"] == "scib_metrics_not_in_runtime"
    else:
        assert conservation["status"] == "measured"
        assert 0 <= conservation["silhouette_label"] <= 1
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
