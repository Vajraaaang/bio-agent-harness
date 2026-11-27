"""Compatibility import for the durable workflow operation ledger."""

import sys
from typing import TYPE_CHECKING

from devbench.engine import workflow_operations

if TYPE_CHECKING:
    from devbench.engine.workflow_operations import (
        ModelCallReservation as ModelCallReservation,
    )
    from devbench.engine.workflow_operations import (
        OperationConflict as OperationConflict,
    )
    from devbench.engine.workflow_operations import (
        WorkflowOperationLedger as WorkflowOperationLedger,
    )
    from devbench.engine.workflow_operations import _digest as _digest
    from devbench.engine.workflow_operations import _json as _json
    from devbench.engine.workflow_operations import _limits as _limits
    from devbench.engine.workflow_operations import _policy as _policy

sys.modules[__name__] = workflow_operations
