"""Fixed grouped scorer copied verbatim into the protected Tier B container."""

import json
import math
import random
import statistics
from collections import Counter, defaultdict
from hashlib import sha256
from pathlib import Path

_NULL_REPEATS = 64


def _read(path: Path, limit: int = 16_000_000):
    with path.open("rb") as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise ValueError("Grouped evaluation input exceeds its limit")
    return json.loads(raw)


def _finite(value: object) -> bool:
    return (
        isinstance(value, (float, int))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def _model(value: dict, features: list[str]) -> tuple[float, dict[str, float]]:
    if set(value) != {"features", "mean", "centroids"} or value["features"] != features:
        raise ValueError("Grouped model must bind the exact feature axis")
    centers = value["centroids"]
    if (
        not _finite(value["mean"])
        or type(centers) is not dict
        or not 2 <= len(centers) <= 32
        or not all(_finite(v) for v in centers.values())
    ):
        raise ValueError("Invalid grouped model coefficients")
    return value["mean"], centers


def _row_score(counts: list[int], feature_n: int) -> float:
    if len(counts) != feature_n or any(
        type(v) is not int or not 0 <= v < 2**53 for v in counts
    ):
        raise ValueError(
            "Evaluation requires original integer counts on the model axis"
        )
    total = sum(counts)
    if total <= 0:
        raise ValueError("Zero evaluation library size")
    return math.log1p(counts[0] / total * 10000)


def _group_rows(data: dict) -> dict[str, tuple[str, float]]:
    units: dict[str, tuple[str, list[float]]] = {}
    samples: set[str] = set()
    for row in data["rows"]:
        sample, unit, label = (row[key] for key in ("observation", "unit", "label"))
        if any(
            type(v) is not str or not 0 < len(v) <= 200 for v in (sample, unit, label)
        ):
            raise ValueError("Invalid held-out donor metadata")
        if sample in samples:
            raise ValueError("Repeated evaluation observation")
        samples.add(sample)
        previous, values = units.setdefault(unit, (label, []))
        if previous != label:
            raise ValueError("Outcome disagrees within a biological unit")
        values.append(_row_score(row["counts"], len(data["features"])))
    if not 4 <= len(units) <= 2_048:
        raise ValueError("Evaluation requires a bounded independent cohort")
    return {
        unit: (label, statistics.mean(values))
        for unit, (label, values) in units.items()
    }


def _discrimination(labels: list[str], predictions: list[str]) -> dict[str, float]:
    success: dict[str, list[bool]] = defaultdict(list)
    for label, prediction in zip(labels, predictions, strict=True):
        success[label].append(label == prediction)
    class_accuracy = [statistics.mean(outcomes) for outcomes in success.values()]
    return {
        "accuracy": sum(sum(outcomes) for outcomes in success.values()) / len(labels),
        "balanced_accuracy": statistics.mean(class_accuracy),
        "worst_class_error": 1 - min(class_accuracy),
    }


def _axis_sha256(groups: dict[str, tuple[str, float]]) -> str:
    axis = json.dumps(sorted(groups), ensure_ascii=False, separators=(",", ":"))
    return sha256(axis.encode()).hexdigest()


def _verify_partitions(inputs, params, features, held):
    data = _read(inputs / "training")
    training = _group_rows(data)
    if (
        features != data["features"]
        or set(training) & held.keys()
        or _axis_sha256(training) != params["training_units_sha256"]
        or _axis_sha256(held) != params["held_units_sha256"]
    ):
        raise ValueError(
            "Comparison requires the exact disjoint biological-unit partitions"
        )


def _evaluate(inputs: Path, parameters: Path) -> dict:
    data = _read(inputs / "held")
    features = data["features"]
    if not features or len(set(features)) != len(features):
        raise ValueError("Evaluation needs a nonempty unique feature axis")
    groups = _group_rows(data)
    params = _read(parameters, 4096)
    _verify_partitions(inputs, params, features, groups)
    ordered = [groups[unit] for unit in sorted(groups)]
    labels = [label for label, _ in ordered]
    if min(Counter(labels).values()) < 2:
        raise ValueError("At least two held-out donors per reported class are required")
    seed = params["null_seed"]
    arms, null = {}, {}
    for arm in ("candidate", "baseline"):
        mean, centers = _model(_read(inputs / arm, 2_000_000), features)
        if set(centers) != set(labels):
            raise ValueError("Frozen model classes must match held-out classes")
        predictions = [
            min(centers, key=lambda label: (abs(value - mean - centers[label]), label))
            for _, value in ordered
        ]
        arms[arm] = _discrimination(labels, predictions)
        rng, permuted = random.Random(seed), list(labels)
        accuracy = []
        for _ in range(_NULL_REPEATS):
            rng.shuffle(permuted)
            accuracy.append(_discrimination(permuted, predictions)["balanced_accuracy"])
        null[arm] = statistics.mean(accuracy)
    return {
        "format": "devbench.grouped-benchmark-scores/1",
        "held_out_donors": len(groups),
        "class_count": len(set(labels)),
        "arms": arms,
        "negative_control": {
            "method": "donor_label_permutation",
            "seed": seed,
            "repetitions": _NULL_REPEATS,
            "mean_balanced_accuracy": null,
        },
    }


if __name__ == "__main__":
    try:
        result = _evaluate(Path("/inputs"), Path("/method/parameters.json"))
    except (OSError, ValueError, KeyError, TypeError, OverflowError):
        raise SystemExit(
            "Frozen grouped evaluation refused the input or model"
        ) from None
    Path("/outputs/out.json").write_text(json.dumps(result, allow_nan=False))
