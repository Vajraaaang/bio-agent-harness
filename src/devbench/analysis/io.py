"""Compatibility import for bounded expression I/O."""
import sys

from devbench.core import expression_io

sys.modules[__name__] = expression_io
