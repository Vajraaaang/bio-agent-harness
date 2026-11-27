"""Nested donor-grouped binary phenotype prediction from raw RNA counts."""

import hashlib
import math
import warnings
from dataclasses import dataclass
from itertools import combinations
from pathlib import Path
from typing import Any, Literal

import numpy as np
import pandas as pd
from numpy.typing import NDArray
from pydantic import Field, model_validator
from scipy.special import logit
from sklearn.exceptions import ConvergenceWarning
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, brier_score_loss, roc_auc_score
from sklearn.model_selection import StratifiedGroupKFold
from sklearn.preprocessing import StandardScaler
from statsmodels.genmod.families import Binomial
from statsmodels.genmod.generalized_linear_model import GLM
from statsmodels.tools.sm_exceptions import (
    PerfectSeparationError,
    PerfectSeparationWarning,
)

from rna_kernels.normalization import log1p_counts_per_10k

from ._files import read_bounded_file
from ._hashing import decode_receipt_json
from ._rna_solver_inputs import (
    DENSE_WORK_BYTES,
    DENSE_WORK_ENTRIES,
    read_count_and_metadata,
    require_labels,
    solver_receipt,
)
from .scientific_types import FrozenContract, Sha256

_Floats = NDArray[np.float64]
_Ints = NDArray[np.intp]


class PhenotypePredictionSpec(FrozenContract):
    format: Literal["devbench.phenotype-prediction/1"] = (
        "devbench.phenotype-prediction/1"
    )
    counts_sha256: Sha256
    metadata_sha256: Sha256
    independent_evaluation_partition_sha256: Sha256 | None = None
    n_splits: int = Field(default=5, ge=2, le=10)
    inner_splits: int = Field(default=3, ge=2, le=10)
    n_hvg: int = Field(default=200, ge=1, le=10_000)
    regularization_c: tuple[float, ...] = Field(
        default=(0.01, 0.1, 1.0, 10.0), min_length=1, max_length=16
    )
    seed: int = Field(default=0, ge=0, le=2**32 - 1)

    @model_validator(mode="after")
    def valid_regularization(self) -> "PhenotypePredictionSpec":
        if any(not 1e-6 <= c <= 1e6 for c in self.regularization_c) or len(
            set(self.regularization_c)
        ) != len(self.regularization_c):
            raise ValueError(
                "regularization_c_requires_distinct_positive_bounded_values"
            )
        return self


def _profiles(metadata: pd.DataFrame) -> tuple[pd.DataFrame, str]:
    group = "cohort_id" if "cohort_id" in metadata else "donor_id"
    columns = list(dict.fromkeys(("donor_id", "label", group)))
    require_labels(metadata, tuple(columns))
    if set(metadata["label"]) != {"0", "1"}:
        raise ValueError("phenotype_requires_both_binary_labels_0_and_1")
    if (metadata.groupby("donor_id")["label"].nunique() > 1).any():
        raise ValueError("conflicting_labels_for_biological_unit")
    donors = metadata.loc[:, columns].drop_duplicates()
    if donors["donor_id"].duplicated().any():
        raise ValueError("each_biological_unit_requires_one_cohort")
    return donors, group


def _folds(
    metadata: pd.DataFrame, n_splits: int, seed: int
) -> list[tuple[_Ints, _Ints]]:
    donors, group = _profiles(metadata)
    if donors.groupby("label").size().min() < n_splits:
        raise ValueError("insufficient_donors_per_class_for_grouped_folds")
    if donors.groupby("label")[group].nunique().min() < n_splits:
        raise ValueError("insufficient_independent_groups_per_class_for_grouped_folds")
    splitter = StratifiedGroupKFold(n_splits=n_splits, shuffle=True, random_state=seed)
    folds = []
    for learn, assess in splitter.split(donors, donors["label"], donors[group]):
        training = donors.iloc[learn]
        test = donors.iloc[assess]
        _check_disjoint(training, test)
        if training["label"].nunique() != 2 or test["label"].nunique() != 2:
            raise ValueError("grouped_fold_does_not_support_both_binary_classes")
        train_rows = np.flatnonzero(metadata["donor_id"].isin(training["donor_id"]))
        test_rows = np.flatnonzero(metadata["donor_id"].isin(test["donor_id"]))
        folds.append((train_rows, test_rows))
    return folds


def _check_disjoint(training: pd.DataFrame, test: pd.DataFrame) -> None:
    if set(training["donor_id"]) & set(test["donor_id"]):
        raise ValueError("donor_leakage_across_training_and_evaluation")
    if ("cohort_id" in training) != ("cohort_id" in test):
        raise ValueError("both_partitions_require_matching_cohort_metadata")
    if "cohort_id" in training and set(training["cohort_id"]) & set(test["cohort_id"]):
        raise ValueError("cohort_leakage_across_training_and_evaluation")


