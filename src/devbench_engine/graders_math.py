"""Deterministic scalar and directional agreement graders."""
import math
from numbers import Real
from typing import Dict, Tuple

Grade = Tuple[bool, float, str]


def grade_numeric_tolerance(actual: float, expected: float, tolerances: Dict) -> Grade:
    kind, tolerance = tolerances.get("type", "absolute"), float(tolerances.get("value", 0))
    if kind not in ("absolute", "relative") or tolerance < 0 or not math.isfinite(tolerance):
        raise ValueError("Tolerance must be finite, nonnegative, and absolute or relative")
    if any(isinstance(x, bool) or not isinstance(x, Real) or not math.isfinite(x) for x in (actual, expected)):
        return False, 0.0, "Both values must be finite numbers"
    limit = tolerance if kind == "absolute" else tolerance * abs(expected)
    error = abs(actual - expected)
    passed = error <= limit or math.isclose(error, limit, rel_tol=1e-12, abs_tol=0)
    return passed, float(passed), f"Absolute error {error:.6g}; allowed {limit:.6g} ({kind})"


def grade_temporal_monotonicity(actual: Dict[str, float], expected: float = 1.0) -> Grade:
    from devharness_server.tools_trajectory import calculate_temporal_monotonicity
    try:
        result = calculate_temporal_monotonicity(actual)
    except ValueError as error:
        return False, 0.0, f"Invalid temporal estimates: {error}"
    return grade_numeric_tolerance(result["score"], expected, {"type": "absolute", "value": 0})
