"""Compatibility import for the platform workflow_actions module."""
import sys

from devbench.platform import workflow_actions

sys.modules[__name__] = workflow_actions
