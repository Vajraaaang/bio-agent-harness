"""Frozen hypothesis inventories and joint Benjamini–Hochberg arithmetic."""

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Annotated, Literal

from pydantic import ConfigDict, Field, model_validator

from ._hashing import canonical_sha256
from .contrast_design import PipelineSpec
from .scientific_types import FrozenContract, Sha256

PlanID = Annotated[str, Field(pattern=r"^[A-Za-z0-9_.:-]{1,128}$")]
JOINT_BH_INPUT = "joint_bh_unadjusted_parquet"
JOINT_BH_OUTPUT_PREFIX = "joint-bh-pooled-"


class JointBHMember(FrozenContract):
    model_config = ConfigDict(revalidate_instances="always")

    aim_id: PlanID
    parameters_sha256: Sha256
    family_sha256: Sha256
    feature_count: int = Field(ge=1, le=100_000)
    step_id: PlanID | None = None

    @classmethod
    def from_pipeline(
        cls, aim_id: str, specification: PipelineSpec, *, step_id: str | None = None
    ) -> "JointBHMember":
        return cls(
            aim_id=aim_id,
            parameters_sha256=canonical_sha256(specification.model_dump(mode="json")),
            family_sha256=canonical_sha256(
                specification.family.model_dump(mode="json")
            ),
            feature_count=len(specification.family.feature_ids),
            step_id=step_id,
        )


class JointBHFitScope(FrozenContract):
    family_id: PlanID
    plan_sha256: Sha256
    member: JointBHMember


class JointBHPlan(FrozenContract):
    model_config = ConfigDict(revalidate_instances="always")

    format: Literal["devbench.thesis-joint-bh/1"] = "devbench.thesis-joint-bh/1"
    family_id: PlanID
    fdr: float = Field(gt=0, lt=1)
    members: tuple[JointBHMember, ...] = Field(min_length=2, max_length=32)
    dependence: Literal["independence", "positive_regression_dependence"]
    dependence_rationale: str = Field(min_length=1, max_length=2048)

    @model_validator(mode="after")
    def canonical_members(self) -> "JointBHPlan":
        steps = {member.step_id for member in self.members}
        if (
            len(set(self.aim_ids)) != len(self.members)
            or len({member.parameters_sha256 for member in self.members})
            != len(self.members)
            or not self.dependence_rationale.strip()
        ):
            raise ValueError("Joint BH requires distinct aims and planned analyses")
        if steps != {None} and (None in steps or len(steps) != len(self.members)):
            raise ValueError("Joint BH requires a distinct frozen step for each aim")
        object.__setattr__(
            self, "members", tuple(sorted(self.members, key=lambda item: item.aim_id))
        )
        return self

    @property
    def aim_ids(self) -> tuple[str, ...]:
        return tuple(member.aim_id for member in self.members)

    def sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json", exclude_none=True))


@dataclass(frozen=True, slots=True)
class JointBHRow:
    gene_id: str
    p_value: float | None
    q_value: float | None


def adjust_joint_bh(
    plan: JointBHPlan,
    *,
    specifications: Mapping[str, PipelineSpec],
    measurements: Mapping[str, Sequence[tuple[str, float | None]]],
) -> dict[str, tuple[JointBHRow, ...]]:
    """Adjust a full attempted family after the coordinator verifies its custody."""
    import numpy as np
    from scipy.stats import false_discovery_control

    plan = JointBHPlan.model_validate(plan)
    if set(plan.aim_ids) != specifications.keys() or (
        specifications.keys() != measurements.keys()
    ):
        raise ValueError("Every frozen aim must supply its complete attempted family")
    probabilities = []
    for member in plan.members:
        pipeline = specifications[member.aim_id]
        rows = measurements[member.aim_id]
        planned = JointBHMember.from_pipeline(
            member.aim_id, pipeline, step_id=member.step_id
        )
        if planned != member or (
            tuple(gene for gene, _ in rows) != pipeline.family.feature_ids
        ):
            raise ValueError("Measurements differ from the frozen ordered family")
        probabilities.extend(_arithmetic_probabilities(rows))
    adjusted = map(
        float, false_discovery_control(np.asarray(probabilities, dtype=float))
    )
    return {
        aim_id: _project_rows(measurements[aim_id], adjusted) for aim_id in plan.aim_ids
    }


def _arithmetic_probabilities(
    rows: Sequence[tuple[str, float | None]],
) -> list[float]:
    values = []
    for _, p in rows:
        if p is None:
            # Preserve the prespecified denominator without emitting a test.
            values.append(1.0)
        elif type(p) in (int, float) and 0 <= p <= 1:
            values.append(float(p))
        else:
            raise ValueError("Joint BH probabilities must be finite or null")
    return values


def _project_rows(
    rows: Sequence[tuple[str, float | None]],
    adjusted: Iterator[float],
) -> tuple[JointBHRow, ...]:
    result = []
    for gene_id, p in rows:
        q = next(adjusted)
        result.append(JointBHRow(gene_id, p, max(p, q) if p is not None else None))
    return tuple(result)
