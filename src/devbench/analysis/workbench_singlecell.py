"""Compatibility import for the scoped Workbench population workflow."""
import sys

from devbench.platform import workbench_singlecell

sys.modules[__name__] = workbench_singlecell
