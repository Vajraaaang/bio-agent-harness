"""Classifier-boundary fixtures, not a trained-model or biological evaluation."""
from __future__ import annotations

import hashlib
import json
import sys
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from tests.test_analysis_singlecell import counts_fixture


@pytest.fixture
def classifier_case(tmp_path, monkeypatch):
    data = counts_fixture()[:4].copy()
    scores = pd.DataFrame([[.9, .08], [.6, .5], [.1, .85], [.4, .3]],
                          index=data.obs_names, columns=["T cell", "B cell"])
    labels = pd.DataFrame({"predicted_labels": ["T cell", "T cell", "B cell", "T cell"]},
                          index=data.obs_names)
    response = SimpleNamespace(probability_matrix=scores, predicted_labels=labels,
                               decision_matrix=np.log(scores / (1-scores)), adata=data)
    model = tmp_path / "fixture.pkl"
    model.write_bytes(b"contract fixture only; never deserialize as a classifier")
    digest = hashlib.sha256(model.read_bytes()).hexdigest()
    monkeypatch.setitem(sys.modules, "celltypist", SimpleNamespace(annotate=lambda *args, **kwargs: response))
    return data, model, digest, response


def annotate(case, **kwargs):
    from devbench.analysis.singlecell import annotate_celltypist
    data, model, digest, _ = case
    return annotate_celltypist(data, model, expected_sha256=digest, **kwargs)


def test_valid_response_keeps_existing_thresholds_and_reports_completion(classifier_case):
    labels, scores, report = annotate(classifier_case)
    assert report["status"] == "completed"
    assert report["minimum_score"] == .5 and report["minimum_margin"] == .15
    assert labels.tolist() == ["T cell", "uncertain", "B cell", "uncertain"]
    np.testing.assert_allclose(scores, [.9, .6, .85, .4])
    assert labels.index.equals(scores.index)
    assert report["uncertain_cells"] == 2
    assert "not_calibrated" in report["scope"]


def test_accepted_numeric_thresholds_produce_finite_json(classifier_case):
    _, _, report = annotate(classifier_case, minimum_score=np.float32(.5), minimum_margin=np.float32(.15))
    encoded = json.dumps(report, allow_nan=False)
    decoded = json.loads(encoded)
    assert decoded["minimum_score"] == .5
    assert decoded["minimum_margin"] == float(np.float32(.15))


@pytest.mark.parametrize("field", ["probability_matrix", "predicted_labels"])
@pytest.mark.parametrize("mutation", ["reordered", "duplicate", "foreign"])
def test_cell_identity_mismatch_is_rejected(classifier_case, field, mutation):
    frame = getattr(classifier_case[-1], field)
    if mutation == "reordered":
        setattr(classifier_case[-1], field, frame.iloc[::-1])
    elif mutation == "duplicate":
        frame.index = ["c0", "c0", "c2", "c3"]
    else:
        frame.index = ["outside", "c1", "c2", "c3"]
    with pytest.raises(ValueError, match="celltypist.*identity"):
        annotate(classifier_case)


@pytest.mark.parametrize("bad", [-.01, 1.01, np.nan, np.inf])
def test_invalid_score_values_are_rejected(classifier_case, bad):
    classifier_case[-1].probability_matrix.iloc[0, 0] = bad
    with pytest.raises(ValueError, match="invalid_celltypist_scores"):
        annotate(classifier_case)


@pytest.mark.parametrize("mutation", ["empty", "duplicate", "missing"])
def test_invalid_score_classes_are_rejected(classifier_case, mutation):
    response = classifier_case[-1]
    if mutation == "empty":
        response.probability_matrix = response.probability_matrix.iloc[:, :0]
    elif mutation == "duplicate":
        response.probability_matrix.columns = ["T cell", "T cell"]
    else:
        response.probability_matrix.columns = ["T cell", None]
    with pytest.raises(ValueError, match="invalid_celltypist_score_classes"):
        annotate(classifier_case)


@pytest.mark.parametrize("label", [None, "not a classifier class", "B cell"])
def test_missing_or_foreign_predicted_labels_are_rejected(classifier_case, label):
    classifier_case[-1].predicted_labels.iloc[0, 0] = label
    with pytest.raises(ValueError, match="invalid_celltypist_labels"):
        annotate(classifier_case)


@pytest.mark.parametrize("parameter", ["minimum_score", "minimum_margin"])
@pytest.mark.parametrize("bad", [np.nan, np.inf, -.1, 1.1, True, None, "0.5"])
def test_invalid_abstention_parameters_are_rejected_before_classifier(classifier_case, monkeypatch, parameter, bad):
    def forbidden(*args, **kwargs):
        pytest.fail("invalid policy must fail before invoking the classifier")
    monkeypatch.setattr(sys.modules["celltypist"], "annotate", forbidden)
    with pytest.raises(ValueError, match="invalid_celltypist_thresholds"):
        annotate(classifier_case, **{parameter: bad})


def test_chunked_annotation_finishes_and_resumes_without_classifier(classifier_case, tmp_path, monkeypatch):
    from devbench.analysis.annotation import run_annotation
    from devbench.analysis.checkpoints import CheckpointStore, sha256
    from devbench.analysis.streaming import read_table

    data, model, digest, response = classifier_case
    source = tmp_path / "source.h5ad"
    data.write_h5ad(source)
    store = CheckpointStore(tmp_path / "checkpoints", {"data_sha256": sha256(source), "fixture": True})
    def qc(folder):
        pd.DataFrame({"qc_pass": [True, False, True, True],
                      "total_counts": np.asarray(data.X.sum(axis=1)).ravel()},
                     index=data.obs_names).to_csv(folder / "metrics.csv")
    store.run("qc", qc)
    def classifier(piece, **kwargs):
        return SimpleNamespace(probability_matrix=response.probability_matrix.loc[piece.obs_names],
                               predicted_labels=response.predicted_labels.loc[piece.obs_names],
                               decision_matrix=response.decision_matrix.loc[piece.obs_names], adata=piece)
    monkeypatch.setattr(sys.modules["celltypist"], "annotate", classifier)
    result = run_annotation(source, store, layer=None, model_path=model, model_sha256=digest,
                            block_rows=2)
    frame = read_table(result / "predictions.csv")
    assert frame.index.tolist() == ["c0", "c2", "c3"]
    assert frame.cell_type.tolist() == ["T cell", "B cell", "uncertain"]
    assert json.loads((result / "report.json").read_text())["status"] == "completed"
    assert store.verify("annotation", ("annotation_00000000", "annotation_00000002"))
    receipt = sha256(result / "receipt.json")
    def forbidden(*args, **kwargs):
        pytest.fail("verified annotation chunks should resume without invoking the classifier")
    monkeypatch.setattr(sys.modules["celltypist"], "annotate", forbidden)
    resumed = run_annotation(source, store, layer=None, model_path=model, model_sha256=digest,
                             block_rows=2)
    assert resumed == result and sha256(result / "receipt.json") == receipt
