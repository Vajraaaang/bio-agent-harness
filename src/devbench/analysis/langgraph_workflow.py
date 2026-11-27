"""Compatibility import for the platform langgraph_workflow module."""

import sys
from typing import TYPE_CHECKING

from devbench.platform import langgraph_workflow

if TYPE_CHECKING:
    from devbench.platform.langgraph_workflow import (
        SerialRNAWorkflow as SerialRNAWorkflow,
    )

sys.modules[__name__] = langgraph_workflow
