"""Shared strict conversion of developmental stage labels to numeric time."""

import math
import re
from numbers import Real
from typing import Any, Iterable

import numpy as np

_NUMBER = re.compile(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?")


def parse_stage(value: Any) -> float:
    """Accept a finite number or a label containing exactly one numeric value."""
    if isinstance(value, Real) and not isinstance(value, bool):
        number = float(value)
    elif isinstance(value, str):
        matches = _NUMBER.findall(value.strip())
        if len(matches) != 1:
            raise ValueError("Stage must contain exactly one numeric time: " + repr(value))
        number = float(matches[0])
    else:
        raise ValueError("Invalid developmental stage: " + repr(value))
    if not math.isfinite(number):
        raise ValueError("Developmental time must be finite")
    return number


def parse_stages(values: Iterable[Any]) -> np.ndarray:
    """Convert stage labels without silently accepting ambiguous ranges."""
    return np.asarray([parse_stage(value) for value in values], dtype=float)
