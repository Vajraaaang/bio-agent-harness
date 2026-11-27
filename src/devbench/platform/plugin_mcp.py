"""Import compatibility for the historical Workbench workflow API.

The daemon serves its research protocol from devbench.mcp.server. Old Workbench
callers share the same module object, including registered worker callbacks.
"""

import sys
from typing import TYPE_CHECKING

from . import plugin_workflows

if __name__ == "__main__":
    raise SystemExit(plugin_workflows.main())

if TYPE_CHECKING:
    from .plugin_workflows import (
        _ACTOR as _ACTOR,
    )
    from .plugin_workflows import (
        Input as Input,
    )
    from .plugin_workflows import (
        Plugin as Plugin,
    )
    from .plugin_workflows import (
        _admit_session as _admit_session,
    )
    from .plugin_workflows import (
        _ledger as _ledger,
    )
    from .plugin_workflows import (
        _lineage as _lineage,
    )
    from .plugin_workflows import (
        _profile as _profile,
    )
    from .plugin_workflows import (
        _roots as _roots,
    )
    from .plugin_workflows import (
        studio_evidence_digests as studio_evidence_digests,
    )
    from .plugin_workflows import (
        studio_job_readback as studio_job_readback,
    )
    from .plugin_workflows import (
        studio_plan_scope as studio_plan_scope,
    )
    from .plugin_workflows import (
        studio_run_projection as studio_run_projection,
    )

sys.modules[__name__] = plugin_workflows
