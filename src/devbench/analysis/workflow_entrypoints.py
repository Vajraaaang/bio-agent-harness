"""Compatibility import for the platform RNA workflow entry points."""

import sys
from typing import TYPE_CHECKING

from devbench.platform import workflow_entrypoints

if TYPE_CHECKING:
    from devbench.platform.workflow_entrypoints import (
        CHECKPOINT_NAME as CHECKPOINT_NAME,
    )
    from devbench.platform.workflow_entrypoints import LEDGER_NAME as LEDGER_NAME
    from devbench.platform.workflow_entrypoints import (
        audit_workflow_design as audit_workflow_design,
    )
    from devbench.platform.workflow_entrypoints import is_rna_plan as is_rna_plan
    from devbench.platform.workflow_entrypoints import (
        require_completed_parent as require_completed_parent,
    )
    from devbench.platform.workflow_entrypoints import (
        workflow_action as workflow_action,
    )
    from devbench.platform.workflow_entrypoints import (
        workflow_full_execution as workflow_full_execution,
    )
    from devbench.platform.workflow_entrypoints import (
        workflow_source as workflow_source,
    )

sys.modules[__name__] = workflow_entrypoints
