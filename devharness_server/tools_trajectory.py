"""Chronologically ordered, signed trajectory metrics."""
import math
from collections.abc import Mapping
from numbers import Real
from typing import Annotated, Dict

from pydantic import Field

from devharness_core.stages import parse_stage


def calculate_temporal_monotonicity(
    stage_values: Annotated[Dict[str, float], Field(min_length=2)],
) -> dict:
    """Compute signed step agreement from at least two distinct finite times."""
    if not isinstance(stage_values, Mapping):
        raise ValueError("Stage estimates must be a mapping")
    pairs = []
    for stage, value in stage_values.items():
        if isinstance(value, bool) or not isinstance(value, Real):
            raise ValueError("Stage estimates must be finite real numbers")
        try:
            estimate = float(value)
            time = parse_stage(stage)
        except OverflowError as error:
            raise ValueError("Stage times and estimates must be finite real numbers") from error
        if not math.isfinite(estimate):
            raise ValueError("Stage estimates must be finite real numbers")
        pairs.append((time, estimate))
    if len(pairs) < 2:
        raise ValueError("At least two distinct timepoints are required")
    pairs.sort()
    if len({stage for stage, _ in pairs}) != len(pairs):
        raise ValueError("Stage names must map to distinct numeric times")
    differences = [b[1] - a[1] for a, b in zip(pairs, pairs[1:])]
    increasing, decreasing = all(x > 0 for x in differences), all(x < 0 for x in differences)
    return {"is_monotonic": increasing or decreasing,
            "direction": "increasing" if increasing else "decreasing" if decreasing else "non_monotonic",
            "score": sum((x > 0) - (x < 0) for x in differences) / len(differences)}
