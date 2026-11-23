"""Compatibility import for the platform workflow_state module."""
import sys
from typing import TYPE_CHECKING

from devbench.platform import workflow_state

if TYPE_CHECKING:
    from devbench.platform.workflow_state import (
        PHASES as PHASES,
        SERIAL_ROUTE as SERIAL_ROUTE,
        STATUSES as STATUSES,
        digest as digest,
        initial_state as initial_state,
        planning_binding as planning_binding,
        validate_workflow_state as validate_workflow_state,
    )

sys.modules[__name__] = workflow_state
