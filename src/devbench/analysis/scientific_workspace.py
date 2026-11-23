"""Compatibility import for the platform scientific workspace controller."""
import sys

from devbench.platform import scientific_workspace

sys.modules[__name__] = scientific_workspace
