"""Donor-disjoint annotation and evidence-binding checks with synthetic counts."""

import json

import anndata as ad
import numpy as np
import pandas as pd
import pytest
from pydantic import ValidationError

from devbench.core.scrna_annotation import (
    AnnotationParameters,
    FoundationComparison,
    require_foundation_benchmark,
)
from rna_kernels import scrna_annotation as worker


def _cohort(path, donors, prefix, *, rare_missing=False):
    labels = ["T", "B", "B"] * len(donors)
    values = [[40, 40, 0, 0], [0, 0, 40, 40], [0, 0, 40, 40]] * len(donors)
    if rare_missing:
        values[-3] = [5, 5, 5, 5]
    obs = pd.DataFrame(
        {"donor": np.repeat(donors, 3), "cell_type": labels},
        index=[f"{prefix}{i}" for i in range(len(labels))],
    )
    ad.AnnData(
        np.array(values), obs=obs, var=pd.DataFrame(index=["T1", "T2", "B1", "B2"])
    ).write_h5ad(path)


@pytest.fixture
def annotation(workspace_tmp_path):
    expr, ref = (
        workspace_tmp_path / f"{key}.h5ad" for key in ("expression", "reference")
    )
    _cohort(expr, ["cal", "test1", "test2"], "q", rare_missing=True)
    _cohort(ref, ["r"], "r")
    params = {
        "method": "marker_panel",
        "panels": {"T": ["T1", "T2"], "B": ["B1", "B2"]},
        "truth_key": "cell_type",
        "calibration_donors": ["cal"],
        "label_provenance": "orthogonal_protein_or_spatial",
        "rare_prevalence": 0.4,
    }
    return {"expression": expr, "reference": ref}, params


def test_donor_accuracy_and_rare_abstention(annotation, workspace_tmp_path):
    paths, params = annotation
    files = worker.run_m2("scrna_annotate_cells", paths, params, workspace_tmp_path)
    result = json.loads(files["result"].read_text())
    assert result["per_class"]["T"] == {
        "rare_in_reference": True,
        "abstention_rate": 0.5,
        "held_out_recall": 0.5,
        "evaluated_cells": 2,
        "evaluated_donors": 2,
        "calibrated_confidence": 1.0,
    }
    assert result["held_out_donor_stability"]["test2"]["balanced_accuracy"] == 0.5
    assert (
        not result["e5_label_checks_passed"] and not result["clinical_release_allowed"]
    )
    assert ["T", 2, 0.5] in json.loads(files["plot_sidecar"].read_text())["rows"]


def test_training_donors_cannot_reenter_validation(annotation, workspace_tmp_path):
    paths, params = annotation
    _cohort(paths["reference"], ["test1"], "r")
    with pytest.raises(ValueError, match="overlap"):
        worker.run_m2("scrna_annotate_cells", paths, params, workspace_tmp_path)


@pytest.mark.parametrize("method", ["popv", "scarches"])
def test_query_transfer_hides_truth_from_adapters(
    monkeypatch, annotation, workspace_tmp_path, method
):
    paths, params = annotation
    params.update(method=method, panels={})
    if method == "popv":
        for k in ("ontology", "ontology_graph", "ontology_embedding"):
            paths[k] = workspace_tmp_path / (k + ".txt")
            paths[k].write_text(k)
    donors = []

    def observe(query, reference, paths, params, temp):
        assert list(query.obs) == ["donor"]
        assert set(query.obs.donor).isdisjoint(reference.obs.donor)
        assert query.obs.donor.nunique() == 1
        donors.append(query.obs.donor.iloc[0])
        return pd.Series("B", index=query.obs_names)

    monkeypatch.setattr(worker, "_predict", observe)
    out = worker.run_m2("scrna_annotate_cells", paths, params, workspace_tmp_path)
    result = json.loads(out["result"].read_text())
    assert donors == ["cal", "test1", "test2"]
    assert result["per_class"]["B"]["calibrated_confidence"] == pytest.approx(2 / 3)
    assert result["abstention_rate"] == 1


@pytest.mark.parametrize("method", ["scGPT", "Geneformer", "scBERT"])
def test_scfm_requires_exact_executed_comparison(method):
    data = dict(
        stage="E4",
        axis="B2",
        model=method,
        baseline="logistic_regression",
        input_sha256="0" * 64,
        frozen_plan_sha256="1" * 64,
        model_execution_sha256="2" * 64,
        baseline_execution_sha256="3" * 64,
        evaluation_sha256="4" * 64,
        training_donors=("d1",),
        evaluation_donors=("d2",),
        metric="accuracy",
        higher_is_better=True,
        model_score=0.92,
        baseline_score=0.88,
        outcome="improved",
    )
    rec = FoundationComparison(**data)
    args = dict(axis="B2", input_sha256="0" * 64, as_default=False)
    with pytest.raises(ValueError, match="E4"):
        require_foundation_benchmark(method, **args, comparison=rec)
    with pytest.raises(ValueError, match="E4"):
        require_foundation_benchmark(
            method,
            **{**args, "axis": "B7"},
            comparison=rec,
            verify_execution=lambda _: True,
        )
    with pytest.raises(ValueError, match="default"):
        require_foundation_benchmark(
            method,
            **{**args, "as_default": True},
            comparison=rec,
            verify_execution=lambda _: True,
        )
    require_foundation_benchmark(
        method, **args, comparison=rec, verify_execution=lambda _: True
    )
    with pytest.raises(ValidationError, match="donor-disjoint"):
        FoundationComparison(**{**data, "evaluation_donors": ("d1",)})
    with pytest.raises(ValidationError, match="outcome"):
        FoundationComparison(**{**data, "outcome": "tied"})
    with pytest.raises(ValidationError):
        AnnotationParameters(method=method, label_provenance="internal_clustering")


