"""Compatibility import for the platform langgraph_workflow module."""
import sys

from devbench.platform import langgraph_workflow

sys.modules[__name__] = langgraph_workflow
