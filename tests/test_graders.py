import pytest


def test_numeric_tolerances():
    from devbench_engine.graders_math import grade_numeric_tolerance as grade
    assert grade(10.5, 10, {"type": "relative", "value": .05})[0]
    assert grade(.01, 0, {"type": "absolute", "value": .02})[0]
    assert not grade(.01, 0, {"type": "relative", "value": .1})[0]
    assert not grade(float("nan"), 1, {"type": "absolute", "value": 1})[0]
    with pytest.raises(ValueError):
        grade(1, 1, {"type": "absolute", "value": -1})


def test_jaccard_count_and_empty():
    from devbench_engine.graders_bio import grade_label_set_jaccard as grade
    assert grade([], [])[1] == 1
    assert grade(["A", "B"], ["A", "C"])[1] == pytest.approx(1 / 3)
    assert grade(["A", "A"], ["A"], expected_count=2)[:2] == (False, 0)
    assert grade(["A"], ["A"], expected_count=2)[:2] == (False, 0)


def test_lcs_empty_reversed_and_partial():
    from devbench_engine.graders_bio import grade_longest_subsequence as grade
    assert grade([], [])[1] == 0
    assert grade(["D", "C", "B", "A"], ["A", "B", "C", "D"])[1] == .25
    assert grade(["A", "C", "D"], ["A", "B", "C", "D"])[1] == .75


def test_composite_veto_and_normalized_credit():
    from devbench_engine.composite import AllOfGrader, AverageOfGrader
    good = {"passed": True, "score": 2, "score_max": 2}
    bad = {"passed": False, "score": 1, "score_max": 4}
    assert AllOfGrader().evaluate([good, bad])["score"] == 0
    assert AllOfGrader().evaluate([good, dict(bad, role="hard_fail")])["score"] == 1
    assert not AllOfGrader().evaluate([dict(good, role="hard_fail")])["passed"]
    partial = AverageOfGrader(pass_rule="score_threshold", score_threshold=.5).evaluate([good, bad])
    assert partial["score"] == .5 and partial["passed"]
    assert AverageOfGrader(pass_rule="min_passing", min_passing_children=1).evaluate([good, bad])["passed"]
    assert not AverageOfGrader().evaluate([good, bad])["passed"]
    with pytest.raises(ValueError):
        AverageOfGrader().evaluate([])
    with pytest.raises(ValueError):
        AverageOfGrader().evaluate([dict(good, score=float("nan"))])


def test_missing_fields_cannot_pass_empty_set_checks():
    from devbench_engine.runner import evaluate_grader
    result = evaluate_grader({}, {"type": "label_set_jaccard", "field": "genes", "expected": []}, {})
    assert not result["passed"]
