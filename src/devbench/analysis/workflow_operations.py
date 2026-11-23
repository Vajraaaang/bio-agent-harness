"""Compatibility import for the durable workflow operation ledger."""
import sys

from devbench.engine import workflow_operations

sys.modules[__name__] = workflow_operations
