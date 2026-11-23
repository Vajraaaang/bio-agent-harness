"""Compatibility import and CLI entry point for the Workbench server."""
import sys
from typing import TYPE_CHECKING

from devbench.platform import workbench_ui

if TYPE_CHECKING:
    from devbench.platform.workbench_ui import WorkbenchService as WorkbenchService
    from devbench.platform.workbench_ui import main as main
    from devbench.platform.workbench_ui import make_server as make_server

if __name__ == "__main__":
    workbench_ui.main(pin_execution=True)
else:
    sys.modules[__name__] = workbench_ui
