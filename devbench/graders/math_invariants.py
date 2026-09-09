"""Explicit mathematical validity gates, independent of model judges."""
import math
from devbench_engine.graders_math import grade_temporal_monotonicity


def grade_design_matrix_gate(answer: dict, threshold: float = 1000) -> dict:
    value = answer.get("condition_number")
    diagnosed = answer.get("rank_deficient") is True or answer.get("collinearity_diagnosed") is True
    if value is None:
        return {"passed": True, "score": 1.0, "score_max": 1.0,
                "reasoning": "No explicit condition number supplied; task schema controls required evidence"}
    valid = type(value) in (int, float) and math.isfinite(value) and value >= 1
    passed = bool(diagnosed or (valid and value <= threshold))
    return {"passed": passed, "score": float(passed), "score_max": 1.0,
            "reasoning": "Condition number checked or collinearity diagnosed" if passed else "Invalid or excessive undiagnosed condition number"}


def grade_fdr_invariant(answer: dict) -> dict:
    statistics = answer.get("gene_statistics", {})
    valid = isinstance(statistics, dict)
    for record in statistics.values() if valid else []:
        if not isinstance(record, dict):
            valid = False
            break
        p = record.get("pvalue", record.get("p_value"))
        q = record.get("qvalue", record.get("q_value", record.get("padj")))
        if not (all(type(v) in (int, float) and math.isfinite(v) for v in (p, q)) and 0 <= p <= q <= 1):
            valid = False
            break
    return {"passed": valid, "score": float(valid), "score_max": 1.0,
            "reasoning": "No explicit p/q violation" if valid else "Invalid probabilities or q < p"}
