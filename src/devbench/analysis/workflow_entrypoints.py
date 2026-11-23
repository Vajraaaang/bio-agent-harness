"""Compatibility import for the platform RNA workflow entry points."""
import sys

from devbench.platform import workflow_entrypoints

sys.modules[__name__] = workflow_entrypoints
