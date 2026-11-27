"""Compatibility import for the platform scientific method planner."""

import sys
from typing import TYPE_CHECKING

from devbench.platform import method_planner

if TYPE_CHECKING:
    from devbench.platform.method_planner import (
        ScientificMethodPlanner as ScientificMethodPlanner,
    )

sys.modules[__name__] = method_planner
