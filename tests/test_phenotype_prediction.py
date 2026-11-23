import hashlib
import json

import numpy as np
import pandas as pd
import pytest


def _inputs(root, *, planted=True, conflict=False, donors=40, features=60, seed=605):
    from devbench.core.phenotype_prediction import PhenotypePredictionSpec

    rng = np.random.default_rng(seed)
    labels = rng.permutation(np.tile([0, 1], donors // 2))
    counts = rng.poisson(24, size=(donors, features))
    if planted:
        counts[labels == 1, :3] += rng.poisson(125, size=(sum(labels), 3))
        counts[labels == 0, 3:6] += rng.poisson(80, size=(sum(labels == 0), 3))
    counts[:, -1] = 0
    ids = [f"sample-{i}" for i in range(donors)]
    units = [f"donor-{i}" for i in range(donors)]
    if conflict:
        counts = np.vstack((counts, counts[0]))
        labels = np.append(labels, 1 - labels[0])
        units.append(units[0])
        ids.append("conflicting-sample")
    table = pd.DataFrame(counts, columns=[f"gene-{i}" for i in range(features)])
    table.insert(0, "sample_id", ids)
    metadata = pd.DataFrame({"sample_id": ids, "donor_id": units, "label": labels})
    paths = [root / f"{name}.csv" for name in ("counts", "metadata")]
    for path, frame in zip(paths, (table, metadata), strict=True):
        frame.to_csv(path, index=False)
    return (
        paths,
        PhenotypePredictionSpec(
            counts_sha256=hashlib.sha256(paths[0].read_bytes()).hexdigest(),
            metadata_sha256=hashlib.sha256(paths[1].read_bytes()).hexdigest(),
            n_splits=5,
            inner_splits=3,
            n_hvg=96,
            seed=29,
        ),
        table,
        metadata,
    )


def _locked_eval(root, spec, table, metadata, *, leaky=False):
    features = list(table.columns[1:])
    entries = []
    rng = np.random.default_rng(836)
    labels = rng.permutation(np.tile([0, 1], 5))
    independent = rng.poisson(24, size=(len(labels), len(features)))
    independent[labels == 1, :3] += rng.poisson(125, size=((labels == 1).sum(), 3))
    independent[labels == 0, 3:6] += rng.poisson(80, size=((labels == 0).sum(), 3))
    independent[:, -1] = rng.integers(50, 151, size=len(labels))
    donor_ids = metadata["donor_id"].tolist()
    for i, (label, counts) in enumerate(zip(labels, independent, strict=True)):
        identifier = donor_ids[i] if leaky else f"external-donor-{i}"
        entries.append(
            {
                "observation": f"external-sample-{i}",
                "unit": identifier,
                "label": str(label),
                "counts": counts.tolist(),
            }
        )
    path = root / "sealed_evaluation.json"
    path.write_text(json.dumps({"features": features, "rows": entries}))
    return path, spec.model_copy(
        update={
            "independent_evaluation_partition_sha256": hashlib.sha256(
                path.read_bytes()
            ).hexdigest(),
        }
    )


def test_planted_signal_in_grouped_outer_folds(tmp_path):
    from devbench.core.phenotype_prediction import fit_phenotype_prediction

    paths, spec, _, _ = _inputs(tmp_path)
    receipt = fit_phenotype_prediction(*paths, spec)
    metrics = receipt["out_of_fold_metrics"]
    assert metrics["roc_auc"] > 0.95 and metrics["pr_auc"] > 0.95
    assert metrics["brier_score"] < 0.1
    assert 0 <= metrics["calibration_error"] <= 1
    assert 0 <= receipt["feature_selection_stability"] <= 1
    assert receipt["count1_unchanged"] and not receipt["clinical_release_allowed"]
    for fold in receipt["outer_folds"]:
        assert not (set(fold["training_donors"]) & set(fold["test_donors"]))
        assert set(f"gene-{i}" for i in range(6)) <= set(fold["selected_features"])


def test_high_dimensional_random_labels_do_not_leak(tmp_path):
    from devbench.core.phenotype_prediction import fit_phenotype_prediction

    # A single 40-donor draw has wide sampling variability. Check the
    # prespecified independent draws jointly and keep the original draw.
    aucs = []
    for seed in range(605, 613):
        paths, spec, _, _ = _inputs(tmp_path, planted=False, features=2000, seed=seed)
        result = fit_phenotype_prediction(*paths, spec)
        aucs.append(result["out_of_fold_metrics"]["roc_auc"])
        assert all(
            len(fold["selected_features"]) == spec.n_hvg
            for fold in result["outer_folds"]
        )
    assert 0.40 <= np.mean(aucs) <= 0.60, aucs
    assert max(aucs) < 0.82, aucs


def test_locked_eval_is_excluded_from_hvg_selection(tmp_path):
    from devbench.core.phenotype_prediction import fit_phenotype_prediction

    paths, spec, table, metadata = _inputs(tmp_path)
    locked, spec = _locked_eval(tmp_path, spec, table, metadata)
    receipt = fit_phenotype_prediction(
        *paths, spec, independent_evaluation_partition=locked
    )
    external = receipt["independent_evaluation"]
    assert external["metrics"]["roc_auc"] > 0.95
    assert "gene-59" not in external["selected_features"]
    assert external["metrics"]["n_donors"] == 10


def test_refuses_locked_donor_leakage(tmp_path):
    from devbench.core.phenotype_prediction import fit_phenotype_prediction

    paths, spec, table, metadata = _inputs(tmp_path)
    locked, spec = _locked_eval(tmp_path, spec, table, metadata, leaky=True)
    with pytest.raises(ValueError, match="donor_leakage"):
        fit_phenotype_prediction(*paths, spec, independent_evaluation_partition=locked)


def test_refuses_conflicting_donor_labels(tmp_path):
    from devbench.core.phenotype_prediction import fit_phenotype_prediction

    paths, spec, _, _ = _inputs(tmp_path, conflict=True)
    with pytest.raises(ValueError, match="conflicting_labels"):
        fit_phenotype_prediction(*paths, spec)


def test_refuses_insufficient_donors_per_class(tmp_path):
    from devbench.core.phenotype_prediction import fit_phenotype_prediction

    paths, spec, _, _ = _inputs(tmp_path, donors=8)
    with pytest.raises(ValueError, match="donors_per_class"):
        fit_phenotype_prediction(*paths, spec)


def test_donor_repeats_and_cohorts_remain_in_one_outer_fold(tmp_path):
    from devbench.core.phenotype_prediction import fit_phenotype_prediction

    paths, spec, counts, metadata = _inputs(tmp_path)
    # Each cohort contains one donor per class. Give one donor a repeat.
    metadata["cohort_id"] = metadata.groupby("label").cumcount().map("c{}".format)
    repeated_meta = metadata.iloc[[0]].assign(sample_id="repeat-sample")
    repeated_counts = counts.iloc[[0]].assign(sample_id="repeat-sample")
    pd.concat([metadata, repeated_meta]).to_csv(paths[1], index=False)
    pd.concat([counts, repeated_counts]).to_csv(paths[0], index=False)
    spec = spec.model_copy(
        update={
            "counts_sha256": hashlib.sha256(paths[0].read_bytes()).hexdigest(),
            "metadata_sha256": hashlib.sha256(paths[1].read_bytes()).hexdigest(),
        }
    )
    receipt = fit_phenotype_prediction(*paths, spec)
    cohorts = metadata.set_index("donor_id")["cohort_id"].to_dict()
    test_donors = []
    for fold in receipt["outer_folds"]:
        training = {cohorts[donor] for donor in fold["training_donors"]}
        test = {cohorts[donor] for donor in fold["test_donors"]}
        assert training.isdisjoint(test)
        test_donors.extend(fold["test_donors"])
    assert sorted(test_donors) == sorted(cohorts)
    assert receipt["cv_group_column"] == "cohort_id"


def test_technical_repeats_leave_each_donor_with_fixed_loss_weight():
    from devbench.core.phenotype_prediction import _weights

    metadata = pd.DataFrame({"donor_id": ["a", "b", "c"]})
    repeats = metadata.loc[metadata.index.repeat([2, 3, 4])].copy()
    repeats["weight"] = _weights(repeats)
    assert np.allclose(repeats.groupby("donor_id")["weight"].sum(), _weights(metadata))