def _normalize(counts: _Floats) -> _Floats:
    totals = counts.sum(axis=1, keepdims=True)
    if np.any(totals == 0):
        raise ValueError("phenotype_requires_positive_sample_library_sizes")
    return log1p_counts_per_10k(counts, totals)


def _weights(metadata: pd.DataFrame) -> _Floats:
    repeats = metadata.groupby("donor_id")["donor_id"].transform("size").to_numpy()
    return 1.0 / repeats


@dataclass(frozen=True)
class _Preprocessor:
    columns: _Ints
    scaler: StandardScaler

    @classmethod
    def fit(
        cls, counts: _Floats, weights: _Floats, n_hvg: int
    ) -> tuple["_Preprocessor", _Floats]:
        normalized = _normalize(counts)
        means = np.average(normalized, axis=0, weights=weights)
        variances = np.average(np.square(normalized - means), axis=0, weights=weights)
        variances[np.ptp(normalized, axis=0) == 0] = 0
        ranked = np.argsort(-variances, kind="stable")
        columns = ranked[variances[ranked] > 0][:n_hvg]
        if not len(columns):
            raise ValueError("training_fold_has_no_variable_features")
        selected = normalized[:, columns]
        scaler = StandardScaler().fit(selected, sample_weight=weights)
        return cls(columns, scaler), scaler.transform(selected)

    def transform(self, counts: _Floats) -> _Floats:
        return self.scaler.transform(_normalize(counts)[:, self.columns])


def _logistic(
    features: _Floats, labels: pd.Series, weights: _Floats, c: float, seed: int
) -> LogisticRegression:
    try:
        with warnings.catch_warnings():
            warnings.filterwarnings("error", category=ConvergenceWarning)
            return LogisticRegression(
                C=c, solver="lbfgs", max_iter=2000, random_state=seed
            ).fit(features, labels.astype(int), sample_weight=weights)
    except ConvergenceWarning as error:
        raise ValueError("phenotype_logistic_model_did_not_converge") from error


def _donor_predictions(metadata: pd.DataFrame, predictions: _Floats) -> pd.DataFrame:
    cells = metadata[["donor_id", "label"]].assign(probability=predictions)
    return cells.groupby("donor_id", sort=True).agg(
        label=("label", "first"), probability=("probability", "mean")
    )


def _choose_c(
    counts: _Floats, metadata: pd.DataFrame, spec: PhenotypePredictionSpec
) -> tuple[float, list[dict[str, float]]]:
    candidates = sorted(spec.regularization_c)
    aucs = np.zeros(len(candidates))
    brier = np.zeros(len(candidates))
    for training, test in _folds(metadata, spec.inner_splits, spec.seed):
        train, validation = metadata.iloc[training], metadata.iloc[test]
        weights = _weights(train)
        prep, learned = _Preprocessor.fit(counts[training], weights, spec.n_hvg)
        held_out = prep.transform(counts[test])
        for i, c in enumerate(candidates):
            model = _logistic(learned, train["label"], weights, c, spec.seed)
            donor = _donor_predictions(validation, model.predict_proba(held_out)[:, 1])
            labels, probability = donor["label"].astype(int), donor["probability"]
            aucs[i] += roc_auc_score(labels, probability)
            brier[i] += brier_score_loss(labels, probability)
    best = min(
        range(len(candidates)), key=lambda i: (-aucs[i], brier[i], candidates[i])
    )
    return candidates[best], [
        {
            "c": c,
            "mean_inner_roc_auc": float(auc / spec.inner_splits),
            "mean_inner_brier_score": float(loss / spec.inner_splits),
        }
        for c, auc, loss in zip(candidates, aucs, brier, strict=True)
    ]


def _calibration_slope(
    labels: _Ints, probabilities: _Floats
) -> tuple[float | None, str | None]:
    if np.ptp(probabilities) <= np.finfo(float).eps:
        return None, "constant_predictions"
    epsilon = np.finfo(float).eps
    logits = logit(np.clip(probabilities, epsilon, 1 - epsilon))
    design = np.column_stack((np.ones(len(logits)), logits))
    # Complete or quasi separation makes the unpenalized calibration slope
    # unidentifiable even if an optimizer returns finite coefficients.
    a, b = logits[labels == 0], logits[labels == 1]
    if a.max() <= b.min() or b.max() <= a.min():
        return None, "separated_calibration_labels"
    try:
        with warnings.catch_warnings():
            warnings.filterwarnings("error", category=PerfectSeparationWarning)
            warnings.filterwarnings("error", category=RuntimeWarning)
            fit = GLM(labels, design, family=Binomial()).fit()
    except (
        ValueError,
        ArithmeticError,
        np.linalg.LinAlgError,
        PerfectSeparationWarning,
        PerfectSeparationError,
        RuntimeWarning,
    ):
        return None, "calibration_fit_failed"
    if not fit.converged or not math.isfinite(float(fit.params[1])):
        return None, "calibration_fit_failed"
    return float(fit.params[1]), None


