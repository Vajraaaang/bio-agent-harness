"""Controller proposals and operator approval for frozen exploratory comparisons."""

from collections.abc import Mapping, Sequence
from types import MappingProxyType

from devbench.core._hashing import canonical_sha256
from devbench.engine.exploratory_benchmark import (
    ExploratoryBenchmarkPlan,
    freeze_exploratory_benchmark,
)


def benchmark_plans(values) -> Mapping[str, ExploratoryBenchmarkPlan]:
    """Admit a bounded catalog supplied by the trusted coordinator."""
    if not isinstance(values, (list, tuple)) or len(values) > 16:
        raise ValueError("Use at most 16 coordinator-supplied comparison plans")
    plans = tuple(ExploratoryBenchmarkPlan.model_validate(value) for value in values)
    registry = {plan.benchmark_id: plan for plan in plans}
    if len(registry) != len(plans):
        raise ValueError("Benchmark choices require distinct plan identities")
    return MappingProxyType(registry)


def _descriptor(plan: ExploratoryBenchmarkPlan) -> dict[str, str]:
    return {
        "benchmark_id": plan.benchmark_id,
        "candidate_step_id": plan.candidate.step_id,
        "baseline_step_id": plan.baseline.step_id,
        "plan_sha256": canonical_sha256(plan.model_dump(mode="json")),
    }


def benchmark_descriptors(
    registry: Mapping[str, ExploratoryBenchmarkPlan],
) -> list[dict[str, str]]:
    """Disclose comparison identities without protected input or evaluation results."""
    return [_descriptor(plan) for _, plan in sorted(registry.items())]


def propose_benchmark(
    registry: Mapping[str, ExploratoryBenchmarkPlan],
    decision: dict,
    offered: Sequence[dict],
    current: Sequence[dict],
) -> dict[str, str]:
    plan = registry.get(decision["benchmark_id"])
    if plan is None or not decision.get("evidence_ids"):
        raise PermissionError("Benchmark proposals require a current comparison")
    descriptor = _descriptor(plan)
    if descriptor not in offered or descriptor not in current:
        raise PermissionError("Benchmark proposals require a current comparison")
    return {
        "status": "awaiting_operator_review",
        "kind": "propose_method_benchmark",
        "benchmark_id": plan.benchmark_id,
        "plan_sha256": descriptor["plan_sha256"],
        "claim_scope": "exploratory_only",
    }


def approve_benchmark_proposal(
    workflow, executor, registry: Mapping[str, ExploratoryBenchmarkPlan],
    sequence: int, *, actor: str, rationale: str, authorize_operator,
):
    """Approve the exact retained comparison with the existing protected evaluator."""
    decisions = workflow.controller_state()["decisions"]
    if type(sequence) is not int or not 0 <= sequence < len(decisions):
        raise ValueError("Use the exact retained benchmark proposal sequence")
    retained = decisions[sequence]["result"]
    plan = registry.get(retained.get("benchmark_id"))
    if (
        plan is None
        or retained.get("status") != "awaiting_operator_review"
        or retained.get("kind") != "propose_method_benchmark"
        or retained.get("plan_sha256") != _descriptor(plan)["plan_sha256"]
    ):
        raise PermissionError("Benchmark approval requires its exact current proposal")
    return freeze_exploratory_benchmark(
        workflow, executor, plan, actor=actor, rationale=rationale,
        authorize_operator=authorize_operator,
    )
