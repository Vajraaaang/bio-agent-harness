"""Verified per-file annotation totals constrain the local comparison."""

from devbench.analysis.workbench_cell_type_comparison import compare_cell_types
from devbench.analysis.workbench_request_contract import (
    CELL_TYPE_COMPARISON_QUESTION,
    classify_request,
)


def test_uses_verified_denominators_and_keeps_inference_held():
    profiles = [
        {"sha256": value, "filename": f"file-{index}.h5ad", "observed": {"cells": count}}
        for index, (value, count) in enumerate((("a" * 64, 10), ("b" * 64, 20)))
    ]
    answers = [
        {"sha256": profile["sha256"], "tables": [{
            "source_ref": "obs/celltypes", "denominator": profile["observed"]["cells"],
            "missing_count": missing, "truncated": False, "rows": rows,
        }]}
        for profile, missing, rows in zip(
            profiles, [2, 0], [[["A", 8, 80.0]], [["A", 5, 25.0], ["B", 15, 75.0]]],
            strict=True,
        )
    ]
    question = CELL_TYPE_COMPARISON_QUESTION + ". Attachment order corresponds to days 9, 11."
    request = classify_request(question)
    steps, tables, _, completion = compare_cell_types(
        profiles, answers, request["cell_type_comparison"]["declared_days"],
    )
    assert completion == "completed"
    assert tables[0]["rows"] == [["B", 0.0, 75.0, 75.0], ["A", 80.0, 25.0, -55.0]]
    assert [row["status"] for row in steps] == ["completed", "completed", "completed", "needs evidence"]
    answers[1]["tables"][0]["truncated"] = True
    steps, tables, _, completion = compare_cell_types(profiles, answers, [9, 11])
    assert completion == "partial" and tables == [] and steps[2]["status"] == "blocked"
