"""Controller proposals retain a frozen comparison for operator review."""

import pytest
from jsonschema import Draft202012Validator

from devbench.analysis.method_planner_contract import (
    ADAPTIVE_CONTROLLER_SCHEMA,
    validate_adaptive_controller_decision,
)
from devbench.engine.exploratory_benchmark import BenchmarkArm, ExploratoryBenchmarkPlan
from devbench_engine.adapters import to_openai_strict_schema
from tests.test_investigation_invalidation import digest


def _plan():
    def arm(identity):
        return BenchmarkArm(
            step_id=identity,
            method_version_id=digest(identity + "-method"),
            parameters_sha256=digest(identity + "-parameters"),
            model_port="model",
        )

    return ExploratoryBenchmarkPlan(
        benchmark_id="bounded-comparison",
        cohort_sha256=digest("cohort"),
        candidate=arm("candidate"),
        baseline=arm("baseline"),
        training_version_id="training-source",
        evaluation_version_id="sealed-evaluation",
        evaluation_step_id="evaluate",
        hypothesis="A compact expression feature improves held-donor accuracy.",
        baseline_rationale="Use a matched baseline on the same training units.",
        minimum_balanced_accuracy_gain=0.02,
        maximum_worst_class_error_increase=0.05,
        null_seed=42,
    )


def test_controller_benchmark_proposal_is_strict_and_awaits_operator():
    from devbench.platform.thesis_benchmark_actions import (
        benchmark_descriptors,
        benchmark_plans,
        propose_benchmark,
    )

    plan = _plan()
    registry = benchmark_plans((plan,))
    choice = benchmark_descriptors(registry)[0]
    proposal = {
        "kind": "propose_method_benchmark",
        "benchmark_id": plan.benchmark_id,
        "rationale": "Compare the measured donor-level scores.",
        "evidence_ids": ["relevant-diagnostic"],
    }
    strict = to_openai_strict_schema(ADAPTIVE_CONTROLLER_SCHEMA)
    Draft202012Validator(strict).validate({"action": proposal})
    assert validate_adaptive_controller_decision(proposal) == proposal
    reviewed = propose_benchmark(registry, proposal, [choice], [choice])
    assert reviewed["status"] == "awaiting_operator_review"
    assert reviewed["plan_sha256"] == choice["plan_sha256"]
    assert reviewed["claim_scope"] == "exploratory_only"
    assert "sealed-evaluation" not in str(choice)
    with pytest.raises(PermissionError, match="current comparison"):
        propose_benchmark(registry, proposal, [choice], [])
