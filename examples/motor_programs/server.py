"""Explicitly requested example STDIO server, including private motor recipes."""

from typing import Annotated

from pydantic import ConfigDict, Field, RootModel

from devbench.platform.legacy_mcp_server import main as serve
from devbench.platform.plugin_mcp import _RegisteredExploratoryPlan, _StandardPlan

from .models import _MotorProgramComparisonPlan, _SharedMotorUMAPPlan


class ExamplePlan(
    RootModel[
        Annotated[
            _StandardPlan
            | _RegisteredExploratoryPlan
            | _MotorProgramComparisonPlan
            | _SharedMotorUMAPPlan,
            Field(discriminator="operation"),
        ]
    ]
):
    model_config = ConfigDict(json_schema_extra={"type": "object"})


def main(argv=None):
    return serve(argv, plan_model=ExamplePlan)


if __name__ == "__main__":
    raise SystemExit(main())
