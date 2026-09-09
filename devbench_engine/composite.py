"""Binary conjunction/veto and normalized partial credit."""
import math
from typing import List


def _validate(children: List[dict]) -> None:
    if not children:
        raise ValueError("Composite graders require at least one child")
    for child in children:
        score, maximum = child.get("score", 0), child.get("score_max", 1)
        if not isinstance(child.get("passed"), bool):
            raise ValueError("Child passed must be a boolean")
        if not math.isfinite(score) or not math.isfinite(maximum) or maximum <= 0 or not 0 <= score <= maximum:
            raise ValueError("Child scores must be finite and between zero and positive score_max")
        if child.get("role", "gate") not in ("gate", "hard_fail"):
            raise ValueError("Child role must be gate or hard_fail")


def _effective(child: dict) -> bool:
    return not child["passed"] if child.get("role") == "hard_fail" else child["passed"]


class AllOfGrader:
    def evaluate(self, children: List[dict]) -> dict:
        _validate(children)
        passed = all(_effective(child) for child in children)
        return {"passed": passed, "score": float(passed), "score_max": 1.0,
                "reasoning": "All requirements satisfied" if passed else "A gate failed or a veto fired", "children": children}


class AverageOfGrader:
    def __init__(self, pass_rule: str = "all", min_passing_children: int = 1, score_threshold: float = .75) -> None:
        if pass_rule not in ("all", "min_passing", "score_threshold"):
            raise ValueError("Unknown pass_rule")
        if not isinstance(min_passing_children, int) or min_passing_children < 0 or not 0 <= score_threshold <= 1:
            raise ValueError("Invalid composite pass threshold")
        self.pass_rule = pass_rule
        self.min_passing_children = min_passing_children
        self.score_threshold = score_threshold

    def evaluate(self, children: List[dict]) -> dict:
        _validate(children)
        if any(child.get("role") == "hard_fail" for child in children):
            raise ValueError("Veto roles belong in all_of; average_of scores raw child credit")
        score = sum(child["score"] for child in children) / sum(child.get("score_max", 1) for child in children)
        passing = sum(child["passed"] for child in children)
        passed = (passing == len(children) if self.pass_rule == "all" else
                  passing >= self.min_passing_children if self.pass_rule == "min_passing" else score >= self.score_threshold)
        return {"passed": passed, "score": score, "score_max": 1.0,
                "reasoning": f"{passing}/{len(children)} children passed; normalized score={score:.6g}", "children": children}