def test_provenance_is_mandatory():
    with pytest.raises(ValidationError):
        AnnotationParameters.model_validate({"method": "scarches"})


def test_popv_runs_ontology_consensus(monkeypatch, annotation, workspace_tmp_path):
    import sys
    from types import SimpleNamespace

    paths, raw = annotation
    params = AnnotationParameters(**dict(raw, method="popv", panels={}))
    query, reference = (
        worker._read(paths["expression"]),
        worker._read(paths["reference"]),
    )
    for name in ("ontology", "ontology_graph", "ontology_embedding"):
        paths[name] = workspace_tmp_path / name

    def process(query_matrix, ref_matrix, **opts):
        assert opts["cl_obo_folder"] == [
            str(paths[k]) for k in ("ontology", "ontology_graph", "ontology_embedding")
        ]
        assert (
            opts["ref_labels_key"] == "cell_type" and opts["ref_batch_key"] == "donor"
        )
        return SimpleNamespace(adata=query_matrix)

    def annotate(dataset):
        dataset.obs["_dataset"] = "query"
        dataset.obs["popv_majority_vote_prediction"] = "wrong_field"
        dataset.obs["popv_prediction"] = "ontology_consensus"

    fake = SimpleNamespace(
        preprocessing=SimpleNamespace(Process_Query=process),
        annotation=SimpleNamespace(annotate_data=annotate),
    )
    monkeypatch.setitem(sys.modules, "popv", fake)
    predictions = worker._predict(query, reference, paths, params, workspace_tmp_path)
    assert predictions.index.equals(query.obs_names) and set(predictions) == {
        "ontology_consensus"
    }


def test_scarches_executes_query_update(monkeypatch, annotation, workspace_tmp_path):
    import sys
    from types import SimpleNamespace

    paths, raw = annotation
    params = AnnotationParameters(**dict(raw, method="scarches", panels={}))
    query, reference = (
        worker._read(paths["expression"]),
        worker._read(paths["reference"]),
    )
    calls = []

    class Scvi:
        @staticmethod
        def setup_anndata(ref, batch_key):
            assert ref is reference and batch_key == "donor"

        def __init__(self, data):
            self.data = data

        def train(self, **args):
            calls.append(("fit", self.data.n_obs))

    class Scanvi(Scvi):
        @classmethod
        def from_scvi_model(cls, base, **args):
            return cls(base.data)

        @classmethod
        def prepare_query_anndata(cls, data, labeled):
            calls.append(("prepare", data.n_obs))

        @classmethod
        def load_query_data(cls, data, labeled):
            assert set(data.obs["cell_type"]) == {"__unknown__"}
            calls.append(("transfer", data.n_obs))
            return cls(data)

        def predict(self, soft):
            assert soft
            return pd.DataFrame({"T": 0.1, "B": 0.9}, index=self.data.obs_names)

    fake = SimpleNamespace(
        settings=SimpleNamespace(seed=None),
        model=SimpleNamespace(SCVI=Scvi, SCANVI=Scanvi),
    )
    monkeypatch.setitem(sys.modules, "scvi", fake)
    values = worker._predict(query, reference, paths, params, workspace_tmp_path)
    assert set(values) == {"B"} and calls == [
        ("fit", 3),
        ("fit", 3),
        ("prepare", 9),
        ("transfer", 9),
        ("fit", 9),
    ]


def test_annotation_claim_requires_independent_receipt_authority(
    annotation, workspace_tmp_path
):
    from devbench.core.scientific_types import content_sha256
    from devbench.core.scrna_annotation import require_verified_annotation
    from devbench.mcp.plot_sidecars import verify_plot_sidecar

    paths, params = annotation
    _cohort(paths["expression"], ["cal", "test1", "test2"], "q")
    out = worker.run_m2("scrna_annotate_cells", paths, params, workspace_tmp_path)
    verify_plot_sidecar(out["plot_sidecar"], "json")
    result = json.loads(out["result"].read_text())
    digest = content_sha256(result)
    verify = dict(
        expression_sha256=result["input_sha256"]["expression"],
        reference_sha256=result["input_sha256"]["reference"],
        verify_custody=lambda s: s == digest,
    )
    require_verified_annotation(result, **verify)
    for altered in [
        {**result, "label_provenance": "internal_clustering"},
        {**result, "method": "forged"},
        {**result, "held_out_stability_passed": False},
    ]:
        with pytest.raises(ValueError, match="stability required"):
            require_verified_annotation(altered, **verify)