def _metrics(metadata: pd.DataFrame, predictions: _Floats) -> dict[str, Any]:
    donors = _donor_predictions(metadata, predictions)
    labels = donors["label"].to_numpy(dtype=int)
    probabilities = donors["probability"].to_numpy(dtype=float)
    bins = np.minimum(9, (10 * probabilities).astype(int))
    ece = float(
        np.abs(np.bincount(bins, weights=probabilities - labels, minlength=10)).sum()
        / len(labels)
    )
    slope, missing = _calibration_slope(labels, probabilities)
    return {
        "n_donors": len(labels),
        "roc_auc": float(roc_auc_score(labels, probabilities)),
        "pr_auc": float(average_precision_score(labels, probabilities)),
        "brier_score": float(brier_score_loss(labels, probabilities)),
        "calibration_error": ece,
        "calibration_slope": slope,
        "calibration_slope_missing_reason": missing,
    }


def _locked_inputs(
    path: Path, digest: str, genes: list[str]
) -> tuple[_Floats, pd.DataFrame]:
    content = read_bounded_file(path, DENSE_WORK_BYTES)
    if hashlib.sha256(content).hexdigest() != digest:
        raise ValueError("independent_evaluation_partition_digest_mismatch")
    report = decode_receipt_json(content)
    if not isinstance(report, dict) or set(report) != {"features", "rows"}:
        raise ValueError("invalid_locked_evaluation_artifact")
    features, rows = report["features"], report["rows"]
    if (
        not isinstance(features, list)
        or any(type(name) is not str for name in features)
        or len(features) != len(genes)
        or set(features) != set(genes)
        or not isinstance(rows, list)
        or not rows
        or len(rows) * len(features) > DENSE_WORK_ENTRIES
    ):
        raise ValueError("locked_evaluation_requires_matching_bounded_feature_axis")
    return _locked_rows(rows, pd.Index(features).get_indexer(genes))


def _locked_rows(rows: list[Any], order: _Ints) -> tuple[_Floats, pd.DataFrame]:
    annotations = []
    matrix = np.empty((len(rows), len(order)), dtype=float)
    exact = {"observation", "unit", "label", "counts"}
    for index, row in enumerate(rows):
        if not isinstance(row, dict) or set(row) not in (exact, exact | {"cohort"}):
            raise ValueError("invalid_locked_evaluation_row")
        counts = row["counts"]
        if (
            not isinstance(counts, list)
            or len(counts) != len(order)
            or any(type(x) is not int or not 0 <= x <= 2**53 for x in counts)
            or not 0 < sum(counts) <= 2**53
        ):
            raise ValueError("locked_evaluation_requires_count1")
        matrix[index] = np.asarray(counts, dtype=float)[order]
        entry = {
            "sample_id": row["observation"],
            "donor_id": row["unit"],
            "label": row["label"],
        }
        if "cohort" in row:
            entry["cohort_id"] = row["cohort"]
        annotations.append(entry)
    meta = pd.DataFrame(annotations)
    require_labels(meta, ("sample_id",))
    if meta["sample_id"].duplicated().any():
        raise ValueError("locked_evaluation_observation_ids_must_be_unique")
    return matrix, meta.set_index("sample_id")


def _fit_final(
    counts: _Floats,
    training: pd.DataFrame,
    chosen: float,
    spec: PhenotypePredictionSpec,
) -> tuple[_Preprocessor, LogisticRegression]:
    weights = _weights(training)
    prep, features = _Preprocessor.fit(counts, weights, spec.n_hvg)
    return prep, _logistic(features, training["label"], weights, chosen, spec.seed)


