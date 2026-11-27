"""Import compatibility for the historical Workbench workflow API.

The daemon serves its research protocol from devbench.mcp.server. Old Workbench
callers share the same module object, including registered worker callbacks.
"""

import sys

from . import plugin_workflows

if __name__ == "__main__":
    raise SystemExit(plugin_workflows.main())

sys.modules[__name__] = plugin_workflows
