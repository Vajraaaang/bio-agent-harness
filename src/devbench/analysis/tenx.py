"""Compatibility import for tenx."""
import sys

from devbench.core import tenx

sys.modules[__name__] = tenx