def _evaluate_locked(
    path: Path,
    digest: str,
    counts: _Floats,
    metadata: pd.DataFrame,
    genes: list[str],
    spec: PhenotypePredictionSpec,
) -> dict[str, Any]:
    chosen, tuning = _choose_c(counts, metadata, spec)
    prep, fit = _fit_final(counts, metadata, chosen, spec)
    evaluation, test = _locked_inputs(path, digest, genes)
    _profiles(test)
    _check_disjoint(metadata, test)
    if not metadata.index.intersection(test.index).empty:
        raise ValueError("observation_leakage_across_training_and_evaluation")
    predictions = fit.predict_proba(prep.transform(evaluation))[:, 1]
    return {
        "partition_sha256": digest,
        "chosen_c": chosen,
        "tuning": tuning,
        "selected_features": [genes[i] for i in prep.columns],
        "metrics": _metrics(test, predictions),
    }


def _nested_cv(
    counts: _Floats,
    metadata: pd.DataFrame,
    genes: list[str],
    spec: PhenotypePredictionSpec,
) -> tuple[_Floats, list[dict[str, Any]]]:
    oof = np.full(len(metadata), np.nan)
    reports: list[dict[str, Any]] = []
    for training, evaluation in _folds(metadata, spec.n_splits, spec.seed):
        train, test = metadata.iloc[training], metadata.iloc[evaluation]
        chosen, tuning = _choose_c(counts[training], train, spec)
        prep, fit = _fit_final(counts[training], train, chosen, spec)
        if np.isfinite(oof[evaluation]).any():
            raise ValueError("duplicate_out_of_fold_evaluation")
        oof[evaluation] = fit.predict_proba(prep.transform(counts[evaluation]))[:, 1]
        reports.append(
            {
                "fold": len(reports) + 1,
                "training_donors": sorted(set(train["donor_id"])),
                "test_donors": sorted(set(test["donor_id"])),
                "chosen_c": chosen,
                "tuning": tuning,
                "selected_features": [genes[i] for i in prep.columns],
            }
        )
    if not np.isfinite(oof).all():
        raise ValueError("incomplete_out_of_fold_evaluation")
    return oof, reports


def fit_phenotype_prediction(
    counts_path: Path,
    metadata_path: Path,
    spec: PhenotypePredictionSpec,
    *,
    independent_evaluation_partition: Path | None = None,
) -> dict[str, Any]:
    """Fit nested binary classifiers using sample_id, donor_id, label and
    optional cohort_id metadata. Evaluate an optional digest-pinned, separately
    supplied original-count partition only after fitting the final training model.
    """
    spec = PhenotypePredictionSpec.model_validate(spec.model_dump())
    digest = spec.independent_evaluation_partition_sha256
    if (independent_evaluation_partition is None) != (digest is None):
        raise ValueError("independent_evaluation_partition_requires_path_and_digest")
    counts, metadata, genes = read_count_and_metadata(
        counts_path,
        metadata_path,
        spec.counts_sha256,
        spec.metadata_sha256,
        "sample_id",
    )
    oof, folds = _nested_cv(counts, metadata, genes, spec)
    sets = [set(fold["selected_features"]) for fold in folds]
    jaccard = [len(a & b) / len(a | b) for a, b in combinations(sets, 2)]
    locked = None
    if independent_evaluation_partition is not None and digest is not None:
        locked = _evaluate_locked(
            independent_evaluation_partition, digest, counts, metadata, genes, spec
        )
    predictions = _donor_predictions(metadata, oof)
    return solver_receipt(
        "devbench.phenotype-prediction-result/1",
        spec,
        {
            "counts": spec.counts_sha256,
            "metadata": spec.metadata_sha256,
            "independent_evaluation_partition": digest,
        },
        {
            "status": "completed",
            "claim_scope": "binary_phenotype_predictive_performance",
            "count1_unchanged": True,
            "cv_group_column": "cohort_id" if "cohort_id" in metadata else "donor_id",
            "class_donor_counts": _profiles(metadata)[0]["label"]
            .value_counts()
            .to_dict(),
            "out_of_fold_metrics": _metrics(metadata, oof),
            "out_of_fold_donor_predictions": predictions.reset_index().to_dict(
                "records"
            ),
            "feature_selection_stability": float(np.mean(jaccard)),
            "outer_folds": folds,
            "independent_evaluation": locked,
            "method": (
                "StratifiedGroupKFold on unique donors, optionally grouped by cohort, "
                "for both nested levels. Each fit learns variability selection and "
                "scaling from its training data following per-sample log1p library "
                "normalization. C maximizes mean inner-fold donor ROC-AUC. Mean "
                "inner-fold Brier score, then stronger regularization, break ties. "
                "Metrics give donors equal "
                "weight using mean sample probabilities. PR-AUC is average precision. "
                "Calibration error uses ten equal-width bins. Stability is mean "
                "pairwise Jaccard across outer training feature sets."
            ),
        },
    )
