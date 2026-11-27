"""Plan declarations for the optional motor-neuron example recipes."""

from typing import Literal

from pydantic import Field

from devbench.platform.plugin_workflows import _TEXT, Input, _Args, _ScopedPlan


class _SharedMotorUMAPParameters(_Args):
    stage_column: _TEXT = "age"
    label_column: _TEXT = "celltypes"
    counts_layer: _TEXT = "counts"


class _SharedMotorUMAPPlan(_ScopedPlan):
    operation: Literal["shared_motor_umap"]
    objective: _TEXT
    inputs: list[Input] = Field(
        min_length=3,
        max_length=3,
        description="Three H5AD inputs in e09, e10, e11 order.",
    )
    parameters: _SharedMotorUMAPParameters = Field(
        default_factory=_SharedMotorUMAPParameters
    )


class _MotorProgramComparisonPlan(_ScopedPlan):
    operation: Literal["motor_program_comparison"]
    parameters: _Args = Field(default_factory=_Args)
