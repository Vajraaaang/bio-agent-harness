"""Observed transitions are required before a signed temporal score can exist."""
import math

import pytest

from devbench_engine.graders_math import grade_temporal_monotonicity
from devbench_engine.runner import evaluate_grader
from devharness_server.tools_trajectory import calculate_temporal_monotonicity


INVALID = [
    {}, {"E8.5": 1}, None, [],
    {"E8.5": 1, "8.5": 2},
    {"unknown": 1, "E10.5": 2},
    {"E8.5": "1", "E10.5": "2"},
    {"E8.5": False, "E10.5": True},
    {"E8.5": None, "E10.5": 2},
    {"E8.5": math.nan, "E10.5": 2},
    {"E8.5": math.inf, "E10.5": 2},
    {"E8.5": 10 ** 400, "E10.5": 2},
    {10 ** 400: 1, 2: 2},
]


@pytest.mark.parametrize("actual", INVALID)
def test_tool_rejects_undefined_or_malformed_temporal_evidence(actual):
    with pytest.raises(ValueError):
        calculate_temporal_monotonicity(actual)


@pytest.mark.parametrize("actual", INVALID)
@pytest.mark.parametrize("expected", [1.0, 0.0, -1.0])
def test_invalid_evidence_is_a_failed_grade_for_every_expected_direction(actual, expected):
    passed, score, reason = grade_temporal_monotonicity(actual, expected)
    assert passed is False and score == 0.0
    assert "Invalid temporal estimates" in reason


@pytest.mark.parametrize("actual,score,direction", [
    ({"E10.5": 2, "E8.5": 1}, 1.0, "increasing"),
    ({"E10.5": 1, "E8.5": 2}, -1.0, "decreasing"),
    ({"E8.5": 1, "E10.5": 1}, 0.0, "non_monotonic"),
    ({"E8.5": 0, "E10.5": 1, "E12.5": 0, "E14.5": 1}, 1 / 3, "non_monotonic"),
])
def test_valid_signed_step_results_and_fractional_targets_are_preserved(actual, score, direction):
    result = calculate_temporal_monotonicity(actual)
    assert result == {"is_monotonic": abs(score) == 1.0, "direction": direction, "score": score}
    assert grade_temporal_monotonicity(actual, score)[:2] == (True, 1.0)


@pytest.mark.parametrize("actual", [{}, {"E8.5": 1}])
def test_invalid_temporal_child_is_a_diagnostic_composite_failure(actual):
    answer = {"stage_kinetics": actual, "other": 1}
    spec = {"type": "all_of", "config": {"children": [
        {"type": "temporal_monotonicity", "config": {"field": "stage_kinetics", "expected": 1}},
        {"type": "numeric_tolerance", "config": {"field": "other", "expected": 1,
            "tolerances": {"type": "absolute", "value": 0}}},
    ]}}
    result = evaluate_grader(answer, spec, {})
    assert result["passed"] is False and result["score"] == 0
    assert result["children"][0]["passed"] is False
    assert "Invalid temporal estimates" in result["children"][0]["reasoning"]
    assert result["children"][1]["passed"] is True


def test_another_failed_gate_still_vetoes_valid_temporal_evidence():
    result = evaluate_grader({"stages": {"E8.5": 1, "E10.5": 2}, "other": 0}, {
        "type": "all_of", "config": {"children": [
            {"type": "temporal_monotonicity", "config": {"field": "stages", "expected": 1}},
            {"type": "numeric_tolerance", "config": {"field": "other", "expected": 1,
                "tolerances": {"type": "absolute", "value": 0}}},
        ]}}, {})
    assert result["passed"] is False
    assert result["children"][0]["passed"] is True


def test_mcp_schema_requires_observed_transitions():
    from devharness_server.dispatcher import ToolDispatcher
    schema = next(tool for tool in ToolDispatcher().get_schemas()
                  if tool["name"] == "calculate_temporal_monotonicity")
    assert schema["inputSchema"]["properties"]["stage_values"]["minProperties"] == 2


def test_unexpected_implementation_errors_are_not_relabelled_as_bad_input(monkeypatch):
    import devharness_server.tools_trajectory as trajectory
    def broken(_):
        raise RuntimeError("unexpected implementation failure")
    monkeypatch.setattr(trajectory, "calculate_temporal_monotonicity", broken)
    with pytest.raises(RuntimeError, match="unexpected implementation failure"):
        grade_temporal_monotonicity({"E8.5": 1, "E10.5": 2})
