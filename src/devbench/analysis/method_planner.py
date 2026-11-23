"""Compatibility import for the platform scientific method planner."""
import sys

from devbench.platform import method_planner

sys.modules[__name__] = method_planner
