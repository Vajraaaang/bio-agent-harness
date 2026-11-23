"""Project reviewed work to a scheduling brief containing no scientific outcomes."""

from dataclasses import dataclass

from devbench.core.procedure_profile import ADMITTED_RESEARCH
from devbench.engine.workflow_operations import OperationBlocked
from rna_kernels.scrna_upstream_contract import SCRNA_OPERATIONS

from .checkpoints import digest

BRIEF_KIND = "reviewed_research_dispatch"
BRIEF_CONTEXT_KEY = "reviewed_research_brief"
BRIEF_EVIDENCE_KEY = "controller_research_brief_sha256"


def eligible_ready_research(state, ready) -> bool:
    return all(step_id in state.nodes for step_id in ready) and any(
        state.nodes[step_id].purpose == ADMITTED_RESEARCH for step_id in ready
    )


@dataclass(frozen=True)
class _Job:
    alias: str
    step_id: str
    role: str
    regime: str | None
    identity: str
    input_digests: tuple[str, ...]
    operation: str | None = None

    def public(self) -> dict[str, str | None]:
        result = {"job_id": self.alias, "fit_kind": self.role, "regime": self.regime}
        if self.operation is not None:
            result["operation"] = self.operation
        return result


def _job(workflow, state, step_id, index: int) -> _Job:
    step = state.nodes.get(step_id)
    if (
        step is None
        or step_id in state.holds
        or step.branch_id in state.retired
        or state.outcome(step_id) is not None
    ):
        raise OperationBlocked("controller_research_frontier_changed")
    research = step.purpose == ADMITTED_RESEARCH
    if research and step.regime is None:
        raise OperationBlocked("controller_research_frontier_changed")
    inputs = workflow._step_inputs(state, step)
    identity = digest(
        {
            "step": step.model_dump(mode="json"),
            "generation": state.generations[step_id],
            "inputs": {
                key: item.model_dump(mode="json") for key, item in inputs.items()
            },
        }
    )
    return _Job(
        alias=f"{'research-fit' if research else 'ready-step'}-{index}",
        step_id=step_id,
        role=("comparison" if step.comparison else "primary")
        if research
        else "registered_step",
        regime=step.regime,
        identity=identity,
        input_digests=tuple(item.sha256 for item in inputs.values()),
        operation=step.operation_id if step.operation_id in SCRNA_OPERATIONS else None,
    )


@dataclass(frozen=True)
class ResearchDispatchBrief:
    jobs: tuple[_Job, ...]
    termination_required: bool

    @property
    def roots(self) -> tuple[str, ...]:
        return tuple(sorted({root for job in self.jobs for root in job.input_digests}))

    def payload(self, brief_id: str) -> dict:
        return {
            "decision_kind": "adaptive_controller",
            "brief": BRIEF_KIND,
            "brief_id": brief_id,
            "ready": [job.alias for job in self.jobs],
            "jobs": [job.public() for job in self.jobs],
            "termination_required": self.termination_required,
            "selection_rules": (
                "Dispatch one listed job alias with an empty evidence_ids array, or "
                "stop with a partial or unresolved disposition. Stop if termination "
                "is required. These are scheduling facts only."
            ),
        }

    def require_current(self, workflow, connection) -> None:
        state = workflow._state(connection)
        current = tuple(
            _job(workflow, state, job.step_id, index)
            for index, job in enumerate(self.jobs, start=1)
        )
        if current != self.jobs:
            raise OperationBlocked("controller_research_frontier_changed")

    def resolve_action(self, action: dict) -> dict:
        if type(action) is not dict or action.get("evidence_ids") != []:
            raise ValueError("A research dispatch brief has no outcome evidence")
        if action.get("kind") == "stop" and action.get("disposition") in {
            "partial",
            "unresolved",
        }:
            return action
        if action.get("kind") == "dispatch" and not self.termination_required:
            for job in self.jobs:
                if action.get("step_id") == job.alias:
                    return {**action, "step_id": job.step_id}
        raise ValueError("Use a disclosed research alias or record an unresolved stop")


def build_dispatch_brief(workflow, connection, context) -> ResearchDispatchBrief | None:
    if context.get(BRIEF_CONTEXT_KEY) is not True:
        return None
    state = workflow._state(connection)
    ready = context["ready"]
    if not eligible_ready_research(state, ready):
        raise OperationBlocked("controller_research_frontier_changed")
    termination = context["termination_required"]
    if type(termination) is not bool:
        raise OperationBlocked("controller_research_frontier_changed")
    return ResearchDispatchBrief(
        tuple(
            _job(workflow, state, step_id, index)
            for index, step_id in enumerate(ready, start=1)
        ),
        termination,
    )
